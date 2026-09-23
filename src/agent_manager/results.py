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

from pydantic import BaseModel, ConfigDict, Field

from agent_manager.errors import EngineError

RESULT_MODELS: dict[str, type[BaseModel]] = {}
"""Every `result:` name with a model behind it. Empty on this branch."""


class _Result(BaseModel):
    """Shared config for every phase result model (design §2).

    ``extra="forbid"`` for the reason ``models._Model`` gives: an unknown key in
    an agent-written file is a signal, not something to drop. ``strict=True``
    because a result file is written by a language model -- ``"3"`` for
    ``commit_count`` or ``1`` for ``refused`` is exactly the sloppiness this
    validation exists to catch. Model-wide rather than per-field, because every
    field here is equally untrusted.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


class Verification(_Result):
    """What Explore reports about how this repo is verified (addendum R1)."""

    full_suite: list[str] = Field(serialization_alias="fullSuite")
    typecheck: str
    lint: list[str]


class ExploreResult(_Result):
    """The `explore` phase's result file (`builtin/task.yaml` line 9)."""

    refused: bool
    reason: str | None
    summary: str
    verification: Verification


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


class CriticResult(_Result):
    """The `critic` phase's result file (`builtin/task.yaml` lines 39 and 53)."""

    blockers: bool
    reason: str | None
    summary: str


class PlanResult(_Result):
    """The `plan` phase's result file (`builtin/task.yaml` line 46).

    No `skill_invoked`: D6 inlines the planning methodology into the prompt, so
    there is no skill invocation left to report.
    """

    path: str
    self_reviewed: bool
    note: str | None


class ImplementResult(_Result):
    """The `implement` phase's result file (`builtin/task.yaml` line 60).

    `plan_hash` carries no format constraint: `reducers.is_plan_hash` owns the
    "8 lowercase hex characters" judgement, and a malformed hash has to reach
    that gate as data rather than dying here.
    """

    blocked: bool
    blocked_reason: str | None
    resumed: bool
    plan_hash: str
    report: str
