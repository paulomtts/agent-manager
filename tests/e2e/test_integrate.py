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

SHARED = "shared.txt"
BASE_LINE = "the line both stories rewrite\n"
A_LINE = "story A rewrote this line\n"
B_LINE = "story B rewrote this line\n"


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


def _same_line_setup(board: dict) -> str:
    """Seed `shared.txt` on main; A and B each rewrite its one line differently."""
    main_before = _seed(board["root"], {SHARED: BASE_LINE})
    _write_edits(board, {"A": {SHARED: A_LINE}, "B": {SHARED: B_LINE}})
    return main_before


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


def test_a_same_line_conflict_is_resolved_verified_and_left_on_the_integration_branch(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Scenario 1: B's merge conflicts with A's; the resolver keeps both sides."""
    root = two_story_board["root"]
    stories = two_story_board["stories"]
    subtasks = two_story_board["subtasks"]
    branches = two_story_board["branches"]
    main_before = _same_line_setup(two_story_board)
    worktree = _integration_worktree(root)

    result = run_milestone_cli(root, two_story_board["milestone"])

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(worktree),
        "merged": [stories["A"], stories["B"]],
        "resolved": [stories["B"]],
    }
    # Both edits, ours (A, merged first) then theirs (B), and no markers.
    assert (worktree / SHARED).read_text(encoding="utf-8") == A_LINE + B_LINE
    assert _git(root, "show", f"{INTEGRATION_BRANCH}:{SHARED}") == A_LINE + B_LINE
    assert not _merge_in_progress(worktree)
    assert _git(worktree, "status", "--porcelain") == ""
    for key in ("A", "B"):
        assert _is_ancestor(root, branches[subtasks[key][0]], INTEGRATION_BRANCH), key

    resolves = [entry for entry in read_fake_log(data["run_id"]) if entry["phase"] == "resolve"]
    assert len(resolves) == 1, resolves
    assert Path(resolves[0]["cwd"]).resolve() == worktree.resolve()

    # `done` only after the integrate workflow's own verify phase passed.
    run = _load_run(root, data["run_id"])
    assert run.status == "done"
    (integrate_story,) = [story for story in run.stories if story.card_id == "integrate"]
    assert integrate_story.title == "Integrate"
    assert integrate_story.status == "done"
    (resolver_row,) = integrate_story.subtasks
    assert resolver_row.card_id == stories["B"]
    assert [phase.name for phase in resolver_row.phases] == ["resolve", "verify"]
    assert {phase.status for phase in resolver_row.phases} == {"done"}

    _assert_base_untouched(root, main_before)


def test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete(
    two_story_board, fake_resolver, run_milestone_cli, read_fake_log
):
    """Scenario 2: git, not the resolver's `resolved` flag, decides."""
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    main_before = _same_line_setup(two_story_board)
    worktree = _integration_worktree(root)
    fake_resolver.refuse()

    first = run_milestone_cli(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    data = _envelope(first)
    assert data["escalated"] is True, data
    assert data["phase"] == "integrate"
    assert data["story"] == stories["B"]
    assert SHARED in data["files"]
    assert data["run_id"]
    assert str(worktree) in data["detail"]
    assert "integrated" not in data
    assert _merge_in_progress(worktree)
    resolves = [entry for entry in read_fake_log(data["run_id"]) if entry["phase"] == "resolve"]
    assert resolves  # non-vacuity: the resolver really was dispatched
    assert {Path(entry["cwd"]).resolve() for entry in resolves} == {worktree.resolve()}
    run = _load_run(root, data["run_id"])
    assert run.status == "escalated"
    (integrate_story,) = [story for story in run.stories if story.card_id == "integrate"]
    assert integrate_story.status == "escalated"
    _assert_base_untouched(root, main_before)

    # A human finishes the merge the resolver left, where the detail says to.
    (worktree / SHARED).write_text(A_LINE + B_LINE, encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--no-edit")
    fake_resolver.reset()
    finished_tip = _git(root, "rev-parse", INTEGRATION_BRANCH).strip()

    second = run_milestone_cli(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    done = _envelope(second)
    assert done["done"] is True, done
    assert done["run_id"] != data["run_id"]
    assert done["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(worktree),
        "merged": [stories["A"], stories["B"]],
        "resolved": [],
    }
    assert not _merge_in_progress(worktree)
    assert _git(root, "rev-parse", INTEGRATION_BRANCH).strip() == finished_tip
    assert "resolve" not in _phases(read_fake_log(done["run_id"]))
    assert _load_run(root, done["run_id"]).status == "done"
    _assert_base_untouched(root, main_before)


def test_relaunching_an_integrated_milestone_moves_no_branch(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Scenario 5: after a resolved Integrate, a relaunch re-merges nothing."""
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    subtasks = two_story_board["subtasks"]
    branches = two_story_board["branches"]
    main_before = _same_line_setup(two_story_board)
    worktree = _integration_worktree(root)

    first = run_milestone_cli(root, milestone)

    assert first.exit_code == 0, (first.output, first.exception)
    first_data = _envelope(first)
    assert first_data["integrated"]["resolved"] == [stories["B"]]  # non-vacuity
    watched = [INTEGRATION_BRANCH, "main", branches[subtasks["A"][0]], branches[subtasks["B"][0]]]
    tips_before = {ref: _git(root, "rev-parse", ref).strip() for ref in watched}

    second = run_milestone_cli(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    data = _envelope(second)
    assert data["done"] is True, data
    assert data["run_id"] != first_data["run_id"]
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(worktree),
        "merged": [stories["A"], stories["B"]],
        "resolved": [],
    }
    assert {ref: _git(root, "rev-parse", ref).strip() for ref in watched} == tips_before
    assert "resolve" not in _phases(read_fake_log(data["run_id"]))
    assert not _merge_in_progress(worktree)
    assert _git(worktree, "status", "--porcelain") == ""
    _assert_base_untouched(root, main_before)
