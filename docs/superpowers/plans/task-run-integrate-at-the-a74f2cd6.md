<!-- task-pipeline: validated -->
# Subtask a74f2cd6: Run Integrate at the end of `run_milestone` and show it in the dry run

Parent story 006d0a0e "Integrate in the runner". This subtask narrows decisions I1, I5 and I6 of `docs/superpowers/specs/2026-09-25-integrate-design.md` (section 3) and covers acceptance items 5 and 6 (section 4). It builds on sibling 6fea51ad (done), which delivered `src/agent_manager/integration.py` (`integrate_milestone`, `integration_branch`, `merge_order`, `IntegrateSuccess`, `IntegrateEscalation`), `steps/integrate.py`, `integrate.yaml` and the resolver role. Before coding, confirm that the working base contains those files. They exist in this worktree.

## Scope

In scope:
- `src/agent_manager/orchestrate.py`: `run_milestone` calls `integration.integrate_milestone` and uses the result to decide the run status and the payload.
- `src/agent_manager/cli.py`: `dry_run_payload` and `dry_run_milestone` add an `integrate` plan. Existing tests that assert full payloads are updated.
- Tests in `tests/test_orchestrate.py` and `tests/test_cli.py`. Any e2e test that asserts the whole milestone payload (`tests/e2e/test_milestone_run.py`, `test_parallel_milestone.py`, `test_production_wiring.py`) is updated only for the new keys.

Out of scope:
- The internals of `integrate_milestone`, `merge_tip`, the resolver role, `integrate.yaml` and the gates.
- `tests/e2e/fake_claude.py` and its `resolve` phase, and the five Integrate e2e scenarios. Those belong to sibling a37460b9.
- `--no-integrate`, a milestone-aware `am resume`, watch/retry/cancel, cost capture, per-story readiness and slow-test marking.

## Observable behaviour

1. **Where it runs.** `run_milestone` walks every level. If no lane escalated or stopped, it then calls `integrate_milestone(plan.stories, root, base_branch, branch_prefix, commands, allow_no_verification, store, run_id, runner_factory)`. Only after that call does it record the run's final status. The escalation early-return inside the level loop does not change: Integrate never runs after a lane escalation.
2. **Runner factory.** `run_milestone` accepts `runner_factory=None`, but `integrate_milestone` requires one. Resolve `None` to `cli.default_runner_factory`, the same way `cli.py:703` does. Tests inject a fake factory.
3. **Success.** Record the run as `done`. The payload keeps `done, run_id, levels, completed, tips, warnings` and adds `integrated: {branch, worktree, merged, resolved}`. `worktree` is a `str`. `merged` and `resolved` are lists of story ids.
4. **Integrate escalation.** Record the run as `escalated`. Return `{escalated: true, phase: "integrate", story, files, detail, run_id, warnings}`. The integration branch and worktree stay exactly as `integrate_milestone` left them (I5). No cleanup happens.
5. **Nothing left to run.** When every story is already `done` (the levels are empty or every lane is a no-op), Integrate still runs, and no early return may skip it. Two consequences:
   - A relaunch after an Integrate escalation retries Integrate.
   - A relaunch of a finished, integrated milestone reports `done` and leaves the integration branch tip unchanged.
6. **Base branch.** Integrate never moves the base branch tip and never pushes. Tests assert that the base tip is unchanged.
7. **Exit code.** `am run --milestone` already exits non-zero when `payload.get("escalated") is True` (around `cli.py:1119-1127`). The Integrate escalation payload carries `escalated: true`, so it exits non-zero with no change to the check. A test must assert this.
8. **Dry run.** `dry_run_payload` gains an `integrate` key with this shape: `{branch, worktree, order: [{story, tip}]}`.
   - `branch` is `integration.integration_branch(branch_prefix)`.
   - `worktree` is `str(cli.worktree_for(repo_dir, branch))`.
   - `order` comes from `integration.merge_order(stories, branch_prefix, base_branch)`. That order is the `compute_integrate_levels` order over all stories, in census order within each level. Stories with no subtasks are skipped.

   `dry_run_payload` stays pure. It gains a required keyword-only `repo_dir: Path` parameter so it can derive the worktree path (every existing `cli.dry_run_payload(...)` call in `tests/test_cli.py` is updated to pass it), and `dry_run_milestone` passes `root` for it. The dry run still writes nothing: no Store or run directory, no fetch, no worktree, no branch and no board write.
9. **Circular import.** `integration` imports `cli` at module load. So `cli` imports `integration` inside the function, as it already does for `orchestrate` (see `cli.py:1088-1094`). `orchestrate` can import `integration` directly, as long as no cycle is introduced. Check that before choosing.

## Error paths

- `integrate_milestone` returns an `IntegrateEscalation`: covered by item 4 above.
- `integrate_milestone` raises. Examples are a non-conflict git failure, or `MergeInProgressError` when the stop is not already turned into an escalation. The exception propagates, `store.close()` still runs in the existing `finally`, and the run is not recorded `done`. Do not catch or reclassify beyond what `integrate_milestone` already returns.
- A lane escalates: the existing behaviour is unchanged, and `integrate_milestone` is never called. A test asserts that it was never called.

## Tests

Tier placement follows design section 14 of `2026-09-23-agent-manager-design.md` as applied in the `tests/test_orchestrate.py` docstring:
- Pure helpers get unit tests on hand-built plans.
- `run_milestone` and CLI tests run at the Steps tier: a real temporary git repo, a real temporary brd board, `XDG_DATA_HOME` under `tmp_path`, and the harness replaced at the injected `driver` and `runner_factory` seams.
- Production wiring under a fake `claude` is the e2e tier, which belongs to sibling a37460b9.

| # | Test | File | Tier |
|---|------|------|------|
| 1 | Success payload shape. A clean two-story milestone returns the existing keys plus `integrated{branch, worktree(str), merged, resolved=[]}`. The run is recorded `done`. The integration branch contains both tips. The base tip is unchanged. | `tests/test_orchestrate.py` | Steps (real repo + board, fake driver) |
| 2 | Integrate escalation payload. Final verification fails (for example, the commands include a failing one). The payload is `{escalated: true, phase: "integrate", story, files, detail, run_id, warnings}`. The run is recorded `escalated`. The integration branch and worktree still exist and are untouched. The base tip is unchanged. | `tests/test_orchestrate.py` | Steps |
| 3 | A lane escalation skips Integrate. There is no integration branch, and the escalation payload is unchanged. | `tests/test_orchestrate.py` | Steps |
| 4 | An all-done milestone still integrates. Every story is already done on the board. `run_milestone` runs no lanes, creates `<prefix>-integrate` and returns `integrated`. | `tests/test_orchestrate.py` | Steps |
| 5 | A relaunch after an Integrate escalation retries. The first run escalates at Integrate. The cause is fixed (commands made passing). A relaunch reports `done` with `integrated`. | `tests/test_orchestrate.py` | Steps |
| 6 | Relaunching a finished, integrated milestone is a no-op. The second run returns `done`, and the integration branch tip is identical to the tip after the first run. | `tests/test_orchestrate.py` | Steps |
| 7 | `dry_run_payload` over a hand-built census includes `integrate{branch, worktree, order}`. The order follows integrate levels, keeps census order within each level, and skips a story with no subtasks. Also update the existing full-payload assertions. | `tests/test_cli.py` (the existing `dry_run_payload` unit tests live there) | Pure unit |
| 8 | `dry_run_milestone` or `am run --milestone --dry-run` on a real repo and board. The output has the `integrate` plan, and nothing was written: no run directory under `XDG_DATA_HOME`, no `<prefix>-integrate` branch, no worktree directory and no board change. | `tests/test_cli.py` | Steps |
| 9 | `am run --milestone` exits non-zero on an Integrate escalation. Patch `orchestrate.run_milestone` to return the Integrate escalation payload, or drive it for real as in test 2. The JSON envelope carries the payload. | `tests/test_cli.py` | Steps |
| 10 | Existing full-payload assertions in `tests/test_orchestrate.py`, `tests/test_cli.py` and the milestone e2e tests are updated to expect `integrated` (and the dry-run `integrate` key). The assertions are not loosened. | as existing | the existing test's tier |

The whole default suite (`uv run pytest`, including `tests/e2e`) stays green.

---

# Run Integrate at the end of `run_milestone` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every level of a milestone finishes clean, `orchestrate.run_milestone` folds every story tip into `<prefix>-integrate` through `integration.integrate_milestone` and reports it, and `am run --milestone --dry-run` previews that Integrate plan without writing anything.

**Architecture:** `orchestrate.run_milestone` gains one call after the level loop, read at call time as `integration.integrate_milestone` so tests can replace that seam the same way they replace `driver`. Two small pure helpers in `orchestrate.py` shape the success and escalation payloads. `cli.dry_run_payload` gains a keyword-only `repo_dir` and an `integrate` key built from `integration.integration_branch`, `cli.worktree_for` and `integration.merge_order`, importing `integration` inside the function because `integration` imports `cli` at load.

**Tech Stack:** Python 3, Typer, Pydantic, pytest, real `git` and `brd` CLIs in Steps-tier fixtures, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-run-integrate-at-the-a74f2cd6/docs/superpowers/specs/task-run-integrate-at-the-a74f2cd6-design.md` (reproduced verbatim above). Parent design: `docs/superpowers/specs/2026-09-25-integrate-design.md`, decisions I1, I5, I6.

**Working directory for every command:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-run-integrate-at-the-a74f2cd6` (branch `m5/task-run-integrate-at-the-a74f2cd6`, cut from `m5/task-merge-every-story-tip-6fea51ad`).

## Global Constraints

- Verification command: `uv run pytest` (no separate lint or typecheck).
- Source under `src/agent_manager/`, tests mirror it under `tests/`.
- Internal state is a dataclass; Pydantic only at a process boundary. No new Pydantic model is needed here.
- CLI output stays the `{"ok": true, "data": ...}` envelope; `--pretty` indents it.
- Integrate never moves the base branch tip and never pushes. Every test that runs a real Integrate asserts the base tip (`main`) is unchanged.
- On an Integrate escalation the integration branch and worktree stay exactly as `integrate_milestone` left them: no cleanup.
- `cli` must not import `integration` or `orchestrate` at module load; `integration` imports `cli` at load.
- Do not touch `integrate_milestone` internals, `steps/integrate.py`, `integrate.yaml`, the resolver role, the gates, or `tests/e2e/fake_claude.py`.
- Do not build the five Integrate e2e scenarios (sibling a37460b9).
- Existing assertions are updated for the new keys, never loosened.
- The whole default suite, `tests/e2e` included, must be green at the end.

## Review Focus

- `runner_factory=None` must reach Integrate as `cli.default_runner_factory` read at call time (not bound at import), while lanes still receive `None` exactly as before. Pinned in Task 3 by `test_no_runner_factory_gives_integrate_cli_default_runner_factory_at_call_time`.
- `integrate_milestone` raising (a git failure that is not a conflict) must propagate and leave the run recorded `started`, never `done`. Pinned in Task 3 by `test_an_integrate_that_raises_propagates_and_the_run_is_never_recorded_done`.
- A dry run over a milestone whose first story is already done must still list that story in the Integrate order (Integrate covers every story, done or not). Pinned in Task 1 by `test_the_milestone_dry_run_shows_the_integrate_plan_and_writes_nothing`.
- A lane escalation must leave no `<prefix>-integrate` branch behind, in the Steps tier and through the production wiring. Pinned in Task 3 (`test_an_escalation_stops_the_run_before_the_next_story`) and Task 4 (`test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it`, `test_an_escalation_in_one_lane_stops_the_other_and_the_next_level_never_starts`).
- Importing `agent_manager.integration` first in a fresh interpreter must not break the `cli`/`orchestrate`/`integration` import triangle. Pinned in Task 3 by the extra `agent_manager.integration` parameter of `test_cli_and_orchestrate_import_cleanly_in_either_order`.

## A consequence the spec did not foresee (read before Task 4)

`tests/e2e/test_parallel_milestone.py` runs stories A and B as independent roots. The fake coder (`tests/e2e/fake_claude.py:388`) writes `IMPLEMENTATION.md` in every subtask, so once Integrate runs, merging B's tip after A's is an add/add conflict on that file. With no `resolve` phase in the fake (sibling a37460b9's job), every clean two-lane test would then escalate at Integrate and the suite would go red. This plan does not touch `fake_claude.py`. Instead Task 4 writes `IMPLEMENTATION.md merge=union` into the parallel board's `.git/info/attributes` (the git common dir, so it applies in every linked worktree and is in no tree). That is repo-level test scaffolding, like the review-fail marker: it tells the fake nothing, and git's built-in union driver folds the two versions so the lane tests never need a resolver. The conflict scenarios stay a37460b9's. Task 0 proves the union driver resolves add/add in a linked worktree on this machine's git before anything depends on it.

## File Structure

- Modify `src/agent_manager/cli.py`: `dry_run_payload` (lines 866-925) gains `repo_dir` and the `integrate` key; `dry_run_milestone` (lines 928-951) passes `repo_dir=root`.
- Modify `src/agent_manager/orchestrate.py`: import `integration`; add `integrated_payload` and `integrate_escalated_payload` after `escalated_payload` (line 158); call Integrate at the end of `run_milestone` (lines 561-572); refresh the `story_tips` and `run_milestone` docstrings.
- Modify `tests/test_cli.py`: pure dry-run tests (lines 1026-1226), Steps-tier dry-run tests (lines 2374-2441), milestone-run constants and exit-code test (lines 2754-2903), import-order test (line 2936).
- Modify `tests/test_orchestrate.py`: imports, docstring, Integrate seam fixtures and branch-making helpers, existing full-payload tests, and new pure, wiring and real-Integrate tests.
- Modify `tests/e2e/conftest.py` (`parallel_board`, lines 312-344), `tests/e2e/test_milestone_run.py`, `tests/e2e/test_parallel_milestone.py`: new-key assertions and the union attribute.

---

### Task 0: Preflight: the base holds sibling 6fea51ad's code, and git's union driver resolves add/add in a linked worktree

**Files:** none changed.

- [ ] **Step 1: Confirm the Integrate module and its collaborators are on this branch**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-run-integrate-at-the-a74f2cd6 && git branch --show-current && ls src/agent_manager/integration.py src/agent_manager/steps/integrate.py src/agent_manager/workflow/builtin/integrate.yaml tests/test_integration.py && grep -n "^def integrate_milestone\|^def integration_branch\|^def merge_order\|^class Integrate" src/agent_manager/integration.py
```
Expected: branch `m5/task-run-integrate-at-the-a74f2cd6`; all four files listed; `integration_branch`, `merge_order`, `IntegrateSuccess`, `IntegrateEscalation` and `integrate_milestone` found. If any file is missing, stop: the branch was not cut from `m5/task-merge-every-story-tip-6fea51ad`.

- [ ] **Step 2: Confirm the baseline suite is green**

Run: `uv run pytest -q`
Expected: all pass (e2e real-harness tests skip). If anything fails here, stop and report: it is not this card's failure.

- [ ] **Step 3: Prove the union merge driver resolves an add/add and a later modify/modify in a linked worktree, reading `.git/info/attributes`**

Run:
```bash
set -e; d="$(mktemp -d)"; git init -q -b main "$d/r"; cd "$d/r"; git config user.email t@example.com; git config user.name t; git config commit.gpgsign false
echo base > README.md; git add .; git commit -qm base
git switch -qc a; echo "from a" > IMPLEMENTATION.md; git add .; git commit -qm a
git switch -qc c; echo "from c" > IMPLEMENTATION.md; git add .; git commit -qm c
git switch -q main; git switch -qc b; echo "from b" > IMPLEMENTATION.md; git add .; git commit -qm b
git switch -q main; echo "IMPLEMENTATION.md merge=union" > .git/info/attributes
git worktree add -q -b integ "$d/integ" main; cd "$d/integ"
git merge --no-ff --no-edit a && git merge --no-ff --no-edit b && git merge --no-ff --no-edit c && git status --porcelain && cat IMPLEMENTATION.md
```
Expected: all three merges succeed with no `CONFLICT` line, `git status --porcelain` prints nothing, and `IMPLEMENTATION.md` holds lines from a, b and c. If a merge reports `CONFLICT`, stop and report: Task 4's scaffolding does not work on this git and the parallel e2e tests need a different answer.

---

### Task 1: The dry run previews Integrate and still writes nothing

Tiers: pure unit (`dry_run_payload` over hand-built census) and Steps (`am run --milestone --dry-run` on a real repo and board), both in `tests/test_cli.py`, where the existing dry-run tests of each tier live.

**Files:**
- Modify: `src/agent_manager/cli.py:866-951`
- Test: `tests/test_cli.py:28-39` (imports), `:1026-1226` (pure dry-run tests), `:2374-2441` (Steps dry-run tests)

**Interfaces:**
- Consumes: `integration.integration_branch(branch_prefix: str) -> str`, `integration.merge_order(stories, branch_prefix: str, base_branch: str) -> list[tuple[StoryPlan, str]]`, `cli.worktree_for(repo_dir: Path, branch: str) -> Path`.
- Produces: `cli.dry_run_payload(stories, *, repo_dir: Path, branch_prefix: str, base_branch: str, max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> dict[str, Any]` whose result has keys `max_concurrent`, `levels`, `already_done`, `integrate`, with `integrate == {"branch": str, "worktree": str, "order": [{"story": str, "tip": str}, ...]}`.

- [ ] **Step 1: Import `integration` in the test module**

In `tests/test_cli.py`, change the import block at lines 28-39 to:
```python
from agent_manager import (
    board,
    census,
    cli,
    dag,
    dispatch,
    integration,
    models,
    orchestrate,
    paths,
    prompt,
    store as store_module,
)
```

- [ ] **Step 2: Add a repo-dir constant for the pure tests**

In `tests/test_cli.py`, directly after `_plan_story` (ends at line 1052) add:
```python
DRY_RUN_REPO = Path("/repo")
"""The repo dir the pure dry-run tests pass. `worktree_for` only joins onto it,
so it need not exist, and the payload is still computed without touching disk."""
```

- [ ] **Step 3: Pass `repo_dir` at every existing pure call and extend the two full-payload assertions**

Change each of these calls in `tests/test_cli.py` (exact current text on the left, replacement on the right):
- line 1067: `payload = cli.dry_run_payload([a, b], branch_prefix="m3", base_branch="main")` becomes `payload = cli.dry_run_payload([a, b], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")`
- line 1115: `payload = cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")` becomes `payload = cli.dry_run_payload([a, b, c], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")`
- line 1134: `cli.dry_run_payload([a, b], branch_prefix="m3", base_branch="main")` becomes `cli.dry_run_payload([a, b], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")`
- line 1145: `cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")` becomes `cli.dry_run_payload([a, b, c], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")`
- lines 1159-1161: `payload = cli.dry_run_payload(\n        [closed, finished, empty], branch_prefix="m3", base_branch="main"\n    )` becomes:
```python
    payload = cli.dry_run_payload(
        [closed, finished, empty],
        repo_dir=DRY_RUN_REPO,
        branch_prefix="m3",
        base_branch="main",
    )
```
- lines 1192-1194: `payload = cli.dry_run_payload(\n        _three_then_one(), branch_prefix="m3", base_branch="main", max_concurrent=bound\n    )` becomes:
```python
    payload = cli.dry_run_payload(
        _three_then_one(),
        repo_dir=DRY_RUN_REPO,
        branch_prefix="m3",
        base_branch="main",
        max_concurrent=bound,
    )
```
- line 1202: `payload = cli.dry_run_payload(_three_then_one(), branch_prefix="m3", base_branch="main")` becomes `payload = cli.dry_run_payload(_three_then_one(), repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")`
- line 1215: `payload = cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")` becomes `payload = cli.dry_run_payload([a, b, c], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")`

In `test_the_dry_run_payload_lists_remaining_subtasks_on_full_list_bases`, replace the closing of the expected dict (lines 1103-1107):
```python
        "already_done": [
            {"kind": "story", "id": a.id, "title": "story 1"},
            {"kind": "subtask", "id": _plan_id(21), "title": "subtask 21", "story": b.id},
        ],
    }
```
with:
```python
        "already_done": [
            {"kind": "story", "id": a.id, "title": "story 1"},
            {"kind": "subtask", "id": _plan_id(21), "title": "subtask 21", "story": b.id},
        ],
        "integrate": {
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [
                {"story": a.id, "tip": branch(a.subtasks[-1])},
                {"story": b.id, "tip": branch(b.subtasks[-1])},
            ],
        },
    }
```

In `test_a_milestone_with_nothing_left_has_no_levels_and_lists_every_story_as_done`, replace the expected dict (lines 1163-1171) with:
```python
    assert payload == {
        "max_concurrent": 4,
        "levels": [],
        "already_done": [
            {"kind": "story", "id": closed.id, "title": "story 1"},
            {"kind": "story", "id": finished.id, "title": "story 2"},
            {"kind": "story", "id": empty.id, "title": "story 3"},
        ],
        "integrate": {
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [
                {"story": closed.id, "tip": dag.subtask_branch("m3", closed.subtasks[-1])},
                {"story": finished.id, "tip": dag.subtask_branch("m3", finished.subtasks[-1])},
            ],
        },
    }
```

- [ ] **Step 4: Write the new pure test (spec test 7)**

In `tests/test_cli.py`, directly after `test_a_blocker_outside_the_milestone_roots_the_story_on_the_base_branch` (ends at line 1226) add:
```python
def test_the_dry_run_payload_plans_integrate_over_every_story_in_integrate_order():
    """Spec test 7. Integrate covers every story, done or not, level by level
    in census order within a level, and a story with no subtasks has no tip of
    its own, so it is left out of the order."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    empty = _plan_story(3, [])
    done = _plan_story(4, [_plan_subtask(41, "done")], status="done")

    payload = cli.dry_run_payload(
        [b, a, empty, done], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )

    assert payload["integrate"] == {
        "branch": "m3-integrate",
        "worktree": "/repo/.claude/worktrees/m3-integrate",
        "order": [
            {"story": a.id, "tip": dag.subtask_branch("m3", a.subtasks[-1])},
            {"story": done.id, "tip": dag.subtask_branch("m3", done.subtasks[-1])},
            {"story": b.id, "tip": dag.subtask_branch("m3", b.subtasks[-1])},
        ],
    }
```

- [ ] **Step 5: Forbid Integrate on every no-write path, and update the Steps-tier key set**

In `tests/test_cli.py`, replace `_forbid_writes` (lines 2374-2378) with:
```python
def _forbid_writes(monkeypatch) -> None:
    monkeypatch.setattr(cli, "run_card", _Forbidden("run_card"))
    monkeypatch.setattr(cli, "Store", _Forbidden("Store"))
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))
    monkeypatch.setattr(cli.board, "set_status", _Forbidden("board.set_status"))
    # The dry run plans Integrate from `integration`'s pure helpers; the two
    # names that would write a branch or a worktree must never be reached.
    monkeypatch.setattr(
        integration, "integrate_milestone", _Forbidden("integration.integrate_milestone")
    )
    monkeypatch.setattr(integration, "merge_tip", _Forbidden("integration.merge_tip"))
```

In `test_the_milestone_dry_run_stacks_each_story_on_the_previous_ones_tip`, change line 2415 from `assert set(data) == {"max_concurrent", "levels", "already_done"}` to:
```python
    assert set(data) == {"max_concurrent", "levels", "already_done", "integrate"}
```

- [ ] **Step 6: Write the Steps-tier no-write test (spec test 8)**

In `tests/test_cli.py`, directly after `test_done_work_is_already_done_and_still_anchors_the_stack` (ends at line 2487) add:
```python
@requires_git
@requires_brd
def test_the_milestone_dry_run_shows_the_integrate_plan_and_writes_nothing(
    project, milestone_board, monkeypatch
):
    """Spec test 8. Story A is already done and still leads the Integrate
    order: Integrate folds in every story's tip. Nothing is written: no run
    directory, no `m2-integrate` branch, no worktree, no board change."""
    stories = milestone_board["stories"]
    subtasks = milestone_board["subtasks"]
    for subtask in subtasks["A"]:
        board.set_status(subtask, "done", repo_dir=project)
    board.set_status(stories["A"], "done", repo_dir=project)
    board_before = board.roots(repo_dir=project)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))

    result = _dry_run(project, milestone_board["milestone"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["integrate"] == {
        "branch": "m2-integrate",
        "worktree": str(cli.worktree_for(project, "m2-integrate")),
        "order": [
            {"story": stories[key], "tip": _m2_branch(project, subtasks[key][-1])}
            for key in "ABC"
        ],
    }
    assert not cli.worktree_for(project, "m2-integrate").exists()
    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before
```

- [ ] **Step 7: Run the dry-run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "dry_run or dry" -q`
Expected: FAIL. The pure tests fail with `TypeError: dry_run_payload() got an unexpected keyword argument 'repo_dir'`; the Steps-tier tests fail on the missing `integrate` key (`KeyError: 'integrate'` or the key-set assertion).

- [ ] **Step 8: Implement the `integrate` plan in `cli.py`**

In `src/agent_manager/cli.py`, replace `dry_run_payload` and `dry_run_milestone` (lines 866-951) with:
```python
def dry_run_payload(
    stories: Sequence[census.StoryPlan],
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
    """O3's preview: dispatch levels with each subtask's branch and base, then Integrate.

    Pure over the census, and every derivation belongs to `dag` or
    `integration`. The cycle check runs first because a cycle is what breaks
    the geometry, and `story_root`'s own guard misses a cycle between two
    populated stories. `stories_by_id` covers every story, closed ones
    included, so a story blocked by a done story still roots on that story's
    tip. A story's `subtasks` lists only what would be dispatched, but each
    `base` comes from `stack_bases` over the full ordered list, so a done
    first subtask still anchors the second. `max_concurrent` is echoed at the
    top, and each level row says how many of its stories would run together:
    `min(len(level), max_concurrent)`. The caller refuses a bound below 1.

    `integrate` is the terminal phase's plan (Integrate addendum I6): the
    branch every tip is merged into, its worktree under `repo_dir`, and the
    merge order `integration.merge_order` gives -- every story with subtasks,
    done or not. `repo_dir` is only joined onto, never read.
    """
    # `integration` imports this module at load time, so importing it at the
    # top of this module would be circular. By call time both are loaded.
    from agent_manager import integration

    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    levels = dag.compute_levels(stories)
    stories_by_id = {story.id: story for story in stories}
    level_rows: list[dict[str, Any]] = []
    for index, level in enumerate(levels):
        story_rows: list[dict[str, Any]] = []
        for story in level:
            bases = dag.stack_bases(story, stories_by_id, branch_prefix, base_branch)
            story_rows.append(
                {
                    "story": story.id,
                    "title": story.title,
                    "root": dag.story_root(
                        story, stories_by_id, branch_prefix, base_branch
                    ),
                    "subtasks": [
                        {
                            "id": subtask.id,
                            "title": subtask.title,
                            "status": subtask.status,
                            "branch": dag.subtask_branch(branch_prefix, subtask),
                            "base": bases[subtask.id],
                        }
                        for subtask in dag.remaining_subtasks(story)
                    ],
                }
            )
        level_rows.append(
            {
                "level": index,
                "concurrent": min(len(level), max_concurrent),
                "stories": story_rows,
            }
        )
    integrate_branch = integration.integration_branch(branch_prefix)
    return {
        "max_concurrent": max_concurrent,
        "levels": level_rows,
        "already_done": already_done_entries(stories),
        "integrate": {
            "branch": integrate_branch,
            "worktree": str(worktree_for(repo_dir, integrate_branch)),
            "order": [
                {"story": story.id, "tip": tip}
                for story, tip in integration.merge_order(
                    stories, branch_prefix, base_branch
                )
            ],
        },
    }


def dry_run_milestone(
    needle: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
    """O3's order: repo dir, roots, milestone, tree, census, then the payload.

    Read-only by construction. The two `brd` reads are its only I/O. No
    `Store` is opened (that would mint a run directory), no runner is built,
    and nothing is fetched, pruned, branched, merged or written to the board.
    The Integrate plan is derived, never run. Every refusal is a type already
    in `HANDLED`.
    """
    root = resolve_repo_dir(repo_dir)
    milestone = census.find_milestone(board.roots(repo_dir=root), needle)
    plan = census.flatten_milestone(board.tree(milestone.id, repo_dir=root))
    return dry_run_payload(
        plan.stories,
        repo_dir=root,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        max_concurrent=max_concurrent,
    )
```

- [ ] **Step 9: Run the dry-run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "dry_run or dry" -q`
Expected: PASS.

- [ ] **Step 10: Run the whole CLI module**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS (no other caller of `dry_run_payload` exists; `grep -rn "dry_run_payload(" src tests` shows only `cli.py` and `tests/test_cli.py`).

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: preview the Integrate plan in the milestone dry run"
```

---

### Task 2: Pure payload helpers for Integrate's two outcomes

Tier: pure unit, in `tests/test_orchestrate.py` beside the existing `escalated_payload` unit tests.

**Files:**
- Modify: `src/agent_manager/orchestrate.py:36` (imports), after `:158` (new helpers)
- Test: `tests/test_orchestrate.py:26` (imports), after `:233` (new tests)

**Interfaces:**
- Consumes: `integration.IntegrateSuccess(branch: str, worktree: Path, merged: list[str], resolved: list[str])`, `integration.IntegrateEscalation(story: str | None, files: list[str], detail: str, phase: str = "integrate")`.
- Produces: `orchestrate.integrated_payload(outcome: integration.IntegrateSuccess) -> dict[str, Any]` returning `{"branch", "worktree" (str), "merged", "resolved"}`; `orchestrate.integrate_escalated_payload(run_id: str, outcome: integration.IntegrateEscalation, warnings: list[str]) -> dict[str, Any]` returning exactly `{"escalated": True, "phase", "story", "files", "detail", "run_id", "warnings"}`.

- [ ] **Step 1: Import `integration` in the test module**

In `tests/test_orchestrate.py`, change line 26 to:
```python
from agent_manager import board, census, cli, dag, engine, integration, models, orchestrate, paths
```

- [ ] **Step 2: Write the failing pure tests**

In `tests/test_orchestrate.py`, directly after `test_an_unnamed_primary_falls_back_to_the_first_escalation_in_census_order` (ends at line 233) add:
```python
def test_the_integrated_payload_is_plain_json_with_the_worktree_as_a_string():
    outcome = integration.IntegrateSuccess(
        branch="m3-integrate",
        worktree=Path("/repo/.claude/worktrees/m3-integrate"),
        merged=["A", "B"],
        resolved=["B"],
    )

    assert orchestrate.integrated_payload(outcome) == {
        "branch": "m3-integrate",
        "worktree": "/repo/.claude/worktrees/m3-integrate",
        "merged": ["A", "B"],
        "resolved": ["B"],
    }


def test_the_integrate_escalation_payload_names_the_phase_story_and_files():
    outcome = integration.IntegrateEscalation(
        story="B", files=["shared.txt"], detail="the resolver did not finish"
    )

    payload = orchestrate.integrate_escalated_payload("run-1", outcome, ["gate warned"])

    assert payload == {
        "escalated": True,
        "phase": "integrate",
        "story": "B",
        "files": ["shared.txt"],
        "detail": "the resolver did not finish",
        "run_id": "run-1",
        "warnings": ["gate warned"],
    }


def test_a_final_verification_escalation_payload_has_no_story():
    outcome = integration.IntegrateEscalation(story=None, files=[], detail="suite red")

    payload = orchestrate.integrate_escalated_payload("run-1", outcome, [])

    assert (payload["story"], payload["files"], payload["phase"]) == (None, [], "integrate")
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "integrated_payload or integrate_escalation_payload or final_verification_escalation_payload" -q`
Expected: FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'integrated_payload'` (and `integrate_escalated_payload`).

- [ ] **Step 4: Implement the helpers**

In `src/agent_manager/orchestrate.py`, change line 36 to:
```python
from agent_manager import board, census, cli, dag, integration, models
```
(`integration` imports `cli` at load, and `cli` imports neither `integration` nor this module at load, so every import order resolves: Task 3 pins it in a fresh interpreter.)

Directly after `escalated_payload` (ends at line 158) add:
```python
def integrated_payload(outcome: integration.IntegrateSuccess) -> dict[str, Any]:
    """A clean run's `integrated` key: where every story tip now lives (addendum I6).

    `worktree` is a `str`, so the payload is plain JSON before `render` ever
    sees it. `merged` and `resolved` are story ids in merge order.
    """
    return {
        "branch": outcome.branch,
        "worktree": str(outcome.worktree),
        "merged": list(outcome.merged),
        "resolved": list(outcome.resolved),
    }


def integrate_escalated_payload(
    run_id: str, outcome: integration.IntegrateEscalation, warnings: list[str]
) -> dict[str, Any]:
    """The result of a run that stopped at Integrate (addendum I5).

    `escalated: true` is what `am run` reads for its exit code, as for a lane
    escalation. `story` is `None` when the final verification failed rather
    than a tip. The branch and worktree are left as Integrate left them.
    """
    return {
        "escalated": True,
        "phase": outcome.phase,
        "story": outcome.story,
        "files": list(outcome.files),
        "detail": outcome.detail,
        "run_id": run_id,
        "warnings": warnings,
    }
```

- [ ] **Step 5: Run them to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "integrated_payload or integrate_escalation_payload or final_verification_escalation_payload" -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: shape the Integrate success and escalation payloads"
```

---

### Task 3: `run_milestone` runs Integrate before it records the run

Tier: Steps, in `tests/test_orchestrate.py` (real temp git repo and brd board, `XDG_DATA_HOME` under `tmp_path`, harness replaced at the `driver` and `runner_factory` seams) and `tests/test_cli.py` (exit code, import order).

The existing `run_milestone` tests use `FakeDriver`, which creates no branches, so a real Integrate would find no tips. Those tests therefore replace Integrate at its own seam, `integration.integrate_milestone` (read by `run_milestone` at call time, exactly like `driver`), with a recorder that returns a success shaped by the real `integration.merge_order`. Their assertions stay whole and gain `integrated` plus an exact record of the call. New tests that request the `real_integrate` fixture run the real Integrate over branches a `BranchingDriver` really commits.

**Files:**
- Modify: `src/agent_manager/orchestrate.py:1-24` (docstring), `:240-258` (`story_tips` docstring), `:465-574` (`run_milestone`)
- Test: `tests/test_orchestrate.py` (imports, docstring, fixtures, existing tests at `:581`, `:745`, `:818`, `:1196`, new tests), `tests/test_cli.py:2754-2774`, `:2889-2903`, `:2936`

**Interfaces:**
- Consumes: `orchestrate.integrated_payload`, `orchestrate.integrate_escalated_payload` (Task 2); `integration.integrate_milestone(stories, repo_dir, base_branch, branch_prefix, commands, allow_no_verification, store, run_id, runner_factory) -> IntegrateSuccess | IntegrateEscalation`; `cli.default_runner_factory`.
- Produces: `run_milestone(...)` clean payload `{"done": True, "run_id", "levels", "completed", "tips", "warnings", "integrated"}`; Integrate escalation payload from `integrate_escalated_payload`; lane escalation payload unchanged.

- [ ] **Step 1: Test-module imports, docstring and constants**

In `tests/test_orchestrate.py`, replace the module docstring (lines 1-12) with:
```python
"""Behaviour of the milestone runner (orchestration addendum O6, Integrate I6).

Two tiers, per design §14:

- `plan_levels`, `story_tips`, `stale_story_anchors` and the payload helpers
  are pure and get unit tests on hand-built plans or outcomes;
- `run_milestone` runs on Steps-tier fixtures -- a real temporary git repo and a
  real temporary brd board, with `XDG_DATA_HOME` under `tmp_path` so
  `paths.data_dir()` never touches the developer's own -- with the harness
  replaced at the injected `driver` seam. No runner, adapter or `claude` is
  involved; production wiring under a fake `claude` belongs to tests/e2e.

`FakeDriver` makes no branches, so by default (`integrate_recorder`, autouse)
Integrate is replaced at its own call-time seam, `integration.integrate_milestone`,
by a recorder. Tests that request `real_integrate` run the real Integrate over
branches `BranchingDriver` or `_commit_branch` really commit.
"""
```

Replace lines 14-16 (`import json`, `import shutil`, `import subprocess`) with:
```python
import json
import shlex
import shutil
import subprocess
import sys
```

Directly after `PREFIX = "m3"` (line 249) add:
```python
LATER = datetime(2026, 9, 24, 13, 0, 0, tzinfo=timezone.utc)
"""A relaunch's clock: a second run needs its own run id."""

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch(PREFIX)`, spelled out so a rename is caught."""

PASS_CMD = shlex.join([sys.executable, "-c", "print('suite green')"])
FAIL_CMD = shlex.join(
    [sys.executable, "-c", "import sys; print('suite is red'); sys.exit(3)"]
)
```

Directly after `_git` (ends at line 256) add:
```python
def _sha(cwd: Path, ref: str) -> str:
    return _git(cwd, "rev-parse", ref).strip()


def _is_ancestor(cwd: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _local_branches(cwd: Path) -> list[str]:
    return _git(cwd, "branch", "--format=%(refname:short)").split()
```

- [ ] **Step 2: The Integrate seam fixtures and the branch-making helpers**

In `tests/test_orchestrate.py`, directly after `_run` (ends at line 410) add:
```python
REAL_INTEGRATE = integration.integrate_milestone
"""Captured at import, before `integrate_recorder` swaps it."""


@dataclass
class IntegrateRecorder:
    """Stands in for `integration.integrate_milestone`, read by `run_milestone` at call time.

    Every call is recorded with the run's status at that moment, which is how
    a test sees that Integrate ran before the run was recorded. `outcome`
    scripts the result: `None` is a success shaped by the real `merge_order`,
    an `IntegrateEscalation` is returned, an exception instance is raised.
    """

    outcome: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self,
        stories,
        repo_dir,
        base_branch,
        branch_prefix,
        commands,
        allow_no_verification,
        store,
        run_id,
        runner_factory,
    ):
        stories = list(stories)
        self.calls.append(
            {
                "stories": [story.id for story in stories],
                "repo_dir": repo_dir,
                "base_branch": base_branch,
                "branch_prefix": branch_prefix,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "store": store,
                "run_id": run_id,
                "runner_factory": runner_factory,
                "run_status": store.load_run(run_id).status,
            }
        )
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if self.outcome is not None:
            return self.outcome
        branch = integration.integration_branch(branch_prefix)
        return integration.IntegrateSuccess(
            branch=branch,
            worktree=cli.worktree_for(repo_dir, branch),
            merged=[
                story.id
                for story, _ in integration.merge_order(stories, branch_prefix, base_branch)
            ],
        )


@pytest.fixture(autouse=True)
def integrate_recorder(monkeypatch) -> IntegrateRecorder:
    recorder = IntegrateRecorder()
    monkeypatch.setattr(integration, "integrate_milestone", recorder)
    return recorder


@pytest.fixture
def real_integrate(monkeypatch, integrate_recorder) -> None:
    """Undo `integrate_recorder`: this test runs the real Integrate."""
    monkeypatch.setattr(integration, "integrate_milestone", REAL_INTEGRATE)


def _integrated(root: Path, merged: list[str]) -> dict[str, Any]:
    """The `integrated` key a clean run reports when no tip needed a resolver."""
    return {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(cli.worktree_for(root, INTEGRATION_BRANCH)),
        "merged": merged,
        "resolved": [],
    }


def _no_resolver(**kwargs: Any) -> Any:
    """A runner factory for tests whose tips never conflict: a resolver is a failure."""
    pytest.fail("Integrate dispatched a resolver, but no tip in this test conflicts")


def _commit_branch(root: Path, branch: str, base: str, card_id: str) -> None:
    """Cut `branch` from `base` in its own worktree and commit one file named for the card.

    Each card writes its own file, so no two story tips ever conflict.
    """
    path = cli.worktree_for(root, branch)
    _git(root, "worktree", "add", "-b", branch, str(path), base)
    (path / f"{dag.short_id(card_id)}.txt").write_text(f"work of {card_id}\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", f"work of {card_id}")


@dataclass
class BranchingDriver(FakeDriver):
    """A `FakeDriver` whose `done` subtask leaves what the real one does.

    A branch with one commit on the subtask's base, and the card `done` on the
    board through the rollup, as `mark_done` does. Sequential runs only.
    """

    def __call__(self, *, store, run_id, card, parent, subtask, repo_dir, **kwargs):
        drive = super().__call__(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=repo_dir,
            **kwargs,
        )
        if drive.summary.status == "done":
            _commit_branch(repo_dir, subtask.branch, subtask.base_branch, card.id)
            rollup.set_status(card.id, "done", repo_dir=repo_dir)
        return drive


def _census_stories(project: Path, milestone: str) -> list[str]:
    """Story ids in census order: siblings made in one second are ordered by id."""
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    return [story.id for story in plan.stories]
```

- [ ] **Step 3: Update the existing full-payload tests (spec test 10), keeping them whole**

In `test_subtasks_run_in_order_each_stacked_on_the_one_before` (line 581): change the signature to `def test_subtasks_run_in_order_each_stacked_on_the_one_before(project, integrate_recorder):` and replace the `assert result == {...}` block (lines 616-626) with:
```python
    assert result == {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": [story_a]}, {"level": 1, "stories": [story_b]}],
        "completed": [a1, a2, b1],
        "tips": [
            {"story": story_a, "tip": branches[a2]},
            {"story": story_b, "tip": branches[b1]},
        ],
        "warnings": [],
        "integrated": _integrated(root, [story_a, story_b]),
    }
    (integrate_call,) = integrate_recorder.calls
    assert isinstance(integrate_call.pop("store"), store_module.Store)
    assert integrate_call == {
        "stories": [story_a, story_b],
        "repo_dir": root,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "commands": ["uv run pytest"],
        "allow_no_verification": True,
        "run_id": run_id,
        "runner_factory": factory,
        "run_status": "started",
    }
```

In `test_a_milestone_with_nothing_pending_still_records_a_done_run` (line 745): change the signature to `(project, integrate_recorder)`, add `root = cli.resolve_repo_dir(project)` after `driver = FakeDriver()`, and replace the `assert result == {...}` block (lines 756-763) with:
```python
    assert result == {
        "done": True,
        "run_id": cli.mint_run_id(shape["milestone"], STARTED_AT),
        "levels": [],
        "completed": [],
        "tips": [{"story": story_a, "tip": _branch(project, a1)}],
        "warnings": [],
        "integrated": _integrated(root, [story_a]),
    }
    # Nothing left to run still integrates, so a relaunch can retry it.
    assert [call["stories"] for call in integrate_recorder.calls] == [[story_a]]
```

In `test_an_escalation_stops_the_run_before_the_next_story` (line 818): change the signature to `(project, integrate_recorder)` and append at the end of the test:
```python
    # Spec test 3: a lane escalation never reaches Integrate.
    assert integrate_recorder.calls == []
    assert INTEGRATION_BRANCH not in _local_branches(project)
```

In `test_an_escalation_parks_the_other_lane_and_no_later_level_starts` (line 1196): change the signature to `(project, integrate_recorder)` and append at the end of the test:
```python
    assert integrate_recorder.calls == []
```

- [ ] **Step 4: Write the new wiring tests (recorder seam)**

In `tests/test_orchestrate.py`, directly after `test_no_driver_resolves_to_cli_drive_subtask_at_call_time` (ends at line 740) add:
```python
@requires_git
@requires_brd
def test_no_runner_factory_gives_integrate_cli_default_runner_factory_at_call_time(
    project, monkeypatch, integrate_recorder
):
    """Integrate needs a factory for a conflict. `None` is production's, read
    off `cli` when Integrate runs, while the lanes still get `None` and resolve
    it in `drive_subtask` as before."""
    shape = _milestone(project, {"A": 1})

    def sentinel_factory(**kwargs: Any) -> Any:
        pytest.fail("the sentinel factory is only compared, never called")

    monkeypatch.setattr(cli, "default_runner_factory", sentinel_factory)
    driver = FakeDriver()

    _run(project, shape["milestone"], driver)

    assert [call["runner_factory"] for call in driver.calls] == [None]
    (integrate_call,) = integrate_recorder.calls
    assert integrate_call["runner_factory"] is sentinel_factory


@requires_git
@requires_brd
def test_an_integrate_escalation_is_recorded_and_reported_with_its_story_and_files(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    integrate_recorder.outcome = integration.IntegrateEscalation(
        story=story_b, files=["shared.txt"], detail="the resolver did not finish"
    )
    driver = FakeDriver(warnings={a1: ["a1 warned"]})

    result = _run(project, shape["milestone"], driver)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "phase": "integrate",
        "story": story_b,
        "files": ["shared.txt"],
        "detail": "the resolver did not finish",
        "run_id": run_id,
        "warnings": ["a1 warned"],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "done",
        b1: "done",
    }
    assert [call["run_status"] for call in integrate_recorder.calls] == ["started"]


@requires_git
@requires_brd
def test_an_integrate_that_raises_propagates_and_the_run_is_never_recorded_done(
    project, integrate_recorder
):
    """Error path: a git failure that is not a conflict is not reclassified."""
    shape = _milestone(project, {"A": 1})
    integrate_recorder.outcome = worktree.GitError(
        "fatal: not a valid object name", argv=["git", "merge"], exit_code=128
    )

    with pytest.raises(worktree.GitError):
        _run(project, shape["milestone"], FakeDriver())

    run = _load(project, cli.mint_run_id(shape["milestone"], STARTED_AT))
    assert run.status == "started"
```

- [ ] **Step 5: Write the real-Integrate tests (spec tests 1, 2, 4, 5, 6)**

In `tests/test_orchestrate.py`, directly after the three tests of Step 4 add:
```python
@requires_git
@requires_brd
def test_a_clean_milestone_is_integrated_before_the_run_is_recorded_done(
    project, real_integrate
):
    """Spec test 1."""
    shape = _milestone(project, {"A": 1, "B": 1})
    root = cli.resolve_repo_dir(project)
    order = _census_stories(project, shape["milestone"])
    by_story = _subtasks_by_story(shape)
    main_before = _sha(project, "main")

    result = _run(
        project,
        shape["milestone"],
        BranchingDriver(),
        commands=[PASS_CMD],
        runner_factory=_no_resolver,
    )

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": order}],
        "completed": [card for story in order for card in by_story[story]],
        "tips": [
            {"story": story, "tip": _branch(project, by_story[story][-1])} for story in order
        ],
        "warnings": [],
        "integrated": _integrated(root, order),
    }
    assert _load(project, run_id).status == "done"
    for story in order:
        assert _is_ancestor(project, _branch(project, by_story[story][-1]), INTEGRATION_BRANCH)
    assert cli.worktree_for(root, INTEGRATION_BRANCH).is_dir()
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_an_integrate_escalation_records_the_run_escalated_and_leaves_the_branch(
    project, real_integrate
):
    """Spec test 2: the final verification fails."""
    shape = _milestone(project, {"A": 1, "B": 1})
    root = cli.resolve_repo_dir(project)
    order = _census_stories(project, shape["milestone"])
    by_story = _subtasks_by_story(shape)
    integrate_worktree = cli.worktree_for(root, INTEGRATION_BRANCH)
    main_before = _sha(project, "main")

    result = _run(
        project,
        shape["milestone"],
        BranchingDriver(),
        commands=[FAIL_CMD],
        runner_factory=_no_resolver,
    )

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert set(result) == {
        "escalated", "phase", "story", "files", "detail", "run_id", "warnings"
    }
    assert result["escalated"] is True
    assert result["phase"] == "integrate"
    assert result["story"] is None
    assert result["files"] == []
    assert result["detail"].startswith(
        f"the integrated branch failed its final verification in {integrate_worktree}"
    )
    assert result["run_id"] == run_id
    assert result["warnings"] == []
    expected = {"run": "escalated"}
    for story in order:
        expected[story] = "done"
        for card in by_story[story]:
            expected[card] = "done"
    assert _statuses(_load(project, run_id)) == expected
    # Left exactly as Integrate left it: both tips merged, a clean worktree.
    assert integrate_worktree.is_dir()
    assert _git(integrate_worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == (
        INTEGRATION_BRANCH
    )
    assert _git(integrate_worktree, "status", "--porcelain") == ""
    for story in order:
        assert _is_ancestor(project, _branch(project, by_story[story][-1]), INTEGRATION_BRANCH)
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_an_all_done_milestone_runs_no_lane_and_still_integrates(project, real_integrate):
    """Spec test 4."""
    shape = _milestone(project, {"A": 1, "B": 1})
    root = cli.resolve_repo_dir(project)
    order = _census_stories(project, shape["milestone"])
    by_story = _subtasks_by_story(shape)
    for story in order:
        (card,) = by_story[story]
        _commit_branch(root, _branch(project, card), "main", card)
        rollup.set_status(card, "done", repo_dir=project)
    main_before = _sha(project, "main")
    assert INTEGRATION_BRANCH not in _local_branches(project)
    driver = FakeDriver()

    result = _run(
        project, shape["milestone"], driver, commands=[PASS_CMD], runner_factory=_no_resolver
    )

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert driver.calls == []
    assert result == {
        "done": True,
        "run_id": run_id,
        "levels": [],
        "completed": [],
        "tips": [
            {"story": story, "tip": _branch(project, by_story[story][-1])} for story in order
        ],
        "warnings": [],
        "integrated": _integrated(root, order),
    }
    assert _statuses(_load(project, run_id)) == {"run": "done"}
    for story in order:
        assert _is_ancestor(project, _branch(project, by_story[story][-1]), INTEGRATION_BRANCH)
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_a_relaunch_after_an_integrate_escalation_retries_integrate(project, real_integrate):
    """Spec test 5: the cause is fixed (the suite made green) and the same
    milestone is relaunched with nothing left to drive."""
    shape = _milestone(project, {"A": 1, "B": 1})
    root = cli.resolve_repo_dir(project)
    order = _census_stories(project, shape["milestone"])
    by_story = _subtasks_by_story(shape)
    main_before = _sha(project, "main")

    first = _run(
        project,
        shape["milestone"],
        BranchingDriver(),
        commands=[FAIL_CMD],
        runner_factory=_no_resolver,
    )
    assert first["escalated"] is True
    assert first["phase"] == "integrate"

    driver = FakeDriver()
    second = _run(
        project,
        shape["milestone"],
        driver,
        commands=[PASS_CMD],
        runner_factory=_no_resolver,
        clock=lambda: LATER,
    )

    second_id = cli.mint_run_id(shape["milestone"], LATER)
    assert driver.calls == []
    assert second == {
        "done": True,
        "run_id": second_id,
        "levels": [],
        "completed": [],
        "tips": [
            {"story": story, "tip": _branch(project, by_story[story][-1])} for story in order
        ],
        "warnings": [],
        "integrated": _integrated(root, order),
    }
    assert _load(project, first["run_id"]).status == "escalated"
    assert _load(project, second_id).status == "done"
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_relaunching_a_finished_integrated_milestone_leaves_the_integration_tip(
    project, real_integrate
):
    """Spec test 6."""
    shape = _milestone(project, {"A": 1, "B": 1})
    main_before = _sha(project, "main")
    first = _run(
        project,
        shape["milestone"],
        BranchingDriver(),
        commands=[PASS_CMD],
        runner_factory=_no_resolver,
    )
    assert first["done"] is True
    tip_after_first = _sha(project, INTEGRATION_BRANCH)
    driver = FakeDriver()

    second = _run(
        project,
        shape["milestone"],
        driver,
        commands=[PASS_CMD],
        runner_factory=_no_resolver,
        clock=lambda: LATER,
    )

    assert driver.calls == []
    assert second["done"] is True
    assert second["integrated"] == first["integrated"]
    assert _sha(project, INTEGRATION_BRANCH) == tip_after_first
    assert _sha(project, "main") == main_before
```

- [ ] **Step 6: CLI-side tests: the clean payload constant, the exit code (spec test 9) and the import triangle**

In `tests/test_cli.py`, replace `CLEAN_MILESTONE` (lines 2754-2762) with:
```python
CLEAN_MILESTONE = {
    "done": True,
    "run_id": "20260924T000000Z-0badcafe",
    "levels": [{"level": 0, "stories": ["story-a"]}],
    "completed": ["subtask-a1"],
    "tips": [{"story": "story-a", "tip": "m3/task-a1"}],
    "warnings": [],
    "integrated": {
        "branch": "m3-integrate",
        "worktree": "/repo/.claude/worktrees/m3-integrate",
        "merged": ["story-a"],
        "resolved": [],
    },
}
"""`run_milestone`'s clean payload shape: `done: true`, `integrated`, and no `status` or `escalated` key."""
```

Directly after `ESCALATED_MILESTONE`'s docstring (line 2774) add:
```python
INTEGRATE_ESCALATED_MILESTONE = {
    "escalated": True,
    "phase": "integrate",
    "story": None,
    "files": [],
    "detail": (
        "the integrated branch failed its final verification in "
        "/repo/.claude/worktrees/m3-integrate: suite red"
    ),
    "run_id": "20260924T000000Z-0badcafe",
    "warnings": [],
}
"""`run_milestone`'s payload when it stopped at Integrate (addendum I5)."""
```

Directly after `test_an_escalated_milestone_exits_one_with_an_ok_envelope` (ends at line 2902) add:
```python
def test_an_integrate_escalation_exits_one_with_an_ok_envelope(tmp_path, monkeypatch):
    """Spec test 9: the existing `escalated is True` check covers the
    Integrate payload with no change of its own."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, INTEGRATE_ESCALATED_MILESTONE)

    result = _milestone_run(tmp_path)

    assert result.exit_code == cli.EXIT_ESCALATED, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(INTEGRATE_ESCALATED_MILESTONE)
```

Change the parametrize line of `test_cli_and_orchestrate_import_cleanly_in_either_order` (line 2936) to:
```python
@pytest.mark.parametrize(
    "first", ["agent_manager.cli", "agent_manager.orchestrate", "agent_manager.integration"]
)
```
and its `code` body (lines 2941-2945) to:
```python
    code = (
        f"import {first}\n"
        "from agent_manager import cli, integration, orchestrate\n"
        "assert orchestrate.cli is cli\n"
        "assert orchestrate.integration is integration\n"
    )
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py tests/test_cli.py -q`
Expected: FAIL. `run_milestone` never calls Integrate yet, so: the updated full-payload tests fail on the missing `integrated` key or on `integrate_recorder.calls == []` where one call is expected; the runner-factory, Integrate-escalation and Integrate-raises tests fail (no call recorded, run recorded `done`, `DID NOT RAISE`); the real-Integrate tests fail on the missing `integrated` key and missing `m3-integrate` branch; and the `agent_manager.integration` import-order case fails on `orchestrate.integration`. `test_an_integrate_escalation_exits_one_with_an_ok_envelope` already passes: it pins the existing check against the new payload shape.

- [ ] **Step 8: Wire Integrate into `run_milestone`**

In `src/agent_manager/orchestrate.py`, replace the tail of `run_milestone` (lines 561-574, from `store.record_run(run_record.model_copy(update={"status": "done"}))` through `store.close()`) with:
```python
        # Integrate (addendum I6) runs only once every level finished clean,
        # and also when there was nothing left to drive: that is how a relaunch
        # retries an Integrate escalation, and why a finished milestone's
        # relaunch is a no-op merge. Read as `integration.integrate_milestone`
        # so a test can replace it, as `driver` is. It needs a factory for a
        # conflicting tip; `None` is production's, read off `cli` now.
        factory = cli.default_runner_factory if runner_factory is None else runner_factory
        outcome = integration.integrate_milestone(
            stories=plan.stories,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            commands=list(commands),
            allow_no_verification=allow_no_verification,
            store=store,
            run_id=run_id,
            runner_factory=factory,
        )
        if isinstance(outcome, integration.IntegrateEscalation):
            # The branch and worktree stay exactly as Integrate left them (I5).
            store.record_run(run_record.model_copy(update={"status": "escalated"}))
            return integrate_escalated_payload(run_id, outcome, warnings)

        store.record_run(run_record.model_copy(update={"status": "done"}))
        return {
            "done": True,
            "run_id": run_id,
            "levels": [
                {"level": index, "stories": [planned.story.id for planned in level]}
                for index, level in enumerate(levels)
            ],
            "completed": completed,
            "tips": tips,
            "warnings": warnings,
            "integrated": integrated_payload(outcome),
        }
    finally:
        store.close()
```

In the `run_milestone` docstring, replace the last paragraph (lines 488-491, `The first escalation sets the run's stop: ... exactly the sequential runner's.`) with:
```python
    The first escalation sets the run's stop: lanes already running park at
    their next phase boundary and are recorded `stopped`, lanes not yet started
    leave their story `pending`, and no later level is scheduled. At
    `max_concurrent=1` the result is exactly the sequential runner's.

    When every level finished clean -- or none had anything to run --
    Integrate folds every story tip into `<branch_prefix>-integrate` before
    the run is recorded. Success records `done` and adds `integrated`; an
    Integrate escalation records `escalated` and returns
    `integrate_escalated_payload`. An exception from Integrate propagates and
    the run is never recorded `done`.
```

In the `story_tips` docstring (lines 243-247), replace `What a human merges after a clean run, since this card has no Integrate.` with `Every story's own branch, reported beside \`integrated\`, which names the one branch they were merged into.`

In the module docstring, change the sentence on line 7-8 `Every derivation belongs to a collaborator: the milestone and its census to \`census\`, levels,` so that the list of collaborators also names Integrate: after `run state to \`Store\`.` (line 9) insert ` The terminal merge of every story tip belongs to \`integration\`.` The resulting lines 6-10 read:
```python
level N+1 starts only after every lane of level N returns. Every derivation
belongs to a collaborator: the milestone and its census to `census`, levels,
stack bases and tips to `dag`, board reads to `board`, rollup to
`steps.rollup`, git to `steps.worktree.run_git`, run state to `Store`. The
terminal merge of every story tip belongs to `integration`. This module decides only the
order of those calls and what a run records.
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py tests/test_cli.py tests/test_integration.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py tests/test_cli.py
git commit -m "feat: run Integrate at the end of run_milestone"
```

---

### Task 4: The production-wiring milestone tests expect the new keys

Tier: the existing tests' own, the default-suite e2e tier under `tests/e2e/` (real `cli.app` through `CliRunner`, fake `claude` first on `PATH`). No new e2e scenario is added and `fake_claude.py` is not touched.

**Files:**
- Modify: `tests/e2e/conftest.py:52-60` (constants), `:312-344` (`parallel_board`)
- Modify: `tests/e2e/test_milestone_run.py:54-99`, `:123-199`
- Modify: `tests/e2e/test_parallel_milestone.py:94-133`, `:222-296`, `:299-355`

**Interfaces:**
- Consumes: the clean payload's `integrated` key and the lane-escalation payload (no `integrated`, no `phase`) from Task 3.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Assert `integrated` in the stacked-line e2e tests**

In `tests/e2e/test_milestone_run.py`, directly after `_all_cards` (ends at line 51) add:
```python
INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""


def _branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()
```

In `test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line`, directly after the `levels` assertion (ends at line 76) add:
```python
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(cli.worktree_for(root, INTEGRATION_BRANCH)),
        "merged": [stories["A"], stories["B"], stories["C"]],
        "resolved": [],
    }
    assert _is_ancestor(root, branches[subtasks["C"][-1]], INTEGRATION_BRANCH)
```

In `test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it`: directly after `assert "review-fail marker" in stopped["detail"]` (line 149) add:
```python
    # A lane escalation never reaches Integrate.
    assert "integrated" not in stopped and "phase" not in stopped, stopped
    assert INTEGRATION_BRANCH not in _branches(root)
```
directly after `assert finished["completed"] == [b1, c1]` (line 182) add:
```python
    assert finished["integrated"]["merged"] == [stories["A"], stories["B"], stories["C"]]
    assert finished["integrated"]["resolved"] == []
```
and directly after `assert idle["completed"] == []` (line 198) add:
```python
    assert idle["integrated"] == finished["integrated"]
```

- [ ] **Step 2: Assert `integrated` in the parallel e2e tests**

In `tests/e2e/test_parallel_milestone.py`, directly after `_run_two_lanes` (ends at line 84) add:
```python
INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""
```

In `test_two_lanes_overlap_in_implement_and_the_milestone_finishes`, directly after `assert data["completed"] == [a1, a2, b1, b2, c1]` (line 117) add:
```python
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(cli.worktree_for(root, INTEGRATION_BRANCH)),
        "merged": [stories["A"], stories["B"], stories["C"]],
        "resolved": [],
    }
    for tip in (branches[a2], branches[b2], branches[c1]):
        assert _is_ancestor(root, tip, INTEGRATION_BRANCH), tip
```

In `test_an_escalation_in_one_lane_stops_the_other_and_the_next_level_never_starts`, directly after `assert "also_escalated" not in data, data` (line 245) add:
```python
    assert "integrated" not in data and "phase" not in data, data
```
and directly before the final `assert _git(root, "rev-parse", "main").strip() == main_before` (line 296) add:
```python
    assert INTEGRATION_BRANCH not in _git(root, "branch", "--format=%(refname:short)").split()
```

In `test_a_relaunch_after_the_escalation_finishes_and_skips_done_subtasks`, directly after `assert finished["completed"] == expected` (line 343) add:
```python
    assert finished["integrated"]["merged"] == [stories["A"], stories["B"], stories["C"]]
    assert finished["integrated"]["resolved"] == []
```

- [ ] **Step 3: Run the e2e milestone tests to verify the parallel ones fail**

Run: `uv run pytest tests/e2e/test_milestone_run.py tests/e2e/test_parallel_milestone.py -q`
Expected: `test_milestone_run.py` PASSES (a stacked line merges cleanly). In `test_parallel_milestone.py`, `test_two_lanes_overlap_in_implement_and_the_milestone_finishes`, `test_one_lane_runs_the_level_s_stories_one_after_the_other`, `test_the_journal_of_a_two_lane_run_is_contiguous_and_rebuilds_the_projection` and `test_a_relaunch_after_the_escalation_finishes_and_skips_done_subtasks` FAIL with exit code 1: the envelope's data carries `"phase": "integrate"` and `"story"` = story B, because A's and B's tips both add `IMPLEMENTATION.md` (the conflict described under "A consequence the spec did not foresee").

- [ ] **Step 4: Fold `IMPLEMENTATION.md` with git's union driver on the parallel board**

In `tests/e2e/conftest.py`, directly after `FAKE_RENDEZVOUS_COUNT_ENV` and its docstring (ends at line 59) add:
```python
FAKE_IMPLEMENTATION_NAME = "IMPLEMENTATION.md"
"""Must equal `fake_claude.IMPLEMENTATION_NAME`: the one file the fake coder writes."""

UNION_ATTRIBUTE = f"{FAKE_IMPLEMENTATION_NAME} merge=union\n"
"""Git's built-in union merge driver for the fake coder's file.

Integrate (card a74f2cd6) merges every story tip into one branch. On
`parallel_board`, A and B are independent roots and the fake coder writes
`IMPLEMENTATION.md` on both, so their tips would conflict and need a resolver
the fake does not have (its `resolve` phase is sibling a37460b9's, as are the
conflict scenarios). Written to the git common dir's `info/attributes`, it
applies in every linked worktree, is in no tree and never shows in
`git status`, and it tells the fake nothing: it only lets git fold the two
versions, so these lane tests stay about lanes."""
```

In `parallel_board`, replace the line `root = fresh_project` (line 321) with:
```python
    root = fresh_project
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
```
and add this sentence to the end of the `parallel_board` docstring (before its closing `"""` on line 320): `The union attribute (\`UNION_ATTRIBUTE\`) lets Integrate fold A's and B's \`IMPLEMENTATION.md\` without a resolver.`

- [ ] **Step 5: Run the e2e milestone tests to verify they pass**

Run: `uv run pytest tests/e2e/test_milestone_run.py tests/e2e/test_parallel_milestone.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_milestone_run.py tests/e2e/test_parallel_milestone.py
git commit -m "test: expect Integrate in the milestone production-wiring tests"
```

---

### Task 5: Full verification

**Files:** none changed.

- [ ] **Step 1: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, `tests/e2e` included (real-harness tests skip as before). If anything fails, fix it in the task that owns the file and re-run; do not loosen an assertion.

- [ ] **Step 2: Confirm the out-of-scope files are untouched**

Run: `git diff --stat m5/task-merge-every-story-tip-6fea51ad -- src/agent_manager/integration.py src/agent_manager/steps/integrate.py src/agent_manager/workflow/builtin/integrate.yaml tests/e2e/fake_claude.py`
Expected: no output.
