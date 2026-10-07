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

from agent_manager import prompt
from agent_manager.errors import AgentPhaseFailed, LimitWaitInterrupted
from agent_manager.runtime import bridge, context, walk
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.state import current_run
from agent_manager.workflow.phases import AgentPhase, Workflow

STEP_TIMEOUT = 3600.0
"""A step's turn timeout, in seconds. Steps have no declared timeout; an hour
bounds a hung git or test-suite call without cutting a slow suite short."""


class Escalated(Exception):
    """A phase ended the subtask: `.phase` names it, `.detail` says why.

    `.result` is the agent's own decoded payload when the phase produced one
    before a gate failed it (`AgentPhaseFailed.result`); `None` for every other
    escalation. `_run` injects it into `summary.results[phase]`, since a failed
    phase's `ContextItem` is never yielded -- only a successful phase's is.
    """

    def __init__(self, phase: str, detail: str | None, result: Any = None) -> None:
        self.phase = phase
        self.detail = detail
        self.result = result
        super().__init__(f"{phase}: {detail}")


@dataclass(frozen=True)
class Compiled:
    """One workflow's two registered tools, and the turns that drive them."""

    workflow: Workflow
    agent_phase: Any
    step_phase: Any

    def turn_for(self, name: str, loop: int, allowance: float = 0.0) -> Turn:
        """`name`'s turn; an agent turn's timeout grows by `allowance` seconds,
        the time its runner may spend waiting out a usage limit."""
        p = self.workflow.phase(name)
        kwargs = {"phase": name, "loop": loop}
        if isinstance(p, AgentPhase):
            return Turn(
                self.agent_phase, timeout=p.timeout.total_seconds() + allowance, kwargs=kwargs
            )
        return Turn(self.step_phase, timeout=STEP_TIMEOUT, kwargs=kwargs)

    def first_turn(self, allowance: float = 0.0) -> Turn:
        return self.turn_for(self.workflow.phases[0].name, 0, allowance)

    def after(self, name: str, loop: int, allowance: float = 0.0) -> Turn | None:
        names = self.workflow.phase_names
        i = names.index(name)
        return self.turn_for(names[i + 1], loop, allowance) if i + 1 < len(names) else None


def turn_allowance(runner: Any) -> float:
    """Seconds `runner` declares it may wait inside one agent turn; 0 for a runner with none."""
    return float(getattr(runner, "turn_allowance", 0.0) or 0.0)


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
            # A wiring bug, not a phase failure: raised before anything runs,
            # rather than escalated as a TypeError.
            raise EngineError(
                "is an agent phase, but no agent runner was injected", phase=phase
            )
        table = context.binding_table(pool, memory, phase)
        # Outside the try: an input no resolver provides is a workflow bug
        # and its `EngineError` must reach the caller as is.
        rendered = prompt.render_prompt(p, table)
        # The resume checkpoint's floor, if it names this very turn
        # (exactly-once E8). Taken whether or not the runner can adopt, so no
        # adoption outlives the first turn after a resume.
        allowance = turn_allowance(deps.agent_runner)
        adoption = deps.take_adoption(phase, loop)
        adopt = getattr(deps.agent_runner, "adopt", None)
        try:
            # Inside the try: an adopt that raises escalates like a dispatch
            # error. `None` -- nothing recorded, or a decline with its own
            # warning -- dispatches as if there had been no crash.
            adopted = None
            if adoption is not None and adopt is not None:
                adopted = await bridge.call_step(
                    adopt,
                    {
                        "phase": p,
                        "context": table,
                        "source_run": adoption.source_run,
                        "floor": adoption.floor,
                    },
                )
            if adopted is not None:
                result = adopted.result
            else:
                result = await bridge.call_agent(deps.agent_runner, p, table, rendered)
        except AgentPhaseFailed as failure:
            if p.on_fail is not None and loop < p.on_fail.max_loops:
                yield ContextItem(
                    content={"for": p.on_fail.phase, "from": phase, "detail": failure.detail}
                )
                yield holder["compiled"].turn_for(p.on_fail.phase, loop + 1, allowance)
                return
            raise Escalated(phase, failure.detail, result=failure.result) from failure
        except LimitWaitInterrupted:
            # A stop arrived during a usage-limit wait: the same turn is queued
            # again, so the paused agent parks before this phase.
            yield holder["compiled"].turn_for(phase, loop, allowance)
            return
        except Exception as error:
            # Total: an exception escaping the walk would leave the subtask
            # recorded `started` forever.
            raise Escalated(phase, walk._render_error(error)) from error
        yield ContextItem(id=phase, description=f"{phase} result", content=context.encode(result))
        nxt = holder["compiled"].after(
            phase, 0 if phase in fresh_loop_after else loop, allowance
        )
        if nxt is not None:
            yield nxt

    async def step_phase(phase: str, loop: int, pool: ContextPool):
        deps = current_run.get()
        deps.running = phase
        allowance = turn_allowance(deps.agent_runner)
        # A step is never adopted and stays at-least-once (exactly-once E9);
        # taking the carried adoption here ends it at the first turn after
        # a resume, so no later agent phase can inherit it.
        deps.take_adoption(phase, loop)
        p = deps.workflow.phase(phase)
        # A step never reads feedback, so it gets an empty memory window.
        table = context.binding_table(pool, ContextQueue(limit=1), phase)
        outcome = await bridge.call_step(
            walk.run_one_step,
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
                yield holder["compiled"].turn_for(outcome.skip_to, loop, allowance)
                return
        nxt = holder["compiled"].after(phase, loop, allowance)
        if nxt is not None:
            yield nxt

    _rename(agent_phase, f"agent_phase_{wf.name}_{suffix}")
    _rename(step_phase, f"step_phase_{wf.name}_{suffix}")
    compiled = Compiled(wf, tool(agent_phase), tool(step_phase))
    holder["compiled"] = compiled
    return compiled
