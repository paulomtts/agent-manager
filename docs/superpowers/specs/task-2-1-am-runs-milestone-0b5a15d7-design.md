# 2.1 `am runs`: `milestone_id` and `card_id` (card 0b5a15d7)

Parent story: e187d6f8 "Richer am runs" (milestone fbf624d6). Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md`, §1 "Richer `am runs`". This subtask delivers only the first two fields of that section. `lease` is 6bf47e74 (2.2) and `progress` is 882b212b (2.3); both are out of scope here.

## Scope

- `store.RunSummary` (`src/agent_manager/store.py`, stays `extra="forbid"`) gains two optional fields, both defaulting to `None`:
  - `milestone_id: str | None`: the value already in the `runs.milestone_id` column. It is null on a `--card` run and on old rows written before the column existed (the `_ADDED_COLUMNS` migration leaves them NULL).
  - `card_id: str | None`: the subtask id of a `--card` run, else null.
- `store.list_runs` selects `milestone_id` and derives `card_id`. Ordering (`started_at DESC, id DESC`) and the existing seven fields are unchanged.
- `card_id` is derived, not stored. A `--card` run is workflow `"task"` and records exactly one story and one subtask row in `subtasks` (PK `run_id, story_id, card_id`). `card_id` is that row's `card_id` when `workflow == "task"`, else null. The natural form is a correlated subquery or LEFT JOIN inside `list_runs`'s SQL. No new column, no migration, no change to `record_run` / `record_subtask` / `load_run`.
- `cli.runs_for` needs no code change: `summary.model_dump()` carries the new keys into `data.runs[]` automatically. The envelope stays `{"ok": true, "data": {"runs": [...]}}`.
- `STATUS_HEADER` and `am status` do not change. The comment near `cli.py:203` that says the status header and `runs`' entries are "the same seven names" becomes inaccurate; touch up the comment only (e.g. say `runs` entries are a superset), not the header.
- README (small docs piece, not core): where `am runs` is described, list the `data.runs[]` keys including `milestone_id` and `card_id` with their null semantics, and add the sentence that consumers ignore unknown keys (new keys are additive). The README has no dedicated `am runs` section today, so a short one is acceptable.

## Observable behaviour

- Milestone run (workflow `"milestone"`, `milestone_id` set): `milestone_id` is the recorded id, `card_id` is null.
- `--card` run (workflow `"task"`, one subtask row): `milestone_id` is null, `card_id` is the subtask's card id.
- `"task"` run with no subtask row yet (recorded before its subtask was written): `card_id` is null, no error.
- Old fixture (runs table created without `milestone_id`, migrated by `open_db`): row still listed, `milestone_id` null, `card_id` derived as above.
- Every pre-existing key in `data.runs[]` keeps its name and value. Nothing removed or renamed; journal stays schema 1.

## Error paths

None new. `list_runs` on an empty projection still returns `[]`; `am runs` on an empty history still returns `{"runs": []}`. A projection row that fails `RunSummary` validation still fails loudly as today.

## Tests (TDD: written first, failing, then made green; `uv run pytest` green at the end)

All of these use SQLite in `tmp_path` (and the `CliRunner` for the CLI ones) with no subprocess, so per the CLAUDE.md "Test tiers" placement rule (and design spec §14) they are **unmarked `unit`** tests, like the neighbouring `list_runs` / `runs` tests. None is marked `git`, `brd`, `e2e_fake`, `soak` or `e2e`.

`tests/test_store.py` (unit):
1. A milestone run listed by `list_runs` has `milestone_id` equal to the recorded id and `card_id is None`.
2. A `task` run with one story and one subtask has `milestone_id is None` and `card_id` equal to the subtask's card id.
3. A `task` run with no subtask row has `card_id is None`. The existing `_record_summary` helper writes the run row only, but via `_run(...)`, whose workflow is `"milestone"` and `milestone_id` is None; so this test (and tests 1 and 2) must `model_copy(update={"workflow": "task"})` / `update={"milestone_id": ...}` on the run (or add keyword parameters to the helper) rather than rely on its defaults. Test 1 needs a milestone run with `milestone_id` set, which `_run` does not give by default.
4. Old fixture: build a `runs` table without `milestone_id` the way `test_a_runs_table_from_before_milestone_id_gains_the_column_and_keeps_its_row` does, open it with `open_db`, and `list_runs` returns the row with `milestone_id is None` and `card_id is None`.
5. Shape pin: `RunSummary.model_fields` (or a dumped summary's keys) equals exactly the seven old names plus `milestone_id` and `card_id`.

`tests/test_cli.py` (unit, `CliRunner`):
6. `am runs` on a `--card`-shaped run (the existing `_record` helper) has `data.runs[0]["card_id"]` equal to the subtask id and `data.runs[0]["milestone_id"]` null.
7. `am runs` on a milestone run has `milestone_id` set and `card_id` null. The existing `_record` helper hardcodes `workflow="task"` and no `milestone_id`, so extend it with optional `workflow` / `milestone_id` keyword parameters (defaults unchanged) or write the milestone run directly with `Store.record_run`.
8. Shape test: the key set of each `data.runs[]` entry is exactly the old seven plus `milestone_id` and `card_id`, and the envelope is `{"ok": true, "data": {"runs": [...]}}`.

Existing `list_runs` / `runs` tests and the replay test that uses `list_runs` (around `test_store.py:2937`) must still pass. Adjust them only if they assert an exact key set.
