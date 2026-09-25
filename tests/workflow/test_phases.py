"""Pure-data tier (spec §9): phases.py has no I/O beyond validate()'s role
loading, so these are plain unit tests -- no fakes, no git, no harness."""

import ast
import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent_manager import results
from agent_manager.steps import plan_check as plan_check_module
from agent_manager.steps import reducers, rollup
from agent_manager.workflow import phases as phases_module
from agent_manager.workflow.loader import load_builtin, load_workflow
from agent_manager.workflow.phases import (
    AgentPhase,
    Goto,
    Retry,
    Step,
    Workflow,
    WorkflowError,
    from_loader,
)
from agent_manager.workflow.registry import (
    FunctionRegistry,
    UnknownFunctionError,
    default_registry,
)

LAUNCHER = timedelta(minutes=10)


def step_fn(worktree): return {"ok": True}
def gate_ok(result): return None
def other_gate(result): return None


def wf(*phases): return Workflow("t", tuple(phases))


def agent(name, **kw):
    kw.setdefault("role", "explorer")
    kw.setdefault("inputs", ("card",))
    kw.setdefault("result", None)
    return AgentPhase(name, **kw)


def test_phases_module_never_imports_pygents():
    tree = ast.parse(Path(phases_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name == "pygents" or name.startswith("pygents.") for name in imported)
    assert "No pygents import here." in (phases_module.__doc__ or "")


def test_defaults_match_the_declared_shape():
    step = Step("a", step_fn)
    assert (step.args, step.gates, step.best_effort, step.when, step.skip_to) == ({}, (), False, None, None)
    phase = agent("b")
    assert phase.gates == () and phase.retry is None and phase.writes is None
    assert phase.timeout == timedelta(minutes=30) and phase.on_fail is None
    assert Goto("b").max_loops == 1
    assert Retry(2, ("gate_failed",)).on == ("gate_failed",)


def test_phase_model_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Step("a", step_fn).name = "b"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        agent("a").timeout = timedelta(0)  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        wf(Step("a", step_fn)).name = "u"  # type: ignore[misc]


def test_phase_names_and_lookup():
    a, b = Step("a", step_fn), agent("b")
    workflow = wf(a, b)
    assert workflow.phase_names == ("a", "b")
    assert workflow.phase("b") is b


def test_unknown_phase_lookup_lists_known_names():
    with pytest.raises(WorkflowError, match=r"no phase named 'zz'.*a, b") as info:
        wf(Step("a", step_fn), agent("b")).phase("zz")
    assert info.value.phase is None


def test_workflow_error_names_the_phase():
    error = WorkflowError("boom", phase="spec")
    assert isinstance(error, ValueError)
    assert error.phase == "spec"
    assert str(error) == "phase 'spec': boom"
    assert str(WorkflowError("boom")) == "boom"


def test_valid_workflow_passes():
    wf(Step("a", step_fn), agent("b"), agent("c", on_fail=Goto("b"))).validate(launcher_timeout=LAUNCHER)


@pytest.mark.parametrize("phases, needle", [
    ((Step("a", step_fn), Step("a", step_fn)), "duplicate"),
    ((Step("a", step_fn, skip_to="a", when=gate_ok),), "skip_to"),
    ((Step("a", step_fn, when=gate_ok),), "when"),
    ((Step("a", step_fn, skip_to="b"), Step("b", step_fn)), "when"),
    ((agent("a", on_fail=Goto("b")), agent("b")), "Goto"),
    ((agent("a"), agent("b", on_fail=Goto("a", max_loops=0))), "max_loops"),
    ((agent("a", role="no_such_role"),), "role"),
    ((agent("a", inputs=("nonsense",)),), "input"),
    ((agent("a", timeout=timedelta(minutes=10)),), "timeout"),
    # Review Focus 2: the other non-strict directions and unknown targets.
    ((Step("a", step_fn), Step("b", step_fn, when=gate_ok, skip_to="a")), "skip_to .* must name a later phase"),
    ((Step("a", step_fn, when=gate_ok, skip_to="nowhere"),), "skip_to .* must name a later phase"),
    ((agent("a", on_fail=Goto("a")),), "Goto .* must name an earlier phase"),
    ((agent("a"), agent("b", on_fail=Goto("nowhere"))), "Goto .* must name an earlier phase"),
])
def test_validate_refuses(phases, needle):
    with pytest.raises(WorkflowError, match=needle) as info:
        wf(*phases).validate(launcher_timeout=LAUNCHER)
    assert info.value.phase is not None


def test_duplicate_is_checked_before_any_other_rule():
    # The first "a" has a bad role, but the duplicate is reported first.
    with pytest.raises(WorkflowError, match="duplicate") as info:
        wf(agent("a", role="no_such_role"), Step("a", step_fn)).validate(launcher_timeout=LAUNCHER)
    assert info.value.phase == "a"


def test_when_without_skip_to_names_both_fields():
    with pytest.raises(WorkflowError, match=r"when.*skip_to") as info:
        wf(Step("a", step_fn, when=gate_ok), Step("b", step_fn)).validate(launcher_timeout=LAUNCHER)
    assert info.value.phase == "a"


def test_an_earlier_phase_name_is_a_valid_input():
    wf(agent("explore"), agent("spec", inputs=("explore",))).validate(launcher_timeout=LAUNCHER)


def test_an_earlier_phase_name_outside_the_resolver_table_is_a_valid_input():
    # "explore" above is also a _TABLE key; "design" is not, so only the
    # earlier-phase rule can accept it.
    wf(agent("design"), agent("impl", inputs=("card", "design"))).validate(launcher_timeout=LAUNCHER)


def test_an_input_naming_a_later_or_same_phase_is_refused():
    # Review Focus 1: only a strictly earlier phase can feed an input.
    with pytest.raises(WorkflowError, match="input 'b' has no resolver and no earlier phase") as info:
        wf(agent("a", inputs=("b",)), agent("b")).validate(launcher_timeout=LAUNCHER)
    assert info.value.phase == "a"
    with pytest.raises(WorkflowError, match="input 'a'"):
        wf(agent("a", inputs=("a",))).validate(launcher_timeout=LAUNCHER)


def test_role_failure_is_chained_and_honours_role_root(tmp_path):
    # Review Focus 3: an empty role_root means even a shipped role cannot load.
    from agent_manager.roles.loader import RoleBundleError

    with pytest.raises(WorkflowError, match="role 'explorer' does not load") as info:
        wf(agent("a")).validate(launcher_timeout=LAUNCHER, role_root=tmp_path)
    assert info.value.phase == "a"
    assert isinstance(info.value.__cause__, RoleBundleError)


def test_timeout_just_above_the_launcher_passes():
    # Review Focus 4: G2 is strict, so one second over is enough.
    wf(agent("a", timeout=LAUNCHER + timedelta(seconds=1))).validate(launcher_timeout=LAUNCHER)
    with pytest.raises(WorkflowError, match="timeout .* must exceed the launcher timeout"):
        wf(agent("a", timeout=LAUNCHER - timedelta(seconds=1))).validate(launcher_timeout=LAUNCHER)


def test_digest_is_stable_and_sensitive():
    base = wf(Step("a", step_fn, gates=(gate_ok,)), agent("b"))
    assert base.digest() == wf(Step("a", step_fn, gates=(gate_ok,)), agent("b")).digest()
    for changed in (
        wf(agent("b"), Step("a", step_fn, gates=(gate_ok,))),              # reorder
        wf(Step("a2", step_fn, gates=(gate_ok,)), agent("b")),             # rename
        wf(Step("a", step_fn, gates=(other_gate,)), agent("b")),           # gate swap
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", retry=Retry(2, ("gate_failed",)))),
        # Review Focus 5: the rest of the spec's must-change list.
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", timeout=timedelta(minutes=31))),
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", role="planner")),
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", inputs=("card", "branch"))),
    ):
        assert changed.digest() != base.digest()


def test_digest_is_a_sha256_hex_string():
    digest = wf(Step("a", step_fn)).digest()
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_digest_changes_when_skip_to_or_on_fail_is_retargeted():
    def skipping(target):
        return wf(Step("a", step_fn, when=gate_ok, skip_to=target), Step("b", step_fn), Step("c", step_fn))

    assert skipping("b").digest() != skipping("c").digest()

    def looping(target):
        return wf(agent("a"), agent("b"), agent("c", on_fail=Goto(target)))

    assert looping("a").digest() != looping("b").digest()
    assert looping("a").digest() != wf(agent("a"), agent("b"), agent("c", on_fail=Goto("a", max_loops=2))).digest()


def test_digest_ignores_args_order():
    # Review Focus 5: args are sorted, so insertion order is not identity.
    first = wf(Step("a", step_fn, args={"x": 1, "y": 2}))
    second = wf(Step("a", step_fn, args={"y": 2, "x": 1}))
    assert first.digest() == second.digest()
    assert first.digest() != wf(Step("a", step_fn, args={"x": 1, "y": 3})).digest()


def other_step_fn(worktree): return {"ok": True}
def other_when(state): return True


class ResultA(BaseModel):
    ok: bool


class ResultB(BaseModel):
    ok: bool


@pytest.mark.parametrize("changed", [
    Step("s", other_step_fn),                                  # run
    Step("s", step_fn, best_effort=True),                      # best_effort
    Step("s", step_fn, when=other_when, skip_to="z"),          # when
    Step("s", step_fn, gates=(gate_ok, other_gate)),           # an added gate
], ids=["run", "best_effort", "when", "gates"])
def test_digest_covers_every_step_field(changed):
    base = Step("s", step_fn, when=gate_ok, skip_to="z") if changed.when else Step("s", step_fn)
    tail = (agent("z"),)
    assert wf(changed, *tail).digest() != wf(base, *tail).digest()


@pytest.mark.parametrize("field, before, after", [
    ("result", ResultA, ResultB),
    ("writes", None, "docs/plan.md"),
    ("gates", (gate_ok,), (other_gate,)),
])
def test_digest_covers_every_agent_field(field, before, after):
    assert wf(agent("a", **{field: before})).digest() != wf(agent("a", **{field: after})).digest()


# --- from_loader (card 0326732a) -------------------------------------------


def fake_run_suite(worktree): return {"passed": True}
def fake_verification_passed_gate(result): return None
def fake_merge_completed_gate(result): return None
def fake_when(state): return True
def fake_run(worktree): return {"ok": True}


def test_from_loader_resolves_every_name_of_the_shipped_task():
    loaded = load_builtin("task", default_registry())
    converted = from_loader(loaded)
    assert converted.phase_names == tuple(p.name for p in loaded.phases)
    review = converted.phase("review")
    assert review.result is results.ReviewResult
    assert reducers.review_gate in review.gates
    assert reducers.plan_hash_gate_adapter in review.gates
    assert converted.phase("explore").retry == Retry(2, ("schema_invalid", "gate_failed"))
    plan_check = converted.phase("plan_check")
    assert plan_check.skip_to == "docs_commit"
    assert plan_check.when is plan_check_module.has_validated_plan
    assert plan_check.run is plan_check_module.find_validated_plan


def test_from_loader_keeps_fakes_from_a_test_registry():
    # The engine tests build workflows from fake registries; the conversion
    # must carry whatever callable the loaded workflow holds, by identity.
    loaded = load_builtin("integrate", default_registry())
    assert from_loader(loaded).phase("verify").run is loaded.function("verify.run_suite")

    fakes = FunctionRegistry()
    fakes.register("merge_completed_gate", fake_merge_completed_gate)
    fakes.register("verify.run_suite", fake_run_suite)
    fakes.register("verification_passed_gate", fake_verification_passed_gate)
    converted = from_loader(load_builtin("integrate", fakes))
    assert converted.phase("verify").run is fake_run_suite
    assert converted.phase("verify").gates == (fake_verification_passed_gate,)
    assert converted.phase("resolve").gates == (fake_merge_completed_gate,)


def test_from_loader_maps_a_deterministic_phase_field_by_field():
    loaded = load_builtin("task", default_registry())
    step = from_loader(loaded).phase("mark_in_progress")
    assert isinstance(step, Step)
    assert step.run is rollup.set_status
    assert step.args == {"status": "in_progress"}
    # A copy, not the loader model's own dict (Review Focus 4).
    assert step.args is not loaded.phase("mark_in_progress").args
    assert step.best_effort is True
    assert step.when is None and step.skip_to is None and step.gates == ()


def test_from_loader_maps_an_agent_phase_field_by_field():
    converted = from_loader(load_builtin("task", default_registry()))
    spec = converted.phase("spec")
    assert isinstance(spec, AgentPhase)
    assert spec.role == "spec_author"
    assert spec.inputs == ("card", "explore", "spec_path")
    assert spec.result is results.SpecResult
    assert spec.writes == "docs/superpowers/specs/{stem}.md"
    assert spec.retry is None and spec.gates == ()
    assert all(p.on_fail is None for p in converted.phases if isinstance(p, AgentPhase))


def test_from_loader_puts_the_timeout_on_every_agent_phase():
    loaded = load_builtin("task", default_registry())
    agents = [p for p in from_loader(loaded).phases if isinstance(p, AgentPhase)]
    assert agents and all(p.timeout == timedelta(minutes=30) for p in agents)
    longer = from_loader(loaded, timeout=timedelta(minutes=45))
    assert all(p.timeout == timedelta(minutes=45)
               for p in longer.phases if isinstance(p, AgentPhase))


def test_from_loader_digest_is_reproducible_across_loads():
    # Review Focus 5: sibling 04a5b91e pins declared digests to this value.
    first = from_loader(load_builtin("task", default_registry())).digest()
    second = from_loader(load_builtin("task", default_registry())).digest()
    assert first == second


def _loaded_with_agent(agent_extra: str):
    """A two-phase `loader.Workflow`: an agent phase `ask` with `agent_extra`
    YAML lines appended, then a deterministic phase `later`."""
    registry = FunctionRegistry()
    registry.register("fake_when", fake_when)
    registry.register("fake_run", fake_run)
    document = (
        "name: t\n"
        "phases:\n"
        "  - name: ask\n"
        "    kind: agent\n"
        "    role: explorer\n"
        f"{agent_extra}"
        "  - name: later\n"
        "    kind: deterministic\n"
        "    run: fake_run\n"
    )
    return load_workflow(document, registry)


def test_from_loader_rejects_an_unknown_result_name():
    loaded = _loaded_with_agent("    result: NoSuchResult\n")
    with pytest.raises(WorkflowError, match="NoSuchResult") as info:
        from_loader(loaded)
    assert info.value.phase == "ask"


@pytest.mark.parametrize("agent_extra", [
    "    when: fake_when\n",
    "    skip_to: later\n",
    "    when: fake_when\n    skip_to: later\n",
], ids=["when", "skip_to", "both"])
def test_from_loader_rejects_when_on_an_agent_phase(agent_extra):
    loaded = _loaded_with_agent(agent_extra)
    with pytest.raises(WorkflowError, match=r"when.*skip_to") as info:
        from_loader(loaded)
    assert info.value.phase == "ask"


def test_from_loader_lets_an_unresolved_name_raise_unknown_function_error():
    # Review Focus 3: the loader's own error, not a KeyError or a WorkflowError.
    stripped = _loaded_with_agent("").model_copy(update={"functions": {}})
    with pytest.raises(UnknownFunctionError, match="fake_run"):
        from_loader(stripped)
