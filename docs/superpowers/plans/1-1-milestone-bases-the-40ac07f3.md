# `milestone_bases`: the pure base-branch decision for board milestones — design

Date: 2026-10-04
Card: `40ac07f3` (subtask 1.1 of story `8fd3e3de`)
Status: approved scope (card), pre-plan

## 0. Parent spec and line references

Parent: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`.
"parent L<n>" below means a line in that file. Source line numbers are from
this branch's HEAD (`6ae4b26`).

Inherited constraints:

| constraint | parent |
|---|---|
| The decision table (five rows) | §1, L39-47 |
| "Open" is `dag.board_levels`' notion: not finished and some card under it is not | L49 |
| `merged` = finished and landed; `canceled`/`archived` = out of play; statuses come from `census.FINISHED_STATUSES` / `OUT_OF_PLAY_STATUSES` | L49-51 |
| The refusal is a new `MilestoneBlockersError`. Its message names the milestone and its open blockers and says to chain them. Exit 3 with the usual envelope | L52-56 |
| The decision is a pure function with no I/O: `milestone_bases(milestones, prefixes, branch_exists, base_branch) -> dict[id, str]`, with the local-branch-exists check injected | L62-65 |
| `integrate_branch(B)` = `<prefix(B)>-integrate` | L44 |
| No merged base for milestones with two or more blockers. Chain them (A ← B ← C) instead | Non-goals L31-32, open question L123-124 |
| Boards with no inter-milestone `blocked_by` edges behave exactly as today. A `done` blocker with no local integrate branch also behaves as today | Compatibility L100-101 |
| Unit tests cover every table row, including a chain A←B←C | Testing L107-110 |

## 1. Purpose

Today every milestone a board run dispatches uses the same `--base-branch`
(`orchestrate.py:2398`). This card adds only the **decision** of which base
each milestone should get, as a pure function plus its error type. Nothing
calls it yet. Wiring it into `run_board` / `_run_board_async` / dry-run is
sibling work (§6).

## 2. Behavior

### 2.1 `dag.milestone_is_open(root: models.CardNode) -> bool` (new, public)

This is the one definition of "open milestone" (parent L49). It returns True
iff all three hold:

- `not census.is_finished(root.status)`,
- `not census.is_out_of_play(root.status)`,
- `dag._has_open_descendant(root)`.

`dag.board_levels` (`dag.py:211-246`) changes its filter at `dag.py:223-229`
to `[root for root in roots if milestone_is_open(root)]`. That change is
behavior-neutral, and the existing `board_levels` tests in `tests/test_dag.py`
pin it. This exists so that `orchestrate` does not reach into the private
`dag._has_open_descendant`, and so that "open" cannot drift between the
leveling and the base decision. The `dag.py` module docstring (`dag.py:24-27`)
gets one clause naming `milestone_is_open` next to `board_levels`.

### 2.2 `orchestrate.MilestoneBlockersError(ValueError)` (new)

It subclasses `ValueError`, following the precedent of
`dag.DependencyCycleError` (`dag.py:134`). `ValueError` is already in
`cli.HANDLED`, so once a later card wires it in, a CLI caller gets the
`{"ok": false, "error": {"type": "MilestoneBlockersError", ...}}` envelope
and exit 3 with no `cli` change (parent L55). This card does not change
`cli.py`.

### 2.3 `orchestrate.milestone_bases(...)` (new)

```python
def milestone_bases(
    milestones: Sequence[models.CardNode],
    prefixes: Mapping[str, str],
    branch_exists: Callable[[str], bool],
    base_branch: str,
) -> dict[str, str]
```

It lives in `src/agent_manager/orchestrate.py`, directly after
`board_prefixes` (`orchestrate.py:2198-2223`), in the "board run" section.
**It cannot live in `dag.py`.** It must call `integration.integration_branch`
(`integration.py:73`), the one place `<prefix>-integrate` is spelled, and
`integration` imports `dag` (`integration.py:29`), so the import would be
circular. `orchestrate` already imports `census`, `dag`, `integration` and
`models`.

**Inputs.**

- `milestones` holds **every** milestone root the caller knows about, open or
  not. Non-open roots matter because the table reads a blocker's status even
  when that blocker is `done`/`merged`. The function never mutates them.
- `prefixes` maps a milestone id to its branch prefix. It must cover every
  blocker whose integrate branch the function names or checks (its open and
  unlanded blockers, below). It may also contain other ids, which are ignored.
- `branch_exists(branch)` is True iff the local branch `branch` exists. It is
  injected, and the function performs no I/O of its own.
- `base_branch` is the run's `--base-branch`, returned unchanged where the
  table says so.

**Output.** The result has one key per milestone in `milestones` for which
`dag.milestone_is_open` is True: exactly the milestones a board run would
dispatch. Keys are in input order. Non-open milestones get no key. An input
with no open milestones returns `{}`.

**Blockers of M.** These are the ids in `M.blocked_by`, deduplicated with
first occurrence kept (`dict.fromkeys`, as at `orchestrate.py:2384`), and
restricted to ids of cards in `milestones`. A blocker id that is not a root
in `milestones` (a deleted card, a story id, a typo) is ignored, the same way
`board_levels` ignores it (`dag.py:238`). Ignoring it means it counts as
satisfied.

**Classifying each blocker B of M.** Status checks go through `census`
helpers, so case is ignored. `orchestrate.py` gets no status string literal
(card). "Landed" needs one new census name, because "`merged` but not `done`"
cannot be said with `is_finished` alone. Add to `census.py`, next to
`OUT_OF_PLAY_STATUSES` (`census.py:45`):

- `LANDED_STATUSES = frozenset({"merged"}) | OUT_OF_PLAY_STATUSES`, with a
  docstring saying these are the statuses whose work a human has landed or
  dropped, so nothing stacks on them. `"merged"` is already a census literal
  (`census.py:41`), and census stays the one module that spells brd statuses.
- `is_landed(status: str | None) -> bool`, which is
  `(status or "").lower() in LANDED_STATUSES`, the same shape as
  `is_finished` / `is_out_of_play`.

Each blocker falls into one of three classes:

| class | condition | effect |
|---|---|---|
| **open** | `dag.milestone_is_open(B)`. This is checked first; a landed status already makes it False | B is a stack candidate. Its branch is `integration_branch(prefixes[B])`, whether or not that branch exists yet (B's own run in this board creates it). `branch_exists` is **not** called. |
| **landed** | `census.is_landed(B.status)` (`merged`, `canceled`, `archived`; any case) | Ignored. `branch_exists` is **not** called. |
| **unlanded** | anything else. That is `done`, or an in-play status (`todo`, `in_progress`, …) with nothing open under it | `branch_exists(integration_branch(prefixes[B]))` is called once. If True, B is a stack candidate. If False, B is treated as landed ("assume landed; today's behaviour", parent L46). |

The "unlanded" class extends parent row 3/4 to blockers whose status is not
literally `done` but which `board_levels` does not consider open (for
example, a `todo` milestone whose whole tree is `done`). Parent L49 makes
"open" the only distinction the board draws, and such a milestone is not
dispatched, so its work, if any, sits on an integrate branch from an earlier
run, exactly as for `done`.

**Decision for M,** where C is M's stack candidates in blocked_by order:

| rule | C | result |
|---|---|---|
| S0 | empty | `base_branch` |
| S1 | exactly one, B | `integration_branch(prefixes[B])` |
| S2 | two or more | raise `MilestoneBlockersError` |

This reproduces every parent row:

- No blockers, or all landed → S0 (parent row 1).
- One open blocker → S1 (row 2).
- One `done` blocker with its branch → S1 (row 3).
- A `done` blocker without its branch → S0 (row 4).
- Two or more open blockers → S2 (row 5).

**Where the parent table is silent, this card decides:**

- One open blocker plus any number of landed or branchless unlanded
  blockers → S1 with the open blocker. The others are satisfied.
- Two or more candidates where at least one is unlanded-with-branch (for
  example, one open plus one `done`-with-branch, or two `done`-with-branch)
  → **S2, refused**. Picking one candidate would silently drop the other's
  unmerged work from M's base. Falling back to `base_branch` would contradict
  row 3. A merged base for milestones is an explicit non-goal (L31-32). The
  message tells the human how to fix it (below).

**Error message (S2).** The refusal is raised for the **first** milestone in
input order that hits S2, before any later milestone is examined. That is
deterministic, and it names one problem at a time, as `board_prefixes` does.
The message must contain:

- M's id,
- every candidate blocker's id, in blocked_by order,
- the word `chain`,
- for an unlanded-with-branch candidate, a hint that marking it `merged`
  removes it.

Exact wording is the planner's, for example:
`milestone <M> is blocked by 2 milestones that are not landed (<B1>, <B2>); a milestone stacks on at most one: chain them (A <- B <- C) or mark the landed ones merged`.
Tests match on the ids and on `chain`, not on the full sentence.

**Missing prefix.** If a blocker needs `prefixes[B]` (open, or unlanded and
therefore checked) and `prefixes` has no entry for it, or the entry is blank
or not a string, raise `ValueError` naming M and B (for example
`milestone <M>'s blocker <B> has no branch prefix`). This is not
`MilestoneBlockersError`. It is a caller bug, refused the same way
`board_prefixes` refuses a blank prefix (`orchestrate.py:2214-2215`). A
landed blocker never needs a prefix.

**Purity.** No subprocess, no filesystem, no `brd`, no clock. The only
outside call is `branch_exists`, and it is called only for unlanded
blockers, at most once per (M, B) pair. The function assumes `milestones`
has no blocker cycle among open milestones. `run_board` runs
`dag.board_levels` (which raises `DependencyCycleError`) first, and this
function does not re-check.

### 2.4 Chain A ← B ← C (parent L32, L109)

With A, B and C all open, C blocked by B, and B blocked by A:
`{A: base_branch, B: "<pA>-integrate", C: "<pB>-integrate"}`.
With A `done` and its branch present, and B and C open: A gets no key,
`B: "<pA>-integrate"`, and `C: "<pB>-integrate"`.

## 3. Out of scope (sibling cards of story `8fd3e3de` and beyond)

- Calling `milestone_bases` from `run_board` / `_run_board_async`
  (`orchestrate.py:2261`, `:2348`), passing `bases[card.id]` at
  `orchestrate.py:2398`, and the placement of the refusal in `run_board`'s
  refusals section (parent L52-54).
- Extending `board_prefixes` / `cli.board_prefix_of` (`cli.py:1297`) to derive
  a prefix for non-open blockers (parent L57-61), and the real `branch_exists`
  implementation over git (`steps/worktree.py:91` is the likely building
  block).
- `--dry-run --board` `base_branch` key and the `base` column (parent L71-74).
- Resume keeping `resumed.base_branch` (parent L69-70).
- `--board --detach` (parent §2), README, e2e_fake scenarios.
- Any `cli.py` change. Any JSON or journal change (this card adds none, and
  the journal stays schema 1).

## 4. Tests

All tests are **unit tier, unmarked**. They exercise pure functions, inject
`branch_exists` as a set-membership lambda or a recording fake, and spawn
nothing (CLAUDE.md "Test tiers"). Tests mirror `src`:
`orchestrate.milestone_bases` → `tests/test_orchestrate.py`, next to the
`board_prefixes` tests (`tests/test_orchestrate.py:6813-6831`).
`dag.milestone_is_open` → `tests/test_dag.py`, next to the `board_levels`
tests. Reuse `_board_milestone(n, blocked_by=, status=, done_children=)`
(`tests/test_orchestrate.py:6777`) and `_plan_id`. Prefixes are
`{id: f"p{n}"}` dicts. `base_branch = "master"`.

`tests/test_orchestrate.py`:

1. **no blockers** → every open milestone maps to `base_branch`. Its keys are
   exactly the open milestones, in input order.
2. **blocker `merged`** (blocker has `status="merged"` and `done_children=True`)
   → `base_branch`. `branch_exists` is never called, which the recording fake
   asserts.
3. **blocker `canceled` / `archived`**, parametrized → `base_branch`, and
   `branch_exists` is never called.
4. **exactly one open blocker** → `"<pB>-integrate"`, with `branch_exists`
   returning False for everything. This shows existence is not consulted for
   an open blocker.
5. **`done` blocker with its integrate branch** → `"<pB>-integrate"`, and
   `branch_exists` was called with exactly `["<pB>-integrate"]`.
6. **`done` blocker without its integrate branch** → `base_branch`.
7. **two open blockers** → `MilestoneBlockersError`. It is an instance of
   `ValueError`. The message contains M's id, both blocker ids and `chain`.
8. **one open blocker + one `done`-with-branch blocker** →
   `MilestoneBlockersError` naming both (§2.3 silent-case decision).
9. **one open blocker + one `done`-without-branch + one `merged` blocker** →
   the open blocker's integrate branch.
10. **chain A ← B ← C**, all open → the mapping in §2.4.
11. **chain with A `done`-with-branch** → A absent, B on `<pA>-integrate`,
    C on `<pB>-integrate`.
12. **blocker id not among `milestones`** (an unknown `_plan_id(99)`) →
    `base_branch`, and `prefixes` lacking that id does not raise.
13. **duplicate blocker id** (`blocked_by=(1, 1)`, 1 open) → S1, not S2.
14. **status case-insensitivity**: a blocker with `"MERGED"` is landed. A
    blocker with `"Done"` goes through `branch_exists`.
15. **non-open milestones get no key**: a `done` milestone, and a `todo`
    milestone with `done_children=True`, are both absent from the result.
16. **missing prefix for a needed blocker** → `ValueError` (not
    `MilestoneBlockersError`) naming M and B. A landed blocker absent from
    `prefixes` does not raise.
17. **`todo` milestone with nothing open under it as blocker** (the
    "unlanded" non-`done` case) → behaves like `done`: its branch if present,
    else `base_branch`.

`tests/test_dag.py`:

18. **`milestone_is_open`**, parametrized: `todo` with an open child → True;
    `done` root → False; `merged` → False; `canceled`/`archived` with an open
    child → False; `todo` with only done children → False; `todo` whose only
    open child is `canceled` → False. The existing `board_levels` tests stay
    green unchanged.

`tests/test_census.py`:

19. **`is_landed`**, parametrized: `merged`, `MERGED`, `canceled`, `Archived`
    → True; `done`, `Done`, `todo`, `in_progress`, `None`, `""` → False.
    Also assert
    `LANDED_STATUSES <= FINISHED_STATUSES | OUT_OF_PLAY_STATUSES` and
    `"done" not in LANDED_STATUSES`. That pins the meaning "finished but not
    plain done, or out of play".

Verification: `uv run pytest` (default unit + git tiers) is green. There is
no lint or typecheck command (CLAUDE.md).

## 5. For the planner

**Files:**

- Modify `src/agent_manager/dag.py`: add `milestone_is_open`, use it in
  `board_levels`, and add one clause to the module docstring.
- Modify `src/agent_manager/census.py`: add `LANDED_STATUSES` and
  `is_landed` (§2.3), and add one line to the module docstring if it lists
  its contents.
- Modify `src/agent_manager/orchestrate.py`: add `MilestoneBlockersError` and
  `milestone_bases` after `board_prefixes`.
- Tests: `tests/test_census.py`, `tests/test_dag.py`,
  `tests/test_orchestrate.py`.

**Interfaces produced** (sibling wiring cards consume these exact names):

- `census.LANDED_STATUSES: frozenset[str]`, `census.is_landed(status: str | None) -> bool`
- `dag.milestone_is_open(root: models.CardNode) -> bool`
- `orchestrate.MilestoneBlockersError(ValueError)`
- `orchestrate.milestone_bases(milestones: Sequence[models.CardNode], prefixes: Mapping[str, str], branch_exists: Callable[[str], bool], base_branch: str) -> dict[str, str]`

Suggested task split: (1) `census.is_landed` and `dag.milestone_is_open`,
with tests 18-19. (2) `milestone_bases` and its error, tests
1-17 written first. Both follow strict TDD (card).

**Review Focus seeds** (failure modes most likely to bite):

1. A duplicate id in `blocked_by` being counted twice and turning S1 into a
   false refusal (test 13).
2. `branch_exists` called for open or landed blockers. In production that is
   a git call per blocker and a source of nondeterminism (tests 2-4).
3. A blocker that is not a milestone root (a story id in `blocked_by`)
   raising `KeyError` on `prefixes` instead of being ignored (test 12).
4. Mixed-case statuses from brd misclassified (test 14).
5. An "open" definition that drifts from `board_levels`, so a milestone gets
   a key but is never dispatched, or the reverse (tests 15, 18).

---

# `milestone_bases` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the pure decision of which base branch each open board milestone stacks on (`orchestrate.milestone_bases`), its refusal (`MilestoneBlockersError`), and the two shared predicates it needs (`census.is_landed`, `dag.milestone_is_open`). Nothing calls `milestone_bases` yet.

**Architecture:** `census` gains one status set and one predicate, so it stays the only module that spells brd statuses. `dag` gains `milestone_is_open`, the single definition of "open milestone", and `board_levels` is rewritten to use it (behavior-neutral). `orchestrate` gains the error type and the pure function right after `board_prefixes`. It classifies each blocker as open, landed or unlanded and applies rules S0/S1/S2. `branch_exists` is injected, so the function does no I/O.

**Tech Stack:** Python 3, Pydantic `models.CardNode`, pytest (unit tier, unmarked), `uv run pytest`.

**Spec:** `docs/superpowers/specs/1-1-milestone-bases-the-40ac07f3.md` (reproduced verbatim above this plan).

## Global Constraints

- All new tests are **unit tier, unmarked**: no subprocess, no `tmp_path`, no `brd`/`git`/`claude`. Each test ≤0.5s.
- `orchestrate.py` gets **no brd status string literal**. Every status check goes through `census.is_finished` / `census.is_out_of_play` / `census.is_landed` / `dag.milestone_is_open`.
- `<prefix>-integrate` is spelled only by `integration.integration_branch` (`integration.py:73`). `milestone_bases` must call it and never format the suffix itself.
- `MilestoneBlockersError` subclasses `ValueError`. No `cli.py` change, no JSON or journal change (journal stays schema 1).
- `milestone_bases` lives in `src/agent_manager/orchestrate.py` directly after `board_prefixes`, never in `dag.py` (that would make a circular import through `integration`).
- Exact public names: `census.LANDED_STATUSES: frozenset[str]`, `census.is_landed(status: str | None) -> bool`, `dag.milestone_is_open(root: models.CardNode) -> bool`, `orchestrate.MilestoneBlockersError(ValueError)`, `orchestrate.milestone_bases(milestones: Sequence[models.CardNode], prefixes: Mapping[str, str], branch_exists: Callable[[str], bool], base_branch: str) -> dict[str, str]`.
- Verification: `uv run pytest` is green. There is no lint or typecheck command.
- Do not run `git push`. Commit only to this branch.

## Review Focus

The spec's own seeds (duplicate blocker id, `branch_exists` called for open or landed blockers, a non-root blocker id, mixed-case statuses, "open" drifting from `board_levels`) already have tests (spec tests 13, 2-4, 12, 14, 15/18). These five failure modes are implied by the spec but no numbered spec test covers them:

1. **The same milestone appears twice in `milestones`** (callers such as `board_claims`' tests pass `[one, two, one]`). Expected: one key, and `branch_exists` is still called at most once per (M, B) pair. Test: `test_milestone_bases_a_repeated_milestone_checks_its_blocker_branch_once` (Task 2).
2. **Milestones given in reverse dependency order** (C, B, A). Classifying a blocker must not depend on whether it was already visited. Expected: the same bases, with keys in input order. Test: `test_milestone_bases_does_not_depend_on_input_order_for_classification` (Task 2).
3. **Two milestones would both be refused.** Expected: the refusal names the first one in input order, and its blockers in `blocked_by` order, not sorted. Test: `test_milestone_bases_refuses_the_first_offending_milestone_naming_blockers_in_blocked_by_order` (Task 2).
4. **A blank or non-string prefix for a needed blocker** (`""`, `"  "`, `None`). Without a check this would produce a branch named `-integrate`. Expected: `ValueError` naming M and B. Test: the parametrized `test_milestone_bases_refuses_a_blank_prefix_for_a_needed_blocker` (Task 2).
5. **A `canceled`/`archived` blocker that still has open work under it.** It must count as landed, not as an open stack candidate. Expected: `base_branch`, with `branch_exists` never called. Test: the `canceled`/`archived` rows of `test_milestone_bases_ignores_a_landed_blocker_without_checking_its_branch` use `done_children=False` (Task 2). Its `dag` side is `milestone_is_open` returning False for `canceled` with an open child (Task 1).

---

## File Structure

| file | change | responsibility |
|---|---|---|
| `src/agent_manager/census.py` | modify (after `OUT_OF_PLAY_STATUSES`, `census.py:45-48`; after `is_out_of_play`, `census.py:56-58`) | `LANDED_STATUSES`, `is_landed` |
| `src/agent_manager/dag.py` | modify (docstring `dag.py:24-27`; import `dag.py:35`; `_has_open_descendant` / `board_levels` `dag.py:198-229`) | `milestone_is_open`; `board_levels` uses it |
| `src/agent_manager/orchestrate.py` | modify (insert between `board_prefixes` (ends `orchestrate.py:2222`) and `board_claims` (`orchestrate.py:2225`)) | `MilestoneBlockersError`, `_blocker_branch`, `milestone_bases` |
| `tests/test_census.py` | modify (append after `test_status_sets_live_in_one_place`, end of file) | test 19 |
| `tests/test_dag.py` | modify (import list `tests/test_dag.py:6-26`; append after `test_board_levels_blocker_released_by_a_finished_milestone`, end of file) | test 18 |
| `tests/test_orchestrate.py` | modify (insert after `test_milestone_status_reads_a_run_milestone_payload`, before the `# ── run_board at its seams` header, about `tests/test_orchestrate.py:6871`) | tests 1-17 and Review Focus 1-4 |

The census docstring (`census.py:1-31`) lists only the three census functions, not the status helpers, so it gets no line (spec §5: "if it lists its contents").

---

### Task 1: `census.is_landed` and `dag.milestone_is_open`

**Files:**
- Modify: `src/agent_manager/census.py:45-58`
- Modify: `src/agent_manager/dag.py:24-27`, `dag.py:35`, `dag.py:198-229`
- Test: `tests/test_census.py` (append), `tests/test_dag.py` (import list plus append)

**Interfaces:**
- Consumes: `census.FINISHED_STATUSES`, `census.OUT_OF_PLAY_STATUSES`, `census.is_finished`, `census.is_out_of_play`, `dag._has_open_descendant(node: CardNode) -> bool` (all existing).
- Produces: `census.LANDED_STATUSES: frozenset[str]` (`{"merged", "canceled", "archived"}`), `census.is_landed(status: str | None) -> bool`, `dag.milestone_is_open(root: CardNode) -> bool`.

- [ ] **Step 1: Write the failing census test (spec test 19)**

Append to the end of `tests/test_census.py`:

```python
@pytest.mark.parametrize(
    ("status", "landed"),
    [
        ("merged", True),
        ("MERGED", True),
        ("canceled", True),
        ("Archived", True),
        ("done", False),
        ("Done", False),
        ("todo", False),
        ("in_progress", False),
        (None, False),
        ("", False),
    ],
)
def test_is_landed_means_merged_or_out_of_play_in_any_case(status, landed):
    assert census.is_landed(status) is landed


def test_landed_statuses_are_finished_but_not_plain_done_or_out_of_play():
    assert census.LANDED_STATUSES <= census.FINISHED_STATUSES | census.OUT_OF_PLAY_STATUSES
    assert "done" not in census.LANDED_STATUSES
    assert census.LANDED_STATUSES == frozenset({"merged", "canceled", "archived"})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_census.py -k "landed" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.census' has no attribute 'is_landed'` (and `... 'LANDED_STATUSES'`).

- [ ] **Step 3: Implement `LANDED_STATUSES` and `is_landed`**

In `src/agent_manager/census.py`, directly after the `OUT_OF_PLAY_STATUSES` docstring (it ends `brd's releasing statuses; `am` never writes either set."""`), insert:

```python

LANDED_STATUSES = frozenset({"merged"}) | OUT_OF_PLAY_STATUSES
"""brd statuses whose work a human has landed (`merged`) or dropped
(`canceled`/`archived`), so nothing stacks on them. Plain `done` is not
landed: its work may still sit on an unmerged integrate branch."""
```

Then directly after `is_out_of_play` (ends `return (status or "").lower() in OUT_OF_PLAY_STATUSES`), insert:

```python


def is_landed(status: str | None) -> bool:
    """True for `merged`, `canceled` or `archived`, in any case."""
    return (status or "").lower() in LANDED_STATUSES
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_census.py -v`
Expected: PASS (all census tests, including the existing `test_status_sets_live_in_one_place` and `test_census_imports_neither_cli_nor_subprocess`).

- [ ] **Step 5: Write the failing dag test (spec test 18)**

In `tests/test_dag.py`, add `milestone_is_open,` to the `from agent_manager.dag import (...)` list, between `is_subtask_done,` and `remaining_subtasks,` (alphabetical):

```python
    is_story_closed,
    is_subtask_done,
    milestone_is_open,
    remaining_subtasks,
```

Append to the end of `tests/test_dag.py` (it reuses the file's `_root(id, blocked_by=None, status="todo", children=None)` and `_card(id, status="todo", children=None)` helpers, defined at `tests/test_dag.py:614` and `:692`):

```python
# ── milestone_is_open: the one definition of an open milestone ──────────────


@pytest.mark.parametrize(
    ("root", "is_open"),
    [
        pytest.param(_root("a"), True, id="todo-with-an-open-child"),
        pytest.param(
            _root("a", status="in_progress", children=[_card("a1", "done", [_card("a1x", "todo")])]),
            True,
            id="open-grandchild",
        ),
        pytest.param(_root("a", status="done"), False, id="done-root"),
        pytest.param(_root("a", status="Done"), False, id="done-root-any-case"),
        pytest.param(_root("a", status="merged"), False, id="merged-root"),
        pytest.param(_root("a", status="MERGED"), False, id="merged-root-any-case"),
        pytest.param(_root("a", status="canceled"), False, id="canceled-with-open-child"),
        pytest.param(_root("a", status="archived"), False, id="archived-with-open-child"),
        pytest.param(
            _root("a", children=[_card("a1", "done"), _card("a2", "merged")]),
            False,
            id="todo-with-only-finished-children",
        ),
        pytest.param(
            _root("a", children=[_card("a1", "canceled", [_card("a1x", "todo")])]),
            False,
            id="todo-whose-only-open-child-is-canceled",
        ),
        pytest.param(_root("a", children=[]), False, id="todo-with-no-children"),
    ],
)
def test_milestone_is_open(root, is_open):
    assert milestone_is_open(root) is is_open


def test_board_levels_keeps_exactly_the_milestones_milestone_is_open_keeps():
    """Review: "open" cannot drift between the leveling and the base decision."""
    roots = [
        _root("a"),
        _root("b", status="done"),
        _root("c", status="merged"),
        _root("d", status="canceled"),
        _root("e", children=[_card("e1", "done")]),
        _root("f", children=[_card("f1", "canceled", [_card("f1x", "todo")])]),
        _root("g", ["a"]),
    ]
    kept = [node.id for level in board_levels(roots) for node in level]
    assert kept == [root.id for root in roots if milestone_is_open(root)]
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'milestone_is_open' from 'agent_manager.dag'`.

- [ ] **Step 7: Implement `milestone_is_open` and route `board_levels` through it**

In `src/agent_manager/dag.py`, directly after `_has_open_descendant` (ends at `if not is_out_of_play(child.status)\n    )`) and before `def board_levels`, insert:

```python


def milestone_is_open(root: CardNode) -> bool:
    """True when the milestone ``root`` still has work a board run would dispatch.

    The one definition of an open milestone, shared by ``board_levels`` and
    ``orchestrate.milestone_bases``. A root is open when it is neither finished
    nor out of play and some in-play card under it, at any depth, is not
    finished.
    """
    return (
        not is_finished(root.status)
        and not is_out_of_play(root.status)
        and _has_open_descendant(root)
    )
```

In `board_levels`, replace:

```python
    pending = [
        root
        for root in roots
        if not is_finished(root.status)
        and not is_out_of_play(root.status)
        and _has_open_descendant(root)
    ]
```

with:

```python
    pending = [root for root in roots if milestone_is_open(root)]
```

In the module docstring (`dag.py:25-27`), replace:

```
order, and ignore blockers outside the milestone. ``board_levels`` is the
same leveling one level up: open milestone roots (``models.CardNode``)
grouped by their own ``blocked_by`` edges, for display and claims only.
```

with:

```
order, and ignore blockers outside the milestone. ``board_levels`` is the
same leveling one level up: open milestone roots (``models.CardNode``)
grouped by their own ``blocked_by`` edges, for display and claims only;
``milestone_is_open`` is the one test of which roots are open.
```

`is_finished` and `is_out_of_play` stay imported (`_has_open_descendant` and `milestone_is_open` use them).

- [ ] **Step 8: Run the dag and census tests to verify they pass**

Run: `uv run pytest tests/test_dag.py tests/test_census.py -v`
Expected: PASS. Every existing `board_levels` test stays green unchanged.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/census.py src/agent_manager/dag.py tests/test_census.py tests/test_dag.py
git commit -m "feat: census.is_landed and dag.milestone_is_open (card 40ac07f3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `orchestrate.MilestoneBlockersError` and `orchestrate.milestone_bases`

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (insert between `board_prefixes`, ending `return prefixes` at about `:2222`, and `def board_claims(` at about `:2225`)
- Test: `tests/test_orchestrate.py` (insert after `test_milestone_status_reads_a_run_milestone_payload`, before the line `# ── run_board at its seams (card baef4f94) ──...`, about `:6871`)

**Interfaces:**
- Consumes: `dag.milestone_is_open(root: models.CardNode) -> bool` and `census.is_landed(status: str | None) -> bool` (Task 1); `integration.integration_branch(branch_prefix: str) -> str` (existing, returns `f"{branch_prefix}-integrate"`). `orchestrate.py` already imports `census`, `dag`, `integration`, `models` and `Callable`, `Mapping`, `Sequence` (`orchestrate.py:52`, `:61-72`). No new imports.
- Test helpers it reuses in `tests/test_orchestrate.py`: `_plan_id(n: int) -> str` (`:63`) and `_board_milestone(n, *, blocked_by=(), status="todo", done_children=False) -> models.CardNode` (`:6777`). Milestone `n`'s id is `_plan_id(n)`. Its one story and one subtask are `todo`, or `done` when `done_children=True`, so `_board_milestone(n)` is open and `_board_milestone(n, done_children=True)` is not.
- Produces: `orchestrate.MilestoneBlockersError(ValueError)` and `orchestrate.milestone_bases(milestones: Sequence[models.CardNode], prefixes: Mapping[str, str], branch_exists: Callable[[str], bool], base_branch: str) -> dict[str, str]`, consumed by sibling wiring cards.

- [ ] **Step 1: Write the failing tests (spec tests 1-17, Review Focus 1-4)**

Insert into `tests/test_orchestrate.py` directly before the line `# ── run_board at its seams (card baef4f94) ──────────────────────────────────`:

```python
# ── milestone_bases (card 40ac07f3) ─────────────────────────────────────────


def _prefixes(*ns: int) -> dict[str, str]:
    """`{_plan_id(n): f"p{n}"}`: milestone `n`'s integrate branch is `p<n>-integrate`."""
    return {_plan_id(n): f"p{n}" for n in ns}


def _recording_exists(*present: str) -> tuple[Callable[[str], bool], list[str]]:
    """A `branch_exists` that answers from `present` and records every call."""
    calls: list[str] = []

    def exists(branch: str) -> bool:
        calls.append(branch)
        return branch in present

    return exists, calls


def test_milestone_bases_puts_unblocked_open_milestones_on_the_base_branch_in_input_order():
    """Spec test 1: no blockers -> base_branch; keys are the open ones, input order."""
    two, one, done = _board_milestone(2), _board_milestone(1), _board_milestone(3, status="done")
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases([two, one, done], _prefixes(1, 2, 3), exists, "master")

    assert list(bases.items()) == [(two.id, "master"), (one.id, "master")]
    assert calls == []


def test_milestone_bases_of_no_open_milestones_is_empty():
    exists, _ = _recording_exists()
    assert orchestrate.milestone_bases([], {}, exists, "master") == {}
    assert (
        orchestrate.milestone_bases(
            [_board_milestone(1, status="done")], _prefixes(1), exists, "master"
        )
        == {}
    )


@pytest.mark.parametrize(
    ("status", "done_children"),
    [("merged", True), ("canceled", False), ("archived", False)],
)
def test_milestone_bases_ignores_a_landed_blocker_without_checking_its_branch(
    status, done_children
):
    """Spec tests 2-3, Review Focus 5: a canceled/archived blocker with open work
    under it is still landed, never a stack candidate."""
    blocker = _board_milestone(1, status=status, done_children=done_children)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists("p1-integrate")

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocked.id: "master"}
    assert calls == []


def test_milestone_bases_stacks_on_one_open_blocker_without_checking_its_branch():
    """Spec test 4: the open blocker's run creates its branch; existence is not asked."""
    blocker, blocked = _board_milestone(1), _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocker.id: "master", blocked.id: "p1-integrate"}
    assert calls == []


def test_milestone_bases_stacks_on_a_done_blocker_whose_integrate_branch_exists():
    """Spec test 5."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists("p1-integrate")

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocked.id: "p1-integrate"}
    assert calls == ["p1-integrate"]


def test_milestone_bases_treats_a_done_blocker_without_its_branch_as_landed():
    """Spec test 6: "assume landed; today's behaviour"."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocked.id: "master"}
    assert calls == ["p1-integrate"]


def test_milestone_bases_refuses_a_milestone_with_two_open_blockers():
    """Spec test 7."""
    one, two = _board_milestone(1), _board_milestone(2)
    blocked = _board_milestone(3, blocked_by=(1, 2))
    exists, _ = _recording_exists()

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        orchestrate.milestone_bases([one, two, blocked], _prefixes(1, 2, 3), exists, "master")

    assert isinstance(caught.value, ValueError)
    message = str(caught.value)
    assert blocked.id in message
    assert one.id in message and two.id in message
    assert "chain" in message


def test_milestone_bases_refuses_an_open_blocker_plus_a_done_blocker_with_its_branch():
    """Spec test 8: picking one would drop the other's unmerged work."""
    one = _board_milestone(1)
    two = _board_milestone(2, status="done", done_children=True)
    blocked = _board_milestone(3, blocked_by=(1, 2))
    exists, _ = _recording_exists("p2-integrate")

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        orchestrate.milestone_bases([one, two, blocked], _prefixes(1, 2, 3), exists, "master")

    message = str(caught.value)
    assert blocked.id in message
    assert one.id in message and two.id in message
    assert "chain" in message
    assert "merged" in message


def test_milestone_bases_stacks_on_the_one_open_blocker_when_the_others_are_satisfied():
    """Spec test 9: open + done-without-branch + merged -> the open one."""
    one = _board_milestone(1)
    two = _board_milestone(2, status="done", done_children=True)
    three = _board_milestone(3, status="merged", done_children=True)
    blocked = _board_milestone(4, blocked_by=(1, 2, 3))
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases(
        [one, two, three, blocked], _prefixes(1, 2, 3, 4), exists, "master"
    )

    assert bases == {one.id: "master", blocked.id: "p1-integrate"}
    assert calls == ["p2-integrate"]


def test_milestone_bases_stacks_a_chain_of_open_milestones():
    """Spec test 10 / §2.4: A <- B <- C, all open."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases([a, b, c], _prefixes(1, 2, 3), exists, "master")

    assert list(bases.items()) == [
        (a.id, "master"),
        (b.id, "p1-integrate"),
        (c.id, "p2-integrate"),
    ]


def test_milestone_bases_stacks_a_chain_whose_head_is_done_with_its_branch():
    """Spec test 11 / §2.4: A done with its branch, B and C open."""
    a = _board_milestone(1, status="done", done_children=True)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    exists, _ = _recording_exists("p1-integrate")

    bases = orchestrate.milestone_bases([a, b, c], _prefixes(1, 2, 3), exists, "master")

    assert list(bases.items()) == [(b.id, "p1-integrate"), (c.id, "p2-integrate")]


def test_milestone_bases_ignores_a_blocker_that_is_not_a_milestone():
    """Spec test 12: an unknown id needs no prefix and counts as satisfied."""
    blocked = _board_milestone(1, blocked_by=(99,))
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases([blocked], _prefixes(1), exists, "master")

    assert bases == {blocked.id: "master"}
    assert calls == []


def test_milestone_bases_counts_a_duplicated_blocker_once():
    """Spec test 13: blocked_by=(1, 1) is S1, not a false S2 refusal."""
    blocker, blocked = _board_milestone(1), _board_milestone(2, blocked_by=(1, 1))
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases[blocked.id] == "p1-integrate"


def test_milestone_bases_reads_blocker_statuses_in_any_case():
    """Spec test 14: MERGED is landed; Done goes through branch_exists."""
    merged = _board_milestone(1, status="MERGED", done_children=True)
    on_merged = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists("p1-integrate")
    assert orchestrate.milestone_bases(
        [merged, on_merged], _prefixes(1, 2), exists, "master"
    ) == {on_merged.id: "master"}
    assert calls == []

    done = _board_milestone(1, status="Done", done_children=True)
    exists, calls = _recording_exists("p1-integrate")
    assert orchestrate.milestone_bases(
        [done, on_merged], _prefixes(1, 2), exists, "master"
    ) == {on_merged.id: "p1-integrate"}
    assert calls == ["p1-integrate"]


def test_milestone_bases_gives_non_open_milestones_no_key():
    """Spec test 15: the keys are exactly what board_levels would dispatch."""
    done = _board_milestone(1, status="done")
    finished_tree = _board_milestone(2, done_children=True)
    live = _board_milestone(3)
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases(
        [done, finished_tree, live], _prefixes(1, 2, 3), exists, "master"
    )

    assert list(bases) == [live.id]
    assert list(bases) == [
        node.id for level in dag.board_levels([done, finished_tree, live]) for node in level
    ]


@pytest.mark.parametrize(
    "blocker",
    [
        pytest.param(_board_milestone(1), id="open"),
        pytest.param(_board_milestone(1, status="done", done_children=True), id="done"),
    ],
)
def test_milestone_bases_refuses_a_needed_blocker_with_no_prefix(blocker):
    """Spec test 16: a caller bug, so ValueError, not MilestoneBlockersError."""
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, _ = _recording_exists()

    with pytest.raises(ValueError) as caught:
        orchestrate.milestone_bases([blocker, blocked], _prefixes(2), exists, "master")

    assert not isinstance(caught.value, orchestrate.MilestoneBlockersError)
    message = str(caught.value)
    assert blocked.id in message and blocker.id in message
    assert "no branch prefix" in message


@pytest.mark.parametrize("prefix", ["", "   ", None])
def test_milestone_bases_refuses_a_blank_prefix_for_a_needed_blocker(prefix):
    """Review Focus 4: a blank prefix would name the branch `-integrate`."""
    blocker, blocked = _board_milestone(1), _board_milestone(2, blocked_by=(1,))
    prefixes = {blocker.id: prefix, blocked.id: "p2"}
    exists, _ = _recording_exists()

    with pytest.raises(ValueError, match="no branch prefix") as caught:
        orchestrate.milestone_bases([blocker, blocked], prefixes, exists, "master")

    assert not isinstance(caught.value, orchestrate.MilestoneBlockersError)


def test_milestone_bases_needs_no_prefix_for_a_landed_blocker():
    """Spec test 16, second half."""
    blocker = _board_milestone(1, status="merged", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(2), exists, "master")

    assert bases == {blocked.id: "master"}


@pytest.mark.parametrize(
    ("present", "expected"),
    [(("p1-integrate",), "p1-integrate"), ((), "master")],
)
def test_milestone_bases_treats_a_todo_blocker_with_nothing_open_like_done(present, expected):
    """Spec test 17: the "unlanded" non-done case."""
    blocker = _board_milestone(1, status="todo", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists(*present)

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocked.id: expected}
    assert calls == ["p1-integrate"]


def test_milestone_bases_a_repeated_milestone_checks_its_blocker_branch_once():
    """Review Focus 1: at most one branch_exists call per (M, B) pair."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists("p1-integrate")

    bases = orchestrate.milestone_bases(
        [blocker, blocked, blocked], _prefixes(1, 2), exists, "master"
    )

    assert bases == {blocked.id: "p1-integrate"}
    assert calls == ["p1-integrate"]


def test_milestone_bases_does_not_depend_on_input_order_for_classification():
    """Review Focus 2: a chain given C, B, A gets the same bases, keyed C, B, A."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases([c, b, a], _prefixes(1, 2, 3), exists, "master")

    assert list(bases.items()) == [
        (c.id, "p2-integrate"),
        (b.id, "p1-integrate"),
        (a.id, "master"),
    ]


def test_milestone_bases_refuses_the_first_offending_milestone_naming_blockers_in_blocked_by_order():
    """Review Focus 3: one problem at a time, deterministically worded."""
    one, two = _board_milestone(1), _board_milestone(2)
    first = _board_milestone(3, blocked_by=(2, 1))
    second = _board_milestone(4, blocked_by=(1, 2))
    exists, _ = _recording_exists()

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        orchestrate.milestone_bases(
            [one, two, first, second], _prefixes(1, 2, 3, 4), exists, "master"
        )

    message = str(caught.value)
    assert first.id in message
    assert second.id not in message
    assert message.index(two.id) < message.index(one.id)


def test_milestone_bases_leaves_its_inputs_alone():
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1, 1))
    milestones = [a, b]
    before = [card.model_copy(deep=True) for card in milestones]
    prefixes = _prefixes(1, 2)
    exists, _ = _recording_exists()

    orchestrate.milestone_bases(milestones, prefixes, exists, "master")

    assert milestones == before
    assert prefixes == _prefixes(1, 2)
```

`Callable` is already imported in `tests/test_orchestrate.py` (`from collections.abc import Awaitable, Callable`, `:35`). `dag`, `models` and `orchestrate` are imported at `:49`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "milestone_bases" -v`
Expected: FAIL. Every test errors with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'milestone_bases'` (or `... 'MilestoneBlockersError'`).

- [ ] **Step 3: Implement the error, the prefix helper and `milestone_bases`**

In `src/agent_manager/orchestrate.py`, directly after `board_prefixes` (its last line is `    return prefixes`) and before `def board_claims(`, insert:

```python


class MilestoneBlockersError(ValueError):
    """A milestone would have to stack on two or more blockers at once.

    A milestone's base is one branch, and a merged base is a non-goal, so the
    human is told to chain the blockers instead. Subclasses `ValueError`, as
    `dag.DependencyCycleError` does: `ValueError` is already in `cli.HANDLED`,
    so a CLI caller gets the `ok: false` envelope and exit 3.
    """


def _blocker_branch(milestone_id: str, blocker_id: str, prefixes: Mapping[str, str]) -> str:
    """The blocker's `<prefix>-integrate`, or `ValueError` when it has no prefix.

    A missing or blank prefix is a caller bug, refused as `board_prefixes`
    refuses one, and never a `MilestoneBlockersError`.
    """
    prefix = prefixes.get(blocker_id)
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError(
            f"milestone {milestone_id}'s blocker {blocker_id} has no branch prefix "
            f"(got {prefix!r})"
        )
    return integration.integration_branch(prefix)


def milestone_bases(
    milestones: Sequence[models.CardNode],
    prefixes: Mapping[str, str],
    branch_exists: Callable[[str], bool],
    base_branch: str,
) -> dict[str, str]:
    """The branch each open milestone stacks on, keyed by id, in input order.

    `milestones` is every milestone root the caller knows, open or not; only
    the open ones (`dag.milestone_is_open`) get a key. A blocker id that is
    not a root in `milestones` is ignored. Each blocker is then exactly one of:

    - open: a stack candidate on its integrate branch, which its own run in
      this board creates. `branch_exists` is not asked.
    - landed (`census.is_landed`): ignored. `branch_exists` is not asked.
    - unlanded (`done`, or in play with nothing open under it): a candidate
      only if `branch_exists` says its integrate branch is there; otherwise
      assumed landed, today's behaviour.

    No candidate stacks on `base_branch`, one stacks on its branch, two or
    more raise `MilestoneBlockersError` for the first such milestone in input
    order. Pure apart from `branch_exists`, which is asked at most once per
    (milestone, blocker) pair. Blocker cycles are `dag.board_levels`' to
    refuse, not this function's.
    """
    by_id = {card.id: card for card in milestones}
    bases: dict[str, str] = {}
    for card in milestones:
        if card.id in bases or not dag.milestone_is_open(card):
            continue
        candidates: list[tuple[str, str]] = []
        unlanded: list[str] = []
        for blocker_id in dict.fromkeys(card.blocked_by):
            blocker = by_id.get(blocker_id)
            if blocker is None:
                continue
            if dag.milestone_is_open(blocker):
                candidates.append((blocker.id, _blocker_branch(card.id, blocker.id, prefixes)))
            elif not census.is_landed(blocker.status):
                branch = _blocker_branch(card.id, blocker.id, prefixes)
                if branch_exists(branch):
                    candidates.append((blocker.id, branch))
                    unlanded.append(blocker.id)
        if len(candidates) > 1:
            listed = ", ".join(blocker_id for blocker_id, _ in candidates)
            message = (
                f"milestone {card.id} is blocked by {len(candidates)} milestones that are "
                f"not landed ({listed}); a milestone stacks on at most one: "
                "chain them (A <- B <- C)"
            )
            if unlanded:
                message += (
                    f", or mark {', '.join(unlanded)} merged if that work has already landed"
                )
            raise MilestoneBlockersError(message)
        bases[card.id] = candidates[0][1] if candidates else base_branch
    return bases
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "milestone_bases" -v`
Expected: PASS (every `milestone_bases` test, all parametrizations).

- [ ] **Step 5: Run the full default suite**

Run: `uv run pytest`
Expected: PASS. This includes the existing `board_prefixes`/`board_claims`/`run_board` tests and the AST import guards (`test_the_engine_selecting_modules_never_import_pygents`, `test_only_orchestrate_imports_grafo`), since no imports changed.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: orchestrate.milestone_bases decides each board milestone's base (card 40ac07f3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Spec coverage map

| spec item | task / test |
|---|---|
| §2.1 `milestone_is_open`, `board_levels` uses it, docstring clause | Task 1 steps 5-7; test 18 = `test_milestone_is_open` + `test_board_levels_keeps_exactly_the_milestones_milestone_is_open_keeps` |
| §2.2 `MilestoneBlockersError(ValueError)`, no `cli.py` change | Task 2 step 3; tests 7, 8 assert `isinstance(..., ValueError)` / the type |
| §2.3 `LANDED_STATUSES`, `is_landed` | Task 1 steps 1-3; test 19 |
| §2.3 keys = open milestones, input order, `{}` when none | tests 1, 15, `..._of_no_open_milestones_is_empty` |
| §2.3 blockers deduplicated, unknown ids ignored | tests 12, 13 |
| §2.3 open / landed / unlanded classes; `branch_exists` call rules | tests 2-6, 9, 14, 17 (recording fake asserts calls) |
| §2.3 S0/S1/S2 and the silent-case decisions | tests 1, 4, 5, 6, 7, 8, 9 |
| §2.3 error message: M id, candidates in blocked_by order, `chain`, merged hint | tests 7, 8, Review Focus 3 |
| §2.3 first offending milestone in input order | Review Focus 3 test |
| §2.3 missing/blank prefix → `ValueError`, landed needs none | test 16 (both halves), Review Focus 4 |
| §2.3 purity, at most once per (M, B) | recording fakes throughout; Review Focus 1; `..._leaves_its_inputs_alone` |
| §2.4 chain A←B←C, and with A done-with-branch | tests 10, 11; Review Focus 2 |
| §3 out of scope | no `cli.py`, `run_board`, journal or README change in any task |
| §4 verification `uv run pytest` | Task 2 step 5 |
<!-- task-pipeline: validated -->
