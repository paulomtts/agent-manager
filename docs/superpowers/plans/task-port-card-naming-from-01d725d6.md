<!-- task-pipeline: validated -->
# Port card naming from naming.mjs (card 01d725d6)

## Scope

Create `src/agent_manager/dag.py` with the five naming functions ported one-for-one from `/home/paulomtts/Code/leave-me-alone/plugins/leave-me-alone/scripts/naming.mjs`, and `tests/test_dag.py` with the sixteen cases listed below, ported from `naming.test.mjs`. Nothing else. `dag.py` is listed as **pure** in the package layout (design spec §4, lines 112-121): no I/O, no subprocesses, no `brd` calls, no imports from `board.py` or `store.py`. The cycle/level/base helpers that will eventually also live in `dag.py` are not part of this card; neither is `shell_quote`, which the design spec explicitly declines to port (line 252). `board.py` and everything `brd`-shaped belongs to sibling subtask 141c96e6 and must not appear here. Milestone orchestration (census, levels, parallel stories, integrate) and non-Claude harnesses are out of scope for the milestone entirely.

The design rationale is already settled and is not re-argued here: the short id is load-bearing and the slug is decoration, so everything that *matches* keys on the id alone and an edited card title can never orphan a branch. Eight hex characters is the chosen width.

## Observable behavior

`short_id(card_id) -> str` returns the first eight characters of the card UUID with dashes stripped and the result lowercased. `"a32af745-15ef-45cd-b52c-64c19ae82c17"` and `"A32AF745-15EF-45CD-B52C-64C19AE82C17"` both yield `"a32af745"`.

`slugify(title, max=24) -> str` lowercases the title, collapses every run of non-`[a-z0-9]` characters to a single `-`, and strips leading and trailing dashes. A `None` title is treated as the empty string (not the literal `"None"`). If the result is longer than `max`, it is cut to `max` characters and then trimmed back to the last `-` inside that cut, so the slug never ends mid-word; if that last dash is at index 0 or absent the truncated cut is kept as-is, and any trailing dashes are stripped from whatever remains. The result is never longer than `max` and never ends in `-`.

`task_stem(card) -> str` is `slugify(card.title) + "-" + short_id(card.id)`, except that when the slug is empty the stem is the short id alone, with no leading dash. The `card` argument is read for `title` and `id`; it accepts both an attribute-bearing object (the future `models.Card`, which is owned by a different card) and a plain mapping, so tests can pass dicts without depending on a module that does not exist yet.

`task_branch(prefix, card) -> str` is `f"{prefix}/task-{task_stem(card)}"`.

`ref_matches_card(ref, card_id) -> bool` is `short_id(card_id) in str(ref)`, with a `None` ref treated as the empty string. It returns `True` when the card's short id appears anywhere in the ref and `False` otherwise. This is the function that makes a title rename harmless.

## Error paths

`short_id` is deliberately strict: a card id is a UUID, and anything else is a bug worth surfacing loudly rather than inventing a plausible short id. It raises (a `ValueError` whose message contains `not a card id` followed by the offending value) for: a non-string argument of any type (`None`, `int`, `dict`, and anything else); the empty string; a string that is not thirty-two hex characters once dashes are removed, including non-hex text like `"nope"` and hex runs of the wrong length like `"a32af745"` or `"deadbeefdeadbeef"`. Note that an `int` such as `12345678` raises on the type check even though it reads as hex. No dash-position validation is performed beyond the strip-and-count rule, matching the reference implementation.

`task_stem`, `task_branch` and `ref_matches_card` inherit that failure: a card with a missing or malformed `id` propagates the same error. A missing or unslugifiable `title` is *not* an error — it yields the bare short id.

## Test list

All sixteen tests below are **unit tests of pure functions**, the first tier of design spec §14 (lines 477-492): ported alongside the logic straight from `naming.test.mjs`, which is the behavioural specification for naming. None of them belongs in the Steps tier (no temporary git repo and no temporary `brd` board is needed or permitted), nor in the Adapters, Engine or End-to-end tiers. They live at `tests/test_dag.py`, mirroring `src/agent_manager/dag.py` per the repo convention.

1. `short_id` returns the first eight hex characters, dashes ignored — unit.
2. `short_id` lowercases an uppercase UUID to the same value — unit.
3. `short_id` raises on the empty string — unit.
4. `short_id` raises on `None` — unit.
5. `short_id` raises on non-hex text (`"nope"`) — unit.
6. `short_id` raises on a number, including one that looks like hex (`12345678`, `1234567890123456`) — unit.
7. `short_id` raises on a hex run that is not a full UUID (`"a32af745"`, `"deadbeefdeadbeef"`) — unit.
8. `short_id` raises on a dict — unit.
9. `slugify` lowercases and collapses punctuation to single dashes: `"40.1 feat: write rows"` -> `"40-1-feat-write-rows"` — unit.
10. `slugify` trims surrounding whitespace and inner runs: `"  Hello,   World!  "` -> `"hello-world"` — unit.
11. `slugify` truncates at a word boundary without a trailing dash: `("abcdefghij klmnopqrst uvwxyz", 24)` -> `"abcdefghij-klmnopqrst"` — unit.
12. `task_stem` is slug then short id, so the id is a stable suffix: `"40-1-feat-write-rows-a32af745"` — unit.
13. `task_stem` of a card titled `"???"` is the bare short id `"a32af745"`, with no leading dash — unit.
14. `task_branch("m12", card)` is `"m12/task-40-1-feat-write-rows-a32af745"` — unit.
15. `ref_matches_card` keys on the short id, so a branch built from the original title still matches a card whose title was since changed — unit.
16. `ref_matches_card("m12/task-quoting-03a6dc10", CARD.id)` is `False`, so two different cards are not confused — unit.

(The strictness cases are enumerated individually above; they correspond to the four `shortId`-rejection `test()` blocks in the source file — one of which asserts the empty-string, `undefined`, and `'nope'` cases together, one of which asserts both number cases, one of which asserts both wrong-length-hex cases, and one of which asserts both the object and `null` cases (the `null` case collapses into the `None` case above, since Python has no `undefined`/`null` distinction) — and may be written as parametrised cases.)

The shared fixture is the reference card from `naming.test.mjs`: id `a32af745-15ef-45cd-b52c-64c19ae82c17`, title `40.1 feat: write rows`. Plain functions and plain values throughout — no Pydantic, since nothing here crosses a process boundary.

## Verification

`uv run pytest`. There is no separate typecheck or lint command.

---

# Card Naming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the pure module `src/agent_manager/dag.py` with `short_id`, `slugify`, `task_stem`, `task_branch` and `ref_matches_card` ported one-for-one from `naming.mjs`, covered by unit tests ported from `naming.test.mjs`.

**Architecture:** One new stdlib-only module and one new test file. `dag.py` imports nothing from the rest of the package — not `paths`, not `models`, not `store` — so this task stands alone on a branch where no sibling subtask's code may be assumed. Card fields are read through a private accessor that handles both a mapping and an attribute-bearing object, so the tests never need `models.Card`. Every function is a plain function over plain values; no Pydantic, no dataclasses, no I/O.

**Tech Stack:** Python 3 (stdlib `re` and `collections.abc.Mapping` only), pytest via `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-port-card-naming-from-01d725d6-design.md` (prepended verbatim above).

## Global Constraints

- `src/agent_manager/dag.py` is **pure**: no I/O, no subprocesses, no `brd` calls, no imports from `board.py`, `store.py`, `paths.py` or `models.py`. Stdlib imports only.
- `shell_quote` does NOT port. Cycle/level/base helpers do NOT belong to this card. `board.py` belongs to sibling subtask 141c96e6 and must not be created or touched here.
- The short id is eight hex characters, lowercased, dashes stripped.
- Every rejection from `short_id` is a `ValueError` whose message contains the literal text `not a card id` followed by the offending value.
- Matching keys on the id alone: `ref_matches_card` never looks at the slug.
- Tests live at `tests/test_dag.py` — the flat, module-mirroring layout already used by `tests/test_paths.py` and `tests/test_store.py`. This is the "Pure functions" tier of design spec §14 (lines 477-492): no temporary git repo, no temporary `brd` board, no fake launcher, no harness.
- Verification is `uv run pytest`. There is no lint or typecheck command.
- The public names are exactly `short_id`, `slugify`, `task_stem`, `task_branch`, `ref_matches_card` — snake_case ports of `shortId`, `slugify`, `taskStem`, `taskBranch`, `refMatchesCard`.

## Review Focus

- A card passed as a plain mapping vs. as an attribute-bearing object: the spec requires both shapes work, but the ported `naming.test.mjs` cases only ever pass a dict. Covered by Task 3, Step 9.
- A card mapping with no `title` key at all (not just an unslugifiable one): the spec says a missing title is not an error and yields the bare short id, but `naming.test.mjs` only tests `"???"`. Covered by Task 3, Step 9.
- `ref_matches_card(None, card_id)`: the spec says a `None` ref is the empty string and therefore `False`, and a caller reading an unset branch field will hit this. Covered by Task 3, Step 9.
- A title with no spaces at all that is longer than `max`: the word-boundary trim finds no dash inside the cut and must keep the hard cut rather than return the empty string. Covered by Task 2, Step 5.
- A non-ASCII title (accents, emoji): `[^a-z0-9]` must collapse those bytes to dashes and leave a pure-ASCII branch-safe slug, since real card titles are typed by humans. Covered by Task 2, Step 5.

---

### Task 1: `short_id`

**Files:**
- Create: `src/agent_manager/dag.py`
- Test: `tests/test_dag.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `short_id(card_id: object) -> str`, returning eight lowercase hex characters, raising `ValueError` for anything that is not a 32-hex-character UUID string. Used by `task_stem` and `ref_matches_card` in Task 3.

- [ ] **Step 1: Write the failing tests for `short_id`**

Create `tests/test_dag.py` with exactly this content:

```python
import pytest

from agent_manager.dag import short_id

CARD = {"id": "a32af745-15ef-45cd-b52c-64c19ae82c17", "title": "40.1 feat: write rows"}


def test_short_id_is_first_eight_hex_chars_dashes_ignored():
    assert short_id(CARD["id"]) == "a32af745"


def test_short_id_lowercases_an_uppercase_uuid():
    assert short_id("A32AF745-15EF-45CD-B52C-64C19AE82C17") == "a32af745"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        None,
        "nope",
        12345678,
        1234567890123456,
        "a32af745",
        "deadbeefdeadbeef",
        {},
    ],
)
def test_short_id_rejects_anything_that_is_not_a_card_id(bad):
    with pytest.raises(ValueError, match="not a card id"):
        short_id(bad)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'agent_manager.dag'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/dag.py`:

```python
"""Card identity in every derived name: branches, plan files, spec files.

The two halves of a derived name are not equal. The short id is load-bearing
and the slug is decoration: a card's title can be edited after its branch
exists, and if matching keyed on the slug that edit would orphan the branch —
the run would report finished work as not done. So everything that MATCHES
uses the id, and the slug exists only so ``git branch`` output is readable.

Eight hex characters collides with probability that does not matter inside one
milestone. The check is deliberately strict: a card id is a UUID, and anything
else is a bug worth surfacing loudly rather than inventing a plausible short
id.

This module is pure: no I/O, no subprocesses, no ``brd``.
"""

import re

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")


def short_id(card_id: object) -> str:
    """First eight hex characters of a card UUID, dashes stripped, lowercased."""
    if not isinstance(card_id, str):
        raise ValueError(f"not a card id: {card_id!r}")
    hex_only = card_id.replace("-", "")
    if not _HEX32.match(hex_only):
        raise ValueError(f"not a card id: {card_id!r}")
    return hex_only[:8].lower()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS — 10 passed (2 positive cases plus 8 parametrised rejections).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): add strict short_id ported from naming.mjs"
```

---

### Task 2: `slugify`

**Files:**
- Modify: `src/agent_manager/dag.py`
- Test: `tests/test_dag.py`

**Interfaces:**
- Consumes: nothing from Task 1 (`slugify` is independent of `short_id`).
- Produces: `slugify(title: object, max: int = 24) -> str`, returning a lowercase dash-joined ASCII slug never longer than `max` and never ending in `-`. Used by `task_stem` in Task 3. The parameter is named `max` (shadowing the builtin inside the function body) to match the spec's signature exactly.

- [ ] **Step 1: Write the failing tests for `slugify`**

Change the import line at the top of `tests/test_dag.py` from:

```python
from agent_manager.dag import short_id
```

to:

```python
from agent_manager.dag import short_id, slugify
```

Then append to `tests/test_dag.py`:

```python
def test_slugify_lowercases_and_collapses_punctuation_to_single_dashes():
    assert slugify("40.1 feat: write rows") == "40-1-feat-write-rows"


def test_slugify_trims_surrounding_whitespace_and_inner_runs():
    assert slugify("  Hello,   World!  ") == "hello-world"


def test_slugify_truncates_at_a_word_boundary_without_a_trailing_dash():
    assert slugify("abcdefghij klmnopqrst uvwxyz", 24) == "abcdefghij-klmnopqrst"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection error — `ImportError: cannot import name 'slugify' from 'agent_manager.dag'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/dag.py`:

```python
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title: object, max: int = 24) -> str:
    """Lowercase dash-joined slug of a title, cut at a word boundary."""
    flat = _NON_SLUG.sub("-", str("" if title is None else title).lower()).strip("-")
    if len(flat) <= max:
        return flat
    # Cut at a word boundary rather than mid-word: a trailing "-uv" fragment
    # makes a branch name harder to read, not easier.
    cut = flat[:max]
    last_dash = cut.rfind("-")
    kept = cut[:last_dash] if last_dash > 0 else cut
    return kept.rstrip("-")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS — 13 passed.

- [ ] **Step 5: Write and run the Review Focus tests for `slugify`**

Append to `tests/test_dag.py`:

```python
def test_slugify_hard_cuts_a_single_long_word_with_no_dash_to_fall_back_to():
    assert slugify("a" * 30, 24) == "a" * 24


def test_slugify_strips_non_ascii_to_dashes_leaving_a_branch_safe_slug():
    result = slugify("Café ☕ résumé")
    assert result == "caf-r-sum"
    assert result.isascii()


def test_slugify_treats_none_as_the_empty_string_not_the_word_none():
    assert slugify(None) == ""


def test_slugify_never_exceeds_max_and_never_ends_in_a_dash():
    for title in ["40.1 feat: write rows", "a" * 30, "one two three four five six"]:
        for limit in [4, 8, 24]:
            result = slugify(title, limit)
            assert len(result) <= limit
            assert not result.endswith("-")
```

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS — 17 passed. (These exercise branches the implementation from Step 3 already covers; if any fails, fix `slugify` rather than the test.)

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): add slugify with word-boundary truncation"
```

---

### Task 3: `task_stem`, `task_branch` and `ref_matches_card`

**Files:**
- Modify: `src/agent_manager/dag.py`
- Test: `tests/test_dag.py`

**Interfaces:**
- Consumes: `short_id(card_id: object) -> str` and `slugify(title: object, max: int = 24) -> str` from Tasks 1 and 2.
- Produces: `task_stem(card: object) -> str`, `task_branch(prefix: str, card: object) -> str`, `ref_matches_card(ref: object, card_id: object) -> bool`. `card` is either a `Mapping` with `"id"`/`"title"` keys or an object with `.id`/`.title` attributes (the future `models.Card`, owned by another card — not imported here).

- [ ] **Step 1: Write the failing tests for `task_stem`**

Change the import line at the top of `tests/test_dag.py` from:

```python
from agent_manager.dag import short_id, slugify
```

to:

```python
from agent_manager.dag import short_id, slugify, task_stem
```

Then append to `tests/test_dag.py`:

```python
def test_task_stem_is_slug_then_short_id_so_the_id_is_a_stable_suffix():
    assert task_stem(CARD) == "40-1-feat-write-rows-a32af745"


def test_task_stem_of_an_unslugifiable_title_is_the_bare_short_id():
    assert task_stem({"id": CARD["id"], "title": "???"}) == "a32af745"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection error — `ImportError: cannot import name 'task_stem' from 'agent_manager.dag'`.

- [ ] **Step 3: Implement `task_stem` and its card accessor**

Change the import block at the top of `src/agent_manager/dag.py` from:

```python
import re
```

to:

```python
import re
from collections.abc import Mapping
```

Then append to `src/agent_manager/dag.py`:

```python
def _field(card: object, name: str) -> object:
    """Read ``name`` off a card given either as a mapping or as an object."""
    if isinstance(card, Mapping):
        return card.get(name)
    return getattr(card, name, None)


def task_stem(card: object) -> str:
    """Readable slug plus the load-bearing short id, or the short id alone."""
    slug = slugify(_field(card, "title"))
    card_short_id = short_id(_field(card, "id"))
    return f"{slug}-{card_short_id}" if slug else card_short_id
```

- [ ] **Step 4: Run the tests to verify `task_stem` passes**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS — 19 passed.

- [ ] **Step 5: Write the failing tests for `task_branch` and `ref_matches_card`**

Change the import line at the top of `tests/test_dag.py` from:

```python
from agent_manager.dag import short_id, slugify, task_stem
```

to:

```python
from agent_manager.dag import (
    ref_matches_card,
    short_id,
    slugify,
    task_branch,
    task_stem,
)
```

Then append to `tests/test_dag.py`:

```python
def test_task_branch_prefixes_the_stem():
    assert task_branch("m12", CARD) == "m12/task-40-1-feat-write-rows-a32af745"


def test_ref_matches_card_keys_on_the_short_id_so_a_rename_still_matches():
    branch = task_branch("m12", CARD)
    renamed = {**CARD, "title": "completely different title"}
    assert ref_matches_card(branch, renamed["id"]) is True


def test_ref_matches_card_does_not_confuse_two_different_cards():
    assert ref_matches_card("m12/task-quoting-03a6dc10", CARD["id"]) is False
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection error — `ImportError: cannot import name 'ref_matches_card' from 'agent_manager.dag'`.

- [ ] **Step 7: Implement `task_branch` and `ref_matches_card`**

Append to `src/agent_manager/dag.py`:

```python
def task_branch(prefix: str, card: object) -> str:
    """Branch name for one card's task worktree."""
    return f"{prefix}/task-{task_stem(card)}"


def ref_matches_card(ref: object, card_id: object) -> bool:
    """True when a branch or ref carries this card's short id anywhere in it.

    Keys on the id and never on the slug, so editing a card's title cannot
    orphan the branch that was named from the old title.
    """
    return short_id(card_id) in str("" if ref is None else ref)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS — 22 passed.

- [ ] **Step 9: Write and run the Review Focus tests for the card-shaped functions**

Append to `tests/test_dag.py`:

```python
class _CardObject:
    """Stand-in for the future models.Card, which another subtask owns."""

    def __init__(self, id: str, title: str) -> None:
        self.id = id
        self.title = title


def test_task_stem_accepts_an_attribute_bearing_card_as_well_as_a_mapping():
    obj = _CardObject(CARD["id"], CARD["title"])
    assert task_stem(obj) == task_stem(CARD) == "40-1-feat-write-rows-a32af745"
    assert task_branch("m12", obj) == "m12/task-40-1-feat-write-rows-a32af745"


def test_task_stem_of_a_card_with_no_title_field_at_all_is_the_bare_short_id():
    assert task_stem({"id": CARD["id"]}) == "a32af745"
    assert task_stem(_CardObject(CARD["id"], None)) == "a32af745"


def test_task_stem_propagates_the_short_id_error_for_a_missing_or_bad_id():
    with pytest.raises(ValueError, match="not a card id"):
        task_stem({"title": "no id here"})
    with pytest.raises(ValueError, match="not a card id"):
        task_branch("m12", {"id": "nope", "title": "bad id"})


def test_ref_matches_card_treats_a_none_ref_as_the_empty_string():
    assert ref_matches_card(None, CARD["id"]) is False


def test_ref_matches_card_still_validates_the_card_id_for_an_empty_ref():
    with pytest.raises(ValueError, match="not a card id"):
        ref_matches_card("", "nope")
```

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS — 27 passed. (`_CardObject(CARD["id"], None)` relies on `slugify` treating `None` as the empty string, which Task 2 already implements.)

- [ ] **Step 10: Run the full suite**

Run: `uv run pytest`
Expected: PASS — every pre-existing test in `tests/` plus the 27 in `tests/test_dag.py`.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "feat(dag): add task_stem, task_branch and ref_matches_card"
```
