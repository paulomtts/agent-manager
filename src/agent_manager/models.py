"""Pydantic models describing the state of one agent-manager run.

Spec §9 draws the tree Run -> StoryRun -> SubtaskRun -> PhaseRun -> Attempt ->
Dispatch. This module is that tree and nothing else: pure data and validation,
no filesystem, no SQLite, no journal writing, no path derivation (paths.py owns
that). Importing it must not read the environment or touch disk.

They are pydantic rather than dataclasses because the SQLite projection is
rebuilt from the append-only journal (D5) and a stale or mistyped line must fail
loudly at that boundary instead of quietly producing a half-populated tree. The
validation message is the signal, so the constraints here are chosen to make the
message legible.

Everything terminal is optional: an attempt in flight when the manager died is
recorded as `started` with no exit code, duration, tokens or cost, and resume has
to load that row back before discarding it and re-running the phase.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

AttemptStatus = Literal[
    "started", "ok", "schema_invalid", "gate_failed", "harness_error"
]
"""`started` (no terminal event yet, §9 resume) plus §6's four journalled
outcomes."""


class _Model(BaseModel):
    """Shared base for every state model; Task 4 gives it its `model_config`."""


class Dispatch(_Model):
    """Everything needed to launch one harness process for one attempt."""

    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)
    role: str = Field(min_length=1)
    cwd: Path
    prompt_path: Path
    result_path: Path


class Attempt(_Model):
    """One dispatch of one phase, numbered from 1 within its phase."""

    n: int = Field(gt=0, strict=True)
    dispatch: Dispatch
    status: AttemptStatus = "started"
    exit_code: int | None = None
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    prompt_path: Path | None = None
    result_path: Path | None = None
    stdout_path: Path | None = None
