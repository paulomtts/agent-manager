"""The pygents subtask engine (pygents-engine design §4): one subtask, one agent, one loop.

The binding table, the escalation and the final subtask row are
`runtime/walk.py`'s helpers, so a summary, a journal line or a phase row keeps
the shape it has always had (G10). The workflow is compiled into two pygents
tools, one `Agent` runs them, and `agent.run()` is always consumed to the end
-- never broken or returned out of.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pygents import Agent, AgentRegistry, ContextPool, ContextQueue

from agent_manager.runtime import walk
from agent_manager.runtime import checkpoint  # registers the BEFORE_TURN hook
from agent_manager.runtime import compile as C
from agent_manager.runtime import context
from agent_manager.runtime.state import RunDeps, current_run
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
    turn in flight if there was one, else the queue head -- the reading the
    `BEFORE_TURN` hook makes. A `done` row holds no turn, and neither does an
    `escalated` row written after a phase escalated: `Escalated` enqueues
    nothing and `agent.run()` clears the turn in flight on its way out.
    """
    agent = checkpoint.agent
    turn = agent.get("current_turn") or next(iter(agent.get("queue") or ()), None)
    return None if turn is None else turn["kwargs"]["phase"]


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
    should_stop: Callable[[], bool] | None = None,
    resume_from: Checkpoint | None = None,
) -> walk.SubtaskSummary:
    """Walk `workflow`'s phases for one subtask on pygents. One `asyncio.run`.

    Every turn is saved as a `turn` checkpoint before it runs, and the run ends
    with a `done` or `escalated` one (`runtime/checkpoint.py`). `should_stop` is
    asked before every turn: once it answers true, a `parked` checkpoint is
    saved, the next phase is not started, and the subtask is recorded
    `stopped before <phase>`.

    `resume_from` continues from a saved checkpoint instead of the first phase:
    the agent is rebuilt from it, so the pool (seed and earlier results) and
    the queue (the pending turn and its loop count) are the checkpoint's, and
    no seed or first turn is added. A checkpoint saved under another workflow
    digest is refused with `CheckpointMismatch` before anything runs or is
    recorded.
    """
    return asyncio.run(
        _drive(
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
            should_stop=should_stop,
            resume_from=resume_from,
        )
    )


async def _drive(
    workflow: Workflow,
    store: Any,
    *,
    story_id: str,
    subtask: Any,
    repo_dir: Path,
    commands: Sequence[str],
    card: Any,
    parent_story: Any,
    extra_context: Mapping[str, Any] | None,
    agent_runner: Any,
    clock: Callable[[], Any],
    should_stop: Callable[[], bool] | None,
    resume_from: Checkpoint | None,
) -> walk.SubtaskSummary:
    # The binding, built and refused exactly as the old engine builds it:
    # before any agent exists, so a refusal records nothing.
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
        digest = workflow.digest()
        if resume_from.digest != digest:
            raise CheckpointMismatch(
                f"checkpoint {resume_from.card_id}#{resume_from.seq} was saved under "
                f"digest {resume_from.digest}, but workflow {workflow.name!r} "
                f"has digest {digest}"
            )
        # A run that died before its `finally` may have left its agent
        # registered under this name; `from_dict` would be refused it.
        _forget(resume_from.agent["name"])
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
    try:
        if resume_from is None:
            await agent.context_pool.add(context.seed_item(binding))
            await agent.put(compiled.first_turn())
        deps = RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop)
        return await _run(agent, deps)
    finally:
        _forget(agent.name)


async def _run(agent: Agent, deps: RunDeps) -> walk.SubtaskSummary:
    summary = walk.SubtaskSummary()
    token = current_run.set(deps)
    # Every `checkpoint.save` below runs inside this `try`, while `current_run`
    # is still set: after the `finally` resets it, `save` is a silent no-op.
    try:
        async for _ in agent.run():  # consumed to the end, always
            pass
    except checkpoint.Parked as parked:
        # The stop, raised by the BEFORE_TURN hook after it saved `parked`:
        # no further row, so that one stays the newest.
        _collect(agent, deps, summary)
        return walk._stop(
            summary, deps.store, deps.story_id, deps.subtask, parked.before_phase
        )
    except C.Escalated as esc:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "escalated")
        return walk._escalate(
            summary, deps.store, deps.story_id, deps.subtask, esc.phase, esc.detail
        )
    except walk.EngineError:
        # A missing runner or an unresolvable input: a wiring or document bug
        # the old engine raises to its caller, `.phase`/`.parameter` intact.
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
    """Free `name` in pygents' process-wide `AgentRegistry` for the next run.

    The installed pygents 0.7.0 has `AgentRegistry.unregister`, but switching
    to it is decision A2 of
    docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md, a card of
    its own. Until then this is the pre-0.7.0 workaround the milestone spec
    (§11) describes: pop the registry's dict, tolerating a name already gone.
    """
    AgentRegistry._registry.pop(name, None)
