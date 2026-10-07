"""`Store`: every write to a run's SQLite projection, as a job on one writer
thread that drains a FIFO queue, under the lease fence. A job runs in a
`BEGIN IMMEDIATE` transaction of its own, except that jobs which may batch and
wait in the queue together share one transaction, each inside a savepoint of
its own. Reads run on a separate read connection. A `record_*` writes an event
and the row it explains in one transaction; after the commit the event is
mirrored to the run's journal file, best-effort.
"""

import json
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from agent_manager import models
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import leases as store_leases
from agent_manager.store import outbox as store_outbox
from agent_manager.store import projects as store_projects
from agent_manager.store import queries as store_queries
from agent_manager.store import replay as store_replay


def _text(value: Path | None) -> str | None:
    return None if value is None else str(value)


_log = logging.getLogger(__name__)


def _node_lines(conn: sqlite3.Connection, run_id: str) -> list[store_journal.JournalLine]:
    """`store_events.run_lines` of `run_id` on `conn`, refusing a run that has
    none with `JournalError` naming it."""
    lines = store_events.run_lines(conn, run_id)
    if not lines:
        raise store_journal.JournalError(f"run {run_id!r} has no events to replay")
    return lines


T = TypeVar("T")

_CLOSED = "Cannot operate on a closed database."
"""The message of the `sqlite3.ProgrammingError` a closed `Store` raises, as a
closed `sqlite3.Connection` words it."""


@dataclass
class _Job:
    """One write: `body(conn)` inside a transaction, then `after_commit`.

    The job runs alone, in a transaction of its own, unless it `batches`: then
    it may share one transaction with the jobs that batch and wait directly
    behind it in the queue, inside a savepoint of its own. `future` carries
    the body's return value, or whatever the body, the retry or
    `after_commit` raised, to the thread that submitted the job.
    """

    body: Callable[[sqlite3.Connection], Any]
    operation: str
    fenced: bool
    after_commit: Callable[[], object] | None
    coalesce: bool = False
    future: Future = field(default_factory=Future)

    @property
    def batches(self) -> bool:
        """Whether the job may share a transaction: `coalesce` and no `after_commit`.

        A job with `after_commit` always runs alone, so `after_commit` still
        runs before the next job starts.
        """
        return self.coalesce and self.after_commit is None


def _main_file(conn: sqlite3.Connection) -> str:
    """The file `conn`'s main database lives in, or `""` for an in-memory one."""
    return next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")


class Store:
    """A run's projection rows and the events that explain them, with the run's
    journal file kept as their mirror.

    Every `record_*` inserts an `events` row and writes the row it explains in
    one transaction, then mirrors the event to the journal file after the
    commit. There is deliberately no public method that writes a tree row on
    its own. The exceptions are `checkpoints` (pygents spec §6),
    `checkpoint_floors` (exactly-once 1.1), `run_controls` and `run_leases`
    (live control C1/C2), `run_claims` (multi-process X5) and
    `board_comments` (board-comments B6): the six row-only tables, which have
    no journal and are the projection's alone. Every row this store writes
    carries its `project_id`. Their methods never touch the journal file, and
    `rebuild_from_events` leaves those rows alone. The lease and control
    writes still record their facts as events (card 1.2.7): `take_lease`
    inserts `lease_acquired` or `lease_taken_over` in its transaction, a
    refused take records `claim_conflict` in a job of its own after the
    rollback, and `mark_control_handled` inserts `control_handled` with the
    mark. Those events take `run_seq` numbers like any other and are never
    mirrored, so the journal file skips them.

    The threads of the process holding a run's lease share one `Store`. Every
    write is one job on the store's single writer thread, run in the order it
    was submitted, inside a `BEGIN IMMEDIATE` transaction that is re-run from
    its start while SQLite is busy. Heartbeat writes (`beat`, `close_window`,
    `set_lease_holder`) waiting in the queue together share one transaction,
    each inside a savepoint of its own, so one that raises fails only its own
    caller; every other write has a transaction of its own. The calling
    thread blocks until its job's transaction has committed or given up and
    gets the job's result or exception. A `record_*` job mirrors its line
    before the next job starts, so the file lists a store's lines in `run_seq`
    order. Once `take_lease` or `adopt_lease` has bound a token, every run
    write first checks, inside its transaction, that the token still holds the
    lease (multi-process X4). Reads run on the calling thread, on a separate
    read-only connection: they see only committed rows and never wait for a
    write.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        journal: store_journal.Journal,
        project_id: int,
    ) -> None:
        self._conn = conn
        self._journal = journal
        self._project_id = project_id
        self._token: str | None = None
        self._jobs: queue.SimpleQueue[_Job | None] = queue.SimpleQueue()
        self._writer: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._closed = False
        self._file = _main_file(conn)
        self._reader: sqlite3.Connection | None = None
        self._reader_lock = threading.Lock()

    @classmethod
    def open(cls, root: Path, run_id: str) -> "Store":
        """A store on `root`'s projection, bound to `root`'s `projects` row.

        The row is resolved, or created on first sight, and committed before
        the store exists, so every run of one project shares one id. The
        wall clock is read here for the row's `created_at`, as a `record_*`
        reads it for its event's `ts`.
        """
        conn = store_db.open_db(root)
        try:
            project_id = store_projects.resolve(
                conn, root, now=datetime.now(timezone.utc)
            )
            conn.commit()
        except BaseException:
            conn.close()
            raise
        return cls(conn, store_journal.Journal(run_id), project_id)

    @property
    def project_id(self) -> int:
        """The `projects.id` every row this store writes carries."""
        return self._project_id

    @property
    def run_id(self) -> str:
        return self._journal.run_id

    @property
    def journal(self) -> store_journal.Journal:
        return self._journal

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def read_connection(self) -> sqlite3.Connection:
        """The read-only connection every read method uses, opened on first use.

        On the writing connection's file, never written through, and never
        used by a job. Raises `ValueError` when the writing connection has no
        file (in-memory) and `sqlite3.ProgrammingError` once `close` has begun.
        """
        with self._reader_lock:
            return self._open_reader()

    def close(self) -> None:
        """Let every job already enqueued finish, stop and join the writer, then
        close the writing connection and the read connection.

        Works whether or not the writer was ever started; a second call is a
        no-op. Called from inside a job it raises `RuntimeError`. Afterwards
        every write and read method raises `sqlite3.ProgrammingError`.
        """
        if threading.current_thread() is self._writer:
            raise RuntimeError(
                "close() was called from inside a job on the writer thread,"
                " which would wait on itself forever"
            )
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            writer = self._writer
            if writer is not None:
                self._jobs.put(None)
        if writer is not None:
            writer.join()
        self._conn.close()
        with self._reader_lock:
            if self._reader is not None:
                self._reader.close()

    def _submit(
        self,
        body: Callable[[sqlite3.Connection], T],
        *,
        operation: str,
        fenced: bool = False,
        after_commit: Callable[[], object] | None = None,
        coalesce: bool = False,
    ) -> T:
        """Run `body` as one job on the writer thread and return what it returned.

        Blocks until the job is done; jobs run in the order they were
        submitted. `body` gets the writing connection inside a
        `BEGIN IMMEDIATE` transaction, under the lease fence when `fenced`, and
        the whole attempt re-runs from `BEGIN` while SQLite is busy
        (`store_db.run_with_retry`, `operation` naming the write). With
        `coalesce` and no `after_commit`, the job may share its transaction
        with the coalesced jobs waiting directly behind it, inside a savepoint
        of its own: a raise then undoes only this job's writes, and this call
        returns once the shared transaction has committed. `after_commit` runs
        on the writer thread after the commit and before the next job, and is
        neither retried nor rolled back. Whatever the body, the retry or
        `after_commit` raised is raised here as the same object. The first call
        starts the writer. Raises `RuntimeError` on the writer thread itself and
        `sqlite3.ProgrammingError` once `close` has begun.
        """
        if threading.current_thread() is self._writer:
            raise RuntimeError(
                f"{operation}: a Store write was submitted from inside a job on"
                " the writer thread, which would wait on itself forever"
            )
        job = _Job(body, operation, fenced, after_commit, coalesce)
        with self._state_lock:
            if self._closed:
                raise sqlite3.ProgrammingError(_CLOSED)
            if self._writer is None:
                self._writer = threading.Thread(
                    target=self._drain,
                    name=f"am-store-writer-{self.run_id}",
                    daemon=True,
                )
                self._writer.start()
            self._jobs.put(job)
        return job.future.result()

    def _drain(self) -> None:
        """The writer thread: run each job in turn until `close` enqueues `None`.

        A job that batches takes along every job that batches and already
        waits directly behind it, and they run as one batch. The first job
        that does not batch, or `None`, is held over and taken next, so jobs
        still run in the order they were submitted, and `None` ends the loop
        only once the batch before it is done. Every outcome of a job,
        `BaseException`s included, goes to that job's caller; the loop always
        moves on to the next job.
        """
        held: list[_Job | None] = []
        while (job := held.pop() if held else self._jobs.get()) is not None:
            if not job.batches:
                self._run_alone(job)
                continue
            batch = [job]
            while not held:
                try:
                    waiting = self._jobs.get_nowait()
                except queue.Empty:
                    break
                if waiting is not None and waiting.batches:
                    batch.append(waiting)
                else:
                    held.append(waiting)
            if len(batch) == 1:
                self._run_alone(job)
            else:
                self._run_batch(batch)

    def _run_alone(self, job: _Job) -> None:
        """Run `job` in a transaction of its own, then its `after_commit`."""
        try:
            result = store_db.run_with_retry(
                lambda: self._transact(job), operation=job.operation
            )
            if job.after_commit is not None:
                job.after_commit()
        except BaseException as error:  # re-raised by the caller
            job.future.set_exception(error)
        else:
            job.future.set_result(result)

    def _run_batch(self, batch: list[_Job]) -> None:
        """Run `batch` as one transaction, re-run from its first job while busy.

        No caller hears back before the commit; each then gets its own job's
        value or exception from the final attempt. If the retry gives up, or
        something outside every job's savepoint raises, nothing of the batch
        is committed and every caller gets that same exception. The retry's
        `operation` is the jobs' distinct operations joined with `+`.
        """
        operation = "+".join(dict.fromkeys(job.operation for job in batch))
        try:
            outcomes = store_db.run_with_retry(
                lambda: self._transact_batch(batch), operation=operation
            )
        except BaseException as error:  # re-raised by every caller
            for job in batch:
                job.future.set_exception(error)
            return
        for job, (value, error) in zip(batch, outcomes, strict=True):
            if error is None:
                job.future.set_result(value)
            else:
                job.future.set_exception(error)

    def _transact(self, job: _Job) -> Any:
        """One attempt at `job` alone: `BEGIN IMMEDIATE`, the fence, the body, `COMMIT`.

        Any raise rolls the whole transaction back.
        """
        with store_db.immediate(self._conn) as conn:
            self._check_fence(conn, job)
            return job.body(conn)

    def _transact_batch(
        self, batch: list[_Job]
    ) -> list[tuple[Any, BaseException | None]]:
        """One attempt at `batch`: `BEGIN IMMEDIATE`, each job in a savepoint, `COMMIT`.

        Each job's fence and body run inside `SAVEPOINT job_<n>`, `n` its place
        in the batch. A busy error escapes, so the whole transaction rolls back
        and the retry re-runs the batch from its first job. Any other raise,
        `BaseException`s included, rolls back to the job's savepoint and becomes
        that job's outcome; the other jobs' writes stand. Returns each job's
        `(value, None)` or `(None, error)`, in batch order.
        """
        outcomes: list[tuple[Any, BaseException | None]] = []
        with store_db.immediate(self._conn) as conn:
            for n, job in enumerate(batch):
                savepoint = f"job_{n}"
                conn.execute(f"SAVEPOINT {savepoint}")
                try:
                    self._check_fence(conn, job)
                    value = job.body(conn)
                except BaseException as error:  # handed to the job's caller
                    operational = isinstance(error, sqlite3.OperationalError)
                    if operational and store_db.is_busy(error):
                        raise
                    conn.execute(f"ROLLBACK TO {savepoint}")
                    conn.execute(f"RELEASE {savepoint}")
                    outcomes.append((None, error))
                else:
                    outcomes.append((value, None))
                    conn.execute(f"RELEASE {savepoint}")
        return outcomes

    def _check_fence(self, conn: sqlite3.Connection, job: _Job) -> None:
        """Raise `LeaseLostError` if `job` is fenced, a token is bound, and this
        run's lease row no longer carries that token."""
        token = self._token
        if job.fenced and token is not None:
            current = store_leases.read_lease(conn, self.run_id)
            if current is None or current.token != token:
                raise store_leases.LeaseLostError(self.run_id, current)

    def _read(self, query: Callable[[sqlite3.Connection], T]) -> T:
        """Run `query` on the read connection, on the calling thread.

        Reads are serialised among themselves by a lock no write takes, so a
        read never waits for a job, and sees only committed rows.
        """
        with self._reader_lock:
            return query(self._open_reader())

    def _open_reader(self) -> sqlite3.Connection:
        """The read connection, opened on first use. Callers hold `_reader_lock`."""
        if self._closed:
            raise sqlite3.ProgrammingError(_CLOSED)
        if self._reader is None:
            if not self._file:
                raise ValueError(
                    f"the store of run {self.run_id!r} writes an in-memory"
                    " database, which no second connection can read"
                )
            self._reader = store_db.open_reader(Path(self._file))
        return self._reader

    # -- recording ---------------------------------------------------------
    #
    # Each method is one fenced job: the event is inserted and the row it
    # explains written in one transaction, the event numbered one past the
    # run's highest `run_seq` inside it. A store whose lease was lost raises
    # `LeaseLostError` before inserting. If the row write raises, the
    # exception propagates unchanged and neither the event nor the row is
    # committed, so no `run_seq` is spent. After the commit the event is
    # mirrored to the journal file (`_mirror`).

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        kind: store_journal.EventKind,
        payload: dict,
        *,
        story_id: str | None = None,
        card_id: str | None = None,
        phase: str | None = None,
        attempt: int | None = None,
    ) -> store_events.EventRow:
        """Insert `kind`'s live event of this store's run on `conn`, inside the
        job's transaction.

        Numbered one past the run's highest `run_seq`; `ts` is the wall clock
        read now, inside the job, so a busy re-run reads it again. Nothing is
        mirrored here: only `_record` mirrors, after its commit.
        """
        return store_events.insert(
            conn,
            project_id=self._project_id,
            run_id=self.run_id,
            ts=store_journal.ts_text(datetime.now(timezone.utc)),
            kind=kind,
            payload=payload,
            source="live",
            story_id=story_id,
            card_id=card_id,
            phase=phase,
            attempt=attempt,
        )

    def _record(
        self,
        operation: str,
        kind: store_journal.EventKind,
        payload: dict,
        write_row: Callable[[sqlite3.Connection], None],
        *,
        story_id: str | None = None,
        card_id: str | None = None,
        phase: str | None = None,
        attempt: int | None = None,
    ) -> store_journal.JournalLine:
        """Insert `kind`'s event, run `write_row`, commit, then mirror the line.

        The event's `ts` is the wall clock read inside the job. A busy re-run
        starts the job over, and only the committed attempt's line is
        mirrored. Returns the line the committed event mirrors to, whether or
        not the mirror reached the file.
        """
        committed: list[store_journal.JournalLine] = []

        def job(conn: sqlite3.Connection) -> store_journal.JournalLine:
            event = self._insert_event(
                conn,
                kind,
                payload,
                story_id=story_id,
                card_id=card_id,
                phase=phase,
                attempt=attempt,
            )
            write_row(conn)
            committed[:] = [store_events.journal_line(event)]
            return committed[0]

        return self._submit(
            job,
            operation=operation,
            fenced=True,
            after_commit=lambda: self._mirror(committed[0]),
        )

    def _mirror(self, line: store_journal.JournalLine) -> None:
        """Append `line` to the run's journal file: the `after_commit` of every
        `record_*`, run once its event and row are committed.

        Best-effort: an `Exception` from the append is logged as a warning
        naming the run, the line's `seq` and its event, and is not raised, so
        the file lacks that `seq`. Anything else that is a `BaseException`
        propagates.
        """
        try:
            self._journal.mirror(line)
        except Exception:
            _log.warning(
                "run %s: event %d (%s) is committed but its journal file line"
                " was not written",
                line.run_id,
                line.seq,
                line.event,
                exc_info=True,
            )

    def record_run(self, run: models.Run) -> store_journal.JournalLine:
        def write_row(conn: sqlite3.Connection) -> None:
            if run.id != self.run_id:
                raise ValueError(
                    f"store is bound to run {self.run_id!r} but was handed run"
                    f" {run.id!r}: the row is keyed by the store's id while the"
                    " journal payload keeps the model's, so the two stores would"
                    " disagree about which run this is"
                )
            self._write_run_row(conn, self.run_id, run)

        return self._record(
            "record_run",
            "run_upsert",
            run.model_dump(mode="json", exclude={"stories"}),
            write_row,
        )

    def record_story(self, story: models.StoryRun) -> store_journal.JournalLine:
        return self._record(
            "record_story",
            "story_upsert",
            story.model_dump(mode="json", exclude={"subtasks"}),
            lambda conn: self._write_story_row(conn, self.run_id, story),
            story_id=story.card_id,
        )

    def record_subtask(self, story_id: str, subtask: models.SubtaskRun) -> store_journal.JournalLine:
        return self._record(
            "record_subtask",
            "subtask_upsert",
            subtask.model_dump(mode="json", exclude={"phases"}),
            lambda conn: self._write_subtask_row(conn, self.run_id, story_id, subtask),
            story_id=story_id,
            card_id=subtask.card_id,
        )

    def record_phase(
        self, story_id: str, card_id: str, phase: models.PhaseRun
    ) -> store_journal.JournalLine:
        return self._record(
            "record_phase",
            "phase_upsert",
            phase.model_dump(mode="json", exclude={"attempts"}),
            lambda conn: self._write_phase_row(conn, self.run_id, story_id, card_id, phase),
            story_id=story_id,
            card_id=card_id,
            phase=phase.name,
        )

    def record_attempt(
        self, story_id: str, card_id: str, phase_name: str, attempt: models.Attempt
    ) -> store_journal.JournalLine:
        return self._record(
            "record_attempt",
            "attempt_upsert",
            attempt.model_dump(mode="json"),
            lambda conn: self._write_attempt_row(
                conn, self.run_id, story_id, card_id, phase_name, attempt
            ),
            story_id=story_id,
            card_id=card_id,
            phase=phase_name,
            attempt=attempt.n,
        )

    # -- row writers -------------------------------------------------------
    #
    # `position` is assigned from the sibling count at insert time and is never
    # touched by the conflict clause, so recording a node twice updates it in
    # place and leaves the order it was first seen in.

    def _write_run_row(self, conn: sqlite3.Connection, run_id: str, run: models.Run) -> None:
        conn.execute(
            """
            INSERT INTO runs (project_id, id, workflow, repo_dir, base_branch,
                              branch_prefix, status, started_at, config, milestone_id)
            VALUES (:project_id, :id, :workflow, :repo_dir, :base_branch,
                    :branch_prefix, :status, :started_at, :config, :milestone_id)
            ON CONFLICT(id) DO UPDATE SET
                workflow=excluded.workflow,
                repo_dir=excluded.repo_dir,
                base_branch=excluded.base_branch,
                branch_prefix=excluded.branch_prefix,
                status=excluded.status,
                started_at=excluded.started_at,
                config=excluded.config,
                milestone_id=excluded.milestone_id
            """,
            {
                "project_id": self._project_id,
                "id": run_id,
                "workflow": run.workflow,
                "repo_dir": str(run.repo_dir),
                "base_branch": run.base_branch,
                "branch_prefix": run.branch_prefix,
                "status": run.status,
                "started_at": store_db.iso(run.started_at),
                "config": json.dumps(run.config.model_dump(mode="json"), sort_keys=True),
                "milestone_id": run.milestone_id,
            },
        )

    def _write_story_row(
        self, conn: sqlite3.Connection, run_id: str, story: models.StoryRun
    ) -> None:
        conn.execute(
            """
            INSERT INTO stories (project_id, run_id, card_id, title, level, status,
                                 tip_branch, position)
            VALUES (:project_id, :run_id, :card_id, :title, :level, :status, :tip_branch,
                    (SELECT COUNT(*) FROM stories WHERE run_id = :run_id))
            ON CONFLICT(run_id, card_id) DO UPDATE SET
                title=excluded.title,
                level=excluded.level,
                status=excluded.status,
                tip_branch=excluded.tip_branch
            """,
            {
                "project_id": self._project_id,
                "run_id": run_id,
                "card_id": story.card_id,
                "title": story.title,
                "level": story.level,
                "status": story.status,
                "tip_branch": story.tip_branch,
            },
        )

    def _write_subtask_row(
        self, conn: sqlite3.Connection, run_id: str, story_id: str, subtask: models.SubtaskRun
    ) -> None:
        conn.execute(
            """
            INSERT INTO subtasks (project_id, run_id, story_id, card_id, branch,
                                  base_branch, status, worktree_path, position)
            VALUES (:project_id, :run_id, :story_id, :card_id, :branch, :base_branch,
                    :status, :worktree_path,
                    (SELECT COUNT(*) FROM subtasks
                      WHERE run_id = :run_id AND story_id = :story_id))
            ON CONFLICT(run_id, story_id, card_id) DO UPDATE SET
                branch=excluded.branch,
                base_branch=excluded.base_branch,
                status=excluded.status,
                worktree_path=excluded.worktree_path
            """,
            {
                "project_id": self._project_id,
                "run_id": run_id,
                "story_id": story_id,
                "card_id": subtask.card_id,
                "branch": subtask.branch,
                "base_branch": subtask.base_branch,
                "status": subtask.status,
                "worktree_path": _text(subtask.worktree_path),
            },
        )

    def _write_phase_row(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        story_id: str,
        card_id: str,
        phase: models.PhaseRun,
    ) -> None:
        conn.execute(
            """
            INSERT INTO phases (project_id, run_id, story_id, card_id, name, kind,
                                status, started_at, ended_at, detail, position)
            VALUES (:project_id, :run_id, :story_id, :card_id, :name, :kind, :status,
                    :started_at, :ended_at, :detail,
                    (SELECT COUNT(*) FROM phases
                      WHERE run_id = :run_id AND story_id = :story_id
                        AND card_id = :card_id))
            ON CONFLICT(run_id, story_id, card_id, name) DO UPDATE SET
                kind=excluded.kind,
                status=excluded.status,
                started_at=excluded.started_at,
                ended_at=excluded.ended_at,
                detail=excluded.detail
            """,
            {
                "project_id": self._project_id,
                "run_id": run_id,
                "story_id": story_id,
                "card_id": card_id,
                "name": phase.name,
                "kind": phase.kind,
                "status": phase.status,
                "started_at": store_db.iso(phase.started_at),
                "ended_at": store_db.iso(phase.ended_at),
                "detail": phase.detail,
            },
        )

    def _write_attempt_row(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        story_id: str,
        card_id: str,
        phase_name: str,
        attempt: models.Attempt,
    ) -> None:
        conn.execute(
            """
            INSERT INTO attempts (project_id, run_id, story_id, card_id, phase, n,
                                  status, exit_code, duration,
                                  prompt_path, result_path, stdout_path, dispatch)
            VALUES (:project_id, :run_id, :story_id, :card_id, :phase, :n, :status,
                    :exit_code, :duration,
                    :prompt_path, :result_path, :stdout_path, :dispatch)
            ON CONFLICT(run_id, story_id, card_id, phase, n) DO UPDATE SET
                status=excluded.status,
                exit_code=excluded.exit_code,
                duration=excluded.duration,
                prompt_path=excluded.prompt_path,
                result_path=excluded.result_path,
                stdout_path=excluded.stdout_path,
                dispatch=excluded.dispatch
            """,
            {
                "project_id": self._project_id,
                "run_id": run_id,
                "story_id": story_id,
                "card_id": card_id,
                "phase": phase_name,
                "n": attempt.n,
                "status": attempt.status,
                "exit_code": attempt.exit_code,
                "duration": attempt.duration,
                "prompt_path": _text(attempt.prompt_path),
                "result_path": _text(attempt.result_path),
                "stdout_path": _text(attempt.stdout_path),
                "dispatch": json.dumps(
                    attempt.dispatch.model_dump(mode="json"), sort_keys=True
                ),
            },
        )

    # -- reading -------------------------------------------------------------

    def load_run(self, run_id: str) -> models.Run | None:
        """`store_queries.load_run` on this store's read connection.

        Kept as a method because every existing caller already holds a
        `Store`; the free function is what a reader without a run id uses.
        Sees only committed rows and never waits for a write.
        """
        return self._read(lambda conn: store_queries.load_run(conn, run_id))

    # -- checkpoints ---------------------------------------------------------
    #
    # A row-only table outside the journal (pygents spec §6, G10): nothing here
    # calls `self._journal`. Each write is one fenced job, so `seq` is read and
    # the row written in one transaction with no other write between.

    def save_checkpoint(
        self,
        card_id: str,
        *,
        workflow: str,
        digest: str,
        reason: str,
        agent: dict,
        saved_at: datetime,
        floor: store_checkpoints.TurnFloor | None = None,
    ) -> store_checkpoints.Checkpoint:
        """Write the next checkpoint of `card_id` under this store's run.

        `seq` is 0 for the card's first row in this run and one past the
        highest after that. With `floor`, a `checkpoint_floors` row keyed by
        the same `(run_id, card_id, seq)` is written in the same transaction,
        under the same fence, in one job. Any `sqlite3.Error` from either
        insert -- an unknown `reason` refused by the `checkpoints` CHECK, a
        negative floor refused by the `checkpoint_floors` CHECK -- rolls back
        both rows and propagates unchanged, and no `seq` is spent.
        """
        def job(conn: sqlite3.Connection) -> store_checkpoints.Checkpoint:
            return store_checkpoints.insert_checkpoint(
                conn,
                self.run_id,
                card_id,
                project_id=self._project_id,
                workflow=workflow,
                digest=digest,
                reason=reason,
                agent=agent,
                saved_at=saved_at,
                floor=floor,
            )

        return self._submit(job, operation="save_checkpoint", fenced=True)

    def latest_checkpoint(self, card_id: str) -> store_checkpoints.Checkpoint | None:
        """The highest-`seq` checkpoint of `card_id` in this store's run, any reason."""
        return self._read(
            lambda conn: store_checkpoints.latest_checkpoint(conn, self.run_id, card_id)
        )

    def latest_turn_checkpoint(self, card_id: str) -> store_checkpoints.Checkpoint | None:
        """The highest-`seq` `turn` checkpoint of `card_id` in this store's run.

        A phase escalation's closing `escalated` row holds no turn
        (`runtime_engine.pending_phase`); the turn the failing phase ran in
        is the newest `turn` row, saved by `BEFORE_TURN` before it ran. A
        milestone resume rewinds to it (card 54e4ec29).
        """
        return self._read(
            lambda conn: store_checkpoints.latest_turn_checkpoint(conn, self.run_id, card_id)
        )

    def latest_open_checkpoint(
        self, card_id: str, workflow: str
    ) -> store_checkpoints.Checkpoint | None:
        """The newest open checkpoint of `card_id` for `workflow`, across every run.

        The card's newest row in any run and any workflow decides first: if it
        is `done`, or it belongs to a run canceled in either spelling, the card
        is closed and this returns `None`. Otherwise it is the newest
        `turn`/`parked`/`escalated` row of `workflow` that does not belong to a
        run canceled in either spelling, or `None`. A checkpoint whose run has
        no `runs` row counts as not canceled. "Newest" is `saved_at`
        descending, then `seq` descending.
        """
        return self._read(
            lambda conn: store_checkpoints.latest_open_checkpoint(conn, card_id, workflow)
        )

    def checkpoint_cards(self, run_id: str) -> list[tuple[str, str]]:
        """Every distinct `(card_id, workflow)` with a checkpoint row under `run_id`.

        Any `reason` counts, `done` included. Ordered by `card_id`, then
        `workflow`. Read-only, and `run_id` is the argument, never
        `self.run_id`: `am reset` asks it about the run it closes
        (am-reset §3.5, card af52db54).
        """
        return self._read(lambda conn: store_checkpoints.checkpoint_cards(conn, run_id))

    # -- board comment outbox ------------------------------------------------
    #
    # A row-only table outside the journal (board-comments B6, B9): nothing
    # here calls `self._journal`, and `rebuild_from_events` leaves the rows
    # alone. Every writer is one fenced job, like `save_checkpoint`. Posting
    # to the board is not this module's job: `comments.py` drains the outbox
    # through `board.py`.

    def enqueue_comment(
        self,
        *,
        run_id: str,
        card_id: str,
        key: str,
        body: str,
        now: datetime,
    ) -> bool:
        """Queue `body` for `card_id` under `key`, once (B9).

        True when a `pending` row was inserted; False when `key` already had a
        row in this store's project, which is left exactly as it was, whatever
        its state. Only the key collision is ignored
        (`ON CONFLICT(project_id, key) DO NOTHING`, not `OR IGNORE`): a NULL
        body or any other refused value raises `sqlite3.IntegrityError` and
        rolls back.
        """
        def job(conn: sqlite3.Connection) -> bool:
            return store_outbox.enqueue_comment(
                conn,
                project_id=self._project_id,
                run_id=run_id,
                card_id=card_id,
                key=key,
                body=body,
                now=now,
            )

        return self._submit(job, operation="enqueue_comment", fenced=True)

    def pending_comments(
        self,
        run_id: str | None = None,
        card_ids: Iterable[str] | None = None,
    ) -> list[store_outbox.CommentRow]:
        """Every `pending` row, oldest `created_at` first, then insertion order.

        Each given filter narrows the result and they are ANDed; with neither,
        every pending row of every run is returned. `card_ids` matches across
        runs, which is what a relaunch needs; an empty `card_ids` matches
        nothing.
        """
        return self._read(
            lambda conn: store_outbox.pending_comments(conn, run_id, card_ids)
        )

    def mark_comment_posted(self, key: str, comment_id: str, now: datetime) -> None:
        """Record that `key`'s body is on the board as `comment_id`.

        The row leaves `pending_comments`. An unknown `key` changes nothing. Only
        this store's project's row is touched.
        """
        self._submit(
            lambda conn: store_outbox.mark_comment_posted(
                conn, key, comment_id, now, project_id=self._project_id
            ),
            operation="mark_comment_posted",
            fenced=True,
        )

    def record_comment_failure(self, key: str) -> int:
        """Count one failed post of `key` and return the new `failed_attempts`.

        A `pending` row reaching `COMMENT_ATTEMPTS` becomes `abandoned` and
        leaves `pending_comments`; a row already `posted` keeps its state. No
        warning is emitted here. An unknown `key` changes nothing and gives 0.
        Only this store's project's row is touched.
        """
        return self._submit(
            lambda conn: store_outbox.record_comment_failure(
                conn, key, project_id=self._project_id
            ),
            operation="record_comment_failure",
            fenced=True,
        )

    # -- leases, claims and control requests -----------------------------------
    #
    # Row-only tables outside the journal (live control C2, multi-process X5):
    # nothing here calls `self._journal`, and `rebuild_from_events` leaves the
    # rows alone. `take_lease` and `mark_control_handled` insert their event
    # (`lease_acquired`/`lease_taken_over`, `control_handled`) in the same
    # transaction as the rows, and a refused take records `claim_conflict`
    # afterwards; none of those events reaches the journal file, and every
    # other method here writes none. `take_lease` is the only check-and-set;
    # every other method touches only the rows whose token matches, and any
    # other token is a silent no-op.

    def take_lease(
        self,
        *,
        token: str,
        pid: int,
        host: str,
        now: datetime,
        is_live: Callable[[store_leases.LeaseRow], bool],
        claims: Iterable[str] = (),
    ) -> store_leases.LeaseTake:
        """Take this run's lease under `token`, with every key of `claims`, atomically.

        One `BEGIN IMMEDIATE` transaction (X5, X9): a live lease under another
        token raises `LeaseHeldError`; otherwise that row, or `None`, is the
        `displaced` one. Then the first key another run of this project holds
        under a live lease raises `ClaimHeldError`. Only then are the lease
        (window open) and every claim upserted, and one event inserted:
        `lease_acquired` (with the claim keys in the order given) when nothing
        was displaced, `lease_taken_over` (naming the displaced row) otherwise,
        even under the same token. Any raise rolls all of it back and leaves
        the bound token as it was. On success, after the commit and before any
        other job runs, the store is bound to `token`. The event is never
        mirrored to the journal file. A refusal is recorded as a
        `claim_conflict` event by a second job (`_record_claim_conflict`), then
        the same exception object is raised.
        """
        keys = tuple(claims)

        def job(conn: sqlite3.Connection) -> store_leases.LeaseTake:
            taken = store_leases.take_lease(
                conn,
                self.run_id,
                project_id=self._project_id,
                token=token,
                pid=pid,
                host=host,
                now=now,
                is_live=is_live,
                claims=keys,
            )
            displaced = taken.displaced
            if displaced is None:
                self._insert_event(
                    conn,
                    "lease_acquired",
                    {"token": token, "pid": pid, "host": host, "claims": list(keys)},
                )
            else:
                self._insert_event(
                    conn,
                    "lease_taken_over",
                    {
                        "token": token,
                        "pid": pid,
                        "host": host,
                        "displaced": {
                            "pid": displaced.pid,
                            "host": displaced.host,
                            "heartbeat_at": store_db.iso(displaced.heartbeat_at),
                        },
                    },
                )
            return taken

        try:
            return self._submit(
                job, operation="take_lease", after_commit=lambda: self.bind_lease(token)
            )
        except (store_leases.LeaseHeldError, store_leases.ClaimHeldError) as refusal:
            self._record_claim_conflict(refusal)
            raise

    def _record_claim_conflict(
        self, refusal: store_leases.LeaseHeldError | store_leases.ClaimHeldError
    ) -> None:
        """Record a refused take as this run's `claim_conflict` event, in a job
        of its own after the take's transaction rolled back.

        Unfenced. `key` is the refused claim key, or `None` when the refusal is
        a live lease on this run; `holder_*` name the holder's lease row. Best
        effort: an `Exception` from the job (`StoreBusyError` after its retry
        budget, say) is logged as a warning naming the run and the failure and
        is not raised, so `take_lease` still raises its refusal. Anything else
        that is a `BaseException` propagates.
        """
        key = refusal.key if isinstance(refusal, store_leases.ClaimHeldError) else None
        holder = refusal.holder
        payload = {
            "key": key,
            "holder_run": holder.run_id,
            "holder_pid": holder.pid,
            "holder_host": holder.host,
        }
        try:
            self._submit(
                lambda conn: self._insert_event(conn, "claim_conflict", payload),
                operation="claim_conflict",
            )
        except Exception as failure:
            _log.warning(
                "run %s: take_lease was refused but its claim_conflict event was"
                " not recorded: %s",
                self.run_id,
                failure,
                exc_info=True,
            )

    def bind_lease(self, token: str | None) -> None:
        """Fence this store's run writes to `token`, or stop fencing with `None`.

        Every fenced job reads the token bound when it runs.
        """
        self._token = token

    def release_claims(self, token: str) -> None:
        """Delete this run's claims held under `token`; any other row is untouched."""
        self._submit(
            lambda conn: store_leases.release_claims(conn, self.run_id, token),
            operation="release_claims",
        )

    def beat(self, token: str, now: datetime) -> None:
        """Move the heartbeat of this run's lease, if `token` still holds it.

        May share one transaction with other heartbeat writes waiting in the
        queue together, inside a savepoint of its own.
        """
        self._submit(
            lambda conn: store_leases.beat(conn, self.run_id, token, now),
            operation="beat",
            coalesce=True,
        )

    def close_window(self, token: str) -> None:
        """Stop accepting control requests under `token` (`accepting = 0`).

        May share one transaction with other heartbeat writes waiting in the
        queue together, inside a savepoint of its own.
        """
        self._submit(
            lambda conn: store_leases.close_window(conn, self.run_id, token),
            operation="close_window",
            coalesce=True,
        )

    def release_lease(self, token: str) -> None:
        """Delete this run's lease, if `token` still holds it."""
        self._submit(
            lambda conn: store_leases.release_lease(conn, self.run_id, token),
            operation="release_lease",
        )

    def adopt_lease(self, token: str) -> store_leases.LeaseRow:
        """Bind this store to `token`, which already holds this run's lease (card aff9fdbf).

        For the detached child of `am run --detach`: the parent took the lease
        and handed it off, so nothing is taken here. If the row is gone or
        carries another token, `LeaseLostError` names the holder now in place
        and the store stays unbound. Otherwise every run write is fenced by
        `token` from here on.
        """
        def job(conn: sqlite3.Connection) -> store_leases.LeaseRow:
            current = store_leases.read_lease(conn, self.run_id)
            if current is None or current.token != token:
                raise store_leases.LeaseLostError(self.run_id, current)
            return current

        return self._submit(
            job, operation="adopt_lease", after_commit=lambda: self.bind_lease(token)
        )

    def set_lease_holder(self, token: str, *, pid: int, host: str) -> None:
        """Name `pid` on `host` as this run's lease holder, if `token` still holds it.

        The parent of `am run --detach` points the row at its child before it
        prints, so `am runs` and `am status` judge the child's liveness. Any
        other token is a silent no-op, like `beat` and `close_window`. May share
        one transaction with other heartbeat writes waiting in the queue
        together, inside a savepoint of its own.
        """
        self._submit(
            lambda conn: store_leases.set_lease_holder(
                conn, self.run_id, token, pid=pid, host=host
            ),
            operation="set_lease_holder",
            coalesce=True,
        )

    def pending_controls(self, token: str) -> list[store_leases.ControlRow]:
        """This run's unhandled requests addressed to `token`, in `seq` order."""
        return self._read(
            lambda conn: store_leases.pending_controls(conn, self.run_id, token)
        )

    def mark_control_handled(self, seq: int, now: datetime) -> None:
        """Record that this run's request `seq` has been applied, and its
        `control_handled` event, in one transaction.

        Only a pending request is marked: an unknown `seq`, or one already
        handled, changes nothing and records nothing, so the first handling's
        `handled_at` stands. The payload's `handled_at` is `now`; the event's
        `ts` is the wall clock read inside the job. Unfenced. If the event
        insert raises, the mark is rolled back with it.
        """

        def job(conn: sqlite3.Connection) -> None:
            handled = store_leases.mark_control_handled(conn, self.run_id, seq, now)
            if handled is not None:
                self._insert_event(
                    conn,
                    "control_handled",
                    {
                        "command": handled.command,
                        "control_seq": handled.seq,
                        "handled_at": store_db.iso(now),
                    },
                )

        self._submit(job, operation="mark_control_handled")

    # -- rebuild -------------------------------------------------------------

    def rebuild_from_events(self, run_id: str, *, force: bool = False) -> models.Run:
        """Replace this run's projection with what its events say (D5).

        The events win: every row of the run's §9 tree (`runs`, `stories`,
        `subtasks`, `phases`, `attempts`) for `run_id` is deleted and rewritten
        from the tree `store_replay.replay` folds from `store_events.run_lines`,
        so the result is the same whether the projection was stale, truncated
        or already correct. The six row-only tables (`checkpoints`,
        `checkpoint_floors`, `run_controls`, `run_leases`, `run_claims`,
        `board_comments`) have no events and are left alone. No journal file
        is opened. A run with no node event raises `JournalError` naming it.

        The exception (journal/DB divergence §3.6): a projection holding a value
        no event ever recorded for that node, a `foreign` mismatch in
        `diverging`'s terms, is refused with `ProjectionDivergedError` before
        any row is touched, unless `force=True`. The check compares the same
        lines the rebuild replays. `stale` mismatches never refuse, and a
        projection with no `runs` row for `run_id` has nothing foreign in it.

        The events read, the divergence check, the delete and every rewrite
        are one fenced job, one transaction on the job's connection, whether
        or not a token is bound: no `record_*` lands between the delete and
        the rewrite, a raise anywhere leaves every row as it was, and a store
        that lost its lease touches no row.
        """

        def job(conn: sqlite3.Connection) -> models.Run:
            lines = _node_lines(conn, run_id)
            run = store_replay.replay(lines)
            if run.id != run_id:
                raise store_journal.JournalError(
                    f"journal of run {run_id!r} has a run_upsert naming run"
                    f" {run.id!r}: refusing to key its projection under two ids"
                )
            if not force:
                projection = store_queries.load_run(conn, run_id)
                if projection is not None:
                    foreign = [
                        mismatch
                        for mismatch in store_replay.diverging(lines, projection)
                        if mismatch.kind == "foreign"
                    ]
                    if foreign:
                        raise store_replay.ProjectionDivergedError(run_id, foreign)
            self._delete_run(conn, run_id)
            self._write_run_row(conn, run_id, run)
            for story in run.stories:
                self._write_story_row(conn, run_id, story)
                for subtask in story.subtasks:
                    self._write_subtask_row(conn, run_id, story.card_id, subtask)
                    for phase in subtask.phases:
                        self._write_phase_row(conn, run_id, story.card_id, subtask.card_id, phase)
                        for attempt in phase.attempts:
                            self._write_attempt_row(
                                conn,
                                run_id,
                                story.card_id,
                                subtask.card_id,
                                phase.name,
                                attempt,
                            )
            return run

        return self._submit(job, operation="rebuild_from_events", fenced=True)

    def replay_events(self, run_id: str) -> models.Run:
        """The §9 tree `run_id`'s events record, without writing anything.

        Adoption reads attempts here and never from the `attempts` projection.
        Any run's events, this store's own or another's, read on the read
        connection: only committed events are seen, and no journal file is
        opened. A run with no node event raises `JournalError` naming it;
        `run_lines`' and `replay`'s own errors propagate unchanged.
        """
        return store_replay.replay(self._read(lambda conn: _node_lines(conn, run_id)))

    def _delete_run(self, conn: sqlite3.Connection, run_id: str) -> None:
        conn.execute("DELETE FROM attempts WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM phases WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM subtasks WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM stories WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))

