"""Run helpers with no Typer in them (architecture cleanup, decision S1).

`cli.py` composes and renders; the plain pieces a run needs -- where its repo
is, what it is called, where its worktree goes, how a runner is made, and which
gate parameters it binds -- live here so the modules downstream of `cli` can
reach them without importing the Typer app. `cli.py` re-exports every name
defined here, so `cli.X is runs.X`. This module never imports `cli`.
"""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from agent_manager import dag
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.walk import AgentPhaseRunner
from agent_manager.store import Store

RUN_ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
"""Sortable, path-safe, second-resolution UTC. Run ids are directory names."""

WORKTREE_PARTS = (".claude", "worktrees")
"""Where a subtask's worktree lives under the repo, matching the layout the rest
of this project already uses."""


class CliError(RuntimeError):
    """The command refused to start a run. One base type for the envelope."""


class RepoDirError(CliError):
    """`--repo-dir` does not name a directory this tool can work in."""


class UnknownRunError(CliError):
    """`status` was asked for a run this project's projection does not hold.

    A `CliError` so it rides the existing `HANDLED` tuple into an `ok: false`
    envelope at exit 3 rather than reaching the renderer as a `None` tree. The
    same class covers "no most-recent run to default to": both are the same
    refusal -- the command was asked for a run and there is none -- and the
    message is what tells the two apart.
    """


class NotResumableError(CliError):
    """The run was found, and it holds nothing `resume` can pick up.

    Its own type rather than `UnknownRunError`'s: the run and its tree read
    fine, so what an operator does next -- start a fresh `run --card`, wait for
    `retry`, or drive the subtasks one at a time -- depends entirely on the
    status this message names, and a script can branch on the `type` field.
    """


class CheckpointMismatchError(CliError, runtime_engine.CheckpointMismatch):
    """`resume` found a checkpoint saved under another `TASK`.

    A `CliError`, so it rides `HANDLED` to an `ok: false` envelope at exit 3,
    and a `runtime_engine.CheckpointMismatch`, so it is the engine's own
    refusal by type (card 02890d5d). The CLI raises it itself, before any
    write, rather than letting `run_subtask` raise it after the orphan
    attempts and the `started` rows were already recorded.
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


class RunnerFactory(Protocol):
    """How the command gets its `AgentPhaseRunner`.

    A factory rather than a runner, because a real `dispatch.AgentRunner` needs
    the store and three ids that do not exist until the run is
    already half set up -- and because a factory is the seam the tests replace
    to launch no harness at all (§14: the launcher is injected).
    """

    def __call__(
        self,
        *,
        store: Store,
        run_id: str,
        story_id: str,
        card_id: str,
    ) -> AgentPhaseRunner: ...


def gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]:
    """The gate parameters `TASK`'s gates bind and `subtask_context` lacks.

    `explore` gates on `verification_gate(suite_cmds, allow_no_verification,
    caller_provided)` and `exploration_output_gate(explore,
    provided_verification)`. Three of those four names come from the caller, and
    this is the caller. `bool()` is deliberate: the reducer tests
    `allow_no_verification is True`, so a truthy stand-in must not open the
    opt-out by accident. `caller_provided` is `False` and
    `provided_verification` is `None` because this card discovers no suite --
    per-run verification discovery is the milestone runner's, not this command's.
    """
    return {
        "suite_cmds": list(commands),
        "allow_no_verification": bool(allow_no_verification),
        "caller_provided": False,
        "provided_verification": None,
    }
