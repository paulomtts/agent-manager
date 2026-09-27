"""The milestone runner (orchestration addendum O6; supervisor-tree T1-T6).

`run_milestone` drives every remaining subtask of one milestone through the
shared per-subtask driver (O4) on one event loop: `asyncio.run(supervise(...))`.
`supervise` builds one `grafo.Node` per census story -- done ones included,
every one with `timeout=None` -- and one edge per in-milestone blocker, so a
`grafo.TreeExecutor` starts each story the moment all its blockers succeeded.
A story's lane takes one of `max_concurrent` slots only once it has started,
so a waiting story never holds a slot, and its subtasks stay strictly
sequential. Levels are no longer barriers; they stay in the report as waves.
A lane fails by raising `LaneEscalated` or `LaneStopped`, so grafo never
releases the dependents of a lane that did not finish clean, and
`collect_outcomes` reads every story's outcome after the tree ran (T6).

Every derivation belongs to a collaborator: the milestone and its census to
`census`, waves, stack bases, roots and tips to `dag`, board reads to `board`,
rollup to `steps.rollup`, git to `steps.worktree.run_git`, run state to
`Store`. The terminal merge of every story tip belongs to `integration`. This
module decides only the order of those calls and what a run records.

The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle, a story whose root would be a merged base -- runs before the
first write, so a refusal leaves no run directory, no store, no fetch and no
prune behind.

`cli` is imported as a module and every name on it is read at call time: the
CLI wiring card makes `cli` import this module, and binding a `cli` name at
import or definition time would break under that circular import. The clock
default is this module's own `_utcnow` for the same reason.

The module holds no mutable state of its own (O4). A run's `StopSignal` is
created by `run_milestone` for that run. This is the only module that imports
`grafo`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

import grafo

from agent_manager import board, census, cli, dag, integration, models
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps import rollup, worktree
from agent_manager.store import Checkpoint, Store

MILESTONE_WORKFLOW = "milestone"
"""The run's `workflow` field: a milestone run, distinct from `run --card`'s `task`."""


GRAFO_LOGGER = "grafo"
"""grafo's logger. It logs every failing node with a traceback on its own
handler; `supervise` silences it so stdout stays one JSON line (T6)."""


STOPPED_PREFIX = "stopped before "
"""How `walk._stop` opens a stopped subtask's `detail` (addendum P4)."""


def stopped_before_phase(detail: str | None) -> str | None:
    """The phase a stopped subtask would have run next, read out of its detail.

    `walk._stop` writes `"stopped before <phase>"` and the summary has no
    field of its own for the phase, so this strips the prefix. A detail without
    the prefix, or no detail at all, gives None.
    """
    if detail is None or not detail.startswith(STOPPED_PREFIX):
        return None
    return detail[len(STOPPED_PREFIX):]


LaneKind = Literal["done", "escalated", "stopped", "pending"]
"""How one story's lane ended (supervisor-tree T6): finished, escalated, stopped
(parked by the stop, or saw it before a subtask), or never started by the tree."""


@dataclass(frozen=True)
class LaneOutcome:
    """What one story's lane did. Internal state, so a dataclass (CLAUDE.md).

    `subtask` is the subtask that escalated or was stopped before. `failed_phase`
    and `detail` describe an escalation, `before_phase` a stop. `completed` and
    `warnings` are this lane's own, in the order they arrived; `run_milestone`
    merges them across lanes in wave order. `story` and `level` are `None` only
    for an error no lane raised, which nothing ties to one story (T6).
    `primary` marks the first escalation in `executor.errors`.
    """

    kind: LaneKind
    story: str | None
    level: int | None
    subtask: str | None = None
    failed_phase: str | None = None
    detail: str | None = None
    before_phase: str | None = None
    completed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    primary: bool = False


class LaneEscalated(Exception):
    """A lane's escalation, raised so grafo never releases its dependents (T6)."""

    def __init__(self, outcome: LaneOutcome) -> None:
        super().__init__(
            f"story {outcome.story} escalated at {outcome.subtask}: {outcome.detail}"
        )
        self.outcome = outcome


class LaneStopped(Exception):
    """A lane that parked, or saw the stop before a subtask (T6)."""

    def __init__(self, outcome: LaneOutcome) -> None:
        super().__init__(f"story {outcome.story} stopped before {outcome.subtask}")
        self.outcome = outcome


def escalated_payload(
    run_id: str,
    primary_story: str | None,
    outcomes: Sequence[LaneOutcome],
    warnings: list[str],
) -> dict[str, Any]:
    """The escalated result for a run's lane outcomes, given in wave order.

    The top-level keys describe the primary escalation, as the sequential
    runner always did. `also_escalated` lists the other escalations and
    `stopped` the parked lanes, both in census order; `completed` is every
    subtask a stopped lane finished before it saw the stop, in the same order.
    Each key is present only when its list is non-empty. A `primary_story`
    that names no escalated outcome falls back to the first escalation in
    census order.
    """
    escalations = [outcome for outcome in outcomes if outcome.kind == "escalated"]
    primary = next(
        (outcome for outcome in escalations if outcome.story == primary_story),
        escalations[0],
    )
    payload: dict[str, Any] = {
        "escalated": True,
        "run_id": run_id,
        "level": primary.level,
        "story": primary.story,
        "subtask": primary.subtask,
        "failed_phase": primary.failed_phase,
        "detail": primary.detail,
        "warnings": warnings,
    }
    also = [
        {
            "level": outcome.level,
            "story": outcome.story,
            "subtask": outcome.subtask,
            "failed_phase": outcome.failed_phase,
            "detail": outcome.detail,
        }
        for outcome in escalations
        if outcome is not primary
    ]
    stopped = [
        {"story": outcome.story, "subtask": outcome.subtask, "before_phase": outcome.before_phase}
        for outcome in outcomes
        if outcome.kind == "stopped"
    ]
    completed = [
        subtask
        for outcome in outcomes
        if outcome.kind == "stopped"
        for subtask in outcome.completed
    ]
    if also:
        payload["also_escalated"] = also
    if stopped:
        payload["stopped"] = stopped
    if completed:
        payload["completed"] = completed
    return payload


def integrated_payload(outcome: integration.IntegrateSuccess) -> dict[str, Any]:
    """A clean run's `integrated` key: where every story tip now lives (addendum I6).

    `worktree` is a `str`, so the payload is plain JSON before `render` ever
    sees it. `merged` and `resolved` are story ids in merge order.
    """
    return {
        "branch": outcome.branch,
        "worktree": str(outcome.worktree),
        "merged": list(outcome.merged),
        "resolved": list(outcome.resolved),
    }


def integrate_escalated_payload(
    run_id: str, outcome: integration.IntegrateEscalation, warnings: list[str]
) -> dict[str, Any]:
    """The result of a run that stopped at Integrate (addendum I5).

    `escalated: true` is what `am run` reads for its exit code, as for a lane
    escalation. `story` is `None` when the final verification failed rather
    than a tip. The branch and worktree are left as Integrate left them.
    """
    return {
        "escalated": True,
        "phase": outcome.phase,
        "story": outcome.story,
        "files": list(outcome.files),
        "detail": outcome.detail,
        "run_id": run_id,
        "warnings": warnings,
    }


def _utcnow() -> datetime:
    """This module's own clock default. `cli._utcnow` is private, and binding a
    `cli` name at definition time would break under the circular import."""
    return datetime.now(timezone.utc)


class Driver(Protocol):
    """`cli.drive_subtask_async`'s keyword signature: drive one subtask, report the result.

    The seam the tests replace. Awaited by the lane on the run's one event
    loop (T3). Annotations are strings (`from __future__ import annotations`),
    so no `cli` name is resolved when this module is imported.

    `stop` is the run's `StopSignal`, passed on every call. `should_stop` stays
    until Task 3.3 deletes it; the lane never passes it. `resume_from` (card
    02890d5d) is passed only when a relaunch found a checkpoint to continue,
    so a driver written before it keeps working.
    """

    async def __call__(
        self,
        *,
        store: Store,
        run_id: str,
        card: models.Card,
        parent: models.Card,
        subtask: models.SubtaskRun,
        repo_dir: Path,
        commands: Sequence[str] = (),
        allow_no_verification: bool = False,
        runner_factory: cli.RunnerFactory | None = None,
        should_stop: Callable[[], bool] | None = None,
        stop: StopSignal | None = None,
        resume_from: Checkpoint | None = None,
    ) -> cli.SubtaskDrive: ...


@dataclass(frozen=True)
class PlannedStory:
    """One pending story with its level and its derived stack geometry.

    Internal state that crosses no process boundary, so a dataclass
    (CLAUDE.md). `bases` covers the story's FULL ordered subtask list, done
    ones included, so a done first subtask still anchors the second (O2).
    """

    story: census.StoryPlan
    level: int
    bases: dict[str, str]
    tip: str

    @property
    def remaining(self) -> list[census.SubtaskPlan]:
        """The subtasks still to drive, in census order."""
        return dag.remaining_subtasks(self.story)


def _merged_root_behind(
    story: census.StoryPlan,
    stories_by_id: dict[str, census.StoryPlan],
    branch_prefix: str,
    base_branch: str,
) -> tuple[census.StoryPlan, dag.RootPlan] | None:
    """The merged root this story's stack would build on, and whose it is, or None.

    A story's own root can be merged, or its single blocker can have no
    subtasks and fall through to a root that is merged -- the same fall-through
    `dag.story_tip` takes. Only that path is followed. The cycle check has
    already run, so the walk ends.
    """
    current = story
    while True:
        root = dag.story_root(current, stories_by_id, branch_prefix, base_branch)
        if root.kind == "merged":
            return current, root
        if root.kind == "base":
            return None
        blocker = stories_by_id[root.blockers[0]]
        if blocker.subtasks:
            return None
        current = blocker


def _merged_root_error(
    story: census.StoryPlan, root: dag.RootPlan, base_branch: str
) -> dag.StackRootError:
    """Today's refusal, word for word, for a story whose root would be merged."""
    listed = ", ".join(f"#{dep}" for dep in root.blockers)
    return dag.StackRootError(
        f"dag: story #{story.id} is blocked by {len(root.blockers)} stories ({listed}), "
        "and a stack can only root on ONE parent branch. Merge those blockers into "
        f"{base_branch} first, or restructure the dependencies so this story has "
        "a single blocker."
    )


def plan_levels(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[list[PlannedStory]]:
    """Dispatch levels with each pending story's bases and tip, derived before any write.

    The same composition as `cli.dry_run_payload`: the cycle check runs first,
    because a cycle is what breaks the geometry, and `stories_by_id` covers
    every story, done ones included, so a story blocked by a done story still
    roots on that story's tip. A story whose root is `dag.RootPlan` kind
    `"merged"` -- two or more in-milestone blockers, directly or through a
    subtask-less blocker -- raises `dag.StackRootError` here, before any
    geometry is returned: the dry run shows a merged root, but nothing builds
    merged bases yet (Task 3.2).
    """
    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    stories_by_id = {story.id: story for story in stories}
    planned: list[list[PlannedStory]] = []
    for index, level in enumerate(dag.compute_levels(stories)):
        for story in level:
            merged = _merged_root_behind(story, stories_by_id, branch_prefix, base_branch)
            if merged is not None:
                raise _merged_root_error(*merged, base_branch)
        planned.append(
            [
                PlannedStory(
                    story=story,
                    level=index,
                    bases=dag.stack_bases(story, stories_by_id, branch_prefix, base_branch),
                    tip=dag.story_tip(story, stories_by_id, branch_prefix, base_branch),
                )
                for story in level
            ]
        )
    return planned


def story_tips(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[dict[str, str]]:
    """Every census story that has subtasks, with the branch its stack ends on.

    Every story's own branch, reported beside `integrated`, which names the one branch they were merged into.
    A story with no subtasks contributes no branch of its own, so it is left
    out.
    """
    stories = list(stories)
    stories_by_id = {story.id: story for story in stories}
    return [
        {
            "story": story.id,
            "tip": dag.story_tip(story, stories_by_id, branch_prefix, base_branch),
        }
        for story in stories
        if story.subtasks
    ]


@dataclass(frozen=True)
class SupervisorPlan:
    """What `supervise` schedules (T1). Internal state, so a dataclass.

    `stories` is every census story, done ones included, in census order: each
    becomes a node. `roots` and `tips` cover all of them. `levels` are the
    pending stories' waves from `plan_levels`, and `rows` their store rows from
    `record_plan`.
    """

    stories: tuple[census.StoryPlan, ...]
    levels: tuple[tuple[PlannedStory, ...], ...]
    roots: dict[str, dag.RootPlan]
    tips: dict[str, str]
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]

    @property
    def planned(self) -> dict[str, PlannedStory]:
        """The pending stories by id, in wave order."""
        return {planned.story.id: planned for level in self.levels for planned in level}


def supervisor_plan(
    stories: Sequence[census.StoryPlan],
    levels: Sequence[Sequence[PlannedStory]],
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]],
    *,
    branch_prefix: str,
    base_branch: str,
) -> SupervisorPlan:
    """Every census story's root and tip beside the pending waves and their rows.

    Pure. `plan_levels` has already run the cycle check and refused `merged`
    roots for every pending story, so this derives geometry and refuses nothing.
    """
    stories = tuple(stories)
    by_id = {story.id: story for story in stories}
    return SupervisorPlan(
        stories=stories,
        levels=tuple(tuple(level) for level in levels),
        roots={
            story.id: dag.story_root(story, by_id, branch_prefix, base_branch)
            for story in stories
        },
        tips={
            story.id: dag.story_tip(story, by_id, branch_prefix, base_branch)
            for story in stories
        },
        rows=rows,
    )


def collect_outcomes(
    plan: SupervisorPlan,
    nodes: Mapping[str, Any],
    errors: Sequence[BaseException],
    finished: Mapping[str, LaneOutcome],
) -> list[LaneOutcome]:
    """One outcome per pending story in wave order, then one per foreign error (T6).

    A node with an output is `done` (its lane's finished outcome). A
    `LaneEscalated` gives its outcome, the first in `errors` marked `primary`.
    A `LaneStopped` gives its outcome. Anything else in `errors` escaped every
    lane's catch-all, so it is `escalated` with `"<Type>: <msg>"` and tied to no
    story. No output and no error is `pending`.
    """
    escalated: dict[str, LaneOutcome] = {}
    stopped: dict[str, LaneOutcome] = {}
    foreign: list[LaneOutcome] = []
    for error in errors:
        if isinstance(error, LaneEscalated):
            outcome = error.outcome if escalated else replace(error.outcome, primary=True)
            escalated.setdefault(outcome.story, outcome)
        elif isinstance(error, LaneStopped):
            stopped.setdefault(error.outcome.story, error.outcome)
        else:
            foreign.append(
                LaneOutcome(
                    kind="escalated",
                    story=None,
                    level=None,
                    detail=f"{type(error).__name__}: {error}",
                )
            )
    outcomes: list[LaneOutcome] = []
    for level in plan.levels:
        for planned in level:
            story_id = planned.story.id
            node = nodes.get(story_id)
            if node is not None and node.output is not None and story_id in finished:
                outcomes.append(finished[story_id])
            elif story_id in escalated:
                outcomes.append(escalated[story_id])
            elif story_id in stopped:
                outcomes.append(stopped[story_id])
            else:
                outcomes.append(LaneOutcome(kind="pending", story=story_id, level=planned.level))
    return outcomes + foreign


def refresh_git(root: Path) -> None:
    """Once per run: `git fetch origin` if an `origin` remote exists, then `git worktree prune`.

    A repo with no `origin` skips the fetch silently. The name must equal
    `origin` exactly: `upstream` or `origin-mirror` is not it. Both calls go
    through `worktree.run_git`, read at call time, and a `GitError` from any
    of them propagates.
    """
    remotes = worktree.run_git(["-C", str(root), "remote"]).split()
    if "origin" in remotes:
        worktree.run_git(["-C", str(root), "fetch", "origin"])
    worktree.run_git(["-C", str(root), "worktree", "prune"])


def stale_story_anchors(
    stories: Sequence[census.StoryPlan],
) -> list[tuple[census.StoryPlan, census.SubtaskPlan]]:
    """Each stale story paired with the subtask its rollup is re-run through.

    A port of `storyRollupAnchor`. A story is stale when it is not closed but
    has no remaining subtasks: every subtask is done and the story card never
    caught up, for example because an earlier run died between the last
    `mark_done` and its rollup. The anchor is its last individually done
    subtask in census order. A story with no done subtask, such as one with no
    subtasks at all, has no anchor and is skipped.
    """
    anchors: list[tuple[census.StoryPlan, census.SubtaskPlan]] = []
    for story in stories:
        if dag.is_story_closed(story) or dag.remaining_subtasks(story):
            continue
        done = [subtask for subtask in story.subtasks if dag.is_subtask_done(subtask)]
        if done:
            anchors.append((story, done[-1]))
    return anchors


def reroll_stale_stories(stories: Sequence[census.StoryPlan], root: Path) -> list[str]:
    """Re-run the rollup through each stale story's anchor, and return warnings.

    Writing `done` to a subtask that is already done is harmless, and the
    rollup's walk to the root repairs the story and the milestone above it.
    Best effort, like `TASK`'s `mark_done`: a `BoardError` becomes a
    warning naming the story and its anchor, and the run goes on.
    """
    warnings: list[str] = []
    for story, anchor in stale_story_anchors(stories):
        try:
            rollup.set_status(anchor.id, "done", repo_dir=root)
        except board.BoardError as error:
            warnings.append(
                f"could not re-roll stale story {story.id} through subtask {anchor.id}: {error}"
            )
    return warnings


def record_plan(
    store: Store,
    levels: list[list[PlannedStory]],
    *,
    root: Path,
    branch_prefix: str,
) -> dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]:
    """Record every pending story and its remaining subtasks `pending`, and return the rows.

    Written before the walk so `status` shows the whole plan even for a run
    that dies on its first phase. The rows come back keyed by story id, each
    with its subtask rows keyed by subtask id, so the walk records transitions
    as copies of exactly what was planned.
    """
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]] = {}
    for level in levels:
        for planned in level:
            story_row = models.StoryRun(
                card_id=planned.story.id,
                title=planned.story.title,
                level=planned.level,
                status="pending",
                tip_branch=planned.tip,
            )
            store.record_story(story_row)
            subtask_rows: dict[str, models.SubtaskRun] = {}
            for subtask in planned.remaining:
                branch = dag.subtask_branch(branch_prefix, subtask)
                subtask_row = models.SubtaskRun(
                    card_id=subtask.id,
                    branch=branch,
                    base_branch=planned.bases[subtask.id],
                    status="pending",
                    worktree_path=cli.worktree_for(root, branch),
                )
                store.record_subtask(planned.story.id, subtask_row)
                subtask_rows[subtask.id] = subtask_row
            rows[planned.story.id] = (story_row, subtask_rows)
    return rows


async def lane(
    story: census.StoryPlan,
    *,
    plan: SupervisorPlan,
    store: Store,
    run_id: str,
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    slots: asyncio.Semaphore,
    stop: StopSignal,
    finished: dict[str, LaneOutcome],
) -> str:
    """One story's node coroutine (T1, T4, T6): drive its remaining subtasks, return its tip.

    A story with nothing left to run returns its tip without taking a slot.
    Otherwise the lane takes a slot -- grafo started it, so every blocker
    already succeeded -- and drives the remaining subtasks in census order,
    each on the base `record_plan` recorded for it. Before each subtask it
    checks the stop: if it fired, the story is recorded `stopped` and
    `LaneStopped` is raised with that subtask never driven. A `stopped`
    summary records the subtask and story `stopped` and raises `LaneStopped`.
    Any other non-`done` summary, or any `Exception` while handling a
    subtask (a lane bug), triggers the stop first and raises `LaneEscalated`
    at that subtask. `LaneEscalated`/`LaneStopped` pass through the catch-all
    unchanged. A `BaseException` is never caught.

    Each subtask's open checkpoint is looked up first
    (`cli.continuable_checkpoint`), inside the same `try`, and handed to the
    driver as `resume_from` when it can be continued.
    """
    planned = plan.planned.get(story.id)
    if planned is None:
        return plan.tips[story.id]
    story_row, subtask_rows = plan.rows[story.id]
    completed: list[str] = []
    warnings: list[str] = []

    def outcome(kind: LaneKind, subtask: str | None, **fields: Any) -> LaneOutcome:
        return LaneOutcome(
            kind=kind,
            story=story.id,
            level=planned.level,
            subtask=subtask,
            completed=tuple(completed),
            warnings=tuple(warnings),
            **fields,
        )

    async with slots:
        current: census.SubtaskPlan | None = None
        try:
            for position, subtask in enumerate(planned.remaining):
                current = subtask
                if stop.triggered:
                    store.record_story(story_row.model_copy(update={"status": "stopped"}))
                    raise LaneStopped(outcome("stopped", subtask.id))
                row = subtask_rows[subtask.id]
                card = await asyncio.to_thread(board.show, subtask.id, repo_dir=root)
                parent = await asyncio.to_thread(board.show, story.id, repo_dir=root)
                row = row.model_copy(update={"status": "started"})
                store.record_subtask(story.id, row)
                if position == 0:
                    store.record_story(story_row.model_copy(update={"status": "started"}))
                # Relaunch continuation (card 02890d5d): the keyword is passed
                # only when there is a row, so a driver that predates it works.
                extra: dict[str, Any] = {}
                checkpoint = cli.continuable_checkpoint(store, subtask.id)
                if checkpoint is not None:
                    extra["resume_from"] = checkpoint
                result = await drive(
                    store=store,
                    run_id=run_id,
                    card=card,
                    parent=parent,
                    subtask=row,
                    repo_dir=root,
                    commands=list(commands),
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    stop=stop,
                    **extra,
                )
                warnings.extend(result.warnings)
                summary = result.summary
                # A `stopped` summary is handled before the non-`done` branch:
                # a stop is not an escalation (P4).
                if summary.status == "stopped":
                    store.record_subtask(story.id, row.model_copy(update={"status": "stopped"}))
                    store.record_story(story_row.model_copy(update={"status": "stopped"}))
                    raise LaneStopped(
                        outcome(
                            "stopped",
                            subtask.id,
                            before_phase=stopped_before_phase(summary.detail),
                        )
                    )
                if summary.status != "done":
                    stop.trigger(story.id)
                    store.record_subtask(story.id, row.model_copy(update={"status": "escalated"}))
                    store.record_story(story_row.model_copy(update={"status": "escalated"}))
                    raise LaneEscalated(
                        outcome(
                            "escalated",
                            subtask.id,
                            failed_phase=summary.failed_phase,
                            detail=summary.detail,
                        )
                    )
                store.record_subtask(story.id, row.model_copy(update={"status": "done"}))
                completed.append(subtask.id)
            store.record_story(story_row.model_copy(update={"status": "done"}))
        except (LaneEscalated, LaneStopped):
            raise
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            stop.trigger(story.id)
            if current is not None:
                store.record_subtask(
                    story.id,
                    subtask_rows[current.id].model_copy(update={"status": "escalated"}),
                )
            store.record_story(story_row.model_copy(update={"status": "escalated"}))
            raise LaneEscalated(
                outcome(
                    "escalated",
                    None if current is None else current.id,
                    detail=f"{type(error).__name__}: {error}",
                )
            ) from error
    finished[story.id] = outcome("done", None)
    return planned.tip


async def supervise(
    plan: SupervisorPlan,
    *,
    store: Store,
    run_id: str,
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    max_concurrent: int,
    stop: StopSignal,
) -> list[LaneOutcome]:
    """Run every census story as a grafo node and collect the outcomes (T1, T6).

    One `grafo.Node` per story, `uuid=story.id`, `timeout=None` always (grafo's
    60 s default would cancel a lane mid-phase). One edge per in-milestone
    blocker, forwarding the blocker's tip as `tip_<short id>` (Task 3.2 reads
    those for merged bases). The executor's roots are the stories with no
    in-milestone blocker; a milestone with no story has no tree to run.

    The `grafo` logger is at CRITICAL for exactly this call: a lane's
    escalation is data in the outcomes, never a traceback on a stream, and
    grafo's own level is restored on every exit.
    """
    grafo_logger = logging.getLogger(GRAFO_LOGGER)
    level_before = grafo_logger.level
    grafo_logger.setLevel(logging.CRITICAL)
    try:
        slots = asyncio.Semaphore(max_concurrent)
        finished: dict[str, LaneOutcome] = {}

        def node_coroutine(story: census.StoryPlan) -> Callable[..., Any]:
            async def run(**tips: str) -> str:
                return await lane(
                    story,
                    plan=plan,
                    store=store,
                    run_id=run_id,
                    root=root,
                    drive=drive,
                    commands=commands,
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    slots=slots,
                    stop=stop,
                    finished=finished,
                )

            return run

        nodes = {
            story.id: grafo.Node(coroutine=node_coroutine(story), uuid=story.id, timeout=None)
            for story in plan.stories
        }
        for story in plan.stories:
            for blocker in plan.roots[story.id].blockers:
                await nodes[blocker].connect(
                    nodes[story.id], forward=f"tip_{dag.short_id(blocker)}"
                )
        roots = [nodes[story.id] for story in plan.stories if not plan.roots[story.id].blockers]
        errors: list[BaseException] = []
        if roots:
            executor = grafo.TreeExecutor(uuid=run_id, roots=roots)
            await executor.run()
            errors = list(executor.errors)
        return collect_outcomes(plan, nodes, errors, finished)
    finally:
        grafo_logger.setLevel(level_before)


def run_milestone(
    milestone: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: cli.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
) -> dict[str, Any]:
    """Drive every remaining subtask of `milestone` as a grafo tree, and report (O6, T1-T6).

    `milestone` is a card id or a title needle (O1). Everything that can refuse,
    `max_concurrent < 1` included, runs before the store is opened. Then one
    `milestone` run is recorded with its whole plan `pending`, and
    `asyncio.run(supervise(...))` runs every story the moment its blockers
    succeeded, at most `max_concurrent` at once. A subtask already `done` on
    the board is never driven, but its branch still anchors the next
    subtask's base. The card and its story are read fresh from the board
    before each subtask. The default driver is `cli.drive_subtask_async`,
    read at call time.

    The first escalation triggers the run's `StopSignal`: running subtasks
    park at their next phase boundary and are recorded `stopped`, a lane
    between subtasks or waiting for a slot ends `stopped` without driving
    anything more, and grafo starts no dependent of a failed lane, so those
    stories stay `pending`.

    When every lane finished clean -- or none had anything to run --
    Integrate folds every story tip into `<branch_prefix>-integrate` before
    the run is recorded. Success records `done` and adds `integrated`; an
    Integrate escalation records `escalated` and returns
    `integrate_escalated_payload`. An exception from Integrate propagates and
    the run is never recorded `done`.
    """
    if max_concurrent < 1:
        raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
    root = cli.resolve_repo_dir(repo_dir)
    milestone_card = census.find_milestone(board.roots(repo_dir=root), milestone)
    plan = census.flatten_milestone(board.tree(milestone_card.id, repo_dir=root))
    levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    tips = story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    drive = cli.drive_subtask_async if driver is None else driver

    # The first side effect. It runs after every refusal and before the store
    # is opened, so a failed fetch leaves no run directory behind.
    refresh_git(root)

    started_at = clock()
    run_id = cli.mint_run_id(milestone_card.id, started_at)
    store = Store.open(root, run_id)
    try:
        run_record = models.Run(
            id=run_id,
            workflow=MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(max_concurrent_stories=max_concurrent),
        )
        store.record_run(run_record)
        rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
        warnings = reroll_stale_stories(plan.stories, root)
        completed: list[str] = []
        stop = StopSignal()

        outcomes = asyncio.run(
            supervise(
                supervisor_plan(
                    plan.stories,
                    levels,
                    rows,
                    branch_prefix=branch_prefix,
                    base_branch=base_branch,
                ),
                store=store,
                run_id=run_id,
                root=root,
                drive=drive,
                commands=list(commands),
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                max_concurrent=max_concurrent,
                stop=stop,
            )
        )
        # Wave order, census order within a wave, never finish order.
        for outcome in outcomes:
            completed.extend(outcome.completed)
            warnings.extend(outcome.warnings)
        if any(outcome.kind == "escalated" for outcome in outcomes):
            store.record_run(run_record.model_copy(update={"status": "escalated"}))
            primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
            return escalated_payload(run_id, primary, outcomes, warnings)

        # Integrate (addendum I6) runs only once every lane finished clean,
        # and also when there was nothing left to drive: that is how a relaunch
        # retries an Integrate escalation, and why a finished milestone's
        # relaunch is a no-op merge. Read as `integration.integrate_milestone`
        # so a test can replace it, as `driver` is. It needs a factory for a
        # conflicting tip; `None` is production's, read off `cli` now.
        factory = cli.default_runner_factory if runner_factory is None else runner_factory
        outcome = integration.integrate_milestone(
            stories=plan.stories,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            commands=list(commands),
            allow_no_verification=allow_no_verification,
            store=store,
            run_id=run_id,
            runner_factory=factory,
        )
        if isinstance(outcome, integration.IntegrateEscalation):
            # The branch and worktree stay exactly as Integrate left them (I5).
            store.record_run(run_record.model_copy(update={"status": "escalated"}))
            return integrate_escalated_payload(run_id, outcome, warnings)

        store.record_run(run_record.model_copy(update={"status": "done"}))
        return {
            "done": True,
            "run_id": run_id,
            "levels": [
                {"level": index, "stories": [planned.story.id for planned in level]}
                for index, level in enumerate(levels)
            ],
            "completed": completed,
            "tips": tips,
            "warnings": warnings,
            "integrated": integrated_payload(outcome),
        }
    finally:
        store.close()
