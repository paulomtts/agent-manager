<!-- task-pipeline: validated -->
# Task 20abebdf — Find a milestone by id or title

Story: b84d47e1 "Census: read a whole milestone from the board" (milestone 99e178cb). Narrows addendum decision O1 (`docs/superpowers/specs/2026-09-24-orchestration-design.md:26-42`) to its first function. Blocked by c1d53fc4, which is done. In this worktree `CardNode` already carries `blocked_by` and `created_at` (`src/agent_manager/models.py:186-200`) and `board.roots()` exists (`board.py:204`). This card uses neither of them. It needs only `CardNode.id` and `CardNode.title`.

## Scope

- New module `src/agent_manager/census.py`. It is pure: no I/O, no subprocess, no `brd`. Its module docstring says so, in the style of `src/agent_manager/dag.py:1-15`. It must not import `cli`, because `cli` will import `census` in a later story.
- `class MilestoneNotFoundError(ValueError)` is the one exception `find_milestone` raises, for both the zero-match and the many-match case. It subclasses `ValueError`, which is already in `cli.HANDLED` (`cli.py:760-766`), so a later CLI caller gets an `ok: false` envelope without any change to `cli.py`. This card does not edit `cli.py` or `HANDLED`.
- `find_milestone(roots: list[CardNode], needle: str | int) -> CardNode` is ported exactly from `~/Code/leave-me-alone/plugins/leave-me-alone/scripts/census.mjs:62-98`.

Out of scope, and owned by sibling 65275a49: `order_siblings`, `flatten_milestone`, `Census`/`StoryPlan`/`SubtaskPlan`, the blocked-to-todo flattening, and any real-`brd` test. Also out of scope: CLI wiring, parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, git-measured review counts, and verification discovery.

## Observable behaviour

1. `wanted = str(needle).strip()`. The `str` is there because callers may pass ints such as `2` or `12`. `roots` of `None` is treated as `[]`.
2. **Exact id.** The first root whose `id == wanted` is returned.
3. **Exact title.** Otherwise, the first root whose `title.lower() == wanted.lower()` is returned outright. There is no ambiguity check, even if the needle is also a substring of other titles.
4. **Substring.** Otherwise, candidates are the roots where `wanted.lower()` occurs in `title.lower()`. If `wanted` matches `^[0-9]+$`, only the first occurrence (`str.find`) is inspected. The span is widened left and right over adjacent digits in the title, and the root stays a candidate only when `end - start <= len(wanted)`. So `"2"` does not resolve `"Milestone 12"`, but `"12"` does. Do not scan later occurrences: the port keeps the JS behaviour.
5. Exactly one candidate is returned.

## Error paths

Both raise `MilestoneNotFoundError`. The messages match the JS byte for byte, including the em dash:

- Zero candidates: `no milestone card matching "<wanted>" — root cards are: <root titles joined by ", ">`, or `(none)` when `roots` is empty.
- Two or more candidates: `ambiguous milestone "<wanted>" — matches: <candidate titles joined by ", ">`. The function never guesses.

## Tests — `tests/test_census.py`

Tier: **pure-function unit tests** for every test below, per design spec §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`). Pure modules get unit tests ported from their `.test.mjs`, in the style of `tests/test_dag.py`. The tests use no `tmp_path`, no subprocess, no `brd` and no fake claude. The module docstring states the tier, as `tests/test_cli.py:1-13` does.

Fixture, ported from `census.test.mjs`: `ID(n) = f"{n}0000000-0000-4000-8000-000000000000"`. Nodes are `CardNode` with `created_at` `2026-01-0n`. `TREE` is node 1, titled `Milestone 12: CSV export`. It has a story child `Story: CSV writer` with subtasks node 4, and node 5 blocked by node 4. It has a second story child, `Story: Document it`, blocked by `ID(2)`.

The first seven tests map one-for-one to `census.test.mjs:70-104`:

1. Exact id match returns the root (unit).
2. Case-insensitive title substring `'csv export'` returns TREE (unit).
3. `'nonexistent'` raises `MilestoneNotFoundError` whose message contains `no milestone card` (unit).
4. Roots `[TREE, node 9 'Milestone 13: CSV import']` with needle `'csv'` raise, with a message containing `ambiguous` (unit).
5. Numeric needle `'2'` and int `2` against `[TREE]` both raise `no milestone card` (unit).
6. With roots `[longer 'Milestone 12: CSV export, revisited', TREE]`, the needle `'Milestone 12: CSV export'` and its lowercase variant both return TREE (unit).
7. Int `12` resolves a card titled `Milestone 12` (unit).

The spec adds three more:

8. `'2'` against a single root `Milestone 12` raises `no milestone card` (unit).
9. The zero-match message lists every root title joined by `", "`. With empty roots it says `(none)` (unit).
10. The two-match message lists both matching titles (unit).

The whole default suite, including `tests/e2e`, stays green under `uv run pytest`.

---

# Find a Milestone by Id or Title Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure `census.find_milestone(roots, needle)` that resolves one milestone root card by exact id, exact case-insensitive title, or a single unambiguous title substring (with a digit-run guard), raising `MilestoneNotFoundError` otherwise.

**Architecture:** One new pure module, `src/agent_manager/census.py`, holding the exception class, the lookup function, and a small private substring-hit helper. It imports only `re` and `agent_manager.models.CardNode`, never `cli`. Tests live in `tests/test_census.py` as pure unit tests, one for one with `census.test.mjs:70-104` plus the spec's additions.

**Tech Stack:** Python 3, Pydantic (`CardNode` model), pytest, run via `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-find-a-milestone-by-id-20abebdf/docs/superpowers/specs/task-find-a-milestone-by-id-20abebdf-design.md` (prepended verbatim above).

**Worktree:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-find-a-milestone-by-id-20abebdf`, branch `m3/task-find-a-milestone-by-id-20abebdf`. All paths below are relative to that worktree root. The branch was cut from `m3/task-carry-blocked-by-and-c1d53fc4`, so `CardNode` already has `blocked_by: list[str]` and `created_at: str | None` (`src/agent_manager/models.py:186-201`). Nothing else from sibling subtasks exists here: there is no `src/agent_manager/census.py` and no `tests/test_census.py` yet.

## Global Constraints

- `census.py` is pure: no I/O, no subprocess, no `brd`. Its module docstring ends with the sentence `This module is pure: no I/O, no subprocesses, no ``brd``.` (the `dag.py:14` wording).
- `census.py` must not import `agent_manager.cli` (cli will import census later).
- Do not edit `src/agent_manager/cli.py` or `HANDLED`; `MilestoneNotFoundError` subclasses `ValueError`, which `HANDLED` (`cli.py:760-766`) already contains.
- Error messages are byte-exact with the JS, including the em dash `—` (U+2014): `no milestone card matching "<wanted>" — root cards are: <titles joined by ", " or (none)>` and `ambiguous milestone "<wanted>" — matches: <titles joined by ", ">`.
- The numeric digit guard inspects only the FIRST `str.find` hit; do not scan later occurrences.
- Numeric means ASCII `[0-9]+` only, as in the JS regex; do not use `str.isdigit()` (it accepts non-ASCII digits).
- Do not implement `order_siblings`, `flatten_milestone`, `Census`, `StoryPlan`, `SubtaskPlan`, or any real-`brd` test (sibling 65275a49 owns them). No CLI wiring.
- Tests are pure unit tests: no `tmp_path`, no subprocess, no `brd`, no fake claude.
- Verification: `uv run pytest` (whole suite, including `tests/e2e`) must be green at the end.

## Review Focus

- A needle with surrounding whitespace (`"  csv export  "`, `" <id> "`) is stripped before matching and resolves as if typed cleanly. Pinned in Task 1 (`test_needle_is_stripped_before_matching`).
- `roots=None` behaves as an empty list: it raises `MilestoneNotFoundError` with `(none)`, not a `TypeError`. Pinned in Task 1 (`test_none_roots_is_treated_as_empty`).
- Two roots with the identical exact title: the first wins with no ambiguity error (the JS `find` behaviour). Pinned in Task 1 (`test_exact_title_returns_first_of_duplicate_titles`).
- A numeric needle whose first hit sits inside a longer run with digits to the RIGHT (`"2"` vs `"Milestone 23"`) is rejected, not just runs extending left. Pinned in Task 2 (`test_numeric_needle_rejects_run_extending_right`).
- A numeric needle whose first hit is inside a longer run but which appears standalone later (`"2"` vs `"Milestone 12, part 2"`) is still rejected, because only the first hit is inspected (port-exact). Pinned in Task 2 (`test_numeric_guard_inspects_only_the_first_hit`).

---

### Task 1: `census.py` with `MilestoneNotFoundError` and the id / exact-title / substring lookup

**Files:**
- Create: `src/agent_manager/census.py`
- Test: `tests/test_census.py` (new; top-level `tests/` alongside `tests/test_dag.py`, mirroring `src/agent_manager/census.py`)

**Interfaces:**
- Consumes: `agent_manager.models.CardNode` (fields `id: str`, `title: str`, `status: str`, `blocked_by: list[str]`, `created_at: str | None`, `children: list[CardNode]`).
- Produces:
  - `class MilestoneNotFoundError(ValueError)` in `agent_manager.census`.
  - `def find_milestone(roots: list[CardNode] | None, needle: str | int) -> CardNode` in `agent_manager.census`.
  - Test-module fixtures `ID(n: int) -> str`, `node(n: int, title: str, **extra) -> CardNode`, and `TREE: CardNode` in `tests/test_census.py` (Task 2 appends tests that use them).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_census.py` with exactly this content:

```python
"""Behaviour of `census.find_milestone`, ported from `census.test.mjs:70-104`.

Tier: pure-function unit tests, per design §14 lines 477-492. `census` is a
pure module, so these tests build `CardNode` values in memory and call the
function directly -- no `tmp_path`, no subprocess, no `brd`, no fake claude.
Real-board census tests belong to sibling cards, not here.
"""

import ast
import inspect

import pytest

from agent_manager import census
from agent_manager.census import MilestoneNotFoundError, find_milestone
from agent_manager.models import CardNode


def ID(n: int) -> str:
    return f"{n}0000000-0000-4000-8000-000000000000"[:36]


def node(n: int, title: str, **extra) -> CardNode:
    fields = {
        "id": ID(n),
        "title": title,
        "status": "todo",
        "blocked_by": [],
        "created_at": f"2026-01-0{n}T00:00:00Z",
        "children": [],
    }
    fields.update(extra)
    return CardNode(**fields)


TREE = node(
    1,
    "Milestone 12: CSV export",
    children=[
        node(
            2,
            "Story: CSV writer",
            children=[
                node(4, "feat: write rows"),
                node(5, "feat: quoting", blocked_by=[ID(4)]),
            ],
        ),
        node(
            3,
            "Story: Document it",
            blocked_by=[ID(2)],
            status="blocked",
            children=[node(6, "docs: usage", status="blocked")],
        ),
    ],
)


# --- census.test.mjs:70-104, one for one ---------------------------------


def test_matches_a_root_card_by_exact_id():
    assert find_milestone([TREE], ID(1)).title == "Milestone 12: CSV export"


def test_matches_by_case_insensitive_title_substring():
    assert find_milestone([TREE], "csv export").id == ID(1)


def test_fails_loudly_when_nothing_matches():
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([TREE], "nonexistent")


def test_fails_loudly_on_ambiguity_rather_than_guessing():
    other = node(9, "Milestone 13: CSV import")
    with pytest.raises(MilestoneNotFoundError, match="ambiguous"):
        find_milestone([TREE, other], "csv")


def test_exact_title_match_wins_outright_over_a_longer_title_containing_it():
    longer = node(9, "Milestone 12: CSV export, revisited")
    assert find_milestone([longer, TREE], "Milestone 12: CSV export").id == ID(1)
    # Case-insensitive too.
    assert find_milestone([longer, TREE], "milestone 12: csv export").id == ID(1)


# --- spec additions ------------------------------------------------------


def test_zero_match_message_lists_every_root_title():
    other = node(9, "Milestone 13: CSV import")
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone([TREE, other], "nonexistent")
    assert str(caught.value) == (
        'no milestone card matching "nonexistent" — root cards are: '
        "Milestone 12: CSV export, Milestone 13: CSV import"
    )


def test_zero_match_message_says_none_when_there_are_no_roots():
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone([], "anything")
    assert str(caught.value) == (
        'no milestone card matching "anything" — root cards are: (none)'
    )


def test_two_match_message_lists_both_matching_titles():
    other = node(9, "Milestone 13: CSV import")
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone([TREE, other], "csv")
    assert str(caught.value) == (
        'ambiguous milestone "csv" — matches: '
        "Milestone 12: CSV export, Milestone 13: CSV import"
    )


# --- review focus --------------------------------------------------------


def test_needle_is_stripped_before_matching():
    assert find_milestone([TREE], "  csv export  ").id == ID(1)
    assert find_milestone([TREE], f" {ID(1)}\n").id == ID(1)


def test_none_roots_is_treated_as_empty():
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone(None, "anything")
    assert str(caught.value).endswith("root cards are: (none)")


def test_exact_title_returns_first_of_duplicate_titles():
    first = node(7, "Milestone 3")
    second = node(8, "Milestone 3")
    assert find_milestone([first, second], "milestone 3").id == ID(7)


# --- the contract with cli.HANDLED and the purity rule -------------------


def test_milestone_not_found_error_is_a_value_error():
    # ValueError is already in cli.HANDLED, so a later CLI caller gets an
    # ok:false envelope with no change to cli.py.
    assert issubclass(MilestoneNotFoundError, ValueError)


def test_census_imports_neither_cli_nor_subprocess():
    tree = ast.parse(inspect.getsource(census))
    imported: set[str] = set()
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            imported.update(alias.name for alias in stmt.names)
        elif isinstance(stmt, ast.ImportFrom):
            imported.add(stmt.module or "")
            imported.update(
                f"{stmt.module}.{alias.name}" for alias in stmt.names
            )
    assert not any(
        name == "agent_manager.cli" or name.endswith(".cli") for name in imported
    )
    assert "subprocess" not in imported
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_census.py -v`
Expected: collection ERROR with `ModuleNotFoundError: No module named 'agent_manager.census'` (or `ImportError: cannot import name 'census' from 'agent_manager'`).

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/census.py` with exactly this content (the substring step has no digit guard yet; Task 2 adds it):

```python
"""Find one milestone among the board's root cards.

`--milestone` used to be a small integer. A UUID is not typeable, so a title
substring is accepted too -- but never guessed at: zero matches or two matches
is an error naming what was on the board, and the caller decides what to type
next. Ported exactly from `census.mjs:62-98` in the leave-me-alone plugin,
including its messages, so the two tools refuse in the same words.

`MilestoneNotFoundError` subclasses `ValueError` on purpose: `ValueError` is
already in `cli.HANDLED`, so a CLI caller turns it into an `ok: false`
envelope without this module importing `cli` (which will import this module).

This module is pure: no I/O, no subprocesses, no ``brd``.
"""

from agent_manager.models import CardNode


class MilestoneNotFoundError(ValueError):
    """No root card, or more than one, matches the milestone the caller typed."""


def find_milestone(roots: list[CardNode] | None, needle: str | int) -> CardNode:
    """The one root card `needle` names: exact id, exact title, or one substring.

    Order matters. An exact id wins. Then an exact case-insensitive title wins
    outright, even when it is also a substring of another root's title. Only
    then is a substring tried, and it must match exactly one root.
    """
    wanted = str(needle).strip()
    pool = list(roots or [])

    for root in pool:
        if root.id == wanted:
            return root

    lowered = wanted.lower()
    for root in pool:
        if root.title.lower() == lowered:
            return root

    matches = [root for root in pool if lowered in root.title.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        listed = ", ".join(root.title for root in pool) or "(none)"
        raise MilestoneNotFoundError(
            f'no milestone card matching "{wanted}" — root cards are: {listed}'
        )
    listed = ", ".join(match.title for match in matches)
    raise MilestoneNotFoundError(
        f'ambiguous milestone "{wanted}" — matches: {listed}'
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_census.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/census.py tests/test_census.py
git commit -m "feat(census): find a milestone root by id, exact title or one substring"
```

---

### Task 2: The numeric digit-run guard

**Files:**
- Modify: `src/agent_manager/census.py` (add `import re`, `_NUMERIC`, `_is_digit`, `_substring_hit`; replace the `matches = [...]` line in `find_milestone`)
- Test: `tests/test_census.py` (append tests at the end of the file)

**Interfaces:**
- Consumes: `find_milestone`, `MilestoneNotFoundError` from Task 1; test fixtures `ID`, `node`, `TREE` already defined at the top of `tests/test_census.py`.
- Produces: no new public names. Private helpers `_substring_hit(title: str, lowered: str, is_numeric: bool) -> bool` and `_is_digit(ch: str) -> bool` in `agent_manager.census`.

- [ ] **Step 1: Write the failing tests**

Append exactly this to the end of `tests/test_census.py`:

```python
# --- the numeric digit-run guard (census.mjs:76-92) ----------------------


def test_numeric_needle_does_not_resolve_via_a_longer_digit_run():
    # census.test.mjs:87-92. "2" must not silently resolve
    # "Milestone 12: CSV export" -- the "2" typed is not this card's "12".
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([TREE], 2)
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([TREE], "2")


def test_numeric_needle_resolves_a_card_whose_whole_digit_run_equals_it():
    # census.test.mjs:101-104.
    twelve = node(9, "Milestone 12")
    assert find_milestone([twelve], 12).id == ID(9)


def test_two_does_not_resolve_milestone_twelve():
    twelve = node(9, "Milestone 12")
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone([twelve], "2")
    assert str(caught.value) == (
        'no milestone card matching "2" — root cards are: Milestone 12'
    )


def test_numeric_needle_rejects_run_extending_right():
    twenty_three = node(9, "Milestone 23: import")
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([twenty_three], "2")


def test_numeric_guard_inspects_only_the_first_hit():
    # Port-exact: the JS checks only the first indexOf hit. Here that hit is
    # inside "12", so the later standalone "2" is never considered.
    card = node(9, "Milestone 12, part 2")
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([card], "2")


def test_numeric_needle_still_picks_the_single_standalone_run():
    twelve = node(8, "Milestone 12: CSV export")
    two = node(9, "Milestone 2: JSON export")
    assert find_milestone([twelve, two], "2").id == ID(9)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_census.py -v`
Expected: these FAIL because the Task 1 substring step has no guard:
- `test_numeric_needle_does_not_resolve_via_a_longer_digit_run` — DID NOT RAISE (returns TREE).
- `test_two_does_not_resolve_milestone_twelve` — DID NOT RAISE.
- `test_numeric_needle_rejects_run_extending_right` — DID NOT RAISE.
- `test_numeric_guard_inspects_only_the_first_hit` — DID NOT RAISE.
- `test_numeric_needle_still_picks_the_single_standalone_run` — raises `MilestoneNotFoundError: ambiguous milestone "2" ...`.

`test_numeric_needle_resolves_a_card_whose_whole_digit_run_equals_it` already PASSES (it guards against the fix over-rejecting); the 13 Task 1 tests still PASS.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/census.py`, replace the import block

```python
from agent_manager.models import CardNode
```

with

```python
import re

from agent_manager.models import CardNode

_NUMERIC = re.compile(r"[0-9]+")


def _is_digit(ch: str) -> bool:
    """ASCII 0-9 only, as the JS `/[0-9]/`; `str.isdigit` also takes e.g. '٢'."""
    return "0" <= ch <= "9"


def _substring_hit(title: str, lowered: str, is_numeric: bool) -> bool:
    """Whether `lowered` is a usable substring of the lowercased `title`.

    A numeric needle ("2") must not resolve via a longer digit run it merely
    sits inside ("Milestone 12") -- that is not the milestone the caller typed,
    and silently resolving it turns a wrong READ into wrong branches, PRs and
    status writes. So a numeric needle only matches a digit run of its own
    length. Only the first occurrence is inspected, exactly as the JS does.
    """
    start = title.find(lowered)
    if start == -1:
        return False
    if not is_numeric:
        return True
    end = start + len(lowered)
    while start > 0 and _is_digit(title[start - 1]):
        start -= 1
    while end < len(title) and _is_digit(title[end]):
        end += 1
    return end - start <= len(lowered)
```

Then, inside `find_milestone`, replace

```python
    matches = [root for root in pool if lowered in root.title.lower()]
```

with

```python
    is_numeric = _NUMERIC.fullmatch(wanted) is not None
    matches = [
        root
        for root in pool
        if _substring_hit(root.title.lower(), lowered, is_numeric)
    ]
```

- [ ] **Step 4: Run the census tests to verify they pass**

Run: `uv run pytest tests/test_census.py -v`
Expected: all 19 tests PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: everything PASSES (including `tests/e2e`); no test outside `tests/test_census.py` changed behaviour, since no other module was edited.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/census.py tests/test_census.py
git commit -m "feat(census): a numeric needle never resolves a longer digit run"
```
