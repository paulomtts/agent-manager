"""Default-suite e2e tier: Integrate through the production wiring (addendum I7).

`am run --milestone` runs through `typer.testing.CliRunner` on the real
`cli.app` with no `runner_factory`, so `orchestrate.run_milestone` reaches
`integration.integrate_milestone`, `cli.default_runner_factory`, the real
`ClaudeAdapter` and `launcher.run_direct`. The only stand-in is the fake
`claude` first on `PATH`. It resolves a conflict from the resolve brief's
`## merge_tip` and `## conflict_files` alone. Unmarked on purpose: this costs
no model and must run on every `uv run pytest`.

Each test builds its own repo and board (`two_story_board`): stories A and B
are independent roots with one subtask each. The implement-edits marker makes
each story's implement write the files a scenario needs, keyed by the brief's
`## branch`.
"""

import json
import subprocess
from pathlib import Path

from agent_manager import cli, models, store

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""


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


def _integration_worktree(root: Path) -> Path:
    return cli.worktree_for(root, INTEGRATION_BRANCH)


def _merge_in_progress(worktree: Path) -> bool:
    """Whether git holds a MERGE_HEAD in `worktree`."""
    probe = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def _seed(root: Path, files: dict[str, str]) -> str:
    """Commit the scenario's base files on `main`, and return `main`'s new tip."""
    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed the scenario's base files")
    return _git(root, "rev-parse", "main").strip()


def _write_edits(board: dict, edits: dict[str, dict[str, str]]) -> None:
    """The implement-edits marker: story key -> files, keyed by the story's subtask branch."""
    table = {
        board["branches"][board["subtasks"][key][0]]: files
        for key, files in edits.items()
    }
    board["implement_edits_marker"].write_text(json.dumps(table), encoding="utf-8")


def _phases(entries) -> list[str]:
    return [entry["phase"] for entry in entries]


def _all_phase_names(run: models.Run) -> set[str]:
    return {
        phase.name
        for story in run.stories
        for subtask in story.subtasks
        for phase in subtask.phases
    }


def _assert_base_untouched(root: Path, main_before: str) -> None:
    """Integrate rule I5: the base branch never moves and nothing is pushed."""
    assert _git(root, "rev-parse", "main").strip() == main_before
    assert _git(root, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert _git(root, "remote").strip() == ""
    assert _git(root, "for-each-ref", "refs/remotes").strip() == ""


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or Integrate stops being checked
    on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_stories_that_touch_different_files_integrate_with_no_resolver(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Scenario 3: a textually clean merge never dispatches an agent."""
    root = two_story_board["root"]
    stories = two_story_board["stories"]
    main_before = _git(root, "rev-parse", "main").strip()
    _write_edits(
        two_story_board,
        {"A": {"a.txt": "written by story A\n"}, "B": {"b.txt": "written by story B\n"}},
    )

    result = run_milestone_cli(root, two_story_board["milestone"])

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(_integration_worktree(root)),
        "merged": [stories["A"], stories["B"]],
        "resolved": [],
    }
    assert _git(root, "show", f"{INTEGRATION_BRANCH}:a.txt") == "written by story A\n"
    assert _git(root, "show", f"{INTEGRATION_BRANCH}:b.txt") == "written by story B\n"

    entries = read_fake_log(data["run_id"])
    assert entries  # non-vacuity: the stories' agents did run
    assert "resolve" not in _phases(entries)
    run = _load_run(root, data["run_id"])
    assert run.status == "done"
    assert "integrate" not in {story.card_id for story in run.stories}
    assert "Integrate" not in {story.title for story in run.stories}
    assert "resolve" not in _all_phase_names(run)

    assert not _merge_in_progress(_integration_worktree(root))
    _assert_base_untouched(root, main_before)
