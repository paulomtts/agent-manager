# Make StopSignal the only stop (card dfc86724)

Parent story: 92c0ab94 "The supervisor". Blocked by 8eca88e2 (done). Narrows supervisor-tree design `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §T5 (lines 76-80) and its file-change table (line 132, "`stop: StopSignal | None` replaces `should_stop`") to one subtask. This is plan Task 3.3.

## Scope

Delete the M6 cooperative stop, `should_stop`, everywhere. After this card, the only way to stop a subtask is a `StopSignal` (`runtime/stop.py`), which pauses the agent, and the `ON_PAUSE` hook (`runtime/checkpoint.py:on_pause`), which saves `parked` and raises `Parked`. `runtime/stop.py` and `on_pause` are already done and stay as they are.

Source changes (all under `src/agent_manager/`):

- `runtime/engine.py`: drop the `should_stop` parameter from `run_subtask` and `run_subtask_async`. Drop it from the `RunDeps(...)` construction too; that call is positional (`workflow, store, story_id, subtask, agent_runner, clock, should_stop, stop=stop`), so check the remaining arguments still bind to the right fields. Remove the docstring line "`should_stop` (M6, until Task 3.3) is asked before every turn."
- `runtime/state.py`: remove the `RunDeps.should_stop` field. Rewrite the `stop` field's docstring so it no longer talks about `should_stop` or Task 3.3.
- `runtime/checkpoint.py`: in `before_turn`, remove only the `if deps.should_stop ...: save(agent, "parked"); raise Parked(head)` branch. `save(agent, "turn")` must still run on every turn. If `head` is no longer used after that, drop it, and drop `snapshot` too if nothing else in the function still reads it. Rewrite the module docstring so it describes one stop: the paragraph that begins "There are two stops until Task 3.3" goes, and so does the opening paragraph's claim that `before_turn` reads the stop. `ON_PAUSE` is the only parking point.
- `runtime/engine.py`: in `run_subtask_async`'s docstring, the sentence "Two stops park the subtask: ..." introduces the two bulleted paragraphs below it; once the `should_stop` bullet is removed (see below), rewrite that sentence too so it no longer says "Two stops" over what is now a single bulleted paragraph about `stop`.
- `cli.py`: remove `should_stop` from `drive_subtask_async` (the parameter and the `"should_stop"` key in the `walk` dict) and from `drive_subtask` (the parameter and where it is forwarded). Update both docstrings. The sync `drive_subtask` then has no stop at all. That is intended: the spec lists its parameters without adding a `stop`, and callers that need to stop await `drive_subtask_async(stop=...)`.
- `orchestrate.py`: remove `should_stop` from the `Driver` Protocol's `__call__` signature (around line 286), and remove the docstring sentence "`should_stop` stays until Task 3.3 deletes it; the lane never passes it." **Note for the planner:** the card's "Files" list leaves out `orchestrate.py`, but the code says this deletion belongs to Task 3.3. It only removes the parameter. Do not touch scheduling (owned by c0dbd454) or base-building (owned by 8eca88e2).
- Remove any `Callable` imports that no longer have a use.

Done when: `grep -rn "should_stop" src tests` finds nothing, and `uv run pytest` passes in full, including `tests/e2e`.

## Observable behaviour

- A stop still gives a `stopped` summary with the detail `"stopped before <next phase>"`, a `parked` checkpoint whose queue head is that phase, and a subtask row with status `stopped`. The difference is that the stop now arrives only through `StopSignal.trigger` or through a `register` on a signal that has already fired.
- Every turn still writes a `turn` checkpoint before it runs. A run with no `stop`, or with one that is never triggered, behaves exactly as today.
- Passing `should_stop=` to `run_subtask`, `run_subtask_async`, `drive_subtask`, `drive_subtask_async`, or `RunDeps` now raises `TypeError`, because the keyword no longer exists. No compatibility shim is kept.

## Error paths

- If a phase escalates in the same turn that a stop is triggered, the escalation still wins, as it does today with `should_stop`.
- An error raised outside a phase still writes an `escalated` checkpoint row through the engine's generic `except Exception` branch.
- No new errors are introduced.

## Tests

The tier rule comes from `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14. Engine- and runtime-level code is tested against temporary stores, repos and boards with canned fakes, in the default suite. Only a real harness goes in `tests/e2e/`. None of the tests below starts a real harness, so none of them belongs in `tests/e2e/`. Each change goes into the existing test file that already covers the code. Tests that set `should_stop=` either switch to a `StopSignal` or are deleted when a `StopSignal` test already covers the same thing. No test may sleep to get ordering: trigger the signal from inside a canned step or fake runner, or block on an `asyncio.Event`.

- `tests/test_engine.py` (engine tier, temp store): the stop block at lines 2318-2622.
  - Replace `_StopFlag` with a `StopSignal` that canned steps `trigger(...)`.
  - Port to it: `test_a_stop_requested_during_phase_three_stops_before_phase_four`, `test_a_stop_already_requested_runs_no_phase_at_all`, `test_a_stop_before_a_deterministic_phase_leaves_it_unstarted`, `test_a_stop_before_an_agent_phase_leaves_the_runner_uncalled`, `test_a_stop_before_an_agent_phase_wins_over_a_missing_runner`, `test_an_escalation_during_the_stop_request_wins_over_the_stop`. The last two may use a pre-triggered signal.
  - Rename `test_a_should_stop_that_never_fires_changes_nothing` so it uses an untriggered `StopSignal`.
  - Delete `test_should_stop_is_checked_only_at_visited_phases`. It tests the polling mechanism itself, which no longer exists.
- `tests/runtime/test_stop_bridge.py` (runtime tier, temp store): delete the two M6 tests at lines 70-126. `test_a_trigger_from_one_subtask_parks_another_mid_phase` and `test_a_stop_triggered_before_the_run_parks_before_the_first_phase` already cover the same things with `StopSignal`. Port the `should_stop=lambda: ran == ["a"]` call around line 351 to a `StopSignal` that step `a` triggers.
- `tests/runtime/test_checkpoint.py` (runtime tier):
  - Add a test that `before_turn` saves a `turn` row for every turn even when a `StopSignal` is triggered mid-run. The expected rows are `turn`, `turn`, …, then `parked` from `on_pause`, with no `parked` written by `before_turn`.
  - Rewrite `test_an_error_outside_a_phase_writes_an_escalated_row`. It currently makes the error by having `should_stop` raise. Raise it from another source that sits outside any phase (for example, a store wrapper whose second `save_checkpoint` raises). Keep its assertions: `ran == ["a"]`, `escalated`, and rows `(0, "turn"), (1, "escalated")`.
- `tests/runtime/test_resume.py` (runtime tier): the six `should_stop=` calls (lines 211, 301, 304, 322, 345, 393) switch to a `StopSignal` triggered by step `a`, or already triggered for the resume at line 304. The parked checkpoint and resume assertions stay as they are.
- `tests/test_cli.py` (CLI tier: temp git repo and brd board, fake runner):
  - `test_drive_subtask_hands_should_stop_to_the_engine` becomes a test that `drive_subtask_async` hands a pre-triggered `StopSignal` to the engine. Driven through `asyncio.run`, it keeps the result "stopped before worktree", with no runner call and no worktree created.
  - `test_drive_subtask_walks_task_with_the_same_arguments` drops the `should_stop` argument and the `"should_stop"` expected key, keeping `"stop": None`.
  - The expected kwargs around line 1679 drop `"should_stop": None`.
  - The `_park_pygents` helper (around line 4405; despite the similarly named `_crash_pygents` nearby, this is the one that calls `drive_subtask` with `should_stop=`) switches to `drive_subtask_async` with a `StopSignal` that the fake runner triggers once it has seen `spec`. It still asserts "stopped before validate_spec".
- `tests/test_orchestrate.py` (orchestrate tier, fake drivers):
  - Fake drivers drop their `should_stop=None` parameter and the recorded `"should_stop"` key (around lines 664, 680, 994).
  - The test around line 1193 stops asserting `calls[0]["should_stop"] is None`. It asserts instead that the lane passes no `should_stop` keyword, and its docstring stops mentioning Task 3.3.

Out of scope: `runtime/stop.py`, `on_pause`, the scheduling and base-building logic in `orchestrate.py` and `bases.py`, and everything in the addendum's §10 (live `am pause/cancel/watch/retry`, verification discovery, grafo `max_workers`, a676f178).
