<!-- task-pipeline: validated -->
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

---

# Enqueue and Flush Outbox Comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `comments.enqueue` (queue a composed `Comment` in the store outbox) and `comments.flush` (post pending outbox rows to the board crash-safely, turning board failures into warnings) to `src/agent_manager/comments.py`.

**Architecture:** `enqueue` is a pass-through to the already-built `Store.enqueue_comment`. `flush` reads `Store.pending_comments` once, then for each row takes `board_api.write_lock(root)` (never inside a store transaction), checks the card's existing comments for a trailing `am-key: <key>` line, posts only when absent, and marks the row posted. `BoardError` and `LockTimeoutError` are caught per row, counted with `Store.record_comment_failure` (whose own 3-strike logic abandons the row) and reported as one warning; everything else propagates.

**Tech Stack:** Python 3.12, SQLite via the existing `agent_manager.store.Store`, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-enqueue-and-flush-b5b7b813-design.md` (prepended above). Parent docs: `docs/superpowers/specs/2026-09-29-board-comments-design.md` (B6–B9) and `docs/superpowers/plans/2026-09-29-board-comments.md` Task 2.1.

**Branch / worktree:** `m12/task-enqueue-and-flush-b5b7b813` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m12/task-enqueue-and-flush-b5b7b813`, cut from `m12/task-compose-outcome-comment-476f1040`. All paths below are relative to that worktree. Every interface this plan consumes (`Store.enqueue_comment`, `Store.pending_comments`, `Store.mark_comment_posted`, `Store.record_comment_failure`, `store.COMMENT_ATTEMPTS`, `store.LeaseLostError`, `board.write_lock`, `board.comment_list`, `board.comment_add`, `board.BoardComment`, `board.BoardError`, `locks.LockTimeoutError`, `comments.Comment`, `comments.key`) was read in this worktree while writing the plan. Nothing from any other subtask (orchestrate/bases/cli wiring, e2e proof) is assumed.

## Global Constraints

- Files touched: `src/agent_manager/comments.py` and `tests/test_comments.py` only. No change to `store.py`, `board.py`, `orchestrate.py`, `bases.py`, `cli.py`, `tests/e2e/`, README or other docs.
- `brd` is only ever invoked from `src/agent_manager/board.py`; this subtask never runs `brd` and never calls the `board` module's functions directly inside `flush` — all board access goes through `board_api`.
- Author is always `"am"`; bodies are posted exactly as stored (last line `am-key: <key>`, already guaranteed by `compose_*`).
- The board lock is taken per row with `with board_api.write_lock(root):`, mirroring `src/agent_manager/steps/rollup.py:118`, and never while a store transaction is open (X8).
- A board failure never escalates, parks or changes a run's status; it only becomes a returned warning.
- Only `board.BoardError` and `locks.LockTimeoutError` are caught; every other exception (including `store.LeaseLostError`, a `BaseException`) propagates.
- Flush has no `now` parameter; it stamps `mark_comment_posted` with `datetime.now(timezone.utc)`.
- Tests are unit tier, all in `tests/test_comments.py`, real temporary `Store` + fake `board_api`, no `brd` process, no network. `tests/conftest.py`'s autouse `isolated_data_home` fixture already points `XDG_DATA_HOME` at a temp dir.
- Verification: `uv run pytest` green (whole suite).

## Review Focus

1. **A card already carrying an `am` comment for a different key** (an earlier lease's `escalated:tok-1` when posting `escalated:tok-2`), or a human comment that mentions the key mid-body: a reasonable person expects the new comment to be posted, not silently treated as a duplicate. Test in Task 2 (`test_flush_ignores_comments_whose_last_line_is_another_key`).
2. **The board returns a previously posted body with trailing whitespace/newlines** (brd or a crash-era copy): expected to be recognised as already posted, so no duplicate. Test in Task 2 (`test_flush_recognises_a_posted_body_with_trailing_whitespace`).
3. **Nothing pending** (the common end-of-run flush on a clean run): expected to return `[]` without taking the board lock or calling the board at all. Test in Task 2 (`test_flush_with_nothing_pending_never_touches_the_board`).
4. **An exception escaping flush mid-row** (the crash case): expected to release the board lock so the next flush in the same process does not deadlock or hit `LockOrderError`. Test in Task 2 (asserted inside `test_a_crash_between_post_and_mark_never_double_posts` via `fake.held == 0`).
5. **The lease is lost while marking a row posted**: expected to propagate `store.LeaseLostError` and NOT be counted as a board failure (no `failed_attempts` increment, no warning), so a taken-over run never burns strikes on another owner's rows. Test in Task 3 (`test_a_lost_lease_while_marking_propagates_and_counts_no_failure`).

---

## File Structure

- `src/agent_manager/comments.py` — existing module with `Comment`, `key`, `agent_reason`, `compose_*`. This plan appends an "outbox" section at the end of the file: `enqueue`, `flush`, and private helpers `_post_one` and `_warning`; updates the module docstring and imports.
- `tests/test_comments.py` — existing pure `compose_*` tests. This plan updates its docstring/imports and appends an outbox section at the end: local `root`/`stores` fixtures, `_at`/`_alive`/`_dead`/`_comment`/`_queue`/`_row` helpers, `FakeBoard`, `CrashOnFirstMark`, and the tests.

---

### Task 1: `comments.enqueue`

**Files:**
- Modify: `src/agent_manager/comments.py:10-17` (imports) and append after line 340 (end of `compose_run_end`)
- Test: `tests/test_comments.py:1-15` (docstring, imports) and append at end of file (after line 593)

**Interfaces:**
- Consumes: `Store.enqueue_comment(*, run_id: str, card_id: str, key: str, body: str, now: datetime) -> bool` (store.py:1466); `comments.Comment(card_id, key, body)`; `comments.key(run_id, card_id, event) -> str`.
- Produces: `comments.enqueue(store: Store, comment: Comment, *, run_id: str, now: datetime) -> None`. Test helpers later tasks use: fixtures `root` (a `Path`) and `stores` (callable `stores(run_id: str = RUN) -> store.Store`), functions `_at(minute: int) -> datetime`, `_alive(row) -> bool`, `_dead(row) -> bool`, `_comment(card_id: str, event: str, *, run_id: str = RUN) -> comments.Comment`, `_row(st: store.Store, key: str) -> sqlite3.Row | None`.

- [ ] **Step 1: Update the test file's docstring and imports**

In `tests/test_comments.py`, replace lines 1-15:

```python
"""Outcome comment bodies (board-comments design B2-B5): pure unit tests."""

import dataclasses

import pytest

from agent_manager import comments
from agent_manager.results import (
    CriticResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    SpecResult,
)
from agent_manager.runtime.walk import SubtaskSummary
```

with:

```python
"""Outcome comments (board-comments design B2-B9): pure `compose_*` unit tests,
then the outbox (`enqueue`/`flush`) against a real temporary store and a fake
`board_api` -- no `brd` process, no network."""

import dataclasses
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import comments, store
from agent_manager.results import (
    CriticResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    SpecResult,
)
from agent_manager.runtime.walk import SubtaskSummary
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/test_comments.py`:

```python


# -- outbox: enqueue and flush (board-comments B6-B9) ---------------------------
#
# Unit tier: a real temporary `Store` (XDG_DATA_HOME is redirected by
# tests/conftest.py) and a fake `board_api`. The real-brd proof is the e2e
# sibling's job.


@pytest.fixture
def root(tmp_path) -> Path:
    """A stand-in for the project worktree the store and board are keyed by."""
    project = tmp_path / "repo"
    project.mkdir()
    return project


@pytest.fixture
def stores(root) -> Iterator[Callable[..., store.Store]]:
    """Open any number of `Store`s on `root`, each on its own connection; close them all.

    A local copy of `tests/test_store.py`'s `stores` fixture (M10 takeover pattern).
    """
    opened: list[store.Store] = []

    def open_store(run_id: str = RUN) -> store.Store:
        st = store.Store.open(root, run_id)
        opened.append(st)
        return st

    yield open_store
    for st in opened:
        st.close()


def _at(minute: int) -> datetime:
    return datetime(2026, 9, 30, 12, minute, tzinfo=timezone.utc)


def _alive(row: store.LeaseRow) -> bool:
    return True


def _dead(row: store.LeaseRow) -> bool:
    return False


def _comment(card_id: str, event: str, *, run_id: str = RUN) -> comments.Comment:
    comment_key = comments.key(run_id, card_id, event)
    body = f"am · {event} · run {run_id}\nbranch: m12/x — é `cmd`\nam-key: {comment_key}"
    return comments.Comment(card_id=card_id, key=comment_key, body=body)


def _row(st: store.Store, key: str):
    return st.connection.execute(
        "SELECT * FROM board_comments WHERE key = ?", (key,)
    ).fetchone()


def test_enqueue_queues_one_pending_row_per_key(stores):
    st = stores()
    first = _comment("card-a", "done")

    assert comments.enqueue(st, first, run_id=RUN, now=_at(0)) is None
    comments.enqueue(
        st, dataclasses.replace(first, body="a different body"), run_id=RUN, now=_at(1)
    )

    rows = st.pending_comments()
    assert [(r.run_id, r.card_id, r.key, r.body, r.state) for r in rows] == [
        (RUN, "card-a", first.key, first.body, "pending")
    ]
    count = st.connection.execute("SELECT COUNT(*) FROM board_comments").fetchone()[0]
    assert count == 1


def test_enqueue_on_a_taken_over_store_raises_and_writes_no_row(stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)
    comment = _comment("card-a", "done")

    with pytest.raises(store.LeaseLostError) as caught:
        comments.enqueue(a, comment, run_id=RUN, now=_at(2))

    assert caught.value.holder is not None and caught.value.holder.token == "t2"
    assert a.connection.in_transaction is False
    assert _row(b, comment.key) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comments.py -k "enqueue" -v`
Expected: both tests FAIL with `AttributeError: module 'agent_manager.comments' has no attribute 'enqueue'`.

- [ ] **Step 4: Add the imports `enqueue` needs**

In `src/agent_manager/comments.py`, replace lines 12-17:

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_manager.runtime.walk import SubtaskSummary
```

with:

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_manager.runtime.walk import SubtaskSummary
    from agent_manager.store import Store
```

- [ ] **Step 5: Write the minimal implementation**

Append to the end of `src/agent_manager/comments.py` (after `compose_run_end`):

```python


# -- outbox (board-comments B6-B9) ---------------------------------------------


def enqueue(store: Store, comment: Comment, *, run_id: str, now: datetime) -> None:
    """Queue `comment` in the store's outbox under its key, once (B6, B9).

    A pass-through to `Store.enqueue_comment`: a key already queued, in any
    state, is left exactly as it was. Nothing is posted here; the caller
    flushes. Nothing is caught: a lost lease raises `store.LeaseLostError`
    and writes no row.
    """
    store.enqueue_comment(
        run_id=run_id,
        card_id=comment.card_id,
        key=comment.key,
        body=comment.body,
        now=now,
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comments.py -v`
Expected: every test in the file PASSES (the two new enqueue tests plus all existing `compose_*` tests).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/comments.py tests/test_comments.py
git commit -m "feat(comments): enqueue outcome comments in the outbox"
```

---

### Task 2: `comments.flush` — posting, ordering, lock, key check

**Files:**
- Modify: `src/agent_manager/comments.py:1-8` (module docstring), the import block from Task 1, and append after `enqueue`
- Test: `tests/test_comments.py` (imports; append after Task 1's tests)

**Interfaces:**
- Consumes: Task 1's `enqueue`, fixtures `root`/`stores`, helpers `_at`, `_comment`, `_row`. `Store.pending_comments(run_id: str | None = None, card_ids: Iterable[str] | None = None) -> list[CommentRow]` (oldest `created_at` first, then rowid); `Store.mark_comment_posted(key: str, comment_id: str, now: datetime) -> None`; `CommentRow(run_id, card_id, key, body, state, comment_id, failed_attempts)`; `board.write_lock(repo_dir) -> locks.ProcessLock` (context manager); `board.comment_list(card_id, *, repo_dir=None) -> list[BoardComment]`; `board.comment_add(card_id, body, *, author="am", repo_dir=None) -> str`; `board.BoardComment(id: str, body: str, author: str)`.
- Produces: `comments.flush(store: Store, root: Path, *, run_id: str | None = None, card_ids: Iterable[str] | None = None, board_api=board) -> list[str]`; private `comments._post_one(store: Store, row: CommentRow, root: Path, board_api) -> None`. Test helpers Task 3 uses: `FakeBoard` (attributes `cards: dict[str, list[board.BoardComment]]`, `calls: list[tuple[str, str, int, Path | None]]`, `added: list[tuple[str, str, str]]`, `held: int`, `locks_taken: int`, `down: bool`, `fail_cards: set[str]`, `lock_timeout: bool`), `CrashOnFirstMark(inner: store.Store, error: BaseException)`, `_queue(st, card_id, event, *, minute=0, run_id=RUN) -> comments.Comment`, `_Crash(Exception)`.

- [ ] **Step 1: Add the test imports**

In `tests/test_comments.py`, replace:

```python
import dataclasses
from collections.abc import Callable, Iterator
```

with:

```python
import contextlib
import dataclasses
from collections.abc import Callable, Iterator
```

and replace:

```python
from agent_manager import comments, store
```

with:

```python
from agent_manager import board, comments, store
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/test_comments.py`:

```python


class FakeBoard:
    """A stand-in for the `board` module: per-card comments, a call log, a tracked lock.

    `down` makes every board call raise; `fail_cards` makes calls for those
    cards raise; `lock_timeout` makes `write_lock` raise on entry, as a real
    `ProcessLock` does. Every failure is the real `board.BoardError` /
    `locks.LockTimeoutError`.
    """

    def __init__(self) -> None:
        self.cards: dict[str, list[board.BoardComment]] = {}
        self.calls: list[tuple[str, str, int, Path | None]] = []
        self.added: list[tuple[str, str, str]] = []
        self.held = 0
        self.locks_taken = 0
        self.down = False
        self.fail_cards: set[str] = set()
        self.lock_timeout = False
        self._next_id = 0

    @contextlib.contextmanager
    def write_lock(self, repo_dir):
        if self.lock_timeout:
            from agent_manager import locks

            raise locks.LockTimeoutError(Path(repo_dir) / "board.lock", 0.0)
        self.locks_taken += 1
        self.held += 1
        try:
            yield
        finally:
            self.held -= 1

    def _call(self, op: str, card_id: str, repo_dir) -> None:
        self.calls.append((op, card_id, self.held, repo_dir))
        if self.down or card_id in self.fail_cards:
            raise board.BoardError(
                f"brd comment {op} failed", argv=["brd", "comment", op, card_id], exit_code=1
            )

    def comment_list(self, card_id, *, repo_dir=None):
        self._call("list", card_id, repo_dir)
        return list(self.cards.get(card_id, []))

    def comment_add(self, card_id, body, *, author="am", repo_dir=None):
        self._call("add", card_id, repo_dir)
        self._next_id += 1
        comment = board.BoardComment(id=f"c{self._next_id}", body=body, author=author)
        self.cards.setdefault(card_id, []).append(comment)
        self.added.append((card_id, body, author))
        return comment.id


class _Crash(Exception):
    """The process dying between `comment_add` and marking the row posted."""


class CrashOnFirstMark:
    """A `Store` whose first `mark_comment_posted` raises `error`; everything else delegates."""

    def __init__(self, inner: store.Store, error: BaseException) -> None:
        self._inner = inner
        self._error = error
        self.marks = 0

    def mark_comment_posted(self, key, comment_id, now):
        self.marks += 1
        if self.marks == 1:
            raise self._error
        self._inner.mark_comment_posted(key, comment_id, now)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _queue(st, card_id: str, event: str, *, minute: int = 0, run_id: str = RUN):
    comment = _comment(card_id, event, run_id=run_id)
    comments.enqueue(st, comment, run_id=run_id, now=_at(minute))
    return comment


def test_flush_posts_pending_rows_oldest_first_as_am(stores, root):
    st = stores()
    newest = _queue(st, "card-a", "done", minute=2)
    oldest = _queue(st, "card-b", "done", minute=0)
    middle = _queue(st, "card-c", "done", minute=1)
    fake = FakeBoard()

    warnings = comments.flush(st, root, board_api=fake)

    assert warnings == []
    assert fake.added == [
        ("card-b", oldest.body, "am"),
        ("card-c", middle.body, "am"),
        ("card-a", newest.body, "am"),
    ]
    assert st.pending_comments() == []
    posted = {c.key: _row(st, c.key) for c in (oldest, middle, newest)}
    assert [(posted[c.key]["state"], posted[c.key]["comment_id"]) for c in (oldest, middle, newest)] == [
        ("posted", "c1"),
        ("posted", "c2"),
        ("posted", "c3"),
    ]
    assert all(posted[c.key]["posted_at"] is not None for c in (oldest, middle, newest))


def test_flush_calls_the_board_only_under_its_write_lock_once_per_row(stores, root):
    st = stores()
    _queue(st, "card-a", "done", minute=0)
    _queue(st, "card-b", "done", minute=1)
    fake = FakeBoard()

    comments.flush(st, root, board_api=fake)

    assert fake.calls == [
        ("list", "card-a", 1, root),
        ("add", "card-a", 1, root),
        ("list", "card-b", 1, root),
        ("add", "card-b", 1, root),
    ]
    assert fake.locks_taken == 2
    assert fake.held == 0


def test_flush_filters_by_run_and_cards(stores, root):
    st = stores()
    a = _queue(st, "card-a", "done", minute=0, run_id="r1")
    b = _queue(st, "card-b", "done", minute=1, run_id="r2")
    c = _queue(st, "card-c", "done", minute=2, run_id="r1")
    fake = FakeBoard()

    assert comments.flush(st, root, run_id="r2", board_api=fake) == []
    assert [added[0] for added in fake.added] == ["card-b"]

    assert comments.flush(st, root, card_ids=["card-c"], board_api=fake) == []
    assert [added[0] for added in fake.added] == ["card-b", "card-c"]

    assert comments.flush(st, root, card_ids=[], board_api=fake) == []
    assert [added[0] for added in fake.added] == ["card-b", "card-c"]

    assert [r.key for r in st.pending_comments()] == [a.key]
    assert _row(st, b.key)["state"] == "posted"
    assert _row(st, c.key)["state"] == "posted"


def test_flush_with_nothing_pending_never_touches_the_board(stores, root):
    st = stores()
    fake = FakeBoard()

    assert comments.flush(st, root, board_api=fake) == []
    assert fake.calls == []
    assert fake.locks_taken == 0


def test_a_crash_between_post_and_mark_never_double_posts(stores, root):
    st = stores()
    comment = _queue(st, "card-a", "done")
    fake = FakeBoard()
    crashing = CrashOnFirstMark(st, _Crash("killed before marking"))

    with pytest.raises(_Crash):
        comments.flush(crashing, root, board_api=fake)

    # The comment reached the board, the row did not move, and the lock is free.
    assert [c.body for c in fake.cards["card-a"]] == [comment.body]
    assert [r.key for r in st.pending_comments()] == [comment.key]
    assert _row(st, comment.key)["failed_attempts"] == 0
    assert fake.held == 0

    assert comments.flush(crashing, root, board_api=fake) == []

    assert fake.added == [("card-a", comment.body, "am")]
    assert len(fake.cards["card-a"]) == 1
    row = _row(st, comment.key)
    assert (row["state"], row["comment_id"]) == ("posted", "c1")


def test_flush_recognises_a_posted_body_with_trailing_whitespace(stores, root):
    st = stores()
    comment = _queue(st, "card-a", "done")
    fake = FakeBoard()
    fake.cards["card-a"] = [
        board.BoardComment(id="c-prior", body=comment.body + "\n\n  ", author="am")
    ]

    assert comments.flush(st, root, board_api=fake) == []

    assert fake.added == []
    row = _row(st, comment.key)
    assert (row["state"], row["comment_id"]) == ("posted", "c-prior")


def test_flush_ignores_comments_whose_last_line_is_another_key(stores, root):
    st = stores()
    earlier = comments.key(RUN, "card-a", "escalated:tok-1")
    comment = _queue(st, "card-a", "escalated:tok-2")
    fake = FakeBoard()
    fake.cards["card-a"] = [
        board.BoardComment(
            id="c-old", body=f"am · escalated · run {RUN}\nam-key: {earlier}", author="am"
        ),
        board.BoardComment(
            id="c-human", body=f"see am-key: {comment.key}\nthanks", author="paulo"
        ),
    ]

    assert comments.flush(st, root, board_api=fake) == []

    assert fake.added == [("card-a", comment.body, "am")]
    assert _row(st, comment.key)["comment_id"] == "c1"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comments.py -k "flush or crash" -v`
Expected: all seven new tests FAIL with `AttributeError: module 'agent_manager.comments' has no attribute 'flush'`.

- [ ] **Step 4: Update the module docstring**

In `src/agent_manager/comments.py`, replace lines 1-8:

```python
"""Outcome comment bodies for brd cards (board-comments design B2-B5).

Pure: no I/O, no `brd`, no store, no clock. Each `compose_*` turns one
outcome's data into a `Comment` that a caller later enqueues and flushes
(Task 2.1); this module never posts anything. Agent text enters only
through `agent_reason`'s three failure fields, quoted, `[[`-escaped and
cut first when a body would exceed `CAP`.
"""
```

with:

```python
"""Outcome comments for brd cards (board-comments design B2-B9).

`key`, `agent_reason` and every `compose_*` are pure: no I/O, no `brd`, no
store, no clock. Each `compose_*` turns one outcome's data into a `Comment`.
Agent text enters only through `agent_reason`'s three failure fields, quoted,
`[[`-escaped and cut first when a body would exceed `CAP`.

`enqueue` and `flush` are the outbox side (B6-B9). `enqueue` queues a
`Comment` in the store's `board_comments` table. `flush` posts the pending
rows through an injected `board_api` (the `board` module by default), one
row at a time under the board write lock and never inside a store
transaction, checking the card for the row's `am-key:` line first so a crash
between posting and marking never double-posts. A board failure becomes a
returned warning, never an exception.
"""
```

- [ ] **Step 5: Extend the imports**

In `src/agent_manager/comments.py`, replace the import block Task 1 left:

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_manager.runtime.walk import SubtaskSummary
    from agent_manager.store import Store
```

with:

```python
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_manager import board

if TYPE_CHECKING:
    from agent_manager.runtime.walk import SubtaskSummary
    from agent_manager.store import CommentRow, Store
```

- [ ] **Step 6: Write the minimal implementation**

Append to the end of `src/agent_manager/comments.py` (after `enqueue`):

```python


def _post_one(store: Store, row: CommentRow, root: Path, board_api: Any) -> None:
    """Put `row` on its card unless its `am-key:` line is already there, then mark it.

    Called with the board write lock held. A comment whose body (trailing
    whitespace ignored) ends with `am-key: <key>` is this row, posted by an
    earlier flush that died before marking (B7); its id is recorded instead
    of posting again.
    """
    marker = f"am-key: {row.key}"
    existing = board_api.comment_list(row.card_id, repo_dir=root)
    match = next((c for c in existing if c.body.rstrip().endswith(marker)), None)
    if match is None:
        comment_id = board_api.comment_add(row.card_id, row.body, author="am", repo_dir=root)
    else:
        comment_id = match.id
    store.mark_comment_posted(row.key, comment_id, datetime.now(timezone.utc))


def flush(
    store: Store,
    root: Path,
    *,
    run_id: str | None = None,
    card_ids: Iterable[str] | None = None,
    board_api: Any = board,
) -> list[str]:
    """Post every pending outbox row, oldest first, and return the warnings (B7).

    `run_id`/`card_ids` narrow the rows exactly as `Store.pending_comments`
    does. Each row takes `board_api.write_lock(root)` on its own -- never
    while a store transaction is open (X8) -- and all board access goes
    through `board_api`, so tests inject a fake.
    """
    warnings: list[str] = []
    for row in store.pending_comments(run_id, card_ids):
        with board_api.write_lock(root):
            _post_one(store, row, root, board_api)
    return warnings
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comments.py -v`
Expected: every test in the file PASSES.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/comments.py tests/test_comments.py
git commit -m "feat(comments): flush the outbox under the board lock, checking am-key first"
```

---

### Task 3: `comments.flush` — board failures become warnings

**Files:**
- Modify: `src/agent_manager/comments.py` (imports; `flush` body; add `_warning` above `flush`)
- Test: `tests/test_comments.py` (imports; append after Task 2's tests)

**Interfaces:**
- Consumes: Task 2's `flush`, `_post_one`, `FakeBoard`, `CrashOnFirstMark`, `_queue`, `_row`; Task 1's `stores`, `root`, `_at`, `_alive`, `_dead`. `Store.record_comment_failure(key: str) -> int` (new `failed_attempts`; a pending row reaching `store.COMMENT_ATTEMPTS == 3` becomes `abandoned`); `board.BoardError`; `locks.LockTimeoutError(path: Path, timeout: float)`; `store.LeaseLostError(run_id: str, holder: LeaseRow | None)`.
- Produces: final `flush` behaviour: catches only `board.BoardError` and `locks.LockTimeoutError` per row, calls `record_comment_failure` once, appends one warning from `_warning(row: CommentRow, attempts: int, error: Exception) -> str`, continues. Warning text: `"board comment <key> on card <card_id> not posted (attempt <n> of 3), will retry: <error>"`, or `"board comment <key> on card <card_id> abandoned after <n> failed attempts: <error>"` once `n >= COMMENT_ATTEMPTS`.

- [ ] **Step 1: Add the test import**

In `tests/test_comments.py`, replace:

```python
from agent_manager import board, comments, store
```

with:

```python
from agent_manager import board, comments, locks, store
```

and in `FakeBoard.write_lock`, replace:

```python
        if self.lock_timeout:
            from agent_manager import locks

            raise locks.LockTimeoutError(Path(repo_dir) / "board.lock", 0.0)
```

with:

```python
        if self.lock_timeout:
            raise locks.LockTimeoutError(Path(repo_dir) / "board.lock", 0.0)
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/test_comments.py`:

```python


def test_board_down_leaves_rows_pending_with_warnings_then_posts_them_later(stores, root):
    st = stores()
    first = _queue(st, "card-a", "done", minute=0)
    second = _queue(st, "card-b", "done", minute=1)
    fake = FakeBoard()
    fake.down = True

    warnings = comments.flush(st, root, board_api=fake)

    assert len(warnings) == 2
    assert first.key in warnings[0] and "card-a" in warnings[0] and "will retry" in warnings[0]
    assert second.key in warnings[1] and "card-b" in warnings[1] and "will retry" in warnings[1]
    assert [r.key for r in st.pending_comments()] == [first.key, second.key]
    assert [r.failed_attempts for r in st.pending_comments()] == [1, 1]
    assert fake.added == []
    assert fake.held == 0

    fake.down = False
    assert comments.flush(st, root, board_api=fake) == []

    assert st.pending_comments() == []
    assert [added[0] for added in fake.added] == ["card-a", "card-b"]


def test_a_board_lock_timeout_is_a_counted_board_failure(stores, root):
    st = stores()
    comment = _queue(st, "card-a", "done")
    fake = FakeBoard()
    fake.lock_timeout = True

    warnings = comments.flush(st, root, board_api=fake)

    assert len(warnings) == 1
    assert comment.key in warnings[0] and "card-a" in warnings[0]
    assert fake.calls == []
    row = _row(st, comment.key)
    assert (row["state"], row["failed_attempts"]) == ("pending", 1)


def test_three_failures_abandon_a_row_with_one_warning(stores, root):
    st = stores()
    comment = _queue(st, "card-a", "done")
    fake = FakeBoard()
    fake.down = True

    first = comments.flush(st, root, board_api=fake)
    second = comments.flush(st, root, board_api=fake)
    third = comments.flush(st, root, board_api=fake)

    assert len(first) == 1 and "will retry" in first[0] and "abandoned" not in first[0]
    assert len(second) == 1 and "will retry" in second[0] and "abandoned" not in second[0]
    assert len(third) == 1
    assert comment.key in third[0] and "card-a" in third[0] and "abandoned" in third[0]
    assert "will retry" not in third[0]
    row = _row(st, comment.key)
    assert (row["state"], row["failed_attempts"]) == ("abandoned", store.COMMENT_ATTEMPTS)
    assert st.pending_comments() == []

    fake.down = False
    calls_before = list(fake.calls)
    assert comments.flush(st, root, board_api=fake) == []
    assert fake.calls == calls_before
    assert fake.added == []


def test_one_failing_card_does_not_block_the_others(stores, root):
    st = stores()
    failing = _queue(st, "card-a", "done", minute=0)
    fine = _queue(st, "card-b", "done", minute=1)
    fake = FakeBoard()
    fake.fail_cards = {"card-a"}

    warnings = comments.flush(st, root, board_api=fake)

    assert len(warnings) == 1
    assert failing.key in warnings[0] and "card-a" in warnings[0]
    assert fake.added == [("card-b", fine.body, "am")]
    assert [r.key for r in st.pending_comments()] == [failing.key]
    assert _row(st, fine.key)["state"] == "posted"


def test_a_lost_lease_while_marking_propagates_and_counts_no_failure(stores, root):
    # Guard for Step 4's except clause: passes before it and must keep passing.
    st = stores()
    comment = _queue(st, "card-a", "done")
    fake = FakeBoard()
    losing = CrashOnFirstMark(st, store.LeaseLostError(RUN, None))

    with pytest.raises(store.LeaseLostError):
        comments.flush(losing, root, board_api=fake)

    row = _row(st, comment.key)
    assert (row["state"], row["failed_attempts"]) == ("pending", 0)
    assert fake.held == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comments.py -k "board_down or lock_timeout or three_failures or one_failing or lost_lease_while_marking" -v`
Expected: the first four FAIL — `board_down`, `three_failures` and `one_failing` with `agent_manager.board.BoardError: brd comment list failed ...` raised out of `flush`, `lock_timeout` with `agent_manager.locks.LockTimeoutError: timed out after 0.0s ...`. `test_a_lost_lease_while_marking_propagates_and_counts_no_failure` PASSES already (it is a guard that Step 4's `except` stays narrow).

- [ ] **Step 4: Write the implementation**

In `src/agent_manager/comments.py`, replace:

```python
from agent_manager import board
```

with:

```python
from agent_manager import board, locks
from agent_manager.store import COMMENT_ATTEMPTS
```

Then insert `_warning` directly above `def flush(`:

```python
def _warning(row: CommentRow, attempts: int, error: Exception) -> str:
    """The one report warning for a row whose post failed (B7, B8).

    `attempts` is `Store.record_comment_failure`'s new count; at
    `COMMENT_ATTEMPTS` the store has already marked the row `abandoned`.
    """
    where = f"board comment {row.key} on card {row.card_id}"
    if attempts >= COMMENT_ATTEMPTS:
        return f"{where} abandoned after {attempts} failed attempts: {error}"
    return f"{where} not posted (attempt {attempts} of {COMMENT_ATTEMPTS}), will retry: {error}"


```

Then replace the loop in `flush`:

```python
    warnings: list[str] = []
    for row in store.pending_comments(run_id, card_ids):
        with board_api.write_lock(root):
            _post_one(store, row, root, board_api)
    return warnings
```

with:

```python
    warnings: list[str] = []
    for row in store.pending_comments(run_id, card_ids):
        try:
            with board_api.write_lock(root):
                _post_one(store, row, root, board_api)
        except (board.BoardError, locks.LockTimeoutError) as error:
            # Counted outside the board lock; the store's own 3-strike rule
            # abandons the row. Anything else (a lost lease, a crash) propagates.
            attempts = store.record_comment_failure(row.key)
            warnings.append(_warning(row, attempts, error))
    return warnings
```

and extend the `flush` docstring by replacing:

```python
    through `board_api`, so tests inject a fake.
    """
```

with:

```python
    through `board_api`, so tests inject a fake.

    A `board.BoardError` or a `locks.LockTimeoutError` on a row is counted
    with `Store.record_comment_failure`, reported as exactly one warning,
    and the next row is tried: a board failure never escalates, parks or
    changes a run (B8). Every other exception propagates.
    """
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comments.py -v`
Expected: every test in the file PASSES.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no failures (the e2e marker's tests are excluded by default; the data-dir guard does not fire).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/comments.py tests/test_comments.py
git commit -m "feat(comments): a board failure in flush becomes a warning, abandoned after three"
```

---

## Spec coverage map

| Spec item | Task / test |
|---|---|
| `enqueue` pass-through, returns `None`, idempotent by key (Test 1) | Task 1 `test_enqueue_queues_one_pending_row_per_key` |
| `enqueue` lost lease raises, no row (Test 10, RF5 of parent plan) | Task 1 `test_enqueue_on_a_taken_over_store_raises_and_writes_no_row` |
| Oldest-first order, author `am`, bodies unchanged, ids recorded, no warnings (Test 2) | Task 2 `test_flush_posts_pending_rows_oldest_first_as_am` |
| Board calls only under `write_lock`, per row, `repo_dir=root` (Test 3) | Task 2 `test_flush_calls_the_board_only_under_its_write_lock_once_per_row` |
| Crash between post and mark (Test 4) | Task 2 `test_a_crash_between_post_and_mark_never_double_posts` |
| Filters pass through (Test 9) | Task 2 `test_flush_filters_by_run_and_cards` |
| Board down then up (Test 5) | Task 3 `test_board_down_leaves_rows_pending_with_warnings_then_posts_them_later` |
| Lock timeout = board failure (Test 6) | Task 3 `test_a_board_lock_timeout_is_a_counted_board_failure` |
| Three strikes, one "abandoned" warning, fourth flush untouched (Test 7) | Task 3 `test_three_failures_abandon_a_row_with_one_warning` |
| One failing row does not block others (Test 8) | Task 3 `test_one_failing_card_does_not_block_the_others` |
| Other exceptions propagate | Task 2 crash test; Task 3 lost-lease-while-marking guard |
| Module docstring update | Task 2 Step 4 |
