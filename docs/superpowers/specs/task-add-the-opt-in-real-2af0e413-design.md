# Subtask 2af0e413: Add the opt-in real-harness parallel test

Parent story 4633be8c ("Prove it: parallel under a fake claude, against a real harness, and documented"), milestone cdbfa10d. Blocked by 1976123f (done). This spec narrows parallel-stories addendum P7 and acceptance 7 (`docs/superpowers/specs/2026-09-24-parallel-stories-design.md`) to one test module.

## Scope

Add one new module, `tests/e2e/test_real_harness_parallel.py`. It is the single opt-in real-harness test for parallel stories: a toy milestone with two independent stories, driven through the real `orchestrate.run_milestone(..., max_concurrent=2)` against the real `claude -p`, with no `runner_factory`, no `driver`, and no fake `claude` on `PATH`.

Model it on `tests/e2e/test_real_harness_milestone.py`:

- `pytestmark = pytest.mark.e2e` at module level, in this module only. Do not mark from `tests/e2e/conftest.py`. That would pull the free fake-claude `test_production_wiring.py` out of the default suite and break its `test_this_module_runs_in_the_default_suite_unmarked`.
- Re-declare these helpers in the module rather than importing them, because `--import-mode=importlib` puts nothing on `sys.path`: `_git`, `_plan_hashes`, `_add_card` (with `--description`), `_seed_toy_repo`, `_top_level_functions`, `VERIFY_COMMANDS`, `TOY_GITIGNORE_ENTRIES`, and `PLAN_HASH_TRAILER`. `_block` is not needed because the stories are independent. `_seed_toy_repo` is extended so the one baseline commit also seeds `hello.py` and a passing `test_hello.py`. Without a baseline test for `hello.py`, pytest would exit 5 and verification would fail before any agent runs.
- Use the conftest fixtures by name: `toolchain` / `project` (the temp repo on `main` with a board) and `module_monkeypatch`, as the template does.
- Add a `real_claude` module fixture that follows the template. It calls `shutil.which("claude")`. If that returns nothing, it calls `pytest.skip` with a message saying the real `claude` CLI is not on PATH and that `uv run pytest` deselects this test.
- `BRANCH_PREFIX = "e2e-real-p"`. It must differ from `m1`, `m3`, `e2e-real` and `e2e-real-m`.
- Add a `toy_parallel_milestone` module fixture. It seeds the repo, then creates milestone M with two stories and no `brd block` between them:
  - Story A has subtask a1: "add `add(a, b)` to `calc.py` and `test_add` in `test_calc.py`".
  - Story B has subtask b1: "add `greet(name)` to `hello.py` and a test in `test_hello.py`".
  - The descriptions carry the full instructions and name `python -m pytest -q` as the suite command.
  - The fixture records branch names via `dag.task_branch(BRANCH_PREFIX, board.show(...))` and records `main_sha` before the run.
- Override `completed_run` without `fake_claude_bin`. It depends on `real_claude` first, so a missing `claude` skips before any board is built. It calls `orchestrate.run_milestone(milestone, repo_dir=project, base_branch="main", branch_prefix=BRANCH_PREFIX, commands=list(VERIFY_COMMANDS), max_concurrent=2)`. `max_concurrent` is the keyword-only parameter at `src/agent_manager/orchestrate.py:476` on the m4 line. It defaults to 1.
- The module docstring must state the following:
  - The pipeline cannot run this test.
  - The test is excluded by `addopts`' `-m "not e2e"` (`pyproject.toml:38`; the marker is registered at `:30`).
  - Every run spends real money.
  - The real run is a human step afterwards: `uv run pytest -m e2e`. A bare path is still deselected and exits 5.
  - The marker lives only in this module, and why.

Out of scope: `tests/e2e/fake_claude.py` and the fake-claude parallel tests (owned by 1976123f), README and main-spec docs (owned by a2dad516), and anything in `src/`. The addendum's section 5 exclusions are also out of scope: Integrate, per-story readiness, milestone-aware resume, watch/retry/cancel, cost capture, the reviewer's Plan-Hash brief, and Ctrl-C.

## Observable behavior (one test, `test_the_real_claude_drives_two_independent_stories_in_parallel`)

1. `completed_run.get("done") is True`. The failure message reports `story`, `subtask`, `failed_phase`, `detail` and `warnings` via `.get`, because an escalation payload lacks `completed`. `completed_run["completed"]` contains both a1 and b1 in any order: under concurrency the order is not fixed.
2. On the board (`board.show(...).status`), a1 and b1 are `done`, and both stories and the milestone are `done` by rollup.
3. The two branches were cut from the base independently:
   - `git merge-base --is-ancestor main <branch>` returns 0 for each branch.
   - `git merge-base --is-ancestor` returns nonzero both ways between the two branches.
4. `rev-list main..<branch>` is non-empty for each branch. Every commit in that range carries at least one `Plan-Hash:` trailer at column 0, and the branch has exactly one distinct value.
5. On each tip's worktree (`cli.worktree_for(project, branch)`), every `VERIFY_COMMANDS` entry exits 0. `_top_level_functions` shows `add` in a1's `calc.py` and `greet` in b1's `hello.py`.
6. `git rev-parse main` equals the recorded `main_sha`.
7. Load the run tree with `store.open_db(project)` and `store.load_run(conn, completed_run["run_id"])`, closing the connection in `finally`. Find the `implement` phase of each subtask. The timestamps are on `PhaseRun.started_at` / `ended_at` (`models.py:97-98`), not on `Attempt`. The two intervals must overlap: `a.started_at < b.ended_at and b.started_at < a.ended_at`, with all four timestamps non-None.

## Error paths

- If `claude` is not on PATH, the module skips with the PATH message and never builds a board or spends money.
- If a real run escalates, the done assertion fails with the escalation's story, subtask, phase and detail, rather than a `KeyError`.
- If implement intervals don't overlap, which means the run was effectively sequential, the failure message prints all four timestamps.

## Tests and tier

Per main spec section 14 ("End to end -- one slow, opt-in test with a real harness, marked and excluded from the default suite"), the only new test belongs in the e2e tier: `tests/e2e/test_real_harness_parallel.py::test_the_real_claude_drives_two_independent_stories_in_parallel`, marked `e2e`. It does not belong in the unit or steps tiers. No other tests are added.

## Verification

- Full suite: `uv run pytest` stays green. That includes all of `tests/e2e` and the existing check that `max_concurrent=1` behaves sequentially. The new test is deselected.
- Collection: `uv run pytest -m e2e --collect-only` lists the new test.
- Default deselection: `uv run pytest --collect-only` does not list it.
- Skip: with `claude` absent from PATH, `uv run pytest -m e2e tests/e2e/test_real_harness_parallel.py -rs` reports it skipped with the PATH message. Never run it with `claude` on PATH in the pipeline.
- The real paid run is a human step afterwards: `uv run pytest -m e2e`.
