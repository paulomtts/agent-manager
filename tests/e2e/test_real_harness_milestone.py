"""End-to-end tier: the opt-in real-harness MILESTONE test.

Orchestration addendum O8 and section 3 acceptance 6
(`docs/superpowers/specs/2026-09-24-orchestration-design.md`), placed per main
spec section 14 (Testing): a real two-story toy milestone driven through
`orchestrate.run_milestone` against the real `claude -p`.

The pipeline cannot exercise this test. It is excluded from the default suite
(`pyproject.toml`'s `addopts` carries `-m "not e2e"`) and every run spends real
money, so the real run is a human step. To run it: `uv run pytest -m e2e`. A
bare path invocation such as
`uv run pytest tests/e2e/test_real_harness_milestone.py` is still deselected by
`addopts` and exits 5 -- pass `-m e2e` alongside the path.

The marker is applied HERE and only here. Marking it from
`tests/e2e/conftest.py` would drag the free, fake-claude
`test_production_wiring.py` out of the default suite, which its own
`test_this_module_runs_in_the_default_suite_unmarked` forbids.

Reused from `tests/e2e/conftest.py`: `toolchain`, `project` and
`module_monkeypatch`. The conftest's `cards` builds a single chain, so this
module builds its own two-story milestone in `toy_milestone`. `completed_run`
is overridden without `fake_claude_bin`: no fake `claude` is ever on `PATH`
here, only the real one.
"""

import ast
import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agent_manager import board, cli, dag, orchestrate

pytestmark = pytest.mark.e2e

BRANCH_PREFIX = "e2e-real-m"
"""Distinct from `m1`, `m3` and `e2e-real`, so this run can never land on a
branch or worktree path another e2e module uses."""

VERIFY_COMMANDS = (f"{shlex.quote(sys.executable)} -m pytest -q",)
"""A real, green command for the toy suite. `verify.run_suite` splits it with
`shlex.split` and runs it without a shell, so the interpreter path is quoted.
The test's own interpreter already has pytest.

Re-declared rather than imported: `--import-mode=importlib` puts nothing on
`sys.path`, so conftest and sibling module names are not importable."""

PLAN_HASH_TRAILER = "Plan-Hash:"

TOY_GITIGNORE_ENTRIES = ("__pycache__/", ".pytest_cache/")
"""What the verify run leaves behind. Ignored, so it never dirties a worktree
and trips `review_gate`."""

TOY_CALC_SOURCE = '"""A toy calculator the e2e agents extend."""\n'

TOY_TEST_SOURCE = (
    "import calc\n"
    "\n"
    "\n"
    "def test_calc_module_imports():\n"
    "    assert calc.__doc__\n"
)
"""A baseline passing test: with no test at all, pytest exits 5 and the
verify command would be red before any agent runs."""

A1_DESCRIPTION = (
    "In `calc.py` at the repository root, add a function `add(a, b)` that "
    "returns `a + b`. In `test_calc.py`, add a test `test_add` that checks "
    "`calc.add(2, 3) == 5`. Keep the existing test passing. The suite runs "
    "with `python -m pytest -q` from the repository root."
)

A2_DESCRIPTION = (
    "In `calc.py` at the repository root, add a function `sub(a, b)` that "
    "returns `a - b`. Leave `add` and its test unchanged. In `test_calc.py`, "
    "add a test `test_sub` that checks `calc.sub(5, 3) == 2`. The suite runs "
    "with `python -m pytest -q` from the repository root."
)

B1_DESCRIPTION = (
    "In `calc.py` at the repository root, add a function `calc(op, a, b)` "
    "that returns `add(a, b)` when `op` is `\"+\"`, returns `sub(a, b)` when "
    "`op` is `\"-\"`, and raises `ValueError` for any other `op`. No "
    "command-line interface: no `argparse`, no `sys.argv`, no `__main__` "
    "block. Leave `add`, `sub` and their tests unchanged. In `test_calc.py`, "
    "add tests for both operators and for the `ValueError`. The suite runs "
    "with `python -m pytest -q` from the repository root."
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _plan_hashes(message: str) -> list[str]:
    """Every `Plan-Hash:` trailer VALUE in one commit message.

    Matched at column 0, not after `.strip()`: a git trailer is unindented by
    definition, so an indented body line that reads `Plan-Hash: ...` is a
    body mention, not a trailer.
    """
    return [
        line.split(":", 1)[1].strip()
        for line in message.splitlines()
        if line.startswith(PLAN_HASH_TRAILER)
    ]


def _block(root: Path, card_id: str, blocker: str) -> None:
    """`brd block <id> --by <blocker>`, as `tests/e2e/conftest.py::_block` does."""
    subprocess.run(
        ["brd", "block", card_id, "--by", blocker],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _add_card(
    root: Path,
    title: str,
    parent: str | None = None,
    blocked_by: Sequence[str] = (),
    description: str | None = None,
) -> str:
    """`tests/e2e/conftest.py::_add_card`, plus an optional `--description`.

    The subtask's full instruction lives in its description, so the title
    stays short.
    """
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    if description is not None:
        argv += ["--description", description]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    card_id = json.loads(completed.stdout)["data"]["id"]
    for blocker in blocked_by:
        _block(root, card_id, blocker)
    return card_id


def _seed_toy_repo(root: Path) -> None:
    """Commit a green toy suite to the current branch of `root` (`main`).

    `calc.py`, a passing `test_calc.py`, and the verify run's byproducts
    appended to the existing `.gitignore`, all in one commit.
    """
    (root / "calc.py").write_text(TOY_CALC_SOURCE, encoding="utf-8")
    (root / "test_calc.py").write_text(TOY_TEST_SOURCE, encoding="utf-8")
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    gitignore.write_text(
        existing + "".join(f"{entry}\n" for entry in TOY_GITIGNORE_ENTRIES),
        encoding="utf-8",
    )
    _git(root, "add", "calc.py", "test_calc.py", ".gitignore")
    _git(root, "commit", "-m", "toy calculator baseline")


def _top_level_functions(source: str) -> set[str]:
    """The names of every module-level `def` in `source`."""
    return {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


@pytest.fixture(scope="module")
def real_claude() -> Path:
    """The real `claude`, or a skip that says so in as many words.

    The conftest's `toolchain` fixture only checks `git` and `brd`, so this
    guard is not redundant with it.
    """
    found = shutil.which("claude")
    if found is None:
        pytest.skip(
            "the real `claude` CLI is not on PATH; the opt-in e2e tier needs it "
            "to perform a real paid run (install claude and put it on PATH, or "
            "just run `uv run pytest`, which deselects this test)"
        )
    return Path(found)


@pytest.fixture(scope="module")
def toy_milestone(project) -> dict[str, Any]:
    """A green toy suite on `main`, then milestone M with two stories.

    Story A: a1 (`add`) then a2 (`sub`), a2 blocked by a1 so the census order
    is fixed. Story B, blocked by A: b1 (`calc`). Branch names come from
    `dag`, never retyped here. `main_sha` is recorded before the run.
    """
    _seed_toy_repo(project)
    milestone = _add_card(project, "Milestone: a toy calculator, real claude")
    story_a = _add_card(project, "Story A: add and sub in calc.py", milestone)
    story_b = _add_card(
        project, "Story B: a CLI-free calc dispatcher", milestone, blocked_by=[story_a]
    )
    a1 = _add_card(
        project, "a1: add add(a, b) to calc.py", story_a, description=A1_DESCRIPTION
    )
    a2 = _add_card(
        project,
        "a2: add sub(a, b) to calc.py",
        story_a,
        blocked_by=[a1],
        description=A2_DESCRIPTION,
    )
    b1 = _add_card(
        project, "b1: add calc(op, a, b) to calc.py", story_b, description=B1_DESCRIPTION
    )
    subtasks = {"a1": a1, "a2": a2, "b1": b1}
    branches = {
        card_id: dag.task_branch(BRANCH_PREFIX, board.show(card_id, repo_dir=project))
        for card_id in subtasks.values()
    }
    return {
        "milestone": milestone,
        "stories": {"A": story_a, "B": story_b},
        "subtasks": subtasks,
        "branches": branches,
        "main_sha": _git(project, "rev-parse", "main").strip(),
    }


@pytest.fixture(scope="module")
def completed_run(real_claude, project, toy_milestone) -> dict[str, Any]:
    """One real, paid `orchestrate.run_milestone` -- no `runner_factory`, no
    `driver`, no fake on `PATH`.

    Overrides the conftest fixture of the same name. `fake_claude_bin` is
    deliberately absent: `cli.drive_subtask` and `cli.default_runner_factory`
    resolve `claude` on `PATH`, and the point of this module is that what
    they find there is real. `real_claude` comes first so a missing `claude`
    skips before the toy milestone is built.
    """
    return orchestrate.run_milestone(
        toy_milestone["milestone"],
        repo_dir=project,
        base_branch="main",
        branch_prefix=BRANCH_PREFIX,
        commands=list(VERIFY_COMMANDS),
    )


def test_the_real_claude_drives_a_two_story_milestone_to_done(
    real_claude, project, toy_milestone, completed_run
):
    """The whole deliverable: a real `claude -p` takes both stories to `done`,
    stacked branch on branch, each subtask's commits carrying one Plan-Hash,
    with the toy suite green on the last tip and `main` untouched.

    justification: verifies the full stacked-branch milestone flow survives
    the real CLI's actual output variability and timing across several
    sequential real dispatches -- a fake-claude stand-in's fixed, instant
    replies cannot exercise that variability."""
    # `.get`, not `[...]`: an escalation payload has no `status` or `completed`.
    assert completed_run.get("done") is True, (
        completed_run.get("story"),
        completed_run.get("subtask"),
        completed_run.get("failed_phase"),
        completed_run.get("detail"),
        completed_run.get("warnings"),
    )

    subtasks = toy_milestone["subtasks"]
    a1, a2, b1 = subtasks["a1"], subtasks["a2"], subtasks["b1"]
    stories = toy_milestone["stories"]
    branches = toy_milestone["branches"]

    assert completed_run["completed"] == [a1, a2, b1], completed_run["completed"]
    assert {"story": stories["B"], "tip": branches[b1]} in completed_run["tips"], (
        completed_run["tips"]
    )

    # `mark_done` is best effort, so the payload alone is not proof. The
    # stories and the milestone reach `done` only through rollup.
    for card_id in (a1, a2, b1, stories["A"], stories["B"], toy_milestone["milestone"]):
        status = board.show(card_id, repo_dir=project).status
        assert status == "done", (card_id, status, completed_run["warnings"])

    # Each subtask's branch is stacked on its predecessor; story B roots on
    # story A's tip.
    chain = (
        (a1, "main", branches[a1]),
        (a2, branches[a1], branches[a2]),
        (b1, branches[a2], branches[b1]),
    )
    for card_id, predecessor, branch in chain:
        ancestry = subprocess.run(
            ["git", "-C", str(project), "merge-base", "--is-ancestor", predecessor, branch],
            capture_output=True,
            text=True,
        )
        assert ancestry.returncode == 0, (card_id, predecessor, branch, ancestry.stderr)

    # Every commit in each subtask's own range carries a Plan-Hash trailer,
    # with exactly one distinct value per subtask.
    for card_id, predecessor, branch in chain:
        revisions = _git(project, "rev-list", f"{predecessor}..{branch}").split()
        assert revisions, (card_id, f"{branch} carries no commits beyond {predecessor}")
        hashes: set[str] = set()
        for revision in revisions:
            message = _git(project, "show", "-s", "--format=%B", revision)
            values = _plan_hashes(message)
            assert values, (card_id, revision, message)
            hashes.update(values)
        assert len(hashes) == 1, (card_id, hashes)

    # The toy suite passes on the last tip, and the tip really did the work.
    last_tip = cli.worktree_for(project, branches[b1])
    assert last_tip.is_dir(), last_tip
    for command in VERIFY_COMMANDS:
        suite = subprocess.run(
            shlex.split(command), cwd=last_tip, capture_output=True, text=True
        )
        assert suite.returncode == 0, (command, suite.stdout[-2000:], suite.stderr[-2000:])
    defined = _top_level_functions((last_tip / "calc.py").read_text(encoding="utf-8"))
    assert {"add", "sub", "calc"} <= defined, sorted(defined)

    # The base branch is untouched.
    assert _git(project, "rev-parse", "main").strip() == toy_milestone["main_sha"]
