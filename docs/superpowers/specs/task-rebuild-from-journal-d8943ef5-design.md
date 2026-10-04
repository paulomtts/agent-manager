# rebuild_from_journal refuses to overwrite foreign values; docs name the journal/row-only boundary (d8943ef5)

Subtask of story ddf4da2c ("am status catches journal/projection divergence instead of trusting either blindly"). Narrows `docs/superpowers/specs/2026-10-03-journal-db-divergence-design.md` §3.1, §3.6, §3.7, §4, §5 to the last remaining child. Baseline: branch `m19/task-am-status-gains-an-f63036db` (blocked_by f63036db, which already contains 80dade04). Line numbers below are from that baseline, not master.

## Scope

In scope:

1. `Store.rebuild_from_journal` gains the §3.6 rail and a keyword-only `force` flag.
2. A new exception class `ProjectionDivergedError` in `src/agent_manager/store.py`.
3. One-clause doc corrections naming the journal/row-only boundary in: the `store.py` module docstring (lines 1-7), the `Store` class docstring (lines ~1285-1289), the `rebuild_from_journal` docstring, design spec `2026-09-23-agent-manager-design.md` §9 "Write ordering" paragraph (lines 371-374), and the README.
4. A short README paragraph documenting `am status`'s `integrity` key.
5. New unit tests in `tests/test_store.py`.

Out of scope (owned elsewhere or excluded by the story): `diverging`/`Mismatch`/`MismatchKind` (80dade04, done; reuse, do not modify), `integrity_view`/`status_for` wiring in `cli.py` (f63036db, done; do not modify), any production caller of `rebuild_from_journal` (none exists; none is added), any repair or `--fix`, journaling the row-only tables, an `am check` command, comparing anything beyond status and tree shape.

## Observable behavior

Signature: `rebuild_from_journal(self, run_id: str, *, force: bool = False) -> models.Run`.

- Inside the existing `with self._lock, self._fenced():` block, after `replay` and the existing `run.id != run_id` `JournalError` guard, and before `self._delete_run(run_id)`: load the current projection over the store's own connection (`self.load_run(run_id)`; the lock is re-entrant) and, when it is not `None`, call `diverging(lines, projection)` on the same journal lines that were just read and replayed (read once, not twice, so check and rebuild see identical lines).
- If `force` is false and any mismatch has `kind == "foreign"`, raise `ProjectionDivergedError` and touch no row (same early-exit shape as the `JournalError` guard; the fenced transaction writes nothing).
- `stale` mismatches never refuse. A projection with no `runs` row for `run_id` (`load_run` returns `None`, i.e. truncated/emptied) has nothing foreign and rebuilds as today.
- `force=True` skips the check entirely and rebuilds exactly as today.
- Return value and all row writes on the non-refusing path are unchanged.
- The check uses `diverging` from this module; no second definition of divergence (§3.7 "One definition").

## Error path

`ProjectionDivergedError(RuntimeError)`, defined in `store.py` next to the other store errors; explicitly not a subclass of `JournalError` (the journal is fine). Its message names the run id and lists every foreign mismatch with its node, field, journal value and projection value (for the incident case: the run node, `escalated`, `cancelled`), and says `force=True` overrides. `stale` mismatches need not appear in the message. Existing errors out of `replay`/`Journal.read` (`JournalError`, `MissingJournalError`, `CorruptJournalError`, validation errors) keep propagating unchanged and take precedence, since they occur before the check.

## Documentation

All corrections state the same boundary in one clause each: the journal rebuilds the run's §9 tree (runs, stories, subtasks, phases, attempts); the six row-only tables (`checkpoints`, `checkpoint_floors`, `run_controls`, `run_leases`, `run_claims`, `board_comments`) have no journal and are the projection's alone.

- `store.py` module docstring: "the projection can be thrown away and rebuilt" gains the row-only exception.
- `Store` class docstring: the exceptions list names all six tables (currently missing `checkpoint_floors` and `run_claims`).
- `rebuild_from_journal` docstring: "the result is the same whether the projection was stale, truncated or already correct" stays and gains the exception that a projection holding a value the journal never recorded is refused unless `force=True`.
- Design spec §9: "The DB is a projection and can be rebuilt from the journal; if the two disagree, the journal wins." gains the row-only clause.
- README: no existing sentence claims the projection is rebuilt from the journal, so the clause goes in the new `integrity` paragraph, placed in the "Pausing and cancelling a run" section directly after the `am status` `control` key paragraph (README.md:394). That paragraph states: `am status <run-id>` always has an `integrity` key `{"checked", "reason", "mismatches"}`; `checked: false` with `reason` one of `"lease is live"`, `"no journal"`, `"journal unreadable: …"` (as `integrity_view` emits them on the baseline); each mismatch is `{"node", "field", "journal", "projection", "kind"}`; the check is report-only and never changes the exit code. Then exactly one sentence each: `stale` means the journal is ahead and a resume or rebuild moves the projection forward; `foreign` means something other than `am` wrote this row. Wording must match the landed `integrity_view`/`Mismatch`, not the plan.

## Tests

All new tests go in `tests/test_store.py`, unmarked = `unit` tier per CLAUDE.md's placement rule (tier chosen by what the test spawns or touches): they drive `Store` against `tmp_path` via the file's `repo` fixture (a plain directory plus redirected `XDG_DATA_HOME`, no git) and edit the projection with the existing `_raw_sql` helper; no subprocess of any kind.

- `unit`: hand-edited cancelled run (record `_run(repo)` then the same with `status="escalated"`, close, `_raw_sql` `UPDATE runs SET status='cancelled'`). Reopen, `rebuild_from_journal(RUN_ID)` raises `ProjectionDivergedError` (and `not isinstance(..., JournalError)`), message names the run node, `escalated` and `cancelled`. Afterwards `runs.status` is still `cancelled` and every table's rows are byte-for-byte as before (snapshot before/after).
- `unit`: same fixture with `force=True` rebuilds; `runs.status` is `escalated`.
- `unit`: a hand-inserted subtask row no journal line created (foreign shape mismatch) also refuses without `force` and leaves rows untouched. (Optional; covers the shape half of `foreign`.)
- `unit`: a status set back to an earlier journaled value (`stale`) rebuilds without `force`.
- Unchanged, must keep passing with default `force=False`: `test_rebuild_picks_up_a_journal_line_whose_row_never_landed` (the journal-ahead `stale` case) and every other existing `rebuild_from_journal` test in `tests/test_store.py` (the tests the divergence spec cites by master line numbers 895-910, 917, 930-945, 1136, 1168, 1220, 1535), `tests/test_integration.py:587` (`git` tier) and `tests/e2e/test_parallel_milestone.py:201` (`e2e_fake`, opt-in). Their tiers are unchanged; do not re-mark them.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none
- Source: CLAUDE.md, pyproject.toml, .github/workflows/publish.yml

Note: the upstream exploration summary was truncated at 8000 characters mid-way through its test-tier paragraph; the tier guidance above was taken directly from CLAUDE.md and divergence spec §4 instead.
