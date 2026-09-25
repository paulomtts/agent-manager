"""The sequential milestone runner (orchestration addendum O6).

`run_milestone` drives every remaining subtask of one milestone, one at a time,
through the shared per-subtask driver (O4). Every derivation belongs to a
collaborator: the milestone and its census to `census`, levels, stack bases and
tips to `dag`, board reads to `board`, rollup to `steps.rollup`, git to
`steps.worktree.run_git`, run state to `Store`. This module decides only the
order of those calls and what a run records.

The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle, a story with two in-milestone blockers, a workflow that will
not load -- runs before the first write, so a refusal leaves no run directory,
no store, no fetch and no prune behind.

`cli` is imported as a module and every name on it is read at call time: the
CLI wiring card makes `cli` import this module, and binding a `cli` name at
import or definition time would break under that circular import. The clock
default is this module's own `_utcnow` for the same reason.

The module holds no mutable state of its own (O4).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from agent_manager import board, census, cli, dag, models
from agent_manager.steps import rollup, worktree
from agent_manager.store import Store
from agent_manager.workflow.loader import load_builtin

MILESTONE_WORKFLOW = "milestone"
"""The run's `workflow` field: a milestone run, distinct from `run --card`'s `task`."""


def _utcnow() -> datetime:
    """This module's own clock default. `cli._utcnow` is private, and binding a
    `cli` name at definition time would break under the circular import."""
    return datetime.now(timezone.utc)


class Driver(Protocol):
    """`cli.drive_subtask`'s keyword signature: drive one subtask, report the result.

    The seam the tests replace. Annotations are strings (`from __future__ import
    annotations`), so no `cli` name is resolved when this module is imported.
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

    What a human merges after a clean run, since this card has no Integrate.
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
    Best effort, like `mark_done` in `task.yaml`: a `BoardError` becomes a
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
) -> dict[str, Any]:
    """Drive every remaining subtask of `milestone`, one at a time, and report (O6).

    `milestone` is a card id or a title needle (O1). Everything that can refuse
    runs before the store is opened. Then one `milestone` run is recorded with
    its whole plan `pending`, and levels, stories and subtasks are walked in
    order. A subtask already `done` on the board is never driven, but its
    branch still anchors the next subtask's base. The card and its story are
    read fresh from the board before each subtask.
    """
    root = cli.resolve_repo_dir(repo_dir)
    milestone_card = census.find_milestone(board.roots(repo_dir=root), milestone)
    plan = census.flatten_milestone(board.tree(milestone_card.id, repo_dir=root))
    levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    tips = story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    # Fail-fast preflight, as `run_card` does: a workflow that will not load
    # must leave no run directory. The driver loads its own copy.
    load_builtin(cli.WORKFLOW_NAME)
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
            config=models.RunConfig(),
        )
        store.record_run(run_record)
        rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
        warnings = reroll_stale_stories(plan.stories, root)
        completed: list[str] = []

        for level in levels:
            for planned in level:
                story_id = planned.story.id
                story_row, subtask_rows = rows[story_id]
                for position, subtask in enumerate(planned.remaining):
                    card = board.show(subtask.id, repo_dir=root)
                    parent = board.show(story_id, repo_dir=root)
                    started = subtask_rows[subtask.id].model_copy(update={"status": "started"})
                    store.record_subtask(story_id, started)
                    if position == 0:
                        store.record_story(story_row.model_copy(update={"status": "started"}))
                    try:
                        result = drive(
                            store=store,
                            run_id=run_id,
                            card=card,
                            parent=parent,
                            subtask=started,
                            repo_dir=root,
                            commands=list(commands),
                            allow_no_verification=allow_no_verification,
                            runner_factory=runner_factory,
                        )
                    except Exception as error:  # not BaseException: Ctrl-C must still stop
                        status = "escalated"
                        failed_phase: str | None = None
                        detail: str | None = f"{type(error).__name__}: {error}"
                    else:
                        warnings.extend(result.warnings)
                        status = result.summary.status
                        failed_phase = result.summary.failed_phase
                        detail = result.summary.detail

                    # Every non-`done` result is recorded `escalated` here. Nothing
                    # returns `stopped` yet; once the engine can (card 0d8b7c9a), a
                    # `stopped` summary must be handled before this branch rather
                    # than fall into it, because `stopped` is not an escalation (P4).
                    if status != "done":
                        store.record_subtask(
                            story_id, started.model_copy(update={"status": "escalated"})
                        )
                        store.record_story(story_row.model_copy(update={"status": "escalated"}))
                        store.record_run(run_record.model_copy(update={"status": "escalated"}))
                        return {
                            "escalated": True,
                            "run_id": run_id,
                            "level": planned.level,
                            "story": story_id,
                            "subtask": subtask.id,
                            "failed_phase": failed_phase,
                            "detail": detail,
                            "warnings": warnings,
                        }

                    store.record_subtask(story_id, started.model_copy(update={"status": "done"}))
                    completed.append(subtask.id)
                store.record_story(story_row.model_copy(update={"status": "done"}))

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
        }
    finally:
        store.close()
