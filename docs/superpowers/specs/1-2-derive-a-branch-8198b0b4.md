# Branch prefixes for non-open blocker milestones — design

Date: 2026-10-04
Card: `8198b0b4` (subtask 1.2 of story `8fd3e3de`)
Status: approved scope (card), pre-plan

## 0. Parent spec and line references

Parent: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`.
"parent L<n>" below means a line in that file. Source line numbers are from
this branch's HEAD (`5acdcf0`).

Inherited constraints:

| constraint | parent |
|---|---|
| `prefix(B)` is derived exactly as `board_prefixes` derives it for open milestones: `<title slug>-<short id>`, or `<P>-<stem>` with `--branch-prefix P` | §1, L57-58 |
| The prefix must be computable for a blocker that is no longer open, so the derivation takes any milestone card, not only the open set | L58-60 |
| A blocker that is a milestone but not a root of the board's open set still uses the same function | L60-61 |
| `integrate_branch(B)` = `<prefix(B)>-integrate` | L44 |
| Prefix derivation comes before the `MilestoneBlockersError` refusal and before the claims check in `run_board`'s refusals | L52-54 |
| Boards with no inter-milestone `blocked_by` edges behave exactly as today | Compatibility L100 |
| JSON changes are additive only; the journal and watch schema stay 1 | L102 |
| Unit test: "prefix derivation for a non-open blocker" | Testing L107-110 |

## 1. Purpose

A milestone's branch prefix comes from two pieces of code:

- `cli.board_prefix_of(branch_prefix)` (`cli.py:1297-1310`) returns the
  closure that derives one card's prefix. That is `dag.task_stem(card)`, or
  `f"{P}-{stem}"` when `--branch-prefix P` is given. It reads only
  `card.title` and `card.id`, so it already works for a card of any status.
- `orchestrate.board_prefixes(milestones, branch_prefix_of)`
  (`orchestrate.py:2198-2223`) applies that closure to each card it is given.
  It refuses a blank or shared prefix and returns `{id: prefix}`.

Both callers, `run_board` (`orchestrate.py:2399-2401`) and
`cli.dry_run_board` (`cli.py:1332-1335`), pass only the open milestones, the
output of `dag.board_levels`. A `done` milestone therefore has no entry in the
map. `orchestrate.milestone_bases` (card 1.1, `orchestrate.py:2251-2307`) needs
`prefixes[B]` for a `done` blocker B whose integrate branch may still exist.
Without an entry, `_blocker_branch` (`orchestrate.py:2236-2248`) raises
`ValueError "... has no branch prefix"`.

This card lets `board_prefixes` also cover the **non-open milestones that
some given milestone lists as a blocker**. The prefix comes from the same
closure, so a blocker that has since become `done` gets the prefix it ran
under, and so the integrate branch it left behind. Nothing calls the new
input yet. Passing it from `run_board` and `dry_run_board` belongs to sibling
cards 1.3 and 1.4 (§4).

## 2. Behavior

### 2.1 `orchestrate.board_prefixes` gains `roots`

```python
def board_prefixes(
    milestones: Sequence[models.CardNode],
    branch_prefix_of: Callable[[models.CardNode], str],
    *,
    roots: Sequence[models.CardNode] = (),
) -> dict[str, str]
```

**Inputs.**

- `milestones`: unchanged. These are the open milestones a board run
  dispatches, in level order.
- `branch_prefix_of`: unchanged. It is the caller's derivation and is called
  once per card that gets an entry.
- `roots` (new, keyword-only, default `()`): every milestone root the caller
  read from the board, open or not. In production that is `board.roots()`,
  which the caller already reads once.

**Which cards get an entry.**

1. Every card in `milestones`, in input order. This is today's behavior,
   unchanged.
2. Then every card in `roots`, in `roots` order, that meets both conditions:
   - its id is not already a key from step 1, and not a key added earlier
     in step 2. A card listed twice gets one entry.
   - its id appears in the `blocked_by` of at least one card in
     `milestones`.

   These are the "blocker roots". The rule is **status-agnostic**. A
   `done`, `merged`, `canceled`, `archived`, or `todo`-with-nothing-open
   blocker all get an entry. Deciding which of them matter is
   `milestone_bases`' job (`census.is_landed`, `branch_exists`), not this
   function's. The function reads no status.

Not included:

- A `roots` card that no milestone lists as a blocker, for example an
  unrelated `done` milestone. Its prefix is never derived, so a closure
  that would fail on it (for example `dag.short_id` raising on a non-UUID
  id) cannot newly break a board.
- A blocker of a blocker. Only **direct** blockers of the given milestones
  are covered. In a chain A(`done`) ← B(`done`) ← C(open), B gets an entry
  and A does not. `milestone_bases` reads only direct blockers of open
  milestones (`orchestrate.py:2281-2285`).
- A `blocked_by` id that is not a card in `roots` (a story id, a deleted
  card, a typo). It is ignored, as `board_levels` and `milestone_bases`
  ignore it, and raises nothing.

**Checks.** Every entry, from step 1 or step 2, passes the same two checks
`board_prefixes` makes today, with today's messages:

- A prefix that is not a non-blank `str` is
  `ValueError("milestone <id> has no branch prefix (got <repr>)")`.
- A prefix already owned by another entry is
  `ValueError("milestones <first id> and <id> share the branch prefix <repr>")`.
  "Another entry" includes open milestones and blocker roots alike.

A blocker root that shares an open milestone's prefix is refused because the
two would name one integrate branch. If the blocked milestone stacked on the
`done` blocker, it would silently be stacking on the open milestone's branch.
The check runs in the order the entries are made, so the first conflict in
that order is the one reported. This matches today's "one problem at a time".

**Output.** A `dict[str, str]` with step-1 keys first, in `milestones`
order, followed by step-2 keys in `roots` order. For each id, the value is
exactly `branch_prefix_of(card)`. With `roots=()`, or with no non-open
blockers, the result is identical to today's, key for key and in the same
order. That is the behavior-preserving guarantee for every existing caller
and test.

**Purity.** No I/O beyond calling `branch_prefix_of`. The inputs are not
mutated.

**Docstring.** The `board_prefixes` docstring says the function now also
keys the given milestones' blocker roots from `roots`, regardless of their
status, so `milestone_bases` can name a non-open blocker's integrate
branch, and that the blank and shared checks cover those entries too. Its
first line stops saying "Each open milestone's" without qualification, for
example: "Each given milestone's branch prefix, then each of its blocker
roots', keyed by id".

### 2.2 `cli.board_prefix_of`: no code change

The closure is already card-agnostic. This card pins that with a test (§3,
test 9) and adds one docstring sentence: the derivation reads only the
card's title and id, never its status, so a milestone that is no longer open
gets the prefix it ran under (parent L57-61).

### 2.3 Same prefix before and after a blocker finishes

The point of this card (card text: "the same prefix it ran under"): for a
milestone card B, `board_prefixes([B], f)[B.id]` while B is open equals
`board_prefixes([M], f, roots=[B, M])[B.id]` after B is `done`, where M is
blocked by B. Both are `f(B)`. Feeding that map to `milestone_bases` with B
`done` and its integrate branch present yields `{M: f"{f(B)}-integrate"}`.
Under 1.1, the same call without `roots` raised `has no branch prefix`.

## 3. Tests

All tests are **unit tier, unmarked**. They call pure functions on in-memory
`CardNode`s, inject `branch_exists` as a lambda, and spawn nothing: no `git`,
`brd` or `claude` (CLAUDE.md "Test tiers": "pure functions and anything driven
through an injected fake"). Tests mirror `src`.

`tests/test_orchestrate.py`, next to the existing `board_prefixes` tests
(`tests/test_orchestrate.py:6813-6831`). Reuse `_board_milestone(n,
blocked_by=, status=, done_children=)` (`:6777`), `_prefix_of` (`:6806`) and
`_plan_id`. Write each test so it fails before the change: `roots` is an
unexpected keyword, or the blocker key is missing.

1. **done blocker gets its prefix**: open M2 blocked by M1 (`status="done"`,
   `done_children=True`), and `board_prefixes([m2], _prefix_of, roots=[m1, m2])`
   → `{m2.id: "p00000002", m1.id: "p00000001"}`, in that key order.
2. **every non-open status is covered**, parametrized over `done`, `merged`,
   `canceled`, `archived`, and `todo` with `done_children=True` → the blocker
   is keyed with `_prefix_of(blocker)`.
3. **unrelated non-open root is not derived**: `roots` holds a `done` M3 that
   nobody blocks on. The closure used records every card it is called on
   and raises for M3. The result has no M3 key, and the closure was never
   called with M3.
4. **only direct blockers**: in the chain M1(`done`) ← M2(`done`) ← M3(open),
   with `milestones=[m3]` and `roots=[m1, m2, m3]`, the keys are exactly
   `[m3.id, m2.id]`.
5. **unknown blocker id ignored**: M2 blocked by `_plan_id(99)`, which is not
   in `roots` → the result is `{m2.id: ...}` with no error.
6. **blocker listed by two milestones or twice** (M2 and M3 both blocked by
   `done` M1, and also `blocked_by=(1, 1)`) → one M1 entry, and the closure
   was called once for M1.
7. **checks cover blocker entries**:
   (a) a closure returning `""` for the `done` blocker only → `ValueError`
   matching `has no branch prefix`;
   (b) a closure returning the same prefix for open M2 and its `done` blocker
   M1 → `ValueError` matching `share the branch prefix`.
8. **behavior-preserving**: for the existing fixtures (open M1, M2, and a
   board where M2 is blocked by open M1), `board_prefixes(ms, _prefix_of)`
   and `board_prefixes(ms, _prefix_of, roots=ms)` both equal today's result,
   with identical `list(items())`. An open blocker already in `milestones`
   is not re-added. The three existing `board_prefixes` tests stay
   unchanged and green.
10. **same prefix before and after, end to end with `milestone_bases`**
    (§2.3): use `cli.board_prefix_of(None)` as the closure, with UUID ids
    from `_plan_id`. While M1 is open, `board_prefixes([m1, m2], f)[m1.id]`
    equals the value from `board_prefixes([m2], f, roots=[m1_done, m2])`,
    where `m1_done` is the same id and title with `status="done"`. Then
    `orchestrate.milestone_bases([m1_done, m2], prefixes, lambda b: True,
    "master")` → `{m2.id: f"{f(m1_done)}-integrate"}`. `_plan_id` ids are
    UUID-shaped (`tests/test_orchestrate.py:63`), so `dag.task_stem`
    accepts them.

`tests/test_cli.py`, next to the `board_prefix_of` tests
(`tests/test_cli.py:3266-3280`):

9. **status-agnostic derivation**, parametrized over `done`, `merged`, and
   `canceled`: `BOARD_CARD.model_copy(update={"status": status})` gives the
   same prefix as `BOARD_CARD` with `--branch-prefix` absent and with
   `"sprint9"`. This test passes immediately, since §2.2 needs no code
   change. It is a pinning test, and the plan says so rather than staging a
   fake red.

Verification: `uv run pytest` (default unit + git tiers) is green. There is
no lint or typecheck command (CLAUDE.md).

## 4. Out of scope

These belong to sibling cards of story `8fd3e3de` and later work.

- **1.3** (`5b772688`): passing `roots=` from `run_board`
  (`orchestrate.py:2399-2401`), the real `branch_exists` over git, calling
  `milestone_bases`, placing `MilestoneBlockersError` in the refusals,
  per-milestone bases in `_run_board_async`, and resume keeping
  `resumed.base_branch`.
- **1.4** (`5bfe746d`): passing `roots=` from `cli.dry_run_board`
  (`cli.py:1332-1335`), and the `base_branch` key and `base` column in the
  dry-run payload.
- **1.5** (`d8b6ed12`): the `e2e_fake` stacking scenarios.
- `--board --detach` (parent §2) and the README.
- Any change to `milestone_bases`, `_blocker_branch`, `board_claims`,
  `dag.board_levels`, `dag.task_stem`, or the claim set. Blocker roots get a
  prefix but claim nothing: `board_claims` still iterates only `milestones`.
- Any JSON, journal or CLI-surface change. None is needed, and the journal
  stays schema 1.

## 5. For the planner

**Files:**

- Modify `src/agent_manager/orchestrate.py:2198-2223`: add the `roots`
  keyword to `board_prefixes`, and rewrite its docstring (§2.1).
- Modify `src/agent_manager/cli.py:1297-1305`: add one docstring sentence
  to `board_prefix_of` (§2.2). No code change.
- Tests: `tests/test_orchestrate.py` (tests 1-8, 10) and
  `tests/test_cli.py` (test 9).

**Interface produced** (1.3 and 1.4 consume this exact signature):

- `orchestrate.board_prefixes(milestones: Sequence[models.CardNode], branch_prefix_of: Callable[[models.CardNode], str], *, roots: Sequence[models.CardNode] = ()) -> dict[str, str]`

The expected call from 1.3 and 1.4 is
`board_prefixes(milestones, prefix_of, roots=all_roots)`, where `all_roots`
is the single `board.roots()` read already in hand.

Suggested split: one task for `board_prefixes` (tests 1-8 and 10 written
first, strict TDD per the card), and one small task for the `cli` pinning
test and docstring (test 9). They may also be folded into one task.

**Review Focus seeds** (failure modes most likely to bite):

1. Deriving a prefix for **every** non-open root instead of only blockers,
   so an unrelated old milestone with an odd id or title newly breaks a board
   (test 3).
2. Key order or content changing for callers that pass no `roots`, which
   would break `board_claims` and dry-run ordering (test 8).
3. A blocker listed by several milestones being derived or keyed twice, or
   tripping the shared-prefix check against itself (test 6).
4. A blocker root whose prefix collides with an open milestone slipping
   through, so stacking would silently use the wrong integrate branch
   (test 7b).
5. Reading status inside `board_prefixes` and skipping `merged`/`canceled`
   blockers. That would duplicate `milestone_bases`' landed logic in a
   second place where it could drift (test 2).
