"""Where `agent_manager.store.leases`' names live, what the module may import,
and that its SQL never commits: the run lease, claim and control-request rows.

Lease behaviour through `Store` is tested in `tests/test_store.py`. Everything
here imports modules, reads source files or opens a SQLite file under
tmp_path; nothing spawns a process, so these are unit tests.
"""

import ast
import inspect
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_manager import models, store
from agent_manager.store import Store
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import leases as store_leases

_REPO = Path(__file__).resolve().parents[2]

_NEW_TEST_FILES = ("test_leases.py", "test_checkpoints.py", "test_outbox.py")
"""Skipped by every scan: their self-check literals name moved names."""

RUN_ID = "run-2026-10-07-01"
OTHER_RUN = "run-2026-10-07-02"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=1)

_PUBLIC_LEASE_NAMES = (
    "LeaseRow",
    "read_lease",
    "ClaimRow",
    "LeaseTake",
    "LeaseHeldError",
    "ClaimHeldError",
    "LeaseLostError",
    "claim_conflicts",
    "held_claims",
    "ControlRow",
    "control_requests",
    "add_control",
    "take_lease",
    "release_claims",
    "beat",
    "close_window",
    "release_lease",
    "set_lease_holder",
    "pending_controls",
    "mark_control_handled",
)

_LEASE_NAMES = (
    *_PUBLIC_LEASE_NAMES,
    "_lease_from_row",
    "_claim_from_row",
    "_control_from_row",
)


def test_leases_is_a_leaf_module_of_the_store_package():
    for name in _PUBLIC_LEASE_NAMES:
        assert getattr(store_leases, name).__module__ == "agent_manager.store.leases", name
    # The fence's error must get past every `except Exception` in the engine.
    assert issubclass(store_leases.LeaseLostError, BaseException)
    assert not issubclass(store_leases.LeaseLostError, Exception)


def test_the_store_package_does_not_re_export_lease_names():
    assert [name for name in (*_LEASE_NAMES, "_iso") if hasattr(store, name)] == []
    # Importing the submodule binds it as the package's `leases` attribute.
    assert inspect.ismodule(store.leases)
    assert store.leases is store_leases
    assert callable(store_db.iso)


def _outside_imports(path: Path) -> list[str]:
    """Every import of `path` that is not the stdlib or `store_db`.

    The AST, not `sys.modules`: importing a leaf always runs the package
    `__init__` first, so `sys.modules` cannot tell them apart.
    """
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


def test_leases_imports_only_the_stdlib_and_store_db():
    assert _outside_imports(Path(store_leases.__file__)) == []


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


def test_leases_never_commits_or_opens_a_transaction():
    assert _transaction_control(Path(store_leases.__file__)) == []


_THROUGH_THE_PACKAGE = re.compile(
    r"\bstore(_module)?\.(LeaseRow|_lease_from_row|read_lease|ClaimRow"
    r"|_claim_from_row|LeaseTake|LeaseHeldError|ClaimHeldError|LeaseLostError"
    r"|claim_conflicts|held_claims|ControlRow|_control_from_row|control_requests"
    r"|add_control)\b"
    r"|from agent_manager\.store import (?!leases\b).*\b(LeaseRow|read_lease"
    r"|ClaimRow|LeaseTake|LeaseHeldError|ClaimHeldError|LeaseLostError"
    r"|claim_conflicts|held_claims|ControlRow|control_requests|add_control)\b"
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


def test_no_caller_reaches_a_lease_name_through_the_store_package():
    # The opt-in tiers (e2e_fake, soak, e2e) never run in the default suite,
    # so a stale reference there would only fail when someone runs that tier.
    # Names `Store` shares as methods (`take_lease`, `beat`, ...) are not
    # scanned: `store.beat(token, now)` is a method call, not the function.
    assert _THROUGH_THE_PACKAGE.search("lease: store_module.LeaseRow,")
    assert _THROUGH_THE_PACKAGE.search("row = store.add_control(conn, RUN_ID,")
    assert _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import ControlRow, LeaseRow, Store"
    )
    assert not _THROUGH_THE_PACKAGE.search("lease: store_leases.LeaseRow,")
    assert not _THROUGH_THE_PACKAGE.search("st.take_lease(token='t1', pid=1)")
    assert not _THROUGH_THE_PACKAGE.search("store.beat(TOKEN, now)")
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import leases as store_leases"
    )
    assert _hits(_THROUGH_THE_PACKAGE) == []


# -- the SQL never commits ---------------------------------------------------


def _seed_lease(conn: sqlite3.Connection, run_id: str = RUN_ID, *, token: str = "t1") -> None:
    conn.execute(
        "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
        " heartbeat_at, accepting) VALUES (?, ?, 1, 'h', ?, ?, 1)",
        (run_id, token, NOW.isoformat(), NOW.isoformat()),
    )
    conn.commit()


def _seed_claim(conn: sqlite3.Connection, key: str, run_id: str, token: str) -> None:
    conn.execute(
        "INSERT INTO run_claims (key, run_id, token, claimed_at) VALUES (?, ?, ?, ?)",
        (key, run_id, token, NOW.isoformat()),
    )
    conn.commit()


def _seed_control(
    conn: sqlite3.Connection,
    seq: int,
    *,
    run_id: str = RUN_ID,
    lease: str = "t1",
    handled_at: datetime | None = None,
) -> None:
    conn.execute(
        "INSERT INTO run_controls (run_id, seq, lease, command, requested_at,"
        " handled_at) VALUES (?, ?, ?, 'pause', ?, ?)",
        (
            run_id,
            seq,
            lease,
            NOW.isoformat(),
            None if handled_at is None else handled_at.isoformat(),
        ),
    )
    conn.commit()


def _claim_keys(conn: sqlite3.Connection) -> list[str]:
    return [row["key"] for row in conn.execute("SELECT key FROM run_claims ORDER BY key")]


def test_take_lease_upserts_the_lease_and_every_claim_without_committing(conns):
    conn, other = conns
    _seed_lease(conn, token="t0")
    displaced = store_leases.read_lease(other, RUN_ID)

    taken = store_leases.take_lease(
        conn,
        RUN_ID,
        token="t1",
        pid=7,
        host="h2",
        now=LATER,
        is_live=lambda row: False,
        claims=(key for key in ("k1", "k2")),  # a one-shot iterator
    )

    assert conn.in_transaction
    assert store_leases.read_lease(other, RUN_ID) == displaced
    assert _claim_keys(other) == []
    conn.commit()
    lease = store_leases.LeaseRow(
        run_id=RUN_ID,
        token="t1",
        pid=7,
        host="h2",
        acquired_at=LATER,
        heartbeat_at=LATER,
        accepting=True,
    )
    assert displaced is not None and displaced.token == "t0"
    assert taken == store_leases.LeaseTake(lease=lease, displaced=displaced)
    assert store_leases.read_lease(other, RUN_ID) == lease
    assert [claim.key for claim in store_leases.held_claims(other, RUN_ID, "t1")] == [
        "k1",
        "k2",
    ]


def test_take_lease_of_a_free_run_displaces_nothing_and_claims_nothing_by_default(conns):
    conn, other = conns

    taken = store_leases.take_lease(
        conn, RUN_ID, token="t1", pid=1, host="h", now=NOW, is_live=lambda row: True
    )
    conn.commit()

    assert taken.displaced is None
    assert store_leases.read_lease(other, RUN_ID) == taken.lease
    assert _claim_keys(other) == []


def test_take_lease_refuses_a_live_lease_under_another_token_and_writes_nothing(conns):
    conn, other = conns
    _seed_lease(conn, token="t0")

    with pytest.raises(store_leases.LeaseHeldError) as raised:
        store_leases.take_lease(
            conn,
            RUN_ID,
            token="t1",
            pid=7,
            host="h2",
            now=LATER,
            is_live=lambda row: True,
            claims=["k1"],
        )
    conn.rollback()

    assert raised.value.holder.token == "t0"
    lease = store_leases.read_lease(other, RUN_ID)
    assert lease is not None and lease.token == "t0"
    assert _claim_keys(other) == []


def test_take_lease_refuses_a_key_another_live_run_holds_and_writes_nothing(conns):
    conn, other = conns
    _seed_lease(conn, OTHER_RUN, token="o1")
    _seed_claim(conn, "k1", OTHER_RUN, "o1")

    with pytest.raises(store_leases.ClaimHeldError) as raised:
        store_leases.take_lease(
            conn,
            RUN_ID,
            token="t1",
            pid=1,
            host="h",
            now=LATER,
            is_live=lambda row: True,
            claims=["k0", "k1"],
        )
    conn.rollback()

    assert raised.value.key == "k1"
    assert raised.value.holder.run_id == OTHER_RUN
    assert store_leases.read_lease(other, RUN_ID) is None
    assert _claim_keys(other) == ["k1"]


def test_release_claims_deletes_only_this_tokens_claims_without_committing(conns):
    conn, other = conns
    _seed_claim(conn, "k1", RUN_ID, "t1")
    _seed_claim(conn, "k2", RUN_ID, "t0")
    _seed_claim(conn, "k3", OTHER_RUN, "t1")

    store_leases.release_claims(conn, RUN_ID, "t1")

    assert conn.in_transaction
    assert _claim_keys(other) == ["k1", "k2", "k3"]
    conn.commit()
    assert _claim_keys(other) == ["k2", "k3"]


def test_beat_moves_only_the_matching_tokens_heartbeat_without_committing(conns):
    conn, other = conns
    _seed_lease(conn, RUN_ID, token="t1")
    _seed_lease(conn, OTHER_RUN, token="t1")

    store_leases.beat(conn, RUN_ID, "t1", LATER)
    store_leases.beat(conn, RUN_ID, "t9", LATER + timedelta(minutes=5))  # not the holder

    assert conn.in_transaction
    assert store_leases.read_lease(other, RUN_ID).heartbeat_at == NOW
    conn.commit()
    assert store_leases.read_lease(other, RUN_ID).heartbeat_at == LATER
    assert store_leases.read_lease(other, OTHER_RUN).heartbeat_at == NOW


def test_close_window_stops_accepting_without_committing(conns):
    conn, other = conns
    _seed_lease(conn)

    store_leases.close_window(conn, RUN_ID, "t1")

    assert conn.in_transaction
    assert store_leases.read_lease(other, RUN_ID).accepting is True
    conn.commit()
    assert store_leases.read_lease(other, RUN_ID).accepting is False


def test_release_lease_deletes_only_the_holders_row_without_committing(conns):
    conn, other = conns
    _seed_lease(conn, RUN_ID, token="t1")
    _seed_lease(conn, OTHER_RUN, token="o1")

    store_leases.release_lease(conn, RUN_ID, "t1")
    store_leases.release_lease(conn, OTHER_RUN, "t9")  # not the holder

    assert conn.in_transaction
    assert store_leases.read_lease(other, RUN_ID) is not None
    conn.commit()
    assert store_leases.read_lease(other, RUN_ID) is None
    assert store_leases.read_lease(other, OTHER_RUN) is not None


def test_set_lease_holder_renames_the_holder_without_committing(conns):
    conn, other = conns
    _seed_lease(conn)

    store_leases.set_lease_holder(conn, RUN_ID, "t1", pid=9, host="h9")

    assert conn.in_transaction
    before = store_leases.read_lease(other, RUN_ID)
    assert (before.pid, before.host) == (1, "h")
    conn.commit()
    lease = store_leases.read_lease(other, RUN_ID)
    assert (lease.pid, lease.host) == (9, "h9")


def test_pending_controls_reads_this_tokens_unhandled_requests_like_the_store(repo, conns):
    conn, _ = conns
    _seed_control(conn, 0)
    _seed_control(conn, 1, handled_at=NOW)
    _seed_control(conn, 2, lease="t0")
    _seed_control(conn, 3)
    _seed_control(conn, 0, run_id=OTHER_RUN)

    rows = store_leases.pending_controls(conn, RUN_ID, "t1")

    assert not conn.in_transaction
    assert [row.seq for row in rows] == [0, 3]
    st = Store.open(repo, RUN_ID)
    try:
        assert st.pending_controls("t1") == rows
    finally:
        st.close()


def test_mark_control_handled_marks_only_this_runs_request_without_committing(conns):
    conn, other = conns
    _seed_control(conn, 0)
    _seed_control(conn, 0, run_id=OTHER_RUN)

    store_leases.mark_control_handled(conn, RUN_ID, 0, LATER)

    assert conn.in_transaction
    assert store_leases.control_requests(other, RUN_ID)[0].handled_at is None
    conn.commit()
    assert store_leases.control_requests(other, RUN_ID)[0].handled_at == LATER
    assert store_leases.control_requests(other, OTHER_RUN)[0].handled_at is None


def test_add_control_numbers_requests_without_committing(conns):
    conn, other = conns

    row = store_leases.add_control(
        conn, RUN_ID, lease="t1", command="pause", requested_at=NOW
    )

    assert conn.in_transaction
    assert store_leases.control_requests(other, RUN_ID) == []
    conn.commit()
    assert row.seq == 0
    assert store_leases.control_requests(other, RUN_ID) == [row]


def test_add_control_refuses_an_unknown_command(conns):
    conn, other = conns

    with pytest.raises(sqlite3.IntegrityError):
        store_leases.add_control(
            conn, RUN_ID, lease="t1", command="explode", requested_at=NOW
        )
    conn.rollback()

    assert store_leases.control_requests(other, RUN_ID) == []


# -- `Store` calls the leaves through the module -----------------------------


def _no_setup(st: Store) -> None:
    pass


def _take(st: Store) -> None:
    st.take_lease(
        token="t1", pid=1, host="h", now=NOW, is_live=lambda row: False, claims=["k1"]
    )


def _save_checkpoint(st: Store) -> None:
    st.save_checkpoint(
        "card-a", workflow="task", digest="d", reason="turn", agent={}, saved_at=NOW
    )


_DELEGATIONS = [
    pytest.param(store_leases, "take_lease", _no_setup, _take, id="leases.take_lease"),
    pytest.param(
        store_leases,
        "release_claims",
        _no_setup,
        lambda st: st.release_claims("t1"),
        id="leases.release_claims",
    ),
    pytest.param(
        store_leases, "beat", _no_setup, lambda st: st.beat("t1", NOW), id="leases.beat"
    ),
    pytest.param(
        store_leases,
        "close_window",
        _no_setup,
        lambda st: st.close_window("t1"),
        id="leases.close_window",
    ),
    pytest.param(
        store_leases,
        "release_lease",
        _no_setup,
        lambda st: st.release_lease("t1"),
        id="leases.release_lease",
    ),
    pytest.param(
        store_leases,
        "set_lease_holder",
        _no_setup,
        lambda st: st.set_lease_holder("t1", pid=2, host="h2"),
        id="leases.set_lease_holder",
    ),
    pytest.param(
        store_leases,
        "pending_controls",
        _no_setup,
        lambda st: st.pending_controls("t1"),
        id="leases.pending_controls",
    ),
    pytest.param(
        store_leases,
        "mark_control_handled",
        _no_setup,
        lambda st: st.mark_control_handled(0, NOW),
        id="leases.mark_control_handled",
    ),
    pytest.param(
        store_leases,
        "read_lease",
        _take,
        lambda st: st.adopt_lease("t1"),
        id="leases.read_lease",
    ),
    pytest.param(
        store_checkpoints,
        "insert_checkpoint",
        _no_setup,
        _save_checkpoint,
        id="checkpoints.insert_checkpoint",
    ),
    pytest.param(
        store_checkpoints,
        "latest_checkpoint",
        _no_setup,
        lambda st: st.latest_checkpoint("card-a"),
        id="checkpoints.latest_checkpoint",
    ),
    pytest.param(
        store_checkpoints,
        "latest_turn_checkpoint",
        _no_setup,
        lambda st: st.latest_turn_checkpoint("card-a"),
        id="checkpoints.latest_turn_checkpoint",
    ),
    pytest.param(
        store_checkpoints,
        "latest_open_checkpoint",
        _no_setup,
        lambda st: st.latest_open_checkpoint("card-a", "task"),
        id="checkpoints.latest_open_checkpoint",
    ),
    pytest.param(
        store_checkpoints,
        "checkpoint_cards",
        _no_setup,
        lambda st: st.checkpoint_cards(RUN_ID),
        id="checkpoints.checkpoint_cards",
    ),
]


@pytest.mark.parametrize(("leaf", "function", "setup", "drive"), _DELEGATIONS)
def test_store_methods_call_the_leaf_through_the_module(
    repo, monkeypatch, leaf, function, setup, drive
):
    # A `Store` method that bound the function by name would never see a
    # patch of the leaf's attribute; this pins the attribute lookup.
    original = getattr(leaf, function)
    seen: list[sqlite3.Connection] = []

    def spy(conn, *args, **kwargs):
        seen.append(conn)
        return original(conn, *args, **kwargs)

    st = Store.open(repo, RUN_ID)
    try:
        setup(st)
        monkeypatch.setattr(leaf, function, spy)
        drive(st)
        assert seen != []
        assert all(conn is st.connection for conn in seen)
    finally:
        st.close()


def _run(repo: Path) -> models.Run:
    return models.Run(
        id=RUN_ID,
        workflow="milestone",
        repo_dir=repo,
        base_branch="main",
        branch_prefix="m1/",
        status="started",
        started_at=NOW,
        config=models.RunConfig(
            max_concurrent_stories=2,
            harness_map={"coder": models.HarnessAssignment(harness="claude", model="sonnet")},
        ),
    )


def test_fence_reads_the_lease_through_store_leases(repo, monkeypatch):
    original = store_leases.read_lease
    seen: list[tuple[sqlite3.Connection, str]] = []

    def spy(conn, run_id):
        seen.append((conn, run_id))
        return original(conn, run_id)

    st = Store.open(repo, RUN_ID)
    try:
        st.take_lease(token="t1", pid=1, host="h", now=NOW, is_live=lambda row: False)
        monkeypatch.setattr(store_leases, "read_lease", spy)
        st.record_run(_run(repo))
        assert seen == [(st.connection, RUN_ID)]
    finally:
        st.close()
