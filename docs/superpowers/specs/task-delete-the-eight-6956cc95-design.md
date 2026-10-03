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
