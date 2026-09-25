<!-- task-pipeline: validated -->
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

---

# Merge one story tip into the integration branch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `merge_tip` to a new `src/agent_manager/steps/integrate.py`. It merges one story tip into the integration branch's worktree with `--no-ff`, is a stable no-op for a tip that is already merged, leaves a conflict in progress and reports its files, and refuses to start while an earlier merge is still unresolved.

**Architecture:** `merge_tip` validates every argument before running any git command. It then checks for an unfinished merge (`MERGE_HEAD`), but only when the worktree is already registered. Next it delegates branch and worktree creation to `worktree.ensure`, asks git whether the tip is already an ancestor of HEAD, and only then runs `git merge --no-ff --no-edit <tip>`. On a merge failure, `git diff --name-only --diff-filter=U` decides the outcome: if it lists files, the result is a conflict; if it lists none, the original `GitError` is re-raised. The function returns a plain six-key dict.

**Tech Stack:** Python 3 stdlib (`subprocess`, `pathlib`), pytest, the real `git` CLI, `uv`.

**Spec:** `docs/superpowers/specs/task-merge-one-story-tip-ce288496-design.md` (included word for word above).

All commands run from the worktree root: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-merge-one-story-tip-ce288496` (branch `m5/task-merge-one-story-tip-ce288496`, cut fresh from master). Do not assume any other m5 subtask's code exists on this branch. In particular, `measure_merge`, `merge_completed_gate` and any `registry.py` change belong to sibling card 9c6741b0, which must not be started here.

## Global Constraints

- Only two files change: NEW `src/agent_manager/steps/integrate.py` and NEW `tests/steps/test_integrate.py`. Do not modify `workflow/registry.py`, `engine.py`, `steps/worktree.py`, any YAML file, or any prompt.
- Signature, exactly: `merge_tip(repo_dir, worktree, integration_branch, base_branch, tip, git_runner=run_git) -> dict[str, object]`.
- The result dict always has exactly these keys: `created: bool`, `conflict: bool`, `files: list[str]`, `merged: str | None`, `already_merged: bool`, `detail: str`.
- `MergeInProgressError` is defined in `steps/integrate.py` and subclasses `RuntimeError`. Its message contains "never resolved" and "already in progress", and names the worktree.
- Reuse `run_git`, `GitRunner`, `GitError`, `worktree_paths` and `ensure` from `agent_manager.steps.worktree`. Do not reimplement base resolution or worktree creation.
- Every git call is an argv list without the leading `git`, and every call goes through `git_runner`. No shell strings.
- Never run `merge --abort`, `reset`, `checkout -f`, `clean`, `commit`, `push` or `prune`, and never move or write the base branch. The tests assert this.
- Tests belong to the Steps tier (design §14), in `tests/steps/test_integrate.py`. They use real `git init` repos in `tmp_path`, no network and no mocks. Every git-touching test carries `@requires_git`, and every scenario asserts that the base state is unchanged.
- Verification: `uv run pytest`. The whole default suite must pass, including `tests/e2e`. There is no lint or typecheck command.

## Review Focus

1. **The tip is given as a raw commit sha, not a branch name.** Expected: it merges the same way, and `merged` is the sha string exactly as passed. The test lives in Task 1: `test_a_tip_given_as_a_commit_sha_is_merged_and_reported_as_given`.
2. **A conflict spans several files.** Expected: `files` lists every unmerged path in git's order, not only the first. The test lives in Task 3: `test_a_conflict_across_several_files_lists_them_all`.
3. **The integration worktree has uncommitted edits that the tip would overwrite.** Expected: git refuses the merge, `GitError` is raised rather than a conflict result, and the edits survive untouched. The test lives in Task 3: `test_local_edits_the_merge_would_overwrite_raise_and_survive`.
4. **A human resolved and committed the conflict, then relaunched with the same tips.** Expected: the guard lets the call through, and each tip is an already-merged no-op with no new merge commit. The test lives in Task 4: `test_a_conflict_a_human_resolved_and_committed_is_merged_on_relaunch`.
5. **The worktree path is spelled differently, with a trailing `/./`, while a merge is in progress.** Expected: the path still counts as registered and the call is refused. The test lives in Task 4: `test_a_differently_spelled_worktree_path_is_still_refused_mid_merge`.

## File Structure

- `src/agent_manager/steps/integrate.py` (NEW): `MergeInProgressError`, `merge_tip`, and private helpers `_required_tip`, `_result`, `_already_contains`, `_says_already_up_to_date`, `_unmerged_files`, `_first_line` and `_refuse_unfinished_merge`. The file has one job: one merge attempt in one worktree.
- `tests/steps/test_integrate.py` (NEW): Steps-tier tests with the helpers `_git`, `_head`, `_commit`, `_init_repo`, `_make_tip`, `_merge`, `_base_state`, `_recorder`, `_is_add`, `_parents` and `_merge_head`, plus the fixtures `repo`, `wt` and `repo_with_origin`.

`merge_tip` imports three private helpers from `steps/worktree.py`: `_required_name`, `_required_absolute` and `_is_registered`. The spec allows `merge_tip` to call "the same validators first", and `_is_registered` gives the in-progress guard exactly the same registration test that `ensure` uses (plain match, then a realpath fallback). If the guard used a weaker test, a trailing slash could slip past it.

---

### Task 1: Validation, branch and worktree via `ensure`, and a clean `--no-ff` merge

**Files:**
- Create: `src/agent_manager/steps/integrate.py`
- Test: `tests/steps/test_integrate.py` (new)

**Interfaces:**
- Consumes: `agent_manager.steps.worktree`: `ensure(branch, base, worktree, repo_dir, git_runner=run_git) -> dict[str, object]` (key `"created": bool`), `run_git(argv: list[str]) -> str`, `GitRunner = Callable[[list[str]], str]`, `GitError(message, *, argv, exit_code=None)`, `_required_name(value, field) -> str`, `_required_absolute(value, field) -> str`.
- Produces: `merge_tip(repo_dir: str | Path, worktree: str | Path, integration_branch: str, base_branch: str, tip: str, git_runner: GitRunner = run_git) -> dict[str, object]`, `class MergeInProgressError(RuntimeError)` with `__init__(self, worktree: str)` and attribute `.worktree`, `_result(*, created, conflict=False, files=(), merged=None, already_merged=False, detail="") -> dict[str, object]`. Also the test helpers listed under File Structure, which later tasks reuse.

- [ ] **Step 1: Write the failing tests**

Create `tests/steps/test_integrate.py`:

```python
"""Behaviour of the Integrate merge step (Integrate addendum I1/I2, card ce288496).

Placement follows design §14: `integrate.py` is a Steps component, so its
behaviour is exercised against real temporary git repositories created with
`git init` / `git worktree` in `tmp_path` -- no network, no mocks, and no
faking of git except where a test must force an answer git itself would only
give in a race. Every scenario also asserts the base branch (and `origin`,
where one exists) is untouched: Integrate never writes the base and never
pushes.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager.steps.integrate import MergeInProgressError, merge_tip
from agent_manager.steps.worktree import GitError, run_git

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the integrate step's steps-tier tests",
)

BRANCH = "m5-integrate"


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _head(cwd: Path) -> str:
    return _git(cwd, "rev-parse", "HEAD").strip()


def _commit(cwd: Path, name: str, body: str) -> str:
    (Path(cwd) / name).write_text(body)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", f"add {name}")
    return _head(cwd)


def _init_repo(root: Path) -> Path:
    """A real git repo at `root` on `main` holding README.md, a.js and b.js."""
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, "README.md", "base\n")
    _commit(root, "a.js", "shared line\n")
    _commit(root, "b.js", "shared line\n")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `main`, isolated in tmp_path, with no origin."""
    return _init_repo(tmp_path / "repo")


@pytest.fixture
def wt(tmp_path: Path) -> Path:
    """Where the integration worktree goes. Not created: merge_tip makes it."""
    return tmp_path / "integrate-wt"


def _make_tip(repo: Path, tmp_path: Path, branch: str, files: dict[str, str]) -> str:
    """Cut `branch` from main in a throwaway worktree, commit `files`, return its sha.

    The throwaway worktree is removed again, so the story tip exists only as a
    branch -- exactly what a finished story leaves behind -- and the main
    checkout is never touched.
    """
    scratch = tmp_path / f"scratch-{branch.replace('/', '-')}"
    _git(repo, "worktree", "add", str(scratch), "-b", branch, "main")
    for name, body in files.items():
        (scratch / name).write_text(body)
        _git(scratch, "add", name)
    _git(scratch, "commit", "-m", f"work on {branch}")
    sha = _head(scratch)
    _git(repo, "worktree", "remove", str(scratch))
    return sha


def _merge(
    repo: Path,
    wt: Path,
    tip: str,
    git_runner=run_git,
    *,
    worktree: str | None = None,
) -> dict[str, object]:
    return merge_tip(
        repo_dir=str(repo),
        worktree=str(wt) if worktree is None else worktree,
        integration_branch=BRANCH,
        base_branch="main",
        tip=tip,
        git_runner=git_runner,
    )


def _base_state(repo: Path) -> dict[str, object]:
    """Everything Integrate must never change: the base, its checkout, the origin."""
    has_origin = "origin" in _git(repo, "remote").split()
    return {
        "main": _git(repo, "rev-parse", "refs/heads/main").strip(),
        "checked_out": _git(repo, "symbolic-ref", "HEAD").strip(),
        "status": _git(repo, "status", "--porcelain"),
        "origin/main": (
            _git(repo, "rev-parse", "refs/remotes/origin/main").strip()
            if has_origin
            else None
        ),
        "origin_refs": _git(repo, "ls-remote", "origin") if has_origin else None,
    }


def _recorder(calls: list[list[str]], inner=run_git):
    """A git runner that records every argv, delegating to `inner` (or returning "")."""

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        return "" if inner is None else inner(argv)

    return runner


def _is_add(argv: list[str]) -> bool:
    return "worktree" in argv and "add" in argv


def _parents(wt: Path) -> list[str]:
    """The parents of the integration worktree's HEAD commit, in order."""
    return _git(wt, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]


def _merge_head(wt: Path) -> str | None:
    """MERGE_HEAD's sha when a merge is in progress in `wt`, else None."""
    completed = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"tip": ""}, "non-empty tip ref"),
        ({"tip": "   "}, "non-empty tip ref"),
        ({"tip": None}, "non-empty tip ref"),
        ({"integration_branch": ""}, "integration_branch"),
        ({"integration_branch": None}, "integration_branch"),
        ({"base_branch": "   "}, "base_branch"),
        ({"worktree": "relative/wt"}, "absolute path for worktree"),
        ({"repo_dir": "relative/repo"}, "absolute path for repo_dir"),
    ],
    ids=[
        "empty-tip",
        "blank-tip",
        "none-tip",
        "empty-integration-branch",
        "none-integration-branch",
        "blank-base-branch",
        "relative-worktree",
        "relative-repo-dir",
    ],
)
def test_bad_arguments_raise_before_any_git_invocation(kwargs, expected):
    calls: list[list[str]] = []
    args = {
        "repo_dir": "/abs/repo",
        "worktree": "/abs/wt",
        "integration_branch": BRANCH,
        "base_branch": "main",
        "tip": "m5/story-a",
        **kwargs,
    }

    with pytest.raises(ValueError, match=expected):
        merge_tip(**args, git_runner=_recorder(calls, inner=None))

    assert calls == []


@requires_git
def test_a_fresh_branch_is_cut_from_the_local_base_when_there_is_no_origin(
    repo: Path, wt: Path, tmp_path: Path
):
    main_sha = _head(repo)
    tip = _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    result = _merge(repo, wt, "m5/story-a")

    assert result["created"] is True
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == BRANCH
    assert _parents(wt) == [main_sha, tip]
    assert _base_state(repo) == before


@pytest.fixture
def repo_with_origin(repo: Path, tmp_path: Path) -> Path:
    """`repo` with a local bare `origin` whose main is one commit AHEAD of local main.

    No network: the origin is a bare repo in tmp_path. Origin being ahead
    makes `origin/main` and `main` disagree, so the test can tell which one
    the integration branch was cut from.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "main")
    _make_tip(repo, tmp_path, "ahead", {"remote-only.txt": "pushed elsewhere\n"})
    _git(repo, "push", "origin", "ahead:main")
    _git(repo, "branch", "-D", "ahead")
    _git(repo, "fetch", "origin")
    return repo


@requires_git
def test_a_fresh_branch_is_cut_from_origin_base_when_origin_resolves(
    repo_with_origin: Path, wt: Path, tmp_path: Path
):
    repo = repo_with_origin
    origin_main = _git(repo, "rev-parse", "origin/main").strip()
    assert origin_main != _head(repo)
    tip = _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    result = _merge(repo, wt, "m5/story-a")

    assert result["created"] is True
    assert _parents(wt) == [origin_main, tip]
    assert (wt / "remote-only.txt").is_file()
    assert _base_state(repo) == before


@requires_git
def test_a_clean_merge_reports_what_was_merged(repo: Path, wt: Path, tmp_path: Path):
    main_sha = _head(repo)
    tip = _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    result = _merge(repo, wt, "m5/story-a")

    assert result == {
        "created": True,
        "conflict": False,
        "files": [],
        "merged": "m5/story-a",
        "already_merged": False,
        "detail": "",
    }
    # --no-ff: a real merge commit with the base first and the tip second.
    assert _parents(wt) == [main_sha, tip]
    assert (wt / "a.js").read_text() == "from story a\n"
    assert _merge_head(wt) is None
    assert _base_state(repo) == before


@requires_git
def test_an_existing_branch_and_worktree_are_reused(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    tip_b = _make_tip(repo, tmp_path, "m5/story-b", {"b.js": "from story b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    prior = _head(wt)

    calls: list[list[str]] = []
    result = _merge(repo, wt, "m5/story-b", _recorder(calls))

    assert result["created"] is False
    assert result["merged"] == "m5/story-b"
    assert not any(_is_add(argv) for argv in calls)
    assert _parents(wt) == [prior, tip_b]
    assert _base_state(repo) == before


@requires_git
def test_a_removed_worktree_is_re_added_on_the_existing_branch_without_b(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    tip_b = _make_tip(repo, tmp_path, "m5/story-b", {"b.js": "from story b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    prior = _head(wt)
    _git(repo, "worktree", "remove", str(wt))

    calls: list[list[str]] = []
    result = _merge(repo, wt, "m5/story-b", _recorder(calls))

    adds = [argv for argv in calls if _is_add(argv)]
    assert adds == [["-C", str(repo), "worktree", "add", str(wt), BRANCH]]
    assert result["created"] is True
    assert result["merged"] == "m5/story-b"
    assert _parents(wt) == [prior, tip_b]
    assert (wt / "a.js").read_text() == "from story a\n"
    assert _base_state(repo) == before


@requires_git
def test_a_tip_given_as_a_commit_sha_is_merged_and_reported_as_given(
    repo: Path, wt: Path, tmp_path: Path
):
    main_sha = _head(repo)
    tip = _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    result = _merge(repo, wt, tip)

    assert result["merged"] == tip
    assert result["conflict"] is False
    assert _parents(wt) == [main_sha, tip]
    assert _base_state(repo) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_integrate.py -v`
Expected: a collection ERROR, `ModuleNotFoundError: No module named 'agent_manager.steps.integrate'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/steps/integrate.py`:

```python
"""Merge one story tip into the integration branch, leaving any conflict for a human.

A deterministic step (design §4 `steps/`, §6; Integrate addendum decisions I1
and I2): no model calls, no board access, no network. Ported from the merge
half of the leave-me-alone plugin's `scripts/integrate.mjs`, whose
`integrate.test.mjs` is the behavioural specification.

The integration branch and its worktree come from `worktree.ensure`, so base
resolution (`origin/<base>`, else local `<base>`) and reuse are not repeated
here. git judges everything git can measure: whether a merge is already in
progress (`MERGE_HEAD`), whether the tip is already merged
(`merge-base --is-ancestor`), and which files conflict
(`diff --diff-filter=U`).

A conflict is left in progress -- `MERGE_HEAD` set, markers in the tree --
because that is exactly the state a resolver needs. So this module never runs
`merge --abort`, `reset`, `checkout -f`, `clean`, `commit` or `push`, and never
writes the base branch; the tests assert all of it.

Every invocation is an argument list handed to the git runner: there is no
shell string and nothing to quote.
"""

from collections.abc import Iterable
from pathlib import Path

from agent_manager.steps.worktree import (
    GitRunner,
    _required_absolute,
    _required_name,
    ensure,
    run_git,
)


class MergeInProgressError(RuntimeError):
    """A merge is already under way in the integration worktree; a human must finish it."""

    def __init__(self, worktree: str) -> None:
        self.worktree = worktree
        super().__init__(
            f"a merge is already in progress in {worktree}: an earlier conflict "
            "was never resolved. A human must finish it -- resolve the conflict "
            f"and commit in {worktree} -- then relaunch."
        )


def _required_tip(value: object) -> str:
    """A non-blank tip ref, or `ValueError` before anything is run."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"merge_tip needs a non-empty tip ref, got {value!r}")
    return value


def _result(
    *,
    created: bool,
    conflict: bool = False,
    files: Iterable[str] = (),
    merged: str | None = None,
    already_merged: bool = False,
    detail: str = "",
) -> dict[str, object]:
    """The step's result: every key always present (spec "Result dict")."""
    return {
        "created": created,
        "conflict": conflict,
        "files": list(files),
        "merged": merged,
        "already_merged": already_merged,
        "detail": detail,
    }


def merge_tip(
    repo_dir: str | Path,
    worktree: str | Path,
    integration_branch: str,
    base_branch: str,
    tip: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Merge `tip` into `integration_branch`'s worktree with `--no-ff`, and report it.

    The return value is the deterministic phase's result -- a plain dict,
    since it crosses no process boundary and so needs no Pydantic model
    (`CLAUDE.md`).
    """
    tip = _required_tip(tip)
    integration_branch = _required_name(integration_branch, "integration_branch")
    base_branch = _required_name(base_branch, "base_branch")
    worktree_path = _required_absolute(worktree, "worktree")
    repo_path = _required_absolute(repo_dir, "repo_dir")

    ensured = ensure(integration_branch, base_branch, worktree_path, repo_path, git_runner)
    created = bool(ensured["created"])

    # --no-edit: take git's generated message; never wait on an editor.
    git_runner(["-C", worktree_path, "merge", "--no-ff", "--no-edit", tip])
    return _result(created=created, merged=tip)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_integrate.py -v`
Expected: PASS, 14 passed: 8 parametrized validation cases and 6 git scenarios.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/integrate.py tests/steps/test_integrate.py
git commit -m "feat(integrate): merge one story tip into the integration branch"
```

---

### Task 2: An already-merged tip is a stable no-op

**Files:**
- Modify: `src/agent_manager/steps/integrate.py` (the import block, new helpers above `merge_tip`, and the body of `merge_tip`)
- Test: `tests/steps/test_integrate.py` (append)

**Interfaces:**
- Consumes: `GitError` from `agent_manager.steps.worktree` (`.exit_code: int | None`). Everything Task 1 produced.
- Produces: `_already_contains(git_runner: GitRunner, worktree_path: str, tip: str) -> bool` and `_says_already_up_to_date(output: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_integrate.py`:

```python
@requires_git
def test_an_already_merged_tip_is_a_stable_no_op(repo: Path, wt: Path, tmp_path: Path):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)
    first = _merge(repo, wt, "m5/story-a")
    head = _head(wt)

    second = _merge(repo, wt, "m5/story-a")
    third = _merge(repo, wt, "m5/story-a")

    expected = {
        "created": False,
        "conflict": False,
        "files": [],
        "merged": "m5/story-a",
        "already_merged": True,
        "detail": "",
    }
    assert first["already_merged"] is False
    assert second == expected
    assert third == expected
    assert _head(wt) == head
    assert _base_state(repo) == before


@requires_git
def test_an_already_merged_tip_never_reaches_git_merge(
    repo: Path, wt: Path, tmp_path: Path
):
    # git measures containment (merge-base --is-ancestor); merge is not even tried.
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")

    calls: list[list[str]] = []
    _merge(repo, wt, "m5/story-a", _recorder(calls))

    assert any("--is-ancestor" in argv for argv in calls)
    assert not any("merge" in argv for argv in calls)
    assert _base_state(repo) == before


@requires_git
def test_git_saying_already_up_to_date_is_also_the_no_op(
    repo: Path, wt: Path, tmp_path: Path
):
    # Only a race could make the ancestry probe say "no" for a merged tip; the
    # probe is forced here so the real `git merge` prints "Already up to date."
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    head = _head(wt)

    def runner(argv: list[str]) -> str:
        if "--is-ancestor" in argv:
            raise GitError("forced: the ancestry probe answers no", argv=argv, exit_code=1)
        return run_git(argv)

    result = _merge(repo, wt, "m5/story-a", runner)

    assert result["already_merged"] is True
    assert result["merged"] == "m5/story-a"
    assert result["conflict"] is False
    assert _head(wt) == head
    assert _base_state(repo) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_integrate.py -v -k "already"`
Expected: all three FAIL. `test_an_already_merged_tip_is_a_stable_no_op` and `test_git_saying_already_up_to_date_is_also_the_no_op` fail with `assert False is True` on `already_merged`. `test_an_already_merged_tip_never_reaches_git_merge` fails on its `--is-ancestor` assertion, because Task 1 never probes ancestry.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/integrate.py`, replace the import block with:

```python
from collections.abc import Iterable
from pathlib import Path

from agent_manager.steps.worktree import (
    GitError,
    GitRunner,
    _required_absolute,
    _required_name,
    ensure,
    run_git,
)
```

Add these helpers directly above `def merge_tip(`:

```python
def _already_contains(git_runner: GitRunner, worktree_path: str, tip: str) -> bool:
    """Whether HEAD already contains `tip`, as git measures it.

    `merge-base --is-ancestor` exits 0 for yes and 1 for no. Anything else
    (a bad ref is 128) is a real failure and propagates.
    """
    try:
        git_runner(["-C", worktree_path, "merge-base", "--is-ancestor", tip, "HEAD"])
    except GitError as error:
        if error.exit_code == 1:
            return False
        raise
    return True


def _says_already_up_to_date(output: str) -> bool:
    """Whether `git merge` reported a no-op ("Already up to date." / "up-to-date")."""
    return "already up to date" in output.lower().replace("-", " ")
```

Replace the whole `merge_tip` function with:

```python
def merge_tip(
    repo_dir: str | Path,
    worktree: str | Path,
    integration_branch: str,
    base_branch: str,
    tip: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Merge `tip` into `integration_branch`'s worktree with `--no-ff`, and report it.

    A tip already contained in HEAD is a no-op, so a relaunch never
    re-merges. The return value is the deterministic phase's result -- a
    plain dict, since it crosses no process boundary and so needs no Pydantic
    model (`CLAUDE.md`).
    """
    tip = _required_tip(tip)
    integration_branch = _required_name(integration_branch, "integration_branch")
    base_branch = _required_name(base_branch, "base_branch")
    worktree_path = _required_absolute(worktree, "worktree")
    repo_path = _required_absolute(repo_dir, "repo_dir")

    ensured = ensure(integration_branch, base_branch, worktree_path, repo_path, git_runner)
    created = bool(ensured["created"])

    if _already_contains(git_runner, worktree_path, tip):
        return _result(created=created, merged=tip, already_merged=True)

    # --no-edit: take git's generated message; never wait on an editor.
    output = git_runner(["-C", worktree_path, "merge", "--no-ff", "--no-edit", tip])
    if _says_already_up_to_date(output):
        return _result(created=created, merged=tip, already_merged=True)
    return _result(created=created, merged=tip)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_integrate.py -v`
Expected: PASS, all 17 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/integrate.py tests/steps/test_integrate.py
git commit -m "feat(integrate): treat an already-merged tip as a stable no-op"
```

---

### Task 3: Leave a conflict in progress and report its files; re-raise other failures

**Files:**
- Modify: `src/agent_manager/steps/integrate.py` (new helpers above `merge_tip`, and the body of `merge_tip`)
- Test: `tests/steps/test_integrate.py` (append)

**Interfaces:**
- Consumes: `GitError.message: str`, `_already_contains` and `_says_already_up_to_date` from Task 2.
- Produces: `_unmerged_files(git_runner: GitRunner, worktree_path: str) -> list[str]` and `_first_line(text: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_integrate.py`:

```python
@requires_git
def test_a_conflict_is_reported_and_left_in_progress(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    tip_b = _make_tip(repo, tmp_path, "m5/story-b", {"a.js": "from story b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    head = _head(wt)

    calls: list[list[str]] = []
    result = _merge(repo, wt, "m5/story-b", _recorder(calls))

    assert result["conflict"] is True
    assert result["files"] == ["a.js"]
    assert result["merged"] is None
    assert result["already_merged"] is False
    assert result["created"] is False
    assert result["detail"].strip() != ""
    assert "\n" not in result["detail"]
    # Left in progress for a resolver: never aborted, never reset.
    assert _merge_head(wt) == tip_b
    assert "<<<<<<<" in (wt / "a.js").read_text()
    assert _head(wt) == head
    assert not any("--abort" in argv for argv in calls)
    assert _base_state(repo) == before


@requires_git
def test_a_conflict_across_several_files_lists_them_all(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "a from a\n", "b.js": "b from a\n"})
    _make_tip(repo, tmp_path, "m5/story-b", {"a.js": "a from b\n", "b.js": "b from b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")

    result = _merge(repo, wt, "m5/story-b")

    assert result["conflict"] is True
    assert result["files"] == ["a.js", "b.js"]
    assert _base_state(repo) == before


@requires_git
def test_a_bad_tip_ref_raises_rather_than_reporting_a_conflict(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)

    with pytest.raises(GitError) as excinfo:
        _merge(repo, wt, "m5/no-such-story")

    assert excinfo.value.exit_code not in (None, 0, 1)
    assert _merge_head(wt) is None
    assert _base_state(repo) == before


@requires_git
def test_local_edits_the_merge_would_overwrite_raise_and_survive(
    repo: Path, wt: Path, tmp_path: Path
):
    # git refuses the merge but leaves no unmerged paths: that is a failure to
    # re-raise, not a conflict to hand to a resolver.
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    _make_tip(repo, tmp_path, "m5/story-c", {"b.js": "from story c\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    head = _head(wt)
    (wt / "b.js").write_text("uncommitted edit\n")

    with pytest.raises(GitError) as excinfo:
        _merge(repo, wt, "m5/story-c")

    assert "merge" in excinfo.value.argv
    assert (wt / "b.js").read_text() == "uncommitted edit\n"
    assert _merge_head(wt) is None
    assert _head(wt) == head
    assert _base_state(repo) == before
```

- [ ] **Step 2: Run the tests to verify the conflict tests fail**

Run: `uv run pytest tests/steps/test_integrate.py -v -k "conflict or bad_tip or local_edits"`
Expected: `test_a_conflict_is_reported_and_left_in_progress` and `test_a_conflict_across_several_files_lists_them_all` FAIL with an uncaught `GitError` from `merge`. `test_a_bad_tip_ref_raises_rather_than_reporting_a_conflict` and `test_local_edits_the_merge_would_overwrite_raise_and_survive` already PASS, since Task 2's code lets every `GitError` propagate. They pin the re-raise path, so the conflict handling added next cannot swallow these errors.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/integrate.py`, add these helpers directly above `def merge_tip(`:

```python
def _unmerged_files(git_runner: GitRunner, worktree_path: str) -> list[str]:
    """The paths git holds as unmerged in `worktree_path`, in git's order."""
    out = git_runner(["-C", worktree_path, "diff", "--name-only", "--diff-filter=U"])
    return [line.strip() for line in out.split("\n") if line.strip()]


def _first_line(text: str) -> str:
    """The first non-blank line of `text`, stripped, or "" when there is none."""
    for line in text.split("\n"):
        if line.strip():
            return line.strip()
    return ""
```

Replace the whole `merge_tip` function with:

```python
def merge_tip(
    repo_dir: str | Path,
    worktree: str | Path,
    integration_branch: str,
    base_branch: str,
    tip: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Merge `tip` into `integration_branch`'s worktree with `--no-ff`, and report it.

    A tip already contained in HEAD is a no-op, so a relaunch never
    re-merges. A content conflict is left in progress and reported with its
    files; any other git failure raises. The return value is the
    deterministic phase's result -- a plain dict, since it crosses no process
    boundary and so needs no Pydantic model (`CLAUDE.md`).
    """
    tip = _required_tip(tip)
    integration_branch = _required_name(integration_branch, "integration_branch")
    base_branch = _required_name(base_branch, "base_branch")
    worktree_path = _required_absolute(worktree, "worktree")
    repo_path = _required_absolute(repo_dir, "repo_dir")

    ensured = ensure(integration_branch, base_branch, worktree_path, repo_path, git_runner)
    created = bool(ensured["created"])

    if _already_contains(git_runner, worktree_path, tip):
        return _result(created=created, merged=tip, already_merged=True)

    try:
        # --no-edit: take git's generated message; never wait on an editor.
        output = git_runner(
            ["-C", worktree_path, "merge", "--no-ff", "--no-edit", tip]
        )
    except GitError as error:
        files = _unmerged_files(git_runner, worktree_path)
        if not files:
            raise
        # Left in progress on purpose: MERGE_HEAD and the markers are what a
        # resolver works from. Never `merge --abort`.
        return _result(
            created=created,
            conflict=True,
            files=files,
            detail=_first_line(error.message),
        )

    if _says_already_up_to_date(output):
        return _result(created=created, merged=tip, already_merged=True)
    return _result(created=created, merged=tip)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_integrate.py -v`
Expected: PASS, all 21 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/integrate.py tests/steps/test_integrate.py
git commit -m "feat(integrate): leave a conflict in progress and report its files"
```

---

### Task 4: Refuse to merge on top of an unresolved merge; sweep for forbidden git operations

**Files:**
- Modify: `src/agent_manager/steps/integrate.py` (the import block, a new helper above `merge_tip`, and the body of `merge_tip`)
- Test: `tests/steps/test_integrate.py` (append)

**Interfaces:**
- Consumes: `worktree_paths(porcelain: str) -> list[str]` and `_is_registered(candidate: str, registered: list[str]) -> bool` from `agent_manager.steps.worktree`, plus `MergeInProgressError` from Task 1.
- Produces: `_refuse_unfinished_merge(git_runner: GitRunner, repo_path: str, worktree_path: str) -> None`, which raises `MergeInProgressError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_integrate.py`:

```python
def _leave_a_conflict(repo: Path, wt: Path, tmp_path: Path) -> str:
    """Merge story-a cleanly, then story-b onto the same line: return story-b's sha."""
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    tip_b = _make_tip(repo, tmp_path, "m5/story-b", {"a.js": "from story b\n"})
    _merge(repo, wt, "m5/story-a")
    conflict = _merge(repo, wt, "m5/story-b")
    assert conflict["conflict"] is True
    return tip_b


@requires_git
def test_a_merge_left_in_progress_refuses_the_next_call(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-c", {"c.txt": "from story c\n"})
    before = _base_state(repo)
    tip_b = _leave_a_conflict(repo, wt, tmp_path)
    head = _head(wt)
    status = _git(wt, "status", "--porcelain")
    marked = (wt / "a.js").read_text()

    for tip in ("m5/story-c", "m5/story-b"):
        with pytest.raises(MergeInProgressError, match="never resolved") as excinfo:
            _merge(repo, wt, tip)
        message = str(excinfo.value)
        assert "already in progress" in message
        assert str(wt) in message
        assert excinfo.value.worktree == str(wt)

    assert _merge_head(wt) == tip_b
    assert _head(wt) == head
    assert _git(wt, "status", "--porcelain") == status
    assert (wt / "a.js").read_text() == marked
    assert _base_state(repo) == before


@requires_git
def test_a_differently_spelled_worktree_path_is_still_refused_mid_merge(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    tip_b = _leave_a_conflict(repo, wt, tmp_path)

    with pytest.raises(MergeInProgressError):
        _merge(repo, wt, "m5/story-b", worktree=f"{wt}{os.sep}.{os.sep}")

    assert _merge_head(wt) == tip_b
    assert _base_state(repo) == before


@requires_git
def test_a_merge_head_probe_that_fails_otherwise_is_re_raised(
    repo: Path, wt: Path, tmp_path: Path
):
    # Exit 1 means "no MERGE_HEAD"; any other failure is not an answer and
    # must not be read as "safe to merge".
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    _make_tip(repo, tmp_path, "m5/story-b", {"b.js": "from story b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    head = _head(wt)

    def runner(argv: list[str]) -> str:
        if "MERGE_HEAD" in argv:
            raise GitError("forced: the probe itself broke", argv=argv, exit_code=128)
        return run_git(argv)

    with pytest.raises(GitError) as excinfo:
        _merge(repo, wt, "m5/story-b", runner)

    assert "MERGE_HEAD" in excinfo.value.argv
    assert _head(wt) == head
    assert _base_state(repo) == before


@requires_git
def test_a_worktree_not_yet_registered_skips_the_probe(
    repo: Path, wt: Path, tmp_path: Path
):
    # The guard does not need the integration branch to exist yet.
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    calls: list[list[str]] = []
    result = _merge(repo, wt, "m5/story-a", _recorder(calls))

    assert result["created"] is True
    assert not any("MERGE_HEAD" in argv for argv in calls)
    assert _base_state(repo) == before


@requires_git
def test_a_conflict_a_human_resolved_and_committed_is_merged_on_relaunch(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)
    (wt / "a.js").write_text("resolved by a human\n")
    _git(wt, "add", "a.js")
    _git(wt, "commit", "--no-edit")
    head = _head(wt)

    for tip in ("m5/story-a", "m5/story-b"):
        assert _merge(repo, wt, tip) == {
            "created": False,
            "conflict": False,
            "files": [],
            "merged": tip,
            "already_merged": True,
            "detail": "",
        }

    assert _head(wt) == head
    assert _merge_head(wt) is None
    assert _base_state(repo) == before


FORBIDDEN_TOKENS = ("reset", "clean", "commit", "push", "prune", "--abort")


def _assert_no_forbidden_git(calls: list[list[str]]) -> None:
    for argv in calls:
        for token in FORBIDDEN_TOKENS:
            assert token not in argv, f"forbidden git operation {token!r} in {argv!r}"
        assert not ("checkout" in argv and "-f" in argv), argv
        assert not ("worktree" in argv and "remove" in argv), argv
        assert "update-ref" not in argv, argv
        assert "branch" not in argv, argv


@requires_git
def test_no_forbidden_git_operation_runs_on_any_path(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    _make_tip(repo, tmp_path, "m5/story-b", {"a.js": "from story b\n"})
    _make_tip(repo, tmp_path, "m5/story-c", {"c.txt": "from story c\n"})
    before = _base_state(repo)
    calls: list[list[str]] = []
    runner = _recorder(calls)

    fresh = _merge(repo, wt, "m5/story-a", runner)
    again = _merge(repo, wt, "m5/story-a", runner)
    conflict = _merge(repo, wt, "m5/story-b", runner)
    with pytest.raises(MergeInProgressError):
        _merge(repo, wt, "m5/story-c", runner)

    assert fresh["created"] is True
    assert again["already_merged"] is True
    assert conflict["conflict"] is True
    _assert_no_forbidden_git(calls)
    assert _base_state(repo) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_integrate.py -v -k "refuse or refused or probe or relaunch or forbidden"`
Expected results:

- These FAIL with `DID NOT RAISE`: `test_a_merge_left_in_progress_refuses_the_next_call`, `test_a_differently_spelled_worktree_path_is_still_refused_mid_merge` and `test_no_forbidden_git_operation_runs_on_any_path`. Without a guard, the second merge fails inside git ("unmerged files"), `diff --diff-filter=U` lists `a.js`, and a conflict dict comes back instead of an error. That silent re-report is exactly what the guard prevents.
- `test_a_merge_head_probe_that_fails_otherwise_is_re_raised` also FAILS with `DID NOT RAISE`, because no probe runs yet.
- These already PASS and pin behaviour the guard must keep: `test_a_worktree_not_yet_registered_skips_the_probe` and `test_a_conflict_a_human_resolved_and_committed_is_merged_on_relaunch`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/integrate.py`, replace the import block with:

```python
from collections.abc import Iterable
from pathlib import Path

from agent_manager.steps.worktree import (
    GitError,
    GitRunner,
    _is_registered,
    _required_absolute,
    _required_name,
    ensure,
    run_git,
    worktree_paths,
)
```

Add this helper directly above `def merge_tip(`:

```python
def _refuse_unfinished_merge(
    git_runner: GitRunner, repo_path: str, worktree_path: str
) -> None:
    """Raise `MergeInProgressError` when `worktree_path` has a merge under way.

    A worktree git does not know yet cannot hold a merge, so the probe is
    skipped and the integration branch need not exist. Registration uses the
    same test as `worktree.ensure`, so a trailing slash or `.` cannot slip
    past. `rev-parse --verify --quiet` exits 1 when MERGE_HEAD is absent; any
    other failure is not an answer and propagates.
    """
    registered = worktree_paths(
        git_runner(["-C", repo_path, "worktree", "list", "--porcelain"])
    )
    if not _is_registered(worktree_path, registered):
        return
    try:
        git_runner(
            ["-C", worktree_path, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"]
        )
    except GitError as error:
        if error.exit_code == 1:
            return
        raise
    raise MergeInProgressError(worktree_path)
```

Replace the whole `merge_tip` function with its final form:

```python
def merge_tip(
    repo_dir: str | Path,
    worktree: str | Path,
    integration_branch: str,
    base_branch: str,
    tip: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Merge `tip` into `integration_branch`'s worktree with `--no-ff`, and report it.

    Refuses to start on top of an unresolved merge. A tip already contained
    in HEAD is a no-op, so a relaunch never re-merges. A content conflict is
    left in progress and reported with its files; any other git failure
    raises. The return value is the deterministic phase's result -- a plain
    dict, since it crosses no process boundary and so needs no Pydantic model
    (`CLAUDE.md`).
    """
    tip = _required_tip(tip)
    integration_branch = _required_name(integration_branch, "integration_branch")
    base_branch = _required_name(base_branch, "base_branch")
    worktree_path = _required_absolute(worktree, "worktree")
    repo_path = _required_absolute(repo_dir, "repo_dir")

    # Before anything else touches git state: never merge on top of a merge.
    _refuse_unfinished_merge(git_runner, repo_path, worktree_path)

    ensured = ensure(integration_branch, base_branch, worktree_path, repo_path, git_runner)
    created = bool(ensured["created"])

    if _already_contains(git_runner, worktree_path, tip):
        return _result(created=created, merged=tip, already_merged=True)

    try:
        # --no-edit: take git's generated message; never wait on an editor.
        output = git_runner(
            ["-C", worktree_path, "merge", "--no-ff", "--no-edit", tip]
        )
    except GitError as error:
        files = _unmerged_files(git_runner, worktree_path)
        if not files:
            raise
        # Left in progress on purpose: MERGE_HEAD and the markers are what a
        # resolver works from. Never `merge --abort`.
        return _result(
            created=created,
            conflict=True,
            files=files,
            detail=_first_line(error.message),
        )

    if _says_already_up_to_date(output):
        return _result(created=created, merged=tip, already_merged=True)
    return _result(created=created, merged=tip)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_integrate.py -v`
Expected: PASS, all 27 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/integrate.py tests/steps/test_integrate.py
git commit -m "feat(integrate): refuse to merge on top of an unresolved merge"
```

---

### Task 5: Full-suite verification

**Files:** none changed.

**Interfaces:**
- Consumes: everything above.
- Produces: nothing new.

- [ ] **Step 1: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS. Every test passes, including `tests/e2e`, `tests/steps/test_worktree.py` (which is unchanged), and the 27 tests in `tests/steps/test_integrate.py`.

- [ ] **Step 2: Confirm the scope held**

Run: `git diff --stat master...HEAD`
Expected: only `src/agent_manager/steps/integrate.py` and `tests/steps/test_integrate.py`, plus the spec and plan docs if the pipeline committed them. No `workflow/registry.py`, `engine.py`, `steps/worktree.py`, YAML or prompt changes.
