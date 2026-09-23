"""`result:` names -> the pydantic model that validates one result file (§6 step 5).

`workflow/loader.py` keeps `AgentPhase.result` a string on purpose ("mapping it
to a class is the agent-dispatch sibling's job"). This module is that mapping,
and nothing more: no validation happens here, and importing it reads no file.

The table ships empty, and that is deliberate. `builtin/task.yaml` names five
result models -- `ExploreResult`, `CriticResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` -- and the design spec gives a field schema
for none of them, so inventing one here would be a design decision this card
was not given. An unresolved name fails loudly at dispatch time instead, which
is strictly better than validating nothing and calling the result `ok`.
"""

from collections.abc import Mapping

from pydantic import BaseModel

from agent_manager.errors import EngineError

RESULT_MODELS: dict[str, type[BaseModel]] = {}
"""Every `result:` name with a model behind it. Empty on this branch."""


def resolve_result_model(
    name: str, table: Mapping[str, type[BaseModel]], *, phase: str
) -> type[BaseModel]:
    """The model class `name` refers to, or an `EngineError` naming the phase."""
    model = table.get(name)
    if model is None:
        raise EngineError(
            f"declares result {name!r}, which no result model is registered for "
            f"(registered: {', '.join(sorted(table)) or 'nothing'}); a result file "
            "cannot be validated against a model that does not exist",
            phase=phase,
        )
    return model
