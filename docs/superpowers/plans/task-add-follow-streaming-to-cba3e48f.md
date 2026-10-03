<!-- task-pipeline: validated -->
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

---

# `am watch --follow` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--follow` to the existing `am watch` command. It streams a hello line and then bare, compact, flushed JournalLine objects (the backlog, then live appends) until it is interrupted, and it refuses with the normal `ok:false` envelope before writing any stream line.

**Architecture:** Every refusal check reuses the sibling's `watch_for(...)` unchanged. If that call raises, the envelope is printed exactly as the non-follow form prints it. If it succeeds, its payload is thrown away and the stream begins. The stream is built from small pieces in `src/agent_manager/cli.py`. `_poll_watch` does one pass over the watched runs, keeping a cursor dict keyed by run directory name (`run_id -> last emitted seq`) and reading through the existing `_journal_events` (so `Journal._for_reading` and `ignore_torn_tail=True` are used). `_follow_watch` is a generator: one immediate pass for the backlog, then sleep plus another pass, up to an optional poll bound. `_stream_watch` writes the hello line and each event through `_emit_stream_line`, and it turns Ctrl-C, a closed pipe, or mid-stream corruption into the exits the spec requires. Tests control polling by monkeypatching two module-level knobs, `cli._watch_sleep` (the sleep callable) and `cli.WATCH_MAX_POLLS` (the stop bound; `None` in production). The backlog is read a second time by the first `_poll_watch` pass instead of being taken from `watch_for`'s payload. That way the cursor is keyed by the run's directory name, which is the same key every later poll uses. If a journal turns corrupt in the microseconds between validation and that pass, the mid-stream rule handles it (stderr, exit 3).

**Tech Stack:** Python 3.12, Typer 0.27 (`typer.testing.CliRunner`, whose `Result` exposes separate `stdout` and `stderr`), Pydantic `JournalLine`, pytest.

**Spec:** `docs/superpowers/specs/task-add-follow-streaming-to-cba3e48f-design.md` (prepended verbatim above). Source design: `docs/superpowers/specs/2026-10-02-am-watch-design.md` §3.2, §3.3, §3.6, §3.7.

**Branch / worktree:** `m16/task-add-follow-streaming-to-cba3e48f` at `/home/paulomtts/Code/agent-manager/.claude/worktrees/m16/task-add-follow-streaming-to-cba3e48f`. It was cut from `m16/task-add-am-watch-for-a-one-43f4f076`, so `watch_for`, `_check_watch_run_id`, `_journal_events`, `WATCH_HANDLED`, the `watch` command (`src/agent_manager/cli.py:1513-1607`) and the watch test helpers (`tests/test_cli.py:7658-7896`) already exist on it. Nothing from any other subtask is assumed.

## Global Constraints

- Hello line, exactly: `{"event": "watch", "schema": 1, "am": agent_manager.__version__, "runs_dir": str(paths.data_dir() / "runs")}`. It is the first stdout line and the only non-JournalLine line.
- A refusal prints `render(error_envelope(...))`, exits `EXIT_ERROR` (3), and writes no stream line before it.
- Journals are opened only through `Journal._for_reading(run_id)` (via `_journal_events`), never `Journal(run_id)`. The data dir comes only from `paths.data_dir()`.
- The cursor is per run, keyed `(run_id, seq)`, never time, byte offset or line count.
- Poll interval is about 250ms (`WATCH_POLL_SECONDS = 0.25`). No new dependency, no inotify, no heartbeat or timing field in output, no synthetic event kinds.
- Stream lines are compact (`render(obj)` with `pretty=False`), newline-terminated, and flushed per line. `--pretty` affects only a refusal envelope.
- Ctrl-C or a closed stdout pipe means exit 0, no traceback, nothing on stderr.
- Mid-stream corruption means exit 3, a one-line message on stderr naming the file and line, and nothing more on stdout.
- Do not touch README.md (b442ff58 owns it). Do not change non-follow output or its tests (43f4f076 owns them).
- Tests go in the default/unit tier, `tests/test_cli.py`, with no `e2e` marker, following the sibling's `XDG_DATA_HOME` + direct `journal.jsonl` writes pattern.
- Verification: `uv run pytest`. There is no lint or typecheck.

## Review Focus

- Ctrl-C mid-stream (`KeyboardInterrupt` out of the sleep): a person expects a quiet exit 0, not Typer's `Aborted!` with exit 1. The test is pinned in Task 3 (`test_watch_follow_ctrl_c_exits_zero_quietly`).
- Consumer closes the pipe (`am watch --follow | head -1`): a person expects exit 0 and no `BrokenPipeError` traceback. The test is pinned in Task 3 (`test_watch_follow_closed_pipe_exits_zero_quietly`).
- A followed RUN_ID journal disappears and is later recreated: a person expects the stream to keep running, with no error and no repeat of seqs already emitted. The test is pinned in Task 2 (`test_watch_follow_tolerates_a_journal_that_disappears`).
- `--follow --pretty`: a person expects the stream to stay one object per line, so `while read line` still works, while a refusal is still indented. Both are pinned in Task 1 (`test_watch_follow_hello_line_shape`, `test_watch_follow_refusal_prints_envelope_and_no_stream`).
- Every other refusal reached with `--follow` (path-like run id, negative `--since`, RUN_ID together with `--all`, neither of them, a corrupt journal at start): a person expects an envelope and no hello line, never a half-started stream. Pinned in Task 1 (`test_watch_follow_refusal_prints_envelope_and_no_stream`, its loop over refusal argvs).

---

### Task 1: `--follow` option, refusal before the stream, hello line, backlog

**Files:**
- Modify: `src/agent_manager/cli.py:20-28` (imports), `src/agent_manager/cli.py:32-45` (add `__version__` import after this block), `src/agent_manager/cli.py:1549-1607` (new helpers between `watch_for` and the command; new option and branch in `watch`)
- Modify: `tests/test_cli.py:30-34` (add `import agent_manager`)
- Test: `tests/test_cli.py` (append after the last watch test, `test_watch_rejects_run_id_with_all_and_neither`, which ends the file at line 7896)

**Interfaces:**
- Consumes (already on branch): `watch_for(run_id: str | None, *, all_runs: bool = False, since: int = 0) -> dict[str, Any]`, `_journal_events(run_id: str, *, since: int) -> list[dict[str, Any]]` (raises `store_module.MissingJournalError`), `WATCH_HANDLED`, `render`, `error_envelope`, `EXIT_ERROR`, `paths.data_dir()`, `paths.list_run_ids()`.
- Produces:
  - `WATCH_POLL_SECONDS: float = 0.25`
  - `WATCH_MAX_POLLS: int | None = None` (polls after the backlog; `None` means poll until interrupted)
  - `_watch_sleep(seconds: float) -> None`
  - `_watch_hello() -> dict[str, Any]`
  - `_emit_stream_line(obj: Mapping[str, Any]) -> None`
  - `_poll_watch(run_id: str | None, *, since: int, cursors: dict[str, int]) -> Iterator[dict[str, Any]]`
  - `_stream_watch(run_id: str | None, *, since: int) -> None`
  - Test helpers in `tests/test_cli.py`: `_watch_line(run_id: str, seq: int) -> dict[str, Any]`, `_append_watch_journal(tmp_path: Path, run_id: str, seqs: list[int], *, tail: str = "") -> list[dict[str, Any]]`, `_watch_follow(monkeypatch, *args: str, actions=()) -> tuple[Result, list[float]]`, `_stream(result) -> list[dict[str, Any]]`, `_hello(tmp_path: Path) -> dict[str, Any]`.

- [ ] **Step 1: Add `import agent_manager` to the test module**

In `tests/test_cli.py`, change lines 30-34 from:

```python
import pytest
import typer
from typer.testing import CliRunner

from agent_manager import (
```

to:

```python
import pytest
import typer
from typer.testing import CliRunner

import agent_manager
from agent_manager import (
```

- [ ] **Step 2: Write the helpers and the two failing tests**

Append to the end of `tests/test_cli.py`:

```python
# ── am watch --follow (card cba3e48f) ──────────────────────────────────────
#
# Default (unit) tier per design §14, like the one-shot tests above: no git,
# no brd, no harness. Polling is driven by replacing `cli._watch_sleep` and
# bounding `cli.WATCH_MAX_POLLS`, so each poll sees exactly what the test wrote
# before it, with no wall-clock wait and no signal.


def _watch_line(run_id: str, seq: int) -> dict[str, Any]:
    """One journal line in the shape `_write_watch_journal` writes, JSON-mode."""
    return store_module.JournalLine(
        seq=seq,
        ts=WATCH_TS,
        run_id=run_id,
        event="phase_upsert",
        card="card-1",
        phase="implement",
        attempt=1,
        payload={"status": "started", "n": seq},
    ).model_dump(mode="json")


def _append_watch_journal(
    tmp_path: Path, run_id: str, seqs: list[int], *, tail: str = ""
) -> list[dict[str, Any]]:
    """Append one line per seq to `run_id`'s journal, then `tail` verbatim."""
    run_dir = _watch_runs_dir(tmp_path) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dumped = [_watch_line(run_id, seq) for seq in seqs]
    text = "".join(json.dumps(line, sort_keys=True) + "\n" for line in dumped)
    with (run_dir / store_module.JOURNAL_NAME).open("a", encoding="utf-8") as handle:
        handle.write(text + tail)
    return dumped


def _watch_follow(monkeypatch, *args: str, actions=()):
    """Run `am watch ARGS --follow` for exactly `len(actions)` polls after the backlog.

    Sleep `i` runs `actions[i]` (an append, a delete, a takeover) before poll
    `i` reads, so every poll sees a known state. Returns the result and the
    seconds each sleep was asked for.
    """
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        actions[len(sleeps) - 1]()

    monkeypatch.setattr(cli, "_watch_sleep", fake_sleep)
    monkeypatch.setattr(cli, "WATCH_MAX_POLLS", len(actions))
    return runner.invoke(cli.app, ["watch", *args, "--follow"]), sleeps


def _stream(result) -> list[dict[str, Any]]:
    """Every stdout line of a follow run, parsed; each must be one JSON object."""
    return [json.loads(line) for line in result.stdout.splitlines()]


def _hello(tmp_path: Path) -> dict[str, Any]:
    return {
        "event": "watch",
        "schema": 1,
        "am": agent_manager.__version__,
        "runs_dir": str(_watch_runs_dir(tmp_path)),
    }


def test_watch_follow_hello_line_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(tmp_path, "run-a", [1, 2, 3, 4])

    result, sleeps = _watch_follow(monkeypatch, "run-a", "--since", "2")

    assert result.exit_code == 0, result.output
    assert sleeps == []
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    assert lines[1:] == written[2:]
    assert [line["seq"] for line in lines[1:]] == [3, 4]
    # Bare JournalLines: no envelope, and compact, one object per line.
    assert all("ok" not in line for line in lines)
    assert result.stdout.endswith("\n")
    assert all(": " not in text for text in result.stdout.splitlines())
    assert result.stderr == ""

    # `--pretty` only shapes a refusal: the stream is byte-for-byte the same.
    pretty, _ = _watch_follow(monkeypatch, "run-a", "--since", "2", "--pretty")
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == result.stdout


def test_watch_follow_refusal_prints_envelope_and_no_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result, sleeps = _watch_follow(monkeypatch, "no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert sleeps == []
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]
    assert "event" not in envelope
    assert not (_watch_runs_dir(tmp_path) / "no-such-run").exists()
    assert not _watch_runs_dir(tmp_path).exists()

    # `--pretty` still indents a refusal, and it is still the only output.
    pretty, _ = _watch_follow(monkeypatch, "no-such-run", "--pretty")
    assert pretty.exit_code == cli.EXIT_ERROR, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == envelope

    # Every other refusal the one-shot form makes is made before the stream too.
    _write_watch_journal(tmp_path, "run-a", [1])
    _write_watch_journal(tmp_path, "run-c", [1], tail="not json\n")
    for argv, kind in (
        (["../escape"], "UnknownRunError"),
        (["run-a", "--since", "-1"], "CliError"),
        (["--all", "run-a"], "CliError"),
        ([], "CliError"),
        (["run-c"], "CorruptJournalError"),
    ):
        refused, sleeps = _watch_follow(monkeypatch, *argv)
        assert refused.exit_code == cli.EXIT_ERROR, (argv, refused.output)
        assert sleeps == [], argv
        refusal_lines = refused.stdout.splitlines()
        assert len(refusal_lines) == 1, (argv, refused.stdout)
        refusal = json.loads(refusal_lines[0])
        assert refusal["ok"] is False, argv
        assert refusal["error"]["type"] == kind, argv
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "watch_follow_hello_line_shape or watch_follow_refusal_prints_envelope" -v`
Expected: both FAIL at `monkeypatch.setattr(cli, "_watch_sleep", ...)` with `AttributeError: <module 'agent_manager.cli' ...> has no attribute '_watch_sleep'`.

- [ ] **Step 4: Add the imports to `cli.py`**

In `src/agent_manager/cli.py`, change lines 20-22 from:

```python
import asyncio
import json
import sqlite3
```

to:

```python
import asyncio
import json
import os
import sqlite3
import sys
import time
```

Then directly after the `from agent_manager import (` ... `)` block (the one ending with `store as store_module,\n)`), add:

```python
from agent_manager import __version__
```

(`os` is used in Task 3. It is added here so the import block changes only once.)

- [ ] **Step 5: Add the stream helpers between `watch_for` and `@app.command("watch")`**

In `src/agent_manager/cli.py`, insert after the end of `watch_for` (the line `    return {"events": events}`) and before `@app.command("watch")`:

```python
WATCH_POLL_SECONDS = 0.25
"""How long `am watch --follow` sleeps between polls (am-watch design 3.2).

Internal: no output line carries it, so a later move to inotify changes no
byte of the stream.
"""

WATCH_MAX_POLLS: int | None = None
"""How many polls follow the backlog before the stream ends by itself.

`None` in production: poll until Ctrl-C or a closed pipe. Tests bound it so a
`CliRunner` invocation returns.
"""


def _watch_sleep(seconds: float) -> None:
    """The pause between polls. A module attribute so tests can replace it."""
    time.sleep(seconds)


def _watch_hello() -> dict[str, Any]:
    """The first line of `am watch --follow`, and the only one that is not a
    JournalLine: where a future schema bump is announced (design 3.6)."""
    return {
        "event": "watch",
        "schema": 1,
        "am": __version__,
        "runs_dir": str(paths.data_dir() / "runs"),
    }


def _emit_stream_line(obj: Mapping[str, Any]) -> None:
    """One compact JSON object and a newline on stdout, flushed at once, so a
    consumer reading a pipe gets each line as it is written."""
    sys.stdout.write(render(obj) + "\n")
    sys.stdout.flush()


def _poll_watch(
    run_id: str | None, *, since: int, cursors: dict[str, int]
) -> Iterator[dict[str, Any]]:
    """One pass over the watched runs: each line above its run's cursor.

    `cursors` maps a run directory name to the highest `seq` already emitted
    for it, so the cursor is `(run_id, seq)` and nothing else (design 3.3). A
    lease takeover appends to the same file at a higher `seq` and needs no
    case of its own. A run not yet in `cursors` starts at `since`. With
    `--all` the runs are listed again on every pass, so a run that appears
    later is picked up. A run with no journal, now or any more, has nothing
    to emit. A torn last line is skipped by `_journal_events` and emitted on a
    later pass once it is complete.
    """
    run_ids = [run_id] if run_id is not None else paths.list_run_ids()
    for each in run_ids:
        try:
            events = _journal_events(each, since=cursors.get(each, since))
        except store_module.MissingJournalError:
            continue
        for event in events:
            cursors[each] = event["seq"]
            yield event


def _stream_watch(run_id: str | None, *, since: int) -> None:
    """The body of `am watch --follow`, once `watch_for` has accepted the call."""
    _emit_stream_line(_watch_hello())
    cursors: dict[str, int] = {}
    for event in _poll_watch(run_id, since=since, cursors=cursors):
        _emit_stream_line(event)
```

- [ ] **Step 6: Add the `--follow` option and branch to the command**

In `src/agent_manager/cli.py`, replace the `watch` command (from `@app.command("watch")` through `    typer.echo(render(ok_envelope(payload), pretty=pretty))`) with:

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
    since: int = typer.Option(
        0, "--since", metavar="SEQ", help="Only events whose seq is greater than SEQ."
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        help="Keep printing events, one JSON object per line, until interrupted.",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print a run's journal events, or every run's, once as one envelope.

    With --follow, print a hello line and then each event as its own line of
    JSON, the backlog first and then new ones as they are appended, until
    interrupted. A refusal is still one envelope at exit 3, printed before
    any stream line.
    """
    try:
        payload = watch_for(run_id, all_runs=all_runs, since=since)
    except WATCH_HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    if follow:
        _stream_watch(run_id, since=since)
        return
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "watch" -v`
Expected: PASS for the two new tests and for every existing one-shot `test_watch_*` test (the non-follow form is unchanged).

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Add am watch --follow: refusal before the stream, hello line, backlog"
```

---

### Task 2: Live polling with a per-run `(run_id, seq)` cursor

**Files:**
- Modify: `src/agent_manager/cli.py` (`_stream_watch` from Task 1; add `_follow_watch` right above it)
- Test: `tests/test_cli.py` (append after the Task 1 tests)

**Interfaces:**
- Consumes: `_poll_watch`, `_emit_stream_line`, `_watch_hello`, `_watch_sleep`, `WATCH_POLL_SECONDS`, `WATCH_MAX_POLLS` (Task 1). Test helpers `_watch_follow`, `_stream`, `_hello`, `_watch_line`, `_append_watch_journal`, `_write_watch_journal`, `_watch_runs_dir` (Task 1 and sibling).
- Produces: `_follow_watch(run_id: str | None, *, since: int, sleep: Callable[[float], None], max_polls: int | None) -> Iterator[dict[str, Any]]`. `_stream_watch` keeps its signature `(run_id: str | None, *, since: int) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_watch_follow_observes_a_line_appended_after_start(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    backlog = _write_watch_journal(tmp_path, "run-a", [1, 2])
    appended: list[dict[str, Any]] = []

    def append_third() -> None:
        appended.extend(_append_watch_journal(tmp_path, "run-a", [3]))

    # Poll 1 sees seq 3; poll 2 sees nothing new, so seq 3 must not repeat.
    result, sleeps = _watch_follow(
        monkeypatch, "run-a", actions=[append_third, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert cli.WATCH_POLL_SECONDS == 0.25
    assert sleeps == [cli.WATCH_POLL_SECONDS, cli.WATCH_POLL_SECONDS]
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    assert lines[1:] == backlog + appended
    assert [line["seq"] for line in lines[1:]] == [1, 2, 3]


def test_watch_follow_survives_lease_takeover(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    old_owner = store_module.Journal("run-t")
    for _ in range(2):
        old_owner.append("phase_upsert", {"by": "old"}, card="card-1", phase="implement", attempt=1)
    # The new owner opens the journal now and caches seq 2, while the stuck
    # old owner is still appending: only `reseek` keeps it from reusing seq 3.
    new_owner = store_module.Journal("run-t")

    def old_owner_keeps_writing() -> None:
        for _ in range(2):
            old_owner.append("phase_upsert", {"by": "old"}, card="card-1", phase="implement", attempt=1)

    def new_owner_takes_over() -> None:
        new_owner.reseek()
        for _ in range(2):
            new_owner.append("phase_upsert", {"by": "new"}, card="card-1", phase="implement", attempt=1)

    result, _ = _watch_follow(
        monkeypatch,
        "run-t",
        actions=[old_owner_keeps_writing, new_owner_takes_over, lambda: None],
    )

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    events = lines[1:]
    seqs = [event["seq"] for event in events]
    assert seqs == [1, 2, 3, 4, 5, 6]  # strictly increasing, contiguous, no repeat
    assert [event["payload"]["by"] for event in events] == ["old"] * 4 + ["new"] * 2
    assert {event["run_id"] for event in events} == {"run-t"}


def test_watch_follow_all_picks_up_a_run_created_later(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    # Nothing to watch yet: the hello line alone, and nothing created under runs/.
    idle, _ = _watch_follow(monkeypatch, "--all", actions=[lambda: None, lambda: None])
    assert idle.exit_code == 0, idle.output
    assert _stream(idle) == [_hello(tmp_path)]
    assert not _watch_runs_dir(tmp_path).exists()

    third = json.dumps(_watch_line("run-new", 3), sort_keys=True)
    created: list[dict[str, Any]] = []

    def create_run_with_a_torn_tail() -> None:
        created.extend(
            _write_watch_journal(tmp_path, "run-new", [1, 2], tail=third[:20])
        )

    def finish_the_torn_line() -> None:
        journal = _watch_runs_dir(tmp_path) / "run-new" / store_module.JOURNAL_NAME
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(third[20:] + "\n")

    result, _ = _watch_follow(
        monkeypatch,
        "--all",
        actions=[lambda: None, create_run_with_a_torn_tail, finish_the_torn_line],
    )

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    # Seqs 1 and 2 on poll 2 with seq 3 held back as a write in flight, then seq 3 on poll 3.
    assert lines[1:] == created + [_watch_line("run-new", 3)]


def test_watch_follow_tolerates_a_journal_that_disappears(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    backlog = _write_watch_journal(tmp_path, "run-a", [1])
    journal = _watch_runs_dir(tmp_path) / "run-a" / store_module.JOURNAL_NAME
    recreated: list[dict[str, Any]] = []

    def delete_journal() -> None:
        journal.unlink()

    def recreate_journal() -> None:
        recreated.extend(_write_watch_journal(tmp_path, "run-a", [1, 2]))

    result, _ = _watch_follow(
        monkeypatch, "run-a", actions=[delete_journal, recreate_journal]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    # Seq 1 is not repeated: the run's cursor outlived the missing file.
    assert lines[1:] == backlog + recreated[1:]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "watch_follow_observes or watch_follow_survives or watch_follow_all_picks or watch_follow_tolerates" -v`
Expected: all four FAIL. `_stream_watch` does not poll yet, so each test's `sleeps` stays empty and its `actions` never run. The first fails on `assert sleeps == [...]`. The takeover test fails on `assert seqs == [1, 2, 3, 4, 5, 6]` because only `[1, 2]` is output. The `--all` test fails on `assert lines[1:] == created + [...]` because only the hello line is output. The disappearing-journal test fails because `recreated` stays empty and seq 2 never appears. (In the `--all` test the idle part passes before the change; it guards the "wrong data dir sees only the hello line" rule.)

- [ ] **Step 3: Add `_follow_watch` and use it in `_stream_watch`**

In `src/agent_manager/cli.py`, replace the Task 1 `_stream_watch`:

```python
def _stream_watch(run_id: str | None, *, since: int) -> None:
    """The body of `am watch --follow`, once `watch_for` has accepted the call."""
    _emit_stream_line(_watch_hello())
    cursors: dict[str, int] = {}
    for event in _poll_watch(run_id, since=since, cursors=cursors):
        _emit_stream_line(event)
```

with:

```python
def _follow_watch(
    run_id: str | None,
    *,
    since: int,
    sleep: Callable[[float], None],
    max_polls: int | None,
) -> Iterator[dict[str, Any]]:
    """The backlog above `since`, then every line appended after it.

    One pass at once for the backlog, then `sleep(WATCH_POLL_SECONDS)` and
    another pass, `max_polls` times or forever when it is `None`. One cursor
    dict spans every pass, so no `seq` of a run is emitted twice and none is
    skipped, however its lines are spread across polls.
    """
    cursors: dict[str, int] = {}
    yield from _poll_watch(run_id, since=since, cursors=cursors)
    polls = 0
    while max_polls is None or polls < max_polls:
        sleep(WATCH_POLL_SECONDS)
        polls += 1
        yield from _poll_watch(run_id, since=since, cursors=cursors)


def _stream_watch(run_id: str | None, *, since: int) -> None:
    """The body of `am watch --follow`, once `watch_for` has accepted the call.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll.
    """
    _emit_stream_line(_watch_hello())
    for event in _follow_watch(
        run_id, since=since, sleep=_watch_sleep, max_polls=WATCH_MAX_POLLS
    ):
        _emit_stream_line(event)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "watch" -v`
Expected: PASS for all four new tests, the two Task 1 tests, and every one-shot `test_watch_*` test.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Poll followed journals with a per-run (run_id, seq) cursor"
```

---

### Task 3: Ending the stream: Ctrl-C, closed pipe, mid-stream corruption

**Files:**
- Modify: `src/agent_manager/cli.py` (`_stream_watch` from Task 2; add `_silence_stdout` right above it)
- Test: `tests/test_cli.py` (append after the Task 2 tests)

**Interfaces:**
- Consumes: `_follow_watch`, `_emit_stream_line`, `_watch_hello`, `_watch_sleep`, `WATCH_MAX_POLLS` (Tasks 1-2), `WATCH_HANDLED`, `EXIT_ERROR`. Test helpers from Task 1.
- Produces: `_silence_stdout() -> None`. `_stream_watch(run_id: str | None, *, since: int) -> None` now returns normally on Ctrl-C or a closed pipe, and raises `typer.Exit(EXIT_ERROR)` after writing one stderr line on a `WATCH_HANDLED` error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_watch_follow_mid_stream_corruption_ends_stream_with_stderr_message(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    backlog = _write_watch_journal(tmp_path, "run-a", [1])
    journal = _watch_runs_dir(tmp_path) / "run-a" / store_module.JOURNAL_NAME
    appended: list[dict[str, Any]] = []

    def append_second() -> None:
        appended.extend(_append_watch_journal(tmp_path, "run-a", [2]))

    def corrupt_third() -> None:
        # Newline-terminated, so a corrupt line rather than a torn tail.
        _append_watch_journal(tmp_path, "run-a", [], tail="not json\n")

    result, sleeps = _watch_follow(
        monkeypatch,
        "run-a",
        actions=[append_second, corrupt_third, lambda: None],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert len(sleeps) == 2  # the stream ended on the corrupt poll
    assert _stream(result) == [_hello(tmp_path), *backlog, *appended]
    message = result.stderr.strip()
    assert "\n" not in message
    assert f"{journal}:3:" in message


def test_watch_follow_ctrl_c_exits_zero_quietly(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    backlog = _write_watch_journal(tmp_path, "run-a", [1, 2])

    def press_ctrl_c() -> None:
        raise KeyboardInterrupt

    result, _ = _watch_follow(monkeypatch, "run-a", actions=[press_ctrl_c])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [_hello(tmp_path), *backlog]


def test_watch_follow_closed_pipe_exits_zero_quietly(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1, 2])
    real_emit = cli._emit_stream_line
    emitted: list[Any] = []

    def emit_into_a_closed_pipe(obj) -> None:
        emitted.append(obj)
        if len(emitted) == 2:  # the reader went away after the hello line
            raise BrokenPipeError(32, "Broken pipe")
        real_emit(obj)

    monkeypatch.setattr(cli, "_emit_stream_line", emit_into_a_closed_pipe)

    result, _ = _watch_follow(monkeypatch, "run-a", actions=[lambda: None])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [_hello(tmp_path)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "watch_follow_mid_stream or watch_follow_ctrl_c or watch_follow_closed_pipe" -v`
Expected: all three FAIL. The corruption test gets exit code 1, because the uncaught `CorruptJournalError` lands in `CliRunner`'s `except Exception`. The Ctrl-C test gets exit code 1 with `Aborted!` on stderr, because Typer turns `KeyboardInterrupt` into an abort. The closed-pipe test gets exit code 1 from the uncaught `BrokenPipeError`. If any of them already passes, stop and investigate before going on.

- [ ] **Step 3: Handle the three endings in `_stream_watch`**

In `src/agent_manager/cli.py`, replace the Task 2 `_stream_watch`:

```python
def _stream_watch(run_id: str | None, *, since: int) -> None:
    """The body of `am watch --follow`, once `watch_for` has accepted the call.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll.
    """
    _emit_stream_line(_watch_hello())
    for event in _follow_watch(
        run_id, since=since, sleep=_watch_sleep, max_polls=WATCH_MAX_POLLS
    ):
        _emit_stream_line(event)
```

with:

```python
def _silence_stdout() -> None:
    """Point fd 1 at /dev/null once the reader has closed the pipe.

    Without it, the interpreter's own flush of stdout at exit raises a second
    `BrokenPipeError` and prints it (Python docs, "Note on SIGPIPE"). A
    stdout with no file descriptor (a test runner's buffer) is left alone.
    """
    try:
        descriptor = sys.stdout.fileno()
    except (OSError, ValueError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, descriptor)
    os.close(devnull)


def _stream_watch(run_id: str | None, *, since: int) -> None:
    """The body of `am watch --follow`, once `watch_for` has accepted the call.

    `_watch_sleep` and `WATCH_MAX_POLLS` are looked up at call time, so a
    test that replaces them controls every poll. Ctrl-C and a closed pipe are
    how a stream normally ends: exit 0, nothing on stderr. A journal that
    turns corrupt after the hello line cannot get an envelope, because every
    line after the first must be a JournalLine. So its message goes to stderr
    and the exit is `EXIT_ERROR`.
    """
    try:
        _emit_stream_line(_watch_hello())
        for event in _follow_watch(
            run_id, since=since, sleep=_watch_sleep, max_polls=WATCH_MAX_POLLS
        ):
            _emit_stream_line(event)
    except KeyboardInterrupt:
        return
    except BrokenPipeError:
        _silence_stdout()
        return
    except WATCH_HANDLED as error:
        typer.echo(f"am watch: {error}", err=True)
        raise typer.Exit(EXIT_ERROR) from None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "watch" -v`
Expected: PASS for the three new tests and for every earlier `test_watch_*` and `test_watch_follow_*` test.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with the `e2e` test deselected by `addopts`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "End am watch --follow quietly on Ctrl-C or a closed pipe, exit 3 on mid-stream corruption"
```

---

## Spec coverage map

- Validation first and refusal (envelope, exit 3, no stream line, no run dir): Task 1, `test_watch_follow_refusal_prints_envelope_and_no_stream` (spec test 2).
- Hello line shape and `--since` backlog: Task 1, `test_watch_follow_hello_line_shape` (spec test 4).
- Body is compact, bare, newline-terminated and flushed, and `--pretty` affects only a refusal: Task 1 (`_emit_stream_line`, hello-line test).
- Backlog then live lines, appended line seen exactly once: Task 2, `test_watch_follow_observes_a_line_appended_after_start` (spec test 1).
- Cursor `(run_id, seq)` and lease takeover with `reseek()`: Task 2, `test_watch_follow_survives_lease_takeover` (spec test 3).
- `--all` relists runs each poll, wrong data dir shows only the hello line, torn tail held back: Task 2, `test_watch_follow_all_picks_up_a_run_created_later` (spec test 5).
- Followed journal disappears means nothing new: Task 2, `test_watch_follow_tolerates_a_journal_that_disappears`.
- Polling about every 250ms, nothing poll-related in output, no new dependency: Tasks 1-2 (`WATCH_POLL_SECONDS`, `time.sleep`, sleeps assertion).
- Injectable sleep and stop: Task 1 (`_watch_sleep`, `WATCH_MAX_POLLS`), used by Task 2's `_follow_watch`.
- Ctrl-C or closed pipe means exit 0 quietly: Task 3.
- Mid-stream corruption means stderr line and exit 3: Task 3, `test_watch_follow_mid_stream_corruption_ends_stream_with_stderr_message` (spec test 6).
- `_for_reading` only, `paths.data_dir()` only: by reuse of `_journal_events` and `paths.list_run_ids()` in `_poll_watch`, with no `Journal(run_id)` in any new production code.
- README untouched, non-follow form unchanged: no task edits README.md, and the one-shot tests run in every Task's Step 4/7.
