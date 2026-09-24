<!-- task-pipeline: validated -->
# Port dependency levels and cycle detection (card c4e32e4a)

Parent story 09a9203b "Stack geometry, and a dry run that shows it" (milestone 99e178cb). This card narrows decision O2 of `docs/superpowers/specs/2026-09-24-orchestration-design.md`, which extends `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. The StoryPlan shape comes from O1.

## Scope

Add pure functions for doneness, dependency levels and blocker-cycle detection to `src/agent_manager/dag.py`. Test them in `tests/test_dag.py`. The functions are ported from `~/Code/leave-me-alone/plugins/leave-me-alone/workflows/orchestrator.js`: lines 84-101 for doneness, 131-165 for levels, and 185-208 for the cycle check.

`dag.py` stays pure: no I/O, no subprocess, no `brd`. It already makes that promise in its module docstring. Extend the docstring to cover the new functions. The functions take `census.StoryPlan` and `census.SubtaskPlan` objects, which are frozen dataclasses defined in `src/agent_manager/census.py` on this branch. Fields are read by attribute (`id`, `title`, `status`, `blocked_by`, `subtasks`). `dag.py` may import `census` for type hints, because `census` is also pure. Do not change `census.py`; it belongs to card b84d47e1.

Out of scope, because other cards own it:
- `subtask_branch`, `story_tip`, `story_root`, `stack_bases` and the two-blocker error belong to sibling 59977446.
- `am run --milestone --dry-run` and any `cli.py` change belong to sibling 36faf21e.
- Section 4 of the orchestration spec rules out parallel stories, Integrate, milestone-aware resume, watch/retry/cancel, git-measured review counts and verification discovery.
- `storyRollupAnchor` (orchestrator.js 104-125) is not part of this card.

## Observable behavior

- `is_subtask_done(subtask) -> bool` is true when `subtask.status.lower() == "done"`.
- `is_story_closed(story) -> bool` is true when `story.status.lower() == "done"`.
- `remaining_subtasks(story) -> list[SubtaskPlan]`:
  - If the story is closed, it returns `[]`, whatever the subtasks' own statuses are.
  - Otherwise it returns the subtasks that are not done, in their existing order.
- `topological_levels(stories) -> list[list[StoryPlan]]`:
  - Let `ids` be the set of input story ids.
  - On each pass, "ready" means every remaining story whose `blocked_by` entries are each either outside `ids` or already placed.
  - Each ready batch becomes one level and keeps input order. The loop repeats until nothing remains.
  - Empty input returns `[]`.
  - The same `StoryPlan` objects come back. The input list is not mutated.
- `compute_levels(stories)`:
  - Drops every story that is closed or has no remaining subtasks.
  - Keeps census order for the rest, with no re-sorting.
  - Returns `topological_levels` of the pending stories.
  - A dropped story's id is outside the pending set, so a story blocked only by a finished story lands in level 0.
- `compute_integrate_levels(stories)` returns `topological_levels` over every story, finished or not.
- `assert_no_blocker_cycles(stories) -> None`:
  - Runs a depth-first search from each story in input order, marking stories as visiting or done.
  - Follows only blockers whose id is a story in the input.
  - Returns `None` when the stories have no cycle.
- Everywhere, blockers outside the milestone (ids not among the input stories) are ignored.

## Error paths

Add one error type, `DependencyCycleError(ValueError)`, to `dag.py`. It subclasses `ValueError` for the same reason as census's errors: `ValueError` is already in `cli.HANDLED`, so a later CLI caller gets an `ok: false` envelope without `dag.py` importing `cli`.

- **`topological_levels` finds no ready story while some remain.** It raises `DependencyCycleError`. The message lists every story still unplaced, in input order, as `#id, #id`, after the text `dependency cycle among stories `.
- **`assert_no_blocker_cycles` reaches a visiting story again.** It raises `DependencyCycleError` with a message containing `dependency cycle among stories #a -> #b -> #a`. The trail is sliced from the first occurrence of the repeated id, and the repeated id is appended at the end.
- **Message wording.** Port both messages from orchestrator.js. The JS `orchestrator:` prefix may be swapped for `dag:`, and the JS text after the cycle (`— no stack can be rooted until it is broken`) may be kept. Tests assert only on the substrings above.

## Comments to port (condensed)

Keep the orchestrator's incident reasoning next to the code:
- brd `status` is the only source of truth for doneness.
- A story marked done is never re-dispatched. During the 2026-08-17 outage, per-subtask lookups returned null and closed stories were re-implemented. The story's single status field cannot be corrupted piecemeal, and a story closed by mistake is reopened by hand.
- Card ids have no inherent order, so census order is the only stable order, and pending stories keep it.
- Integrate levels include finished stories, because a finished story's tip still has to be folded in.
- The cycle check has to run before any geometry. `story_tip` returns a branch immediately for a populated story, so a cycle between two populated stories never trips `story_root`'s own guard.

## Tests

All of these tests go in `tests/test_dag.py`, extending the existing file with plain pytest functions (parametrized where natural). §14 of the base design spec puts them in the pure unit-test tier: "Pure functions (dag.py, ...) — unit tests ported alongside the logic". They need no git, brd, subprocess or fake `claude`, and nothing goes under `tests/e2e/`. Build the inputs with real `census.StoryPlan` and `census.SubtaskPlan` instances.

1. **Doneness is case-insensitive.** `is_subtask_done` and `is_story_closed` both accept `"done"` and `"DONE"`, and reject `"todo"` and `"in_progress"`. (Unit)
2. **A closed story has no remaining subtasks.** `remaining_subtasks` returns `[]` for a closed story, even when its subtasks are todo. (Unit)
3. **Open story, mixed subtasks.** On an open story with a mix of done and todo subtasks, `remaining_subtasks` returns the not-done subtasks in order. (Unit)
4. **Linear chain.** For A ← B ← C, `compute_levels` returns `[[A], [B], [C]]`. (Unit)
5. **Diamond.** For A, then B and C blocked by A, then D blocked by B and C, the result is `[[A], [B, C], [D]]`, with B and C in input order. (Unit)
6. **Independent roots.** Two unblocked stories share level 0, in input order. (Unit)
7. **Done story, dispatch versus integrate.** Story A is done and B is blocked by A. `compute_levels` returns `[[B]]`, without A. `compute_integrate_levels` returns `[[A], [B]]`. Also check that a story that is open but has every subtask done is dropped from `compute_levels`. (Unit)
8. **External blocker ignored.** A story blocked by an id outside the input is placed in level 0 by `topological_levels`, and `assert_no_blocker_cycles` does not raise for it. (Unit)
9. **Two-story cycle.** A and B block each other. `assert_no_blocker_cycles` raises `DependencyCycleError` with a message containing `#a -> #b -> #a`, where the actual ids replace `a` and `b` and the trail starts from the first story walked. `topological_levels` also raises `DependencyCycleError`, naming both. (Unit)
10. **No cycle, no error.** On an acyclic milestone, such as the diamond, `assert_no_blocker_cycles` returns `None`. (Unit)

Verification: run `uv run pytest`. The whole default suite must pass, including `tests/e2e`.

---

# Port dependency levels and cycle detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pure doneness, dependency-level and blocker-cycle functions (ported from orchestrator.js) to `src/agent_manager/dag.py`, with unit tests in `tests/test_dag.py`.

**Architecture:** Three groups of pure functions appended to the existing `dag.py`: doneness helpers (`is_subtask_done`, `is_story_closed`, `remaining_subtasks`), a shared Kahn-style level engine (`topological_levels`) with two input-selecting wrappers (`compute_levels`, `compute_integrate_levels`), and a DFS cycle check (`assert_no_blocker_cycles`). All read `census.StoryPlan` / `census.SubtaskPlan` by attribute; the one new error type `DependencyCycleError` subclasses `ValueError` so `cli.HANDLED` already covers it.

**Tech Stack:** Python 3, stdlib only, pytest, run via `uv`.

**Spec:** `docs/superpowers/specs/task-port-dependency-levels-c4e32e4a-design.md` (reproduced verbatim above).

## Global Constraints

- `dag.py` stays pure: no I/O, no subprocess, no `brd`. It may import `agent_manager.census` (also pure) and nothing from `cli` or `board`.
- Do not modify `src/agent_manager/census.py` (card b84d47e1 owns it).
- Do not write `subtask_branch`, `story_tip`, `story_root`, `stack_bases` or the two-blocker error (sibling 59977446), and do not touch `cli.py` (sibling 36faf21e). Do not port `storyRollupAnchor`.
- Blockers outside the milestone (ids not among the input stories) are ignored everywhere.
- `DependencyCycleError(ValueError)`; message substrings: `dependency cycle among stories #id, #id` (levels) and `dependency cycle among stories #a -> #b -> #a` (cycle check).
- Census order is kept; never re-sort stories.
- Tests are plain pytest unit tests in `tests/test_dag.py` using real `census.StoryPlan` / `census.SubtaskPlan`; no git, brd, subprocess, fake `claude`, nothing under `tests/e2e/`.
- Verification: `uv run pytest` — the whole default suite, including `tests/e2e`, must be green.

## Review Focus

1. A story that lists itself in `blocked_by`: both `topological_levels` and `assert_no_blocker_cycles` should raise `DependencyCycleError` (the cycle check reports `#a -> #a`), not loop forever or silently place it. Test added in Task 3 (and the levels half in Task 2).
2. A cycle reached through a non-cyclic story walked first (C blocked by A, A and B block each other, input `[C, A, B]`): the reported trail should be only the cycle, `#a -> #b -> #a`, without `#c`. Test added in Task 3.
3. A story blocked both by a finished in-milestone story and by an external id: `compute_levels` should put it in level 0 (both blockers are outside the pending set). Test added in Task 2.
4. Caller's list untouched and the same objects returned: `topological_levels` must not mutate the input list and must return the identical `StoryPlan` instances. Test added in Task 2.
5. A status of `None` (the JS guards with `?? ''`): doneness helpers should return `False` rather than crash with `AttributeError`, and empty inputs should give `[]` / `None`. Tests added in Tasks 1 and 2.

---

## File Structure

- Modify: `src/agent_manager/dag.py` — append the new functions and `DependencyCycleError` after `ref_matches_card` (currently ends at line 74); extend the module docstring (lines 1-15).
- Modify: `tests/test_dag.py` — extend the import block (lines 1-9) and append tests at the end of the file (currently ends at line 136).

Nothing else changes.

---

### Task 1: Doneness helpers

**Files:**
- Modify: `src/agent_manager/dag.py` (imports at line 17-18; append after line 74)
- Test: `tests/test_dag.py` (import block lines 1-9; append at end)

**Interfaces:**
- Consumes: `agent_manager.census.StoryPlan(id: str, title: str, status: str, blocked_by: list[str], subtasks: list[SubtaskPlan])`, `agent_manager.census.SubtaskPlan(id: str, title: str, status: str)` — both frozen dataclasses already on this branch.
- Produces: `is_subtask_done(subtask: SubtaskPlan) -> bool`, `is_story_closed(story: StoryPlan) -> bool`, `remaining_subtasks(story: StoryPlan) -> list[SubtaskPlan]`. Test helpers `_sub(id: str, status: str = "todo") -> SubtaskPlan` and `_story(id: str, blocked_by: list[str] | None = None, status: str = "todo", subtasks: list[SubtaskPlan] | None = None) -> StoryPlan` in `tests/test_dag.py`, used by Tasks 2 and 3.

- [ ] **Step 1: Write the failing tests**

Replace the import block at the top of `tests/test_dag.py` (lines 1-9) with:

```python
import pytest

from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.dag import (
    is_story_closed,
    is_subtask_done,
    ref_matches_card,
    remaining_subtasks,
    short_id,
    slugify,
    task_branch,
    task_stem,
)
```

Append to the end of `tests/test_dag.py`:

```python
# ── doneness, levels and cycles ─────────────────────────────────────────────


def _sub(id: str, status: str = "todo") -> SubtaskPlan:
    return SubtaskPlan(id=id, title=f"subtask {id}", status=status)


def _story(
    id: str,
    blocked_by: list[str] | None = None,
    status: str = "todo",
    subtasks: list[SubtaskPlan] | None = None,
) -> StoryPlan:
    """A story; by default open with one todo subtask, so it is pending."""
    return StoryPlan(
        id=id,
        title=f"story {id}",
        status=status,
        blocked_by=list(blocked_by or []),
        subtasks=[_sub(f"{id}1")] if subtasks is None else subtasks,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [("done", True), ("DONE", True), ("Done", True), ("todo", False), ("in_progress", False)],
)
def test_doneness_is_case_insensitive_for_subtasks_and_stories(status, expected):
    assert is_subtask_done(_sub("s", status)) is expected
    assert is_story_closed(_story("a", status=status)) is expected


def test_doneness_treats_a_missing_status_as_not_done():
    assert is_subtask_done(SubtaskPlan(id="s", title="s", status=None)) is False
    story = StoryPlan(id="a", title="a", status=None, blocked_by=[], subtasks=[])
    assert is_story_closed(story) is False


def test_a_closed_story_has_no_remaining_subtasks_even_if_they_are_todo():
    story = _story("a", status="done", subtasks=[_sub("a1"), _sub("a2", "in_progress")])
    assert remaining_subtasks(story) == []


def test_an_open_story_keeps_its_not_done_subtasks_in_order():
    a1, a2, a3, a4 = _sub("a1", "done"), _sub("a2"), _sub("a3", "DONE"), _sub("a4", "in_progress")
    story = _story("a", subtasks=[a1, a2, a3, a4])
    assert remaining_subtasks(story) == [a2, a4]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'is_story_closed' from 'agent_manager.dag'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/dag.py`, replace the import lines (currently lines 17-18):

```python
import re
from collections.abc import Mapping
```

with:

```python
import re
from collections.abc import Mapping

from agent_manager.census import StoryPlan, SubtaskPlan
```

Append to the end of `src/agent_manager/dag.py`:

```python


# ── doneness ────────────────────────────────────────────────────────────────
# Port of orchestrator.js:84-101. brd `status` is the only source of truth for
# doneness; there is no other field to consult.


def is_subtask_done(subtask: SubtaskPlan) -> bool:
    """True when the subtask's brd status is ``done``, in any case."""
    return (subtask.status or "").lower() == "done"


def is_story_closed(story: StoryPlan) -> bool:
    """True when the story's own brd status is ``done``, in any case.

    A story marked done is finished, full stop: its subtasks are never
    re-dispatched. During the 2026-08-17 outage per-subtask lookups returned
    null and closed stories were re-implemented. The story's single status
    field cannot be corrupted piecemeal, so it is the safer gate; a story
    closed by mistake is reopened by hand.
    """
    return (story.status or "").lower() == "done"


def remaining_subtasks(story: StoryPlan) -> list[SubtaskPlan]:
    """The story's not-done subtasks in census order; none if the story is closed.

    The census already ordered the subtasks by their ``blocked_by`` chain, so
    nothing is re-sorted here.
    """
    if is_story_closed(story):
        return []
    return [subtask for subtask in story.subtasks if not is_subtask_done(subtask)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS for every test in the file (the existing naming tests and the new doneness tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): port story and subtask doneness helpers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 2: Dependency levels for dispatch and integrate

**Files:**
- Modify: `src/agent_manager/dag.py` (append after the Task 1 code)
- Test: `tests/test_dag.py` (import block; append at end)

**Interfaces:**
- Consumes: `is_story_closed`, `remaining_subtasks` from Task 1; test helpers `_sub`, `_story` from Task 1.
- Produces: `class DependencyCycleError(ValueError)`, `topological_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]`, `compute_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]`, `compute_integrate_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]`. Test helper `_ids(levels: list[list[StoryPlan]]) -> list[list[str]]`, used by Task 3.

- [ ] **Step 1: Write the failing tests**

Replace the `from agent_manager.dag import (...)` block in `tests/test_dag.py` with:

```python
from agent_manager.dag import (
    DependencyCycleError,
    compute_integrate_levels,
    compute_levels,
    is_story_closed,
    is_subtask_done,
    ref_matches_card,
    remaining_subtasks,
    short_id,
    slugify,
    task_branch,
    task_stem,
    topological_levels,
)
```

Append to the end of `tests/test_dag.py`:

```python
def _ids(levels: list[list[StoryPlan]]) -> list[list[str]]:
    return [[story.id for story in level] for level in levels]


def _diamond() -> list[StoryPlan]:
    return [
        _story("a"),
        _story("b", ["a"]),
        _story("c", ["a"]),
        _story("d", ["b", "c"]),
    ]


def test_linear_chain_is_one_story_per_level():
    stories = [_story("a"), _story("b", ["a"]), _story("c", ["b"])]
    assert _ids(compute_levels(stories)) == [["a"], ["b"], ["c"]]


def test_diamond_groups_the_two_middle_stories_in_input_order():
    assert _ids(compute_levels(_diamond())) == [["a"], ["b", "c"], ["d"]]


def test_diamond_keeps_census_order_not_id_order_in_a_shared_level():
    stories = [_story("a"), _story("c", ["a"]), _story("b", ["a"]), _story("d", ["b", "c"])]
    assert _ids(compute_levels(stories)) == [["a"], ["c", "b"], ["d"]]


def test_independent_roots_share_level_zero_in_input_order():
    assert _ids(compute_levels([_story("y"), _story("x")])) == [["y", "x"]]


def test_a_done_story_is_dropped_from_dispatch_but_kept_for_integrate():
    stories = [_story("a", status="done"), _story("b", ["a"])]
    assert _ids(compute_levels(stories)) == [["b"]]
    assert _ids(compute_integrate_levels(stories)) == [["a"], ["b"]]


def test_an_open_story_whose_subtasks_are_all_done_is_dropped_from_dispatch():
    stories = [
        _story("a", subtasks=[_sub("a1", "done"), _sub("a2", "DONE")]),
        _story("b", ["a"]),
    ]
    assert _ids(compute_levels(stories)) == [["b"]]
    assert _ids(compute_integrate_levels(stories)) == [["a"], ["b"]]


def test_an_open_story_with_no_subtasks_is_dropped_from_dispatch():
    stories = [_story("a", subtasks=[]), _story("b")]
    assert _ids(compute_levels(stories)) == [["b"]]


def test_blocked_by_a_finished_story_and_an_external_id_lands_in_level_zero():
    stories = [_story("a", status="done"), _story("b", ["a", "outside"])]
    assert _ids(compute_levels(stories)) == [["b"]]


def test_an_external_blocker_is_ignored_by_the_level_engine():
    stories = [_story("a", ["not-in-this-milestone"]), _story("b", ["a"])]
    assert _ids(topological_levels(stories)) == [["a"], ["b"]]


def test_levels_return_the_same_objects_and_leave_the_input_list_alone():
    stories = _diamond()
    before = list(stories)
    levels = topological_levels(stories)
    assert stories == before
    assert [id(story) for level in levels for story in level] == [id(s) for s in stories]


def test_empty_input_has_no_levels():
    assert topological_levels([]) == []
    assert compute_levels([]) == []
    assert compute_integrate_levels([]) == []


def test_a_two_story_cycle_stops_the_level_engine_naming_both():
    stories = [_story("a", ["b"]), _story("b", ["a"])]
    with pytest.raises(DependencyCycleError, match="dependency cycle among stories #a, #b"):
        topological_levels(stories)


def test_the_level_engine_names_only_the_unplaced_stories_of_a_cycle():
    stories = [_story("root"), _story("a", ["root", "b"]), _story("b", ["a"])]
    with pytest.raises(DependencyCycleError) as caught:
        compute_integrate_levels(stories)
    message = str(caught.value)
    assert "#a, #b" in message
    assert "#root" not in message


def test_a_self_blocking_story_stops_the_level_engine():
    with pytest.raises(DependencyCycleError, match="#a"):
        topological_levels([_story("a", ["a"])])


def test_a_dependency_cycle_error_is_a_value_error():
    assert issubclass(DependencyCycleError, ValueError)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'DependencyCycleError' from 'agent_manager.dag'`.

- [ ] **Step 3: Write the minimal implementation**

Append to the end of `src/agent_manager/dag.py`:

```python


# ── dependency levels ───────────────────────────────────────────────────────
# Port of orchestrator.js:131-165. One topological engine groups stories into
# levels by their ``blocked_by`` edges; dispatch and integrate differ only in
# which stories they feed it.


class DependencyCycleError(ValueError):
    """Stories' ``blocked_by`` edges form a cycle, so no order exists.

    Subclasses ``ValueError`` because ``ValueError`` is already in
    ``cli.HANDLED``: a CLI caller turns it into an ``ok: false`` envelope
    without this module importing ``cli``.
    """


def topological_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]:
    """``stories`` grouped into dependency levels, each level in input order.

    A story is ready once every blocker is either outside ``stories`` (ignored:
    it is not this milestone's to order) or already placed. The same
    ``StoryPlan`` objects come back; the input list is not touched.
    """
    ids = {story.id for story in stories}
    placed: set[str] = set()
    levels: list[list[StoryPlan]] = []
    rest = list(stories)
    while rest:
        ready = [
            story
            for story in rest
            if all(dep not in ids or dep in placed for dep in story.blocked_by or [])
        ]
        if not ready:
            listed = ", ".join(f"#{story.id}" for story in rest)
            raise DependencyCycleError(f"dag: dependency cycle among stories {listed}")
        levels.append(ready)
        placed.update(story.id for story in ready)
        rest = [story for story in rest if story.id not in placed]
    return levels


def compute_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]:
    """Dispatch levels: only stories that still have subtasks to run.

    A closed story, or one with no remaining subtasks, is dropped. Its id then
    sits outside the pending set, so a story it blocked lands in level 0.

    Card ids are opaque strings with no inherent order, so the census's own
    order is the only stable one: pending stories keep it, never re-sorted.
    """
    pending = [
        story
        for story in stories
        if not is_story_closed(story) and remaining_subtasks(story)
    ]
    return topological_levels(pending)


def compute_integrate_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]:
    """Integrate levels: every story, finished or not.

    A story finished in an earlier run still needs its tip folded in, or a
    resumed milestone's Integrate would report only its own slice as the
    whole milestone.
    """
    return topological_levels(stories)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS for every test in the file.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): port dependency levels for dispatch and integrate

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 3: Blocker-cycle check, module docstring, full suite

**Files:**
- Modify: `src/agent_manager/dag.py` (module docstring lines 1-15; append after the Task 2 code)
- Test: `tests/test_dag.py` (import block; append at end)

**Interfaces:**
- Consumes: `DependencyCycleError` from Task 2; test helpers `_story`, `_diamond` from Tasks 1-2.
- Produces: `assert_no_blocker_cycles(stories: list[StoryPlan]) -> None` (raises `DependencyCycleError`). Sibling 59977446 will call it before its stack geometry.

- [ ] **Step 1: Write the failing tests**

Replace the `from agent_manager.dag import (...)` block in `tests/test_dag.py` with:

```python
from agent_manager.dag import (
    DependencyCycleError,
    assert_no_blocker_cycles,
    compute_integrate_levels,
    compute_levels,
    is_story_closed,
    is_subtask_done,
    ref_matches_card,
    remaining_subtasks,
    short_id,
    slugify,
    task_branch,
    task_stem,
    topological_levels,
)
```

Append to the end of `tests/test_dag.py`:

```python
def test_a_two_story_cycle_is_reported_as_a_trail_from_the_first_story_walked():
    stories = [_story("a", ["b"]), _story("b", ["a"])]
    with pytest.raises(DependencyCycleError) as caught:
        assert_no_blocker_cycles(stories)
    assert "dependency cycle among stories #a -> #b -> #a" in str(caught.value)


def test_the_cycle_trail_follows_input_order_for_where_it_starts():
    stories = [_story("b", ["a"]), _story("a", ["b"])]
    with pytest.raises(DependencyCycleError, match="#b -> #a -> #b"):
        assert_no_blocker_cycles(stories)


def test_the_cycle_trail_omits_a_non_cyclic_story_that_led_into_it():
    stories = [_story("c", ["a"]), _story("a", ["b"]), _story("b", ["a"])]
    with pytest.raises(DependencyCycleError) as caught:
        assert_no_blocker_cycles(stories)
    message = str(caught.value)
    assert "dependency cycle among stories #a -> #b -> #a" in message
    assert "#c" not in message


def test_a_self_blocking_story_is_a_one_story_cycle():
    with pytest.raises(DependencyCycleError, match="#a -> #a"):
        assert_no_blocker_cycles([_story("a", ["a"])])


def test_a_cycle_between_finished_stories_is_still_caught():
    stories = [_story("a", ["b"], status="done"), _story("b", ["a"], status="done")]
    with pytest.raises(DependencyCycleError, match="#a -> #b -> #a"):
        assert_no_blocker_cycles(stories)


def test_an_external_blocker_does_not_trip_the_cycle_check():
    stories = [_story("a", ["not-in-this-milestone"]), _story("b", ["a"])]
    assert assert_no_blocker_cycles(stories) is None


def test_an_acyclic_milestone_passes_the_cycle_check():
    assert assert_no_blocker_cycles(_diamond()) is None
    assert assert_no_blocker_cycles([]) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'assert_no_blocker_cycles' from 'agent_manager.dag'`.

- [ ] **Step 3: Write the minimal implementation**

Append to the end of `src/agent_manager/dag.py`:

```python


# ── cycle detection ─────────────────────────────────────────────────────────
# Port of orchestrator.js:185-208.


def assert_no_blocker_cycles(stories: list[StoryPlan]) -> None:
    """Raise ``DependencyCycleError`` if the stories' ``blocked_by`` edges cycle.

    This must run before any stack geometry. ``compute_levels`` also refuses
    a cycle, but the geometry has to be sound first, and a cycle is exactly
    what breaks it. ``story_root``'s own guard is not enough either:
    ``story_tip`` returns a branch immediately for a story with subtasks, so a
    cycle between two populated stories never recurses back to trip it.

    Depth-first from each story in input order; only blockers that are stories
    in ``stories`` are followed, so blockers outside the milestone are ignored.
    """
    by_id = {story.id: story for story in stories}
    state: dict[str, str] = {}  # id -> "visiting" | "done"

    def walk(story_id: str, trail: list[str]) -> None:
        if state.get(story_id) == "done":
            return
        if state.get(story_id) == "visiting":
            cycle = trail[trail.index(story_id):] + [story_id]
            joined = " -> ".join(f"#{node}" for node in cycle)
            raise DependencyCycleError(
                f"dag: dependency cycle among stories {joined}"
                " — no stack can be rooted until it is broken"
            )
        state[story_id] = "visiting"
        for dep in by_id[story_id].blocked_by or []:
            if dep in by_id:
                walk(dep, [*trail, story_id])
        state[story_id] = "done"

    for story in stories:
        walk(story.id, [])
```

Then replace the last paragraph of the module docstring at the top of `src/agent_manager/dag.py` (currently line 14):

```python
This module is pure: no I/O, no subprocesses, no ``brd``.
"""
```

with:

```python
The rest of the module is the milestone's dependency graph, ported from the
leave-me-alone orchestrator: doneness read from brd ``status`` alone
(``is_subtask_done``, ``is_story_closed``, ``remaining_subtasks``), stories
grouped into dependency levels for dispatch and for integrate
(``topological_levels``, ``compute_levels``, ``compute_integrate_levels``),
and a blocker-cycle check that must run before any stack geometry
(``assert_no_blocker_cycles``). All of them read ``census.StoryPlan`` and
``census.SubtaskPlan`` by attribute, keep census order, and ignore blockers
outside the milestone.

This module is pure: no I/O, no subprocesses, no ``brd``.
"""
```

- [ ] **Step 4: Run the dag tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS for every test in the file.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: every test passes, including `tests/e2e`, with no errors or failures. If the new `from agent_manager.census import ...` in `dag.py` causes a circular-import error anywhere, stop and report it rather than moving the import (census imports only `agent_manager.models`, so none is expected).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): port the blocker-cycle check that guards stack geometry

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

## Spec coverage map

| Spec item | Task |
|---|---|
| `is_subtask_done`, `is_story_closed` (test 1) | Task 1 |
| `remaining_subtasks` closed / open (tests 2, 3) | Task 1 |
| `topological_levels`, empty input, same objects, no mutation | Task 2 |
| `compute_levels` drop + census order; linear, diamond, roots (tests 4-6) | Task 2 |
| Done story dispatch vs integrate, all-subtasks-done story (test 7) | Task 2 |
| External blocker ignored (test 8) | Task 2 (levels), Task 3 (cycle check) |
| Two-story cycle, both functions (test 9) | Task 2 (levels message), Task 3 (trail message) |
| No cycle, no error (test 10) | Task 3 |
| `DependencyCycleError(ValueError)` | Task 2 |
| Condensed incident comments | Tasks 1, 2, 3 (docstrings and section comments) |
| Module docstring extended | Task 3 |
| `uv run pytest` whole suite | Task 3 Step 5 |
