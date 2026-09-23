"""Run one agent phase to a terminal outcome (design §6 lines 261-278).

`engine.run_subtask` resolves an agent phase's `inputs` and renders its prompt,
then hands `(phase, context, rendered)` to an injected `AgentPhaseRunner`
(`engine.py` lines 211-222). This module is that runner: the attempt directory,
the dispatch, the result file, the gates and the retry loop.

Three rules shape everything here, and none of them is negotiable:

- D7 / §8 line 317: nothing in this module starts a process. The argv comes
  from the adapter's `build_command` and is run through the injected
  `LauncherFn`, which is what keeps `bwrap` a later swap and every test above
  the launcher process-free. `subprocess` is deliberately not imported.
- D4 / §6 step 5: the contract is `result.json`. `stdout.log` is captured as a
  log and is only ever handed to `parse_usage`, never parsed for a result.
- §6 step 3: the attempt directory comes from `paths.attempt_dir`, which is
  rooted under `paths.data_dir()` and therefore outside every worktree -- a
  result file written inside the worktree would fail the verify step's
  clean-tree check or be swept into a commit.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_manager import engine, models, paths, prompt
from agent_manager.errors import EngineError
from agent_manager.harness.base import HarnessAdapter, Outcome
from agent_manager.harness.registry import DEFAULT_HARNESS
from agent_manager.roles.loader import RoleBundle
from agent_manager.workflow.loader import AgentPhase, Workflow

RESULT_NAME = "result.json"
"""The result file §6 step 3 puts in every attempt directory."""

STDOUT_NAME = "stdout.log"
"""The captured harness log of one attempt (§6 step 4). A log, not a channel."""

FEEDBACK_HEADING = "## feedback on the previous attempt"
"""Heading of the block §6 step 7 appends before a re-dispatch.

A `##` section, matching `prompt._assemble`'s section format, so the retry
prompt reads as one more input section rather than as a stray paragraph.
"""


def next_attempt(run_id: str, card: str, phase: str) -> int:
    """The lowest attempt number this phase has no directory for yet.

    Scanned from disk rather than counted in memory: a resumed run finds the
    attempts a previous process made, and overwriting one would destroy the
    prompt, result and log that are the only evidence of what happened.
    """
    card_dir = paths.run_dir(run_id) / card
    attempt = 1
    while (card_dir / f"{phase}.{attempt}").exists():
        attempt += 1
    return attempt


def with_feedback(
    rendered: prompt.RenderedPrompt, feedback: str
) -> prompt.RenderedPrompt:
    """The same prompt with one feedback block appended (§6 step 7).

    Appended to whatever it is handed, so a third attempt carries both earlier
    complaints: the spec's "the prior prompt plus the feedback block". Dispatch
    is stateless (D1), so the whole input of every attempt has to be the file on
    disk -- nothing is carried in the harness's head between attempts.
    """
    return replace(
        rendered,
        text=f"{rendered.text}\n{FEEDBACK_HEADING}\n{feedback}\n",
        sections=rendered.sections + (("feedback", feedback),),
    )


@dataclass(frozen=True)
class Target:
    """Where one phase's dispatch is going: which adapter, which model."""

    adapter: HarnessAdapter
    model: str


def resolve_target(
    role: RoleBundle,
    harness_map: Mapping[str, models.HarnessAssignment],
    adapters: Mapping[str, HarnessAdapter],
    *,
    phase: str,
) -> Target:
    """§6 step 1: the harness and model assigned to this phase's role.

    `RunConfig.harness_map` is the run's explicit answer and wins outright. With
    no entry, the role falls back to `DEFAULT_HARNESS` and to the model its own
    `policy.toml` records for that harness -- D6 puts the default model in the
    bundle precisely so a run that configures nothing still dispatches.

    Both failures are `EngineError` naming the phase rather than a retryable
    outcome: no re-dispatch fixes a missing adapter or a missing default model.
    """
    assignment = harness_map.get(role.name)
    harness = DEFAULT_HARNESS if assignment is None else assignment.harness
    adapter = adapters.get(harness)
    if adapter is None:
        raise EngineError(
            f"role {role.name!r} is routed to harness {harness!r}, which has no "
            f"adapter (adapters: {', '.join(sorted(adapters)) or 'none'})",
            phase=phase,
        )
    if assignment is not None:
        return Target(adapter=adapter, model=assignment.model)
    model = role.policy.default_model.get(harness)
    if model is None:
        raise EngineError(
            f"role {role.name!r} has no default model for harness {harness!r} in its "
            f"policy.toml (it has: "
            f"{', '.join(sorted(role.policy.default_model)) or 'nothing'}) and the "
            "run's harness_map assigns none",
            phase=phase,
        )
    return Target(adapter=adapter, model=model)


DEFAULT_TIMEOUT = 1800.0
"""Wall-clock seconds one attempt gets, matching `models.Dispatch.timeout`'s own
default (§8 line 315): a ceiling that stops a wedged process, not a budget."""


@dataclass(frozen=True)
class Verdict:
    """How one attempt ended, before the retry policy is consulted.

    Internal-only state, so a dataclass rather than a pydantic model (CLAUDE.md).

    `status` is one of §6 line 277's four outcomes and is the value journalled.
    `fatal` marks a failure no `retry.on` list can make retryable -- a gate that
    raised instead of returning a verdict, per the spec's error paths -- and is
    kept separate from `status` because the journalled outcome name is part of
    the contract and must stay one of the four.
    """

    status: models.AttemptStatus
    result: Any = None
    detail: str | None = None
    fatal: bool = False


def build_dispatch(
    *,
    target: Target,
    role: RoleBundle,
    cwd: Path,
    prompt_path: Path,
    attempt_dir: Path,
    timeout: float,
) -> models.Dispatch:
    """Everything one harness process needs, for one attempt (§8 lines 315-318).

    The result path is inside the attempt directory and therefore outside the
    worktree (§6 step 3); `cwd` is the subtask worktree, which is where D7 pins
    the harness.
    """
    return models.Dispatch(
        harness=target.adapter.name,
        model=target.model,
        role=role.name,
        cwd=cwd,
        prompt_path=prompt_path,
        result_path=attempt_dir / RESULT_NAME,
        timeout=timeout,
    )


def classify(
    outcome: Outcome, result_path: Path, model: type[BaseModel] | None
) -> Verdict:
    """One attempt's outcome, from the launcher's report and the result file.

    The order is §6 line 278's, and it is load-bearing: a timeout or a non-zero
    exit is a `harness_error` whatever is on disk, and a missing file after a
    clean exit is a `harness_error` too -- nothing is parsed in either case.
    Only past those does the file get read, and from there every failure is
    `schema_invalid`, because a file that exists and cannot be validated is
    exactly what re-dispatching with the validator's text can fix.

    A verdict of `ok` here means "the result file is good"; the gates run after
    and may still turn it into `gate_failed`.

    A validated result is returned as its JSON-mode dump rather than as the
    model instance: the ported gates read it with `Mapping.get`
    (`steps/reducers.py`), and §7 inlines it into a later phase's prompt as
    JSON. Handing them a `BaseModel` would make every gate silently read `None`.
    """
    if outcome.timed_out:
        return Verdict(
            "harness_error",
            detail=f"the harness timed out and was killed after {outcome.duration:.1f}s",
        )
    if outcome.exit_code != 0:
        return Verdict("harness_error", detail=f"the harness exited {outcome.exit_code}")
    if not result_path.is_file():
        return Verdict(
            "harness_error", detail=f"the harness wrote no result file at {result_path}"
        )
    try:
        text = result_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return Verdict(
            "schema_invalid", detail=f"{result_path} is not valid UTF-8 text: {error}"
        )
    except OSError as error:
        return Verdict(
            "schema_invalid",
            detail=f"{result_path} cannot be read: {type(error).__name__}: {error}",
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return Verdict("schema_invalid", detail=f"{result_path} is not valid JSON: {error}")
    if model is None:
        if not isinstance(data, dict):
            return Verdict(
                "schema_invalid",
                detail=(
                    f"{result_path} must hold a JSON object, got "
                    f"{type(data).__name__}: later phases and every gate read the "
                    "result by key"
                ),
            )
        return Verdict("ok", result=data)
    try:
        validated = model.model_validate(data)
    except ValidationError as error:
        return Verdict("schema_invalid", detail=str(error))
    return Verdict("ok", result=validated.model_dump(mode="json"))


def gate_values(
    context: Mapping[str, Any], phase_name: str, result: Any
) -> dict[str, Any]:
    """The binding table this phase's gates see.

    The same table `engine._gate_values` builds for a deterministic phase, and
    for the same two reasons: the result appears under `result` (the parameter
    name the ported gates in `steps/reducers.py` declare) and under the phase's
    own name (how §6 says later phases read it), except where that name is one
    of the keys the engine owns.
    """
    values = {**context, "result": result}
    if phase_name not in engine.RESERVED_CONTEXT_KEYS:
        values[phase_name] = result
    return values


def _render_verdict(verdict: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(verdict.items()))


def evaluate_gates(
    phase: AgentPhase,
    workflow: Workflow,
    values: Mapping[str, Any],
    warnings: list[str],
) -> Verdict | None:
    """`None` when every gate passes, else the `gate_failed` verdict (§6 step 6).

    A gate returns `None` to pass, a mapping with `warn` to warn, or any other
    mapping to fail -- the contract `_evaluate_gates` already applies to
    deterministic phases. No per-gate retryable flag exists and this subtask
    does not add one: whether a `gate_failed` is retried is `retry.on`'s answer
    alone.

    The two ways a gate can be *wrong* rather than unhappy -- raising, or
    returning something that is not a mapping -- come back `fatal`, so no
    `retry.on` list can re-dispatch into a situation the harness cannot change.
    A binding failure is different again and propagates as `EngineError`: it
    means the document names a gate whose parameters nothing supplies, which is
    a bug in the document, not in the attempt.
    """
    for name in phase.gates:
        gate = workflow.function(name)
        kwargs = engine.bind_arguments(gate, values, phase=phase.name, function=name)
        try:
            verdict = gate(**kwargs)
        except Exception as error:
            return Verdict(
                "gate_failed",
                detail=(
                    f"phase {phase.name!r} gate {name!r} raised "
                    f"{type(error).__name__}: {error}; a gate returns None to pass or "
                    "a mapping verdict to fail, so this is a broken gate rather than a "
                    "failed attempt"
                ),
                fatal=True,
            )
        if verdict is None:
            continue
        if not isinstance(verdict, Mapping):
            return Verdict(
                "gate_failed",
                detail=(
                    f"phase {phase.name!r} gate {name!r} returned "
                    f"{type(verdict).__name__}; a gate returns None to pass or a "
                    "mapping verdict to fail, and anything else would be read as a "
                    "pass by accident"
                ),
                fatal=True,
            )
        if "warn" in verdict:
            warnings.append(
                f"phase {phase.name!r} gate {name!r} warned: {verdict['warn']}"
            )
            continue
        return Verdict(
            "gate_failed",
            detail=(
                f"phase {phase.name!r} gate {name!r} failed: {_render_verdict(verdict)}"
            ),
        )
    return None
