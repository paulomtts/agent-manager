"""e2e_fake: one real `am run --milestone ... --detach` and one real `am run --board --detach` (cards aff9fdbf, 03f027ea).

Production wiring under the fake `claude`, with real git and brd. The `am`
parent is a real child process, and the engine runs in the grandchild it
forks into its own session. The fake's hold parks a1's `implement`, which
keeps the detached run live while the test looks at it after the parent has
exited. Order comes from marker files and the lease row, never from sleeps.
Watch, pause and resume are sibling 3.3's scenario, not this one.
"""

import json
import os
import re
import signal
import stat
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_manager import cli, control, detach, paths, store
from agent_manager.store import db as store_db
from agent_manager.store import leases as store_leases

PREFIX = "m3"
"""The `--branch-prefix` of this scenario; equals the e2e conftest's `MILESTONE_PREFIX`."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the e2e conftest's `VERIFY_COMMANDS[0]`, as in test_multi_process.py."""

DEADLINE = 240.0
"""Only bounds a broken run; a healthy one never waits this long."""

POLL = 0.05

BOARD_STAMP = re.compile(r"^\d{8}T\d{6}Z$")
"""`runs.RUN_ID_TIME_FORMAT`'s shape, the first half of a board file's stem."""


def _until(predicate: Callable[[], bool], what: str, timeout: float = DEADLINE) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"{what} did not happen within {timeout}s")
        time.sleep(POLL)


def _lease(root: Path, run_id: str) -> store_leases.LeaseRow | None:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        return store_leases.read_lease(conn, run_id)
    finally:
        conn.close()


@pytest.fixture
def detached_pids():
    """Every detached child the test started; its whole session is killed at teardown."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.e2e_fake
def test_a_detached_milestone_run_outlives_its_parent_and_leaves_its_report(
    milestone_board, fake_claude_bin, hold, am, detached_pids
):
    root = milestone_board["root"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)  # only a1 is held; the rest pass straight through

    code, envelope = am(
        "run",
        "--milestone",
        milestone_board["milestone"],
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        PREFIX,
        "--verify",
        VERIFY,
        "--detach",
    )

    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    data = envelope["data"]
    assert set(data) == {"run_id", "pid", "log", "detached"}
    assert data["detached"] is True
    pid, run_id = data["pid"], data["run_id"]
    detached_pids.append(pid)
    run_dir = paths.data_dir() / "runs" / run_id
    assert Path(data["log"]) == run_dir / detach.RUN_LOG_NAME
    assert stat.S_IMODE(os.stat(data["log"]).st_mode) == 0o600

    # The parent has exited (am() waited for it); the child is still working.
    _until(lambda: hold.held_marker(a1).exists(), "a1's implement being held")
    assert control.pid_alive(pid)
    assert os.getsid(pid) == pid

    code, listing = am("runs", "--repo-dir", str(root))
    assert code == 0, listing
    (row,) = listing["data"]["runs"]
    assert row["id"] == run_id
    assert row["lease"]["pid"] == pid
    assert row["lease"]["live"] is True

    hold.release(a1)
    report = run_dir / detach.REPORT_NAME
    _until(report.exists, "report.json appearing")
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    final = json.loads(report.read_text(encoding="utf-8"))
    assert final["ok"] is True, final
    assert final["data"]["done"] is True, final
    assert final["data"]["run_id"] == run_id
    _until(lambda: _lease(root, run_id) is None, "the child releasing its lease")


@pytest.mark.e2e_fake
def test_a_detached_board_run_outlives_its_parent_and_leaves_its_report(
    milestone_board, fake_claude_bin, hold, am, detached_pids
):
    """Card 03f027ea: the board form. No `--branch-prefix`, so the milestone
    runs under its own card stem; a1's implement is held to keep the child live."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)  # only a1 is held; the rest pass straight through

    code, envelope = am(
        "run",
        "--board",
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--verify",
        VERIFY,
        "--detach",
    )

    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    data = envelope["data"]
    assert set(data) == {"board", "detached", "pid", "log", "report", "levels"}
    assert data["board"] is True
    assert data["detached"] is True
    assert data["levels"] == [{"level": 0, "milestones": [milestone]}]
    pid = data["pid"]
    detached_pids.append(pid)
    log, report = Path(data["log"]), Path(data["report"])
    boards = paths.data_dir() / "boards"
    digest = paths.project_digest(root)
    stamp = log.name.partition("-")[0]
    assert BOARD_STAMP.match(stamp), log
    assert log == boards / f"{stamp}-{digest}{detach.BOARD_LOG_SUFFIX}"
    assert report == boards / f"{stamp}-{digest}{detach.BOARD_REPORT_SUFFIX}"
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600

    # The parent has exited (am() waited for it); the child is still working.
    _until(lambda: hold.held_marker(a1).exists(), "a1's implement being held")
    assert control.pid_alive(pid)
    assert os.getsid(pid) == pid
    assert not report.exists()

    code, listing = am("runs", "--repo-dir", str(root))
    assert code == 0, listing
    (row,) = listing["data"]["runs"]
    assert row["lease"]["pid"] == pid
    assert row["lease"]["live"] is True
    run_id = row["id"]

    hold.release(a1)
    _until(report.exists, "the board's report appearing")
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    final = json.loads(report.read_text(encoding="utf-8"))
    assert final["ok"] is True, final
    assert final["data"]["board"] is True, final
    assert final["data"]["ok"] is True, final
    (entry,) = final["data"]["milestones"]
    assert entry["milestone_id"] == milestone
    assert entry["status"] == "done", entry
    assert entry["run_id"] == run_id
    _until(lambda: _lease(root, run_id) is None, "the child releasing its lease")
