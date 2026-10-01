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
