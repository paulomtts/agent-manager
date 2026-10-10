"""e2e_fake tier: `am.db` contention between real processes (card 1.3.6).

Single-store design D:381-385. Every `am` is a real child process under the
fake `claude`. A foreign process holding `am.db`'s write lock past the retry
budget ends a live `am run` with the `StoreBusyError` envelope at exit 3, and
records nothing it did not commit. Two `am` processes writing different runs
at once lose no event, and each run's events stay in order. The foreign holder
is `storehelpers.hold_db`; order comes from marker files, pipe lines and
exits, never from sleeping.
"""

from pathlib import Path

import pytest
from storehelpers import hold_db, reap, release_db

from agent_manager import cli, paths
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import replay as store_replay
from agent_manager.store import writer as store_writer

PREFIX = "m10"
"""The `--branch-prefix` of the single-run scenario."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

BUSY_ENTRY = (
    "from agent_manager.store import db; "
    "db.BUSY_TIMEOUT_SECONDS = 0.2; "
    "db.RETRY_FIRST_PAUSE = 0.05; "
    "db.RETRY_PAUSE_CAP = 0.2; "
    "db.RETRY_ATTEMPTS = 3; "
    "db.RETRY_DEADLINE_SECONDS = 5.0; "
    "from agent_manager.cli import app; app()"
)
"""An `am` child whose retry budget is short: the real Typer app, with the
store's constants set before `cli` is imported. The design's 2 s
`busy_timeout` inside a 10 s deadline (D:196-197) has this shape;
`BUSY_TIMEOUT_SECONDS`' 30 s does not, and would hold the test for 30 s."""


def _common(root: Path, prefix: str) -> list[str]:
    """The flags `am run` needs for this repo, prefix and suite."""
    return [
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        prefix,
        "--verify",
        VERIFY,
    ]


def _data(code: int, envelope: dict) -> dict:
    """The `data` of an ok envelope from a child that exited 0."""
    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


@pytest.mark.e2e_fake
def test_am_run_blocked_past_the_retry_budget_is_a_store_busy_envelope_at_exit_3(
    milestone_board, fake_claude_bin, hold, spawn_am, finish_am, am, wait_for_file
):
    """D:383-384. The holder is taken only once a1's implement is held: every
    opening write is done by then, and the run's next write after the agent
    returns is a `record_*` inside `run_with_retry` (Review Focus 3)."""
    root = milestone_board["root"]
    a1 = milestone_board["subtasks"]["A"][0]
    hold.arm()
    child = spawn_am("run", "--card", a1, *_common(root, PREFIX), entry=BUSY_ENTRY)
    wait_for_file(hold.held_marker(a1), child)
    holder, head = hold_db()
    try:
        hold.release(a1)
        code, envelope = finish_am(child)
        # Review Focus 4: read under the hold through a WAL reader.
        reader = store_db.open_reader(paths.db_path())
        try:
            held_head = store_events.head(reader)
        finally:
            reader.close()
        release_db(holder)
    finally:
        reap(holder)

    assert code == cli.EXIT_ERROR, envelope
    assert envelope["ok"] is False, envelope
    error = envelope["error"]
    assert error["type"] == "StoreBusyError", error
    assert error["message"].startswith("record_"), error
    assert "stayed busy or locked" in error["message"], error
    assert held_head == head
    runs = _data(*am("runs", "--repo-dir", str(root)))["runs"]
    (run_id,) = [row["id"] for row in runs]
    st = store_writer.Store.open(root, run_id)
    try:
        lines = store_events.run_lines(st.read_connection, run_id)
        loaded = st.load_run(run_id)
    finally:
        st.close()
    assert loaded is not None, run_id
    assert store_replay.diverging(lines, loaded) == [], run_id


def _events_by_run(run_ids: list[str]) -> dict[str, list[store_events.EventRow]]:
    """Each run's committed events in `seq` order, read through a WAL reader."""
    reader = store_db.open_reader(paths.db_path())
    try:
        return {run_id: store_events.read(reader, run_id=run_id) for run_id in run_ids}
    finally:
        reader.close()


@pytest.mark.e2e_fake
def test_two_am_processes_on_different_runs_keep_every_event_in_order(
    two_milestone_board, fake_claude_bin, rendezvous, spawn_am, finish_am
):
    """D:385. Each milestone drives one story at a time, so the count-2
    rendezvous can only be met while both processes are live: their writes
    overlap in time."""
    root = two_milestone_board["root"]
    prefixes = {"first": "m10a", "second": "m10b"}
    rendezvous.arm(2)

    children = {
        side: spawn_am(
            "run",
            "--milestone",
            two_milestone_board["milestones"][side],
            *_common(root, prefix),
            "--max-concurrent",
            "1",
        )
        for side, prefix in prefixes.items()
    }
    results = {side: _data(*finish_am(child)) for side, child in children.items()}

    run_ids = [results[side]["run_id"] for side in prefixes]
    assert all(results[side]["done"] is True for side in prefixes), results
    assert run_ids[0] != run_ids[1]
    events = _events_by_run(run_ids)
    seqs = {run_id: [event.seq for event in events[run_id]] for run_id in run_ids}
    for run_id in run_ids:
        assert seqs[run_id], run_id
        # One writer per run, `run_seq` assigned MAX+1 inside the transaction.
        assert [event.run_seq for event in events[run_id]] == list(
            range(1, len(events[run_id]) + 1)
        ), run_id
        assert seqs[run_id] == sorted(set(seqs[run_id])), run_id
    first, second = (seqs[run_id] for run_id in run_ids)
    assert set(first).isdisjoint(second)
    # Non-vacuity: each run committed events both before and after the other's.
    assert min(first) < max(second) and min(second) < max(first)
    for run_id in run_ids:
        st = store_writer.Store.open(root, run_id)
        try:
            replayed = st.replay_events(run_id)
            loaded = st.load_run(run_id)
            lines = store_events.run_lines(st.read_connection, run_id)
        finally:
            st.close()
        assert loaded is not None, run_id
        assert store_replay.diverging(lines, loaded) == [], run_id
        assert replayed == loaded, run_id
