"""Integrate: fold every story tip of a milestone into one local branch, then verify it.

Integrate addendum (`docs/superpowers/specs/2026-09-25-integrate-design.md`)
decisions I1, I3, I4 and I5, narrowed by card 6fea51ad. The order is
`dag.compute_integrate_levels` over every story, done or not, in census order
within a level. A story with no subtasks has no tip of its own and is skipped.
Each tip is merged by `steps.integrate.merge_tip` into `<prefix>-integrate`, in
the worktree `runs.worktree_for` names. After the last tip the suite runs once in
that worktree, and `verification_passed_gate` judges what it measured.

A conflicting tip is handed to `workflow.integrate.INTEGRATE` (resolve, then
verify) through `runtime.engine.run_subtask`, under a synthetic "Integrate" story with
one synthetic subtask per conflicting story. Both are recorded before the
engine records any phase, because `store.rebuild_from_events` refuses a phase
whose story or subtask no earlier event created. A resolver that does not finish
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

from agent_manager import dag, models, runs
from agent_manager.census import StoryPlan
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.walk import SubtaskSummary
from agent_manager.steps import reducers, verify
from agent_manager.steps.integrate import MergeInProgressError, merge_tip
from agent_manager.store.writer import Store
from agent_manager.workflow import integrate as integrate_workflow

PHASE = "integrate"
"""The `phase` every Integrate escalation names."""

INTEGRATE_STORY_ID = "integrate"
"""The synthetic story every resolver subtask hangs from (addendum I3)."""

INTEGRATE_STORY_TITLE = "Integrate"


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
    through. `bool()` matches `runs.gate_context`: the reducer tests `is True`.
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
    runner_factory: runs.RunnerFactory,
) -> SubtaskSummary:
    """Drive the `integrate` workflow once for one conflicting tip.

    The synthetic subtask is recorded before `run_subtask` journals its first
    phase. The caller has already recorded the synthetic story. The walk is
    `runtime.engine.run_subtask` over `workflow.integrate.INTEGRATE`.
    """
    subtask = models.SubtaskRun(
        card_id=story_id,
        branch=branch,
        base_branch=base_branch,
        status="started",
        worktree_path=worktree,
    )
    store.record_subtask(INTEGRATE_STORY_ID, subtask)
    runner = runner_factory(
        store=store,
        run_id=run_id,
        story_id=INTEGRATE_STORY_ID,
        card_id=story_id,
    )
    walk = {
        "story_id": INTEGRATE_STORY_ID,
        "subtask": subtask,
        "repo_dir": repo_dir,
        "commands": commands,
        "extra_context": {
            "merge_tip": tip,
            "conflict_files": list(files),
            **runs.gate_context(commands, allow_no_verification),
        },
        "agent_runner": runner,
    }
    return runtime_engine.run_subtask(integrate_workflow.INTEGRATE, store, **walk)


def _resolver_detail(
    tip: str, branch: str, worktree: Path, summary: SubtaskSummary
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
    runner_factory: runs.RunnerFactory,
) -> IntegrateOutcome:
    """Merge every story tip into `<branch_prefix>-integrate`, then verify it once.

    The caller owns the run: it has recorded the `Run` in `store` under
    `run_id`, and it decides the run's status from the outcome. This function
    records only the synthetic "Integrate" story and its subtasks, and only when
    a tip conflicts.
    """
    root = Path(repo_dir).resolve()
    branch = integration_branch(branch_prefix)
    worktree = runs.worktree_for(root, branch)
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
