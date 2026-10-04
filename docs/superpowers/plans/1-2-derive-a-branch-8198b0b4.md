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

---

# Branch prefixes for non-open blocker milestones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `orchestrate.board_prefixes` gains a keyword-only `roots` so it also keys, status-agnostically, every root card that one of the given milestones lists as a direct blocker, letting `milestone_bases` name a `done` blocker's integrate branch.

**Architecture:** One pure function changes. Entries are made in two passes through one shared check helper (blank / shared prefix): first `milestones` in order (today's behavior, unchanged), then `roots` in order, keeping only cards whose id is a direct `blocked_by` of some given milestone and not yet keyed. `cli.board_prefix_of` gets a docstring sentence and a pinning test only. No caller passes `roots` yet (sibling cards 1.3 / 1.4).

**Tech Stack:** Python 3, pytest, `uv`. Models are `agent_manager.models.CardNode` (Pydantic).

**Spec:** `docs/superpowers/specs/1-2-derive-a-branch-8198b0b4.md` (reproduced above).

## Global Constraints

- Signature, exactly: `board_prefixes(milestones: Sequence[models.CardNode], branch_prefix_of: Callable[[models.CardNode], str], *, roots: Sequence[models.CardNode] = ()) -> dict[str, str]`.
- `prefix(B)` is derived exactly as for open milestones: the caller's `branch_prefix_of(card)`, value stored verbatim.
- With `roots=()` or no non-open blockers, the result is identical to today's, key for key and in the same order.
- Error messages unchanged: `ValueError(f"milestone {card.id} has no branch prefix (got {prefix!r})")` and `ValueError(f"milestones {owners[prefix]} and {card.id} share the branch prefix {prefix!r}")`.
- `board_prefixes` reads no status. Pure apart from calling `branch_prefix_of`; inputs not mutated.
- Only direct blockers of `milestones`; `blocked_by` ids not in `roots` are ignored silently.
- No change to `milestone_bases`, `_blocker_branch`, `board_claims`, `dag.board_levels`, `dag.task_stem`, `run_board`, `dry_run_board`, JSON, journal (stays schema 1) or CLI surface.
- All new tests are unit tier (unmarked): no `git`, `brd`, `claude` subprocess.
- Verification: `uv run pytest`. There is no lint or typecheck command.

## Review Focus

1. **An unrelated non-open root in `roots`** (e.g. an old `done` milestone with an odd id) — expected: its prefix is never derived, so it cannot newly break a board. Pinned by Task 1 test `test_board_prefixes_never_derives_a_root_nobody_blocks_on`.
2. **A caller passing no `roots`, or `roots` equal to the open milestones** — expected: identical keys, values and order to today, so `board_claims` and dry-run order do not move. Pinned by Task 1 test `test_board_prefixes_with_roots_but_no_non_open_blocker_is_todays_result`.
3. **A blocker named by several milestones, or twice by one** — expected: one entry, closure called once for it, no self-collision on the shared-prefix check. Pinned by Task 1 test `test_board_prefixes_keys_a_blocker_named_twice_once`.
4. **A `done` blocker whose prefix equals an open milestone's** — expected: `ValueError ... share the branch prefix`, never a silent stack on the wrong integrate branch. Pinned by Task 1 test `test_board_prefixes_refuses_a_blocker_root_sharing_an_open_milestones_prefix`.
5. **`merged` / `canceled` / `archived` / `todo`-with-nothing-open blockers** — expected: all keyed; filtering by status is `milestone_bases`' job. Pinned by Task 1 test `test_board_prefixes_keys_a_blocker_root_of_any_non_open_status`.

---

## File Structure

- Modify `src/agent_manager/orchestrate.py:2198-2223` — `board_prefixes`: new `roots` keyword, second pass, new docstring.
- Modify `src/agent_manager/cli.py:1297-1310` — `board_prefix_of`: one docstring sentence, no code change.
- Modify `tests/test_orchestrate.py` — insert nine tests (spec tests 1-8, 10) after `test_board_prefixes_refuses_two_milestones_on_one_prefix` (ends at line 6832), before `test_board_claims_unions_each_milestones_claims_first_occurrence_first` (line 6835).
- Modify `tests/test_cli.py` — insert spec test 9 after `test_board_prefix_of_with_a_prefix_joins_it_to_the_stem_and_never_reuses_it_verbatim` (ends at line 3279), before `BLANK_BOARD_PREFIX = ...` (line 3282).

---

### Task 1: `orchestrate.board_prefixes` keys the given milestones' blocker roots

**Files:**
- Modify: `src/agent_manager/orchestrate.py:2198-2223`
- Test: `tests/test_orchestrate.py` (insert after line 6832)

**Interfaces:**
- Consumes (existing, in `tests/test_orchestrate.py`): `_plan_id(n: int) -> str` (line 63, UUID-shaped, short id = `n` in 8 hex digits); `_board_milestone(n: int, *, blocked_by: tuple[int, ...] = (), status: str = "todo", done_children: bool = False) -> models.CardNode` (line 6777; title `f"milestone {n}"`, id `_plan_id(n)`, one story with one subtask whose status is `"done"` iff `done_children`); `_prefix_of(card) -> str` (line 6806, returns `f"p{dag.short_id(card.id)}"`, e.g. `"p00000001"`). Existing production: `cli.board_prefix_of(branch_prefix: str | None) -> Callable[[models.CardNode], str]`; `orchestrate.milestone_bases(milestones, prefixes, branch_exists, base_branch) -> dict[str, str]`; `dag.milestone_is_open(card) -> bool`.
- Produces: `orchestrate.board_prefixes(milestones: Sequence[models.CardNode], branch_prefix_of: Callable[[models.CardNode], str], *, roots: Sequence[models.CardNode] = ()) -> dict[str, str]` — consumed by sibling cards 1.3 and 1.4 as `board_prefixes(milestones, prefix_of, roots=all_roots)`.

Background for the engineer: a milestone is "open" (`dag.milestone_is_open`) when its status is neither finished (`done`, `merged`) nor out of play (`canceled`, `archived`) and some card under it is not finished. So `_board_milestone(1, status="todo", done_children=True)` is **not** open even though its status is `todo`. `board_prefixes` must not care: it reads no status at all.

- [ ] **Step 1: Write the failing tests**

Insert this block in `tests/test_orchestrate.py` directly after the end of `test_board_prefixes_refuses_two_milestones_on_one_prefix` (after line 6832, keeping two blank lines before and after):

```python
# ── board_prefixes over blocker roots (card 8198b0b4) ───────────────────────


def _prefix_recording(calls: list[str], prefix_of: Callable[[models.CardNode], str] = _prefix_of):
    """`prefix_of`, recording the id of every card it is called on in `calls`."""

    def prefix(card: models.CardNode) -> str:
        calls.append(card.id)
        return prefix_of(card)

    return prefix


def test_board_prefixes_keys_a_done_blocker_root_after_the_given_milestones():
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))

    prefixes = orchestrate.board_prefixes([blocked], _prefix_of, roots=[blocker, blocked])

    assert list(prefixes.items()) == [(blocked.id, "p00000002"), (blocker.id, "p00000001")]


@pytest.mark.parametrize(
    "status, done_children",
    [
        ("done", True),
        ("merged", True),
        ("canceled", False),
        ("archived", False),
        ("todo", True),
    ],
)
def test_board_prefixes_keys_a_blocker_root_of_any_non_open_status(status, done_children):
    """Review Focus 5: which blockers matter is `milestone_bases`' call, not this one's."""
    blocker = _board_milestone(1, status=status, done_children=done_children)
    blocked = _board_milestone(2, blocked_by=(1,))
    assert not dag.milestone_is_open(blocker)

    prefixes = orchestrate.board_prefixes([blocked], _prefix_of, roots=[blocker, blocked])

    assert prefixes[blocker.id] == _prefix_of(blocker)


def test_board_prefixes_never_derives_a_root_nobody_blocks_on():
    """Review Focus 1: an unrelated old milestone cannot newly break a board."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    unrelated = _board_milestone(3, status="done", done_children=True)
    calls: list[str] = []

    def prefix_of(card: models.CardNode) -> str:
        calls.append(card.id)
        if card.id == unrelated.id:
            raise ValueError(f"not a card id: {card.id!r}")
        return _prefix_of(card)

    prefixes = orchestrate.board_prefixes(
        [blocked], prefix_of, roots=[blocker, blocked, unrelated]
    )

    assert list(prefixes) == [blocked.id, blocker.id]
    assert unrelated.id not in calls


def test_board_prefixes_keys_only_direct_blockers_of_the_given_milestones():
    """A(done) <- B(done) <- C(open): B is C's blocker and gets a key, A does not."""
    a = _board_milestone(1, status="done", done_children=True)
    b = _board_milestone(2, blocked_by=(1,), status="done", done_children=True)
    c = _board_milestone(3, blocked_by=(2,))

    prefixes = orchestrate.board_prefixes([c], _prefix_of, roots=[a, b, c])

    assert list(prefixes) == [c.id, b.id]


def test_board_prefixes_ignores_a_blocker_id_that_is_not_a_root():
    blocked = models.CardNode(
        id=_plan_id(2), title="milestone 2", status="todo", blocked_by=[_plan_id(99)]
    )

    prefixes = orchestrate.board_prefixes([blocked], _prefix_of, roots=[blocked])

    assert prefixes == {blocked.id: "p00000002"}


def test_board_prefixes_keys_a_blocker_named_twice_once():
    """Review Focus 3: one entry, one derivation, no collision with itself."""
    blocker = _board_milestone(1, status="done", done_children=True)
    two = _board_milestone(2, blocked_by=(1, 1))
    three = _board_milestone(3, blocked_by=(1,))
    calls: list[str] = []

    prefixes = orchestrate.board_prefixes(
        [two, three], _prefix_recording(calls), roots=[blocker, two, three, blocker]
    )

    assert list(prefixes.items()) == [
        (two.id, "p00000002"),
        (three.id, "p00000003"),
        (blocker.id, "p00000001"),
    ]
    assert calls.count(blocker.id) == 1


@pytest.mark.parametrize("prefix", ["", "   ", None])
def test_board_prefixes_refuses_a_blank_prefix_for_a_blocker_root(prefix):
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))

    def prefix_of(card: models.CardNode) -> str:
        return prefix if card.id == blocker.id else _prefix_of(card)

    with pytest.raises(ValueError, match=f"milestone {blocker.id} has no branch prefix"):
        orchestrate.board_prefixes([blocked], prefix_of, roots=[blocker, blocked])


def test_board_prefixes_refuses_a_blocker_root_sharing_an_open_milestones_prefix():
    """Review Focus 4: both would name one integrate branch; stacking would use the wrong one."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))

    with pytest.raises(
        ValueError,
        match=f"milestones {blocked.id} and {blocker.id} share the branch prefix 'm14'",
    ):
        orchestrate.board_prefixes([blocked], lambda card: "m14", roots=[blocker, blocked])


@pytest.mark.parametrize(
    "milestones",
    [
        [_board_milestone(1), _board_milestone(2)],
        [_board_milestone(1), _board_milestone(2, blocked_by=(1,))],
    ],
    ids=["independent", "open-blocker"],
)
def test_board_prefixes_with_roots_but_no_non_open_blocker_is_todays_result(milestones):
    """Review Focus 2: an open blocker already given is not re-added; order is untouched."""
    calls: list[str] = []

    today = orchestrate.board_prefixes(milestones, _prefix_of)
    with_roots = orchestrate.board_prefixes(milestones, _prefix_recording(calls), roots=milestones)

    assert list(with_roots.items()) == list(today.items())
    assert list(today.items()) == [
        (milestones[0].id, "p00000001"),
        (milestones[1].id, "p00000002"),
    ]
    assert calls == [milestones[0].id, milestones[1].id]


def test_a_blocker_keeps_its_prefix_once_done_and_milestone_bases_stacks_on_it():
    """Spec 2.3: the prefix a blocker ran under names the integrate branch it left behind."""
    prefix_of = cli.board_prefix_of(None)
    open_blocker = _board_milestone(1)
    blocked = _board_milestone(2, blocked_by=(1,))
    done_blocker = _board_milestone(1, status="done", done_children=True)
    assert done_blocker.id == open_blocker.id and done_blocker.title == open_blocker.title

    while_open = orchestrate.board_prefixes([open_blocker, blocked], prefix_of)
    once_done = orchestrate.board_prefixes([blocked], prefix_of, roots=[done_blocker, blocked])

    assert once_done[done_blocker.id] == while_open[open_blocker.id] == prefix_of(done_blocker)
    assert orchestrate.milestone_bases(
        [done_blocker, blocked], once_done, lambda branch: True, "master"
    ) == {blocked.id: f"{prefix_of(done_blocker)}-integrate"}
```

Notes on the tests:
- `_prefix_recording` is defined after (the name `_recording` is already taken by an unrelated helper at line 5730, so it must not be reused) `_prefix_of` (line 6806), so its default argument resolves. `Callable` is already imported at the top of the file (`from collections.abc import Awaitable, Callable`).
- In `test_board_prefixes_keys_a_blocker_root_of_any_non_open_status`, `canceled`/`archived` use `done_children=False` on purpose: they are non-open by status alone. The `assert not dag.milestone_is_open(blocker)` guards that every case really is a non-open blocker.
- The blank-prefix test reuses the existing three blank values (`""`, `"   "`, `None`), matching `test_board_prefixes_refuses_a_blank_prefix`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "board_prefixes or blocker_keeps_its_prefix" -v`
Expected: the three existing `board_prefixes` tests PASS; every new test FAILS with `TypeError: board_prefixes() got an unexpected keyword argument 'roots'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/orchestrate.py`, replace the whole of `board_prefixes` (lines 2198-2223) with:

```python
def board_prefixes(
    milestones: Sequence[models.CardNode],
    branch_prefix_of: Callable[[models.CardNode], str],
    *,
    roots: Sequence[models.CardNode] = (),
) -> dict[str, str]:
    """Each given milestone's branch prefix, then each of its blocker roots', keyed by id.

    The given milestones come first, in input order. Then each card in `roots`
    (every milestone root the caller read, open or not) that one of them lists
    in `blocked_by` and that has no key yet, in `roots` order, whatever its
    status: a blocker that is no longer open keeps the prefix it ran under, so
    `milestone_bases` can name the integrate branch it left behind. Whether that
    blocker matters is `milestone_bases`' call, not this one's. Only direct
    blockers are keyed; a `roots` card nobody here blocks on is never derived,
    and a `blocked_by` id that is not in `roots` is ignored. With no `roots`
    the result is the given milestones' alone.

    `branch_prefix_of` is the caller's: deriving a prefix is not this module's
    job. This only checks it, for every entry alike. A prefix that is not a
    non-blank string is `ValueError`, as `run_milestone` refuses a missing one.
    So is a prefix two entries share: two milestones would both claim
    `branch:<prefix>-integrate`, and the deduplicated board claim set would hide
    that until the second milestone's own pre-flight refused it mid-run; a
    blocker root sharing one would have its blocked milestone stack on the
    other's branch.
    """
    prefixes: dict[str, str] = {}
    owners: dict[str, str] = {}

    def add(card: models.CardNode) -> None:
        prefix = branch_prefix_of(card)
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError(f"milestone {card.id} has no branch prefix (got {prefix!r})")
        if prefix in owners:
            raise ValueError(
                f"milestones {owners[prefix]} and {card.id} share the branch prefix {prefix!r}"
            )
        owners[prefix] = card.id
        prefixes[card.id] = prefix

    for card in milestones:
        add(card)
    blocker_ids = {blocker_id for card in milestones for blocker_id in card.blocked_by}
    for card in roots:
        if card.id in blocker_ids and card.id not in prefixes:
            add(card)
    return prefixes
```

Why this shape:
- Step 1 (`for card in milestones: add(card)`) is byte-for-byte today's loop body, so existing behavior, including a duplicated card in `milestones` being refused as sharing its own prefix, is unchanged.
- `blocker_ids` is computed from `milestones` only, never from cards added in step 2, which is what makes it "direct blockers only".
- `card.id not in prefixes` both skips an open blocker already keyed in step 1 and keeps a card listed twice in `roots` to one entry and one derivation.
- No status is read anywhere.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "board_prefixes or blocker_keeps_its_prefix or milestone_bases or board_claims" -v`
Expected: all PASS (the existing `board_prefixes`, `milestone_bases` and `board_claims` tests included).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: board_prefixes keys the given milestones' non-open blocker roots (card 8198b0b4)"
```

---

### Task 2: pin that `cli.board_prefix_of` is status-agnostic

**Files:**
- Modify: `src/agent_manager/cli.py:1297-1303` (docstring only)
- Test: `tests/test_cli.py` (insert after line 3279)

**Interfaces:**
- Consumes (existing, in `tests/test_cli.py`): `BOARD_CARD = models.CardNode(id=SOME_CARD, title="Milestone 14: run the board", status="todo")` (line 3262), `SOME_CARD = "cbe34d00-9d8d-4f41-9c94-f99e665771b0"` (line 3007); `cli.board_prefix_of(branch_prefix: str | None) -> Callable[[models.CardNode], str]`.
- Produces: nothing new; `board_prefix_of`'s signature and behavior are unchanged.

This test is a **pinning test**: `board_prefix_of` already reads only `card.title` and `card.id` (via `dag.task_stem`), so it passes before any change. Do not fake a red; run it once to confirm it passes, then add the docstring sentence that states the guarantee it pins.

- [ ] **Step 1: Write the pinning test**

Insert in `tests/test_cli.py` directly after `test_board_prefix_of_with_a_prefix_joins_it_to_the_stem_and_never_reuses_it_verbatim` (after line 3279, two blank lines before and after):

```python
@pytest.mark.parametrize("status", ["done", "merged", "canceled"])
@pytest.mark.parametrize("branch_prefix", [None, "sprint9"])
def test_board_prefix_of_ignores_status_so_a_finished_milestone_keeps_its_prefix(
    status, branch_prefix
):
    """Card 8198b0b4: a blocker that is no longer open gets the prefix it ran under."""
    prefix_of = cli.board_prefix_of(branch_prefix)
    finished = BOARD_CARD.model_copy(update={"status": status})

    assert prefix_of(finished) == prefix_of(BOARD_CARD)
```

- [ ] **Step 2: Run the test and confirm it already passes**

Run: `uv run pytest tests/test_cli.py -k board_prefix_of -v`
Expected: PASS for all six new parametrized cases and the two existing `board_prefix_of` tests. (Passing immediately is correct: spec §2.2 needs no code change; this test pins existing behavior.)

- [ ] **Step 3: Add the docstring sentence**

In `src/agent_manager/cli.py`, replace the `board_prefix_of` docstring:

```python
    """Run-board spec 3.2: how one milestone's branch prefix is derived under `--board`.

    With `--branch-prefix` omitted the prefix is the milestone card's own
    `dag.task_stem`; given, it is `<branch_prefix>-<stem>`, never the given
    value verbatim, so two milestones can never share it. Checking the result
    (blank, shared) is `orchestrate.board_prefixes`'s job, not this one's.
    """
```

with:

```python
    """Run-board spec 3.2: how one milestone's branch prefix is derived under `--board`.

    With `--branch-prefix` omitted the prefix is the milestone card's own
    `dag.task_stem`; given, it is `<branch_prefix>-<stem>`, never the given
    value verbatim, so two milestones can never share it. It reads only the
    card's title and id, never its status, so a milestone that is no longer
    open gets the prefix it ran under. Checking the result (blank, shared) is
    `orchestrate.board_prefixes`'s job, not this one's.
    """
```

- [ ] **Step 4: Run the full default suite**

Run: `uv run pytest`
Expected: all PASS (default `unit` + `git` tiers).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "test: pin that board_prefix_of ignores a milestone's status (card 8198b0b4)"
```

---

## Spec coverage map

| spec item | where |
|---|---|
| §2.1 signature, `roots` keyword-only default `()` | Task 1 Step 3 |
| §2.1 step 1 entries unchanged, input order | Task 1 Step 3; test `..._with_roots_but_no_non_open_blocker_is_todays_result` + existing three |
| §2.1 step 2: direct blockers in `roots` order, deduplicated | Task 1 Step 3; tests 1, 4, 6 |
| §2.1 status-agnostic | tests 2, 1 |
| §2.1 not included: unrelated root, blocker of blocker, unknown id | tests 3, 4, 5 |
| §2.1 checks cover blocker entries, today's messages | tests 7a (`..._blank_prefix_for_a_blocker_root`), 7b |
| §2.1 output order and behavior-preserving guarantee | tests 1, 6, 8 |
| §2.1 docstring rewrite, first line | Task 1 Step 3 |
| §2.2 `board_prefix_of` docstring sentence, pinning test | Task 2 |
| §2.3 same prefix before/after + `milestone_bases` | test 10 (`test_a_blocker_keeps_its_prefix_once_done_and_milestone_bases_stacks_on_it`) |
| §3 unit tier only | all tests call pure functions; no subprocess |
| §4 out of scope (callers, `board_claims`, JSON) | not touched by any task |
<!-- task-pipeline: validated -->
