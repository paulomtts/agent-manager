<!-- task-pipeline: validated -->
# Subtask 64babfa2 — Move dry_run_payload's pure plan computation into runs.py

Parent story: 4bc0a3e0 "cli.py sheds its collaborator role" (milestone 9c44c2fb, architecture cleanup S1). Source of truth: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` §3 "S1", §5 "Compatibility", §6 "Testing". Builds on the state left by `1c21b3dd` (this card's `blocked_by`). Line numbers below are from this worktree at spec time and drift; re-read before editing.

## Scope

In scope:

- Add a new pure function to `src/agent_manager/runs.py`, `compute_dry_run_plan(stories, *, repo_dir, branch_prefix, base_branch, max_concurrent)`, holding the computation currently inline in `cli.dry_run_payload` (`cli.py:776-862`): the `dag.assert_no_blocker_cycles` check, `dag.compute_levels`, the `stories_by_id` map, the per-story row assembly (`dag.stack_bases`, `dag.story_root`, `dag.subtask_branch`, `dag.remaining_subtasks`, the `merged_from` key only when `root.kind == "merged"`), the per-level `concurrent = min(len(level), max_concurrent)`, and the Integrate plan (`integration.integration_branch`, `worktree_for(repo_dir, ...)`, `integration.merge_order`). It returns the computed level rows and the integrate plan (exact return shape — e.g. a small dataclass or a dict with `levels`/`integrate` — is a planning choice; plain data, no Pydantic, since it is internal-only state).
- `max_concurrent` is a required argument of `compute_dry_run_plan`; the `DEFAULT_MAX_CONCURRENT` constant and default stay on `cli.dry_run_payload` (`tests/test_cli.py:3147` asserts `cli.DEFAULT_MAX_CONCURRENT`).
- `cli.dry_run_payload` keeps its exact signature, calls `runs.compute_dry_run_plan(...)`, and only assembles the envelope dict: `max_concurrent`, `levels`, `already_done`, `integrate`, in that key order. Its current docstring (`cli.py:776-800`) describes the pure computation's own details (the cycle check ordering, `stories_by_id`, `stack_bases`, the merged-root rule, `concurrent`'s formula) — that content moves onto `runs.compute_dry_run_plan`'s docstring, next to the code it now describes. `cli.dry_run_payload` keeps a short docstring describing only what it still does: delegate to `compute_dry_run_plan` and assemble the envelope (mentioning `already_done_entries` and the envelope key order, since those still live here). Splitting the docstring this way, rather than copying it verbatim onto both functions, is what "docstring intent" means here — a verbatim copy would leave `cli.dry_run_payload`'s docstring describing `dag` calls it no longer makes.
- The `integration` import inside the new `runs.py` function stays **deferred (in-function)**, with a comment mirroring the existing one at `cli.py:808-810`. A top-level import would create `runs → integration → cli → runs` because `integration.py:29` still imports `cli` and `cli.py:51` imports `runs` at top level. `cli.dry_run_payload` no longer needs its own deferred `integration` import once the computation moves; removing that now-dead import from `dry_run_payload` is fine, but the deferred imports elsewhere in `cli.py` are untouched.
- `already_done_entries` (`cli.py:749`) — judgment call, not settled by the card text: it stays in `cli.py` and `dry_run_payload` keeps calling it when assembling the envelope. The card names "levels, bases, roots and the Integrate plan", not the already-done listing. Moving it too would be defensible, but it is left out so this card stays inside what the card text says.

Out of scope (owned elsewhere or excluded by the spec):

- Repointing `bases.py:34`, `integration.py:29`, `orchestrate.py:54` at `runs`, deleting cli.py's other deferred in-function imports, dropping re-exports, and the static "bases/integration never import cli" test — all belong to sibling `61a0d9be`.
- `default_runner_factory` stays in `cli.py` (exemption set by `46244d0e`); helpers already moved by `46244d0e`/`1c21b3dd` are not touched again.
- No change to the pygents turn/phase model, checkpoint format, harness adapter contract; no typecheck/CI gate; no rewriting of existing monkeypatch calls; no grafo change.
- `runs.py` gains no Typer import.

## Observable behavior

None changes (§5 Compatibility). `am run --dry-run` output is byte-for-byte identical: same envelope, same exit codes, same key order at every level (top-level `max_concurrent, levels, already_done, integrate`; level row `level, concurrent, stories`; story row `story, title, root, subtasks[, merged_from]`; subtask row `id, title, status, branch, base`; integrate `branch, worktree, order`; order entries `story, tip`). `merged_from` is still present only on merged-root rows, in `blocked_by` order. `worktree` is still `str(worktree_for(repo_dir, integrate_branch))`, and `repo_dir` is still only joined onto, never read.

## Error paths

- A blocker cycle still raises from `dag.assert_no_blocker_cycles` as the first thing done, now inside `runs.compute_dry_run_plan`, and propagates through `cli.dry_run_payload` unchanged (same exception type and message). The CLI error envelope and exit code are the same as before.
- No new error paths; a `max_concurrent < 1` is still refused by the caller, not here.

## Tests

Tier rule: this repo has no tier taxonomy. Tests mirror source one file per module, flat under `tests/`; only real-harness end-to-end tests go in `tests/e2e/` (marked `e2e`, excluded by `-m "not e2e"` in `pyproject.toml`). This is a pure-function extraction, so nothing goes in `tests/e2e/`.

- Existing `cli.dry_run_payload` tests in `tests/test_cli.py` (around lines 880-1110, e.g. `test_the_dry_run_payload_lists_remaining_subtasks_on_full_list_bases`, `test_the_dry_run_payload_plans_integrate_over_every_story_in_integrate_order`, `test_the_dry_run_payload_defaults_to_four_lanes`, the cycle-refusal test near line 967) — tier: flat `tests/test_cli.py`. Must pass **unchanged**; they are the byte-for-byte guard for the envelope.
- Existing CLI-level `--dry-run` tests and the `DEFAULT_MAX_CONCURRENT` assertion in `tests/test_cli.py` — tier: flat `tests/test_cli.py`. Pass unchanged.
- Optional: one direct test of `runs.compute_dry_run_plan` (e.g. levels and integrate order for a small census, `concurrent` honoring the passed `max_concurrent`) — tier: flat `tests/test_runs.py` (exists, mirrors `runs.py`). Add only if planning finds it useful; the existing `test_cli.py` coverage already exercises the function through the wrapper.

## Verification

- fullSuite: `uv run pytest` (green)
- typecheck: none (CLAUDE.md: no separate lint or typecheck command)
- lint: none

---

# Move dry_run_payload's pure plan computation into runs.py — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the pure dry-run plan computation (cycle check, levels, bases, roots, `merged_from`, per-level `concurrent`, Integrate plan) out of `cli.dry_run_payload` into a new `runs.compute_dry_run_plan`, leaving `cli.dry_run_payload` as a thin envelope assembler with byte-identical output.

**Architecture:** `runs.py` gains a frozen dataclass `DryRunPlan(levels, integrate)` and a function `compute_dry_run_plan(stories, *, repo_dir, branch_prefix, base_branch, max_concurrent) -> DryRunPlan` whose body is the current inline computation moved verbatim, with the `integration` import kept deferred inside the function (a top-level import would cycle `runs → integration → cli → runs`). `cli.py` adds both names to its existing `from agent_manager.runs import (...)` re-export block, and `cli.dry_run_payload` materializes `stories` once, calls `compute_dry_run_plan`, and builds `{"max_concurrent", "levels", "already_done", "integrate"}` in that order. `already_done_entries` stays in `cli.py`.

**Tech Stack:** Python, pytest, `uv`. No new dependencies.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-move-dry-run-payload-s-64babfa2/docs/superpowers/specs/task-move-dry-run-payload-s-64babfa2-design.md` (prepended above).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-move-dry-run-payload-s-64babfa2`, branch `m13/task-move-dry-run-payload-s-64babfa2`, cut from `m13/task-move-resume-helpers-and-1c21b3dd`. All paths below are relative to that worktree; run every command from it. Do not assume any other subtask's code (in particular `61a0d9be`'s repointing) exists on this branch.

## Global Constraints

- No CLI-observable change: same envelope, same exit codes, same `--dry-run` output, same key order at every level (top-level `max_concurrent, levels, already_done, integrate`; level row `level, concurrent, stories`; story row `story, title, root, subtasks[, merged_from]`; subtask row `id, title, status, branch, base`; integrate `branch, worktree, order`; order entries `story, tip`).
- `runs.py` gains no Typer import and never imports `cli` (existing tests `test_runs_source_imports_neither_typer_nor_cli_nor_launcher` and `test_importing_runs_loads_neither_typer_nor_cli` must stay green).
- The `integration` import in `runs.compute_dry_run_plan` stays deferred, inside the function.
- `max_concurrent` is required on `compute_dry_run_plan`; `DEFAULT_MAX_CONCURRENT` and its default stay on `cli.dry_run_payload`.
- `cli.dry_run_payload` keeps its exact signature.
- `already_done_entries` stays in `cli.py`.
- Internal state is a plain dataclass, not Pydantic (CLAUDE.md).
- Do not touch `bases.py`, `integration.py`, `orchestrate.py`, `default_runner_factory`, or cli.py's other deferred imports (sibling `61a0d9be`, exemption from `46244d0e`).
- Existing `tests/test_cli.py` dry-run tests pass unchanged. Nothing goes in `tests/e2e/`.
- Verification: `uv run pytest`. No typecheck, no lint.

## Review Focus

1. Stories passed as a one-shot iterator (not a list): both the levels and `already_done` must still see every story, because `cli.dry_run_payload` now hands `stories` to two consumers. Pinned in Task 1 (`test_the_dry_run_payload_accepts_a_one_shot_iterator`) and Task 2 (`test_compute_dry_run_plan_accepts_a_one_shot_iterator`).
2. Byte-identical rendering depends on dict key order, which `==` on dicts does not check: every row type must keep its key order. Pinned in Task 1 (`test_the_dry_run_payload_keeps_every_key_order`).
3. A blocker cycle between two populated stories must still be refused first, with `assert_no_blocker_cycles`'s arrow-trail message, from the new function too. Pinned in Task 2 (`test_compute_dry_run_plan_checks_for_blocker_cycles_first`).
4. A completely empty census (no stories at all) must give no levels, no already-done entries, and an Integrate plan with an empty order, not an error. Pinned in Task 1 (`test_an_empty_census_gives_an_empty_dry_run_payload`) and Task 2 (`test_compute_dry_run_plan_over_an_empty_census`).
5. Importing `runs` first in a fresh interpreter and then calling `compute_dry_run_plan` must work: the deferred `integration` import pulls in `cli`, which imports from the already-loaded `runs`, so no circular-import error. Pinned in Task 2 (`test_compute_dry_run_plan_runs_in_a_fresh_interpreter_that_imported_runs_first`).

---

## File Structure

- Modify `src/agent_manager/runs.py` — add `DryRunPlan` dataclass and `compute_dry_run_plan`; extend the module docstring; add `dataclass` and `census` imports (`census` imports only `models`, so no cycle).
- Modify `src/agent_manager/cli.py` — add `DryRunPlan` and `compute_dry_run_plan` to the `from agent_manager.runs import (...)` block (currently lines 51-67); replace the body and docstring of `dry_run_payload` (currently lines 776-862).
- Modify `tests/test_cli.py` — add three characterization tests after `test_the_dry_run_payload_defaults_to_four_lanes` (currently ends line 1097). Existing tests are not edited.
- Modify `tests/test_runs.py` — add direct tests for `compute_dry_run_plan`, the delegation structure test, and extend `MOVED_NAMES`.

---

### Task 1: Characterization guards for the dry-run envelope

These tests pin behavior the existing suite does not: key order, iterator input, and an empty census. They are written against the current code and are expected to PASS before any refactor; they are the safety net for Task 3.

**Files:**
- Modify: `tests/test_cli.py` (insert after `test_the_dry_run_payload_defaults_to_four_lanes`, currently lines 1093-1097)

**Interfaces:**
- Consumes: existing `cli.dry_run_payload(stories, *, repo_dir, branch_prefix, base_branch, max_concurrent=DEFAULT_MAX_CONCURRENT) -> dict[str, Any]`, and the existing test helpers `_plan_id`, `_plan_subtask`, `_plan_story`, `DRY_RUN_REPO` in `tests/test_cli.py`.
- Produces: nothing for later tasks beyond the guard tests themselves.

- [ ] **Step 1: Write the characterization tests**

Insert immediately after `test_the_dry_run_payload_defaults_to_four_lanes` in `tests/test_cli.py`:

```python
def test_the_dry_run_payload_keeps_every_key_order():
    """Review focus: `--dry-run` output must stay byte-for-byte identical, and
    dict equality ignores key order, so each row type's key order is pinned
    here -- a merged-root row included, since it alone carries `merged_from`."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id, b.id])
    done = _plan_story(4, [_plan_subtask(41, "done")], status="done")
    partial = _plan_story(5, [_plan_subtask(51, "done"), _plan_subtask(52)])

    payload = cli.dry_run_payload(
        [a, b, c, done, partial], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )

    assert list(payload) == ["max_concurrent", "levels", "already_done", "integrate"]
    for level in payload["levels"]:
        assert list(level) == ["level", "concurrent", "stories"]
        for row in level["stories"]:
            expected = ["story", "title", "root", "subtasks"]
            if row["story"] == c.id:
                expected.append("merged_from")
            assert list(row) == expected
            for subtask in row["subtasks"]:
                assert list(subtask) == ["id", "title", "status", "branch", "base"]
    assert [list(entry) for entry in payload["already_done"]] == [
        ["kind", "id", "title"],
        ["kind", "id", "title", "story"],
    ]
    assert list(payload["integrate"]) == ["branch", "worktree", "order"]
    assert payload["integrate"]["order"]
    for entry in payload["integrate"]["order"]:
        assert list(entry) == ["story", "tip"]


def test_the_dry_run_payload_accepts_a_one_shot_iterator():
    """Review focus: the stories feed both the levels and `already_done`, so a
    generator must be read once and seen by both."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    done = _plan_story(3, [_plan_subtask(31, "done")], status="done")
    stories = [a, b, done]

    from_list = cli.dry_run_payload(
        stories, repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )
    from_iterator = cli.dry_run_payload(
        iter(stories), repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )

    assert from_iterator == from_list
    assert from_iterator["already_done"] != []
    assert [len(level["stories"]) for level in from_iterator["levels"]] == [1, 1]


def test_an_empty_census_gives_an_empty_dry_run_payload():
    """Review focus: a milestone with no stories at all is not an error."""
    payload = cli.dry_run_payload([], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")

    assert payload == {
        "max_concurrent": 4,
        "levels": [],
        "already_done": [],
        "integrate": {
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [],
        },
    }
```

- [ ] **Step 2: Run the new tests against the current code**

Run: `uv run pytest tests/test_cli.py -k "keeps_every_key_order or one_shot_iterator or empty_census_gives_an_empty" -v`
Expected: 3 PASSED. These are characterization tests of the pre-refactor behavior; if any fails, stop — the test is wrong about current behavior, not the code, and must be corrected to match the current output before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: pin the dry-run envelope's key order, iterator input and empty census"
```

---

### Task 2: `runs.compute_dry_run_plan` and `runs.DryRunPlan`

**Files:**
- Modify: `src/agent_manager/runs.py` (imports at lines 13-22, module docstring at lines 1-11, append new code at end of file after `continuable_checkpoint`)
- Test: `tests/test_runs.py`

**Interfaces:**
- Consumes: `dag.assert_no_blocker_cycles`, `dag.compute_levels`, `dag.stack_bases`, `dag.story_root` (returns `RootPlan` with `.branch`, `.kind`, `.blockers`), `dag.subtask_branch`, `dag.remaining_subtasks`; `integration.integration_branch(branch_prefix) -> str`; `integration.merge_order(stories, branch_prefix, base_branch) -> list[tuple[StoryPlan, str]]`; `runs.worktree_for(repo_dir, branch) -> Path`.
- Produces:
  - `runs.DryRunPlan` — `@dataclass(frozen=True)` with fields, in this order, `levels: list[dict[str, Any]]` and `integrate: dict[str, Any]`.
  - `runs.compute_dry_run_plan(stories: Iterable[census.StoryPlan], *, repo_dir: Path, branch_prefix: str, base_branch: str, max_concurrent: int) -> DryRunPlan`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_runs.py`, change the import line `from agent_manager import runs` (line 17) to:

```python
from agent_manager import census, dag, runs
```

Then append to the end of `tests/test_runs.py`:

```python
def _plan_id(n: int) -> str:
    """A UUID-shaped card id whose short id is `n` in eight hex digits
    (`dag.short_id` refuses anything that is not 32 hex characters)."""
    return f"{n:08x}-0000-4000-8000-000000000000"


def _plan_subtask(n: int, status: str = "todo") -> census.SubtaskPlan:
    return census.SubtaskPlan(id=_plan_id(n), title=f"subtask {n}", status=status)


def _plan_story(
    n: int,
    subtasks: list[census.SubtaskPlan],
    *,
    status: str = "todo",
    blocked_by: tuple[str, ...] | list[str] = (),
) -> census.StoryPlan:
    return census.StoryPlan(
        id=_plan_id(n),
        title=f"story {n}",
        status=status,
        blocked_by=list(blocked_by),
        subtasks=list(subtasks),
    )


PLAN_REPO = Path("/repo")
"""`worktree_for` only joins onto the repo dir, so it need not exist."""


def _plan(stories, max_concurrent: int = 4) -> "runs.DryRunPlan":
    return runs.compute_dry_run_plan(
        stories,
        repo_dir=PLAN_REPO,
        branch_prefix="m3",
        base_branch="main",
        max_concurrent=max_concurrent,
    )


def test_dry_run_plan_is_a_plain_dataclass_of_levels_then_integrate():
    import dataclasses

    assert dataclasses.is_dataclass(runs.DryRunPlan)
    assert [field.name for field in dataclasses.fields(runs.DryRunPlan)] == [
        "levels",
        "integrate",
    ]
    assert runs.compute_dry_run_plan.__module__ == "agent_manager.runs"


def test_compute_dry_run_plan_computes_levels_bases_roots_and_the_integrate_plan():
    """Remaining subtasks only, bases from the full ordered list, a dependent
    rooted on its blocker's tip, and Integrate over every story with a tip."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    def branch(subtask: census.SubtaskPlan) -> str:
        return dag.subtask_branch("m3", subtask)

    assert _plan([a, b]) == runs.DryRunPlan(
        levels=[
            {
                "level": 0,
                "concurrent": 1,
                "stories": [
                    {
                        "story": a.id,
                        "title": "story 1",
                        "root": "main",
                        "subtasks": [
                            {
                                "id": _plan_id(12),
                                "title": "subtask 12",
                                "status": "todo",
                                "branch": branch(a.subtasks[1]),
                                "base": branch(a.subtasks[0]),
                            }
                        ],
                    }
                ],
            },
            {
                "level": 1,
                "concurrent": 1,
                "stories": [
                    {
                        "story": b.id,
                        "title": "story 2",
                        "root": branch(a.subtasks[-1]),
                        "subtasks": [
                            {
                                "id": _plan_id(21),
                                "title": "subtask 21",
                                "status": "todo",
                                "branch": branch(b.subtasks[0]),
                                "base": branch(a.subtasks[-1]),
                            }
                        ],
                    }
                ],
            },
        ],
        integrate={
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [
                {"story": a.id, "tip": branch(a.subtasks[-1])},
                {"story": b.id, "tip": branch(b.subtasks[-1])},
            ],
        },
    )


@pytest.mark.parametrize("bound, concurrent", [(1, [1, 1]), (2, [2, 1]), (10, [3, 1])])
def test_compute_dry_run_plan_bounds_each_level_by_the_passed_max_concurrent(
    bound, concurrent
):
    stories = [
        _plan_story(1, [_plan_subtask(11)]),
        _plan_story(2, [_plan_subtask(21)]),
        _plan_story(3, [_plan_subtask(31)]),
        _plan_story(4, [_plan_subtask(41)], blocked_by=[_plan_id(1)]),
    ]

    plan = _plan(stories, max_concurrent=bound)

    assert [level["concurrent"] for level in plan.levels] == concurrent


def test_compute_dry_run_plan_checks_for_blocker_cycles_first():
    """Review focus: the arrow trail is `assert_no_blocker_cycles`'s own
    message, which proves it ran before any geometry."""
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    with pytest.raises(dag.DependencyCycleError) as caught:
        _plan([a, b])

    assert f"#{a.id} -> #{b.id} -> #{a.id}" in str(caught.value)


def test_compute_dry_run_plan_marks_only_merged_roots_with_merged_from():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[b.id, "outside", a.id])

    plan = _plan([a, b, c])

    rows = {row["story"]: row for level in plan.levels for row in level["stories"]}
    assert rows[c.id]["root"] == "m3/base-00000003"
    assert rows[c.id]["merged_from"] == [b.id, a.id]
    assert list(rows[c.id]) == ["story", "title", "root", "subtasks", "merged_from"]
    assert "merged_from" not in rows[a.id]
    assert "merged_from" not in rows[b.id]


def test_compute_dry_run_plan_accepts_a_one_shot_iterator():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    assert _plan(iter([a, b])) == _plan([a, b])


def test_compute_dry_run_plan_over_an_empty_census():
    assert _plan([]) == runs.DryRunPlan(
        levels=[],
        integrate={
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [],
        },
    )


def test_compute_dry_run_plan_runs_in_a_fresh_interpreter_that_imported_runs_first():
    """Review focus: `integration` imports `cli`, which imports from `runs`, so
    `runs` may only import `integration` at call time. Importing `runs` alone
    and then calling the function must not hit a circular import."""
    code = (
        "import sys; from pathlib import Path; import agent_manager.runs as runs; "
        "assert 'agent_manager.cli' not in sys.modules; "
        "plan = runs.compute_dry_run_plan([], repo_dir=Path('/repo'), "
        "branch_prefix='m3', base_branch='main', max_concurrent=4); "
        "assert plan.levels == [], plan; "
        "assert plan.integrate['branch'] == 'm3-integrate', plan"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runs.py -k "dry_run_plan" -v`
Expected: FAIL — every new test errors with `AttributeError: module 'agent_manager.runs' has no attribute 'DryRunPlan'` or `... 'compute_dry_run_plan'` (the subprocess test fails with `CalledProcessError`).

- [ ] **Step 3: Update the imports of `src/agent_manager/runs.py`**

Replace the import block (lines 13-22):

```python
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from agent_manager import dag, models, store as store_module
```

with:

```python
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from agent_manager import census, dag, models, store as store_module
```

(The remaining four import lines, `runtime.engine`, `runtime.walk`, `store`, `workflow.task`, are unchanged.)

- [ ] **Step 4: Extend the module docstring of `src/agent_manager/runs.py`**

Replace lines 3-10:

```python
`cli.py` composes and renders; the plain pieces a run needs -- where its repo
is, what it is called, where its worktree goes, how a runner is made, which
gate parameters it binds, and how an interrupted run is picked back up (which
subtask is resumable, which attempts were orphaned, which checkpoint a relaunch
continues from, and the refusals those raise) -- live here so the modules
downstream of `cli` can reach them without importing the Typer app. `cli.py`
re-exports every name defined here, so `cli.X is runs.X`. This module never
imports `cli`.
```

with:

```python
`cli.py` composes and renders; the plain pieces a run needs -- where its repo
is, what it is called, where its worktree goes, how a runner is made, which
gate parameters it binds, how an interrupted run is picked back up (which
subtask is resumable, which attempts were orphaned, which checkpoint a relaunch
continues from, and the refusals those raise), and the `--dry-run` preview's
levels and Integrate plan -- live here so the modules downstream of `cli` can
reach them without importing the Typer app. `cli.py` re-exports every name
defined here, so `cli.X is runs.X`. This module never imports `cli` at load
time; `compute_dry_run_plan` imports `integration` (which still imports `cli`)
only when called.
```

- [ ] **Step 5: Implement `DryRunPlan` and `compute_dry_run_plan`**

Append to the end of `src/agent_manager/runs.py` (after `continuable_checkpoint`):

```python


@dataclass(frozen=True)
class DryRunPlan:
    """What `compute_dry_run_plan` works out; `cli.dry_run_payload` wraps it.

    `levels` is one row per dispatch level and `integrate` is the terminal
    phase's plan, both already in the exact shape and key order `--dry-run`
    prints. Plain data, not Pydantic: it never crosses a process boundary.
    """

    levels: list[dict[str, Any]]
    integrate: dict[str, Any]


def compute_dry_run_plan(
    stories: Iterable[census.StoryPlan],
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int,
) -> DryRunPlan:
    """O3's preview: dispatch levels with each subtask's branch and base, then Integrate.

    Pure over the census, and every derivation belongs to `dag`. The cycle
    check runs first because a cycle is what breaks the geometry, and
    `story_root`'s own guard misses a cycle between two populated stories.
    `stories_by_id` covers every story, closed ones included, so a story
    blocked by a done story still roots on that story's tip. A story's
    `subtasks` lists only what would be dispatched, but each `base` comes
    from `stack_bases` over the full ordered list, so a done first subtask
    still anchors the second. Each level row says how many of its stories
    would run together: `min(len(level), max_concurrent)`. The caller refuses
    a bound below 1 and supplies the default.

    A story's `root` is `dag.story_root(...).branch`. A story with two or more
    in-milestone blockers is not refused here: its `root` is its own merged
    base branch and its row gains `merged_from`, the blockers in `blocked_by`
    order. The key is absent for every other row. The real run still refuses
    such a story (`orchestrate.plan_levels`).

    `integrate` is the terminal phase's plan (Integrate addendum I6): the
    branch every tip is merged into, its worktree under `repo_dir`, and the
    merge order `integration.merge_order` gives -- every story with subtasks,
    done or not. `repo_dir` is only joined onto, never read.
    """
    # `integration` imports `cli` at load time, and `cli` imports this module
    # at load time, so importing it at the top of this module would be
    # circular. By call time all three are loaded.
    from agent_manager import integration

    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    levels = dag.compute_levels(stories)
    stories_by_id = {story.id: story for story in stories}
    level_rows: list[dict[str, Any]] = []
    for index, level in enumerate(levels):
        story_rows: list[dict[str, Any]] = []
        for story in level:
            bases = dag.stack_bases(story, stories_by_id, branch_prefix, base_branch)
            root = dag.story_root(story, stories_by_id, branch_prefix, base_branch)
            row: dict[str, Any] = {
                "story": story.id,
                "title": story.title,
                "root": root.branch,
                "subtasks": [
                    {
                        "id": subtask.id,
                        "title": subtask.title,
                        "status": subtask.status,
                        "branch": dag.subtask_branch(branch_prefix, subtask),
                        "base": bases[subtask.id],
                    }
                    for subtask in dag.remaining_subtasks(story)
                ],
            }
            if root.kind == "merged":
                row["merged_from"] = list(root.blockers)
            story_rows.append(row)
        level_rows.append(
            {
                "level": index,
                "concurrent": min(len(level), max_concurrent),
                "stories": story_rows,
            }
        )
    integrate_branch = integration.integration_branch(branch_prefix)
    return DryRunPlan(
        levels=level_rows,
        integrate={
            "branch": integrate_branch,
            "worktree": str(worktree_for(repo_dir, integrate_branch)),
            "order": [
                {"story": story.id, "tip": tip}
                for story, tip in integration.merge_order(
                    stories, branch_prefix, base_branch
                )
            ],
        },
    )
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_runs.py -v`
Expected: all PASS, including the pre-existing `test_runs_source_imports_neither_typer_nor_cli_nor_launcher` (the in-function import names `agent_manager` / `agent_manager.integration`, neither forbidden) and `test_importing_runs_loads_neither_typer_nor_cli` (the import is only executed at call time).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/runs.py tests/test_runs.py
git commit -m "feat(runs): add compute_dry_run_plan, the pure dry-run levels and Integrate plan"
```

---

### Task 3: `cli.dry_run_payload` delegates to `runs.compute_dry_run_plan`

**Files:**
- Modify: `src/agent_manager/cli.py` (re-export block currently lines 51-67; `dry_run_payload` currently lines 776-862)
- Test: `tests/test_runs.py`

**Interfaces:**
- Consumes: `runs.DryRunPlan` (fields `levels`, `integrate`) and `runs.compute_dry_run_plan(stories, *, repo_dir, branch_prefix, base_branch, max_concurrent) -> DryRunPlan` from Task 2; `cli.already_done_entries(stories: Sequence[census.StoryPlan]) -> list[dict[str, str]]` (unchanged, stays in `cli.py`).
- Produces: `cli.dry_run_payload` with its unchanged signature `(stories: Sequence[census.StoryPlan], *, repo_dir: Path, branch_prefix: str, base_branch: str, max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> dict[str, Any]`; `cli.compute_dry_run_plan is runs.compute_dry_run_plan` and `cli.DryRunPlan is runs.DryRunPlan`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_runs.py`, extend the `MOVED_NAMES` tuple (currently ending with `"continuable_checkpoint",`) so it reads:

```python
MOVED_NAMES = (
    "RUN_ID_TIME_FORMAT",
    "WORKTREE_PARTS",
    "CliError",
    "RepoDirError",
    "resolve_repo_dir",
    "mint_run_id",
    "worktree_for",
    "RunnerFactory",
    "gate_context",
    "UnknownRunError",
    "NotResumableError",
    "CheckpointMismatchError",
    "select_resumable",
    "orphan_attempts",
    "continuable_checkpoint",
    "DryRunPlan",
    "compute_dry_run_plan",
)
```

Then append to the end of `tests/test_runs.py`:

```python
def test_cli_dry_run_payload_delegates_the_plan_to_runs():
    """`cli.dry_run_payload` only assembles the envelope: it calls
    `compute_dry_run_plan`, makes no `dag` call and imports no `integration`.
    Checked on the function's code, not its docstring."""
    import textwrap

    from agent_manager import cli

    function = ast.parse(textwrap.dedent(inspect.getsource(cli.dry_run_payload))).body[0]
    body = function.body[1:] if ast.get_docstring(function) else function.body
    names = {
        node.id
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    }
    imports = [
        node
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert "compute_dry_run_plan" in names
    assert "already_done_entries" in names
    assert "dag" not in names
    assert "integration" not in names
    assert imports == []


def test_cli_dry_run_payload_is_the_plan_wrapped_in_the_envelope():
    from agent_manager import cli

    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    plan = _plan([a, b], max_concurrent=3)

    payload = cli.dry_run_payload(
        [a, b], repo_dir=PLAN_REPO, branch_prefix="m3", base_branch="main", max_concurrent=3
    )

    assert payload == {
        "max_concurrent": 3,
        "levels": plan.levels,
        "already_done": cli.already_done_entries([a, b]),
        "integrate": plan.integrate,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runs.py -k "moved_name or delegates_the_plan or wrapped_in_the_envelope" -v`
Expected: FAIL — `test_cli_re_exports_the_moved_name_as_the_same_object[DryRunPlan]` and `[compute_dry_run_plan]` fail with `AttributeError: module 'agent_manager.cli' has no attribute ...`, and `test_cli_dry_run_payload_delegates_the_plan_to_runs` fails on `assert "compute_dry_run_plan" in names`. (`test_cli_dry_run_payload_is_the_plan_wrapped_in_the_envelope` already passes — it is an equivalence guard, not the RED driver.)

- [ ] **Step 3: Re-export the new names from `cli.py`**

In `src/agent_manager/cli.py`, replace the re-export block (currently lines 51-67):

```python
from agent_manager.runs import (
    RUN_ID_TIME_FORMAT,
    WORKTREE_PARTS,
    CheckpointMismatchError,
    CliError,
    NotResumableError,
    RepoDirError,
    RunnerFactory,
    UnknownRunError,
    continuable_checkpoint,
    gate_context,
    mint_run_id,
    orphan_attempts,
    resolve_repo_dir,
    select_resumable,
    worktree_for,
)
```

with:

```python
from agent_manager.runs import (
    RUN_ID_TIME_FORMAT,
    WORKTREE_PARTS,
    CheckpointMismatchError,
    CliError,
    DryRunPlan,
    NotResumableError,
    RepoDirError,
    RunnerFactory,
    UnknownRunError,
    compute_dry_run_plan,
    continuable_checkpoint,
    gate_context,
    mint_run_id,
    orphan_attempts,
    resolve_repo_dir,
    select_resumable,
    worktree_for,
)
```

- [ ] **Step 4: Replace `cli.dry_run_payload`'s docstring and body**

Re-read `src/agent_manager/cli.py` around `def dry_run_payload(` first (line numbers drift). Replace the whole function, from `def dry_run_payload(` through its closing `    }` (currently lines 776-862), with:

```python
def dry_run_payload(
    stories: Sequence[census.StoryPlan],
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
    """O3's preview envelope: `runs.compute_dry_run_plan`'s levels and Integrate plan.

    The levels, bases, roots, `merged_from` rows, per-level `concurrent` and
    the Integrate plan are all computed by `compute_dry_run_plan` (which also
    refuses a blocker cycle first). This only reads `stories` once, echoes
    `max_concurrent` (defaulting to `DEFAULT_MAX_CONCURRENT`), adds
    `already_done_entries`, and returns the envelope with its keys in the
    order `--dry-run` prints them: `max_concurrent`, `levels`, `already_done`,
    `integrate`.
    """
    stories = list(stories)
    plan = compute_dry_run_plan(
        stories,
        repo_dir=repo_dir,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        max_concurrent=max_concurrent,
    )
    return {
        "max_concurrent": max_concurrent,
        "levels": plan.levels,
        "already_done": already_done_entries(stories),
        "integrate": plan.integrate,
    }
```

Note: the old in-function `from agent_manager import integration` inside `dry_run_payload` is deleted with the old body, since nothing in this function uses `integration` any more. Do not touch cli.py's other deferred imports (those are sibling `61a0d9be`'s). `worktree_for` stays in the re-export block because other `cli.py` code and `cli.worktree_for` callers use it.

- [ ] **Step 5: Run the targeted tests to verify they pass**

Run: `uv run pytest tests/test_runs.py tests/test_cli.py -k "dry_run or moved_name or delegates_the_plan or wrapped_in_the_envelope or key_order or one_shot_iterator or empty_census or four_lanes or DEFAULT_MAX_CONCURRENT or blocker" -v`
Expected: all PASS, including the unchanged pre-existing `tests/test_cli.py` dry-run tests and Task 1's characterization guards.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass (e2e excluded by the default `-m "not e2e"` addopts). No existing test in `tests/test_cli.py` was edited.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_runs.py
git commit -m "refactor(cli): dry_run_payload delegates the plan to runs.compute_dry_run_plan"
```
