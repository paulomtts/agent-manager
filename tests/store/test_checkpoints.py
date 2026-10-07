"""Where `agent_manager.store.checkpoints`' names live, what the module may
import, and that its SQL never commits: the `checkpoints` and
`checkpoint_floors` rows.

Checkpoint behaviour through `Store` is tested in `tests/test_store.py`.
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
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store.writer import Store

_REPO = Path(__file__).resolve().parents[2]

_NEW_TEST_FILES = ("test_leases.py", "test_checkpoints.py", "test_outbox.py")
"""Skipped by every scan: their self-check literals name moved names."""

RUN_ID = "run-2026-10-07-01"
OTHER_RUN = "run-2026-10-07-02"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=1)
FLOOR = store_checkpoints.TurnFloor(phase="spec", loop=1, source_run="run-earlier", floor=3)

_PUBLIC_CHECKPOINT_NAMES = (
    "TurnFloor",
    "Checkpoint",
    "insert_checkpoint",
    "latest_checkpoint",
    "latest_turn_checkpoint",
    "latest_open_checkpoint",
    "checkpoint_cards",
)

_CHECKPOINT_NAMES = (
    *_PUBLIC_CHECKPOINT_NAMES,
    "_checkpoint_from_row",
    "_CHECKPOINT_SELECT",
)


def test_checkpoints_is_a_leaf_module_of_the_store_package():
    for name in _PUBLIC_CHECKPOINT_NAMES:
        assert (
            getattr(store_checkpoints, name).__module__ == "agent_manager.store.checkpoints"
        ), name


def test_the_store_package_does_not_re_export_checkpoint_names():
    assert [name for name in _CHECKPOINT_NAMES if hasattr(store, name)] == []
    assert inspect.ismodule(store.checkpoints)
    assert store.checkpoints is store_checkpoints


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


def test_checkpoints_imports_only_the_stdlib_models_and_store_db():
    # `models` for the canceled-run spellings `latest_open_checkpoint` skips.
    assert _outside_imports(Path(store_checkpoints.__file__)) == ["agent_manager.models"]


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


def test_checkpoints_never_commits_or_opens_a_transaction():
    assert _transaction_control(Path(store_checkpoints.__file__)) == []


_THROUGH_THE_PACKAGE = re.compile(
    r"\bstore(_module)?\.(TurnFloor|Checkpoint|_checkpoint_from_row"
    r"|_CHECKPOINT_SELECT)\b"
    r"|from agent_manager\.store import (?!checkpoints\b).*\b(TurnFloor|Checkpoint)\b"
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


def test_no_caller_reaches_a_checkpoints_name_through_the_store_package():
    # Names `Store` shares as methods (`latest_checkpoint`, ...) are not
    # scanned: `st.latest_checkpoint(card)` is a method call.
    assert _THROUGH_THE_PACKAGE.search(") -> store_module.Checkpoint | None:")
    assert _THROUGH_THE_PACKAGE.search("floor = store.TurnFloor('a', 0, RUN_ID, 0)")
    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import Checkpoint, Store")
    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import TurnFloor")
    assert not _THROUGH_THE_PACKAGE.search(") -> store_checkpoints.Checkpoint | None:")
    assert not _THROUGH_THE_PACKAGE.search("raise runtime_engine.CheckpointMismatch(")
    assert not _THROUGH_THE_PACKAGE.search("kept = st.latest_checkpoint('ef248597')")
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import checkpoints as store_checkpoints"
    )
    assert _hits(_THROUGH_THE_PACKAGE) == []


# -- the SQL never commits ---------------------------------------------------


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _save(
    conn: sqlite3.Connection,
    run_id: str,
    card_id: str,
    *,
    reason: str,
    saved_at: datetime,
) -> store_checkpoints.Checkpoint:
    checkpoint = store_checkpoints.insert_checkpoint(
        conn,
        run_id,
        card_id,
        workflow="task",
        digest="d",
        reason=reason,
        agent={},
        saved_at=saved_at,
    )
    conn.commit()
    return checkpoint


def test_insert_checkpoint_numbers_rows_per_card_and_writes_a_floor_only_when_given(conns):
    conn, other = conns

    first = store_checkpoints.insert_checkpoint(
        conn,
        RUN_ID,
        "card-a",
        workflow="task",
        digest="d1",
        reason="turn",
        agent={"b": 1, "a": [2]},
        saved_at=NOW,
    )

    assert conn.in_transaction
    assert _count(other, "checkpoints") == 0
    second = store_checkpoints.insert_checkpoint(
        conn,
        RUN_ID,
        "card-a",
        workflow="task",
        digest="d1",
        reason="parked",
        agent={},
        saved_at=LATER,
        floor=FLOOR,
    )
    conn.commit()

    assert (first.seq, second.seq) == (0, 1)
    assert first.floor is None
    assert first.agent == {"a": [2], "b": 1}
    assert (
        other.execute("SELECT agent FROM checkpoints WHERE seq = 0").fetchone()[0]
        == '{"a": [2], "b": 1}'
    )
    assert [
        tuple(row)
        for row in other.execute(
            "SELECT run_id, card_id, seq, phase, loop, source_run, floor"
            " FROM checkpoint_floors"
        )
    ] == [(RUN_ID, "card-a", 1, "spec", 1, "run-earlier", 3)]
    assert store_checkpoints.latest_checkpoint(other, RUN_ID, "card-a") == second


def test_insert_checkpoint_leaves_a_refused_row_for_the_caller_to_roll_back(conns):
    conn, other = conns

    with pytest.raises(sqlite3.IntegrityError):
        store_checkpoints.insert_checkpoint(
            conn,
            RUN_ID,
            "card-a",
            workflow="task",
            digest="d",
            reason="bogus",
            agent={},
            saved_at=NOW,
        )
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        store_checkpoints.insert_checkpoint(
            conn,
            RUN_ID,
            "card-a",
            workflow="task",
            digest="d",
            reason="turn",
            agent={},
            saved_at=NOW,
            floor=store_checkpoints.TurnFloor("spec", 1, RUN_ID, -1),
        )
    # The `checkpoints` row went in before the floor was refused; it is still
    # uncommitted, so the caller's rollback takes it back.
    assert conn.in_transaction
    conn.rollback()

    assert (_count(conn, "checkpoints"), _count(conn, "checkpoint_floors")) == (0, 0)
    assert _count(other, "checkpoints") == 0
    assert _save(conn, RUN_ID, "card-a", reason="turn", saved_at=NOW).seq == 0


def test_checkpoint_readers_match_the_store_methods(repo, conns):
    conn, _ = conns
    turn = _save(conn, RUN_ID, "card-a", reason="turn", saved_at=NOW)
    escalated = _save(conn, RUN_ID, "card-a", reason="escalated", saved_at=LATER)
    _save(conn, OTHER_RUN, "card-b", reason="done", saved_at=LATER)

    assert store_checkpoints.latest_checkpoint(conn, RUN_ID, "card-a") == escalated
    assert store_checkpoints.latest_turn_checkpoint(conn, RUN_ID, "card-a") == turn
    assert store_checkpoints.latest_open_checkpoint(conn, "card-a", "task") == escalated
    assert store_checkpoints.latest_open_checkpoint(conn, "card-a", "other") is None
    assert store_checkpoints.latest_open_checkpoint(conn, "card-b", "task") is None
    assert store_checkpoints.checkpoint_cards(conn, RUN_ID) == [("card-a", "task")]
    assert store_checkpoints.checkpoint_cards(conn, OTHER_RUN) == [("card-b", "task")]
    assert not conn.in_transaction
    st = Store.open(repo, RUN_ID)
    try:
        assert st.latest_checkpoint("card-a") == escalated
        assert st.latest_turn_checkpoint("card-a") == turn
        assert st.latest_open_checkpoint("card-a", "task") == escalated
        assert st.checkpoint_cards(OTHER_RUN) == [("card-b", "task")]
    finally:
        st.close()
