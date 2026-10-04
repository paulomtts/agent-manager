# 2.3 e2e_fake: a detached board run, watched, paused and resumed — spec

Card `47969cf6` (subtask of story `22561886`; blocked by `03f027ea`, which
landed `am run --board --detach`).
Parent design: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`
(cited below as "parent", by section and line).

## Summary

This card adds **one test**. It is the parent's `e2e_fake` scenario "a detached
board run followed with `am watch --all`, paused and resumed per milestone"
(parent §Testing, lines 112-115). Everything the scenario uses already exists on
this branch: `orchestrate.detach_board`, the `boards/` log and report, `am watch
--all [--follow]`, `milestone_id` on every board run's `run_upsert`, and per-run
`am pause` / `am resume`. **No file under `src/` changes.** If the test shows a
gap in `src/`, the implementer stops and reports it. Fixing it is not part of
this card.

## Inherited constraints

- A board has no board-level run record or run id; it is one run per milestone
  (parent §Non-goals, line 33). The test therefore never looks for a board run id.
  It finds each milestone's run through the `--all` stream.
- The detach envelope is `{"ok": true, "data": {"board": true, "detached": true,
  "pid", "log", "report", "levels"}}` and has no `run_id`. Each milestone's run is
  created when that milestone is dispatched. Its first `run_upsert` carries
  `milestone_id`, which is never null on a board run (parent §Design 2, lines
  87-91).
- The child writes the final board payload to `<data dir>/boards/<stamp>-<digest>.report.json`
  when the board ends (parent §Design 2, lines 83-86).
- Journal and watch schema stay 1, and JSON changes are additive only (parent
  §Compatibility, line 102; card text). The hello line's `schema` must be `1`.
- Boards with no inter-milestone `blocked_by` behave as before stacking existed
  (parent §Compatibility, line 100). The fixture's two milestones are independent,
  so both are on `--base-branch main`.
- Tier rules come from CLAUDE.md "Test tiers": `@pytest.mark.e2e_fake` means
  production wiring under the fake `claude`. It is opt-in with
  `uv run pytest -m e2e_fake`, has one test per scenario family, and the tier
  budget is ≤8 min.

## Observable behavior the test pins

The test uses the `two_milestone_board` fixture (`tests/e2e/conftest.py:507`).
It has milestones `first` (stories A, B → subtasks a1, b1) and `second` (stories
C, D → c1, d1). There are no blockers, and `UNION_ATTRIBUTE` is set so Integrate
needs no resolver. The test also uses `fake_claude_bin`, `hold`, `am`,
`spawn_am`, `am_processes`, and a module-local `detached_pids` fixture that
kills each session with `killpg` at teardown. It runs under the default
`--max-concurrent` (1). It passes no `--branch-prefix`, so each milestone uses
its own card-derived prefix. The test never hardcodes a branch name.

`hold.arm()`, then `hold.release(b1, c1, d1)`. Only a1's `implement` is held.

1. **Detach.** `am run --board --repo-dir ROOT --base-branch main --verify VERIFY --detach`
   - exits 0 with `ok: true`.
   - `data["levels"] == [{"level": 0, "milestones": [first, second]}]`. The
     order is `dag.board_levels`' input order. The implementer confirms this by
     running the test. If the order differs, compare the set of milestone ids
     instead of hardcoding the order.
   - `data["pid"]` is appended to `detached_pids`.
   - The other envelope keys, file modes, `getsid` and the `am runs` row are
     already pinned by `test_detached_run.py::test_a_detached_board_run_outlives_its_parent_and_leaves_its_report`,
     so this test does **not** assert them again.
2. **Watch every run.** `spawn_am("watch", "--all", "--follow")` starts right
   after the detach returns, before any milestone run necessarily exists.
   - The first line is the hello line `{"event": "watch", "schema": 1, "am": ..., "runs_dir": str(paths.data_dir() / "runs")}`.
     It has no `ok` key.
   - Every later line is one JournalLine. Its keys are exactly
     `store.JournalLine.model_fields`, and its `event` is in
     `get_args(store.EventKind)`. Its `run_id` may be either milestone's run.
     The `_Stream` helper must not pin one run id (see "Test helpers").
   - Runs that appear after the watcher started are picked up (`_poll_watch`
     lists the runs again on every pass).
3. **Each milestone's first `run_upsert` carries its `milestone_id`.** The test
   drains the stream until it has seen a `run_upsert` for two distinct
   `run_id`s. For each run, the **first** `run_upsert` in stream order has
   `payload["milestone_id"]` set to a milestone's full card id. The two values are
   exactly `{first, second}`. This gives the map `run_of[milestone] -> run_id`,
   and the two run ids differ.
4. **Held.** The test waits on `hold.held_marker(a1)`. Then `am status run_of[first]`
   shows `run.status == "started"` and a live lease.
5. **Pause one milestone's run only.** `am pause run_of[first] --repo-dir ROOT`
   exits 0. Its data is `{run_id: run_of[first], command: "pause", effective: "pause",
   already_requested: false}`. The test waits until `am status run_of[first]`
   `control.requests` is exactly one `pause` row with `handled_at` set. Only then
   does it call `hold.release(a1)`. Nothing is sent to `second`'s run.
6. **The board ends with one milestone stopped and the other done.** The test waits
   for the file at `data["report"]` to exist, then for both runs' leases to be
   released (`store.read_lease(...) is None`). The report holds:
   - `ok: true` (the envelope), `data.board: true`, `data.ok: false`. `ok` is true
     only when every entry is `done` (`orchestrate.py:2506`).
   - `data.levels` equals the detach envelope's `levels`.
   - `data.milestones` has two entries, in level order. The `first` entry has
     `status == "stopped"` (`milestone_status`, `orchestrate.py:2350-2366`),
     `paused: true`, `run_id == run_of[first]`, `resume == f"am resume {run_of[first]}"`,
     a1 **not** in `completed`, and a1 present in `stopped` as
     `{"story": A, "subtask": a1, "before_phase": "review"}`. None of
     `escalated`, `failed_phase`, `integrated` or `done` is present
     (`controlled_payload`, `orchestrate.py:165-198`). The test does **not** pin
     `pending` or b1's place: with one shared slot, whether lane B ran before the
     pause depends on scheduling.
   - The `second` entry has `status == "done"`, `done: true`, `run_id == run_of[second]`,
     and `set(integrated.merged) == {C, D}`.
   - `am status` reports `stopped` for `first`'s run and `done` for `second`'s.
   - The stream is drained until `first`'s run has a `run_upsert` with status
     `stopped`.
7. **Resume the paused milestone in the foreground.** `am resume run_of[first] --repo-dir ROOT --verify VERIFY`
   exits 0. Its data has `done: true`, `resumed: true`, `run_id == run_of[first]`,
   no `escalated` key, a1 in `completed`, and `set(integrated.merged) == {A, B}`.
   Afterwards `am status` shows `done` and the lease is gone. The board report
   file is **not** rewritten by the resume: re-reading it gives the same bytes as
   in step 6. The board's child has ended, and resume is a per-run command.
8. **One stream, both runs, exactly the journals.** A one-shot `am watch --all`
   exits 0 with `data.events`. The test checks:
   - The set of `run_id`s in it is exactly `{run_of[first], run_of[second]}`.
     The data dir is per-test (`tests/conftest.py:91`).
   - The test drains the follow stream until, for each run, its last streamed
     `seq` reaches that run's highest `seq` in the one-shot.
   - Then, per run, the follow stream's events (in stream order) equal the
     one-shot's events for that run, and their `seq`s are `1..N`. A `seq` is per
     run. The test never asserts a global order across runs.
   - Collapsed `run_upsert` statuses: `first`'s run contains the subsequence
     `started, stopped, started, done`. `second`'s run ends in `done` and has no
     `stopped`.
9. **Ctrl-C.** `SIGINT` to the watcher makes it exit 0 within `DEADLINE`, with
   empty stderr.

### Error paths the test must surface (not handle)

Each wait goes through `_until(predicate, what)` or `_Stream.drain_until(..., what)`,
bounded by `DEADLINE = 240.0`. A run that never reaches the expected state fails
with `pytest.fail` and a message that names the step. It never hangs. The
following each fail with their own message:
- the hello line is a refusal envelope;
- the watch stream reaches EOF early;
- fewer than two runs ever appear;
- a run's first `run_upsert` has a null or unexpected `milestone_id`;
- the pause is never handled;
- the report never appears.

The order of steps comes only from hold marker files, `handled_at`, the report
file, lease rows and stream contents. No step uses `time.sleep` to order events.

## Test helpers (module-local, copied rather than shared)

The test copies these from `tests/e2e/test_detached_pause_resume.py`, matching
that module's style (no `PREFIX`: a board derives its own): constants `VERIFY = "git rev-parse --verify HEAD"`
(must equal the e2e conftest's `VERIFY_COMMANDS[0]`), `DEADLINE`, `POLL`,
`CONTROL_KEYS_NEVER_PRESENT`, `EVENT_KINDS`, `JOURNAL_KEYS`, and the helpers
`_until`, `_lease`, `_status`, `_run_statuses`, `_collapse`, `_is_subsequence`,
plus the `detached_pids` fixture. Two helpers change:
- `_Stream(child)` takes no `run_id`. `drain_until` checks keys and kind but not
  `run_id`. The class adds `by_run() -> dict[str, list[dict]]`, which groups
  `events` by `run_id` in stream order.
- `_run_statuses(events)` is applied to one run's events (from `by_run()`).

## Tests

| Test | Tier | Why this tier |
|---|---|---|
| `tests/e2e/test_detached_board_pause_resume.py::test_a_detached_board_is_watched_with_all_and_one_milestone_is_paused_and_resumed` | `e2e_fake` (explicit `@pytest.mark.e2e_fake`) | It spawns real `am` child processes (a detached child in its own session, a follow watcher), real git and brd, and the fake `claude` on `PATH`: production wiring under the fake harness. That is the definition of `e2e_fake`, and it is the parent's named `e2e_fake` scenario (parent line 114-115). It is too slow and spawns too much for `unit`/`git`. It needs no real `claude`, so it is not `e2e`. |

There are no other tests. The detach mechanics already have their own `e2e_fake`
test (card 03f027ea), and the pure functions have `unit` tests.

TDD note: the behavior already exists, so the new test is expected to pass on its
first full run. To show it is not vacuous, the implementer briefly inverts one
key assertion and records that it fails, then restores it. Suitable assertions
are step 3's `milestone_id` equality or step 6's `status == "stopped"`. The
inverted version is not committed.

## Verification

- `uv run pytest -m e2e_fake tests/e2e/test_detached_board_pause_resume.py` passes.
- `uv run pytest` (default tiers) stays green. It does not collect this test
  (`pyproject.toml` `addopts` excludes `e2e_fake`).
- The new test's wall time is well inside the 8-minute `e2e_fake` tier budget
  (aim: under 60 s).

## Out of scope

- Any change under `src/` (see Summary).
- Re-asserting what card 03f027ea's test pins: envelope key set, `boards/` path
  shape and stamp, 0600 modes, `getsid`, `am runs` lease row.
- Milestone stacking scenarios: "the second's worktree contains the first's
  commits" and the three-milestone chain (parent lines 112-114). Those belong to
  sibling cards.
- Pausing or resuming the whole board, and `am cancel` on a board run. The parent
  defines neither.
- Running with `--max-concurrent > 1`, `--branch-prefix`, or `--dry-run`.
- Editing `tests/e2e/test_detached_run.py`'s module docstring line "Watch, pause
  and resume are sibling 3.3's scenario". It is left alone, and this module's
  docstring states that it covers the board form of that scenario.

## Plan handoff notes (for the plan author)

- This is a single task. The new module is
  `tests/e2e/test_detached_board_pause_resume.py`. Follow writing-plans format:
  write the test, run it, do the sanity-fail, restore, run the default suite,
  commit.
- Module docstring in the sibling style. It names the card (`47969cf6`), the tier,
  production wiring, that order comes from markers / `handled_at` / report /
  lease rows and never from sleeps, and what test_detached_run.py already pins.

## Review Focus candidates

1. **A run's directory appears before its journal's first line.** `--all` must
   skip such a run and pick it up later, not fail. The stream only ever carrying
   one run is the symptom.
2. **The slot order is not fixed.** With one shared slot, `second` may finish
   before a1 is ever held, or after the pause. The test must pass under either
   order. It must not assert `second`'s status before the report exists.
3. **The lease of the paused run is released while the board child keeps running
   the other milestone.** Resume happens only after the report exists, so the
   resume cannot race the child's store or git lock.
4. **The report file is read once complete.** It is written atomically
   (card 03f027ea), so `exists()` and then a read is safe. A partial read would be
   a regression in `detach.write_board_report`, not something this test retries.
5. **Watcher teardown on failure.** If an assertion fails mid-test, the
   `am_processes` / `detached_pids` fixtures must still kill the watcher and the
   detached session, so a failed run leaves no stray `am` processes.

---

# 2.3 e2e_fake detached board watch/pause/resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `e2e_fake` test that detaches a two-milestone board, follows it with `am watch --all --follow`, pauses one milestone's run, resumes it in the foreground, and checks that the stream carries exactly both runs' journals.

**Architecture:** A new test module, `tests/e2e/test_detached_board_pause_resume.py`, copies its helpers from `tests/e2e/test_detached_pause_resume.py`. Two changes: `_Stream` pins no run id and adds `by_run()`. The test drives real `am` child processes, real git and brd, and the fake `claude`. Order comes only from hold markers, `handled_at`, the board report file, lease rows and stream contents. No file under `src/` changes.

**Tech Stack:** Python 3, pytest (`e2e_fake` marker), `subprocess`/`threading`/`queue`, the e2e conftest fixtures (`two_milestone_board`, `fake_claude_bin`, `hold`, `am`, `spawn_am`, `am_processes`).

**Spec:** `docs/superpowers/specs/2-3-e2e-fake-a-detached-47969cf6.md` (prepended above).

## Global Constraints

- No file under `src/` changes. If the test exposes a gap in `src/`, stop and report it. Do not fix it on this card.
- Journal and watch schema stay `1`. The hello line's `schema` must be `1`.
- The test is marked explicitly `@pytest.mark.e2e_fake`. It is opt-in with `uv run pytest -m e2e_fake`, and the tier budget is ≤8 min (aim: under 60 s for this test).
- `VERIFY = "git rev-parse --verify HEAD"` must equal the e2e conftest's `VERIFY_COMMANDS[0]`.
- `DEADLINE = 240.0` bounds every wait. No `time.sleep` is used to order events; the only sleep is `_until`'s `POLL`.
- No `--branch-prefix`, no `--max-concurrent`, no `--dry-run`. The test never hardcodes a branch name and never looks for a board run id.
- Do not re-assert what `test_detached_run.py::test_a_detached_board_run_outlives_its_parent_and_leaves_its_report` pins: envelope key set, `boards/` path shape and stamp, 0600 modes, `getsid`, the `am runs` lease row.
- Do not edit `tests/e2e/test_detached_run.py`.

## Facts read from the code on this branch (the test depends on them)

- `two_milestone_board` (`tests/e2e/conftest.py:507-537`) returns `{"root", "milestones": {"first", "second"}, "stories": {"first": [A, B], "second": [C, D]}, "subtasks": {"first": [a1, b1], "second": [c1, d1]}}`. There are no blockers, and `UNION_ATTRIBUTE` is written to `.git/info/attributes`.
- Each board entry is `{"milestone_id": card.id, "status": milestone_status(payload), **payload}` (`orchestrate.py:2745`), so it also carries the run payload's `run_id`, `paused`, `resume`, `stopped`, `completed`, `done` and `integrated`.
- `run_board_engine` returns `{"ok": all(status == "done"), "board": True, "levels": pre.levels_payload, "milestones": entries}` (`orchestrate.py:2506-2511`). The detached child writes it wrapped in the `ok: true` envelope to `data["report"]`.
- `controlled_payload` (`orchestrate.py:167-198`) for a pause: `paused`, `run_id`, `stopped`, `completed`, `pending`, `warnings`, `resume`, and never `escalated`, `failed_phase`, `integrated` or `done`.
- A `run_upsert` payload is `run.model_dump(mode="json", exclude={"stories"})` (`store.py:1592`), which includes `milestone_id` and `status`.
- `am watch --all` (`cli.watch_for`, `cli._poll_watch`) lists `paths.list_run_ids()` again on every pass and skips a run with no journal yet. One-shot events are ordered `(run_id, seq)`.
- `Hold.held_marker(card_id)` and `Hold.release(*card_ids)` (`tests/e2e/conftest.py:795-805`). `hold.arm()` with no phase holds `implement`.

## Review Focus

1. **A run's directory exists before its journal's first line.** `--all` must skip that run and pick it up on a later pass. Pinned by step 3: the drain waits for a `run_upsert` from two distinct run ids, and fails with "fewer than two runs" wording if only one ever appears.
2. **The slot order is not fixed.** `second` may finish before a1 is held or after the pause. The test asserts nothing about `second`'s status until the report exists, and it does not pin `pending` or b1's place.
3. **The paused run's lease is released while the child still runs the other milestone.** Resume happens only after the report exists **and** both leases are gone (step 6). Resume refuses a live lease, so those waits are load-bearing.
4. **The report file is read once, when complete.** It is written atomically, so `exists()` followed by one read is enough. Step 7 re-reads its bytes and asserts they are unchanged after the resume.
5. **Watcher teardown on failure.** `spawn_am` tracks the watcher in `am_processes`, whose `close` kills it. `detached_pids` killpg's the detached session, and `hold` calls `release_all` at teardown. A failing assertion therefore leaves no stray `am`. Pinned by using those fixtures, not raw `subprocess.Popen`.

---

### Task 1: The detached board watch/pause/resume e2e_fake test

**Files:**
- Create: `tests/e2e/test_detached_board_pause_resume.py`
- Read only (do not modify): `tests/e2e/test_detached_pause_resume.py`, `tests/e2e/conftest.py`, `tests/e2e/test_detached_run.py`

**Interfaces:**
- Consumes: the fixtures `two_milestone_board`, `fake_claude_bin`, `hold`, `am`, `spawn_am`, `am_processes` (`tests/e2e/conftest.py`). `am(*args) -> tuple[int, dict]` runs a real `am` child to completion. `spawn_am(*args) -> subprocess.Popen` starts a tracked child with stdout piped. `am_processes.stderr_of(child) -> str`. From `agent_manager`: `cli.resolve_repo_dir`, `paths.data_dir`, `store.open_db`, `store.read_lease`, `store.LeaseRow`, `store.JournalLine`, `store.EventKind`.
- Produces: nothing other tasks rely on (single task).

- [ ] **Step 1: Write the test module**

Create `tests/e2e/test_detached_board_pause_resume.py` with exactly this content:

```python
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

from agent_manager import cli, paths, store

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
    assert hello["schema"] == 1, hello
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
```

Notes for the implementer:
- Step 1 compares the level's milestones as a sorted list, so the detach order does not matter. The spec allows this fallback. Step 6 still pins that the report's `milestones` follow `levels` order exactly.
- In the step 6 lease loop, `milestone` is bound when `_until` is called. Each `_until` finishes before the loop moves on, so the late-binding lambda is safe.
- `stopped["stopped"]` is checked with `in`, not `==`. With one shared slot, whether b1 is also parked depends on scheduling, and the spec forbids pinning b1's place.

- [ ] **Step 2: Run the test (it is expected to pass at once; the behavior already exists)**

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_board_pause_resume.py -v --durations=0`
Expected: `1 passed`, with the test's wall time well under 60 s.

If it fails:
- If the failure is in this test's own logic (a wrong key name, or an order assumption the spec says not to make), fix the test and rerun.
- If it shows a gap in `src/`, for example `--all` never picks up the second run, a `run_upsert` without `milestone_id`, the report never written, or the resume rewriting the report, **stop**. Report the failing step and its message. Do not change `src/`.

- [ ] **Step 3: Sanity-fail: prove the key assertion is not vacuous**

Temporarily change this line in step 6 of the test:

```python
    assert stopped["status"] == "stopped", stopped
```

to:

```python
    assert stopped["status"] == "done", stopped
```

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_board_pause_resume.py -v`
Expected: FAIL at that assertion, with an `AssertionError` showing the entry with `'status': 'stopped'`. Record the failure message for the final report.

- [ ] **Step 4: Restore the assertion and rerun**

Change the line back to:

```python
    assert stopped["status"] == "stopped", stopped
```

Confirm `git diff --stat` shows only the new file (it is untracked, so check `git status --short` too). Then run:
Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_board_pause_resume.py -v`
Expected: `1 passed`.

- [ ] **Step 5: Run the default suite (the new test must not be collected)**

Run: `uv run pytest`
Expected: all pass. `tests/e2e/test_detached_board_pause_resume.py` is deselected by `addopts`' `-m "not brd and not e2e_fake and not soak and not e2e"`.

Optionally confirm deselection: `uv run pytest tests/e2e/test_detached_board_pause_resume.py` should report `1 deselected`.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/test_detached_board_pause_resume.py
git commit -m "test: e2e_fake detached board watched with --all, one milestone paused and resumed (card 47969cf6)"
```
<!-- task-pipeline: validated -->
