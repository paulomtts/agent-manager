"""`AgentPhaseFailed`: how an agent phase's terminal failure leaves the runner.

`EngineError` lives in `agent_manager.runtime.errors`, importable without
pygents.
"""

from typing import Any


class AgentPhaseFailed(RuntimeError):
    """An agent phase ended on a terminal failure, with its attempts recorded.

    Raised rather than returned because `runtime.walk.AgentPhaseRunner` is typed
    `(phase, context, rendered) -> result`: there is no second channel in that
    signature, and returning a sentinel result would be indistinguishable from a
    phase whose harness genuinely produced one. The pygents walk's `agent_phase`
    tool catches it: a critic with `on_fail` loops back, anything else escalates
    -- the same edge a step reaches through `_Outcome(ok=False, ...)`.

    `outcome` is one of §6 line 277's four journalled names, so an operator
    reading the message knows whether to fix a prompt, a gate or a harness.

    `result` is the agent's own decoded payload when a dispatch succeeded but a
    gate then failed it (e.g. review's `unresolved_blockers`) -- `None` when
    nothing was ever produced (a raised or broken gate, a harness failure). A
    failed phase's `ContextItem` is never yielded (only a successful phase's
    is), so this is the only channel that gets the agent's own explanation of
    *why* into `SubtaskSummary.results[failed_phase]` for an escalation, which
    is what `comments.agent_reason` reads to compose the board comment.
    """

    def __init__(
        self, phase: str, *, outcome: str, detail: str, result: Any = None
    ) -> None:
        self.phase = phase
        self.outcome = outcome
        self.detail = detail
        self.result = result
        super().__init__(f"phase {phase!r} ended {outcome}: {detail}")
