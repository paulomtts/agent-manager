<!-- task-pipeline: validated -->
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

---

# `am watch` (one-shot) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a terminating `am watch [RUN_ID | --all] [--since SEQ] [--pretty]` command that prints a run's (or every run's) journal events once as a `{"ok": true, "data": {"events": [...]}}` envelope, without ever creating a run directory.

**Architecture:** A new read-only helper `paths.list_run_ids()` lists the run directories under `paths.data_dir() / "runs"` without creating anything. In `cli.py`, a pure-ish `watch_for(...)` builds the payload by reading journals only through `store.Journal._for_reading(run_id).read(ignore_torn_tail=True)`, mapping `MissingJournalError` to `UnknownRunError`; the Typer command `watch` wraps it in the same envelope/exit-3 pattern as `status`/`runs`/`logs`, catching `store.JournalError` in addition to `HANDLED` so a corrupt journal is an envelope, not a traceback.

**Tech Stack:** Python, Typer, Pydantic v2, pytest with `typer.testing.CliRunner`, run with `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-add-am-watch-for-a-one-43f4f076-design.md` (prepended above), derived from `docs/superpowers/specs/2026-10-02-am-watch-design.md` §3.6-3.7.

## Global Constraints

- Never construct `Journal(run_id)` in the watch path; read only via `store_module.Journal._for_reading(run_id).read(ignore_torn_tail=True)`.
- Resolve the data directory only through `paths.data_dir()` (honours `XDG_DATA_HOME`).
- No `--follow` option, not even a stub (sibling cba3e48f owns it).
- Do not touch `README.md` (sibling b442ff58 owns it; README.md:410 stays as is).
- Do not add `JournalError` to the global `HANDLED` tuple (cli.py:1072); catch it only in the `watch` command, so no other command's crash behaviour changes.
- Refusals: `typer.echo(render(error_envelope(error), pretty=pretty))` then `raise typer.Exit(EXIT_ERROR) from None` (exit 3).
- Every new test is in the default unit tier, unmarked (no `e2e`, no `requires_git`/`requires_brd`), sets `XDG_DATA_HOME` into `tmp_path`, and writes `journal.jsonl` files directly.
- Verification: `uv run pytest`. No lint or typecheck step.

## Review Focus

- A run id that is a path (`../escape`, `a/b`, `.`, `..`, empty) must be refused as `UnknownRunError` and must not read a `journal.jsonl` outside `<data dir>/runs/`; pinned by `test_watch_refuses_a_run_id_that_is_a_path` in Task 2.
- A corrupt (newline-terminated non-JSON) journal line must give an `ok: false` `CorruptJournalError` envelope with exit 3, for one run and under `--all`; pinned by `test_watch_corrupt_journal_is_an_envelope_not_a_traceback` (Task 2) and `test_watch_all_corrupt_journal_is_an_envelope` (Task 3).
- A negative `--since` must be refused with a `CliError` envelope at exit 3, not silently treated as 0; pinned by `test_watch_refuses_a_negative_since` in Task 2.
- `--since` beyond the last seq is an empty list with exit 0, not a refusal; pinned by `test_watch_since_past_the_last_seq_is_empty` in Task 2.
- Under `--all`, a run directory with no journal yet, and a plain file sitting in `runs/`, are skipped silently, and `--since` applies to each run's own seq; pinned by `test_watch_all_skips_a_run_with_no_journal_yet` and `test_watch_all_applies_since_to_each_runs_own_seq` in Task 3, and `test_list_run_ids_lists_only_directories_sorted` in Task 1.

Note on malformed-but-JSON lines (e.g. a known event missing `ts`): `JournalLine.model_validate` raises `pydantic.ValidationError`, which subclasses `ValueError` and is already in `HANDLED`, so it already yields an envelope; no extra code is needed.

---

### Task 1: `paths.list_run_ids()` — list run directories without creating any

**Files:**
- Modify: `src/agent_manager/paths.py` (append after `highest_attempt`, currently ending at line 72)
- Test: `tests/test_paths.py` (append at end of file, after `test_highest_attempt_stops_at_the_first_gap`)

**Interfaces:**
- Consumes: `paths.data_dir() -> Path` (paths.py:14).
- Produces: `paths.list_run_ids() -> list[str]` — sorted names of the directories directly under `data_dir() / "runs"`; `[]` when `runs/` does not exist; creates neither `runs/` nor any run directory.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paths.py`:

```python
def test_list_run_ids_lists_only_directories_sorted(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    runs = tmp_path / "agent-manager" / "runs"
    (runs / "run-b").mkdir(parents=True)
    (runs / "run-a").mkdir()
    (runs / "stray.txt").write_text("not a run\n")

    assert paths.list_run_ids() == ["run-a", "run-b"]
    # Listing only: nothing was added or removed under runs/.
    assert sorted(p.name for p in runs.iterdir()) == ["run-a", "run-b", "stray.txt"]


def test_list_run_ids_is_empty_and_creates_nothing_without_a_runs_directory(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert paths.list_run_ids() == []
    assert not (tmp_path / "agent-manager" / "runs").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -k list_run_ids -v`
Expected: both FAIL with `AttributeError: module 'agent_manager.paths' has no attribute 'list_run_ids'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/paths.py`:

```python
def list_run_ids() -> list[str]:
    """Every run id that has a directory under `data_dir()/runs`, sorted.

    Lists only. Unlike `run_dir`, it creates neither the `runs` directory nor
    any run directory, so a reader that walks every run (`am watch --all`)
    leaves the data directory as it found it. A missing `runs` directory is an
    empty list, not an error. Plain files beside the run directories are not
    runs and are skipped.
    """
    runs = data_dir() / "runs"
    if not runs.is_dir():
        return []
    return sorted(entry.name for entry in runs.iterdir() if entry.is_dir())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: all PASS, including the two new `list_run_ids` tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/paths.py tests/test_paths.py
git commit -m "Add paths.list_run_ids, a run-directory walk that creates nothing"
```

---

### Task 2: `am watch RUN_ID` — single-run one-shot read with `--since`

**Files:**
- Modify: `src/agent_manager/cli.py` — add `paths,` to the `from agent_manager import (...)` block (lines 32-44, between `orchestrate,` and `prompt,`); insert `WATCH_HANDLED`, `_check_watch_run_id`, `_journal_events`, `watch_for` and the `watch` command after the `logs` command (which ends at line 1509, just before `def _resume_from_checkpoint` at line 1512).
- Test: `tests/test_cli.py` (append a new section at the end of the file, after the last test `test_a_card_comment_the_board_refuses_is_a_warning_and_changes_nothing_else`).

**Interfaces:**
- Consumes: `store_module.Journal._for_reading(run_id) -> Journal` (store.py:304), `Journal.read(*, ignore_torn_tail: bool) -> list[JournalLine]` (store.py:394), `store_module.MissingJournalError`, `store_module.JournalError`, `store_module.JournalLine`, `store_module.JOURNAL_NAME`; `HANDLED`, `CliError`, `UnknownRunError`, `render`, `ok_envelope`, `error_envelope`, `EXIT_ERROR` already in `cli.py`.
- Produces (used by Task 3):
  - `cli.WATCH_HANDLED: tuple[type[BaseException], ...]` = `(*HANDLED, store_module.JournalError)`
  - `cli._check_watch_run_id(run_id: str) -> None` — raises `UnknownRunError` for a path-like id
  - `cli._journal_events(run_id: str, *, since: int) -> list[dict[str, Any]]` — raises `store_module.MissingJournalError` when there is no journal
  - `cli.watch_for(run_id: str, *, since: int = 0) -> dict[str, Any]` returning `{"events": [...]}` (Task 3 widens the signature)
  - Test helpers in `tests/test_cli.py`: `WATCH_TS`, `_watch_runs_dir(tmp_path) -> Path`, `_write_watch_journal(tmp_path, run_id, seqs, *, tail="") -> list[dict[str, Any]]`, `_watch(*args: str)`

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
# ── am watch, one-shot (card 43f4f076) ─────────────────────────────────────
#
# Default (unit) tier per design §14: no git, no brd, no harness. Journals are
# written straight to `XDG_DATA_HOME/agent-manager/runs/<id>/journal.jsonl`.

WATCH_TS = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _watch_runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "xdg" / "agent-manager" / "runs"


def _write_watch_journal(
    tmp_path: Path, run_id: str, seqs: list[int], *, tail: str = ""
) -> list[dict[str, Any]]:
    """Write `run_id`'s journal with one line per seq, then `tail` verbatim.

    Returns the lines as `am watch` must report them: JSON-mode dumps.
    """
    run_dir = _watch_runs_dir(tmp_path) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dumped = [
        store_module.JournalLine(
            seq=seq,
            ts=WATCH_TS,
            run_id=run_id,
            event="phase_upsert",
            card="card-1",
            phase="implement",
            attempt=1,
            payload={"status": "started", "n": seq},
        ).model_dump(mode="json")
        for seq in seqs
    ]
    text = "".join(json.dumps(line, sort_keys=True) + "\n" for line in dumped)
    (run_dir / store_module.JOURNAL_NAME).write_text(text + tail, encoding="utf-8")
    return dumped


def _watch(*args: str):
    return runner.invoke(cli.app, ["watch", *args])


def test_watch_single_run_returns_events_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(tmp_path, "run-a", [1, 2, 3])

    result = _watch("run-a")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"ok": True, "data": {"events": written}}
    assert [event["seq"] for event in written] == [1, 2, 3]

    pretty = _watch("run-a", "--pretty")
    assert pretty.exit_code == 0, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(result.stdout)


def test_watch_since_filters_to_later_seqs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(tmp_path, "run-a", [1, 2, 3, 4])

    result = _watch("run-a", "--since", "2")

    assert result.exit_code == 0, result.output
    events = json.loads(result.stdout)["data"]["events"]
    assert [event["seq"] for event in events] == [3, 4]
    assert events == written[2:]


def test_watch_since_past_the_last_seq_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1, 2])

    result = _watch("run-a", "--since", "99")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"ok": True, "data": {"events": []}}


def test_watch_refuses_a_negative_since(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1])

    result = _watch("run-a", "--since", "-1")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CliError"
    assert "--since" in envelope["error"]["message"]


def test_watch_unknown_run_refuses_and_creates_no_run_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = _watch("no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]
    assert not (_watch_runs_dir(tmp_path) / "no-such-run").exists()
    assert not _watch_runs_dir(tmp_path).exists()


def test_watch_tolerates_a_torn_last_line(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(
        tmp_path, "run-a", [1, 2], tail='{"seq": 3, "ts": "2026-10'
    )

    result = _watch("run-a")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["events"] == written


def test_watch_corrupt_journal_is_an_envelope_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # Newline-terminated, so it is not a torn tail: a corrupt line.
    _write_watch_journal(tmp_path, "run-a", [1], tail="not json\n")

    result = _watch("run-a")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CorruptJournalError"


@pytest.mark.parametrize("run_id", ["../escape", "a/b", ".", ".."])
def test_watch_refuses_a_run_id_that_is_a_path(tmp_path, monkeypatch, run_id):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # runs/ must exist for "runs/../escape" to resolve on disk, so that without
    # the guard "../escape" really would read the journal one level above it.
    _watch_runs_dir(tmp_path).mkdir(parents=True)
    escape = tmp_path / "xdg" / "agent-manager" / "escape"
    escape.mkdir(parents=True)
    line = store_module.JournalLine(
        seq=1, ts=WATCH_TS, run_id="escape", event="run_upsert"
    ).model_dump(mode="json")
    (escape / store_module.JOURNAL_NAME).write_text(
        json.dumps(line) + "\n", encoding="utf-8"
    )

    result = _watch(run_id)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert list(_watch_runs_dir(tmp_path).iterdir()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k watch -v`
Expected: every new test FAILS — `watch` is not a command yet, so each invocation exits 2 with "No such command 'watch'" and the `exit_code` assertions fail (exit_code 2 != 0 / != 3).

- [ ] **Step 3: Add `paths` to the `cli.py` imports**

In `src/agent_manager/cli.py`, change the `from agent_manager import (...)` block (lines 32-44) to:

```python
from agent_manager import (
    board,
    census,
    comments,
    control,
    dag,
    dispatch,
    locks,
    models,
    orchestrate,
    paths,
    prompt,
    store as store_module,
)
```

(`paths` is used by Task 3's `--all` branch; adding it here keeps Task 3's diff to the watch code.)

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/cli.py`, insert immediately after the `logs` command (after the line `    typer.echo(render(ok_envelope(payload), pretty=pretty))` that ends `logs`, before `def _resume_from_checkpoint(`):

```python
WATCH_HANDLED: tuple[type[BaseException], ...] = (*HANDLED, store_module.JournalError)
"""`HANDLED` plus `JournalError`, for `watch` only.

A corrupt journal is a refusal for a reader, so `watch` turns it into an
`ok: false` envelope at exit 3. It is not added to `HANDLED` itself: for the
commands that write a journal, a corrupt one is still a bug that should crash
with its stack intact. `MissingJournalError` never reaches this tuple; `watch_for`
turns it into `UnknownRunError` first.
"""


def _check_watch_run_id(run_id: str) -> None:
    """Refuse a run id that is a path rather than one directory name.

    `Journal._for_reading` joins the id onto `<data dir>/runs/`, so `..` or a
    `/` would read a `journal.jsonl` outside the runs directory.
    """
    if run_id in ("", ".", "..") or Path(run_id).name != run_id:
        raise UnknownRunError(
            f"run id {run_id!r} is not a run directory name"
            " (`agent-manager watch --all` reads every run there is)"
        )


def _journal_events(run_id: str, *, since: int) -> list[dict[str, Any]]:
    """`run_id`'s journal lines with `seq > since`, JSON-mode, in `seq` order.

    Opened through `Journal._for_reading`, never `Journal(run_id)`: the normal
    constructor calls `paths.run_dir`, which would create a directory for a run
    that does not exist (am-watch design 3.7). A torn last line is an append in
    flight and is skipped. Raises `MissingJournalError` when there is no journal.
    """
    lines = store_module.Journal._for_reading(run_id).read(ignore_torn_tail=True)
    return [line.model_dump(mode="json") for line in lines if line.seq > since]


def watch_for(run_id: str, *, since: int = 0) -> dict[str, Any]:
    """The payload of `am watch RUN_ID`: `{"events": [...]}`."""
    if since < 0:
        raise CliError(f"--since must be 0 or more, got {since}")
    _check_watch_run_id(run_id)
    try:
        return {"events": _journal_events(run_id, since=since)}
    except store_module.MissingJournalError as error:
        raise UnknownRunError(
            f"run {run_id!r} has no journal under the data directory"
            " (`agent-manager watch --all` reads every run there is)"
        ) from error


@app.command("watch")
def watch(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run whose journal is read."),
    since: int = typer.Option(
        0, "--since", metavar="SEQ", help="Only events whose seq is greater than SEQ."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print a run's journal events once, as one envelope."""
    try:
        payload = watch_for(run_id, since=since)
    except WATCH_HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k watch -v`
Expected: all the new watch tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Add am watch RUN_ID: a one-shot read of one run's journal"
```

---

### Task 3: `am watch --all` and the RUN_ID/`--all` argument rule

**Files:**
- Modify: `src/agent_manager/cli.py` — replace the `watch_for` function and the `watch` command added in Task 2 (keep `WATCH_HANDLED`, `_check_watch_run_id`, `_journal_events` unchanged).
- Test: `tests/test_cli.py` (append after the Task 2 watch tests, at the end of the file).

**Interfaces:**
- Consumes: `paths.list_run_ids() -> list[str]` (Task 1); `WATCH_HANDLED`, `_check_watch_run_id(run_id: str) -> None`, `_journal_events(run_id: str, *, since: int) -> list[dict[str, Any]]` (Task 2); test helpers `_watch_runs_dir`, `_write_watch_journal`, `_watch` (Task 2).
- Produces: `cli.watch_for(run_id: str | None, *, all_runs: bool = False, since: int = 0) -> dict[str, Any]`; the `watch` command gains `--all` and an optional `RUN_ID`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
def test_watch_all_reads_across_more_than_one_run(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # Written b first, so the order in the output is the sort, not creation order.
    run_b = _write_watch_journal(tmp_path, "run-b", [1, 2])
    run_a = _write_watch_journal(tmp_path, "run-a", [1, 2, 3])

    result = _watch("--all")

    assert result.exit_code == 0, result.output
    events = json.loads(result.stdout)["data"]["events"]
    assert events == run_a + run_b
    assert [(event["run_id"], event["seq"]) for event in events] == [
        ("run-a", 1),
        ("run-a", 2),
        ("run-a", 3),
        ("run-b", 1),
        ("run-b", 2),
    ]


def test_watch_all_applies_since_to_each_runs_own_seq(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    run_a = _write_watch_journal(tmp_path, "run-a", [1, 2, 3])
    run_b = _write_watch_journal(tmp_path, "run-b", [1, 2, 3, 4])

    result = _watch("--all", "--since", "2")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["events"] == run_a[2:] + run_b[2:]


def test_watch_all_with_no_runs_directory_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = _watch("--all")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"ok": True, "data": {"events": []}}
    assert not _watch_runs_dir(tmp_path).exists()


def test_watch_all_skips_a_run_with_no_journal_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    run_a = _write_watch_journal(tmp_path, "run-a", [1])
    (_watch_runs_dir(tmp_path) / "run-not-started").mkdir()
    (_watch_runs_dir(tmp_path) / "stray.txt").write_text("not a run\n")

    result = _watch("--all")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["events"] == run_a
    assert sorted(p.name for p in (_watch_runs_dir(tmp_path) / "run-not-started").iterdir()) == []


def test_watch_all_corrupt_journal_is_an_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1])
    _write_watch_journal(tmp_path, "run-b", [1], tail="not json\n")

    result = _watch("--all")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CorruptJournalError"


def test_watch_rejects_run_id_with_all_and_neither(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1])

    for argv in (["run-a", "--all"], []):
        result = _watch(*argv)
        assert result.exit_code == cli.EXIT_ERROR, (argv, result.output)
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is False, argv
        assert envelope["error"]["type"] == "CliError", argv
        message = envelope["error"]["message"]
        assert "RUN_ID" in message and "--all" in message, argv
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "watch_all or watch_rejects" -v`
Expected: all six FAIL — `--all` is not an option yet ("No such option: --all", exit 2), and `am watch` with no argument is Typer's "Missing argument 'RUN_ID'" (exit 2), so every `exit_code` assertion fails.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, replace the whole `watch_for` function and the whole `watch` command from Task 2 with:

```python
def watch_for(
    run_id: str | None, *, all_runs: bool = False, since: int = 0
) -> dict[str, Any]:
    """The payload of `am watch`: `{"events": [...]}`.

    Exactly one of `run_id` and `all_runs`. With `run_id`, a run with no
    journal is `UnknownRunError`. With `all_runs`, every directory under
    `<data dir>/runs/` is read, a run with no journal yet is skipped, and a
    missing `runs/` is no events: a watcher pointed at the wrong data
    directory sees nothing, not an error (am-watch design 3.7). Events are
    ordered by `(run_id, seq)`; `since` filters each run's own `seq`.
    """
    if all_runs == (run_id is not None):
        raise CliError(
            "give exactly one of RUN_ID or --all:"
            " `am watch RUN_ID` reads one run, `am watch --all` reads every run"
        )
    if since < 0:
        raise CliError(f"--since must be 0 or more, got {since}")
    if run_id is not None:
        _check_watch_run_id(run_id)
        try:
            return {"events": _journal_events(run_id, since=since)}
        except store_module.MissingJournalError as error:
            raise UnknownRunError(
                f"run {run_id!r} has no journal under the data directory"
                " (`agent-manager watch --all` reads every run there is)"
            ) from error
    events: list[dict[str, Any]] = []
    for each in paths.list_run_ids():
        try:
            events.extend(_journal_events(each, since=since))
        except store_module.MissingJournalError:
            continue
    return {"events": events}


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
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print a run's journal events, or every run's, once, as one envelope."""
    try:
        payload = watch_for(run_id, all_runs=all_runs, since=since)
    except WATCH_HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 4: Run the watch tests to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/test_paths.py -k "watch or list_run_ids" -v`
Expected: every Task 1, Task 2 and Task 3 test PASSES (Task 2's single-run tests still pass with the widened signature).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with the `e2e`-marked test deselected by the default `addopts`. Then confirm README.md is untouched: before this task's commit, `HEAD~2` is the commit this branch was cut from, so `git diff --stat HEAD~2 -- README.md` must print nothing.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Add am watch --all and the RUN_ID-or---all argument rule"
```
