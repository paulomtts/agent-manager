"""Errors raised by the engine and the helpers it calls.

`EngineError` lives here rather than in `engine.py` so `prompt.py` can raise it
without importing the engine that imports `prompt.py`. `engine.EngineError`
stays a valid name: `engine.py` imports it back and every existing caller,
including the tests, keeps working against the same class object.
"""


class EngineError(RuntimeError):
    """The engine refused to run, or could not make sense of, a phase.

    Carries the coordinates an operator needs to find the offending line of the
    workflow document: which phase, which registered function, which parameter.
    Prompt rendering reuses `parameter` for the name of the declared input it
    could not resolve -- the input name is what an operator greps the document
    for, exactly as a parameter name is.
    """

    def __init__(
        self,
        reason: str,
        *,
        phase: str | None = None,
        function: str | None = None,
        parameter: str | None = None,
    ) -> None:
        self.reason = reason
        self.phase = phase
        self.function = function
        self.parameter = parameter
        parts = []
        if phase is not None:
            parts.append(f"phase {phase!r}")
        if function is not None:
            parts.append(f"function {function!r}")
        if parameter is not None:
            parts.append(f"parameter {parameter!r}")
        prefix = ", ".join(parts)
        super().__init__(f"{prefix}: {reason}" if prefix else reason)


class AgentPhaseFailed(RuntimeError):
    """An agent phase ended on a terminal failure, with its attempts recorded.

    Raised rather than returned because `engine.AgentPhaseRunner` is typed
    `(phase, context, rendered) -> result`: there is no second channel in that
    signature, and returning a sentinel result would be indistinguishable from a
    phase whose harness genuinely produced one. `run_subtask` catches it and
    turns it into `summary.status = "escalated"` -- the same edge the
    deterministic branch reaches through `_Outcome(ok=False, ...)`.

    `outcome` is one of §6 line 277's four journalled names, so an operator
    reading the message knows whether to fix a prompt, a gate or a harness.
    """

    def __init__(self, phase: str, *, outcome: str, detail: str) -> None:
        self.phase = phase
        self.outcome = outcome
        self.detail = detail
        super().__init__(f"phase {phase!r} ended {outcome}: {detail}")
