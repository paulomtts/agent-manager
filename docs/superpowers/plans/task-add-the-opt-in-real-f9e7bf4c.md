<!-- task-pipeline: validated -->
# Subtask f9e7bf4c: add the opt-in real-harness milestone test

Date: 2026-09-24
Parent: story 5017dd8c "Prove it against a real harness, and document it" (milestone 99e178cb)
Narrows: `2026-09-24-orchestration-design.md` O8 and section 3 acceptance 6. Also main spec section 14 (Testing) and `2026-09-23-real-harness-design.md` R4.

## Scope

Add one new test module, `tests/e2e/test_real_harness_milestone.py`. It drives a real two-story toy milestone through `orchestrate.run_milestone` against the real `claude -p` and asserts the result on the board and in git.

This card changes nothing in `src/`, `tests/e2e/conftest.py`, `pyproject.toml`, `README.md` or any spec. README and the main spec's section 10 status note belong to sibling 7b6ad9bd, which is blocked by this card.

The real, paid run is a human step (O8, acceptance 6). The pipeline cannot exercise it: the module is excluded from the default suite and every run spends real money. The module docstring must say this in those terms and give the command `uv run pytest -m e2e`. It must also note that a bare path invocation is still deselected by `addopts` and exits 5, so `-m e2e` has to be passed alongside a path.

Out of scope: any fake `claude` (this module uses only the real one), parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, and verification discovery.

## The driver this test calls

This test calls `orchestrate.run_milestone` (`src/agent_manager/orchestrate.py:235`) directly, the same way `test_real_harness.py` calls `cli.run_card`. It passes no `runner_factory` and no `driver`, so `cli.drive_subtask` and `cli.default_runner_factory` resolve the real `claude` on `PATH`:

```python
orchestrate.run_milestone(
    milestone_id,
    repo_dir=project,
    base_branch="main",
    branch_prefix=BRANCH_PREFIX,
    commands=list(VERIFY_COMMANDS),
)
```

On success it returns `{"done": True, "run_id", "levels", "completed", "tips", "warnings"}`, where `tips` is a list of `{"story", "tip"}`. On escalation it returns `{"escalated": True, "run_id", "level", "story", "subtask", "failed_phase", "detail", "warnings"}`. There is no `status` key. Branch names come from `dag.task_branch(BRANCH_PREFIX, board.show(id, repo_dir=...))`, the same way the conftest's `milestone_board` derives them. Never retype them by hand.

## Module shape

- `pytestmark = pytest.mark.e2e`, set in this module only. Never set it in the conftest: `test_production_wiring.py` asserts that its own module runs unmarked in the default suite.
- Local re-declarations, because `--import-mode=importlib` makes conftest and sibling names unimportable: `_git`, `_plan_hashes` (the column-0 trailer-value parser, copied from `test_real_harness.py`), `PLAN_HASH_TRAILER = "Plan-Hash:"`, a distinct `BRANCH_PREFIX` (e.g. `"e2e-real-m"`, distinct from `m1`, `m3` and `e2e-real`), and `VERIFY_COMMANDS`.
- `VERIFY_COMMANDS` must be a real, green test command for the toy suite, not `git rev-parse`. `verify.run_suite` splits commands with `shlex.split` and does not use a shell. So use `f"{shlex.quote(sys.executable)} -m pytest -q"`: the test's own interpreter already has pytest.
- A `real_claude` module fixture. When `shutil.which("claude")` is None it calls `pytest.skip` with the exact message from `test_real_harness.py:99-103` ("the real `claude` CLI is not on PATH; the opt-in e2e tier needs it ...").
- Reused conftest fixtures: `toolchain`, `project` (module-scoped git repo on `main`, brd board, `XDG_DATA_HOME` in tmp) and `module_monkeypatch`. The conftest's `cards` and `completed_run` are not used as they stand. `completed_run` is overridden in this module without `fake_claude_bin`. The function-scoped `fresh_project` and `milestone_board` do not fit a module-scoped paid run, so they are not used.

### Fixtures

1. `toy_milestone(project)`, module-scoped:
   - Seed the toy repo on `main` so the verify command is green before any agent runs: a module `calc.py` (for example with a docstring only), a trivial passing `test_calc.py`, and `__pycache__/` plus `.pytest_cache/` appended to the existing `.gitignore`. Commit all of it. Without the ignore entries, the verify run would dirty the worktree and trip `review_gate`. Without a baseline test, pytest exits 5.
   - Build the cards with a module-local `_add_card(root, title, parent=None, blocked_by=())` that follows `conftest.py:63-89` (`brd add`, then `brd block <id> --by <blocker>`). Re-declare it here, because it cannot be imported.
   - Milestone M.
   - Story A: subtask a1 "add `add(a, b)` to `calc.py` with a test", then subtask a2 "add `sub(a, b)` to `calc.py` with a test", blocked_by a1 so the census order is fixed.
   - Story B, blocked_by A: subtask b1 "add a CLI-free `calc(op, a, b)` to `calc.py` that dispatches to `add` and `sub`, with a test".
   - `brd add` has a `--description` flag (confirmed), but the conftest's `_add_card` does not pass it. The local `_add_card` takes an extra optional `description` argument and appends `--description`, so each subtask card carries its full instruction there and the title stays short.
   - Record `main_sha = git rev-parse main` after seeding and before the run.
   - Return the ids, the per-subtask branches, and `main_sha`.
2. `completed_run(real_claude, project, toy_milestone)`, module-scoped: the single `run_milestone` call shown above.

## Observable behavior asserted

One test function, `test_the_real_claude_drives_a_two_story_milestone_to_done`, reading the one module-scoped run:

- The payload has `done` True. On failure the assertion message carries the escalation fields (`story`, `subtask`, `failed_phase`, `detail`, `warnings`).
- `completed == [a1, a2, b1]`.
- `tips` contains `{"story": B, "tip": branch(b1)}`.
- Each of a1, a2, b1, A, B and M reads `done` through `board.show(id, repo_dir=project).status`. The payload alone is not proof, because `mark_done` is best effort. The story and milestone reach `done` only through rollup.
- Stacking, checked with `git merge-base --is-ancestor`:
  - `main` is an ancestor of `branch(a1)`.
  - `branch(a1)` is an ancestor of `branch(a2)`.
  - `branch(a2)` is an ancestor of `branch(b1)`, because story B roots on story A's tip.
- Plan-Hash trailers: for each subtask, `rev-list <predecessor>..<branch>` is non-empty. Every commit in the range has at least one `Plan-Hash:` trailer value, and the whole range has exactly one distinct value. The predecessors are `main`, then a1's branch, then a2's branch.
- The toy suite passes on the last tip. Run `VERIFY_COMMANDS` in `cli.worktree_for(project, branch(b1))` and expect exit 0. As a non-vacuity check, `calc.py` there defines `add`, `sub` and `calc`.
- `git rev-parse main` still equals `main_sha`, so the base branch is untouched.

## Error paths

- `claude` is not on `PATH`: the whole module skips with the PATH message. This happens before any board or git write that belongs to the run.
- `git` or `brd` is missing: the conftest's `toolchain` skips the module.
- The run escalates: the first assertion fails and shows the escalation payload. No retry is attempted.

## Tests and their tier

Per main spec section 14 (lines ~465-492), "end to end" is the slow, opt-in, real-harness tier. It is marked `e2e`, excluded from the default suite, and lives in `tests/e2e/` beside the unmarked fake-claude production-wiring tests.

| Test | Tier |
|---|---|
| `tests/e2e/test_real_harness_milestone.py::test_the_real_claude_drives_a_two_story_milestone_to_done` | End to end, real harness, opt-in. Marked `e2e`, excluded by default, run by a human. |

No other test is added. Any free or fake-claude milestone coverage belongs to the default tier (unmarked), not in this file.

## Verification (pipeline-runnable; the paid run is a human step)

1. `uv run pytest -m e2e --collect-only` lists the new test.
2. The default `uv run pytest` deselects it, and the whole default suite, `tests/e2e` included, stays green.
3. `uv run pytest -m e2e tests/e2e/test_real_harness_milestone.py`, with `claude` absent from `PATH`, reports the test as skipped with the PATH message.

---

# Opt-in Real-Harness Milestone Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tests/e2e/test_real_harness_milestone.py`, one opt-in, `e2e`-marked test that drives a real two-story toy milestone through `orchestrate.run_milestone` against the real `claude` and asserts the board, the git stacking, the Plan-Hash trailers, the toy suite and an untouched `main`.

**Architecture:** One new test module in the end-to-end tier, modelled on `tests/e2e/test_real_harness.py`. It reuses the conftest's `toolchain`, `project` and `module_monkeypatch` fixtures, re-declares its helpers locally (importlib import mode), adds a `real_claude` skip guard, a module-scoped `toy_milestone` fixture that seeds a green toy suite on `main` and builds the cards, and a module-scoped `completed_run` override that calls `orchestrate.run_milestone` with no `runner_factory` and no `driver`. Nothing in `src/`, the conftest, `pyproject.toml`, README or any spec changes.

**Tech Stack:** Python 3.12, pytest 9 (`--import-mode=importlib`, `-m "not e2e"` in addopts), git, the `brd` CLI, the real `claude` CLI (human run only).

**Spec:** `docs/superpowers/specs/task-add-the-opt-in-real-f9e7bf4c-design.md` (prepended verbatim above).

## Global Constraints

- The marker is `pytestmark = pytest.mark.e2e`, in the new module only. Never in `tests/e2e/conftest.py`.
- No fake `claude` anywhere in this module. `completed_run` does not request `fake_claude_bin`.
- `orchestrate.run_milestone(milestone_id, repo_dir=project, base_branch="main", branch_prefix=BRANCH_PREFIX, commands=list(VERIFY_COMMANDS))`, with no `runner_factory` and no `driver`.
- `BRANCH_PREFIX = "e2e-real-m"` (distinct from `m1`, `m3` and `e2e-real`).
- `VERIFY_COMMANDS = (f"{shlex.quote(sys.executable)} -m pytest -q",)`. Commands are split with `shlex.split` and never run in a shell.
- `PLAN_HASH_TRAILER = "Plan-Hash:"`, matched at column 0.
- The skip message is copied exactly from `tests/e2e/test_real_harness.py:99-103`.
- Branch names come from `dag.task_branch(BRANCH_PREFIX, board.show(id, repo_dir=project))`, never typed by hand.
- The success payload keys are `done`, `run_id`, `levels`, `completed`, `tips` (list of `{"story", "tip"}`), `warnings`. The escalation payload has `escalated`, `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail`, `warnings`, and no `status` key.
- The module docstring says the pipeline cannot exercise it (excluded by default, spends real money), that the real run is a human step, gives `uv run pytest -m e2e`, and says a bare path invocation is deselected and exits 5.
- Do not touch `src/`, `tests/e2e/conftest.py`, `pyproject.toml`, `README.md` or any spec.
- The whole default suite (`uv run pytest`), `tests/e2e` included, stays green.

## Review Focus

- `claude` absent from `PATH`: the test must skip with the PATH message, and `real_claude` must resolve before `toy_milestone`, so no toy seeding or card is written. Pinned by Task 1 Step 6 (skip run under a PATH with no `claude`).
- A bare path invocation (`uv run pytest tests/e2e/test_real_harness_milestone.py`) must be deselected, exit 5, and launch nothing. Pinned by Task 1 Step 5.
- The verify command's own byproducts (`.pytest_cache/`, `__pycache__/`) must not dirty the worktree, and the baseline toy suite must be green before any agent runs (pytest exits 5 on zero tests). Pinned by Task 1 Step 7 (seed smoke checks exit 0 and an empty `git status --porcelain`).
- An interpreter path containing spaces must survive `shlex.split` as one argv element. Pinned by Task 1 Step 7 (smoke asserts `shlex.split(VERIFY_COMMANDS[0])[0] == sys.executable`).
- An escalated run must fail with a readable message, not a `KeyError`, because the escalation payload has no `status` or `completed` key. The test reads the first assertion's fields with `.get(...)` and asserts `done` before touching `completed`; Task 1 Step 3's code does this, and Task 1 Step 8 (full suite) proves the marker did not drag `test_production_wiring.py` out of the default suite.

---

### Task 1: The opt-in real-harness milestone test module

**Files:**
- Create: `tests/e2e/test_real_harness_milestone.py`
- Read only (do not modify): `tests/e2e/conftest.py` (fixtures `toolchain`, `project`, `module_monkeypatch`), `tests/e2e/test_real_harness.py` (template and skip message), `src/agent_manager/orchestrate.py:235-356` (`run_milestone`), `src/agent_manager/cli.py:152` (`worktree_for`), `src/agent_manager/dag.py:78` (`task_branch`), `src/agent_manager/steps/verify.py:117-160` (`run_command`, `shlex.split`)

**Interfaces:**
- Consumes:
  - conftest fixtures `project -> Path` (module-scoped git repo on `main` plus brd board, `XDG_DATA_HOME` in tmp; depends on `toolchain` and `module_monkeypatch`).
  - `orchestrate.run_milestone(milestone: str, *, repo_dir: Path, base_branch: str, branch_prefix: str, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory=None, driver=None, clock=...) -> dict[str, Any]`.
  - `board.show(card_id: str, *, repo_dir: Path) -> models.Card` (has `.status`).
  - `dag.task_branch(prefix: str, card: object) -> str`.
  - `cli.worktree_for(repo_dir: Path, branch: str) -> Path`.
- Produces (module-local, nothing imports them):
  - `_seed_toy_repo(root: Path) -> None`
  - `_add_card(root: Path, title: str, parent: str | None = None, blocked_by: Sequence[str] = (), description: str | None = None) -> str`
  - fixture `toy_milestone -> dict[str, Any]` with keys `"milestone": str`, `"stories": {"A": str, "B": str}`, `"subtasks": {"a1": str, "a2": str, "b1": str}`, `"branches": dict[str, str]` (subtask id to branch), `"main_sha": str`.
  - fixture `completed_run -> dict[str, Any]` (the `run_milestone` payload).

This card adds a test, not production code, so the RED/GREEN cycle is on the pipeline-runnable checks the spec names: the test does not exist and cannot be collected (RED), then it is collected under `-m e2e`, deselected by default, and skipped without `claude` (GREEN). The paid run is a human step.

- [ ] **Step 1: Confirm the `brd add` flags the fixture relies on**

Run: `brd add --help`
Expected: the help lists `--title`, `--parent` and `--description`. Also run `brd block --help` and expect `--by`. If `--description` is missing, stop and report it; the spec says it is confirmed.

- [ ] **Step 2: Run the collection check to verify it fails (RED)**

Run: `uv run pytest -m e2e --collect-only -q tests/e2e/test_real_harness_milestone.py`
Expected: FAIL with `ERROR: file or directory not found: tests/e2e/test_real_harness_milestone.py` (non-zero exit).

- [ ] **Step 3: Write the module**

Create `tests/e2e/test_real_harness_milestone.py` with exactly this content:

```python
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
    with the toy suite green on the last tip and `main` untouched."""
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
```

- [ ] **Step 4: Run the collection check to verify it passes (GREEN, spec verification 1)**

Run: `uv run pytest -m e2e --collect-only -q`
Expected: the output lists `tests/e2e/test_real_harness_milestone.py::test_the_real_claude_drives_a_two_story_milestone_to_done` (alongside the existing `tests/e2e/test_real_harness.py` test), and no collection errors.

- [ ] **Step 5: Verify the default invocation deselects it (spec verification 2, first half)**

Run: `uv run pytest --collect-only -q tests/e2e/test_real_harness_milestone.py; echo "exit=$?"`
Expected: `1 deselected`, no test listed, and `exit=5`. This proves a bare path invocation launches nothing.

- [ ] **Step 6: Verify it skips with the PATH message when `claude` is absent (spec verification 3)**

Build a `PATH` that holds only `git` and `brd`, so no `claude` can be found, and call `uv` by absolute path:

```bash
UV="$(command -v uv)"
NOCLAUDE_BIN="$(mktemp -d)"
ln -s "$(command -v git)" "$NOCLAUDE_BIN/git"
ln -s "$(command -v brd)" "$NOCLAUDE_BIN/brd"
PATH="$NOCLAUDE_BIN" command -v claude || echo "claude absent: ok"
PATH="$NOCLAUDE_BIN" "$UV" run pytest -m e2e -rs tests/e2e/test_real_harness_milestone.py
rm -rf "$NOCLAUDE_BIN"
```

Expected: `claude absent: ok`, then `1 skipped` with the reason `the real \`claude\` CLI is not on PATH; the opt-in e2e tier needs it to perform a real paid run (install claude and put it on PATH, or just run \`uv run pytest\`, which deselects this test)`. The skip must not say `the brd CLI must be installed` or `the git CLI must be installed`; if it does, `brd`'s launcher needs an interpreter off this `PATH`, so add a symlink for that interpreter to `$NOCLAUDE_BIN` and rerun.

- [ ] **Step 7: Smoke the toy seed without claude (Review Focus: green baseline, clean worktree, quoted interpreter)**

This loads the module by path, seeds a throwaway repo shaped like the conftest's `project` (a `.gitignore` already present), runs `VERIFY_COMMANDS` there, and checks the tree stays clean. It launches no agent and commits nothing to this repo.

```bash
uv run python - <<'EOF'
import importlib.util
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "milestone_e2e", "tests/e2e/test_real_harness_milestone.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert shlex.split(module.VERIFY_COMMANDS[0])[0] == sys.executable, module.VERIFY_COMMANDS

root = Path(tempfile.mkdtemp()) / "toy"
root.mkdir()
subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
for key, value in (
    ("user.email", "tests@example.com"),
    ("user.name", "agent-manager tests"),
    ("commit.gpgsign", "false"),
):
    module._git(root, "config", key, value)
(root / ".gitignore").write_text(".brd-local\n", encoding="utf-8")
module._git(root, "add", ".gitignore")
module._git(root, "commit", "-m", "base")

module._seed_toy_repo(root)
for command in module.VERIFY_COMMANDS:
    completed = subprocess.run(shlex.split(command), cwd=root, capture_output=True, text=True)
    assert completed.returncode == 0, (command, completed.stdout, completed.stderr)
porcelain = module._git(root, "status", "--porcelain")
assert porcelain == "", porcelain
assert module._top_level_functions("def add(a, b):\n    return a + b\n") == {"add"}
print("toy seed ok:", root)
EOF
```

Expected: `toy seed ok: /tmp/.../toy`. A non-zero pytest exit means the baseline test is broken; a non-empty porcelain means an ignore entry is missing.

- [ ] **Step 8: Run the full default suite (spec verification 2, second half)**

Run: `uv run pytest`
Expected: all tests pass, with the two `e2e` tests reported as deselected. `tests/e2e/test_production_wiring.py::test_this_module_runs_in_the_default_suite_unmarked` must still pass, proving the marker did not leak into the conftest.

- [ ] **Step 9: Confirm nothing else changed**

Run: `git status --porcelain`
Expected: only `?? tests/e2e/test_real_harness_milestone.py` (plus this plan file and the spec, if they are not yet committed). No change under `src/`, to `tests/e2e/conftest.py`, `pyproject.toml`, `README.md` or `docs/superpowers/specs/2026-09-2*`.

- [ ] **Step 10: Commit**

```bash
git add tests/e2e/test_real_harness_milestone.py
git commit -m "$(cat <<'EOF'
test(e2e): add the opt-in real-harness milestone test

A real two-story toy milestone driven through orchestrate.run_milestone
against the real claude. Marked e2e, excluded from the default suite;
the paid run is a human step: uv run pytest -m e2e.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

- [ ] **Step 11: Hand the paid run to a human**

Do not run `uv run pytest -m e2e` with `claude` on `PATH`: it spends real money and is a human step (O8, acceptance 6). Report in the task summary that the pipeline verified collection, default deselection and the no-`claude` skip, and that a human runs `uv run pytest -m e2e tests/e2e/test_real_harness_milestone.py` to exercise the real run.
