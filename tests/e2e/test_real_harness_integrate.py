"""End-to-end tier: the opt-in real-harness INTEGRATE conflict test.

Integrate addendum I7 and acceptance 7
(`docs/superpowers/specs/2026-09-25-integrate-design.md`), placed per main
spec section 14 (Testing): a real toy milestone with two INDEPENDENT stories
that both replace the same line of `calc.py`, driven through
`orchestrate.run_milestone(..., max_concurrent=2)` against the real
`claude -p`, with no `runner_factory`, no `driver`, and no fake `claude` on
`PATH`. The two story tips conflict at Integrate, a real resolver finishes
the merge, and git -- never the resolver's own report -- judges the result
(I3). The contract checked is I1 to I6 of the addendum.

The pipeline cannot run this test. It is excluded from the default suite
(`pyproject.toml:38`: `addopts` carries `-m "not e2e"`; the `e2e` marker is
registered at `pyproject.toml:31`), and every run spends real money. The real
run is a human step afterwards: `uv run pytest -m e2e`. A bare path invocation
such as `uv run pytest tests/e2e/test_real_harness_integrate.py` is still
deselected by `addopts` and exits 5 -- pass `-m e2e` alongside the path.

The marker is applied HERE and only here. Marking it from
`tests/e2e/conftest.py` would drag the free, fake-claude
`test_production_wiring.py` out of the default suite, which its own
`test_this_module_runs_in_the_default_suite_unmarked` forbids.

Reused from `tests/e2e/conftest.py`: `toolchain`, `project` and
`module_monkeypatch`. The toy-repo helpers are re-declared from
`test_real_harness_parallel.py` rather than imported, because
`--import-mode=importlib` puts nothing on `sys.path`. `completed_run` is
overridden without `fake_claude_bin`: only the real `claude` is ever on
`PATH` here.
"""

import ast
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_manager import board, cli, dag, integration, orchestrate, store

pytestmark = pytest.mark.e2e

BRANCH_PREFIX = "e2e-real-i"
"""Distinct from `m1`, `m3`, `e2e-real`, `e2e-real-m` and `e2e-real-p`, so this
run can never land on a branch or worktree path another e2e module uses."""

VERIFY_COMMANDS = (f"{shlex.quote(sys.executable)} -m pytest -q",)
"""A real, green command for the toy suite. `verify.run_suite` splits it with
`shlex.split` and runs it without a shell, so the interpreter path is quoted.
The test's own interpreter already has pytest."""

TOY_GITIGNORE_ENTRIES = ("__pycache__/", ".pytest_cache/")
"""What the verify run leaves behind. Ignored, so it never dirties a worktree
and trips `review_gate` or Integrate's clean-tree check."""

OPERATIONS_MARKER = "# OPERATIONS GO HERE"
"""The one line both stories replace, so their tips conflict when merged."""

TOY_CALC_SOURCE = (
    '"""A toy calculator the e2e agents extend."""\n'
    "\n"
    f"{OPERATIONS_MARKER}\n"
)

TOY_CALC_TEST_SOURCE = (
    "import calc\n"
    "\n"
    "\n"
    "def test_calc_module_imports():\n"
    "    assert calc.__doc__\n"
)
"""A baseline passing test: with no test at all, pytest exits 5 and the verify
command would be red before any agent runs."""

A1_DESCRIPTION = (
    "In `calc.py` at the repository root, replace the line "
    f"`{OPERATIONS_MARKER}` with a function `add(a, b)` that returns `a + b`. "
    "Add a new file `test_add.py` at the repository root with a test "
    "`test_add` that checks `calc.add(2, 3) == 5`. Keep the existing tests "
    "passing. Do not create or touch `test_sub.py`. The suite runs with "
    "`python -m pytest -q` from the repository root."
)

B1_DESCRIPTION = (
    "In `calc.py` at the repository root, replace the line "
    f"`{OPERATIONS_MARKER}` with a function `sub(a, b)` that returns `a - b`. "
    "Add a new file `test_sub.py` at the repository root with a test "
    "`test_sub` that checks `calc.sub(5, 3) == 2`. Keep the existing tests "
    "passing. Do not create or touch `test_add.py`. The suite runs with "
    "`python -m pytest -q` from the repository root."
)

CONFLICT_MARKER_PATTERN = r"^(<<<<<<<|=======|>>>>>>>)( |$)"
"""A git conflict marker at the start of a line, as `git grep -E` reads it."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _is_ancestor(root: Path, earlier: str, later: str) -> int:
    """`git merge-base --is-ancestor`'s exit code: 0 yes, 1 no."""
    return subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    ).returncode


def _add_card(
    root: Path,
    title: str,
    parent: str | None = None,
    description: str | None = None,
) -> str:
    """`tests/e2e/conftest.py::_add_card`, plus an optional `--description`.

    No `blocked_by`: this module's stories are independent on purpose. The
    subtask's full instruction lives in its description, so the title stays
    short.
    """
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    if description is not None:
        argv += ["--description", description]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]["id"]


def _seed_toy_repo(root: Path) -> None:
    """Commit a green one-module toy suite to the current branch (`main`).

    `calc.py` holding the marker line, a passing baseline test, and the verify
    run's byproducts appended to the existing `.gitignore`, all in one commit.
    """
    (root / "calc.py").write_text(TOY_CALC_SOURCE, encoding="utf-8")
    (root / "test_calc.py").write_text(TOY_CALC_TEST_SOURCE, encoding="utf-8")
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    gitignore.write_text(
        existing + "".join(f"{entry}\n" for entry in TOY_GITIGNORE_ENTRIES),
        encoding="utf-8",
    )
    _git(root, "add", "calc.py", "test_calc.py", ".gitignore")
    _git(root, "commit", "-m", "toy calc baseline with the operations marker")


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
def toy_conflict_milestone(project) -> dict[str, Any]:
    """A green toy suite on `main`, then milestone M with two conflicting stories.

    Story A (created first): a1 replaces the marker line with `add`. Story B
    (created second): b1 replaces the same line with `sub`. No `brd block`
    anywhere, so both stories land in level 0 and run side by side, and A is
    merged first at Integrate (census order, I1), which makes B the conflicting
    tip. Branch names come from `dag` and `integration`, never retyped here.
    `main_sha` is recorded before the run.
    """
    _seed_toy_repo(project)
    milestone = _add_card(
        project, "Milestone: two toy stories that conflict at Integrate, real claude"
    )
    story_a = _add_card(project, "Story A: add in calc.py", milestone)
    story_b = _add_card(project, "Story B: sub in calc.py", milestone)
    a1 = _add_card(
        project, "a1: add add(a, b) to calc.py", story_a, description=A1_DESCRIPTION
    )
    b1 = _add_card(
        project, "b1: add sub(a, b) to calc.py", story_b, description=B1_DESCRIPTION
    )
    subtasks = {"a1": a1, "b1": b1}
    branches = {
        card_id: dag.task_branch(BRANCH_PREFIX, board.show(card_id, repo_dir=project))
        for card_id in subtasks.values()
    }
    return {
        "milestone": milestone,
        "stories": {"A": story_a, "B": story_b},
        "subtasks": subtasks,
        "branches": branches,
        "integrate_branch": integration.integration_branch(BRANCH_PREFIX),
        "main_sha": _git(project, "rev-parse", "main").strip(),
    }


@pytest.fixture(scope="module")
def completed_run(real_claude, project, toy_conflict_milestone) -> dict[str, Any]:
    """One real, paid `orchestrate.run_milestone` at `max_concurrent=2` -- no
    `runner_factory`, no `driver`, no fake on `PATH`.

    Overrides the conftest fixture of the same name. `fake_claude_bin` is
    deliberately absent: `cli.drive_subtask` and `cli.default_runner_factory`
    resolve `claude` on `PATH`, and the point of this module is that what
    they find there -- the story agents AND the Integrate resolver -- is real.
    `real_claude` comes first so a missing `claude` skips before the toy
    milestone is built.
    """
    return orchestrate.run_milestone(
        toy_conflict_milestone["milestone"],
        repo_dir=project,
        base_branch="main",
        branch_prefix=BRANCH_PREFIX,
        commands=list(VERIFY_COMMANDS),
        max_concurrent=2,
    )


def test_the_real_claude_resolves_a_real_merge_conflict_at_integrate(
    real_claude, project, toy_conflict_milestone, completed_run
):
    """The whole deliverable: two real stories that rewrite the same line are
    folded into `e2e-real-i-integrate`, a real resolver finishes the conflicting
    merge, and git finds both functions, no markers, no merge in progress, a
    clean tree and a green suite -- with `main` untouched and nothing pushed."""
    # 1. Done. `.get`, not `[...]`: an escalation payload has no `done`.
    assert completed_run.get("done") is True, (
        completed_run.get("phase"),
        completed_run.get("story"),
        completed_run.get("subtask"),
        completed_run.get("failed_phase"),
        completed_run.get("files"),
        completed_run.get("detail"),
        completed_run.get("warnings"),
    )

    stories = toy_conflict_milestone["stories"]
    story_a, story_b = stories["A"], stories["B"]
    subtasks = toy_conflict_milestone["subtasks"]
    branches = toy_conflict_milestone["branches"]
    integrate_branch = toy_conflict_milestone["integrate_branch"]
    worktree = cli.worktree_for(project, integrate_branch)

    # 2. The integration branch exists, judged by git, and so does its worktree.
    probe = subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/heads/{integrate_branch}",
        ],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, (integrate_branch, probe.stderr)
    assert worktree.is_dir(), worktree
    assert worktree.is_relative_to(
        (project / ".claude" / "worktrees").resolve()
    ), worktree

    # 3. The payload cross-check: B merged second, so B is the one resolved.
    assert completed_run["integrated"] == {
        "branch": integrate_branch,
        "worktree": str(worktree),
        "merged": [story_a, story_b],
        "resolved": [story_b],
    }, completed_run["integrated"]

    # 4. The premise, judged by git: both story tips are in the integration branch.
    for key in ("a1", "b1"):
        tip = branches[subtasks[key]]
        assert _is_ancestor(project, tip, integrate_branch) == 0, (key, tip)

    # 5. Both functions survive at top level, and both stories' tests are there.
    calc_source = _git(project, "show", f"{integrate_branch}:calc.py")
    defined = _top_level_functions(calc_source)
    assert {"add", "sub"} <= defined, (sorted(defined), calc_source)
    for name in ("test_add.py", "test_sub.py"):
        present = subprocess.run(
            ["git", "-C", str(project), "cat-file", "-e", f"{integrate_branch}:{name}"],
            capture_output=True,
            text=True,
        )
        assert present.returncode == 0, (name, present.stderr)

    # 6. No conflict markers anywhere on the branch: `git grep` exits 1 on no match.
    markers = subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "grep",
            "-n",
            "-E",
            CONFLICT_MARKER_PATTERN,
            integrate_branch,
        ],
        capture_output=True,
        text=True,
    )
    assert markers.returncode == 1, (markers.returncode, markers.stdout, markers.stderr)

    # 7. No merge left in progress in the integration worktree.
    merge_head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    assert merge_head.returncode != 0, merge_head.stdout

    # 8. A clean tree, checked before this test's own suite run below.
    assert _git(worktree, "status", "--porcelain") == ""

    # 9. The toy suite passes in the integration worktree.
    for command in VERIFY_COMMANDS:
        suite = subprocess.run(
            shlex.split(command), cwd=worktree, capture_output=True, text=True
        )
        assert suite.returncode == 0, (
            command,
            suite.stdout[-2000:],
            suite.stderr[-2000:],
        )

    # 10. The resolver's attempt is on record under the synthetic story.
    conn = store.open_db(project)
    try:
        run = store.load_run(conn, completed_run["run_id"])
    finally:
        conn.close()
    assert run is not None, completed_run["run_id"]
    integrate_stories = [story for story in run.stories if story.card_id == "integrate"]
    assert len(integrate_stories) == 1, [story.card_id for story in run.stories]
    (integrate_story,) = integrate_stories
    assert integrate_story.title == "Integrate", integrate_story.title
    assert len(integrate_story.subtasks) == 1, integrate_story.subtasks
    (resolver_row,) = integrate_story.subtasks
    assert resolver_row.card_id == story_b, (resolver_row.card_id, story_b)
    assert [phase.name for phase in resolver_row.phases] == ["resolve", "verify"], [
        (phase.name, phase.status) for phase in resolver_row.phases
    ]
    assert {phase.status for phase in resolver_row.phases} == {"done"}, [
        (phase.name, phase.status) for phase in resolver_row.phases
    ]

    # 11. The base branch is untouched (I5).
    assert (
        _git(project, "rev-parse", "main").strip() == toy_conflict_milestone["main_sha"]
    )

    # 12. Nothing was pushed (I5, card rule 4).
    assert _git(project, "for-each-ref", "refs/remotes").strip() == ""
