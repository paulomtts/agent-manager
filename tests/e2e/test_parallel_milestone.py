"""Default-suite e2e tier: parallel stories through the production wiring.

Addendum P7 and main spec section 14: `am run --milestone --max-concurrent N`
runs through `typer.testing.CliRunner` on the real `cli.app` with no
`runner_factory` and no `driver`, so `orchestrate.run_milestone` reaches
`cli.drive_subtask`, `cli.default_runner_factory`, the real `ClaudeAdapter` and
`launcher.run_direct`. The only stand-in is the fake `claude` first on `PATH`,
armed with an implement-only rendezvous: at count 2 a run can only finish if
two lanes were inside implement at the same time. Unmarked on purpose.

Each test builds its own repo and board (`parallel_board`): A (a1 -> a2) and B
(b1 -> b2) are independent roots, C (c1) is blocked by A.
"""

import json
import subprocess
from pathlib import Path

from agent_manager import board, cli, models, store


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
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
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


def _subtask_rows(run: models.Run) -> dict[str, models.SubtaskRun]:
    return {
        subtask.card_id: subtask
        for story in run.stories
        for subtask in story.subtasks
    }


def _run_two_lanes(parallel_board, rendezvous, run_milestone_cli):
    """Two lanes, rendezvous count 2: finishing at all proves a1 and b1 overlapped."""
    rendezvous.arm(2)
    return run_milestone_cli(
        parallel_board["root"], parallel_board["milestone"], max_concurrent=2
    )


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or parallel wiring stops being
    checked on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_two_lanes_overlap_in_implement_and_the_milestone_finishes(
    parallel_board, rendezvous, run_milestone_cli
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


def test_the_journal_of_a_two_lane_run_is_contiguous_and_rebuilds_the_projection(
    parallel_board, rendezvous, run_milestone_cli
):
    """Spec test 3, on its own two-lane run (same shape as spec test 1)."""
    root = parallel_board["root"]
    stories = parallel_board["stories"]

    result = _run_two_lanes(parallel_board, rendezvous, run_milestone_cli)

    assert result.exit_code == 0, (result.output, result.exception)
    run_id = _envelope(result)["run_id"]
    lines = store.Journal(run_id).read()
    seqs = [line.seq for line in lines]
    assert seqs == list(range(1, len(seqs) + 1))
    # Non-vacuity: the two lanes' phase lines really interleave, so contiguity
    # was tested under concurrent appends and not a sequential run. Phase lines
    # only: `record_plan` journals every story `pending` up front, so story
    # lines would interleave even in a one-lane run.
    phase_lines = [line for line in lines if line.event == "phase_upsert"]
    first_b = min(line.seq for line in phase_lines if line.story == stories["B"])
    last_a = max(line.seq for line in phase_lines if line.story == stories["A"])
    assert first_b < last_a, (first_b, last_a)

    st = store.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        projection = st.load_run(run_id)
        rebuilt = st.rebuild_from_journal(run_id)
        after = st.load_run(run_id)
    finally:
        st.close()
    assert projection is not None
    assert rebuilt == projection
    assert after == projection
