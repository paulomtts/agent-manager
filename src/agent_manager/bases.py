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

from agent_manager import cli, models
from agent_manager.dag import RootPlan
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.runtime.walk import SubtaskSummary
from agent_manager.steps import reducers, verify
from agent_manager.steps.integrate import MergeInProgressError, _ref_exists, merge_tip
from agent_manager.steps.worktree import ensure, run_git
from agent_manager.store import Store
from agent_manager.workflow import integrate as integrate_workflow

BASES_STORY_ID = "bases"
"""The synthetic story every base-resolver subtask hangs from."""

BASES_STORY_TITLE = "Merged bases"


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


async def _resolve_conflict(
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
    stop: StopSignal | None,
) -> SubtaskSummary:
    """Walk `workflow.integrate.INTEGRATE` once for one conflicting tip.

    Mirrors `integration._resolve_conflict`, awaited on the running loop and
    stop-aware. The synthetic subtask `base-<story id>` is recorded before the
    engine journals its first phase, because `store.rebuild_from_journal`
    refuses a phase whose subtask no earlier line created. The caller has
    already recorded the `bases` story.
    """
    card_id = f"base-{story_id}"
    subtask = models.SubtaskRun(
        card_id=card_id,
        branch=branch,
        base_branch=base_branch,
        status="started",
        worktree_path=worktree,
    )
    store.record_subtask(BASES_STORY_ID, subtask)
    runner = runner_factory(
        store=store, run_id=run_id, story_id=BASES_STORY_ID, card_id=card_id
    )
    return await runtime_engine.run_subtask_async(
        integrate_workflow.INTEGRATE,
        store,
        story_id=BASES_STORY_ID,
        subtask=subtask,
        repo_dir=repo_dir,
        commands=commands,
        extra_context={
            "merge_tip": tip,
            "conflict_files": list(files),
            **cli.gate_context(commands, allow_no_verification),
        },
        agent_runner=runner,
        stop=stop,
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
    resolved: list[str] = []
    story_recorded = False
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
            files = [str(name) for name in result["files"]]
            if store is None or story_id is None or runner_factory is None:
                raise BaseFailed(
                    f"conflict merging {tip} into the merged base {root.branch} "
                    f"({', '.join(files)}): no resolver is available, so the merge "
                    f"is left in progress in {worktree} for a human"
                )
            if not story_recorded:
                store.record_story(
                    models.StoryRun(
                        card_id=BASES_STORY_ID,
                        title=BASES_STORY_TITLE,
                        level=0,
                        status="started",
                    )
                )
                story_recorded = True
            await _resolve_conflict(
                story_id=story_id,
                tip=tip,
                files=files,
                branch=root.branch,
                base_branch=tips[0],
                worktree=worktree,
                repo_dir=repo,
                commands=suite,
                allow_no_verification=allow_no_verification,
                store=store,
                run_id=run_id if run_id is not None else store.run_id,
                runner_factory=runner_factory,
                stop=stop,
            )
            resolved.append(tip)
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
        branch=root.branch,
        merged=merged,
        already_merged=already_merged,
        resolved=resolved,
    )
