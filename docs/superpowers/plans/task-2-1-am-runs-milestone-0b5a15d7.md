<!-- task-pipeline: validated -->
# 2.1 `am runs`: `milestone_id` and `card_id` (card 0b5a15d7)

Parent story: e187d6f8 "Richer am runs" (milestone fbf624d6). Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md`, §1 "Richer `am runs`". This subtask delivers only the first two fields of that section. `lease` is 6bf47e74 (2.2) and `progress` is 882b212b (2.3); both are out of scope here.

## Scope

- `store.RunSummary` (`src/agent_manager/store.py`, stays `extra="forbid"`) gains two optional fields, both defaulting to `None`:
  - `milestone_id: str | None`: the value already in the `runs.milestone_id` column. It is null on a `--card` run and on old rows written before the column existed (the `_ADDED_COLUMNS` migration leaves them NULL).
  - `card_id: str | None`: the subtask id of a `--card` run, else null.
- `store.list_runs` selects `milestone_id` and derives `card_id`. Ordering (`started_at DESC, id DESC`) and the existing seven fields are unchanged.
- `card_id` is derived, not stored. A `--card` run is workflow `"task"` and records exactly one story and one subtask row in `subtasks` (PK `run_id, story_id, card_id`). `card_id` is that row's `card_id` when `workflow == "task"`, else null. The natural form is a correlated subquery or LEFT JOIN inside `list_runs`'s SQL. No new column, no migration, no change to `record_run` / `record_subtask` / `load_run`.
- `cli.runs_for` needs no code change: `summary.model_dump()` carries the new keys into `data.runs[]` automatically. The envelope stays `{"ok": true, "data": {"runs": [...]}}`.
- `STATUS_HEADER` and `am status` do not change. The comment near `cli.py:203` that says the status header and `runs`' entries are "the same seven names" becomes inaccurate; touch up the comment only (e.g. say `runs` entries are a superset), not the header.
- README (small docs piece, not core): where `am runs` is described, list the `data.runs[]` keys including `milestone_id` and `card_id` with their null semantics, and add the sentence that consumers ignore unknown keys (new keys are additive). The README has no dedicated `am runs` section today, so a short one is acceptable.

## Observable behaviour

- Milestone run (workflow `"milestone"`, `milestone_id` set): `milestone_id` is the recorded id, `card_id` is null.
- `--card` run (workflow `"task"`, one subtask row): `milestone_id` is null, `card_id` is the subtask's card id.
- `"task"` run with no subtask row yet (recorded before its subtask was written): `card_id` is null, no error.
- Old fixture (runs table created without `milestone_id`, migrated by `open_db`): row still listed, `milestone_id` null, `card_id` derived as above.
- Every pre-existing key in `data.runs[]` keeps its name and value. Nothing removed or renamed; journal stays schema 1.

## Error paths

None new. `list_runs` on an empty projection still returns `[]`; `am runs` on an empty history still returns `{"runs": []}`. A projection row that fails `RunSummary` validation still fails loudly as today.

## Tests (TDD: written first, failing, then made green; `uv run pytest` green at the end)

All of these use SQLite in `tmp_path` (and the `CliRunner` for the CLI ones) with no subprocess, so per the CLAUDE.md "Test tiers" placement rule (and design spec §14) they are **unmarked `unit`** tests, like the neighbouring `list_runs` / `runs` tests. None is marked `git`, `brd`, `e2e_fake`, `soak` or `e2e`.

`tests/test_store.py` (unit):
1. A milestone run listed by `list_runs` has `milestone_id` equal to the recorded id and `card_id is None`.
2. A `task` run with one story and one subtask has `milestone_id is None` and `card_id` equal to the subtask's card id.
3. A `task` run with no subtask row has `card_id is None`. The existing `_record_summary` helper writes the run row only, but via `_run(...)`, whose workflow is `"milestone"` and `milestone_id` is None; so this test (and tests 1 and 2) must `model_copy(update={"workflow": "task"})` / `update={"milestone_id": ...}` on the run (or add keyword parameters to the helper) rather than rely on its defaults. Test 1 needs a milestone run with `milestone_id` set, which `_run` does not give by default.
4. Old fixture: build a `runs` table without `milestone_id` the way `test_a_runs_table_from_before_milestone_id_gains_the_column_and_keeps_its_row` does, open it with `open_db`, and `list_runs` returns the row with `milestone_id is None` and `card_id is None`.
5. Shape pin: `RunSummary.model_fields` (or a dumped summary's keys) equals exactly the seven old names plus `milestone_id` and `card_id`.

`tests/test_cli.py` (unit, `CliRunner`):
6. `am runs` on a `--card`-shaped run (the existing `_record` helper) has `data.runs[0]["card_id"]` equal to the subtask id and `data.runs[0]["milestone_id"]` null.
7. `am runs` on a milestone run has `milestone_id` set and `card_id` null. The existing `_record` helper hardcodes `workflow="task"` and no `milestone_id`, so extend it with optional `workflow` / `milestone_id` keyword parameters (defaults unchanged) or write the milestone run directly with `Store.record_run`.
8. Shape test: the key set of each `data.runs[]` entry is exactly the old seven plus `milestone_id` and `card_id`, and the envelope is `{"ok": true, "data": {"runs": [...]}}`.

Existing `list_runs` / `runs` tests and the replay test that uses `list_runs` (around `test_store.py:2937`) must still pass. Adjust them only if they assert an exact key set.

---

# 2.1 `am runs`: `milestone_id` and `card_id` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every entry of `am runs`'s `data.runs[]` (and every `store.RunSummary`) carries `milestone_id` (from `runs.milestone_id`) and `card_id` (the single subtask of a `task` run, else null).

**Architecture:** `store.RunSummary` gains two optional fields defaulting to `None`. `store.list_runs` selects the existing `runs.milestone_id` column and derives `card_id` with a correlated subquery over `subtasks`, guarded by `CASE WHEN runs.workflow = 'task'`. `cli.runs_for` is unchanged: its `model_dump()` carries the new keys. No schema change, no migration, no journal change.

**Tech Stack:** Python, SQLite (`sqlite3`), Pydantic v2, Typer `CliRunner`, pytest, `uv`.

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-2-1-am-runs-milestone-0b5a15d7/docs/superpowers/specs/task-2-1-am-runs-milestone-0b5a15d7-design.md` (reproduced verbatim above). Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §1.

All paths below are relative to the worktree root `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-2-1-am-runs-milestone-0b5a15d7`. Run every command from that directory.

## Global Constraints

- `store.RunSummary` keeps `model_config = ConfigDict(extra="forbid")`.
- New fields: `milestone_id: str | None = None` and `card_id: str | None = None`, both defaulting to `None`.
- New JSON keys are additive only: no existing `data.runs[]` key is removed or renamed; the journal stays schema 1.
- CLI output stays the `{"ok": true, "data": {"runs": [...]}}` envelope.
- `card_id` is derived in `list_runs`'s SQL, only when `workflow == "task"`; no new column, no `_ADDED_COLUMNS` entry, no change to `record_run` / `record_subtask` / `load_run`.
- `list_runs` ordering stays `ORDER BY started_at DESC, id DESC`.
- `cli.RUN_IDENTITY` (the "status header" tuple the spec calls `STATUS_HEADER`) and `am status` output do not change; only the docstring comment under it is reworded.
- All new tests are unmarked (`unit` tier): SQLite in `tmp_path` and `CliRunner`, no subprocess. They live in `tests/test_store.py` and `tests/test_cli.py`, beside the existing `list_runs` / `runs` tests.
- Out of scope: `lease` (2.2), `progress` (2.3), `--detach`, `logs --follow`, `--from-now`, `--board` docs.
- `uv run pytest` must be green at the end.

## Review Focus

- A milestone run records many `subtasks` rows, but its `card_id` must still be null: the `workflow = 'task'` guard is the only thing preventing a milestone run from reporting one of its subtasks. Pinned by `test_list_runs_never_gives_a_milestone_run_a_card_id_even_with_subtask_rows` (Task 1) and by the CLI milestone test, whose `_record` writes a subtask row.
- Two `task` runs in one project must each report their own card; a subquery not correlated on `run_id` would leak one run's card into the other. Pinned by `test_list_runs_gives_each_task_run_its_own_card_id` (Task 1).
- A `task` row written before `milestone_id` existed (old fixture, workflow `task`, with a subtask row) must still derive its `card_id` after `open_db` migrates the table. Pinned by `test_an_old_task_run_lists_its_card_id_after_the_milestone_id_migration` (Task 1).
- A `task` run that somehow carries more than one subtask row must not error (a scalar subquery returning two rows is fine in SQLite, but the answer must be deterministic): the lowest `position`, then lowest `card_id`, wins. Pinned by `test_list_runs_picks_the_first_subtask_when_a_task_run_has_several` (Task 1).
- `am runs --pretty` and the compact form must both carry the two new keys as explicit `null`s rather than omitting them, since consumers distinguish "absent" from "null". Pinned by `test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id` (Task 1), which checks both renderings.

---

### Task 1: `RunSummary` gains `milestone_id` and `card_id`, and `am runs` shows them

**Files:**
- Modify: `src/agent_manager/store.py:588-624` (`RunSummary` and `list_runs`)
- Test: `tests/test_store.py` (modify `_record_summary` at lines 1264-1270; add tests after `test_list_runs_breaks_a_started_at_tie_with_the_run_id`, which ends at line 1329; add old-fixture tests after `test_a_runs_table_from_before_milestone_id_gains_the_column_and_keeps_its_row`, which ends at line 2190)
- Test: `tests/test_cli.py` (modify `_record` at lines 3925-3986; add tests after `test_runs_agrees_with_status_about_the_most_recent_run`, which ends at line 4174)

**Interfaces:**
- Consumes: `store.Store.open(root, run_id)`, `Store.record_run(models.Run)`, `Store.record_story(models.StoryRun)`, `Store.record_subtask(story_id: str, models.SubtaskRun)`, `store.open_db(root)`, the existing test helpers `_run`, `_story`, `_subtask`, `_LEGACY_RUNS`, `MILESTONE_ID`, `RUN_ID` in `tests/test_store.py` and `projection`, `runner`, `RECORDED_AT` in `tests/test_cli.py`.
- Produces:
  - `store.RunSummary` with fields, in order: `id: str`, `workflow: str`, `repo_dir: Path`, `base_branch: str`, `branch_prefix: str`, `status: models.Status`, `started_at: datetime | None = None`, `milestone_id: str | None = None`, `card_id: str | None = None`.
  - `store.list_runs(conn: sqlite3.Connection) -> list[RunSummary]` (signature unchanged).
  - Test helper `_record_summary(root: Path, run_id: str, started_at: datetime | None, *, workflow: str = "milestone", milestone_id: str | None = None, subtask_cards: tuple[str, ...] = ()) -> None`.
  - Test helper `_record(root, run_id, *, started_at, status="done", with_phases=True, workflow="task", milestone_id=None) -> None` in `tests/test_cli.py`.

- [ ] **Step 1: Extend `_record_summary` in `tests/test_store.py`**

Replace lines 1264-1270:

```python
def _record_summary(root: Path, run_id: str, started_at: datetime | None) -> None:
    """One run row in `root`'s projection, with nothing below it."""
    opened = store.Store.open(root, run_id)
    try:
        opened.record_run(_run(root, run_id).model_copy(update={"started_at": started_at}))
    finally:
        opened.close()
```

with:

```python
def _record_summary(
    root: Path,
    run_id: str,
    started_at: datetime | None,
    *,
    workflow: str = "milestone",
    milestone_id: str | None = None,
    subtask_cards: tuple[str, ...] = (),
) -> None:
    """One run row in `root`'s projection, plus, when `subtask_cards` is given,
    one story holding those subtasks in that order. The defaults write the run
    row alone, as every older caller expects."""
    opened = store.Store.open(root, run_id)
    try:
        opened.record_run(
            _run(root, run_id).model_copy(
                update={
                    "started_at": started_at,
                    "workflow": workflow,
                    "milestone_id": milestone_id,
                }
            )
        )
        if subtask_cards:
            opened.record_story(_story())
            for card in subtask_cards:
                opened.record_subtask(_story().card_id, _subtask(card))
    finally:
        opened.close()
```

- [ ] **Step 2: Write the failing `list_runs` tests in `tests/test_store.py`**

Insert after `test_list_runs_breaks_a_started_at_tie_with_the_run_id` (ends line 1329), before `test_latest_run_id_is_the_newest_recorded_run`. `MILESTONE_ID` is a module-level constant defined further down the file (line 2088); it is resolved at call time, so using it here is fine.

```python
SUMMARY_KEYS = {
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "milestone_id",
    "card_id",
}
"""The seven names `am runs` always had, plus the two this card adds."""


def _listed(root: Path) -> list[store.RunSummary]:
    conn = store.open_db(root)
    try:
        return store.list_runs(conn)
    finally:
        conn.close()


def test_list_runs_gives_a_milestone_run_its_milestone_id_and_no_card_id(repo):
    _record_summary(repo, "run-m", None, milestone_id=MILESTONE_ID)

    [summary] = _listed(repo)

    assert summary.milestone_id == MILESTONE_ID
    assert summary.card_id is None


def test_list_runs_gives_a_card_run_its_card_id_and_no_milestone_id(repo):
    _record_summary(repo, "run-t", None, workflow="task", subtask_cards=("ef248597",))

    [summary] = _listed(repo)

    assert summary.workflow == "task"
    assert summary.milestone_id is None
    assert summary.card_id == "ef248597"


def test_list_runs_gives_a_task_run_with_no_subtask_row_no_card_id(repo):
    """A `--card` run's row is written before its subtask row, so a reader can
    see it in between; that is not an error."""
    _record_summary(repo, "run-t", None, workflow="task")

    [summary] = _listed(repo)

    assert summary.card_id is None
    assert summary.milestone_id is None


def test_list_runs_never_gives_a_milestone_run_a_card_id_even_with_subtask_rows(repo):
    _record_summary(
        repo,
        "run-m",
        None,
        milestone_id=MILESTONE_ID,
        subtask_cards=("ef248597", "1535b285"),
    )

    [summary] = _listed(repo)

    assert summary.milestone_id == MILESTONE_ID
    assert summary.card_id is None


def test_list_runs_gives_each_task_run_its_own_card_id(repo):
    _record_summary(
        repo,
        "run-a",
        datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        workflow="task",
        subtask_cards=("aaaa1111",),
    )
    _record_summary(
        repo,
        "run-b",
        datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc),
        workflow="task",
        subtask_cards=("bbbb2222",),
    )

    summaries = _listed(repo)

    assert [(s.id, s.card_id) for s in summaries] == [
        ("run-b", "bbbb2222"),
        ("run-a", "aaaa1111"),
    ]


def test_list_runs_picks_the_first_subtask_when_a_task_run_has_several(repo):
    """A `--card` run records one subtask, but a projection holding two must
    still list the run, with a stable answer: the lowest position wins."""
    _record_summary(
        repo, "run-t", None, workflow="task", subtask_cards=("zzzz9999", "aaaa1111")
    )

    [summary] = _listed(repo)

    assert summary.card_id == "zzzz9999"


def test_run_summary_fields_are_the_old_seven_plus_milestone_id_and_card_id():
    assert set(store.RunSummary.model_fields) == SUMMARY_KEYS
    assert store.RunSummary.model_config["extra"] == "forbid"
    assert store.RunSummary.model_fields["milestone_id"].default is None
    assert store.RunSummary.model_fields["card_id"].default is None


def test_a_listed_run_dumps_exactly_the_summary_keys(repo):
    _record_summary(repo, "run-t", None, workflow="task", subtask_cards=("ef248597",))

    [summary] = _listed(repo)

    assert set(summary.model_dump()) == SUMMARY_KEYS
```

- [ ] **Step 3: Write the failing old-fixture tests in `tests/test_store.py`**

Insert after `test_a_runs_table_from_before_milestone_id_gains_the_column_and_keeps_its_row` (ends line 2190), before `test_a_runs_milestone_id_survives_a_rebuild_from_the_journal`:

```python
def _migrated_legacy(repo: Path, extra_sql: str = "") -> list[store.RunSummary]:
    """`_LEGACY_RUNS` (plus `extra_sql`) written into a fresh database, which a
    second `open_db` then migrates; the migrated projection's listing."""
    fresh = store.open_db(repo)
    try:
        fresh.executescript(_LEGACY_RUNS + extra_sql)
        fresh.commit()
    finally:
        fresh.close()

    migrated = store.open_db(repo)
    try:
        return store.list_runs(migrated)
    finally:
        migrated.close()


def test_an_old_runs_row_lists_with_no_milestone_id_and_no_card_id(repo):
    [summary] = _migrated_legacy(repo)

    assert (summary.id, summary.workflow, summary.status) == (
        RUN_ID,
        "milestone",
        "escalated",
    )
    assert summary.milestone_id is None
    assert summary.card_id is None


def test_an_old_task_run_lists_its_card_id_after_the_milestone_id_migration(repo):
    [summary] = _migrated_legacy(
        repo,
        """
        UPDATE runs SET workflow = 'task' WHERE id = 'run-2026-09-23-01';
        INSERT INTO subtasks (run_id, story_id, card_id, branch, base_branch,
                              status, worktree_path, position)
        VALUES ('run-2026-09-23-01', '8831189b', 'ef248597', 'm1/task-ef248597',
                'main', 'escalated', NULL, 0);
        """,
    )

    assert summary.workflow == "task"
    assert summary.milestone_id is None
    assert summary.card_id == "ef248597"
```

- [ ] **Step 4: Extend `_record` in `tests/test_cli.py`**

Replace the signature and the `record_run` call of `_record` (lines 3925-3947):

```python
def _record(
    root: Path,
    run_id: str,
    *,
    started_at: datetime,
    status: str = "done",
    with_phases: bool = True,
) -> None:
    """One run -- story, subtask, and optionally two phases and two attempts --
    in `root`'s projection, written the only way this program writes rows."""
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status=status,
                started_at=started_at,
            )
        )
```

with:

```python
def _record(
    root: Path,
    run_id: str,
    *,
    started_at: datetime,
    status: str = "done",
    with_phases: bool = True,
    workflow: str = "task",
    milestone_id: str | None = None,
) -> None:
    """One run -- story, subtask, and optionally two phases and two attempts --
    in `root`'s projection, written the only way this program writes rows. The
    defaults are a `--card`-shaped run; pass `workflow="milestone"` and a
    `milestone_id` for a milestone-shaped one."""
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status=status,
                started_at=started_at,
                milestone_id=milestone_id,
            )
        )
```

The rest of `_record` (story, subtask `card-1`, phases, attempts) stays as it is.

- [ ] **Step 5: Write the failing `am runs` tests in `tests/test_cli.py`**

Insert after `test_runs_agrees_with_status_about_the_most_recent_run` (ends line 4174), before `test_a_missing_repo_dir_is_an_envelope_for_both_read_commands`:

```python
RUNS_ENTRY_KEYS = {
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "milestone_id",
    "card_id",
}
"""Every `data.runs[]` entry: the seven names `am runs` always had, plus the
two that card 0b5a15d7 added."""

RUNS_MILESTONE_ID = "9c44c2fb-0000-4000-8000-000000000000"


def test_runs_shows_a_card_runs_card_id_and_a_null_milestone_id(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["card_id"] == "card-1"
    assert entry["milestone_id"] is None


def test_runs_shows_a_milestone_runs_milestone_id_and_a_null_card_id(projection):
    """`_record` writes a subtask row under the milestone run too, so this also
    pins that a milestone run never reports one of its subtasks as `card_id`."""
    _record(
        projection,
        "20260923T090000Z-cbe34d00",
        started_at=RECORDED_AT,
        workflow="milestone",
        milestone_id=RUNS_MILESTONE_ID,
    )

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["workflow"] == "milestone"
    assert entry["milestone_id"] == RUNS_MILESTONE_ID
    assert entry["card_id"] is None


def test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id(projection):
    _record(
        projection,
        "20260921T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
    )
    _record(
        projection,
        "20260923T090000Z-cbe34d00",
        started_at=RECORDED_AT,
        workflow="milestone",
        milestone_id=RUNS_MILESTONE_ID,
    )
    argv = ["runs", "--repo-dir", str(projection)]

    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])

    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for envelope in (json.loads(plain.stdout), json.loads(pretty.stdout)):
        assert set(envelope) == {"ok", "data"}
        assert envelope["ok"] is True
        assert set(envelope["data"]) == {"runs"}
        assert len(envelope["data"]["runs"]) == 2
        for entry in envelope["data"]["runs"]:
            assert set(entry) == RUNS_ENTRY_KEYS
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_store.py tests/test_cli.py -k "milestone_id or card_id or summary_keys or summary_fields or own_card_id or several or old_runs_row or old_task_run" -v`

Expected: FAIL. The `list_runs` tests fail with `AttributeError: 'RunSummary' object has no attribute 'milestone_id'` (or `card_id`); `test_run_summary_fields_are_the_old_seven_plus_milestone_id_and_card_id` and `test_a_listed_run_dumps_exactly_the_summary_keys` fail on the set comparison (seven keys, not nine); the three CLI tests fail with `KeyError: 'card_id'` or the key-set assertion. Pre-existing tests in the selection (`test_a_runs_milestone_id_round_trips_through_load_run_and_is_overwritten`, `test_a_runs_table_from_before_milestone_id_gains_the_column_and_keeps_its_row`, `test_a_runs_milestone_id_survives_a_rebuild_from_the_journal`, `test_a_fresh_runs_table_carries_milestone_id_as_its_last_column`) still PASS. If any new test passes here, stop: it is not testing the change.

- [ ] **Step 7: Add the two fields to `RunSummary`**

In `src/agent_manager/store.py`, replace the `RunSummary` class (lines 588-606) with:

```python
class RunSummary(BaseModel):
    """One row of the shared `runs` table, without the tree hanging off it.

    A `models.Run` would be a lie here: its `stories` list would always be empty
    because `runs` is the only table read for it. The fields are the run's
    identity and nothing else, and they go through pydantic for the same reason
    `load_run` does -- a projection that drifted from `models` must fail loudly
    rather than print half a history.

    `milestone_id` is the `runs` column: null on a `--card` run and on a row
    written before the column existed. `card_id` is not stored anywhere on the
    run row: it is the single subtask a `task` (`--card`) run records, and null
    for any other workflow or before that subtask row is written. Both default
    to `None`, so they are additive: no older key changed.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    workflow: str
    repo_dir: Path
    base_branch: str
    branch_prefix: str
    status: models.Status
    started_at: datetime | None = None
    milestone_id: str | None = None
    card_id: str | None = None
```

- [ ] **Step 8: Select `milestone_id` and derive `card_id` in `list_runs`**

In `src/agent_manager/store.py`, replace `list_runs` (lines 609-624) with:

```python
def list_runs(conn: sqlite3.Connection) -> list[RunSummary]:
    """Every run recorded in this project's projection, newest first.

    Takes a connection rather than a root so one caller can list the history and
    then load a run's tree over the same connection, and close it once. The
    connection comes from `open_db(root)`; there is no second database.

    `started_at DESC` puts a NULL start time last (SQLite orders NULL below every
    value, so descending sends it to the end) and the id breaks a tie, which run
    ids minted at second resolution really do produce.

    `card_id` is derived, not stored: a `task` run (`am run --card`) records one
    story and one subtask, and that subtask's card is the run's card. Any other
    workflow -- a milestone run records many subtasks -- gets NULL. Should a
    `task` run ever hold several subtask rows, the lowest `position`, then the
    lowest card id, wins, so the answer is stable rather than an error.
    """
    rows = conn.execute(
        "SELECT runs.id, runs.workflow, runs.repo_dir, runs.base_branch,"
        " runs.branch_prefix, runs.status, runs.started_at, runs.milestone_id,"
        " CASE WHEN runs.workflow = 'task' THEN ("
        "   SELECT subtasks.card_id FROM subtasks"
        "    WHERE subtasks.run_id = runs.id"
        "    ORDER BY subtasks.position, subtasks.card_id LIMIT 1"
        " ) END AS card_id"
        " FROM runs ORDER BY runs.started_at DESC, runs.id DESC"
    ).fetchall()
    return [RunSummary.model_validate(dict(row)) for row in rows]
```

`dict(row)` uses the result column names; SQLite names a bare `runs.id` column `id` (not `runs.id`), so the keys match the field names. `card_id` is named explicitly by `AS card_id`.

- [ ] **Step 9: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_store.py tests/test_cli.py -k "milestone_id or card_id or summary_keys or summary_fields or own_card_id or several or old_runs_row or old_task_run" -v`

Expected: PASS, every selected test.

- [ ] **Step 10: Run the neighbouring `list_runs` / `runs` / replay tests**

Run: `uv run pytest tests/test_store.py tests/test_cli.py -k "list_runs or latest_run_id or runs or rebuild or cancelled" -v`

Expected: PASS, including `test_list_runs_returns_this_projects_runs_newest_first`, `test_list_runs_puts_a_run_with_no_start_time_last`, `test_list_runs_breaks_a_started_at_tie_with_the_run_id`, `test_runs_lists_the_projects_history_newest_first`, `test_runs_agrees_with_status_about_the_most_recent_run`, and the replay test around `tests/test_store.py:2937` that asserts `[(summary.id, summary.status) ...] == [(RUN_ID, "cancelled")]`.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py tests/test_cli.py
git commit -m "feat(runs): add milestone_id and card_id to RunSummary and am runs"
```

---

### Task 2: Reword the `RUN_IDENTITY` comment and document `am runs` in the README

No behaviour change, so no new test: the key set this documents is already pinned by `test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id` (Task 1).

**Files:**
- Modify: `src/agent_manager/cli.py:202-204` (the docstring under `RUN_IDENTITY`)
- Modify: `README.md` (new `### Listing runs` section inserted before `### Watching a run`, which is at line 429)

**Interfaces:**
- Consumes: the `data.runs[]` key set produced by Task 1 (`id`, `workflow`, `repo_dir`, `base_branch`, `branch_prefix`, `status`, `started_at`, `milestone_id`, `card_id`).
- Produces: nothing code depends on.

- [ ] **Step 1: Reword the comment under `RUN_IDENTITY`**

In `src/agent_manager/cli.py`, replace lines 202-204:

```python
"""The run's own fields, without `config` and without the tree below it. §10's
`status` header and `runs`' entries are the same seven names, so the two
commands describe a run the same way."""
```

with:

```python
"""The run's own fields, without `config` and without the tree below it. §10's
`status` header is these seven names. Each `runs` entry carries the same seven,
plus `milestone_id` and `card_id` (a superset), so the two commands still
describe a run's identity the same way."""
```

Do not change the tuple itself.

- [ ] **Step 2: Add the `### Listing runs` section to `README.md`**

Insert immediately before the line `### Watching a run` (line 429), leaving one blank line after the inserted block:

````markdown
### Listing runs

`am runs` lists this repository's runs from its projection, newest first. Like `am status`, it takes no lease, no claim and no lock.

```bash
am runs --repo-dir . --pretty
```

`data.runs` is a list with one object per run. Each object has these keys:

- `id`, `workflow` (`milestone` or `task`), `repo_dir`, `base_branch`, `branch_prefix`, `status`, `started_at` (`null` if never recorded).
- `milestone_id`: the full id of the milestone card a milestone run drives. It is `null` on a `--card` run, and on a run recorded by an `am` too old to store it.
- `card_id`: the subtask card an `am run --card` run drives. It is `null` on a milestone run, and on a `--card` run whose subtask has not been recorded yet.

New keys are additive: a newer `am` may add keys to these objects, but never removes or renames one. Consumers should ignore any key they do not recognize.

````

- [ ] **Step 3: Run the CLI tests to confirm nothing moved**

Run: `uv run pytest tests/test_cli.py -v -k "runs or status"`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/agent_manager/cli.py README.md
git commit -m "docs(runs): document milestone_id and card_id in am runs"
```

---

### Task 3: Full verification

**Files:** none modified.

**Interfaces:** none.

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest`

Expected: every test in the default `unit` + `git` tiers passes; no new test is skipped or deselected (none carries a marker).

- [ ] **Step 2: Confirm scope**

Run: `git diff --stat ami/task-1-1-watch-from-now-db129e6a...HEAD`

Expected: only `src/agent_manager/store.py`, `src/agent_manager/cli.py`, `tests/test_store.py`, `tests/test_cli.py`, `README.md`. No change to `_SCHEMA`, `_ADDED_COLUMNS`, `record_run`, `record_subtask`, `load_run`, `runs_for`, the `RUN_IDENTITY` tuple, or the journal; no `lease` or `progress` keys anywhere.
