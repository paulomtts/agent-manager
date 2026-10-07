"""Where `agent_manager.store.outbox`' names live, what the module may import,
and that its SQL never commits: the `board_comments` outbox rows.

Outbox behaviour through `Store` is tested in `tests/test_store.py`.
Everything here imports modules, reads source files or opens a SQLite file
under tmp_path; nothing spawns a process, so these are unit tests.
"""

import ast
import inspect
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_manager import store
from agent_manager.store import outbox as store_outbox
from agent_manager.store.writer import Store

_REPO = Path(__file__).resolve().parents[2]

_NEW_TEST_FILES = ("test_leases.py", "test_checkpoints.py", "test_outbox.py")
"""Skipped by every scan: their self-check literals name moved names."""

RUN_ID = "run-2026-10-07-01"
OTHER_RUN = "run-2026-10-07-02"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=1)

_PUBLIC_OUTBOX_NAMES = (
    "CommentRow",
    "enqueue_comment",
    "pending_comments",
    "mark_comment_posted",
    "record_comment_failure",
)

_OUTBOX_NAMES = (*_PUBLIC_OUTBOX_NAMES, "COMMENT_ATTEMPTS", "_comment_from_row")


def test_outbox_is_a_leaf_module_of_the_store_package():
    for name in _PUBLIC_OUTBOX_NAMES:
        assert getattr(store_outbox, name).__module__ == "agent_manager.store.outbox", name
    assert store_outbox.COMMENT_ATTEMPTS == 3


def test_the_store_package_does_not_re_export_outbox_names():
    assert [name for name in _OUTBOX_NAMES if hasattr(store, name)] == []
    assert inspect.ismodule(store.outbox)
    assert store.outbox is store_outbox


def _outside_imports(path: Path) -> list[str]:
    """Every import of `path` that is not the stdlib or `store_db`."""
    tree = ast.parse(path.read_text())
    outside: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            outside.extend(
                alias.name
                for alias in node.names
                if alias.name.split(".")[0] not in sys.stdlib_module_names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                outside.append("." * node.level + module)
            elif module == "agent_manager":
                outside.extend(f"agent_manager.{alias.name}" for alias in node.names)
            elif module == "agent_manager.store":
                outside.extend(
                    f"agent_manager.store.{alias.name}"
                    for alias in node.names
                    if (alias.name, alias.asname) != ("db", "store_db")
                )
            elif module.split(".")[0] not in sys.stdlib_module_names:
                outside.append(module)
    return outside


def test_outbox_imports_only_the_stdlib_and_store_db():
    assert _outside_imports(Path(store_outbox.__file__)) == []


_TRANSACTION_CALLS = frozenset({"commit", "rollback", "immediate", "open_db", "connect"})


def _transaction_control(path: Path) -> list[str]:
    """Every call or literal in `path` that would open, end or own a transaction."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _TRANSACTION_CALLS
        ):
            found.append(f"line {node.lineno}: .{node.func.attr}()")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "BEGIN" in node.value
        ):
            found.append(f"line {node.lineno}: {node.value!r}")
    return found


def test_outbox_never_commits_or_opens_a_transaction():
    assert _transaction_control(Path(store_outbox.__file__)) == []


_THROUGH_THE_PACKAGE = re.compile(
    r"\bstore(_module)?\.(COMMENT_ATTEMPTS|CommentRow|_comment_from_row)\b"
    r"|from agent_manager\.store import (?!outbox\b).*\b(COMMENT_ATTEMPTS|CommentRow)\b"
)


def _hits(pattern: re.Pattern[str]) -> list[str]:
    """Every line under `src/` and `tests/` that `pattern` matches."""
    skipped = {(_REPO / "tests" / "store" / name).resolve() for name in _NEW_TEST_FILES}
    return [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for root in (_REPO / "src", _REPO / "tests")
        for path in sorted(root.rglob("*.py"))
        if path.resolve() not in skipped and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]


def test_no_caller_reaches_an_outbox_name_through_the_store_package():
    # Names `Store` shares as methods (`enqueue_comment`, ...) are not
    # scanned: `st.pending_comments()` is a method call.
    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import COMMENT_ATTEMPTS")
    assert _THROUGH_THE_PACKAGE.search("    from agent_manager.store import CommentRow, Store")
    assert _THROUGH_THE_PACKAGE.search("assert attempts < store.COMMENT_ATTEMPTS")
    assert not _THROUGH_THE_PACKAGE.search("assert attempts < store_outbox.COMMENT_ATTEMPTS")
    assert not _THROUGH_THE_PACKAGE.search("rows = st.pending_comments()")
    assert not _THROUGH_THE_PACKAGE.search("from agent_manager.store import outbox as store_outbox")
    assert _hits(_THROUGH_THE_PACKAGE) == []


# -- the SQL never commits ---------------------------------------------------


def _count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]


def _enqueue(
    conn: sqlite3.Connection,
    key: str,
    *,
    project_id: int,
    run_id: str = RUN_ID,
    card_id: str = "card-a",
    now: datetime = NOW,
) -> None:
    assert store_outbox.enqueue_comment(
        conn,
        project_id=project_id,
        run_id=run_id,
        card_id=card_id,
        key=key,
        body=f"body {key}",
        now=now,
    )
    conn.commit()


def _state(conn: sqlite3.Connection, key: str) -> tuple:
    row = conn.execute(
        "SELECT state, comment_id, failed_attempts, posted_at FROM board_comments"
        " WHERE key = ?",
        (key,),
    ).fetchone()
    return tuple(row)


def test_enqueue_comment_queues_a_key_once_without_committing(conns, project_id):
    conn, other = conns

    assert (
        store_outbox.enqueue_comment(
            conn, run_id=RUN_ID, card_id="card-a", key="k1", body="first", now=NOW,
            project_id=project_id,
        )
        is True
    )

    assert conn.in_transaction
    assert store_outbox.pending_comments(other) == []
    conn.commit()
    assert (
        store_outbox.enqueue_comment(
            conn, run_id=RUN_ID, card_id="card-a", key="k1", body="second", now=LATER,
            project_id=project_id,
        )
        is False
    )
    conn.commit()
    assert store_outbox.pending_comments(other) == [
        store_outbox.CommentRow(
            run_id=RUN_ID,
            card_id="card-a",
            key="k1",
            body="first",
            state="pending",
            comment_id=None,
            failed_attempts=0,
        )
    ]


def test_enqueue_comment_with_no_body_raises_and_leaves_nothing_once_rolled_back(conns, project_id):
    conn, other = conns

    with pytest.raises(sqlite3.IntegrityError):
        store_outbox.enqueue_comment(
            conn, run_id=RUN_ID, card_id="card-a", key="k1", body=None, now=NOW,
            project_id=project_id,
        )
    conn.rollback()

    assert _count(conn) == 0
    assert _count(other) == 0


def test_pending_comments_filters_and_orders_like_the_store(repo, conns, project_id):
    conn, _ = conns
    _enqueue(conn, "k1", now=NOW, project_id=project_id)
    _enqueue(conn, "k2", run_id=OTHER_RUN, card_id="card-b", now=NOW, project_id=project_id)
    _enqueue(conn, "k3", now=NOW - timedelta(minutes=1), project_id=project_id)
    _enqueue(conn, "k4", now=NOW - timedelta(minutes=2), project_id=project_id)
    store_outbox.mark_comment_posted(conn, "k4", "c-4", NOW, project_id=project_id)
    conn.commit()

    def keys(**filters) -> list[str]:
        return [row.key for row in store_outbox.pending_comments(conn, **filters)]

    assert keys() == ["k3", "k1", "k2"]
    assert keys(run_id=RUN_ID) == ["k3", "k1"]
    assert keys(card_ids=["card-b"]) == ["k2"]
    assert keys(run_id=RUN_ID, card_ids=iter(["card-b"])) == []
    assert keys(card_ids=[]) == []
    assert not conn.in_transaction
    st = Store.open(repo, RUN_ID)
    try:
        assert st.pending_comments(run_id=RUN_ID) == store_outbox.pending_comments(
            conn, run_id=RUN_ID
        )
    finally:
        st.close()


def test_pending_comments_with_no_card_ids_never_queries():
    closed = sqlite3.connect(":memory:")
    closed.close()

    assert store_outbox.pending_comments(closed, card_ids=[]) == []


def test_mark_comment_posted_records_the_board_id_without_committing(conns, project_id):
    conn, other = conns
    _enqueue(conn, "k1", project_id=project_id)

    store_outbox.mark_comment_posted(conn, "k1", "c-9", LATER, project_id=project_id)
    store_outbox.mark_comment_posted(conn, "nope", "c-0", LATER, project_id=project_id)  # unknown key

    assert conn.in_transaction
    assert _state(other, "k1") == ("pending", None, 0, None)
    conn.commit()
    assert _state(other, "k1") == ("posted", "c-9", 0, LATER.isoformat())
    assert _count(other) == 1


def test_record_comment_failure_counts_abandons_at_the_cap_and_keeps_posted_rows(conns, project_id):
    conn, other = conns
    _enqueue(conn, "k1", project_id=project_id)
    _enqueue(conn, "k2", project_id=project_id)
    store_outbox.mark_comment_posted(conn, "k2", "c-2", NOW, project_id=project_id)
    conn.commit()

    assert store_outbox.record_comment_failure(conn, "k1", project_id=project_id) == 1
    assert conn.in_transaction
    assert _state(other, "k1")[2] == 0
    conn.commit()
    counts = [
        store_outbox.record_comment_failure(conn, "k1", project_id=project_id)
        for _ in range(store_outbox.COMMENT_ATTEMPTS - 1)
    ]
    posted = [
        store_outbox.record_comment_failure(conn, "k2", project_id=project_id)
        for _ in range(store_outbox.COMMENT_ATTEMPTS)
    ]
    conn.commit()

    assert counts == [2, 3]
    state, _, attempts, _ = _state(other, "k1")
    assert (state, attempts) == ("abandoned", 3)
    assert posted == [1, 2, 3]
    assert _state(other, "k2")[0] == "posted"
    assert store_outbox.record_comment_failure(conn, "nope", project_id=project_id) == 0


# -- comments are per project (B6) ---------------------------------------------


def _comment(conn: sqlite3.Connection, project_id: int, key: str) -> tuple | None:
    row = conn.execute(
        "SELECT body, state, comment_id, failed_attempts FROM board_comments"
        " WHERE project_id = ? AND key = ?",
        (project_id, key),
    ).fetchone()
    return None if row is None else tuple(row)


def test_the_same_comment_key_in_two_projects_queues_twice(
    conns, project_id, other_project_id
):
    conn, other = conns

    def enqueue(pid: int, run_id: str, body: str, now: datetime = NOW) -> bool:
        return store_outbox.enqueue_comment(
            conn, project_id=pid, run_id=run_id, card_id="card-a", key="c", body=body, now=now
        )

    assert enqueue(project_id, RUN_ID, "for A") is True
    assert enqueue(other_project_id, OTHER_RUN, "for B") is True
    assert enqueue(project_id, RUN_ID, "again", LATER) is False
    conn.commit()

    assert _comment(other, project_id, "c") == ("for A", "pending", None, 0)
    assert _comment(other, other_project_id, "c") == ("for B", "pending", None, 0)


def test_mark_posted_and_record_failure_touch_only_their_project(
    conns, project_id, other_project_id
):
    conn, other = conns
    for pid, run_id in ((project_id, RUN_ID), (other_project_id, OTHER_RUN)):
        store_outbox.enqueue_comment(
            conn, project_id=pid, run_id=run_id, card_id="card-a", key="c", body="b", now=NOW
        )
    conn.commit()

    store_outbox.mark_comment_posted(conn, "c", "c-1", LATER, project_id=project_id)
    failures = store_outbox.record_comment_failure(conn, "c", project_id=other_project_id)
    conn.commit()

    assert failures == 1
    assert _comment(other, project_id, "c") == ("b", "posted", "c-1", 0)
    assert _comment(other, other_project_id, "c") == ("b", "pending", None, 1)


def test_a_key_only_another_project_has_is_unknown_to_this_one(
    conns, project_id, other_project_id
):
    # Review Focus 3.
    conn, other = conns
    store_outbox.enqueue_comment(
        conn,
        project_id=other_project_id,
        run_id=OTHER_RUN,
        card_id="card-a",
        key="c",
        body="b",
        now=NOW,
    )
    conn.commit()

    store_outbox.mark_comment_posted(conn, "c", "c-1", LATER, project_id=project_id)
    failures = store_outbox.record_comment_failure(conn, "c", project_id=project_id)
    conn.commit()

    assert failures == 0
    assert _comment(other, other_project_id, "c") == ("b", "pending", None, 0)
    assert _comment(other, project_id, "c") is None
