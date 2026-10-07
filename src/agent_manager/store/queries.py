"""Read-only queries over a project's projection: each run's summary with
its progress, and one run's assembled tree. Every function takes an open
connection and never writes or commits."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agent_manager import models


class RunLease(BaseModel):
    """The `lease` of one `am runs` entry: `am status`'s `control.lease`
    without `acquired_at`.

    Filled by `cli.runs_for`, never by `list_runs`: `live` is
    `control.lease_is_live` at read time, and `control` imports the `store`
    package, which imports this module, so the reverse import would be
    circular. `pid`, `host` and `heartbeat_at`
    are nullable to match the published type, though a real `run_leases` row
    always fills them. `heartbeat_at` is an ISO string, as in `control_view`.
    """

    model_config = ConfigDict(extra="forbid")

    live: bool
    pid: int | None
    host: str | None
    heartbeat_at: str | None
    accepting: bool


class ProgressCount(BaseModel):
    """`done` of `total` rows at one level of a run's tree (`stories` or
    `subtasks`). Only status `done` counts toward `done`; every other status,
    `failed`, `escalated`, `stopped` and `cancelled` included, counts toward
    `total` alone."""

    model_config = ConfigDict(extra="forbid")

    done: int
    total: int


class ProgressCurrent(BaseModel):
    """The step a run is in, read from rows only: the `started` phase's
    subtask `card`, its `phase` name, and the highest `attempt` number
    recorded for that phase, or `None` before its first attempt row."""

    model_config = ConfigDict(extra="forbid")

    card: str
    phase: str
    attempt: int | None


class RunProgress(BaseModel):
    """The `progress` of one `am runs` entry, counted by `list_runs` from the
    run's `stories`, `subtasks`, `phases` and `attempts` rows.

    `current` is required: `None` is how it says no phase is `started`."""

    model_config = ConfigDict(extra="forbid")

    stories: ProgressCount
    subtasks: ProgressCount
    current: ProgressCurrent | None


class RunSummary(BaseModel):
    """One row of the shared `runs` table, without the tree hanging off it.

    A `models.Run` would be a lie here: its `stories` list would always be empty
    because `runs` is the only table read for it. The fields are the run's
    identity and nothing else, and they go through pydantic for the same reason
    `load_run` does -- a projection that drifted from `models` must fail loudly
    rather than print half a history.

    `milestone_id` is the `runs` column: null on a `--card` run and on a row
    written before the column existed. `card_id` is not stored anywhere on the
    run row: it is the single subtask a `task` (`--card`) run records, and null
    for any other workflow or before that subtask row is written. Both default
    to `None`, so they are additive: no older key changed.

    `story_id` is the run's `config.story_id`: the story card an
    `am run --story` run drives, `None` on any other run and on a row whose
    stored config has no `story_id` key. It defaults to `None`, so it is
    additive.

    `lease` is the run's `run_leases` row as a `RunLease`, or `None` when the
    run has no lease row. `list_runs` always leaves it `None`; `am runs`
    fills it in `cli`. It too defaults to `None`, so it is additive.

    `progress` is how far the run has got, counted from its tree rows as a
    `RunProgress`. `list_runs` always fills it (a run with no tree rows is 0
    of 0 with no `current`); it defaults to `None` only so that a
    `RunSummary` built by hand stays valid, which keeps it additive.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    workflow: str
    repo_dir: Path
    base_branch: str
    branch_prefix: str
    status: models.Status
    started_at: datetime | None = None
    milestone_id: str | None = None
    card_id: str | None = None
    story_id: str | None = None
    lease: RunLease | None = None
    progress: RunProgress | None = None


def _progress_count(
    conn: sqlite3.Connection, table: Literal["stories", "subtasks"], run_id: str
) -> ProgressCount:
    """`done` of `total` rows of `table` for one run. `table` is one of two
    literals from this module, never user input, so formatting it in is safe.
    `SUM` over no rows is NULL, hence the `COALESCE`."""
    row = conn.execute(
        f"SELECT COUNT(*) AS total, COALESCE(SUM(status = 'done'), 0) AS done"
        f" FROM {table} WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return ProgressCount(done=row["done"], total=row["total"])


_CURRENT_PHASE_SQL = """
SELECT phases.card_id AS card,
       phases.name    AS phase,
       (SELECT MAX(attempts.n) FROM attempts
         WHERE attempts.run_id   = phases.run_id
           AND attempts.story_id = phases.story_id
           AND attempts.card_id  = phases.card_id
           AND attempts.phase    = phases.name) AS attempt
  FROM phases
 WHERE phases.run_id = ? AND phases.status = 'started'
 ORDER BY phases.started_at DESC, phases.story_id, phases.card_id, phases.position
 LIMIT 1
"""
"""The run's in-flight phase: the `started` one that started last (a NULL
`started_at` sorts last under `DESC`), ties broken by story, card, then
position, with the highest attempt number of that phase, or NULL before its
first attempt row."""


def _run_progress(conn: sqlite3.Connection, run_id: str) -> RunProgress:
    """One run's `RunProgress`, from read-only `SELECT`s on `conn`."""
    row = conn.execute(_CURRENT_PHASE_SQL, (run_id,)).fetchone()
    return RunProgress(
        stories=_progress_count(conn, "stories", run_id),
        subtasks=_progress_count(conn, "subtasks", run_id),
        current=None if row is None else ProgressCurrent(**dict(row)),
    )


def list_runs(conn: sqlite3.Connection) -> list[RunSummary]:
    """Every run recorded in this project's projection, newest first.

    Takes a connection rather than a root so one caller can list the history and
    then load a run's tree over the same connection, and close it once. The
    connection comes from `open_db(root)`; there is no second database.

    `started_at DESC` puts a NULL start time last (SQLite orders NULL below every
    value, so descending sends it to the end) and the id breaks a tie, which run
    ids minted at second resolution really do produce.

    `card_id` is derived, not stored: a `task` run (`am run --card`) records one
    story and one subtask, and that subtask's card is the run's card. Any other
    workflow -- a milestone run records many subtasks -- gets NULL. Should a
    `task` run ever hold several subtask rows, the lowest `position`, then the
    lowest card id, wins, so the answer is stable rather than an error.

    `story_id` is read from the row's `config` JSON alone. A config with no
    `story_id` key lists as `None`; a config that is not JSON raises
    `sqlite3.OperationalError`, and a `story_id` that is neither a string nor
    null fails `RunSummary` validation, so neither is listed as `None`.

    `progress` is counted here for every run, never left `None`, from plain
    `SELECT`s over that run's `stories`, `subtasks`, `phases` and `attempts`
    rows; nothing in it needs `control`. At each level `done` counts only
    status `done` and `total` counts every row, so `failed`, `escalated`,
    `stopped` and `cancelled` rows are in `total` alone. The synthetic
    Integrate story and its resolver subtasks are rows like any other (`store`
    cannot import `integration`, which imports it), so a milestone run that
    resolved a conflict shows one story more than its milestone has.
    `current` is the `started` phase that started last (see
    `_CURRENT_PHASE_SQL`), or `None` when no phase is started. It is read
    from rows, not from liveness: a run whose process died mid-phase still
    shows the phase it stopped in, and `lease.live` tells whether anyone is
    still working on it. A run with no tree rows is 0 of 0 with no `current`.
    """
    rows = conn.execute(
        "SELECT runs.id, runs.workflow, runs.repo_dir, runs.base_branch,"
        " runs.branch_prefix, runs.status, runs.started_at, runs.milestone_id,"
        " runs.config -> '$.story_id' AS story_id,"
        " CASE WHEN runs.workflow = 'task' THEN ("
        "   SELECT subtasks.card_id FROM subtasks"
        "    WHERE subtasks.run_id = runs.id"
        "    ORDER BY subtasks.position, subtasks.card_id LIMIT 1"
        " ) END AS card_id"
        " FROM runs ORDER BY runs.started_at DESC, runs.id DESC"
    ).fetchall()
    return [
        RunSummary.model_validate(
            {
                **dict(row),
                "story_id": None
                if row["story_id"] is None
                else json.loads(row["story_id"]),
                "progress": _run_progress(conn, row["id"]),
            }
        )
        for row in rows
    ]


def latest_run_id(conn: sqlite3.Connection) -> str | None:
    """The most recent run of this project, or `None` if it has never been run.

    Derived from `list_runs` rather than from a second `ORDER BY`, so "most
    recent" can never mean two different things in two commands.
    """
    summaries = list_runs(conn)
    return summaries[0].id if summaries else None


def load_run(conn: sqlite3.Connection, run_id: str) -> models.Run | None:
    """Assemble one run's projection back into the §9 tree, or `None` if absent.

    A free function over a connection, because the reader that needs it -- the
    `status` command -- has no `Journal` and must not create one: `Journal`
    derives its path from `paths.run_dir`, which creates the directory, so
    looking up a run that does not exist through `Store.open` would leave an
    artifact directory behind for a run nobody ever started.

    Every value goes back through the `models` validators, so a projection that
    drifted from the schema fails here rather than downstream.
    """
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None

    run = models.Run(
        id=row["id"],
        workflow=row["workflow"],
        repo_dir=row["repo_dir"],
        base_branch=row["base_branch"],
        branch_prefix=row["branch_prefix"],
        status=row["status"],
        started_at=row["started_at"],
        config=json.loads(row["config"]),
        milestone_id=row["milestone_id"],
    )

    for story_row in conn.execute(
        "SELECT * FROM stories WHERE run_id = ? ORDER BY position", (run_id,)
    ).fetchall():
        story = models.StoryRun(
            card_id=story_row["card_id"],
            title=story_row["title"],
            level=story_row["level"],
            status=story_row["status"],
            tip_branch=story_row["tip_branch"],
        )
        run.stories.append(story)

        for subtask_row in conn.execute(
            "SELECT * FROM subtasks WHERE run_id = ? AND story_id = ? ORDER BY position",
            (run_id, story.card_id),
        ).fetchall():
            subtask = models.SubtaskRun(
                card_id=subtask_row["card_id"],
                branch=subtask_row["branch"],
                base_branch=subtask_row["base_branch"],
                status=subtask_row["status"],
                worktree_path=subtask_row["worktree_path"],
            )
            story.subtasks.append(subtask)

            for phase_row in conn.execute(
                "SELECT * FROM phases WHERE run_id = ? AND story_id = ?"
                " AND card_id = ? ORDER BY position",
                (run_id, story.card_id, subtask.card_id),
            ).fetchall():
                phase = models.PhaseRun(
                    name=phase_row["name"],
                    kind=phase_row["kind"],
                    status=phase_row["status"],
                    started_at=phase_row["started_at"],
                    ended_at=phase_row["ended_at"],
                    detail=phase_row["detail"],
                )
                subtask.phases.append(phase)

                for attempt_row in conn.execute(
                    "SELECT * FROM attempts WHERE run_id = ? AND story_id = ?"
                    " AND card_id = ? AND phase = ? ORDER BY n",
                    (run_id, story.card_id, subtask.card_id, phase.name),
                ).fetchall():
                    phase.attempts.append(
                        models.Attempt(
                            n=attempt_row["n"],
                            dispatch=json.loads(attempt_row["dispatch"]),
                            status=attempt_row["status"],
                            exit_code=attempt_row["exit_code"],
                            duration=attempt_row["duration"],
                            prompt_path=attempt_row["prompt_path"],
                            result_path=attempt_row["result_path"],
                            stdout_path=attempt_row["stdout_path"],
                        )
                    )

    return run


def run_status(conn: sqlite3.Connection, run_id: str) -> str | None:
    """`runs.status` of `run_id`, or `None` if the run was never recorded.

    The legacy spelling is returned as `canceled`; any other stored value is
    returned as stored.

    A free function over a connection, like `load_run`, for a reader in
    another process that needs the status alone (`am pause`, `am resume`).
    """
    row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    return None if row is None else models.canonical_status(row["status"])


def run_config(conn: sqlite3.Connection, run_id: str) -> models.RunConfig | None:
    """`runs.config` of `run_id`, validated, or `None` if the run was never recorded.

    A free function over a connection, like `run_status`, for a reader that
    needs the config alone: the production runner factory reads the run's
    recorded launcher here.
    """
    row = conn.execute("SELECT config FROM runs WHERE id = ?", (run_id,)).fetchone()
    return None if row is None else models.RunConfig.model_validate_json(row["config"])
