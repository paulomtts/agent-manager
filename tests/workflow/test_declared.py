"""Pure-data tier (spec §9): TASK and INTEGRATE are constant phase-model data,
so these are plain unit tests -- no fakes, no git, no harness. The only I/O is
load_builtin's read of the shipped YAML and validate()'s role loading."""

import ast
from datetime import timedelta
from pathlib import Path

from agent_manager import dispatch
from agent_manager.workflow import integrate as integrate_module
from agent_manager.workflow import task as task_module
from agent_manager.workflow.loader import load_builtin
from agent_manager.workflow.phases import AgentPhase, Step, from_loader
from agent_manager.workflow.registry import default_registry
from agent_manager.workflow.task import TASK, LAUNCHER_TIMEOUT
from agent_manager.workflow.integrate import INTEGRATE


def _shipped(name, like):
    converted = from_loader(load_builtin(name, default_registry()))
    # timeouts are new data the YAML never had: compare with TASK's own
    return type(converted)(converted.name, tuple(
        p if not hasattr(p, "timeout") else type(p)(**{**p.__dict__, "timeout": like.phase(p.name).timeout})
        for p in converted.phases))


def _assert_same_callables(declared, name):
    """Identity, not just qualname: every callable is the object the registry binds."""
    converted = from_loader(load_builtin(name, default_registry()))
    assert declared.phase_names == converted.phase_names
    for mine, theirs in zip(declared.phases, converted.phases):
        assert type(mine) is type(theirs), mine.name
        assert len(mine.gates) == len(theirs.gates), mine.name
        for ours, registered in zip(mine.gates, theirs.gates):
            assert ours is registered, mine.name
        if isinstance(mine, Step):
            assert mine.run is theirs.run, mine.name
            assert mine.when is theirs.when, mine.name
        else:
            assert mine.result is theirs.result, mine.name


def _imported_modules(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_task_equals_the_shipped_yaml():
    assert TASK.digest() == _shipped("task", TASK).digest()


def test_integrate_equals_the_shipped_yaml():
    assert INTEGRATE.digest() == _shipped("integrate", INTEGRATE).digest()


def test_both_validate():
    TASK.validate(launcher_timeout=LAUNCHER_TIMEOUT)
    INTEGRATE.validate(launcher_timeout=LAUNCHER_TIMEOUT)


def test_task_callables_are_the_registry_bindings():
    _assert_same_callables(TASK, "task")


def test_integrate_callables_are_the_registry_bindings():
    _assert_same_callables(INTEGRATE, "integrate")


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
