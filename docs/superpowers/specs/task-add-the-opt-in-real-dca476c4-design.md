# Add the opt-in real-harness conflict test (card dca476c4)

Parent story f96f2b53 "Prove it against a real harness, and document it", under milestone db5b5a3b (milestone 5, Integrate). This card narrows Integrate addendum I7 and acceptance 7 (`docs/superpowers/specs/2026-09-25-integrate-design.md`) to one test module. The contract it checks is I1 to I6 of that addendum.

## Scope

The card delivers one new module, `tests/e2e/test_real_harness_integrate.py`, and nothing else. There are no source changes and no changes to `tests/e2e/conftest.py`, `pyproject.toml`, `README.md` or any spec. Documentation belongs to sibling card 4c78e3ea "Document Integrate", which is blocked on this card.

The module is the opt-in proof that Integrate resolves a real merge conflict with a real agent. A toy milestone has two independent stories that edit the same line of `calc.py`. The milestone runs through the real `claude` CLI with two lanes, and the test judges the integrated result with git.

## Structure (modelled on `tests/e2e/test_real_harness_parallel.py`)

- Module docstring. It cites I7 and acceptance 7 of the addendum, and the test tier (main spec section 14), in the style of the sibling real-harness modules. It must say:
  - The pipeline cannot run this test. It is excluded by default, and every run spends real money.
  - The real run is a human step afterwards: `uv run pytest -m e2e`.
  - Running the bare path still deselects the test through `addopts` and exits 5, so `-m e2e` has to be passed along with the path.
  - The marker is applied only in this module, never in conftest. Marking it from conftest would take `test_production_wiring.py` out of the default suite, which that file's `test_this_module_runs_in_the_default_suite_unmarked` forbids.
  - The helpers are re-declared rather than imported, because `--import-mode=importlib` puts nothing on `sys.path`.
- Module-level constants:
  - `pytestmark = pytest.mark.e2e`.
  - `BRANCH_PREFIX = "e2e-real-i"`. This prefix is new: `m1`, `m3`, `e2e-real`, `e2e-real-m` and `e2e-real-p` are already taken. The integration branch is `integration.integration_branch(BRANCH_PREFIX)`, which is `e2e-real-i-integrate`.
  - `VERIFY_COMMANDS = (f"{shlex.quote(sys.executable)} -m pytest -q",)`.
- Re-declared helpers, copied from the template: `_git`, `_is_ancestor`, `_add_card(root, title, parent, description)` (no `blocked_by`), `_top_level_functions` (uses `ast`), and `_seed_toy_repo`.
- Fixtures:
  - A module-scoped `real_claude` fixture, copied verbatim from the template. When `shutil.which("claude")` is None it calls `pytest.skip("the real `claude` CLI is not on PATH; ...")`.
  - A module-scoped toy-milestone fixture.
  - A module-scoped `completed_run` override that depends on `real_claude` and the toy milestone, and not on `fake_claude_bin`, so only the real claude is on PATH. It calls `orchestrate.run_milestone(milestone, repo_dir=project, base_branch="main", branch_prefix=BRANCH_PREFIX, commands=list(VERIFY_COMMANDS), max_concurrent=2)`. It passes no `runner_factory` and no `driver`. This is the same path `am run --milestone` takes (cli.py:1085-1103).
- Conftest fixtures it reuses: `toolchain`, `project` and `module_monkeypatch`.

## Toy repo and milestone

- `_seed_toy_repo` commits these files to `main`:
  - `calc.py`: a module docstring plus a line that is exactly `# OPERATIONS GO HERE`.
  - `test_calc.py`: a baseline test that passes. It only imports `calc` and asserts its docstring. Without it, pytest exits 5 and verify is red before any agent runs.
  - `.gitignore`, with the entries `__pycache__/` and `.pytest_cache/`.
- Milestone M has two independent stories and no `brd block`. Story A is created first and has subtask a1. Story B is created second and has subtask b1.
  - a1's description: replace the `# OPERATIONS GO HERE` line in `calc.py` with `def add(a, b): return a + b`, and add `test_add.py` asserting `calc.add(2, 3) == 5`.
  - b1's description: the same, with `sub(a, b)` returning `a - b`, and `test_sub.py` asserting `calc.sub(5, 3) == 2`.
  - Both descriptions also say to keep the existing tests passing, not to touch the other story's test file, and that the suite runs with `python -m pytest -q` from the repository root.
  - Both stories replace the same marker line, so their tips conflict when merged.
- The fixture returns:
  - the milestone, story and subtask ids;
  - each story's tip branch;
  - `integrate_branch`;
  - `main_sha`, which is `git rev-parse main` recorded before the run.

## Observable behavior asserted (one test function)

The test is named something like `test_the_real_claude_resolves_a_real_merge_conflict_at_integrate`. Git judges every fact that git can measure (card rule 3). The payload and the store are only cross-checks and the record of the resolver.

1. The run is done: `completed_run.get("done") is True`. It uses `.get` because an escalation payload has no `done`. The failure message includes the escalation fields (`phase`, `story`, `files`, `detail`).
2. The integration branch exists: `git rev-parse --verify refs/heads/e2e-real-i-integrate` succeeds. Its worktree is `cli.worktree_for(project, integrate_branch)`, which is a directory under `.claude/worktrees/`.
3. The payload: `completed_run["integrated"] == {"branch": integrate_branch, "worktree": <that worktree, as the payload serialises it>, "merged": [A, B], "resolved": [B]}`. B is the conflicting story because it is merged second (census order, I1).
4. The premise holds, judged by git: both story tips are ancestors of the integration branch.
5. Both functions are present. `git show <integrate_branch>:calc.py` defines both `add` and `sub` at top level, checked with `ast`. `test_add.py` and `test_sub.py` both exist on the branch.
6. No file contains conflict markers. `git grep -n -E '^(<<<<<<<|=======|>>>>>>>)( |$)' <integrate_branch>` finds nothing (exit 1).
7. No merge is in progress: `git -C <worktree> rev-parse -q --verify MERGE_HEAD` fails.
8. The worktree is clean: `git -C <worktree> status --porcelain` is empty.
9. The toy suite passes. Each command in `VERIFY_COMMANDS`, run in the integration worktree, exits 0. The tails of stdout and stderr are attached to the failure message.
10. The resolver's attempt is recorded. The test opens the run with `store.open_db` and `store.load_run(conn, completed_run["run_id"])`, and closes the connection as the template does. Exactly one story has `card_id == "integrate"` (`integration.INTEGRATE_STORY_ID`) and title `"Integrate"`. It has a single subtask whose `card_id` is B. That subtask's phase names are `["resolve", "verify"]`, and both phases have status `done`.
11. The base branch is untouched: `git rev-parse main` still equals `main_sha` (I5).
12. Nothing was pushed: `git for-each-ref refs/remotes` in the project is empty (I5, card rule 4).

## Error paths

- `claude` is not on PATH: every test in the module skips with the PATH message. `real_claude` runs before the toy milestone is seeded, so nothing is built and nothing is spent.
- `git` or `brd` is not on PATH: the conftest `toolchain` fixture skips, as it already does.
- The run escalates, for example at Integrate: assertion 1 fails and reports the escalation fields. The integration branch and worktree stay as they are for a human to inspect (I5). The test does no cleanup beyond what the `project` fixture already does.

## Out of scope

- `--no-integrate` and a milestone-aware `am resume`.
- Watch, retry and cancel.
- Cost capture.
- The reviewer's Plan-Hash brief.
- Marking slow tests, and per-story readiness.
- README and spec status notes, which belong to card 4c78e3ea.
- Fake-claude Integrate tests, which are already in `tests/e2e/test_integrate.py`.

## Tests and tier

The placement rule is main spec section 14 (Testing, line 495). A test that drives a real harness belongs to the end-to-end tier: one slow, opt-in test, marked and excluded from the default suite. It is not a unit test or a step test, and it does not use the fake claude.

| Test | Tier |
|---|---|
| `test_the_real_claude_resolves_a_real_merge_conflict_at_integrate` (assertions 1-12) | End-to-end, opt-in real harness: `tests/e2e/`, module-level `pytest.mark.e2e` |

## Card-level verification (without a real run)

The pipeline cannot run the real test, so the card is verified in three ways:

- Collection: `uv run pytest --collect-only -q -m e2e` lists the new test.
- Deselection: `uv run pytest` does not run the new test, and the whole default suite stays green, including `tests/e2e` (card rule 2). `test_production_wiring.py` is still in the default suite.
- Skip: when `claude` is not on PATH, `uv run pytest -m e2e tests/e2e/test_real_harness_integrate.py` reports a skip whose reason contains "the real `claude` CLI is not on PATH".

The real run is a human step afterwards: `uv run pytest -m e2e`.
