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

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["pending", "started", "done", "failed", "escalated"]
"""Lifecycle of a run, story, subtask or phase. `started` is the non-terminal
state resume keys off (§9)."""

PhaseKind = Literal["agent", "deterministic"]
"""§5: a phase either dispatches a harness or runs a registered function."""

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


class PhaseRun(_Model):
    """One phase of one subtask, with every attempt made at it."""

    name: str = Field(min_length=1)
    kind: PhaseKind
    status: Status = "pending"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    attempts: list[Attempt] = Field(default_factory=list)


class SubtaskRun(_Model):
    """One subtask card, driven on its own branch in its own worktree."""

    card_id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    base_branch: str = Field(min_length=1)
    status: Status = "pending"
    worktree_path: Path | None = None
    phases: list[PhaseRun] = Field(default_factory=list)
