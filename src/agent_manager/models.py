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
recorded as `started` with no exit code or duration, and resume has to load that
row back before discarding it and re-running the phase.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal[
    "pending", "started", "done", "failed", "escalated", "stopped", "cancelled"
]
"""Lifecycle of a run, story, subtask or phase. `started` is the non-terminal
state resume keys off (§9). `stopped` (addendum P4) is a clean stop on request
between phases: it is not `failed`, and relaunching the same command continues
it. `cancelled` (live control, C9) is a run closed for good by `am cancel`: its
checkpoints are never continued and it is never resumed."""

PhaseKind = Literal["agent", "deterministic"]
"""§5: a phase either dispatches a harness or runs a registered function."""

AttemptStatus = Literal[
    "started", "ok", "schema_invalid", "gate_failed", "harness_error"
]
"""`started` (no terminal event yet, §9 resume) plus §6's four journalled
outcomes."""

Launcher = Literal["direct", "bwrap", "container"]
"""§4: how a harness process is contained when it runs."""


class _Model(BaseModel):
    """Shared config for every state model.

    Unknown keys are an error, never a silent drop: a journal line from an older
    schema must surface as a validation failure rather than as data loss in the
    rebuilt projection.
    """

    model_config = ConfigDict(extra="forbid")


class Dispatch(_Model):
    """Everything needed to launch one harness process for one attempt."""

    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)
    role: str = Field(min_length=1)
    cwd: Path
    prompt_path: Path
    result_path: Path
    timeout: float = Field(default=1800.0, gt=0, allow_inf_nan=False)
    """Wall-clock seconds the harness gets before it is killed (§8 line 315).

    Defaulted rather than required: journal lines written before this field
    existed carry no `timeout` key, and under `extra="forbid"` a new *required*
    field would stop every stored line from loading. Thirty minutes is a
    deliberately generous ceiling -- it exists to stop a wedged process, not to
    bound a working one.
    """


class Attempt(_Model):
    """One dispatch of one phase, numbered from 1 within its phase."""

    n: int = Field(gt=0, strict=True)
    dispatch: Dispatch
    status: AttemptStatus = "started"
    exit_code: int | None = None
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
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
    detail: str | None = None
    """Why a `failed` phase failed, in the journal line that records it.

    The journal is the truth the projection is rebuilt from (§9), so a phase
    recorded `failed` with no cause leaves an audit trail nobody can act on
    once the process that held the in-memory summary is gone. Empty for every
    other status.
    """

    attempts: list[Attempt] = Field(default_factory=list)


class SubtaskRun(_Model):
    """One subtask card, driven on its own branch in its own worktree."""

    card_id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    base_branch: str = Field(min_length=1)
    status: Status = "pending"
    worktree_path: Path | None = None
    phases: list[PhaseRun] = Field(default_factory=list)


class StoryRun(_Model):
    """One story card. Its subtasks run sequentially, stacked on each other."""

    card_id: str = Field(min_length=1)
    title: str
    level: int = Field(ge=0)
    status: Status = "pending"
    tip_branch: str | None = None
    subtasks: list[SubtaskRun] = Field(default_factory=list)


class HarnessAssignment(_Model):
    """One entry of `RunConfig.harness_map`: which harness and model a role gets."""

    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)


class RunConfig(_Model):
    """The knobs a run was started with, recorded so resume reuses them."""

    max_concurrent_stories: int = Field(default=4, gt=0)
    dry_run: bool = False
    launcher: Launcher = "direct"
    harness_map: dict[str, HarnessAssignment] = Field(default_factory=dict)
    story_id: str | None = None
    """The story card a run is restricted to; `None` when the run is not a story run."""


class Run(_Model):
    """The root of the state tree: one invocation of one workflow."""

    id: str = Field(min_length=1)
    workflow: str = Field(min_length=1)
    repo_dir: Path
    base_branch: str = Field(min_length=1)
    branch_prefix: str = Field(min_length=1)
    status: Status = "pending"
    started_at: datetime | None = None
    config: RunConfig = Field(default_factory=RunConfig)
    milestone_id: str | None = None
    """The full id of the milestone card a `milestone` run drives.

    Resume finds its milestone by this id. It is None for a task run, and for
    a milestone run recorded before the field existed: those fall back to the
    short id at the end of the run id (`orchestrate.find_run_milestone`).
    Defaulted because journal lines written before it carry no such key.
    """

    stories: list[StoryRun] = Field(default_factory=list)


class Card(BaseModel):
    """One `brd` card as `brd show` reports it (design §4: board.py's boundary).

    Deliberately not a `_Model`: `extra="forbid"` is right for journal lines we
    wrote ourselves, and wrong for another program's output. A `brd` schema
    addition must not break a running milestone, so unknown keys are ignored.
    `status` is a plain string because the board's vocabulary
    (`todo`/`in_progress`/`done`/`blocked`) is brd's to define and is not the
    run lifecycle `Status` above.

    `blocked_by` (ids this card waits on) and `created_at` (brd's ISO string,
    kept as printed) are carried for the census.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    title: str
    status: str = Field(min_length=1)
    parent_id: str | None = None
    description: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    created_at: str | None = None


class CardNode(BaseModel):
    """One node of `brd tree`'s JSON: a card plus its nested descendants.

    Tree nodes carry no `parent_id` of their own -- the nesting under
    `children` is what preserves milestone -> story -> subtask depth.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    title: str
    status: str = Field(min_length=1)
    description: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    created_at: str | None = None
    children: list["CardNode"] = Field(default_factory=list)
