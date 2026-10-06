"""End-to-end tier: the opt-in real-harness PARALLEL test.

Parallel-stories addendum P7 and acceptance 7
(`docs/superpowers/specs/2026-09-24-parallel-stories-design.md`), placed per
main spec section 14 (Testing): a real toy milestone with two INDEPENDENT
stories driven through `orchestrate.run_milestone(..., max_concurrent=2)`
against the real `claude -p`, with no `runner_factory`, no `driver`, and no
fake `claude` on `PATH`.

The pipeline cannot run this test. It is excluded from the default suite
(`pyproject.toml:38`: `addopts` carries `-m "not e2e"`; the `e2e` marker is
registered at `pyproject.toml:30`), and every run spends real money. The real
run is a human step afterwards: `uv run pytest -m e2e`. A bare path invocation
such as `uv run pytest tests/e2e/test_real_harness_parallel.py` is still
deselected by `addopts` and exits 5 -- pass `-m e2e` alongside the path.

The marker is applied HERE and only here. Marking it from
`tests/e2e/conftest.py` would drag the free, fake-claude
`test_production_wiring.py` out of the default suite, which its own
`test_this_module_runs_in_the_default_suite_unmarked` forbids.

Reused from `tests/e2e/conftest.py`: `toolchain`, `project` and
`module_monkeypatch`. The toy-repo helpers are re-declared from
`test_real_harness_milestone.py` rather than imported, because
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
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from agent_manager import board, cli, dag, models, orchestrate, store
from agent_manager.store import db as store_db

pytestmark = pytest.mark.e2e

BRANCH_PREFIX = "e2e-real-p"
"""Distinct from `m1`, `m3`, `e2e-real` and `e2e-real-m`, so this run can
never land on a branch or worktree path another e2e module uses."""

VERIFY_COMMANDS = (f"{shlex.quote(sys.executable)} -m pytest -q",)
"""A real, green command for the toy suite. `verify.run_suite` splits it with
`shlex.split` and runs it without a shell, so the interpreter path is quoted.
The test's own interpreter already has pytest."""

PLAN_HASH_TRAILER = "Plan-Hash:"

TOY_GITIGNORE_ENTRIES = ("__pycache__/", ".pytest_cache/")
"""What the verify run leaves behind. Ignored, so it never dirties a worktree
and trips `review_gate`."""

TOY_CALC_SOURCE = '"""A toy calculator the e2e agents extend."""\n'

TOY_CALC_TEST_SOURCE = (
    "import calc\n"
    "\n"
    "\n"
    "def test_calc_module_imports():\n"
    "    assert calc.__doc__\n"
)

TOY_HELLO_SOURCE = '"""A toy greeter the e2e agents extend."""\n'

TOY_HELLO_TEST_SOURCE = (
    "import hello\n"
    "\n"
    "\n"
    "def test_hello_module_imports():\n"
    "    assert hello.__doc__\n"
)
"""Baseline passing tests for both toy modules: with no test at all, pytest
exits 5 and the verify command would be red before any agent runs."""

A1_DESCRIPTION = (
    "In `calc.py` at the repository root, add a function `add(a, b)` that "
    "returns `a + b`. In `test_calc.py`, add a test `test_add` that checks "
    "`calc.add(2, 3) == 5`. Keep the existing tests passing. Do not touch "
    "`hello.py` or `test_hello.py`. The suite runs with `python -m pytest -q` "
    "from the repository root."
)

B1_DESCRIPTION = (
    "In `hello.py` at the repository root, add a function `greet(name)` that "
    "returns `f\"Hello, {name}!\"`. In `test_hello.py`, add a test "
    "`test_greet` that checks `hello.greet(\"Ada\") == \"Hello, Ada!\"`. Keep "
    "the existing tests passing. Do not touch `calc.py` or `test_calc.py`. "
    "The suite runs with `python -m pytest -q` from the repository root."
)


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
    """Commit a green two-module toy suite to the current branch (`main`).

    `calc.py`, `hello.py`, a passing test for each, and the verify run's
    byproducts appended to the existing `.gitignore`, all in one commit.
    """
    (root / "calc.py").write_text(TOY_CALC_SOURCE, encoding="utf-8")
    (root / "test_calc.py").write_text(TOY_CALC_TEST_SOURCE, encoding="utf-8")
    (root / "hello.py").write_text(TOY_HELLO_SOURCE, encoding="utf-8")
    (root / "test_hello.py").write_text(TOY_HELLO_TEST_SOURCE, encoding="utf-8")
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    gitignore.write_text(
        existing + "".join(f"{entry}\n" for entry in TOY_GITIGNORE_ENTRIES),
        encoding="utf-8",
    )
    _git(
        root,
        "add",
        "calc.py",
        "test_calc.py",
        "hello.py",
        "test_hello.py",
        ".gitignore",
    )
    _git(root, "commit", "-m", "toy calc and hello baseline")


def _top_level_functions(source: str) -> set[str]:
    """The names of every module-level `def` in `source`."""
    return {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _implement_span(
    run: models.Run, card_id: str
) -> tuple[datetime | None, datetime | None]:
    """The `implement` phase's `started_at`/`ended_at` for one subtask.

    The timestamps live on `PhaseRun`, not on `Attempt`. The last phase row
    named `implement` wins, should there ever be more than one.
    """
    phases = [
        phase
        for story in run.stories
        for subtask in story.subtasks
        if subtask.card_id == card_id
        for phase in subtask.phases
        if phase.name == "implement"
    ]
    assert phases, (card_id, "no implement phase recorded")
    return phases[-1].started_at, phases[-1].ended_at


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
def toy_parallel_milestone(project) -> dict[str, Any]:
    """A green toy suite on `main`, then milestone M with two independent stories.

    Story A: a1 (`add` in `calc.py`). Story B: b1 (`greet` in `hello.py`).
    No `brd block` anywhere, so both stories land in level 0 and run side by
    side. Branch names come from `dag`, never retyped here. `main_sha` is
    recorded before the run.
    """
    _seed_toy_repo(project)
    milestone = _add_card(project, "Milestone: two toy stories in parallel, real claude")
    story_a = _add_card(project, "Story A: add in calc.py", milestone)
    story_b = _add_card(project, "Story B: greet in hello.py", milestone)
    a1 = _add_card(
        project, "a1: add add(a, b) to calc.py", story_a, description=A1_DESCRIPTION
    )
    b1 = _add_card(
        project, "b1: add greet(name) to hello.py", story_b, description=B1_DESCRIPTION
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
        "main_sha": _git(project, "rev-parse", "main").strip(),
    }


@pytest.fixture(scope="module")
def completed_run(real_claude, project, toy_parallel_milestone) -> dict[str, Any]:
    """One real, paid `orchestrate.run_milestone` at `max_concurrent=2` -- no
    `runner_factory`, no `driver`, no fake on `PATH`.

    Overrides the conftest fixture of the same name. `fake_claude_bin` is
    deliberately absent: `cli.drive_subtask` and `cli.default_runner_factory`
    resolve `claude` on `PATH`, and the point of this module is that what
    they find there is real. `real_claude` comes first so a missing `claude`
    skips before the toy milestone is built.
    """
    return orchestrate.run_milestone(
        toy_parallel_milestone["milestone"],
        repo_dir=project,
        base_branch="main",
        branch_prefix=BRANCH_PREFIX,
        commands=list(VERIFY_COMMANDS),
        max_concurrent=2,
    )


def test_the_real_claude_drives_two_independent_stories_in_parallel(
    real_claude, project, toy_parallel_milestone, completed_run
):
    """The whole deliverable: a real `claude -p` takes two independent stories
    to `done` side by side, each on its own branch cut from `main`, each
    subtask's commits carrying one Plan-Hash, the toy suite green on both
    tips, `main` untouched, and the two implement phases overlapping.

    justification: two real `claude -p` sessions, each with real tool
    permissions, working sibling worktrees at the same time with nothing
    forcing them to overlap. The `e2e_fake` twin
    (`test_parallel_milestone.py::test_two_lanes_overlap_in_implement_and_the_milestone_finishes`)
    already proves overlap, but only through a rendezvous that holds scripted
    fake processes inside implement until both arrive; it cannot observe
    whether real, variable-length model sessions still overlap unforced (the
    implement-span assertion) and each still lands its own working change
    (`add` in `calc.py`, `greet` in `hello.py`, a green suite on both tips)
    on its own independent branch."""
    # `.get`, not `[...]`: an escalation payload has no `completed`.
    assert completed_run.get("done") is True, (
        completed_run.get("story"),
        completed_run.get("subtask"),
        completed_run.get("failed_phase"),
        completed_run.get("detail"),
        completed_run.get("warnings"),
    )

    subtasks = toy_parallel_milestone["subtasks"]
    a1, b1 = subtasks["a1"], subtasks["b1"]
    stories = toy_parallel_milestone["stories"]
    branches = toy_parallel_milestone["branches"]

    # Under concurrency the completion order is not fixed: compare as a set.
    completed = completed_run["completed"]
    assert len(completed) == 2 and set(completed) == {a1, b1}, completed

    # `mark_done` is best effort, so the payload alone is not proof. The
    # stories and the milestone reach `done` only through rollup.
    for card_id in (a1, b1, stories["A"], stories["B"], toy_parallel_milestone["milestone"]):
        status = board.show(card_id, repo_dir=project).status
        assert status == "done", (card_id, status, completed_run["warnings"])

    # Both branches were cut from `main`, independently of each other.
    branch_a, branch_b = branches[a1], branches[b1]
    for branch in (branch_a, branch_b):
        assert _is_ancestor(project, "main", branch) == 0, ("main", branch)
    assert _is_ancestor(project, branch_a, branch_b) != 0, (branch_a, branch_b)
    assert _is_ancestor(project, branch_b, branch_a) != 0, (branch_b, branch_a)

    # Every commit in each branch's own range carries a Plan-Hash trailer,
    # with exactly one distinct value per subtask.
    for card_id, branch in ((a1, branch_a), (b1, branch_b)):
        revisions = _git(project, "rev-list", f"main..{branch}").split()
        assert revisions, (card_id, f"{branch} carries no commits beyond main")
        hashes: set[str] = set()
        for revision in revisions:
            message = _git(project, "show", "-s", "--format=%B", revision)
            values = _plan_hashes(message)
            assert values, (card_id, revision, message)
            hashes.update(values)
        assert len(hashes) == 1, (card_id, hashes)

    # The toy suite passes on each tip, and each tip really did its work.
    for branch, module, function in (
        (branch_a, "calc.py", "add"),
        (branch_b, "hello.py", "greet"),
    ):
        tip = cli.worktree_for(project, branch)
        assert tip.is_dir(), tip
        for command in VERIFY_COMMANDS:
            suite = subprocess.run(
                shlex.split(command), cwd=tip, capture_output=True, text=True
            )
            assert suite.returncode == 0, (
                branch,
                command,
                suite.stdout[-2000:],
                suite.stderr[-2000:],
            )
        defined = _top_level_functions((tip / module).read_text(encoding="utf-8"))
        assert function in defined, (branch, module, sorted(defined))

    # The base branch is untouched.
    assert _git(project, "rev-parse", "main").strip() == toy_parallel_milestone["main_sha"]

    # The two implement phases overlapped in time: the run really was parallel.
    conn = store_db.open_db(project)
    try:
        run = store.load_run(conn, completed_run["run_id"])
    finally:
        conn.close()
    assert run is not None, completed_run["run_id"]
    a_start, a_end = _implement_span(run, a1)
    b_start, b_end = _implement_span(run, b1)
    spans = {"a1": (a_start, a_end), "b1": (b_start, b_end)}
    assert None not in (a_start, a_end, b_start, b_end), spans
    assert a_start < b_end and b_start < a_end, spans
