"""A `phases.Workflow` as two generic pygents tools (pygents-engine design G3, §5).

Every phase runs through one of two tools: `agent_phase` for an `AgentPhase`,
`step_phase` for a `Step`. A turn names the tool and carries only
`{"phase", "loop"}`; everything else is read at run time -- the binding table
from the pool, the run's dependencies from `state.current_run`.

pygents' `ToolRegistry` is process-wide, keyed on the tool's `__name__`, and
refuses a second tool under a name it holds. So the tools are named after the
workflow and the first eight hex digits of its digest, and a compilation is
built once per `(name, digest)` behind a lock: two lanes compiling one workflow
share one compilation, and two versions of a workflow get two.

The tools read each phase from the running workflow (`deps.workflow`), not the
compiled one. Two workflow objects can share a name and a digest -- the digest
keys a callable on `module.qualname`, which a factory's closures share -- while
holding different callables, and a shared compilation must still call the
running workflow's own. Everything the compilation itself reads (phase order,
kinds, timeouts) is covered by the digest, so it cannot differ between them.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from pygents import ContextItem, ContextPool, ContextQueue, Turn, tool

from agent_manager import engine as old_engine
from agent_manager import prompt
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.runtime import bridge, context
from agent_manager.runtime.state import current_run
from agent_manager.workflow.phases import AgentPhase, Workflow

STEP_TIMEOUT = 3600.0
"""A step's turn timeout, in seconds. Steps have no declared timeout; an hour
bounds a hung git or test-suite call without cutting a slow suite short."""


class Escalated(Exception):
    """A phase ended the subtask: `.phase` names it, `.detail` says why."""

    def __init__(self, phase: str, detail: str | None) -> None:
        self.phase = phase
        self.detail = detail
        super().__init__(f"{phase}: {detail}")


@dataclass(frozen=True)
class Compiled:
    """One workflow's two registered tools, and the turns that drive them."""

    workflow: Workflow
    agent_phase: Any
    step_phase: Any

    def turn_for(self, name: str, loop: int) -> Turn:
        p = self.workflow.phase(name)
        kwargs = {"phase": name, "loop": loop}
        if isinstance(p, AgentPhase):
            return Turn(self.agent_phase, timeout=p.timeout.total_seconds(), kwargs=kwargs)
        return Turn(self.step_phase, timeout=STEP_TIMEOUT, kwargs=kwargs)

    def first_turn(self) -> Turn:
        return self.turn_for(self.workflow.phases[0].name, 0)

    def after(self, name: str, loop: int) -> Turn | None:
        names = self.workflow.phase_names
        i = names.index(name)
        return self.turn_for(names[i + 1], loop) if i + 1 < len(names) else None


_CACHE: dict[tuple[str, str], Compiled] = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    """Forget every compilation. For tests, which clear `ToolRegistry` alongside:
    recompiling while the old tools are still registered would be refused."""
    with _LOCK:
        _CACHE.clear()


def compile_workflow(wf: Workflow) -> Compiled:
    key = (wf.name, wf.digest())
    with _LOCK:
        compiled = _CACHE.get(key)
        if compiled is None:
            compiled = _CACHE[key] = _build(wf, suffix=key[1][:8])
        return compiled


def _rename(fn: Any, name: str) -> None:
    # `ToolRegistry` keys on `__name__`, which `tool()` copies from the function;
    # `__qualname__` is set too so the tool's repr names what it is registered as.
    fn.__name__ = name
    fn.__qualname__ = name


def _fresh_loop_after(wf: Workflow) -> frozenset[str]:
    """The critics whose pass gives what follows a fresh loop count (G4).

    G4 lets each critic loop at most once, so a loop one critic spent must not
    use up the next one's. A critic's pass resets the count unless some later
    phase's `Goto` lands at or before it: that walk can come back through the
    critic, and a reset there would re-earn the loop on every pass and never
    escalate. Such workflows keep the one shared count.
    """
    names = wf.phase_names
    fresh = set()
    for i, p in enumerate(wf.phases):
        if not isinstance(p, AgentPhase) or p.on_fail is None:
            continue
        if not any(
            isinstance(q, AgentPhase)
            and q.on_fail is not None
            and names.index(q.on_fail.phase) <= i
            for q in wf.phases[i + 1 :]
        ):
            fresh.add(p.name)
    return frozenset(fresh)


def _build(wf: Workflow, *, suffix: str) -> Compiled:
    holder: dict[str, Compiled] = {}
    fresh_loop_after = _fresh_loop_after(wf)

    async def agent_phase(phase: str, loop: int, pool: ContextPool, memory: ContextQueue):
        deps = current_run.get()
        deps.running = phase
        p = deps.workflow.phase(phase)
        if deps.agent_runner is None:
            # A wiring bug, not a phase failure: raised as the old engine raises
            # it, before anything runs, rather than escalated as a TypeError.
            raise EngineError(
                "is an agent phase, but no agent runner was injected", phase=phase
            )
        table = context.binding_table(pool, memory, phase)
        # Outside the try, as in the old engine: an input no resolver provides
        # is a workflow bug and its `EngineError` must reach the caller as is.
        rendered = prompt.render_prompt(p, table)
        try:
            result = await bridge.call_agent(deps.agent_runner, p, table, rendered)
        except AgentPhaseFailed as failure:
            if p.on_fail is not None and loop < p.on_fail.max_loops:
                yield ContextItem(
                    content={"for": p.on_fail.phase, "from": phase, "detail": failure.detail}
                )
                yield holder["compiled"].turn_for(p.on_fail.phase, loop + 1)
                return
            raise Escalated(phase, failure.detail) from failure
        except Exception as error:
            # Total, as the old engine's agent branch is: an exception escaping
            # the walk would leave the subtask recorded `started` forever.
            raise Escalated(phase, old_engine._render_error(error)) from error
        yield ContextItem(id=phase, description=f"{phase} result", content=context.encode(result))
        nxt = holder["compiled"].after(phase, 0 if phase in fresh_loop_after else loop)
        if nxt is not None:
            yield nxt

    async def step_phase(phase: str, loop: int, pool: ContextPool):
        deps = current_run.get()
        deps.running = phase
        p = deps.workflow.phase(phase)
        # A step never reads feedback, so it gets an empty memory window.
        table = context.binding_table(pool, ContextQueue(limit=1), phase)
        outcome = await bridge.call_step(
            old_engine.run_one_step,
            {
                "phase": p,
                "table": table,
                "store": deps.store,
                "story_id": deps.story_id,
                "subtask": deps.subtask,
                "clock": deps.clock,
            },
        )
        deps.warnings.extend(outcome.warnings)
        if not outcome.ok:
            if not p.best_effort:
                raise Escalated(phase, outcome.detail)
            deps.warnings.append(f"best-effort phase {phase!r} failed: {outcome.detail}")
        else:
            yield ContextItem(
                id=phase, description=f"{phase} result", content=context.encode(outcome.result)
            )
            if outcome.skip_to is not None:
                names = holder["compiled"].workflow.phase_names
                deps.skipped.extend(names[names.index(phase) + 1 : names.index(outcome.skip_to)])
                yield holder["compiled"].turn_for(outcome.skip_to, loop)
                return
        nxt = holder["compiled"].after(phase, loop)
        if nxt is not None:
            yield nxt

    _rename(agent_phase, f"agent_phase_{wf.name}_{suffix}")
    _rename(step_phase, f"step_phase_{wf.name}_{suffix}")
    compiled = Compiled(wf, tool(agent_phase), tool(step_phase))
    holder["compiled"] = compiled
    return compiled
