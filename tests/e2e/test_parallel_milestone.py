"""Default-suite e2e tier: parallel stories through the production wiring.

Addendum P7 and main spec section 14: `am run --milestone --max-concurrent N`
runs through `typer.testing.CliRunner` on the real `cli.app` with no
`runner_factory` and no `driver`, so `orchestrate.run_milestone` reaches
`asyncio.run(supervise(...))`, the grafo tree, `cli.drive_subtask_async`,
`cli.default_runner_factory`, the real `ClaudeAdapter` and
`launcher.run_direct`. The only stand-in is the fake `claude` first on `PATH`,
armed with an implement-only rendezvous: at count 2 a run can only finish if
two lanes were inside implement at the same time. Unmarked on purpose.

Each test builds its own repo and board. On `parallel_board`, A (a1 -> a2) and
B (b1 -> b2) are independent roots and C (c1) is blocked by A alone: the
lone-blocker fast path. Levels are waves in the report only; C is scheduled by
its blocker A (supervisor-tree T1). The escalation and merged-base scenarios
are proven through `FakeDriver` in tests/test_orchestrate.py (test-tier V6);
this module keeps the real-subprocess wiring proofs.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
import eventlines

from agent_manager import board, cli, models
from agent_manager.store import db as store_db
from agent_manager.store import queries as store_queries
from agent_manager.store import writer as store_writer
from agent_manager.runtime.stop import StopSignal


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _cwds(entries) -> set[Path]:
    return {Path(entry["cwd"]).resolve() for entry in entries}


def _all_cards(parallel_board) -> list[str]:
    return [
        *(card for chain in parallel_board["subtasks"].values() for card in chain),
        *parallel_board["stories"].values(),
        parallel_board["milestone"],
    ]


def _run_two_lanes(parallel_board, rendezvous, run_milestone_cli):
    """Two lanes, rendezvous count 2: finishing at all proves a1 and b1 overlapped."""
    rendezvous.arm(2)
    return run_milestone_cli(
        parallel_board["root"], parallel_board["milestone"], max_concurrent=2
    )


INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""


def test_two_lanes_overlap_in_implement_and_the_milestone_finishes(
    parallel_board, rendezvous, run_milestone_cli, checkpoint_rows
):
    """Spec test 1."""
    root = parallel_board["root"]
    stories = parallel_board["stories"]
    subtasks = parallel_board["subtasks"]
    branches = parallel_board["branches"]
    a1, a2 = subtasks["A"]
    b1, b2 = subtasks["B"]
    (c1,) = subtasks["C"]
    main_before = _git(root, "rev-parse", "main").strip()

    result = _run_two_lanes(parallel_board, rendezvous, run_milestone_cli)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert [level["stories"] for level in data["levels"]] == [
        [stories["A"], stories["B"]],
        [stories["C"]],
    ]
    assert data["completed"] == [a1, a2, b1, b2, c1]
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(cli.worktree_for(root, INTEGRATION_BRANCH)),
        "merged": [stories["A"], stories["B"], stories["C"]],
        "resolved": [],
    }
    for tip in (branches[a2], branches[b2], branches[c1]):
        assert _is_ancestor(root, tip, INTEGRATION_BRANCH), tip
    # The env reached every implement child: one marker per subtask worktree.
    assert len(rendezvous.markers()) == 5

    for card_id in _all_cards(parallel_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id

    # Each story is its own stacked line, rooted where the DAG says.
    assert _is_ancestor(root, branches[a1], branches[a2])
    assert _is_ancestor(root, branches[b1], branches[b2])
    assert _is_ancestor(root, "main", branches[b1])
    assert not _is_ancestor(root, branches[a1], branches[b1])
    # C is blocked by A, so c1 roots on A's tip.
    assert _is_ancestor(root, branches[a2], branches[c1])
    assert not _is_ancestor(root, branches[b2], branches[c1])

    assert _git(root, "rev-parse", "main").strip() == main_before

    rows = checkpoint_rows(root, data["run_id"])
    assert rows > 0, rows


def _story_span(run: models.Run, story_id: str):
    """The earliest phase start and the latest phase end across one story's subtasks."""
    (story,) = [story for story in run.stories if story.card_id == story_id]
    phases = [phase for subtask in story.subtasks for phase in subtask.phases]
    starts = [phase.started_at for phase in phases if phase.started_at is not None]
    ends = [phase.ended_at for phase in phases if phase.ended_at is not None]
    assert starts and ends, story_id  # non-vacuity: the story really ran phases
    return min(starts), max(ends)


def test_one_lane_runs_the_level_s_stories_one_after_the_other(
    parallel_board, rendezvous, run_milestone_cli
):
    """Spec test 2: `--max-concurrent 1` behaves as the sequential runner did.
    Count 1 keeps the rendezvous satisfiable by a single lane."""
    root = parallel_board["root"]
    stories = parallel_board["stories"]
    rendezvous.arm(1)

    result = run_milestone_cli(root, parallel_board["milestone"], max_concurrent=1)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert len(rendezvous.markers()) == 5
    run = _load_run(root, data["run_id"])
    a_start, a_end = _story_span(run, stories["A"])
    b_start, b_end = _story_span(run, stories["B"])
    # Census order within the level: A's lane runs to the end before B's starts.
    assert a_end <= b_start, (a_start, a_end, b_start, b_end)


def test_the_journal_of_a_two_lane_run_is_strictly_increasing_and_rebuilds_the_projection(
    parallel_board, rendezvous, run_milestone_cli
):
    """Spec test 3, on its own two-lane run (same shape as spec test 1)."""
    root = parallel_board["root"]
    stories = parallel_board["stories"]

    result = _run_two_lanes(parallel_board, rendezvous, run_milestone_cli)

    assert result.exit_code == 0, (result.output, result.exception)
    run_id = _envelope(result)["run_id"]
    # Production wiring (the CLI, its lanes, adoption) writes no journal file.
    assert not eventlines.journal_file(run_id).exists()
    lines = eventlines.run_lines(run_id)
    seqs = [line.seq for line in lines]
    # Strictly increasing, not contiguous: the run's lease and control events
    # take `run_seq` numbers `run_lines` skips (card 1.2.7).
    assert seqs == sorted(set(seqs)) and seqs[0] >= 1, seqs
    # Non-vacuity: the two lanes' phase lines really interleave, so the order
    # was tested under concurrent records and not a sequential run. Phase lines
    # only: `record_plan` journals every story `pending` up front, so story
    # lines would interleave even in a one-lane run.
    phase_lines = [line for line in lines if line.event == "phase_upsert"]
    first_b = min(line.seq for line in phase_lines if line.story == stories["B"])
    last_a = max(line.seq for line in phase_lines if line.story == stories["A"])
    assert first_b < last_a, (first_b, last_a)

    st = store_writer.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        projection = st.load_run(run_id)
        rebuilt = st.rebuild_from_events(run_id)
        after = st.load_run(run_id)
    finally:
        st.close()
    assert projection is not None
    assert rebuilt == projection
    assert after == projection


def _launch_with_a1_review_failing(parallel_board, rendezvous, run_milestone_cli):
    """Two lanes, count 2, and a1's review failing through the production gate.

    The rendezvous makes a1 and b1 leave implement together. Lane A then runs
    one fake process (a1's review) before it escalates; lane B would need b1's
    review, verify and mark_done plus every phase of b2 to finish, so it is
    parked by the stop at some phase boundary. Which boundary is not fixed, so
    the assertions read it out of the report (see the plan's determinism note).
    """
    a1 = parallel_board["subtasks"]["A"][0]
    parallel_board["review_fail_marker"].write_text(
        f"{parallel_board['branches'][a1]}\n", encoding="utf-8"
    )
    rendezvous.arm(2)
    return run_milestone_cli(
        parallel_board["root"], parallel_board["milestone"], max_concurrent=2
    )


def test_a_relaunch_after_the_escalation_finishes_and_skips_done_subtasks(
    parallel_board, rendezvous, run_milestone_cli, read_fake_log
):
    """Spec test 5: relaunches after spec test 4's escalation, on a board of its own.

    Spec test 4 itself (the escalation parks the other lane and its dependent
    never starts) is proven through `FakeDriver` in tests/test_orchestrate.py
    (`test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending`).
    """
    root = parallel_board["root"]
    milestone = parallel_board["milestone"]
    stories = parallel_board["stories"]
    subtasks = parallel_board["subtasks"]
    branches = parallel_board["branches"]
    a1, a2 = subtasks["A"]
    b1, b2 = subtasks["B"]
    (c1,) = subtasks["C"]
    main_before = _git(root, "rev-parse", "main").strip()

    first = _launch_with_a1_review_failing(parallel_board, rendezvous, run_milestone_cli)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    every_subtask = [card for chain in subtasks.values() for card in chain]
    done_first = [
        card for card in every_subtask
        if board.show(card, repo_dir=root).status == "done"
    ]
    assert a1 not in done_first and c1 not in done_first, done_first

    # Fix the fake and drop the rendezvous, then relaunch the same command.
    parallel_board["review_fail_marker"].unlink()
    rendezvous.disarm()
    second = run_milestone_cli(root, milestone, max_concurrent=2)

    assert second.exit_code == 0, (second.output, second.exception)
    finished = _envelope(second)
    assert finished["done"] is True, finished
    assert finished["run_id"] != stopped["run_id"]
    assert [level["stories"] for level in finished["levels"]] == [
        [stories["A"], stories["B"]],
        [stories["C"]],
    ]
    expected = [
        card
        for key in ("A", "B", "C")
        for card in subtasks[key]
        if card not in done_first
    ]
    assert finished["completed"] == expected
    assert finished["integrated"]["merged"] == [stories["A"], stories["B"], stories["C"]]
    assert finished["integrated"]["resolved"] == []
    second_entries = read_fake_log(finished["run_id"])
    assert second_entries
    done_worktrees = {cli.worktree_for(root, branches[card]).resolve() for card in done_first}
    assert not (_cwds(second_entries) & done_worktrees)
    assert cli.worktree_for(root, branches[c1]).resolve() in _cwds(second_entries)

    for card_id in _all_cards(parallel_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
    assert _is_ancestor(root, branches[a1], branches[a2])
    assert _is_ancestor(root, branches[b1], branches[b2])
    assert _is_ancestor(root, branches[a2], branches[c1])
    assert _git(root, "rev-parse", "main").strip() == main_before


def test_the_lanes_await_drive_subtask_async_on_the_runs_loop(
    parallel_board, rendezvous, run_milestone_cli, monkeypatch
):
    """T3: one event loop per run. Every subtask goes through the awaitable
    `cli.drive_subtask_async`, handed the run's `StopSignal`; the sync
    `cli.drive_subtask` (its own `asyncio.run`) is never reached."""
    awaited: list[str] = []
    real = cli.drive_subtask_async

    async def spy(**kwargs):
        awaited.append(kwargs["card"].id)
        assert isinstance(kwargs["stop"], StopSignal), kwargs.get("stop")
        return await real(**kwargs)

    def forbidden(**kwargs):
        raise AssertionError("the sync cli.drive_subtask was reached from a milestone run")

    monkeypatch.setattr(cli, "drive_subtask_async", spy)
    monkeypatch.setattr(cli, "drive_subtask", forbidden)

    result = _run_two_lanes(parallel_board, rendezvous, run_milestone_cli)

    assert result.exit_code == 0, (result.output, result.exception)
    assert _envelope(result)["done"] is True
    assert sorted(awaited) == sorted(
        card for chain in parallel_board["subtasks"].values() for card in chain
    )


def test_no_rendezvous_is_left_armed_for_later_tests():
    """Review focus: the tests above arm the rendezvous through the
    function-scoped `monkeypatch`; it must be gone once they end, or every later
    fake in the session would wait on a stale dir. Kept last in the module."""
    assert "FAKE_CLAUDE_RENDEZVOUS_DIR" not in os.environ
    assert "FAKE_CLAUDE_RENDEZVOUS_COUNT" not in os.environ
