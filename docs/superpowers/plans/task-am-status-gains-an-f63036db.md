<!-- task-pipeline: validated -->
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

---

# `am status` integrity key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am status` reports, under an always-present `integrity` key, whether the run's journal and its SQLite projection agree, without writing anything and without ever changing the exit code.

**Architecture:** A new pure-ish renderer `cli.integrity_view(run_id, run, lease, *, now)` sits next to `cli.control_view`. It short-circuits on a live lease, otherwise reads the journal through the non-creating `Journal._for_reading(...).read(ignore_torn_tail=True)`, hands the lines and the already-loaded projection to the sibling's `store_module.diverging`, and turns every journal failure into a `checked: false` reason. `status_for` attaches its result to the payload it already builds; `status_payload` itself is untouched.

**Tech Stack:** Python 3, Typer, Pydantic v2, SQLite (`sqlite3`), pytest with `typer.testing.CliRunner`. Run everything with `uv run`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-am-status-gains-an-f63036db/docs/superpowers/specs/task-am-status-gains-an-f63036db-design.md` (prepended above).

## Global Constraints

- `integrity` is always present with exactly the keys `checked`, `reason`, `mismatches`; exit code is 0 in every integrity outcome; no error envelope from any integrity path.
- Reasons are exactly `"lease is live"`, `"no journal"`, or `"journal unreadable: <str(error)>"`; `reason` is `None` when `checked` is true.
- Order: live lease first (journal not read, `diverging` not called), then journal read + `diverging` inside ONE `try`, then `checked: true`.
- Journal is opened only via `store_module.Journal._for_reading(run_id).read(ignore_torn_tail=True)`; never `store_module.Journal(run_id)`, never `Store.open`.
- Catch `store_module.MissingJournalError` (→ `"no journal"`) before `(store_module.JournalError, ValidationError)` (→ `"journal unreadable: ..."`); `ValidationError` is `pydantic.ValidationError`.
- Mismatches serialize with `dataclasses.asdict`, in the order `diverging` returns them; never rely on `render`'s `default=str`.
- Report-only: no `record_*`, no row or journal write, no control request. `status_for`'s "Read-only: no `record_*` is called" stays literally true.
- Do not touch `diverging`, `Mismatch`, `rebuild_from_journal`, `ProjectionDivergedError`, the store.py module docstring, or the README (siblings 80dade04 / d8943ef5).
- All new tests are unmarked (`unit` tier) in `tests/test_cli.py`; they spawn no subprocess.
- Verification: `uv run pytest`. No typecheck or lint is configured.

## Review Focus

- An existing but empty `journal.jsonl` (a crash right after file creation): `read()` returns `[]` and `replay` inside `diverging` raises `JournalError("journal contains no run_upsert line")`. Expected: `checked: false, reason: "journal unreadable: journal contains no run_upsert line"`, exit 0. Pinned in Task 1.
- A journal line that parses as an envelope but whose payload fails model validation (e.g. a run status written by a newer schema): `diverging` raises a pydantic `ValidationError`. Expected: `"journal unreadable: ..."` at exit 0, not a traceback. Pinned in Task 1.
- A journal written by a newer `am` with an event kind this version does not know: expected to be skipped, the run still checked with no mismatches. Pinned in Task 1.
- A live lease over a journal that is itself corrupt: the live-lease rule wins and the journal is never opened. Expected: `"lease is live"`, not `"journal unreadable"`. Pinned in Task 2.
- `am status` with no RUN_ID (the "most recent run" default): the same `integrity` key must appear, computed for the defaulted run id. Pinned in Task 1 (the clean-run test loops over both invocations).

---

## File Structure

- Modify `src/agent_manager/cli.py`:
  - imports (line 28 `from dataclasses import dataclass`; after line 33 `import typer`) — add `asdict` and `pydantic.ValidationError`.
  - new `integrity_view` directly after `control_view` (ends line 289).
  - `status_for` (lines 1430-1474) — docstring sentence plus attaching `payload["integrity"]`.
- Modify `tests/test_cli.py`: append a new section at the end of the file (after line 9217). It reuses existing helpers that are defined earlier in the module: `projection` fixture (3902), `_record` (3926), `RECORDED_AT` (3990), `CONTROL_RUN_ID`/`CONTROL_NOW` (6225-6226), `_at` (6238), `_freeze_clock` (6242), `_plant_run` (6247), `_plant_lease` (6254), `_controls` (6343), plus module imports `cli`, `paths`, `store_module`, `json`, `shutil`, `sqlite3`, `pytest`, `Path`, and the module-level `runner`.

---

### Task 1: `integrity_view` checks the journal against the projection, and tolerates every journal failure

**Files:**
- Modify: `src/agent_manager/cli.py:28` (dataclasses import), `src/agent_manager/cli.py:33` (pydantic import), insert after `src/agent_manager/cli.py:289` (`integrity_view`), `src/agent_manager/cli.py:1430-1474` (`status_for`)
- Test: `tests/test_cli.py` (append at end of file)

**Interfaces:**
- Consumes (from base branch `m19/task-store-diverging-80dade04`): `store_module.diverging(lines: list[store_module.JournalLine], projection: models.Run) -> list[store_module.Mismatch]`; `store_module.Mismatch` is a frozen dataclass with fields `node: dict[str, str | int | None]`, `field: Literal["status"] | None`, `journal: str | None`, `projection: str | None`, `kind: Literal["stale", "foreign"]`. Also `store_module.Journal._for_reading(run_id: str) -> Journal`, `Journal.read(*, ignore_torn_tail: bool = False) -> list[JournalLine]`, `store_module.JournalError`, `store_module.MissingJournalError`.
- Produces: `cli.integrity_view(run_id: str, run: models.Run, lease: store_module.LeaseRow | None, *, now: datetime) -> dict[str, Any]` returning `{"checked": bool, "reason": str | None, "mismatches": list[dict[str, Any]]}`; `status_for`'s payload gains key `"integrity"`. In this task `lease`/`now` are accepted but not yet used; Task 2 adds the live-lease rule.

- [ ] **Step 1: Confirm the base carries `diverging`**

Run: `grep -n "^def diverging\|^class Mismatch" /home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-am-status-gains-an-f63036db/src/agent_manager/store.py`
Expected: two hits (`class Mismatch:` near line 596, `def diverging(` near line 741). If there are none, stop: the branch was not cut from `m19/task-store-diverging-80dade04`.

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
# ── am status integrity (card f63036db) ─────────────────────────────────────
#
# journal/DB divergence spec §3.3, §3.5, §3.7: `status` compares the journal
# with the projection through `store.diverging` and reports it under an
# always-present `integrity` key, at exit 0, writing nothing. Unit tier: the
# projection fixture writes SQLite rows and journal files in `tmp_path`; no
# subprocess.

CLEAN_INTEGRITY = {"checked": True, "reason": None, "mismatches": []}

RUN_CANCELLED_BY_HAND = {
    "node": {"story": None, "card": None, "phase": None, "attempt": None},
    "field": "status",
    "journal": "started",
    "projection": "cancelled",
    "kind": "foreign",
}
"""What `_plant_run` (journaled `started`) reports after `_hand_edit_run_status(..., "cancelled")`."""


def _journal_path(run_id: str = CONTROL_RUN_ID) -> Path:
    """The run's journal file, located without `Journal(run_id)`, which would
    create the run directory."""
    return paths.data_dir() / "runs" / run_id / store_module.JOURNAL_NAME


def _hand_edit_run_status(root: Path, status: str, run_id: str = CONTROL_RUN_ID) -> None:
    """Change the run's projected status behind the store's back, as a human
    with `sqlite3` would: no journal line records it."""
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        with store_module.immediate(conn):
            conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
    finally:
        conn.close()


def _status_data(root: Path, *args: str) -> dict[str, Any]:
    result = runner.invoke(cli.app, ["status", *args, "--repo-dir", str(root)])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    return envelope["data"]


def _projection_snapshot(root: Path) -> dict[str, list[str]]:
    """Every table's rows, order-insensitively, over a plain read connection."""
    db = paths.project_db_path(cli.resolve_repo_dir(root))
    conn = sqlite3.connect(db)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]
        return {
            name: sorted(repr(row) for row in conn.execute(f'SELECT * FROM "{name}"').fetchall())
            for name in tables
        }
    finally:
        conn.close()


def test_status_of_a_clean_run_is_checked_and_otherwise_unchanged(projection, monkeypatch):
    """Spec test 1; Review Focus: the no-RUN_ID default carries `integrity` too.
    Every key but `integrity` renders byte-for-byte as `status_payload` did
    before this card."""
    _freeze_clock(monkeypatch)
    _record(projection, CONTROL_RUN_ID, started_at=RECORDED_AT, status="started")
    conn = store_module.open_db(cli.resolve_repo_dir(projection))
    try:
        run = store_module.load_run(conn, CONTROL_RUN_ID)
    finally:
        conn.close()
    before = cli.status_payload(run, cli.control_view(None, [], now=CONTROL_NOW))
    expected = cli.render(cli.ok_envelope({**before, "integrity": CLEAN_INTEGRITY}))

    for args in (["status", CONTROL_RUN_ID], ["status"]):
        result = runner.invoke(cli.app, [*args, "--repo-dir", str(projection)])

        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == expected


def test_status_reports_a_run_hand_edited_to_cancelled_as_one_foreign_mismatch(
    projection, monkeypatch
):
    """Spec test 2: report-only -- exit 0, and no control request is filed."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _hand_edit_run_status(projection, "cancelled")

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == {
        "checked": True,
        "reason": None,
        "mismatches": [RUN_CANCELLED_BY_HAND],
    }
    assert data["control"]["requests"] == []
    assert _controls(projection) == []


def test_the_integrity_check_writes_no_row_and_no_journal_byte(projection, monkeypatch):
    """Spec test 3: every table and the journal's bytes are identical after a
    `status` that found and reported a mismatch."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _hand_edit_run_status(projection, "cancelled")
    tables_before = _projection_snapshot(projection)
    journal_before = _journal_path().read_bytes()
    runs_before = sorted(p.name for p in (paths.data_dir() / "runs").iterdir())

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"]["checked"] is True
    assert data["integrity"]["mismatches"] == [RUN_CANCELLED_BY_HAND]
    assert _projection_snapshot(projection) == tables_before
    assert _journal_path().read_bytes() == journal_before
    assert sorted(p.name for p in (paths.data_dir() / "runs").iterdir()) == runs_before


def test_status_of_a_run_with_no_journal_says_so_and_creates_no_run_directory(
    projection, monkeypatch
):
    """Spec test 5: `Journal._for_reading`, never the constructor that calls
    `paths.run_dir` and would create the directory."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    run_dir = paths.data_dir() / "runs" / CONTROL_RUN_ID
    shutil.rmtree(run_dir)

    def forbidden(self, run_id):
        raise AssertionError("status must not construct Journal(run_id)")

    monkeypatch.setattr(store_module.Journal, "__init__", forbidden)

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == {"checked": False, "reason": "no journal", "mismatches": []}
    assert not run_dir.exists()


def test_a_torn_final_journal_line_is_ignored_and_the_run_still_checked(
    projection, monkeypatch
):
    """Spec test 6, first half: an append in flight, no trailing newline."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    with _journal_path().open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 9999, "ts": "2026-')

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == CLEAN_INTEGRITY


def test_a_newline_terminated_non_json_line_makes_the_journal_unreadable(
    projection, monkeypatch
):
    """Spec test 6, second half: still `CorruptJournalError`, reported at exit 0."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    with _journal_path().open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    data = _status_data(projection, CONTROL_RUN_ID)

    integrity = data["integrity"]
    assert integrity["checked"] is False
    assert integrity["reason"].startswith("journal unreadable: ")
    assert "line is not JSON" in integrity["reason"]
    assert integrity["mismatches"] == []


def test_an_empty_journal_is_unreadable_not_a_traceback(projection, monkeypatch):
    """Review Focus: `read()` returns `[]`, and `replay` inside `diverging`
    raises `JournalError` -- the try must cover `diverging` too."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _journal_path().write_text("", encoding="utf-8")

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == {
        "checked": False,
        "reason": "journal unreadable: journal contains no run_upsert line",
        "mismatches": [],
    }


def test_a_journal_payload_that_fails_validation_is_unreadable(projection, monkeypatch):
    """Review Focus: the envelope is valid, the run payload is not, so
    `diverging` raises a pydantic `ValidationError`."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    path = _journal_path()
    first, *rest = path.read_text(encoding="utf-8").splitlines(keepends=True)
    line = json.loads(first)
    assert line["event"] == "run_upsert"
    line["payload"]["status"] = "not-a-status"
    path.write_text(json.dumps(line) + "\n" + "".join(rest), encoding="utf-8")

    data = _status_data(projection, CONTROL_RUN_ID)

    integrity = data["integrity"]
    assert integrity["checked"] is False
    assert integrity["reason"].startswith("journal unreadable: ")
    assert "validation error" in integrity["reason"]
    assert integrity["mismatches"] == []


def test_an_unknown_journal_event_kind_is_skipped_by_the_check(projection, monkeypatch):
    """Review Focus: a line a newer `am` wrote is not divergence and does not raise."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    with _journal_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"seq": 9999, "event": "from_the_future"}) + "\n")

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == CLEAN_INTEGRITY
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "integrity or no_journal or torn_final or non_json_line or empty_journal or fails_validation or unknown_journal_event or hand_edited_to_cancelled" -v`
Expected: all nine FAIL — `KeyError: 'integrity'` for the `_status_data` tests, and an `assert ... == ...` stdout mismatch for `test_status_of_a_clean_run_is_checked_and_otherwise_unchanged`.

- [ ] **Step 4: Add the imports**

In `src/agent_manager/cli.py`, change line 28:

```python
from dataclasses import asdict, dataclass
```

and directly after line 33 (`import typer`) add:

```python
from pydantic import ValidationError
```

- [ ] **Step 5: Add `integrity_view` after `control_view`**

Insert in `src/agent_manager/cli.py` immediately after the end of `control_view` (after line 289, before `def status_payload(`):

```python
def integrity_view(
    run_id: str,
    run: models.Run,
    lease: store_module.LeaseRow | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """The `integrity` key of `status`: does the journal agree with `run`?

    Journal/DB divergence spec §3.3, §3.7. Always the three keys `checked`,
    `reason` and `mismatches`, and never an error: a journal that cannot be
    compared is `checked: false` with the reason why, so `status` keeps its
    exit code. Report-only (§3.4): nothing is written and no control request
    is filed.

    The journal is opened through `Journal._for_reading`, never
    `Journal(run_id)`, whose `paths.run_dir` would create a directory for a
    run that has none, and a torn last line is an append in flight and is
    skipped, as in `_journal_events`. The one `try` covers `diverging` as well
    as `read`, because `replay` inside it raises `JournalError` or a pydantic
    `ValidationError` of its own. Mismatches are `store.diverging`'s, in its
    tree-walk order: there is one definition of divergence.
    """
    try:
        lines = store_module.Journal._for_reading(run_id).read(ignore_torn_tail=True)
        found = store_module.diverging(lines, run)
    except store_module.MissingJournalError:
        return {"checked": False, "reason": "no journal", "mismatches": []}
    except (store_module.JournalError, ValidationError) as error:
        return {
            "checked": False,
            "reason": f"journal unreadable: {error}",
            "mismatches": [],
        }
    return {
        "checked": True,
        "reason": None,
        "mismatches": [asdict(mismatch) for mismatch in found],
    }
```

- [ ] **Step 6: Wire it into `status_for`**

In `src/agent_manager/cli.py`, replace the `status_for` docstring and its final `return status_payload(run, state)` (lines 1431-1439 and 1472). The docstring becomes:

```python
    """The §9 tree and §10 table of one run of this project.

    Read-only: no `record_*` is called, and the connection is closed on every
    path including the refusals, the way `run_card` closes its store. The default
    run id comes from `store_module.latest_run_id`, which is the head of the very
    listing `runs` prints, so the two commands cannot disagree about which run is
    the most recent one. The lease and every control request are read on the
    same connection and rendered by `control_view`, still without a write.
    The `integrity` key compares the run's journal with the loaded tree through
    `integrity_view`, which reads the journal and writes nothing either.
    """
```

and the tail of the `try` block, replacing `return status_payload(run, state)`, becomes:

```python
        payload = status_payload(run, state)
        payload["integrity"] = integrity_view(wanted, run, lease, now=now)
        return payload
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "integrity or no_journal or torn_final or non_json_line or empty_journal or fails_validation or unknown_journal_event or hand_edited_to_cancelled" -v`
Expected: all nine PASS.

- [ ] **Step 8: Run the existing status tests to verify nothing else moved**

Run: `uv run pytest tests/test_cli.py -k status -v`
Expected: PASS (the existing tests read only `run`, `rows`, `stories` and `control`, which are unchanged).

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: am status reports journal/projection integrity"
```

---

### Task 2: A live lease wins over the check

**Files:**
- Modify: `src/agent_manager/cli.py` (`integrity_view`, added in Task 1 right after `control_view`)
- Test: `tests/test_cli.py` (append at end of file)

**Interfaces:**
- Consumes: `cli.integrity_view(run_id, run, lease, *, now)` from Task 1; `control.lease_is_live(lease: store_module.LeaseRow, *, now: datetime) -> bool` (src/agent_manager/control.py:65); test helpers from Task 1 (`_hand_edit_run_status`, `_status_data`, `_journal_path`, `RUN_CANCELLED_BY_HAND`).
- Produces: `integrity_view` returns `{"checked": False, "reason": "lease is live", "mismatches": []}` whenever `lease is not None and control.lease_is_live(lease, now=now)`, without opening the journal. Signature unchanged.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
@pytest.mark.parametrize(
    "heartbeat_at, integrity",
    [
        (
            CONTROL_NOW - timedelta(seconds=5),
            {"checked": False, "reason": "lease is live", "mismatches": []},
        ),
        (
            CONTROL_NOW - timedelta(seconds=31),
            {"checked": True, "reason": None, "mismatches": [RUN_CANCELLED_BY_HAND]},
        ),
    ],
    ids=["live", "stale"],
)
def test_a_live_lease_is_not_checked_and_a_stale_one_is(
    projection, monkeypatch, heartbeat_at, integrity
):
    """Spec test 4 (§3.5): writes in flight are noise, not divergence, even
    against a hand-edited projection; a dead lease's run is checked."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _hand_edit_run_status(projection, "cancelled")
    _plant_lease(projection, heartbeat_at=heartbeat_at)

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == integrity


def test_a_live_lease_never_opens_the_journal(projection, monkeypatch):
    """Review Focus: the live-lease rule comes first, so even a corrupt
    journal reads as `lease is live`, and `diverging` is never called."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    with _journal_path().open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    _plant_lease(projection, heartbeat_at=CONTROL_NOW - timedelta(seconds=5))

    def forbidden(*args, **kwargs):
        raise AssertionError("a live run's journal must not be compared")

    monkeypatch.setattr(store_module, "diverging", forbidden)

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == {"checked": False, "reason": "lease is live", "mismatches": []}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "live_lease_is_not_checked or live_lease_never_opens" -v`
Expected: `test_a_live_lease_is_not_checked_and_a_stale_one_is[live]` FAILS (integrity is `checked: True` with the foreign mismatch), `test_a_live_lease_never_opens_the_journal` FAILS (reason is `"journal unreadable: ..."`), and `[stale]` PASSES.

- [ ] **Step 3: Add the live-lease rule to `integrity_view`**

In `src/agent_manager/cli.py`, inside `integrity_view`, insert before the `try:` line:

```python
    if lease is not None and control.lease_is_live(lease, now=now):
        return {"checked": False, "reason": "lease is live", "mismatches": []}
```

and extend its docstring by adding this paragraph after the first one:

```python
    A live lease (§3.5) is `checked: false, reason: "lease is live"` before the
    journal is opened: a running process's writes in flight are noise, not
    divergence, even against a hand-edited projection. A dead lease, or none,
    is checked.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "live_lease_is_not_checked or live_lease_never_opens" -v`
Expected: all three PASS.

- [ ] **Step 5: Run the full default suite**

Run: `uv run pytest`
Expected: PASS, with no new failures, within the documented ≤90s default-tier budget. There is no typecheck or lint command to run.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: am status skips the integrity check while the lease is live"
```

---

## Self-Review

- Spec coverage: Scope items `integrity_view` + `status_for` wiring + docstring sentence → Task 1 Steps 5-6. Decision order step 1 (live lease, `None` guard, journal not read) → Task 2. Step 2 (`_for_reading`, `ignore_torn_tail`, `MissingJournalError`, `JournalError`/`ValidationError`, one `try` around `read` and `diverging`) → Task 1 Step 5, pinned by the no-journal, corrupt, empty-journal and validation tests. Step 3 (`asdict`, tree order, stale or no lease checked) → Task 1 Step 5 and Task 2 `[stale]`. Report-only → test 3 plus test 2's `_controls` check. Spec tests 1-6 → Task 1 (1, 2, 3, 5, 6 both halves) and Task 2 (4). Out-of-scope items are untouched. The spec's XDG-mismatch case cannot be staged separately, because the projection DB also lives under `paths.data_dir()`. It is the same `MissingJournalError` path as test 5.
- Placeholder scan: no TBD/TODO. Every code step has full code.
- Type consistency: `integrity_view(run_id, run, lease, *, now)` is identical in Tasks 1 and 2 and at the `status_for` call site. Helper names (`_journal_path`, `_hand_edit_run_status`, `_status_data`, `_projection_snapshot`, `RUN_CANCELLED_BY_HAND`, `CLEAN_INTEGRITY`) are defined in Task 1 and reused unchanged in Task 2.
- Review Focus: all five lines have a test in their owning task.
