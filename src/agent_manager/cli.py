"""The Typer app (design §4 line 128, §10 lines 388-411).

This module composes and renders; it decides nothing a collaborator already
decides. Branch names come from `dag`, board reads from `board`, artifact paths
from `paths` via `store`, the phase walk from `engine`, and the dispatch from
`dispatch.AgentRunner`. §4 calls this file "typer app" and that is the whole
constraint: no step logic, no gate logic, no branch strings built by hand, and
no run state written anywhere but through `Store`.

Output is brd's envelope, because a human and a script read the same two tools
and `{"ok": ..., "data": ...}` is already what one of them prints (CLAUDE.md,
§10 line 404). JSON is one line by default and indented under `--pretty`.

Exit codes carry what the envelope cannot: `0` for a subtask that finished, `1`
for one that escalated -- an escalation is a truthful result, so the envelope
stays `ok: true` -- and `3` for "this tool could not run that", leaving `2` to
Typer's own usage errors.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import typer

from agent_manager import (
    board,
    census,
    dag,
    dispatch,
    models,
    prompt,
    store as store_module,
)
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.walk import AgentPhaseRunner, SubtaskSummary
from agent_manager.harness.launcher import run_direct
from agent_manager.runtime import engine as runtime_engine
from agent_manager.store import Store
from agent_manager.workflow import task as task_workflow

EXIT_ESCALATED = 1
"""The subtask escalated. §12: a full stop a human has to read."""

EXIT_ERROR = 3
"""The tool could not run the subtask at all. `2` belongs to Typer's usage errors."""

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


class ParentlessCardError(CliError):
    """The card has no parent story.

    `run --card` drives a subtask *of a story*: `Store.record_subtask` and
    `Store.record_phase` are both keyed by a story id, and inventing one would
    put rows in the projection that `status` and `resume` could never join back
    to a real card.
    """


class UnknownCardError(CliError):
    """The run's tree holds no subtask with that card id.

    Its own type rather than `UnknownRunError`'s: the run was found and the card
    was not, so the fix an operator needs is `agent-manager status <run-id>` --
    a different instruction from the one a missing run gets -- and a script can
    tell the two apart by the `type` field of the envelope.
    """


class UnknownPhaseError(CliError):
    """The card has no phase of that name in this run.

    Separate from `UnknownAttemptError` because the two refusals point at
    different lists: this one can name the phases that exist, and conflating them
    would cost an operator that list at exactly the moment they mistyped a name.
    """


class UnknownAttemptError(CliError):
    """The selected phase has no such attempt -- or has none at all.

    One type for both, because they are one fact: the command was asked for an
    attempt and there is none to report. The message is what tells apart "you
    asked for attempt 7 of three" from "nothing has run for this card yet", the
    same way `UnknownRunError` carries two readings of one refusal.
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


def ok_envelope(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def error_envelope(error: BaseException) -> dict[str, Any]:
    """brd's failure envelope. The type is the exception's own class name, so an
    operator can grep the source for the thing that refused."""
    return {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}}


def render(envelope: Mapping[str, Any], *, pretty: bool = False) -> str:
    """The envelope as text: one line by default, indented under `--pretty`.

    `default=str` is not decoration: the payload carries a `Path`, and a
    renderer that raised `TypeError` on it would turn a finished run into a
    traceback with no envelope at all. `sort_keys` makes the output diffable.
    """
    if pretty:
        return json.dumps(envelope, indent=2, sort_keys=True, default=str)
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True, default=str)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


RUN_IDENTITY = (
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
)
"""The run's own fields, without `config` and without the tree below it. §10's
`status` header and `runs`' entries are the same seven names, so the two
commands describe a run the same way."""


def status_rows(run: models.Run) -> list[dict[str, Any]]:
    """§10's table as flat rows: one per attempt, in §9 tree order.

    Pure over the tree `load_run` already assembled -- no database, no clock --
    so the command stays a composition. A phase with no attempts gets a row of
    its own with `attempt: None`, because a `pending` or `started` phase is
    precisely what an operator runs `status` to see, and an attempt-keyed table
    would have nowhere to put it.

    `state` is the attempt's status on an attempt row and the phase's status on a
    phase row: both are the state of the thing the row is about.
    """
    rows: list[dict[str, Any]] = []
    for story in run.stories:
        for subtask in story.subtasks:
            for phase in subtask.phases:
                if not phase.attempts:
                    rows.append(
                        {
                            "story": story.card_id,
                            "subtask": subtask.card_id,
                            "phase": phase.name,
                            "attempt": None,
                            "state": phase.status,
                        }
                    )
                    continue
                for attempt in phase.attempts:
                    rows.append(
                        {
                            "story": story.card_id,
                            "subtask": subtask.card_id,
                            "phase": phase.name,
                            "attempt": attempt.n,
                            "state": attempt.status,
                        }
                    )
    return rows


def status_payload(run: models.Run) -> dict[str, Any]:
    """The run's identity, the §9 tree, and the flat table over it.

    `model_dump()` rather than `model_dump(mode="json")`: the payload keeps its
    `Path` and `datetime` objects and `render`'s `default=str` stringifies them
    once, at the edge, the same way `run_card`'s `worktree` is handled. Field
    names are `models.py`'s and are not renamed for display.
    """
    tree = run.model_dump()
    return {
        "run": {field: tree[field] for field in RUN_IDENTITY},
        "stories": tree["stories"],
        "rows": status_rows(run),
    }


def find_subtask(
    run: models.Run, card: str
) -> tuple[models.StoryRun, models.SubtaskRun] | None:
    """The `(story, subtask)` pair for one card id, or `None`.

    Pure over the tree `load_run` assembled, like `status_rows`. The owning story
    comes back with the match because `SubtaskRun` carries no back-reference to
    it and the `logs` payload's `story_id` has nowhere else to come from; a
    second walk to recover it would be a second source of truth for one match.

    The first match in §9 tree order wins. A card id appears once per run in
    everything this program writes, so a duplicate is a corrupt projection, and
    answering deterministically beats answering arbitrarily.
    """
    for story in run.stories:
        for subtask in story.subtasks:
            if subtask.card_id == card:
                return story, subtask
    return None


def select_attempt(
    subtask: models.SubtaskRun,
    *,
    phase: str | None = None,
    attempt: int | None = None,
) -> tuple[models.PhaseRun, models.Attempt]:
    """The `(phase, attempt)` §10's `logs` should report, or a refusal.

    Pure over the tree, no filesystem: which attempt is meant is a question about
    recorded state, and answering it before any file is opened is what keeps the
    artifact reading a single straight-line step.

    With no `phase`, the last phase in position order that actually has attempts
    wins -- `load_run` preserves position, and a trailing `pending` or
    deterministic phase has no artifacts to print. With no `attempt`, the highest
    `n` wins; `max` rather than `attempts[-1]` because the ordering is
    `load_run`'s promise, not the model's, and this function is also called with
    trees built by hand.
    """
    if phase is None:
        chosen = next((item for item in reversed(subtask.phases) if item.attempts), None)
        if chosen is None:
            raise UnknownAttemptError(
                f"no attempt has been recorded for card {subtask.card_id!r} yet"
                " (`agent-manager status` shows which phases exist)"
            )
    else:
        chosen = next((item for item in subtask.phases if item.name == phase), None)
        if chosen is None:
            names = ", ".join(item.name for item in subtask.phases) or "none"
            raise UnknownPhaseError(
                f"card {subtask.card_id!r} has no phase {phase!r};"
                f" recorded phases: {names}"
            )

    if attempt is None:
        if not chosen.attempts:
            raise UnknownAttemptError(
                f"phase {chosen.name!r} of card {subtask.card_id!r} has no recorded"
                " attempt yet"
            )
        return chosen, max(chosen.attempts, key=lambda item: item.n)

    for candidate in chosen.attempts:
        if candidate.n == attempt:
            return chosen, candidate
    numbers = ", ".join(str(item.n) for item in chosen.attempts) or "none"
    raise UnknownAttemptError(
        f"phase {chosen.name!r} of card {subtask.card_id!r} has no attempt {attempt};"
        f" recorded attempts: {numbers}"
    )


def read_artifact(path: Path | None) -> dict[str, Any]:
    """One artifact as `{path, present, text}`, never a raised exception.

    `logs` exists to show an operator what the harness produced -- including the
    half-written or malformed file that made a phase fail -- so a missing path, a
    missing file and undecodable bytes are all facts to report, not refusals.
    `is_file()` rather than `exists()`: a recorded path that somehow names a
    directory must read as absent instead of raising `IsADirectoryError` out of
    a read-only command. `errors="replace"` for the same reason. The `OSError`
    arm closes the same hole for the file that `is_file()` accepts and the read
    then refuses -- an unreadable mode, a dead symlink target, a file deleted
    between the two calls: none of those are in `HANDLED`, so any of them would
    otherwise leave the operator a traceback instead of an envelope.

    `path` stays a `Path`; `render`'s `default=str` stringifies it once at the
    edge, exactly as `status_payload` leaves `worktree_path` alone.
    """
    absent = {"path": path, "present": False, "text": None}
    if path is None or not Path(path).is_file():
        return absent
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return absent
    return {"path": path, "present": True, "text": text}


def logs_payload(
    run: models.Run,
    story: models.StoryRun,
    subtask: models.SubtaskRun,
    phase: models.PhaseRun,
    attempt: models.Attempt,
) -> dict[str, Any]:
    """§10's `logs` output: what was selected, and the three artifacts of it.

    The locations come from the `Attempt` row the projection already holds, never
    from `paths.attempt_dir` -- that helper creates the directory it names, and
    a read-only command that minted an artifact directory for a run nobody
    started would be writing state outside `Store`.

    `result.json` is read as text like the other two and is deliberately not
    parsed: the unparseable result is precisely the one an operator runs `logs`
    to look at.
    """
    return {
        "run_id": run.id,
        "story_id": story.card_id,
        "card": subtask.card_id,
        "phase": phase.name,
        "attempt": attempt.n,
        "status": attempt.status,
        "exit_code": attempt.exit_code,
        "artifacts": {
            "prompt": read_artifact(attempt.prompt_path),
            "result": read_artifact(attempt.result_path),
            "stdout": read_artifact(attempt.stdout_path),
        },
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


def checkpoint_resume_phase(
    checkpoint: store_module.Checkpoint | None, *, card_id: str, run_id: str
) -> str:
    """The phase `resume` continues `card_id` at, or a refusal.

    Pure over the row `Store.latest_checkpoint` returned, so every refusal is
    testable without a store, and `resume_run` calls it before its first
    write. In order: no row (a run that died before its first turn, or one
    that predates checkpoints); a newest row `done` (only the final status
    write was lost); a
    digest other than `TASK.digest()`; a row holding no turn, which is what a
    phase escalation leaves (`runtime_engine.pending_phase`).
    """
    if checkpoint is None:
        raise NotResumableError(
            f"card {card_id} in run {run_id!r} has no checkpoint to resume from:"
            " the run died before its first turn, or it predates checkpoints;"
            " start a fresh run with `agent-manager run --card`"
        )
    if checkpoint.reason == "done":
        raise NotResumableError(
            f"the newest checkpoint of card {card_id} in run {run_id!r} is 'done',"
            " so there is no turn to continue -- only the final status write was"
            " lost; start a fresh run with `agent-manager run --card` if the card"
            " still needs work"
        )
    digest = task_workflow.TASK.digest()
    if checkpoint.digest != digest:
        raise CheckpointMismatchError(
            f"workflow changed since checkpoint: checkpoint #{checkpoint.seq} of card"
            f" {card_id} in run {run_id!r} was saved under digest {checkpoint.digest},"
            f" but workflow {task_workflow.TASK.name!r} now has digest {digest};"
            " start a fresh run with `agent-manager run --card`"
        )
    phase = runtime_engine.pending_phase(checkpoint)
    if phase is None:
        raise NotResumableError(
            f"the newest checkpoint of card {card_id} in run {run_id!r} is"
            f" {checkpoint.reason!r} with no turn left to run: a phase escalated and"
            " ended the walk; start a fresh run with `agent-manager run --card`"
        )
    return phase


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


app = typer.Typer(
    add_completion=False,
    help="Drive brd cards through the agent-manager workflow engine.",
)


@app.callback()
def main() -> None:
    """agent-manager: run one subtask card end to end.

    The callback exists so `run` stays a named subcommand: a Typer app with one
    command and no callback collapses into a bare command, and §10's grammar is
    `agent-manager run ...`.
    """


WORKFLOW_NAME = "task"
"""The only document `run --card` drives. `--workflow` is §10's, not this card's."""

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


def default_runner_factory(
    *,
    store: Store,
    run_id: str,
    story_id: str,
    card_id: str,
) -> AgentPhaseRunner:
    """The production runner: real adapters, real roles, the direct launcher.

    `adapters` and `result_models` keep `AgentRunner`'s own defaults and
    `harness_map` stays empty, so every role falls back to `DEFAULT_HARNESS` and
    to the model its own `policy.toml` names (D6). Choosing a harness per role is
    `--harness`'s job, and `--harness` is not this card's.
    """
    return dispatch.AgentRunner(
        store=store,
        launcher=run_direct,
        run_id=run_id,
        story_id=story_id,
        card_id=card_id,
    )


def gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]:
    """The gate parameters `builtin/task.yaml` binds and `subtask_context` lacks.

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


@dataclass(frozen=True)
class SubtaskDrive:
    """What one `drive_subtask` call did: the engine's summary, plus every warning.

    `warnings` is the summary's own list followed by the runner's out-of-band
    list. Internal state, so a dataclass rather than a pydantic model.
    """

    summary: SubtaskSummary
    warnings: list[str]


def drive_subtask(
    *,
    store: Store,
    run_id: str,
    card: models.Card,
    parent: models.Card,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
    should_stop: Callable[[], bool] | None = None,
    resume_from: store_module.Checkpoint | None = None,
) -> SubtaskDrive:
    """Walk one subtask through `workflow.task.TASK` under a store the caller owns.

    Addendum O4's shared driver. `run_card` calls it once, and a milestone runner
    calls it once per subtask against one store and one run id. The caller owns
    everything around the walk: the board reads, the run id, opening and
    closing the store, and the run/story/subtask rows. This function catches
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    `should_stop` goes straight to the engine; a stop is
    `summary.status == "stopped"`.

    The walk is `runtime.engine.run_subtask` over `TASK`. `resume_from` (card
    02890d5d) continues it from a saved checkpoint; it joins the walk's
    keywords only when given, so a fresh walk is called exactly as before.
    """
    factory = default_runner_factory if runner_factory is None else runner_factory
    runner = factory(
        store=store,
        run_id=run_id,
        story_id=parent.id,
        card_id=card.id,
    )
    walk: dict[str, Any] = {
        "story_id": parent.id,
        "subtask": subtask,
        "repo_dir": repo_dir,
        "commands": commands,
        "card": card,
        "parent_story": parent,
        "extra_context": gate_context(commands, allow_no_verification),
        "agent_runner": runner,
        "should_stop": should_stop,
    }
    if resume_from is not None:
        walk["resume_from"] = resume_from
    summary = runtime_engine.run_subtask(task_workflow.TASK, store, **walk)
    # `AgentRunner` collects gate warnings out of band (dispatch.py:375):
    # its signature returns a result, so a warning has nowhere else to go,
    # and dropping them is the §12 failure this whole list exists to prevent.
    warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
    return SubtaskDrive(summary=summary, warnings=warnings)


def run_card(
    card_id: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str = "master",
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Drive one subtask card through `workflow.task.TASK` once, and report.

    The order is the spec's and it is load-bearing: the board reads happen before
    a run id exists (so a bad card leaves no run directory), and the run, story
    and subtask rows are written before the walk starts (so `status` and `resume`
    can see a run that died on its first phase).
    """
    root = resolve_repo_dir(repo_dir)
    card = board.show(card_id, repo_dir=root)
    if not card.parent_id:
        raise ParentlessCardError(
            f"card {card.id} ({card.title!r}) has no parent card; `run --card` drives "
            "a subtask of a story, and the story is what every run record is keyed by"
        )
    parent = board.show(card.parent_id, repo_dir=root)

    branch = dag.task_branch(branch_prefix, card)
    worktree = worktree_for(root, branch)
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)

    store = Store.open(root, run_id)
    try:
        run_record = models.Run(
            id=run_id,
            workflow=WORKFLOW_NAME,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(),
        )
        story = models.StoryRun(
            card_id=parent.id,
            title=parent.title,
            level=0,
            status="started",
            tip_branch=branch,
        )
        subtask = models.SubtaskRun(
            card_id=card.id,
            branch=branch,
            base_branch=base_branch,
            status="started",
            worktree_path=worktree,
        )
        store.record_run(run_record)
        store.record_story(story)
        store.record_subtask(story.card_id, subtask)

        drive = drive_subtask(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
        )
        summary = drive.summary

        store.record_run(run_record.model_copy(update={"status": summary.status}))
        store.record_story(story.model_copy(update={"status": summary.status}))
        store.record_subtask(
            story.card_id, subtask.model_copy(update={"status": summary.status})
        )

        warnings = drive.warnings
        return {
            "run_id": run_id,
            "card_id": card.id,
            "story_id": parent.id,
            "branch": branch,
            "base_branch": base_branch,
            "worktree": str(worktree),
            "status": summary.status,
            "failed_phase": summary.failed_phase,
            "detail": summary.detail,
            "skipped": list(summary.skipped),
            "warnings": warnings,
        }
    finally:
        store.close()


DEFAULT_MAX_CONCURRENT = 4
"""How many of a level's stories a milestone run drives at once when
`--max-concurrent` is not given (main spec section 11, addendum P1). It matches
`models.RunConfig.max_concurrent_stories`'s default."""


def already_done_entries(stories: Sequence[census.StoryPlan]) -> list[dict[str, str]]:
    """Everything in the census that never enters a dispatch level, in census order.

    A story that is closed, or that has no remaining subtasks, is one
    `kind: "story"` entry, and its subtasks are not listed on their own: the
    story is the unit that is skipped. A done subtask of a story that is still
    pending is a `kind: "subtask"` entry naming its story, because that story
    shows up in a level without it.
    """
    entries: list[dict[str, str]] = []
    for story in stories:
        if dag.is_story_closed(story) or not dag.remaining_subtasks(story):
            entries.append({"kind": "story", "id": story.id, "title": story.title})
            continue
        for subtask in story.subtasks:
            if dag.is_subtask_done(subtask):
                entries.append(
                    {
                        "kind": "subtask",
                        "id": subtask.id,
                        "title": subtask.title,
                        "story": story.id,
                    }
                )
    return entries


def dry_run_payload(
    stories: Sequence[census.StoryPlan],
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
    """O3's preview: dispatch levels with each subtask's branch and base, then Integrate.

    Pure over the census, and every derivation belongs to `dag`. The cycle
    check runs first because a cycle is what breaks the geometry, and
    `story_root`'s own guard misses a cycle between two populated stories.
    `stories_by_id` covers every story, closed ones included, so a story
    blocked by a done story still roots on that story's tip. A story's
    `subtasks` lists only what would be dispatched, but each `base` comes
    from `stack_bases` over the full ordered list, so a done first subtask
    still anchors the second. `max_concurrent` is echoed at the top, and each
    level row says how many of its stories would run together:
    `min(len(level), max_concurrent)`. The caller refuses a bound below 1.

    `integrate` is the terminal phase's plan (Integrate addendum I6): the
    branch every tip is merged into, its worktree under `repo_dir`, and the
    merge order `integration.merge_order` gives -- every story with subtasks,
    done or not. `repo_dir` is only joined onto, never read.
    """
    # `integration` imports this module at load time, so importing it at the
    # top of this module would be circular. By call time both are loaded.
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
            story_rows.append(
                {
                    "story": story.id,
                    "title": story.title,
                    "root": dag.story_root(
                        story, stories_by_id, branch_prefix, base_branch
                    ),
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
            )
        level_rows.append(
            {
                "level": index,
                "concurrent": min(len(level), max_concurrent),
                "stories": story_rows,
            }
        )
    integrate_branch = integration.integration_branch(branch_prefix)
    return {
        "max_concurrent": max_concurrent,
        "levels": level_rows,
        "already_done": already_done_entries(stories),
        "integrate": {
            "branch": integrate_branch,
            "worktree": str(worktree_for(repo_dir, integrate_branch)),
            "order": [
                {"story": story.id, "tip": tip}
                for story, tip in integration.merge_order(
                    stories, branch_prefix, base_branch
                )
            ],
        },
    }


def dry_run_milestone(
    needle: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
    """O3's order: repo dir, roots, milestone, tree, census, then the payload.

    Read-only by construction. The two `brd` reads are its only I/O. No
    `Store` is opened (that would mint a run directory), no runner is built,
    and nothing is fetched, pruned, branched, merged or written to the board.
    The Integrate plan is derived, never run. Every refusal is a type already in `HANDLED`.
    """
    root = resolve_repo_dir(repo_dir)
    milestone = census.find_milestone(board.roots(repo_dir=root), needle)
    plan = census.flatten_milestone(board.tree(milestone.id, repo_dir=root))
    return dry_run_payload(
        plan.stories,
        repo_dir=root,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        max_concurrent=max_concurrent,
    )


HANDLED: tuple[type[BaseException], ...] = (
    CliError,
    board.BoardError,
    EngineError,
    ValueError,
)
"""Everything the command turns into an `ok: false` envelope and exit 3.

`ValueError` is in the list for one concrete reason: `dag.short_id` raises a
bare one for a card id that is not a UUID, and a typed `--card` must not come
back as a traceback. Anything outside this tuple is a bug in this program and
should crash loudly with its stack intact.
"""


def _check_run_targets(
    *,
    card: str | None,
    milestone: str | None,
    dry_run: bool,
    max_concurrent: int | None = None,
) -> None:
    """Refuse a bad `--card` / `--milestone` / `--dry-run` / `--max-concurrent` combination as a usage error.

    `typer.BadParameter` is Typer's own exit 2, which `EXIT_ERROR`'s docstring
    reserves. It is raised before the `HANDLED` try block, so nothing is read
    or dispatched. A blank `--milestone` is refused here too: the census strips
    the needle, and an empty needle is a substring of every title, so on a
    one-milestone board it would silently pick that milestone.
    `--max-concurrent` is `None` when not given, so giving it with `--card` is
    refused whatever its value, the default included. The Option has no
    `min=1`, so a value below 1 is refused here, worded and routed like every
    other run-target refusal.
    """
    if card is not None and milestone is not None:
        raise typer.BadParameter(
            "give --card or --milestone, not both",
            param_hint="'--card' / '--milestone'",
        )
    if card is None and milestone is None:
        raise typer.BadParameter(
            "one of --card or --milestone is required",
            param_hint="'--card' / '--milestone'",
        )
    if milestone is not None and not milestone.strip():
        raise typer.BadParameter(
            "--milestone needs a card id or a title substring, not a blank string",
            param_hint="'--milestone'",
        )
    if dry_run and card is not None:
        raise typer.BadParameter(
            "--dry-run previews a milestone and does not apply to --card",
            param_hint="'--dry-run'",
        )
    if max_concurrent is not None and max_concurrent < 1:
        raise typer.BadParameter(
            f"--max-concurrent must be at least 1, got {max_concurrent}",
            param_hint="'--max-concurrent'",
        )
    if card is not None and max_concurrent is not None:
        raise typer.BadParameter(
            "--max-concurrent applies only to --milestone",
            param_hint="'--max-concurrent'",
        )


@app.command("run")
def run(
    card: str | None = typer.Option(
        None, "--card", help="The subtask card id to drive. Exclusive with --milestone."
    ),
    milestone: str | None = typer.Option(
        None,
        "--milestone",
        help=(
            "A milestone card id or title substring: drive every remaining subtask. "
            "Exclusive with --card."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="With --milestone: print the levels and stack bases, and write nothing.",
    ),
    max_concurrent: int | None = typer.Option(
        None,
        "--max-concurrent",
        help=(
            "With --milestone: how many of a level's stories run at once "
            f"(default {DEFAULT_MAX_CONCURRENT}). 1 runs them one at a time."
        ),
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    base_branch: str = typer.Option(
        "master", "--base-branch", help="The branch this subtask's branch is cut from."
    ),
    branch_prefix: str = typer.Option(
        ...,
        "--branch-prefix",
        help="Milestone prefix for the derived branch name, e.g. `m2`.",
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help="Proceed even when no verification suite is available (§12's opt-out).",
    ),
    verify: list[str] = typer.Option(
        [],
        "--verify",
        help=(
            "One whole verification command, repeatable. Passed through verbatim "
            "and in the order given; the engine runs them in sequence."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Drive one subtask card or a whole milestone end to end, or preview a milestone with --dry-run."""
    _check_run_targets(
        card=card,
        milestone=milestone,
        dry_run=dry_run,
        max_concurrent=max_concurrent,
    )
    lanes = DEFAULT_MAX_CONCURRENT if max_concurrent is None else max_concurrent
    try:
        if milestone is not None and dry_run:
            payload = dry_run_milestone(
                milestone,
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
                max_concurrent=lanes,
            )
        elif milestone is not None:
            # `orchestrate` imports this module at load time and reads its names
            # at call time, so importing it at the top of this module would be
            # circular. By the time a command runs, both are fully loaded. Read
            # as `orchestrate.run_milestone` so a test can patch it there. No
            # runner_factory and no driver: production gets
            # `default_runner_factory` and `drive_subtask`.
            from agent_manager import orchestrate

            payload = orchestrate.run_milestone(
                milestone,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
            )
        else:
            payload = run_card(
                card,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                allow_no_verification=allow_no_verification,
                commands=list(verify),
            )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    # A card payload reports `status`. A milestone payload has no `status` key:
    # it carries `escalated: true` only when it stopped, a clean one carries
    # `done: true`, and a dry-run preview carries neither. So the flag is read
    # with `.get`, never indexed. Both checks are strict equality on purpose: a
    # `stopped` card (addendum P4) is not an escalation and exits 0.
    if milestone is None:
        escalated = payload["status"] == "escalated"
    else:
        escalated = payload.get("escalated") is True
    if escalated:
        raise typer.Exit(EXIT_ESCALATED)


def status_for(run_id: str | None, *, repo_dir: Path) -> dict[str, Any]:
    """The §9 tree and §10 table of one run of this project.

    Read-only: no `record_*` is called, and the connection is closed on every
    path including the refusals, the way `run_card` closes its store. The default
    run id comes from `store_module.latest_run_id`, which is the head of the very
    listing `runs` prints, so the two commands cannot disagree about which run is
    the most recent one.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        wanted = run_id
        if wanted is None:
            wanted = store_module.latest_run_id(conn)
            if wanted is None:
                raise UnknownRunError(
                    f"no run has been recorded for {root}, so there is no most recent"
                    " run to report on; pass a run id or start one with `run --card`"
                )
        run = store_module.load_run(conn, wanted)
        if run is None:
            raise UnknownRunError(
                f"run {wanted!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        return status_payload(run)
    finally:
        conn.close()


@app.command("status")
def status(
    run_id: str | None = typer.Argument(
        None, metavar="[RUN_ID]", help="The run to report on. Defaults to the most recent."
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Report one run as story / subtask / phase / attempt / state."""
    try:
        payload = status_for(run_id, repo_dir=repo_dir)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))


def runs_for(*, repo_dir: Path) -> dict[str, Any]:
    """This project's run history, newest first.

    An empty history is an empty list, not a refusal: a project that has never
    been run is a fact. `model_dump()` keeps the `Path` and `datetime` objects
    for `render`'s `default=str`, exactly as `status_payload` does, so a run
    looks the same in both commands.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        return {"runs": [summary.model_dump() for summary in store_module.list_runs(conn)]}
    finally:
        conn.close()


@app.command("runs")
def runs(
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """List this project's run history, newest first."""
    try:
        payload = runs_for(repo_dir=repo_dir)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))


def logs_for(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
) -> dict[str, Any]:
    """§10's `logs`: one attempt of one card of one run, with its artifacts.

    Read-only, like `status_for`: the projection is reached through the free
    `open_db` / `load_run` rather than `Store.open`, which would construct a
    `Journal` and therefore mint a run directory for a run that may not exist.
    The connection is closed on every path including the refusals.

    `run_id` is required -- §10 writes `logs <run-id> <card>` and there is no
    "most recent run" reading of it to default to.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        found = find_subtask(run, card)
        if found is None:
            raise UnknownCardError(
                f"card {card!r} is not in run {run_id!r}"
                f" (`agent-manager status {run_id}` lists the cards that are)"
            )
        story, subtask = found
        chosen_phase, chosen_attempt = select_attempt(
            subtask, phase=phase, attempt=attempt
        )
        return logs_payload(run, story, subtask, chosen_phase, chosen_attempt)
    finally:
        conn.close()


@app.command("logs")
def logs(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run to read."),
    card: str = typer.Argument(..., metavar="CARD", help="The subtask card id."),
    phase: str | None = typer.Option(
        None, "--phase", help="Which phase. Defaults to the last one with attempts."
    ),
    attempt: int | None = typer.Option(
        None, "--attempt", help="Which attempt. Defaults to the highest recorded."
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print one attempt's prompt, result and captured stdout."""
    try:
        payload = logs_for(
            run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))


def _resume_from_checkpoint(
    run: models.Run,
    *,
    root: Path,
    allow_no_verification: bool,
    commands: Sequence[str],
    runner_factory: RunnerFactory | None,
) -> dict[str, Any]:
    """Continue the run's one in-flight subtask from its newest checkpoint.

    Every refusal that needs no store -- nothing in flight, a card the board
    lost -- comes before `Store.open`. The checkpoint can only be read through
    the store, so its refusals (`checkpoint_resume_phase`) come right after it
    is opened and before the first write. Then the orphan attempts are marked
    `harness_error`, the run, story and subtask are recorded `started`, and
    `drive_subtask` walks `TASK` from the checkpoint, whose queue says where the
    walk goes on. A milestone run records `workflow="milestone"`, which names no
    workflow; every subtask is walked through `TASK` either way (card 02890d5d).
    """
    story, subtask = select_resumable(run)
    card = board.show(subtask.card_id, repo_dir=root)
    parent = board.show(story.card_id, repo_dir=root)
    orphans = orphan_attempts(subtask)
    resumed = subtask.model_copy(update={"status": "started"})

    store = Store.open(root, run.id)
    try:
        checkpoint = store.latest_checkpoint(subtask.card_id)
        phase = checkpoint_resume_phase(checkpoint, card_id=subtask.card_id, run_id=run.id)
        for orphan, attempt in orphans:
            store.record_attempt(
                story.card_id,
                subtask.card_id,
                orphan.name,
                attempt.model_copy(update={"status": "harness_error"}),
            )
        store.record_run(run.model_copy(update={"status": "started"}))
        store.record_story(story.model_copy(update={"status": "started"}))
        store.record_subtask(story.card_id, resumed)

        drive = drive_subtask(
            store=store,
            run_id=run.id,
            card=card,
            parent=parent,
            subtask=resumed,
            repo_dir=root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            resume_from=checkpoint,
        )
        summary = drive.summary

        store.record_run(run.model_copy(update={"status": summary.status}))
        store.record_story(story.model_copy(update={"status": summary.status}))
        store.record_subtask(
            story.card_id, resumed.model_copy(update={"status": summary.status})
        )

        return {
            "run_id": run.id,
            "card_id": subtask.card_id,
            "story_id": story.card_id,
            "branch": subtask.branch,
            "base_branch": subtask.base_branch,
            "worktree": None
            if subtask.worktree_path is None
            else str(subtask.worktree_path),
            "status": summary.status,
            "failed_phase": summary.failed_phase,
            "detail": summary.detail,
            "skipped": list(summary.skipped),
            "warnings": drive.warnings,
            "resumed_from": phase,
            "discarded_attempts": [
                {"phase": orphan.name, "n": attempt.n} for orphan, attempt in orphans
            ],
        }
    finally:
        store.close()


def resume_run(
    run_id: str,
    *,
    repo_dir: Path,
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
) -> dict[str, Any]:
    """Pick one stopped or killed subtask back up from its checkpoint (§9, card 02890d5d).

    The order is load-bearing in the same way `run_card`'s is, only inverted:
    every refusal -- unknown run, nothing in flight, a card the board lost --
    happens before `Store.open`, because `Store.open` constructs a `Journal`
    and therefore mints a run directory, and a refusal that left one behind
    would be this command writing state for a run it declined to touch.

    Branch, base branch and worktree come from the recorded `SubtaskRun` and
    never from a flag: §9's "the run records what it was started with" is the
    reason the record exists. The two knobs the record does *not* carry --
    `models.RunConfig` has no suite commands and no `allow_no_verification` --
    are taken as arguments here rather than grown onto the model.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
    finally:
        conn.close()
    return _resume_from_checkpoint(
        run,
        root=root,
        allow_no_verification=allow_no_verification,
        commands=commands,
        runner_factory=runner_factory,
    )

@app.command("resume")
def resume(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run to pick back up."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help="Proceed even when no verification suite is available (§12's opt-out).",
    ),
    verify: list[str] = typer.Option(
        [],
        "--verify",
        help=(
            "One whole verification command, repeatable. The run record does not "
            "carry the suite, so a resume is told it the way a fresh run was."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Continue a stopped or killed subtask from its checkpoint, and drive it to the end.

    No `--base-branch` and no `--branch-prefix`: both were decided when the run
    started and are recorded on the subtask (§9). `--allow-no-verification` and
    `--verify` are offered because `models.RunConfig` carries neither the opt-out
    nor the suite commands. The checkpoint carries the gate context the run
    started with.
    """
    try:
        payload = resume_run(
            run_id,
            repo_dir=repo_dir,
            allow_no_verification=allow_no_verification,
            commands=list(verify),
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    # Strict equality on purpose: a `stopped` walk (addendum P4) is not an
    # escalation, so it exits 0 with an ok envelope.
    if payload["status"] == "escalated":
        raise typer.Exit(EXIT_ESCALATED)
