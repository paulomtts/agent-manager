# Record control requests and leases in the store (card b973aa1d, plan Task 1.1)

Narrows `docs/superpowers/specs/2026-09-27-live-control-design.md` (decisions C1–C12) and Task 1.1 of `docs/superpowers/plans/2026-09-27-live-control.md` to this subtask. Both files are on the `docs/live-control` branch (`.claude/worktrees/docs-live-control/`), not on `master`. Parent story: ad03d386 "Control state and the park with a reason". Commit message: `feat(store): control requests, run leases and the cancelled status`.

## Scope

This subtask changes only the store and model layers: `src/agent_manager/models.py` and `src/agent_manager/store.py`, with tests in `tests/test_store.py` and `tests/test_models.py`. It adds no CLI, no async code, no watcher, no `Lease` class, and no `StopSignal` changes. Those belong to sibling 3113456a (Task 1.2: `runtime/stop.py`, new `control.py`), which consumes the names below exactly as given. Do not add anything else.

## Deliverables

1. **`models.Status`** (models.py:25) gains `"cancelled"`: `Literal["pending", "started", "done", "failed", "escalated", "stopped", "cancelled"]`.
2. **`_SCHEMA`** (store.py:28–106) gains two `CREATE TABLE IF NOT EXISTS` tables after `checkpoints`:
   - `run_controls`: columns `run_id TEXT NOT NULL`, `seq INTEGER NOT NULL`, `lease TEXT NOT NULL`, `command TEXT NOT NULL CHECK (command IN ('pause','cancel'))`, `requested_at TEXT NOT NULL`, `handled_at TEXT`, with `PRIMARY KEY (run_id, seq)`.
   - `run_leases`: columns `run_id TEXT PRIMARY KEY`, `token TEXT NOT NULL`, `pid INTEGER NOT NULL`, `host TEXT NOT NULL`, `acquired_at TEXT NOT NULL`, `heartbeat_at TEXT NOT NULL`, `accepting INTEGER NOT NULL`.

   No existing table gains a column and no CHECK constraint changes. `runs.status` has no CHECK, so `cancelled` needs no schema change. An existing pre-M9 database gets the two tables the next time `open_db` runs.
3. **Row types** (frozen dataclasses):
   - `LeaseRow(run_id: str, token: str, pid: int, host: str, acquired_at: datetime, heartbeat_at: datetime, accepting: bool)`
   - `ControlRow(run_id: str, seq: int, lease: str, command: str, requested_at: datetime, handled_at: datetime | None)`

   Timestamps are stored as text and parsed back the same way the existing store does. `accepting` comes back as a real `bool`.
4. **Free functions over a `sqlite3.Connection`**, in the style of `load_run` (store.py:416):
   - `run_status(conn, run_id) -> str | None` returns `runs.status`, or `None` if the run is unknown.
   - `read_lease(conn, run_id) -> LeaseRow | None`.
   - `control_requests(conn, run_id, *, lease: str | None = None) -> list[ControlRow]` returns rows in `seq` order. With `lease=None` it returns every lease's rows.
   - `@contextmanager immediate(conn)`:
     - If `conn.in_transaction`, commit the implicit transaction first.
     - Run `conn.execute("BEGIN IMMEDIATE")`.
     - Yield.
     - On normal exit, `conn.commit()`. On any exception, `conn.rollback()` and re-raise.

     This handles Python sqlite3's legacy-transaction behaviour.
   - `add_control(conn, run_id, *, lease, command, requested_at) -> ControlRow` inserts a row with `seq = max(seq) + 1` for that run, starting at 0, and `handled_at` NULL. It does not commit; it is meant to run inside `immediate`. An unknown `command` raises `sqlite3.IntegrityError` from the CHECK constraint. That CHECK is the only guard: no Python-side validation.
5. **`Store` methods.** Each one runs under `self._lock` and commits. Each is keyed to `self.run_id`.
   - `acquire_lease(*, token, pid, host, now) -> LeaseRow` upserts on `run_id` and sets `accepting = 1` and `acquired_at = heartbeat_at = now`.
   - `beat(token, now) -> None` sets `heartbeat_at`.
   - `close_window(token) -> None` sets `accepting = 0`.
   - `release_lease(token) -> None` deletes the row.

     `beat`, `close_window` and `release_lease` touch only the row whose `token` matches. With any other token they silently do nothing: no error.
   - `pending_controls(token) -> list[ControlRow]` returns rows for this run and this lease where `handled_at IS NULL`, in `seq` order.
   - `mark_control_handled(seq, now) -> None` sets `handled_at` on this run's row `seq`.
6. **`Store.latest_open_checkpoint(card_id, workflow)`** (store.py:922–944) changes as follows:
   - The newest-row query now `LEFT JOIN`s `runs` on `r.id = c.run_id`.
   - If the card's newest row (any run, any workflow) is `done`, the method returns `None`. It also returns `None` if that row belongs to a run whose status is `cancelled`.
   - Otherwise the second query is the same as today, with the added condition `AND run_id NOT IN (SELECT id FROM runs WHERE status = 'cancelled')`.

   The existing ordering stays: `saved_at DESC, seq DESC`. Update the docstring.
7. **Journal isolation.** Nothing journals `run_controls` or `run_leases`. `rebuild_from_journal` and `_delete_run` (store.py:948–996) leave both tables untouched. A `cancelled` run status round-trips through the journal replay and `list_runs` the same way `stopped` does.

## Error paths

- An unknown `command` in `add_control` raises `sqlite3.IntegrityError`.
- An exception inside `immediate` rolls back and re-raises, so no partial rows are left.
- A lease method called with a wrong token does nothing.
- `read_lease` and `run_status` return `None` when there is no row.

## Tests

Tier per the milestone spec §7 placement rule: tests sit beside the module they exercise, and the store/DB layer goes in `tests/test_store.py`. The plan's test snippets use fixture names that do not exist on master (`opened_store`, `root`, `_project`, `_save`, `T0`/`T1`). Adapt them to the file's real conventions: the `repo` fixture, `store.Store.open(repo, RUN_ID)`, `_save_checkpoint(st, card, reason=..., saved_at=_at(i))`, `_run(repo, run_id)`. A "second process" is a second `store.open_db` connection.

- `tests/test_models.py` (models layer): `Status` accepts `"cancelled"`, and a `Run` or subtask model validates with it.
- `tests/test_store.py` (store layer):
  - `test_the_control_tables_appear_on_an_existing_database`: build a pre-M9 schema by hand, then `open_db` adds `run_controls` and `run_leases`.
  - `test_a_lease_is_touched_only_through_its_own_token`:
    - `acquire_lease` returns a row with `accepting=True` and `heartbeat_at == now`.
    - `beat`, `close_window` and `release_lease` with the token `"other"` leave the row equal to the original.
    - `beat` then `close_window` with the right token updates `heartbeat_at` and sets `accepting is False`.
    - `release_lease` with the right token makes `read_lease` return `None`.
  - `test_a_request_from_another_connection_is_pending_for_its_lease_only`:
    - A second connection calls `add_control` twice inside `immediate`, one `pause` under lease `t1` and one `cancel` under lease `old`.
    - `pending_controls("t1")` returns `["pause"]`.
    - After `mark_control_handled(0, T1)` it returns empty.
    - `control_requests(other, run_id)` shows `handled_at` as `[T1, None]`.
  - `test_immediate_rolls_back_on_error`: `add_control` followed by an exception inside `immediate` leaves no row, and the exception propagates.
  - `test_an_unknown_command_is_refused_by_the_check`: `add_control(..., command="resume")` raises `sqlite3.IntegrityError`.
  - `test_a_cancelled_runs_checkpoints_are_never_open` (Review Focus 4):
    - A `stopped` run and a later `cancelled` run both leave `parked` rows for card `c1`; `latest_open_checkpoint("c1", "task")` is `None`.
    - An unrelated card `c2` with a `parked` row in a `stopped` run still resolves to that run.
  - `test_a_cancelled_run_round_trips_through_the_journal_and_the_listing`: a run recorded with status `cancelled` survives `rebuild_from_journal` and shows as `cancelled` in `list_runs`.
  - Also assert that `rebuild_from_journal` leaves the `run_controls` and `run_leases` rows intact. This can be folded into the round-trip test.

Verification: `uv run pytest` passes for the whole suite, including `tests/e2e`. The work is on its own `m9`-prefixed branch off `master`, and nothing is pushed.

## Note on inputs

The exploration summary handed to this stage was truncated at 8000 characters, in the middle of the test-placement paragraph. That means the upstream stage over-ran its brief. The placement rule above was confirmed against the plan's Task 1.1 file list (`tests/test_store.py`, `tests/test_models.py`) rather than inferred from the missing text.
