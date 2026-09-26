"""The milestone runner (orchestration addendum O6, parallel-stories P1/P4/P6).

`run_milestone` drives every remaining subtask of one milestone through the
shared per-subtask driver (O4). Each dependency level's stories run as lanes on
a pool bounded by `max_concurrent`; a story's subtasks stay strictly sequential
and level N+1 starts only after every lane of level N returns. Every derivation
belongs to a collaborator: the milestone and its census to `census`, levels,
stack bases and tips to `dag`, board reads to `board`, rollup to
`steps.rollup`, git to `steps.worktree.run_git`, run state to `Store`. The
terminal merge of every story tip belongs to `integration`. This module decides only the
order of those calls and what a run records.

The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle, a story with two in-milestone blockers -- runs before the
first write, so a refusal leaves no run directory, no store, no fetch and no
prune behind.

`cli` is imported as a module and every name on it is read at call time: the
CLI wiring card makes `cli` import this module, and binding a `cli` name at
import or definition time would break under that circular import. The clock
default is this module's own `_utcnow` for the same reason.

The module holds no mutable state of its own (O4). A run's lock and stop event
live in a `RunStop` that `run_milestone` creates for that run.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from agent_manager import board, census, cli, dag, integration, models
from agent_manager.steps import rollup, worktree
from agent_manager.store import Checkpoint, Store

MILESTONE_WORKFLOW = "milestone"
"""The run's `workflow` field: a milestone run, distinct from `run --card`'s `task`."""


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


LaneKind = Literal["done", "escalated", "stopped", "not_started"]
"""How one story's lane ended: finished, escalated, parked by the stop after it
had started, or never started because the stop was already set."""


@dataclass(frozen=True)
class LaneOutcome:
    """What one story's lane did. Internal state, so a dataclass (CLAUDE.md).

    `subtask` is the subtask that escalated or was parked. `failed_phase` and
    `detail` describe an escalation, `before_phase` a stop. `completed` and
    `warnings` are this lane's own, in the order they arrived; `run_milestone`
    merges them across lanes in census order.
    """

    kind: LaneKind
    story: str
    level: int
    subtask: str | None = None
    failed_phase: str | None = None
    detail: str | None = None
    before_phase: str | None = None
    completed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass
class RunStop:
    """One run's cooperative stop, shared by every lane of that run (P4, P6).

    `run_milestone` creates one per run, so this module still holds no mutable
    state of its own. Each lane hands `event.is_set` to the driver as
    `should_stop`. `lock` decides which escalation came first, so `primary` is
    well defined however the lanes interleave.
    """

    event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    primary: str | None = None

    def escalate(self, story_id: str) -> None:
        """Set the stop, and name `story_id` the primary escalation if none is yet."""
        with self.lock:
            if self.primary is None:
                self.primary = story_id
            self.event.set()


def escalated_payload(
    run_id: str,
    primary_story: str | None,
    outcomes: Sequence[LaneOutcome],
    warnings: list[str],
) -> dict[str, Any]:
    """The escalated result for one level's lane outcomes, given in census order.

    The top-level keys describe the primary escalation, as the sequential
    runner always did. `also_escalated` lists the other escalations and
    `stopped` the parked lanes, both in census order, and each key is present
    only when its list is non-empty. A `primary_story` that names no escalated
    outcome falls back to the first escalation in census order.
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
    if also:
        payload["also_escalated"] = also
    if stopped:
        payload["stopped"] = stopped
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
    """`cli.drive_subtask`'s keyword signature: drive one subtask, report the result.

    The seam the tests replace. Annotations are strings (`from __future__ import
    annotations`), so no `cli` name is resolved when this module is imported.

    `resume_from` (card 02890d5d) is passed only when a relaunch found a
    checkpoint to continue, so a driver written before it keeps working.
    """

    def __call__(
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
    roots on that story's tip. A story with two in-milestone blockers raises
    `dag.StackRootError` here, from `stack_bases`.
    """
    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    stories_by_id = {story.id: story for story in stories}
    planned: list[list[PlannedStory]] = []
    for index, level in enumerate(dag.compute_levels(stories)):
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


def run_story_lane(
    planned: PlannedStory,
    *,
    store: Store,
    run_id: str,
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]],
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    stop: RunStop,
) -> LaneOutcome:
    """Drive one story's remaining subtasks in order, and say how the lane ended.

    The stop is checked here once, before the story's first subtask: if it is
    already set the story never starts and its rows stay `pending`. After that
    the lane never checks it itself; it hands `stop.event.is_set` to the driver
    and the engine parks between phases (P4), so a running phase is never
    interrupted. A `stopped` summary records the subtask and story `stopped`.
    Any other non-`done` result, or an `Exception` raised while handling a
    subtask, escalates through `stop.escalate`. A `BaseException` sets the stop
    so sibling lanes park, and propagates.

    Each subtask's open checkpoint is looked up first
    (`cli.continuable_checkpoint`), inside the same `try`, and handed to the
    driver as `resume_from` when it can be continued.
    """
    story_id = planned.story.id
    level = planned.level
    if stop.event.is_set():
        return LaneOutcome(kind="not_started", story=story_id, level=level)

    story_row, subtask_rows = rows[story_id]
    completed: list[str] = []
    warnings: list[str] = []
    for position, subtask in enumerate(planned.remaining):
        row = subtask_rows[subtask.id]
        try:
            card = board.show(subtask.id, repo_dir=root)
            parent = board.show(story_id, repo_dir=root)
            row = row.model_copy(update={"status": "started"})
            store.record_subtask(story_id, row)
            if position == 0:
                store.record_story(story_row.model_copy(update={"status": "started"}))
            # Relaunch continuation (card 02890d5d): a card whose open
            # checkpoint was saved under this `TASK` continues from it; a
            # changed workflow, a closed card or no row starts it fresh, with
            # no error. The keyword is passed only when there is a row, so a
            # driver that predates it keeps working.
            extra: dict[str, Any] = {}
            checkpoint = cli.continuable_checkpoint(store, subtask.id)
            if checkpoint is not None:
                extra["resume_from"] = checkpoint
            result = drive(
                store=store,
                run_id=run_id,
                card=card,
                parent=parent,
                subtask=row,
                repo_dir=root,
                commands=list(commands),
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                should_stop=stop.event.is_set,
                **extra,
            )
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            status = "escalated"
            failed_phase: str | None = None
            detail: str | None = f"{type(error).__name__}: {error}"
        except BaseException:
            stop.event.set()
            raise
        else:
            warnings.extend(result.warnings)
            status = result.summary.status
            failed_phase = result.summary.failed_phase
            detail = result.summary.detail

        # A `stopped` summary is handled before the non-`done` branch: a stop
        # is not an escalation (P4).
        if status == "stopped":
            store.record_subtask(story_id, row.model_copy(update={"status": "stopped"}))
            store.record_story(story_row.model_copy(update={"status": "stopped"}))
            return LaneOutcome(
                kind="stopped",
                story=story_id,
                level=level,
                subtask=subtask.id,
                before_phase=stopped_before_phase(detail),
                completed=tuple(completed),
                warnings=tuple(warnings),
            )
        if status != "done":
            stop.escalate(story_id)
            store.record_subtask(story_id, row.model_copy(update={"status": "escalated"}))
            store.record_story(story_row.model_copy(update={"status": "escalated"}))
            return LaneOutcome(
                kind="escalated",
                story=story_id,
                level=level,
                subtask=subtask.id,
                failed_phase=failed_phase,
                detail=detail,
                completed=tuple(completed),
                warnings=tuple(warnings),
            )

        store.record_subtask(story_id, row.model_copy(update={"status": "done"}))
        completed.append(subtask.id)

    store.record_story(story_row.model_copy(update={"status": "done"}))
    return LaneOutcome(
        kind="done",
        story=story_id,
        level=level,
        completed=tuple(completed),
        warnings=tuple(warnings),
    )


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
    """Drive every remaining subtask of `milestone`, level by level, and report (O6).

    `milestone` is a card id or a title needle (O1). Everything that can refuse,
    `max_concurrent < 1` included, runs before the store is opened. Then one
    `milestone` run is recorded with its whole plan `pending`, and each level's
    stories run as lanes on a pool of `max_concurrent` threads, with a barrier
    between levels. A subtask already `done` on the board is never driven, but
    its branch still anchors the next subtask's base. The card and its story
    are read fresh from the board before each subtask.

    The first escalation sets the run's stop: lanes already running park at
    their next phase boundary and are recorded `stopped`, lanes not yet started
    leave their story `pending`, and no later level is scheduled. At
    `max_concurrent=1` the result is exactly the sequential runner's.

    When every level finished clean -- or none had anything to run --
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
    drive = cli.drive_subtask if driver is None else driver

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
        stop = RunStop()

        for level in levels:
            with ThreadPoolExecutor(
                max_workers=max_concurrent, thread_name_prefix="am-lane"
            ) as pool:
                futures = [
                    pool.submit(
                        run_story_lane,
                        planned,
                        store=store,
                        run_id=run_id,
                        rows=rows,
                        root=root,
                        drive=drive,
                        commands=list(commands),
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        stop=stop,
                    )
                    for planned in level
                ]
            # Leaving the `with` block is the level barrier: every lane has
            # returned. `result()` re-raises a lane's BaseException here, after
            # the pool has shut down, in census order.
            outcomes = [future.result() for future in futures]
            # Census order, never completion order: deterministic at any bound.
            for outcome in outcomes:
                completed.extend(outcome.completed)
                warnings.extend(outcome.warnings)
            if any(outcome.kind == "escalated" for outcome in outcomes):
                store.record_run(run_record.model_copy(update={"status": "escalated"}))
                return escalated_payload(run_id, stop.primary, outcomes, warnings)

        # Integrate (addendum I6) runs only once every level finished clean,
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
