<!-- task-pipeline: validated -->
# Port stack roots, tips and bases (card 59977446)

Subtask of story 09a9203b "Stack geometry, and a dry run that shows it". Narrows decision O2 of `docs/superpowers/specs/2026-09-24-orchestration-design.md` (lines 44-54), which extends `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. Port source: `~/Code/leave-me-alone/plugins/leave-me-alone/workflows/orchestrator.js:222-268`.

## Base

Build on sibling c4e32e4a's finished work (branch `m3/task-port-dependency-levels-c4e32e4a`, HEAD 0c3401d). Its `dag.py` already holds the doneness helpers, the levels, `DependencyCycleError` and `assert_no_blocker_cycles`, and imports `StoryPlan` and `SubtaskPlan` from `census.py`. Do not re-port or change any of that. If the working branch does not yet contain it, integrate that branch first.

## Scope

Only `src/agent_manager/dag.py` and `tests/test_dag.py`. The code stays pure: no I/O, no subprocess, no `brd`. Update the module docstring's paragraph that lists what the module holds so it also mentions stack geometry.

Out of scope: `cli.py`, including everything in `am run --milestone --dry-run` (sibling 36faf21e). Also out: the levels and cycle code, parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, and git-measured review counts.

## Behavior

Types: `story` is a `StoryPlan`. `stories_by_id` is a `Mapping[str, StoryPlan]` for the milestone's stories. `prefix` and `base_branch` are `str`. `seen` is an optional `set[str]` that defaults to a fresh set per top-level call. Never use a shared mutable default.

- `subtask_branch(prefix, subtask) -> str` returns `task_branch(prefix, subtask)`. It adds no naming logic of its own, so there is one naming source. Port the orchestrator's comment about why geometry never consults PR head refs, condensed: the prefix is part of the milestone's identity, and determinism beats reconciling against an external system.
- `story_tip(story, stories_by_id, prefix, base_branch, seen=None) -> str`: if `story.subtasks` is non-empty, it returns `subtask_branch(prefix, story.subtasks[-1])`. That is the last subtask of the FULL ordered list, whatever its status. A story with no subtasks contributes no branch, so the call falls through to `story_root(...)` with the same `seen`.
- `story_root(story, stories_by_id, prefix, base_branch, seen=None) -> str`:
  - If `story.id` is already in `seen`, raise `DependencyCycleError`. The message names the story and says a cycle was reached while computing its stack root.
  - Otherwise add `story.id` to `seen`.
  - Blockers are the entries of `story.blocked_by` that are keys of `stories_by_id`, in their original order. External blockers are ignored.
  - With 0 blockers, return `base_branch`.
  - With 1 blocker, return that blocker's `story_tip(...)` with the same `seen`. This holds even when the blocker's status is `done`, because done does not mean its code landed anywhere.
  - With 2 or more, raise `StackRootError`.
  - Port the orchestrator's incident comments, condensed: why external blockers are skipped, and why multiple blockers are refused rather than guessed.
- `stack_bases(story, stories_by_id, prefix, base_branch) -> dict[str, str]`: maps each subtask id in `story.subtasks` (FULL list, census order, never `remaining_subtasks`) to its base branch.
  - Index 0 gets `story_root(story, ...)`.
  - Index i gets `subtask_branch(prefix, story.subtasks[i-1])`. A done first subtask therefore still anchors the second.
  - A story with no subtasks still computes its root, so root errors surface, and returns `{}`.

The docstrings note that `assert_no_blocker_cycles` must run before any of these functions. The `seen` guard is only a backstop, because a cycle between two populated stories never recurses back to trip it.

## Error paths

- `StackRootError(ValueError)` is a new class, declared in the style of `DependencyCycleError`. Its docstring gives the reason for subclassing `ValueError`: it is already in `cli.HANDLED`. It is raised when a story has 2 or more in-milestone blockers. The message must contain:
  - the story id, as `#<id>`
  - the blocker count and every blocker id, as `#<id>`, comma-joined
  - a statement that a stack can only root on ONE parent branch
  - the instruction to merge those blockers into `<base_branch>` first, or to restructure the dependencies so the story has a single blocker
- The cycle guard in `story_root` reuses `DependencyCycleError`. It is a cycle, and it is already a `ValueError`.
- `subtask_branch` passes through `task_branch`'s own `ValueError` for a malformed card id without changing it.

## Tests

The tier rule comes from the design doc's section 14 "Testing", `2026-09-23-agent-manager-design.md:477-492`: pure `dag.py` functions get plain unit tests. Every test below is therefore a **unit test in `tests/test_dag.py`** and builds `StoryPlan`/`SubtaskPlan` fixtures in memory. None goes in `test_cli.py` or `tests/e2e`. The whole default suite, including `tests/e2e`, must stay green.

1. `subtask_branch(prefix, s) == task_branch(prefix, s)` for a `SubtaskPlan`.
2. A story with no blockers roots on `base_branch`.
3. A story whose only blockers are outside `stories_by_id` roots on `base_branch`.
4. A story with one in-milestone blocker roots on that blocker's tip, which is the branch of the blocker's last subtask.
5. A blocker with status `done`, all subtasks `done`, still yields its tip.
6. Two blockers raise `StackRootError`. The message contains the story id, both blocker ids, and `base_branch`. The error is also an instance of `ValueError`.
7. A blocker with no subtasks falls through to its own root: A has no blockers, B (no subtasks) is blocked by A, and C is blocked by B. C's root is A's tip. Also check that a subtask-less story with no blockers has a tip equal to `base_branch`.
8. Cycle guard: two subtask-less stories block each other, and `story_root` raises `DependencyCycleError`. A pre-populated `seen` containing the story's id also raises.
9. `stack_bases` uses the FULL list. With a first subtask that is `done`, index 0 maps to the story root and index 1 maps to the done subtask's branch. Every subtask id appears.
10. `stack_bases` for a story with no subtasks returns `{}`.
11. The milestone-2 board: three stories chained (S1, then S2 blocked by S1, then S3 blocked by S2) with 2-3 subtasks each. Assert the full `stack_bases` map of every story. S1's first subtask bases on `base_branch`, S2's first on S1's last subtask branch, S3's first on S2's last subtask branch, and every other subtask on its predecessor.

---

# Stack Roots, Tips and Bases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the pure stack-geometry functions `subtask_branch`, `story_tip`, `story_root` and `stack_bases`, plus `StackRootError`, to `src/agent_manager/dag.py`, ported from the leave-me-alone orchestrator.

**Architecture:** Four pure functions appended to `dag.py` below `assert_no_blocker_cycles`, in a new `# ── stack geometry ──` section. `story_tip` and `story_root` are mutually recursive and share one `seen` set per top-level call. Branch names come only from the existing `task_branch`. Nothing reads git, brd or the filesystem.

**Tech Stack:** Python 3, frozen dataclasses from `agent_manager.census`, pytest, run through `uv`.

**Spec:** `docs/superpowers/specs/task-port-stack-roots-tips-59977446-design.md` (prepended verbatim above).

## Global Constraints

- Files touched: only `src/agent_manager/dag.py` and `tests/test_dag.py`. Do not touch `cli.py`, the doneness helpers, the levels code, `DependencyCycleError`'s declaration or `assert_no_blocker_cycles`.
- `dag.py` stays pure: no I/O, no subprocess, no `brd`.
- `seen` defaults to `None`; a fresh `set()` is made per top-level call. Never a shared mutable default.
- `stack_bases` walks the FULL `story.subtasks` list, never `remaining_subtasks`.
- `StackRootError` subclasses `ValueError` (already in `cli.HANDLED`); the cycle guard reuses `DependencyCycleError`.
- All new tests are unit tests in `tests/test_dag.py`. None go in `tests/test_cli.py` or `tests/e2e`.
- Verification: `uv run pytest` (the whole default suite, including `tests/e2e`, must stay green). There is no lint or typecheck command.
- The branch is `m3/task-port-stack-roots-tips-59977446`, cut from `m3/task-port-dependency-levels-c4e32e4a`. `dag.py` on it already has `DependencyCycleError` (line 128) and `assert_no_blocker_cycles` (line 194), and `tests/test_dag.py` already has `_sub` (line 151) and `_story` (line 155). Before Task 1, confirm with `grep -n "def assert_no_blocker_cycles" src/agent_manager/dag.py`; if it is missing, stop and merge `m3/task-port-dependency-levels-c4e32e4a` first instead of re-porting it.

## Review Focus

The spec is silent on these inputs. Each one is pinned by a test in the task that owns the code.

1. Calling `story_root` / `story_tip` twice on the same story in one process: a shared default `seen` would make the second call raise a false cycle. Expected: both calls return the same branch. Pinned in Task 2 (`test_repeated_top_level_calls_each_get_a_fresh_seen`).
2. A story blocked by one in-milestone story AND some external ids: expected to root on the in-milestone blocker's tip, not on the base and not with an error. Pinned in Task 2 (`test_external_blockers_beside_one_in_milestone_blocker_are_ignored`).
3. The same blocker id listed twice in `blocked_by`: a person would expect "blocked by #a" to mean one blocker, not "blocked by 2 stories (#a, #a)". Expected: duplicates count once and the story roots on #a's tip. Blockers are de-duplicated with `dict.fromkeys`, which keeps the spec's "original order". Pinned in Task 3 (`test_a_blocker_listed_twice_counts_once`).
4. A subtask with a malformed id reached through geometry: expected to surface `task_branch`'s own "not a card id" `ValueError` unchanged, not a new error. Pinned in Task 1 (`test_subtask_branch_passes_task_branchs_bad_id_error_through`) and Task 4 (`test_stack_bases_surfaces_a_malformed_subtask_id`).
5. A subtask-less story with two blockers passed to `stack_bases`: expected to raise `StackRootError` rather than quietly return `{}`, because the spec says root errors must surface. Pinned in Task 4 (`test_stack_bases_of_a_subtask_less_story_still_surfaces_a_root_error`).

Also covered along the way: `blocked_by=None` is read as no blockers (Task 2), matching the `story.blocked_by or []` idiom the existing levels code uses.

## File Structure

- Modify: `src/agent_manager/dag.py` — module docstring paragraph (lines 14-22) gains stack geometry; new section appended after `assert_no_blocker_cycles` (ends at line 226) holding `StackRootError`, `subtask_branch`, `story_tip`, `story_root`, `stack_bases`.
- Modify: `tests/test_dag.py` — the import block (lines 4-18) gains the new names; new fixture helpers and tests appended at the end of the file (after line 342).

Fixture note for every task: the existing `_sub(id)` helper builds subtasks with ids like `"a1"`, which are not card UUIDs, and `task_branch` rejects them with "not a card id". Every geometry test therefore builds subtasks with `_gsub(title, hex8, status)` (defined in Task 1), which gives a real 32-hex id whose short id is `hex8`, and always passes `subtasks=` explicitly to `_story` (its default subtask uses `_sub`).

---

### Task 1: `subtask_branch`

**Files:**
- Modify: `src/agent_manager/dag.py` (append after line 226)
- Test: `tests/test_dag.py` (import block lines 4-18; append at end)

**Interfaces:**
- Consumes: `task_branch(prefix: str, card: object) -> str` (`dag.py:75`), `SubtaskPlan(id: str, title: str, status: str)` (`census.py:165`).
- Produces: `subtask_branch(prefix: str, subtask: SubtaskPlan) -> str`. Test helpers `PREFIX = "m3"`, `BASE = "main"`, `_gsub(title: str, hex8: str, status: str = "todo") -> SubtaskPlan`, `_by_id(*stories: StoryPlan) -> dict[str, StoryPlan]`, used by Tasks 2-4.

- [ ] **Step 1: Write the failing test**

In `tests/test_dag.py`, replace the import block (lines 4-18) with:

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
    subtask_branch,
    task_branch,
    task_stem,
    topological_levels,
)
```

Append to the end of `tests/test_dag.py`:

```python
# ── stack geometry ──────────────────────────────────────────────────────────

PREFIX = "m3"
BASE = "main"


def _gsub(title: str, hex8: str, status: str = "todo") -> SubtaskPlan:
    """A subtask with a real card id, so ``task_branch`` accepts it.

    Its short id is ``hex8`` and its branch is ``m3/task-<title>-<hex8>``.
    """
    return SubtaskPlan(id=f"{hex8}-0000-4000-8000-000000000000", title=title, status=status)


def _by_id(*stories: StoryPlan) -> dict[str, StoryPlan]:
    return {story.id: story for story in stories}


def test_subtask_branch_is_task_branch_so_names_have_one_source():
    sub = _gsub("a1", "aaaa0001")
    assert subtask_branch(PREFIX, sub) == task_branch(PREFIX, sub) == "m3/task-a1-aaaa0001"


def test_subtask_branch_passes_task_branchs_bad_id_error_through():
    with pytest.raises(ValueError, match="not a card id"):
        subtask_branch(PREFIX, SubtaskPlan(id="nope", title="bad", status="todo"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'subtask_branch' from 'agent_manager.dag'`.

- [ ] **Step 3: Write minimal implementation**

Append to the end of `src/agent_manager/dag.py`:

```python


# ── stack geometry ──────────────────────────────────────────────────────────
# Port of orchestrator.js:211-268. Where each subtask's branch stacks is
# DERIVED from the census, never discovered. ``assert_no_blocker_cycles`` must
# run before any of these functions.


def subtask_branch(prefix: str, subtask: SubtaskPlan) -> str:
    """The branch a subtask's work lives on: exactly ``task_branch``.

    Derived, never looked up. An earlier orchestrator preferred a PR's real
    head ref, which made the geometry depend on the PRs and the PR matching
    depend on the geometry — a circularity that bred two bugs in one
    afternoon. Determinism beats reconciling against an external system, so
    the prefix is part of the milestone's identity, full stop.
    """
    return task_branch(prefix, subtask)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS, including `test_subtask_branch_is_task_branch_so_names_have_one_source` and `test_subtask_branch_passes_task_branchs_bad_id_error_through`.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): derive a subtask's branch from task_branch"
```

---

### Task 2: `story_tip` and `story_root` (base, one blocker, fall-through, cycle guard)

**Files:**
- Modify: `src/agent_manager/dag.py` (append after `subtask_branch` from Task 1)
- Test: `tests/test_dag.py` (import block; append at end)

**Interfaces:**
- Consumes: `subtask_branch(prefix: str, subtask: SubtaskPlan) -> str` (Task 1); `DependencyCycleError` (`dag.py:128`); `StoryPlan(id, title, status, blocked_by: list[str], subtasks: list[SubtaskPlan])` (`census.py:174`); test helpers `PREFIX`, `BASE`, `_gsub`, `_by_id` (Task 1) and `_story(id, blocked_by=None, status="todo", subtasks=None)` (`tests/test_dag.py:155`).
- Produces: `story_tip(story: StoryPlan, stories_by_id: Mapping[str, StoryPlan], prefix: str, base_branch: str, seen: set[str] | None = None) -> str` and `story_root(story: StoryPlan, stories_by_id: Mapping[str, StoryPlan], prefix: str, base_branch: str, seen: set[str] | None = None) -> str`. The cycle-guard message is `dag: dependency cycle reached story #<id> while computing its stack root`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_dag.py`, replace the import block with:

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
    story_root,
    story_tip,
    subtask_branch,
    task_branch,
    task_stem,
    topological_levels,
)
```

Append to the end of `tests/test_dag.py`:

```python
def test_a_story_with_no_blockers_roots_on_the_base_branch():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    assert story_root(a, _by_id(a), PREFIX, BASE) == "main"


def test_a_story_blocked_only_outside_the_milestone_roots_on_the_base_branch():
    a = _story("a", ["not-in-this-milestone"], subtasks=[_gsub("a1", "aaaa0001")])
    assert story_root(a, _by_id(a), PREFIX, BASE) == "main"


def test_a_missing_blocked_by_is_read_as_no_blockers():
    a = StoryPlan(
        id="a", title="a", status="todo", blocked_by=None, subtasks=[_gsub("a1", "aaaa0001")]
    )
    assert story_root(a, _by_id(a), PREFIX, BASE) == "main"


def test_a_story_tip_is_the_branch_of_its_last_subtask():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001"), _gsub("a2", "aaaa0002")])
    assert story_tip(a, _by_id(a), PREFIX, BASE) == "m3/task-a2-aaaa0002"


def test_one_in_milestone_blocker_roots_on_that_blockers_last_subtask():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001"), _gsub("a2", "aaaa0002")])
    b = _story("b", ["a"], subtasks=[_gsub("b1", "bbbb0001")])
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == "m3/task-a2-aaaa0002"


def test_external_blockers_beside_one_in_milestone_blocker_are_ignored():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", ["outside-1", "a", "outside-2"], subtasks=[_gsub("b1", "bbbb0001")])
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == "m3/task-a1-aaaa0001"


def test_a_done_blocker_still_yields_its_tip_because_done_is_not_landed():
    a = _story(
        "a",
        status="done",
        subtasks=[_gsub("a1", "aaaa0001", "done"), _gsub("a2", "aaaa0002", "done")],
    )
    b = _story("b", ["a"], subtasks=[_gsub("b1", "bbbb0001")])
    assert story_tip(a, _by_id(a, b), PREFIX, BASE) == "m3/task-a2-aaaa0002"
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == "m3/task-a2-aaaa0002"


def test_a_subtask_less_blocker_falls_through_to_its_own_root():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001"), _gsub("a2", "aaaa0002")])
    b = _story("b", ["a"], subtasks=[])
    c = _story("c", ["b"], subtasks=[_gsub("c1", "cccc0001")])
    stories = _by_id(a, b, c)
    assert story_tip(b, stories, PREFIX, BASE) == "m3/task-a2-aaaa0002"
    assert story_root(c, stories, PREFIX, BASE) == "m3/task-a2-aaaa0002"


def test_a_subtask_less_story_with_no_blockers_has_the_base_as_its_tip():
    d = _story("d", subtasks=[])
    assert story_tip(d, _by_id(d), PREFIX, BASE) == "main"


def test_the_seen_guard_stops_a_cycle_between_subtask_less_stories():
    a = _story("a", ["b"], subtasks=[])
    b = _story("b", ["a"], subtasks=[])
    with pytest.raises(
        DependencyCycleError,
        match="dependency cycle reached story #a while computing its stack root",
    ):
        story_root(a, _by_id(a, b), PREFIX, BASE)


def test_a_pre_populated_seen_containing_the_story_raises():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    with pytest.raises(DependencyCycleError, match="#a"):
        story_root(a, _by_id(a), PREFIX, BASE, seen={"a"})


def test_repeated_top_level_calls_each_get_a_fresh_seen():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", ["a"], subtasks=[])
    c = _story("c", ["b"], subtasks=[_gsub("c1", "cccc0001")])
    stories = _by_id(a, b, c)
    for _ in range(2):
        assert story_root(c, stories, PREFIX, BASE) == "m3/task-a1-aaaa0001"
        assert story_tip(b, stories, PREFIX, BASE) == "m3/task-a1-aaaa0001"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'story_root' from 'agent_manager.dag'`.

- [ ] **Step 3: Write minimal implementation**

Append to the end of `src/agent_manager/dag.py` (after `subtask_branch`):

```python


def story_tip(
    story: StoryPlan,
    stories_by_id: Mapping[str, StoryPlan],
    prefix: str,
    base_branch: str,
    seen: set[str] | None = None,
) -> str:
    """The branch a story's stack ends on — what a dependent story roots from.

    The last subtask of the FULL ordered list, whatever its status. A story
    with no subtasks contributes no branch, so it falls through to its own
    root with the same ``seen``. Run ``assert_no_blocker_cycles`` first.
    """
    if story.subtasks:
        return subtask_branch(prefix, story.subtasks[-1])
    return story_root(story, stories_by_id, prefix, base_branch, seen)


def story_root(
    story: StoryPlan,
    stories_by_id: Mapping[str, StoryPlan],
    prefix: str,
    base_branch: str,
    seen: set[str] | None = None,
) -> str:
    """Where a story's stack starts: ``base_branch`` or one blocker's tip.

    A blocker marked ``done`` still yields its tip: done does not mean its
    code landed anywhere. ``assert_no_blocker_cycles`` must run first; the
    ``seen`` guard here is only a backstop, because ``story_tip`` returns
    immediately for a story with subtasks, so a cycle between two populated
    stories never recurses back to trip it.
    """
    seen = set() if seen is None else seen
    if story.id in seen:
        raise DependencyCycleError(
            f"dag: dependency cycle reached story #{story.id} while computing its stack root"
        )
    seen.add(story.id)
    # Only blockers inside this milestone can be stacked on; anything else is
    # external work whose branch this run knows nothing about.
    blockers = [dep for dep in story.blocked_by or [] if dep in stories_by_id]
    if not blockers:
        return base_branch
    return story_tip(stories_by_id[blockers[0]], stories_by_id, prefix, base_branch, seen)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS for every test in the file, including the twelve new ones.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): port story tips and roots with a seen-guard"
```

---

### Task 3: `StackRootError` for two or more blockers

**Files:**
- Modify: `src/agent_manager/dag.py` (the `stack geometry` section from Tasks 1-2)
- Test: `tests/test_dag.py` (import block; append at end)

**Interfaces:**
- Consumes: `story_root` (Task 2); test helpers `PREFIX`, `BASE`, `_gsub`, `_by_id`, `_story`.
- Produces: `class StackRootError(ValueError)`. Message shape: `dag: story #<id> is blocked by <n> stories (#<b1>, #<b2>, ...), and a stack can only root on ONE parent branch. Merge those blockers into <base_branch> first, or restructure the dependencies so this story has a single blocker.`

- [ ] **Step 1: Write the failing tests**

In `tests/test_dag.py`, replace the import block with:

```python
from agent_manager.dag import (
    DependencyCycleError,
    StackRootError,
    assert_no_blocker_cycles,
    compute_integrate_levels,
    compute_levels,
    is_story_closed,
    is_subtask_done,
    ref_matches_card,
    remaining_subtasks,
    short_id,
    slugify,
    story_root,
    story_tip,
    subtask_branch,
    task_branch,
    task_stem,
    topological_levels,
)
```

Append to the end of `tests/test_dag.py`:

```python
def test_two_in_milestone_blockers_refuse_to_guess_a_root():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story("c", ["a", "outside", "b"], subtasks=[_gsub("c1", "cccc0001")])
    with pytest.raises(StackRootError) as caught:
        story_root(c, _by_id(a, b, c), PREFIX, BASE)
    message = str(caught.value)
    assert "story #c is blocked by 2 stories (#a, #b)" in message
    assert "ONE parent branch" in message
    assert "Merge those blockers into main first" in message
    assert "single blocker" in message
    assert "#outside" not in message
    assert isinstance(caught.value, ValueError)


def test_a_stack_root_error_is_a_value_error():
    assert issubclass(StackRootError, ValueError)


def test_a_blocker_listed_twice_counts_once():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", ["a", "a"], subtasks=[_gsub("b1", "bbbb0001")])
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == "m3/task-a1-aaaa0001"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'StackRootError' from 'agent_manager.dag'`.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/dag.py`, insert this class directly above `def subtask_branch` (inside the `stack geometry` section, after its header comment):

```python
class StackRootError(ValueError):
    """A story has no single parent branch for its stack to root on.

    Subclasses ``ValueError`` because ``ValueError`` is already in
    ``cli.HANDLED``: a CLI caller turns it into an ``ok: false`` envelope
    without this module importing ``cli``.
    """


```

Then in `story_root`, replace:

```python
    blockers = [dep for dep in story.blocked_by or [] if dep in stories_by_id]
    if not blockers:
        return base_branch
    return story_tip(stories_by_id[blockers[0]], stories_by_id, prefix, base_branch, seen)
```

with:

```python
    # A blocker listed twice is still one blocker.
    blockers = [dep for dep in dict.fromkeys(story.blocked_by or []) if dep in stories_by_id]
    if not blockers:
        return base_branch
    if len(blockers) > 1:
        # Deliberately not guessing. Rooting on one blocker silently builds
        # this story without the others' code, and an octopus base would need
        # a merge, which stacking never does. A human picks.
        listed = ", ".join(f"#{dep}" for dep in blockers)
        raise StackRootError(
            f"dag: story #{story.id} is blocked by {len(blockers)} stories ({listed}), "
            "and a stack can only root on ONE parent branch. Merge those blockers into "
            f"{base_branch} first, or restructure the dependencies so this story has "
            "a single blocker."
        )
    return story_tip(stories_by_id[blockers[0]], stories_by_id, prefix, base_branch, seen)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS for every test in the file, including the three new ones and all of Task 2's.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): refuse to root a stack on more than one blocker"
```

---

### Task 4: `stack_bases`, the milestone-2 board, and the module docstring

**Files:**
- Modify: `src/agent_manager/dag.py` (module docstring lines 14-22; append after `story_root`)
- Test: `tests/test_dag.py` (import block; append at end)

**Interfaces:**
- Consumes: `story_root`, `subtask_branch` (Tasks 1-2), `StackRootError` (Task 3), `assert_no_blocker_cycles` (`dag.py:194`); test helpers `PREFIX`, `BASE`, `_gsub`, `_by_id`, `_story`.
- Produces: `stack_bases(story: StoryPlan, stories_by_id: Mapping[str, StoryPlan], prefix: str, base_branch: str) -> dict[str, str]`, keyed by subtask id in census order. This is what sibling 36faf21e's dry run will call.

- [ ] **Step 1: Write the failing tests**

In `tests/test_dag.py`, replace the import block with:

```python
from agent_manager.dag import (
    DependencyCycleError,
    StackRootError,
    assert_no_blocker_cycles,
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

Append to the end of `tests/test_dag.py`:

```python
def test_stack_bases_anchor_on_the_full_list_even_past_a_done_first_subtask():
    a1 = _gsub("a1", "aaaa0001", "done")
    a2 = _gsub("a2", "aaaa0002")
    a3 = _gsub("a3", "aaaa0003")
    a = _story("a", subtasks=[a1, a2, a3])
    bases = stack_bases(a, _by_id(a), PREFIX, BASE)
    assert bases == {
        a1.id: "main",
        a2.id: "m3/task-a1-aaaa0001",
        a3.id: "m3/task-a2-aaaa0002",
    }
    assert list(bases) == [a1.id, a2.id, a3.id]


def test_stack_bases_of_a_closed_story_still_maps_every_subtask():
    a1 = _gsub("a1", "aaaa0001", "done")
    a2 = _gsub("a2", "aaaa0002", "done")
    a = _story("a", status="done", subtasks=[a1, a2])
    assert stack_bases(a, _by_id(a), PREFIX, BASE) == {
        a1.id: "main",
        a2.id: "m3/task-a1-aaaa0001",
    }


def test_stack_bases_of_a_story_with_no_subtasks_is_empty():
    a = _story("a", subtasks=[])
    assert stack_bases(a, _by_id(a), PREFIX, BASE) == {}


def test_stack_bases_of_a_subtask_less_story_still_surfaces_a_root_error():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story("c", ["a", "b"], subtasks=[])
    with pytest.raises(StackRootError, match="#c"):
        stack_bases(c, _by_id(a, b, c), PREFIX, BASE)


def test_stack_bases_surfaces_a_malformed_subtask_id():
    a = _story(
        "a",
        subtasks=[SubtaskPlan(id="nope", title="bad", status="todo"), _gsub("a2", "aaaa0002")],
    )
    with pytest.raises(ValueError, match="not a card id"):
        stack_bases(a, _by_id(a), PREFIX, BASE)


def test_the_milestone_two_board_stacks_each_story_on_the_previous_ones_tip():
    s1a, s1b = _gsub("s1a", "11110001"), _gsub("s1b", "11110002")
    s2a, s2b, s2c = _gsub("s2a", "22220001"), _gsub("s2b", "22220002"), _gsub("s2c", "22220003")
    s3a, s3b = _gsub("s3a", "33330001"), _gsub("s3b", "33330002")
    s1 = _story("s1", subtasks=[s1a, s1b])
    s2 = _story("s2", ["s1"], subtasks=[s2a, s2b, s2c])
    s3 = _story("s3", ["s2"], subtasks=[s3a, s3b])
    stories = _by_id(s1, s2, s3)
    assert_no_blocker_cycles([s1, s2, s3])

    assert stack_bases(s1, stories, PREFIX, BASE) == {
        s1a.id: "main",
        s1b.id: "m3/task-s1a-11110001",
    }
    assert stack_bases(s2, stories, PREFIX, BASE) == {
        s2a.id: "m3/task-s1b-11110002",
        s2b.id: "m3/task-s2a-22220001",
        s2c.id: "m3/task-s2b-22220002",
    }
    assert stack_bases(s3, stories, PREFIX, BASE) == {
        s3a.id: "m3/task-s2c-22220003",
        s3b.id: "m3/task-s3a-33330001",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'stack_bases' from 'agent_manager.dag'`.

- [ ] **Step 3: Write minimal implementation**

Append to the end of `src/agent_manager/dag.py` (after `story_root`):

```python


def stack_bases(
    story: StoryPlan,
    stories_by_id: Mapping[str, StoryPlan],
    prefix: str,
    base_branch: str,
) -> dict[str, str]:
    """Each subtask id mapped to the branch it stacks on, in census order.

    The first subtask stacks on the story's root; every other one on the
    previous subtask in the FULL list, never ``remaining_subtasks``, so a done
    first subtask still anchors the second. The root is computed even for a
    story with no subtasks, so its errors surface. Run
    ``assert_no_blocker_cycles`` first.
    """
    root = story_root(story, stories_by_id, prefix, base_branch)
    ordered = story.subtasks
    return {
        subtask.id: root if index == 0 else subtask_branch(prefix, ordered[index - 1])
        for index, subtask in enumerate(ordered)
    }
```

Then update the module docstring. In `src/agent_manager/dag.py`, replace:

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
```

with:

```python
The rest of the module is the milestone's dependency graph, ported from the
leave-me-alone orchestrator: doneness read from brd ``status`` alone
(``is_subtask_done``, ``is_story_closed``, ``remaining_subtasks``), stories
grouped into dependency levels for dispatch and for integrate
(``topological_levels``, ``compute_levels``, ``compute_integrate_levels``),
a blocker-cycle check that must run before any stack geometry
(``assert_no_blocker_cycles``), and the stack geometry itself: where each
story's stack roots and ends and what each subtask's branch stacks on
(``subtask_branch``, ``story_tip``, ``story_root``, ``stack_bases``), all
derived from the census and never discovered. All of them read
``census.StoryPlan`` and ``census.SubtaskPlan`` by attribute, keep census
order, and ignore blockers outside the milestone.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS for every test in the file, including the six new ones.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS, whole default suite green including `tests/e2e`, with no failures and no errors.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): map each subtask to the branch it stacks on"
```

---

## Spec coverage map

| Spec item | Task / test |
|---|---|
| `subtask_branch` delegates to `task_branch`, ported comment | Task 1, `test_subtask_branch_is_task_branch_so_names_have_one_source` |
| `task_branch`'s `ValueError` passes through | Task 1, `test_subtask_branch_passes_task_branchs_bad_id_error_through` |
| Test 2: no blockers -> base | Task 2, `test_a_story_with_no_blockers_roots_on_the_base_branch` |
| Test 3: only external blockers -> base | Task 2, `test_a_story_blocked_only_outside_the_milestone_roots_on_the_base_branch` |
| Test 4: one blocker -> its tip | Task 2, `test_one_in_milestone_blocker_roots_on_that_blockers_last_subtask` |
| Test 5: done blocker -> its tip | Task 2, `test_a_done_blocker_still_yields_its_tip_because_done_is_not_landed` |
| Test 6: two blockers -> `StackRootError`, message, `ValueError` | Task 3, `test_two_in_milestone_blockers_refuse_to_guess_a_root` |
| Test 7: subtask-less fall-through, lone tip = base | Task 2, `test_a_subtask_less_blocker_falls_through_to_its_own_root`, `test_a_subtask_less_story_with_no_blockers_has_the_base_as_its_tip` |
| Test 8: seen guard, pre-populated `seen` | Task 2, `test_the_seen_guard_stops_a_cycle_between_subtask_less_stories`, `test_a_pre_populated_seen_containing_the_story_raises` |
| Test 9: bases from FULL list | Task 4, `test_stack_bases_anchor_on_the_full_list_even_past_a_done_first_subtask` |
| Test 10: no subtasks -> `{}` (root still computed) | Task 4, `test_stack_bases_of_a_story_with_no_subtasks_is_empty`, `test_stack_bases_of_a_subtask_less_story_still_surfaces_a_root_error` |
| Test 11: milestone-2 board | Task 4, `test_the_milestone_two_board_stacks_each_story_on_the_previous_ones_tip` |
| Fresh `seen` per call | Task 2, `test_repeated_top_level_calls_each_get_a_fresh_seen` |
| Docstrings name `assert_no_blocker_cycles` as a precondition; incident comments ported | Tasks 1-4 implementation blocks |
| Module docstring mentions stack geometry | Task 4, Step 3 |
