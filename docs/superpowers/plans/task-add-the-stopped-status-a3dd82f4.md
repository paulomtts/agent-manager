<!-- task-pipeline: validated -->
# Add the stopped status (card a3dd82f4)

Parent story: 9bfb5ac2 "Stop cleanly: a stopped status and a cooperative stop" (Decision P4 of `2026-09-24-parallel-stories-design.md`, lines 66-75). Sibling 0d8b7c9a builds on this card.

## Scope

This card adds `stopped` as a recorded status and decides on purpose how every place that enumerates or branches on status treats it. Nothing produces `stopped` yet, so there is no behaviour change on any path that runs today. Per P4, `stopped` is not `failed`: relaunching the same command continues a stopped subtask through the existing idempotence and Plan-Hash re-entrancy.

In scope:

- `src/agent_manager/models.py`: `Status` becomes `Literal["pending", "started", "done", "failed", "escalated", "stopped"]`. Run, StoryRun, SubtaskRun and PhaseRun pick it up because they all use `Status`. `AttemptStatus` does not change.
- `src/agent_manager/engine.py`: `SubtaskSummary.status` widens from `Literal["done", "escalated"]` to `Literal["done", "escalated", "stopped"]`. No engine code produces it.
- `src/agent_manager/cli.py`, `select_resumable`: when no subtask is `started` and at least one is recorded `stopped`, the refusal message adds a stopped-specific remedy. It says a stopped subtask is continued by relaunching the milestone with the same `agent-manager run --milestone <id>` command. It must not suggest `retry`. The existing `found: card=status` listing and the `status <run>` pointer stay. When there are no stopped subtasks the message is unchanged. The docstring is updated to name `stopped` among the zero-started cases.
- Audit decisions. These places get no code change, only a comment where one helps a later reader:
  - `cli.status_rows`: `state` is `phase.status` or `attempt.status` and prints verbatim, so `stopped` shows with no change. A test pins this.
  - Exit codes. `run` (cli.py ~1040-1043) checks `payload["status"] == "escalated"` for a card and `payload.get("escalated") is True` for a milestone. `resume` (cli.py ~1361) checks `payload["status"] == "escalated"`. All three are strict equality, so `stopped` exits 0 with an `{"ok": true, ...}` envelope and never `EXIT_ESCALATED`. This stays as it is, and a test pins it.
  - `run --card` (cli.py ~789-803) and `resume` (cli.py ~1291-1307) copy `summary.status` into the run, story and subtask rows and into the payload `status`. A `stopped` summary is therefore recorded as `stopped` at all three levels and reported as `"status": "stopped"`. This is the intended flow, because the run did not finish and it did not fail. No code change.
  - `orchestrate.py` (~322): `if status != "done"` records the subtask, story and run as `escalated` and returns `escalated: True`. This is the one branch that would misclassify `stopped`, but the engine cannot return `stopped` until 0d8b7c9a, and the milestone treatment is owned by that card and later ones. It stays as it is, with a short comment noting that `stopped` must be handled here once the engine can produce it. It gets no behaviour change and no test in this card.
  - `store.py`: there is no CHECK constraint on status, so no schema change is needed. `load_run` and `rebuild_from_journal` validate through the pydantic models and accept `stopped` once `Status` does.

Out of scope: the `should_stop` callable, the pre-phase check, the "stopped before <phase>" detail, the `drive_subtask` and `Driver` pass-through, and the fake-runner stop tests, all owned by 0d8b7c9a. Also out of scope: the P5 `stopped` report key, Ctrl-C handling, milestone-aware `am resume`, watch/retry/cancel, and the rest of addendum section 5.

## Observable behaviour

- Models validate `status="stopped"` and still reject unknown strings.
- A run whose rows or journal lines carry `stopped` loads and rebuilds without error, and the value is preserved.
- `am status <run>` shows `stopped` in the state column for a stopped phase.
- `am resume <run>` on a run with a stopped subtask and none started refuses with `NotResumableError`, as it does today. The message now also tells the user to relaunch `run --milestone`.
- No command exits with the escalation code because of `stopped` alone.
- The default suite, including tests/e2e, stays green. `--max-concurrent 1` is unchanged.

## Error paths

- An invalid status string in the store or journal still raises from rebuild. The existing test at tests/test_store.py ~669 keeps passing because it checks a subset of the names.
- The resume refusal for zero-started runs keeps its current wording when no subtask is `stopped`.

## Tests

The tier comes from section 14 of `2026-09-23-agent-manager-design.md`: pure functions get unit tests, and tests mirror source files. None of these tests belong in e2e.

1. `tests/test_models.py` (unit, pure models): SubtaskRun, StoryRun and PhaseRun each accept `status="stopped"`. Run is covered too, since it shares the same `Status`.
2. `tests/test_models.py` (unit): an unknown status string is still rejected, showing the literal only grew by one value.
3. `tests/test_store.py` (store tier, temp directory like the existing store tests): a run recorded with a `stopped` subtask, story and phase (via `_record_full_run` or a variant) comes back as `stopped` from `load_run`.
4. `tests/test_store.py` (store tier): the same run rebuilt through `rebuild_from_journal` keeps `stopped` at each level.
5. `tests/test_cli.py` (unit, pure `status_rows`): a run with a stopped phase yields a row whose state is `stopped`.
6. `tests/test_cli.py` (unit, pure `select_resumable` with `_pure_run`, `_pure_story` and `_pure_subtask`): with one stopped subtask and none started, it raises `NotResumableError`. The message contains `card=stopped` and names relaunching `run --milestone`, and it does not contain `retry`.
7. `tests/test_cli.py` (unit): with zero started subtasks and none stopped (for example done and escalated), the message has no stopped remedy, which is a regression guard.
8. `tests/test_cli.py` (CLI tier, CliRunner or the exit-code helper): a card-run or resume payload with `status: "stopped"` maps to exit 0 with an `ok: true` envelope, not `EXIT_ESCALATED`. If the exit decision is inline in the command body and cannot be reached without a real stopped summary, drive it with an injected fake runner or driver whose summary is `stopped`, using the existing CLI test seams. Do not add engine stop plumbing.

---

# Add the stopped status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `stopped` a valid recorded status for runs, stories, subtasks and phases, widen the engine summary to carry it, and give the `resume` refusal a stopped-specific remedy, while pinning that nothing else changes behaviour.

**Architecture:** One literal grows by one value in `models.Status` (and `engine.SubtaskSummary.status`), and pydantic validation carries it through the store rows and the journal rebuild with no schema change. The only behavioural change is a conditional suffix on the zero-started refusal in `cli.select_resumable`. Every other status branch is audited and left alone, with pin tests for `status_rows` and the exit codes and a comment on the `orchestrate.py` branch that sibling 0d8b7c9a will have to revisit.

**Tech Stack:** Python, Pydantic v2, Typer (`typer.testing.CliRunner`), SQLite via `agent_manager.store`, pytest, run with `uv`.

**Spec:** `docs/superpowers/specs/task-add-the-stopped-status-a3dd82f4-design.md` (reproduced verbatim above). Parent decisions: `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` P4 (lines 66-75). Test placement: section 14 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.

**Worktree and branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-add-the-stopped-status-a3dd82f4` on branch `m4/task-add-the-stopped-status-a3dd82f4`, cut from `m4/task-serialize-board-writes-daf14164`. All paths below are relative to that worktree. Do not assume any sibling card's code (in particular 0d8b7c9a's `should_stop`) exists on this branch.

## Global Constraints

- `Status` must be exactly `Literal["pending", "started", "done", "failed", "escalated", "stopped"]`, in that order.
- `AttemptStatus` does not change: `Literal["started", "ok", "schema_invalid", "gate_failed", "harness_error"]`.
- `engine.SubtaskSummary.status` becomes `Literal["done", "escalated", "stopped"]` and no engine code produces `stopped`.
- The stopped remedy must point at relaunching the same `agent-manager run --milestone` command and must never contain the word `retry`.
- When no subtask is `stopped`, the zero-started refusal text is byte-for-byte unchanged.
- `stopped` alone never raises `typer.Exit(EXIT_ESCALATED)`; the envelope stays `{"ok": true, "data": ...}`.
- No store schema change. No change to the `orchestrate.py` `if status != "done"` behaviour (comment only).
- No stop plumbing: no `should_stop`, no pre-phase check, no "stopped before <phase>" detail, no `drive_subtask`/`Driver` pass-through (all owned by 0d8b7c9a).
- Tests go in the mirrored unit/store/CLI files (`tests/test_models.py`, `tests/test_engine.py`, `tests/test_store.py`, `tests/test_cli.py`), never in `tests/e2e`.
- Verification: `uv run pytest` (whole default suite, including tests/e2e, stays green).

## Review Focus

- A run with one `stopped` and one `escalated` subtask and none started: the refusal must still list both in `found:` and add the relaunch remedy naming only the stopped card, not the escalated one. Pinned in Task 2.
- A run with one `stopped` and one `started` subtask: `select_resumable` must return the started one, because `stopped` is not "in flight". Pinned in Task 2.
- The run-level `stopped` status must show in the `status` header (`status_payload(run)["run"]["status"]`), not only in the rows. Pinned in Task 1.
- `AttemptStatus` must not silently pick up `stopped`: an `Attempt(status="stopped")` must still be rejected. Pinned in Task 1.
- A milestone run whose driver returns a `stopped` summary is today recorded `escalated` by `orchestrate.py`. This card deliberately does not change or test that (owned by 0d8b7c9a); Task 3 leaves a comment at the branch so the next card cannot miss it.

---

### Task 1: Accept `stopped` in the models, the engine summary, the store and `status`

**Files:**
- Modify: `src/agent_manager/models.py:25-27`
- Modify: `src/agent_manager/engine.py:244`
- Test: `tests/test_models.py` (append after `test_phase_rejects_an_unknown_status_naming_the_allowed_set`, line ~226)
- Test: `tests/test_engine.py` (append at end of file)
- Test: `tests/test_store.py` (append after `test_an_in_flight_attempt_survives_the_rebuild_as_started`, line ~842)
- Test: `tests/test_cli.py` (append after `test_status_rows_of_a_run_with_no_stories_are_empty`, line ~261)

**Interfaces:**
- Consumes: nothing new.
- Produces: `models.Status` including `"stopped"`; `engine.SubtaskSummary.status: Literal["done", "escalated", "stopped"]`. Task 2 relies on `models.SubtaskRun(status="stopped")` being valid.

- [ ] **Step 1: Write the failing model tests**

Append to `tests/test_models.py`, directly after `test_phase_rejects_an_unknown_status_naming_the_allowed_set`. `typing` is not yet imported in this file, so add `import typing` next to the other stdlib imports at the top (after `import sys`).

```python
import typing
```

```python
def test_every_status_carrying_model_accepts_stopped():
    # P4: `stopped` is a recorded status of its own, not `failed`. Every model
    # that uses `Status` must load it, or a stopped run's rows would not
    # validate back out of the store.
    assert models.PhaseRun(name="implement", kind="agent", status="stopped").status == "stopped"
    assert (
        models.SubtaskRun(
            card_id="a3dd82f4", branch="m4/x-a3dd82f4", base_branch="main", status="stopped"
        ).status
        == "stopped"
    )
    assert (
        models.StoryRun(card_id="9bfb5ac2", title="Stop cleanly", level=0, status="stopped").status
        == "stopped"
    )
    assert (
        models.Run(
            id="run-2026-09-24-01",
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m4/",
            status="stopped",
        ).status
        == "stopped"
    )


def test_status_grew_by_exactly_stopped_and_still_rejects_unknown_values():
    assert typing.get_args(models.Status) == (
        "pending",
        "started",
        "done",
        "failed",
        "escalated",
        "stopped",
    )
    with pytest.raises(ValidationError) as excinfo:
        models.SubtaskRun(
            card_id="a3dd82f4", branch="m4/x-a3dd82f4", base_branch="main", status="halted"
        )
    message = str(excinfo.value)
    for allowed in ("pending", "started", "done", "failed", "escalated", "stopped"):
        assert allowed in message


def test_attempt_status_does_not_pick_up_stopped():
    # `stopped` is a lifecycle state of a run/story/subtask/phase. An attempt's
    # outcome vocabulary is separate and unchanged by this card.
    with pytest.raises(ValidationError):
        models.Attempt(n=1, dispatch=_dispatch(), status="stopped")
```

- [ ] **Step 2: Write the failing engine summary test**

Append to the end of `tests/test_engine.py`. `typing` is not yet imported there, so add `import typing` after `import json` at the top.

```python
import typing
```

```python
def test_a_subtask_summary_may_report_stopped():
    # The dataclass does not enforce its Literal at runtime, so the annotation
    # is the contract: sibling 0d8b7c9a returns `stopped` through this field,
    # and `run --card` / `resume` copy it verbatim into the rows and payload.
    hints = typing.get_type_hints(engine.SubtaskSummary)
    assert typing.get_args(hints["status"]) == ("done", "escalated", "stopped")
```

- [ ] **Step 3: Write the failing store round-trip tests**

Append to `tests/test_store.py`, directly after `test_an_in_flight_attempt_survives_the_rebuild_as_started`:

```python
def _record_stopped_run(st: store.Store, repo: Path) -> None:
    """One run stopped cleanly mid-subtask: `stopped` at every level that uses `Status`."""
    st.record_run(models.Run.model_validate({**_run(repo).model_dump(), "status": "stopped"}))
    st.record_story(
        models.StoryRun(
            card_id="8831189b",
            title="Foundations: paths, run store and journal",
            level=0,
            status="stopped",
            tip_branch="m1/task-ef248597",
        )
    )
    st.record_subtask(
        "8831189b",
        models.SubtaskRun(
            card_id="ef248597",
            branch="m1/task-ef248597",
            base_branch="main",
            status="stopped",
            worktree_path=Path("/repo/.claude/worktrees/m1/task-ef248597"),
        ),
    )
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="implement",
            kind="agent",
            status="stopped",
            started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
        ),
    )


def _stopped_levels(run: models.Run) -> tuple[str, str, str, str]:
    story = run.stories[0]
    subtask = story.subtasks[0]
    return run.status, story.status, subtask.status, subtask.phases[0].status


def test_a_stopped_run_loads_back_as_stopped_from_the_rows(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_stopped_run(st, repo)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert loaded is not None
    assert _stopped_levels(loaded) == ("stopped", "stopped", "stopped", "stopped")


def test_a_stopped_run_survives_a_rebuild_from_the_journal(repo):
    # D5: the journal is the truth. A stopped run rebuilt after the projection
    # is lost must come back stopped, not fail validation and not turn into
    # another status.
    st = store.Store.open(repo, RUN_ID)
    _record_stopped_run(st, repo)
    st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
        row = rebuilt.connection.execute(
            "SELECT status FROM subtasks WHERE run_id = ?", (RUN_ID,)
        ).fetchone()
    finally:
        rebuilt.close()

    assert _stopped_levels(returned) == ("stopped", "stopped", "stopped", "stopped")
    assert after == returned
    assert row["status"] == "stopped"
```

- [ ] **Step 4: Write the failing `status_rows` test**

Append to `tests/test_cli.py`, directly after `test_status_rows_of_a_run_with_no_stories_are_empty`:

```python
def test_status_rows_and_header_show_a_stopped_run_verbatim():
    """`state` is the phase's own status on a phase row, so `stopped` needs no
    mapping: it prints as recorded. The run header carries the run's status the
    same way."""
    run = _pure_run(
        [
            models.StoryRun(
                card_id="story-1",
                title="One",
                level=0,
                status="stopped",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-1",
                        branch="m1/a",
                        base_branch="main",
                        status="stopped",
                        phases=[
                            models.PhaseRun(name="implement", kind="agent", status="stopped")
                        ],
                    )
                ],
            )
        ]
    ).model_copy(update={"status": "stopped"})

    assert cli.status_rows(run) == [
        {
            "story": "story-1",
            "subtask": "card-1",
            "phase": "implement",
            "attempt": None,
            "state": "stopped",
        }
    ]
    assert cli.status_payload(run)["run"]["status"] == "stopped"
```

- [ ] **Step 5: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_models.py::test_every_status_carrying_model_accepts_stopped tests/test_models.py::test_status_grew_by_exactly_stopped_and_still_rejects_unknown_values tests/test_models.py::test_attempt_status_does_not_pick_up_stopped tests/test_engine.py::test_a_subtask_summary_may_report_stopped tests/test_store.py::test_a_stopped_run_loads_back_as_stopped_from_the_rows tests/test_store.py::test_a_stopped_run_survives_a_rebuild_from_the_journal tests/test_cli.py::test_status_rows_and_header_show_a_stopped_run_verbatim -v`

Expected: FAIL for all except `test_attempt_status_does_not_pick_up_stopped` (which already passes and guards against over-widening). The model, store and CLI tests fail with a pydantic `ValidationError` ("Input should be 'pending', 'started', 'done', 'failed' or 'escalated'"); the `get_args` tests fail with a tuple mismatch missing `'stopped'`.

- [ ] **Step 6: Widen `Status` in `models.py`**

Replace lines 25-27 of `src/agent_manager/models.py`:

```python
Status = Literal["pending", "started", "done", "failed", "escalated", "stopped"]
"""Lifecycle of a run, story, subtask or phase. `started` is the non-terminal
state resume keys off (§9). `stopped` (addendum P4) is a clean stop on request
between phases: it is not `failed`, and relaunching the same command continues
it."""
```

- [ ] **Step 7: Widen `SubtaskSummary.status` in `engine.py`**

Replace line 244 of `src/agent_manager/engine.py`:

```python
    status: Literal["done", "escalated", "stopped"] = "done"
```

- [ ] **Step 8: Run the new tests to verify they pass**

Run: the same command as Step 5.
Expected: all 7 PASS.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest`
Expected: PASS, including `tests/test_store.py::test_a_line_with_an_invalid_status_raises_out_of_rebuild` and `tests/test_models.py::test_phase_rejects_an_unknown_status_naming_the_allowed_set` (both check a subset of names).

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/models.py src/agent_manager/engine.py tests/test_models.py tests/test_engine.py tests/test_store.py tests/test_cli.py
git commit -m "Add the stopped status to the run lifecycle and the subtask summary"
```

---

### Task 2: Give the `resume` refusal a stopped-specific remedy

**Files:**
- Modify: `src/agent_manager/cli.py:393-427` (`select_resumable` docstring and zero-started branch)
- Test: `tests/test_cli.py` (append after `test_select_resumable_refuses_more_than_one_subtask_in_flight`, line ~434)

**Interfaces:**
- Consumes: `models.SubtaskRun` accepting `status="stopped"` (Task 1); test helpers `_pure_run`, `_pure_story`, `_pure_subtask` already in `tests/test_cli.py`.
- Produces: `cli.select_resumable(run: models.Run) -> tuple[models.StoryRun, models.SubtaskRun]` unchanged in signature; the zero-started `NotResumableError` message gains a suffix only when at least one subtask is `stopped`.

- [ ] **Step 1: Write the failing and guard tests**

Append to `tests/test_cli.py`, directly after `test_select_resumable_refuses_more_than_one_subtask_in_flight`:

```python
def test_select_resumable_tells_a_stopped_run_to_relaunch_the_milestone():
    """P4: a stopped subtask is not failed and is not in flight. It continues
    when the operator relaunches the same `run --milestone` command, so that is
    the remedy named -- never `retry`, which is for escalations."""
    stopped = _pure_subtask("card-1", []).model_copy(update={"status": "stopped"})
    run = _pure_run([_pure_story("story-1", [stopped])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    message = str(caught.value)
    assert "card-1=stopped" in message
    assert "agent-manager run --milestone" in message
    assert "agent-manager status" in message
    assert "retry" not in message


def test_select_resumable_names_only_the_stopped_cards_in_the_remedy():
    """Review Focus: stopped and escalated side by side. Both stay in `found:`,
    and the relaunch remedy names the stopped card alone."""
    stopped = _pure_subtask("card-1", []).model_copy(update={"status": "stopped"})
    escalated = _pure_subtask("card-2", []).model_copy(update={"status": "escalated"})
    run = _pure_run([_pure_story("story-1", [stopped, escalated])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    message = str(caught.value)
    assert "found: card-1=stopped, card-2=escalated" in message
    remedy = message.split("shows the run as it stands", 1)[1]
    assert "card-1" in remedy
    assert "card-2" not in remedy
    assert "agent-manager run --milestone" in remedy
    assert "retry" not in message


def test_select_resumable_keeps_its_wording_when_nothing_is_stopped():
    """Regression guard: the stopped remedy is an addition, not a rewrite. With
    no stopped subtask the refusal is exactly what it was."""
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    escalated = _pure_subtask("card-2", []).model_copy(update={"status": "escalated"})
    run = _pure_run([_pure_story("story-1", [done, escalated])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    assert str(caught.value) == (
        "run '20260923T140506Z-cbe34d00' has no subtask recorded 'started', so there"
        " is no work in flight to pick up (found: card-1=done, card-2=escalated);"
        " `agent-manager status 20260923T140506Z-cbe34d00` shows the run as it stands"
    )


def test_select_resumable_does_not_count_a_stopped_subtask_as_in_flight():
    """Review Focus: `stopped` is not `started`. A run with one of each resumes
    the started one instead of refusing as if two were in flight."""
    stopped = _pure_subtask("card-1", []).model_copy(update={"status": "stopped"})
    started = _pure_subtask("card-2", [])
    run = _pure_run([_pure_story("story-1", [stopped]), _pure_story("story-2", [started])])

    story, subtask = cli.select_resumable(run)

    assert story.card_id == "story-2"
    assert subtask is started
```

- [ ] **Step 2: Run the tests to verify the right ones fail**

Run: `uv run pytest tests/test_cli.py -k "select_resumable" -v`
Expected: `test_select_resumable_tells_a_stopped_run_to_relaunch_the_milestone` FAILS on `assert "agent-manager run --milestone" in message`, and `test_select_resumable_names_only_the_stopped_cards_in_the_remedy` FAILS on `assert "card-1" in remedy` (the remedy slice is empty). `test_select_resumable_keeps_its_wording_when_nothing_is_stopped` and `test_select_resumable_does_not_count_a_stopped_subtask_as_in_flight` PASS already (they are guards). All pre-existing `select_resumable` tests PASS.

- [ ] **Step 3: Implement the remedy in `select_resumable`**

In `src/agent_manager/cli.py`, replace the docstring paragraph at lines 400-404 and the zero-started branch at lines 414-427 so the function reads:

```python
def select_resumable(run: models.Run) -> tuple[models.StoryRun, models.SubtaskRun]:
    """The one subtask of `run` that was in flight, or a refusal naming why not.

    Pure over the tree `load_run` assembled, like `find_subtask`: which subtask
    is resumable is a question about recorded state, and answering it before any
    store is opened is what keeps a refusal from minting a run directory.

    Exactly one `started` subtask is the resumable shape. Zero means the run
    finished, escalated, stopped or never started, and the statuses are listed
    because the fix differs for each. A `stopped` subtask (addendum P4) stopped
    cleanly between phases and did not fail, so the refusal names its remedy
    outright: relaunch the same `run --milestone` command, whose idempotence and
    Plan-Hash re-entrancy continue it. It never points at `retry`, which is for
    escalations. More than one is a milestone-shaped run: this command drives
    one subtask the way `run --card` does, and choosing between them would leave
    the rest recorded `started` with nothing driving them.
    """
    started = [
        (story, subtask)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.status == "started"
    ]
    if len(started) == 1:
        return started[0]
    if not started:
        found = (
            ", ".join(
                f"{subtask.card_id}={subtask.status}"
                for story in run.stories
                for subtask in story.subtasks
            )
            or "no subtask at all"
        )
        stopped = [
            subtask.card_id
            for story in run.stories
            for subtask in story.subtasks
            if subtask.status == "stopped"
        ]
        remedy = (
            f"; {', '.join(stopped)} stopped cleanly and did not fail, so relaunch the"
            " same `agent-manager run --milestone` command that started this run to"
            " continue from where it stopped"
            if stopped
            else ""
        )
        raise NotResumableError(
            f"run {run.id!r} has no subtask recorded 'started', so there is no work"
            f" in flight to pick up (found: {found});"
            f" `agent-manager status {run.id}` shows the run as it stands{remedy}"
        )
    cards = ", ".join(subtask.card_id for _story, subtask in started)
    raise NotResumableError(
        f"run {run.id!r} has {len(started)} subtasks recorded 'started' ({cards}),"
        " and `resume` drives one subtask the way `run --card` does;"
        f" `agent-manager status {run.id}` shows all of them"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "select_resumable" -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Tell a stopped run's resume refusal to relaunch the milestone"
```

---

### Task 3: Pin that `stopped` never escalates, and mark the orchestrate branch

**Files:**
- Modify: `src/agent_manager/cli.py:1035-1044` (comment only, in `run`)
- Modify: `src/agent_manager/cli.py:1360-1362` (comment only, in `resume`)
- Modify: `src/agent_manager/orchestrate.py:322` (comment only)
- Test: `tests/test_cli.py` (append after `test_an_escalated_subtask_is_ok_true_and_exit_one`, line ~1552, and after `test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one`, line ~3770)

**Interfaces:**
- Consumes: `cli.run_card` and `cli.resume_run` as module-level names the commands call (the existing monkeypatch seams, see `test_repeated_verify_options_reach_run_card_in_command_line_order`); `_invoke(project, card_id, *extra)`, `_fake_payload(card_id, story_id)`, and the module-level `runner = CliRunner()` already in `tests/test_cli.py`.
- Produces: no new names.

These are pin tests. The exit decision is already strict equality on `"escalated"`, so they pass on first run; their job is to fail if a later card widens the check to something like `status != "done"`. `run_card` and `resume_run` are replaced wholesale, so no git repo or brd board is needed and the tests carry no `requires_git`/`requires_brd` marks.

- [ ] **Step 1: Write the `run --card` pin test**

Append to `tests/test_cli.py`, directly after `test_an_escalated_subtask_is_ok_true_and_exit_one`:

```python
def test_a_stopped_card_run_is_ok_true_and_exit_zero(tmp_path, monkeypatch):
    """P4: `stopped` is neither a failure nor an escalation. The exit decision is
    strict equality on `escalated`, so a stopped payload exits 0 with an ok
    envelope; this pins that against a later `!= "done"` widening."""

    def fake_run_card(card_id, **kwargs):
        return {**_fake_payload(card_id, "story-1"), "status": "stopped"}

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(tmp_path, "cbe34d00-9d8d-4f41-9c94-f99e665771b0")

    assert result.exit_code == 0, result.output
    assert result.exit_code != cli.EXIT_ESCALATED
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "stopped"
```

- [ ] **Step 2: Write the `resume` pin test**

Append to `tests/test_cli.py`, directly after `test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one`:

```python
def test_a_resumed_walk_that_stops_is_ok_true_and_exit_zero(tmp_path, monkeypatch):
    """`resume` copies `summary.status` into its payload, so a stopped walk
    reports `stopped`, and its strict `== "escalated"` check keeps exit 0."""

    def fake_resume_run(run_id, **kwargs):
        return {"run_id": run_id, "status": "stopped"}

    monkeypatch.setattr(cli, "resume_run", fake_resume_run)
    result = runner.invoke(
        cli.app, ["resume", "20260923T140506Z-cbe34d00", "--repo-dir", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "stopped"
```

- [ ] **Step 3: Run the pin tests**

Run: `uv run pytest tests/test_cli.py::test_a_stopped_card_run_is_ok_true_and_exit_zero tests/test_cli.py::test_a_resumed_walk_that_stops_is_ok_true_and_exit_zero -v`
Expected: PASS (pin tests: current behaviour is already correct). If either fails with a `KeyError` or a non-zero exit, stop and inspect: it means the exit decision is not what the spec audited, and the spec must be revisited before changing code.

- [ ] **Step 4: Document the exit decision in `run`**

In `src/agent_manager/cli.py`, replace the comment block at lines 1035-1038 (inside `run`, just above `if milestone is None:`) with:

```python
    # A card payload reports `status`. A milestone payload has no `status` key:
    # it carries `escalated: true` only when it stopped, a clean one carries
    # `done: true`, and a dry-run preview carries neither. So the flag is read
    # with `.get`, never indexed. Both checks are strict equality on purpose:
    # a `stopped` card (addendum P4) is not an escalation and exits 0.
```

- [ ] **Step 5: Document the exit decision in `resume`**

In `src/agent_manager/cli.py`, replace lines 1361-1362 (the end of `resume`) with:

```python
    # Strict equality on purpose: a `stopped` walk (addendum P4) is not an
    # escalation, so it exits 0 with an ok envelope.
    if payload["status"] == "escalated":
        raise typer.Exit(EXIT_ESCALATED)
```

- [ ] **Step 6: Mark the orchestrate branch that will misclassify `stopped`**

In `src/agent_manager/orchestrate.py`, replace line 322 (`                    if status != "done":`) with:

```python
                    # Every non-`done` result is recorded `escalated` here. Nothing
                    # returns `stopped` yet; once the engine can (card 0d8b7c9a), a
                    # `stopped` summary must be handled before this branch rather
                    # than fall into it, because `stopped` is not an escalation (P4).
                    if status != "done":
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS, including tests/e2e and every existing `tests/test_orchestrate.py` test (comment-only change there).

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/cli.py src/agent_manager/orchestrate.py tests/test_cli.py
git commit -m "Pin that a stopped result never sets the escalation exit code"
```
