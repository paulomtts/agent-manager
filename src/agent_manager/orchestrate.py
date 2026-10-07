"""The milestone runner (orchestration addendum O6; supervisor-tree T1-T6).

`run_milestone` drives every remaining subtask of one milestone through the
shared per-subtask driver (O4) on one event loop, under the run's
`control.Lease`: `asyncio.run(control.controlled(supervise(...)))`.
`supervise` builds one `grafo.Node` per census story -- done ones included,
every one with `timeout=None` -- and one edge per in-milestone blocker, so a
`grafo.TreeExecutor` starts each story the moment all its blockers succeeded.
A story's lane takes one of `max_concurrent` slots only once it has started,
so a waiting story never holds a slot, and its subtasks stay strictly
sequential. Levels are no longer barriers; they stay in the report as waves.
A lane fails by raising `LaneEscalated` or `LaneStopped`, so grafo never
releases the dependents of a lane that did not finish clean, and
`collect_outcomes` reads every story's outcome after the tree ran (T6).

A story with two or more in-milestone blockers roots on a merged base
(supervisor-tree §5): it is reached through one grafo edge per blocker, so its
lane runs only after every blocker succeeded. The lane reads the blockers' tips
from `plan.tips` in `root_plan.blockers` order and awaits `bases.build` with
them, after it took its slot and before its first subtask, so that subtask
stacks on `<prefix>/base-<short id>`. A lone-blocker story stays the fast path:
no base branch and no extra verify.

Every derivation belongs to a collaborator: the milestone and its census to
`census`, waves, stack bases, roots and tips to `dag`, board reads to `board`,
rollup to `steps.rollup`, git to `steps.worktree.run_git`, run state to
`Store`. The terminal merge of every story tip belongs to `integration`. This
module decides only the order of those calls and what a run records.

The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle -- runs before the first write, so a refusal leaves no run
directory, no store, no fetch and no prune behind.

`preflight_story` is the same stage 1 for one story of a milestone: it
returns a `MilestonePreflight` whose plan holds only that story and the done
blockers it stacks on, and refuses an open blocker (`errors.StoryBlockedError`)
before the claims. `run_story` runs it through the same engine, which skips
Integrate for a story run; `preflight_milestone` cuts a resumed story run's
plan the same way.

The run helpers S1 moved out of the Typer module (`RunnerFactory`, the resume
error types, `mint_run_id`, `resolve_repo_dir`, `worktree_for`,
`orphan_attempts`, `continuable_checkpoint`) are read off `runs`. `cli` is
still imported, as a module, for what it alone defines -- the production
`default_runner_factory` and the `drive_subtask_async` driver -- and every
name on it is read at call time: `cli` imports this module, and binding a
`cli` name at import or definition time would break under that circular
import. The clock default is this module's own `_utcnow` for the same reason.

The module holds no mutable state of its own (O4). A run's `StopSignal` is
created by `run_milestone` for that run. This is the only module that imports
`grafo`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

import grafo

from agent_manager import (
    bases,
    board,
    census,
    cli,
    comments,
    control,
    dag,
    detach,
    errors,
    integration,
    models,
    paths,
    runs,
)
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import Command, StopSignal
from agent_manager.steps import rollup, worktree
from agent_manager.store import Store
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import queries as store_queries
from agent_manager.workflow import integrate as integrate_workflow
from agent_manager.workflow import task as task_workflow
from agent_manager.workflow.phases import Workflow

MILESTONE_WORKFLOW = "milestone"
"""The run's `workflow` field: a milestone run, distinct from `run --card`'s `task`."""


GRAFO_LOGGER = "grafo"
"""grafo's logger. It logs every failing node with a traceback on its own
handler; `supervise` silences it so stdout stays one JSON line (T6)."""


LaneKind = Literal["done", "escalated", "stopped", "pending"]
"""How one story's lane ended (supervisor-tree T6): finished, escalated, stopped
(parked by the stop, or saw it before a subtask), or never started by the tree."""


T = TypeVar("T")
"""An item `build_dag_tree` turns into one grafo node."""


@dataclass(frozen=True)
class LaneOutcome:
    """What one story's lane did. Internal state, so a dataclass (CLAUDE.md).

    `subtask` is the subtask that escalated or was stopped before. `failed_phase`
    and `detail` describe an escalation, `before_phase` a stop. `completed` and
    `warnings` are this lane's own, in the order they arrived; `run_milestone`
    merges them across lanes in wave order. `story` and `level` are `None` only
    for an error no lane raised, which nothing ties to one story (T6).
    `primary` marks the first escalation in `executor.errors`. `base` is the
    merged base this lane built, as the story's `dag.RootPlan`, or None when it
    built none; `run_milestone` reports it under `bases` (supervisor-tree §5).
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
    base: dag.RootPlan | None = None


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


def stopped_row(outcome: LaneOutcome) -> dict[str, Any]:
    """A stopped lane as every payload lists it: the subtask it stopped at and
    the phase it parked before (None when it stopped between subtasks)."""
    return {
        "story": outcome.story,
        "subtask": outcome.subtask,
        "before_phase": outcome.before_phase,
    }


def escalation_row(outcome: LaneOutcome) -> dict[str, Any]:
    """An escalated lane as `also_escalated` and `escalations` list it."""
    return {
        "level": outcome.level,
        "story": outcome.story,
        "subtask": outcome.subtask,
        "failed_phase": outcome.failed_phase,
        "detail": outcome.detail,
    }


def controlled_payload(
    run_id: str,
    command: Command,
    outcomes: Sequence[LaneOutcome],
    warnings: list[str],
) -> dict[str, Any]:
    """The result of a run a control ended (live control C12), outcomes in wave order.

    `paused` or `canceled`, then `run_id`, `stopped` (census order, the
    `escalated_payload` row shape), `completed` (every lane's finished
    subtasks, wave order), `pending` (story ids) and `warnings`. A pause adds
    the `resume` hint. A cancel adds `escalations` only when a lane really
    escalated, primary first -- the outcome marked `primary`, else the first
    in census order. There is never an `escalated` key: a control is not an
    escalation.
    """
    payload: dict[str, Any] = {
        "paused" if command == "pause" else models.CANCELED: True,
        "run_id": run_id,
        "stopped": [stopped_row(outcome) for outcome in outcomes if outcome.kind == "stopped"],
        "completed": [subtask for outcome in outcomes for subtask in outcome.completed],
        "pending": [outcome.story for outcome in outcomes if outcome.kind == "pending"],
        "warnings": warnings,
    }
    if command == "pause":
        payload["resume"] = f"am resume {run_id}"
        return payload
    escalations = [outcome for outcome in outcomes if outcome.kind == "escalated"]
    if escalations:
        primary = next((outcome for outcome in escalations if outcome.primary), escalations[0])
        payload["escalations"] = [escalation_row(primary)] + [
            escalation_row(outcome) for outcome in escalations if outcome is not primary
        ]
    return payload


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
    also = [escalation_row(outcome) for outcome in escalations if outcome is not primary]
    stopped = [stopped_row(outcome) for outcome in outcomes if outcome.kind == "stopped"]
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


def bases_payload(outcomes: Sequence[LaneOutcome]) -> list[dict[str, Any]]:
    """Every merged base a lane built this run, in the order the outcomes come.

    `collect_outcomes` gives wave order, so this is wave order. `blockers` is
    the story's `root_plan.blockers`, the order the tips were merged in.
    """
    return [
        {
            "story": outcome.story,
            "branch": outcome.base.branch,
            "blockers": list(outcome.base.blockers),
        }
        for outcome in outcomes
        if outcome.base is not None
    ]


def with_bases(payload: dict[str, Any], built: list[dict[str, Any]]) -> dict[str, Any]:
    """`payload` with `bases` set, only when a base was built: every payload
    shape carries the key on the same terms (spec, Report)."""
    if built:
        payload["bases"] = built
    return payload


def _utcnow() -> datetime:
    """This module's own clock default. `cli._utcnow` is private, and binding a
    `cli` name at definition time would break under the circular import."""
    return datetime.now(timezone.utc)


def post_comment(
    store: Store, root: Path, comment: comments.Comment, *, run_id: str
) -> list[str]:
    """Queue `comment`, then flush its card's pending rows; the flush's warnings.

    Called only after the outcome it describes is recorded (board-comments B2).
    A board failure comes back as a warning, never an exception (B8); a lost
    lease propagates like any fenced write. Flushing by card also posts any
    earlier run's leftover row for that card.
    """
    comments.enqueue(store, comment, run_id=run_id, now=_utcnow())
    return comments.flush(store, root, card_ids=[comment.card_id])


async def post_comment_async(
    store: Store, root: Path, comment: comments.Comment, *, run_id: str
) -> list[str]:
    """`post_comment` off the run's event loop: a flush runs `brd`, a blocking
    subprocess, so a lane awaits it in a worker thread as it awaits `board.show`."""
    return await asyncio.to_thread(post_comment, store, root, comment, run_id=run_id)


class Driver(Protocol):
    """`cli.drive_subtask_async`'s keyword signature: drive one subtask, report the result.

    The seam the tests replace. Awaited by the lane on the run's one event
    loop (T3). Annotations are strings (`from __future__ import annotations`),
    so no `cli` name is resolved when this module is imported.

    `stop` is the run's `StopSignal`, passed on every call; it is the only
    stop. `resume_from` (card 02890d5d) is passed only when a relaunch found a
    checkpoint to continue, so a driver written before it keeps working.
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
        runner_factory: runs.RunnerFactory | None = None,
        stop: StopSignal | None = None,
        resume_from: store_checkpoints.Checkpoint | None = None,
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


def plan_levels(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[list[PlannedStory]]:
    """Dispatch levels with each pending story's bases and tip, derived before any write.

    The same composition as `runs.compute_dry_run_plan`: the cycle check runs first,
    because a cycle is what breaks the geometry, and `stories_by_id` covers
    every story, done ones included, so a story blocked by a done story still
    roots on that story's tip. A story rooted on a merged base -- its own, for
    two or more in-milestone blockers, or a subtask-less blocker's that it
    falls through to -- is planned like any other: `dag.stack_bases` roots its
    first subtask on that base branch, and the lane of the story that owns the
    base builds it before anything stacks on it (supervisor-tree §5).
    """
    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    stories_by_id = {story.id: story for story in stories}
    return [
        [
            PlannedStory(
                story=story,
                level=index,
                bases=dag.stack_bases(story, stories_by_id, branch_prefix, base_branch),
                tip=dag.story_tip(story, stories_by_id, branch_prefix, base_branch),
            )
            for story in level
        ]
        for index, level in enumerate(dag.compute_levels(stories))
    ]


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
    `record_plan`. `resuming` is set on a milestone resume (card 54e4ec29),
    whose validated checkpoints, keyed by card id -- subtasks and
    `base-<story>` resolvers -- are `checkpoints`.
    """

    stories: tuple[census.StoryPlan, ...]
    levels: tuple[tuple[PlannedStory, ...], ...]
    roots: dict[str, dag.RootPlan]
    tips: dict[str, str]
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]
    checkpoints: Mapping[str, store_checkpoints.Checkpoint] = field(default_factory=dict)
    resuming: bool = False

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
    checkpoints: Mapping[str, store_checkpoints.Checkpoint] | None = None,
) -> SupervisorPlan:
    """Every census story's root and tip beside the pending waves and their rows.

    Pure. `plan_levels` has already run the cycle check, so this derives
    geometry and refuses nothing. `checkpoints` is given only on a resume,
    and marks the plan `resuming` even when it is empty.
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
        checkpoints=dict(checkpoints or {}),
        resuming=checkpoints is not None,
    )


def collect_outcomes(
    plan: SupervisorPlan,
    nodes: Mapping[str, Any],
    errors: Sequence[BaseException],
    finished: Mapping[str, LaneOutcome],
) -> list[LaneOutcome]:
    """One outcome per pending story in wave order, then one per base-only lane
    that ran, in census order, then one per foreign error (T6).

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
    # A base-only lane (`base_only_lane`) belongs to no wave: its outcome
    # follows the waves, in census order, so a failed base is never dropped.
    for story in plan.stories:
        if story.id in plan.planned:
            continue
        if story.id in finished:
            outcomes.append(finished[story.id])
        elif story.id in escalated:
            outcomes.append(escalated[story.id])
        elif story.id in stopped:
            outcomes.append(stopped[story.id])
    return outcomes + foreign


def refresh_git(root: Path) -> None:
    """Once per run: `git fetch origin` if an `origin` remote exists, then `git worktree prune`.

    A repo with no `origin` skips the fetch silently. The name must equal
    `origin` exactly: `upstream` or `origin-mirror` is not it. Both calls go
    through `worktree.run_git`, read at call time, and a `GitError` from any
    of them propagates.

    All three git calls run under `worktree.git_lock(root)` (spec X7), so the
    prune never sweeps while another `am` process is mid-`worktree add` on the
    same repository. A `locks.LockTimeoutError` propagates uncaught, before any
    git call, and reaches `cli.HANDLED` when the run has not started.
    """
    with worktree.git_lock(root):
        remotes = worktree.run_git(["-C", str(root), "remote"]).split()
        if "origin" in remotes:
            worktree.run_git(["-C", str(root), "fetch", "origin"])
        worktree.run_git(["-C", str(root), "worktree", "prune"])


REOPENED_STATUSES = ("stopped", "escalated", "started")
"""The row statuses a resume records `started` again (spec, point 2)."""


def resumable_milestone_run(root: Path, run_id: str) -> models.Run:
    """The recorded milestone run `run_id`, or the refusal that says why not.

    Read-only through the free `open_db` / `load_run`, like `cli.resume_run`:
    `Store.open` would construct a `Journal`. Refused, in this order (live
    control C9): an unknown run, a run of another workflow, then a run
    canceled in either spelling and a `done` run (card 54e4ec29, card 0e1edf31).
    """
    conn = store_db.open_db(root)
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise runs.UnknownRunError(
            f"run {run_id!r} is not in the projection for {root}"
            " (`agent-manager runs` lists the ones that are)"
        )
    if run.workflow != MILESTONE_WORKFLOW:
        raise runs.NotResumableError(
            f"run {run_id!r} is a {run.workflow!r} run, not a {MILESTONE_WORKFLOW!r} run"
        )
    if models.is_canceled(run.status):
        raise runs.NotResumableError(
            f"run {run_id} was canceled; start new work with am run --milestone"
        )
    if run.status == "done":
        raise runs.NotResumableError(
            f"run {run.id} finished; start new work with am run --milestone"
        )
    return run


def find_run_milestone(
    roots: Sequence[models.CardNode] | None, run: models.Run
) -> models.CardNode:
    """The root card a resumed milestone `run` drives.

    `run.milestone_id` is the milestone's full card id, recorded when the run
    started, and is authoritative: the root with exactly that id, or
    `runs.NotResumableError` if the board has none (the card was deleted or
    reparented). It never falls back to the short id, which could name a
    different milestone.

    A run recorded before `milestone_id` existed has None there. For those,
    `runs.mint_run_id` built the run id as `<timestamp>-<short milestone id>`,
    so the one root whose short id ends the run id is the milestone. Zero or
    several such roots is `runs.NotResumableError`. A title edit breaks
    neither lookup.
    """
    if run.milestone_id is not None:
        for node in roots or []:
            if node.id == run.milestone_id:
                return node
        raise runs.NotResumableError(
            f"run {run.id!r} belongs to milestone {run.milestone_id}, and no root"
            " card on the board has that id"
        )
    short = run.id.rsplit("-", 1)[-1]
    matches = [node for node in roots or [] if dag.short_id(node.id) == short]
    if len(matches) != 1:
        raise runs.NotResumableError(
            f"run {run.id!r} belongs to milestone {short}, and {len(matches)} root"
            " cards on the board have that short id"
        )
    return matches[0]


def open_cards(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[tuple[str, Workflow]]:
    """Every card a resume may continue, with the workflow its checkpoint must match.

    Each remaining subtask under `TASK`, then, for a story that is not closed
    and roots on a merged base, its resolver `base-<story id>` under
    `INTEGRATE`. Census order. `dag.assert_no_blocker_cycles` must have run.
    """
    stories = list(stories)
    by_id = {story.id: story for story in stories}
    cards: list[tuple[str, Workflow]] = []
    for story in stories:
        for subtask in dag.remaining_subtasks(story):
            cards.append((subtask.id, task_workflow.TASK))
        root_plan = dag.story_root(story, by_id, branch_prefix, base_branch)
        if root_plan.kind == "merged" and not dag.is_story_closed(story):
            cards.append((bases.resolver_card_id(story.id), integrate_workflow.INTEGRATE))
    return cards


def _refuse_changed_workflow(checkpoint: store_checkpoints.Checkpoint, workflow: Workflow, run_id: str) -> None:
    """`runs.CheckpointMismatchError` when `checkpoint` was saved under another digest.

    Worded like `cli.checkpoint_resume_phase`'s refusal, with the milestone
    remedy.
    """
    digest = workflow.digest()
    if checkpoint.digest != digest:
        raise runs.CheckpointMismatchError(
            f"workflow changed since checkpoint: checkpoint #{checkpoint.seq} of card"
            f" {checkpoint.card_id} in run {run_id!r} was saved under digest"
            f" {checkpoint.digest}, but workflow {workflow.name!r} now has digest"
            f" {digest}; start new work with am run --milestone"
        )


def resume_point(store: Store, card_id: str, workflow: Workflow) -> store_checkpoints.Checkpoint | None:
    """The checkpoint a resume continues `card_id` from, None to start it fresh, or a refusal.

    The newest row of `card_id` in this store's run decides. None, or `done`
    (only a board write was lost), starts the card fresh. Any other newest row
    is judged against `workflow`'s digest and refused on a mismatch. A row
    that holds a turn is continued as is; one that does not -- a phase
    escalation -- is rewound to the card's newest `turn` row, the turn the
    failing phase ran in, judged the same way.
    """
    newest = store.latest_checkpoint(card_id)
    if newest is None or newest.reason == "done":
        return None
    _refuse_changed_workflow(newest, workflow, store.run_id)
    if runtime_engine.pending_phase(newest) is not None:
        return newest
    turn = store.latest_turn_checkpoint(card_id)
    if turn is None:
        return None
    _refuse_changed_workflow(turn, workflow, store.run_id)
    return turn


def resume_checkpoints(
    store: Store, cards: Sequence[tuple[str, Workflow]]
) -> dict[str, store_checkpoints.Checkpoint]:
    """`resume_point` for every open card, keyed by card id, only where there is one.

    Reads only, so a refusal on any card leaves everything as it was: the
    whole resume is refused (spec, Error paths).
    """
    found: dict[str, store_checkpoints.Checkpoint] = {}
    for card_id, workflow in cards:
        checkpoint = resume_point(store, card_id, workflow)
        if checkpoint is not None:
            found[card_id] = checkpoint
    return found


def reopen_rows(store: Store, run: models.Run, open_card_ids: set[str]) -> None:
    """Mark every orphan attempt `harness_error`, then reopen the open rows.

    `run` is the tree as the interrupted run left it. An orphan is
    `runs.orphan_attempts`' in-flight attempt, marked as `cli`'s
    `_resume_from_checkpoint` marks it. A subtask or resolver row of an open
    card that is stopped, escalated or started is recorded `started`.
    """
    for story in run.stories:
        for subtask in story.subtasks:
            for phase, attempt in runs.orphan_attempts(subtask):
                store.record_attempt(
                    story.card_id,
                    subtask.card_id,
                    phase.name,
                    attempt.model_copy(update={"status": "harness_error"}),
                )
            if subtask.card_id in open_card_ids and subtask.status in REOPENED_STATUSES:
                store.record_subtask(
                    story.card_id, subtask.model_copy(update={"status": "started"})
                )


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
                    worktree_path=runs.worktree_for(root, branch),
                )
                store.record_subtask(planned.story.id, subtask_row)
                subtask_rows[subtask.id] = subtask_row
            rows[planned.story.id] = (story_row, subtask_rows)
    return rows


async def build_merged_base(
    story: census.StoryPlan,
    root_plan: dag.RootPlan,
    tips: Sequence[str],
    *,
    store: Store,
    run_id: str,
    root: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: runs.RunnerFactory | None,
    stop: StopSignal,
    resume_from: store_checkpoints.Checkpoint | None = None,
) -> None:
    """Await `bases.build` for one merged-root story (supervisor-tree §5).

    `tips` are the blockers' tips, read by the caller from `plan.tips` in
    `root_plan.blockers` order. `bases.build` is read off its module at call
    time so a test can replace it. A `None` factory is
    production's, `cli.default_runner_factory`, read at call time as
    Integrate reads it, so a conflicting tip reaches the resolver instead of
    failing for a human. `resume_from` is the resolver's checkpoint on a
    resume (card 54e4ec29), passed only when there is one.
    """
    factory = cli.default_runner_factory if runner_factory is None else runner_factory
    extra: dict[str, Any] = {}
    if resume_from is not None:
        extra["resume_from"] = resume_from
    await bases.build(
        root_plan,
        list(tips),
        repo_dir=root,
        commands=list(commands),
        allow_no_verification=allow_no_verification,
        store=store,
        run_id=run_id,
        story_id=story.id,
        runner_factory=factory,
        stop=stop,
        **extra,
    )


def builds_a_base_alone(story: census.StoryPlan, root_plan: dag.RootPlan) -> bool:
    """Whether a story with no subtasks must still build its merged base.

    Such a story is in no wave (`dag.compute_levels` drops it), but a story it
    blocks falls through to its root, so the branch must exist before that
    dependent runs. A closed story is left alone: a done story whose base was
    never built is milestone-wide resume's.
    """
    return (
        root_plan.kind == "merged"
        and not story.subtasks
        and not dag.is_story_closed(story)
    )


class StoryRecorder:
    """Every row one story's lane writes, and every `LaneOutcome` it builds (cleanup §S5).

    One per lane invocation, and it serves both lanes. `lane` builds it once
    the story's planned rows are known. `base_only_lane` builds it with
    `without_rows`: a subtask-less story has no rows, since `record_plan`
    records only stories with work, so that recorder writes nothing and only
    builds outcomes, all with `level=None`.

    It owns the outcome's state: the subtasks `completed` so far, the lane's
    `warnings`, and the merged `base` once it is built. Every outcome
    snapshots them at the moment it is built. The methods that end a lane
    write their rows and return the outcome for the lane to raise. The
    recorder never signals the stop: `stop.trigger` stays with the caller,
    right before an escalation's writes.

    Some transitions write only the story row: a stop seen before a subtask
    or before the base, and a base failure. So `stopped` and `escalated` take
    a keyword-only `subtask_row` that says which subtask row to write first,
    if any. `"started"` is the row `started` returned and the driver was
    handed. `"planned"` is `record_plan`'s row, which the catch-all writes for
    a subtask it may never have started. A row-less recorder has no subtask
    rows, so `started`, `subtask_done` and any `subtask_row` raise `KeyError`
    on it rather than write one.
    """

    def __init__(
        self,
        store: Store,
        story_id: str,
        level: int | None,
        story_row: models.StoryRun | None,
        subtask_rows: Mapping[str, models.SubtaskRun],
    ) -> None:
        self._store = store
        self._story_id = story_id
        self._level = level
        self._story_row = story_row
        self._subtask_rows = subtask_rows
        self._started: dict[str, models.SubtaskRun] = {}
        self._completed: list[str] = []
        self._warnings: list[str] = []
        self._base: dag.RootPlan | None = None

    @classmethod
    def without_rows(cls, store: Store, story_id: str) -> StoryRecorder:
        """A recorder for a story with no store rows: it writes nothing.

        Its outcomes have `level=None`, so `collect_outcomes` reports them
        after the waves.
        """
        return cls(store, story_id, None, None, {})

    def started(self, *, subtask_id: str, first: bool) -> models.SubtaskRun:
        """Record the subtask `started`, and the story too on its first subtask.

        Returns the started row, which is the one the driver is handed and
        the one every later write for this subtask copies.
        """
        row = self._subtask_rows[subtask_id].model_copy(update={"status": "started"})
        self._started[subtask_id] = row
        self._store.record_subtask(self._story_id, row)
        if first:
            self._record_story("started")
        return row

    def subtask_done(self, subtask_id: str, tip: str) -> None:
        """Record the subtask `done` and count it as completed.

        `tip` is the subtask's branch. No row field holds a tip, so it is not
        written.
        """
        row = self._started[subtask_id]
        self._store.record_subtask(self._story_id, row.model_copy(update={"status": "done"}))
        self._completed.append(subtask_id)

    def stopped(
        self,
        subtask_id: str | None,
        before_phase: str | None,
        *,
        subtask_row: Literal["started"] | None = None,
    ) -> LaneOutcome:
        """Record the story `stopped`, after the started subtask when there is one."""
        if subtask_row == "started":
            assert subtask_id is not None
            self._record_subtask(self._started[subtask_id], "stopped")
        self._record_story("stopped")
        return self._outcome("stopped", subtask_id, before_phase=before_phase)

    def escalated(
        self,
        subtask_id: str | None,
        phase: str | None,
        detail: str | None,
        *,
        subtask_row: Literal["started", "planned"] | None = None,
    ) -> LaneOutcome:
        """Record the story `escalated`, after the named subtask row when there is one."""
        if subtask_row is not None:
            assert subtask_id is not None
            source = (
                self._started[subtask_id]
                if subtask_row == "started"
                else self._subtask_rows[subtask_id]
            )
            self._record_subtask(source, "escalated")
        self._record_story("escalated")
        return self._outcome("escalated", subtask_id, failed_phase=phase, detail=detail)

    def base_built(self, base: dag.RootPlan) -> None:
        """The merged base is built: every later outcome carries it."""
        self._base = base

    def add_warnings(self, warnings: Sequence[str]) -> None:
        """Keep a driven subtask's warnings, in the order they arrived."""
        self._warnings.extend(warnings)

    def done(self) -> LaneOutcome:
        """Record the story `done` and return its outcome."""
        self._record_story("done")
        return self._outcome("done", None)

    def _record_story(self, status: str) -> None:
        if self._story_row is None:
            return
        self._store.record_story(self._story_row.model_copy(update={"status": status}))

    def _record_subtask(self, row: models.SubtaskRun, status: str) -> None:
        self._store.record_subtask(self._story_id, row.model_copy(update={"status": status}))

    def _outcome(self, kind: LaneKind, subtask: str | None, **fields: Any) -> LaneOutcome:
        return LaneOutcome(
            kind=kind,
            story=self._story_id,
            level=self._level,
            subtask=subtask,
            completed=tuple(self._completed),
            warnings=tuple(self._warnings),
            base=self._base,
            **fields,
        )


async def base_only_lane(
    story: census.StoryPlan,
    root_plan: dag.RootPlan,
    tips: Sequence[str],
    *,
    plan: SupervisorPlan,
    store: Store,
    run_id: str,
    root: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: runs.RunnerFactory | None,
    slots: asyncio.Semaphore,
    stop: StopSignal,
    finished: dict[str, LaneOutcome],
) -> str:
    """A subtask-less story's lane: build its merged base, return it as its tip.

    It takes a slot, since a base can dispatch a resolver, and checks the stop
    first. The failure paths are `lane`'s for a merged base, but with no
    subtask to name and no store row to write -- `record_plan` records only
    stories with work -- so it runs on a row-less `StoryRecorder`
    (`StoryRecorder.without_rows`), which writes nothing. Every outcome has
    `level=None` and `collect_outcomes` reports it after the waves.
    """
    recorder = StoryRecorder.without_rows(store, story.id)
    async with slots:
        if stop.triggered:
            raise LaneStopped(recorder.stopped(None, None))
        try:
            await build_merged_base(
                story,
                root_plan,
                tips,
                store=store,
                run_id=run_id,
                root=root,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                stop=stop,
                resume_from=plan.checkpoints.get(bases.resolver_card_id(story.id)),
            )
        except bases.BaseFailed as error:
            if error.stopped:
                raise LaneStopped(recorder.stopped(None, None)) from error
            stop.trigger(story.id)
            # Recorded first (a row-less recorder writes nothing), then
            # board-comments B2: on the story card; there is no store row here.
            escalated = recorder.escalated(None, "base", error.detail)
            flushed = await post_comment_async(
                store,
                root,
                comments.compose_base_failed(
                    run_id=run_id,
                    story_id=story.id,
                    base_branch=root_plan.branch,
                    detail=error.detail,
                ),
                run_id=run_id,
            )
            raise LaneEscalated(
                replace(escalated, warnings=escalated.warnings + tuple(flushed))
            ) from error
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            stop.trigger(story.id)
            raise LaneEscalated(
                recorder.escalated(None, None, f"{type(error).__name__}: {error}")
            ) from error
    recorder.base_built(root_plan)
    finished[story.id] = recorder.done()
    return plan.tips[story.id]


async def lane(
    story: census.StoryPlan,
    *,
    plan: SupervisorPlan,
    store: Store,
    run_id: str,
    lease_token: str,
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: runs.RunnerFactory | None,
    slots: asyncio.Semaphore,
    stop: StopSignal,
    finished: dict[str, LaneOutcome],
) -> str:
    """One story's node coroutine (T1, T4, T6): drive its remaining subtasks, return its tip.

    A story with nothing left to run returns its tip without taking a slot,
    unless `builds_a_base_alone` says it must first build its merged base
    (`base_only_lane`). Otherwise the lane takes a slot -- grafo started it, so
    every blocker already succeeded -- and drives the remaining subtasks in
    census order, each on the base `record_plan` recorded for it. Before each
    subtask it checks the stop: if it fired, the story is recorded `stopped`
    and `LaneStopped` is raised with that subtask never driven. A `stopped`
    summary records the subtask and story `stopped` and raises `LaneStopped`.
    Any other non-`done` summary, or any `Exception` while handling a
    subtask (a lane bug), triggers the stop first and raises `LaneEscalated`
    at that subtask. `LaneEscalated`/`LaneStopped` pass through the catch-all
    unchanged. A `BaseException` is never caught.

    A story whose root is `merged` is reached through one grafo edge per
    blocker, so its lane runs only after every blocker succeeded; it reads the
    blockers' tips from `plan.tips` in `root_plan.blockers` order. The lane
    takes its slot, checks the stop (fired: `stopped` at its first subtask,
    nothing built), then awaits
    `build_merged_base` before its first subtask, whose recorded base is the
    merged base branch. `BaseFailed(stopped=False)` triggers the stop, records
    the story `escalated` and raises `LaneEscalated` with `failed_phase="base"`,
    no subtask and the failure's detail; `BaseFailed(stopped=True)` records it
    `stopped` and raises `LaneStopped` with no subtask. Any other error from
    the base reaches the catch-all with no subtask. Once built, the outcome
    carries the story's `RootPlan` as `base`, whatever happens after.

    Every row the lane writes and every outcome it builds go through one
    `StoryRecorder`, built once the story's planned rows are read. The lane
    keeps the control flow and every `stop.trigger`; the recorder keeps
    `completed`, `warnings` and the built base.

    Each subtask's open checkpoint is looked up first
    (`runs.continuable_checkpoint`), inside the same `try`, and handed to the
    driver as `resume_from` when it can be continued.

    On a resume (`plan.resuming`, card 54e4ec29) the subtask's checkpoint is
    `plan.checkpoints`' and the lenient relaunch lookup is never read; a
    merged base gets its resolver's checkpoint the same way.

    `lease_token` is this life's `control.Lease.token`; it keys the escalation
    comment, so a later life escalating at the same phase comments again.
    """
    root_plan = plan.roots[story.id]
    planned = plan.planned.get(story.id)
    tips: list[str] | None = None
    if root_plan.kind == "merged":
        tips = [plan.tips[blocker] for blocker in root_plan.blockers]
    if planned is None and not builds_a_base_alone(story, root_plan):
        return plan.tips[story.id]
    if planned is None:
        return await base_only_lane(
            story,
            root_plan,
            tips,
            plan=plan,
            store=store,
            run_id=run_id,
            root=root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            slots=slots,
            stop=stop,
            finished=finished,
        )
    story_row, subtask_rows = plan.rows[story.id]
    recorder = StoryRecorder(store, story.id, planned.level, story_row, subtask_rows)

    async with slots:
        current: census.SubtaskPlan | None = None
        try:
            if root_plan.kind == "merged":
                # Checked before the base as before every subtask: a lane that
                # finds the stop fired builds nothing (spec, first error path).
                if stop.triggered:
                    raise LaneStopped(recorder.stopped(planned.remaining[0].id, None))
                assert tips is not None
                try:
                    await build_merged_base(
                        story,
                        root_plan,
                        tips,
                        store=store,
                        run_id=run_id,
                        root=root,
                        commands=commands,
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        stop=stop,
                        resume_from=plan.checkpoints.get(bases.resolver_card_id(story.id)),
                    )
                except bases.BaseFailed as error:
                    # A parked resolver is a stop, not an escalation (P4).
                    if error.stopped:
                        raise LaneStopped(recorder.stopped(None, None)) from error
                    stop.trigger(story.id)
                    escalated = recorder.escalated(None, "base", error.detail)
                    # Board-comments B2: after the escalation is recorded; the
                    # failed base is the story's, so is the comment.
                    flushed = await post_comment_async(
                        store,
                        root,
                        comments.compose_base_failed(
                            run_id=run_id,
                            story_id=story.id,
                            base_branch=root_plan.branch,
                            detail=error.detail,
                        ),
                        run_id=run_id,
                    )
                    raise LaneEscalated(
                        replace(escalated, warnings=escalated.warnings + tuple(flushed))
                    ) from error
                recorder.base_built(root_plan)
            for position, subtask in enumerate(planned.remaining):
                current = subtask
                if stop.triggered:
                    raise LaneStopped(recorder.stopped(subtask.id, None))
                card = await asyncio.to_thread(board.show, subtask.id, repo_dir=root)
                parent = await asyncio.to_thread(board.show, story.id, repo_dir=root)
                row = recorder.started(subtask_id=subtask.id, first=position == 0)
                # Relaunch continuation (card 02890d5d) is lenient and reads
                # across runs; a resume (card 54e4ec29) hands on exactly the
                # checkpoints `resume_checkpoints` already validated. Either
                # way the keyword is passed only when there is a row, so a
                # driver that predates it works.
                extra: dict[str, Any] = {}
                if plan.resuming:
                    checkpoint = plan.checkpoints.get(subtask.id)
                else:
                    checkpoint = runs.continuable_checkpoint(store, subtask.id)
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
                recorder.add_warnings(result.warnings)
                summary = result.summary
                # A `stopped` summary is handled before the non-`done` branch:
                # a stop is not an escalation (P4).
                if summary.status == "stopped":
                    raise LaneStopped(
                        recorder.stopped(
                            subtask.id,
                            summary.before_phase,
                            subtask_row="started",
                        )
                    )
                if summary.status != "done":
                    stop.trigger(story.id)
                    escalated = recorder.escalated(
                        subtask.id,
                        summary.failed_phase,
                        summary.detail,
                        subtask_row="started",
                    )
                    # Board-comments B2: after the escalation is recorded, keyed
                    # by this life's lease token, so a second life escalating at
                    # the same phase comments again.
                    flushed = await post_comment_async(
                        store,
                        root,
                        comments.compose_escalated(
                            run_id=run_id,
                            card_id=subtask.id,
                            token=lease_token,
                            failed_phase=summary.failed_phase,
                            detail=summary.detail,
                            reason=comments.agent_reason(
                                summary.results, summary.failed_phase
                            ),
                        ),
                        run_id=run_id,
                    )
                    raise LaneEscalated(
                        replace(escalated, warnings=escalated.warnings + tuple(flushed))
                    )
                recorder.subtask_done(subtask.id, row.branch)
                # Board-comments B2: after the outcome is recorded, never instead of it.
                recorder.add_warnings(
                    await post_comment_async(
                        store,
                        root,
                        comments.compose_done(
                            run_id=run_id,
                            card_id=subtask.id,
                            summary=summary,
                            branch=row.branch,
                            # Where the walk actually continued, as the engine
                            # reports it: `None` on a fresh walk, and on one
                            # whose checkpoint was declined and started over.
                            resumed_at=summary.resumed_at,
                        ),
                        run_id=run_id,
                    )
                )
            done = recorder.done()
        except (LaneEscalated, LaneStopped):
            raise
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            stop.trigger(story.id)
            raise LaneEscalated(
                recorder.escalated(
                    None if current is None else current.id,
                    None,
                    f"{type(error).__name__}: {error}",
                    subtask_row=None if current is None else "planned",
                )
            ) from error
    finished[story.id] = done
    return planned.tip


async def run_until_killed(
    work: Awaitable[Any], killed: asyncio.Event, fatal: Sequence[BaseException]
) -> None:
    """Await `work`, unless a lane dies of a `BaseException` first: then re-raise it.

    grafo's workers catch only `Exception`. asyncio re-raises only
    `KeyboardInterrupt` and `SystemExit` out of the loop by itself; any other
    `BaseException` is stored on the grafo worker task, and
    `TreeExecutor.run` drops it in `gather(..., return_exceptions=True)` --
    in the pinned grafo release this does not merely leave the lane pending,
    it hangs `gather()` forever (confirmed with `faulthandler`). So `supervise`
    records such an exception in `fatal` and sets `killed`, and this re-raises
    the first one at once: it leaves `asyncio.run`, which cancels every other
    lane where it stands, and the latest checkpoints stand for `am resume`
    (supervisor-tree §7, card 949d51a0). It never waits for `work` itself to
    finish once `killed` fires -- `work` may never finish on its own. If the
    caller is cancelled first, `work` is cancelled with it, as a plain `await`
    would do: the tree is never left running behind it.
    """
    running = asyncio.ensure_future(work)
    watcher = asyncio.ensure_future(killed.wait())
    try:
        await asyncio.wait({running, watcher}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        running.cancel()
        raise
    finally:
        watcher.cancel()
    if fatal:
        running.cancel()
        raise fatal[0]
    await running


async def build_dag_tree(
    items: Sequence[T],
    *,
    id_of: Callable[[T], str],
    blockers_of: Callable[[T], Sequence[str]],
    node_factory: Callable[[T], Callable[..., Awaitable[Any]]],
    forward: Callable[[T], str | None] | None = None,
) -> tuple[dict[str, grafo.Node], list[grafo.Node]]:
    """Build the grafo nodes and edges for `items`, and pick the executor's roots.

    One `grafo.Node` per item, `uuid=id_of(item)`, `timeout=None` always
    (grafo's 60 s default would cancel a long-running node). `nodes_by_id` is
    keyed by `id_of(item)`, in `items` order.

    Every blocker of an item gets one edge to it, in `blockers_of` order,
    whatever the blocker count: grafo enqueues a node only once every parent
    returned, and never once a parent raised, so an item with two or more
    blockers is a real join. Each edge forwards its blocker's output as
    `forward(blocker_item)` -- `forward` is handed the blocker item, not its
    id, once per edge -- or nothing on that edge when `forward` is `None` or
    returns `None`. This helper creates no events and does no waiting.

    `roots` is, in `items` order, every item with no blocker; an item with
    one or more blockers is never a root. A blocker id not among `items` is
    the caller's to avoid; nothing is filtered, validated or de-duplicated.
    Empty `items` gives `({}, [])`.
    """
    nodes = {
        id_of(item): grafo.Node(coroutine=node_factory(item), uuid=id_of(item), timeout=None)
        for item in items
    }
    items_by_id = {id_of(item): item for item in items}
    roots: list[grafo.Node] = []
    for item in items:
        blockers = blockers_of(item)
        if not blockers:
            roots.append(nodes[id_of(item)])
            continue
        for blocker in blockers:
            parent = nodes[blocker]
            name = None if forward is None else forward(items_by_id[blocker])
            await parent.connect(nodes[id_of(item)], forward=name)
    return nodes, roots


async def supervise(
    plan: SupervisorPlan,
    *,
    store: Store,
    run_id: str,
    lease_token: str,
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: runs.RunnerFactory | None,
    max_concurrent: int,
    stop: StopSignal,
    slots: asyncio.Semaphore | None = None,
) -> list[LaneOutcome]:
    """Run every census story as a grafo node and collect the outcomes (T1, T6).

    The tree comes from `build_dag_tree` over `plan.stories`, each story's
    blockers being its `plan.roots` in-milestone blockers: one node per story
    and one edge per blocker, each forwarding the blocker's tip as
    `tip_<short id>`, so a story rooted on a `merged` base (two or more
    in-milestone blockers) is a grafo join, not an executor root: its lane
    runs only after every blocker succeeded, and reads the blockers' tips from
    `plan.tips` in `root_plan.blockers` order. A milestone with no story has
    no tree to run.

    `slots` is the semaphore every lane takes its slot from: when given it is
    used as is, so concurrent `supervise` calls handed the same one share one
    budget and `max_concurrent` sizes nothing; when omitted this call makes its
    own `asyncio.Semaphore(max_concurrent)`, as a solo run always has.

    A lane that dies of a `BaseException` other than a cancellation ends the
    whole call at once, re-raised by `run_until_killed`: grafo alone would
    drop it (§7).

    The `grafo` logger is at CRITICAL for exactly this call: a lane's
    escalation is data in the outcomes, never a traceback on a stream, and
    grafo's own level is restored on every exit.
    """
    grafo_logger = logging.getLogger(GRAFO_LOGGER)
    level_before = grafo_logger.level
    grafo_logger.setLevel(logging.CRITICAL)
    try:
        if slots is None:
            slots = asyncio.Semaphore(max_concurrent)
        finished: dict[str, LaneOutcome] = {}
        # A lane's `BaseException` that is neither an `Exception` nor a
        # cancellation: grafo would drop it (`run_until_killed`).
        fatal: list[BaseException] = []
        killed = asyncio.Event()

        def node_coroutine(story: census.StoryPlan) -> Callable[..., Any]:
            async def run(**tips: str) -> str:
                try:
                    result = await lane(
                        story,
                        plan=plan,
                        store=store,
                        run_id=run_id,
                        lease_token=lease_token,
                        root=root,
                        drive=drive,
                        commands=commands,
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        slots=slots,
                        stop=stop,
                        finished=finished,
                    )
                except BaseException as error:
                    if not isinstance(error, (Exception, asyncio.CancelledError)):
                        fatal.append(error)
                        killed.set()
                    raise
                else:
                    return result

            return run

        nodes, roots = await build_dag_tree(
            items=plan.stories,
            id_of=lambda story: story.id,
            blockers_of=lambda story: plan.roots[story.id].blockers,
            node_factory=node_coroutine,
            forward=lambda blocker: f"tip_{dag.short_id(blocker.id)}",
        )
        errors: list[BaseException] = []
        if roots:
            executor = grafo.TreeExecutor(uuid=run_id, roots=roots)
            await run_until_killed(executor.run(), killed, fatal)
            errors = list(executor.errors)
        return collect_outcomes(plan, nodes, errors, finished)
    finally:
        grafo_logger.setLevel(level_before)


def milestone_claims(
    milestone_id: str, stories: Sequence[census.StoryPlan], branch_prefix: str
) -> list[str]:
    """The `run_claims` keys a milestone run holds under its lease (X5, X6).

    `card:<milestone_id>`, then `card:<id>` for every remaining subtask
    (`dag.remaining_subtasks`: a done subtask or a closed story adds none) in
    census order, then `branch:<branch_prefix>-integrate`. Pure; a key
    already listed is not repeated, so the first occurrence keeps its place.
    """
    keys = [control.card_claim(milestone_id)]
    keys.extend(
        control.card_claim(subtask.id)
        for story in stories
        for subtask in dag.remaining_subtasks(story)
    )
    keys.append(control.branch_claim(integration.integration_branch(branch_prefix)))
    return list(dict.fromkeys(keys))


def story_claims(
    milestone_id: str, story: census.StoryPlan, branch_prefix: str
) -> list[str]:
    """The `run_claims` keys a story run holds under its lease (run-story design).

    `card:<milestone_id>`, `card:<story.id>`, then `card:<id>` for every
    remaining subtask (`dag.remaining_subtasks`, the set `milestone_claims`
    uses, so a card run on a done subtask is not refused), then
    `branch:<subtask branch>` for each of those subtasks, census order. Never
    `branch:<prefix>-integrate`: a story run does not integrate. Pure; a key
    already listed is not repeated, so the first occurrence keeps its place.
    """
    remaining = dag.remaining_subtasks(story)
    keys = [control.card_claim(milestone_id), control.card_claim(story.id)]
    keys.extend(control.card_claim(subtask.id) for subtask in remaining)
    keys.extend(
        control.branch_claim(dag.subtask_branch(branch_prefix, subtask))
        for subtask in remaining
    )
    return list(dict.fromkeys(keys))


def milestone_card_ids(
    milestone_id: str, stories: Sequence[census.StoryPlan]
) -> list[str]:
    """The milestone card, then each story followed by its subtasks, census order.

    The cards a run's start flush covers (board-comments B7): done subtasks
    and closed stories included, since an earlier run may have left a pending
    comment on any of them. Pure; a card already listed is not repeated.
    """
    ids = [milestone_id]
    for story in stories:
        ids.append(story.id)
        ids.extend(subtask.id for subtask in story.subtasks)
    return list(dict.fromkeys(ids))


# ── the three stages of a milestone run (card 5daa944e) ─────────────────────


@dataclass(frozen=True)
class MilestonePreflight:
    """What `preflight_milestone` read and decided for one milestone run.

    Everything the recorded stage and the engine read afterwards. On a resume,
    `base_branch`, `branch_prefix` and `max_concurrent` are the recorded
    run's, and `resumed` is that run as it was left. `drive` is the chosen
    driver. Internal state, so a dataclass.
    """

    root: Path
    resumed: models.Run | None
    milestone_card: models.CardNode
    plan: census.Census
    levels: list[list[PlannedStory]]
    tips: list[dict[str, str]]
    keys: list[str]
    base_branch: str
    branch_prefix: str
    max_concurrent: int
    run_id: str
    run_record: models.Run
    drive: Driver


def preflight_milestone(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    max_concurrent: int = 1,
    clock: Callable[[], datetime] = _utcnow,
    resume_run_id: str | None = None,
    driver: Driver | None = None,
) -> MilestonePreflight:
    """Stage 1 of a milestone run: every read and refusal, then the run record (card 5daa944e).

    In today's order: the resumable run (resume only), the board roots, the
    milestone (`census.find_milestone`'s unknown/ambiguous refusal, or
    `find_run_milestone`), its census, `plan_levels` (the cycle refusal), the
    tips, the claims, and `cli.refuse_claimed` as the last refusal --
    read-only, before `refresh_git` and before any store, so a key another
    live run holds leaves no fetch, prune, run row or run directory. A fresh
    run then refreshes git (its first side effect, still before the store),
    reads the clock and mints the run id; a resume keeps its own id and
    refreshes git later, under the lease. The store is never opened here.

    A resumed run whose `config.story_id` is set is a story run: its story
    must still be a child of the milestone (else `runs.NotResumableError`,
    before any write and before git), and its plan, levels, tips and claim
    keys are `_story_plan`'s, as a fresh `preflight_story` computes them,
    `errors.StoryBlockedError` for a re-opened blocker included.
    """
    root = runs.resolve_repo_dir(repo_dir)
    resumed = None if resume_run_id is None else resumable_milestone_run(root, resume_run_id)
    if resumed is not None:
        base_branch = resumed.base_branch
        branch_prefix = resumed.branch_prefix
        max_concurrent = resumed.config.max_concurrent_stories
    roots = board.roots(repo_dir=root)
    if resumed is None:
        milestone_card = census.find_milestone(roots, milestone)
    else:
        milestone_card = find_run_milestone(roots, resumed)
    tree = board.tree(milestone_card.id, repo_dir=root)
    if resumed is None or resumed.config.story_id is None:
        plan = census.flatten_milestone(tree)
        levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
        tips = story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
        keys = milestone_claims(milestone_card.id, plan.stories, branch_prefix)
    else:
        # A resumed story run keeps its story: the plan is cut exactly as
        # `preflight_story` cuts a fresh one.
        story_id = resumed.config.story_id
        story_card = next((card for card in tree.children if card.id == story_id), None)
        if story_card is None:
            raise runs.NotResumableError(
                f"run {resumed.id!r} runs story {story_id}, and milestone"
                f" {milestone_card.id} has no story card with that id"
            )
        plan, levels, tips, keys = _story_plan(
            tree,
            story_card,
            root=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
        )
    drive = cli.drive_subtask_async if driver is None else driver
    # The last refusal (X5, X6): read-only, before `refresh_git` and before
    # `Store.open`, so a milestone, remaining subtask or integration branch
    # another live run claims leaves no fetch, prune, run row or run
    # directory. A resume's own rows are not a conflict; `take_lease` in the
    # recorded stage re-checks atomically.
    cli.refuse_claimed(root, keys, run_id=None if resumed is None else resumed.id)

    if resumed is None:
        # The first side effect. It runs after every refusal and before the store
        # is opened, so a failed fetch leaves no run directory behind.
        refresh_git(root)
        started_at = clock()
        run_id = runs.mint_run_id(milestone_card.id, started_at)
        run_record = models.Run(
            id=run_id,
            workflow=MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(max_concurrent_stories=max_concurrent),
            milestone_id=milestone_card.id,
        )
    else:
        run_id = resumed.id
        # Stamps a run recorded before `milestone_id` existed, so the next
        # resume no longer needs the short-id fallback.
        run_record = resumed.model_copy(
            update={"status": "started", "milestone_id": milestone_card.id}
        )
    return MilestonePreflight(
        root=root,
        resumed=resumed,
        milestone_card=milestone_card,
        plan=plan,
        levels=levels,
        tips=tips,
        keys=keys,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        max_concurrent=max_concurrent,
        run_id=run_id,
        run_record=run_record,
        drive=drive,
    )


def _restricted_stories(
    stories: Sequence[census.StoryPlan],
    selected: census.StoryPlan,
    *,
    root: Path,
    branch_prefix: str,
) -> list[census.StoryPlan]:
    """A story run's `plan.stories`: the done blockers `selected` stacks on, then `selected`.

    A story with nothing to run is carried alone, its blockers unjudged. Else
    its in-milestone blockers (ids of `stories`, census order, each once) are
    classified by status alone first: any not finished and not out of play is
    open, and `errors.StoryBlockedError` names every open one before a single
    branch is looked up. Then a `merged` blocker is dropped, and a `done` one
    is kept only when it has subtasks and its tip is a local branch of `root`
    (`_local_branch_exists`, read at call time; a `GitError` propagates). Kept
    blockers lose their own edges, so `dag.story_root` roots `selected` on
    their tips alone and never walks past them.
    """
    if not dag.remaining_subtasks(selected):
        return [replace(selected, blocked_by=[])]
    wanted = set(selected.blocked_by)
    blockers = [story for story in stories if story.id in wanted]
    open_blockers = [
        blocker
        for blocker in blockers
        if not census.is_finished(blocker.status)
        and not census.is_out_of_play(blocker.status)
    ]
    if open_blockers:
        raise errors.StoryBlockedError(
            selected.id,
            selected.title,
            [(blocker.id, blocker.title) for blocker in open_blockers],
        )
    exists = _local_branch_exists(root)
    kept = [
        blocker
        for blocker in blockers
        if blocker.status.lower() == "done"
        and blocker.subtasks
        and exists(dag.subtask_branch(branch_prefix, blocker.subtasks[-1]))
    ]
    return [
        *(replace(blocker, blocked_by=[]) for blocker in kept),
        replace(selected, blocked_by=[blocker.id for blocker in kept]),
    ]


def story_census(
    tree: models.CardNode, story: models.CardNode, *, root: Path, branch_prefix: str
) -> census.Census:
    """A story run's census: `tree`'s, cycle-checked, cut to `story` and the done blockers it stacks on.

    `story` is a child of `tree`. The whole census of `tree` is checked for a
    blocker cycle first (`dag.DependencyCycleError`), even one `story` is not
    part of. It is then cut by `_restricted_stories`: the kept done blockers,
    then `story` last, or `errors.StoryBlockedError` for an open blocker. A
    story the census dropped (out of play) leaves no stories at all.
    Read-only, except that `_local_branch_exists` runs git to look up a done
    blocker's tip.
    """
    full = census.flatten_milestone(tree)
    dag.assert_no_blocker_cycles(full.stories)
    selected = next((planned for planned in full.stories if planned.id == story.id), None)
    stories = (
        []
        if selected is None
        else _restricted_stories(full.stories, selected, root=root, branch_prefix=branch_prefix)
    )
    return census.Census(milestone_title=full.milestone_title, stories=stories)


def _story_plan(
    tree: models.CardNode,
    story: models.CardNode,
    *,
    root: Path,
    base_branch: str,
    branch_prefix: str,
) -> tuple[census.Census, list[list[PlannedStory]], list[dict[str, str]], list[str]]:
    """A story run's `(plan, levels, tips, keys)`, cut from its milestone's `tree`.

    `story` is a child of `tree`. `plan` is `story_census` (which raises
    `dag.DependencyCycleError` and `errors.StoryBlockedError`): empty for a
    story the census dropped (out of play), and with no wave for a story with
    no remaining subtasks. `tips` names `story` alone; `keys` are
    `story_claims`, never the integration branch. Read-only, except that
    `_local_branch_exists` runs git to look up a done blocker's tip.
    """
    plan = story_census(tree, story, root=root, branch_prefix=branch_prefix)
    if plan.stories:
        selected = plan.stories[-1]
    else:
        # Out of play: the census dropped it, so it has nothing to run and
        # claims only the milestone and story cards.
        selected = census.StoryPlan(story.id, story.title, story.status, [], [])
    levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    tips = [
        tip
        for tip in story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
        if tip["story"] == selected.id
    ]
    keys = story_claims(tree.id, selected, branch_prefix)
    return plan, levels, tips, keys


def preflight_story(
    story: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    clock: Callable[[], datetime] = _utcnow,
    driver: Driver | None = None,
) -> MilestonePreflight:
    """Stage 1 of a story run: `preflight_milestone`'s result, its plan cut to one story.

    A fresh run only. The story is `census.find_story`'s pick
    (`StoryNotFoundError` propagates); the plan is its parent milestone's
    census, cycle-checked as a milestone run's is, cut to that story and the
    done blockers it stacks on (`_restricted_stories`, which refuses an open
    blocker with `errors.StoryBlockedError` before the claims). A story
    the census dropped (out of play) leaves an empty plan, and a story with
    no remaining subtasks a plan with no wave: nothing to run, not an error.
    Then the claims (`story_claims`) and `cli.refuse_claimed` as the last
    refusal. Everything up to there is read-only; `refresh_git` is the first
    side effect, then the clock and the run id, minted from the story's id.
    The run is recorded as a milestone run of the parent milestone, one
    story at a time, with `RunConfig.story_id` naming the story.
    """
    root = runs.resolve_repo_dir(repo_dir)
    roots = board.roots(repo_dir=root)
    match = census.find_story(roots, story)
    plan, levels, tips, keys = _story_plan(
        board.tree(match.milestone.id, repo_dir=root),
        match.story,
        root=root,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
    )
    # The last refusal (X5, X6), read-only, as in `preflight_milestone`.
    cli.refuse_claimed(root, keys, run_id=None)

    # The first side effect, after every refusal and before any store.
    refresh_git(root)
    started_at = clock()
    run_id = runs.mint_run_id(match.story.id, started_at)
    run_record = models.Run(
        id=run_id,
        workflow=MILESTONE_WORKFLOW,
        repo_dir=root,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        status="started",
        started_at=started_at,
        config=models.RunConfig(max_concurrent_stories=1, story_id=match.story.id),
        milestone_id=match.milestone.id,
    )
    return MilestonePreflight(
        root=root,
        resumed=None,
        milestone_card=match.milestone,
        plan=plan,
        levels=levels,
        tips=tips,
        keys=keys,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        max_concurrent=1,
        run_id=run_id,
        run_record=run_record,
        drive=cli.drive_subtask_async if driver is None else driver,
    )


@dataclass(frozen=True)
class RecordedMilestoneRun:
    """A milestone run past its recorded stage (card 5daa944e).

    Its id, its open store, the lease it holds, the plan rows `record_plan`
    wrote, and on a resume each open card's checkpoint (`None` on a fresh
    run). Internal state, so a dataclass.
    """

    run_id: str
    store: Store
    lease: control.Lease
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]
    checkpoints: dict[str, store_checkpoints.Checkpoint] | None


@contextmanager
def recorded_milestone_run(pre: MilestonePreflight) -> Iterator[RecordedMilestoneRun]:
    """Stage 2 of a milestone run: open the store, take the lease, record the plan (card 5daa944e).

    On a resume the checkpoints are read first: the store's own refusal, a
    checkpoint saved under another workflow, is read-only and comes before
    the lease, before git and before any write. Then `cli.run_lease` takes
    the lease with `pre.keys`, inside the `try` that closes the store, so the
    claims and the lease are released before `store.close()` on every exit
    (live control C2, X5), an exception in the block included. It is taken
    before `record_run`, so every run write is fenced by this token; a lost
    race is `ClaimedError` or `RunIsLiveError` with nothing recorded. A
    resume refreshes git first under the lease. Then the run is recorded
    `started` and the whole plan `pending`; a resume then reopens its rows.
    """
    store = Store.open(pre.root, pre.run_id)
    try:
        checkpoints: dict[str, store_checkpoints.Checkpoint] | None = None
        cards: list[tuple[str, Workflow]] = []
        if pre.resumed is not None:
            cards = open_cards(
                pre.plan.stories, branch_prefix=pre.branch_prefix, base_branch=pre.base_branch
            )
            checkpoints = resume_checkpoints(store, cards)
        with cli.run_lease(store, claims=pre.keys) as lease:
            if pre.resumed is not None:
                # A resume's first side effect, under this life's lease (X5):
                # a run still live elsewhere was refused on entry, before git.
                refresh_git(pre.root)
            store.record_run(pre.run_record)
            rows = record_plan(store, pre.levels, root=pre.root, branch_prefix=pre.branch_prefix)
            if pre.resumed is not None:
                # After `record_plan`, which records every planned row `pending`.
                reopen_rows(store, pre.resumed, {card_id for card_id, _workflow in cards})
            yield RecordedMilestoneRun(
                run_id=pre.run_id,
                store=store,
                lease=lease,
                rows=rows,
                checkpoints=checkpoints,
            )
    finally:
        store.close()


async def run_milestone_engine(
    pre: MilestonePreflight,
    recorded: RecordedMilestoneRun,
    *,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
    slots: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Stage 3 of a milestone run: drive a recorded, leased run to its report (card 5daa944e).

    From the first engine-side action to the last: the start flush of the
    milestone's pending comments and the stale-story re-roll, then
    `control.controlled(supervise(...))` under the recorded stage's lease
    (`lease_token=lease.token`), then the outcome precedence, Integrate, the
    final run record, the run-end comment and the report. The driver and the
    lane bound are `pre.drive` and `pre.max_concurrent` (a resume's bound is
    the recorded run's). The caller owns the store and the lease; a crash
    propagates.

    A story run (`pre.run_record.config.story_id` set) never reaches
    Integrate: once its lanes finished clean it is recorded `done` and its
    report is the milestone `done` payload without `integrated`.
    """
    store, lease, run_id = recorded.store, recorded.lease, recorded.run_id
    rows, checkpoints = recorded.rows, recorded.checkpoints
    root, plan, levels, tips = pre.root, pre.plan, pre.levels, pre.tips
    milestone_card, run_record, resumed = pre.milestone_card, pre.run_record, pre.resumed
    base_branch, branch_prefix = pre.base_branch, pre.branch_prefix

    # Board-comments B7: any run's leftover comments on this milestone's
    # cards go out under this lease, before anything is driven; a board
    # failure is a warning and the run goes on (B8).
    warnings = comments.flush(
        store, root, card_ids=milestone_card_ids(milestone_card.id, plan.stories)
    )
    warnings.extend(reroll_stale_stories(plan.stories, root))
    completed: list[str] = []
    stop = StopSignal()

    # `controlled` only ever parks the run through `stop` (C3); it
    # closes the window and runs a final sweep before returning.
    outcomes = await control.controlled(
        supervise(
            supervisor_plan(
                plan.stories,
                levels,
                rows,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
                checkpoints=checkpoints,
            ),
            store=store,
            run_id=run_id,
            lease_token=lease.token,
            root=root,
            drive=pre.drive,
            commands=list(commands),
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            max_concurrent=pre.max_concurrent,
            stop=stop,
            slots=slots,
        ),
        store=store,
        stop=stop,
        lease=lease,
        interval=control_interval,
    )
    # Wave order, census order within a wave, never finish order.
    for outcome in outcomes:
        completed.extend(outcome.completed)
        warnings.extend(outcome.warnings)
    built_bases = bases_payload(outcomes)
    total = sum(len(story.subtasks) for story in plan.stories)

    def report(payload: dict[str, Any]) -> dict[str, Any]:
        """Every payload shape on the same terms: `bases` when built, and on
        a resume `resumed` plus `took_over` when a dead holder's lease
        was taken over (X5), as `cli._resume_from_checkpoint` reports it."""
        if resumed is not None:
            payload["resumed"] = True
            if lease.displaced is not None:
                payload["took_over"] = {
                    "pid": lease.displaced.pid,
                    "host": lease.displaced.host,
                    "heartbeat_at": lease.displaced.heartbeat_at.isoformat(),
                }
        return with_bases(payload, built_bases)

    def comment_run_end(payload: dict[str, Any]) -> dict[str, Any]:
        """Comment `payload`'s outcome on the milestone card (board-comments B2).

        Called after the run's final record, on every exit that records
        one. `total` goes only into the dict `compose_run_end` reads, so
        the report keeps its shape; the flush's warnings join the
        report's own `warnings`.
        """
        comment = comments.compose_run_end(
            run_id=run_id,
            milestone_id=milestone_card.id,
            token=lease.token,
            payload={**payload, "total": total},
        )
        payload["warnings"].extend(post_comment(store, root, comment, run_id=run_id))
        return payload

    # Outcome precedence (live control C6): the first match wins. A
    # control is never an escalation, and a paused or canceled run
    # never reaches Integrate in this invocation.
    if stop.requested == "cancel":
        store.record_run(run_record.model_copy(update={"status": models.CANCELED}))
        payload = report(controlled_payload(run_id, "cancel", outcomes, warnings))
        # Board-comments B2 (card 5d9a875f): after the cancel is recorded,
        # each subtask it parked, in wave order, then the milestone. A lane
        # stopped while its base built names no subtask and gets nothing;
        # an escalated lane already commented its own escalation.
        for outcome in outcomes:
            if outcome.kind != "stopped" or outcome.subtask is None:
                continue
            assert outcome.story is not None
            comment = comments.compose_canceled(
                run_id=run_id,
                card_id=outcome.subtask,
                before_phase=outcome.before_phase,
                branch=rows[outcome.story][1][outcome.subtask].branch,
                relaunch=f"am run --milestone {milestone_card.id}",
            )
            payload["warnings"].extend(post_comment(store, root, comment, run_id=run_id))
        return comment_run_end(payload)
    if any(outcome.kind == "escalated" for outcome in outcomes):
        store.record_run(run_record.model_copy(update={"status": "escalated"}))
        primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
        payload = escalated_payload(run_id, primary, outcomes, warnings)
        if stop.requested == "pause":
            payload["control"] = "pause"
        return comment_run_end(report(payload))
    if stop.requested == "pause":
        store.record_run(run_record.model_copy(update={"status": "stopped"}))
        # Board-comments B2 (card 5d9a875f): only the milestone's run-end;
        # a parked subtask is resumed, not closed, so it gets no comment.
        return comment_run_end(report(controlled_payload(run_id, "pause", outcomes, warnings)))

    def done_payload() -> dict[str, Any]:
        """A clean run's report before Integrate's key: each wave, what this
        invocation finished, and every planned story's tip."""
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
        }

    if run_record.config.story_id is not None:
        # A story run ends on its story's tip branch: there is no Integrate,
        # so no `<prefix>-integrate` branch and no `integrated` key.
        store.record_run(run_record.model_copy(update={"status": "done"}))
        return comment_run_end(report(done_payload()))

    # Integrate (addendum I6) runs only once every lane finished clean,
    # and also when there was nothing left to drive: that is how a relaunch
    # retries an Integrate escalation, and why a finished milestone's
    # relaunch is a no-op merge. Read as `integration.integrate_milestone`
    # so a test can replace it, as `driver` is. It needs a factory for a
    # conflicting tip; `None` is production's, read off `cli` now.
    factory = cli.default_runner_factory if runner_factory is None else runner_factory
    # `integrate_milestone` stays a synchronous call (I6); it is run on a
    # worker thread, not the loop thread, only because its conflict
    # resolver (`runtime_engine.run_subtask`) makes its own nested
    # `asyncio.run(...)` call, which `asyncio.run` refuses once this
    # coroutine is already running on the loop thread. `Store`'s
    # connection is `check_same_thread=False` for exactly this kind of
    # cross-thread, strictly sequential use (store.py).
    # A cancel waits for that thread, so the store and lease
    # outlive it (`_in_thread_to_completion`).
    outcome = await _in_thread_to_completion(
        integration.integrate_milestone,
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
        return comment_run_end(report(integrate_escalated_payload(run_id, outcome, warnings)))

    store.record_run(run_record.model_copy(update={"status": "done"}))
    return comment_run_end(
        report({**done_payload(), "integrated": integrated_payload(outcome)})
    )


def run_milestone(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    resume_run_id: str | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Drive every remaining subtask of `milestone` as a grafo tree, and report (O6, T1-T6).

    `milestone` is a card id or a title needle (O1). This wrapper validates
    its arguments (`max_concurrent < 1` included) and then runs the three
    stages (card 5daa944e) under one `asyncio.run(_run_milestone_async(...))`:
    `preflight_milestone` runs everything that can refuse, another live
    run's claim included, before the store is opened; `recorded_milestone_run`
    takes a `control.Lease` with the run's `milestone_claims`
    (`cli.run_lease`) and records one `milestone` run with its whole plan
    `pending`; and `run_milestone_engine` runs `control.controlled(supervise(...))`,
    which starts every story the moment its blockers succeeded, at most
    `max_concurrent` at once. A subtask already `done` on
    the board is never driven, but its branch still anchors the next
    subtask's base. The card and its story are read fresh from the board
    before each subtask. The default driver is `cli.drive_subtask_async`,
    read at call time.

    The first escalation triggers the run's `StopSignal`: running subtasks
    park at their next phase boundary and are recorded `stopped`, a lane
    between subtasks or waiting for a slot ends `stopped` without driving
    anything more, and grafo starts no dependent of a failed lane, so those
    stories stay `pending`.

    An applied `am cancel` or `am pause` fires the same `StopSignal` through
    `stop.request`, so lanes park exactly as for an escalation. Once the tree
    returns, the first match wins (C6): a cancel records the run `canceled`
    and returns `controlled_payload`; an escalation records `escalated` as
    below, with `control: "pause"` added when a pause was applied; a pause
    records `stopped` and returns `controlled_payload` with its `resume`
    hint. Only a run with none of these reaches Integrate.

    When every lane finished clean -- or none had anything to run --
    Integrate folds every story tip into `<branch_prefix>-integrate` before
    the run is recorded. Success records `done` and adds `integrated`; an
    Integrate escalation records `escalated` and returns
    `integrate_escalated_payload`. An exception from Integrate propagates and
    the run is never recorded `done`.

    `resume_run_id` continues that milestone run instead (card 54e4ec29).
    `milestone`, `base_branch`, `branch_prefix`, `max_concurrent` and
    `clock` are then not read: the milestone is the one the run recorded
    (`find_run_milestone`), and the rest is what the run recorded too. Both a
    fresh and a resumed run are recorded with `milestone_id` set to the
    milestone card's full id, which stamps a run recorded before that field
    existed. The plan is
    re-derived from the board as a fresh run derives it. Every refusal -- an
    unknown, non-milestone, `cancelled` or `done` run, an unknown milestone, a blocker
    cycle, and a checkpoint saved under another workflow digest -- comes
    before the first write and before git is refreshed. Then the run is
    recorded `started`, the plan is re-recorded, orphan attempts are marked
    `harness_error` and every open stopped, escalated or started row is
    recorded `started` (`reopen_rows`), and `supervise` runs under the same
    run id with each open checkpoint handed on as `resume_from`. Every
    payload gains `resumed: true`; `completed` is this invocation's work.

    Claims (multi-process X5, X6): the run's keys are `milestone_claims` --
    `card:<milestone>`, `card:<id>` of every remaining subtask, and
    `branch:<branch_prefix>-integrate`. `cli.refuse_claimed` checks them
    read-only as the last refusal, before `refresh_git` and `Store.open`, so
    a key another live run holds is `ClaimedError` with no fetch, prune, run
    row or run directory; a resume's own rows are no conflict. On a resume,
    `refresh_git` runs inside the lease, after `resume_checkpoints`, and a
    dead holder the lease took over is reported under `took_over` in every
    payload.

    Board comments (board-comments B2, B7): once the lease is held, before
    anything is driven, every pending outbox row on the milestone's cards is
    flushed. Each done or escalated subtask and each failed merged base is
    commented by its lane. A cancel comments each subtask it parked, in wave
    order (card 5d9a875f). Every recorded end -- cancelled, escalated, paused
    or done -- is commented on the milestone card. A flush's warnings join
    the report's `warnings`; nothing else about the run changes.

    The lease (live control C2) and its claims are held from `record_run` to
    the run's final record, and released before the store closes; every
    refusal comes before it. `controlled` polls this lease's `am pause`/`am
    cancel` requests every `control_interval` seconds and applies them to the
    run's one `StopSignal`; it closes the window and sweeps once more when the
    tree returns, before Integrate. A crash propagates and releases the lease
    and its claims.
    """
    if resume_run_id is None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
        if milestone is None or base_branch is None or branch_prefix is None:
            raise ValueError(
                "a fresh milestone run needs a milestone, a base branch and a branch prefix"
            )
    return asyncio.run(
        _run_milestone_async(
            milestone,
            repo_dir=repo_dir,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            driver=driver,
            clock=clock,
            max_concurrent=max_concurrent,
            resume_run_id=resume_run_id,
            control_interval=control_interval,
        )
    )


async def _in_thread_to_completion(function: Callable[..., T], /, **kwargs: Any) -> T:
    """Await `function(**kwargs)` on a worker thread, and never unwind before it returns.

    A thread cannot be cancelled, so a cancel arriving while it runs is held
    until the thread finishes and only then re-raised: the caller's `finally`
    blocks (closing the store, releasing the lease) never run under a call
    that is still using them. The thread's own result or exception is dropped
    once the caller has been cancelled.
    """
    future = asyncio.ensure_future(asyncio.to_thread(function, **kwargs))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        while not future.done():
            try:
                await asyncio.wait({future})
            except asyncio.CancelledError:
                continue
        if not future.cancelled():
            future.exception()  # retrieved, so it is not logged as never retrieved
        raise


async def _run_milestone_async(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    resume_run_id: str | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
    slots: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """`run_milestone`'s body without its argument validation, awaitable in a
    caller's own event loop.

    It composes the run's three stages (card 5daa944e): `preflight_milestone`
    (every read and refusal, `cli.refuse_claimed` last, then a fresh run's
    `refresh_git` and run record; no store), `recorded_milestone_run` (the
    store opened, the lease and claims taken before `record_run`, the plan
    recorded `pending`; the lease and claims released before `store.close()`
    on every exit) and `run_milestone_engine` (the start flush,
    `control.controlled(supervise(...))`, Integrate and the report).

    A caller that skips `run_milestone` must validate its own arguments first:
    a fresh run needs `max_concurrent >= 1` and a `milestone`, `base_branch`
    and `branch_prefix`. `slots`, when given, is forwarded to `supervise` and
    bounds this run's lanes instead of `max_concurrent`, so several runs can
    share one semaphore; `None` lets `supervise` make its own
    `asyncio.Semaphore(max_concurrent)`, as `run_milestone` does. The run is
    still recorded with `max_concurrent`. Board reads and `refresh_git` stay
    synchronous on the loop thread; Integrate stays a synchronous call but runs
    on a worker thread (see its call site), and a cancel arriving meanwhile
    waits for it to return before this coroutine unwinds.
    """
    pre = preflight_milestone(
        milestone,
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        max_concurrent=max_concurrent,
        clock=clock,
        resume_run_id=resume_run_id,
        driver=driver,
    )
    with recorded_milestone_run(pre) as recorded:
        return await run_milestone_engine(
            pre,
            recorded,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            control_interval=control_interval,
            slots=slots,
        )


def run_story(
    story: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Drive every remaining subtask of one story through the milestone engine, and report.

    `story` is a story card id or a title needle. Runs the three stages under
    one `asyncio.run(_run_story_async(...))`: `preflight_story` (every refusal
    -- `StoryNotFoundError`, `StoryBlockedError`, `DependencyCycleError`,
    `ClaimedError` -- before any store, run directory or git refresh),
    `recorded_milestone_run` and `run_milestone_engine`. The run is a
    `milestone` run of the story's parent milestone with `RunConfig.story_id`
    set and one lane at a time. It ends on the story's tip branch: no
    Integrate, so a clean run is recorded `done` and reports the milestone
    `done` payload without `integrated`. A story with nothing left to run is
    the same `done` payload with no level and nothing driven. Pause, cancel
    and escalation report as a milestone run's do; `am resume` of the run
    resumes this story alone (`preflight_milestone`).
    """
    return asyncio.run(
        _run_story_async(
            story,
            repo_dir=repo_dir,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            driver=driver,
            clock=clock,
            control_interval=control_interval,
        )
    )


async def _run_story_async(
    story: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """`run_story`'s body, awaitable in a caller's own event loop."""
    pre = preflight_story(
        story,
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        clock=clock,
        driver=driver,
    )
    with recorded_milestone_run(pre) as recorded:
        return await run_milestone_engine(
            pre,
            recorded,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            control_interval=control_interval,
        )


def _detach_recorded(
    pre: MilestonePreflight,
    *,
    detacher: detach.Detacher,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: runs.RunnerFactory | None,
    control_interval: float,
) -> dict[str, Any]:
    """Stage 2 of `pre` here, stage 3 in a detached child; the hand-off's payload.

    `recorded_milestone_run` records and leases the run; inside it `run.log`
    is created and the lease handed off, so the stage exits releasing nothing
    and closes its store. The child runs `run_milestone_engine` on this very
    `pre`, with the plan rows and checkpoints the recorded stage wrote
    (`cli.hand_off_to_child`).
    """
    with recorded_milestone_run(pre) as recorded:
        log = detach.create_run_log(pre.run_id)
        rows, checkpoints = recorded.rows, recorded.checkpoints
        token = recorded.lease.hand_off()

    def engine(store: Store, lease: control.Lease) -> dict[str, Any]:
        handed = RecordedMilestoneRun(
            run_id=pre.run_id, store=store, lease=lease, rows=rows, checkpoints=checkpoints
        )
        return asyncio.run(
            run_milestone_engine(
                pre,
                handed,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                control_interval=control_interval,
            )
        )

    return cli.hand_off_to_child(
        root=pre.root, run_id=pre.run_id, token=token, log=log, engine=engine, detacher=detacher
    )


def detach_milestone(
    milestone: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    detacher: detach.Detacher,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    max_concurrent: int = 1,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """`am run --milestone --detach` (card aff9fdbf): stages 1 and 2 here, stage 3 in a child.

    A fresh run only. `preflight_milestone` and `recorded_milestone_run` run
    exactly as for `run_milestone`, so every refusal, `refresh_git` and the
    `pending` plan are the same. The hand-off is `_detach_recorded`'s.
    """
    pre = preflight_milestone(
        milestone,
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        max_concurrent=max_concurrent,
        clock=clock,
        driver=driver,
    )
    return _detach_recorded(
        pre,
        detacher=detacher,
        commands=commands,
        allow_no_verification=allow_no_verification,
        runner_factory=runner_factory,
        control_interval=control_interval,
    )


def detach_story(
    story: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    detacher: detach.Detacher,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """`run_story` with its run handed to a detached child: stages 1 and 2 here, stage 3 there.

    `preflight_story` runs exactly as for `run_story`, so every refusal comes
    before any store, run directory, `refresh_git` or fork. The hand-off is
    `_detach_recorded`'s; the child's report is the story run's payload,
    with no `integrated`.
    """
    pre = preflight_story(
        story,
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        clock=clock,
        driver=driver,
    )
    return _detach_recorded(
        pre,
        detacher=detacher,
        commands=commands,
        allow_no_verification=allow_no_verification,
        runner_factory=runner_factory,
        control_interval=control_interval,
    )


# ── the board run (card baef4f94) ───────────────────────────────────────────


BoardStatus = Literal["done", "escalated", "stopped", "canceled", "blocked"]
"""How one milestone of a board run ended: its own run's outcome, or `blocked`
when a blocker did not finish `done` and it was never dispatched."""


def board_prefixes(
    milestones: Sequence[models.CardNode],
    branch_prefix_of: Callable[[models.CardNode], str],
    *,
    roots: Sequence[models.CardNode] = (),
) -> dict[str, str]:
    """Each given milestone's branch prefix, then each of its blocker roots', keyed by id.

    The given milestones come first, in input order. Then each card in `roots`
    (every milestone root the caller read, open or not) that one of them lists
    in `blocked_by` and that has no key yet, in `roots` order, whatever its
    status: a blocker that is no longer open keeps the prefix it ran under, so
    `milestone_bases` can name the integrate branch it left behind. Whether that
    blocker matters is `milestone_bases`' call, not this one's. Only direct
    blockers are keyed; a `roots` card nobody here blocks on is never derived,
    and a `blocked_by` id that is not in `roots` is ignored. With no `roots`
    the result is the given milestones' alone.

    `branch_prefix_of` is the caller's: deriving a prefix is not this module's
    job. This only checks it, for every entry alike. A prefix that is not a
    non-blank string is `ValueError`, as `run_milestone` refuses a missing one.
    So is a prefix two entries share: two milestones would both claim
    `branch:<prefix>-integrate`, and the deduplicated board claim set would hide
    that until the second milestone's own pre-flight refused it mid-run; a
    blocker root sharing one would have its blocked milestone stack on the
    other's branch.
    """
    prefixes: dict[str, str] = {}
    owners: dict[str, str] = {}

    def add(card: models.CardNode) -> None:
        prefix = branch_prefix_of(card)
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError(f"milestone {card.id} has no branch prefix (got {prefix!r})")
        if prefix in owners:
            raise ValueError(
                f"milestones {owners[prefix]} and {card.id} share the branch prefix {prefix!r}"
            )
        owners[prefix] = card.id
        prefixes[card.id] = prefix

    for card in milestones:
        add(card)
    blocker_ids = {blocker_id for card in milestones for blocker_id in card.blocked_by}
    for card in roots:
        if card.id in blocker_ids and card.id not in prefixes:
            add(card)
    return prefixes


class MilestoneBlockersError(ValueError):
    """A milestone would have to stack on two or more blockers at once.

    A milestone's base is one branch, and a merged base is a non-goal, so the
    human is told to chain the blockers instead. Subclasses `ValueError`, as
    `dag.DependencyCycleError` does: `ValueError` is already in `cli.HANDLED`,
    so a CLI caller gets the `ok: false` envelope and exit 3.
    """


def _blocker_branch(milestone_id: str, blocker_id: str, prefixes: Mapping[str, str]) -> str:
    """The blocker's `<prefix>-integrate`, or `ValueError` when it has no prefix.

    A missing or blank prefix is a caller bug, refused as `board_prefixes`
    refuses one, and never a `MilestoneBlockersError`.
    """
    prefix = prefixes.get(blocker_id)
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError(
            f"milestone {milestone_id}'s blocker {blocker_id} has no branch prefix "
            f"(got {prefix!r})"
        )
    return integration.integration_branch(prefix)


def milestone_bases(
    milestones: Sequence[models.CardNode],
    prefixes: Mapping[str, str],
    branch_exists: Callable[[str], bool],
    base_branch: str,
) -> dict[str, str]:
    """The branch each open milestone stacks on, keyed by id, in input order.

    `milestones` is every milestone root the caller knows, open or not; only
    the open ones (`dag.milestone_is_open`) get a key. A blocker id that is
    not a root in `milestones` is ignored. Each blocker is then exactly one of:

    - open: a stack candidate on its integrate branch, which its own run in
      this board creates. `branch_exists` is not asked.
    - landed (`census.is_landed`): ignored. `branch_exists` is not asked.
    - unlanded (`done`, or in play with nothing open under it): a candidate
      only if `branch_exists` says its integrate branch is there; otherwise
      assumed landed, today's behaviour.

    No candidate stacks on `base_branch`, one stacks on its branch, two or
    more raise `MilestoneBlockersError` for the first such milestone in input
    order. Pure apart from `branch_exists`, which is asked at most once per
    (milestone, blocker) pair. Blocker cycles are `dag.board_levels`' to
    refuse, not this function's.
    """
    by_id = {card.id: card for card in milestones}
    bases: dict[str, str] = {}
    for card in milestones:
        if card.id in bases or not dag.milestone_is_open(card):
            continue
        candidates: list[tuple[str, str]] = []
        unlanded: list[str] = []
        for blocker_id in dict.fromkeys(card.blocked_by):
            blocker = by_id.get(blocker_id)
            if blocker is None:
                continue
            if dag.milestone_is_open(blocker):
                candidates.append((blocker.id, _blocker_branch(card.id, blocker.id, prefixes)))
            elif not census.is_landed(blocker.status):
                branch = _blocker_branch(card.id, blocker.id, prefixes)
                if branch_exists(branch):
                    candidates.append((blocker.id, branch))
                    unlanded.append(blocker.id)
        if len(candidates) > 1:
            listed = ", ".join(blocker_id for blocker_id, _ in candidates)
            message = (
                f"milestone {card.id} is blocked by {len(candidates)} milestones that are "
                f"not landed ({listed}); a milestone stacks on at most one: "
                "chain them (A <- B <- C)"
            )
            if unlanded:
                message += (
                    f", or mark {', '.join(unlanded)} merged if that work has already landed"
                )
            raise MilestoneBlockersError(message)
        bases[card.id] = candidates[0][1] if candidates else base_branch
    return bases


def board_claims(
    milestones: Sequence[models.CardNode], prefixes: Mapping[str, str]
) -> list[str]:
    """Every open milestone's `milestone_claims`, in the order given, deduplicated.

    The stories come from `census.flatten_milestone`, the census
    `run_milestone` reads. The union keeps a key's first occurrence in its
    place (`list(dict.fromkeys(...))`), the discipline `milestone_claims`
    itself follows. Pure.
    """
    keys: list[str] = []
    for card in milestones:
        stories = census.flatten_milestone(card).stories
        keys.extend(milestone_claims(card.id, stories, prefixes[card.id]))
    return list(dict.fromkeys(keys))


def milestone_status(payload: Mapping[str, Any]) -> BoardStatus:
    """One `_run_milestone_async` payload read as a board status.

    `done` is the only clean outcome. A cancel, flagged `True` under either
    of its keys (`canceled`, `cancelled`), is `canceled`; an escalation (a
    paused one included) is `escalated`, a pause is `stopped`. Any other
    shape is not clean, so it counts as `escalated`.
    """
    if payload.get("done") is True:
        return "done"
    if any(payload.get(name) is True for name in models.CANCELED_STATUSES):
        return models.CANCELED
    if payload.get("escalated") is True:
        return "escalated"
    if payload.get("paused") is True:
        return "stopped"
    return "escalated"


def _local_branch_exists(root: Path) -> Callable[[str], bool]:
    """`run_board`'s `branch_exists` for `milestone_bases`: is `<branch>` a local branch of `root`?

    Each call runs `git -C <root> rev-parse --verify --quiet refs/heads/<branch>`
    through `worktree.run_git`, read at call time. Only `refs/heads/` counts: a
    tag or a remote-tracking ref with the same short name is not a local
    branch. Exit 1 is the ref being absent, so `False`; any other `GitError`
    (exit 128, "not a git repository") is not an answer and propagates, so a
    broken repository never silently drops a stacked milestone onto the base
    branch. No `git_lock`: a read-only ref lookup is not worth a
    `LockTimeoutError` path in a refusal check.
    """

    def exists(branch: str) -> bool:
        try:
            worktree.run_git(
                ["-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]
            )
        except worktree.GitError as error:
            if error.exit_code == 1:
                return False
            raise
        return True

    return exists


# ── the two stages of a board run (card 203a9a5e) ───────────────────────────


@dataclass(frozen=True)
class BoardPreflight:
    """What `preflight_board` read and decided for one board run.

    Everything `run_board_engine` reads afterwards, so the engine never reads
    the board again. `milestones` is `levels` flattened, in level order.
    `levels_payload` is the payload's `levels` value, so a foreground envelope
    and the run's report name the same levels. No run id: a board run has no
    Run record. Internal state, so a dataclass.
    """

    root: Path
    base_branch: str
    max_concurrent: int
    levels: list[list[models.CardNode]]
    milestones: list[models.CardNode]
    prefixes: dict[str, str]
    bases: dict[str, str]
    levels_payload: list[dict[str, Any]]


def preflight_board(
    *,
    repo_dir: Path,
    base_branch: str | None,
    branch_prefix_of: Callable[[models.CardNode], str],
    max_concurrent: int = 1,
) -> BoardPreflight:
    """Stage 1 of a board run: every argument check, read and refusal (card 203a9a5e).

    `run_board`'s refusals, in its order, each propagating unchanged and
    leaving nothing behind: `max_concurrent < 1` and a missing `base_branch`
    (`ValueError`, before the board is read); `board.roots()`, read once, then
    `dag.board_levels` (`DependencyCycleError`); `board_prefixes` with
    `roots=` (`ValueError`); `milestone_bases` over `_local_branch_exists`
    (`MilestoneBlockersError`, or a `GitError` that is not exit 1); last,
    only when something is open, one `cli.refuse_claimed` over `board_claims`
    (`ClaimedError`). `board.roots`, `cli.refuse_claimed` and
    `_local_branch_exists` are read at call time. Starts no event loop, opens
    no store and writes nothing.
    """
    if max_concurrent < 1:
        raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
    if not base_branch:
        raise ValueError("a board run needs a base branch")
    root = runs.resolve_repo_dir(repo_dir)
    all_roots = board.roots(repo_dir=root)
    levels = dag.board_levels(all_roots)
    milestones = [card for level in levels for card in level]
    prefixes = board_prefixes(milestones, branch_prefix_of, roots=all_roots)
    bases = milestone_bases(all_roots, prefixes, _local_branch_exists(root), base_branch)
    levels_payload = [
        {"level": index, "milestones": [card.id for card in level]}
        for index, level in enumerate(levels)
    ]
    if milestones:
        cli.refuse_claimed(root, board_claims(milestones, prefixes))
    return BoardPreflight(
        root=root,
        base_branch=base_branch,
        max_concurrent=max_concurrent,
        levels=levels,
        milestones=milestones,
        prefixes=prefixes,
        bases=bases,
        levels_payload=levels_payload,
    )


def run_board_engine(
    pre: BoardPreflight,
    *,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Stage 2 of a board run: run the board `pre` approved, and report (card 203a9a5e).

    `pre` is the only input. The board is not read again, prefixes and bases
    are not derived again, and the up-front claim check is not repeated: that
    pre-flight is the caller's. Each milestone's own pre-flight inside
    `_run_milestone_async` still runs, so a claim taken since `pre` is an
    `escalated` entry. With nothing open it returns `ok` with no milestones and
    starts no event loop. Otherwise one `asyncio.run(_run_board_async(...))`
    on `pre.max_concurrent`, and `run_board`'s payload with `pre.levels_payload`
    as its `levels`. Synchronous; does not mutate `pre`.
    """
    if not pre.milestones:
        return {"ok": True, "board": True, "levels": pre.levels_payload, "milestones": []}
    entries = asyncio.run(
        _run_board_async(
            pre.milestones,
            prefixes=pre.prefixes,
            bases=pre.bases,
            root=pre.root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            driver=driver,
            clock=clock,
            max_concurrent=pre.max_concurrent,
            control_interval=control_interval,
        )
    )
    return {
        "ok": all(entry["status"] == "done" for entry in entries),
        "board": True,
        "levels": pre.levels_payload,
        "milestones": entries,
    }


def run_board(
    *,
    repo_dir: Path,
    base_branch: str | None,
    branch_prefix_of: Callable[[models.CardNode], str],
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Drive every open milestone on the board as one grafo tree, and report.

    It composes `preflight_board` and `run_board_engine` (card 203a9a5e).

    Refusals come first, in this order, and each leaves nothing behind. Bad
    arguments are `ValueError` before the board is read: `max_concurrent < 1`
    and a missing `base_branch`, as `run_milestone` refuses them. Then come the
    open milestones: `board.roots()`, read once, leveled by `dag.board_levels`,
    so a done milestone with nothing open under it drops out and a blocker
    cycle is `DependencyCycleError`. Next, each milestone's prefix from
    `branch_prefix_of`, and each non-open blocker root's too (`board_prefixes`
    with `roots=`: blank or shared is `ValueError`). Next, each open
    milestone's base (`milestone_bases` over every root): its one open
    blocker's `<prefix>-integrate`, or its one unlanded blocker's when that
    branch exists locally (`_local_branch_exists`), else `base_branch`; two
    such blockers is `MilestoneBlockersError`, before the claims check. Last,
    one `cli.refuse_claimed` over every open milestone's
    `milestone_claims`, unioned in level order (`board_claims`): a key another
    live run holds is `ClaimedError` before any milestone starts, so there is
    no run row, run directory or lease for any of them. Each milestone's own
    pre-flight inside `_run_milestone_async` still runs and catches a claim
    taken after this one.

    Then one `asyncio.run` covers the whole board with one
    `asyncio.Semaphore(max_concurrent)` that every milestone's lanes share
    (`_run_board_async`), each milestone on its own base. A milestone runs
    once every open blocker finished `done`. A milestone whose blocker did
    not finish `done` is never dispatched and is reported `blocked`. A milestone that raises is reported
    `escalated` with `"<Type>: <msg>"` and never disturbs its siblings.

    Returns the plain payload, not the CLI envelope:
    `{"ok", "board": True, "levels": [{"level", "milestones": [ids]}],
    "milestones": [entry, ...]}`. Entries are in level order. A dispatched
    entry is `{"milestone_id", "status", **its run payload}`. `ok` is true
    only when every entry is `done`. An empty board is `ok` with nothing run.

    Known limitation, kept on purpose: there is no board-level Run record.
    Each milestone keeps its own Run row and nothing else records what the
    board run had dispatched, so a crash mid-board loses only that
    bookkeeping. To recover, re-run `am run --board`: done milestones drop out
    through `board_levels` and the claims. Or resume a stopped or escalated
    milestone on its own with `am resume <run-id>`.
    """
    pre = preflight_board(
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix_of=branch_prefix_of,
        max_concurrent=max_concurrent,
    )
    return run_board_engine(
        pre,
        commands=commands,
        allow_no_verification=allow_no_verification,
        runner_factory=runner_factory,
        driver=driver,
        clock=clock,
        control_interval=control_interval,
    )


class BoardLogExistsError(ValueError):
    """A board detach found its log already there (card 03f027ea).

    Another board run on this repository was detached in the same second, so
    the two would share a log and a report. Subclasses `ValueError`, so it is
    in `cli.HANDLED`: an `ok: false` envelope and exit 3, nothing forked, and
    the existing log untouched.
    """


def detach_board(
    *,
    repo_dir: Path,
    base_branch: str | None,
    branch_prefix_of: Callable[[models.CardNode], str],
    detacher: detach.Detacher,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    max_concurrent: int = 1,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """`am run --board --detach` (card 03f027ea): pre-flight here, the board run in a child.

    `preflight_board` runs exactly as for `run_board`, so every refusal is
    the same and leaves nothing behind, `<data dir>/boards/` included. Then
    the log `<data dir>/boards/<stamp>-<digest>.log` is created exclusively
    at 0600 (`<stamp>` from `clock`, `<digest>` the repository's
    `paths.project_digest`); an existing one is `BoardLogExistsError`.
    `detacher` gets the body and the log, and the child is let go at once:
    a board run has no run id, store or lease to point at it, so this does
    not go through `cli.hand_off_to_child`. Nothing holds a store across the
    fork: `preflight_board` opens none.

    The child runs `run_board_engine` on this very `pre` (no second board
    read or claim check; each milestone's own run is created when it is
    dispatched) and writes `<stem>.report.json`: the envelope a foreground
    `am run --board` would have printed, or a `HANDLED` error's envelope.
    Anything else propagates with no report; its traceback goes to the log.

    Returns `{"board", "detached", "pid", "log", "report", "levels"}`, with
    `levels` the foreground payload's.
    """
    pre = preflight_board(
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix_of=branch_prefix_of,
        max_concurrent=max_concurrent,
    )
    stem = f"{clock().strftime(runs.RUN_ID_TIME_FORMAT)}-{paths.project_digest(pre.root)}"
    try:
        log = detach.create_board_log(stem)
    except FileExistsError as error:
        raise BoardLogExistsError(
            f"board log {error.filename} already exists: another board run on this "
            "repository was detached in the same second; run the command again"
        ) from None
    report = detach.board_report_path(stem)

    def body() -> None:
        try:
            payload = run_board_engine(
                pre,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                driver=driver,
                clock=clock,
                control_interval=control_interval,
            )
        except cli.HANDLED as error:
            detach.write_board_report(report, cli.render(cli.error_envelope(error)))
            return
        detach.write_board_report(report, cli.render(cli.ok_envelope(payload)))

    spawned = detacher(body, log)
    spawned.go()
    return {
        "board": True,
        "detached": True,
        "pid": spawned.pid,
        "log": str(log),
        "report": str(report),
        "levels": pre.levels_payload,
    }


async def _run_board_async(
    milestones: Sequence[models.CardNode],
    *,
    prefixes: Mapping[str, str],
    bases: Mapping[str, str],
    root: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: runs.RunnerFactory | None,
    driver: Driver | None,
    clock: Callable[[], datetime],
    max_concurrent: int,
    control_interval: float,
) -> list[dict[str, Any]]:
    """`run_board`'s one event loop: one entry per milestone, in `milestones` order.

    Each milestone is dispatched on its own entry in `bases` (`milestone_bases`'
    answer), never on one shared base; `bases` keys every open milestone, so a
    missing key is a caller bug and surfaces as that milestone's `escalated`
    entry. The tree comes from `build_dag_tree`, with each milestone's
    blockers being its `blocked_by` restricted to `milestones` (a done blocker is not here,
    so it is satisfied). grafo itself holds a node back until every one of its
    parents' edges has fired, so by the time a node body runs, every blocker's
    `milestone_ok` entry is already set -- no extra waiting needed here. A
    node never raises an `Exception`, so an edge fires whether or not the
    blocker finished `done`; a node dispatches only when every blocker is
    clean, and otherwise records `blocked` with the unclean blockers. A
    non-`Exception` `BaseException` (not a cancel) ends the whole run through
    `run_until_killed`, as in `supervise`, because grafo would drop it and
    hang. The `grafo` logger is at CRITICAL for exactly this call.
    """
    grafo_logger = logging.getLogger(GRAFO_LOGGER)
    level_before = grafo_logger.level
    grafo_logger.setLevel(logging.CRITICAL)
    try:
        slots = asyncio.Semaphore(max_concurrent)
        open_ids = {card.id for card in milestones}
        blockers = {
            card.id: [
                blocker for blocker in dict.fromkeys(card.blocked_by) if blocker in open_ids
            ]
            for card in milestones
        }
        milestone_ok: dict[str, bool] = {}
        entries: dict[str, dict[str, Any]] = {}
        fatal: list[BaseException] = []
        killed = asyncio.Event()

        async def dispatch(card: models.CardNode) -> dict[str, Any]:
            try:
                payload = await _run_milestone_async(
                    card.id,
                    repo_dir=root,
                    base_branch=bases[card.id],
                    branch_prefix=prefixes[card.id],
                    commands=commands,
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    driver=driver,
                    clock=clock,
                    max_concurrent=max_concurrent,
                    control_interval=control_interval,
                    slots=slots,
                )
            except Exception as error:
                return {
                    "milestone_id": card.id,
                    "status": "escalated",
                    "error": f"{type(error).__name__}: {error}",
                }
            return {"milestone_id": card.id, "status": milestone_status(payload), **payload}

        def node_coroutine(card: models.CardNode) -> Callable[..., Awaitable[str]]:
            async def run(**_forwarded: Any) -> str:
                try:
                    unclean = [
                        blocker
                        for blocker in blockers[card.id]
                        if not milestone_ok.get(blocker, False)
                    ]
                    if unclean:
                        entries[card.id] = {
                            "milestone_id": card.id,
                            "status": "blocked",
                            "blocked_by": unclean,
                        }
                    else:
                        entries[card.id] = await dispatch(card)
                except BaseException as error:
                    if not isinstance(error, (Exception, asyncio.CancelledError)):
                        fatal.append(error)
                        killed.set()
                    raise
                finally:
                    milestone_ok[card.id] = entries.get(card.id, {}).get("status") == "done"
                return card.id

            return run

        _nodes, roots = await build_dag_tree(
            milestones,
            id_of=lambda card: card.id,
            blockers_of=lambda card: blockers[card.id],
            node_factory=node_coroutine,
        )
        executor = grafo.TreeExecutor(uuid="board", roots=roots)
        await run_until_killed(executor.run(), killed, fatal)
        return [entries[card.id] for card in milestones]
    finally:
        grafo_logger.setLevel(level_before)
