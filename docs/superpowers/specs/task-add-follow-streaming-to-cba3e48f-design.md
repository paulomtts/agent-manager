# Subtask cba3e48f: Add `--follow` streaming to `am watch`

Parent story: 0adbb38b ("am watch exposes the journal as a public, documented stream"). Source design: `docs/superpowers/specs/2026-10-02-am-watch-design.md`, sections 3.2, 3.3 and 3.6, under the constraints in 3.7. This subtask narrows that design to the streaming form of the command. It builds on the terminating form, which sibling 43f4f076 has already landed in `src/agent_manager/cli.py`: `WATCH_HANDLED`, `_check_watch_run_id`, `_journal_events`, `watch_for` and the `@app.command("watch")` function.

## Scope

Add a `--follow` option to the existing `am watch` command, giving `am watch [RUN_ID | --all] [--since SEQ] [--follow]`. Reuse the sibling's argument validation, run-id check, `_for_reading` journal access and `paths.list_run_ids()` walk. Do not fork them.

Out of scope:
- Everything about the non-follow form, which belongs to 43f4f076. Its envelope, its output and its tests stay unchanged.
- README changes. This includes the new envelope-rule wording ("every terminating command prints one line of JSON; `am watch --follow` prints one JSON object per line until stopped") and the JournalLine contract section. Both belong to b442ff58. Leave README.md untouched.
- inotify, or any new dependency. v1 uses polling only.
- New or synthetic event kinds, including any "run finished" or "escalation" line. Section 3.3 forbids these.

## Observable behavior

- **Validation happens first.** Before anything is written to stdout, `--follow` runs the same checks the non-follow form runs. These are: exactly one of RUN_ID and `--all`, `--since` of 0 or more, a run id that is a single directory name, a journal that exists for RUN_ID, and an initial read that raises no `CorruptJournalError`.
- **Refusal.** When any of those checks fails, the command prints the usual `{"ok": false, "error": {...}}` envelope through `render(error_envelope(...))`, exits with `EXIT_ERROR` (3), and writes no streamed line. The first line of output is therefore enough to tell a stream from a refusal. A refusal for an unknown run creates no directory under `<data dir>/runs/`.
- **Hello line.** On success, the first stdout line is exactly one object: `{"event": "watch", "schema": 1, "am": agent_manager.__version__, "runs_dir": str(paths.data_dir() / "runs")}`. It is the only line in the stream that is not a JournalLine. It is written even when there is nothing to stream yet.
- **Body.** After the hello line, the stream carries bare JSON objects, one per line, each a `JournalLine` dumped in JSON mode with the same fields as the non-follow `events`. There is no `{"ok":...}` wrapper. Each line is compact and newline-terminated, whether or not `--pretty` is given; `--pretty` only affects a refusal envelope. Every line is flushed when it is written, so a consumer reading a pipe (`am watch --follow | while read line; ...`) receives it promptly.
- **Backlog, then live lines.** The stream first emits the existing lines with `seq > --since`, then keeps emitting newly appended lines until it is interrupted.
- **Cursor.** Each run keeps its own cursor, keyed by `(run_id, seq)` and never by time, byte offset or line count. A line is emitted only when its `seq` is greater than that run's last emitted `seq`, and lines within a run go out in `seq` order. No seq is emitted twice, and none is skipped.
- **Lease takeover.** A new owner calls `Journal.reseek()` and keeps appending to the same file at a higher `seq`. The watcher follows this through the same cursor rule, with no special case for takeovers.
- **Torn tail.** An unterminated final line is a write in flight. It is skipped on this poll (`read(ignore_torn_tail=True)`) and emitted on a later poll once it is complete.
- **Unknown event kinds.** These are skipped by `Journal.read`, as in the non-follow form.
- **Journal access.** Journals are opened only through `Journal._for_reading(run_id)`, never through `Journal(run_id)`. The data directory is resolved only through `paths.data_dir()`, so `XDG_DATA_HOME` is honored.
- **`--all`.** Each poll lists the runs again, so a run directory or journal that appears after the command starts is picked up. Its backlog above `--since` is emitted first, then its live lines. A run that has no journal yet, or a missing `runs/` or data directory, means nothing to emit rather than an error, so a watcher pointed at the wrong data directory sees only the hello line. Across runs there is no global order. Within one poll, lines go out in `(run_id, seq)` order.
- **Polling.** Each watched file is polled about every 250ms. Polling is internal and appears nowhere in the output: there are no heartbeat lines and no timing fields. The stream must look identical if polling is later replaced by inotify.
- **Interruption.** Ctrl-C (`KeyboardInterrupt`) or a closed stdout pipe ends the stream quietly, with no traceback and exit 0.
- **Testability.** The loop's sleep and its stop condition must be injectable, for example a sleep callable or an iteration bound. Tests can then drive appends between polls deterministically, without wall-clock waits and without sending signals.

## Error paths

- A refusal before the stream starts follows the rules above.
- After the hello line, a run's journal can become corrupt: a non-JSON line that is not the torn tail. An envelope can no longer be printed at that point, because it would break the contract that every line after the first is a JournalLine. The stream therefore ends with exit 3, and a one-line message naming the file and line goes to stderr. Nothing else is written to stdout.
- After the hello line, a followed run's journal file can disappear under RUN_ID. This is treated as nothing new to emit, not as an error.

## Tests

The tier rule in force is §14 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. Code with no real `git`, `brd` or `claude` subprocess goes in the default (unit) suite. Only the single slow, real-money end-to-end test is marked `e2e`, and `pyproject.toml` has no other marker. The six-tier addendum (`2026-10-02-test-tier-design.md`) is only a proposal and has not landed, so it does not govern these tests. The sibling 43f4f076 sets the precedent these tests follow. Each test sets `XDG_DATA_HOME` to `tmp_path` and writes `journal.jsonl` lines directly, or appends through a real `Journal` and `reseek()`. Tests use `CliRunner` against `app`, or call the follow function directly with an injected sleep and stop. None of them is marked `e2e`.

1. `test_watch_follow_observes_a_line_appended_after_start`: the hello line comes first, then the backlog, then a line appended between polls appears exactly once. Tier: default/unit (`tests/test_cli.py`).
2. `test_watch_follow_refusal_prints_envelope_and_no_stream`: an unknown RUN_ID with `--follow` exits 3, the only stdout line is the `ok: false` envelope with type `UnknownRunError`, there is no hello line, and no run directory is created. Tier: default/unit (`tests/test_cli.py`).
3. `test_watch_follow_survives_lease_takeover`: lines are written as one `Journal` instance. A second instance opened for the same run, playing the new owner, calls `reseek()` and appends at higher seqs between polls. The watcher's output seqs are strictly increasing and contiguous, with no duplicate and no gap. Tier: default/unit (`tests/test_cli.py`).
4. `test_watch_follow_hello_line_shape`: the first line equals `{"event": "watch", "schema": 1, "am": __version__, "runs_dir": <tmp data dir>/runs}`, and `--since` drops backlog lines at or below SEQ. Tier: default/unit (`tests/test_cli.py`).
5. `test_watch_follow_all_picks_up_a_run_created_later`: with `--all` and no runs at the start, the output is only the hello line. A run journal created between polls then has its lines emitted, and a torn tail on it is held back until it is completed. Tier: default/unit (`tests/test_cli.py`).
6. `test_watch_follow_mid_stream_corruption_ends_stream_with_stderr_message`: after the hello line and at least one backlog or live line, a non-JSON, newline-terminated line is appended between polls. The process exits 3, nothing further is written to stdout after the point of corruption, and stderr contains a one-line message naming the journal file and the bad line's number (the message already produced by `CorruptJournalError`). Tier: default/unit (`tests/test_cli.py`).

Tests 1 to 3 are the ones the card names. Tests 4 to 6 cover the hello-line contract, the `--all`/torn-tail behavior, and the mid-stream corruption error path, all described above.

## Verification

`uv run pytest`. There is no lint or typecheck step.
