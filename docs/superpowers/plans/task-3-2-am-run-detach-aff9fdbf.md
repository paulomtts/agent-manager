<!-- task-pipeline: validated -->
# 3.2 am run --detach (card aff9fdbf)

Parent story fea654ef "am run --detach". Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` section 2 and its Compatibility section (additive keys only, journal schema 1). Builds on 3.1 (5daa944e, `task-3-1-split-run-pre-5daa944e-design.md`): this worktree already carries 3.1's seam (`preflight_card` / `recorded_card_run` / `run_card_engine` in cli.py, `preflight_milestone` / `recorded_milestone_run` / `run_milestone_engine` in orchestrate.py). The seam's shape is not changed here.

Out of scope (siblings or other items): the detach + `am watch --follow` + `am pause` + `am resume` e2e_fake scenario (3.3, ef6e5633), richer `am runs` fields, `logs --follow`, `--board` docs, and moving the missing-verification gate into pre-flight.

## Observable behaviour

- New flag `--detach` on `am run`, valid with `--card` and `--milestone`.
- The foreground runs stage 1 (pre-flight) and stage 2 (recorded: store opened, lease and claims taken, `started` rows written) exactly as a foreground run does. Every refusal raised there (parentless card, claimed card or key, live run, unknown or ambiguous milestone, blocker cycle) is reported with the same `HANDLED` error envelope and exit 3 as today, and nothing is detached.
- It then creates `<data dir>/runs/<run-id>/run.log` with mode 0600 (`paths.run_dir(run_id)`), hands stage 3 (the engine) to a child in its own session (`os.setsid` / `start_new_session` semantics, precedent harness/launcher.py:155) whose stdout and stderr go to `run.log`, prints exactly one envelope `{"ok":true,"data":{"run_id":...,"pid":<child pid>,"log":"<abs path of run.log>","detached":true}}` (honouring `--pretty`) and exits 0. The printed `run_id` is the id `am runs` lists for the run.
- Lease hand-off: the run's lease (same token, same claims) passes to the child. Before the parent prints, the lease row's `pid` is the child's pid, so `am runs` / `am status` see a live lease after the parent exits; the heartbeat runs in the child; the parent exits without releasing the lease or claims; the child releases them (then closes its store) when the engine ends, on every exit path, as the foreground does today. No sqlite connection or heartbeat thread may cross a fork: the parent stops its heartbeat and closes its store before the child starts, and the child opens its own store and adopts the existing token rather than taking a new lease. (Mechanism, fork after the recorded stage vs. a re-exec'd hidden entry, is the plan's choice; the `control.Lease` constructor already accepts `pid`/`host`, and the hand-off must not open a window where the row names a dead pid with a fresh heartbeat.)
- New surface this requires (the seam as 3.1 left it cannot do it): `recorded_card_run` / `recorded_milestone_run` end by `Lease.__exit__`, which releases the claims and the lease, so a plain exit of those contexts cannot be the parent's hand-off. The plan adds (a) a `control.Lease` hand-off exit that stops and joins the heartbeat and unbinds the store but releases nothing, and a way to construct or enter a `Lease` around an existing token (adopt: no `take_lease`, heartbeat restarts, `__exit__` releases as today); (b) a token-fenced `Store` method that sets the lease row's `pid` (and `host`) for a token, no-op for any other token, in the style of `beat`/`close_window`; (c) a way for the recorded contexts to exit by hand-off instead of release (a flag on the `RecordedRun` / `RecordedMilestoneRun`, or a sibling context), without changing the foreground path. Ordering: the parent spawns the child, then sets the row's pid to the child's pid while the parent is still alive (so the row never names a dead pid), then exits the hand-off, closes its store, and only then prints. The child must not touch the store before the pid update commits; the plan chooses how it waits (e.g. a pipe the parent writes after the update).
- The child runs stage 3 on the same `pre` and `recorded` data (same run id, `started_at`, plan rows, checkpoints), never on a re-derived one: a re-exec'd entry would have to rebuild them without minting a new run id or re-running `refresh_git`. A fork child never returns into Typer or pytest; it ends with `os._exit` after flushing. The detach orchestration (stage 1, stage 2, spawn, hand-off) lives in cli.py / orchestrate.py as a function taking the detacher, not inside `run_card` / `run_milestone`, whose foreground behaviour must not change. Detach applies to a fresh `--milestone` run only; `am resume` is untouched.
- When the engine ends, the child writes `<data dir>/runs/<run-id>/report.json` (mode 0600) holding the envelope a foreground run would have printed: `ok_envelope(payload)` on a normal end (done, escalated, stopped, blocked alike), or `error_envelope(error)` for a `HANDLED` error raised by the engine. An unhandled crash leaves its traceback in `run.log`, releases the lease and claims, and writes no report.json (the run is then resumable like any crashed run). report.json is written atomically (temp file in the same directory, then rename) so a reader never sees a partial file. `am status` reads the store as today and needs no change.
- Refused as `typer.BadParameter` (exit 2) in `_check_run_targets`, before anything is read: `--detach` with `--dry-run` ("--dry-run writes nothing and cannot be detached"), and `--detach` with `--board` (the board path was not split by 3.1 and board dispatch is spec item 5; refuse with a message saying `--detach` applies to `--card` and `--milestone`).
- A run without `--detach` is byte-for-byte unchanged. No journal or schema change.
- Missing verification is not a pre-flight refusal today (it is the engine's explore-phase `verification_gate`, steps/reducers.py:30, which depends on what exploration discovers); with `--detach` it surfaces in the child's outcome and report.json, as 3.1 decided. Note this in the README rather than moving it.

## Error paths

- Pre-flight or recorded-stage refusal: exit 3 envelope, no child, no run.log; same on-disk state as the foreground refusal.
- The detach itself fails (fork/spawn `OSError`, run.log cannot be created): the recorded context exits with the exception, so lease and claims are released and the store closed (3.1's guarantee); the error is reported as an exit-3 envelope if it maps to `HANDLED`, otherwise it propagates. The `started` run row remains, as with any engine crash.

## README

In the `am run` section: document `--detach`, the printed envelope, `run.log` and `report.json` (both mode 0600 under `<data dir>/runs/<run-id>/`), the two refusals, that missing verification surfaces in the report rather than the foreground, and that consumers ignore unknown keys. Add a `--detach` line to `RUN_EXAMPLES`.

## Tests (TDD, written first; `uv run pytest` green, plus `uv run pytest -m e2e_fake` for the last one)

Unit tests drive `am run --detach` through `CliRunner` with an injected fake detacher (a seam the plan names, standing in for the real fork/setsid) that records the call and returns a fake pid, plus `fake_board`, `XDG_DATA_HOME` under `tmp_path`, a plain `tmp_path` repo dir, `orchestrate.refresh_git` patched to a recorder, and a fake drive / `FakeDriver`. No subprocess: these are **unit tier (unmarked)**, each within 0.5s, in tests/test_cli.py (and tests/test_orchestrate.py where the milestone engine is exercised).

1. `--detach --dry-run` exits 2 with a BadParameter message; detacher never called; no run dir. Unit.
2. `--detach --board` exits 2; detacher never called. Unit.
3. `--card <parentless> --detach` and `--card <claimed> --detach` print the same exit-3 envelopes as without `--detach`; detacher never called; no run.log. Unit.
4. `--milestone <cycle>` and `--milestone <ambiguous>` with `--detach`: same exit-3 envelopes as foreground, detacher not called, `refresh_git` not called. Unit.
5. Successful card detach: exit 0, one envelope with exactly `run_id`, `pid` (the fake's), `log`, `detached: true`; `run_id` equals the id `runs_for` lists; `run.log` exists with mode 0600; the lease row's pid is the fake child pid and the lease and claims are still held after the command returns. Unit.
6. Same for `--milestone`: run row recorded `started` with planned rows `pending`, lease held with all milestone claim keys, driver not called in the parent. Unit.
7. The child body (run inline by the fake, against the handed-off lease token): adopts the existing token without a new `take_lease`, runs the engine with a fake drive, writes report.json (mode 0600) equal to the foreground payload's ok envelope, then releases lease and claims before closing the store. Unit.
8. The child body when the engine raises a `HANDLED` error writes the error envelope to report.json and releases the lease; when it raises an unhandled error, writes no report.json and still releases. Unit.
9a. The new surface, unit and against a real `Store` in `tmp_path`: the hand-off exit leaves lease row and claims in place and stops the heartbeat; the pid-setter changes the row only for the matching token; an adopting `Lease` takes no new lease, keeps the token and claims, and releases both on exit. Unit.
9. Detacher raising `OSError`: lease and claims released, store closed, no detached envelope printed. Unit.
10. A run without `--detach` still prints the full payload and exit code as before (one regression check through the existing seam tests is enough). Unit.
11. Real detach lifecycle, one test only, in tests/e2e/ marked **`@pytest.mark.e2e_fake`** (it spawns `am` as a subprocess under production wiring with the fake claude, real git and brd, so it cannot be unit or git tier): start `am run --milestone ... --detach` as a subprocess, parse the envelope, assert the subprocess exited 0 while `pid` is still alive in its own session, `am runs` lists the printed `run_id` with a live lease, and report.json appears with an ok envelope once the child exits. No watch, pause or resume (3.3).

---

# am run --detach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `am run --detach` for `--card` and `--milestone`: pre-flight and the recorded stage run in the foreground with today's refusals, then the engine moves to a forked child in its own session that owns the run's lease, logs to `run.log` and writes `report.json` when the engine ends.

**Architecture:** A new process-level module `src/agent_manager/detach.py` owns `run.log` / `report.json` creation and `fork_detacher` (fork, `setsid`, redirect, wait on a go-pipe, run the body, `os._exit`). `control.Lease` gains `hand_off()` (stop and join the heartbeat, unbind the store, release nothing; `__exit__` then becomes a no-op) and an `adopt=` token (bind to an existing lease with no `take_lease`; `__exit__` releases as today). `Store` gains `adopt_lease(token)` and `set_lease_holder(token, pid=, host=)`. `cli.detach_card` and `orchestrate.detach_milestone` run stage 1 and stage 2 through 3.1's unchanged seam, call `lease.hand_off()` inside the recorded context (so the context exits releasing nothing and closes the store), then `cli.hand_off_to_child` forks, points the lease row at the child pid over a fresh connection, and writes the go byte. The child (`cli.run_detached_child`) opens its own store, adopts the token, runs stage 3 on the same in-memory `pre`/`recorded` data, writes `report.json`, and releases.

**Tech Stack:** Python 3, Typer, Pydantic, sqlite3, `os.fork` / `os.setsid` / `os.pipe`, pytest (`CliRunner`, `FakeBoard`, `FakeDriver`), uv.

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-3-2-am-run-detach-aff9fdbf/docs/superpowers/specs/task-3-2-am-run-detach-aff9fdbf-design.md` (prepended verbatim above). Upstream: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` section 2.

Inputs note: the orchestrator's spec summary and exploration summary were both truncated at their caps (2336 to 2000 and 9033 to 8000 characters), which shows those upstream stages over-ran their brief. This plan was written from the spec on disk and from the code in this worktree, not from either summary.

## Mechanism decision and the one ordering deviation

- **Fork, not re-exec.** The child must run on the same `pre` / `recorded` objects (run id, `started_at`, plan rows, checkpoints) without re-minting a run id or re-running `refresh_git`. A fork carries them in memory. A re-exec would have to serialize and rebuild them.
- **Ordering.** The spec lists "spawn, set pid, exit the hand-off, close the store, print". It also says no sqlite connection or heartbeat thread may cross a fork. Both cannot hold if the pid is set on the same store after the fork, so this plan uses: `hand_off()` (heartbeat stopped and joined, store unbound) → recorded context exits (store closed, nothing released) → fork (child blocks on the go-pipe before touching any store) → parent opens a **fresh** `Store`, calls `set_lease_holder(token, pid=child)`, closes it → parent writes the go byte → parent prints. Every invariant still holds. No connection or thread is alive at fork time. The row names the parent pid (alive) until the update, then the child pid (alive, blocked). The child touches no store before the update commits. The heartbeat gap is milliseconds, far below `LEASE_STALE_SECONDS` (30s).
- **Recorded contexts unchanged (spec item c).** The hand-off flag lives on the `Lease` object. After `lease.hand_off()`, `Lease.__exit__` returns at once, so `run_lease` / `recorded_card_run` / `recorded_milestone_run` exit without releasing and still close the store. The foreground path never calls `hand_off()`, so it is unchanged.
- **Failure after hand-off.** If the detacher raises, or the pid update raises (the child is then told to abort through EOF on the pipe), `release_handed_off` releases the claims and lease over a fresh store, and the error propagates (exit 3 if it is in `HANDLED`, a traceback otherwise). If creating `run.log` fails, the failure is raised inside the recorded context before `hand_off()`, so 3.1's release-then-close guarantee covers it.

## File structure

- Create `src/agent_manager/detach.py`: `RUN_LOG_NAME`, `REPORT_NAME`, `FILE_MODE`, `Spawned`, `Detacher`, `create_run_log`, `write_report`, `fork_detacher`. It imports only `paths` and the stdlib.
- Modify `src/agent_manager/store.py`: `Store.adopt_lease`, `Store.set_lease_holder` (in the leases section, after `release_lease`).
- Modify `src/agent_manager/control.py`: `Lease.__init__(adopt=)`, `Lease.__enter__` adopt branch, `Lease.__exit__` hand-off short-circuit, `Lease.hand_off`, `Lease._stop_heartbeat`.
- Modify `src/agent_manager/cli.py`: `import socket`; import `detach`; `release_handed_off`, `run_detached_child`, `hand_off_to_child`, `detach_card`; `_check_run_targets(detach=)`; the `--detach` option and its dispatch in `run`; `RUN_EXAMPLES`.
- Modify `src/agent_manager/orchestrate.py`: import `detach`; `detach_milestone`.
- Modify `README.md`: the refusals paragraph and a new "Running detached with `--detach`" subsection.
- Tests (all unit/unmarked except the last): `tests/test_detach.py` (new, mirrors `detach.py`), `tests/test_store.py`, `tests/test_control.py`, `tests/test_cli.py`, `tests/test_orchestrate.py`, and `tests/e2e/test_detached_run.py` (new, `@pytest.mark.e2e_fake`).

## Global Constraints

- Python CLI (Typer + Pydantic) packaged with `uv`. Source under `src/agent_manager/`, tests mirror it under `tests/`.
- CLI output is JSON by default, `--pretty` for humans, envelope `{"ok": true, "data": ...}`; failures `{"ok": false, "error": {"type", "message"}}`.
- Exit codes: `0` success, `1` escalated (foreground only), `2` Typer usage errors (`typer.BadParameter`), `3` (`EXIT_ERROR`) for `HANDLED` refusals.
- New JSON keys are additive only; journal stays schema 1; no schema change.
- A run without `--detach` is byte-for-byte unchanged.
- `run.log` and `report.json` are mode `0600` under `<data dir>/runs/<run-id>/`; `report.json` is written atomically (temp file in the same directory, then rename).
- Refusal wording, exact: `"--dry-run writes nothing and cannot be detached"` and `"--detach applies to --card and --milestone, not --board"`.
- Test tiers: unmarked = unit, no subprocess of any kind, ≤0.5s per test; the real-spawn test is `@pytest.mark.e2e_fake` in `tests/e2e/`, opt-in with `uv run pytest -m e2e_fake`.
- `control.py` may import only `agent_manager.store`, `agent_manager.runtime.stop` and the stdlib (pinned by `test_control_module_imports_no_cli_orchestrate_or_grafo`).
- Verification: `uv run pytest` (default suite) and `uv run pytest -m e2e_fake tests/e2e/test_detached_run.py`.

## Review Focus

1. The lease pid update fails after the fork (database locked). The child must not run, and the lease and claims must be released. Pinned in Task 6 (`test_a_failed_lease_pid_update_aborts_the_child_and_releases`).
2. Between hand-off and adoption, another process takes the run's lease. The child must refuse to adopt, release nothing that is not its own, and start no heartbeat. Pinned in Task 2 (`test_an_adopting_lease_refuses_a_token_that_no_longer_holds_the_run`) and Task 3 (store-level `test_adopt_lease_refuses_a_token_that_does_not_hold_the_run`).
3. `--detach --pretty` prints the indented envelope, like every other command under `--pretty`. Pinned in Task 6 (`test_detach_honours_pretty`).
4. A `run.log` that already exists with a looser mode, or a umask that strips bits, still ends at exactly `0600`. A rewritten `report.json` leaves no temp file behind. Pinned in Task 1.
5. After the parent returns, no heartbeat thread is left alive and the parent's stores are closed, so nothing stays open in the process that printed. Pinned in Task 6 (`test_a_detached_card_run_prints_one_envelope_and_leaves_the_lease_to_the_child`, `_alive_heartbeats()` and `closes` assertions).

---

### Task 1: `detach.py` process primitives

**Files:**
- Create: `src/agent_manager/detach.py`
- Test: `tests/test_detach.py`

**Interfaces:**
- Consumes: `paths.run_dir(run_id) -> Path` (src/agent_manager/paths.py:47).
- Produces: `RUN_LOG_NAME = "run.log"`, `REPORT_NAME = "report.json"`, `FILE_MODE = 0o600`; `@dataclass(frozen=True) class Spawned(pid: int, go: Callable[[], None], abort: Callable[[], None])`; `Detacher = Callable[[Callable[[], None], Path], Spawned]`; `create_run_log(run_id: str) -> Path`; `write_report(run_id: str, text: str) -> Path`; `fork_detacher(body: Callable[[], None], log: Path) -> Spawned`.

`fork_detacher` really forks, so it gets no unit test (the unit tier forbids subprocesses). Task 9's e2e_fake test exercises it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_detach.py`:

```python
"""Unit tier: the files `am run --detach` leaves (card aff9fdbf).

`fork_detacher` really forks, so it is exercised only by the e2e_fake test
`tests/e2e/test_detached_run.py`; nothing here starts a process.
"""

import ast
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from agent_manager import detach, paths

RUN_ID = "20261004T090000Z-1a2b3c4d"


@pytest.fixture(autouse=True)
def data_home(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg"


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_create_run_log_makes_an_empty_0600_file_in_the_run_directory():
    log = detach.create_run_log(RUN_ID)

    assert log == paths.data_dir() / "runs" / RUN_ID / detach.RUN_LOG_NAME
    assert log.is_file()
    assert log.read_bytes() == b""
    assert _mode(log) == 0o600


def test_create_run_log_is_0600_whatever_the_umask_or_an_existing_file_said():
    existing = paths.run_dir(RUN_ID) / detach.RUN_LOG_NAME
    existing.write_text("earlier\n", encoding="utf-8")
    existing.chmod(0o644)
    old = os.umask(0)
    try:
        log = detach.create_run_log(RUN_ID)
    finally:
        os.umask(old)

    assert _mode(log) == 0o600
    assert log.read_text(encoding="utf-8") == "earlier\n"


def test_write_report_writes_one_line_at_0600_and_leaves_no_temp_file():
    path = detach.write_report(RUN_ID, json.dumps({"ok": True, "data": {"done": True}}))

    assert path == paths.data_dir() / "runs" / RUN_ID / detach.REPORT_NAME
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True, "data": {"done": True}}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert _mode(path) == 0o600
    assert sorted(entry.name for entry in path.parent.iterdir()) == [detach.REPORT_NAME]


def test_write_report_replaces_an_earlier_report_whole():
    detach.write_report(RUN_ID, '{"ok":false}')

    path = detach.write_report(RUN_ID, '{"ok":true}')

    assert path.read_text(encoding="utf-8") == '{"ok":true}\n'
    assert sorted(entry.name for entry in path.parent.iterdir()) == [detach.REPORT_NAME]


def test_spawned_carries_the_pid_and_the_two_signals():
    events: list[str] = []
    spawned = detach.Spawned(pid=4242, go=lambda: events.append("go"), abort=lambda: events.append("abort"))

    spawned.go()
    spawned.abort()

    assert spawned.pid == 4242
    assert events == ["go", "abort"]


def test_detach_module_imports_only_paths_and_the_stdlib():
    tree = ast.parse(Path(detach.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base == "agent_manager":
                modules |= {f"agent_manager.{alias.name}" for alias in node.names}
            else:
                modules.add(base)
    ours = {name for name in modules if name.split(".")[0] == "agent_manager"}
    theirs = {name.split(".")[0] for name in modules} - {"agent_manager"}
    assert ours <= {"agent_manager.paths"}
    assert theirs <= set(sys.stdlib_module_names) | {"__future__"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_detach.py -v`
Expected: FAIL at collection with `ImportError: cannot import name 'detach' from 'agent_manager'`.

- [ ] **Step 3: Write the module**

Create `src/agent_manager/detach.py`:

```python
"""Hand a run's engine to a child in its own session (`am run --detach`, card aff9fdbf).

Process-level pieces only: the run's `run.log` and `report.json`, and
`fork_detacher`, which forks the child. What the child runs, and the lease
hand-off around it, live in `cli` (`hand_off_to_child`, `run_detached_child`).
This module imports only `paths` and the stdlib.

Fork, not a re-exec: the child runs stage 3 on the very `pre` and `recorded`
objects the parent built, so no run id is minted twice and `refresh_git` is
not run again. The caller makes sure no sqlite connection and no heartbeat
thread is alive when `fork_detacher` is called.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from agent_manager import paths

RUN_LOG_NAME = "run.log"
"""The detached child's stdout and stderr, under the run's directory."""

REPORT_NAME = "report.json"
"""The envelope a foreground run would have printed, written when the engine ends."""

FILE_MODE = 0o600
"""Both files are the operator's alone: they can quote prompts and paths."""

_GO = b"g"
"""The one byte the parent writes once the lease row names the child."""


@dataclass(frozen=True)
class Spawned:
    """A child that exists but has not started its body yet.

    `go` lets it run; `abort` closes the pipe without the go byte, and the
    child exits without touching any store. Exactly one of the two is called.
    """

    pid: int
    go: Callable[[], None]
    abort: Callable[[], None]


Detacher = Callable[[Callable[[], None], Path], Spawned]
"""Starts `body` in a child whose output goes to `log`; production's is `fork_detacher`."""


def create_run_log(run_id: str) -> Path:
    """`<data dir>/runs/<run_id>/run.log`, created if missing, mode exactly 0600.

    `fchmod` after the open, because `O_CREAT`'s mode is masked by the umask
    and an existing file keeps whatever mode it had.
    """
    path = paths.run_dir(run_id) / RUN_LOG_NAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)
    finally:
        os.close(fd)
    return path


def write_report(run_id: str, text: str) -> Path:
    """Write `text` and a newline to the run's `report.json`, atomically, mode 0600.

    A temp file in the same directory, fsynced, then `os.replace`d over the
    target, so a reader sees no file or a whole one, never a partial one.
    """
    directory = paths.run_dir(run_id)
    target = directory / REPORT_NAME
    fd, temp = tempfile.mkstemp(dir=directory, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, FILE_MODE)
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        raise
    return target


def fork_detacher(body: Callable[[], None], log: Path) -> Spawned:
    """Fork a child in its own session that runs `body` once told to go.

    The child calls `setsid`, points stdin at /dev/null and stdout and stderr
    at `log`, then blocks reading the go-pipe. On the go byte it runs `body`;
    on EOF it runs nothing. Any exception is printed to `log`. It always
    ends with `os._exit`, so it never returns into Typer or pytest.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        _child(body, log, read_fd, write_fd)
    os.close(read_fd)

    def go() -> None:
        try:
            os.write(write_fd, _GO)
        finally:
            os.close(write_fd)

    def abort() -> None:
        os.close(write_fd)

    return Spawned(pid=pid, go=go, abort=abort)


def _child(body: Callable[[], None], log: Path, read_fd: int, write_fd: int) -> NoReturn:
    code = 1
    try:
        os.close(write_fd)
        os.setsid()
        _redirect(log)
        signal = os.read(read_fd, 1)
        os.close(read_fd)
        if signal == _GO:
            body()
            code = 0
    except BaseException:
        traceback.print_exc()
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(code)


def _redirect(log: Path) -> None:
    """stdin from /dev/null; stdout and stderr appended to `log`."""
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    out = os.open(log, os.O_WRONLY | os.O_APPEND)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.close(out)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_detach.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/detach.py tests/test_detach.py
git commit -m "feat(detach): run.log, atomic report.json and fork_detacher (card aff9fdbf)"
```

---

### Task 2: `Store.adopt_lease` and `Store.set_lease_holder`

**Files:**
- Modify: `src/agent_manager/store.py` (leases section, insert after `release_lease`, currently ending at line 1962)
- Test: `tests/test_store.py` (append after `test_a_lease_is_touched_only_through_its_own_token`, line 3513)

**Interfaces:**
- Consumes: `read_lease`, `LeaseRow`, `LeaseLostError(run_id, holder)`, `Store.bind_lease`, `Journal.reseek`.
- Produces: `Store.adopt_lease(token: str) -> LeaseRow` (raises `LeaseLostError` if the run's row is missing or carries another token; on success binds the store to `token` and reseeks the journal); `Store.set_lease_holder(token: str, *, pid: int, host: str) -> None` (no-op for any other token).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py` right after `test_a_lease_is_touched_only_through_its_own_token`:

```python
# -- lease hand-off (am run --detach, card aff9fdbf) ----------------------------


def test_set_lease_holder_moves_pid_and_host_only_for_its_own_token(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        lease = st.take_lease(
            token="t1", pid=42, host="h", now=_at(0), is_live=lambda row: False
        ).lease

        st.set_lease_holder("other", pid=7, host="elsewhere")
        assert store.read_lease(st.connection, RUN_ID) == lease

        st.set_lease_holder("t1", pid=7, host="elsewhere")
        assert store.read_lease(st.connection, RUN_ID) == dataclasses.replace(
            lease, pid=7, host="elsewhere"
        )
        assert st.connection.in_transaction is False
    finally:
        st.close()


def test_adopt_lease_binds_the_held_token_and_numbers_after_the_last_line(repo):
    first = store.Store.open(repo, RUN_ID)
    second = store.Store.open(repo, RUN_ID)
    try:
        first.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=lambda row: False)
        assert first.record_run(_run(repo)).seq == 1
        first.bind_lease(None)

        held = second.adopt_lease("t1")

        assert held.token == "t1"
        # Opened before line 1 was written: only the reseek numbers this line 2.
        assert second.record_run(_run(repo)).seq == 2
        thief = store.Store.open(repo, RUN_ID)
        try:
            thief.take_lease(token="thief", pid=9, host="h", now=_at(1), is_live=lambda row: False)
        finally:
            thief.close()
        with pytest.raises(store.LeaseLostError):
            second.record_run(_run(repo))
    finally:
        first.close()
        second.close()


def test_adopt_lease_refuses_a_token_that_does_not_hold_the_run(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.LeaseLostError) as missing:
            st.adopt_lease("t1")
        assert missing.value.holder is None

        st.take_lease(token="t2", pid=1, host="h", now=_at(0), is_live=lambda row: False)
        st.bind_lease(None)
        with pytest.raises(store.LeaseLostError) as other:
            st.adopt_lease("t1")
        assert other.value.holder is not None and other.value.holder.token == "t2"

        # Still unbound: bound to t1, this write would have been fenced out.
        assert st.record_run(_run(repo)).event == "run_upsert"
    finally:
        st.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "set_lease_holder or adopt_lease" -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'set_lease_holder'` / `'adopt_lease'`.

- [ ] **Step 3: Implement**

In `src/agent_manager/store.py`, insert directly after the `release_lease` method (after its `self._conn.commit()`):

```python
    def adopt_lease(self, token: str) -> LeaseRow:
        """Bind this store to `token`, which already holds this run's lease (card aff9fdbf).

        For the detached child of `am run --detach`: the parent took the lease
        and handed it off, so nothing is taken here. If the row is gone or
        carries another token, `LeaseLostError` names the holder now in place
        and the store stays unbound. Otherwise every run write is fenced by
        `token` from here on, and the journal re-reads its highest `seq`, as
        `take_lease` does, since the parent appended after this store opened.
        """
        with self._lock:
            current = read_lease(self._conn, self.run_id)
            if current is None or current.token != token:
                raise LeaseLostError(self.run_id, current)
            self.bind_lease(token)
            self._journal.reseek()
            return current

    def set_lease_holder(self, token: str, *, pid: int, host: str) -> None:
        """Name `pid` on `host` as this run's lease holder, if `token` still holds it.

        The parent of `am run --detach` points the row at its child before it
        prints, so `am runs` and `am status` judge the child's liveness. Any
        other token is a silent no-op, like `beat` and `close_window`.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE run_leases SET pid = ?, host = ? WHERE run_id = ? AND token = ?",
                (pid, host, self.run_id, token),
            )
            self._conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "set_lease_holder or adopt_lease or lease_is_touched" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): adopt_lease and set_lease_holder for the detach hand-off (card aff9fdbf)"
```

---

### Task 3: `control.Lease.hand_off` and `Lease(adopt=...)`

**Files:**
- Modify: `src/agent_manager/control.py:89-161` (class docstring, `__init__`, `__enter__`, `__exit__`; add `hand_off`, `_stop_heartbeat`)
- Test: `tests/test_control.py` (append after `test_lease_exit_leaves_a_new_holders_lease_and_claims_alone`, line 515)

**Interfaces:**
- Consumes: `Store.adopt_lease(token) -> LeaseRow`, `Store.bind_lease(None)` (Task 2).
- Produces: `control.Lease(store, *, claims=(), heartbeat=..., clock=..., pid=None, host=None, adopt: str | None = None)`; `Lease.hand_off() -> str` (returns the token; afterwards `__exit__` releases nothing); with `adopt=token`, `__enter__` calls `store.adopt_lease(token)`, sets `displaced = None`, beats once at `clock()`, starts the heartbeat; `__exit__` releases claims then lease as today.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_control.py` after `test_lease_exit_leaves_a_new_holders_lease_and_claims_alone`:

```python
# -- hand-off and adoption (am run --detach, card aff9fdbf) ---------------------


def test_a_handed_off_lease_stops_beating_unbinds_and_releases_nothing(root, opened_store):
    run = models.Run(
        id=RUN_ID,
        workflow="task",
        repo_dir=root,
        base_branch="main",
        branch_prefix="m10/",
        status="started",
        config=models.RunConfig(),
    )
    with control.Lease(opened_store, claims=["card:a"], clock=lambda: _at(0)) as lease:
        token = lease.hand_off()
        assert token == lease.token
        assert _heartbeat_threads() == []

    row = _read_lease(root)
    assert row is not None and row.token == token
    assert _held(root, token) == ["card:a"]
    thief = store.Store.open(root, RUN_ID)
    try:
        thief.take_lease(
            token="thief", pid=1, host="elsewhere", now=_at(0), is_live=lambda row: False
        )
    finally:
        thief.close()
    # Bound to the handed-off token, this write would raise LeaseLostError.
    assert opened_store.record_run(run).event == "run_upsert"


def test_an_adopting_lease_keeps_the_token_and_claims_and_releases_both_on_exit(
    root, opened_store
):
    with control.Lease(
        opened_store, claims=["card:a"], pid=4242, host="build-box", clock=lambda: _at(0)
    ) as first:
        token = first.hand_off()

    class NoTake(Wrapped):
        def take_lease(self, **kwargs: Any) -> Any:
            pytest.fail("an adopting lease took a new lease")

    child = store.Store.open(root, RUN_ID)
    try:
        with control.Lease(NoTake(child), adopt=token, clock=lambda: _at(100)) as adopted:
            assert adopted.token == token
            assert adopted.displaced is None
            assert len(_heartbeat_threads()) == 1
            row = _read_lease(root)
            assert row is not None
            assert (row.token, row.pid, row.host) == (token, 4242, "build-box")
            assert row.heartbeat_at == _at(100)
            assert _held(root, token) == ["card:a"]
        assert _read_lease(root) is None
        assert _all_claims(root) == []
        assert _heartbeat_threads() == []
    finally:
        child.close()


def test_an_adopting_lease_refuses_a_token_that_no_longer_holds_the_run(root, opened_store):
    _plant(root, token="someone-else", heartbeat_at=_at(0), claims=("card:a",))

    with pytest.raises(store.LeaseLostError) as caught:
        with control.Lease(opened_store, adopt="handed-off"):
            pytest.fail("adopted a lease another process holds")

    assert caught.value.holder is not None and caught.value.holder.token == "someone-else"
    row = _read_lease(root)
    assert row is not None and row.token == "someone-else"
    assert _all_claims(root) == [("card:a", RUN_ID, "someone-else")]
    assert _heartbeat_threads() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_control.py -k "handed_off or adopting" -v`
Expected: FAIL with `AttributeError: 'Lease' object has no attribute 'hand_off'` and `TypeError: Lease.__init__() got an unexpected keyword argument 'adopt'`.

- [ ] **Step 3: Implement**

In `src/agent_manager/control.py`, replace the whole `class Lease` from its docstring through the end of `__exit__` (lines 89-161) with:

```python
class Lease:
    """This process's claim on a run and its keys, held for a `with` block (C2, X5).

    `__enter__` takes a fresh token and calls `Store.take_lease` with `claims`,
    `now = clock()` and `is_live` built on `lease_is_live` at that `now`, so a
    live holder of the run or of any key refuses the lease
    (`store.LeaseHeldError`, `store.ClaimHeldError`) and a dead one is taken
    over and kept in `displaced`. Only then does it start a daemon heartbeat
    thread. That thread waits on a `threading.Event`, never `time.sleep`, so
    `__exit__` wakes it at once. `__exit__` stops and joins it, then releases
    this token's claims, then this token's lease, on any exit, and never
    swallows the exception. A process that took the lease over keeps its rows.

    `am run --detach` (card aff9fdbf) adds two things. `hand_off()` stops and
    joins the heartbeat and unbinds the store but releases nothing; `__exit__`
    then does nothing, so the token and its claims outlive this process for a
    child to adopt. `adopt=token` enters around a token that already holds the
    run: no `take_lease`, `Store.adopt_lease` instead (which refuses a token
    that no longer holds it), one beat at once, then the heartbeat; its
    `__exit__` releases exactly as above.
    """

    def __init__(
        self,
        store: Store,
        *,
        claims: Sequence[str] = (),
        heartbeat: float = HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
        pid: int | None = None,
        host: str | None = None,
        adopt: str | None = None,
    ) -> None:
        self._store = store
        self._claims = tuple(claims)
        self._heartbeat = heartbeat
        self._clock = clock
        self._pid = pid
        self._host = host
        self._adopt = adopt
        self._handed_off = False
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self.token = ""
        self.displaced: LeaseRow | None = None

    def __enter__(self) -> Lease:
        if self._adopt is None:
            self.token = uuid4().hex
            now = self._clock()
            taken = self._store.take_lease(
                token=self.token,
                pid=os.getpid() if self._pid is None else self._pid,
                host=socket.gethostname() if self._host is None else self._host,
                now=now,
                is_live=lambda row: lease_is_live(row, now=now),
                claims=self._claims,
            )
            self.displaced = taken.displaced
        else:
            self.token = self._adopt
            self._store.adopt_lease(self.token)
            self.displaced = None
            self.beat()
        self._handed_off = False
        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._keep_beating, name="am-lease-heartbeat", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._handed_off:
            # The token and its claims now belong to the detached child.
            return
        self._stop_heartbeat()
        try:
            try:
                self._store.release_claims(self.token)
            finally:
                self._store.release_lease(self.token)
        finally:
            # This process no longer holds the run: stop fencing its writes to
            # a token that is gone, as M9's store never fenced them.
            self._store.bind_lease(None)

    def hand_off(self) -> str:
        """Stop beating and unbind the store, releasing nothing; return the token.

        For `am run --detach`: called inside the `with` block, so the block's
        exit leaves the lease row and its claims for the child to adopt.
        """
        self._stop_heartbeat()
        self._store.bind_lease(None)
        self._handed_off = True
        return self.token

    def _stop_heartbeat(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
```

Leave `beat`, `close_window` and `_keep_beating` as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v`
Expected: PASS, the existing `Lease` tests and the import-boundary test included.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/control.py tests/test_control.py
git commit -m "feat(control): Lease.hand_off and Lease(adopt=) for am run --detach (card aff9fdbf)"
```

---

### Task 4: the `--detach` flag and its two usage refusals

**Files:**
- Modify: `src/agent_manager/cli.py` (imports at lines 20-48; `_check_run_targets` at line 1294; `run` options at lines 1407-1415 and the `_check_run_targets` call at line 1462)
- Test: `tests/test_cli.py` (imports at lines 15-51; new tests after `test_check_run_targets_accepts_board_with_or_without_a_prefix_and_with_dry_run`, line 3395)

**Interfaces:**
- Consumes: `detach` module (Task 1).
- Produces: `_check_run_targets(..., detach: bool = False)`; `run(..., detach_run: bool = typer.Option(False, "--detach", ...))`. Test helpers in `tests/test_cli.py` that later tasks reuse: `FAKE_CHILD_PID = 424242`, `class _FakeDetacher` (attributes `calls: list[Path]`, `events: list[str]`, `body`, `at_go`, constructor `error: BaseException | None = None`), `_mode(path) -> int`, `_alive_heartbeats() -> list[threading.Thread]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add `import re` and `import stat` to the stdlib imports (alphabetical, after `import json`/`import os` and before `import subprocess`), and add `detach,` to the `from agent_manager import (...)` list between `dag,` and `dispatch,`.

Insert after `test_check_run_targets_accepts_board_with_or_without_a_prefix_and_with_dry_run`:

```python
# ── am run --detach (card aff9fdbf) ─────────────────────────────────────────
#
# Unit tier: `detach.fork_detacher` is replaced by `_FakeDetacher`, which
# forks nothing; its `body` is run inline by the tests that need the child.

FAKE_CHILD_PID = 424242
"""The pid `_FakeDetacher` reports; no such child exists."""


class _FakeDetacher:
    """Stands in for `detach.fork_detacher`: records the call, starts nothing.

    `body` keeps what the real child would run. `at_go`, when set, runs as
    the parent writes the go byte, so a test can see the store at that moment.
    """

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[Path] = []
        self.events: list[str] = []
        self.body: Any = None
        self.at_go: Any = None

    def __call__(self, body: Any, log: Path) -> detach.Spawned:
        self.calls.append(log)
        if self.error is not None:
            raise self.error
        self.body = body
        return detach.Spawned(pid=FAKE_CHILD_PID, go=self._go, abort=self._abort)

    def _go(self) -> None:
        if self.at_go is not None:
            self.at_go()
        self.events.append("go")

    def _abort(self) -> None:
        self.events.append("abort")


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _alive_heartbeats() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "am-lease-heartbeat" and thread.is_alive()
    ]


DRY_RUN_DETACH = "--dry-run writes nothing and cannot be detached"
BOARD_DETACH = "--detach applies to --card and --milestone, not --board"


@pytest.mark.parametrize(
    ("kwargs", "message", "hint"),
    [
        (
            {"card": None, "milestone": "M9", "board": False, "branch_prefix": "m9", "dry_run": True},
            DRY_RUN_DETACH,
            "'--detach' / '--dry-run'",
        ),
        (
            {"card": SOME_CARD, "milestone": None, "board": False, "branch_prefix": "m9", "dry_run": True},
            DRY_RUN_DETACH,
            "'--detach' / '--dry-run'",
        ),
        (
            {"card": None, "milestone": None, "board": True, "branch_prefix": None, "dry_run": False},
            BOARD_DETACH,
            "'--detach' / '--board'",
        ),
    ],
)
def test_check_run_targets_refuses_detach_with_dry_run_or_board(kwargs, message, hint):
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(detach=True, **kwargs)

    assert caught.value.message == message
    assert caught.value.param_hint == hint


@pytest.mark.parametrize(
    "kwargs",
    [
        {"card": SOME_CARD, "milestone": None},
        {"card": None, "milestone": "M9", "max_concurrent": 2},
    ],
)
def test_check_run_targets_accepts_detach_with_card_or_milestone(kwargs):
    assert (
        cli._check_run_targets(dry_run=False, branch_prefix="m9", detach=True, **kwargs) is None
    )


@pytest.mark.parametrize(
    ("targets", "word"),
    [
        (["--milestone", "M9", "--branch-prefix", "m9", "--dry-run"], "detached"),
        (["--board"], "applies"),
    ],
)
def test_detach_with_dry_run_or_board_is_a_usage_error_that_detaches_nothing(
    tmp_path, monkeypatch, targets, word
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = runner.invoke(
        cli.app, ["run", *targets, "--detach", "--repo-dir", str(tmp_path)]
    )

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert word in result.output
    assert fake.calls == []
    assert not (paths.data_dir() / "runs").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "detach" -v`
Expected: FAIL. The function-level tests fail with `TypeError: _check_run_targets() got an unexpected keyword argument 'detach'`. The CLI tests fail because `"detached"` / `"applies"` is not in Typer's `No such option: --detach` output.

- [ ] **Step 3: Implement**

In `src/agent_manager/cli.py`:

1. Add `import socket` after `import os` / `import sqlite3` (stdlib block, alphabetical, between `sqlite3` and `sys`), and add `detach,` to the `from agent_manager import (...)` list between `dag,` and `dispatch,`.

2. In `_check_run_targets`, add the parameter and the two checks. Signature becomes:

```python
def _check_run_targets(
    *,
    card: str | None,
    milestone: str | None,
    dry_run: bool,
    max_concurrent: int | None = None,
    board: bool = False,
    branch_prefix: str | None = None,
    detach: bool = False,
) -> None:
```

Append to its docstring:

```
    `--detach` (card aff9fdbf) is refused with `--dry-run`, which writes
    nothing to hand off, and with `--board`, whose run was not split into
    pre-flight, recorded stage and engine.
```

Insert immediately before `if dry_run and card is not None:`:

```python
    if detach and dry_run:
        raise typer.BadParameter(
            "--dry-run writes nothing and cannot be detached",
            param_hint="'--detach' / '--dry-run'",
        )
    if detach and board:
        raise typer.BadParameter(
            "--detach applies to --card and --milestone, not --board",
            param_hint="'--detach' / '--board'",
        )
```

3. In `run`, add the option right after the `dry_run` option:

```python
    detach_run: bool = typer.Option(
        False,
        "--detach",
        help=(
            "With --card or --milestone: check, record and lease the run here, "
            "then hand it to a background process in its own session and print "
            "its run id, pid and log. Its output goes to "
            "<data dir>/runs/<run-id>/run.log and its final envelope to report.json."
        ),
    ),
```

and pass it to the check:

```python
    _check_run_targets(
        card=card,
        milestone=milestone,
        dry_run=dry_run,
        max_concurrent=max_concurrent,
        board=whole_board,
        branch_prefix=branch_prefix,
        detach=detach_run,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "detach or check_run_targets" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): --detach flag, refused with --dry-run and --board (card aff9fdbf)"
```

---

### Task 5: the detached child body and the release fallback

**Files:**
- Modify: `src/agent_manager/cli.py` (insert after the `HANDLED` docstring, line 1291, before `_check_run_targets`)
- Test: `tests/test_cli.py` (append after the Task 4 tests)

**Interfaces:**
- Consumes: `control.Lease(store, adopt=token)`, `Lease.hand_off()` (Task 3); `detach.write_report` (Task 1); `HANDLED`, `render`, `ok_envelope`, `error_envelope`, `Store`.
- Produces: `release_handed_off(root: Path, run_id: str, token: str) -> None`; `run_detached_child(*, root: Path, run_id: str, token: str, engine: Callable[[Store, control.Lease], dict[str, Any]]) -> None`. `run_detached_child` reads `detach.write_report` at call time, so tests can patch it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def _handed_off_card_run(root: Path, card_id: str) -> tuple[Any, str]:
    """Stage 1 and 2 of a card run, then the parent's hand-off: what the child inherits."""
    pre = _preflight(root, card_id)
    with cli.recorded_card_run(pre) as recorded:
        token = recorded.lease.hand_off()
    return pre, token


def _no_take_lease(self, **kwargs: Any) -> Any:
    pytest.fail("the detached child took a new lease instead of adopting its own")


def _report_path(run_id: str) -> Path:
    return paths.data_dir() / "runs" / run_id / detach.REPORT_NAME


def test_the_detached_child_adopts_the_lease_reports_then_releases_before_closing(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    pre, token = _handed_off_card_run(root, cards["subtask"])
    monkeypatch.setattr(store_module.Store, "take_lease", _no_take_lease)
    closes = _close_snapshots(monkeypatch)
    held_at_report: list[bool] = []
    real_write = detach.write_report

    def spying_write(run_id: str, text: str) -> Path:
        held_at_report.append(_card_lease(root, run_id) is not None)
        return real_write(run_id, text)

    monkeypatch.setattr(detach, "write_report", spying_write)
    seen: list[tuple[str, str, str, int]] = []

    def engine(store, lease):
        row = _card_lease(root, pre.run_id)
        seen.append((store.run_id, lease.token, row.token, len(_alive_heartbeats())))
        return {"run_id": pre.run_id, "status": "done"}

    cli.run_detached_child(root=pre.root, run_id=pre.run_id, token=token, engine=engine)

    assert seen == [(pre.run_id, token, token, 1)]
    report = _report_path(pre.run_id)
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "ok": True,
        "data": {"run_id": pre.run_id, "status": "done"},
    }
    assert _mode(report) == 0o600
    assert held_at_report == [True]
    assert closes == [(0, 0)]
    assert _card_lease(root, pre.run_id) is None
    assert _claim_rows(root) == []
    assert _alive_heartbeats() == []


def test_the_detached_child_reports_a_handled_error_and_releases(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    pre, token = _handed_off_card_run(root, cards["subtask"])
    closes = _close_snapshots(monkeypatch)

    def engine(store, lease):
        raise cli.UnknownCardError("the card is gone")

    cli.run_detached_child(root=pre.root, run_id=pre.run_id, token=token, engine=engine)

    assert json.loads(_report_path(pre.run_id).read_text(encoding="utf-8")) == {
        "ok": False,
        "error": {"type": "UnknownCardError", "message": "the card is gone"},
    }
    assert closes == [(0, 0)]
    assert _card_lease(root, pre.run_id) is None
    assert _claim_rows(root) == []


def test_a_crashing_detached_child_writes_no_report_and_still_releases(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    pre, token = _handed_off_card_run(root, cards["subtask"])
    closes = _close_snapshots(monkeypatch)

    def engine(store, lease):
        raise RuntimeError("engine bug")

    with pytest.raises(RuntimeError, match="engine bug"):
        cli.run_detached_child(root=pre.root, run_id=pre.run_id, token=token, engine=engine)

    assert not _report_path(pre.run_id).exists()
    assert closes == [(0, 0)]
    assert _card_lease(root, pre.run_id) is None
    assert _claim_rows(root) == []
    assert _recorded_run_ids(root) == [pre.run_id]


def test_release_handed_off_releases_the_claims_and_lease_and_closes(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    pre, token = _handed_off_card_run(root, cards["subtask"])
    assert _card_lease(root, pre.run_id) is not None
    closes = _close_snapshots(monkeypatch)

    cli.release_handed_off(pre.root, pre.run_id, token)

    assert closes == [(0, 0)]
    assert _card_lease(root, pre.run_id) is None
    assert _claim_rows(root) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "detached_child or release_handed_off" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'run_detached_child'` / `'release_handed_off'`.

- [ ] **Step 3: Implement**

In `src/agent_manager/cli.py`, insert after the `HANDLED` docstring and before `def _check_run_targets`:

```python
# ── am run --detach (card aff9fdbf) ─────────────────────────────────────────


def release_handed_off(root: Path, run_id: str, token: str) -> None:
    """Release a handed-off lease's claims, then the lease, over a fresh store.

    For a detach that failed after `Lease.hand_off()`: the recorded stage's
    store is already closed and its lease no longer releases anything.
    """
    store = Store.open(root, run_id)
    try:
        try:
            store.release_claims(token)
        finally:
            store.release_lease(token)
    finally:
        store.close()


def run_detached_child(
    *,
    root: Path,
    run_id: str,
    token: str,
    engine: Callable[[Store, control.Lease], dict[str, Any]],
) -> None:
    """What the detached child of `am run --detach` runs: stage 3, then report.

    It opens its own store and adopts `token` (no new lease: the claims the
    parent took stay under it), so the heartbeat runs here. `engine` runs
    stage 3 on that store and lease. Its payload is written to `report.json`
    as `ok_envelope`, or a `HANDLED` error as `error_envelope`, both while
    the lease is still held. Leaving the lease releases the claims, then the
    lease, then the store closes, on every exit. Anything else propagates
    with no report: its traceback goes to `run.log`.
    """
    store = Store.open(root, run_id)
    try:
        with control.Lease(store, adopt=token) as lease:
            try:
                payload = engine(store, lease)
            except HANDLED as error:
                detach.write_report(run_id, render(error_envelope(error)))
                return
            detach.write_report(run_id, render(ok_envelope(payload)))
    finally:
        store.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "detached_child or release_handed_off" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): run_detached_child adopts the lease, reports, releases (card aff9fdbf)"
```

---

### Task 6: `hand_off_to_child`, `detach_card` and `am run --card --detach`

**Files:**
- Modify: `src/agent_manager/cli.py` (append after `run_detached_child`; the dispatch and exit-code block of `run`, lines 1471-1543)
- Test: `tests/test_cli.py` (append after the Task 5 tests)

**Interfaces:**
- Consumes: `preflight_card`, `recorded_card_run`, `RecordedRun`, `run_card_engine` (3.1 seam, read by global name at call time); `detach.create_run_log`, `detach.Detacher`, `detach.fork_detacher` (Task 1); `Store.set_lease_holder` (Task 2); `Lease.hand_off` (Task 3); `run_detached_child`, `release_handed_off` (Task 5).
- Produces: `hand_off_to_child(*, root: Path, run_id: str, token: str, log: Path, engine: Callable[[Store, control.Lease], dict[str, Any]], detacher: detach.Detacher) -> dict[str, Any]` returning `{"run_id", "pid", "log", "detached": True}`; `detach_card(card_id: str, *, repo_dir: Path, branch_prefix: str, detacher: detach.Detacher, base_branch: str = "master", allow_no_verification: bool = False, commands: Sequence[str] = (), runner_factory: RunnerFactory | None = None, clock: Callable[[], datetime] = _utcnow, control_interval: float = control.CONTROL_POLL_SECONDS) -> dict[str, Any]`. `run` reads `detach.fork_detacher` at call time.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def _card_run_args(root: Path, card_id: str, *extra: str) -> list[str]:
    return [
        "run",
        "--card",
        card_id,
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        "m1",
        "--allow-no-verification",
        *extra,
    ]


def _lease_pids(root: Path) -> list[int]:
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        return [row["pid"] for row in conn.execute("SELECT pid FROM run_leases ORDER BY run_id")]
    finally:
        conn.close()


def test_a_detached_card_run_prints_one_envelope_and_leaves_the_lease_to_the_child(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    monkeypatch.setattr(cli, "drive_subtask_async", _no_drive)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)
    closes = _close_snapshots(monkeypatch)
    pids_at_go: list[list[int]] = []
    fake.at_go = lambda: pids_at_go.append(_lease_pids(root))

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 1
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == {"run_id", "pid", "log", "detached"}
    assert (data["pid"], data["detached"]) == (FAKE_CHILD_PID, True)
    run_id = data["run_id"]
    assert [entry["id"] for entry in cli.runs_for(repo_dir=root)["runs"]] == [run_id]
    log = Path(data["log"])
    assert log == paths.data_dir() / "runs" / run_id / detach.RUN_LOG_NAME
    assert log.is_file() and _mode(log) == 0o600
    assert fake.calls == [log]
    assert fake.events == ["go"]
    assert pids_at_go == [[FAKE_CHILD_PID]]
    lease = _card_lease(root, run_id)
    assert lease is not None
    assert (lease.pid, lease.host) == (FAKE_CHILD_PID, socket.gethostname())
    assert _claim_rows(root) == [(control.card_claim(cards["subtask"]), run_id, lease.token)]
    assert _alive_heartbeats() == []
    # The recorded stage's store, then the pid update's: both closed, both still holding.
    assert closes == [(1, 1), (1, 1)]
    assert not (log.parent / detach.REPORT_NAME).exists()


def test_detach_honours_pretty(tmp_path, monkeypatch, fake_board):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    monkeypatch.setattr(detach, "fork_detacher", _FakeDetacher())

    result = runner.invoke(
        cli.app, _card_run_args(root, cards["subtask"], "--detach", "--pretty")
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert result.stdout == cli.render(envelope, pretty=True) + "\n"
    assert envelope["data"]["detached"] is True


def test_the_detached_card_child_reports_the_foreground_payload_then_releases(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)
    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.stdout)["data"]["run_id"]

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _done_drive(calls))
    captured: list[dict[str, Any]] = []
    real_engine = cli.run_card_engine

    async def spying_engine(pre, recorded, **kwargs):
        payload = await real_engine(pre, recorded, **kwargs)
        captured.append(payload)
        return payload

    monkeypatch.setattr(cli, "run_card_engine", spying_engine)
    monkeypatch.setattr(store_module.Store, "take_lease", _no_take_lease)
    closes = _close_snapshots(monkeypatch)

    fake.body()

    (payload,) = captured
    assert (payload["run_id"], payload["status"]) == (run_id, "done")
    assert [call["run_id"] for call in calls] == [run_id]
    report = _report_path(run_id)
    assert json.loads(report.read_text(encoding="utf-8")) == json.loads(
        cli.render(cli.ok_envelope(payload))
    )
    assert _mode(report) == 0o600
    assert closes == [(0, 0)]
    assert _card_lease(root, run_id) is None
    assert _claim_rows(root) == []


def test_a_parentless_card_is_refused_the_same_with_or_without_detach(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    loose = fake_board.add_card("A card with no story")
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    foreground = runner.invoke(cli.app, _card_run_args(root, loose))
    detached = runner.invoke(cli.app, _card_run_args(root, loose, "--detach"))

    assert (foreground.exit_code, detached.exit_code) == (cli.EXIT_ERROR, cli.EXIT_ERROR)
    assert json.loads(detached.stdout) == json.loads(foreground.stdout)
    assert json.loads(detached.stdout)["error"]["type"] == "ParentlessCardError"
    assert fake.calls == []
    assert _run_dirs() == []


def test_a_claimed_card_is_refused_the_same_with_or_without_detach(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    foreground = runner.invoke(cli.app, _card_run_args(root, cards["subtask"]))
    detached = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    def steady(stdout: str) -> dict[str, Any]:
        envelope = json.loads(stdout)
        envelope["error"]["message"] = re.sub(r"\d+s ago", "Ns ago", envelope["error"]["message"])
        return envelope

    assert (foreground.exit_code, detached.exit_code) == (cli.EXIT_ERROR, cli.EXIT_ERROR)
    assert steady(detached.stdout) == steady(foreground.stdout)
    assert json.loads(detached.stdout)["error"]["type"] == "ClaimedError"
    assert fake.calls == []
    assert _run_dirs() == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]


def test_a_failed_detach_releases_the_claim_and_lease_and_prints_no_detached_envelope(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    fake = _FakeDetacher(error=OSError("fork failed"))
    monkeypatch.setattr(detach, "fork_detacher", fake)
    closes = _close_snapshots(monkeypatch)

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    assert isinstance(result.exception, OSError)
    assert '"detached"' not in result.stdout
    assert len(fake.calls) == 1
    assert closes[-1] == (0, 0)
    assert _claim_rows(root) == []
    (run_id,) = _recorded_run_ids(root)
    assert _card_lease(root, run_id) is None


def test_a_run_log_that_cannot_be_created_releases_through_the_recorded_stage(
    tmp_path, monkeypatch, fake_board
):
    """Spec, Error paths: the failure is raised inside the recorded stage,
    before the hand-off, so 3.1's release-then-close covers it."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    def unwritable(run_id: str) -> Path:
        raise PermissionError("run.log: permission denied")

    monkeypatch.setattr(detach, "create_run_log", unwritable)
    closes = _close_snapshots(monkeypatch)

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    assert isinstance(result.exception, PermissionError)
    assert '"detached"' not in result.stdout
    assert fake.calls == []
    assert closes == [(0, 0)]
    assert _claim_rows(root) == []
    (run_id,) = _recorded_run_ids(root)
    assert _card_lease(root, run_id) is None


def test_a_failed_lease_pid_update_aborts_the_child_and_releases(
    tmp_path, monkeypatch, fake_board
):
    """Review Focus 1: the child is told to abort and never runs its body."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    def locked(self, token: str, *, pid: int, host: str) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store_module.Store, "set_lease_holder", locked)

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    assert isinstance(result.exception, sqlite3.OperationalError)
    assert '"detached"' not in result.stdout
    assert fake.events == ["abort"]
    assert _claim_rows(root) == []
    (run_id,) = _recorded_run_ids(root)
    assert _card_lease(root, run_id) is None


def test_a_card_run_without_detach_still_prints_its_full_payload(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _done_drive(calls))
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"]))

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["status"] == "done"
    assert "detached" not in data
    assert fake.calls == []
    assert _claim_rows(root) == []
    assert not (paths.data_dir() / "runs" / data["run_id"] / detach.RUN_LOG_NAME).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "detached_card or detach_honours or failed_detach or failed_lease_pid or run_log_that_cannot or refused_the_same or without_detach" -v`
Expected: the success, pretty, child, failed-detach and failed-pid-update tests FAIL, because `--detach` still runs the foreground path (`fake.calls == []`, and the envelope has no `detached`). The two "refused the same" tests and the without-detach test may already PASS: they pin behaviour that must not change.

- [ ] **Step 3: Implement**

In `src/agent_manager/cli.py`, append after `run_detached_child`:

```python
def hand_off_to_child(
    *,
    root: Path,
    run_id: str,
    token: str,
    log: Path,
    engine: Callable[[Store, control.Lease], dict[str, Any]],
    detacher: detach.Detacher,
) -> dict[str, Any]:
    """Start the detached child, point the lease at it, let it go, and report.

    Called with the lease already handed off and the recorded stage's store
    closed, so no connection and no heartbeat thread crosses the fork. The
    child blocks until `go`. The lease row is re-pointed at the child's pid
    over a fresh store while this process is still alive, so the row never
    names a dead pid, and only then is the child let go. A failed spawn or
    pid update releases the claims and lease (after telling a spawned child
    to abort) and propagates.
    """

    def body() -> None:
        run_detached_child(root=root, run_id=run_id, token=token, engine=engine)

    try:
        spawned = detacher(body, log)
    except BaseException:
        release_handed_off(root, run_id, token)
        raise
    try:
        store = Store.open(root, run_id)
        try:
            store.set_lease_holder(token, pid=spawned.pid, host=socket.gethostname())
        finally:
            store.close()
    except BaseException:
        spawned.abort()
        release_handed_off(root, run_id, token)
        raise
    spawned.go()
    return {"run_id": run_id, "pid": spawned.pid, "log": str(log), "detached": True}


def detach_card(
    card_id: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    detacher: detach.Detacher,
    base_branch: str = "master",
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """`am run --card --detach`: stages 1 and 2 here, stage 3 in a detached child.

    `preflight_card` and `recorded_card_run` run exactly as for `run_card`, so
    every refusal is the same. Inside the recorded stage `run.log` is created
    (a failure there releases as any crash does) and the lease is handed
    off, so the stage exits releasing nothing and closes its store. The child
    runs `run_card_engine` on this very `pre` and run id (`hand_off_to_child`).
    """
    pre = preflight_card(
        card_id,
        repo_dir=repo_dir,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        clock=clock,
    )
    with recorded_card_run(pre) as recorded:
        log = detach.create_run_log(pre.run_id)
        token = recorded.lease.hand_off()

    def engine(store: Store, lease: control.Lease) -> dict[str, Any]:
        return asyncio.run(
            run_card_engine(
                pre,
                RecordedRun(run_id=pre.run_id, store=store, lease=lease),
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                control_interval=control_interval,
            )
        )

    return hand_off_to_child(
        root=pre.root, run_id=pre.run_id, token=token, log=log, engine=engine, detacher=detacher
    )
```

In `run`, replace the final `else:` branch of the dispatch (the one calling `run_card`) with:

```python
        elif detach_run:
            # Read as `detach.fork_detacher` so a test can patch it there.
            payload = detach_card(
                card,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                allow_no_verification=allow_no_verification,
                commands=list(verify),
                detacher=detach.fork_detacher,
            )
        else:
            payload = run_card(
                card,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                allow_no_verification=allow_no_verification,
                commands=list(verify),
            )
```

and directly after `typer.echo(render(ok_envelope(payload), pretty=pretty))` insert:

```python
    if detach_run:
        # A handed-off run's outcome is in its report.json, not this exit code.
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v -k "detach or seam or recorded_card_run or preflight_card or run_card"`
Expected: PASS, the 3.1 seam tests included.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): am run --card --detach hands the engine to a forked child (card aff9fdbf)"
```

---

### Task 7: `orchestrate.detach_milestone` and `am run --milestone --detach`

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (import list at lines 61-72; new function after `_run_milestone_async`, which ends at line 2128)
- Modify: `src/agent_manager/cli.py` (the `elif milestone is not None:` branch of `run`)
- Test: `tests/test_orchestrate.py` (imports at lines 22-54; append after `test_run_milestone_hands_the_engine_the_lease_of_the_recorded_stage`, line 7493)

**Interfaces:**
- Consumes: `preflight_milestone`, `recorded_milestone_run`, `RecordedMilestoneRun`, `run_milestone_engine` (3.1 seam, read by global name at call time); `detach.create_run_log`, `detach.Detacher`; `cli.hand_off_to_child` (Task 6).
- Produces: `orchestrate.detach_milestone(milestone: str, *, repo_dir: Path, base_branch: str, branch_prefix: str, detacher: detach.Detacher, commands: Sequence[str] = (), allow_no_verification: bool = False, max_concurrent: int = 1, runner_factory: runs.RunnerFactory | None = None, driver: Driver | None = None, clock: Callable[[], datetime] = _utcnow, control_interval: float = control.CONTROL_POLL_SECONDS) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, add `import stat` to the stdlib imports (after `import socket`), add `from typer.testing import CliRunner` after `import pytest`, and change line 46 to include `detach`:

```python
from agent_manager import bases, board, census, cli, comments, control, dag, detach, integration, locks, models, orchestrate, paths, runs
```

Append after `test_run_milestone_hands_the_engine_the_lease_of_the_recorded_stage`:

```python
# ── am run --milestone --detach (card aff9fdbf) ─────────────────────────────
#
# Unit tier, like the seam tests above: FakeBoard, a plain repo dir,
# `refresh_git` patched, `integrate_recorder` autouse, and `_FakeDetacher`
# instead of `detach.fork_detacher`. Nothing forks.

FAKE_CHILD_PID = 424242
"""The pid `_FakeDetacher` reports; no such child exists."""

detach_runner = CliRunner()


class _FakeDetacher:
    """Stands in for `detach.fork_detacher`: records the call, starts nothing."""

    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.events: list[str] = []
        self.body: Any = None

    def __call__(self, body: Any, log: Path) -> detach.Spawned:
        self.calls.append(log)
        self.body = body
        return detach.Spawned(
            pid=FAKE_CHILD_PID,
            go=lambda: self.events.append("go"),
            abort=lambda: self.events.append("abort"),
        )


def _milestone_run_args(root: Path, needle: str, *extra: str) -> list[str]:
    return [
        "run",
        "--milestone",
        needle,
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        PREFIX,
        "--allow-no-verification",
        *extra,
    ]


def _no_take_lease(self, **kwargs: Any) -> Any:
    pytest.fail("the detached child took a new lease instead of adopting its own")


def test_an_ambiguous_milestone_is_refused_the_same_with_or_without_detach(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    _add_card(root, "Milestone 3: orchestration")
    _add_card(root, "Milestone 3: integration")
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    foreground = detach_runner.invoke(cli.app, _milestone_run_args(root, "Milestone 3"))
    detached = detach_runner.invoke(
        cli.app, _milestone_run_args(root, "Milestone 3", "--detach")
    )

    assert (foreground.exit_code, detached.exit_code) == (cli.EXIT_ERROR, cli.EXIT_ERROR)
    assert json.loads(detached.stdout) == json.loads(foreground.stdout)
    assert json.loads(detached.stdout)["error"]["type"] == "MilestoneNotFoundError"
    assert fake.calls == []
    assert _run_dirs() == []


def test_a_blocker_cycle_is_refused_the_same_with_or_without_detach(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = _add_card(root, "Milestone 3: orchestration")
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    monkeypatch.setattr(
        census,
        "flatten_milestone",
        lambda node: census.Census(milestone_title=node.title, stories=[a, b]),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    foreground = detach_runner.invoke(cli.app, _milestone_run_args(root, milestone))
    detached = detach_runner.invoke(cli.app, _milestone_run_args(root, milestone, "--detach"))

    assert (foreground.exit_code, detached.exit_code) == (cli.EXIT_ERROR, cli.EXIT_ERROR)
    assert json.loads(detached.stdout) == json.loads(foreground.stdout)
    assert json.loads(detached.stdout)["error"]["type"] == "DependencyCycleError"
    assert fake.calls == []
    assert _run_dirs() == []


def test_a_detached_milestone_run_records_and_leases_the_plan_and_drives_nothing_here(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    story = shape["stories"]["A"]
    refreshed: list[Path] = []
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: refreshed.append(at))
    monkeypatch.setattr(cli, "drive_subtask_async", _forbidden("drive_subtask_async"))
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = detach_runner.invoke(
        cli.app, _milestone_run_args(root, shape["milestone"], "--detach")
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert set(data) == {"run_id", "pid", "log", "detached"}
    assert (data["pid"], data["detached"]) == (FAKE_CHILD_PID, True)
    run_id = data["run_id"]
    assert _run_ids(root) == [run_id]
    assert _statuses(_load(root, run_id)) == {
        "run": "started",
        story: "pending",
        a1: "pending",
        a2: "pending",
    }
    lease = _lease(root, run_id)
    assert lease is not None and lease.pid == FAKE_CHILD_PID
    assert _held_keys(root, run_id) == _expected_claims(shape["milestone"], [a1, a2])
    log = Path(data["log"])
    assert log == paths.run_dir(run_id) / detach.RUN_LOG_NAME
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600
    assert fake.calls == [log]
    assert fake.events == ["go"]
    assert refreshed == [root]


def test_the_detached_milestone_child_drives_the_recorded_plan_and_reports_it(
    tmp_path, monkeypatch, fake_board, integrate_recorder
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    story = shape["stories"]["A"]
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    fake = _FakeDetacher()
    driver = FakeDriver()

    data = orchestrate.detach_milestone(
        shape["milestone"],
        repo_dir=root,
        base_branch="main",
        branch_prefix=PREFIX,
        detacher=fake,
        allow_no_verification=True,
        driver=driver,
        clock=lambda: STARTED_AT,
        control_interval=0.01,
    )

    run_id = data["run_id"]
    assert run_id == runs.mint_run_id(shape["milestone"], STARTED_AT)
    assert driver.calls == []
    monkeypatch.setattr(store_module.Store, "take_lease", _no_take_lease)
    closes = _close_snapshots(monkeypatch)

    fake.body()

    report = paths.run_dir(run_id) / detach.REPORT_NAME
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    expected = {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": [story]}],
        "completed": [a1],
        "tips": [{"story": story, "tip": _branch(root, a1)}],
        "warnings": [],
        "integrated": _integrated(root, [story]),
    }
    assert json.loads(report.read_text(encoding="utf-8")) == json.loads(
        cli.render(cli.ok_envelope(expected))
    )
    assert [call["card"] for call in driver.calls] == [a1]
    assert [call["run_id"] for call in integrate_recorder.calls] == [run_id]
    assert _load(root, run_id).status == "done"
    assert closes == [(0, 0)]
    assert _lease(root, run_id) is None
    assert _claim_rows(root) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "detach" -v`
Expected: `test_the_detached_milestone_child_drives_the_recorded_plan_and_reports_it` FAILS with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'detach_milestone'`. `test_a_detached_milestone_run_records_and_leases_the_plan_and_drives_nothing_here` FAILS because `--milestone --detach` still runs the foreground path (`_forbidden("drive_subtask_async")` fires). The two refusal tests may already PASS: they pin behaviour that must not change.

- [ ] **Step 3: Implement**

In `src/agent_manager/orchestrate.py`, add `detach,` to the `from agent_manager import (...)` list between `dag,` and `integration,`. Insert after `_run_milestone_async` (before the `# ── the board run` banner):

```python
def detach_milestone(
    milestone: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    detacher: detach.Detacher,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    max_concurrent: int = 1,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """`am run --milestone --detach` (card aff9fdbf): stages 1 and 2 here, stage 3 in a child.

    A fresh run only. `preflight_milestone` and `recorded_milestone_run` run
    exactly as for `run_milestone`, so every refusal, `refresh_git` and the
    `pending` plan are the same. Inside the recorded stage `run.log` is
    created and the lease handed off, so the stage exits releasing nothing
    and closes its store. The child runs `run_milestone_engine` on this very
    `pre`, with the plan rows and checkpoints the recorded stage wrote
    (`cli.hand_off_to_child`).
    """
    pre = preflight_milestone(
        milestone,
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        max_concurrent=max_concurrent,
        clock=clock,
        driver=driver,
    )
    with recorded_milestone_run(pre) as recorded:
        log = detach.create_run_log(pre.run_id)
        rows, checkpoints = recorded.rows, recorded.checkpoints
        token = recorded.lease.hand_off()

    def engine(store: Store, lease: control.Lease) -> dict[str, Any]:
        handed = RecordedMilestoneRun(
            run_id=pre.run_id, store=store, lease=lease, rows=rows, checkpoints=checkpoints
        )
        return asyncio.run(
            run_milestone_engine(
                pre,
                handed,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                control_interval=control_interval,
            )
        )

    return cli.hand_off_to_child(
        root=pre.root, run_id=pre.run_id, token=token, log=log, engine=engine, detacher=detacher
    )
```

In `src/agent_manager/cli.py` `run`, insert before `elif milestone is not None:` (the foreground branch that calls `orchestrate.run_milestone`):

```python
        elif milestone is not None and detach_run:
            # Read as `orchestrate.detach_milestone` and `detach.fork_detacher`
            # so a test can patch either.
            payload = orchestrate.detach_milestone(
                milestone,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
                detacher=detach.fork_detacher,
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "detach or seam or recorded_milestone_run or preflight_milestone or hands_the_engine" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py src/agent_manager/cli.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): am run --milestone --detach via detach_milestone (card aff9fdbf)"
```

---

### Task 8: README and `RUN_EXAMPLES`

**Files:**
- Modify: `src/agent_manager/cli.py` (`RUN_EXAMPLES`, line 1373)
- Modify: `README.md` (refusals paragraph at lines 80-84; new subsection inserted before `#### Preview with \`--dry-run\``, line 86)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: the `--detach` option (Task 4).
- Produces: documentation only.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_run_help_and_examples_document_detach():
    assert "--detach" in cli.RUN_EXAMPLES

    result = runner.invoke(cli.app, ["run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--detach" in result.output
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_run_help_and_examples_document_detach -v`
Expected: FAIL on `assert "--detach" in cli.RUN_EXAMPLES`.

- [ ] **Step 3: Implement**

Replace `RUN_EXAMPLES` in `src/agent_manager/cli.py` with:

```python
RUN_EXAMPLES = """\
Examples:
  am run --milestone "M9" --branch-prefix m9 --dry-run --pretty       # preview the plan
  am run --milestone "M9" --branch-prefix m9 --verify "uv run pytest"  # run it
  am run --milestone "M9" --branch-prefix m9 --verify "uv run pytest" --detach  # run it in the background
  am run --board --verify "uv run pytest"                             # run every open milestone
  am status <run-id> --pretty                                         # watch it (another terminal)
  am resume <run-id> --verify "uv run pytest"                         # after a fix, stop or crash
"""
```

In `README.md`, replace the refusals paragraph (lines 80-84) with:

```markdown
Some combinations are refused before anything is read: `--card` together with
`--milestone`, neither of them, a blank `--milestone`, `--dry-run` with
`--card`, `--max-concurrent` with `--card`, a `--max-concurrent` below 1, and
`--detach` with `--dry-run` or with `--board`.
These are usage errors, like a missing `--branch-prefix`: Typer prints the
message on stderr, nothing is printed on stdout, and the exit code is 2.
```

Insert this subsection directly before `#### Preview with \`--dry-run\``:

````markdown
#### Running detached with `--detach`

```bash
am run --milestone "document milestone runs" --branch-prefix m3 --verify "uv run pytest" --detach
```

`--detach` works with `--card` and `--milestone`. The command first does everything a foreground run does before its first subtask: it reads the board, makes every check, records the run and takes its lease. A refusal at that point comes back as the usual `{"ok": false, ...}` envelope with exit code 3, and nothing starts. Then the run moves to a background process in its own session, and the command prints one envelope and exits 0:

```json
{"ok":true,"data":{"detached":true,"log":"/home/me/.local/share/agent-manager/runs/20261004T090000Z-1a2b3c4d/run.log","pid":48213,"run_id":"20261004T090000Z-1a2b3c4d"}}
```

- `run_id` is the id `am runs`, `am status`, `am watch`, `am pause` and `am resume` take. `pid` is the background process. It holds the run's lease, and `am runs` and `am status` show it as the lease's `pid`.
- The background process writes its output to `run.log`. When the run ends it writes `report.json`. Both files are in `<data dir>/runs/<run-id>/` (`$XDG_DATA_HOME/agent-manager`, or `~/.local/share/agent-manager`) and both are mode 0600. `report.json` holds the envelope the same run would have printed in the foreground: `{"ok": true, "data": ...}` for a run that finished, escalated, stopped or was cancelled, or `{"ok": false, "error": ...}` for a run the tool could not carry on. It is written in one step, so it is either absent or complete.
- A crash writes no `report.json`. Its traceback is in `run.log`, the lease and claims are released, and the run can be resumed like any crashed run.
- The exit code is 0 whenever the run was handed off, even if it later escalates. Read the outcome from `report.json` or `am status`.
- A missing verification command is not caught before the run starts. The verification gate runs during the explore phase, so with `--detach` it shows up in `report.json` and `am status`, not on your terminal. Pass `--verify` or `--allow-no-verification`.
- `--detach` with `--dry-run` or with `--board` is refused as a usage error (exit 2).
- Later versions may add keys to these envelopes. Ignore keys you do not know.
````

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py -k "help_and_examples or RUN_EXAMPLES or run_help" -v`
Expected: PASS, including the existing `am run --board` examples test at line 3872.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py README.md tests/test_cli.py
git commit -m "docs: document am run --detach, run.log and report.json (card aff9fdbf)"
```

---

### Task 9: e2e_fake: a real detached milestone run

**Files:**
- Create: `tests/e2e/test_detached_run.py`

**Interfaces:**
- Consumes: e2e fixtures from `tests/e2e/conftest.py` (`milestone_board` → `{"root", "milestone", "stories", "subtasks", "branches"}`, `fake_claude_bin`, `hold` with `arm()`, `release(*ids)`, `held_marker(id)`, `am(*args) -> (code, envelope)`); `detach.RUN_LOG_NAME`, `detach.REPORT_NAME`; `control.pid_alive`; `store.open_db`, `store.read_lease`; `paths.data_dir`. The prefix and verify command are restated locally, as `tests/e2e/test_multi_process.py` does: a bare `from conftest import` there resolves to the root `tests/conftest.py`.
- Produces: the one real-spawn test for 3.2. It spawns `am` (and through it git, brd and the fake claude), so it is `@pytest.mark.e2e_fake` and does not run under `uv run pytest`.

- [ ] **Step 1: Write the test**

Create `tests/e2e/test_detached_run.py`:

```python
"""e2e_fake: one real `am run --milestone ... --detach` (card aff9fdbf).

Production wiring under the fake `claude`, with real git and brd. The `am`
parent is a real child process, and the engine runs in the grandchild it
forks into its own session. The fake's hold parks a1's `implement`, which
keeps the detached run live while the test looks at it after the parent has
exited. Order comes from marker files and the lease row, never from sleeps.
Watch, pause and resume are sibling 3.3's scenario, not this one.
"""

import json
import os
import signal
import stat
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_manager import cli, control, detach, paths, store

PREFIX = "m3"
"""The `--branch-prefix` of this scenario; equals the e2e conftest's `MILESTONE_PREFIX`."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the e2e conftest's `VERIFY_COMMANDS[0]`, as in test_multi_process.py."""

DEADLINE = 240.0
"""Only bounds a broken run; a healthy one never waits this long."""

POLL = 0.05


def _until(predicate: Callable[[], bool], what: str, timeout: float = DEADLINE) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"{what} did not happen within {timeout}s")
        time.sleep(POLL)


def _lease(root: Path, run_id: str) -> store.LeaseRow | None:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return store.read_lease(conn, run_id)
    finally:
        conn.close()


@pytest.fixture
def detached_pids():
    """Every detached child the test started; its whole session is killed at teardown."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.e2e_fake
def test_a_detached_milestone_run_outlives_its_parent_and_leaves_its_report(
    milestone_board, fake_claude_bin, hold, am, detached_pids
):
    root = milestone_board["root"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)  # only a1 is held; the rest pass straight through

    code, envelope = am(
        "run",
        "--milestone",
        milestone_board["milestone"],
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        PREFIX,
        "--verify",
        VERIFY,
        "--detach",
    )

    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    data = envelope["data"]
    assert set(data) == {"run_id", "pid", "log", "detached"}
    assert data["detached"] is True
    pid, run_id = data["pid"], data["run_id"]
    detached_pids.append(pid)
    run_dir = paths.data_dir() / "runs" / run_id
    assert Path(data["log"]) == run_dir / detach.RUN_LOG_NAME
    assert stat.S_IMODE(os.stat(data["log"]).st_mode) == 0o600

    # The parent has exited (am() waited for it); the child is still working.
    _until(lambda: hold.held_marker(a1).exists(), "a1's implement being held")
    assert control.pid_alive(pid)
    assert os.getsid(pid) == pid

    code, listing = am("runs", "--repo-dir", str(root))
    assert code == 0, listing
    (row,) = listing["data"]["runs"]
    assert row["id"] == run_id
    assert row["lease"]["pid"] == pid
    assert row["lease"]["live"] is True

    hold.release(a1)
    report = run_dir / detach.REPORT_NAME
    _until(report.exists, "report.json appearing")
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    final = json.loads(report.read_text(encoding="utf-8"))
    assert final["ok"] is True, final
    assert final["data"]["done"] is True, final
    assert final["data"]["run_id"] == run_id
    _until(lambda: _lease(root, run_id) is None, "the child releasing its lease")
```

- [ ] **Step 2: Run it**

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_run.py -v`
Expected: PASS (or SKIP with "the git/brd CLI must be installed" when `brd` is absent). Tasks 1-8 already built the behaviour, so there is no RED step here. If it fails, read `run.log` under the printed run directory before changing code.

- [ ] **Step 3: Confirm the default suite does not collect it**

Run: `uv run pytest tests/e2e/test_detached_run.py -v`
Expected: `1 deselected`. The `addopts` `-m` expression leaves out `e2e_fake`.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_detached_run.py
git commit -m "test(e2e_fake): a detached milestone run outlives its parent and reports (card aff9fdbf)"
```

---

### Task 10: full verification

- [ ] **Step 1: Default suite**

Run: `uv run pytest`
Expected: all green, and no unit-tier budget violation reported by `tests/conftest.py` (each new unmarked test ≤0.5s, none spawning a subprocess).

- [ ] **Step 2: The opt-in tier this card touches**

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_run.py`
Expected: 1 passed.

- [ ] **Step 3: Confirm the foreground path is untouched**

Run: `git diff -U0 ami/task-3-1-split-run-pre-5daa944e -- src/agent_manager/cli.py src/agent_manager/orchestrate.py | grep -n "^[-+].*def \(run_card\|recorded_card_run\|preflight_card\|run_card_engine\|run_milestone\|recorded_milestone_run\|preflight_milestone\|run_milestone_engine\|_run_milestone_async\)("`
Expected: no output. No 3.1 seam signature changed. Then read the diff of those function bodies (`git diff ami/task-3-1-split-run-pre-5daa944e -- src/agent_manager/cli.py src/agent_manager/orchestrate.py`) and confirm that every hunk is an addition outside them.

- [ ] **Step 4: Commit any fixes**

If Steps 1-3 needed fixes, commit them with a message naming what failed. Otherwise there is nothing to commit.
