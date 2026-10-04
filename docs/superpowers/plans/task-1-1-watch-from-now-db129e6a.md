<!-- task-pipeline: validated -->
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

---

# `am watch --from-now` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--from-now` to `am watch` so that `am watch [RUN_ID | --all] --follow --from-now` prints the hello line and then only journal lines appended after the command started, and refuse `--from-now` with `--since` (any value, including 0) or without `--follow` via the usual exit-3 `CliError` envelope.

**Architecture:** Both refusals live in `watch_for` (the function every `watch` call already goes through before any stream line), which gains keyword-only `follow`, `from_now` and `since_given` parameters with defaults that keep every existing call unchanged. The `--since` Typer option changes its default from `0` to `None` so `watch` can tell "not given" from `--since 0`; everywhere else it passes the numeric `since` (`0` when not given). The backlog skip is a single branch in `_follow_watch`: with `from_now=True` the first `_poll_watch` pass is drained into the shared `cursors` dict without yielding, then the normal sleep/poll loop runs. Runs that appear later have no cursor and start at `since` (0), so they are emitted in full; a torn tail is never counted because `_journal_events` already skips it.

**Tech Stack:** Python 3.12, Typer (`typer.testing.CliRunner`, separate `stdout`/`stderr`), Pydantic `JournalLine`, pytest.

**Spec:** `docs/superpowers/specs/task-1-1-watch-from-now-db129e6a-design.md` (prepended verbatim above). Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` section 4.

**Branch / worktree:** `ami/task-1-1-watch-from-now-db129e6a` at `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-1-1-watch-from-now-db129e6a`, cut fresh from `origin/master`. Everything this plan touches already exists on master: `watch_for` (`src/agent_manager/cli.py:1638-1672`), `_poll_watch` (`:1713-1735`), `_follow_watch` (`:1738-1758`), `_stream_watch` (`:1777-1800`), the `watch` command (`:1803-1838`), and the test helpers `_write_watch_journal`, `_watch`, `_watch_line`, `_append_watch_journal`, `_watch_follow`, `_stream`, `_hello` (`tests/test_cli.py:7937-8234`). No other subtask's code is assumed.

## Global Constraints

- Additive only. Journal stays schema 1; `_watch_hello()` (`cli.py:1695-1703`) is not touched: `{"event": "watch", "schema": 1, "am": __version__, "runs_dir": ...}`.
- Journal-line, lease and claim contracts do not change.
- A `watch` call without `--from-now` behaves exactly as today, byte for byte (including `--since 0`, `--since -1`, and the default).
- Refusals are `CliError` raised from `watch_for`, before any journal read; output is `render(error_envelope(error), pretty=pretty)`, exit `EXIT_ERROR` (3), no hello line, `_watch_sleep` never called.
- Check order inside `watch_for`: RUN_ID/`--all`, then negative `--since`, then `--from-now` with `--since`, then `--from-now` without `--follow`.
- `--from-now` help string: "With --follow, skip the backlog: print only events appended after the command starts. Exclusive with --since."
- All new tests are unmarked unit tests in `tests/test_cli.py` (tmp_path journals + fake sleep, no subprocess). No `git`, `brd`, `e2e_fake` markers.
- Do not touch `README.md` (owned by the sibling story "Document the new surface").
- Verification: `uv run pytest`. No lint, no typecheck.

## Review Focus

- A torn last line at start under `--from-now`: a person expects it to be emitted once it is completed, not swallowed as backlog. Pinned in Task 2 (`test_watch_follow_from_now_emits_a_torn_tail_once_complete`).
- A run whose journal at start holds only a torn first line (no complete line): a person expects its seq 1 to be emitted once complete, because it has no seeded cursor. Pinned in Task 2 (same test, `run-d`).
- `--from-now` with an unknown or path-like RUN_ID: a person expects the same `UnknownRunError` envelope as plain `--follow`, with no hello line. Pinned in Task 1 (`test_watch_from_now_keeps_existing_refusals_and_since_zero`).
- `--since 0` / `--since -1` without `--from-now` after the default becomes `None`: a person expects today's behaviour (all events; the "must be 0 or more" refusal). Pinned in Task 1 (same test).
- A call breaking both new rules (`--from-now --since 2`, no `--follow`): a person expects one deterministic message, the `--since` conflict. Pinned in Task 1 (`test_watch_from_now_refused_with_since`).

---

### Task 1: `--from-now` option and its two refusals

**Files:**
- Modify: `src/agent_manager/cli.py:1638-1672` (`watch_for`: new keyword-only parameters and two checks)
- Modify: `src/agent_manager/cli.py:1803-1838` (`watch`: `--since` default `None`, new `--from-now` option, pass the new arguments)
- Test: `tests/test_cli.py` (append after the last test in the file, `test_watch_follow_silence_stdout_leaves_a_descriptorless_stdout_alone`, which ends at line 8502)

**Interfaces:**
- Consumes (on master): `_watch_follow(monkeypatch, *args: str, actions=()) -> tuple[Result, list[float]]`, `_watch(*args: str) -> Result`, `_write_watch_journal(tmp_path, run_id, seqs, *, tail="") -> list[dict]`, `_watch_runs_dir(tmp_path) -> Path`, `cli.EXIT_ERROR`, `CliError`.
- Produces:
  - `watch_for(run_id: str | None, *, all_runs: bool = False, since: int = 0, follow: bool = False, from_now: bool = False, since_given: bool = False) -> dict[str, Any]`
  - `watch` command with `since: int | None` (default `None`) and `from_now: bool` (`--from-now`, default `False`). In this task `watch` calls `_stream_watch(run_id, since=since_value)` unchanged; Task 2 adds `from_now=` to that call.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
# ── am watch --from-now (card db129e6a) ────────────────────────────────────
#
# Default (unit) tier per design §14, like the follow tests above: journals in
# tmp_path, polling driven by the fake `cli._watch_sleep`, no subprocess.


def _assert_one_cli_error(result, *needles: str) -> dict[str, Any]:
    """The refusal shape: exit 3, exactly one envelope line, ok false, CliError."""
    assert result.exit_code == cli.EXIT_ERROR, result.output
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert envelope["ok"] is False
    assert "event" not in envelope
    assert envelope["error"]["type"] == "CliError"
    for needle in needles:
        assert needle in envelope["error"]["message"], envelope
    return envelope


def test_watch_from_now_refused_with_since(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1, 2])

    for argv in (
        ["run-a", "--from-now", "--since", "0"],
        ["run-a", "--from-now", "--since", "2"],
        ["--all", "--from-now", "--since", "0"],
    ):
        result, sleeps = _watch_follow(monkeypatch, *argv)
        assert sleeps == [], argv
        _assert_one_cli_error(result, "--from-now", "--since", "exclusive")

    # `--pretty` still indents the refusal, and it is still the only output.
    pretty, sleeps = _watch_follow(
        monkeypatch, "run-a", "--from-now", "--since", "0", "--pretty"
    )
    assert pretty.exit_code == cli.EXIT_ERROR, pretty.output
    assert sleeps == []
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout)["error"]["type"] == "CliError"

    # Breaking both new rules at once reports the --since conflict.
    both = _watch("run-a", "--from-now", "--since", "2")
    _assert_one_cli_error(both, "--from-now", "--since", "exclusive")


def test_watch_from_now_refused_without_follow(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1, 2])

    for argv in (["run-a", "--from-now"], ["--all", "--from-now"]):
        result = runner.invoke(cli.app, ["watch", *argv])
        _assert_one_cli_error(result, "--from-now", "--follow")


def test_watch_from_now_keeps_existing_refusals_and_since_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(tmp_path, "run-a", [1, 2])

    # RUN_ID handling is unchanged under --from-now: no hello line, no polls.
    for argv, kind in (
        (["no-such-run", "--from-now"], "UnknownRunError"),
        (["../escape", "--from-now"], "UnknownRunError"),
        (["run-a", "--all", "--from-now"], "CliError"),
    ):
        refused, sleeps = _watch_follow(monkeypatch, *argv)
        assert refused.exit_code == cli.EXIT_ERROR, (argv, refused.output)
        assert sleeps == [], argv
        refusal_lines = refused.stdout.splitlines()
        assert len(refusal_lines) == 1, (argv, refused.stdout)
        refusal = json.loads(refusal_lines[0])
        assert refusal["ok"] is False, argv
        assert refusal["error"]["type"] == kind, argv
    assert not (_watch_runs_dir(tmp_path) / "no-such-run").exists()

    # Without --from-now, `--since 0` is still the default and `--since -1`
    # is still refused by the old check.
    zero = _watch("run-a", "--since", "0")
    assert zero.exit_code == 0, zero.output
    assert json.loads(zero.stdout) == {"ok": True, "data": {"events": written}}
    negative = _watch("run-a", "--since", "-1")
    _assert_one_cli_error(negative, "--since must be 0 or more")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "from_now" -v`
Expected: `test_watch_from_now_refused_with_since`, `test_watch_from_now_refused_without_follow` and `test_watch_from_now_keeps_existing_refusals_and_since_zero` FAIL on `assert result.exit_code == cli.EXIT_ERROR` with exit code 2 (Typer usage error "No such option: --from-now").

- [ ] **Step 3: Add the refusals to `watch_for`**

In `src/agent_manager/cli.py`, replace the signature, docstring and the `--since` check of `watch_for` (lines 1638-1656) with:

```python
def watch_for(
    run_id: str | None,
    *,
    all_runs: bool = False,
    since: int = 0,
    follow: bool = False,
    from_now: bool = False,
    since_given: bool = False,
) -> dict[str, Any]:
    """The payload of `am watch`: `{"events": [...]}`.

    Exactly one of `run_id` and `all_runs`. With `run_id`, a run with no
    journal is `UnknownRunError`. With `all_runs`, every directory under
    `<data dir>/runs/` is read, a run with no journal yet is skipped, and a
    missing `runs/` is no events: a watcher pointed at the wrong data
    directory sees nothing, not an error (am-watch design 3.7). Events are
    ordered by `(run_id, seq)`; `since` filters each run's own `seq`.

    `from_now` (`--from-now`) is refused with `since_given` (any `--since`
    on the command line, 0 included) and without `follow`. Both refusals
    come before any journal is read, so `watch` prints them as the usual
    exit-3 envelope with no stream line.
    """
    if all_runs == (run_id is not None):
        raise CliError(
            "give exactly one of RUN_ID or --all:"
            " `am watch RUN_ID` reads one run, `am watch --all` reads every run"
        )
    if since < 0:
        raise CliError(f"--since must be 0 or more, got {since}")
    if from_now and since_given:
        raise CliError(
            "--from-now and --since are exclusive: --from-now skips the whole"
            " backlog, --since picks where in it to start; give one of them"
        )
    if from_now and not follow:
        raise CliError(
            "--from-now needs --follow: it skips the backlog of a stream,"
            " and without --follow there is only the backlog"
        )
```

The rest of `watch_for` (from `if run_id is not None:` at line 1657 to the end at line 1672) is unchanged.

- [ ] **Step 4: Add the `--from-now` option and the `since_given` plumbing to `watch`**

In `src/agent_manager/cli.py`, replace the whole `watch` command (lines 1803-1838) with:

```python
@app.command("watch")
def watch(
    run_id: str | None = typer.Argument(
        None,
        metavar="[RUN_ID]",
        help="The run whose journal is read. Omit it and pass --all for every run.",
    ),
    all_runs: bool = typer.Option(
        False, "--all", help="Read every run's journal under the data directory."
    ),
    since: int | None = typer.Option(
        None,
        "--since",
        metavar="SEQ",
        help="Only events whose seq is greater than SEQ (default 0).",
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        help="Keep printing events, one JSON object per line, until interrupted.",
    ),
    from_now: bool = typer.Option(
        False,
        "--from-now",
        help=(
            "With --follow, skip the backlog: print only events appended after"
            " the command starts. Exclusive with --since."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print a run's journal events, or every run's, once as one envelope.

    With --follow, print a hello line and then each event as its own line of
    JSON, the backlog first and then new ones as they are appended, until
    interrupted. With --follow --from-now, the backlog is skipped and only
    events appended after the start are printed. A refusal is still one
    envelope at exit 3, printed before any stream line.
    """
    # `None` means --since was not given, which --from-now must tell apart
    # from an explicit `--since 0`; every other use wants the number.
    since_given = since is not None
    since_value = since if since is not None else 0
    try:
        payload = watch_for(
            run_id,
            all_runs=all_runs,
            since=since_value,
            follow=follow,
            from_now=from_now,
            since_given=since_given,
        )
    except WATCH_HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    if follow:
        _stream_watch(run_id, since=since_value)
        return
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "from_now" -v`
Expected: all three PASS.

- [ ] **Step 6: Run every existing watch test to verify nothing changed**

Run: `uv run pytest tests/test_cli.py -k "watch" -v`
Expected: PASS, including `test_watch_since_filters_to_later_seqs`, `test_watch_refuses_a_negative_since`, `test_watch_follow_hello_line_shape` and `test_watch_follow_refusal_prints_envelope_and_no_stream`.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(watch): add --from-now option, refused with --since or without --follow"
```

---

### Task 2: `--follow --from-now` skips the backlog

**Files:**
- Modify: `src/agent_manager/cli.py:1738-1758` (`_follow_watch`: `from_now` parameter, drained first pass)
- Modify: `src/agent_manager/cli.py:1777-1800` (`_stream_watch`: `from_now` parameter, passed to `_follow_watch`)
- Modify: `src/agent_manager/cli.py` `watch` body (the `_stream_watch(run_id, since=since_value)` line written in Task 1)
- Test: `tests/test_cli.py` (append after `test_watch_from_now_keeps_existing_refusals_and_since_zero` from Task 1)

**Interfaces:**
- Consumes: `watch` with `--from-now` accepted (Task 1); `_poll_watch(run_id: str | None, *, since: int, cursors: dict[str, int]) -> Iterator[dict[str, Any]]` (on master, unchanged); test helpers `_watch_follow`, `_stream`, `_hello`, `_write_watch_journal`, `_append_watch_journal`, `_watch_line`, `_watch_runs_dir`.
- Produces:
  - `_follow_watch(run_id: str | None, *, since: int, sleep: Callable[[float], None], max_polls: int | None, from_now: bool = False) -> Iterator[dict[str, Any]]`
  - `_stream_watch(run_id: str | None, *, since: int, from_now: bool = False) -> None`

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
def test_watch_follow_from_now_skips_backlog(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1, 2, 3])

    # Nothing appended: the hello line alone.
    idle, idle_sleeps = _watch_follow(
        monkeypatch, "run-a", "--from-now", actions=[lambda: None]
    )
    assert idle.exit_code == 0, idle.output
    assert idle_sleeps == [cli.WATCH_POLL_SECONDS]
    assert _stream(idle) == [_hello(tmp_path)]

    appended: list[dict[str, Any]] = []

    def append_fourth() -> None:
        appended.extend(_append_watch_journal(tmp_path, "run-a", [4]))

    def append_fifth() -> None:
        appended.extend(_append_watch_journal(tmp_path, "run-a", [5]))

    # The last poll sees nothing new, so seq 5 must not repeat.
    result, sleeps = _watch_follow(
        monkeypatch,
        "run-a",
        "--from-now",
        actions=[append_fourth, append_fifth, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert sleeps == [cli.WATCH_POLL_SECONDS] * 3
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    assert lines[1:] == appended
    assert [line["seq"] for line in lines[1:]] == [4, 5]
    assert result.stderr == ""


def test_watch_follow_all_from_now_skips_only_runs_present_at_start(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1, 2])
    _write_watch_journal(tmp_path, "run-c", [1])  # present at start, never appended

    def first_poll() -> None:
        _append_watch_journal(tmp_path, "run-a", [3])
        _write_watch_journal(tmp_path, "run-b", [1, 2])  # appears after the start

    def second_poll() -> None:
        _append_watch_journal(tmp_path, "run-a", [4])
        _append_watch_journal(tmp_path, "run-b", [3])

    result, sleeps = _watch_follow(
        monkeypatch, "--all", "--from-now", actions=[first_poll, second_poll]
    )

    assert result.exit_code == 0, result.output
    assert len(sleeps) == 2
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    # Runs are read in sorted order on each poll: run-a, then run-b.
    assert lines[1:] == [
        _watch_line("run-a", 3),
        _watch_line("run-b", 1),
        _watch_line("run-b", 2),
        _watch_line("run-a", 4),
        _watch_line("run-b", 3),
    ]
    assert result.stderr == ""


def test_watch_follow_from_now_emits_a_torn_tail_once_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    third_a = json.dumps(_watch_line("run-a", 3), sort_keys=True)
    first_d = json.dumps(_watch_line("run-d", 1), sort_keys=True)
    # run-a: seqs 1-2 complete, seq 3 still being written at start.
    _write_watch_journal(tmp_path, "run-a", [1, 2], tail=third_a[:20])
    # run-d: only a torn first line at start, so it gets no seeded cursor.
    _write_watch_journal(tmp_path, "run-d", [], tail=first_d[:20])

    def finish_the_torn_lines() -> None:
        for run_id, text in (("run-a", third_a), ("run-d", first_d)):
            journal = _watch_runs_dir(tmp_path) / run_id / store_module.JOURNAL_NAME
            with journal.open("a", encoding="utf-8") as handle:
                handle.write(text[20:] + "\n")

    result, _ = _watch_follow(
        monkeypatch,
        "--all",
        "--from-now",
        actions=[lambda: None, finish_the_torn_lines, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _hello(tmp_path),
        _watch_line("run-a", 3),
        _watch_line("run-d", 1),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "follow_from_now or follow_all_from_now" -v`
Expected: all three FAIL on the `lines[1:] == ...` / `_stream(...) == ...` assertions, because the backlog (run-a seqs 1-3, run-c seq 1, run-a seqs 1-2) is still printed after the hello line.

- [ ] **Step 3: Drain the backlog pass in `_follow_watch` when `from_now`**

In `src/agent_manager/cli.py`, replace `_follow_watch` (lines 1738-1758) with:

```python
def _follow_watch(
    run_id: str | None,
    *,
    since: int,
    sleep: Callable[[float], None],
    max_polls: int | None,
    from_now: bool = False,
) -> Iterator[dict[str, Any]]:
    """The backlog above `since`, then every line appended after it.

    One pass at once for the backlog, then `sleep(WATCH_POLL_SECONDS)` and
    another pass, `max_polls` times or forever when it is `None`. One cursor
    dict spans every pass, so no `seq` of a run is emitted twice and none is
    skipped, however its lines are spread across polls.

    With `from_now` the backlog pass still runs, so it seeds each existing
    run's cursor to its highest complete `seq`, but nothing it reads is
    yielded. A torn last line is not read, so it is emitted once complete;
    a run with no complete line, or none at all yet, has no cursor and is
    emitted in full from `since` when its lines appear.
    """
    cursors: dict[str, int] = {}
    backlog = _poll_watch(run_id, since=since, cursors=cursors)
    if from_now:
        for _ in backlog:
            pass
    else:
        yield from backlog
    polls = 0
    while max_polls is None or polls < max_polls:
        sleep(WATCH_POLL_SECONDS)
        polls += 1
        yield from _poll_watch(run_id, since=since, cursors=cursors)
```

- [ ] **Step 4: Thread `from_now` through `_stream_watch`**

In `src/agent_manager/cli.py`, replace the `_stream_watch` signature line and its `_follow_watch` call (lines 1777 and 1789-1791). The signature becomes:

```python
def _stream_watch(run_id: str | None, *, since: int, from_now: bool = False) -> None:
```

and the call inside the `try` becomes:

```python
        for event in _follow_watch(
            run_id,
            since=since,
            sleep=_watch_sleep,
            max_polls=WATCH_MAX_POLLS,
            from_now=from_now,
        ):
            _emit_stream_line(event)
```

Everything else in `_stream_watch` (docstring, hello line first, the `KeyboardInterrupt` / `BrokenPipeError` / `WATCH_HANDLED` handlers) is unchanged, so the hello line is still emitted before the seeding pass and a journal that turns corrupt during seeding still goes to stderr with exit 3.

- [ ] **Step 5: Pass `from_now` from `watch` to `_stream_watch`**

In the `watch` command body written in Task 1, Step 4, change:

```python
    if follow:
        _stream_watch(run_id, since=since_value)
        return
```

to:

```python
    if follow:
        _stream_watch(run_id, since=since_value, from_now=from_now)
        return
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "from_now" -v`
Expected: all six from-now tests (three from Task 1, three from this task) PASS.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with every existing watch and follow test (`tests/test_cli.py` lines 7929-8502) unchanged and green.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(watch): --follow --from-now skips each existing run's backlog"
```
