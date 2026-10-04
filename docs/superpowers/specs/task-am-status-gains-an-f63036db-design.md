# `am status` gains an `integrity` key (card f63036db)

Parent story: ddf4da2c ("am status catches journal/projection divergence instead of trusting either blindly"). Source of truth: `docs/superpowers/specs/2026-10-03-journal-db-divergence-design.md` §3.3, §3.5, §3.7.

## Prerequisite

`store.diverging(lines, projection) -> list[Mismatch]` and `Mismatch`/`MismatchKind` landed in sibling 80dade04 on branch `m19/task-store-diverging-80dade04` and are not on master yet. This subtask's base must include that commit range (rebase or merge) before coding. This subtask only calls `diverging`; it never reimplements or modifies it.

## Scope

- A new `integrity_view` function in `src/agent_manager/cli.py`, in the same role as `control_view` (cli.py:247): it renders the integrity check into a plain dict.
- `status_for` (cli.py:1416-1460) runs the check after the run, lease and control state are loaded, on the same connection, and the payload gains a top-level `integrity` key. Every other payload key is unchanged.
- The `status_for` docstring's "Read-only: no `record_*` is called" stays literally true, and gets a sentence about the integrity check.

## Observable behavior

`integrity` is always present and has the shape `{"checked": bool, "reason": str | None, "mismatches": [Mismatch, ...]}`. The exit code is 0 in every case below. None of these paths returns an error envelope.

Decided in this order:

1. Live lease: if `lease is not None and control.lease_is_live(lease, now=now)` is true (using the same `lease` and `now` that `status_for` already holds — `lease_is_live` takes a `LeaseRow`, not `LeaseRow | None`, so the `None` guard is mandatory, the same guard `status_for`'s existing `claims` computation already uses), the result is `checked: false, reason: "lease is live", mismatches: []`. The journal is not read and `diverging` is not called, even if the projection was hand-edited (§3.5). Writes in flight during a live run are noise, not divergence. A run with no lease row at all (`lease is None`) falls through to step 2: it is not live, so it is checked.
2. Otherwise read the journal with `store_module.Journal._for_reading(run_id).read(ignore_torn_tail=True)`, the pattern `_journal_events` uses (cli.py:1634). Never call the `Journal(run_id)` constructor, because it creates the run directory through `paths.run_dir`, and never call `Store.open`.
   - `MissingJournalError` gives `checked: false, reason: "no journal"`. This covers a projection whose journal sits under a different `XDG_DATA_HOME` or data dir.
   - `JournalError` (which includes `CorruptJournalError`) or a pydantic `ValidationError` gives `checked: false, reason: "journal unreadable: <message>"`. These are the same classes `adopt` tolerates at dispatch.py:645-650.
   - This `try`/`except` must wrap the `store.diverging(lines, run)` call of step 3 as well as the `.read(...)` call above: `diverging` calls `replay` internally, and `replay` can itself raise `JournalError("journal contains no run_upsert line")` or a pydantic `ValidationError` while validating a payload, independently of what `.read()` already validated. Catching only around `.read()` and leaving `diverging()` bare would let one of those two errors escape uncaught and break the "exit 0 in every case" guarantee below.
3. Otherwise the result is `checked: true, reason: None, mismatches: [m ... for m in store.diverging(lines, run)]`, each one serialized JSON-mode (each `Mismatch` field is already a plain `str | None` or `dict`, so `dataclasses.asdict` — or equivalent field-by-field construction, the way `control_view` builds its dict from `LeaseRow`/`ControlRow` — produces exactly the shape below; do not rely on `render`'s `default=str` fallback, which would stringify a raw `Mismatch` object instead of emitting its fields). A projection with a stale (non-live) heartbeat, or no lease at all, is checked.

Each `Mismatch` serializes as `node` (`{story, card, phase, attempt}`, all `None` for the run itself), `field` (`"status"`, or `None` for a shape mismatch), `journal`, `projection` (status strings or `None`), and `kind` (`"stale"` or `"foreign"`). Order is the tree-walk order that `diverging` already produces. A torn final line (no trailing newline) is ignored. A newline-terminated non-JSON line is still a `CorruptJournalError`. Unknown event kinds are already skipped by `Journal.read` and `replay`, so they do not raise.

The check is report-only. It makes no `record_*` call and no row or journal write of any kind (§3.4). It does not file a control request.

## Out of scope

- `rebuild_from_journal`, its `force` parameter, `ProjectionDivergedError`, and the store.py module docstring, README integrity section or spec corrections. All of these belong to d8943ef5, which is blocked on this card.
- Any repair or write behavior, a standalone `am check` command, and comparing anything beyond status and tree shape. The whole parent story excludes these.
- Changes to `diverging`/`Mismatch`, which belong to 80dade04.

## Tests

All of these go in `tests/test_cli.py` and use or extend the existing `projection` fixture and its `_record` helper (tests/test_cli.py:3868-3902). Tier: unmarked `unit` for every one. The CLAUDE.md "Test tiers" placement rule assigns a tier by what a test spawns. These tests only write SQLite rows and journal files in `tmp_path` and spawn no `git`, `brd` or `claude`, so none of them is `git`.

1. Clean run: `integrity == {"checked": true, "reason": null, "mismatches": []}`, and every other payload key is byte-identical to the output before this change. (unit)
2. A run hand-edited to cancelled in the projection gives exactly one `foreign` mismatch at exit 0, and `control.requests` is still empty. (unit)
3. Read-only invariant: snapshot every table and the journal bytes before and after the status call, and assert both are unchanged. (unit)
4. A live lease gives `checked: false, reason: "lease is live"` against an edited projection. The same run with a stale heartbeat is checked and reports the mismatch. (unit)
5. No journal file gives `checked: false, reason: "no journal"` at exit 0, and no run directory gets created. This guards against the directory-creating `Journal(run_id)` constructor. (unit)
6. A torn final journal line is tolerated, and the result is checked with that line ignored. A newline-terminated non-JSON line gives `checked: false` with `reason` starting `"journal unreadable: "`. (unit)

## Verification

- `uv run pytest`
- Typecheck: none configured.
- Lint: none configured.
