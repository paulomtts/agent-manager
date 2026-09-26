"""`AgentPhaseFailed`: how an agent phase's terminal failure leaves the runner.

`EngineError` lives in `agent_manager.runtime.errors`, importable without
pygents.
"""


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
    """

    def __init__(self, phase: str, *, outcome: str, detail: str) -> None:
        self.phase = phase
        self.outcome = outcome
        self.detail = detail
        super().__init__(f"phase {phase!r} ended {outcome}: {detail}")
