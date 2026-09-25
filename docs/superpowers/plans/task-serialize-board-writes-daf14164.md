<!-- task-pipeline: validated -->
# Serialize board writes and rollups, and stress brd itself (card daf14164)

Parent story 19b21273 "Serialize the shared git and board resources". This narrows decision P3 of `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` (lines 58-65) to its board half. The git half (the per-repo `git worktree add` lock in `steps/worktree.py`) was delivered by sibling 88150552 and is out of bounds here: do not touch `steps/worktree.py` or its tests, and add no `git worktree prune`.

## Scope

1. `src/agent_manager/board.py` gets one module-level reentrant lock, `WRITE_LOCK = threading.RLock()`. It is public because `steps/rollup.py` has to take it too. `board.set_status` holds it for its whole body (`_run`, `_decode`, `_validated`). `show`, `tree` and `roots` stay unlocked.
2. `src/agent_manager/steps/rollup.py`: `rollup.set_status` holds `board.WRITE_LOCK` around its whole body. That covers writing the card, then every iteration of the ancestor walk (`board.show`, `board.tree`, `rollup_status`, the conditional `board.set_status`). The whole thing is one critical section, because a rollup reads, then modifies, then writes. The lock has to be an `RLock` because the nested `board.set_status` calls take it again on the same thread. These stay as they are: the first parameter named `card` (the engine binds by name), the return shape `{"card", "status", "rolled_up"}`, `board.BoardError` left uncaught, and the `MAX_ANCESTRY_DEPTH = 16` behaviour.
3. Retry only if needed. If the brd stress test below shows that `brd update` fails when called concurrently, `board._run` gets a bounded retry: a few attempts with a short backoff, and it retries only when brd reports its lock error. Before writing the code, check what brd actually prints when this happens. It could be an `{"ok": false, "error": {...}}` envelope on stdout, or a non-zero exit with only stderr. The retry must recognise the shape brd really uses. Every other failure is raised on the first attempt, exactly as it is today. When the attempts run out, the last failure surfaces the same way it would have without the retry: as `BoardError` from `_run`, or through `_decode`. If brd does not fail under the stress test, `_run` is left unchanged.
4. Module docstrings stay truthful. In `board.py`, `set_status` is still the entire write surface, nothing about a run is written to the board, and brd is only ever invoked with argv lists, never through a shell. Add one sentence about the write lock, plus a note on the retry if one was added. In `rollup.py`, add a sentence saying the walk runs under the board lock.

Only threads sharing one process are supported. Two `am` processes on the same repository are not supported, and this card adds no cross-process locking such as file locks.

## Observable behaviour

- With `--max-concurrent 1`, the sequential path behaves exactly as before: the same writes happen in the same order and the results are unchanged.
- Concurrent `rollup.set_status` calls on sibling subtasks can no longer lose a parent update. The last walk to run sees every child's final status and writes the correct parent and milestone status.
- Only a lock-type failure changes. A brd lock error that clears within the retry budget is no longer a `BoardError`. That applies only if the retry was needed.

## Error paths

- A `BoardError` raised anywhere inside the rollup critical section propagates. Because the lock is released through a `with` block, a failure cannot leave it held.
- The depth guard still raises `BoardError("exceeded maximum ancestry depth")` inside the lock, and the lock is released afterwards.
- Retry exhaustion (if retry exists) raises the final attempt's error unchanged. Non-lock failures such as a missing `brd`, card not found, or an invalid status are never retried.

## Tests

Tier rule, from design spec section 14 (line ~488): steps and the board adapter are tested against a real temporary `brd` board over subprocess, with brd never mocked. Narrow pure checks go at the bottom of `tests/test_board.py`. Nothing here belongs in `tests/e2e`.

- `tests/test_board.py`, `test_brd_update_survives_concurrent_writers` (board adapter tier, real temp brd, `requires_brd`): a temp board with 8 cards. 8 threads each call `subprocess.run(["brd", "update", id, "--status", s], cwd=board_root)` directly on their own card, bypassing `board.py` and its lock, 25 writes each, cycling through `todo`/`in_progress`/`done` (never `blocked`). Every call must return code 0 with an `ok: true` envelope, and afterwards each card's `brd show` status must equal the last status written to it. The test is not weakened if brd flakes. It is the evidence for whether the retry is needed. Set `XDG_DATA_HOME` to a tmp dir, the same way `temp_board` in `tests/steps/test_rollup.py` does, so subprocesses inherit it.
- Only if the retry is added: `tests/test_board.py`, `test_run_retries_brd_lock_error_then_succeeds` and `test_run_gives_up_after_bounded_lock_retries` (narrow checks at the bottom, using the existing fake-brd-script pattern at lines 83-100). The fake `brd` counts its attempts in a file and prints brd's real lock-error output N times before succeeding. The tests assert that the call succeeds within budget, that the attempt count is right, and that the error surfaces after the budget. A third check, `test_run_does_not_retry_non_lock_failure`, asserts that a fake brd failing with some other error is invoked exactly once.
- `tests/steps/test_rollup.py`, `test_concurrent_rollups_reach_done` (steps tier, real temp brd, `requires_brd`, reuses `temp_board`, `_add_card` and `_brd_json`): one milestone with 2 stories and 4 subtasks under each. 8 threads each call `rollup.set_status(subtask, "done", repo_dir=root)`, gated by a barrier so they start together. Afterwards `_brd_json` shows both stories and the milestone as `done`, and every thread returned without raising. Loop 20 iterations, each with fresh cards or with the cards reset to `todo`.
- All existing tests in `tests/steps/test_rollup.py` (lines 72-270) and `tests/test_board.py` stay green unchanged, and so does the whole default suite, including `tests/e2e`.

## Verification

`uv run pytest`. There is no typecheck or lint step.

## Final report must state

What `brd update` did under the 8x25 concurrent stress test: whether any call failed, and if so the exact failure output. Also whether the bounded retry in `board._run` was added and, if it was, the attempt count and backoff it uses.

---

# Serialize Board Writes and Rollups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize every brd status write and every whole rollup walk behind one in-process reentrant lock, and prove with a lock-bypassing stress test whether `brd update` itself survives concurrent callers (adding a bounded lock-error retry to `board._run` only if it does not).

**Architecture:** `board.py` gains a public `WRITE_LOCK = threading.RLock()` held for the whole of `board.set_status`; reads (`show`, `tree`, `roots`) stay unlocked. `steps/rollup.py` holds the same lock around its entire body, so the card write plus the ancestor read-modify-write walk is one critical section; nested `board.set_status` calls re-enter the RLock on the same thread. A raw 8x25 `brd update` stress test decides whether `board._run` needs a retry on brd's lock error.

**Tech Stack:** Python 3.12, `threading`, `subprocess`, pytest, the real `brd` CLI over subprocess.

**Spec:** `docs/superpowers/specs/task-serialize-board-writes-daf14164-design.md` (prepended verbatim above). Parent decision: `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` P3 (lines 58-65).

**Branch / worktree:** `m4/task-serialize-board-writes-daf14164` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-serialize-board-writes-daf14164`. All paths below are relative to that worktree. Do not assume any other subtask's code exists beyond what is already on this branch (sibling 88150552's `steps/worktree.py` lock is present; do not touch it).

## Global Constraints

- Lock: exactly one, `board.WRITE_LOCK = threading.RLock()`, public, module-level in `src/agent_manager/board.py`.
- `board.show`, `board.tree`, `board.roots` take no lock.
- `rollup.set_status` keeps its signature `set_status(card: str, status: str, repo_dir: str | Path | None = None) -> dict[str, object]`, its return shape `{"card", "status", "rolled_up"}`, leaves `board.BoardError` uncaught, and keeps `MAX_ANCESTRY_DEPTH = 16`.
- brd is only ever invoked with argv lists, never a shell (`test_board_never_uses_a_shell` must stay green).
- No cross-process locking (no file locks). Two `am` processes on one repository are unsupported.
- Do not touch `src/agent_manager/steps/worktree.py` or its tests; no `git worktree prune`.
- Tests: real temp brd over subprocess, brd never mocked, nothing in `tests/e2e`. Board-adapter tests in `tests/test_board.py` (narrow fake-brd checks at the bottom); rollup tests in `tests/steps/test_rollup.py`.
- The stress test is never weakened if brd flakes; a flake is the evidence for the retry.
- Retry (only if needed): bounded, short backoff, retries only brd's real lock error; every other failure raised on the first attempt; exhaustion surfaces exactly as today.
- Verification: `uv run pytest` (no lint, no typecheck). Whole default suite green, including `tests/e2e` and the `--max-concurrent 1` path.

## Review Focus

- A `BoardError` raised inside the rollup critical section (nonexistent card, depth guard) must not leave `WRITE_LOCK` held; another thread must be able to take it straight after. Pinned in Task 4 (`test_a_failed_rollup_releases_the_board_lock`).
- A caller that already holds `WRITE_LOCK` on the same thread and then calls `rollup.set_status` must not deadlock (it must be an RLock, not a Lock). Pinned in Task 4 (`test_rollup_reenters_a_board_lock_its_own_thread_already_holds`), run on a worker thread with a join timeout so a regression fails instead of hanging the suite.
- Reads must stay unlocked: a `board.show` from another thread must complete while a writer holds the lock. Pinned in Task 1 (`test_reads_do_not_wait_for_the_board_write_lock`).
- The ancestor walk (not just the card write) must be inside the lock, otherwise two sibling rollups can interleave read and write of the shared story. Pinned deterministically in Task 4 (`test_the_whole_rollup_walk_runs_under_the_board_lock`), in addition to the probabilistic race test in Task 5.
- If the retry is added: a non-lock failure (usage error on stderr, `ok:false` envelope such as `CardNotFoundError`) must run brd exactly once. Pinned in Task 3 (`test_run_does_not_retry_non_lock_failure`, parametrized over both shapes).

## File Structure

- Modify `src/agent_manager/board.py`: add `import threading`, `WRITE_LOCK`, wrap `set_status` body, docstring sentence. Conditionally (Task 3) add `import time`, retry constants, `_is_lock_error`, and a retry loop in `_run`.
- Modify `src/agent_manager/steps/rollup.py`: wrap `set_status` body in `with board.WRITE_LOCK:`, docstring sentences.
- Modify `tests/test_board.py`: lock tests (Task 1), brd stress test (Task 2), conditional retry checks at the bottom (Task 3).
- Modify `tests/steps/test_rollup.py`: lock-scope tests (Task 4), concurrent rollup race test (Task 5).

---

### Task 1: `board.WRITE_LOCK` around `board.set_status`

**Files:**
- Modify: `src/agent_manager/board.py:1-30` (docstring, imports, lock constant), `src/agent_manager/board.py:224-242` (`set_status`)
- Test: `tests/test_board.py` (new tests inserted directly after `test_set_status_blocked_propagates_brds_own_rejection`, which ends at line 477)

**Interfaces:**
- Consumes: nothing new.
- Produces: `board.WRITE_LOCK: threading.RLock` (module-level, public). `board.set_status(card_id: str, status: str, *, repo_dir: Path | None = None) -> models.Card` unchanged in signature, now serialized on `WRITE_LOCK`.

- [ ] **Step 1: Write the failing tests**

Add `import threading` to the imports at the top of `tests/test_board.py` (after `import subprocess`, line 11):

```python
import json
import shutil
import subprocess
import threading
from pathlib import Path
```

Insert after `test_set_status_blocked_propagates_brds_own_rejection` (after line 477):

```python
def test_write_lock_is_reentrant():
    # rollup.set_status holds it and then calls board.set_status on the same
    # thread, so a plain Lock would deadlock.
    assert board.WRITE_LOCK.acquire(blocking=False)
    try:
        assert board.WRITE_LOCK.acquire(blocking=False)
        board.WRITE_LOCK.release()
    finally:
        board.WRITE_LOCK.release()


@requires_brd
def test_set_status_waits_for_the_board_write_lock(temp_board):
    subtask = _add_card(temp_board, "Serialize board writes")
    outcome: dict[str, object] = {}

    def write() -> None:
        try:
            outcome["card"] = board.set_status(
                subtask, "in_progress", repo_dir=temp_board
            )
        except BaseException as exc:  # surfaced by the assertions below
            outcome["error"] = exc

    with board.WRITE_LOCK:
        writer = threading.Thread(target=write)
        writer.start()
        writer.join(timeout=1.0)
        # Held by this thread: the writer must still be waiting, and brd untouched.
        assert writer.is_alive()
        assert _brd_json(temp_board, "show", subtask)["status"] == "todo"

    writer.join(timeout=30)
    assert not writer.is_alive()
    assert "error" not in outcome
    assert outcome["card"].status == "in_progress"
    assert _brd_json(temp_board, "show", subtask)["status"] == "in_progress"


@requires_brd
def test_reads_do_not_wait_for_the_board_write_lock(temp_board):
    subtask = _add_card(temp_board, "Serialize board writes")
    outcome: dict[str, object] = {}

    def read() -> None:
        outcome["card"] = board.show(subtask, repo_dir=temp_board)
        outcome["node"] = board.tree(subtask, repo_dir=temp_board)
        outcome["roots"] = board.roots(repo_dir=temp_board)

    with board.WRITE_LOCK:
        reader = threading.Thread(target=read)
        reader.start()
        reader.join(timeout=30)
        assert not reader.is_alive()

    assert outcome["card"].id == subtask
    assert outcome["node"].id == subtask
    assert [node.id for node in outcome["roots"]] == [subtask]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "write_lock or waits_for_the_board_write_lock" -v`
Expected: all three FAIL with `AttributeError: module 'agent_manager.board' has no attribute 'WRITE_LOCK'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/board.py`, replace the module docstring paragraph at lines 3-7 so it reads:

```python
"""The only caller of the `brd` CLI (design §4 line 119).

Four operations cross this seam: read one card, read a card subtree, read
every root of the board, write a card status. Nothing about a *run* is ever
written to the board (decision D5, design §9) -- run state lives in
agent-manager's own SQLite projection and journal, so `set_status` is the
module's entire write surface.

Writes are serialized within one process: `set_status` runs under the
module-level `WRITE_LOCK`, a reentrant lock that `steps/rollup.py` also holds
around its whole read-modify-write walk up a card's ancestors. Reads take no
lock. Nothing here coordinates two separate `am` processes on one repository;
that is not supported.

Every invocation is an argument list handed to `subprocess`. Design §5 line 252
is explicit that the program runs commands itself with argument lists, so
`shell_quote` from the shell-script original does not port and there is no
string to quote: a card id full of shell metacharacters is just one argv
element.

Naming, slugs, branches and ref matching are `dag.py`'s job, not this module's.
"""
```

Replace the imports and add the lock after `BRD` (current lines 18-30):

```python
import json
import subprocess
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_manager import models

M = TypeVar("M", bound=BaseModel)

BRD = "brd"
"""Executable name, resolved on PATH. Argv element zero of every call."""

WRITE_LOCK = threading.RLock()
"""Serializes board writes across threads of one process.

Held for the whole of `set_status`, and by `steps/rollup.py` around its entire
ancestor walk, so a rollup's read-modify-write is one critical section.
Reentrant because the walk calls `set_status` again on the same thread. Reads
(`show`, `tree`, `roots`) do not take it.
"""
```

Replace the body of `set_status` (current lines 239-242) with:

```python
    argv = set_status_argv(card_id, status)
    with WRITE_LOCK:
        completed = _run(argv, repo_dir)
        data = _decode(
            completed.stdout, argv=argv, exit_code=completed.returncode
        )
        return _validated(models.Card, data, argv=argv)
```

and append one paragraph to the end of the `set_status` docstring (before the closing `"""`):

```python

    Runs entirely under `WRITE_LOCK`, so concurrent writers in one process
    reach brd one at a time.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: PASS, including the three new tests, `test_board_never_uses_a_shell`, and every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "Serialize board.set_status on a module-level reentrant write lock"
```

---

### Task 2: Stress `brd update` itself, bypassing the lock

This task produces evidence, not new source code. The test calls `brd` directly via `subprocess`, so it exercises brd with no `board.py` code (and no lock) in the path. It may pass on first run; that is the expected outcome given brd's source (`/home/paulomtts/Code/brd/src/brd/db.py:8-15` opens SQLite with `timeout=10` and WAL). It is not weakened if it fails.

**Files:**
- Test: `tests/test_board.py` (insert after the Task 1 tests, i.e. after `test_reads_do_not_wait_for_the_board_write_lock`)

**Interfaces:**
- Consumes: `temp_board`, `_add_card`, `_brd_json`, `requires_brd` already in `tests/test_board.py`.
- Produces: the go / no-go decision for Task 3, plus the exact failure output for the final report.

- [ ] **Step 1: Write the stress test**

```python
_STRESS_STATUSES = ("todo", "in_progress", "done")
_STRESS_WRITERS = 8
_STRESS_WRITES_EACH = 25


def _stress_status(writer: int, write: int) -> str:
    # Offset by writer so the cards end on different statuses, not all on one.
    return _STRESS_STATUSES[(writer + write) % len(_STRESS_STATUSES)]


@requires_brd
def test_brd_update_survives_concurrent_writers(temp_board):
    # Main spec §17 "brd concurrency" / parallel-stories P3: this deliberately
    # bypasses board.py and WRITE_LOCK, calling `brd update` straight from 8
    # threads, to find out whether brd itself tolerates concurrent writers.
    # Never weaken this if it flakes -- a failure is the evidence that
    # board._run needs its bounded lock-error retry.
    cards = [
        _add_card(temp_board, f"stress card {writer}")
        for writer in range(_STRESS_WRITERS)
    ]
    failures: list[tuple[str, int, int, str, str]] = []
    barrier = threading.Barrier(_STRESS_WRITERS)

    def hammer(writer: int, card_id: str) -> None:
        barrier.wait()
        for write in range(_STRESS_WRITES_EACH):
            completed = subprocess.run(
                ["brd", "update", card_id, "--status", _stress_status(writer, write)],
                cwd=temp_board,
                capture_output=True,
                text=True,
            )
            try:
                ok = json.loads(completed.stdout).get("ok") is True
            except (json.JSONDecodeError, AttributeError):
                ok = False
            if completed.returncode != 0 or not ok:
                failures.append(
                    (card_id, write, completed.returncode, completed.stdout, completed.stderr)
                )

    threads = [
        threading.Thread(target=hammer, args=(writer, card_id))
        for writer, card_id in enumerate(cards)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)
    assert not any(thread.is_alive() for thread in threads)

    assert failures == []
    for writer, card_id in enumerate(cards):
        expected = _stress_status(writer, _STRESS_WRITES_EACH - 1)
        assert _brd_json(temp_board, "show", card_id)["status"] == expected
```

- [ ] **Step 2: Run it, several times, and record what brd did**

Run: `for i in 1 2 3 4 5; do uv run pytest tests/test_board.py::test_brd_update_survives_concurrent_writers -v; done`
Expected (per brd's source): PASS all five times. Record for the final report: pass/fail count across the five runs and, for any failure, the exact `failures` tuples pytest prints (returncode, stdout, stderr).

Decision:
- All five PASS: brd tolerates 8 concurrent writers. Skip Task 3 entirely; `board._run` stays unchanged. Commit (Step 3) and continue with Task 4.
- Any FAIL whose stderr contains `database is locked` with empty stdout and returncode 1 (the shape brd's source produces: `sqlite3.OperationalError` is not a `BrdError`, so `cli/_app.py:74-96` does not envelope it and it escapes as a traceback on stderr): commit this test anyway (it stays red until Task 3), then do Task 3.
- Any FAIL of a different shape (an `ok:false` envelope, a different message, a wrong final status with every call ok): stop and escalate with the captured output. Do not improvise a retry predicate for an unobserved shape, and do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_board.py
git commit -m "Stress brd update from 8 threads, bypassing the board lock"
```

---

### Task 3 (conditional): bounded lock-error retry in `board._run`

Do this task ONLY if Task 2 Step 2 recorded a failure with returncode 1, empty stdout, and `database is locked` in stderr. Otherwise skip it and note "retry not needed" for the final report.

**Files:**
- Modify: `src/agent_manager/board.py` (imports; new constants and `_is_lock_error` after `set_status_argv`; `_run` at current lines ~95-120 after Task 1; module docstring)
- Test: `tests/test_board.py` (bottom of the file, after `test_roots_argv_is_brd_tree_with_no_id`)

**Interfaces:**
- Consumes: `board._run(argv: list[str], repo_dir: Path | None) -> subprocess.CompletedProcess[str]` (signature unchanged).
- Produces: `board._LOCK_RETRY_ATTEMPTS: int = 4`, `board._LOCK_RETRY_BACKOFF_SECONDS: float = 0.05`, `board._LOCK_ERROR_MARKER: str = "database is locked"`, `board._is_lock_error(completed: subprocess.CompletedProcess[str]) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to the bottom of `tests/test_board.py`:

```python
def _counting_fake_brd(tmp_path: Path, *, fail_times: int, failure: str) -> Path:
    """A fake brd that fails `fail_times` times with `failure`, then succeeds.

    Each invocation appends to an `attempts` file beside the script, so a test
    can count how many times `_run` actually ran it. `failure` is a shell
    snippet that prints the failure and exits non-zero.
    """
    fake_brd = tmp_path / "brd"
    attempts = tmp_path / "attempts"
    fake_brd.write_text(
        "#!/bin/sh\n"
        f"echo x >> '{attempts}'\n"
        f"n=$(wc -l < '{attempts}')\n"
        f"if [ \"$n\" -le {fail_times} ]; then\n"
        f"{failure}\n"
        "fi\n"
        "echo '{\"ok\": true, \"data\": {}}'\n"
    )
    fake_brd.chmod(0o755)
    return fake_brd


_LOCK_FAILURE = (
    "echo 'sqlite3.OperationalError: database is locked' >&2\n"
    "exit 1"
)


def _attempts(tmp_path: Path) -> int:
    return len((tmp_path / "attempts").read_text().splitlines())


def test_run_retries_brd_lock_error_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "_LOCK_RETRY_BACKOFF_SECONDS", 0)
    fake_brd = _counting_fake_brd(
        tmp_path, fail_times=board._LOCK_RETRY_ATTEMPTS - 1, failure=_LOCK_FAILURE
    )

    completed = board._run([str(fake_brd), "update", "x", "--status", "done"], None)

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"ok": True, "data": {}}
    assert _attempts(tmp_path) == board._LOCK_RETRY_ATTEMPTS


def test_run_gives_up_after_bounded_lock_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "_LOCK_RETRY_BACKOFF_SECONDS", 0)
    fake_brd = _counting_fake_brd(tmp_path, fail_times=1000, failure=_LOCK_FAILURE)
    argv = [str(fake_brd), "update", "x", "--status", "done"]

    with pytest.raises(board.BoardError) as excinfo:
        board._run(argv, None)

    # Surfaces exactly as it would have without the retry.
    assert "database is locked" in excinfo.value.message
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == argv
    assert _attempts(tmp_path) == board._LOCK_RETRY_ATTEMPTS


@pytest.mark.parametrize(
    "failure",
    [
        "echo 'Usage: brd [OPTIONS]' >&2\nexit 2",
        "echo '{\"ok\": false, \"error\": {\"type\": \"CardNotFoundError\", "
        "\"message\": \"no card with id x\"}}'\nexit 1",
    ],
    ids=["stderr-usage-error", "ok-false-envelope"],
)
def test_run_does_not_retry_non_lock_failure(failure, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "_LOCK_RETRY_BACKOFF_SECONDS", 0)
    fake_brd = _counting_fake_brd(tmp_path, fail_times=1000, failure=failure)
    argv = [str(fake_brd), "update", "x", "--status", "done"]

    try:
        completed = board._run(argv, None)
    except board.BoardError:
        pass  # the stderr-only shape raises from _run, as today
    else:
        # the envelope shape comes back for _decode to raise, as today
        assert completed.returncode == 1
        with pytest.raises(board.BoardError):
            board._decode(completed.stdout, argv=argv, exit_code=completed.returncode)

    assert _attempts(tmp_path) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "retries_brd_lock_error or gives_up_after or does_not_retry_non_lock" -v`
Expected: `test_run_retries_brd_lock_error_then_succeeds` and `test_run_gives_up_after_bounded_lock_retries` FAIL with `AttributeError: ... has no attribute '_LOCK_RETRY_BACKOFF_SECONDS'` (monkeypatch.setattr raises on a missing attribute); both `test_run_does_not_retry_non_lock_failure` cases FAIL the same way.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/board.py`, add `import time` to the imports (after `import threading`):

```python
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import TypeVar
```

Insert directly after `set_status_argv` (before `def _run`):

```python
_LOCK_RETRY_ATTEMPTS = 4
"""Total brd invocations `_run` makes when brd keeps reporting its lock error."""

_LOCK_RETRY_BACKOFF_SECONDS = 0.05
"""Sleep before the first retry; doubled before each later one (0.05, 0.1, 0.2)."""

_LOCK_ERROR_MARKER = "database is locked"
"""What brd prints when SQLite's busy timeout runs out.

`sqlite3.OperationalError` is not a `BrdError`, so brd does not wrap it in an
`ok: false` envelope: it escapes as a traceback on stderr, stdout empty, exit 1.
"""


def _is_lock_error(completed: "subprocess.CompletedProcess[str]") -> bool:
    """True only for brd's stderr-only SQLite lock failure; nothing else retries."""
    return (
        completed.returncode != 0
        and not completed.stdout.strip()
        and _LOCK_ERROR_MARKER in completed.stderr
    )
```

Replace the body of `_run` (the `try: ... return completed` block) with:

```python
    for attempt in range(1, _LOCK_RETRY_ATTEMPTS + 1):
        try:
            completed = subprocess.run(
                argv,
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise BoardError(
                f"could not run {argv[0]}: {exc.strerror}", argv=argv
            ) from exc
        if attempt < _LOCK_RETRY_ATTEMPTS and _is_lock_error(completed):
            time.sleep(_LOCK_RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1))
            continue
        break

    if completed.returncode != 0 and not completed.stdout.strip():
        detail = completed.stderr.strip() or "no output"
        raise BoardError(detail, argv=argv, exit_code=completed.returncode)

    return completed
```

and extend the `_run` docstring with:

```python

    brd's SQLite lock error (`_is_lock_error`) is retried up to
    `_LOCK_RETRY_ATTEMPTS` invocations in total with a doubling backoff; any
    other failure is returned or raised on its first occurrence, and an
    exhausted retry surfaces exactly like an unretried failure.
```

Add one sentence to the module docstring's write-lock paragraph (added in Task 1), so it ends:

```python
Writes are serialized within one process: `set_status` runs under the
module-level `WRITE_LOCK`, a reentrant lock that `steps/rollup.py` also holds
around its whole read-modify-write walk up a card's ancestors. Reads take no
lock. Nothing here coordinates two separate `am` processes on one repository;
that is not supported. `_run` retries brd's SQLite "database is locked" failure
a bounded number of times, and nothing else.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: PASS, including the three retry checks (four cases), `test_brd_update_survives_concurrent_writers`, `test_missing_brd_on_path_raises_board_error` (still one `FileNotFoundError`, no retry), and `test_non_zero_exit_with_no_stdout_reports_stderr_and_the_exit_code`.

Then rerun the stress test five times: `for i in 1 2 3 4 5; do uv run pytest tests/test_board.py::test_brd_update_survives_concurrent_writers -v; done`
Expected: FAIL still (the stress test deliberately calls raw `brd`, not `board._run`, so the retry does not mask brd's behaviour). If so, record in the final report that brd fails under raw concurrency and that `board._run` now absorbs it. Then mark the stress test `@pytest.mark.xfail(strict=False, reason="brd itself fails under concurrent writers; board._run retries -- see card daf14164 report")`, leaving its body and assertions unchanged, and commit that together with this task. This is a spec gap, not a free choice: the spec requires both "never weaken the stress test" and "full suite green", and a raw-brd test cannot be made green by a retry in `board._run`. The non-strict xfail keeps every assertion and still reports the failure as evidence. Name this gap explicitly in the final report so a human can decide whether to keep it.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "Retry brd's SQLite lock error a bounded number of times in board._run"
```

---

### Task 4: Hold `board.WRITE_LOCK` around the whole rollup walk

**Files:**
- Modify: `src/agent_manager/steps/rollup.py:1-25` (module docstring), `src/agent_manager/steps/rollup.py:63-123` (`set_status`)
- Test: `tests/steps/test_rollup.py` (imports at lines 15-23; new tests inserted after `test_the_walk_is_capped_at_sixteen_ancestors`, which ends at line 270, before the `# --- Pure-function tier` comment)

**Interfaces:**
- Consumes: `board.WRITE_LOCK` (Task 1), `board.show`, `board.tree`, `board.set_status`.
- Produces: `rollup.set_status(card: str, status: str, repo_dir: str | Path | None = None) -> dict[str, object]` unchanged in signature and return shape, now one critical section on `board.WRITE_LOCK`.

- [ ] **Step 1: Write the failing tests**

Add `import threading` to the imports of `tests/steps/test_rollup.py`:

```python
import json
import shutil
import subprocess
import threading
from pathlib import Path
```

Append one sentence to the module docstring of `tests/steps/test_rollup.py` (after "`_add_card` and `_brd_json`."):

```python
Two lock-scope tests wrap `board.show` and `board.tree` in pass-through spies
that only record whether `board.WRITE_LOCK` is held; the real functions still
run against the real board.
```

Insert after `test_the_walk_is_capped_at_sixteen_ancestors`:

```python
def _write_lock_held_by_another_thread() -> bool:
    """Whether some other thread holds board.WRITE_LOCK right now.

    Probed from a fresh thread with a non-blocking acquire, so it uses only the
    lock's public API and never blocks.
    """
    acquired: list[bool] = []

    def probe() -> None:
        got = board.WRITE_LOCK.acquire(blocking=False)
        if got:
            board.WRITE_LOCK.release()
        acquired.append(got)

    prober = threading.Thread(target=probe)
    prober.start()
    prober.join()
    return not acquired[0]


@requires_brd
def test_the_whole_rollup_walk_runs_under_the_board_lock(temp_board, monkeypatch):
    # A rollup is read-modify-write: the ancestor reads must be inside the same
    # critical section as the writes, or two sibling walks can interleave.
    milestone = _add_card(temp_board, "Milestone 4")
    story = _add_card(temp_board, "Serialize the shared resources", milestone)
    subtask = _add_card(temp_board, "Serialize board writes", story)
    held_during: list[tuple[str, bool]] = []
    real_show, real_tree = board.show, board.tree

    def show_spy(card_id, **kwargs):
        held_during.append(("show", _write_lock_held_by_another_thread()))
        return real_show(card_id, **kwargs)

    def tree_spy(card_id, **kwargs):
        held_during.append(("tree", _write_lock_held_by_another_thread()))
        return real_tree(card_id, **kwargs)

    monkeypatch.setattr(board, "show", show_spy)
    monkeypatch.setattr(board, "tree", tree_spy)

    result = rollup.set_status(subtask, "done", repo_dir=temp_board)

    assert result["card"] == subtask
    # show(subtask), tree(story), show(story), tree(milestone), show(milestone)
    assert [name for name, _ in held_during] == ["show", "tree", "show", "tree", "show"]
    assert all(held for _, held in held_during)
    assert not _write_lock_held_by_another_thread()


@requires_brd
def test_a_failed_rollup_releases_the_board_lock(temp_board):
    with pytest.raises(board.BoardError):
        rollup.set_status("deadbeef", "done", repo_dir=temp_board)
    assert not _write_lock_held_by_another_thread()

    # The depth guard raises from inside the walk; the lock must still be free.
    chain = [_add_card(temp_board, "Level 0")]
    for level in range(1, rollup.MAX_ANCESTRY_DEPTH + 2):
        chain.append(_add_card(temp_board, f"Level {level}", chain[-1]))
    with pytest.raises(board.BoardError, match="exceeded maximum ancestry depth"):
        rollup.set_status(chain[-1], "done", repo_dir=temp_board)
    assert not _write_lock_held_by_another_thread()


@requires_brd
def test_rollup_reenters_a_board_lock_its_own_thread_already_holds(temp_board):
    story = _add_card(temp_board, "Serialize the shared resources")
    subtask = _add_card(temp_board, "Serialize board writes", story)
    outcome: dict[str, object] = {}

    def nested() -> None:
        with board.WRITE_LOCK:
            outcome["result"] = rollup.set_status(
                subtask, "done", repo_dir=temp_board
            )

    # On a worker with a timeout, so a non-reentrant lock fails the test
    # instead of hanging the suite.
    worker = threading.Thread(target=nested, daemon=True)
    worker.start()
    worker.join(timeout=60)
    assert not worker.is_alive()
    assert outcome["result"] == {
        "card": subtask,
        "status": "done",
        "rolled_up": [{"card": story, "status": "done"}],
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_rollup.py -k "under_the_board_lock or releases_the_board_lock or reenters" -v`
Expected: `test_the_whole_rollup_walk_runs_under_the_board_lock` FAILS on `assert all(held for _, held in held_during)` (with only Task 1 in place, the lock is held during `board.set_status` but not during the walk's `show`/`tree` calls). The other two PASS already (Task 1's `with` block releases on error, and the RLock re-enters); they are regression guards for the change in Step 3.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/rollup.py`, replace the "Nothing is cached" paragraph of the module docstring (lines 17-18) with:

```python
Nothing is cached: every parent is read fresh, because sibling work can change
a shared ancestor between one level and the next.

The whole call -- the card's write and every level of the walk -- runs under
`board.WRITE_LOCK`, one critical section, because a rollup reads, then
modifies, then writes. Concurrent calls on sibling subtasks in one process
therefore run one after another, and the last one sees every sibling's final
status. The lock is reentrant, so the nested `board.set_status` calls re-take
it on the same thread.
```

Replace the body of `set_status` (lines 101-123) with:

```python
    path = Path(repo_dir) if repo_dir is not None else None
    with board.WRITE_LOCK:
        written = board.set_status(card, status, repo_dir=path)

        rolled_up: list[dict[str, str]] = []
        current = written.id
        depth = 0
        while True:
            parent_id = board.show(current, repo_dir=path).parent_id
            if not parent_id:
                break
            depth += 1
            if depth > MAX_ANCESTRY_DEPTH:
                raise board.BoardError(
                    "exceeded maximum ancestry depth",
                    argv=board.show_argv(current),
                )
            node = board.tree(parent_id, repo_dir=path)
            target = rollup_status(child.status for child in node.children)
            if target is not None and stored_status(node.status) != target:
                parent = board.set_status(parent_id, target, repo_dir=path)
                rolled_up.append({"card": parent.id, "status": parent.status})
            current = parent_id

        return {
            "card": written.id,
            "status": written.status,
            "rolled_up": rolled_up,
        }
```

Add one paragraph to the `set_status` docstring, directly after the paragraph ending "More than `MAX_ANCESTRY_DEPTH` ancestors raises `board.BoardError`.":

```python

    All of it -- the card's write and the whole walk -- holds
    `board.WRITE_LOCK`, so a concurrent call on a sibling cannot interleave
    its reads and writes with this one. The lock is released by a `with`
    block, so a `board.BoardError` from anywhere inside, the depth guard
    included, never leaves it held.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_rollup.py -v`
Expected: PASS, the three new tests and every existing one (lines 72-310 of the original file, including `test_the_walk_is_capped_at_sixteen_ancestors` and the pure-function tier).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/rollup.py tests/steps/test_rollup.py
git commit -m "Hold the board write lock around the whole rollup walk"
```

---

### Task 5: Concurrent rollup race test

A regression test for the outcome the lock guarantees. It is probabilistic: without Task 4 it would usually, not always, lose a parent update. The deterministic proof that the walk is locked is Task 4's `test_the_whole_rollup_walk_runs_under_the_board_lock`; this test proves the end result under real contention.

Runtime note: each iteration serializes about 8 rollups of up to ~8 brd calls each, plus 11 `brd add`s; expect roughly 5-15 s per iteration, so up to a few minutes for 20. That is the spec's required count; do not reduce it.

**Files:**
- Test: `tests/steps/test_rollup.py` (insert after `test_rollup_reenters_a_board_lock_its_own_thread_already_holds`, before the `# --- Pure-function tier` comment)

**Interfaces:**
- Consumes: `rollup.set_status` (Task 4), `temp_board`, `_add_card`, `_brd_json`, `requires_brd`, `threading` import (Task 4 Step 1).
- Produces: nothing new.

- [ ] **Step 1: Write the test**

```python
_RACE_ITERATIONS = 20
_RACE_STORIES = 2
_RACE_SUBTASKS_PER_STORY = 4


@requires_brd
def test_concurrent_rollups_reach_done(temp_board):
    # Parallel-stories P3: 8 sibling-and-cousin subtasks finishing at once must
    # not lose a story or milestone update. Fresh cards every iteration.
    for iteration in range(_RACE_ITERATIONS):
        milestone = _add_card(temp_board, f"Milestone {iteration}")
        stories = [
            _add_card(temp_board, f"Story {iteration}.{s}", milestone)
            for s in range(_RACE_STORIES)
        ]
        subtasks = [
            _add_card(temp_board, f"Subtask {iteration}.{s}.{t}", story)
            for s, story in enumerate(stories)
            for t in range(_RACE_SUBTASKS_PER_STORY)
        ]
        barrier = threading.Barrier(len(subtasks))
        results: dict[str, dict[str, object]] = {}
        errors: list[tuple[str, BaseException]] = []

        def mark_done(card: str) -> None:
            barrier.wait()
            try:
                results[card] = rollup.set_status(card, "done", repo_dir=temp_board)
            except BaseException as exc:
                errors.append((card, exc))

        threads = [
            threading.Thread(target=mark_done, args=(card,), daemon=True)
            for card in subtasks
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)

        assert not any(thread.is_alive() for thread in threads), iteration
        assert errors == [], iteration
        assert sorted(results) == sorted(subtasks), iteration
        assert all(result["status"] == "done" for result in results.values())
        for story in stories:
            assert _brd_json(temp_board, "show", story)["status"] == "done", (
                iteration,
                story,
            )
        assert _brd_json(temp_board, "show", milestone)["status"] == "done", iteration
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest tests/steps/test_rollup.py::test_concurrent_rollups_reach_done -v`
Expected: PASS. If it fails, the lock scope in Task 4 is wrong (or brd itself failed, visible in `errors`); debug with superpowers:systematic-debugging, do not loosen the assertions.

- [ ] **Step 3: Commit**

```bash
git add tests/steps/test_rollup.py
git commit -m "Race 8 concurrent rollups across two stories, 20 times over"
```

---

### Task 6: Full verification and final report

**Files:** none modified.

- [ ] **Step 1: Confirm untouched files**

Run: `git diff --stat m4/task-serialize-git-worktree-88150552 -- src/agent_manager/steps/worktree.py tests/steps/test_worktree.py tests/e2e`
Expected: no output (none of these changed).

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest`
Expected: PASS, whole default suite, including `tests/e2e` collection (the paid e2e test stays deselected by `-m "not e2e"`) and every test that drives the `--max-concurrent 1` sequential path. Zero failures. If Task 3 ran and marked the stress test xfail, it reports as `xfailed`/`xpassed`, not as a failure.

- [ ] **Step 3: Write the final report (in the task's final message, not a file)**

It must state:
- What `brd update` did under the 8x25 concurrent stress test across the five Task 2 runs: whether any call failed, and if so the exact failure output (returncode, stdout, stderr) captured in Task 2 Step 2.
- Whether the bounded retry in `board._run` was added. If yes: `_LOCK_RETRY_ATTEMPTS = 4` total invocations, backoff 0.05 s doubled before each retry (0.05, 0.1, 0.2 s), recognising only a non-zero exit with empty stdout and `database is locked` in stderr. If no: "`board._run` unchanged; brd's SQLite busy timeout (10 s, WAL) absorbed 8 concurrent writers".
- That board writes and whole rollup walks are now serialized on `board.WRITE_LOCK` (RLock), reads are unlocked, and two `am` processes on one repository remain unsupported.
