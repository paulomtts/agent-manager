<!-- task-pipeline: validated -->
# Subtask 3ebd08fb — Attempt drops cost/token fields; attempts table drops the matching columns

Parent story: d5aa963b "Remove dead cost/token tracking". Source design: `docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md` §§3.1, 3.3, 4.2, 4.3, 5.2, 5.3 items 1–5. This document narrows that agreed design to this one subtask; it introduces nothing new.

Line numbers below are from this worktree and are approximate; symbols are authoritative. (The upstream exploration summary was truncated at its file:line reference list, and several of its line numbers do not match this worktree, e.g. `replay` is at store.py:559 and its `Attempt.model_validate` call at :614. Everything here was rechecked against the worktree by symbol.)

## Scope

In scope: `src/agent_manager/models.py`, `src/agent_manager/store.py`, and their tests (`tests/test_models.py`, `tests/test_store.py`), plus one resume-adoption test in `tests/test_dispatch.py`.

Out of scope:
- `dispatch.py`'s usage removal. Sibling 38f7bada already did it.
- `harness.base.Usage`, `HarnessAdapter.parse_usage`, the `ClaudeAdapter` scanners and `_tokens`/`_cost` helpers, the fake-adapter `parse_usage` stubs, import hygiene, README, and design-spec §9/§17 text. All of these belong to sibling 7988f1db, which is blocked on this card.

## Changes

1. **`models.Attempt`**: delete `tokens_in`, `tokens_out` and `cost`. `Attempt` ends up with exactly eight fields: `n, dispatch, status, exit_code, duration, prompt_path, result_path, stdout_path`. It keeps `extra="forbid"` (inherited from `_Model`). In the module docstring (models.py:15), change "no exit code, duration, tokens or cost" to "no exit code or duration".
2. **`_SCHEMA` `CREATE TABLE IF NOT EXISTS attempts`**: remove the three column definitions `tokens_in INTEGER`, `tokens_out INTEGER` and `cost REAL`. Do not use any `ALTER TABLE … DROP COLUMN`, because migration stays additive-only (see `_add_missing_columns` / `open_db`).
   - A fresh DB gets 12 attempt columns.
   - An existing 15-column DB is left as it is. Its three retired columns are never written or read again, so they stay NULL.
3. **`load_run`**: stop passing `attempt_row["tokens_in"]`, `["tokens_out"]` and `["cost"]` into `models.Attempt(...)`.
4. **`_write_attempt_row`**: remove the three names from four places:
   - the INSERT column list
   - the VALUES placeholders
   - the `ON CONFLICT … DO UPDATE SET` clause
   - the bound-parameter dict

   The narrower statement must work against both the 12-column and the 15-column table.
5. **Replay reading shim**:
   - Add a module constant `_RETIRED_ATTEMPT_KEYS: frozenset[str] = frozenset({"tokens_in", "tokens_out", "cost"})` next to `_EVENT_KINDS`. Give it a docstring or comment that names the source design and gives the reason: every journal written before 2026-10-03 carries these keys, as null.
   - In `replay()`, just before `models.Attempt.model_validate(line.payload)`, validate a copy of the payload with those keys removed. Remove them whether their values are null or not.
   - This must be a named allow-list, not `extra="ignore"`. Any other unknown key on an attempt payload must still raise `pydantic.ValidationError` naming that key.
   - Only the `attempt_upsert` branch strips keys. `run_upsert`, `story_upsert`, `subtask_upsert` and `phase_upsert` keep strict validation unchanged.
   - Add one sentence to `replay`'s "Nothing here is defensive" docstring naming this single exception.

## Observable behavior and error paths

- Old journals (attempt lines with `tokens_in`/`tokens_out`/`cost`, null or not) must still work in two places:
  - `Store.rebuild_from_journal` and `Store.replay_journal` replay them into a tree whose attempts have none of those attributes.
  - Resume adoption (dispatch's `replay_journal` call, which catches `ValidationError` and declines silently) still adopts recorded `ok` attempts. It must not quietly redispatch every phase. This is why the shim is required, not optional.
- An attempt line with any other unknown key still fails loudly with `ValidationError`.
- A 15-column legacy DB opens, accepts `record_attempt`, and loads through `load_run` without error. The retired columns read NULL.

## Tests

Tier rule (CLAUDE.md "Test tiers"): a test's tier is set by what it spawns. sqlite is not a subprocess, and `FakeLauncher` is an injected fake. None of these tests spawn git, brd or claude, so all of them are unmarked, which means the default **unit** tier. `tests/test_store.py`'s neighbouring tests are unmarked too. The exploration summary's "git tier, like its neighbours" was wrong.

New tests:
1. `tests/test_store.py` (unit): hand-write a journal whose `attempt_upsert` payload carries `"tokens_in": null, "tokens_out": null, "cost": null`, plus a variant with non-null values.
   - `rebuild_from_journal` and `replay_journal` both return the tree with the attempt intact and none of those attributes.
   - A third line with one unrelated unknown key (e.g. `"operator": "x"`, following the existing pattern near test_store.py:577–585) still raises `ValidationError` whose message names that key.
2. `tests/test_dispatch.py` (unit, FakeLauncher): a source run whose journal has retired keys on an `ok` attempt is adopted on resume, not declined as unreadable.
3. `tests/test_models.py` (unit): `set(models.Attempt.model_fields) == {"n","dispatch","status","exit_code","duration","prompt_path","result_path","stdout_path"}`.
4. `tests/test_store.py` (unit), next to `test_a_fresh_phases_table_carries_detail_as_its_last_column`: on a fresh DB, `PRAGMA table_info(attempts)` lists exactly 12 names, none of them retired.
5. `tests/test_store.py` (unit), following the `_LEGACY_PHASES` pattern: recreate `attempts` in the old 15-column shape, then `record_attempt` and `load_run`. Nothing raises on open, write or load, and the three retired columns read NULL.

Existing-test edits (§3.1 Tests table / §5.2, all unit):
- `tests/test_models.py`:
  - Delete the three `is None` assertions on the retired fields.
  - Drop the `tokens_in=`/`tokens_out=`/`cost=` kwargs from fixtures.
  - Narrow the negative/NaN rejection tests to `duration` only, and rename them to match.
  - Change `attempt.cost is None` to `attempt.duration is None`.
- `tests/test_store.py`: replace `cost=` / `.cost` arguments and assertions with `duration=` / `.duration`.

Verification: `uv run pytest` passes.

---

# Attempt Drops Cost/Token Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `tokens_in`/`tokens_out`/`cost` from `models.Attempt` and from the `attempts` table, and keep every pre-2026-10-03 journal (whose attempt lines carry those keys) replayable through a named, attempt-only reading shim.

**Architecture:** Task 1 deletes the three fields from the model and, in the same commit, from everything in `store.py` that reads or writes them (`_SCHEMA`, `load_run`, `_write_attempt_row`), since the store stops working the moment the model loses them. No column is ever dropped from an existing database. Task 2 adds `_RETIRED_ATTEMPT_KEYS` plus a tiny pure helper that returns an attempt payload without those keys, and routes both places that validate a journalled attempt payload through it: `replay` and `_journaled_statuses` (the latter is what `diverging` uses, and `rebuild_from_journal` runs `diverging` whenever a projection exists).

**Tech Stack:** Python 3, Pydantic v2, sqlite3, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-attempt-drops-cost-3ebd08fb-design.md` (prepended above), narrowing `docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md`.

**Branch / worktree:** `m19/task-attempt-drops-cost-3ebd08fb` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-attempt-drops-cost-3ebd08fb`, cut from `m19/task-dispatch-stops-reading-38f7bada`. Assume nothing from sibling 7988f1db exists here: `harness.base.Usage`, `HarnessAdapter.parse_usage` and `tests/test_dispatch.py`'s `FakeAdapter.parse_usage` stub are all still present and must be left alone.

## Global Constraints

- `Attempt` keeps exactly eight fields: `n, dispatch, status, exit_code, duration, prompt_path, result_path, stdout_path`, and keeps `extra="forbid"` (inherited from `_Model`).
- Never `ALTER TABLE … DROP COLUMN`. Migration stays additive-only; a fresh DB gets 12 attempt columns, an existing 15-column DB is left as it is.
- The shim is a named allow-list (`_RETIRED_ATTEMPT_KEYS`), not `extra="ignore"`; it strips the three keys whether null or not; it applies to `attempt_upsert` payloads only.
- Any other unknown key on an attempt payload still raises `pydantic.ValidationError` naming that key.
- Do not touch `dispatch.py`, `harness/`, `README`, or the design spec text (siblings 38f7bada and 7988f1db own them). `harness/base.py`'s `Usage` docstring still says its field names match `Attempt.tokens_in`/`tokens_out`/`cost`; that sentence goes stale here but is 7988f1db's to delete along with `Usage` itself.
- Every new test is unmarked (default `unit` tier): sqlite is not a subprocess and `FakeLauncher` is an injected fake. No `git`/`brd`/`e2e*` marks.
- Verification: `uv run pytest`.

## Deviation found while planning (read before Task 2)

The spec puts the shim "just before `models.Attempt.model_validate(line.payload)`" in `replay()`. There is a second place in `store.py` that validates journalled attempt payloads strictly: `_journaled_statuses` (`_NODE_MODELS[line.event].model_validate(line.payload)`, store.py:~702), which `diverging` calls. `Store.rebuild_from_journal` runs `diverging` whenever the projection already holds the run (`force=False`, the default), and `cli.py:~325` runs it for the divergence report. Shimming only `replay` would leave `rebuild_from_journal` over a live projection and the CLI divergence check raising `ValidationError` on every old journal, which breaks the spec's own "Old journals ... must still work in ... `Store.rebuild_from_journal`" promise. Task 2 therefore routes both call sites through one helper, keeps the `attempt_upsert`-only scope, and pins the `diverging` path with a test. Nothing else in the spec changes.

The exploration summary was truncated at its file:line reference list (it said so itself). This plan does not depend on the missing text: every location below was re-read in this worktree.

## Review Focus

1. `rebuild_from_journal` (default `force=False`) on a run whose projection exists and whose journal has old attempt lines: the user expects the rebuild to succeed, not a `ValidationError` from the divergence pre-check. Pinned in Task 2 by `test_an_attempt_line_carrying_the_retired_usage_keys_still_replays` (rebuilds over a live projection and asserts `diverging(...) == []`).
2. An old journal whose attempt lines carry non-null usage values (a harness that did report cost at some point): expected to replay exactly like the null case, values discarded. Pinned in Task 2 by the `non_null` parametrization of the same test.
3. A retired key on a non-attempt line (`run_upsert`, `story_upsert`, `subtask_upsert`, `phase_upsert`): expected to still fail loudly, since no such line ever carried it. Pinned in Task 2 by `test_only_attempt_lines_shed_the_retired_usage_keys`.
4. Re-recording an attempt (the `ON CONFLICT … DO UPDATE` path) against a legacy 15-column `attempts` table: expected to update in place with the retired columns left NULL. Pinned in Task 1 by `test_an_attempts_table_that_still_has_the_usage_columns_keeps_working`, which records implement attempt 1 twice.
5. Constructing or validating an `Attempt` directly with a retired key (outside `replay`): expected to still be rejected, so the shim cannot leak into the model. Pinned in Task 1 by `test_attempt_itself_still_forbids_a_retired_usage_key`.

---

### Task 1: `Attempt` loses its usage fields; the store stops reading and writing them

**Files:**
- Modify: `src/agent_manager/models.py:14-16` (module docstring), `:86-88` (three fields)
- Modify: `src/agent_manager/store.py:83-100` (`_SCHEMA` attempts table), `:238-256` (`open_db` docstring), `:919-933` (`load_run` attempt construction), `:1596-1644` (`Store._write_attempt_row`)
- Test: `tests/test_models.py` (new tests after `test_attempt_rejects_an_unknown_status_naming_the_allowed_set` ~line 197; edits at ~104-133, ~163-181, ~529-531, ~588)
- Test: `tests/test_store.py` (new tests after `test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it` ~line 2081; edits at ~660, ~676, ~813-815, ~861, ~868, ~954-956, ~4783)
- Test: `tests/test_dispatch.py` (edits at ~659-685, ~748-754, ~787-795)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `models.Attempt` with exactly the eight fields above; `store._SCHEMA` creating a 12-column `attempts` table; `Store._write_attempt_row(run_id, story_id, card_id, phase_name, attempt)` writing only the 12 columns (signature unchanged); `tests/test_dispatch.py` module constant `_USAGE_KEYS: frozenset[str] = frozenset({"tokens_in", "tokens_out", "cost"})` (Task 2's adoption test reuses it).

- [ ] **Step 1: Write the new failing model tests**

In `tests/test_models.py`, insert directly after `test_attempt_rejects_an_unknown_status_naming_the_allowed_set` (ends ~line 197):

```python
def test_attempt_carries_exactly_eight_fields_and_no_usage():
    # Remove-cost-tracking §5.3 item 3: nothing ever populated tokens or cost,
    # so the fields are gone rather than left defaulted to None.
    assert set(models.Attempt.model_fields) == {
        "n",
        "dispatch",
        "status",
        "exit_code",
        "duration",
        "prompt_path",
        "result_path",
        "stdout_path",
    }


@pytest.mark.parametrize("retired", ["tokens_in", "tokens_out", "cost"])
def test_attempt_itself_still_forbids_a_retired_usage_key(retired):
    # Old journal lines are reconciled in `store.replay`, never by loosening
    # the model: validating an Attempt payload with a retired key still fails.
    payload = models.Attempt(n=1, dispatch=_dispatch()).model_dump(mode="json")
    payload[retired] = None

    with pytest.raises(ValidationError) as excinfo:
        models.Attempt.model_validate(payload)

    assert [error["loc"] for error in excinfo.value.errors()] == [(retired,)]
```

- [ ] **Step 2: Edit the existing model tests that name the retired fields**

In `tests/test_models.py`, `test_attempt_in_flight_has_no_terminal_fields` (~line 104): delete these three lines and nothing else:

```python
    assert attempt.tokens_in is None
    assert attempt.tokens_out is None
    assert attempt.cost is None
```

In `test_attempt_records_a_finished_outcome` (~line 117), delete these three kwargs (keep `duration=12.5`):

```python
        tokens_in=1200,
        tokens_out=340,
        cost=0.42,
```

Replace both `test_attempt_rejects_negative_usage_numbers` and `test_attempt_rejects_nan_and_infinite_usage_numbers` (~lines 163-181) with:

```python
def test_attempt_rejects_a_negative_duration():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n=1, dispatch=_dispatch(), duration=-5.0)
    assert [error["loc"] for error in excinfo.value.errors()] == [("duration",)]


def test_attempt_rejects_a_nan_or_infinite_duration():
    # `inf >= 0` is true and every nan comparison is false, so a bad clock
    # reading would sail past a plain lower bound.
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            models.Attempt(n=1, dispatch=_dispatch(), duration=bad)
```

In `_full_run`'s explore attempt (~lines 529-531), delete these three kwargs (keep `duration=31.25`):

```python
                                        tokens_in=8000,
                                        tokens_out=1500,
                                        cost=0.31,
```

In `test_round_trip_keeps_an_in_flight_attempt_in_flight` (~line 588), replace:

```python
    assert attempt.cost is None
```

with:

```python
    assert attempt.duration is None
```

- [ ] **Step 3: Write the new failing store tests**

In `tests/test_store.py`, insert directly after `test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it` (ends ~line 2081) and before the `# -- run milestone id ---` comment:

```python
# -- attempts without usage columns ------------------------------------------
#
# Remove-cost-tracking §5.3 items 4-5: a fresh `attempts` table has no
# tokens/cost columns, and a table created before that keeps them, unwritten
# and unread, because migration is additive-only. Unit tier: real temp DB and
# journal, no subprocess.

ATTEMPT_COLUMNS = [
    "run_id",
    "story_id",
    "card_id",
    "phase",
    "n",
    "status",
    "exit_code",
    "duration",
    "prompt_path",
    "result_path",
    "stdout_path",
    "dispatch",
]

LEGACY_ATTEMPT_COLUMNS = [
    *ATTEMPT_COLUMNS[:8],
    "tokens_in",
    "tokens_out",
    "cost",
    *ATTEMPT_COLUMNS[8:],
]
"""The `attempts` columns as they shipped before 2026-10-03, in table order."""


def _attempt_columns(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("PRAGMA table_info(attempts)").fetchall()]


def test_a_fresh_attempts_table_has_no_usage_columns(repo):
    conn = store.open_db(repo)
    try:
        columns = _attempt_columns(conn)
    finally:
        conn.close()

    assert columns == ATTEMPT_COLUMNS
    assert len(columns) == 12


_LEGACY_ATTEMPTS = """
DROP TABLE attempts;
CREATE TABLE attempts (
    run_id       TEXT NOT NULL,
    story_id     TEXT NOT NULL,
    card_id      TEXT NOT NULL,
    phase        TEXT NOT NULL,
    n            INTEGER NOT NULL,
    status       TEXT NOT NULL,
    exit_code    INTEGER,
    duration     REAL,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    cost         REAL,
    prompt_path  TEXT,
    result_path  TEXT,
    stdout_path  TEXT,
    dispatch     TEXT NOT NULL,
    PRIMARY KEY (run_id, story_id, card_id, phase, n)
);
"""
"""The `attempts` table exactly as it shipped before 2026-10-03, empty."""


def test_an_attempts_table_that_still_has_the_usage_columns_keeps_working(repo):
    legacy = store.open_db(repo)
    legacy.executescript(_LEGACY_ATTEMPTS)
    legacy.commit()
    legacy.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        columns = _attempt_columns(st.connection)
        _record_full_run(st, repo)
        # Re-recording implement attempt 1 takes the ON CONFLICT path.
        st.record_attempt(
            "8831189b",
            "ef248597",
            "implement",
            models.Attempt(
                n=1,
                dispatch=_dispatch(card="ef248597", phase="implement"),
                status="ok",
                exit_code=0,
                duration=7.5,
            ),
        )
        usage = [
            tuple(row)
            for row in st.connection.execute(
                "SELECT tokens_in, tokens_out, cost FROM attempts ORDER BY phase, n"
            ).fetchall()
        ]
        loaded = st.load_run(RUN_ID)
        rebuilt = st.rebuild_from_journal(RUN_ID)
        after = st.load_run(RUN_ID)
    finally:
        st.close()

    assert columns == LEGACY_ATTEMPT_COLUMNS
    assert usage == [(None, None, None), (None, None, None)]
    assert loaded is not None
    implement = loaded.stories[0].subtasks[1].phases[1].attempts[0]
    assert (implement.status, implement.exit_code, implement.duration) == ("ok", 0, 7.5)
    assert rebuilt == loaded
    assert after == loaded
```

- [ ] **Step 4: Edit the existing store tests that name the retired fields**

In `tests/test_store.py`, `test_record_story_subtask_phase_and_attempt_write_their_rows` (~line 660), replace:

```python
            models.Attempt(n=1, dispatch=_dispatch(), status="ok", exit_code=0, cost=0.42),
```

with:

```python
            models.Attempt(n=1, dispatch=_dispatch(), status="ok", exit_code=0, duration=4.5),
```

and (~line 676) replace:

```python
        assert attempt_row["cost"] == pytest.approx(0.42)
```

with:

```python
        assert attempt_row["duration"] == pytest.approx(4.5)
```

In `_record_full_run` (~lines 813-815), delete these three kwargs (keep `duration=31.25`):

```python
            tokens_in=8000,
            tokens_out=1500,
            cost=0.31,
```

In `test_load_run_rebuilds_the_tree_in_recorded_order`, replace (~line 861):

```python
    assert finished.cost == pytest.approx(0.31)
```

with:

```python
    assert finished.duration == pytest.approx(31.25)
```

and replace (~line 868):

```python
    assert in_flight.cost is None
```

with:

```python
    assert in_flight.duration is None
```

In `test_an_in_flight_attempt_survives_the_rebuild_as_started` (~lines 954-956), delete these three lines (the `attempt.duration is None` line just above them stays):

```python
    assert attempt.tokens_in is None
    assert attempt.tokens_out is None
    assert attempt.cost is None
```

In `test_diverging_ignores_every_field_but_status` (~line 4783), replace:

```python
    _raw_sql(repo, "UPDATE attempts SET cost = 9.5, exit_code = 42, tokens_in = 7")
```

with:

```python
    _raw_sql(
        repo, "UPDATE attempts SET duration = 9.5, exit_code = 42, stdout_path = '/elsewhere'"
    )
```

- [ ] **Step 5: Tighten the dispatch journal assertions to key absence**

In `tests/test_dispatch.py`, directly after `_terminal_attempts` (ends ~line 665), add:

```python
_USAGE_KEYS = frozenset({"tokens_in", "tokens_out", "cost"})
"""The attempt keys every journal written before 2026-10-03 carries, as null."""
```

In `test_the_outcome_is_journalled_on_the_attempt` (~line 668), replace the leading comment:

```python
    # §5.2, narrowed for this slice: `Attempt` still declares the three fields
    # (3ebd08fb removes them and tightens this to key-absence), so they are
    # asserted `None`. The log is deliberately the old bait -- FakeAdapter's
    # `parse_usage` would turn it into 11/22/0.5 if anything still asked.
```

with:

```python
    # §5.2: `Attempt` no longer declares the three usage fields, so the
    # journalled payload carries no such keys at all. The log is deliberately
    # the old bait -- FakeAdapter's `parse_usage` would turn it into 11/22/0.5
    # if anything still asked.
```

and replace its last three lines (~683-685):

```python
    assert terminal["tokens_in"] is None
    assert terminal["tokens_out"] is None
    assert terminal["cost"] is None
```

with:

```python
    assert _USAGE_KEYS.isdisjoint(terminal)
```

In `test_the_engine_never_opens_the_harness_log` (~lines 752-754), replace:

```python
    assert terminal["tokens_in"] is None
    assert terminal["tokens_out"] is None
    assert terminal["cost"] is None
```

with:

```python
    assert _USAGE_KEYS.isdisjoint(terminal)
```

In `test_a_timed_out_attempt_journals_its_duration_and_no_usage` (~lines 793-795, inside the `for terminal in terminals:` loop, keep the 8-space indent), replace:

```python
        assert terminal["tokens_in"] is None
        assert terminal["tokens_out"] is None
        assert terminal["cost"] is None
```

with:

```python
        assert _USAGE_KEYS.isdisjoint(terminal)
```

- [ ] **Step 6: Run the tests to verify the right ones fail**

Run: `uv run pytest tests/test_models.py tests/test_store.py tests/test_dispatch.py -q`

Expected FAIL (and only these):
- `test_attempt_carries_exactly_eight_fields_and_no_usage` — the field set still includes `tokens_in`, `tokens_out`, `cost`.
- `test_attempt_itself_still_forbids_a_retired_usage_key[tokens_in|tokens_out|cost]` — `DID NOT RAISE`.
- `test_a_fresh_attempts_table_has_no_usage_columns` — the column list has 15 entries.
- `test_the_outcome_is_journalled_on_the_attempt`, `test_the_engine_never_opens_the_harness_log[absent|directory|non_utf8]`, `test_a_timed_out_attempt_journals_its_duration_and_no_usage` — the payload still has the three keys (as null).

Expected PASS already: `test_an_attempts_table_that_still_has_the_usage_columns_keeps_working` (today's code is 15-column; this test is the guard that the narrowed statement in Step 8 keeps working against an old table) and every edited existing test.

- [ ] **Step 7: Remove the three fields from `models.Attempt`**

In `src/agent_manager/models.py`, replace the module docstring's last paragraph (lines 14-16):

```python
Everything terminal is optional: an attempt in flight when the manager died is
recorded as `started` with no exit code, duration, tokens or cost, and resume has
to load that row back before discarding it and re-running the phase.
```

with:

```python
Everything terminal is optional: an attempt in flight when the manager died is
recorded as `started` with no exit code or duration, and resume has to load that
row back before discarding it and re-running the phase.
```

In `class Attempt`, delete these three lines (86-88):

```python
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
```

so the class body reads:

```python
    n: int = Field(gt=0, strict=True)
    dispatch: Dispatch
    status: AttemptStatus = "started"
    exit_code: int | None = None
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    prompt_path: Path | None = None
    result_path: Path | None = None
    stdout_path: Path | None = None
```

- [ ] **Step 8: Stop the store creating, reading and writing the three columns**

In `src/agent_manager/store.py` `_SCHEMA`, replace the attempts table (lines 83-100) with:

```sql
CREATE TABLE IF NOT EXISTS attempts (
    run_id       TEXT NOT NULL,
    story_id     TEXT NOT NULL,
    card_id      TEXT NOT NULL,
    phase        TEXT NOT NULL,
    n            INTEGER NOT NULL,
    status       TEXT NOT NULL,
    exit_code    INTEGER,
    duration     REAL,
    prompt_path  TEXT,
    result_path  TEXT,
    stdout_path  TEXT,
    dispatch     TEXT NOT NULL,
    PRIMARY KEY (run_id, story_id, card_id, phase, n)
);
```

In `open_db`'s docstring, replace:

```python
    the new column as NULL. It is a no-op on a database that already has the
    column, so opening the same database any number of times is safe.
```

with:

```python
    the new column as NULL. It is a no-op on a database that already has the
    column, so opening the same database any number of times is safe. Nothing
    is ever dropped: an `attempts` table created before 2026-10-03 keeps its
    `tokens_in`, `tokens_out` and `cost` columns, which nothing writes or reads
    any more, so they stay NULL.
```

In module-level `load_run` (~lines 920-932), replace the `models.Attempt(...)` call with:

```python
                        models.Attempt(
                            n=attempt_row["n"],
                            dispatch=json.loads(attempt_row["dispatch"]),
                            status=attempt_row["status"],
                            exit_code=attempt_row["exit_code"],
                            duration=attempt_row["duration"],
                            prompt_path=attempt_row["prompt_path"],
                            result_path=attempt_row["result_path"],
                            stdout_path=attempt_row["stdout_path"],
                        )
```

Replace the body of `Store._write_attempt_row` (~lines 1604-1644) with:

```python
        self._conn.execute(
            """
            INSERT INTO attempts (run_id, story_id, card_id, phase, n, status,
                                  exit_code, duration,
                                  prompt_path, result_path, stdout_path, dispatch)
            VALUES (:run_id, :story_id, :card_id, :phase, :n, :status,
                    :exit_code, :duration,
                    :prompt_path, :result_path, :stdout_path, :dispatch)
            ON CONFLICT(run_id, story_id, card_id, phase, n) DO UPDATE SET
                status=excluded.status,
                exit_code=excluded.exit_code,
                duration=excluded.duration,
                prompt_path=excluded.prompt_path,
                result_path=excluded.result_path,
                stdout_path=excluded.stdout_path,
                dispatch=excluded.dispatch
            """,
            {
                "run_id": run_id,
                "story_id": story_id,
                "card_id": card_id,
                "phase": phase_name,
                "n": attempt.n,
                "status": attempt.status,
                "exit_code": attempt.exit_code,
                "duration": attempt.duration,
                "prompt_path": _text(attempt.prompt_path),
                "result_path": _text(attempt.result_path),
                "stdout_path": _text(attempt.stdout_path),
                "dispatch": json.dumps(
                    attempt.dispatch.model_dump(mode="json"), sort_keys=True
                ),
            },
        )
        self._commit()
```

Then confirm nothing in `src/` still names the fields on an attempt: search `src/agent_manager/models.py` and `src/agent_manager/store.py` for `tokens_in|tokens_out|cost` — expected: no matches except the `open_db` docstring sentence just added. (`src/agent_manager/harness/` still matches; that is sibling 7988f1db's and stays.)

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py tests/test_store.py tests/test_dispatch.py -q`
Expected: PASS (all).

Run: `uv run pytest`
Expected: PASS. Journals written by today's code no longer carry the three keys, so no existing replay test needs the Task 2 shim.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/models.py src/agent_manager/store.py tests/test_models.py tests/test_store.py tests/test_dispatch.py
git commit -m "cleanup: drop Attempt's tokens/cost fields and stop the store writing their columns"
```

---

### Task 2: Old journals still replay — the attempt-only retired-key shim

**Files:**
- Modify: `src/agent_manager/store.py:341` (add constant and helper after `_EVENT_KINDS`), `:559-618` (`replay` docstring and attempt branch), `:652-659` (`_NODE_MODELS` docstring), `:698-704` (`_journaled_statuses`)
- Test: `tests/test_store.py` (new tests after `test_a_line_with_an_unknown_payload_key_raises_out_of_rebuild`, ~line 1019)
- Test: `tests/test_dispatch.py` (new test after `test_a_source_journal_that_fails_validation_declines`, ~line 2493)

**Interfaces:**
- Consumes: Task 1's eight-field `models.Attempt`; `tests/test_dispatch.py`'s `_USAGE_KEYS` from Task 1; existing test helpers `_record_full_run`, `_append_raw`, `_dispatch`, `_run`, `_story`, `_subtask` (`tests/test_store.py`) and `_succeed_once`, `_terminal_attempts`, `_runner`, `_passing_phase`, `_context`, `_declines`, `_reused`, `FakeLauncher`, `EXPLORED`, `OTHER_RUN_ID`, `STORY_ID`, `CARD`, `VALID_RESULT` (`tests/test_dispatch.py`).
- Produces: `store._RETIRED_ATTEMPT_KEYS: frozenset[str]` and `store._current_attempt_payload(payload: dict[str, Any]) -> dict[str, Any]` (returns a new dict; never mutates its argument).

- [ ] **Step 1: Write the failing store tests**

In `tests/test_store.py`, insert directly after `test_a_line_with_an_unknown_payload_key_raises_out_of_rebuild` (ends ~line 1018):

```python
# -- retired attempt usage keys (remove-cost-tracking §4.3) -------------------
#
# Every journal written before 2026-10-03 carries `tokens_in`, `tokens_out`
# and `cost` on each attempt line. `Attempt` no longer declares them, so
# `replay` sheds exactly those three names from an attempt payload and stays
# strict about everything else. Unit tier: real temp DB and journal.

_RETIRED_NULL = {"tokens_in": None, "tokens_out": None, "cost": None}
_RETIRED_SET = {"tokens_in": 8000, "tokens_out": 1500, "cost": 0.31}


def _append_old_attempt(journal: store.Journal, extra: dict) -> None:
    """Re-record implement attempt 1 as `ok`, the way an `am` from before
    2026-10-03 wrote it: today's payload plus `extra`."""
    payload = models.Attempt(
        n=1,
        dispatch=_dispatch(card="ef248597", phase="implement"),
        status="ok",
        exit_code=0,
        duration=12.5,
    ).model_dump(mode="json")
    _append_raw(
        journal,
        {
            "seq": journal.last_seq() + 1,
            "ts": "2026-09-23T10:20:00+00:00",
            "run_id": RUN_ID,
            "event": "attempt_upsert",
            "story": "8831189b",
            "card": "ef248597",
            "phase": "implement",
            "attempt": 1,
            "payload": {**payload, **extra},
        },
    )


@pytest.mark.parametrize("retired", [_RETIRED_NULL, _RETIRED_SET], ids=["null", "non_null"])
def test_an_attempt_line_carrying_the_retired_usage_keys_still_replays(repo, retired):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        _append_old_attempt(st.journal, retired)
        replayed = st.replay_journal(RUN_ID)
        # The projection already holds this run, so the rebuild runs its
        # `diverging` pre-check over the same old lines first.
        rebuilt = st.rebuild_from_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
        assert loaded is not None
        mismatches = store.diverging(st.journal.read(), loaded)
    finally:
        st.close()

    assert replayed == rebuilt == loaded
    attempt = rebuilt.stories[0].subtasks[1].phases[1].attempts[0]
    assert (attempt.n, attempt.status, attempt.exit_code, attempt.duration) == (
        1,
        "ok",
        0,
        12.5,
    )
    for key in retired:
        assert not hasattr(attempt, key)
    assert set(retired).isdisjoint(attempt.model_dump())
    assert mismatches == []


@pytest.mark.parametrize("read", ["replay_journal", "rebuild_from_journal"])
def test_an_attempt_line_with_any_other_unknown_key_still_raises_naming_only_it(
    repo, read
):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        _append_old_attempt(st.journal, {**_RETIRED_NULL, "operator": "x"})
        with pytest.raises(ValidationError) as excinfo:
            getattr(st, read)(RUN_ID)
    finally:
        st.close()

    assert [error["loc"] for error in excinfo.value.errors()] == [("operator",)]
    assert "operator" in str(excinfo.value)


@pytest.mark.parametrize(
    "event", ["run_upsert", "story_upsert", "subtask_upsert", "phase_upsert"]
)
def test_only_attempt_lines_shed_the_retired_usage_keys(repo, event):
    # No other node ever carried these keys, so on any other line they are
    # still an unknown key and still fail loudly.
    coordinates, payload = {
        "run_upsert": ({}, _run(repo).model_dump(mode="json", exclude={"stories"})),
        "story_upsert": (
            {"story": "8831189b"},
            _story().model_dump(mode="json", exclude={"subtasks"}),
        ),
        "subtask_upsert": (
            {"story": "8831189b"},
            _subtask("ef248597", base="m1/task-fdebc746").model_dump(
                mode="json", exclude={"phases"}
            ),
        ),
        "phase_upsert": (
            {"story": "8831189b", "card": "ef248597"},
            models.PhaseRun(name="implement", kind="agent", status="started").model_dump(
                mode="json", exclude={"attempts"}
            ),
        ),
    }[event]
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        _append_raw(
            st.journal,
            {
                "seq": st.journal.last_seq() + 1,
                "ts": "2026-09-23T10:20:00+00:00",
                "run_id": RUN_ID,
                "event": event,
                **coordinates,
                "payload": {**payload, "cost": None},
            },
        )
        with pytest.raises(ValidationError) as excinfo:
            st.replay_journal(RUN_ID)
    finally:
        st.close()

    assert [error["loc"] for error in excinfo.value.errors()] == [("cost",)]
```

- [ ] **Step 2: Write the failing resume-adoption test**

In `tests/test_dispatch.py`, insert directly after `test_a_source_journal_that_fails_validation_declines` (ends ~line 2492):

```python
def test_a_source_run_whose_ok_attempt_carries_retired_usage_keys_is_still_adopted(
    store, tmp_path, worktree
):
    # Remove-cost-tracking §5.3 item 2: every journal written before
    # 2026-10-03 carries `tokens_in`/`tokens_out`/`cost` (null) on its attempt
    # lines. `adopt` turns replay's ValidationError into a silent decline, so
    # without the replay shim every old run would quietly redispatch.
    _succeed_once(store, tmp_path, worktree)
    [ok] = _terminal_attempts(store)
    store.journal.append(
        "attempt_upsert",
        {**ok, **dict.fromkeys(_USAGE_KEYS)},
        story=STORY_ID,
        card=CARD,
        phase="explore",
        attempt=1,
    )
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        launcher = FakeLauncher(results=[VALID_RESULT])
        runner, _ = _runner(other, launcher, tmp_path, worktree, run_id=OTHER_RUN_ID)
        adopted = runner.adopt(
            _passing_phase(), _context(worktree), source_run=RUN_ID, floor=0
        )
    finally:
        other.close()

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)
    assert launcher.calls == []
    assert _declines(runner) == []
    assert runner.warnings == [_reused(1)]
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_store.py tests/test_dispatch.py -q -k "retired_usage_keys or any_other_unknown_key"`

Expected FAIL:
- `test_an_attempt_line_carrying_the_retired_usage_keys_still_replays[null|non_null]` — `ValidationError ... tokens_in Extra inputs are not permitted` out of `replay_journal`.
- `test_an_attempt_line_with_any_other_unknown_key_still_raises_naming_only_it[replay_journal|rebuild_from_journal]` — the error locs are `[("tokens_in",), ("tokens_out",), ("cost",), ("operator",)]` (order as pydantic reports), not `[("operator",)]`.
- `test_a_source_run_whose_ok_attempt_carries_retired_usage_keys_is_still_adopted` — `adopted` is `None` and `_declines(runner)` holds one "its journal cannot be read: ValidationError" warning.

Expected PASS already: `test_only_attempt_lines_shed_the_retired_usage_keys[...]` (the guard that the shim stays scoped to attempt lines; it must still pass after Step 4).

- [ ] **Step 4: Add the shim and route both attempt-validating call sites through it**

In `src/agent_manager/store.py`, directly after `_EVENT_KINDS: frozenset[str] = frozenset(get_args(EventKind))` (line 341), add:

```python


_RETIRED_ATTEMPT_KEYS: frozenset[str] = frozenset({"tokens_in", "tokens_out", "cost"})
"""Attempt payload keys dropped before validation (remove-cost-tracking design,
docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md §4.3).

Every journal written before 2026-10-03 carries them on each `attempt_upsert`
line, as null. `models.Attempt` no longer declares them and forbids unknown
keys, so without this every old journal would stop replaying: the projection
could not be rebuilt, and resume adoption would silently decline every old run.
A named allow-list rather than `extra="ignore"`: any other unknown key still
fails, and only attempt payloads are touched."""


def _current_attempt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """`payload` without `_RETIRED_ATTEMPT_KEYS`, whatever their values, as a
    new dict. The journal line's own payload is never mutated."""
    return {
        key: value for key, value in payload.items() if key not in _RETIRED_ATTEMPT_KEYS
    }
```

In `replay`, replace the docstring:

```python
    """Fold journal lines, in sequence order, back into the §9 tree.

    Nothing here is defensive: a line that fails `models` validation raises the
    `pydantic.ValidationError` straight out, because an old-schema line has to
    fail loudly rather than quietly drop a field from the projection.
    """
```

with:

```python
    """Fold journal lines, in sequence order, back into the §9 tree.

    Nothing here is defensive: a line that fails `models` validation raises the
    `pydantic.ValidationError` straight out, because an old-schema line has to
    fail loudly rather than quietly drop a field from the projection. The one
    exception is `_RETIRED_ATTEMPT_KEYS`: an `attempt_upsert` payload sheds
    those three named keys, which every pre-2026-10-03 journal carries, before
    it is validated, and any other unknown key still raises.
    """
```

and replace the attempt branch's last line:

```python
        _upsert(phase.attempts, "n", models.Attempt.model_validate(line.payload), None)
```

with:

```python
        attempt = models.Attempt.model_validate(_current_attempt_payload(line.payload))
        _upsert(phase.attempts, "n", attempt, None)
```

Replace the `_NODE_MODELS` docstring:

```python
"""The model each event's payload validates as, exactly as `replay` reads it."""
```

with:

```python
"""The model each event's payload validates as, exactly as `replay` reads it
(an `attempt_upsert` payload first sheds `_RETIRED_ATTEMPT_KEYS`)."""
```

Replace the body of `_journaled_statuses`:

```python
    seen: dict[_NodeKey, set[str]] = {}
    for line in sorted(lines, key=lambda item: item.seq):
        node: Any = _NODE_MODELS[line.event].model_validate(line.payload)
        seen.setdefault(_line_node(line, node), set()).add(node.status)
    return seen
```

with:

```python
    seen: dict[_NodeKey, set[str]] = {}
    for line in sorted(lines, key=lambda item: item.seq):
        payload = (
            _current_attempt_payload(line.payload)
            if line.event == "attempt_upsert"
            else line.payload
        )
        node: Any = _NODE_MODELS[line.event].model_validate(payload)
        seen.setdefault(_line_node(line, node), set()).add(node.status)
    return seen
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_store.py tests/test_dispatch.py -q -k "retired_usage_keys or any_other_unknown_key"`
Expected: PASS (all, including the `only_attempt_lines` guard).

Run: `uv run pytest tests/test_store.py -q -k "diverging or unknown_payload_key"`
Expected: PASS — in particular `test_diverging_mutates_neither_its_lines_nor_its_projection` (the helper builds a new dict) and `test_a_line_with_an_unknown_payload_key_raises_out_of_rebuild` (`tokens` is not a retired key, so it still raises).

- [ ] **Step 6: Run the full default suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py tests/test_dispatch.py
git commit -m "fix: replay sheds retired attempt usage keys so pre-2026-10-03 journals still load"
```
