"""The Typer app (design §4 line 128, §10 lines 388-411).

This module composes and renders; it decides nothing a collaborator already
decides. Branch names come from `dag`, board reads from `board`, artifact paths
from `paths` via `store`, the phase walk from `engine`, and the dispatch from
`dispatch.AgentRunner`. §4 calls this file "typer app" and that is the whole
constraint: no step logic, no gate logic, no branch strings built by hand, and
no run state written anywhere but through `Store`.

Output is brd's envelope, because a human and a script read the same two tools
and `{"ok": ..., "data": ...}` is already what one of them prints (CLAUDE.md,
§10 line 404). JSON is one line by default and indented under `--pretty`.

Exit codes carry what the envelope cannot: `0` for a subtask that finished, `1`
for one that escalated -- an escalation is a truthful result, so the envelope
stays `ok: true` -- and `3` for "this tool could not run that", leaving `2` to
Typer's own usage errors.
"""

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from agent_manager import dag

EXIT_ESCALATED = 1
"""The subtask escalated. §12: a full stop a human has to read."""

EXIT_ERROR = 3
"""The tool could not run the subtask at all. `2` belongs to Typer's usage errors."""

RUN_ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
"""Sortable, path-safe, second-resolution UTC. Run ids are directory names."""

WORKTREE_PARTS = (".claude", "worktrees")
"""Where a subtask's worktree lives under the repo, matching the layout the rest
of this project already uses."""


class CliError(RuntimeError):
    """The command refused to start a run. One base type for the envelope."""


class RepoDirError(CliError):
    """`--repo-dir` does not name a directory this tool can work in."""


class ParentlessCardError(CliError):
    """The card has no parent story.

    `run --card` drives a subtask *of a story*: `Store.record_subtask` and
    `Store.record_phase` are both keyed by a story id, and inventing one would
    put rows in the projection that `status` and `resume` could never join back
    to a real card.
    """


def resolve_repo_dir(repo_dir: Path) -> Path:
    """`--repo-dir` as an existing absolute directory, or `RepoDirError`.

    Resolved before anything is derived from it: `steps/worktree.ensure` refuses
    a relative `worktree` or `repo_dir` outright, and the default value of the
    option is `.`.
    """
    resolved = Path(repo_dir).expanduser().resolve()
    if not resolved.is_dir():
        raise RepoDirError(
            f"--repo-dir {str(repo_dir)!r} is not a directory (resolved to {resolved})"
        )
    return resolved


def mint_run_id(card_id: str, now: datetime) -> str:
    """`<UTC timestamp>-<short card id>`: unique, sortable, and greppable.

    The short id comes from `dag`, like every other derived name in the program,
    which also means a card id that is not a UUID is refused here rather than
    producing a run directory nobody can trace back to a card.
    """
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{dag.short_id(card_id)}"


def worktree_for(repo_dir: Path, branch: str) -> Path:
    """`<repo_dir>/.claude/worktrees/<branch>`, absolute.

    Absolute because `steps/worktree.ensure` requires it, and built by joining
    the branch's own segments so a branch like `m1/task-x` becomes two path
    components rather than one with a slash in its name.
    """
    return Path(repo_dir).resolve().joinpath(*WORKTREE_PARTS, *branch.split("/"))


def ok_envelope(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def error_envelope(error: BaseException) -> dict[str, Any]:
    """brd's failure envelope. The type is the exception's own class name, so an
    operator can grep the source for the thing that refused."""
    return {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}}


def render(envelope: Mapping[str, Any], *, pretty: bool = False) -> str:
    """The envelope as text: one line by default, indented under `--pretty`.

    `default=str` is not decoration: the payload carries a `Path`, and a
    renderer that raised `TypeError` on it would turn a finished run into a
    traceback with no envelope at all. `sort_keys` makes the output diffable.
    """
    if pretty:
        return json.dumps(envelope, indent=2, sort_keys=True, default=str)
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True, default=str)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


app = typer.Typer(
    add_completion=False,
    help="Drive brd cards through the agent-manager workflow engine.",
)


@app.callback()
def main() -> None:
    """agent-manager: run one subtask card end to end.

    The callback exists so `run` stays a named subcommand: a Typer app with one
    command and no callback collapses into a bare command, and §10's grammar is
    `agent-manager run ...`.
    """
