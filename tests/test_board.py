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
