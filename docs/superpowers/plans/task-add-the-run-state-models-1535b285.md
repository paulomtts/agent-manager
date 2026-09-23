<!-- task-pipeline: validated -->
# Subtask 1535b285 — Add the run state models

Parent story: 8831189b "Foundations: paths, run store and journal". Sibling subtasks: fdebc746 (`paths.py`, done, this subtask is blocked_by it) and ef248597 (SQLite store + append-only journal, blocked on this subtask). Source of truth for the shape: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §9 (lines 346-368), decisions D4 and D5 (lines 69-70).

## Scope

One module, `src/agent_manager/models.py`, holding the Pydantic models that describe the state of a run: `Run`, `StoryRun`, `SubtaskRun`, `PhaseRun`, `Attempt`, `Dispatch`, plus the `RunConfig` object §9 draws as `config:` under `Run`. Pure data and validation only — no filesystem, no SQLite, no journal writing, no path construction. The module must import cleanly without touching the environment or disk.

Pydantic rather than dataclasses is deliberate: §4's package layout names `models.py` explicitly as "pydantic models (§9)", and D5's projection/journal duality requires lossless, validated round-tripping (see Observable behaviour #2) plus loud rejection of a stale or mistyped journal line — a guarantee plain dataclasses do not give for free. (D4's own validation-and-retry loop is a separate mechanism: it validates phase result files such as `ExploreResult`/`ImplementResult` against harness output, which this subtask's Scope explicitly places out of scope.) This overrides CLAUDE.md's general "plain dataclasses are fine for internal-only state" guidance for this module specifically.

Out of scope, owned elsewhere: phase result models (`ExploreResult`, `CriticResult`, `PlanResult`, `ImplementResult`, `ReviewResult`) belong to the engine story; path derivation is fdebc746's `paths.py`; persistence, projection rebuild and journal line schema are ef248597. Milestone orchestration (census, levels, parallel stories, integrate) and non-Claude harnesses are out of scope for the whole milestone.

## Observable behaviour

The module exposes the six models plus `RunConfig`, nested exactly as §9 draws them:

- `Run` — `id`, `workflow`, `repo_dir`, `base_branch`, `branch_prefix`, `status`, `started_at`, `config: RunConfig`, `stories: list[StoryRun]`.
- `RunConfig` — `max_concurrent_stories`, `dry_run`, `launcher`, `harness_map` (role to `{harness, model}`).
- `StoryRun` — `card_id`, `title`, `level`, `status`, `tip_branch`, `subtasks: list[SubtaskRun]`, ordered and executed sequentially within the story.
- `SubtaskRun` — `card_id`, `branch`, `base_branch`, `worktree_path`, `status`, `phases: list[PhaseRun]`.
- `PhaseRun` — `name`, `kind`, `status`, `started_at`, `ended_at`, `attempts: list[Attempt]`.
- `Attempt` — `n`, `exit_code`, `duration`, `tokens_in`, `tokens_out`, `cost`, `dispatch: Dispatch`, and the three artifact paths (`prompt.txt`, `result.json`, `stdout.log`).
- `Dispatch` — `harness`, `model`, `role`, `cwd`, `prompt_path`, `result_path`.

Behavioural requirements that follow from the constraints:

1. **A run in flight must be representable.** Per §9 resume semantics, an attempt that was started when the process died is recorded as `started` with no terminal event. So every terminal-only field on `Attempt` and `PhaseRun` (`exit_code`, `duration`, token counts, `cost`, `ended_at`) is optional and absent-by-default, and the status values include a non-terminal started state. Constructing an `Attempt` that carries only `n`, `dispatch` and a started status must validate; the engine can then discard it and re-run the phase.
2. **Round-trip fidelity.** Because the DB is a projection and the journal is truth (D5), a full `Run` must serialise and deserialise losslessly — `Run.model_validate(run.model_dump(mode="json"))` equals the original. This is what lets ef248597 rebuild the projection from journal lines without the model losing information.
3. **Attempt identity is addressable.** A journal line carries run id, card, phase, attempt number and a sequence number; the models must expose those five coordinates so a line can be matched to a node in the tree — run `id`, `SubtaskRun.card_id`, `PhaseRun.name`, `Attempt.n`. The sequence number itself lives in the journal, not in these models.
4. **Defaults keep partial construction cheap.** Nested collections (`stories`, `subtasks`, `phases`, `attempts`, `harness_map`) default to empty, so a run can be built top-down as the engine discovers work.

## Error paths

Validation must fail loudly and with readable messages, since the message is the retry signal:

- Missing required identity fields (`Run.id`, `StoryRun.card_id`, `SubtaskRun.card_id`, `PhaseRun.name`, `Attempt.n`, and `Dispatch.harness`/`role`) raise `pydantic.ValidationError`.
- Unknown status values raise `ValidationError` naming the allowed set — a typo'd status must not silently persist.
- Wrong types where coercion is not wanted (e.g. a non-numeric `Attempt.n`, a non-list `stories`) raise `ValidationError`.
- Out-of-range numerics raise `ValidationError`: `Attempt.n` is a positive integer, `RunConfig.max_concurrent_stories` is a positive integer, `duration`/`cost`/token counts are non-negative when present.
- Unknown extra fields are rejected rather than silently dropped, so a stale journal line or a mistyped key surfaces as an error instead of data loss.

## Test list

Tests live in `tests/test_models.py`, mirroring the source layout per CLAUDE.md, written in the precedent style of `tests/test_paths.py` — plain pytest functions, one behaviour per test, module docstring explaining the why.

Tier, per the repo's own tiering in spec §14 (lines 477-495, tiered by *kind of code*, not by unit/integration/e2e): `models.py` is pure, data-only code with no I/O, so **every test below is in the "pure functions" tier** — plain unit tests in the default `uv run pytest` suite, no temporary git repository, no `brd` board, no fake adapter, no harness launcher, and no `tmp_path`/`monkeypatch` fixtures needed. None of these tests belong to the Steps, Adapters, Engine or End-to-end tiers.

1. A minimal `Run` constructs with only its required fields and empty `stories` — pure functions tier.
2. A fully populated four-level tree (`Run` → `StoryRun` → `SubtaskRun` → `PhaseRun` → `Attempt` → `Dispatch`) constructs and preserves nesting and subtask order — pure functions tier.
3. An `Attempt` in flight — started status, no `exit_code`, no `duration`, no token counts, no `cost` — validates, and the corresponding `PhaseRun` validates with `ended_at` absent (§9 resume) — pure functions tier.
4. `Run.model_validate(run.model_dump(mode="json"))` round-trips a fully populated run without loss (D5 projection/journal duality) — pure functions tier.
5. The five journal coordinates (run id, subtask card id, phase name, attempt number) are reachable from a constructed tree, so a journal line can be located — pure functions tier.
6. Missing a required identity field raises `ValidationError` mentioning that field — pure functions tier.
7. An invalid status value raises `ValidationError` listing the permitted values — pure functions tier.
8. A wrongly typed field (non-numeric `Attempt.n`) raises `ValidationError` — pure functions tier.
9. Out-of-range numerics (`Attempt.n` zero or negative, `max_concurrent_stories` zero, negative `cost`) raise `ValidationError` — pure functions tier.
10. An unexpected extra field raises `ValidationError` rather than being dropped — pure functions tier.
11. `RunConfig` defaults and a populated `harness_map` of role to `{harness, model}` both validate — pure functions tier.
12. Nested collection defaults are independent per instance (no shared mutable default leaking between two `Run` objects) — pure functions tier.
13. Importing `agent_manager.models` performs no I/O and requires no environment variables — pure functions tier.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none

---

# Run State Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/models.py`, the Pydantic tree (`Run` → `StoryRun` → `SubtaskRun` → `PhaseRun` → `Attempt` → `Dispatch`, plus `RunConfig`) that every later phase records its state into.

**Architecture:** One pure, I/O-free module built bottom-up: the leaves (`Dispatch`, `Attempt`) first, then the containers, then the cross-cutting guarantees (JSON round-trip, extra-field rejection, per-instance defaults). All models inherit a private `_Model` base whose `model_config` forbids unknown keys, so a stale journal line fails loudly instead of losing data. Every terminal field is `| None` with a `None` default so an attempt that was in flight when the manager died is representable and discardable on resume.

**Tech Stack:** Python 3.12+, Pydantic v2 (`pydantic>=2.9`, already in `pyproject.toml` dependencies), pytest (`pytest>=9.1.1`, dev group), `uv` for running.

**Spec:** `docs/superpowers/specs/task-add-the-run-state-models-1535b285-design.md` (prepended verbatim above). Upstream source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §9 lines 346-368, D4/D5 lines 69-70, §14 lines 477-495.

## Global Constraints

- Branch `m1/task-add-the-run-state-models-1535b285`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-add-the-run-state-models-1535b285`. All paths below are relative to that worktree root.
- The branch is cut from `origin/m1/task-add-paths-and-the-run-fdebc746`, so `src/agent_manager/paths.py` and `tests/test_paths.py` already exist. Do not modify them, do not import `paths` from `models.py`, and do not assume any other subtask's code exists.
- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md).
- `models.py` is pure data: no `import os`, no `import sqlite3`, no filesystem access, no environment reads, no module-level side effects.
- Every model forbids unknown fields (`model_config = ConfigDict(extra="forbid")`), per the spec's Error paths.
- Every test added by this plan is in spec §14's **"pure functions"** tier: plain pytest functions in `tests/test_models.py`, default `uv run pytest` suite, no git repo, no brd board, no harness, no `tmp_path`.
- Verification: full suite `uv run pytest`. Typecheck: none. Lint: none.
- Out of scope, do not add here: `ExploreResult`/`CriticResult`/`PlanResult`/`ImplementResult`/`ReviewResult`, any store/journal code, any path derivation.

## Review Focus

- **Naive (timezone-less) `started_at`/`ended_at` through `model_dump(mode="json")`** — a journal line written without an offset must round-trip to the same `datetime`, not shift or fail. Test in Task 4.
- **Empty-string identity fields** (`Run.id=""`, `SubtaskRun.card_id=""`) — the spec only names *missing* fields, but an empty id is the same data loss with a worse failure mode downstream; must raise `ValidationError`. Tests in Tasks 2 and 3.
- **`Attempt.n=True`** — `bool` is an `int` in Python, so a truthy flag mistakenly passed as an attempt number must be rejected, not silently stored as attempt 1. Test in Task 1. Plain `Field(gt=0)` alone does **not** reject this: pydantic v2 accepts `bool` wherever `int` is declared, so `n` must be declared `Field(gt=0, strict=True)` (verified: without `strict=True` the Task 1 test suite fails one test, `test_attempt_rejects_a_bool_n`, with `DID NOT RAISE ValidationError`).
- **A negative `exit_code`** — a harness killed on timeout exits `-9`; that is legitimate recorded data and must validate, even though the neighbouring numerics are constrained non-negative. Test in Task 1.
- **`cost`/`duration` of `nan` or `inf`** — a bad usage parse must not slip past a `ge=0` bound (`inf >= 0` is true, `nan` comparisons are false), so inf/nan are rejected explicitly. Test in Task 1.

## File Structure

- `src/agent_manager/models.py` (create) — the whole deliverable: status literals, `_Model` base, `Dispatch`, `Attempt`, `PhaseRun`, `SubtaskRun`, `StoryRun`, `HarnessAssignment`, `RunConfig`, `Run`. Grown leaf-first across Tasks 1-3; Task 4 adds no production code.
- `tests/test_models.py` (create) — all thirteen spec tests plus the five Review Focus tests, grown alongside the module.
- Untouched: `src/agent_manager/__init__.py`, `src/agent_manager/paths.py`, `tests/test_paths.py`, `tests/test_package.py`, `pyproject.toml` (Pydantic is already a dependency).

`HarnessAssignment` is not in the spec's list of exposed models; it exists solely to give `RunConfig.harness_map`'s `{harness, model}` values the same validated, extra-forbidding treatment as everything else, rather than an opaque `dict[str, dict[str, str]]` that would silently swallow a mistyped key. It is a helper of `RunConfig`, not a seventh state node.

---

### Task 1: Dispatch and Attempt (the leaves)

**Files:**
- Create: `src/agent_manager/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `AttemptStatus = Literal["started", "ok", "schema_invalid", "gate_failed", "harness_error"]`
  - `class _Model(BaseModel)` — the shared base of every model in Tasks 2-3. Task 4 adds its `model_config`.
  - `class Dispatch(_Model)`: `harness: str`, `model: str`, `role: str`, `cwd: Path`, `prompt_path: Path`, `result_path: Path` (all required).
  - `class Attempt(_Model)`: `n: int` (>0, `strict=True` so a `bool` is rejected rather than silently coerced to 0/1 — see Review Focus), `dispatch: Dispatch`, `status: AttemptStatus = "started"`, `exit_code: int | None = None`, `duration: float | None = None`, `tokens_in: int | None = None`, `tokens_out: int | None = None`, `cost: float | None = None`, `prompt_path: Path | None = None`, `result_path: Path | None = None`, `stdout_path: Path | None = None`.
  - Test helper `_dispatch() -> models.Dispatch` in `tests/test_models.py`, reused by Tasks 2-4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_models.py`:

```python
"""Behaviour of the run state models (design spec §9).

These models are pure data: nothing here touches the filesystem, git, brd or a
harness, so every test is a plain construction/validation assertion in the
"pure functions" tier of spec §14 and runs in the default `uv run pytest` suite.

The assertions lean on the *messages* pydantic produces, not just on the fact
that it raised: the validation error is what a retrying agent reads, so a status
typo has to name the statuses that would have worked.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_manager import models


def _dispatch() -> models.Dispatch:
    return models.Dispatch(
        harness="claude",
        model="opus",
        role="coder",
        cwd=Path("/repo/.claude/worktrees/m1/task-add-the-run-state-models-1535b285"),
        prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
        result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
    )


def test_dispatch_carries_the_six_launch_coordinates():
    dispatch = _dispatch()
    assert dispatch.harness == "claude"
    assert dispatch.model == "opus"
    assert dispatch.role == "coder"
    assert dispatch.cwd.name == "task-add-the-run-state-models-1535b285"
    assert dispatch.prompt_path.name == "prompt.txt"
    assert dispatch.result_path.name == "result.json"


def test_dispatch_requires_harness_and_role():
    with pytest.raises(ValidationError) as excinfo:
        models.Dispatch(
            model="opus",
            cwd=Path("/repo/wt"),
            prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
            result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
        )
    message = str(excinfo.value)
    assert "harness" in message
    assert "role" in message


def test_attempt_in_flight_has_no_terminal_fields():
    # §9 resume: an attempt the crash caught mid-run is `started` with nothing
    # terminal recorded, and must load back so the engine can discard it.
    attempt = models.Attempt(n=1, dispatch=_dispatch())
    assert attempt.status == "started"
    assert attempt.exit_code is None
    assert attempt.duration is None
    assert attempt.tokens_in is None
    assert attempt.tokens_out is None
    assert attempt.cost is None
    assert attempt.stdout_path is None


def test_attempt_records_a_finished_outcome():
    attempt = models.Attempt(
        n=2,
        dispatch=_dispatch(),
        status="ok",
        exit_code=0,
        duration=12.5,
        tokens_in=1200,
        tokens_out=340,
        cost=0.42,
        prompt_path=Path("/runs/run-1/1535b285/implement.2/prompt.txt"),
        result_path=Path("/runs/run-1/1535b285/implement.2/result.json"),
        stdout_path=Path("/runs/run-1/1535b285/implement.2/stdout.log"),
    )
    assert attempt.n == 2
    assert attempt.status == "ok"
    assert attempt.stdout_path.name == "stdout.log"


def test_attempt_requires_n_and_dispatch():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt()
    message = str(excinfo.value)
    assert "n" in message
    assert "dispatch" in message


def test_attempt_rejects_a_non_numeric_n():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n="first", dispatch=_dispatch())
    assert "n" in str(excinfo.value)


def test_attempt_rejects_a_bool_n():
    # `True` is an int in Python; an attempt numbered True is an upstream bug,
    # not attempt 1.
    with pytest.raises(ValidationError):
        models.Attempt(n=True, dispatch=_dispatch())


def test_attempt_numbers_start_at_one():
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            models.Attempt(n=bad, dispatch=_dispatch())


def test_attempt_rejects_negative_usage_numbers():
    for field, value in (
        ("cost", -0.01),
        ("duration", -5.0),
        ("tokens_in", -1),
        ("tokens_out", -1),
    ):
        with pytest.raises(ValidationError):
            models.Attempt(n=1, dispatch=_dispatch(), **{field: value})


def test_attempt_rejects_nan_and_infinite_usage_numbers():
    # `inf >= 0` is true and every nan comparison is false, so a bad usage parse
    # would sail past a plain lower bound.
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            models.Attempt(n=1, dispatch=_dispatch(), cost=bad)
        with pytest.raises(ValidationError):
            models.Attempt(n=1, dispatch=_dispatch(), duration=bad)


def test_attempt_keeps_a_signal_exit_code():
    # A harness killed on timeout exits -9. That is data, not a validation error.
    attempt = models.Attempt(
        n=1, dispatch=_dispatch(), status="harness_error", exit_code=-9
    )
    assert attempt.exit_code == -9


def test_attempt_rejects_an_unknown_status_naming_the_allowed_set():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n=1, dispatch=_dispatch(), status="finished")
    message = str(excinfo.value)
    for allowed in ("started", "ok", "schema_invalid", "gate_failed", "harness_error"):
        assert allowed in message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: collection error — `ImportError: cannot import name 'models' from 'agent_manager'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/models.py`:

```python
"""Pydantic models describing the state of one agent-manager run.

Spec §9 draws the tree Run -> StoryRun -> SubtaskRun -> PhaseRun -> Attempt ->
Dispatch. This module is that tree and nothing else: pure data and validation,
no filesystem, no SQLite, no journal writing, no path derivation (paths.py owns
that). Importing it must not read the environment or touch disk.

They are pydantic rather than dataclasses because the SQLite projection is
rebuilt from the append-only journal (D5) and a stale or mistyped line must fail
loudly at that boundary instead of quietly producing a half-populated tree. The
validation message is the signal, so the constraints here are chosen to make the
message legible.

Everything terminal is optional: an attempt in flight when the manager died is
recorded as `started` with no exit code, duration, tokens or cost, and resume has
to load that row back before discarding it and re-running the phase.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

AttemptStatus = Literal[
    "started", "ok", "schema_invalid", "gate_failed", "harness_error"
]
"""`started` (no terminal event yet, §9 resume) plus §6's four journalled
outcomes."""


class _Model(BaseModel):
    """Shared base for every state model; Task 4 gives it its `model_config`."""


class Dispatch(_Model):
    """Everything needed to launch one harness process for one attempt."""

    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)
    role: str = Field(min_length=1)
    cwd: Path
    prompt_path: Path
    result_path: Path


class Attempt(_Model):
    """One dispatch of one phase, numbered from 1 within its phase."""

    n: int = Field(gt=0, strict=True)
    dispatch: Dispatch
    status: AttemptStatus = "started"
    exit_code: int | None = None
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    prompt_path: Path | None = None
    result_path: Path | None = None
    stdout_path: Path | None = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py
git commit -m "feat: add Dispatch and Attempt run state models"
```

---

### Task 2: PhaseRun and SubtaskRun

**Files:**
- Modify: `src/agent_manager/models.py` (append after `Attempt`)
- Test: `tests/test_models.py` (append)

**Interfaces:**
- Consumes: `_Model`, `Attempt`, `Dispatch` from Task 1; test helper `_dispatch()`.
- Produces:
  - `Status = Literal["pending", "started", "done", "failed", "escalated"]` — shared by `PhaseRun`, `SubtaskRun`, `StoryRun` and `Run`.
  - `PhaseKind = Literal["agent", "deterministic"]`.
  - `class PhaseRun(_Model)`: `name: str`, `kind: PhaseKind`, `status: Status = "pending"`, `started_at: datetime | None = None`, `ended_at: datetime | None = None`, `attempts: list[Attempt] = []`.
  - `class SubtaskRun(_Model)`: `card_id: str`, `branch: str`, `base_branch: str`, `status: Status = "pending"`, `worktree_path: Path | None = None`, `phases: list[PhaseRun] = []`.

- [ ] **Step 1: Write the failing tests**

Change the import block at the top of `tests/test_models.py` from:

```python
from pathlib import Path
```

to:

```python
from datetime import datetime, timezone
from pathlib import Path
```

Then append to `tests/test_models.py`:

```python
def test_phase_in_flight_has_no_end_time():
    phase = models.PhaseRun(
        name="implement",
        kind="agent",
        status="started",
        started_at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        attempts=[models.Attempt(n=1, dispatch=_dispatch())],
    )
    assert phase.ended_at is None
    assert phase.attempts[0].status == "started"
    assert phase.attempts[0].exit_code is None


def test_phase_defaults_to_pending_with_no_attempts():
    phase = models.PhaseRun(name="verify", kind="deterministic")
    assert phase.status == "pending"
    assert phase.started_at is None
    assert phase.ended_at is None
    assert phase.attempts == []


def test_phase_rejects_an_unknown_status_naming_the_allowed_set():
    with pytest.raises(ValidationError) as excinfo:
        models.PhaseRun(name="implement", kind="agent", status="in_flight")
    message = str(excinfo.value)
    for allowed in ("pending", "started", "done", "failed", "escalated"):
        assert allowed in message


def test_phase_rejects_an_unknown_kind():
    with pytest.raises(ValidationError) as excinfo:
        models.PhaseRun(name="implement", kind="wizard")
    message = str(excinfo.value)
    assert "agent" in message
    assert "deterministic" in message


def test_phase_requires_a_name():
    with pytest.raises(ValidationError) as excinfo:
        models.PhaseRun(kind="agent")
    assert "name" in str(excinfo.value)


def test_subtask_holds_its_phases_in_order():
    subtask = models.SubtaskRun(
        card_id="1535b285",
        branch="m1/task-add-the-run-state-models-1535b285",
        base_branch="m1/task-add-paths-and-the-run-fdebc746",
        worktree_path=Path("/repo/.claude/worktrees/m1/task-add-the-run-state-models-1535b285"),
        status="started",
        phases=[
            models.PhaseRun(name="explore", kind="agent", status="done"),
            models.PhaseRun(name="implement", kind="agent", status="started"),
            models.PhaseRun(name="verify", kind="deterministic"),
        ],
    )
    assert [phase.name for phase in subtask.phases] == [
        "explore",
        "implement",
        "verify",
    ]
    assert subtask.base_branch == "m1/task-add-paths-and-the-run-fdebc746"


def test_subtask_defaults_to_pending_with_no_worktree_yet():
    subtask = models.SubtaskRun(
        card_id="1535b285",
        branch="m1/task-add-the-run-state-models-1535b285",
        base_branch="main",
    )
    assert subtask.status == "pending"
    assert subtask.worktree_path is None
    assert subtask.phases == []


def test_subtask_requires_a_card_id():
    with pytest.raises(ValidationError) as excinfo:
        models.SubtaskRun(branch="m1/x-1535b285", base_branch="main")
    assert "card_id" in str(excinfo.value)


def test_subtask_rejects_an_empty_card_id():
    # An empty id loses the journal coordinate just as thoroughly as a missing
    # one, and fails further downstream.
    with pytest.raises(ValidationError) as excinfo:
        models.SubtaskRun(card_id="", branch="m1/x-1535b285", base_branch="main")
    assert "card_id" in str(excinfo.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `AttributeError: module 'agent_manager.models' has no attribute 'PhaseRun'` on the new tests; the Task 1 tests still pass.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/models.py`, extend the import block from:

```python
from pathlib import Path
from typing import Literal
```

to:

```python
from datetime import datetime
from pathlib import Path
from typing import Literal
```

Add the `Status` and `PhaseKind` aliases directly above the existing `AttemptStatus` alias:

```python
Status = Literal["pending", "started", "done", "failed", "escalated"]
"""Lifecycle of a run, story, subtask or phase. `started` is the non-terminal
state resume keys off (§9)."""

PhaseKind = Literal["agent", "deterministic"]
"""§5: a phase either dispatches a harness or runs a registered function."""
```

Then append after the `Attempt` class:

```python
class PhaseRun(_Model):
    """One phase of one subtask, with every attempt made at it."""

    name: str = Field(min_length=1)
    kind: PhaseKind
    status: Status = "pending"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    attempts: list[Attempt] = Field(default_factory=list)


class SubtaskRun(_Model):
    """One subtask card, driven on its own branch in its own worktree."""

    card_id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    base_branch: str = Field(min_length=1)
    status: Status = "pending"
    worktree_path: Path | None = None
    phases: list[PhaseRun] = Field(default_factory=list)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py
git commit -m "feat: add PhaseRun and SubtaskRun run state models"
```

---

### Task 3: StoryRun, RunConfig and Run

**Files:**
- Modify: `src/agent_manager/models.py` (append after `SubtaskRun`)
- Test: `tests/test_models.py` (append)

**Interfaces:**
- Consumes: `_Model`, `Status`, `SubtaskRun`, `PhaseRun`, `Attempt`, `Dispatch` from Tasks 1-2; test helper `_dispatch()`.
- Produces:
  - `Launcher = Literal["direct", "bwrap", "container"]` (§4 `launcher.py` seam).
  - `class StoryRun(_Model)`: `card_id: str`, `title: str`, `level: int` (>=0), `status: Status = "pending"`, `tip_branch: str | None = None`, `subtasks: list[SubtaskRun] = []`.
  - `class HarnessAssignment(_Model)`: `harness: str`, `model: str`.
  - `class RunConfig(_Model)`: `max_concurrent_stories: int = 1` (>0), `dry_run: bool = False`, `launcher: Launcher = "direct"`, `harness_map: dict[str, HarnessAssignment] = {}`.
  - `class Run(_Model)`: `id: str`, `workflow: str`, `repo_dir: Path`, `base_branch: str`, `branch_prefix: str`, `status: Status = "pending"`, `started_at: datetime | None = None`, `config: RunConfig = RunConfig()`, `stories: list[StoryRun] = []`.
  - Test helper `_full_run() -> models.Run` in `tests/test_models.py`, reused by Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
def test_minimal_run_needs_only_its_identity_fields():
    run = models.Run(
        id="run-2026-09-23-01",
        workflow="task",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    assert run.status == "pending"
    assert run.started_at is None
    assert run.stories == []
    assert run.config.max_concurrent_stories == 1
    assert run.config.dry_run is False
    assert run.config.launcher == "direct"
    assert run.config.harness_map == {}


def test_run_requires_an_id():
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
        )
    assert "id" in str(excinfo.value)


def test_run_rejects_an_empty_id():
    # The run id is the first coordinate on every journal line; "" is not one.
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            id="",
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
        )
    assert "id" in str(excinfo.value)


def test_run_rejects_a_non_list_stories():
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            id="run-2026-09-23-01",
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
            stories="8831189b",
        )
    assert "stories" in str(excinfo.value)


def test_run_config_accepts_a_populated_harness_map():
    config = models.RunConfig(
        max_concurrent_stories=3,
        dry_run=True,
        launcher="bwrap",
        harness_map={
            "coder": models.HarnessAssignment(harness="claude", model="sonnet"),
            "reviewer": {"harness": "claude", "model": "opus"},
        },
    )
    assert config.max_concurrent_stories == 3
    assert config.dry_run is True
    assert config.launcher == "bwrap"
    assert config.harness_map["coder"].model == "sonnet"
    assert config.harness_map["reviewer"].harness == "claude"


def test_run_config_rejects_zero_concurrency():
    with pytest.raises(ValidationError) as excinfo:
        models.RunConfig(max_concurrent_stories=0)
    assert "max_concurrent_stories" in str(excinfo.value)


def test_run_config_rejects_an_unknown_launcher():
    with pytest.raises(ValidationError) as excinfo:
        models.RunConfig(launcher="docker")
    message = str(excinfo.value)
    for allowed in ("direct", "bwrap", "container"):
        assert allowed in message


def test_run_config_rejects_a_harness_map_entry_missing_its_model():
    with pytest.raises(ValidationError) as excinfo:
        models.RunConfig(harness_map={"coder": {"harness": "claude"}})
    assert "model" in str(excinfo.value)


def test_story_rejects_a_negative_level():
    with pytest.raises(ValidationError) as excinfo:
        models.StoryRun(card_id="8831189b", title="Foundations", level=-1)
    assert "level" in str(excinfo.value)


def _full_run() -> models.Run:
    """A run mid-flight: one story, one finished subtask, one in progress."""
    return models.Run(
        id="run-2026-09-23-01",
        workflow="milestone",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
        status="started",
        started_at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        config=models.RunConfig(
            max_concurrent_stories=2,
            harness_map={
                "coder": models.HarnessAssignment(harness="claude", model="sonnet")
            },
        ),
        stories=[
            models.StoryRun(
                card_id="8831189b",
                title="Foundations: paths, run store and journal",
                level=0,
                status="started",
                tip_branch="m1/task-add-paths-and-the-run-fdebc746",
                subtasks=[
                    models.SubtaskRun(
                        card_id="fdebc746",
                        branch="m1/task-add-paths-and-the-run-fdebc746",
                        base_branch="main",
                        worktree_path=Path("/repo/.claude/worktrees/m1/task-add-paths-and-the-run-fdebc746"),
                        status="done",
                        phases=[
                            models.PhaseRun(
                                name="verify",
                                kind="deterministic",
                                status="done",
                                started_at=datetime(2026, 9, 23, 10, 5, tzinfo=timezone.utc),
                                ended_at=datetime(2026, 9, 23, 10, 6, tzinfo=timezone.utc),
                            )
                        ],
                    ),
                    models.SubtaskRun(
                        card_id="1535b285",
                        branch="m1/task-add-the-run-state-models-1535b285",
                        base_branch="m1/task-add-paths-and-the-run-fdebc746",
                        worktree_path=Path("/repo/.claude/worktrees/m1/task-add-the-run-state-models-1535b285"),
                        status="started",
                        phases=[
                            models.PhaseRun(
                                name="explore",
                                kind="agent",
                                status="done",
                                started_at=datetime(2026, 9, 23, 10, 10, tzinfo=timezone.utc),
                                ended_at=datetime(2026, 9, 23, 10, 12, tzinfo=timezone.utc),
                                attempts=[
                                    models.Attempt(
                                        n=1,
                                        dispatch=_dispatch(),
                                        status="ok",
                                        exit_code=0,
                                        duration=31.25,
                                        tokens_in=8000,
                                        tokens_out=1500,
                                        cost=0.31,
                                        prompt_path=Path("/runs/run-1/1535b285/explore.1/prompt.txt"),
                                        result_path=Path("/runs/run-1/1535b285/explore.1/result.json"),
                                        stdout_path=Path("/runs/run-1/1535b285/explore.1/stdout.log"),
                                    )
                                ],
                            ),
                            models.PhaseRun(
                                name="implement",
                                kind="agent",
                                status="started",
                                started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
                                attempts=[models.Attempt(n=1, dispatch=_dispatch())],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def test_full_tree_preserves_nesting_and_subtask_order():
    run = _full_run()
    assert [story.card_id for story in run.stories] == ["8831189b"]
    story = run.stories[0]
    assert story.level == 0
    assert [subtask.card_id for subtask in story.subtasks] == ["fdebc746", "1535b285"]
    in_progress = story.subtasks[1]
    assert [phase.name for phase in in_progress.phases] == ["explore", "implement"]
    assert in_progress.phases[1].attempts[0].dispatch.role == "coder"
    assert in_progress.phases[1].ended_at is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `AttributeError: module 'agent_manager.models' has no attribute 'Run'` on the new tests; Tasks 1-2 tests still pass.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/models.py`, add the `Launcher` alias directly below the `PhaseKind` alias:

```python
Launcher = Literal["direct", "bwrap", "container"]
"""§4: how a harness process is contained when it runs."""
```

Then append after the `SubtaskRun` class:

```python
class StoryRun(_Model):
    """One story card. Its subtasks run sequentially, stacked on each other."""

    card_id: str = Field(min_length=1)
    title: str
    level: int = Field(ge=0)
    status: Status = "pending"
    tip_branch: str | None = None
    subtasks: list[SubtaskRun] = Field(default_factory=list)


class HarnessAssignment(_Model):
    """One entry of `RunConfig.harness_map`: which harness and model a role gets."""

    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)


class RunConfig(_Model):
    """The knobs a run was started with, recorded so resume reuses them."""

    max_concurrent_stories: int = Field(default=1, gt=0)
    dry_run: bool = False
    launcher: Launcher = "direct"
    harness_map: dict[str, HarnessAssignment] = Field(default_factory=dict)


class Run(_Model):
    """The root of the state tree: one invocation of one workflow."""

    id: str = Field(min_length=1)
    workflow: str = Field(min_length=1)
    repo_dir: Path
    base_branch: str = Field(min_length=1)
    branch_prefix: str = Field(min_length=1)
    status: Status = "pending"
    started_at: datetime | None = None
    config: RunConfig = Field(default_factory=RunConfig)
    stories: list[StoryRun] = Field(default_factory=list)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, 31 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py
git commit -m "feat: add StoryRun, RunConfig and Run state models"
```

---

### Task 4: Extra-field rejection, round-trip, journal coordinates and purity

**Files:**
- Modify: `src/agent_manager/models.py` (the `_Model` base only)
- Test: `tests/test_models.py` (append)

**Interfaces:**
- Consumes: all models from Tasks 1-3; test helpers `_dispatch()` and `_full_run()`.
- Produces: `_Model.model_config = ConfigDict(extra="forbid")`, which every model in the module inherits.

- [ ] **Step 1: Write the failing tests**

Change the import block at the top of `tests/test_models.py` from:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_manager import models
```

to:

```python
import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

import agent_manager
from agent_manager import models
```

Then append to `tests/test_models.py`:

```python
def test_full_run_round_trips_through_json():
    # D5: the DB is a projection, the journal is truth. Rebuilding the
    # projection must not lose a field on the way through JSON.
    run = _full_run()
    assert models.Run.model_validate(run.model_dump(mode="json")) == run


def test_round_trip_survives_a_naive_started_at():
    # A timestamp written without an offset must come back as the same instant,
    # not shifted and not rejected.
    naive = datetime(2026, 9, 23, 10, 0)
    run = _full_run().model_copy(update={"started_at": naive})
    restored = models.Run.model_validate(run.model_dump(mode="json"))
    assert restored.started_at == naive
    assert restored == run


def test_round_trip_keeps_an_in_flight_attempt_in_flight():
    run = _full_run()
    restored = models.Run.model_validate(run.model_dump(mode="json"))
    attempt = restored.stories[0].subtasks[1].phases[1].attempts[0]
    assert attempt.status == "started"
    assert attempt.exit_code is None
    assert attempt.cost is None


def test_journal_coordinates_are_reachable_from_the_tree():
    # A journal line carries run id, card, phase, attempt and a sequence number;
    # the first four must locate a node here (the sequence number lives only in
    # the journal).
    run = _full_run()
    subtask = run.stories[0].subtasks[1]
    phase = subtask.phases[1]
    attempt = phase.attempts[0]
    assert (run.id, subtask.card_id, phase.name, attempt.n) == (
        "run-2026-09-23-01",
        "1535b285",
        "implement",
        1,
    )


def test_unknown_fields_are_rejected_at_every_level():
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            id="run-2026-09-23-01",
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
            sequence=7,
        )
    assert "sequence" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n=1, dispatch=_dispatch(), tokens=10)
    assert "tokens" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        models.PhaseRun(name="verify", kind="deterministic", finished_at=None)
    assert "finished_at" in str(excinfo.value)


def test_nested_collection_defaults_are_per_instance():
    first = models.Run(
        id="run-1",
        workflow="task",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    second = models.Run(
        id="run-2",
        workflow="task",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    first.stories.append(
        models.StoryRun(card_id="8831189b", title="Foundations", level=0)
    )
    first.config.harness_map["coder"] = models.HarnessAssignment(
        harness="claude", model="sonnet"
    )
    assert second.stories == []
    assert second.config.harness_map == {}
    assert first.config is not second.config


def test_importing_models_needs_no_environment_and_no_disk():
    # Pure data module: no env reads, no sqlite, no paths.py, so a fresh import
    # with an empty environment must still succeed.
    with mock.patch.dict(os.environ, {}, clear=True):
        sys.modules.pop("agent_manager.models", None)
        try:
            fresh = importlib.import_module("agent_manager.models")
            assert fresh.Run.__name__ == "Run"
            assert not hasattr(fresh, "os")
            assert not hasattr(fresh, "sqlite3")
            assert not hasattr(fresh, "paths")
        finally:
            sys.modules["agent_manager.models"] = models
            agent_manager.models = models
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: `test_unknown_fields_are_rejected_at_every_level` FAILS with `DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>` — pydantic's default is to ignore unknown keys, so `sequence=7` is silently dropped today. The other six new tests pass: they pin guarantees Tasks 1-3 were deliberately built to provide (declared `Path`/`datetime` types, `default_factory` collections), and their passing is the point, not a reason to skip Step 3.

- [ ] **Step 3: Forbid unknown fields on the shared base**

In `src/agent_manager/models.py`, change the import line from:

```python
from pydantic import BaseModel, Field
```

to:

```python
from pydantic import BaseModel, ConfigDict, Field
```

and replace the `_Model` class with:

```python
class _Model(BaseModel):
    """Shared config for every state model.

    Unknown keys are an error, never a silent drop: a journal line from an older
    schema must surface as a validation failure rather than as data loss in the
    rebuilt projection.
    """

    model_config = ConfigDict(extra="forbid")
```

If any of the other six tests failed, the likely causes and fixes in `src/agent_manager/models.py` are:

- Round-trip inequality on `repo_dir`/`worktree_path`: a `Path` must be declared as `Path`, not `str`; check the field types match Task 1-3's Interfaces blocks exactly.
- Round-trip inequality on `started_at`: the field must be `datetime | None`, not `str | None`.
- Extra field still accepted after the change above: some model is missing `_Model` as its base — every model class must read `class X(_Model):`.
- Shared default leaking: a collection field was given a literal default (`= []` or `= {}`) instead of `Field(default_factory=list)` / `Field(default_factory=dict)`, or `config` was given `= RunConfig()` instead of `Field(default_factory=RunConfig)`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS — 38 tests in `tests/test_models.py`, plus the pre-existing `tests/test_paths.py` and `tests/test_package.py` tests, no failures and no errors.

- [ ] **Step 5: Commit**

```bash
git add tests/test_models.py src/agent_manager/models.py
git commit -m "feat: forbid unknown fields and pin journal round-trip of run state models"
```

---

## Coverage map (spec test list → task)

| Spec test | Task |
|---|---|
| 1 minimal `Run` | 3 |
| 2 full four-level tree, subtask order | 3 |
| 3 in-flight `Attempt` and `PhaseRun` without `ended_at` | 1 and 2 |
| 4 JSON round-trip | 4 |
| 5 journal coordinates reachable | 4 |
| 6 missing identity field | 1 (`Dispatch`, `Attempt`), 2 (`PhaseRun`, `SubtaskRun`), 3 (`Run`) |
| 7 invalid status names the allowed set | 1 (`Attempt`), 2 (`PhaseRun`) |
| 8 non-numeric `Attempt.n` | 1 |
| 9 out-of-range numerics | 1 (`n`, `cost`, `duration`, tokens), 3 (`max_concurrent_stories`) |
| 10 extra field rejected | 4 |
| 11 `RunConfig` defaults and `harness_map` | 3 |
| 12 per-instance defaults | 4 |
| 13 import needs no environment or disk | 4 |
