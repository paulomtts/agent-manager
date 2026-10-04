<!-- task-pipeline: validated -->
# Card 9b7d0364 — 4.2 `am logs --follow`: end event

Parent story 762352b2 "am logs --follow". Blocked by 4.1 (1faf964f, done). Source of truth: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §3 (lines 80-94), "Compatibility and versioning" (115-121), "Testing" (123-135).

## Base

4.1's code is not on master. This card builds on branch `ami/task-4-1-logs-follow-hello-1faf964f` (HEAD 6932cde): `LogsSelection`, `select_logs`, `logs_follow_for`, `_read_log_bytes`, `_utf8_complete_length`, `_follow_logs`, `_stream_logs` in `src/agent_manager/cli.py`, and the follow tests and helpers (`_logs_follow`, `_logs_args`, `_record_for_logs`, `_write_logs_attempt`, `LOGS_RUN_ID`) in `tests/test_cli.py`.

## Scope

In `am logs RUN CARD [--phase P] [--attempt N] --follow [--since-offset B]`, once the followed attempt has a terminal status and the file has stopped growing, print one last line `{"event":"end","status":S}` and exit 0. S is one of `ok`, `schema_invalid`, `gate_failed`, `harness_error`.

Out of scope (owned by 4.1 or other stories, must not change): the hello line, the `{offset,text}` chunk shape and offsets, `--since-offset` semantics, the refusals (envelope, exit 3, before the first stream line), Ctrl-C and closed pipe ending at exit 0, non-follow `logs`, `am runs`, `--detach`, `watch --from-now`, `--board` docs, README, the e2e_fake detach test.

## Observable behavior

- Status is re-read during the stream. The `LogsSelection` snapshot is stale, so before each read of the file the followed attempt's status is looked up again. The lookup is read-only: `store_module.open_db` + `load_run`, connection closed every time. Never `Store.open`, `paths.attempt_dir` or `paths.run_dir`, which create directories. What gets streamed has to carry enough to find the attempt again: run id, card, phase name, and the attempt number (agent) or step attempt number (deterministic). Concretely, `logs_follow_for` returns the `LogsSelection` instead of a bare `Path` (no test calls it directly; the hello path comes from `selection.followed_path()`), and `_stream_logs`/`_follow_logs` take it and derive the lookup key from it.
- Terminal means any `AttemptStatus` other than `started` (`models.py:37-40`).
- End condition. The status check comes before the read it applies to, so bytes written just before the status flips are never lost. If the status seen before a read is terminal and that read finds no new complete bytes past the cursor, the stream ends. While the status is terminal, reads repeat without sleeping until one comes back empty, so a finished attempt drains and ends without waiting. The `WATCH_MAX_POLLS` bound is therefore checked only when the status seen before the read was `started`; a terminal status skips the bound check so the drain always completes.
- Before `end`, any incomplete trailing UTF-8 bytes that 4.1 held back below the cursor are emitted as one final chunk, decoded with `errors="replace"`. The file will not grow again, so without this the stream would hang or drop bytes. Offsets stay byte-exact.
- `end` is the last line. It has exactly two keys, `event` and `status`, and is written with `_emit_stream_line` (compact, so `--pretty` has no effect). After it, `_stream_logs` returns normally: exit 0, empty stderr.
- An attempt that is already terminal when the command starts: hello, the backlog chunks from the offset, then `end`, with no sleep at all. A `--since-offset` at or past EOF gives hello then `end`.
- A missing stdout file with a terminal attempt: hello then `end` (no hang, nothing created).
- Poll bound: `WATCH_MAX_POLLS` counts sleeps only. Reaching it while the attempt is still `started` stops the stream as in 4.1, with no `end` line. `end` is only emitted for a terminal attempt, never because the bound was hit. Known limit, accepted: a run killed so that its attempt row stays `started` is never ended by this stream (no run-level liveness check; that is not in §3).
- Deterministic phases (`--phase` names a `kind=="deterministic"` step, `attempt is None`). **Mapping decision, flagged for confirmation.** These phases have no `Attempt` row, only `PhaseRun.status` (`models.Status`), and §3 lists only the four `AttemptStatus` values. Rule: step attempt N is terminal when a later `<phase>.M` directory exists (M > N), or when `PhaseRun.status` is neither `pending` nor `started`. S is `ok` when N is the latest attempt and the phase is `done`. Every other terminal case is `gate_failed`: a superseded attempt, or the phase `failed`, `escalated`, `stopped` or `cancelled`. The lookup uses only the read-only `paths.recorded_attempts` and `load_run`.
- The `--follow` help text and the `logs` docstring stop saying "until interrupted" and mention the end event.
- Compatibility: purely additive. The logs hello stays `schema: 1`, the watch hello is unchanged, and the journal stays schema 1.

## Error paths

- If the re-lookup can no longer find the run, card, phase or attempt, or raises any error already in `HANDLED`, the existing post-hello path applies: stderr `am logs: …`, exit `EXIT_ERROR`. No new error types are added.
- Ctrl-C or a closed pipe during the terminal drain or while writing `end`: exit 0 as in 4.1.

## Tests

All tests are in `tests/test_cli.py`. Every one is **unmarked `unit`** under the test-placement rule (CLAUDE.md "Test tiers", design spec §14): files live in `tmp_path` through the `projection` fixture, `cli._watch_sleep` and `cli.WATCH_MAX_POLLS` are monkeypatched, and nothing spawns a subprocess or uses git. That matches the 4.1 follow tests. None of them is `git` or `e2e_fake`.

Write the tests first.

1. `test_logs_follow_ends_on_terminal_status`, parametrized over `ok`, `schema_invalid`, `gate_failed`, `harness_error` (unit). The implement attempt starts as `started` with a backlog. One action appends bytes and records the attempt with status X. Expected stream: hello, backlog chunk, appended chunk, then `{"event":"end","status":X}` as the last line. Exit 0, empty stderr, exactly one sleep.
2. `test_logs_follow_already_complete_file_ends_without_waiting` (unit). Uses the default `_record_for_logs` (implement.1 is `gate_failed`), `WATCH_MAX_POLLS=None`, and a `_watch_sleep` that fails the test if it is called. Expected: hello, backlog chunks, `end` with `gate_failed`, exit 0.
3. `test_logs_follow_started_attempt_never_ends_on_poll_bound` (unit). A `started` attempt with the bound reached: no `end` line, exit 0.
4. `test_logs_follow_terminal_missing_file_ends` (unit). `stdout=False` with a terminal attempt. Expected: hello then `end`, exit 0, the file is not created.
5. `test_logs_follow_terminal_flushes_partial_utf8_tail` (unit). A terminal attempt whose file ends mid-character. Expected: the last chunk has U+FFFD at the correct byte offset, then `end`.
6. `test_logs_follow_since_offset_at_eof_on_terminal_attempt` (unit). Expected: hello then `end`.
7. `test_logs_follow_deterministic_phase_end_status`, parametrized (unit): the latest attempt with the phase `done` gives `ok`; the latest attempt with the phase `failed` gives `gate_failed`; a superseded attempt with a later `<phase>.M` directory gives `gate_failed`; the phase `pending` or `started` on the latest attempt gives no `end`.
8. `test_logs_follow_relookup_lost_attempt_errors` (unit). The run disappears from the projection mid-stream. Expected: stderr `am logs: …`, exit `EXIT_ERROR`.

Adjustments to existing 4.1 tests (unit, no change in what they assert). `_write_logs_attempt` records implement.1 as `gate_failed`, which is terminal, so with this change the 4.1 follow tests that depend on waiting, polling or no `end` line would end early. Add a status knob to `_record_for_logs`/`_write_logs_attempt`, keeping the current default for the one-shot tests. Switch these tests to a `started` implement attempt: hello_shape, backlog_with_offsets, multibyte_not_split, since_offset_resumes, picks_up_appended_data, waits_for_missing_file, since_offset_beyond_end_waits, truncated_file, invalid_bytes, ctrl_c, closed_pipe, mid_stream_error (it expects one sleep before the failing second read; on a terminal attempt that read happens with no sleep). Run `test_logs_follow_writes_nothing` with at least one poll on a `started` attempt and one stream that ends with `end` on a terminal attempt, so it shows that the status re-lookup creates nothing. `uv run pytest` stays green.

---

# `am logs --follow` end event Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am logs RUN CARD --follow` prints a final `{"event":"end","status":S}` line and exits 0 once the followed attempt is terminal and its stdout file has stopped growing.

**Architecture:** `logs_follow_for` returns the `LogsSelection` (not a bare `Path`). `_stream_logs` builds a zero-argument `end_status` callable around a new read-only `logs_end_status(selection, *, repo_dir)` re-lookup, and `_follow_logs` calls it before every read: a terminal status drains the file without sleeping, flushes any held-back partial UTF-8 tail, then yields the `end` line. Deterministic phases map through a new pure `step_end_status(subtask, phase, n, recorded)`.

**Tech Stack:** Python, Typer, Pydantic models, SQLite projection via `store_module.open_db`/`load_run`, pytest with `typer.testing.CliRunner`.

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-4-2-logs-follow-end-9b7d0364/docs/superpowers/specs/task-4-2-logs-follow-end-9b7d0364-design.md` (prepended above). Upstream: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §3.

**Branch / worktree:** `ami/task-4-2-logs-follow-end-9b7d0364` at `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-4-2-logs-follow-end-9b7d0364`, cut from `ami/task-4-1-logs-follow-hello-1faf964f`. All paths below are relative to that worktree. Only 4.1's code is assumed present; no other subtask's code is.

## Global Constraints

- End line is exactly `{"event":"end","status":S}` with S in `ok`, `schema_invalid`, `gate_failed`, `harness_error`, written with `_emit_stream_line` (compact, sorted keys: `{"event":"end","status":"ok"}`); `--pretty` has no effect on it.
- After `end`, exit 0 and empty stderr.
- Terminal means any `AttemptStatus` other than `started`.
- Status re-lookup is read-only: `store_module.open_db` + `store_module.load_run`, connection closed every time; never `Store.open`, `paths.attempt_dir`, `paths.run_dir`. Deterministic lookup uses only `paths.recorded_attempts` and `load_run`.
- `WATCH_MAX_POLLS` counts sleeps only and never produces `end`; it is checked only when the status seen before the read was `started`.
- Re-lookup failures use the existing post-hello path: stderr `am logs: …`, exit `EXIT_ERROR` (3). No new error types.
- Logs hello stays `schema: 1`; watch hello unchanged; journal stays schema 1.
- All new tests are unmarked (`unit` tier) in `tests/test_cli.py`; no subprocess, no git.
- Verification: `uv run pytest` (no separate lint/typecheck).

## Review Focus

- Following an older attempt (`--phase explore --attempt 1`, `gate_failed`) while a later one (`explore.2`, `ok`) exists must end with the followed attempt's own status, not the latest's. Test: `test_logs_follow_ends_with_the_followed_attempts_own_status` (Task 3).
- A reader that closes the pipe exactly when the `end` line is written (`am logs … --follow | head -2`) must get exit 0 and empty stderr. Test: `test_logs_follow_closed_pipe_on_end_exits_zero_quietly` (Task 3).
- Ctrl-C while a terminal attempt is still draining (no sleep happens, so the interrupt lands in a read) must exit 0 quietly. Test: `test_logs_follow_ctrl_c_during_drain_exits_zero` (Task 3).
- `--pretty` on a stream that ends: the bytes, `end` line included, must equal the non-pretty stream. Assertion inside `test_logs_follow_already_complete_file_ends_without_waiting` (Task 3).
- A deterministic `<phase>.N` directory that vanishes mid-stream (or an N above every recorded directory) must be a refusal on stderr, not a hang or a bogus `end`. Test: `test_step_end_status_refuses_an_attempt_no_longer_on_disk` (Task 2).

---

### Task 1: Status knob in the logs test scaffolding; 4.1 follow tests on a `started` attempt

Pure test refactor: nothing in `src/` changes, every test passes before and after. It exists so Task 3's behavior change does not end the 4.1 streams early.

**Files:**
- Modify: `tests/test_cli.py:4591-4672` (`_write_logs_attempt`, `_record_for_logs`)
- Modify: `tests/test_cli.py:5184-5568` (the 4.1 follow tests listed in Step 3)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_write_logs_attempt(run_id: str, phase: str, n: int, *, stdout: bool = True, status: str | None = None) -> models.Attempt` and `_record_for_logs(root: Path, run_id: str, *, stdout: bool = True, implement_status: str | None = None) -> None`. `None` keeps today's default (`ok` for n > 1, `gate_failed` for n == 1). Task 3 relies on `implement_status="started"`.

- [ ] **Step 1: Add the `status` knob to `_write_logs_attempt`**

Replace the whole function at `tests/test_cli.py:4591-4615` with:

```python
def _write_logs_attempt(
    run_id: str,
    phase: str,
    n: int,
    *,
    stdout: bool = True,
    status: str | None = None,
) -> models.Attempt:
    """One attempt's three files on disk, plus the row that points at them.

    The *test* calls `paths.attempt_dir` -- which creates the directory -- because
    in production `dispatch.AgentRunner` is what creates it. `logs` itself must
    never call it, and `test_logs_writes_nothing` is what pins that.

    `status=None` keeps the long-standing default (`gate_failed` for attempt 1,
    `ok` after it). A `started` attempt has no exit code yet.
    """
    if status is None:
        status = "ok" if n > 1 else "gate_failed"
    directory = paths.attempt_dir(run_id, "card-1", phase, n)
    (directory / "prompt.txt").write_text(f"prompt for {phase}.{n}\n", encoding="utf-8")
    (directory / "result.json").write_text(
        json.dumps({"phase": phase, "attempt": n}), encoding="utf-8"
    )
    if stdout:
        (directory / "stdout.log").write_text(f"stdout of {phase}.{n}\n", encoding="utf-8")
    return models.Attempt(
        n=n,
        dispatch=_recorded_dispatch(run_id),
        status=status,
        exit_code=None if status == "started" else (0 if status == "ok" else 1),
        prompt_path=directory / "prompt.txt",
        result_path=directory / "result.json",
        stdout_path=directory / "stdout.log",
    )
```

- [ ] **Step 2: Add `implement_status` to `_record_for_logs`**

In `tests/test_cli.py`, change the signature and docstring of `_record_for_logs` (line 4618) and its `implement` attempt (lines 4660-4665):

```python
def _record_for_logs(
    root: Path,
    run_id: str,
    *,
    stdout: bool = True,
    implement_status: str | None = None,
) -> None:
    """A run with two agent phases (two attempts, then one) and a pending phase.

    The trailing `verify` phase has no attempts, so the no-flag default has to
    skip it to reach `implement`. `implement_status` sets `implement.1`'s
    status; `None` keeps the default `gate_failed`, which is terminal, so a
    `logs --follow` test that needs the stream to keep polling passes
    `"started"`.
    """
```

and

```python
        opened.record_attempt(
            "story-1",
            "card-1",
            "implement",
            _write_logs_attempt(
                run_id, "implement", 1, stdout=stdout, status=implement_status
            ),
        )
```

The rest of the body is unchanged.

- [ ] **Step 3: Switch the 4.1 follow tests that need a live stream to a `started` attempt**

In each of these functions in `tests/test_cli.py`, replace the first line `_record_for_logs(projection, LOGS_RUN_ID)` with:

```python
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
```

Functions: `test_logs_follow_hello_shape`, `test_logs_follow_streams_backlog_with_offsets`, `test_logs_follow_multibyte_not_split`, `test_logs_follow_since_offset_resumes`, `test_logs_follow_picks_up_appended_data`, `test_logs_follow_since_offset_beyond_end_waits`, `test_logs_follow_truncated_file_emits_nothing_new`, `test_logs_follow_invalid_bytes_keep_byte_offsets`, `test_logs_follow_ctrl_c_exits_zero`, `test_logs_follow_closed_pipe_exits_zero_quietly`, `test_logs_follow_mid_stream_error_goes_to_stderr`.

In `test_logs_follow_waits_for_missing_file`, replace `_record_for_logs(projection, LOGS_RUN_ID, stdout=False)` with:

```python
    _record_for_logs(
        projection, LOGS_RUN_ID, stdout=False, implement_status="started"
    )
```

Leave `test_logs_follow_deterministic_phase_follows_stdout_log` (its `verify` phase is `pending`, so it never ends), `test_logs_follow_refusals` (refuses before the stream) and `test_logs_follow_writes_nothing` (reworked in Task 3) as they are. No assertion changes in any test.

- [ ] **Step 4: Run the logs tests to verify they still pass**

Run: `uv run pytest tests/test_cli.py -k logs -q`
Expected: PASS (all; this is a refactor, behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(logs): status knob for logs fixtures; follow tests use a started attempt"
```

---

### Task 2: `step_end_status`, the deterministic-phase mapping

**Files:**
- Modify: `src/agent_manager/cli.py` (add `step_end_status` directly after `select_step_attempt`, which ends at line 487)
- Test: `tests/test_cli.py` (append after `test_logs_follow_mid_stream_error_goes_to_stderr`, before `CRASHED_AT`)

**Interfaces:**
- Consumes: `UnknownAttemptError` (`cli.py:121`), `models.SubtaskRun`, `models.PhaseRun`, `Sequence` (already imported in `cli.py`).
- Produces: `step_end_status(subtask: models.SubtaskRun, phase: models.PhaseRun, n: int, recorded: Sequence[int]) -> str | None`. Returns `"ok"`, `"gate_failed"`, or `None` (not terminal yet). Raises `UnknownAttemptError` when `n` is not in `recorded`. Task 3 calls it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` after `test_logs_follow_mid_stream_error_goes_to_stderr`:

```python
def _verify_phase(status: str) -> tuple[models.SubtaskRun, models.PhaseRun]:
    subtask = models.SubtaskRun(card_id="card-1", branch="m1/task-x", base_branch="main")
    return subtask, models.PhaseRun(name="verify", kind="deterministic", status=status)


@pytest.mark.parametrize(
    ("status", "recorded", "n", "expected"),
    [
        ("done", [1, 2], 2, "ok"),
        ("failed", [1, 2], 2, "gate_failed"),
        ("escalated", [1], 1, "gate_failed"),
        ("stopped", [1], 1, "gate_failed"),
        ("cancelled", [1], 1, "gate_failed"),
        ("started", [1, 2], 1, "gate_failed"),
        ("done", [1, 2], 1, "gate_failed"),
        ("pending", [1], 1, None),
        ("started", [1], 1, None),
    ],
    ids=[
        "latest-done",
        "latest-failed",
        "latest-escalated",
        "latest-stopped",
        "latest-cancelled",
        "superseded-while-started",
        "superseded-while-done",
        "latest-pending",
        "latest-started",
    ],
)
def test_step_end_status(status, recorded, n, expected):
    """Card 4.2's mapping for a deterministic phase: superseded or failed is
    `gate_failed`, latest and `done` is `ok`, latest and running is `None`."""
    subtask, phase = _verify_phase(status)

    assert cli.step_end_status(subtask, phase, n, recorded) == expected


@pytest.mark.parametrize(("n", "recorded"), [(3, [1, 2]), (1, [])])
def test_step_end_status_refuses_an_attempt_no_longer_on_disk(n, recorded):
    """Review Focus 5: the followed `<phase>.N` directory is gone, so the
    re-lookup refuses instead of reporting an end it cannot know."""
    subtask, phase = _verify_phase("done")

    with pytest.raises(cli.UnknownAttemptError, match=f"has no attempt {n} any more"):
        cli.step_end_status(subtask, phase, n, recorded)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k step_end_status -q`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'step_end_status'`.

- [ ] **Step 3: Implement `step_end_status`**

Insert in `src/agent_manager/cli.py` directly after `select_step_attempt` (after line 487, before `def step_logs_payload`):

```python
def step_end_status(
    subtask: models.SubtaskRun,
    phase: models.PhaseRun,
    n: int,
    recorded: Sequence[int],
) -> str | None:
    """The `logs --follow` end status of deterministic attempt `n`, or `None`.

    A deterministic phase has no `Attempt` row, so card 4.2 maps it onto the
    `AttemptStatus` vocabulary: attempt `n` is over once a later `<phase>.M`
    directory exists, or once the phase is neither `pending` nor `started`.
    It ended `ok` only when it is the latest attempt and the phase is `done`;
    a superseded attempt, or a phase `failed`, `escalated`, `stopped` or
    `cancelled`, ended `gate_failed`. Pure: the caller scans `recorded` with
    the read-only `paths.recorded_attempts`.
    """
    if n not in recorded:
        numbers = ", ".join(str(item) for item in recorded) or "none"
        raise UnknownAttemptError(
            f"phase {phase.name!r} of card {subtask.card_id!r} has no attempt {n}"
            f" any more; recorded attempts: {numbers}"
        )
    if max(recorded) > n:
        return "gate_failed"
    if phase.status in ("pending", "started"):
        return None
    return "ok" if phase.status == "done" else "gate_failed"
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_cli.py -k step_end_status -q`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(logs): map a deterministic step attempt onto an end status"
```

---

### Task 3: `logs --follow` re-reads the status and ends with `{"event":"end"}`

**Files:**
- Modify: `src/agent_manager/cli.py:1999-2034` (`logs_follow_for` returns `LogsSelection`), add `logs_end_status` right after it
- Modify: `src/agent_manager/cli.py:2037-2100` (`logs` command: help text, docstring, call site)
- Modify: `src/agent_manager/cli.py:2398-2455` (`_follow_logs`, `_stream_logs`)
- Test: `tests/test_cli.py` (new helpers and tests appended after the Task 2 tests, before `CRASHED_AT`; `test_logs_follow_writes_nothing` at line 5321 replaced)

**Interfaces:**
- Consumes: `step_end_status(subtask, phase, n, recorded) -> str | None` (Task 2); `_record_for_logs(..., implement_status=...)` (Task 1); existing `LogsSelection`, `select_logs`, `find_subtask`, `resolve_repo_dir`, `UnknownRunError`, `UnknownCardError`, `UnknownPhaseError`, `UnknownAttemptError`, `CliError`, `store_module.open_db`, `store_module.load_run`, `paths.recorded_attempts`, `_read_log_bytes`, `_utf8_complete_length`, `_emit_stream_line`, `_watch_sleep`, `WATCH_MAX_POLLS`, `WATCH_POLL_SECONDS`.
- Produces:
  - `logs_follow_for(run_id, card, *, repo_dir, phase=None, attempt=None, since_offset=0) -> LogsSelection`
  - `logs_end_status(selection: LogsSelection, *, repo_dir: Path) -> str | None`
  - `_follow_logs(path: Path | None, *, offset: int, sleep: Callable[[float], None], max_polls: int | None, end_status: Callable[[], str | None]) -> Iterator[dict[str, Any]]` (yields `{offset,text}` chunks, then possibly one `{"event":"end","status":S}`)
  - `_stream_logs(selection: LogsSelection, *, repo_dir: Path, offset: int) -> None`

- [ ] **Step 1: Write the test helpers**

Append to `tests/test_cli.py` after the Task 2 tests:

```python
def _logs_follow_no_wait(monkeypatch, *args: str):
    """Run `am logs ARGS --follow` with no poll bound and a sleep that fails.

    For an attempt that is already over: the stream must drain and end on
    its own, so any sleep is a bug, and an unbounded loop would hang rather
    than pass. `pytest.fail` raises a `BaseException`, which `CliRunner`
    does not swallow.
    """

    def no_sleep(seconds: float) -> None:
        pytest.fail(f"the stream slept {seconds}s on an attempt that is already over")

    monkeypatch.setattr(cli, "_watch_sleep", no_sleep)
    monkeypatch.setattr(cli, "WATCH_MAX_POLLS", None)
    return runner.invoke(cli.app, ["logs", *args, "--follow"])


def _set_implement_status(root: Path, status: str) -> None:
    """Re-record `implement.1` with `status`, as the runner's terminal write
    does. Test-side only: it opens a `Store`, which `logs` must never do."""
    directory = paths.attempt_path(LOGS_RUN_ID, "card-1", "implement", 1)
    opened = store_module.Store.open(root, LOGS_RUN_ID)
    try:
        opened.record_attempt(
            "story-1",
            "card-1",
            "implement",
            models.Attempt(
                n=1,
                dispatch=_recorded_dispatch(LOGS_RUN_ID),
                status=status,
                exit_code=None if status == "started" else (0 if status == "ok" else 1),
                prompt_path=directory / "prompt.txt",
                result_path=directory / "result.json",
                stdout_path=directory / "stdout.log",
            ),
        )
    finally:
        opened.close()


def _set_verify_status(root: Path, status: str) -> None:
    """Re-record the deterministic `verify` phase with `status`."""
    opened = store_module.Store.open(root, LOGS_RUN_ID)
    try:
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="verify", kind="deterministic", status=status),
        )
    finally:
        opened.close()


def _end_line(status: str) -> dict[str, Any]:
    return {"event": "end", "status": status}
```

- [ ] **Step 2: Write the failing spec tests (1-8)**

Append to `tests/test_cli.py` after the helpers:

```python
@pytest.mark.parametrize("status", ["ok", "schema_invalid", "gate_failed", "harness_error"])
def test_logs_follow_ends_on_terminal_status(projection, monkeypatch, status):
    """Card 4.2: bytes appended just before the status flips are still
    streamed, then `end` carries the attempt's status and the exit is 0."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    stdout = _implement_stdout()
    original = stdout.read_bytes()

    def finish_the_attempt() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"last words\n")
        _set_implement_status(projection, status)

    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[finish_the_attempt]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert sleeps == [cli.WATCH_POLL_SECONDS]
    assert _stream(result) == [
        _logs_hello_line(stdout),
        {"offset": 0, "text": original.decode("utf-8")},
        {"offset": len(original), "text": "last words\n"},
        _end_line(status),
    ]
    assert result.stdout.splitlines()[-1] == f'{{"event":"end","status":"{status}"}}'


def test_logs_follow_already_complete_file_ends_without_waiting(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)  # implement.1 is gate_failed
    content = "first line\nsecond líne\n€uro\n"
    _implement_stdout().write_bytes(content.encode("utf-8"))

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": content},
        _end_line("gate_failed"),
    ]
    assert result.stdout.splitlines()[-1] == '{"event":"end","status":"gate_failed"}'

    # Review Focus 4: `--pretty` changes no byte of a stream that ends.
    pretty = _logs_follow_no_wait(monkeypatch, *_logs_args(projection, "--pretty"))
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == result.stdout


def test_logs_follow_started_attempt_never_ends_on_poll_bound(projection, monkeypatch):
    """The bound stops a test stream; it is not an end. Passes before the
    change too: it pins that `end` is never emitted for a `started` attempt."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")

    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[lambda: None, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert sleeps == [cli.WATCH_POLL_SECONDS, cli.WATCH_POLL_SECONDS]
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]


def test_logs_follow_terminal_missing_file_ends(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, stdout=False)  # gate_failed

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        _end_line("gate_failed"),
    ]
    assert not _implement_stdout().exists()


def test_logs_follow_terminal_flushes_partial_utf8_tail(projection, monkeypatch):
    """The held-back half character can never be completed once the attempt
    is over, so it goes out as U+FFFD at its own byte offset before `end`."""
    _record_for_logs(projection, LOGS_RUN_ID)  # gate_failed
    _implement_stdout().write_bytes(b"caf\xc3")  # the first byte of "é" only

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "caf"},
        {"offset": 3, "text": "�"},
        _end_line("gate_failed"),
    ]


@pytest.mark.parametrize("past_end", [0, 978], ids=["at-eof", "past-eof"])
def test_logs_follow_since_offset_at_eof_on_terminal_attempt(
    projection, monkeypatch, past_end
):
    _record_for_logs(projection, LOGS_RUN_ID)  # gate_failed
    offset = _implement_stdout().stat().st_size + past_end

    result = _logs_follow_no_wait(
        monkeypatch, *_logs_args(projection, "--since-offset", str(offset))
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout(), offset),
        _end_line("gate_failed"),
    ]


@pytest.mark.parametrize(
    ("phase_status", "attempts", "extra", "expected"),
    [
        ("done", 2, [], "ok"),
        ("failed", 2, [], "gate_failed"),
        ("started", 2, ["--attempt", "1"], "gate_failed"),
        ("pending", 1, [], None),
        ("started", 1, [], None),
    ],
    ids=["latest-done", "latest-failed", "superseded", "latest-pending", "latest-started"],
)
def test_logs_follow_deterministic_phase_end_status(
    projection, monkeypatch, phase_status, attempts, extra, expected
):
    """Card 4.2's flagged mapping, end to end through the CLI."""
    _record_for_logs(projection, LOGS_RUN_ID)
    for n in range(1, attempts + 1):
        _write_step_logs(LOGS_RUN_ID, n)
    _set_verify_status(projection, phase_status)
    followed = 1 if extra else attempts
    stdout = paths.attempt_path(LOGS_RUN_ID, "card-1", "verify", followed) / "stdout.log"
    body = {
        "offset": 0,
        "text": f"==> uv run pytest (exit 1)\nstdout of verify.{followed}\n",
    }
    args = _logs_args(projection, "--phase", "verify", *extra)

    if expected is None:
        result, sleeps = _logs_follow(monkeypatch, *args, actions=[lambda: None])
        assert result.exit_code == 0, result.output
        assert sleeps == [cli.WATCH_POLL_SECONDS]
        assert _stream(result) == [_logs_hello_line(stdout), body]
    else:
        result = _logs_follow_no_wait(monkeypatch, *args)
        assert result.exit_code == 0, result.output
        assert _stream(result) == [_logs_hello_line(stdout), body, _end_line(expected)]
    assert result.stderr == ""


def test_logs_follow_relookup_lost_attempt_errors(projection, monkeypatch):
    """The run vanishes from the projection after the hello: the re-lookup
    refuses on stderr at exit 3, as any post-hello error does."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")

    def forget_the_run() -> None:
        conn = sqlite3.connect(paths.project_db_path(projection))
        try:
            conn.execute("DELETE FROM runs WHERE id = ?", (LOGS_RUN_ID,))
            conn.commit()
        finally:
            conn.close()

    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[forget_the_run]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert sleeps == [cli.WATCH_POLL_SECONDS]
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]
    assert result.stderr.startswith(
        f"am logs: run {LOGS_RUN_ID!r} is not in the projection for "
    )
    assert result.stderr.endswith("\n")
```

- [ ] **Step 3: Write the failing Review Focus tests**

Append to `tests/test_cli.py` after the Step 2 tests:

```python
@pytest.mark.parametrize(
    ("extra", "n", "status"),
    [
        (["--phase", "explore", "--attempt", "1"], 1, "gate_failed"),
        (["--phase", "explore"], 2, "ok"),
    ],
    ids=["older-attempt", "latest-attempt"],
)
def test_logs_follow_ends_with_the_followed_attempts_own_status(
    projection, monkeypatch, extra, n, status
):
    """Review Focus 1: the re-lookup is keyed by the followed attempt's
    number, never by "the latest attempt of the phase"."""
    _record_for_logs(projection, LOGS_RUN_ID)
    stdout = paths.attempt_path(LOGS_RUN_ID, "card-1", "explore", n) / "stdout.log"

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection, *extra))

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _logs_hello_line(stdout),
        {"offset": 0, "text": f"stdout of explore.{n}\n"},
        _end_line(status),
    ]


def test_logs_follow_closed_pipe_on_end_exits_zero_quietly(projection, monkeypatch):
    """Review Focus 2: the reader goes away exactly as `end` is written."""
    _record_for_logs(projection, LOGS_RUN_ID)  # gate_failed
    real_emit = cli._emit_stream_line

    def emit_until_end(obj) -> None:
        if obj.get("event") == "end":
            raise BrokenPipeError(32, "Broken pipe")
        real_emit(obj)

    monkeypatch.setattr(cli, "_emit_stream_line", emit_until_end)

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]


def test_logs_follow_ctrl_c_during_drain_exits_zero(projection, monkeypatch):
    """Review Focus 3: a terminal attempt drains without sleeping, so Ctrl-C
    lands in a read, not a sleep; it is still exit 0 and silent."""
    _record_for_logs(projection, LOGS_RUN_ID)  # gate_failed
    real_read = cli._read_log_bytes
    reads: list[int] = []

    def read_then_interrupt(path, offset):
        reads.append(offset)
        if len(reads) == 2:
            raise KeyboardInterrupt
        return real_read(path, offset)

    monkeypatch.setattr(cli, "_read_log_bytes", read_then_interrupt)

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]
```

- [ ] **Step 4: Rework `test_logs_follow_writes_nothing`**

Replace the whole function at `tests/test_cli.py:5321-5342` with:

```python
def test_logs_follow_writes_nothing(projection, monkeypatch):
    """`logs --follow` is read-only like `logs`: a missing stdout file is
    waited on, never created; the per-read status re-lookup creates nothing,
    whether it finds the attempt running or over; a refusal mints no run
    directory."""
    _record_for_logs(
        projection, LOGS_RUN_ID, stdout=False, implement_status="started"
    )
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    polling, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[lambda: None]
    )
    assert polling.exit_code == 0, polling.output
    assert sleeps == [cli.WATCH_POLL_SECONDS]
    assert all(line.get("event") != "end" for line in _stream(polling))
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
    assert not _implement_stdout().exists()

    # The test, not `logs`, records the terminal status; snapshot after it.
    _set_implement_status(projection, "harness_error")
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    ended = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))
    assert ended.exit_code == 0, ended.output
    assert _stream(ended)[-1] == _end_line("harness_error")
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
    assert not _implement_stdout().exists()

    refusal, _ = _logs_follow(
        monkeypatch, "no-such-run", "card-1", "--repo-dir", str(projection)
    )
    assert refusal.exit_code == cli.EXIT_ERROR
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()
```

- [ ] **Step 5: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "logs_follow" -q`
Expected: FAIL. The terminal-attempt tests fail on `the stream slept 0.25s on an attempt that is already over` (today's loop sleeps forever with no bound); `test_logs_follow_ends_on_terminal_status` fails because the stream has no `end` line; `test_logs_follow_relookup_lost_attempt_errors` fails with exit 0 instead of 3; `test_logs_follow_writes_nothing` fails at the `ended` stream. `test_logs_follow_started_attempt_never_ends_on_poll_bound`, the `latest-pending`/`latest-started` deterministic cases and the Task 1 tests already pass, which is expected: they pin behavior that must survive.

- [ ] **Step 6: Make `logs_follow_for` return the selection and add `logs_end_status`**

In `src/agent_manager/cli.py`, replace `logs_follow_for` (lines 1999-2034) with the following, and add `logs_end_status` directly after it:

```python
def logs_follow_for(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
    since_offset: int = 0,
) -> LogsSelection:
    """Validate `am logs --follow` and return the attempt it streams.

    The same selection as `logs_for` (`select_logs`), so every refusal the
    one-shot makes is made here too, before the hello line. On top of those:
    a negative `--since-offset` (worded as `watch --since` is), and an agent
    attempt that recorded no stdout path, since the hello must name a file.
    The projection connection is closed by `select_logs` before this
    returns. The selection, not only its file, is returned because the
    stream looks the attempt's status up again before every read
    (`logs_end_status`, card 4.2), and that needs the run, card, phase and
    attempt number.
    """
    if since_offset < 0:
        raise CliError(f"--since-offset must be 0 or more, got {since_offset}")
    selection = select_logs(
        run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
    )
    if selection.followed_path() is None:
        number = (
            selection.attempt.n
            if selection.attempt is not None
            else selection.step_attempt
        )
        raise CliError(
            f"attempt {number} of phase {selection.phase.name!r} of card"
            f" {selection.subtask.card_id!r} recorded no stdout path,"
            " so there is no file to follow"
        )
    return selection


def logs_end_status(selection: LogsSelection, *, repo_dir: Path) -> str | None:
    """The followed attempt's status if it is over, `None` while it runs.

    Looked up afresh on every call, because `selection` is a snapshot from
    before the stream began. Read-only, like `select_logs`: the free
    `open_db` / `load_run`, the connection closed before anything else, and
    for a deterministic phase only `paths.recorded_attempts`; never
    `Store.open`, `paths.attempt_dir` or `paths.run_dir`, which create
    directories. An agent attempt is over once its status is anything but
    `started`; a deterministic one maps through `step_end_status`. A run,
    card, phase or attempt that can no longer be found is a refusal, which
    `_stream_logs` reports on stderr at exit 3.
    """
    run_id = selection.run.id
    card = selection.subtask.card_id
    name = selection.phase.name
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise UnknownRunError(
            f"run {run_id!r} is not in the projection for {root} any more"
        )
    found = find_subtask(run, card)
    if found is None:
        raise UnknownCardError(f"card {card!r} is not in run {run_id!r} any more")
    _, subtask = found
    phase = next((item for item in subtask.phases if item.name == name), None)
    if phase is None:
        raise UnknownPhaseError(f"card {card!r} has no phase {name!r} any more")
    if selection.attempt is None:
        if selection.step_attempt is None:
            raise CliError(
                f"phase {name!r} of card {card!r} was selected with neither"
                " an attempt row nor a step attempt"
            )
        return step_end_status(
            subtask,
            phase,
            selection.step_attempt,
            paths.recorded_attempts(run_id, card, name),
        )
    n = selection.attempt.n
    row = next((item for item in phase.attempts if item.n == n), None)
    if row is None:
        raise UnknownAttemptError(
            f"phase {name!r} of card {card!r} has no attempt {n} any more"
        )
    return None if row.status == "started" else row.status
```

- [ ] **Step 7: Update the `logs` command**

In `src/agent_manager/cli.py`, in `logs` (lines 2037-2100): replace the `--follow` option's `help`:

```python
    follow: bool = typer.Option(
        False,
        "--follow",
        help=(
            "Keep printing the attempt's stdout as it grows, one JSON object"
            ' per line; once the attempt is over and the file stops growing,'
            ' print {"event":"end","status":...} and exit 0.'
        ),
    ),
```

replace the docstring:

```python
    """Print one attempt's prompt, result and captured stdout/stderr.

    With --follow, print a hello line naming the attempt's stdout file and
    then its bytes as `{"offset", "text"}` lines, the existing content first
    and then each append. Once the attempt has a terminal status and the
    file has stopped growing, a last `{"event": "end", "status": ...}` line
    follows and the exit is 0. A refusal is still one envelope at exit 3,
    printed before any stream line.
    """
```

replace the follow branch inside the `try`:

```python
        if follow:
            selection = logs_follow_for(
                run_id,
                card,
                repo_dir=repo_dir,
                phase=phase,
                attempt=attempt,
                since_offset=offset,
            )
```

and the call after the `except`:

```python
    if follow:
        _stream_logs(selection, repo_dir=repo_dir, offset=offset)
        return
```

- [ ] **Step 8: Replace `_follow_logs` and `_stream_logs`**

In `src/agent_manager/cli.py`, replace `_follow_logs` and `_stream_logs` (lines 2398-2455) with:

```python
def _follow_logs(
    path: Path | None,
    *,
    offset: int,
    sleep: Callable[[float], None],
    max_polls: int | None,
    end_status: Callable[[], str | None],
) -> Iterator[dict[str, Any]]:
    """`path`'s bytes from `offset` as `{"offset", "text"}` chunks, then each
    append, then `{"event": "end", "status": ...}` once the attempt is over.

    `end_status()` is asked before every read, so bytes written just before
    the status flips are still read. While it returns `None` the attempt is
    running: one read, then `sleep(WATCH_POLL_SECONDS)` and another,
    `max_polls` sleeps or forever when it is `None`. Once it returns a
    status, reads repeat with no sleep and no poll bound until one finds
    nothing new; then a trailing partial UTF-8 character still held back is
    yielded as one replacement-decoded chunk (the file will not grow to
    complete it) and the `end` line closes the stream. One byte cursor spans
    every read, so chunks are contiguous. A read that finds nothing past the
    cursor (no file yet, a file shorter than the cursor) yields nothing.
    """
    cursor = offset
    polls = 0
    while True:
        ended = end_status()
        data = _read_log_bytes(path, cursor)
        complete = _utf8_complete_length(data)
        if complete:
            yield {
                "offset": cursor,
                "text": data[:complete].decode("utf-8", errors="replace"),
            }
            cursor += complete
            if ended is not None:
                continue
        if ended is not None:
            if data:
                yield {"offset": cursor, "text": data.decode("utf-8", errors="replace")}
            yield {"event": "end", "status": ended}
            return
        if max_polls is not None and polls >= max_polls:
            return
        sleep(WATCH_POLL_SECONDS)
        polls += 1


def _stream_logs(selection: LogsSelection, *, repo_dir: Path, offset: int) -> None:
    """The body of `am logs --follow`, once `logs_follow_for` has accepted it.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll. The stream ends by itself
    with the `end` line once `logs_end_status` finds the attempt over and the
    file drained: exit 0. Ctrl-C and a closed pipe also end it at exit 0,
    nothing on stderr. After the hello line no envelope can be printed, so a
    handled error, a failed status re-lookup included, goes to stderr and the
    exit is `EXIT_ERROR`, mirroring `_stream_watch`.
    """
    path = selection.followed_path()
    try:
        _emit_stream_line(_logs_hello(path, offset))
        for line in _follow_logs(
            path,
            offset=offset,
            sleep=_watch_sleep,
            max_polls=WATCH_MAX_POLLS,
            end_status=lambda: logs_end_status(selection, repo_dir=repo_dir),
        ):
            _emit_stream_line(line)
    except KeyboardInterrupt:
        return
    except BrokenPipeError:
        _silence_stdout()
        return
    except HANDLED as error:
        typer.echo(f"am logs: {error}", err=True)
        raise typer.Exit(EXIT_ERROR) from None
```

`logs_follow_for` has already refused a `None` path, so `_logs_hello` always receives a `Path` here.

- [ ] **Step 9: Run the logs tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "logs" -q`
Expected: PASS (all logs tests, 4.1's and 4.2's).

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(logs): end --follow with {\"event\":\"end\",\"status\"} once the attempt is over"
```

---

### Task 4: Full verification

**Files:** none changed.

**Interfaces:** none.

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest`
Expected: PASS, no failures or errors (the `unit` + `git` tiers; the new tests run in the unit tier).

- [ ] **Step 2: Confirm nothing outside this card's scope moved**

Run: `git diff ami/task-4-1-logs-follow-hello-1faf964f --stat`
Expected: only `src/agent_manager/cli.py` and `tests/test_cli.py` are listed (the spec and plan under `docs/superpowers/` appear only if they were committed; they are untracked until then, so `git status --short` shows them instead).
