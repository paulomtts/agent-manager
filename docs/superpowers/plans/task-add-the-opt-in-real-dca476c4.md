<!-- task-pipeline: validated -->
# Add the opt-in real-harness conflict test (card dca476c4)

Parent story f96f2b53 "Prove it against a real harness, and document it", under milestone db5b5a3b (milestone 5, Integrate). This card narrows Integrate addendum I7 and acceptance 7 (`docs/superpowers/specs/2026-09-25-integrate-design.md`) to one test module. The contract it checks is I1 to I6 of that addendum.

## Scope

The card delivers one new module, `tests/e2e/test_real_harness_integrate.py`, and nothing else. There are no source changes and no changes to `tests/e2e/conftest.py`, `pyproject.toml`, `README.md` or any spec. Documentation belongs to sibling card 4c78e3ea "Document Integrate", which is blocked on this card.

The module is the opt-in proof that Integrate resolves a real merge conflict with a real agent. A toy milestone has two independent stories that edit the same line of `calc.py`. The milestone runs through the real `claude` CLI with two lanes, and the test judges the integrated result with git.

## Structure (modelled on `tests/e2e/test_real_harness_parallel.py`)

- Module docstring. It cites I7 and acceptance 7 of the addendum, and the test tier (main spec section 14), in the style of the sibling real-harness modules. It must say:
  - The pipeline cannot run this test. It is excluded by default, and every run spends real money.
  - The real run is a human step afterwards: `uv run pytest -m e2e`.
  - Running the bare path still deselects the test through `addopts` and exits 5, so `-m e2e` has to be passed along with the path.
  - The marker is applied only in this module, never in conftest. Marking it from conftest would take `test_production_wiring.py` out of the default suite, which that file's `test_this_module_runs_in_the_default_suite_unmarked` forbids.
  - The helpers are re-declared rather than imported, because `--import-mode=importlib` puts nothing on `sys.path`.
- Module-level constants:
  - `pytestmark = pytest.mark.e2e`.
  - `BRANCH_PREFIX = "e2e-real-i"`. This prefix is new: `m1`, `m3`, `e2e-real`, `e2e-real-m` and `e2e-real-p` are already taken. The integration branch is `integration.integration_branch(BRANCH_PREFIX)`, which is `e2e-real-i-integrate`.
  - `VERIFY_COMMANDS = (f"{shlex.quote(sys.executable)} -m pytest -q",)`.
- Re-declared helpers, copied from the template: `_git`, `_is_ancestor`, `_add_card(root, title, parent, description)` (no `blocked_by`), `_top_level_functions` (uses `ast`), and `_seed_toy_repo`.
- Fixtures:
  - A module-scoped `real_claude` fixture, copied verbatim from the template. When `shutil.which("claude")` is None it calls `pytest.skip("the real `claude` CLI is not on PATH; ...")`.
  - A module-scoped toy-milestone fixture.
  - A module-scoped `completed_run` override that depends on `real_claude` and the toy milestone, and not on `fake_claude_bin`, so only the real claude is on PATH. It calls `orchestrate.run_milestone(milestone, repo_dir=project, base_branch="main", branch_prefix=BRANCH_PREFIX, commands=list(VERIFY_COMMANDS), max_concurrent=2)`. It passes no `runner_factory` and no `driver`. This is the same path `am run --milestone` takes (cli.py:1085-1103).
- Conftest fixtures it reuses: `toolchain`, `project` and `module_monkeypatch`.

## Toy repo and milestone

- `_seed_toy_repo` commits these files to `main`:
  - `calc.py`: a module docstring plus a line that is exactly `# OPERATIONS GO HERE`.
  - `test_calc.py`: a baseline test that passes. It only imports `calc` and asserts its docstring. Without it, pytest exits 5 and verify is red before any agent runs.
  - `.gitignore`, with the entries `__pycache__/` and `.pytest_cache/`.
- Milestone M has two independent stories and no `brd block`. Story A is created first and has subtask a1. Story B is created second and has subtask b1.
  - a1's description: replace the `# OPERATIONS GO HERE` line in `calc.py` with `def add(a, b): return a + b`, and add `test_add.py` asserting `calc.add(2, 3) == 5`.
  - b1's description: the same, with `sub(a, b)` returning `a - b`, and `test_sub.py` asserting `calc.sub(5, 3) == 2`.
  - Both descriptions also say to keep the existing tests passing, not to touch the other story's test file, and that the suite runs with `python -m pytest -q` from the repository root.
  - Both stories replace the same marker line, so their tips conflict when merged.
- The fixture returns:
  - the milestone, story and subtask ids;
  - each story's tip branch;
  - `integrate_branch`;
  - `main_sha`, which is `git rev-parse main` recorded before the run.

## Observable behavior asserted (one test function)

The test is named something like `test_the_real_claude_resolves_a_real_merge_conflict_at_integrate`. Git judges every fact that git can measure (card rule 3). The payload and the store are only cross-checks and the record of the resolver.

1. The run is done: `completed_run.get("done") is True`. It uses `.get` because an escalation payload has no `done`. The failure message includes the escalation fields (`phase`, `story`, `files`, `detail`).
2. The integration branch exists: `git rev-parse --verify refs/heads/e2e-real-i-integrate` succeeds. Its worktree is `cli.worktree_for(project, integrate_branch)`, which is a directory under `.claude/worktrees/`.
3. The payload: `completed_run["integrated"] == {"branch": integrate_branch, "worktree": <that worktree, as the payload serialises it>, "merged": [A, B], "resolved": [B]}`. B is the conflicting story because it is merged second (census order, I1).
4. The premise holds, judged by git: both story tips are ancestors of the integration branch.
5. Both functions are present. `git show <integrate_branch>:calc.py` defines both `add` and `sub` at top level, checked with `ast`. `test_add.py` and `test_sub.py` both exist on the branch.
6. No file contains conflict markers. `git grep -n -E '^(<<<<<<<|=======|>>>>>>>)( |$)' <integrate_branch>` finds nothing (exit 1).
7. No merge is in progress: `git -C <worktree> rev-parse -q --verify MERGE_HEAD` fails.
8. The worktree is clean: `git -C <worktree> status --porcelain` is empty.
9. The toy suite passes. Each command in `VERIFY_COMMANDS`, run in the integration worktree, exits 0. The tails of stdout and stderr are attached to the failure message.
10. The resolver's attempt is recorded. The test opens the run with `store.open_db` and `store.load_run(conn, completed_run["run_id"])`, and closes the connection as the template does. Exactly one story has `card_id == "integrate"` (`integration.INTEGRATE_STORY_ID`) and title `"Integrate"`. It has a single subtask whose `card_id` is B. That subtask's phase names are `["resolve", "verify"]`, and both phases have status `done`.
11. The base branch is untouched: `git rev-parse main` still equals `main_sha` (I5).
12. Nothing was pushed: `git for-each-ref refs/remotes` in the project is empty (I5, card rule 4).

## Error paths

- `claude` is not on PATH: every test in the module skips with the PATH message. `real_claude` runs before the toy milestone is seeded, so nothing is built and nothing is spent.
- `git` or `brd` is not on PATH: the conftest `toolchain` fixture skips, as it already does.
- The run escalates, for example at Integrate: assertion 1 fails and reports the escalation fields. The integration branch and worktree stay as they are for a human to inspect (I5). The test does no cleanup beyond what the `project` fixture already does.

## Out of scope

- `--no-integrate` and a milestone-aware `am resume`.
- Watch, retry and cancel.
- Cost capture.
- The reviewer's Plan-Hash brief.
- Marking slow tests, and per-story readiness.
- README and spec status notes, which belong to card 4c78e3ea.
- Fake-claude Integrate tests, which are already in `tests/e2e/test_integrate.py`.

## Tests and tier

The placement rule is main spec section 14 (Testing, line 495). A test that drives a real harness belongs to the end-to-end tier: one slow, opt-in test, marked and excluded from the default suite. It is not a unit test or a step test, and it does not use the fake claude.

| Test | Tier |
|---|---|
| `test_the_real_claude_resolves_a_real_merge_conflict_at_integrate` (assertions 1-12) | End-to-end, opt-in real harness: `tests/e2e/`, module-level `pytest.mark.e2e` |

## Card-level verification (without a real run)

The pipeline cannot run the real test, so the card is verified in three ways:

- Collection: `uv run pytest --collect-only -q -m e2e` lists the new test.
- Deselection: `uv run pytest` does not run the new test, and the whole default suite stays green, including `tests/e2e` (card rule 2). `test_production_wiring.py` is still in the default suite.
- Skip: when `claude` is not on PATH, `uv run pytest -m e2e tests/e2e/test_real_harness_integrate.py` reports a skip whose reason contains "the real `claude` CLI is not on PATH".

The real run is a human step afterwards: `uv run pytest -m e2e`.

---

# Opt-in Real-Harness Integrate Conflict Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tests/e2e/test_real_harness_integrate.py`, one opt-in, `e2e`-marked test that drives a two-story toy milestone whose stories conflict on the same line of `calc.py` through the real `claude` CLI, and judges with git that Integrate's real resolver finished the merge cleanly.

**Architecture:** A single new test module, modelled line for line on `tests/e2e/test_real_harness_parallel.py`. It reuses the conftest fixtures `toolchain`, `project` and `module_monkeypatch`, re-declares the toy helpers (because `--import-mode=importlib` puts nothing on `sys.path`), overrides `completed_run` without `fake_claude_bin`, and calls `orchestrate.run_milestone(..., max_concurrent=2)` with no `runner_factory` and no `driver`. No source, conftest, `pyproject.toml`, README or spec file changes.

**Tech Stack:** Python 3, pytest (`--import-mode=importlib`, `-m "not e2e"` in `addopts`), git, brd, the real `claude` CLI (opt-in only), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-add-the-opt-in-real-dca476c4/docs/superpowers/specs/task-add-the-opt-in-real-dca476c4-design.md` (prepended verbatim above). Contract: `docs/superpowers/specs/2026-09-25-integrate-design.md` I1-I7 and acceptance 7.

**Provenance note:** the computed task text that launched this plan carried two upstream summaries (the spec author's summary and the exploration findings) that were both truncated mid-sentence at their character caps. The truncation is itself evidence that those upstream stages over-ran their briefs. This plan does not guess the missing text: it was written from the spec read from disk and from the code in the worktree (`src/agent_manager/integration.py`, `src/agent_manager/orchestrate.py`, `src/agent_manager/cli.py`, `tests/e2e/conftest.py`, `tests/e2e/test_real_harness_parallel.py`, `tests/e2e/test_integrate.py`).

## Global Constraints

- Deliverable is exactly one new file: `tests/e2e/test_real_harness_integrate.py`. No changes to `src/`, `tests/e2e/conftest.py`, `pyproject.toml`, `README.md` or any spec.
- `pytestmark = pytest.mark.e2e` at module level, and only in this module. Never mark from conftest.
- `BRANCH_PREFIX = "e2e-real-i"`; the integration branch is `integration.integration_branch(BRANCH_PREFIX)` == `"e2e-real-i-integrate"`.
- `VERIFY_COMMANDS = (f"{shlex.quote(sys.executable)} -m pytest -q",)`.
- `completed_run` must not depend on `fake_claude_bin`; `orchestrate.run_milestone` gets no `runner_factory` and no `driver`, and `max_concurrent=2`.
- The `real_claude` skip message starts "the real `claude` CLI is not on PATH".
- Git judges everything git can measure (card rule 3); the payload and the store are cross-checks.
- The base branch `main` is untouched and nothing is pushed (I5, card rule 4).
- The whole default suite (`uv run pytest`) stays green, including `tests/e2e` (card rule 2).
- NEVER run `uv run pytest -m e2e` (with or without a path) while `claude` is on PATH during this card: it performs a real paid run and also selects the other real-harness modules. The real run is a human step afterwards.

## Review Focus

1. A bare-path invocation (`uv run pytest tests/e2e/test_real_harness_integrate.py`) must deselect the test and exit 5, not run it and spend money. Pinned by Task 1 Step 5.
2. A machine without `claude` on PATH must skip the module with the PATH message before any repo is seeded or any card is created. Pinned by Task 1 Step 6 (runs with a PATH that holds only `uv` and `git`, so `real_claude` is the first fixture to act and skips).
3. The new marker must not leak onto the free fake-claude modules (`test_production_wiring.py`, `test_integrate.py`), which must still run unmarked on every `uv run pytest`. Pinned by Task 1 Step 7 (the full default suite includes their `test_this_module_runs_in_the_default_suite_unmarked` checks).
4. A real run whose agents do not actually conflict (for example an agent that appends instead of replacing the marker line) must fail loudly rather than pass vacuously. Pinned in the test body by assertion 3 (`resolved == [B]`) and assertion 10 (exactly one "integrate" story with a `resolve` phase).
5. The verify run's byproducts (`__pycache__/`, `.pytest_cache/`) must not dirty the integration worktree and trip the clean-tree check. Pinned by `_seed_toy_repo` writing both entries to `.gitignore`, and by assertion 8 running before assertion 9's suite run.

---

### Task 1: The opt-in real-harness Integrate conflict test module

**Files:**
- Create: `tests/e2e/test_real_harness_integrate.py`
- Test: `tests/e2e/test_real_harness_integrate.py` (end-to-end tier, opt-in real harness, per main spec section 14; sits beside `tests/e2e/test_real_harness.py`, `tests/e2e/test_real_harness_milestone.py`, `tests/e2e/test_real_harness_parallel.py`)

**Interfaces:**
- Consumes (existing code, verified in this worktree):
  - `tests/e2e/conftest.py`: fixtures `toolchain` (module), `project(tmp_path_factory, module_monkeypatch, toolchain) -> Path` (module; a git repo on `main` with a committed brd board and a `.gitignore` from `brd init`), `module_monkeypatch` (module).
  - `agent_manager.orchestrate.run_milestone(milestone: str, *, repo_dir: Path, base_branch: str, branch_prefix: str, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory=None, driver=None, clock=..., max_concurrent: int = 1) -> dict[str, Any]`. On success returns `{"done": True, "run_id", "levels", "completed", "tips", "warnings", "integrated": {"branch": str, "worktree": str, "merged": list[str], "resolved": list[str]}}`. A level escalation returns `{"escalated", "run_id", "level", "story", "subtask", "failed_phase", "detail", "warnings", ...}`; an Integrate escalation returns `{"escalated": True, "phase": "integrate", "story", "files", "detail", ...}`.
  - `agent_manager.integration.integration_branch(branch_prefix: str) -> str` returns `f"{branch_prefix}-integrate"`.
  - `agent_manager.cli.worktree_for(repo_dir: Path, branch: str) -> Path` returns `<repo_dir resolved>/.claude/worktrees/<branch segments>`; the payload's `worktree` is `str()` of exactly this.
  - `agent_manager.dag.task_branch(prefix: str, card) -> str`; `agent_manager.board.show(card_id, repo_dir=...)`.
  - `agent_manager.store.open_db(root) -> connection`; `agent_manager.store.load_run(conn, run_id) -> models.Run | None`; `models.Run.stories[*].card_id/.title/.status/.subtasks[*].card_id/.phases[*].name/.status`.
- Produces: nothing other code imports. The test id is `tests/e2e/test_real_harness_integrate.py::test_the_real_claude_resolves_a_real_merge_conflict_at_integrate`.

TDD note for this tier: the real test cannot run in the pipeline (it is deselected by default and spends real money). The RED/GREEN cycle is therefore on the three pipeline-observable behaviors the spec names under "Card-level verification": collection under `-m e2e`, deselection by default, and the skip without `claude`. The real run is a human step afterwards.

- [ ] **Step 1: Confirm the module does not exist yet (RED)**

Run from `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-add-the-opt-in-real-dca476c4`:

```bash
uv run pytest --collect-only -q -m e2e tests/e2e/test_real_harness_integrate.py
```

Expected: FAIL with `ERROR: file or directory not found: tests/e2e/test_real_harness_integrate.py` and exit code 4. `--collect-only` never runs a test, so this is safe even with `claude` on PATH.

- [ ] **Step 2: Write the module**

Create `tests/e2e/test_real_harness_integrate.py` with exactly this content:

```python
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
```

Notes for the implementer:
- `cli.worktree_for` already resolves its `repo_dir`, and `orchestrate.integrated_payload` serialises the worktree as `str(outcome.worktree)` built from the same call, so `str(worktree)` is the exact payload value.
- `is_relative_to` compares against the resolved `.claude/worktrees` directory because `worktree` is itself resolved.
- The fixture argument order of the test (`real_claude` first) is load-bearing: it makes `real_claude` skip before `project` seeds anything or `toolchain` runs.

- [ ] **Step 3: Confirm the test is collected under `-m e2e` (GREEN for collection)**

```bash
uv run pytest --collect-only -q -m e2e tests/e2e/test_real_harness_integrate.py
```

Expected: PASS, printing `tests/e2e/test_real_harness_integrate.py::test_the_real_claude_resolves_a_real_merge_conflict_at_integrate` and `1 test collected`. `--collect-only` runs no fixture, so nothing is spent.

- [ ] **Step 4: Confirm the whole `-m e2e` collection lists it alongside the siblings**

```bash
uv run pytest --collect-only -q -m e2e
```

Expected: the list includes `tests/e2e/test_real_harness_integrate.py::test_the_real_claude_resolves_a_real_merge_conflict_at_integrate` beside the existing `test_real_harness*.py` tests, with no collection errors.

- [ ] **Step 5: Confirm a bare-path invocation deselects it and exits 5**

```bash
uv run pytest tests/e2e/test_real_harness_integrate.py; echo "exit=$?"
```

Expected: `1 deselected` and `exit=5`. If it reports `1 passed`, `1 failed` or anything other than deselected, stop: the marker is not applied and the test may have spent money.

- [ ] **Step 6: Confirm it skips with the PATH message when `claude` is absent**

This step must never reach a real run, so it builds a PATH that holds only `uv` and `git` (no `claude`). `real_claude` is the test's first fixture, so it skips before `toolchain` or `project` act.

```bash
SCRATCH_BIN="$(mktemp -d)"
ln -s "$(command -v uv)" "$SCRATCH_BIN/uv"
ln -s "$(command -v git)" "$SCRATCH_BIN/git"
test ! -e "$SCRATCH_BIN/claude" && PATH="$SCRATCH_BIN" uv run pytest -m e2e -rs tests/e2e/test_real_harness_integrate.py; echo "exit=$?"
```

Expected: `1 skipped`, and the `-rs` summary line contains `the real \`claude\` CLI is not on PATH`. Exit code 0. If the output shows `1 passed` or `1 failed`, a `claude` was reachable: stop and report it.

- [ ] **Step 7: Run the full default suite**

```bash
uv run pytest
```

Expected: PASS, all green, with the new test counted among the deselected ones. This run still includes `tests/e2e/test_production_wiring.py` and `tests/e2e/test_integrate.py`, whose `test_this_module_runs_in_the_default_suite_unmarked` checks prove the new marker did not leak onto them.

- [ ] **Step 8: Confirm the diff is exactly one new file**

```bash
git status --porcelain
```

Expected: only `?? tests/e2e/test_real_harness_integrate.py` plus the untracked spec and plan documents under `docs/superpowers/`. No modified tracked file (no ` M` lines).

- [ ] **Step 9: Commit**

```bash
git add tests/e2e/test_real_harness_integrate.py docs/superpowers/specs/task-add-the-opt-in-real-dca476c4-design.md docs/superpowers/plans/task-add-the-opt-in-real-dca476c4.md
git commit -m "Add the opt-in real-harness Integrate conflict test

The real run is a human step afterwards: uv run pytest -m e2e.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

After this task, the card is verified without a real run. The real, paid run is a human step afterwards: `uv run pytest -m e2e` (or `uv run pytest -m e2e tests/e2e/test_real_harness_integrate.py` for this module only).
