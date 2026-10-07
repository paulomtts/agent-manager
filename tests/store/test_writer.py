"""Where `agent_manager.store.writer`'s `Store` lives, what the module may
import, that no caller reaches `Store` through the `agent_manager.store`
package, which holds no code, and how its writer thread, job queue and read
connection behave.

The rest of `Store`'s behaviour is tested in `tests/test_store.py`. The tests
here import modules, read source files, or drive a `Store` on a real SQLite
file under `tmp_path` with in-process threads; nothing spawns a process, so
these are unit tests.
"""

import ast
import inspect
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
    assert names <= {*_STORE_LEAVES, "writer"}, sorted(names)


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
        st.rebuild_from_journal(RUN_A)
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
