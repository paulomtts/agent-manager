"""e2e_fake: an agent's `pkill -f` cannot reach the `am` engine, foreground or detached (card 4a3e0414).

Run-hardening Story A's claim (design "Story A / Goal" and "Testing"): no
command an agent runs, `pkill -f "<verify string>"` and `pkill -f ''`
included, can signal the engine, and the engine's own `/proc/<pid>/cmdline`
carries no verification text.

Real: the `am` console entry (`cli.entry`, started through
`spawn_am_console`) and its `argv_guard` re-exec, the launcher, `bwrap`,
`pkill`, git and brd. Faked: only `claude`, whose pkill marker makes every
implement run `pkill -f` on the marker's patterns from inside the agent's
process, after its hold.

Every case runs story A of `milestone_board` (a1 -> a2) with a1's implement
held, so the engine's command line is read while it is parked. `VERIFY` is a
real, green, per-test unique command, so `pkill -f VERIFY` can match only a
stand-in process the test starts with that text in its argv. The control case
runs the same pattern without isolation and the stand-in dies: that is what
makes the isolated cases' surviving stand-in a proof rather than a vacuity.
Order comes from marker files, process exits, `report.json` and the lease
row, never from sleeps.
"""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
from conftest import missing_binary

from agent_manager import argv_guard, cli, detach, paths, store
from agent_manager.harness import launcher

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: the board fixture derives its branches with it."""

DEADLINE = 240.0
"""Only bounds a broken run; a healthy one never waits this long."""

POLL = 0.05
"""Seconds between checks of a waited-for condition. A polling cadence, never an ordering."""


def _until(predicate: Callable[[], bool], what: str, timeout: float = DEADLINE) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"{what} did not happen within {timeout}s")
        time.sleep(POLL)


@pytest.fixture
def bwrap_ready() -> None:
    """Skip unless `bwrap` is on PATH and can start here.

    A probe failure (bwrap installed, user namespaces blocked) is a skip, so an
    `--isolation bwrap` refused at exit 3 is never mistaken for a failed proof.
    """
    missing = missing_binary(["bwrap"], among=("bwrap",))
    if missing is not None:
        pytest.skip(f"the {missing} CLI must be installed for the agent-signal proofs")
    reason = launcher.probe("bwrap")
    if reason is not None:
        pytest.skip(reason)


def _verify_text() -> str:
    """A real green command no other process on this host has in its argv."""
    return f"test -n am-a6-{uuid.uuid4().hex}"


def _stand_in(am_processes, verify: str) -> subprocess.Popen:
    """A sleeper whose command line holds `verify`; killed at teardown."""
    return am_processes.track(
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)", verify],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    )


def _story_argv(
    root: Path, story: str, verify: str, isolation: str, *, detach: bool = False
) -> list[str]:
    argv = [
        "run",
        "--story",
        story,
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        PREFIX,
        "--verify",
        verify,
        "--isolation",
        isolation,
    ]
    if detach:
        argv.append("--detach")
    return argv


def _assert_neutral(pid: int, verify: str) -> None:
    """The engine's `/proc/<pid>/cmdline` has the hidden flag and no verification text."""
    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    tokens = raw.split(b"\0")
    assert argv_guard.FROM_ENV_FLAG.encode() in tokens, tokens
    assert argv_guard.VERIFY_FLAG.encode() not in tokens, tokens
    assert not any(
        token.startswith(argv_guard.VERIFY_FLAG.encode() + b"=") for token in tokens
    ), tokens
    assert verify.encode() not in raw, tokens
    assert verify.split()[-1].encode() not in raw, tokens


def _recorded_launcher(root: Path, run_id: str) -> str:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run.config.launcher


def _implement_pkills(entries: Iterable[Mapping[str, Any]]) -> list[Any]:
    """Each implement's `pkill` record, in the order the fake logged them."""
    return [entry.get("pkill") for entry in entries if entry["phase"] == "implement"]


@pytest.mark.e2e_fake
def test_without_isolation_the_agents_pkill_kills_a_matching_process_but_not_the_engine(
    bwrap_ready,
    milestone_board,
    fake_claude_bin,
    hold,
    pkill_marker,
    am_processes,
    spawn_am_console,
    finish_am,
    wait_for_file,
    read_fake_log,
):
    """The control: same pattern, same stand-in, no PID namespace. The pkill
    really kills, and only the neutral argv keeps the engine out of it."""
    root = milestone_board["root"]
    story = milestone_board["stories"]["A"]
    a1, a2 = milestone_board["subtasks"]["A"]
    verify = _verify_text()
    # Never `""` here: un-isolated, the fake's guard would refuse it.
    pkill_marker.write_text(json.dumps([verify]), encoding="utf-8")
    stand_in = _stand_in(am_processes, verify)
    hold.arm()
    hold.release(a2)  # only a1's implement is held

    child = spawn_am_console(*_story_argv(root, story, verify, "none"))
    wait_for_file(hold.held_marker(a1), child)

    _assert_neutral(child.pid, verify)
    assert stand_in.poll() is None

    hold.release(a1)
    _until(lambda: stand_in.poll() is not None, "the stand-in dying to a1's pkill")
    assert stand_in.returncode == -signal.SIGTERM

    code, envelope = finish_am(child)
    assert code == 0, (envelope, am_processes.stderr_of(child))
    assert envelope["ok"] is True, envelope
    data = envelope["data"]
    assert data["done"] is True, data
    assert _recorded_launcher(root, data["run_id"]) == "direct"
    # a1's pkill killed the stand-in; by a2's there was nothing left to match.
    assert _implement_pkills(read_fake_log(data["run_id"])) == [
        [{"pattern": verify, "returncode": 0}],
        [{"pattern": verify, "returncode": 1}],
    ]


def _isolated_pkills(verify: str) -> list[dict[str, Any]]:
    """What one implement records inside bwrap: the verify pattern reached
    nothing (1); the empty pattern really ran and matched in-namespace processes (0)."""
    return [
        {"pattern": verify, "returncode": 1},
        {"pattern": "", "returncode": 0},
    ]


@pytest.mark.e2e_fake
def test_under_bwrap_an_agents_pkill_reaches_neither_the_engine_nor_the_stand_in(
    bwrap_ready,
    milestone_board,
    fake_claude_bin,
    hold,
    pkill_marker,
    am_processes,
    spawn_am_console,
    finish_am,
    wait_for_file,
    read_fake_log,
):
    root = milestone_board["root"]
    story = milestone_board["stories"]["A"]
    a1, a2 = milestone_board["subtasks"]["A"]
    verify = _verify_text()
    pkill_marker.write_text(json.dumps([verify, ""]), encoding="utf-8")
    stand_in = _stand_in(am_processes, verify)
    hold.arm()
    hold.release(a2)  # only a1's implement is held

    child = spawn_am_console(*_story_argv(root, story, verify, "bwrap"))
    wait_for_file(hold.held_marker(a1), child)

    _assert_neutral(child.pid, verify)
    assert stand_in.poll() is None

    hold.release(a1)
    code, envelope = finish_am(child)

    # 0, not -SIGTERM or -SIGKILL: the engine lived through both pkills.
    assert code == 0, (envelope, am_processes.stderr_of(child))
    assert envelope["ok"] is True, envelope
    data = envelope["data"]
    assert data["done"] is True, data
    assert stand_in.poll() is None
    assert _recorded_launcher(root, data["run_id"]) == "bwrap"
    assert _implement_pkills(read_fake_log(data["run_id"])) == [
        _isolated_pkills(verify),
        _isolated_pkills(verify),
    ]


def _lease(root: Path, run_id: str) -> store.LeaseRow | None:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return store.read_lease(conn, run_id)
    finally:
        conn.close()


@pytest.fixture
def detached_pids():
    """Every detached engine the test started; its whole session is killed at teardown."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.e2e_fake
def test_under_bwrap_a_detached_engine_survives_its_agents_pkill(
    bwrap_ready,
    milestone_board,
    fake_claude_bin,
    hold,
    pkill_marker,
    am_processes,
    spawn_am_console,
    finish_am,
    read_fake_log,
    detached_pids,
):
    root = milestone_board["root"]
    story = milestone_board["stories"]["A"]
    a1, a2 = milestone_board["subtasks"]["A"]
    verify = _verify_text()
    pkill_marker.write_text(json.dumps([verify, ""]), encoding="utf-8")
    stand_in = _stand_in(am_processes, verify)
    hold.arm()
    hold.release(a2)  # only a1's implement is held

    child = spawn_am_console(*_story_argv(root, story, verify, "bwrap", detach=True))
    code, envelope = finish_am(child)

    assert code == 0, (envelope, am_processes.stderr_of(child))
    assert envelope["ok"] is True, envelope
    data = envelope["data"]
    assert data["detached"] is True, data
    pid, run_id = data["pid"], data["run_id"]
    detached_pids.append(pid)

    _until(lambda: hold.held_marker(a1).exists(), "a1's implement being held")
    lease = _lease(root, run_id)
    assert lease is not None, run_id
    # The pid read below is the detached engine's, not a stale or foreign one.
    assert lease.pid == pid
    assert os.getsid(pid) == pid
    _assert_neutral(lease.pid, verify)
    assert stand_in.poll() is None

    hold.release(a1)
    report = paths.data_dir() / "runs" / run_id / detach.REPORT_NAME
    _until(report.exists, "report.json appearing")
    final = json.loads(report.read_text(encoding="utf-8"))
    assert final["ok"] is True, final
    assert final["data"]["done"] is True, final
    _until(lambda: _lease(root, run_id) is None, "the engine releasing its lease")

    assert stand_in.poll() is None
    assert _recorded_launcher(root, run_id) == "bwrap"
    assert _implement_pkills(read_fake_log(run_id)) == [
        _isolated_pkills(verify),
        _isolated_pkills(verify),
    ]
