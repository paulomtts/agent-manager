"""Where `agent_manager.store.writer`'s `Store` lives, what the module may
import, that no caller reaches `Store` through the `agent_manager.store`
package, which holds no code, and how its writer thread, job queue and read
connection behave.

The rest of `Store`'s behaviour is tested in `tests/test_store.py`. The tests
here import modules, read source files, or drive a `Store` on a real SQLite
file under `tmp_path` with in-process threads; nothing spawns a process, so
these are unit tests. The one exception forks a real child to prove the
at-fork hook, and is marked `e2e_fake`.
"""

import ast
import contextlib
import gc
import inspect
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import warnings
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import eventlines

from agent_manager import (
    bases,
    cli,
    control,
    dispatch,
    integration,
    models,
    orchestrate,
    runs,
    store,
)
from agent_manager.runtime import walk as runtime_walk
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import leases as store_leases
from agent_manager.store import outbox as store_outbox
from agent_manager.store import projects as store_projects
from agent_manager.store import queries as store_queries
from agent_manager.store import writer as store_writer

_REPO = Path(__file__).resolve().parents[2]

_STORE_LEAVES = (
    "db",
    "journal",
    "replay",
    "queries",
    "leases",
    "checkpoints",
    "outbox",
    "projects",
    "events",
)

_WRITER_MAY_IMPORT = frozenset(
    {"agent_manager.models", *(f"agent_manager.store.{leaf}" for leaf in _STORE_LEAVES)}
)


def test_store_is_defined_in_the_writer_module():
    assert inspect.isclass(store_writer.Store)
    assert store_writer.Store.__module__ == "agent_manager.store.writer"
    assert store_writer._text(None) is None
    assert store_writer._text(Path("/a/b")) == "/a/b"


def test_the_store_package_holds_no_code_and_re_exports_nothing():
    body = ast.parse(Path(store.__file__).read_text()).body
    assert len(body) == 1, [type(node).__name__ for node in body]
    assert isinstance(body[0], ast.Expr)
    assert isinstance(body[0].value, ast.Constant)
    assert isinstance(body[0].value.value, str)
    # `store_writer` is imported above, so the package has loaded it by now.
    assert not hasattr(store, "Store")
    assert not hasattr(store, "_text")
    assert store.writer is store_writer
    names = {name for name in vars(store) if not name.startswith("__")}
    assert names <= {*_STORE_LEAVES, "writer", "legacy", "backup"}, sorted(names)


def _imported_modules(path: Path) -> list[str]:
    """Every module `path` imports, anywhere in its AST, as a dotted name.

    `from agent_manager import models` resolves to `agent_manager.models` and
    `from agent_manager.store import db` to `agent_manager.store.db`. A
    relative import resolves against the `agent_manager.store` package.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = ["agent_manager", "store"][: 3 - node.level]
                module = ".".join([*package, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if module in ("agent_manager", "agent_manager.store"):
                found.extend(f"{module}.{alias.name}" for alias in node.names)
            else:
                found.append(module)
    return found


def test_writer_imports_only_models_and_the_store_leaves():
    imported = _imported_modules(Path(store_writer.__file__))
    ours = {name for name in imported if name.split(".")[0] == "agent_manager"}
    theirs = [
        name
        for name in imported
        if name.split(".")[0] not in (*sys.stdlib_module_names, "agent_manager")
    ]
    assert sorted(ours - _WRITER_MAY_IMPORT) == []
    assert theirs == []


def test_no_store_leaf_imports_the_writer():
    package = Path(store.__file__).parent
    importers = [
        path.name
        for path in sorted(package.glob("*.py"))
        if path.name != "writer.py"
        and "agent_manager.store.writer" in _imported_modules(path)
    ]
    assert importers == []


_THROUGH_THE_PACKAGE = re.compile(
    r"\bstore(_module)?\.Store\b"
    r"|^\s*from agent_manager\.store import (?!writer\b)[^#]*\bStore\b"
)


def _hits(pattern: re.Pattern[str]) -> list[str]:
    """Every line under `src/` and `tests/`, outside this file, that `pattern` matches."""
    me = Path(__file__).resolve()
    return [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for root in (_REPO / "src", _REPO / "tests")
        for path in sorted(root.rglob("*.py"))
        if path.resolve() != me and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]


def test_no_caller_reaches_store_through_the_store_package():
    # The opt-in tiers (e2e_fake, soak, e2e) never run in the default suite,
    # so a stale reference there would only fail when someone runs that tier.
    # The import form is anchored at the line start, so the sibling scans'
    # self-check literals, which start with `assert` or a quote, are not hits.
    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import Store")
    assert _THROUGH_THE_PACKAGE.search("    from agent_manager.store import Store")
    # Split across two lines: the outbox and replay scans read this file too,
    # and either literal whole on one line is a hit for them.
    assert _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import "
        "CommentRow, Store"
    )
    assert _THROUGH_THE_PACKAGE.search("st = store.Store.open(root, run_id)")
    assert _THROUGH_THE_PACKAGE.search('monkeypatch.setattr(store_module.Store, "open", spy)')
    assert not _THROUGH_THE_PACKAGE.search("from agent_manager.store.writer import Store")
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import writer as store_writer"
    )
    assert not _THROUGH_THE_PACKAGE.search("st = store_writer.Store.open(root, run_id)")
    assert not _THROUGH_THE_PACKAGE.search("rows = store.latest_checkpoint()")
    assert not _THROUGH_THE_PACKAGE.search(
        '    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import '
        'Mismatch, Store")'
    )
    assert _hits(_THROUGH_THE_PACKAGE) == []


def test_writer_module_docstring_states_a_contract():
    doc = store_writer.__doc__
    assert doc is not None and doc.strip()
    assert "§" not in doc


def test_every_source_caller_binds_the_one_store_class():
    # A patch on the class (`monkeypatch.setattr(store_writer.Store, ...)`)
    # reaches every caller only because each binds this same class object.
    callers = (bases, cli, control, dispatch, integration, orchestrate, runs, runtime_walk)
    assert [
        module.__name__ for module in callers if module.Store is not store_writer.Store
    ] == []


RUN_A = "run-2026-10-07-01"
RUN_B = "run-2026-10-07-02"


def test_store_open_resolves_the_project_once_per_repo(repo):
    first = store_writer.Store.open(repo, RUN_A)
    second = store_writer.Store.open(repo, RUN_B)
    try:
        ids = (first.project_id, second.project_id)
    finally:
        first.close()
        second.close()

    # A fresh connection sees the row: `Store.open` committed it.
    observer = store_db.open_db(repo)
    try:
        rows = [tuple(row) for row in observer.execute("SELECT id, repo_dir FROM projects")]
    finally:
        observer.close()

    assert ids[0] == ids[1]
    assert rows == [(ids[0], str(repo.resolve()))]


def test_store_open_through_a_symlink_is_the_same_project(repo):
    # Review Focus 1: two spellings of one directory are one project.
    link = repo.parent / "repo-link"
    link.symlink_to(repo, target_is_directory=True)

    direct = store_writer.Store.open(repo, RUN_A)
    linked = store_writer.Store.open(link, RUN_B)
    try:
        assert linked.project_id == direct.project_id
        count = direct.connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    finally:
        direct.close()
        linked.close()

    assert count == 1


def test_store_project_id_is_read_only_and_set_by_the_constructor(repo):
    st = store_writer.Store(store_db.open_db(repo), store_journal.Journal(RUN_A), 7)
    try:
        assert st.project_id == 7
        with pytest.raises(AttributeError):
            st.project_id = 8  # type: ignore[misc]
    finally:
        st.close()


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

_TREE_TABLES = ("runs", "stories", "subtasks", "phases", "attempts")
_STORE_WRITTEN_TABLES = (
    *_TREE_TABLES,
    "checkpoints",
    "checkpoint_floors",
    "run_leases",
    "run_claims",
    "board_comments",
)


def _project_ids(conn, table: str) -> list[int]:
    return [row[0] for row in conn.execute(f"SELECT project_id FROM {table}")]


def _record_tree(st: store_writer.Store, repo: Path) -> None:
    st.record_run(
        models.Run(
            id=RUN_A,
            workflow="milestone",
            repo_dir=repo,
            base_branch="main",
            branch_prefix="m1/",
            status="started",
            started_at=NOW,
        )
    )
    st.record_story(models.StoryRun(card_id="story-a", title="Story", level=0, status="started"))
    st.record_subtask(
        "story-a",
        models.SubtaskRun(
            card_id="card-a", branch="m1/task-card-a", base_branch="main", status="started"
        ),
    )
    st.record_phase(
        "story-a",
        "card-a",
        models.PhaseRun(name="implement", kind="agent", status="started", started_at=NOW),
    )
    st.record_attempt(
        "story-a",
        "card-a",
        "implement",
        models.Attempt(
            n=1,
            dispatch=models.Dispatch(
                harness="claude",
                model="sonnet",
                role="coder",
                cwd=repo,
                prompt_path=repo / "prompt.txt",
                result_path=repo / "result.json",
            ),
        ),
    )


def test_every_row_the_store_writes_carries_its_project_id(repo):
    # Another project is seen first, so this store's id is not the first one
    # a fresh table hands out: a hard-coded or defaulted id cannot pass.
    seed = store_db.open_db(repo)
    try:
        elsewhere = store_projects.resolve(seed, repo.parent / "elsewhere", now=NOW)
        seed.commit()
    finally:
        seed.close()
    st = store_writer.Store.open(repo, RUN_A)
    try:
        _record_tree(st, repo)
        st.save_checkpoint(
            "card-a",
            workflow="task",
            digest="d",
            reason="turn",
            agent={},
            saved_at=NOW,
            floor=store_checkpoints.TurnFloor(phase="implement", loop=0, source_run=RUN_A, floor=0),
        )
        st.enqueue_comment(run_id=RUN_A, card_id="card-a", key="k1", body="b", now=NOW)
        st.take_lease(
            token="t1", pid=1, host="h", now=NOW, is_live=lambda row: False, claims=["card:card-a"]
        )
        written = {table: _project_ids(st.connection, table) for table in _STORE_WRITTEN_TABLES}
        st.rebuild_from_events(RUN_A)
        rebuilt = {table: _project_ids(st.connection, table) for table in _TREE_TABLES}
        project_id = st.project_id
    finally:
        st.close()

    assert project_id != elsewhere
    assert written == {table: [project_id] for table in _STORE_WRITTEN_TABLES}
    assert rebuilt == {table: [project_id] for table in _TREE_TABLES}


# -- the writer thread and its job queue -------------------------------------

TIMEOUT = 5.0
"""The longest any test here waits on another thread; it only expires on failure."""


def _busy() -> sqlite3.OperationalError:
    """A busy error as SQLite raises it: `run_with_retry` re-runs the job."""
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return error


def _insert_meta(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, 'v')", (key,))


def _meta_keys(conn: sqlite3.Connection, prefix: str) -> list[str]:
    """Committed `meta` keys starting with `prefix`, in insertion order."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT key FROM meta WHERE key LIKE ? ORDER BY rowid", (prefix + "%",)
        )
    ]


def _writer_threads(run_id: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == f"am-store-writer-{run_id}"]


class _Caller:
    """Calls `fn(*args, **kwargs)` on a thread of its own and keeps the outcome."""

    def __init__(self, fn, *args, **kwargs) -> None:
        self.value: object = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(
            target=self._run, args=(fn, args, kwargs), daemon=True
        )
        self.thread.start()

    def _run(self, fn, args, kwargs) -> None:
        try:
            self.value = fn(*args, **kwargs)
        except BaseException as error:  # handed to the test through `error`
            self.error = error

    def wait(self) -> "_Caller":
        self.thread.join(TIMEOUT)
        assert not self.thread.is_alive(), "the call did not return within the timeout"
        return self


class _Gate:
    """A job that holds the writer thread, inside its open transaction, until released."""

    def __init__(self, st: store_writer.Store) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()
        self._caller = _Caller(st._submit, self._body, operation="gate")
        assert self.entered.wait(TIMEOUT), "the gate job never started"

    def _body(self, conn: sqlite3.Connection) -> None:
        self.entered.set()
        assert self.released.wait(TIMEOUT), "the gate was never released"

    def release(self) -> None:
        self.released.set()
        self._caller.wait()

    def __enter__(self) -> "_Gate":
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


def _wait_enqueued(st: store_writer.Store, count: int) -> None:
    """Return once `count` jobs wait in `st`'s queue behind the running one."""
    deadline = time.monotonic() + TIMEOUT
    while st._jobs.qsize() < count:
        assert time.monotonic() < deadline, f"{count} job(s) were never enqueued"
        time.sleep(0.001)


def test_every_job_runs_on_the_one_writer_thread(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        callers = [
            _Caller(st._submit, lambda conn: threading.current_thread(), operation="probe")
            for _ in range(2)
        ]
        ran_on = [caller.wait().value for caller in callers]
    finally:
        st.close()

    assert [caller.error for caller in callers] == [None, None]
    assert ran_on[0] is ran_on[1]
    assert ran_on[0] not in [caller.thread for caller in callers]
    assert ran_on[0] is not threading.current_thread()
    assert ran_on[0].name == f"am-store-writer-{RUN_A}"


def test_jobs_run_in_the_order_they_were_enqueued(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        callers: list[_Caller] = []
        with _Gate(st):
            for n in range(5):
                callers.append(
                    _Caller(
                        st._submit,
                        lambda conn, key=f"fifo-{n}": _insert_meta(conn, key),
                        operation="fifo",
                    )
                )
                _wait_enqueued(st, n + 1)
        for caller in callers:
            caller.wait()
        keys = _meta_keys(st.connection, "fifo-")
    finally:
        st.close()

    assert [caller.error for caller in callers] == [None] * 5
    assert keys == [f"fifo-{n}" for n in range(5)]


def test_a_failing_job_fails_only_its_own_caller(repo):
    failure = ValueError("job A refuses")
    ran_on: list[threading.Thread] = []

    def job_a(conn):
        ran_on.append(threading.current_thread())
        _insert_meta(conn, "job-a")
        raise failure

    def job_b(conn):
        _insert_meta(conn, "job-b")
        return "b"

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(ValueError) as caught:
            st._submit(job_a, operation="job_a")
        b = st._submit(job_b, operation="job_b")
        third_ran_on = st._submit(lambda conn: threading.current_thread(), operation="job_c")
        keys = _meta_keys(st.connection, "job-")
    finally:
        st.close()

    assert caught.value is failure
    assert b == "b"
    assert keys == ["job-b"]
    assert third_ran_on is ran_on[0]


@pytest.mark.parametrize(
    "error",
    [store_leases.LeaseLostError(RUN_A, None), KeyboardInterrupt()],
    ids=["lease-lost", "keyboard-interrupt"],
)
def test_a_base_exception_from_a_job_reaches_its_caller_unchanged(repo, error):
    def job(conn):
        raise error

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(type(error)) as caught:
            st._submit(job, operation="raises")
        after = st._submit(lambda conn: "still serving", operation="after")
    finally:
        st.close()

    assert caught.value is error
    assert after == "still serving"


def test_a_busy_job_is_rolled_back_and_re_run_from_its_start(repo, monkeypatch):
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    attempts: list[int] = []

    def job(conn):
        attempts.append(len(attempts) + 1)
        _insert_meta(conn, f"busy-{len(attempts)}")
        if len(attempts) == 1:
            raise _busy()
        return f"attempt {len(attempts)}"

    st = store_writer.Store.open(repo, RUN_A)
    try:
        result = st._submit(job, operation="busy_once")
        keys = _meta_keys(st.connection, "busy-")
    finally:
        st.close()

    assert attempts == [1, 2]
    assert result == "attempt 2"
    assert keys == ["busy-2"]


def test_a_job_busy_past_the_budget_raises_store_busy_error(repo, monkeypatch):
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    calls: list[int] = []

    def job(conn):
        calls.append(1)
        _insert_meta(conn, f"never-{len(calls)}")
        raise _busy()

    def next_job(conn):
        _insert_meta(conn, "next")
        return "next"

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(store_db.StoreBusyError) as caught:
            st._submit(job, operation="always_busy")
        after = st._submit(next_job, operation="next")
        never = _meta_keys(st.connection, "never-")
    finally:
        st.close()

    assert caught.value.operation == "always_busy"
    assert caught.value.attempts == store_db.RETRY_ATTEMPTS
    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
    assert len(calls) == store_db.RETRY_ATTEMPTS
    assert never == []
    assert after == "next"


def test_an_integrity_error_is_not_retried(repo, monkeypatch):
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    calls: list[int] = []

    def job(conn):
        calls.append(1)
        conn.execute("INSERT INTO meta (key, value) VALUES ('refused', NULL)")

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            st._submit(job, operation="refused")
    finally:
        st.close()

    assert calls == [1]


def test_a_submit_from_inside_a_job_raises_instead_of_deadlocking(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        call = _Caller(
            st._submit,
            lambda conn: st._submit(lambda inner: None, operation="inner"),
            operation="outer",
        ).wait()
    finally:
        st.close()

    assert isinstance(call.error, RuntimeError)
    assert "writer thread" in str(call.error)


def test_close_without_a_write_and_twice_is_safe(repo):
    st = store_writer.Store.open(repo, RUN_A)
    st.close()
    st.close()

    assert _writer_threads(RUN_A) == []
    with pytest.raises(sqlite3.ProgrammingError):
        st.connection.execute("SELECT 1")


def test_close_lets_already_enqueued_jobs_finish(repo):
    def queued_job(conn):
        _insert_meta(conn, "queued")
        return "landed"

    st = store_writer.Store.open(repo, RUN_A)
    gate = _Gate(st)
    try:
        queued = _Caller(st._submit, queued_job, operation="queued")
        _wait_enqueued(st, 1)
        closer = _Caller(st.close)
        _wait_enqueued(st, 2)
        gate.release()
        closer.wait()
        queued.wait()
    finally:
        gate.release()
        st.close()

    observer = store_db.open_db(repo)
    try:
        keys = _meta_keys(observer, "queued")
    finally:
        observer.close()

    assert queued.error is None and queued.value == "landed"
    assert closer.error is None
    assert keys == ["queued"]
    assert _writer_threads(RUN_A) == []


def test_close_from_inside_a_job_raises(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(RuntimeError, match="writer thread"):
            st._submit(lambda conn: st.close(), operation="closes")
        after = st._submit(lambda conn: "open", operation="after")
    finally:
        st.close()

    assert after == "open"


def test_a_write_racing_close_either_lands_or_raises_programming_error(repo):
    st = store_writer.Store.open(repo, RUN_A)
    start = threading.Barrier(9)

    def write(n: int) -> int:
        start.wait(TIMEOUT)
        st._submit(lambda conn: _insert_meta(conn, f"race-{n}"), operation="race")
        return n

    def close() -> None:
        start.wait(TIMEOUT)
        st.close()

    try:
        writers = [_Caller(write, n) for n in range(8)]
        closer = _Caller(close)
        for caller in [*writers, closer]:
            caller.wait()
    finally:
        st.close()

    observer = store_db.open_db(repo)
    try:
        keys = set(_meta_keys(observer, "race-"))
    finally:
        observer.close()

    assert closer.error is None
    refused = [caller.error for caller in writers if caller.error is not None]
    assert all(isinstance(error, sqlite3.ProgrammingError) for error in refused)
    landed = {f"race-{caller.value}" for caller in writers if caller.error is None}
    assert keys == landed
    assert _writer_threads(RUN_A) == []


# -- reads ----------------------------------------------------------------------


def test_a_read_does_not_wait_for_a_running_write(repo):
    inserted = threading.Event()
    release = threading.Event()
    st = store_writer.Store.open(repo, RUN_A)

    def held_open(conn):
        store_checkpoints.insert_checkpoint(
            conn,
            RUN_A,
            "card-a",
            project_id=st.project_id,
            workflow="task",
            digest="d",
            reason="turn",
            agent={},
            saved_at=NOW,
        )
        store_outbox.enqueue_comment(
            conn,
            project_id=st.project_id,
            run_id=RUN_A,
            card_id="card-a",
            key="k1",
            body="b",
            now=NOW,
        )
        inserted.set()
        assert release.wait(TIMEOUT)

    try:
        writing = _Caller(st._submit, held_open, operation="held_open")
        assert inserted.wait(TIMEOUT)
        during = _Caller(
            lambda: (st.latest_checkpoint("card-a"), st.pending_comments())
        ).wait()
        release.set()
        writing.wait()
        after_checkpoint = st.latest_checkpoint("card-a")
        after_comments = st.pending_comments()
    finally:
        release.set()
        st.close()

    assert during.error is None
    assert during.value == (None, [])
    assert after_checkpoint is not None and after_checkpoint.seq == 0
    assert [comment.key for comment in after_comments] == ["k1"]


def test_a_read_sees_the_callers_own_completed_write(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        saved = st.save_checkpoint(
            "card-a", workflow="task", digest="d", reason="turn", agent={}, saved_at=NOW
        )
        found = st.latest_checkpoint("card-a")
    finally:
        st.close()

    assert found == saved


def _main_file_of(conn: sqlite3.Connection) -> str:
    return next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")


def test_reads_use_the_read_connection_and_writes_the_writing_connection(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        reader = st.read_connection
        again = st.read_connection
        files = (_main_file_of(reader), _main_file_of(st.connection))
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("INSERT INTO meta (key, value) VALUES ('refused', 'v')")
    finally:
        st.close()

    assert reader is not st.connection
    assert again is reader
    assert files[0] == files[1] != ""
    assert reader.row_factory is sqlite3.Row


_READS = [
    pytest.param(lambda st: st.load_run(RUN_A), id="load_run"),
    pytest.param(lambda st: st.latest_checkpoint("card-a"), id="latest_checkpoint"),
    pytest.param(lambda st: st.latest_turn_checkpoint("card-a"), id="latest_turn_checkpoint"),
    pytest.param(
        lambda st: st.latest_open_checkpoint("card-a", "task"), id="latest_open_checkpoint"
    ),
    pytest.param(lambda st: st.checkpoint_cards(RUN_A), id="checkpoint_cards"),
    pytest.param(lambda st: st.pending_comments(), id="pending_comments"),
    pytest.param(lambda st: st.pending_controls("t1"), id="pending_controls"),
]


@pytest.mark.parametrize("read", _READS)
def test_the_writer_is_not_used_by_reads(repo, read):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        with _Gate(st):
            call = _Caller(read, st).wait()
    finally:
        st.close()

    assert call.error is None


def test_close_joins_the_writer_and_closes_both_connections(repo):
    st = store_writer.Store.open(repo, RUN_A)
    st._submit(lambda conn: None, operation="start")
    st.latest_checkpoint("card-a")
    (writer,) = _writer_threads(RUN_A)
    reader = st.read_connection

    st.close()

    assert writer.is_alive() is False
    with pytest.raises(sqlite3.ProgrammingError):
        st.connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        reader.execute("SELECT 1")


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda st: st.beat("t1", NOW), id="write"),
        pytest.param(lambda st: st.latest_checkpoint("card-a"), id="read"),
    ],
)
def test_a_write_or_read_after_close_raises_programming_error(repo, call):
    st = store_writer.Store.open(repo, RUN_A)
    st.close()

    with pytest.raises(sqlite3.ProgrammingError):
        call(st)

    assert _writer_threads(RUN_A) == []
    assert st._reader is None


def test_the_first_read_of_a_store_over_an_in_memory_connection_raises_value_error(repo):
    st = store_writer.Store(
        sqlite3.connect(":memory:", check_same_thread=False),
        store_journal.Journal(RUN_A),
        1,
    )
    try:
        with pytest.raises(ValueError, match="in-memory"):
            st.latest_checkpoint("card-a")
    finally:
        st.close()


# -- writes as jobs ------------------------------------------------------------


def _run(repo: Path) -> models.Run:
    return models.Run(
        id=RUN_A,
        workflow="milestone",
        repo_dir=repo,
        base_branch="main",
        branch_prefix="m1/",
        status="started",
        started_at=NOW,
    )


def _attempt(repo: Path, n: int) -> models.Attempt:
    return models.Attempt(
        n=n,
        dispatch=models.Dispatch(
            harness="claude",
            model="sonnet",
            role="coder",
            cwd=repo,
            prompt_path=repo / "prompt.txt",
            result_path=repo / "result.json",
        ),
    )


def _count(st: store_writer.Store, table: str) -> int:
    return st.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_no_writer_thread_exists_until_the_first_write(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.latest_checkpoint("card-a")
        before = _writer_threads(RUN_A)
        st.beat("t1", NOW)
        after = _writer_threads(RUN_A)
        alive = [thread.is_alive() for thread in after]
    finally:
        st.close()

    assert before == []
    assert alive == [True]


def test_a_write_from_inside_a_job_raises_instead_of_deadlocking(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        call = _Caller(
            st._submit, lambda conn: st.beat("t1", NOW), operation="nested"
        ).wait()
    finally:
        st.close()

    assert isinstance(call.error, RuntimeError)


def test_after_commit_runs_on_the_writer_thread_before_the_next_job(repo):
    def steal(conn):
        conn.execute("DELETE FROM run_leases WHERE run_id = ?", (RUN_A,))

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with _Gate(st):
            taking = _Caller(
                st.take_lease, token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True
            )
            _wait_enqueued(st, 1)
            stealing = _Caller(st._submit, steal, operation="steal")
            _wait_enqueued(st, 2)
            recording = _Caller(
                st.record_story,
                models.StoryRun(card_id="story-a", title="Story", level=0, status="started"),
            )
            _wait_enqueued(st, 3)
        for caller in (taking, stealing, recording):
            caller.wait()
    finally:
        st.close()

    assert taking.error is None
    assert stealing.error is None
    # Fenced by "t1": the bind ran before the record's job started.
    assert isinstance(recording.error, store_leases.LeaseLostError)


# -- records: one event and its row in one transaction, the line after -------


_STORY = models.StoryRun(card_id="story-a", title="Fundações — ü", level=0, status="started")
_SUBTASK = models.SubtaskRun(
    card_id="card-a", branch="m1/task-card-a", base_branch="main", status="started"
)
_PHASE = models.PhaseRun(name="implement", kind="agent", status="started", started_at=NOW)


def _events(st: store_writer.Store, run_id: str = RUN_A) -> list[store_events.EventRow]:
    """`run_id`'s committed events, in `seq` order, read on `st`'s read connection."""
    return store_events.read(st.read_connection, run_id=run_id)


def _file_texts(st: store_writer.Store) -> list[str]:
    """`st`'s run's journal file, one raw text line per entry, in file order."""
    if not st.journal.path.exists():
        return []
    return st.journal.path.read_text(encoding="utf-8").splitlines()


def _line_text(event: store_events.EventRow) -> str:
    """The text of the journal line `event` is mirrored to."""
    return json.dumps(
        {
            "seq": event.run_seq,
            "ts": event.ts,
            "run_id": event.run_id,
            "event": event.kind,
            "story": event.story_id,
            "card": event.card_id,
            "phase": event.phase,
            "attempt": event.attempt,
            "payload": event.payload,
        },
        sort_keys=True,
    )


_RECORDS = [
    pytest.param(
        lambda st, repo: st.record_run(_run(repo)),
        "run_upsert",
        (None, None, None, None),
        lambda repo: _run(repo).model_dump(mode="json", exclude={"stories"}),
        "runs",
        id="run",
    ),
    pytest.param(
        lambda st, repo: st.record_story(_STORY),
        "story_upsert",
        ("story-a", None, None, None),
        lambda repo: _STORY.model_dump(mode="json", exclude={"subtasks"}),
        "stories",
        id="story",
    ),
    pytest.param(
        lambda st, repo: st.record_subtask("story-a", _SUBTASK),
        "subtask_upsert",
        ("story-a", "card-a", None, None),
        lambda repo: _SUBTASK.model_dump(mode="json", exclude={"phases"}),
        "subtasks",
        id="subtask",
    ),
    pytest.param(
        lambda st, repo: st.record_phase("story-a", "card-a", _PHASE),
        "phase_upsert",
        ("story-a", "card-a", "implement", None),
        lambda repo: _PHASE.model_dump(mode="json", exclude={"attempts"}),
        "phases",
        id="phase",
    ),
    pytest.param(
        lambda st, repo: st.record_attempt("story-a", "card-a", "implement", _attempt(repo, 1)),
        "attempt_upsert",
        ("story-a", "card-a", "implement", 1),
        lambda repo: _attempt(repo, 1).model_dump(mode="json"),
        "attempts",
        id="attempt",
    ),
]


@pytest.mark.parametrize(("record", "kind", "coordinates", "payload", "table"), _RECORDS)
def test_a_record_commits_its_event_and_its_row_together(
    repo, record, kind, coordinates, payload, table
):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        line = record(st, repo)
        events = _events(st)
        rows = st.read_connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        project_id = st.project_id
    finally:
        st.close()

    (event,) = events
    assert (event.kind, event.source, event.project_id, event.schema) == (
        kind,
        "live",
        project_id,
        1,
    )
    assert (event.story_id, event.card_id, event.phase, event.attempt) == coordinates
    assert event.payload == payload(repo)
    assert event.run_seq == line.seq == 1
    assert rows == 1


def test_a_failed_row_write_leaves_no_event_and_consumes_no_run_seq(repo, monkeypatch):
    st = store_writer.Store.open(repo, RUN_A)
    real = st._write_story_row

    def failing_row_write(*args):
        raise sqlite3.OperationalError("disk I/O error")

    try:
        st.record_run(_run(repo))
        monkeypatch.setattr(st, "_write_story_row", failing_row_write)
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            st.record_story(_STORY)
        kinds = [event.kind for event in _events(st)]
        texts = _file_texts(st)
        monkeypatch.setattr(st, "_write_story_row", real)
        line = st.record_story(_STORY)
    finally:
        st.close()

    assert kinds == ["run_upsert"]
    assert [json.loads(text)["seq"] for text in texts] == [1]
    assert line.seq == 2


def test_the_file_line_equals_the_event(repo):
    # Review Focus 5: `_STORY`'s title is not ASCII.
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.record_run(_run(repo))
        st.record_story(_STORY)
        st.record_subtask("story-a", _SUBTASK)
        st.record_phase("story-a", "card-a", _PHASE)
        line = st.record_attempt("story-a", "card-a", "implement", _attempt(repo, 1))
        events = _events(st)
        texts = _file_texts(st)
    finally:
        st.close()

    assert [event.kind for event in events] == [
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
        "phase_upsert",
        "attempt_upsert",
    ]
    assert texts == [_line_text(event) for event in events]
    assert line == store_journal.Journal(RUN_A).read()[-1]


def test_record_returns_the_line_numbered_by_the_events_table(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        lines = [
            st.record_run(_run(repo)),
            st.record_story(_STORY),
            st.record_subtask("story-a", _SUBTASK),
        ]
        events = _events(st)
    finally:
        st.close()

    assert [line.seq for line in lines] == [event.run_seq for event in events] == [1, 2, 3]
    for line, event in zip(lines, events, strict=True):
        stamped = datetime.fromisoformat(event.ts)
        assert stamped.utcoffset() == timedelta(0)
        assert stamped == line.ts


def test_each_run_numbers_its_own_events_from_one(repo):
    # Review Focus 3: two runs share the machine-wide file.
    first = store_writer.Store.open(repo, RUN_A)
    second = store_writer.Store.open(repo, RUN_B)
    try:
        first.record_run(_run(repo))
        first.record_story(_STORY)
        line = second.record_run(_run(repo).model_copy(update={"id": RUN_B}))
        events = _events(second, RUN_B)
    finally:
        first.close()
        second.close()

    assert line.seq == 1
    assert [(event.run_id, event.run_seq) for event in events] == [(RUN_B, 1)]


def test_a_busy_re_run_of_a_record_writes_one_event_and_one_line(repo, monkeypatch):
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    st = store_writer.Store.open(repo, RUN_A)
    real = st._write_run_row
    calls: list[int] = []

    def busy_once(*args):
        calls.append(1)
        if len(calls) == 1:
            raise _busy()
        return real(*args)

    monkeypatch.setattr(st, "_write_run_row", busy_once)
    try:
        line = st.record_run(_run(repo))
        events = _events(st)
        lines = eventlines.run_lines(st.run_id)
        runs = _count(st, "runs")
    finally:
        st.close()

    assert calls == [1, 1]
    assert [event.run_seq for event in events] == [1]
    assert lines == [line]
    assert runs == 1


def test_the_mirror_runs_after_the_commit_on_the_writer_thread(repo, monkeypatch):
    st = store_writer.Store.open(repo, RUN_A)
    real = st.journal.mirror
    seen: list[tuple[str, list[int]]] = []

    def spying_mirror(line):
        committed = [event.run_seq for event in _events(st)]
        seen.append((threading.current_thread().name, committed))
        real(line)

    monkeypatch.setattr(st.journal, "mirror", spying_mirror)
    try:
        st.record_run(_run(repo))
    finally:
        st.close()

    assert seen == [(f"am-store-writer-{RUN_A}", [1])]


def test_a_failed_file_mirror_does_not_fail_the_record(repo, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="agent_manager.store.writer")
    st = store_writer.Store.open(repo, RUN_A)
    real = st.journal.mirror

    def full_disk(line):
        raise OSError("disk full")

    try:
        monkeypatch.setattr(st.journal, "mirror", full_disk)
        line = st.record_run(_run(repo))
        events = _events(st)
        runs = _count(st, "runs")
        texts = _file_texts(st)
        monkeypatch.setattr(st.journal, "mirror", real)
        story_line = st.record_story(_STORY)
    finally:
        st.close()

    assert (line.seq, line.event) == (1, "run_upsert")
    assert [event.run_seq for event in events] == [1]
    assert runs == 1
    assert texts == []
    warnings = [record for record in caplog.records if record.name == "agent_manager.store.writer"]
    assert [record.levelno for record in warnings] == [logging.WARNING]
    (warning,) = warnings
    assert warning.args == (RUN_A, 1, "run_upsert")
    assert RUN_A in warning.getMessage() and "run_upsert" in warning.getMessage()
    assert warning.exc_info is not None and isinstance(warning.exc_info[1], OSError)
    assert story_line.seq == 2


def test_a_base_exception_from_the_mirror_reaches_the_caller_and_keeps_the_commit(
    repo, monkeypatch
):
    # Review Focus 1: only `Exception`s are swallowed.
    st = store_writer.Store.open(repo, RUN_A)

    def interrupted(line):
        raise KeyboardInterrupt

    monkeypatch.setattr(st.journal, "mirror", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            st.record_run(_run(repo))
        events = _events(st)
    finally:
        st.close()

    assert [event.kind for event in events] == ["run_upsert"]


def test_a_canceled_status_is_stored_and_mirrored_verbatim(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.record_run(_run(repo).model_copy(update={"status": models.CANCELED}))
        (event,) = _events(st)
        (text,) = _file_texts(st)
    finally:
        st.close()

    assert event.payload["status"] == models.CANCELED
    assert json.loads(text)["payload"]["status"] == models.CANCELED


def test_a_legacy_cancelled_event_mirrors_verbatim(repo):
    # Models canonicalise on validation, so only an event already stored with
    # the legacy spelling can carry it: the event -> line -> file layer keeps it.
    st = store_writer.Store.open(repo, RUN_A)
    try:
        event = st._submit(
            lambda conn: store_events.insert(
                conn,
                project_id=st.project_id,
                run_id=RUN_A,
                ts=store_journal.ts_text(NOW),
                kind="run_upsert",
                payload={"status": models.LEGACY_CANCELED},
                source="live",
            ),
            operation="seed",
        )
        line = store_events.journal_line(event)
        st.journal.mirror(line)
        (stored,) = _events(st)
        (text,) = _file_texts(st)
    finally:
        st.close()

    assert line.payload == {"status": models.LEGACY_CANCELED}
    assert stored.payload == {"status": models.LEGACY_CANCELED}
    assert json.loads(text)["payload"] == {"status": models.LEGACY_CANCELED}


def test_the_mirrored_line_is_journal_line_of_the_committed_event(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        returned = st.record_run(_run(repo))
        (event,) = _events(st)
        (text,) = _file_texts(st)
    finally:
        st.close()

    assert returned == store_events.journal_line(event)
    assert store_journal.JournalLine.model_validate_json(text) == store_events.journal_line(event)


# -- batches: coalesced jobs share one transaction ---------------------------


def _traced(st: store_writer.Store) -> list[str]:
    """Every SQL statement `st`'s writing connection runs from now on, in order."""
    statements: list[str] = []
    st.connection.set_trace_callback(statements.append)
    return statements


def _begins(statements: list[str]) -> int:
    return sum(1 for statement in statements if statement == "BEGIN IMMEDIATE")


def _savepoints(statements: list[str]) -> int:
    return sum(1 for statement in statements if statement.startswith("SAVEPOINT"))


def _inserting(key: str):
    """A job body that inserts `key` into `meta` and returns it."""

    def body(conn: sqlite3.Connection) -> str:
        _insert_meta(conn, key)
        return key

    return body


def _coalesced(st: store_writer.Store, body, operation: str = "batch"):
    """A call that submits `body` to `st` as a job that may batch."""
    return lambda: st._submit(body, operation=operation, coalesce=True)


def _enqueue_in_order(st: store_writer.Store, calls) -> list[_Caller]:
    """Start a `_Caller` per call, each enqueued before the next starts.

    Call it while a `_Gate` holds the writer: the queue then holds the jobs
    in exactly this order.
    """
    callers: list[_Caller] = []
    for call in calls:
        callers.append(_Caller(call))
        _wait_enqueued(st, len(callers))
    return callers


def _wait_all(callers: list[_Caller]) -> None:
    for caller in callers:
        caller.wait()


def test_coalesced_jobs_queued_together_commit_in_one_transaction(repo):
    st = store_writer.Store.open(repo, RUN_A)
    statements = _traced(st)
    try:
        with _Gate(st):
            callers = _enqueue_in_order(
                st, [_coalesced(st, _inserting(f"batch-{n}")) for n in range(3)]
            )
        _wait_all(callers)
        keys = _meta_keys(st.connection, "batch-")
    finally:
        st.close()

    assert [(caller.value, caller.error) for caller in callers] == [
        (f"batch-{n}", None) for n in range(3)
    ]
    assert keys == ["batch-0", "batch-1", "batch-2"]
    assert _begins(statements) == 2  # the gate's, then the batch's
    assert _savepoints(statements) == 3


def test_no_caller_in_a_batch_returns_before_the_commit(repo):
    b_entered = threading.Event()
    b_released = threading.Event()

    def job_b(conn):
        _insert_meta(conn, "batch-b")
        b_entered.set()
        assert b_released.wait(TIMEOUT), "job B was never released"
        return "batch-b"

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with _Gate(st):
            a, b = _enqueue_in_order(
                st, [_coalesced(st, _inserting("batch-a")), _coalesced(st, job_b)]
            )
        assert b_entered.wait(TIMEOUT), "job B never started"
        a.thread.join(0.05)
        a_still_waiting = a.thread.is_alive()
        seen_before_commit = _meta_keys(st.read_connection, "batch-")
        b_released.set()
        _wait_all([a, b])
        keys = _meta_keys(st.connection, "batch-")
    finally:
        b_released.set()
        st.close()

    assert a_still_waiting
    assert seen_before_commit == []
    assert (a.value, a.error) == ("batch-a", None)
    assert (b.value, b.error) == ("batch-b", None)
    assert keys == ["batch-a", "batch-b"]


def test_a_job_that_cannot_batch_runs_alone_in_queue_order(repo):
    st = store_writer.Store.open(repo, RUN_A)
    statements = _traced(st)
    try:
        with _Gate(st):
            callers = _enqueue_in_order(
                st,
                [
                    _coalesced(st, _inserting("order-c1")),
                    _coalesced(st, _inserting("order-c2")),
                    lambda: st._submit(_inserting("order-p"), operation="plain"),
                    _coalesced(st, _inserting("order-c3")),
                ],
            )
        _wait_all(callers)
        keys = _meta_keys(st.connection, "order-")
    finally:
        st.close()

    assert [caller.error for caller in callers] == [None] * 4
    assert keys == ["order-c1", "order-c2", "order-p", "order-c3"]
    # The gate, {c1, c2}, p alone, c3 alone.
    assert _begins(statements) == 4


def test_a_coalesced_job_with_after_commit_runs_alone(repo):
    order: list[str] = []

    def first(conn):
        order.append("body 1")
        return "one"

    def second(conn):
        order.append("body 2")
        return "two"

    st = store_writer.Store.open(repo, RUN_A)
    statements = _traced(st)
    try:
        with _Gate(st):
            callers = _enqueue_in_order(
                st,
                [
                    lambda: st._submit(
                        first,
                        operation="first",
                        coalesce=True,
                        after_commit=lambda: order.append("after_commit 1"),
                    ),
                    _coalesced(st, second),
                ],
            )
        _wait_all(callers)
    finally:
        st.close()

    assert [(caller.value, caller.error) for caller in callers] == [
        ("one", None),
        ("two", None),
    ]
    assert order == ["body 1", "after_commit 1", "body 2"]
    assert _begins(statements) == 3  # the gate's, then one per job


def test_close_finishes_a_queued_batch_then_stops(repo):
    st = store_writer.Store.open(repo, RUN_A)
    gate = _Gate(st)
    try:
        callers = _enqueue_in_order(
            st, [_coalesced(st, _inserting(f"closing-{n}")) for n in range(3)]
        )
        closer = _Caller(st.close)
        _wait_enqueued(st, 4)
        gate.release()
        closer.wait()
        _wait_all(callers)
    finally:
        gate.release()
        st.close()

    observer = store_db.open_db(repo)
    try:
        keys = _meta_keys(observer, "closing-")
    finally:
        observer.close()

    assert [(caller.value, caller.error) for caller in callers] == [
        (f"closing-{n}", None) for n in range(3)
    ]
    assert closer.error is None
    assert keys == ["closing-0", "closing-1", "closing-2"]
    assert _writer_threads(RUN_A) == []


def test_a_batch_busy_past_the_budget_fails_every_caller(repo, monkeypatch):
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    calls: list[int] = []

    def always_busy(conn):
        calls.append(1)
        _insert_meta(conn, "budget-b")
        raise _busy()

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with _Gate(st):
            a, b = _enqueue_in_order(
                st,
                [
                    _coalesced(st, _inserting("budget-a"), operation="op_a"),
                    _coalesced(st, always_busy, operation="op_b"),
                ],
            )
        _wait_all([a, b])
        after = st._submit(_inserting("budget-after"), operation="after")
        keys = _meta_keys(st.connection, "budget-")
    finally:
        st.close()

    assert isinstance(a.error, store_db.StoreBusyError)
    assert b.error is a.error
    assert a.error.operation == "op_a+op_b"
    assert a.error.attempts == store_db.RETRY_ATTEMPTS
    assert len(calls) == store_db.RETRY_ATTEMPTS
    assert keys == ["budget-after"]
    assert after == "budget-after"


def test_a_failure_outside_every_savepoint_fails_the_whole_batch(repo, monkeypatch):
    # Review Focus 1: the COMMIT itself refuses.
    failure = RuntimeError("the commit is refused")
    real = store_db.immediate

    @contextlib.contextmanager
    def refusing_commit(conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        finally:
            conn.rollback()
        raise failure

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with _Gate(st):
            a, b = _enqueue_in_order(
                st,
                [
                    _coalesced(st, _inserting("whole-a")),
                    _coalesced(st, _inserting("whole-b")),
                ],
            )
            # The gate is already inside the real `immediate`; only the batch
            # meets the refusing one.
            monkeypatch.setattr(store_db, "immediate", refusing_commit)
        _wait_all([a, b])
        monkeypatch.setattr(store_db, "immediate", real)
        after = st._submit(_inserting("whole-after"), operation="after")
        keys = _meta_keys(st.connection, "whole-")
    finally:
        st.close()

    assert a.error is failure
    assert b.error is failure
    assert keys == ["whole-after"]
    assert after == "whole-after"


def _raise_value_error(conn: sqlite3.Connection, raised: list[BaseException]) -> None:
    error = ValueError("job B refuses")
    raised.append(error)
    raise error


def _raise_integrity_error(conn: sqlite3.Connection, raised: list[BaseException]) -> None:
    try:
        _insert_meta(conn, "batch-b")  # a duplicate of the key job B just inserted
    except sqlite3.IntegrityError as error:
        raised.append(error)
        raise


def _raise_operational_error(
    conn: sqlite3.Connection, raised: list[BaseException]
) -> None:
    try:
        conn.execute("SELECT * FROM no_such_table")  # not busy, so not retried
    except sqlite3.OperationalError as error:
        raised.append(error)
        raise


@pytest.mark.parametrize(
    "fail",
    [_raise_value_error, _raise_integrity_error, _raise_operational_error],
    ids=["value-error", "integrity-error", "operational-error"],
)
def test_a_job_raising_in_a_batch_fails_only_its_own_caller(repo, fail):
    raised: list[BaseException] = []

    def job_b(conn):
        _insert_meta(conn, "batch-b")
        fail(conn, raised)

    st = store_writer.Store.open(repo, RUN_A)
    statements = _traced(st)
    try:
        with _Gate(st):
            a, b, c = _enqueue_in_order(
                st,
                [
                    _coalesced(st, _inserting("batch-a")),
                    _coalesced(st, job_b),
                    _coalesced(st, _inserting("batch-c")),
                ],
            )
        _wait_all([a, b, c])
        keys = _meta_keys(st.connection, "batch-")
    finally:
        st.close()

    assert len(raised) == 1
    assert b.error is raised[0]
    assert (a.value, a.error) == ("batch-a", None)
    assert (c.value, c.error) == ("batch-c", None)
    assert keys == ["batch-a", "batch-c"]
    assert _begins(statements) == 2  # the gate's, then the batch's


@pytest.mark.parametrize(
    "error",
    [store_leases.LeaseLostError(RUN_A, None), KeyboardInterrupt()],
    ids=["lease-lost", "keyboard-interrupt"],
)
def test_a_base_exception_in_a_batch_reaches_only_its_caller(repo, error):
    def job_b(conn):
        _insert_meta(conn, "batch-b")
        raise error

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with _Gate(st):
            a, b, c = _enqueue_in_order(
                st,
                [
                    _coalesced(st, _inserting("batch-a")),
                    _coalesced(st, job_b),
                    _coalesced(st, _inserting("batch-c")),
                ],
            )
        _wait_all([a, b, c])
        after = st._submit(lambda conn: "still serving", operation="after")
        keys = _meta_keys(st.connection, "batch-")
    finally:
        st.close()

    assert b.error is error
    assert (a.value, a.error) == ("batch-a", None)
    assert (c.value, c.error) == ("batch-c", None)
    assert keys == ["batch-a", "batch-c"]
    assert after == "still serving"


def test_a_busy_job_re_runs_the_whole_batch(repo, monkeypatch):
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    a_calls: list[int] = []
    b_calls: list[int] = []

    def job_a(conn):
        a_calls.append(len(a_calls) + 1)
        _insert_meta(conn, "rerun-a")
        return f"a{len(a_calls)}"

    def job_b(conn):
        b_calls.append(len(b_calls) + 1)
        _insert_meta(conn, "rerun-b")
        if len(b_calls) == 1:
            raise _busy()
        return f"b{len(b_calls)}"

    st = store_writer.Store.open(repo, RUN_A)
    statements = _traced(st)
    try:
        with _Gate(st):
            a, b = _enqueue_in_order(st, [_coalesced(st, job_a), _coalesced(st, job_b)])
        _wait_all([a, b])
        keys = _meta_keys(st.connection, "rerun-")
    finally:
        st.close()

    assert a_calls == [1, 2]
    assert b_calls == [1, 2]
    assert (a.value, a.error) == ("a2", None)
    assert (b.value, b.error) == ("b2", None)
    assert keys == ["rerun-a", "rerun-b"]
    assert _begins(statements) == 3  # the gate's, then two attempts


def test_outcomes_come_from_the_batchs_final_attempt(repo, monkeypatch):
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    a_calls: list[int] = []
    b_calls: list[int] = []

    def job_a(conn):
        a_calls.append(1)
        _insert_meta(conn, "final-a")
        if len(a_calls) == 1:
            raise ValueError("attempt 1 only")
        return "a2"

    def job_b(conn):
        b_calls.append(1)
        _insert_meta(conn, "final-b")
        if len(b_calls) == 1:
            raise _busy()
        return "b2"

    st = store_writer.Store.open(repo, RUN_A)
    try:
        with _Gate(st):
            a, b = _enqueue_in_order(st, [_coalesced(st, job_a), _coalesced(st, job_b)])
        _wait_all([a, b])
        keys = _meta_keys(st.connection, "final-")
    finally:
        st.close()

    assert (a.value, a.error) == ("a2", None)
    assert (b.value, b.error) == ("b2", None)
    assert keys == ["final-a", "final-b"]


def test_a_fenced_job_in_a_batch_is_fenced_in_its_own_savepoint(repo):
    # Review Focus 5.
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        thief = store_db.open_db(repo)
        try:
            thief.execute("DELETE FROM run_leases WHERE run_id = ?", (RUN_A,))
            thief.commit()
        finally:
            thief.close()

        with _Gate(st):
            a, b, c = _enqueue_in_order(
                st,
                [
                    _coalesced(st, _inserting("fence-a")),
                    lambda: st._submit(
                        _inserting("fence-b"),
                        operation="fenced",
                        fenced=True,
                        coalesce=True,
                    ),
                    _coalesced(st, _inserting("fence-c")),
                ],
            )
        _wait_all([a, b, c])
        keys = _meta_keys(st.connection, "fence-")
    finally:
        st.close()

    assert isinstance(b.error, store_leases.LeaseLostError)
    assert (a.value, a.error) == ("fence-a", None)
    assert (c.value, c.error) == ("fence-c", None)
    assert keys == ["fence-a", "fence-c"]


LATER = datetime(2026, 10, 7, 12, 5, tzinfo=timezone.utc)
LATEST = datetime(2026, 10, 7, 12, 10, tzinfo=timezone.utc)


def _take_t1(st: store_writer.Store) -> None:
    st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)


def test_heartbeat_writes_waiting_together_share_one_transaction(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        _take_t1(st)
        statements = _traced(st)
        with _Gate(st):
            statements.clear()
            callers = _enqueue_in_order(
                st,
                [
                    lambda: st.beat("t1", LATER),
                    lambda: st.close_window("t1"),
                    lambda: st.set_lease_holder("t1", pid=2, host="h2"),
                ],
            )
        _wait_all(callers)
        lease = store_leases.read_lease(st.connection, RUN_A)
    finally:
        st.close()

    assert [(caller.value, caller.error) for caller in callers] == [(None, None)] * 3
    assert lease is not None
    assert (lease.heartbeat_at, lease.accepting, lease.pid, lease.host) == (
        LATER,
        False,
        2,
        "h2",
    )
    assert _begins(statements) == 1


def test_record_jobs_never_batch(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.record_run(_run(repo))
        _take_t1(st)
        statements = _traced(st)
        with _Gate(st):
            statements.clear()
            callers = _enqueue_in_order(
                st,
                [
                    lambda: st.beat("t1", LATER),
                    lambda: st.record_story(
                        models.StoryRun(
                            card_id="story-a", title="Story", level=0, status="started"
                        )
                    ),
                    lambda: st.beat("t1", LATEST),
                ],
            )
        _wait_all(callers)
        stories = [line.event for line in eventlines.run_lines(st.run_id) if line.event == "story_upsert"]
    finally:
        st.close()

    assert [caller.error for caller in callers] == [None] * 3
    assert _begins(statements) == 3
    assert stories == ["story_upsert"]


def test_two_beats_in_one_batch_leave_the_later_heartbeat(repo):
    # Review Focus 3: rows are written in queue order.
    st = store_writer.Store.open(repo, RUN_A)
    try:
        _take_t1(st)
        statements = _traced(st)
        with _Gate(st):
            statements.clear()
            callers = _enqueue_in_order(
                st, [lambda: st.beat("t1", LATER), lambda: st.beat("t1", LATEST)]
            )
        _wait_all(callers)
        lease = store_leases.read_lease(st.connection, RUN_A)
    finally:
        st.close()

    assert [caller.error for caller in callers] == [None, None]
    assert lease is not None and lease.heartbeat_at == LATEST
    assert _begins(statements) == 1


def test_a_stale_token_heartbeat_in_a_batch_changes_nothing_and_fails_no_one(repo):
    # Review Focus 4: a heartbeat thread still running after a takeover.
    st = store_writer.Store.open(repo, RUN_A)
    try:
        _take_t1(st)
        statements = _traced(st)
        with _Gate(st):
            statements.clear()
            callers = _enqueue_in_order(
                st,
                [
                    lambda: st.close_window("stale"),
                    lambda: st.beat("t1", LATER),
                    lambda: st.set_lease_holder("stale", pid=9, host="other"),
                ],
            )
        _wait_all(callers)
        lease = store_leases.read_lease(st.connection, RUN_A)
    finally:
        st.close()

    assert [(caller.value, caller.error) for caller in callers] == [(None, None)] * 3
    assert lease is not None
    assert (lease.token, lease.heartbeat_at, lease.accepting, lease.pid, lease.host) == (
        "t1",
        LATER,
        True,
        1,
        "h",
    )
    assert _begins(statements) == 1


def test_heartbeats_racing_records_all_land(repo):
    # Review Focus 2: the lease heartbeat beats while lanes record.
    st = store_writer.Store.open(repo, RUN_A)
    start = threading.Barrier(4)

    def work(worker: int) -> None:
        start.wait(TIMEOUT)
        for i in range(10):
            st.beat("t1", NOW)
            st.record_attempt(
                "story-a", "card-a", "implement", _attempt(repo, worker * 10 + i + 1)
            )

    try:
        _take_t1(st)
        callers = [_Caller(work, worker) for worker in range(4)]
        _wait_all(callers)
        lines = [line for line in eventlines.run_lines(st.run_id) if line.event == "attempt_upsert"]
        rows = _count(st, "attempts")
        events = _events(st)
        texts = _file_texts(st)
    finally:
        st.close()

    assert [caller.error for caller in callers] == [None] * 4
    assert len(lines) == 40
    assert len({line.seq for line in lines}) == 40
    assert rows == 40
    # `_take_t1`'s `lease_acquired` is run_seq 1 and is never mirrored.
    assert [(event.kind, event.run_seq) for event in events[:1]] == [("lease_acquired", 1)]
    attempts = events[1:]
    assert [event.run_seq for event in attempts] == list(range(2, 42))
    assert texts == [_line_text(event) for event in attempts]


def test_a_lost_lease_writes_no_line_no_event_and_no_row(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.record_run(_run(repo))
        st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        thief = store_db.open_db(repo)
        try:
            thief.execute("DELETE FROM run_leases WHERE run_id = ?", (RUN_A,))
            thief.commit()
        finally:
            thief.close()

        with pytest.raises(store_leases.LeaseLostError):
            st.record_story(
                models.StoryRun(card_id="story-a", title="Story", level=0, status="started")
            )
        events = [line.event for line in eventlines.run_lines(st.run_id)]
        kinds = [event.kind for event in _events(st)]
        stories = _count(st, "stories")
    finally:
        st.close()

    assert events == ["run_upsert"]
    # The take's own event committed; the lost store's record added nothing.
    assert kinds == ["run_upsert", "lease_acquired"]
    assert stories == 0


def test_rebuild_from_events_is_one_transaction_without_a_token(repo, monkeypatch):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        _record_tree(st, repo)
        before = store_queries.load_run(st.connection, RUN_A)

        def exploding(*args):
            raise RuntimeError("the rewrite fails after the delete")

        monkeypatch.setattr(st, "_write_story_row", exploding)
        with pytest.raises(RuntimeError, match="after the delete"):
            st.rebuild_from_events(RUN_A)
        after = store_queries.load_run(st.connection, RUN_A)
    finally:
        st.close()

    assert before is not None
    assert after == before


def test_concurrent_record_calls_all_land(repo):
    st = store_writer.Store.open(repo, RUN_A)
    start = threading.Barrier(4)

    def record(worker: int) -> None:
        start.wait(TIMEOUT)
        for i in range(10):
            st.record_attempt("story-a", "card-a", "implement", _attempt(repo, worker * 10 + i + 1))

    try:
        callers = [_Caller(record, worker) for worker in range(4)]
        for caller in callers:
            caller.wait()
        lines = [line for line in eventlines.run_lines(st.run_id) if line.event == "attempt_upsert"]
        rows = _count(st, "attempts")
    finally:
        st.close()

    assert [caller.error for caller in callers] == [None] * 4
    assert len(lines) == 40
    assert len({line.seq for line in lines}) == 40
    assert rows == 40


def test_a_busy_re_run_of_take_lease_still_claims_every_key(repo, monkeypatch):
    # Review Focus 1: a generator of claims survives the job being re-run.
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    real = store_leases.take_lease
    calls: list[int] = []

    def busy_once(conn, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            list(kwargs["claims"])
            raise _busy()
        return real(conn, *args, **kwargs)

    monkeypatch.setattr(store_leases, "take_lease", busy_once)
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.take_lease(
            token="t1",
            pid=1,
            host="h",
            now=NOW,
            is_live=lambda row: True,
            claims=(key for key in ["card:a", "card:b"]),
        )
        claimed = sorted(row[0] for row in st.connection.execute("SELECT key FROM run_claims"))
    finally:
        st.close()

    assert calls == [1, 1]
    assert claimed == ["card:a", "card:b"]


def test_an_after_commit_failure_reaches_the_caller_and_keeps_the_commit(repo, monkeypatch):
    # Review Focus 4.
    ran_on: list[str] = []
    st = store_writer.Store.open(repo, RUN_A)

    def failing_bind(token):
        ran_on.append(threading.current_thread().name)
        raise OSError("bind failed")

    monkeypatch.setattr(st, "bind_lease", failing_bind)
    try:
        with pytest.raises(OSError, match="bind failed"):
            st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        lease = store_leases.read_lease(st.connection, RUN_A)
        after = st._submit(lambda conn: "next", operation="next")
    finally:
        st.close()

    assert ran_on == [f"am-store-writer-{RUN_A}"]
    assert lease is not None and lease.token == "t1"
    assert after == "next"


def test_taking_or_adopting_a_lease_never_reads_the_journal(repo, monkeypatch):
    # Records are numbered by the events table, so a new lease holder has no
    # file `seq` to re-read.
    st = store_writer.Store.open(repo, RUN_A)

    def unreadable(*args, **kwargs):
        raise AssertionError("the journal was read")

    monkeypatch.setattr(st.journal, "last_seq", unreadable)
    try:
        st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        st.bind_lease(None)
        held = st.adopt_lease("t1")
        line = st.record_run(_run(repo))
    finally:
        st.close()

    assert held.token == "t1"
    assert line.seq == 2  # the take's `lease_acquired` is run_seq 1
    assert not hasattr(store_journal.Journal, "reseek")


# -- lease and control events (card 1.2.7) -----------------------------------
#
# Each is a live event of the run, with no node coordinates, numbered by the
# events table alongside the node upserts and never mirrored to the file.

_NO_COORDINATES = (None, None, None, None)


def _plant_holder(
    repo: Path,
    *,
    run_id: str = RUN_A,
    token: str = "t0",
    pid: int = 7,
    host: str = "other-box",
    heartbeat_at: datetime = NOW,
) -> None:
    """A `run_leases` row of `run_id`, written on a second connection as
    another process's take would have left it."""
    conn = store_db.open_db(repo)
    try:
        with store_db.immediate(conn):
            project_id = store_projects.resolve(conn, repo, now=NOW)
            conn.execute(
                "INSERT INTO run_leases (project_id, run_id, token, pid, host,"
                " acquired_at, heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    project_id,
                    run_id,
                    token,
                    pid,
                    host,
                    store_db.iso(heartbeat_at),
                    store_db.iso(heartbeat_at),
                ),
            )
    finally:
        conn.close()


def _plant_control(
    repo: Path, command: str = "pause", *, lease: str = "t1"
) -> store_leases.ControlRow:
    """One pending request of `RUN_A`, inserted on a second connection as `am pause` would."""
    conn = store_db.open_db(repo)
    try:
        with store_db.immediate(conn):
            return store_leases.add_control(
                conn,
                RUN_A,
                project_id=store_projects.resolve(conn, repo, now=NOW),
                lease=lease,
                command=command,
                requested_at=NOW,
            )
    finally:
        conn.close()


def _claims(st: store_writer.Store) -> list[tuple[str, str]]:
    """Every committed `run_claims` row as `(key, run_id)`, in key order."""
    return [
        (row[0], row[1])
        for row in st.read_connection.execute(
            "SELECT key, run_id FROM run_claims ORDER BY key"
        )
    ]


def _stamped_between(
    event: store_events.EventRow, before: datetime, after: datetime
) -> bool:
    """Whether `event.ts` is a UTC instant read between `before` and `after`."""
    stamped = datetime.fromisoformat(event.ts)
    return stamped.utcoffset() == timedelta(0) and before <= stamped <= after


def _failing_insert_for(kind: str, error: BaseException):
    """`store_events.insert`, except that an insert of `kind` raises `error`."""
    real = store_events.insert

    def insert(conn, **kwargs):
        if kwargs["kind"] == kind:
            raise error
        return real(conn, **kwargs)

    return insert


def test_mark_control_handled_writes_one_control_handled_event_with_the_row(repo):
    _plant_control(repo, "cancel")
    st = store_writer.Store.open(repo, RUN_A)
    try:
        before = datetime.now(timezone.utc)
        st.mark_control_handled(0, LATER)
        after = datetime.now(timezone.utc)
        requests = store_leases.control_requests(st.read_connection, RUN_A)
        events = _events(st)
        project_id = st.project_id
    finally:
        st.close()

    (event,) = events
    assert (event.kind, event.source, event.project_id, event.schema, event.run_seq) == (
        "control_handled",
        "live",
        project_id,
        1,
        1,
    )
    assert (event.story_id, event.card_id, event.phase, event.attempt) == _NO_COORDINATES
    assert event.payload == {
        "command": "cancel",
        "control_seq": 0,
        "handled_at": LATER.isoformat(),
    }
    assert _stamped_between(event, before, after)
    assert [row.handled_at for row in requests] == [LATER]


def test_mark_control_handled_of_an_unknown_seq_changes_nothing(repo):
    _plant_control(repo)
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.mark_control_handled(5, LATER)
        requests = store_leases.control_requests(st.read_connection, RUN_A)
        events = _events(st)
    finally:
        st.close()

    assert events == []
    assert [row.handled_at for row in requests] == [None]


def test_a_second_mark_control_handled_keeps_the_first_and_writes_no_event(repo):
    _plant_control(repo)
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.mark_control_handled(0, LATER)
        st.mark_control_handled(0, LATEST)
        requests = store_leases.control_requests(st.read_connection, RUN_A)
        events = _events(st)
    finally:
        st.close()

    assert [row.handled_at for row in requests] == [LATER]
    assert [(event.kind, event.payload["handled_at"]) for event in events] == [
        ("control_handled", LATER.isoformat())
    ]


def test_a_failed_control_handled_insert_leaves_the_request_pending(repo, monkeypatch):
    _plant_control(repo)
    monkeypatch.setattr(
        store_events,
        "insert",
        _failing_insert_for("control_handled", sqlite3.OperationalError("disk I/O error")),
    )
    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            st.mark_control_handled(0, LATER)
        requests = store_leases.control_requests(st.read_connection, RUN_A)
        events = _events(st)
    finally:
        st.close()

    assert [row.handled_at for row in requests] == [None]
    assert events == []


def test_a_first_take_lease_writes_one_lease_acquired_event(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        before = datetime.now(timezone.utc)
        taken = st.take_lease(
            token="t1",
            pid=1,
            host="h",
            now=NOW,
            is_live=lambda row: True,
            claims=["card:b", "card:a"],
        )
        after = datetime.now(timezone.utc)
        events = _events(st)
        project_id = st.project_id
    finally:
        st.close()

    assert taken.displaced is None
    (event,) = events
    assert (event.kind, event.source, event.project_id, event.schema, event.run_seq) == (
        "lease_acquired",
        "live",
        project_id,
        1,
        1,
    )
    assert (event.story_id, event.card_id, event.phase, event.attempt) == _NO_COORDINATES
    # The claim keys in the order passed, not sorted.
    assert event.payload == {"token": "t1", "pid": 1, "host": "h", "claims": ["card:b", "card:a"]}
    # `ts` is the wall clock inside the job, not the lease's `now`.
    assert _stamped_between(event, before, after)


def test_a_take_lease_with_no_claims_records_an_empty_claims_list(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        events = _events(st)
    finally:
        st.close()

    (event,) = events
    assert event.payload["claims"] == []


def test_a_take_over_a_dead_holder_writes_lease_taken_over_naming_it(repo):
    _plant_holder(repo, token="t0", pid=7, host="other-box", heartbeat_at=NOW)
    st = store_writer.Store.open(repo, RUN_A)
    try:
        taken = st.take_lease(
            token="t1", pid=1, host="h", now=LATER, is_live=lambda row: False, claims=["card:a"]
        )
        events = _events(st)
    finally:
        st.close()

    assert taken.displaced is not None and taken.displaced.token == "t0"
    (event,) = events
    assert (event.kind, event.run_seq) == ("lease_taken_over", 1)
    assert (event.story_id, event.card_id, event.phase, event.attempt) == _NO_COORDINATES
    # No `claims` key on a takeover.
    assert event.payload == {
        "token": "t1",
        "pid": 1,
        "host": "h",
        "displaced": {"pid": 7, "host": "other-box", "heartbeat_at": NOW.isoformat()},
    }


def test_a_re_take_under_the_same_token_writes_lease_taken_over(repo):
    # Review Focus 1: `LeaseTake` reports the old row as displaced, and the
    # event follows `LeaseTake`.
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        taken = st.take_lease(token="t1", pid=2, host="h2", now=LATER, is_live=lambda row: True)
        events = _events(st)
    finally:
        st.close()

    assert taken.displaced is not None and taken.displaced.token == "t1"
    assert [(event.kind, event.run_seq) for event in events] == [
        ("lease_acquired", 1),
        ("lease_taken_over", 2),
    ]
    assert events[1].payload == {
        "token": "t1",
        "pid": 2,
        "host": "h2",
        "displaced": {"pid": 1, "host": "h", "heartbeat_at": NOW.isoformat()},
    }


def test_a_failed_lease_event_rolls_the_take_back_and_leaves_the_store_unbound(
    repo, monkeypatch
):
    # Review Focus 4.
    monkeypatch.setattr(
        store_events,
        "insert",
        _failing_insert_for("lease_acquired", sqlite3.OperationalError("disk I/O error")),
    )
    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            st.take_lease(
                token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True, claims=["card:a"]
            )
        lease = store_leases.read_lease(st.read_connection, RUN_A)
        claims = _claims(st)
        events = _events(st)
        # Fenced to "t1" with no "t1" row, this would raise `LeaseLostError`.
        line = st.record_run(_run(repo))
    finally:
        st.close()

    assert lease is None
    assert claims == []
    assert events == []
    assert line.seq == 1  # no `run_seq` was spent by the rolled-back take


def test_a_busy_re_run_of_take_lease_commits_one_lease_event(repo, monkeypatch):
    # Review Focus 3: the busy error comes after the lease event was inserted.
    monkeypatch.setattr(store_db, "RETRY_FIRST_PAUSE", 0)
    real = store_events.insert
    kinds: list[str] = []

    def busy_after_the_first_insert(conn, **kwargs):
        event = real(conn, **kwargs)
        kinds.append(kwargs["kind"])
        if len(kinds) == 1:
            raise _busy()
        return event

    monkeypatch.setattr(store_events, "insert", busy_after_the_first_insert)
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        events = _events(st)
    finally:
        st.close()

    assert kinds == ["lease_acquired", "lease_acquired"]
    assert [(event.kind, event.run_seq) for event in events] == [("lease_acquired", 1)]


def test_a_lease_event_takes_a_run_seq_but_never_reaches_the_journal_file(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        line = st.record_run(_run(repo))
        events = _events(st)
        texts = _file_texts(st)
        lines = store_events.run_lines(st.read_connection, RUN_A)
    finally:
        st.close()

    assert [(event.kind, event.run_seq) for event in events] == [
        ("lease_acquired", 1),
        ("run_upsert", 2),
    ]
    assert line.seq == 2
    assert texts == [_line_text(events[1])]
    assert lines == [line]


def test_the_other_lease_writes_record_no_event(repo):
    st = store_writer.Store.open(repo, RUN_A)
    try:
        st.take_lease(
            token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True, claims=["card:a"]
        )
        st.beat("t1", LATER)
        st.close_window("t1")
        st.set_lease_holder("t1", pid=2, host="h2")
        st.bind_lease(None)
        st.adopt_lease("t1")
        st.release_claims("t1")
        st.release_lease("t1")
        events = _events(st)
    finally:
        st.close()

    assert [event.kind for event in events] == ["lease_acquired"]


def _recording_take_lease(monkeypatch) -> list[BaseException]:
    """Wrap `store_leases.take_lease` to keep every exception it raises."""
    real = store_leases.take_lease
    raised: list[BaseException] = []

    def recording(conn, *args, **kwargs):
        try:
            return real(conn, *args, **kwargs)
        except BaseException as error:  # kept for the test, then re-raised
            raised.append(error)
            raise

    monkeypatch.setattr(store_leases, "take_lease", recording)
    return raised


def test_a_take_refused_by_a_live_lease_records_one_claim_conflict(repo, monkeypatch):
    _plant_holder(repo, token="t0", pid=7, host="other-box")
    raised = _recording_take_lease(monkeypatch)
    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(store_leases.LeaseHeldError) as caught:
            st.take_lease(
                token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True, claims=["card:a"]
            )
        lease = store_leases.read_lease(st.read_connection, RUN_A)
        claims = _claims(st)
        events = _events(st)
        project_id = st.project_id
    finally:
        st.close()

    # The very object the take raised, so `control.Lease` and `cli.run_lease`
    # translate it as before.
    assert len(raised) == 1 and caught.value is raised[0]
    assert lease is not None and (lease.token, lease.pid, lease.host) == ("t0", 7, "other-box")
    assert claims == []
    (event,) = events
    assert (event.kind, event.source, event.project_id, event.schema, event.run_seq) == (
        "claim_conflict",
        "live",
        project_id,
        1,
        1,
    )
    assert (event.story_id, event.card_id, event.phase, event.attempt) == _NO_COORDINATES
    assert event.payload == {
        "key": None,
        "holder_run": RUN_A,
        "holder_pid": 7,
        "holder_host": "other-box",
    }


def test_a_take_refused_by_a_held_claim_records_its_key_and_holder(repo):
    other = store_writer.Store.open(repo, RUN_B)
    st = store_writer.Store.open(repo, RUN_A)
    try:
        other.take_lease(
            token="tb", pid=2, host="h2", now=NOW, is_live=lambda row: True, claims=["card:a"]
        )
        with pytest.raises(store_leases.ClaimHeldError) as caught:
            st.take_lease(
                token="t1",
                pid=1,
                host="h",
                now=NOW,
                is_live=lambda row: True,
                claims=["card:z", "card:a"],
            )
        lease = store_leases.read_lease(st.read_connection, RUN_A)
        claims = _claims(st)
        events = _events(st)
    finally:
        st.close()
        other.close()

    assert caught.value.key == "card:a"
    assert lease is None
    assert claims == [("card:a", RUN_B)]
    (event,) = events
    assert event.kind == "claim_conflict"
    assert event.payload == {
        "key": "card:a",
        "holder_run": RUN_B,
        "holder_pid": 2,
        "holder_host": "h2",
    }


def test_a_failed_claim_conflict_write_is_logged_and_the_refusal_still_raised(
    repo, monkeypatch, caplog
):
    # Review Focus 2.
    caplog.set_level(logging.WARNING, logger="agent_manager.store.writer")
    _plant_holder(repo, token="t0", pid=7, host="other-box")
    raised = _recording_take_lease(monkeypatch)
    monkeypatch.setattr(
        store_events,
        "insert",
        _failing_insert_for("claim_conflict", sqlite3.OperationalError("disk I/O error")),
    )
    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(store_leases.LeaseHeldError) as caught:
            st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
        events = _events(st)
    finally:
        st.close()

    assert caught.value is raised[0]
    assert events == []
    warnings = [record for record in caplog.records if record.name == "agent_manager.store.writer"]
    (warning,) = warnings
    assert warning.levelno == logging.WARNING
    assert RUN_A in warning.getMessage() and "disk I/O error" in warning.getMessage()
    assert warning.exc_info is not None
    assert isinstance(warning.exc_info[1], sqlite3.OperationalError)


def test_a_base_exception_from_the_claim_conflict_job_propagates(repo, monkeypatch):
    # Only `Exception`s are swallowed.
    _plant_holder(repo, token="t0", pid=7, host="other-box")
    monkeypatch.setattr(
        store_events, "insert", _failing_insert_for("claim_conflict", KeyboardInterrupt())
    )
    st = store_writer.Store.open(repo, RUN_A)
    try:
        with pytest.raises(KeyboardInterrupt):
            st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True)
    finally:
        st.close()


# -- the at-fork hook ------------------------------------------------------------


def test_every_store_is_tracked_weakly_for_the_fork_hook(repo):
    st = store_writer.Store.open(repo, RUN_A)
    assert st in store_writer._STORES
    st.close()
    gone = weakref.ref(st)
    del st
    gc.collect()

    assert gone() is None


def test_the_at_fork_hook_leaves_every_open_store_inert(repo, monkeypatch):
    # Review Focus 5: `idle` never started its writer or opened its reader.
    busy = store_writer.Store.open(repo, RUN_A)
    idle = store_writer.Store.open(repo, RUN_B)
    closed = store_writer.Store.open(repo, "run-2026-10-07-03")
    closed.close()
    busy.record_run(_run(repo))
    assert busy.load_run(RUN_A) is not None
    jobs, writer = busy._jobs, busy._writer
    state_lock, reader_lock = busy._state_lock, busy._reader_lock
    closed_jobs = closed._jobs
    assert writer is not None and writer.is_alive()
    monkeypatch.setattr(store_writer, "_STORES", weakref.WeakSet([busy, idle, closed]))
    try:
        store_writer._after_fork_in_child()

        assert busy._jobs is not jobs
        assert busy._writer is None
        assert busy._state_lock is not state_lock
        assert busy._reader_lock is not reader_lock
        for st in (busy, idle):
            began = time.monotonic()
            with pytest.raises(sqlite3.ProgrammingError, match="inherited across a fork"):
                st.record_run(_run(repo))
            with pytest.raises(sqlite3.ProgrammingError, match="inherited across a fork"):
                st.load_run(RUN_A)
            st.close()
            assert time.monotonic() - began < 1.0
            # `close` touched neither connection.
            assert tuple(st.connection.execute("SELECT 1").fetchone()) == (1,)
        assert closed._jobs is closed_jobs
        assert closed._forked is False
    finally:
        # In this process the old writer thread is real: stop it on its old queue.
        jobs.put(None)
        writer.join(timeout=5.0)
        for st in (busy, idle):
            st.connection.close()
            if st._reader is not None:
                st._reader.close()


FORK_DEADLINE = 10.0
"""Only bounds a child that hangs on the inherited store; a healthy one exits at once."""


def _in_forked_child(inherited: store_writer.Store, repo: Path) -> int:
    """What the child does with the parent's store, then with its own; 0 when all held."""
    try:
        inherited.record_run(_run(repo))
    except sqlite3.ProgrammingError as error:
        if "inherited across a fork" not in str(error):
            return 3
    else:
        return 2
    try:
        inherited.load_run(RUN_A)
    except sqlite3.ProgrammingError:
        pass
    else:
        return 4
    inherited.close()
    own = store_writer.Store.open(repo, RUN_B)
    try:
        own.record_run(_run(repo).model_copy(update={"id": RUN_B}))
    finally:
        own.close()
    return 0


@pytest.mark.e2e_fake
def test_a_forked_child_never_waits_on_the_parents_writer(repo):
    parent = store_writer.Store.open(repo, RUN_A)
    try:
        # The writer thread is running, and the parent holds `_state_lock` at
        # the moment of the fork: without the hook the child's first write
        # would wait on that lock forever.
        parent.record_run(_run(repo))
        with warnings.catch_warnings():
            # Python warns on any fork of a process that has threads.
            warnings.simplefilter("ignore", DeprecationWarning)
            with parent._state_lock:
                pid = os.fork()
                if pid == 0:
                    code = 1
                    try:
                        code = _in_forked_child(parent, repo)
                    finally:
                        os._exit(code)
        deadline = time.monotonic() + FORK_DEADLINE
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done == pid:
                break
            if time.monotonic() >= deadline:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                pytest.fail(f"the forked child {pid} did not exit within {FORK_DEADLINE}s")
            time.sleep(0.01)

        assert os.waitstatus_to_exitcode(status) == 0
        parent.record_run(_run(repo).model_copy(update={"status": "done"}))
        assert parent.load_run(RUN_A).status == "done"
        child_run = parent.load_run(RUN_B)
        assert child_run is not None and child_run.id == RUN_B
    finally:
        parent.close()
