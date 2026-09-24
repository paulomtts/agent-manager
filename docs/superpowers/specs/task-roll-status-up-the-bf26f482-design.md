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
