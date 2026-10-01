# Let the agent runner adopt a recorded attempt (card 4aed8212)

Parent: fd673816 "Adoption: a resumed agent phase reuses its recorded result" (milestone 4e0d2a6c, exactly-once). Source: plan `docs/superpowers/plans/2026-09-27-exactly-once.md`, Task 2.1. Branch prefix `m11`, based on master (milestones 9 and 10 merged). Nothing is pushed and the base branch does not move.

## Scope

Files: `src/agent_manager/dispatch.py` and `src/agent_manager/store.py` (only `Store.replay_journal` and `Journal.read`'s new keyword). Tests: `tests/test_dispatch.py` and `tests/test_store.py`.

Out of scope: `src/agent_manager/runtime/compile.py`, `deps.take_adoption`, the `bridge.call_step(adopt, ...)` wiring, and `tests/runtime/test_exactly_once.py`. These belong to sibling 6ecbe6e2, together with every crash-window (W0/W1/W2), Goto-loop, cross-run relaunch, parked-subtask and checkpoint-floor integration test. Steps are not adopted and stay at-least-once. No table gains a column, no CHECK constraint changes, and `checkpoint_floors` is not touched. The CLI envelope and exit codes do not change. `dispatch.py` must not import pygents.

## Deliverables

1. **`dispatch.read_result(result_path: Path | None, model: type[BaseModel] | None) -> Verdict`.** This is the file half of `classify` (dispatch.py:202-259), moved verbatim:
   - `model is None` returns `Verdict("ok", result=None)`.
   - A missing file returns `harness_error`.
   - A UTF-8, JSON or schema failure returns `schema_invalid`.
   - Otherwise it returns `ok` with the JSON-mode dump.

   `classify` keeps its exit-status and timeout checks (lines 227-233) and then delegates to `read_result`. Every existing `classify` test passes unchanged.
2. **`Adopted`.** A plain frozen dataclass: `@dataclass(frozen=True) class Adopted: result: Any; attempt: int; source_run: str`.
3. **`AgentRunner._result_model(phase)`.** The result-model resolution, factored out of `__call__` (lines 428-435). Both `__call__` and `adopt` use it.
4. **`AgentRunner.adopt(self, phase: AgentPhase, context: Mapping[str, Any], *, source_run: str, floor: int) -> Adopted | None`.** It takes and returns plain values and runs these steps in order:
   1. `model = self._result_model(phase)`.
   2. `run = self.store.replay_journal(source_run)`. If this raises `JournalError` (any subclass) or `pydantic.ValidationError`, decline.
   3. Find the subtask whose `card_id == self.card_id` among any story in `run` (the other run's story structure is not assumed to match this run's `self.story_id`), then that subtask's phase named `phase.name`. Candidates are its attempts with `n > floor` and `status == "ok"`. If the subtask or phase is not found in `run`, or there are no candidates, return `None` and add **no** warning.
   4. For the candidate with the highest `n`, call `read_result(None if model is None else attempt.result_path, model)`. If the verdict is not `ok`, decline. Then call `evaluate_gates(phase, gate_values(context, phase.name, verdict.result), self.warnings)`. If it returns a verdict (failed or fatal), decline.
   5. Call `self._record_phase(phase, "done", found.started_at or self.clock(), self.clock(), None)`, where `found` is the matched phase run. Append the warning `phase {name!r} was not dispatched again: attempt {n} of run {source_run} had already succeeded (result reused)`. Return `Adopted(verdict.result, n, source_run)`.

   To decline, append `phase {name!r}: attempt {n} of run {source_run} was not reused ({why}); dispatching again` to `self.warnings` and return `None`. A decline writes no row. `adopt` never copies an attempt row into the current run. It reads attempts only from the journal, never from the `attempts` projection table.
5. **`Store.replay_journal(run_id: str) -> models.Run`.** For its own run it reads `self._journal`. For any other run it reads `Journal(run_id)` with `ignore_torn_tail=True`. It passes the lines to the existing `replay` and returns the result. The whole call runs under `with self._lock:`. It writes nothing, so it does not need `_fenced()`.
6. **`Journal.read(*, ignore_torn_tail: bool = False)`.** When the flag is set, a last non-blank line that is not valid JSON and has no trailing newline is treated as an append still in flight and skipped. Any other non-JSON line still raises `CorruptJournalError`. A missing journal still raises `MissingJournalError`. The default behavior does not change.

## Error paths (all non-fatal; adoption only ever adds `warnings` lines)

- The source journal is missing, corrupt or fails validation: decline.
- No ok attempt has `n > floor`, including orphaned `started` attempts later marked `harness_error`: return `None` silently.
- The result file is deleted, is not JSON or fails the schema: decline, with the reason in `why`.
- A gate now fails or is fatal: decline.

## Tests

Tier rule: design spec §14, "Testing". `adopt`, `read_result`, `classify` and `replay_journal` are engine code, so they are driven directly with the counting `FakeLauncher` and canned result files. They live in the flat engine test files, not in `tests/runtime/` (pygents wiring, which belongs to the sibling) and not in `tests/e2e/` (the single real-harness test). All of the tests below are engine-tier.

`tests/test_dispatch.py` (these reuse `store`, `worktree`, `FakeLauncher`, `_runner`, `_context`, `_workflow`, and a `_succeed_once` helper):
- `test_adopt_returns_the_recorded_result_without_dispatching`: `adopt` returns `Adopted({...}, 1, RUN_ID)`, the launcher is called once, and a "not dispatched again" warning is added.
- `test_an_attempt_at_or_below_the_floor_is_not_adopted`: with `floor=1`, `adopt` returns `None` and adds no decline warning.
- `test_an_orphaned_attempt_is_never_adopted`: the launcher writes a valid result and then crashes, and `adopt` returns `None`.
- `test_a_result_that_no_longer_validates_is_declined`: parametrized over `delete`, `not_json` and `schema`. Each returns `None` and adds a "was not reused" warning.
- `test_a_gate_that_fails_now_declines`: the gate now returns `{"blocked": True}`, so `adopt` returns `None` and adds a decline warning.
- `test_adopt_reads_the_journal_not_the_projection`: after `DELETE FROM attempts`, adoption still succeeds.
- `test_adopt_reads_another_runs_journal`: a runner whose store is bound to `run-2` adopts from `RUN_ID`. `source_run == RUN_ID`, the phase status in `run-2` is `done`, and no attempt row is copied.
- `test_a_missing_source_journal_declines`: with `source_run="never-ran"`, `adopt` returns `None` and adds a decline warning.
- `test_a_phase_without_a_result_model_adopts_none`: the phase has no model, so `Adopted.result is None`.

`tests/test_store.py`:
- `test_replay_journal_of_another_run_ignores_a_torn_tail`: `{"seq": 9` is appended with no newline to `run-2`'s journal, and the replay succeeds without it. A non-JSON line elsewhere still raises `CorruptJournalError`.
- `test_replay_journal_holds_the_store_lock`: a `record_*` call blocked on the lock in another thread cannot add a line in the middle of the replay.

Existing `classify` tests must stay unchanged and green.

## Verification

`uv run pytest`: the whole suite is green, including `tests/e2e`. There is no lint or typecheck step.
