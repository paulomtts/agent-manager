"""Behaviour of the brd board adapter (design §4 line 119, §9, spec 141c96e6).

Placement follows design §14: board.py is the brd caller, so its read/write
behaviour is exercised against a real temporary brd board over subprocess --
no mocking of brd, no network. The narrow checks at the bottom cover the pure
parts (argv construction, envelope decoding) that need no board at all.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager import board, models

requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the board adapter's steps-tier tests",
)


def test_show_argv_is_a_list_of_plain_arguments():
    assert board.show_argv("141c96e6") == ["brd", "show", "141c96e6"]


def test_tree_argv_is_a_list_of_plain_arguments():
    assert board.tree_argv("141c96e6") == ["brd", "tree", "141c96e6"]


def test_set_status_argv_uses_brd_update_with_a_status_option():
    # brd has no set-status subcommand; `update --status` is the only writer.
    assert board.set_status_argv("141c96e6", "in_progress") == [
        "brd",
        "update",
        "141c96e6",
        "--status",
        "in_progress",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        board.show_argv("141c96e6"),
        board.tree_argv("141c96e6"),
        board.set_status_argv("141c96e6", "done"),
    ],
    ids=["show", "tree", "set_status"],
)
def test_every_builder_returns_a_list_of_strings(argv):
    assert isinstance(argv, list)
    assert all(isinstance(element, str) for element in argv)
    assert argv[0] == "brd"


def test_shell_metacharacters_stay_inside_one_argv_element():
    # Design §5 line 252: argument lists, never shell strings. A hostile id is
    # one element, unquoted and unmodified -- there is nothing to escape.
    hostile = "141c96e6; rm -rf /"
    assert board.show_argv(hostile) == ["brd", "show", hostile]
    assert board.set_status_argv(hostile, "done; echo pwned") == [
        "brd",
        "update",
        hostile,
        "--status",
        "done; echo pwned",
    ]


def test_missing_brd_on_path_raises_board_error(monkeypatch, tmp_path):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    with pytest.raises(board.BoardError) as excinfo:
        board._run(board.show_argv("141c96e6"), None)
    assert "brd" in str(excinfo.value)
    assert excinfo.value.argv == ["brd", "show", "141c96e6"]
    assert excinfo.value.exit_code is None


def test_non_zero_exit_with_no_stdout_reports_stderr_and_the_exit_code(tmp_path):
    # A brd crash or a typer usage error writes only to stderr. Parsing "" as
    # JSON would bury that behind a JSONDecodeError.
    fake_brd = tmp_path / "brd"
    fake_brd.write_text(
        "#!/bin/sh\necho 'Usage: brd [OPTIONS]' >&2\nexit 2\n"
    )
    fake_brd.chmod(0o755)
    with pytest.raises(board.BoardError) as excinfo:
        board._run([str(fake_brd), "show", "141c96e6"], None)
    assert "Usage: brd [OPTIONS]" in str(excinfo.value)
    assert excinfo.value.exit_code == 2


def test_run_returns_the_completed_process_on_success(tmp_path):
    fake_brd = tmp_path / "brd"
    fake_brd.write_text('#!/bin/sh\necho \'{"ok": true, "data": {}}\'\n')
    fake_brd.chmod(0o755)
    completed = board._run([str(fake_brd), "show", "141c96e6"], None)
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"ok": True, "data": {}}


def test_run_invokes_brd_in_the_given_repo_dir(tmp_path):
    fake_brd = tmp_path / "brd"
    fake_brd.write_text('#!/bin/sh\nprintf \'{"ok": true, "data": "%s"}\' "$(pwd)"\n')
    fake_brd.chmod(0o755)
    elsewhere = tmp_path / "some-repo"
    elsewhere.mkdir()
    completed = board._run([str(fake_brd), "show", "x"], elsewhere)
    assert json.loads(completed.stdout)["data"] == str(elsewhere.resolve())


def test_board_never_uses_a_shell():
    # The one grep that keeps the §5 constraint honest as the module grows.
    source = Path(board.__file__).read_text()
    assert "shell=True" not in source
    assert "os.system" not in source


_SHOW_PAYLOAD = {
    "id": "141c96e6",
    "title": "Add the brd board adapter",
    "description": "the only caller of brd",
    "status": "todo",
    "parent_id": "492ac463",
    "created_at": "2026-09-23T10:00:00+00:00",
    "updated_at": "2026-09-23T10:00:00+00:00",
    "blocked_by": [],
    "children": [],
}


def test_decode_returns_the_data_of_an_ok_envelope():
    data = board._decode(
        json.dumps({"ok": True, "data": _SHOW_PAYLOAD}),
        argv=board.show_argv("141c96e6"),
        exit_code=0,
    )
    card = board._validated(models.Card, data, argv=board.show_argv("141c96e6"))
    assert card.id == "141c96e6"
    assert card.title == "Add the brd board adapter"
    assert card.status == "todo"
    assert card.parent_id == "492ac463"


def test_decode_raises_board_error_carrying_brds_own_message():
    envelope = {
        "ok": False,
        "error": {"type": "CardNotFoundError", "message": "no card with id nope"},
    }
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(
            json.dumps(envelope), argv=board.show_argv("nope"), exit_code=1
        )
    assert excinfo.value.message == "no card with id nope"
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.exit_code == 1
    assert "no card with id nope" in str(excinfo.value)


def test_decode_tolerates_an_error_envelope_without_a_message():
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(
            json.dumps({"ok": False}), argv=board.show_argv("nope"), exit_code=1
        )
    assert excinfo.value.error_type is None
    assert excinfo.value.exit_code == 1


@pytest.mark.parametrize(
    "stdout",
    ["", "   ", "not json at all", "Traceback (most recent call last):", "{"],
    ids=["empty", "blank", "prose", "traceback", "truncated"],
)
def test_non_json_stdout_raises_board_error_not_a_json_decode_error(stdout):
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(stdout, argv=board.show_argv("141c96e6"), exit_code=0)
    assert not isinstance(excinfo.value, json.JSONDecodeError)
    assert "JSON" in str(excinfo.value)


@pytest.mark.parametrize(
    "payload", ['["ok"]', '"ok"', "null", '{"data": {}}'], ids=["list", "str", "null", "no-ok"]
)
def test_json_that_is_not_an_envelope_raises_board_error(payload):
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(payload, argv=board.show_argv("141c96e6"), exit_code=0)
    assert "envelope" in str(excinfo.value)


def test_a_non_zero_exit_is_an_error_even_behind_an_ok_envelope():
    # Spec: non-zero exit raises, full stop. An ok envelope from a process that
    # then failed is not a success.
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(
            json.dumps({"ok": True, "data": _SHOW_PAYLOAD}),
            argv=board.show_argv("141c96e6"),
            exit_code=3,
        )
    assert excinfo.value.exit_code == 3
    assert "exited 3" in str(excinfo.value)


def test_data_missing_a_required_field_raises_board_error():
    incomplete = {key: value for key, value in _SHOW_PAYLOAD.items() if key != "title"}
    with pytest.raises(board.BoardError) as excinfo:
        board._validated(models.Card, incomplete, argv=board.show_argv("141c96e6"))
    assert "title" in str(excinfo.value)
    assert excinfo.value.argv == ["brd", "show", "141c96e6"]


def test_unknown_extra_fields_in_data_are_tolerated():
    generous = {**_SHOW_PAYLOAD, "assignee": "paulo", "labels": ["m1"]}
    card = board._validated(models.Card, generous, argv=board.show_argv("141c96e6"))
    assert card.id == "141c96e6"
    assert not hasattr(card, "assignee")


@pytest.fixture
def temp_board(tmp_path, monkeypatch):
    """A real, empty brd board in a throwaway directory.

    brd keys its SQLite files off XDG_DATA_HOME and resolves the board from the
    nearest `.brd` marker at or above its cwd, so pointing XDG_DATA_HOME at
    tmp_path and running in a fresh directory isolates these tests completely
    from the developer's own board. The subprocess inherits the patched
    environment.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "board-repo"
    root.mkdir()
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]["id"]


def _brd_json(root: Path, *args: str) -> object:
    completed = subprocess.run(
        ["brd", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]


@requires_brd
def test_show_reads_a_card_from_a_real_board(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    story = _add_card(temp_board, "Naming and the brd board adapter", milestone)
    subtask = _add_card(temp_board, "Add the brd board adapter", story)

    card = board.show(subtask, repo_dir=temp_board)

    assert isinstance(card, models.Card)
    assert card.id == subtask
    assert card.title == "Add the brd board adapter"
    assert card.status == "todo"
    assert card.parent_id == story


@requires_brd
def test_show_reports_a_top_level_card_with_no_parent(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    card = board.show(milestone, repo_dir=temp_board)
    assert card.parent_id is None


@requires_brd
def test_show_of_a_nonexistent_card_raises_board_error_with_brds_message(temp_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.show("no-such-card", repo_dir=temp_board)
    assert "no-such-card" in excinfo.value.message
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == ["brd", "show", "no-such-card"]


@requires_brd
def test_show_outside_a_brd_project_raises_board_error(tmp_path, monkeypatch):
    # No `.brd` marker anywhere above: brd answers with an ok:false
    # ProjectNotFoundError envelope and exits 1. That must surface as this
    # module's one error type, message intact.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    not_a_project = tmp_path / "nowhere"
    not_a_project.mkdir()
    with pytest.raises(board.BoardError) as excinfo:
        board.show("141c96e6", repo_dir=not_a_project)
    assert excinfo.value.error_type == "ProjectNotFoundError"
    assert excinfo.value.exit_code == 1


@requires_brd
def test_tree_returns_the_root_node_not_a_list(temp_board):
    # brd's build_tree always returns list[dict]; a bare id just makes it a
    # singleton. Callers want the node.
    milestone = _add_card(temp_board, "Milestone 1")
    node = board.tree(milestone, repo_dir=temp_board)
    assert isinstance(node, models.CardNode)
    assert node.id == milestone
    assert node.title == "Milestone 1"


@requires_brd
def test_tree_preserves_milestone_story_subtask_nesting(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    story = _add_card(temp_board, "Naming and the brd board adapter", milestone)
    first = _add_card(temp_board, "Port card naming from naming.mjs", story)
    second = _add_card(temp_board, "Add the brd board adapter", story)

    node = board.tree(milestone, repo_dir=temp_board)

    assert [child.id for child in node.children] == [story]
    story_node = node.children[0]
    assert [grandchild.id for grandchild in story_node.children] == [first, second]
    assert story_node.children[1].title == "Add the brd board adapter"
    assert story_node.children[1].children == []


@requires_brd
def test_tree_rooted_at_a_leaf_has_no_children(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    subtask = _add_card(temp_board, "Add the brd board adapter", milestone)
    node = board.tree(subtask, repo_dir=temp_board)
    assert node.id == subtask
    assert node.children == []


@requires_brd
def test_tree_does_not_re_sort_or_re_parent_what_brd_returned(temp_board):
    # Ordering and depth are brd's. Compare against brd's own raw JSON.
    milestone = _add_card(temp_board, "Milestone 1")
    for title in ("story a", "story b", "story c"):
        _add_card(temp_board, title, milestone)

    raw = _brd_json(temp_board, "tree", milestone)
    node = board.tree(milestone, repo_dir=temp_board)

    assert [child.id for child in node.children] == [
        child["id"] for child in raw[0]["children"]
    ]


@pytest.mark.parametrize(
    "data",
    [[], [{"id": "a", "title": "a", "status": "todo", "children": []},
          {"id": "b", "title": "b", "status": "todo", "children": []}],
     {"id": "a", "title": "a", "status": "todo", "children": []}],
    ids=["empty-list", "two-roots", "bare-dict"],
)
def test_tree_requires_exactly_one_root(data, tmp_path, monkeypatch):
    # A future brd change, or a board.py bug, must not become an IndexError or
    # a silently-wrong root.
    fake_brd = tmp_path / "brd"
    fake_brd.write_text(
        "#!/bin/sh\ncat <<'EOF'\n"
        + json.dumps({"ok": True, "data": data})
        + "\nEOF\n"
    )
    fake_brd.chmod(0o755)
    monkeypatch.setattr(board, "BRD", str(fake_brd))
    with pytest.raises(board.BoardError) as excinfo:
        board.tree("141c96e6", repo_dir=tmp_path)
    assert "exactly one root" in str(excinfo.value)
