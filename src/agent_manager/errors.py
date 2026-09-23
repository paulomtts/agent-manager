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
