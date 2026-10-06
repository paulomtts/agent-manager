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
import shlex
import subprocess
import sys
from pathlib import Path

from agent_manager import cli, models, store
from agent_manager.store import db as store_db

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""

SHARED = "shared.txt"
BASE_LINE = "the line both stories rewrite\n"
A_LINE = "story A rewrote this line\n"
B_LINE = "story B rewrote this line\n"

CALC_SOURCE = "def add(a, b):\n    return a + b\n"

TEST_CALC_SOURCE = (
    "from calc import add\n"
    "\n"
    "\n"
    "def test_add():\n"
    "    assert add(2, 3) == 5\n"
)

RENAMED_CALC_SOURCE = "def plus(a, b):\n    return a + b\n"
"""Story A renames `add` to `plus`..."""

RENAMED_TEST_SOURCE = (
    "from calc import plus\n"
    "\n"
    "\n"
    "def test_plus():\n"
    "    assert plus(2, 3) == 5\n"
)
"""...and updates the base test that used the old name."""

EXTRA_TEST_SOURCE = (
    "from calc import add\n"
    "\n"
    "\n"
    "def test_add_negative():\n"
    "    assert add(-1, 1) == 0\n"
)
"""Story B adds a test that imports the old name: green on base, red once A lands."""

CHECK_SOURCE = '''"""A stdlib-only suite runner: every test_* function in every test_*.py here."""
import importlib
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
failed = []
ran = 0
for path in sorted(ROOT.glob("test_*.py")):
    try:
        module = importlib.import_module(path.stem)
    except Exception as error:
        print(f"{path.name}: {type(error).__name__}: {error}", file=sys.stderr)
        failed.append(f"{path.name} ({type(error).__name__})")
        continue
    for name, test in sorted(vars(module).items()):
        if name.startswith("test_") and callable(test):
            ran += 1
            try:
                test()
            except Exception as error:
                print(f"{path.name}::{name}: {type(error).__name__}: {error}", file=sys.stderr)
                failed.append(f"{path.name}::{name} ({type(error).__name__})")
if failed:
    print("check.py: FAILED " + ", ".join(failed), file=sys.stderr)
    sys.exit(1)
if ran == 0:
    print("check.py: FAILED no test ran", file=sys.stderr)
    sys.exit(1)
print(f"check.py: {ran} passed")
'''
"""The repo's own suite. It does not depend on `pytest` being on the child's
`PATH`, and it writes no bytecode, so it never dirties a worktree."""

CHECK_COMMAND = shlex.join([sys.executable, "-B", "check.py"])
"""`verify.run_suite` splits with `shlex.split` and runs without a shell, so
the interpreter path is quoted."""


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


def _check(cwd: Path) -> subprocess.CompletedProcess:
    """Run the repo's own suite in `cwd`, the way the verify phase does."""
    return subprocess.run(
        [sys.executable, "-B", "check.py"], cwd=cwd, capture_output=True, text=True
    )


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


def test_a_clean_merge_that_breaks_the_suite_escalates_at_integrate(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Scenario 4: each story is green alone; together the suite is red."""
    root = two_story_board["root"]
    subtasks = two_story_board["subtasks"]
    branches = two_story_board["branches"]
    main_before = _seed(
        root,
        {"calc.py": CALC_SOURCE, "test_calc.py": TEST_CALC_SOURCE, "check.py": CHECK_SOURCE},
    )
    assert _check(root).returncode == 0  # the base is green
    _write_edits(
        two_story_board,
        {
            "A": {"calc.py": RENAMED_CALC_SOURCE, "test_calc.py": RENAMED_TEST_SOURCE},
            "B": {"test_calc_extra.py": EXTRA_TEST_SOURCE},
        },
    )

    result = run_milestone_cli(root, two_story_board["milestone"], verify=[CHECK_COMMAND])

    assert result.exit_code == cli.EXIT_ESCALATED, (result.output, result.exception)
    data = _envelope(result)
    assert data["escalated"] is True, data
    assert data["phase"] == "integrate"
    assert data["story"] is None  # the final verification, not a tip
    assert data["files"] == []
    assert "check.py" in data["detail"]
    assert "test_calc_extra.py" in data["detail"]
    assert "integrated" not in data

    worktree = _integration_worktree(root)
    assert not _merge_in_progress(worktree)
    for key in ("A", "B"):
        branch = branches[subtasks[key][0]]
        assert _is_ancestor(root, branch, INTEGRATION_BRANCH), key
        # Each story passed its own verify phase, and is still green alone.
        assert _check(cli.worktree_for(root, branch)).returncode == 0, key
    red = _check(worktree)
    assert red.returncode != 0
    assert "test_calc_extra.py" in red.stderr

    # The merge was textually clean, so no resolver was ever dispatched.
    assert "resolve" not in _phases(read_fake_log(data["run_id"]))
    assert _load_run(root, data["run_id"]).status == "escalated"
    _assert_base_untouched(root, main_before)
