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
