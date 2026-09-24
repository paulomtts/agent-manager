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
