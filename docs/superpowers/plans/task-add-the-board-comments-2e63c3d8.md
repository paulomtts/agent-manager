<!-- task-pipeline: validated -->
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

---

# board_comments Outbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the row-only `board_comments` outbox table to the SQLite projection, with a frozen `CommentRow` and four fenced/locked `Store` methods (`enqueue_comment`, `pending_comments`, `mark_comment_posted`, `record_comment_failure`).

**Architecture:** One more `CREATE TABLE IF NOT EXISTS` in `_SCHEMA`, so `open_db` creates it on fresh and existing databases alike with no migration. The Store methods sit in a new "board comment outbox" section of `Store`, beside the checkpoint section; every writer is `with self._lock, self._fenced():` plus a `try/except sqlite3.Error: rollback; raise` around its SQL and `self._commit()`, copied from `save_checkpoint`. Nothing touches the journal, `board.py`, or any `brd` call.

**Tech Stack:** Python 3, stdlib `sqlite3`, `dataclasses`, pytest, run with `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m12/task-add-the-board-comments-2e63c3d8/docs/superpowers/specs/task-add-the-board-comments-2e63c3d8-design.md` (reproduced verbatim above). Parent decisions: `docs/superpowers/specs/2026-09-29-board-comments-design.md` B6, B7, B9.

## Global Constraints

- Only `src/agent_manager/store.py` and `tests/test_store.py` change. No `board.py`, no `comments.py`, no `brd` call, no CLI or orchestrate wiring.
- Table shape, exactly: `board_comments(run_id TEXT NOT NULL, card_id TEXT NOT NULL, key TEXT PRIMARY KEY, body TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('pending','posted','abandoned')), comment_id TEXT, failed_attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, posted_at TEXT)`.
- Added via `CREATE TABLE IF NOT EXISTS` in `_SCHEMA`; no migration code.
- `CommentRow(run_id, card_id, key, body, state, comment_id, failed_attempts)`, `@dataclass(frozen=True)`, built by `_comment_from_row`.
- Every writer: `with self._lock, self._fenced():`, commit through `self._commit()`, `except sqlite3.Error: self._conn.rollback(); raise`. Not a bare `immediate()`.
- Readers take `self._lock`.
- Abandon threshold is 3 failures. No warning is emitted by the store.
- Unknown key to `mark_comment_posted` / `record_comment_failure` is a silent no-op; `record_comment_failure` returns 0.
- All tests are unmarked, in `tests/test_store.py`, using the existing `repo` / `stores` fixtures (which redirect `XDG_DATA_HOME` and `HOME` into `tmp_path`). None in `tests/e2e`.
- Verification: `uv run pytest` (no typecheck, no lint).

## Implementation note: "INSERT OR IGNORE keyed on key"

The spec's semantics are "insert, or ignore when `key` already exists". The plan implements that as `INSERT ... ON CONFLICT(key) DO NOTHING`, not the literal `INSERT OR IGNORE`. SQLite's `OR IGNORE` resolution also silently skips NOT NULL and CHECK violations, so a `None` body would be swallowed and `enqueue_comment` would return False as if the key existed. `ON CONFLICT(key) DO NOTHING` ignores only the key collision, so every other constraint still raises `sqlite3.IntegrityError` and rolls back, as the spec's error paths require. The codebase already uses `ON CONFLICT(...)` upserts (`take_lease`, store.py:1450-1466). Review Focus item 5 pins this.

## Review Focus

1. `record_comment_failure` called on a row that is already `posted` (a late failure report racing a success) must not flip it to `abandoned`: the comment really is on the board. Expected: state stays `posted`. Test: Task 4, `test_a_failure_on_a_posted_comment_never_abandons_it`.
2. A taken-over store calling `mark_comment_posted` or `record_comment_failure` must raise `LeaseLostError` and leave the row exactly as it was (spec: "every writer"), not only `enqueue_comment`. Test: Task 4, `test_a_taken_over_store_neither_marks_nor_fails_a_comment`.
3. Two comments enqueued with the same `now` (one tick composes several bodies) must come back in the order they were enqueued, not key order. Test: Task 3, `test_pending_comments_breaks_a_created_at_tie_by_insertion_order`.
4. A database error during `enqueue_comment` must roll back, leave no transaction open and release the lock, so the next enqueue works. Test: Task 2, `test_a_refused_enqueue_rolls_back_and_writes_nothing`.
5. A `None` body (a compose bug upstream) must raise `sqlite3.IntegrityError`, not be silently ignored and reported as "already enqueued". Test: Task 2, `test_an_enqueue_with_no_body_is_refused_not_ignored`.

---

## File Structure

- Modify `src/agent_manager/store.py`:
  - `_SCHEMA` (lines 29-145): append the `board_comments` table after `run_claims`.
  - New module constant `COMMENT_ATTEMPTS = 3` and `CommentRow` / `_comment_from_row`, placed after `ControlRow` / `_control_from_row` / `control_requests` (ends line 871) and before `immediate` (line 874).
  - `Store` class docstring (lines 930-948): name `board_comments` among the row-only tables.
  - New `Store` section "board comment outbox" inserted after `latest_open_checkpoint` (ends line 1408) and before the "leases, claims and control requests" comment block (line 1410).
- Modify `tests/test_store.py`: append a new section at the end of the file (after `test_replay_journal_writes_nothing`, line 3449-3463). It reuses `repo` (line 33), `stores` (line 605-617), `RUN_ID` (line 30), `OTHER_RUN_ID` (line 1741), `_at` (line 1744), `_held_elsewhere` (line 1278), `_alive` / `_dead` (lines 2446-2451). `sqlite3`, `dataclasses`, `pytest`, `store`, `paths` are already imported at the top.

---

### Task 1: The `board_comments` table

**Files:**
- Modify: `src/agent_manager/store.py:139-145` (end of `_SCHEMA`), `src/agent_manager/store.py:930-948` (Store docstring)
- Test: `tests/test_store.py` (append at end of file)

**Interfaces:**
- Consumes: nothing new.
- Produces: table `board_comments` with columns, in order, `run_id, card_id, key, body, state, comment_id, failed_attempts, created_at, posted_at`, created by every `store.open_db(root)`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python


# -- board comment outbox ----------------------------------------------------------
#
# Board-comments spec B6/B7/B9: a row-only `board_comments` table outside the
# journal, like `checkpoints` and `run_leases`. Steps tier: real temp DB, no
# harness, no brd. The fake-board flush tests belong to `comments.py`.

_COMMENT_COLUMNS = [
    "run_id",
    "card_id",
    "key",
    "body",
    "state",
    "comment_id",
    "failed_attempts",
    "created_at",
    "posted_at",
]


def test_open_db_creates_the_board_comments_table(repo):
    conn = store.open_db(repo)
    try:
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(board_comments)").fetchall()
        ]
    finally:
        conn.close()
    assert columns == _COMMENT_COLUMNS


def test_the_board_comments_table_appears_on_an_existing_database(repo):
    # A pre-M12 database: every table but the new one, with a row in it.
    first = store.open_db(repo)
    first.execute("DROP TABLE IF EXISTS board_comments")
    first.execute(
        "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
        " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (RUN_ID, "milestone", str(repo), "main", "m1/", "stopped", None, "{}"),
    )
    first.commit()
    first.close()

    conn = store.open_db(repo)
    try:
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(board_comments)").fetchall()
        ]
        kept = [row["id"] for row in conn.execute("SELECT id FROM runs").fetchall()]
    finally:
        conn.close()

    assert columns == _COMMENT_COLUMNS
    assert kept == [RUN_ID]


def test_a_board_comment_with_an_unknown_state_is_refused(repo):
    conn = store.open_db(repo)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO board_comments (run_id, card_id, key, body, state,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (RUN_ID, "card-a", "k-bogus", "body", "bogus", _at(0).isoformat()),
            )
        conn.rollback()
        count = conn.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]
    finally:
        conn.close()
    assert count == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "board_comments_table or unknown_state" -v`
Expected: FAIL. The two `columns` tests fail with `assert [] == ['run_id', ...]` (PRAGMA on a missing table returns no rows); the CHECK test fails with `sqlite3.OperationalError: no such table: board_comments`.

- [ ] **Step 3: Add the table to `_SCHEMA`**

In `src/agent_manager/store.py`, replace the end of `_SCHEMA`:

```python
CREATE TABLE IF NOT EXISTS run_claims (
    key        TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    token      TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);
"""
```

with:

```python
CREATE TABLE IF NOT EXISTS run_claims (
    key        TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    token      TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_comments (
    run_id          TEXT NOT NULL,
    card_id         TEXT NOT NULL,
    key             TEXT PRIMARY KEY,
    body            TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('pending', 'posted', 'abandoned')),
    comment_id      TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    posted_at       TEXT
);
"""
```

- [ ] **Step 4: Name the table in the Store docstring**

In `src/agent_manager/store.py`, in the `Store` class docstring, replace:

```python
    The exceptions are `checkpoints` (pygents spec §6), `run_controls` and
    `run_leases` (live control C1/C2): row-only tables outside the journal.
```

with:

```python
    The exceptions are `checkpoints` (pygents spec §6), `run_controls` and
    `run_leases` (live control C1/C2) and `board_comments` (board-comments
    B6): row-only tables outside the journal.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "board_comments_table or unknown_state" -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat: add the board_comments outbox table to the store"
```

---

### Task 2: `Store.enqueue_comment`

**Files:**
- Modify: `src/agent_manager/store.py` (new section in `Store`, after `latest_open_checkpoint`, before the `# -- leases, claims and control requests` comment)
- Test: `tests/test_store.py` (append at end of file)

**Interfaces:**
- Consumes: table `board_comments` (Task 1).
- Produces: `Store.enqueue_comment(self, *, run_id: str, card_id: str, key: str, body: str, now: datetime) -> bool`. Test helper `_enqueue(st, key, *, card_id="card-a", run_id=RUN_ID, body="body", now=None) -> bool` and `_comment_row(conn, key) -> sqlite3.Row | None`, both used by Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python


def _enqueue(
    st: store.Store,
    key: str,
    *,
    card_id: str = "card-a",
    run_id: str = RUN_ID,
    body: str = "body",
    now: datetime | None = None,
) -> bool:
    return st.enqueue_comment(
        run_id=run_id,
        card_id=card_id,
        key=key,
        body=body,
        now=_at(0) if now is None else now,
    )


def _comment_row(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM board_comments WHERE key = ?", (key,)).fetchone()


def test_enqueue_comment_inserts_once_and_never_overwrites(repo):
    body = "## Done\n\nmerged `m12/x` — é\n"
    st = store.Store.open(repo, RUN_ID)
    try:
        first = _enqueue(st, "k1", body=body, now=_at(1))
        again = _enqueue(
            st, "k1", run_id=OTHER_RUN_ID, card_id="card-b", body="other", now=_at(2)
        )
        row = _comment_row(st.connection, "k1")
        count = st.connection.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]
        journal_exists = st.journal.path.exists()
    finally:
        st.close()

    assert first is True
    assert again is False
    assert count == 1
    assert row is not None
    assert dict(row) == {
        "run_id": RUN_ID,
        "card_id": "card-a",
        "key": "k1",
        "body": body,
        "state": "pending",
        "comment_id": None,
        "failed_attempts": 0,
        "created_at": _at(1).isoformat(),
        "posted_at": None,
    }
    # Row-only: the outbox never appends a journal line (the journal file is
    # only created by its first append).
    assert journal_exists is False


def test_an_enqueue_under_a_lost_lease_raises_and_writes_nothing(stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)

    with pytest.raises(store.LeaseLostError) as caught:
        _enqueue(a, "k1")
    assert caught.value.holder is not None and caught.value.holder.token == "t2"
    assert a.connection.in_transaction is False
    assert _comment_row(b.connection, "k1") is None

    # The store holding the lease writes under its own fence and commits it:
    # `a`, a separate connection, sees the committed row.
    assert _enqueue(b, "k1") is True
    assert b.connection.in_transaction is False
    assert _comment_row(a.connection, "k1") is not None


def test_a_refused_enqueue_rolls_back_and_writes_nothing(repo, stores):
    # Review Focus 4: a database error inside the fence rolls back cleanly.
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)

    saboteur = store.open_db(repo)
    try:
        saboteur.execute(
            "CREATE TRIGGER refuse_comments BEFORE INSERT ON board_comments"
            " BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
        saboteur.commit()
    finally:
        saboteur.close()

    with pytest.raises(sqlite3.IntegrityError):
        _enqueue(st, "k1")
    assert st.connection.in_transaction is False
    assert _held_elsewhere(st._lock) is False
    assert _comment_row(st.connection, "k1") is None

    st.connection.execute("DROP TRIGGER refuse_comments")
    st.connection.commit()
    assert _enqueue(st, "k1") is True


def test_an_enqueue_with_no_body_is_refused_not_ignored(repo):
    # Review Focus 5: only a key collision is ignored; NOT NULL still raises.
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _enqueue(st, "k1", body=None)  # type: ignore[arg-type]
        assert st.connection.in_transaction is False
        assert _comment_row(st.connection, "k1") is None
        assert _enqueue(st, "k1") is True
    finally:
        st.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "enqueue" -v`
Expected: FAIL, 4 tests, each with `AttributeError: 'Store' object has no attribute 'enqueue_comment'`.

- [ ] **Step 3: Implement `enqueue_comment`**

In `src/agent_manager/store.py`, insert between the end of `latest_open_checkpoint` (the line `            return None if row is None else _checkpoint_from_row(row)` that closes it) and the comment line `    # -- leases, claims and control requests -----------------------------------`:

```python
    # -- board comment outbox ------------------------------------------------
    #
    # A row-only table outside the journal (board-comments B6, B9): nothing
    # here calls `self._journal`, and `rebuild_from_journal` leaves the rows
    # alone. Every writer holds the store lock and the fence of the bound
    # lease token, like `save_checkpoint`. Posting to the board is not this
    # module's job: `comments.py` drains the outbox through `board.py`.

    def enqueue_comment(
        self,
        *,
        run_id: str,
        card_id: str,
        key: str,
        body: str,
        now: datetime,
    ) -> bool:
        """Queue `body` for `card_id` under `key`, once (B9).

        True when a `pending` row was inserted; False when `key` already had a
        row, which is left exactly as it was, whatever its state. Only the key
        collision is ignored (`ON CONFLICT(key) DO NOTHING`, not `OR IGNORE`):
        a NULL body or any other refused value raises `sqlite3.IntegrityError`
        and rolls back.
        """
        with self._lock, self._fenced():
            try:
                cursor = self._conn.execute(
                    "INSERT INTO board_comments (run_id, card_id, key, body, state,"
                    " comment_id, failed_attempts, created_at, posted_at)"
                    " VALUES (?, ?, ?, ?, 'pending', NULL, 0, ?, NULL)"
                    " ON CONFLICT(key) DO NOTHING",
                    (run_id, card_id, key, body, _iso(now)),
                )
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
            return cursor.rowcount == 1

```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "enqueue" -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat: enqueue board comments idempotently under the lease fence"
```

---

### Task 3: `CommentRow` and `Store.pending_comments`

**Files:**
- Modify: `src/agent_manager/store.py` (module level after `control_requests`, before `@contextmanager def immediate`; `Store` outbox section after `enqueue_comment`)
- Test: `tests/test_store.py` (append at end of file)

**Interfaces:**
- Consumes: `Store.enqueue_comment(*, run_id, card_id, key, body, now) -> bool` (Task 2); test helper `_enqueue(st, key, *, card_id="card-a", run_id=RUN_ID, body="body", now=None)` (Task 2).
- Produces: `store.CommentRow` (frozen dataclass: `run_id: str`, `card_id: str`, `key: str`, `body: str`, `state: str`, `comment_id: str | None`, `failed_attempts: int`); `store._comment_from_row(row: sqlite3.Row) -> CommentRow`; `Store.pending_comments(self, run_id: str | None = None, card_ids: Iterable[str] | None = None) -> list[CommentRow]`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python


def test_pending_comments_filters_by_run_and_cards_oldest_first(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k1", card_id="card-a", run_id=RUN_ID, body="one", now=_at(2))
        _enqueue(st, "k2", card_id="card-b", run_id=RUN_ID, body="two", now=_at(1))
        _enqueue(st, "k3", card_id="card-a", run_id=OTHER_RUN_ID, body="three", now=_at(0))

        def keys(**filters) -> list[str]:
            return [row.key for row in st.pending_comments(**filters)]

        every = st.pending_comments()
        by_run = keys(run_id=RUN_ID)
        by_cards = keys(card_ids=["card-a"])
        both = keys(run_id=RUN_ID, card_ids=["card-a"])
        empty = keys(card_ids=[])
        one_shot = keys(card_ids=iter(["card-b"]))
        unknown = keys(card_ids=("card-never",), run_id=OTHER_RUN_ID)
    finally:
        st.close()

    assert [row.key for row in every] == ["k3", "k2", "k1"]
    assert every[0] == store.CommentRow(
        run_id=OTHER_RUN_ID,
        card_id="card-a",
        key="k3",
        body="three",
        state="pending",
        comment_id=None,
        failed_attempts=0,
    )
    assert by_run == ["k2", "k1"]
    # card_ids reaches across runs: relaunch finds an older run's rows.
    assert by_cards == ["k3", "k1"]
    assert both == ["k1"]
    assert empty == []
    assert one_shot == ["k2"]
    assert unknown == []
    with pytest.raises(dataclasses.FrozenInstanceError):
        every[0].state = "posted"  # type: ignore[misc]


def test_pending_comments_breaks_a_created_at_tie_by_insertion_order(repo):
    # Review Focus 3: one tick composes several bodies with the same `now`.
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k-b", now=_at(5))
        _enqueue(st, "k-a", now=_at(5))
        _enqueue(st, "k-c", now=_at(5))
        found = [row.key for row in st.pending_comments(run_id=RUN_ID)]
    finally:
        st.close()
    assert found == ["k-b", "k-a", "k-c"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "pending_comments" -v`
Expected: FAIL, 2 tests, with `AttributeError: 'Store' object has no attribute 'pending_comments'`.

- [ ] **Step 3: Add `CommentRow` and `_comment_from_row`**

In `src/agent_manager/store.py`, insert after the `control_requests` function (which ends with `    return [_control_from_row(row) for row in rows]`) and before `@contextmanager` / `def immediate`:

```python


COMMENT_ATTEMPTS = 3
"""Failed posts after which a `board_comments` row is `abandoned` (board-comments
B7). The warning that names an abandoned row belongs to `comments.py`."""


@dataclass(frozen=True)
class CommentRow:
    """One queued outcome comment: a row of `board_comments` (board-comments B6).

    Row-only and outside the journal, like `Checkpoint`. `key` is the
    idempotency key a replay or resume enqueues again (B9); `comment_id` is
    the board's id once posted, else `None`.
    """

    run_id: str
    card_id: str
    key: str
    body: str
    state: str
    comment_id: str | None
    failed_attempts: int


def _comment_from_row(row: sqlite3.Row) -> CommentRow:
    return CommentRow(
        run_id=row["run_id"],
        card_id=row["card_id"],
        key=row["key"],
        body=row["body"],
        state=row["state"],
        comment_id=row["comment_id"],
        failed_attempts=row["failed_attempts"],
    )
```

- [ ] **Step 4: Add `pending_comments`**

In `src/agent_manager/store.py`, inside `Store`, immediately after the `enqueue_comment` method (after its `            return cursor.rowcount == 1` line), insert:

```python

    def pending_comments(
        self,
        run_id: str | None = None,
        card_ids: Iterable[str] | None = None,
    ) -> list[CommentRow]:
        """Every `pending` row, oldest `created_at` first, then insertion order.

        Each given filter narrows the result and they are ANDed; with neither,
        every pending row of every run is returned. `card_ids` matches across
        runs, which is what a relaunch needs; an empty `card_ids` matches
        nothing.
        """
        clauses = ["state = 'pending'"]
        params: list[str] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if card_ids is not None:
            cards = list(card_ids)
            if not cards:
                return []
            clauses.append(f"card_id IN ({', '.join('?' for _ in cards)})")
            params.extend(cards)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM board_comments WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at, rowid",
                params,
            ).fetchall()
            return [_comment_from_row(row) for row in rows]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "pending_comments" -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat: read pending board comments by run and cards, oldest first"
```

---

### Task 4: `Store.mark_comment_posted` and `Store.record_comment_failure`

**Files:**
- Modify: `src/agent_manager/store.py` (`Store` outbox section, after `pending_comments`)
- Test: `tests/test_store.py` (append at end of file)

**Interfaces:**
- Consumes: `Store.enqueue_comment` (Task 2), `Store.pending_comments` and `COMMENT_ATTEMPTS = 3` (Task 3); test helpers `_enqueue` and `_comment_row(conn, key)` (Task 2).
- Produces: `Store.mark_comment_posted(self, key: str, comment_id: str, now: datetime) -> None`; `Store.record_comment_failure(self, key: str) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python


def test_mark_comment_posted_moves_the_row_out_of_pending(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k1", now=_at(0))
        _enqueue(st, "k2", now=_at(1))
        st.mark_comment_posted("k1", "c-101", _at(3))
        row = _comment_row(st.connection, "k1")
        pending = [r.key for r in st.pending_comments()]
        in_transaction = st.connection.in_transaction
    finally:
        st.close()

    assert row is not None
    assert (row["state"], row["comment_id"], row["posted_at"]) == (
        "posted",
        "c-101",
        _at(3).isoformat(),
    )
    assert pending == ["k2"]
    assert in_transaction is False


def test_record_comment_failure_abandons_the_row_on_the_third_failure(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k1")
        counts = []
        states = []
        for _ in range(store.COMMENT_ATTEMPTS):
            counts.append(st.record_comment_failure("k1"))
            row = _comment_row(st.connection, "k1")
            assert row is not None
            states.append(row["state"])
        pending = st.pending_comments()
    finally:
        st.close()

    assert store.COMMENT_ATTEMPTS == 3
    assert counts == [1, 2, 3]
    assert states == ["pending", "pending", "abandoned"]
    assert pending == []


def test_an_unknown_comment_key_is_left_alone(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.mark_comment_posted("k-never", "c-1", _at(0))
        failures = st.record_comment_failure("k-never")
        count = st.connection.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]
        in_transaction = st.connection.in_transaction
    finally:
        st.close()

    assert failures == 0
    assert count == 0
    assert in_transaction is False


def test_a_failure_on_a_posted_comment_never_abandons_it(repo):
    # Review Focus 1: a late failure must not undo a comment the board has.
    st = store.Store.open(repo, RUN_ID)
    try:
        _enqueue(st, "k1")
        st.mark_comment_posted("k1", "c-101", _at(1))
        for _ in range(store.COMMENT_ATTEMPTS):
            st.record_comment_failure("k1")
        row = _comment_row(st.connection, "k1")
    finally:
        st.close()

    assert row is not None
    assert (row["state"], row["comment_id"]) == ("posted", "c-101")


def test_a_taken_over_store_neither_marks_nor_fails_a_comment(stores):
    # Review Focus 2: every outbox writer is fenced, not only enqueue.
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    _enqueue(a, "k1")
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)

    writes = [
        lambda: a.mark_comment_posted("k1", "c-101", _at(2)),
        lambda: a.record_comment_failure("k1"),
    ]
    for write in writes:
        with pytest.raises(store.LeaseLostError) as caught:
            write()
        assert caught.value.holder is not None and caught.value.holder.token == "t2"
        assert a.connection.in_transaction is False

    row = _comment_row(b.connection, "k1")
    assert row is not None
    assert (row["state"], row["comment_id"], row["failed_attempts"], row["posted_at"]) == (
        "pending",
        None,
        0,
        None,
    )

    # The new holder's writes go through its own fence.
    assert b.record_comment_failure("k1") == 1
    b.mark_comment_posted("k1", "c-202", _at(3))
    row = _comment_row(a.connection, "k1")
    assert row is not None
    assert (row["state"], row["comment_id"]) == ("posted", "c-202")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "comment_posted or comment_failure or unknown_comment_key or posted_comment or marks_nor_fails" -v`
Expected: FAIL, 5 tests, with `AttributeError: 'Store' object has no attribute 'mark_comment_posted'` (or `... 'record_comment_failure'` for the abandon test).

- [ ] **Step 3: Implement both writers**

In `src/agent_manager/store.py`, inside `Store`, immediately after `pending_comments` (after its `            return [_comment_from_row(row) for row in rows]` line), insert:

```python

    def mark_comment_posted(self, key: str, comment_id: str, now: datetime) -> None:
        """Record that `key`'s body is on the board as `comment_id`.

        The row leaves `pending_comments`. An unknown `key` changes nothing.
        """
        with self._lock, self._fenced():
            try:
                self._conn.execute(
                    "UPDATE board_comments SET state = 'posted', comment_id = ?,"
                    " posted_at = ? WHERE key = ?",
                    (comment_id, _iso(now), key),
                )
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise

    def record_comment_failure(self, key: str) -> int:
        """Count one failed post of `key` and return the new `failed_attempts`.

        A `pending` row reaching `COMMENT_ATTEMPTS` becomes `abandoned` and
        leaves `pending_comments`; a row already `posted` keeps its state. No
        warning is emitted here. An unknown `key` changes nothing and gives 0.
        """
        with self._lock, self._fenced():
            try:
                self._conn.execute(
                    "UPDATE board_comments SET failed_attempts = failed_attempts + 1,"
                    " state = CASE WHEN state = 'pending' AND failed_attempts + 1 >= ?"
                    " THEN 'abandoned' ELSE state END WHERE key = ?",
                    (COMMENT_ATTEMPTS, key),
                )
                row = self._conn.execute(
                    "SELECT failed_attempts FROM board_comments WHERE key = ?", (key,)
                ).fetchone()
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
            return 0 if row is None else row["failed_attempts"]
```

(In an SQLite `UPDATE`, every right-hand `failed_attempts` reads the pre-update value, so the `CASE` sees the same `failed_attempts + 1` that is being stored.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "comment_posted or comment_failure or unknown_comment_key or posted_comment or marks_nor_fails" -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat: mark board comments posted and abandon after three failures"
```

---

### Task 5: Full verification

**Files:** none changed.

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a green suite.

- [ ] **Step 1: Run every new outbox test together**

Run: `uv run pytest tests/test_store.py -k "board_comment or comment" -v`
Expected: every selected test passes; the 14 new ones (3 from Task 1, 4 from Task 2, 2 from Task 3, 5 from Task 4) are among them.

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass, including `tests/e2e` (the opt-in slow test stays deselected per the existing pytest configuration). In particular `test_open_db_creates_every_projection_table` (tests/test_store.py:108) and `test_the_control_tables_appear_on_an_existing_database` (tests/test_store.py:2079) still pass, since both use `<=` subset checks on table names.

- [ ] **Step 3: Confirm scope**

Run: `git diff --stat master...HEAD` (or against the branch's base, `m12/task-add-board-comment-add-3fb36324`)
Expected: only `src/agent_manager/store.py`, `tests/test_store.py` and this plan/spec under `docs/superpowers/` changed. No `board.py`, no `comments.py`.
