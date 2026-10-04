"""The files `am run --detach` leaves, and the fork that leaves them (card aff9fdbf).

Unmarked tests are unit tier and start no process. `fork_detacher` really
forks, so its own tests below are marked `e2e_fake`; the whole detached run
is `tests/e2e/test_detached_run.py`.
"""

import ast
import json
import os
import signal
import stat
import sys
import time
from pathlib import Path

import pytest

from agent_manager import detach, paths

RUN_ID = "20261004T090000Z-1a2b3c4d"


@pytest.fixture(autouse=True)
def data_home(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg"


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_create_run_log_makes_an_empty_0600_file_in_the_run_directory():
    log = detach.create_run_log(RUN_ID)

    assert log == paths.data_dir() / "runs" / RUN_ID / detach.RUN_LOG_NAME
    assert log.is_file()
    assert log.read_bytes() == b""
    assert _mode(log) == 0o600


def test_create_run_log_is_0600_whatever_the_umask_or_an_existing_file_said():
    existing = paths.run_dir(RUN_ID) / detach.RUN_LOG_NAME
    existing.write_text("earlier\n", encoding="utf-8")
    existing.chmod(0o644)
    old = os.umask(0)
    try:
        log = detach.create_run_log(RUN_ID)
    finally:
        os.umask(old)

    assert _mode(log) == 0o600
    assert log.read_text(encoding="utf-8") == "earlier\n"


def test_write_report_writes_one_line_at_0600_and_leaves_no_temp_file():
    path = detach.write_report(RUN_ID, json.dumps({"ok": True, "data": {"done": True}}))

    assert path == paths.data_dir() / "runs" / RUN_ID / detach.REPORT_NAME
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True, "data": {"done": True}}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert _mode(path) == 0o600
    assert sorted(entry.name for entry in path.parent.iterdir()) == [detach.REPORT_NAME]


def test_write_report_replaces_an_earlier_report_whole():
    detach.write_report(RUN_ID, '{"ok":false}')

    path = detach.write_report(RUN_ID, '{"ok":true}')

    assert path.read_text(encoding="utf-8") == '{"ok":true}\n'
    assert sorted(entry.name for entry in path.parent.iterdir()) == [detach.REPORT_NAME]


BOARD_STEM = "20261004T090000Z-" + "ab" * 32


def test_create_board_log_makes_an_empty_0600_file_under_boards():
    log = detach.create_board_log(BOARD_STEM)

    assert log == paths.data_dir() / "boards" / f"{BOARD_STEM}{detach.BOARD_LOG_SUFFIX}"
    assert log.is_file()
    assert log.read_bytes() == b""
    assert _mode(log) == 0o600


@pytest.mark.parametrize("umask", [0o000, 0o277])
def test_create_board_log_is_0600_whatever_the_umask(umask):
    paths.boards_dir()  # made first: a 0o277 umask would make it untraversable
    old = os.umask(umask)
    try:
        log = detach.create_board_log(BOARD_STEM)
    finally:
        os.umask(old)

    assert _mode(log) == 0o600


def test_create_board_log_refuses_an_existing_log_and_leaves_it_alone():
    log = detach.create_board_log(BOARD_STEM)
    log.write_text("the first board run\n", encoding="utf-8")

    with pytest.raises(FileExistsError) as caught:
        detach.create_board_log(BOARD_STEM)

    assert str(caught.value.filename) == str(log)
    assert log.read_text(encoding="utf-8") == "the first board run\n"


def test_board_report_path_names_the_report_and_creates_only_the_directory():
    path = detach.board_report_path(BOARD_STEM)

    assert path == paths.data_dir() / "boards" / f"{BOARD_STEM}{detach.BOARD_REPORT_SUFFIX}"
    assert path.parent.is_dir()
    assert not path.exists()
    assert list(path.parent.iterdir()) == []


def test_write_board_report_writes_one_line_at_0600_and_leaves_no_temp_file():
    target = detach.board_report_path(BOARD_STEM)

    path = detach.write_board_report(target, json.dumps({"ok": True, "data": {"board": True}}))

    assert path == target
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True, "data": {"board": True}}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert _mode(path) == 0o600
    assert sorted(entry.name for entry in path.parent.iterdir()) == [target.name]


def test_write_board_report_replaces_an_earlier_report_whole():
    target = detach.board_report_path(BOARD_STEM)
    detach.write_board_report(target, '{"ok":false}')

    path = detach.write_board_report(target, '{"ok":true}')

    assert path.read_text(encoding="utf-8") == '{"ok":true}\n'
    assert sorted(entry.name for entry in path.parent.iterdir()) == [target.name]


@pytest.mark.parametrize("umask", [0o000, 0o277])
def test_write_board_report_is_0600_whatever_the_umask(umask):
    target = detach.board_report_path(BOARD_STEM)
    old = os.umask(umask)
    try:
        detach.write_board_report(target, '{"ok":true}')
    finally:
        os.umask(old)

    assert _mode(target) == 0o600


# -- fork_detacher itself: these fork, so they are e2e_fake, not unit -----------
#
# The tier follows what a test spawns, not its directory (CLAUDE.md "Test
# tiers"): each test below forks a real child, so each is marked `e2e_fake`
# explicitly and stays out of the default run. Order comes from the go-pipe
# and `waitpid`, never from sleeps. `sys.stdout`/`sys.stderr` are pointed back
# at fds 1 and 2, as they are in a real `am` process, because pytest's capture
# replaces them with objects that do not write to fd 1 or 2.

CHILD_DEADLINE = 30.0
"""Only bounds a broken child; a healthy one exits at once."""


def _real_std_streams(monkeypatch) -> None:
    """Called in the test body: pytest re-installs its capture between setup and call."""
    monkeypatch.setattr(sys, "stdout", sys.__stdout__)
    monkeypatch.setattr(sys, "stderr", sys.__stderr__)


def _reap(pid: int) -> int:
    """Wait for `pid` to exit and return its exit code; kill it past the deadline."""
    deadline = time.monotonic() + CHILD_DEADLINE
    while True:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return os.waitstatus_to_exitcode(status)
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            pytest.fail(f"the detached child {pid} did not exit within {CHILD_DEADLINE}s")
        time.sleep(0.01)


@pytest.mark.e2e_fake
def test_fork_detacher_runs_the_body_in_its_own_session_only_once_told_to_go(
    tmp_path, monkeypatch
):
    _real_std_streams(monkeypatch)
    log = tmp_path / "run.log"
    log.touch()
    marker = tmp_path / "ran"

    def body() -> None:
        print("from the detached child", flush=True)
        marker.write_text(f"{os.getpid()} {os.getsid(0)}", encoding="utf-8")

    spawned = detach.fork_detacher(body, log)
    try:
        # The child is blocked on the go-pipe, so its body cannot have run yet.
        assert not marker.exists()
    finally:
        spawned.go()

    assert _reap(spawned.pid) == 0
    assert marker.read_text(encoding="utf-8") == f"{spawned.pid} {spawned.pid}"
    assert "from the detached child" in log.read_text(encoding="utf-8")


@pytest.mark.e2e_fake
def test_an_aborted_detached_child_exits_without_running_its_body(tmp_path, monkeypatch):
    _real_std_streams(monkeypatch)
    log = tmp_path / "run.log"
    log.touch()
    marker = tmp_path / "ran"

    spawned = detach.fork_detacher(lambda: marker.touch(), log)
    spawned.abort()

    _reap(spawned.pid)
    assert not marker.exists()


@pytest.mark.e2e_fake
def test_a_detached_child_whose_body_raises_leaves_its_traceback_in_run_log(
    tmp_path, monkeypatch
):
    _real_std_streams(monkeypatch)
    log = tmp_path / "run.log"
    log.touch()

    def body() -> None:
        raise RuntimeError("engine bug in the child")

    spawned = detach.fork_detacher(body, log)
    spawned.go()

    assert _reap(spawned.pid) != 0
    text = log.read_text(encoding="utf-8")
    assert "Traceback" in text
    assert "RuntimeError: engine bug in the child" in text


def test_detach_module_imports_only_paths_and_the_stdlib():
    tree = ast.parse(Path(detach.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base == "agent_manager":
                modules |= {f"agent_manager.{alias.name}" for alias in node.names}
            else:
                modules.add(base)
    ours = {name for name in modules if name.split(".")[0] == "agent_manager"}
    theirs = {name.split(".")[0] for name in modules} - {"agent_manager"}
    assert ours <= {"agent_manager.paths"}
    assert theirs <= set(sys.stdlib_module_names) | {"__future__"}
