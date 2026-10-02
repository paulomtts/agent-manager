# Add the board_comments outbox to the store (card 2e63c3d8)

Subtask of story 60189137 ("Plumbing: board calls, the outbox table, composed bodies"). This narrows decisions B6, B7 (abandon-at-3 counter only) and B9 of `docs/superpowers/specs/2026-09-29-board-comments-design.md` to the store layer. Base `master` (M9/M10/M11 merged); branch prefix `m12`.

## Scope

Only `src/agent_manager/store.py` and `tests/test_store.py` change.

In scope:
- A new row-only table in `_SCHEMA` (store.py:27-145), added with `CREATE TABLE IF NOT EXISTS` and no migration, in the same family as `checkpoints`, `run_controls` and `run_leases` (M9 C1 pattern):
  `board_comments(run_id TEXT NOT NULL, card_id TEXT NOT NULL, key TEXT PRIMARY KEY, body TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('pending','posted','abandoned')), comment_id TEXT, failed_attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, posted_at TEXT)`.
- A frozen dataclass `CommentRow(run_id, card_id, key, body, state, comment_id, failed_attempts)` plus a `_comment_from_row` helper. These follow the `Checkpoint`/`_checkpoint_from_row` convention (store.py:614-635) and the `LeaseRow`/`_lease_from_row` convention (store.py:672-690).
- Four `Store` methods, listed below.
- Update the Store class docstring (store.py:929-946) so it names `board_comments` among the row-only tables.

Out of scope, owned by siblings:
- `board.py` and every `brd` call belong to 3fb36324, which is done. brd is only ever called from `board.py`.
- `comments.py` belongs to 476f1040. That covers compose, keys, caps, escaping, `enqueue`/`flush` orchestration, the "abandoned" report warning, and the fake-board flush tests.
- Wiring into orchestrate, bases and cli belongs to later stories.

## Observable behavior

- `enqueue_comment(*, run_id, card_id, key, body, now) -> bool` runs `INSERT OR IGNORE` keyed on `key`, with `state='pending'`, `failed_attempts=0` and `created_at=now`. It returns True if a row was inserted. It returns False if the key already existed, and in that case the existing row is left untouched: its body, state and run_id are not overwritten. That is the B9 replay/resume idempotency. The write runs under `with self._lock, self._fenced():` and commits through `self._commit()`, exactly like `save_checkpoint` (store.py:1274-1340) and the `record_*` writers. It is not a bare `immediate()` call. With a lease token bound, a lost or foreign lease raises `LeaseLostError` and nothing is written. With no token bound, it commits normally.
- `pending_comments(run_id: str | None = None, card_ids: Iterable[str] | None = None) -> list[CommentRow]` returns only rows where `state='pending'`, oldest first, ordered by `created_at` with insertion order (rowid) as the tie-break. Each filter that is given is ANDed with the others. With both filters at None it returns every pending row across all runs. `card_ids` matches across runs, which is what relaunch needs. An empty `card_ids` returns `[]`.
- `mark_comment_posted(key, comment_id, now)` sets `state='posted'`, `comment_id` and `posted_at=now`. After that the row no longer appears in `pending_comments`.
- `record_comment_failure(key) -> int` increments `failed_attempts` and returns the new value. When the value reaches 3 it sets `state='abandoned'`, and the row drops out of `pending_comments`. This method emits no warning.
- The read methods take `self._lock`. The mutating methods (`mark_comment_posted`, `record_comment_failure`) use the same lock-plus-`_fenced()` writer pattern as `enqueue_comment`.

## Error paths

- A `sqlite3.Error` during a write rolls back and re-raises, following the `save_checkpoint` try/except pattern.
- If `mark_comment_posted` or `record_comment_failure` gets a key that does not exist, it does nothing: no row is created and no exception is raised. `record_comment_failure` then returns 0. (This is a narrowing choice. The findings do not fix it, and an outbox owner may race a deleted row.)
- A `state` value outside the three allowed ones is rejected by the CHECK constraint (`sqlite3.IntegrityError`).
- Under a bound token whose lease is gone or held by another token, every writer raises `LeaseLostError` and leaves the table unchanged.

## Tests

All of these go in `tests/test_store.py` as direct, unmarked store-level tests that reuse the existing `repo` fixture and follow the checkpoint/lease test style at tests/test_store.py:1768-2041. The tier comes from the main design spec §14 together with the board-comments spec §6. This is pure sqlite projection logic with no git, brd or harness involvement, i.e. the "Outbox" half of that section, so none of these tests go in `tests/e2e` or any orchestrate or integration file. All of them stay inside the conftest data-directory isolation guard.

1. `board_comments` exists on a fresh store, and opening an existing DB that lacks it creates it, with no migration. Tier: store unit (test_store.py).
2. `enqueue_comment` inserts a pending row and returns True. Calling it again with the same key and a different body returns False and the original body and state are kept. Tier: store unit.
3. An enqueue under a bound token whose lease was taken by another token raises `LeaseLostError` and writes no row. A matching token succeeds. Tier: store unit.
4. `pending_comments` returns rows oldest first. It filters by `run_id`, and by `card_ids` across two runs. Combined filters are ANDed. An empty `card_ids` gives `[]`. Tier: store unit.
5. After `mark_comment_posted`, the state is `posted` with `comment_id` and `posted_at` set, and the row is gone from `pending_comments`. Tier: store unit.
6. `record_comment_failure` returns 1, then 2, then 3. After the third call the state is `abandoned` and the row is excluded from `pending_comments`. Tier: store unit.
7. With an unknown key, `mark_comment_posted` and `record_comment_failure` do nothing (the latter returns 0). Tier: store unit.
8. An invalid `state` written directly is rejected by the CHECK constraint. Tier: store unit.

## Verification

- Full suite: `uv run pytest` (whole suite, including `tests/e2e`, must stay green)
- Typecheck: none
- Lint: none
