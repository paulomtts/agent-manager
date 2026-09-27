# Raise the pygents floor and free agent names with unregister (card 59fd7f22)

Parent story: dd4a87d5 "Adopt pygents 0.7.0" (milestone b75742dd). This subtask narrows decisions A1 and A2 of `docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md` (plan Task 1.1 in `docs/superpowers/plans/2026-09-25-pygents-070-adoption.md`). Nothing new is designed here.

## Scope

- **A1: the pygents floor.** `pyproject.toml` declares `"pygents>=0.6.7"`. Raise it to `"pygents>=0.7.0"` with `uv add "pygents>=0.7.0"` so that `pyproject.toml` and `uv.lock` both change. `uv.lock` already resolves pygents 0.7.0, so the only stale part is the declared floor. The resolved version must not change.
- **A2: public unregister.** In `src/agent_manager/runtime/engine.py`, change the body of `_forget(name)` from `AgentRegistry._registry.pop(name, None)` to `AgentRegistry.unregister(name)` wrapped in `contextlib.suppress(UnregisteredAgentError)`. `UnregisteredAgentError` is imported from `pygents.errors`. Suppressing the error is required because the name can legitimately be missing. One example is resuming a checkpoint whose agent was never registered in this process. Rewrite the docstring so it no longer describes the pre-0.7 workaround.
- Keep both call sites exactly as they are. The first, before `Agent.from_dict` on resume, clears a stale name that a run which died before its `finally` may have left behind. The second, in the `finally` after `_run`, frees the agent's own name for reuse.
- **`runtime/compile.py`: no change.** The card listed it only in case it drops tools from the registry. It does not. Nothing in it pops or deletes from `ToolRegistry`, and `clear_cache()` clears only the module's own `_CACHE`. Following the plan's Global Constraint, this subtask records that the workaround does not exist there and leaves the file untouched.
- **Test consistency.** Two assertions in `tests/runtime/test_resume.py`, at roughly lines 406 and 431, read `AgentRegistry._registry` directly (`... not in AgentRegistry._registry`). Change them to the public idiom `with pytest.raises(UnregisteredAgentError): AgentRegistry.get(name)`. Leave `test_resuming_twice_in_one_process` unchanged, and it must still pass. Tests that call a registry's public `.clear()` between cases stay as they are, as A2 allows (for example `tests/runtime/conftest.py`).

## Observable behavior

- After a run finishes, by any exit path that reaches the `finally`, its agent name is free, so a new `Agent` with the same name can be constructed or restored in the same process.
- Resuming a checkpoint whose agent name is not registered does not raise. Neither `_forget` call ever raises `UnregisteredAgentError`.
- Nothing under `src/agent_manager` touches private state on `AgentRegistry`, `ToolRegistry` or `HookRegistry`.

## Error paths

- `_forget` on an absent name: `UnregisteredAgentError`, a `KeyError` subclass, is suppressed. No other exception type is suppressed.

## Tests

All three new tests go in the new file `tests/runtime/test_engine_registry.py`. Under the placement rule in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14, these are **engine-tier tests: engine driven with a fake adapter or fake steps**, and the repo keeps that tier in `tests/runtime/`, mirroring `src/agent_manager/runtime/`. They do not belong in `tests/e2e/`, which is kept for the single real-harness opt-in test, or in top-level `tests/test_*.py`.

1. `test_a_finished_run_frees_its_agent_name` (engine tier, `tests/runtime/`): run a subtask to completion with fake steps. Afterwards `AgentRegistry.get(name)` raises `UnregisteredAgentError`, and a second run reusing the name succeeds.
2. `test_cleanup_of_an_agent_that_was_never_registered_does_not_raise` (engine tier, `tests/runtime/`; Review Focus 1): `_forget` on a name that is not registered returns without error. This covers the resume-from-checkpoint case.
3. `test_no_private_registry_access_in_src` (engine tier, `tests/runtime/`, a static guard over `src/`): the regex `(AgentRegistry|ToolRegistry|HookRegistry)\._` matches nothing under `src/agent_manager`.

Edited tests in `tests/runtime/test_resume.py` (engine tier, `tests/runtime/`):

- The two `._registry` membership assertions switch to the `pytest.raises(UnregisteredAgentError)` / `AgentRegistry.get` idiom.
- `test_resuming_twice_in_one_process` must keep passing unmodified.

## Out of scope

- The run-loop cancellation and early-exit guards, `tests/runtime/test_cancellation.py`, and the no-instance-hooks test (A3/A5). These belong to sibling 0822234f.
- Edits to the spec and addendum docs and to `CLAUDE.md` (A4). These belong to sibling 4f31e025.
- The M6 process-tree kill in `runtime/bridge.py`, the global `@hook(..., tags={"subtask"})` hooks in `runtime/checkpoint.py`, exactly-once phases, live control, several `am` processes, and any change to pygents itself.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none
