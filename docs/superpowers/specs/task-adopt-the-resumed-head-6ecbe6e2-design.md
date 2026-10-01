# Adopt the resumed head in the compiled agent phase (subtask 6ecbe6e2)

Parent story fd673816 ("Adoption: a resumed agent phase reuses its recorded result"), milestone 4e0d2a6c. Task 2.2 of `docs/superpowers/plans/2026-09-27-exactly-once.md`; design `docs/superpowers/specs/2026-09-27-exactly-once-design.md` E8 (also E9, E11). Both docs live only in commit 6ab5618 (`git show 6ab5618:<path>`), not on master's tree.

## Base and prerequisites

Branch prefix `m11`, based on master (milestones 9 and 10 merged) with the two prerequisite branches merged in, since their code is not on master HEAD:

- `m11/task-compute-the-floor-at-94088f7e` (Task 1.2): `runtime/state.py` `Adoption(phase, loop, source_run, floor)`, `RunDeps.adopt`, `RunDeps.take_adoption(phase, loop)` (returns the carried adoption only on a `(phase, loop)` match and always clears it).
- `m11/task-let-the-agent-runner-4aed8212` (Task 2.1): `dispatch.Adopted(result, attempt, source_run)`, `dispatch.read_result`, `AgentRunner.adopt(phase, context, *, source_run, floor) -> Adopted | None`, `Store.replay_journal`.

Names above were read from those worktrees; the implementer must re-read the code as built after merging and adapt, and say so in the result. Nothing is pushed; the base branch never moves.

## Scope

Owned files: `src/agent_manager/runtime/compile.py` and the new `tests/runtime/test_exactly_once.py`. Do not modify `dispatch.py`, `store.py`, or `runtime/state.py`; only consume their interfaces.

## Behavior

`agent_phase` (compile.py:130-161 on master):

- After `table = context.binding_table(...)` and `rendered = prompt.render_prompt(...)`, call `deps.take_adoption(phase, loop)`.
- If it returns an `Adoption` and `getattr(deps.agent_runner, "adopt", None)` is not `None`, await `bridge.call_step(adopt, {"phase": p, "context": table, "source_run": a.source_run, "floor": a.floor})` inside the existing `try` (the block that today holds `result = await bridge.call_agent(...)`), so an unexpected error from adopt escalates through the same `except Exception -> Escalated(phase, ...)` path as a dispatch error.
- A non-`None` `Adopted` supplies `result = adopted.result` and `bridge.call_agent` is skipped. `None` (nothing to adopt, or a decline) falls through to `bridge.call_agent` as today.
- A runner without `adopt` (e.g. a plain callable/fake) dispatches as today; the adoption is still consumed.
- Everything after the `try` is unchanged: the `ContextItem` yield, successor turn, `fresh_loop_after` handling, and `on_fail` handling of `AgentPhaseFailed` from a dispatch.

`step_phase` (compile.py:163-196): call `deps.take_adoption(phase, loop)` on entry and discard the result, so no adoption outlives the first turn after resume. Steps are never adopted and stay at-least-once (E9).

Observable surface: the only user-visible effect of an adoption is the one warning line `AgentRunner.adopt` already appends (naming the phase, attempt, and source run id). CLI envelope and exit codes unchanged. No schema changes, no new checkpoint write points (BEFORE_TURN stays the only one).

## Error paths

- `adopt` raises an unexpected exception: escalated as `Escalated(phase, walk._render_error(error))`, the same as a dispatch error.
- `adopt` declines (result file invalid, gates fail, journal unreadable): returns `None` with its own warning; the phase dispatches again normally.
- Adoption for a different `(phase, loop)` than the first turn run: discarded by `take_adoption`; the phase dispatches.
- Missing agent runner: the existing `EngineError` before anything runs, unchanged.

## Tests

All in `tests/runtime/test_exactly_once.py`. Tier, per the design spec section 14 "Testing" placement rule: the Engine tier (engine driven with an injected fake launcher returning canned results, including crash-mid-phase, to prove resume) — which is where `tests/runtime` sits; the exactly-once design's section 7 names this file for the crash-window/dispatch-count tests. Not the e2e tier (the fake-claude kill-and-resume test in `tests/e2e/test_exactly_once.py` is out of scope).

Fixture: real `AgentRunner` + a counting `FakeLauncher` + real store/journal + real pygents agents; workflow `w`(step) -> `a`(agent) -> `b`(agent) -> `z`(step); a `_dispatches(launcher)` helper counting launcher calls per phase. Crashes are a plain `BaseException` subclass, mirroring `_Crash` (tests/runtime/test_resume.py:42). There is no reusable "arm" helper in this repo (the exactly-once plan's pseudocode names one, but it was never built): raise it the way `test_resume.py` already does, inline in a fake runner/step/launcher or via `monkeypatch.setattr` wrapping the target call (e.g. the inline `raise _Crash(...)` in `_five`'s step closures at test_resume.py:89-90, or the fake-runner crash points in `_crash_on_the_second_spec` at test_resume.py:573-584) -- pick whichever shape fits each test's crash point (mid-`_record_phase`, mid-`bridge.call_agent`, mid-launcher, mid-`run_one_step`). No sleeps.

1. W1: crash after `a`'s `done` row -> resume does not dispatch `a` again; `b` dispatched once.
2. W2: crash after `call_agent` returns for `a` (before the next checkpoint) -> resume adopts `a` (one dispatch total), one warning line.
3. W2 at the last agent phase (`b`) -> adopted, `z` runs, subtask completes, `b` dispatched once.
4. Double crash: crash again after resume but before the adopted phase finishes -> second resume still adopts; `a` dispatched once overall.
5. W0: crash mid-dispatch with a valid `result.json` on disk -> dispatches again; attempt statuses `[(1, "harness_error"), (2, "ok")]`.
6. Goto-looped phase: a phase revisited across loop iterations dispatches each iteration and is never adopted.
7. Relaunch across runs: resume in a new run adopts once from the earlier run; the warning names the source run id.
8. Step caught in the window: crash after a step's work but before its checkpoint -> the step reruns (at-least-once, E9).
9. Checkpoint with no floor row (pre-milestone degradation, E11) -> dispatches again, no error.
10. Parked subtask: `StopSignal` mid-`a` -> resume dispatches `b` exactly once.

## Verification

`uv run pytest` (full suite). No typecheck, no lint.
