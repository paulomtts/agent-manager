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
