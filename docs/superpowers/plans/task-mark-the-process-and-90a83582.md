<!-- task-pipeline: validated -->
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

---

# Mark the process- and git-touching tests explicitly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every test in `tests/e2e/test_fake_claude.py` the tier its helpers actually touch (`e2e_fake` for `_run_fake`, `git` for `_implement_repo`, none for pure tests) and exempt that one module from the `tests/e2e/` directory auto-mark so the pure tests join the default run.

**Architecture:** Three tasks, each leaving `uv run pytest` green. Task 1 adds an AST-based guard test inside `test_fake_claude.py` that derives each test's required tier from the module's own call graph, then adds the 37 explicit markers to make it pass (while the auto-mark still covers the module, so the pure tests stay opt-in for now). Task 2 adds a single-path exemption `_AUTO_MARK_EXEMPT` in `tests/conftest.py`, applied in `default_tier_marker`, which moves the pure tests into the default `unit` tier. Task 3 rewrites the module docstring and runs the per-tier collect-only checks.

**Tech Stack:** Python 3.12, pytest 9 (`--import-mode=importlib`, `pythonpath = ["tests"]`), `ast` from the standard library, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-mark-the-process-and-90a83582/docs/superpowers/specs/task-mark-the-process-and-90a83582-design.md` (reproduced verbatim above). Tier rules: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V1, V2, V7.

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-mark-the-process-and-90a83582` (branch `m15/task-mark-the-process-and-90a83582`). Run every command from that root.

## Global Constraints

- Choice for V7's fork: **stay in place**. The pure tests stay in `tests/e2e/test_fake_claude.py`, unmarked. The PR description (here: the final commit message body) must say this was the chosen option.
- One test gets one tier marker. A test that calls both `_run_fake` and `_implement_repo` gets `e2e_fake` only.
- `_implement_repo` is reached directly or through `_review_worktree` (`:555`) or `_conflicted_repo` (`:1200`); all such tests are `git`.
- Do not add a module-level `pytestmark` to `tests/e2e/test_fake_claude.py`.
- Do not change the bodies of existing tests in `tests/e2e/test_fake_claude.py`.
- `tests/conftest.py`: a named, single-entry exemption next to `_DIRECTORY_TIERS`, keyed by the path relative to `tests/` (`e2e/test_fake_claude.py`), applied in `default_tier_marker`, with a comment pointing to the module docstring of `tests/e2e/test_fake_claude.py`. Do not change any other module's auto-mark behaviour, the budget check, the PATH shim, the e2e cap, or the skip hook.
- Budgets: unit 0.5s per test, `git` 2s per test. A misclassified test is retiered, not exempted; a `git` test over 2s is reported in the PR, not silenced. Do not weaken the shim or the budget.
- Out of scope: pytest-xdist or any parallelism; pygents engine, checkpoint format, harness adapter contract, `dispatch.py`'s `LauncherFn`; the 5 `e2e` tests; milestone 14 `am run --board`; V3 FakeBoard, V4/V5 conversions, V6 deletions, V8 rollup/board split, V9 justification lines.
- Verification: `uv run pytest`. No lint or typecheck command exists.

## Review Focus

1. A test added to `test_fake_claude.py` later that calls `_run_fake` but forgets its marker: `_run_fake` spawns `sys.executable` by absolute path, so the unit-tier PATH shim does not catch it for critic phases and it would silently run in the unit tier. Expected: a test fails naming it. Pinned by `test_each_test_here_carries_exactly_the_tier_its_helpers_touch` (Task 1).
2. A test that reaches `_implement_repo` only through `_review_worktree`/`_conflicted_repo` (or a future helper wrapping them): expected to need `git`. Pinned by the same guard test, which walks the call graph transitively (Task 1).
3. A test that ends up with two tier markers (for example `git` and `e2e_fake` on `test_the_fake_process_holds_before_it_implements`): expected to be rejected. Pinned by the guard test comparing the exact tier set (Task 1).
4. The real pytest hook, not only the pure helper, leaves an unmarked item in `e2e/test_fake_claude.py` unmarked. Pinned by `test_hook_leaves_unmarked_fake_claude_items_unmarked` (Task 2).
5. A look-alike path (`e2e/test_fake_claude_other.py`, `e2e/sub/test_fake_claude.py`, `steps/test_fake_claude.py`) must keep its directory default. Pinned by `test_default_tier_marker_exemption_is_file_scoped` (Task 2).

Note on the spec's "Observable behaviour" first bullet: the default `addopts` selection is `not brd and not e2e_fake and not soak and not e2e`, which includes the `git` tier. So `uv run pytest --collect-only` collects the unit tests **and** the `git` tests from this module. Task 3 checks the unit-only set with an explicit `-m` that also excludes `git`, and reports all counts.

Expected item counts for `tests/e2e/test_fake_claude.py` after Task 3 (items, i.e. parametrizations counted separately): `e2e_fake` 8 items (8 functions); `git` 43 items (29 functions); unit 60 items (43 functions: the 42 existing pure functions plus the new guard test); total 111 items. Default selection from this module: 103 items (60 unit + 43 git).

---

### Task 1: Explicit tier markers on the process- and git-touching tests, with a guard test

**Files:**
- Modify: `tests/e2e/test_fake_claude.py:36-52` (add guard helpers and guard test after `_conftest_constant`)
- Modify: `tests/e2e/test_fake_claude.py` (decorators above 37 test functions, listed in Step 3)
- Test: `tests/e2e/test_fake_claude.py` (the guard test is unit tier: it parses a file, no subprocess; until Task 2 it is auto-marked `e2e_fake` by the directory hook, which is why Steps 2 and 4 pass `-m e2e_fake`)

**Interfaces:**
- Consumes: the module's own helpers `_run_fake` (`:253`), `_implement_repo` (`:372`), `_review_worktree` (`:555`), `_conflicted_repo` (`:1200`) — by name only, via `ast`.
- Produces: `_TIERS: frozenset[str]`, `_tier_marks(node: ast.FunctionDef) -> set[str]`, `_expected_tiers() -> dict[str, tuple[set[str], set[str]]]` (test name to `(required tiers, tiers on its decorators)`), and the test `test_each_test_here_carries_exactly_the_tier_its_helpers_touch`. Task 3's docstring names that test.

- [ ] **Step 1: Write the failing guard test**

Insert this block in `tests/e2e/test_fake_claude.py` directly after the end of `_conftest_constant` (after the line `    raise AssertionError(f"tests/e2e/conftest.py defines no {name}")`, before `def test_the_prompt_path_is_parsed_out_of_the_adapters_p_sentence`). `ast` and `Path` are already imported at the top of the file.

```python
_TIERS = frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})
"""`tests/conftest.py`'s `TIER_MARKERS`, written out: that conftest is not
imported here, for the same reason `_conftest_constant` parses its sibling."""


def _tier_marks(node):
    """The tier names among `node`'s `@pytest.mark.<name>` decorators, called or bare."""
    names = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "mark"
        ):
            names.add(target.attr)
    return names & _TIERS


def _expected_tiers():
    """Each test function in this module mapped to (the tiers it needs, the tiers it has).

    Needs `e2e_fake` when it reaches `_run_fake`, else `git` when it reaches
    `_implement_repo` (directly or through `_review_worktree`/`_conflicted_repo`),
    else no tier. Reaching is transitive over this module's top-level functions.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    calls = {
        name: {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in functions
        }
        for name, node in functions.items()
    }

    def reaches(start, target):
        seen, stack = {start}, [start]
        while stack:
            for callee in calls[stack.pop()]:
                if callee == target:
                    return True
                if callee not in seen:
                    seen.add(callee)
                    stack.append(callee)
        return False

    expected = {}
    for name, node in functions.items():
        if not name.startswith("test_"):
            continue
        if reaches(name, "_run_fake"):
            want = {"e2e_fake"}
        elif reaches(name, "_implement_repo"):
            want = {"git"}
        else:
            want = set()
        expected[name] = (want, _tier_marks(node))
    return expected


def test_each_test_here_carries_exactly_the_tier_its_helpers_touch():
    """This module is exempt from the `tests/e2e/` auto-mark, so its markers are
    the only thing keeping a process- or git-touching test out of the unit tier.
    `_run_fake` spawns by absolute path, which the unit PATH shim cannot catch."""
    expected = _expected_tiers()

    wrong = {
        name: f"needs {sorted(want)}, has {sorted(has)}"
        for name, (want, has) in expected.items()
        if want != has
    }

    assert wrong == {}
    wants = [want for want, _ in expected.values()]
    # Non-vacuity: the walk really finds all three kinds.
    assert {"e2e_fake"} in wants
    assert {"git"} in wants
    assert set() in wants
```

- [ ] **Step 2: Run the guard test to verify it fails**

Run: `uv run pytest "tests/e2e/test_fake_claude.py::test_each_test_here_carries_exactly_the_tier_its_helpers_touch" -m e2e_fake -v`
Expected: FAIL on `assert wrong == {}`, with 37 entries in `wrong`: the 8 `_run_fake` tests as `needs ['e2e_fake'], has []` and the 29 `_implement_repo` tests as `needs ['git'], has []`.

- [ ] **Step 3: Add the 37 explicit markers**

Add exactly one decorator line directly above each `def` named below. For a function that already has `@pytest.mark.parametrize(...)`, put the tier marker on its own line above the parametrize decorator. Change nothing else in these functions. Line numbers are from the file before Step 1's insertion (they shift down by the inserted block); find each by name.

`@pytest.mark.e2e_fake` (8, they reach `_run_fake`):

| was line | function |
|---|---|
| 271 | `test_the_fake_writes_a_gate_passing_critic_result_where_the_brief_says` |
| 295 | `test_the_fake_logs_its_phase_and_cwd_beside_the_run_directory` |
| 316 | `test_a_brief_without_a_result_contract_makes_the_fake_exit_non_zero` |
| 331 | `test_a_feedback_block_after_the_contract_does_not_hide_the_contract` |
| 766 | `test_a_rendezvous_failure_makes_the_fake_process_exit_1` |
| 946 | `test_the_fake_process_holds_before_it_implements` (also calls `_implement_repo`; `e2e_fake` only) |
| 1532 | `test_a_bad_critic_blocks_budget_makes_the_fake_process_exit_1` |
| 1553 | `test_a_blocking_critic_process_writes_the_blocked_result` |

`@pytest.mark.git` (29, they reach `_implement_repo` and not `_run_fake`):

| was line | function | reaches via |
|---|---|---|
| 421 | `test_the_fake_coder_takes_its_trailer_hash_from_the_brief_not_the_plan_file` | direct |
| 442 | `test_an_implement_brief_with_no_plan_hash_section_stops_the_fake` | direct |
| 459 | `test_a_padded_plan_hash_section_still_produces_a_single_line_trailer` | direct |
| 504 | `test_a_stacked_subtask_with_its_own_plan_commits_its_own_implementation` | direct |
| 521 | `test_a_second_implement_on_the_same_plan_resumes_instead_of_failing` | direct |
| 592 | `test_without_a_marker_the_review_passes_the_real_review_gate` | `_review_worktree` |
| 604 | `test_a_marker_naming_the_briefs_branch_makes_a_review_the_real_gate_blocks` | `_review_worktree` |
| 625 | `test_a_marker_naming_only_other_branches_does_not_fail_this_review` | `_review_worktree` |
| 661 | `test_without_a_rendezvous_dir_implement_neither_waits_nor_writes_a_marker` | direct |
| 678 | `test_a_met_rendezvous_writes_a_marker_named_for_the_cwd_and_implements` | direct |
| 698 | `test_a_rendezvous_counts_markers_other_lanes_left` | direct |
| 717 | `test_an_unmet_rendezvous_fails_the_fake_before_it_commits` | direct |
| 734 | `test_the_same_cwd_arriving_twice_counts_once` | direct |
| 1009 | `test_the_marker_entry_for_the_briefs_branch_is_written_and_committed` | direct |
| 1028 | `test_a_marker_entry_for_another_branch_writes_nothing_extra` | direct |
| 1039 | `test_a_marker_with_no_branch_section_in_the_brief_stops_the_fake` | direct |
| 1054 | `test_a_marker_path_outside_the_worktree_is_refused_before_any_write` (parametrized) | direct |
| 1072 | `test_a_marker_that_is_not_json_is_refused` | direct |
| 1084 | `test_a_marker_that_is_not_an_object_of_branches_is_refused` (parametrized) | direct |
| 1098 | `test_a_marker_entry_that_is_not_path_to_content_strings_is_refused` (parametrized) | direct |
| 1256 | `test_the_resolver_keeps_both_sides_commits_and_the_real_gate_passes` (parametrized) | `_conflicted_repo` |
| 1274 | `test_an_empty_resolver_env_value_means_resolve` | `_conflicted_repo` |
| 1283 | `test_a_refusing_resolver_claims_resolved_but_git_still_says_no` | `_conflicted_repo` |
| 1304 | `test_an_unknown_resolver_env_value_fails_the_fake_and_touches_nothing` | `_conflicted_repo` |
| 1320 | `test_a_resolve_brief_missing_a_section_fails_the_fake` (parametrized) | `_conflicted_repo` |
| 1337 | `test_a_malformed_conflict_files_section_fails_the_fake` (parametrized) | `_conflicted_repo` |
| 1353 | `test_a_merge_tip_that_is_not_merge_head_fails_the_fake` | `_conflicted_repo` |
| 1364 | `test_a_resolve_with_no_merge_in_progress_fails_the_fake` | direct |
| 1375 | `test_a_listed_conflict_file_that_is_not_there_fails_before_any_write` | `_conflicted_repo` |

The three shapes, exactly:

Plain function (`e2e_fake`):

```python
@pytest.mark.e2e_fake
def test_the_fake_writes_a_gate_passing_critic_result_where_the_brief_says(tmp_path):
```

The function that calls both helpers (`e2e_fake` only, no `git`):

```python
@pytest.mark.e2e_fake
def test_the_fake_process_holds_before_it_implements(tmp_path, monkeypatch):
```

Parametrized function (`git`, tier marker above parametrize):

```python
@pytest.mark.git
@pytest.mark.parametrize("relative", ["../outside.txt", "/tmp/absolute.txt", ""])
def test_a_marker_path_outside_the_worktree_is_refused_before_any_write(
    tmp_path, relative
):
```

The other five parametrized `git` functions follow the third shape: `@pytest.mark.git` on the line immediately above their existing `@pytest.mark.parametrize(`. Leave every one of the 42 pure test functions without a tier decorator (their existing `@pytest.mark.parametrize` decorators at was-lines 750, 1430-1431 and 1493 stay as they are), and do not add a module-level `pytestmark`.

- [ ] **Step 4: Run the guard test to verify it passes**

Run: `uv run pytest "tests/e2e/test_fake_claude.py::test_each_test_here_carries_exactly_the_tier_its_helpers_touch" -m e2e_fake -v`
Expected: PASS.

- [ ] **Step 5: Run the module under each tier it now has**

Run: `uv run pytest tests/e2e/test_fake_claude.py -m git -v`
Expected: 43 passed (29 functions), none failed by `git-tier budget exceeded`. If any `git` test fails with `git-tier budget exceeded: ...s > 2s`, do not silence it: write down its node id and measured time for the Task 3 commit message, and only retier it if it really drives the fake-claude process (none of these do).

Run: `uv run pytest tests/e2e/test_fake_claude.py -m e2e_fake -v`
Expected: 68 passed: the 8 explicitly marked `e2e_fake` items plus the 60 unmarked items (59 pure items and the guard test), which the directory hook still auto-marks `e2e_fake` until Task 2. The 43 `git` items are deselected.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all pass. The 43 `git` items from this module now run in the default run.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_fake_claude.py
git commit -m "test: mark test_fake_claude's _run_fake tests e2e_fake and _implement_repo tests git

A guard test derives each test's tier from the module's own call graph
(_run_fake -> e2e_fake, else _implement_repo, directly or via
_review_worktree/_conflicted_repo -> git, else none) and fails on any
mismatch. Card 90a83582."
```

---

### Task 2: Exempt `e2e/test_fake_claude.py` from the directory auto-mark

**Files:**
- Modify: `tests/conftest.py:8-11` (module docstring sentence about the auto-mark)
- Modify: `tests/conftest.py:101` (add `_AUTO_MARK_EXEMPT` after `_DIRECTORY_TIERS`)
- Modify: `tests/conftest.py:115-128` (`default_tier_marker`)
- Test: `tests/test_conftest_tiers.py` (unit tier: top level of `tests/`, pure function calls on `PurePosixPath` and a fake item; this file's own docstring already states it carries no tier marker)

**Interfaces:**
- Consumes: `default_tier_marker(rel_path: PurePath, existing: Iterable[str]) -> str | None` and `pytest_collection_modifyitems(config, items)` from `tests/conftest.py`; `_FakeItem` and `TESTS_DIR` already in `tests/test_conftest_tiers.py`.
- Produces: `_AUTO_MARK_EXEMPT: frozenset[str]` in `tests/conftest.py`, containing exactly `"e2e/test_fake_claude.py"`; `default_tier_marker` returns `None` when `rel_path.as_posix()` is in it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_conftest_tiers.py`, insert after `test_nested_dir_under_e2e_still_gets_e2e_fake` (ends at line 59):

```python
def test_default_tier_marker_exempts_fake_claude_module():
    assert default_tier_marker(PurePosixPath("e2e/test_fake_claude.py"), set()) is None


@pytest.mark.parametrize(
    ("rel_path", "tier"),
    [
        ("e2e/test_fake_claude_other.py", "e2e_fake"),
        ("e2e/sub/test_fake_claude.py", "e2e_fake"),
        ("steps/test_fake_claude.py", "git"),
    ],
)
def test_default_tier_marker_exemption_is_file_scoped(rel_path, tier):
    assert default_tier_marker(PurePosixPath(rel_path), set()) == tier
```

And append at the end of the file, after `test_hook_decides_each_item_in_a_shared_module_independently`:

```python
def test_hook_leaves_unmarked_fake_claude_items_unmarked():
    """The real hook, not only the helper: the module's pure tests stay unit tier."""
    item = _FakeItem(TESTS_DIR / "e2e" / "test_fake_claude.py")
    pytest_collection_modifyitems(None, [item])
    assert item.added == []
```

- [ ] **Step 2: Run them to verify the exemption tests fail**

Run: `uv run pytest tests/test_conftest_tiers.py -v -k "fake_claude"`
Expected: `test_default_tier_marker_exempts_fake_claude_module` FAILS (`assert 'e2e_fake' is None`) and `test_hook_leaves_unmarked_fake_claude_items_unmarked` FAILS (`assert ['e2e_fake'] == []`). The three `test_default_tier_marker_exemption_is_file_scoped` cases PASS already; they guard against a too-wide exemption in Step 3.

- [ ] **Step 3: Add the exemption to `tests/conftest.py`**

Replace line 101:

```python
_DIRECTORY_TIERS = {"e2e": "e2e_fake", "steps": "git"}
```

with:

```python
_DIRECTORY_TIERS = {"e2e": "e2e_fake", "steps": "git"}

# Modules the directory auto-mark skips, as posix paths relative to tests/. One
# exact file each, never a prefix. Their tests carry their tier markers one by
# one, and unmarked ones stay in the unit tier: see the module docstring of
# tests/e2e/test_fake_claude.py for why that module is the exception.
_AUTO_MARK_EXEMPT = frozenset({"e2e/test_fake_claude.py"})
```

Replace `default_tier_marker` (lines 115-128) with:

```python
def default_tier_marker(rel_path: PurePath, existing: Iterable[str]) -> str | None:
    """The tier marker an item at `rel_path` (relative to tests/) should get.

    None when its first directory is neither `e2e` nor `steps`, when it is one of
    the `_AUTO_MARK_EXEMPT` modules, or when `existing` (every marker name on the
    item's chain) already holds a tier.
    """
    if len(rel_path.parts) < 2:
        return None
    if rel_path.as_posix() in _AUTO_MARK_EXEMPT:
        return None
    tier = _DIRECTORY_TIERS.get(rel_path.parts[0])
    if tier is None:
        return None
    if TIER_MARKERS.intersection(existing):
        return None
    return tier
```

In the module docstring, replace lines 8-11:

```
It also gives directory-conventional tests a default tier marker: items under
`tests/e2e/` get `e2e_fake` and items under `tests/steps/` get `git`, unless the
item already carries a tier marker anywhere on its marker chain. The hook only
adds markers; the addopts `-m` expression in pyproject.toml does the deselecting.
```

with:

```
It also gives directory-conventional tests a default tier marker: items under
`tests/e2e/` get `e2e_fake` and items under `tests/steps/` get `git`, unless the
item already carries a tier marker anywhere on its marker chain or its module is
in `_AUTO_MARK_EXEMPT` (tests/e2e/test_fake_claude.py, which marks its tests one
by one). The hook only adds markers; the addopts `-m` expression in
pyproject.toml does the deselecting.
```

- [ ] **Step 4: Run the conftest tier tests to verify they pass**

Run: `uv run pytest tests/test_conftest_tiers.py -v`
Expected: all pass, including the five new items.

- [ ] **Step 5: Run the newly-unit tests of the fake module under the shim and budget**

Run: `uv run pytest tests/e2e/test_fake_claude.py -m "not git and not brd and not e2e_fake and not soak and not e2e" -v`
Expected: 60 passed (the 42 pure functions' 59 items plus the guard test), with no `unit-tier budget exceeded` and no `forbidden in the unit tier` failure. If one fails either way, it is misclassified: give it the tier matching what it touches (and confirm the guard test agrees), do not loosen the shim or the 0.5s budget.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all pass. The default run now includes the module's 60 unit items and 43 git items.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_conftest_tiers.py
git commit -m "test: exempt e2e/test_fake_claude.py from the tests/e2e auto-mark

A single exact-path _AUTO_MARK_EXEMPT entry, applied in
default_tier_marker, so that module's unmarked pure tests stay in the
default unit tier. Card 90a83582."
```

---

### Task 3: Rewrite the module docstring and verify the per-tier counts

**Files:**
- Modify: `tests/e2e/test_fake_claude.py:1-12` (module docstring)

**Interfaces:**
- Consumes: `_AUTO_MARK_EXEMPT` (Task 2) and `test_each_test_here_carries_exactly_the_tier_its_helpers_touch` (Task 1), named in the docstring.
- Produces: nothing code-facing.

- [ ] **Step 1: Replace the module docstring**

Replace lines 1-12 of `tests/e2e/test_fake_claude.py` (the whole current docstring, from `"""Tests for the fake` to the closing `"""`) with:

```python
"""Tests for the fake `claude` of the production-wiring tier.

Tier follows what a test touches, not the directory it sits in (V1 of
`docs/superpowers/specs/2026-10-02-test-tier-design.md`, which supersedes the
design doc's old testing section). Each test here is marked on its own:

- `@pytest.mark.e2e_fake`: the tests that call `_run_fake`, which runs
  `fake_claude.py` as a child process. A test that also builds a git repo is
  still `e2e_fake` only: one test, one tier.
- `@pytest.mark.git`: the tests that build a real git repo in `tmp_path`
  through `_implement_repo`, directly or via `_review_worktree` or
  `_conflicted_repo`, and call the script's functions in-process.
- no marker: the pure parsing, schema and payload tests, in the default `unit`
  tier with its PATH shim and 0.5s budget.

This module is the one exception to the `tests/e2e/` directory auto-mark
(`_AUTO_MARK_EXEMPT` in `tests/conftest.py`). Every other module under
`tests/e2e/` drives production wiring, so `e2e_fake` is the right default
there. Most tests here only exercise the fake's helpers as plain functions;
auto-marking them would keep them out of the default run for no reason. They
stay in this file, rather than moving to a separate parsing module, because
they share its by-path load of the script and its brief and schema fixtures.
There is no module-level `pytestmark`, which would mark the pure tests too.
`test_each_test_here_carries_exactly_the_tier_its_helpers_touch` fails when a
test's marker disagrees with the helpers it reaches.

`tests/e2e/fake_claude.py` is a script, not a package module: it is copied to a
tmp directory and executed as `claude` by the production-wiring tier. It is
loaded here by path rather than imported by name, because `tests/e2e` is not on
`sys.path` under `--import-mode=importlib`.
"""
```

- [ ] **Step 2: Run the module's default selection**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: 103 passed (60 unit + 43 git), 8 deselected.

- [ ] **Step 3: Collect-only, default selection**

Run: `uv run pytest tests/e2e/test_fake_claude.py --collect-only -q`
Expected: last line `103/111 tests collected (8 deselected)`. None of the 8 `_run_fake` test names appears in the list.

Run: `uv run pytest tests/e2e/test_fake_claude.py --collect-only -q -m "not git and not brd and not e2e_fake and not soak and not e2e"`
Expected: last line `60/111 tests collected (51 deselected)` — the unit tier from this module; no `_run_fake` or `_implement_repo` test among them.

- [ ] **Step 4: Collect-only, `-m e2e_fake`**

Run: `uv run pytest tests/e2e/test_fake_claude.py --collect-only -q -m e2e_fake`
Expected: last line `8/111 tests collected (103 deselected)`, listing exactly the 8 functions in Task 1 Step 3's `e2e_fake` table.

Run: `uv run pytest -m e2e_fake --collect-only -q`
Expected: the 8 from this module plus every other `tests/e2e/` module's tests, as before this change.

- [ ] **Step 5: Collect-only, `-m git`**

Run: `uv run pytest tests/e2e/test_fake_claude.py --collect-only -q -m git`
Expected: last line `43/111 tests collected (68 deselected)`, the 29 functions in Task 1 Step 3's `git` table.

- [ ] **Step 6: Run the opt-in tier on its own, then the full suite**

Run: `uv run pytest -m e2e_fake`
Expected: all pass (V7's acceptance line in §7 of the tier doc).

Run: `uv run pytest`
Expected: all pass. Note any test this module contributes to the `--durations` list above 0.5s (unit) or near 2s (git) for the commit message.

- [ ] **Step 7: Commit, with the PR notes in the body**

Fill the three counts with the numbers Steps 3-5 printed (expected 60, 8, 43) and the budget line with what Step 6 and Task 1 Step 5 showed (expected: none over budget).

```bash
git add tests/e2e/test_fake_claude.py
git commit -m "docs(tests): explain test_fake_claude's per-test tiers and its auto-mark exemption

V7 option chosen: stay in place. The pure tests stay in
tests/e2e/test_fake_claude.py, unmarked, and the module is exempted from
the tests/e2e directory auto-mark (tests/conftest.py _AUTO_MARK_EXEMPT)
rather than moved to tests/test_fake_claude_parsing.py.

Per-tier counts for tests/e2e/test_fake_claude.py (collected items):
- unit (no marker): 60
- e2e_fake: 8
- git: 43
git tests over the 2s budget: none

Card 90a83582."
```
