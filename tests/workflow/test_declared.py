"""Pure-data tier (spec §9): TASK and INTEGRATE are constant phase-model data,
so these are plain unit tests -- no fakes, no git, no harness. The only I/O is
validate()'s role loading."""

import ast
from datetime import timedelta
from pathlib import Path

from agent_manager import dispatch
from agent_manager.workflow import integrate as integrate_module
from agent_manager.workflow import task as task_module
from agent_manager.workflow import phases
from agent_manager.workflow.phases import AgentPhase, Goto
from agent_manager.workflow.task import TASK, LAUNCHER_TIMEOUT
from agent_manager.workflow.integrate import INTEGRATE


def _imported_modules(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_both_validate():
    TASK.validate(launcher_timeout=LAUNCHER_TIMEOUT)
    INTEGRATE.validate(launcher_timeout=LAUNCHER_TIMEOUT)


def test_launcher_timeout_is_the_dispatch_default():
    assert LAUNCHER_TIMEOUT == timedelta(seconds=dispatch.DEFAULT_TIMEOUT)
    assert LAUNCHER_TIMEOUT == timedelta(minutes=30)


def test_task_timeouts_are_the_chosen_values_floored_above_the_launcher():
    timeouts = {p.name: p.timeout for p in TASK.phases if isinstance(p, AgentPhase)}
    assert timeouts == {
        "explore": timedelta(minutes=35),
        "spec": timedelta(minutes=35),
        "validate_spec": timedelta(minutes=35),
        "plan": timedelta(minutes=35),
        "validate_plan": timedelta(minutes=35),
        "implement": timedelta(minutes=90),
        "review": timedelta(minutes=45),
    }
    assert task_module.AGENT_TIMEOUT_FLOOR == LAUNCHER_TIMEOUT + timedelta(minutes=5)
    assert task_module.agent_timeout(20) == task_module.AGENT_TIMEOUT_FLOOR
    assert task_module.agent_timeout(90) == timedelta(minutes=90)


def test_the_floor_is_the_launcher_timeout_plus_the_one_margin():
    # T9: the 5 min margin has one source, `phases.LAUNCHER_MARGIN`, which the
    # compiler's turn-timeout derivation (D3) reads too.
    assert phases.LAUNCHER_MARGIN == timedelta(minutes=5)
    assert task_module.AGENT_TIMEOUT_FLOOR == LAUNCHER_TIMEOUT + phases.LAUNCHER_MARGIN
    assert task_module.AGENT_TIMEOUT_FLOOR == timedelta(seconds=2100)
    assert "minutes=5" not in Path(task_module.__file__).read_text(encoding="utf-8")


def test_integrate_timeout_is_floored_above_the_launcher():
    timeouts = {p.name: p.timeout for p in INTEGRATE.phases if isinstance(p, AgentPhase)}
    assert timeouts == {"resolve": timedelta(minutes=35)}


def test_integrate_reuses_the_task_timeout_floor():
    source = Path(integrate_module.__file__).read_text(encoding="utf-8")
    assert "agent_manager.workflow.task" in _imported_modules(integrate_module)
    assert "DEFAULT_TIMEOUT" not in source
    assert "LAUNCHER_TIMEOUT =" not in source
    assert "AGENT_TIMEOUT_FLOOR =" not in source


def test_declared_modules_never_import_pygents():
    for module in (task_module, integrate_module):
        imported = _imported_modules(module)
        assert not any(n == "pygents" or n.startswith("pygents.") for n in imported), module.__name__


def test_each_validation_phase_names_its_own_critic():
    assert TASK.phase("validate_spec").role == "spec_critic"
    assert TASK.phase("validate_plan").role == "plan_critic"


def test_task_critics_loop_back_once():
    """G4: each critic loops back to the phase it judged, at most once; review
    does not loop; only the looped-to phases read `feedback`."""
    assert TASK.phase("validate_spec").on_fail == Goto("spec", 1)
    assert TASK.phase("validate_plan").on_fail == Goto("plan", 1)
    assert TASK.phase("review").on_fail is None
    agents = [p for p in TASK.phases if isinstance(p, AgentPhase)]
    assert {p.name for p in agents if p.on_fail is not None} == {
        "validate_spec",
        "validate_plan",
    }
    assert {p.name for p in agents if "feedback" in p.inputs} == {"spec", "plan"}
    assert TASK.phase("spec").inputs == ("card", "explore", "spec_path", "feedback")
    assert TASK.phase("plan").inputs == ("spec_path", "plan_path", "feedback")


def test_integrate_declares_no_loop_and_no_feedback():
    """This card turns on TASK's critics only."""
    agents = [p for p in INTEGRATE.phases if isinstance(p, AgentPhase)]
    assert all(p.on_fail is None for p in agents)
    assert all("feedback" not in p.inputs for p in agents)
