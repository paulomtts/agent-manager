"""Behaviour of the roll-up step (design §4 `steps/`, subtask card 43008688).

Placement follows design §14: `rollup.py` is a Steps component whose entire
behaviour is a brd write, so it is exercised against a real temporary brd board
over subprocess -- brd is not mocked, and neither is `board.set_status`.

`tests/steps/` has no `conftest.py` (`test_verify.py` defines its own
`requires_git` marker locally), so the brd helpers are lifted from
`tests/test_board.py`: the `requires_brd` marker, the `temp_board` fixture,
`_add_card` and `_brd_json`.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager import board
from agent_manager.steps import rollup

requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the roll-up step's steps-tier tests",
)


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
def test_set_status_really_changes_the_card_on_the_board(temp_board):
    milestone = _add_card(temp_board, "Milestone 2")
    story = _add_card(temp_board, "Close the seams the wiring test found", milestone)
    subtask = _add_card(temp_board, "Implement the rollup.set_status step", story)
    assert _brd_json(temp_board, "show", subtask)["status"] == "todo"

    result = rollup.set_status(subtask, "in_progress", repo_dir=temp_board)

    stored = _brd_json(temp_board, "show", subtask)
    assert stored["status"] == "in_progress"
    # The mapping reports what brd stored, not the literal that was requested.
    assert result == {"card": subtask, "status": stored["status"]}


@requires_brd
def test_a_second_identical_call_is_a_harmless_no_op(temp_board):
    subtask = _add_card(temp_board, "Implement the rollup.set_status step")

    first = rollup.set_status(subtask, "done", repo_dir=temp_board)
    second = rollup.set_status(subtask, "done", repo_dir=temp_board)

    assert second == first
    assert _brd_json(temp_board, "show", subtask)["status"] == "done"


@requires_brd
def test_a_later_call_with_a_different_status_overwrites(temp_board):
    subtask = _add_card(temp_board, "Implement the rollup.set_status step")

    rollup.set_status(subtask, "in_progress", repo_dir=temp_board)
    result = rollup.set_status(subtask, "done", repo_dir=temp_board)

    assert result == {"card": subtask, "status": "done"}
    assert _brd_json(temp_board, "show", subtask)["status"] == "done"


@requires_brd
def test_a_nonexistent_card_raises_board_error(temp_board):
    with pytest.raises(board.BoardError):
        rollup.set_status("deadbeef", "done", repo_dir=temp_board)


@requires_brd
def test_an_empty_card_id_raises_board_error(temp_board):
    # A context key that was never populated must fail loudly, not write nothing.
    with pytest.raises(board.BoardError):
        rollup.set_status("", "done", repo_dir=temp_board)


# --- Pure-function tier (design §14): the status computation, no board. ---


def test_stored_status_flattens_blocked_to_todo():
    assert rollup.stored_status("blocked") == "todo"
    assert rollup.stored_status("todo") == "todo"
    assert rollup.stored_status("in_progress") == "in_progress"
    assert rollup.stored_status("done") == "done"


def test_rollup_status_is_none_without_children():
    assert rollup.rollup_status([]) is None


def test_rollup_status_is_todo_when_every_child_is_todo():
    assert rollup.rollup_status(["todo", "todo"]) == "todo"


def test_rollup_status_counts_blocked_children_as_todo():
    assert rollup.rollup_status(["todo", "blocked"]) == "todo"
    assert rollup.rollup_status(["blocked"]) == "todo"


def test_rollup_status_is_done_when_every_child_is_done():
    assert rollup.rollup_status(["done", "done"]) == "done"


def test_rollup_status_is_in_progress_by_progress_not_least_advanced():
    # One done child among unstarted ones means the parent is under way.
    assert rollup.rollup_status(["done", "todo"]) == "in_progress"
    assert rollup.rollup_status(["done", "blocked"]) == "in_progress"
    assert rollup.rollup_status(["in_progress"]) == "in_progress"
    assert rollup.rollup_status(["todo", "in_progress", "done"]) == "in_progress"


def test_rollup_status_accepts_a_generator():
    assert rollup.rollup_status(s for s in ["done", "done"]) == "done"
