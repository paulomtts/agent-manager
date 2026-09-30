# Persist PhaseRun.detail through the store — subtask design

Card: 503e0d1b-8f11-46b3-9a05-b2465761f931 (subtask of story 20096657 "Stop losing phase failure reasons and milestone identity").
Source of truth: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, Decision S2 (§3), §5 Compatibility, §6 Testing. That doc's line numbers have drifted; the current locations in `src/agent_manager/store.py` are listed below and must be re-read before implementing.

## Problem

`models.PhaseRun.detail` already exists and `dispatch.py` / `walk.py` fill it in memory (for example a phase's failure reason). `record_phase` already journals it, because `model_dump(exclude={"attempts"})` includes `detail`, and `replay()` already restores it through `PhaseRun.model_validate`. The SQL projection drops it: the `phases` table has no `detail` column, `_write_phase_row` does not write it, and `load_run` does not read it back. So any run loaded from the DB has `detail=None` on every phase.

## Scope

All changes are in `src/agent_manager/store.py`, plus one or more tests in `tests/test_store.py`.

1. **Schema.** The `phases` table in `_SCHEMA` (currently store.py:64-75) gets a nullable `detail TEXT` column. New databases get it straight from `CREATE TABLE IF NOT EXISTS`.
2. **Migration guard in `open_db()`** (store.py:139). Databases created before this change already have a `phases` table without `detail`, so `CREATE TABLE IF NOT EXISTS` does nothing for them. `open_db` must add the column with `ALTER TABLE phases ADD COLUMN detail TEXT`, but only when the column is missing. SQLite has no `ADD COLUMN IF NOT EXISTS` and the repo has no `ALTER TABLE` precedent, so the guard has to be written here. It can either check `PRAGMA table_info(phases)` first, or catch only the "duplicate column name" `sqlite3.OperationalError`. Requirements:
   - Opening the same DB any number of times is safe.
   - The migration is not destructive: no table rebuild or copy, and existing rows keep their data with `detail` NULL.
   - Any other `OperationalError` is re-raised, not swallowed.
   `open_db`'s own docstring currently says reopening an existing database
   "neither destroys nor migrates what is already there"; that claim becomes
   false once this guard exists, so update the docstring to describe the new,
   additive-only migration alongside the existing `CREATE ... IF NOT EXISTS`
   behaviour.
3. **Write.** `Store._write_phase_row` (store.py:919) includes `phase.detail` in its insert/upsert, including on the update path, so a phase that later gains or changes `detail` is overwritten.
4. **Read.** The module-level `load_run` (store.py:437; `PhaseRun` is built at around line 494) selects `detail` and passes it to `models.PhaseRun(detail=...)`. `Store.load_run` (store.py:1002) only wraps this function, so it needs no change of its own.

`rebuild_from_journal` (store.py:1213) needs no change. It replays the journal, which already carries `detail`, and writes rows through `_write_phase_row`. Once step 3 lands, it restores `detail` for old runs.

## Observable behaviour

- `load_run` / `Store.load_run` returns `PhaseRun.detail` exactly as it was recorded, or `None` if none was recorded.
- After `rebuild_from_journal`, `detail` is restored from the journal, including for runs recorded before the column existed.
- There is no CLI surface change beyond `detail` now showing up where it was silently dropped before. §5 describes this as "a strict improvement, not breaking", and the `{"ok": true, "data": ...}` envelope is unchanged.

## Error paths

- Legacy DB without the column: `open_db` adds it, and existing phase rows read back with `detail=None`.
- `open_db` run again on a DB that has already been migrated: nothing happens and no error is raised.
- Unrelated SQLite errors during the guard: re-raised.

## Out of scope

- `replay`, `record_phase` and the journal format, which already carry `detail`.
- Sibling subtask 6bdc1690 (`runs.milestone_id`, `models.Run.milestone_id`, `run_milestone`, `find_run_milestone`, the colliding-short-id test). That subtask is blocked on this one, but it owns all of those.
- Changes to pygents' turn/phase model, the checkpoint format, or the harness adapter contract.
- Adding a typecheck or CI gate.
- Rewriting the boundary monkeypatches.
- Replacing or removing grafo.

## Tests

Tier rule: design spec §14 "Testing" and the `tests/test_store.py` docstring. `store.py` does I/O against a real temporary SQLite DB and journal, with no network, no mocks and no harness, so these tests belong in the **Steps** tier. Put them in `tests/test_store.py` next to the existing `load_run` / `rebuild_from_journal` tests. None of them go in `tests/e2e` or `tests/runtime`.

1. **Steps tier:** a phase recorded as `failed` with a non-empty `detail` comes back with the same `status` and `detail` from `load_run`.
2. **Steps tier:** the same run, after `rebuild_from_journal`, still has that `detail` when loaded with `load_run`.
3. **Steps tier:** a phase re-recorded with a changed `detail` reads back the latest value (the upsert updates the column).
4. **Steps tier:** for a legacy DB, create a `phases` table with the old column set (no `detail`) and insert a row. Then:
   - `open_db` adds the column, and the existing row survives with `detail=None`.
   - A second `open_db` does not raise.
   - `rebuild_from_journal` fills `detail` in from the journal.

## Verification

`uv run pytest` (per CLAUDE.md there is no separate lint or typecheck command).
