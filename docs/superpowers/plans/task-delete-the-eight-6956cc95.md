<!-- task-pipeline: validated -->
# Delete the default-suite-unmarked meta tests (subtask 6956cc95)

Parent story: e3555d8c "Retier tests/e2e/* and remove its duplicate twins". Governing spec: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V6 (lines 152-159), with V2 (lines 94-125) supplying the structural replacement.

## Scope

Delete the function `test_this_module_runs_in_the_default_suite_unmarked` (signature, docstring, body, and the blank lines that separate it from its neighbours) from every `tests/e2e/*.py` module that defines one, **in this subtask's own worktree**. The card and V6 both say 8, and this worktree has exactly 8 definitions:

| Module | Line |
|---|---|
| `tests/e2e/test_exactly_once.py` | 235 |
| `tests/e2e/test_board_comments.py` | 330 |
| `tests/e2e/test_multi_process.py` | 101 |
| `tests/e2e/test_integrate.py` | 208 |
| `tests/e2e/test_parallel_milestone.py` | 99 |
| `tests/e2e/test_milestone_run.py` | 127 |
| `tests/e2e/test_production_wiring.py` | 170 |
| `tests/e2e/test_milestone_resume.py` | 429 |

`tests/e2e/test_run_board.py` does not exist on this subtask's base (it ships with the milestone 14 `am run --board` work, which this worktree does not have — see "Out of scope"). Do not go looking for a ninth module. If a future rebase onto a base that includes `test_run_board.py` introduces a ninth `test_this_module_runs_in_the_default_suite_unmarked`, delete it the same way, but that is not expected here; confirm with `grep -rn "def test_this_module_runs_in_the_default_suite_unmarked" tests/` before starting and delete whatever it actually lists.

Why: V2's `pytest_collection_modifyitems` hook in `tests/conftest.py` marks every item under `tests/e2e/` as `e2e_fake` based on its directory. That hook makes the per-module "no marker reaches this module" guards obsolete. They would also fail outright once the hook adds `e2e_fake`, because they assert that the marker set is empty.

Also remove anything the deletion leaves unused, such as a `request`-only helper or an import that only these functions referenced. Do not change anything else in these modules.

## Out of scope

- The scenario twins (`e2e/test_parallel_milestone.py:477,441,513,245`, `e2e/test_integrate.py:360,302`, `e2e/test_milestone_run.py:69`, `e2e/test_board_comments.py:290,337,438,543`, `e2e/test_production_wiring.py:41`). Sibling subtask 32aa47aa owns them, and it is blocked on this one.
- `tests/e2e/test_real_harness*.py`. These only mention the test name in docstring prose and already carry `pytestmark = pytest.mark.e2e`. They have no function to delete.
- Docstrings in other tests that cross-reference these guards, such as "Like `test_milestone_run.py`'s guard". Only touch them if the deleted function's own module would otherwise point at a test that no longer exists.
- Also out of scope: pytest-xdist or parallel execution as the canonical verify step, and any change to the pygents engine, checkpoint format, harness adapter contract, or `dispatch.py`'s `LauncherFn` seam. The e2e tier must not drop below its current 5 tests, and what those tests verify must not change. No milestone 14 `am run --board` work.

## Precondition (sequencing)

The blocker story 186fe934 ("Define and enforce the test tiers") is marked done on the board, but its code is not on current master. Current master has no `e2e_fake` marker in `pyproject.toml` and no collection hook in `tests/conftest.py`. That work lives on the m15 task branches (marker registration, the directory auto-mark e59eddd/465d1d4, and others). This subtask's worktree must be built on a base that already includes those branches.

Before deleting anything, confirm that `uv run pytest --collect-only -m e2e_fake` lists `tests/e2e` items. If it lists none, the hook is missing. Stop and report it as a blocker rather than deleting. Deleting the guards without the hook in place would remove the only enforcement.

## Observable behaviour

- `grep -rn "def test_this_module_runs_in_the_default_suite_unmarked" tests/` returns nothing.
- `uv run pytest --collect-only -m e2e_fake` still lists every `tests/e2e` module it listed before. The only change in the collected items is the 8 removed meta tests, and every scenario/wiring test is still present.
- `uv run pytest` passes.

## Error paths

- Hook absent from the base: stop before deleting and report the blocker (see Precondition).
- A module would become empty of tests or fail to import after the deletion: this should not happen, since every listed module keeps its scenario/wiring tests. If it does, stop and report rather than deleting more.

## Tests

No tests are added or changed. The subtask only deletes. All 8 deleted functions were `e2e_fake`-tier items (auto-marked by directory under V1/V2). The tests left in those modules stay in the `e2e_fake` tier because their location is unchanged. Under the V1 placement rule, any test a later subtask adds must take its tier from what it touches:

- fake-driven: `unit`
- real git only: `git`
- real brd: `brd`
- fake-claude subprocess: `e2e_fake`
- stress: `soak`
- real claude: `e2e` with a `justification:` line

## Verification

1. Before the change: `uv run pytest --collect-only -m e2e_fake`. Record the list of modules and items.
2. After the change: `uv run pytest --collect-only -m e2e_fake`. It must list the same modules, with exactly the 8 meta tests gone.
3. `uv run pytest`.

---

# Delete the Default-Suite-Unmarked Meta Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the 8 obsolete `test_this_module_runs_in_the_default_suite_unmarked` guard functions from `tests/e2e/`. The directory auto-mark hook in `tests/conftest.py` replaces them structurally, and under `-m e2e_fake` they fail today because the hook's `e2e_fake` marker shows up in `own_markers`.

**Architecture:** This is a pure deletion. Each guard is a self-contained function that takes only the built-in `request` fixture. None of them uses a module import, helper or fixture that nothing else uses, so deleting the function, its docstring, its body and the two blank lines after it is the whole change in each file. The "failing test" is the guard set itself: under `-m e2e_fake` all 8 fail on this base. Once they are deleted, that selection collects nothing, and the rest of the `e2e_fake` collection is unchanged.

**Tech Stack:** Python, pytest (run through `uv run pytest`).

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-delete-the-eight-6956cc95/docs/superpowers/specs/task-delete-the-eight-6956cc95-design.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-delete-the-eight-6956cc95` on branch `m15/task-delete-the-eight-6956cc95`. Run every command from that directory.

## Global Constraints

- Only delete the 8 `test_this_module_runs_in_the_default_suite_unmarked` functions. Do not change any other line in these modules.
- Do not touch the scenario twins: `tests/e2e/test_parallel_milestone.py:477,441,513,245`, `tests/e2e/test_integrate.py:360,302`, `tests/e2e/test_milestone_run.py:69`, `tests/e2e/test_board_comments.py:290,337,438,543`, `tests/e2e/test_production_wiring.py:41`. Sibling subtask 32aa47aa owns them.
- Do not touch `tests/e2e/test_real_harness.py`, `tests/e2e/test_real_harness_integrate.py`, `tests/e2e/test_real_harness_milestone.py` or `tests/e2e/test_real_harness_parallel.py`. They only mention the name in docstrings.
- Do not change `tests/conftest.py`, `pyproject.toml`, the pygents engine, the checkpoint format, the harness adapter contract, or `dispatch.py`'s `LauncherFn` seam. Do not use pytest-xdist as the verify step.
- The `e2e` tier stays at its current 5 tests, verifying the same things.
- No new tests. If a later subtask adds one, it picks its tier by what it touches: fake-driven → `unit`, real git only → `git`, real brd → `brd`, fake-claude subprocess → `e2e_fake`, stress → `soak`, real claude → `e2e` with a `justification:` line.
- Canonical verification: `uv run pytest`.

## Review Focus

- The hook could be missing from the base, so `-m e2e_fake` collects no `tests/e2e` items. In that case the work must stop before any deletion, not delete anyway. Task 1 Step 1 checks this.
- An edit could swallow the neighbouring test. Several guards sit directly above a real test (`test_no_kill_switch_is_left_armed_for_later_tests` in two modules, `test_escalation_then_resume_...`, `test_am_returns_the_exit_code_...`), and `test_milestone_run.py`'s guard sits above the `_load_run` helper. The before/after collection diff in Task 1 Step 13 must show exactly the 8 guard node ids removed and nothing else.
- An edit could leave the file with a module-level code formatting break, such as one blank line or three blank lines between top-level definitions. Every replacement below keeps exactly two blank lines between the preceding and following definitions. Steps 11 and 13 run collection, which would surface any import or syntax error.
- `test_exactly_once.py`'s guard refers to `test_resume_after_implement_returns_adopts_it` by name. That function must stay, because only the reference is removed. The collection diff confirms it is still present.
- The `test_no_kill_switch_is_left_armed_for_later_tests` tests in `test_exactly_once.py` and `test_milestone_resume.py` say "Kept last in the module". They must stay last. They do, because only the function above them is removed.

---

### Task 1: Delete the 8 guard functions

**Files:**
- Modify: `tests/e2e/test_exactly_once.py:235-247`
- Modify: `tests/e2e/test_board_comments.py:330-336`
- Modify: `tests/e2e/test_multi_process.py:101-107`
- Modify: `tests/e2e/test_integrate.py:208-214`
- Modify: `tests/e2e/test_parallel_milestone.py:99-105`
- Modify: `tests/e2e/test_milestone_run.py:127-133`
- Modify: `tests/e2e/test_production_wiring.py:170-183`
- Modify: `tests/e2e/test_milestone_resume.py:429-435`
- Test: the same modules, collected under `-m e2e_fake` (tier `e2e_fake`, applied by directory by `tests/conftest.py`'s `pytest_collection_modifyitems`)

**Interfaces:**
- Consumes: `tests/conftest.py`'s directory auto-mark (`default_tier_marker`, `pytest_collection_modifyitems`), which is already on this base, and the `e2e_fake` marker plus addopts `-m "not brd and not e2e_fake and not soak and not e2e"` in `pyproject.toml`.
- Produces: no new names. After this task, no `tests/e2e/*.py` defines `test_this_module_runs_in_the_default_suite_unmarked`. Sibling 32aa47aa relies on that.

- [ ] **Step 1: Check the precondition (the hook is present)**

Run:

```bash
uv run pytest --collect-only -q -m e2e_fake | grep -c '^tests/e2e/'
```

Expected: a number greater than 0. If it prints `0`, the auto-mark hook is missing from this base. STOP and report a blocker naming story 186fe934. Do not continue.

- [ ] **Step 2: Confirm the set of definitions to delete**

Run:

```bash
grep -rn "def test_this_module_runs_in_the_default_suite_unmarked" tests/
```

Expected: exactly these 8 lines, and no `tests/e2e/test_run_board.py`:

```
tests/e2e/test_exactly_once.py:235:def test_this_module_runs_in_the_default_suite_unmarked(request):
tests/e2e/test_board_comments.py:330:def test_this_module_runs_in_the_default_suite_unmarked(request):
tests/e2e/test_multi_process.py:101:def test_this_module_runs_in_the_default_suite_unmarked(request):
tests/e2e/test_integrate.py:208:def test_this_module_runs_in_the_default_suite_unmarked(request):
tests/e2e/test_parallel_milestone.py:99:def test_this_module_runs_in_the_default_suite_unmarked(request):
tests/e2e/test_milestone_run.py:127:def test_this_module_runs_in_the_default_suite_unmarked(request):
tests/e2e/test_production_wiring.py:170:def test_this_module_runs_in_the_default_suite_unmarked(request):
tests/e2e/test_milestone_resume.py:429:def test_this_module_runs_in_the_default_suite_unmarked(request):
```

The order may differ. If a ninth line appears, delete that one the same way, by removing the function and the two blank lines after it, and count 9 everywhere below that says 8.

- [ ] **Step 3: Record the baseline e2e_fake collection, then run the guards to see them fail (RED)**

```bash
uv run pytest --collect-only -q -m e2e_fake > /tmp/6956cc95-e2e_fake-before.txt
uv run pytest tests/e2e -m e2e_fake -k test_this_module_runs_in_the_default_suite_unmarked
```

Expected: 8 failed. Each failure is an `AssertionError` on `{mark.name for mark in request.node.own_markers} == set()`, with `{'e2e_fake'}` on the left. The hook's `item.add_marker` appends to `own_markers`. In `test_exactly_once.py`, the first assert checks the proof function's `pytestmark` and passes, so its guard fails on the second assert. The hook's marker breaks the guards, and that is the regression this task removes.

- [ ] **Step 4: Delete the guard in `tests/e2e/test_exactly_once.py`**

Replace this text:

```python
    assert _implement_commits(root, branch) == 1
    assert _load_run(root, run_id).status == "done"


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Review Focus 2: no `e2e` marker may reach this module, or the
    exactly-once proof stops running on every `uv run pytest`.

    The proof's own marks are read off the function: a mark on it deselects
    the proof but not this guard, so checking only this guard's node would
    never see it."""
    proof = test_resume_after_implement_returns_adopts_it
    assert {mark.name for mark in getattr(proof, "pytestmark", [])} == set()
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_no_kill_switch_is_left_armed_for_later_tests():
```

with:

```python
    assert _implement_commits(root, branch) == 1
    assert _load_run(root, run_id).status == "done"


def test_no_kill_switch_is_left_armed_for_later_tests():
```

- [ ] **Step 5: Delete the guard in `tests/e2e/test_board_comments.py`**

Replace this text:

```python
    assert f"next: `git merge {INTEGRATION_BRANCH}`" in lines, end.body


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_milestone_run.py`'s guard: no `e2e` marker may reach this
    module, or outcome comments stop being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_escalation_then_resume_keeps_escalation_and_appends_resumed_done(
```

with:

```python
    assert f"next: `git merge {INTEGRATION_BRANCH}`" in lines, end.body


def test_escalation_then_resume_keeps_escalation_and_appends_resumed_done(
```

- [ ] **Step 6: Delete the guard in `tests/e2e/test_multi_process.py`**

Replace this text:

```python
        stdout=subprocess.PIPE,
        text=True,
    )


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or the multi-process proof stops
    running on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_am_returns_the_exit_code_and_the_parsed_envelope(tmp_path, am):
```

with:

```python
        stdout=subprocess.PIPE,
        text=True,
    )


def test_am_returns_the_exit_code_and_the_parsed_envelope(tmp_path, am):
```

- [ ] **Step 7: Delete the guard in `tests/e2e/test_integrate.py`**

Replace this text:

```python
        [sys.executable, "-B", "check.py"], cwd=cwd, capture_output=True, text=True
    )


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or Integrate stops being checked
    on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_stories_that_touch_different_files_integrate_with_no_resolver(
```

with:

```python
        [sys.executable, "-B", "check.py"], cwd=cwd, capture_output=True, text=True
    )


def test_stories_that_touch_different_files_integrate_with_no_resolver(
```

- [ ] **Step 8: Delete the guard in `tests/e2e/test_parallel_milestone.py`**

Replace this text:

```python
INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or parallel wiring stops being
    checked on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_two_lanes_overlap_in_implement_and_the_milestone_finishes(
```

with:

```python
INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""


def test_two_lanes_overlap_in_implement_and_the_milestone_finishes(
```

- [ ] **Step 9: Delete the guard in `tests/e2e/test_milestone_run.py`**

Replace this text:

```python
    rows = checkpoint_rows(root, data["run_id"])
    assert rows > 0, rows


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_production_wiring.py`'s guard: no `e2e` marker may reach this
    module, or the milestone wiring stops being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def _load_run(root: Path, run_id: str) -> models.Run:
```

with:

```python
    rows = checkpoint_rows(root, data["run_id"])
    assert rows > 0, rows


def _load_run(root: Path, run_id: str) -> models.Run:
```

- [ ] **Step 10: Delete the guards in `tests/e2e/test_production_wiring.py` and `tests/e2e/test_milestone_resume.py`**

In `tests/e2e/test_production_wiring.py`, replace this text:

```python
    assert f"\n## plan_hash\n{expected}\n" in brief, brief


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Spec "Suite placement": this test costs nothing and must keep running by
    default. Sibling 34d3388b registers the `e2e` marker and adds
    `-m "not e2e"` to addopts; when it lands, nothing in THIS module may carry
    that marker, or the production wiring stops being checked on every run.

    Asserted against the collected node's markers rather than the file's text:
    a text scan would trip over its own assertion strings, and a marker applied
    from a conftest would not appear in this file at all.
    """
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_the_engine_authored_the_docs_commit_before_the_coder_ran(
```

with:

```python
    assert f"\n## plan_hash\n{expected}\n" in brief, brief


def test_the_engine_authored_the_docs_commit_before_the_coder_ran(
```

In `tests/e2e/test_milestone_resume.py`, replace this text:

```python
        len(read_fake_log(run_id)),
    ) == before


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_milestone_run.py`'s guard: no `e2e` marker may reach this
    module, or milestone resume stops being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_no_kill_switch_is_left_armed_for_later_tests():
```

with:

```python
        len(read_fake_log(run_id)),
    ) == before


def test_no_kill_switch_is_left_armed_for_later_tests():
```

- [ ] **Step 11: Confirm the guards are gone (GREEN)**

```bash
grep -rn "def test_this_module_runs_in_the_default_suite_unmarked" tests/
uv run pytest tests/e2e -m e2e_fake -k test_this_module_runs_in_the_default_suite_unmarked
```

Expected: `grep` prints nothing and exits 1. `pytest` reports `no tests ran` with every item deselected and exits 5, with no collection errors. A collection or import error in any modified module means an edit broke the file. Fix that edit, and do not delete anything else.

The deleted guards used only the built-in `request` fixture and names defined in their own modules, so no imports or helpers become unused. Confirm the one name the `test_exactly_once.py` guard referenced is still defined:

```bash
grep -n "def test_resume_after_implement_returns_adopts_it" tests/e2e/test_exactly_once.py
```

Expected: one line.

- [ ] **Step 12: Record the after-collection**

```bash
uv run pytest --collect-only -q -m e2e_fake > /tmp/6956cc95-e2e_fake-after.txt
```

- [ ] **Step 13: Diff the collections: exactly the 8 guards removed**

```bash
diff <(grep '^tests/' /tmp/6956cc95-e2e_fake-before.txt) <(grep '^tests/' /tmp/6956cc95-e2e_fake-after.txt)
diff <(grep '^tests/' /tmp/6956cc95-e2e_fake-before.txt | cut -d: -f1 | sort -u) <(grep '^tests/' /tmp/6956cc95-e2e_fake-after.txt | cut -d: -f1 | sort -u)
```

Expected from the first `diff`: only removal lines (`<`), exactly 8 of them, and each one ends `::test_this_module_runs_in_the_default_suite_unmarked`, one per module from Step 2. There are no `>` lines, and no other node id disappears. Expected from the second `diff`: no output, so the same set of modules is still collected. The `N/M tests collected` summary line in the after-file shows 8 fewer selected items than the before-file.

- [ ] **Step 14: Run the full suite**

```bash
uv run pytest
```

Expected: passes, exit code 0. The deleted guards were already deselected by the default addopts, so the default run's selected count does not change.

- [ ] **Step 15: Run the opt-in `e2e_fake` tier over the touched modules**

```bash
uv run pytest -m e2e_fake tests/e2e/test_exactly_once.py tests/e2e/test_board_comments.py tests/e2e/test_multi_process.py tests/e2e/test_integrate.py tests/e2e/test_parallel_milestone.py tests/e2e/test_milestone_run.py tests/e2e/test_production_wiring.py tests/e2e/test_milestone_resume.py
```

Expected: every collected test passes. A failure in a test that is not a guard, and that also fails on the pre-change base (check with `git stash; <same command>; git stash pop`), is pre-existing and outside this subtask's scope. Report it rather than fixing it here.

- [ ] **Step 16: Commit**

```bash
git add tests/e2e/test_exactly_once.py tests/e2e/test_board_comments.py tests/e2e/test_multi_process.py tests/e2e/test_integrate.py tests/e2e/test_parallel_milestone.py tests/e2e/test_milestone_run.py tests/e2e/test_production_wiring.py tests/e2e/test_milestone_resume.py
git commit -m "test: delete the 8 default-suite-unmarked e2e guards made obsolete by the directory auto-mark (V6)"
```
