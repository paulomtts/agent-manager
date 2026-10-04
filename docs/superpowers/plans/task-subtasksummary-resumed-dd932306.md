<!-- task-pipeline: validated -->
# Report the resume point from `SubtaskSummary.resumed_at`, and document the worktree re-ensure — subtask dd932306

Parent story ab65eee1 ("A resume survives a worktree deleted outside am's bookkeeping"). Milestone design: `docs/superpowers/specs/2026-10-03-resume-worktree-reensure-design.md` §3.4, §3.5, §3.6, §3.7, §4 (docs paragraph), §5 (first risk). Builds on siblings cf03b236 (`steps/worktree.py` `ensure` re-adds a stale-registered missing path) and f76af5b2 (the engine's resume branch, `_worktree_kept`, the `ensure_worktree` seam, and `SubtaskSummary.resumed_at`), both already present in this worktree.

## Scope

Two read-site substitutions, one test-fake adjustment, and README text. Nothing else.

1. `src/agent_manager/cli.py`, the `am resume` payload of a `task` run (line 1965): `"resumed_from": phase` becomes `"resumed_from": summary.resumed_at`. The `phase = checkpoint_resume_phase(...)` call at 1893-1895 stays: it still refuses a done / phase-escalated / digest-mismatched checkpoint before any write. Only its value stops being reported. If `phase` is then unused, drop the binding but keep the call.
2. `src/agent_manager/orchestrate.py`, the lane's `comments.compose_done(...)` (1316-1326): `resumed_at=None if checkpoint is None else runtime_engine.pending_phase(checkpoint)` becomes `resumed_at=summary.resumed_at`. Update the adjacent comment to say it is where the walk actually continued, `None` on a fresh or declined walk.
3. README.md (this worktree):
   - In "## Resuming: what runs again" (line 514 on), add a paragraph and the three-case table: worktree intact (nothing runs, no warning, continues at the checkpoint's pending phase); worktree missing, branch survives (worktree added again for the branch, one warning, continues at the pending phase); worktree missing and branch gone, or the re-add fails (checkpoint not resumed, one warning, the subtask is walked from its first phase, `worktree`, as a fresh walk, whose own step reports a real git failure as an ordinary escalation at `worktree`). Quote the three warning lines exactly as `engine._worktree_kept` produces them:
     - `checkpoint #<seq> of run <run-id>: worktree <path> was missing and was added again for branch '<branch>'; resuming at '<phase>'`
     - `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and branch '<branch>' no longer exists); starting from the first phase`
     - `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and could not be added again: GitError: <message>); starting from the first phase`
     State that after a decline `data.resumed_from` is `null` and a milestone lane's `done` comment carries no `(resumed at <phase>)` line, and that the declined checkpoint row is not deleted, only superseded by the fresh walk's newer checkpoint.
   - In the `am resume` prose (line 39), after "keeps the suite and the opt-out the run started with", add the decline exception: when the checkpoint is declined because its worktree could not be kept, the fresh walk uses the `--verify` / `--allow-no-verification` passed now, not the kept suite, and a `verification: kept from checkpoint: [...]` warning shown beside the decline warning is then stale (spec §5, first risk).
   - Docs are written from the landed code in this worktree (`runtime/engine.py` `run_subtask_async` / `_worktree_kept`, `steps/worktree.py` `ensure`), not paraphrased from the milestone spec.

Out of scope: `runtime/engine.py` (any worktree logic), `runtime/walk.py` (the field already exists; do not move it to `SubtaskDrive`, `bases.py` reads the summary), `steps/worktree.py`, `bases.py`, `cli.checkpoint_resume_phase`, `runs.continuable_checkpoint`, `orchestrate.resume_checkpoints`, `kept_warning` ordering, and every test sibling f76af5b2 already fixed (`tests/runtime/test_resume.py`, `test_exactly_once.py`, `test_cancellation.py`, `test_stop_bridge.py`, task-run resume fixtures). `cli.py` / `orchestrate.py` must not learn about worktrees (§3.7 "one call site"). README line 567 (`(resumed at <phase>)` follows "when a milestone run picked the subtask up from a checkpoint") stays as is.

## Observable behaviour

- Intact worktree (every existing resume): unchanged. `resumed_from` / `(resumed at <phase>)` equal the checkpoint's pending phase, because the engine sets `resumed_at = pending_phase(resume_from)` on the kept path.
- Worktree re-added: same pending phase reported; the re-added warning rides `data.warnings` / the lane's warnings through the existing plumbing.
- Declined: `data.resumed_from` is `null`; the lane's `done` comment has no `(resumed at ...)` line.
- Error paths: none new. Refusals (`CheckpointMismatch`, done / escalated checkpoints) still happen before any write via `checkpoint_resume_phase` and the engine's digest check; a missing worktree is a recovery, never a refusal (§3.7), which is why the reported value is read from the post-walk summary.

## Correction to the exploration findings

The findings claimed `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` (tests/test_orchestrate.py:6242) passes unchanged. It does not: it drives `CheckpointDriver` (test_orchestrate.py:3624), a `FakeDriver` that never reaches the engine and returns `SubtaskSummary(status="done")` with `resumed_at=None`, so after substitution 2 the `(resumed at review)` line disappears. `CheckpointDriver` must mirror the engine's kept path: when `resume_from` is a checkpoint (not `_ABSENT` / `None`), set the returned summary's `resumed_at` to `runtime_engine.pending_phase(resume_from)`. The findings also called the `tests/test_cli.py` resume tests git-tier; they are marked `@pytest.mark.brd` + `@pytest.mark.git`, i.e. the opt-in `brd` tier. The exploration summary was truncated at 8000 characters mid-sentence ("Any README-only doc change n..."), a sign the upstream stage over-ran its brief; nothing past that point was relied on.

## Tests

Tier per the CLAUDE.md placement rule (chosen by what the test spawns; the `project` / `cards` fixtures in test_cli.py and test_orchestrate.py run the real `brd` binary, hence `brd`).

1. `brd` — existing `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` (test_orchestrate.py) passes with the adjusted `CheckpointDriver`; the existing "no `(resumed at`" assertion at test_orchestrate.py:6067 still holds.
2. `brd` — new, test_orchestrate.py: a lane handed a checkpoint whose driver reports a declined walk (summary `resumed_at=None`, e.g. a `CheckpointDriver` option or subclass) posts a `done` comment with no `(resumed at` line. Proves the lane reads the summary, not the checkpoint.
3. `brd` — existing test_cli.py assertions `resumed_from == "plan"` / `"validate_spec"` (5319, 5724, 5746, 6921, 7134) pass unchanged through the real engine's fast path.
4. `brd` — new, test_cli.py: `am resume` of a `task` run crashed after a checkpoint, with the worktree removed (`shutil.rmtree`) and its branch deleted (`git update-ref -d refs/heads/<branch>`, since `git branch -D` is refused while the stale registration stands), returns `data.resumed_from is None` and the "no longer exists" decline line in `data.warnings`.
5. Unit — existing `tests/test_comments.py` `compose_done` tests are untouched (the comment formatter does not change).
6. README changes need no test.

Default `uv run pytest` must stay green; run `uv run pytest -m brd` for 1-4.

---

# Report the resume point from `SubtaskSummary.resumed_at` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `am resume`'s `data.resumed_from` and a milestone lane's `done` comment `(resumed at <phase>)` line report where the walk actually continued (`SubtaskSummary.resumed_at`), and document the worktree re-ensure / decline behaviour in the README.

**Architecture:** The engine (`runtime/engine.py`, already landed by sibling f76af5b2) decides whether a checkpoint is kept and sets `summary.resumed_at`. The two callers stop recomputing the phase from the checkpoint before the walk and read the post-walk summary instead. No caller learns about worktrees. The orchestrate test fake `CheckpointDriver` is taught to mimic the engine's kept path so the lane's tests keep observing a `resumed_at`.

**Tech Stack:** Python 3, Typer CLI, pytest (tiers via markers), `uv`.

**Spec:** `docs/superpowers/specs/task-subtasksummary-resumed-dd932306-design.md` (prepended verbatim above). Milestone design: `docs/superpowers/specs/2026-10-03-resume-worktree-reensure-design.md`.

**Working directory for every command:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-subtasksummary-resumed-dd932306` (branch `m19/task-subtasksummary-resumed-dd932306`). All paths below are relative to it.

## Global Constraints

- Do not touch `src/agent_manager/runtime/engine.py`, `src/agent_manager/runtime/walk.py`, `src/agent_manager/steps/worktree.py`, `src/agent_manager/bases.py`, `cli.checkpoint_resume_phase`, `runs.continuable_checkpoint`, `orchestrate.resume_checkpoints`, or the `kept_warning` ordering in `cli._resume_from_checkpoint`.
- Do not touch tests sibling f76af5b2 already fixed: `tests/runtime/test_resume.py`, `tests/runtime/test_exactly_once.py`, `tests/runtime/test_cancellation.py`, `tests/runtime/test_stop_bridge.py`, and the task-run resume fixtures in `tests/test_cli.py` (`_crash_pygents`, `_resume_factory`, `recording_runner`).
- `cli.py` / `orchestrate.py` must not learn about worktrees: the edits are pure read-site substitutions of `summary.resumed_at`.
- The `checkpoint_resume_phase(...)` call in `cli._resume_from_checkpoint` stays (it refuses before any write); only its return value stops being reported.
- `SubtaskSummary.resumed_at` stays on `SubtaskSummary`, not `SubtaskDrive`.
- README warning lines are quoted exactly as `engine._worktree_kept` builds them (engine.py:275-289); `<seq>`, `<run-id>`, `<path>`, `<branch>`, `<phase>`, `<message>` are the only placeholders.
- README line 567 (`(resumed at <phase>)` follows the first line "When a milestone run picked the subtask up from a checkpoint") stays as is.
- Test tiers: every test touched or added here lives in `tests/test_cli.py` or `tests/test_orchestrate.py` and is marked `@pytest.mark.brd` and `@pytest.mark.git` (the `project` / `cards` fixtures run the real `brd`). Run them with `uv run pytest -m brd`; `uv run pytest` (default unit + git tiers) must stay green.
- No hard-wrapped prose in the README additions: one paragraph per line, as the surrounding README does.

## Review Focus

1. A `--card` resume whose worktree was deleted but whose branch survives: a user expects `data.resumed_from` to still name the checkpoint's pending phase and `data.warnings` to carry the "was added again" line. Pinned by `test_a_pygents_resume_with_its_worktree_deleted_re_adds_it_and_resumes_at_plan` in Task 2.
2. A `--card` resume whose worktree and branch are both gone: `data.resumed_from` must be `null` (not the stale checkpoint phase) and the "no longer exists" line must be in `data.warnings`. Pinned by `test_a_pygents_resume_with_worktree_and_branch_gone_declines_the_checkpoint` in Task 2.
3. A milestone lane whose driver declined the checkpoint it was handed: the `done` comment must not claim `(resumed at <phase>)`. Pinned by `test_a_lane_whose_walk_declined_its_checkpoint_posts_done_without_resumed_at` in Task 1.
4. A milestone lane that resumed (kept) a checkpoint: the `done` comment still has `(resumed at review)`. Pinned by the existing `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` with the fixed `CheckpointDriver` in Task 1.
5. A decline under a `--verify` that differs from the kept suite: the `verification: kept from checkpoint: [...]` warning is computed before the walk and is then stale. The spec makes this a documentation item only (§5, first risk); it is covered by the README text in Task 3, with no behaviour change and no test.

---

### Task 1: The milestone lane's `done` comment reads `summary.resumed_at`

**Files:**
- Modify: `tests/test_orchestrate.py:3623-3631` (`CheckpointDriver`)
- Test: `tests/test_orchestrate.py` (new test right after `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review`, which ends at line 6269)
- Modify: `src/agent_manager/orchestrate.py:1316-1326`

**Interfaces:**
- Consumes: `SubtaskSummary.resumed_at: str | None` (`src/agent_manager/runtime/walk.py:258`), `runtime_engine.pending_phase(checkpoint: Checkpoint) -> str | None` (`src/agent_manager/runtime/engine.py:42`), `cli.SubtaskDrive(summary, warnings)` (frozen dataclass, `cli.py:592`).
- Produces: `CheckpointDriver.declined: set[str]` — card ids for which the fake reports a declined walk (summary `resumed_at` stays `None` even though a checkpoint was handed in). For every other card handed a checkpoint, the fake returns `resumed_at = runtime_engine.pending_phase(resume_from)`.

- [ ] **Step 1: Teach `CheckpointDriver` the engine's kept path and a `declined` option**

In `tests/test_orchestrate.py`, replace the class at lines 3623-3631:

```python
@dataclass
class CheckpointDriver(FakeDriver):
    """`FakeDriver` that also takes `resume_from` and records it per card."""

    resumed: dict[str, Any] = field(default_factory=dict)

    async def __call__(self, *, resume_from: Any = _ABSENT, **kwargs: Any) -> cli.SubtaskDrive:
        self.resumed[kwargs["card"].id] = resume_from
        return await super().__call__(**kwargs)
```

with:

```python
@dataclass
class CheckpointDriver(FakeDriver):
    """`FakeDriver` that also takes `resume_from` and records it per card.

    Like the engine's kept path, a card handed a checkpoint comes back with
    `summary.resumed_at` set to that checkpoint's pending phase. A card in
    `declined` stands for a walk whose checkpoint was declined (its worktree
    could not be kept) and started over: `resumed_at` stays `None`.
    """

    resumed: dict[str, Any] = field(default_factory=dict)
    declined: set[str] = field(default_factory=set)

    async def __call__(self, *, resume_from: Any = _ABSENT, **kwargs: Any) -> cli.SubtaskDrive:
        card_id = kwargs["card"].id
        self.resumed[card_id] = resume_from
        drive = await super().__call__(**kwargs)
        if resume_from is _ABSENT or resume_from is None or card_id in self.declined:
            return drive
        summary = replace(
            drive.summary, resumed_at=runtime_engine.pending_phase(resume_from)
        )
        return replace(drive, summary=summary)
```

(`replace` is already imported from `dataclasses` at line 34 and `runtime_engine` at line 46.)

- [ ] **Step 2: Write the failing test**

In `tests/test_orchestrate.py`, insert directly after `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` (after its last line, `assert run_end_rows == [(key, "posted") for key in milestone_keys]`):

```python
@pytest.mark.brd
@pytest.mark.git
def test_a_lane_whose_walk_declined_its_checkpoint_posts_done_without_resumed_at(project):
    """Resume worktree re-ensure §3.6: the lane hands a1 a checkpoint, but the
    walk declined it and started over (`summary.resumed_at` is None), so the
    `done` comment names no resume point. The lane reads the summary, not the
    checkpoint it handed in."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, milestone, FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",))
    driver = CheckpointDriver(declined={a1})

    result = _resume(project, run_id, driver)

    assert result["done"] is True, result
    assert driver.resumed[a1] is not _ABSENT
    found = _comments(project, a1)
    keys = _keys(found)
    assert keys[-1] == f"{run_id}/{a1}/done", keys
    assert "(resumed at" not in found[-1].body
```

- [ ] **Step 3: Run the new test to verify it fails**

Run: `uv run pytest -m brd tests/test_orchestrate.py::test_a_lane_whose_walk_declined_its_checkpoint_posts_done_without_resumed_at -v`
Expected: FAIL on `assert "(resumed at" not in found[-1].body` — the lane still computes `runtime_engine.pending_phase(checkpoint)` from the checkpoint it handed in, so the body contains `(resumed at review)`.

- [ ] **Step 4: Substitute the read site in the lane**

In `src/agent_manager/orchestrate.py`, replace lines 1316-1326:

```python
                        comments.compose_done(
                            run_id=run_id,
                            card_id=subtask.id,
                            summary=summary,
                            branch=row.branch,
                            # Where the walk picked up, only when the lane
                            # handed the driver a checkpoint as `resume_from`.
                            resumed_at=None
                            if checkpoint is None
                            else runtime_engine.pending_phase(checkpoint),
                        ),
```

with:

```python
                        comments.compose_done(
                            run_id=run_id,
                            card_id=subtask.id,
                            summary=summary,
                            branch=row.branch,
                            # Where the walk actually continued, as the engine
                            # reports it: `None` on a fresh walk, and on one
                            # whose checkpoint was declined and started over.
                            resumed_at=summary.resumed_at,
                        ),
```

(`checkpoint` and `runtime_engine` are still used elsewhere in the function and module — lines 1251-1255 and 699 — so no import or binding becomes unused.)

- [ ] **Step 5: Run the new test and the existing lane resume / comment tests**

Run: `uv run pytest -m brd tests/test_orchestrate.py -k "declined_its_checkpoint or resumed_at_review or one_done_comment_on_each_subtask or relaunch_continues or checkpoint_lookup_that_fails" -v`
Expected: PASS for all of them — the new test; `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` (the fixed fake supplies `resumed_at="review"`); `test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story` (its `(resumed at` absence at line 6067 still holds); the two `CheckpointDriver` tests at 3664 and 3695.

- [ ] **Step 6: Run the whole orchestrate module in both tiers**

Run: `uv run pytest -m brd tests/test_orchestrate.py && uv run pytest tests/test_orchestrate.py`
Expected: PASS (every other `CheckpointDriver` user — lines 4821, 4902, 4967, 5162 — sees the same `resumed_at` the old lane computed).

- [ ] **Step 7: Commit**

```bash
git add tests/test_orchestrate.py src/agent_manager/orchestrate.py
git commit -m "fix: a lane's done comment names the phase the walk actually resumed at"
```

---

### Task 2: `am resume` of a `task` run reports `summary.resumed_at`

**Files:**
- Test: `tests/test_cli.py` (two new tests right after `test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint`, which ends at line 5324)
- Modify: `src/agent_manager/cli.py:1893-1895` and `src/agent_manager/cli.py:1965`

**Interfaces:**
- Consumes: `SubtaskSummary.resumed_at` (set by `runtime_engine.run_subtask_async`, engine.py:177-193, 242), the warning lines built by `engine._worktree_kept` (engine.py:269-289), existing test helpers in `tests/test_cli.py`: `_crash_pygents(project, cards, phase) -> str` (5137), `_resume_factory(seen=None, crash_at=None, crash_with=KeyboardInterrupt)` (4723), `_checkpoint_rows(root) -> list[tuple]` rows `(run_id, card_id, seq, reason, digest)` (5164), `_git(cwd, *args) -> str` (1196), `store_module.open_db`, `store_module.load_run`, `cli.find_subtask(run, card_id) -> (story, subtask) | None`, `cli.resolve_repo_dir`. `shutil` is imported at line 20.
- Produces: helper `_resumable_subtask(project: Path, run_id: str, card_id: str) -> models.SubtaskRun` (the recorded subtask row, giving `branch` and `worktree_path`) and `_newest_seq(project: Path, run_id: str, card_id: str) -> int`, both local to `tests/test_cli.py`.

- [ ] **Step 1: Write the failing test (decline) and the pin test (re-added)**

In `tests/test_cli.py`, insert directly after `test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint` (after its last line, `assert board.show(cards["subtask"], repo_dir=project).status == "done"`):

```python
def _resumable_subtask(project: Path, run_id: str, card_id: str) -> models.SubtaskRun:
    """The subtask row `run_id` recorded for `card_id`: its branch and worktree."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    found = cli.find_subtask(run, card_id)
    assert found is not None
    return found[1]


def _newest_seq(project: Path, run_id: str, card_id: str) -> int:
    """The seq of `card_id`'s newest checkpoint in `run_id`: the one a resume reads."""
    return max(
        row[2]
        for row in _checkpoint_rows(cli.resolve_repo_dir(project))
        if row[0] == run_id and row[1] == card_id
    )


@pytest.mark.brd
@pytest.mark.git
def test_a_pygents_resume_with_its_worktree_deleted_re_adds_it_and_resumes_at_plan(
    project, cards
):
    """Resume worktree re-ensure §3.2 case 2: the branch survives, so the
    worktree is added again, the checkpoint is kept and `resumed_from` still
    names its pending phase, with the re-added line in `data.warnings`."""
    run_id = _crash_pygents(project, cards, "plan")
    subtask = _resumable_subtask(project, run_id, cards["subtask"])
    seq = _newest_seq(project, run_id, cards["subtask"])
    shutil.rmtree(subtask.worktree_path)
    seen: list[str] = []

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory(seen))

    assert seen[0] == "plan"
    assert payload["status"] == "done"
    assert payload["resumed_from"] == "plan"
    assert (
        f"checkpoint #{seq} of run {run_id}: worktree {subtask.worktree_path} was missing"
        f" and was added again for branch '{subtask.branch}'; resuming at 'plan'"
    ) in payload["warnings"]


@pytest.mark.brd
@pytest.mark.git
def test_a_pygents_resume_with_worktree_and_branch_gone_declines_the_checkpoint(
    project, cards
):
    """Resume worktree re-ensure §3.2 case 3 and §3.6: the branch is gone too,
    so the checkpoint is declined and the subtask is walked from its first
    phase. `resumed_from` is then `null`, not the checkpoint's `plan`."""
    run_id = _crash_pygents(project, cards, "plan")
    subtask = _resumable_subtask(project, run_id, cards["subtask"])
    seq = _newest_seq(project, run_id, cards["subtask"])
    root = cli.resolve_repo_dir(project)
    shutil.rmtree(subtask.worktree_path)
    # git refuses `branch -D` while the stale registration still claims the
    # branch, which is why the branch is deleted through `update-ref` here.
    _git(root, "update-ref", "-d", f"refs/heads/{subtask.branch}")

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["resumed_from"] is None
    assert payload["status"] == "done"
    assert (
        f"checkpoint #{seq} of run {run_id} was not resumed (worktree"
        f" {subtask.worktree_path} is missing and branch '{subtask.branch}' no longer"
        " exists); starting from the first phase"
    ) in payload["warnings"]
    assert set(payload) == RESUME_KEYS
```

(`models` and `store_module` are already imported via the `from agent_manager import (...)` block at line 36 — `_force_started` at 5181 uses both; `RESUME_KEYS` is defined at 5119.)

- [ ] **Step 2: Run both new tests**

Run: `uv run pytest -m brd tests/test_cli.py -k "worktree_deleted_re_adds_it or worktree_and_branch_gone" -v`
Expected: `test_a_pygents_resume_with_its_worktree_deleted_re_adds_it_and_resumes_at_plan` PASSES already (the kept path reports the same phase either way; it pins Review Focus 1). `test_a_pygents_resume_with_worktree_and_branch_gone_declines_the_checkpoint` FAILS on `assert payload["resumed_from"] is None` with `'plan' is not None`, because the payload still reports the pre-walk `checkpoint_resume_phase` value.

- [ ] **Step 3: Substitute the read site in the payload**

In `src/agent_manager/cli.py`, replace lines 1893-1895:

```python
            phase = checkpoint_resume_phase(
                checkpoint, card_id=subtask.card_id, run_id=run.id
            )
```

with:

```python
            # Refuses a done, phase-escalated or digest-mismatched checkpoint
            # before any write. The phase it names is not reported: the walk
            # may decline the checkpoint (its worktree could not be kept), so
            # `resumed_from` is read from the summary after the walk.
            checkpoint_resume_phase(checkpoint, card_id=subtask.card_id, run_id=run.id)
```

and replace line 1965:

```python
            "resumed_from": phase,
```

with:

```python
            "resumed_from": summary.resumed_at,
```

- [ ] **Step 4: Run the new tests and every existing `resumed_from` assertion**

Run: `uv run pytest -m brd tests/test_cli.py -k "worktree_deleted_re_adds_it or worktree_and_branch_gone or resumes_at_plan or resumed_from or resume" -v`
Expected: PASS, including the existing assertions at test_cli.py lines 5319, 5724, 5746, 6921 and 7134 (`"plan"` / `"validate_spec"`), which go through the engine's intact-worktree fast path.

- [ ] **Step 5: Run the whole cli module in both tiers**

Run: `uv run pytest -m brd tests/test_cli.py && uv run pytest tests/test_cli.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_cli.py src/agent_manager/cli.py
git commit -m "fix: am resume reports resumed_from from the walk, null after a declined checkpoint"
```

---

### Task 3: README — what a resume does with a missing worktree

**Files:**
- Modify: `README.md:39` (the `am resume` paragraph)
- Modify: `README.md:334` (the `task`-run resume paragraph, `resumed_from` sentence)
- Modify: `README.md:530-532` (end of "## Resuming: what runs again")

**Interfaces:**
- Consumes: the landed behaviour of `engine._worktree_kept` (engine.py:248-290): no path or an existing directory keeps the checkpoint with no git and no warning; a missing directory gets one `worktree.ensure` call; `branch_existed` true keeps it with the "added again" line; `branch_existed` false (ensure has cut the branch again from its base) or any exception declines it; `walk._render_error` renders an exception as `<TypeName>: <message>`. `run_subtask_async` (engine.py:192-198): a decline sets `resume_from = None`, so the walk starts fresh from the first phase, and the declined row is neither deleted nor rewritten.
- Produces: README text only.

- [ ] **Step 1: Add the decline exception to the `am resume` paragraph**

In `README.md` line 39, replace the sentence:

```
A walk continued from a checkpoint keeps the suite and the opt-out the run started with, so on a `--card` run `--verify` and `--allow-no-verification` have no effect.
```

with:

```
A walk continued from a checkpoint keeps the suite and the opt-out the run started with, so on a `--card` run `--verify` and `--allow-no-verification` have no effect. The exception is a checkpoint declined because its worktree could not be kept (see [Resuming: what runs again](#resuming-what-runs-again)): that subtask is walked again from its first phase with the `--verify` commands and `--allow-no-verification` passed now, not the kept suite, and a `verification: kept from checkpoint: [...]` warning shown beside the decline warning is then stale.
```

- [ ] **Step 2: Note the `null` in the `task`-run resume paragraph**

In `README.md` line 334, replace:

```
`data` names the phase the walk continued at as `resumed_from` and lists the marked attempts as `discarded_attempts`.
```

with:

```
`data` names the phase the walk continued at as `resumed_from` (`null` when the checkpoint was declined and the walk started over, see [Resuming: what runs again](#resuming-what-runs-again)) and lists the marked attempts as `discarded_attempts`.
```

- [ ] **Step 3: Add the worktree cases to "## Resuming: what runs again"**

In `README.md`, insert after the paragraph that starts `Exactly-once covers am's dispatch of an agent phase` (line 532) and before `## What the board records`, separated by blank lines:

````
Before a checkpoint is continued, the subtask's worktree is checked. A worktree deleted outside `am` (an `rm -rf`, a `git worktree remove`) is a recovery, never a refusal:

| The subtask's worktree on resume | What happens | Warning | Where the walk goes on |
|---|---|---|---|
| Present (or the subtask has none) | Nothing runs: no git, no warning. | none | At the checkpoint's pending phase. |
| Missing, its branch still exists | The worktree is added again for the branch, which keeps its commits. | one, "added again" | At the checkpoint's pending phase. |
| Missing, and its branch is gone too, or adding it again fails | The checkpoint is not resumed. | one, "was not resumed" | From the subtask's first phase, `worktree`, as a fresh walk. When the branch was gone it is cut again from its base; a real git failure is reported by the `worktree` step as an ordinary escalation at `worktree`. |

The three warning lines, in `data.warnings` on a `--card` run and in the run's warnings on a milestone run:

```
checkpoint #<seq> of run <run-id>: worktree <path> was missing and was added again for branch '<branch>'; resuming at '<phase>'
checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and branch '<branch>' no longer exists); starting from the first phase
checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and could not be added again: GitError: <message>); starting from the first phase
```

After a decline, `data.resumed_from` is `null` and a milestone run's `done` comment for that subtask carries no `(resumed at <phase>)` line: both report where the walk actually went on, not the checkpoint it was handed. The declined checkpoint row is not deleted or rewritten; the fresh walk's first checkpoint, saved at a higher seq, supersedes it.
````

- [ ] **Step 4: Check the README against the landed code**

Read `src/agent_manager/runtime/engine.py:248-290` and confirm, character for character, that each of the three warning lines in Step 3 matches the f-strings there with `{label}` = `checkpoint #<seq> of run <run-id>`, `{path}` = `<path>`, `{subtask.branch}` = `<branch>`, `{pending_phase(checkpoint)}` = `<phase>`, and `{walk._render_error(error)}` = `GitError: <message>`. Fix the README if any differ; do not touch the engine.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: what a resume does when the subtask's worktree is missing"
```

---

### Task 4: Full verification

**Files:** none.

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest`
Expected: PASS (unit + git tiers).

- [ ] **Step 2: Run the opt-in `brd` tier, where every test this plan touched lives**

Run: `uv run pytest -m brd`
Expected: PASS, including `test_a_lane_whose_walk_declined_its_checkpoint_posts_done_without_resumed_at`, `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review`, `test_a_pygents_resume_with_its_worktree_deleted_re_adds_it_and_resumes_at_plan` and `test_a_pygents_resume_with_worktree_and_branch_gone_declines_the_checkpoint`.

- [ ] **Step 3: Confirm the off-limits files are untouched**

Run: `git diff --stat m19/task-run-subtask-async-s-f76af5b2...HEAD`
Expected: only `src/agent_manager/cli.py`, `src/agent_manager/orchestrate.py`, `tests/test_cli.py`, `tests/test_orchestrate.py`, `README.md` (plus this plan / the spec under `docs/superpowers/`). No `runtime/engine.py`, `runtime/walk.py`, `steps/worktree.py`, `bases.py`, or `tests/runtime/*`.
