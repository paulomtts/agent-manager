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

A story with two or more in-milestone blockers roots on a merged base
(supervisor-tree §5): its lane waits for every blocker to finish clean, then
awaits `bases.build` with their tips (`blocker_tips`), after it took its slot
and before its first subtask, so that
subtask stacks on `<prefix>/base-<short id>`. A lone-blocker story stays the
fast path: no base branch and no extra verify.

Every derivation belongs to a collaborator: the milestone and its census to
`census`, waves, stack bases, roots and tips to `dag`, board reads to `board`,
rollup to `steps.rollup`, git to `steps.worktree.run_git`, run state to
`Store`. The terminal merge of every story tip belongs to `integration`. This
module decides only the order of those calls and what a run records.

The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle -- runs before the first write, so a refusal leaves no run
directory, no store, no fetch and no prune behind.

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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

import grafo

from agent_manager import bases, board, census, cli, dag, integration, models
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps import rollup, worktree
from agent_manager.store import Checkpoint, Store, load_run, open_db
from agent_manager.workflow import integrate as integrate_workflow
from agent_manager.workflow import task as task_workflow
from agent_manager.workflow.phases import Workflow

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
        runner_factory: cli.RunnerFactory | None = None,
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


def plan_levels(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[list[PlannedStory]]:
    """Dispatch levels with each pending story's bases and tip, derived before any write.

    The same composition as `cli.dry_run_payload`: the cycle check runs first,
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
    checkpoints: Mapping[str, Checkpoint] = field(default_factory=dict)
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
    checkpoints: Mapping[str, Checkpoint] | None = None,
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
    """
    remotes = worktree.run_git(["-C", str(root), "remote"]).split()
    if "origin" in remotes:
        worktree.run_git(["-C", str(root), "fetch", "origin"])
    worktree.run_git(["-C", str(root), "worktree", "prune"])


REOPENED_STATUSES = ("stopped", "escalated", "started")
"""The row statuses a resume records `started` again (spec, point 2)."""


def resumable_milestone_run(root: Path, run_id: str) -> models.Run:
    """The recorded milestone run `run_id`, or the refusal that says why not.

    Read-only through the free `open_db` / `load_run`, like `cli.resume_run`:
    `Store.open` would construct a `Journal`. An unknown run, a run of
    another workflow, and a `done` run are refused (card 54e4ec29).
    """
    conn = open_db(root)
    try:
        run = load_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise cli.UnknownRunError(
            f"run {run_id!r} is not in the projection for {root}"
            " (`agent-manager runs` lists the ones that are)"
        )
    if run.workflow != MILESTONE_WORKFLOW:
        raise cli.NotResumableError(
            f"run {run_id!r} is a {run.workflow!r} run, not a {MILESTONE_WORKFLOW!r} run"
        )
    if run.status == "done":
        raise cli.NotResumableError(
            f"run {run.id} finished; start new work with am run --milestone"
        )
    return run


def find_run_milestone(
    roots: Sequence[models.CardNode] | None, run_id: str
) -> models.CardNode:
    """The one root card whose short id ends `run_id`.

    `cli.mint_run_id` builds a milestone run's id as `<timestamp>-<short
    milestone id>`, and `models.Run` records no milestone id of its own, so
    the id is how a resume finds its milestone. A title edit cannot break it.
    """
    short = run_id.rsplit("-", 1)[-1]
    matches = [node for node in roots or [] if dag.short_id(node.id) == short]
    if len(matches) != 1:
        raise cli.NotResumableError(
            f"run {run_id!r} belongs to milestone {short}, and {len(matches)} root"
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


def _refuse_changed_workflow(checkpoint: Checkpoint, workflow: Workflow, run_id: str) -> None:
    """`cli.CheckpointMismatchError` when `checkpoint` was saved under another digest.

    Worded like `cli.checkpoint_resume_phase`'s refusal, with the milestone
    remedy.
    """
    digest = workflow.digest()
    if checkpoint.digest != digest:
        raise cli.CheckpointMismatchError(
            f"workflow changed since checkpoint: checkpoint #{checkpoint.seq} of card"
            f" {checkpoint.card_id} in run {run_id!r} was saved under digest"
            f" {checkpoint.digest}, but workflow {workflow.name!r} now has digest"
            f" {digest}; start new work with am run --milestone"
        )


def resume_point(store: Store, card_id: str, workflow: Workflow) -> Checkpoint | None:
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
) -> dict[str, Checkpoint]:
    """`resume_point` for every open card, keyed by card id, only where there is one.

    Reads only, so a refusal on any card leaves everything as it was: the
    whole resume is refused (spec, Error paths).
    """
    found: dict[str, Checkpoint] = {}
    for card_id, workflow in cards:
        checkpoint = resume_point(store, card_id, workflow)
        if checkpoint is not None:
            found[card_id] = checkpoint
    return found


def reopen_rows(store: Store, run: models.Run, open_card_ids: set[str]) -> None:
    """Mark every orphan attempt `harness_error`, then reopen the open rows.

    `run` is the tree as the interrupted run left it. An orphan is
    `cli.orphan_attempts`' in-flight attempt, marked as `cli`'s
    `_resume_from_checkpoint` marks it. A subtask or resolver row of an open
    card that is stopped, escalated or started is recorded `started`.
    """
    for story in run.stories:
        for subtask in story.subtasks:
            for phase, attempt in cli.orphan_attempts(subtask):
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
                    worktree_path=cli.worktree_for(root, branch),
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
    runner_factory: cli.RunnerFactory | None,
    stop: StopSignal,
    resume_from: Checkpoint | None = None,
) -> None:
    """Await `bases.build` for one merged-root story (supervisor-tree §5).

    `tips` are the blockers' tips, already resolved by the caller in
    `root_plan.blockers` order (`blocker_tips`). `bases.build` is read off its
    module at call time so a test can replace it. A `None` factory is
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


async def blocker_tips(
    root_plan: dag.RootPlan,
    plan: SupervisorPlan,
    story_done: Mapping[str, asyncio.Event],
    story_ok: Mapping[str, bool],
) -> list[str] | None:
    """Every blocker's tip, in `root_plan.blockers` order, once each is done.

    A merged-root story is itself one of `supervise`'s grafo roots (T1's own
    dependency edges are not used for a 2+-blocker join: grafo's dynamic
    worker pool can starve a join node forever when an unrelated sibling lane
    is still in flight, a defect in grafo itself, confirmed outside this
    module and out of scope to fix there). This lane instead waits on each
    blocker's own completion signal, then reads its tip off `plan.tips` --
    the same value the blocker's own lane would have returned, computed at
    plan time (`dag.story_tip`), so no data is lost by not using grafo's
    runtime forwarding for this edge. None means a blocker did not finish
    clean (escalated or stopped): the caller must not build the base.
    """
    for blocker in root_plan.blockers:
        await story_done[blocker].wait()
    if not all(story_ok.get(blocker, False) for blocker in root_plan.blockers):
        return None
    return [plan.tips[blocker] for blocker in root_plan.blockers]


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
    runner_factory: cli.RunnerFactory | None,
    slots: asyncio.Semaphore,
    stop: StopSignal,
    finished: dict[str, LaneOutcome],
) -> str:
    """A subtask-less story's lane: build its merged base, return it as its tip.

    It takes a slot, since a base can dispatch a resolver, and checks the stop
    first. The failure paths are `lane`'s for a merged base, but with no
    subtask to name and no store row to write -- `record_plan` records only
    stories with work -- so every outcome has `level=None` and
    `collect_outcomes` reports it after the waves.
    """

    def outcome(kind: LaneKind, **fields: Any) -> LaneOutcome:
        return LaneOutcome(kind=kind, story=story.id, level=None, **fields)

    async with slots:
        if stop.triggered:
            raise LaneStopped(outcome("stopped"))
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
                raise LaneStopped(outcome("stopped")) from error
            stop.trigger(story.id)
            raise LaneEscalated(
                outcome("escalated", failed_phase="base", detail=error.detail)
            ) from error
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            stop.trigger(story.id)
            raise LaneEscalated(
                outcome("escalated", detail=f"{type(error).__name__}: {error}")
            ) from error
    finished[story.id] = outcome("done", base=root_plan)
    return plan.tips[story.id]


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
    story_done: Mapping[str, asyncio.Event],
    story_ok: Mapping[str, bool],
) -> str:
    """One story's node coroutine (T1, T4, T6): drive its remaining subtasks, return its tip.

    A story with nothing left to run returns its tip without taking a slot,
    unless `builds_a_base_alone` says it must first build its merged base
    (`base_only_lane`); a `merged` one still waits for its blockers first. Otherwise the lane takes a slot -- grafo started it, so
    every blocker already succeeded -- and drives the remaining subtasks in
    census order, each on the base `record_plan` recorded for it. Before each
    subtask it checks the stop: if it fired, the story is recorded `stopped`
    and `LaneStopped` is raised with that subtask never driven. A `stopped`
    summary records the subtask and story `stopped` and raises `LaneStopped`.
    Any other non-`done` summary, or any `Exception` while handling a
    subtask (a lane bug), triggers the stop first and raises `LaneEscalated`
    at that subtask. `LaneEscalated`/`LaneStopped` pass through the catch-all
    unchanged. A `BaseException` is never caught.

    A story whose root is `merged` is one of `supervise`'s grafo roots (not
    reached through a blocker's edge; `blocker_tips` explains why), so it
    waits for its own blockers here, before taking a slot: `blocker_tips`
    returns None when a blocker did not finish clean, and this lane then
    returns its tip without ever taking a slot, exactly as a story whose
    blocker's edge grafo never fired would (T1's existing contract). Once
    every blocker is clean, the lane takes its slot, checks the stop (fired:
    `stopped` at its first subtask, nothing built), then awaits
    `build_merged_base` before its first subtask, whose recorded base is the
    merged base branch. `BaseFailed(stopped=False)` triggers the stop, records
    the story `escalated` and raises `LaneEscalated` with `failed_phase="base"`,
    no subtask and the failure's detail; `BaseFailed(stopped=True)` records it
    `stopped` and raises `LaneStopped` with no subtask. Any other error from
    the base reaches the catch-all with no subtask. Once built, the outcome
    carries the story's `RootPlan` as `base`, whatever happens after.

    Each subtask's open checkpoint is looked up first
    (`cli.continuable_checkpoint`), inside the same `try`, and handed to the
    driver as `resume_from` when it can be continued.

    On a resume (`plan.resuming`, card 54e4ec29) the subtask's checkpoint is
    `plan.checkpoints`' and the lenient relaunch lookup is never read; a
    merged base gets its resolver's checkpoint the same way.
    """
    root_plan = plan.roots[story.id]
    planned = plan.planned.get(story.id)
    tips: list[str] | None = None
    if root_plan.kind == "merged":
        # Waited for even with nothing left to run: grafo would have held a
        # blocked story, done or not, behind its blockers' edges.
        tips = await blocker_tips(root_plan, plan, story_done, story_ok)
        if tips is None:
            return plan.tips[story.id]
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
    completed: list[str] = []
    warnings: list[str] = []
    built: dag.RootPlan | None = None

    def outcome(kind: LaneKind, subtask: str | None, **fields: Any) -> LaneOutcome:
        return LaneOutcome(
            kind=kind,
            story=story.id,
            level=planned.level,
            subtask=subtask,
            completed=tuple(completed),
            warnings=tuple(warnings),
            base=built,
            **fields,
        )

    async with slots:
        current: census.SubtaskPlan | None = None
        try:
            if root_plan.kind == "merged":
                # Checked before the base as before every subtask: a lane that
                # finds the stop fired builds nothing (spec, first error path).
                if stop.triggered:
                    store.record_story(story_row.model_copy(update={"status": "stopped"}))
                    raise LaneStopped(outcome("stopped", planned.remaining[0].id))
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
                        store.record_story(story_row.model_copy(update={"status": "stopped"}))
                        raise LaneStopped(outcome("stopped", None)) from error
                    stop.trigger(story.id)
                    store.record_story(story_row.model_copy(update={"status": "escalated"}))
                    raise LaneEscalated(
                        outcome("escalated", None, failed_phase="base", detail=error.detail)
                    ) from error
                built = root_plan
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
                # Relaunch continuation (card 02890d5d) is lenient and reads
                # across runs; a resume (card 54e4ec29) hands on exactly the
                # checkpoints `resume_checkpoints` already validated. Either
                # way the keyword is passed only when there is a row, so a
                # driver that predates it works.
                extra: dict[str, Any] = {}
                if plan.resuming:
                    checkpoint = plan.checkpoints.get(subtask.id)
                else:
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
    blocker, forwarding the blocker's tip as `tip_<short id>`, for a story
    whose root is a single blocker. A story rooted on a `merged` base (two or
    more in-milestone blockers) is instead one of the executor's roots itself,
    with no incoming edge: grafo's dynamic worker pool can starve a 2+-parent
    join forever when an unrelated sibling lane is still in flight (confirmed
    outside this module, in the pinned grafo release; not a `dag`/`bases`
    defect, and out of scope to fix in grafo). Its lane instead waits on each
    blocker's own completion, signalled by `story_done`/`story_ok` below, and
    reads the blocker's tip off `plan.tips` (`blocker_tips`); every lane sets
    its own signal on exit, success or not, so this never hangs. The
    executor's roots are therefore the stories with no in-milestone blocker,
    plus every merged-root story; a milestone with no story has no tree to run.

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
        story_done: dict[str, asyncio.Event] = {story.id: asyncio.Event() for story in plan.stories}
        story_ok: dict[str, bool] = {}

        def node_coroutine(story: census.StoryPlan) -> Callable[..., Any]:
            async def run(**tips: str) -> str:
                try:
                    result = await lane(
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
                        story_done=story_done,
                        story_ok=story_ok,
                    )
                except BaseException:
                    story_ok[story.id] = False
                    raise
                else:
                    story_ok[story.id] = True
                    return result
                finally:
                    story_done[story.id].set()

            return run

        nodes = {
            story.id: grafo.Node(coroutine=node_coroutine(story), uuid=story.id, timeout=None)
            for story in plan.stories
        }
        for story in plan.stories:
            root_plan = plan.roots[story.id]
            if root_plan.kind == "merged":
                continue
            for blocker in root_plan.blockers:
                await nodes[blocker].connect(
                    nodes[story.id], forward=f"tip_{dag.short_id(blocker)}"
                )
        roots = [
            nodes[story.id]
            for story in plan.stories
            if not plan.roots[story.id].blockers or plan.roots[story.id].kind == "merged"
        ]
        errors: list[BaseException] = []
        if roots:
            executor = grafo.TreeExecutor(uuid=run_id, roots=roots)
            await executor.run()
            errors = list(executor.errors)
        return collect_outcomes(plan, nodes, errors, finished)
    finally:
        grafo_logger.setLevel(level_before)


def run_milestone(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: cli.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    resume_run_id: str | None = None,
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

    `resume_run_id` continues that milestone run instead (card 54e4ec29).
    `milestone`, `base_branch`, `branch_prefix`, `max_concurrent` and
    `clock` are then not read: the milestone is the one the run id names
    (`find_run_milestone`) and the rest is what the run recorded. The plan is
    re-derived from the board as a fresh run derives it. Every refusal -- an
    unknown, non-milestone or `done` run, an unknown milestone, a blocker
    cycle, and a checkpoint saved under another workflow digest -- comes
    before the first write and before git is refreshed. Then the run is
    recorded `started`, the plan is re-recorded, orphan attempts are marked
    `harness_error` and every open stopped, escalated or started row is
    recorded `started` (`reopen_rows`), and `supervise` runs under the same
    run id with each open checkpoint handed on as `resume_from`. Every
    payload gains `resumed: true`; `completed` is this invocation's work.
    """
    if resume_run_id is None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
        if milestone is None or base_branch is None or branch_prefix is None:
            raise ValueError(
                "a fresh milestone run needs a milestone, a base branch and a branch prefix"
            )
    root = cli.resolve_repo_dir(repo_dir)
    resumed = None if resume_run_id is None else resumable_milestone_run(root, resume_run_id)
    if resumed is not None:
        base_branch = resumed.base_branch
        branch_prefix = resumed.branch_prefix
        max_concurrent = resumed.config.max_concurrent_stories
    roots = board.roots(repo_dir=root)
    if resumed is None:
        milestone_card = census.find_milestone(roots, milestone)
    else:
        milestone_card = find_run_milestone(roots, resumed.id)
    plan = census.flatten_milestone(board.tree(milestone_card.id, repo_dir=root))
    levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    tips = story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    drive = cli.drive_subtask_async if driver is None else driver

    if resumed is None:
        # The first side effect. It runs after every refusal and before the store
        # is opened, so a failed fetch leaves no run directory behind.
        refresh_git(root)
        started_at = clock()
        run_id = cli.mint_run_id(milestone_card.id, started_at)
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
    else:
        run_id = resumed.id
        run_record = resumed.model_copy(update={"status": "started"})
    store = Store.open(root, run_id)
    try:
        checkpoints: dict[str, Checkpoint] | None = None
        cards: list[tuple[str, Workflow]] = []
        if resumed is not None:
            cards = open_cards(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
            # The store's own refusal, a checkpoint saved under another
            # workflow, comes before the first write and before git is touched.
            checkpoints = resume_checkpoints(store, cards)
            refresh_git(root)
        store.record_run(run_record)
        rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
        if resumed is not None:
            # After `record_plan`, which records every planned row `pending`.
            reopen_rows(store, resumed, {card_id for card_id, _workflow in cards})
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
                    checkpoints=checkpoints,
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
        built_bases = bases_payload(outcomes)

        def report(payload: dict[str, Any]) -> dict[str, Any]:
            """Every payload shape on the same terms: `bases` when built, `resumed` on a resume."""
            if resumed is not None:
                payload["resumed"] = True
            return with_bases(payload, built_bases)

        if any(outcome.kind == "escalated" for outcome in outcomes):
            store.record_run(run_record.model_copy(update={"status": "escalated"}))
            primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
            return report(escalated_payload(run_id, primary, outcomes, warnings))

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
            return report(integrate_escalated_payload(run_id, outcome, warnings))

        store.record_run(run_record.model_copy(update={"status": "done"}))
        return report(
            {
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
        )
    finally:
        store.close()
