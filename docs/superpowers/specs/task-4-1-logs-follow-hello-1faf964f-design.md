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
