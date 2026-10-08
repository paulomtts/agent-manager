"""Read-only queries over the projection: each run's summary with
its progress, and one run's assembled tree. Every function takes an open
connection and never writes or commits."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Literal

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


class RunProject(BaseModel):
    """The `project` of one `am runs` entry: the run's `runs.project_id` and
    that project's `projects.repo_dir`, the resolved path the project is
    keyed by. It can differ in spelling from the run's own `repo_dir`, which
    is the path the run recorded; neither is rewritten."""

    model_config = ConfigDict(extra="forbid")

    id: int
    repo_dir: Path


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

    `project` is the run's project as a `RunProject`. `list_runs` always
    fills it from a `LEFT JOIN projects`, and leaves it `None` only when
    `runs.project_id` names no `projects` row (possible only in a
    hand-damaged database): such a run is still listed. It defaults to
    `None`, so it is additive.
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
    project: RunProject | None = None


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


@dataclass(frozen=True)
class AllProjects:
    """The type of `ALL_PROJECTS`: `list_runs(project_id=ALL_PROJECTS)` lists
    every project's runs. A type of its own, so "no filter" can never be
    confused with `None`, which means "this repository has no project row"."""


ALL_PROJECTS: Final = AllProjects()
"""The one `AllProjects` value callers pass."""


@dataclass(frozen=True)
class RunCursor:
    """Where a `list_runs` page starts: the rows strictly after this run in the
    listing order. `started_at` is the cursor run's stored column text, not a
    parsed value, so the keyset compares exactly as `ORDER BY` sorts.
    `run_cursor` builds one from a run id."""

    started_at: str | None
    id: str


def _summary(conn: sqlite3.Connection, row: sqlite3.Row) -> RunSummary:
    """One `list_runs` row as a `RunSummary`, with its `progress` counted and
    its `project` built from the joined `project_key` / `project_repo_dir`
    columns (`None` when the join found no `projects` row)."""
    fields = dict(row)
    project_key = fields.pop("project_key")
    project_repo_dir = fields.pop("project_repo_dir")
    return RunSummary.model_validate(
        {
            **fields,
            "story_id": None
            if fields["story_id"] is None
            else json.loads(fields["story_id"]),
            "progress": _run_progress(conn, fields["id"]),
            "project": None
            if project_key is None
            else RunProject(id=project_key, repo_dir=project_repo_dir),
        }
    )


def list_runs(
    conn: sqlite3.Connection,
    *,
    project_id: int | None | AllProjects,
    limit: int | None = None,
    before: RunCursor | datetime | None = None,
) -> list[RunSummary]:
    """The runs of project `project_id`, newest first.

    Only rows whose `runs.project_id` is `project_id` are listed: the
    projection holds every repository's runs, and a listing for one
    repository shows that repository's alone. `None` (a repository with no
    `projects` row) lists nothing. `ALL_PROJECTS` lists every run of every
    project in one order, not grouped by project. Takes a connection rather
    than a root so one caller can list the history and then load a run's
    tree over the same connection, and close it once.

    `started_at DESC` puts a NULL start time last (SQLite orders NULL below every
    value, so descending sends it to the end) and the id breaks a tie, which run
    ids minted at second resolution really do produce.

    `limit` keeps at most that many rows (`None`: all of them); the caller
    checks it is at least 1. `before` starts the page later in that order:

    - a `RunCursor` keeps the rows strictly after the cursor run, the keyset
      `(started_at, id) < (cursor.started_at, cursor.id)` under the order
      above. With a dated cursor that is every earlier `started_at`, the same
      `started_at` with a smaller id, and every NULL `started_at`; with a
      NULL one, the NULL `started_at` rows with a smaller id. Paging by the
      last id of each page therefore never skips or repeats a run, even
      inside a group sharing one `started_at`.
    - a `datetime` keeps the rows whose `started_at` is a strictly earlier
      instant; NULL `started_at` rows have no instant and are dropped. A
      naive value is taken as UTC, an aware one is converted to UTC, and the
      result is compared as `isoformat()` text. That text comparison is the
      instant comparison because every `started_at` is written by
      `store_db.iso` from a UTC-aware datetime (`store/writer.py`
      `_write_run_row`, `cli._utcnow`), so all share one format and offset;
      `'+'` sorts below `'.'`, so a whole second precedes its fractions.

    `LIMIT` and the keyset are applied in SQL, so `progress` is counted only
    for the rows returned.

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

    `project` is joined from `projects` (`LEFT JOIN`), so a run whose
    `project_id` names no `projects` row is still listed, with `project`
    `None`.
    """
    where: list[str] = []
    params: list[object] = []
    if project_id is None:
        return []
    if not isinstance(project_id, AllProjects):
        where.append("runs.project_id = ?")
        params.append(project_id)
    if isinstance(before, RunCursor):
        if before.started_at is None:
            where.append("(runs.started_at IS NULL AND runs.id < ?)")
            params.append(before.id)
        else:
            where.append(
                "(runs.started_at < ?"
                " OR (runs.started_at = ? AND runs.id < ?)"
                " OR runs.started_at IS NULL)"
            )
            params.extend([before.started_at, before.started_at, before.id])
    elif isinstance(before, datetime):
        instant = (
            before.replace(tzinfo=timezone.utc)
            if before.tzinfo is None
            else before.astimezone(timezone.utc)
        )
        where.append("runs.started_at < ?")
        params.append(instant.isoformat())
    sql = (
        "SELECT runs.id, runs.workflow, runs.repo_dir, runs.base_branch,"
        " runs.branch_prefix, runs.status, runs.started_at, runs.milestone_id,"
        " runs.config -> '$.story_id' AS story_id,"
        " CASE WHEN runs.workflow = 'task' THEN ("
        "   SELECT subtasks.card_id FROM subtasks"
        "    WHERE subtasks.run_id = runs.id"
        "    ORDER BY subtasks.position, subtasks.card_id LIMIT 1"
        " ) END AS card_id,"
        " projects.id AS project_key, projects.repo_dir AS project_repo_dir"
        " FROM runs LEFT JOIN projects ON projects.id = runs.project_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY runs.started_at DESC, runs.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_summary(conn, row) for row in rows]


def latest_run_id(conn: sqlite3.Connection, *, project_id: int | None) -> str | None:
    """The most recent run of project `project_id`, or `None` if it has none.

    Derived from `list_runs` rather than from a second `ORDER BY`, so "most
    recent" can never mean two different things in two commands, and it is
    scoped exactly as `list_runs` is.
    """
    summaries = list_runs(conn, project_id=project_id)
    return summaries[0].id if summaries else None


def load_run(conn: sqlite3.Connection, run_id: str) -> models.Run | None:
    """Assemble one run's projection back into the §9 tree, or `None` if absent.

    A free function over a connection, because the reader that needs it -- the
    `status` command -- is read-only and opens no `Store`: `Store.open`
    resolves, or creates, the project row.

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


def run_project_id(conn: sqlite3.Connection, run_id: str) -> int | None:
    """`runs.project_id` of `run_id`, or `None` if the run was never recorded."""
    row = conn.execute(
        "SELECT project_id FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    return None if row is None else row["project_id"]


def run_known(conn: sqlite3.Connection, run_id: str) -> bool:
    """Whether `run_id` has an `events` row, of any kind and any project, or a
    `runs` row. Lease and claim events can precede the run's `run_upsert`, and
    a `runs` row can exist without events: either makes the run known. The
    one definition of a run looked up by id alone. Read-only."""
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM events WHERE run_id = ?)"
        " OR EXISTS (SELECT 1 FROM runs WHERE id = ?)",
        (run_id, run_id),
    ).fetchone()
    return bool(row[0])


def run_cursor(conn: sqlite3.Connection, run_id: str) -> tuple[RunCursor, int] | None:
    """`run_id` as a `list_runs` `before` cursor, with its `runs.project_id`,
    or `None` if the run was never recorded.

    The cursor holds the stored `started_at` text, so the keyset compares
    exactly the value `ORDER BY` sorted. The project id lets a repo-scoped
    caller refuse a run of another project.
    """
    row = conn.execute(
        "SELECT started_at, id, project_id FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    return RunCursor(started_at=row["started_at"], id=row["id"]), row["project_id"]
