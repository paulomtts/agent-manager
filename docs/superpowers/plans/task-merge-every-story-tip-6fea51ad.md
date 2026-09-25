<!-- task-pipeline: validated -->
# Subtask 6fea51ad — Merge every story tip and verify the result

Parent story 006d0a0e "Integrate in the runner" (milestone db5b5a3b). This narrows decisions I1, I3, I4 and I5 of `docs/superpowers/specs/2026-09-25-integrate-design.md` to one new module. I2 (the merge step) and the resolver role, `ResolveResult`, `merge_completed_gate` and `workflow/builtin/integrate.yaml` already exist on this base (`src/agent_manager/steps/integrate.py`, `src/agent_manager/workflow/builtin/integrate.yaml`) and are reused unchanged.

## Scope

A new `src/agent_manager/integration.py` exposing:

```python
def integrate_milestone(
    stories, repo_dir, base_branch, branch_prefix, commands,
    allow_no_verification, store, run_id, runner_factory,
) -> IntegrateOutcome
```

- `stories`: the census `StoryPlan`s of the milestone (all of them, done or not).
- `runner_factory`: a `cli.RunnerFactory`.
- The outcome is a plain dataclass (internal state, per CLAUDE.md, so not Pydantic). It is either a success or an escalation:
  - success: `branch`, `worktree`, `merged` (story ids whose tip is in the integration branch, in merge order, already-merged ones included) and `resolved` (story ids whose conflict a resolver fixed).
  - escalation: `phase = "integrate"`, `story` (the story id involved, or `None` for the final verification), `files` (conflicting files, or empty) and `detail` (a human-readable reason).

No edits to `orchestrate.py` or `cli.py`. It only reuses `cli.worktree_for` and `cli.gate_context`. Wiring into `run_milestone`, the payload keys, the run status, the exit code, relaunch behaviour and `--dry-run` belong to sibling a74f2cd6. The e2e tests and the fake claude `resolve` phase belong to sibling a37460b9.

## Observable behaviour

1. **Order (I1).** Levels come from `dag.compute_integrate_levels(stories)`, over all stories. Within a level the order is census order. Each story's tip comes from `dag.story_tip(story, stories_by_id, branch_prefix, base_branch)`. A story with no subtasks is skipped: it is not merged and not listed in `merged`.
2. **Branch and worktree.** The integration branch is `f"{branch_prefix}-integrate"`. The worktree is `cli.worktree_for(repo_dir, branch)`, which gives `.claude/worktrees/<branch>`. Merges run one at a time in that worktree.
3. **Merging.** For each tip, call `steps.integrate.merge_tip(repo_dir, worktree, integration_branch, base_branch, tip)`. A result that is clean or `already_merged` moves on to the next tip.
4. **Conflict (I3).** When `merge_tip` reports `conflict`:
   - Record the synthetic story in the store, if it is not already recorded: `StoryRun(card_id="integrate", title="Integrate", ...)`.
   - Record one synthetic `SubtaskRun` for this story, with `card_id` = the conflicting story's id, `branch` = the integration branch, `base_branch` = the base branch and `worktree_path` = the integration worktree.
   - Both records go through `store.record_story` / `store.record_subtask` before the engine journals any phase. `store.rebuild_from_journal` rejects a phase whose story or subtask was not created by an earlier line.
   - Then load `workflow.loader.load_builtin("integrate", registry)` and call `engine.run_subtask(workflow, store, story_id="integrate", subtask=..., repo_dir=..., commands=..., extra_context=..., agent_runner=...)`. `agent_runner` is `runner_factory(workflow=..., store=..., run_id=run_id, story_id="integrate", card_id=<story id>)`. `extra_context` is `{"merge_tip": tip, "conflict_files": files}` merged with `cli.gate_context(commands, allow_no_verification)`.
   - If the subtask ends `done`, add the story id to `merged` and `resolved`, then continue. Otherwise stop and escalate with the story, the files and a detail. The branch and worktree are left exactly as they are, with `MERGE_HEAD` still in place. Integrate never aborts, resets or cleans up.
5. **Final verification (I4).** After the last tip, call `steps.verify.run_suite(commands, worktree)` on the integration worktree and judge the result with `steps.reducers.verification_passed_gate`. A blocked verdict escalates (`story=None`, with the verdict's reason as `detail`). The check is skipped only when `commands` is empty and `allow_no_verification` is set, which is the same rule as the subtask gate. Empty commands without the flag escalate through `steps.reducers.verification_gate(list(commands), allow_no_verification, True)`, checked before `run_suite` (`verification_passed_gate` alone cannot catch an empty suite, since running zero commands reports passed); its verdict's `detail` becomes the escalation detail. With commands present, `run_suite` runs and `verification_passed_gate` judges it. Pass/fail is taken from the suite's measured result, never from an agent.
6. **Already integrated.** When every tip is already contained, every `merge_tip` returns `already_merged`, no agent is dispatched and the integration branch HEAD does not move. The final verification still runs under rule 5. The outcome is a success.
7. **Safety (I5).** Integrate never checks out, merges into, resets or pushes the base branch or any story branch, and never pushes anything. Every write happens in the integration worktree.

## Error paths

- `steps.integrate.MergeInProgressError` from `merge_tip` becomes an escalation. Its `story` is the tip's story, and its `detail` is the error's own message, which tells the human to finish the merge in the worktree and relaunch. Nothing is dispatched and nothing is recorded in the store.
- A resolver subtask that does not end `done` (the agent refused, `merge_completed_gate` failed after retries, or the `verify` phase failed) escalates as in rule 4, leaving `MERGE_HEAD` in place.
- A final verification failure escalates as in rule 5.
- Any other git failure from `merge_tip` (a bad ref, an I/O error) propagates as an exception. It is not turned into an escalation.

## Tests

All of these go in `tests/test_integration.py`, which mirrors `src/agent_manager/integration.py`. Per the test-placement rule in the findings, they are ordinary default-suite tests: not under `tests/e2e/` (that folder is for production wiring through the fake claude on PATH, which is sibling a37460b9) and not marked `e2e` (that marker is the opt-in real-agent tier). Each test uses real temporary git repos (a base branch plus story branches built with `git`), a real `Store` and a fake `runner_factory` whose agent runner counts dispatches.

The fake resolver knows only what its brief tells it. It reads the conflict list and the result path from the prompt text alone, with no plan hash and no committing of a spec or plan. It resolves by editing the files and committing the merge. An env-var switch that makes it refuse is allowed as test scaffolding. Every outcome that git can measure (`MERGE_HEAD`, HEAD, branch tips, file contents) is asserted through git, not through the agent's report.

1. **No conflict dispatches no agent.** Two stories touching different files: both are merged in level/census order, the factory is never called, the outcome is a success with an empty `resolved` list, and verification ran.
2. **A conflict dispatches exactly once per conflicting tip.** Two stories editing the same line: exactly one dispatch, for the conflicting story. `resolved` holds that story. The synthetic "Integrate" story and subtask are in the store, and `store.rebuild_from_journal` replays cleanly. git shows no `MERGE_HEAD` and both tips as ancestors of the integration HEAD.
3. **A failing resolver escalates with `MERGE_HEAD` left in place.** The refusing fake leads to an escalation with `phase="integrate"`, the story and the files. git shows `MERGE_HEAD` still present and the branch/worktree still there.
4. **An already-in-progress merge escalates.** Set up the worktree with an unresolved merge beforehand. The outcome is an escalation whose `detail` says a human must finish the merge and relaunch, and there are zero dispatches.
5. **A final verification failure escalates.** Clean merges plus a failing command in `commands` give an escalation at `integrate` whose detail names the verification failure.
6. **An already-integrated milestone is a no-op.** Run once to success, record the integration HEAD, run again: zero dispatches, HEAD unchanged, success.
7. **The base branch is untouched and nothing is pushed.** In every scenario above, the base branch tip SHA is the same before and after. Tests use a repo with a bare `origin` remote where needed, and assert that `origin` has no integration branch and no moved refs.

Also required: the whole default suite, `tests/e2e` included, stays green.

---

# Merge Every Story Tip and Verify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/integration.py` with `integrate_milestone(...)`. It merges every story tip of a milestone into `<prefix>-integrate`, dispatches the `integrate.yaml` resolver once per conflicting tip, runs the suite once at the end, and returns a plain-dataclass success or escalation.

**Architecture:** One new module that only composes existing parts. `dag` gives the order and the tips, `steps.integrate.merge_tip` does every git write, `engine.run_subtask` runs the builtin `integrate.yaml` with the injected `cli.RunnerFactory`, and `steps.verify.run_suite` plus `steps.reducers` judge the final suite. The only store writes are the synthetic "Integrate" story and one synthetic subtask per conflicting story, both recorded before the engine journals a phase.

**Tech Stack:** Python 3, pytest, real `git` in `tmp_path`, `agent_manager.store.Store`, `dispatch.AgentRunner` with a fake adapter and launcher.

**Spec:** `docs/superpowers/specs/task-merge-every-story-tip-6fea51ad-design.md` (reproduced verbatim above).

## Global Constraints

- New module path: `src/agent_manager/integration.py`. Tests: `tests/test_integration.py`, in the default suite, not under `tests/e2e/`, not marked `e2e`.
- No edits to `src/agent_manager/orchestrate.py` or `src/agent_manager/cli.py`. Only `cli.worktree_for`, `cli.gate_context` and the `cli.RunnerFactory` type are reused.
- The outcome is a plain dataclass, not Pydantic (CLAUDE.md: internal state).
- Integration branch: `f"{branch_prefix}-integrate"`. Worktree: `cli.worktree_for(repo_dir, branch)`.
- Synthetic story: `StoryRun(card_id="integrate", title="Integrate", ...)`. Synthetic subtask: `card_id` = the conflicting story's id, `branch` = the integration branch, `base_branch` = the base branch, `worktree_path` = the integration worktree.
- `extra_context` = `{"merge_tip": tip, "conflict_files": files}` merged with `cli.gate_context(commands, allow_no_verification)`.
- Escalation `phase` is always `"integrate"`, and `story` is `None` for the final verification.
- Final-verification skip rule: skipped only when `commands` is empty AND `allow_no_verification` is set. Empty without the flag escalates through `reducers.verification_gate(list(commands), allow_no_verification, True)` before `run_suite`.
- `MergeInProgressError` becomes an escalation with the error's own message. Every other git failure propagates.
- Never touch the base branch or a story branch, and never push. Every test asserts this through git.
- The fake resolver learns the conflict list and the result path only from the brief text. It computes no plan hash and commits no spec or plan. Refusal is switched by the env var `AM_TEST_FAKE_RESOLVER_REFUSE=1`.
- Verification for the repo: `uv run pytest` (the whole default suite, `tests/e2e` included, stays green).

## Review Focus

- An empty `commands` list without `--allow-no-verification` must escalate instead of passing vacuously, because running zero commands reports `passed: True`. Test: `test_empty_commands_without_the_opt_out_escalate_after_merging` (Task 1).
- An empty `commands` list with the opt-out set must skip the final check and succeed. Test: `test_empty_commands_with_the_opt_out_skip_the_final_check` (Task 1).
- A story whose tip branch was never created (its last subtask never ran) is a bad ref. It must raise `GitError` and never become a success or an escalation. Test: `test_a_missing_story_tip_propagates_the_git_error` (Task 1).
- Two conflicting tips in one milestone must dispatch twice, once per tip, under one synthetic "Integrate" story holding two subtasks. Test: `test_two_conflicting_tips_dispatch_once_each_under_one_integrate_story` (Task 3).
- After a human finishes a left-in-progress merge, a relaunch must succeed with no new dispatch and an empty `resolved`, because git now says the tip is contained. Test: `test_a_relaunch_after_a_human_finished_the_merge_succeeds_without_dispatch` (Task 3).

---

## File Structure

- Create: `src/agent_manager/integration.py`. It holds the outcome dataclasses (`IntegrateSuccess`, `IntegrateEscalation`, the `IntegrateOutcome` alias), `integration_branch`, `merge_order`, `integrate_milestone` and private helpers for the final verification and the resolver dispatch.
- Create: `tests/test_integration.py`. It holds the git and store fixtures, the fake adapter, resolver and factory, and every scenario test.

No other file changes.

---

### Task 1: Clean merges in integrate order, then final verification

**Files:**
- Create: `src/agent_manager/integration.py`
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes (already on this base):
  - `agent_manager.dag.compute_integrate_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]`
  - `agent_manager.dag.story_tip(story, stories_by_id, prefix, base_branch) -> str`
  - `agent_manager.dag.subtask_branch(prefix, subtask) -> str`
  - `agent_manager.steps.integrate.merge_tip(repo_dir, worktree, integration_branch, base_branch, tip) -> dict` with keys `created, conflict, files, merged, already_merged, detail`
  - `agent_manager.steps.verify.run_suite(commands, worktree) -> {"passed", "verified", "detail"}`
  - `agent_manager.steps.reducers.verification_gate(suite_cmds, allow_no_verification, caller_provided) -> dict | None`
  - `agent_manager.steps.reducers.verification_passed_gate(result) -> dict | None`
  - `agent_manager.cli.worktree_for(repo_dir: Path, branch: str) -> Path`, `agent_manager.cli.RunnerFactory`
- Produces:
  - `@dataclass(frozen=True) class IntegrateSuccess: branch: str; worktree: Path; merged: list[str]; resolved: list[str]`
  - `@dataclass(frozen=True) class IntegrateEscalation: story: str | None; files: list[str]; detail: str; phase: str = "integrate"`
  - `IntegrateOutcome = IntegrateSuccess | IntegrateEscalation`
  - `integration_branch(branch_prefix: str) -> str`
  - `merge_order(stories: Sequence[StoryPlan], branch_prefix: str, base_branch: str) -> list[tuple[StoryPlan, str]]`
  - `integrate_milestone(stories, repo_dir, base_branch, branch_prefix, commands, allow_no_verification, store, run_id, runner_factory) -> IntegrateOutcome`. All parameters can be passed by keyword.
  - Constants `PHASE = "integrate"`, `INTEGRATE_STORY_ID = "integrate"`, `INTEGRATE_STORY_TITLE = "Integrate"`, `WORKFLOW_NAME = "integrate"`.

- [ ] **Step 1: Write the failing tests (scaffold plus clean-path scenarios)**

Create `tests/test_integration.py` with exactly this content:

```python
"""integrate_milestone: every story tip merged into one branch, then verified.

Card 6fea51ad (Integrate addendum I1, I3, I4, I5). Default-suite tests that mirror
`src/agent_manager/integration.py`: not under `tests/e2e/` and not marked `e2e`.

Everything is real except the harness: temporary git repos with a bare `origin`,
a real `Store` and journal, the shipped `builtin/integrate.yaml` driven by
`engine.run_subtask`, and `dispatch.AgentRunner` as the agent runner. The runner
factory hands out an `AgentRunner` whose launcher is a fake resolver. The fake
learns the conflicting files and its result path only by parsing the brief it
is handed. It never asks git for the conflict list, never computes a plan hash
and never commits documents. `AM_TEST_FAKE_RESOLVER_REFUSE=1` makes it refuse.

Anything git can measure is asserted through git. Every test also asserts that
the base branch, the story branches, the main checkout and `origin` are
unchanged: Integrate never writes them and never pushes.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_manager import cli, dag, dispatch, models
from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.harness.base import Outcome
from agent_manager.integration import (
    IntegrateEscalation,
    IntegrateSuccess,
    integrate_milestone,
)
from agent_manager.steps.integrate import merge_tip
from agent_manager.steps.worktree import GitError
from agent_manager.store import Store

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for integrate_milestone's tests",
)

BASE = "main"
PREFIX = "m5"
INTEGRATION_BRANCH = "m5-integrate"
RUN_ID = "run-2026-09-25-integrate-milestone"
REFUSE_ENV = "AM_TEST_FAKE_RESOLVER_REFUSE"
RESOLVED = "resolved line\n"

STORY_A = "5a5a5a5a-0000-4000-8000-00000000000a"
STORY_B = "5b5b5b5b-0000-4000-8000-00000000000b"
STORY_C = "5c5c5c5c-0000-4000-8000-00000000000c"
STORY_D = "5d5d5d5d-0000-4000-8000-00000000000d"
STORY_EMPTY = "5e5e5e5e-0000-4000-8000-00000000000e"
SUB_A = "a1a1a1a1-0000-4000-8000-000000000001"
SUB_B = "b2b2b2b2-0000-4000-8000-000000000002"
SUB_C = "c3c3c3c3-0000-4000-8000-000000000003"
SUB_D = "d4d4d4d4-0000-4000-8000-000000000004"

PASS_CMD = shlex.join([sys.executable, "-c", "print('suite green')"])
FAIL_CMD = shlex.join(
    [sys.executable, "-c", "import sys; print('suite is red'); sys.exit(3)"]
)


def _cwd_recorder(marker: Path) -> str:
    """A passing command that appends the directory it ran in to `marker`."""
    code = (
        "import os, pathlib; "
        f"pathlib.Path({str(marker)!r}).open('a').write(os.getcwd() + '\\n')"
    )
    return shlex.join([sys.executable, "-c", code])


# ── git helpers ──────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _sha(cwd: Path, ref: str) -> str:
    return _git(cwd, "rev-parse", ref).strip()


def _merge_head(worktree: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _is_ancestor(worktree: Path, ref: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", ref, "HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _merged_second_parents(worktree: Path) -> list[str]:
    """The tip each first-parent merge above the base brought in, oldest first."""
    merges = _git(worktree, "rev-list", "--first-parent", "--reverse", f"{BASE}..HEAD")
    return [_sha(worktree, f"{commit}^2") for commit in merges.split()]


# ── the repo, the census and the store ───────────────────────────────────────


@dataclass
class Repo:
    root: Path
    tmp: Path

    @property
    def worktree(self) -> Path:
        return cli.worktree_for(self.root, INTEGRATION_BRANCH)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Repo:
    """A repo on `main` with a bare `origin`, holding README.md and shared.txt.

    `.claude/` is excluded the way projects ignore it, so the integration
    worktree under `.claude/worktrees/` does not show in the main checkout's
    `git status`.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv(REFUSE_ENV, raising=False)

    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", BASE, str(root)], capture_output=True, text=True, check=True
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    exclude = root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\n.claude/\n")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    (root / "shared.txt").write_text("shared line\n", encoding="utf-8")
    _git(root, "add", "README.md", "shared.txt")
    _git(root, "commit", "-m", "base")

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(root), str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "fetch", "origin")
    return Repo(root=root, tmp=tmp_path)


@pytest.fixture
def store(repo: Repo) -> Iterator[Store]:
    """A real store with the run already recorded, as the milestone runner does."""
    opened = Store.open(repo.root, RUN_ID)
    opened.record_run(
        models.Run(
            id=RUN_ID,
            workflow="task",
            repo_dir=repo.root,
            base_branch=BASE,
            branch_prefix=PREFIX,
            status="started",
        )
    )
    yield opened
    opened.close()


def _story(
    story_id: str,
    title: str,
    subtask_id: str | None = None,
    blocked_by: tuple[str, ...] = (),
) -> StoryPlan:
    subtasks = (
        []
        if subtask_id is None
        else [SubtaskPlan(id=subtask_id, title=f"{title} work", status="done")]
    )
    return StoryPlan(
        id=story_id,
        title=title,
        status="done",
        blocked_by=list(blocked_by),
        subtasks=subtasks,
    )


def _tip_branch(story: StoryPlan) -> str:
    return dag.subtask_branch(PREFIX, story.subtasks[-1])


def _story_branch(
    repo: Repo, story: StoryPlan, files: dict[str, str], start: str = BASE
) -> str:
    """Create the story's tip branch from `start` with `files` committed; return it."""
    branch = _tip_branch(story)
    scratch = repo.tmp / f"scratch-{dag.short_id(story.subtasks[-1].id)}"
    _git(repo.root, "worktree", "add", str(scratch), "-b", branch, start)
    for name, body in files.items():
        (scratch / name).write_text(body, encoding="utf-8")
    _git(scratch, "add", *files)
    _git(scratch, "commit", "-m", f"work on {branch}")
    _git(repo.root, "worktree", "remove", str(scratch))
    return branch


def _protected(repo: Repo, branches: list[str]) -> dict[str, str]:
    """Everything Integrate must never change."""
    state = {
        "base": _sha(repo.root, f"refs/heads/{BASE}"),
        "checked_out": _git(repo.root, "symbolic-ref", "HEAD").strip(),
        "status": _git(repo.root, "status", "--porcelain"),
        "origin_refs": _git(repo.root, "ls-remote", "origin"),
    }
    for branch in branches:
        state[branch] = _sha(repo.root, f"refs/heads/{branch}")
    return state


def _assert_protected(repo: Repo, before: dict[str, str], branches: list[str]) -> None:
    after = _protected(repo, branches)
    assert after == before
    assert INTEGRATION_BRANCH not in after["origin_refs"]


# ── the harness doubles ──────────────────────────────────────────────────────


class _FakeAdapter:
    """A `HarnessAdapter` by shape. Its argv names the brief and nothing else."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return ["fake-resolver", "--prompt", str(d.prompt_path)]

    def parse_usage(self, stdout: str) -> None:
        return None


_CONFLICT_HEADING = "\n## conflict_files\n"
_RESULT_LEAD = "write your result as valid JSON to exactly this path:\n\n"


def _conflict_files_from(brief: str) -> list[str]:
    start = brief.index(_CONFLICT_HEADING) + len(_CONFLICT_HEADING)
    files, _end = json.JSONDecoder().raw_decode(brief, start)
    return files


def _result_path_from(brief: str) -> Path:
    start = brief.index(_RESULT_LEAD) + len(_RESULT_LEAD)
    return Path(brief[start : brief.index("\n", start)])


@dataclass
class FakeResolver:
    """A `LauncherFn` double that plays the resolver in the worktree it runs in.

    It reads only the brief. It resolves by writing `RESOLVED` into every listed
    file, staging them and committing the merge. With `REFUSE_ENV=1` it changes
    nothing and reports `resolved: false`.
    """

    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        brief = Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
        files = _conflict_files_from(brief)
        self.calls.append(files)
        worktree = Path(cwd)
        if os.environ.get(REFUSE_ENV) == "1":
            result: dict[str, Any] = {"resolved": False, "summary": "refusing to resolve"}
        else:
            for name in files:
                (worktree / name).write_text(RESOLVED, encoding="utf-8")
            _git(worktree, "add", *files)
            _git(worktree, "commit", "--no-edit")
            result = {"resolved": True, "summary": f"resolved {', '.join(files)}"}
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        _result_path_from(brief).write_text(json.dumps(result), encoding="utf-8")
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.1,
            stdout_path=stdout_path,
        )


@dataclass
class FakeFactory:
    """A `cli.RunnerFactory` that counts dispatches and wires in `FakeResolver`."""

    resolver: FakeResolver = field(default_factory=FakeResolver)
    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, *, workflow, store, run_id, story_id, card_id):
        self.calls.append(
            {
                "workflow": workflow.name,
                "run_id": run_id,
                "story_id": story_id,
                "card_id": card_id,
            }
        )
        adapter = _FakeAdapter()
        return dispatch.AgentRunner(
            workflow=workflow,
            store=store,
            launcher=self.resolver,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            adapters={adapter.name: adapter},
            harness_map={
                "resolver": models.HarnessAssignment(harness=adapter.name, model="fake-model")
            },
        )


def _integrate(
    repo: Repo,
    store: Store,
    stories: list[StoryPlan],
    *,
    factory: FakeFactory,
    commands: list[str] | None = None,
    allow_no_verification: bool = False,
):
    return integrate_milestone(
        stories=stories,
        repo_dir=repo.root,
        base_branch=BASE,
        branch_prefix=PREFIX,
        commands=[PASS_CMD] if commands is None else commands,
        allow_no_verification=allow_no_verification,
        store=store,
        run_id=RUN_ID,
        runner_factory=factory,
    )


# ── clean merges and the final verification ─────────────────────────────────


def _clean_milestone(repo: Repo) -> tuple[list[StoryPlan], list[str]]:
    """Census [B, C, empty, A], where B is blocked by A and stacked on A's tip.

    Levels: [C, empty, A] then [B]. Merge order is therefore C, A, B, and the
    empty story is skipped.
    """
    story_a = _story(STORY_A, "Story A", SUB_A)
    story_b = _story(STORY_B, "Story B", SUB_B, blocked_by=(STORY_A,))
    story_c = _story(STORY_C, "Story C", SUB_C)
    empty = _story(STORY_EMPTY, "Empty story")
    tip_a = _story_branch(repo, story_a, {"a.txt": "from a\n"})
    tip_b = _story_branch(repo, story_b, {"b.txt": "from b\n"}, start=tip_a)
    tip_c = _story_branch(repo, story_c, {"c.txt": "from c\n"})
    return [story_b, story_c, empty, story_a], [tip_c, tip_a, tip_b]


def test_clean_tips_merge_in_level_then_census_order_and_dispatch_no_agent(
    repo: Repo, store: Store
) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)
    marker = repo.tmp / "verified.txt"
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory, commands=[_cwd_recorder(marker)])

    assert isinstance(outcome, IntegrateSuccess)
    assert outcome.branch == INTEGRATION_BRANCH
    assert outcome.worktree == repo.worktree
    assert outcome.merged == [STORY_C, STORY_A, STORY_B]
    assert outcome.resolved == []
    assert factory.calls == []
    assert factory.resolver.calls == []
    # git, not the outcome, says what was merged and in which order.
    assert _git(repo.worktree, "symbolic-ref", "--short", "HEAD").strip() == INTEGRATION_BRANCH
    assert _merge_head(repo.worktree) is None
    assert _merged_second_parents(repo.worktree) == [_sha(repo.root, tip) for tip in tips]
    # The final verification ran exactly once, in the integration worktree.
    ran_in = [Path(line).resolve() for line in marker.read_text().splitlines()]
    assert ran_in == [repo.worktree.resolve()]
    assert store.load_run(RUN_ID).stories == []
    _assert_protected(repo, before, tips)


def test_a_failing_final_verification_escalates_after_the_merges(
    repo: Repo, store: Store
) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory, commands=[FAIL_CMD])

    assert isinstance(outcome, IntegrateEscalation)
    assert outcome.phase == "integrate"
    assert outcome.story is None
    assert outcome.files == []
    assert "verification failed" in outcome.detail
    assert factory.calls == []
    for tip in tips:
        assert _is_ancestor(repo.worktree, tip)
    _assert_protected(repo, before, tips)


def test_empty_commands_without_the_opt_out_escalate_after_merging(
    repo: Repo, store: Store
) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)

    outcome = _integrate(repo, store, stories, factory=FakeFactory(), commands=[])

    assert isinstance(outcome, IntegrateEscalation)
    assert outcome.phase == "integrate"
    assert outcome.story is None
    assert "no full-suite command is available" in outcome.detail
    for tip in tips:
        assert _is_ancestor(repo.worktree, tip)
    _assert_protected(repo, before, tips)


def test_empty_commands_with_the_opt_out_skip_the_final_check(
    repo: Repo, store: Store
) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)

    outcome = _integrate(
        repo, store, stories, factory=FakeFactory(), commands=[], allow_no_verification=True
    )

    assert isinstance(outcome, IntegrateSuccess)
    assert outcome.merged == [STORY_C, STORY_A, STORY_B]
    _assert_protected(repo, before, tips)


def test_an_already_integrated_milestone_is_a_no_op(repo: Repo, store: Store) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)
    marker = repo.tmp / "verified.txt"
    factory = FakeFactory()

    first = _integrate(repo, store, stories, factory=factory, commands=[_cwd_recorder(marker)])
    assert isinstance(first, IntegrateSuccess)
    head = _sha(repo.worktree, "HEAD")

    second = _integrate(repo, store, stories, factory=factory, commands=[_cwd_recorder(marker)])

    assert isinstance(second, IntegrateSuccess)
    assert second.merged == [STORY_C, STORY_A, STORY_B]
    assert second.resolved == []
    assert factory.calls == []
    assert _sha(repo.worktree, "HEAD") == head
    assert _sha(repo.root, f"refs/heads/{INTEGRATION_BRANCH}") == head
    # Rule 6: the final verification still runs on the second pass.
    assert len(marker.read_text().splitlines()) == 2
    _assert_protected(repo, before, tips)


def test_a_missing_story_tip_propagates_the_git_error(repo: Repo, store: Store) -> None:
    """A story whose last subtask never produced a branch is a bad ref. That is
    a git failure other than a merge in progress, so it propagates."""
    story_a = _story(STORY_A, "Story A", SUB_A)
    story_d = _story(STORY_D, "Story D", SUB_D)
    tip_a = _story_branch(repo, story_a, {"a.txt": "from a\n"})
    before = _protected(repo, [tip_a])
    factory = FakeFactory()

    with pytest.raises(GitError):
        _integrate(repo, store, [story_a, story_d], factory=factory)

    assert factory.calls == []
    _assert_protected(repo, before, [tip_a])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_integration.py -v`
Expected: collection ERROR with `ModuleNotFoundError: No module named 'agent_manager.integration'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/integration.py` with exactly this content:

```python
"""Integrate: fold every story tip of a milestone into one local branch, then verify it.

Integrate addendum (`docs/superpowers/specs/2026-09-25-integrate-design.md`)
decisions I1, I3, I4 and I5, narrowed by card 6fea51ad. The order is
`dag.compute_integrate_levels` over every story, done or not, in census order
within a level. A story with no subtasks has no tip of its own and is skipped.
Each tip is merged by `steps.integrate.merge_tip` into `<prefix>-integrate`, in
the worktree `cli.worktree_for` names. After the last tip the suite runs once in
that worktree, and `verification_passed_gate` judges what it measured.

Integrate never checks out, merges into, resets or pushes the base branch or a
story branch, and never pushes anything. Every git write is `merge_tip`'s, in
the integration worktree. The outcome is internal state, so it is a plain
dataclass (CLAUDE.md). Wiring this into `orchestrate.run_milestone` belongs to
sibling a74f2cd6.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_manager import cli, dag
from agent_manager.census import StoryPlan
from agent_manager.steps import reducers, verify
from agent_manager.steps.integrate import merge_tip
from agent_manager.store import Store

PHASE = "integrate"
"""The `phase` every Integrate escalation names."""

INTEGRATE_STORY_ID = "integrate"
"""The synthetic story every resolver subtask hangs from (addendum I3)."""

INTEGRATE_STORY_TITLE = "Integrate"

WORKFLOW_NAME = "integrate"
"""The builtin document a conflicting tip is resolved with."""


@dataclass(frozen=True)
class IntegrateSuccess:
    """Every tip is in `branch`, and the final verification passed or was opted out."""

    branch: str
    worktree: Path
    merged: list[str] = field(default_factory=list)
    """Story ids whose tip is in `branch`, in merge order, already-merged ones included."""
    resolved: list[str] = field(default_factory=list)
    """Story ids whose conflict a resolver fixed."""


@dataclass(frozen=True)
class IntegrateEscalation:
    """Integrate stopped. A human reads `detail`, and the worktree is left as it is."""

    story: str | None
    """The story whose tip was involved, or `None` for the final verification."""
    files: list[str]
    detail: str
    phase: str = PHASE


IntegrateOutcome = IntegrateSuccess | IntegrateEscalation


def integration_branch(branch_prefix: str) -> str:
    """`<branch_prefix>-integrate`: the one branch every story tip is merged into."""
    return f"{branch_prefix}-integrate"


def merge_order(
    stories: Sequence[StoryPlan], branch_prefix: str, base_branch: str
) -> list[tuple[StoryPlan, str]]:
    """Each story that has subtasks, paired with its tip, in integrate order.

    Levels come from `dag.compute_integrate_levels` over every story, and each
    level keeps census order. A story with no subtasks is left out: its
    `story_tip` would fall through to its root, which is another story's tip or
    the base, and neither is this story's work.
    """
    stories = list(stories)
    stories_by_id = {story.id: story for story in stories}
    ordered: list[tuple[StoryPlan, str]] = []
    for level in dag.compute_integrate_levels(stories):
        for story in level:
            if not story.subtasks:
                continue
            tip = dag.story_tip(story, stories_by_id, branch_prefix, base_branch)
            ordered.append((story, tip))
    return ordered


def _final_verification(
    commands: list[str], allow_no_verification: bool, worktree: Path
) -> str | None:
    """`None` when the integrated branch is verified or opted out, else the reason.

    An empty suite is judged by `verification_gate` first, because running zero
    commands reports `passed: True` and `verification_passed_gate` would wave it
    through. `bool()` matches `cli.gate_context`: the reducer tests `is True`.
    """
    missing = reducers.verification_gate(commands, bool(allow_no_verification), True)
    if missing is not None:
        return str(missing["detail"])
    if not commands:
        return None
    verdict = reducers.verification_passed_gate(verify.run_suite(commands, worktree))
    if verdict is None:
        return None
    return (
        f"the integrated branch failed its final verification in {worktree}: "
        f"{verdict['detail']}"
    )


def integrate_milestone(
    stories: Sequence[StoryPlan],
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    commands: Sequence[str],
    allow_no_verification: bool,
    store: Store,
    run_id: str,
    runner_factory: cli.RunnerFactory,
) -> IntegrateOutcome:
    """Merge every story tip into `<branch_prefix>-integrate`, then verify it once."""
    root = Path(repo_dir).resolve()
    branch = integration_branch(branch_prefix)
    worktree = cli.worktree_for(root, branch)
    suite = list(commands)
    merged: list[str] = []
    resolved: list[str] = []

    for story, tip in merge_order(stories, branch_prefix, base_branch):
        result = merge_tip(root, worktree, branch, base_branch, tip)
        if result["conflict"]:
            files = [str(name) for name in result["files"]]
            return IntegrateEscalation(
                story=story.id,
                files=files,
                detail=(
                    f"merging {tip} into {branch} conflicted in {', '.join(files)}; "
                    f"the merge is left in progress in {worktree}"
                ),
            )
        merged.append(story.id)

    failure = _final_verification(suite, allow_no_verification, worktree)
    if failure is not None:
        return IntegrateEscalation(story=None, files=[], detail=failure)
    return IntegrateSuccess(branch=branch, worktree=worktree, merged=merged, resolved=resolved)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_integration.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/integration.py tests/test_integration.py
git commit -m "feat(integration): merge every story tip in integrate order and verify once"
```

---

### Task 2: A merge already in progress escalates with the human-must-finish message

**Files:**
- Modify: `src/agent_manager/integration.py` (the import of `merge_tip` and the `merge_tip` call inside `integrate_milestone`)
- Test: `tests/test_integration.py` (append)

**Interfaces:**
- Consumes: `agent_manager.steps.integrate.MergeInProgressError(worktree: str)`, whose message contains "A human must finish it" and "then relaunch". `integrate_milestone` and `IntegrateEscalation` come from Task 1.
- Produces: no new names. `integrate_milestone` now returns `IntegrateEscalation(story=<tip's story id>, files=[], detail=str(error))` in this case.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integration.py`:

```python
# ── a merge already in progress ──────────────────────────────────────────────


def _conflicting_pair(repo: Repo) -> tuple[list[StoryPlan], list[str]]:
    """Stories A and B, both cut from the base, editing the same line of shared.txt."""
    story_a = _story(STORY_A, "Story A", SUB_A)
    story_b = _story(STORY_B, "Story B", SUB_B)
    tip_a = _story_branch(repo, story_a, {"shared.txt": "story a\n"})
    tip_b = _story_branch(repo, story_b, {"shared.txt": "story b\n"})
    return [story_a, story_b], [tip_a, tip_b]


def test_a_merge_already_in_progress_escalates_without_dispatching(
    repo: Repo, store: Store
) -> None:
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    # An earlier run left B's conflict unresolved in the integration worktree.
    assert merge_tip(repo.root, repo.worktree, INTEGRATION_BRANCH, BASE, tips[0])["conflict"] is False
    assert merge_tip(repo.root, repo.worktree, INTEGRATION_BRANCH, BASE, tips[1])["conflict"] is True
    merge_head = _merge_head(repo.worktree)
    head = _sha(repo.worktree, "HEAD")
    assert merge_head is not None
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory)

    assert isinstance(outcome, IntegrateEscalation)
    assert outcome.phase == "integrate"
    assert outcome.story == STORY_A
    assert outcome.files == []
    assert "A human must finish it" in outcome.detail
    assert "relaunch" in outcome.detail
    assert str(repo.worktree) in outcome.detail
    assert factory.calls == []
    assert factory.resolver.calls == []
    assert _merge_head(repo.worktree) == merge_head
    assert _sha(repo.worktree, "HEAD") == head
    assert store.load_run(RUN_ID).stories == []
    _assert_protected(repo, before, tips)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_integration.py::test_a_merge_already_in_progress_escalates_without_dispatching -v`
Expected: FAIL with `agent_manager.steps.integrate.MergeInProgressError: a merge is already in progress in ...` raised out of `integrate_milestone`.

- [ ] **Step 3: Implement the escalation**

In `src/agent_manager/integration.py`, replace:

```python
from agent_manager.steps.integrate import merge_tip
```

with:

```python
from agent_manager.steps.integrate import MergeInProgressError, merge_tip
```

Then replace:

```python
        result = merge_tip(root, worktree, branch, base_branch, tip)
        if result["conflict"]:
```

with:

```python
        try:
            result = merge_tip(root, worktree, branch, base_branch, tip)
        except MergeInProgressError as error:
            # The error's own message already tells the human to finish the
            # merge in the worktree and relaunch. Nothing is dispatched or
            # recorded: that unresolved merge is not this run's to touch.
            return IntegrateEscalation(story=story.id, files=[], detail=str(error))
        if result["conflict"]:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_integration.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/integration.py tests/test_integration.py
git commit -m "feat(integration): escalate a merge already in progress for a human to finish"
```

---

### Task 3: Resolve each conflicting tip through `builtin/integrate.yaml`

**Files:**
- Modify: `src/agent_manager/integration.py` (full replacement below)
- Test: `tests/test_integration.py` (append)

**Interfaces:**
- Consumes:
  - `agent_manager.workflow.loader.load_builtin(name: str, registry=None) -> Workflow`
  - `agent_manager.engine.run_subtask(workflow, store, *, story_id, subtask, repo_dir, commands, extra_context, agent_runner) -> engine.SubtaskSummary` (`status` is `"done" | "escalated" | "stopped"`, and `failed_phase`/`detail` are set on escalation)
  - `agent_manager.cli.gate_context(commands, allow_no_verification) -> dict` (keys `suite_cmds`, `allow_no_verification`, `caller_provided`, `provided_verification`)
  - `models.StoryRun(card_id, title, level, status, tip_branch)`, `models.SubtaskRun(card_id, branch, base_branch, status, worktree_path)`
  - `Store.record_story(story)`, `Store.record_subtask(story_id, subtask)`, `Store.load_run(run_id)`, `Store.rebuild_from_journal(run_id)`
  - The `cli.RunnerFactory` call shape `runner_factory(*, workflow, store, run_id, story_id, card_id)`
- Produces: the same public names as Task 1. The new behaviour is that a conflicting tip is resolved by the resolver subtask. On success its id goes into both `merged` and `resolved`. On failure the result is `IntegrateEscalation(story, files, detail)` with `MERGE_HEAD` left in place. The synthetic story's status is recorded `done` on success and `escalated` on any escalation, but only once a conflict has recorded it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integration.py`:

```python
# ── conflicts go to the resolver ─────────────────────────────────────────────


def _integrate_story(store: Store) -> models.StoryRun:
    run = store.load_run(RUN_ID)
    assert [story.card_id for story in run.stories] == ["integrate"]
    return run.stories[0]


def test_a_conflict_dispatches_exactly_once_for_the_conflicting_tip(
    repo: Repo, store: Store
) -> None:
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory)

    assert isinstance(outcome, IntegrateSuccess), outcome
    assert outcome.merged == [STORY_A, STORY_B]
    assert outcome.resolved == [STORY_B]
    assert factory.calls == [
        {"workflow": "integrate", "run_id": RUN_ID, "story_id": "integrate", "card_id": STORY_B}
    ]
    assert factory.resolver.calls == [["shared.txt"]]
    # git judges the merge, not the resolver's report.
    assert _merge_head(repo.worktree) is None
    assert _git(repo.worktree, "status", "--porcelain") == ""
    assert _is_ancestor(repo.worktree, tips[0])
    assert _is_ancestor(repo.worktree, tips[1])
    assert _git(repo.worktree, "show", "HEAD:shared.txt") == RESOLVED
    # The synthetic story and subtask are in the store, and replay agrees.
    story = _integrate_story(store)
    assert story.title == "Integrate"
    assert story.status == "done"
    [subtask] = story.subtasks
    assert subtask.card_id == STORY_B
    assert subtask.branch == INTEGRATION_BRANCH
    assert subtask.base_branch == BASE
    assert subtask.worktree_path == repo.worktree
    assert subtask.status == "done"
    assert [phase.name for phase in subtask.phases] == ["resolve", "verify"]
    rebuilt = store.rebuild_from_journal(RUN_ID)
    assert [s.card_id for s in rebuilt.stories] == ["integrate"]
    assert [(s.card_id, s.status) for s in rebuilt.stories[0].subtasks] == [(STORY_B, "done")]
    assert [p.name for p in rebuilt.stories[0].subtasks[0].phases] == ["resolve", "verify"]
    _assert_protected(repo, before, tips)


def test_a_refusing_resolver_escalates_and_leaves_merge_head_in_place(
    repo: Repo, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    monkeypatch.setenv(REFUSE_ENV, "1")
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory)

    assert isinstance(outcome, IntegrateEscalation)
    assert outcome.phase == "integrate"
    assert outcome.story == STORY_B
    assert outcome.files == ["shared.txt"]
    assert "resolve" in outcome.detail
    assert "MERGE_HEAD exists" in outcome.detail
    assert [call["card_id"] for call in factory.calls] == [STORY_B]
    # One dispatch per conflicting tip; the runner's own retry is inside it.
    assert factory.resolver.calls == [["shared.txt"], ["shared.txt"]]
    # Left exactly as it is for a human: never aborted, reset or cleaned.
    assert _merge_head(repo.worktree) == _sha(repo.root, tips[1])
    assert repo.worktree.is_dir()
    assert str(repo.worktree) in _git(repo.root, "worktree", "list", "--porcelain")
    assert _sha(repo.root, f"refs/heads/{INTEGRATION_BRANCH}")
    story = _integrate_story(store)
    assert story.status == "escalated"
    assert [(s.card_id, s.status) for s in story.subtasks] == [(STORY_B, "escalated")]
    _assert_protected(repo, before, tips)


def test_two_conflicting_tips_dispatch_once_each_under_one_integrate_story(
    repo: Repo, store: Store
) -> None:
    story_a = _story(STORY_A, "Story A", SUB_A)
    story_b = _story(STORY_B, "Story B", SUB_B)
    story_c = _story(STORY_C, "Story C", SUB_C)
    tips = [
        _story_branch(repo, story_a, {"shared.txt": "story a\n"}),
        _story_branch(repo, story_b, {"shared.txt": "story b\n"}),
        _story_branch(repo, story_c, {"shared.txt": "story c\n"}),
    ]
    before = _protected(repo, tips)
    factory = FakeFactory()

    outcome = _integrate(repo, store, [story_a, story_b, story_c], factory=factory)

    assert isinstance(outcome, IntegrateSuccess), outcome
    assert outcome.merged == [STORY_A, STORY_B, STORY_C]
    assert outcome.resolved == [STORY_B, STORY_C]
    assert [call["card_id"] for call in factory.calls] == [STORY_B, STORY_C]
    assert factory.resolver.calls == [["shared.txt"], ["shared.txt"]]
    assert _merge_head(repo.worktree) is None
    for tip in tips:
        assert _is_ancestor(repo.worktree, tip)
    story = _integrate_story(store)
    assert [(s.card_id, s.status) for s in story.subtasks] == [
        (STORY_B, "done"),
        (STORY_C, "done"),
    ]
    _assert_protected(repo, before, tips)


def test_a_relaunch_after_a_human_finished_the_merge_succeeds_without_dispatch(
    repo: Repo, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    monkeypatch.setenv(REFUSE_ENV, "1")
    first_factory = FakeFactory()
    first = _integrate(repo, store, stories, factory=first_factory)
    assert isinstance(first, IntegrateEscalation)
    assert len(first_factory.calls) == 1

    # The human finishes the merge in the integration worktree.
    (repo.worktree / "shared.txt").write_text("human fix\n", encoding="utf-8")
    _git(repo.worktree, "add", "shared.txt")
    _git(repo.worktree, "commit", "--no-edit")
    monkeypatch.delenv(REFUSE_ENV)
    head = _sha(repo.worktree, "HEAD")
    second_factory = FakeFactory()

    second = _integrate(repo, store, stories, factory=second_factory)

    assert isinstance(second, IntegrateSuccess), second
    assert second.merged == [STORY_A, STORY_B]
    assert second.resolved == []
    assert second_factory.calls == []
    assert _sha(repo.worktree, "HEAD") == head
    assert _git(repo.worktree, "show", "HEAD:shared.txt") == "human fix\n"
    _assert_protected(repo, before, tips)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_integration.py -v -k "conflict or refusing or relaunch"`
Expected: 4 FAIL. `test_a_conflict_dispatches_exactly_once_for_the_conflicting_tip` and `test_two_conflicting_tips_dispatch_once_each_under_one_integrate_story` fail on `assert isinstance(outcome, IntegrateSuccess)`, because the outcome is the Task 1 conflict escalation. `test_a_refusing_resolver_escalates_and_leaves_merge_head_in_place` fails on `assert "MERGE_HEAD exists" in outcome.detail`, because no resolver was dispatched. The `"resolve"` check before it can pass by accident, since pytest's `tmp_path` name for this test contains "resolver". `test_a_relaunch_after_a_human_finished_the_merge_succeeds_without_dispatch` fails on `assert len(first_factory.calls) == 1`.

- [ ] **Step 3: Implement the resolver dispatch**

Replace the whole of `src/agent_manager/integration.py` with:

```python
"""Integrate: fold every story tip of a milestone into one local branch, then verify it.

Integrate addendum (`docs/superpowers/specs/2026-09-25-integrate-design.md`)
decisions I1, I3, I4 and I5, narrowed by card 6fea51ad. The order is
`dag.compute_integrate_levels` over every story, done or not, in census order
within a level. A story with no subtasks has no tip of its own and is skipped.
Each tip is merged by `steps.integrate.merge_tip` into `<prefix>-integrate`, in
the worktree `cli.worktree_for` names. After the last tip the suite runs once in
that worktree, and `verification_passed_gate` judges what it measured.

A conflicting tip is handed to the builtin `integrate.yaml` (resolve, then
verify) through `engine.run_subtask`, under a synthetic "Integrate" story with
one synthetic subtask per conflicting story. Both are recorded before the
engine journals any phase, because `store.rebuild_from_journal` refuses a phase
whose story or subtask no earlier line created. A resolver that does not finish
stops the run with the merge left in progress for a human.

Integrate never checks out, merges into, resets or pushes the base branch or a
story branch, and never pushes anything. Every git write is `merge_tip`'s or the
resolver's, in the integration worktree. The outcome is internal state, so it is
a plain dataclass (CLAUDE.md). Wiring this into `orchestrate.run_milestone`
belongs to sibling a74f2cd6.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_manager import cli, dag, engine, models
from agent_manager.census import StoryPlan
from agent_manager.steps import reducers, verify
from agent_manager.steps.integrate import MergeInProgressError, merge_tip
from agent_manager.store import Store
from agent_manager.workflow.loader import load_builtin

PHASE = "integrate"
"""The `phase` every Integrate escalation names."""

INTEGRATE_STORY_ID = "integrate"
"""The synthetic story every resolver subtask hangs from (addendum I3)."""

INTEGRATE_STORY_TITLE = "Integrate"

WORKFLOW_NAME = "integrate"
"""The builtin document a conflicting tip is resolved with."""


@dataclass(frozen=True)
class IntegrateSuccess:
    """Every tip is in `branch`, and the final verification passed or was opted out."""

    branch: str
    worktree: Path
    merged: list[str] = field(default_factory=list)
    """Story ids whose tip is in `branch`, in merge order, already-merged ones included."""
    resolved: list[str] = field(default_factory=list)
    """Story ids whose conflict a resolver fixed."""


@dataclass(frozen=True)
class IntegrateEscalation:
    """Integrate stopped. A human reads `detail`, and the worktree is left as it is."""

    story: str | None
    """The story whose tip was involved, or `None` for the final verification."""
    files: list[str]
    detail: str
    phase: str = PHASE


IntegrateOutcome = IntegrateSuccess | IntegrateEscalation


def integration_branch(branch_prefix: str) -> str:
    """`<branch_prefix>-integrate`: the one branch every story tip is merged into."""
    return f"{branch_prefix}-integrate"


def merge_order(
    stories: Sequence[StoryPlan], branch_prefix: str, base_branch: str
) -> list[tuple[StoryPlan, str]]:
    """Each story that has subtasks, paired with its tip, in integrate order.

    Levels come from `dag.compute_integrate_levels` over every story, and each
    level keeps census order. A story with no subtasks is left out: its
    `story_tip` would fall through to its root, which is another story's tip or
    the base, and neither is this story's work.
    """
    stories = list(stories)
    stories_by_id = {story.id: story for story in stories}
    ordered: list[tuple[StoryPlan, str]] = []
    for level in dag.compute_integrate_levels(stories):
        for story in level:
            if not story.subtasks:
                continue
            tip = dag.story_tip(story, stories_by_id, branch_prefix, base_branch)
            ordered.append((story, tip))
    return ordered


def _final_verification(
    commands: list[str], allow_no_verification: bool, worktree: Path
) -> str | None:
    """`None` when the integrated branch is verified or opted out, else the reason.

    An empty suite is judged by `verification_gate` first, because running zero
    commands reports `passed: True` and `verification_passed_gate` would wave it
    through. `bool()` matches `cli.gate_context`: the reducer tests `is True`.
    """
    missing = reducers.verification_gate(commands, bool(allow_no_verification), True)
    if missing is not None:
        return str(missing["detail"])
    if not commands:
        return None
    verdict = reducers.verification_passed_gate(verify.run_suite(commands, worktree))
    if verdict is None:
        return None
    return (
        f"the integrated branch failed its final verification in {worktree}: "
        f"{verdict['detail']}"
    )


def _resolve_conflict(
    *,
    story_id: str,
    tip: str,
    files: list[str],
    branch: str,
    base_branch: str,
    worktree: Path,
    repo_dir: Path,
    commands: list[str],
    allow_no_verification: bool,
    store: Store,
    run_id: str,
    runner_factory: cli.RunnerFactory,
) -> engine.SubtaskSummary:
    """Drive `builtin/integrate.yaml` once for one conflicting tip.

    The synthetic subtask is recorded before `run_subtask` journals its first
    phase. The caller has already recorded the synthetic story.
    """
    workflow = load_builtin(WORKFLOW_NAME)
    subtask = models.SubtaskRun(
        card_id=story_id,
        branch=branch,
        base_branch=base_branch,
        status="started",
        worktree_path=worktree,
    )
    store.record_subtask(INTEGRATE_STORY_ID, subtask)
    runner = runner_factory(
        workflow=workflow,
        store=store,
        run_id=run_id,
        story_id=INTEGRATE_STORY_ID,
        card_id=story_id,
    )
    return engine.run_subtask(
        workflow,
        store,
        story_id=INTEGRATE_STORY_ID,
        subtask=subtask,
        repo_dir=repo_dir,
        commands=commands,
        extra_context={
            "merge_tip": tip,
            "conflict_files": list(files),
            **cli.gate_context(commands, allow_no_verification),
        },
        agent_runner=runner,
    )


def _resolver_detail(
    tip: str, branch: str, worktree: Path, summary: engine.SubtaskSummary
) -> str:
    return (
        f"the resolver did not finish merging {tip} into {branch}: phase "
        f"{summary.failed_phase!r} ended {summary.status} ({summary.detail}). "
        f"The branch and worktree are left as they are in {worktree}; a human must "
        "finish the merge there, then relaunch."
    )


def integrate_milestone(
    stories: Sequence[StoryPlan],
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    commands: Sequence[str],
    allow_no_verification: bool,
    store: Store,
    run_id: str,
    runner_factory: cli.RunnerFactory,
) -> IntegrateOutcome:
    """Merge every story tip into `<branch_prefix>-integrate`, then verify it once.

    The caller owns the run: it has recorded the `Run` in `store` under
    `run_id`, and it decides the run's status from the outcome. This function
    records only the synthetic "Integrate" story and its subtasks, and only when
    a tip conflicts.
    """
    root = Path(repo_dir).resolve()
    branch = integration_branch(branch_prefix)
    worktree = cli.worktree_for(root, branch)
    suite = list(commands)
    story_row = models.StoryRun(
        card_id=INTEGRATE_STORY_ID,
        title=INTEGRATE_STORY_TITLE,
        level=0,
        status="started",
        tip_branch=branch,
    )
    story_recorded = False
    merged: list[str] = []
    resolved: list[str] = []

    def escalate(story_id: str | None, files: list[str], detail: str) -> IntegrateEscalation:
        if story_recorded:
            store.record_story(story_row.model_copy(update={"status": "escalated"}))
        return IntegrateEscalation(story=story_id, files=files, detail=detail)

    for story, tip in merge_order(stories, branch_prefix, base_branch):
        try:
            result = merge_tip(root, worktree, branch, base_branch, tip)
        except MergeInProgressError as error:
            # The error's own message already tells the human to finish the
            # merge in the worktree and relaunch. Nothing is dispatched or
            # recorded: that unresolved merge is not this run's to touch.
            return escalate(story.id, [], str(error))
        if result["conflict"]:
            files = [str(name) for name in result["files"]]
            if not story_recorded:
                store.record_story(story_row)
                story_recorded = True
            summary = _resolve_conflict(
                story_id=story.id,
                tip=tip,
                files=files,
                branch=branch,
                base_branch=base_branch,
                worktree=worktree,
                repo_dir=root,
                commands=suite,
                allow_no_verification=allow_no_verification,
                store=store,
                run_id=run_id,
                runner_factory=runner_factory,
            )
            if summary.status != "done":
                return escalate(
                    story.id, files, _resolver_detail(tip, branch, worktree, summary)
                )
            resolved.append(story.id)
        merged.append(story.id)

    failure = _final_verification(suite, allow_no_verification, worktree)
    if failure is not None:
        return escalate(None, [], failure)
    if story_recorded:
        store.record_story(story_row.model_copy(update={"status": "done"}))
    return IntegrateSuccess(branch=branch, worktree=worktree, merged=merged, resolved=resolved)
```

- [ ] **Step 4: Run this module's tests to verify they pass**

Run: `uv run pytest tests/test_integration.py -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS with no failures, `tests/e2e` included. The opt-in `e2e`-marked real-harness tests are deselected by the pyproject default, as before this change.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/integration.py tests/test_integration.py
git commit -m "feat(integration): resolve each conflicting tip through builtin integrate.yaml"
```

---

## Self-Review Notes

- Spec coverage:
  - Rules 1-3 (order, skip, branch/worktree, merging): Task 1, `test_clean_tips_merge_in_level_then_census_order_and_dispatch_no_agent`.
  - Rule 4 (conflict, synthetic records, run_subtask, factory args, extra_context, escalation with MERGE_HEAD): Task 3, via the conflict, refusal and two-conflicts tests.
  - Rule 5 (final verification and the empty-suite rules): Task 1, via the failing-verification test and both empty-commands tests.
  - Rule 6 (already integrated): Task 1, `test_an_already_integrated_milestone_is_a_no_op`.
  - Rule 7 (safety): `_assert_protected` in every test.
  - Error paths: `MergeInProgressError` is Task 2, bad-ref propagation is Task 1, resolver failure is Task 3 and verification failure is Task 1.
  - Spec tests 1-7 map to the named tests above.
  - The whole default suite is run in Task 3 Step 5.
- Placeholder scan: none. Every code step carries complete code.
- Type consistency: `IntegrateSuccess`, `IntegrateEscalation`, `integrate_milestone` keyword names, `INTEGRATE_STORY_ID`, `FakeFactory.calls` keys (`workflow`, `run_id`, `story_id`, `card_id`) and `FakeResolver.calls` are used the same way in every task.
