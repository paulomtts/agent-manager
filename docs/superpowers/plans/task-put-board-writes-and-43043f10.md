<!-- task-pipeline: validated -->
# Put board writes and git worktree operations under the process-wide locks (card 43043f10)

Subtask of story 00220793 ("Process-wide locks: board writes and git worktree operations"), milestone 619c2a8e. Narrows plan Task 1.2 of `docs/superpowers/plans/2026-09-27-multi-process.md` and spec X7/X8 and §7 "Testing" (Wiring) of `docs/superpowers/specs/2026-09-27-multi-process-design.md`. Both documents exist only on the `docs/multi-process` branch (and in the `.claude/worktrees/docs-multi-process/` worktree), not on `master` and not on this card's branch; this card's branch need not merge them in, since every decision from them that this card needs is already reproduced below. Branch prefix `m10`.

## Prerequisite

This card consumes `locks.py` (`ProcessLock`, `project_lock`, `LockTimeoutError`, `LockOrderError`, `LOCK_TIMEOUT_SECONDS`) and `paths.project_lock_path` from sibling c5e616c2 ("Add ProcessLock and the project lock files"). That work exists only on branch `m10/task-add-processlock-and-the-c5e616c2`, not on `master`. This card's branch must include that commit (merge or rebase from it) before any change here compiles. Do not redefine or re-implement any locking primitive; if something in `locks.py` looks wrong, report it rather than fix it here. `tests/test_locks.py` and `tests/test_paths.py` belong to the sibling, except for the helper extraction below.

Line numbers below were read from `master` plus the sibling branch; re-locate before editing and state any drift.

## Scope

1. `board.py`
   - Add `write_lock(repo_dir: Path | None) -> locks.ProcessLock`, returning `locks.project_lock(<repo_dir resolved, or Path.cwd() resolved when None>, "board", local=WRITE_LOCK)`.
   - `WRITE_LOCK` (line ~39) stays the same module-level `RLock` object, which is now the in-process layer of the board lock.
   - `set_status` (lines ~240-262) runs under `write_lock(repo_dir)` instead of `with WRITE_LOCK:`. The body is otherwise unchanged.
   - In the module docstring, remove the "Nothing here coordinates two separate `am` processes..." text (lines ~12-13) and cite spec X7 in its place.
2. `steps/rollup.py`: `set_status` (line ~70) runs its whole ancestor walk under `board.write_lock(path)` instead of `with board.WRITE_LOCK:` (line ~115). The nested `board.set_status` calls re-enter the same `ProcessLock`, which is reentrant and takes one flock.
3. `steps/worktree.py`
   - `_repo_lock` (lines ~176-188), and the `_REPO_LOCKS` registry it fills (line ~169), hold `threading.RLock` instead of a plain `Lock`, because the `ProcessLock` layer needs reentrancy. `ensure` still never re-enters it.
   - Add `git_lock(repo_path: str | Path) -> locks.ProcessLock`, returning `locks.project_lock(Path(repo_path).resolve(), "git", local=_repo_lock(repo_path))`.
   - `ensure`'s re-check-then-`worktree add` critical section (lines ~230-249) uses `git_lock(repo_path)` instead of `_repo_lock(repo_path)`.
   - Two separate docstrings say two `am` processes on one repository are not supported; both go stale once `git_lock` lands and both are edited to cite spec X7 in their place: the module docstring's "Threads in one process only" sentence (line ~19), and `_repo_lock`'s own docstring, which ends "In-process threads only: two `am` processes on one repository are not supported." (lines ~180-181).
4. `orchestrate.refresh_git` (lines ~530-541) runs its `remote`, `fetch origin` and `worktree prune` git calls under `worktree.git_lock(root)`.
5. `cli.HANDLED` (lines ~1077-1082) adds `locks.LockTimeoutError` to `(CliError, board.BoardError, EngineError, ValueError)`.
6. Create `tests/lockhelpers.py`. Move the `HOLDER`/`PROBE` child-process source strings and the `_reap`, `_holder`, `_release` and `_probe` functions (lines ~24-100 of `tests/test_locks.py` on the sibling branch) into it unchanged in behaviour and under the same names (`_reap` is a private dependency of `_holder`/`_release` and must move with them; `_free_to_another_thread`, further down the file, stays in `tests/test_locks.py` — it is only used by that file's own in-process reentrancy test). Import the four moved functions from `tests/test_locks.py`, `tests/steps/test_rollup.py`, `tests/steps/test_worktree.py` and `tests/test_orchestrate.py`.

Out of scope: claims, leases, the store, `store.immediate`, `LeaseLostError`/`ClaimedError`, e2e tests (`tests/e2e/test_multi_process.py`), and any change to `locks.py` or `paths.py`.

## Observable behaviour

- Two `am` processes on the same project serialise board writes, whether a single `set_status` or a full rollup walk. Within a process, threads still serialise on `WRITE_LOCK`.
- Two processes serialise `worktree add` (with its re-check) and `refresh_git`'s `remote`/`fetch`/`prune` on one lock per resolved repository path. `repo/`, `repo` and `repo/sub/..` all map to the same lock.
- A rollup's nested `board.set_status` calls do not deadlock: one flock is taken per outermost acquisition.
- No lock file is created inside a repository or worktree. Lock files come only from `paths.project_lock_path` under the data directory.
- Readers (`am status`, `am runs`, `am logs`, `am run --dry-run`) take no `ProcessLock` and still work.

## Invariants and error paths

- The `board` and `git` locks are never nested in either order. No new code path may take `git_lock` inside `write_lock` or the reverse. `ProcessLock` would raise `LockOrderError`.
- No store transaction is opened while a `ProcessLock` is held.
- `LockTimeoutError` is never caught in `board.py`, `rollup.py`, `worktree.py` or `orchestrate.py`. Inside a phase it fails the deterministic step the same way `GitError`/`BoardError` do, and the lane escalates. Before a run starts it propagates to `cli.HANDLED` and becomes an `{"ok": false, ...}` envelope at exit 3.
- The existing `WRITE_LOCK`-based tests in `tests/test_board.py` stay green unchanged.

## Tests

Test-placement rule (design spec 2026-09-23 §14): steps are tested against temporary git repos and a temporary `brd` board with no network. The milestone spec §7 groups these tests as "Wiring", placed in the existing step/module test files and using real child processes. Ordering is proven only with marker files, pipes (the child prints "held" and waits on stdin) and thread `Event`s, never with sleeps. All tests below are in the default suite; none is e2e.

- **`tests/steps/test_rollup.py`** (Wiring / step tier)
  - New `fake_brd` fixture: a `brd` script on `PATH` that answers from a canned board and logs each call with a flag saying whether the release marker file was present. This is separate from, and does not replace, the file's existing `temp_board` fixture and its real, unmocked `brd` subprocess tests; only the new lock-ordering tests below use `fake_brd`. The module docstring's "brd is not mocked, and neither is any `board` function" sentence is updated to say this fake is the one exception, scoped to proving lock order, and why a real `brd` cannot prove it (there is no way to observe from outside that a real `brd update` call has not yet started).
  - With a child holding `project_lock(repo, "board")`, `rollup.set_status` makes no `brd` call until the child is released. Every logged call has the marker flag set.
  - A rollup whose walk changes several ancestors completes under the flock (nested `board.set_status` does not deadlock).
- **`tests/steps/test_worktree.py`** (Wiring / step tier, real git in a temp repo)
  - With a child holding the `git` lock, `worktree.ensure`'s `worktree add` runs only after the release marker exists.
  - `git_lock` is keyed by the resolved repository: `git_lock(repo)`, `git_lock(f"{repo}/")` and `git_lock(repo / "x" / "..")` return the same `ProcessLock`.
- **`tests/test_orchestrate.py`** (Wiring tier): with a monkeypatched `run_git` that records a `_probe` result when it sees `prune`, `refresh_git`'s prune runs while the `git` lock is held (the probe fails to acquire).
- **`tests/test_locks.py`** (sibling's Locks unit tier): no new tests. It only switches to importing its helpers from `tests/lockhelpers.py` and must stay green.
- **`tests/test_board.py`**: unchanged, must stay green.

## Note on inputs

The exploration findings given to this stage were truncated at 8000 characters, partway through the test-placement paragraph. That suggests the upstream stage went past its brief. The test placement above comes from the milestone spec §7 "Wiring" list, which I read directly (it names `tests/test_board.py`, `tests/steps/test_rollup.py`, `tests/steps/test_worktree.py` and `tests/test_orchestrate.py`), not from the missing text.

---

# Board Writes and Git Worktree Operations Under Process-Wide Locks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `board.set_status`, the whole `rollup.set_status` walk, `worktree.ensure`'s `worktree add` and `orchestrate.refresh_git` take the sibling's process-wide `ProcessLock`s (`board` and `git`), and make a `LockTimeoutError` a handled CLI refusal.

**Architecture:** Two thin accessor functions, `board.write_lock(repo_dir)` and `worktree.git_lock(repo_path)`, return the registry's one `ProcessLock` per resolved project path, with the existing in-process locks (`board.WRITE_LOCK`, `worktree._repo_lock(...)`, now an `RLock`) as their in-process layer. Every critical section that used the in-process lock switches to the accessor; nothing catches `LockTimeoutError` except `cli.HANDLED`. Tests prove ordering with a child holder process, a marker file and a `threading.Event` fired from `locks._backoff`, never with sleeps.

**Tech Stack:** Python 3.12 (`.python-version`), `fcntl.flock` via `agent_manager.locks`, pytest 9 in `--import-mode=importlib`, real `git`, a fake `brd` script on `PATH`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-put-board-writes-and-43043f10/docs/superpowers/specs/task-put-board-writes-and-43043f10-design.md` (reproduced verbatim above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-put-board-writes-and-43043f10`; run every command from there.

## Drift found while reading the code as built (state these in the PR/commit notes)

- The worktree branch was cut from `m10/task-add-processlock-and-the-c5e616c2`, so `src/agent_manager/locks.py`, `paths.project_lock_path` and `tests/test_locks.py` are already present. No merge is needed; Task 1 Step 1 only confirms it.
- Line numbers match the spec: `board.WRITE_LOCK` at `board.py:39`, `set_status` at `board.py:240-264` (`with WRITE_LOCK:` at 259), `rollup.set_status` at `rollup.py:70` (`with board.WRITE_LOCK:` at 115), `_REPO_LOCKS` at `worktree.py:169`, `_repo_lock` at `worktree.py:176-189`, `ensure`'s locked block at `worktree.py:230-261`, `refresh_git` at `orchestrate.py:530-541`, `HANDLED` at `cli.py:1077-1082`. The helpers in `tests/test_locks.py` sit at lines 26-103.
- **`pyproject.toml` runs pytest with `--import-mode=importlib`, which puts nothing on `sys.path`** (see the comment in `tests/e2e/test_real_harness_parallel.py:25`). A plain `tests/lockhelpers.py` would therefore not be importable from `tests/steps/`. Task 1 adds `pythonpath = ["tests"]` to `[tool.pytest.ini_options]`. That is the one file touched beyond the spec's list, and it changes no test module's name: importlib mode names modules from rootdir-relative paths regardless of `sys.path`.
- **`threading.RLock` has no `.locked()` before Python 3.14, and this project pins 3.12.** Two existing tests in `tests/steps/test_worktree.py` (`test_a_failed_add_releases_the_repository_lock`, line 758, and `test_a_same_branch_at_a_different_path_surfaces_gits_error_and_frees_the_lock`, line 788) call `worktree._repo_lock(str(repo)).locked()`. They would fail with `AttributeError` once `_repo_lock` returns an `RLock`. Task 3 rewrites those two assertions to probe from another thread, which works on both kinds of lock. It also adds a flock probe to each. `tests/test_board.py` is not touched.

## Global Constraints

- Do not redefine or re-implement any locking primitive; `locks.py` and `paths.py` are not edited.
- `board.WRITE_LOCK` stays the same module-level `threading.RLock` object.
- The `board` and `git` locks are never nested in either order.
- No store transaction is opened while a `ProcessLock` is held.
- `LockTimeoutError` is never caught in `board.py`, `rollup.py`, `worktree.py` or `orchestrate.py`; only `cli.HANDLED` turns it into an `{"ok": false, ...}` envelope at exit 3.
- No lock file is created inside a repository or worktree.
- Ordering is proven only with marker files, pipes and thread `Event`s, never with sleeps.
- All new tests are in the default suite; none is e2e. `tests/test_board.py` is unchanged.
- Nothing is pushed; the base branch never moves. Branch: `m10/task-put-board-writes-and-43043f10`.
- Verification: `uv run pytest`.

## Review Focus

1. **A holder that outlives the timeout.** When another process holds the lock past its timeout, `LockTimeoutError` must reach the caller. No `brd` or `git` call may have been made, and the in-process layer must be free afterwards. Tests: Task 2 `test_a_board_lock_timeout_propagates_before_any_brd_call`, Task 3 `test_a_git_lock_timeout_propagates_and_no_worktree_is_added`, Task 4 `test_a_git_lock_timeout_in_refresh_git_propagates_before_any_git_call`, Task 5 envelope test.
2. **`repo_dir=None` for a board write.** `brd` resolves its board from the cwd, so the lock must be keyed by the resolved cwd and be the same object as the explicit spelling. Test: Task 2 `test_the_board_lock_is_one_per_resolved_repository_and_defaults_to_the_cwd`.
3. **Different spellings of one repository** (trailing slash, a `..` hop through a directory that does not exist) must map to one `ProcessLock`, or two lanes would take two flocks and not serialise. Tests: Task 2 (board), Task 3 `test_git_lock_is_keyed_by_the_resolved_repository`.
4. **A failure inside the critical section** (a `BoardError` from `brd`, a `GitError` from `worktree add`) must release both the flock and the in-process layer. Tests: Task 2 `test_a_failed_rollup_releases_the_board_flock`, Task 3 (the two rewritten failed-add tests now also probe the flock).
5. **Lock files appearing in the repository.** Tests: Task 2 `test_a_nested_rollup_walk_runs_every_brd_call_under_one_flock` asserts the project dir stays empty. Task 3 `test_worktree_add_waits_for_the_git_lock_of_another_process` asserts no `*.lock` file exists under the repository.

---

### Task 1: Move the holder/probe helpers to `tests/lockhelpers.py`

**Files:**
- Create: `tests/lockhelpers.py`
- Modify: `tests/test_locks.py:1-103`
- Modify: `pyproject.toml:30-41`

**Interfaces:**
- Consumes: `locks.project_lock`, `paths.project_lock_path` (sibling c5e616c2).
- Produces (module `lockhelpers`, importable as `from lockhelpers import ...` from any test file): `HOLDER: str`, `PROBE: str`, `_reap(child: subprocess.Popen[str]) -> None`, `_holder(root: Path, name: str) -> subprocess.Popen[str]` (returns once the child printed `held`), `_release(child: subprocess.Popen[str]) -> None`, `_probe(root: Path, name: str) -> str` (`"busy"` or `"free"`).

This is a pure move, with no behaviour change, so there is no RED step. The existing `tests/test_locks.py` suite is the regression check.

- [ ] **Step 1: Confirm the sibling's work is on this branch**

Run: `git log --oneline -3 && ls src/agent_manager/locks.py tests/test_locks.py`
Expected: both files listed. If `locks.py` is missing, stop and report: the branch must include `m10/task-add-processlock-and-the-c5e616c2`.

- [ ] **Step 2: Create `tests/lockhelpers.py`**

```python
"""Cross-process holder and probe helpers for the lock tests (spec X7, X8).

Moved unchanged from `tests/test_locks.py` so the Wiring tests in
`tests/steps/test_rollup.py`, `tests/steps/test_worktree.py` and
`tests/test_orchestrate.py` can use the same child processes. Importable as
`lockhelpers` because `pyproject.toml` puts `tests/` on `pythonpath`
(`--import-mode=importlib` adds nothing to `sys.path` itself).

Children inherit the test's `XDG_DATA_HOME` (tests/conftest.py), so they flock
the same files as the test process. Order comes from pipes: `_holder` returns
only after the child printed "held", and `_release` only after it printed
"released".
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from agent_manager import locks
    lock = locks.project_lock(Path(sys.argv[1]), sys.argv[2])
    lock.acquire()
    print("held", flush=True)
    sys.stdin.readline()          # released by the parent
    lock.release()
    print("released", flush=True)
    """
)

PROBE = textwrap.dedent(
    """
    import fcntl, os, sys
    from pathlib import Path
    from agent_manager import paths
    path = paths.project_lock_path(Path(sys.argv[1]), sys.argv[2])
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("busy", flush=True)
    else:
        print("free", flush=True)
    finally:
        os.close(fd)
    """
)


def _reap(child: subprocess.Popen[str]) -> None:
    """Kill `child` if it is still running, wait for it and close its pipes."""
    if child.poll() is None:
        child.kill()
    child.wait()
    for stream in (child.stdin, child.stdout):
        if stream is not None and not stream.closed:
            with contextlib.suppress(OSError):
                stream.close()


def _holder(root: Path, name: str) -> subprocess.Popen[str]:
    """A child process holding `project_lock(root, name)` until `_release`."""
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(root), name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    line = child.stdout.readline().strip()
    if line != "held":
        _reap(child)
        pytest.fail(f"holder child did not report 'held' (got {line!r})")
    return child


def _release(child: subprocess.Popen[str]) -> None:
    """Tell a `_holder` child to release, and wait until it has."""
    child.stdin.write("\n")
    child.stdin.flush()
    assert child.stdout.readline().strip() == "released"
    child.stdin.close()
    assert child.wait() == 0
    child.stdout.close()


def _probe(root: Path, name: str) -> str:
    """`"busy"` if another process holds the flock on the lock file, else `"free"`."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(root), name],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
```

- [ ] **Step 3: Put `tests/` on pytest's `pythonpath`**

In `pyproject.toml`, replace:

```toml
addopts = "--import-mode=importlib -m \"not e2e\""
```

with:

```toml
addopts = "--import-mode=importlib -m \"not e2e\""
# importlib mode puts nothing on sys.path, so shared test helpers that are not
# fixtures (tests/lockhelpers.py) are importable only through this entry.
pythonpath = ["tests"]
```

- [ ] **Step 4: Switch `tests/test_locks.py` to the shared helpers**

Replace lines 1-103 of `tests/test_locks.py` (the module docstring through the end of `_probe`) with:

```python
"""Unit tests for `agent_manager.locks` (spec X7, X8; plan Task 1.1).

Cross-process order comes from pipes and exit codes, thread order from
`threading.Event`; no test sleeps to establish ordering. The holder and probe
helpers live in `tests/lockhelpers.py`, shared with the Wiring tests. Children
inherit the test's `XDG_DATA_HOME` (tests/conftest.py), so they flock the same
files.
"""

from __future__ import annotations

import inspect
import os
import threading
import time
from pathlib import Path

import pytest
from lockhelpers import _holder, _probe, _reap, _release

from agent_manager import locks, paths
```

`_free_to_another_thread` (currently starting at line 106) and everything below it stay as they are. `contextlib`, `subprocess`, `sys` and `textwrap` are dropped from the imports because only the moved helpers used them.

- [ ] **Step 5: Run the locks suite**

Run: `uv run pytest tests/test_locks.py -v`
Expected: every test PASSES (same count as before the move).

- [ ] **Step 6: Commit**

```bash
git add tests/lockhelpers.py tests/test_locks.py pyproject.toml
git commit -m "test: move the lock holder/probe helpers to tests/lockhelpers.py"
```

---

### Task 2: Board writes and the rollup walk take the `board` process lock

**Files:**
- Modify: `src/agent_manager/board.py:1-46` (docstring, imports, `WRITE_LOCK` docstring, new `write_lock`), `src/agent_manager/board.py:240-264` (`set_status`)
- Modify: `src/agent_manager/steps/rollup.py:20-25`, `:89-93`, `:115` (docstrings and the lock)
- Test: `tests/steps/test_rollup.py` (Wiring / step tier: docstring, imports, `fake_brd` fixture, new tests)

**Interfaces:**
- Consumes: `lockhelpers._holder/_release/_reap/_probe` (Task 1); `locks.project_lock(root, name, *, local=None) -> ProcessLock`, `locks.LockTimeoutError`, `locks._backoff(attempt: int) -> float`; `paths.project_lock_path(root, name) -> Path`.
- Produces: `board.write_lock(repo_dir: Path | None) -> locks.ProcessLock`. `board.set_status` and `rollup.set_status` hold it.

- [ ] **Step 1: Update the test module docstring and imports**

In `tests/steps/test_rollup.py`, replace lines 1-28 (docstring through `from agent_manager.steps import rollup`) with:

```python
"""Behaviour of the roll-up step (design §4 `steps/`, subtask cards 43008688, bf26f482).

Placement follows design §14: `rollup.py` is a Steps component whose behaviour
is brd reads and writes -- write the card, then walk its ancestors -- so it is
exercised against a real temporary brd board over subprocess. brd is not
mocked, and neither is any `board` function, with one exception: the
`fake_brd` fixture, used only by the process-lock tests (spec X7, card
43043f10). Those tests must show that no `brd` call ran while another process
held the board lock, and a real `brd` gives no way to observe from outside that
an `update` has not yet started. The fake logs every call together with whether
the release marker file existed and whether the board lock's flock was held at
that moment. The pure status computation (`stored_status`, `rollup_status`)
gets plain unit tests at the end of the file.

`tests/steps/` has no `conftest.py` (`test_verify.py` defines its own
`requires_git` marker locally), so the brd helpers are lifted from
`tests/test_board.py`: the `requires_brd` marker, the `temp_board` fixture,
`_add_card` and `_brd_json`.

Two lock-scope tests wrap `board.show` and `board.tree` in pass-through spies
that only record whether `board.WRITE_LOCK` is held; the real functions still
run against the real board.
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from lockhelpers import _holder, _probe, _reap, _release

from agent_manager import board, locks, paths
from agent_manager.steps import rollup
```

- [ ] **Step 2: Write the `fake_brd` fixture and the failing tests**

In `tests/steps/test_rollup.py`, insert the following block immediately before the line `# --- Pure-function tier (design §14): the status computation, no board. ---`:

```python
# --- Process-wide board lock (spec X7, card 43043f10): a fake brd on PATH. ---

FAKE_BRD_SOURCE = textwrap.dedent(
    """
    import fcntl, json, os, sys
    from pathlib import Path

    home = Path(os.environ["FAKE_BRD_HOME"])
    state_file = home / "board.json"
    argv = sys.argv[1:]

    fd = os.open(os.environ["FAKE_BRD_LOCK"], os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        flock = "busy"
    else:
        flock = "free"
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

    with (home / "calls.log").open("a") as log:
        entry = {"argv": argv, "marker": (home / "released").exists(), "flock": flock}
        print(json.dumps(entry), file=log)

    cards = json.loads(state_file.read_text())

    def card(card_id):
        stored = cards[card_id]
        return {
            "id": card_id,
            "title": stored["title"],
            "status": stored["status"],
            "parent_id": stored["parent_id"],
        }

    def node(card_id):
        stored = cards[card_id]
        return {
            "id": card_id,
            "title": stored["title"],
            "status": stored["status"],
            "children": [
                node(child) for child, row in cards.items() if row["parent_id"] == card_id
            ],
        }

    verb, card_id = argv[0], argv[1]
    if card_id not in cards:
        error = {"type": "CardNotFoundError", "message": f"no card {card_id}"}
        print(json.dumps({"ok": False, "error": error}))
        sys.exit(1)
    if verb == "show":
        data = card(card_id)
    elif verb == "tree":
        data = [node(card_id)]
    elif verb == "update":
        cards[card_id]["status"] = argv[3]
        state_file.write_text(json.dumps(cards))
        data = card(card_id)
    else:
        print(json.dumps({"ok": False, "error": {"type": "Usage", "message": verb}}))
        sys.exit(2)
    print(json.dumps({"ok": True, "data": data}))
    """
)

CANNED_BOARD = {
    "m1": {"title": "Milestone 10", "status": "todo", "parent_id": None},
    "st1": {"title": "Process-wide locks", "status": "todo", "parent_id": "m1"},
    "sub1": {"title": "Put board writes under the lock", "status": "todo", "parent_id": "st1"},
    "sub2": {"title": "A sibling subtask", "status": "todo", "parent_id": "st1"},
}
"""sub1 going done rolls st1 and m1 up to in_progress: three writes, one walk."""


@dataclass
class FakeBrd:
    """Handle on the `fake_brd` fixture: the project dir brd runs in, and its log."""

    root: Path
    home: Path

    @property
    def marker(self) -> Path:
        """The release marker; outside `root`, so nothing lands in the project."""
        return self.home / "released"

    def calls(self) -> list[dict[str, object]]:
        """Every logged call, in order: `argv`, `marker` (bool), `flock` ("busy"/"free")."""
        log = self.home / "calls.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def fake_brd(tmp_path, monkeypatch) -> FakeBrd:
    """A `brd` on PATH answering from `CANNED_BOARD` and logging every call.

    Each log line records whether the release marker existed and whether the
    project's board flock was held (probed non-blocking) when that call ran.
    The lock path is computed here, after tests/conftest.py pointed
    XDG_DATA_HOME at this test's own directory, and handed to the script.
    """
    home = tmp_path / "fake-brd"
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    root = tmp_path / "project"
    root.mkdir()
    (home / "board.json").write_text(json.dumps(CANNED_BOARD))
    script = bin_dir / "brd"
    script.write_text(f"#!{sys.executable}\n{FAKE_BRD_SOURCE}")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_BRD_HOME", str(home))
    monkeypatch.setenv("FAKE_BRD_LOCK", str(paths.project_lock_path(root, "board")))
    return FakeBrd(root=root, home=home)


def _signal_first_flock_miss(monkeypatch) -> threading.Event:
    """An Event set the first time any ProcessLock finds its flock taken.

    `locks._flock` asks `_backoff` for a delay only after a failed
    non-blocking attempt, so the Event means "a thread is now waiting on
    another process's flock" -- no timing involved.
    """
    progressed = threading.Event()
    real_backoff = locks._backoff

    def signalling_backoff(attempt: int) -> float:
        progressed.set()
        return real_backoff(attempt)

    monkeypatch.setattr(locks, "_backoff", signalling_backoff)
    return progressed


def _roll_in_thread(
    fake_brd: FakeBrd, card: str, status: str, done: threading.Event | None = None
) -> tuple[threading.Thread, dict[str, object]]:
    """Start `rollup.set_status` on a daemon thread; its outcome lands in the dict.

    `done`, when given, is set as the call returns or raises.
    """
    outcome: dict[str, object] = {}

    def roll() -> None:
        try:
            outcome["result"] = rollup.set_status(card, status, repo_dir=fake_brd.root)
        except BaseException as exc:  # surfaced by the caller's assertions
            outcome["error"] = exc
        finally:
            if done is not None:
                done.set()

    worker = threading.Thread(target=roll, daemon=True)
    worker.start()
    return worker, outcome


def test_a_rollup_waits_for_another_process_holding_the_board_lock(
    fake_brd, monkeypatch
):
    # `progressed` fires on the worker's first failed flock attempt (it is now
    # waiting on the child) or, if nothing locks, when the worker finishes.
    # Only then is the marker written and the child released, so a call logged
    # without the marker can only have run while the child held the lock.
    progressed = _signal_first_flock_miss(monkeypatch)
    child = _holder(fake_brd.root, "board")
    try:
        worker, outcome = _roll_in_thread(fake_brd, "sub1", "done", done=progressed)
        assert progressed.wait(timeout=30)
        fake_brd.marker.touch()
        _release(child)
        worker.join(timeout=60)
        assert not worker.is_alive()
    finally:
        _reap(child)

    assert "error" not in outcome, outcome
    calls = fake_brd.calls()
    assert calls
    assert all(call["marker"] for call in calls), calls


def test_a_nested_rollup_walk_runs_every_brd_call_under_one_flock(fake_brd):
    # Three writes (sub1, st1, m1) re-enter the lock the walk already holds; a
    # second flock on a new descriptor would block its own process forever,
    # so the worker thread's join timeout is the deadlock detector.
    worker, outcome = _roll_in_thread(fake_brd, "sub1", "done")
    worker.join(timeout=60)
    assert not worker.is_alive()

    assert "error" not in outcome, outcome
    assert outcome["result"] == {
        "card": "sub1",
        "status": "done",
        "rolled_up": [
            {"card": "st1", "status": "in_progress"},
            {"card": "m1", "status": "in_progress"},
        ],
    }
    calls = fake_brd.calls()
    assert [call["argv"][0] for call in calls] == [
        "update", "show", "tree", "update", "show", "tree", "update", "show",
    ]
    assert all(call["flock"] == "busy" for call in calls), calls
    assert _probe(fake_brd.root, "board") == "free"
    assert list(fake_brd.root.iterdir()) == []  # no lock file in the project


def test_a_board_lock_timeout_propagates_before_any_brd_call(fake_brd, monkeypatch):
    lock = board.write_lock(fake_brd.root)
    monkeypatch.setattr(lock, "_timeout", 0)
    child = _holder(fake_brd.root, "board")
    try:
        with pytest.raises(locks.LockTimeoutError):
            rollup.set_status("sub1", "done", repo_dir=fake_brd.root)
    finally:
        _reap(child)

    assert fake_brd.calls() == []
    assert not _write_lock_held_by_another_thread()


def test_a_failed_rollup_releases_the_board_flock(fake_brd):
    with pytest.raises(board.BoardError):
        rollup.set_status("no-such-card", "done", repo_dir=fake_brd.root)

    assert _probe(fake_brd.root, "board") == "free"
    assert not _write_lock_held_by_another_thread()


def test_the_board_lock_is_one_per_resolved_repository_and_defaults_to_the_cwd(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()

    lock = board.write_lock(repo)

    assert lock is locks.project_lock(repo, "board")
    assert board.write_lock(repo / "x" / "..") is lock
    assert board.write_lock(Path(f"{repo}{os.sep}")) is lock
    assert lock.path == paths.project_lock_path(repo, "board")
    assert lock._local is board.WRITE_LOCK
    monkeypatch.chdir(repo)
    assert board.write_lock(None) is lock
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/steps/test_rollup.py -v -k "board_lock or nested_rollup or failed_rollup_releases_the_board_flock"`
Expected: FAIL.
- `test_a_rollup_waits_for_another_process_holding_the_board_lock` fails on `all(call["marker"] ...)`, because every call ran with `marker: false`.
- `test_a_nested_rollup_walk_runs_every_brd_call_under_one_flock` fails on `flock == "busy"`, because every call saw `"free"`.
- `test_a_board_lock_timeout_propagates_before_any_brd_call` and `test_the_board_lock_is_one_per_resolved_repository_and_defaults_to_the_cwd` fail with `AttributeError: module 'agent_manager.board' has no attribute 'write_lock'`.
- `test_a_failed_rollup_releases_the_board_flock` PASSES already, because nothing takes the flock yet. It is a regression guard for Step 4 (Review Focus 4), not a RED test.
- The existing tests that `-k "board_lock"` also selects (`test_the_whole_rollup_walk_runs_under_the_board_lock`, `test_a_failed_rollup_releases_the_board_lock`) PASS; they need a real `brd` and skip without one.

- [ ] **Step 4: Implement `board.write_lock` and use it in `board.set_status`**

In `src/agent_manager/board.py`, replace lines 9-13:

```python
Writes are serialized within one process: `set_status` runs under the
module-level `WRITE_LOCK`, a reentrant lock that `steps/rollup.py` also holds
around its whole read-modify-write walk up a card's ancestors. Reads take no
lock. Nothing here coordinates two separate `am` processes on one repository;
that is not supported.
```

with:

```python
Writes are serialized across threads and across `am` processes on one project
(spec X7 of the multi-process design): `set_status` runs under
`write_lock(repo_dir)`, the project's process-wide `board` lock, whose
in-process layer is the module-level `WRITE_LOCK`. `steps/rollup.py` holds the
same lock around its whole read-modify-write walk up a card's ancestors. Reads
take no lock. A `locks.LockTimeoutError` is never caught here.
```

Replace the imports (lines 24-32):

```python
import json
import subprocess
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_manager import models
```

with:

```python
import json
import subprocess
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_manager import locks, models
```

Replace the `WRITE_LOCK` block (lines 39-46):

```python
WRITE_LOCK = threading.RLock()
"""Serializes board writes across threads of one process.

Held for the whole of `set_status`, and by `steps/rollup.py` around its entire
ancestor walk, so a rollup's read-modify-write is one critical section.
Reentrant because the walk calls `set_status` again on the same thread. Reads
(`show`, `tree`, `roots`) do not take it.
"""
```

with:

```python
WRITE_LOCK = threading.RLock()
"""The in-process layer of `write_lock`: serializes board writes across threads.

Held (through `write_lock`) for the whole of `set_status`, and by
`steps/rollup.py` around its entire ancestor walk, so a rollup's
read-modify-write is one critical section. Reentrant because the walk calls
`set_status` again on the same thread. Reads (`show`, `tree`, `roots`) do not
take it.
"""


def write_lock(repo_dir: Path | None) -> locks.ProcessLock:
    """The project's process-wide board write lock (spec X7).

    Keyed by the resolved `repo_dir`, or by the resolved working directory when
    it is `None` -- the directory `brd` resolves its board from. `WRITE_LOCK` is
    its in-process layer, read at call time, so threads of this process still
    serialize on it. Reentrant: a rollup's nested `set_status` takes no second
    flock.
    """
    root = Path(repo_dir).resolve() if repo_dir is not None else Path.cwd().resolve()
    return locks.project_lock(root, "board", local=WRITE_LOCK)
```

In `set_status`, replace:

```python
    Runs entirely under `WRITE_LOCK`, so concurrent writers in one process
    reach brd one at a time.
    """
    argv = set_status_argv(card_id, status)
    with WRITE_LOCK:
```

with:

```python
    Runs entirely under `write_lock(repo_dir)`, so concurrent writers -- threads
    of this process or other `am` processes on the project -- reach brd one at a
    time. A `locks.LockTimeoutError` from the lock propagates uncaught.
    """
    argv = set_status_argv(card_id, status)
    with write_lock(repo_dir):
```

- [ ] **Step 5: Put the rollup walk under `board.write_lock(path)`**

In `src/agent_manager/steps/rollup.py`, replace lines 20-25:

```python
The whole call -- the card's write and every level of the walk -- runs under
`board.WRITE_LOCK`, one critical section, because a rollup reads, then
modifies, then writes. Concurrent calls on sibling subtasks in one process
therefore run one after another, and the last one sees every sibling's final
status. The lock is reentrant, so the nested `board.set_status` calls re-take
it on the same thread.
```

with:

```python
The whole call -- the card's write and every level of the walk -- runs under
`board.write_lock(path)`, the project's process-wide board lock (spec X7), one
critical section, because a rollup reads, then modifies, then writes.
Concurrent calls on sibling subtasks, in this process or another `am` process,
therefore run one after another, and the last one sees every sibling's final
status. The lock is reentrant, so the nested `board.set_status` calls re-take
it on the same thread without a second flock.
```

Replace lines 89-93:

```python
    All of it -- the card's write and the whole walk -- holds
    `board.WRITE_LOCK`, so a concurrent call on a sibling cannot interleave
    its reads and writes with this one. The lock is released by a `with`
    block, so a `board.BoardError` from anywhere inside, the depth guard
    included, never leaves it held.
```

with:

```python
    All of it -- the card's write and the whole walk -- holds
    `board.write_lock(path)`, so a concurrent call on a sibling, from any
    thread or process, cannot interleave its reads and writes with this one.
    The lock is released by a `with` block, so a `board.BoardError` from
    anywhere inside, the depth guard included, never leaves it held. A
    `locks.LockTimeoutError` while waiting for it propagates uncaught, before
    any `brd` call.
```

Replace line 115:

```python
    with board.WRITE_LOCK:
```

with:

```python
    with board.write_lock(path):
```

- [ ] **Step 6: Run the rollup and board suites**

Run: `uv run pytest tests/steps/test_rollup.py tests/test_board.py -v`
Expected: all PASS. That includes the unchanged `test_the_card_write_and_whole_walk_are_one_critical_section`: it monkeypatches `board.WRITE_LOCK`, `write_lock` reads it at call time, and `temp_board` gives the test its own `XDG_DATA_HOME`, so the lock path is registered fresh with the counting lock. It also includes every `WRITE_LOCK` test in `tests/test_board.py`, unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/board.py src/agent_manager/steps/rollup.py tests/steps/test_rollup.py
git commit -m "feat(board): board writes and the rollup walk take the process-wide board lock"
```

---

### Task 3: `worktree add` takes the `git` process lock

**Files:**
- Modify: `src/agent_manager/steps/worktree.py:16-27` (docstring, imports), `:169-189` (`_REPO_LOCKS`, `_repo_lock`, new `git_lock`), `:232-235` (`ensure`)
- Test: `tests/steps/test_worktree.py` (Wiring / step tier: imports, two rewritten assertions, new tests at the end)

**Interfaces:**
- Consumes: `lockhelpers._holder/_release/_reap/_probe` (Task 1); `locks.project_lock`, `locks.LockTimeoutError`, `locks._backoff`; `paths.project_lock_path`.
- Produces: `worktree.git_lock(repo_path: str | Path) -> locks.ProcessLock`; `worktree._repo_lock(repo_path: str | Path) -> threading.RLock` (same object per resolved path, now reentrant). Task 4 uses `worktree.git_lock(root)`.

- [ ] **Step 1: Update imports and add the thread-probe helper**

In `tests/steps/test_worktree.py`, replace lines 9-20:

```python
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_manager.steps import worktree
from agent_manager.steps.worktree import GitError
```

with:

```python
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from lockhelpers import _holder, _probe, _reap, _release

from agent_manager import locks, paths
from agent_manager.steps import worktree
from agent_manager.steps.worktree import GitError
```

Then insert, immediately after the `_recorder` function (after line 79), the following helper:

```python
def _repo_lock_is_free(repo: Path) -> bool:
    """Whether another thread can take `repo`'s in-process git lock right now.

    Probed with a non-blocking acquire from a fresh thread, so it never blocks
    and works for a `Lock` or an `RLock` alike (`RLock.locked()` only exists
    from Python 3.14; this project runs 3.12).
    """
    lock = worktree._repo_lock(str(repo))
    outcome: list[bool] = []

    def take() -> None:
        got = lock.acquire(blocking=False)
        if got:
            lock.release()
        outcome.append(got)

    prober = threading.Thread(target=take)
    prober.start()
    prober.join(timeout=30)
    assert not prober.is_alive()
    return outcome == [True]
```

- [ ] **Step 2: Rewrite the two `.locked()` assertions**

In `test_a_failed_add_releases_the_repository_lock`, replace:

```python
    assert worktree._repo_lock(str(repo)).locked() is False
    result = worktree.ensure(
```

with:

```python
    assert _repo_lock_is_free(repo)
    assert _probe(repo, "git") == "free"
    result = worktree.ensure(
```

In `test_a_same_branch_at_a_different_path_surfaces_gits_error_and_frees_the_lock`, replace:

```python
    assert "add" in excinfo.value.argv
    assert worktree._repo_lock(str(repo)).locked() is False
```

with:

```python
    assert "add" in excinfo.value.argv
    assert _repo_lock_is_free(repo)
    assert _probe(repo, "git") == "free"
```

- [ ] **Step 3: Write the failing tests**

Append to the end of `tests/steps/test_worktree.py`:

```python
# --- Process-wide git lock (spec X7, card 43043f10) -------------------------


def test_the_repository_lock_is_reentrant(tmp_path: Path):
    # It is the in-process layer of a ProcessLock, which re-acquires it on
    # every nested acquire; a plain Lock would deadlock its own thread.
    lock = worktree._repo_lock(str(tmp_path))
    assert lock.acquire(blocking=False)
    try:
        assert lock.acquire(blocking=False)
        lock.release()
    finally:
        lock.release()


@requires_git
def test_git_lock_is_keyed_by_the_resolved_repository(repo: Path):
    lock = worktree.git_lock(repo)

    assert worktree.git_lock(str(repo)) is lock
    assert worktree.git_lock(f"{repo}{os.sep}") is lock
    assert worktree.git_lock(repo / "x" / "..") is lock
    assert lock is locks.project_lock(repo, "git")
    assert lock.path == paths.project_lock_path(repo, "git")
    assert lock._local is worktree._repo_lock(str(repo))


@requires_git
def test_worktree_add_waits_for_the_git_lock_of_another_process(
    repo: Path, tmp_path: Path, monkeypatch
):
    # `progressed` fires on the lane's first failed flock attempt (it is now
    # waiting on the child) or, if nothing locks, when the lane finishes. Only
    # then is the marker written and the child released, so an `add` that saw
    # no marker ran while the child held the git lock.
    marker = tmp_path / "released"
    progressed = threading.Event()
    real_backoff = locks._backoff

    def signalling_backoff(attempt: int) -> float:
        progressed.set()
        return real_backoff(attempt)

    monkeypatch.setattr(locks, "_backoff", signalling_backoff)
    adds: list[bool] = []

    def runner(argv: list[str]) -> str:
        if _is_add(argv):
            adds.append(marker.exists())
        return worktree.run_git(argv)

    outcome: dict[str, object] = {}

    def lane() -> None:
        try:
            outcome["result"] = worktree.ensure(
                branch="m10/waits",
                base="main",
                worktree=str(tmp_path / "wt"),
                repo_dir=str(repo),
                git_runner=runner,
            )
        except BaseException as exc:  # surfaced by the assertions below
            outcome["error"] = exc
        finally:
            progressed.set()

    child = _holder(repo, "git")
    try:
        worker = threading.Thread(target=lane, daemon=True)
        worker.start()
        assert progressed.wait(timeout=30)
        marker.touch()
        _release(child)
        worker.join(timeout=60)
        assert not worker.is_alive()
    finally:
        _reap(child)

    assert "error" not in outcome, outcome
    assert outcome["result"]["created"] is True
    assert adds == [True]
    assert _probe(repo, "git") == "free"
    assert list(repo.rglob("*.lock")) == []  # no lock file in the repository


@requires_git
def test_a_git_lock_timeout_propagates_and_no_worktree_is_added(
    repo: Path, tmp_path: Path, monkeypatch
):
    lock = worktree.git_lock(repo)
    monkeypatch.setattr(lock, "_timeout", 0)
    calls: list[list[str]] = []
    child = _holder(repo, "git")
    try:
        with pytest.raises(locks.LockTimeoutError):
            worktree.ensure(
                branch="m10/timeout",
                base="main",
                worktree=str(tmp_path / "wt"),
                repo_dir=str(repo),
                git_runner=_recorder(calls, worktree.run_git),
            )
    finally:
        _reap(child)

    assert not any(_is_add(argv) for argv in calls)
    assert not (tmp_path / "wt").exists()
    assert _repo_lock_is_free(repo)
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/steps/test_worktree.py -v -k "reentrant or git_lock or waits_for_the_git_lock"`
Expected: FAIL.
- `test_the_repository_lock_is_reentrant` fails on the second `acquire(blocking=False)`, because the lock is a plain `Lock`.
- `test_git_lock_is_keyed_by_the_resolved_repository` and `test_a_git_lock_timeout_propagates_and_no_worktree_is_added` fail with `AttributeError: ... has no attribute 'git_lock'`.
- `test_worktree_add_waits_for_the_git_lock_of_another_process` fails on `adds == [True]`, because it got `[False]`.

- [ ] **Step 5: Implement `git_lock`, the `RLock` registry and the `ensure` switch**

In `src/agent_manager/steps/worktree.py`, replace lines 16-20:

```python
Parallel lanes (parallel-stories decision P3) share one repository, so
`git worktree add` runs under a per-repository lock, re-checking registration
inside it so two threads ensuring the same worktree both succeed. Threads in
one process only. There is deliberately no `git worktree prune`: it is a global
sweep that could remove another lane's not-yet-populated worktree.
```

with:

```python
Parallel lanes (parallel-stories decision P3) share one repository, and so may
separate `am` processes, so `git worktree add` runs under `git_lock`, the
repository's process-wide `git` lock (spec X7 of the multi-process design),
re-checking registration inside it so two lanes ensuring the same worktree both
succeed. A `locks.LockTimeoutError` from it is never caught here. There is
deliberately no `git worktree prune`: it is a global sweep that could remove
another lane's not-yet-populated worktree.
```

Replace the imports (lines 23-27):

```python
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
```

with:

```python
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from agent_manager import locks
```

Replace lines 169-189 (from `_REPO_LOCKS` through the end of `_repo_lock`):

```python
_REPO_LOCKS: dict[str, threading.Lock] = {}
"""One lock per repository, keyed by its resolved path, created on first use."""

_REPO_LOCKS_GUARD = threading.Lock()
"""Guards lookup-or-create in `_REPO_LOCKS`, so one repository never gets two locks."""


def _repo_lock(repo_path: str) -> threading.Lock:
    """The lock serializing `git worktree add` on the repository at `repo_path`.

    Keyed by the resolved path, so a trailing slash, a `..` hop or a symlinked
    spelling of the same repository all share one lock. In-process threads
    only: two `am` processes on one repository are not supported.
    """
    key = str(Path(repo_path).resolve())
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _REPO_LOCKS[key] = lock
        return lock
```

with:

```python
_REPO_LOCKS: dict[str, threading.RLock] = {}
"""One lock per repository, keyed by its resolved path, created on first use."""

_REPO_LOCKS_GUARD = threading.Lock()
"""Guards lookup-or-create in `_REPO_LOCKS`, so one repository never gets two locks."""


def _repo_lock(repo_path: str | Path) -> threading.RLock:
    """The in-process layer of `git_lock` for the repository at `repo_path`.

    Keyed by the resolved path, so a trailing slash, a `..` hop or a symlinked
    spelling of the same repository all share one lock. An `RLock` because a
    `ProcessLock` re-acquires its in-process layer on every nested acquire;
    `ensure` itself never re-enters it. Other `am` processes on the repository
    are coordinated by `git_lock`'s flock (spec X7).
    """
    key = str(Path(repo_path).resolve())
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REPO_LOCKS[key] = lock
        return lock


def git_lock(repo_path: str | Path) -> locks.ProcessLock:
    """The repository's process-wide `git` lock (spec X7).

    Serializes `git worktree add` (with its re-check) here and
    `orchestrate.refresh_git`'s `remote`/`fetch`/`prune` across threads and
    `am` processes. Keyed by the resolved repository path; its in-process
    layer is `_repo_lock(repo_path)`. Never taken while the board lock is held,
    nor the reverse.
    """
    return locks.project_lock(
        Path(repo_path).resolve(), "git", local=_repo_lock(repo_path)
    )
```

In `ensure`, replace:

```python
        # git must never run two `worktree add` on one repository at once.
        # Only the re-check and the add are held under the lock; the reads
        # above and the commit count below stay unlocked.
        with _repo_lock(repo_path):
```

with:

```python
        # git must never run two `worktree add` on one repository at once,
        # from any thread or process. Only the re-check and the add are held
        # under the lock; the reads above and the commit count below stay
        # unlocked.
        with git_lock(repo_path):
```

- [ ] **Step 6: Run the worktree suite**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: all PASS. That includes the existing thread tests `test_eight_distinct_lanes_in_parallel_all_succeed` (`peak == 1`), `test_two_threads_ensuring_the_same_worktree_both_succeed` and `test_an_existing_worktree_takes_no_lock`, and the two rewritten failed-add tests.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/steps/worktree.py tests/steps/test_worktree.py
git commit -m "feat(worktree): worktree add takes the process-wide git lock"
```

---

### Task 4: `refresh_git` runs under the `git` process lock

**Files:**
- Modify: `src/agent_manager/orchestrate.py:530-541`
- Test: `tests/test_orchestrate.py` (Wiring tier: imports, two new tests after `test_a_failed_fetch_propagates_and_leaves_no_run_behind`)

**Interfaces:**
- Consumes: `worktree.git_lock(repo_path) -> locks.ProcessLock` (Task 3); `lockhelpers._holder/_probe/_reap/_release` (Task 1).
- Produces: `orchestrate.refresh_git(root: Path) -> None`, same signature, now holding `worktree.git_lock(root)` for all three git calls.

- [ ] **Step 1: Add the imports**

In `tests/test_orchestrate.py`, replace line 40:

```python
from agent_manager import bases, board, census, cli, control, dag, integration, models, orchestrate, paths
```

with:

```python
from lockhelpers import _holder, _probe, _reap, _release

from agent_manager import bases, board, census, cli, control, dag, integration, locks, models, orchestrate, paths
```

`_release` is imported for parity with the other Wiring files (spec scope item 6). It is unused in this file.

- [ ] **Step 2: Write the failing tests**

In `tests/test_orchestrate.py`, insert immediately after the end of `test_a_failed_fetch_propagates_and_leaves_no_run_behind` (after its `assert list(paths.data_dir().iterdir()) == []` line) and before `def test_only_a_stale_story_is_anchored_and_on_its_last_done_subtask`:

```python
def test_refresh_git_prunes_under_the_git_lock(tmp_path, monkeypatch):
    # Spec X7: `remote`, `fetch origin` and `worktree prune` all run while this
    # process holds the repository's git flock, so another process's probe
    # finds it busy at each call.
    seen: list[tuple[list[str], str]] = []

    def probing_run_git(argv: list[str]) -> str:
        seen.append((argv[2:], _probe(tmp_path, "git")))
        return "origin\n" if argv[2:] == ["remote"] else ""

    monkeypatch.setattr(worktree, "run_git", probing_run_git)

    orchestrate.refresh_git(tmp_path)

    assert seen == [
        (["remote"], "busy"),
        (["fetch", "origin"], "busy"),
        (["worktree", "prune"], "busy"),
    ]
    assert _probe(tmp_path, "git") == "free"


def test_a_git_lock_timeout_in_refresh_git_propagates_before_any_git_call(
    tmp_path, monkeypatch
):
    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run_git", lambda argv: calls.append(argv) or "")
    lock = worktree.git_lock(tmp_path)
    monkeypatch.setattr(lock, "_timeout", 0)
    child = _holder(tmp_path, "git")
    try:
        with pytest.raises(locks.LockTimeoutError):
            orchestrate.refresh_git(tmp_path)
    finally:
        _reap(child)

    assert calls == []
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v -k "refresh_git"`
Expected: FAIL.
- `test_refresh_git_prunes_under_the_git_lock` fails because every probe was `"free"`.
- `test_a_git_lock_timeout_in_refresh_git_propagates_before_any_git_call` fails with `DID NOT RAISE <class 'agent_manager.locks.LockTimeoutError'>`.

- [ ] **Step 4: Implement**

In `src/agent_manager/orchestrate.py`, replace lines 530-541:

```python
def refresh_git(root: Path) -> None:
    """Once per run: `git fetch origin` if an `origin` remote exists, then `git worktree prune`.

    A repo with no `origin` skips the fetch silently. The name must equal
    `origin` exactly: `upstream` or `origin-mirror` is not it. Both calls go
    through `worktree.run_git`, read at call time, and a `GitError` from any
    of them propagates.
    """
    remotes = worktree.run_git(["-C", str(root), "remote"]).split()
    if "origin" in remotes:
        worktree.run_git(["-C", str(root), "fetch", "origin"])
    worktree.run_git(["-C", str(root), "worktree", "prune"])
```

with:

```python
def refresh_git(root: Path) -> None:
    """Once per run: `git fetch origin` if an `origin` remote exists, then `git worktree prune`.

    A repo with no `origin` skips the fetch silently. The name must equal
    `origin` exactly: `upstream` or `origin-mirror` is not it. Both calls go
    through `worktree.run_git`, read at call time, and a `GitError` from any
    of them propagates.

    All three git calls run under `worktree.git_lock(root)` (spec X7), so the
    prune never sweeps while another `am` process is mid-`worktree add` on the
    same repository. A `locks.LockTimeoutError` propagates uncaught, before any
    git call, and reaches `cli.HANDLED` when the run has not started.
    """
    with worktree.git_lock(root):
        remotes = worktree.run_git(["-C", str(root), "remote"]).split()
        if "origin" in remotes:
            worktree.run_git(["-C", str(root), "fetch", "origin"])
        worktree.run_git(["-C", str(root), "worktree", "prune"])
```

- [ ] **Step 5: Run the orchestrate suite**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS. That includes the existing `test_a_repo_with_no_origin_prunes_worktrees_and_never_fetches`, `test_an_origin_remote_is_fetched_exactly_once_before_the_prune` and `test_a_failed_fetch_propagates_and_leaves_no_run_behind`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): refresh_git runs under the process-wide git lock"
```

---

### Task 5: `LockTimeoutError` is a handled CLI refusal

**Files:**
- Modify: `src/agent_manager/cli.py:31-40` (import), `:1077-1089` (`HANDLED` and its docstring)
- Test: `tests/test_cli.py:33-45` (import), `:3195-3212` (parametrize)

**Interfaces:**
- Consumes: `locks.LockTimeoutError(path: Path, timeout: float)` (its `str()` is `"timed out after {timeout}s waiting for the lock {path}"`).
- Produces: `cli.HANDLED` contains `locks.LockTimeoutError`.

- [ ] **Step 1: Write the failing test**

In `tests/test_cli.py`, replace lines 33-45:

```python
from agent_manager import (
    board,
    census,
    cli,
    dag,
    dispatch,
    integration,
    models,
    orchestrate,
    paths,
    prompt,
    store as store_module,
)
```

with:

```python
from agent_manager import (
    board,
    census,
    cli,
    dag,
    dispatch,
    integration,
    locks,
    models,
    orchestrate,
    paths,
    prompt,
    store as store_module,
)
```

Replace the parametrize block of `test_a_handled_error_from_a_milestone_run_is_an_envelope`:

```python
@pytest.mark.parametrize(
    "error",
    [
        cli.CliError("no milestone matches 'Milestone 3'"),
        board.BoardError("brd refused", argv=["brd", "tree"]),
        ValueError("not a card id: 'x'"),
    ],
    ids=["CliError", "BoardError", "ValueError"],
)
```

with:

```python
@pytest.mark.parametrize(
    "error",
    [
        cli.CliError("no milestone matches 'Milestone 3'"),
        board.BoardError("brd refused", argv=["brd", "tree"]),
        ValueError("not a card id: 'x'"),
        # Spec X7: another process held a project lock past its timeout before
        # the run started (e.g. `refresh_git`'s git lock).
        locks.LockTimeoutError(Path("/data/projects/abc.git.lock"), 600.0),
    ],
    ids=["CliError", "BoardError", "ValueError", "LockTimeoutError"],
)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py -v -k "test_a_handled_error_from_a_milestone_run_is_an_envelope"`
Expected: the `LockTimeoutError` case FAILS in `_refusal` on `assert result.exit_code == cli.EXIT_ERROR`, because the error escapes as an unhandled exception. The other three cases PASS.

- [ ] **Step 3: Implement**

In `src/agent_manager/cli.py`, replace lines 31-40:

```python
from agent_manager import (
    board,
    census,
    control,
    dag,
    dispatch,
    models,
    prompt,
    store as store_module,
)
```

with:

```python
from agent_manager import (
    board,
    census,
    control,
    dag,
    dispatch,
    locks,
    models,
    prompt,
    store as store_module,
)
```

Replace lines 1077-1089:

```python
HANDLED: tuple[type[BaseException], ...] = (
    CliError,
    board.BoardError,
    EngineError,
    ValueError,
)
"""Everything the command turns into an `ok: false` envelope and exit 3.

`ValueError` is in the list for one concrete reason: `dag.short_id` raises a
bare one for a card id that is not a UUID, and a typed `--card` must not come
back as a traceback. Anything outside this tuple is a bug in this program and
should crash loudly with its stack intact.
"""
```

with:

```python
HANDLED: tuple[type[BaseException], ...] = (
    CliError,
    board.BoardError,
    EngineError,
    ValueError,
    locks.LockTimeoutError,
)
"""Everything the command turns into an `ok: false` envelope and exit 3.

`ValueError` is in the list for one concrete reason: `dag.short_id` raises a
bare one for a card id that is not a UUID, and a typed `--card` must not come
back as a traceback. `locks.LockTimeoutError` is in it because a start refused
while another `am` process held a project lock past its timeout (spec X7) is a
refusal, not a bug; nothing below the CLI catches it. Anything outside this
tuple is a bug in this program and should crash loudly with its stack intact.
"""
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_cli.py -v -k "handled_error or import_cleanly"`
Expected: all four parametrized cases PASS, and `test_cli_and_orchestrate_import_cleanly_in_either_order` still PASSES (the new `locks` import adds no cycle: `locks` imports only `paths`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): a LockTimeoutError is an ok:false envelope at exit 3"
```

---

### Task 6: Full verification and invariant check

**Files:** none modified unless the suite finds a regression.

- [ ] **Step 1: Confirm nothing catches `LockTimeoutError` outside the CLI**

Run: `git grep -n "LockTimeoutError" -- src/agent_manager`
Expected: matches only in `src/agent_manager/locks.py` (definition and raises), the docstrings of `board.py`, `rollup.py`, `worktree.py` and `orchestrate.py`, and `cli.py` (`HANDLED` and its docstring). There must be no `except` clause naming it outside `locks.py`.

- [ ] **Step 2: Confirm the stale "not supported" wording is gone**

Run: `git grep -n -e "not supported" -e "Threads in one process only" -e "In-process threads only" -- src/agent_manager/board.py src/agent_manager/steps/worktree.py`
Expected: no output.

- [ ] **Step 3: Confirm the board and git locks are never nested**

Run: `git grep -n -e "git_lock" -e "write_lock" -- src/agent_manager`
Expected: `write_lock` appears only in `board.py` (definition, `set_status`) and `steps/rollup.py`. `git_lock` appears only in `steps/worktree.py` (definition, `ensure`) and `orchestrate.py` (`refresh_git`). None of these bodies calls the other module's lock or a store method.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: all PASS (the e2e tests stay deselected by `addopts`), and there is no `DATA-DIR GUARD FAILED` line.

- [ ] **Step 5: Commit any fix-ups (only if Steps 1-4 required changes)**

```bash
git add -A
git commit -m "fix: address full-suite findings for the process-wide lock wiring"
```
