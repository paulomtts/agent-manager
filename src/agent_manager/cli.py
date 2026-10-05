"""The Typer app (design §4 line 128, §10 lines 388-411).

This module composes and renders; it decides nothing a collaborator already
decides. Branch names come from `dag`, board reads from `board`, artifact paths
from `paths` via `store`, the phase walk from `runtime.engine`, and the dispatch from
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

import asyncio
import json
import os
import socket
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agent_manager import (
    board,
    census,
    comments,
    control,
    dag,
    detach,
    dispatch,
    locks,
    models,
    orchestrate,
    paths,
    prompt,
    store as store_module,
)
from agent_manager import __version__
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.stop import StopSignal
from agent_manager.runtime.walk import AgentPhaseRunner, SubtaskSummary
from agent_manager.harness.launcher import run_direct
from agent_manager.runtime import engine as runtime_engine
from agent_manager.steps import verify as verify_step
from agent_manager.store import Store
from agent_manager.workflow import task as task_workflow

# The run helpers S1 moved to `runs` (card 61a0d9be finished the move: bases,
# integration and orchestrate read them off `runs`). This module uses some by
# their bare names; the rest stay importable as `cli.X` for the tests and e2e
# drivers that still read them here. `cli.X is runs.X` for every name.
from agent_manager.runs import (
    RUN_ID_TIME_FORMAT,
    WORKTREE_PARTS,
    CheckpointMismatchError,
    CliError,
    DryRunPlan,
    NotResumableError,
    RepoDirError,
    RunnerFactory,
    UnknownRunError,
    compute_dry_run_plan,
    continuable_checkpoint,
    gate_context,
    mint_run_id,
    orphan_attempts,
    resolve_repo_dir,
    select_resumable,
    worktree_for,
)

EXIT_ESCALATED = 1
"""The subtask escalated. §12: a full stop a human has to read."""

EXIT_ERROR = 3
"""The tool could not run the subtask at all. `2` belongs to Typer's usage errors."""


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


class NotRunningError(CliError):
    """`am pause`/`am cancel` was asked to steer a run that is not `started` (C8).

    The message names the recorded status and what to run instead, because
    a stopped run wants `am resume` and a finished one wants nothing.
    """


class DeadRunError(CliError):
    """The run is recorded `started`, but no live process holds its lease (C2, C8).

    Nobody is left to honour a request, so none is recorded. The message
    names the lease's pid, host and heartbeat age, or says there is no lease,
    and points at `am resume` and `am reset`.
    """


class NotAcceptingError(CliError):
    """The run's control window has closed: it is finishing (C3, C8)."""


class RunIsLiveError(CliError):
    """`am resume` was asked for a run another live process still holds (C10)."""


class NotResettableError(CliError):
    """`am reset` was asked to close a run that finished `done` (am-reset §3.4).

    Only the CLI raises it, so it lives beside `RunIsLiveError` and
    `DeadRunError` rather than in `runs` with `NotResumableError`.
    """


class ClaimedError(CliError):
    """A card or branch this run needs is claimed by another run's live lease (X5, X11).

    `key` is the claim key (`card:<id>` or `branch:<name>`) and `run_id` the
    run that holds it, so a script can act on the refusal without parsing
    the message.
    """

    def __init__(self, message: str, *, key: str, run_id: str) -> None:
        super().__init__(message)
        self.key = key
        self.run_id = run_id


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
`status` header is these seven names. Each `runs` entry carries the same seven,
plus `milestone_id`, `card_id`, `lease` and `progress` (a superset), so the two
commands still describe a run's identity the same way."""


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


def _lease_fields(lease: store_module.LeaseRow, *, now: datetime) -> dict[str, Any]:
    """The lease fields `am status` and `am runs` both show.

    One function so the two commands cannot drift: `control_view` adds
    `acquired_at` on top, `runs_for` shows these five as they are. `live` is
    `control.lease_is_live` at `now`, worked out at read time and never
    stored; `heartbeat_at` is an ISO string.
    """
    return {
        "pid": lease.pid,
        "host": lease.host,
        "heartbeat_at": lease.heartbeat_at.isoformat(),
        "accepting": lease.accepting,
        "live": control.lease_is_live(lease, now=now),
    }


def control_view(
    lease: store_module.LeaseRow | None,
    requests: Sequence[store_module.ControlRow],
    *,
    now: datetime,
    claims: Sequence[str] = (),
) -> dict[str, Any]:
    """C12's `control` key: the lease or `None`, every life's requests in seq
    order, and `claims`, the keys the live lease holds (X5; empty otherwise).

    `live` is worked out here, at read time, by `control.lease_is_live`; it
    is never stored. Timestamps are ISO strings.
    """
    return {
        "lease": None
        if lease is None
        else {
            **_lease_fields(lease, now=now),
            "acquired_at": lease.acquired_at.isoformat(),
        },
        "requests": [
            {
                "command": row.command,
                "requested_at": row.requested_at.isoformat(),
                "handled_at": None if row.handled_at is None else row.handled_at.isoformat(),
            }
            for row in requests
        ],
        "claims": list(claims),
    }


def integrity_view(
    run_id: str,
    run: models.Run,
    lease: store_module.LeaseRow | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """The `integrity` key of `status`: does the journal agree with `run`?

    Journal/DB divergence spec §3.3, §3.7. Always the three keys `checked`,
    `reason` and `mismatches`, and never an error: a journal that cannot be
    compared is `checked: false` with the reason why, so `status` keeps its
    exit code. Report-only (§3.4): nothing is written and no control request
    is filed.

    The journal is opened through `Journal._for_reading`, never
    `Journal(run_id)`, whose `paths.run_dir` would create a directory for a
    run that has none, and a torn last line is an append in flight and is
    skipped, as in `_journal_events`. The one `try` covers `diverging` as well
    as `read`, because `replay` inside it raises `JournalError` or a pydantic
    `ValidationError` of its own. Mismatches are `store.diverging`'s, in its
    tree-walk order: there is one definition of divergence.

    A live lease (§3.5) is `checked: false, reason: "lease is live"` before the
    journal is opened: a running process's writes in flight are noise, not
    divergence, even against a hand-edited projection. A dead lease, or none,
    is checked.
    """
    if lease is not None and control.lease_is_live(lease, now=now):
        return {"checked": False, "reason": "lease is live", "mismatches": []}
    try:
        lines = store_module.Journal._for_reading(run_id).read(ignore_torn_tail=True)
        found = store_module.diverging(lines, run)
    except store_module.MissingJournalError:
        return {"checked": False, "reason": "no journal", "mismatches": []}
    except (store_module.JournalError, ValidationError) as error:
        return {
            "checked": False,
            "reason": f"journal unreadable: {error}",
            "mismatches": [],
        }
    return {
        "checked": True,
        "reason": None,
        "mismatches": [asdict(mismatch) for mismatch in found],
    }


def status_payload(
    run: models.Run, control: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The run's identity, the §9 tree, the flat table over it, and live control.

    `model_dump()` rather than `model_dump(mode="json")`: the payload keeps its
    `Path` and `datetime` objects and `render`'s `default=str` stringifies them
    once, at the edge, the same way `run_card`'s `worktree` is handled. Field
    names are `models.py`'s and are not renamed for display. `control` is
    `control_view`'s result; `None` renders as no lease and no requests, so
    the key is always present (C12).
    """
    tree = run.model_dump()
    return {
        "run": {field: tree[field] for field in RUN_IDENTITY},
        "stories": tree["stories"],
        "rows": status_rows(run),
        "control": {"lease": None, "requests": [], "claims": []}
        if control is None
        else control,
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
    """§10's `logs` output: what was selected, and the artifacts of it.

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
            # The launcher merges an agent's stderr into stdout; the key is
            # here so agent and deterministic payloads share one shape.
            "stderr": read_artifact(None),
        },
    }


def select_step_attempt(
    subtask: models.SubtaskRun,
    phase: models.PhaseRun,
    recorded: Sequence[int],
    attempt: int | None = None,
) -> int:
    """Which attempt of a deterministic phase `logs` should report, or a refusal.

    A deterministic phase has no `Attempt` rows (spec e1b1e7d5 Decision 2), so
    its attempts are the `<phase>.N` directories on disk, which the caller
    scans (`paths.recorded_attempts`) and passes in -- keeping this, like
    `select_attempt`, pure. Same defaults and the same wording as
    `select_attempt`: the highest number with no `attempt`.
    """
    if attempt is None:
        if not recorded:
            raise UnknownAttemptError(
                f"phase {phase.name!r} of card {subtask.card_id!r} has no recorded"
                " attempt yet"
            )
        return max(recorded)
    if attempt in recorded:
        return attempt
    numbers = ", ".join(str(n) for n in recorded) or "none"
    raise UnknownAttemptError(
        f"phase {phase.name!r} of card {subtask.card_id!r} has no attempt {attempt};"
        f" recorded attempts: {numbers}"
    )


def step_end_status(
    subtask: models.SubtaskRun,
    phase: models.PhaseRun,
    n: int,
    recorded: Sequence[int],
) -> str | None:
    """The `logs --follow` end status of deterministic attempt `n`, or `None`.

    A deterministic phase has no `Attempt` row, so card 4.2 maps it onto the
    `AttemptStatus` vocabulary: attempt `n` is over once a later `<phase>.M`
    directory exists, or once the phase is neither `pending` nor `started`.
    It ended `ok` only when it is the latest attempt and the phase is `done`;
    a superseded attempt, or a phase `failed`, `escalated`, `stopped` or
    `cancelled`, ended `gate_failed`. Pure: the caller scans `recorded` with
    the read-only `paths.recorded_attempts`.
    """
    if n not in recorded:
        numbers = ", ".join(str(item) for item in recorded) or "none"
        raise UnknownAttemptError(
            f"phase {phase.name!r} of card {subtask.card_id!r} has no attempt {n}"
            f" any more; recorded attempts: {numbers}"
        )
    if max(recorded) > n:
        return "gate_failed"
    if phase.status in ("pending", "started"):
        return None
    return "ok" if phase.status == "done" else "gate_failed"


def step_logs_payload(
    run: models.Run,
    story: models.StoryRun,
    subtask: models.SubtaskRun,
    phase: models.PhaseRun,
    attempt: int,
    directory: Path,
) -> dict[str, Any]:
    """`logs_payload`'s shape for a deterministic phase's on-disk attempt.

    `status` and `exit_code` are `None`: no `Attempt` row exists to carry
    them, and each command's exit label is in its log header instead. A step
    writes no prompt or result, so those two are always absent.
    """
    return {
        "run_id": run.id,
        "story_id": story.card_id,
        "card": subtask.card_id,
        "phase": phase.name,
        "attempt": attempt,
        "status": None,
        "exit_code": None,
        "artifacts": {
            "prompt": read_artifact(None),
            "result": read_artifact(None),
            "stdout": read_artifact(directory / verify_step.STDOUT_LOG),
            "stderr": read_artifact(directory / verify_step.STDERR_LOG),
        },
    }


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


@dataclass(frozen=True)
class SubtaskDrive:
    """What one `drive_subtask` call did: the engine's summary, plus every warning.

    `warnings` is the summary's own list followed by the runner's out-of-band
    list. Internal state, so a dataclass rather than a pydantic model.
    """

    summary: SubtaskSummary
    warnings: list[str]


async def drive_subtask_async(
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
    stop: StopSignal | None = None,
    resume_from: store_module.Checkpoint | None = None,
) -> SubtaskDrive:
    """Walk one subtask through `workflow.task.TASK` on the caller's event loop.

    The awaitable form of `drive_subtask` (supervisor-tree T2): a supervisor
    lane awaits it, so it must not open a loop of its own. A plain coroutine,
    not a pygents Agent. The contract is `drive_subtask`'s: the caller owns the
    store, the run id and every row around the walk, and this function catches
    nothing -- an escalation is `summary.status == "escalated"`, a stop is
    `"stopped"`, and engine errors propagate.

    `stop` (T5) is the run's `StopSignal`, handed to the engine as is; it is
    the only stop. `resume_from` joins the walk's keywords only when given, so
    a fresh walk is called exactly as before.
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
        "stop": stop,
    }
    if resume_from is not None:
        walk["resume_from"] = resume_from
    summary = await runtime_engine.run_subtask_async(task_workflow.TASK, store, **walk)
    # `AgentRunner` collects gate warnings out of band (dispatch.py:375):
    # its signature returns a result, so a warning has nowhere else to go,
    # and dropping them is the §12 failure this whole list exists to prevent.
    warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
    return SubtaskDrive(summary=summary, warnings=warnings)


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
    resume_from: store_module.Checkpoint | None = None,
) -> SubtaskDrive:
    """Walk one subtask through `workflow.task.TASK` under a store the caller owns.

    Addendum O4's shared driver. `run_card` calls it once, and a milestone runner
    calls it once per subtask against one store and one run id. The caller owns
    everything around the walk: the board reads, the run id, opening and
    closing the store, and the run/story/subtask rows. This function catches
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    It takes no stop: a caller that must stop awaits
    `drive_subtask_async(stop=...)`, where a stop is `summary.status == "stopped"`.

    One `asyncio.run` around `drive_subtask_async`, whose walk is
    `runtime.engine.run_subtask_async` over `TASK`. `resume_from` (card
    02890d5d) continues it from a saved checkpoint; it joins the walk's
    keywords only when given, so a fresh walk is called exactly as before.
    Being `asyncio.run`, it raises `RuntimeError` inside a running loop;
    callers there await `drive_subtask_async` instead.
    """
    return asyncio.run(
        drive_subtask_async(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=repo_dir,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            resume_from=resume_from,
        )
    )


def card_run_status(summary: SubtaskSummary, stop: StopSignal) -> str:
    """The run row's status after a `task` walk (live control C6, C11).

    A cancel closes the run for good whatever the walk ended as, so it wins
    even over an escalation. A pause changes nothing: the walk already parked
    as `stopped`, and an escalation it met stays `escalated`. The story and
    subtask rows always keep `summary.status`.
    """
    if stop.requested == "cancel":
        return "cancelled"
    return summary.status


def _run_is_live_error(lease: store_module.LeaseRow, now: datetime) -> RunIsLiveError:
    """C10's refusal of a run another live process holds, worded once for every caller."""
    return RunIsLiveError(
        f"run {lease.run_id} is still running in pid {lease.pid} on {lease.host}"
        f" (heartbeat {_heartbeat_age(lease, now)}s ago); wait for it to exit,"
        f" or `am status {lease.run_id}`"
    )


def _claimed_error(key: str, holder: store_module.LeaseRow, now: datetime) -> ClaimedError:
    """X11's refusal of a claimed key. The kind and name come from the key itself,
    split on its first `:`, so a `branch:` claim reads as a branch."""
    kind, _, name = key.partition(":")
    return ClaimedError(
        f"{kind} {name} is being driven by run {holder.run_id}"
        f" (pid {holder.pid} on {holder.host},"
        f" heartbeat {_heartbeat_age(holder, now)}s ago);"
        f" wait for it, or `am pause {holder.run_id}`",
        key=key,
        run_id=holder.run_id,
    )


def refuse_claimed(root: Path, keys: Sequence[str], *, run_id: str | None = None) -> None:
    """Refuse, before any write, a run whose keys another live run already claims.

    Read-only preflight (X5): one `open_db` connection, `claim_conflicts`
    judged by `control.lease_is_live` at `_utcnow()`, closed on every path.
    It takes no lease, claim or lock, so a refusal here leaves no run
    directory. `run_id` excludes that run's own rows (a resume). The first
    live conflict raises `ClaimedError`; `take_lease` re-checks atomically.
    """
    now = _utcnow()
    conn = store_module.open_db(root)
    try:
        conflicts = store_module.claim_conflicts(
            conn,
            keys,
            is_live=lambda row: control.lease_is_live(row, now=now),
            run_id=run_id,
        )
    finally:
        conn.close()
    if conflicts:
        key, holder = conflicts[0]
        raise _claimed_error(key, holder, now)


@contextmanager
def run_lease(store: Store, *, claims: Sequence[str] = ()) -> Iterator[control.Lease]:
    """Hold `control.Lease(store, claims=claims)` for the block, with CLI refusals.

    A thin wrapper: only entering is translated -- `store.LeaseHeldError`
    becomes C10's `RunIsLiveError`, `store.ClaimHeldError` becomes
    `ClaimedError` -- and the block's exit, an exception included, is
    `Lease.__exit__`'s, which releases the claims then the lease.
    """
    stack = ExitStack()
    try:
        lease = stack.enter_context(control.Lease(store, claims=claims))
    except store_module.LeaseHeldError as error:
        raise _run_is_live_error(error.holder, _utcnow()) from error
    except store_module.ClaimHeldError as error:
        raise _claimed_error(error.key, error.holder, _utcnow()) from error
    with stack:
        yield lease


def card_outcome_comment(
    *,
    run_id: str,
    card: models.Card,
    summary: SubtaskSummary,
    stop: StopSignal,
    branch: str,
    token: str,
) -> comments.Comment | None:
    """The one board comment a `run --card` walk leaves on its card, or None (card 5d9a875f).

    Chosen by `summary.status`, so a cancel that met an escalation comments
    the escalation: `done` is the done comment, `escalated` the escalation
    keyed by this life's lease `token`, and `stopped` under a cancel the
    cancelled comment with the `am run --card` relaunch. A stop under a pause
    is resumed, not closed, so it gets None. Never a story or milestone comment.
    """
    if summary.status == "done":
        return comments.compose_done(
            run_id=run_id, card_id=card.id, summary=summary, branch=branch, resumed_at=None
        )
    if summary.status == "escalated":
        failed_phase = summary.failed_phase or ""
        return comments.compose_escalated(
            run_id=run_id,
            card_id=card.id,
            token=token,
            failed_phase=failed_phase,
            detail=summary.detail,
            reason=comments.agent_reason(summary.results, failed_phase),
        )
    if stop.requested == "cancel":
        return comments.compose_cancelled(
            run_id=run_id,
            card_id=card.id,
            before_phase=summary.before_phase,
            branch=branch,
            relaunch=f"am run --card {card.id}",
        )
    return None


@dataclass(frozen=True)
class CardPreflight:
    """What `preflight_card` read and decided for one `run --card` (card 5daa944e).

    Everything the recorded stage and the engine read afterwards, built with
    no side effect: no store, no run directory, no lease. The three records
    are the `started` rows `recorded_card_run` writes. Internal state, so a
    dataclass.
    """

    root: Path
    card: models.Card
    parent: models.Card
    branch: str
    worktree: Path
    base_branch: str
    claims: list[str]
    run_id: str
    run_record: models.Run
    story: models.StoryRun
    subtask: models.SubtaskRun


def preflight_card(
    card_id: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str = "master",
    clock: Callable[[], datetime] = _utcnow,
) -> CardPreflight:
    """Stage 1 of `run --card`: every board read and refusal, then the run id (card 5daa944e).

    In today's order: the card, `ParentlessCardError`, its parent, the branch
    and worktree, then `refuse_claimed` over the `card:<id>` claim, read-only
    and before any store exists, so a refused card leaves no run directory
    (X5). Only then is the clock read and the run id minted, and the
    `started` run, story and subtask records built. Nothing is written.
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
    claims = [control.card_claim(card.id)]
    # Read-only and before `Store.open`, so a refused card leaves no run
    # directory (X5); `take_lease` in the recorded stage re-checks atomically.
    refuse_claimed(root, claims)
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)
    return CardPreflight(
        root=root,
        card=card,
        parent=parent,
        branch=branch,
        worktree=worktree,
        base_branch=base_branch,
        claims=claims,
        run_id=run_id,
        run_record=models.Run(
            id=run_id,
            workflow=WORKFLOW_NAME,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(),
        ),
        story=models.StoryRun(
            card_id=parent.id,
            title=parent.title,
            level=0,
            status="started",
            tip_branch=branch,
        ),
        subtask=models.SubtaskRun(
            card_id=card.id,
            branch=branch,
            base_branch=base_branch,
            status="started",
            worktree_path=worktree,
        ),
    )


@dataclass(frozen=True)
class RecordedRun:
    """A run past its recorded stage: its id, its open store and the lease it holds.

    What the engine of `run --card` needs that pre-flight could not give it
    (card 5daa944e). Internal state, so a dataclass.
    """

    run_id: str
    store: Store
    lease: control.Lease


@contextmanager
def recorded_card_run(pre: CardPreflight) -> Iterator[RecordedRun]:
    """Stage 2 of `run --card`: open the store, take the lease, record `started` (card 5daa944e).

    The lease and the `card:<id>` claim are taken inside the `try` that closes
    the store, so they are released before `store.close()` on every exit, an
    exception in the block included (C2, X5). They are taken before
    `record_run`, so every run write is fenced by this token; a lost race is
    `ClaimedError` (or `RunIsLiveError`) with nothing written but the empty
    run directory. The run, story and subtask rows are written before the
    block runs, so `status` and `resume` can see a run that dies on its first
    phase.
    """
    store = Store.open(pre.root, pre.run_id)
    try:
        with run_lease(store, claims=pre.claims) as lease:
            store.record_run(pre.run_record)
            store.record_story(pre.story)
            store.record_subtask(pre.story.card_id, pre.subtask)
            yield RecordedRun(run_id=pre.run_id, store=store, lease=lease)
    finally:
        store.close()


async def run_card_engine(
    pre: CardPreflight,
    recorded: RecordedRun,
    *,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Stage 3 of `run --card`: walk a recorded, leased run and report (card 5daa944e).

    The walk runs under `control.controlled` with the recorded stage's lease,
    which polls for `am pause`/`am cancel` every `control_interval` seconds
    and turns one into `stop.request` (C11). Then the outcome rows are
    recorded and, still under the lease, the card gets at most one comment
    (`card_outcome_comment`, keyed by `lease.token`); a flush's warnings join
    the payload's `warnings`. The caller owns the store and the lease.
    """
    store, lease, run_id = recorded.store, recorded.lease, recorded.run_id
    stop = StopSignal()
    # `controlled` only ever parks the walk through `stop` (C3); it
    # closes the window and runs a final sweep before returning.
    drive = await control.controlled(
        drive_subtask_async(
            store=store,
            run_id=run_id,
            card=pre.card,
            parent=pre.parent,
            subtask=pre.subtask,
            repo_dir=pre.root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            stop=stop,
        ),
        store=store,
        stop=stop,
        lease=lease,
        interval=control_interval,
    )
    summary = drive.summary
    run_status = card_run_status(summary, stop)

    store.record_run(pre.run_record.model_copy(update={"status": run_status}))
    store.record_story(pre.story.model_copy(update={"status": summary.status}))
    store.record_subtask(
        pre.story.card_id, pre.subtask.model_copy(update={"status": summary.status})
    )

    # Board-comments B2 (card 5d9a875f): after the outcome is recorded and
    # still under the lease, so the outbox write is fenced. A board
    # failure is a warning (B8); a lost lease propagates.
    comment = card_outcome_comment(
        run_id=run_id,
        card=pre.card,
        summary=summary,
        stop=stop,
        branch=pre.branch,
        token=lease.token,
    )
    if comment is not None:
        # `orchestrate` imports `cli`, so it is read here, at call time.
        from agent_manager import orchestrate

        drive.warnings.extend(orchestrate.post_comment(store, pre.root, comment, run_id=run_id))

    return {
        "run_id": run_id,
        "card_id": pre.card.id,
        "story_id": pre.parent.id,
        "branch": pre.branch,
        "base_branch": pre.base_branch,
        "worktree": str(pre.worktree),
        "status": run_status,
        "failed_phase": summary.failed_phase,
        "detail": summary.detail,
        "skipped": list(summary.skipped),
        "warnings": drive.warnings,
    }


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
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Drive one subtask card through `workflow.task.TASK` once, and report.

    Three stages (card 5daa944e), composed here: `preflight_card` (the board
    reads and every refusal, then the run id and the `started` records, with
    no side effect), `recorded_card_run` (the store opened, the lease and
    the `card:<id>` claim taken, the `started` rows written) and
    `run_card_engine` (the walk, the outcome rows and the card comment),
    which runs under one `asyncio.run`.

    The order is the spec's and it is load-bearing: the board reads happen before
    a run id exists (so a bad card leaves no run directory), and the run, story
    and subtask rows are written before the walk starts (so `status` and `resume`
    can see a run that died on its first phase).

    Live control (C11) and claims (X5): the card is refused before
    `Store.open` if another live run claims it, and from before the
    `started` rows through the final ones the run holds a `control.Lease`
    with the `card:<id>` claim (`run_lease`), and the walk runs under `control.controlled`,
    which polls for `am pause`/`am cancel` every `control_interval` seconds
    and turns one into `stop.request`. A pause parks the walk before its next
    phase (`stopped`, resumable); a cancel parks it the same way and records
    the run `cancelled` (`card_run_status`). No control cancels a running phase.
    The lease and claim are released before `store.close()` on every exit.

    Board comments (card 5d9a875f): once the rows are recorded, still under
    the lease, the card gets at most one comment (`card_outcome_comment`);
    a flush's warnings join the payload's `warnings` and nothing else changes.
    """
    pre = preflight_card(
        card_id,
        repo_dir=repo_dir,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        clock=clock,
    )
    with recorded_card_run(pre) as recorded:
        return asyncio.run(
            run_card_engine(
                pre,
                recorded,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                control_interval=control_interval,
            )
        )


DEFAULT_MAX_CONCURRENT = 4
"""How many of a level's stories a milestone run drives at once when
`--max-concurrent` is not given (main spec section 11, addendum P1). It matches
`models.RunConfig.max_concurrent_stories`'s default."""


def already_done_entries(stories: Sequence[census.StoryPlan]) -> list[dict[str, str]]:
    """Everything in the census that never enters a dispatch level, in census order.

    An out-of-play (`canceled`/`archived`) story is not listed at all. A story that is closed, or that has no remaining subtasks, is one
    `kind: "story"` entry, and its subtasks are not listed on their own: the
    story is the unit that is skipped. A done subtask of a story that is still
    pending is a `kind: "subtask"` entry naming its story, because that story
    shows up in a level without it.
    """
    entries: list[dict[str, str]] = []
    for story in stories:
        if census.is_out_of_play(story.status):
            continue  # canceled/archived: not part of the plan, so not "done" either
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
    """O3's preview envelope: `runs.compute_dry_run_plan`'s levels and Integrate plan.

    The levels, bases, roots, `merged_from` rows, per-level `concurrent` and
    the Integrate plan are all computed by `compute_dry_run_plan` (which also
    refuses a blocker cycle first). This only reads `stories` once, echoes
    `max_concurrent` (defaulting to `DEFAULT_MAX_CONCURRENT`), adds
    `already_done_entries`, and returns the envelope with its keys in the
    order `--dry-run` prints them: `max_concurrent`, `levels`, `already_done`,
    `integrate`.
    """
    stories = list(stories)
    plan = compute_dry_run_plan(
        stories,
        repo_dir=repo_dir,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        max_concurrent=max_concurrent,
    )
    return {
        "max_concurrent": max_concurrent,
        "levels": plan.levels,
        "already_done": already_done_entries(stories),
        "integrate": plan.integrate,
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


def dry_run_story(
    needle: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
) -> dict[str, Any]:
    """`--dry-run --story`: the one story a story run would drive, on one lane, with no Integrate.

    The plan is the real run's, `orchestrate.story_census` over the story's
    milestone, so every refusal of the run's pre-flight but `ClaimedError`
    (a preview checks no claim) is raised the same: `StoryNotFoundError`,
    `DependencyCycleError`, `StoryBlockedError`. `levels` is
    `compute_dry_run_plan`'s at one lane: the selected story alone, rooted
    where the run would root it; a done blocker it stacks on gets no row.
    `already_done` lists the selected story's entries only, and `integrate`
    is `None`. Read-only by construction: the two `brd` reads and, for a
    done blocker only, the read-only local-branch lookup are its only I/O. No
    `Store` is opened, git is not refreshed, nothing is written.
    """
    root = resolve_repo_dir(repo_dir)
    match = census.find_story(board.roots(repo_dir=root), needle)
    plan = orchestrate.story_census(
        board.tree(match.milestone.id, repo_dir=root),
        match.story,
        root=root,
        branch_prefix=branch_prefix,
    )
    preview = compute_dry_run_plan(
        plan.stories,
        repo_dir=root,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        max_concurrent=1,
    )
    return {
        "max_concurrent": 1,
        "levels": preview.levels,
        "already_done": already_done_entries(
            [story for story in plan.stories if story.id == match.story.id]
        ),
        "integrate": None,
    }


def board_prefix_of(branch_prefix: str | None) -> Callable[[models.CardNode], str]:
    """Run-board spec 3.2: how one milestone's branch prefix is derived under `--board`.

    With `--branch-prefix` omitted the prefix is the milestone card's own
    `dag.task_stem`; given, it is `<branch_prefix>-<stem>`, never the given
    value verbatim, so two milestones can never share it. It reads only the
    card's title and id, never its status, so a milestone that is no longer
    open gets the prefix it ran under. Checking the result (blank, shared) is
    `orchestrate.board_prefixes`'s job, not this one's.
    """

    def prefix_of(card: models.CardNode) -> str:
        stem = dag.task_stem(card)
        return stem if branch_prefix is None else f"{branch_prefix}-{stem}"

    return prefix_of


def dry_run_board(
    *,
    repo_dir: Path,
    branch_prefix: str | None,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
    """`--dry-run --board`: every open milestone, by level, each on its own base.

    Read-only by construction, like `dry_run_milestone`, and composed the way
    `run_board` composes its refusals. `board.roots()` is read once (each root
    already nests its whole tree), leveled by `dag.board_levels` (a done
    milestone drops out, a cycle is `DependencyCycleError`). Each milestone's
    prefix, and each non-open blocker root's, comes from
    `orchestrate.board_prefixes(..., roots=)` over `board_prefix_of`, the very
    check `run_board` makes. Each milestone's base comes from
    `orchestrate.milestone_bases` with `orchestrate._local_branch_exists`, read
    at call time: its one open blocker's `<prefix>-integrate`, or its one
    unlanded blocker's when that branch exists locally, else `base_branch`;
    two such blockers is `MilestoneBlockersError`. The base is the entry's
    `base_branch`, and its `plan` (its own `dry_run_payload`) is computed
    against it. The only I/O past the board read is that read-only
    `git rev-parse`, asked only about an unlanded, non-open blocker. No
    `Store` is opened, no claim is checked, no runner is built, and
    `orchestrate.run_board` is never called. Every refusal is a type already
    in `HANDLED`; a `GitError` from a broken repository propagates, as it
    does from `run_board`.
    """
    root = resolve_repo_dir(repo_dir)
    all_roots = board.roots(repo_dir=root)
    levels = dag.board_levels(all_roots)
    milestones = [card for level in levels for card in level]
    prefixes = orchestrate.board_prefixes(
        milestones, board_prefix_of(branch_prefix), roots=all_roots
    )
    bases = orchestrate.milestone_bases(
        all_roots, prefixes, orchestrate._local_branch_exists(root), base_branch
    )
    return {
        "board": True,
        "max_concurrent": max_concurrent,
        "levels": [
            {
                "level": index,
                "milestones": [
                    {
                        "milestone_id": card.id,
                        "title": card.title,
                        "branch_prefix": prefixes[card.id],
                        "base_branch": bases[card.id],
                        "plan": dry_run_payload(
                            census.flatten_milestone(card).stories,
                            repo_dir=root,
                            branch_prefix=prefixes[card.id],
                            base_branch=bases[card.id],
                            max_concurrent=max_concurrent,
                        ),
                    }
                    for card in level
                ],
            }
            for index, level in enumerate(levels)
        ],
    }


HANDLED: tuple[type[BaseException], ...] = (
    CliError,
    board.BoardError,
    EngineError,
    ValueError,
    locks.LockTimeoutError,
    store_module.LeaseLostError,
    store_module.CorruptJournalError,
)
"""Everything the command turns into an `ok: false` envelope and exit 3.

`ValueError` is in the list for one concrete reason: `dag.short_id` raises a
bare one for a card id that is not a UUID, and a typed `--card` must not come
back as a traceback. `locks.LockTimeoutError` is in it because a start refused
while another `am` process held a project lock past its timeout (spec X7) is a
refusal, not a bug; nothing below the CLI catches it. `store_module.LeaseLostError`
is in it because another process took this run's lease over mid-walk (spec X4):
the fence stopped every write, and the operator gets the envelope naming the new
holder. It is a `BaseException`, so it has to be listed by name.
`store_module.CorruptJournalError` is in it because a crashed run can leave a
torn line in its journal, and `Store.open` reading it (`am reset`, `am resume`)
is a refusal naming the file and line, not a bug; only that subclass, not
`JournalError` as a whole. Anything outside this tuple is a bug in this program
and should crash loudly with its stack intact.
"""


# ── am run --detach (card aff9fdbf) ─────────────────────────────────────────


def release_handed_off(root: Path, run_id: str, token: str) -> None:
    """Release a handed-off lease's claims, then the lease, over a fresh store.

    For a detach that failed after `Lease.hand_off()`: the recorded stage's
    store is already closed and its lease no longer releases anything.
    """
    store = Store.open(root, run_id)
    try:
        try:
            store.release_claims(token)
        finally:
            store.release_lease(token)
    finally:
        store.close()


def run_detached_child(
    *,
    root: Path,
    run_id: str,
    token: str,
    engine: Callable[[Store, control.Lease], dict[str, Any]],
) -> None:
    """What the detached child of `am run --detach` runs: stage 3, then report.

    It opens its own store and adopts `token` (no new lease: the claims the
    parent took stay under it), so the heartbeat runs here. `engine` runs
    stage 3 on that store and lease. Its payload is written to `report.json`
    as `ok_envelope`, or a `HANDLED` error as `error_envelope`, both while
    the lease is still held. Leaving the lease releases the claims, then the
    lease, then the store closes, on every exit. Anything else propagates
    with no report: its traceback goes to `run.log`.
    """
    store = Store.open(root, run_id)
    try:
        with control.Lease(store, adopt=token) as lease:
            try:
                payload = engine(store, lease)
            except HANDLED as error:
                detach.write_report(run_id, render(error_envelope(error)))
                return
            detach.write_report(run_id, render(ok_envelope(payload)))
    finally:
        store.close()


def hand_off_to_child(
    *,
    root: Path,
    run_id: str,
    token: str,
    log: Path,
    engine: Callable[[Store, control.Lease], dict[str, Any]],
    detacher: detach.Detacher,
) -> dict[str, Any]:
    """Start the detached child, point the lease at it, let it go, and report.

    Called with the lease already handed off and the recorded stage's store
    closed, so no connection and no heartbeat thread crosses the fork. The
    child blocks until `go`. The lease row is re-pointed at the child's pid
    over a fresh store while this process is still alive, so the row never
    names a dead pid, and only then is the child let go. A failed spawn or
    pid update releases the claims and lease (after telling a spawned child
    to abort) and propagates.
    """

    def body() -> None:
        run_detached_child(root=root, run_id=run_id, token=token, engine=engine)

    try:
        spawned = detacher(body, log)
    except BaseException:
        release_handed_off(root, run_id, token)
        raise
    try:
        store = Store.open(root, run_id)
        try:
            store.set_lease_holder(token, pid=spawned.pid, host=socket.gethostname())
        finally:
            store.close()
    except BaseException:
        spawned.abort()
        release_handed_off(root, run_id, token)
        raise
    spawned.go()
    return {"run_id": run_id, "pid": spawned.pid, "log": str(log), "detached": True}


def detach_card(
    card_id: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    detacher: detach.Detacher,
    base_branch: str = "master",
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """`am run --card --detach`: stages 1 and 2 here, stage 3 in a detached child.

    `preflight_card` and `recorded_card_run` run exactly as for `run_card`, so
    every refusal is the same. Inside the recorded stage `run.log` is created
    (a failure there releases as any crash does) and the lease is handed
    off, so the stage exits releasing nothing and closes its store. The child
    runs `run_card_engine` on this very `pre` and run id (`hand_off_to_child`).
    """
    pre = preflight_card(
        card_id,
        repo_dir=repo_dir,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        clock=clock,
    )
    with recorded_card_run(pre) as recorded:
        log = detach.create_run_log(pre.run_id)
        token = recorded.lease.hand_off()

    def engine(store: Store, lease: control.Lease) -> dict[str, Any]:
        return asyncio.run(
            run_card_engine(
                pre,
                RecordedRun(run_id=pre.run_id, store=store, lease=lease),
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                control_interval=control_interval,
            )
        )

    return hand_off_to_child(
        root=pre.root, run_id=pre.run_id, token=token, log=log, engine=engine, detacher=detacher
    )


def _check_run_targets(
    *,
    card: str | None,
    milestone: str | None,
    dry_run: bool,
    max_concurrent: int | None = None,
    board: bool = False,
    branch_prefix: str | None = None,
    detach: bool = False,
    story: str | None = None,
) -> None:
    """Refuse a bad `--card` / `--milestone` / `--story` / `--board` / `--branch-prefix` / `--dry-run` / `--max-concurrent` combination as a usage error.

    `typer.BadParameter` is Typer's own exit 2, which `EXIT_ERROR`'s docstring
    reserves. It is raised before the `HANDLED` try block, so nothing is read
    or dispatched. Exactly one of `--card`, `--milestone`, `--story` and
    `--board` is a target; two given are refused before a missing one is. A
    blank `--milestone` or `--story` is refused here too: the census strips
    the needle, and an empty needle is a substring of every title, so on a
    one-milestone (or one-story) board it would silently pick that card.
    `--branch-prefix` is an Option with no default so that board mode can
    omit it (each milestone then uses its own card stem, run-board spec 3.2);
    `--card`, `--milestone` and `--story` still require it, refused here at
    the same exit 2 Typer gave a missing required option. A blank one with
    `--board` is refused, since `<prefix>-<stem>` would start with a dash.
    `--max-concurrent` is `None` when not given, so giving it with `--card`
    or `--story` is refused whatever its value, the default included. The
    Option has no `min=1`, so a value below 1 is refused here, worded and
    routed like every other run-target refusal.
    `--detach` (card aff9fdbf) is refused with `--dry-run`, which writes
    nothing to hand off.
    """
    if board and card is not None:
        raise typer.BadParameter(
            "give --board or --card, not both",
            param_hint="'--board' / '--card'",
        )
    if board and milestone is not None:
        raise typer.BadParameter(
            "give --board or --milestone, not both",
            param_hint="'--board' / '--milestone'",
        )
    if card is not None and milestone is not None:
        raise typer.BadParameter(
            "give --card or --milestone, not both",
            param_hint="'--card' / '--milestone'",
        )
    if story is not None and board:
        raise typer.BadParameter(
            "give --board or --story, not both",
            param_hint="'--board' / '--story'",
        )
    if story is not None and card is not None:
        raise typer.BadParameter(
            "give --card or --story, not both",
            param_hint="'--card' / '--story'",
        )
    if story is not None and milestone is not None:
        raise typer.BadParameter(
            "give --milestone or --story, not both",
            param_hint="'--milestone' / '--story'",
        )
    if card is None and milestone is None and story is None and not board:
        raise typer.BadParameter(
            "one of --card, --milestone, --story or --board is required",
            param_hint="'--card' / '--milestone' / '--story' / '--board'",
        )
    if milestone is not None and not milestone.strip():
        raise typer.BadParameter(
            "--milestone needs a card id or a title substring, not a blank string",
            param_hint="'--milestone'",
        )
    if story is not None and not story.strip():
        raise typer.BadParameter(
            "--story needs a card id or a title piece, not a blank string",
            param_hint="'--story'",
        )
    if not board and branch_prefix is None:
        raise typer.BadParameter(
            "--branch-prefix is required with --card, --milestone or --story",
            param_hint="'--branch-prefix'",
        )
    if board and branch_prefix is not None and not branch_prefix.strip():
        raise typer.BadParameter(
            "--branch-prefix with --board needs a non-blank prefix, not a blank string",
            param_hint="'--branch-prefix'",
        )
    if detach and dry_run:
        raise typer.BadParameter(
            "--dry-run writes nothing and cannot be detached",
            param_hint="'--detach' / '--dry-run'",
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
    if (card is not None or story is not None) and max_concurrent is not None:
        raise typer.BadParameter(
            "--max-concurrent applies only to --milestone or --board",
            param_hint="'--max-concurrent'",
        )


RUN_EXAMPLES = """\
Examples:
  am run --milestone "M9" --branch-prefix m9 --dry-run --pretty       # preview the plan
  am run --milestone "M9" --branch-prefix m9 --verify "uv run pytest"  # run it
  am run --milestone "M9" --branch-prefix m9 --verify "uv run pytest" --detach  # run it in the background
  am run --board --verify "uv run pytest"                             # run every open milestone
  am run --board --verify "uv run pytest" --detach                    # ... in the background
  am status <run-id> --pretty                                         # watch it (another terminal)
  am resume <run-id> --verify "uv run pytest"                         # after a fix, stop or crash
"""


@app.command("run", epilog=RUN_EXAMPLES)
def run(
    card: str | None = typer.Option(
        None,
        "--card",
        help="The subtask card id to drive. Exclusive with --milestone and --board.",
    ),
    milestone: str | None = typer.Option(
        None,
        "--milestone",
        help=(
            "A milestone card id or title substring: drive every remaining subtask. "
            "Exclusive with --card and --board."
        ),
    ),
    whole_board: bool = typer.Option(
        False,
        "--board",
        help=(
            "Drive every open milestone on the board as one dependency graph: a "
            "milestone starts once every milestone blocking it finished done. "
            "Exclusive with --card and --milestone."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "With --milestone: show the plan (story order, each subtask's branch "
            "and base, merged bases) and write nothing. With --board: show every "
            "open milestone by level, each with its own plan, and write nothing."
        ),
    ),
    detach_run: bool = typer.Option(
        False,
        "--detach",
        help=(
            "With --card, --milestone or --board: make every check here (and, for "
            "--card or --milestone, record and lease the run), then hand the run to "
            "a background process in its own session and print its pid and log. "
            "A card or milestone run's output goes to "
            "<data dir>/runs/<run-id>/run.log and its final envelope to "
            "report.json; a board's output goes to "
            "<data dir>/boards/<stamp>-<digest>.log and its final envelope to "
            "<stamp>-<digest>.report.json."
        ),
    ),
    max_concurrent: int | None = typer.Option(
        None,
        "--max-concurrent",
        help=(
            "With --milestone: how many stories run at once "
            f"(default {DEFAULT_MAX_CONCURRENT}). A story starts as soon as its "
            "blockers finish. With --board: how many stories run at once across "
            "the whole board."
        ),
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    base_branch: str = typer.Option(
        "master",
        "--base-branch",
        help=(
            "Where unblocked stories start (and --card's branch is cut from). "
            "Never modified."
        ),
    ),
    branch_prefix: str | None = typer.Option(
        None,
        "--branch-prefix",
        help=(
            "Milestone prefix for the derived branch name, e.g. `m2`. Required with "
            "--card and --milestone. Optional with --board: each milestone's prefix "
            "is its own card stem, or `<prefix>-<stem>` when given."
        ),
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help="Proceed without any --verify command, on purpose.",
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
    """Drive one subtask card, a whole milestone, or every open milestone (--board) end to end, or preview a milestone or the board with --dry-run."""
    _check_run_targets(
        card=card,
        milestone=milestone,
        dry_run=dry_run,
        max_concurrent=max_concurrent,
        board=whole_board,
        branch_prefix=branch_prefix,
        detach=detach_run,
    )
    lanes = DEFAULT_MAX_CONCURRENT if max_concurrent is None else max_concurrent
    try:
        if whole_board and dry_run:
            payload = dry_run_board(
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
                max_concurrent=lanes,
            )
        elif whole_board and detach_run:
            # Read as `orchestrate.detach_board` and `detach.fork_detacher`
            # so a test can patch either.
            payload = orchestrate.detach_board(
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix_of=board_prefix_of(branch_prefix),
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
                detacher=detach.fork_detacher,
            )
        elif whole_board:
            # Read as `orchestrate.run_board` so a test can patch it there.
            # No runner_factory and no driver: production gets the defaults.
            # `lanes` is the board-wide bound on stories running at once.
            payload = orchestrate.run_board(
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix_of=board_prefix_of(branch_prefix),
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
            )
        elif milestone is not None and dry_run:
            payload = dry_run_milestone(
                milestone,
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
                max_concurrent=lanes,
            )
        elif milestone is not None and detach_run:
            # Read as `orchestrate.detach_milestone` and `detach.fork_detacher`
            # so a test can patch either.
            payload = orchestrate.detach_milestone(
                milestone,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
                detacher=detach.fork_detacher,
            )
        elif milestone is not None:
            # Read as `orchestrate.run_milestone` so a test can patch it there.
            # No runner_factory and no driver: production gets
            # `default_runner_factory` and `drive_subtask`.
            payload = orchestrate.run_milestone(
                milestone,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
            )
        elif detach_run:
            # Read as `detach.fork_detacher` so a test can patch it there.
            payload = detach_card(
                card,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                allow_no_verification=allow_no_verification,
                commands=list(verify),
                detacher=detach.fork_detacher,
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
    if detach_run:
        # A handed-off run's outcome is in its report.json, not this exit code.
        return
    # A board payload carries one entry per milestone under `milestones`, each
    # with a `status`; a board dry-run carries no `milestones` key at all, so
    # it is read with `.get` and an empty default. A card payload reports
    # `status`. A milestone payload has no `status` key: it carries
    # `escalated: true` only when it stopped, a clean one carries `done: true`,
    # and a dry-run preview carries neither, so it is read with `.get`, never
    # indexed. Every check is strict equality on purpose: a `stopped` card
    # (addendum P4), or a stopped, cancelled or blocked milestone, is not an
    # escalation and exits 0.
    if whole_board:
        escalated = any(
            entry.get("status") == "escalated" for entry in payload.get("milestones", [])
        )
    elif milestone is None:
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
    the most recent one. The lease and every control request are read on the
    same connection and rendered by `control_view`, still without a write.
    The `integrity` key compares the run's journal with the loaded tree through
    `integrity_view`, which reads the journal and writes nothing either.
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
        lease = store_module.read_lease(conn, wanted)
        now = _utcnow()
        # Only a live lease's claims count (X5): a dead one's leftover rows
        # are anyone's to take, so they are not shown as held.
        claims = (
            [claim.key for claim in store_module.held_claims(conn, wanted, lease.token)]
            if lease is not None and control.lease_is_live(lease, now=now)
            else []
        )
        state = control_view(
            lease,
            store_module.control_requests(conn, wanted),
            now=now,
            claims=claims,
        )
        payload = status_payload(run, state)
        payload["integrity"] = integrity_view(wanted, run, lease, now=now)
        return payload
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
    """This project's run history, newest first, each run with its lease and progress.

    An empty history is an empty list, not a refusal: a project that has never
    been run is a fact. `model_dump()` keeps the `Path` and `datetime` objects
    for `render`'s `default=str`, exactly as `status_payload` does, so a run
    looks the same in both commands.

    `lease` is filled here, not in `store.list_runs`: `live` needs `control`,
    which `store` must not import. Each run's `run_leases` row is read on the
    same connection and shaped by `_lease_fields`, the helper `control_view`
    uses, so it is `am status`'s `control.lease` minus `acquired_at`, or
    `None` when the run has no lease row. One `now` judges the whole listing.

    `progress` arrives already counted by `store.list_runs`; `model_copy`
    keeps it and `model_dump` carries it into the entry unchanged.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        now = _utcnow()
        entries = []
        for summary in store_module.list_runs(conn):
            lease = store_module.read_lease(conn, summary.id)
            shown = (
                None
                if lease is None
                else store_module.RunLease(**_lease_fields(lease, now=now))
            )
            entries.append(summary.model_copy(update={"lease": shown}).model_dump())
        return {"runs": entries}
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


@dataclass(frozen=True)
class LogsSelection:
    """The attempt `am logs` reports on, as `select_logs` chose it.

    An agent phase carries its `Attempt` row. A deterministic phase has no
    row (spec e1b1e7d5 Decision 2), so it carries the `<phase>.N` number and
    directory found on disk instead, and `attempt` is `None`.
    """

    run: models.Run
    story: models.StoryRun
    subtask: models.SubtaskRun
    phase: models.PhaseRun
    attempt: models.Attempt | None
    step_attempt: int | None = None
    step_directory: Path | None = None

    def payload(self) -> dict[str, Any]:
        """`logs`' one-shot payload: `logs_payload` or `step_logs_payload`."""
        if self.attempt is not None:
            return logs_payload(
                self.run, self.story, self.subtask, self.phase, self.attempt
            )
        if self.step_attempt is None or self.step_directory is None:
            raise CliError(
                f"phase {self.phase.name!r} of card {self.subtask.card_id!r}"
                " was selected with neither an attempt row nor a step directory"
            )
        return step_logs_payload(
            self.run,
            self.story,
            self.subtask,
            self.phase,
            self.step_attempt,
            self.step_directory,
        )

    def followed_path(self) -> Path | None:
        """The file `logs --follow` reads: the agent attempt's recorded
        `stdout_path` (the launcher merges stderr into it), or a deterministic
        phase's `<phase>.N/stdout.log`. `None` when an agent attempt recorded
        no stdout path."""
        if self.attempt is not None:
            return self.attempt.stdout_path
        if self.step_directory is None:
            return None
        return self.step_directory / verify_step.STDOUT_LOG


def select_logs(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
) -> LogsSelection:
    """Which attempt §10's `logs` reports, shared by the one-shot and `--follow`.

    Read-only, like `status_for`: the projection is reached through the free
    `open_db` / `load_run` rather than `Store.open`, which would construct a
    `Journal` and therefore mint a run directory for a run that may not exist.
    The connection is closed on every path including the refusals.

    A `--phase` naming a deterministic phase is answered from disk: its
    attempts are the `<phase>.N` directories `run_one_step` created (spec
    e1b1e7d5). With no `--phase`, only recorded `Attempt` rows count.
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
        step = next(
            (
                item
                for item in subtask.phases
                if item.name == phase and item.kind == "deterministic"
            ),
            None,
        )
        if step is not None:
            # Read-only on disk: `recorded_attempts` and `attempt_path` create
            # nothing, unlike `attempt_dir` and `run_dir`.
            recorded = paths.recorded_attempts(run_id, card, step.name)
            n = select_step_attempt(subtask, step, recorded, attempt)
            directory = paths.attempt_path(run_id, card, step.name, n)
            return LogsSelection(
                run=run,
                story=story,
                subtask=subtask,
                phase=step,
                attempt=None,
                step_attempt=n,
                step_directory=directory,
            )
        chosen_phase, chosen_attempt = select_attempt(
            subtask, phase=phase, attempt=attempt
        )
        return LogsSelection(
            run=run,
            story=story,
            subtask=subtask,
            phase=chosen_phase,
            attempt=chosen_attempt,
        )
    finally:
        conn.close()


def logs_for(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
) -> dict[str, Any]:
    """§10's `logs`: one attempt of one card of one run, with its artifacts.

    `run_id` is required -- §10 writes `logs <run-id> <card>` and there is no
    "most recent run" reading of it to default to. The selection, and its
    read-only rules, are `select_logs`'; the artifacts are read after the
    projection connection is closed, from the paths the selection names.
    """
    return select_logs(
        run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
    ).payload()


def logs_follow_for(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
    since_offset: int = 0,
) -> LogsSelection:
    """Validate `am logs --follow` and return the attempt it streams.

    The same selection as `logs_for` (`select_logs`), so every refusal the
    one-shot makes is made here too, before the hello line. On top of those:
    a negative `--since-offset` (worded as `watch --since` is), and an agent
    attempt that recorded no stdout path, since the hello must name a file.
    The projection connection is closed by `select_logs` before this
    returns. The selection, not only its file, is returned because the
    stream looks the attempt's status up again before every read
    (`logs_end_status`, card 4.2), and that needs the run, card, phase and
    attempt number.
    """
    if since_offset < 0:
        raise CliError(f"--since-offset must be 0 or more, got {since_offset}")
    selection = select_logs(
        run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
    )
    if selection.followed_path() is None:
        number = (
            selection.attempt.n
            if selection.attempt is not None
            else selection.step_attempt
        )
        raise CliError(
            f"attempt {number} of phase {selection.phase.name!r} of card"
            f" {selection.subtask.card_id!r} recorded no stdout path,"
            " so there is no file to follow"
        )
    return selection


def logs_end_status(selection: LogsSelection, *, repo_dir: Path) -> str | None:
    """The followed attempt's status if it is over, `None` while it runs.

    Looked up afresh on every call, because `selection` is a snapshot from
    before the stream began. Read-only, like `select_logs`: the free
    `open_db` / `load_run`, the connection closed before anything else, and
    for a deterministic phase only `paths.recorded_attempts`; never
    `Store.open`, `paths.attempt_dir` or `paths.run_dir`, which create
    directories. An agent attempt is over once its status is anything but
    `started`; a deterministic one maps through `step_end_status`. A run,
    card, phase or attempt that can no longer be found is a refusal, which
    `_stream_logs` reports on stderr at exit 3.
    """
    run_id = selection.run.id
    card = selection.subtask.card_id
    name = selection.phase.name
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise UnknownRunError(
            f"run {run_id!r} is not in the projection for {root} any more"
        )
    found = find_subtask(run, card)
    if found is None:
        raise UnknownCardError(f"card {card!r} is not in run {run_id!r} any more")
    _, subtask = found
    phase = next((item for item in subtask.phases if item.name == name), None)
    if phase is None:
        raise UnknownPhaseError(f"card {card!r} has no phase {name!r} any more")
    if selection.attempt is None:
        if selection.step_attempt is None:
            raise CliError(
                f"phase {name!r} of card {card!r} was selected with neither"
                " an attempt row nor a step attempt"
            )
        return step_end_status(
            subtask,
            phase,
            selection.step_attempt,
            paths.recorded_attempts(run_id, card, name),
        )
    n = selection.attempt.n
    row = next((item for item in phase.attempts if item.n == n), None)
    if row is None:
        raise UnknownAttemptError(
            f"phase {name!r} of card {card!r} has no attempt {n} any more"
        )
    return None if row.status == "started" else row.status


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
    follow: bool = typer.Option(
        False,
        "--follow",
        help=(
            "Keep printing the attempt's stdout as it grows, one JSON object"
            " per line; once the attempt is over and the file stops growing,"
            ' print {"event":"end","status":...} and exit 0.'
        ),
    ),
    since_offset: int | None = typer.Option(
        None,
        "--since-offset",
        metavar="BYTES",
        help="With --follow, start at this byte offset of the stdout file (default 0).",
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print one attempt's prompt, result and captured stdout/stderr.

    With --follow, print a hello line naming the attempt's stdout file and
    then its bytes as `{"offset", "text"}` lines, the existing content first
    and then each append. Once the attempt has a terminal status and the
    file has stopped growing, a last `{"event": "end", "status": ...}` line
    follows and the exit is 0. A refusal is still one envelope at exit 3,
    printed before any stream line.
    """
    offset = since_offset if since_offset is not None else 0
    try:
        # `None` means --since-offset was not given; any given value, 0
        # included, needs --follow, as --from-now does for `watch`.
        if since_offset is not None and not follow:
            raise CliError(
                "--since-offset needs --follow: it resumes a stream,"
                " and without --follow there is no stream"
            )
        if follow:
            selection = logs_follow_for(
                run_id,
                card,
                repo_dir=repo_dir,
                phase=phase,
                attempt=attempt,
                since_offset=offset,
            )
        else:
            payload = logs_for(
                run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
            )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    if follow:
        _stream_logs(selection, repo_dir=repo_dir, offset=offset)
        return
    typer.echo(render(ok_envelope(payload), pretty=pretty))


WATCH_HANDLED: tuple[type[BaseException], ...] = (*HANDLED, store_module.JournalError)
"""`HANDLED` plus `JournalError`, for `watch` only.

A corrupt journal is a refusal for a reader, so `watch` turns it into an
`ok: false` envelope at exit 3. It is not added to `HANDLED` itself: for the
commands that write a journal, a corrupt one is still a bug that should crash
with its stack intact. `MissingJournalError` never reaches this tuple; `watch_for`
turns it into `UnknownRunError` first.
"""


def _check_watch_run_id(run_id: str) -> None:
    """Refuse a run id that is a path rather than one directory name.

    `Journal._for_reading` joins the id onto `<data dir>/runs/`, so `..` or a
    `/` would read a `journal.jsonl` outside the runs directory.
    """
    if run_id in ("", ".", "..") or Path(run_id).name != run_id:
        raise UnknownRunError(
            f"run id {run_id!r} is not a run directory name"
            " (`agent-manager watch --all` reads every run there is)"
        )


def _journal_events(run_id: str, *, since: int) -> list[dict[str, Any]]:
    """`run_id`'s journal lines with `seq > since`, JSON-mode, in `seq` order.

    Opened through `Journal._for_reading`, never `Journal(run_id)`: the normal
    constructor calls `paths.run_dir`, which would create a directory for a run
    that does not exist (am-watch design 3.7). A torn last line is an append in
    flight and is skipped. Raises `MissingJournalError` when there is no journal.
    """
    lines = store_module.Journal._for_reading(run_id).read(ignore_torn_tail=True)
    return [line.model_dump(mode="json") for line in lines if line.seq > since]


def watch_for(
    run_id: str | None,
    *,
    all_runs: bool = False,
    since: int = 0,
    follow: bool = False,
    from_now: bool = False,
    since_given: bool = False,
) -> dict[str, Any]:
    """The payload of `am watch`: `{"events": [...]}`.

    Exactly one of `run_id` and `all_runs`. With `run_id`, a run with no
    journal is `UnknownRunError`. With `all_runs`, every directory under
    `<data dir>/runs/` is read, a run with no journal yet is skipped, and a
    missing `runs/` is no events: a watcher pointed at the wrong data
    directory sees nothing, not an error (am-watch design 3.7). Events are
    ordered by `(run_id, seq)`; `since` filters each run's own `seq`.

    `from_now` (`--from-now`) is refused with `since_given` (any `--since`
    on the command line, 0 included) and without `follow`. Both refusals
    come before any journal is read, so `watch` prints them as the usual
    exit-3 envelope with no stream line.
    """
    if all_runs == (run_id is not None):
        raise CliError(
            "give exactly one of RUN_ID or --all:"
            " `am watch RUN_ID` reads one run, `am watch --all` reads every run"
        )
    if since < 0:
        raise CliError(f"--since must be 0 or more, got {since}")
    if from_now and since_given:
        raise CliError(
            "--from-now and --since are exclusive: --from-now skips the whole"
            " backlog, --since picks where in it to start; give one of them"
        )
    if from_now and not follow:
        raise CliError(
            "--from-now needs --follow: it skips the backlog of a stream,"
            " and without --follow there is only the backlog"
        )
    if run_id is not None:
        _check_watch_run_id(run_id)
        try:
            return {"events": _journal_events(run_id, since=since)}
        except store_module.MissingJournalError as error:
            raise UnknownRunError(
                f"run {run_id!r} has no journal under the data directory"
                " (`agent-manager watch --all` reads every run there is)"
            ) from error
    events: list[dict[str, Any]] = []
    for each in paths.list_run_ids():
        try:
            events.extend(_journal_events(each, since=since))
        except store_module.MissingJournalError:
            continue
    return {"events": events}


WATCH_POLL_SECONDS = 0.25
"""How long `am watch --follow` sleeps between polls (am-watch design 3.2).

Internal: no output line carries it, so a later move to inotify changes no
byte of the stream.
"""

WATCH_MAX_POLLS: int | None = None
"""How many polls follow the backlog before the stream ends by itself.

`None` in production: poll until Ctrl-C or a closed pipe. Tests bound it so a
`CliRunner` invocation returns.
"""


def _watch_sleep(seconds: float) -> None:
    """The pause between polls. A module attribute so tests can replace it."""
    time.sleep(seconds)


def _watch_hello() -> dict[str, Any]:
    """The first line of `am watch --follow`, and the only one that is not a
    JournalLine: where a future schema bump is announced (design 3.6)."""
    return {
        "event": "watch",
        "schema": 1,
        "am": __version__,
        "runs_dir": str(paths.data_dir() / "runs"),
    }


def _emit_stream_line(obj: Mapping[str, Any]) -> None:
    """One compact JSON object and a newline on stdout, flushed at once, so a
    consumer reading a pipe gets each line as it is written."""
    sys.stdout.write(render(obj) + "\n")
    sys.stdout.flush()


def _poll_watch(
    run_id: str | None, *, since: int, cursors: dict[str, int]
) -> Iterator[dict[str, Any]]:
    """One pass over the watched runs: each line above its run's cursor.

    `cursors` maps a run directory name to the highest `seq` already emitted
    for it, so the cursor is `(run_id, seq)` and nothing else (design 3.3). A
    lease takeover appends to the same file at a higher `seq` and needs no
    case of its own. A run not yet in `cursors` starts at `since`. With
    `--all` the runs are listed again on every pass, so a run that appears
    later is picked up. A run with no journal, now or any more, has nothing
    to emit. A torn last line is skipped by `_journal_events` and emitted on a
    later pass once it is complete.
    """
    run_ids = [run_id] if run_id is not None else paths.list_run_ids()
    for each in run_ids:
        try:
            events = _journal_events(each, since=cursors.get(each, since))
        except store_module.MissingJournalError:
            continue
        for event in events:
            cursors[each] = event["seq"]
            yield event


def _follow_watch(
    run_id: str | None,
    *,
    since: int,
    sleep: Callable[[float], None],
    max_polls: int | None,
    from_now: bool = False,
) -> Iterator[dict[str, Any]]:
    """The backlog above `since`, then every line appended after it.

    One pass at once for the backlog, then `sleep(WATCH_POLL_SECONDS)` and
    another pass, `max_polls` times or forever when it is `None`. One cursor
    dict spans every pass, so no `seq` of a run is emitted twice and none is
    skipped, however its lines are spread across polls.

    With `from_now` the backlog pass still runs, so it seeds each existing
    run's cursor to its highest complete `seq`, but nothing it reads is
    yielded. A torn last line is not read, so it is emitted once complete;
    a run with no complete line, or none at all yet, has no cursor and is
    emitted in full from `since` when its lines appear.
    """
    cursors: dict[str, int] = {}
    backlog = _poll_watch(run_id, since=since, cursors=cursors)
    if from_now:
        for _ in backlog:
            pass
    else:
        yield from backlog
    polls = 0
    while max_polls is None or polls < max_polls:
        sleep(WATCH_POLL_SECONDS)
        polls += 1
        yield from _poll_watch(run_id, since=since, cursors=cursors)


def _silence_stdout() -> None:
    """Point fd 1 at /dev/null once the reader has closed the pipe.

    Without it, the interpreter's own flush of stdout at exit raises a second
    `BrokenPipeError` and prints it (Python docs, "Note on SIGPIPE"). A
    stdout with no file descriptor (a test runner's buffer) is left alone.
    """
    try:
        descriptor = sys.stdout.fileno()
    except (OSError, ValueError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, descriptor)
    os.close(devnull)


def _stream_watch(run_id: str | None, *, since: int, from_now: bool = False) -> None:
    """The body of `am watch --follow`, once `watch_for` has accepted the call.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll. Ctrl-C and a closed pipe are
    how a stream normally ends: exit 0, nothing on stderr. A journal that
    turns corrupt after the hello line cannot get an envelope, because every
    line after the first must be a JournalLine. So its message goes to stderr
    and the exit is `EXIT_ERROR`.
    """
    try:
        _emit_stream_line(_watch_hello())
        for event in _follow_watch(
            run_id,
            since=since,
            sleep=_watch_sleep,
            max_polls=WATCH_MAX_POLLS,
            from_now=from_now,
        ):
            _emit_stream_line(event)
    except KeyboardInterrupt:
        return
    except BrokenPipeError:
        _silence_stdout()
        return
    except WATCH_HANDLED as error:
        typer.echo(f"am watch: {error}", err=True)
        raise typer.Exit(EXIT_ERROR) from None


def _read_log_bytes(path: Path | None, offset: int) -> bytes:
    """`path`'s bytes from `offset` to its current end, for `logs --follow`.

    Never raises for the file's state: a path not recorded, a file not
    written yet, a directory, an unreadable mode, or a file shorter than
    `offset` all read as `b""`, so a poll that finds nothing waits for the
    next one (spec card 4.1, "File not there yet"). Opens for reading only,
    so nothing is created.
    """
    if path is None:
        return b""
    try:
        with Path(path).open("rb") as handle:
            handle.seek(offset)
            return handle.read()
    except OSError:
        return b""


def _utf8_complete_length(data: bytes) -> int:
    """How many leading bytes of `data` end on a UTF-8 character boundary.

    Only a trailing sequence whose lead byte is valid but whose continuation
    bytes have not all arrived yet is held back: at most 3 bytes. Any other
    byte, including a stray continuation or an invalid lead, counts as
    complete, so `errors="replace"` turns it into U+FFFD and an invalid
    tail can never stall the stream.
    """
    end = len(data)
    for index in range(end - 1, max(end - 4, 0) - 1, -1):
        byte = data[index]
        if byte & 0xC0 == 0x80:
            continue
        if byte < 0x80:
            return end
        if 0xC2 <= byte <= 0xDF:
            needed = 2
        elif 0xE0 <= byte <= 0xEF:
            needed = 3
        elif 0xF0 <= byte <= 0xF4:
            needed = 4
        else:
            return end
        return index if end - index < needed else end
    return end


def _logs_hello(path: Path, offset: int) -> dict[str, Any]:
    """The first line of `am logs --follow`: which file, from which byte.

    Its own `schema`, independent of the journal's and of `watch`'s hello,
    so the chunk shape can evolve without touching either.
    """
    return {"event": "logs", "schema": 1, "path": str(path), "offset": offset}


def _follow_logs(
    path: Path | None,
    *,
    offset: int,
    sleep: Callable[[float], None],
    max_polls: int | None,
    end_status: Callable[[], str | None],
) -> Iterator[dict[str, Any]]:
    """`path`'s bytes from `offset` as `{"offset", "text"}` chunks, then each
    append, then `{"event": "end", "status": ...}` once the attempt is over.

    `end_status()` is asked before every read, so bytes written just before
    the status flips are still read. While it returns `None` the attempt is
    running: one read, then `sleep(WATCH_POLL_SECONDS)` and another,
    `max_polls` sleeps or forever when it is `None`. Once it returns a
    status, reads repeat with no sleep and no poll bound until one finds
    nothing new; then a trailing partial UTF-8 character still held back is
    yielded as one replacement-decoded chunk (the file will not grow to
    complete it) and the `end` line closes the stream. One byte cursor spans
    every read, so chunks are contiguous. A read that finds nothing past the
    cursor (no file yet, a file shorter than the cursor) yields nothing.
    """
    cursor = offset
    polls = 0
    while True:
        ended = end_status()
        data = _read_log_bytes(path, cursor)
        complete = _utf8_complete_length(data)
        if complete:
            yield {
                "offset": cursor,
                "text": data[:complete].decode("utf-8", errors="replace"),
            }
            cursor += complete
            if ended is not None:
                continue
        if ended is not None:
            if data:
                yield {"offset": cursor, "text": data.decode("utf-8", errors="replace")}
            yield {"event": "end", "status": ended}
            return
        if max_polls is not None and polls >= max_polls:
            return
        sleep(WATCH_POLL_SECONDS)
        polls += 1


def _stream_logs(selection: LogsSelection, *, repo_dir: Path, offset: int) -> None:
    """The body of `am logs --follow`, once `logs_follow_for` has accepted it.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll. The stream ends by itself
    with the `end` line once `logs_end_status` finds the attempt over and the
    file drained: exit 0. Ctrl-C and a closed pipe also end it at exit 0,
    nothing on stderr. After the hello line no envelope can be printed, so a
    handled error, a failed status re-lookup included, goes to stderr and the
    exit is `EXIT_ERROR`, mirroring `_stream_watch`.
    """
    path = selection.followed_path()
    try:
        _emit_stream_line(_logs_hello(path, offset))
        for line in _follow_logs(
            path,
            offset=offset,
            sleep=_watch_sleep,
            max_polls=WATCH_MAX_POLLS,
            end_status=lambda: logs_end_status(selection, repo_dir=repo_dir),
        ):
            _emit_stream_line(line)
    except KeyboardInterrupt:
        return
    except BrokenPipeError:
        _silence_stdout()
        return
    except HANDLED as error:
        typer.echo(f"am logs: {error}", err=True)
        raise typer.Exit(EXIT_ERROR) from None


@app.command("watch")
def watch(
    run_id: str | None = typer.Argument(
        None,
        metavar="[RUN_ID]",
        help="The run whose journal is read. Omit it and pass --all for every run.",
    ),
    all_runs: bool = typer.Option(
        False, "--all", help="Read every run's journal under the data directory."
    ),
    since: int | None = typer.Option(
        None,
        "--since",
        metavar="SEQ",
        help="Only events whose seq is greater than SEQ (default 0).",
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        help="Keep printing events, one JSON object per line, until interrupted.",
    ),
    from_now: bool = typer.Option(
        False,
        "--from-now",
        help=(
            "With --follow, skip the backlog: print only events appended after"
            " the command starts. Exclusive with --since."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print a run's journal events, or every run's, once as one envelope.

    With --follow, print a hello line and then each event as its own line of
    JSON, the backlog first and then new ones as they are appended, until
    interrupted. With --follow --from-now, the backlog is skipped and only
    events appended after the start are printed. A refusal is still one
    envelope at exit 3, printed before any stream line.
    """
    # `None` means --since was not given, which --from-now must tell apart
    # from an explicit `--since 0`; every other use wants the number.
    since_given = since is not None
    since_value = since if since is not None else 0
    try:
        payload = watch_for(
            run_id,
            all_runs=all_runs,
            since=since_value,
            follow=follow,
            from_now=from_now,
            since_given=since_given,
        )
    except WATCH_HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    if follow:
        _stream_watch(run_id, since=since_value, from_now=from_now)
        return
    typer.echo(render(ok_envelope(payload), pretty=pretty))


def _resume_from_checkpoint(
    run: models.Run,
    *,
    root: Path,
    allow_no_verification: bool,
    commands: Sequence[str],
    runner_factory: RunnerFactory | None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Continue a `task` run's one in-flight subtask from its newest checkpoint.

    Every refusal that needs no store -- nothing in flight, a card the board
    lost -- comes before `Store.open`. The checkpoint can only be read through
    the store, so its refusals (`checkpoint_resume_phase`) come right after it
    is opened and before the first write. Then the orphan attempts are marked
    `harness_error`, the run, story and subtask are recorded `started`, and
    `drive_subtask_async` walks `TASK` from the checkpoint, whose queue says
    where the walk goes on. A `milestone` run never comes here: `resume_run`
    hands it to `orchestrate.run_milestone` (card 54e4ec29).

    Live control (C11) and claims (X5), as in `run_card`: the card is refused
    before `Store.open` if another live run claims it, and from right after
    `Store.open` through the final rows this life of the run holds a fresh
    `control.Lease` with the `card:<id>` claim (`run_lease`); a dead holder it
    took over is reported under `took_over`. The walk runs under
    `control.controlled`. A pause parks it `stopped`; a cancel parks it and
    records the run `cancelled` (`card_run_status`).

    Once the checkpoint is accepted, the run's pending board comments are
    flushed (board-comments B7) and their warnings lead the payload's. Next
    comes, when a passed `commands` differs from the suite the checkpoint
    keeps (`runtime_engine.kept_commands`), one `verification: kept from
    checkpoint: [...]` warning naming the kept suite (card 5b19aa93).
    """
    story, subtask = select_resumable(run)
    card = board.show(subtask.card_id, repo_dir=root)
    parent = board.show(story.card_id, repo_dir=root)
    orphans = orphan_attempts(subtask)
    resumed = subtask.model_copy(update={"status": "started"})
    claims = [control.card_claim(subtask.card_id)]
    # Read-only and before `Store.open` (X5); the run's own claims are not a
    # conflict, and `take_lease` below re-checks atomically.
    refuse_claimed(root, claims, run_id=run.id)

    store = Store.open(root, run.id)
    try:
        # Right after `Store.open` and inside the `try` that closes the store:
        # a lost race refuses before the orphan writes, every write below is
        # fenced, and the claims and lease are released before `store.close()`
        # on every exit, a checkpoint refusal included (C2, X5).
        with run_lease(store, claims=claims) as lease:
            checkpoint = store.latest_checkpoint(subtask.card_id)
            # Refuses a done, phase-escalated or digest-mismatched checkpoint
            # before any write. The phase it names is not reported: the walk
            # may decline the checkpoint (its worktree could not be kept), so
            # `resumed_from` is read from the summary after the walk.
            checkpoint_resume_phase(checkpoint, card_id=subtask.card_id, run_id=run.id)
            # Card 5b19aa93: the walk keeps the checkpoint's suite, not
            # `commands`. Say so when a passed `--verify` differs from it; an
            # omitted flag or an unknown kept suite says nothing.
            kept = runtime_engine.kept_commands(checkpoint)
            kept_warning = (
                [f"verification: kept from checkpoint: {kept!r}"]
                if commands and kept is not None and list(commands) != kept
                else []
            )
            # Board-comments B7: this run's leftover comments go out under this
            # life's lease, after every refusal and before the walk goes on; a
            # board failure is a warning, never a refusal (B8).
            flushed = comments.flush(store, root, run_id=run.id)
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

            stop = StopSignal()
            drive = asyncio.run(
                control.controlled(
                    drive_subtask_async(
                        store=store,
                        run_id=run.id,
                        card=card,
                        parent=parent,
                        subtask=resumed,
                        repo_dir=root,
                        commands=commands,
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        stop=stop,
                        resume_from=checkpoint,
                    ),
                    store=store,
                    stop=stop,
                    lease=lease,
                    interval=control_interval,
                )
            )
            summary = drive.summary
            run_status = card_run_status(summary, stop)

            store.record_run(run.model_copy(update={"status": run_status}))
            store.record_story(story.model_copy(update={"status": summary.status}))
            store.record_subtask(
                story.card_id, resumed.model_copy(update={"status": summary.status})
            )

        payload: dict[str, Any] = {
            "run_id": run.id,
            "card_id": subtask.card_id,
            "story_id": story.card_id,
            "branch": subtask.branch,
            "base_branch": subtask.base_branch,
            "worktree": None
            if subtask.worktree_path is None
            else str(subtask.worktree_path),
            "status": run_status,
            "failed_phase": summary.failed_phase,
            "detail": summary.detail,
            "skipped": list(summary.skipped),
            "warnings": [*flushed, *kept_warning, *drive.warnings],
            "resumed_from": summary.resumed_at,
            "discarded_attempts": [
                {"phase": orphan.name, "n": attempt.n} for orphan, attempt in orphans
            ],
        }
        if lease.displaced is not None:
            # A dead holder's lease was taken over (X5): say whose.
            payload["took_over"] = {
                "pid": lease.displaced.pid,
                "host": lease.displaced.host,
                "heartbeat_at": lease.displaced.heartbeat_at.isoformat(),
            }
        return payload
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
    """Pick a stopped, escalated or killed run back up from its checkpoints (§9).

    The run's recorded `workflow` decides. A `task` run continues its one
    in-flight subtask (`_resume_from_checkpoint`, card 02890d5d), exactly as
    before. A `milestone` run continues the whole milestone under the same
    run id (`orchestrate.run_milestone(resume_run_id=...)`, card 54e4ec29).
    Any other workflow is refused.

    The order is load-bearing in the same way `run_card`'s is, only inverted:
    every refusal -- unknown run, nothing in flight, a card the board lost --
    happens before `Store.open`, because `Store.open` constructs a `Journal`
    and therefore mints a run directory, and a refusal that left one behind
    would be this command writing state for a run it declined to touch.

    Branch, base branch and worktree come from the recorded run and never
    from a flag: §9's "the run records what it was started with" is the
    reason the record exists. The two knobs the record does *not* carry --
    `models.RunConfig` has no suite commands and no `allow_no_verification` --
    are still taken as arguments. A walk continued from a checkpoint never
    reads them: its binding comes from the checkpoint's pool, and a `task`
    resume whose `commands` differ from that pool's adds a `verification:
    kept from checkpoint: [...]` warning. On a milestone
    they also reach what starts afresh -- subtasks with no checkpoint, merged
    bases and Integrate.

    A cancelled run is refused for both workflows (live control C9), and so
    is a run whose lease is still live (C10): both refusals read only the
    connection that loaded the run.
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
        # C9, then C10: both read-only and before anything is written, so a
        # refusal leaves no run directory, row or journal line behind.
        if run.status == "cancelled":
            raise NotResumableError(
                f"run {run.id} was cancelled; start new work with `am run --milestone`"
            )
        lease = store_module.read_lease(conn, run.id)
        now = _utcnow()
        if lease is not None and control.lease_is_live(lease, now=now):
            raise _run_is_live_error(lease, now)
    finally:
        conn.close()
    if run.workflow == WORKFLOW_NAME:
        return _resume_from_checkpoint(
            run,
            root=root,
            allow_no_verification=allow_no_verification,
            commands=commands,
            runner_factory=runner_factory,
        )
    # Read as `orchestrate.run_milestone` so a test can patch it there.
    if run.workflow == orchestrate.MILESTONE_WORKFLOW:
        return orchestrate.run_milestone(
            None,
            repo_dir=root,
            commands=list(commands),
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            resume_run_id=run.id,
        )
    raise NotResumableError(
        f"run {run.id!r} records workflow {run.workflow!r}, and `resume` continues"
        f" only {WORKFLOW_NAME!r} and {orchestrate.MILESTONE_WORKFLOW!r} runs"
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
        help=(
            "A walk continued from a checkpoint keeps the opt-out the run started "
            "with. On a milestone run, this applies to what starts afresh: "
            "subtasks with no checkpoint, merged bases and Integrate."
        ),
    ),
    verify: list[str] = typer.Option(
        [],
        "--verify",
        help=(
            "A walk continued from a checkpoint keeps the suite the run started "
            "with, and says so in `warnings` when it differs. On a milestone run, "
            "this is the suite for what starts afresh: subtasks with no "
            "checkpoint, merged bases and Integrate."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Continue a stopped, escalated or killed run from its checkpoints, and drive it to the end.

    No `--base-branch` and no `--branch-prefix`: both were decided when the run
    started and are recorded. A `task` run continues its one subtask; a
    `milestone` run continues the whole milestone under the same run id.
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
    # A task payload reports `status`; a milestone payload has none and
    # carries `escalated: true` only when it stopped, as for `run`. Both
    # checks are strict on purpose: a `stopped` walk (addendum P4) is not an
    # escalation, so it exits 0 with an ok envelope.
    if payload.get("status") == "escalated" or payload.get("escalated") is True:
        raise typer.Exit(EXIT_ESCALATED)


CONTROL_COMMANDS: tuple[str, ...] = ("pause", "cancel")
"""What `am pause` and `am cancel` record, weakest first (live control C6)."""


def _heartbeat_age(lease: store_module.LeaseRow, now: datetime) -> int:
    """Whole seconds since `lease` last beat, for a refusal message."""
    return int((now - lease.heartbeat_at).total_seconds())


def _controllable_lease(
    conn: sqlite3.Connection, run_id: str, *, command: str, now: datetime
) -> store_module.LeaseRow:
    """The lease a request to `run_id` is addressed to, or C8's refusal.

    The order is C8's: unknown run, not `started`, no live lease, window
    closed. Runs inside `request_control`'s transaction, so a refusal rolls
    back and leaves no row.
    """
    status = store_module.run_status(conn, run_id)
    if status is None:
        raise UnknownRunError(
            f"run {run_id!r} is not in the projection"
            " (`agent-manager runs` lists the ones that are)"
        )
    if status != "started":
        raise NotRunningError(
            f"run {run_id} is {status}, not started, so there is nothing to {command};"
            f" `am status {run_id}` shows it, and `am resume {run_id}` continues a"
            " stopped or escalated run"
        )
    lease = store_module.read_lease(conn, run_id)
    if lease is None:
        raise DeadRunError(
            f"run {run_id} is recorded started but no process holds its lease;"
            f" it is not running, so `am resume {run_id}` picks it up,"
            f" or `am reset {run_id}` closes it"
        )
    if not control.lease_is_live(lease, now=now):
        raise DeadRunError(
            f"run {run_id} is not running: its lease is held by pid {lease.pid}"
            f" on {lease.host}, last heartbeat {_heartbeat_age(lease, now)}s ago;"
            f" `am resume {run_id}` picks it up, or `am reset {run_id}` closes it"
        )
    if not lease.accepting:
        raise NotAcceptingError(
            f"run {run_id} is finishing and no longer accepts pause or cancel;"
            f" `am status {run_id}` shows how it ends"
        )
    return lease


CONTROL_SUBSUMES: dict[str, tuple[str, ...]] = {
    "pause": ("pause", "cancel"),
    "cancel": ("cancel",),
}
"""Requests already recorded that make a new one a no-op (C8). A pause is
covered by any pause or cancel, a cancel only by a cancel, so a cancel after a
pause is recorded and upgrades it."""


def _record_control(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    lease: store_module.LeaseRow,
    command: str,
    now: datetime,
) -> tuple[store_module.ControlRow, bool]:
    """Record `command` for this life of the run; the flag says it was already there.

    Only rows addressed to `lease.token` count, so a request sent to an
    earlier life never makes one to a resumed run a no-op. A no-op returns
    the first row that covers it, whose time is reported as `requested_at`.
    """
    for row in store_module.control_requests(conn, run_id, lease=lease.token):
        if row.command in CONTROL_SUBSUMES[command]:
            return row, True
    row = store_module.add_control(
        conn, run_id, lease=lease.token, command=command, requested_at=now
    )
    return row, False


def _effective_command(rows: Sequence[store_module.ControlRow]) -> str:
    """The strongest command recorded for one life: `cancel` beats `pause` (C6)."""
    return "cancel" if any(row.command == "cancel" for row in rows) else "pause"


def _control_message(run_id: str, command: str, *, effective: str, already: bool) -> str:
    if already:
        return (
            f"{command} was already requested for run {run_id};"
            f" the effective request is {effective}"
        )
    if command == "pause":
        return (
            f"pause requested for run {run_id}; it parks at its next phase"
            f" boundary, and `am resume {run_id}` continues it"
        )
    return (
        f"cancel requested for run {run_id}; it stops at its next phase"
        " boundary and cannot be resumed"
    )


def request_control(
    run_id: str,
    command: str,
    *,
    repo_dir: Path,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Record `am pause` or `am cancel` for the process holding `run_id` (C8).

    One `BEGIN IMMEDIATE` transaction covers the refusals, the idempotence
    check and the insert, so two requesters cannot both insert and a refusal
    leaves no row. The process holding the lease applies the request at its
    next poll; this function only records it. SQLite is the only channel (C1).
    """
    if command not in CONTROL_COMMANDS:
        raise ValueError(
            f"unknown control command {command!r};"
            f" expected one of {', '.join(CONTROL_COMMANDS)}"
        )
    root = resolve_repo_dir(repo_dir)
    now = clock()
    conn = store_module.open_db(root)
    try:
        with store_module.immediate(conn):
            lease = _controllable_lease(conn, run_id, command=command, now=now)
            row, already = _record_control(
                conn, run_id, lease=lease, command=command, now=now
            )
            effective = _effective_command(
                store_module.control_requests(conn, run_id, lease=lease.token)
            )
    finally:
        conn.close()
    return {
        "run_id": run_id,
        "command": command,
        "effective": effective,
        "requested_at": row.requested_at.isoformat(),
        "already_requested": already,
        "message": _control_message(run_id, command, effective=effective, already=already),
    }


def _control(command: str, run_id: str, *, repo_dir: Path, pretty: bool) -> None:
    """`resume`'s envelope pattern for `pause` and `cancel`.

    `clock=_utcnow` reads the module global at call time, so a test that
    freezes `cli._utcnow` freezes this command too.
    """
    try:
        payload = request_control(run_id, command, repo_dir=repo_dir, clock=_utcnow)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))


@app.command("pause")
def pause(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The running run to park."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is written."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Ask a running run to park at its next phase boundary; `am resume` continues it."""
    _control("pause", run_id, repo_dir=repo_dir, pretty=pretty)


@app.command("cancel")
def cancel(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The running run to stop."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is written."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Ask a running run to stop at its next phase boundary and close it for good."""
    _control("cancel", run_id, repo_dir=repo_dir, pretty=pretty)


def _reset_live_error(lease: store_module.LeaseRow, now: datetime) -> RunIsLiveError:
    """`am reset`'s read-only refusal of a run a live process holds (am-reset §3.4).

    `_run_is_live_error`'s pid, host and heartbeat age, but pointing at
    `am cancel`: the run is being driven, and a reset is for one that is not.
    """
    return RunIsLiveError(
        f"run {lease.run_id} is still running in pid {lease.pid} on {lease.host}"
        f" (heartbeat {_heartbeat_age(lease, now)}s ago); `am cancel {lease.run_id}`"
        " stops it, and `am reset` is for a run nobody is driving"
    )


def _not_resettable_error(run_id: str) -> NotResettableError:
    """`am reset`'s refusal of a finished run, worded once for both places it is checked."""
    return NotResettableError(
        f"run {run_id} finished (done), so there is nothing to close;"
        " start new work with `am run`"
    )


def _reset_message(run_id: str, *, already: bool) -> str:
    """What `am reset` tells the operator about `run_id` (am-reset §3.5)."""
    if already:
        return f"run {run_id} was already cancelled; nothing was written"
    return (
        f"run {run_id} is cancelled; `am resume {run_id}` refuses it,"
        " and a relaunch starts its cards from their first phase"
    )


def reset_run(run_id: str, *, repo_dir: Path) -> dict[str, Any]:
    """Close a run nobody is driving by recording it `cancelled` (am-reset §3.2-3.3).

    The run is loaded read-only, exactly as `resume_run` loads it. Refused, in
    order and before `Store.open`: an unknown run (`UnknownRunError`), a run a
    live process holds (`RunIsLiveError`, pointing at `am cancel`), and a
    finished one (`NotResettableError`). Then, as `_resume_from_checkpoint`
    does up to its first write and no further: `Store.open`, the run's own
    lease with no claims (a reset drives no card and no branch), and one
    fenced `record_run` of `cancelled`. No checkpoint row is written or
    deleted, and no other row is touched. `cards` then reports each
    `(card_id, workflow)` the run checkpointed and, by
    `Store.latest_open_checkpoint`, which other run (if any) a relaunch
    would still continue it from (`open_in`). The lease is released and the
    store closed on every exit.

    A run already `cancelled` is not refused: the lease is taken and released
    around the check, and nothing is journalled (`already_cancelled: true`).
    A displaced dead holder is reported under `took_over`.
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
        # Unknown, live, done: read-only and before `Store.open`, which would
        # mint a run directory, so a refusal leaves nothing behind (§3.4).
        lease = store_module.read_lease(conn, run.id)
        now = _utcnow()
        if lease is not None and control.lease_is_live(lease, now=now):
            raise _reset_live_error(lease, now)
        if run.status == "done":
            raise _not_resettable_error(run.id)
    finally:
        conn.close()

    store = Store.open(root, run.id)
    try:
        # `take_lease` re-checks liveness atomically: a live holder that
        # appeared since the check above refuses here as `RunIsLiveError`,
        # and a dead one is taken over. The status is read again under the
        # lease, so two resets serialise (the second sees `cancelled` and
        # writes nothing) and a run that finished meanwhile is never
        # overwritten. No claims: a reset drives no card and no branch.
        with run_lease(store) as lease:
            current = store.load_run(run.id) or run
            if current.status == "done":
                raise _not_resettable_error(run.id)
            already = current.status == "cancelled"
            if not already:
                store.record_run(current.model_copy(update={"status": "cancelled"}))
        # After the status write (or the no-op): each `(card_id, workflow)` the
        # run checkpointed, with the run a relaunch would continue it from by
        # the newest-row rule -- `null` unless that is another run (§3.5).
        cards: list[dict[str, Any]] = []
        for card_id, workflow in store.checkpoint_cards(run.id):
            found = store.latest_open_checkpoint(card_id, workflow)
            cards.append(
                {
                    "card_id": card_id,
                    "workflow": workflow,
                    "open_in": (
                        found.run_id
                        if found is not None and found.run_id != run.id
                        else None
                    ),
                }
            )
    finally:
        store.close()
    payload: dict[str, Any] = {
        "run_id": run.id,
        "previous_status": current.status,
        "status": "cancelled",
        "already_cancelled": already,
        "cards": cards,
        "message": _reset_message(run.id, already=already),
    }
    if lease.displaced is not None:
        # A dead holder's lease was taken over (X5): say whose, as resume does.
        payload["took_over"] = {
            "pid": lease.displaced.pid,
            "host": lease.displaced.host,
            "heartbeat_at": lease.displaced.heartbeat_at.isoformat(),
        }
    return payload


@app.command("reset")
def reset(
    run_id: str = typer.Argument(
        ..., metavar="RUN_ID", help="The run nobody is driving to close."
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is written."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Close a run nobody is driving: record it cancelled under its own lease.

    A running run wants `am cancel` instead; a finished one needs nothing.
    """
    try:
        payload = reset_run(run_id, repo_dir=repo_dir)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
