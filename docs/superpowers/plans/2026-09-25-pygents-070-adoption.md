# Adopting pygents 0.7.0 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Each task is one `brd` subtask card driven by the `task` workflow; the card's description names its Task and carries its excerpt.

**Goal:** Replace milestone 6/7's pygents workarounds with the fixes shipped in pygents 0.7.0, raise the floor, and pin the fixed behaviour with tests here.

**Architecture:** No new components. Registry edits go through `unregister`; cancellation behaviour is asserted instead of worked around; two constraint rules in the M6/M7 addenda are retired.

**Tech Stack:** Python 3.12, `pygents>=0.7.0`, pytest (+ pytest-asyncio, added in M6), `uv`.

**Spec:** `docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md` (A1–A5).

## Global Constraints

- **Read the code as built first.** M6 and M7 may have implemented a workaround differently from their plans. Each task starts by locating the real code (the grep commands in the task) and adapts to it; if a workaround described here does not exist, the task says so in its result and changes nothing for it.
- Verification for every task: `uv run pytest` green (the whole suite, including `tests/e2e`).
- No `_registry`/`_items` access to pygents registries in `src/` after Task 1.1.
- M6's process-tree kill in `runtime/bridge.py` and the global, module-level `@hook(..., tags={"subtask"})` hooks stay (spec §2).
- Branch prefix `m8`. Base: `master` with milestone 7 merged.

## Review Focus

1. **`unregister` of a name that is not registered** (a run that crashed before its agent was built, or a second cleanup). Must not raise out of the engine: catch only `UnregisteredAgentError`. Test in Task 1.1.
2. **Resuming the same card twice in one process** after the change. Still no `ValueError`. Test in Task 1.1 (M6's Review Focus 4 test must still pass unchanged).
3. **Cancellation while a deterministic step is running** (a `to_thread` worker, no `claude -p`). The agent ends not running and the step's turn `CANCELLED`; the worker thread finishes on its own. Test in Task 1.2.
4. **A subtask agent restored from a checkpoint** (`Agent.from_dict`) carries no instance hooks either. Test in Task 1.2 (A5).
5. **An engine path that stops reading `run()` early** after A4 simplification (e.g. on `Parked`). The agent is reusable and no loop error is logged. Test in Task 1.2 if such a path exists.

---

## Story 1 — Adopt pygents 0.7.0

### Task 1.1: Raise the floor and remove agents with `unregister`

**Files:** `pyproject.toml`, `uv.lock`, `src/agent_manager/runtime/engine.py` (and `runtime/compile.py` if it drops tools). Test: `tests/runtime/test_resume.py`, `tests/runtime/test_engine_registry.py` (new).

**Interfaces:** consumes `pygents.AgentRegistry.unregister(name: str) -> None` (raises `pygents.errors.UnregisteredAgentError` for an unknown name), same on `ToolRegistry`.

- [ ] **Step 1: Locate** — `grep -rn "_registry\|_items\|_forget\|AgentRegistry\|ToolRegistry" src/agent_manager` and read each hit.
- [ ] **Step 2: Failing tests**

```python
def test_a_finished_run_frees_its_agent_name(tmp_path):
    summary = run_subtask(WORKFLOW, store, ...)            # fake steps, as in tests/runtime
    assert summary.status == "done"
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(f"{run_id}:{card_id}")          # the name is free again

def test_cleanup_of_an_agent_that_was_never_registered_does_not_raise(): ...   # Review Focus 1
def test_no_private_registry_access_in_src():
    hits = subprocess.run(["grep", "-rnE", r"(AgentRegistry|ToolRegistry|HookRegistry)\._", "src/agent_manager"],
                          capture_output=True, text=True).stdout
    assert hits == ""
```

M6's `test_resuming_twice_in_one_process` (Review Focus 2) stays and must pass unchanged.
- [ ] **Step 3:** `uv add "pygents>=0.7.0"`; replace each private-dict removal with `AgentRegistry.unregister(name)` inside `with contextlib.suppress(UnregisteredAgentError)` where the name may be absent, plain otherwise.
- [ ] **Step 4:** `uv run pytest` green.  **Step 5: Commit** — `git commit -m "refactor(runtime): pygents 0.7.0; free agent names with unregister"`

### Task 1.2: Pin pygents' cancellation behaviour and drop defensive code

**Files:** `src/agent_manager/runtime/engine.py`, `src/agent_manager/orchestrate.py` (only if they hold defensive code), `tests/runtime/test_cancellation.py` (new).

- [ ] **Step 1: Locate** — read `runtime/engine.py`'s run loop and `orchestrate.py`'s lane: note any code that exists only because pre-0.7 pygents mishandled early exit or cancellation (for example, a manual `_is_running` reset, a guard around `run()` closing, a "consume fully even after Parked" loop). List it in the commit message.
- [ ] **Step 2: Failing tests** (real pygents agents, fake launcher, temporary store)

```python
async def test_cancelling_mid_agent_phase_leaves_a_clean_agent_and_a_resumable_checkpoint():
    started = asyncio.Event()
    def runner(phase, context, rendered):                  # blocks in a to_thread worker like claude -p
        loop.call_soon_threadsafe(started.set); return fake_launcher_blocking(...)
    task = asyncio.create_task(run_subtask_async(WORKFLOW, store, ..., agent_runner=runner))
    await started.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not agent_named(run_id, card_id)._is_running                    # or: the name is free (Task 1.1)
    assert last_turn_of(...).metadata.stop_reason is StopReason.CANCELLED
    assert fake_process_was_killed()                                        # M6 bridge, unchanged
    assert store.latest_checkpoint(card_id).reason == "turn"                # resume point intact

async def test_cancelling_a_deterministic_step(): ...          # Review Focus 3
def test_engine_agents_carry_no_instance_hooks(): ...           # A5: fresh and from_dict-restored agents: hooks == [], turn_hooks == []
async def test_an_early_exit_path_leaves_the_agent_reusable(): ...   # Review Focus 5, only if Step 1 found such a path
```

- [ ] **Step 3:** Remove the defensive code found in Step 1, keeping the tests green.
- [ ] **Step 4:** `uv run pytest` green.  **Step 5: Commit** — `git commit -m "test(runtime): pin pygents 0.7.0 cancellation; drop pre-0.7 guards"`

### Task 1.3: Retire the workaround rules in the addenda

**Files:** `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`, `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, `CLAUDE.md` (only if it carries either rule).

- [ ] **Step 1:** `grep -n "break or return out\|never register a pygents hook as a closure\|closure hooks\|SafeExecutionError\|AgentRegistry\|upstream pygents" docs/superpowers/specs/2026-09-25-*.md CLAUDE.md`.
- [ ] **Step 2:** In M6's addendum: §2's bullets on early exit, closure hooks and `AFTER_TURN` each gain "Fixed in pygents 0.7.0; see the adoption addendum."; §11's "Upstream pygents fixes" item is marked done (pygents 0.7.0). In both addenda, rules that forbid breaking out of `run()` or closure hooks are replaced by one line: "pygents ≥0.7.0 makes this safe; the engine still consumes `run()` and keeps global module-level hooks by design." Do not rewrite anything else.
- [ ] **Step 3:** `uv run pytest` green (docs-only; the suite proves nothing broke).  **Step 4: Commit** — `git commit -m "docs: retire the pre-0.7 pygents workaround rules"`
