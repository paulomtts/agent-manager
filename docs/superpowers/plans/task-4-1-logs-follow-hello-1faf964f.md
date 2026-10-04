<!-- task-pipeline: validated -->
# 4.1 `am logs --follow`: hello, chunks, offsets (card 1faf964f)

Parent story 762352b2 "am logs --follow". This narrows section 3 of `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` (lines 80-92), together with its compatibility notes (lines 115-121) and test list (lines 123-135), to the streaming core. The terminal `end` event belongs to sibling 4.2 (9b7d0364) and is out of scope.

## Scope

Add `--follow` and `--since-offset B` to `am logs RUN_ID CARD [--phase P] [--attempt N]` in `src/agent_manager/cli.py`. Tests go in `tests/test_cli.py`.

Out of scope:
- The `{"event":"end",...}` line, and stopping when the attempt reaches a terminal status. Both belong to 4.2.
- `am runs` lease/progress, `--detach`, `watch --from-now`, `--board` docs and README changes. These belong to other stories.
- Following `stderr.log`.
- The spec's e2e_fake detached-run test.

## Observable behaviour

- **No `--follow`**: output is byte-for-byte what it is today, one `ok_envelope(logs_for(...))`. `--pretty` still applies. With `--follow`, `--pretty` is ignored: stream lines are always compact.
- **With `--follow`**: the call is validated first, with the same run, card, phase and attempt selection as `logs_for` (`select_attempt`, or for a deterministic phase `paths.recorded_attempts` + `select_step_attempt` + `paths.attempt_path`). Implementation note: `logs_for` selects the attempt inline and returns a payload, so the plan should extract the selection into a small shared read-only helper that returns the followed path (keeping `logs_for` output unchanged) rather than duplicating it. Then stdout receives the following, as compact JSON, one object per line, each flushed through `_emit_stream_line`:
  1. A hello line: `{"event":"logs","schema":1,"path":"<followed file>","offset":B}`. `B` is the starting byte offset: the `--since-offset` value, or 0 when the flag is absent. `path` is a string.
  2. Chunk lines: `{"offset":O,"text":"..."}`. `O` is the byte offset in the file of the chunk's first byte. Each chunk covers bytes `[O, O+len)`, and the next chunk starts at `O+len`, so offsets are contiguous with no gaps or overlaps. The first chunk's offset equals the hello `offset`. Text is decoded as UTF-8 with `errors="replace"`. A chunk never ends partway through a multibyte character. A trailing incomplete sequence (at most 3 bytes) is held back and emitted at the start of the next chunk once the rest arrives. Empty chunks are never emitted.
  3. Polling: one read of the backlog from `B` happens at once. After that, the stream sleeps and reads again, picking up appended bytes. It sleeps via `_watch_sleep(WATCH_POLL_SECONDS)` and runs `WATCH_MAX_POLLS` times, or forever when that is `None`. Both values are looked up at call time so tests can bound them. The stream does not inspect attempt status and never emits an `end` event. It runs until Ctrl-C, a closed pipe, or the poll bound.
- **Which file is followed**:
  - Agent phase: `Attempt.stdout_path` from the projection row (the launcher merges stderr into it).
  - Deterministic phase (`kind == "deterministic"`, selected by `--phase`): `<phase>.N/stdout.log` (`verify_step.STDOUT_LOG`) under `paths.attempt_path(...)`, because `Attempt.stdout_path` does not exist for these phases.
- **File not there yet**: if the followed path does not exist yet, cannot be read, or is shorter than `B`, the poll emits nothing and is not treated as an error. Bytes are emitted once the file exists and has grown past the cursor.
- **Read-only**: nothing is created on disk. There are no calls to `paths.attempt_dir` or `paths.run_dir`. The projection is opened with `store_module.open_db` + `load_run`, and the connection is closed on every path, including refusals, before streaming starts. The stream itself reads only the followed file.
- **Endings**: `KeyboardInterrupt` returns exit 0 with nothing on stderr. `BrokenPipeError` triggers `_silence_stdout()` and exit 0. A `HANDLED` error raised after the hello line goes to stderr as `am logs: <message>` with exit 3, mirroring `_stream_watch`.

## Error paths (all before the first stream line)

Each of these prints the usual `{"ok":false,...}` envelope via `error_envelope` and exits 3 (`EXIT_ERROR`), with no hello and no chunk lines:
- unknown run (`UnknownRunError`)
- unknown card (`UnknownCardError`)
- unknown phase (`UnknownPhaseError`)
- unknown attempt, or no attempt recorded (`UnknownAttemptError`)
- `--since-offset` below 0: `CliError("--since-offset must be 0 or more, got B")`, the same wording as `watch --since`
- `--since-offset` given without `--follow` (any value, 0 included, so the option is `int | None` and "given" means not `None`, as `since_given` does for `watch`), refused by analogy with `--from-now` without `--follow`. Suggested wording: `CliError("--since-offset needs --follow: it resumes a stream, and without --follow there is no stream")`
- the selected agent attempt has no recorded `stdout_path` (`CliError`), since there is no file to name in the hello
- any other `HANDLED` error raised while validating

## Contract

The new JSON keys are additive only. The journal stays schema 1 and the `watch` hello stays at schema 1. The `logs` hello carries its own `"schema":1`.

## Tests (TDD, written first)

All tests go in `tests/test_cli.py` and belong to the **unmarked `unit` tier**. They touch only files in `tmp_path`, the projection fixture and a monkeypatched `cli._watch_sleep` / `cli.WATCH_MAX_POLLS`, with no subprocess of any kind. So per CLAUDE.md "Test tiers", they are not `git` and not `e2e_fake`. They reuse `_write_logs_attempt`, `_record_for_logs` and `LOGS_RUN_ID` (around lines 4591-4675). They add a `_logs_follow(monkeypatch, *args, actions=())` helper modelled on `_watch_follow` / `_stream` (around line 8590): each sleep runs the next action, such as appending bytes, and each stdout line is parsed as JSON.

1. `test_logs_follow_hello_shape` (unit): the first line is exactly `{"event":"logs","schema":1,"path":<attempt stdout_path>,"offset":0}`.
2. `test_logs_follow_streams_backlog_with_offsets` (unit): the existing file content comes back as chunks. The concatenated text equals the file, the first offset is 0, and the offsets are contiguous byte offsets.
3. `test_logs_follow_multibyte_not_split` (unit): an append that ends mid-character (for example the first byte of `é`) is completed on a later poll. No chunk contains U+FFFD, and the byte offsets still line up.
4. `test_logs_follow_since_offset_resumes` (unit): `--since-offset B` gives a hello `offset` of B and a first chunk at B, with text equal to `file[B:]`.
5. `test_logs_follow_picks_up_appended_data` (unit): bytes appended during a sleep action appear as a later chunk at the old file length.
6. `test_logs_follow_waits_for_missing_file` (unit): the file is absent at start, so the stream has only the hello. The file is created during a sleep, and its bytes then appear from offset 0.
7. `test_logs_follow_deterministic_phase_follows_stdout_log` (unit): `--phase <deterministic step>` follows `<phase>.N/stdout.log`, and the hello path names it.
8. `test_logs_follow_refusals` (unit, parametrized): unknown run, card, phase and attempt, an agent attempt with no recorded `stdout_path`, `--since-offset -1`, and `--since-offset` without `--follow` (including `--since-offset 0`). Each gives exit 3, a single `ok:false` envelope on stdout, and no hello line.
9. `test_logs_follow_ctrl_c_exits_zero` (unit): a sleep action that raises `KeyboardInterrupt` gives exit 0, the lines emitted so far, and empty stderr.
10. `test_logs_follow_writes_nothing` (unit): mirrors `test_logs_writes_nothing`. A follow run creates no file or directory under the data dir.
11. `test_logs_without_follow_unchanged` (unit): the existing non-follow logs tests stay green. This test adds an assertion that the output has no `event` key and remains one envelope.

## Verification

`uv run pytest` is green. There is no separate lint or typecheck.

---

# `am logs --follow` (hello, chunks, offsets) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am logs RUN_ID CARD [--phase] [--attempt] --follow [--since-offset B]` streams a hello line and then contiguous byte-offset chunks of the selected attempt's stdout file, polling for appended bytes, without changing the non-follow envelope.

**Architecture:** The inline selection in `logs_for` is extracted into a read-only `select_logs(...) -> LogsSelection`, which both builds the existing payload and names the followed file. Two pure helpers (`_read_log_bytes`, `_utf8_complete_length`) read from a byte cursor and hold back a trailing partial UTF-8 sequence. A `_follow_logs` generator (backlog read, then `sleep` + read up to `max_polls` times) and a `_stream_logs` driver mirror `_follow_watch` / `_stream_watch`, reusing `_watch_sleep`, `WATCH_POLL_SECONDS`, `WATCH_MAX_POLLS` and `_emit_stream_line`.

**Tech Stack:** Python 3, Typer, Pydantic models, SQLite projection, pytest with `typer.testing.CliRunner`, `uv`.

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-4-1-logs-follow-hello-1faf964f/docs/superpowers/specs/task-4-1-logs-follow-hello-1faf964f-design.md` (prepended verbatim above). Parent spec: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` section 3.

Planner note: the spec author's summary handed to the planner was truncated at 2000 characters mid-sentence ("hello shap..."), which suggests that upstream stage over-ran its brief. This plan was written from the on-disk, post-review spec above, not from that summary.

**Branch / worktree:** `ami/task-4-1-logs-follow-hello-1faf964f` in `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-4-1-logs-follow-hello-1faf964f`. All paths below are relative to that worktree. Nothing from sibling 4.2 (the `end` event) exists on this branch, and none is added.

## Global Constraints

- New JSON keys are additive only. The journal stays schema 1, the `watch` hello stays `"schema": 1`, and the `logs` hello carries its own `"schema": 1`.
- `logs` is read-only. Never call `paths.attempt_dir` or `paths.run_dir`. Use `paths.recorded_attempts` / `paths.attempt_path` and `Attempt.stdout_path`.
- The projection is opened via `store_module.open_db` + `store_module.load_run` (never `Store.open`). The connection is closed on every path, before any streaming starts.
- Every refusal is `error_envelope(error)` on stdout + `typer.Exit(EXIT_ERROR)` (3), printed before any stream line.
- Stream lines are compact JSON via `_emit_stream_line` (flushes each line). `--pretty` affects only the non-follow envelope and refusals.
- No `{"event":"end"}` line, and no stop on terminal attempt status (that is 4.2).
- Polling uses `_watch_sleep(WATCH_POLL_SECONDS)` and `WATCH_MAX_POLLS`, both looked up at call time.
- Text is decoded `utf-8` with `errors="replace"`. Offsets are byte offsets.
- All new tests are unmarked (`unit` tier) in `tests/test_cli.py`. No subprocess, no `@pytest.mark.git`, no `e2e_fake`.
- Verification: `uv run pytest` (no separate lint or typecheck).

## Review Focus

1. The followed file contains invalid UTF-8 bytes (binary output, a stray `\xff`). Expected: the bytes are emitted as U+FFFD, the stream never stalls, and the next chunk's `offset` advances by raw bytes, not by decoded text length. Pinned by `test_utf8_complete_length_*` (Task 2) and `test_logs_follow_invalid_bytes_keep_byte_offsets` (Task 3).
2. `--since-offset` beyond the current end of the file (a consumer resuming from a saved cursor on a file still being written). Expected: hello with that offset, no chunk until the file grows past it, then a chunk starting exactly at that offset. Pinned by `test_logs_follow_since_offset_beyond_end_waits` (Task 3).
3. The followed file is truncated or rewritten shorter than the cursor mid-stream. Expected: no crash, no duplicate or rewound chunk, exit 0. Bytes below the cursor are not re-emitted. Pinned by `test_logs_follow_truncated_file_emits_nothing_new` (Task 3).
4. The reader closes the pipe (`am logs ... --follow | head -1`). Expected: exit 0, nothing on stderr, no traceback. Pinned by `test_logs_follow_closed_pipe_exits_zero_quietly` (Task 5).
5. A handled error raised after the hello (for example while reading). Expected: no envelope on stdout (the stream already started), a single `am logs: <message>` line on stderr, and exit 3. Pinned by `test_logs_follow_mid_stream_error_goes_to_stderr` (Task 5).

---

## File Structure

- Modify `src/agent_manager/cli.py`:
  - Around `logs_for` (currently lines 1858-1917): add `LogsSelection` and `select_logs`, then make `logs_for` delegate to them.
  - After `logs_for`, before `@app.command("logs")`: add `logs_follow_for`.
  - The `logs` command (currently lines 1920-1943): add the `--follow` and `--since-offset` options and the follow branch.
  - After `_stream_watch` (currently ends line 2181), before `@app.command("watch")`: add `_read_log_bytes`, `_utf8_complete_length`, `_logs_hello`, `_follow_logs` and `_stream_logs`. They sit there because they use `_watch_sleep`, `WATCH_POLL_SECONDS`, `WATCH_MAX_POLLS`, `_emit_stream_line` and `_silence_stdout`, which are defined there. `logs` calls them at runtime, so their position below `logs` is fine.
- Modify `tests/test_cli.py`: insert every new test and helper immediately before the line `CRASHED_AT = datetime(2026, 9, 23, 11, 30, 0, tzinfo=timezone.utc)` (currently line 5034), right after `test_logs_for_a_deterministic_phase_writes_nothing`. Each task appends below the previous task's block, so the anchor line is always directly below the new code. The tests reuse `_record_for_logs`, `_write_logs_attempt`, `_write_step_logs`, `_runs_snapshot`, `_attempt_rows`, `_recorded_dispatch`, `LOGS_RUN_ID`, `runner` and the `projection` fixture (all already in this file), plus `_stream` (defined in the watch section, around line 8608, and resolved at call time).

---

### Task 1: Extract a read-only `select_logs` that names the followed file

**Files:**
- Modify: `src/agent_manager/cli.py:1858-1917` (`logs_for`)
- Test: `tests/test_cli.py` (insert before `CRASHED_AT = ...`, currently line 5034)

**Interfaces:**
- Consumes: the existing `find_subtask`, `select_attempt`, `select_step_attempt`, `logs_payload`, `step_logs_payload`, `paths.recorded_attempts`, `paths.attempt_path`, `verify_step.STDOUT_LOG`, `resolve_repo_dir`, `store_module.open_db`, `store_module.load_run`, `UnknownRunError`, `UnknownCardError`.
- Produces:
  - `@dataclass(frozen=True) class LogsSelection` with fields `run: models.Run`, `story: models.StoryRun`, `subtask: models.SubtaskRun`, `phase: models.PhaseRun`, `attempt: models.Attempt | None` (set for agent phases, `None` for deterministic), `step_attempt: int | None = None`, `step_directory: Path | None = None`. Methods: `payload() -> dict[str, Any]` and `followed_path() -> Path | None`.
  - `select_logs(run_id: str, card: str, *, repo_dir: Path, phase: str | None = None, attempt: int | None = None) -> LogsSelection`.
  - `logs_for(...)` keeps its signature and output, and becomes `select_logs(...).payload()`.
  - Test helper `_implement_stdout() -> Path`.

- [ ] **Step 1: Write the failing tests**

Insert into `tests/test_cli.py` immediately before `CRASHED_AT = datetime(2026, 9, 23, 11, 30, 0, tzinfo=timezone.utc)`:

```python
def _implement_stdout() -> Path:
    """Where `_record_for_logs` puts `implement.1`'s stdout, read-only."""
    return paths.attempt_path(LOGS_RUN_ID, "card-1", "implement", 1) / "stdout.log"


def test_logs_without_follow_unchanged(projection):
    """Card 4.1: without `--follow`, `logs` is still exactly one envelope."""
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert set(envelope) == {"ok", "data"}
    assert "event" not in envelope
    assert "offset" not in envelope["data"]
    assert envelope["data"]["artifacts"]["stdout"]["text"] == "stdout of implement.1\n"


def test_select_logs_names_the_file_a_follow_reads(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    step_dir = _write_step_logs(LOGS_RUN_ID, 1)

    agent = cli.select_logs(LOGS_RUN_ID, "card-1", repo_dir=projection)
    assert agent.phase.name == "implement"
    assert agent.attempt is not None
    assert agent.attempt.n == 1
    assert agent.followed_path() == _implement_stdout()
    assert agent.payload() == cli.logs_for(LOGS_RUN_ID, "card-1", repo_dir=projection)

    step = cli.select_logs(LOGS_RUN_ID, "card-1", repo_dir=projection, phase="verify")
    assert step.phase.name == "verify"
    assert step.attempt is None
    assert step.step_attempt == 1
    assert step.followed_path() == step_dir / "stdout.log"
    assert step.payload() == cli.logs_for(
        LOGS_RUN_ID, "card-1", repo_dir=projection, phase="verify"
    )
```

- [ ] **Step 2: Run the tests to see the expected state**

Run: `uv run pytest tests/test_cli.py -k "test_logs_without_follow_unchanged or test_select_logs_names_the_file_a_follow_reads" -v`
Expected: `test_logs_without_follow_unchanged` PASSES (it pins today's behaviour before the refactor). `test_select_logs_names_the_file_a_follow_reads` FAILS with `AttributeError: module 'agent_manager.cli' has no attribute 'select_logs'`.

- [ ] **Step 3: Implement `LogsSelection` and `select_logs`, and make `logs_for` delegate**

In `src/agent_manager/cli.py`, replace the whole `logs_for` function (from `def logs_for(` through its closing `conn.close()`, currently lines 1858-1917) with:

```python
@dataclass(frozen=True)
class LogsSelection:
    """The attempt `am logs` reports on, as `select_logs` chose it.

    An agent phase carries its `Attempt` row. A deterministic phase has no
    row (spec e1b1e7d5 Decision 2), so it carries the `<phase>.N` number and
    directory found on disk instead, and `attempt` is `None`.
    """

    run: models.Run
    story: models.StoryRun
    subtask: models.SubtaskRun
    phase: models.PhaseRun
    attempt: models.Attempt | None
    step_attempt: int | None = None
    step_directory: Path | None = None

    def payload(self) -> dict[str, Any]:
        """`logs`' one-shot payload: `logs_payload` or `step_logs_payload`."""
        if self.attempt is not None:
            return logs_payload(
                self.run, self.story, self.subtask, self.phase, self.attempt
            )
        if self.step_attempt is None or self.step_directory is None:
            raise CliError(
                f"phase {self.phase.name!r} of card {self.subtask.card_id!r}"
                " was selected with neither an attempt row nor a step directory"
            )
        return step_logs_payload(
            self.run,
            self.story,
            self.subtask,
            self.phase,
            self.step_attempt,
            self.step_directory,
        )

    def followed_path(self) -> Path | None:
        """The file `logs --follow` reads: the agent attempt's recorded
        `stdout_path` (the launcher merges stderr into it), or a deterministic
        phase's `<phase>.N/stdout.log`. `None` when an agent attempt recorded
        no stdout path."""
        if self.attempt is not None:
            return self.attempt.stdout_path
        if self.step_directory is None:
            return None
        return self.step_directory / verify_step.STDOUT_LOG


def select_logs(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
) -> LogsSelection:
    """Which attempt §10's `logs` reports, shared by the one-shot and `--follow`.

    Read-only, like `status_for`: the projection is reached through the free
    `open_db` / `load_run` rather than `Store.open`, which would construct a
    `Journal` and therefore mint a run directory for a run that may not exist.
    The connection is closed on every path including the refusals.

    A `--phase` naming a deterministic phase is answered from disk: its
    attempts are the `<phase>.N` directories `run_one_step` created (spec
    e1b1e7d5). With no `--phase`, only recorded `Attempt` rows count.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        found = find_subtask(run, card)
        if found is None:
            raise UnknownCardError(
                f"card {card!r} is not in run {run_id!r}"
                f" (`agent-manager status {run_id}` lists the cards that are)"
            )
        story, subtask = found
        step = next(
            (
                item
                for item in subtask.phases
                if item.name == phase and item.kind == "deterministic"
            ),
            None,
        )
        if step is not None:
            # Read-only on disk: `recorded_attempts` and `attempt_path` create
            # nothing, unlike `attempt_dir` and `run_dir`.
            recorded = paths.recorded_attempts(run_id, card, step.name)
            n = select_step_attempt(subtask, step, recorded, attempt)
            directory = paths.attempt_path(run_id, card, step.name, n)
            return LogsSelection(
                run=run,
                story=story,
                subtask=subtask,
                phase=step,
                attempt=None,
                step_attempt=n,
                step_directory=directory,
            )
        chosen_phase, chosen_attempt = select_attempt(
            subtask, phase=phase, attempt=attempt
        )
        return LogsSelection(
            run=run,
            story=story,
            subtask=subtask,
            phase=chosen_phase,
            attempt=chosen_attempt,
        )
    finally:
        conn.close()


def logs_for(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
) -> dict[str, Any]:
    """§10's `logs`: one attempt of one card of one run, with its artifacts.

    `run_id` is required -- §10 writes `logs <run-id> <card>` and there is no
    "most recent run" reading of it to default to. The selection, and its
    read-only rules, are `select_logs`'; the artifacts are read after the
    projection connection is closed, from the paths the selection names.
    """
    return select_logs(
        run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
    ).payload()
```

- [ ] **Step 4: Run the tests and see them pass, plus every existing logs test**

Run: `uv run pytest tests/test_cli.py -k "logs" -v`
Expected: all PASS, including `test_logs_writes_nothing`, `test_logs_for_a_deterministic_phase_*` and the two new tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "refactor(logs): extract read-only select_logs naming the followed file"
```

---

### Task 2: Pure byte-cursor helpers `_read_log_bytes` and `_utf8_complete_length`

**Files:**
- Modify: `src/agent_manager/cli.py` (insert after `_stream_watch`, which ends with `raise typer.Exit(EXIT_ERROR) from None` just before `@app.command("watch")`, currently line 2181)
- Test: `tests/test_cli.py` (insert before `CRASHED_AT = ...`)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `_read_log_bytes(path: Path | None, offset: int) -> bytes`: the bytes of `path` from `offset` to EOF. Returns `b""` for `None`, a missing file, a directory, any `OSError`, or a file shorter than `offset`.
  - `_utf8_complete_length(data: bytes) -> int`: how many leading bytes of `data` end on a UTF-8 character boundary. Only a trailing truncated sequence with a valid lead byte (at most 3 bytes) is excluded.

- [ ] **Step 1: Write the failing tests**

Insert into `tests/test_cli.py` immediately before `CRASHED_AT = ...`:

```python
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", 0),
        (b"abc", 3),
        ("é".encode(), 2),
        ("é".encode()[:1], 0),
        (b"caf\xc3", 3),
        (b"x" + "€".encode()[:2], 1),
        (b"x" + "€".encode(), 4),
        (b"ab" + "😀".encode()[:3], 2),
        (b"ab" + "😀".encode(), 6),
    ],
)
def test_utf8_complete_length_holds_back_only_a_truncated_tail(data, expected):
    assert cli._utf8_complete_length(data) == expected


@pytest.mark.parametrize(
    "data",
    [b"ok\xff", b"ok\xc0", b"\x80\x80\x80\x80", b"ok\x80"],
)
def test_utf8_complete_length_never_holds_back_invalid_bytes(data):
    """A byte that cannot start a sequence is emitted (as U+FFFD), so an
    invalid tail can never stall the stream."""
    assert cli._utf8_complete_length(data) == len(data)


def test_read_log_bytes_reads_from_the_offset(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_bytes(b"0123456789")

    assert cli._read_log_bytes(log, 0) == b"0123456789"
    assert cli._read_log_bytes(log, 4) == b"456789"
    assert cli._read_log_bytes(log, 10) == b""
    assert cli._read_log_bytes(log, 50) == b""


def test_read_log_bytes_is_empty_for_anything_unreadable(tmp_path):
    assert cli._read_log_bytes(None, 0) == b""
    assert cli._read_log_bytes(tmp_path / "missing.log", 0) == b""
    assert cli._read_log_bytes(tmp_path, 0) == b""
    assert not (tmp_path / "missing.log").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "utf8_complete_length or read_log_bytes" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute '_utf8_complete_length'` (and `_read_log_bytes`).

- [ ] **Step 3: Implement the helpers**

In `src/agent_manager/cli.py`, insert immediately after the end of `_stream_watch` (after its line `        raise typer.Exit(EXIT_ERROR) from None`) and before `@app.command("watch")`:

```python
def _read_log_bytes(path: Path | None, offset: int) -> bytes:
    """`path`'s bytes from `offset` to its current end, for `logs --follow`.

    Never raises for the file's state: a path not recorded, a file not
    written yet, a directory, an unreadable mode, or a file shorter than
    `offset` all read as `b""`, so a poll that finds nothing waits for the
    next one (spec card 4.1, "File not there yet"). Opens for reading only,
    so nothing is created.
    """
    if path is None:
        return b""
    try:
        with Path(path).open("rb") as handle:
            handle.seek(offset)
            return handle.read()
    except OSError:
        return b""


def _utf8_complete_length(data: bytes) -> int:
    """How many leading bytes of `data` end on a UTF-8 character boundary.

    Only a trailing sequence whose lead byte is valid but whose continuation
    bytes have not all arrived yet is held back: at most 3 bytes. Any other
    byte, including a stray continuation or an invalid lead, counts as
    complete, so `errors="replace"` turns it into U+FFFD and an invalid
    tail can never stall the stream.
    """
    end = len(data)
    for index in range(end - 1, max(end - 4, 0) - 1, -1):
        byte = data[index]
        if byte & 0xC0 == 0x80:
            continue
        if byte < 0x80:
            return end
        if 0xC2 <= byte <= 0xDF:
            needed = 2
        elif 0xE0 <= byte <= 0xEF:
            needed = 3
        elif 0xF0 <= byte <= 0xF4:
            needed = 4
        else:
            return end
        return index if end - index < needed else end
    return end
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "utf8_complete_length or read_log_bytes" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(logs): byte-cursor reader and UTF-8 boundary helper for --follow"
```

---

### Task 3: `--follow` and `--since-offset` with a hello line, contiguous chunks and polling

**Files:**
- Modify: `src/agent_manager/cli.py`: add `logs_follow_for` after `logs_for`; rewrite the `logs` command (currently lines 1920-1943); add `_logs_hello`, `_follow_logs` and `_stream_logs` right after `_utf8_complete_length` (from Task 2)
- Test: `tests/test_cli.py` (insert before `CRASHED_AT = ...`)

**Interfaces:**
- Consumes: `select_logs` / `LogsSelection.followed_path()` (Task 1), `_read_log_bytes` and `_utf8_complete_length` (Task 2), and the existing `_watch_sleep`, `WATCH_POLL_SECONDS`, `WATCH_MAX_POLLS` and `_emit_stream_line`.
- Produces:
  - `logs_follow_for(run_id: str, card: str, *, repo_dir: Path, phase: str | None = None, attempt: int | None = None, since_offset: int = 0) -> Path | None`. Task 4 tightens it to `-> Path` and adds refusals.
  - `_logs_hello(path: Path, offset: int) -> dict[str, Any]` returns `{"event": "logs", "schema": 1, "path": str(path), "offset": offset}`.
  - `_follow_logs(path: Path, *, offset: int, sleep: Callable[[float], None], max_polls: int | None) -> Iterator[dict[str, Any]]` yields `{"offset": int, "text": str}`.
  - `_stream_logs(path: Path, *, offset: int) -> None`. Task 5 adds the ending handling.
  - New `logs` options `--follow: bool` and `--since-offset: int | None` (metavar `BYTES`).
  - Test helpers `_logs_follow(monkeypatch, *args, actions=(), follow=True) -> tuple[Result, list[float]]`, `_logs_args(projection, *extra) -> list[str]`, `_logs_hello_line(path, offset=0) -> dict` and `_assert_contiguous(chunks, start) -> int`.

- [ ] **Step 1: Write the failing tests**

Insert into `tests/test_cli.py` immediately before `CRASHED_AT = ...`:

```python
def _logs_follow(monkeypatch, *args: str, actions=(), follow: bool = True):
    """Run `am logs ARGS --follow` for exactly `len(actions)` polls after the backlog.

    Modelled on `_watch_follow`: sleep `i` runs `actions[i]` (an append, a
    truncate, a Ctrl-C) before poll `i` reads. `follow=False` drops the flag
    so the refusal of `--since-offset` alone can be driven through the same
    helper. Returns the result and the seconds each sleep was asked for.
    """
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        actions[len(sleeps) - 1]()

    monkeypatch.setattr(cli, "_watch_sleep", fake_sleep)
    monkeypatch.setattr(cli, "WATCH_MAX_POLLS", len(actions))
    argv = ["logs", *args, *(["--follow"] if follow else [])]
    return runner.invoke(cli.app, argv), sleeps


def _logs_args(projection: Path, *extra: str) -> list[str]:
    return [LOGS_RUN_ID, "card-1", *extra, "--repo-dir", str(projection)]


def _logs_hello_line(path: Path, offset: int = 0) -> dict[str, Any]:
    return {"event": "logs", "schema": 1, "path": str(path), "offset": offset}


def _assert_contiguous(chunks: list[dict[str, Any]], start: int) -> int:
    """Each chunk starts where the last ended, in bytes; none is empty.

    Only for valid UTF-8 content: a U+FFFD from replacement re-encodes to a
    different byte length than the bytes it replaced.
    """
    cursor = start
    for chunk in chunks:
        assert set(chunk) == {"offset", "text"}, chunk
        assert chunk["offset"] == cursor, chunks
        assert chunk["text"], chunks
        cursor += len(chunk["text"].encode("utf-8"))
    return cursor


def test_logs_follow_hello_shape(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)

    result, sleeps = _logs_follow(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert sleeps == []
    lines = _stream(result)
    assert lines[0] == _logs_hello_line(_implement_stdout())
    assert all("ok" not in line for line in lines)
    assert all("event" not in line for line in lines[1:])
    assert result.stdout.endswith("\n")
    assert all(": " not in text for text in result.stdout.splitlines())
    assert result.stderr == ""

    # `--pretty` only shapes a refusal: the stream is byte-for-byte the same.
    pretty, _ = _logs_follow(monkeypatch, *_logs_args(projection, "--pretty"))
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == result.stdout


def test_logs_follow_streams_backlog_with_offsets(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)
    content = "first line\nsecond líne\n€uro\n"
    _implement_stdout().write_bytes(content.encode("utf-8"))

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines[0] == _logs_hello_line(_implement_stdout())
    chunks = lines[1:]
    assert chunks[0]["offset"] == 0
    assert "".join(chunk["text"] for chunk in chunks) == content
    assert _assert_contiguous(chunks, 0) == len(content.encode("utf-8"))


def test_logs_follow_multibyte_not_split(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)
    stdout = _implement_stdout()
    stdout.write_bytes(b"caf\xc3")  # the first byte of "é" only

    def finish_the_character() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"\xa9 ok\n")

    result, _ = _logs_follow(
        monkeypatch,
        *_logs_args(projection),
        actions=[finish_the_character, lambda: None],
    )

    assert result.exit_code == 0, result.output
    chunks = _stream(result)[1:]
    assert chunks == [
        {"offset": 0, "text": "caf"},
        {"offset": 3, "text": "é ok\n"},
    ]
    assert all("�" not in chunk["text"] for chunk in chunks)
    assert _assert_contiguous(chunks, 0) == stdout.stat().st_size


def test_logs_follow_since_offset_resumes(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)
    content = _implement_stdout().read_bytes()
    assert content == b"stdout of implement.1\n"

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection, "--since-offset", "10"))

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines[0] == _logs_hello_line(_implement_stdout(), 10)
    assert lines[1:] == [{"offset": 10, "text": content[10:].decode("utf-8")}]


def test_logs_follow_picks_up_appended_data(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)
    stdout = _implement_stdout()
    original = stdout.read_bytes()

    def append_more() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"more\n")

    # Poll 1 sees the append; poll 2 sees nothing new, so it must not repeat.
    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[append_more, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert sleeps == [cli.WATCH_POLL_SECONDS, cli.WATCH_POLL_SECONDS]
    lines = _stream(result)
    assert lines[1:] == [
        {"offset": 0, "text": original.decode("utf-8")},
        {"offset": len(original), "text": "more\n"},
    ]
    assert all(line.get("event") != "end" for line in lines)


def test_logs_follow_waits_for_missing_file(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, stdout=False)
    stdout = _implement_stdout()

    def still_absent() -> None:
        assert not stdout.exists()

    def create_it() -> None:
        stdout.write_bytes(b"late\n")

    result, _ = _logs_follow(
        monkeypatch,
        *_logs_args(projection),
        actions=[still_absent, create_it, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(stdout),
        {"offset": 0, "text": "late\n"},
    ]


def test_logs_follow_deterministic_phase_follows_stdout_log(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)
    second = _write_step_logs(LOGS_RUN_ID, 2)

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection, "--phase", "verify"))

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _logs_hello_line(second / "stdout.log"),
        {"offset": 0, "text": "==> uv run pytest (exit 1)\nstdout of verify.2\n"},
    ]


def test_logs_follow_writes_nothing(projection, monkeypatch):
    """`logs --follow` is read-only like `logs`: a missing stdout file is
    waited on, never created, and a refusal mints no run directory."""
    _record_for_logs(projection, LOGS_RUN_ID, stdout=False)
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    success, _ = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[lambda: None]
    )
    assert success.exit_code == 0, success.output
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


def test_logs_follow_since_offset_beyond_end_waits(projection, monkeypatch):
    """Review Focus 2: a cursor past EOF is not an error; bytes appear once
    the file grows past it, starting exactly at the cursor."""
    _record_for_logs(projection, LOGS_RUN_ID)
    stdout = _implement_stdout()
    size = stdout.stat().st_size

    def grow_short_of_the_cursor() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"a" * (500 - size))

    def grow_past_the_cursor() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"b" * 505)

    result, _ = _logs_follow(
        monkeypatch,
        *_logs_args(projection, "--since-offset", "1000"),
        actions=[grow_short_of_the_cursor, grow_past_the_cursor],
    )

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _logs_hello_line(stdout, 1000),
        {"offset": 1000, "text": "bbbbb"},
    ]


def test_logs_follow_truncated_file_emits_nothing_new(projection, monkeypatch):
    """Review Focus 3: a file cut below the cursor is no crash and no rewind;
    bytes below the cursor are never re-emitted."""
    _record_for_logs(projection, LOGS_RUN_ID)
    stdout = _implement_stdout()
    original = stdout.read_bytes()

    def truncate() -> None:
        stdout.write_bytes(b"")

    def rewrite_shorter() -> None:
        stdout.write_bytes(b"new\n")

    result, _ = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[truncate, rewrite_shorter]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(stdout),
        {"offset": 0, "text": original.decode("utf-8")},
    ]


def test_logs_follow_invalid_bytes_keep_byte_offsets(projection, monkeypatch):
    """Review Focus 1: invalid UTF-8 becomes U+FFFD, and the next offset
    still counts raw bytes, not decoded characters."""
    _record_for_logs(projection, LOGS_RUN_ID)
    stdout = _implement_stdout()
    stdout.write_bytes(b"ok\xff\xfe\n")

    def append_more() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"next\n")

    result, _ = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[append_more]
    )

    assert result.exit_code == 0, result.output
    assert _stream(result)[1:] == [
        {"offset": 0, "text": "ok��\n"},
        {"offset": 5, "text": "next\n"},
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "logs_follow" -v`
Expected: every `test_logs_follow_*` above FAILS. Typer exits with code 2 and `No such option: --follow`, so the `exit_code == 0` (or `== cli.EXIT_ERROR`) assertions fail.

- [ ] **Step 3: Add `logs_follow_for` after `logs_for`**

In `src/agent_manager/cli.py`, insert immediately after the `logs_for` function from Task 1 and before `@app.command("logs")`:

```python
def logs_follow_for(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
    since_offset: int = 0,
) -> Path | None:
    """Validate `am logs --follow` and name the file it streams.

    The same selection as `logs_for` (`select_logs`), so every refusal the
    one-shot makes is made here too, before the hello line. The projection
    connection is closed by `select_logs` before this returns, and streaming
    reads only the returned file.
    """
    selection = select_logs(
        run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
    )
    return selection.followed_path()
```

- [ ] **Step 4: Add `_logs_hello`, `_follow_logs` and `_stream_logs` after `_utf8_complete_length`**

In `src/agent_manager/cli.py`, insert immediately after `_utf8_complete_length` (Task 2) and before `@app.command("watch")`:

```python
def _logs_hello(path: Path, offset: int) -> dict[str, Any]:
    """The first line of `am logs --follow`: which file, from which byte.

    Its own `schema`, independent of the journal's and of `watch`'s hello,
    so the chunk shape can evolve without touching either.
    """
    return {"event": "logs", "schema": 1, "path": str(path), "offset": offset}


def _follow_logs(
    path: Path,
    *,
    offset: int,
    sleep: Callable[[float], None],
    max_polls: int | None,
) -> Iterator[dict[str, Any]]:
    """`path`'s bytes from `offset` as `{"offset", "text"}` chunks, then each append.

    One read at once for the backlog, then `sleep(WATCH_POLL_SECONDS)` and
    another read, `max_polls` times or forever when it is `None`. One byte
    cursor spans every read, so chunks are contiguous: each starts where the
    last ended. A trailing partial UTF-8 character stays below the cursor
    and is read again, completed, on a later poll. A read that finds nothing
    past the cursor (no file yet, a file shorter than the cursor) yields
    nothing. Attempt status is not consulted: ending the stream on a
    terminal attempt is card 4.2's.
    """
    cursor = offset
    polls = 0
    while True:
        data = _read_log_bytes(path, cursor)
        complete = _utf8_complete_length(data)
        if complete:
            yield {
                "offset": cursor,
                "text": data[:complete].decode("utf-8", errors="replace"),
            }
            cursor += complete
        if max_polls is not None and polls >= max_polls:
            return
        sleep(WATCH_POLL_SECONDS)
        polls += 1


def _stream_logs(path: Path, *, offset: int) -> None:
    """The body of `am logs --follow`, once `logs_follow_for` has accepted it.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll.
    """
    _emit_stream_line(_logs_hello(path, offset))
    for chunk in _follow_logs(
        path, offset=offset, sleep=_watch_sleep, max_polls=WATCH_MAX_POLLS
    ):
        _emit_stream_line(chunk)
```

- [ ] **Step 5: Add the options and the follow branch to the `logs` command**

In `src/agent_manager/cli.py`, replace the whole `logs` command (from `@app.command("logs")` through its final `typer.echo(render(ok_envelope(payload), pretty=pretty))`) with:

```python
@app.command("logs")
def logs(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run to read."),
    card: str = typer.Argument(..., metavar="CARD", help="The subtask card id."),
    phase: str | None = typer.Option(
        None, "--phase", help="Which phase. Defaults to the last one with attempts."
    ),
    attempt: int | None = typer.Option(
        None, "--attempt", help="Which attempt. Defaults to the highest recorded."
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        help=(
            "Keep printing the attempt's stdout as it grows, one JSON object"
            " per line, until interrupted."
        ),
    ),
    since_offset: int | None = typer.Option(
        None,
        "--since-offset",
        metavar="BYTES",
        help="With --follow, start at this byte offset of the stdout file (default 0).",
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print one attempt's prompt, result and captured stdout/stderr.

    With --follow, print a hello line naming the attempt's stdout file and
    then its bytes as `{"offset", "text"}` lines, the existing content first
    and then each append, until interrupted. A refusal is still one envelope
    at exit 3, printed before any stream line.
    """
    offset = since_offset if since_offset is not None else 0
    try:
        if follow:
            followed = logs_follow_for(
                run_id,
                card,
                repo_dir=repo_dir,
                phase=phase,
                attempt=attempt,
                since_offset=offset,
            )
        else:
            payload = logs_for(
                run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
            )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    if follow:
        _stream_logs(followed, offset=offset)
        return
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "logs" -v`
Expected: PASS for every `test_logs_follow_*` test written in Step 1, for the Task 1 and Task 2 tests, and for every pre-existing `test_logs_*` test.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(logs): --follow streams a hello line and contiguous byte-offset chunks"
```

---

### Task 4: Refusals before the first stream line

**Files:**
- Modify: `src/agent_manager/cli.py` (`logs_follow_for` from Task 3, and the `try:` block of the `logs` command)
- Test: `tests/test_cli.py` (insert before `CRASHED_AT = ...`)

**Interfaces:**
- Consumes: `_logs_follow` and `_logs_args` (Task 3), `select_logs` / `LogsSelection` (Task 1), and the test helpers `_recorded_dispatch` and `_record_for_logs`.
- Produces: `logs_follow_for(...) -> Path` (tightened from `Path | None`). It raises `CliError` for a negative `since_offset` and for an agent attempt with no `stdout_path`. The `logs` command raises `CliError` when `--since-offset` is given without `--follow`.

- [ ] **Step 1: Write the failing test**

Insert into `tests/test_cli.py` immediately before `CRASHED_AT = ...`:

```python
def _record_review_without_stdout_path(root: Path) -> None:
    """Add an agent `review` phase whose one attempt recorded no stdout path."""
    opened = store_module.Store.open(root, LOGS_RUN_ID)
    try:
        opened.record_phase(
            "story-1", "card-1", models.PhaseRun(name="review", kind="agent", status="failed")
        )
        opened.record_attempt(
            "story-1",
            "card-1",
            "review",
            models.Attempt(
                n=1, dispatch=_recorded_dispatch(LOGS_RUN_ID), status="harness_error"
            ),
        )
    finally:
        opened.close()


@pytest.mark.parametrize(
    ("argv", "follow", "kind", "message"),
    [
        (["no-such-run", "card-1"], True, "UnknownRunError", None),
        ([LOGS_RUN_ID, "card-9"], True, "UnknownCardError", None),
        ([LOGS_RUN_ID, "card-1", "--phase", "reveiw"], True, "UnknownPhaseError", None),
        (
            [LOGS_RUN_ID, "card-1", "--phase", "explore", "--attempt", "9"],
            True,
            "UnknownAttemptError",
            None,
        ),
        ([LOGS_RUN_ID, "card-1", "--phase", "verify"], True, "UnknownAttemptError", None),
        (
            [LOGS_RUN_ID, "card-1", "--phase", "review"],
            True,
            "CliError",
            "attempt 1 of phase 'review' of card 'card-1' recorded no stdout path,"
            " so there is no file to follow",
        ),
        (
            [LOGS_RUN_ID, "card-1", "--since-offset", "-1"],
            True,
            "CliError",
            "--since-offset must be 0 or more, got -1",
        ),
        (
            [LOGS_RUN_ID, "card-1", "--since-offset", "0"],
            False,
            "CliError",
            "--since-offset needs --follow: it resumes a stream,"
            " and without --follow there is no stream",
        ),
        (
            [LOGS_RUN_ID, "card-1", "--since-offset", "5"],
            False,
            "CliError",
            "--since-offset needs --follow: it resumes a stream,"
            " and without --follow there is no stream",
        ),
    ],
)
def test_logs_follow_refusals(projection, monkeypatch, argv, follow, kind, message):
    _record_for_logs(projection, LOGS_RUN_ID)
    _record_review_without_stdout_path(projection)

    result, sleeps = _logs_follow(
        monkeypatch,
        *argv,
        "--repo-dir",
        str(projection),
        actions=[lambda: None],
        follow=follow,
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert sleeps == []
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert envelope["ok"] is False
    assert "event" not in envelope
    assert envelope["error"]["type"] == kind
    if message is not None:
        assert envelope["error"]["message"] == message
```

- [ ] **Step 2: Run the test to see the expected failures**

Run: `uv run pytest tests/test_cli.py -k "test_logs_follow_refusals" -v`
Expected: the five `Unknown*Error` cases PASS already, because Task 3 validates through `select_logs`. They are kept as regression guards. The `review` case FAILS (a hello line with `"path": "None"` is streamed at exit 0). The `--since-offset -1` case FAILS: the hello line is printed before anything rejects the offset. The negative `seek` then either reads as `b""` (exit 0) or escapes as a `ValueError` (exit 1). Neither is the exit-3 single-envelope result. The two `--since-offset` without `--follow` cases FAIL (exit 0 with a success envelope).

- [ ] **Step 3: Add the refusals to `logs_follow_for`**

In `src/agent_manager/cli.py`, replace the body of `logs_follow_for` (Task 3) so the function reads:

```python
def logs_follow_for(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
    since_offset: int = 0,
) -> Path:
    """Validate `am logs --follow` and name the file it streams.

    The same selection as `logs_for` (`select_logs`), so every refusal the
    one-shot makes is made here too, before the hello line. On top of those:
    a negative `--since-offset` (worded as `watch --since` is), and an agent
    attempt that recorded no stdout path, since the hello must name a file.
    The projection connection is closed by `select_logs` before this
    returns, and streaming reads only the returned file.
    """
    if since_offset < 0:
        raise CliError(f"--since-offset must be 0 or more, got {since_offset}")
    selection = select_logs(
        run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
    )
    followed = selection.followed_path()
    if followed is None:
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
    return followed
```

- [ ] **Step 4: Refuse `--since-offset` without `--follow` in the `logs` command**

In the `logs` command, replace the line `    try:` and the `        if follow:` line directly below it (the start of the `try` block from Task 3) with:

```python
    try:
        # `None` means --since-offset was not given; any given value, 0
        # included, needs --follow, as --from-now does for `watch`.
        if since_offset is not None and not follow:
            raise CliError(
                "--since-offset needs --follow: it resumes a stream,"
                " and without --follow there is no stream"
            )
        if follow:
```

The rest of the `try` block (the `logs_follow_for(...)` call, the `else:` with `logs_for(...)`, and the `except HANDLED` arm) stays as written in Task 3.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "logs" -v`
Expected: PASS, including all nine `test_logs_follow_refusals` cases and every earlier logs test.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(logs): refuse bad --since-offset and pathless attempts before streaming"
```

---

### Task 5: Stream endings (Ctrl-C, closed pipe, mid-stream error)

**Files:**
- Modify: `src/agent_manager/cli.py` (`_stream_logs` from Task 3)
- Test: `tests/test_cli.py` (insert before `CRASHED_AT = ...`)

**Interfaces:**
- Consumes: `_stream_logs`, `_follow_logs` and `_read_log_bytes` (Tasks 2-3), the existing `_silence_stdout`, `HANDLED` and `EXIT_ERROR`, and `cli.CliError` (re-exported from `agent_manager.runs`).
- Produces: `_stream_logs(path: Path, *, offset: int) -> None` with the same signature. `KeyboardInterrupt` returns (exit 0). `BrokenPipeError` calls `_silence_stdout()` and returns (exit 0). `HANDLED` prints `am logs: <message>` to stderr and raises `typer.Exit(EXIT_ERROR)`.

- [ ] **Step 1: Write the failing tests**

Insert into `tests/test_cli.py` immediately before `CRASHED_AT = ...`:

```python
def test_logs_follow_ctrl_c_exits_zero(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)

    def press_ctrl_c() -> None:
        raise KeyboardInterrupt

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection), actions=[press_ctrl_c])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]


def test_logs_follow_closed_pipe_exits_zero_quietly(projection, monkeypatch):
    """Review Focus 4: `am logs ... --follow | head -1`."""
    _record_for_logs(projection, LOGS_RUN_ID)
    real_emit = cli._emit_stream_line
    emitted: list[Any] = []

    def emit_into_a_closed_pipe(obj) -> None:
        emitted.append(obj)
        if len(emitted) == 2:  # the reader went away after the hello line
            raise BrokenPipeError(32, "Broken pipe")
        real_emit(obj)

    monkeypatch.setattr(cli, "_emit_stream_line", emit_into_a_closed_pipe)

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection), actions=[lambda: None])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [_logs_hello_line(_implement_stdout())]


def test_logs_follow_mid_stream_error_goes_to_stderr(projection, monkeypatch):
    """Review Focus 5: after the hello no envelope can follow, so a handled
    error is one stderr line and exit 3, as `watch --follow` does."""
    _record_for_logs(projection, LOGS_RUN_ID)
    real_read = cli._read_log_bytes
    reads: list[int] = []

    def read_then_fail(path, offset):
        reads.append(offset)
        if len(reads) == 2:
            raise cli.CliError("the log went away")
        return real_read(path, offset)

    monkeypatch.setattr(cli, "_read_log_bytes", read_then_fail)

    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[lambda: None, lambda: None]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert len(sleeps) == 1  # the stream ended on the failing poll
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]
    assert result.stderr == "am logs: the log went away\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "ctrl_c_exits_zero or logs_follow_closed_pipe or mid_stream_error_goes_to_stderr" -v`
Expected: all three FAIL. Ctrl-C ends as Click's `Abort` with a non-zero exit and `Aborted!` on stderr. The broken pipe is a non-zero exit from Click's EPIPE handling. The `CliError` escapes the command as an unhandled exception (exit code 1).

- [ ] **Step 3: Add the ending handling to `_stream_logs`**

In `src/agent_manager/cli.py`, replace the whole `_stream_logs` function from Task 3 with:

```python
def _stream_logs(path: Path, *, offset: int) -> None:
    """The body of `am logs --follow`, once `logs_follow_for` has accepted it.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll. Ctrl-C and a closed pipe are
    how a stream normally ends: exit 0, nothing on stderr. After the hello
    line no envelope can be printed, so a handled error goes to stderr and
    the exit is `EXIT_ERROR`, mirroring `_stream_watch`.
    """
    try:
        _emit_stream_line(_logs_hello(path, offset))
        for chunk in _follow_logs(
            path, offset=offset, sleep=_watch_sleep, max_polls=WATCH_MAX_POLLS
        ):
            _emit_stream_line(chunk)
    except KeyboardInterrupt:
        return
    except BrokenPipeError:
        _silence_stdout()
        return
    except HANDLED as error:
        typer.echo(f"am logs: {error}", err=True)
        raise typer.Exit(EXIT_ERROR) from None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "logs" -v`
Expected: PASS for the three new tests and every earlier logs test.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(logs): --follow ends quietly on Ctrl-C and closed pipe, stderr on errors"
```

---

### Task 6: Full verification

**Files:** none modified.

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest`
Expected: all tests pass (default `unit` + `git` tiers). Every new test is unmarked and finishes well within the unit tier's 0.5s budget. No test sleeps for real, since `_watch_sleep` is monkeypatched and `WATCH_MAX_POLLS` is bounded.

- [ ] **Step 2: Confirm scope**

Run: `git diff ami/task-3-3-e2e-fake-detach-ef6e5633 --stat`
Expected: only `src/agent_manager/cli.py` and `tests/test_cli.py` change (the spec and plan under `docs/superpowers/` appear only if they were committed). There is no `"end"` event anywhere in the diff (`git diff ami/task-3-3-e2e-fake-detach-ef6e5633 -- src | grep '"end"'` prints nothing).

---

## Spec coverage map

| Spec item | Task |
| --- | --- |
| Shared read-only selection helper; `logs_for` output unchanged (test 11) | 1 |
| Agent `Attempt.stdout_path` / deterministic `<phase>.N/stdout.log` | 1, 3 (test 7) |
| Hello `{"event":"logs","schema":1,"path","offset"}` (test 1), `--pretty` ignored | 3 |
| Contiguous byte-offset chunks, `errors="replace"`, no empty chunks (test 2) | 2, 3 |
| Partial UTF-8 held back at most 3 bytes (test 3) | 2, 3 |
| `--since-offset` resume (test 4) | 3 |
| Polling via `_watch_sleep` / `WATCH_POLL_SECONDS` / `WATCH_MAX_POLLS`, appended data (test 5) | 3 |
| Missing / short / unreadable file waits silently (test 6) | 2, 3 |
| Read-only, connection closed before streaming (test 10) | 1, 3 |
| Refusals: unknown run/card/phase/attempt, negative offset, offset without follow, no stdout_path (test 8) | 4 |
| Ctrl-C exit 0 (test 9), BrokenPipe, mid-stream HANDLED to stderr + exit 3 | 5 |
| No `end` event, no stop on terminal status | 3 (asserted in test 5), 6 |
