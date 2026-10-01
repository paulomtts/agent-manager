<!-- task-pipeline: validated -->
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

---

# StoryRecorder for `lane` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every store write and every `LaneOutcome` that `lane` builds into one `StoryRecorder` per lane invocation, without changing any recorded row, write order, or outcome field.

**Architecture:** A plain class `StoryRecorder` in `src/agent_manager/orchestrate.py`, placed directly above `base_only_lane`. It owns the outcome state (`completed`, `warnings`, `base`) that the `outcome()` closure in `lane` reads today. It has the four §S5 methods, plus keyword-only `subtask_row` selectors and three small helpers (`base_built`, `add_warnings`, `done`) for the writes the four calls can't express. `lane` keeps all control flow and every `stop.trigger` call; each place it used to call `store.record_*` or `outcome(...)` becomes one recorder call.

**Tech Stack:** Python 3, Pydantic v2 models (`models.StoryRun`, `models.SubtaskRun`, via `model_copy(update=...)`), pytest run via `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-add-storyrecorder-and-7640024e/docs/superpowers/specs/task-add-storyrecorder-and-7640024e-design.md` (prepended above). Milestone source of truth: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-add-storyrecorder-and-7640024e/docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` §S5.

All paths below are inside the worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-add-storyrecorder-and-7640024e` on branch `m13/task-add-storyrecorder-and-7640024e`. Run every command from that directory.

## Global Constraints

- The class lives in `src/agent_manager/orchestrate.py`. No new module.
- No new tests. §S5's test strategy says: "no new tests — test_orchestrate.py's assertions on recorded story/subtask status and journal shape are the oracle; they must pass against the extracted StoryRecorder unchanged." `tests/test_orchestrate.py` is not edited.
- If a test ever becomes necessary, it goes in the Engine tier (`tests/test_orchestrate.py`, fake-collaborator style), never in `tests/e2e/`.
- Do not touch `base_only_lane` (`orchestrate.py:865-923`) or its `outcome()` closure. It belongs to sibling 8fc3628c.
- Do not touch `stopped_before_phase` / `STOPPED_PREFIX` (`orchestrate.py:73-86`) or `SubtaskSummary`. They belong to sibling 150613bb.
- `stop.trigger(story.id)` stays in `lane`, immediately before the escalation writes. The recorder never signals.
- `subtask_done(subtask_id, tip)` adds no write for `tip`, because no row field holds a tip.
- The only verification command is `uv run pytest`. There is no typecheck or lint command, and none may be added.
- Don't change the pygents turn/phase model, the checkpoint format, the harness adapter contract, grafo, or any existing monkeypatch call.

## Review Focus

These failure modes follow from the spec, but no test in `tests/test_orchestrate.py` reliably pins them. The spec forbids new tests, which overrides the writing-plans default of adding a test per line. So each one is instead a numbered review check in Task 1, Step 5, done by reading the diff against today's code (quoted in Task 1, Step 3).

1. The driver mutates the `row` object it was handed, for example appending phases in place. The expected behaviour: the `stopped`, `escalated` and `done` subtask writes after `drive` still copy that exact object, the started row, not a fresh copy of `record_plan`'s row. That is why `started` stores the row it returns and `subtask_row="started"` reuses it.
2. An `Exception` is raised by `board.show` before the subtask was started. The expected behaviour: the catch-all still writes that subtask `escalated`, copied from `record_plan`'s row (`subtask_rows[current.id]`). It must not skip the write or `KeyError` just because `started` never ran. That is why the catch-all uses `subtask_row="planned"`.
3. An `Exception` is raised by the final story `done` write, after the last `subtask_done`. The expected behaviour: the catch-all re-writes the last subtask `escalated` (because `current` is still set) and then the story `escalated`, just as today. So the `done` write must stay inside the `try`, and `finished[story.id]` must be set only after the `async with slots:` block.
4. Two lanes run concurrently. The expected behaviour: each gets its own `completed` and `warnings`. So the recorder must hold no class-level mutable defaults, and exactly one recorder is built per `lane` call.
5. A merged-root story stops or fails before or at its base. The expected behaviour: those outcomes carry `base=None`, and every outcome after `build_merged_base` succeeds carries `base=root_plan`. The pre-base stop outcome names `planned.remaining[0].id`, while the `BaseFailed` outcomes name no subtask.

---

### Task 1: Add `StoryRecorder` and route every write and outcome in `lane` through it

This is one task because the class has no caller and no test of its own without the `lane` refactor: it is scaffolding for that deliverable. The oracle is the unchanged Engine suite. Because the spec forbids new tests, the TDD "RED" step here is a deliberate mutation (Step 9). It proves the existing Engine tests fail when the recorder writes the wrong status or drops `completed`, so the green run in Step 7 actually covers the recorder.

**Files:**
- Modify: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-add-storyrecorder-and-7640024e/src/agent_manager/orchestrate.py`. Insert the class between `builds_a_base_alone` (ends line 862) and `base_only_lane` (line 865). Replace the `lane` body from line 1009 (`story_row, subtask_rows = plan.rows[story.id]`) through line 1145 (`return planned.tip`), and add one paragraph to the `lane` docstring (lines 943-981).
- Test (oracle, unchanged): `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-add-storyrecorder-and-7640024e/tests/test_orchestrate.py`

**Interfaces:**
- Consumes (existing, unchanged): `Store.record_story(story: models.StoryRun) -> JournalLine` and `Store.record_subtask(story_id: str, subtask: models.SubtaskRun) -> JournalLine` (`src/agent_manager/store.py:780,790`); `LaneOutcome` and `LaneKind` (`orchestrate.py:89-118`); `PlannedStory.level: int` (`orchestrate.py:348`); `SupervisorPlan.rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]` (`orchestrate.py:426`).
- Produces (for sibling 8fc3628c to reuse later):
  - `StoryRecorder(store: Store, story_id: str, level: int, story_row: models.StoryRun, subtask_rows: Mapping[str, models.SubtaskRun])`
  - `started(*, subtask_id: str, first: bool) -> models.SubtaskRun`
  - `subtask_done(subtask_id: str, tip: str) -> None`
  - `stopped(subtask_id: str | None, before_phase: str | None, *, subtask_row: Literal["started"] | None = None) -> LaneOutcome`
  - `escalated(subtask_id: str | None, phase: str | None, detail: str | None, *, subtask_row: Literal["started", "planned"] | None = None) -> LaneOutcome`
  - `base_built(base: dag.RootPlan) -> None`
  - `add_warnings(warnings: Sequence[str]) -> None`
  - `done() -> LaneOutcome`

- [ ] **Step 1: Confirm the oracle runs green on the untouched branch, and is not skipped**

The relevant Engine tests carry `@requires_git` / `@requires_brd`. If they are skipped here, a green run proves nothing.

Run: `uv run pytest tests/test_orchestrate.py -rs -q -k "a_lane_bug_becomes_escalated_with_type_and_message or an_escalation_parks_the_other_lane_and_its_dependent_stays_pending or a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next"`
Expected: `3 passed`, with no `SKIPPED` lines. If any is skipped, stop and install or put on `PATH` the missing `git`/`brd` before continuing. Every later step depends on these tests actually running.

Then run: `uv run pytest -q`
Expected: all pass. Record the pass count, because Step 7 must match it exactly.

- [ ] **Step 2: Add the `StoryRecorder` class**

In `src/agent_manager/orchestrate.py`, insert the following between the end of `builds_a_base_alone` (the `)` closing its `return` at line 862) and `async def base_only_lane(` (line 865), keeping two blank lines on each side. Every name it uses (`Store`, `models`, `dag`, `Literal`, `Mapping`, `Sequence`, `LaneOutcome`, `LaneKind`, `Any`) is already imported or defined earlier in the module.

```python
class StoryRecorder:
    """Every row one story's `lane` writes, and every `LaneOutcome` it builds (cleanup §S5).

    One per `lane` invocation, built once the story's planned rows are known.
    It owns the outcome's state: the subtasks `completed` so far, the lane's
    `warnings`, and the merged `base` once it is built. Every outcome
    snapshots them at the moment it is built. The methods that end a lane
    write their rows and return the outcome for `lane` to raise. The recorder
    never signals the stop: `stop.trigger` stays with the caller, right
    before an escalation's writes.

    Some transitions write only the story row: a stop seen before a subtask
    or before the base, and a base failure. So `stopped` and `escalated` take
    a keyword-only `subtask_row` that says which subtask row to write first,
    if any. `"started"` is the row `started` returned and the driver was
    handed. `"planned"` is `record_plan`'s row, which the catch-all writes for
    a subtask it may never have started.
    """

    def __init__(
        self,
        store: Store,
        story_id: str,
        level: int,
        story_row: models.StoryRun,
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

    def started(self, *, subtask_id: str, first: bool) -> models.SubtaskRun:
        """Record the subtask `started`, and the story too on its first subtask.

        Returns the started row, which is the one the driver is handed and
        the one every later write for this subtask copies.
        """
        row = self._subtask_rows[subtask_id].model_copy(update={"status": "started"})
        self._started[subtask_id] = row
        self._store.record_subtask(self._story_id, row)
        if first:
            self._record_story("started")
        return row

    def subtask_done(self, subtask_id: str, tip: str) -> None:
        """Record the subtask `done` and count it as completed.

        `tip` is the subtask's branch. No row field holds a tip, so it is not
        written.
        """
        row = self._started[subtask_id]
        self._store.record_subtask(self._story_id, row.model_copy(update={"status": "done"}))
        self._completed.append(subtask_id)

    def stopped(
        self,
        subtask_id: str | None,
        before_phase: str | None,
        *,
        subtask_row: Literal["started"] | None = None,
    ) -> LaneOutcome:
        """Record the story `stopped`, after the started subtask when there is one."""
        if subtask_row == "started":
            assert subtask_id is not None
            self._record_subtask(self._started[subtask_id], "stopped")
        self._record_story("stopped")
        return self._outcome("stopped", subtask_id, before_phase=before_phase)

    def escalated(
        self,
        subtask_id: str | None,
        phase: str | None,
        detail: str | None,
        *,
        subtask_row: Literal["started", "planned"] | None = None,
    ) -> LaneOutcome:
        """Record the story `escalated`, after the named subtask row when there is one."""
        if subtask_row is not None:
            assert subtask_id is not None
            source = (
                self._started[subtask_id]
                if subtask_row == "started"
                else self._subtask_rows[subtask_id]
            )
            self._record_subtask(source, "escalated")
        self._record_story("escalated")
        return self._outcome("escalated", subtask_id, failed_phase=phase, detail=detail)

    def base_built(self, base: dag.RootPlan) -> None:
        """The merged base is built: every later outcome carries it."""
        self._base = base

    def add_warnings(self, warnings: Sequence[str]) -> None:
        """Keep a driven subtask's warnings, in the order they arrived."""
        self._warnings.extend(warnings)

    def done(self) -> LaneOutcome:
        """Record the story `done` and return its outcome."""
        self._record_story("done")
        return self._outcome("done", None)

    def _record_story(self, status: str) -> None:
        self._store.record_story(self._story_row.model_copy(update={"status": status}))

    def _record_subtask(self, row: models.SubtaskRun, status: str) -> None:
        self._store.record_subtask(self._story_id, row.model_copy(update={"status": status}))

    def _outcome(self, kind: LaneKind, subtask: str | None, **fields: Any) -> LaneOutcome:
        return LaneOutcome(
            kind=kind,
            story=self._story_id,
            level=self._level,
            subtask=subtask,
            completed=tuple(self._completed),
            warnings=tuple(self._warnings),
            base=self._base,
            **fields,
        )
```

- [ ] **Step 3: Route `lane` through one recorder**

Today's code, lines 1009-1145, for reference during review:

```python
    story_row, subtask_rows = plan.rows[story.id]
    completed: list[str] = []
    warnings: list[str] = []
    built: dag.RootPlan | None = None

    def outcome(kind: LaneKind, subtask: str | None, **fields: Any) -> LaneOutcome:
        ...
    async with slots:
        ...
    finished[story.id] = outcome("done", None)
    return planned.tip
```

Replace everything from `    story_row, subtask_rows = plan.rows[story.id]` (line 1009) through `    return planned.tip` (line 1145) with the following. Leave everything above line 1009 byte-for-byte as is, including the early returns and the `base_only_lane` delegation.

```python
    story_row, subtask_rows = plan.rows[story.id]
    recorder = StoryRecorder(store, story.id, planned.level, story_row, subtask_rows)

    async with slots:
        current: census.SubtaskPlan | None = None
        try:
            if root_plan.kind == "merged":
                # Checked before the base as before every subtask: a lane that
                # finds the stop fired builds nothing (spec, first error path).
                if stop.triggered:
                    raise LaneStopped(recorder.stopped(planned.remaining[0].id, None))
                assert tips is not None
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
                    # A parked resolver is a stop, not an escalation (P4).
                    if error.stopped:
                        raise LaneStopped(recorder.stopped(None, None)) from error
                    stop.trigger(story.id)
                    raise LaneEscalated(
                        recorder.escalated(None, "base", error.detail)
                    ) from error
                recorder.base_built(root_plan)
            for position, subtask in enumerate(planned.remaining):
                current = subtask
                if stop.triggered:
                    raise LaneStopped(recorder.stopped(subtask.id, None))
                card = await asyncio.to_thread(board.show, subtask.id, repo_dir=root)
                parent = await asyncio.to_thread(board.show, story.id, repo_dir=root)
                row = recorder.started(subtask_id=subtask.id, first=position == 0)
                # Relaunch continuation (card 02890d5d) is lenient and reads
                # across runs; a resume (card 54e4ec29) hands on exactly the
                # checkpoints `resume_checkpoints` already validated. Either
                # way the keyword is passed only when there is a row, so a
                # driver that predates it works.
                extra: dict[str, Any] = {}
                if plan.resuming:
                    checkpoint = plan.checkpoints.get(subtask.id)
                else:
                    checkpoint = cli.continuable_checkpoint(store, subtask.id)
                if checkpoint is not None:
                    extra["resume_from"] = checkpoint
                result = await drive(
                    store=store,
                    run_id=run_id,
                    card=card,
                    parent=parent,
                    subtask=row,
                    repo_dir=root,
                    commands=list(commands),
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    stop=stop,
                    **extra,
                )
                recorder.add_warnings(result.warnings)
                summary = result.summary
                # A `stopped` summary is handled before the non-`done` branch:
                # a stop is not an escalation (P4).
                if summary.status == "stopped":
                    raise LaneStopped(
                        recorder.stopped(
                            subtask.id,
                            stopped_before_phase(summary.detail),
                            subtask_row="started",
                        )
                    )
                if summary.status != "done":
                    stop.trigger(story.id)
                    raise LaneEscalated(
                        recorder.escalated(
                            subtask.id,
                            summary.failed_phase,
                            summary.detail,
                            subtask_row="started",
                        )
                    )
                recorder.subtask_done(subtask.id, row.branch)
            done = recorder.done()
        except (LaneEscalated, LaneStopped):
            raise
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            stop.trigger(story.id)
            raise LaneEscalated(
                recorder.escalated(
                    None if current is None else current.id,
                    None,
                    f"{type(error).__name__}: {error}",
                    subtask_row=None if current is None else "planned",
                )
            ) from error
    finished[story.id] = done
    return planned.tip
```

What stays the same:
- The old `row = subtask_rows[subtask.id]` lookup that sat before the two `board.show` calls is gone. `started` now does that lookup after them. `record_plan` (`orchestrate.py:766-776`) builds a row for every `planned.remaining` subtask, so the lookup can't fail on a real plan. If a row were missing, both versions end in the same unhandled `KeyError` from the catch-all's own `subtask_rows[current.id]`.
- The `done` outcome is now built inside the slot, where the story `done` write already was. `completed`, `warnings` and `base` can't change between that point and the old build point after the slot. `finished[story.id]` is still assigned only after the `async with slots:` block exits.

- [ ] **Step 4: Add one paragraph to the `lane` docstring**

In `lane`'s docstring, directly before the paragraph that starts `    Each subtask's open checkpoint is looked up first` (line 974), insert this paragraph followed by one blank line:

```python
    Every row the lane writes and every outcome it builds go through one
    `StoryRecorder`, built once the story's planned rows are read. The lane
    keeps the control flow and every `stop.trigger`; the recorder keeps
    `completed`, `warnings` and the built base.

```

- [ ] **Step 5: Static checks and Review Focus pass**

Run: `rg -n "store\.record_story|store\.record_subtask|def outcome|completed\.append|warnings\.extend|built = " src/agent_manager/orchestrate.py`
Expected: no hit falls inside `lane` (the lines from `async def lane(` to `async def run_until_killed(`). `def outcome` appears exactly once, inside `base_only_lane`. The `record_story` / `record_subtask` hits are only in `record_plan`, other pre-existing functions outside `lane`, and the `StoryRecorder` methods (as `self._store.record_...`). The `completed\.append` and `warnings\.extend` patterns also match `self._completed.append(...)` and `self._warnings.extend(...)` inside `StoryRecorder`, and the pre-existing, untouched `warnings.extend(outcome.warnings)` in the wave-collection loop near the end of the file (outside `lane`) still matches too -- neither is a problem, since neither falls inside `lane`.

Run: `git diff --stat`
Expected: only `src/agent_manager/orchestrate.py` changed.

Run: `git diff -U0 src/agent_manager/orchestrate.py | rg -n "^[-+].*(def base_only_lane|def stopped_before_phase|STOPPED_PREFIX)"`
Expected: no output, meaning `base_only_lane` and `stopped_before_phase` are untouched.

Then check each Review Focus line against the new code:
1. The `stopped` and `escalated` calls that follow `drive` pass `subtask_row="started"`, and `subtask_done` copies `self._started[subtask_id]`. Both reuse the same object `started` returned and `drive` received.
2. The catch-all passes `subtask_row="planned"` whenever `current` is set, so it reads `self._subtask_rows`, never `self._started`. It can't `KeyError` for a subtask that was never started.
3. `recorder.done()` is the last statement inside the `try`, and `finished[story.id] = done` comes after the `async with` block.
4. `StoryRecorder` has no class attributes. All state is set in `__init__`, and `lane` builds exactly one recorder.
5. `recorder.base_built(root_plan)` sits on the line where `built = root_plan` was, after the `try/except bases.BaseFailed`. The pre-base stop passes `planned.remaining[0].id`, and both `BaseFailed` outcomes pass `None` as the subtask.

- [ ] **Step 6: Run the three named Engine tests**

Run: `uv run pytest tests/test_orchestrate.py -rs -q -k "a_lane_bug_becomes_escalated_with_type_and_message or an_escalation_parks_the_other_lane_and_its_dependent_stays_pending or a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next"`
Expected: `3 passed`, with no `SKIPPED`.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass, with the same pass count recorded in Step 1. `git status` shows `tests/` unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/orchestrate.py
git commit -m "refactor: route lane's story and subtask writes through one StoryRecorder

Add StoryRecorder (started, subtask_done, stopped, escalated, plus
base_built/add_warnings/done) and drop lane's outcome() closure and its
captured completed/warnings/built locals. Behaviour-preserving: the
existing test_orchestrate.py suite passes unchanged (cleanup spec S5).
base_only_lane is left for 8fc3628c."
```

- [ ] **Step 9: RED check that the oracle catches a wrong recorder (temporary mutation, never committed)**

First mutation: in `StoryRecorder.stopped`, change `self._record_story("stopped")` to `self._record_story("escalated")`.

Run: `uv run pytest tests/test_orchestrate.py -q -k "a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next"`
Expected: FAIL, because the `_statuses(...)` assertion shows story B as `"escalated"` where `"stopped"` was expected.

Revert it: `git checkout -- src/agent_manager/orchestrate.py`

Second mutation: in `StoryRecorder.subtask_done`, delete the line `self._completed.append(subtask_id)`.

Run: `uv run pytest tests/test_orchestrate.py -q -k "a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next"`
Expected: FAIL, because `assert result["completed"] == [b1]` gets `[]`.

Revert it: `git checkout -- src/agent_manager/orchestrate.py`

If either mutation does not fail, the oracle doesn't cover that part of the recorder. Stop and report it rather than adding a test, since the spec forbids new tests without a spec change.

- [ ] **Step 10: Confirm the tree is back at the commit and still green**

Run: `git status --porcelain`
Expected: no output.

Run: `uv run pytest -q`
Expected: all pass, with the same count as Step 1.
