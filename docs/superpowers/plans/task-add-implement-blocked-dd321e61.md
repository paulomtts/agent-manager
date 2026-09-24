<!-- task-pipeline: validated -->
# Subtask dd321e61: add `implement_blocked_gate`

Parent story: 8a174c56 ("Seam: a blocked coder must stop the subtask"). Source decision: `docs/superpowers/specs/2026-09-24-orchestration-design.md`, O7 (lines 103-110). This card narrows O7 and does not extend it.

## Problem

`ImplementResult.blocked` (`src/agent_manager/results.py:91-105`) is never read. When a coder reports that it cannot proceed, for example because the baseline suite was already red or the plan hash could not be computed, `review` runs anyway, and the subtask can end `done` on partial work.

## Scope

1. **Gate.** Add `implement_blocked_gate(result)` to `src/agent_manager/steps/reducers.py`. It is a pure function in the same style as `verification_passed_gate` (`reducers.py:322`).
   - The parameter must be named `result`, because the engine binds gate arguments by parameter name. The implement result arrives as a `model_dump(mode="json")` dict under both `result` and `implement` (`dispatch.gate_values`).
   - If `result` is a Mapping and `result.get("blocked") is True`, return `{"blocked": "implement", "detail": <blocked_reason>}`. The detail is `str(blocked_reason or "").strip()`. If that is empty, use a fallback detail saying the coder reported blocked but gave no reason.
   - If `result` is a Mapping and `blocked` is not identically `True`, return `None` to pass.
   - If `result` is not a Mapping, return a `{"blocked": "implement", "detail": ...}` verdict that says there was no implement result to judge. Do not raise. This follows the module docstring's rule that malformed content produces a verdict, not an exception.
2. **Registry.** In `src/agent_manager/workflow/registry.py`:
   - Add `"implement_blocked_gate"` to the sorted `BUILTIN_FUNCTION_NAMES` tuple (~line 210), between `"exploration_output_gate"` and the `"plan_check…"` entries.
   - Add `registry.register("implement_blocked_gate", reducers.implement_blocked_gate)` in `default_registry` (~line 240), next to the other reducers. Register the function directly, with no wrapper.
   - Updating the reducer counts in the docstrings is optional.
3. **Workflow.** In `src/agent_manager/workflow/builtin/task.yaml`, add `gates: [implement_blocked_gate]` to the `implement` phase. Do **not** add a `retry:` block. The phase stays non-retryable, because re-dispatching the same brief would repeat the same answer.

## Observable behaviour

- A healthy implement result (`blocked: false`) passes the gate, and the run proceeds to `review` exactly as it does today.
- A result with `blocked: true` produces a gate verdict. `implement` is an agent phase, so its gates run in `dispatch.evaluate_gates` (`dispatch.py:279`, called at ~line 512), not in `engine._evaluate_gates` (that one serves deterministic phases only and `_GateFailed` is not involved). The verdict becomes a `gate_failed` attempt whose detail is `phase 'implement' gate 'implement_blocked_gate' failed: blocked=implement, detail=<reason>`. With no `retry:` block the budget is 1 attempt (`dispatch.py:413`), so the failure is non-retryable and the runner raises `AgentPhaseFailed`, which the engine escalates. The subtask is escalated at phase `implement`, and the failure detail carries the coder's `blocked_reason`. `review` is never dispatched and no review attempt is recorded.

## Error paths

- `blocked: true` with a `blocked_reason` that is `None`, empty, or only whitespace still blocks, with the fallback "gave no reason" detail.
- A non-Mapping `result` blocks with a "no implement result to judge" detail. It never raises.
- Truthy values of `blocked` that are not `True`, such as `"true"` or `1`, pass. This matches the identity check in `verification_passed_gate`. Pydantic validation of `ImplementResult` upstream is what guarantees a real bool.

## Tests

Tier placement follows `docs/superpowers/specs/2026-09-23-agent-manager-design.md` section 14 (lines 477-492).

1. `tests/steps/test_reducers.py`: **pure-function unit tier**, because `steps/reducers.py` is a pure module. Add `implement_blocked_gate` to the imports at the top of the file.
   - A normal result (`blocked: false`) returns `None`.
   - `blocked: true` with a reason returns `{"blocked": "implement", "detail": <that reason>}`.
   - `blocked: true` with the reason missing, `None` or empty returns a `blocked: implement` verdict whose detail says the coder gave no reason.
2. `tests/workflow/test_builtin_task.py`: the existing **builtin-document tests**, which check the YAML against the registry (the pure/document level).
   - Change the assertion at ~line 225 in `test_implement_is_handed_the_plan_hash_last…` from `phase.gates == []` to `phase.gates == ["implement_blocked_gate"]`, and assert that the implement phase has no retry.
   - `test_the_document_still_names_exactly_the_gates_this_suite_covers` (~line 473) hard-codes the gate list; insert `("implement", "implement_blocked_gate")` there too, after the `validate_plan` entry and before `("review", "review_gate")`. `GATED_PHASES` itself (~line 465) is derived from the document and needs no edit.
   - The existing parametrised bind and healthy-run tests (~lines 258-280) pick up the new gate through `_implement_result()`, and must pass unchanged.
3. `tests/test_engine.py`: **Engine tier**, driven by a fake adapter that returns canned result files, one of them gate-failing. Load the real workflow with `load_builtin("task", _registry(...))`, as at lines 1181, 1647 and 1666, or use the FakeAdapter/FakeLauncher pattern from `tests/test_dispatch.py:173-265`. The canned implement result has `blocked: true` and a reason. Assert that:
   - `summary.status == "escalated"`.
   - The failure is at phase `implement` and its detail contains the reason.
   - The store has no attempt row for `review`.
   - The launcher never received a `review` dispatch.
   - The existing sync checks (`tests/workflow/test_registry.py:104` and `tests/test_engine.py:1175`, which compare `BUILTIN_FUNCTION_NAMES` with the names in task.yaml) must still pass.
4. **Production-wiring tier** (`tests/e2e`, fake `claude`). No new test is needed here. The healthy fake reports `blocked: false`, so the whole default suite, `tests/e2e` included, must stay green under `uv run pytest`. The fake `claude` must not learn anything beyond what the brief tells it.

## Out of scope

Everything in the addendum's section 4 that belongs to sibling cards: parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, and verification discovery. There is no retry block on `implement` and no change to the coder prompt, which already asks the coder to report `blocked: true` with a reason.

---

# `implement_blocked_gate` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A coder that reports `blocked: true` stops its subtask at `implement`, escalated, with the coder's reason in the failure detail, and `review` is never dispatched.

**Architecture:** One new pure gate in `src/agent_manager/steps/reducers.py`, registered as the bare function in `src/agent_manager/workflow/registry.py`, and named in the `implement` phase's `gates:` list in `src/agent_manager/workflow/builtin/task.yaml`. The existing agent-phase gate path (`dispatch.evaluate_gates` inside `dispatch.AgentRunner._attempt`) turns the verdict into a `gate_failed` attempt; with no `retry:` block the budget is 1, so `AgentRunner` raises `AgentPhaseFailed` and `engine.run_subtask` escalates. No engine or dispatch code changes.

**Tech Stack:** Python 3, Pydantic, PyYAML-backed workflow loader, pytest, run via `uv`.

**Spec:** `docs/superpowers/specs/task-add-implement-blocked-dd321e61-design.md` (prepended verbatim above).

## Global Constraints

- The gate's parameter is named exactly `result` (the engine binds gate arguments by parameter name).
- Blocked verdict shape is exactly `{"blocked": "implement", "detail": <str>}`; pass is `None`.
- `blocked` is judged by identity: `result.get("blocked") is True`. Truthy stand-ins (`"true"`, `1`) pass.
- Detail is `str(blocked_reason or "").strip()`, with a fallback containing "gave no reason" when empty.
- A non-Mapping `result` returns a verdict containing "no implement result to judge" and never raises.
- The registry registers `reducers.implement_blocked_gate` itself, no wrapper.
- `BUILTIN_FUNCTION_NAMES` stays sorted; the new name goes between `"exploration_output_gate"` and `"plan_check.find_validated_plan"`.
- The `implement` phase in `task.yaml` gets `gates: [implement_blocked_gate]` and NO `retry:` block.
- No change to the coder prompt, to `engine.py`, to `dispatch.py`, or to `tests/e2e/fake_claude.py`.
- The branch is `m3/task-add-implement-blocked-dd321e61`, cut fresh from master: do not assume any other subtask's code exists.
- Verification: `uv run pytest` (whole default suite, `tests/e2e` included) must be green at the end.

## Review Focus

1. A truthy-but-not-`True` `blocked` (`"true"`, `1`) — a reasonable reader might expect a block, but the spec says pass (identity, mirroring `verification_passed_gate`); pinned by `test_implement_blocked_gate_passes_a_truthy_stand_in_for_blocked` in Task 1.
2. A whitespace-only `blocked_reason` (`"   "`, `"\n\t"`) — must still block with the "gave no reason" fallback, never an empty detail; pinned by the parametrised fallback test in Task 1.
3. A non-Mapping result (`None`, a list, a string) — must yield a verdict, not a `TypeError`/`AttributeError` that `dispatch.evaluate_gates` would turn into a *fatal broken-gate* message blaming the gate; pinned by `test_implement_blocked_gate_blocks_anything_that_is_not_a_result_mapping` in Task 1.
4. A healthy result that still carries a stale `blocked_reason` (`blocked: false, blocked_reason: "was blocked earlier"`) — must pass, since only `blocked` decides; pinned by `test_implement_blocked_gate_ignores_a_reason_when_not_blocked` in Task 1.
5. A blocked coder being re-dispatched — the phase has no retry, so exactly one coder dispatch must happen; pinned by the `launcher.roles == ["coder"]` assertion in the Engine-tier test in Task 2.

---

### Task 1: The pure `implement_blocked_gate` reducer

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (append after `verification_passed_gate`, which ends at line 343, before the `critic_blockers_gate` comment block at line 346)
- Test: `tests/steps/test_reducers.py` (imports at lines 12-24; new tests inserted after line 570, the end of `test_verification_passed_gate_blocks_anything_that_is_not_a_result_mapping`)

**Interfaces:**
- Consumes: `reducers._js_text(value: object) -> str` (existing, `reducers.py:130`), `collections.abc.Mapping` (already imported at `reducers.py:20`).
- Produces: `implement_blocked_gate(result: object) -> dict[str, str] | None` in `agent_manager.steps.reducers`. Returns `None` to pass, else `{"blocked": "implement", "detail": str}`. Task 2 registers this exact callable under the name `"implement_blocked_gate"`.

- [ ] **Step 1: Add the import**

In `tests/steps/test_reducers.py`, replace:

```python
    exploration_output_gate,
    is_plan_hash,
```

with:

```python
    exploration_output_gate,
    implement_blocked_gate,
    is_plan_hash,
```

- [ ] **Step 2: Write the failing tests**

In `tests/steps/test_reducers.py`, directly after the function `test_verification_passed_gate_blocks_anything_that_is_not_a_result_mapping` (which ends with `assert "no verification result" in verdict["detail"]`), insert:

```python


# ── implement_blocked_gate ───────────────────────────────────────────────────
# Decision O7: the coder REPORTS that it cannot proceed and never decides what
# happens next; this gate is the stop. The input is the snake_case
# `model_dump(mode="json")` of a validated `ImplementResult`.

IMPLEMENT_REPORT = "stopped before writing any code; see blocked_reason"


def _implement(blocked, blocked_reason=None, **extra):
    return {
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "resumed": False,
        "plan_hash": "a1b2c3d4",
        "report": IMPLEMENT_REPORT,
        **extra,
    }


def test_implement_blocked_gate_passes_a_coder_that_was_not_blocked():
    assert implement_blocked_gate(_implement(False)) is None


def test_implement_blocked_gate_blocks_at_implement_with_the_coders_reason():
    verdict = implement_blocked_gate(
        _implement(True, "the baseline suite was already red: 3 failed")
    )
    assert verdict == {
        "blocked": "implement",
        "detail": "the baseline suite was already red: 3 failed",
    }


def test_implement_blocked_gate_strips_the_reason_it_carries():
    verdict = implement_blocked_gate(_implement(True, "  plan hash unavailable \n"))
    assert verdict == {"blocked": "implement", "detail": "plan hash unavailable"}


@pytest.mark.parametrize("useless", [None, "", "   ", "\n\t "])
def test_implement_blocked_gate_still_blocks_when_the_coder_gave_no_reason(useless):
    verdict = implement_blocked_gate(_implement(True, useless))
    assert verdict["blocked"] == "implement"
    assert "gave no reason" in verdict["detail"]


def test_implement_blocked_gate_still_blocks_when_the_reason_key_is_absent():
    verdict = implement_blocked_gate({"blocked": True})
    assert verdict["blocked"] == "implement"
    assert "gave no reason" in verdict["detail"]


@pytest.mark.parametrize("truthy", ["true", 1, "yes", ["x"]])
def test_implement_blocked_gate_passes_a_truthy_stand_in_for_blocked(truthy):
    """Strict identity, like `verification_passed_gate`: Pydantic validation of
    `ImplementResult` upstream guarantees a real bool, so only `True` blocks."""
    assert implement_blocked_gate(_implement(truthy, "ignored")) is None


def test_implement_blocked_gate_ignores_a_reason_when_not_blocked():
    assert implement_blocked_gate(_implement(False, "was blocked earlier")) is None


@pytest.mark.parametrize("dead", [None, "blocked", ["blocked"], 7, True])
def test_implement_blocked_gate_blocks_anything_that_is_not_a_result_mapping(dead):
    verdict = implement_blocked_gate(dead)
    assert verdict["blocked"] == "implement"
    assert "no implement result to judge" in verdict["detail"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'implement_blocked_gate' from 'agent_manager.steps.reducers'`.

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/steps/reducers.py`, directly after the `verification_passed_gate` function (after its final `    }` line, before the comment line `# The `validate_spec` / `validate_plan` gate (`builtin/task.yaml` lines 40 and`), insert:

```python


# The `implement` phase's gate (`builtin/task.yaml`), decision O7. The coder is
# told to REPORT `blocked: true` with a reason when it cannot proceed (a red
# baseline, a plan hash it could not compute) and never to decide what happens
# next -- for the reason `review_gate` exists: an agent that both measures and
# judges can talk itself out of the judgement. Without this gate `review` ran
# anyway and a subtask could reach `done` on partial work. Identity on `True`,
# as in `verification_passed_gate`: `ImplementResult` is validated upstream, so
# a real bool is guaranteed and a truthy stand-in is not a report of anything.
def implement_blocked_gate(result: object) -> dict[str, str] | None:
    """``None`` when the coder was not blocked, else a blocked verdict."""
    if not isinstance(result, Mapping):
        return {
            "blocked": "implement",
            "detail": (
                "no implement result to judge: the implement phase returned "
                f"{_js_text(result)} instead of a result mapping, so nothing "
                "established that the coder finished its work."
            ),
        }
    if result.get("blocked") is not True:
        return None
    detail = str(result.get("blocked_reason") or "").strip()
    return {
        "blocked": "implement",
        "detail": detail
        or "the coder reported blocked: true but gave no reason",
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS, every test in the file, including all new `test_implement_blocked_gate_*` cases.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(reducers): add implement_blocked_gate for a coder that reports blocked"
```

---

### Task 2: Wire the gate onto `implement` and prove a blocked coder escalates

This task changes the registry and the document together: `tests/workflow/test_registry.py:102-104` asserts the registry names, `BUILTIN_FUNCTION_NAMES` and the names in `task.yaml` all agree, so none of the three can move alone.

**Files:**
- Modify: `src/agent_manager/workflow/registry.py:210-224` (`BUILTIN_FUNCTION_NAMES`), `:237` (docstring count), `:247-253` (reducer registrations)
- Modify: `src/agent_manager/workflow/builtin/task.yaml:65-69` (`implement` phase)
- Test (document tier): `tests/workflow/test_builtin_task.py:225` and `:473-481`, plus one new test at the end of the file
- Test (document tier): `tests/workflow/test_registry.py:85-99` (`TASK_YAML_NAMES`) and `:107-118`
- Test (Engine tier): `tests/test_engine.py` imports at lines 17-20, fake registries at `:1160-1174` and `:1628-1642`, one new test appended at the end of the file

**Interfaces:**
- Consumes: `reducers.implement_blocked_gate(result: object) -> dict[str, str] | None` from Task 1. From existing code: `dispatch.AgentRunner` (dataclass fields `workflow, store, launcher, run_id, story_id, card_id, adapters, harness_map`), `harness.base.Outcome(argv, exit_code, timed_out, duration, stdout_path)`, `models.HarnessAssignment(harness, model)`, `models.Dispatch.role` / `.result_path`, `engine.SubtaskSummary.status/failed_phase/detail`.
- Produces: the registry name `"implement_blocked_gate"` resolving to `reducers.implement_blocked_gate`; the `implement` phase with `gates == ["implement_blocked_gate"]` and `retry is None`.

- [ ] **Step 1: Write the failing document-tier tests in `tests/workflow/test_builtin_task.py`**

Replace (in `test_implement_is_handed_the_plan_hash_last_after_the_documents`, line 225):

```python
        "plan_hash",
    ]
    assert phase.gates == []
```

with:

```python
        "plan_hash",
    ]
    # Decision O7: a coder that reports `blocked: true` stops the subtask here.
    assert phase.gates == ["implement_blocked_gate"]
    # Not retryable on purpose: re-dispatching the same brief would repeat the
    # same answer, so the budget stays at one attempt.
    assert phase.retry is None
```

Replace (in `test_the_document_still_names_exactly_the_gates_this_suite_covers`):

```python
        ("validate_plan", "critic_blockers_gate"),
        ("review", "review_gate"),
```

with:

```python
        ("validate_plan", "critic_blockers_gate"),
        ("implement", "implement_blocked_gate"),
        ("review", "review_gate"),
```

Append at the very end of the file (after `test_verification_passed_gate_blocks_a_red_suite_on_the_verify_phase`):

```python


def test_implement_blocked_gate_reads_a_real_dumped_blocked_result_and_blocks() -> None:
    """Binding alone would not catch a gate that reads the wrong key: this feeds
    it the snake_case dump of a real `ImplementResult`, as `dispatch.py` does."""
    gate = load_builtin("task").function("implement_blocked_gate")
    blocked = ImplementResult(
        blocked=True,
        blocked_reason="the baseline suite was already red: 3 failed",
        resumed=False,
        plan_hash=PLAN_HASH,
        report=REAL_SUMMARY,
    ).model_dump(mode="json")
    values = _values_for("implement", blocked)
    verdict = gate(
        **engine.bind_arguments(
            gate, values, phase="implement", function="implement_blocked_gate"
        )
    )
    assert verdict == {
        "blocked": "implement",
        "detail": "the baseline suite was already red: 3 failed",
    }
```

- [ ] **Step 2: Write the failing registry-sync tests in `tests/workflow/test_registry.py`**

Replace (inside `TASK_YAML_NAMES`):

```python
    "exploration_output_gate",
    "plan_check.find_validated_plan",
```

with:

```python
    "exploration_output_gate",
    "implement_blocked_gate",
    "plan_check.find_validated_plan",
```

Replace (end of `test_default_registry_resolves_the_ported_reducers_to_the_real_callables`):

```python
    assert registry.resolve("critic_blockers_gate") is reducers.critic_blockers_gate
```

with:

```python
    assert registry.resolve("critic_blockers_gate") is reducers.critic_blockers_gate
    assert registry.resolve("implement_blocked_gate") is reducers.implement_blocked_gate
```

- [ ] **Step 3: Write the failing Engine-tier test in `tests/test_engine.py`**

Replace the imports at lines 17-20:

```python
from agent_manager import engine, models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.workflow.loader import AgentPhase, load_builtin, load_workflow
from agent_manager.workflow.registry import BUILTIN_FUNCTION_NAMES, FunctionRegistry
```

with:

```python
from agent_manager import dispatch, engine, models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.harness.base import Outcome
from agent_manager.steps import reducers
from agent_manager.workflow.loader import AgentPhase, load_builtin, load_workflow
from agent_manager.workflow.registry import BUILTIN_FUNCTION_NAMES, FunctionRegistry
```

Append at the very end of the file (after `test_extra_context_may_not_redefine_the_base_branch_alias`):

```python


# ── decision O7: a blocked coder stops the subtask ───────────────────────────
# Engine tier per design §14: the real `task.yaml`, the real gate, the real
# `dispatch.AgentRunner` for the two phases under test, and a fake adapter plus
# a fake launcher that writes a canned result file. No process is started. The
# launcher learns the result path only from the argv the adapter built from the
# dispatch, exactly as a harness would.


class _FakeAdapter:
    """A `HarnessAdapter` by shape, whose argv names a program nothing runs."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return ["fake-harness", "--role", d.role, "--result", str(d.result_path)]

    def parse_usage(self, stdout: str) -> None:
        return None


class _CannedLauncher:
    """A `LauncherFn` double: writes `results[role]` as the result file, or
    nothing for a role it has no canned answer for, and records every role it
    was asked to run."""

    def __init__(self, results: dict[str, str]) -> None:
        self.results = results
        self.roles: list[str] = []

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        role = argv[argv.index("--role") + 1]
        self.roles.append(role)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        canned = self.results.get(role)
        if canned is not None:
            Path(argv[argv.index("--result") + 1]).write_text(canned, encoding="utf-8")
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.5,
            stdout_path=stdout_path,
        )


BLOCKED_REASON = "the baseline suite was already red: 3 failed before any change"
BLOCKED_IMPLEMENT = json.dumps(
    {
        "blocked": True,
        "blocked_reason": BLOCKED_REASON,
        "resumed": False,
        "plan_hash": "a1b2c3d4",
        "report": "stopped before writing any code",
    }
)


def test_a_blocked_coder_escalates_the_subtask_at_implement_and_review_never_runs(store):
    calls: list[str] = []
    functions = _builtin_functions(calls, validated=True)
    # The one real gate under test; every other agent gate stays the fake that
    # raises, so reaching `review` could not pass silently either.
    functions["implement_blocked_gate"] = reducers.implement_blocked_gate
    workflow = load_builtin("task", _registry(functions))

    launcher = _CannedLauncher({"coder": BLOCKED_IMPLEMENT})
    adapter = _FakeAdapter()
    subtask = _subtask()
    dispatching = dispatch.AgentRunner(
        workflow=workflow,
        store=store,
        launcher=launcher,
        run_id=RUN_ID,
        story_id=STORY_ID,
        card_id=subtask.card_id,
        adapters={adapter.name: adapter},
        harness_map={
            "coder": models.HarnessAssignment(harness=adapter.name, model="fake-model"),
            "reviewer": models.HarnessAssignment(harness=adapter.name, model="fake-model"),
        },
    )

    def agent_runner(phase, context, rendered):
        if phase.name in ("implement", "review"):
            return dispatching(phase, context, rendered)
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=subtask,
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=agent_runner,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "implement"
    assert BLOCKED_REASON in summary.detail
    assert "implement_blocked_gate" in summary.detail
    assert "blocked=implement" in summary.detail
    # Exactly one coder dispatch (no retry block) and no reviewer dispatch.
    assert launcher.roles == ["coder"]
    attempts = store.connection.execute(
        "SELECT phase, status FROM attempts ORDER BY phase, n"
    ).fetchall()
    assert [tuple(row) for row in attempts] == [("implement", "gate_failed")]
    assert "verify.run_suite" not in calls
    assert "rollup.set_status:done" not in calls
```

- [ ] **Step 4: Run the new and changed tests to verify they fail**

Run: `uv run pytest tests/workflow/test_builtin_task.py tests/workflow/test_registry.py "tests/test_engine.py::test_a_blocked_coder_escalates_the_subtask_at_implement_and_review_never_runs" -v`
Expected: FAIL. Specifically:
- `test_implement_is_handed_the_plan_hash_last_after_the_documents` fails with `assert [] == ['implement_blocked_gate']`.
- `test_the_document_still_names_exactly_the_gates_this_suite_covers` fails on the list comparison.
- `test_implement_blocked_gate_reads_a_real_dumped_blocked_result_and_blocks` fails because `load_builtin("task").function("implement_blocked_gate")` raises (the document does not name it yet).
- `test_default_registry_holds_exactly_the_names_task_yaml_uses` and `test_default_registry_resolves_the_ported_reducers_to_the_real_callables` fail (name not registered).
- The Engine-tier test fails on `assert summary.failed_phase == "implement"` (actual `"review"`: the blocked result passes ungated, `review` is dispatched, the launcher has no canned reviewer result, so review is a `harness_error`).

- [ ] **Step 5: Register the gate in `src/agent_manager/workflow/registry.py`**

Replace:

```python
    "exploration_output_gate",
    "plan_check.find_validated_plan",
```

with:

```python
    "exploration_output_gate",
    "implement_blocked_gate",
    "plan_check.find_validated_plan",
```

Replace:

```python
    The six reducers and the five implemented steps are the real, imported
```

with:

```python
    The seven reducers and the five implemented steps are the real, imported
```

Replace:

```python
    registry.register("critic_blockers_gate", reducers.critic_blockers_gate)
```

with:

```python
    registry.register("critic_blockers_gate", reducers.critic_blockers_gate)
    # Decision O7: a coder that reports blocked stops the subtask at implement.
    registry.register("implement_blocked_gate", reducers.implement_blocked_gate)
```

- [ ] **Step 6: Gate the `implement` phase in `src/agent_manager/workflow/builtin/task.yaml`**

Replace:

```yaml
  - name: implement
    kind: agent
    role: coder
    inputs: [plan_path, spec_path, branch, base_branch, plan_hash]
    result: ImplementResult
```

with:

```yaml
  - name: implement
    kind: agent
    role: coder
    inputs: [plan_path, spec_path, branch, base_branch, plan_hash]
    result: ImplementResult
    gates: [implement_blocked_gate]
```

Do not add a `retry:` key.

- [ ] **Step 7: Keep the Engine tier's fake registries in sync with the document**

`load_builtin("task", _registry(...))` now needs the name `implement_blocked_gate`, and `tests/test_engine.py:1175` asserts the fake table equals `BUILTIN_FUNCTION_NAMES`. In `tests/test_engine.py`, in `test_the_builtin_task_document_walks_against_a_fake_registry`, replace:

```python
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "verification_gate": agent_only_gate,
    }
    assert sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)
```

with:

```python
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "implement_blocked_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "verification_gate": agent_only_gate,
    }
    assert sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)
```

And in `_builtin_functions`, replace:

```python
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "verification_gate": agent_only_gate,
    }


def _walk_builtin(
```

with:

```python
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "implement_blocked_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "verification_gate": agent_only_gate,
    }


def _walk_builtin(
```

- [ ] **Step 8: Run the targeted tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py tests/workflow/test_builtin_task.py tests/workflow/test_registry.py tests/test_engine.py tests/test_dispatch.py -v`
Expected: PASS, including the parametrised `test_every_gate_binds_every_parameter_against_real_results[implement-implement_blocked_gate]` and `test_every_gate_passes_on_a_healthy_run[implement-implement_blocked_gate]` cases, which now exist because `GATED_PHASES` is derived from the document.

- [ ] **Step 9: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, `tests/e2e` included. The production-wiring fake (`tests/e2e/fake_claude.py:283-284`) reports `blocked=False, blocked_reason=None`, so the gate passes there and healthy runs reach `review` unchanged. If any e2e test fails, do not edit `tests/e2e/fake_claude.py` to compute or learn anything beyond the brief; diagnose with superpowers:systematic-debugging instead.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/workflow/registry.py src/agent_manager/workflow/builtin/task.yaml tests/workflow/test_builtin_task.py tests/workflow/test_registry.py tests/test_engine.py
git commit -m "feat(workflow): gate implement on implement_blocked_gate so a blocked coder escalates"
```
