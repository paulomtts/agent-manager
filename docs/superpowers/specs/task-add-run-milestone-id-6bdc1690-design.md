# Subtask 6bdc1690 — Add Run.milestone_id and stop relying on the short-id parse

Story: 20096657 "Stop losing phase failure reasons and milestone identity" (milestone 13, architecture cleanup). Parent design: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` §3 "S2" (lines ~143-151), §5 (lines ~291-299), §6 (lines ~301-336).

## Prerequisite

Blocked by sibling 503e0d1b "Persist PhaseRun.detail through the store" (done, on branch `m13/task-persist-phaserun-detail-503e0d1b`, e.g. commit 8856405). Its `store.py` changes — the `_ADDED_COLUMNS` tuple and the `_add_missing_columns(conn)` helper called from `open_db` after `executescript(_SCHEMA)` and before `commit()` — must be merged or rebased into this branch before work starts. This subtask reuses that mechanism; it does not add a second one.

## Scope

- `src/agent_manager/models.py`: `class Run` gains `milestone_id: str | None = None`.
- `src/agent_manager/store.py`:
  - `_SCHEMA`'s `runs` `CREATE TABLE IF NOT EXISTS` gains `milestone_id TEXT` as its last column (the `_ADDED_COLUMNS` invariant: fresh and migrated databases end with the same column order).
  - `_ADDED_COLUMNS` gains `("runs", "milestone_id", "TEXT")`.
  - `Store._write_run_row` writes `milestone_id` on insert and on the `ON CONFLICT ... UPDATE` path.
  - `load_run` reads `row["milestone_id"]` into `models.Run(...)`.
- `src/agent_manager/orchestrate.py`:
  - `run_milestone`: the fresh-run `models.Run(...)` construction sets `milestone_id=milestone_card.id`. The resume branch (`resumed.model_copy(update={"status": "started"})`) also sets `milestone_id=milestone_card.id`, so a pre-migration run resolved once by the fallback is stamped from then on.
  - `find_run_milestone` takes the resumed `models.Run` (not just its id). When `run.milestone_id` is set, it returns the root whose full `id` equals it. Only when `milestone_id` is None (runs written before this migration) does it fall back to the existing parse: `run.id.rsplit("-", 1)[-1]` matched against `dag.short_id(node.id)`. Update the docstring, which currently says `models.Run` records no milestone id. The one caller (`run_milestone`, currently `find_run_milestone(roots, resumed.id)`) passes `resumed`.

## Observable behavior

- New milestone runs persist their milestone card's full id. Resume uses that id, so two root cards whose 8-character short ids collide no longer make a resume ambiguous.
- Runs recorded before this change still resume through the short-id fallback, with the same result and the same errors as today.
- Opening an existing database adds the nullable column in place (`ALTER TABLE ... ADD COLUMN`). Existing rows read back as `milestone_id=None`. Nothing is rebuilt or dropped.
- `rebuild_from_journal` needs no change. The journal's `record_run` dump (`model_dump(mode="json", exclude={"stories"})`) carries the new field automatically, and older journal lines without it validate to None.
- No CLI output, envelope, or exit-code change (spec §5).

## Error paths

- `milestone_id` set, but no root card on the board has that id (card deleted or reparented): raise `cli.NotResumableError` with a message naming the recorded milestone id. Do not fall back to the short-id parse in this case, because the recorded id is authoritative.
- `milestone_id` None, and the fallback finds zero or several matching roots: the existing `NotResumableError` wording stays unchanged.

## Out of scope

- The `phases.detail` column, `_write_phase_row`, the phase-detail read, and the design of `_add_missing_columns` all belong to 503e0d1b.
- `cli.mint_run_id` and the run-id format stay unchanged.
- Also out of scope: the pygents turn/phase model, the checkpoint format, the harness adapter contract, adding a typecheck or CI gate, rewriting boundary monkeypatches, and replacing grafo.

## Tests

Tier placement follows CLAUDE.md: tests mirror source modules under `tests/`, and `tests/e2e/` is only for whole-process CLI wiring. Everything here is store/model/orchestrate unit logic, so none of it goes in e2e.

- `tests/test_models.py`: `Run` validates without `milestone_id` (defaults to None) and round-trips a given value.
- `tests/test_store.py`: a run recorded with `milestone_id` comes back with it from `load_run`.
- `tests/test_store.py`: a database created with the old `runs` schema (no `milestone_id`) gains the column on `open_db`. Its existing run loads with `milestone_id=None`. The column is last, and the fresh and migrated `PRAGMA table_info(runs)` column orders match.
- `tests/test_store.py`: `rebuild_from_journal` restores `milestone_id` from a journal that has it, and yields None from a journal line that lacks it.
- `tests/test_orchestrate.py`: `find_run_milestone` with two root cards whose short ids collide. Each run, with its `milestone_id` recorded, resolves to its own card. This is the required regression case from the card and spec §6.
- `tests/test_orchestrate.py`: `find_run_milestone` with `milestone_id=None` still uses the short-id parse. Update the existing test near line 3665 (`test_a_resumed_run_names_its_milestone_by_the_short_id_in_its_run_id`), both of whose calls pass a raw run id string (`RESUME_RUN_ID` and a second literal), to each pass a `Run` with `milestone_id=None` and that id. Its ambiguity/not-found error still raises.
- `tests/test_orchestrate.py`: `find_run_milestone` with a `milestone_id` that matches no root raises `NotResumableError`.
- `tests/test_orchestrate.py`: `run_milestone` stamps `milestone_id` on a fresh run's stored record. Resuming a pre-migration run (no `milestone_id`) leaves its record stamped with the resolved card id.

## Verification

`uv run pytest` (full suite, per CLAUDE.md; no typecheck or lint command).
