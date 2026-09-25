<!-- task-pipeline: validated -->
# Subtask 2af0e413: Add the opt-in real-harness parallel test

Parent story 4633be8c ("Prove it: parallel under a fake claude, against a real harness, and documented"), milestone cdbfa10d. Blocked by 1976123f (done). This spec narrows parallel-stories addendum P7 and acceptance 7 (`docs/superpowers/specs/2026-09-24-parallel-stories-design.md`) to one test module.

## Scope

Add one new module, `tests/e2e/test_real_harness_parallel.py`. It is the single opt-in real-harness test for parallel stories: a toy milestone with two independent stories, driven through the real `orchestrate.run_milestone(..., max_concurrent=2)` against the real `claude -p`, with no `runner_factory`, no `driver`, and no fake `claude` on `PATH`.

Model it on `tests/e2e/test_real_harness_milestone.py`:

- `pytestmark = pytest.mark.e2e` at module level, in this module only. Do not mark from `tests/e2e/conftest.py`. That would pull the free fake-claude `test_production_wiring.py` out of the default suite and break its `test_this_module_runs_in_the_default_suite_unmarked`.
- Re-declare these helpers in the module rather than importing them, because `--import-mode=importlib` puts nothing on `sys.path`: `_git`, `_plan_hashes`, `_add_card` (with `--description`), `_seed_toy_repo`, `_top_level_functions`, `VERIFY_COMMANDS`, `TOY_GITIGNORE_ENTRIES`, and `PLAN_HASH_TRAILER`. `_block` is not needed because the stories are independent. `_seed_toy_repo` is extended so the one baseline commit also seeds `hello.py` and a passing `test_hello.py`. Without a baseline test for `hello.py`, pytest would exit 5 and verification would fail before any agent runs.
- Use the conftest fixtures by name: `toolchain` / `project` (the temp repo on `main` with a board) and `module_monkeypatch`, as the template does.
- Add a `real_claude` module fixture that follows the template. It calls `shutil.which("claude")`. If that returns nothing, it calls `pytest.skip` with a message saying the real `claude` CLI is not on PATH and that `uv run pytest` deselects this test.
- `BRANCH_PREFIX = "e2e-real-p"`. It must differ from `m1`, `m3`, `e2e-real` and `e2e-real-m`.
- Add a `toy_parallel_milestone` module fixture. It seeds the repo, then creates milestone M with two stories and no `brd block` between them:
  - Story A has subtask a1: "add `add(a, b)` to `calc.py` and `test_add` in `test_calc.py`".
  - Story B has subtask b1: "add `greet(name)` to `hello.py` and a test in `test_hello.py`".
  - The descriptions carry the full instructions and name `python -m pytest -q` as the suite command.
  - The fixture records branch names via `dag.task_branch(BRANCH_PREFIX, board.show(...))` and records `main_sha` before the run.
- Override `completed_run` without `fake_claude_bin`. It depends on `real_claude` first, so a missing `claude` skips before any board is built. It calls `orchestrate.run_milestone(milestone, repo_dir=project, base_branch="main", branch_prefix=BRANCH_PREFIX, commands=list(VERIFY_COMMANDS), max_concurrent=2)`. `max_concurrent` is the keyword-only parameter at `src/agent_manager/orchestrate.py:476` on the m4 line. It defaults to 1.
- The module docstring must state the following:
  - The pipeline cannot run this test.
  - The test is excluded by `addopts`' `-m "not e2e"` (`pyproject.toml:38`; the marker is registered at `:30`).
  - Every run spends real money.
  - The real run is a human step afterwards: `uv run pytest -m e2e`. A bare path is still deselected and exits 5.
  - The marker lives only in this module, and why.

Out of scope: `tests/e2e/fake_claude.py` and the fake-claude parallel tests (owned by 1976123f), README and main-spec docs (owned by a2dad516), and anything in `src/`. The addendum's section 5 exclusions are also out of scope: Integrate, per-story readiness, milestone-aware resume, watch/retry/cancel, cost capture, the reviewer's Plan-Hash brief, and Ctrl-C.

## Observable behavior (one test, `test_the_real_claude_drives_two_independent_stories_in_parallel`)

1. `completed_run.get("done") is True`. The failure message reports `story`, `subtask`, `failed_phase`, `detail` and `warnings` via `.get`, because an escalation payload lacks `completed`. `completed_run["completed"]` contains both a1 and b1 in any order: under concurrency the order is not fixed.
2. On the board (`board.show(...).status`), a1 and b1 are `done`, and both stories and the milestone are `done` by rollup.
3. The two branches were cut from the base independently:
   - `git merge-base --is-ancestor main <branch>` returns 0 for each branch.
   - `git merge-base --is-ancestor` returns nonzero both ways between the two branches.
4. `rev-list main..<branch>` is non-empty for each branch. Every commit in that range carries at least one `Plan-Hash:` trailer at column 0, and the branch has exactly one distinct value.
5. On each tip's worktree (`cli.worktree_for(project, branch)`), every `VERIFY_COMMANDS` entry exits 0. `_top_level_functions` shows `add` in a1's `calc.py` and `greet` in b1's `hello.py`.
6. `git rev-parse main` equals the recorded `main_sha`.
7. Load the run tree with `store.open_db(project)` and `store.load_run(conn, completed_run["run_id"])`, closing the connection in `finally`. Find the `implement` phase of each subtask. The timestamps are on `PhaseRun.started_at` / `ended_at` (`models.py:97-98`), not on `Attempt`. The two intervals must overlap: `a.started_at < b.ended_at and b.started_at < a.ended_at`, with all four timestamps non-None.

## Error paths

- If `claude` is not on PATH, the module skips with the PATH message and never builds a board or spends money.
- If a real run escalates, the done assertion fails with the escalation's story, subtask, phase and detail, rather than a `KeyError`.
- If implement intervals don't overlap, which means the run was effectively sequential, the failure message prints all four timestamps.

## Tests and tier

Per main spec section 14 ("End to end -- one slow, opt-in test with a real harness, marked and excluded from the default suite"), the only new test belongs in the e2e tier: `tests/e2e/test_real_harness_parallel.py::test_the_real_claude_drives_two_independent_stories_in_parallel`, marked `e2e`. It does not belong in the unit or steps tiers. No other tests are added.

## Verification

- Full suite: `uv run pytest` stays green. That includes all of `tests/e2e` and the existing check that `max_concurrent=1` behaves sequentially. The new test is deselected.
- Collection: `uv run pytest -m e2e --collect-only` lists the new test.
- Default deselection: `uv run pytest --collect-only` does not list it.
- Skip: with `claude` absent from PATH, `uv run pytest -m e2e tests/e2e/test_real_harness_parallel.py -rs` reports it skipped with the PATH message. Never run it with `claude` on PATH in the pipeline.
- The real paid run is a human step afterwards: `uv run pytest -m e2e`.

---

# Opt-in Real-Harness Parallel Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tests/e2e/test_real_harness_parallel.py`, the single opt-in (`e2e`-marked) test that drives a two-independent-story toy milestone through the real `orchestrate.run_milestone(..., max_concurrent=2)` against the real `claude -p` and proves both stories finished, independently, with overlapping implement phases.

**Architecture:** One new test module in the e2e tier, modelled line-for-line on `tests/e2e/test_real_harness_milestone.py`. It re-declares that module's toy-repo helpers locally (importlib mode puts nothing on `sys.path`), extends the seed with `hello.py`/`test_hello.py`, builds its own board in a module fixture, overrides the conftest `completed_run` with a real, fake-free call, and asserts seven observable properties. Nothing in `src/`, `tests/e2e/conftest.py`, `tests/e2e/fake_claude.py`, README or docs changes.

**Tech Stack:** Python 3.12, pytest 9 (`--import-mode=importlib`, `-m "not e2e"` in addopts), git, `brd`, the real `claude` CLI (human run only), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413/docs/superpowers/specs/task-add-the-opt-in-real-2af0e413-design.md` (reproduced verbatim above).

**Working directory for every command:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413` (branch `m4/task-add-the-opt-in-real-2af0e413`, cut from `m4/task-prove-parallel-stories-1976123f`). All relative paths below are relative to it.

**Facts verified on this branch before writing the plan:**
- `orchestrate.run_milestone` (`src/agent_manager/orchestrate.py:465-477`) takes keyword-only `max_concurrent: int = 1` (line 476) and on success returns `{"done": True, "run_id", "levels", "completed", "tips", "warnings"}` (lines 562-572).
- `models.PhaseRun` has `name`, `kind`, `status`, `started_at: datetime | None`, `ended_at: datetime | None` (`src/agent_manager/models.py:91-108`); `SubtaskRun.card_id`/`phases` (lines 111-119); `Run.stories` (line 160).
- `store.open_db(root)` (`src/agent_manager/store.py:105`) and `store.load_run(conn, run_id) -> models.Run | None` (line 403); `tests/e2e/conftest.py:198-205` uses exactly `store.open_db(project)` then `store.load_run(conn, completed_run["run_id"])`.
- `cli.worktree_for(repo_dir, branch)` is at `src/agent_manager/cli.py:152`; `dag.task_branch(prefix, card)` at `src/agent_manager/dag.py:78`.
- Conftest fixtures `module_monkeypatch` (`tests/e2e/conftest.py:100`), `toolchain` (:107), `project` (:149, module-scoped, git repo on `main` plus a brd board, sets `XDG_DATA_HOME`).
- The e2e marker is registered at `pyproject.toml:30-32`; addopts at `pyproject.toml:38` is `--import-mode=importlib -m "not e2e"`.
- The workflow phase is named `"implement"` (`tests/e2e/conftest.py:44`).

## Global Constraints

- Marker: `pytestmark = pytest.mark.e2e` in `tests/e2e/test_real_harness_parallel.py` ONLY; never touch `tests/e2e/conftest.py`.
- `BRANCH_PREFIX = "e2e-real-p"` (must differ from `m1`, `m3`, `e2e-real`, `e2e-real-m`).
- No `runner_factory`, no `driver`, no `fake_claude_bin`, no fake `claude` on `PATH`.
- Call: `orchestrate.run_milestone(milestone, repo_dir=project, base_branch="main", branch_prefix=BRANCH_PREFIX, commands=list(VERIFY_COMMANDS), max_concurrent=2)`.
- Skip message when `claude` is missing must say the real `claude` CLI is not on PATH and that `uv run pytest` deselects this test.
- Module docstring: pipeline cannot run it; excluded by addopts `-m "not e2e"` (`pyproject.toml:38`, marker at `:30`); every run spends real money; real run is a human step afterwards, `uv run pytest -m e2e`; a bare path is still deselected and exits 5; the marker lives only here and why.
- Out of scope: `src/`, `tests/e2e/fake_claude.py`, `tests/e2e/conftest.py`, README, main spec, any other test.
- NEVER run the new test with `claude` on `PATH` from the pipeline: it spends money.
- Default suite `uv run pytest` must stay green.

## Review Focus

- A pipeline run where `claude` IS on `PATH` would spend money: the skip check must be run with a `PATH` scrubbed of every directory containing `claude`, and `uv` invoked by absolute path, since `uv` and `claude` commonly share `~/.local/bin`. Task 1 Step 6 builds that PATH and asserts `claude` is absent before running pytest.
- Fixture order: if `project`/`toolchain` were resolved before `real_claude`, a machine without `brd` would skip with the wrong message, and a machine with `brd` would build a board it never uses. `real_claude` is the first parameter of both `completed_run` and the test, so it resolves first. Task 1 Step 6 checks that the skip reason is the PATH message.
- Nondeterministic completion order under concurrency: asserting `completed == [a1, b1]` would flake. The test compares sets (`set(completed) == {a1, b1}` plus `len == 2`).
- An escalation payload has no `completed`/`tips`: every read that comes before the done assertion uses `.get`, and the done assertion runs first.
- A missing `implement` phase or a None timestamp must fail with a readable message, not an `AttributeError`/`TypeError`. `_implement_span` asserts the phase exists, and the overlap assertion checks all four timestamps are non-None before comparing them.

---

## File Structure

- Create: `tests/e2e/test_real_harness_parallel.py`. The single opt-in real-harness parallel test: local helpers, the `real_claude`, `toy_parallel_milestone` and `completed_run` module fixtures, and one test function.

No other file is created or modified.

### Task 1: The opt-in real-harness parallel test module

**Files:**
- Create: `tests/e2e/test_real_harness_parallel.py`
- Test: `tests/e2e/test_real_harness_parallel.py::test_the_real_claude_drives_two_independent_stories_in_parallel` (e2e tier, marked `e2e`, per main spec section 14)

**Interfaces:**
- Consumes (conftest, by fixture name): `project -> Path` (module scope; also pulls in `toolchain` and `module_monkeypatch`).
- Consumes (src): `orchestrate.run_milestone(milestone: str, *, repo_dir: Path, base_branch: str, branch_prefix: str, commands: Sequence[str] = (), ..., max_concurrent: int = 1) -> dict[str, Any]`; `board.show(card_id, repo_dir=...) -> models.Card` (`.status`); `dag.task_branch(prefix: str, card) -> str`; `cli.worktree_for(repo_dir: Path, branch: str) -> Path`; `store.open_db(root: Path) -> sqlite3.Connection`; `store.load_run(conn, run_id: str) -> models.Run | None`; `models.PhaseRun.started_at/ended_at: datetime | None`.
- Produces: module fixtures `real_claude -> Path`, `toy_parallel_milestone -> dict[str, Any]` with keys `milestone: str`, `stories: {"A": str, "B": str}`, `subtasks: {"a1": str, "b1": str}`, `branches: {card_id: branch_name}`, `main_sha: str`; `completed_run -> dict[str, Any]`. No later task consumes these.

A note on RED for this card: the test itself cannot run in the pipeline (it costs money), so the failing check is its collection under `-m e2e`, which fails before the module exists. GREEN is: collected under `-m e2e`, deselected by default, skipped with the PATH message when `claude` is absent, and the whole default suite still green.

- [ ] **Step 1: Run the collection check to verify it fails (RED)**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413
uv run pytest -m e2e --collect-only -q tests/e2e/test_real_harness_parallel.py
```
Expected: FAIL. pytest reports `ERROR: file or directory not found: tests/e2e/test_real_harness_parallel.py` and exits 4.

- [ ] **Step 2: Write the module**

Create `tests/e2e/test_real_harness_parallel.py` with exactly this content:

```python
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
    tips, `main` untouched, and the two implement phases overlapping."""
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
    conn = store.open_db(project)
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
```

- [ ] **Step 3: Run the collection check to verify it passes (GREEN)**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413
uv run pytest -m e2e --collect-only -q
```
Expected: exit 0, and the output lists `tests/e2e/test_real_harness_parallel.py::test_the_real_claude_drives_two_independent_stories_in_parallel` alongside the two existing e2e tests (`test_real_harness.py`, `test_real_harness_milestone.py`). This only collects, so nothing runs and nothing is spent.

- [ ] **Step 4: Verify the default run deselects it**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413
uv run pytest --collect-only -q | grep -c test_real_harness_parallel || true
```
Expected: `0`. The default addopts `-m "not e2e"` deselects it.

Also run:
```bash
uv run pytest -q tests/e2e/test_real_harness_parallel.py; echo "exit=$?"
```
Expected: `1 deselected` in the summary and `exit=5` (a bare path is still deselected, as the docstring states).

- [ ] **Step 5: Verify the marker is only in this module and conftest is untouched**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413
git status --porcelain
grep -n "pytest.mark.e2e" tests/e2e/conftest.py || echo "conftest unmarked"
```
Expected: `git status` shows only `?? tests/e2e/test_real_harness_parallel.py` (plus the plan/spec docs if they are not yet committed), and the grep prints `conftest unmarked`.

- [ ] **Step 6: Verify it skips with the PATH message when `claude` is absent (never with `claude` present)**

This spends nothing only if `claude` is truly off `PATH`. Build a `PATH` without any directory that holds a `claude` executable, call `uv` by absolute path (it often lives next to `claude` in `~/.local/bin`), and refuse to continue if `claude` is still resolvable:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413
UV="$(command -v uv)"
NOCLAUDE_PATH="$(printf '%s' "$PATH" | tr ':' '\n' | while IFS= read -r d; do [ -n "$d" ] && [ ! -x "$d/claude" ] && printf '%s:' "$d"; done)"
NOCLAUDE_PATH="${NOCLAUDE_PATH%:}"
if env PATH="$NOCLAUDE_PATH" sh -c 'command -v claude' >/dev/null 2>&1; then echo "ABORT: claude still on PATH"; else env PATH="$NOCLAUDE_PATH" "$UV" run pytest -m e2e tests/e2e/test_real_harness_parallel.py -rs; fi
```
Expected: no `ABORT` line; pytest reports `1 skipped`, and the `-rs` summary line reads `SKIPPED [1] ... the real \`claude\` CLI is not on PATH; the opt-in e2e tier needs it to perform a real paid run (install claude and put it on PATH, or just run \`uv run pytest\`, which deselects this test)`. If the skip reason is instead `the brd CLI must be installed for the e2e tier`, then `real_claude` did not resolve first: check that `real_claude` is the first parameter of both `completed_run` and the test function.

- [ ] **Step 7: Run the whole default suite**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413
uv run pytest
```
Expected: all tests pass, with 3 deselected (the three `e2e` modules' tests). That includes all of `tests/e2e` (unmarked fake-claude tests, `test_production_wiring.py::test_this_module_runs_in_the_default_suite_unmarked`, and the `max_concurrent=1` sequential check in `tests/e2e/test_parallel_milestone.py`).

- [ ] **Step 8: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-opt-in-real-2af0e413
git add tests/e2e/test_real_harness_parallel.py
git commit -m "$(cat <<'EOF'
Add the opt-in real-harness parallel test

One e2e-marked module drives a two-independent-story toy milestone through
the real orchestrate.run_milestone(max_concurrent=2) against the real
claude -p, and checks both stories done, independent branches, one
Plan-Hash per branch, green tips, main untouched and overlapping implement
phases. Deselected by default; the paid run is a human step:
uv run pytest -m e2e.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

- [ ] **Step 9: Hand off the human step**

Report, without running it: the real paid run is a human step afterwards, `uv run pytest -m e2e` (or `uv run pytest -m e2e tests/e2e/test_real_harness_parallel.py` for this module alone), run from a checkout where the real `claude` is on `PATH`. The pipeline must not run it.
