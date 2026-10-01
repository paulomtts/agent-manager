<!-- task-pipeline: validated -->
# Adopt the resumed head in the compiled agent phase (subtask 6ecbe6e2)

Parent story fd673816 ("Adoption: a resumed agent phase reuses its recorded result"), milestone 4e0d2a6c. Task 2.2 of `docs/superpowers/plans/2026-09-27-exactly-once.md`; design `docs/superpowers/specs/2026-09-27-exactly-once-design.md` E8 (also E9, E11). Both docs live only in commit 6ab5618 (`git show 6ab5618:<path>`), not on master's tree.

## Base and prerequisites

Branch prefix `m11`, based on master (milestones 9 and 10 merged) with the two prerequisite branches merged in, since their code is not on master HEAD:

- `m11/task-compute-the-floor-at-94088f7e` (Task 1.2): `runtime/state.py` `Adoption(phase, loop, source_run, floor)`, `RunDeps.adopt`, `RunDeps.take_adoption(phase, loop)` (returns the carried adoption only on a `(phase, loop)` match and always clears it).
- `m11/task-let-the-agent-runner-4aed8212` (Task 2.1): `dispatch.Adopted(result, attempt, source_run)`, `dispatch.read_result`, `AgentRunner.adopt(phase, context, *, source_run, floor) -> Adopted | None`, `Store.replay_journal`.

Names above were read from those worktrees; the implementer must re-read the code as built after merging and adapt, and say so in the result. Nothing is pushed; the base branch never moves.

## Scope

Owned files: `src/agent_manager/runtime/compile.py` and the new `tests/runtime/test_exactly_once.py`. Do not modify `dispatch.py`, `store.py`, or `runtime/state.py`; only consume their interfaces.

## Behavior

`agent_phase` (compile.py:130-161 on master):

- After `table = context.binding_table(...)` and `rendered = prompt.render_prompt(...)`, call `deps.take_adoption(phase, loop)`.
- If it returns an `Adoption` and `getattr(deps.agent_runner, "adopt", None)` is not `None`, await `bridge.call_step(adopt, {"phase": p, "context": table, "source_run": a.source_run, "floor": a.floor})` inside the existing `try` (the block that today holds `result = await bridge.call_agent(...)`), so an unexpected error from adopt escalates through the same `except Exception -> Escalated(phase, ...)` path as a dispatch error.
- A non-`None` `Adopted` supplies `result = adopted.result` and `bridge.call_agent` is skipped. `None` (nothing to adopt, or a decline) falls through to `bridge.call_agent` as today.
- A runner without `adopt` (e.g. a plain callable/fake) dispatches as today; the adoption is still consumed.
- Everything after the `try` is unchanged: the `ContextItem` yield, successor turn, `fresh_loop_after` handling, and `on_fail` handling of `AgentPhaseFailed` from a dispatch.

`step_phase` (compile.py:163-196): call `deps.take_adoption(phase, loop)` on entry and discard the result, so no adoption outlives the first turn after resume. Steps are never adopted and stay at-least-once (E9).

Observable surface: the only user-visible effect of an adoption is the one warning line `AgentRunner.adopt` already appends (naming the phase, attempt, and source run id). CLI envelope and exit codes unchanged. No schema changes, no new checkpoint write points (BEFORE_TURN stays the only one).

## Error paths

- `adopt` raises an unexpected exception: escalated as `Escalated(phase, walk._render_error(error))`, the same as a dispatch error.
- `adopt` declines (result file invalid, gates fail, journal unreadable): returns `None` with its own warning; the phase dispatches again normally.
- Adoption for a different `(phase, loop)` than the first turn run: discarded by `take_adoption`; the phase dispatches.
- Missing agent runner: the existing `EngineError` before anything runs, unchanged.

## Tests

All in `tests/runtime/test_exactly_once.py`. Tier, per the design spec section 14 "Testing" placement rule: the Engine tier (engine driven with an injected fake launcher returning canned results, including crash-mid-phase, to prove resume) — which is where `tests/runtime` sits; the exactly-once design's section 7 names this file for the crash-window/dispatch-count tests. Not the e2e tier (the fake-claude kill-and-resume test in `tests/e2e/test_exactly_once.py` is out of scope).

Fixture: real `AgentRunner` + a counting `FakeLauncher` + real store/journal + real pygents agents; workflow `w`(step) -> `a`(agent) -> `b`(agent) -> `z`(step); a `_dispatches(launcher)` helper counting launcher calls per phase. Crashes are a plain `BaseException` subclass, mirroring `_Crash` (tests/runtime/test_resume.py:42). There is no reusable "arm" helper in this repo (the exactly-once plan's pseudocode names one, but it was never built): raise it the way `test_resume.py` already does, inline in a fake runner/step/launcher or via `monkeypatch.setattr` wrapping the target call (e.g. the inline `raise _Crash(...)` in `_five`'s step closures at test_resume.py:89-90, or the fake-runner crash points in `_crash_on_the_second_spec` at test_resume.py:573-584) -- pick whichever shape fits each test's crash point (mid-`_record_phase`, mid-`bridge.call_agent`, mid-launcher, mid-`run_one_step`). No sleeps.

1. W1: crash after `a`'s `done` row -> resume does not dispatch `a` again; `b` dispatched once.
2. W2: crash after `call_agent` returns for `a` (before the next checkpoint) -> resume adopts `a` (one dispatch total), one warning line.
3. W2 at the last agent phase (`b`) -> adopted, `z` runs, subtask completes, `b` dispatched once.
4. Double crash: crash again after resume but before the adopted phase finishes -> second resume still adopts; `a` dispatched once overall.
5. W0: crash mid-dispatch with a valid `result.json` on disk -> dispatches again; attempt statuses `[(1, "harness_error"), (2, "ok")]`.
6. Goto-looped phase: a phase revisited across loop iterations dispatches each iteration and is never adopted.
7. Relaunch across runs: resume in a new run adopts once from the earlier run; the warning names the source run id.
8. Step caught in the window: crash after a step's work but before its checkpoint -> the step reruns (at-least-once, E9).
9. Checkpoint with no floor row (pre-milestone degradation, E11) -> dispatches again, no error.
10. Parked subtask: `StopSignal` mid-`a` -> resume dispatches `b` exactly once.

## Verification

`uv run pytest` (full suite). No typecheck, no lint.

---

# Adopt the Resumed Head in the Compiled Agent Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A resumed subtask whose head turn is an agent phase that already succeeded reuses that recorded result (one warning line) instead of dispatching the harness again, while steps stay at-least-once.

**Architecture:** `compile.agent_phase` consumes the run's carried `Adoption` via `RunDeps.take_adoption(phase, loop)` and, when the injected runner has an `adopt` method, calls it through `bridge.call_step` inside the existing `try`, skipping `bridge.call_agent` on a non-`None` `Adopted`. `compile.step_phase` consumes and discards the adoption on entry. Everything else (the floor computation, the carried adoption, `AgentRunner.adopt`, `Store.replay_journal`) is already built on this branch and only consumed.

**Tech Stack:** Python 3, pygents (agents/tools/hooks), Pydantic, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-adopt-the-resumed-head-6ecbe6e2/docs/superpowers/specs/task-adopt-the-resumed-head-6ecbe6e2-design.md` (prepended verbatim above). Upstream design: `git show 6ab5618:docs/superpowers/specs/2026-09-27-exactly-once-design.md` (E8, E9, E11).

## Code as built (read before planning; adapt if it moved)

All paths are inside the worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-adopt-the-resumed-head-6ecbe6e2` (branch `m11/task-adopt-the-resumed-head-6ecbe6e2`, cut from `m11/task-let-the-agent-runner-4aed8212`). Verified on disk while writing this plan — both prerequisites are already present on this branch, so nothing needs merging:

- `src/agent_manager/runtime/state.py:20-31` `Adoption(phase, loop, source_run, floor)`; `:51` `RunDeps.adopt`; `:55-63` `RunDeps.take_adoption(phase, loop) -> Adoption | None` (always clears).
- `src/agent_manager/runtime/engine.py:184-191` builds `RunDeps(..., adopt=Adoption(**vars(resume_from.floor)))` from the resume checkpoint's floor (`None` for a fresh run or a floorless row).
- `src/agent_manager/runtime/checkpoint.py:66-95` `_floor`: a `turn`/`parked` row whose next turn is an agent phase carries `TurnFloor(phase, loop, run_id, paths.highest_attempt(...))`, or the carried adoption unchanged on a `(phase, loop)` match; a step head gets no floor.
- `src/agent_manager/dispatch.py:172-184` `Adopted(result, attempt, source_run)`; `:652-709` `AgentRunner.adopt(phase, context, *, source_run, floor) -> Adopted | None` (reads `store.replay_journal(source_run)`, adopts the highest `ok` attempt numbered above `floor`, re-reads the file, re-runs gates, records the phase `done`, appends `"phase 'X' was not dispatched again: attempt N of run R had already succeeded (result reused)"`); `:711-728` `_decline` appends `"phase 'X': attempt N of run R was not reused (WHY); dispatching again"`.
- `src/agent_manager/store.py:1587-1602` `Store.replay_journal(run_id)` (needs a `run_upsert` line at the head of the journal — tests must seed run/story/subtask lines, as `tests/test_dispatch.py:1640` `_seed` does).
- `src/agent_manager/runtime/compile.py:130-161` `agent_phase` (the `try` at `:144-157` is the insertion point); `:163-196` `step_phase`.
- `src/agent_manager/runtime/bridge.py:69` `call_agent(runner, phase, context, rendered)`; `:89` `call_step(fn, kwargs)` (runs `fn(**kwargs)` in a thread).
- `src/agent_manager/cli.py:539` `orphan_attempts(subtask)`; `cli.py:1594-1600` is how a real resume marks orphaned `started` attempts `harness_error` before resuming — the W0 and Goto tests mirror it, since the engine itself does not.
- `tests/runtime/test_resume.py:44` `_Crash(BaseException)`, `:89-91` inline crash in a step closure, `:101-127` `_StopAfterA` (the no-sleep stop trigger pattern), `:311-317` `_new_process()`.
- `tests/test_dispatch.py:159-196` `make_role`, `:199-221` `FakeAdapter`, `:290-323` `FakeLauncher` — the new file copies the minimal shapes it needs (no test module in this repo imports another).

**Deviation from the spec's two-file scope (necessary, flagged):** `tests/runtime/test_resume.py:643` asserts that a plain-callable runner sees `current_run.get().adopt == Adoption("spec", 1, "run-earlier", 7)` *while it is dispatching*. The spec requires the adoption to be consumed before dispatch ("the adoption is still consumed"), so that assertion must change. Task 1 Step 6 rewrites only that test to observe the adoption via a `take_adoption` spy instead; its floor assertions are untouched. Report this in the result.

## Global Constraints

- Owned files: `src/agent_manager/runtime/compile.py` and the new `tests/runtime/test_exactly_once.py` (plus the one-test adaptation in `tests/runtime/test_resume.py` above). Do not modify `dispatch.py`, `store.py`, or `runtime/state.py`.
- No existing table gains a column, no CHECK changes; checkpoint_floors is a new row-only table outside the journal.
- BEFORE_TURN stays the only turn write point; no AFTER_PUT/AFTER_TURN checkpoint.
- dispatch.py never imports pygents; AgentRunner.adopt takes/returns plain values.
- Adoption reads the source run's journal via Store.replay_journal under the store lock, never the projection; never adopts a non-ok attempt; always re-validates the file and re-runs gates.
- Steps are not adopted, stay at-least-once, must be idempotent (E9).
- CLI envelope/exit codes unchanged; adoption surfaces only as one warning line.
- No test sleeps; a crash is a plain BaseException subclass raised at a named point, matching `_Crash` in tests/runtime/test_resume.py.
- Branch prefix m11, base master with milestones 9 and 10 merged; nothing pushed; base branch never moves.
- Test tier: every test in this plan is Engine tier (design spec section 14) and lives in `tests/runtime/test_exactly_once.py`, beside `test_resume.py` and `test_checkpoint.py`. The registry-isolating autouse fixture `fresh_pygents` comes from `tests/runtime/conftest.py`.
- Verification: `uv run pytest` (full suite). No typecheck, no lint.

## Review Focus

1. A recorded result that no longer holds up at resume (result file edited/corrupted between crash and resume) — expected: one decline warning, the phase dispatches again, the subtask still completes. Test: `test_a_damaged_result_is_declined_and_dispatched_again` (Task 1).
2. `adopt` itself raising an unexpected exception — expected: the subtask escalates at that phase with `"<Type>: <msg>"`, nothing re-dispatched, no crash out of the engine. Test: `test_an_adopt_that_raises_escalates_like_a_dispatch_error` (Task 1).
3. A carried adoption naming a different `(phase, loop)` than the head turn — expected: discarded, the head dispatches, no warning. Test: `test_a_mismatched_adoption_is_discarded` (Task 3).
4. A runner with no `adopt` method (plain callable) and a carried floor — expected: dispatches exactly as before, adoption consumed. Test: the adapted `test_resume.py::test_a_carried_floor_survives_a_resume` (Task 1).
5. A relaunch whose source run's journal has gone missing — expected: decline warning naming attempt `?` and the source run, dispatch again, subtask completes. Test: `test_a_relaunch_whose_source_journal_is_gone_dispatches_again` (Task 3).

---

### Task 1: Adopt the resumed head in `agent_phase`

**Files:**
- Create: `tests/runtime/test_exactly_once.py`
- Modify: `src/agent_manager/runtime/compile.py:140-157`
- Modify: `tests/runtime/test_resume.py:31` and `:626-649`

**Interfaces:**
- Consumes: `RunDeps.take_adoption(phase: str, loop: int) -> Adoption | None`; `Adoption.source_run: str`, `Adoption.floor: int`; `AgentRunner.adopt(phase, context, *, source_run: str, floor: int) -> Adopted | None`; `Adopted.result: Any`; `bridge.call_step(fn, kwargs: Mapping[str, Any]) -> Any`.
- Produces: in `tests/runtime/test_exactly_once.py`, module-level helpers later tasks use unchanged: `_Crash`, `FakeLauncher(results: dict[str, list[str | None]], crash_on: set[tuple[str, int]], on_call: Callable[[str], None] | None, calls: list[str])`, `_dispatches(launcher) -> dict[str, int]`, `_workflow(ran: list[str], *, critic: bool = False) -> Workflow`, `_runner(opened, launcher, roles) -> dispatch.AgentRunner`, `_subtask() -> models.SubtaskRun`, `_go(workflow, opened, runner, **kwargs) -> SubtaskSummary`, `_seed(opened, run_id=RUN_ID)`, `_reused(phase, n, source_run) -> str`, `_phase_rows(opened, phase) -> list[str]`, `_attempt_statuses(opened, phase) -> list[tuple[int, str]]`, `_head(agent) -> str`, `_next_turn(agent) -> dict`, `_mark_orphans(opened)`, `_new_process()`, crash arms `_crash_after_call_agent(monkeypatch, name)`, `_crash_after_done_row(monkeypatch, name)`, `_crash_after_adopt(monkeypatch)`, `_crash_after_step(monkeypatch, name)`, `_StopDuring(phase)`; fixtures `store`, `roles`; constants `RUN_ID`, `OTHER_RUN_ID`, `STORY_ID`, `CARD_ID`, `REPO`, `FIXED`, `FOUND`, `FOUND_JSON`, `REJECTED_JSON`.

- [ ] **Step 1: Write the fixture module and the failing adoption tests**

Create `tests/runtime/test_exactly_once.py` with exactly this content:

```python
"""Exactly-once agent phases across a crash (exactly-once design E8, E9, E11; card 6ecbe6e2).

Engine tier (design §14): `runtime.engine.run_subtask` drives a real
`dispatch.AgentRunner` over a counting `FakeLauncher` that writes canned
result files, a real temp SQLite projection plus a real temp JSONL journal,
and real pygents agents. Nothing is ever spawned. The workflow is
`w` (step) -> `a` (agent) -> `b` (agent) -> `z` (step).

A crash is `_Crash`, a plain `BaseException`, as in tests/runtime/test_resume.py:
neither the engine, pygents nor `AgentRunner` may catch it. Each crash point is
armed by wrapping the call it fires after with `monkeypatch.setattr`, once.
No sleeps: the one stop is handed to the loop with `call_soon_threadsafe`.
Registry isolation between tests is the autouse `fresh_pygents` fixture.
"""

import asyncio
import dataclasses
import hashlib
import json
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pygents import AgentRegistry, ToolRegistry

from agent_manager import cli, dispatch, models, paths, store as store_module
from agent_manager.harness.base import Outcome
from agent_manager.runtime import bridge, walk
from agent_manager.runtime import compile as compile_mod
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.store import TurnFloor
from agent_manager.workflow.phases import AgentPhase, Goto, Step, Workflow

RUN_ID = "run-2026-10-01-01"
OTHER_RUN_ID = "run-2026-10-01-02"
STORY_ID = "fd673816"
CARD_ID = "6ecbe6e2"
REPO = Path("/repo")
FIXED = datetime(2026, 10, 1, tzinfo=timezone.utc)


class _Crash(BaseException):
    """A process death at a named point: not an `Exception`, so nothing may catch it."""


# ── result model, roles, adapter, launcher ──────────────────────────────────


class Found(BaseModel):
    """Both agent phases' result model."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    ok: bool = True


FOUND = {"summary": "found it", "ok": True}
FOUND_JSON = json.dumps(FOUND)
REJECTED_JSON = json.dumps({"summary": "not yet", "ok": False})


def critic_gate(result):
    """`b`'s gate: a result with `ok` false fails it."""
    return None if result["ok"] else {"approved": False}


POLICY = """\
allowed_tools = ["Read"]
max_attempts = 2
required_capabilities = []

[default_model]
claude = "sonnet"
fake = "fake-model"
"""

METHODOLOGY = """\
# Test-driven development

## the loop

Red, green, refactor. Never write implementation code before a failing test.
"""


def make_role(root: Path, name: str = "explorer") -> Path:
    """A synthetic role bundle, as tests/test_dispatch.py's `make_role` builds it."""
    directory = root / name
    (directory / "methodology").mkdir(parents=True, exist_ok=True)
    (directory / "system.md").write_text(
        f"Standing instructions for {name}.\n", encoding="utf-8"
    )
    (directory / "policy.toml").write_text(POLICY, encoding="utf-8")
    path = directory / "methodology" / "test-driven-development.md"
    path.write_text(METHODOLOGY, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "VENDORED.lock").write_text(
        "[[vendored]]\n"
        'file = "test-driven-development.md"\n'
        'upstream = "skills/test-driven-development.md"\n'
        f'sha256 = "{digest}"\n',
        encoding="utf-8",
    )
    return directory


class FakeAdapter:
    """A `HarnessAdapter` by shape, whose argv names a program nothing runs."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return [
            "fake-harness",
            "--model",
            d.model,
            "--prompt",
            str(d.prompt_path),
            "--result",
            str(d.result_path),
        ]

    def parse_usage(self, stdout: str):
        return None


@dataclass
class FakeLauncher:
    """A counting `LauncherFn` double that writes canned result files.

    `calls` holds the phase of every dispatch, in order (read from the attempt
    directory `{phase}.{n}` the result path sits in). `results[phase][i]` is
    that phase's (i+1)th dispatch's `result.json` text, the last entry
    repeating; a phase with no entry gets `FOUND_JSON`. A `(phase, nth)` in
    `crash_on` writes its result file and then raises `_Crash`, once: the
    manager died mid-dispatch, after the harness wrote a valid file (W0).
    `on_call`, when set, is called with the phase before the file is written.
    """

    results: dict[str, list[str | None]] = field(default_factory=dict)
    crash_on: set[tuple[str, int]] = field(default_factory=set)
    on_call: Callable[[str], None] | None = None
    calls: list[str] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        result_path = Path(argv[argv.index("--result") + 1])
        phase = result_path.parent.name.rsplit(".", 1)[0]
        self.calls.append(phase)
        nth = self.calls.count(phase)
        if self.on_call is not None:
            self.on_call(phase)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("log\n", encoding="utf-8")
        script = self.results.get(phase, [FOUND_JSON])
        canned = script[min(nth - 1, len(script) - 1)]
        if canned is not None:
            result_path.write_text(canned, encoding="utf-8")
        if (phase, nth) in self.crash_on:
            self.crash_on.discard((phase, nth))
            raise _Crash(f"killed while {phase} dispatch {nth} was in flight")
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=1.0,
            stdout_path=stdout_path,
        )


def _dispatches(launcher: FakeLauncher) -> dict[str, int]:
    """How many times each phase reached the launcher, over every run."""
    return dict(Counter(launcher.calls))


# ── workflow, store, runner ─────────────────────────────────────────────────


def _workflow(ran: list[str], *, critic: bool = False) -> Workflow:
    """`w` -> `a` -> `b` -> `z`. The steps append their name to `ran`.
    With `critic`, a `b` whose gate fails loops back to `a` once."""

    def make(name: str):
        def run(card: str) -> dict[str, Any]:
            ran.append(name)
            return {name: name.upper()}

        return run

    return Workflow(
        "exactly_once",
        (
            Step("w", make("w")),
            AgentPhase("a", "explorer", (), Found),
            AgentPhase(
                "b",
                "explorer",
                (),
                Found,
                gates=(critic_gate,),
                on_fail=Goto("a", 1) if critic else None,
            ),
            Step("z", make("z")),
        ),
    )


def _seed(opened, run_id: str = RUN_ID) -> None:
    """The run, story and subtask lines `Store.replay_journal` needs above any phase line."""
    opened.record_run(
        models.Run(
            id=run_id,
            workflow="exactly_once",
            repo_dir=REPO,
            base_branch="master",
            branch_prefix="m11/",
        )
    )
    opened.record_story(models.StoryRun(card_id=STORY_ID, title="Adoption", level=0))
    opened.record_subtask(
        STORY_ID,
        models.SubtaskRun(card_id=CARD_ID, branch=f"m11/task-{CARD_ID}", base_branch="master"),
    )


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    _seed(opened)
    yield opened
    opened.close()


@pytest.fixture
def roles(tmp_path) -> Path:
    make_role(tmp_path / "bundles")
    return tmp_path / "bundles"


def _runner(opened, launcher: FakeLauncher, roles: Path) -> dispatch.AgentRunner:
    """A real `AgentRunner` for `opened`'s run. One per run: a new process
    starts with an empty `warnings` list."""
    adapter = FakeAdapter()
    return dispatch.AgentRunner(
        store=opened,
        launcher=launcher,
        run_id=opened.run_id,
        story_id=STORY_ID,
        card_id=CARD_ID,
        adapters={adapter.name: adapter},
        result_models={},
        harness_map={
            "explorer": models.HarnessAssignment(harness=adapter.name, model="fake-model")
        },
        role_root=roles,
        timeout=45.0,
    )


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m11/task-adopt-the-resumed-head-{CARD_ID}",
        base_branch="m11/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _go(workflow: Workflow, opened, runner, **kwargs: Any):
    return runtime_engine.run_subtask(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        agent_runner=runner,
        **kwargs,
    )


# ── reading what happened ───────────────────────────────────────────────────


def _reused(phase: str, n: int, source_run: str) -> str:
    """The one warning line an adoption writes (`AgentRunner.adopt`)."""
    return (
        f"phase {phase!r} was not dispatched again: attempt {n} of run {source_run} "
        "had already succeeded (result reused)"
    )


def _phase_rows(opened, phase: str) -> list[str]:
    """Every `phase_upsert` status of `phase` in `opened`'s own journal, in order."""
    return [
        line.payload["status"]
        for line in opened.journal.read()
        if line.event == "phase_upsert" and line.phase == phase
    ]


def _attempt_statuses(opened, phase: str) -> list[tuple[int, str]]:
    """`phase`'s attempts as the journal last records them: `(n, status)`."""
    run = opened.replay_journal(opened.run_id)
    return [
        (attempt.n, attempt.status)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.card_id == CARD_ID
        for recorded in subtask.phases
        if recorded.name == phase
        for attempt in recorded.attempts
    ]


def _next_turn(agent: dict) -> dict:
    """The turn a stored agent would run next: its current turn, else its queue head."""
    return agent["current_turn"] or agent["queue"][0]


def _head(agent: dict) -> str:
    return _next_turn(agent)["kwargs"]["phase"]


def _mark_orphans(opened) -> None:
    """What a real resume does first (cli.py, before `drive_subtask_async`):
    every attempt left `started` is recorded `harness_error`."""
    run = opened.replay_journal(opened.run_id)
    for story in run.stories:
        for subtask in story.subtasks:
            for phase, attempt in cli.orphan_attempts(subtask):
                opened.record_attempt(
                    story.card_id,
                    subtask.card_id,
                    phase.name,
                    attempt.model_copy(update={"status": "harness_error"}),
                )


def _new_process() -> None:
    """A restart: every pygents registry and the compile cache start empty."""
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()


# ── crash points ────────────────────────────────────────────────────────────


def _crash_after_call_agent(monkeypatch, name: str) -> None:
    """W2: `name`'s dispatch returned its result, and the process died before
    the next checkpoint. Once."""
    armed = {name}
    real = bridge.call_agent

    async def call_agent(runner, phase, context, rendered):
        result = await real(runner, phase, context, rendered)
        if phase.name in armed:
            armed.discard(phase.name)
            raise _Crash(f"killed after {phase.name}'s dispatch returned")
        return result

    monkeypatch.setattr(bridge, "call_agent", call_agent)


def _crash_after_done_row(monkeypatch, name: str) -> None:
    """W1: `name`'s `done` row is in the journal, and the process died. Once."""
    armed = {name}
    real = dispatch.AgentRunner._record_phase

    def record_phase(self, phase, status, *args):
        real(self, phase, status, *args)
        if status == "done" and phase.name in armed:
            armed.discard(phase.name)
            raise _Crash(f"killed after {phase.name}'s done row")

    monkeypatch.setattr(dispatch.AgentRunner, "_record_phase", record_phase)


def _crash_after_adopt(monkeypatch) -> None:
    """The process died right after an adoption returned, before its turn ended. Once."""
    armed = [True]
    real = dispatch.AgentRunner.adopt

    def adopt(self, phase, context, *, source_run, floor):
        adopted = real(self, phase, context, source_run=source_run, floor=floor)
        if armed:
            armed.clear()
            raise _Crash(f"killed after {phase.name} was adopted")
        return adopted

    monkeypatch.setattr(dispatch.AgentRunner, "adopt", adopt)


def _crash_after_step(monkeypatch, name: str) -> None:
    """Step `name` ran and recorded `done`, and the process died before the
    next checkpoint. Once."""
    armed = {name}
    real = walk.run_one_step

    def run_one_step(**kwargs):
        outcome = real(**kwargs)
        if kwargs["phase"].name in armed:
            armed.discard(kwargs["phase"].name)
            raise _Crash(f"killed after step {kwargs['phase'].name}")
        return outcome

    monkeypatch.setattr(walk, "run_one_step", run_one_step)


class _StopDuring:
    """A `StopSignal` the launcher triggers while `phase` is dispatching, once.

    tests/runtime/test_resume.py's `_StopAfterA`, moved into the launcher: the
    launcher runs in a `to_thread` worker and the signal lives on the loop, so
    the trigger is handed over with `call_soon_threadsafe` and the worker
    blocks on a `threading.Event` until it has run -- no sleeps.
    """

    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.signal = StopSignal()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._fired = threading.Event()

    def __call__(self, phase: str) -> None:
        if phase != self.phase or self._fired.is_set():
            return
        assert self.loop is not None, "set before the run"

        def trigger() -> None:
            self.signal.trigger(STORY_ID)
            self._fired.set()

        self.loop.call_soon_threadsafe(trigger)
        if not self._fired.wait(5):
            raise RuntimeError("the stop was never triggered")


# ── adoption: the windows where the head already succeeded ──────────────────


def test_w2_a_crash_after_the_dispatch_returned_adopts_on_resume(store, roles, monkeypatch):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_call_agent(monkeypatch, "a")

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    crashed = store.latest_checkpoint(CARD_ID)
    assert (crashed.reason, _head(crashed.agent)) == ("turn", "a")
    assert crashed.floor == TurnFloor("a", 0, RUN_ID, 0)
    assert _dispatches(launcher) == {"a": 1}

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("a", 1, RUN_ID)]
    assert summary.results["a"] == FOUND
    assert ran == ["w", "z"]
    assert _attempt_statuses(store, "a") == [(1, "ok")]


def test_w1_a_crash_after_the_done_row_does_not_dispatch_again(store, roles, monkeypatch):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_done_row(monkeypatch, "a")

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    assert _phase_rows(store, "a") == ["started", "done"]
    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "a"

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("a", 1, RUN_ID)]


def test_w2_at_the_last_agent_phase_adopts_and_the_subtask_completes(
    store, roles, monkeypatch
):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_call_agent(monkeypatch, "b")

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "b"
    assert crashed.floor == TurnFloor("b", 0, RUN_ID, 0)
    assert ran == ["w"]

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("b", 1, RUN_ID)]
    assert summary.results["b"] == FOUND
    assert ran == ["w", "z"]
    assert store.latest_checkpoint(CARD_ID).reason == "done"


def test_a_second_crash_before_the_adopted_phase_finishes_still_adopts(
    store, roles, monkeypatch
):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)

    _crash_after_adopt(monkeypatch)
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles), resume_from=crashed)

    second = store.latest_checkpoint(CARD_ID)
    assert second.seq > crashed.seq
    assert (second.reason, _head(second.agent)) == ("turn", "a")
    # The resumed BEFORE_TURN re-saved the carried floor unchanged.
    assert second.floor == TurnFloor("a", 0, RUN_ID, 0)

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=second)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("a", 1, RUN_ID)]


def test_a_relaunch_adopts_once_from_the_earlier_run(store, roles, monkeypatch, tmp_path):
    launcher = FakeLauncher()
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(_workflow([]), store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)

    _new_process()
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        _seed(other, OTHER_RUN_ID)
        runner = _runner(other, launcher, roles)
        ran: list[str] = []
        summary = _go(_workflow(ran), other, runner, resume_from=crashed)
        a_rows = _phase_rows(other, "a")
        newest = other.latest_checkpoint(CARD_ID)
    finally:
        other.close()

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("a", 1, RUN_ID)]
    assert RUN_ID in runner.warnings[0]
    # The adoption is recorded in the new run only, as one `done` row.
    assert a_rows == ["done"]
    assert newest.reason == "done"
    assert ran == ["z"]


def test_an_adopt_that_raises_escalates_like_a_dispatch_error(store, roles, monkeypatch):
    launcher = FakeLauncher()
    wf = _workflow([])
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)

    def adopt(self, phase, context, *, source_run, floor):
        raise RuntimeError("journal vanished")

    monkeypatch.setattr(dispatch.AgentRunner, "adopt", adopt)
    summary = _go(wf, store, _runner(store, launcher, roles), resume_from=crashed)

    assert summary.status == "escalated"
    assert summary.failed_phase == "a"
    assert summary.detail == "RuntimeError: journal vanished"
    assert _dispatches(launcher) == {"a": 1}
    assert store.latest_checkpoint(CARD_ID).reason == "escalated"


def test_a_damaged_result_is_declined_and_dispatched_again(store, roles, monkeypatch):
    launcher = FakeLauncher()
    wf = _workflow([])
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)
    result_file = paths.attempt_dir(RUN_ID, CARD_ID, "a", 1) / dispatch.RESULT_NAME
    result_file.write_text("I could not produce JSON, sorry.", encoding="utf-8")

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 1}
    [warning] = runner.warnings
    assert warning.startswith(f"phase 'a': attempt 1 of run {RUN_ID} was not reused (")
    assert "is not valid JSON" in warning
    assert warning.endswith("); dispatching again")
    assert _attempt_statuses(store, "a") == [(1, "ok"), (2, "ok")]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run (from the worktree): `uv run pytest tests/runtime/test_exactly_once.py -v`
Expected: all 7 tests FAIL. The W1/W2/W2-last/relaunch tests fail on `_dispatches(launcher) == {"a": 1, "b": 1}` (actual `{"a": 2, "b": 1}`, or `{"a": 1, "b": 2}` for the last-phase test); the double-crash test fails at its second `pytest.raises(_Crash)` (DID NOT RAISE — `adopt` is never called); the adopt-raises test fails on `summary.status == "escalated"` (actual `"done"`); the damaged-result test fails on `[warning] = runner.warnings` (empty list). If anything fails for another reason (fixture error, role loading, replay `JournalError`), fix the fixture before continuing — do not touch `compile.py` yet.

- [ ] **Step 3: Implement adoption in `agent_phase`**

In `src/agent_manager/runtime/compile.py`, replace lines 140-145:

```python
        table = context.binding_table(pool, memory, phase)
        # Outside the try: an input no resolver provides is a workflow bug
        # and its `EngineError` must reach the caller as is.
        rendered = prompt.render_prompt(p, table)
        try:
            result = await bridge.call_agent(deps.agent_runner, p, table, rendered)
```

with:

```python
        table = context.binding_table(pool, memory, phase)
        # Outside the try: an input no resolver provides is a workflow bug
        # and its `EngineError` must reach the caller as is.
        rendered = prompt.render_prompt(p, table)
        # The resume checkpoint's floor, if it names this very turn
        # (exactly-once E8). Taken whether or not the runner can adopt, so no
        # adoption outlives the first turn after a resume.
        adoption = deps.take_adoption(phase, loop)
        adopt = getattr(deps.agent_runner, "adopt", None)
        try:
            # Inside the try: an adopt that raises escalates like a dispatch
            # error. `None` -- nothing recorded, or a decline with its own
            # warning -- dispatches as if there had been no crash.
            adopted = None
            if adoption is not None and adopt is not None:
                adopted = await bridge.call_step(
                    adopt,
                    {
                        "phase": p,
                        "context": table,
                        "source_run": adoption.source_run,
                        "floor": adoption.floor,
                    },
                )
            if adopted is not None:
                result = adopted.result
            else:
                result = await bridge.call_agent(deps.agent_runner, p, table, rendered)
```

Leave lines 146-161 (the two `except` clauses, the `ContextItem` yield and the successor turn) exactly as they are.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/runtime/test_exactly_once.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the resume tests and see the one assertion this change invalidates**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: exactly one FAIL, `test_a_carried_floor_survives_a_resume`, at `assert seen[0] == ("spec", Adoption("spec", 1, "run-earlier", 7))` (actual `("spec", None)`): the plain runner now runs after `agent_phase` has consumed the adoption. Every other test passes. Any other failure is a bug in Step 3 — fix `compile.py`, not the test.

- [ ] **Step 6: Adapt that test to observe the adoption where it is now taken**

In `tests/runtime/test_resume.py`, change line 31 from:

```python
from agent_manager.runtime.state import Adoption, current_run
```

to:

```python
from agent_manager.runtime.state import Adoption, RunDeps, current_run
```

and replace the whole `test_a_carried_floor_survives_a_resume` function (lines 626-649) with:

```python
def test_a_carried_floor_survives_a_resume(store, monkeypatch):
    wf, crashed = _crash_in_the_loop(store)
    # The first run floored the turn it died in under its own id.
    assert crashed.reason == "turn"
    assert crashed.floor == TurnFloor("spec", 1, RUN_ID, 0)
    # As if that row had itself been carried from an earlier run: the resume
    # must keep the earlier run's id and number, not recompute its own.
    carried = TurnFloor("spec", 1, "run-earlier", 7)
    seen: list[tuple[str, Adoption | None]] = []
    taken: list[Adoption | None] = []
    take = RunDeps.take_adoption

    def recording_take(self, phase, loop):
        taken.append(take(self, phase, loop))
        return taken[-1]

    monkeypatch.setattr(RunDeps, "take_adoption", recording_take)

    summary = _go(
        wf,
        store,
        agent_runner=_critic_fails_recording(seen),
        resume_from=dataclasses.replace(crashed, floor=carried),
    )

    # The resumed head's tool takes the carried floor before it dispatches
    # (compile.agent_phase, exactly-once E8). This runner has no `adopt`, so
    # it dispatches as before -- and sees the adoption already consumed.
    assert taken[0] == Adoption("spec", 1, "run-earlier", 7)
    assert seen[0] == ("spec", None)
    assert summary.status == "escalated"
    assert _floors(store)[crashed.seq + 1:] == [
        ("turn", carried),
        ("turn", TurnFloor("validate_spec", 1, RUN_ID, 0)),
        ("escalated", None),
    ]
```

- [ ] **Step 7: Run the runtime tier to verify it is green**

Run: `uv run pytest tests/runtime -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/runtime/compile.py tests/runtime/test_exactly_once.py tests/runtime/test_resume.py
git commit -m "feat(runtime): adopt the resumed head in the compiled agent phase"
```

---

### Task 2: A step head consumes the adoption and is never adopted (E9)

**Files:**
- Modify: `src/agent_manager/runtime/compile.py` (`step_phase`, the three lines after its `async def`, currently `:164-166` before Task 1 shifted them)
- Test: `tests/runtime/test_exactly_once.py` (append)

**Interfaces:**
- Consumes: from Task 1's test module, unchanged: `FakeLauncher`, `_dispatches`, `_workflow`, `_runner`, `_go`, `_seed`, `_head`, `_phase_rows`, `_crash_after_step`, `_Crash`, fixtures `store`, `roles`, constants `RUN_ID`, `OTHER_RUN_ID`, `CARD_ID`; `RunDeps.take_adoption(phase, loop)`.
- Produces: `step_phase` calls `deps.take_adoption(phase, loop)` on entry and discards the result.

- [ ] **Step 1: Append the failing step-head test and the E9 rerun test**

Append to `tests/runtime/test_exactly_once.py`:

```python
# ── steps are never adopted (E9) ────────────────────────────────────────────


def test_a_step_caught_in_the_window_runs_again(store, roles, monkeypatch):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_step(monkeypatch, "w")

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    assert ran == ["w"]
    assert _phase_rows(store, "w") == ["started", "done"]
    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "w"
    assert crashed.floor is None  # a step head carries no floor

    summary = _go(wf, store, _runner(store, launcher, roles), resume_from=crashed)

    assert summary.status == "done"
    assert ran == ["w", "w", "z"]  # at-least-once: the step ran again
    assert _dispatches(launcher) == {"a": 1, "b": 1}


def test_a_carried_adoption_does_not_outlive_a_step_head(store, roles, monkeypatch, tmp_path):
    launcher = FakeLauncher()
    ran: list[str] = []
    # Run 1 completes: `a` attempt 1 is `ok` in RUN_ID's journal.
    assert _go(_workflow(ran), store, _runner(store, launcher, roles)).status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}

    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        _seed(other, OTHER_RUN_ID)
        _crash_after_step(monkeypatch, "w")
        with pytest.raises(_Crash):
            _go(_workflow(ran), other, _runner(other, launcher, roles))
        crashed = other.latest_checkpoint(CARD_ID)
        assert _head(crashed.agent) == "w"
        # A floor naming `a`, carried into a resume whose head is the step
        # `w`: the step's turn is the first after resume, so it must end the
        # adoption, and `a` must dispatch rather than reuse run 1's result.
        forged = dataclasses.replace(crashed, floor=TurnFloor("a", 0, RUN_ID, 0))
        runner = _runner(other, launcher, roles)
        summary = _go(_workflow(ran), other, runner, resume_from=forged)
    finally:
        other.close()

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 2}
    assert runner.warnings == []
```

- [ ] **Step 2: Run them to verify the step-head test fails**

Run: `uv run pytest tests/runtime/test_exactly_once.py -v -k "step"`
Expected: `test_a_carried_adoption_does_not_outlive_a_step_head` FAILS on `_dispatches(launcher) == {"a": 2, "b": 2}` (actual `{"a": 1, "b": 2}`: the adoption survived `w` and `a` adopted run 1's attempt). `test_a_step_caught_in_the_window_runs_again` PASSES already — it pins E9's at-least-once rerun, which no change here may break.

- [ ] **Step 3: Consume the adoption in `step_phase`**

In `src/agent_manager/runtime/compile.py`, in `step_phase`, replace:

```python
    async def step_phase(phase: str, loop: int, pool: ContextPool):
        deps = current_run.get()
        deps.running = phase
        p = deps.workflow.phase(phase)
```

with:

```python
    async def step_phase(phase: str, loop: int, pool: ContextPool):
        deps = current_run.get()
        deps.running = phase
        # A step is never adopted and stays at-least-once (exactly-once E9);
        # taking the carried adoption here ends it at the first turn after
        # a resume, so no later agent phase can inherit it.
        deps.take_adoption(phase, loop)
        p = deps.workflow.phase(phase)
```

- [ ] **Step 4: Run the file to verify it passes**

Run: `uv run pytest tests/runtime/test_exactly_once.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/compile.py tests/runtime/test_exactly_once.py
git commit -m "feat(runtime): a step head ends the carried adoption (E9)"
```

---

### Task 3: Pin the windows that must dispatch again

These tests pin behaviour adoption must leave alone: each is expected to PASS against Tasks 1-2. A failure means the adoption change is wrong — fix `compile.py`, never the assertion.

**Files:**
- Test: `tests/runtime/test_exactly_once.py` (append)

**Interfaces:**
- Consumes: from Task 1's test module, unchanged: `FakeLauncher` (incl. `results`, `crash_on`, `on_call`), `_dispatches`, `_workflow(ran, critic=True)`, `_runner`, `_subtask`, `_go`, `_seed`, `_head`, `_next_turn`, `_attempt_statuses`, `_mark_orphans`, `_new_process`, `_crash_after_call_agent`, `_StopDuring`, `_Crash`, fixtures `store`, `roles`, constants `RUN_ID`, `OTHER_RUN_ID`, `STORY_ID`, `CARD_ID`, `REPO`, `FIXED`, `FOUND_JSON`, `REJECTED_JSON`; `runtime_engine.run_subtask_async`.
- Produces: nothing new.

- [ ] **Step 1: Append the dispatch-again tests**

Append to `tests/runtime/test_exactly_once.py`:

```python
# ── the windows that must dispatch again ────────────────────────────────────


def test_w0_a_crash_mid_dispatch_dispatches_again(store, roles):
    # The harness wrote a valid result.json, but the attempt was never judged:
    # it stays `started`, a resume marks it `harness_error`, and an attempt
    # that is not `ok` is never adopted.
    launcher = FakeLauncher(crash_on={("a", 1)})
    wf = _workflow([])

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    assert (paths.attempt_dir(RUN_ID, CARD_ID, "a", 1) / dispatch.RESULT_NAME).is_file()
    crashed = store.latest_checkpoint(CARD_ID)
    assert crashed.floor == TurnFloor("a", 0, RUN_ID, 0)
    _mark_orphans(store)

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 1}
    assert _attempt_statuses(store, "a") == [(1, "harness_error"), (2, "ok")]
    assert runner.warnings == []


def test_a_goto_looped_phase_dispatches_every_iteration_and_is_never_adopted(store, roles):
    # `b` rejects the first `a`, so `a` runs again at loop 1 and dies there
    # mid-dispatch. Its loop-0 attempt 1 is `ok` but sits at the loop-1 floor,
    # so the resume must dispatch `a` a third time rather than reuse it.
    launcher = FakeLauncher(
        results={"b": [REJECTED_JSON, FOUND_JSON]}, crash_on={("a", 2)}
    )
    wf = _workflow([], critic=True)

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    assert _dispatches(launcher) == {"a": 2, "b": 1}
    crashed = store.latest_checkpoint(CARD_ID)
    assert _next_turn(crashed.agent)["kwargs"] == {"phase": "a", "loop": 1}
    assert crashed.floor == TurnFloor("a", 1, RUN_ID, 1)
    _mark_orphans(store)

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 3, "b": 2}
    assert runner.warnings == []
    assert _attempt_statuses(store, "a") == [(1, "ok"), (2, "harness_error"), (3, "ok")]


def test_a_checkpoint_with_no_floor_row_dispatches_again(store, roles, monkeypatch):
    # E11: a row saved before checkpoint_floors existed has no floor; the
    # resume degrades to at-least-once, with no error.
    launcher = FakeLauncher()
    wf = _workflow([])
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    store.connection.execute("DELETE FROM checkpoint_floors")
    store.connection.commit()
    crashed = store.latest_checkpoint(CARD_ID)
    assert crashed.floor is None

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 1}
    assert runner.warnings == []


def test_a_parked_subtask_dispatches_the_next_phase_once(store, roles):
    stop = _StopDuring("a")
    launcher = FakeLauncher(on_call=stop)
    ran: list[str] = []
    wf = _workflow(ran)

    async def go():
        stop.loop = asyncio.get_running_loop()
        return await runtime_engine.run_subtask_async(
            wf,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            clock=lambda: FIXED,
            agent_runner=_runner(store, launcher, roles),
            stop=stop.signal,
        )

    parked_summary = asyncio.run(go())

    assert parked_summary.status == "stopped"
    assert parked_summary.detail == "stopped before b"
    parked = store.latest_checkpoint(CARD_ID)
    assert parked.reason == "parked"
    assert parked.floor == TurnFloor("b", 0, RUN_ID, 0)
    assert _dispatches(launcher) == {"a": 1}

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=parked)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == []
    assert ran == ["w", "z"]


def test_a_mismatched_adoption_is_discarded(store, roles, monkeypatch):
    # `b` succeeded and the process died; the row's floor is forged to name
    # `a`. `take_adoption("b", 0)` discards it, so `b` dispatches again.
    launcher = FakeLauncher()
    wf = _workflow([])
    _crash_after_call_agent(monkeypatch, "b")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "b"
    forged = dataclasses.replace(crashed, floor=TurnFloor("a", 0, RUN_ID, 0))

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=forged)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 2}
    assert runner.warnings == []


def test_a_relaunch_whose_source_journal_is_gone_dispatches_again(
    store, roles, monkeypatch, tmp_path
):
    launcher = FakeLauncher()
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(_workflow([]), store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)
    store.journal.path.unlink()

    _new_process()
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        _seed(other, OTHER_RUN_ID)
        runner = _runner(other, launcher, roles)
        summary = _go(_workflow([]), other, runner, resume_from=crashed)
    finally:
        other.close()

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 1}
    [warning] = runner.warnings
    assert warning.startswith(
        f"phase 'a': attempt ? of run {RUN_ID} was not reused (its journal cannot be read: "
    )
    assert "MissingJournalError" in warning
    assert warning.endswith("); dispatching again")
```

- [ ] **Step 2: Run the file to verify everything passes**

Run: `uv run pytest tests/runtime/test_exactly_once.py -v`
Expected: 15 passed. If one of the six new tests fails, the adoption in `compile.py` adopts where it must not (e.g. ignores the floor, or keys the adoption on the wrong turn) — fix `compile.py`. A failure inside `_mark_orphans` or the stop helper is a fixture bug: fix the helper.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_exactly_once.py
git commit -m "test(runtime): pin the crash windows that must dispatch again"
```

---

### Task 4: Full-suite verification

**Files:** none changed unless a failure below requires it.

**Interfaces:**
- Consumes: everything above.
- Produces: a green `uv run pytest`.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: all pass (the e2e tier stays excluded by default).

- [ ] **Step 2: If a test outside `tests/runtime` fails, classify it before touching anything**

A resume test in `tests/test_cli.py` or `tests/test_orchestrate.py` that drives a real `AgentRunner` and asserts a phase was dispatched again although its head turn already had an `ok` attempt above the checkpoint floor is asserting exactly what E8 changes: update that one assertion to the adopted count plus the one `"was not dispatched again"` warning line, and list the file and test in the result. Any other failure (an envelope or exit-code change, a missing checkpoint row, a step not rerun) is a bug in `compile.py`: fix the code, not the test. Re-run `uv run pytest` until green.

- [ ] **Step 3: Commit any adaptation from Step 2 (skip if none)**

```bash
git add -A tests
git commit -m "test: a resumed head that already succeeded is adopted (E8)"
```

- [ ] **Step 4: Report**

In the task result, state: the code was read as built on `m11/task-let-the-agent-runner-4aed8212` (both prerequisites present, nothing merged); `compile.py` and the new `tests/runtime/test_exactly_once.py` are the deliverable; `tests/runtime/test_resume.py::test_a_carried_floor_survives_a_resume` was adapted (and why); any Step 2 adaptations. Nothing pushed.
