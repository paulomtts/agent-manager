"""`Store`: every write to a run's journal and its SQLite projection, under one
lock and the lease fence. The journal line is appended before the row it
describes, so the journal is the truth the projection is rebuilt from.
"""

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from agent_manager import models
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import journal as store_journal
from agent_manager.store import leases as store_leases
from agent_manager.store import outbox as store_outbox
from agent_manager.store import projects as store_projects
from agent_manager.store import queries as store_queries
from agent_manager.store import replay as store_replay


def _text(value: Path | None) -> str | None:
    return None if value is None else str(value)


class Store:
    """The two stores of D5, bound together by the write ordering of §9.

    Every `record_*` appends the journal line first and writes the row second.
    There is deliberately no public method that writes a tree row on its own.
    The exceptions are `checkpoints` (pygents spec §6), `checkpoint_floors`
    (exactly-once 1.1), `run_controls` and `run_leases` (live control C1/C2),
    `run_claims` (multi-process X5) and `board_comments` (board-comments B6):
    the six row-only tables, which have no journal and are the projection's
    alone.
    Their methods write rows and never touch the journal, and
    `rebuild_from_journal` leaves those rows alone.

    The threads of the process holding a run's lease share one `Store`. A
    single re-entrant lock serialises every use of the shared connection. Each
    `record_*` holds it across the journal append and the row write, so the two
    are one critical section and journal order equals row order; `close`,
    `load_run` and `rebuild_from_journal` hold it too. Once `take_lease` has
    bound a token, every run write also runs inside `_fenced()`, one
    `BEGIN IMMEDIATE` transaction that first checks the token still holds the
    lease (multi-process X4). The lock never covers the caller's own work,
    only the append and the row write.
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
        self._lock = threading.RLock()
        self._token: str | None = None
        self._in_fence = False

    @classmethod
    def open(cls, root: Path, run_id: str) -> "Store":
        """A store on `root`'s projection, bound to `root`'s `projects` row.

        The row is resolved, or created on first sight, and committed before
        the store exists, so every run of one project shares one id. The
        wall clock is read here for the row's `created_at`, as
        `Journal.append` reads it for a line's time.
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _fenced(self) -> Iterator[None]:
        """Run one write as a single transaction fenced by the bound token (X4, X9).

        With no token bound this is a no-op and the write commits as it always
        has. Otherwise it opens `immediate`, and if this run's lease row is gone
        or carries another token it raises `LeaseLostError` before the body
        runs, so nothing is appended or written. While the body runs,
        `_in_fence` makes `_commit` a no-op: the journal append and the row
        write commit together when `immediate` exits, or roll back on a raise.
        Callers already hold `self._lock`.
        """
        if self._token is None:
            yield
            return
        with store_db.immediate(self._conn):
            current = store_leases.read_lease(self._conn, self.run_id)
            if current is None or current.token != self._token:
                raise store_leases.LeaseLostError(self.run_id, current)
            self._in_fence = True
            try:
                yield
            finally:
                self._in_fence = False

    def _commit(self) -> None:
        """Commit a row write, unless a fence will commit it with its journal line."""
        if not self._in_fence:
            self._conn.commit()

    # -- recording ---------------------------------------------------------
    #
    # Each method holds the store lock, and the fence of the bound lease token,
    # across its whole body: the journal line is appended first and the row
    # written second (§9), with no other record able to land in between. A
    # store whose lease was lost raises `LeaseLostError` before appending. If
    # the row write raises, the line stays on disk, the exception propagates
    # unchanged and the `with` block releases the lock.

    def record_run(self, run: models.Run) -> store_journal.JournalLine:
        with self._lock, self._fenced():
            if run.id != self.run_id:
                raise ValueError(
                    f"store is bound to run {self.run_id!r} but was handed run"
                    f" {run.id!r}: the row is keyed by the store's id while the"
                    " journal payload keeps the model's, so the two stores would"
                    " disagree about which run this is"
                )
            line = self._journal.append(
                "run_upsert", run.model_dump(mode="json", exclude={"stories"})
            )
            self._write_run_row(self.run_id, run)
            return line

    def record_story(self, story: models.StoryRun) -> store_journal.JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "story_upsert",
                story.model_dump(mode="json", exclude={"subtasks"}),
                story=story.card_id,
            )
            self._write_story_row(self.run_id, story)
            return line

    def record_subtask(self, story_id: str, subtask: models.SubtaskRun) -> store_journal.JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "subtask_upsert",
                subtask.model_dump(mode="json", exclude={"phases"}),
                story=story_id,
                card=subtask.card_id,
            )
            self._write_subtask_row(self.run_id, story_id, subtask)
            return line

    def record_phase(
        self, story_id: str, card_id: str, phase: models.PhaseRun
    ) -> store_journal.JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "phase_upsert",
                phase.model_dump(mode="json", exclude={"attempts"}),
                story=story_id,
                card=card_id,
                phase=phase.name,
            )
            self._write_phase_row(self.run_id, story_id, card_id, phase)
            return line

    def record_attempt(
        self, story_id: str, card_id: str, phase_name: str, attempt: models.Attempt
    ) -> store_journal.JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "attempt_upsert",
                attempt.model_dump(mode="json"),
                story=story_id,
                card=card_id,
                phase=phase_name,
                attempt=attempt.n,
            )
            self._write_attempt_row(
                self.run_id, story_id, card_id, phase_name, attempt
            )
            return line

    # -- row writers -------------------------------------------------------
    #
    # `position` is assigned from the sibling count at insert time and is never
    # touched by the conflict clause, so recording a node twice updates it in
    # place and leaves the order it was first seen in.

    def _write_run_row(self, run_id: str, run: models.Run) -> None:
        self._conn.execute(
            """
            INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,
                              status, started_at, config, milestone_id)
            VALUES (:id, :workflow, :repo_dir, :base_branch, :branch_prefix,
                    :status, :started_at, :config, :milestone_id)
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
        self._commit()

    def _write_story_row(self, run_id: str, story: models.StoryRun) -> None:
        self._conn.execute(
            """
            INSERT INTO stories (run_id, card_id, title, level, status, tip_branch, position)
            VALUES (:run_id, :card_id, :title, :level, :status, :tip_branch,
                    (SELECT COUNT(*) FROM stories WHERE run_id = :run_id))
            ON CONFLICT(run_id, card_id) DO UPDATE SET
                title=excluded.title,
                level=excluded.level,
                status=excluded.status,
                tip_branch=excluded.tip_branch
            """,
            {
                "run_id": run_id,
                "card_id": story.card_id,
                "title": story.title,
                "level": story.level,
                "status": story.status,
                "tip_branch": story.tip_branch,
            },
        )
        self._commit()

    def _write_subtask_row(
        self, run_id: str, story_id: str, subtask: models.SubtaskRun
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO subtasks (run_id, story_id, card_id, branch, base_branch,
                                  status, worktree_path, position)
            VALUES (:run_id, :story_id, :card_id, :branch, :base_branch,
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
                "run_id": run_id,
                "story_id": story_id,
                "card_id": subtask.card_id,
                "branch": subtask.branch,
                "base_branch": subtask.base_branch,
                "status": subtask.status,
                "worktree_path": _text(subtask.worktree_path),
            },
        )
        self._commit()

    def _write_phase_row(
        self, run_id: str, story_id: str, card_id: str, phase: models.PhaseRun
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO phases (run_id, story_id, card_id, name, kind, status,
                                started_at, ended_at, detail, position)
            VALUES (:run_id, :story_id, :card_id, :name, :kind, :status,
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
        self._commit()

    def _write_attempt_row(
        self,
        run_id: str,
        story_id: str,
        card_id: str,
        phase_name: str,
        attempt: models.Attempt,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO attempts (run_id, story_id, card_id, phase, n, status,
                                  exit_code, duration,
                                  prompt_path, result_path, stdout_path, dispatch)
            VALUES (:run_id, :story_id, :card_id, :phase, :n, :status,
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
        self._commit()

    # -- reading -------------------------------------------------------------

    def load_run(self, run_id: str) -> models.Run | None:
        """`store_queries.load_run` over this store's own connection.

        Kept as a method because `rebuild_from_journal` and every existing caller
        already hold a `Store`; the free function is what a reader without a run
        id uses. Holds the store lock so a read on the shared connection never
        interleaves with a write's execute or commit.
        """
        with self._lock:
            return store_queries.load_run(self._conn, run_id)

    # -- checkpoints ---------------------------------------------------------
    #
    # A row-only table outside the journal (pygents spec §6, G10): nothing here
    # calls `self._journal`. Each method holds the store lock across its whole
    # body, so `seq` is read and the row written with no other write between.

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
        under the same fence, with one commit. Any `sqlite3.Error` from either
        insert -- an unknown `reason` refused by the `checkpoints` CHECK, a
        negative floor refused by the `checkpoint_floors` CHECK -- rolls back
        both rows and propagates unchanged, and no `seq` is spent.
        """
        with self._lock, self._fenced():
            try:
                checkpoint = store_checkpoints.insert_checkpoint(
                    self._conn,
                    self.run_id,
                    card_id,
                    workflow=workflow,
                    digest=digest,
                    reason=reason,
                    agent=agent,
                    saved_at=saved_at,
                    floor=floor,
                )
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
            return checkpoint

    def latest_checkpoint(self, card_id: str) -> store_checkpoints.Checkpoint | None:
        """The highest-`seq` checkpoint of `card_id` in this store's run, any reason."""
        with self._lock:
            return store_checkpoints.latest_checkpoint(self._conn, self.run_id, card_id)

    def latest_turn_checkpoint(self, card_id: str) -> store_checkpoints.Checkpoint | None:
        """The highest-`seq` `turn` checkpoint of `card_id` in this store's run.

        A phase escalation's closing `escalated` row holds no turn
        (`runtime_engine.pending_phase`); the turn the failing phase ran in
        is the newest `turn` row, saved by `BEFORE_TURN` before it ran. A
        milestone resume rewinds to it (card 54e4ec29).
        """
        with self._lock:
            return store_checkpoints.latest_turn_checkpoint(
                self._conn, self.run_id, card_id
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
        with self._lock:
            return store_checkpoints.latest_open_checkpoint(self._conn, card_id, workflow)

    def checkpoint_cards(self, run_id: str) -> list[tuple[str, str]]:
        """Every distinct `(card_id, workflow)` with a checkpoint row under `run_id`.

        Any `reason` counts, `done` included. Ordered by `card_id`, then
        `workflow`. Read-only, and `run_id` is the argument, never
        `self.run_id`: `am reset` asks it about the run it closes
        (am-reset §3.5, card af52db54).
        """
        with self._lock:
            return store_checkpoints.checkpoint_cards(self._conn, run_id)

    # -- board comment outbox ------------------------------------------------
    #
    # A row-only table outside the journal (board-comments B6, B9): nothing
    # here calls `self._journal`, and `rebuild_from_journal` leaves the rows
    # alone. Every writer holds the store lock and the fence of the bound
    # lease token, like `save_checkpoint`. Posting to the board is not this
    # module's job: `comments.py` drains the outbox through `board.py`.

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
        row, which is left exactly as it was, whatever its state. Only the key
        collision is ignored (`ON CONFLICT(key) DO NOTHING`, not `OR IGNORE`):
        a NULL body or any other refused value raises `sqlite3.IntegrityError`
        and rolls back.
        """
        with self._lock, self._fenced():
            try:
                inserted = store_outbox.enqueue_comment(
                    self._conn,
                    run_id=run_id,
                    card_id=card_id,
                    key=key,
                    body=body,
                    now=now,
                )
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
            return inserted

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
        with self._lock:
            return store_outbox.pending_comments(self._conn, run_id, card_ids)

    def mark_comment_posted(self, key: str, comment_id: str, now: datetime) -> None:
        """Record that `key`'s body is on the board as `comment_id`.

        The row leaves `pending_comments`. An unknown `key` changes nothing.
        """
        with self._lock, self._fenced():
            try:
                store_outbox.mark_comment_posted(self._conn, key, comment_id, now)
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise

    def record_comment_failure(self, key: str) -> int:
        """Count one failed post of `key` and return the new `failed_attempts`.

        A `pending` row reaching `COMMENT_ATTEMPTS` becomes `abandoned` and
        leaves `pending_comments`; a row already `posted` keeps its state. No
        warning is emitted here. An unknown `key` changes nothing and gives 0.
        """
        with self._lock, self._fenced():
            try:
                failures = store_outbox.record_comment_failure(self._conn, key)
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
            return failures

    # -- leases, claims and control requests -----------------------------------
    #
    # Row-only tables outside the journal (live control C2, multi-process X5):
    # nothing here calls `self._journal`, and `rebuild_from_journal` leaves the
    # rows alone. `take_lease` is the only check-and-set; every other method
    # touches only the rows whose token matches, and any other token is a
    # silent no-op.

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
        `displaced` one. Then the first key another run holds under a live
        lease raises `ClaimHeldError`. Only then are the lease (window open)
        and every claim upserted and committed. Any raise rolls all of it
        back and leaves the bound token as it was. On success the store is
        bound to `token` and the journal re-reads its highest `seq`.
        """
        with self._lock:
            with store_db.immediate(self._conn):
                taken = store_leases.take_lease(
                    self._conn,
                    self.run_id,
                    token=token,
                    pid=pid,
                    host=host,
                    now=now,
                    is_live=is_live,
                    claims=claims,
                )
            self.bind_lease(token)
            self._journal.reseek()
            return taken

    def bind_lease(self, token: str | None) -> None:
        """Fence this store's run writes to `token`, or stop fencing with `None`."""
        with self._lock:
            self._token = token

    def release_claims(self, token: str) -> None:
        """Delete this run's claims held under `token`; any other row is untouched."""
        with self._lock:
            store_leases.release_claims(self._conn, self.run_id, token)
            self._conn.commit()

    def beat(self, token: str, now: datetime) -> None:
        """Move the heartbeat of this run's lease, if `token` still holds it."""
        with self._lock:
            store_leases.beat(self._conn, self.run_id, token, now)
            self._conn.commit()

    def close_window(self, token: str) -> None:
        """Stop accepting control requests under `token` (`accepting = 0`)."""
        with self._lock:
            store_leases.close_window(self._conn, self.run_id, token)
            self._conn.commit()

    def release_lease(self, token: str) -> None:
        """Delete this run's lease, if `token` still holds it."""
        with self._lock:
            store_leases.release_lease(self._conn, self.run_id, token)
            self._conn.commit()

    def adopt_lease(self, token: str) -> store_leases.LeaseRow:
        """Bind this store to `token`, which already holds this run's lease (card aff9fdbf).

        For the detached child of `am run --detach`: the parent took the lease
        and handed it off, so nothing is taken here. If the row is gone or
        carries another token, `LeaseLostError` names the holder now in place
        and the store stays unbound. Otherwise every run write is fenced by
        `token` from here on, and the journal re-reads its highest `seq`, as
        `take_lease` does, since the parent appended after this store opened.
        """
        with self._lock:
            current = store_leases.read_lease(self._conn, self.run_id)
            if current is None or current.token != token:
                raise store_leases.LeaseLostError(self.run_id, current)
            self.bind_lease(token)
            self._journal.reseek()
            return current

    def set_lease_holder(self, token: str, *, pid: int, host: str) -> None:
        """Name `pid` on `host` as this run's lease holder, if `token` still holds it.

        The parent of `am run --detach` points the row at its child before it
        prints, so `am runs` and `am status` judge the child's liveness. Any
        other token is a silent no-op, like `beat` and `close_window`.
        """
        with self._lock:
            store_leases.set_lease_holder(
                self._conn, self.run_id, token, pid=pid, host=host
            )
            self._conn.commit()

    def pending_controls(self, token: str) -> list[store_leases.ControlRow]:
        """This run's unhandled requests addressed to `token`, in `seq` order."""
        with self._lock:
            return store_leases.pending_controls(self._conn, self.run_id, token)

    def mark_control_handled(self, seq: int, now: datetime) -> None:
        """Record that this run's request `seq` has been applied."""
        with self._lock:
            store_leases.mark_control_handled(self._conn, self.run_id, seq, now)
            self._conn.commit()

    # -- rebuild -------------------------------------------------------------

    def rebuild_from_journal(self, run_id: str, *, force: bool = False) -> models.Run:
        """Replace this run's projection with what its journal says (D5).

        The journal wins: every row of the run's §9 tree (`runs`, `stories`,
        `subtasks`, `phases`, `attempts`) for `run_id` is deleted and rewritten
        from the replayed tree, so the result is the same whether the projection
        was stale, truncated or already correct. The six row-only tables
        (`checkpoints`, `checkpoint_floors`, `run_controls`, `run_leases`,
        `run_claims`, `board_comments`) have no journal and are left alone.

        The exception (journal/DB divergence §3.6): a projection holding a value
        no journal line ever recorded for that node, a `foreign` mismatch in
        `diverging`'s terms, is refused with `ProjectionDivergedError` before
        any row is touched, unless `force=True`. The check compares the same
        journal lines the rebuild replays. `stale` mismatches never refuse, and
        a projection with no `runs` row for `run_id` has nothing foreign in it.

        The store lock is held from reading the journal through the delete and
        every rewrite, so no `record_*` lands between the delete and the
        rewrite. `_delete_run` is only called from here and takes no lock of
        its own. With a lease token bound, the delete and every rewrite are
        one fenced transaction: a store that lost its lease touches no row.
        """
        with self._lock, self._fenced():
            journal = (
                self._journal if self._journal.run_id == run_id else store_journal.Journal(run_id)
            )
            lines = journal.read()
            run = store_replay.replay(lines)
            if run.id != run_id:
                raise store_journal.JournalError(
                    f"journal of run {run_id!r} has a run_upsert naming run"
                    f" {run.id!r}: refusing to key its projection under two ids"
                )
            if not force:
                projection = self.load_run(run_id)
                if projection is not None:
                    foreign = [
                        mismatch
                        for mismatch in store_replay.diverging(lines, projection)
                        if mismatch.kind == "foreign"
                    ]
                    if foreign:
                        raise store_replay.ProjectionDivergedError(run_id, foreign)
            self._delete_run(run_id)
            self._write_run_row(run_id, run)
            for story in run.stories:
                self._write_story_row(run_id, story)
                for subtask in story.subtasks:
                    self._write_subtask_row(run_id, story.card_id, subtask)
                    for phase in subtask.phases:
                        self._write_phase_row(
                            run_id, story.card_id, subtask.card_id, phase
                        )
                        for attempt in phase.attempts:
                            self._write_attempt_row(
                                run_id,
                                story.card_id,
                                subtask.card_id,
                                phase.name,
                                attempt,
                            )
            return run

    def replay_journal(self, run_id: str) -> models.Run:
        """The §9 tree `run_id`'s journal records, without touching any row.

        Adoption reads attempts here and never from the `attempts` projection.
        The store lock is held across the read: `Journal.read` takes no lock,
        and other lanes of a milestone resume append to this run's journal
        through this store, so an unlocked read could meet half a line. Nothing
        is written, so there is no `_fenced()`. Another run's journal may be
        live in another process, so only there is a torn final line ignored.
        """
        with self._lock:
            if run_id == self.run_id:
                lines = self._journal.read()
            else:
                lines = store_journal.Journal._for_reading(run_id).read(ignore_torn_tail=True)
            return store_replay.replay(lines)

    def _delete_run(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM attempts WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM phases WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM subtasks WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM stories WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        self._commit()
