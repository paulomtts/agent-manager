"""The pygents subtask engine (pygents-engine design §4): one subtask, one agent, one loop.

The binding table, the escalation and the final subtask row are
`runtime/walk.py`'s helpers, so a summary, a journal line or a phase row keeps
the shape it has always had (G10). The workflow is compiled into two pygents
tools, one `Agent` runs them, and `agent.run()` is always consumed to the end
-- never broken or returned out of.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pygents import Agent, AgentRegistry, ContextPool, ContextQueue
from pygents.errors import UnregisteredAgentError

from agent_manager.runtime import walk
from agent_manager.runtime import checkpoint  # registers the BEFORE_TURN and ON_PAUSE hooks
from agent_manager.runtime import compile as C
from agent_manager.runtime import context
from agent_manager.runtime.state import Adoption, RunDeps, current_run
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps import worktree
from agent_manager.workflow.phases import Workflow

if TYPE_CHECKING:
    # Annotation only (the module has `from __future__ import annotations`);
    # the name `store` is taken by `run_subtask`'s parameter.
    from agent_manager.store import Checkpoint


class CheckpointMismatch(Exception):
    """A checkpoint saved under another version of the workflow: its digest is
    not the digest of the workflow asked to resume it. Raised before any agent
    is built, so nothing is run or recorded."""


def pending_phase(checkpoint: Checkpoint) -> str | None:
    """The phase `checkpoint`'s agent would run next, or `None` if it holds no turn.

    Read-only: it reads the stored `Agent.to_dict()` and builds nothing, so a
    caller outside `runtime/` can name where a resume would start without
    touching a pygents structure itself (card 02890d5d). The next turn is the
    turn in flight if there was one, else the queue head. A `done` row holds
    no turn, and neither does an
    `escalated` row written after a phase escalated: `Escalated` enqueues
    nothing and `agent.run()` clears the turn in flight on its way out.
    """
    agent = checkpoint.agent
    turn = agent.get("current_turn") or next(iter(agent.get("queue") or ()), None)
    return None if turn is None else turn["kwargs"]["phase"]


def kept_commands(checkpoint: Checkpoint) -> list[str] | None:
    """The verification commands `checkpoint`'s walk keeps, or `None` if it does not say.

    Read-only, as `pending_phase` is: it reads the `"subtask"` seed item out of
    the stored `Agent.to_dict()` and builds nothing. A resume continues with
    the checkpoint's pool, so this seed -- not a newly passed `--verify` -- is
    the suite the resumed walk verifies with (card 5b19aa93). A pool with no
    seed, a seed with no `commands`, or `commands` that are not a list give
    `None`, never an exception: a report must never fail a resume.
    """
    pool = checkpoint.agent.get("context_pool") or {}
    for item in pool.get("items") or ():
        if item.get("id") != context.SUBTASK:
            continue
        content = item.get("content")
        commands = content.get("commands") if isinstance(content, dict) else None
        return list(commands) if isinstance(commands, list) else None
    return None


def run_subtask(
    workflow: Workflow,
    store: Any,
    *,
    story_id: str,
    subtask: Any,
    repo_dir: Path,
    commands: Sequence[str] = (),
    card: Any = None,
    parent_story: Any = None,
    extra_context: Mapping[str, Any] | None = None,
    agent_runner: Any = None,
    clock: Callable[[], Any] = walk._utcnow,
    ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure,
    stop: StopSignal | None = None,
    resume_from: Checkpoint | None = None,
) -> walk.SubtaskSummary:
    """Walk `workflow`'s phases for one subtask on pygents. One `asyncio.run`
    around `run_subtask_async`, which documents the parameters."""
    return asyncio.run(
        run_subtask_async(
            workflow,
            store,
            story_id=story_id,
            subtask=subtask,
            repo_dir=repo_dir,
            commands=commands,
            card=card,
            parent_story=parent_story,
            extra_context=extra_context,
            agent_runner=agent_runner,
            clock=clock,
            ensure_worktree=ensure_worktree,
            stop=stop,
            resume_from=resume_from,
        )
    )


async def run_subtask_async(
    workflow: Workflow,
    store: Any,
    *,
    story_id: str,
    subtask: Any,
    repo_dir: Path,
    commands: Sequence[str] = (),
    card: Any = None,
    parent_story: Any = None,
    extra_context: Mapping[str, Any] | None = None,
    agent_runner: Any = None,
    clock: Callable[[], Any] = walk._utcnow,
    ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure,
    stop: StopSignal | None = None,
    resume_from: Checkpoint | None = None,
) -> walk.SubtaskSummary:
    """Walk `workflow`'s phases for one subtask on the running event loop.

    Every turn is saved as a `turn` checkpoint before it runs, and the run ends
    with a `done` or `escalated` one (`runtime/checkpoint.py`).

    `stop`, a `StopSignal`, is the only stop. The agent is registered with it
    for the run and unregistered on every exit. A trigger pauses the agent:
    the turn in flight finishes, a `parked` checkpoint is saved, the next
    phase is not started, and the subtask is recorded `stopped before
    <phase>`. A trigger after the last phase finished changes nothing: the
    subtask ends `done`.

    `resume_from` continues from a saved checkpoint instead of the first phase:
    the agent is rebuilt from it, so the pool (seed and earlier results) and
    the queue (the pending turn and its loop count) are the checkpoint's, and
    no seed or first turn is added. A checkpoint saved under another workflow
    digest is refused with `CheckpointMismatch` before anything runs or is
    recorded.

    `ensure_worktree` is `steps.worktree.ensure` unless a test injects a fake:
    a resume calls it, on a worker thread, only when the subtask's worktree
    directory is missing (resume worktree re-ensure §3.1). `summary.resumed_at`
    names the phase a kept checkpoint continued at, else `None`.
    """
    # The binding, built and refused before any agent exists, so a refusal
    # records nothing.
    binding = walk.subtask_context(
        subtask, repo_dir, commands, card=card, parent_story=parent_story
    )
    if extra_context:
        reserved = sorted(set(extra_context) & set(walk.RESERVED_CONTEXT_KEYS))
        if reserved:
            raise walk.EngineError(
                "extra_context supplies "
                f"{', '.join(repr(key) for key in reserved)}, which the engine owns "
                f"(reserved: {', '.join(walk.RESERVED_CONTEXT_KEYS)})"
            )
        binding.update(extra_context)
    binding.update(walk._document_paths(workflow, card))

    # Compiled first on both paths: it registers the digest-prefixed tools
    # that `Agent.from_dict` below resolves by name from `ToolRegistry`.
    compiled = C.compile_workflow(workflow)
    resumed_at: str | None = None
    # Out-of-band lines for `summary.warnings`, handed to `RunDeps` below.
    warnings: list[str] = []
    if resume_from is not None:
        digest = workflow.digest()
        if resume_from.digest != digest:
            raise CheckpointMismatch(
                f"checkpoint {resume_from.card_id}#{resume_from.seq} was saved under "
                f"digest {resume_from.digest}, but workflow {workflow.name!r} "
                f"has digest {digest}"
            )
        # A run that died before its `finally` may have left its agent
        # registered under this name; `from_dict` -- or, on a decline, the
        # fresh `Agent` of the same name -- would be refused it.
        _forget(resume_from.agent["name"])
        if await _worktree_kept(resume_from, subtask, repo_dir, ensure_worktree, warnings):
            resumed_at = pending_phase(resume_from)
        else:
            # Declined: the walk starts over exactly as a fresh run does. The
            # row is neither deleted nor rewritten; the fresh walk's first
            # `turn` save, at a higher seq, supersedes it.
            resume_from = None
    if resume_from is None:
        agent = Agent(
            f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}",
            workflow.name,
            [compiled.agent_phase, compiled.step_phase],
            context_pool=ContextPool(),
            context_queue=ContextQueue(limit=10),
            tags=["subtask"],
        )
    else:
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
        # A row parked by a `StopSignal` was saved while its agent was paused,
        # and `from_dict` restores that pause; left in place, ON_PAUSE would
        # park the resumed agent again before it ran anything. That pause
        # belonged to the stopped run: only this run's `stop`, registered in
        # `_run` after this line, may pause the agent now.
        agent.resume()
    try:
        if resume_from is None:
            await agent.context_pool.add(context.seed_item(binding))
            await agent.put(compiled.first_turn(C.turn_allowance(agent_runner)))
        # The resume checkpoint's floor is carried as the run's adoption, so a
        # re-save of that turn writes it unchanged (exactly-once E4/E5). A fresh
        # run, or a row saved with no floor, starts with none.
        adopt = (
            None
            if resume_from is None or resume_from.floor is None
            else Adoption(**vars(resume_from.floor))
        )
        deps = RunDeps(
            workflow,
            store,
            story_id,
            subtask,
            agent_runner,
            clock,
            stop=stop,
            adopt=adopt,
            warnings=warnings,
        )
        summary = await _run(agent, deps)
        summary.resumed_at = resumed_at
        return summary
    finally:
        _forget(agent.name)


async def _worktree_kept(
    checkpoint: Checkpoint,
    subtask: Any,
    repo_dir: Path,
    ensure_worktree: Callable[..., Mapping[str, object]],
    warnings: list[str],
) -> bool:
    """Whether `checkpoint` may be resumed as saved, its worktree being there.

    Resume worktree re-ensure §3.1-§3.4. No path, or a path that is a
    directory, keeps the checkpoint and runs nothing: no git, no warning. A
    missing directory gets one `ensure_worktree` call on a worker thread (it
    is sync git): a branch that survived keeps the checkpoint, the worktree
    re-added; a branch that is gone, or any error, declines it, so the walk
    starts over from the first phase, whose own `ensure` step reports a real
    failure the ordinary way. Never raises: a missing worktree is a recovery,
    not a refusal. Each non-fast outcome appends exactly one line to `warnings`.
    """
    path = subtask.worktree_path
    if path is None or Path(path).is_dir():
        return True
    label = f"checkpoint #{checkpoint.seq} of run {checkpoint.run_id}"
    try:
        report = await asyncio.to_thread(
            ensure_worktree, subtask.branch, subtask.base_branch, path, repo_dir
        )
    except Exception as error:
        warnings.append(
            f"{label} was not resumed (worktree {path} is missing and could not be"
            f" added again: {walk._render_error(error)}); starting from the first phase"
        )
        return False
    if not report.get("branch_existed"):
        warnings.append(
            f"{label} was not resumed (worktree {path} is missing and branch"
            f" '{subtask.branch}' no longer exists); starting from the first phase"
        )
        return False
    warnings.append(
        f"{label}: worktree {path} was missing and was added again for branch"
        f" '{subtask.branch}'; resuming at '{pending_phase(checkpoint)}'"
    )
    return True


async def _run(agent: Agent, deps: RunDeps) -> walk.SubtaskSummary:
    summary = walk.SubtaskSummary()
    token = current_run.set(deps)
    # Every `checkpoint.save` below runs inside this `try`, while `current_run`
    # is still set: after the `finally` resets it, `save` is a silent no-op.
    try:
        if deps.stop is not None:
            # Registered for exactly the life of `run()`: a signal that has
            # already fired pauses the agent here, before its first turn.
            deps.stop.register(agent)
        async for _ in agent.run():  # consumed to the end, always
            pass
    except checkpoint.Parked as parked:
        # The stop, raised by the ON_PAUSE hook after it saved `parked`: no
        # further row, so that one stays the newest.
        _collect(agent, deps, summary)
        return walk._stop(
            summary, deps.store, deps.story_id, deps.subtask, parked.before_phase
        )
    except C.Escalated as esc:
        _collect(agent, deps, summary)
        if esc.result is not None:
            # The failed phase's own `ContextItem` is never yielded -- only a
            # successful phase's is -- so `_collect` cannot see it; this is the
            # one place the agent's own explanation reaches `summary.results`
            # for an escalation (read by `comments.agent_reason`).
            summary.results[esc.phase] = esc.result
        checkpoint.save(agent, "escalated")
        return walk._escalate(
            summary, deps.store, deps.story_id, deps.subtask, esc.phase, esc.detail
        )
    except walk.EngineError:
        # A missing runner or an unresolvable input: a wiring or workflow bug
        # raised to the caller, `.phase`/`.parameter` intact.
        # Not an escalation, so no checkpoint row.
        raise
    except Exception as error:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "escalated")
        # `deps.running` is only `None` if the error came before any tool was
        # entered; there is no phase to name then.
        return walk._escalate(
            summary,
            deps.store,
            deps.story_id,
            deps.subtask,
            deps.running or "?",
            walk._render_error(error),
        )
    else:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "done")
    finally:
        # A `BaseException` (cancellation, KeyboardInterrupt) passes straight
        # through here and writes nothing: the last `turn` row stands.
        # Unregistered on every exit, so a later trigger never pauses an
        # agent whose run is over.
        if deps.stop is not None:
            deps.stop.unregister(agent)
        current_run.reset(token)
    walk._record_subtask_status(deps.store, deps.story_id, deps.subtask, summary.status)
    return summary


def _collect(agent: Agent, deps: RunDeps, summary: walk.SubtaskSummary) -> None:
    summary.results = {
        item.id: context.decode(item.content)
        for item in agent.context_pool.items
        if item.id not in (context.SUBTASK, context.SKIPPED)
    }
    summary.skipped = list(deps.skipped)
    summary.warnings = list(deps.warnings)


def _forget(name: str) -> None:
    """Free `name` in pygents' process-wide `AgentRegistry` so it can be reused.

    The name may legitimately be absent: a resumed checkpoint's agent was
    never registered in this process, or a run's own name is already gone.
    Only `UnregisteredAgentError` is tolerated; any other error is a real
    fault and propagates.
    """
    with contextlib.suppress(UnregisteredAgentError):
        AgentRegistry.unregister(name)
