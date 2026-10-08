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
import math
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
from typing import Any, Literal

import typer
from pydantic import ValidationError

from agent_manager import (
    argv_guard,
    board,
    census,
    comments,
    control,
    dag,
    detach,
    dispatch,
    journal_check,
    locks,
    migrate,
    models,
    orchestrate,
    paths,
    prompt,
)
from agent_manager import __version__
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.stop import StopSignal
from agent_manager.runtime.walk import AgentPhaseRunner, SubtaskSummary
from agent_manager.harness import launcher
from agent_manager.runtime import engine as runtime_engine
from agent_manager.steps import verify as verify_step
from agent_manager.store import backup as store_backup
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import leases as store_leases
from agent_manager.store import projects as store_projects
from agent_manager.store import queries as store_queries
from agent_manager.store import replay as store_replay
from agent_manager.store.writer import Store
from agent_manager.workflow import integrate as integrate_workflow
from agent_manager.workflow import task as task_workflow
from agent_manager.workflow.phases import AgentPhase, Workflow

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
    HarnessOverride,
    NotResumableError,
    RepoDirError,
    BaseBranchError,
    RunnerFactory,
    UnknownRunError,
    unknown_run,
    compute_dry_run_plan,
    continuable_checkpoint,
    gate_context,
    mint_run_id,
    orphan_attempts,
    resolve_base_branch,
    resolve_repo_dir,
    select_resumable,
    with_harness_override,
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
`status` header is these seven names plus `story_id`, which is the run's
`config.story_id` (`None` unless the run is an `am run --story` run) and the
only part of `config` the header shows. Each `runs` entry carries the same
seven, plus `milestone_id`, `card_id`, `story_id`, `lease` and `progress` (a
superset), so the two commands still describe a run's identity the same way."""


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


def _lease_fields(lease: store_leases.LeaseRow, *, now: datetime) -> dict[str, Any]:
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
    lease: store_leases.LeaseRow | None,
    requests: Sequence[store_leases.ControlRow],
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
    conn: sqlite3.Connection,
    run_id: str,
    run: models.Run,
    lease: store_leases.LeaseRow | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """The `integrity` key of `status`: do the run's events agree with `run`?

    Journal/DB divergence spec §3.3, §3.7. Always the three keys `checked`,
    `reason` and `mismatches`, and never an error: events that cannot be
    compared are `checked: false` with the reason why, so `status` keeps its
    exit code. Report-only (§3.4): nothing is written and no control request
    is filed.

    The events are read on `conn` through `store_events.run_lines`; no
    journal file is read and no run directory is created. A run with no node
    event is `"no events"`. The one `try` covers `diverging` as well as
    `run_lines`, because `replay` inside it raises `JournalError` or a
    pydantic `ValidationError` of its own: either is
    `"events unreadable: <error>"`. Mismatches are `store_replay.diverging`'s,
    in its tree-walk order: there is one definition of divergence.

    A live lease (§3.5) is `checked: false, reason: "lease is live"` before any
    event is read: a running process's writes in flight are noise, not
    divergence, even against a hand-edited projection. A dead lease, or none,
    is checked.
    """
    if lease is not None and control.lease_is_live(lease, now=now):
        return {"checked": False, "reason": "lease is live", "mismatches": []}
    try:
        lines = store_events.run_lines(conn, run_id)
        if not lines:
            return {"checked": False, "reason": "no events", "mismatches": []}
        found = store_replay.diverging(lines, run)
    except (store_journal.JournalError, ValidationError) as error:
        return {
            "checked": False,
            "reason": f"events unreadable: {error}",
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
    the key is always present (C12). `warnings` lists the run's recorded
    `isolation_warning`, or is empty (A5).
    """
    tree = run.model_dump()
    return {
        "run": {
            **{field: tree[field] for field in RUN_IDENTITY},
            "story_id": run.config.story_id,
        },
        "stories": tree["stories"],
        "rows": status_rows(run),
        "control": {"lease": None, "requests": [], "claims": []}
        if control is None
        else control,
        "warnings": []
        if run.config.isolation_warning is None
        else [run.config.isolation_warning],
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
    checkpoint: store_checkpoints.Checkpoint | None, *, card_id: str, run_id: str
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


def entry() -> None:
    """The `am` console script: `argv_guard` first, then the app.

    `argv_guard.reexec_neutral` replaces the process when `run` or `resume`
    has a `--verify`; when it returns, the app runs with the warnings it
    returned (none, or `ARGV_VISIBLE_WARNING`) as `ctx.obj["argv_warnings"]`.
    """
    warning = argv_guard.reexec_neutral(sys.argv)
    app(obj={"argv_warnings": [] if warning is None else [warning]})


def add_argv_warnings(ctx: typer.Context, payload: dict[str, Any]) -> None:
    """Append `entry`'s argv warnings to an ok payload's `warnings`, creating the list.

    No warnings, or no `ctx.obj` (a `CliRunner` that passes none), leaves the
    payload as it is.
    """
    warnings = (ctx.obj or {}).get("argv_warnings", [])
    if warnings:
        payload.setdefault("warnings", []).extend(warnings)


WORKFLOW_NAME = "task"
"""The only document `run --card` drives. `--workflow` is §10's, not this card's."""


def default_runner_factory(
    *,
    store: Store,
    run_id: str,
    story_id: str,
    card_id: str,
    harness_timeout: float | None = None,
    harness_timeouts: Mapping[str, float] | None = None,
) -> AgentPhaseRunner:
    """The production runner: real adapters, real roles, the run's recorded launcher.

    The launcher mode is the run's `RunConfig.launcher`, read from the
    projection on every call (A5 B2), so subtask phases, both conflict
    resolvers, a detached child and a resumed run all launch the way the run
    was recorded. It never probes: that happened at run or resume start. A
    run with no row is `UnknownRunError`, never a silent `direct`.
    `launcher.get_launcher` is read at call time so a test can patch it.

    `adapters` and `result_models` keep `AgentRunner`'s own defaults and
    `harness_map` stays empty, so every role falls back to `DEFAULT_HARNESS` and
    to the model its own `policy.toml` names (D6). Choosing a harness per role is
    `--harness`'s job, and `--harness` is not this card's.

    `harness_timeout` and `harness_timeouts` are the run's recorded
    `RunConfig` values (card eee43099); `runner_factory_for` is what passes
    them. Without them every attempt gets `dispatch.DEFAULT_TIMEOUT`.
    """
    config = store_queries.run_config(store.connection, run_id)
    if config is None:
        raise UnknownRunError(
            f"run {run_id!r} has no row in the projection, so the launcher it was"
            " recorded with is unknown; no agent is launched for it"
        )
    return dispatch.AgentRunner(
        store=store,
        launcher=launcher.get_launcher(config.launcher),
        run_id=run_id,
        story_id=story_id,
        card_id=card_id,
        harness_timeout=harness_timeout,
        harness_timeouts=dict(harness_timeouts or {}),
        max_limit_wait_hours=config.max_limit_wait_hours,
    )


def runner_factory_for(
    config: models.RunConfig, runner_factory: RunnerFactory | None
) -> RunnerFactory | None:
    """The runner factory a run's engine hands on, given its recorded `config` (card eee43099).

    An injected `runner_factory` (the §14 test seam) wins and is returned as
    is, so it is still called with the four `RunnerFactory` keywords only. A
    config with no timeout gives `None`, exactly what was handed on before
    timeouts were recorded. Otherwise a factory that calls
    `default_runner_factory` -- looked up at call time, so a test that patches
    it is honored -- with the config's two values added.
    """
    if runner_factory is not None:
        return runner_factory
    if config.harness_timeout is None and not config.harness_timeouts:
        return None
    harness_timeout = config.harness_timeout
    harness_timeouts = dict(config.harness_timeouts)

    def bound(*, store: Store, run_id: str, story_id: str, card_id: str) -> AgentPhaseRunner:
        return default_runner_factory(
            store=store,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            harness_timeout=harness_timeout,
            harness_timeouts=harness_timeouts,
        )

    return bound


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
    resume_from: store_checkpoints.Checkpoint | None = None,
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
    resume_from: store_checkpoints.Checkpoint | None = None,
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
        return models.CANCELED
    return summary.status


def _run_is_live_error(lease: store_leases.LeaseRow, now: datetime) -> RunIsLiveError:
    """C10's refusal of a run another live process holds, worded once for every caller."""
    return RunIsLiveError(
        f"run {lease.run_id} is still running in pid {lease.pid} on {lease.host}"
        f" (heartbeat {_heartbeat_age(lease, now)}s ago); wait for it to exit,"
        f" or `am status {lease.run_id}`"
    )


def _claimed_error(key: str, holder: store_leases.LeaseRow, now: datetime) -> ClaimedError:
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

    Read-only preflight (X5): one `open_db` connection, the project looked up
    (never created) with `store_projects.lookup`, `claim_conflicts` judged by
    `control.lease_is_live` at `_utcnow()` within that project, closed on
    every path. A project with no row has no claim to conflict with. It takes
    no lease, claim or lock, so a refusal here leaves no run directory.
    `run_id` excludes that run's own rows (a resume). The first live conflict
    raises `ClaimedError`; `take_lease` re-checks atomically.
    """
    now = _utcnow()
    conn = store_db.open_db(root)
    try:
        project_id = store_projects.lookup(conn, root)
        conflicts = (
            []
            if project_id is None
            else store_leases.claim_conflicts(
                conn,
                keys,
                project_id=project_id,
                is_live=lambda row: control.lease_is_live(row, now=now),
                run_id=run_id,
            )
        )
    finally:
        conn.close()
    if conflicts:
        key, holder = conflicts[0]
        raise _claimed_error(key, holder, now)


@contextmanager
def run_lease(store: Store, *, claims: Sequence[str] = ()) -> Iterator[control.Lease]:
    """Hold `control.Lease(store, claims=claims)` for the block, with CLI refusals.

    A thin wrapper: only entering is translated -- `store_leases.LeaseHeldError`
    becomes C10's `RunIsLiveError`, `store_leases.ClaimHeldError` becomes
    `ClaimedError` -- and the block's exit, an exception included, is
    `Lease.__exit__`'s, which releases the claims then the lease.
    """
    stack = ExitStack()
    try:
        lease = stack.enter_context(control.Lease(store, claims=claims))
    except store_leases.LeaseHeldError as error:
        raise _run_is_live_error(error.holder, _utcnow()) from error
    except store_leases.ClaimHeldError as error:
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
    """The one board comment a `run --card` walk leaves on its card, or None.

    Chosen by `summary.status`, so a cancel that met an escalation comments
    the escalation: `done` is the done comment, `escalated` the escalation
    keyed by this life's lease `token`, and `stopped` under a cancel the
    cancel comment with the `am run --card` relaunch. A stop under a pause
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
        return comments.compose_canceled(
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
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    launcher: models.Launcher | None = None,
    isolation_warning: str | None = None,
    clock: Callable[[], datetime] = _utcnow,
    harness_timeout: float | None = None,
    harness_timeouts: Mapping[str, float] | None = None,
) -> CardPreflight:
    """Stage 1 of `run --card`: every board read and refusal, then the run id (card 5daa944e).

    In today's order: the card, `ParentlessCardError`, its parent, the branch
    and worktree, then `refuse_claimed` over the `card:<id>` claim, read-only
    and before any store exists, so a refused card leaves no run directory
    (X5). Only then is the clock read and the run id minted, and the
    `started` run, story and subtask records built; the run's config records
    `commands` as its `verify` suite, `allow_no_verification`, and the resolved
    `launcher` (`None` recording `direct`) with its `isolation_warning`. Nothing
    is written.
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
            config=models.RunConfig(
                verify=list(commands),
                allow_no_verification=allow_no_verification,
                launcher="direct" if launcher is None else launcher,
                isolation_warning=isolation_warning,
                harness_timeout=harness_timeout,
                harness_timeouts=dict(harness_timeouts or {}),
            ),
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
    The walk's runner gets the run's recorded harness timeouts
    (`runner_factory_for`, card eee43099).
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
            runner_factory=runner_factory_for(pre.run_record.config, runner_factory),
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
    launcher: models.Launcher | None = None,
    isolation_warning: str | None = None,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
    harness_timeout: float | None = None,
    harness_timeouts: Mapping[str, float] | None = None,
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
    the run `canceled` (`card_run_status`). No control cancels a running phase.
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
        commands=commands,
        allow_no_verification=allow_no_verification,
        launcher=launcher,
        isolation_warning=isolation_warning,
        clock=clock,
        harness_timeout=harness_timeout,
        harness_timeouts=harness_timeouts,
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
    store_leases.LeaseLostError,
    store_journal.CorruptJournalError,
    store_db.StoreSchemaError,
    store_db.MigrationRequiredError,
    store_db.StoreBusyError,
    migrate.MigrationRefusedError,
    store_backup.BackupRefusedError,
)
"""Everything the command turns into an `ok: false` envelope and exit 3.

`ValueError` is in the list for one concrete reason: `dag.short_id` raises a
bare one for a card id that is not a UUID, and a typed `--card` must not come
back as a traceback. `locks.LockTimeoutError` is in it because a start refused
while another `am` process held a project lock past its timeout (spec X7) is a
refusal, not a bug; nothing below the CLI catches it. `store_leases.LeaseLostError`
is in it because another process took this run's lease over mid-walk (spec X4):
the fence stopped every write, and the operator gets the envelope naming the new
holder. It is a `BaseException`, so it has to be listed by name.
`store_journal.CorruptJournalError` is in it because a crashed run can leave a
torn line in its journal, and `Store.open` reading it (`am reset`, `am resume`)
is a refusal naming the file and line, not a bug; only that subclass, not
`JournalError` as a whole. `store_db.StoreSchemaError` is in it because an
`am.db` written by a newer `am` is a refusal naming the file and both versions,
not a bug. `store_db.MigrationRequiredError` is in it because every command
that opens the projection refuses, naming `am migrate`, on a machine whose
per-project databases have not been migrated; it is raised before anything is
written. `store_db.StoreBusyError` is in it because a write that stayed busy or
locked through `store_db.run_with_retry`'s whole budget is a refusal naming the
operation and the budget, not a bug: the lease goes stale and the run is
resumable. `migrate.MigrationRefusedError` is in it because a merge that cannot
be done safely is a refusal naming the reason and the files or runs, raised
before anything is committed, not a bug. `store_backup.BackupRefusedError` is
in it because a backup with no `am.db` to copy, a target that already exists or
a target directory that does not is a refusal naming the reason and the path,
with nothing written, not a bug. Anything outside this tuple is a bug in this
program and should crash loudly with its stack intact.
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
    launcher: models.Launcher | None = None,
    isolation_warning: str | None = None,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
    harness_timeout: float | None = None,
    harness_timeouts: Mapping[str, float] | None = None,
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
        commands=commands,
        allow_no_verification=allow_no_verification,
        launcher=launcher,
        isolation_warning=isolation_warning,
        clock=clock,
        harness_timeout=harness_timeout,
        harness_timeouts=harness_timeouts,
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


def verify_commands(verify: Sequence[str], *, from_env: bool) -> list[str]:
    """The verification commands `run` and `resume` drive, from `--verify` or `AM_VERIFY_JSON`.

    `AM_VERIFY_JSON` is popped from `os.environ` on every call, with or
    without `from_env`, so no process the run spawns inherits it; its value
    is read only under `--verify-from-env`, the flag `argv_guard` puts in
    place of every `--verify`. Without the flag the commands are `verify`.
    With it they are the variable's JSON list of strings, the empty list
    included. The flag with any `--verify`, with the variable unset, or with
    a value that is not a JSON list of strings is a usage error (exit 2)
    whose message never echoes the value.
    """
    raw = os.environ.pop(argv_guard.VERIFY_ENV, None)
    if not from_env:
        return list(verify)
    if verify:
        raise typer.BadParameter(
            "--verify and --verify-from-env are exclusive",
            param_hint=argv_guard.FROM_ENV_FLAG,
        )
    if raw is None:
        raise typer.BadParameter(
            f"--verify-from-env needs {argv_guard.VERIFY_ENV}, and it is unset",
            param_hint=argv_guard.FROM_ENV_FLAG,
        )
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise typer.BadParameter(
            f"{argv_guard.VERIFY_ENV} is not a JSON list of strings",
            param_hint=argv_guard.FROM_ENV_FLAG,
        )
    return value


def agent_phase_names(*workflows: Workflow) -> tuple[str, ...]:
    """The `AgentPhase` names of `workflows`, in declared order, each once."""
    return tuple(
        dict.fromkeys(
            phase.name
            for workflow in workflows
            for phase in workflow.phases
            if isinstance(phase, AgentPhase)
        )
    )


TASK_AGENT_PHASES = agent_phase_names(task_workflow.TASK)
"""The phases `--harness-timeout PHASE=` may name on `run --card` and `run --story`,
which dispatch only `task` phases."""

MILESTONE_AGENT_PHASES = agent_phase_names(task_workflow.TASK, integrate_workflow.INTEGRATE)
"""The phases `--harness-timeout PHASE=` may name on `run --milestone`, `run --board`
and `resume`: a milestone run ends in Integrate."""

HARNESS_TIMEOUT_MIN = 60
"""The smallest `--harness-timeout` in seconds. The CLI's bound, not the model's."""

HARNESS_TIMEOUT_MAX = 86400
"""The largest `--harness-timeout` in seconds."""


def _harness_timeout_error(message: str) -> typer.BadParameter:
    return typer.BadParameter(message, param_hint="'--harness-timeout'")


def _harness_seconds(text: str, given: str) -> float:
    """`text` as seconds in bounds, or the usage error naming `given`, the whole value."""
    if not text:
        raise _harness_timeout_error(f"{given!r} has an empty value; expected [PHASE=]SECONDS")
    try:
        seconds = float(text)
    except ValueError:
        raise _harness_timeout_error(f"{given!r}: {text!r} is not a number of seconds") from None
    if not math.isfinite(seconds):
        raise _harness_timeout_error(f"{given!r}: {text!r} is not a finite number of seconds")
    if not HARNESS_TIMEOUT_MIN <= seconds <= HARNESS_TIMEOUT_MAX:
        raise _harness_timeout_error(
            f"{given!r}: seconds must be from {HARNESS_TIMEOUT_MIN} to"
            f" {HARNESS_TIMEOUT_MAX} inclusive"
        )
    return seconds


def parse_harness_timeouts(
    values: Sequence[str], *, phases: Sequence[str]
) -> tuple[float | None, dict[str, float]]:
    """Every `--harness-timeout` value, parsed to `(run default, per-phase map)` (card 33dc5549).

    A bare `SECONDS` is the run default. `PHASE=SECONDS` is split on the
    first `=`, and `PHASE` must be one of `phases`, matched exactly. Each
    `SECONDS` is a finite `float()` from `HARNESS_TIMEOUT_MIN` to
    `HARNESS_TIMEOUT_MAX` inclusive. The run default or one phase given twice
    is refused rather than last-wins, so a typo cannot hide. Every refusal is
    `typer.BadParameter`, Typer's exit 2. Order does not matter, and no
    values give `(None, {})`.
    """
    default: float | None = None
    per_phase: dict[str, float] = {}
    for value in values:
        name, separator, text = value.partition("=")
        if not separator:
            seconds = _harness_seconds(value, value)
            if default is not None:
                raise _harness_timeout_error(
                    "the run default is given twice; give one bare SECONDS"
                )
            default = seconds
            continue
        if not name:
            raise _harness_timeout_error(
                f"{value!r} has an empty phase name; expected PHASE=SECONDS"
            )
        if name not in phases:
            raise _harness_timeout_error(
                f"{value!r}: unknown phase {name!r}; the agent phases are: {', '.join(phases)}"
            )
        seconds = _harness_seconds(text, value)
        if name in per_phase:
            raise _harness_timeout_error(f"phase {name!r} is given twice")
        per_phase[name] = seconds
    return default, per_phase


def run_agent_phases(run: models.Run) -> tuple[str, ...]:
    """The agent phases a recorded `run` can dispatch (card eee43099).

    A `task` run and a story run (`config.story_id` set) never reach
    Integrate, so only the `task` phases; a milestone run also `resolve`.
    """
    if run.workflow == WORKFLOW_NAME or run.config.story_id is not None:
        return TASK_AGENT_PHASES
    return MILESTONE_AGENT_PHASES


def refuse_undispatchable_harness_phases(
    run: models.Run, per_phase: Mapping[str, float]
) -> None:
    """`--harness-timeout`'s usage error for a phase `run` cannot dispatch.

    `resume` validates against `MILESTONE_AGENT_PHASES` before anything is
    loaded; this is the second check, once the run's shape is known.
    """
    phases = run_agent_phases(run)
    for name in per_phase:
        if name not in phases:
            raise _harness_timeout_error(
                f"run {run.id} cannot dispatch phase {name!r}; its agent phases are:"
                f" {', '.join(phases)}"
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
  am run --story "Story 3.1" --branch-prefix m9 --verify "uv run pytest"  # one story, no Integrate
  am run --board --verify "uv run pytest"                             # run every open milestone
  am run --board --verify "uv run pytest" --detach                    # ... in the background
  am status <run-id> --pretty                                         # watch it (another terminal)
  am resume <run-id> --verify "uv run pytest"                         # after a fix, stop or crash
"""


@app.command("run", epilog=RUN_EXAMPLES)
def run(
    ctx: typer.Context,
    card: str | None = typer.Option(
        None,
        "--card",
        help="The subtask card id to drive. Exclusive with --milestone, --story and --board.",
    ),
    milestone: str | None = typer.Option(
        None,
        "--milestone",
        help=(
            "A milestone card id or title substring: drive every remaining subtask. "
            "Exclusive with --card, --story and --board."
        ),
    ),
    story: str | None = typer.Option(
        None,
        "--story",
        help=(
            "A story card id or title piece: drive every remaining subtask of that one "
            "story, on its own stack, with no Integrate. Exclusive with --card, "
            "--milestone and --board."
        ),
    ),
    whole_board: bool = typer.Option(
        False,
        "--board",
        help=(
            "Drive every open milestone on the board as one dependency graph: a "
            "milestone starts once every milestone blocking it finished done. "
            "Exclusive with --card, --milestone and --story."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "With --milestone: show the plan (story order, each subtask's branch "
            "and base, merged bases) and write nothing. With --story: show that "
            "one story's plan, with no Integrate, and write nothing. With --board: "
            "show every open milestone by level, each with its own plan, and write "
            "nothing."
        ),
    ),
    detach_run: bool = typer.Option(
        False,
        "--detach",
        help=(
            "With --card, --milestone, --story or --board: make every check here "
            "(and, for --card, --milestone or --story, record and lease the run), "
            "then hand the run to a background process in its own session and print "
            "its pid and log. A card, milestone or story run's output goes to "
            "<data dir>/runs/<run-id>/run.log and its final envelope to "
            "report.json; a board's output goes to "
            "<data dir>/boards/<stamp>-<digest>.log and its final envelope to "
            "<stamp>-<digest>.report.json."
        ),
    ),
    max_limit_wait: float = typer.Option(
        models.DEFAULT_MAX_LIMIT_WAIT_HOURS,
        "--max-limit-wait",
        min=0,
        help=(
            "Hours a phase may wait for a harness usage limit to reset before it "
            "escalates; a wait does not use up an attempt. The default 0 never "
            "waits: a limit hit escalates at once, naming the reset time. "
            "Recorded in the run and kept on resume."
        ),
    ),
    max_concurrent: int | None = typer.Option(
        None,
        "--max-concurrent",
        help=(
            "With --milestone: how many stories run at once "
            f"(default {DEFAULT_MAX_CONCURRENT}). A story starts as soon as its "
            "blockers finish. With --board: how many stories run at once across "
            "the whole board. Not with --card or --story."
        ),
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    base_branch: str | None = typer.Option(
        None,
        "--base-branch",
        help=(
            "Where unblocked stories start (and --card's branch is cut from). "
            "Must be an existing branch. Default: the repository's default "
            "branch (origin/HEAD, else the checked-out branch, else `master`). "
            "Never modified."
        ),
    ),
    branch_prefix: str | None = typer.Option(
        None,
        "--branch-prefix",
        help=(
            "Milestone prefix for the derived branch name, e.g. `m2`. Required with "
            "--card, --milestone and --story. Optional with --board: each milestone's "
            "prefix is its own card stem, or `<prefix>-<stem>` when given."
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
    verify_from_env: bool = typer.Option(
        False, argv_guard.FROM_ENV_FLAG, hidden=True
    ),
    isolation: launcher.IsolationRequest = typer.Option(
        "auto",
        "--isolation",
        help=(
            "Run every agent in a PID namespace of its own, so it cannot signal "
            "the engine: `bwrap`, `unshare`, `auto` (bwrap, then unshare, else "
            "none with a warning) or `none`. A named mode this host cannot start "
            "is refused before anything is written. Ignored with --dry-run."
        ),
    ),
    harness_timeout_values: list[str] = typer.Option(
        [],
        "--harness-timeout",
        metavar="[PHASE=]SECONDS",
        help=(
            "The harness timeout in seconds (60 to 86400), repeatable: a bare value is the "
            "run's default, PHASE=SECONDS overrides one agent phase. Recorded with the run. "
            "Ignored with --dry-run."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Drive one subtask card, one story (--story, no Integrate), a whole milestone, or every open milestone (--board) end to end, or preview a story, a milestone or the board with --dry-run."""
    commands = verify_commands(verify, from_env=verify_from_env)
    _check_run_targets(
        card=card,
        milestone=milestone,
        dry_run=dry_run,
        max_concurrent=max_concurrent,
        board=whole_board,
        branch_prefix=branch_prefix,
        detach=detach_run,
        story=story,
    )
    # After the target checks and before the `HANDLED` block (card 33dc5549):
    # a bad value reads no board, opens no store and creates no run directory.
    # A card or story run dispatches only `task` phases; a milestone or board
    # run ends in Integrate.
    default_timeout, phase_timeouts = parse_harness_timeouts(
        harness_timeout_values,
        phases=(
            TASK_AGENT_PHASES
            if card is not None or story is not None
            else MILESTONE_AGENT_PHASES
        ),
    )
    # Passed only when given, so a run without the flag calls exactly as before.
    # The dry-run previews never receive them.
    timeouts: dict[str, Any] = (
        {"harness_timeout": default_timeout, "harness_timeouts": phase_timeouts}
        if harness_timeout_values
        else {}
    )
    lanes = DEFAULT_MAX_CONCURRENT if max_concurrent is None else max_concurrent
    models.max_limit_wait_default.set(max_limit_wait)
    isolation_warning: str | None = None
    try:
        # A5 B1: first after the argument checks, before any board read,
        # `refresh_git`, store, lease or fork, so a refused mode writes
        # nothing. A dry run launches nothing and probes nothing. Read as
        # `launcher.resolve_isolation` so a test can patch it.
        mode: models.Launcher | None = None
        if not dry_run:
            resolved = launcher.resolve_isolation(isolation)
            mode, isolation_warning = resolved.mode, resolved.warning
        # Before any run row, claim or worktree exists, and for --dry-run too.
        base_branch = resolve_base_branch(repo_dir, base_branch)
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
                commands=commands,
                allow_no_verification=allow_no_verification,
                launcher=mode,
                isolation_warning=isolation_warning,
                max_concurrent=lanes,
                detacher=detach.fork_detacher,
                **timeouts,
            )
        elif whole_board:
            # Read as `orchestrate.run_board` so a test can patch it there.
            # No runner_factory and no driver: production gets the defaults.
            # `lanes` is the board-wide bound on stories running at once.
            payload = orchestrate.run_board(
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix_of=board_prefix_of(branch_prefix),
                commands=commands,
                allow_no_verification=allow_no_verification,
                launcher=mode,
                isolation_warning=isolation_warning,
                max_concurrent=lanes,
                **timeouts,
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
                commands=commands,
                allow_no_verification=allow_no_verification,
                launcher=mode,
                isolation_warning=isolation_warning,
                max_concurrent=lanes,
                detacher=detach.fork_detacher,
                **timeouts,
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
                commands=commands,
                allow_no_verification=allow_no_verification,
                launcher=mode,
                isolation_warning=isolation_warning,
                max_concurrent=lanes,
                **timeouts,
            )
        elif story is not None and dry_run:
            payload = dry_run_story(
                story,
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
            )
        elif story is not None and detach_run:
            # Read as `orchestrate.detach_story` and `detach.fork_detacher`
            # so a test can patch either.
            payload = orchestrate.detach_story(
                story,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=commands,
                allow_no_verification=allow_no_verification,
                launcher=mode,
                isolation_warning=isolation_warning,
                detacher=detach.fork_detacher,
                **timeouts,
            )
        elif story is not None:
            # Read as `orchestrate.run_story` so a test can patch it there.
            # No runner_factory and no driver: production gets the defaults.
            payload = orchestrate.run_story(
                story,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=commands,
                allow_no_verification=allow_no_verification,
                launcher=mode,
                isolation_warning=isolation_warning,
                **timeouts,
            )
        elif detach_run:
            # Read as `detach.fork_detacher` so a test can patch it there.
            payload = detach_card(
                card,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                allow_no_verification=allow_no_verification,
                launcher=mode,
                isolation_warning=isolation_warning,
                commands=commands,
                detacher=detach.fork_detacher,
                **timeouts,
            )
        else:
            payload = run_card(
                card,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                allow_no_verification=allow_no_verification,
                launcher=mode,
                isolation_warning=isolation_warning,
                commands=commands,
                **timeouts,
            )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    if isolation_warning is not None:
        # Once, at the top level (a board's milestone entries never carry
        # it), before `argv_guard`'s warning.
        payload.setdefault("warnings", []).append(isolation_warning)
    add_argv_warnings(ctx, payload)
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    if detach_run:
        # A handed-off run's outcome is in its report.json, not this exit code.
        return
    # A board payload carries one entry per milestone under `milestones`, each
    # with a `status`; a board dry-run carries no `milestones` key at all, so
    # it is read with `.get` and an empty default. A card payload reports
    # `status`. A milestone or story payload has no `status` key: it carries
    # `escalated: true` only when it stopped, a clean one carries `done: true`,
    # a paused or cancelled one carries that flag, and a dry-run preview
    # carries none of them, so it is read with `.get`, never indexed. Every
    # check is strict equality on purpose: a `stopped` card (addendum P4), or a
    # stopped, cancelled or blocked milestone or story, is not an escalation
    # and exits 0.
    if whole_board:
        escalated = any(
            entry.get("status") == "escalated" for entry in payload.get("milestones", [])
        )
    elif card is not None:
        escalated = payload["status"] == "escalated"
    else:
        escalated = payload.get("escalated") is True
    if escalated:
        raise typer.Exit(EXIT_ESCALATED)


def _project_run(conn: sqlite3.Connection, root: Path, run_id: str) -> models.Run | None:
    """`store_queries.load_run`, or `None` unless `run_id` is a run of `root`'s project.

    The projection holds every repository's runs, so a run another project
    recorded reads as absent: a command scoped to `--repo-dir` neither sees
    nor acts on another repository's run. A repository with no `projects`
    row (looked up, never created) knows no run.
    """
    run = store_queries.load_run(conn, run_id)
    if run is None:
        return None
    if store_queries.run_project_id(conn, run_id) != store_projects.lookup(conn, root):
        return None
    return run


def status_for(run_id: str | None, *, repo_dir: Path | None) -> dict[str, Any]:
    """The §9 tree and §10 table of one run, of this project or, by id, of any.

    `repo_dir` given: it is resolved (`RepoDirError` if it is no directory)
    and the run must be one of its project's, through `_project_run`; with no
    `run_id`, the default run is its project's most recent. `repo_dir` `None`
    with no `run_id` means `Path(".")`, so the command and direct callers
    agree. `repo_dir` `None` with a `run_id` is the machine-wide lookup: run
    ids are machine-unique, so `store_queries.load_run` finds the run
    whichever project recorded it, even one whose `projects` row is missing.
    No directory is resolved then -- the current directory plays no part --
    and `open_db_for_reading` gets `Path(".")` only because it takes a root,
    which never chooses the file. An id it does not hold is an
    `UnknownRunError` naming no repository.

    Read-only: no `record_*` is called, and the connection is closed on every
    path including the refusals, the way `run_card` closes its store. The default
    run id comes from `store_queries.latest_run_id`, which is the head of the very
    listing `runs` prints, so the two commands cannot disagree about which run is
    the most recent one. The lease and every control request are read on the
    same connection and rendered by `control_view`, still without a write.
    The `integrity` key compares the run's events with the loaded tree through
    `integrity_view`, on the same connection, writing nothing either.

    Every statement runs in one `store_db.read_snapshot`, and the first one
    reads `store_events.head`: that is `as_of_seq`, and the payload reflects
    every event up to it and none after it. Lease liveness is still judged at
    read time, against `now`. `store_id` is `store_db.store_id`, read in the
    same snapshot and never minted here: it names the database read, so a
    consumer seeing a different one drops any `as_of_seq` it holds.
    """
    machine_wide = run_id is not None and repo_dir is None
    root = Path(".") if machine_wide else resolve_repo_dir(repo_dir or Path("."))
    conn = store_db.open_db_for_reading(root)
    try:
        with store_db.read_snapshot(conn):
            as_of_seq = store_events.head(conn)
            store_id = store_db.store_id(conn)
            wanted = run_id
            if wanted is None:
                wanted = store_queries.latest_run_id(
                    conn, project_id=store_projects.lookup(conn, root)
                )
                if wanted is None:
                    raise UnknownRunError(
                        f"no run has been recorded for {root}, so there is no most recent"
                        " run to report on; pass a run id or start one with `run --card`"
                    )
            if machine_wide:
                run = store_queries.load_run(conn, wanted)
                if run is None:
                    raise UnknownRunError(
                        f"run {wanted!r} is not in the projection"
                        " (`agent-manager runs --all-projects` lists the ones that are)"
                    )
            else:
                run = _project_run(conn, root, wanted)
                if run is None:
                    raise UnknownRunError(
                        f"run {wanted!r} is not in the projection for {root}"
                        " (`agent-manager runs` lists the ones that are)"
                    )
            lease = store_leases.read_lease(conn, wanted)
            now = _utcnow()
            # Only a live lease's claims count (X5): a dead one's leftover rows
            # are anyone's to take, so they are not shown as held.
            claims = (
                [claim.key for claim in store_leases.held_claims(conn, wanted, lease.token)]
                if lease is not None and control.lease_is_live(lease, now=now)
                else []
            )
            state = control_view(
                lease,
                store_leases.control_requests(conn, wanted),
                now=now,
                claims=claims,
            )
            payload = status_payload(run, state)
            payload["integrity"] = integrity_view(conn, wanted, run, lease, now=now)
            payload["as_of_seq"] = as_of_seq
            payload["store_id"] = store_id
            return payload
    finally:
        conn.close()


@app.command("status")
def status(
    run_id: str | None = typer.Argument(
        None, metavar="[RUN_ID]", help="The run to report on. Defaults to the most recent."
    ),
    repo_dir: Path | None = typer.Option(
        None,
        "--repo-dir",
        help="The repository whose projection is read (default: the current"
        " directory). Without it, a RUN_ID is looked up across every project.",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Report one run as story / subtask / phase / attempt / state.

    With RUN_ID and no --repo-dir the run is found by id alone, whichever
    repository recorded it; with --repo-dir it must be that repository's.
    With no RUN_ID it is the most recent run of --repo-dir (default `.`).
    """
    try:
        payload = status_for(run_id, repo_dir=repo_dir)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))


def _runs_before(
    conn: sqlite3.Connection,
    before: str,
    *,
    scope: int | None | store_queries.AllProjects,
    root: Path,
) -> store_queries.RunCursor | datetime:
    """`am runs --before X` as `store_queries.list_runs`' `before`.

    X is looked up as a run id first: a run in `scope` is its `RunCursor`
    (`ALL_PROJECTS` takes any run). A run recorded for another project, when
    the listing is scoped to `root`'s, is an `UnknownRunError` -- the same
    refusal `status` gives a run of another repository -- not a position in
    this listing. A value that is no run id is parsed with
    `datetime.fromisoformat` (a date alone is midnight); `list_runs` reads a
    naive value as UTC. Neither is an `UnknownRunError`.
    """
    found = store_queries.run_cursor(conn, before)
    if found is not None:
        cursor, project_id = found
        if isinstance(scope, store_queries.AllProjects) or project_id == scope:
            return cursor
        raise UnknownRunError(
            f"run {before!r} is not in the projection for {root}"
            " (`agent-manager runs` lists the ones that are)"
        )
    try:
        return datetime.fromisoformat(before)
    except ValueError:
        raise UnknownRunError(
            f"--before {before!r} is neither a run id nor an ISO 8601 timestamp"
            " (pass the last run id of the previous page)"
        ) from None


def runs_for(
    *,
    repo_dir: Path,
    all_projects: bool = False,
    limit: int | None = None,
    before: str | None = None,
) -> dict[str, Any]:
    """This project's run history, or every project's, newest first, each run
    with its lease and progress.

    An empty history is an empty list, not a refusal: a project that has never
    been run is a fact. `model_dump()` keeps the `Path` and `datetime` objects
    for `render`'s `default=str`, exactly as `status_payload` does, so a run
    looks the same in both commands.

    `all_projects` lists every project's runs in one order. `--repo-dir` is
    then ignored: never resolved, so a directory that does not exist is no
    `RepoDirError`, and handed to `open_db_for_reading` only because that
    takes a root, which never chooses the file. `limit` keeps the first
    `limit` rows; `before` starts after it (see `_runs_before`). `--limit`
    below 1, and `--before` without `--limit`, are `CliError`s raised before
    the database is opened.

    `lease` is filled here, not in `store_queries.list_runs`: `live` needs `control`,
    which `store` must not import. Each run's `run_leases` row is read on the
    same connection and shaped by `_lease_fields`, the helper `control_view`
    uses, so it is `am status`'s `control.lease` minus `acquired_at`, or
    `None` when the run has no lease row. One `now` judges the whole listing.

    `progress` and `project` arrive already filled by `store_queries.list_runs`;
    `model_copy` keeps them and `model_dump` carries them into the entry
    unchanged.

    Every statement runs in one `store_db.read_snapshot`, and the first one
    reads `store_events.head`: that is `as_of_seq`, the machine-wide head (so
    it can be above 0 on an empty listing), and the listing -- the `--before`
    lookup included -- reflects every event up to it and none after it.
    `store_id` is `store_db.store_id`, read in the same snapshot and never
    minted here, so it is `None` when no `am.db` exists yet or its `meta` has
    no `store_id` row.
    """
    if limit is not None and limit < 1:
        raise CliError(f"--limit must be at least 1, got {limit}")
    if before is not None and limit is None:
        raise CliError("--before requires --limit")
    root = repo_dir if all_projects else resolve_repo_dir(repo_dir)
    conn = store_db.open_db_for_reading(root)
    try:
        with store_db.read_snapshot(conn):
            as_of_seq = store_events.head(conn)
            store_id = store_db.store_id(conn)
            scope = (
                store_queries.ALL_PROJECTS
                if all_projects
                else store_projects.lookup(conn, root)
            )
            start = (
                None
                if before is None
                else _runs_before(conn, before, scope=scope, root=root)
            )
            now = _utcnow()
            entries = []
            for summary in store_queries.list_runs(
                conn, project_id=scope, limit=limit, before=start
            ):
                lease = store_leases.read_lease(conn, summary.id)
                shown = (
                    None
                    if lease is None
                    else store_queries.RunLease(**_lease_fields(lease, now=now))
                )
                entries.append(summary.model_copy(update={"lease": shown}).model_dump())
            return {"runs": entries, "as_of_seq": as_of_seq, "store_id": store_id}
    finally:
        conn.close()


@app.command("runs")
def runs(
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    all_projects: bool = typer.Option(
        False, "--all-projects", help="List every project's runs; --repo-dir is ignored."
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="List at most this many runs (at least 1)."
    ),
    before: str | None = typer.Option(
        None,
        "--before",
        help="Start the page after this run: pass the last run id of the previous"
        " page. An ISO 8601 timestamp lists runs started strictly before it."
        " Needs --limit.",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """List this project's run history, or every project's, newest first."""
    try:
        payload = runs_for(
            repo_dir=repo_dir, all_projects=all_projects, limit=limit, before=before
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))


def _event_line(row: store_events.EventRow) -> dict[str, Any]:
    """One `am events` line: `row`'s `store_events.journal_line` dumped in
    JSON mode plus `gseq`, the row's global `seq`. Its own `seq` stays the
    per-run number."""
    return {**store_events.journal_line(row).model_dump(mode="json"), "gseq": row.seq}


def _check_event_values(
    *,
    after_seq: int | None = None,
    limit: int | None = None,
    tail: int | None = None,
    before_seq: int | None = None,
) -> None:
    """`am events`' value checks, as `CliError`s, the first failing one in
    this order: `limit` below 1, `after_seq` below 0, `tail` below 1,
    `before_seq` below 1. `None` means "not given" and is never refused."""
    if limit is not None and limit < 1:
        raise CliError(f"--limit must be at least 1, got {limit}")
    if after_seq is not None and after_seq < 0:
        raise CliError(f"--after-seq must be 0 or more, got {after_seq}")
    if tail is not None and tail < 1:
        raise CliError(f"--tail must be at least 1, got {tail}")
    if before_seq is not None and before_seq < 1:
        raise CliError(f"--before-seq must be at least 1, got {before_seq}")


def _refuse_unknown_run(conn: sqlite3.Connection, run_id: str) -> None:
    """`unknown_run(run_id)` unless `store_queries.run_known` knows the run (an
    `events` row or a `runs` row). Run it inside the caller's snapshot.
    Read-only."""
    if not store_queries.run_known(conn, run_id):
        raise unknown_run(run_id)


def events_for(
    run_id: str,
    *,
    after_seq: int | None = None,
    limit: int | None = None,
    tail: int | None = None,
    before_seq: int | None = None,
) -> dict[str, Any]:
    """`run_id`'s events in one window, ascending by global `seq`, and the
    machine-wide `head`.

    `None` means "not given" for every keyword. The window is, by what is
    given: nothing, `after_seq` and/or `limit` -- the events with a `gseq`
    above `after_seq` (0 when not given), the first `limit` of them; `tail`
    -- the run's last `tail` events (all of them when it has fewer);
    `before_seq` -- the events with a `gseq` below it, the nearest `limit` of
    them (all when no `limit`). A caller pages forward by passing the last
    `gseq` as `after_seq`, and backwards by passing the first `gseq` as
    `before_seq` with `limit`. A window holding nothing (past the run's last
    event or `head`, `before_seq` 1 or at or below the run's first event) is
    `[]`, not a refusal; a `before_seq` above `head` reads up to `head`.

    Each line is the row's `store_events.journal_line` dumped in JSON mode
    plus `gseq`, the row's global `seq`; its own `seq` stays the per-run
    number. Every kind is included and no other run's row ever is.

    These are `CliError`s raised before the database is opened, the first
    failing one in this order: `limit` below 1, `after_seq` below 0, `tail`
    below 1, `before_seq` below 1; then `tail` with `after_seq`, with
    `before_seq` or with `limit`, and `before_seq` with `after_seq` -- given
    at all, so an explicit `after_seq=0` counts. A run with no `events` row
    and no `runs` row is an `UnknownRunError`; a `runs` row with no events is
    an empty page. Run ids are machine-unique, so no repository is resolved:
    `open_db_for_reading` gets `Path(".")` only because it takes a root,
    which never chooses the file.

    Every statement runs in one `store_db.read_snapshot`, and the first one
    reads `store_events.head`: the run check and the page see every event up
    to it and none after it, so no line's `gseq` exceeds `head`. Read-only;
    the connection is closed on every path.
    """
    _check_event_values(
        after_seq=after_seq, limit=limit, tail=tail, before_seq=before_seq
    )
    if tail is not None:
        for flag, value in (
            ("--after-seq", after_seq),
            ("--before-seq", before_seq),
            ("--limit", limit),
        ):
            if value is not None:
                raise CliError(f"--tail cannot be combined with {flag}")
    if before_seq is not None and after_seq is not None:
        raise CliError("--before-seq cannot be combined with --after-seq")
    conn = store_db.open_db_for_reading(Path("."))
    try:
        with store_db.read_snapshot(conn):
            head = store_events.head(conn)
            _refuse_unknown_run(conn, run_id)
            if tail is not None:
                rows = store_events.read_last(conn, limit=tail, run_id=run_id)
            elif before_seq is not None:
                rows = store_events.read_last(
                    conn, before_seq=before_seq, limit=limit, run_id=run_id
                )
            else:
                rows = store_events.read(
                    conn, after_seq=after_seq or 0, limit=limit, run_id=run_id
                )
            return {"events": [_event_line(row) for row in rows], "head": head}
    finally:
        conn.close()


def escalations_for(
    *,
    after_seq: int | None = None,
    limit: int | None = None,
    project: Path | None = None,
) -> dict[str, Any]:
    """Every run's escalation events above `after_seq`, ascending by global
    `seq`, and the machine-wide `head`.

    An escalation event is what `store_events.read_escalations` says it is (a
    `run_upsert` whose payload `status` is `"escalated"`); nothing here
    filters rows. `None` means "not given": `after_seq` reads from 0, and
    `limit` keeps the first `limit` escalations, so a caller pages forward by
    passing the last `gseq` as `after_seq`. Lines are `events_for`'s, built
    by `_event_line`, of every run and every project.

    `project` narrows the page to one repository: `Path(project).expanduser()`
    is looked up through `store_projects.lookup` (non-strict `resolve()`), so
    `..` and symlinked spellings of one directory match. It is never created
    or required to exist, so a path with no `projects` row (a deleted
    repository, a directory that never ran, a file) is an empty page. `head`
    stays machine-wide either way.

    `limit` below 1 and `after_seq` below 0 are `CliError`s raised before the
    database is opened, with `events_for`'s messages. `open_db_for_reading`
    gets `Path(".")` only because it takes a root, which never chooses the
    file. Every statement runs in one `store_db.read_snapshot`, and the first
    one reads `store_events.head`, so no line's `gseq` exceeds `head`.
    Read-only; the connection is closed on every path.
    """
    _check_event_values(after_seq=after_seq, limit=limit)
    conn = store_db.open_db_for_reading(Path("."))
    try:
        with store_db.read_snapshot(conn):
            head = store_events.head(conn)
            project_id = None
            if project is not None:
                project_id = store_projects.lookup(conn, Path(project).expanduser())
                if project_id is None:
                    return {"events": [], "head": head}
            rows = store_events.read_escalations(
                conn, after_seq=after_seq or 0, limit=limit, project_id=project_id
            )
            return {"events": [_event_line(row) for row in rows], "head": head}
    finally:
        conn.close()


def _check_events_form(
    run_id: str | None,
    *,
    escalations: bool,
    project: Path | None,
    tail: int | None,
    before_seq: int | None,
) -> None:
    """Which of `am events`' two forms was asked for, as `CliError`s, the
    first failing one in this order: RUN with `--escalations`, `--project`
    without `--escalations`, neither, `--escalations` with `--tail`, with
    `--before-seq`. The escalation read pages forward only."""
    if escalations and run_id is not None:
        raise CliError("--escalations cannot be combined with RUN")
    if project is not None and not escalations:
        raise CliError("--project requires --escalations")
    if not escalations and run_id is None:
        raise CliError("RUN is required unless --escalations is given")
    if escalations:
        for flag, value in (("--tail", tail), ("--before-seq", before_seq)):
            if value is not None:
                raise CliError(f"--escalations cannot be combined with {flag}")


@app.command("events")
def events(
    run_id: str | None = typer.Argument(
        None, metavar="RUN", help="The run whose events are read."
    ),
    escalations: bool = typer.Option(
        False,
        "--escalations",
        help="List every run's escalation events (a run_upsert whose status is"
        " escalated) instead of one run's. Not with RUN, --tail or --before-seq.",
    ),
    project: Path | None = typer.Option(
        None,
        "--project",
        help="With --escalations, list only this repository's escalations. A path"
        " that never ran is an empty page.",
    ),
    after_seq: int | None = typer.Option(
        None,
        "--after-seq",
        help="List events with a gseq above this (0 or more). To page forward,"
        " pass the last gseq of the previous page.",
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="List at most this many events (at least 1)."
    ),
    tail: int | None = typer.Option(
        None,
        "--tail",
        help="List the run's last N events (at least 1). Not with --after-seq,"
        " --before-seq or --limit.",
    ),
    before_seq: int | None = typer.Option(
        None,
        "--before-seq",
        help="List events with a gseq below this (at least 1), the nearest --limit of"
        " them. To page backwards, pass the first gseq of the previous page. Not"
        " with --after-seq or --tail.",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """List one run's events, or every run's escalations, in gseq order, with
    the machine-wide head.

    The run is found by id alone, whichever repository recorded it. Pass the
    last gseq as --after-seq to read the next page, or the first gseq as
    --before-seq (with --limit) to read the previous one; --tail N reads the
    last N. --escalations, given instead of RUN, lists the escalation events
    of every run (or of one --project), paged forward the same way with
    --after-seq and --limit.
    """
    try:
        _check_event_values(
            after_seq=after_seq, limit=limit, tail=tail, before_seq=before_seq
        )
        _check_events_form(
            run_id,
            escalations=escalations,
            project=project,
            tail=tail,
            before_seq=before_seq,
        )
        if escalations:
            payload = escalations_for(after_seq=after_seq, limit=limit, project=project)
        else:
            payload = events_for(
                run_id, after_seq=after_seq, limit=limit, tail=tail, before_seq=before_seq
            )
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

    Read-only, like `status_for`: the projection is reached through
    `open_db_for_reading` / `load_run`, never `Store.open`, so nothing is
    created under the data directory. The connection is closed on every path
    including the refusals.

    A `--phase` naming a deterministic phase is answered from disk: its
    attempts are the `<phase>.N` directories `run_one_step` created (spec
    e1b1e7d5). With no `--phase`, only recorded `Attempt` rows count.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_db.open_db_for_reading(root)
    try:
        run = _project_run(conn, root, run_id)
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
    before the stream began. Read-only, like `select_logs`:
    `open_db_for_reading` / `load_run`, the connection closed before anything else, and
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
    conn = store_db.open_db_for_reading(root)
    try:
        run = _project_run(conn, root, run_id)
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


def _check_watch_form(
    run_id: str | None, *, all_runs: bool, project: Path | None
) -> None:
    """Which events `am watch` was asked for, as `CliError`s, the first
    failing one in this order: RUN with `--project`, `--project` with `--all`
    or `--all-projects`, then anything but exactly one of RUN, `--all` (or
    `--all-projects`, the same set) and `--project`. `all_runs` is `--all` or
    `--all-projects`."""
    if project is not None and run_id is not None:
        raise CliError("--project cannot be combined with RUN")
    if project is not None and all_runs:
        raise CliError("--project cannot be combined with --all or --all-projects")
    if (run_id is not None) + all_runs + (project is not None) != 1:
        raise CliError(
            "give exactly one of RUN_ID, --all (or --all-projects) or --project:"
            " `am watch RUN_ID` reads one run, `am watch --all` every run of every"
            " project, `am watch --project PATH` one repository's runs"
        )


def watch_for(
    run_id: str | None,
    *,
    all_runs: bool = False,
    project: Path | None = None,
    since: int = 0,
    follow: bool = False,
    from_now: bool = False,
    since_given: bool = False,
    since_seq: int | None = None,
) -> dict[str, Any] | None:
    """The payload of `am watch`: `{"events": [...]}`, or `None` with `follow`.

    Exactly one selector: `run_id` (that run's events), `all_runs` (`--all`
    or `--all-projects`: every run of every project) or `project` (the runs of
    the repository `Path(project).expanduser()` names, looked up through
    `store_projects.lookup`, so it is never created or required to exist; a
    path with no `projects` row has no events). A run with no `events` row and
    no `runs` row is `UnknownRunError`; a run id is never joined onto a path.

    Each line is `_event_line(row)`: the journal line plus `gseq`. Lines are in
    ascending `gseq` order, and `since` keeps only rows whose per-run `seq`
    (`run_seq`) is above it, for each run alike. `since_seq` (`--since-seq`,
    `None` when not given) keeps only rows whose global `gseq` is above it,
    the same meaning as `am events --after-seq`; with both, a row must pass
    both. A `since_seq` at or above head is not an error: no rows.

    The refusals are `CliError`s raised before the database is opened, the
    first failing one in this order: `_check_watch_form`, `since` below 0,
    `since_seq` below 0, `from_now` with `since_given` (any `--since` on the
    command line, 0 included), `from_now` with `since_seq` given (0
    included), `from_now` without `follow`. So `watch` prints them as the
    usual exit-3 envelope with no stream line.

    Without `follow` the events are read in one `store_db.read_snapshot` on
    one `open_db_for_reading(Path("."))` connection (the root never chooses
    the file), closed on every path; with no `am.db` nothing is created.

    With `follow` nothing is read but, for `run_id`, the unknown-run check in
    one short snapshot: the stream reads the backlog itself.
    """
    _check_watch_form(run_id, all_runs=all_runs, project=project)
    if since < 0:
        raise CliError(f"--since must be 0 or more, got {since}")
    if since_seq is not None and since_seq < 0:
        raise CliError(f"--since-seq must be 0 or more, got {since_seq}")
    if from_now and since_given:
        raise CliError(
            "--from-now and --since are exclusive: --from-now skips the whole"
            " backlog, --since picks where in it to start; give one of them"
        )
    if from_now and since_seq is not None:
        raise CliError(
            "--from-now and --since-seq are exclusive: --from-now starts at head,"
            " --since-seq resumes after a cursor you already hold; give one of them"
        )
    if from_now and not follow:
        raise CliError(
            "--from-now needs --follow: it skips the backlog of a stream,"
            " and without --follow there is only the backlog"
        )
    if follow:
        # The stream reads its own backlog; only an unknown RUN is refused here.
        if run_id is not None:
            conn = store_db.open_db_for_reading(Path("."))
            try:
                with store_db.read_snapshot(conn):
                    _refuse_unknown_run(conn, run_id)
            finally:
                conn.close()
        return None
    conn = store_db.open_db_for_reading(Path("."))
    try:
        with store_db.read_snapshot(conn):
            project_id = None
            if run_id is not None:
                _refuse_unknown_run(conn, run_id)
            elif project is not None:
                project_id = store_projects.lookup(conn, Path(project).expanduser())
                if project_id is None:
                    return {"events": []}
            rows = store_events.read(
                conn,
                after_seq=since_seq or 0,
                run_id=run_id,
                project_id=project_id,
            )
    finally:
        conn.close()
    return {"events": [_event_line(row) for row in rows if row.run_seq > since]}


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


def _watch_hello(*, head: int, cursor_reset: bool, store_id: str | None) -> dict[str, Any]:
    """The first line of `am watch --follow`, and the only one that is not a
    JournalLine. Its `schema` is 2; the journal lines after it are emitted as
    stored.

    `head` is the machine-wide head the stream started from (0 with no
    `am.db`); `cursor_reset` is whether `--since-seq` was above it, so the
    stream starts at head instead; `store_id` is the id of the database read
    at start (`None` with no `am.db`), so a consumer can tell a replaced
    database even when its head is at or above the cursor it holds.
    """
    return {
        "event": "watch",
        "schema": 2,
        "am": __version__,
        "head": head,
        "cursor_reset": cursor_reset,
        "store_id": store_id,
        "runs_dir": str(paths.data_path() / "runs"),
    }


def _emit_stream_line(obj: Mapping[str, Any]) -> None:
    """One compact JSON object and a newline on stdout, flushed at once, so a
    consumer reading a pipe gets each line as it is written."""
    sys.stdout.write(render(obj) + "\n")
    sys.stdout.flush()


@dataclass
class _WatchCursor:
    """Where `am watch --follow` has read to: `gseq`, the largest global `seq`
    any poll read (emitted or filtered out by `--since`), starting where
    `_stream_watch` chose from the start read; and `project_id`,
    `--project`'s id once a poll found it."""

    gseq: int = 0
    project_id: int | None = None


def _watch_start() -> tuple[int, str | None]:
    """The machine-wide `head` and `store_db.store_id` a stream starts from,
    read in one snapshot on a read connection of its own, closed on every
    path; `(0, None)` with no `am.db`, and nothing is created. The stream's
    only head read."""
    conn = store_db.open_db_for_reading(Path("."))
    try:
        with store_db.read_snapshot(conn):
            return store_events.head(conn), store_db.store_id(conn)
    finally:
        conn.close()


def _poll_watch(
    cursor: _WatchCursor, *, run_id: str | None, project: Path | None, since: int
) -> Iterator[dict[str, Any]]:
    """One poll: the selected events above `cursor.gseq`, in `gseq` order, as
    `_event_line`s, keeping those whose per-run `seq` is above `since`.

    The rows are read in one snapshot on a connection of this poll's own,
    closed before the first line is yielded, so a stream holds no connection
    between polls and an `am.db` created after it started is read. Every row
    read advances `cursor.gseq`, filtered or not, so none is read twice and
    none committed above the cursor is skipped. `project` is looked up until
    it has a `projects` row; until then the poll reads nothing.
    """
    conn = store_db.open_db_for_reading(Path("."))
    try:
        with store_db.read_snapshot(conn):
            if project is not None and cursor.project_id is None:
                cursor.project_id = store_projects.lookup(
                    conn, Path(project).expanduser()
                )
                if cursor.project_id is None:
                    return
            rows = store_events.read(
                conn,
                after_seq=cursor.gseq,
                run_id=run_id,
                project_id=cursor.project_id,
            )
    finally:
        conn.close()
    for row in rows:
        cursor.gseq = row.seq
        if row.run_seq > since:
            yield _event_line(row)


def _follow_watch(
    run_id: str | None,
    *,
    project: Path | None = None,
    since: int,
    sleep: Callable[[float], None],
    max_polls: int | None,
    start_gseq: int = 0,
    backlog: bool = True,
) -> Iterator[dict[str, Any]]:
    """The backlog, then every event recorded after it.

    One poll at once for the backlog, then `sleep(WATCH_POLL_SECONDS)` and
    another poll, `max_polls` times or forever when it is `None`. One
    `_WatchCursor` spans every poll, so no event is emitted twice and none is
    skipped, however runs interleave across polls.

    The cursor starts at `start_gseq`, which `_stream_watch` chose from the
    start read; this never reads head itself. Without `backlog`
    (`--from-now`) there is no poll before the first sleep, so only events
    committed after the start are emitted, and a run or project that appears
    later is emitted from its first event.
    """
    cursor = _WatchCursor(gseq=start_gseq)
    if backlog:
        yield from _poll_watch(cursor, run_id=run_id, project=project, since=since)
    polls = 0
    while max_polls is None or polls < max_polls:
        sleep(WATCH_POLL_SECONDS)
        polls += 1
        yield from _poll_watch(cursor, run_id=run_id, project=project, since=since)


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


def _stream_watch(
    run_id: str | None,
    *,
    project: Path | None = None,
    since: int,
    head: int,
    store_id: str | None,
    from_now: bool = False,
    since_seq: int | None = None,
) -> None:
    """The body of `am watch --follow`, once `watch_for` has accepted the call
    and `watch` has made the start read (`_watch_start`).

    The cursor starts at 0 with no `since_seq`, at `since_seq` when it is at
    or below `head`, and at `head` with `from_now` (no backlog poll) or when
    `since_seq` is above it: that cursor belongs to another database, so the
    hello says `cursor_reset: true` and every event committed after the
    start is emitted. A reset is not an error.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll. Ctrl-C and a closed pipe
    are how a stream normally ends: exit 0, nothing on stderr. An error
    after the hello line (a busy or too-new database, a row whose payload is
    not JSON or whose kind `JournalLine` refuses) cannot get an envelope,
    because every line after the first must be an event line. So its
    message goes to stderr and the exit is `EXIT_ERROR`.
    """
    cursor_reset = since_seq is not None and since_seq > head
    if from_now or cursor_reset:
        start_gseq = head
    else:
        start_gseq = since_seq or 0
    try:
        _emit_stream_line(
            _watch_hello(head=head, cursor_reset=cursor_reset, store_id=store_id)
        )
        for event in _follow_watch(
            run_id,
            project=project,
            since=since,
            sleep=_watch_sleep,
            max_polls=WATCH_MAX_POLLS,
            start_gseq=start_gseq,
            backlog=not from_now,
        ):
            _emit_stream_line(event)
    except KeyboardInterrupt:
        return
    except BrokenPipeError:
        _silence_stdout()
        return
    except HANDLED as error:
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
        help="The run whose events are read. Omit it and pass --all,"
        " --all-projects or --project.",
    ),
    all_runs: bool = typer.Option(
        False, "--all", help="Read every run's events, of every project."
    ),
    all_projects: bool = typer.Option(
        False,
        "--all-projects",
        help="Read every run's events, of every project (the same as --all).",
    ),
    project: Path | None = typer.Option(
        None,
        "--project",
        metavar="PATH",
        help="Read only the runs of this repository. A path that never ran has"
        " no events.",
    ),
    since: int | None = typer.Option(
        None,
        "--since",
        metavar="SEQ",
        help="Only events whose per-run seq is greater than SEQ (default 0);"
        " across runs, each run's own seq.",
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        help="Keep printing events, one JSON object per line, until interrupted.",
    ),
    since_seq: int | None = typer.Option(
        None,
        "--since-seq",
        metavar="GSEQ",
        help="Only events whose global gseq is greater than GSEQ: resume after"
        " the last gseq you read. Exclusive with --from-now.",
    ),
    from_now: bool = typer.Option(
        False,
        "--from-now",
        help=(
            "With --follow, skip the backlog: print only events appended after"
            " the command starts. Exclusive with --since and --since-seq."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print a run's events, every run's, or one repository's, once as one
    envelope, in gseq order. Each event is a journal line plus its gseq.

    With --follow, print a hello line and then each event as its own line of
    JSON, the backlog first and then new ones as they are recorded, until
    interrupted. With --follow --from-now, the backlog is skipped and only
    events recorded after the start are printed. With --since-seq GSEQ, only
    events whose gseq is above GSEQ are printed, once or as a stream: pass
    the last gseq you read to resume. A refusal is still one envelope at
    exit 3, printed before any stream line.
    """
    # `None` means --since was not given, which --from-now must tell apart
    # from an explicit `--since 0`; every other use wants the number.
    since_given = since is not None
    since_value = since if since is not None else 0
    try:
        payload = watch_for(
            run_id,
            all_runs=all_runs or all_projects,
            project=project,
            since=since_value,
            follow=follow,
            from_now=from_now,
            since_given=since_given,
            since_seq=since_seq,
        )
        # The stream's one start read comes before any stream line, so its
        # failure is a refusal envelope like `watch_for`'s.
        start = _watch_start() if follow else None
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    if start is not None:
        head, store_id = start
        _stream_watch(
            run_id,
            project=project,
            since=since_value,
            head=head,
            store_id=store_id,
            from_now=from_now,
            since_seq=since_seq,
        )
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
    harness_override: HarnessOverride | None = None,
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
    records the run `canceled` (`card_run_status`).

    Once the checkpoint is accepted, the run's pending board comments are
    flushed (board-comments B7) and their warnings lead the payload's. Next
    comes, when a passed `commands` differs from the suite the checkpoint
    keeps (`runtime_engine.kept_commands`), one `verification: kept from
    checkpoint: [...]` warning naming the kept suite (card 5b19aa93).
    `commands` is the passed `--verify` and is read for that warning only:
    the walk is driven with `run.config.verify` and `allow_no_verification`,
    and both `record_run` calls write `run`'s config back.

    Harness timeouts (card eee43099): `harness_override`, when given,
    replaces both of the run's recorded values before anything is read or
    written (`with_harness_override`), so every run record this life writes
    carries it and a later resume keeps it. Without it the recorded values
    stay. Either way the walk's runner launches with them
    (`runner_factory_for`).
    """
    run = with_harness_override(run, harness_override)
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
                        commands=run.config.verify,
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory_for(run.config, runner_factory),
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
    isolation: Literal["none"] | None = None,
    harness_override: HarnessOverride | None = None,
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
    reason the record exists. So do the suite and the opt-out
    (`RunConfig.verify`, `RunConfig.allow_no_verification`): a non-empty
    `commands` replaces the recorded suite, `allow_no_verification` can only
    add the opt-out, and the result is written back to the record by the
    resume's first `record_run`, so a refused resume still writes nothing.
    A walk continued from a checkpoint keeps the checkpoint's pool instead,
    and a `task` resume whose passed `commands` differ from it adds a
    `verification: kept from checkpoint: [...]` warning. On a milestone the
    resulting suite and opt-out reach what starts afresh -- subtasks with no
    checkpoint, merged bases and Integrate. When a passed `commands` differs
    from the recorded suite, the payload's last warning is `verification:
    replaced in run record: [...]`, naming the suite it replaced.

    A run canceled in either spelling is refused for both workflows (live
    control C9), and so is a run whose lease is still live (C10): both
    refusals read only the connection that loaded the run.

    The launcher mode (A5 B3) is decided after those read-only refusals and
    before anything is written. `isolation="none"` runs it `direct` with no
    warning and probes nothing. Otherwise a run recorded `bwrap` or
    `unshare` is re-probed (`launcher.resolve_isolation`) and refused with
    `IsolationUnavailableError` if this host can no longer start it; a run
    recorded `direct` keeps `direct` and its recorded `isolation_warning`
    with no probe. The pair goes into the same config copy as the suite, so
    a refused resume writes nothing, and a warning is placed in `warnings`
    before the `verification: replaced` entry.

    Harness timeouts (card eee43099) are recorded on the run, so a resume
    keeps them: without `harness_override` both workflows are called exactly
    as before and launch with the recorded values. A `harness_override`
    `(default, per-phase map)` replaces both recorded values, never merged,
    and is recorded by the first run record the resume writes. A per-phase
    name the loaded run cannot dispatch (`run_agent_phases`: `resolve` on a
    `task` or story run) is `--harness-timeout`'s usage error, exit 2,
    raised after the refusals above and before `Store.open`.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_db.open_db(root)
    try:
        run = _project_run(conn, root, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        # C9, then C10: both read-only and before anything is written, so a
        # refusal leaves no run directory, row or journal line behind.
        if models.is_canceled(run.status):
            raise NotResumableError(
                f"run {run.id} was canceled; start new work with `am run --milestone`"
            )
        lease = store_leases.read_lease(conn, run.id)
        now = _utcnow()
        if lease is not None and control.lease_is_live(lease, now=now):
            raise _run_is_live_error(lease, now)
    finally:
        conn.close()
    if run.workflow not in (WORKFLOW_NAME, orchestrate.MILESTONE_WORKFLOW):
        raise NotResumableError(
            f"run {run.id!r} records workflow {run.workflow!r}, and `resume` continues"
            f" only {WORKFLOW_NAME!r} and {orchestrate.MILESTONE_WORKFLOW!r} runs"
        )
    # Passed only when given, so a resume without the flag calls exactly as before.
    override: dict[str, Any] = {}
    if harness_override is not None:
        refuse_undispatchable_harness_phases(run, harness_override[1])
        override["harness_override"] = harness_override
    # A5 B3: after every read-only refusal, before anything is written.
    if isolation == "none":
        mode: models.Launcher = "direct"
        isolation_warning: str | None = None
    elif run.config.launcher in ("bwrap", "unshare"):
        # Read as `launcher.resolve_isolation` so a test can patch it.
        restored = launcher.resolve_isolation(run.config.launcher)
        mode, isolation_warning = restored.mode, restored.warning
    else:
        mode, isolation_warning = run.config.launcher, run.config.isolation_warning
    explicit = list(commands)
    recorded = run.config.verify
    run = run.model_copy(
        update={
            "config": run.config.model_copy(
                update={
                    "verify": explicit or list(recorded),
                    "allow_no_verification": allow_no_verification
                    or run.config.allow_no_verification,
                    "launcher": mode,
                    "isolation_warning": isolation_warning,
                }
            )
        }
    )
    replaced = (
        f"verification: replaced in run record: {recorded!r}"
        if explicit and explicit != recorded
        else None
    )
    if run.workflow == WORKFLOW_NAME:
        payload = _resume_from_checkpoint(
            run,
            root=root,
            allow_no_verification=run.config.allow_no_verification,
            commands=commands,
            runner_factory=runner_factory,
            **override,
        )
    else:
        # Read as `orchestrate.run_milestone` so a test can patch it there.
        payload = orchestrate.run_milestone(
            None,
            repo_dir=root,
            commands=list(run.config.verify),
            allow_no_verification=run.config.allow_no_verification,
            launcher=mode,
            isolation_warning=isolation_warning,
            runner_factory=runner_factory,
            resume_run_id=run.id,
            **override,
        )
    if isolation_warning is not None:
        payload.setdefault("warnings", []).append(isolation_warning)
    if replaced is not None:
        payload.setdefault("warnings", []).append(replaced)
    return payload


@app.command("resume")
def resume(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run to pick back up."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help=(
            "A run started with the opt-out keeps it. Passed, this adds the "
            "opt-out to a run that lacked it, and the run records it."
        ),
    ),
    verify: list[str] = typer.Option(
        [],
        "--verify",
        help=(
            "Omitted, the run's recorded suite is used. Passed, it replaces the "
            "recorded suite for what starts afresh and is recorded. A walk "
            "continued from a checkpoint still keeps the checkpoint's suite, "
            "and says so in `warnings` when a passed one differs."
        ),
    ),
    verify_from_env: bool = typer.Option(
        False, argv_guard.FROM_ENV_FLAG, hidden=True
    ),
    isolation: Literal["none"] | None = typer.Option(
        None,
        "--isolation",
        help=(
            "Omitted, the run's recorded launcher is restored and re-probed, and a "
            "run recorded isolated is refused if this host can no longer start it. "
            "`none` runs the rest of it without isolation, on purpose, and records that."
        ),
    ),
    harness_timeout_values: list[str] = typer.Option(
        [],
        "--harness-timeout",
        metavar="[PHASE=]SECONDS",
        help=(
            "The harness timeout in seconds (60 to 86400), repeatable: a bare value is the "
            "run's default, PHASE=SECONDS overrides one agent phase. Replaces both recorded "
            "values and is recorded; without it the run keeps the timeouts it was started with."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Continue a stopped, escalated or killed run from its checkpoints, and drive it to the end.

    No `--base-branch` and no `--branch-prefix`: both were decided when the run
    started and are recorded. A `task` run continues its one subtask; a
    `milestone` run continues the whole milestone under the same run id.
    """
    commands = verify_commands(verify, from_env=verify_from_env)
    # Before anything is loaded (card 33dc5549): the run's workflow is known
    # only once it is, so every agent phase a `task` or `milestone` run can
    # dispatch is accepted here, and `resume_run` refuses a phase the loaded
    # run cannot dispatch (card eee43099). Given, the pair replaces both
    # recorded values and is recorded; passed only when given, so a resume
    # without the flag calls exactly as before and keeps the recorded values.
    default_timeout, phase_timeouts = parse_harness_timeouts(
        harness_timeout_values, phases=MILESTONE_AGENT_PHASES
    )
    override: dict[str, Any] = (
        {"harness_override": (default_timeout, phase_timeouts)}
        if harness_timeout_values
        else {}
    )
    try:
        payload = resume_run(
            run_id,
            repo_dir=repo_dir,
            allow_no_verification=allow_no_verification,
            commands=commands,
            isolation=isolation,
            **override,
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    add_argv_warnings(ctx, payload)
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    # A task payload reports `status`; a milestone payload has none and
    # carries `escalated: true` only when it stopped, as for `run`. Both
    # checks are strict on purpose: a `stopped` walk (addendum P4) is not an
    # escalation, so it exits 0 with an ok envelope.
    if payload.get("status") == "escalated" or payload.get("escalated") is True:
        raise typer.Exit(EXIT_ESCALATED)


CONTROL_COMMANDS: tuple[str, ...] = ("pause", "cancel")
"""What `am pause` and `am cancel` record, weakest first (live control C6)."""


def _heartbeat_age(lease: store_leases.LeaseRow, now: datetime) -> int:
    """Whole seconds since `lease` last beat, for a refusal message."""
    return int((now - lease.heartbeat_at).total_seconds())


def _controllable_lease(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    project_id: int | None,
    command: str,
    now: datetime,
) -> store_leases.LeaseRow:
    """The lease a request to `run_id` is addressed to, or C8's refusal.

    The order is C8's: unknown run, not `started`, no live lease, window
    closed. A run of another project than `project_id`, or any run when
    `project_id` is `None`, is an unknown run. Runs inside `request_control`'s
    transaction, so a refusal rolls back and leaves no row.
    """
    status = store_queries.run_status(conn, run_id)
    if status is None or store_queries.run_project_id(conn, run_id) != project_id:
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
    lease = store_leases.read_lease(conn, run_id)
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
    project_id: int,
    lease: store_leases.LeaseRow,
    command: str,
    now: datetime,
) -> tuple[store_leases.ControlRow, bool]:
    """Record `command` for this life of the run; the flag says it was already there.

    Only rows addressed to `lease.token` count, so a request sent to an
    earlier life never makes one to a resumed run a no-op. A no-op returns
    the first row that covers it, whose time is reported as `requested_at`,
    and inserts nothing; a new row carries `project_id`.
    """
    for row in store_leases.control_requests(conn, run_id, lease=lease.token):
        if row.command in CONTROL_SUBSUMES[command]:
            return row, True
    row = store_leases.add_control(
        conn,
        run_id,
        project_id=project_id,
        lease=lease.token,
        command=command,
        requested_at=now,
    )
    return row, False


def _effective_command(rows: Sequence[store_leases.ControlRow]) -> str:
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
    check, the insert and its `control_requested` event (`ts` the request's
    clock, never mirrored to a journal file), so two requesters cannot both
    insert and a refusal or a failed event insert leaves no row. A no-op
    request inserts neither. The project is looked up, never created, so a
    refusal creates no `projects` row either. The process holding the lease
    applies the request at its next poll; this function only records it.
    SQLite is the only channel (C1).
    """
    if command not in CONTROL_COMMANDS:
        raise ValueError(
            f"unknown control command {command!r};"
            f" expected one of {', '.join(CONTROL_COMMANDS)}"
        )
    root = resolve_repo_dir(repo_dir)
    now = clock()
    conn = store_db.open_db(root)
    try:
        with store_db.immediate(conn):
            project_id = store_projects.lookup(conn, root)
            lease = _controllable_lease(
                conn, run_id, project_id=project_id, command=command, now=now
            )
            row, already = _record_control(
                conn, run_id, project_id=project_id, lease=lease, command=command, now=now
            )
            if not already:
                store_events.insert(
                    conn,
                    project_id=project_id,
                    run_id=run_id,
                    ts=store_journal.ts_text(now),
                    kind="control_requested",
                    payload={
                        "command": row.command,
                        "lease": row.lease,
                        "requested_at": store_db.iso(row.requested_at),
                        "control_seq": row.seq,
                    },
                    source="live",
                )
            effective = _effective_command(
                store_leases.control_requests(conn, run_id, lease=lease.token)
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


def _reset_live_error(lease: store_leases.LeaseRow, now: datetime) -> RunIsLiveError:
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
    """What `am reset` tells the operator about `run_id`."""
    if already:
        return f"run {run_id} was already canceled; nothing was written"
    return (
        f"run {run_id} is canceled; `am resume {run_id}` refuses it,"
        " and a relaunch starts its cards from their first phase"
    )


def reset_run(run_id: str, *, repo_dir: Path) -> dict[str, Any]:
    """Close a run nobody is driving by recording it `canceled`.

    The run is loaded read-only, exactly as `resume_run` loads it. Refused, in
    order and before `Store.open`: an unknown run (`UnknownRunError`), a run a
    live process holds (`RunIsLiveError`, pointing at `am cancel`), and a
    finished one (`NotResettableError`). Then, as `_resume_from_checkpoint`
    does up to its first write and no further: `Store.open`, the run's own
    lease with no claims (a reset drives no card and no branch), and one
    fenced `record_run` of `canceled`. No checkpoint row is written or
    deleted, and no other row is touched. `cards` then reports each
    `(card_id, workflow)` the run checkpointed and, by
    `Store.latest_open_checkpoint`, which other run (if any) a relaunch
    would still continue it from (`open_in`). The lease is released and the
    store closed on every exit.

    A run already canceled, in either stored spelling, is not refused: the
    lease is taken and released around the check, and nothing is journalled
    (`already_canceled: true`).
    A displaced dead holder is reported under `took_over`.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_db.open_db(root)
    try:
        run = _project_run(conn, root, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        # Unknown, live, done: read-only and before `Store.open`, which would
        # mint a run directory, so a refusal leaves nothing behind (§3.4).
        lease = store_leases.read_lease(conn, run.id)
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
        # lease, so two resets serialise (the second sees the run canceled
        # and writes nothing) and a run that finished meanwhile is never
        # overwritten. No claims: a reset drives no card and no branch.
        with run_lease(store) as lease:
            current = store.load_run(run.id) or run
            if current.status == "done":
                raise _not_resettable_error(run.id)
            already = models.is_canceled(current.status)
            if not already:
                store.record_run(current.model_copy(update={"status": models.CANCELED}))
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
        "status": models.CANCELED,
        "already_canceled": already,
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
    """Close a run nobody is driving: record it canceled under its own lease.

    A running run wants `am cancel` instead; a finished one needs nothing.
    """
    try:
        payload = reset_run(run_id, repo_dir=repo_dir)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))


@app.command("migrate")
def migrate_command(
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Merge the per-project databases and run journals an older `am` left
    into `am.db`: once, refused while a run is live, legacy files untouched."""
    try:
        report = migrate.migrate(now=_utcnow())
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(asdict(report)), pretty=pretty))


@app.command("backup")
def backup_command(
    out: Path | None = typer.Option(
        None,
        "--out",
        help="The file to create; default <data dir>/backups/am-<UTC stamp>.db.",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Copy `am.db` to a new file through SQLite's online-backup API: safe
    while runs are live, never overwriting an existing file."""
    try:
        result = store_backup.backup(out, now=_utcnow())
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(asdict(result)), pretty=pretty))


@app.command("journal-check")
def journal_check_command(
    run_id: str | None = typer.Argument(
        None, metavar="[RUN]", help="The run to check; or give --all."
    ),
    all_runs: bool = typer.Option(
        False, "--all", help="Check every run in am.db or with a journal file."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Compare each run's `events` with its `journal.jsonl` and report every
    difference, changing nothing; a run live meanwhile can show its in-flight
    tail as a transient difference."""
    try:
        if (run_id is not None) == all_runs:
            raise CliError("give exactly one of RUN and --all")
        report = journal_check.check(run_id)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(asdict(report)), pretty=pretty))
