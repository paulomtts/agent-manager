# Mark the process- and git-touching tests explicitly (card 90a83582)

Parent story 83c9691c "Split test_fake_claude.py by what it actually touches" (milestone 66ed75cd). Narrows V7 of `docs/superpowers/specs/2026-10-02-test-tier-design.md` to one change set. Depends on V1/V2 (story 186fe934), which has already landed in this worktree: the markers are registered in `pyproject.toml:33-48` and the directory auto-mark lives in `tests/conftest.py:99-149` (`TIER_MARKERS`, `_DIRECTORY_TIERS`, `default_tier_marker`, `pytest_collection_modifyitems`).

## Scope

Choice for V7's "move vs. stay in place" fork: **stay in place**. The pure tests stay in `tests/e2e/test_fake_claude.py`, unmarked. The PR description must say this was the chosen option.

1. `tests/e2e/test_fake_claude.py`:
   - Every test function that calls `_run_fake` (helper at `:253`) gets an explicit `@pytest.mark.e2e_fake`. The card counts about 12 call sites, but line numbers drift; count by enclosing test function, not by call site or by the example line numbers here — walking the actual file's `_run_fake` call sites gives 8 such test functions (one of them, `test_the_fake_process_holds_before_it_implements`, also calls `_implement_repo` and gets `e2e_fake` only, per the next bullet).
   - Every test function that calls `_implement_repo` (helper at `:372`), directly or indirectly, and not `_run_fake` gets an explicit `@pytest.mark.git`. The card counts about 26 call sites; once `_review_worktree`/`_conflicted_repo` callers are included, the real set is 29 test functions. "Indirectly" matters here: `_review_worktree` (`:555`) and `_conflicted_repo` (`:1200`) each call `_implement_repo` themselves, so the tests that call *those* two helpers (for example around `:593, :608, :628` and `:1261, :1276, :1289, :1308, :1328, :1344, :1355, :1380`) build a real git repo too and need `git` just the same, even though `_implement_repo` does not appear in their own bodies. Do not mark only the tests that call `_implement_repo` by name; walk the call graph through `_review_worktree` and `_conflicted_repo` as well, or every unmarked test in this module that goes through those two helpers will run in the unit tier, where `git` is stubbed to exit 99, and fail.
   - A test that calls both helpers (for example around `:946/:952` and `:766/:783`, if they turn out to share a function) gets `e2e_fake` only, never both, because it spawns the fake-claude subprocess. One test gets one tier marker.
   - Every other test (about 42 pure parsing, schema and payload tests, once the `_review_worktree`/`_conflicted_repo` tests above are correctly counted as `git`) gets no marker.
   - Rewrite the module docstring (`:1-11`) so it stays accurate. It must say three things. First, tier follows what a test touches, not its directory (V1). Second, the `_run_fake` tests are `e2e_fake` and the `_implement_repo` tests are `git`, both marked explicitly. Third, this module is the one exception to the `tests/e2e/` directory auto-mark: its unmarked tests are pure and belong in the default `unit` tier, and the reason is given. Replace the old "four `_run_fake` tests" and "design §14" wording, because that section has been superseded. Keep the paragraph about loading `fake_claude.py` by path.
2. `tests/conftest.py`: as written, the auto-mark gives `e2e_fake` to every unmarked item under `tests/e2e/`. That would pull the pure tests out of the default run. Scope the auto-mark so this one module is exempt. Use a named, single-entry exemption next to `_DIRECTORY_TIERS`, keyed by the path relative to `tests/` (`e2e/test_fake_claude.py`), and apply it in `default_tier_marker`, which then returns `None` for that path. Add a comment pointing to this module's docstring. Do not change any other module's auto-mark behaviour, the budget check, the PATH shim, the e2e cap, or the skip hook.

## Observable behaviour

- `uv run pytest --collect-only` (default `addopts` `-m`) still collects about 42 tests from `tests/e2e/test_fake_claude.py`, and none of them calls `_run_fake` or `_implement_repo` (directly or, for `_implement_repo`, through `_review_worktree`/`_conflicted_repo`).
- `uv run pytest -m e2e_fake --collect-only` includes exactly the `_run_fake` tests from this module, plus the other `tests/e2e/` modules as before.
- `uv run pytest -m git --collect-only` includes exactly the `_implement_repo`-only tests from this module, plus the existing `git` tests.
- `uv run pytest` passes in full. `uv run pytest -m e2e_fake` passes on its own (V7's acceptance line in §7 of the tier doc).
- Every other file under `tests/e2e/` and `tests/steps/` is auto-marked exactly as before.

## Error paths and constraints

- Unmarked pure tests now run under the unit-tier PATH shim (`brd`, `git` and `claude` stubs that exit 99) and the 0.5s per-test budget. A pure test that turns out to spawn `git` or `claude`, or to exceed 0.5s, is misclassified. Give it the tier that matches what it touches. Do not weaken the shim or the budget.
- `git`-marked tests have a 2s per-test budget. A test that calls `_implement_repo` and breaks that budget must be reported in the PR, not silenced. Moving it to `e2e_fake` is allowed only if it really drives the fake-claude process.
- Do not add a module-level `pytestmark`. That would mark the pure tests and defeat the split.
- Out of scope (tier doc §8 and the card): pytest-xdist or any parallelism; pygents engine, checkpoint format, harness adapter contract, `dispatch.py`'s `LauncherFn`; reducing or changing the 5 `e2e` tests; milestone 14 `am run --board`; V3 FakeBoard, V4/V5 conversions, V6 deletions, V8 rollup/board split, V9 justification lines.

## Tests

Tier placement follows V1 (§3 of the tier doc): tier is set by what the test touches, and directory only supplies a default that an explicit marker overrides.

- `tests/test_conftest_tiers.py`: add `test_default_tier_marker_exempts_fake_claude_module`, which asserts `default_tier_marker(PurePosixPath("e2e/test_fake_claude.py"), set()) is None`. Tier: **unit**, because it is a pure function call with no subprocess.
- `tests/test_conftest_tiers.py`: add `test_default_tier_marker_exemption_is_file_scoped`, which asserts that `e2e/test_fake_claude_other.py` and `e2e/sub/test_fake_claude.py` still get `e2e_fake`, so the exemption stays a single exact path. Tier: **unit**, pure.
- The existing `_run_fake` tests in `test_fake_claude.py`: tier **e2e_fake**, because they spawn the fake-claude script as a child process. No changes to their bodies.
- The existing `_implement_repo` tests in `test_fake_claude.py`: tier **git**, because they build real git repos in `tmp_path` and use no `brd` or `claude`. No changes to their bodies.
- The existing pure tests in `test_fake_claude.py`: tier **unit** (no marker), because they are pure parsing and schema checks with no subprocess. No changes to their bodies.

Verification: `uv run pytest` (full suite). There is no lint or typecheck command. Also run the card's collect-only checks above with the default selection, with `-m e2e_fake` and with `-m git`, and put the three per-tier counts for this module in the PR.
