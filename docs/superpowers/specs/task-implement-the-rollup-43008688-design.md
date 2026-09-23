# Subtask 43008688 — Implement the `rollup.set_status` step

Parent story: 360cd141 "Close the seams the wiring test found" (milestone 7aa00a90). Siblings f68d29e2 and be376902 are done; this card is the last of the three. This spec narrows the already-agreed milestone design (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`, plus the real-harness addendum `docs/superpowers/specs/2026-09-23-real-harness-design.md`) to one module and its tests. No design decisions are reopened here.

## Scope

Two source files and two test files.

1. NEW `src/agent_manager/steps/rollup.py` — a deterministic step that writes one card's board status through `board.set_status`.
2. `src/agent_manager/workflow/registry.py` — bind the name `rollup.set_status` to the real callable instead of the placeholder.
3. NEW `tests/steps/test_rollup.py` — Steps-tier tests against a real temporary brd board.
4. `tests/workflow/test_registry.py` — move `rollup.set_status` from the placeholder assertions to the real-callable assertions.

Out of scope, explicitly: ancestor roll-up (parent story/milestone status propagation — deferred, real-harness addendum §4), milestone orchestration, parallel stories, `integrate`, non-Claude harnesses, and anything owned by the two done siblings. Do **not** touch `workflow/builtin/task.yaml` (phase ordering is f68d29e2's), `results.py` or `dispatch.py` (be376902's). `critic_blockers_gate` is already a real reducer (`reducers.critic_blockers_gate`) and is not touched.

## The step

`rollup.set_status` is called by the two `best_effort: true` deterministic phases of `workflow/builtin/task.yaml`: `mark_in_progress` (`args: { status: in_progress }`) and the terminal `mark_done` (`args: { status: done }`). Everything else it needs comes from the engine context.

Observable behaviour:

- It delegates to `board.set_status(card_id, status, repo_dir=repo_dir)` (`src/agent_manager/board.py:198-216`), which runs `brd update <id> --status <status>` and returns a validated `models.Card`.
- It returns a small plain mapping describing what was written — the card id and the status brd reports back on the returned `Card` (not the status that was requested; the truth is what brd stored). A `dict[str, object]`, matching the shape the other deterministic steps return (`worktree.ensure`, `plan_check.find_validated_plan`). No pydantic model: the convention reserves those for process-boundary results, and this value never leaves the process as a schema.
- It is read-through-free and cacheless: no `board.show` before or after the write, no journaling, no ancestor lookup.
- Idempotency is inherited, not implemented. `brd update --status` stores the value it is given, so a repeated identical call is another successful write of the same value; resume re-runs whole phases and that must stay harmless. The step adds no "already in this status" short-circuit.

### Binding contract (the gotcha)

The engine binds step arguments **by parameter name** from the run context, overlaid with the document's `args` (`src/agent_manager/engine.py:153-202`, `bind_arguments`). The context key holding the card id is `card`, a bare id string (`engine.py:89-98`; the full `models.Card` lives under `card_details`). There is no `card_id` key, and `bind_arguments` raises `EngineError` for any declared parameter with no default that is not in the supplied mapping.

Therefore the step's first parameter must be named `card`, even though the card text writes the signature as `set_status(card_id, status, repo_dir)`. `status` arrives from the document's `args`; `repo_dir` from the context (`engine.py:96`). No positional-only parameters — `bind_arguments` rejects them outright. The `card_id` naming in the card text is descriptive of intent, not of the parameter name; the binding rule wins.

### Error paths

- `board.BoardError` — brd missing, brd non-zero with no envelope, an `{"ok": false}` envelope (including a nonexistent card id), non-JSON output, or a payload that fails `models.Card` validation. The step **does not catch it**. Tolerance is the engine's policy: both call sites are `best_effort: true`, so the engine records the warning and the run continues. Swallowing it here would make a board-write failure invisible.
- No other exception type is introduced by this module. It defines no error class of its own.

## Registry change

In `src/agent_manager/workflow/registry.py`:

- Add `rollup` to the steps import (line 19, currently `from agent_manager.steps import plan_check, reducers, verify, worktree`).
- Replace the `_placeholder("rollup.set_status", ...)` registration (lines 222-225) with `registry.register("rollup.set_status", rollup.set_status)`, alongside the other real steps, so `default_registry().resolve("rollup.set_status") is rollup.set_status`.
- `critic_blockers_gate` is already registered as the real reducer; leave it. After this change no placeholder registrations remain; leave the `_placeholder` helper (registry.py:151) in place, unused, rather than widening scope.
- Correct the now-stale prose: the `default_registry` docstring says "`steps/rollup.py` does not exist" and counts "four implemented steps" / "one name still has no implementation"; the section comment says "Owned by siblings" and the registration sits in a separate block. Update to the post-change reality (six reducers, five implemented steps, no placeholders) and move the rollup registration into the deterministic-steps block.
- `BUILTIN_FUNCTION_NAMES` is unchanged — the name was always listed.

## Tests

Tier assignment per the test-placement rule, design spec §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`): pure functions unit-tested; **Steps tested against temporary git repos and a temporary brd board, no network**; adapters via pure `build_command` and an injected launcher; engine via a fake adapter; one opt-in slow end-to-end. `rollup.set_status` is a deterministic Steps component whose entire behaviour is a brd write, so its tests are **Steps tier** and run against a real temporary brd board — brd is not mocked, and neither is `board.set_status`.

New file `tests/steps/test_rollup.py`, with a module docstring in the style of `tests/steps/test_verify.py` stating the placement and citing §14. `tests/steps/` has no `conftest.py` (`test_verify.py` defines its own `requires_git` marker locally), so lift the brd helpers from `tests/test_board.py` into this module: the `requires_brd` skipif marker (line 18), the `temp_board` fixture (lines 237-257: `XDG_DATA_HOME` pointed at `tmp_path`, `brd init --name temp-board` in a fresh directory), `_add_card` (line 260) and `_brd_json` (line 270).

Steps tier, `tests/steps/test_rollup.py`:

1. **The card's status really changes.** Add a card to the temp board, call the step with `status="in_progress"`, and assert the stored status via `brd show` JSON (`_brd_json`) — not only the returned mapping. Also assert the returned mapping reports the card id and the new status.
2. **A second identical call is a no-op that does not raise.** Call the step twice with the same status; the second call returns the same mapping and `brd show` still reports that status. This is the resume/`best_effort` guarantee the board module documents at `board.py:207-211`.
3. **A nonexistent card id raises `board.BoardError`.** `pytest.raises(board.BoardError)` around a call with an id that was never added, proving the step does not swallow the adapter's failure.

Unit tier (no board needed) — optional but worthwhile as the wiring-level check the card asks for, in the same file or in `tests/workflow/test_registry.py`:

4. **The engine can bind the document's arguments to this function.** Call `engine.bind_arguments(rollup.set_status, <a context containing `card` and `repo_dir`>, {"status": "done"}, ...)` and assert the bound keyword mapping is complete — this is the regression guard for the `card` vs `card_id` parameter-name trap, and it is a pure-function check on `bind_arguments`, so it needs no board.

Unit tier, `tests/workflow/test_registry.py`:

5. Extend `test_default_registry_resolves_implemented_steps_to_the_real_callables` (line 108) with `registry.resolve("rollup.set_status") is rollup.set_status`, importing `rollup`.
6. Delete `test_the_one_remaining_placeholder_resolves_and_raises_when_called` (lines 123-128): it asserts `rollup.set_status` raises `NotImplementedError`, which is no longer true, and no other placeholder remains. `critic_blockers_gate` is already covered as real code (lines 111 and 131) and needs no change.
7. The name list at lines 78-91 and `test_default_registry_holds_exactly_the_names_task_yaml_uses` are unchanged.

`tests/workflow/test_builtin_task.py:60` (asserting the rollup phases are `best_effort`) and `tests/test_engine.py` (which uses its own fake `set_status`) need no change.

## Verification

`uv run pytest` (full suite). No typecheck step, no lint step.
