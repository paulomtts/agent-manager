"""Result model names -> the pydantic model that validates one result file (§6 step 5).

A declared `phases.AgentPhase` carries its result model as the class itself.
This module is the table of those classes by name, and nothing more: no
validation happens here, and importing it reads no file.

`RESULT_MODELS` maps every result model a shipped workflow declares --
`ExploreResult`, `CriticResult`, `SpecResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` from `workflow.task.TASK`, and
`ResolveResult` from `workflow.integrate.INTEGRATE` -- to itself by name.
`Verification` is not in it; no phase declares it, and it is reachable only as
`ExploreResult.verification`. A name with no entry still fails loudly at
dispatch time, which is strictly better than validating nothing and calling
the result `ok`.
"""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from agent_manager.runtime.errors import EngineError


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
    typecheck: str = Field(
        description=(
            "The repo's typecheck command, exactly as you would type it, or an empty "
            "string when it has none. Never prose such as 'none' or 'n/a'."
        )
    )
    lint: list[str] = Field(
        description=(
            "The repo's lint commands, each exactly as you would type it, or an empty "
            "list when it has none."
        )
    )


class ExploreResult(_Result):
    """The `explore` phase's result file."""

    refused: bool
    reason: str | None
    summary: str
    verification: Verification


class CriticResult(_Result):
    """The `validate_spec` and `validate_plan` phases' result file."""

    blockers: bool
    reason: str | None
    summary: str


class SpecResult(_Result):
    """The `spec` phase's result file.

    `PlanResult`'s shape minus `self_reviewed`: nothing asks the spec author to
    self-review, so a result claiming it is an unknown key. `path` is what the
    agent says it wrote; `walk._document_paths` still derives `spec_path` from
    the phase's `writes:` template, so this field is the agent's claim on record
    rather than the engine's input. No `serialization_alias` on either field: no
    reducer in `steps/reducers.py` reads a spec result, so there is no camelCase
    port to honour.
    """

    path: str
    note: str | None


class PlanResult(_Result):
    """The `plan` phase's result file.

    No `skill_invoked`: D6 inlines the planning methodology into the prompt, so
    there is no skill invocation left to report.
    """

    path: str
    self_reviewed: bool
    note: str | None


class ImplementResult(_Result):
    """The `implement` phase's result file.

    `plan_hash` carries no format constraint: `reducers.is_plan_hash` owns the
    "8 lowercase hex characters" judgement, and a malformed hash has to reach
    that gate as data rather than dying here.
    """

    blocked: bool
    blocked_reason: str | None
    resumed: bool
    plan_hash: str
    report: str


class ReviewResult(_Result):
    """The `review` phase's result file.

    `commit_count` and `tagged_count` carry camelCase serialisation aliases
    because `reducers.review_gate` reads `commitCount`/`taggedCount` off the
    dumped mapping (lines 187-188) -- that port is a behavioural specification
    and does not move. Serialisation only: validation stays snake_case, so the
    JSON Schema embedded in the agent's prompt names exactly one spelling.
    """

    findings: list[str]
    unresolved_blockers: list[str]
    fix_summary: str
    porcelain: str
    commit_count: int = Field(serialization_alias="commitCount")
    tagged_count: int = Field(serialization_alias="taggedCount")
    plan_hash: str


class ResolveResult(_Result):
    """The `resolve` phase's result file (addendum I3).

    `resolved` is advisory. Whether the merge really completed is something git
    can measure, so `merge_completed_gate` judges it from the repository, not from
    this flag. No `serialization_alias` on either field, because no reducer reads a
    resolve result under a camelCase name.
    """

    resolved: bool
    summary: str


RESULT_MODELS: dict[str, type[BaseModel]] = {
    "ExploreResult": ExploreResult,
    "CriticResult": CriticResult,
    "SpecResult": SpecResult,
    "PlanResult": PlanResult,
    "ImplementResult": ImplementResult,
    "ReviewResult": ReviewResult,
    "ResolveResult": ResolveResult,
}
"""Every result model a shipped workflow declares, keyed by class name.

`TASK` declares the first six; `INTEGRATE` declares `ResolveResult` for its
`resolve` phase. `Verification` is absent on purpose: no phase declares it, and
it is reachable only as `ExploreResult.verification`.
"""


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
