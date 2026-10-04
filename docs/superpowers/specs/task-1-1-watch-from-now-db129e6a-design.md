# 1.1 `am watch --from-now` (card db129e6a)

Narrows section 4 of `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` to one subtask. Parent: story 7caeca37 "am watch --from-now", milestone fbf624d6 "am interfaces for the Omarchy plugin". This card is the story's only child.

## Scope

- `src/agent_manager/cli.py`: a new `--from-now` Typer option on the `watch` command, a `from_now` parameter threaded through `watch_for`, `_stream_watch` and `_follow_watch`, and the two new refusals.
- `tests/test_cli.py`: three new unit tests beside the existing follow tests (around lines 8237-8500).
- Out of scope: README (owned by the sibling story "Document the new surface"), the e2e_fake test that the milestone spec mentions (another story), and every other milestone story (richer `am runs`, `--detach`, `logs --follow`, `--board`).
- Additive only. The journal stays schema 1. The hello line from `_watch_hello` keeps its exact shape and `schema: 1`. The journal-line, lease and claim contracts do not change. A `watch` call without `--from-now` behaves exactly as it does today, byte for byte.

## Observable behaviour

`am watch [RUN_ID | --all] --follow --from-now`:

1. Prints the hello line, the same as any `--follow`.
2. Emits nothing from the backlog. Every run whose journal exists when the stream starts has its cursor seeded to that journal's current highest complete `seq`, and nothing is emitted while seeding. The seeding goes through the same `_journal_events` / `_poll_watch` path, so a torn last line is not counted. If that line is completed later, it is emitted. Mechanically: `_follow_watch(..., from_now=True)` runs its first (backlog) `_poll_watch` pass into the shared `cursors` dict but discards what it yields, then enters the normal sleep/poll loop. `_stream_watch` emits the hello line before this pass. A run with a journal that has no complete line yet gets no cursor entry and is read from 0.
3. Then polls as `--follow` already does (`_watch_sleep(WATCH_POLL_SECONDS)`, bounded by `WATCH_MAX_POLLS`) and emits each line appended after the start, in order, with no duplicates.
4. With `--all`, a run that has no journal at start has no seeded cursor. It is therefore read from `seq` 0 when it appears, and all of its lines are emitted. Only runs that existed at start have their backlog skipped.
5. RUN_ID handling is unchanged. An unknown RUN_ID is still `UnknownRunError`, and a bad id is refused as it is today.

Stream endings (Ctrl-C, closed pipe, a journal that becomes corrupt mid-stream going to stderr with `EXIT_ERROR`) are unchanged.

## Error paths

Both refusals are `CliError` raised from `watch_for`, next to the existing RUN_ID/`--all` and `--since` checks. That puts them before any journal is read. `watch` then turns them into `error_envelope` output (`{"ok": false, "error": {...}}`, `--pretty` honoured) with exit 3 (`EXIT_ERROR`). No hello line and no stream line are printed, and `_watch_sleep` is never called.

- `--from-now` together with `--since` is refused. The refusal triggers whenever `--since` is given on the command line, including `--since 0`. The current Typer default of `0` can't express that, so the option becomes `since: int | None = typer.Option(None, "--since", ...)`. `watch` computes `since_given = since is not None` and passes `since or 0` as the numeric `since` everywhere else, so the existing behaviour and the `--since must be 0 or more` check are unchanged. The message names both flags and says they are exclusive.
- `--from-now` without `--follow` is refused, in both one-shot RUN_ID and one-shot `--all` mode. The message says `--from-now` needs `--follow`. To make this possible, `watch_for` gains keyword-only `follow: bool = False`, `from_now: bool = False` and `since_given: bool = False` parameters (defaults keep every existing call unchanged). Check order inside `watch_for`: the RUN_ID/`--all` check, then `--since` negative, then `--from-now` with `--since`, then `--from-now` without `--follow`; so a call that breaks both new rules reports the `--since` conflict.

The `--from-now` Typer option gets a help string, for example: "With --follow, skip the backlog: print only events appended after the command starts. Exclusive with --since."

## Tests (TDD: write them first, see them fail, then implement)

All three go in `tests/test_cli.py`. They set `XDG_DATA_HOME` to tmp_path, write journals with `_write_watch_journal` / `_append_watch_journal` (`_watch_line`), drive polls through `_watch_follow` (which fakes `cli._watch_sleep` and sets `cli.WATCH_MAX_POLLS`) or through `runner.invoke` for one-shot calls, and parse with `_stream` / `_hello`. They spawn no subprocess and touch only files under tmp_path. Under the test-placement rule (CLAUDE.md "Test tiers", test-tier design V1, design spec section 14) that makes all three **unmarked unit tests**: no `git`, `brd` or `e2e_fake` marker.

1. `test_watch_follow_from_now_skips_backlog` (unit). `run-a` has backlog seqs 1-3. Run `_watch_follow(monkeypatch, "run-a", "--from-now", actions=[append 4, 5])`. Expect exit 0, a first line equal to `_hello(tmp_path)`, then only seqs 4 and 5, and empty stderr. Add an `--all` case in the same test or a sibling unit test: `run-a` has a backlog, and during a poll an existing run gets an append while a new `run-b` appears with seqs 1-2. Expect only `run-a`'s new lines plus all of `run-b`'s lines.
2. `test_watch_from_now_refused_with_since` (unit). Run `_watch_follow(monkeypatch, "run-a", "--from-now", "--since", "0")` against an existing journal. Expect `exit_code == cli.EXIT_ERROR`, stdout to be exactly one envelope with `ok` false and error type `CliError`, no `event` key, no hello line, and `sleeps == []`.
3. `test_watch_from_now_refused_without_follow` (unit). Run `runner.invoke(cli.app, ["watch", "run-a", "--from-now"])` and the same with `--all`. Expect exit 3 and one `CliError` envelope with `ok` false.

The existing watch and follow tests (lines 7929-8500) must still pass unchanged.

## Verification

`uv run pytest` (unit + git tiers). There is no typecheck or lint step.
