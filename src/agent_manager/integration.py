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
