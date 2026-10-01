<!-- task-pipeline: validated -->
# Prove exactly-once end to end and document the guarantee (card 3b6a6c27)

Milestone 11 "exactly-once phases" (4e0d2a6c) → story 5716184f "Proof and documentation" → this subtask (the story's only child). Branch prefix `m11`. This subtask adds no mechanism. It proves the mechanism that sibling stories 5bb73149 (checkpoint floors) and fd673816 (adoption) already built, and it writes down the guarantee.

## Preconditions and gaps

- **Prerequisite code is not on master.** `checkpoint_floors`, `Store.replay_journal`, `AgentRunner.adopt`, `dispatch.read_result`, and the `compile.py` adoption wiring live only on branches `m11/task-save-a-floor-atomically-e600b86a`, `m11/task-compute-the-floor-at-94088f7e`, `m11/task-let-the-agent-runner-4aed8212`, and `m11/task-adopt-the-resumed-head-6ecbe6e2`. There is no `m11-integrate` branch yet. The e2e test below can only pass once those branches are merged underneath it. Do not reimplement any of that mechanism here. If it is missing at implementation time, say so in the result rather than re-creating it.
- **Cited design docs are missing.** `2026-09-27-exactly-once-design.md` (along with `-live-control-` and `-multi-process-`) and the plan `2026-09-27-exactly-once.md` exist nowhere in `docs/superpowers/`. The decision labels E1, E2, and E9 can only be taken from the card text. The E1 table content below is therefore narrowed from the card and its sibling stories, not quoted from a file.
- **Exploration findings were truncated.** The upstream summary was cut at 8000 characters, in the middle of the e2e-helper references. The test-placement rule used below comes from `pyproject.toml` (`addopts = -m "not e2e"`; the `e2e` marker is reserved for the paid real-`claude` test) and from the sibling `tests/e2e/test_milestone_resume.py` docstring ("Default-suite e2e tier … Unmarked on purpose"). It was not taken from the missing text.
- **Code references must be checked against the built code.** References come from master plus the M9/M10 plans. Read the code as built, adapt to it, and state any divergence in the result.

## Scope (four files plus one new test)

1. **`tests/e2e/test_exactly_once.py` (new)**
   - Drives `am run --card <id>` and then `am resume <run-id>` through `CliRunner().invoke(cli.app, ...)` against the fake `claude` on `PATH`: the existing `fake_claude_bin` fixture and a fresh project or board fixture from `tests/e2e/conftest.py`.
   - Kill: monkeypatch `runtime.bridge.call_agent` with a wrapper that has the real signature `(runner, phase, context, rendered)`. The wrapper awaits the real function. When `phase` is `implement`, it raises `class _Killed(BaseException)` after the real call returns. That point is after the dispatch completed and its attempt was journalled `ok`, but before the next turn's checkpoint recorded completion. This follows the `_Crash(BaseException)` convention in `tests/runtime/test_resume.py:42`. No sleeps.
   - The wrapper is removed (or disarmed) before `am resume`.
   - The basename clashes with the sibling's `tests/runtime/test_exactly_once.py`. `--import-mode=importlib` already tolerates that, as `pyproject.toml` notes for `test_integrate.py`.
2. **`README.md`**: add a `## Resuming: what runs again` section next to the existing resume/usage material (before `## Develop`). It contains:
   - The E1 per-phase-kind table:
     - Agent phase whose `ok` attempt was recorded: adopted, not dispatched again. The result file is re-validated and the gates re-run.
     - Agent phase with no `ok` attempt, or only a started or orphaned attempt: dispatched again.
     - Step: may run again (at-least-once) and must be idempotent.
   - A note that the harness's own effects (for example commits or files an agent wrote before a kill) are never transactional. Exactly-once covers am's dispatch of a phase, not what the harness did.
   - A statement that adoption surfaces only as one warnings line, and that the envelope and exit codes are unchanged.
   - Also fix the now-stale sentence in `#### Relaunching resumes` (the "On a `task` run" paragraph, currently: "One exception: a phase that finished just before the process was killed, before the next checkpoint was saved, runs again too, because phases are at-least-once."). That claim is no longer true for agent phases once this card lands; reword it to say an agent phase whose dispatch already completed is adopted rather than re-run, and point to the new `## Resuming: what runs again` section, while keeping the at-least-once statement for steps.
3. **`src/agent_manager/workflow/phases.py`**: give `class Step` (lines 40–48) the docstring: "A step may run more than once for one walk (a resume re-runs a step whose completion was not checkpointed); it must be idempotent." No behavior change.
4. **`docs/superpowers/specs/2026-09-25-pygents-engine-design.md`**:
   - Append to §6 "Known limit, accepted" (around lines 302–305): "Closed for agent phases by milestone 11 (`2026-09-27-exactly-once-design.md`); steps stay at-least-once by contract."
   - In §11 "Deferred", update the bullet "Exactly-once phases (checkpoint on the next turn's `put`)" (around line 422). Mark it delivered for agent phases by milestone 11, mechanism: the floor saved with each checkpoint plus adoption, not an AFTER_PUT checkpoint. Note that steps stay at-least-once. Do not leave it reading as open.
5. **Board**: close nothing by hand.

## Observable behavior the test pins

- The first invoke dies with `_Killed`. The run is left resumable: not `done`, and an open checkpoint exists.
- `am resume <run-id>` exits 0 with envelope `{"ok": true, ...}` and the run status is `done`.
- The fake log (`read_fake_log` or the fake-claude log) shows `implement` dispatched **exactly once** across both invocations. Every agent phase after `implement` (`review`) is dispatched once.
- The worktree branch holds **exactly one** commit with subject `IMPLEMENT_SUBJECT` (`"feat: implement this card"`, from `fake_claude.build_result`).
- The resume envelope's warnings include exactly one line containing `"not dispatched again"` that names `implement`. There are no other new report fields.

## Error paths

- If the resume dispatches `implement` again, the test fails on the dispatch count and on the commit count. That is the regression this test exists to catch.
- If the adopted result fails re-validation or its gates, the resume must re-dispatch, per the sibling stories. This test does not exercise that path; it is covered by the sibling unit and runtime tests.
- The test must fail only when something is miswired. It must not depend on timing.

## Tests

| Test | Tier | Rationale |
|---|---|---|
| `tests/e2e/test_exactly_once.py::test_resume_after_implement_returns_adopts_it` (kill after `implement`'s `call_agent` returns, resume, then assert `done`, one implement dispatch, one implement commit, one "not dispatched again" warning) | Default-suite e2e tier: `tests/e2e/`, **unmarked** (no `@pytest.mark.e2e`) | It uses the fake `claude` through the real CLI, `ClaudeAdapter`, and launcher, like `test_milestone_resume.py`. The `e2e` marker is reserved for the opt-in paid real-`claude` run that `addopts` excludes. |

The README, the `Step` docstring, and the engine-spec edits get no new tests. `uv run pytest` must stay green.

## Out of scope

- Adopting started or orphaned attempts.
- Persisting step results.
- AFTER_PUT or AFTER_TURN checkpoints.
- New report fields.
- Any schema, store, dispatch, or compile change.
- Writing the missing `2026-09-27-*` design docs.

---

# Exactly-once end-to-end proof and documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove end to end, through the real `am` CLI and the fake `claude`, that a resumed `--card` run does not dispatch an `implement` phase again if it already succeeded, and document the per-phase-kind resume guarantee.

**Architecture:** This adds one new default-suite e2e test that kills the manager with a plain `BaseException` right after `implement`'s `bridge.call_agent` returns, then runs `am resume` and checks dispatch counts, commits, the projection and the warning line. It also makes three documentation-only edits: the `Step` docstring, a README section, and the engine design spec's §6 and §11. No production behavior changes.

**Tech Stack:** Python 3, pytest (`--import-mode=importlib`, `asyncio_mode = "auto"`), Typer `CliRunner`, the `brd` and `git` CLIs, `tests/e2e/fake_claude.py`.

**Spec:** `docs/superpowers/specs/task-prove-it-end-to-end-and-3b6a6c27-design.md` (prepended above).

## Notes from reading the code as built (divergences from the spec text)

- **The prerequisite mechanism is already on this branch.** The worktree was cut from `m11/task-adopt-the-resumed-head-6ecbe6e2`, and it already holds:
  - `AgentRunner.adopt` at `src/agent_manager/dispatch.py:652-709`, whose warning text is at `:705-708`;
  - `store.TurnFloor` at `src/agent_manager/store.py:600-611`, with `Checkpoint.floor` at `:632`;
  - the adoption wiring in `src/agent_manager/runtime/compile.py:144-167`, which reads `bridge.call_agent` at call time on line 167;
  - `tests/runtime/test_exactly_once.py`.

  Task 1 Step 1 re-checks this before anything else and stops if it is missing. The spec's first precondition ("not on master") is about master, not about this branch.
- **The commit-count assertion cannot catch the regression alone.** The fake coder is idempotent: `tests/e2e/fake_claude.py:733-738` commits only when `git status --porcelain` is non-empty. So an `implement` dispatched a second time reports `resumed=True` and makes no second commit. The spec's Error-paths line says the regression fails "on the dispatch count and on the commit count". Only the dispatch count (fake log) and the projection's attempt list actually discriminate. The plan keeps the commit assertion because the spec pins it, and relies on the other two to catch the regression. Task 1's RED step shows which assertion fires.
- **The board fixture.** The test uses `milestone_board`, an existing function-scoped fixture from `tests/e2e/conftest.py:283-311` that builds a fresh repo and board through `fresh_project`. It drives subtask `a1`, a child of story A, under the prefix `m3` (`MILESTONE_PREFIX`), exactly as `tests/e2e/test_milestone_run.py:297-323` does for its `--card` kill test. No new fixture is needed.
- **The kill switch is removed with `monkeypatch.setattr(bridge, "call_agent", _REAL_CALL_AGENT)`, not with `monkeypatch.undo()`.** The same function-scoped `monkeypatch` also carries `fresh_project`'s `XDG_DATA_HOME`, and `undo()` would revert that between the two invocations.
- **README line 315 contradicts the new guarantee.** It says a phase that finished just before the kill "runs again too, because phases are at-least-once". The spec does not list this line, but leaving it would make the README contradict its new section. Task 3 rewrites that one sentence to point at the new section. Report this divergence in the result.
- **`2026-09-27-exactly-once-design.md` does not exist** in `docs/superpowers/specs/`. The card mandates the §6 sentence verbatim, so the reference is written as the card gives it, and that file stays a dangling citation. Report this in the result. Writing that doc is out of scope.
- **The spec summary passed to this planner was truncated** at 2000 characters, mid-sentence ("a failed re-validation or gate must lead to a fresh dispat…"). The plan works from the full spec on disk, whose Error-paths section covers that sentence. That summary over-ran its brief, and nothing was guessed from the missing text.

## Global Constraints

- Test tier: default-suite e2e, `tests/e2e/`, **unmarked** (no `@pytest.mark.e2e`). The `e2e` marker is reserved for the paid real-`claude` run that `addopts = "--import-mode=importlib -m \"not e2e\""` excludes.
- Crash = a plain `BaseException` subclass (`_Killed`) raised at a named point. No sleeps, no timing dependence.
- The wrapper has the real signature `call_agent(runner, phase, context, rendered)` (`src/agent_manager/runtime/bridge.py:69-71`) and awaits the real function.
- No schema, store, dispatch, or compile change. No new report field. The CLI envelope and exit codes are unchanged.
- Steps are not adopted. They stay at-least-once and must be idempotent (E9).
- Close nothing on the board by hand. Nothing is pushed.
- Verification: `uv run pytest`.
- No hard-wrapped prose in the README additions. One paragraph is one line, matching the surrounding README.

## Review Focus

1. **A leftover kill switch.** If `bridge.call_agent` stayed patched after the test, every later agent phase in the session would die. Expected: it is restored before `am resume`, and a module-final guard test asserts `bridge.call_agent is _REAL_CALL_AGENT` (Task 1).
2. **Marker drift.** Someone adds `@pytest.mark.e2e` and the proof silently leaves the default suite. Expected: a guard test asserts that no marker reaches the module (Task 1).
3. **A kill that leaves the attempt `started` instead of `ok`.** This would test the W0 re-dispatch path rather than adoption. Expected: before resuming, the projection shows `implement` attempts `== [(1, "ok")]`, and the resume reports `discarded_attempts == []` (Task 1).
4. **A crash checkpoint that does not name `implement`.** If the newest checkpoint were not `implement`'s turn with its floor, a pass could come from something other than adoption. Expected: before resuming, the test asserts `pending_phase == "implement"` and that the floor names `implement` in this run with floor `0` (Task 1).
5. **A warning that names the wrong phase or run, or is duplicated.** Expected: exactly one `"not dispatched again"` line, equal to the full `AgentRunner.adopt` text for `implement`, attempt 1, this run id (Task 1).

---

### Task 1: The end-to-end exactly-once test

**Files:**
- Create: `tests/e2e/test_exactly_once.py`
- Read only (temporary sabotage in Step 3, reverted in Step 5): `src/agent_manager/runtime/compile.py:148`

**Interfaces:**
- Consumes:
  - conftest fixtures `milestone_board` (dict with `root`, `subtasks["A"]`, `branches`), `fake_claude_bin`, `read_fake_log(run_id) -> list[dict]` (entries have a `"phase"` key), and `checkpoint_rows(root, run_id) -> int`;
  - `cli.app`, `cli.resolve_repo_dir`;
  - `store.open_db`, `store.load_run`, `store.latest_run_id`, `store.Store.open(root, run_id).latest_checkpoint(card_id) -> Checkpoint | None`, with `Checkpoint.floor: TurnFloor | None` (fields `phase`, `loop`, `source_run`, `floor`);
  - `runtime.engine.pending_phase(checkpoint) -> str | None`;
  - `runtime.bridge.call_agent`.
- Produces: nothing other tasks use.

- [ ] **Step 1: Confirm the prerequisite mechanism is on this branch**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-prove-it-end-to-end-and-3b6a6c27
grep -n "def adopt" src/agent_manager/dispatch.py
grep -n "was not dispatched again" src/agent_manager/dispatch.py
grep -n "class TurnFloor" src/agent_manager/store.py
grep -n "take_adoption" src/agent_manager/runtime/compile.py
```
Expected: one hit each, at `dispatch.py:652`, `dispatch.py:706`, `store.py:600`, and `compile.py:147` and `:191`. If any is missing, STOP. Do not reimplement it. Report in the result that the sibling branches (`e600b86a`, `94088f7e`, `4aed8212`, `6ecbe6e2`) are not merged underneath.

- [ ] **Step 2: Write the test**

Create `tests/e2e/test_exactly_once.py`:

```python
"""Default-suite e2e tier: a finished agent phase is not dispatched again on resume (card 3b6a6c27).

Milestone 11, exactly-once phases. `am run --card` and `am resume` run
through `CliRunner` on the real `cli.app` with no `runner_factory`, so every
launch goes through `cli.default_runner_factory`, the real
`dispatch.AgentRunner`, the real `ClaudeAdapter` and `launcher.run_direct` to
the fake `claude` first on `PATH`.

The manager is killed with `_Killed`, a plain `BaseException`, right after
`implement`'s `bridge.call_agent` returned: its attempt is journalled `ok`
and its commit is on the branch, but the next turn's checkpoint was never
saved. `am resume` must adopt that recorded result instead of dispatching
`implement` again. The kill is test scaffolding in the manager process
(`runtime/compile.py` reads `bridge.call_agent` at call time); the fake is
never told anything. No sleeps. Unmarked on purpose: it must run on every
`uv run pytest`.

Note: the fake coder commits only when the tree changed
(`fake_claude.build_result`), so a second `implement` would add no second
commit. The dispatch count and the recorded attempts are what catch a
re-dispatch; the commit count pins the spec's "exactly one implement commit".
"""

import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import cli, models, store
from agent_manager.runtime import bridge
from agent_manager.runtime import engine as runtime_engine

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: `milestone_board` derives its branches with it."""

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

IMPLEMENT_SUBJECT = "feat: implement this card"
"""The subject line of the commit the fake coder makes (`fake_claude.build_result`)."""

_REAL_CALL_AGENT = bridge.call_agent
"""The unpatched door, captured at import: what the kill switch wraps and is restored to."""


class _Killed(BaseException):
    """The manager process dying. A plain `BaseException`, so neither the
    engine, pygents nor `CliRunner` swallows it, and not `KeyboardInterrupt`,
    which asyncio re-raises out of the event loop before the engine unwinds."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _latest_run_id(root: Path) -> str:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _latest_checkpoint(root: Path, run_id: str, card_id: str) -> store.Checkpoint:
    opened = store.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        checkpoint = opened.latest_checkpoint(card_id)
    finally:
        opened.close()
    assert checkpoint is not None, (run_id, card_id)
    return checkpoint


def _attempts(root: Path, run_id: str, card_id: str, phase_name: str) -> list[tuple[int, str]]:
    """`(n, status)` of every attempt of `phase_name` the projection holds for `card_id`."""
    run = _load_run(root, run_id)
    return [
        (attempt.n, attempt.status)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.card_id == card_id
        for phase in subtask.phases
        if phase.name == phase_name
        for attempt in phase.attempts
    ]


def _implement_commits(root: Path, branch: str) -> int:
    subjects = _git(root, "log", "--format=%s", f"main..{branch}").splitlines()
    return subjects.count(IMPLEMENT_SUBJECT)


def _dispatches(entries) -> Counter:
    """How many times the fake ran each phase, over every invocation of the run."""
    return Counter(entry["phase"] for entry in entries)


def _run_card(root: Path, card_id: str):
    return CliRunner().invoke(
        cli.app,
        [
            "run",
            "--card",
            card_id,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            PREFIX,
            "--verify",
            VERIFY,
        ],
    )


def _resume(root: Path, run_id: str):
    return CliRunner().invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(root), "--verify", VERIFY]
    )


def _kill_after_implement_returns(monkeypatch) -> None:
    """Kill the manager once, right after `implement`'s dispatch returned.

    The real `call_agent` runs the real `AgentRunner`, which launches the fake,
    validates its result and records the attempt `ok` and the phase `done`;
    only then does the wrapper raise. One-shot.
    """
    armed = {"on": True}

    async def call_agent(runner, phase, context, rendered):
        result = await _REAL_CALL_AGENT(runner, phase, context, rendered)
        if armed["on"] and phase.name == "implement":
            armed["on"] = False
            raise _Killed("killed after implement's dispatch returned")
        return result

    monkeypatch.setattr(bridge, "call_agent", call_agent)


def test_resume_after_implement_returns_adopts_it(
    milestone_board, fake_claude_bin, read_fake_log, checkpoint_rows, monkeypatch
):
    """Killed after `implement` succeeded; `am resume` finishes the run `done`
    with `implement` dispatched once in total, one implement commit, and one
    "not dispatched again" warning."""
    root = milestone_board["root"]
    a1 = milestone_board["subtasks"]["A"][0]
    branch = milestone_board["branches"][a1]
    _kill_after_implement_returns(monkeypatch)

    # First invocation: the manager dies after implement's dispatch returned.
    with pytest.raises(_Killed):
        _run_card(root, a1)

    run_id = _latest_run_id(root)
    assert _load_run(root, run_id).status == "started"
    assert checkpoint_rows(root, run_id) > 0
    # Review Focus 4: the newest checkpoint is implement's own turn, with its floor.
    crashed = _latest_checkpoint(root, run_id, a1)
    assert crashed.reason == "turn"
    assert runtime_engine.pending_phase(crashed) == "implement"
    assert crashed.floor is not None
    assert (crashed.floor.phase, crashed.floor.source_run, crashed.floor.floor) == (
        "implement",
        run_id,
        0,
    )
    # Review Focus 3: the dispatch finished and was judged, not orphaned.
    assert _attempts(root, run_id, a1, "implement") == [(1, "ok")]
    assert _dispatches(read_fake_log(run_id)) == Counter(AGENT_PHASES[:6])
    assert _implement_commits(root, branch) == 1

    # Review Focus 1: the kill switch is removed, not just disarmed, before resume.
    monkeypatch.setattr(bridge, "call_agent", _REAL_CALL_AGENT)

    # Second invocation: am resume <run-id>.
    result = _resume(root, run_id)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    # The regression this test exists for: implement dispatched a second time.
    assert _dispatches(read_fake_log(run_id)) == Counter(AGENT_PHASES)
    assert _attempts(root, run_id, a1, "implement") == [(1, "ok")]
    assert data["status"] == "done", data
    assert data["resumed_from"] == "implement"
    assert data["discarded_attempts"] == []
    # Review Focus 5: exactly one adoption line, naming implement, attempt 1, this run.
    reused = [line for line in data["warnings"] if "not dispatched again" in line]
    assert reused == [
        f"phase 'implement' was not dispatched again: attempt 1 of run {run_id} "
        "had already succeeded (result reused)"
    ], data["warnings"]
    assert _implement_commits(root, branch) == 1
    assert _load_run(root, run_id).status == "done"


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Review Focus 2: no `e2e` marker may reach this module, or the
    exactly-once proof stops running on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_no_kill_switch_is_left_armed_for_later_tests():
    """Review Focus 1: the kill switch goes through the function-scoped
    `monkeypatch`; it must be gone once its test ends. Kept last in the module."""
    assert bridge.call_agent is _REAL_CALL_AGENT
```

- [ ] **Step 3: Sabotage adoption to watch the test fail (RED)**

The mechanism already exists, so RED comes from temporarily disabling it. In `src/agent_manager/runtime/compile.py`, change line 148 from

```python
        adopt = getattr(deps.agent_runner, "adopt", None)
```

to

```python
        adopt = None
```

Do not commit this.

- [ ] **Step 4: Run the test and verify it fails for the right reason**

Run: `uv run pytest tests/e2e/test_exactly_once.py::test_resume_after_implement_returns_adopts_it -v`

Expected: FAIL on the line `assert _dispatches(read_fake_log(run_id)) == Counter(AGENT_PHASES)` that follows the resume, with the left side showing `'implement': 2`. Every assertion before the resume passes. If the failure is anywhere else (for example `pytest.raises(_Killed)` not raising, a non-zero `exit_code`, or the floor assertion), the test is miswired: fix the test, not the code.

- [ ] **Step 5: Revert the sabotage**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-prove-it-end-to-end-and-3b6a6c27
git checkout -- src/agent_manager/runtime/compile.py
git status --porcelain
```
Expected: `git status --porcelain` lists only `?? tests/e2e/test_exactly_once.py`.

- [ ] **Step 6: Run the module and verify it passes (GREEN)**

Run: `uv run pytest tests/e2e/test_exactly_once.py -v`

Expected: 3 passed (`test_resume_after_implement_returns_adopts_it`, `test_this_module_runs_in_the_default_suite_unmarked`, `test_no_kill_switch_is_left_armed_for_later_tests`).

- [ ] **Step 7: Confirm the basename clash with `tests/runtime/test_exactly_once.py` collects cleanly**

Run: `uv run pytest tests/e2e/test_exactly_once.py tests/runtime/test_exactly_once.py -q`

Expected: all pass, with no "import file mismatch" error.

- [ ] **Step 8: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-prove-it-end-to-end-and-3b6a6c27
git add tests/e2e/test_exactly_once.py
git commit -m "test: prove a resumed card run adopts implement end to end"
```

---

### Task 2: Document that a step must be idempotent

**Files:**
- Modify: `src/agent_manager/workflow/phases.py:40-48`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. This is a docstring only. `Workflow.digest()` keys on phase fields and callables' `module.qualname`, not on the `Step` class docstring, so checkpoint digests do not change.

This task adds no test: the spec says the docstring gets none, and it changes no behavior. Its check is that the existing suite stays green.

- [ ] **Step 1: Add the docstring**

In `src/agent_manager/workflow/phases.py`, replace

```python
@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[..., Any]
```

with

```python
@dataclass(frozen=True)
class Step:
    """A step may run more than once for one walk (a resume re-runs a step whose completion was not checkpointed); it must be idempotent."""

    name: str
    run: Callable[..., Any]
```

- [ ] **Step 2: Run the workflow, runtime and exactly-once tests**

Run: `uv run pytest tests/workflow tests/runtime tests/e2e/test_exactly_once.py -q`

Expected: all pass. In particular, `tests/workflow/test_phases.py`'s digest tests are unchanged.

- [ ] **Step 3: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-prove-it-end-to-end-and-3b6a6c27
git add src/agent_manager/workflow/phases.py
git commit -m "docs: a Step must be idempotent (exactly-once E9)"
```

---

### Task 3: README section and engine design spec

**Files:**
- Modify: `README.md:315` (the `task`-run resume paragraph) and insert a new section between line 408 (`for everything deferred.`) and line 410 (`## Develop`)
- Modify: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md:302-305` (§6 "Known limit, accepted") and `:422` (§11 "Deferred")

**Interfaces:**
- Consumes: the warning text from `src/agent_manager/dispatch.py:705-708`: `phase '<name>' was not dispatched again: attempt <n> of run <run-id> had already succeeded (result reused)`. The decline text is at `:724-727`: `phase '<name>': attempt <n> of run <run-id> was not reused (<why>); dispatching again`.
- Produces: the README anchor `#resuming-what-runs-again`.

Docs only, so no test, per the spec. The check is `uv run pytest` staying green.

- [ ] **Step 1: Fix the stale at-least-once sentence in README line 315**

In `README.md`, replace exactly

```
One exception: a phase that finished just before the process was killed, before the next checkpoint was saved, runs again too, because phases are at-least-once.
```

with

```
A phase that finished just before the process was killed, before the next checkpoint was saved, depends on its kind: an agent phase is adopted and not dispatched again, and a step runs again (see [Resuming: what runs again](#resuming-what-runs-again)).
```

(The rest of that line, from `Attempts left recorded` onward, stays as it is.)

- [ ] **Step 2: Add the "Resuming: what runs again" section before `## Develop`**

In `README.md`, replace exactly

```
for everything deferred.

## Develop
```

with

````
for everything deferred.

## Resuming: what runs again

`am resume <run-id>`, and a relaunch that continues an open checkpoint from an earlier run, go on at the turn the newest checkpoint saved, which is before the interrupted phase ran. Whether that phase runs again depends on its kind and on what was recorded before the process stopped:

| Phase kind, and what was recorded before the stop | On resume |
|---|---|
| Agent phase with an `ok` attempt recorded for this turn (it finished, but the next checkpoint was not saved) | Adopted, not dispatched again. Its result file is read and validated again, and the phase's gates run again against the resumed context. If either fails, the result is not reused and the phase is dispatched again. |
| Agent phase with no `ok` attempt, or only an attempt left `started` (marked `harness_error` on resume) | Dispatched again. An attempt that never finished is never adopted, even when its result file looks valid. |
| Step (`worktree`, `docs_commit`, `verify`, ...) | May run again. Steps are at-least-once, and every step must be idempotent. |

An adoption shows only as one line in the report's `warnings`:

```
phase 'implement' was not dispatched again: attempt 1 of run <run-id> had already succeeded (result reused)
```

A recorded result that no longer holds up is dispatched again, with one warning line `phase '<name>': attempt <n> of run <run-id> was not reused (<why>); dispatching again`. The envelope shape and the exit codes are the same as for any other resume.

Exactly-once covers am's dispatch of an agent phase, not what the harness did. The harness's own effects are never transactional: commits, files written in the worktree, or anything else an agent did before the kill stay as they are, whether the phase is then adopted or dispatched again. A phase that is dispatched again finds that work already in its worktree; `implement`, for example, resumes from git and the `Plan-Hash` trailers.

## Develop
````

- [ ] **Step 3: Append the closing sentence to §6 of the engine design spec**

In `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`, replace exactly

```
phases are at-least-once. Inside a phase, git and the Plan-Hash trailers remain what
`implement` resumes from.
```

with

```
phases are at-least-once. Inside a phase, git and the Plan-Hash trailers remain what
`implement` resumes from. Closed for agent phases by milestone 11 (`2026-09-27-exactly-once-design.md`); steps stay at-least-once by contract.
```

- [ ] **Step 4: Mark the §11 "Deferred" bullet delivered**

In the same file, replace exactly

```
- Exactly-once phases (checkpoint on the next turn's `put`).
```

with

```
- ~~Exactly-once phases (checkpoint on the next turn's `put`).~~ Delivered for agent
  phases by milestone 11 (`2026-09-27-exactly-once-design.md`), by a different
  mechanism: a floor saved with each checkpoint, and adoption of the recorded `ok`
  attempt on resume, not a checkpoint on the next turn's `put`. Steps stay
  at-least-once by contract.
```

(This file hard-wraps at about 88 columns, so the bullet follows the file's own style. The README additions do not hard-wrap.)

- [ ] **Step 5: Check the edits landed and nothing else moved**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-prove-it-end-to-end-and-3b6a6c27
grep -n "^## Resuming: what runs again" README.md
grep -n "runs again too, because phases are at-least-once" README.md
grep -n "Closed for agent phases by milestone 11" docs/superpowers/specs/2026-09-25-pygents-engine-design.md
grep -n "Delivered for agent" docs/superpowers/specs/2026-09-25-pygents-engine-design.md
git diff --stat
```
Expected:
- one heading hit, just above `## Develop`;
- no hit for the old sentence;
- one hit in §6;
- one hit in §11;
- `git diff --stat` lists only `README.md` and `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`.

- [ ] **Step 6: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-prove-it-end-to-end-and-3b6a6c27
git add README.md docs/superpowers/specs/2026-09-25-pygents-engine-design.md
git commit -m "docs: what a resume runs again (exactly-once for agent phases)"
```

---

### Task 4: Full verification

**Files:** none.

- [ ] **Step 1: Run the whole suite**

Run: `cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-prove-it-end-to-end-and-3b6a6c27 && uv run pytest`

Expected: all pass, with the paid `e2e`-marked tests deselected. `tests/e2e/test_exactly_once.py` is among the collected tests (3 passed).

- [ ] **Step 2: Confirm the tree is clean and nothing on the board was touched by hand**

Run: `git status --porcelain && git log --oneline -3`

Expected: no output from `status`. The last three commits are Task 3's, Task 2's and Task 1's. Close no card by hand.

- [ ] **Step 3: Write the result note**

In the subtask result, state these divergences from the spec:
1. The mechanism was already on this branch, cut from `6ecbe6e2`.
2. The fake coder is idempotent, so the commit count alone cannot catch a re-dispatch; the fake-log dispatch count and the projection's attempts do.
3. README line 315's stale at-least-once sentence was rewritten to point at the new section.
4. `2026-09-27-exactly-once-design.md`, cited in the §6 and §11 edits as the card mandates, does not exist in the repo.
