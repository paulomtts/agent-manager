# Subtask ef248597 — Add the SQLite store and the append-only journal

Parent story: 8831189b "Foundations: paths, run store and journal". Blocked by 1535b285 (state models, done); sits on top of fdebc746 (paths, done).

Narrowing of the agreed design in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` — D5 (line 70), §9 (lines 346-387), §14 (lines 477-492). No new design decisions are taken here.

## Scope

One new module, `src/agent_manager/store.py`, plus its mirror test module `tests/test_store.py`. It owns exactly three things: the SQLite projection (schema + connection in WAL mode), the append-only journal writer, and `rebuild_from_journal`.

Out of scope, owned elsewhere or by a later story: path derivation (`paths.py` — call `paths.project_db_path(root)` and `paths.run_dir(run_id)`, never re-derive the data dir, the sha256 digest or the run directory); the state model tree (`models.py` — serialize and validate with `Run`/`StoryRun`/`SubtaskRun`/`PhaseRun`/`Attempt`/`Dispatch`/`RunConfig`/`HarnessAssignment` and the `Status`/`AttemptStatus`/`PhaseKind`/`Launcher` literals, never redefine them); phase result models (`ExploreResult`, `CriticResult`, …) which belong to the engine story; the engine's resume loop itself, milestone orchestration (census, levels, parallel stories, integrate), the CLI, and non-Claude harnesses.

The store is also independent of `brd`: it writes no board state and reads none, so `brd forget`/`purge` and agent-manager cleanup cannot affect each other (D5). Nothing in this module may take a repository worktree as a write base.

## Observable behaviour

**Two stores, one truth.** The journal at `paths.run_dir(run_id) / "journal.jsonl"` is the append-only truth. The SQLite file at `paths.project_db_path(root)` is a queryable projection of it, holding rows for runs, stories, subtasks, phases and attempts. Where they disagree the journal wins, and the projection is discardable at any time.

**Connection.** Opening the project DB creates its parent directories via `paths`, applies the schema idempotently (safe to call against an existing DB), and puts the connection in WAL journal mode. Reopening an existing DB must not destroy or migrate data — schema creation is `IF NOT EXISTS`-shaped.

**Journal lines.** One JSON object per line, newline-terminated, appended and flushed so a crash immediately after the call cannot lose the line. Every line carries at least: the monotonic sequence number, the run id, the card id, the phase name, the attempt number and the event payload. The sequence number is monotonically increasing within a run and never reused; it is derived from what is already on disk when the journal is opened, so an appender attached to a partially written journal continues the sequence rather than restarting it. Card, phase and attempt are null for events that are above that level (e.g. run-level status transitions).

**Write ordering is load-bearing.** Every state-changing operation appends the journal line *first* and writes/updates the SQLite row *second*. If the SQLite write fails or the process dies between the two, the journal is still correct and the projection is rebuildable. There is no code path that writes a row without a preceding journal line.

**`rebuild_from_journal(run_id)`** reads the journal for that run in sequence order, replays events into the model tree, and writes the resulting projection into the DB, replacing whatever rows that run had. A DB that was truncated, deleted or corrupted mid-run yields, after rebuild, the same query results the journal describes. Rebuild is idempotent: running it twice produces the same projection and does not duplicate rows.

**Attempts in flight.** An attempt whose journal has a `started` line but no terminal line (`ok`, `schema_invalid`, `gate_failed`, `harness_error`) is reconstructed with `status="started"` and no exit code, duration, tokens or cost. The store preserves that state rather than deleting or normalising it — the engine's resume logic is what discards it (§9 lines 370-373). Distinguishing in-flight from terminal attempts after a rebuild is the store's contract.

## Error paths

- A journal line that fails `models` validation — unknown key, bad status literal, missing required field — propagates the `pydantic.ValidationError` out of `rebuild_from_journal`. The `extra="forbid"` base is deliberate: an old-schema line must fail loudly, never be skipped, and never silently drop a field. No `try/except` that swallows it.
- A malformed (non-JSON) line likewise raises, naming the file and the line number so the operator can see which line is bad.
- `rebuild_from_journal` for a run with no journal file raises a clear error rather than silently producing an empty projection — an empty run and a missing run are different failures.
- SQLite write failures propagate; they do not corrupt the journal, which was already appended.
- Appending to a journal directory that cannot be created propagates the OS error from `paths`.

## Test list

All tests live in `tests/test_store.py`, mirroring `src/agent_manager/store.py`. Per the placement rule in spec §14, this module touches real files (SQLite DB, journal), so nothing here is in the "pure functions" tier; these are deterministic, no-network, temp-directory tests in the **"steps"-style I/O tier** — real temp DB files and real temp journals rather than mocks — and they stay in the default `uv run pytest` suite. None of them dispatches a harness, so none belongs in the single opt-in "end to end" test. The module docstring states this tier explicitly, following the sibling `tests/test_models.py` convention. `HOME`/`XDG_DATA_HOME` are pointed at a tmp path so `paths` writes nowhere real.

1. **Schema and WAL** ("steps" I/O tier) — opening the project DB creates the file under `paths.project_db_path(root)` and `PRAGMA journal_mode` reports `wal`; the expected tables for runs, stories, subtasks, phases and attempts exist.
2. **Re-open is non-destructive** ("steps" I/O tier) — opening an existing DB that already holds a run leaves its rows intact.
3. **Journal line shape** ("steps" I/O tier) — an appended event is one JSON line in `run_dir(run_id)/journal.jsonl` carrying run id, card, phase, attempt and a sequence number.
4. **Monotonic sequence across reopen** ("steps" I/O tier) — sequence numbers increase by one across appends, and a writer reopened against an existing journal continues from the last number rather than restarting at zero.
5. **Journal precedes the row** ("steps" I/O tier) — with the SQLite write forced to fail, the journal line is still present on disk and the projection is missing the row; a subsequent rebuild produces it. This is the write-ordering guarantee of §9 line 365.
6. **Truncated DB rebuilt from journal** ("steps" I/O tier) — the card's required test. Drive a multi-story/multi-subtask/multi-phase run through the store, truncate (or delete) the DB mid-run, call `rebuild_from_journal(run_id)`, and assert the reconstructed projection equals the pre-truncation state.
7. **Rebuild is idempotent** ("steps" I/O tier) — calling `rebuild_from_journal` twice leaves identical rows, with no duplicates.
8. **In-flight attempt survives rebuild** ("steps" I/O tier) — an attempt journalled as `started` with no terminal line rebuilds as `status="started"` with exit code, duration, tokens and cost all unset, and is distinguishable from a terminal attempt.
9. **Old-schema line fails loudly** ("steps" I/O tier) — a journal line with an extra/unknown key, and one with an invalid `Status` value, each raise `ValidationError` out of `rebuild_from_journal` instead of being skipped.
10. **Corrupt line fails loudly** ("steps" I/O tier) — a non-JSON line raises an error identifying the journal and the line.
11. **Missing journal** ("steps" I/O tier) — `rebuild_from_journal` on an unknown run id raises rather than returning an empty projection.
12. **Store writes nothing into the repo worktree** ("steps" I/O tier) — after a full run is recorded against a temp git-less repo dir, that directory is unchanged; all artifacts live under the temp data dir (D5 independence, and the §4 constraint that run artifacts never enter the worktree).

## Verification

`uv run pytest`. No separate lint or typecheck command in this project.
