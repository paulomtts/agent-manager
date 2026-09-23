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
