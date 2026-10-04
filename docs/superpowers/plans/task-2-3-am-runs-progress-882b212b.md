<!-- task-pipeline: validated -->
# Subtask 2.3 — `am runs`: `progress` (card 882b212b)

Narrows `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §1 ("Richer `am runs`") to its last field, `progress`. Story e187d6f8 "Richer am runs". Siblings 2.1 (`milestone_id`, `card_id`) and 2.2 (`lease`) are done, and this worktree already contains their work (`RunLease`, `RunSummary.milestone_id/card_id/lease`, `cli._lease_fields`, `cli.runs_for`, README "Listing runs"). Build on that tip, and leave those fields, their SQL and their tests as they are.

## Scope

- `src/agent_manager/store.py`: three new pydantic models, all `ConfigDict(extra="forbid")`:
  - `ProgressCount { done: int, total: int }`
  - `ProgressCurrent { card: str, phase: str, attempt: int | None }`
  - `RunProgress { stories: ProgressCount, subtasks: ProgressCount, current: ProgressCurrent | None }`
- `RunSummary` gains `progress: RunProgress | None = None`, placed after `lease`. The default keeps the field additive for anyone who builds a `RunSummary` by hand. Update the class docstring.
- `store.list_runs` fills `progress` for every run, using read-only `SELECT`s on the same connection over the `stories`, `subtasks`, `phases` and `attempts` rows for that `run_id`. It needs no `control` import, because nothing in it depends on liveness. `list_runs` never returns `progress: None`. `latest_run_id` calls `list_runs`, so it pays for the progress queries too; accept that and do not add a second listing path. Update the `list_runs` docstring with the rules below.
- `src/agent_manager/cli.py`: `runs_for` needs no logic change, because `model_dump()` already carries the new field into `data.runs[]`. Reword the `RUN_IDENTITY` docstring so that the superset it lists includes `progress`.
- `README.md` "Listing runs": add one `progress` bullet after the `lease` bullet. Keep the existing "New keys are additive" paragraph and do not add a second one.
- Out of scope: `--detach`, `logs --follow`, `watch --from-now`, README `--board` docs, any change to the journal (it stays schema 1), and any change to the lease, claim, control-request or journal-line contracts. No new tables or columns.

## Observable behavior (the rules to pin)

Each `data.runs[]` entry has `progress` with exactly the keys `stories`, `subtasks` and `current`. `stories` and `subtasks` each have exactly `done` and `total`.

- `stories.total` is the number of `stories` rows for the run, and `stories.done` is the number of those rows with `status = 'done'`. `subtasks` follows the same rule over the `subtasks` rows. "Done" means only the `done` value of `models.Status`. `failed`, `escalated`, `stopped` and `cancelled` count toward `total` but not toward `done`.
- The synthetic Integrate story (`card_id` `integrate`, title `Integrate`) and its resolver subtasks are projection rows, so they are counted like any other row. `store` does not special-case them: `integration` imports `store`, so `store` cannot import that constant. A milestone run that had to resolve a conflict therefore shows one more story than the milestone has. State this in the docstring and the README.
- A `task` (`--card`) run counts as its one story and one subtask.
- `current` is the in-flight step, read only from rows. It is the `phases` row with `status = 'started'` that has the latest `started_at`, (`started_at` is nullable text: order `started_at DESC`, so a NULL sorts last and a started phase with a NULL `started_at` is chosen only if no other phase is started), with ties broken ascending by `story_id`, `card_id` and then `position`, so the result is stable when a milestone run has parallel subtasks in flight. In that object, `card` is the phase row's `card_id`, `phase` is its `name`, and `attempt` is the highest `attempts.n` for that `(story_id, card_id, phase)`, or `null` when no attempt row exists yet. When no phase is `started`, `current` is `null`.
- `current` is based on rows and not on liveness. A run whose process died mid-phase still shows the phase it stopped in, and the sibling `lease.live` tells a consumer whether anyone is still working on it. Say so in the docstring and the README.
- A run with no tree rows (just recorded, or never got past the run row) gives `{stories:{done:0,total:0}, subtasks:{done:0,total:0}, current:null}`. It is not `progress: null`.
- Counts are per run. The rows of another run never leak into this one.
- Error paths: none are new. An empty history is still `{"runs": []}`. `RunSummary.model_validate` (with `extra="forbid"` all the way down) still fails loudly on a projection that drifted.

## Tests (TDD: write first; all spawn nothing, so all are unmarked `unit` tests)

Every test below drives a SQLite fixture store (`repo` / `projection` fixtures, `record_run`, `record_story`, `record_subtask`, and phase/attempt recording). None spawns `git`, `brd` or `claude`, so per CLAUDE.md "Test tiers" / design spec §14 all of them are unmarked (`unit`). No `git`, `brd` or `e2e_fake` test is added. The spec's one `e2e_fake` belongs to the `--detach` story.

`tests/test_store.py` (unit):
1. Extend the `SUMMARY_KEYS` pin (`set(store.RunSummary.model_fields) == SUMMARY_KEYS`) with `progress`, and extend `_summary_fields` if it needs to. Pin `RunProgress`, `ProgressCount` and `ProgressCurrent` field sets the same way, plus `extra="forbid"` (an unknown key is rejected).
2. `test_list_runs_gives_a_run_with_no_tree_rows_zero_progress_and_no_current`: 0/0, 0/0, `current is None`, and `progress is not None`.
3. `test_list_runs_counts_stories_and_subtasks_done_against_total`: a mix of statuses (`done`, `started`, `pending`, `failed`, `escalated`) and the exact counts expected.
4. `test_list_runs_counts_a_task_run_as_one_story_and_one_subtask`.
5. `test_list_runs_counts_the_integrate_story_and_its_resolver_subtasks`.
6. `test_list_runs_keeps_each_runs_progress_to_its_own_rows`: two runs, with disjoint counts and disjoint `current`.
7. `test_list_runs_current_is_the_started_phase_with_its_latest_attempt`: several attempts, where `attempt` is the max `n`.
8. `test_list_runs_current_attempt_is_null_before_any_attempt_row`.
9. `test_list_runs_current_is_null_when_no_phase_is_started`: all phases `done` or `pending`.
10. `test_list_runs_current_picks_the_latest_started_phase_among_parallel_ones`, which also covers the tie-break.
11. `test_list_runs_progress_reads_without_writing`: `total_changes` on the connection is unchanged.

`tests/test_cli.py` (unit, `am runs` through the CLI runner on the `projection` fixture):
12. Extend `test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id` (or its successor shape test) so that the entry key set includes `progress`. Rename the test if its name stops being true.
13. `test_runs_progress_has_exactly_its_keys_plain_and_pretty`: the nested key sets of `progress`, `stories`, `subtasks` and, when present, `current`, with both plain and `--pretty` output, modelled on `test_runs_lease_has_exactly_the_five_keys_plain_and_pretty`.
14. `test_runs_shows_a_fixture_runs_progress`: a fixture run tree with known done/total and a started phase shows those exact values in `data.runs[]`. A second run with no rows shows the zero shape and `current: null`.

Verification: `uv run pytest` (full suite) must be green. There is no typecheck or lint step.

---

# `am runs` progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `am runs` entry gains an additive `progress` object, `{stories:{done,total}, subtasks:{done,total}, current:{card,phase,attempt}|null}`, counted by `store.list_runs` from the run's projection rows.

**Architecture:** Three strict pydantic models (`ProgressCount`, `ProgressCurrent`, `RunProgress`) sit next to `RunLease` in `src/agent_manager/store.py`, and `RunSummary` gains `progress: RunProgress | None = None`. `list_runs` fills it for each run with read-only `SELECT`s on the connection it was given (one count query per level, one query for the current phase with a correlated `MAX(attempts.n)`). `cli.runs_for` already does `summary.model_copy(update={"lease": ...}).model_dump()`, so `progress` reaches `data.runs[]` with no logic change; only docstrings and the README change outside `store`.

**Tech Stack:** Python, Typer, Pydantic v2, SQLite (`sqlite3` with `row_factory = sqlite3.Row`), pytest, `uv`.

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-2-3-am-runs-progress-882b212b/docs/superpowers/specs/task-2-3-am-runs-progress-882b212b-design.md` (reproduced verbatim above).

**Worktree / branch:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-2-3-am-runs-progress-882b212b`, branch `ami/task-2-3-am-runs-progress-882b212b`, cut from `ami/task-2-2-am-runs-lease-6bf47e74`. Every path below is relative to that worktree. The plan relies only on what that base already has: `store.RunLease`, `RunSummary.milestone_id/card_id/lease`, `cli._lease_fields`, `cli.runs_for`, the README "Listing runs" section, and the 2.1/2.2 tests. It assumes nothing from any other subtask.

## Global Constraints

- New JSON keys are additive only: no existing key of `data.runs[]` changes name, type or meaning.
- The journal stays schema 1; no change to the lease, claim, control-request or journal-line contracts. No new tables or columns.
- `store` must not import `control` (pinned already by `test_store_does_not_import_control`), and `store` cannot import `integration` (circular).
- All new models use `ConfigDict(extra="forbid")`.
- `list_runs` never returns `progress: None`; a run with no tree rows is `{stories:{done:0,total:0}, subtasks:{done:0,total:0}, current:null}`.
- "Done" means only status `done`.
- `progress` SQL is read-only `SELECT`s on the caller's connection.
- TDD: every test is written and seen failing before its code.
- Tests are unmarked (`unit`) and live in `tests/test_store.py` and `tests/test_cli.py`; no `git`, `brd`, `e2e_fake` or `e2e` test is added.
- Verification: `uv run pytest` green. No typecheck, no lint.

## Review Focus

1. A node recorded twice (`record_story`/`record_subtask` upsert, e.g. `started` then `done`) must move `done` without growing `total`. Pinned in Task 2 by `test_list_runs_counts_a_task_run_as_one_story_and_one_subtask`.
2. A phase that finishes is re-recorded `done` in place, so it must drop out of `current` rather than linger. Pinned in Task 2 by `test_list_runs_current_is_null_when_no_phase_is_started`.
3. A run whose run row says `failed` (process died, or the engine gave up) while a phase row is still `started` must still show that phase as `current`: the rule is rows, not run status. Pinned in Task 2 by `test_list_runs_current_comes_from_rows_not_from_the_run_status`.
4. A database migrated from before `milestone_id` (a bare old `runs` row) must list with the zero `progress`, not crash or show `null`. Pinned in Task 2 by `test_an_old_runs_row_lists_with_zero_progress`.
5. Two runs that use identical story, card and phase names (every `--card` run of the same card does) must not leak `current` or `attempt` into each other, even when the other run's phase started later with more attempts. Pinned in Task 2 by `test_list_runs_keeps_each_runs_progress_to_its_own_rows`.

Known and deliberately not pinned: `phases.started_at` is ordered as text, so rows written with mixed UTC offsets would sort lexically. `runs.started_at` has the same property today in `list_runs`, the engine writes UTC, and the spec fixes `ORDER BY started_at DESC`; every test uses UTC.

## File Structure

- Modify `src/agent_manager/store.py`: add `ProgressCount`, `ProgressCurrent`, `RunProgress` between `RunLease` (line 588) and `RunSummary` (line 608); add `progress` to `RunSummary` and its docstring; add `_progress_count`, `_CURRENT_PHASE_SQL`, `_run_progress` just above `list_runs` (line 642); make `list_runs` fill `progress` and extend its docstring.
- Modify `src/agent_manager/cli.py`: `RUN_IDENTITY` docstring (lines 202-205), `runs_for` docstring (lines 1497-1509). No logic change.
- Modify `README.md`: one bullet after the `lease` bullet (line 442) in "Listing runs".
- Modify `tests/test_store.py`: `SUMMARY_KEYS` (1355-1368), the fields-pin test (1482-1487), new model tests after `test_run_summary_rejects_a_lease_missing_a_key` (ends 1584), new `list_runs` progress tests before `test_latest_run_id_is_the_newest_recorded_run` (1624), one legacy test after `test_an_old_task_run_lists_its_card_id_after_the_milestone_id_migration` (ends 2528).
- Modify `tests/test_cli.py`: `RUNS_ENTRY_KEYS` (4182-4195), rename the shape test at 4235, new helpers and tests after `test_runs_attaches_each_runs_own_lease_and_reads_the_clock_once` (ends 4429).

---

### Task 1: `RunProgress` models and the `RunSummary.progress` field

**Files:**
- Modify: `src/agent_manager/store.py:588-640`
- Test: `tests/test_store.py:1355-1368`, `tests/test_store.py:1482-1487`, new tests after line 1584
- Test: `tests/test_cli.py:4182-4195`, `tests/test_cli.py:4235`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `store.ProgressCount(done: int, total: int)`
  - `store.ProgressCurrent(card: str, phase: str, attempt: int | None)` (all three required)
  - `store.RunProgress(stories: ProgressCount, subtasks: ProgressCount, current: ProgressCurrent | None)` (all three required)
  - `store.RunSummary.progress: RunProgress | None = None`
  - Test constants `PROGRESS_KEYS`, `PROGRESS_COUNT_KEYS`, `PROGRESS_CURRENT_KEYS`, helper `_progress_fields(**overrides) -> dict` in `tests/test_store.py`; `RUNS_ENTRY_KEYS` in `tests/test_cli.py` now includes `"progress"`.

- [ ] **Step 1: Extend the store key pins (failing)**

In `tests/test_store.py`, replace the `SUMMARY_KEYS` block (lines 1355-1368) with:

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
    "lease",
    "progress",
}
"""The seven names `am runs` always had, plus `milestone_id` and `card_id`
(card 0b5a15d7), `lease` (card 6bf47e74) and `progress` (card 882b212b)."""
```

Replace the test at lines 1482-1487 with:

```python
def test_run_summary_fields_are_the_old_seven_plus_milestone_id_card_id_lease_and_progress():
    assert set(store.RunSummary.model_fields) == SUMMARY_KEYS
    assert store.RunSummary.model_config["extra"] == "forbid"
    assert store.RunSummary.model_fields["milestone_id"].default is None
    assert store.RunSummary.model_fields["card_id"].default is None
    assert store.RunSummary.model_fields["lease"].default is None
    assert store.RunSummary.model_fields["progress"].default is None
```

- [ ] **Step 2: Add the progress model tests (failing)**

In `tests/test_store.py`, directly after `test_run_summary_rejects_a_lease_missing_a_key` (ends at line 1584) and before `test_list_runs_leaves_the_lease_to_the_caller_even_with_a_lease_row`, insert:

```python
PROGRESS_KEYS = {"stories", "subtasks", "current"}
PROGRESS_COUNT_KEYS = {"done", "total"}
PROGRESS_CURRENT_KEYS = {"card", "phase", "attempt"}
"""The `runs[]` progress object (card 882b212b): two counts and the step in flight."""


def _progress_fields(**overrides) -> dict:
    """One valid `RunProgress` as a plain dict."""
    return {
        "stories": {"done": 1, "total": 2},
        "subtasks": {"done": 3, "total": 5},
        "current": {"card": "card-1", "phase": "implement", "attempt": 2},
        **overrides,
    }


def test_run_progress_models_have_exactly_their_keys_and_forbid_others():
    assert set(store.RunProgress.model_fields) == PROGRESS_KEYS
    assert set(store.ProgressCount.model_fields) == PROGRESS_COUNT_KEYS
    assert set(store.ProgressCurrent.model_fields) == PROGRESS_CURRENT_KEYS
    for model in (store.RunProgress, store.ProgressCount, store.ProgressCurrent):
        assert model.model_config["extra"] == "forbid"


def test_run_summary_progress_defaults_to_none():
    """Additive for anyone who builds a `RunSummary` by hand; `list_runs`
    itself always fills it."""
    summary = store.RunSummary.model_validate(_summary_fields())

    assert summary.progress is None
    assert summary.model_dump()["progress"] is None


def test_run_summary_accepts_a_progress_object_and_dumps_it_as_a_plain_dict():
    summary = store.RunSummary.model_validate(_summary_fields(progress=_progress_fields()))

    assert isinstance(summary.progress, store.RunProgress)
    assert isinstance(summary.progress.current, store.ProgressCurrent)
    assert summary.model_dump()["progress"] == _progress_fields()


def test_run_summary_accepts_a_null_current_and_a_null_attempt():
    no_current = store.RunSummary.model_validate(
        _summary_fields(progress=_progress_fields(current=None))
    )
    no_attempt = store.RunSummary.model_validate(
        _summary_fields(
            progress=_progress_fields(
                current={"card": "card-1", "phase": "explore", "attempt": None}
            )
        )
    )

    assert no_current.progress is not None
    assert no_current.progress.current is None
    assert no_attempt.progress is not None
    assert no_attempt.progress.current is not None
    assert no_attempt.progress.current.attempt is None


@pytest.mark.parametrize(
    "progress, loc",
    [
        (_progress_fields(extra=1), ("progress", "extra")),
        (
            _progress_fields(stories={"done": 0, "total": 0, "failed": 0}),
            ("progress", "stories", "failed"),
        ),
        (
            _progress_fields(
                current={"card": "c", "phase": "p", "attempt": 1, "story": "s"}
            ),
            ("progress", "current", "story"),
        ),
    ],
    ids=["progress", "count", "current"],
)
def test_run_summary_rejects_an_unknown_key_anywhere_in_progress(progress, loc):
    with pytest.raises(ValidationError) as caught:
        store.RunSummary.model_validate(_summary_fields(progress=progress))

    [error] = caught.value.errors()
    assert error["loc"] == loc
    assert error["type"] == "extra_forbidden"


def test_run_summary_rejects_a_progress_missing_current():
    """`current` is required, never silently absent: `null` is the only way
    to say nothing is in flight."""
    fields = _progress_fields()
    del fields["current"]

    with pytest.raises(ValidationError) as caught:
        store.RunSummary.model_validate(_summary_fields(progress=fields))

    [error] = caught.value.errors()
    assert error["loc"] == ("progress", "current")
    assert error["type"] == "missing"
```

- [ ] **Step 3: Extend the CLI entry-key pin (failing)**

In `tests/test_cli.py`, replace the `RUNS_ENTRY_KEYS` block (lines 4182-4195) with:

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
    "lease",
    "progress",
}
"""Every `data.runs[]` entry: the seven names `am runs` always had, plus
`milestone_id` and `card_id` (card 0b5a15d7), `lease` (card 6bf47e74) and
`progress` (card 882b212b)."""
```

Rename the test at line 4235 (its body stays unchanged) from

```python
def test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id(projection):
```

to

```python
def test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_card_id_lease_and_progress(projection):
```

- [ ] **Step 4: Run the new and changed tests to see them fail**

Run: `uv run pytest tests/test_store.py tests/test_cli.py -k "progress or summary_fields or runs_entries or runs_lease_has_exactly or dumps_exactly_the_summary_keys" -v`

Expected: FAIL. `test_run_progress_models_have_exactly_their_keys_and_forbid_others` fails with `AttributeError: module 'agent_manager.store' has no attribute 'RunProgress'`; the `SUMMARY_KEYS` pins fail because `progress` is missing; the CLI `RUNS_ENTRY_KEYS` tests fail because entries lack `progress`; the `_summary_fields(progress=...)` tests fail with `extra_forbidden` at `("progress",)`.

- [ ] **Step 5: Add the models and the field**

In `src/agent_manager/store.py`, between the end of `class RunLease` (line 605, `accepting: bool`) and `class RunSummary(BaseModel):` (line 608), insert:

```python
class ProgressCount(BaseModel):
    """`done` of `total` rows at one level of a run's tree (`stories` or
    `subtasks`). Only status `done` counts toward `done`; every other status,
    `failed`, `escalated`, `stopped` and `cancelled` included, counts toward
    `total` alone."""

    model_config = ConfigDict(extra="forbid")

    done: int
    total: int


class ProgressCurrent(BaseModel):
    """The step a run is in, read from rows only: the `started` phase's
    subtask `card`, its `phase` name, and the highest `attempt` number
    recorded for that phase, or `None` before its first attempt row."""

    model_config = ConfigDict(extra="forbid")

    card: str
    phase: str
    attempt: int | None


class RunProgress(BaseModel):
    """The `progress` of one `am runs` entry, counted by `list_runs` from the
    run's `stories`, `subtasks`, `phases` and `attempts` rows.

    `current` is required: `None` is how it says no phase is `started`."""

    model_config = ConfigDict(extra="forbid")

    stories: ProgressCount
    subtasks: ProgressCount
    current: ProgressCurrent | None
```

In `class RunSummary`, append this paragraph to the end of the docstring (after the `lease` paragraph, before the closing `"""`):

```
    `progress` is how far the run has got, counted from its tree rows as a
    `RunProgress`. `list_runs` always fills it (a run with no tree rows is 0
    of 0 with no `current`); it defaults to `None` only so that a
    `RunSummary` built by hand stays valid, which keeps it additive.
```

and add the field after `lease: RunLease | None = None`:

```python
    progress: RunProgress | None = None
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/test_store.py tests/test_cli.py -k "progress or summary_fields or runs_entries or runs_lease_has_exactly or dumps_exactly_the_summary_keys" -v`

Expected: PASS. (The CLI entries now carry `"progress": null`, because `list_runs` does not fill it yet; Task 2 pins the filled values.)

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py tests/test_cli.py
git commit -m "feat(store): add RunProgress models and RunSummary.progress (882b212b)"
```

---

### Task 2: `list_runs` counts `progress` from the run's rows

**Files:**
- Modify: `src/agent_manager/store.py:642-669` (helpers above `list_runs`, `list_runs` body and docstring)
- Modify: `src/agent_manager/cli.py:202-205`, `src/agent_manager/cli.py:1497-1509` (docstrings only)
- Test: `tests/test_store.py` new tests before line 1624 (`test_latest_run_id_is_the_newest_recorded_run`), one after line 2528
- Test: `tests/test_cli.py` new helpers and tests after line 4429

**Interfaces:**
- Consumes: `store.ProgressCount`, `store.ProgressCurrent`, `store.RunProgress`, `RunSummary.progress` from Task 1; existing test helpers `_run(repo, run_id)`, `_dispatch(card, phase, n)`, `_listed(root)`, `_migrated_legacy(repo, extra_sql="")` in `tests/test_store.py`; `_record(root, run_id, *, started_at, status="done", with_phases=True, workflow="task", milestone_id=None)`, `_recorded_dispatch(run_id)`, `RECORDED_AT`, `runner`, `RUNS_ENTRY_KEYS` in `tests/test_cli.py`.
- Produces:
  - `store._progress_count(conn: sqlite3.Connection, table: Literal["stories", "subtasks"], run_id: str) -> ProgressCount`
  - `store._run_progress(conn: sqlite3.Connection, run_id: str) -> RunProgress`
  - `store.list_runs(conn)` now returns every `RunSummary` with `progress` set.
  - Test helpers in `tests/test_store.py`: `_progress_run`, `_tree_story`, `_tree_subtask`, `_tree_phase`, `_tree_attempt`, `_progress_at`, `_progress_of`, `_expected`.

- [ ] **Step 1: Add the store test helpers and the counting tests (failing)**

In `tests/test_store.py`, directly before `def test_latest_run_id_is_the_newest_recorded_run(repo):` (line 1624), insert:

```python
# -- progress (card 882b212b) -------------------------------------------------
#
# Unit tier: a SQLite fixture store in tmp_path, nothing spawned.


def _progress_run(
    root: Path,
    run_id: str,
    *,
    workflow: str = "milestone",
    status: str = "started",
    started_at: datetime | None = None,
) -> store.Store:
    """An open store for `run_id` holding its run row and nothing below it.
    The caller records the tree and closes the store."""
    opened = store.Store.open(root, run_id)
    opened.record_run(
        _run(root, run_id).model_copy(
            update={"workflow": workflow, "status": status, "started_at": started_at}
        )
    )
    return opened


def _tree_story(card_id: str, status: str = "started", title: str = "A story") -> models.StoryRun:
    return models.StoryRun(card_id=card_id, title=title, level=0, status=status)


def _tree_subtask(card_id: str, status: str = "started") -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id, branch=f"m1/task-{card_id}", base_branch="main", status=status
    )


def _tree_phase(
    name: str, status: str, started_at: datetime | None = None
) -> models.PhaseRun:
    return models.PhaseRun(name=name, kind="agent", status=status, started_at=started_at)


def _tree_attempt(card: str, phase: str, n: int) -> models.Attempt:
    return models.Attempt(n=n, dispatch=_dispatch(card, phase, n))


def _progress_at(minute: int) -> datetime:
    return datetime(2026, 10, 4, 9, minute, tzinfo=timezone.utc)


def _progress_of(root: Path) -> dict[str, dict | None]:
    """Each listed run's `progress`, dumped, keyed by run id."""
    return {
        summary.id: None if summary.progress is None else summary.progress.model_dump()
        for summary in _listed(root)
    }


def _expected(
    stories: tuple[int, int] = (0, 0),
    subtasks: tuple[int, int] = (0, 0),
    current: dict | None = None,
) -> dict:
    """A dumped `RunProgress` from `(done, total)` pairs."""
    return {
        "stories": {"done": stories[0], "total": stories[1]},
        "subtasks": {"done": subtasks[0], "total": subtasks[1]},
        "current": current,
    }


def test_list_runs_gives_a_run_with_no_tree_rows_zero_progress_and_no_current(repo):
    """`progress` is never null from `list_runs`: a run row with nothing below
    it is 0 of 0 at both levels with no current step."""
    _progress_run(repo, "run-bare").close()

    [summary] = _listed(repo)

    assert summary.progress is not None
    assert summary.progress.current is None
    assert summary.progress.model_dump() == _expected()


def test_list_runs_counts_stories_and_subtasks_done_against_total(repo):
    """Only `done` is done: every other status counts toward `total` alone."""
    layout = {
        ("s-done", "done"): [("a", "done"), ("b", "done")],
        ("s-started", "started"): [("c", "started"), ("d", "stopped")],
        ("s-failed", "failed"): [("e", "failed")],
        ("s-escalated", "escalated"): [("f", "cancelled")],
        ("s-pending", "pending"): [("g", "pending")],
    }
    opened = _progress_run(repo, "run-m")
    try:
        for (story_id, story_status), subtasks in layout.items():
            opened.record_story(_tree_story(story_id, story_status))
            for card, status in subtasks:
                opened.record_subtask(story_id, _tree_subtask(card, status))
    finally:
        opened.close()

    assert _progress_of(repo) == {"run-m": _expected(stories=(1, 5), subtasks=(2, 7))}


def test_list_runs_counts_a_task_run_as_one_story_and_one_subtask(repo):
    """Re-recording a node upserts its row, so finishing the card moves `done`
    without growing `total`."""
    opened = _progress_run(repo, "run-t", workflow="task")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("ef248597"))
        assert _progress_of(repo) == {
            "run-t": _expected(stories=(0, 1), subtasks=(0, 1))
        }

        opened.record_subtask("story-1", _tree_subtask("ef248597", "done"))
        opened.record_story(_tree_story("story-1", "done"))
    finally:
        opened.close()

    assert _progress_of(repo) == {"run-t": _expected(stories=(1, 1), subtasks=(1, 1))}


def test_list_runs_counts_the_integrate_story_and_its_resolver_subtasks(repo):
    """`store` cannot import `integration` (which imports `store`), so the
    synthetic Integrate story is a story like any other: a run that resolved
    a conflict shows one story more than its milestone has. Its resolver
    subtask is named after the conflicting story, as `integration` records it."""
    opened = _progress_run(repo, "run-m")
    try:
        opened.record_story(_tree_story("s1", "done"))
        opened.record_subtask("s1", _tree_subtask("a", "done"))
        opened.record_story(_tree_story("integrate", "started", title="Integrate"))
        opened.record_subtask("integrate", _tree_subtask("s1"))
        opened.record_phase(
            "integrate", "s1", _tree_phase("resolve", "started", _progress_at(5))
        )
    finally:
        opened.close()

    assert _progress_of(repo) == {
        "run-m": _expected(
            stories=(1, 2),
            subtasks=(1, 2),
            current={"card": "s1", "phase": "resolve", "attempt": None},
        )
    }


def test_list_runs_keeps_each_runs_progress_to_its_own_rows(repo):
    """Both runs use the same story, card and phase names, and the later run's
    phase started later with more attempts: none of it may leak across."""
    early = _progress_run(repo, "run-a", started_at=_progress_at(0))
    try:
        early.record_story(_tree_story("story-1", "done"))
        early.record_subtask("story-1", _tree_subtask("card-1", "done"))
        early.record_subtask("story-1", _tree_subtask("card-2"))
        early.record_phase(
            "story-1", "card-2", _tree_phase("implement", "started", _progress_at(1))
        )
        early.record_attempt(
            "story-1", "card-2", "implement", _tree_attempt("card-2", "implement", 1)
        )
    finally:
        early.close()
    late = _progress_run(repo, "run-b", started_at=_progress_at(10))
    try:
        late.record_story(_tree_story("story-1"))
        late.record_story(_tree_story("story-2"))
        late.record_subtask("story-1", _tree_subtask("card-2"))
        late.record_phase(
            "story-1", "card-2", _tree_phase("implement", "started", _progress_at(30))
        )
        for n in (1, 2, 3):
            late.record_attempt(
                "story-1", "card-2", "implement", _tree_attempt("card-2", "implement", n)
            )
    finally:
        late.close()

    assert _progress_of(repo) == {
        "run-a": _expected(
            stories=(1, 1),
            subtasks=(1, 2),
            current={"card": "card-2", "phase": "implement", "attempt": 1},
        ),
        "run-b": _expected(
            stories=(0, 2),
            subtasks=(0, 1),
            current={"card": "card-2", "phase": "implement", "attempt": 3},
        ),
    }


def test_an_empty_history_still_lists_no_runs_with_progress(repo):
    """No new error path: an empty projection is still an empty list."""
    assert _listed(repo) == []
```

- [ ] **Step 2: Add the `current` tests and the no-write test (failing)**

Directly after the block from Step 1 (still before `test_latest_run_id_is_the_newest_recorded_run`), insert:

```python
def test_list_runs_current_is_the_started_phase_with_its_latest_attempt(repo):
    """`attempt` is the highest `n` of that phase of that card: not of a done
    phase before it, and not of the same phase name on another card."""
    opened = _progress_run(repo, "run-m")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_subtask("story-1", _tree_subtask("card-2", "done"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("explore", "done", _progress_at(1))
        )
        for n in (1, 2, 3, 4):
            opened.record_attempt(
                "story-1", "card-1", "explore", _tree_attempt("card-1", "explore", n)
            )
        opened.record_phase(
            "story-1", "card-2", _tree_phase("implement", "done", _progress_at(2))
        )
        for n in range(1, 8):
            opened.record_attempt(
                "story-1", "card-2", "implement", _tree_attempt("card-2", "implement", n)
            )
        opened.record_phase(
            "story-1", "card-1", _tree_phase("implement", "started", _progress_at(3))
        )
        for n in (1, 2):
            opened.record_attempt(
                "story-1", "card-1", "implement", _tree_attempt("card-1", "implement", n)
            )
    finally:
        opened.close()

    [summary] = _listed(repo)

    assert summary.progress is not None
    assert summary.progress.current == store.ProgressCurrent(
        card="card-1", phase="implement", attempt=2
    )


def test_list_runs_current_attempt_is_null_before_any_attempt_row(repo):
    opened = _progress_run(repo, "run-t", workflow="task")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("explore", "started", _progress_at(1))
        )
    finally:
        opened.close()

    assert _progress_of(repo)["run-t"]["current"] == {
        "card": "card-1",
        "phase": "explore",
        "attempt": None,
    }


def test_list_runs_current_is_null_when_no_phase_is_started(repo):
    """A phase that finished is re-recorded `done` in place, so it drops out;
    a `pending` phase is not in flight."""
    opened = _progress_run(repo, "run-t", workflow="task")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("explore", "started", _progress_at(1))
        )
        opened.record_attempt(
            "story-1", "card-1", "explore", _tree_attempt("card-1", "explore", 1)
        )
        opened.record_phase(
            "story-1", "card-1", _tree_phase("explore", "done", _progress_at(1))
        )
        opened.record_phase("story-1", "card-1", _tree_phase("verify", "pending"))
    finally:
        opened.close()

    assert _progress_of(repo) == {"run-t": _expected(stories=(0, 1), subtasks=(0, 1))}


def test_list_runs_current_picks_the_latest_started_phase_among_parallel_ones(repo):
    """A milestone run has subtasks of several stories in flight at once; the
    one whose phase started last is `current`, whatever order they were
    recorded in."""
    opened = _progress_run(repo, "run-m")
    try:
        for story_id, card, minute in (("s1", "c1", 5), ("s2", "c2", 9), ("s3", "c3", 7)):
            opened.record_story(_tree_story(story_id))
            opened.record_subtask(story_id, _tree_subtask(card))
            opened.record_phase(
                story_id, card, _tree_phase("implement", "started", _progress_at(minute))
            )
    finally:
        opened.close()

    assert _progress_of(repo)["run-m"]["current"] == {
        "card": "c2",
        "phase": "implement",
        "attempt": None,
    }


def test_list_runs_current_breaks_a_started_at_tie_by_story_then_card_then_position(repo):
    """Each run records the loser first, so insertion order cannot pass for
    the tie-break."""
    same = _progress_at(5)
    by_story = _progress_run(repo, "run-story")
    try:
        for story_id, card in (("bbbb", "c-of-b"), ("aaaa", "c-of-a")):
            by_story.record_story(_tree_story(story_id))
            by_story.record_subtask(story_id, _tree_subtask(card))
            by_story.record_phase(story_id, card, _tree_phase("implement", "started", same))
    finally:
        by_story.close()
    by_card = _progress_run(repo, "run-card")
    try:
        by_card.record_story(_tree_story("story-1"))
        for card in ("c2", "c1"):
            by_card.record_subtask("story-1", _tree_subtask(card))
            by_card.record_phase("story-1", card, _tree_phase("implement", "started", same))
    finally:
        by_card.close()
    by_position = _progress_run(repo, "run-position")
    try:
        by_position.record_story(_tree_story("story-1"))
        by_position.record_subtask("story-1", _tree_subtask("card-1"))
        for name in ("zeta", "alpha"):
            by_position.record_phase("story-1", "card-1", _tree_phase(name, "started", same))
    finally:
        by_position.close()

    currents = {run_id: progress["current"] for run_id, progress in _progress_of(repo).items()}

    assert currents == {
        "run-story": {"card": "c-of-a", "phase": "implement", "attempt": None},
        "run-card": {"card": "c1", "phase": "implement", "attempt": None},
        "run-position": {"card": "card-1", "phase": "zeta", "attempt": None},
    }


def test_list_runs_current_takes_a_started_phase_with_no_start_time_only_when_alone(repo):
    """`started_at DESC` sends NULL last: an undated started phase loses to any
    dated one, even one whose story sorts after it, and wins when alone."""
    mixed = _progress_run(repo, "run-mixed")
    try:
        mixed.record_story(_tree_story("a"))
        mixed.record_subtask("a", _tree_subtask("card-a"))
        mixed.record_phase("a", "card-a", _tree_phase("undated", "started", None))
        mixed.record_story(_tree_story("b"))
        mixed.record_subtask("b", _tree_subtask("card-b"))
        mixed.record_phase("b", "card-b", _tree_phase("dated", "started", _progress_at(1)))
    finally:
        mixed.close()
    alone = _progress_run(repo, "run-alone")
    try:
        alone.record_story(_tree_story("a"))
        alone.record_subtask("a", _tree_subtask("card-a"))
        alone.record_phase("a", "card-a", _tree_phase("undated", "started", None))
    finally:
        alone.close()

    currents = {run_id: progress["current"] for run_id, progress in _progress_of(repo).items()}

    assert currents == {
        "run-mixed": {"card": "card-b", "phase": "dated", "attempt": None},
        "run-alone": {"card": "card-a", "phase": "undated", "attempt": None},
    }


def test_list_runs_current_comes_from_rows_not_from_the_run_status(repo):
    """A run whose process died mid-phase keeps the `started` phase row it
    stopped in, and `current` shows it whatever the run row says; `lease.live`
    is what tells a consumer nobody is working on it."""
    opened = _progress_run(repo, "run-dead", status="failed")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("implement", "started", _progress_at(1))
        )
        opened.record_attempt(
            "story-1", "card-1", "implement", _tree_attempt("card-1", "implement", 1)
        )
    finally:
        opened.close()

    [summary] = _listed(repo)

    assert summary.status == "failed"
    assert summary.progress is not None
    assert summary.progress.model_dump()["current"] == {
        "card": "card-1",
        "phase": "implement",
        "attempt": 1,
    }


def test_list_runs_progress_reads_without_writing(repo):
    """`am runs` takes no lock and writes nothing: the progress queries are
    plain `SELECT`s, and leave no transaction open behind them."""
    opened = _progress_run(repo, "run-t", workflow="task")
    try:
        opened.record_story(_tree_story("story-1"))
        opened.record_subtask("story-1", _tree_subtask("card-1"))
        opened.record_phase(
            "story-1", "card-1", _tree_phase("implement", "started", _progress_at(1))
        )
        opened.record_attempt(
            "story-1", "card-1", "implement", _tree_attempt("card-1", "implement", 1)
        )
    finally:
        opened.close()

    conn = store.open_db(repo)
    try:
        before = conn.total_changes
        [summary] = store.list_runs(conn)
        after = conn.total_changes
        in_transaction = conn.in_transaction
    finally:
        conn.close()

    assert summary.progress is not None
    assert summary.progress.current is not None
    assert after == before
    assert in_transaction is False
```

- [ ] **Step 3: Add the legacy-database test (failing)**

In `tests/test_store.py`, directly after `test_an_old_task_run_lists_its_card_id_after_the_milestone_id_migration` (ends at line 2528 before this task's inserts; search for the function name) and before `test_a_runs_milestone_id_survives_a_rebuild_from_the_journal`, insert:

```python
def test_an_old_runs_row_lists_with_zero_progress(repo):
    """A database from before `milestone_id` has a bare `runs` row and empty
    tree tables; it lists with the zero `progress`, not `null` or an error."""
    [summary] = _migrated_legacy(repo)

    assert summary.progress is not None
    assert summary.progress.model_dump() == _expected()
```

- [ ] **Step 4: Add the CLI helpers and tests (failing)**

In `tests/test_cli.py`, directly after `test_runs_attaches_each_runs_own_lease_and_reads_the_clock_once` (ends at line 4429, `assert len(calls) == 1`) and before `def test_a_missing_repo_dir_is_an_envelope_for_both_read_commands`, insert:

```python
RUNS_PROGRESS_KEYS = {"stories", "subtasks", "current"}
RUNS_PROGRESS_COUNT_KEYS = {"done", "total"}
RUNS_PROGRESS_CURRENT_KEYS = {"card", "phase", "attempt"}
"""`data.runs[].progress` (card 882b212b), its two counts, and a non-null `current`."""


def _record_started_phase(root: Path, run_id: str, *, attempts: int) -> None:
    """`_record`'s `card-1` given a third phase, `implement`, still `started`,
    with `attempts` attempt rows numbered from 1."""
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(
                name="implement", kind="agent", status="started", started_at=RECORDED_AT
            ),
        )
        for n in range(1, attempts + 1):
            opened.record_attempt(
                "story-1",
                "card-1",
                "implement",
                models.Attempt(n=n, dispatch=_recorded_dispatch(run_id)),
            )
    finally:
        opened.close()


def _record_bare_run(root: Path, run_id: str, *, started_at: datetime) -> None:
    """A run row with nothing below it: a run that never got past starting."""
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=started_at,
            )
        )
    finally:
        opened.close()


def test_runs_progress_has_exactly_its_keys_plain_and_pretty(projection):
    """The shape pin for `progress`, beside `RUNS_ENTRY_KEYS`'s own: one run
    with a `current`, one without."""
    in_flight = "20260923T090000Z-cbe34d00"
    _record(projection, in_flight, started_at=RECORDED_AT, status="started")
    _record_started_phase(projection, in_flight, attempts=1)
    _record(
        projection,
        "20260921T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
    )
    argv = ["runs", "--repo-dir", str(projection)]

    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])

    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for envelope in (json.loads(plain.stdout), json.loads(pretty.stdout)):
        entries = envelope["data"]["runs"]
        assert [entry["progress"]["current"] is None for entry in entries] == [False, True]
        for entry in entries:
            assert set(entry) == RUNS_ENTRY_KEYS
            progress = entry["progress"]
            assert set(progress) == RUNS_PROGRESS_KEYS
            for level in ("stories", "subtasks"):
                assert set(progress[level]) == RUNS_PROGRESS_COUNT_KEYS
                assert all(type(progress[level][key]) is int for key in RUNS_PROGRESS_COUNT_KEYS)
            if progress["current"] is not None:
                assert set(progress["current"]) == RUNS_PROGRESS_CURRENT_KEYS
                assert isinstance(progress["current"]["card"], str)
                assert isinstance(progress["current"]["phase"], str)
                assert type(progress["current"]["attempt"]) is int


def test_runs_shows_a_fixture_runs_progress(projection):
    """Exact values through the CLI: a run in flight, a finished one, and one
    with no tree rows at all, which is the zero shape and never `null`."""
    in_flight = "20260923T090000Z-cbe34d00"
    finished = "20260922T090000Z-cbe34d00"
    bare = "20260921T090000Z-cbe34d00"
    _record(projection, in_flight, started_at=RECORDED_AT, status="started")
    _record_started_phase(projection, in_flight, attempts=2)
    _record(
        projection,
        finished,
        started_at=datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc),
    )
    _record_bare_run(
        projection, bare, started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    )

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    entries = json.loads(result.stdout)["data"]["runs"]
    assert [entry["id"] for entry in entries] == [in_flight, finished, bare]
    assert {entry["id"]: entry["progress"] for entry in entries} == {
        in_flight: {
            "stories": {"done": 0, "total": 1},
            "subtasks": {"done": 0, "total": 1},
            "current": {"card": "card-1", "phase": "implement", "attempt": 2},
        },
        finished: {
            "stories": {"done": 1, "total": 1},
            "subtasks": {"done": 1, "total": 1},
            "current": None,
        },
        bare: {
            "stories": {"done": 0, "total": 0},
            "subtasks": {"done": 0, "total": 0},
            "current": None,
        },
    }
```

- [ ] **Step 5: Run the new tests to see them fail**

Run: `uv run pytest tests/test_store.py tests/test_cli.py -k "progress or current or counts_ or keeps_each_runs" -v`

Expected: FAIL. The store tests fail on `summary.progress is not None` / `_progress_of(...)` returning `None` values (e.g. `assert {'run-m': None} == {'run-m': {...}}`); `test_runs_progress_has_exactly_its_keys_plain_and_pretty` fails with `TypeError: 'NoneType' object is not subscriptable`; `test_runs_shows_a_fixture_runs_progress` fails comparing `None` to the expected dicts. (`test_an_empty_history_still_lists_no_runs_with_progress` already passes; it pins that no new error path appears.)

- [ ] **Step 6: Implement the progress queries in `list_runs`**

In `src/agent_manager/store.py`, directly above `def list_runs(conn: sqlite3.Connection) -> list[RunSummary]:` (line 642 before Task 1's insert), insert:

```python
def _progress_count(
    conn: sqlite3.Connection, table: Literal["stories", "subtasks"], run_id: str
) -> ProgressCount:
    """`done` of `total` rows of `table` for one run. `table` is one of two
    literals from this module, never user input, so formatting it in is safe.
    `SUM` over no rows is NULL, hence the `COALESCE`."""
    row = conn.execute(
        f"SELECT COUNT(*) AS total, COALESCE(SUM(status = 'done'), 0) AS done"
        f" FROM {table} WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return ProgressCount(done=row["done"], total=row["total"])


_CURRENT_PHASE_SQL = """
SELECT phases.card_id AS card,
       phases.name    AS phase,
       (SELECT MAX(attempts.n) FROM attempts
         WHERE attempts.run_id   = phases.run_id
           AND attempts.story_id = phases.story_id
           AND attempts.card_id  = phases.card_id
           AND attempts.phase    = phases.name) AS attempt
  FROM phases
 WHERE phases.run_id = ? AND phases.status = 'started'
 ORDER BY phases.started_at DESC, phases.story_id, phases.card_id, phases.position
 LIMIT 1
"""
"""The run's in-flight phase: the `started` one that started last (a NULL
`started_at` sorts last under `DESC`), ties broken by story, card, then
position, with the highest attempt number of that phase, or NULL before its
first attempt row."""


def _run_progress(conn: sqlite3.Connection, run_id: str) -> RunProgress:
    """One run's `RunProgress`, from read-only `SELECT`s on `conn`."""
    row = conn.execute(_CURRENT_PHASE_SQL, (run_id,)).fetchone()
    return RunProgress(
        stories=_progress_count(conn, "stories", run_id),
        subtasks=_progress_count(conn, "subtasks", run_id),
        current=None if row is None else ProgressCurrent(**dict(row)),
    )
```

(`Literal` is already imported at the top of `store.py`: `from typing import Any, Literal, get_args`.)

Replace the last line of `list_runs`,

```python
    return [RunSummary.model_validate(dict(row)) for row in rows]
```

with

```python
    return [
        RunSummary.model_validate({**dict(row), "progress": _run_progress(conn, row["id"])})
        for row in rows
    ]
```

and append this to the end of the `list_runs` docstring (after the `card_id` paragraph, before the closing `"""`):

```
    `progress` is counted here for every run, never left `None`, from plain
    `SELECT`s over that run's `stories`, `subtasks`, `phases` and `attempts`
    rows; nothing in it needs `control`. At each level `done` counts only
    status `done` and `total` counts every row, so `failed`, `escalated`,
    `stopped` and `cancelled` rows are in `total` alone. The synthetic
    Integrate story and its resolver subtasks are rows like any other (`store`
    cannot import `integration`, which imports it), so a milestone run that
    resolved a conflict shows one story more than its milestone has.
    `current` is the `started` phase that started last (see
    `_CURRENT_PHASE_SQL`), or `None` when no phase is started. It is read
    from rows, not from liveness: a run whose process died mid-phase still
    shows the phase it stopped in, and `lease.live` tells whether anyone is
    still working on it. A run with no tree rows is 0 of 0 with no `current`.
```

- [ ] **Step 7: Reword the `cli` docstrings (no logic change)**

In `src/agent_manager/cli.py`, replace the `RUN_IDENTITY` docstring (lines 202-205):

```python
"""The run's own fields, without `config` and without the tree below it. §10's
`status` header is these seven names. Each `runs` entry carries the same seven,
plus `milestone_id`, `card_id` and `lease` (a superset), so the two commands
still describe a run's identity the same way."""
```

with:

```python
"""The run's own fields, without `config` and without the tree below it. §10's
`status` header is these seven names. Each `runs` entry carries the same seven,
plus `milestone_id`, `card_id`, `lease` and `progress` (a superset), so the two
commands still describe a run's identity the same way."""
```

In `runs_for`'s docstring, replace the first line

```
    """This project's run history, newest first, each run with its lease.
```

with

```
    """This project's run history, newest first, each run with its lease and progress.
```

and append, after the paragraph ending `One \`now\` judges the whole listing.`:

```

    `progress` arrives already counted by `store.list_runs`; `model_copy`
    keeps it and `model_dump` carries it into the entry unchanged.
```

- [ ] **Step 8: Run the new tests to see them pass**

Run: `uv run pytest tests/test_store.py tests/test_cli.py -k "progress or current or counts_ or keeps_each_runs or summary or runs_" -v`

Expected: PASS, including the pre-existing `list_runs`, lease and `runs` tests.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/store.py src/agent_manager/cli.py tests/test_store.py tests/test_cli.py
git commit -m "feat(runs): count progress per run in list_runs (882b212b)"
```

---

### Task 3: Document `progress` in the README and verify the full suite

**Files:**
- Modify: `README.md:442-444`

**Interfaces:**
- Consumes: the behaviour pinned in Task 2.
- Produces: nothing code-facing.

- [ ] **Step 1: Add the `progress` bullet**

In `README.md`, section "Listing runs", directly after the `lease` bullet (the line starting ``- `lease`: the process holding the run``) and before the blank line and the "New keys are additive" paragraph, insert this one line (do not add another "additive" paragraph):

```markdown
- `progress`: how far the run has got, counted from its recorded tree: `{stories: {done, total}, subtasks: {done, total}, current}`. `done` counts only rows whose status is `done`; `failed`, `escalated`, `stopped` and `cancelled` rows count toward `total` only. A milestone run that had to resolve a merge conflict also counts its synthetic `Integrate` story and that story's resolver subtasks, so it shows one story more than the milestone has. `current` is `{card, phase, attempt}` for the `started` phase that started most recently (`attempt` is that phase's highest attempt number, `null` before its first attempt), or `null` when no phase is started. It is read from the recorded rows, not from a live process: a run whose process died mid-phase still shows the phase it stopped in, so check `lease.live` to know whether anyone is still working on it. A run with nothing recorded below it shows `0` of `0` at both levels and `current: null`; `progress` itself is never `null`.
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest`

Expected: PASS (all default `unit` + `git` tiers green; no test was added to an opt-in tier).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document am runs progress (882b212b)"
```

---

## Notes

- The spec author's summary handed to the planner was truncated at 2000 characters; this plan was written from the corrected spec read from disk (reproduced above), not from that summary, so the truncation does not affect it.
- Spec test 10 is split into three tests (`..._picks_the_latest_started_phase_among_parallel_ones`, `..._breaks_a_started_at_tie_by_story_then_card_then_position`, `..._takes_a_started_phase_with_no_start_time_only_when_alone`) so each ordering rule fails on its own.
- `tests/test_store.py`'s module docstring calls its tests "steps" tier; that predates the tier spec. The new tests are unmarked and spawn nothing, which is the `unit` tier by the placement rule; no marker is added.
