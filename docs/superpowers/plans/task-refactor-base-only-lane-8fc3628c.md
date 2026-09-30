<!-- task-pipeline: validated -->
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

---

# Refactor `base_only_lane` onto `StoryRecorder` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `base_only_lane`'s local `outcome(...)` helper and hand-built `LaneOutcome`s with calls on one row-less `StoryRecorder`, with no change to any outcome, store write or CLI output.

**Architecture:** `StoryRecorder` gains a row-less mode: `level` and `story_row` become optional, `_record_story` is a no-op when `story_row is None`, and a named alternate constructor `StoryRecorder.without_rows(store, story_id)` builds it with `level=None`, `story_row=None` and an empty subtask-row mapping. `base_only_lane` builds one such recorder and turns each of its four exits into a recorder call; `stop.trigger` stays in the lane. `lane` keeps its existing positional construction untouched.

**Tech Stack:** Python 3, dataclasses, asyncio, pytest (run via `uv run pytest`).

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c/docs/superpowers/specs/task-refactor-base-only-lane-8fc3628c-design.md` (prepended above).

## Global Constraints

- Only `src/agent_manager/orchestrate.py` changes. No test file is added or edited (spec, Tests: "S5 and §6 say no new tests. The existing assertions are the oracle and must pass unchanged.").
- No store writes are added: the subtask-less story still has no story or subtask row.
- `lane`'s construction (`StoryRecorder(store, story.id, planned.level, story_row, subtask_rows)`, orchestrate.py:1141) and control flow do not change.
- Do not touch `SubtaskSummary`, the `stopped_before_phase` helper (sibling 150613bb), `tests/test_bases.py`, or anything in spec §8.
- `stop.trigger` stays with the caller; the recorder never signals the stop.
- `except Exception` stays `Exception`, never `BaseException` (Ctrl-C must still stop).
- Verification: `uv run pytest`. There is no lint or typecheck command.
- This branch is cut from `m13/task-add-storyrecorder-and-7640024e`: `StoryRecorder` exists at orchestrate.py:865-988 and `base_only_lane` at :991-1049. Assume nothing from any other subtask.

## Review Focus

The spec forbids new tests (S5, §6), so these are not pinned by new tests in this plan. They are the paths the existing oracle does not exercise for a subtask-less (row-less) story; the reviewer must check each one by reading the diff against the pre-change `outcome(...)` calls:

1. Stop already fired when a base-only lane takes its slot: expect `LaneStopped` with `LaneOutcome(kind="stopped", story=J, level=None)` and no store write. No existing test drives this path for a `"J": 0` story; `recorder.stopped(None, None)` must not write a row (row-less `_record_story` no-op).
2. `BaseFailed(stopped=True)` from a base-only lane (resolver parked): expect `LaneStopped` with the same stopped outcome and no `stop.trigger`. The existing parked test (test_orchestrate.py:3239) covers a story with subtasks, which goes through `lane`, not `base_only_lane`.
3. A non-`BaseFailed` `Exception` from a base-only lane: expect `stop.trigger(story.id)` then `LaneEscalated` with `failed_phase=None`, `detail="<Type>: <msg>"`, `level=None`, `base=None`. The existing catch-all test (:3323) uses story C with one subtask, so it exercises `lane`'s catch-all, not this one.
4. Any outcome from the escalated/stopped paths must carry `base=None`: `recorder.base_built(root_plan)` must be called only after `build_merged_base` returns, outside the `try`, never before the `await`.
5. Misuse of a row-less recorder (`started`, `subtask_done`, or `subtask_row=` on it) must fail loudly: `started` raises `KeyError` on the empty mapping, `subtask_done` raises `KeyError` on the empty `_started`, and `stopped`/`escalated` with `subtask_row` index those same empty dicts. The reviewer must confirm no code path silently writes a subtask row with `story_row is None`.

---

### Task 1: Row-less `StoryRecorder` and `base_only_lane` on it

This is a behaviour-preserving refactor with no new tests by spec, so the RED step is replaced by a baseline run of the oracle: record that the named tests pass before the change, so a failure after the change is attributable to the change. The row-less mode and the lane rewrite are one task because the row-less mode has no caller, and so no test cycle, without the rewrite.

**Files:**
- Modify: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c/src/agent_manager/orchestrate.py:865-900` (`StoryRecorder` docstring, `__init__`, new `without_rows`)
- Modify: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c/src/agent_manager/orchestrate.py:972-973` (`_record_story`)
- Modify: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c/src/agent_manager/orchestrate.py:1007-1049` (`base_only_lane` docstring and body)
- Test (unchanged oracle): `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c/tests/test_orchestrate.py`

**Interfaces:**
- Consumes: `LaneOutcome` (orchestrate.py:95), `LaneStopped`/`LaneEscalated` (:121, :131), `build_merged_base`, `bases.BaseFailed` (`.stopped`, `.detail`), `bases.resolver_card_id`, `StopSignal.triggered`/`.trigger`.
- Produces: `StoryRecorder.__init__(self, store: Store, story_id: str, level: int | None, story_row: models.StoryRun | None, subtask_rows: Mapping[str, models.SubtaskRun]) -> None` and `StoryRecorder.without_rows(cls, store: Store, story_id: str) -> StoryRecorder` (classmethod). `base_only_lane`'s signature is unchanged.

- [ ] **Step 1: Baseline — run the oracle before touching code**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c && uv run pytest tests/test_orchestrate.py -v -k "subtask_less or base_only or base_alone or other_error_from_the_base or merged or base"
```

Expected: every selected test PASSES (or is skipped only because `git`/`brd` is absent; if any are skipped, note it, since the full-suite run in Step 5 must then be run where they execute). Then run the whole suite once:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c && uv run pytest
```

Expected: all pass. Record the pass/skip counts to compare against in Step 5. If anything already fails, stop and report: the oracle must be green before a refactor.

- [ ] **Step 2: Give `StoryRecorder` a row-less mode**

In `src/agent_manager/orchestrate.py`, replace the `StoryRecorder` docstring and `__init__` (lines 865-900) with:

```python
class StoryRecorder:
    """Every row one story's lane writes, and every `LaneOutcome` it builds (cleanup §S5).

    One per lane invocation, and it serves both lanes. `lane` builds it once
    the story's planned rows are known. `base_only_lane` builds it with
    `without_rows`: a subtask-less story has no rows, since `record_plan`
    records only stories with work, so that recorder writes nothing and only
    builds outcomes, all with `level=None`.

    It owns the outcome's state: the subtasks `completed` so far, the lane's
    `warnings`, and the merged `base` once it is built. Every outcome
    snapshots them at the moment it is built. The methods that end a lane
    write their rows and return the outcome for the lane to raise. The
    recorder never signals the stop: `stop.trigger` stays with the caller,
    right before an escalation's writes.

    Some transitions write only the story row: a stop seen before a subtask
    or before the base, and a base failure. So `stopped` and `escalated` take
    a keyword-only `subtask_row` that says which subtask row to write first,
    if any. `"started"` is the row `started` returned and the driver was
    handed. `"planned"` is `record_plan`'s row, which the catch-all writes for
    a subtask it may never have started. A row-less recorder has no subtask
    rows, so `started`, `subtask_done` and any `subtask_row` raise `KeyError`
    on it rather than write one.
    """

    def __init__(
        self,
        store: Store,
        story_id: str,
        level: int | None,
        story_row: models.StoryRun | None,
        subtask_rows: Mapping[str, models.SubtaskRun],
    ) -> None:
        self._store = store
        self._story_id = story_id
        self._level = level
        self._story_row = story_row
        self._subtask_rows = subtask_rows
        self._started: dict[str, models.SubtaskRun] = {}
        self._completed: list[str] = []
        self._warnings: list[str] = []
        self._base: dag.RootPlan | None = None

    @classmethod
    def without_rows(cls, store: Store, story_id: str) -> StoryRecorder:
        """A recorder for a story with no store rows: it writes nothing.

        Its outcomes have `level=None`, so `collect_outcomes` reports them
        after the waves.
        """
        return cls(store, story_id, None, None, {})
```

(`from __future__ import annotations` is at orchestrate.py:43, so the `-> StoryRecorder` annotation needs no quotes.)

- [ ] **Step 3: Make `_record_story` a no-op without a story row**

Replace lines 972-973:

```python
    def _record_story(self, status: str) -> None:
        self._store.record_story(self._story_row.model_copy(update={"status": status}))
```

with:

```python
    def _record_story(self, status: str) -> None:
        if self._story_row is None:
            return
        self._store.record_story(self._story_row.model_copy(update={"status": status}))
```

Leave `_record_subtask`, `started`, `subtask_done`, `stopped`, `escalated`, `done` and `_outcome` exactly as they are.

- [ ] **Step 4: Rewrite `base_only_lane` on the recorder**

Replace the docstring and body of `base_only_lane` (lines 1007-1049, everything from the `"""A subtask-less story's lane` docstring through `return plan.tips[story.id]`) with the following. The signature (lines 991-1006) is unchanged.

```python
    """A subtask-less story's lane: build its merged base, return it as its tip.

    It takes a slot, since a base can dispatch a resolver, and checks the stop
    first. The failure paths are `lane`'s for a merged base, but with no
    subtask to name and no store row to write -- `record_plan` records only
    stories with work -- so it runs on a row-less `StoryRecorder`
    (`StoryRecorder.without_rows`), which writes nothing. Every outcome has
    `level=None` and `collect_outcomes` reports it after the waves.
    """
    recorder = StoryRecorder.without_rows(store, story.id)
    async with slots:
        if stop.triggered:
            raise LaneStopped(recorder.stopped(None, None))
        try:
            await build_merged_base(
                story,
                root_plan,
                tips,
                store=store,
                run_id=run_id,
                root=root,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                stop=stop,
                resume_from=plan.checkpoints.get(bases.resolver_card_id(story.id)),
            )
        except bases.BaseFailed as error:
            if error.stopped:
                raise LaneStopped(recorder.stopped(None, None)) from error
            stop.trigger(story.id)
            raise LaneEscalated(recorder.escalated(None, "base", error.detail)) from error
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            stop.trigger(story.id)
            raise LaneEscalated(
                recorder.escalated(None, None, f"{type(error).__name__}: {error}")
            ) from error
    recorder.base_built(root_plan)
    finished[story.id] = recorder.done()
    return plan.tips[story.id]
```

Check by reading the diff that each outcome matches the deleted `outcome(...)` calls field for field:

- stop fired / parked: old `LaneOutcome(kind="stopped", story=story.id, level=None)`; new `_outcome("stopped", None, before_phase=None)` gives `subtask=None`, `before_phase=None`, `completed=()`, `warnings=()`, `base=None` — identical to the dataclass defaults.
- `BaseFailed`: old `failed_phase="base", detail=error.detail`; new `escalated(None, "base", error.detail)` passes `failed_phase="base", detail=error.detail`, `base=None` (not yet built).
- catch-all: old `detail=...` only; new `escalated(None, None, detail)` passes `failed_phase=None` (the default) and the same `detail`.
- done: old `outcome("done", base=root_plan)`; new `base_built(root_plan)` then `done()` gives `base=root_plan`, `subtask=None`.

Then confirm nothing else referenced the deleted helper and that `Any`/`LaneKind` are still used elsewhere (they are imported at orchestrate.py:51 and used by `StoryRecorder._outcome`, so no import changes):

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c && grep -n "def outcome\|outcome(\"" src/agent_manager/orchestrate.py
```

Expected: no output.

- [ ] **Step 5: Run the oracle, then the full suite**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c && uv run pytest tests/test_orchestrate.py -v -k "subtask_less or base_only or base_alone or other_error_from_the_base or merged or base"
```

Expected: the same tests as Step 1 PASS, in particular `test_a_subtask_less_storys_failed_base_escalates_the_run_and_its_dependent_stays_pending` (no row for J), `test_a_subtask_less_story_on_two_blockers_builds_its_base_and_its_dependent_stacks_on_it` (`bases` report entry), `test_a_resume_hands_a_subtask_less_storys_resolver_checkpoint_to_its_base`, and `test_a_base_only_lanes_outcome_follows_the_waves_in_census_order`.

Then:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c && uv run pytest
```

Expected: all pass, with the same pass/skip counts recorded in Step 1. No test file changed (`git status` shows only `src/agent_manager/orchestrate.py` and this plan/spec under `docs/`).

- [ ] **Step 6: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-refactor-base-only-lane-8fc3628c && git add src/agent_manager/orchestrate.py && git commit -m "refactor: run base_only_lane on a row-less StoryRecorder

StoryRecorder gains without_rows(store, story_id): level=None, no story
row, no subtask rows, so its story writes are no-ops but it still builds
the same LaneOutcome. base_only_lane's local outcome helper is gone; its
four exits are recorder calls, with stop.trigger kept in the lane.
No change to what is recorded (cleanup S5, card 8fc3628c)."
```
