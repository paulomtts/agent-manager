# Add StoryRecorder and refactor `lane` to use it (card 7640024e)

Parent story: 0f2b1491 "One StoryRecorder instead of nine inlined state transitions". Milestone design: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` §S5. This subtask narrows §S5 to `lane` only.

## Scope

- Add a `StoryRecorder` class to `src/agent_manager/orchestrate.py`. No new module.
- Refactor `lane` (`orchestrate.py:926`) so every `store.record_story` / `store.record_subtask` write, and every `LaneOutcome` it builds, goes through one `StoryRecorder` instance created per `lane` invocation. This happens after the early returns and the `base_only_lane` delegation, at the point where `story_row, subtask_rows = plan.rows[story.id]` is read today.
- Remove the `outcome()` closure at `orchestrate.py:1014` and the closure-captured `completed` / `warnings` / `built` locals. The recorder owns these as its own state.

## Out of scope (do not touch)

- `base_only_lane` (`orchestrate.py:865`) and its `outcome()` closure at line 890 belong to sibling 8fc3628c. `lane` still delegates to it unchanged.
- `stopped_before_phase` (`orchestrate.py:76-85`) and `SubtaskSummary` belong to sibling 150613bb. `lane` still passes `stopped_before_phase(summary.detail)` as the `before_phase` argument.
- The pygents turn/phase model, the checkpoint format, the harness adapter contract, grafo, and the existing monkeypatch calls in the tests. No new typecheck command or CI gate.
- New tests. See "Tests" below.

## StoryRecorder shape

The recorder is built from `store`, the story id, `planned.level`, `story_row` and `subtask_rows`. It holds the outcome builder's state: the tuple sources `completed` and `warnings`, plus `base` (`dag.RootPlan | None`). Its four named methods follow §S5: `started()`, `subtask_done(subtask_id, tip)`, `stopped(subtask_id, before_phase)` and `escalated(subtask_id, phase, detail)`.

- Methods that end the lane return the `LaneOutcome` they built, so `lane` can write `raise LaneStopped(recorder.stopped(...))`.
- These methods may take extra keyword-only parameters, or the class may have small helpers (for example, recording that the base was built, adding warnings, the final story `done`, or building the `done` outcome). Use them only where the four calls cannot express a write that `lane` makes today. Behaviour preservation always takes precedence over the "exactly one pair per method" wording in §S5, because some of today's transitions write only the story row.
- `subtask_done` takes `tip` as the §S5 signature requires. It must not add a new write, because no row field holds a tip today.
- `stop.trigger(story.id)` stays in `lane`, immediately before the escalation writes, exactly where it is now. The recorder does no signalling, which leaves `base_only_lane`'s trigger-without-writes shape open for sibling 8fc3628c.
- Do not add base-only features, such as `level=None` or no rows, beyond what `lane` needs. Just don't design them out.

## Observable behaviour: must be byte-for-byte unchanged

For every path, the rows written, their order and their status values must match today's exactly. So must the `LaneOutcome` fields: `kind`, `story`, `level`, `subtask`, `completed`, `warnings`, `base`, `failed_phase`, `detail` and `before_phase`. Today's write map is the spec:

| Path (current line) | Writes, in order | Outcome |
|---|---|---|
| Merged root, stop already fired (1033) | story `stopped` | `stopped`, subtask = `planned.remaining[0].id` |
| `BaseFailed(stopped=True)` (1053) | story `stopped` | `stopped`, subtask None |
| `BaseFailed(stopped=False)` (1055-1056) | trigger, then story `escalated` | `escalated`, subtask None, `failed_phase="base"`, detail from error |
| Stop fired before a subtask (1064) | story `stopped` (no subtask row) | `stopped`, subtask = that subtask |
| Subtask start (1070-1072) | subtask `started`, then story `started` on position 0 only | none |
| Summary `stopped` (1103-1104) | subtask `stopped`, story `stopped` | `stopped`, subtask, `before_phase=stopped_before_phase(detail)` |
| Summary not `done` (1113-1115) | trigger, then subtask `escalated`, story `escalated` | `escalated`, subtask, `failed_phase` and `detail` from the summary |
| Summary `done` (1124-1125) | subtask `done`, then the id is appended to `completed` | none |
| All subtasks done (1126) | story `done` | `done`, subtask None, returned via `finished[story.id]` after the slot is released |
| Catch-all `Exception` (1130-1136) | trigger, then subtask `escalated` from `subtask_rows[current.id]` if there is a current subtask, then story `escalated` | `escalated`, subtask = current or None, detail `"Type: msg"` |

The `completed` and `warnings` snapshots taken at outcome-build time stay the same. Warnings are extended from `result.warnings` before the summary is inspected. `base` is set only after `build_merged_base` succeeds, and is carried on every later outcome.

## Error paths

These are unchanged. `LaneEscalated` and `LaneStopped` pass through the catch-all untouched, and `BaseException` is never caught. Errors raised inside a recorder method are inside the same `try` as today, so they reach the catch-all just as an inline `store.record_*` failure does now.

## Tests

Add no new tests; §S5's test strategy forbids it. The oracle is the existing `tests/test_orchestrate.py` suite, which is the Engine tier under design spec §14: `lane` driven with fake drivers and collaborators. It must pass unchanged with `uv run pytest`. The most relevant tests are those asserting on recorded story and subtask status and on journal shape, for example:

- `test_a_lane_bug_becomes_escalated_with_type_and_message` (Engine tier, `tests/test_orchestrate.py`)
- `test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending` (Engine tier, `tests/test_orchestrate.py`)
- `test_a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next` (Engine tier, `tests/test_orchestrate.py`)

If a test ever proves necessary, it goes in the Engine tier (`tests/test_orchestrate.py`, fake-collaborator style). It never goes in `tests/e2e/`. None is planned.

## Verification

`uv run pytest`. There is no typecheck or lint command.
