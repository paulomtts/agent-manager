# Adopting pygents 0.7.0 — design addendum

Date: 2026-09-25
Extends: `2026-09-25-pygents-engine-design.md` (milestone 6: §2, §11), `2026-09-25-supervisor-tree-design.md` (milestone 7: §7)
Status: milestone 8 scope; decisions A1–A5. Builds on milestone 7 merged.

## 1. Why

Milestones 6 and 7 were designed against pygents 0.6.7 and work around four pygents defects found
while sketching them. pygents 0.7.0 (released 2026-09-25) fixes all four. This milestone replaces
the workarounds with the fixes, raises the dependency floor, and pins the fixed behaviour with tests
in this repo so a pygents regression shows up here.

## 2. What was found

Verified against `pygents==0.7.0` from PyPI with the original reproductions:

| pygents defect (0.6.7) | 0.7.0 behaviour (verified) | Workaround in M6/M7 (as planned) |
|---|---|---|
| Leaving `agent.run()` early raised `SafeExecutionError`, left `_is_running = True` and the tool task running | early exit closes the in-flight turn: `_is_running` False, turn `StopReason.CANCELLED`, no loop errors, the agent is reusable at once | M6 rule "never break or return out of `agent.run()`"; M6/M7 rely on `asyncio.run` cancellation for Ctrl-C without knowing the agent's state afterwards |
| Instance hooks that are closures collided by name (`ValueError`) | closures attach; saving an owner that holds one raises `UnserializableHookError` | M6 rule "never register a pygents hook as a closure" (M6's hooks are module-level and global by design, which stays) |
| No way to remove one registry entry | `AgentRegistry.unregister(name)` (and on `ToolRegistry`, `HookRegistry`); an unknown name raises the registry's not-found error | M6 Task 3.4/4.3: `runtime/engine.py` forgets an agent's name by editing the registry's private dict before resume and after a run |
| `to_dict()` in `AFTER_TURN` listed the finished turn as `current_turn` | `current_turn` is `None` in `AFTER_TURN` | M6 checkpoints only at `BEFORE_TURN` (correct either way; nothing to remove) |

Also in 0.7.0: `BaseTool` exported; `safe_execution` closes the wrapped async generator on early
stop. Neither is used here.

Unchanged by 0.7.0, and staying:
- M6's bridge kills the `claude -p` process tree when a turn is cancelled: pygents cancels its
  own task, but cannot reach a process a `to_thread` worker launched.
- M6's hooks stay global, module-level `@hook(..., tags={"subtask"})` functions: they must fire
  for every subtask agent, and must stay saveable.
- The at-least-once window (issue `232cbd44`): the phase's journal row is written inside its turn,
  so a checkpoint at `AFTER_TURN` has the same window as the next `BEFORE_TURN`. 0.7.0 makes
  `AFTER_TURN` consistent but does not close that window; the issue stays open.

## 3. Decisions

**A1 — `pygents>=0.7.0`.** `pyproject.toml` and `uv.lock`.

**A2 — Registry removal uses `unregister`.** Every place that removes an agent (or a compiled
tool) from a pygents registry by touching private state calls `AgentRegistry.unregister(name)` /
`ToolRegistry.unregister(name)` instead, ignoring only the registry's not-found error where the
name may legitimately be absent. No `_registry`/`_items` access to pygents registries remains in
`src/`. Tests that clear whole registries between cases may keep `clear()`.

**A3 — Cancellation is relied on and pinned.** New tests in this repo: cancelling the task that
drives a subtask agent mid-phase (the Ctrl-C path) leaves the agent not running, the phase's turn
`CANCELLED`, the fake launcher's process killed by M6's bridge, and the latest `turn` checkpoint
intact for `am resume`. Any defensive code M6/M7 added only because pygents mishandled
cancellation or early exit is removed; the tests stay.

**A4 — The workaround rules are retired, not the designs.** The rules "never break or return out
of `agent.run()`" and "never register a pygents hook as a closure" are removed from the M6/M7
addenda's constraints, each replaced by a one-line note that pygents 0.7.0 makes the pattern
safe. Consuming `run()` to the end remains the engine's normal loop, and the hooks remain global
and module-level, because those are the right designs, not workarounds. Where an engine path now
exits `run()` early (for example on `Parked`), the old "consume fully" dance may be simplified.

**A5 — Saving can never meet a closure.** A test pins that a subtask agent built by the engine
carries no instance hooks, so `to_dict()` never raises `UnserializableHookError`; if a future
change attaches one, that test names it.

## 4. Changes by file (as planned in M6/M7; cards read the code as built)

| File | Change |
|---|---|
| `pyproject.toml`, `uv.lock` | `pygents>=0.7.0` (A1) |
| `src/agent_manager/runtime/engine.py` | `unregister` instead of the private-dict forget, before resume and after a run (A2) |
| `src/agent_manager/runtime/compile.py` | `ToolRegistry.unregister` wherever compiled tools are dropped, if any (A2) |
| `tests/runtime/` | cancellation tests (A3), no-closure test (A5) |
| `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`, `2026-09-25-supervisor-tree-design.md` | retire the two workaround rules (A4); mark M6 §11's upstream-fixes item done |
| `CLAUDE.md` | nothing unless it carries either rule |

## 5. Testing

- `uv run pytest` green.
- `grep -rn "_registry\|_items" src/agent_manager | grep -i -E "AgentRegistry|ToolRegistry|HookRegistry"` returns nothing (A2).
- A3 and A5 tests as above, with the fake launcher and real pygents agents.

## 6. Out of scope

- Exactly-once phases (issue `232cbd44`), live control (`eb5db173`), several `am` processes (`2db2a2ef`).
- Any change to pygents itself.
