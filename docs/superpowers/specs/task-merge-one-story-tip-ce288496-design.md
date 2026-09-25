# Merge one story tip into the integration branch (card ce288496)

Subtask of story 9b04dd11 "The merge step: merge a tip, and judge a merge with git" (milestone db5b5a3b). Narrows decisions I1 and I2 of `docs/superpowers/specs/2026-09-25-integrate-design.md` to one function and its tests.

## Scope

Deliver:

- NEW `src/agent_manager/steps/integrate.py` with `merge_tip(repo_dir, worktree, integration_branch, base_branch, tip, git_runner=run_git) -> dict` and a typed `MergeInProgressError` (defined in this module, subclass of `RuntimeError`).
- NEW `tests/steps/test_integrate.py`.

Nothing else changes. Out of scope for this card (owned by sibling 9c6741b0 or later cards): `measure_merge`, `merge_completed_gate`, any `workflow/registry.py` change or `engine.bind_arguments` binding, the `resolver` role, `integrate.yaml`, prompt input-table changes. Out of scope for the milestone: `--no-integrate`, milestone-aware `am resume`, watch/retry/cancel, cost capture, the reviewer's Plan-Hash brief, marking slow tests, per-story readiness.

## Conventions

- Reuse from `steps/worktree.py`: `run_git`, `GitRunner`, `GitError` (`argv`, `exit_code`, `message`), `worktree_paths`, and `ensure`. Do not reimplement branch/worktree creation or base resolution.
- `git_runner` takes an argv without the leading `git`, returns stdout, raises `GitError` on non-zero exit. Argv lists only, no shell strings.
- Return a plain dict (no Pydantic: it crosses no process boundary, per CLAUDE.md). Module docstring in the style of `worktree.py`.

## Observable behaviour

1. Validation at the door. A blank or non-string `tip` raises `ValueError` in `merge_tip`. Blank branch names and non-absolute `worktree` / `repo_dir` raise `ValueError` through `worktree.ensure`'s own checks (merge_tip may call the same validators first so nothing runs before validation fails).
2. In-progress guard, before anything else touches git state. If `worktree` is already a registered worktree of `repo_dir` (per `git -C <repo> worktree list --porcelain` and `worktree_paths`), run `git -C <wt> rev-parse --verify --quiet MERGE_HEAD`. Exit 0 means a merge is in progress: raise `MergeInProgressError` whose message contains the phrase "never resolved" and says an earlier conflict was never resolved and a human must finish it (resolve the conflict and commit in `<wt>`, then relaunch). `GitError` with `exit_code == 1` means absent: continue. Any other `GitError` re-raises. If the worktree is not registered yet, there is no merge to guard and the check is skipped, so the guard does not need the branch to exist.
3. Branch and worktree. Call `worktree.ensure(integration_branch, base_branch, worktree, repo_dir, git_runner)`. It cuts a fresh branch from `origin/<base>` when that exists, else local `<base>`; reuses an existing branch without `-b`; reuses an existing worktree with no re-add; re-adds a removed worktree for an existing branch without `-b`. `created` in the result is `ensure(...)["created"]`.
4. Already merged. Run `git -C <wt> merge-base --is-ancestor <tip> HEAD`. Exit 0: the tip is already contained; do not run `merge`; return `already_merged=True`, `merged=tip`, `conflict=False`. Exit 1: not contained, continue. Any other `GitError` (for example a bad ref, exit 128) re-raises. Calling twice with the same tip is stable: the second call is the same no-op and HEAD does not move.
5. Merge. Run `git -C <wt> merge --no-ff <tip>`. On success return `merged=tip`, `already_merged=False`, `conflict=False`. As a fallback, if merge output says "Already up to date", treat it as step 4's no-op.
6. Merge failure. Run `git -C <wt> diff --name-only --diff-filter=U`. If it lists files, return `conflict=True`, `files=[...]` (in git's order), `detail` = first line of the merge error message, `merged=None`, and leave the merge in progress (`MERGE_HEAD` set, markers in the tree). If it lists no files, re-raise the original merge `GitError`.
7. Forbidden: `merge --abort`, `reset`, `checkout -f`, `clean`, `commit`, `push`, and any command that moves or writes the base branch.

Result dict, every key always present: `created: bool`, `conflict: bool`, `files: list[str]` (empty unless conflict), `merged: str | None` (the tip when merged or already merged, else `None`), `already_merged: bool`, `detail: str` (empty unless conflict).

## Tests

Placement rule: design spec `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 puts `src/agent_manager/steps/*` in the **Steps tier**: real temporary git repos, no network, no mocks. Every test below is Steps tier, in `tests/steps/test_integrate.py` (mirrors `src/agent_manager/steps/integrate.py`). No unit-only, Adapter, Engine or end-to-end tests, and no fake claude (there is no agent). Follow `tests/steps/test_worktree.py`: a `requires_git` skipif marker, helpers `_git(cwd, *args)`, `_head`, `_commit`, `_init_repo` (`git init -b main`, `user.email`, `user.name`), and a module docstring stating the placement. A shared assertion helper checks that the `main` tip sha (and `origin/main` where an origin exists) is unchanged after every scenario, and that nothing was pushed to the origin.

Ported from `leave-me-alone/.../integrate.test.mjs` (all Steps tier):

1. Argument validation: blank tip, blank branch/base, relative worktree or repo_dir each raise `ValueError`, and no branch or worktree is created.
2. Fresh branch with an origin: cut from `origin/main` (origin ahead of local main); assert the branch's start commit equals `origin/main` and `created` is True.
3. Existing branch and worktree reused: second call has `created` False, no re-add, branch HEAD keeps prior commits.
4. Clean merge: `conflict` False, `merged == tip`, `already_merged` False, a merge commit with two parents exists on the integration branch.
5. Conflict: two tips edit the same line of `a.js`; second merge gives `conflict` True, `files == ['a.js']`, non-empty `detail`, `MERGE_HEAD` present, conflict markers in `a.js`.
6. Call while a merge is in progress raises `MergeInProgressError` (message contains "never resolved", per behaviour 2), and `MERGE_HEAD` and the tree are untouched.
7. Branch exists but its worktree was removed (`git worktree remove`): re-added without `-b`, prior branch commits kept, merge proceeds.
8. Non-conflict failure (nonexistent tip ref) raises `GitError`, not a conflict result; no `MERGE_HEAD`.

New cases (all Steps tier):

9. Already-merged tip is a no-op: `already_merged` True, `merged == tip`, HEAD unchanged; a third call is identical (stable).
10. Repo with no origin: branch is cut from local `main`; assert start commit equals `main`.
11. Base branch tip unchanged after every scenario above (via the shared helper, asserted in each test).

## Verification

```bash
uv run pytest
```

The whole default suite, including `tests/e2e`, must stay green. There is no lint or typecheck command.
