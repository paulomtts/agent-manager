# Refactor `base_only_lane` onto the same `StoryRecorder` (card 8fc3628c)

Parent story: 0f2b1491 "One StoryRecorder instead of nine inlined state transitions". Source of truth: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, decision S5 and §6 Testing. This narrows S5 to its second half: `lane` already runs on `StoryRecorder` (sibling 7640024e, present in this worktree at `src/agent_manager/orchestrate.py`, class at line 865); this subtask moves `base_only_lane` (line 991) onto it.

## Scope

In `src/agent_manager/orchestrate.py` only:

1. Let `StoryRecorder` run without store rows. A subtask-less merged-root story (the only kind `builds_a_base_alone` sends to `base_only_lane`) has no entry in `plan.rows` and no `planned.level`, because `record_plan` records only stories with work. The existing tests rely on this: `test_a_subtask_less_storys_failed_base_escalates_the_run_and_its_dependent_stays_pending` asserts the store holds no row for J. So the recorder needs a row-less mode. In this mode `level` is `None`, there is no story row, and the subtask-row mapping is empty. Every story-row write (`_record_story`, and therefore `stopped`, `escalated` and `done`) writes nothing, but the method still builds and returns the same `LaneOutcome`. The implementer picks the form: an optional `story_row: models.StoryRun | None` with `level: int | None` on the constructor, or a named alternate constructor such as `StoryRecorder.without_rows(store, story_id)`. Both are acceptable. `lane`'s construction and behaviour must not change.
2. Rewrite `base_only_lane` as a call sequence against one `StoryRecorder` in that row-less mode. Delete its local `outcome(...)` helper.
   - If the stop has already fired, raise `LaneStopped(recorder.stopped(None, None))`.
   - `BaseFailed(stopped=True)`: raise `LaneStopped(recorder.stopped(None, None))`.
   - `BaseFailed(stopped=False)`: call `stop.trigger(story.id)`, then raise `LaneEscalated(recorder.escalated(None, "base", error.detail))`.
   - Any other `Exception` from the base: call `stop.trigger(story.id)`, then raise `LaneEscalated(recorder.escalated(None, None, f"{type(error).__name__}: {error}"))`.
   - On success: `recorder.base_built(root_plan)`, then `finished[story.id] = recorder.done()`, then return `plan.tips[story.id]`.
   - `stop.trigger` stays with the caller, as the recorder's contract already says. The slot acquisition, the order of checks, the `build_merged_base` arguments and the `resume_from` lookup do not change.
3. Update the docstrings of `StoryRecorder` and `base_only_lane`. They should say that the recorder serves both lanes and that the base-only lane uses the row-less mode, which writes no rows.

## Observable behaviour (must be identical)

- Each path builds the same `LaneOutcome` field for field as today. `story=story.id`, `level=None`, `subtask=None`, and `completed` and `warnings` are empty.
- `failed_phase="base"` and `detail` are set only on a `BaseFailed` escalation. On the catch-all path, `detail` is set and `failed_phase` is `None`.
- `before_phase` is `None`.
- `base` is `root_plan` only on `done`, and `None` on every stopped or escalated outcome, because the base was never built on those paths.
- No store writes are added. The subtask-less story still has no story or subtask row. `am status` output, journal shape, the CLI JSON envelopes and the `bases` report entry do not change.
- `collect_outcomes` still reports these outcomes after the waves, because `level=None`.

## Error paths

The paths are the four listed in Scope item 2: stop fired before the base, the base parked, the base failed, and any other `Exception`. `LaneStopped` and `LaneEscalated` propagate unchanged. A `BaseException` such as Ctrl-C is never caught. The row-less recorder must never index `subtask_rows`: `base_only_lane` never passes `subtask_row=` and never calls `started` or `subtask_done`. If code misuses those calls on a row-less recorder, failing loudly (KeyError or assert) is acceptable. Silently writing a row is not.

## Out of scope

- `SubtaskSummary.before_phase` and the `stopped_before_phase` text-parsing helper belong to sibling 150613bb. Leave both alone.
- Do not change `lane`'s control flow or rows.
- Out of scope per spec §8: pygents' turn and phase model, the checkpoint format, the harness adapter contract, adding a typecheck or CI gate, rewriting boundary monkeypatches, and dropping grafo.
- `tests/test_bases.py` fixtures are not modified.

## Tests

S5 and §6 say no new tests. The existing assertions are the oracle and must pass unchanged. There is no tier-taxonomy doc. The placement rules are that `tests/` mirrors `src/` one file per module (CLAUDE.md), and that `tests/e2e/` (`-m e2e`, excluded by default in pyproject) is only for real-`claude` runs. Every test below is therefore a fast fake-harness test in `tests/test_orchestrate.py`, and none belongs in `tests/e2e/`.

- `test_only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone` (:543): fast, `tests/test_orchestrate.py`.
- `test_a_base_only_lanes_outcome_follows_the_waves_in_census_order` (:553): fast, `tests/test_orchestrate.py`.
- `test_a_story_behind_a_subtask_less_story_waits_for_the_blocker_beneath` (:2482): fast, `tests/test_orchestrate.py`.
- `test_any_other_error_from_the_base_is_a_lane_escalation_with_no_subtask` (:3323): fast, `tests/test_orchestrate.py`.
- `test_a_subtask_less_story_on_two_blockers_builds_its_base_and_its_dependent_stacks_on_it` (:3372): fast, `tests/test_orchestrate.py`.
- `test_a_subtask_less_storys_failed_base_escalates_the_run_and_its_dependent_stays_pending` (:3410). This is the oracle for the absent store row. Fast, `tests/test_orchestrate.py`.
- `test_a_closed_subtask_less_story_builds_no_base` (:3455): fast, `tests/test_orchestrate.py`.
- `test_a_subtask_less_merged_storys_dependent_stays_pending_when_a_blocker_failed` (:3500): fast, `tests/test_orchestrate.py`.
- `test_a_resume_hands_a_subtask_less_storys_resolver_checkpoint_to_its_base` (:3959): fast, `tests/test_orchestrate.py`.
- The rest of the merged-base block starting at `tests/test_orchestrate.py:2959`, and all of `lane`'s existing tests: fast, `tests/test_orchestrate.py`. These guard that the recorder change leaves `lane` unchanged.

Verification: `uv run pytest`. There is no lint or typecheck command.
