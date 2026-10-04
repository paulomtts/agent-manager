<!-- task-pipeline: validated -->
# 2.2 `am runs`: lease (card 6bf47e74)

Parent story: e187d6f8 "Richer `am runs`". Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §1 ("Richer `am runs`"). This document narrows that section to the `lease` key only.

## Prerequisite

This card builds on 2.1 (0b5a15d7, done). 2.1 added `milestone_id` / `card_id` to `RunSummary` and changed `list_runs`. That code lives on branch `ami/task-2-1-am-runs-milestone-0b5a15d7` (commits d33a193, bc33efb, cecc008) and is NOT on master at 09e8b80. The working branch `ami/task-2-2-am-runs-lease-6bf47e74` is based on it (HEAD cecc008, verified). Before editing, run `git log --oneline` to confirm this still holds; if it does not, stop and report the problem. Do not reimplement 2.1.

## Scope

- `RunSummary` (`src/agent_manager/store.py`, `extra="forbid"`) gains one optional field, `lease`, which defaults to `None`, following 2.1's pattern for `milestone_id` / `card_id`. Its value is either `null` or an object with exactly these keys:
  `{ live: bool, pid: int | null, host: str | null, heartbeat_at: str | null, accepting: bool }`
  Use a small pydantic sub-model (also `extra="forbid"`) or an equivalent typed shape that validates under `extra="forbid"`. `LeaseRow` has non-null `pid`, `host` and `heartbeat_at`, so real output always has them populated; the `| null` in the type is the source design's wording. Declare the sub-model fields as `pid: int | None`, `host: str | None`, `heartbeat_at: str | None` to match it.
- `am runs` (`runs_for` in `src/agent_manager/cli.py`) fills `lease` for each run in `data.runs[]`. `store.list_runs` stays unchanged (it returns summaries with `lease=None`). `runs_for` then, for each summary, reads `store_module.read_lease(conn, summary.id)` on the same connection, with one `now = _utcnow()` for the whole listing, and attaches the result with `summary.model_copy(update={"lease": ...})` before `model_dump()`. A nested sub-model instance dumps to a plain dict, so the envelope stays JSON.
- `live` comes from the same computation as `am status`'s `control.lease`, which is `control.lease_is_live(lease, now=now)` (fresh heartbeat within 30s inclusive, and the pid alive or the lease on another host). It is computed at read time and never stored. `heartbeat_at` is emitted as an ISO string (`.isoformat()`), the same as `control_view`. `acquired_at` is not included.
- Extract a small shared helper in `cli.py` that turns a `LeaseRow` plus `now` into the common fields (pid, host, heartbeat_at, accepting, live). Both `control_view`'s `lease` and the runs `lease` use it, so the two outputs cannot drift. `control_view` adds `acquired_at` on top, and its output must not change.
- Layering: `store.py` must not import `control` (`control.py` already imports `store`, so that would be circular). Compute `live` in `cli.py`. `store.list_runs` takes no part in computing `live`.
- README `am runs` section: document `lease` next to 2.1's `milestone_id` / `card_id`, and keep or add the sentence telling consumers to ignore unknown keys.

## Observable behavior

- A run with a lease row has `lease` as the object above. `live` is true or false according to `lease_is_live` at the time of the call.
- A run with no lease row has `lease: null`.
- The change is additive only. The journal stays schema 1. No existing `runs[]` key is renamed, retyped or removed. Existing fixtures without lease rows still produce the same output as before, plus `"lease": null`.
- No error paths are added. A missing lease row is `null`, not an error.

## Non-goals

- `progress` (2.3, 882b212b). Do not add it.
- Redoing `milestone_id` / `card_id` (2.1).
- `--detach`, `logs --follow` and `--from-now` belong to other stories.
- Any change to the lease, claim, control-request or journal-line contracts. Do not change `am status` output either.

## Tests (write first)

All of these read and write SQLite in tmp dirs through injected or planted state, with no subprocess. Under the CLAUDE.md "Test tiers" placement rule they are unmarked `unit` tests.

`tests/test_cli.py` (next to the existing `runs` tests at about lines 4120-4200, using the lease helpers at about lines 6233-6290: `_plant_run`, `_plant_lease`, `_at`, `_freeze_clock`, `CONTROL_NOW`, and the frozen-clock pattern from the status `control.lease` tests at about lines 6649-6720):
1. `runs` live lease: the default `_plant_lease` (this pid and host, fresh heartbeat) gives `lease == {live: true, pid, host, heartbeat_at: <iso str>, accepting}` with exactly those keys. Tier: unit.
2. `runs` dead lease: a stale heartbeat (`_at(-31)`) and/or a dead pid on the same host gives `live: false`, with the other fields still populated. Tier: unit.
3. `runs` no lease row: `lease is None` (JSON `null`). Tier: unit.
4. Parity: for the same planted lease and frozen clock, the `am runs` lease equals `am status`'s `control.lease` minus `acquired_at`. Tier: unit.
5. Shape pin: the `runs[]` lease key set is exactly `{live, pid, host, heartbeat_at, accepting}`, placed next to the existing contract or shape tests. The existing `runs` tests keep passing, with `lease` null on old fixtures. Tier: unit.

`tests/test_store.py` (the list_runs tests at about lines 1265-1340, `_record_summary`):
6. `RunSummary` accepts `lease=None` (the default) and a valid lease object, and rejects an unknown key inside `lease` (`extra="forbid"`). Tier: unit.

---

# `am runs` lease Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each `am runs` entry gains a `lease` key, `{live, pid, host, heartbeat_at, accepting}` or `null`, computed exactly as `am status`'s `control.lease` (minus `acquired_at`).

**Architecture:** `store.py` gains a pydantic sub-model `RunLease` (`extra="forbid"`) and an optional `RunSummary.lease: RunLease | None = None`; `store.list_runs` is untouched and always returns `lease=None`. `cli.py` gains one private helper `_lease_fields(lease, *, now)` that both `control_view` (adding `acquired_at` on top) and `runs_for` use; `runs_for` reads each run's lease on the same connection with a single `now = _utcnow()` and attaches it with `model_copy`. `store.py` never imports `control` (that would be circular).

**Tech Stack:** Python, Pydantic v2, Typer, SQLite, pytest (run via `uv run pytest`).

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-2-2-am-runs-lease-6bf47e74/docs/superpowers/specs/task-2-2-am-runs-lease-6bf47e74-design.md` (prepended above).

All paths below are relative to the worktree root `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-2-2-am-runs-lease-6bf47e74`. Run every command from that directory.

## Global Constraints

- The lease object has exactly these keys: `live: bool`, `pid: int | null`, `host: str | null`, `heartbeat_at: str | null`, `accepting: bool`. No `acquired_at`.
- `RunSummary.lease` defaults to `None`; the sub-model is `extra="forbid"`; `RunSummary` stays `extra="forbid"`.
- `live` is `control.lease_is_live(lease, now=now)`, computed at read time in `cli.py`, never stored.
- `heartbeat_at` is emitted as `.isoformat()`, the same as `control_view`.
- One `now = _utcnow()` for the whole `am runs` listing.
- `store.py` must not import `control`.
- `control_view` output (and so `am status` output) must not change.
- Additive only: journal stays schema 1; no existing `runs[]` key renamed, retyped or removed; no change to the lease, claim, control-request or journal-line contracts.
- Do not add `progress` (2.3); do not redo `milestone_id` / `card_id` (2.1); do not touch `--detach`, `logs --follow`, `--from-now`.
- All new tests are unmarked (`unit` tier): SQLite in tmp dirs, no subprocess. Pids used for "dead" are `0` (never alive per `control.pid_alive`), never a reaped child process.
- Verification: `uv run pytest`.

## Review Focus

- Several runs, only one holding a lease: each entry must carry its own lease and a run with no lease row must stay `null`, never inherit a neighbour's row. Pinned in Task 2 (`test_runs_attaches_each_runs_own_lease_and_reads_the_clock_once`).
- The 30-second staleness boundary is inclusive: a heartbeat exactly 30s old is still `live: true`. Pinned in Task 2 (`boundary-30s` case of the live test).
- A lease held on another host with a pid that is dead here must read as `live: true` (this host cannot probe it), exactly as `am status` says. Pinned in Task 2 (`other-host-unprobed-pid` case).
- A lease whose control window closed (`accepting = 0`) must show `accepting: false` while `live` stays true. Pinned in Task 2 (`not-accepting` case).
- The listing must read the clock once, so two runs cannot be judged against different instants. Pinned in Task 2 (same test as the first line, counting `_utcnow` calls).

---

### Task 1: `RunLease` sub-model and `RunSummary.lease`

**Files:**
- Modify: `src/agent_manager/store.py:588-614` (add `RunLease` above `RunSummary`, add the `lease` field and a docstring paragraph)
- Test: `tests/test_store.py:1354-1365` (`SUMMARY_KEYS`), `tests/test_store.py:1479-1491` (existing summary tests), plus new tests inserted after `test_a_listed_run_dumps_exactly_the_summary_keys` (ends line 1491)

**Interfaces:**
- Consumes: nothing new.
- Produces: `store.RunLease(BaseModel)` with fields `live: bool`, `pid: int | None`, `host: str | None`, `heartbeat_at: str | None`, `accepting: bool`, all required, `model_config = ConfigDict(extra="forbid")`. `store.RunSummary.lease: RunLease | None = None`. `store.list_runs(conn)` unchanged and returns `lease=None` on every summary.

- [ ] **Step 0: Confirm the branch contains 2.1**

Run: `git log --oneline -5`
Expected: `cecc008` (2.1's test commit) is in the list, and `grep -n "card_id: str | None = None" src/agent_manager/store.py` prints a line inside `RunSummary`. If either is missing, STOP and report: this card must not reimplement 2.1.

- [ ] **Step 1: Write the failing tests**

In `tests/test_store.py`, replace the `SUMMARY_KEYS` block (lines 1354-1365) with:

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
}
"""The seven names `am runs` always had, plus `milestone_id` and `card_id`
(card 0b5a15d7) and `lease` (card 6bf47e74)."""
```

Replace `test_run_summary_fields_are_the_old_seven_plus_milestone_id_and_card_id` (lines 1479-1483) with:

```python
def test_run_summary_fields_are_the_old_seven_plus_milestone_id_card_id_and_lease():
    assert set(store.RunSummary.model_fields) == SUMMARY_KEYS
    assert store.RunSummary.model_config["extra"] == "forbid"
    assert store.RunSummary.model_fields["milestone_id"].default is None
    assert store.RunSummary.model_fields["card_id"].default is None
    assert store.RunSummary.model_fields["lease"].default is None
```

Then insert, directly after `test_a_listed_run_dumps_exactly_the_summary_keys` (after line 1491), these tests:

```python
RUN_LEASE_KEYS = {"live", "pid", "host", "heartbeat_at", "accepting"}
"""The `runs[]` lease object: `am status`'s `control.lease` minus `acquired_at`."""


def _summary_fields(**overrides) -> dict:
    """The fields of one valid `RunSummary`, without the database."""
    return {
        "id": "run-x",
        "workflow": "task",
        "repo_dir": Path("/repo"),
        "base_branch": "main",
        "branch_prefix": "m1",
        "status": "started",
        **overrides,
    }


def _run_lease_fields(**overrides) -> dict:
    """One valid `RunLease` as a plain dict."""
    return {
        "live": True,
        "pid": 4242,
        "host": "box",
        "heartbeat_at": "2026-09-29T09:00:00+00:00",
        "accepting": True,
        **overrides,
    }


def test_run_lease_has_exactly_the_five_keys_and_forbids_others():
    assert set(store.RunLease.model_fields) == RUN_LEASE_KEYS
    assert store.RunLease.model_config["extra"] == "forbid"


def test_run_summary_lease_defaults_to_none():
    summary = store.RunSummary.model_validate(_summary_fields())

    assert summary.lease is None
    assert summary.model_dump()["lease"] is None


def test_run_summary_accepts_a_lease_object_and_dumps_it_as_a_plain_dict():
    summary = store.RunSummary.model_validate(_summary_fields(lease=_run_lease_fields()))

    assert isinstance(summary.lease, store.RunLease)
    assert summary.model_dump()["lease"] == _run_lease_fields()


def test_run_summary_accepts_null_pid_host_and_heartbeat_in_a_lease():
    """The source design types these three as nullable; the model must agree."""
    summary = store.RunSummary.model_validate(
        _summary_fields(lease=_run_lease_fields(pid=None, host=None, heartbeat_at=None))
    )

    assert summary.lease is not None
    assert (summary.lease.pid, summary.lease.host, summary.lease.heartbeat_at) == (
        None,
        None,
        None,
    )


def test_run_summary_rejects_an_unknown_key_inside_the_lease():
    """The error must sit at `lease.acquired_at`: a `RunSummary` with no
    `lease` field at all would also raise, but at `lease`, the wrong reason."""
    with pytest.raises(ValidationError) as caught:
        store.RunSummary.model_validate(
            _summary_fields(
                lease=_run_lease_fields(acquired_at="2026-09-29T08:59:00+00:00")
            )
        )

    [error] = caught.value.errors()
    assert error["loc"] == ("lease", "acquired_at")
    assert error["type"] == "extra_forbidden"


def test_run_summary_rejects_a_lease_missing_a_key():
    fields = _run_lease_fields()
    del fields["accepting"]

    with pytest.raises(ValidationError) as caught:
        store.RunSummary.model_validate(_summary_fields(lease=fields))

    [error] = caught.value.errors()
    assert error["loc"] == ("lease", "accepting")
    assert error["type"] == "missing"


def test_list_runs_leaves_the_lease_to_the_caller_even_with_a_lease_row(repo):
    """`live` needs `control`, which `store` must not import, so `list_runs`
    never fills `lease`: `am runs` does, in `cli`."""
    _record_summary(repo, "run-l", None, workflow="task")
    _plant_lease(repo, "run-l", token="life-1")

    [summary] = _listed(repo)

    assert summary.lease is None


def test_store_does_not_import_control():
    """`control` imports `store`; the reverse would be a circular import."""
    source = Path(store.__file__).read_text()

    assert "from agent_manager import control" not in source
    assert "from agent_manager.control" not in source
    assert "import agent_manager.control" not in source
```

(`_plant_lease`, `_record_summary`, `_listed`, `Path`, `pytest` and `ValidationError` already exist in `tests/test_store.py`; `_plant_lease` is defined at line 3169 and resolved at call time.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "run_summary or run_lease or summary_keys or list_runs_leaves or store_does_not_import_control" -v`
Expected: FAIL. `test_run_summary_fields_are_the_old_seven_plus_milestone_id_card_id_and_lease` and `test_a_listed_run_dumps_exactly_the_summary_keys` fail on the key set (no `lease`); `test_run_lease_has_exactly_the_five_keys_and_forbids_others` fails with `AttributeError: module 'agent_manager.store' has no attribute 'RunLease'`; `test_run_summary_lease_defaults_to_none` and `test_list_runs_leaves_the_lease_to_the_caller_even_with_a_lease_row` fail with `AttributeError` on `.lease`; the accepts-a-lease tests raise `ValidationError` (`lease` is an extra field on `RunSummary`); the two reject tests fail on `error["loc"] == ("lease",)` instead of the nested location. `test_store_does_not_import_control` passes already: it is a layering guard, not a driver.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, insert directly above `class RunSummary(BaseModel):` (line 588):

```python
class RunLease(BaseModel):
    """The `lease` of one `am runs` entry: `am status`'s `control.lease`
    without `acquired_at`.

    Filled by `cli.runs_for`, never by `list_runs`: `live` is
    `control.lease_is_live` at read time, and `control` imports this module,
    so the reverse import would be circular. `pid`, `host` and `heartbeat_at`
    are nullable to match the published type, though a real `run_leases` row
    always fills them. `heartbeat_at` is an ISO string, as in `control_view`.
    """

    model_config = ConfigDict(extra="forbid")

    live: bool
    pid: int | None
    host: str | None
    heartbeat_at: str | None
    accepting: bool


```

Then in `RunSummary`, append this paragraph to the end of the docstring (after "...so they are additive: no older key changed."):

```python

    `lease` is the run's `run_leases` row as a `RunLease`, or `None` when the
    run has no lease row. `list_runs` always leaves it `None`; `am runs`
    fills it in `cli`. It too defaults to `None`, so it is additive.
```

and add the field after `card_id: str | None = None` (line 614):

```python
    lease: RunLease | None = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS, every test in the file (including the legacy-migration and replay tests that call `list_runs`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): RunSummary gains an optional lease sub-model (6bf47e74)"
```

---

### Task 2: `am runs` fills `lease` through a helper shared with `control_view`, plus README

**Files:**
- Modify: `src/agent_manager/cli.py:202-205` (`RUN_IDENTITY` docstring), `src/agent_manager/cli.py:248-281` (`control_view`, new `_lease_fields` above it), `src/agent_manager/cli.py:1483-1496` (`runs_for`)
- Modify: `README.md:437-443` (`### Listing runs`)
- Test: `tests/test_cli.py:4182-4194` (`RUNS_ENTRY_KEYS`), new tests inserted after `test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id` (ends line 4256)

**Interfaces:**
- Consumes: `store_module.RunLease(live: bool, pid: int | None, host: str | None, heartbeat_at: str | None, accepting: bool)` and `store_module.RunSummary.lease: RunLease | None = None` from Task 1; existing `store_module.read_lease(conn, run_id) -> LeaseRow | None`, `control.lease_is_live(lease, *, now) -> bool`, `cli._utcnow() -> datetime`.
- Produces: `cli._lease_fields(lease: store_module.LeaseRow, *, now: datetime) -> dict[str, Any]` returning exactly `{"pid", "host", "heartbeat_at", "accepting", "live"}`; `cli.runs_for(*, repo_dir)` entries carry `"lease"` (dict or `None`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, replace the `RUNS_ENTRY_KEYS` block (lines 4182-4194) with:

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
}
"""Every `data.runs[]` entry: the seven names `am runs` always had, plus
`milestone_id` and `card_id` (card 0b5a15d7) and `lease` (card 6bf47e74)."""

RUNS_LEASE_KEYS = {"live", "pid", "host", "heartbeat_at", "accepting"}
"""A non-null `data.runs[].lease`: `am status`'s `control.lease` minus
`acquired_at`."""
```

Then insert, directly after `test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id` (after line 4256), the tests below. They use `_freeze_clock`, `_plant_run`, `_plant_lease`, `_at`, `CONTROL_RUN_ID` and `HERE`, which are defined further down the module (around lines 6232-6306) and are resolved when the test body runs. For that reason the `parametrize` lists hold plain offsets and `None` sentinels, never `_at(...)` or `HERE`, which would not exist yet at import time.

```python
RUNS_NEWER_RUN_ID = "20260930T090000Z-cbe34d00"
"""A second run, started after `CONTROL_RUN_ID`'s `RECORDED_AT`, so it lists first."""


@pytest.mark.parametrize(
    "heartbeat_offset, pid, host, accepting",
    [
        (0, None, None, True),
        (-30, None, None, True),
        (-5, 0, "elsewhere.invalid", True),
        (-5, None, None, False),
    ],
    ids=["fresh-here", "boundary-30s", "other-host-unprobed-pid", "not-accepting"],
)
def test_runs_shows_a_live_lease(
    projection, monkeypatch, heartbeat_offset, pid, host, accepting
):
    """Spec test 1, plus Review Focus: the 30s boundary is inclusive, another
    host's pid is never probed here, and a closed control window still reads
    live. `pid`/`host` `None` mean `_plant_lease`'s default: this process,
    this host."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(
        projection,
        pid=pid,
        host=host,
        heartbeat_at=_at(heartbeat_offset),
        accepting=accepting,
    )

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["lease"] == {
        "live": True,
        "pid": os.getpid() if pid is None else pid,
        "host": HERE if host is None else host,
        "heartbeat_at": _at(heartbeat_offset).isoformat(),
        "accepting": accepting,
    }


@pytest.mark.parametrize(
    "heartbeat_offset, pid",
    [
        (-31, None),
        (-5, 0),
    ],
    ids=["stale-heartbeat", "dead-pid-here"],
)
def test_runs_shows_a_dead_lease_with_its_fields_still_filled(
    projection, monkeypatch, heartbeat_offset, pid
):
    """Spec test 2. Pid 0 is never alive (`control.pid_alive`), so no process
    has to be spawned and reaped to get a dead pid on this host."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, pid=pid, heartbeat_at=_at(heartbeat_offset))

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["lease"] == {
        "live": False,
        "pid": os.getpid() if pid is None else pid,
        "host": HERE,
        "heartbeat_at": _at(heartbeat_offset).isoformat(),
        "accepting": True,
    }


def test_runs_shows_a_null_lease_for_a_run_with_no_lease_row(projection, monkeypatch):
    """Spec test 3: no row is `null`, not an error and not a missing key."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    assert '"lease":null' in result.stdout
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert "lease" in entry
    assert entry["lease"] is None


@pytest.mark.parametrize("heartbeat_offset", [-5, -31], ids=["live", "stale"])
def test_runs_lease_is_status_control_lease_without_acquired_at(
    projection, monkeypatch, heartbeat_offset
):
    """Spec test 4: one computation, two commands, no drift."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, heartbeat_at=_at(heartbeat_offset))

    listed = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])
    reported = runner.invoke(
        cli.app, ["status", CONTROL_RUN_ID, "--repo-dir", str(projection)]
    )

    assert listed.exit_code == 0, listed.output
    assert reported.exit_code == 0, reported.output
    [entry] = json.loads(listed.stdout)["data"]["runs"]
    status_lease = json.loads(reported.stdout)["data"]["control"]["lease"]
    assert "acquired_at" in status_lease
    assert entry["lease"] == {
        key: value for key, value in status_lease.items() if key != "acquired_at"
    }


def test_runs_lease_has_exactly_the_five_keys_plain_and_pretty(projection, monkeypatch):
    """Spec test 5: the shape pin, beside `RUNS_ENTRY_KEYS`'s own."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection)
    argv = ["runs", "--repo-dir", str(projection)]

    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])

    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for envelope in (json.loads(plain.stdout), json.loads(pretty.stdout)):
        [entry] = envelope["data"]["runs"]
        assert set(entry) == RUNS_ENTRY_KEYS
        assert set(entry["lease"]) == RUNS_LEASE_KEYS
        assert isinstance(entry["lease"]["live"], bool)
        assert isinstance(entry["lease"]["pid"], int)
        assert isinstance(entry["lease"]["host"], str)
        assert isinstance(entry["lease"]["heartbeat_at"], str)
        assert isinstance(entry["lease"]["accepting"], bool)


def test_runs_attaches_each_runs_own_lease_and_reads_the_clock_once(
    projection, monkeypatch
):
    """Review Focus: a run without a lease row never inherits its neighbour's,
    and the whole listing is judged against one instant."""
    calls: list[datetime] = []

    def counting_now() -> datetime:
        calls.append(CONTROL_NOW)
        return CONTROL_NOW

    monkeypatch.setattr(cli, "_utcnow", counting_now)
    _plant_run(projection)
    _record(
        projection,
        RUNS_NEWER_RUN_ID,
        started_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
        with_phases=False,
    )
    _plant_lease(projection, run_id=CONTROL_RUN_ID, heartbeat_at=_at(-31))

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    entries = json.loads(result.stdout)["data"]["runs"]
    assert [entry["id"] for entry in entries] == [RUNS_NEWER_RUN_ID, CONTROL_RUN_ID]
    assert entries[0]["lease"] is None
    assert entries[1]["lease"] is not None
    assert entries[1]["lease"]["live"] is False
    assert entries[1]["lease"]["heartbeat_at"] == _at(-31).isoformat()
    assert len(calls) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "runs_" -v`
Expected: FAIL. The new lease tests fail with `KeyError: 'lease'` (or `"lease" in entry` false, or the `'"lease":null'` substring missing); `test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id` fails because `RUNS_ENTRY_KEYS` now has `lease`; the clock test fails with `len(calls) == 0`. The older `runs_` tests (history order, empty project, agrees-with-status, milestone/card id) still pass.

- [ ] **Step 3: Extract the shared helper and keep `control_view` byte-identical**

In `src/agent_manager/cli.py`, insert directly above `def control_view(` (line 248):

```python
def _lease_fields(lease: store_module.LeaseRow, *, now: datetime) -> dict[str, Any]:
    """The lease fields `am status` and `am runs` both show.

    One function so the two commands cannot drift: `control_view` adds
    `acquired_at` on top, `runs_for` shows these five as they are. `live` is
    `control.lease_is_live` at `now`, worked out at read time and never
    stored; `heartbeat_at` is an ISO string.
    """
    return {
        "pid": lease.pid,
        "host": lease.host,
        "heartbeat_at": lease.heartbeat_at.isoformat(),
        "accepting": lease.accepting,
        "live": control.lease_is_live(lease, now=now),
    }


```

Then replace the `"lease"` entry of `control_view`'s returned dict (lines 262-271):

```python
        "lease": None
        if lease is None
        else {
            "pid": lease.pid,
            "host": lease.host,
            "acquired_at": lease.acquired_at.isoformat(),
            "heartbeat_at": lease.heartbeat_at.isoformat(),
            "accepting": lease.accepting,
            "live": control.lease_is_live(lease, now=now),
        },
```

with:

```python
        "lease": None
        if lease is None
        else {
            **_lease_fields(lease, now=now),
            "acquired_at": lease.acquired_at.isoformat(),
        },
```

- [ ] **Step 4: Run the status tests to verify `control_view` did not change**

Run: `uv run pytest tests/test_cli.py -k "status" -v`
Expected: PASS, in particular `test_status_shows_the_lease_and_every_lifes_requests_in_seq_order[live]` and `[stale]` and `test_status_lists_the_claims_of_the_live_lease`, which pin `control.lease` exactly. (`render` uses `sort_keys=True`, so the key order change inside the dict cannot change the bytes printed.)

- [ ] **Step 5: Fill `lease` in `runs_for`**

In `src/agent_manager/cli.py`, replace `runs_for` (lines 1483-1496) with:

```python
def runs_for(*, repo_dir: Path) -> dict[str, Any]:
    """This project's run history, newest first, each run with its lease.

    An empty history is an empty list, not a refusal: a project that has never
    been run is a fact. `model_dump()` keeps the `Path` and `datetime` objects
    for `render`'s `default=str`, exactly as `status_payload` does, so a run
    looks the same in both commands.

    `lease` is filled here, not in `store.list_runs`: `live` needs `control`,
    which `store` must not import. Each run's `run_leases` row is read on the
    same connection and shaped by `_lease_fields`, the helper `control_view`
    uses, so it is `am status`'s `control.lease` minus `acquired_at`, or
    `None` when the run has no lease row. One `now` judges the whole listing.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        now = _utcnow()
        entries = []
        for summary in store_module.list_runs(conn):
            lease = store_module.read_lease(conn, summary.id)
            shown = (
                None
                if lease is None
                else store_module.RunLease(**_lease_fields(lease, now=now))
            )
            entries.append(summary.model_copy(update={"lease": shown}).model_dump())
        return {"runs": entries}
    finally:
        conn.close()
```

Also update the `RUN_IDENTITY` docstring (lines 202-205) from:

```python
"""The run's own fields, without `config` and without the tree below it. §10's
`status` header is these seven names. Each `runs` entry carries the same seven,
plus `milestone_id` and `card_id` (a superset), so the two commands still
describe a run's identity the same way."""
```

to:

```python
"""The run's own fields, without `config` and without the tree below it. §10's
`status` header is these seven names. Each `runs` entry carries the same seven,
plus `milestone_id`, `card_id` and `lease` (a superset), so the two commands
still describe a run's identity the same way."""
```

- [ ] **Step 6: Run the runs tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "runs_ or status" -v`
Expected: PASS, all of them, including every pre-existing `runs_` test (old fixtures now show `"lease": null`).

- [ ] **Step 7: Document `lease` in the README**

In `README.md`, replace lines 437-443 of `### Listing runs`:

```markdown
`data.runs` is a list with one object per run. Each object has these keys:

- `id`, `workflow` (`milestone` or `task`), `repo_dir`, `base_branch`, `branch_prefix`, `status`, `started_at` (`null` if never recorded).
- `milestone_id`: the full id of the milestone card a milestone run drives. It is `null` on a `--card` run, and on a run recorded by an `am` too old to store it.
- `card_id`: the subtask card an `am run --card` run drives. It is `null` on a milestone run, and on a `--card` run whose subtask has not been recorded yet.

New keys are additive: a newer `am` may add keys to these objects, but never removes or renames one. Consumers should ignore any key they do not recognize.
```

with:

```markdown
`data.runs` is a list with one object per run. Each object has these keys:

- `id`, `workflow` (`milestone` or `task`), `repo_dir`, `base_branch`, `branch_prefix`, `status`, `started_at` (`null` if never recorded).
- `milestone_id`: the full id of the milestone card a milestone run drives. It is `null` on a `--card` run, and on a run recorded by an `am` too old to store it.
- `card_id`: the subtask card an `am run --card` run drives. It is `null` on a milestone run, and on a `--card` run whose subtask has not been recorded yet.
- `lease`: the process holding the run, or `null` if no process has a lease row for it. When present it is `{live, pid, host, heartbeat_at, accepting}`, the same values `am status <run-id>` shows in `control.lease` (without `acquired_at`). `live` is worked out when you ask: the heartbeat is at most 30 seconds old, and the lease is on another host or its pid is alive here. `heartbeat_at` is an ISO 8601 string. `accepting` is `false` once the run's control window has closed.

New keys are additive: a newer `am` may add keys to these objects, but never removes or renames one. Consumers should ignore any key they do not recognize.
```

- [ ] **Step 8: Run the full default suite**

Run: `uv run pytest`
Expected: PASS (unit + git tiers), no new failures.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py README.md
git commit -m "feat(cli): am runs shows each run's lease, shared with am status (6bf47e74)"
```
