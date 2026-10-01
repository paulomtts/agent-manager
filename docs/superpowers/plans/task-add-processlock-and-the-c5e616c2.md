<!-- task-pipeline: validated -->
# Add ProcessLock and the project lock files (subtask c5e616c2)

Parent story: 00220793 "Process-wide locks: board writes and git worktree operations". Source design: `docs/superpowers/specs/2026-09-27-multi-process-design.md` sections X7 and X8, and Plan Task 1.1 in `docs/superpowers/plans/2026-09-27-multi-process.md`. Both documents exist only in the `.claude/worktrees/docs-multi-process/` worktree and are not yet on `master`. This subtask narrows that agreed design. It adds no new design.

## Scope

In scope (only these four files):
- `src/agent_manager/paths.py`: add `project_lock_path`.
- `src/agent_manager/locks.py` (new): `LOCK_TIMEOUT_SECONDS`, `LockTimeoutError`, `LockOrderError`, `ProcessLock`, `project_lock`.
- `tests/test_locks.py` (new).
- `tests/test_paths.py`: additions for `project_lock_path`.

Out of scope. These belong to sibling subtask 43043f10 (Plan Task 1.2):
- Any change to `board.py`, `steps/rollup.py`, `steps/worktree.py`, `orchestrate.py` or `cli.py`.
- `board.write_lock` and `worktree.git_lock`.
- Adding `LockTimeoutError` to `cli.HANDLED`.
- Creating `tests/lockhelpers.py`. The holder and probe helpers stay in `tests/test_locks.py` for now. Write them so they can be moved into another module unchanged: module-level, depending only on `sys.executable`, `paths` and `locks`.
- No pid file, heartbeat, staleness rule or other liveness mechanism. This lock only provides mutual exclusion. Run liveness belongs to the milestone-9 lease machinery.

## Observable behaviour

### `paths.project_lock_path(root: Path, name: str) -> Path`

- Returns `data_dir()/"projects"/f"{digest}.{name}.lock"`.
- `digest` is `sha256(str(root.resolve()).encode()).hexdigest()`, the same digest `project_db_path` uses. The lock file for a root therefore sits next to that root's `.db`. Symlinked and relative roots resolve to the same path.
- Creates the `projects` directory. Never creates the lock file.

### `locks` module

- `LOCK_TIMEOUT_SECONDS = 600.0`.
- `LockTimeoutError(RuntimeError)` exposes `.path`, the lock file path.
- `LockOrderError(RuntimeError)`.

### `ProcessLock(path, *, local: threading.RLock | None = None, timeout: float = LOCK_TIMEOUT_SECONDS)`

- Exposes `.path` as a `Path`.
- If `local` is not given, it defaults to a fresh `RLock`.
- Provides `acquire(timeout=None)`, `release()` and `__enter__`/`__exit__`.
- Reentrancy:
  - The lock is reentrant per thread.
  - Only the outermost `acquire` opens the file and takes the flock. A nested `acquire` on the same thread only increments a depth counter and re-enters `local`. It must not open a second descriptor, because a second flock would deadlock the process against itself.
- Acquire order:
  1. The outermost `acquire` takes `local` first.
  2. It opens the lock file with `os.open(path, O_RDWR | O_CREAT, 0o644)`, non-inheritable.
  3. It polls `fcntl.flock(fd, LOCK_EX | LOCK_NB)`. Between attempts it waits `threading.Event().wait(min(0.05 * 2**n, 0.5))`, until the effective timeout runs out.
  4. The effective timeout is the argument if one is given, otherwise the instance's `timeout`.
  5. `timeout=0` tries once. `local` is then taken with `blocking=False`, because `RLock.acquire(timeout=0)` raises.
- Release order:
  1. The outermost `release` unlocks the flock (`LOCK_UN`) and closes the descriptor.
  2. It removes the lock from the thread's held set.
  3. It releases `local`.
  - Every nesting level releases `local` once.
- Follow the reference `acquire` in Plan Task 1.1 step 3.

### `project_lock(root, name, *, local=None) -> ProcessLock`

- Returns one object per resolved lock path per process.
- Uses a registry guarded by a module-level lock, in the same pattern as `worktree._REPO_LOCKS` / `_REPO_LOCKS_GUARD` (`src/agent_manager/steps/worktree.py:169-189`).
- A later call for the same path with a different `local` raises `ValueError`.

### Invariants (X7/X8)

- The in-process lock is always taken before the flock and released after it.
- A thread holds at most one `ProcessLock` at a time. `board` and `git` are therefore never held together.
- Nothing in this module opens a store transaction.
- Lock files live only under the data directory, never inside a repository or worktree.
- A crash (SIGKILL) releases the flock, because the kernel drops it when the descriptor closes.

## Error paths

- **Flock not obtained in time.** Release `local` (the in-process layer must not leak), then raise `LockTimeoutError` with `.path`.
- **`local` not obtained in time.** Raise `LockTimeoutError`. Nothing is left held.
- **Any exception while taking the flock on the outermost level.** Release `local` and re-raise.
- **Thread already holds a different `ProcessLock`.** `acquire` raises `LockOrderError` before taking anything. The message names the locks already held.
- **`project_lock` called for an already-registered path with a different `local`.** Raises `ValueError`.

## Tests

### Tier placement

The rule is design spec §14 "Testing" (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:505-520`):
- Pure functions and module-level logic get unit tests in the default suite under `tests/`, mirroring `src/`.
- "End to end" means only the slow, opt-in real-harness test (`tests/e2e`).
- The plan labels the Task 1.1 lock tests "(unit)".

All tests below are therefore **unit** tests in the default suite. The child processes they spawn are plain `python -c` holder/probe scripts, not `am` or a harness, so they do not make a test e2e. Children inherit the test's `XDG_DATA_HOME` from `tests/conftest.py`. No test sleeps to establish ordering. Cross-process order comes from stdout lines (`held`, `released`, `busy`/`free`), stdin release lines and exit codes. Thread order comes from `threading.Event` handshakes.

### `tests/test_locks.py` (unit)

The helpers follow the plan's literal skeleton:
- `HOLDER`/`_holder(root, name)`: a child acquires, prints `held`, waits for a stdin line, releases and prints `released`.
- `_probe(root, name)`: a child opens `project_lock_path` and tries `LOCK_EX | LOCK_NB` once, printing `busy` or `free`.

Tests:
1. `test_a_lock_held_by_another_process_times_out_then_is_acquired`: with a holder child running, `acquire(timeout=0)` raises `LockTimeoutError` whose `.path == paths.project_lock_path(root, "git")`. After the child prints `released`, `acquire(timeout=0)` succeeds.
2. `test_a_killed_holder_releases_the_lock`: SIGKILL the holder and wait for it. `acquire(timeout=0)` then succeeds. (Review Focus 5.)
3. `test_nesting_on_one_thread_takes_one_flock`: the probe reports `busy` inside both nested levels and after the inner exit, and `free` after the outer exit. (Review Focus 1.)
4. `test_two_threads_serialise_on_the_in_process_layer`: a second thread blocks while the first holds the lock and proceeds only after the first releases. Coordinated by `threading.Event`, with no sleeps.
5. `test_holding_one_project_lock_and_asking_for_another_is_refused`: while holding `git`, acquiring `board` raises `LockOrderError`.
6. `test_the_lock_file_is_under_the_data_directory_not_the_repository`: after an acquire/release, the lock file is under `paths.data_dir()` and not under the repository root.
7. `test_a_timeout_releases_the_in_process_layer`: after a `LockTimeoutError` caused by a holder child, another thread can take `local` (for example `local.acquire(blocking=False)` returns `True`).

Additional unit tests implied by the interface:
- A second `project_lock` for the same root and name returns the identical object.
- A different `local` for an already-registered path raises `ValueError`.

### `tests/test_paths.py` (unit)

These mirror the style of the existing `project_db_path` tests:
- The `project_lock_path` digest equals `project_db_path`'s. Its filename is `f"{digest}.{name}.lock"`, and it lives in the same `projects` directory.
- Symlinked and relative roots resolve to the same lock path.
- Calling it creates the `projects` directory but not the lock file.

## Verification

`uv run pytest` fully green, `tests/e2e` included. Branch prefix `m10`, based on `master` with milestone 9 merged. Nothing is pushed.

## Note on inputs

The exploration findings given to this stage were truncated at 8000 characters, in the middle of the test-tier section. That suggests the upstream stage over-ran its brief. The tier rule above was recovered by reading design spec §14 and the plan's "(unit)" label directly, not by guessing what the cut-off text said.

---

# ProcessLock and project lock files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `paths.project_lock_path` and a new `agent_manager.locks` module (`ProcessLock`, `project_lock`, `LockTimeoutError`, `LockOrderError`, `LOCK_TIMEOUT_SECONDS`) giving reentrant, process-wide mutual exclusion via an `fcntl.flock` on a file beside the project database.

**Architecture:** `project_lock_path` shares `project_db_path`'s sha256 digest through one private helper, so a root's `.lock` files sit next to its `.db` under the data directory. `ProcessLock` layers a flock over an in-process `threading.RLock`: the outermost `acquire` takes the `RLock` then the flock (polled with capped exponential back-off), nested acquires only bump a depth counter, and a thread-local "held" set refuses a second, different `ProcessLock` on the same thread. `project_lock` hands out one `ProcessLock` per lock path per process through a guarded registry, mirroring `worktree._REPO_LOCKS`.

**Tech Stack:** Python 3.12, `fcntl`, `os`, `threading`, `hashlib`; pytest with real child processes (`subprocess.Popen([sys.executable, "-c", ...])`). No new dependency.

**Spec:** `docs/superpowers/specs/task-add-processlock-and-the-c5e616c2-design.md` (prepended above). Upstream: `.claude/worktrees/docs-multi-process/docs/superpowers/specs/2026-09-27-multi-process-design.md` X7/X8 and `.claude/worktrees/docs-multi-process/docs/superpowers/plans/2026-09-27-multi-process.md` Task 1.1 (paths relative to the main checkout `/home/paulomtts/Code/agent-manager`).

## Global Constraints

- Only four files change: `src/agent_manager/paths.py`, `src/agent_manager/locks.py` (new), `tests/test_paths.py`, `tests/test_locks.py` (new). No change to `board.py`, `steps/rollup.py`, `steps/worktree.py`, `orchestrate.py` or `cli.py`; do not create `tests/lockhelpers.py` (sibling card 43043f10 owns all of that).
- Lock order: a `ProcessLock` takes its in-process lock before its flock and releases it after. A thread holds at most one `ProcessLock`.
- No store transaction is opened by anything in `locks.py`; `locks.py` imports only `paths` from the package.
- Lock files live under `paths.data_dir()/"projects"`, never in a repository or worktree. The lock file is never deleted on release.
- One liveness mechanism: no pid file, no heartbeat, no staleness rule in this module.
- No test sleeps to prove ordering. Cross-process order comes from pipes (`held`/`released`/`busy`/`free` lines, a stdin release line) and exit codes; thread order from `threading.Event`. `Event.wait(timeout=30)` / `join(timeout=30)` are only hang guards.
- Children inherit the test's `XDG_DATA_HOME` from the autouse `isolated_data_home` fixture in `tests/conftest.py`, so they share the parent's lock files.
- All tests are unit tier: `tests/test_paths.py` and `tests/test_locks.py`, directly under `tests/`, mirroring `src/agent_manager/paths.py` and `src/agent_manager/locks.py` (same convention as the existing `tests/test_store.py`, `tests/test_control.py`).
- Verification: `uv run pytest` whole suite green. Branch `m10/task-add-processlock-and-the-c5e616c2`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-add-processlock-and-the-c5e616c2`, base `master`. Nothing is pushed.

## Review Focus

1. **A long wait must not crash the back-off.** Taken literally, `0.05 * 2**n` raises `OverflowError` once `n` reaches 1024 (about 510 s into the default 600 s wait at 0.5 s per poll), so a caller waiting on a slow holder would get an `OverflowError` rather than the lock or a `LockTimeoutError`. The exponent is capped in `_backoff`, and `test_backoff_doubles_then_caps_and_never_overflows` in Task 2 pins it.
2. **A stray `release()`** from a thread that does not hold the lock (never acquired, or another thread's lock) raises `RuntimeError` and leaves the real holder's flock and depth untouched. Pinned by `test_release_by_a_thread_that_does_not_hold_it_is_refused` in Task 2.
3. **An exception inside `with lock:`** releases both layers: the probe reports `free` and another thread can take `local`. Pinned by `test_an_exception_in_the_body_releases_both_layers` in Task 2.
4. **A `LockOrderError` refusal takes nothing.** After the refusal, the refused lock's flock is free (probe) and its `local` is free to another thread. Pinned inside `test_holding_one_project_lock_and_asking_for_another_is_refused` in Task 2.
5. **Different spellings of one root share one lock object.** A symlinked or relative root must return the identical `ProcessLock`, so a nested acquire through the other spelling re-enters rather than deadlocking on a second flock. Pinned by `test_project_lock_is_one_object_per_resolved_path` in Task 2 and `test_project_lock_path_resolves_relative_and_symlinked_spelling` in Task 1.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/agent_manager/paths.py` | Add `_project_digest(root)` (shared by `project_db_path` and the new `project_lock_path`) and `project_lock_path(root, name)`. |
| `src/agent_manager/locks.py` (new) | `LOCK_TIMEOUT_SECONDS`, `LockTimeoutError`, `LockOrderError`, `_backoff`, `_flock`, thread-local held set, `ProcessLock`, `project_lock` registry. |
| `tests/test_paths.py` | Unit tests for `project_lock_path`. |
| `tests/test_locks.py` (new) | Unit tests for `locks`, including module-level `HOLDER`/`_holder`/`_release`/`_reap`/`PROBE`/`_probe` helpers that sibling card 43043f10 will later move to `tests/lockhelpers.py`. |

---

### Task 1: `paths.project_lock_path`

**Files:**
- Modify: `src/agent_manager/paths.py:22-26` (`project_db_path`) and add `project_lock_path` right after it
- Test: `tests/test_paths.py` (append after `test_project_db_path_accepts_a_root_that_does_not_exist`, line 125)

**Interfaces:**
- Consumes: `paths.data_dir() -> Path` (existing, `src/agent_manager/paths.py:14`).
- Produces: `paths.project_lock_path(root: Path, name: str) -> Path`, returning `data_dir()/"projects"/f"{sha256(str(root.resolve()).encode()).hexdigest()}.{name}.lock"`; creates the `projects` directory, never the file. Private `paths._project_digest(root: Path) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paths.py` (after line 125; `hashlib`, `Path`, `pytest` and `paths` are already imported at the top):

```python
def test_project_lock_path_sits_beside_the_project_db(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    digest = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()
    result = paths.project_lock_path(project_root, "board")
    assert result.name == f"{digest}.board.lock"
    assert result.parent == paths.project_db_path(project_root).parent
    assert result.parent == tmp_path / "data" / "agent-manager" / "projects"


def test_project_lock_path_creates_the_projects_dir_but_not_the_file(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    projects_dir = tmp_path / "data" / "agent-manager" / "projects"
    assert not projects_dir.exists()

    result = paths.project_lock_path(project_root, "git")
    assert projects_dir.is_dir()
    assert not result.exists()


def test_project_lock_path_differs_per_name_and_per_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    repo1 = tmp_path / "repo1"
    repo1.mkdir()
    repo2 = tmp_path / "repo2"
    repo2.mkdir()

    assert paths.project_lock_path(repo1, "board") != paths.project_lock_path(
        repo1, "git"
    )
    assert paths.project_lock_path(repo1, "git") != paths.project_lock_path(
        repo2, "git"
    )
    assert paths.project_lock_path(repo1, "git") == paths.project_lock_path(
        repo1, "git"
    )


def test_project_lock_path_resolves_relative_and_symlinked_spelling(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project_root)
    monkeypatch.chdir(tmp_path)

    expected = paths.project_lock_path(project_root, "git")
    assert paths.project_lock_path(Path("repo"), "git") == expected
    assert paths.project_lock_path(project_root / "sub" / "..", "git") == expected
    assert paths.project_lock_path(link, "git") == expected


def test_project_lock_path_digest_matches_a_fixed_vector(monkeypatch, tmp_path):
    # Same literal digest as test_project_db_path_digest_matches_a_fixed_vector:
    # the lock file and the database share one digest per root.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert paths.project_lock_path(Path("/nonexistent/repo"), "git").name == (
        "5b6e8e2d129e523b4fabf8a73dcdc18cb7f253565385fd9e6e5c0888ba865785.git.lock"
    )


def test_project_lock_path_never_lands_inside_the_repository(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    result = paths.project_lock_path(project_root, "board")
    assert result.is_relative_to(paths.data_dir())
    assert not result.is_relative_to(project_root)
    assert list(project_root.iterdir()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -k project_lock_path -v`
Expected: 6 FAILED, each with `AttributeError: module 'agent_manager.paths' has no attribute 'project_lock_path'`.

- [ ] **Step 3: Implement**

In `src/agent_manager/paths.py`, replace lines 22-26 (the whole `project_db_path` function) with:

```python
def _project_digest(root: Path) -> str:
    """The per-project file stem: sha256 of the resolved root path."""
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()


def _projects_dir() -> Path:
    result = data_dir() / "projects"
    result.mkdir(parents=True, exist_ok=True)
    return result


def project_db_path(root: Path) -> Path:
    return _projects_dir() / f"{_project_digest(root)}.db"


def project_lock_path(root: Path, name: str) -> Path:
    """The file a process-wide lock named `name` flocks for the project at `root`.

    Beside the project's database under the data directory, never inside the
    repository. Creates the `projects` directory; the lock file itself is created
    by whoever first opens it (`locks.ProcessLock`).
    """
    return _projects_dir() / f"{_project_digest(root)}.{name}.lock"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: all PASS (the existing `project_db_path` tests included, proving the refactor kept its digest).

- [ ] **Step 5: Commit**

```bash
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-add-processlock-and-the-c5e616c2 add src/agent_manager/paths.py tests/test_paths.py
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-add-processlock-and-the-c5e616c2 commit -m "feat(paths): project_lock_path beside the project database"
```

---

### Task 2: `locks` module — `ProcessLock` and `project_lock`

**Files:**
- Create: `src/agent_manager/locks.py`
- Create: `tests/test_locks.py`

**Interfaces:**
- Consumes: `paths.project_lock_path(root: Path, name: str) -> Path` (Task 1).
- Produces (later relied on by sibling card 43043f10, which calls `locks.project_lock(root, "board", local=WRITE_LOCK)` and `locks.project_lock(root, "git", local=_repo_lock(...))`, and adds `locks.LockTimeoutError` to `cli.HANDLED`):
  - `LOCK_TIMEOUT_SECONDS: float = 600.0`
  - `class LockTimeoutError(RuntimeError)`: `__init__(self, path: Path, timeout: float)`, attributes `.path: Path`, `.timeout: float`
  - `class LockOrderError(RuntimeError)`
  - `class ProcessLock`: `__init__(self, path: Path, *, local: threading.RLock | None = None, timeout: float = LOCK_TIMEOUT_SECONDS)`, `.path: Path`, `acquire(self, timeout: float | None = None) -> None`, `release(self) -> None`, `__enter__(self) -> ProcessLock`, `__exit__(self, *exc) -> None`
  - `project_lock(root: Path, name: str, *, local: threading.RLock | None = None) -> ProcessLock`
  - Private, tested directly: `_backoff(attempt: int) -> float`
- Test helpers (module-level in `tests/test_locks.py`, to be moved unchanged by 43043f10): `HOLDER: str`, `_holder(root: Path, name: str) -> subprocess.Popen[str]`, `_release(child) -> None`, `_reap(child) -> None`, `PROBE: str`, `_probe(root: Path, name: str) -> str` (returns `"busy"` or `"free"`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_locks.py`:

```python
"""Unit tests for `agent_manager.locks` (spec X7, X8; plan Task 1.1).

Cross-process order comes from pipes and exit codes, thread order from
`threading.Event`; no test sleeps to establish ordering. The holder and probe
helpers are module-level and depend only on `sys.executable`, `paths` and
`locks`, so they can move to a shared helper module unchanged. Children inherit
the test's `XDG_DATA_HOME` (tests/conftest.py), so they flock the same files.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from agent_manager import locks, paths

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


def _free_to_another_thread(local: threading.RLock) -> bool:
    """Whether a different thread can take `local` right now (never blocks)."""
    outcome: list[bool] = []

    def take() -> None:
        got = local.acquire(blocking=False)
        if got:
            local.release()
        outcome.append(got)

    worker = threading.Thread(target=take)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive()
    return outcome[0]


# --- the seven plan tests -------------------------------------------------


def test_a_lock_held_by_another_process_times_out_then_is_acquired(tmp_path):
    child = _holder(tmp_path, "git")
    try:
        lock = locks.project_lock(tmp_path, "git")
        with pytest.raises(locks.LockTimeoutError) as caught:
            lock.acquire(timeout=0)
        assert caught.value.path == paths.project_lock_path(tmp_path, "git")
        _release(child)
        lock.acquire(timeout=0)
        lock.release()
    finally:
        _reap(child)


def test_a_killed_holder_releases_the_lock(tmp_path):  # Review Focus 5 (plan)
    child = _holder(tmp_path, "board")
    try:
        child.kill()
        child.wait()
        lock = locks.project_lock(tmp_path, "board")
        lock.acquire(timeout=0)
        lock.release()
    finally:
        _reap(child)


def test_nesting_on_one_thread_takes_one_flock(tmp_path):  # Review Focus 1 (plan)
    lock = locks.project_lock(tmp_path, "board")
    with lock:
        with lock:
            assert _probe(tmp_path, "board") == "busy"
        assert _probe(tmp_path, "board") == "busy"
    assert _probe(tmp_path, "board") == "free"


def test_two_threads_serialise_on_the_in_process_layer(tmp_path):
    lock = locks.project_lock(tmp_path, "board")
    events: list[str] = []
    failures: list[BaseException] = []
    refused = threading.Event()

    def second() -> None:
        try:
            try:
                lock.acquire(timeout=0)
            except locks.LockTimeoutError:
                events.append("second refused")
            else:
                lock.release()
                events.append("second got it while the first held it")
            refused.set()
            lock.acquire()
            events.append("second acquired")
            assert _probe(tmp_path, "board") == "busy"
            lock.release()
        except BaseException as error:  # surfaced to the main thread below
            failures.append(error)
            refused.set()

    lock.acquire()
    worker = threading.Thread(target=second)
    worker.start()
    assert refused.wait(timeout=30)
    events.append("first releasing")
    lock.release()
    worker.join(timeout=30)

    assert not worker.is_alive()
    assert failures == []
    assert events == ["second refused", "first releasing", "second acquired"]
    assert _probe(tmp_path, "board") == "free"


def test_holding_one_project_lock_and_asking_for_another_is_refused(tmp_path):
    board_local = threading.RLock()
    git = locks.project_lock(tmp_path, "git")
    board = locks.project_lock(tmp_path, "board", local=board_local)
    with git:
        with pytest.raises(locks.LockOrderError) as caught:
            board.acquire()
        assert str(git.path) in str(caught.value)
        # Review Focus 4: the refusal took neither layer of `board`.
        assert _probe(tmp_path, "board") == "free"
        assert _free_to_another_thread(board_local)
    board.acquire(timeout=0)
    board.release()


def test_the_lock_file_is_under_the_data_directory_not_the_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with locks.project_lock(repo, "board"):
        pass
    lock_file = paths.project_lock_path(repo, "board")
    assert lock_file.exists()  # kept after release; never deleted
    assert lock_file.is_relative_to(paths.data_dir())
    assert not lock_file.is_relative_to(repo)
    assert list(repo.iterdir()) == []


def test_a_timeout_releases_the_in_process_layer(tmp_path):
    local = threading.RLock()
    child = _holder(tmp_path, "git")
    try:
        lock = locks.project_lock(tmp_path, "git", local=local)
        with pytest.raises(locks.LockTimeoutError):
            lock.acquire(timeout=0)
        assert _free_to_another_thread(local)
        _release(child)
        lock.acquire(timeout=0)
        lock.release()
    finally:
        _reap(child)


# --- timeouts --------------------------------------------------------------


def test_a_positive_timeout_polls_until_it_runs_out(tmp_path):
    local = threading.RLock()
    child = _holder(tmp_path, "git")
    try:
        lock = locks.project_lock(tmp_path, "git", local=local)
        with pytest.raises(locks.LockTimeoutError) as caught:
            lock.acquire(timeout=0.2)
        assert caught.value.path == paths.project_lock_path(tmp_path, "git")
        assert caught.value.timeout == 0.2
        assert _free_to_another_thread(local)
    finally:
        _reap(child)


def test_the_instance_timeout_applies_when_acquire_gets_none(tmp_path):
    child = _holder(tmp_path, "git")
    try:
        lock = locks.ProcessLock(paths.project_lock_path(tmp_path, "git"), timeout=0)
        with pytest.raises(locks.LockTimeoutError):
            lock.acquire()
    finally:
        _reap(child)


def test_timeout_default_is_ten_minutes():
    assert locks.LOCK_TIMEOUT_SECONDS == 600.0
    lock = locks.ProcessLock(Path("/nonexistent/x.lock"))
    assert lock.path == Path("/nonexistent/x.lock")


def test_backoff_doubles_then_caps_and_never_overflows():  # Review Focus 1
    assert [locks._backoff(n) for n in range(5)] == pytest.approx(
        [0.05, 0.1, 0.2, 0.4, 0.5]
    )
    assert locks._backoff(1024) == 0.5
    assert locks._backoff(10_000) == 0.5


def test_the_errors_are_runtime_errors():
    assert issubclass(locks.LockTimeoutError, RuntimeError)
    assert issubclass(locks.LockOrderError, RuntimeError)
    error = locks.LockTimeoutError(Path("/x.lock"), 1.5)
    assert error.path == Path("/x.lock")
    assert "/x.lock" in str(error)


# --- release and failure paths ----------------------------------------------


def test_release_by_a_thread_that_does_not_hold_it_is_refused(tmp_path):  # RF 2
    lock = locks.project_lock(tmp_path, "board")
    with pytest.raises(RuntimeError):
        lock.release()

    failures: list[BaseException] = []

    def stray_release() -> None:
        try:
            lock.release()
        except BaseException as error:
            failures.append(error)

    with lock:
        worker = threading.Thread(target=stray_release)
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], RuntimeError)
        assert _probe(tmp_path, "board") == "busy"
    assert _probe(tmp_path, "board") == "free"


def test_an_exception_in_the_body_releases_both_layers(tmp_path):  # RF 3
    local = threading.RLock()
    lock = locks.project_lock(tmp_path, "board", local=local)
    with pytest.raises(KeyError):
        with lock:
            with lock:
                raise KeyError("boom")
    assert _probe(tmp_path, "board") == "free"
    assert _free_to_another_thread(local)
    lock.acquire(timeout=0)
    lock.release()


def test_a_flock_failure_releases_the_in_process_layer(tmp_path):
    local = threading.RLock()
    lock = locks.ProcessLock(tmp_path / "missing-dir" / "x.lock", local=local)
    with pytest.raises(FileNotFoundError):
        lock.acquire(timeout=0)
    assert _free_to_another_thread(local)


def test_the_lock_descriptor_is_not_inheritable(tmp_path):
    lock = locks.project_lock(tmp_path, "git")
    with lock:
        assert os.get_inheritable(lock._fd) is False


# --- the registry ------------------------------------------------------------


def test_project_lock_is_one_object_per_resolved_path(tmp_path, monkeypatch):  # RF 5
    repo = tmp_path / "repo"
    repo.mkdir()
    link = tmp_path / "link"
    link.symlink_to(repo)
    monkeypatch.chdir(tmp_path)

    lock = locks.project_lock(repo, "board")
    assert locks.project_lock(repo, "board") is lock
    assert locks.project_lock(link, "board") is lock
    assert locks.project_lock(Path("repo"), "board") is lock
    assert locks.project_lock(repo, "git") is not lock
    assert lock.path == paths.project_lock_path(repo, "board")

    with locks.project_lock(repo, "board"):
        with locks.project_lock(link, "board"):  # re-enters: no second flock
            assert _probe(repo, "board") == "busy"
    assert _probe(repo, "board") == "free"


def test_project_lock_refuses_a_different_local_for_a_registered_path(tmp_path):
    local = threading.RLock()
    lock = locks.project_lock(tmp_path, "board", local=local)
    assert locks.project_lock(tmp_path, "board", local=local) is lock
    assert locks.project_lock(tmp_path, "board") is lock  # None means "whichever"
    with pytest.raises(ValueError):
        locks.project_lock(tmp_path, "board", local=threading.RLock())


def test_project_lock_refuses_an_explicit_local_after_a_default_one(tmp_path):
    locks.project_lock(tmp_path, "git")
    with pytest.raises(ValueError):
        locks.project_lock(tmp_path, "git", local=threading.RLock())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_locks.py -v`
Expected: collection ERROR for `tests/test_locks.py` with `ImportError: cannot import name 'locks' from 'agent_manager'` (the module does not exist yet).

- [ ] **Step 3: Implement**

Create `src/agent_manager/locks.py`:

```python
"""Process-wide locks on files beside the project database (spec X7, X8).

A `ProcessLock` layers an `fcntl.flock` on a file under the data directory
(`paths.project_lock_path`) over an in-process `threading.RLock`. The in-process
lock is always taken before the flock and released after it. The lock is
reentrant per thread: only the outermost `acquire` opens the file and takes the
flock, because a second flock on a new descriptor would block its own process.

A thread holds at most one `ProcessLock` at a time; asking for a second,
different one raises `LockOrderError`, which is what keeps the `board` and `git`
locks from ever being held together. A crashed holder releases its flock when
the kernel closes its descriptor, so this module has no liveness mechanism of
its own (no pid file, no heartbeat, no staleness rule). Nothing here opens a
store transaction.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from pathlib import Path

from agent_manager import paths

LOCK_TIMEOUT_SECONDS = 600.0
"""Default wait for a `ProcessLock`, in seconds."""

_BACKOFF_FIRST = 0.05
_BACKOFF_CAP = 0.5
_BACKOFF_MAX_EXPONENT = 4
"""`0.05 * 2**4` already exceeds the cap; bounding the exponent keeps a long wait
from overflowing the float conversion of `2**n`."""


class LockTimeoutError(RuntimeError):
    """A `ProcessLock` was not obtained within its timeout."""

    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__(f"timed out after {timeout}s waiting for the lock {path}")
        self.path = Path(path)
        self.timeout = timeout


class LockOrderError(RuntimeError):
    """A thread holding one `ProcessLock` asked for a different one."""


_HELD = threading.local()


def _held() -> set[ProcessLock]:
    """The `ProcessLock`s the calling thread holds (at most one)."""
    held = getattr(_HELD, "locks", None)
    if held is None:
        held = set()
        _HELD.locks = held
    return held


def _backoff(attempt: int) -> float:
    """Seconds to wait before flock attempt `attempt + 1`: 0.05 doubling, capped at 0.5."""
    return min(_BACKOFF_FIRST * 2 ** min(attempt, _BACKOFF_MAX_EXPONENT), _BACKOFF_CAP)


def _flock(path: Path, timeout: float) -> int:
    """Open `path` and take an exclusive flock on it, polling until `timeout`.

    Returns the open, non-inheritable descriptor holding the flock. `timeout <= 0`
    tries once. Raises `LockTimeoutError` when the time runs out; the descriptor
    is closed on every failure.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    try:
        deadline = time.monotonic() + timeout
        attempt = 0
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeoutError(path, timeout) from None
                threading.Event().wait(min(_backoff(attempt), remaining))
                attempt += 1
    except BaseException:
        os.close(fd)
        raise


class ProcessLock:
    """A reentrant lock shared by the threads of this process and by other processes."""

    def __init__(
        self,
        path: Path,
        *,
        local: threading.RLock | None = None,
        timeout: float = LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.path = Path(path)
        self._local = local if local is not None else threading.RLock()
        self._timeout = timeout
        self._depth = 0  # touched only by the thread holding `_local`
        self._fd: int | None = None

    def __repr__(self) -> str:
        return f"ProcessLock({str(self.path)!r})"

    def acquire(self, timeout: float | None = None) -> None:
        held = _held()
        if held and self not in held:
            raise LockOrderError(
                f"{self.path}: this thread already holds "
                f"{sorted(str(lock.path) for lock in held)}"
            )
        wait = self._timeout if timeout is None else timeout
        # `RLock.acquire(timeout=0)` raises, so a zero timeout is "try once".
        got = (
            self._local.acquire(timeout=wait)
            if wait > 0
            else self._local.acquire(blocking=False)
        )
        if not got:
            raise LockTimeoutError(self.path, wait)
        if self._depth == 0:
            try:
                self._fd = _flock(self.path, wait)
            except BaseException:
                self._local.release()
                raise
            held.add(self)
        self._depth += 1

    def release(self) -> None:
        held = _held()
        if self not in held:
            raise RuntimeError(f"{self.path}: released by a thread that does not hold it")
        try:
            self._depth -= 1
            if self._depth == 0:
                fd, self._fd = self._fd, None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
                    held.discard(self)
        finally:
            self._local.release()

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


_LOCKS: dict[str, ProcessLock] = {}
"""One `ProcessLock` per lock file path, created on first use."""

_LOCKS_GUARD = threading.Lock()
"""Guards lookup-or-create in `_LOCKS`, so one lock file never gets two objects."""


def project_lock(
    root: Path, name: str, *, local: threading.RLock | None = None
) -> ProcessLock:
    """The process's one `ProcessLock` named `name` for the project at `root`.

    Keyed by `paths.project_lock_path`, which resolves `root`, so every spelling
    of one repository shares one object (and so re-enters instead of taking a
    second flock). `local=None` accepts whichever in-process lock the path was
    registered with; an explicit `local` other than the registered one raises
    `ValueError`.
    """
    path = paths.project_lock_path(root, name)
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = ProcessLock(path, local=local)
            _LOCKS[key] = lock
        elif local is not None and local is not lock._local:
            raise ValueError(
                f"{path}: already registered with a different in-process lock"
            )
        return lock
```

Notes for the implementer:
- `release` follows the spec's order exactly: `LOCK_UN`, close the descriptor, remove from the thread's held set, then release `local` (at every nesting level, even if unlocking raised).
- The `min(..., remaining)` in `_flock` keeps the final wait from overshooting the deadline; the back-off itself is exactly `min(0.05 * 2**n, 0.5)` with `n` capped at 4 (Review Focus 1).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_locks.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: whole suite green (the default `-m "not e2e"` selection from `pyproject.toml`), and no "DATA-DIR GUARD FAILED" line from `tests/conftest.py`.

- [ ] **Step 6: Commit**

```bash
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-add-processlock-and-the-c5e616c2 add src/agent_manager/locks.py tests/test_locks.py
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-add-processlock-and-the-c5e616c2 commit -m "feat(locks): process-wide reentrant locks on files beside the project database"
```
