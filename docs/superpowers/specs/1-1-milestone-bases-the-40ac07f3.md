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
