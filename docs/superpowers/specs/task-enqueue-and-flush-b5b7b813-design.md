# Subtask b5b7b813 — Enqueue and flush outbox comments crash-safely

Card `b5b7b813-e97e-4dcb-adda-b8130ec15ec6`, under story 77b0a064 "Outbox and wiring", Milestone 12 (21f4cf06). Narrows plan Task 2.1 (`docs/superpowers/plans/2026-09-29-board-comments.md:86-104`) and board-comments design B6–B9 (`docs/superpowers/specs/2026-09-29-board-comments-design.md:86-103`).

## Scope

Files touched: `src/agent_manager/comments.py` (add `enqueue` and `flush` to the existing module) and `tests/test_comments.py` (add tests next to the existing `compose_*` tests). Nothing else.

Reused as-is, not modified: `Store.enqueue_comment`, `Store.pending_comments`, `Store.mark_comment_posted`, `Store.record_comment_failure`, `CommentRow`, `COMMENT_ATTEMPTS`, `LeaseLostError` (store.py); `board.write_lock`, `board.comment_list`, `board.comment_add`, `board.BoardComment`, `board.BoardError` (board.py); `Comment`, `compose_*`, `key()` (comments.py).

Base note: this worktree already contains the three plumbing branches (comments.py with `Comment`/`compose_*`, board.py comment calls, store.py outbox methods), so the interfaces above are importable here. On bare `master` they are not; the integrate stage must merge those branches first.

Out of scope (owned by siblings): any call site in `orchestrate.py`, `bases.py`, `cli.py` (65ed3c70, 5d9a875f); real-`brd` e2e proof `tests/e2e/test_board_comments.py` and README/spec docs (0f6ab456 / 649a8e88); any change to store.py or board.py.

## Interfaces

`enqueue(store, comment: Comment, *, run_id: str, now: datetime) -> None`

- Calls `store.enqueue_comment(run_id=run_id, card_id=comment.card_id, key=comment.key, body=comment.body, now=now)` and returns `None` (the store's inserted/ignored bool is discarded).
- Idempotent by key: a second enqueue of the same key leaves the existing row exactly as it was, whatever its state.
- Does not catch anything. In particular `store.LeaseLostError` (a `BaseException`) propagates and no row is written.
- Does not flush; callers (sibling tasks) call `flush` after.

`flush(store, root: Path, *, run_id: str | None = None, card_ids=None, board_api=board) -> list[str]`

- Reads `store.pending_comments(run_id, card_ids)` once (oldest `created_at` first, then insertion order) and processes rows in that order. Filters pass straight through with the store's semantics (both `None` = every pending row; empty `card_ids` = nothing).
- For each row, under `with board_api.write_lock(root):` (the same pattern as `steps/rollup.py:118`), never while a store transaction is open (X8):
  1. `existing = board_api.comment_list(row.card_id, repo_dir=root)`.
  2. If any `c` in `existing` has `c.body.rstrip().endswith(f"am-key: {row.key}")`, use `c.id`; otherwise `comment_id = board_api.comment_add(row.card_id, row.body, author="am", repo_dir=root)`.
  3. `store.mark_comment_posted(row.key, comment_id, now)` where `now` is the current UTC time (`datetime.now(timezone.utc)`, the repo's usual clock) taken by flush itself, since the plan signature has no `now` parameter.
- All board access goes through `board_api` (`write_lock`, `comment_list`, `comment_add`), never the `board` module directly, so tests can inject a fake.
- The body is posted exactly as stored; flush never edits it. Author is always `"am"`.
- Returns the list of warning strings (empty when everything posted).

## Error paths

- `board.BoardError` from `comment_list`/`comment_add`, or `locks.LockTimeoutError` from acquiring `board_api.write_lock` (the "timeout" of B7; board.py deliberately lets it propagate): call `store.record_comment_failure(row.key)`, append exactly one warning for that row, continue with the next row. Never raise these out of flush.
- The warning names the card id and the key. When `record_comment_failure` returns a count `>= COMMENT_ATTEMPTS` (the row is now `abandoned`, by the store's own 3-strike logic), that row's single warning says it was abandoned instead of "will retry". Flush does not implement its own counter.
- A failed row stays `pending` (until the third strike) and is picked up by a later flush.
- Anything else (e.g. `LeaseLostError` from the fenced store writes, an unexpected exception inside `mark_comment_posted`) is not caught and propagates. This is what makes the crash test meaningful: a crash between `comment_add` and marking leaves the row pending and the comment on the board, and the next flush's key check absorbs it.
- Flush never escalates, parks, or changes run status (B8). It only returns warnings.

Docs-in-code: the comments.py module docstring currently says the module is "Pure: no I/O, no `brd`, no store, no clock". Update it so `compose_*` stays described as pure and `enqueue`/`flush` are described as the outbox side (store + `board_api`).

## Tests

Tier rule (agent-manager design §14; board-comments design §6): pure functions and the outbox/flush logic are unit-tested in `tests/test_comments.py` against a fake `board_api` (no `brd` process, no network); real-`brd` against a temporary board is reserved for the opt-in e2e `tests/e2e/test_board_comments.py` (sibling 649a8e88). So every test below is **unit tier, `tests/test_comments.py`**, using a real temporary `Store` (as the existing store tests do) plus a fake `board_api` that records calls, holds per-card comment lists, and offers a `write_lock(root)` context manager. Fakes raise the real `board.BoardError`.

1. `enqueue` inserts a pending row with the comment's card id, key and body; a second enqueue of the same key adds nothing. Unit.
2. Pending rows post in order, oldest first: three enqueued comments → `comment_add` called in enqueue order with `author="am"` and unchanged bodies; every row marked posted with the id the fake returned; no warnings. Unit.
3. Every `comment_list`/`comment_add` call happens while the fake's `write_lock` is held, and the lock is taken per row. Unit.
4. Crash between post and mark (Review Focus 2): wrap the store so `mark_comment_posted` raises on its first call; first flush raises after `comment_add` succeeded; second flush finds the `am-key` line via `comment_list`, calls `comment_add` zero more times, and marks the row posted with the existing comment's id. Unit.
5. Board down then up (Review Focus 4): fake raises `BoardError` → flush returns one warning per row, rows remain in `pending_comments`, no exception; fake recovers → next flush posts them and returns no warnings. Unit.
6. Lock timeout: fake `write_lock` raises `locks.LockTimeoutError` → treated like a board failure (warning, row pending, failure counted). Unit.
7. Three strikes: one row fails on three successive flushes → after the third it is `abandoned`, no longer in `pending_comments`, and that flush returns exactly one warning saying so; a fourth flush does not touch it. Unit.
8. One failing row does not block others: card A's calls raise, card B's succeed → B posted, A pending, one warning. Unit.
9. Filters: `run_id`/`card_ids` restrict which rows are posted. Unit.
10. Lost lease (Review Focus 5): with the M10 two-store takeover pattern (`test_a_taken_over_store_writes_nothing`, `tests/test_store.py:2834`; the `stores` fixture lives in `tests/test_store.py:2606`, so `test_comments.py` defines an equivalent local fixture) — store `a` takes the lease, store `b` takes it over (`is_live=_dead`); `comments.enqueue(a, ...)` raises `store.LeaseLostError` and `board_comments` has no row for the key. Unit.
