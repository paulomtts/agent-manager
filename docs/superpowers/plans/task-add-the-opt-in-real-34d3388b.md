<!-- task-pipeline: validated -->
<!-- SPEC (verbatim, prepended per the planning pipeline) -->

# Subtask 34d3388b — the opt-in real-harness e2e test

Parent story 01bad3fd (milestone 7aa00a90). Blocked by 8a3922b9, which is done and owns everything this card builds on. Realises addendum R4 and section 3's acceptance line of `docs/superpowers/specs/2026-09-23-real-harness-design.md`: "Real harness, opt-in. `pytest -m e2e` runs the same toy card against a real `claude -p`. Excluded by default. Run by a human."

## Scope

Two files change.

1. A new test module under `tests/e2e/` holding exactly one test: the same toy card as the fake-claude production-wiring test, driven through `cli.run_card` with NO `runner_factory`, against whatever real `claude` is on `PATH`. It carries the `e2e` marker and self-skips when `claude` is absent.
2. `pyproject.toml` `[tool.pytest.ini_options]`: register the `e2e` marker under `markers = [...]` and extend `addopts` to `--import-mode=importlib -m "not e2e"`. `--import-mode=importlib` must stay, and so must the comment above it that explains why (two `test_loader.py` basenames collide in prepend mode).

Out of scope, and explicitly owned elsewhere: the fake `claude` script, `tests/e2e/conftest.py`'s toy-repo fixtures, and the default-suite production-wiring test and its assertions all belong to sibling 8a3922b9 and must not be edited, marked or skipped. Amendments R5 (`SpecResult` with `path` and `note | None`), R6 (worktree created before any agent phase, explore included) and R7 (`rollup.set_status` calls `board.set_status`) are production seams that earlier cards already landed; this card only observes them and must not re-implement or adjust them. Milestone orchestration, parallel stories, `integrate`, non-Claude harnesses and ancestor roll-up are addendum section 4 deferrals and stay untouched.

## Fixture reuse

`tests/e2e/conftest.py` is already present in this worktree (from the sibling's branch) and is the source of the toy repo: `module_monkeypatch`, `toolchain`, `project`, `cards`, `git()`, `VERIFY_COMMANDS = ("git rev-parse --verify HEAD",)`, `AGENT_PHASES`, and the downstream fixtures `run_tree`, `agent_attempts`, `worktree`, all of which consume a fixture named `completed_run`. Reuse them; do not duplicate a second toy repo.

The one thing that cannot be reused is `completed_run`: it depends on `fake_claude_bin`, which prepends a fake `claude` to `PATH`. This card defines, in the new module, a module-scoped fixture that is also named `completed_run`, so it overrides the conftest one and `run_tree`, `agent_attempts` and `worktree` resolve to it without duplication. It makes the same call — `cli.run_card(cards["subtask"], repo_dir=project, base_branch="main", branch_prefix=<this module's prefix>, commands=list(VERIFY_COMMANDS))` — with `fake_claude_bin` deliberately absent from its parameter list. Any new fixture this card needs lives in the new module, not in the shared conftest, for the marker reason below.

`toolchain` only checks `git` and `brd`, so it does not cover `claude`; the new module needs its own `shutil.which("claude")` guard.

## Observable behaviour

Running `uv run pytest` must not collect the new test for execution: it is deselected by `-m "not e2e"`, and every other test in the repo — including `tests/e2e/test_production_wiring.py` and `tests/e2e/test_fake_claude.py` — must keep running exactly as before.

Running `uv run pytest -m e2e` selects the new test and nothing else. With `claude` on `PATH` it performs one real, paid run of the toy card end to end. With `claude` absent it reports a skip whose message names `claude` and `PATH`, so a reader knows immediately that the tool is missing rather than that the test is broken.

The marker must be applied only inside the new module (a module-level `pytestmark` or a per-test `@pytest.mark.e2e`). Applying it from `tests/e2e/conftest.py` or via a package-wide `pytestmark` would mark the sibling's wiring test too, silently removing it from the default suite; the sibling's `test_this_module_runs_in_the_default_suite_unmarked` asserts against exactly that, and it must keep passing.

The marker must be registered in `pyproject.toml`, so no `PytestUnknownMarkWarning` appears and `-m e2e` is a documented selector rather than a typo-prone string.

### What the real run asserts

- The card ends `done`: the returned payload's `status`, and — per R7 and the `best_effort: true` on `mark_done` — `board.show(card_id, repo_dir=project).status == "done"` on the board itself.
- The branch carries commits beyond the base: `git rev-list main..HEAD` in the worktree is non-empty (a branch with no commits would otherwise satisfy the trailer loop vacuously).
- Every one of those commits carries a `Plan-Hash:` trailer, and every commit carries the *same* hash value — the real agent takes it from the implement brief, so a drifting value means the brief or the gate is not doing its job.
- Every recorded result file validates against its declared model: use the conftest `agent_attempts` fixture (the last attempt of each agent phase, built from `run_tree` via `store.open_db` + `store.load_run`), and for each agent attempt with a result file, resolve the phase's declared `result:` name through `agent_manager.results.RESULT_MODELS` and validate the file's contents against that model. R5's `SpecResult` is included by construction, not by a special case.

## Error paths

- `claude` not on `PATH`: `pytest.skip` with a message naming `claude` and `PATH`. This is the path exercised in CI and in this card's own verification.
- `git` or `brd` missing: handled by the shared `toolchain` fixture's existing skip; not re-implemented.
- The run fails or a gate rejects: the assertions fail with the payload's `failed_phase`, `detail` and `warnings` in the assertion message, the way the sibling's first test does, so a human debugging a paid run does not have to re-run it to learn where it stopped.
- An unregistered `result:` name or a malformed result file: surfaces as a validation failure naming the phase, not a bare `KeyError`.

## Tests

Per the test-placement rule in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` section 14 (lines 477-492), which names five tiers — pure unit, steps, adapters, engine with a fake adapter, and one slow opt-in end-to-end test with a real harness.

1. **The real-harness toy-card test** (the whole deliverable, one test function). Tier: **end to end** — the single slow opt-in test the rule allows, in `tests/e2e/`, marked `e2e`, excluded from the default suite, run by a human. It makes the assertions listed above.

No other test is added. Assertion helpers, if any are extracted, are inlined in the module rather than promoted to a pure-unit-tier test of their own: this card's budget is one module, and the rule keeps other tiers for other kinds of subject.

## Verification of this card

None of these spend money; the real run is a human step and must not happen in the pipeline.

- `uv run pytest -m e2e --collect-only` lists the new test.
- `uv run pytest` deselects it, and the sibling's production-wiring tests still run and pass.
- `uv run pytest -m e2e -rs`, with `claude` absent from `PATH`, reports a skip whose reason carries the `claude`/`PATH` message.

<!-- END SPEC -->

---

# Opt-in real-harness e2e test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the single slow opt-in end-to-end test that drives the toy card through `cli.run_card` against a real `claude` on `PATH`, marked `e2e` and excluded from the default `uv run pytest`.

**Architecture:** One new module `tests/e2e/test_real_harness.py` carrying a module-level `pytestmark = pytest.mark.e2e`, a `shutil.which("claude")` skip guard, and a module-scoped `completed_run` fixture that **overrides** the conftest fixture of the same name by omitting `fake_claude_bin` — so the sibling's `project`, `cards`, `run_tree`, `agent_attempts` and `worktree` fixtures all resolve against the real run with no duplicated toy repo. `pyproject.toml` registers the `e2e` marker and adds `-m "not e2e"` to `addopts`, keeping `--import-mode=importlib` and its explanatory comment.

**Tech Stack:** Python 3.12+, pytest (>=9.1.1) with `--import-mode=importlib`, `uv` for running, Pydantic v2 for result-file validation, real `git` / `brd` / `claude` CLIs.

**Spec:** `docs/superpowers/specs/task-add-the-opt-in-real-34d3388b-design.md` (prepended verbatim above).

## Global Constraints

- Exactly two files change: create `tests/e2e/test_real_harness.py`, modify `pyproject.toml`. Nothing else.
- Do not edit, mark or skip `tests/e2e/conftest.py`, `tests/e2e/fake_claude.py`, `tests/e2e/test_fake_claude.py` or `tests/e2e/test_production_wiring.py` — sibling 8a3922b9 owns them and `test_this_module_runs_in_the_default_suite_unmarked` asserts that nothing marks them.
- The `e2e` marker is applied **only** inside the new module (module-level `pytestmark` or a per-test decorator). Never from `tests/e2e/conftest.py` and never as a package-wide marker.
- `addopts` must end up exactly `"--import-mode=importlib -m \"not e2e\""`, and the three-line comment above it in `pyproject.toml` (lines 30-32) stays untouched.
- Do not touch production code under `src/agent_manager/`. R5 (`SpecResult`), R6 (worktree before explore) and R7 (`rollup.set_status` → `board.set_status`) are already landed seams; this card only observes them.
- **Money safety:** between Task 1 and Task 2 the new test is not yet excluded by default, so a plain `uv run pytest` on a machine with `claude` on `PATH` would start a real paid run. In that window run **only** `--collect-only` commands, exactly as the steps below specify. The real paid run is a human step and must never happen in this pipeline.
- Verification command for this repo: `uv run pytest`. There is no lint or typecheck command.

## Review Focus

Five conditions the spec implies that the single test does not exercise on its own. The spec caps this card at one test module and one test function ("No other test is added"), so where a new test would break that cap the check is pinned to an assertion inside the one test or to an explicit verification step, and that is called out per line.

1. **A `Plan-Hash:` string appearing in a commit body but not as a trailer value, or two commits carrying different hashes.** A substring check (`"Plan-Hash:" in message`, the sibling's form) passes on a body mention and cannot see drift. → Covered inside Task 1's single test: parse the value after `Plan-Hash:` off each commit message and assert the set of values has exactly one element.
2. **A `done` run that recorded no agent attempts, or an attempt with `result_path = None`.** `models.Attempt.result_path` is `Path | None` (`src/agent_manager/models.py:85`), so a `for` loop over attempts is vacuously green when the dict is empty or the paths are missing. → Covered inside Task 1's single test: collect the set of phase names actually validated and assert it equals all seven of `AGENT_PHASES`.
3. **A result file whose JSON is malformed or violates the model.** Pydantic's `ValidationError` names the model but not the phase, so a paid run's failure would not say which agent wrote the bad file. → Covered inside Task 1's single test: the validation call is wrapped and re-raised through `pytest.fail` with the phase name and the result path in the message.
4. **`-m e2e` passed on the command line while `addopts` already carries `-m "not e2e"`.** If the two combined instead of the last one winning, `uv run pytest -m e2e` would select nothing and the card's whole deliverable would be unrunnable. → Covered by Task 2's verification step `uv run pytest -m e2e --collect-only`, which must list exactly the one test; no test can assert this, since it is a property of the invocation, not of the code under test.
5. **Running the new module by path, e.g. `uv run pytest tests/e2e/test_real_harness.py`.** `addopts`' `-m "not e2e"` still applies, so the run deselects everything and exits 5 ("no tests collected") — surprising unless documented. → Covered by Task 2 Step 5: the module docstring states the two invocations that actually run it (`uv run pytest -m e2e`, or add `-m e2e` to a path invocation), and the registered marker's help text in `pyproject.toml` repeats it.

---

## File Structure

- **Create `tests/e2e/test_real_harness.py`** — the end-to-end tier's one opt-in test, its `claude`-on-`PATH` guard, its overriding `completed_run` fixture, and the two module-local constants/helpers it cannot import across the conftest boundary.
- **Modify `pyproject.toml:28-33`** — `[tool.pytest.ini_options]`: add `markers`, extend `addopts`.

Why the constants are re-declared rather than imported: with `--import-mode=importlib` pytest inserts nothing into `sys.path`, so `tests/e2e/conftest.py`'s module-level names (`VERIFY_COMMANDS`, `AGENT_PHASES`, `git()`) are not importable from a sibling test module. `tests/e2e/test_production_wiring.py:19-34` sets the precedent by re-declaring `AGENT_PHASES` and a local `_git`. The *fixtures* — the expensive part — are genuinely reused through pytest's own fixture resolution, which is what the spec's "do not duplicate a second toy repo" is about.

---

## Task 1: The real-harness end-to-end test module

**Files:**
- Create: `tests/e2e/test_real_harness.py`
- Reads (do not modify): `tests/e2e/conftest.py`, `src/agent_manager/results.py`, `src/agent_manager/cli.py:588-741`

**Interfaces:**
- Consumes, from `tests/e2e/conftest.py`: fixtures `project -> Path`, `cards -> dict[str, str]` (keys `"milestone"`, `"story"`, `"subtask"`), `run_tree -> models.Run`, `agent_attempts -> dict[str, models.Attempt]`, `worktree -> Path` — all `scope="module"`; `run_tree`, `agent_attempts` and `worktree` each depend on a fixture named `completed_run`, which this module overrides.
- Consumes, from `src/agent_manager`: `cli.run_card(card_id, *, repo_dir, branch_prefix, base_branch="master", allow_no_verification=False, commands=(), runner_factory=None, clock=...) -> dict[str, Any]` with payload keys `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings` (`cli.py:727-739`); `board.show(card_id, *, repo_dir) -> Card` with `.status`; `results.RESULT_MODELS: dict[str, type[BaseModel]]`; `results.resolve_result_model(name, table, *, phase) -> type[BaseModel]`; `workflow.load_builtin("task")` with `.phase(name).result -> str`; `models.Attempt.result_path: Path | None`.
- Produces, for Task 2: the module path `tests/e2e/test_real_harness.py`, the marker name `e2e`, and the test node id `tests/e2e/test_real_harness.py::test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch`.

- [ ] **Step 1: Confirm the RED — nothing is selected by `-m e2e` today**

Run: `uv run pytest -m e2e --collect-only -q`

Expected: FAIL to find anything — every test is deselected and pytest exits 5 with "no tests ran" / a `no tests collected` summary, plus (harmlessly) `PytestUnknownMarkWarning` is not raised because no test uses the mark yet. This is the baseline the spec's first verification command must flip.

- [ ] **Step 2: Write the new test module**

Create `tests/e2e/test_real_harness.py` with exactly this content:

```python
"""End-to-end tier (design §14 lines 477-492): the ONE slow opt-in test.

Addendum R4 and §3 of `docs/superpowers/specs/2026-09-23-real-harness-design.md`:
"Real harness, opt-in. `pytest -m e2e` runs the same toy card against a real
`claude -p`. Excluded by default. Run by a human."

This module costs real money every time it runs, so `pyproject.toml`'s
`addopts` carries `-m "not e2e"` and the marker below opts it out of the
default suite. To run it: `uv run pytest -m e2e` (a bare path invocation such
as `uv run pytest tests/e2e/test_real_harness.py` is still deselected by
`addopts` and exits 5 -- pass `-m e2e` alongside the path).

The marker is applied HERE and only here. Marking it from `tests/e2e/conftest.py`
would drag the sibling's free, fake-claude `test_production_wiring.py` out of the
default suite, which its own `test_this_module_runs_in_the_default_suite_unmarked`
forbids.

Everything expensive is reused from `tests/e2e/conftest.py`: the toy git repo,
the toy brd board and the milestone -> story -> subtask card chain. Only
`completed_run` is overridden, because the conftest's version pulls in
`fake_claude_bin`, which prepends a fake `claude` to `PATH` -- the exact thing
this module must not have.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_manager import board, cli, results
from agent_manager.workflow import load_builtin

pytestmark = pytest.mark.e2e

BRANCH_PREFIX = "e2e-real"
"""Distinct from the sibling's `m1`, so a real run and a fake run in the same
toy repo could never land on the same branch or worktree path."""

VERIFY_COMMANDS = ("git rev-parse --verify HEAD",)
"""Must match `tests/e2e/conftest.py`'s constant of the same name.

Re-declared rather than imported: `--import-mode=importlib` puts nothing on
`sys.path`, so a conftest's module-level names are not importable from a
sibling test module. `test_production_wiring.py:19-34` re-declares
`AGENT_PHASES` and its own `_git` for the same reason."""

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)
"""`builtin/task.yaml`'s seven agent phases; see `VERIFY_COMMANDS` on why this
is re-declared."""

PLAN_HASH_TRAILER = "Plan-Hash:"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _plan_hashes(message: str) -> list[str]:
    """Every `Plan-Hash:` trailer VALUE in one commit message.

    The value, not merely the presence of the string: a commit body that
    mentions the word would satisfy a substring check, and two commits with
    different hashes would satisfy it too.
    """
    return [
        line.split(":", 1)[1].strip()
        for line in message.splitlines()
        if line.strip().startswith(PLAN_HASH_TRAILER)
    ]


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
def completed_run(real_claude, project, cards) -> dict[str, Any]:
    """One real, paid `cli.run_card` -- no `runner_factory`, no fake on `PATH`.

    Overrides the conftest fixture of the same name, so `run_tree`,
    `agent_attempts` and `worktree` resolve against THIS run. `fake_claude_bin`
    is deliberately absent from the parameter list: `cli.default_runner_factory`
    (`cli.py:588`) builds the real `dispatch.AgentRunner` over
    `harness/launcher.py:94` `run_direct`, which resolves `claude` on `PATH`,
    and the whole point of this module is that what it finds there is real.
    """
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix=BRANCH_PREFIX,
        commands=list(VERIFY_COMMANDS),
    )


def test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch(
    project, completed_run, agent_attempts, worktree
):
    """The whole deliverable: a real `claude -p` takes the toy subtask from
    `todo` to `done`, leaving a branch whose every commit carries one and the
    same `Plan-Hash` trailer and whose every result file validates."""
    assert completed_run["status"] == "done", (
        completed_run["failed_phase"],
        completed_run["detail"],
        completed_run["warnings"],
    )

    # R7 / `mark_done` is `best_effort: true`, so the payload alone is not proof.
    card = board.show(completed_run["card_id"], repo_dir=project)
    assert card.status == "done", (
        card.status,
        completed_run["failed_phase"],
        completed_run["warnings"],
    )

    revisions = _git(worktree, "rev-list", "main..HEAD").split()
    assert revisions, "the branch carries no commits beyond main"

    hashes: set[str] = set()
    for revision in revisions:
        message = _git(worktree, "show", "-s", "--format=%B", revision)
        values = _plan_hashes(message)
        assert values, (revision, message)
        hashes.update(values)
    assert len(hashes) == 1, hashes

    workflow = load_builtin("task")
    validated: set[str] = set()
    for name, attempt in sorted(agent_attempts.items()):
        assert attempt.result_path is not None, name
        path = Path(attempt.result_path)
        assert path.is_file(), (name, path)
        model = results.resolve_result_model(
            workflow.phase(name).result, results.RESULT_MODELS, phase=name
        )
        try:
            model.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as error:
            pytest.fail(
                f"{name}: {path} does not validate against "
                f"{model.__name__}: {error}"
            )
        validated.add(name)

    # Non-vacuity: an empty `agent_attempts` would sail through the loop above.
    assert validated == set(AGENT_PHASES), sorted(validated)
```

- [ ] **Step 3: Run the collection check to verify the marker now selects it**

Run: `uv run pytest -m e2e --collect-only -q`

Expected: one line, `tests/e2e/test_real_harness.py::test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch`, and `1/N tests collected (N-1 deselected)`. Also expected, and fixed in Task 2: a `PytestUnknownMarkWarning: Unknown pytest.mark.e2e` warning.

- [ ] **Step 4: Confirm the second RED — the default suite still collects it**

Run: `uv run pytest --collect-only -q`

Expected: the same `test_real_harness.py` node id appears in the listing, with nothing deselected. That is the failure Task 2 fixes. **Do not run `uv run pytest` without `--collect-only` at this point** — if `claude` is on your `PATH` that would start a real paid run.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_real_harness.py
git commit -m "test: add the opt-in real-harness e2e toy-card test"
```

---

## Task 2: Register the `e2e` marker and exclude it from the default suite

**Files:**
- Modify: `pyproject.toml:28-33` (`[tool.pytest.ini_options]`)

**Interfaces:**
- Consumes, from Task 1: the marker name `e2e`, applied as a module-level `pytestmark` in `tests/e2e/test_real_harness.py`.
- Produces: `addopts = "--import-mode=importlib -m \"not e2e\""` and a `markers` list containing the `e2e` entry, so `uv run pytest` deselects the test and `uv run pytest -m e2e` selects it.

- [ ] **Step 1: Confirm the RED is still standing**

Run: `uv run pytest --collect-only -q | tail -5`

Expected: the summary line shows `N tests collected` with **0 deselected**, and `tests/e2e/test_real_harness.py` is in the listing. (Collection only — still no paid run.)

- [ ] **Step 2: Edit `[tool.pytest.ini_options]`**

In `pyproject.toml`, replace the block currently at lines 28-33:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
# importlib mode, not the default prepend mode: tests/workflow/test_loader.py and
# tests/roles/test_loader.py share a basename, and prepend mode names test modules
# after their basename alone, so collecting both aborts with "import file mismatch".
addopts = "--import-mode=importlib"
```

with:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "e2e: slow, opt-in, costs real money -- drives the toy card against the real `claude` on PATH. Excluded by the `-m \"not e2e\"` in addopts; run it with `uv run pytest -m e2e` (a bare path invocation is still deselected, so pass `-m e2e` alongside any path).",
]
# importlib mode, not the default prepend mode: tests/workflow/test_loader.py and
# tests/roles/test_loader.py share a basename, and prepend mode names test modules
# after their basename alone, so collecting both aborts with "import file mismatch".
# `-m "not e2e"` keeps the paid real-harness test out of the default suite; a `-m`
# passed on the command line overrides this one, which is how `-m e2e` selects it.
addopts = "--import-mode=importlib -m \"not e2e\""
```

- [ ] **Step 3: Verify the default suite now deselects it (spec verification b)**

Run: `uv run pytest`

Expected: PASS, green, with `1 deselected` in the summary and no `test_real_harness` node in the run. The sibling's `tests/e2e/test_production_wiring.py` and `tests/e2e/test_fake_claude.py` still run and pass, `test_this_module_runs_in_the_default_suite_unmarked` included. No `PytestUnknownMarkWarning`. This is now safe to run: the marker keeps the paid test out.

- [ ] **Step 4: Verify `-m e2e` selects exactly the new test (spec verification a, Review Focus 4)**

Run: `uv run pytest -m e2e --collect-only -q`

Expected: exactly one collected node, `tests/e2e/test_real_harness.py::test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch`, everything else deselected, and no unknown-mark warning. If instead nothing is collected, the command-line `-m` failed to override `addopts`' `-m` — stop and fix, the deliverable would be unrunnable.

- [ ] **Step 5: Verify the skip message when `claude` is absent (spec verification c)**

Run (builds a scratch `PATH` holding only `git`, `brd`, `uv` and `python3`, so `claude` is provably absent while the toolchain skip is not tripped; `uv`, `brd` and `claude` all live in `~/.local/bin` on this machine, so a bare `PATH="/usr/bin:/bin"` would fail to find `uv` at all):

```bash
scratch="$(mktemp -d)"
for tool in git brd uv python3; do ln -s "$(command -v "$tool")" "$scratch/$tool"; done
env PATH="$scratch" "$scratch/uv" run pytest -m e2e -rs
```

Expected: `1 skipped`, and the `-rs` short summary reads `SKIPPED ... the real `claude` CLI is not on PATH; the opt-in e2e tier needs it to perform a real paid run ...`. If the skip reason names `git` or `brd` instead, a tool symlink is wrong; fix the scratch dir so the `claude` message is the one reported.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git commit -m "chore: register the e2e marker and exclude it from the default suite"
```

---

## Human step (not part of this pipeline)

With a real, authenticated `claude` on `PATH`, a human runs the paid test once and confirms it passes:

```bash
uv run pytest -m e2e -rs -vv
```

This spends money and must not be run by an agent or in CI. A failure here reports `failed_phase`, `detail` and `warnings` from the payload, so the stopping point is legible without a second paid run.
