"""e2e_fake: a detached milestone run, watched, paused and resumed (card ef6e5633).

Production wiring under the fake `claude`, with real git and brd: `am run
--milestone ... --detach` hands the engine to a child in its own session,
`am watch RUN_ID --follow` streams its journal, `am pause` parks it at the
next phase boundary, and a foreground `am resume` drives it to `done` under
the same run id. This is the sequence the plugin relies on.

The fake's hold parks a1's `implement`, so the pause is recorded while a1 is
mid-phase. Order comes from the hold marker files, the pause row's
`handled_at` (read through `am status`), `report.json` and the lease row,
never from sleeps. Every wait is bounded and fails naming its step.

What 3.2's test_detached_run.py already pins (the detach envelope's keys,
file modes, `getsid`, the `am runs` row) is not asserted again here.
"""

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, get_args

import pytest

from agent_manager import cli, detach, paths, store

PREFIX = "m3"
"""The `--branch-prefix` of this scenario; equals the e2e conftest's `MILESTONE_PREFIX`."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the e2e conftest's `VERIFY_COMMANDS[0]`, as in test_detached_run.py."""

DEADLINE = 240.0
"""Only bounds a broken run; a healthy one never waits this long."""

POLL = 0.05

CONTROL_KEYS_NEVER_PRESENT = {"escalated", "failed_phase", "integrated", "done"}
"""A paused payload never escalates, never names a failed phase and never
reaches Integrate (live control C6); as in test_live_control.py."""

EVENT_KINDS = frozenset(get_args(store.EventKind))

JOURNAL_KEYS = frozenset(store.JournalLine.model_fields)
"""Every key a JournalLine dumps; `am watch` prints `model_dump(mode="json")`."""


def _until(predicate: Callable[[], bool], what: str, timeout: float = DEADLINE) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"{what} did not happen within {timeout}s")
        time.sleep(POLL)


def _lease(root: Path, run_id: str) -> store.LeaseRow | None:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return store.read_lease(conn, run_id)
    finally:
        conn.close()


def _status(am: Callable[..., tuple[int, dict[str, Any]]], root: Path, run_id: str) -> dict[str, Any]:
    """`am status RUN_ID --repo-dir ROOT`'s data, from a real child process."""
    code, envelope = am("status", run_id, "--repo-dir", str(root))
    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _run_statuses(events: Sequence[dict[str, Any]]) -> list[str]:
    """The run status of every `run_upsert`, in stream order."""
    return [event["payload"]["status"] for event in events if event["event"] == "run_upsert"]


def _collapse(values: Sequence[str]) -> list[str]:
    """`values` with consecutive repeats folded into one."""
    collapsed: list[str] = []
    for value in values:
        if not collapsed or collapsed[-1] != value:
            collapsed.append(value)
    return collapsed


def _is_subsequence(needle: Sequence[str], haystack: Sequence[str]) -> bool:
    remaining = iter(haystack)
    return all(any(item == candidate for candidate in remaining) for item in needle)


class _Stream:
    """`am watch RUN_ID --follow`'s stdout, one parsed line at a time.

    A daemon thread reads the pipe into a queue, so every read here is
    bounded by `DEADLINE` and a broken run fails instead of hanging. `None`
    in the queue means the pipe reached EOF. `events` is every JournalLine
    read so far, in stream order, each checked as it arrives.
    """

    def __init__(self, child: subprocess.Popen, run_id: str) -> None:
        self.child = child
        self.run_id = run_id
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.events: list[dict[str, Any]] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.child.stdout is not None
        try:
            for line in self.child.stdout:
                self.lines.put(line)
        except (OSError, ValueError):
            pass  # the pipe was closed at teardown
        finally:
            self.lines.put(None)

    def _next(self, what: str, timeout: float) -> dict[str, Any]:
        try:
            line = self.lines.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            pytest.fail(f"watch stream: no line within {DEADLINE}s while waiting for {what}")
        if line is None:
            pytest.fail(
                f"watch stream ended (exit {self.child.poll()}) while waiting for {what}"
            )
        return json.loads(line)

    def hello(self) -> dict[str, Any]:
        return self._next("the hello line", DEADLINE)

    def drain_until(self, predicate: Callable[[list[dict[str, Any]]], bool], what: str) -> None:
        deadline = time.monotonic() + DEADLINE
        while not predicate(self.events):
            event = self._next(what, deadline - time.monotonic())
            assert set(event) == JOURNAL_KEYS, event
            assert event["run_id"] == self.run_id, event
            assert event["event"] in EVENT_KINDS, event
            self.events.append(event)


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
def test_a_detached_milestone_run_is_watched_paused_and_resumed_to_done(
    milestone_board, fake_claude_bin, hold, am, spawn_am, am_processes, detached_pids
):
    root = milestone_board["root"]
    stories = milestone_board["stories"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)  # only a1's implement is held

    # 1. Detach.
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
    run_id, pid = envelope["data"]["run_id"], envelope["data"]["pid"]
    detached_pids.append(pid)
    run_dir = paths.data_dir() / "runs" / run_id

    # 2. Watch starts: a hello line, never a refusal envelope.
    watcher = spawn_am("watch", run_id, "--follow")
    stream = _Stream(watcher, run_id)
    hello = stream.hello()
    assert "ok" not in hello, (
        f"am watch --follow refused the just-detached run (spec candidate (a)): {hello}"
    )
    assert hello["event"] == "watch", hello
    assert hello["schema"] == 1, hello
    assert "am" in hello, hello
    assert hello["runs_dir"] == str(paths.data_dir() / "runs"), hello

    # 3. Held: a1 is mid-implement, the run is started and its lease is live.
    _until(hold.held_marker(a1).exists, "a1's implement being held")
    held = _status(am, root, run_id)
    assert held["run"]["status"] == "started", held["run"]
    assert held["control"]["lease"] is not None, held["control"]
    assert held["control"]["lease"]["live"] is True, held["control"]

    # 4. Pause.
    code, paused = am("pause", run_id, "--repo-dir", str(root))
    assert code == 0, paused
    assert paused["ok"] is True, paused
    assert paused["data"]["run_id"] == run_id, paused
    assert paused["data"]["command"] == "pause", paused
    assert paused["data"]["effective"] == "pause", paused
    assert paused["data"]["already_requested"] is False, paused

    # 5. The detached process applied the pause; only then let a1 go on.
    def pause_applied() -> bool:
        requests = _status(am, root, run_id)["control"]["requests"]
        return [row["command"] for row in requests] == ["pause"] and (
            requests[0]["handled_at"] is not None
        )

    _until(pause_applied, "the detached run applying the pause")
    hold.release(a1)

    # 6. Parked: the paused life's report, its lease released.
    report = run_dir / detach.REPORT_NAME
    _until(report.exists, "the paused life's report.json")
    _until(lambda: _lease(root, run_id) is None, "the detached child releasing its lease")
    final = json.loads(report.read_text(encoding="utf-8"))
    assert final["ok"] is True, final
    parked = final["data"]
    assert parked["paused"] is True, parked
    assert parked["run_id"] == run_id, parked
    assert parked["resume"] == f"am resume {run_id}", parked
    assert not CONTROL_KEYS_NEVER_PRESENT & set(parked), parked
    assert a1 not in parked["completed"], parked
    assert parked["stopped"] == [
        {"story": stories["A"], "subtask": a1, "before_phase": "review"}
    ], parked
    assert _status(am, root, run_id)["run"]["status"] == "stopped"
    stream.drain_until(
        lambda events: "stopped" in _run_statuses(events),
        "a run_upsert recording the run stopped",
    )

    # 7. Resume in the foreground, to done, under the same run id.
    code, resumed = am("resume", run_id, "--repo-dir", str(root), "--verify", VERIFY)
    assert code == 0, resumed
    assert resumed["ok"] is True, resumed
    done = resumed["data"]
    assert done["done"] is True, done
    assert done["resumed"] is True, done
    assert done["run_id"] == run_id, done
    assert "escalated" not in done, done
    assert a1 in done["completed"], done
    assert set(done["integrated"]["merged"]) == set(stories.values()), done
    assert _status(am, root, run_id)["run"]["status"] == "done"
    assert _lease(root, run_id) is None

    # 8. One stream across both lives: exactly the journal, seq 1..N.
    code, once = am("watch", run_id)
    assert code == 0, once
    assert once["ok"] is True, once
    expected = once["data"]["events"]
    last_seq = max(event["seq"] for event in expected)
    stream.drain_until(
        lambda events: bool(events) and events[-1]["seq"] >= last_seq,
        f"the stream reaching seq {last_seq}",
    )
    assert stream.events == expected
    assert [event["seq"] for event in stream.events] == list(
        range(1, len(stream.events) + 1)
    )
    statuses = _collapse(_run_statuses(stream.events))
    assert _is_subsequence(["started", "stopped", "started", "done"], statuses), statuses

    # 9. Ctrl-C ends the stream cleanly.
    watcher.send_signal(signal.SIGINT)
    try:
        exit_code = watcher.wait(timeout=DEADLINE)
    except subprocess.TimeoutExpired:
        pytest.fail(f"am watch --follow did not exit within {DEADLINE}s of SIGINT")
    assert exit_code == 0, am_processes.stderr_of(watcher)
    assert am_processes.stderr_of(watcher) == ""
