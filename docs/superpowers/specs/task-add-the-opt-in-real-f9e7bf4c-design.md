# Subtask f9e7bf4c: add the opt-in real-harness milestone test

Date: 2026-09-24
Parent: story 5017dd8c "Prove it against a real harness, and document it" (milestone 99e178cb)
Narrows: `2026-09-24-orchestration-design.md` O8 and section 3 acceptance 6. Also main spec section 14 (Testing) and `2026-09-23-real-harness-design.md` R4.

## Scope

Add one new test module, `tests/e2e/test_real_harness_milestone.py`. It drives a real two-story toy milestone through `orchestrate.run_milestone` against the real `claude -p` and asserts the result on the board and in git.

This card changes nothing in `src/`, `tests/e2e/conftest.py`, `pyproject.toml`, `README.md` or any spec. README and the main spec's section 10 status note belong to sibling 7b6ad9bd, which is blocked by this card.

The real, paid run is a human step (O8, acceptance 6). The pipeline cannot exercise it: the module is excluded from the default suite and every run spends real money. The module docstring must say this in those terms and give the command `uv run pytest -m e2e`. It must also note that a bare path invocation is still deselected by `addopts` and exits 5, so `-m e2e` has to be passed alongside a path.

Out of scope: any fake `claude` (this module uses only the real one), parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, and verification discovery.

## The driver this test calls

This test calls `orchestrate.run_milestone` (`src/agent_manager/orchestrate.py:235`) directly, the same way `test_real_harness.py` calls `cli.run_card`. It passes no `runner_factory` and no `driver`, so `cli.drive_subtask` and `cli.default_runner_factory` resolve the real `claude` on `PATH`:

```python
orchestrate.run_milestone(
    milestone_id,
    repo_dir=project,
    base_branch="main",
    branch_prefix=BRANCH_PREFIX,
    commands=list(VERIFY_COMMANDS),
)
```

On success it returns `{"done": True, "run_id", "levels", "completed", "tips", "warnings"}`, where `tips` is a list of `{"story", "tip"}`. On escalation it returns `{"escalated": True, "run_id", "level", "story", "subtask", "failed_phase", "detail", "warnings"}`. There is no `status` key. Branch names come from `dag.task_branch(BRANCH_PREFIX, board.show(id, repo_dir=...))`, the same way the conftest's `milestone_board` derives them. Never retype them by hand.

## Module shape

- `pytestmark = pytest.mark.e2e`, set in this module only. Never set it in the conftest: `test_production_wiring.py` asserts that its own module runs unmarked in the default suite.
- Local re-declarations, because `--import-mode=importlib` makes conftest and sibling names unimportable: `_git`, `_plan_hashes` (the column-0 trailer-value parser, copied from `test_real_harness.py`), `PLAN_HASH_TRAILER = "Plan-Hash:"`, a distinct `BRANCH_PREFIX` (e.g. `"e2e-real-m"`, distinct from `m1`, `m3` and `e2e-real`), and `VERIFY_COMMANDS`.
- `VERIFY_COMMANDS` must be a real, green test command for the toy suite, not `git rev-parse`. `verify.run_suite` splits commands with `shlex.split` and does not use a shell. So use `f"{shlex.quote(sys.executable)} -m pytest -q"`: the test's own interpreter already has pytest.
- A `real_claude` module fixture. When `shutil.which("claude")` is None it calls `pytest.skip` with the exact message from `test_real_harness.py:99-103` ("the real `claude` CLI is not on PATH; the opt-in e2e tier needs it ...").
- Reused conftest fixtures: `toolchain`, `project` (module-scoped git repo on `main`, brd board, `XDG_DATA_HOME` in tmp) and `module_monkeypatch`. The conftest's `cards` and `completed_run` are not used as they stand. `completed_run` is overridden in this module without `fake_claude_bin`. The function-scoped `fresh_project` and `milestone_board` do not fit a module-scoped paid run, so they are not used.

### Fixtures

1. `toy_milestone(project)`, module-scoped:
   - Seed the toy repo on `main` so the verify command is green before any agent runs: a module `calc.py` (for example with a docstring only), a trivial passing `test_calc.py`, and `__pycache__/` plus `.pytest_cache/` appended to the existing `.gitignore`. Commit all of it. Without the ignore entries, the verify run would dirty the worktree and trip `review_gate`. Without a baseline test, pytest exits 5.
   - Build the cards with a module-local `_add_card(root, title, parent=None, blocked_by=())` that follows `conftest.py:63-89` (`brd add`, then `brd block <id> --by <blocker>`). Re-declare it here, because it cannot be imported.
   - Milestone M.
   - Story A: subtask a1 "add `add(a, b)` to `calc.py` with a test", then subtask a2 "add `sub(a, b)` to `calc.py` with a test", blocked_by a1 so the census order is fixed.
   - Story B, blocked_by A: subtask b1 "add a CLI-free `calc(op, a, b)` to `calc.py` that dispatches to `add` and `sub`, with a test".
   - `brd add` has a `--description` flag (confirmed), but the conftest's `_add_card` does not pass it. The local `_add_card` takes an extra optional `description` argument and appends `--description`, so each subtask card carries its full instruction there and the title stays short.
   - Record `main_sha = git rev-parse main` after seeding and before the run.
   - Return the ids, the per-subtask branches, and `main_sha`.
2. `completed_run(real_claude, project, toy_milestone)`, module-scoped: the single `run_milestone` call shown above.

## Observable behavior asserted

One test function, `test_the_real_claude_drives_a_two_story_milestone_to_done`, reading the one module-scoped run:

- The payload has `done` True. On failure the assertion message carries the escalation fields (`story`, `subtask`, `failed_phase`, `detail`, `warnings`).
- `completed == [a1, a2, b1]`.
- `tips` contains `{"story": B, "tip": branch(b1)}`.
- Each of a1, a2, b1, A, B and M reads `done` through `board.show(id, repo_dir=project).status`. The payload alone is not proof, because `mark_done` is best effort. The story and milestone reach `done` only through rollup.
- Stacking, checked with `git merge-base --is-ancestor`:
  - `main` is an ancestor of `branch(a1)`.
  - `branch(a1)` is an ancestor of `branch(a2)`.
  - `branch(a2)` is an ancestor of `branch(b1)`, because story B roots on story A's tip.
- Plan-Hash trailers: for each subtask, `rev-list <predecessor>..<branch>` is non-empty. Every commit in the range has at least one `Plan-Hash:` trailer value, and the whole range has exactly one distinct value. The predecessors are `main`, then a1's branch, then a2's branch.
- The toy suite passes on the last tip. Run `VERIFY_COMMANDS` in `cli.worktree_for(project, branch(b1))` and expect exit 0. As a non-vacuity check, `calc.py` there defines `add`, `sub` and `calc`.
- `git rev-parse main` still equals `main_sha`, so the base branch is untouched.

## Error paths

- `claude` is not on `PATH`: the whole module skips with the PATH message. This happens before any board or git write that belongs to the run.
- `git` or `brd` is missing: the conftest's `toolchain` skips the module.
- The run escalates: the first assertion fails and shows the escalation payload. No retry is attempted.

## Tests and their tier

Per main spec section 14 (lines ~465-492), "end to end" is the slow, opt-in, real-harness tier. It is marked `e2e`, excluded from the default suite, and lives in `tests/e2e/` beside the unmarked fake-claude production-wiring tests.

| Test | Tier |
|---|---|
| `tests/e2e/test_real_harness_milestone.py::test_the_real_claude_drives_a_two_story_milestone_to_done` | End to end, real harness, opt-in. Marked `e2e`, excluded by default, run by a human. |

No other test is added. Any free or fake-claude milestone coverage belongs to the default tier (unmarked), not in this file.

## Verification (pipeline-runnable; the paid run is a human step)

1. `uv run pytest -m e2e --collect-only` lists the new test.
2. The default `uv run pytest` deselects it, and the whole default suite, `tests/e2e` included, stays green.
3. `uv run pytest -m e2e tests/e2e/test_real_harness_milestone.py`, with `claude` absent from `PATH`, reports the test as skipped with the PATH message.
