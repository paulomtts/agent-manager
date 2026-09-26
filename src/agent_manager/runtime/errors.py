"""`EngineError`, importable without pygents (rule 1).

The exception `prompt.py`, `dispatch.py`, `results.py` and the pygents walk
raise for a wiring or workflow bug. It lives in a module that imports nothing,
so the modules below the runtime can raise it without an import cycle and
without loading pygents; `runtime/__init__.py` stays import-free for the same
reason.
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
