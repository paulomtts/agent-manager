<!-- task-pipeline: validated -->
# Build a merged base from clean merges (card 06bf46bb)

Subtask of story f7b2edd1 "Merged bases" (milestone c2a981a3). Plan: `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 2.1. Design: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §5. Sibling Task 2.2 (card 8fe30578, "Resolve base conflicts with the Integrate resolver") extends this module and is out of scope here.

## Scope

Create `src/agent_manager/bases.py`, a plain async module (not a pygents Agent, no `grafo` import) that builds a multi-blocker story's merged base branch by cutting it from the first blocker tip, merging every other tip in cleanly, and verifying the result once. Add `tests/test_bases.py`.

## Interface (verbatim from the plan; Task 2.2 depends on this exact shape)

- `@dataclass(frozen=True) class BaseResult: branch: str; merged: list[str]; already_merged: list[str]; resolved: list[str]`. Field names and order are fixed. `resolved` is always `[]` in this task.
- `class BaseFailed(Exception)` with attributes `.detail: str` and `.stopped: bool`. `str(exc)` carries the detail. Every failure in this task has `stopped=False`.
- `async def build(root: RootPlan, tips: list[str], *, repo_dir, commands, allow_no_verification, store, run_id, story_id, runner_factory, stop) -> BaseResult`.
  - `root` is a `dag.RootPlan` of kind `"merged"`; `root.branch` is already `<prefix>/base-<short id of story>` (built by `dag.base_branch_name`), so `build` uses `root.branch` and does not re-derive it. `RootPlan` exists in this worktree's `dag.py` (landed by the Groundwork story); use it, do not define another.
  - `tips` are the blockers' tip refs, already in `root.blockers` order.
  - `store`, `run_id`, `story_id`, `runner_factory`, `stop` are accepted and unused in this task; they exist so Task 2.2 does not change the call site.

## Observable behavior

1. Worktree location: `<repo_dir>/.claude/worktrees/<root.branch>` (absolute), i.e. the convention of `cli.worktree_for` (`src/agent_manager/cli.py:165`). There is no worktree helper in `paths.py`. Reuse `cli.worktree_for` or its convention; do not introduce an import cycle (orchestrate will import bases later).
2. Cut `root.branch` from `tips[0]` with `steps.worktree.ensure` (via `asyncio.to_thread`). If the branch/worktree already exists it is reused (resume/relaunch).
3. For each `tip` in `tips[1:]`, call `steps.integrate.merge_tip(repo_dir, worktree, root.branch, tips[0], tip, git_runner=run_git)` via `asyncio.to_thread`:
   - clean merge: append `tip` to `merged`;
   - `already_merged`: append `tip` to `already_merged` and continue;
   - `conflict`: raise `BaseFailed` whose detail contains `conflict` and `resolver not wired` (and names the tip), `stopped=False`. The in-progress merge is left as `merge_tip` leaves it (never aborted). Task 2.2 replaces this branch.
   - `MergeInProgressError`: raise `BaseFailed(detail, stopped=False)` whose detail says an earlier conflict in the worktree was never resolved and a human must finish the merge there, then resume (the error's own message already says "never resolved").
4. Verify once, mirroring `integration._final_verification` (`src/agent_manager/integration.py:100-120`): `reducers.verification_gate(commands, bool(allow_no_verification), True)` first (an empty suite is judged before anything runs); if commands is non-empty, `verify.run_suite(commands, worktree)` via `asyncio.to_thread`, judged by `reducers.verification_passed_gate`. Any failure raises `BaseFailed(detail, stopped=False)` naming the worktree/reason.
5. Return `BaseResult(branch=root.branch, merged=..., already_merged=..., resolved=[])`.
6. Every git and verify call goes through `asyncio.to_thread`. Nothing is pushed; the milestone's base branch (e.g. `master`) is never checked out, moved, or written.

## Error paths

- Missing tip ref (e.g. a deleted local branch `m7/gone`), whether it is `tips[0]` (the cut) or a later tip (merge): raise `BaseFailed` whose message names the missing ref, `stopped=False`. No raw `GitError` escapes for this case, and nothing hangs.
- Conflict: `BaseFailed("conflict ... resolver not wired")`, as above.
- Merge already in progress in the base worktree: `BaseFailed` saying it was "never resolved", as above.
- Verification failure or a disallowed empty suite: `BaseFailed`, `stopped=False`.

## Tests: `tests/test_bases.py`

Tier: **Steps**. Every test goes here, per design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md` lines 501-516): steps run against temporary git repos in `tmp_path`, with no network and no mocks of git, following the precedent in `tests/steps/test_integrate.py:1-9`. There is no `tests/conftest.py`. Port or adapt the repo helpers from `tests/steps/test_integrate.py` (`_init_repo`, `_make_tip`, `_git`, `_head`, `_commit`) and add the new helpers the plan names: a `two_story_repo` fixture (master plus tips `m7/a`, `m7/b`), `MASTER_BEFORE` (master's sha recorded at setup), `is_ancestor(repo, a, b)`, and `rev(repo, ref)`. Tests are `async def` (`asyncio_mode = "auto"`), and none of them sleep. Every test asserts `rev(repo, "master") == MASTER_BEFORE`.

1. `test_two_clean_tips_merge_into_the_base`: `RootPlan("merged", "m7/base-cccccccc", ("A","B"))` with tips `["m7/a","m7/b"]` and `commands=["true"]`. Expect `branch == "m7/base-cccccccc"`, `merged == ["m7/b"]`, `already_merged == []`, `resolved == []`, and both tips are ancestors of the base.
2. `test_building_twice_merges_nothing_the_second_time`: a second `build` call on the same repo gives `merged == []`, `already_merged == ["m7/b"]`, and the base sha is unchanged.
3. `test_a_tip_already_inside_the_other_is_already_merged` (Review Focus 4): B is stacked on A, and tips are ordered so that `tips[0]` is B's tip and A's tip follows. Expect `already_merged == [A's tip]`, `merged == []`, and the base equals B's tip.
4. `test_a_missing_tip_fails_naming_the_ref` (Review Focus 5): `pytest.raises(bases.BaseFailed, match="m7/gone")` with `stopped is False`, covering a missing later tip. Also cover a missing `tips[0]`, either with a parametrize or a second case.
5. `test_a_failing_verify_fails_the_base`: `commands=["false"]` raises `BaseFailed` with `stopped is False`.
6. `test_an_empty_suite_is_judged_before_running`: `commands=[]` with `allow_no_verification=False` raises `BaseFailed`. With `True`, the build succeeds.
7. `test_a_merge_in_progress_fails_for_a_human`: `MERGE_HEAD` is left in the base worktree, which raises `BaseFailed` matching "never resolved" with `stopped is False`.
8. `test_a_conflict_is_not_resolved_yet`: conflicting A/B tips raise `BaseFailed` matching `conflict.*resolver not wired`, with `stopped is False`.

The whole default suite (`uv run pytest`, including `tests/e2e`) must stay green.

## Out of scope

The following are out of scope: conflict resolution through the Integrate resolver, the synthetic `bases` story, `BASES_STORY_ID`/`BASES_STORY_TITLE`, populating `resolved`, and `stopped=True` (all Task 2.2). Also out: wiring `build` into `orchestrate`/`supervise` (Story 3), verification discovery, live pause/cancel/watch/retry, more than one `am` process per repo, a grafo `max_workers` option, and leave-me-alone multi-blocker support.

---

# Build a Merged Base from Clean Merges Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/bases.py`, whose `async build(...)` cuts a multi-blocker story's merged base from its first blocker tip, merges every other tip in with `merge_tip`, verifies once, and reports a `BaseResult` (or raises `BaseFailed`).

**Architecture:** A plain async module (no grafo, not a pygents Agent) composed from existing deterministic steps: `steps.worktree.ensure` for the cut, `steps.integrate.merge_tip` for each further tip, and the same verification sequence as `integration._final_verification`. Every git and verify call runs through `asyncio.to_thread`. The worktree lives where `cli.worktree_for(repo_dir, root.branch)` says. Conflicts fail with "resolver not wired"; Task 2.2 replaces that branch.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (`asyncio_mode = "auto"`), real temporary git repositories via the `git` CLI.

**Spec:** `docs/superpowers/specs/task-build-a-merged-base-06bf46bb-design.md` (prepended above). Parent plan: `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 2.1. Design: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §5.

## Global Constraints

- Only `orchestrate.py` imports grafo; `bases.py` must not (no line starting `import grafo` / `from grafo`).
- `bases.build` is a plain `async def`, not a pygents Agent; every git and verify call goes through `asyncio.to_thread`.
- `BaseResult` fields are exactly `branch, merged, already_merged, resolved` in that order, frozen dataclass; `BaseFailed` has `.detail: str` and `.stopped: bool`; every failure here has `stopped=False`.
- `build(root, tips, *, repo_dir, commands, allow_no_verification, store, run_id, story_id, runner_factory, stop)` — all parameters after `tips` keyword-only; the last five are accepted and unused.
- Use `dag.RootPlan` from `src/agent_manager/dag.py:254-269`; do not define another.
- Worktree location is `cli.worktree_for(repo_dir, root.branch)` (`src/agent_manager/cli.py:165-172`), i.e. `<repo_dir>/.claude/worktrees/<branch segments>`. `paths.py` has no worktree helper.
- The milestone's base branch (`master` in the tests) is never checked out, moved or written; nothing is pushed; `merge --abort` is never run.
- Tests live in `tests/test_bases.py` (Steps tier, design §14): real temporary git repos in `tmp_path`, no git mocks, no sleeps, every test asserts `rev(repo, "master") == MASTER_BEFORE`.
- Verification: `uv run pytest` (whole default suite, including `tests/e2e`) must be green at the end.

## Review Focus

1. A base worktree that was removed after a killed run while its branch survives: the next `build` must re-add the worktree on the existing branch, merge nothing again, and leave the base sha unchanged. Test: `test_a_removed_base_worktree_is_re_added_and_nothing_is_re_merged` (Task 1).
2. A tip given as a commit sha rather than a branch name: it is merged and reported exactly as given. Test: `test_a_tip_given_as_a_sha_is_merged_and_reported_as_given` (Task 1).
3. An empty `tips` list (a caller bug): refused with a `ValueError` naming the base branch before any git runs, and no branch is created. Test: `test_no_tips_is_refused_before_any_git` (Task 2).
4. A conflict must be left in progress (MERGE_HEAD set, markers in the tree, base head unmoved) so Task 2.2's resolver or a human can work from it. Asserted inside `test_a_conflict_is_not_resolved_yet` (Task 2).
5. Verification must run inside the base worktree, not the repo's own checkout. Test: `test_verification_runs_in_the_base_worktree` (Task 3).

## File Structure

- Create `src/agent_manager/bases.py` — `BaseResult`, `BaseFailed`, `build` and two private helpers (`_missing_tips`, `_verify`). One responsibility: build a merged base.
- Create `tests/test_bases.py` — Steps-tier tests with helpers ported from `tests/steps/test_integrate.py:35-102` plus `rev`, `is_ancestor`, `two_story_repo`, `MASTER_BEFORE`.

No existing file is modified.

Test location: the Steps tier's convention is "tests mirror src" (CLAUDE.md). `tests/steps/` holds tests for `src/agent_manager/steps/*`; top-level modules that compose steps keep their Steps-tier tests at the top level (`src/agent_manager/integration.py` -> `tests/test_integration.py`). `bases.py` is a top-level module, so its tests go in `tests/test_bases.py`, which is also the path the spec and parent plan name.

---

### Task 1: The interface and the clean-merge path

**Files:**
- Create: `src/agent_manager/bases.py`
- Test: `tests/test_bases.py`

**Interfaces:**
- Consumes: `dag.RootPlan(kind, branch, blockers)` (`src/agent_manager/dag.py:254-269`); `cli.worktree_for(repo_dir: Path, branch: str) -> Path`; `steps.worktree.ensure(branch, base, worktree, repo_dir, git_runner=run_git) -> dict`; `steps.integrate.merge_tip(repo_dir, worktree, integration_branch, base_branch, tip, git_runner=run_git) -> dict` with keys `created, conflict, files, merged, already_merged, detail`.
- Produces: `bases.BaseResult(branch: str, merged: list[str], already_merged: list[str], resolved: list[str])` (frozen); `bases.BaseFailed(detail: str, *, stopped: bool = False)` with `.detail`, `.stopped`; `async bases.build(root, tips, *, repo_dir, commands, allow_no_verification, store, run_id, story_id, runner_factory, stop) -> BaseResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bases.py`:

```python
"""Behaviour of `bases.build` (supervisor-tree plan Task 2.1, card 06bf46bb).

Placement follows design §14: `bases.py` wraps the worktree, merge and verify
steps, so it is a Steps component and is exercised against real temporary git
repositories created in `tmp_path` -- no network and no mocks of git. The repo
helpers are ported from `tests/steps/test_integrate.py` (there is no
`tests/conftest.py`). Every scenario asserts the milestone's base branch,
`master`, never moves. No test sleeps.
"""

import dataclasses
import inspect
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager import bases
from agent_manager.dag import RootPlan

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the bases steps-tier tests",
)

BASE = "m7/base-cccccccc"
ROOT = RootPlan("merged", BASE, ("A", "B"))


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def rev(repo: Path, ref: str) -> str:
    """The sha `ref` resolves to in `repo`."""
    return _git(repo, "rev-parse", ref).strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    """Whether git says `ancestor` is contained in `descendant`."""
    completed = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _commit(cwd: Path, name: str, body: str) -> str:
    (Path(cwd) / name).write_text(body)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", f"add {name}")
    return rev(cwd, "HEAD")


def _init_repo(root: Path) -> Path:
    """A real git repo at `root` on `master` holding README.md and shared.txt."""
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "master", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, "README.md", "base\n")
    _commit(root, "shared.txt", "shared line\n")
    return root.resolve()


def _make_tip(
    repo: Path,
    tmp_path: Path,
    branch: str,
    files: dict[str, str],
    start: str = "master",
) -> str:
    """Cut `branch` from `start` in a throwaway worktree, commit `files`, return its sha.

    The throwaway worktree is removed again, so the tip exists only as a
    branch -- what a finished story leaves behind -- and master's checkout is
    never touched.
    """
    scratch = tmp_path / f"scratch-{branch.replace('/', '-')}"
    _git(repo, "worktree", "add", str(scratch), "-b", branch, start)
    for name, body in files.items():
        (scratch / name).write_text(body)
        _git(scratch, "add", name)
    _git(scratch, "commit", "-m", f"work on {branch}")
    sha = rev(scratch, "HEAD")
    _git(repo, "worktree", "remove", str(scratch))
    return sha


def base_worktree(repo: Path) -> Path:
    """Where `cli.worktree_for` puts the base branch's worktree."""
    return repo / ".claude" / "worktrees" / "m7" / "base-cccccccc"


def _merge_head(wt: Path) -> str | None:
    """MERGE_HEAD's sha when a merge is in progress in `wt`, else None."""
    completed = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


async def _build(
    repo: Path,
    tips: list[str],
    *,
    root: RootPlan = ROOT,
    commands: tuple[str, ...] | list[str] = ("true",),
    allow_no_verification: bool = False,
) -> bases.BaseResult:
    """`bases.build` with the Task 2.2-only parameters left empty."""
    return await bases.build(
        root,
        list(tips),
        repo_dir=repo,
        commands=list(commands),
        allow_no_verification=allow_no_verification,
        store=None,
        run_id=None,
        story_id=None,
        runner_factory=None,
        stop=None,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `master`, isolated in tmp_path, with no origin."""
    return _init_repo(tmp_path / "repo")


@pytest.fixture
def MASTER_BEFORE(repo: Path) -> str:
    """Master's sha, recorded at setup, before any build runs."""
    return rev(repo, "master")


@pytest.fixture
def two_story_repo(repo: Path, tmp_path: Path) -> Path:
    """`repo` plus two independent story tips: m7/a adds a.txt, m7/b adds b.txt."""
    _make_tip(repo, tmp_path, "m7/a", {"a.txt": "from story a\n"})
    _make_tip(repo, tmp_path, "m7/b", {"b.txt": "from story b\n"})
    return repo


def test_the_result_and_failure_shapes_are_the_plans():
    assert [field.name for field in dataclasses.fields(bases.BaseResult)] == [
        "branch",
        "merged",
        "already_merged",
        "resolved",
    ]
    result = bases.BaseResult(branch=BASE, merged=[], already_merged=[], resolved=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.branch = "other"  # type: ignore[misc]
    failed = bases.BaseFailed("why")
    assert isinstance(failed, Exception)
    assert (failed.detail, failed.stopped, str(failed)) == ("why", False, "why")
    assert bases.BaseFailed("why", stopped=True).stopped is True


def test_build_is_a_plain_coroutine_that_does_not_import_grafo():
    assert inspect.iscoroutinefunction(bases.build)
    source = Path(bases.__file__).read_text()
    assert not any(
        line.startswith(("import grafo", "from grafo")) for line in source.splitlines()
    )
    params = inspect.signature(bases.build).parameters
    assert list(params) == [
        "root",
        "tips",
        "repo_dir",
        "commands",
        "allow_no_verification",
        "store",
        "run_id",
        "story_id",
        "runner_factory",
        "stop",
    ]
    assert all(
        params[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in list(params)[2:]
    )


@requires_git
async def test_two_clean_tips_merge_into_the_base(two_story_repo: Path, MASTER_BEFORE: str):
    repo = two_story_repo

    result = await _build(repo, ["m7/a", "m7/b"], commands=["true"])

    assert result == bases.BaseResult(
        branch=BASE, merged=["m7/b"], already_merged=[], resolved=[]
    )
    assert result.branch == "m7/base-cccccccc"
    assert is_ancestor(repo, "m7/a", BASE) and is_ancestor(repo, "m7/b", BASE)
    wt = base_worktree(repo)
    assert wt.is_dir()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == BASE
    assert _merge_head(wt) is None
    assert _git(repo, "symbolic-ref", "HEAD").strip() == "refs/heads/master"
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_building_twice_merges_nothing_the_second_time(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo
    await _build(repo, ["m7/a", "m7/b"])
    first = rev(repo, BASE)

    second = await _build(repo, ["m7/a", "m7/b"])

    assert second == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/b"], resolved=[]
    )
    assert rev(repo, BASE) == first
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_tip_already_inside_the_other_is_already_merged(
    repo: Path, tmp_path: Path, MASTER_BEFORE: str
):
    # B is stacked on A, so B's tip already contains A's.
    _make_tip(repo, tmp_path, "m7/a", {"a.txt": "from story a\n"})
    _make_tip(repo, tmp_path, "m7/b", {"b.txt": "from story b\n"}, start="m7/a")

    result = await _build(repo, ["m7/b", "m7/a"])

    assert result == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/a"], resolved=[]
    )
    assert rev(repo, BASE) == rev(repo, "m7/b")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_removed_base_worktree_is_re_added_and_nothing_is_re_merged(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo
    await _build(repo, ["m7/a", "m7/b"])
    built = rev(repo, BASE)
    _git(repo, "worktree", "remove", str(base_worktree(repo)))

    result = await _build(repo, ["m7/a", "m7/b"])

    assert result.merged == []
    assert result.already_merged == ["m7/b"]
    assert base_worktree(repo).is_dir()
    assert rev(repo, BASE) == built
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_tip_given_as_a_sha_is_merged_and_reported_as_given(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo
    sha_b = rev(repo, "m7/b")

    result = await _build(repo, ["m7/a", sha_b])

    assert result.merged == [sha_b]
    assert result.already_merged == []
    assert is_ancestor(repo, sha_b, BASE)
    assert rev(repo, "master") == MASTER_BEFORE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bases.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'agent_manager.bases'` (or `ImportError: cannot import name 'bases'`).

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/bases.py`:

```python
"""Build a multi-blocker story's merged base from its blockers' tips.

Supervisor-tree design §5 (`docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`),
plan Task 2.1 (card 06bf46bb). A story blocked by two or more in-milestone
stories roots on `dag.base_branch_name`, a branch this module builds: it is
cut from the first blocker's tip with `worktree.ensure`, every other tip is
merged in with `steps.integrate.merge_tip`, and the result is verified once
the way `integration._final_verification` verifies the integrated branch.

A plain async function, not a pygents Agent (milestone rule 2), and grafo is
not imported here. Every git and verify call runs in a thread through
`asyncio.to_thread`, so a lane awaiting its base never blocks the loop.

Task 2.2 (card 8fe30578) wires conflicts to the Integrate resolver and is the
first user of `store`, `run_id`, `story_id`, `runner_factory` and `stop`,
which `build` already accepts so its call site never changes.

Nothing is pushed, and the milestone's base branch is never checked out,
merged into or moved: every git write is `ensure`'s or `merge_tip`'s, in the
base worktree `cli.worktree_for` names. The outcome is internal state, so it
is a plain dataclass (CLAUDE.md).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_manager import cli
from agent_manager.dag import RootPlan
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps.integrate import merge_tip
from agent_manager.steps.worktree import ensure, run_git
from agent_manager.store import Store


@dataclass(frozen=True)
class BaseResult:
    """The merged base is built and verified.

    `merged` and `already_merged` list the tips after the first, in the order
    given; `resolved` lists the tips a resolver fixed (always empty until
    Task 2.2).
    """

    branch: str
    merged: list[str]
    already_merged: list[str]
    resolved: list[str]


class BaseFailed(Exception):
    """The merged base could not be built. A human reads `detail`.

    `stopped` is True only when a stop signal cut the build short (Task 2.2);
    every failure a clean-merge build raises is a real failure.
    """

    def __init__(self, detail: str, *, stopped: bool = False) -> None:
        self.detail = detail
        self.stopped = stopped
        super().__init__(detail)


async def build(
    root: RootPlan,
    tips: list[str],
    *,
    repo_dir: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    store: Store | None,
    run_id: str | None,
    story_id: str | None,
    runner_factory: cli.RunnerFactory | None,
    stop: StopSignal | None,
) -> BaseResult:
    """Cut `root.branch` from `tips[0]`, merge every other tip, verify once.

    `tips` are the blockers' tips in `root.blockers` order. An existing branch
    or worktree is reused, and a tip already inside the base is reported as
    `already_merged`, so a relaunch re-merges nothing. `store`, `run_id`,
    `story_id`, `runner_factory` and `stop` are unused until Task 2.2.
    """
    tips = list(tips)
    repo = Path(repo_dir).resolve()
    worktree = cli.worktree_for(repo, root.branch)
    await asyncio.to_thread(ensure, root.branch, tips[0], worktree, repo)

    merged: list[str] = []
    already_merged: list[str] = []
    for tip in tips[1:]:
        result = await asyncio.to_thread(
            merge_tip, repo, worktree, root.branch, tips[0], tip, git_runner=run_git
        )
        if result["already_merged"]:
            already_merged.append(tip)
        else:
            merged.append(tip)

    return BaseResult(
        branch=root.branch, merged=merged, already_merged=already_merged, resolved=[]
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bases.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/bases.py tests/test_bases.py
git commit -m "feat(bases): cut a merged base and merge clean tips into it"
```

---

### Task 2: Failure paths — missing tips, no tips, conflicts, a merge left in progress

**Files:**
- Modify: `src/agent_manager/bases.py` (whole file replaced below)
- Test: `tests/test_bases.py` (append)

**Interfaces:**
- Consumes: Task 1's `BaseResult`, `BaseFailed`, `build`, and test helpers `_build`, `_make_tip`, `_git`, `rev`, `base_worktree`, `_merge_head`, fixtures `repo`, `two_story_repo`, `MASTER_BEFORE`; `steps.integrate.MergeInProgressError`; `steps.integrate._ref_exists(git_runner, worktree_path: str, ref: str) -> bool` (`src/agent_manager/steps/integrate.py:205-217`, exits-1-means-absent probe).
- Produces: `build` raises `ValueError` (empty tips, message names `root.branch`), `BaseFailed` naming each missing ref, `BaseFailed` containing `conflict` … `resolver not wired` (names tip and files), and `BaseFailed` wrapping `MergeInProgressError` ("never resolved"). All with `stopped=False`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bases.py`:

```python
@pytest.fixture
def conflicting_repo(repo: Path, tmp_path: Path) -> Path:
    """`repo` plus m7/a and m7/b, both rewriting shared.txt's one line."""
    _make_tip(repo, tmp_path, "m7/a", {"shared.txt": "from story a\n"})
    _make_tip(repo, tmp_path, "m7/b", {"shared.txt": "from story b\n"})
    return repo


@requires_git
@pytest.mark.parametrize(
    "tips",
    [["m7/a", "m7/gone"], ["m7/gone", "m7/b"]],
    ids=["missing-later-tip", "missing-first-tip"],
)
async def test_a_missing_tip_fails_naming_the_ref(
    two_story_repo: Path, tmp_path: Path, MASTER_BEFORE: str, tips: list[str]
):
    repo = two_story_repo
    # A tip deleted after an earlier run finished its story.
    _make_tip(repo, tmp_path, "m7/gone", {"gone.txt": "deleted later\n"})
    _git(repo, "branch", "-D", "m7/gone")

    with pytest.raises(bases.BaseFailed, match="m7/gone") as excinfo:
        await _build(repo, tips)

    assert excinfo.value.stopped is False
    assert "m7/gone" in excinfo.value.detail
    assert _git(repo, "branch", "--list", BASE).strip() == ""
    assert not base_worktree(repo).exists()
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_no_tips_is_refused_before_any_git(two_story_repo: Path, MASTER_BEFORE: str):
    repo = two_story_repo

    with pytest.raises(ValueError, match=BASE):
        await _build(repo, [])

    assert _git(repo, "branch", "--list", BASE).strip() == ""
    assert not base_worktree(repo).exists()
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_conflict_is_not_resolved_yet(conflicting_repo: Path, MASTER_BEFORE: str):
    repo = conflicting_repo

    with pytest.raises(bases.BaseFailed, match="conflict.*resolver not wired") as excinfo:
        await _build(repo, ["m7/a", "m7/b"])

    assert excinfo.value.stopped is False
    assert "m7/b" in excinfo.value.detail
    assert "shared.txt" in excinfo.value.detail
    # Left in progress for Task 2.2's resolver or a human: never aborted.
    wt = base_worktree(repo)
    assert _merge_head(wt) == rev(repo, "m7/b")
    assert "<<<<<<< " in (wt / "shared.txt").read_text()
    assert rev(repo, BASE) == rev(repo, "m7/a")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_merge_in_progress_fails_for_a_human(
    two_story_repo: Path, tmp_path: Path, MASTER_BEFORE: str
):
    repo = two_story_repo
    _make_tip(repo, tmp_path, "m7/c", {"c.txt": "from story c\n"})
    await _build(repo, ["m7/a", "m7/b"])
    wt = base_worktree(repo)
    head = rev(repo, BASE)
    # A merge someone started in the base worktree and never finished.
    _git(wt, "merge", "--no-ff", "--no-commit", "m7/c")
    assert _merge_head(wt) == rev(repo, "m7/c")

    with pytest.raises(bases.BaseFailed, match="never resolved") as excinfo:
        await _build(repo, ["m7/a", "m7/b"])

    assert excinfo.value.stopped is False
    assert BASE in excinfo.value.detail
    assert str(wt) in excinfo.value.detail
    assert _merge_head(wt) == rev(repo, "m7/c")
    assert rev(repo, BASE) == head
    assert rev(repo, "master") == MASTER_BEFORE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bases.py -v -k "missing_tip or no_tips or conflict or merge_in_progress"`
Expected: 5 FAIL — the two missing-tip cases raise a raw `GitError` instead of `BaseFailed`; `test_no_tips_is_refused_before_any_git` raises `IndexError`; `test_a_conflict_is_not_resolved_yet` fails with "DID NOT RAISE" (the conflict is counted as merged); `test_a_merge_in_progress_fails_for_a_human` raises a raw `MergeInProgressError`.

- [ ] **Step 3: Implement the failure paths**

Replace the whole of `src/agent_manager/bases.py` with:

```python
"""Build a multi-blocker story's merged base from its blockers' tips.

Supervisor-tree design §5 (`docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`),
plan Task 2.1 (card 06bf46bb). A story blocked by two or more in-milestone
stories roots on `dag.base_branch_name`, a branch this module builds: it is
cut from the first blocker's tip with `worktree.ensure`, every other tip is
merged in with `steps.integrate.merge_tip`, and the result is verified once
the way `integration._final_verification` verifies the integrated branch.

A plain async function, not a pygents Agent (milestone rule 2), and grafo is
not imported here. Every git and verify call runs in a thread through
`asyncio.to_thread`, so a lane awaiting its base never blocks the loop.

A conflicting merge is left in progress, exactly as `merge_tip` leaves it,
and fails the base with "resolver not wired": Task 2.2 (card 8fe30578)
replaces that branch with the Integrate resolver and is the first user of
`store`, `run_id`, `story_id`, `runner_factory` and `stop`, which `build`
already accepts so its call site never changes.

Nothing is pushed, and the milestone's base branch is never checked out,
merged into or moved: every git write is `ensure`'s or `merge_tip`'s, in the
base worktree `cli.worktree_for` names. The outcome is internal state, so it
is a plain dataclass (CLAUDE.md).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_manager import cli
from agent_manager.dag import RootPlan
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps.integrate import MergeInProgressError, _ref_exists, merge_tip
from agent_manager.steps.worktree import ensure, run_git
from agent_manager.store import Store


@dataclass(frozen=True)
class BaseResult:
    """The merged base is built and verified.

    `merged` and `already_merged` list the tips after the first, in the order
    given; `resolved` lists the tips a resolver fixed (always empty until
    Task 2.2).
    """

    branch: str
    merged: list[str]
    already_merged: list[str]
    resolved: list[str]


class BaseFailed(Exception):
    """The merged base could not be built. A human reads `detail`.

    `stopped` is True only when a stop signal cut the build short (Task 2.2);
    every failure a clean-merge build raises is a real failure.
    """

    def __init__(self, detail: str, *, stopped: bool = False) -> None:
        self.detail = detail
        self.stopped = stopped
        super().__init__(detail)


def _missing_tips(repo: Path, tips: list[str]) -> list[str]:
    """The tips that do not resolve in `repo`, in the order given.

    Asked before anything is cut or merged, so a deleted blocker branch fails
    the base by name instead of surfacing as a raw git error mid-build.
    """
    return [tip for tip in tips if not _ref_exists(run_git, str(repo), tip)]


async def build(
    root: RootPlan,
    tips: list[str],
    *,
    repo_dir: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    store: Store | None,
    run_id: str | None,
    story_id: str | None,
    runner_factory: cli.RunnerFactory | None,
    stop: StopSignal | None,
) -> BaseResult:
    """Cut `root.branch` from `tips[0]`, merge every other tip, verify once.

    `tips` are the blockers' tips in `root.blockers` order. An existing branch
    or worktree is reused, and a tip already inside the base is reported as
    `already_merged`, so a relaunch re-merges nothing. `store`, `run_id`,
    `story_id`, `runner_factory` and `stop` are unused until Task 2.2.
    """
    tips = list(tips)
    if not tips:
        raise ValueError(
            f"bases.build needs at least one blocker tip to build {root.branch}, got none"
        )
    repo = Path(repo_dir).resolve()
    worktree = cli.worktree_for(repo, root.branch)

    missing = await asyncio.to_thread(_missing_tips, repo, tips)
    if missing:
        raise BaseFailed(
            f"cannot build the merged base {root.branch}: blocker tip "
            f"{', '.join(missing)} does not exist in {repo}"
        )

    await asyncio.to_thread(ensure, root.branch, tips[0], worktree, repo)

    merged: list[str] = []
    already_merged: list[str] = []
    for tip in tips[1:]:
        try:
            result = await asyncio.to_thread(
                merge_tip, repo, worktree, root.branch, tips[0], tip, git_runner=run_git
            )
        except MergeInProgressError as error:
            # The error already says the earlier conflict was never resolved
            # and a human must finish it in the worktree.
            raise BaseFailed(
                f"cannot build the merged base {root.branch}: {error}"
            ) from error
        if result["conflict"]:
            files = ", ".join(str(name) for name in result["files"])
            raise BaseFailed(
                f"conflict merging {tip} into the merged base {root.branch} "
                f"({files}): resolver not wired, so the merge is left in progress "
                f"in {worktree} for a human"
            )
        if result["already_merged"]:
            already_merged.append(tip)
        else:
            merged.append(tip)

    return BaseResult(
        branch=root.branch, merged=merged, already_merged=already_merged, resolved=[]
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bases.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/bases.py tests/test_bases.py
git commit -m "feat(bases): fail a merged base on missing tips, conflicts and unfinished merges"
```

---

### Task 3: Verify the base once

**Files:**
- Modify: `src/agent_manager/bases.py` (whole file replaced below)
- Test: `tests/test_bases.py` (append)

**Interfaces:**
- Consumes: Task 2's `build`, `BaseFailed`, `_missing_tips`; test helpers `_build`, `rev`, `is_ancestor`, `base_worktree`, fixtures `two_story_repo`, `MASTER_BEFORE`; `reducers.verification_gate(suite_cmds, allow_no_verification, caller_provided) -> dict | None` (`src/agent_manager/steps/reducers.py:30-59`, empty-suite detail starts "no full-suite command is available"); `verify.run_suite(commands, worktree) -> dict` (`src/agent_manager/steps/verify.py:259`); `reducers.verification_passed_gate(result) -> dict | None` (`src/agent_manager/steps/reducers.py:322-343`).
- Produces: `build` raises `BaseFailed` with "has no verification" (empty suite disallowed) or "failed its verification in <worktree>" (red suite), `stopped=False`; verification runs with the base worktree as cwd.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bases.py`:

```python
@requires_git
async def test_a_failing_verify_fails_the_base(two_story_repo: Path, MASTER_BEFORE: str):
    repo = two_story_repo

    with pytest.raises(bases.BaseFailed, match="failed its verification") as excinfo:
        await _build(repo, ["m7/a", "m7/b"], commands=["false"])

    assert excinfo.value.stopped is False
    assert BASE in excinfo.value.detail
    assert str(base_worktree(repo)) in excinfo.value.detail
    # The merges stand; only the verdict failed.
    assert is_ancestor(repo, "m7/b", BASE)
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_an_empty_suite_is_judged_before_running(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo

    with pytest.raises(bases.BaseFailed, match="no full-suite command") as excinfo:
        await _build(repo, ["m7/a", "m7/b"], commands=[], allow_no_verification=False)
    assert excinfo.value.stopped is False

    result = await _build(repo, ["m7/a", "m7/b"], commands=[], allow_no_verification=True)

    assert result == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/b"], resolved=[]
    )
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_verification_runs_in_the_base_worktree(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo

    await _build(
        repo,
        ["m7/a", "m7/b"],
        commands=["test -f a.txt", "test -f b.txt", "touch verified-here.txt"],
    )

    assert (base_worktree(repo) / "verified-here.txt").is_file()
    assert not (repo / "verified-here.txt").exists()
    assert rev(repo, "master") == MASTER_BEFORE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bases.py -v -k "verify or verification or empty_suite"`
Expected: 3 FAIL — the first two with "DID NOT RAISE" (nothing verifies yet), the third on `assert (base_worktree(repo) / "verified-here.txt").is_file()` (no command ran).

- [ ] **Step 3: Implement verification**

Replace the whole of `src/agent_manager/bases.py` with:

```python
"""Build a multi-blocker story's merged base from its blockers' tips.

Supervisor-tree design §5 (`docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`),
plan Task 2.1 (card 06bf46bb). A story blocked by two or more in-milestone
stories roots on `dag.base_branch_name`, a branch this module builds: it is
cut from the first blocker's tip with `worktree.ensure`, every other tip is
merged in with `steps.integrate.merge_tip`, and the result is verified once
the way `integration._final_verification` verifies the integrated branch.

A plain async function, not a pygents Agent (milestone rule 2), and grafo is
not imported here. Every git and verify call runs in a thread through
`asyncio.to_thread`, so a lane awaiting its base never blocks the loop.

A conflicting merge is left in progress, exactly as `merge_tip` leaves it,
and fails the base with "resolver not wired": Task 2.2 (card 8fe30578)
replaces that branch with the Integrate resolver and is the first user of
`store`, `run_id`, `story_id`, `runner_factory` and `stop`, which `build`
already accepts so its call site never changes.

Nothing is pushed, and the milestone's base branch is never checked out,
merged into or moved: every git write is `ensure`'s or `merge_tip`'s, in the
base worktree `cli.worktree_for` names. The outcome is internal state, so it
is a plain dataclass (CLAUDE.md).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_manager import cli
from agent_manager.dag import RootPlan
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps import reducers, verify
from agent_manager.steps.integrate import MergeInProgressError, _ref_exists, merge_tip
from agent_manager.steps.worktree import ensure, run_git
from agent_manager.store import Store


@dataclass(frozen=True)
class BaseResult:
    """The merged base is built and verified.

    `merged` and `already_merged` list the tips after the first, in the order
    given; `resolved` lists the tips a resolver fixed (always empty until
    Task 2.2).
    """

    branch: str
    merged: list[str]
    already_merged: list[str]
    resolved: list[str]


class BaseFailed(Exception):
    """The merged base could not be built. A human reads `detail`.

    `stopped` is True only when a stop signal cut the build short (Task 2.2);
    every failure a clean-merge build raises is a real failure.
    """

    def __init__(self, detail: str, *, stopped: bool = False) -> None:
        self.detail = detail
        self.stopped = stopped
        super().__init__(detail)


def _missing_tips(repo: Path, tips: list[str]) -> list[str]:
    """The tips that do not resolve in `repo`, in the order given.

    Asked before anything is cut or merged, so a deleted blocker branch fails
    the base by name instead of surfacing as a raw git error mid-build.
    """
    return [tip for tip in tips if not _ref_exists(run_git, str(repo), tip)]


def _verify(
    commands: list[str], allow_no_verification: bool, branch: str, worktree: Path
) -> str | None:
    """`None` when the base is verified or opted out, else the reason.

    Mirrors `integration._final_verification`: an empty suite is judged by
    `verification_gate` first, because running zero commands reports
    `passed: True` and `verification_passed_gate` would wave it through.
    `bool()` matches `cli.gate_context`: the reducer tests `is True`.
    """
    missing = reducers.verification_gate(commands, bool(allow_no_verification), True)
    if missing is not None:
        return f"the merged base {branch} has no verification: {missing['detail']}"
    if not commands:
        return None
    verdict = reducers.verification_passed_gate(verify.run_suite(commands, worktree))
    if verdict is None:
        return None
    return (
        f"the merged base {branch} failed its verification in {worktree}: "
        f"{verdict['detail']}"
    )


async def build(
    root: RootPlan,
    tips: list[str],
    *,
    repo_dir: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    store: Store | None,
    run_id: str | None,
    story_id: str | None,
    runner_factory: cli.RunnerFactory | None,
    stop: StopSignal | None,
) -> BaseResult:
    """Cut `root.branch` from `tips[0]`, merge every other tip, verify once.

    `tips` are the blockers' tips in `root.blockers` order. An existing branch
    or worktree is reused, and a tip already inside the base is reported as
    `already_merged`, so a relaunch re-merges nothing. `store`, `run_id`,
    `story_id`, `runner_factory` and `stop` are unused until Task 2.2.
    """
    tips = list(tips)
    if not tips:
        raise ValueError(
            f"bases.build needs at least one blocker tip to build {root.branch}, got none"
        )
    repo = Path(repo_dir).resolve()
    worktree = cli.worktree_for(repo, root.branch)
    suite = list(commands)

    missing = await asyncio.to_thread(_missing_tips, repo, tips)
    if missing:
        raise BaseFailed(
            f"cannot build the merged base {root.branch}: blocker tip "
            f"{', '.join(missing)} does not exist in {repo}"
        )

    await asyncio.to_thread(ensure, root.branch, tips[0], worktree, repo)

    merged: list[str] = []
    already_merged: list[str] = []
    for tip in tips[1:]:
        try:
            result = await asyncio.to_thread(
                merge_tip, repo, worktree, root.branch, tips[0], tip, git_runner=run_git
            )
        except MergeInProgressError as error:
            # The error already says the earlier conflict was never resolved
            # and a human must finish it in the worktree.
            raise BaseFailed(
                f"cannot build the merged base {root.branch}: {error}"
            ) from error
        if result["conflict"]:
            files = ", ".join(str(name) for name in result["files"])
            raise BaseFailed(
                f"conflict merging {tip} into the merged base {root.branch} "
                f"({files}): resolver not wired, so the merge is left in progress "
                f"in {worktree} for a human"
            )
        if result["already_merged"]:
            already_merged.append(tip)
        else:
            merged.append(tip)

    failure = await asyncio.to_thread(
        _verify, suite, allow_no_verification, root.branch, worktree
    )
    if failure is not None:
        raise BaseFailed(failure)

    return BaseResult(
        branch=root.branch, merged=merged, already_merged=already_merged, resolved=[]
    )
```

- [ ] **Step 4: Run the module's tests to verify they pass**

Run: `uv run pytest tests/test_bases.py -v`
Expected: 15 passed.

- [ ] **Step 5: Run the whole default suite**

Run: `uv run pytest`
Expected: all tests pass (e2e-marked tests are deselected by `addopts`; everything else under `tests/`, including `tests/e2e`'s non-marked tests, is green). If anything outside `tests/test_bases.py` fails, stop and investigate — this task modifies no existing file.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/bases.py tests/test_bases.py
git commit -m "feat(bases): build a merged base from clean merges"
```

---

## Spec coverage map

| Spec item | Task / test |
| --- | --- |
| `BaseResult` shape, `BaseFailed(.detail, .stopped)` | Task 1 `test_the_result_and_failure_shapes_are_the_plans` |
| `build` signature, keyword-only, plain coroutine, no grafo | Task 1 `test_build_is_a_plain_coroutine_that_does_not_import_grafo` |
| Behavior 1 worktree location | Task 1 `test_two_clean_tips_merge_into_the_base` (`base_worktree`) |
| Behavior 2 cut from `tips[0]`, reuse | Task 1 tests 1, 2, removed-worktree |
| Behavior 3 clean / already_merged | Task 1 tests 1, 2, stacked, sha |
| Behavior 3 conflict | Task 2 `test_a_conflict_is_not_resolved_yet` |
| Behavior 3 `MergeInProgressError` | Task 2 `test_a_merge_in_progress_fails_for_a_human` |
| Behavior 4 verify (gate first, run_suite, passed gate) | Task 3 all three tests |
| Behavior 5 result, `resolved == []` | Task 1 tests 1, 2, stacked; Task 3 empty-suite |
| Behavior 6 master untouched, nothing pushed | every test asserts `rev(repo, "master") == MASTER_BEFORE` |
| Missing tip in either position | Task 2 `test_a_missing_tip_fails_naming_the_ref` (parametrized) |
| Out of scope (resolver, `stopped=True`, orchestrate wiring) | not implemented; conflict raises "resolver not wired" |
