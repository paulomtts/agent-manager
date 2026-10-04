<!-- task-pipeline: validated -->
# am reset reports which cards a relaunch would still continue (af52db54)

Subtask of story 816cb8b3 ("am reset closes a run nobody is driving"), milestone 19. Narrows `docs/superpowers/specs/2026-10-03-am-reset-design.md` §3.5 and §4 test 10, plus the `cards` half of test 1. Builds on the landed sibling 736d6728 (`am reset RUN_ID`). Its `reset_run` emits a `"cards": []` placeholder marked `# af52db54 fills this in from the run's checkpoints.` (`src/agent_manager/cli.py:2408-2409`).

## Scope

In scope:

1. Add `Store.checkpoint_cards(run_id: str) -> list[tuple[str, str]]` in `src/agent_manager/store.py`. It is a read-only query that returns every distinct `(card_id, workflow)` pair that has at least one `checkpoints` row whose `run_id` is the given run.
   - Any `reason` counts, including `done`.
   - The order is deterministic: `card_id`, then `workflow`.
   - It writes nothing and runs under `self._lock`, like the other readers.
   - It takes `run_id` explicitly and does not use `self.run_id`.
2. In `reset_run` (`src/agent_manager/cli.py`), replace the placeholder with the real `cards` computation:
   - Compute it while the store is still open (before `store.close()`), after the `run_lease` block. That is after the single `record_run` write, or after the no-op in the already-cancelled case.
   - For each `(card_id, workflow)` from `store.checkpoint_cards(run.id)`, call `cp = store.latest_open_checkpoint(card_id, workflow)`.
   - Emit `{"card_id": card_id, "workflow": workflow, "open_in": cp.run_id if cp is not None and cp.run_id != run.id else None}`.
   - Keep `cards` in the order `checkpoint_cards` returns.

Out of scope (leave untouched):

- Sibling 736d6728 owns the command, lease, fencing, takeover, refusals, the already-cancelled no-op, `message` and `took_over`.
- Sibling 522adfb5 owns the relaunch and resume wiring tests, the `worktree.ensure` git companion, the `e2e_fake` incident replay, and the README.
- Resolving the chain case is out of scope (no `--cascade`). It is only reported.

## Observable behavior

- The envelope's `cards` is a list of `{"card_id", "workflow", "open_in"}`. All other envelope keys are unchanged.
- `open_in` is `null` in the ordinary case. The card's newest row belonged to the reset run, which is now `cancelled`, so the newest-row rule in `latest_open_checkpoint` (`store.py:1555-1588`) closes it.
- `open_in` names another run only when `latest_open_checkpoint` still returns a row from a different run. This is the chain case: the reset run adopted that run's checkpoint and crashed before writing a newer row, or the card's newest row is another run's under a different workflow.
- `open_in` names the run the next relaunch would continue from. It does not claim that run's worktree exists, and no filesystem check is made.
- `open_in` comes only from `latest_open_checkpoint`. The adoption logic is not re-derived.
- A run with no checkpoint rows reports `cards: []`.
- An already-cancelled run still reports `cards`, computed the same way.
- No checkpoint row is written or deleted. The reset's only write is still the existing `record_run`.

## Error paths

There are no new error paths:

- Every refusal (`UnknownRunError`, `RunIsLiveError`, `NotResettableError`) raises before `cards` is computed, so it is unaffected.
- A store read error while computing `cards` propagates like any other store error. The status write has already committed by then.

## Tests

All tests are driven through the `projection` fixture in `tests/test_cli.py` or the store directly. Checkpoints are seeded through the store, and `brd`/`git`/`claude` are stubbed off `PATH`. Nothing spawns a subprocess, so under the CLAUDE.md placement rule ("chosen by what it actually spawns or touches") every test is `unit` tier and unmarked.

1. **`checkpoint_cards` returns only this run's distinct pairs** (`unit`). Seed:
   - several rows for one card and workflow (`turn`, `parked`, `done`);
   - a second workflow for the same card;
   - rows under another run.

   Assert the result is exactly this run's distinct pairs, in sorted order, and that an unknown run returns `[]`. Place it with the existing store checkpoint-query tests.
2. **Existing reset test 1 updated** (`unit`). `test_reset_records_a_stopped_run_cancelled_through_one_journal_line` currently asserts `"cards": []`. Change it to assert that the run's card appears with its `workflow` and `open_in: null`, in both the worktree-present and worktree-removed passes. The spec §4 test 7 case (no checkpoints) keeps asserting `cards: []`.
3. **Chain case: the newest row decides** (`unit`, spec test 10).
   - Seed: run Y holds an open row for card c, and run X holds a newer open row for c in the same workflow.
   - Reset X and assert `cards` lists c with `open_in: null`.
   - Assert `runs.continuable_checkpoint` (`runs.py:216`) returns `None` for c even though Y's row is still open.
   - Uses two stores on one database, the idiom from `tests/test_control.py`.
4. **Chain variant: a newer row under `bases`** (`unit`, spec test 10).
   - Seed, oldest first:
     1. Y holds an open `task` row for c (the row X adopted).
     2. X holds a `turn` row for c under `task`.
     3. Y holds the newest open row for c under `bases`.
   - Reset X. The newest row is now Y's, which is open, so `cards` reports `{"card_id": c, "workflow": "task", "open_in": Y}`.
   - Then reset Y. Resetting X again reports `open_in: null`, because the newest row now belongs to a cancelled run. Y's own reset also reports `null` for both of its pairs.
5. **An already-cancelled reset still reports `cards`** (`unit`). A second reset of the same run returns `already_cancelled: true` with the same `cards` as the first, and the journal length is unchanged.

## Verification

Run `uv run pytest` (the default `unit` + `git` tiers). There is no separate lint or typecheck command.

---

# am reset `cards` (af52db54) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am reset RUN_ID` reports, per `(card_id, workflow)` the run checkpointed, which other run (if any) the next relaunch would still continue that card from.

**Architecture:** A new read-only `Store.checkpoint_cards(run_id)` lists the run's distinct `(card_id, workflow)` pairs. `cli.reset_run` calls it after the `run_lease` block and before `store.close()`, asks the existing `Store.latest_open_checkpoint` for each pair, and reports `open_in` as that row's `run_id` only when it differs from the reset run. No checkpoint row is written or deleted.

**Tech Stack:** Python, SQLite (`sqlite3` with `sqlite3.Row`), Typer CLI, pytest.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-am-reset-reports-which-af52db54/docs/superpowers/specs/task-am-reset-reports-which-af52db54-design.md` (prepended above), narrowing `docs/superpowers/specs/2026-10-03-am-reset-design.md` §3.5 and §4 test 10.

**Branch / worktree:** `m19/task-am-reset-reports-which-af52db54` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-am-reset-reports-which-af52db54`, cut from `m19/task-add-am-reset-run-id-736d6728`. Every path below is relative to that worktree. Nothing from 522adfb5 exists on this branch, and nothing here depends on it.

## Global Constraints

- Envelope `cards` entries are exactly `{"card_id", "workflow", "open_in"}`; every other envelope key (`RESET_KEYS`) is unchanged.
- `open_in` = `cp.run_id if cp is not None and cp.run_id != run.id else None`, where `cp = store.latest_open_checkpoint(card_id, workflow)`. Never re-derive adoption logic.
- `checkpoint_cards` is read-only, holds `self._lock`, takes `run_id` explicitly (never `self.run_id`), includes every `reason` (`done` too), ordered by `card_id`, then `workflow`.
- `cards` is computed inside the `try:` that owns `store`, after the `with run_lease(store) as lease:` block, before `store.close()`.
- No checkpoint row is written or deleted; `reset_run`'s only write stays the existing `record_run`.
- Every test in this plan is `unit` tier: unmarked, no subprocess, no `@pytest.mark.git`.
- Out of scope: README, the `e2e_fake` scenario, relaunch/resume wiring tests (522adfb5); refusals, lease, `message`, `took_over` (736d6728).
- Verification: `uv run pytest`. No lint or typecheck command exists.

## Review Focus

1. A run that checkpointed several cards, one of them only with a `done` row: every pair is listed in `card_id`/`workflow` order and the `done` card reports `open_in: null` (it is still listed, since any reason counts). Pinned in Task 2 by `test_reset_lists_every_pair_it_checkpointed_in_order_a_done_card_included`.
2. Another run's pair the reset run never checkpointed (Y's `bases` row in the variant) must not appear in X's `cards`. Pinned in Task 2 by the exact-list assertion in `test_reset_names_the_run_a_newer_bases_row_keeps_the_card_open_in`.
3. `checkpoint_cards` called on a store bound to a different run must still answer for the run it is given, not `self.run_id`. Pinned in Task 1 by calling it from the `OTHER_RUN_ID` store.
4. Computing `cards` must not write: checkpoint rows are byte-identical before and after a reset that reports `open_in` for another run. Pinned in Task 2 by the `_checkpoint_rows` assertions in the chain test.
5. A refusal (unknown/live/done run) must still return its error envelope with no `cards` key and no store opened. Already pinned by the existing 736d6728 tests (`test_reset_refuses_*`, which forbid `cli.Store`), which Task 2 leaves untouched and re-runs.

---

### Task 1: `Store.checkpoint_cards`

**Files:**
- Modify: `src/agent_manager/store.py` (insert a new method right after `latest_open_checkpoint`, which ends at line 1588, before the `# -- board comment outbox` comment at line 1590)
- Test: `tests/test_store.py` (insert after `test_latest_open_checkpoint_skips_a_done_row_of_its_workflow_when_another_is_newest`, which ends at line 2576, before `# -- run controls and leases` at line 2579)

**Interfaces:**
- Consumes: existing `store.Store.open(repo, run_id)`, `Store.save_checkpoint`, and the test helpers in `tests/test_store.py`: `repo` fixture, `RUN_ID`, `OTHER_RUN_ID`, `_at(minute)`, `_save_checkpoint(st, card_id, *, reason, workflow, saved_at, ...)`.
- Produces: `Store.checkpoint_cards(self, run_id: str) -> list[tuple[str, str]]` returning `[(card_id, workflow), ...]` sorted by `card_id`, then `workflow`; `[]` for a run with no rows.

- [ ] **Step 1: Write the failing test**

Insert into `tests/test_store.py` after line 2576:

```python
def test_checkpoint_cards_lists_this_runs_distinct_pairs_in_order(repo):
    """af52db54: `am reset` reports every `(card_id, workflow)` the run
    checkpointed, whatever the reason, and nothing another run saved."""
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-b", reason="turn", saved_at=_at(0))
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(1))
        _save_checkpoint(st, "card-a", reason="parked", saved_at=_at(2))
        _save_checkpoint(st, "card-a", reason="done", saved_at=_at(3))
        _save_checkpoint(st, "card-a", reason="turn", workflow="integrate", saved_at=_at(4))
    finally:
        st.close()

    other = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(other, "card-c", reason="turn", saved_at=_at(5))
        _save_checkpoint(other, "card-a", reason="parked", workflow="bases", saved_at=_at(6))
        before = other.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
        # Asked from a store bound to another run: the argument decides.
        mine = other.checkpoint_cards(RUN_ID)
        theirs = other.checkpoint_cards(OTHER_RUN_ID)
        unknown = other.checkpoint_cards("run-never-saved")
        after = other.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    finally:
        other.close()

    assert mine == [("card-a", "integrate"), ("card-a", "task"), ("card-b", "task")]
    assert theirs == [("card-a", "bases"), ("card-c", "task")]
    assert unknown == []
    assert after == before == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_checkpoint_cards_lists_this_runs_distinct_pairs_in_order -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'checkpoint_cards'`

- [ ] **Step 3: Write minimal implementation**

Insert into `src/agent_manager/store.py` directly after the `return None if row is None else _checkpoint_from_row(row)` line that closes `latest_open_checkpoint` (line 1588), keeping one blank line before the `# -- board comment outbox` block:

```python

    def checkpoint_cards(self, run_id: str) -> list[tuple[str, str]]:
        """Every distinct `(card_id, workflow)` with a checkpoint row under `run_id`.

        Any `reason` counts, `done` included. Ordered by `card_id`, then
        `workflow`. Read-only, and `run_id` is the argument, never
        `self.run_id`: `am reset` asks it about the run it closes
        (am-reset §3.5, card af52db54).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT card_id, workflow FROM checkpoints"
                " WHERE run_id = ? ORDER BY card_id, workflow",
                (run_id,),
            ).fetchall()
            return [(row["card_id"], row["workflow"]) for row in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store.py -k checkpoint -v`
Expected: PASS (the new test and every existing checkpoint test)

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat: add Store.checkpoint_cards, a run's distinct checkpointed pairs"
```

---

### Task 2: `reset_run` reports `cards` with `open_in`

**Files:**
- Modify: `src/agent_manager/cli.py:2394-2411` (`reset_run`: compute `cards` after the `with run_lease(store) as lease:` block, inside the `try`, and replace the placeholder at lines 2408-2409)
- Modify: `tests/test_cli.py:36-50` (add `runs` to the `from agent_manager import (...)` list)
- Modify: `tests/test_cli.py:8579-8584` (section comment), `8617-8633` (`_plant_parked_checkpoint` gains keyword overrides), `8650` and `8673` (test 1 docstring and `cards` assertion)
- Test: `tests/test_cli.py` (new tests inserted after `test_two_resets_of_one_run_leave_exactly_one_run_upsert`, which ends at line 8906)

**Interfaces:**
- Consumes: `Store.checkpoint_cards(run_id: str) -> list[tuple[str, str]]` (Task 1); existing `Store.latest_open_checkpoint(card_id: str, workflow: str) -> Checkpoint | None`; `runs.continuable_checkpoint(store: Store, card_id: str) -> Checkpoint | None`; test helpers in `tests/test_cli.py`: `projection` fixture, `_plant_run`, `_record(root, run_id, *, started_at, status, with_phases)`, `RECORDED_AT`, `CONTROL_RUN_ID`, `CONTROL_NOW`, `OTHER_RUN_ID`, `_at(seconds)`, `_invoke_reset(root, run_id=CONTROL_RUN_ID, *extra)`, `_journal_lines`, `_recorded_status(root, run_id)`, `_checkpoint_rows(root)`, `_lease(root)`.
- Produces: `reset_run(...)["cards"]: list[dict[str, str | None]]`, each `{"card_id": str, "workflow": str, "open_in": str | None}`, in `checkpoint_cards` order. `_plant_parked_checkpoint(root, *, run_id=CONTROL_RUN_ID, card_id="card-1", workflow=task_workflow.TASK.name, reason="parked", saved_at=CONTROL_NOW) -> None`.

- [ ] **Step 1: Add `runs` to the test imports**

In `tests/test_cli.py`, change the import block at lines 36-50 so `runs` sits between `prompt` and `store as store_module`:

```python
from agent_manager import (
    board,
    census,
    cli,
    control,
    dag,
    dispatch,
    integration,
    locks,
    models,
    orchestrate,
    paths,
    prompt,
    runs,
    store as store_module,
)
```

- [ ] **Step 2: Generalise the checkpoint planter and update the section comment**

Replace the section comment's last line (line 8584) and the helper at lines 8617-8633 with:

```python
# only, no subprocess. `cards` (card af52db54) lists each `(card_id,
# workflow)` the run checkpointed with the run a relaunch would continue it
# from (`open_in`), or `null`.
```

```python
def _plant_parked_checkpoint(
    root: Path,
    *,
    run_id: str = CONTROL_RUN_ID,
    card_id: str = "card-1",
    workflow: str = task_workflow.TASK.name,
    reason: str = "parked",
    saved_at: datetime = CONTROL_NOW,
) -> None:
    """One checkpoint row, by default the open `parked` row of card-1 a paused
    walk leaves under the run. The keywords plant the other rows the `cards`
    tests need: another run's, another workflow's, an older or newer one."""
    opened = store_module.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        opened.save_checkpoint(
            card_id,
            workflow=workflow,
            digest=task_workflow.TASK.digest(),
            reason=reason,
            agent={
                "current_turn": None,
                "queue": [{"kwargs": {"phase": "plan", "loop": 0}}],
            },
            saved_at=saved_at,
        )
    finally:
        opened.close()
```

(The existing `_plant_parked_checkpoint(projection)` call in test 1 keeps working unchanged through the defaults.)

- [ ] **Step 3: Update existing reset test 1 (RED)**

In `test_reset_records_a_stopped_run_cancelled_through_one_journal_line`, change the docstring (line 8650) and the `"cards": []` entry of the expected dict (line 8673):

```python
    """Spec test 1, `cards` half from af52db54: the run's own row was the
    newest, so the card is closed and `open_in` is null."""
```

```python
        "cards": [{"card_id": "card-1", "workflow": "task", "open_in": None}],
```

Leave `test_reset_closes_a_run_that_never_saved_a_checkpoint` (asserts `cards == []`) and `test_reset_of_a_cancelled_run_is_a_no_op_that_writes_nothing` (no checkpoints, `cards: []`) unchanged.

- [ ] **Step 4: Write the new failing tests**

Insert into `tests/test_cli.py` after `test_two_resets_of_one_run_leave_exactly_one_run_upsert` (ends at line 8906):

```python
def _plant_other_run(root: Path, status: str = "stopped") -> None:
    """Run Y (`OTHER_RUN_ID`): a second run in the projection, as a crashed
    earlier life of the same card leaves it."""
    _record(root, OTHER_RUN_ID, started_at=RECORDED_AT, status=status, with_phases=False)


def test_a_repeated_reset_reports_the_same_cards_and_writes_nothing(projection):
    """af52db54 spec test 5: an already-cancelled reset still reports `cards`."""
    _plant_run(projection, status="stopped")
    _plant_parked_checkpoint(projection)

    first = _invoke_reset(projection)
    lines_after_first = _journal_lines()
    checkpoints_after_first = _checkpoint_rows(projection)
    second = _invoke_reset(projection)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_data = json.loads(first.stdout)["data"]
    second_data = json.loads(second.stdout)["data"]
    assert first_data["already_cancelled"] is False
    assert second_data["already_cancelled"] is True
    assert second_data["cards"] == first_data["cards"] == [
        {"card_id": "card-1", "workflow": "task", "open_in": None}
    ]
    assert _journal_lines() == lines_after_first
    assert _checkpoint_rows(projection) == checkpoints_after_first
    assert _lease(projection) is None


def test_reset_lists_every_pair_it_checkpointed_in_order_a_done_card_included(
    projection,
):
    """Review Focus 1: every `(card_id, workflow)` with a row under the run,
    any reason, ordered by card then workflow."""
    _plant_run(projection, status="stopped")
    _plant_parked_checkpoint(projection, card_id="card-2", reason="done", saved_at=_at(0))
    _plant_parked_checkpoint(projection, card_id="card-1", saved_at=_at(1))
    _plant_parked_checkpoint(
        projection, card_id="card-1", workflow="integrate", reason="turn", saved_at=_at(2)
    )

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["cards"] == [
        {"card_id": "card-1", "workflow": "integrate", "open_in": None},
        {"card_id": "card-1", "workflow": "task", "open_in": None},
        {"card_id": "card-2", "workflow": "task", "open_in": None},
    ]


def test_reset_reports_open_in_null_when_its_own_row_is_the_newest(projection):
    """af52db54 spec test 10, chain case: Y's open row is older than X's, so
    once X is cancelled the newest-row rule closes the card, and a relaunch
    continues nothing even though Y's row is still open. Two stores on one
    database: a reader bound to Y stays open across X's reset."""
    _plant_run(projection, status="stopped")
    _plant_other_run(projection)
    _plant_parked_checkpoint(projection, run_id=OTHER_RUN_ID, saved_at=_at(0))
    _plant_parked_checkpoint(projection, saved_at=_at(1))
    reader = store_module.Store.open(cli.resolve_repo_dir(projection), OTHER_RUN_ID)
    try:
        before = runs.continuable_checkpoint(reader, "card-1")
        assert before is not None and before.run_id == CONTROL_RUN_ID
        checkpoints_before = _checkpoint_rows(projection)

        result = _invoke_reset(projection)

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["cards"] == [
            {"card_id": "card-1", "workflow": "task", "open_in": None}
        ]
        assert runs.continuable_checkpoint(reader, "card-1") is None
        assert reader.latest_open_checkpoint("card-1", task_workflow.TASK.name) is None
    finally:
        reader.close()
    # Y's row is still there and Y is untouched: only the newest row decided.
    assert _checkpoint_rows(projection) == checkpoints_before
    assert any(row[0] == OTHER_RUN_ID for row in checkpoints_before)
    assert _recorded_status(projection, OTHER_RUN_ID) == "stopped"


def test_reset_names_the_run_a_newer_bases_row_keeps_the_card_open_in(projection):
    """af52db54 spec test 10, variant: Y's newest row for the card is under
    `bases`, so resetting X leaves Y's older `task` row continuable
    (`open_in: Y`). Once Y is reset too, every report is null."""
    _plant_run(projection, status="stopped")
    _plant_other_run(projection)
    _plant_parked_checkpoint(projection, run_id=OTHER_RUN_ID, saved_at=_at(0))
    _plant_parked_checkpoint(projection, reason="turn", saved_at=_at(1))
    _plant_parked_checkpoint(
        projection, run_id=OTHER_RUN_ID, workflow="bases", saved_at=_at(2)
    )
    checkpoints_before = _checkpoint_rows(projection)

    first = _invoke_reset(projection)

    assert first.exit_code == 0, first.output
    # Exactly X's own pair: Y's `bases` pair is not X's to report.
    assert json.loads(first.stdout)["data"]["cards"] == [
        {"card_id": "card-1", "workflow": "task", "open_in": OTHER_RUN_ID}
    ]

    other = _invoke_reset(projection, OTHER_RUN_ID)

    assert other.exit_code == 0, other.output
    other_data = json.loads(other.stdout)["data"]
    assert other_data["previous_status"] == "stopped"
    assert other_data["cards"] == [
        {"card_id": "card-1", "workflow": "bases", "open_in": None},
        {"card_id": "card-1", "workflow": "task", "open_in": None},
    ]

    again = _invoke_reset(projection)

    assert again.exit_code == 0, again.output
    again_data = json.loads(again.stdout)["data"]
    assert again_data["already_cancelled"] is True
    assert again_data["cards"] == [
        {"card_id": "card-1", "workflow": "task", "open_in": None}
    ]
    assert _checkpoint_rows(projection) == checkpoints_before
    assert _recorded_status(projection, OTHER_RUN_ID) == "cancelled"
```

- [ ] **Step 5: Run the reset tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k reset -v`
Expected: FAIL in `test_reset_records_a_stopped_run_cancelled_through_one_journal_line` (both params), `test_a_repeated_reset_reports_the_same_cards_and_writes_nothing`, `test_reset_lists_every_pair_it_checkpointed_in_order_a_done_card_included`, `test_reset_reports_open_in_null_when_its_own_row_is_the_newest` and `test_reset_names_the_run_a_newer_bases_row_keeps_the_card_open_in`, each on an assertion comparing `cards` against the placeholder `[]`. Every other reset test still PASSES.

- [ ] **Step 6: Write minimal implementation**

In `src/agent_manager/cli.py`, replace lines 2394-2411 (from `with run_lease(store) as lease:` through the payload's `"message"` entry) with:

```python
        with run_lease(store) as lease:
            current = store.load_run(run.id) or run
            if current.status == "done":
                raise _not_resettable_error(run.id)
            already = current.status == "cancelled"
            if not already:
                store.record_run(current.model_copy(update={"status": "cancelled"}))
        # After the status write (or the no-op): each `(card_id, workflow)` the
        # run checkpointed, with the run a relaunch would continue it from by
        # the newest-row rule -- `null` unless that is another run (§3.5).
        cards: list[dict[str, Any]] = []
        for card_id, workflow in store.checkpoint_cards(run.id):
            found = store.latest_open_checkpoint(card_id, workflow)
            cards.append(
                {
                    "card_id": card_id,
                    "workflow": workflow,
                    "open_in": (
                        found.run_id
                        if found is not None and found.run_id != run.id
                        else None
                    ),
                }
            )
    finally:
        store.close()
    payload: dict[str, Any] = {
        "run_id": run.id,
        "previous_status": current.status,
        "status": "cancelled",
        "already_cancelled": already,
        "cards": cards,
        "message": _reset_message(run.id, already=already),
```

Also update the `reset_run` docstring: replace lines 2358-2360 (from `    fenced `record_run` of `cancelled`. No checkpoint row is written or` through `    closed on every exit.`) with:

```python
    fenced `record_run` of `cancelled`. No checkpoint row is written or
    deleted, and no other row is touched. `cards` then reports each
    `(card_id, workflow)` the run checkpointed and, by
    `Store.latest_open_checkpoint`, which other run (if any) a relaunch
    would still continue it from (`open_in`). The lease is released and the
    store closed on every exit.
```

- [ ] **Step 7: Run the reset tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k reset -v`
Expected: PASS, every reset test, including the unchanged 736d6728 refusal tests (`test_reset_refuses_*`) and `test_reset_closes_a_run_that_never_saved_a_checkpoint`.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: am reset reports each checkpointed card and the run a relaunch would continue"
```

---

### Task 3: Full verification

**Files:** none modified.

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: a green default suite.

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest`
Expected: PASS (default `unit` + `git` tiers). If anything outside the reset/checkpoint tests fails, it is a regression from Task 2's `_plant_parked_checkpoint` change or `runs` import; fix it in place and re-run.

- [ ] **Step 2: Confirm the placeholder marker is gone**

Run: `grep -n "af52db54 fills this in" src/agent_manager/cli.py tests/test_cli.py`
Expected: no output (exit status 1).

- [ ] **Step 3: Commit (only if Step 1 required a fix)**

```bash
git add -u
git commit -m "fix: keep the default suite green after the am reset cards change"
```
