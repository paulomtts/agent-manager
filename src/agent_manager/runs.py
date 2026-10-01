"""Run helpers with no Typer in them (architecture cleanup, decision S1).

`cli.py` composes and renders; the plain pieces a run needs -- where its repo
is, what it is called, where its worktree goes, how a runner is made, which
gate parameters it binds, how an interrupted run is picked back up (which
subtask is resumable, which attempts were orphaned, which checkpoint a relaunch
continues from, and the refusals those raise), and the `--dry-run` preview's
levels and Integrate plan -- live here so the modules downstream of `cli` can
reach them without importing the Typer app. `cli.py` re-exports every name
defined here, so `cli.X is runs.X`. This module never imports `cli` at load
time; `compute_dry_run_plan` imports `integration` (which still imports `cli`)
only when called.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from agent_manager import census, dag, models, store as store_module
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.walk import AgentPhaseRunner
from agent_manager.store import Store
from agent_manager.workflow import task as task_workflow

RUN_ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
"""Sortable, path-safe, second-resolution UTC. Run ids are directory names."""

WORKTREE_PARTS = (".claude", "worktrees")
"""Where a subtask's worktree lives under the repo, matching the layout the rest
of this project already uses."""


class CliError(RuntimeError):
    """The command refused to start a run. One base type for the envelope."""


class RepoDirError(CliError):
    """`--repo-dir` does not name a directory this tool can work in."""


class UnknownRunError(CliError):
    """`status` was asked for a run this project's projection does not hold.

    A `CliError` so it rides the existing `HANDLED` tuple into an `ok: false`
    envelope at exit 3 rather than reaching the renderer as a `None` tree. The
    same class covers "no most-recent run to default to": both are the same
    refusal -- the command was asked for a run and there is none -- and the
    message is what tells the two apart.
    """


class NotResumableError(CliError):
    """The run was found, and it holds nothing `resume` can pick up.

    Its own type rather than `UnknownRunError`'s: the run and its tree read
    fine, so what an operator does next -- start a fresh `run --card`, wait for
    `retry`, or drive the subtasks one at a time -- depends entirely on the
    status this message names, and a script can branch on the `type` field.
    """


class CheckpointMismatchError(CliError, runtime_engine.CheckpointMismatch):
    """`resume` found a checkpoint saved under another `TASK`.

    A `CliError`, so it rides `HANDLED` to an `ok: false` envelope at exit 3,
    and a `runtime_engine.CheckpointMismatch`, so it is the engine's own
    refusal by type (card 02890d5d). The CLI raises it itself, before any
    write, rather than letting `run_subtask` raise it after the orphan
    attempts and the `started` rows were already recorded.
    """


def resolve_repo_dir(repo_dir: Path) -> Path:
    """`--repo-dir` as an existing absolute directory, or `RepoDirError`.

    Resolved before anything is derived from it: `steps/worktree.ensure` refuses
    a relative `worktree` or `repo_dir` outright, and the default value of the
    option is `.`.
    """
    resolved = Path(repo_dir).expanduser().resolve()
    if not resolved.is_dir():
        raise RepoDirError(
            f"--repo-dir {str(repo_dir)!r} is not a directory (resolved to {resolved})"
        )
    return resolved


def mint_run_id(card_id: str, now: datetime) -> str:
    """`<UTC timestamp>-<short card id>`: unique, sortable, and greppable.

    The short id comes from `dag`, like every other derived name in the program,
    which also means a card id that is not a UUID is refused here rather than
    producing a run directory nobody can trace back to a card.
    """
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{dag.short_id(card_id)}"


def worktree_for(repo_dir: Path, branch: str) -> Path:
    """`<repo_dir>/.claude/worktrees/<branch>`, absolute.

    Absolute because `steps/worktree.ensure` requires it, and built by joining
    the branch's own segments so a branch like `m1/task-x` becomes two path
    components rather than one with a slash in its name.
    """
    return Path(repo_dir).resolve().joinpath(*WORKTREE_PARTS, *branch.split("/"))


class RunnerFactory(Protocol):
    """How the command gets its `AgentPhaseRunner`.

    A factory rather than a runner, because a real `dispatch.AgentRunner` needs
    the store and three ids that do not exist until the run is
    already half set up -- and because a factory is the seam the tests replace
    to launch no harness at all (§14: the launcher is injected).
    """

    def __call__(
        self,
        *,
        store: Store,
        run_id: str,
        story_id: str,
        card_id: str,
    ) -> AgentPhaseRunner: ...


def gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]:
    """The gate parameters `TASK`'s gates bind and `subtask_context` lacks.

    `explore` gates on `verification_gate(suite_cmds, allow_no_verification,
    caller_provided)` and `exploration_output_gate(explore,
    provided_verification)`. Three of those four names come from the caller, and
    this is the caller. `bool()` is deliberate: the reducer tests
    `allow_no_verification is True`, so a truthy stand-in must not open the
    opt-out by accident. `caller_provided` is `False` and
    `provided_verification` is `None` because this card discovers no suite --
    per-run verification discovery is the milestone runner's, not this command's.
    """
    return {
        "suite_cmds": list(commands),
        "allow_no_verification": bool(allow_no_verification),
        "caller_provided": False,
        "provided_verification": None,
    }


def select_resumable(run: models.Run) -> tuple[models.StoryRun, models.SubtaskRun]:
    """The one subtask of `run` that was in flight, or a refusal naming why not.

    Pure over the tree `load_run` assembled, like `find_subtask`: which subtask
    is resumable is a question about recorded state, and answering it before any
    store is opened is what keeps a refusal from minting a run directory.

    Exactly one `started` or `stopped` subtask is the resumable shape. A
    `stopped` subtask (addendum P4) was parked between phases, and its parked
    checkpoint is what `resume` continues from (card 02890d5d). Zero means the
    run finished, escalated or never started, and the statuses are listed
    because the fix differs for each; an escalation is `retry`'s, never this
    command's. More than one is a milestone-shaped run: this command drives one
    subtask the way `run --card` does, and choosing between them would leave the
    rest recorded in flight with nothing driving them.
    """
    resumable = ("started", "stopped")
    wanted = " or ".join(repr(status) for status in resumable)
    in_flight = [
        (story, subtask)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.status in resumable
    ]
    if len(in_flight) == 1:
        return in_flight[0]
    if not in_flight:
        found = (
            ", ".join(
                f"{subtask.card_id}={subtask.status}"
                for story in run.stories
                for subtask in story.subtasks
            )
            or "no subtask at all"
        )
        raise NotResumableError(
            f"run {run.id!r} has no subtask recorded {wanted}, so there is no work"
            f" in flight to pick up (found: {found});"
            f" `agent-manager status {run.id}` shows the run as it stands"
        )
    cards = ", ".join(subtask.card_id for _story, subtask in in_flight)
    raise NotResumableError(
        f"run {run.id!r} has {len(in_flight)} subtasks recorded {wanted} ({cards}),"
        " and `resume` drives one subtask the way `run --card` does;"
        f" `agent-manager status {run.id}` shows all of them"
    )


def orphan_attempts(
    subtask: models.SubtaskRun,
) -> list[tuple[models.PhaseRun, models.Attempt]]:
    """Every attempt recorded `started` with no terminal event, in tree order.

    §9's "in-flight attempt": the manager was killed between the row that says a
    dispatch began and the row that says how it ended. The owning phase comes
    back with it because `Store.record_attempt` is keyed by phase name and an
    `Attempt` carries no back-reference, exactly as `find_subtask` returns the
    owning story.
    """
    return [
        (phase, attempt)
        for phase in subtask.phases
        for attempt in phase.attempts
        if attempt.status == "started"
    ]


def continuable_checkpoint(
    store: Store, card_id: str
) -> store_module.Checkpoint | None:
    """The open checkpoint a pygents relaunch continues `card_id` from, or `None`.

    `Store.latest_open_checkpoint` across every run, for `TASK`'s name. A row
    saved under another digest, or one holding no turn (a phase escalation,
    see `runtime_engine.pending_phase`), is `None` too: a relaunch never
    refuses, it starts the card from its first phase (card 02890d5d).
    """
    found = store.latest_open_checkpoint(card_id, task_workflow.TASK.name)
    if found is None or found.digest != task_workflow.TASK.digest():
        return None
    if runtime_engine.pending_phase(found) is None:
        return None
    return found


@dataclass(frozen=True)
class DryRunPlan:
    """What `compute_dry_run_plan` works out; `cli.dry_run_payload` wraps it.

    `levels` is one row per dispatch level and `integrate` is the terminal
    phase's plan, both already in the exact shape and key order `--dry-run`
    prints. Plain data, not Pydantic: it never crosses a process boundary.
    """

    levels: list[dict[str, Any]]
    integrate: dict[str, Any]


def compute_dry_run_plan(
    stories: Iterable[census.StoryPlan],
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int,
) -> DryRunPlan:
    """O3's preview: dispatch levels with each subtask's branch and base, then Integrate.

    Pure over the census, and every derivation belongs to `dag`. The cycle
    check runs first because a cycle is what breaks the geometry, and
    `story_root`'s own guard misses a cycle between two populated stories.
    `stories_by_id` covers every story, closed ones included, so a story
    blocked by a done story still roots on that story's tip. A story's
    `subtasks` lists only what would be dispatched, but each `base` comes
    from `stack_bases` over the full ordered list, so a done first subtask
    still anchors the second. Each level row says how many of its stories
    would run together: `min(len(level), max_concurrent)`. The caller refuses
    a bound below 1 and supplies the default.

    A story's `root` is `dag.story_root(...).branch`. A story with two or more
    in-milestone blockers is not refused here: its `root` is its own merged
    base branch and its row gains `merged_from`, the blockers in `blocked_by`
    order. The key is absent for every other row. The real run still refuses
    such a story (`orchestrate.plan_levels`).

    `integrate` is the terminal phase's plan (Integrate addendum I6): the
    branch every tip is merged into, its worktree under `repo_dir`, and the
    merge order `integration.merge_order` gives -- every story with subtasks,
    done or not. `repo_dir` is only joined onto, never read.
    """
    # `integration` imports `cli` at load time, and `cli` imports this module
    # at load time, so importing it at the top of this module would be
    # circular. By call time all three are loaded.
    from agent_manager import integration

    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    levels = dag.compute_levels(stories)
    stories_by_id = {story.id: story for story in stories}
    level_rows: list[dict[str, Any]] = []
    for index, level in enumerate(levels):
        story_rows: list[dict[str, Any]] = []
        for story in level:
            bases = dag.stack_bases(story, stories_by_id, branch_prefix, base_branch)
            root = dag.story_root(story, stories_by_id, branch_prefix, base_branch)
            row: dict[str, Any] = {
                "story": story.id,
                "title": story.title,
                "root": root.branch,
                "subtasks": [
                    {
                        "id": subtask.id,
                        "title": subtask.title,
                        "status": subtask.status,
                        "branch": dag.subtask_branch(branch_prefix, subtask),
                        "base": bases[subtask.id],
                    }
                    for subtask in dag.remaining_subtasks(story)
                ],
            }
            if root.kind == "merged":
                row["merged_from"] = list(root.blockers)
            story_rows.append(row)
        level_rows.append(
            {
                "level": index,
                "concurrent": min(len(level), max_concurrent),
                "stories": story_rows,
            }
        )
    integrate_branch = integration.integration_branch(branch_prefix)
    return DryRunPlan(
        levels=level_rows,
        integrate={
            "branch": integrate_branch,
            "worktree": str(worktree_for(repo_dir, integrate_branch)),
            "order": [
                {"story": story.id, "tip": tip}
                for story, tip in integration.merge_order(
                    stories, branch_prefix, base_branch
                )
            ],
        },
    )
