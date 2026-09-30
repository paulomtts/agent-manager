<!-- task-pipeline: validated -->
# Subtask d147d97a — Prove pause, resume and cancel under the fake claude

Parent story: 49e7aa15 "Prove and document live control" (milestone bdc5838b). Narrows Task 3.1 of `docs/superpowers/plans/2026-09-27-live-control.md` and §7 "End to end" of `docs/superpowers/specs/2026-09-27-live-control-design.md`. Both documents live on branch `docs/live-control` (worktree `.claude/worktrees/docs-live-control`, commit 7cf1263), not on `master`.

## Base branch

The code under test (`control.py`, `StopSignal.request`/`.requested`, `am pause`/`am cancel`, `request_control`, the `run_milestone` Lease + `controlled` wiring, `controlled_payload`, and the cancelled/live refusals in `resume_run`/`resumable_milestone_run`) is not on `master`. It is on `m9/task-honour-pause-and-cancel-9f5467e3`, which sits on top of the earlier m9 task branches. This subtask's branch must be based on that tip, or on wherever the workflow stacks m9 work. The implementer checks this before writing anything. Read the code as built first. If a name differs from the spec (for example `latest_run_id`, the `effective` key or the `paused`/`cancelled` payload keys), follow the code and say so in the commit or PR notes.

## Scope

- Create one new file, `tests/e2e/test_live_control.py`, containing exactly the two tests below. No production code changes are expected.
- Reuse the board fixtures and fake-`claude` wiring in `tests/e2e/conftest.py` without modifying them, unless a change is strictly necessary.
- Follow the scaffolding pattern in `tests/e2e/test_milestone_resume.py` (`_kill_with_c1_in_plan_and_d3_in_implement`): use `monkeypatch.setattr(cli, "run_direct", ...)`, look up the real `run_direct` at call time, and block on a `threading.Event`. The difference is that this test *holds* the launch instead of killing it. A local `_hold(monkeypatch, card, phase, entered, release)` helper wraps `cli.run_direct`. When the named card's `plan` launch arrives, it sets `entered`, waits on `release`, and then calls the real `run_direct`. The hold is one-shot: it fires once only and passes every other call straight through, so nothing stays armed for `am resume` or the relaunch.
- Out of scope: any unit or integration coverage already owned by the sibling stories (`tests/test_store.py`, `tests/runtime/test_stop.py`, `tests/test_control.py`, `tests/test_orchestrate.py`, `tests/test_cli.py`). That includes the §6 refusal table, lease liveness, `--card` pause/cancel, and `am status`'s `control` key. Documentation is out of scope too; it belongs to sibling Task 3.2.

## Observable behaviour to prove

1. **Pause then resume** (`test_a_paused_milestone_resumes_with_nothing_dispatched_twice`):
   - A worker thread runs `am run --milestone <ms>` through `CliRunner`.
   - The test waits on `entered` with a bounded wait, then gets the run id from a second `store.open_db(project)` connection. That second connection is the "other process"; it is also `am pause`'s real path.
   - It runs `am pause <run_id> --repo-dir <project>` from the test thread. This must exit 0 with `data.effective == "pause"`.
   - It sets `release` and joins the worker. The run must exit 0 with `data.paused is True`, and `am status <run_id> --repo-dir <project>` must report `data["run"]["status"] == "stopped"` (the run's own status sits under the `run` key, not at the payload's top level; see `cli.status_payload`).
   - It records the fake-invocation counts, then runs `am resume <run_id> --repo-dir <project> --verify <VERIFY>`. This must exit 0 and its data must contain `integrated`.
   - Comparing the counts before and after resume must show no phase dispatched twice. In particular, the held card's `plan` completes exactly once.
2. **Cancel then relaunch** (`test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch`):
   - Set up the same hold, but run `am cancel <run_id>` instead. This must exit 0.
   - After release, the run must exit 0 with `data.cancelled is True`, with no `resume`, `escalated`, `failed_phase` or `integrated` key. `am status <run_id> --repo-dir <project>` must report `data["run"]["status"] == "cancelled"`.
   - `am resume <run_id>` must fail with exit 3 and the `{"ok": false, "error": {...}}` envelope (`NotResumableError`).
   - A fresh `am run --milestone <ms>` must dispatch the held card again from `explore`, as shown by the fake's invocation record. The cancelled run's parked checkpoint must not be continued.

## Error paths covered

- `am resume` of a cancelled run gives exit 3 (C9).
- A control never produces `escalated` or a `failed_phase`, and a paused or cancelled invocation never runs Integrate (C6). The tests assert this on the controlled payloads.

## Constraints (from the story, verbatim intent)

- No sleeps to prove ordering. Use `threading.Event` with bounded waits, and always set `release` in a `finally` so a failed assertion cannot hang the thread.
- The only control channel is the per-project SQLite. Do not add sockets, signals, or runtime dependencies.
- The CLI envelope stays unchanged.
- Run `uv run pytest` and get the whole suite green, `tests/e2e` included. Any bug the tests expose is first reproduced by its own failing test (in the appropriate existing test module) and then fixed in this task.
- Make one commit: `test(e2e): pause, resume and cancel a milestone under the fake claude`. Use the `m9` branch prefix. Push nothing, and never move the base branch.

## Test list and tier

The tier follows the repo's placement rule. `pyproject.toml` `addopts` has `-m "not e2e"`. Only tests that drive a real `claude` subprocess (`tests/e2e/test_real_harness*.py`) are marked `@pytest.mark.e2e`. Fake-`claude` end-to-end tests go in `tests/e2e/` unmarked and run in the default suite.

| Test | File | Tier |
|---|---|---|
| `test_a_paused_milestone_resumes_with_nothing_dispatched_twice` | `tests/e2e/test_live_control.py` | e2e directory, default suite, **unmarked** (fake `claude`) |
| `test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch` | `tests/e2e/test_live_control.py` | e2e directory, default suite, **unmarked** (fake `claude`) |
| (only if a bug is found) a regression test for that bug | the existing unit/integration module that owns the faulty code (`tests/test_control.py`, `tests/test_orchestrate.py`, `tests/test_cli.py`, `tests/test_store.py`, or `tests/runtime/test_stop.py`) | default suite, unmarked |

No `pytestmark = pytest.mark.e2e` in the new file.

---

# Prove pause, resume and cancel under the fake claude: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tests/e2e/test_live_control.py`, whose two default-suite tests drive `am run --milestone`, `am pause`/`am cancel`, `am status`, `am resume` and a relaunch through `CliRunner` against the fake `claude`. They prove that a paused milestone resumes with no phase dispatched twice, and that a cancelled one refuses `am resume` and relaunches from `explore`.

**Architecture:** The tests are test-only scaffolding on top of code that already exists on the base branch. A one-shot `_hold` wraps `cli.run_direct` and blocks the named card's `plan` launch on a `threading.Event`. A pass-through wrapper on `control.apply_pending` sets a second `threading.Event` once the running process has applied the request. The milestone runs in a worker thread. The test thread reads the run id on a second `store.open_db` connection, runs `am pause`/`am cancel`, waits until the request has been applied, then releases the hold. Nothing sleeps.

**Tech Stack:** Python 3, pytest, `typer.testing.CliRunner`, `threading`, SQLite (via `agent_manager.store`), the fake `claude` from `tests/e2e/fake_claude.py`.

**Spec:** `docs/superpowers/specs/task-prove-pause-resume-and-d147d97a-design.md` (prepended above). Upstream: `docs/superpowers/specs/2026-09-27-live-control-design.md` §7 and `docs/superpowers/plans/2026-09-27-live-control.md` Task 3.1. Both are on branch `docs/live-control` (worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/docs-live-control`).

**Worktree:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-prove-pause-resume-and-d147d97a`, branch `m9/task-prove-pause-resume-and-d147d97a`, cut from `m9/task-honour-pause-and-cancel-9f5467e3`. Every path below is relative to it, and every command runs from it.

## Global Constraints

- No new runtime dependency; no socket/fifo/signal handler — the only channel is the per-project SQLite (C1).
- The park is M7's: `StopSignal` + `ON_PAUSE` + `Parked` — no second stop path, never cancel a running phase to honour a control.
- A control never produces `escalated` or a `failed_phase`; a paused or cancelled run never runs Integrate in that invocation (C6).
- CLI envelope unchanged: `{"ok": true, "data": ...}` / `{"ok": false, "error": {"type","message"}}` at exit 3, `--pretty`.
- No test sleeps to prove ordering: block fakes on `asyncio.Event`/`asyncio.Barrier`/`threading.Event`; a "second process" = a second `store.open_db` connection.
- New file is unmarked: no `pytestmark = pytest.mark.e2e`, no `@pytest.mark.e2e`.
- `tests/e2e/conftest.py` is reused unmodified unless strictly necessary.
- No production code change is expected. A bug found gets a failing regression test first, in the module that owns the faulty code (default suite, unmarked), then the fix.
- Verification: `uv run pytest`, whole suite green, `tests/e2e` included.
- Exactly one commit: `test(e2e): pause, resume and cancel a milestone under the fake claude`. Nothing pushed; the base branch never moves.

## Code as built (read before writing; names the plan relies on)

These were read on the base branch. Where they differ from the upstream live-control spec, the plan follows the code:

- `cli.run_direct` is a module global in `src/agent_manager/cli.py` (`from agent_manager.harness.launcher import run_direct`, line 44). `cli.default_runner_factory` reads it at call time, so `monkeypatch.setattr(cli, "run_direct", ...)` intercepts every launch. The signature is `run_direct(argv, *, cwd, timeout, stdout_path, on_spawn=None)`.
- `control.watch` (in `src/agent_manager/control.py`) calls the module global `apply_pending(store, stop, token, clock=clock)` on every tick (default `CONTROL_POLL_SECONDS = 1.0`). It returns the list of applied `ControlRow`s. `controlled` calls it once more in its `finally`. Patching `control.apply_pending` therefore sees every application.
- `cli.request_control` returns `{"run_id", "command", "effective", "requested_at", "already_requested", "message"}`.
- `orchestrate.controlled_payload` returns `paused: True` or `cancelled: True`, plus `run_id`, `stopped`, `completed`, `pending` and `warnings`. A pause also adds `resume: "am resume <run_id>"`. The payload never has an `escalated` key.
- `orchestrate.run_milestone` records `stopped` for a pause and `cancelled` for a cancel, and returns before `integration.integrate_milestone` (C6).
- `cli.status_payload` puts the run's status at `data["run"]["status"]` (`RUN_IDENTITY` includes `"status"`).
- `cli.resume_run` raises `NotResumableError("run <id> was cancelled; start new work with `am run --milestone`")` before anything is written. Exit code `cli.EXIT_ERROR == 3`.
- `Store.latest_open_checkpoint` (used by `cli.continuable_checkpoint` on a relaunch) ignores checkpoints of `cancelled` runs, so a relaunch starts the held card at `explore`.
- `store.latest_run_id(conn)` and `store.open_db(root)` exist as used in `tests/e2e/test_milestone_resume.py`.
- The fake logs one JSON line per launch to `paths.run_dir(run_id) / "fake-claude.log"`, with `phase` and `result_path` = `<run dir>/<card>/<phase>.<n>/result.json`. The conftest's `read_fake_log(run_id)` reads it.
- `two_story_board` (conftest) is a milestone with two independent stories, A (a1) and B (b1), with the union merge attribute set so Integrate folds them cleanly.

**Deviation from the spec, stated:** the spec says to "set `release` and join" right after `am pause` exits 0. As built, the running process applies requests on a 1-second poll. If the test released straight after `am pause`, the held card could run to its end before the request was applied, and then the cancel test's "relaunch restarts the held card at `explore`" would be a race. The plan therefore also waits on an `applied` `threading.Event`, which a pass-through wrapper on `control.apply_pending` sets once the request is applied. That is still an event, not a sleep, so it stays inside the "no sleeps" constraint. The implementer mentions this in the commit body.

**Deviation from the conftest, stated:** none. `run_milestone_cli`, `read_fake_log` and `two_story_board` are used as they are.

## File Structure

- Create: `tests/e2e/test_live_control.py`. This holds the two tests, the local helpers (`_hold`, `_signal_when_applied`, `_control_while_held`, `_in_background`, envelope/status/count helpers) and the module constants. The helpers are copied from the patterns in `tests/e2e/test_milestone_resume.py` rather than imported from it, because sibling e2e modules each keep their own helpers (compare `_git`/`_envelope` in `test_milestone_run.py` and `test_milestone_resume.py`).
- Modify: nothing else, unless Task 3's bug path triggers.

## Review Focus

1. **A control that lands while a phase is mid-launch.** The held `plan` must finish and be recorded once, and the card must park before `validate_plan`, never cancelled mid-phase. Pinned in Task 1: before resume, a1's counts are exactly explore..plan once each.
2. **A second lane (b1) caught mid-flight by the same pause.** On resume it must continue from its own checkpoint, not redo its in-flight phase. Pinned in Task 1: the counts after resume equal every phase once for a1 and b1, and no key counted before resume grows.
3. **A cancelled card whose worktree already holds the fake's spec/plan output.** The relaunch must still drive it cleanly from `explore` to `done`. Pinned in Task 2: the relaunch exits 0 with `done: True`, and a1's relaunch counts equal every phase once.
4. **A paused or cancelled invocation creating the integrate branch anyway.** A person would expect no `m3-integrate` branch until a clean finish. Pinned in both tasks: `INTEGRATION_BRANCH not in _local_branches(root)` after the controlled run.
5. **A refused `am resume` of a cancelled run that still writes or launches something.** Pinned in Task 2: the fake log length for the cancelled run and its `cancelled` status are unchanged by the refusal.

---

### Task 1: Pause a held milestone, then resume it with nothing dispatched twice

**Files:**
- Create: `tests/e2e/test_live_control.py`
- Test: `tests/e2e/test_live_control.py::test_a_paused_milestone_resumes_with_nothing_dispatched_twice`

**Interfaces:**
- Consumes (conftest fixtures, unchanged): `two_story_board -> {"root": Path, "milestone": str, "stories": {"A": str, "B": str}, "subtasks": {"A": [a1], "B": [b1]}, "branches": {...}, ...}`; `run_milestone_cli(root: Path, milestone: str, max_concurrent: int | None = None, verify=None) -> click.testing.Result`; `read_fake_log(run_id: str) -> list[dict]`; the built-in `monkeypatch` and `request`.
- Produces (module-local, used by Task 2): `WAIT: float`, `VERIFY: str`, `AGENT_PHASES: tuple[str, ...]`, `INTEGRATION_BRANCH: str`, `_git(cwd, *args) -> str`, `_local_branches(root) -> list[str]`, `_envelope(result) -> dict`, `_error(result) -> dict`, `_latest_run_id(root) -> str`, `_status(root, run_id) -> str`, `_resume(root, run_id) -> Result`, `_attempt_of(stdout_path) -> tuple[str, str]`, `_card_phase_counts(entries) -> Counter`, `_counts(full=(), partial=None) -> Counter`, `_only(counts, card) -> Counter`, `_hold(monkeypatch, card, phase, entered, release) -> None`, `_signal_when_applied(monkeypatch, applied) -> None`, `_control_while_held(root, milestone, command, *, run_milestone_cli, entered, release, applied) -> tuple[str, dict, Result]`.

- [ ] **Step 1: Confirm the base branch carries the code under test**

Run:
```bash
git merge-base --is-ancestor m9/task-honour-pause-and-cancel-9f5467e3 HEAD && echo based-ok
grep -n "def apply_pending\|def controlled" src/agent_manager/control.py
grep -n "def request_control\|^def pause\|^def cancel\|was cancelled; start new work" src/agent_manager/cli.py
grep -n "def controlled_payload\|stop.requested == \"cancel\"" src/agent_manager/orchestrate.py
```
Expected: `based-ok`, and each grep prints at least one line. If any is missing, stop: the branch was cut from the wrong base, so report it instead of writing tests.

- [ ] **Step 2: Write the module, its helpers and the pause test**

Create `tests/e2e/test_live_control.py` with exactly this content:

```python
"""Default-suite e2e tier: pause, resume and cancel a milestone under the fake claude (card d147d97a).

Live-control spec §7 "End to end". `am run --milestone`, `am pause`,
`am cancel`, `am status` and `am resume` run through `CliRunner` on the real
`cli.app` with no `runner_factory`, so every launch goes through
`cli.default_runner_factory`, the real `ClaudeAdapter` and `cli.run_direct` to
the fake `claude` first on `PATH`.

The milestone runs in a worker thread. A one-shot hold (`_hold`) blocks a1's
`plan` launch on a `threading.Event` before the real launch. While it is held,
the test thread reads the run id on a second `store.open_db` connection, which
stands for another process and is also `am pause`'s real path, and records the
control through the CLI. It then waits until the running process has applied
the request (`_signal_when_applied`, a pass-through wrapper on
`control.apply_pending`), and only then releases the hold. No sleep proves any
ordering.

Unmarked on purpose: fake-claude e2e tests run on every `uv run pytest`; only
`tests/e2e/test_real_harness*.py` carry the `e2e` marker.
"""

import json
import subprocess
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agent_manager import cli, control, store
from agent_manager.harness import launcher

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)
"""Must equal the conftest's `AGENT_PHASES`: `TASK`'s seven agent phases, in order."""

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""

HELD_PHASE = "plan"
"""The phase whose launch is held while the control is sent."""

WAIT = 120.0
"""Seconds any bounded wait gives up after. Generous: it only bounds a broken
run; a healthy one never waits this long."""

CONTROL_KEYS_NEVER_PRESENT = {"escalated", "failed_phase", "integrated", "done"}
"""A controlled payload never escalates, never names a failed phase and never
reaches Integrate (live control C6)."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _error(result) -> dict:
    """The `error` of brd's failure envelope, `{"type": ..., "message": ...}`."""
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False, envelope
    return envelope["error"]


def _latest_run_id(root: Path) -> str:
    """The run id, read on a second connection: the "other process" of spec §7."""
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _status(root: Path, run_id: str) -> str:
    """`am status <run-id>`'s recorded run status (`data["run"]["status"]`)."""
    result = CliRunner().invoke(cli.app, ["status", run_id, "--repo-dir", str(root)])
    assert result.exit_code == 0, (result.output, result.exception)
    return _envelope(result)["run"]["status"]


def _resume(root: Path, run_id: str):
    """`am resume <run-id>`: no prefix, base or bound, only what the record lacks."""
    return CliRunner().invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(root), "--verify", VERIFY]
    )


def _attempt_of(stdout_path: Path) -> tuple[str, str]:
    """(card id, phase) of the attempt a launch belongs to.

    `paths.attempt_dir` is `<run dir>/<card>/<phase>.<n>` and the dispatcher
    hands the launcher `<attempt dir>/stdout.log`, so the launch names its own
    attempt; the fake is never asked.
    """
    attempt = Path(stdout_path).parent
    return attempt.parent.name, attempt.name.rsplit(".", 1)[0]


def _card_phase_counts(entries: Iterable[Mapping]) -> Counter:
    """How many times the fake ran each (card id, phase), read off its result path."""
    return Counter(
        (Path(entry["result_path"]).parents[1].name, entry["phase"]) for entry in entries
    )


def _counts(full: Iterable[str] = (), partial: Mapping[str, str] | None = None) -> Counter:
    """Every agent phase once per `full` card; for each `partial` card, its
    phases up to and including the named one once."""
    counts: Counter = Counter()
    for card in full:
        counts.update((card, phase) for phase in AGENT_PHASES)
    for card, last in (partial or {}).items():
        counts.update(
            (card, phase) for phase in AGENT_PHASES[: AGENT_PHASES.index(last) + 1]
        )
    return counts


def _only(counts: Counter, card: str) -> Counter:
    """The part of `counts` that belongs to `card`."""
    return Counter({key: value for key, value in counts.items() if key[0] == card})


def _hold(
    monkeypatch,
    card: str,
    phase: str,
    entered: threading.Event,
    release: threading.Event,
) -> None:
    """Hold `card`'s `phase` launch once: announce it, wait for `release`, then launch.

    Test scaffolding in the manager process: `cli.default_runner_factory`
    reads `cli.run_direct` at call time. The launch runs in a `to_thread`
    worker, so blocking here leaves the event loop, and so the control
    watcher, free. One-shot: the first matching launch disarms it, and every
    other launch (`am resume`'s and the relaunch's included) passes straight
    through. Undone with the test's function-scoped `monkeypatch`.
    """
    real = launcher.run_direct
    armed = {"on": True}

    def holding(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        if armed["on"] and _attempt_of(stdout_path) == (card, phase):
            armed["on"] = False
            entered.set()
            if not release.wait(WAIT):
                raise AssertionError(f"{card}'s {phase} launch was never released")
        return real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )

    monkeypatch.setattr(cli, "run_direct", holding)


def _signal_when_applied(monkeypatch, applied: threading.Event) -> None:
    """Set `applied` once the running process has applied a control request.

    A pass-through wrapper on `control.apply_pending`, which `control.watch`
    reads as a module global on every tick. It returns the rows it applied;
    a non-empty list means `StopSignal.request` has run. Undone with the
    test's function-scoped `monkeypatch`.
    """
    real = control.apply_pending

    def applying(*args: Any, **kwargs: Any):
        rows = real(*args, **kwargs)
        if rows:
            applied.set()
        return rows

    monkeypatch.setattr(control, "apply_pending", applying)


def _in_background(work: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """Run `work` on a daemon thread; its result or error lands in the box."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = work()
        except BaseException as error:  # surfaced by the caller, never swallowed
            box["error"] = error

    worker = threading.Thread(target=target, name="am-run-milestone", daemon=True)
    worker.start()
    return worker, box


def _control_while_held(
    root: Path,
    milestone: str,
    command: str,
    *,
    run_milestone_cli,
    entered: threading.Event,
    release: threading.Event,
    applied: threading.Event,
):
    """Run the milestone in a worker, send `am <command>` while the hold is in, and finish it.

    Returns (run id, the control command's `data`, the milestone run's
    `CliRunner` result). `release` is set in a `finally`, so a failed
    assertion never leaves the worker hanging. The control command's own
    `CliRunner.invoke` finishes before `release` is set. Both invocations swap
    `sys.stdout`, so they must nest and never interleave, and at that point
    the worker writes nothing because its envelope comes after the run
    returns.
    """
    worker, box = _in_background(lambda: run_milestone_cli(root, milestone))
    try:
        assert entered.wait(WAIT), "the held launch never arrived"
        run_id = _latest_run_id(root)
        requested = CliRunner().invoke(cli.app, [command, run_id, "--repo-dir", str(root)])
        assert requested.exit_code == 0, (requested.output, requested.exception)
        control_data = _envelope(requested)
        assert applied.wait(WAIT), f"the running process never applied the {command}"
    finally:
        release.set()
        worker.join(WAIT)
    assert not worker.is_alive(), "the milestone run never finished after release"
    assert "error" not in box, box.get("error")
    return run_id, control_data, box["result"]


def test_a_paused_milestone_resumes_with_nothing_dispatched_twice(
    two_story_board, run_milestone_cli, read_fake_log, monkeypatch, request
):
    """Spec §7 pause: held in a1's plan, paused from another connection, parked
    `stopped` without Integrate; `am resume` finishes it `integrated` under the
    same run id and no phase is dispatched twice."""
    # The tier: default suite, never the opt-in `e2e` marker.
    assert request.node.get_closest_marker("e2e") is None
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    (a1,) = two_story_board["subtasks"]["A"]
    (b1,) = two_story_board["subtasks"]["B"]
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    _hold(monkeypatch, a1, HELD_PHASE, entered, release)
    _signal_when_applied(monkeypatch, applied)

    run_id, requested, first = _control_while_held(
        root,
        milestone,
        "pause",
        run_milestone_cli=run_milestone_cli,
        entered=entered,
        release=release,
        applied=applied,
    )

    # `am pause` recorded the request for this life of the run.
    assert requested["run_id"] == run_id
    assert requested["command"] == "pause"
    assert requested["effective"] == "pause"
    assert requested["already_requested"] is False

    # The run parked: exit 0, `paused`, never an escalation, never Integrate (C6).
    assert first.exit_code == 0, (first.output, first.exception)
    paused = _envelope(first)
    assert paused["paused"] is True, paused
    assert paused["run_id"] == run_id
    assert paused["resume"] == f"am resume {run_id}"
    assert "cancelled" not in paused
    assert not CONTROL_KEYS_NEVER_PRESENT & set(paused), paused
    assert _status(root, run_id) == "stopped"
    assert INTEGRATION_BRANCH not in _local_branches(root)

    # The held plan finished once and a1 parked before validate_plan: a
    # control never cancels a running phase.
    before = _card_phase_counts(read_fake_log(run_id))
    assert _only(before, a1) == _counts(partial={a1: HELD_PHASE}), before

    resumed = _resume(root, run_id)

    assert resumed.exit_code == 0, (resumed.output, resumed.exception)
    data = _envelope(resumed)
    assert data["done"] is True, data
    assert data["resumed"] is True
    assert data["run_id"] == run_id
    assert "escalated" not in data
    assert a1 in data["completed"]
    assert set(data["integrated"]["merged"]) == set(stories.values())
    assert _status(root, run_id) == "done"
    assert INTEGRATION_BRANCH in _local_branches(root)

    # Nothing dispatched twice: every phase of both cards exactly once over
    # both invocations, and no phase counted before the resume grew.
    after = _card_phase_counts(read_fake_log(run_id))
    assert after == _counts(full=(a1, b1)), after
    assert not set(before) & set(after - before), (before, after)
    assert after[(a1, HELD_PHASE)] == 1
```

- [ ] **Step 3: Run the new test**

Run: `uv run pytest tests/e2e/test_live_control.py::test_a_paused_milestone_resumes_with_nothing_dispatched_twice -v`
Expected: PASS, because the code under test already exists on the base branch. If it FAILS or hangs past `WAIT`, do not edit the assertions to fit. Go to Task 3, Step 1, the bug path.

- [ ] **Step 4: Prove the test can fail (RED by mutation)**

The behaviour already exists, so show that the test really exercises it. Temporarily make the control a no-op. In `src/agent_manager/runtime/stop.py`, make the first line of the body of `StopSignal.request` (just after its docstring) this:

```python
        return False
```

Run: `uv run pytest tests/e2e/test_live_control.py::test_a_paused_milestone_resumes_with_nothing_dispatched_twice -v`
Expected: FAIL at `assert paused["paused"] is True` (a `KeyError: 'paused'`, because the run finishes `done` with `integrated`). It must not hang: `apply_pending` still returns the row, so `applied` is set.

- [ ] **Step 5: Revert the mutation and confirm GREEN**

Run:
```bash
git checkout -- src/agent_manager/runtime/stop.py
git diff --exit-code -- src/
uv run pytest tests/e2e/test_live_control.py::test_a_paused_milestone_resumes_with_nothing_dispatched_twice -v
```
Expected: `git diff` prints nothing and exits 0, and the test PASSES.

(No commit here: the spec asks for exactly one commit, made in Task 3.)

---

### Task 2: Cancel a held milestone: resume is refused and a relaunch starts over at explore

**Files:**
- Modify: `tests/e2e/test_live_control.py` (append one test at the end of the file)
- Test: `tests/e2e/test_live_control.py::test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch`

**Interfaces:**
- Consumes (from Task 1, same module): `WAIT`, `HELD_PHASE`, `INTEGRATION_BRANCH`, `CONTROL_KEYS_NEVER_PRESENT`, `_envelope(result) -> dict`, `_error(result) -> dict`, `_status(root, run_id) -> str`, `_resume(root, run_id) -> Result`, `_local_branches(root) -> list[str]`, `_card_phase_counts(entries) -> Counter`, `_counts(full=(), partial=None) -> Counter`, `_only(counts, card) -> Counter`, `_hold(monkeypatch, card, phase, entered, release)`, `_signal_when_applied(monkeypatch, applied)`, `_control_while_held(root, milestone, command, *, run_milestone_cli, entered, release, applied) -> (str, dict, Result)`; `cli.EXIT_ERROR` (`== 3`).
- Produces: nothing new for other tasks.

- [ ] **Step 1: Append the cancel test**

Add this at the end of `tests/e2e/test_live_control.py`:

```python
def test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch(
    two_story_board, run_milestone_cli, read_fake_log, monkeypatch, request
):
    """Spec §7 cancel: held in a1's plan, cancelled from another connection,
    recorded `cancelled` without Integrate; `am resume` refuses it at exit 3
    and launches nothing; a fresh `am run --milestone` drives a1 again from
    `explore`, never continuing the cancelled run's parked checkpoint (C9)."""
    assert request.node.get_closest_marker("e2e") is None
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    (a1,) = two_story_board["subtasks"]["A"]
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    _hold(monkeypatch, a1, HELD_PHASE, entered, release)
    _signal_when_applied(monkeypatch, applied)

    run_id, requested, first = _control_while_held(
        root,
        milestone,
        "cancel",
        run_milestone_cli=run_milestone_cli,
        entered=entered,
        release=release,
        applied=applied,
    )

    assert requested["run_id"] == run_id
    assert requested["command"] == "cancel"
    assert requested["effective"] == "cancel"
    assert requested["already_requested"] is False

    # Cancelled: exit 0, no resume hint, never an escalation, never Integrate (C6).
    assert first.exit_code == 0, (first.output, first.exception)
    cancelled = _envelope(first)
    assert cancelled["cancelled"] is True, cancelled
    assert cancelled["run_id"] == run_id
    assert "resume" not in cancelled and "paused" not in cancelled, cancelled
    assert not CONTROL_KEYS_NEVER_PRESENT & set(cancelled), cancelled
    assert _status(root, run_id) == "cancelled"
    assert INTEGRATION_BRANCH not in _local_branches(root)
    # a1 parked after its held plan: explore..plan once, nothing after.
    cancelled_counts = _card_phase_counts(read_fake_log(run_id))
    assert _only(cancelled_counts, a1) == _counts(partial={a1: HELD_PHASE})

    # `am resume` of a cancelled run is refused at exit 3, launching and writing nothing.
    launches = len(read_fake_log(run_id))
    refused = _resume(root, run_id)

    assert refused.exit_code == cli.EXIT_ERROR == 3, (refused.output, refused.exception)
    refusal = _error(refused)
    assert refusal["type"] == "NotResumableError"
    assert "cancelled" in refusal["message"]
    assert len(read_fake_log(run_id)) == launches
    assert _status(root, run_id) == "cancelled"

    # A fresh relaunch is new work: a new run id, a1 driven again from explore.
    relaunch = run_milestone_cli(root, milestone)

    assert relaunch.exit_code == 0, (relaunch.output, relaunch.exception)
    finished = _envelope(relaunch)
    assert finished["done"] is True, finished
    assert "escalated" not in finished
    assert finished["run_id"] != run_id
    assert a1 in finished["completed"]
    assert set(finished["integrated"]["merged"]) == set(stories.values())
    relaunched = _card_phase_counts(read_fake_log(finished["run_id"]))
    # Every a1 phase exactly once, `explore` included: the cancelled run's
    # parked checkpoint (pending validate_plan) was not continued.
    assert _only(relaunched, a1) == _counts(full=(a1,)), relaunched
    assert relaunched[(a1, "explore")] == 1
    # The cancelled run stays cancelled.
    assert _status(root, run_id) == "cancelled"
```

- [ ] **Step 2: Run the new test**

Run: `uv run pytest tests/e2e/test_live_control.py::test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch -v`
Expected: PASS. If it FAILS or hangs, do not bend the assertions. Go to Task 3, Step 1.

- [ ] **Step 3: Prove the test can fail (RED by mutation)**

The relaunch must not continue a cancelled run's checkpoint. Temporarily break that. In `src/agent_manager/store.py`, inside `Store.latest_open_checkpoint`, change the first query's guard

```python
                or newest["status"] == "cancelled"
```

to

```python
                or False
```

and delete this line from the second query:

```python
                " AND run_id NOT IN (SELECT id FROM runs WHERE status = 'cancelled')"
```

Run: `uv run pytest tests/e2e/test_live_control.py::test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch -v`
Expected: FAIL at `assert _only(relaunched, a1) == _counts(full=(a1,))`. The relaunch continues a1 from `validate_plan`, so `explore`..`plan` are missing from the new run's log.

- [ ] **Step 4: Revert the mutation and confirm GREEN**

Run:
```bash
git checkout -- src/agent_manager/store.py
git diff --exit-code -- src/
uv run pytest tests/e2e/test_live_control.py -v
```
Expected: `git diff` prints nothing and exits 0, and both tests PASS.

---

### Task 3: Whole suite green, then the single commit

**Files:**
- Test: whole suite
- Modify (bug path only): the source module that owns the fault, plus its existing test module (`tests/test_control.py`, `tests/test_orchestrate.py`, `tests/test_cli.py`, `tests/test_store.py` or `tests/runtime/test_stop.py`)

**Interfaces:**
- Consumes: `tests/e2e/test_live_control.py` from Tasks 1-2.
- Produces: one commit on `m9/task-prove-pause-resume-and-d147d97a`.

- [ ] **Step 1: Bug path, taken only if a Task 1 or Task 2 run failed or hung, or Step 2 below fails**

Invoke `superpowers:systematic-debugging` and find the root cause in `src/agent_manager/`. Then:
1. Write a failing regression test in the existing test module that owns the faulty code, in the default suite and unmarked. For example, a fault in `orchestrate.run_milestone`'s control handling goes in `tests/test_orchestrate.py`, and a fault in `cli.resume_run` or `cli.request_control` goes in `tests/test_cli.py`. It must fail for the bug's reason. Run it on its own with `uv run pytest <that module>::<that test> -v` and confirm FAIL.
2. Make the minimal fix in the owning source file.
3. Re-run that regression test (PASS), then `uv run pytest tests/e2e/test_live_control.py -v` (PASS).
4. Name the bug and the fix in the commit body (Step 3).
Skip this step entirely if every run so far passed.

- [ ] **Step 2: Run the whole suite**

Run: `uv run pytest`
Expected: every collected test passes, `tests/e2e/test_live_control.py` included; the `e2e`-marked real-harness tests are deselected by `addopts`. Also confirm that the new file is collected in the default run:

Run: `uv run pytest tests/e2e/test_live_control.py --collect-only -q`
Expected: both test ids are listed, and neither is deselected.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_live_control.py
git status --porcelain
git commit -m "test(e2e): pause, resume and cancel a milestone under the fake claude" \
  -m "Adds tests/e2e/test_live_control.py (default suite, unmarked, fake claude). A one-shot hold on cli.run_direct blocks a1's plan launch; am pause / am cancel run from the test thread against a second store.open_db connection. Beyond the spec, the tests also wait on a threading.Event set by a pass-through wrapper on control.apply_pending, because the process polls requests every CONTROL_POLL_SECONDS; releasing before the request is applied would race. Names follow the code: request_control's 'effective' key, controlled_payload's 'paused'/'cancelled' keys, store.latest_run_id."
```
If Task 3 Step 1 ran, also `git add` the regression test module and the fixed source file before committing, and append a sentence naming the bug and fix to the second `-m`. `git status --porcelain` before the commit must show only the intended files. Push nothing.
