# Serialize `git worktree add` per repository — subtask 88150552

Parent story 19b21273 "Serialize the shared git and board resources" (milestone cdbfa10d). This subtask narrows decision P3 of `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` (lines 58-64) to the git half: "`git worktree add` runs under a per-repository lock." The board lock, the rollup critical section, the `brd` stress test and any `board._run` retry belong to sibling daf14164 and are out of scope here.

## Scope

Only `src/agent_manager/steps/worktree.py` and `tests/steps/test_worktree.py` change. `board.py` and `rollup.py` are not touched.

- A private per-repository lock registry in `worktree.py`: a module-level `dict` mapping the resolved repository path (`Path(repo_path).resolve()`, as a string) to a `threading.Lock`, plus one module-level guard `threading.Lock`. Locks are created lazily; lookup-or-create happens under the guard, so two threads can never create two different locks for the same repository. Two spellings of the same repository (trailing slash, `..`, symlink) resolve to the same key and therefore the same lock.
- `ensure` holds that repository's lock around the worktree-creation step only. The initial unlocked reads stay unlocked and in their current order: `for-each-ref`, `worktree list --porcelain`, and the `origin/<base>` probe. `_commit_count` after creation stays unlocked.
- Same-path race handling: when the unlocked read found the worktree not registered, then inside the lock `ensure` re-runs `worktree list --porcelain` and checks registration again before adding. If the path is now registered (another thread created it in between), it skips the add and reports `created=False` and `worktree_existed=True`. Otherwise it runs the same `worktree add` argv it runs today and reports `created=True`. When the unlocked read already found the worktree registered, no lock is taken and nothing changes from today.
- The signature stays `ensure(branch, base, worktree, repo_dir, git_runner=run_git)`, and so does the return dict's key set (`branch`, `worktree`, `branch_existed`, `worktree_existed`, `created`, `commit_count`). Both are consumed by `workflow/registry.py:259`, `workflow/builtin/task.yaml:7` and `engine.py`.
- No `git worktree prune` is added anywhere. Prune is a global sweep and could remove another lane's worktree that is not yet populated. The module's promise (it never resets, deletes, cleans, commits or pushes) is kept unchanged.
- In-process threads only. Two `am` processes on one repository are not supported, and there is no file lock or cross-process mechanism.

## Observable behavior

- Sequential callers (`--max-concurrent 1`, and every existing test) see the same results as today. The only difference on the create path is one extra `worktree list --porcelain` call inside the lock. Existing index-based assertions still hold: the origin probe is still `calls[2]`, and no `add` runs for an existing worktree.
- N threads ensuring distinct branches at distinct paths in one repository all succeed, because git never runs two `worktree add` commands on that repository at once.
- Two threads ensuring the same branch at the same path both succeed. Exactly one reports `created=True`.
- Different repositories get different locks and never block each other.

## Error paths

- A failing `worktree add` raises `GitError` from inside the lock, as it does today. The lock is released no matter what (context manager), so a failed lane never wedges other lanes on that repository.
- Argument validation (`_required_name` / `_required_absolute`) still raises before any git call or lock acquisition. `test_bad_arguments_raise_before_any_git_invocation` stays green.
- A same-branch/different-path collision still surfaces git's own error. This subtask does not handle it.

## Tests

All tests below go in the **Steps tier**, in `tests/steps/test_worktree.py`. The test-placement rule is design spec §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:488`): "Steps -- against temporary git repositories and a temporary brd board; no network". These tests run against a real `tmp_path` git repo, reuse the existing `repo` fixture (line 47) and the `_git`/`_commit` helpers, and carry the `requires_git` marker. They are not unit tests with a fake runner, and they are not e2e.

1. **Eight distinct lanes in parallel** (Steps tier). Eight threads start together behind a `threading.Barrier(8)`. Each calls `ensure` with its own branch and its own `tmp_path` worktree path, all against `repo` with base `main`. Assert:
   - every thread returns without an exception and with `created=True`;
   - `git worktree list --porcelain` shows 9 entries (8 plus the main checkout);
   - walking `repo/.git` finds no `*.lock` file;
   - in each worktree, `rev-parse --abbrev-ref HEAD` equals its requested branch;
   - in each worktree, `merge-base HEAD main` equals `main`'s commit, and so does `HEAD` (the branch was cut from the requested base).
2. **Same branch, same path, two threads** (Steps tier). Two threads behind a `threading.Barrier(2)` call `ensure` with the identical branch, base and worktree path. Assert that both return without an exception, that exactly one result has `created=True` and the other `created=False`, and that the worktree is registered exactly once.
3. **Existing guarantees stay green** (Steps tier, existing tests unchanged). These are `test_an_existing_worktree_add_is_never_attempted_a_second_time` (line 266), `test_the_origin_probe_is_recorded_and_its_failure_is_not_an_error` (line 507) and `test_no_forbidden_git_operation_runs_on_any_path` (lines 525-572, forbidden tokens: reset, clean, commit, push, prune).

Acceptance also requires the whole default `uv run pytest` suite, including `tests/e2e`, to pass.
