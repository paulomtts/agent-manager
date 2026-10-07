"""e2e_fake: a detached board run, watched with --all, one milestone paused and resumed (card 47969cf6).

Production wiring under the fake `claude`, with real git and brd: `am run
--board --detach` hands the board to a child in its own session, which runs
one milestone run per milestone. `am watch --all --follow` streams every
run's journal as each appears, `am pause` parks one milestone's run at its
next phase boundary while the other milestone runs to `done`, and a
foreground `am resume` drives the paused run to `done` under the same run id.
This is the board form of test_detached_pause_resume.py's scenario.

The fake's hold parks a1's `implement`, so the pause is recorded while a1 is
mid-phase. Order comes from the hold marker files, the pause row's
`handled_at` (read through `am status`), the board's report file and the
lease rows, never from sleeps. Every wait is bounded and fails naming its
step.

What test_detached_run.py already pins for the board form (the detach
envelope's keys, the `boards/` paths, file modes, `getsid`, the `am runs`
row) is not asserted again here.
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

from agent_manager import cli, paths
from agent_manager.store import db as store_db
from agent_manager.store import journal as store_journal
from agent_manager.store import leases as store_leases

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the e2e conftest's `VERIFY_COMMANDS[0]`, as in test_detached_run.py."""

DEADLINE = 240.0
"""Only bounds a broken run; a healthy one never waits this long."""

POLL = 0.05

CONTROL_KEYS_NEVER_PRESENT = {"escalated", "failed_phase", "integrated", "done"}
"""A paused payload never escalates, never names a failed phase and never
reaches Integrate (live control C6); as in test_live_control.py."""

EVENT_KINDS = frozenset(get_args(store_journal.EventKind))

JOURNAL_KEYS = frozenset(store_journal.JournalLine.model_fields)
"""Every key a JournalLine dumps; `am watch` prints `model_dump(mode="json")`."""


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


def _status(am: Callable[..., tuple[int, dict[str, Any]]], root: Path, run_id: str) -> dict[str, Any]:
    """`am status RUN_ID --repo-dir ROOT`'s data, from a real child process."""
    code, envelope = am("status", run_id, "--repo-dir", str(root))
    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _run_statuses(events: Sequence[dict[str, Any]]) -> list[str]:
    """The run status of every `run_upsert` in one run's events, in stream order."""
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


def _first_upserts(events: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each run's first `run_upsert`, keyed by run id, in stream order."""
    first: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["event"] == "run_upsert":
            first.setdefault(event["run_id"], event)
    return first


class _Stream:
    """`am watch --all --follow`'s stdout, one parsed line at a time.

    A daemon thread reads the pipe into a queue, so every read here is
    bounded by `DEADLINE` and a broken run fails instead of hanging. `None`
    in the queue means the pipe reached EOF. `events` is every JournalLine
    read so far, in stream order, each checked as it arrives. Any run's line
    is accepted: `--all` interleaves every run there is.
    """

    def __init__(self, child: subprocess.Popen) -> None:
        self.child = child
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
            assert event["event"] in EVENT_KINDS, event
            self.events.append(event)

    def by_run(self) -> dict[str, list[dict[str, Any]]]:
        """`events` grouped by `run_id`, each run's events in stream order."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in self.events:
            grouped.setdefault(event["run_id"], []).append(event)
        return grouped


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
def test_a_detached_board_is_watched_with_all_and_one_milestone_is_paused_and_resumed(
    two_milestone_board, fake_claude_bin, hold, am, spawn_am, am_processes, detached_pids
):
    root = two_milestone_board["root"]
    first = two_milestone_board["milestones"]["first"]
    second = two_milestone_board["milestones"]["second"]
    a, b = two_milestone_board["stories"]["first"]
    c, d = two_milestone_board["stories"]["second"]
    a1, b1 = two_milestone_board["subtasks"]["first"]
    c1, d1 = two_milestone_board["subtasks"]["second"]
    hold.arm()
    hold.release(b1, c1, d1)  # only a1's implement is held

    # 1. Detach the whole board; no branch prefix, so each milestone derives its own.
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
    detached_pids.append(data["pid"])
    (level,) = data["levels"]
    assert level["level"] == 0, data["levels"]
    assert sorted(level["milestones"]) == sorted([first, second]), data["levels"]
    report = Path(data["report"])

    # 2. Watch every run, started before any milestone run necessarily exists.
    watcher = spawn_am("watch", "--all", "--follow")
    stream = _Stream(watcher)
    hello = stream.hello()
    assert "ok" not in hello, f"am watch --all --follow refused to stream: {hello}"
    assert hello["event"] == "watch", hello
    assert hello["schema"] == 2, hello
    assert "am" in hello, hello
    assert hello["runs_dir"] == str(paths.data_dir() / "runs"), hello

    # 3. Each milestone's run appears, its first run_upsert naming its milestone.
    stream.drain_until(
        lambda events: len(_first_upserts(events)) >= 2,
        "a run_upsert from two distinct runs (fewer than two runs ever appeared)",
    )
    firsts = _first_upserts(stream.events)
    run_of: dict[str, str] = {}
    for run_id, upsert in firsts.items():
        milestone_id = upsert["payload"]["milestone_id"]
        assert milestone_id in {first, second}, (
            f"run {run_id}'s first run_upsert has milestone_id {milestone_id!r}: {upsert}"
        )
        run_of[milestone_id] = run_id
    assert set(run_of) == {first, second}, firsts
    assert run_of[first] != run_of[second], run_of

    # 4. Held: a1 is mid-implement, first's run is started and its lease is live.
    _until(hold.held_marker(a1).exists, "a1's implement being held")
    held = _status(am, root, run_of[first])
    assert held["run"]["status"] == "started", held["run"]
    assert held["control"]["lease"] is not None, held["control"]
    assert held["control"]["lease"]["live"] is True, held["control"]

    # 5. Pause first's run only; nothing is sent to second's.
    code, paused = am("pause", run_of[first], "--repo-dir", str(root))
    assert code == 0, paused
    assert paused["ok"] is True, paused
    assert paused["data"]["run_id"] == run_of[first], paused
    assert paused["data"]["command"] == "pause", paused
    assert paused["data"]["effective"] == "pause", paused
    assert paused["data"]["already_requested"] is False, paused

    def pause_applied() -> bool:
        requests = _status(am, root, run_of[first])["control"]["requests"]
        return [row["command"] for row in requests] == ["pause"] and (
            requests[0]["handled_at"] is not None
        )

    _until(pause_applied, "the detached board applying first's pause")
    hold.release(a1)

    # 6. The board ends: first stopped, second done, both leases released.
    _until(report.exists, "the board's report appearing")
    for milestone in (first, second):
        _until(
            lambda: _lease(root, run_of[milestone]) is None,
            f"the board child releasing {milestone}'s lease",
        )
    report_bytes = report.read_bytes()
    final = json.loads(report_bytes)
    assert final["ok"] is True, final
    board_payload = final["data"]
    assert board_payload["board"] is True, board_payload
    assert board_payload["ok"] is False, board_payload
    assert board_payload["levels"] == data["levels"], board_payload
    entries = board_payload["milestones"]
    assert [entry["milestone_id"] for entry in entries] == level["milestones"], entries
    stopped = next(entry for entry in entries if entry["milestone_id"] == first)
    finished = next(entry for entry in entries if entry["milestone_id"] == second)
    assert stopped["status"] == "stopped", stopped
    assert stopped["paused"] is True, stopped
    assert stopped["run_id"] == run_of[first], stopped
    assert stopped["resume"] == f"am resume {run_of[first]}", stopped
    assert not CONTROL_KEYS_NEVER_PRESENT & set(stopped), stopped
    assert a1 not in stopped["completed"], stopped
    assert {"story": a, "subtask": a1, "before_phase": "review"} in stopped["stopped"], stopped
    assert finished["status"] == "done", finished
    assert finished["done"] is True, finished
    assert finished["run_id"] == run_of[second], finished
    assert set(finished["integrated"]["merged"]) == {c, d}, finished
    assert _status(am, root, run_of[first])["run"]["status"] == "stopped"
    assert _status(am, root, run_of[second])["run"]["status"] == "done"
    stream.drain_until(
        lambda events: "stopped"
        in _run_statuses([event for event in events if event["run_id"] == run_of[first]]),
        "a run_upsert recording first's run stopped",
    )

    # 7. Resume first's run in the foreground, to done, under the same run id.
    code, resumed = am(
        "resume", run_of[first], "--repo-dir", str(root), "--verify", VERIFY
    )
    assert code == 0, resumed
    assert resumed["ok"] is True, resumed
    done = resumed["data"]
    assert done["done"] is True, done
    assert done["resumed"] is True, done
    assert done["run_id"] == run_of[first], done
    assert "escalated" not in done, done
    assert a1 in done["completed"], done
    assert set(done["integrated"]["merged"]) == {a, b}, done
    assert _status(am, root, run_of[first])["run"]["status"] == "done"
    assert _lease(root, run_of[first]) is None
    assert report.read_bytes() == report_bytes, "am resume rewrote the board's report"

    # 8. One stream, both runs, exactly their journals: seq 1..N per run.
    code, once = am("watch", "--all")
    assert code == 0, once
    assert once["ok"] is True, once
    expected: dict[str, list[dict[str, Any]]] = {}
    for event in once["data"]["events"]:
        expected.setdefault(event["run_id"], []).append(event)
    assert set(expected) == {run_of[first], run_of[second]}, sorted(expected)
    last_seq = {run_id: max(event["seq"] for event in events) for run_id, events in expected.items()}

    def caught_up(events: list[dict[str, Any]]) -> bool:
        seen: dict[str, int] = {}
        for event in events:
            seen[event["run_id"]] = event["seq"]
        return all(seen.get(run_id, 0) >= seq for run_id, seq in last_seq.items())

    stream.drain_until(caught_up, f"the stream reaching each run's last seq {last_seq}")
    streamed = stream.by_run()
    assert set(streamed) == set(expected), sorted(streamed)
    for run_id, events in expected.items():
        assert streamed[run_id] == events, run_id
        assert [event["seq"] for event in streamed[run_id]] == list(
            range(1, len(events) + 1)
        ), run_id
    first_statuses = _collapse(_run_statuses(streamed[run_of[first]]))
    assert _is_subsequence(["started", "stopped", "started", "done"], first_statuses), (
        first_statuses
    )
    second_statuses = _collapse(_run_statuses(streamed[run_of[second]]))
    assert second_statuses[-1] == "done", second_statuses
    assert "stopped" not in second_statuses, second_statuses

    # 9. Ctrl-C ends the stream cleanly.
    watcher.send_signal(signal.SIGINT)
    try:
        exit_code = watcher.wait(timeout=DEADLINE)
    except subprocess.TimeoutExpired:
        pytest.fail(f"am watch --all --follow did not exit within {DEADLINE}s of SIGINT")
    assert exit_code == 0, am_processes.stderr_of(watcher)
    assert am_processes.stderr_of(watcher) == ""
