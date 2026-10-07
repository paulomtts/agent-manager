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

A conflicting tip is handed to `workflow.integrate.INTEGRATE` (resolve, then
verify) through `runtime.engine.run_subtask_async`, awaited on the running
loop with the run's `StopSignal` (plan Task 2.2, card 8fe30578). It runs under
a synthetic "Merged bases" story (`BASES_STORY_ID`), recorded on the first
conflict only, with one synthetic subtask `base-<story id>` recorded before
the engine journals a phase. A resolver that escalates fails the base; one
parked by the stop fails it with `stopped=True`. Either way the merge is left
in the worktree for a human or a later resume.

Nothing is pushed, and the milestone's base branch is never checked out,
merged into or moved: every git write is `ensure`'s or `merge_tip`'s, in the
base worktree `runs.worktree_for` names. The outcome is internal state, so it
is a plain dataclass (CLAUDE.md).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_manager import models, runs
from agent_manager.dag import RootPlan
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.runtime.walk import SubtaskSummary
from agent_manager.steps import reducers, verify
from agent_manager.steps.integrate import MergeInProgressError, _ref_exists, merge_tip
from agent_manager.steps.worktree import GitError, ensure, run_git
from agent_manager.store import Store
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.workflow import integrate as integrate_workflow

BASES_STORY_ID = "bases"
"""The synthetic story every base-resolver subtask hangs from."""

BASES_STORY_TITLE = "Merged bases"


def resolver_card_id(story_id: str) -> str:
    """The synthetic subtask a story's base resolver walks under: `base-<story id>`.

    One name for the card the resolver records and checkpoints under, and the
    one a milestone resume looks its checkpoint up by (card 54e4ec29).
    """
    return f"base-{story_id}"


def _bases_story() -> models.StoryRun:
    """The synthetic story every resolver subtask hangs from, recorded `started`."""
    return models.StoryRun(
        card_id=BASES_STORY_ID,
        title=BASES_STORY_TITLE,
        level=0,
        status="started",
    )


@dataclass(frozen=True)
class BaseResult:
    """The merged base is built and verified.

    `merged` and `already_merged` list the tips after the first, in the order
    given; `resolved` lists the tips a resolver fixed, which are in `merged`
    too.
    """

    branch: str
    merged: list[str]
    already_merged: list[str]
    resolved: list[str]


class BaseFailed(Exception):
    """The merged base could not be built. A human reads `detail`.

    `stopped` is True only when a stop signal parked the base's resolver;
    every other failure is a real failure.
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


def _in_progress_tip(worktree: Path, tips: list[str]) -> str | None:
    """The tip `worktree`'s unfinished merge is merging, or None.

    None when the worktree does not exist, holds no merge, or its MERGE_HEAD
    is none of `tips`. `rev-parse --verify --quiet MERGE_HEAD` exits 1 when
    there is no merge; any other failure propagates.
    """
    if not worktree.exists():
        return None
    try:
        head = run_git(
            ["-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"]
        ).strip()
    except GitError as error:
        if error.exit_code == 1:
            return None
        raise
    for tip in tips:
        sha = run_git(["-C", str(worktree), "rev-parse", "--verify", f"{tip}^{{commit}}"])
        if sha.strip() == head:
            return tip
    return None


def _verify(
    commands: list[str], allow_no_verification: bool, branch: str, worktree: Path
) -> str | None:
    """`None` when the base is verified or opted out, else the reason.

    Mirrors `integration._final_verification`: an empty suite is judged by
    `verification_gate` first, because running zero commands reports
    `passed: True` and `verification_passed_gate` would wave it through.
    `bool()` matches `runs.gate_context`: the reducer tests `is True`.
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
    runner_factory: runs.RunnerFactory,
    stop: StopSignal | None,
    resume_from: store_checkpoints.Checkpoint | None = None,
) -> SubtaskSummary:
    """Walk `workflow.integrate.INTEGRATE` once for one conflicting tip.

    Mirrors `integration._resolve_conflict`, awaited on the running loop and
    stop-aware. The synthetic subtask `base-<story id>` is recorded before the
    engine journals its first phase, because `store.rebuild_from_journal`
    refuses a phase whose subtask no earlier line created. The caller has
    already recorded the `bases` story. `resume_from` continues the walk from
    a saved checkpoint (card 54e4ec29); the engine then binds from the
    checkpoint's pool, so `tip` and `files` only name it.
    """
    card_id = resolver_card_id(story_id)
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
            **runs.gate_context(commands, allow_no_verification),
        },
        agent_runner=runner,
        stop=stop,
        resume_from=resume_from,
    )


def _resolver_detail(
    tip: str, branch: str, worktree: Path, summary: SubtaskSummary
) -> str:
    """Why a resolver that escalated failed the base. Modelled on `integration._resolver_detail`."""
    return (
        f"the resolver did not finish merging {tip} into the merged base {branch}: "
        f"phase {summary.failed_phase!r} ended {summary.status} ({summary.detail}). "
        f"The branch and worktree are left as they are in {worktree}; a human must "
        "finish the merge there, then relaunch."
    )


def _stopped_detail(
    tip: str, branch: str, worktree: Path, summary: SubtaskSummary
) -> str:
    """Why a stopped resolver cut the base short. `summary.detail` is `stopped before <phase>`."""
    return (
        f"the resolver merging {tip} into the merged base {branch} was stopped "
        f"({summary.detail}). The branch and worktree are left as they are in "
        f"{worktree}, with a parked checkpoint to continue from."
    )


def _resolver_failure(
    tip: str, branch: str, worktree: Path, summary: SubtaskSummary
) -> BaseFailed | None:
    """The `BaseFailed` a resolver's summary means, or None when it finished `done`."""
    if summary.status == "stopped":
        return BaseFailed(_stopped_detail(tip, branch, worktree, summary), stopped=True)
    if summary.status != "done":
        return BaseFailed(_resolver_detail(tip, branch, worktree, summary))
    return None


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
    runner_factory: runs.RunnerFactory | None,
    stop: StopSignal | None,
    resume_from: store_checkpoints.Checkpoint | None = None,
) -> BaseResult:
    """Cut `root.branch` from `tips[0]`, merge every other tip, verify once.

    `tips` are the blockers' tips in `root.blockers` order. An existing branch
    or worktree is reused, and a tip already inside the base is reported as
    `already_merged`, so a relaunch re-merges nothing. A conflict needs
    `store`, `story_id` and `runner_factory` to reach the resolver, and fails
    for a human without them; `run_id` defaults to the store's, and `stop=None`
    means nothing can stop the resolver.

    `resume_from` (card 54e4ec29) is the resolver's checkpoint from an
    interrupted run. The resolver is continued from it first, before any
    tip is merged, because an unfinished merge it left would refuse every
    `merge_tip`. The tip it was resolving is the one `MERGE_HEAD` names; a
    tip it finished counts as `merged` and `resolved`. With no merge in
    progress (it was parked after committing) the tip is unknown, so it is
    reported as the loop finds it, `already_merged`.
    """
    tips = list(tips)
    if not tips:
        raise ValueError(
            f"bases.build needs at least one blocker tip to build {root.branch}, got none"
        )
    repo = Path(repo_dir).resolve()
    worktree = runs.worktree_for(repo, root.branch)
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
    if resume_from is not None:
        if store is None or story_id is None or runner_factory is None:
            raise BaseFailed(
                f"cannot continue the resolver of the merged base {root.branch}: "
                "continuing it needs a store, a story id and a runner factory; the "
                f"worktree is left as it is in {worktree}"
            )
        resumed_tip = await asyncio.to_thread(_in_progress_tip, worktree, tips[1:])
        label = resumed_tip if resumed_tip is not None else "the tip it was resolving"
        store.record_story(_bases_story())
        story_recorded = True
        summary = await _resolve_conflict(
            story_id=story_id,
            tip=label,
            files=[],
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
            resume_from=resume_from,
        )
        failure = _resolver_failure(label, root.branch, worktree, summary)
        if failure is not None:
            raise failure
        if resumed_tip is not None:
            resolved.append(resumed_tip)

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
                store.record_story(_bases_story())
                story_recorded = True
            summary = await _resolve_conflict(
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
            failure = _resolver_failure(tip, root.branch, worktree, summary)
            if failure is not None:
                raise failure
            resolved.append(tip)
        # A tip the resumed resolver finished is contained by now, but this
        # run merged it: it is reported merged, not already merged.
        if result["already_merged"] and tip not in resolved:
            already_merged.append(tip)
        else:
            merged.append(tip)

    verdict = await asyncio.to_thread(
        _verify, suite, allow_no_verification, root.branch, worktree
    )
    if verdict is not None:
        raise BaseFailed(verdict)

    return BaseResult(
        branch=root.branch,
        merged=merged,
        already_merged=already_merged,
        resolved=resolved,
    )
