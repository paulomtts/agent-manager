# Task 1.2 — Pin pygents' cancellation behaviour and drop pre-0.7 guards (card 0822234f)

Parent story: dd4a87d5 "Adopt pygents 0.7.0" (milestone b75742dd). Spec of record: `docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md`, decisions A3 and A5 (with A4 bounding what may be removed). Blocked by 59fd7f22 (Task 1.1); this worktree already carries its state: `engine.py`'s `_forget()` uses `contextlib.suppress(UnregisteredAgentError): AgentRegistry.unregister(name)`.

## Scope

1. New file `tests/runtime/test_cancellation.py`: pins, with real pygents `Agent` objects, a fake launcher and a temporary store, how the engine behaves when the task driving a subtask agent is cancelled (A3) and that engine-built agents carry no instance hooks (A5). Conventions follow `tests/runtime/test_resume.py` (agent_runner injection, `StopSignal` fixtures, its `_go`-style driver helper).
2. Remove from `src/agent_manager/runtime/engine.py` (and `src/agent_manager/orchestrate.py`, only if it holds any) code that exists solely to work around pre-0.7 pygents mishandling cancellation or early exit. Before editing, read the code as built and confirm each candidate. Expected result, based on locating:
   - `_run`'s `async for _ in agent.run(): pass` (engine.py ~197) is the normal loop. `checkpoint.Parked` already leaves via a raise, and no extra "keep consuming after Parked" loop exists. Keep it (A4). The module docstring (lines 1-7) and the inline comment "consumed to the end, always" stay too, because retiring the rule's wording belongs to Task 1.3's docs.
   - No `_is_running` manual reset exists in `src/`, so there is nothing to remove.
   - `_forget()` and its two call sites (before `Agent.from_dict` on resume, and in `run_subtask_async`'s `finally`) are A2's design, not a workaround. Keep them.
   - orchestrate.py's cancellation forwarding (grafo `run_until_killed` and the `BaseException` handling around lines 1100-1234) is genuine propagation design. Leave it untouched unless Step 1 shows a pygents-specific guard.
   If confirmation matches this, the source change is empty and the card's report must say so explicitly. It must not invent a removal.

## Observable behaviour pinned

- **Cancel mid-agent-phase** (the Ctrl-C path): `run_subtask_async` runs as an asyncio task. The fake launcher blocks inside an agent phase, and the test cancels the task. Afterwards:
  - `CancelledError` propagates to the caller.
  - The agent is not running, and its name is free in `AgentRegistry`: a fresh agent under the same name can be constructed or registered.
  - The turn in flight ended with stop reason `CANCELLED`.
  - The fake launcher's process was killed by M6's bridge (`runtime/bridge.py` kill_tree, unchanged).
  - The store's newest checkpoint for the card has `reason == "turn"` and no `done`/`escalated`/`parked` row after it.
  - `pending_phase(latest)` names the cancelled phase, and resuming from that checkpoint with a non-blocking fake launcher runs to `done`.
- **Cancel during a deterministic step phase**: the same assertions, except for the process kill, which applies only if the step spawns through the bridge. Specifically: the agent is not running, the name is free, the turn is `CANCELLED`, and the latest `turn` checkpoint is intact and resumable.
- **No closure hooks (A5)**: an agent built by `run_subtask_async` on the fresh path, and one rebuilt through the resume path (`Agent.from_dict` of a stored checkpoint), both have `hooks == []` and `turn_hooks == []`. `to_dict()` succeeds on both, without `UnserializableHookError`. If a hook is ever attached, the assertion message names it.
- **Early exit leaves the agent reusable**: this test is written only if Step 1 finds an engine path that breaks or returns out of `agent.run()`. Given the findings above, none is expected, so the test is omitted and the omission is noted.

## Error paths

- Cancellation writes no checkpoint row, because `_run`'s `finally` handles only `stop.unregister` and `current_run.reset`. The tests assert that no row appears after the last `turn` row.
- `StopSignal` registration is undone on cancel: `stop.unregister(agent)` ran, so a later trigger does not touch the dead agent.
- The name is freed even though `_run` exited by `BaseException`, through `run_subtask_async`'s `finally` → `_forget`.

## Constraints

- Do not touch `runtime/checkpoint.py` (the `Parked` class and the module-level `@hook(..., tags={"subtask"})` BEFORE_TURN/ON_PAUSE hooks) or `runtime/bridge.py`.
- Do not access `_registry` or `_items` on any pygents registry in `src/`. Tests may use registry `clear()` between cases.
- Stay out of sibling and milestone scope: the floor bump and unregister swap belong to 59fd7f22, the spec/CLAUDE.md rule retirement to 4f31e025, and exactly-once phases (232cbd44), live control (eb5db173) and multiple `am` processes (2db2a2ef) to their own issues. No pygents changes.

## Tests

All tests are Engine tier per `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14: engine driven with a fake launcher/adapter, no real harness, in the default suite. They live in `tests/runtime/test_cancellation.py`. None goes in `tests/e2e/`, which is reserved for the opt-in real-harness tests.

| Test | Tier |
|---|---|
| cancel mid-agent-phase: CancelledError propagates, agent not running / name free, turn `CANCELLED`, fake process killed, latest checkpoint `turn` and resumable to `done` | Engine (`tests/runtime/`) |
| cancel mid-step-phase: same, without the process-kill assertion unless the step spawns via the bridge | Engine (`tests/runtime/`) |
| cancel with a `StopSignal` registered: agent unregistered from the signal, no `parked` row | Engine (`tests/runtime/`) |
| fresh engine-built agent: `hooks == []`, `turn_hooks == []`, `to_dict()` succeeds | Engine (`tests/runtime/`) |
| resume-rebuilt agent (`Agent.from_dict`): `hooks == []`, `turn_hooks == []`, `to_dict()` succeeds | Engine (`tests/runtime/`) |
| early-exit path leaves agent reusable, only if Step 1 finds such a path (expected: omitted) | Engine (`tests/runtime/`) |

Verification: `uv run pytest` green, and `grep -rn "_registry\|_items" src/agent_manager | grep -i -E "AgentRegistry|ToolRegistry|HookRegistry"` returns nothing.
