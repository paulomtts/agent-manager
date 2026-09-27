<!-- task-pipeline: validated -->
# Root multi-blocker stories on a merged base in dag (card 1693e86e)

Subtask of e90a2247 ("Groundwork: the stop, an awaitable driver, multi-blocker roots"), milestone 7. Narrows Task 1.3 of `docs/superpowers/plans/2026-09-25-supervisor-tree.md` and `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §3 T7/T10, §4 (table and `RootPlan`), §8 (`--dry-run` row).

Note: the exploration findings handed to this stage were truncated mid-sentence at the test-tier placement rule. The tier assignments below were taken from the tier docstrings already in `tests/test_dag.py`/`tests/test_cli.py`/`tests/test_orchestrate.py` (design §14: pure helpers are unit tests; commands on real temp git + brd boards are Steps-tier fixtures), not from the missing text.

## Scope

In scope:

- `pyproject.toml`: add `grafo>=0.3.5` as a runtime dependency (`uv add "grafo>=0.3.5"`). No module imports grafo in this card; when it is imported later, only `orchestrate.py` may do so.
- `src/agent_manager/dag.py`:
  - New `@dataclass(frozen=True) class RootPlan` with `kind: Literal["base", "tip", "merged"]`, `branch: str` (the branch the story's first subtask builds on), `blockers: tuple[str, ...]` (in-milestone blockers, de-duplicated, in census order, i.e. the order they appear in `story.blocked_by` filtered to ids in `stories_by_id`).
  - New `base_branch_name(prefix, story) -> str` returning `f"{prefix}/base-{short_id(story.id)}"`.
  - `story_root(...)` returns a `RootPlan`: no in-milestone blockers gives `RootPlan("base", base_branch, ())`; exactly one gives `RootPlan("tip", <that blocker's story_tip>, (id,))`; two or more gives `RootPlan("merged", base_branch_name(prefix, story), (ids...))` and no longer raises `StackRootError`. The `seen`-based cycle backstop still raises `DependencyCycleError`.
  - `story_tip` (subtask-less fall-through) and `stack_bases` use `story_root(...).branch`; both still return plain strings / `dict[str, str]`.
  - `StackRootError` stays defined and importable (orchestrate raises it now).
- `src/agent_manager/cli.py` `dry_run_payload`: each story row's `"root"` is `RootPlan.branch`; when `kind == "merged"` the row gains `"merged_from": [blocker ids in census order]` (key absent otherwise). A merged root is not refused: the payload is returned and `am run --milestone ... --dry-run` exits 0 with `ok: true`. Cycles are still refused (`DependencyCycleError`).
- `src/agent_manager/orchestrate.py` `plan_levels`: a real run still refuses a merged root, as today, until Task 3.2. Because `dag` no longer raises, `plan_levels` must itself raise `dag.StackRootError` before anything is written when a planned story's root is `merged` — including the case where the root is reached through a subtask-less blocker whose own root is merged (today that also raised, via `story_tip` -> `story_root`). The message keeps today's shape: names the story `#<id>` and every in-milestone blocker `#<id>`, says a stack can only root on one parent branch, and suggests merging into `base_branch` or restructuring. Update the `plan_levels` docstring accordingly.

Out of scope (later subtasks / Task 3.2): `bases.py` merged-base builder, any grafo `Node`/`TreeExecutor` wiring, lane/supervisor code, dispatch behavior changes, StopSignal/checkpoint (364babde), `drive_subtask_async` (9b944409). `integration.py`'s `story_tip` use needs no change.

## Error paths

- Blocker cycle: `DependencyCycleError`, unchanged, from `assert_no_blocker_cycles` in both dry run and real run; `story_root`'s `seen` backstop still raises it.
- Two or more in-milestone blockers: dry run succeeds with `merged_from`; real run (`plan_levels` / `run_milestone`) raises `dag.StackRootError` naming both blockers, before any branch, worktree, run directory, or board write.
- Out-of-milestone blockers are ignored exactly as today (they neither count toward `merged` nor appear in `blockers`).

## Tests

Tier per the existing file docstrings (design §14): pure-function unit tier for `dag` functions, `dry_run_payload` and `plan_levels`; Steps tier (real temp git repo + real temp brd board, `@requires_git @requires_brd`) for the `am --dry-run` CLI test.

Test-helper note: `tests/test_dag.py`'s `_story(id, ...)` uses letter ids ("a", "b"), which `dag.short_id` rejects (needs 32 hex chars). Tests that hit `base_branch_name` must use UUID-shaped ids, e.g. a `_plan_id(n)`-style helper like the one in `tests/test_cli.py:840` / `tests/test_orchestrate.py:44`, or real UUIDs. There is no `story()` helper despite the plan's excerpt. This applies to the id of the BLOCKED story itself (the one whose `story_root` is asserted to be `merged`), not just to subtask ids: `base_branch_name` is computed from that story's own `id` even when a test only checks `.kind` and never reads `.branch`, so `_story("c", ["a", "b"], ...)` still raises inside `story_root`/`stack_bases` before any assertion runs. Every test below that puts a story into a `merged` root — the new `test_two_blockers_give_a_merged_root`, the rewritten `test_two_in_milestone_blockers_refuse_to_guess_a_root` (:463), and the rewritten `test_stack_bases_of_a_subtask_less_story_still_surfaces_a_root_error` (:517) — must give that blocked story a UUID-shaped id (a `_plan_id(n)`-style helper for story ids, distinct from `_gsub`'s subtask ids); its blockers' own ids do not need to change.

`tests/test_dag.py` (pure tier):

- New: `test_two_blockers_give_a_merged_root` — story C blocked by A and B (plus an outside id) gets `RootPlan(kind="merged", branch=f"{PREFIX}/base-{short_id(c.id)}", blockers=(a.id, b.id))`, blockers in census order.
- New: `base_branch_name` returns `<prefix>/base-<short id>`.
- New or updated: no blockers -> `kind="base"`, `branch=BASE`, `blockers=()`; one blocker -> `kind="tip"`, `branch` = blocker's tip, `blockers=(id,)`; a duplicated blocker is still one (`tip`).
- New: `stack_bases` of a two-blocker story roots its first subtask on the merged base branch.
- Rewrite `test_two_in_milestone_blockers_refuse_to_guess_a_root` (:463) into a merged-root assertion.
- Rewrite `test_stack_bases_of_a_subtask_less_story_still_surfaces_a_root_error` (:517): no longer raises; returns `{}` (a subtask-less story) and `story_root` for it is `merged`.
- Every existing `story_root(...)` comparison to a bare string compares `.branch` (or the full `RootPlan`).
- Existing cycle tests stay passing unchanged.

`tests/test_cli.py`:

- Pure tier: rewrite `test_the_dry_run_refuses_a_story_with_two_in_milestone_blockers` (:966) to assert the payload is returned, C's row has `root == f"m3/base-{short_id(c.id)}"` and `merged_from == [a.id, b.id]`, and rows with base/tip roots have no `merged_from` key. This test already builds story ids via `_plan_id`/`_plan_story`, so no id changes are needed here.
- Steps tier: rewrite `test_a_story_blocked_by_two_stories_is_an_envelope_naming_both` (:2645) into a success test: `am --dry-run` exits 0, `ok: true`, the joined story's row has `merged_from == [first, second]` and `root` naming its `base-` branch, and nothing is written (`_assert_nothing_written`, `_forbid_writes` kept). Real brd card ids from `_add_card` are already UUIDs, so no id changes are needed here either.

`tests/test_orchestrate.py` — must pass UNCHANGED (not edited by this card):

- `test_plan_levels_refuses_a_story_with_two_in_milestone_blockers` (:99, pure tier).
- `test_a_story_with_two_blockers_is_refused_before_anything_is_written` (~:1309, Steps tier, real `run_milestone`).

Whole default suite (`uv run pytest`, including `tests/e2e`) stays green.

---

# Root multi-blocker stories on a merged base — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dag.story_root` return a `RootPlan` that roots a story with two or more in-milestone blockers on its own merged base branch (instead of refusing), show that in the milestone dry run as `merged_from`, and keep the real run refusing it from `orchestrate.plan_levels` with today's message.

**Architecture:** `dag` gains a frozen `RootPlan(kind, branch, blockers)` and `base_branch_name(prefix, story)`. `story_root` returns a `RootPlan`; `story_tip` and `stack_bases` read `.branch` so their string outputs are unchanged. `cli.dry_run_payload` renders `root.branch` plus `merged_from` for a merged root. `orchestrate.plan_levels` now owns the refusal: before building any `PlannedStory`, it walks each pending story's root (following the same subtask-less fall-through `story_tip` follows) and raises `dag.StackRootError` if it meets a merged root.

**Tech Stack:** Python 3.12, `uv`, pytest (`uv run pytest`), Typer CLI, brd board fixtures for the Steps tier.

**Spec:** `docs/superpowers/specs/task-root-multi-blocker-1693e86e-design.md` (prepended verbatim above).

**Upstream truncation notice:** both the spec author's summary and the exploration findings handed to this planning stage were cut off by the harness (the exploration findings mid-sentence at "TEST-TIER PLACEMENT RULE (ste"). That truncation is itself evidence the upstream stages over-ran their brief. This plan does not guess the missing text: tier placement is taken from the spec's own Tests section (which read it from the tier docstrings at `tests/test_cli.py:1-13` and `tests/test_orchestrate.py:1-17`), and every file path and line below was read from the worktree.

**Branch/worktree:** `m7/task-root-multi-blocker-1693e86e` at `/home/paulomtts/Code/agent-manager/.claude/worktrees/m7/task-root-multi-blocker-1693e86e`. All paths below are relative to that worktree. Do not assume any other subtask's code beyond what is already on this branch.

## Global Constraints

- Dependency floor, verbatim: `grafo>=0.3.5`, added as a runtime dependency with `uv add "grafo>=0.3.5"`. No module imports grafo in this card; when it is imported later, only `orchestrate.py` may do so.
- Merged base branch name, verbatim: `f"{prefix}/base-{short_id(story.id)}"`.
- `RootPlan` fields, verbatim: `kind: Literal["base", "tip", "merged"]`, `branch: str`, `blockers: tuple[str, ...]`; `@dataclass(frozen=True)`.
- `blockers` / `merged_from` order: census order, i.e. the order they appear in `story.blocked_by` filtered to ids in `stories_by_id`, de-duplicated.
- `merged_from` key is present only when `kind == "merged"`.
- The dry run must exit 0 with `ok: true` for a merged root; cycles are still refused with `DependencyCycleError`.
- The real run refuses a merged root with `dag.StackRootError` before any branch, worktree, run directory or board write, naming the story and every in-milestone blocker as `#<id>`.
- `StackRootError` stays defined in `dag.py` and importable.
- `tests/test_orchestrate.py::test_plan_levels_refuses_a_story_with_two_in_milestone_blockers` and `tests/test_orchestrate.py::test_a_story_with_two_blockers_is_refused_before_anything_is_written` stay byte-for-byte unchanged and passing.
- Whole default suite `uv run pytest` (including `tests/e2e`) green at the end of every task.
- Out of scope: `bases.py`, grafo `Node`/`TreeExecutor`, lanes/supervisor, dispatch behavior, StopSignal/checkpoint, `drive_subtask_async`.

## Review Focus

1. A story whose single blocker has no subtasks and is itself blocked by two in-milestone stories: the real run must still refuse (today it does via `story_tip` -> `story_root`), naming the two-blocker story and both its blockers. Test: `test_plan_levels_refuses_a_story_rooted_through_a_subtask_less_story_on_two_blockers` in Task 2.
2. A blocker id listed twice among two distinct in-milestone blockers (`["a", "b", "a"]`): the root is merged with `blockers == ("a", "b")`, not three entries. Test: `test_a_duplicated_blocker_in_a_merged_root_is_listed_once` in Task 2.
3. Out-of-milestone ids mixed between in-milestone blockers: they must not appear in `blockers` or `merged_from`. Tests: `test_two_blockers_give_a_merged_root` (dag) and `test_the_dry_run_roots_a_story_with_two_in_milestone_blockers_on_a_merged_base` (cli) in Task 2.
4. Blockers listed in an order that is not id order (`["b", "a"]`): order must follow `blocked_by`, not be sorted. Test: the rewritten `test_two_in_milestone_blockers_root_on_a_merged_base_in_blocked_by_order` in Task 2.
5. Three in-milestone blockers, one of them already `done`: a done blocker still counts (done is not landed), so all three appear. Test: `test_three_blockers_one_done_all_count_toward_the_merged_root` in Task 2.

Note on scope of test edits: the spec forbids editing the two named `tests/test_orchestrate.py` tests. Review Focus item 1 has no other pure-tier home than `tests/test_orchestrate.py` (it exercises `plan_levels`), so Task 2 APPENDS one new test there, directly after `test_plan_levels_refuses_a_blocker_cycle_before_any_geometry`, and leaves every existing test in that file untouched.

---

### Task 1: Add the grafo dependency, `RootPlan` and `base_branch_name`

Pure additions: nothing existing changes behavior, so the whole suite stays green.

**Files:**
- Modify: `pyproject.toml:7-11` (via `uv add`; also updates `uv.lock`)
- Modify: `src/agent_manager/dag.py:30-33` (imports), `:238-244` (insert `RootPlan` after `StackRootError`), `:247-256` (insert `base_branch_name` after `subtask_branch`)
- Test: `tests/test_dag.py` (pure tier)

**Interfaces:**
- Consumes: `dag.short_id(card_id) -> str` (raises `ValueError("not a card id: ...")`), `census.StoryPlan`.
- Produces:
  - `dag.RootPlan` — `@dataclass(frozen=True)`, fields `kind: Literal["base", "tip", "merged"]`, `branch: str`, `blockers: tuple[str, ...]`.
  - `dag.base_branch_name(prefix: str, story: StoryPlan) -> str` returning `f"{prefix}/base-{short_id(story.id)}"`.
  - Test helper `tests/test_dag.py::_story_id(n: int) -> str` returning `f"{n:08x}-0000-4000-8000-000000000000"`.

- [ ] **Step 1: Add the dependency**

Run (from the worktree root):

```bash
uv add "grafo>=0.3.5"
```

Expected: `pyproject.toml`'s `dependencies` list now contains `"grafo>=0.3.5",` alongside `typer`, `pydantic`, `pygents`, and `uv.lock` is updated. Confirm it resolves:

```bash
uv run python -c "import importlib.metadata as m; print(m.version('grafo'))"
```

Expected: prints a version `>= 0.3.5`. Do not add any `import grafo` anywhere.

- [ ] **Step 2: Write the failing tests**

In `tests/test_dag.py`, add `import dataclasses` above `import pytest` (line 1), and extend the `from agent_manager.dag import (...)` block (lines 4-23) so it reads:

```python
import dataclasses

import pytest

from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.dag import (
    DependencyCycleError,
    RootPlan,
    StackRootError,
    assert_no_blocker_cycles,
    base_branch_name,
    compute_integrate_levels,
    compute_levels,
    is_story_closed,
    is_subtask_done,
    ref_matches_card,
    remaining_subtasks,
    short_id,
    slugify,
    stack_bases,
    story_root,
    story_tip,
    subtask_branch,
    task_branch,
    task_stem,
    topological_levels,
)
```

Then, directly after `_by_id` (currently `tests/test_dag.py:364-365`), add the helper and the tests:

```python
def _story_id(n: int) -> str:
    """A UUID-shaped STORY id whose short id is ``n`` in eight hex digits.

    ``base_branch_name`` goes through ``short_id``, which refuses the letter
    ids ``_story`` uses elsewhere, so a story that roots on a merged base needs
    a real-shaped id of its own. Distinct from ``_gsub``'s subtask ids.
    """
    return f"{n:08x}-0000-4000-8000-000000000000"


def test_base_branch_name_is_the_prefix_then_base_then_the_short_id():
    c = _story(_story_id(0xC), subtasks=[])
    assert base_branch_name(PREFIX, c) == "m3/base-0000000c"
    assert base_branch_name(PREFIX, c) == f"{PREFIX}/base-{short_id(c.id)}"


def test_base_branch_name_refuses_a_story_whose_id_is_not_a_card_id():
    with pytest.raises(ValueError, match="not a card id"):
        base_branch_name(PREFIX, _story("c", subtasks=[]))


def test_a_root_plan_is_frozen_and_compares_by_value():
    root = RootPlan(kind="base", branch="main", blockers=())
    assert root == RootPlan("base", "main", ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        root.branch = "other"  # type: ignore[misc]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'RootPlan' from 'agent_manager.dag'`.

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/dag.py`, replace the import block (lines 30-33):

```python
import re
from collections.abc import Mapping

from agent_manager.census import StoryPlan, SubtaskPlan
```

with:

```python
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from agent_manager.census import StoryPlan, SubtaskPlan
```

Directly after the `StackRootError` class (ends at line 244), insert:

```python


@dataclass(frozen=True)
class RootPlan:
    """Where a story's stack starts, and why.

    ``kind`` is ``"base"`` when the story has no in-milestone blocker
    (``branch`` is the milestone's base branch), ``"tip"`` when it has exactly
    one (``branch`` is that blocker's tip), and ``"merged"`` when it has two or
    more (``branch`` is the story's own ``base_branch_name``, which a later
    card builds by merging the blockers' tips). ``blockers`` are the
    in-milestone blockers, de-duplicated, in the order the story's
    ``blocked_by`` lists them. Internal state, so a dataclass (CLAUDE.md).
    """

    kind: Literal["base", "tip", "merged"]
    branch: str
    blockers: tuple[str, ...]
```

Directly after `subtask_branch` (ends at line 256, `return task_branch(prefix, subtask)`), insert:

```python


def base_branch_name(prefix: str, story: StoryPlan) -> str:
    """The branch a multi-blocker story's merged base lives on.

    Keyed on the story's short id like every derived name, so a title edit
    cannot orphan it; ``short_id`` refuses anything that is not a card id.
    """
    return f"{prefix}/base-{short_id(story.id)}"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS (all tests, including the three new ones).

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. Nothing existing changed behavior.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): add RootPlan, base_branch_name and the grafo dependency"
```

---

### Task 2: `story_root` returns a `RootPlan`; dry run shows merged roots; the real run keeps refusing them

One coherent flip: once `story_root` stops raising, `cli.dry_run_payload` and `orchestrate.plan_levels` must change in the same commit, or the suite goes red (the dry run would embed a `RootPlan` object, and the real run would silently root a two-blocker story on a base branch nobody built). A reviewer cannot accept the dag change while rejecting either consumer, so they are one task.

**Files:**
- Modify: `src/agent_manager/dag.py` — module docstring line 22, `StackRootError` docstring (`:238-244` before Task 1's insert), `story_tip` (`:259-274`), `story_root` (`:277-315`), `stack_bases` (`:318-337`). Line numbers shift by Task 1's inserts; match on the code shown.
- Modify: `src/agent_manager/cli.py:866-930` (`dry_run_payload` docstring and story row)
- Modify: `src/agent_manager/orchestrate.py:248-275` (`plan_levels`; new private helpers directly above it)
- Test: `tests/test_dag.py` (pure tier), `tests/test_cli.py:969-978` (pure tier), `tests/test_cli.py:2849-2868` (Steps tier), `tests/test_orchestrate.py` after `:116` (pure tier, one appended test)

**Interfaces:**
- Consumes (from Task 1): `dag.RootPlan(kind, branch, blockers)`, `dag.base_branch_name(prefix, story) -> str`, test helper `_story_id(n) -> str`.
- Produces:
  - `dag.story_root(story, stories_by_id, prefix, base_branch, seen=None) -> RootPlan` (no longer raises `StackRootError`; still raises `DependencyCycleError`).
  - `dag.story_tip(...) -> str` and `dag.stack_bases(...) -> dict[str, str]`, unchanged signatures and return types.
  - Dry-run story row: `{"story", "title", "root": str, "subtasks": [...], "merged_from": list[str] (only when merged)}`.
  - `orchestrate._merged_root_behind(story, stories_by_id, branch_prefix, base_branch) -> tuple[census.StoryPlan, dag.RootPlan] | None` and `orchestrate._merged_root_error(story, root, base_branch) -> dag.StackRootError` (private).

- [ ] **Step 1: Update the existing bare-string `story_root` comparisons in `tests/test_dag.py`**

Make each replacement exactly (old -> new):

`test_a_story_with_no_blockers_roots_on_the_base_branch`:
```python
    assert story_root(a, _by_id(a), PREFIX, BASE) == "main"
```
->
```python
    assert story_root(a, _by_id(a), PREFIX, BASE) == RootPlan("base", "main", ())
```
Apply the same replacement in `test_a_story_blocked_only_outside_the_milestone_roots_on_the_base_branch` and `test_a_missing_blocked_by_is_read_as_no_blockers` (each has the identical line; use the Edit tool once per test with enough surrounding context to be unique).

`test_one_in_milestone_blocker_roots_on_that_blockers_last_subtask`:
```python
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == "m3/task-a2-aaaa0002"
```
->
```python
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == RootPlan(
        "tip", "m3/task-a2-aaaa0002", ("a",)
    )
```

`test_external_blockers_beside_one_in_milestone_blocker_are_ignored`:
```python
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == "m3/task-a1-aaaa0001"
```
->
```python
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == RootPlan(
        "tip", "m3/task-a1-aaaa0001", ("a",)
    )
```

`test_a_done_blocker_still_yields_its_tip_because_done_is_not_landed`:
```python
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == "m3/task-a2-aaaa0002"
```
->
```python
    assert story_root(b, _by_id(a, b), PREFIX, BASE).branch == "m3/task-a2-aaaa0002"
```

`test_a_subtask_less_blocker_falls_through_to_its_own_root`:
```python
    assert story_root(c, stories, PREFIX, BASE) == "m3/task-a2-aaaa0002"
```
->
```python
    assert story_root(c, stories, PREFIX, BASE) == RootPlan(
        "tip", "m3/task-a2-aaaa0002", ("b",)
    )
```

`test_repeated_top_level_calls_each_get_a_fresh_seen`:
```python
        assert story_root(c, stories, PREFIX, BASE) == "m3/task-a1-aaaa0001"
```
->
```python
        assert story_root(c, stories, PREFIX, BASE).branch == "m3/task-a1-aaaa0001"
```

`test_a_blocker_listed_twice_counts_once`:
```python
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == "m3/task-a1-aaaa0001"
```
->
```python
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == RootPlan(
        "tip", "m3/task-a1-aaaa0001", ("a",)
    )
```

Leave every `story_tip(...)` comparison, both cycle tests (`test_the_seen_guard_stops_a_cycle_between_subtask_less_stories`, `test_a_pre_populated_seen_containing_the_story_raises`) and `test_a_stack_root_error_is_a_value_error` unchanged.

- [ ] **Step 2: Rewrite the two refusal tests in `tests/test_dag.py` and add the new merged-root tests**

Replace the whole of `test_two_in_milestone_blockers_refuse_to_guess_a_root` (currently `tests/test_dag.py:463-475`) with:

```python
def test_two_in_milestone_blockers_root_on_a_merged_base_in_blocked_by_order():
    """No longer refused: the story roots on its own merged base, and the
    blockers keep the order ``blocked_by`` lists them, not id order."""
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story(_story_id(0xC), ["b", "outside", "a"], subtasks=[_gsub("c1", "cccc0001")])
    root = story_root(c, _by_id(a, b, c), PREFIX, BASE)
    assert root == RootPlan("merged", "m3/base-0000000c", ("b", "a"))


def test_two_blockers_give_a_merged_root():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story(_story_id(0xC), ["a", "outside", "b"], subtasks=[_gsub("c1", "cccc0001")])
    assert story_root(c, _by_id(a, b, c), PREFIX, BASE) == RootPlan(
        kind="merged", branch=f"{PREFIX}/base-{short_id(c.id)}", blockers=("a", "b")
    )


def test_a_duplicated_blocker_in_a_merged_root_is_listed_once():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story(_story_id(0xC), ["a", "b", "a"], subtasks=[_gsub("c1", "cccc0001")])
    assert story_root(c, _by_id(a, b, c), PREFIX, BASE).blockers == ("a", "b")


def test_three_blockers_one_done_all_count_toward_the_merged_root():
    """Done is not landed, so a done blocker still has to be merged in."""
    a = _story("a", status="done", subtasks=[_gsub("a1", "aaaa0001", "done")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    d = _story("d", subtasks=[_gsub("d1", "dddd0001")])
    c = _story(_story_id(0xC), ["a", "b", "d"], subtasks=[_gsub("c1", "cccc0001")])
    assert story_root(c, _by_id(a, b, d, c), PREFIX, BASE) == RootPlan(
        "merged", "m3/base-0000000c", ("a", "b", "d")
    )
```

Replace the whole of `test_stack_bases_of_a_subtask_less_story_still_surfaces_a_root_error` (currently `tests/test_dag.py:517-522`) with:

```python
def test_a_subtask_less_story_on_two_blockers_has_no_bases_and_its_merged_base_as_tip():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story(_story_id(0xC), ["a", "b"], subtasks=[])
    stories = _by_id(a, b, c)
    assert stack_bases(c, stories, PREFIX, BASE) == {}
    assert story_root(c, stories, PREFIX, BASE).kind == "merged"
    assert story_tip(c, stories, PREFIX, BASE) == "m3/base-0000000c"


def test_stack_bases_of_a_two_blocker_story_root_its_first_subtask_on_the_merged_base():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c1, c2 = _gsub("c1", "cccc0001"), _gsub("c2", "cccc0002")
    c = _story(_story_id(0xC), ["a", "b"], subtasks=[c1, c2])
    assert stack_bases(c, _by_id(a, b, c), PREFIX, BASE) == {
        c1.id: "m3/base-0000000c",
        c2.id: "m3/task-c1-cccc0001",
    }
```

- [ ] **Step 3: Run the dag tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: FAIL. The updated comparisons fail with `AssertionError` (`'main' == RootPlan(...)` etc., since `story_root` still returns a `str`); the merged-root tests fail with `StackRootError: dag: story #0000000c-... is blocked by 2 stories ...`. The `.branch` comparisons fail with `AttributeError: 'str' object has no attribute 'branch'`.

- [ ] **Step 4: Rewrite the pure-tier dry-run refusal test in `tests/test_cli.py`**

Replace the whole of `test_the_dry_run_refuses_a_story_with_two_in_milestone_blockers` (`tests/test_cli.py:969-978`) with:

```python
def test_the_dry_run_roots_a_story_with_two_in_milestone_blockers_on_a_merged_base():
    """Not refused: the joined story's root is its own merged base branch and
    `merged_from` names its in-milestone blockers in `blocked_by` order, an
    outside id left out. Rows rooted on the base or on one tip have no
    `merged_from` key."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(
        3, [_plan_subtask(31), _plan_subtask(32)], blocked_by=[a.id, "outside", b.id]
    )
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[a.id])

    payload = cli.dry_run_payload(
        [a, b, c, d], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )

    rows = {row["story"]: row for level in payload["levels"] for row in level["stories"]}
    assert rows[c.id]["root"] == f"m3/base-{dag.short_id(c.id)}" == "m3/base-00000003"
    assert rows[c.id]["merged_from"] == [a.id, b.id]
    assert [row["base"] for row in rows[c.id]["subtasks"]] == [
        "m3/base-00000003",
        dag.subtask_branch("m3", c.subtasks[0]),
    ]
    assert rows[a.id]["root"] == rows[b.id]["root"] == "main"
    assert rows[d.id]["root"] == dag.subtask_branch("m3", a.subtasks[-1])
    for story in (a, b, d):
        assert "merged_from" not in rows[story.id]
    # The row survives the one-line JSON render untouched.
    rendered = json.loads(cli.render(cli.ok_envelope(payload)))["data"]
    assert rendered == payload
```

- [ ] **Step 5: Rewrite the Steps-tier dry-run refusal test in `tests/test_cli.py`**

Replace the whole of `test_a_story_blocked_by_two_stories_is_an_envelope_naming_both` (`tests/test_cli.py:2849-2868`, decorators included) with:

```python
@requires_git
@requires_brd
def test_a_story_blocked_by_two_stories_dry_runs_on_a_merged_base(project, monkeypatch):
    milestone = _add_card(project, "Milestone 8: diamond")
    first = _add_card(project, "Story one", milestone)
    second = _add_card(project, "Story two", milestone)
    joined = _add_card(project, "Story three", milestone)
    for story in (first, second, joined):
        _add_card(project, f"only subtask of {story}", story)
    _block(project, joined, first)
    _block(project, joined, second)
    # `merged_from` follows the joined story's `blocked_by` as brd reports it,
    # which the census copies through untouched.
    joined_node = next(
        node for node in board.tree(milestone, repo_dir=project).children if node.id == joined
    )
    census_order = [dep for dep in joined_node.blocked_by if dep in (first, second)]
    assert sorted(census_order) == sorted([first, second])
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    result = _dry_run(project, milestone)

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    rows = {
        row["story"]: row for level in envelope["data"]["levels"] for row in level["stories"]
    }
    assert rows[joined]["merged_from"] == census_order
    assert rows[joined]["root"] == f"m2/base-{dag.short_id(joined)}"
    assert rows[joined]["subtasks"][0]["base"] == rows[joined]["root"]
    assert "merged_from" not in rows[first]
    assert "merged_from" not in rows[second]
    _assert_nothing_written(project, porcelain_before)
```

- [ ] **Step 6: Run the cli tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "merged_base" -v`
Expected: FAIL. The pure test raises `agent_manager.dag.StackRootError` out of `dry_run_payload`; the Steps test fails `assert result.exit_code == 0` (exit code is `cli.EXIT_ERROR`, output is an `ok: false` `StackRootError` envelope). If `brd` or `git` is missing locally the Steps test is skipped by `@requires_brd`/`@requires_git`; it must still run in the verification environment.

- [ ] **Step 7: Append the subtask-less fall-through refusal test to `tests/test_orchestrate.py`**

Insert directly after `test_plan_levels_refuses_a_blocker_cycle_before_any_geometry` (ends at `tests/test_orchestrate.py:116`), changing no existing test:

```python


def test_plan_levels_refuses_a_story_rooted_through_a_subtask_less_story_on_two_blockers():
    """A subtask-less story blocked by two stories has a merged root, and a
    story it blocks falls through to that root. Until merged bases are built
    (Task 3.2), the real run refuses it, naming the two-blocker story and
    both of its blockers."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    joined = _plan_story(3, [], blocked_by=[a.id, b.id])
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[joined.id])

    with pytest.raises(dag.StackRootError) as caught:
        orchestrate.plan_levels([a, b, joined, d], branch_prefix="m3", base_branch="main")

    message = str(caught.value)
    assert f"#{joined.id}" in message
    assert f"#{a.id}" in message
    assert f"#{b.id}" in message
    assert "ONE parent branch" in message
```

- [ ] **Step 8: Run it to confirm it pins today's behavior**

Run: `uv run pytest tests/test_orchestrate.py -k "refuse" -v`
Expected: PASS for every selected test, including `test_plan_levels_refuses_a_story_with_two_in_milestone_blockers`, `test_a_story_with_two_blockers_is_refused_before_anything_is_written` and the new one. This new test is a regression guard, not a RED: today `dag.stack_bases(d)` raises through `story_tip(joined)` -> `story_root(joined)`. Step 10 shows it turning red once `dag` stops raising, which is what proves the orchestrate guard in Step 11 is needed.

- [ ] **Step 9: Implement the `dag` flip**

In `src/agent_manager/dag.py`:

Module docstring, replace:
```python
(``subtask_branch``, ``story_tip``, ``story_root``, ``stack_bases``), all
```
with:
```python
(``subtask_branch``, ``base_branch_name``, ``story_tip``, ``story_root`` and
its ``RootPlan``, ``stack_bases``), all
```

`StackRootError` docstring, replace:
```python
class StackRootError(ValueError):
    """A story has no single parent branch for its stack to root on.

    Subclasses ``ValueError`` because ``ValueError`` is already in
```
with:
```python
class StackRootError(ValueError):
    """A story has no single parent branch for its stack to root on.

    ``story_root`` no longer raises it: a multi-blocker story gets a
    ``"merged"`` ``RootPlan``. ``orchestrate.plan_levels`` raises it for such
    a story on a real run until merged bases are built.

    Subclasses ``ValueError`` because ``ValueError`` is already in
```

In `story_tip`, replace:
```python
    if story.subtasks:
        return subtask_branch(prefix, story.subtasks[-1])
    return story_root(story, stories_by_id, prefix, base_branch, seen)
```
with:
```python
    if story.subtasks:
        return subtask_branch(prefix, story.subtasks[-1])
    return story_root(story, stories_by_id, prefix, base_branch, seen).branch
```

Replace the whole `story_root` function with:

```python
def story_root(
    story: StoryPlan,
    stories_by_id: Mapping[str, StoryPlan],
    prefix: str,
    base_branch: str,
    seen: set[str] | None = None,
) -> RootPlan:
    """Where a story's stack starts: the base, one blocker's tip, or a merged base.

    No in-milestone blocker roots on ``base_branch``; exactly one roots on that
    blocker's tip; two or more root on the story's own ``base_branch_name``,
    which a later card builds by merging every blocker's tip. A blocker marked
    ``done`` still counts and still yields its tip: done does not mean its code
    landed anywhere. ``assert_no_blocker_cycles`` must run first; the ``seen``
    guard here is only a backstop, because ``story_tip`` returns immediately
    for a story with subtasks, so a cycle between two populated stories never
    recurses back to trip it.
    """
    seen = set() if seen is None else seen
    if story.id in seen:
        raise DependencyCycleError(
            f"dag: dependency cycle reached story #{story.id} while computing its stack root"
        )
    seen.add(story.id)
    # Only blockers inside this milestone can be stacked on; anything else is
    # external work whose branch this run knows nothing about.
    # A blocker listed twice is still one blocker.
    blockers = tuple(
        dep for dep in dict.fromkeys(story.blocked_by or []) if dep in stories_by_id
    )
    if not blockers:
        return RootPlan("base", base_branch, ())
    if len(blockers) > 1:
        return RootPlan("merged", base_branch_name(prefix, story), blockers)
    tip = story_tip(stories_by_id[blockers[0]], stories_by_id, prefix, base_branch, seen)
    return RootPlan("tip", tip, blockers)
```

In `stack_bases`, replace the docstring sentence and the root line:
```python
    first subtask still anchors the second. The root is computed even for a
    story with no subtasks, so its errors surface. Run
    ``assert_no_blocker_cycles`` first.
    """
    root = story_root(story, stories_by_id, prefix, base_branch)
```
with:
```python
    first subtask still anchors the second. The root is computed even for a
    story with no subtasks, so its errors surface. Run
    ``assert_no_blocker_cycles`` first.
    """
    root = story_root(story, stories_by_id, prefix, base_branch).branch
```

- [ ] **Step 10: Run the dag tests (pass) and the orchestrate refusals (now fail)**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS.

Run: `uv run pytest tests/test_orchestrate.py -k "refuse" -v`
Expected: FAIL for `test_plan_levels_refuses_a_story_with_two_in_milestone_blockers`, `test_a_story_with_two_blockers_is_refused_before_anything_is_written` (Steps tier; skipped only if `brd`/`git` are absent) and `test_plan_levels_refuses_a_story_rooted_through_a_subtask_less_story_on_two_blockers`, each with `Failed: DID NOT RAISE <class 'agent_manager.dag.StackRootError'>`. `test_plan_levels_refuses_a_blocker_cycle_before_any_geometry` and any other selected test still PASS.

- [ ] **Step 11: Implement the refusal in `orchestrate.plan_levels`**

In `src/agent_manager/orchestrate.py`, insert these two helpers directly above `def plan_levels(` (line 248):

```python
def _merged_root_behind(
    story: census.StoryPlan,
    stories_by_id: dict[str, census.StoryPlan],
    branch_prefix: str,
    base_branch: str,
) -> tuple[census.StoryPlan, dag.RootPlan] | None:
    """The merged root this story's stack would build on, and whose it is, or None.

    A story's own root can be merged, or its single blocker can have no
    subtasks and fall through to a root that is merged -- the same fall-through
    `dag.story_tip` takes. Only that path is followed. The cycle check has
    already run, so the walk ends.
    """
    current = story
    while True:
        root = dag.story_root(current, stories_by_id, branch_prefix, base_branch)
        if root.kind == "merged":
            return current, root
        if root.kind == "base":
            return None
        blocker = stories_by_id[root.blockers[0]]
        if blocker.subtasks:
            return None
        current = blocker


def _merged_root_error(
    story: census.StoryPlan, root: dag.RootPlan, base_branch: str
) -> dag.StackRootError:
    """Today's refusal, word for word, for a story whose root would be merged."""
    listed = ", ".join(f"#{dep}" for dep in root.blockers)
    return dag.StackRootError(
        f"dag: story #{story.id} is blocked by {len(root.blockers)} stories ({listed}), "
        "and a stack can only root on ONE parent branch. Merge those blockers into "
        f"{base_branch} first, or restructure the dependencies so this story has "
        "a single blocker."
    )


```

Then replace the whole `plan_levels` function with:

```python
def plan_levels(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[list[PlannedStory]]:
    """Dispatch levels with each pending story's bases and tip, derived before any write.

    The same composition as `cli.dry_run_payload`: the cycle check runs first,
    because a cycle is what breaks the geometry, and `stories_by_id` covers
    every story, done ones included, so a story blocked by a done story still
    roots on that story's tip. A story whose root is `dag.RootPlan` kind
    `"merged"` -- two or more in-milestone blockers, directly or through a
    subtask-less blocker -- raises `dag.StackRootError` here, before any
    geometry is returned: the dry run shows a merged root, but nothing builds
    merged bases yet (Task 3.2).
    """
    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    stories_by_id = {story.id: story for story in stories}
    planned: list[list[PlannedStory]] = []
    for index, level in enumerate(dag.compute_levels(stories)):
        for story in level:
            merged = _merged_root_behind(story, stories_by_id, branch_prefix, base_branch)
            if merged is not None:
                raise _merged_root_error(*merged, base_branch)
        planned.append(
            [
                PlannedStory(
                    story=story,
                    level=index,
                    bases=dag.stack_bases(story, stories_by_id, branch_prefix, base_branch),
                    tip=dag.story_tip(story, stories_by_id, branch_prefix, base_branch),
                )
                for story in level
            ]
        )
    return planned
```

- [ ] **Step 12: Run the orchestrate tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: PASS, including the two unchanged refusal tests and the appended one.

- [ ] **Step 13: Implement the dry-run row in `cli.dry_run_payload`**

In `src/agent_manager/cli.py`, in the `dry_run_payload` docstring, replace:
```python
    `min(len(level), max_concurrent)`. The caller refuses a bound below 1.
```
with:
```python
    `min(len(level), max_concurrent)`. The caller refuses a bound below 1.

    A story's `root` is `dag.story_root(...).branch`. A story with two or more
    in-milestone blockers is not refused here: its `root` is its own merged
    base branch and its row gains `merged_from`, the blockers in `blocked_by`
    order. The key is absent for every other row. The real run still refuses
    such a story (`orchestrate.plan_levels`).
```

Replace the story-row loop body:
```python
        for story in level:
            bases = dag.stack_bases(story, stories_by_id, branch_prefix, base_branch)
            story_rows.append(
                {
                    "story": story.id,
                    "title": story.title,
                    "root": dag.story_root(
                        story, stories_by_id, branch_prefix, base_branch
                    ),
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
            )
```
with:
```python
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
```

- [ ] **Step 14: Run the cli tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, including both rewritten `merged_base` tests and the unchanged `test_the_dry_run_payload_lists_remaining_subtasks_on_full_list_bases` / `test_the_dry_run_payload_keeps_census_order_across_and_within_levels` / `test_the_dry_run_checks_for_blocker_cycles_before_any_geometry`.

- [ ] **Step 15: Confirm grafo is still imported nowhere**

Use Grep for `grafo` over `src/` and `tests/`.
Expected: no matches (the only mentions are in `pyproject.toml` and `uv.lock`).

- [ ] **Step 16: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, whole default suite including `tests/e2e` (the paid `-m e2e` test stays deselected by `addopts`).

- [ ] **Step 17: Commit**

```bash
git add src/agent_manager/dag.py src/agent_manager/cli.py src/agent_manager/orchestrate.py tests/test_dag.py tests/test_cli.py tests/test_orchestrate.py
git commit -m "feat(dag): root multi-blocker stories on a merged base; dry run shows merged_from, real run still refuses"
```

---

## Self-Review

- Spec coverage: grafo dependency (Task 1 Step 1); `RootPlan` and `base_branch_name` (Task 1); `story_root` returning `RootPlan` with base/tip/merged and the `seen` backstop kept (Task 2 Step 9, existing cycle tests unchanged); `story_tip`/`stack_bases` using `.branch` (Task 2 Step 9); `StackRootError` kept importable (untouched class, `test_a_stack_root_error_is_a_value_error` kept); dry-run `root`/`merged_from`/exit 0 (Task 2 Steps 4, 5, 13); real-run refusal incl. subtask-less fall-through, today's message shape, docstring updated (Task 2 Steps 7, 11); every spec-listed test rewrite and new test (Task 2 Steps 1, 2, 4, 5; Task 1 Step 2); the two named `test_orchestrate.py` tests untouched (Steps 10, 12); whole suite (Steps 6, 16).
- Deviation from the spec's test list, stated openly: the rewritten `:463` test is renamed to `test_two_in_milestone_blockers_root_on_a_merged_base_in_blocked_by_order` and uses a reversed `blocked_by` so it pins order rather than duplicating `test_two_blockers_give_a_merged_root`; the `:517` test is renamed to describe its new behavior; the Steps-tier test asserts `merged_from` equals the joined card's `blocked_by` as brd reports it (the spec's own definition of census order) rather than hard-coding `[first, second]`, since brd's ordering of two `block` calls is not something this codebase pins; one new test is appended to `tests/test_orchestrate.py` for Review Focus item 1.
- Placeholder scan: no TBD/TODO; every code step has the code.
- Type consistency: `RootPlan(kind, branch, blockers)`, `base_branch_name(prefix, story)`, `_story_id(n)`, `_merged_root_behind(...) -> tuple[StoryPlan, RootPlan] | None`, `_merged_root_error(story, root, base_branch)` are used with the same names and argument orders everywhere.
