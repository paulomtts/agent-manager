# Select the engine with `--engine` through run, milestone and Integrate (card 7fdec762)

Narrows plan Task 4.4 of `docs/superpowers/plans/2026-09-25-pygents-engine.md`. Source of truth: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (extending the agent-manager, orchestration and integrate designs). Story a6c7bff3 "Checkpoints and resume"; blocked_by 5698e4f6 (done), whose `runtime.engine.run_subtask(..., resume_from=...)` this card calls as-is.

Note: the exploration findings handed to this stage were truncated at 8000 characters, mid-sentence in the test-placement rule. The rule below was re-read from the design spec §14 and `pyproject.toml` directly, not reconstructed from the cut-off text.

## Scope

- `cli.py`: add `Engine = Literal["yaml", "pygents"]`. `drive_subtask(..., engine: Engine = "yaml")`, `run_card(..., engine: Engine = "yaml")`, and the Typer `run` command gets `--engine` (default `yaml`), forwarded to `run_card` or `orchestrate.run_milestone`.
- `orchestrate.py`: `run_milestone(..., engine: Engine = "yaml")`. It passes `engine` to every driver call through `run_story_lane`, and to `integration.integrate_milestone`. The `Driver` Protocol gains the same `engine` keyword, because it mirrors `drive_subtask`'s signature.
- `integration.py`: `integrate_milestone(..., engine="yaml")` passes it to `_resolve_conflict(..., engine)`.
- Dispatch inside `drive_subtask`:
  - `engine == "yaml"`: exactly today's code. It calls `agent_manager.engine.run_subtask` with the YAML `Workflow` from `load_builtin("task")`.
  - `engine == "pygents"`: calls `agent_manager.runtime.engine.run_subtask(workflow.task.TASK, store, ...)` with the same keyword arguments the yaml call passes (`story_id`, `subtask`, `repo_dir`, `commands`, `card`, `parent_story`, `extra_context=gate_context(...)`, `agent_runner`, `should_stop`), minus `start_phase`. It does not pass `resume_from`.
- The same split applies in `_resolve_conflict`. On pygents it calls `runtime.engine.run_subtask(workflow.integrate.INTEGRATE, ...)` with the same `extra_context` (`merge_tip`, `conflict_files`, gate context). It does not pass `card` or `parent_story`, matching today's call.
- `RunnerFactory` still gets `workflow=` on both engines. That value is always the YAML `Workflow` that `load_builtin` returns (`task`, or `integrate` for the resolver), never `TASK` or `INTEGRATE`. `dispatch.AgentRunner` reads it only for by-name gates, and the phase model never produces those. The `RunnerFactory` signature does not change.
- The fail-fast `load_builtin` preflights in `run_card` and `run_milestone` stay as they are on both engines.
- `SubtaskDrive`'s warnings merge (summary warnings plus the runner's out-of-band warnings) is the same on both engines.

## Invariants

- RULE 1: `cli.py`, `orchestrate.py` and `integration.py` must not import `pygents`. They reach it only through `agent_manager.runtime.engine`. Where that import sits (module top or lazy inside the pygents branch) is the plan's choice. `workflow/phases.py`, `task.py` and `integrate.py` stay pygents-free.
- Name collision: `cli.py` and `integration.py` already use the old engine module under the name `engine` (`engine.run_subtask`, `engine.SubtaskSummary`, `engine.AgentPhaseRunner`). A parameter called `engine` would shadow that module inside `drive_subtask` and `_resolve_conflict`. Inside those bodies the module must be reached under a name the parameter cannot shadow. The public keyword stays `engine`, as the plan names it.
- RULE 5 / G10: the JSON `data` of `run --card` and `run --milestone`, plus the journal lines, phase/attempt rows, `SubtaskSummary` and escalation payloads, must be the same on both engines, apart from `run_id` and timestamps.
- The parallel milestone works on pygents unchanged. Each lane thread's call to `runtime.engine.run_subtask` does its own `asyncio.run`. This card adds no event loop, no lane-as-task and no supervisor.
- RULE 3: this card adds no code in `runtime/`.

## Observable behavior

- `am run --card X` and `am run --milestone M`, with no `--engine` or with `--engine yaml`, behave byte-for-byte as they do today.
- With `--engine pygents`, the same commands give the same envelope `{"ok": true, "data": ...}` (and the same `--pretty` rendering). The only differences are in `run_id` and timestamps.
- `--dry-run` accepts `--engine` and ignores it. It drives nothing.
- A relaunch with `--engine pygents` re-drives a non-done subtask from its first phase, exactly as yaml does. Continuing from a checkpoint is card 02890d5d's job, not this card's.

## Error paths

- `--engine bogus` (any value outside `yaml`/`pygents`) is a usage error: exit 2, nothing on stdout, and no run directory is created. Typer's own choice validation for the `Literal` annotation should produce this. If Typer 0.27.2 does not validate a `Literal` cleanly, fall back to the existing `typer.BadParameter` pattern (cli.py ~1001-1037), which also exits 2. Do not add a new validation helper beyond that.
- Neither engine adds new exceptions. An escalation is still `summary.status == "escalated"`, and a stop is still `"stopped"`. `CheckpointMismatch` cannot be raised here, because `resume_from` is never passed.

## Out of scope

`am resume --engine`, checkpoint continuation on relaunch, orphan-attempt marking and digest-mismatch exit 3 (all card 02890d5d). The second `engine.run_subtask` call inside `resume_run` (cli.py ~1382) is not touched. Also out: the supervisor tree, exactly-once phases, prompt benchmarking, upstream pygents fixes, and any change to `runtime/engine.py`'s resume logic.

## Tests

Test-placement rule: design spec §14 plus `pyproject.toml`. Pure logic and CLI plumbing go in unit tests under `tests/`, mirroring `src/`, with the launcher/runner injected and no harness run. Fake-`claude` end-to-end tests live in `tests/e2e/`, unmarked, in the default suite. Only real-harness tests carry the `e2e` marker, and those are excluded by `addopts`. This card adds nothing to the real-harness tier.

1. Unit tier, `tests/test_cli.py`: `run --card X --engine bogus` exits 2, stdout is empty, and no run directory exists.
2. Unit tier, `tests/test_cli.py`: `drive_subtask(engine="pygents")`, with `runtime.engine.run_subtask` stubbed, gets `workflow.task.TASK` as its first argument. Its keywords are the yaml call's keywords minus `start_phase`, with no `resume_from`. The factory gets the YAML `Workflow` from `load_builtin`. `drive_subtask()` with the default engine still calls the old `engine.run_subtask`.
3. Unit tier, `tests/test_orchestrate.py`: `run_milestone(engine="pygents")`, with a fake `driver`, passes `engine="pygents"` to every driver call and to `integrate_milestone`. With no argument, `"yaml"` is passed.
4. Unit tier, `tests/test_integration.py`: `_resolve_conflict(engine="pygents")`, with `runtime.engine.run_subtask` stubbed, gets `workflow.integrate.INTEGRATE` and the same `extra_context`. The runner factory gets the YAML `integrate` `Workflow`. The plan lists no parametrized e2e case for the resolver path, so this unit test is what covers it. Parametrizing `tests/e2e/test_integrate.py` is not required by this card.
5. Fake-claude e2e tier, `tests/e2e/test_production_wiring.py`: parametrize over `engine ∈ {yaml, pygents}` by passing `--engine`. The existing assertions hold on both engines. The JSON `data` is the same on both, ignoring `run_id` and timestamps.
6. Fake-claude e2e tier, `tests/e2e/test_milestone_run.py`: the same parametrization and the same identical-`data` assertion, including the review-failure-then-relaunch scenario. On pygents that relaunch re-drives from the start.
7. Fake-claude e2e tier, `tests/e2e/test_parallel_milestone.py`: the same parametrization. Lanes overlap, a single lane runs sequentially, the journal is contiguous and rebuilds the projection, the escalation stops the other lane, and the relaunch finishes. All of this must hold on pygents with one `asyncio.run` per lane thread.
8. Each module keeps its `test_this_module_runs_in_the_default_suite_unmarked` guard. Parametrization must not add the `e2e` marker.

Fake `claude` stays brief-driven: no engine-awareness is added to `tests/e2e/fake_claude.py`.

Verify: `uv run pytest` is green, with the whole default suite passing on both engines, parallel milestone included. Commit: `feat(cli): --engine selects the YAML or the pygents engine`.

---

# `--engine` Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `am run --card` / `am run --milestone` (and the Integrate resolver they reach) walk each subtask on either the YAML engine or the pygents engine, selected by `--engine`, with identical observable output.

**Architecture:** One `engine: Engine = "yaml"` keyword is threaded from the Typer `run` command through `run_card` / `orchestrate.run_milestone` → `run_story_lane` → the `Driver` (`cli.drive_subtask`) and → `integration.integrate_milestone` → `_resolve_conflict`. Only the two leaf functions branch: `yaml` keeps calling `agent_manager.engine.run_subtask` with the loaded YAML `Workflow`; `pygents` calls `agent_manager.runtime.engine.run_subtask` with `workflow.task.TASK` / `workflow.integrate.INTEGRATE` and the identical keyword arguments. The old module is reached as `yaml_engine` and the new one as `runtime_engine` inside those bodies, so the `engine` parameter never shadows a module.

**Tech Stack:** Python 3.12, Typer 0.27.2 (vendored click), Pydantic, pytest 9 with `--import-mode=importlib`, pygents (only under `src/agent_manager/runtime/`).

**Spec:** `docs/superpowers/specs/task-select-the-engine-with-7fdec762-design.md` (prepended above).

## Global Constraints

- Only `src/agent_manager/runtime/` may import `pygents`. `cli.py`, `orchestrate.py`, `integration.py` import `agent_manager.runtime.engine`, never `pygents`.
- No code changes under `src/agent_manager/runtime/` (RULE 3). No change to `runtime.engine.run_subtask`'s resume logic; `resume_from` is never passed.
- `resume_run` (cli.py ~1303-1425) and its `engine.run_subtask(... start_phase=...)` call are not touched.
- `Engine = Literal["yaml", "pygents"]`, default `"yaml"` everywhere.
- Default behaviour (no `--engine` / `--engine yaml`) is byte-for-byte today's.
- `--engine bogus` → exit 2, empty stdout, no run directory. Typer 0.27.2 has no `Literal` support (`typer/main.py:1625` raises `RuntimeError("Type not yet supported")`; `typer/` has no `Literal` handling at all), so the spec's fallback applies: the option is typed `str` and refused in `_check_run_targets` with `typer.BadParameter` (param_hint `'--engine'`).
- `RunnerFactory` always receives the YAML `Workflow` from `load_builtin` (never `TASK`/`INTEGRATE`); its signature is unchanged.
- CLI envelope stays `{"ok": true, "data": ...}`; `--pretty` unchanged.
- Fake `claude` (`tests/e2e/fake_claude.py`) gets no engine-awareness (RULE 4).
- No e2e module may gain the `e2e` marker; engine parametrization is done with fixture `params`, never `pytest.mark.parametrize`, so no mark lands on those modules.
- Verification: `uv run pytest`.

## Review Focus

1. `--engine` silently ignored somewhere in the chain (both parametrized runs actually walk yaml): every parametrized e2e test would pass vacuously. Expected: a pygents run leaves `checkpoints` rows for its run id and a yaml run leaves none. Pinned in Task 4 (`checkpoint_rows` non-vacuity assertions) plus the sabotage check in Task 4 Step 3.
2. A near-miss engine value (`PYGENTS`, `Pygents`, empty string) on the CLI: expected a usage error (exit 2, empty stdout, no run dir), never a silent yaml run and never case-folding. Pinned in Task 3 (`test_a_bad_engine_is_a_usage_error_that_starts_nothing`).
3. A programmatic caller passing an unknown engine to `drive_subtask` / `_resolve_conflict`: expected a `ValueError` before any runner is built or row is recorded, never a silent fall-through to yaml. Pinned in Task 1 and Task 2.
4. `--dry-run --engine pygents` on a milestone: expected the same preview and nothing driven or written, and `dry_run_milestone` never sees an `engine` key. Pinned in Task 3 (`test_a_dry_run_accepts_and_ignores_the_engine`).
5. A real conflicting tip resolved on pygents through `integrate_milestone` (not a stub): expected the same `IntegrateSuccess`, the same `resolve`/`verify` rows, and the same factory call as on yaml. Pinned in Task 2 (`test_a_conflict_resolves_the_same_way_on_the_pygents_engine`).

---

## File Structure

- Modify `src/agent_manager/cli.py`: `Engine`, `ENGINES`, the `yaml_engine` / `runtime_engine` / `task_workflow` imports, `drive_subtask` dispatch, `run_card` forwarding, `_check_run_targets` engine refusal, `run --engine`.
- Modify `src/agent_manager/integration.py`: `yaml_engine` / `runtime_engine` / `integrate_workflow` imports, `_resolve_conflict` dispatch, `integrate_milestone` forwarding.
- Modify `src/agent_manager/orchestrate.py`: `Driver` Protocol, `run_story_lane`, `run_milestone` forwarding.
- Modify `tests/test_cli.py`: drive_subtask/run_card engine tests (Task 1), CLI option tests and the whole-kwargs update (Task 3).
- Modify `tests/test_integration.py`: `_resolve_conflict` routing tests, real pygents conflict test, `_integrate(engine=...)` (Task 2).
- Modify `tests/test_orchestrate.py`: `FakeDriver`, `GatedDriver`, `IntegrateRecorder` accept `engine`; new forwarding test; whole-dict update; RULE-1 import guard (Task 2).
- Modify `tests/e2e/conftest.py`: default `engine` fixture, `project`/`completed_run`/`run_milestone_cli` use it, `engine_parity`, `board_card_labels`, `checkpoint_rows` fixtures (Task 4).
- Modify `tests/e2e/test_production_wiring.py`, `tests/e2e/test_milestone_run.py`, `tests/e2e/test_parallel_milestone.py`: module-scoped parametrized `engine` override, parity and non-vacuity assertions (Task 4).

---

### Task 1: `drive_subtask` and `run_card` select the walk by engine

**Files:**
- Modify: `src/agent_manager/cli.py:20-43` (imports), `:598-599` (after `WORKFLOW_NAME`), `:679-728` (`drive_subtask`), `:731-806` (`run_card`)
- Test: `tests/test_cli.py` (imports at `:28-44`; new tests inserted after `test_drive_subtask_hands_should_stop_to_the_engine`, which ends at `:1643`, before `runner = CliRunner()` at `:1646`)

**Interfaces:**
- Consumes: `agent_manager.runtime.engine.run_subtask(workflow: phases.Workflow, store, *, story_id, subtask, repo_dir, commands=(), card=None, parent_story=None, extra_context=None, agent_runner=None, clock=..., should_stop=None, resume_from=None) -> engine.SubtaskSummary` (already on the branch, `src/agent_manager/runtime/engine.py:39-54`); `agent_manager.workflow.task.TASK`.
- Produces: `cli.Engine = Literal["yaml", "pygents"]`; `cli.ENGINES: tuple[str, ...] == ("yaml", "pygents")`; `cli.drive_subtask(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, should_stop=None, engine: Engine = "yaml") -> SubtaskDrive` (raises `ValueError("unknown engine ...")` for any other value); `cli.run_card(card_id, *, repo_dir, branch_prefix, base_branch="master", allow_no_verification=False, commands=(), runner_factory=None, clock=_utcnow, engine: Engine = "yaml") -> dict`; module aliases `cli.yaml_engine` (is `agent_manager.engine`), `cli.runtime_engine` (is `agent_manager.runtime.engine`), `cli.task_workflow` (is `agent_manager.workflow.task`).

- [ ] **Step 1: Write the failing tests**

Add these imports to `tests/test_cli.py` directly below the existing `from agent_manager.errors import AgentPhaseFailed, EngineError` line (`:41`):

```python
from agent_manager import engine as yaml_engine
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow import loader
from agent_manager.workflow import task as task_workflow
```

Insert after `test_drive_subtask_hands_should_stop_to_the_engine` (after `:1643`) and before `runner = CliRunner()`:

```python
# ── engine selection (card 7fdec762) ─────────────────────────────────────────

DRIVE_CARD = models.Card(
    id="7fdec762-0000-4000-8000-000000000001",
    title="Select the engine with --engine",
    status="todo",
    parent_id="a6c7bff3-0000-4000-8000-000000000002",
)
DRIVE_PARENT = models.Card(
    id="a6c7bff3-0000-4000-8000-000000000002",
    title="Checkpoints and resume",
    status="in_progress",
)
DRIVE_RUN_ID = "20260926T000000Z-7fdec762"
DRIVE_REPO = Path("/repo")


def _drive_row() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=DRIVE_CARD.id,
        branch="m6/task-select-the-engine-7fdec762",
        base_branch="main",
        status="started",
        worktree_path=Path("/repo/.claude/worktrees/m6/task-select-the-engine-7fdec762"),
    )


def _record_walks(monkeypatch) -> dict[str, list[tuple[Any, Any, dict[str, Any]]]]:
    """Stub both engines' `run_subtask`; every call lands under its engine's name.

    Patched on the modules themselves, so the stub is what `drive_subtask`
    reaches whatever alias it holds the module under.
    """
    walks: dict[str, list[tuple[Any, Any, dict[str, Any]]]] = {"yaml": [], "pygents": []}

    def recorder(name: str):
        def run_subtask(workflow, store, **kwargs):
            walks[name].append((workflow, store, kwargs))
            return yaml_engine.SubtaskSummary(status="done")

        return run_subtask

    monkeypatch.setattr(yaml_engine, "run_subtask", recorder("yaml"))
    monkeypatch.setattr(runtime_engine, "run_subtask", recorder("pygents"))
    return walks


def _recording_factory(seen: list[dict[str, Any]]):
    """A runner factory that records its kwargs and hands back one opaque runner."""
    runner = object()

    def factory(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return runner

    return factory, runner


@pytest.mark.parametrize("engine", ["yaml", "pygents"])
def test_drive_subtask_walks_the_engine_it_is_given_with_the_same_arguments(
    monkeypatch, engine
):
    """Spec test 2: the walk differs only in which `run_subtask` gets which
    workflow. The keywords are compared whole, so a `start_phase` or a
    `resume_from` sneaking into either call fails here."""
    walks = _record_walks(monkeypatch)
    seen: list[dict[str, Any]] = []
    factory, runner = _recording_factory(seen)
    store = object()
    subtask = _drive_row()

    def stop() -> bool:
        return False

    drive = cli.drive_subtask(
        store=store,
        run_id=DRIVE_RUN_ID,
        card=DRIVE_CARD,
        parent=DRIVE_PARENT,
        subtask=subtask,
        repo_dir=DRIVE_REPO,
        commands=["uv run pytest"],
        runner_factory=factory,
        should_stop=stop,
        engine=engine,
    )

    other = "yaml" if engine == "pygents" else "pygents"
    assert walks[other] == []
    ((workflow, passed_store, kwargs),) = walks[engine]
    assert passed_store is store
    if engine == "pygents":
        assert workflow is task_workflow.TASK
    else:
        assert isinstance(workflow, loader.Workflow)
        assert workflow.name == cli.WORKFLOW_NAME
    assert kwargs == {
        "story_id": DRIVE_PARENT.id,
        "subtask": subtask,
        "repo_dir": DRIVE_REPO,
        "commands": ["uv run pytest"],
        "card": DRIVE_CARD,
        "parent_story": DRIVE_PARENT,
        "extra_context": cli.gate_context(["uv run pytest"], False),
        "agent_runner": runner,
        "should_stop": stop,
    }
    assert drive.summary.status == "done"
    assert drive.warnings == []
    # The factory gets the loaded YAML document on BOTH engines, never TASK.
    (factory_call,) = seen
    assert isinstance(factory_call["workflow"], loader.Workflow)
    assert factory_call["workflow"].name == cli.WORKFLOW_NAME
    assert {key: value for key, value in factory_call.items() if key != "workflow"} == {
        "store": store,
        "run_id": DRIVE_RUN_ID,
        "story_id": DRIVE_PARENT.id,
        "card_id": DRIVE_CARD.id,
    }


def test_drive_subtask_defaults_to_the_yaml_walk(monkeypatch):
    walks = _record_walks(monkeypatch)
    factory, _ = _recording_factory([])

    cli.drive_subtask(
        store=object(),
        run_id=DRIVE_RUN_ID,
        card=DRIVE_CARD,
        parent=DRIVE_PARENT,
        subtask=_drive_row(),
        repo_dir=DRIVE_REPO,
        runner_factory=factory,
    )

    assert len(walks["yaml"]) == 1
    assert walks["pygents"] == []


@pytest.mark.parametrize("engine", ["PYGENTS", "Yaml", "bogus", ""])
def test_drive_subtask_refuses_an_unknown_engine_before_building_a_runner(
    monkeypatch, engine
):
    """Review Focus 3: a typo never falls through to the yaml walk."""
    walks = _record_walks(monkeypatch)
    seen: list[dict[str, Any]] = []
    factory, _ = _recording_factory(seen)

    with pytest.raises(ValueError, match="unknown engine"):
        cli.drive_subtask(
            store=object(),
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=_drive_row(),
            repo_dir=DRIVE_REPO,
            runner_factory=factory,
            engine=engine,
        )

    assert seen == []
    assert walks == {"yaml": [], "pygents": []}


def test_engines_lists_exactly_the_literal_values():
    assert cli.ENGINES == ("yaml", "pygents")


@requires_git
@requires_brd
@pytest.mark.parametrize("given, passed", [({}, "yaml"), ({"engine": "pygents"}, "pygents")])
def test_run_card_hands_its_engine_to_drive_subtask(project, cards, monkeypatch, given, passed):
    seen: list[str] = []

    def fake_drive_subtask(**kwargs: Any) -> cli.SubtaskDrive:
        seen.append(kwargs["engine"])
        return cli.SubtaskDrive(summary=yaml_engine.SubtaskSummary(status="done"), warnings=[])

    monkeypatch.setattr(cli, "drive_subtask", fake_drive_subtask)

    payload = cli.run_card(
        cards["subtask"], repo_dir=project, base_branch="main", branch_prefix="m1", **given
    )

    assert seen == [passed]
    assert payload["status"] == "done"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "drive_subtask_walks or defaults_to_the_yaml or refuses_an_unknown_engine or engines_lists or run_card_hands_its_engine" -v`
Expected: FAIL. `drive_subtask()` / `run_card()` raise `TypeError: ... got an unexpected keyword argument 'engine'`, `test_engines_lists_exactly_the_literal_values` fails with `AttributeError: module 'agent_manager.cli' has no attribute 'ENGINES'`. `test_drive_subtask_defaults_to_the_yaml_walk` may already pass (it pins today's behaviour).

- [ ] **Step 3: Implement**

In `src/agent_manager/cli.py`, change the typing import at `:25`:

```python
from typing import Any, Literal, Protocol, get_args
```

Directly below the `from agent_manager import (... )` block (after `:38`), and keeping that block unchanged (other functions and tests still use `cli.engine`), add:

```python
from agent_manager import engine as yaml_engine
```

and below `from agent_manager.harness.launcher import run_direct` (`:40`) add:

```python
from agent_manager.runtime import engine as runtime_engine
```

and below `from agent_manager.store import Store` (`:41`) add:

```python
from agent_manager.workflow import task as task_workflow
```

After the `WORKFLOW_NAME` docstring (`:599`) add:

```python
Engine = Literal["yaml", "pygents"]
"""Which engine walks a subtask: the YAML walk in `agent_manager.engine`, or the
pygents one in `agent_manager.runtime.engine`. Only `runtime/` imports pygents;
this module reaches it through `runtime_engine` alone (pygents-engine RULE 1)."""

ENGINES: tuple[str, ...] = get_args(Engine)
"""`Engine`'s values, in the order a refusal lists them."""
```

Replace `drive_subtask` (`:679-728`) with:

```python
def drive_subtask(
    *,
    store: Store,
    run_id: str,
    card: models.Card,
    parent: models.Card,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
    should_stop: Callable[[], bool] | None = None,
    engine: Engine = "yaml",
) -> SubtaskDrive:
    """Walk one subtask through `builtin/task.yaml` under a store the caller owns.

    Addendum O4's shared driver. `run_card` calls it once, and a milestone runner
    calls it once per subtask against one store and one run id. The caller owns
    everything around the walk: the board reads, the run id, opening and
    closing the store, and the run/story/subtask rows. This function catches
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    `should_stop` goes straight to the engine; a stop is
    `summary.status == "stopped"`.

    `engine` picks the walk (card 7fdec762). `yaml` is `agent_manager.engine`
    over the loaded document, exactly as before. `pygents` is
    `agent_manager.runtime.engine` over `workflow.task.TASK`, with the same
    keyword arguments and no `resume_from`. The runner factory gets the loaded
    YAML `Workflow` on both: `dispatch.AgentRunner` reads it only for by-name
    gates, which `TASK` never produces. The parameter shadows the module-level
    `engine` name here, so the walks are reached as `yaml_engine` and
    `runtime_engine`. Any other value is refused before a runner is built.
    """
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; expected one of {', '.join(ENGINES)}")
    workflow = load_builtin(WORKFLOW_NAME)
    factory = default_runner_factory if runner_factory is None else runner_factory
    runner = factory(
        workflow=workflow,
        store=store,
        run_id=run_id,
        story_id=parent.id,
        card_id=card.id,
    )
    walk: dict[str, Any] = {
        "story_id": parent.id,
        "subtask": subtask,
        "repo_dir": repo_dir,
        "commands": commands,
        "card": card,
        "parent_story": parent,
        "extra_context": gate_context(commands, allow_no_verification),
        "agent_runner": runner,
        "should_stop": should_stop,
    }
    if engine == "pygents":
        summary = runtime_engine.run_subtask(task_workflow.TASK, store, **walk)
    else:
        summary = yaml_engine.run_subtask(workflow, store, **walk)
    # `AgentRunner` collects gate warnings out of band (dispatch.py:375):
    # its signature returns a result, so a warning has nowhere else to go,
    # and dropping them is the §12 failure this whole list exists to prevent.
    warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
    return SubtaskDrive(summary=summary, warnings=warnings)
```

In `run_card`'s signature (`:731-741`), add the keyword after `clock`:

```python
    clock: Callable[[], datetime] = _utcnow,
    engine: Engine = "yaml",
) -> dict[str, Any]:
```

and in its `drive_subtask(...)` call (`:796-806`) add `engine=engine,` after `runner_factory=runner_factory,`:

```python
        drive = drive_subtask(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            engine=engine,
        )
```

Append one sentence to `run_card`'s docstring: `` `engine` goes to `drive_subtask` unchanged; the preflight loads the YAML document on both engines. ``

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, including the pre-existing `test_drive_subtask_*` tests and the test that patches `cli.engine.run_subtask` (`:2018`), which still reaches the yaml walk because `yaml_engine` is the same module object.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): drive_subtask and run_card select the walk by engine"
```

---

### Task 2: Thread `engine` through `run_milestone`, `integrate_milestone` and `_resolve_conflict`

**Files:**
- Modify: `src/agent_manager/integration.py:25-34` (imports), `:124-173` (`_resolve_conflict`), `:176-177` (`_resolver_detail` annotation), `:187-251` (`integrate_milestone`)
- Modify: `src/agent_manager/orchestrate.py:202-222` (`Driver`), `:392-444` (`run_story_lane`), `:500-620` (`run_milestone`)
- Test: `tests/test_integration.py` (imports `:32-42`, `_integrate` helper `:347-366`, new tests after `test_a_conflict_dispatches_exactly_once_for_the_conflicting_tip` which ends at `:593`)
- Test: `tests/test_orchestrate.py` (`FakeDriver` `:422-477`, `IntegrateRecorder` `:496-548`, `GatedDriver.__call__` `:740-753`, whole-dict assertion `:838-848`, new tests after `test_no_runner_factory_gives_integrate_cli_default_runner_factory_at_call_time` which ends at `:985`)

**Interfaces:**
- Consumes: `cli.Engine`, `cli.ENGINES` (Task 1); `cli.drive_subtask(..., engine=...)` (Task 1); `agent_manager.workflow.integrate.INTEGRATE`; `runtime.engine.run_subtask` as in Task 1.
- Produces: `integration._resolve_conflict(*, story_id, tip, files, branch, base_branch, worktree, repo_dir, commands, allow_no_verification, store, run_id, runner_factory, engine: cli.Engine = "yaml") -> yaml_engine.SubtaskSummary` (the same class as `engine.SubtaskSummary`, annotated through the alias so the parameter cannot shadow it) (raises `ValueError("unknown engine ...")` before recording anything); `integration.integrate_milestone(stories, repo_dir, base_branch, branch_prefix, commands, allow_no_verification, store, run_id, runner_factory, engine: cli.Engine = "yaml")`; `orchestrate.Driver.__call__(..., should_stop=None, engine: cli.Engine = "yaml")`; `orchestrate.run_story_lane(..., stop, engine: cli.Engine = "yaml")`; `orchestrate.run_milestone(milestone, *, repo_dir, base_branch, branch_prefix, commands=(), allow_no_verification=False, runner_factory=None, driver=None, clock=_utcnow, max_concurrent=1, engine: cli.Engine = "yaml")`.

- [ ] **Step 1: Write the failing tests (integration)**

In `tests/test_integration.py`, change the `from agent_manager import cli, dag, dispatch, models` line (`:32`) to:

```python
from agent_manager import cli, dag, dispatch, integration, models
from agent_manager import engine as yaml_engine
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow import integrate as integrate_workflow
from agent_manager.workflow import loader
```

Replace the `_integrate` helper (`:347-366`) with one that forwards an engine:

```python
def _integrate(
    repo: Repo,
    store: Store,
    stories: list[StoryPlan],
    *,
    factory: FakeFactory,
    commands: list[str] | None = None,
    allow_no_verification: bool = False,
    engine: str = "yaml",
):
    return integrate_milestone(
        stories=stories,
        repo_dir=repo.root,
        base_branch=BASE,
        branch_prefix=PREFIX,
        commands=[PASS_CMD] if commands is None else commands,
        allow_no_verification=allow_no_verification,
        store=store,
        run_id=RUN_ID,
        runner_factory=factory,
        engine=engine,
    )
```

Insert after `test_a_conflict_dispatches_exactly_once_for_the_conflicting_tip` (after `:593`):

```python
def test_a_conflict_resolves_the_same_way_on_the_pygents_engine(
    repo: Repo, store: Store
) -> None:
    """Review Focus 5: the same scenario as the test above, walked by the
    pygents engine over `INTEGRATE` through the real `integrate_milestone`.
    Everything the yaml walk is asserted to leave behind is asserted here."""
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory, engine="pygents")

    assert isinstance(outcome, IntegrateSuccess), outcome
    assert outcome.merged == [STORY_A, STORY_B]
    assert outcome.resolved == [STORY_B]
    assert factory.calls == [
        {"workflow": "integrate", "run_id": RUN_ID, "story_id": "integrate", "card_id": STORY_B}
    ]
    assert factory.resolver.calls == [["shared.txt"]]
    assert _merge_head(repo.worktree) is None
    assert _git(repo.worktree, "status", "--porcelain") == ""
    assert _git(repo.worktree, "show", "HEAD:shared.txt") == RESOLVED
    story = _integrate_story(store)
    assert story.status == "done"
    [subtask] = story.subtasks
    assert (subtask.card_id, subtask.status) == (STORY_B, "done")
    assert [phase.name for phase in subtask.phases] == ["resolve", "verify"]
    rebuilt = store.rebuild_from_journal(RUN_ID)
    assert [p.name for p in rebuilt.stories[0].subtasks[0].phases] == ["resolve", "verify"]
    _assert_protected(repo, before, tips)


class _RecordingStore:
    """Only what `_resolve_conflict` touches on a store: `record_subtask`."""

    def __init__(self) -> None:
        self.subtasks: list[tuple[str, models.SubtaskRun]] = []

    def record_subtask(self, story_id: str, subtask: models.SubtaskRun) -> None:
        self.subtasks.append((story_id, subtask))


def _stub_both_walks(monkeypatch):
    walks: dict[str, list[tuple[Any, Any, dict[str, Any]]]] = {"yaml": [], "pygents": []}

    def recorder(name: str):
        def run_subtask(workflow, store, **kwargs):
            walks[name].append((workflow, store, kwargs))
            return yaml_engine.SubtaskSummary(status="done")

        return run_subtask

    monkeypatch.setattr(yaml_engine, "run_subtask", recorder("yaml"))
    monkeypatch.setattr(runtime_engine, "run_subtask", recorder("pygents"))
    return walks


def _resolve(store, factory, tmp_path: Path, engine: str):
    return integration._resolve_conflict(
        story_id=STORY_B,
        tip="m5/task-b",
        files=["shared.txt"],
        branch=INTEGRATION_BRANCH,
        base_branch=BASE,
        worktree=tmp_path / "integrate-worktree",
        repo_dir=tmp_path,
        commands=[PASS_CMD],
        allow_no_verification=False,
        store=store,
        run_id=RUN_ID,
        runner_factory=factory,
        engine=engine,
    )


@pytest.mark.parametrize("engine", ["yaml", "pygents"])
def test_resolve_conflict_walks_the_engine_it_is_given_with_the_same_arguments(
    monkeypatch, tmp_path: Path, engine: str
) -> None:
    """Spec test 4: `INTEGRATE` on pygents, the loaded `integrate` document on
    yaml, identical keywords (no `card`, no `parent_story`, no `resume_from`),
    and the factory gets the YAML document on both."""
    walks = _stub_both_walks(monkeypatch)
    factory_calls: list[dict[str, Any]] = []
    runner = object()

    def factory(**kwargs: Any) -> Any:
        factory_calls.append(kwargs)
        return runner

    store = _RecordingStore()

    summary = _resolve(store, factory, tmp_path, engine)

    assert summary.status == "done"
    other = "yaml" if engine == "pygents" else "pygents"
    assert walks[other] == []
    ((workflow, passed_store, kwargs),) = walks[engine]
    assert passed_store is store
    if engine == "pygents":
        assert workflow is integrate_workflow.INTEGRATE
    else:
        assert isinstance(workflow, loader.Workflow)
        assert workflow.name == "integrate"
    expected_subtask = models.SubtaskRun(
        card_id=STORY_B,
        branch=INTEGRATION_BRANCH,
        base_branch=BASE,
        status="started",
        worktree_path=tmp_path / "integrate-worktree",
    )
    assert store.subtasks == [("integrate", expected_subtask)]
    assert kwargs == {
        "story_id": "integrate",
        "subtask": expected_subtask,
        "repo_dir": tmp_path,
        "commands": [PASS_CMD],
        "extra_context": {
            "merge_tip": "m5/task-b",
            "conflict_files": ["shared.txt"],
            **cli.gate_context([PASS_CMD], False),
        },
        "agent_runner": runner,
    }
    (factory_call,) = factory_calls
    assert isinstance(factory_call["workflow"], loader.Workflow)
    assert factory_call["workflow"].name == "integrate"
    assert {key: value for key, value in factory_call.items() if key != "workflow"} == {
        "store": store,
        "run_id": RUN_ID,
        "story_id": "integrate",
        "card_id": STORY_B,
    }


@pytest.mark.parametrize("engine", ["PYGENTS", "bogus", ""])
def test_resolve_conflict_refuses_an_unknown_engine_before_recording_anything(
    monkeypatch, tmp_path: Path, engine: str
) -> None:
    """Review Focus 3: nothing recorded, no runner built, no walk."""
    walks = _stub_both_walks(monkeypatch)
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> Any:
        factory_calls.append(kwargs)
        return object()

    store = _RecordingStore()

    with pytest.raises(ValueError, match="unknown engine"):
        _resolve(store, factory, tmp_path, engine)

    assert store.subtasks == []
    assert factory_calls == []
    assert walks == {"yaml": [], "pygents": []}
```

- [ ] **Step 2: Write the failing tests (orchestrate) and update the fakes**

In `tests/test_orchestrate.py`, replace `FakeDriver.__call__`'s signature and recording (`:437-465`) so it accepts and records `engine`:

```python
    def __call__(
        self,
        *,
        store,
        run_id,
        card,
        parent,
        subtask,
        repo_dir,
        commands=(),
        allow_no_verification=False,
        runner_factory=None,
        should_stop=None,
        engine="yaml",
    ) -> cli.SubtaskDrive:
        self.calls.append(
            {
                "card": card.id,
                "parent": parent.id,
                "branch": subtask.branch,
                "base": subtask.base_branch,
                "worktree": subtask.worktree_path,
                "status": subtask.status,
                "run_id": run_id,
                "repo_dir": repo_dir,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "runner_factory": runner_factory,
                "engine": engine,
            }
        )
```

(the rest of `FakeDriver.__call__` from `self.snapshots.append(...)` on is unchanged).

Replace `IntegrateRecorder.__call__`'s signature and recording (`:509-535`):

```python
    def __call__(
        self,
        stories,
        repo_dir,
        base_branch,
        branch_prefix,
        commands,
        allow_no_verification,
        store,
        run_id,
        runner_factory,
        engine="yaml",
    ):
        stories = list(stories)
        self.calls.append(
            {
                "stories": [story.id for story in stories],
                "repo_dir": repo_dir,
                "base_branch": base_branch,
                "branch_prefix": branch_prefix,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "store": store,
                "run_id": run_id,
                "runner_factory": runner_factory,
                "engine": engine,
                "run_status": store.load_run(run_id).status,
            }
        )
```

(the rest is unchanged).

In `GatedDriver.__call__` (`:740-753`), add `engine="yaml",` after `should_stop=None,` in the signature. Its recorded dict is unchanged.

In `test_subtasks_run_in_order_each_stacked_on_the_one_before`, the whole-dict assertion (`:838-848`) gains the default engine:

```python
    assert integrate_call == {
        "stories": [story_a, story_b],
        "repo_dir": root,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "commands": ["uv run pytest"],
        "allow_no_verification": True,
        "run_id": run_id,
        "runner_factory": factory,
        "engine": "yaml",
        "run_status": "started",
    }
```

Add `import ast` to the imports at the top of `tests/test_orchestrate.py` (after `import json`, `:19`). Insert after `test_no_runner_factory_gives_integrate_cli_default_runner_factory_at_call_time` (after `:985`):

```python
@requires_git
@requires_brd
@pytest.mark.parametrize(
    "given, passed",
    [({}, "yaml"), ({"engine": "yaml"}, "yaml"), ({"engine": "pygents"}, "pygents")],
)
@pytest.mark.parametrize("lanes", [1, 2])
def test_run_milestone_hands_its_engine_to_every_driver_call_and_to_integrate(
    project, integrate_recorder, given, passed, lanes
):
    """Spec test 3: one engine per run, on every lane thread and on Integrate.
    With no argument the engine is `yaml`."""
    shape = _milestone(project, {"A": 2, "B": 1})
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver, max_concurrent=lanes, **given)

    assert result["done"] is True
    assert len(driver.calls) == 3
    assert [call["engine"] for call in driver.calls] == [passed] * 3
    assert [call["engine"] for call in integrate_recorder.calls] == [passed]


def _imported_modules(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_engine_selecting_modules_never_import_pygents():
    """Pygents-engine RULE 1: these three reach pygents through
    `agent_manager.runtime.engine` only. A guard: it passes before this card
    and must keep passing after it."""
    for module in (cli, orchestrate, integration):
        imported = _imported_modules(module)
        assert not any(n == "pygents" or n.startswith("pygents.") for n in imported), (
            module.__name__
        )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_integration.py tests/test_orchestrate.py -v`
Expected: the new integration tests FAIL with `TypeError: ... unexpected keyword argument 'engine'` (from `integrate_milestone` / `_resolve_conflict`). `test_run_milestone_hands_its_engine_to_every_driver_call_and_to_integrate` FAILS with `TypeError: run_milestone() got an unexpected keyword argument 'engine'` for the `{"engine": ...}` cases; its `{}` cases already PASS, since the fakes default to `"yaml"`, and they pin that default. Every pre-existing test still PASSES (the fakes' new `engine` parameter has a default). `test_the_engine_selecting_modules_never_import_pygents` PASSES (guard).

- [ ] **Step 4: Implement `integration.py`**

Replace the import block (`:25-34`) with:

```python
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_manager import cli, dag, engine, models
from agent_manager import engine as yaml_engine
from agent_manager.census import StoryPlan
from agent_manager.runtime import engine as runtime_engine
from agent_manager.steps import reducers, verify
from agent_manager.steps.integrate import MergeInProgressError, merge_tip
from agent_manager.store import Store
from agent_manager.workflow import integrate as integrate_workflow
from agent_manager.workflow.loader import load_builtin
```

Replace `_resolve_conflict` (`:124-173`) with:

```python
def _resolve_conflict(
    *,
    story_id: str,
    tip: str,
    files: list[str],
    branch: str,
    base_branch: str,
    worktree: Path,
    repo_dir: Path,
    commands: list[str],
    allow_no_verification: bool,
    store: Store,
    run_id: str,
    runner_factory: cli.RunnerFactory,
    engine: cli.Engine = "yaml",
) -> yaml_engine.SubtaskSummary:
    """Drive the `integrate` workflow once for one conflicting tip.

    The synthetic subtask is recorded before `run_subtask` journals its first
    phase. The caller has already recorded the synthetic story.

    `engine` picks the walk as `cli.drive_subtask` does (card 7fdec762): `yaml`
    walks the loaded `builtin/integrate.yaml`, `pygents` walks
    `workflow.integrate.INTEGRATE`, with the same keywords. The runner factory
    gets the loaded YAML document on both. The parameter shadows the
    module-level `engine` name here, so the walks are reached as `yaml_engine`
    and `runtime_engine`. Any other value is refused before anything is
    recorded.
    """
    if engine not in cli.ENGINES:
        raise ValueError(
            f"unknown engine {engine!r}; expected one of {', '.join(cli.ENGINES)}"
        )
    workflow = load_builtin(WORKFLOW_NAME)
    subtask = models.SubtaskRun(
        card_id=story_id,
        branch=branch,
        base_branch=base_branch,
        status="started",
        worktree_path=worktree,
    )
    store.record_subtask(INTEGRATE_STORY_ID, subtask)
    runner = runner_factory(
        workflow=workflow,
        store=store,
        run_id=run_id,
        story_id=INTEGRATE_STORY_ID,
        card_id=story_id,
    )
    walk = {
        "story_id": INTEGRATE_STORY_ID,
        "subtask": subtask,
        "repo_dir": repo_dir,
        "commands": commands,
        "extra_context": {
            "merge_tip": tip,
            "conflict_files": list(files),
            **cli.gate_context(commands, allow_no_verification),
        },
        "agent_runner": runner,
    }
    if engine == "pygents":
        return runtime_engine.run_subtask(integrate_workflow.INTEGRATE, store, **walk)
    return yaml_engine.run_subtask(workflow, store, **walk)
```

In `integrate_milestone`'s signature (`:187-197`) add the keyword after `runner_factory`:

```python
    run_id: str,
    runner_factory: cli.RunnerFactory,
    engine: cli.Engine = "yaml",
) -> IntegrateOutcome:
```

append to its docstring: `` `engine` is handed to every resolver dispatch unchanged. ``, and in its `_resolve_conflict(...)` call (`:238-251`) add `engine=engine,` after `runner_factory=runner_factory,`.

`_resolver_detail` (`:176-177`) keeps `engine.SubtaskSummary` in its annotation; it has no `engine` parameter, so nothing shadows it there.

- [ ] **Step 5: Implement `orchestrate.py`**

Replace the `Driver.__call__` signature (`:209-222`):

```python
    def __call__(
        self,
        *,
        store: Store,
        run_id: str,
        card: models.Card,
        parent: models.Card,
        subtask: models.SubtaskRun,
        repo_dir: Path,
        commands: Sequence[str] = (),
        allow_no_verification: bool = False,
        runner_factory: cli.RunnerFactory | None = None,
        should_stop: Callable[[], bool] | None = None,
        engine: cli.Engine = "yaml",
    ) -> cli.SubtaskDrive: ...
```

In `run_story_lane`'s signature (`:392-404`) add after `stop: RunStop,`:

```python
    stop: RunStop,
    engine: cli.Engine = "yaml",
) -> LaneOutcome:
```

and in its `drive(...)` call (`:433-444`) add `engine=engine,` after `should_stop=stop.event.is_set,`:

```python
            result = drive(
                store=store,
                run_id=run_id,
                card=card,
                parent=parent,
                subtask=row,
                repo_dir=root,
                commands=list(commands),
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                should_stop=stop.event.is_set,
                engine=engine,
            )
```

In `run_milestone`'s signature (`:500-512`) add after `max_concurrent: int = 1,`:

```python
    max_concurrent: int = 1,
    engine: cli.Engine = "yaml",
) -> dict[str, Any]:
```

append to its docstring: `` `engine` goes unchanged to every driver call on every lane thread and to Integrate; the preflight loads the YAML document on both engines. ``; in the `pool.submit(run_story_lane, ...)` call (`:576-588`) add `engine=engine,` after `stop=stop,`; and in `integration.integrate_milestone(...)` (`:610-620`) add `engine=engine,` after `runner_factory=factory,`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_integration.py tests/test_orchestrate.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/integration.py src/agent_manager/orchestrate.py tests/test_integration.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): thread engine through run_milestone and Integrate"
```

---

### Task 3: The `run --engine` option

**Files:**
- Modify: `src/agent_manager/cli.py:25` (typing import), `:992-1040` (`_check_run_targets`), `:1043-1135` (`run`)
- Test: `tests/test_cli.py` (whole-kwargs assertion `:2949-2961`; new tests after `test_an_explicit_max_concurrent_reaches_run_milestone`, which ends at `:2977`)

**Interfaces:**
- Consumes: `cli.Engine`, `cli.ENGINES`, `run_card(..., engine=)` (Task 1); `orchestrate.run_milestone(..., engine=)` (Task 2).
- Produces: `am run ... --engine {yaml|pygents}` (default `yaml`); `_check_run_targets(*, card, milestone, dry_run, max_concurrent=None, engine="yaml") -> None` raising `typer.BadParameter(..., param_hint="'--engine'")` for any other value.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, the whole-kwargs assertion in `test_a_milestone_run_calls_run_milestone_once_with_the_run_options` (`:2949-2961`) gains the default engine:

```python
    assert calls == [
        (
            "Milestone 3",
            {
                "repo_dir": tmp_path,
                "base_branch": "main",
                "branch_prefix": "m3",
                "commands": ["uv run pytest", "uv run ruff check"],
                "allow_no_verification": True,
                "max_concurrent": 4,
                "engine": "yaml",
            },
        )
    ]
```

Insert after `test_an_explicit_max_concurrent_reaches_run_milestone` (after `:2977`):

```python
@pytest.mark.parametrize("value", ["bogus", "PYGENTS", "Yaml", ""])
@pytest.mark.parametrize(
    "targets",
    [
        ["--card", SOME_CARD],
        ["--milestone", "2"],
        ["--milestone", "2", "--dry-run"],
    ],
)
def test_a_bad_engine_is_a_usage_error_that_starts_nothing(
    tmp_path, monkeypatch, targets, value
):
    """Spec test 1 and Review Focus 2: exit 2, NOTHING on stdout (usage errors
    go to stderr), no case folding, and no run directory. Every run path is
    forbidden, so reaching one fails the test."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))

    result = runner.invoke(
        cli.app,
        [
            "run",
            *targets,
            "--engine",
            value,
            "--repo-dir",
            str(tmp_path),
            "--branch-prefix",
            "m2",
        ],
    )

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "--engine" in result.output
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.parametrize(
    "extra, passed", [((), "yaml"), (("--engine", "yaml"), "yaml"), (("--engine", "pygents"), "pygents")]
)
def test_the_engine_reaches_run_card(tmp_path, monkeypatch, extra, passed):
    """No git or brd: `run_card` is replaced. Without `--engine` it gets `yaml`."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, "a6c7bff3-0000-4000-8000-000000000002")

    monkeypatch.setattr(cli, "run_card", fake_run_card)

    result = runner.invoke(
        cli.app,
        ["run", "--card", SOME_CARD, "--repo-dir", str(tmp_path), "--branch-prefix", "m2", *extra],
    )

    assert result.exit_code == 0, result.output
    assert seen["engine"] == passed


@pytest.mark.parametrize(
    "extra, passed", [((), "yaml"), (("--engine", "yaml"), "yaml"), (("--engine", "pygents"), "pygents")]
)
def test_the_engine_reaches_run_milestone(tmp_path, monkeypatch, extra, passed):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_milestone(monkeypatch, CLEAN_MILESTONE)

    result = _milestone_run(tmp_path, *extra)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(CLEAN_MILESTONE)
    ((_, kwargs),) = calls
    assert kwargs["engine"] == passed


def test_a_dry_run_accepts_and_ignores_the_engine(tmp_path, monkeypatch):
    """Review Focus 4: the preview is the same call it always was -- no
    `engine` key reaches it -- and nothing is driven or written."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_dry_run_milestone(needle, **kwargs):
        calls.append((needle, kwargs))
        return {"max_concurrent": kwargs["max_concurrent"], "levels": [], "already_done": []}

    monkeypatch.setattr(cli, "dry_run_milestone", fake_dry_run_milestone)

    result = _milestone_run(tmp_path, "--dry-run", "--engine", "pygents")

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "Milestone 3",
            {
                "repo_dir": tmp_path,
                "branch_prefix": "m3",
                "base_branch": "main",
                "max_concurrent": 4,
            },
        )
    ]
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "milestone_run_calls_run_milestone_once or bad_engine or engine_reaches or dry_run_accepts_and_ignores" -v`
Expected: FAIL. Every `--engine` invocation exits 2 with `No such option: --engine`, so the forwarding and dry-run tests fail on `exit_code == 0`, and the no-`--engine` forwarding cases fail with `KeyError: 'engine'`. The whole-kwargs test fails on the missing `"engine": "yaml"` key. The bad-engine test may already pass at this point, because Typer refuses the unknown option with exit 2 and an empty stdout too; it is a guard that must keep passing once `--engine` exists and refuses the value itself (Step 4).

- [ ] **Step 3: Implement**

In `src/agent_manager/cli.py`, change the typing import (`:25`) to:

```python
from typing import Any, Literal, Protocol, cast, get_args
```

Replace `_check_run_targets` (`:992-1040`) with the same function plus one parameter and one refusal:

```python
def _check_run_targets(
    *,
    card: str | None,
    milestone: str | None,
    dry_run: bool,
    max_concurrent: int | None = None,
    engine: str = "yaml",
) -> None:
    """Refuse a bad `--card` / `--milestone` / `--dry-run` / `--max-concurrent` / `--engine` combination as a usage error.

    `typer.BadParameter` is Typer's own exit 2, which `EXIT_ERROR`'s docstring
    reserves. It is raised before the `HANDLED` try block, so nothing is read
    or dispatched. A blank `--milestone` is refused here too: the census strips
    the needle, and an empty needle is a substring of every title, so on a
    one-milestone board it would silently pick that milestone.
    `--max-concurrent` is `None` when not given, so giving it with `--card` is
    refused whatever its value, the default included. The Option has no
    `min=1`, so a value below 1 is refused here, worded and routed like every
    other run-target refusal. `--engine` is typed `str` because Typer 0.27.2
    cannot take a `Literal` annotation, so a value outside `ENGINES` is refused
    here, exactly and without case folding; `--dry-run` still validates it.
    """
    if card is not None and milestone is not None:
        raise typer.BadParameter(
            "give --card or --milestone, not both",
            param_hint="'--card' / '--milestone'",
        )
    if card is None and milestone is None:
        raise typer.BadParameter(
            "one of --card or --milestone is required",
            param_hint="'--card' / '--milestone'",
        )
    if milestone is not None and not milestone.strip():
        raise typer.BadParameter(
            "--milestone needs a card id or a title substring, not a blank string",
            param_hint="'--milestone'",
        )
    if dry_run and card is not None:
        raise typer.BadParameter(
            "--dry-run previews a milestone and does not apply to --card",
            param_hint="'--dry-run'",
        )
    if max_concurrent is not None and max_concurrent < 1:
        raise typer.BadParameter(
            f"--max-concurrent must be at least 1, got {max_concurrent}",
            param_hint="'--max-concurrent'",
        )
    if card is not None and max_concurrent is not None:
        raise typer.BadParameter(
            "--max-concurrent applies only to --milestone",
            param_hint="'--max-concurrent'",
        )
    if engine not in ENGINES:
        raise typer.BadParameter(
            f"--engine must be one of {', '.join(ENGINES)}, got {engine!r}",
            param_hint="'--engine'",
        )
```

In `run`'s parameters (`:1044-1094`), add before `pretty: bool = ...`:

```python
    engine: str = typer.Option(
        "yaml",
        "--engine",
        help=(
            "Which engine walks each subtask: `yaml` (the default) or `pygents`. "
            "--dry-run accepts it and ignores it."
        ),
    ),
```

Replace the head of `run`'s body (`:1096-1099`) with:

```python
    _check_run_targets(
        card=card,
        milestone=milestone,
        dry_run=dry_run,
        max_concurrent=max_concurrent,
        engine=engine,
    )
    selected = cast(Engine, engine)
    lanes = DEFAULT_MAX_CONCURRENT if max_concurrent is None else max_concurrent
```

In the `orchestrate.run_milestone(...)` call (`:1118-1126`) add `engine=selected,` after `max_concurrent=lanes,`; in the `run_card(...)` call (`:1128-1135`) add `engine=selected,` after `commands=list(verify),`. The `dry_run_milestone(...)` call is unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, including `test_bad_run_targets_are_usage_errors_that_start_nothing` (its target list is refused before the engine check) and the `--card` tests that patch `run_card` with `**kwargs` fakes.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): --engine selects the YAML or the pygents engine"
```

---

### Task 4: Run the fake-claude e2e tier on both engines

**Files:**
- Modify: `tests/e2e/conftest.py` (imports `:11-24`, `project` `:172-177`, `completed_run` `:203-218`, `run_milestone_cli` `:436-471`, new fixtures appended at the end)
- Modify: `tests/e2e/test_production_wiring.py` (imports `:11-18`, new fixture and tests)
- Modify: `tests/e2e/test_milestone_run.py` (imports `:14-18`, the two scenario tests)
- Modify: `tests/e2e/test_parallel_milestone.py` (imports `:15-21`, the two clean-run tests)

**Interfaces:**
- Consumes: `cli.run_card(..., engine=)` (Task 1), `am run --engine` (Task 3), `store.open_db`, the `checkpoints` table (`src/agent_manager/store.py:95-105`, sibling 3d4947e8, already on the branch).
- Produces (fixtures in `tests/e2e/conftest.py`): `engine` (module-scoped, returns `"yaml"`; a module overrides it with `params=["yaml", "pygents"]`); `engine_parity -> Callable[[str, str, Mapping[str, Any]], None]` called as `engine_parity(key, engine, data, *, tmp: Path, cards: Mapping[str, str])`; `board_card_labels -> Callable[[Mapping[str, Any]], dict[str, str]]`; `checkpoint_rows -> Callable[[Path, str], int]`.

Tier: fake-claude e2e, `tests/e2e/`, unmarked (spec Tests 5-8). Engine parametrization uses a module-scoped fixture with `params`, which adds no marker, so every `test_this_module_runs_in_the_default_suite_unmarked` guard (which requests no `engine`) stays a single unparametrized test and keeps passing. Real-harness modules reuse `project` and get the conftest default `engine == "yaml"`, so their paid runs are not doubled.

- [ ] **Step 1: Write the conftest fixtures and the parametrized tests**

In `tests/e2e/conftest.py`, change `from collections.abc import Callable, Sequence` (`:16`) to:

```python
from collections.abc import Callable, Mapping, Sequence
```

Insert before the `project` fixture (before `:172`):

```python
@pytest.fixture(scope="module")
def engine() -> str:
    """The engine every run in a module uses: `yaml` unless the module overrides
    this fixture with `params=["yaml", "pygents"]` (card 7fdec762).

    Module-scoped so the module-scoped `project` can depend on it: an override
    with params then builds one repo and board per engine, and pytest runs a
    module's tests grouped by engine."""
    return "yaml"
```

Replace `project` (`:172-177`) with:

```python
@pytest.fixture(scope="module")
def project(tmp_path_factory, module_monkeypatch, toolchain, engine) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board.

    One per engine: a module that runs on both engines drives the same card
    twice, and a card a run moved to `done` cannot be driven again."""
    base = tmp_path_factory.mktemp(f"e2e-{engine}")
    module_monkeypatch.setenv("XDG_DATA_HOME", str(base / "xdg"))
    return _init_project(base / "project", "e2e-board")
```

Replace `completed_run` (`:203-218`) with:

```python
@pytest.fixture(scope="module")
def completed_run(project, cards, fake_claude_bin, engine) -> dict[str, Any]:
    """One real `cli.run_card`, with NO `runner_factory`, on the module's engine.

    That omission is the point: `cli.default_runner_factory` (`cli.py:588`)
    builds the real `dispatch.AgentRunner` with the real
    `harness/launcher.py:94` `run_direct`, which `Popen`s the real
    `ClaudeAdapter` argv -- resolved on `PATH` to `fake_claude_bin`.
    """
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        commands=list(VERIFY_COMMANDS),
        engine=engine,
    )
```

Replace `run_milestone_cli` (`:436-471`) with:

```python
@pytest.fixture
def run_milestone_cli(fake_claude_bin, engine) -> Callable[..., Any]:
    """`am run --milestone --engine <engine>` through `CliRunner`, with no runner_factory anywhere.

    Depends on `fake_claude_bin` so the fake is first on `PATH`: the real
    `ClaudeAdapter` resolves `claude` to it through the real `run_direct`.
    `--engine` is always passed; in a module that does not override `engine`
    it is `yaml`, the default, so those runs are unchanged.
    """
    runner = CliRunner()

    def invoke(
        root: Path,
        milestone: str,
        max_concurrent: int | None = None,
        verify: Sequence[str] | None = None,
    ):
        argv = [
            "run",
            "--milestone",
            milestone,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            MILESTONE_PREFIX,
            "--engine",
            engine,
        ]
        commands = VERIFY_COMMANDS if verify is None else tuple(verify)
        for command in commands:
            argv += ["--verify", command]
        # Only when asked: existing callers keep their exact argv.
        if max_concurrent is not None:
            argv += ["--max-concurrent", str(max_concurrent)]
        return runner.invoke(cli.app, argv)

    return invoke
```

Append at the end of `tests/e2e/conftest.py`:

```python
def _engine_neutral(
    data: Mapping[str, Any], *, tmp: Path, cards: Mapping[str, str]
) -> dict[str, Any]:
    """`data` with everything one board or one run owns replaced by a label.

    The two engines run on two boards, so card ids, their short ids, the tmp
    dir (repo, worktrees and `XDG_DATA_HOME` all live under it) and the run id
    differ by construction. Everything else must match. `run_id` itself is
    dropped: the spec compares `data` ignoring `run_id` and timestamps. Longest
    needle first, so a card id is labelled before its own short id.
    """
    labels: dict[str, str] = {str(tmp): "<tmp>", str(tmp.resolve()): "<tmp>"}
    run_id = data.get("run_id")
    if isinstance(run_id, str):
        labels[run_id] = "<run_id>"
    for label, card_id in cards.items():
        labels[card_id] = f"<{label}>"
        labels[dag.short_id(card_id)] = f"<{label}:short>"
    text = json.dumps(data, sort_keys=True)
    for needle in sorted(labels, key=len, reverse=True):
        text = text.replace(needle, labels[needle])
    neutral = json.loads(text)
    neutral.pop("run_id", None)
    return neutral


@pytest.fixture(scope="session")
def engine_parity() -> Callable[..., None]:
    """Spec G10: the same scenario's `data` is the same on every engine.

    `check(key, engine, data, tmp=..., cards=...)` records the engine-neutral
    form of `data` under `key` and asserts it equals every other engine's
    record for that key. Whichever engine runs second does the comparing, so
    the check does not depend on test order; a lone engine (a `-k` selection)
    has nothing to compare against and passes.
    """
    seen: dict[str, dict[str, Any]] = {}

    def check(
        key: str,
        engine: str,
        data: Mapping[str, Any],
        *,
        tmp: Path,
        cards: Mapping[str, str],
    ) -> None:
        neutral = _engine_neutral(data, tmp=tmp, cards=cards)
        by_engine = seen.setdefault(key, {})
        by_engine[engine] = neutral
        for other, recorded in by_engine.items():
            assert recorded == neutral, (key, other, engine)

    return check


@pytest.fixture
def board_card_labels() -> Callable[[Mapping[str, Any]], dict[str, str]]:
    """A board fixture's cards as `{label: card id}`, for `engine_parity`.

    Works for `milestone_board` and `parallel_board`: `milestone`, then
    `story-<KEY>` and `<key><n>` for the n-th subtask of that story."""

    def labels(shape: Mapping[str, Any]) -> dict[str, str]:
        found = {"milestone": shape["milestone"]}
        for key, story in shape["stories"].items():
            found[f"story-{key}"] = story
            for n, subtask in enumerate(shape["subtasks"][key], start=1):
                found[f"{key.lower()}{n}"] = subtask
        return found

    return labels


@pytest.fixture
def checkpoint_rows() -> Callable[[Path, str], int]:
    """How many `checkpoints` rows one run wrote.

    Review Focus 1: only the pygents engine checkpoints (its BEFORE_TURN hook),
    so a pygents run with zero rows means `--engine` never reached the walk and
    every engine-parametrized assertion passed vacuously on yaml twice."""

    def count(root: Path, run_id: str) -> int:
        conn = store.open_db(cli.resolve_repo_dir(root))
        try:
            (rows,) = conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        return int(rows)

    return count
```

In `tests/e2e/test_production_wiring.py`, add `import pytest` after `from pathlib import Path` (`:14`), and insert after `AGENT_PHASES` (after `:28`):

```python
@pytest.fixture(scope="module", params=["yaml", "pygents"])
def engine(request) -> str:
    """Overrides the conftest's `engine`: every test in this module that reads
    the run runs once per engine, each on its own repo and board (spec test 5).
    Fixture params, not a parametrize mark, so no marker reaches this module."""
    return request.param
```

Append at the end of `tests/e2e/test_production_wiring.py`:

```python
def test_the_selected_engine_is_the_one_that_walked(
    engine, project, completed_run, checkpoint_rows
):
    """Review Focus 1: non-vacuity for the whole parametrization."""
    rows = checkpoint_rows(project, completed_run["run_id"])
    if engine == "pygents":
        assert rows > 0, "a pygents run wrote no checkpoint: --engine never reached the walk"
    else:
        assert rows == 0, rows


def test_the_run_data_is_the_same_on_both_engines(
    engine, project, cards, completed_run, engine_parity
):
    """Spec test 5 / G10: `run_card`'s data, ignoring `run_id`."""
    engine_parity(
        "production-wiring", engine, completed_run, tmp=project.parent, cards=cards
    )
```

In `tests/e2e/test_milestone_run.py`, add `import pytest` after `from pathlib import Path` (`:16`), and insert after `_branches` (after `:59`):

```python
@pytest.fixture(scope="module", params=["yaml", "pygents"])
def engine(request) -> str:
    """Overrides the conftest's `engine` (spec test 6). Fixture params, not a
    parametrize mark, so no marker reaches this module."""
    return request.param
```

Change the signature of `test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line` (`:62-64`) to:

```python
def test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line(
    milestone_board,
    run_milestone_cli,
    engine,
    tmp_path,
    engine_parity,
    board_card_labels,
    checkpoint_rows,
):
```

and append at the end of its body (after `:114`):

```python
    rows = checkpoint_rows(root, data["run_id"])
    assert (rows > 0) is (engine == "pygents"), (engine, rows)
    engine_parity(
        "milestone-clean", engine, data, tmp=tmp_path, cards=board_card_labels(milestone_board)
    )
```

Change the signature of `test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it` (`:138-140`) to:

```python
def test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it(
    milestone_board,
    review_fail_marker,
    run_milestone_cli,
    read_fake_log,
    engine,
    tmp_path,
    engine_parity,
    board_card_labels,
):
```

add `labels = board_card_labels(milestone_board)` as the first line of its body, and add these three lines, each right after the named assertion block:

after `assert c_status_after == c_status_before` (`:190`):

```python
    engine_parity("milestone-relaunch-stopped", engine, stopped, tmp=tmp_path, cards=labels)
```

after `assert _is_ancestor(root, branches[b1], branches[c1])` (`:210`):

```python
    # On pygents too, the relaunch re-drives b1 from its first phase (no
    # checkpoint continuation here -- that is card 02890d5d's).
    engine_parity("milestone-relaunch-finished", engine, finished, tmp=tmp_path, cards=labels)
```

after `assert read_fake_log(idle["run_id"]) == []` (`:220`):

```python
    engine_parity("milestone-relaunch-idle", engine, idle, tmp=tmp_path, cards=labels)
```

In `tests/e2e/test_parallel_milestone.py`, add `import pytest` after `from pathlib import Path` (`:18`), and insert after `INTEGRATION_BRANCH`'s docstring (after `:88`):

```python
@pytest.fixture(scope="module", params=["yaml", "pygents"])
def engine(request) -> str:
    """Overrides the conftest's `engine` (spec test 7): every lane scenario
    runs once per engine, and on pygents each lane thread's walk does its own
    `asyncio.run`. Fixture params, not a parametrize mark, so no marker reaches
    this module."""
    return request.param
```

Change the signature of `test_two_lanes_overlap_in_implement_and_the_milestone_finishes` (`:98-100`) to:

```python
def test_two_lanes_overlap_in_implement_and_the_milestone_finishes(
    parallel_board,
    rendezvous,
    run_milestone_cli,
    engine,
    tmp_path,
    engine_parity,
    board_card_labels,
    checkpoint_rows,
):
```

and append at the end of its body (after `:145`):

```python
    rows = checkpoint_rows(root, data["run_id"])
    assert (rows > 0) is (engine == "pygents"), (engine, rows)
    engine_parity(
        "parallel-two-lanes", engine, data, tmp=tmp_path, cards=board_card_labels(parallel_board)
    )
```

Change the signature of `test_one_lane_runs_the_level_s_stories_one_after_the_other` (`:158-160`) to:

```python
def test_one_lane_runs_the_level_s_stories_one_after_the_other(
    parallel_board,
    rendezvous,
    run_milestone_cli,
    engine,
    tmp_path,
    engine_parity,
    board_card_labels,
):
```

and append at the end of its body (after `:177`):

```python
    engine_parity(
        "parallel-one-lane", engine, data, tmp=tmp_path, cards=board_card_labels(parallel_board)
    )
```

The journal, escalation and relaunch tests in this module need no body change: they request `run_milestone_cli`, which requests `engine`, so they already run once per engine. They get no parity call on purpose: which phase boundary lane B parks at is not fixed even between two yaml runs (the module's own determinism note on `_launch_with_a1_review_failing`), so their `data` cannot be compared across runs; their existing assertions are what must hold on both engines.

- [ ] **Step 2: Run the e2e tier and confirm the tests are collected per engine**

Run: `uv run pytest tests/e2e -v`
Expected: every scenario test appears twice, as `[yaml]` and `[pygents]`; each `test_this_module_runs_in_the_default_suite_unmarked` appears once; `test_integrate.py` tests appear once (conftest default `yaml`); the real-harness tests are deselected by `addopts`. All PASS. These are acceptance tests over wiring Tasks 1-3 already built, so they are expected to pass on their first run. If a `[pygents]` case fails while its `[yaml]` twin passes, that is a real parity gap in `src/agent_manager/runtime/`: stop and report it with the failing assertion. Do not patch `runtime/` under this card (RULE 3) and do not weaken the assertion.

- [ ] **Step 3: Watch the non-vacuity guard fail (sabotage check, then revert)**

Temporarily change the dispatch line in `cli.drive_subtask` (from Task 1) from `if engine == "pygents":` to `if False:`, then run:

Run: `uv run pytest tests/e2e/test_production_wiring.py -k "selected_engine" -v`
Expected: `test_the_selected_engine_is_the_one_that_walked[pygents]` FAILS with `a pygents run wrote no checkpoint: --engine never reached the walk`; `[yaml]` passes.

Revert the change to `if engine == "pygents":` and re-run the same command.
Expected: both PASS. Confirm `git diff src/agent_manager/cli.py` shows no leftover of the sabotage.

- [ ] **Step 4: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, the whole default suite green on both engines, parallel milestone included.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_production_wiring.py tests/e2e/test_milestone_run.py tests/e2e/test_parallel_milestone.py
git commit -m "test(e2e): run the fake-claude tier on both engines"
```
