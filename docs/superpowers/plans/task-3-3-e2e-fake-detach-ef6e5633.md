<!-- task-pipeline: validated -->
# 3.3 e2e_fake: detach, watch, pause, resume (card ef6e5633)

Parent story fea654ef "am run --detach". Blocked by 3.2 (aff9fdbf, done). Narrows the Testing line of `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` ("starts a detached milestone run, follows its logs and watch stream, pauses it with am pause and resumes it") to the watch stream only: `am logs --follow` does not exist yet and is a separate story.

## Base

This branch (`ami/task-3-3-e2e-fake-detach-ef6e5633`) already contains 3.2's work: `src/agent_manager/detach.py` (`RUN_LOG_NAME`, `REPORT_NAME`, `fork_detacher`) and `tests/e2e/test_detached_run.py` are present. Master (09e8b80) has no `--detach`, so this work must not be rebased onto master as it stands. The test does not use `watch --from-now` or the 2.x `am runs` fields (`lease`, `progress`), even where they are present on this branch.

## Scope

The deliverable is one new file, `tests/e2e/test_detached_pause_resume.py`, holding exactly one test marked `@pytest.mark.e2e_fake`. The marker is explicit, not left to the directory auto-mark. The file mirrors `tests/e2e/test_detached_run.py`: it uses the same `PREFIX = "m3"`, `VERIFY = "git rev-parse --verify HEAD"` (must equal conftest `VERIFY_COMMANDS[0]`), `DEADLINE`/`POLL`, `_until`, `_lease`, the `detached_pids` fixture (killpg at teardown) and the same `am run --milestone ... --detach` argv. A module docstring names the card and says that ordering comes from markers, control rows and the lease row, never sleeps.

The card adds no production code. If the test exposes a bug, report it rather than widen the card. Two candidate areas: (a) `am watch RUN_ID --follow` refusing a just-detached run because it has no journal yet; (b) the paused life's `report.json` is left in place after a foreground `am resume`. The test does not assert anything about (b).

The test does not re-assert what 3.2 already covers: the detach envelope's key set, log/report file modes, `getsid`, the `am runs` row, or the child outliving its parent.

## Observable sequence (what the test asserts, in order)

Fixtures: `milestone_board`, `fake_claude_bin`, `hold`, `am`, `spawn_am`, `am_processes`, `detached_pids`. The board is A (a1 -> a2), then B blocked by A (b1), then C blocked by B (c1). So while a1 is held, nothing else is in flight.

1. **Detach.** `hold.arm()` holds `implement`, the fake's default. Then `hold.release(a2, b1, c1)`. `am run --milestone M --repo-dir R --base-branch main --branch-prefix m3 --verify VERIFY --detach` exits 0 with `ok` true. Take `run_id` and `pid` from it, and add `pid` to `detached_pids`.
2. **Watch starts.** Run `spawn_am("watch", run_id, "--follow")`. Read stdout one line at a time on a daemon reader thread that feeds a `queue.Queue`. Each `get` is bounded by `DEADLINE`, so a broken run fails instead of hanging. The first line is the hello: `event == "watch"`, `schema == 1`, with `am` and `runs_dir` present and no `ok` key (it is a stream, not a refusal). `runs_dir` equals `paths.data_dir() / "runs"`. Every later line is a JournalLine (`seq`, `ts`, `run_id`, `event` in `store.EventKind`) with `run_id == run_id`.
3. **Held.** `_until(hold.held_marker(a1).exists)`. `am status run_id --repo-dir R` reports `data.run.status == "started"` and a live `data.control.lease`.
4. **Pause.** `am pause run_id --repo-dir R` exits 0. Its `data` has `run_id == run_id`, `command == "pause"`, `effective == "pause"` and `already_requested is False`.
5. **Applied before release.** Poll with `_until` until `am status` shows `data.control.requests` holding one `pause` row with a non-null `handled_at`. That is the cross-process stand-in for `test_live_control.py`'s `_signal_when_applied`. Only after that, call `hold.release(a1)`.
6. **Parked.** `_until` `report.json` exists under `paths.data_dir()/"runs"/run_id`, then `_until(_lease(R, run_id) is None)`. The report is `ok` true, and its `data` has `paused is True`, `run_id == run_id` and `resume == f"am resume {run_id}"`. It has none of `escalated`, `failed_phase`, `integrated` or `done` (the `CONTROL_KEYS_NEVER_PRESENT` of `test_live_control.py`), and `a1` is not in `completed`. `stopped` names a1 parked before `review`, because a control never cancels the running `implement`. The row shape is `orchestrate.stopped_row`: `{"story": A, "subtask": a1, "before_phase": "review"}`. Confirm this against the run rather than assume it. `am status` now reports `run.status == "stopped"`. The watch stream has delivered a `run_upsert` whose `payload.status == "stopped"`.
7. **Resume (foreground).** `am resume run_id --repo-dir R --verify VERIFY` exits 0 (`resume_run` refuses a live lease, C10, so step 6's lease wait is load-bearing). Its `data` has `done is True`, `resumed is True`, `run_id == run_id`, no `escalated`, `a1` in `completed`, and `set(integrated.merged) == set(stories.values())`. `am status` then reports `run.status == "done"`, and `_lease` is `None`.
8. **Stream continues across lives.** Fetch the one-shot `am watch run_id` (no `--follow`). Keep draining the queue until the stream's last `seq` equals the one-shot's max `seq`. The stream's JournalLines (everything after the hello) must equal the one-shot's `data.events` exactly, in the same order. Their `seq` values must be `1..N` with no gap and no duplicate across the pause/resume boundary. The `run_upsert` statuses, in stream order and with consecutive repeats collapsed, must contain `started`, `stopped`, `started` and `done` as a subsequence.
9. **Stream ends cleanly.** Send `SIGINT` to the watch child. It exits 0 and its stderr file (`am_processes.stderr_of`) is empty.

## Error paths

The test itself exercises no refusal. The existing unit and live-control tests cover the C8 and C10 refusals. Inside the test, every wait is a bounded `_until` or queue `get` that ends in `pytest.fail` with a message naming the step. Teardown never leaves a process behind: `detached_pids` killpg's the detached session, `am_processes.close` kills the watch child, and `hold` calls `release_all`.

## Tests

| Test | File | Tier | Why this tier |
|---|---|---|---|
| `test_a_detached_milestone_run_is_watched_paused_and_resumed_to_done` | `tests/e2e/test_detached_pause_resume.py` (new) | `@pytest.mark.e2e_fake` (explicit) | Spawns real `am` processes, a detached session-leader child, real git and brd, and the fake `claude`. That is production wiring under the fake harness, which the CLAUDE.md placement rule puts in `e2e_fake`, not unit or `git`. It is the one test for this scenario family. |

Tests first (TDD): write the test and run it. If it passes on this branch with no production change, the card is done.

## Verification

- `uv run pytest -m e2e_fake tests/e2e/test_detached_pause_resume.py`: the new test passes. It is excluded from the default run by `addopts`.
- `uv run pytest`: the default unit + git suite stays green.
- There is no lint or typecheck command.

---

# 3.3 e2e_fake detach/watch/pause/resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one explicitly marked `e2e_fake` test that drives a detached milestone run through `am watch --follow`, `am pause` and a foreground `am resume` to `done`, asserting the exact sequence the plugin relies on.

**Architecture:** A single new test module, `tests/e2e/test_detached_pause_resume.py`, mirroring `tests/e2e/test_detached_run.py` (same constants, `_until`, `_lease`, `detached_pids`, same detach argv). The watch child is spawned with the existing `spawn_am` fixture (stdout is a pipe, stderr a per-child file); a small `_Stream` helper reads its stdout on a daemon thread into a `queue.Queue` so every read is bounded. Ordering comes from the fake's hold markers, the `run_controls.handled_at` column surfaced by `am status`, `report.json` and the lease row, never from sleeps. No production code changes.

**Tech Stack:** Python, pytest, the real `am` Typer app run as child processes (`python -c "from agent_manager.cli import app; app()"`), real git, real brd, the fake `claude` (`tests/e2e/fake_claude.py`).

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-3-3-e2e-fake-detach-ef6e5633/docs/superpowers/specs/task-3-3-e2e-fake-detach-ef6e5633-design.md` (prepended verbatim above).

## Global Constraints

- Exactly one new test, in the new file `tests/e2e/test_detached_pause_resume.py`, marked `@pytest.mark.e2e_fake` explicitly.
- No production code changes. If the test exposes a bug, stop and report it; do not widen the card.
- `PREFIX = "m3"`, `VERIFY = "git rev-parse --verify HEAD"` (must equal `tests/e2e/conftest.py` `VERIFY_COMMANDS[0]`), `--base-branch main`.
- Do not re-assert 3.2's coverage: detach envelope key set, log/report file modes, `getsid`, the `am runs` row, the child outliving its parent.
- Do not use `watch --from-now`, `am logs --follow`, or the 2.x `am runs` fields (`lease`, `progress`).
- Ordering by markers, control rows and the lease row only; every wait is a bounded `_until` or bounded `queue.get` ending in `pytest.fail` naming the step.
- Branch `ami/task-3-3-e2e-fake-detach-ef6e5633`, cut from `ami/task-3-2-am-run-detach-aff9fdbf`; never rebase onto master.
- Verification: `uv run pytest -m e2e_fake tests/e2e/test_detached_pause_resume.py` and `uv run pytest`. There is no lint or typecheck command.

## Facts read from the code on this branch (the test depends on them)

- `cli._watch_hello()` (`src/agent_manager/cli.py:2060`) returns `{"event": "watch", "schema": 1, "am": __version__, "runs_dir": str(paths.data_dir() / "runs")}`.
- `cli.watch_for` (`cli.py:1982`) refuses a `RUN_ID` with no journal (`UnknownRunError`) as an exit-3 envelope before any stream line: this is the spec's candidate bug (a). The one-shot payload is `{"events": [...]}`, each event `JournalLine.model_dump(mode="json")`, so every key of `store.JournalLine.model_fields` is present.
- `_stream_watch` (`cli.py:2154`) returns on `KeyboardInterrupt`, so SIGINT ends the stream with exit 0 and nothing on stderr.
- `store.EventKind` (`src/agent_manager/store.py:269`) is `Literal["run_upsert", "story_upsert", "subtask_upsert", "phase_upsert", "attempt_upsert"]`; a `run_upsert`'s `payload` is `run.model_dump(mode="json", exclude={"stories"})`, so `payload["status"]` is the run status.
- `am status` data: `run` holds `RUN_IDENTITY` (`cli.py:195`, includes `status`); `control` is `control_view` (`cli.py:267`): `{"lease": {pid, host, heartbeat_at, accepting, live, acquired_at} | None, "requests": [{command, requested_at, handled_at}], "claims": [...]}`.
- `am pause` data (`cli.request_control`, `cli.py:2624`): `run_id`, `command`, `effective`, `requested_at`, `already_requested`, `message`.
- Paused milestone payload (`orchestrate.controlled_payload`, `src/agent_manager/orchestrate.py:166`): `paused`, `run_id`, `stopped` (rows from `stopped_row`: `story`, `subtask`, `before_phase`), `completed`, `pending`, `warnings`, `resume`. Run recorded `stopped` (`orchestrate.py:1871`).
- Done milestone payload (`orchestrate.py:1909-1925`): `done`, `run_id`, `levels`, `completed`, `tips`, `warnings`, `integrated` (`branch`, `worktree`, `merged`, `resolved`); on a resume `report()` adds `resumed: True`.
- `cli.resume_run` (`cli.py:2386`) refuses a live lease (C10) and, for a milestone run, calls `orchestrate.run_milestone(None, ..., resume_run_id=run.id)` in the foreground; `am resume` takes `--repo-dir` and repeated `--verify`, no `--base-branch`.
- `AmProcesses.spawn` (`tests/e2e/conftest.py:660`) gives stdout as a text pipe and stderr as a file (`stderr_of`). Do NOT call `finish` on the watch child: `communicate` would compete with the reader thread for stdout. Use `child.wait(timeout=...)`.
- The `hold` fixture sets env through `monkeypatch`; every `am` child (and the detached grandchild, and the foreground resume) inherits it.

## Review Focus

These are the failure modes the spec implies that this single test does not exercise. The card limits the deliverable to exactly one test and forbids widening, so none of them gets a new test here; each line says where it is (or is not) covered so the reviewer can judge it.

- `am watch RUN_ID --follow` started in the instant between the detach envelope and the run's first journal line: a plugin expects a stream, the code refuses with exit 3 (candidate bug (a)). This test hits the window only if the race goes that way; if it does, the hello assertion fails with a message naming (a), and the executor reports it rather than fixing it.
- A stale `report.json` from the paused life remaining after a foreground `am resume` finishes: a plugin that reads `report.json` to learn the outcome would see `paused: true` for a run that is `done` (candidate (b)). Deliberately not asserted, per spec.
- A second `am pause` on the same life: expected `already_requested: true` and no second row. Covered by the live-control unit tests, not here.
- The watch consumer closing its pipe early (plugin crash): expected exit 0, no traceback on stderr. Covered by the existing `am watch` tests (`BrokenPipeError` path), not here.
- `am resume` attempted while the paused detached child still holds a live lease: expected the C10 exit-3 refusal. Covered by existing resume tests; this test avoids it by waiting for `_lease(...) is None`.

---

### Task 1: The detached pause/resume e2e_fake test

**Files:**
- Create: `tests/e2e/test_detached_pause_resume.py`
- Read only (do not modify): `tests/e2e/test_detached_run.py`, `tests/e2e/conftest.py`, `src/agent_manager/cli.py`, `src/agent_manager/orchestrate.py`, `src/agent_manager/store.py`, `src/agent_manager/detach.py`

**Interfaces:**
- Consumes (from `tests/e2e/conftest.py`, unchanged): fixtures `milestone_board -> dict` (`root: Path`, `milestone: str`, `stories: {"A","B","C"} -> str`, `subtasks: {"A": [a1, a2], "B": [b1], "C": [c1]}`), `fake_claude_bin`, `hold` (`arm(phase=None)`, `held_marker(card_id) -> Path`, `release(*card_ids)`), `am(*args) -> tuple[int, dict]`, `spawn_am(*args) -> subprocess.Popen`, `am_processes` (`stderr_of(child) -> str`). From production: `cli.resolve_repo_dir`, `store.open_db`, `store.read_lease`, `store.LeaseRow`, `store.EventKind`, `store.JournalLine`, `paths.data_dir`, `detach.REPORT_NAME`.
- Produces: the test `test_a_detached_milestone_run_is_watched_paused_and_resumed_to_done` and module-private helpers `_until`, `_lease`, `_status`, `_run_statuses`, `_collapse`, `_is_subsequence`, `_Stream`, fixture `detached_pids`. Nothing outside this file uses them.

- [ ] **Step 1: Confirm the branch base and that the target file does not exist yet**

Run (from `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-3-3-e2e-fake-detach-ef6e5633`):

```bash
git branch --show-current
git merge-base --is-ancestor ami/task-3-2-am-run-detach-aff9fdbf HEAD && echo "3.2 included"
ls src/agent_manager/detach.py tests/e2e/test_detached_run.py
ls tests/e2e/test_detached_pause_resume.py
```

Expected: branch `ami/task-3-3-e2e-fake-detach-ef6e5633`, `3.2 included`, both 3.2 files listed, and `ls: cannot access 'tests/e2e/test_detached_pause_resume.py': No such file or directory`. If 3.2 is not an ancestor, stop and report: the test cannot run without `--detach`.

- [ ] **Step 2: Run the not-yet-existing test to see RED**

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_pause_resume.py -v`
Expected: FAIL (pytest exit 4) with `ERROR: file or directory not found: tests/e2e/test_detached_pause_resume.py`. The scenario has no test.

- [ ] **Step 3: Write the test file**

Create `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-3-3-e2e-fake-detach-ef6e5633/tests/e2e/test_detached_pause_resume.py` with exactly this content:

```python
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
```

- [ ] **Step 4: Run the new test to see GREEN**

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_pause_resume.py -v`
Expected: `1 passed`. A healthy run takes well under the 8-minute e2e_fake tier budget.

If it fails, do NOT change production code and do NOT loosen an assertion to get green. Read the failure plus `<data dir>/runs/<run_id>/run.log` (the detached child's output; the path is in pytest's tmp `xdg/agent-manager/runs/`) and classify:
- Hello line has an `ok` key: candidate bug (a), `am watch --follow` refused a just-detached run. Stop and report it with the envelope.
- `parked["stopped"]` has a different `before_phase`: the spec says confirm this against the run. Stop and report the observed row; do not edit the expected value without a reply.
- Any other mismatch in step 6, 7 or 8 (keys, statuses, seq gaps or duplicates, stream not equal to the one-shot): stop and report the assertion, the observed values and the relevant `run.log` lines as a possible production bug.
- A plain test-code error (typo, wrong fixture name, import): fix the test file only and rerun.

- [ ] **Step 5: Prove the test can fail (mutation check, then revert)**

Temporarily change the expected park phase in step 6 of the test from `"review"` to `"implement"`:

```python
    assert parked["stopped"] == [
        {"story": stories["A"], "subtask": a1, "before_phase": "implement"}
    ], parked
```

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_pause_resume.py -v`
Expected: FAIL with an `AssertionError` on the `parked["stopped"]` line, and teardown leaves no process behind (pytest exits; `pgrep -f fake_claude` and `pgrep -f "agent_manager.cli"` print nothing afterwards).

Then restore the line exactly to:

```python
    assert parked["stopped"] == [
        {"story": stories["A"], "subtask": a1, "before_phase": "review"}
    ], parked
```

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_pause_resume.py -v`
Expected: `1 passed`.

- [ ] **Step 6: Confirm the tier placement**

Run: `uv run pytest tests/e2e/test_detached_pause_resume.py --collect-only -q`
Expected: `no tests collected` / `1 deselected` (the default `addopts` excludes `e2e_fake`).

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_pause_resume.py --collect-only -q`
Expected: exactly one item, `tests/e2e/test_detached_pause_resume.py::test_a_detached_milestone_run_is_watched_paused_and_resumed_to_done`.

- [ ] **Step 7: Run the default suite**

Run: `uv run pytest`
Expected: all pass (unit + git tiers), same counts as before this change plus none (the new test is deselected).

- [ ] **Step 8: Run the sibling e2e_fake detach test, so the shared fixtures are untouched**

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_run.py tests/e2e/test_detached_pause_resume.py -v`
Expected: `2 passed`.

- [ ] **Step 9: Commit**

```bash
git add tests/e2e/test_detached_pause_resume.py
git commit -m "test(e2e_fake): detached milestone run is watched, paused and resumed to done (ef6e5633)"
```

Expected: one commit touching only `tests/e2e/test_detached_pause_resume.py` (plus this plan and the spec if they are not yet committed on the branch). `git status` is clean afterwards.
