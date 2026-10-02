# Subtask 43f4f076: Add `am watch` for a one-shot read of a run's journal

Parent story: 0adbb38b ("am watch exposes the journal as a public, documented stream"). Source design: `docs/superpowers/specs/2026-10-02-am-watch-design.md`, sections 3.6 and 3.7, with 3.1 to 3.4 as background. This subtask narrows that design to the terminating, non-`--follow` form of the command.

## Scope

Add a Typer command `am watch [RUN_ID | --all] [--since SEQ] [--pretty]` to `src/agent_manager/cli.py`. It reads one run's journal, or every run's journal, once, prints a single envelope and exits.

Out of scope, because sibling subtasks own it:
- `--follow`, bare-JSON-per-line streaming, the `{"event":"watch","schema":1,...}` hello line, the poll loop, the guarantee that a refusal comes before any streamed line, and continuity across a lease takeover or reseek. These belong to cba3e48f. Do not add a `--follow` option, not even a stub.
- README changes, including the line "`watch` and `retry` do not exist" at README.md:410, and the documentation of the JournalLine contract. These belong to b442ff58. Leave README.md untouched.

## Observable behavior

- **Success.** Exit 0. Print `{"ok": true, "data": {"events": [...]}}` through the existing `render` (one line by default, indented under `--pretty`). Each event is a `JournalLine` dumped in JSON mode, with the same field names as the journal (`seq`, `ts`, `run_id`, `event`, `story`, `card`, `phase`, `attempt`, `payload`).
- **Single run (`RUN_ID`).** Events come from `Journal._for_reading(run_id).read(ignore_torn_tail=True)`, in `seq` order. Never construct `Journal(run_id)`, because its `__init__` calls `paths.run_dir`, and that creates the run directory.
- **`--all`.** Walk every subdirectory of `paths.data_dir() / "runs"` and read each one's journal the same way. The data directory must be resolved through `paths.data_dir()` so that `XDG_DATA_HOME` is honored. No helper for this walk exists yet, so this subtask adds one (a small function in `store.py` or `paths.py`) that only lists directories and never creates any. Events are ordered by `(run_id, seq)`, which is the cursor that 3.3 defines. Two cases are skipped silently rather than refused: a run directory that has no journal file yet, and a `runs/` directory or data directory that does not exist. The reason is 3.7's rule that a watcher pointed at the wrong data directory "sees nothing, not an error". With nothing to read, the result is `{"events": []}`.
- **`--since SEQ`.** Keep only events with `seq > SEQ`. With `--all`, the filter applies to each run's own `seq`. `SEQ` must be an integer of 0 or more, and 0 means no filtering.
- **Torn tail.** A final line with no terminating newline that does not parse is dropped, not treated as an error. This is what `ignore_torn_tail=True` does.
- **Unrecognized event kinds.** These are already skipped by `Journal.read`, which was the prerequisite card 487ba681. Nothing extra is needed here.

## Error paths

All refusals follow the existing `status`/`runs`/`logs` pattern: print `render(error_envelope(error), pretty=pretty)` and raise `typer.Exit(EXIT_ERROR)`, which exits with code 3.

- **Unknown run id (no `--all`).** `read()` raises `store.MissingJournalError`. `HANDLED` (cli.py:1072) does not currently contain `JournalError` or its subclasses, so the command must catch `MissingJournalError` explicitly and re-raise it as the existing CLI-level `runs.UnknownRunError`, a `CliError`. This matches how `logs` maps a missing run, and the envelope's `type` becomes `UnknownRunError`. No directory may be created under `<data dir>/runs/`.
- **Corrupt journal.** A non-JSON line that is not the torn tail raises `store.CorruptJournalError`, whether reading one run or using `--all`. It must also produce an `ok: false` envelope with exit 3 rather than a traceback. Catch `JournalError` in the command (or add it to `HANDLED`); either approach is acceptable.
- **Invalid argument combinations.** Passing both `RUN_ID` and `--all`, or passing neither, is refused with a `CliError` envelope and exit 3. The message should name the two accepted forms.

## Tests

Tier rule in force: §14 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. Pure functions and anything without real `git`, `brd` or `claude` subprocesses go in the default (unit) suite. Only the single slow end-to-end test is marked `e2e`. The six-tier addendum (`2026-10-02-test-tier-design.md`) is only proposed and is not wired into `pyproject.toml` or `conftest.py`, so it does not apply. None of the tests below are marked `e2e`. Every test sets `XDG_DATA_HOME` to a `tmp_path` and writes `journal.jsonl` files directly. CLI tests use `CliRunner` against `app`, in the style of the existing `status`/`runs`/`logs` tests in `tests/test_cli.py`.

1. `test_watch_since_filters_to_later_seqs`: a run with seqs 1 to 4 and `--since 2` returns only seqs 3 and 4. Tier: default/unit (`tests/test_cli.py`).
2. `test_watch_all_reads_across_more_than_one_run`: two run directories, each with a journal, and `--all` returns events from both, ordered by `(run_id, seq)`. Tier: default/unit (`tests/test_cli.py`).
3. `test_watch_unknown_run_refuses_and_creates_no_run_directory`: exit 3, `ok: false`, error `type` is `UnknownRunError`, and `<data dir>/runs/<id>` does not exist afterwards. Tier: default/unit (`tests/test_cli.py`).
4. `test_watch_tolerates_a_torn_last_line`: a journal whose last line is unterminated and truncated gives exit 0 and returns every complete line. Tier: default/unit (`tests/test_cli.py`).
5. `test_watch_single_run_returns_events_envelope`: a plain `am watch RUN_ID` returns `{"ok": true, "data": {"events": [...]}}` with every line in `seq` order. Tier: default/unit (`tests/test_cli.py`).
6. `test_watch_all_with_no_runs_directory_returns_empty`: no `runs/` directory gives exit 0 and `events == []`, and the directory is still not created afterwards. Tier: default/unit (`tests/test_cli.py`).
7. `test_watch_rejects_run_id_with_all_and_neither`: both cases exit 3 with an `ok: false` envelope. Tier: default/unit (`tests/test_cli.py`).
8. A unit test for the new run-directory walking helper: it lists only directories, returns empty when the base directory is missing, and creates nothing. Tier: default/unit (`tests/test_store.py` or `tests/test_paths.py`, matching wherever the helper lives).

Tests 1 to 4 are the ones the card names. Tests 5 to 8 cover the remaining behavior described above.

## Verification

`uv run pytest`. There is no lint or typecheck step.
