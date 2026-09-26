"""The pygents subtask engine (pygents-engine design §4): one subtask, one agent, one loop.

`run_subtask` is the old engine's `run_subtask` with the walk replaced. The
binding table, the escalation and the final subtask row are the old engine's
own helpers, called exactly as it calls them, so a summary, a journal line or
a phase row cannot tell the two engines apart (G10). What differs is the walk:
the workflow is compiled into two pygents tools, one `Agent` runs them, and
`agent.run()` is always consumed to the end -- never broken or returned out of.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pygents import Agent, AgentRegistry, ContextPool, ContextQueue

from agent_manager import engine as old
from agent_manager.runtime import checkpoint  # registers the BEFORE_TURN hook
from agent_manager.runtime import compile as C
from agent_manager.runtime import context
from agent_manager.runtime.state import RunDeps, current_run
from agent_manager.workflow.phases import Workflow


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
    clock: Callable[[], Any] = old._utcnow,
    should_stop: Callable[[], bool] | None = None,
) -> old.SubtaskSummary:
    """Walk `workflow`'s phases for one subtask on pygents. One `asyncio.run`.

    No `start_phase`: resume belongs to the yaml engine. Every turn is saved as
    a `turn` checkpoint before it runs, and the run ends with a `done` or
    `escalated` one (`runtime/checkpoint.py`). `should_stop` is asked before
    every turn: once it answers true, a `parked` checkpoint is saved, the next
    phase is not started, and the subtask is recorded `stopped before <phase>`.
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
) -> old.SubtaskSummary:
    # The binding, built and refused exactly as the old engine builds it:
    # before any agent exists, so a refusal records nothing.
    binding = old.subtask_context(
        subtask, repo_dir, commands, card=card, parent_story=parent_story
    )
    if extra_context:
        reserved = sorted(set(extra_context) & set(old.RESERVED_CONTEXT_KEYS))
        if reserved:
            raise old.EngineError(
                "extra_context supplies "
                f"{', '.join(repr(key) for key in reserved)}, which the engine owns "
                f"(reserved: {', '.join(old.RESERVED_CONTEXT_KEYS)})"
            )
        binding.update(extra_context)
    binding.update(old._document_paths(workflow, card))

    compiled = C.compile_workflow(workflow)
    agent = Agent(
        f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}",
        workflow.name,
        [compiled.agent_phase, compiled.step_phase],
        context_pool=ContextPool(),
        context_queue=ContextQueue(limit=10),
        tags=["subtask"],
    )
    try:
        await agent.context_pool.add(context.seed_item(binding))
        await agent.put(compiled.first_turn())
        deps = RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop)
        return await _run(agent, deps)
    finally:
        _forget(agent.name)


async def _run(agent: Agent, deps: RunDeps) -> old.SubtaskSummary:
    summary = old.SubtaskSummary()
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
        return old._stop(
            summary, deps.store, deps.story_id, deps.subtask, parked.before_phase
        )
    except C.Escalated as esc:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "escalated")
        return old._escalate(
            summary, deps.store, deps.story_id, deps.subtask, esc.phase, esc.detail
        )
    except old.EngineError:
        # A missing runner or an unresolvable input: a wiring or document bug
        # the old engine raises to its caller, `.phase`/`.parameter` intact.
        # Not an escalation, so no checkpoint row.
        raise
    except Exception as error:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "escalated")
        # `deps.running` is only `None` if the error came before any tool was
        # entered; there is no phase to name then.
        return old._escalate(
            summary,
            deps.store,
            deps.story_id,
            deps.subtask,
            deps.running or "?",
            old._render_error(error),
        )
    else:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "done")
    finally:
        # A `BaseException` (cancellation, KeyboardInterrupt) passes straight
        # through here and writes nothing: the last `turn` row stands.
        current_run.reset(token)
    old._record_subtask_status(deps.store, deps.story_id, deps.subtask, summary.status)
    return summary


def _collect(agent: Agent, deps: RunDeps, summary: old.SubtaskSummary) -> None:
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
