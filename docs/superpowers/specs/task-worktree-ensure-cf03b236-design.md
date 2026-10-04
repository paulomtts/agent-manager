# Subtask cf03b236 — `worktree.ensure` recreates a stale-registered-but-missing path

Parent: ab65eee1 ("A resume survives a worktree deleted outside am's bookkeeping"), milestone 246a77c3. Narrows §3.3 and §4 items 7-10 of `docs/superpowers/specs/2026-10-03-resume-worktree-reensure-design.md` to one function.

## Scope

Only `src/agent_manager/steps/worktree.py` (`ensure`) and its tests: new and extended tests in `tests/steps/test_worktree.py`, plus one new test in `tests/test_bases.py` that exercises the shared seam. No edits to `bases.py`, `runtime/engine.py`, `walk.py`, `cli.py`, `orchestrate.py`, the README, or any other test file. Those belong to siblings f76af5b2 (engine resume re-ensure/decline) and dd932306 (`SubtaskSummary.resumed_at`).

## Behavior

`ensure` gets one new rule: a registered worktree path that is not a directory on disk counts as not existing.

- `worktree_existed = _is_registered(path, registered) and Path(path).is_dir()`. The same rule applies at the unlocked initial check and again at the re-check under `git_lock(repo_path)`, so the double-checked locking against racing lanes keeps working. The add itself stays inside the lock.
- When the re-check under the lock sees a stale registration (registered, but no directory), the add carries `-f`:
  - branch survives: `["-C", repo, "worktree", "add", "-f", path, branch]`. This checks out the existing branch and never re-cuts it from base, so the commits of a hand-`rm -rf`'d worktree survive.
  - branch also gone: `["-C", repo, "worktree", "add", "-f", path, "-b", branch, resolved_base]`.
- `-f` is passed only in the stale-registration case. A clean add (path not registered at all) uses exactly today's argv. That way git still refuses when the same branch is checked out live at another path, and that error still surfaces as `GitError` with the lock released.
- A registered path whose directory exists keeps today's behavior: `worktree_existed=True`, `created=False`, no add, no lock taken.
- The result dict keeps exactly its current keys (`branch`, `worktree`, `branch_existed`, `worktree_existed`, `created`, `commit_count`). In the recovery case it reports `worktree_existed=False, created=True`. No key is added, and callers read nothing new.

## Invariants (unchanged, must still hold)

- This step never runs `worktree prune`, `worktree remove`, `reset`, `checkout -f`, `clean`, `commit`, or `push`. `-f` on `worktree add` is not on that list. It is git's documented override for a "missing but already registered" path, and it overrides bookkeeping only, never branch content.
- It stays deterministic: no model calls and no board access.
- It returns a plain dict, not Pydantic, because the result crosses no process boundary.
- Every `worktree add` runs serialized under `git_lock(repo_path)`.

## Error paths

- Any git failure on the `-f` add propagates as `GitError` and releases the lock, the same as the existing add.
- Under `-f`, a branch checked out live elsewhere is not a concern for the stale case: the only other registration of the branch is the dead one being replaced.

## Tests

Placement rule (CLAUDE.md): a test's tier is set by what it spawns, not by its directory. Every test below spawns real `git` in `tmp_path` and nothing else (no `brd`, no `claude`), so all of them are **`git` tier**. In `tests/steps/` the conftest auto-marks them `git`, which matches. In `tests/test_bases.py` the test needs an explicit `@pytest.mark.git`, as its neighbours already have.

1. `test_worktree.py`, new (git tier): **`rm -rf`'d worktree, branch survives.** `ensure` once, commit on the branch, `shutil.rmtree` the worktree dir, then `ensure` again through a recording runner. Expect `branch_existed=True, worktree_existed=False, created=True`. Also expect: the directory exists, `git -C <wt> log` shows the commit (not re-cut), the recorded `worktree add` argv contains `-f` and no `-b`, and `git worktree list --porcelain` has no `prunable` line afterwards.
2. `test_worktree.py`, new (git tier): **`rm -rf`'d worktree, branch also deleted.** Same setup, then `git update-ref -d refs/heads/<branch>`. The test also asserts that `git branch -D` is refused while the stale registration stands, which explains why `update-ref` is used. Expect `branch_existed=False, created=True`, argv with both `-f` and `-b`, and the branch re-cut from base (`commit_count == 0`).
3. `test_worktree.py`, new (git tier): **cleanly removed worktree** (`git worktree remove` + `git branch -D`). Expect the plain `-b` add with no `-f` in any argv. This pins the existing behavior.
4. `test_worktree.py`, extend `test_no_forbidden_git_operation_runs_on_any_path` (git tier): additionally assert that no recorded argv contains `-f` across the fresh-add, existing-branch-resume and second-identical-call paths. The existing forbidden-token assertions stay as they are.
5. `tests/test_bases.py`, new, `@pytest.mark.git`: **merged-base worktree recovered through `bases.build`.** Build once so the merged-base worktree exists, `shutil.rmtree` it, then build again. The resolver at `bases.py:293` calls the same `ensure` and recreates the worktree with the merged-base branch intact. `bases.py` gets zero code changes.
