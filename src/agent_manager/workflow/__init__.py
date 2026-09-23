"""The workflow document: its typed form, and the names it may reference.

`load_builtin("task")` is the engine's entry point (design §5). Nothing here
executes a phase -- the engine, the prompt renderer and the harness dispatch
are sibling modules that consume what this package returns.
"""

from agent_manager.workflow.loader import (
    AgentPhase,
    DeterministicPhase,
    RetryPolicy,
    Workflow,
    builtin_path,
    load_builtin,
    load_workflow,
)
from agent_manager.workflow.registry import (
    DuplicateFunctionError,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
    default_registry,
)

__all__ = [
    "AgentPhase",
    "DeterministicPhase",
    "DuplicateFunctionError",
    "FunctionRegistry",
    "RetryPolicy",
    "UnknownFunctionError",
    "Workflow",
    "WorkflowLoadError",
    "builtin_path",
    "default_registry",
    "load_builtin",
    "load_workflow",
]
