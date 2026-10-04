<!-- task-pipeline: validated -->
# A relaunch and an `am resume` both see a reset run correctly — subtask design

Card 522adfb5 (story 816cb8b3 "am reset closes a run nobody is driving", milestone 246a77c3). Narrows `docs/superpowers/specs/2026-10-03-am-reset-design.md` §3.6, §3.7 and §4 tests 8, 9, 12 to this subtask.

## Scope

This subtask adds proof and documentation only. The `am reset RUN_ID` command, its refusals, `took_over`, `already_cancelled`, `cards`/`open_in` and the `DeadRunError` message pointer already landed with siblings 736d6728 and af52db54, and they are present on this worktree's branch (`src/agent_manager/cli.py:2349` `reset_run`, `cli.py:2441` `@app.command("reset")`, `cli.py:2046-2049` the cancelled-run refusal in `resume_run`, `cli.py:2167-2173` the dead-run message). Do not re-implement or reshape any of that.

No production code change is expected. Relaunch already resolves `runs.continuable_checkpoint(store, subtask.id)` (`orchestrate.py:1253-1255`), which goes through `Store.latest_open_checkpoint`'s cancelled-run exclusion, and passes `resume_from` only when the result is non-None. `worktree.ensure` already recreates a removed worktree and re-cuts a deleted branch. If a test shows one of these does not hold, stop and report it. Do not patch around it here.

Out of scope, carried from the story: `am reset` writing any git or brd state; re-ensuring the worktree on resume (that is the separate `2026-10-03-resume-worktree-reensure-design.md`); journal/DB divergence detection. The project-wide "never" rules still hold: no raw SQL against the projection from a command, no write outside `_fenced()` once a token is bound, no push, no `main`/`master`.

## Observable behaviour to prove (§3.6)

- **Relaunch after reset.** A new `am run --milestone` dispatches a card whose newest checkpoint belongs to a reset (now `cancelled`) run with no `resume_from`. The card's walk starts at the `worktree` phase. This holds whether the checkpoint's worktree directory still exists or was removed by hand.
- **Resume of the reset run.** `am resume <reset-run-id>` raises `NotResumableError` with exactly `run <id> was cancelled; start new work with \`am run --milestone\``, which is the same wording an `am cancel`led run gets. It exits 3 for both `task` and `milestone` workflows and writes nothing, because the check precedes `Store.open`.

## Documentation (§3.7), README.md

Write this from the command and envelope fields as they actually landed on this branch, not from the spec's plan.

- In "Pausing and cancelling a run", after the Ctrl-C/pause/cancel list, add one paragraph introducing `am reset <run-id>`. It covers these points:
  - It is for a run nobody is driving: one that crashed, or one that is terminal and was torn down by hand.
  - It records the run `cancelled` exactly as a cancel would, so everything the section says about a cancelled run applies.
  - It writes no git and touches no card.
  - A repeat reports `already_cancelled`.
  - It has three refusals: an unknown run, a live run (with a pointer to `am cancel`), and a `done` run.
  - It reports the cards it checkpointed in `cards`, with `open_in` naming the run a relaunch would still adopt from.
- The `DeadRunError` bullet (currently README.md:400) gains the `am reset` pointer that the landed message already carries.
- In "Relaunching resumes", the sentence "A relaunch after an `am cancel` ignores the cancelled run's checkpoints…" (README.md:322) gains "or an `am reset`".
- The watch section's `run_upsert` row needs no change.

## Tests

Tier placement follows CLAUDE.md's rule, restated in design spec §14 (`2026-09-23-agent-manager-design.md`): a test's tier is chosen by what it actually spawns or touches, not by its directory, and a test is marked explicitly when the directory default does not fit.

1. **Relaunch after reset starts fresh (spec test 8, main body).** Tier: `unit`, unmarked. It runs through `orchestrate` with `FakeDriver` and the store, with no subprocess. Model it on `tests/test_orchestrate.py:3678` (`test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh`) and place it in `tests/test_orchestrate.py`.
   - Setup: give a card an open checkpoint under run X, then reset X through `cli.reset_run`/the command, not a hand-written status.
   - Assertions: the relaunch dispatches the card with no `resume_from` key, and its walk begins at `worktree`.
   - Parametrize it twice: the checkpoint's worktree directory present, and absent.
2. **Git companion: `worktree.ensure` recreates and re-cuts (spec test 8, git proof).** Tier: `git`. It spawns real `git` in `tmp_path`.
   - Both cases already exist in `tests/steps/test_worktree.py`:
     - `test_an_rm_rf_worktree_whose_branch_survives_is_re_added_with_its_commits` (:990) covers a surviving branch whose worktree was removed.
     - `test_an_rm_rf_worktree_whose_branch_is_also_gone_is_re_cut_from_base` (:1032) covers a deleted branch.
   - Both currently rely on the directory auto-mark. Satisfy the companion by adding an explicit `@pytest.mark.git` to these two tests rather than duplicating them.
   - Add a new test only if the implementer finds a §3.6 case they do not cover.
3. **`am resume` of a reset run refuses (spec test 9).** Tier: `unit`, unmarked. It uses the `projection` fixture with `brd`/`git`/`claude` stubbed off `PATH`. Place it in `tests/test_cli.py`.
   - Parametrize it over `workflow` ∈ {`task`, `milestone`}.
   - Setup: plant a resettable run and reset it with `am reset`.
   - Assertions: `am resume` gives exit 3, envelope `{"type": "NotResumableError", "message": "run <id> was cancelled; start new work with \`am run --milestone\`"}`, and `_resume_guard_state` is unchanged.
   - Reuse the shape of `test_resume_writes_nothing_when_it_refuses` (:4789) and `test_resume_refuses_a_cancelled_run_and_writes_nothing` (:6732), including `_forbid_resume`.
4. **The 2026-10-03 incident, replayed (spec test 12).** Tier: `e2e_fake`, marked explicitly with `@pytest.mark.e2e_fake`. It uses production wiring and spawns the fake `claude` per phase.
   - Placement: under `tests/e2e/`. `test_production_wiring.py` was suggested. The closest existing analogue is `tests/e2e/test_live_control.py:322` (cancel, then relaunch from scratch), and putting the test beside it reuses its hold/pause helpers and fixtures. The plan stage picks one module and states why.
   - Steps:
     1. Pause a milestone mid-subtask under the fake `claude`.
     2. Remove that subtask's worktree directory by hand.
     3. Run `am reset <run-id>` and assert exit 0 and the envelope.
     4. Run a fresh `am run --milestone`.
   - Assertions:
     - The card is driven from `worktree` to `done`.
     - No phase is continued from the reset run's checkpoint.
     - `am resume` of the reset run refuses.

Verification: `uv run pytest` covers tests 1–3, and `uv run pytest -m e2e_fake` covers test 4.

Note: the exploration summary handed to this stage was truncated at 8000 of 9257 characters, mid "File:line ref" section, which means that stage over-ran its brief. The references above were re-checked directly against this worktree rather than taken from the missing text.

---

# A relaunch and an `am resume` both see a reset run correctly — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, with tests at the right tier, that after `am reset <run-id>` a relaunch starts the run's cards fresh at `worktree` (worktree present or removed) and `am resume <run-id>` refuses exactly as for a cancelled run, then document `am reset` in the README.

**Architecture:** No production code changes. Four test additions (an orchestrate-level relaunch test through `FakeDriver`, explicit `git` markers on two existing `worktree.ensure` tests, a CLI resume-refusal test, and one `e2e_fake` incident replay) plus three README edits. Every test exercises code that already landed on this branch (`cli.reset_run` at `src/agent_manager/cli.py:2349`, the cancelled refusal in `resume_run` at `cli.py:2046-2049`, `runs.continuable_checkpoint` at `src/agent_manager/runs.py:216`, `Store.latest_open_checkpoint` at `src/agent_manager/store.py:1555`, `worktree.ensure` in `src/agent_manager/steps/worktree.py`).

**Tech Stack:** Python, pytest (tier markers in `tests/conftest.py`), Typer `CliRunner`, SQLite projection via `agent_manager.store`, real `git` in `tmp_path`, the fake `claude` in `tests/e2e/`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-a-relaunch-and-an-am-522adfb5/docs/superpowers/specs/task-a-relaunch-and-an-am-522adfb5-design.md` (reproduced above), narrowing `docs/superpowers/specs/2026-10-03-am-reset-design.md` §3.6, §3.7, §4 tests 8, 9, 12.

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-a-relaunch-and-an-am-522adfb5`, branch `m19/task-a-relaunch-and-an-am-522adfb5`, cut from `m19/task-am-reset-reports-which-af52db54`. All paths below are relative to that worktree. Every command runs from that worktree's root.

## Global Constraints

- No production code change. If a test fails with the reset in place, stop and report it; do not patch `src/` (spec "Scope").
- `am reset` writes no git and no brd state; do not add any (spec "Scope").
- No raw SQL against the projection from a command, no write outside `_fenced()` once a token is bound, no push, no `main`/`master` (spec "Scope").
- Tier placement: by what the test actually spawns or touches, not its directory; mark explicitly when the directory default does not fit (CLAUDE.md "Placement rule", design spec §14).
- Unmarked (`unit`) tests run with `brd`/`git`/`claude` stubs that exit 99 first on `PATH` (`tests/conftest.py:286-321`); per-test budgets are unit ≤0.5s, git ≤2s (`tests/conftest.py:263-264`).
- Exact refusal text: `run <id> was cancelled; start new work with \`am run --milestone\`` (`cli.py:2047-2049`).
- Exact reset message: `run <id> is cancelled; \`am resume <id>\` refuses it, and a relaunch starts its cards from their first phase` (`cli.py:2343-2346`).
- Reset envelope keys: `run_id`, `previous_status`, `status`, `already_cancelled`, `cards`, `message`, plus `took_over` only when a dead holder's lease was taken over (`cli.py:2423-2438`).
- Verification: `uv run pytest` (default `unit` + `git` tiers) and `uv run pytest -m e2e_fake` for the incident replay.

### Deviation from the spec, stated once

Spec test 1 says "Tier: `unit`, unmarked". Its own named model, `tests/test_orchestrate.py:3678`, runs on the `project` fixture (`tests/test_orchestrate.py:980-1006`), which runs `git init`/`git commit` with real `git`, and the test itself needs `git worktree add` to make the worktree "present". Under the unit tier the `git` stub exits 99, so the fixture would fail before the test body runs. By the placement rule the spec defers to, the test touches real `git` and nothing else (the board is the in-memory `FakeBoard` and the driver is `FakeDriver`), so it is `@pytest.mark.git` and not `brd`. Precedent for git-only on this fixture: `tests/test_orchestrate.py:6286` (`test_a_lane_whose_walk_declined_its_checkpoint_posts_done_without_resumed_at`). It still runs under `uv run pytest`, so the spec's verification line holds.

### Module choice for the incident replay

Spec test 4 leaves the module to this stage. It goes in `tests/e2e/test_live_control.py`, beside `test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch` (:322). That module already has `_hold`, `_signal_when_applied`, `_control_while_held`, `_resume`, `_status`, `_error`, `_counts`, `_only` and `_card_phase_counts` for exactly a pause-then-relaunch milestone flow. `tests/e2e/test_production_wiring.py` is built around a single `cli.run_card` (`completed_run`, `worktree` fixtures) and has none of the milestone control helpers.

## Review Focus

1. A card whose newest checkpoint is the reset run's but an earlier, non-cancelled run still holds an older open row: a person expects the relaunch to start fresh (matching `open_in: null`), not to fall back to the stale older row. Test: `test_a_relaunch_after_am_reset_does_not_fall_back_to_an_older_runs_open_row` (Task 1).
2. A card with a newer open row under another run than the reset one: a person expects `open_in` to name that run and the relaunch to actually continue from it, so the envelope is truthful. Test: `test_a_relaunch_after_am_reset_continues_the_run_open_in_names` (Task 1).
3. A run that crashed mid-turn (a `turn` checkpoint, not a pause's `parked`): reset must close it the same way. Test: the `reason` parametrization of `test_a_relaunch_after_am_reset_starts_the_card_fresh_at_worktree` (Task 1).
4. A run reset twice (the second reports `already_cancelled: true`): `am resume` must still refuse with the same wording and write nothing. Test: the `resets` parametrization of `test_resume_refuses_a_reset_run_as_cancelled_and_writes_nothing` (Task 3).
5. A refused resume of a reset run must leave the checkpoint rows untouched, so a later relaunch's `open_in` reasoning is unchanged. Test: the `_checkpoint_rows` assertion in `test_resume_refuses_a_reset_run_as_cancelled_and_writes_nothing` (Task 3).

---

### Task 1: A relaunch after `am reset` starts the card fresh (orchestrate, `git` tier)

**Files:**
- Modify: `tests/test_orchestrate.py:28` (add `import shutil` after `import shlex`)
- Modify: `tests/test_orchestrate.py` — insert a new section immediately before the line `# ── merged bases (supervisor-tree §5, card 8eca88e2) ────────────────────────` (currently line 3729)

**Interfaces:**
- Consumes (all existing in `tests/test_orchestrate.py`): `project` fixture (:980), `_milestone(project, stories)` (:1009), `_branch(project, card_id) -> str` (:1039), `FakeDriver` (:1043, `calls[i]["worktree"]` is the recorded `SubtaskRun.worktree_path`), `_run(project, milestone, driver, **overrides)` (:1102), `_git(cwd, *args) -> str` (:915), `LATER` (:903), `EARLIER` (:3619), `_ABSENT` (:3616), `CheckpointDriver` (:3623, `resumed[card_id]` is the `resume_from` handed in or `_ABSENT`), `_plant(project, run_id, card_id, reason, *, digest=None, queue=("implement",), minute=0) -> store_module.Checkpoint` (:3648). From production: `cli.reset_run(run_id, *, repo_dir) -> dict` (`cli.py:2349`), `cli.mint_run_id(milestone_id, datetime) -> str`, `runs.continuable_checkpoint(store, card_id)` (`runs.py:216`), `task_workflow.TASK.phases[0].name`.
- Produces: `_continuable(project, run_id, card_id) -> store_module.Checkpoint | None` and `_escalated_first_run(project) -> tuple[str, str, str, Path]` (module-private helpers used only by this task's tests).

- [ ] **Step 1: Add the `shutil` import**

In `tests/test_orchestrate.py`, change:

```python
import shlex
import socket
```

to:

```python
import shlex
import shutil
import socket
```

- [ ] **Step 2: Write the tests**

Insert immediately before the line `# ── merged bases (supervisor-tree §5, card 8eca88e2) ────────────────────────`:

```python
# ── a relaunch after `am reset` starts the card fresh (card 522adfb5) ───────
#
# am-reset spec §3.6 / test 8, through the lane with `FakeDriver`. Git tier,
# not unit: the `project` fixture and the worktree under test need real git;
# the board is the in-memory FakeBoard, so no `brd` marker.

FIRST_PHASE = "worktree"
"""`TASK`'s first phase: where a walk handed no `resume_from` begins."""


def _continuable(project: Path, run_id: str, card_id: str) -> store_module.Checkpoint | None:
    """What a relaunch's lane would continue `card_id` from, read as the lane reads it."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        return runs.continuable_checkpoint(opened, card_id)
    finally:
        opened.close()


def _escalated_first_run(project: Path) -> tuple[str, str, str, Path]:
    """A one-story, one-subtask milestone run once and escalated at a1's review.

    Returns (milestone id, a1, the run id, a1's recorded worktree path). The
    run ended, so its lease is released and `am reset` finds nobody driving it.
    `FakeDriver` saves no checkpoint: each test plants the rows it needs.
    """
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first_driver = FakeDriver(outcomes={a1: ("review", "boom")})
    first = _run(project, shape["milestone"], first_driver)
    assert first["escalated"] is True, first
    return shape["milestone"], a1, first["run_id"], Path(first_driver.calls[0]["worktree"])


@pytest.mark.git
@pytest.mark.parametrize("reason", ["parked", "turn"])
@pytest.mark.parametrize(
    "worktree_present", [True, False], ids=["worktree-present", "worktree-removed"]
)
def test_a_relaunch_after_am_reset_starts_the_card_fresh_at_worktree(
    project, worktree_present, reason
):
    """am-reset spec test 8: a1's newest checkpoint is an open row of run X.
    Before the reset a relaunch would continue it; after `cli.reset_run(X)`
    the lane hands a1 no `resume_from`, so its walk begins at `worktree`,
    whether a1's worktree directory survived or was removed by hand. Review
    Focus 3: a crash's `turn` row closes the same way as a pause's `parked`."""
    milestone, a1, reset_id, wt = _escalated_first_run(project)
    planted = _plant(project, reset_id, a1, reason, queue=("review",))
    _git(project, "worktree", "add", "-b", _branch(project, a1), str(wt), "main")
    if not worktree_present:
        shutil.rmtree(wt)
    found = _continuable(project, reset_id, a1)
    assert found is not None
    assert (found.run_id, found.seq) == (reset_id, planted.seq)

    reset = cli.reset_run(reset_id, repo_dir=project)

    assert reset["status"] == "cancelled"
    assert reset["already_cancelled"] is False
    assert reset["cards"] == [
        {"card_id": a1, "workflow": task_workflow.TASK.name, "open_in": None}
    ]
    assert _continuable(project, reset_id, a1) is None
    driver = CheckpointDriver()

    result = _run(project, milestone, driver, clock=lambda: LATER)

    assert result["done"] is True, result
    assert result["run_id"] != reset_id
    assert result["completed"] == [a1]
    assert [call["card"] for call in driver.calls] == [a1]
    assert driver.resumed[a1] is _ABSENT
    assert driver.calls[0]["worktree"] == wt
    assert task_workflow.TASK.phases[0].name == FIRST_PHASE
    assert wt.is_dir() is worktree_present


@pytest.mark.git
def test_a_relaunch_after_am_reset_does_not_fall_back_to_an_older_runs_open_row(project):
    """Review Focus 1: an earlier run with no `runs` row (so not cancelled)
    holds an older open row of a1, and the reset run holds the newest. The
    newest row decides, so a1 is closed: `open_in` is null and the relaunch
    starts a1 fresh rather than continuing the older row."""
    milestone, a1, reset_id, _wt = _escalated_first_run(project)
    older = cli.mint_run_id(milestone, EARLIER)
    _plant(project, older, a1, "parked", minute=0)
    _plant(project, reset_id, a1, "parked", queue=("review",), minute=5)

    reset = cli.reset_run(reset_id, repo_dir=project)

    assert reset["cards"] == [
        {"card_id": a1, "workflow": task_workflow.TASK.name, "open_in": None}
    ]
    driver = CheckpointDriver()
    result = _run(project, milestone, driver, clock=lambda: LATER)
    assert result["done"] is True, result
    assert driver.resumed[a1] is _ABSENT


@pytest.mark.git
def test_a_relaunch_after_am_reset_continues_the_run_open_in_names(project):
    """Review Focus 2: another run (no `runs` row, so not cancelled) saved a
    newer open row of a1 than the reset run did. The reset reports that run
    in `open_in`, and the relaunch does continue a1 from exactly that row:
    the envelope tells the truth about what a relaunch adopts."""
    milestone, a1, reset_id, _wt = _escalated_first_run(project)
    _plant(project, reset_id, a1, "parked", queue=("review",), minute=0)
    other = cli.mint_run_id(milestone, EARLIER.replace(minute=30))
    newer = _plant(project, other, a1, "parked", queue=("implement",), minute=5)

    reset = cli.reset_run(reset_id, repo_dir=project)

    assert reset["cards"] == [
        {"card_id": a1, "workflow": task_workflow.TASK.name, "open_in": other}
    ]
    driver = CheckpointDriver()
    result = _run(project, milestone, driver, clock=lambda: LATER)
    assert result["done"] is True, result
    got = driver.resumed[a1]
    assert got is not _ABSENT
    assert (got.run_id, got.seq) == (other, newer.seq)
```

- [ ] **Step 3: RED — prove the main test can fail without the reset**

These tests characterise code that already landed, so RED is shown by removing the behaviour under test. Temporarily replace the line `    reset = cli.reset_run(reset_id, repo_dir=project)` in `test_a_relaunch_after_am_reset_starts_the_card_fresh_at_worktree` with `    reset = {"status": "cancelled", "already_cancelled": False, "cards": [{"card_id": a1, "workflow": task_workflow.TASK.name, "open_in": None}]}` and run:

Run: `uv run pytest "tests/test_orchestrate.py::test_a_relaunch_after_am_reset_starts_the_card_fresh_at_worktree" -v`
Expected: 4 FAILED, each at `assert _continuable(project, reset_id, a1) is None` (the run is still `escalated`, so its row is still continuable).

Then restore the line to `    reset = cli.reset_run(reset_id, repo_dir=project)`.

- [ ] **Step 4: GREEN — run the three tests with the reset in place**

Run: `uv run pytest tests/test_orchestrate.py -k "relaunch_after_am_reset" -v`
Expected: 6 passed (4 parametrizations + 2 Review Focus tests). If any fails with the reset in place, stop and report it with the failure output (spec "Scope": no production patch here).

- [ ] **Step 5: Commit**

```bash
git add tests/test_orchestrate.py
git commit -m "test: a relaunch after am reset starts the card fresh at worktree"
```

---

### Task 2: Mark the `worktree.ensure` recovery tests `git` explicitly

**Files:**
- Modify: `tests/steps/test_worktree.py:990` (`test_an_rm_rf_worktree_whose_branch_survives_is_re_added_with_its_commits`)
- Modify: `tests/steps/test_worktree.py:1032` (`test_an_rm_rf_worktree_whose_branch_is_also_gone_is_re_cut_from_base`)

**Interfaces:**
- Consumes: nothing new; `pytest` is already imported at `tests/steps/test_worktree.py:17`.
- Produces: nothing other tasks use.

These two tests already prove spec test 8's git companion (a surviving branch whose worktree was `rm -rf`ed is re-added with its commits; a deleted branch is re-cut from base). They are `git` today only through the `tests/steps/` directory auto-mark (`tests/conftest.py:128`). The spec asks for an explicit marker, not a duplicate. There is no behaviour change, so there is no RED step; the check is that they still pass when selected by `-m git`.

- [ ] **Step 1: Add the marker to the surviving-branch test**

Change:

```python
def test_an_rm_rf_worktree_whose_branch_survives_is_re_added_with_its_commits(
    repo: Path, tmp_path: Path
):
```

to:

```python
@pytest.mark.git
def test_an_rm_rf_worktree_whose_branch_survives_is_re_added_with_its_commits(
    repo: Path, tmp_path: Path
):
```

- [ ] **Step 2: Add the marker to the deleted-branch test**

Change:

```python
def test_an_rm_rf_worktree_whose_branch_is_also_gone_is_re_cut_from_base(
    repo: Path, tmp_path: Path
):
```

to:

```python
@pytest.mark.git
def test_an_rm_rf_worktree_whose_branch_is_also_gone_is_re_cut_from_base(
    repo: Path, tmp_path: Path
):
```

- [ ] **Step 3: Run them under `-m git`**

Run: `uv run pytest tests/steps/test_worktree.py -m git -k "rm_rf_worktree_whose_branch" -v`
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/steps/test_worktree.py
git commit -m "test: mark the worktree.ensure recovery tests git explicitly (am-reset test 8 companion)"
```

---

### Task 3: `am resume` of a reset run refuses, both workflows (CLI, unit tier)

**Files:**
- Modify: `tests/test_cli.py` — append a new section at the end of the file (after `test_a_cancel_of_a_dead_run_points_at_am_resume_and_am_reset`, currently ending at line 9171)

**Interfaces:**
- Consumes (all existing in `tests/test_cli.py`): `projection` fixture (:3902), `runner` (module `CliRunner`), `CONTROL_RUN_ID` (:6225), `_freeze_clock(monkeypatch)` (:6242), `_plant_run(root, *, status, workflow)` (:6247), `_forbid_resume(monkeypatch)` (:6720), `_resume_guard_state(root) -> tuple` (:6726), `_checkpoint_rows(root) -> list[tuple]` (:5165), `_invoke_reset(root, run_id=CONTROL_RUN_ID, *extra)` (:8604), `_recorded_status(root, run_id=CONTROL_RUN_ID) -> str | None` (:8612), `_plant_parked_checkpoint(root, ...)` (:8620). From production: `cli.EXIT_ERROR`.
- Produces: nothing other tasks use.

Unit tier: the `projection` fixture is a plain directory plus `XDG_DATA_HOME`; reset and the refused resume touch only the SQLite projection and the journal, so no subprocess runs and the 99-exit stubs on `PATH` are never hit.

- [ ] **Step 1: Write the test**

Append to `tests/test_cli.py`:

```python


# ── am resume of a reset run (card 522adfb5) ────────────────────────────────
#
# am-reset spec §3.6 / test 9: a run closed by `am reset` is refused by
# `am resume` exactly as an `am cancel`led one is, before `Store.open`. Unit
# tier: the projection fixture and the store only, no subprocess.


@pytest.mark.parametrize("resets", [1, 2], ids=["reset-once", "reset-twice"])
@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_resume_refuses_a_reset_run_as_cancelled_and_writes_nothing(
    projection, monkeypatch, workflow, resets
):
    """am-reset spec test 9, both workflows, with the assertion shape of
    `test_resume_refuses_a_cancelled_run_and_writes_nothing`. Review Focus 4:
    a second reset (`already_cancelled: true`) changes nothing about the
    refusal. Review Focus 5: the refusal leaves every checkpoint row as the
    reset left it."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="stopped", workflow=workflow)
    _plant_parked_checkpoint(projection)
    for n in range(resets):
        reset = _invoke_reset(projection)
        assert reset.exit_code == 0, reset.output
        assert json.loads(reset.stdout)["data"]["already_cancelled"] is (n > 0)
    assert _recorded_status(projection) == "cancelled"
    before = _resume_guard_state(projection)
    checkpoints_before = _checkpoint_rows(projection)
    _forbid_resume(monkeypatch)

    result = runner.invoke(cli.app, ["resume", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR == 3, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "NotResumableError",
        "message": (
            f"run {CONTROL_RUN_ID} was cancelled;"
            " start new work with `am run --milestone`"
        ),
    }
    assert _resume_guard_state(projection) == before
    assert _checkpoint_rows(projection) == checkpoints_before
    assert _recorded_status(projection) == "cancelled"
```

- [ ] **Step 2: RED — prove the test can fail without the reset**

Temporarily delete these five lines from the test body:

```python
    for n in range(resets):
        reset = _invoke_reset(projection)
        assert reset.exit_code == 0, reset.output
        assert json.loads(reset.stdout)["data"]["already_cancelled"] is (n > 0)
    assert _recorded_status(projection) == "cancelled"
```

Run: `uv run pytest tests/test_cli.py -k "resume_refuses_a_reset_run" -v`
Expected: 4 FAILED. The run is still `stopped`, so `resume_run` gets past the cancelled check and reaches a `_Forbidden` stand-in (`_resume_from_checkpoint` for `task`, `orchestrate.run_milestone` for `milestone`), which calls `pytest.fail("the milestone dry run reached cli....")`.

Then restore the five lines exactly as in Step 1.

- [ ] **Step 3: GREEN — run it with the reset in place**

Run: `uv run pytest tests/test_cli.py -k "resume_refuses_a_reset_run" -v`
Expected: 4 passed. If any fails with the reset in place, stop and report it (no production patch here).

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: am resume refuses a reset run as cancelled for both workflows"
```

---

### Task 4: The 2026-10-03 incident, replayed under the fake `claude` (`e2e_fake`)

**Files:**
- Modify: `tests/e2e/test_live_control.py:22-30` (imports: add `import shutil` and `import pytest`)
- Modify: `tests/e2e/test_live_control.py` — append a helper and a test at the end of the file (after line 393)

**Interfaces:**
- Consumes (all existing in `tests/e2e/test_live_control.py`): `_git(cwd, *args) -> str` (:64), `_local_branches(root) -> list[str]` (:71), `_envelope(result) -> dict` (:75), `_error(result) -> dict` (:82), `_status(root, run_id) -> str` (:100), `_resume(root, run_id)` (:107), `_card_phase_counts(entries) -> Counter` (:125), `_counts(full=(), partial=None) -> Counter` (:132), `_only(counts, card) -> Counter` (:145), `_hold(monkeypatch, card, phase, entered, release)` (:150), `_signal_when_applied(monkeypatch, applied)` (:182), `_control_while_held(root, milestone, command, *, run_milestone_cli, entered, release, applied) -> (run_id, control_data, result)` (:216), `HELD_PHASE = "plan"` (:52). Fixtures from `tests/e2e/conftest.py`: `two_story_board` (:473, keys `root`, `milestone`, `stories`, `subtasks`, `branches`), `run_milestone_cli` (:540), `read_fake_log` (:577). From production: `cli.worktree_for(root, branch) -> Path`, `cli.resolve_repo_dir`, `store.open_db`, `store.load_run(conn, run_id) -> models.Run | None`.
- Produces: `_subtask_of(root, run_id, card_id) -> models.SubtaskRun` (module-private helper, this task only).

Tier: `e2e_fake`, marked explicitly. It spawns the fake `claude` per agent phase through `cli.default_runner_factory`, real `git`, and the board the e2e conftest builds; `tests/e2e/test_live_control.py` is not in `_AUTO_MARK_EXEMPT` (`tests/conftest.py:136`), so the explicit marker agrees with the directory default.

- [ ] **Step 1: Add the imports**

In `tests/e2e/test_live_control.py`, change:

```python
import json
import subprocess
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from typer.testing import CliRunner
```

to:

```python
import json
import shutil
import subprocess
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
```

- [ ] **Step 2: Write the helper and the test**

Append to `tests/e2e/test_live_control.py`:

```python


def _subtask_of(root: Path, run_id: str, card_id: str):
    """`card_id`'s recorded `SubtaskRun` in `run_id`, phases in the order the walk recorded them."""
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    for story in run.stories:
        for subtask in story.subtasks:
            if subtask.card_id == card_id:
                return subtask
    raise AssertionError(f"{card_id} is not recorded in run {run_id}")


@pytest.mark.e2e_fake
def test_a_reset_of_a_paused_milestone_whose_worktree_was_removed_relaunches_it_from_worktree(
    two_story_board, run_milestone_cli, read_fake_log, monkeypatch, request
):
    """am-reset spec test 12, the 2026-10-03 incident replayed: a milestone
    paused in a1's plan, a1's worktree removed by hand, `am reset` closes the
    run, `am resume` refuses it, and a fresh `am run --milestone` drives a1
    from `worktree` to `done` with every agent phase run once, `explore`
    included: nothing is continued from the reset run's checkpoint."""
    assert request.node.get_closest_marker("e2e") is None
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    (a1,) = two_story_board["subtasks"]["A"]
    branch = two_story_board["branches"][a1]
    worktree = cli.worktree_for(root, branch)
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    _hold(monkeypatch, a1, HELD_PHASE, entered, release)
    _signal_when_applied(monkeypatch, applied)

    run_id, _requested, first = _control_while_held(
        root,
        milestone,
        "pause",
        run_milestone_cli=run_milestone_cli,
        entered=entered,
        release=release,
        applied=applied,
    )

    # Paused: a1 parked after its held plan, the run recorded `stopped`.
    assert first.exit_code == 0, (first.output, first.exception)
    assert _envelope(first)["paused"] is True
    assert _status(root, run_id) == "stopped"
    paused_counts = _card_phase_counts(read_fake_log(run_id))
    assert _only(paused_counts, a1) == _counts(partial={a1: HELD_PHASE}), paused_counts

    # The incident: a1's worktree removed by hand; its branch survives.
    assert worktree.is_dir()
    shutil.rmtree(worktree)
    assert not worktree.exists()
    assert branch in _local_branches(root)

    reset = CliRunner().invoke(cli.app, ["reset", run_id, "--repo-dir", str(root)])

    assert reset.exit_code == 0, (reset.output, reset.exception)
    closed = _envelope(reset)
    assert set(closed) == {
        "run_id",
        "previous_status",
        "status",
        "already_cancelled",
        "cards",
        "message",
    }, closed
    assert closed["run_id"] == run_id
    assert closed["previous_status"] == "stopped"
    assert closed["status"] == "cancelled"
    assert closed["already_cancelled"] is False
    assert {"card_id": a1, "workflow": "task", "open_in": None} in closed["cards"], closed
    assert all(card["open_in"] is None for card in closed["cards"]), closed["cards"]
    assert closed["message"] == (
        f"run {run_id} is cancelled; `am resume {run_id}` refuses it,"
        " and a relaunch starts its cards from their first phase"
    )
    assert _status(root, run_id) == "cancelled"

    # `am resume` of the reset run is refused at exit 3, launching nothing.
    launches = len(read_fake_log(run_id))
    refused = _resume(root, run_id)

    assert refused.exit_code == cli.EXIT_ERROR == 3, (refused.output, refused.exception)
    assert _error(refused) == {
        "type": "NotResumableError",
        "message": f"run {run_id} was cancelled; start new work with `am run --milestone`",
    }
    assert len(read_fake_log(run_id)) == launches

    relaunch = run_milestone_cli(root, milestone)

    assert relaunch.exit_code == 0, (relaunch.output, relaunch.exception)
    finished = _envelope(relaunch)
    assert finished["done"] is True, finished
    assert "escalated" not in finished
    new_run = finished["run_id"]
    assert new_run != run_id
    assert a1 in finished["completed"]
    assert set(finished["integrated"]["merged"]) == set(stories.values())
    # Driven from `worktree` to `done`: the walk's first recorded phase is the
    # worktree step, and it recreated the removed directory on a1's branch.
    subtask = _subtask_of(root, new_run, a1)
    assert subtask.status == "done", subtask
    assert subtask.phases[0].name == "worktree", [p.name for p in subtask.phases]
    assert subtask.phases[0].status == "done"
    assert worktree.is_dir()
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    # Nothing continued from the reset run's parked checkpoint: every agent
    # phase of a1 exactly once in the relaunch, `explore` included.
    relaunched = _card_phase_counts(read_fake_log(new_run))
    assert _only(relaunched, a1) == _counts(full=(a1,)), relaunched
    assert relaunched[(a1, "explore")] == 1
    # The reset run stays cancelled.
    assert _status(root, run_id) == "cancelled"
```

- [ ] **Step 3: RED — prove the test can fail without the reset**

Temporarily replace the line `    reset = CliRunner().invoke(cli.app, ["reset", run_id, "--repo-dir", str(root)])` with `    reset = CliRunner().invoke(cli.app, ["status", run_id, "--repo-dir", str(root)])` and run:

Run: `uv run pytest -m e2e_fake "tests/e2e/test_live_control.py::test_a_reset_of_a_paused_milestone_whose_worktree_was_removed_relaunches_it_from_worktree" -v`
Expected: FAILED at the `assert set(closed) == {...}` assertion (a status envelope, not a reset one; the run is still `stopped`).

Then restore the line to `    reset = CliRunner().invoke(cli.app, ["reset", run_id, "--repo-dir", str(root)])`.

- [ ] **Step 4: GREEN — run it with the reset in place**

Run: `uv run pytest -m e2e_fake "tests/e2e/test_live_control.py::test_a_reset_of_a_paused_milestone_whose_worktree_was_removed_relaunches_it_from_worktree" -v`
Expected: 1 passed. If it fails with the reset in place, stop and report it with the output (no production patch here; a failure in the relaunch's `worktree` step would be a §3.6 finding, not something to work around).

- [ ] **Step 5: Run the whole module's tier to check the neighbours still pass**

Run: `uv run pytest -m e2e_fake tests/e2e/test_live_control.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/test_live_control.py
git commit -m "test: replay the 2026-10-03 incident: reset a paused milestone with a removed worktree and relaunch"
```

---

### Task 5: Document `am reset` in the README

**Files:**
- Modify: `README.md:322` (the "Relaunching resumes" cancel sentence)
- Modify: `README.md:400` (the `DeadRunError` bullet)
- Modify: `README.md:407-409` (insert one paragraph after the Ctrl-C/pause/cancel list)

**Interfaces:**
- Consumes: the landed command and envelope (`cli.py:2318-2460`): refusals `UnknownRunError`, `RunIsLiveError` (points at `am cancel <run-id>`), `NotResettableError` (`done`); payload keys `run_id`, `previous_status`, `status`, `already_cancelled`, `cards` (`{"card_id", "workflow", "open_in"}`), `message`, optional `took_over` (`{"pid", "host", "heartbeat_at"}`); options `--repo-dir` (default `.`) and `--pretty`; the `DeadRunError` wording `` `am resume <run-id>` picks it up, or `am reset <run-id>` closes it `` (`cli.py:2164-2174`).
- Produces: nothing other tasks use.

There is no README test in this repo, so this task has no RED step; Step 4 checks the text landed.

- [ ] **Step 1: Add "or an `am reset`" to the relaunch sentence**

In `README.md`, replace:

```
A relaunch after an `am cancel` ignores the cancelled run's checkpoints, so a subtask the cancel parked starts again from its first phase.
```

with:

```
A relaunch after an `am cancel` or an `am reset` ignores the cancelled run's checkpoints, so a subtask that run left parked starts again from its first phase.
```

- [ ] **Step 2: Add the `am reset` pointer to the `DeadRunError` bullet**

Replace:

```
- `DeadRunError`: the run is recorded `started`, but no process holds its lease, or the lease is dead. Nobody is left to act on a request. `am resume <run-id>` picks the run up.
```

with:

```
- `DeadRunError`: the run is recorded `started`, but no process holds its lease, or the lease is dead. Nobody is left to act on a request. `am resume <run-id>` picks the run up, or `am reset <run-id>` closes it.
```

- [ ] **Step 3: Add the `am reset` paragraph after the Ctrl-C/pause/cancel list**

Replace:

```
- **Cancel** parks the same way, but closes the run for good. `am resume` refuses it. A relaunch with `am run --milestone` ignores the cancelled run's checkpoints, so a subtask the cancel parked starts again from its first phase. A cancel does not reset board cards: they keep whatever status the run left them in.

`am resume` refuses, with exit code 3
```

with:

```
- **Cancel** parks the same way, but closes the run for good. `am resume` refuses it. A relaunch with `am run --milestone` ignores the cancelled run's checkpoints, so a subtask the cancel parked starts again from its first phase. A cancel does not reset board cards: they keep whatever status the run left them in.

`am reset <run-id>` closes a run nobody is driving: one whose process crashed, or one that stopped or escalated and whose worktrees you then tore down by hand. It takes `--repo-dir` (default `.`) and `--pretty`, and records the run `cancelled` exactly as a cancel would, so everything this section says about a cancelled run applies to it: `am resume` refuses it, and a relaunch with `am run --milestone` starts its subtasks again from their first phase, `worktree`, which recreates a worktree directory that is gone. It writes no git and touches no board card. It exits 0 and prints `{"ok": true, "data": {"run_id", "previous_status", "status": "cancelled", "already_cancelled", "cards", "message"}}`, plus `took_over` (`{"pid", "host", "heartbeat_at"}`) when a dead process still held the run's lease. Resetting a run that is already cancelled writes nothing and reports `already_cancelled: true`. `cards` has one `{"card_id", "workflow", "open_in"}` per card the run saved a checkpoint for: `open_in` is `null` when a relaunch starts that card fresh, and names another run when that run holds the card's newest open checkpoint, so a relaunch would still continue the card from it. It refuses, with `{"ok": false, "error": {"type", "message"}}`, exit code 3 and nothing written, an unknown run (`UnknownRunError`), a run a live process still holds (`RunIsLiveError`, which points at `am cancel <run-id>` instead), and a run that finished `done` (`NotResettableError`).

`am resume` refuses, with exit code 3
```

- [ ] **Step 4: Check the text landed**

Run: `grep -n "am reset" README.md`
Expected: three lines — the "Relaunching resumes" paragraph (around line 322), the `DeadRunError` bullet (around line 400), and the new `am reset <run-id>` paragraph (around line 409).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document am reset and point the relaunch and DeadRunError text at it"
```

---

### Task 6: Full verification

**Files:** none changed.

**Interfaces:** none.

- [ ] **Step 1: Default suite**

Run: `uv run pytest`
Expected: all pass, including the 6 new tests in `tests/test_orchestrate.py` (git tier), the 4 in `tests/test_cli.py` (unit tier) and the 2 re-marked tests in `tests/steps/test_worktree.py`; no tier-budget failure (`unit-tier budget exceeded` / `git-tier budget exceeded`).

- [ ] **Step 2: The opt-in tier the incident replay lives in**

Run: `uv run pytest -m e2e_fake tests/e2e/test_live_control.py`
Expected: 3 passed.

- [ ] **Step 3: Confirm no production file changed**

Run: `git diff --stat m19/task-am-reset-reports-which-af52db54 -- src/`
Expected: no output.
