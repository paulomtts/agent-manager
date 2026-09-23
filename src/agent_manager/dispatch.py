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

from collections.abc import Mapping
from dataclasses import dataclass, replace

from agent_manager import models, paths, prompt
from agent_manager.errors import EngineError
from agent_manager.harness.base import HarnessAdapter
from agent_manager.harness.registry import DEFAULT_HARNESS
from agent_manager.roles.loader import RoleBundle

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
