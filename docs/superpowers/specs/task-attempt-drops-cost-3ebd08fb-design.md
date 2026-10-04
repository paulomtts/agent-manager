# Subtask 3ebd08fb — Attempt drops cost/token fields; attempts table drops the matching columns

Parent story: d5aa963b "Remove dead cost/token tracking". Source design: `docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md` §§3.1, 3.3, 4.2, 4.3, 5.2, 5.3 items 1–5. This document narrows that agreed design to this one subtask; it introduces nothing new.

Line numbers below are from this worktree and are approximate; symbols are authoritative. (The upstream exploration summary was truncated at its file:line reference list, and several of its line numbers do not match this worktree, e.g. `replay` is at store.py:559 and its `Attempt.model_validate` call at :614. Everything here was rechecked against the worktree by symbol.)

## Scope

In scope: `src/agent_manager/models.py`, `src/agent_manager/store.py`, and their tests (`tests/test_models.py`, `tests/test_store.py`), plus one resume-adoption test in `tests/test_dispatch.py`.

Out of scope:
- `dispatch.py`'s usage removal. Sibling 38f7bada already did it.
- `harness.base.Usage`, `HarnessAdapter.parse_usage`, the `ClaudeAdapter` scanners and `_tokens`/`_cost` helpers, the fake-adapter `parse_usage` stubs, import hygiene, README, and design-spec §9/§17 text. All of these belong to sibling 7988f1db, which is blocked on this card.

## Changes

1. **`models.Attempt`**: delete `tokens_in`, `tokens_out` and `cost`. `Attempt` ends up with exactly eight fields: `n, dispatch, status, exit_code, duration, prompt_path, result_path, stdout_path`. It keeps `extra="forbid"` (inherited from `_Model`). In the module docstring (models.py:15), change "no exit code, duration, tokens or cost" to "no exit code or duration".
2. **`_SCHEMA` `CREATE TABLE IF NOT EXISTS attempts`**: remove the three column definitions `tokens_in INTEGER`, `tokens_out INTEGER` and `cost REAL`. Do not use any `ALTER TABLE … DROP COLUMN`, because migration stays additive-only (see `_add_missing_columns` / `open_db`).
   - A fresh DB gets 12 attempt columns.
   - An existing 15-column DB is left as it is. Its three retired columns are never written or read again, so they stay NULL.
3. **`load_run`**: stop passing `attempt_row["tokens_in"]`, `["tokens_out"]` and `["cost"]` into `models.Attempt(...)`.
4. **`_write_attempt_row`**: remove the three names from four places:
   - the INSERT column list
   - the VALUES placeholders
   - the `ON CONFLICT … DO UPDATE SET` clause
   - the bound-parameter dict

   The narrower statement must work against both the 12-column and the 15-column table.
5. **Replay reading shim**:
   - Add a module constant `_RETIRED_ATTEMPT_KEYS: frozenset[str] = frozenset({"tokens_in", "tokens_out", "cost"})` next to `_EVENT_KINDS`. Give it a docstring or comment that names the source design and gives the reason: every journal written before 2026-10-03 carries these keys, as null.
   - In `replay()`, just before `models.Attempt.model_validate(line.payload)`, validate a copy of the payload with those keys removed. Remove them whether their values are null or not.
   - This must be a named allow-list, not `extra="ignore"`. Any other unknown key on an attempt payload must still raise `pydantic.ValidationError` naming that key.
   - Only the `attempt_upsert` branch strips keys. `run_upsert`, `story_upsert`, `subtask_upsert` and `phase_upsert` keep strict validation unchanged.
   - Add one sentence to `replay`'s "Nothing here is defensive" docstring naming this single exception.

## Observable behavior and error paths

- Old journals (attempt lines with `tokens_in`/`tokens_out`/`cost`, null or not) must still work in two places:
  - `Store.rebuild_from_journal` and `Store.replay_journal` replay them into a tree whose attempts have none of those attributes.
  - Resume adoption (dispatch's `replay_journal` call, which catches `ValidationError` and declines silently) still adopts recorded `ok` attempts. It must not quietly redispatch every phase. This is why the shim is required, not optional.
- An attempt line with any other unknown key still fails loudly with `ValidationError`.
- A 15-column legacy DB opens, accepts `record_attempt`, and loads through `load_run` without error. The retired columns read NULL.

## Tests

Tier rule (CLAUDE.md "Test tiers"): a test's tier is set by what it spawns. sqlite is not a subprocess, and `FakeLauncher` is an injected fake. None of these tests spawn git, brd or claude, so all of them are unmarked, which means the default **unit** tier. `tests/test_store.py`'s neighbouring tests are unmarked too. The exploration summary's "git tier, like its neighbours" was wrong.

New tests:
1. `tests/test_store.py` (unit): hand-write a journal whose `attempt_upsert` payload carries `"tokens_in": null, "tokens_out": null, "cost": null`, plus a variant with non-null values.
   - `rebuild_from_journal` and `replay_journal` both return the tree with the attempt intact and none of those attributes.
   - A third line with one unrelated unknown key (e.g. `"operator": "x"`, following the existing pattern near test_store.py:577–585) still raises `ValidationError` whose message names that key.
2. `tests/test_dispatch.py` (unit, FakeLauncher): a source run whose journal has retired keys on an `ok` attempt is adopted on resume, not declined as unreadable.
3. `tests/test_models.py` (unit): `set(models.Attempt.model_fields) == {"n","dispatch","status","exit_code","duration","prompt_path","result_path","stdout_path"}`.
4. `tests/test_store.py` (unit), next to `test_a_fresh_phases_table_carries_detail_as_its_last_column`: on a fresh DB, `PRAGMA table_info(attempts)` lists exactly 12 names, none of them retired.
5. `tests/test_store.py` (unit), following the `_LEGACY_PHASES` pattern: recreate `attempts` in the old 15-column shape, then `record_attempt` and `load_run`. Nothing raises on open, write or load, and the three retired columns read NULL.

Existing-test edits (§3.1 Tests table / §5.2, all unit):
- `tests/test_models.py`:
  - Delete the three `is None` assertions on the retired fields.
  - Drop the `tokens_in=`/`tokens_out=`/`cost=` kwargs from fixtures.
  - Narrow the negative/NaN rejection tests to `duration` only, and rename them to match.
  - Change `attempt.cost is None` to `attempt.duration is None`.
- `tests/test_store.py`: replace `cost=` / `.cost` arguments and assertions with `duration=` / `.duration`.

Verification: `uv run pytest` passes.
