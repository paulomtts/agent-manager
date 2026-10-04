<!-- task-pipeline: validated -->
# Subtask 736d6728 — `am reset RUN_ID`, writing `cancelled` through the lease

Narrows `docs/superpowers/specs/2026-10-03-am-reset-design.md` (§3.1–3.4, the §3.4 `DeadRunError` pointer, and the envelope skeleton of §3.5) to one subtask under story 816cb8b3. The design is settled there; this document only fixes what this subtask delivers and what it leaves to its siblings.

## Scope

In scope:

- A new command `am reset RUN_ID [--repo-dir] [--pretty]` in `src/agent_manager/cli.py`. It does exactly this: load the run read-only, refuse, take the run's lease, write `cancelled`, release the lease.
- A new `NotResettableError(CliError)` in `cli.py`, next to `DeadRunError`/`RunIsLiveError` (cli.py:137-149). Those are the run-control refusals that only `cli.py` raises. `UnknownRunError`/`NotResumableError` live in `runs.py` because `orchestrate` raises them too, and nothing outside the CLI raises this one.
- Adding `store_module.CorruptJournalError` (store.py:284, a `JournalError` subclass) to `HANDLED` (cli.py:1142-1149). A crashed run's torn journal then becomes an exit-3 envelope instead of a traceback. Add only that subclass, not `JournalError` as a whole.
- Both `DeadRunError` wordings in `_controllable_lease` (cli.py:2150-2153 and 2155-2159) gain "or `am reset <run-id>` closes it" after the existing `am resume <run-id>` pointer. This is the only change to an existing command.

Out of scope, owned by siblings:

- af52db54 owns `Store.checkpoint_cards(run_id)`, the `latest_open_checkpoint`-based `open_in` computation, chain detection, and spec §4 test 10. This subtask always emits `cards: []` as a placeholder so the envelope key exists, and the sibling fills it in. This subtask does not call `latest_open_checkpoint`.
- 522adfb5 owns the relaunch/resume proofs (spec §4 tests 8, 9), the `git`-tier `worktree.ensure` companion, the `e2e_fake` incident replay (test 12) and the README update (§3.7).

Unchanged (§2): no raw SQL against the projection, no write outside `_fenced()` once a token is bound, no git of any kind, no push, no touching main/master, no card or board writes.

## Observable behavior

1. Read-only phase. Mirror `resume_run` (cli.py:1982-2057): `resolve_repo_dir`, `open_db`, `load_run`, then the refusals below on that same connection, in this order, all before `Store.open`. A refusal therefore leaves no run directory, lease row or journal line behind.
   1. The run id is not in the projection: `UnknownRunError`, with the same message style as `resume_run`'s (`am runs` lists the ones that are).
   2. A lease row is present and `control.lease_is_live`: `RunIsLiveError`. `_run_is_live_error` (cli.py:718) cannot be reused as-is here — its wording ("wait for it to exit, or `am status <run-id>`") has neither an `am cancel` pointer nor "nobody driving" framing, and is also what `run_lease`'s translation raises for step 3's race (acceptable there per §3.4, since that is only the rare post-read-only-check race). This refusal needs its own message, matching `_run_is_live_error`'s pid/host/heartbeat-age content but saying: the run is running, so `am cancel <run-id>` is the command; `am reset` is for a run nobody is driving. It must not point at `am resume`.
   3. `status == "done"`: `NotResettableError`. The message says the run finished, there is nothing to close, and new work starts with `am run`.

   Each refusal is an `{"ok": false, "error": {"type", "message"}}` envelope at exit 3.
2. Already cancelled. `status == "cancelled"` is not a refusal. The result is exit 0 with `already_cancelled: true`, `previous_status: "cancelled"`, and no journal line or row written. The design allows the check to sit inside the lease block (one code path, lease taken and released) or before it. Either way, nothing reaches the journal.
3. Write phase, for every other status (`stopped`, `escalated`, or `started` with no lease or a dead lease):
   - Call `Store.open(root, run_id)`, then `run_lease(store)` (cli.py:767) with **no claims**.
   - `take_lease` re-checks liveness atomically. A live holder that appeared after the read-only check surfaces as `RunIsLiveError` through `run_lease`'s translation (generic wording is acceptable there).
   - A dead holder is displaced and reported as `took_over: {pid, host, heartbeat_at}`, exactly as `_resume_from_checkpoint` reports it (cli.py:1970-1976).
   - Inside the block there is exactly one write: `store.record_run(run.model_copy(update={"status": "cancelled"}))` (store.py:1196). That is one fenced `run_upsert` journal line.
   - No checkpoint row is written or deleted. Story, subtask, phase and attempt rows are untouched.
   - When the block exits, the lease row is released and the store closed.
   - The command never looks at the filesystem beyond the store: worktree presence is irrelevant.
4. A run with no checkpoints resets like any other, with `cards: []`. There is no "nothing to reset" refusal.
5. Success envelope: `{"ok": true, "data": {"run_id", "previous_status", "status": "cancelled", "already_cancelled", "cards": [], "message", ["took_over"]}}`. `took_over` is present only when a dead lease was displaced. The message follows §3.5: the run is cancelled, `am resume` refuses it, and a relaunch starts its cards from their first phase.

## Tests

All tests go in `tests/test_cli.py`. Concurrency tests either go there or sit beside `tests/test_control.py`'s two-store pattern.

All are **`unit`** tier, unmarked, per the placement rule (design spec §14 / CLAUDE.md "Test tiers": a test's tier is chosen by what it actually spawns). They are driven through the `projection` fixture (tests/test_cli.py:3901) and the store, with `brd`/`git`/`claude` stubbed off `PATH`, and spawn no subprocess. None lives under `tests/steps/` or `tests/e2e/`, so no auto-marking applies, and none needs an explicit mark.

1. **Reset of a `stopped` run with an open `parked` checkpoint** (unit). Expected:
   - exit 0, and the run row reads `cancelled`;
   - the journal gained exactly one `run_upsert` line with `payload.status == "cancelled"` and the next `seq`;
   - `rebuild_from_journal` yields `cancelled`;
   - the checkpoint row is unchanged, and `latest_open_checkpoint` for the card returns `None`;
   - the envelope has `previous_status: "stopped"` and `already_cancelled: false`.

   Run it twice, once with the worktree directory present and once with it removed. The store write must be identical. The `cards`/`open_in` assertions are af52db54's.
2. **Refused: live lease** (unit). Setup: a lease row with a fresh heartbeat and this process's pid. Expected: `RunIsLiveError`, exit 3, a message naming `am cancel`, and status, journal length and lease row all unchanged.
3. **Started run with a dead lease** (unit). Setup: a stale heartbeat or a dead pid. Expected: success, `took_over` names the displaced pid/host/heartbeat_at, and no `run_leases` row for the run remains.
4. **Already cancelled** (unit). Expected: exit 0, `already_cancelled: true`, journal length unchanged.
5. **Refused: `done`** (unit). Expected: `NotResettableError`, exit 3, nothing written.
6. **Refused: unknown run** (unit). Expected: `UnknownRunError`, exit 3, and no run directory created.
7. **Run with no checkpoints** (unit). Expected: success with `cards: []` and the status reads `cancelled`. The follow-on `am resume` refusal is 522adfb5's test 9.
8. **Concurrency** (unit). Use two stores on one database, as `tests/test_control.py` does. Two cases:
   - Another store holds the run's lease live and `lease_is_live` is monkeypatched so the read-only check passes. The reset must still be refused at `take_lease` with `RunIsLiveError`, with nothing written.
   - Two sequential resets of one run leave exactly one `run_upsert` line, and the second reports `already_cancelled: true`.
9. **`CorruptJournalError` is handled** (unit). A run whose journal has a torn line in its middle produces an exit-3 `ok: false` envelope, not a traceback.
10. **`DeadRunError` pointer** (unit). `am cancel` of a `started` run gets `am reset <run-id>` in its message in both cases: with no lease and with a dead lease. Extend the existing `DeadRunError` tests if they assert on the message.

---

# `am reset RUN_ID` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `am reset RUN_ID [--repo-dir] [--pretty]`, which closes a run nobody is driving by recording it `cancelled` through one fenced `run_upsert` under the run's own lease, with three read-only refusals before `Store.open`.

**Architecture:** A pure-ish `reset_run(run_id, *, repo_dir) -> dict` in `src/agent_manager/cli.py` mirrors `resume_run`'s read-only refusal block, then `_resume_from_checkpoint`'s `Store.open` + `run_lease(store)` takeover, stopping right after the first write. The status is re-read under the lease, so two resets serialise on `take_lease` and the second is a no-op. A thin Typer command `reset` wraps it in `resume`'s envelope pattern. Two small edits ride along: `store_module.CorruptJournalError` joins `HANDLED`, and both `DeadRunError` wordings point at `am reset <run-id>`.

**Tech Stack:** Python, Typer, Pydantic, SQLite (via `agent_manager.store`), pytest with `typer.testing.CliRunner`.

**Spec:** `docs/superpowers/specs/task-add-am-reset-run-id-736d6728-design.md` (prepended above), narrowing `docs/superpowers/specs/2026-10-03-am-reset-design.md`.

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-add-am-reset-run-id-736d6728`, branch `m19/task-add-am-reset-run-id-736d6728`. Do not assume any sibling subtask's code (af52db54's `Store.checkpoint_cards`, 522adfb5's README/e2e) exists on this branch. Run every command below from the worktree root.

## Global Constraints

- No raw SQL against the projection from `cli.py`; every write goes through `Store` (`store.record_run`).
- No write outside `_fenced()` once a token is bound: the single `record_run` happens inside `with run_lease(store)`.
- `run_lease(store)` is called with no `claims` — reset owns no card and no branch.
- No git, no push, no main/master, no board/card writes, no filesystem look beyond the store (worktree presence is irrelevant).
- Never write or delete a checkpoint row; never touch story/subtask/phase/attempt rows.
- All three refusals (unknown, live lease, `done`) happen read-only, in that order, before `Store.open`, and exit 3 (`cli.EXIT_ERROR`) with `{"ok": false, "error": {"type", "message"}}`.
- `cancelled` is not a refusal: exit 0, `already_cancelled: true`, nothing journalled.
- Envelope keys: `run_id`, `previous_status`, `status` (always `"cancelled"`), `already_cancelled`, `cards` (always `[]` here — af52db54 fills it), `message`, plus `took_over: {pid, host, heartbeat_at}` only when a dead lease was displaced.
- Add only `store_module.CorruptJournalError` to `HANDLED`, never `JournalError` as a whole.
- Every test in this plan is `unit` tier: unmarked, in `tests/test_cli.py`, driven through the `projection` fixture, spawning no subprocess (do not use `_reaped_pid`, which spawns one).
- Verification: `uv run pytest`.

## Review Focus

1. `--pretty` on `am reset` — a human expects the same indented envelope every other command gives; pinned in Task 1 (`test_reset_pretty_prints_an_indented_envelope`).
2. A run that is `escalated`, or `started` with no lease at all, or a `milestone`-workflow run — the spec says "every other status" resets, whatever the workflow; pinned in Task 1 (`test_reset_closes_every_resettable_status_of_either_workflow`).
3. A live lease held from another host with a pid that means nothing here — a person expects it to be refused as live, not taken over; pinned in Task 2 (the `another-host` case of the live-lease refusal test).
4. A run that finishes `done` between the read-only check and `take_lease` (someone's `am resume` ran to completion in the gap) — a reset must never overwrite `done` with `cancelled`; pinned in Task 3 (`test_reset_rereads_the_status_under_the_lease_and_never_overwrites_done`).
5. A lease released even when the reset refuses under the lease — no `run_leases` row may linger after any reset outcome; asserted (`_lease(projection) is None`) in the Task 3 and Task 4 tests.

---

## File Structure

- Modify `src/agent_manager/cli.py`:
  - add `class NotResettableError(CliError)` right after `class RunIsLiveError` (currently cli.py:149-150);
  - add `store_module.CorruptJournalError` to `HANDLED` (currently cli.py:1142-1161) and one docstring sentence;
  - extend both `DeadRunError` messages in `_controllable_lease` (currently cli.py:2149-2159);
  - append, after the `cancel` command at the end of the file (currently cli.py:2291-2300), `_reset_live_error`, `_not_resettable_error`, `_reset_message`, `reset_run` and the `@app.command("reset")` function.
- Modify `tests/test_cli.py`: append one new section `# ── am reset (card 736d6728) ──` at the end of the file. It reuses module-level helpers already defined in the file: `runner`, `_Forbidden`, `_checkpoint_rows`, `_resume_guard_state`, `_record_milestone`, `CONTROL_RUN_ID`, `CONTROL_NOW`, `HERE`, `_at`, `_freeze_clock`, `_plant_run`, `_plant_lease`, `_lease`, `_controls`, `_invoke_control`. Imports already present at the top of the file: `json`, `os`, `datetime`, `timedelta`, `timezone`, `Path`, `pytest`, `cli`, `control`, `models`, `paths`, `store_module`, `task_workflow`.

---

### Task 1: `am reset` records a resettable run `cancelled` through its lease

**Files:**
- Modify: `src/agent_manager/cli.py` (append after the `cancel` command at the end of the file)
- Test: `tests/test_cli.py` (append a new section at the end of the file)

**Interfaces:**
- Consumes: `resolve_repo_dir(Path) -> Path`, `store_module.open_db(Path) -> sqlite3.Connection`, `store_module.load_run(conn, run_id) -> models.Run | None`, `Store.open(root, run_id) -> Store`, `run_lease(store, *, claims=()) -> ContextManager[control.Lease]`, `Store.record_run(models.Run) -> JournalLine`.
- Produces: `cli.reset_run(run_id: str, *, repo_dir: Path) -> dict[str, Any]`; `cli._reset_message(run_id: str) -> str` (gains a keyword `already: bool` in Task 3); Typer command `reset` (`am reset RUN_ID [--repo-dir PATH] [--pretty]`); test helpers `RESET_KEYS`, `_invoke_reset`, `_journal_lines`, `_recorded_status`, `_plant_parked_checkpoint`, `_open_checkpoint`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
# ── am reset (card 736d6728) ─────────────────────────────────────────────────
#
# `am reset RUN_ID` closes a run nobody is driving: read-only refusals, then
# the run's own lease with no claims, one fenced `run_upsert` of `cancelled`,
# and the lease released. Unit tier: the projection fixture and the store
# only, no subprocess. `cards` stays `[]` here; af52db54 fills it in.

RESET_KEYS = {
    "run_id",
    "previous_status",
    "status",
    "already_cancelled",
    "cards",
    "message",
}

RESET_MESSAGE = (
    f"run {CONTROL_RUN_ID} is cancelled; `am resume {CONTROL_RUN_ID}` refuses it,"
    " and a relaunch starts its cards from their first phase"
)


def _invoke_reset(root: Path, run_id: str = CONTROL_RUN_ID, *extra: str):
    return runner.invoke(cli.app, ["reset", run_id, "--repo-dir", str(root), *extra])


def _journal_lines(run_id: str = CONTROL_RUN_ID) -> list[store_module.JournalLine]:
    return store_module.Journal(run_id).read()


def _recorded_status(root: Path, run_id: str = CONTROL_RUN_ID) -> str | None:
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        return store_module.run_status(conn, run_id)
    finally:
        conn.close()


def _plant_parked_checkpoint(root: Path) -> None:
    """One open `parked` checkpoint of card-1 under the run, as a paused walk leaves it."""
    opened = store_module.Store.open(cli.resolve_repo_dir(root), CONTROL_RUN_ID)
    try:
        opened.save_checkpoint(
            "card-1",
            workflow=task_workflow.TASK.name,
            digest=task_workflow.TASK.digest(),
            reason="parked",
            agent={
                "current_turn": None,
                "queue": [{"kwargs": {"phase": "plan", "loop": 0}}],
            },
            saved_at=CONTROL_NOW,
        )
    finally:
        opened.close()


def _open_checkpoint(root: Path) -> store_module.Checkpoint | None:
    opened = store_module.Store.open(cli.resolve_repo_dir(root), CONTROL_RUN_ID)
    try:
        return opened.latest_open_checkpoint("card-1", task_workflow.TASK.name)
    finally:
        opened.close()


@pytest.mark.parametrize(
    "worktree_present", [True, False], ids=["worktree-present", "worktree-removed"]
)
def test_reset_records_a_stopped_run_cancelled_through_one_journal_line(
    projection, worktree_present
):
    """Spec test 1 (store half; `cards`/`open_in` are af52db54's)."""
    _plant_run(projection, status="stopped")
    _plant_parked_checkpoint(projection)
    worktree = projection / ".claude" / "worktrees" / "m1" / "task-x"
    if worktree_present:
        worktree.mkdir(parents=True)
    assert _open_checkpoint(projection) is not None
    lines_before = _journal_lines()
    checkpoints_before = _checkpoint_rows(projection)

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == RESET_KEYS
    assert data == {
        "run_id": CONTROL_RUN_ID,
        "previous_status": "stopped",
        "status": "cancelled",
        "already_cancelled": False,
        "cards": [],
        "message": RESET_MESSAGE,
    }
    assert _recorded_status(projection) == "cancelled"
    lines_after = _journal_lines()
    assert lines_after[: len(lines_before)] == lines_before
    (added,) = lines_after[len(lines_before) :]
    assert added.event == "run_upsert"
    assert added.seq == lines_before[-1].seq + 1
    first = next(line for line in lines_before if line.event == "run_upsert")
    # The same write whether or not the worktree exists: only `status` moved.
    assert added.payload == {**first.payload, "status": "cancelled"}
    assert _checkpoint_rows(projection) == checkpoints_before
    assert _open_checkpoint(projection) is None
    assert worktree.exists() is worktree_present
    assert _lease(projection) is None
    rebuilt = store_module.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        assert rebuilt.rebuild_from_journal(CONTROL_RUN_ID).status == "cancelled"
    finally:
        rebuilt.close()


def test_reset_closes_a_run_that_never_saved_a_checkpoint(projection):
    """Spec test 7: no "nothing to reset" refusal."""
    _plant_run(projection, status="stopped")
    assert _checkpoint_rows(projection) == []

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["cards"] == []
    assert data["status"] == "cancelled"
    assert _recorded_status(projection) == "cancelled"


@pytest.mark.parametrize("workflow", ["task", "milestone"])
@pytest.mark.parametrize("status", ["stopped", "escalated", "started"])
def test_reset_closes_every_resettable_status_of_either_workflow(
    projection, status, workflow
):
    """Review Focus 2: every status but `done`/`cancelled` resets, a
    `started` run with no lease included, whatever the workflow."""
    _plant_run(projection, status=status, workflow=workflow)

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["previous_status"] == status
    assert data["already_cancelled"] is False
    assert _recorded_status(projection) == "cancelled"
    assert _lease(projection) is None


def test_reset_pretty_prints_an_indented_envelope(projection):
    """Review Focus 1."""
    _plant_run(projection, status="stopped")

    result = _invoke_reset(projection, CONTROL_RUN_ID, "--pretty")

    assert result.exit_code == 0, result.output
    assert "\n" in result.stdout.strip()
    assert json.loads(result.stdout)["data"]["status"] == "cancelled"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "reset_records_a_stopped_run or reset_closes or reset_pretty" -v`
Expected: FAIL — every case exits 2 with Typer's "No such command 'reset'", so `result.exit_code == 0` fails.

- [ ] **Step 3: Write the minimal implementation**

Append to the end of `src/agent_manager/cli.py` (after the `cancel` command):

```python
def _reset_message(run_id: str) -> str:
    """What `am reset` tells the operator after closing `run_id` (am-reset §3.5)."""
    return (
        f"run {run_id} is cancelled; `am resume {run_id}` refuses it,"
        " and a relaunch starts its cards from their first phase"
    )


def reset_run(run_id: str, *, repo_dir: Path) -> dict[str, Any]:
    """Close a run nobody is driving by recording it `cancelled` (am-reset §3.2-3.3).

    The run is loaded read-only, exactly as `resume_run` loads it. Then, as
    `_resume_from_checkpoint` does up to its first write and no further:
    `Store.open`, the run's own lease with no claims (a reset drives no card
    and no branch), and one fenced `record_run` of `cancelled`. No checkpoint
    row is written or deleted, and no other row is touched. The lease is
    released and the store closed on every exit.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    store = Store.open(root, run.id)
    try:
        with run_lease(store):
            store.record_run(run.model_copy(update={"status": "cancelled"}))
    finally:
        store.close()
    return {
        "run_id": run.id,
        "previous_status": run.status,
        "status": "cancelled",
        "already_cancelled": False,
        # af52db54 fills this in from the run's checkpoints.
        "cards": [],
        "message": _reset_message(run.id),
    }


@app.command("reset")
def reset(
    run_id: str = typer.Argument(
        ..., metavar="RUN_ID", help="The run nobody is driving to close."
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is written."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Close a run nobody is driving: record it cancelled under its own lease.

    A running run wants `am cancel` instead; a finished one needs nothing.
    """
    try:
        payload = reset_run(run_id, repo_dir=repo_dir)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "reset_records_a_stopped_run or reset_closes or reset_pretty" -v`
Expected: PASS (2 + 1 + 6 + 1 = 10 cases).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: add am reset RUN_ID, recording a run cancelled under its lease"
```

---

### Task 2: The three read-only refusals, before `Store.open`

**Files:**
- Modify: `src/agent_manager/cli.py` — add `NotResettableError` after `class RunIsLiveError(CliError)` (currently cli.py:149-150); add `_reset_live_error` and `_not_resettable_error` just above `_reset_message`; replace `reset_run`'s read-only block.
- Test: `tests/test_cli.py` (append to the `am reset` section)

**Interfaces:**
- Consumes: `reset_run` from Task 1; `store_module.read_lease(conn, run_id) -> LeaseRow | None`; `control.lease_is_live(lease, *, now) -> bool`; `_utcnow() -> datetime`; `_heartbeat_age(lease, now) -> int`; `UnknownRunError` (imported from `runs`).
- Produces: `cli.NotResettableError(CliError)`; `cli._reset_live_error(lease: store_module.LeaseRow, now: datetime) -> RunIsLiveError`; `cli._not_resettable_error(run_id: str) -> NotResettableError` (Task 3 reuses it under the lease); test helper `_forbid_reset_writes(monkeypatch)`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
def _forbid_reset_writes(monkeypatch) -> None:
    """A refusal must come before `Store.open`, so reaching it fails the test."""
    monkeypatch.setattr(cli, "Store", _Forbidden("Store"))


def test_reset_refuses_an_unknown_run_and_creates_no_run_directory(
    projection, monkeypatch
):
    """Spec test 6."""
    _forbid_reset_writes(monkeypatch)

    result = _invoke_reset(projection, "no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "UnknownRunError",
        "message": (
            f"run 'no-such-run' is not in the projection for"
            f" {cli.resolve_repo_dir(projection)}"
            " (`agent-manager runs` lists the ones that are)"
        ),
    }
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()


@pytest.mark.parametrize(
    "lease, pid, host",
    [
        ({}, None, None),
        ({"pid": 0, "host": "am-test-other-host.invalid"}, 0, "am-test-other-host.invalid"),
    ],
    ids=["this-host", "another-host"],
)
def test_reset_refuses_a_run_whose_lease_is_live_and_points_at_am_cancel(
    projection, monkeypatch, lease, pid, host
):
    """Spec test 2, plus Review Focus 3: a fresh heartbeat from another host
    is live whatever its pid."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="started")
    _plant_lease(projection, heartbeat_at=_at(-5), **lease)
    before = (_resume_guard_state(projection), _recorded_status(projection))
    _forbid_reset_writes(monkeypatch)

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error == {
        "type": "RunIsLiveError",
        "message": (
            f"run {CONTROL_RUN_ID} is still running in pid"
            f" {os.getpid() if pid is None else pid} on {HERE if host is None else host}"
            f" (heartbeat 5s ago); `am cancel {CONTROL_RUN_ID}` stops it,"
            " and `am reset` is for a run nobody is driving"
        ),
    }
    assert "am resume" not in error["message"]
    assert (_resume_guard_state(projection), _recorded_status(projection)) == before


@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_reset_refuses_a_finished_run_and_writes_nothing(
    projection, monkeypatch, workflow
):
    """Spec test 5."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="done", workflow=workflow)
    before = (_resume_guard_state(projection), _recorded_status(projection))
    _forbid_reset_writes(monkeypatch)

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "NotResettableError",
        "message": (
            f"run {CONTROL_RUN_ID} finished (done), so there is nothing to close;"
            " start new work with `am run`"
        ),
    }
    assert (_resume_guard_state(projection), _recorded_status(projection)) == before


def test_not_resettable_error_is_a_handled_cli_error():
    assert isinstance(cli.NotResettableError("finished"), cli.CliError)
    assert isinstance(cli.NotResettableError("finished"), cli.HANDLED)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "reset_refuses or not_resettable_error" -v`
Expected: FAIL — `test_not_resettable_error_is_a_handled_cli_error` fails with `AttributeError: module 'agent_manager.cli' has no attribute 'NotResettableError'`; the three refusal tests fail with `pytest.fail("the milestone dry run reached cli.Store.open")` (or, for the unknown run, the same `_Forbidden` failure), because Task 1's `reset_run` goes straight to `Store.open`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, directly after

```python
class RunIsLiveError(CliError):
    """`am resume` was asked for a run another live process still holds (C10)."""
```

add:

```python


class NotResettableError(CliError):
    """`am reset` was asked to close a run that finished `done` (am-reset §3.4).

    Only the CLI raises it, so it lives beside `RunIsLiveError` and
    `DeadRunError` rather than in `runs` with `NotResumableError`.
    """
```

Directly above `def _reset_message(run_id: str) -> str:` add:

```python
def _reset_live_error(lease: store_module.LeaseRow, now: datetime) -> RunIsLiveError:
    """`am reset`'s read-only refusal of a run a live process holds (am-reset §3.4).

    `_run_is_live_error`'s pid, host and heartbeat age, but pointing at
    `am cancel`: the run is being driven, and a reset is for one that is not.
    """
    return RunIsLiveError(
        f"run {lease.run_id} is still running in pid {lease.pid} on {lease.host}"
        f" (heartbeat {_heartbeat_age(lease, now)}s ago); `am cancel {lease.run_id}`"
        " stops it, and `am reset` is for a run nobody is driving"
    )


def _not_resettable_error(run_id: str) -> NotResettableError:
    """`am reset`'s refusal of a finished run, worded once for both places it is checked."""
    return NotResettableError(
        f"run {run_id} finished (done), so there is nothing to close;"
        " start new work with `am run`"
    )


```

In `reset_run`, replace

```python
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
```

with

```python
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        # Unknown, live, done: read-only and before `Store.open`, which would
        # mint a run directory, so a refusal leaves nothing behind (§3.4).
        lease = store_module.read_lease(conn, run.id)
        now = _utcnow()
        if lease is not None and control.lease_is_live(lease, now=now):
            raise _reset_live_error(lease, now)
        if run.status == "done":
            raise _not_resettable_error(run.id)
    finally:
        conn.close()
```

and extend `reset_run`'s docstring first paragraph with: "Refused, in order and before `Store.open`: an unknown run (`UnknownRunError`), a run a live process holds (`RunIsLiveError`, pointing at `am cancel`), and a finished one (`NotResettableError`)."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "reset" -v`
Expected: PASS (Task 1's cases still pass, plus the new refusal cases).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: refuse am reset of an unknown, live or finished run before opening the store"
```

---

### Task 3: Takeover, the already-cancelled no-op, and the status re-read under the lease

**Files:**
- Modify: `src/agent_manager/cli.py` — `_reset_message` gains `already`; `reset_run`'s store block and payload.
- Test: `tests/test_cli.py` (append to the `am reset` section)

**Interfaces:**
- Consumes: `reset_run`, `_not_resettable_error`, `_reset_message` from Tasks 1-2; `control.Lease.displaced: LeaseRow | None`; `Store.load_run(run_id) -> models.Run | None`; `Store.take_lease(*, token, pid, host, now, is_live, claims=()) -> LeaseTake`.
- Produces: `cli._reset_message(run_id: str, *, already: bool) -> str`; `reset_run`'s payload adds `took_over: {"pid": int, "host": str, "heartbeat_at": str}` when a dead lease was displaced, and reports `already_cancelled: True` / `previous_status` read under the lease.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
@pytest.mark.parametrize(
    "stale, pid",
    [(True, None), (False, 0)],
    ids=["stale-heartbeat", "dead-pid-on-this-host"],
)
def test_reset_takes_over_a_dead_lease_and_names_its_holder(
    projection, monkeypatch, stale, pid
):
    """Spec test 3, the crash case. `control.Lease` judges liveness on the
    real clock, so the planted heartbeat is relative to the real now."""
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_run(projection, status="started")
    heartbeat = now - timedelta(seconds=31) if stale else now
    _plant_lease(projection, token="crashed", pid=pid, heartbeat_at=heartbeat)
    lines_before = _journal_lines()

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert set(data) == RESET_KEYS | {"took_over"}
    assert data["took_over"] == {
        "pid": os.getpid() if pid is None else pid,
        "host": HERE,
        "heartbeat_at": heartbeat.isoformat(),
    }
    assert data["previous_status"] == "started"
    assert data["already_cancelled"] is False
    assert _recorded_status(projection) == "cancelled"
    assert len(_journal_lines()) == len(lines_before) + 1
    assert _lease(projection) is None


def test_reset_of_a_cancelled_run_is_a_no_op_that_writes_nothing(projection):
    """Spec test 4."""
    _plant_run(projection, status="cancelled")
    lines_before = _journal_lines()

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data == {
        "run_id": CONTROL_RUN_ID,
        "previous_status": "cancelled",
        "status": "cancelled",
        "already_cancelled": True,
        "cards": [],
        "message": f"run {CONTROL_RUN_ID} was already cancelled; nothing was written",
    }
    assert _journal_lines() == lines_before
    assert _recorded_status(projection) == "cancelled"
    assert _lease(projection) is None


def test_two_resets_of_one_run_leave_exactly_one_run_upsert(projection):
    """Spec test 8, second case: the second reset sees `cancelled` under the
    lease and is a no-op."""
    _plant_run(projection, status="stopped")
    upserts_before = [line for line in _journal_lines() if line.event == "run_upsert"]

    first = _invoke_reset(projection)
    second = _invoke_reset(projection)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert json.loads(first.stdout)["data"]["already_cancelled"] is False
    second_data = json.loads(second.stdout)["data"]
    assert second_data["already_cancelled"] is True
    assert second_data["previous_status"] == "cancelled"
    upserts_after = [line for line in _journal_lines() if line.event == "run_upsert"]
    assert len(upserts_after) == len(upserts_before) + 1
    assert _lease(projection) is None


def test_reset_is_refused_at_take_lease_when_a_live_holder_slips_past_the_check(
    projection, monkeypatch
):
    """Spec test 8, first case: two stores on one database. The holder's
    lease is live; `lease_is_live` is blinded for the read-only check only,
    so `take_lease`'s atomic re-check is what refuses."""
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_run(projection, status="started")
    holder = store_module.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        holder.take_lease(
            token="holder", pid=os.getpid(), host=HERE, now=now, is_live=lambda row: True
        )
    finally:
        holder.close()
    real = control.lease_is_live
    seen: list[str] = []

    def blind_first(lease, **kwargs):
        seen.append(lease.token)
        if len(seen) == 1:
            return False
        return real(lease, **kwargs)

    monkeypatch.setattr(control, "lease_is_live", blind_first)
    lines_before = _journal_lines()

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "RunIsLiveError"
    assert seen[:2] == ["holder", "holder"]
    assert _journal_lines() == lines_before
    assert _recorded_status(projection) == "started"
    lease = _lease(projection)
    assert lease is not None and lease.token == "holder"


def test_reset_rereads_the_status_under_the_lease_and_never_overwrites_done(
    projection, monkeypatch
):
    """Review Focus 4: the run finished between the read-only check and
    `take_lease`. The read-only load is made to see `stopped`; the load
    under the lease sees the real `done` and refuses before writing."""
    _plant_run(projection, status="done")
    real = store_module.load_run
    calls: list[str] = []

    def stale_first(conn, run_id):
        loaded = real(conn, run_id)
        calls.append(run_id)
        if len(calls) == 1 and loaded is not None:
            return loaded.model_copy(update={"status": "stopped"})
        return loaded

    monkeypatch.setattr(store_module, "load_run", stale_first)
    lines_before = _journal_lines()

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "NotResettableError"
    assert len(calls) == 2
    assert _journal_lines() == lines_before
    assert _recorded_status(projection) == "done"
    assert _lease(projection) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "reset_takes_over or cancelled_run_is_a_no_op or two_resets or slips_past or never_overwrites_done" -v`
Expected: FAIL — `reset_takes_over…` fails on `set(data) == RESET_KEYS | {"took_over"}` (no `took_over`); `cancelled_run_is_a_no_op` and `two_resets` fail on `already_cancelled` being `False` and an extra journal line; `never_overwrites_done` fails with exit 0 and `done` overwritten by `cancelled`. `slips_past` may already pass (it pins `run_lease`'s existing translation); that is expected and fine — keep it as the regression guard the spec names.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, replace `_reset_message` with:

```python
def _reset_message(run_id: str, *, already: bool) -> str:
    """What `am reset` tells the operator about `run_id` (am-reset §3.5)."""
    if already:
        return f"run {run_id} was already cancelled; nothing was written"
    return (
        f"run {run_id} is cancelled; `am resume {run_id}` refuses it,"
        " and a relaunch starts its cards from their first phase"
    )
```

In `reset_run`, replace everything from `store = Store.open(root, run.id)` to the end of the function with:

```python
    store = Store.open(root, run.id)
    try:
        # `take_lease` re-checks liveness atomically: a live holder that
        # appeared since the check above refuses here as `RunIsLiveError`,
        # and a dead one is taken over. The status is read again under the
        # lease, so two resets serialise (the second sees `cancelled` and
        # writes nothing) and a run that finished meanwhile is never
        # overwritten. No claims: a reset drives no card and no branch.
        with run_lease(store) as lease:
            current = store.load_run(run.id) or run
            if current.status == "done":
                raise _not_resettable_error(run.id)
            already = current.status == "cancelled"
            if not already:
                store.record_run(current.model_copy(update={"status": "cancelled"}))
    finally:
        store.close()
    payload: dict[str, Any] = {
        "run_id": run.id,
        "previous_status": current.status,
        "status": "cancelled",
        "already_cancelled": already,
        # af52db54 fills this in from the run's checkpoints.
        "cards": [],
        "message": _reset_message(run.id, already=already),
    }
    if lease.displaced is not None:
        # A dead holder's lease was taken over (X5): say whose, as resume does.
        payload["took_over"] = {
            "pid": lease.displaced.pid,
            "host": lease.displaced.host,
            "heartbeat_at": lease.displaced.heartbeat_at.isoformat(),
        }
    return payload
```

Add to `reset_run`'s docstring: "A run already `cancelled` is not refused: the lease is taken and released around the check, and nothing is journalled (`already_cancelled: true`). A displaced dead holder is reported under `took_over`."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "reset" -v`
Expected: PASS (all Task 1-3 cases).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: am reset takes over a dead lease and is a no-op on a cancelled run"
```

---

### Task 4: A torn journal is an exit-3 envelope, not a traceback

**Files:**
- Modify: `src/agent_manager/cli.py` — `HANDLED` (currently cli.py:1142-1161)
- Test: `tests/test_cli.py` (append to the `am reset` section)

**Interfaces:**
- Consumes: `reset_run`/`reset` from Tasks 1-3; `store_module.CorruptJournalError`, `store_module.MissingJournalError`; `store_module.Journal(run_id).path`.
- Produces: `cli.HANDLED` containing `store_module.CorruptJournalError` (and not `JournalError`/`MissingJournalError`).

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
def test_reset_of_a_run_whose_journal_is_torn_mid_file_is_an_envelope(projection):
    """Spec test 9: `Store.open` reads the journal's highest `seq`, and a
    non-JSON line in its middle is `CorruptJournalError` -- a refusal at
    exit 3, not a traceback."""
    _plant_run(projection, status="stopped")
    path = store_module.Journal(CONTROL_RUN_ID).path
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text(lines[0] + "{torn\n" + "".join(lines[1:]), encoding="utf-8")

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CorruptJournalError"
    assert f"{path}:2:" in envelope["error"]["message"]
    assert _recorded_status(projection) == "stopped"
    assert _lease(projection) is None


def test_handled_takes_a_corrupt_journal_but_not_every_journal_error():
    """Only the torn-journal subclass is a refusal; a missing journal or
    any other `JournalError` stays a bug with its stack."""
    assert isinstance(store_module.CorruptJournalError("torn"), cli.HANDLED)
    assert not isinstance(store_module.MissingJournalError("gone"), cli.HANDLED)
    assert not isinstance(store_module.JournalError("other"), cli.HANDLED)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "torn_mid_file or corrupt_journal_but_not" -v`
Expected: FAIL — the reset test exits 1 with `result.exception` a `CorruptJournalError` (no envelope, so `result.exit_code == cli.EXIT_ERROR` fails); the `HANDLED` test fails on its first assertion.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, replace

```python
    locks.LockTimeoutError,
    store_module.LeaseLostError,
)
```

(the tail of `HANDLED`) with

```python
    locks.LockTimeoutError,
    store_module.LeaseLostError,
    store_module.CorruptJournalError,
)
```

and in the `HANDLED` docstring, replace

```
holder. It is a `BaseException`, so it has to be listed by name. Anything outside
this tuple is a bug in this program and should crash loudly with its stack intact.
```

with

```
holder. It is a `BaseException`, so it has to be listed by name.
`store_module.CorruptJournalError` is in it because a crashed run can leave a
torn line in its journal, and `Store.open` reading it (`am reset`, `am resume`)
is a refusal naming the file and line, not a bug; only that subclass, not
`JournalError` as a whole. Anything outside this tuple is a bug in this program
and should crash loudly with its stack intact.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "torn_mid_file or corrupt_journal_but_not or watch or handled" -v`
Expected: PASS (the `watch` tests that assert `CorruptJournalError` envelopes still pass; `WATCH_HANDLED` already spreads `HANDLED`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "fix: turn a torn journal into an exit-3 envelope instead of a traceback"
```

---

### Task 5: `DeadRunError` points at `am reset <run-id>`

**Files:**
- Modify: `src/agent_manager/cli.py` — both `DeadRunError(...)` calls in `_controllable_lease` (currently cli.py:2149-2159)
- Test: `tests/test_cli.py` (append to the `am reset` section)

**Interfaces:**
- Consumes: `_controllable_lease` (unchanged signature), test helpers `_freeze_clock`, `_plant_run`, `_plant_lease`, `_invoke_control`, `_controls`.
- Produces: both `DeadRunError` messages end with "`am resume <run-id>` picks it up, or `am reset <run-id>` closes it".

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/test_cli.py`:

```python
@pytest.mark.parametrize(
    "lease",
    [None, {"heartbeat_at": CONTROL_NOW - timedelta(seconds=31)}],
    ids=["no-lease", "dead-lease"],
)
def test_a_cancel_of_a_dead_run_points_at_am_resume_and_am_reset(
    projection, monkeypatch, lease
):
    """Spec test 10: both `DeadRunError` wordings name `am reset <run-id>`."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    if lease is not None:
        _plant_lease(projection, **lease)

    result = _invoke_control(projection, "cancel")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "DeadRunError"
    assert error["message"].endswith(
        f"`am resume {CONTROL_RUN_ID}` picks it up,"
        f" or `am reset {CONTROL_RUN_ID}` closes it"
    )
    assert _controls(projection) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k "points_at_am_resume_and_am_reset" -v`
Expected: FAIL on the `endswith` assertion — both messages currently end at "picks it up".

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, inside `_controllable_lease`, replace

```python
        raise DeadRunError(
            f"run {run_id} is recorded started but no process holds its lease;"
            f" it is not running, so `am resume {run_id}` picks it up"
        )
```

with

```python
        raise DeadRunError(
            f"run {run_id} is recorded started but no process holds its lease;"
            f" it is not running, so `am resume {run_id}` picks it up,"
            f" or `am reset {run_id}` closes it"
        )
```

and replace

```python
            f" on {lease.host}, last heartbeat {_heartbeat_age(lease, now)}s ago;"
            f" `am resume {run_id}` picks it up"
        )
```

with

```python
            f" on {lease.host}, last heartbeat {_heartbeat_age(lease, now)}s ago;"
            f" `am resume {run_id}` picks it up, or `am reset {run_id}` closes it"
        )
```

Also extend the `DeadRunError` class docstring's last sentence to: "The message names the lease's pid, host and heartbeat age, or says there is no lease, and points at `am resume` and `am reset`."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "dead_run or no_live_lease or dead_lease or points_at_am_resume_and_am_reset" -v`
Expected: PASS — the new test and the existing `test_a_request_to_a_started_run_with_no_live_lease_is_refused` / `test_a_dead_lease_is_refused_as_dead_even_when_its_window_has_closed` (which assert on message pieces, not the whole message).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: point DeadRunError at am reset alongside am resume"
```

---

### Task 6: Full verification

**Files:** none changed.

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest`
Expected: PASS, with the default `unit` + `git` tiers inside their budgets. Every new test is unmarked (`unit`) and spawns no subprocess; if any new test exceeds ≤0.5s, investigate (the `control.Lease` heartbeat thread is joined on exit and should not add latency).

- [ ] **Step 2: Confirm the scope boundary**

Run: `git diff master --stat -- src/ tests/` (or against this branch's base `m19/task-subtasksummary-resumed-dd932306`)
Expected: only `src/agent_manager/cli.py` and `tests/test_cli.py` changed; no `store.py` change (no `checkpoint_cards`), no README, no `tests/e2e/` or `tests/steps/` file.
