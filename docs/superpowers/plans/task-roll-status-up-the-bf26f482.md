<!-- task-pipeline: validated -->
# Roll status up the ancestors (subtask bf26f482)

Narrows addendum O5 (`docs/superpowers/specs/2026-09-24-orchestration-design.md:71-79`) to one change in `src/agent_manager/steps/rollup.py` plus its tests. Parent story f8290fd6 ("Run a milestone: rollup, the shared driver, the runner"). Blocked by 38809280 (done). Blocks c9037ac9 (`run_milestone`), which uses this rollup for its stale-story re-roll. Nothing from that card is built here.

## Scope

- Touches only `src/agent_manager/steps/rollup.py` and `tests/steps/test_rollup.py`.
- Does not touch `cli.py`, `orchestrate.py`, `tests/e2e` or `builtin/task.yaml`.
- Out of scope, per addendum §4: parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, and verification discovery.

## Observable behavior

`set_status(card: str, status: str, repo_dir: str | Path | None = None) -> dict[str, object]` keeps its name, signature and parameter names. The first parameter must stay `card` because the engine binds by name. That means `task.yaml` (lines 19 and 86), `tests/test_engine.py` and `tests/workflow/test_registry.py` stay untouched. `repo_dir` is passed through to every board call.

1. **Write the card.** Call `board.set_status(card, status)`. There is still no "already in this status" short-circuit, so repeat calls stay harmless on resume.
2. **Walk up the ancestors.** Start from the written card and repeat:
   - Get `parent_id = board.show(current).parent_id`. `CardNode` has no `parent_id`, which is why `show` is needed. Stop when it is empty or None.
   - Read `board.tree(parent_id)` fresh on every iteration. Nothing is cached.
   - Compute the target status from the node's direct children, by progress, as a port of `rollupStatus` and `storedStatus` from leave-me-alone's `scripts/rollup.mjs`:
     - The stored status maps `blocked` to `todo` and leaves every other status unchanged.
     - With no children the result is None, and the parent is not written.
     - If every child's stored status is `todo`, the target is `todo`.
     - If every child's stored status is `done`, the target is `done`.
     - Anything else gives `in_progress`.
   - Write `board.set_status(parent_id, target)` only when the target is not None and differs from the stored status of the parent's own status. Record `{"card": parent_id, "status": <status brd returned>}` in `rolled_up`.
   - Keep walking even when a parent was unchanged. That is how a stale grandparent left by an interrupted earlier run gets repaired.
   - Cap the depth at 16 ancestors. Going past the cap raises `board.BoardError("exceeded maximum ancestry depth")`. `BoardError` subclasses `RuntimeError`, so it satisfies either reading of the card, and it flows through the same error path as every other board failure.
3. **Return a plain dict:** `{"card": written.id, "status": written.status, "rolled_up": [...]}`.
   - `rolled_up` lists only the ancestors actually changed, nearest first.
   - It is `[]` for a parentless card, and also when every ancestor was already correct.

A pure helper holds the status computation, for example `rollup_status(children_statuses) -> str | None` plus the `blocked`-to-`todo` mapping. It is module-level, has no I/O, and can be tested without a board.

Update the docstrings:
- The module docstring (lines 6-9) says roll-up is deliberately absent. Replace that with a description of the walk.
- The function docstring (line 46 area) says exactly one card is written. It must now describe the walk and `rolled_up`.

## Error paths

- `board.BoardError` from any board call is not caught: the card write, any `show`, any `tree`, or any ancestor write. Both `task.yaml` call sites are `best_effort`, so the engine journals the error as a warning. Catching it here would hide the failure.
- The card's own write is not undone if the walk fails later. Ancestors written before the failure stay written, and a later call repairs the rest.
- An empty or nonexistent card id still fails at the first `board.set_status` with `BoardError`, before any walk starts.
- A parent chain deeper than 16 ancestors raises `BoardError` as described above.

## Tests

Test-placement rule: design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:479-494`).
- Steps are tested against a real temporary brd board, with no network and no mocking of brd.
- Pure functions get unit tests.
- End-to-end tests with a fake claude belong only in `tests/e2e`, and this card adds none.

Every test goes in `tests/steps/test_rollup.py`. The board tests reuse `temp_board`, `_add_card`, `_brd_json` and the `requires_brd` marker.

Existing tests are kept and updated (step tier, real temp brd board):
- `test_set_status_really_changes_the_card_on_the_board` uses the milestone, story and subtask setup. The equality check on the result now includes `rolled_up`, and the test asserts that the story and milestone read back as `in_progress`.
- The idempotency test and the overwrite test use parentless cards. Their expected dicts gain `"rolled_up": []`.
- The two `BoardError` tests (nonexistent id and empty id) are unchanged.

New tests (step tier, real temp brd board):
- When the first subtask of a story goes `in_progress`, the story and milestone become `in_progress`. `rolled_up` lists both, story first.
- When the last subtask goes `done` and its siblings are already `done`, the story and milestone become `done`. This assumes the story is the milestone's only child.
- A stale grandparent is repaired even when the parent is already correct. Set up a story that is already correct for its children and a milestone left stale. After `set_status`, the milestone is corrected, and `rolled_up` contains only the milestone.
- A card with no parent returns `rolled_up == []`.

Optional:
- A depth-cap test (step tier, real temp brd board): a chain of more than 16 ancestors raises `BoardError`.
- A pure unit test of the status helper (pure-function tier, no board). It covers no children giving None, all `todo`, `blocked` counted as `todo`, all `done`, and a mix giving `in_progress`.

The whole default suite (`uv run pytest`, including `tests/e2e`) must stay green, and `am run --card` behavior stays unchanged.

---

# Roll Status Up the Ancestors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `rollup.set_status` write the card and then recompute and repair every ancestor's board status by progress, returning which ancestors it changed.

**Architecture:** Two pure module-level helpers (`stored_status`, `rollup_status`) port leave-me-alone's `storedStatus`/`rollupStatus`. `set_status` keeps its signature, writes the card, then loops: `board.show(current).parent_id` -> `board.tree(parent_id)` (fresh) -> compute target from direct children -> write only if it differs -> continue to the root, capped at 16 ancestors. All board errors propagate.

**Tech Stack:** Python 3, pytest, the real `brd` CLI over subprocess (via `agent_manager.board`), `uv`.

**Spec:** `docs/superpowers/specs/task-roll-status-up-the-bf26f482-design.md` (reproduced above).

## Global Constraints

- Files touched: only `src/agent_manager/steps/rollup.py` and `tests/steps/test_rollup.py`. Do not touch `cli.py`, `orchestrate.py`, `tests/e2e`, or `src/agent_manager/workflow/builtin/task.yaml`.
- Signature stays exactly `set_status(card: str, status: str, repo_dir: str | Path | None = None) -> dict[str, object]`; first parameter name stays `card` (the engine binds by parameter name).
- `repo_dir` is passed (as `Path` or `None`) to every `board.*` call.
- Return is a plain `dict`, not a pydantic model: `{"card": written.id, "status": written.status, "rolled_up": [{"card": <id>, "status": <status brd returned>}, ...]}`, nearest ancestor first, `[]` when nothing changed.
- `blocked` maps to `todo` (brd derives `blocked` at read time and refuses to store it).
- Depth cap: 16 ancestors. Finding a 17th ancestor raises `board.BoardError("exceeded maximum ancestry depth", argv=...)`. Note `BoardError.__init__` requires the keyword-only `argv`; pass `board.show_argv(current)` (the `brd show` call that revealed the over-deep parent).
- `board.BoardError` is never caught in `rollup.py`. Writes made before a failure are not undone.
- Tests for this card live only in `tests/steps/test_rollup.py` (design §14: steps against a real temp brd board, no mocking of brd; pure helpers get unit tests in the same file). No tests in `tests/e2e`.
- Verification: `uv run pytest` (whole suite, including `tests/e2e`) must be green.

## Review Focus

1. A todo sibling that brd reports as `blocked` (blocked-by an unfinished sibling) must count as `todo`, so a stale `in_progress` story whose children are all todo/blocked is rolled back to `todo` -- pinned by `test_a_blocked_sibling_counts_as_todo_when_rolling_up` in Task 2.
2. A parent that brd reports as `blocked` but whose stored status equals the target (`todo`) must not be rewritten, so `rolled_up` stays `[]` -- pinned by `test_a_parent_reported_blocked_is_not_rewritten_to_todo` in Task 2.
3. Re-running the same `done` transition after the chain is already `done` (resume re-runs whole phases) must change nothing and report `rolled_up == []` -- pinned inside `test_the_last_subtask_going_done_marks_story_and_milestone_done` in Task 2.
4. `repo_dir` must reach `show` and `tree`, not only the card write; pytest runs with cwd at the repo root, whose own `.brd` board does not hold the temp cards, so any board call without `repo_dir` fails with `BoardError` -- pinned by every real-board walk test in Task 2 (all pass `repo_dir=temp_board` and walk at least one ancestor).
5. A walk that fails part-way keeps the writes already made (no rollback), and the cap boundary is exact (16 ancestors allowed, 17 raises) -- pinned by `test_the_walk_is_capped_at_sixteen_ancestors` in Task 3.

---

### Task 1: Pure status-computation helpers

**Files:**
- Modify: `src/agent_manager/steps/rollup.py:21-23` (add helpers after the imports)
- Test: `tests/steps/test_rollup.py` (append at the end of the file)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `rollup.stored_status(status: str) -> str` -- `"blocked"` -> `"todo"`, anything else unchanged.
  - `rollup.rollup_status(children_statuses: Iterable[str]) -> str | None` -- `None` for no children; `"todo"` if all stored statuses are `todo`; `"done"` if all are `done`; else `"in_progress"`.
  - `rollup.MAX_ANCESTRY_DEPTH: int = 16`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-roll-status-up-the-bf26f482/tests/steps/test_rollup.py`:

```python
# --- Pure-function tier (design §14): the status computation, no board. ---


def test_stored_status_flattens_blocked_to_todo():
    assert rollup.stored_status("blocked") == "todo"
    assert rollup.stored_status("todo") == "todo"
    assert rollup.stored_status("in_progress") == "in_progress"
    assert rollup.stored_status("done") == "done"


def test_rollup_status_is_none_without_children():
    assert rollup.rollup_status([]) is None


def test_rollup_status_is_todo_when_every_child_is_todo():
    assert rollup.rollup_status(["todo", "todo"]) == "todo"


def test_rollup_status_counts_blocked_children_as_todo():
    assert rollup.rollup_status(["todo", "blocked"]) == "todo"
    assert rollup.rollup_status(["blocked"]) == "todo"


def test_rollup_status_is_done_when_every_child_is_done():
    assert rollup.rollup_status(["done", "done"]) == "done"


def test_rollup_status_is_in_progress_by_progress_not_least_advanced():
    # One done child among unstarted ones means the parent is under way.
    assert rollup.rollup_status(["done", "todo"]) == "in_progress"
    assert rollup.rollup_status(["done", "blocked"]) == "in_progress"
    assert rollup.rollup_status(["in_progress"]) == "in_progress"
    assert rollup.rollup_status(["todo", "in_progress", "done"]) == "in_progress"


def test_rollup_status_accepts_a_generator():
    assert rollup.rollup_status(s for s in ["done", "done"]) == "done"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_rollup.py -k "stored_status or rollup_status" -v`
Expected: 7 FAIL with `AttributeError: module 'agent_manager.steps.rollup' has no attribute 'stored_status'` (or `'rollup_status'`).

- [ ] **Step 3: Write the minimal implementation**

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-roll-status-up-the-bf26f482/src/agent_manager/steps/rollup.py`, replace the import block

```python
from pathlib import Path

from agent_manager import board
```

with

```python
from collections.abc import Iterable
from pathlib import Path

from agent_manager import board

MAX_ANCESTRY_DEPTH = 16
"""Most ancestors the walk will visit; a guard against a corrupted parent chain."""


def stored_status(status: str) -> str:
    """The status as brd would store it: `blocked` is derived, so it reads as `todo`.

    brd computes `blocked` at read time from the dependency graph and refuses
    to store it, so every comparison flattens it here, in one place.
    """
    return "todo" if status == "blocked" else status


def rollup_status(children_statuses: Iterable[str]) -> str | None:
    """A parent's status computed from its direct children, by progress.

    `None` when there are no children (the parent is not written); `todo` when
    every child is unstarted; `done` when every child is done; otherwise
    `in_progress` -- one finished child among unstarted ones means the parent
    is under way, not unstarted. A port of leave-me-alone's `rollupStatus`.
    """
    statuses = [stored_status(status) for status in children_statuses]
    if not statuses:
        return None
    if all(status == "todo" for status in statuses):
        return "todo"
    if all(status == "done" for status in statuses):
        return "done"
    return "in_progress"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_rollup.py -v`
Expected: all 7 new tests PASS; the 5 existing board tests still PASS (behavior of `set_status` unchanged so far).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/rollup.py tests/steps/test_rollup.py
git commit -m "feat(rollup): add the pure stored_status/rollup_status helpers"
```

---

### Task 2: Walk the ancestors and report `rolled_up`

**Files:**
- Modify: `src/agent_manager/steps/rollup.py:1-19` (module docstring) and the `set_status` function (currently `rollup.py:26-53`, shifted down by Task 1)
- Modify: `tests/steps/test_rollup.py:1-11` (module docstring), `:69-103` (three existing tests), and add new board tests after `test_an_empty_card_id_raises_board_error`

**Interfaces:**
- Consumes: `rollup.stored_status`, `rollup.rollup_status`, `rollup.MAX_ANCESTRY_DEPTH` from Task 1; `board.set_status(card_id, status, *, repo_dir) -> models.Card`, `board.show(card_id, *, repo_dir) -> models.Card` (has `parent_id: str | None`), `board.tree(card_id, *, repo_dir) -> models.CardNode` (has `status`, `children`), `board.show_argv(card_id) -> list[str]`, `board.BoardError(message, *, argv, ...)`.
- Produces: `rollup.set_status(card: str, status: str, repo_dir: str | Path | None = None) -> dict[str, object]` returning `{"card": str, "status": str, "rolled_up": list[dict[str, str]]}`. Task 3 relies on the depth cap raising `board.BoardError` with message `"exceeded maximum ancestry depth"`.

- [ ] **Step 1: Update the test-module docstring**

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-roll-status-up-the-bf26f482/tests/steps/test_rollup.py`, replace lines 1-11

```python
"""Behaviour of the roll-up step (design §4 `steps/`, subtask card 43008688).

Placement follows design §14: `rollup.py` is a Steps component whose entire
behaviour is a brd write, so it is exercised against a real temporary brd board
over subprocess -- brd is not mocked, and neither is `board.set_status`.
```

with

```python
"""Behaviour of the roll-up step (design §4 `steps/`, subtask cards 43008688, bf26f482).

Placement follows design §14: `rollup.py` is a Steps component whose behaviour
is brd reads and writes -- write the card, then walk its ancestors -- so it is
exercised against a real temporary brd board over subprocess. brd is not
mocked, and neither is any `board` function. The pure status computation
(`stored_status`, `rollup_status`) gets plain unit tests at the end of the file.
```

(Leave the remaining docstring paragraph about `tests/steps/` having no `conftest.py` and the closing `"""` as they are.)

- [ ] **Step 2: Update the three existing tests for `rolled_up`**

Replace `test_set_status_really_changes_the_card_on_the_board` (lines 69-81) with:

```python
@requires_brd
def test_set_status_really_changes_the_card_on_the_board(temp_board):
    milestone = _add_card(temp_board, "Milestone 2")
    story = _add_card(temp_board, "Close the seams the wiring test found", milestone)
    subtask = _add_card(temp_board, "Implement the rollup.set_status step", story)
    assert _brd_json(temp_board, "show", subtask)["status"] == "todo"

    result = rollup.set_status(subtask, "in_progress", repo_dir=temp_board)

    stored = _brd_json(temp_board, "show", subtask)
    assert stored["status"] == "in_progress"
    # The mapping reports what brd stored, not the literal that was requested.
    assert result == {
        "card": subtask,
        "status": stored["status"],
        "rolled_up": [
            {"card": story, "status": "in_progress"},
            {"card": milestone, "status": "in_progress"},
        ],
    }
    assert _brd_json(temp_board, "show", story)["status"] == "in_progress"
    assert _brd_json(temp_board, "show", milestone)["status"] == "in_progress"
```

In `test_a_later_call_with_a_different_status_overwrites`, replace

```python
    assert result == {"card": subtask, "status": "done"}
```

with

```python
    assert result == {"card": subtask, "status": "done", "rolled_up": []}
```

In `test_a_second_identical_call_is_a_harmless_no_op`, replace

```python
    assert second == first
```

with

```python
    assert second == first == {"card": subtask, "status": "done", "rolled_up": []}
```

Leave `test_a_nonexistent_card_raises_board_error` and `test_an_empty_card_id_raises_board_error` unchanged.

- [ ] **Step 3: Add the new real-board walk tests**

Insert immediately after `test_an_empty_card_id_raises_board_error` (and before the pure-function block added in Task 1):

```python
@requires_brd
def test_the_first_subtask_going_in_progress_starts_story_and_milestone(temp_board):
    milestone = _add_card(temp_board, "Milestone 3")
    story = _add_card(temp_board, "Run a milestone", milestone)
    first = _add_card(temp_board, "Extract the shared driver", story)
    _add_card(temp_board, "Roll status up the ancestors", story)

    result = rollup.set_status(first, "in_progress", repo_dir=temp_board)

    assert result["rolled_up"] == [
        {"card": story, "status": "in_progress"},
        {"card": milestone, "status": "in_progress"},
    ]
    assert _brd_json(temp_board, "show", story)["status"] == "in_progress"
    assert _brd_json(temp_board, "show", milestone)["status"] == "in_progress"


@requires_brd
def test_the_last_subtask_going_done_marks_story_and_milestone_done(temp_board):
    milestone = _add_card(temp_board, "Milestone 3")
    story = _add_card(temp_board, "Run a milestone", milestone)
    earlier = _add_card(temp_board, "Extract the shared driver", story)
    last = _add_card(temp_board, "Roll status up the ancestors", story)
    # The state a real run leaves behind before the last subtask finishes.
    _brd_json(temp_board, "update", earlier, "--status", "done")
    _brd_json(temp_board, "update", last, "--status", "in_progress")
    _brd_json(temp_board, "update", story, "--status", "in_progress")
    _brd_json(temp_board, "update", milestone, "--status", "in_progress")

    result = rollup.set_status(last, "done", repo_dir=temp_board)

    assert result == {
        "card": last,
        "status": "done",
        "rolled_up": [
            {"card": story, "status": "done"},
            {"card": milestone, "status": "done"},
        ],
    }
    assert _brd_json(temp_board, "show", story)["status"] == "done"
    assert _brd_json(temp_board, "show", milestone)["status"] == "done"

    # Resume re-runs whole phases: the same transition again changes nothing.
    again = rollup.set_status(last, "done", repo_dir=temp_board)
    assert again == {"card": last, "status": "done", "rolled_up": []}


@requires_brd
def test_a_stale_grandparent_is_repaired_past_a_correct_parent(temp_board):
    milestone = _add_card(temp_board, "Milestone 3")
    story = _add_card(temp_board, "Run a milestone", milestone)
    first = _add_card(temp_board, "Extract the shared driver", story)
    _add_card(temp_board, "Roll status up the ancestors", story)
    # An interrupted earlier run: the story was rolled up, the milestone never was.
    _brd_json(temp_board, "update", first, "--status", "in_progress")
    _brd_json(temp_board, "update", story, "--status", "in_progress")
    assert _brd_json(temp_board, "show", milestone)["status"] == "todo"

    result = rollup.set_status(first, "in_progress", repo_dir=temp_board)

    assert result["rolled_up"] == [{"card": milestone, "status": "in_progress"}]
    assert _brd_json(temp_board, "show", story)["status"] == "in_progress"
    assert _brd_json(temp_board, "show", milestone)["status"] == "in_progress"


@requires_brd
def test_a_card_with_no_parent_rolls_nothing_up(temp_board):
    lone = _add_card(temp_board, "A card with no parent")

    result = rollup.set_status(lone, "in_progress", repo_dir=temp_board)

    assert result == {"card": lone, "status": "in_progress", "rolled_up": []}


@requires_brd
def test_a_blocked_sibling_counts_as_todo_when_rolling_up(temp_board):
    story = _add_card(temp_board, "Run a milestone")
    first = _add_card(temp_board, "Extract the shared driver", story)
    second = _brd_json(
        temp_board,
        "add",
        "--title",
        "Roll status up the ancestors",
        "--parent",
        story,
        "--blocked-by",
        first,
    )["id"]
    assert _brd_json(temp_board, "show", second)["status"] == "blocked"
    # A stale story: nothing under it has actually started.
    _brd_json(temp_board, "update", story, "--status", "in_progress")

    result = rollup.set_status(first, "todo", repo_dir=temp_board)

    # Children read ["todo", "blocked"]; blocked is todo, so the story is todo.
    assert result["rolled_up"] == [{"card": story, "status": "todo"}]
    assert _brd_json(temp_board, "show", story)["status"] == "todo"


@requires_brd
def test_a_parent_reported_blocked_is_not_rewritten_to_todo(temp_board):
    milestone = _add_card(temp_board, "Milestone 3")
    before = _add_card(temp_board, "An earlier story", milestone)
    story = _brd_json(
        temp_board,
        "add",
        "--title",
        "A story waiting on the earlier one",
        "--parent",
        milestone,
        "--blocked-by",
        before,
    )["id"]
    subtask = _add_card(temp_board, "A subtask of the waiting story", story)
    assert _brd_json(temp_board, "show", story)["status"] == "blocked"

    result = rollup.set_status(subtask, "todo", repo_dir=temp_board)

    # The story reads `blocked` but is stored `todo`, which is already the target.
    assert result["rolled_up"] == []
```

- [ ] **Step 4: Run the tests to verify the new and updated ones fail**

Run: `uv run pytest tests/steps/test_rollup.py -v`
Expected: FAIL for `test_set_status_really_changes_the_card_on_the_board`, `test_a_second_identical_call_is_a_harmless_no_op`, `test_a_later_call_with_a_different_status_overwrites`, `test_a_card_with_no_parent_rolls_nothing_up`, `test_the_last_subtask_going_done_marks_story_and_milestone_done` (result dict has no `rolled_up` key: `AssertionError` on the dict comparison), and `KeyError: 'rolled_up'` for `test_the_first_subtask_going_in_progress_starts_story_and_milestone`, `test_a_stale_grandparent_is_repaired_past_a_correct_parent`, `test_a_blocked_sibling_counts_as_todo_when_rolling_up`, `test_a_parent_reported_blocked_is_not_rewritten_to_todo`. The two `BoardError` tests and the 7 pure tests PASS.

- [ ] **Step 5: Implement the walk in `set_status`**

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-roll-status-up-the-bf26f482/src/agent_manager/steps/rollup.py`, replace the whole `set_status` function (from `def set_status(` through `return {"card": written.id, "status": written.status}`) with:

```python
def set_status(
    card: str, status: str, repo_dir: str | Path | None = None
) -> dict[str, object]:
    """Set `card`'s board status, roll it up its ancestors, and report both.

    Called by the two `best_effort: true` phases of `builtin/task.yaml`
    (`mark_in_progress`, `mark_done`), which supply `status` through the
    document's `args`.

    First the card itself is written. Then the walk climbs one ancestor at a
    time: `board.show` names the parent (tree nodes carry no `parent_id`),
    `board.tree` reads that parent and its children fresh -- nothing is cached,
    so a concurrent write elsewhere is seen as late as possible -- and
    `rollup_status` computes its status from the direct children. The parent is
    written only when that differs from its `stored_status`. The walk always
    continues to the root, even past an unchanged parent, so a grandparent left
    stale by an interrupted earlier run is repaired. More than
    `MAX_ANCESTRY_DEPTH` ancestors raises `board.BoardError`.

    Idempotency is inherited, not implemented: `brd update --status` stores the
    value it is given, so a repeated identical call is another successful write
    of the same card and, with the ancestors already correct, writes none of
    them. There is no "already in this status" short-circuit for the card,
    because resume re-runs whole phases and that must stay harmless.

    `board.BoardError` is not caught, from any call. Tolerance is the engine's
    policy -- both call sites are `best_effort`, so the engine journals the
    warning and the run continues; swallowing it here would make a failed board
    write invisible. Writes made before a failure are not undone; the next call
    repairs the rest.

    The returned mapping reports the statuses brd answered with, not the
    requested literals: the board's copy is the truth. `rolled_up` lists only
    the ancestors this call changed, nearest first, and is `[]` when the card
    has no parent or every ancestor was already right. A plain
    `dict[str, object]`, like `worktree.ensure`'s result -- it crosses no
    process boundary, so it needs no pydantic model (`CLAUDE.md`).
    """
    path = Path(repo_dir) if repo_dir is not None else None
    written = board.set_status(card, status, repo_dir=path)

    rolled_up: list[dict[str, str]] = []
    current = written.id
    depth = 0
    while True:
        parent_id = board.show(current, repo_dir=path).parent_id
        if not parent_id:
            break
        depth += 1
        if depth > MAX_ANCESTRY_DEPTH:
            raise board.BoardError(
                "exceeded maximum ancestry depth", argv=board.show_argv(current)
            )
        node = board.tree(parent_id, repo_dir=path)
        target = rollup_status(child.status for child in node.children)
        if target is not None and stored_status(node.status) != target:
            parent = board.set_status(parent_id, target, repo_dir=path)
            rolled_up.append({"card": parent.id, "status": parent.status})
        current = parent_id

    return {"card": written.id, "status": written.status, "rolled_up": rolled_up}
```

- [ ] **Step 6: Rewrite the module docstring**

In the same file, replace the module docstring (lines 1-19, from `"""Write one card's board status` through the closing `"""`) with:

```python
"""Write one card's board status and roll it up its ancestors.

Design §4 `steps/`, §6; subtask cards 43008688 and bf26f482; orchestration
addendum O5 (`docs/superpowers/specs/2026-09-24-orchestration-design.md:71-79`).

A deterministic step: no model call, no network beyond whatever `brd` itself
does, no filesystem work of its own. It writes the card with `board.set_status`
(`brd update <id> --status <status>`), then walks up the parent chain: for each
ancestor it reads the parent id with `board.show`, re-reads the parent's subtree
with `board.tree`, computes the parent's status from its direct children by
progress (`rollup_status`: all todo -> todo, all done -> done, anything else ->
in_progress, `blocked` counted as todo), and writes it only when it differs from
what is stored. The walk always reaches the root, so a stale grandparent is
repaired, and is capped at `MAX_ANCESTRY_DEPTH` ancestors. The status rule is a
port of `rollupStatus`/`storedStatus` from leave-me-alone's `scripts/rollup.mjs`.

Nothing is cached: every parent is read fresh, because sibling work can change
a shared ancestor between one level and the next.

The first parameter is named `card`, not `card_id`, because the engine binds
arguments by parameter name out of the run context and the context key holding
the bare id string is `card` (`engine.py:98`, `bind_arguments` at
`engine.py:163-212`). There is no `card_id` key, so a parameter by that name
would fail to bind and the engine would report a missing required parameter.
"""
```

- [ ] **Step 7: Run the step tests to verify they pass**

Run: `uv run pytest tests/steps/test_rollup.py -v`
Expected: all tests PASS (5 existing, 6 new board tests, 7 pure tests).

- [ ] **Step 8: Run the engine and registry suites that reference the step**

Run: `uv run pytest tests/test_engine.py tests/workflow/test_registry.py -v`
Expected: PASS (signature unchanged).

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/steps/rollup.py tests/steps/test_rollup.py
git commit -m "feat(rollup): roll a card's status up its ancestors by progress"
```

---

### Task 3: Pin the ancestry depth cap

**Files:**
- Test: `tests/steps/test_rollup.py` (insert after `test_a_parent_reported_blocked_is_not_rewritten_to_todo`)

**Interfaces:**
- Consumes: `rollup.set_status` and `rollup.MAX_ANCESTRY_DEPTH` from Tasks 1-2; `board.BoardError`.
- Produces: nothing new.

This task pins behavior already implemented in Task 2, so its test is expected to pass on first run; the RED check here is a mutation check to prove the test discriminates.

- [ ] **Step 1: Write the depth-cap test**

Insert after `test_a_parent_reported_blocked_is_not_rewritten_to_todo` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-roll-status-up-the-bf26f482/tests/steps/test_rollup.py`:

```python
@requires_brd
def test_the_walk_is_capped_at_sixteen_ancestors(temp_board):
    # chain[0] is the root; chain[i] has exactly i ancestors.
    chain = [_add_card(temp_board, "Level 0")]
    for level in range(1, rollup.MAX_ANCESTRY_DEPTH + 2):
        chain.append(_add_card(temp_board, f"Level {level}", chain[-1]))
    at_the_cap = chain[rollup.MAX_ANCESTRY_DEPTH]
    past_the_cap = chain[rollup.MAX_ANCESTRY_DEPTH + 1]

    # Exactly 16 ancestors is allowed: every one of them is rolled up.
    result = rollup.set_status(at_the_cap, "in_progress", repo_dir=temp_board)
    assert [entry["card"] for entry in result["rolled_up"]] == list(
        reversed(chain[: rollup.MAX_ANCESTRY_DEPTH])
    )

    # A 17th ancestor raises -- and the card's own write is not undone.
    with pytest.raises(board.BoardError, match="exceeded maximum ancestry depth"):
        rollup.set_status(past_the_cap, "done", repo_dir=temp_board)
    assert _brd_json(temp_board, "show", past_the_cap)["status"] == "done"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/steps/test_rollup.py::test_the_walk_is_capped_at_sixteen_ancestors -v`
Expected: PASS.

- [ ] **Step 3: Mutation check (RED proof), then revert**

Temporarily change `if depth > MAX_ANCESTRY_DEPTH:` to `if depth >= MAX_ANCESTRY_DEPTH:` in `src/agent_manager/steps/rollup.py` and run:

Run: `uv run pytest tests/steps/test_rollup.py::test_the_walk_is_capped_at_sixteen_ancestors -v`
Expected: FAIL with `board.BoardError: exceeded maximum ancestry depth` raised by the first (`at_the_cap`) call.

Then revert the line to `if depth > MAX_ANCESTRY_DEPTH:` and re-run the same command. Expected: PASS.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: all tests PASS, including `tests/e2e`.

- [ ] **Step 5: Commit**

```bash
git add tests/steps/test_rollup.py
git commit -m "test(rollup): pin the sixteen-ancestor depth cap"
```
