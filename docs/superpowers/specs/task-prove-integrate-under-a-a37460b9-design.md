# Prove Integrate under a fake claude (card a37460b9)

Subtask of story 006d0a0e "Integrate in the runner" (milestone db5b5a3b). Narrows Integrate design I7 and acceptance 1-5 (`docs/superpowers/specs/2026-09-25-integrate-design.md`) to test code. Acceptance 6 (dry run) belongs to a74f2cd6 and 7 (real agent) is a human step; neither is in scope.

Note on inputs: the exploration summary this spec was written from was truncated at 8000 characters, inside the test-placement paragraph. The tier assignments below come from the placement rule in the main design spec section 14 and the existing layout of `tests/e2e/`, not from the missing text.

## Base

This worktree already carries the Integrate code from the sibling branches (`integration.py`, `steps/integrate.py`, `workflow/builtin/integrate.yaml`, the resolver bundle, `results.ResolveResult`, the `merge_tip` / `conflict_files` prompt rows, `merge_completed_gate`). The card must not change anything under `src/`. It touches only `tests/e2e/`: `fake_claude.py`, `conftest.py`, `test_fake_claude.py`, and one new module `tests/e2e/test_integrate.py`. If a scenario shows that product code is wrong, stop and report it. Do not patch around it in tests.

## Scope

### 1. Fake `claude`: a `resolve` branch (extend `tests/e2e/fake_claude.py`)

- `build_result` gains `phase == "resolve"`, placed before the final `raise`. It reads `## merge_tip` (a verbatim ref) and `## conflict_files` (an inline JSON list of repo-relative paths) through `sections()` / `_section()` and nothing else. If either section is missing, `_section` raises `FakeClaudeError`. If `conflict_files` is not a JSON list of strings, it raises `FakeClaudeError`. As a sanity check, `MERGE_HEAD` in the cwd must resolve to the same commit as `merge_tip`. If it does not, it raises `FakeClaudeError`.
- Normal mode: for each listed file in the cwd (the integration worktree), rewrite each conflict hunk to keep both sides, ours then theirs. It drops the `<<<<<<<` / `=======` / `>>>>>>>` lines, and also any `|||||||` base section so diff3/zdiff3 user configs work. Then it `git add`s each file, runs `git commit --no-edit`, and returns `override(payload, resolved=True, summary=SUMMARY)`.
- New module constant `RESOLVER_ENV = "FAKE_CLAUDE_RESOLVER"`. If it is unset or empty, the fake uses normal mode. If it is `refuse`, the fake returns `resolved=True` and leaves the tree untouched: no edit, no add, no commit. This is the advisory flag lying, so git has to catch it. Any other value raises `FakeClaudeError`, so a typo in a test fails loudly.
- New test-controlled input for story-specific edits, keyed by the brief the same way `REVIEW_FAIL_MARKER` is. The constant is `IMPLEMENT_EDITS_MARKER = "fake-claude-implement-edits"`. It names a JSON file in the repo's git common dir (found via `git rev-parse --git-common-dir`, like `review_fail_branches`) that maps branch to `{relative path: full file content}`. In `implement`, after the rendezvous and before `git add -A`, the fake writes the entry for the brief's `## branch` section (implement's inputs already include `branch`), if there is one, next to `IMPLEMENTATION.md`. If there is no file or no entry, behaviour is unchanged. This is test scaffolding: the brief still picks the subtask, and the fake computes nothing.
- The module docstring's list of test-controlled inputs becomes four: review-fail marker, rendezvous, implement-edits marker, `RESOLVER_ENV`. Standard library only, no `agent_manager` import, no plan-hash computing.
- `conftest.py` gains the twins `FAKE_RESOLVER_ENV` and `FAKE_IMPLEMENT_EDITS_MARKER`, beside the existing `FAKE_*` block.
- `run_milestone_cli` gains an optional `verify` argument, a sequence of commands. `None` means `VERIFY_COMMANDS`, so existing callers' argv stays byte-identical.

### 2. How the scenarios make stories edit specific files

Each scenario seeds files on `main` (committed by the test on `fresh_project` before the run), builds its own board (milestone, two independent stories A and B, one subtask each), and writes the implement-edits marker keyed by `dag.task_branch(MILESTONE_PREFIX, ...)`. Every scenario also writes `UNION_ATTRIBUTE` to `.git/info/attributes`, because the fake writes `IMPLEMENTATION.md` in every story and those per-card contents would otherwise always conflict. The union attribute covers only that file. The conflicts under test are created by the marker's same-line edits to a different file, so the union attribute cannot hide them.

The final verification in scenario 4 needs a real suite. Base gets `calc.py` (a toy function, as in `test_real_harness_milestone.py:61-67`) and a stdlib-only `check.py` that imports every `test_*.py` in the repo root and calls each `test_*` function, exiting non-zero on failure. The verify command is `f"{sys.executable} check.py"`, passed through the new `verify` argument. It does not depend on `pytest` being on the child's `PATH`.

## Observable behaviour (`tests/e2e/test_integrate.py`)

All scenarios run through production wiring only: `run_milestone_cli` (`am run --milestone` through `CliRunner`, `--base-branch main`, `--branch-prefix m3`, no `runner_factory`), the real `ClaudeAdapter`, `run_direct`, and the fake on `PATH`, all on `fresh_project`. The integration branch is `m3-integrate` and its worktree is `cli.worktree_for(root, "m3-integrate")`. In every scenario the test records `main`'s tip after seeding and asserts it is unchanged at the end. It also asserts the repo has no remote and no `refs/remotes` (I5).

1. **Same-line conflict, resolved.** Base has `shared.txt` with one line. A and B each replace that line differently. The envelope is `ok`, status `done`, and `data.integrated.branch == "m3-integrate"`. `merged` holds both stories and `resolved` names exactly the story whose merge conflicted (the second in integrate order). In the integration worktree, `shared.txt` has both edits and no conflict markers, `MERGE_HEAD` is absent, and `git status --porcelain` is empty. The run finished `done` only after the `verify` phase and the final verification passed there. The fake log has a `resolve` entry whose cwd is the integration worktree.
2. **Resolver refuses; a human finishes.** Same board with `FAKE_CLAUDE_RESOLVER=refuse` set via `monkeypatch`. The run escalates: `escalated: true`, `phase == "integrate"`, `story` is the conflicting story, `files` includes `shared.txt`, and `run_id` is present. `MERGE_HEAD` exists in the integration worktree. The test then plays human: it runs `git add -A` and `git commit --no-edit` there and calls `monkeypatch.delenv`. On relaunch the status is `done`, `integrated` is set, and no `MERGE_HEAD` remains. `main` is unchanged throughout.
3. **Different files, no agent.** A edits `a.txt` and B edits `b.txt`. The status is `done`, `integrated.resolved == []`, and the integration branch contains both files. No `resolve` entry appears in the fake log for the run. The store has no synthetic "Integrate" story and no subtask row or attempt with phase `resolve`.
4. **Clean merge, broken suite.** A renames the function in `calc.py` (and updates any base test that used it). B adds `test_calc_extra.py`, which imports the old name. Each story is green alone on base: both run the same `check.py` verify, and the run gets as far as Integrate. The merge is textually clean. The run escalates with `phase == "integrate"`, and `detail` carries the verification failure: it names the failing command or its output. There is no `MERGE_HEAD`, the integration branch holds both merges, and `main` is unchanged.
5. **Relaunch of an integrated milestone changes nothing.** After scenario 1's successful run (or a clean equivalent), the test records `rev-parse m3-integrate` and relaunches. The status is `done`, and `m3-integrate`, `main` and the story branch tips are all unchanged. No new `resolve` entry appears in the fake log.

No test leaves an env var or marker armed. Env vars go through the function-scoped `monkeypatch`, and markers live in each test's own `fresh_project`.

## Error paths covered

A brief without `merge_tip` or `conflict_files`, or with a malformed `conflict_files`, fails the fake. `MERGE_HEAD` disagreeing with `merge_tip` fails the fake. An unknown `FAKE_CLAUDE_RESOLVER` value fails the fake. Scenario 2 covers the resolver lying (the gate is git-measured). Scenario 4 covers a clean-but-broken merge.

## Tests and their tiers

Placement rule: design spec section 14 (pure functions go to unit tests; steps run against temporary git repos and a temporary brd board with no network; end to end means the real harness, opt-in and marked). This repo's production-wiring tier is `tests/e2e/` with the fake on `PATH`, and it runs unmarked in the default suite. The `e2e` marker is reserved for paid real-harness runs.

| Test | Tier |
|---|---|
| `test_fake_claude.py`: pins `RESOLVER_ENV == conftest.FAKE_RESOLVER_ENV` and `IMPLEMENT_EDITS_MARKER == conftest.FAKE_IMPLEMENT_EDITS_MARKER` | unit (pure constants), in `tests/e2e/test_fake_claude.py` beside the existing pins |
| `test_fake_claude.py`: the resolve branch strips markers (both styles), adds and commits in a temp repo with a real in-progress merge; refuse mode leaves `MERGE_HEAD` and the markers; missing/malformed sections, a mismatched `merge_tip` and an unknown env value raise `FakeClaudeError` | steps tier (temporary git repo, no network), in `tests/e2e/test_fake_claude.py` |
| `test_fake_claude.py`: implement writes the marker's files only for the brief's branch; no marker means unchanged behaviour | steps tier (temporary git repo) |
| `test_integrate.py` scenarios 1-5 | production-wiring tier, `tests/e2e/`, **unmarked**, default suite |
| `test_integrate.py::test_this_module_runs_in_the_default_suite_unmarked` (pattern of `test_parallel_milestone.py:87`) | production-wiring tier, unmarked |
| Existing callers of `run_milestone_cli` keep their argv (covered by the existing e2e modules still passing) | production-wiring tier, unmarked |

Done means `uv run pytest` is fully green, including `tests/e2e/`.
