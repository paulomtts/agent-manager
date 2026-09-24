"""Default-suite e2e tier: `am run --milestone` through the production wiring.

Addendum O8 and main spec §14: the command runs through `typer.testing.CliRunner`
on the real `cli.app`, with no `runner_factory`. So `orchestrate.run_milestone`
reaches `cli.drive_subtask`, `cli.default_runner_factory`, the real
`ClaudeAdapter` and `launcher.run_direct`, and the only stand-in is the fake
`claude` first on `PATH`. Unmarked on purpose: this costs no model and must run
on every `uv run pytest`.

Each test builds its own repo and board (`milestone_board`), because a
milestone run moves every card it touches.
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


def _all_cards(milestone_board) -> list[str]:
    return [
        *(card for chain in milestone_board["subtasks"].values() for card in chain),
        *milestone_board["stories"].values(),
        milestone_board["milestone"],
    ]


def test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line(
    milestone_board, run_milestone_cli
):
    """Spec test 1 / acceptance 2."""
    root = milestone_board["root"]
    subtasks = milestone_board["subtasks"]
    stories = milestone_board["stories"]
    branches = milestone_board["branches"]
    order = [*subtasks["A"], *subtasks["B"], *subtasks["C"]]
    main_before = _git(root, "rev-parse", "main").strip()

    result = run_milestone_cli(root, milestone_board["milestone"])

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert data["completed"] == order
    assert [level["stories"] for level in data["levels"]] == [
        [stories["A"]],
        [stories["B"]],
        [stories["C"]],
    ]

    # Done ON THE BOARD, subtasks by `mark_done` and stories and the milestone
    # by the rollup walk. This test writes no status itself.
    for card_id in _all_cards(milestone_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id

    # One stacked line: each branch contains its predecessor's commits.
    chain = [branches[card_id] for card_id in order]
    for earlier, later in zip(chain, chain[1:]):
        assert _is_ancestor(root, earlier, later), (earlier, later)
    assert _is_ancestor(root, branches[subtasks["A"][-1]], branches[subtasks["B"][0]])

    # Every commit the run made, on every subtask branch, carries the trailer.
    for branch in chain:
        revisions = _git(root, "rev-list", f"main..{branch}").split()
        assert revisions, branch  # non-vacuity
        for revision in revisions:
            message = _git(root, "show", "-s", "--format=%B", revision)
            assert any(
                line.startswith("Plan-Hash: ") for line in message.splitlines()
            ), (branch, revision, message)

    assert _git(root, "rev-parse", "main").strip() == main_before


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_production_wiring.py`'s guard: no `e2e` marker may reach this
    module, or the milestone wiring stops being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


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


def test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it(
    milestone_board, review_fail_marker, run_milestone_cli, read_fake_log
):
    """Spec test 2 / acceptances 3 and 5."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    stories = milestone_board["stories"]
    branches = milestone_board["branches"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    c_worktree = cli.worktree_for(root, branches[c1])
    b_worktree = cli.worktree_for(root, branches[b1])
    review_fail_marker.write_text(f"{branches[b1]}\n", encoding="utf-8")

    # First launch: B's review fails through the production review gate.
    first = run_milestone_cli(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["escalated"] is True, stopped
    assert stopped["story"] == stories["B"]
    assert stopped["subtask"] == b1
    assert stopped["failed_phase"] == "review"
    assert "review_gate" in stopped["detail"]
    assert "review-fail marker" in stopped["detail"]
    # The marker never dirtied B's worktree.
    assert _git(b_worktree, "status", "--porcelain") == ""

    # Story C never started: no worktree, no branch, no phase, no agent.
    assert not c_worktree.exists()
    assert branches[c1] not in _git(root, "branch", "--format=%(refname:short)").split()
    rows = {
        subtask.card_id: subtask
        for story in _load_run(root, stopped["run_id"]).stories
        for subtask in story.subtasks
    }
    assert rows[b1].status == "escalated"
    assert rows[c1].status == "pending"
    assert rows[c1].phases == []
    first_entries = read_fake_log(stopped["run_id"])
    assert first_entries  # non-vacuity: agents did run in this run
    assert c_worktree.resolve() not in _cwds(first_entries)
    # Never started: brd reports it `blocked` (its story is blocked by B) or
    # `todo`, never `in-progress` or `done`.
    assert board.show(c1, repo_dir=root).status in ("todo", "blocked")

    # Fix the fake, then relaunch the same command.
    review_fail_marker.unlink()
    second = run_milestone_cli(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    finished = _envelope(second)
    assert finished["done"] is True, finished
    assert finished["run_id"] != stopped["run_id"]
    assert finished["completed"] == [b1, c1]
    second_entries = read_fake_log(finished["run_id"])
    assert second_entries
    a_worktrees = {cli.worktree_for(root, branches[card]).resolve() for card in (a1, a2)}
    assert not (_cwds(second_entries) & a_worktrees)
    for card_id in _all_cards(milestone_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
    assert _is_ancestor(root, branches[a2], branches[b1])
    assert _is_ancestor(root, branches[b1], branches[c1])

    # Review focus: relaunching a finished milestone drives nothing.
    third = run_milestone_cli(root, milestone)

    assert third.exit_code == 0, (third.output, third.exception)
    idle = _envelope(third)
    assert idle["done"] is True
    assert idle["completed"] == []
    assert read_fake_log(idle["run_id"]) == []
