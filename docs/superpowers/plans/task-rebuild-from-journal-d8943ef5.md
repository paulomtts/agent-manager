<!-- task-pipeline: validated -->
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

---

# rebuild_from_journal foreign-value rail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Store.rebuild_from_journal` refuses, with a new `ProjectionDivergedError`, to overwrite a projection value no journal line ever recorded (unless `force=True`), and the module docstring, `Store` docstring, `rebuild_from_journal` docstring, design spec §9 and README all name the same journal/row-only boundary, with the README also documenting `am status`'s `integrity` key.

**Architecture:** The rail sits inside `rebuild_from_journal`, inside the existing `with self._lock, self._fenced():` block, between the `run.id != run_id` guard and `self._delete_run(run_id)`. It reuses the journal lines already read for `replay`, loads the projection with `self.load_run(run_id)`, and filters `diverging(lines, projection)` for `kind == "foreign"`. Nothing is written before the raise, so a refused rebuild touches no row. The docs task is text-only.

**Tech Stack:** Python 3, SQLite (`sqlite3`), Pydantic, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-rebuild-from-journal-d8943ef5/docs/superpowers/specs/task-rebuild-from-journal-d8943ef5-design.md` (prepended above). Parent spec: `docs/superpowers/specs/2026-10-03-journal-db-divergence-design.md` §3.6.

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-rebuild-from-journal-d8943ef5` on `m19/task-rebuild-from-journal-d8943ef5`, cut from `m19/task-am-status-gains-an-f63036db`. That base already has `store.diverging`, `store.Mismatch`, `store.MismatchKind` (`src/agent_manager/store.py:588-761`) and `cli.integrity_view` (`src/agent_manager/cli.py:293-338`). Nothing else from any other subtask is assumed. Every path below is relative to that worktree.

## Global Constraints

- Signature exactly: `rebuild_from_journal(self, run_id: str, *, force: bool = False) -> models.Run`.
- `ProjectionDivergedError` subclasses `RuntimeError` and is NOT a subclass of `JournalError`.
- The rail lives inside `rebuild_from_journal`, not in a CLI wrapper. No production caller is added.
- The journal is read once; the check and the rebuild see the same `lines`.
- `stale` mismatches never refuse. A projection with no `runs` row (`load_run` returns `None`) rebuilds as today. `force=True` skips the check entirely.
- Do not modify `diverging`, `Mismatch`, `MismatchKind`, `_walk`, `integrity_view` or `status_for`.
- The six row-only tables, in this order everywhere they are listed: `checkpoints`, `checkpoint_floors`, `run_controls`, `run_leases`, `run_claims`, `board_comments`. The tree's five tables: `runs`, `stories`, `subtasks`, `phases`, `attempts`.
- README sentences, exactly one each: `stale` = the journal is ahead and a resume or a rebuild moves the projection forward; `foreign` = something other than `am` wrote this row.
- Every new test is unmarked (`unit` tier): real sqlite and journal files under `tmp_path`, no subprocess. Do not re-mark any existing test.
- Verification: `uv run pytest`. No typecheck, no lint.

## Review Focus

- A store bound to a lease (`take_lease`) that refuses a rebuild: a person expects no transaction left open, the lease still theirs, and the hand-edited value still in place. Pinned in Task 1 by `test_a_bound_store_refusing_a_rebuild_leaves_no_transaction_open_and_keeps_its_lease`.
- A projection that has both a `stale` and a `foreign` mismatch: a person expects the refusal, with only the `foreign` one named (the stale one is the journal being ahead and is not their problem). Pinned in Task 1 by `test_rebuild_refusal_names_only_the_foreign_mismatches`.
- A corrupt journal under a hand-edited projection: the journal error is the real problem and must come out, not a divergence complaint built on a journal that cannot be read. Pinned in Task 1 by `test_a_corrupt_journal_raises_before_the_foreign_value_check`.
- The store lock after a refusal: every later `record_*` would deadlock if the refusal left it held. Pinned in Task 1 inside `test_rebuild_refuses_a_hand_edited_run_status_and_touches_no_row` (`_held_elsewhere(st._lock) is False`).
- A projection row whose value the models cannot even validate (a hand-written `runs.status = 'bogus'`): before this change a rebuild silently repaired it; now `self.load_run` raises the pydantic `ValidationError` before anything is deleted. That is still a refusal that touches no row, and `force=True` still repairs it. The spec does not cover this input; the plan pins the behavior as it falls out (no conversion to `ProjectionDivergedError`) in `test_rebuild_of_an_unloadable_projection_refuses_and_force_repairs_it`, and the reviewer should confirm that is acceptable.

## Deviation from the spec, flagged

The spec says `tests/test_store.py:1535` (`test_rebuild_and_load_run_hold_the_store_lock_on_the_shared_connection`, at `tests/test_store.py:1493-1548` on this base) must pass unchanged. It cannot: that test monkeypatches the module-level `store.load_run` and asserts the exact sequence of spied calls, and the rail's `self.load_run(run_id)` goes through that same module global, so a new `("load_run", True)` entry now appears between `("read", True)` and `("_delete_run", True)`. The plan updates that one expected list (Task 1 Step 1). The new entry still says `True` (the lock is held), so the change strengthens what the test pins rather than weakening it. The alternatives (a private alias of `load_run` that the spy cannot see, or skipping the spy) would hide the projection read from a test whose job is to see every read on the shared connection.

---

### Task 1: The foreign-value rail in `rebuild_from_journal`

**Files:**
- Modify: `src/agent_manager/store.py:276-285` (add `ProjectionDivergedError` and `_describe_node` after `CorruptJournalError`)
- Modify: `src/agent_manager/store.py:2029-2052` (`rebuild_from_journal` signature and body, up to `self._delete_run(run_id)`)
- Test: `tests/test_store.py:1542-1548` (expected `seen` list of the existing lock test)
- Test: `tests/test_store.py` (append a new section at the end of the file, after line 4942)

**Interfaces:**
- Consumes (already on the base branch): `store.diverging(lines: list[JournalLine], projection: models.Run) -> list[Mismatch]`; `store.Mismatch(node: dict[str, str | int | None], field: Literal["status"] | None, journal: str | None, projection: str | None, kind: Literal["stale", "foreign"])`; `Store.load_run(self, run_id: str) -> models.Run | None`; `Journal.read(self, *, ignore_torn_tail: bool = False) -> list[JournalLine]`; `replay(lines) -> models.Run`. Test helpers already in `tests/test_store.py`: `repo` fixture, `stores` fixture (line 3144), `RUN_ID`, `_run`, `_story`, `_subtask`, `_record_full_run` (line 773), `_save_checkpoint` (line 2255), `_at` (line 2251), `_alive` (line 2985), `_held_elsewhere` (line 1394), `_node` (line 4652), `_raw_sql` (line 4661).
- Produces: `store.ProjectionDivergedError(run_id: str, mismatches: list[Mismatch])`, a `RuntimeError` with attributes `.run_id: str` and `.mismatches: list[Mismatch]` (foreign ones only, in `diverging`'s tree-walk order). `Store.rebuild_from_journal(self, run_id: str, *, force: bool = False) -> models.Run`. Message format per foreign mismatch: `"<node> <field or 'shape'>: journal <journal!r>, projection <projection!r>"`, where `<node>` is `"run"` for the run and otherwise the non-`None` coordinates as `key=value` joined by spaces (for example `"story=8831189b card=deadbeef"`).

- [ ] **Step 1: Update the existing lock test's expected call sequence**

In `tests/test_store.py`, inside `test_rebuild_and_load_run_hold_the_store_lock_on_the_shared_connection`, replace:

```python
    assert rebuilt == loaded
    assert seen == [
        ("read", True),
        ("_delete_run", True),
        ("_write_attempt_row", True),
        ("_write_attempt_row", True),
        ("load_run", True),
    ]
```

with:

```python
    assert rebuilt == loaded
    # The first `load_run` is the foreign-value check (divergence §3.6): it
    # reads the projection under the same lock, before anything is deleted.
    assert seen == [
        ("read", True),
        ("load_run", True),
        ("_delete_run", True),
        ("_write_attempt_row", True),
        ("_write_attempt_row", True),
        ("load_run", True),
    ]
```

- [ ] **Step 2: Append the new tests at the end of `tests/test_store.py`**

Append after the last line of the file (after `test_diverging_reports_mismatches_in_tree_walk_order`):

```python


# -- rebuild_from_journal's foreign-value rail (journal/DB divergence §3.6) ---
#
# Unit tier: real sqlite and journal files under tmp_path, no subprocess.


def _all_rows(repo: Path) -> dict[str, list[tuple]]:
    """Every row of every table, in rowid order, read behind the store's back."""
    conn = sqlite3.connect(paths.project_db_path(repo))
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                " AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in tables
        }
    finally:
        conn.close()


def _projected_run_status(repo: Path) -> str | None:
    conn = store.open_db(repo)
    try:
        return store.run_status(conn, RUN_ID)
    finally:
        conn.close()


def _hand_cancel_an_escalated_run(repo: Path) -> None:
    """The 2026-10-03 incident: the journal says `escalated`, a hand-edit `cancelled`.

    A full tree and a checkpoint ride along so the no-row-touched snapshot
    covers tree rows and row-only rows alike.
    """
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        st.record_run(_run(repo).model_copy(update={"status": "escalated"}))
        _save_checkpoint(st, "ef248597")
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))


def test_rebuild_refuses_a_hand_edited_run_status_and_touches_no_row(repo):
    _hand_cancel_an_escalated_run(repo)
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.ProjectionDivergedError) as caught:
            st.rebuild_from_journal(RUN_ID)
        assert _held_elsewhere(st._lock) is False
        assert st.connection.in_transaction is False
    finally:
        st.close()

    error = caught.value
    assert isinstance(error, RuntimeError)
    assert not isinstance(error, store.JournalError)
    assert error.run_id == RUN_ID
    assert error.mismatches == [
        store.Mismatch(
            node=_node(),
            field="status",
            journal="escalated",
            projection="cancelled",
            kind="foreign",
        )
    ]
    message = str(error)
    assert RUN_ID in message
    assert "run status: journal 'escalated', projection 'cancelled'" in message
    assert "force=True" in message
    assert _projected_run_status(repo) == "cancelled"
    assert _all_rows(repo) == before


def test_rebuild_with_force_overwrites_a_hand_edited_run_status(repo):
    _hand_cancel_an_escalated_run(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        rebuilt = st.rebuild_from_journal(RUN_ID, force=True)
        loaded = st.load_run(RUN_ID)
        kept = st.latest_checkpoint("ef248597")
    finally:
        st.close()

    assert rebuilt.status == "escalated"
    assert loaded == rebuilt
    assert _projected_run_status(repo) == "escalated"
    assert kept is not None  # row-only: the forced rebuild still leaves it alone


def test_rebuild_refuses_a_hand_inserted_subtask_and_touches_no_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
    finally:
        st.close()
    _raw_sql(
        repo,
        "INSERT INTO subtasks (run_id, story_id, card_id, branch, base_branch,"
        " status, worktree_path, position) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (RUN_ID, "8831189b", "deadbeef", "m1/task-deadbeef", "main", "done", 0),
    )
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.ProjectionDivergedError) as caught:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()

    assert caught.value.mismatches == [
        store.Mismatch(
            node=_node(story="8831189b", card="deadbeef"),
            field=None,
            journal=None,
            projection="done",
            kind="foreign",
        )
    ]
    assert (
        "story=8831189b card=deadbeef shape: journal None, projection 'done'"
        in str(caught.value)
    )
    assert _all_rows(repo) == before


def test_rebuild_still_repairs_a_status_set_back_to_an_earlier_journaled_value(repo):
    # `stale` (§3.2): the journal recorded `started` for this story, so the
    # projection is merely behind and the rebuild goes ahead without `force`.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_story(_story().model_copy(update={"status": "done"}))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE stories SET status = 'started' WHERE card_id = '8831189b'")

    st = store.Store.open(repo, RUN_ID)
    try:
        rebuilt = st.rebuild_from_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert rebuilt.stories[0].status == "done"
    assert loaded == rebuilt


def test_rebuild_refusal_names_only_the_foreign_mismatches(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_story(_story().model_copy(update={"status": "done"}))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE stories SET status = 'started' WHERE card_id = '8831189b'")
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.ProjectionDivergedError) as caught:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()

    assert caught.value.mismatches == [
        store.Mismatch(
            node=_node(),
            field="status",
            journal="started",
            projection="cancelled",
            kind="foreign",
        )
    ]
    assert "story=8831189b" not in str(caught.value)
    assert _all_rows(repo) == before


def test_a_bound_store_refusing_a_rebuild_leaves_no_transaction_open_and_keeps_its_lease(
    repo, stores
):
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    st.record_run(_run(repo))
    st.record_run(_run(repo).model_copy(update={"status": "escalated"}))
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))

    with pytest.raises(store.ProjectionDivergedError):
        st.rebuild_from_journal(RUN_ID)

    assert st.connection.in_transaction is False
    assert _held_elsewhere(st._lock) is False
    kept = store.read_lease(st.connection, RUN_ID)
    assert kept is not None and kept.token == "t1"
    assert store.run_status(st.connection, RUN_ID) == "cancelled"


def test_a_corrupt_journal_raises_before_the_foreign_value_check(repo):
    _hand_cancel_an_escalated_run(repo)
    with store.Journal(RUN_ID).path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.CorruptJournalError):
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()

    assert _all_rows(repo) == before


def test_rebuild_of_an_unloadable_projection_refuses_and_force_repairs_it(repo):
    # A value the models cannot validate is certainly not one `am` wrote: the
    # projection read raises before anything is deleted. `force` skips the read.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET status = 'bogus' WHERE id = ?", (RUN_ID,))
    before = _all_rows(repo)

    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(ValidationError):
            st.rebuild_from_journal(RUN_ID)
        assert _all_rows(repo) == before
        rebuilt = st.rebuild_from_journal(RUN_ID, force=True)
    finally:
        st.close()

    assert rebuilt.status == "started"
    assert _projected_run_status(repo) == "started"
```

- [ ] **Step 3: Run the new and updated tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k "hold_the_store_lock_on_the_shared_connection or rebuild_refuses or rebuild_with_force or rebuild_still_repairs or rebuild_refusal_names or refusing_a_rebuild or before_the_foreign_value_check or unloadable_projection"`

Expected:
- `test_rebuild_and_load_run_hold_the_store_lock_on_the_shared_connection` FAILS on the `seen` assertion (no `("load_run", True)` before `("_delete_run", True)`).
- `test_rebuild_refuses_a_hand_edited_run_status_and_touches_no_row`, `test_rebuild_refuses_a_hand_inserted_subtask_and_touches_no_row`, `test_rebuild_refusal_names_only_the_foreign_mismatches`, `test_a_bound_store_refusing_a_rebuild_leaves_no_transaction_open_and_keeps_its_lease` FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'ProjectionDivergedError'`.
- `test_rebuild_with_force_overwrites_a_hand_edited_run_status` FAILS with `TypeError: ... got an unexpected keyword argument 'force'`.
- `test_rebuild_of_an_unloadable_projection_refuses_and_force_repairs_it` FAILS with `Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>` (today the rebuild silently repairs it).
- `test_rebuild_still_repairs_a_status_set_back_to_an_earlier_journaled_value` and `test_a_corrupt_journal_raises_before_the_foreign_value_check` PASS already: they pin behavior the rail must not change.

- [ ] **Step 4: Add `ProjectionDivergedError` and its node formatter**

In `src/agent_manager/store.py`, directly after:

```python
class CorruptJournalError(JournalError):
    """A journal line is not JSON. Names the file and the 1-based line number."""
```

insert:

```python


def _describe_node(node: dict[str, str | int | None]) -> str:
    """`"run"` for the run, else its non-`None` coordinates as `key=value`."""
    parts = [f"{key}={value}" for key, value in node.items() if value is not None]
    return " ".join(parts) if parts else "run"


class ProjectionDivergedError(RuntimeError):
    """The projection holds values no journal line recorded (divergence §3.6).

    Raised by `Store.rebuild_from_journal` before it deletes anything, when
    `diverging` finds a `foreign` mismatch: rebuilding would overwrite what
    something other than the store wrote. Not a `JournalError`: the journal is
    fine. `mismatches` holds only the foreign ones, in tree-walk order.
    """

    def __init__(self, run_id: str, mismatches: "list[Mismatch]") -> None:
        details = "; ".join(
            f"{_describe_node(mismatch.node)} {mismatch.field or 'shape'}:"
            f" journal {mismatch.journal!r}, projection {mismatch.projection!r}"
            for mismatch in mismatches
        )
        super().__init__(
            f"projection of run {run_id!r} holds values its journal never"
            f" recorded: {details}. Nothing was changed;"
            " rebuild_from_journal(..., force=True) overwrites them."
        )
        self.run_id = run_id
        self.mismatches = mismatches
```

(`Mismatch` is defined later in the module, at line ~596, hence the quoted annotation.)

- [ ] **Step 5: Add the rail to `rebuild_from_journal`**

In `src/agent_manager/store.py`, replace:

```python
    def rebuild_from_journal(self, run_id: str) -> models.Run:
```

with:

```python
    def rebuild_from_journal(self, run_id: str, *, force: bool = False) -> models.Run:
```

and replace the body lines:

```python
        with self._lock, self._fenced():
            journal = (
                self._journal if self._journal.run_id == run_id else Journal(run_id)
            )
            run = replay(journal.read())
            if run.id != run_id:
                raise JournalError(
                    f"journal of run {run_id!r} has a run_upsert naming run"
                    f" {run.id!r}: refusing to key its projection under two ids"
                )
            self._delete_run(run_id)
```

with:

```python
        with self._lock, self._fenced():
            journal = (
                self._journal if self._journal.run_id == run_id else Journal(run_id)
            )
            lines = journal.read()
            run = replay(lines)
            if run.id != run_id:
                raise JournalError(
                    f"journal of run {run_id!r} has a run_upsert naming run"
                    f" {run.id!r}: refusing to key its projection under two ids"
                )
            if not force:
                projection = self.load_run(run_id)
                if projection is not None:
                    foreign = [
                        mismatch
                        for mismatch in diverging(lines, projection)
                        if mismatch.kind == "foreign"
                    ]
                    if foreign:
                        raise ProjectionDivergedError(run_id, foreign)
            self._delete_run(run_id)
```

Leave everything from `self._write_run_row(run_id, run)` to `return run` unchanged. (The docstring is corrected in Task 2.)

- [ ] **Step 6: Run the new and updated tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v -k "hold_the_store_lock_on_the_shared_connection or rebuild_refuses or rebuild_with_force or rebuild_still_repairs or rebuild_refusal_names or refusing_a_rebuild or before_the_foreign_value_check or unloadable_projection"`

Expected: all 9 PASS.

- [ ] **Step 7: Run every existing rebuild test and the full default suite**

Run: `uv run pytest tests/test_store.py -v -k rebuild`
Expected: all PASS (including `test_rebuild_picks_up_a_journal_line_whose_row_never_landed`, `test_a_db_truncated_mid_run_is_rebuilt_from_its_journal`, `test_rebuilding_twice_changes_nothing_and_duplicates_nothing`, `test_a_status_transition_on_a_parent_keeps_the_children_recorded_before_it`, `test_a_bound_rebuild_that_fails_midway_leaves_the_projection_whole`, `test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it`).

Run: `uv run pytest`
Expected: PASS, no failures (this covers `tests/test_integration.py:587` in the `git` tier and `tests/test_cli.py:8705`). `tests/e2e/test_parallel_milestone.py:201` is `e2e_fake` and opt-in; it is not re-marked.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat: rebuild_from_journal refuses to overwrite foreign projection values unless force=True"
```

---

### Task 2: Name the journal/row-only boundary in the docs and document `integrity`

Text-only; no test can observe docstrings or Markdown, so this task has no RED step. Its gate is that the suite still passes and the six tables are named identically in all five places.

**Files:**
- Modify: `src/agent_manager/store.py:1-7` (module docstring)
- Modify: `src/agent_manager/store.py:1285-1289` (`Store` class docstring; line numbers shift by about +30 after Task 1's insert, so match on the text)
- Modify: `src/agent_manager/store.py` `rebuild_from_journal` docstring (match on the text)
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:371-374`
- Modify: `README.md:394` (insert a new paragraph after it)

**Interfaces:**
- Consumes: `store.ProjectionDivergedError` and the `force` flag from Task 1 (named in the `rebuild_from_journal` docstring); `cli.integrity_view`'s reason strings `"lease is live"`, `"no journal"`, `f"journal unreadable: {error}"` (`src/agent_manager/cli.py:321-333`) and `Mismatch`'s fields `node`, `field`, `journal`, `projection`, `kind` (`src/agent_manager/store.py:595-610`).
- Produces: nothing code depends on.

- [ ] **Step 1: Correct the `store.py` module docstring**

Replace:

```python
D5 keeps two independent stores: `paths.project_db_path(root)` holds a
queryable projection of the state tree, and `paths.run_dir(run_id)/journal.jsonl`
holds the append-only audit trail. The journal is appended *before* the row is
written, so if the two ever disagree the journal wins and the projection can be
thrown away and rebuilt (§9 lines 365-368).
```

with:

```python
D5 keeps two independent stores: `paths.project_db_path(root)` holds a
queryable projection of the state tree, and `paths.run_dir(run_id)/journal.jsonl`
holds the append-only audit trail. The journal is appended *before* the row is
written, so if the two ever disagree the journal wins and the run's §9 tree
(`runs`, `stories`, `subtasks`, `phases`, `attempts`) can be thrown away and
rebuilt (§9 lines 365-368); the six row-only tables (`checkpoints`,
`checkpoint_floors`, `run_controls`, `run_leases`, `run_claims`,
`board_comments`) have no journal and are the projection's alone.
```

- [ ] **Step 2: Correct the `Store` class docstring**

Replace:

```python
    The exceptions are `checkpoints` (pygents spec §6), `run_controls` and
    `run_leases` (live control C1/C2) and `board_comments` (board-comments
    B6): row-only tables outside the journal.
```

with:

```python
    The exceptions are `checkpoints` (pygents spec §6), `checkpoint_floors`
    (exactly-once 1.1), `run_controls` and `run_leases` (live control C1/C2),
    `run_claims` (multi-process X5) and `board_comments` (board-comments B6):
    the six row-only tables, which have no journal and are the projection's
    alone.
```

- [ ] **Step 3: Correct the `rebuild_from_journal` docstring**

Replace:

```python
        """Replace this run's projection with what its journal says (D5).

        The journal wins: every row for `run_id` is deleted and rewritten from
        the replayed tree, so the result is the same whether the projection was
        stale, truncated or already correct.
```

with:

```python
        """Replace this run's projection with what its journal says (D5).

        The journal wins: every row of the run's §9 tree (`runs`, `stories`,
        `subtasks`, `phases`, `attempts`) for `run_id` is deleted and rewritten
        from the replayed tree, so the result is the same whether the projection
        was stale, truncated or already correct. The six row-only tables
        (`checkpoints`, `checkpoint_floors`, `run_controls`, `run_leases`,
        `run_claims`, `board_comments`) have no journal and are left alone.

        The exception (journal/DB divergence §3.6): a projection holding a value
        no journal line ever recorded for that node, a `foreign` mismatch in
        `diverging`'s terms, is refused with `ProjectionDivergedError` before
        any row is touched, unless `force=True`. The check compares the same
        journal lines the rebuild replays. `stale` mismatches never refuse, and
        a projection with no `runs` row for `run_id` has nothing foreign in it.
```

Leave the following paragraph (`The store lock is held from reading the journal ...`) unchanged.

- [ ] **Step 4: Correct design spec §9 "Write ordering"**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace:

```markdown
**Write ordering.** The journal is appended *before* the SQLite row is updated,
and every journal line carries the run id, the card, the phase, the attempt and
a monotonic sequence number. The DB is a projection and can be rebuilt from the
journal; if the two disagree, the journal wins.
```

with:

```markdown
**Write ordering.** The journal is appended *before* the SQLite row is updated,
and every journal line carries the run id, the card, the phase, the attempt and
a monotonic sequence number. The DB is a projection and the run's tree (`runs`,
`stories`, `subtasks`, `phases`, `attempts`) can be rebuilt from the journal,
while the six row-only tables (`checkpoints`, `checkpoint_floors`,
`run_controls`, `run_leases`, `run_claims`, `board_comments`) have no journal
and are the projection's alone; if the two disagree, the journal wins.
```

- [ ] **Step 5: Add the `integrity` paragraph to the README**

In `README.md`, directly after the paragraph that begins `` `am status <run-id>` always has a `control` key: `` (line 394) and before the paragraph that begins `A request is refused, with`, insert one blank line and then this paragraph (one line, no hard wrap):

```markdown
`am status <run-id>` also always has an `integrity` key: `{"checked", "reason", "mismatches"}`. It compares the run's journal, which `am` appends before every write, with the projection `am status` reads. The journal rebuilds only the run's tree (`runs`, `stories`, `subtasks`, `phases`, `attempts`); the six row-only tables (`checkpoints`, `checkpoint_floors`, `run_controls`, `run_leases`, `run_claims`, `board_comments`) have no journal and are the projection's alone, so they are never compared. When no comparison was made, `checked` is `false` and `reason` says why: `"lease is live"` (a running process's writes in flight are not divergence), `"no journal"`, or `"journal unreadable: <error>"`. Otherwise `checked` is `true` and `reason` is `null`. Each entry of `mismatches` is `{"node", "field", "journal", "projection", "kind"}`: `node` is `{"story", "card", "phase", "attempt"}`, all `null` for the run itself; `field` is `"status"` when both sides have the node with different statuses and `null` when only one side has it; `journal` and `projection` are each side's status, `null` on the side that lacks the node. Only statuses and the tree's shape are compared. The check only reports: it writes nothing and never changes the exit code. A `stale` mismatch means the journal is ahead, and a resume or a rebuild moves the projection forward. A `foreign` mismatch means something other than `am` wrote this row.
```

- [ ] **Step 6: Check consistency and run the full suite**

Run: `rg -n "checkpoint_floors" src/agent_manager/store.py docs/superpowers/specs/2026-09-23-agent-manager-design.md README.md`
Expected: hits in the module docstring, the `Store` class docstring, the `rebuild_from_journal` docstring, the §9 "Write ordering" paragraph and the new README paragraph (plus the existing schema and `TurnFloor` lines), each listing the six tables in the Global Constraints order.

Run: `uv run pytest`
Expected: PASS, no failures.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/store.py docs/superpowers/specs/2026-09-23-agent-manager-design.md README.md
git commit -m "docs: name the journal/row-only boundary and document am status's integrity key"
```
