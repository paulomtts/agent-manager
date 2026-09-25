"""Pure-data tier (spec §9): phases.py has no I/O beyond validate()'s role
loading, so these are plain unit tests -- no fakes, no git, no harness."""

import ast
import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest

from agent_manager.workflow import phases as phases_module
from agent_manager.workflow.phases import (
    AgentPhase,
    Goto,
    Retry,
    Step,
    Workflow,
    WorkflowError,
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


def test_input_names_are_exactly_the_resolver_table():
    from agent_manager import prompt

    assert isinstance(prompt.INPUT_NAMES, frozenset)
    assert prompt.INPUT_NAMES == frozenset(prompt._TABLE)
    assert "card" in prompt.INPUT_NAMES


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
