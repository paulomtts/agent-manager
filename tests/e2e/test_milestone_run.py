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
