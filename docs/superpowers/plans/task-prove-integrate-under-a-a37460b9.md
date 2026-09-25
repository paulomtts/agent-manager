<!-- task-pipeline: validated -->
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

---

# Prove Integrate under a fake claude Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the Integrate phase end to end under the fake `claude`: teach the fake a brief-driven `resolve` phase and story-specific implement edits, then add an unmarked production-wiring module that covers the five Integrate scenarios.

**Architecture:** Test code only, all under `tests/e2e/`. `fake_claude.py` gains a pure `keep_both_sides` hunk rewriter, a `resolve` branch in `build_result` driven only by the brief's `## merge_tip` / `## conflict_files`, a `FAKE_CLAUDE_RESOLVER=refuse` switch, and an implement-edits marker in the git common dir keyed by the implement brief's `## branch`. `conftest.py` gains the twin constants, a `two_story_board` fixture, a `fake_resolver` fixture, and an optional `verify` argument on `run_milestone_cli`. The new `tests/e2e/test_integrate.py` drives `am run --milestone` through `CliRunner` with no `runner_factory`.

**Tech Stack:** Python 3, pytest, Typer `CliRunner`, real `git` and `brd` CLIs, the standard library only inside `fake_claude.py`.

**Spec:** `docs/superpowers/specs/task-prove-integrate-under-a-a37460b9-design.md` (reproduced verbatim above).

**Inputs note:** The orchestrating prompt's spec summary was cut off at 2000 characters and its exploration summary at 8000 characters. This plan was written from the spec file on disk and from reading the code on this branch, not from those summaries. The spec itself records that the exploration truncation fell inside the test-placement paragraph. The tiers below follow the spec's own table, which uses design section 14 and the existing `tests/e2e/` layout.

**Base branch facts the implementer can rely on (verified on this worktree):** `src/agent_manager/integration.py`, `src/agent_manager/steps/integrate.py` (`merge_tip`, `merge_completed_gate`), `src/agent_manager/workflow/builtin/integrate.yaml` (phases `resolve` then `verify`; `resolve` inputs `[branch, base_branch, merge_tip, conflict_files, verification]`, `retry: max_attempts 2`), `results.ResolveResult` (`resolved: bool`, `summary: str`), `prompt.py` rows `merge_tip` (verbatim) and `conflict_files` (inline JSON), and `orchestrate.run_milestone` calling `integration.integrate_milestone` all exist. A clean milestone payload carries `done: True` and `integrated: {branch, worktree, merged, resolved}`. An Integrate escalation payload is `{escalated: True, phase: "integrate", story, files, detail, run_id, warnings}`, and `am run` exits `cli.EXIT_ESCALATED` (1). The final-verification escalation has `story: None`, `files: []`, and a `detail` that starts `the integrated branch failed its final verification in <worktree>: verification failed: <command> — <last stderr line>`. The synthetic Integrate story has `card_id == "integrate"` and title `"Integrate"`. Its subtask's `card_id` is the conflicting story's id. The resolver's attempt dir is `<run dir>/<story id>/resolve.<n>/`, so the fake's `log_path` still lands in the run dir.

## Global Constraints

- Nothing under `src/` changes. Only `tests/e2e/fake_claude.py`, `tests/e2e/conftest.py`, `tests/e2e/test_fake_claude.py` and the new `tests/e2e/test_integrate.py`.
- `fake_claude.py`: standard library only, no `agent_manager` import, no plan-hash computing, no learning the conflict list or merge tip other than from the brief.
- `RESOLVER_ENV = "FAKE_CLAUDE_RESOLVER"`: unset or empty means resolve, `refuse` means claim `resolved=True` and touch nothing, and any other value raises `FakeClaudeError`.
- `IMPLEMENT_EDITS_MARKER = "fake-claude-implement-edits"`: a JSON file in the git common dir mapping branch to `{relative path: full file content}`.
- Twins in `conftest.py`: `FAKE_RESOLVER_ENV` and `FAKE_IMPLEMENT_EDITS_MARKER`, pinned from `test_fake_claude.py`.
- `run_milestone_cli(root, milestone, max_concurrent=None, verify=None)`: `verify=None` means `VERIFY_COMMANDS`, so existing callers' argv stays byte-identical.
- Scenario 4's verify command is the spec's `f"{sys.executable} check.py"`, with two refinements: it is built with `shlex.join` because `verify.run_suite` splits with `shlex.split`, and it adds `-B` so the suite writes no `__pycache__` into a worktree.
- The integration branch is `m3-integrate` and its worktree is `cli.worktree_for(root, "m3-integrate")`.
- `test_integrate.py` is unmarked and runs in the default suite (`pyproject` addopts `-m "not e2e"`).
- Every scenario asserts that `main`'s tip is unchanged, that the root checkout is still on `main`, and that there is no remote and no `refs/remotes`.
- No env var or marker is left armed: env goes through function-scoped `monkeypatch`, and markers live inside each test's own `fresh_project`.
- If a scenario shows product code is wrong, stop and report it. Do not edit `src/` and do not weaken a test to get around it.
- Verification: `uv run pytest`.

## Review Focus

- A `=======` line outside any conflict hunk (a markdown or rst underline in a conflicted file) must survive `keep_both_sides` unchanged. Pinned in Task 3.
- A conflict hunk that never closes must make the fake fail rather than silently truncate the file. Pinned in Task 3.
- An implement-edits entry whose path is absolute, contains `..`, or is empty must be refused before the fake writes anything, so a typo in a test cannot write outside the worktree. Pinned in Task 1.
- If an implement-edits marker exists but the implement brief has no `## branch` section, the fake must fail loudly and must not silently skip the edits. Pinned in Task 1.
- If `## conflict_files` lists a path that is not a file in the cwd, the fake must fail before rewriting or committing anything. Pinned in Task 4.

---

## File Structure

- Modify `tests/e2e/fake_claude.py`: the module docstring; a `_common_dir_file` helper shared by `review_fail_branches` and the new `implement_edits`; `IMPLEMENT_EDITS_MARKER`; `keep_both_sides`; `RESOLVER_ENV`, `RESOLVER_REFUSE`, `REFUSE_SUMMARY`, `resolver_mode`, `conflict_files_of`, `check_merge_head`; the `implement` branch writes marker edits; a new `resolve` branch in `build_result`.
- Modify `tests/e2e/conftest.py`: `FAKE_IMPLEMENT_EDITS_MARKER`, `FAKE_RESOLVER_ENV`, the `two_story_board` fixture, the `FakeResolver` dataclass plus the `fake_resolver` fixture, and a `verify` argument on `run_milestone_cli`.
- Modify `tests/e2e/test_fake_claude.py`: the twin pins (read from `conftest.py` with `ast`, because `--import-mode=importlib` makes conftest names unimportable), implement-edits tests, `keep_both_sides` tests, and resolve-branch tests against a real in-progress merge.
- Create `tests/e2e/test_integrate.py`: the five scenarios, the unmarked guard, and the "nothing left armed" guard.

---

### Task 1: The implement-edits marker

**Files:**
- Modify: `tests/e2e/fake_claude.py:1-21` (docstring), `:205-218` (constants), `:291-303` (`review_fail_branches`), `:342-348` (after `_section`), `:376-405` (`implement` branch)
- Modify: `tests/e2e/conftest.py:52-62` (the `FAKE_*` block)
- Test: `tests/e2e/test_fake_claude.py`

**Interfaces:**
- Consumes: existing `fake_claude.git`, `fake_claude._section`, `fake_claude.sections`, and `test_fake_claude._implement_repo`, `_implement_brief_text`, `_head`, `_porcelain`, `IMPLEMENT_SCHEMA`, `BRIEF_HASH`.
- Produces: `fake_claude.IMPLEMENT_EDITS_MARKER: str`; `fake_claude._common_dir_file(cwd, name) -> Path`; `fake_claude.implement_edits(cwd, found: dict, phase: str) -> dict[Path, str]`; `conftest.FAKE_IMPLEMENT_EDITS_MARKER: str`; `test_fake_claude._conftest_constant(name: str) -> object`.

- [ ] **Step 1: Write the failing tests**

In `tests/e2e/test_fake_claude.py`, add `import ast` to the imports (keep them alphabetical: `import ast` goes before `import importlib.util`). Then, directly after the `fake_claude = ...` / `_spec.loader.exec_module(fake_claude)` block (after line 28), add:

```python
_CONFTEST = Path(__file__).with_name("conftest.py")


def _conftest_constant(name):
    """A module-level literal from `tests/e2e/conftest.py`, read without importing it.

    `--import-mode=importlib` puts nothing on `sys.path`, so conftest names are
    not importable; parsing the file is how the twins are pinned to each other.
    """
    tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"tests/e2e/conftest.py defines no {name}")
```

At the end of the file, add:

```python
IMPLEMENT_BRANCH = "m3/task-a1-00000001"
OTHER_BRANCH = "m3/task-b1-00000002"


def _implement_on_branch(repo, branch=IMPLEMENT_BRANCH):
    """An implement brief that carries `## branch`, as `builtin/task.yaml` renders it."""
    text = _implement_brief_text(BRIEF_HASH) + f"\n## branch\n{branch}\n"
    return fake_claude.build_result(
        "implement", fake_claude.payload_from_schema(IMPLEMENT_SCHEMA), text, repo
    )


def _write_edits(repo, table):
    (repo / ".git" / fake_claude.IMPLEMENT_EDITS_MARKER).write_text(
        json.dumps(table), encoding="utf-8"
    )


def _show(repo, spec):
    return subprocess.run(
        ["git", "-C", str(repo), "show", spec],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_the_implement_edits_marker_name_is_the_conftest_twin():
    """The fixture writes `FAKE_IMPLEMENT_EDITS_MARKER`; the script reads
    `IMPLEMENT_EDITS_MARKER`. They meet across a process boundary."""
    assert fake_claude.IMPLEMENT_EDITS_MARKER == "fake-claude-implement-edits"
    assert fake_claude.IMPLEMENT_EDITS_MARKER == _conftest_constant(
        "FAKE_IMPLEMENT_EDITS_MARKER"
    )


def test_the_marker_entry_for_the_briefs_branch_is_written_and_committed(tmp_path):
    repo = _implement_repo(tmp_path)
    _write_edits(
        repo,
        {
            IMPLEMENT_BRANCH: {"shared.txt": "story A\n", "pkg/nested.txt": "deep\n"},
            OTHER_BRANCH: {"shared.txt": "story B\n"},
        },
    )

    payload = _implement_on_branch(repo)

    assert payload["resumed"] is False
    assert _show(repo, "HEAD:shared.txt") == "story A\n"
    assert _show(repo, "HEAD:pkg/nested.txt") == "deep\n"
    assert _show(repo, f"HEAD:{fake_claude.IMPLEMENTATION_NAME}")  # still written
    assert _porcelain(repo) == ""


def test_a_marker_entry_for_another_branch_writes_nothing_extra(tmp_path):
    repo = _implement_repo(tmp_path)
    _write_edits(repo, {OTHER_BRANCH: {"shared.txt": "story B\n"}})

    payload = _implement_on_branch(repo)

    assert payload["resumed"] is False
    assert not (repo / "shared.txt").exists()
    assert _porcelain(repo) == ""


def test_a_marker_with_no_branch_section_in_the_brief_stops_the_fake(tmp_path):
    """Review focus: the edits are keyed by the brief's `## branch`. A brief
    without it must fail loudly, never quietly skip the edits."""
    repo = _implement_repo(tmp_path)
    _write_edits(repo, {IMPLEMENT_BRANCH: {"shared.txt": "story A\n"}})
    before = _head(repo)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement(repo)

    assert "branch" in str(caught.value)
    assert _head(repo) == before
    assert _porcelain(repo) == ""


@pytest.mark.parametrize("relative", ["../outside.txt", "/tmp/absolute.txt", ""])
def test_a_marker_path_outside_the_worktree_is_refused_before_any_write(
    tmp_path, relative
):
    """Review focus: a typo in a test must not write outside the worktree."""
    repo = _implement_repo(tmp_path)
    _write_edits(repo, {IMPLEMENT_BRANCH: {relative: "nope\n"}})
    before = _head(repo)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement_on_branch(repo)

    assert "not a path inside the worktree" in str(caught.value)
    assert _head(repo) == before
    assert _porcelain(repo) == ""
    assert not (tmp_path / "outside.txt").exists()


def test_a_marker_that_is_not_json_is_refused(tmp_path):
    repo = _implement_repo(tmp_path)
    (repo / ".git" / fake_claude.IMPLEMENT_EDITS_MARKER).write_text(
        "{not json", encoding="utf-8"
    )

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement_on_branch(repo)

    assert "not valid JSON" in str(caught.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v -k "implement_edits or marker_entry or marker_with_no_branch or marker_path or marker_that_is_not_json"`
Expected: FAIL. Each test errors with `AttributeError: module 'e2e_fake_claude' has no attribute 'IMPLEMENT_EDITS_MARKER'`.

- [ ] **Step 3: Add the conftest twin**

In `tests/e2e/conftest.py`, directly after the `FAKE_REVIEW_FAIL_MARKER` constant and its docstring (after line 53), add:

```python
FAKE_IMPLEMENT_EDITS_MARKER = "fake-claude-implement-edits"
"""Must equal `fake_claude.IMPLEMENT_EDITS_MARKER`, which `test_fake_claude.py` pins.

A JSON file in the repo's git common dir mapping a branch to
`{relative path: full file content}`: what that branch's implement writes."""
```

- [ ] **Step 4: Add the constant, the shared helper and `implement_edits` to the fake**

In `tests/e2e/fake_claude.py`, directly after `REVIEW_FAIL_PORCELAIN` and its docstring (after line 218), add:

```python
IMPLEMENT_EDITS_MARKER = "fake-claude-implement-edits"
"""A JSON file, in the repo's git common dir, of files a subtask's implement writes.

It maps a branch to `{repo-relative path: full file content}`. It is found from
this process's own cwd through `git rev-parse --git-common-dir`, like
`REVIEW_FAIL_MARKER`, so it is in no worktree's tree. It is keyed by the
implement brief's `## branch` section, so the brief still picks the subtask.
The marker only says what that subtask's files contain, which is how the
Integrate tests make two stories edit the same line. No marker, or no entry
for the branch, changes nothing.
"""
```

Replace `review_fail_branches` (lines 291-303) with:

```python
def _common_dir_file(cwd, name):
    """`<git common dir>/<name>`, found from `cwd`."""
    common = git(cwd, "rev-parse", "--git-common-dir").strip()
    # Relative (`.git`) in a main checkout, absolute in a linked worktree;
    # joining onto the cwd handles both.
    return Path(cwd) / common / name


def review_fail_branches(cwd):
    """The branches the review-fail marker names, or an empty set when there is none."""
    marker = _common_dir_file(cwd, REVIEW_FAIL_MARKER)
    if not marker.is_file():
        return set()
    return {
        line.strip()
        for line in marker.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
```

Directly after `_section` (after line 348), add:

```python
def _inside_worktree(relative, marker):
    """`relative` as a `Path`, refusing anything that could land outside the cwd."""
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts:
        raise FakeClaudeError(
            f"the implement-edits marker {marker} names {relative!r}, which is "
            "not a path inside the worktree"
        )
    return path


def implement_edits(cwd, found, phase):
    """The files the implement-edits marker gives the brief's `## branch`, or `{}`.

    No marker means `{}` without reading the brief, so an implement brief that
    carries no `## branch` still works when no test asked for edits.
    """
    marker = _common_dir_file(cwd, IMPLEMENT_EDITS_MARKER)
    if not marker.is_file():
        return {}
    branch = _section(found, "branch", phase)
    try:
        table = json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FakeClaudeError(
            f"the implement-edits marker {marker} is not valid JSON: {error}"
        ) from None
    if not isinstance(table, dict):
        raise FakeClaudeError(
            f"the implement-edits marker {marker} is not a JSON object of branches"
        )
    entry = table.get(branch, {})
    if not isinstance(entry, dict) or not all(
        isinstance(name, str) and isinstance(content, str)
        for name, content in entry.items()
    ):
        raise FakeClaudeError(
            f"the implement-edits marker {marker} entry for {branch!r} is not an "
            "object of path -> content strings"
        )
    return {_inside_worktree(name, marker): content for name, content in entry.items()}
```

In the `implement` branch of `build_result`, replace lines 384-391 (from `digest = _section(found, "plan_hash", phase)` through `git(cwd, "add", "-A")`) with:

```python
        digest = _section(found, "plan_hash", phase)
        relative = _section(found, "plan_path", phase)
        # Test scaffolding, keyed by the brief's `## branch`: read and checked
        # before anything is written, so a bad marker leaves the tree untouched.
        edits = implement_edits(cwd, found, phase)
        # The content names this card's plan, so a subtask stacked on another's
        # branch (where the file already exists) still has a change to commit.
        (Path(cwd) / IMPLEMENTATION_NAME).write_text(
            f"# implementation of {relative}\n\n{SUMMARY}\n", encoding="utf-8"
        )
        for path, content in edits.items():
            target = Path(cwd) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        git(cwd, "add", "-A")
```

Keep the comment block about card f26b377d that sits above `digest = ...`.

- [ ] **Step 5: Update the module docstring to three inputs**

In `tests/e2e/fake_claude.py`, replace lines 13-18 (from `fail, because that failure is the test's whole point. There are exactly two` through `which only makes implement wait for other lanes and changes nothing it writes.`) with:

```
fail, because that failure is the test's whole point. There are exactly three
test-controlled inputs, and none tells the fake anything the brief owns:
`REVIEW_FAIL_MARKER`, a file in the repo's git common dir that the fake finds
from its own cwd and compares with the brief's `## branch`;
`IMPLEMENT_EDITS_MARKER`, a JSON file beside it giving the files an implement
writes for the brief's `## branch`; and the implement-only rendezvous
(`RENDEZVOUS_DIR_ENV` / `RENDEZVOUS_COUNT_ENV`), which only makes implement
wait for other lanes and changes nothing it writes.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: PASS for every test in the file, the pre-existing review-fail and rendezvous tests included.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/conftest.py tests/e2e/test_fake_claude.py
git commit -m "test(e2e): let the fake coder write per-branch files from an implement-edits marker"
```

---

### Task 2: A two-story board and the different-files scenario

**Files:**
- Modify: `tests/e2e/conftest.py` (new fixture after `parallel_board`, ~line 364)
- Create: `tests/e2e/test_integrate.py`

**Interfaces:**
- Consumes: `conftest.FAKE_IMPLEMENT_EDITS_MARKER` (Task 1), `conftest.UNION_ATTRIBUTE`, `_add_card`, `fresh_project`, `run_milestone_cli`, `read_fake_log`, `dag.task_branch`, `board.show`, `MILESTONE_PREFIX`.
- Produces: fixture `two_story_board -> dict` with keys `root: Path`, `milestone: str`, `stories: {"A": str, "B": str}`, `subtasks: {"A": [str], "B": [str]}`, `branches: {card_id: str}`, `implement_edits_marker: Path`. Test-module helpers `_git`, `_is_ancestor`, `_envelope`, `_load_run`, `_integration_worktree`, `_merge_in_progress`, `_seed`, `_write_edits`, `_phases`, `_assert_base_untouched`, and the constant `INTEGRATION_BRANCH = "m3-integrate"`.

- [ ] **Step 1: Write the failing test module**

Create `tests/e2e/test_integrate.py`:

```python
"""Default-suite e2e tier: Integrate through the production wiring (addendum I7).

`am run --milestone` runs through `typer.testing.CliRunner` on the real
`cli.app` with no `runner_factory`, so `orchestrate.run_milestone` reaches
`integration.integrate_milestone`, `cli.default_runner_factory`, the real
`ClaudeAdapter` and `launcher.run_direct`. The only stand-in is the fake
`claude` first on `PATH`. It resolves a conflict from the resolve brief's
`## merge_tip` and `## conflict_files` alone. Unmarked on purpose: this costs
no model and must run on every `uv run pytest`.

Each test builds its own repo and board (`two_story_board`): stories A and B
are independent roots with one subtask each. The implement-edits marker makes
each story's implement write the files a scenario needs, keyed by the brief's
`## branch`.
"""

import json
import subprocess
from pathlib import Path

from agent_manager import cli, models, store

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _integration_worktree(root: Path) -> Path:
    return cli.worktree_for(root, INTEGRATION_BRANCH)


def _merge_in_progress(worktree: Path) -> bool:
    """Whether git holds a MERGE_HEAD in `worktree`."""
    probe = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def _seed(root: Path, files: dict[str, str]) -> str:
    """Commit the scenario's base files on `main`, and return `main`'s new tip."""
    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed the scenario's base files")
    return _git(root, "rev-parse", "main").strip()


def _write_edits(board: dict, edits: dict[str, dict[str, str]]) -> None:
    """The implement-edits marker: story key -> files, keyed by the story's subtask branch."""
    table = {
        board["branches"][board["subtasks"][key][0]]: files
        for key, files in edits.items()
    }
    board["implement_edits_marker"].write_text(json.dumps(table), encoding="utf-8")


def _phases(entries) -> list[str]:
    return [entry["phase"] for entry in entries]


def _all_phase_names(run: models.Run) -> set[str]:
    return {
        phase.name
        for story in run.stories
        for subtask in story.subtasks
        for phase in subtask.phases
    }


def _assert_base_untouched(root: Path, main_before: str) -> None:
    """Integrate rule I5: the base branch never moves and nothing is pushed."""
    assert _git(root, "rev-parse", "main").strip() == main_before
    assert _git(root, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert _git(root, "remote").strip() == ""
    assert _git(root, "for-each-ref", "refs/remotes").strip() == ""


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or Integrate stops being checked
    on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_stories_that_touch_different_files_integrate_with_no_resolver(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Scenario 3: a textually clean merge never dispatches an agent."""
    root = two_story_board["root"]
    stories = two_story_board["stories"]
    main_before = _git(root, "rev-parse", "main").strip()
    _write_edits(
        two_story_board,
        {"A": {"a.txt": "written by story A\n"}, "B": {"b.txt": "written by story B\n"}},
    )

    result = run_milestone_cli(root, two_story_board["milestone"])

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(_integration_worktree(root)),
        "merged": [stories["A"], stories["B"]],
        "resolved": [],
    }
    assert _git(root, "show", f"{INTEGRATION_BRANCH}:a.txt") == "written by story A\n"
    assert _git(root, "show", f"{INTEGRATION_BRANCH}:b.txt") == "written by story B\n"

    entries = read_fake_log(data["run_id"])
    assert entries  # non-vacuity: the stories' agents did run
    assert "resolve" not in _phases(entries)
    run = _load_run(root, data["run_id"])
    assert run.status == "done"
    assert "integrate" not in {story.card_id for story in run.stories}
    assert "Integrate" not in {story.title for story in run.stories}
    assert "resolve" not in _all_phase_names(run)

    assert not _merge_in_progress(_integration_worktree(root))
    _assert_base_untouched(root, main_before)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/e2e/test_integrate.py -v`
Expected: `test_this_module_runs_in_the_default_suite_unmarked` PASSES, and `test_stories_that_touch_different_files_integrate_with_no_resolver` ERRORS with `fixture 'two_story_board' not found`.

- [ ] **Step 3: Add the `two_story_board` fixture**

In `tests/e2e/conftest.py`, directly after the `parallel_board` fixture (after line 363), add:

```python
@pytest.fixture
def two_story_board(fresh_project) -> dict[str, Any]:
    """One milestone with two independent stories, A (a1) and B (b1), for Integrate.

    Both stories are level-0 roots, so Integrate merges A's tip and then B's
    into `m3-integrate`. `UNION_ATTRIBUTE` folds the fake coder's
    `IMPLEMENTATION.md` and covers only that file, so any conflict a scenario
    wants comes from the implement-edits marker's own files. The test writes
    that marker (`implement_edits_marker`) keyed by `branches`.
    """
    root = fresh_project
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    milestone = _add_card(root, "Milestone 5: integrate under a fake claude")
    a = _add_card(root, "Story A: one side of the merge", milestone)
    b = _add_card(root, "Story B: the other side of the merge", milestone)
    a1 = _add_card(root, "a1: only subtask of story A", a)
    b1 = _add_card(root, "b1: only subtask of story B", b)
    subtasks = {"A": [a1], "B": [b1]}
    branches = {
        card_id: dag.task_branch(MILESTONE_PREFIX, board.show(card_id, repo_dir=root))
        for chain in subtasks.values()
        for card_id in chain
    }
    return {
        "root": root,
        "milestone": milestone,
        "stories": {"A": a, "B": b},
        "subtasks": subtasks,
        "branches": branches,
        "implement_edits_marker": root / ".git" / FAKE_IMPLEMENT_EDITS_MARKER,
    }
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/e2e/test_integrate.py -v`
Expected: PASS for both tests. If the scenario fails in product code (for example a `resolve` phase is dispatched for a clean merge), stop and report it. Do not edit `src/`.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_integrate.py
git commit -m "test(e2e): prove a clean two-story Integrate dispatches no resolver"
```

---

### Task 3: `keep_both_sides`, the fake's hunk rewriter

**Files:**
- Modify: `tests/e2e/fake_claude.py` (new functions directly after `implement_edits`)
- Test: `tests/e2e/test_fake_claude.py`

**Interfaces:**
- Consumes: `fake_claude.FakeClaudeError`.
- Produces: `fake_claude.keep_both_sides(text: str) -> str`, and `fake_claude._is_marker(bare: str, sigil: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/e2e/test_fake_claude.py`:

```python
def test_both_sides_of_a_hunk_are_kept_ours_then_theirs():
    text = "top\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> side\nbottom\n"

    assert fake_claude.keep_both_sides(text) == "top\nours\ntheirs\nbottom\n"


def test_a_diff3_base_section_is_dropped():
    text = (
        "<<<<<<< HEAD\nours\n||||||| merged common ancestors\nbase\n"
        "=======\ntheirs\n>>>>>>> side\n"
    )

    assert fake_claude.keep_both_sides(text) == "ours\ntheirs\n"


def test_every_hunk_in_a_file_is_rewritten():
    text = (
        "<<<<<<< HEAD\none\n=======\nuno\n>>>>>>> side\n"
        "middle\n"
        "<<<<<<< HEAD\ntwo\n=======\ndos\n>>>>>>> side\n"
    )

    assert fake_claude.keep_both_sides(text) == "one\nuno\nmiddle\ntwo\ndos\n"


def test_an_underline_outside_a_hunk_is_kept():
    """Review focus: `=======` is a markdown/rst underline as often as a marker;
    it only counts inside a hunk."""
    text = "Title\n=======\n\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> side\n"

    assert fake_claude.keep_both_sides(text) == "Title\n=======\n\nours\ntheirs\n"


def test_crlf_lines_keep_their_endings():
    text = "<<<<<<< HEAD\r\nours\r\n=======\r\ntheirs\r\n>>>>>>> side\r\n"

    assert fake_claude.keep_both_sides(text) == "ours\r\ntheirs\r\n"


def test_a_file_with_no_hunk_is_unchanged():
    assert fake_claude.keep_both_sides("plain\ntext\n") == "plain\ntext\n"


def test_an_unclosed_hunk_is_refused_rather_than_truncated():
    """Review focus: half a rewrite would commit a file missing its tail."""
    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.keep_both_sides("<<<<<<< HEAD\nours\n=======\ntheirs\n")

    assert "never closed" in str(caught.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v -k "hunk or diff3 or underline or crlf or no_hunk"`
Expected: FAIL with `AttributeError: module 'e2e_fake_claude' has no attribute 'keep_both_sides'`.

- [ ] **Step 3: Implement `keep_both_sides`**

In `tests/e2e/fake_claude.py`, directly after `implement_edits`, add:

```python
def _is_marker(bare, sigil):
    """A conflict-marker line: the seven-character sigil alone or followed by a space."""
    return bare == sigil or bare.startswith(sigil + " ")


def keep_both_sides(text):
    """`text` with every conflict hunk replaced by its two sides, ours then theirs.

    The marker lines go, and so does a diff3/zdiff3 `|||||||` base section, so
    a developer's `merge.conflictStyle` cannot break the fake. A `=======` line
    counts only inside a hunk, so a markdown underline survives. Line endings
    are kept as they were. A hunk that never closes is refused rather than
    half-rewritten.
    """
    kept = []
    state = None  # None outside a hunk, else "ours", "base" or "theirs"
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if state is None:
            if _is_marker(bare, "<<<<<<<"):
                state = "ours"
            else:
                kept.append(line)
        elif state == "ours" and _is_marker(bare, "|||||||"):
            state = "base"
        elif state in ("ours", "base") and bare == "=======":
            state = "theirs"
        elif state == "theirs" and _is_marker(bare, ">>>>>>>"):
            state = None
        elif state != "base":
            kept.append(line)
    if state is not None:
        raise FakeClaudeError(
            "a conflict hunk is never closed: no `>>>>>>>` line after its `<<<<<<<`"
        )
    return "".join(kept)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: PASS for the whole file.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_fake_claude.py
git commit -m "test(e2e): add the fake resolver's keep-both-sides hunk rewriter"
```

---

### Task 4: The fake's `resolve` phase, and the same-line scenario

**Files:**
- Modify: `tests/e2e/fake_claude.py` (constants after `IMPLEMENT_EDITS_MARKER`; functions after `keep_both_sides`; a new branch in `build_result` before the final `raise`; module docstring)
- Test: `tests/e2e/test_fake_claude.py`, `tests/e2e/test_integrate.py`

**Interfaces:**
- Consumes: `keep_both_sides` (Task 3), `_section`, `git`, `override`, `SUMMARY`; in tests `_implement_repo`, `_porcelain`, `_head`, `steps.integrate.merge_completed_gate(result, worktree)`; in `test_integrate.py` the Task 2 helpers and `two_story_board`.
- Produces: `fake_claude.RESOLVER_ENV = "FAKE_CLAUDE_RESOLVER"`, `fake_claude.RESOLVER_REFUSE = "refuse"`, `fake_claude.REFUSE_SUMMARY: str`, `fake_claude.resolver_mode() -> str` (`"resolve"` or `"refuse"`), `fake_claude.conflict_files_of(found, phase) -> list[str]`, `fake_claude.check_merge_head(cwd, tip) -> None`. In `test_integrate.py`: constants `SHARED`, `BASE_LINE`, `A_LINE`, `B_LINE` and helper `_same_line_setup(board) -> str` (returns `main`'s tip after seeding).

- [ ] **Step 1: Write the failing fake tests**

In `tests/e2e/test_fake_claude.py`, add this import below `from agent_manager.steps.reducers import review_gate`:

```python
from agent_manager.steps.integrate import merge_completed_gate
```

Append to the file:

```python
RESOLVE_SCHEMA = {
    "properties": {
        "resolved": {"type": "boolean"},
        "summary": {"type": "string"},
    },
    "type": "object",
}
"""`results.ResolveResult`'s shape, written out for the same reason as
`IMPLEMENT_SCHEMA`."""

SIDE_BRANCH = "m3/task-b1-00000002"
CONFLICT_FILES = '[\n  "shared.txt"\n]'
"""`prompt._inline_json`'s multi-line rendering of `["shared.txt"]`."""


def _git_ok(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _merge_head_exists(repo):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def _conflicted_repo(tmp_path, style="merge"):
    """A real repo stopped mid-merge: `main` says `ours`, `SIDE_BRANCH` says `theirs`.

    The conflict style is set in the repo's own config, so a developer's global
    `merge.conflictStyle` cannot change which markers the test sees.
    """
    repo = _implement_repo(tmp_path)
    _git_ok(repo, "config", "merge.conflictStyle", style)
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    _git_ok(repo, "add", "shared.txt")
    _git_ok(repo, "commit", "-m", "base line")
    _git_ok(repo, "checkout", "-b", SIDE_BRANCH)
    (repo / "shared.txt").write_text("theirs\n", encoding="utf-8")
    _git_ok(repo, "commit", "-am", "theirs")
    _git_ok(repo, "checkout", "main")
    (repo / "shared.txt").write_text("ours\n", encoding="utf-8")
    _git_ok(repo, "commit", "-am", "ours")
    merge = subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "--no-edit", SIDE_BRANCH],
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0, merge.stdout  # non-vacuity: a real conflict
    assert _merge_head_exists(repo)
    return repo


def _resolve_brief_text(tip=SIDE_BRANCH, files=CONFLICT_FILES):
    """A `resolve` brief shaped like `builtin/integrate.yaml` renders it."""
    text = (
        "# Resolver\n\nstanding instructions\n\n"
        "# phase: resolve\n# role: resolver\n"
        "\n## branch\nm3-integrate\n"
        "\n## base_branch\nmain\n"
    )
    if tip is not None:
        text += f"\n## merge_tip\n{tip}\n"
    if files is not None:
        text += f"\n## conflict_files\n{files}\n"
    return text


def _resolve(repo, **brief):
    return fake_claude.build_result(
        "resolve",
        fake_claude.payload_from_schema(RESOLVE_SCHEMA),
        _resolve_brief_text(**brief),
        repo,
    )


def test_the_resolver_env_var_name_is_pinned():
    assert fake_claude.RESOLVER_ENV == "FAKE_CLAUDE_RESOLVER"
    assert fake_claude.RESOLVER_REFUSE == "refuse"


@pytest.mark.parametrize("style", ["merge", "diff3", "zdiff3"])
def test_the_resolver_keeps_both_sides_commits_and_the_real_gate_passes(
    tmp_path, monkeypatch, style
):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path, style)

    payload = _resolve(repo)

    assert payload == {"resolved": True, "summary": fake_claude.SUMMARY}
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "ours\ntheirs\n"
    assert not _merge_head_exists(repo)
    assert _porcelain(repo) == ""
    parents = _git_ok(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert len(parents) == 3  # a real merge commit: itself plus two parents
    assert merge_completed_gate(payload, repo) is None


def test_an_empty_resolver_env_value_means_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv(fake_claude.RESOLVER_ENV, "")
    repo = _conflicted_repo(tmp_path)

    _resolve(repo)

    assert not _merge_head_exists(repo)


def test_a_refusing_resolver_claims_resolved_but_git_still_says_no(
    tmp_path, monkeypatch
):
    """Addendum I3: `resolved` is advisory. Refuse mode lies, and the production
    gate, which only asks git, blocks it."""
    monkeypatch.setenv(fake_claude.RESOLVER_ENV, fake_claude.RESOLVER_REFUSE)
    repo = _conflicted_repo(tmp_path)
    before = (repo / "shared.txt").read_bytes()
    head = _head(repo)

    payload = _resolve(repo)

    assert payload == {"resolved": True, "summary": fake_claude.REFUSE_SUMMARY}
    assert _merge_head_exists(repo)
    assert (repo / "shared.txt").read_bytes() == before
    assert b"<<<<<<< " in before
    assert _head(repo) == head
    verdict = merge_completed_gate(payload, repo)
    assert verdict is not None and "MERGE_HEAD" in verdict["detail"]


def test_an_unknown_resolver_env_value_fails_the_fake_and_touches_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(fake_claude.RESOLVER_ENV, "refused")
    repo = _conflicted_repo(tmp_path)
    before = (repo / "shared.txt").read_bytes()

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo)

    assert fake_claude.RESOLVER_ENV in str(caught.value)
    assert "refused" in str(caught.value)
    assert _merge_head_exists(repo)
    assert (repo / "shared.txt").read_bytes() == before


@pytest.mark.parametrize(
    ("missing", "brief"),
    [("merge_tip", {"tip": None}), ("conflict_files", {"files": None})],
)
def test_a_resolve_brief_missing_a_section_fails_the_fake(
    tmp_path, monkeypatch, missing, brief
):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo, **brief)

    assert missing in str(caught.value)
    assert _merge_head_exists(repo)


@pytest.mark.parametrize(
    "files", ["not json", '{"shared.txt": 1}', "[1]", '[""]', '"shared.txt"']
)
def test_a_malformed_conflict_files_section_fails_the_fake(
    tmp_path, monkeypatch, files
):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo, files=files)

    assert "conflict_files" in str(caught.value)
    assert _merge_head_exists(repo)


def test_a_merge_tip_that_is_not_merge_head_fails_the_fake(tmp_path, monkeypatch):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo, tip="main")

    assert "MERGE_HEAD" in str(caught.value)
    assert _merge_head_exists(repo)


def test_a_resolve_with_no_merge_in_progress_fails_the_fake(tmp_path, monkeypatch):
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _implement_repo(tmp_path)
    _git_ok(repo, "branch", SIDE_BRANCH)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo)

    assert "MERGE_HEAD" in str(caught.value)


def test_a_listed_conflict_file_that_is_not_there_fails_before_any_write(
    tmp_path, monkeypatch
):
    """Review focus: nothing is rewritten or committed if one path is wrong."""
    monkeypatch.delenv(fake_claude.RESOLVER_ENV, raising=False)
    repo = _conflicted_repo(tmp_path)
    before = (repo / "shared.txt").read_bytes()

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _resolve(repo, files='["shared.txt", "missing.txt"]')

    assert "missing.txt" in str(caught.value)
    assert (repo / "shared.txt").read_bytes() == before
    assert _merge_head_exists(repo)
```

- [ ] **Step 2: Write the failing same-line e2e scenario**

In `tests/e2e/test_integrate.py`, add below `INTEGRATION_BRANCH` and its docstring:

```python
SHARED = "shared.txt"
BASE_LINE = "the line both stories rewrite\n"
A_LINE = "story A rewrote this line\n"
B_LINE = "story B rewrote this line\n"
```

Add below `_assert_base_untouched`:

```python
def _same_line_setup(board: dict) -> str:
    """Seed `shared.txt` on main; A and B each rewrite its one line differently."""
    main_before = _seed(board["root"], {SHARED: BASE_LINE})
    _write_edits(board, {"A": {SHARED: A_LINE}, "B": {SHARED: B_LINE}})
    return main_before
```

Append to the module:

```python
def test_a_same_line_conflict_is_resolved_verified_and_left_on_the_integration_branch(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Scenario 1: B's merge conflicts with A's; the resolver keeps both sides."""
    root = two_story_board["root"]
    stories = two_story_board["stories"]
    subtasks = two_story_board["subtasks"]
    branches = two_story_board["branches"]
    main_before = _same_line_setup(two_story_board)
    worktree = _integration_worktree(root)

    result = run_milestone_cli(root, two_story_board["milestone"])

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(worktree),
        "merged": [stories["A"], stories["B"]],
        "resolved": [stories["B"]],
    }
    # Both edits, ours (A, merged first) then theirs (B), and no markers.
    assert (worktree / SHARED).read_text(encoding="utf-8") == A_LINE + B_LINE
    assert _git(root, "show", f"{INTEGRATION_BRANCH}:{SHARED}") == A_LINE + B_LINE
    assert not _merge_in_progress(worktree)
    assert _git(worktree, "status", "--porcelain") == ""
    for key in ("A", "B"):
        assert _is_ancestor(root, branches[subtasks[key][0]], INTEGRATION_BRANCH), key

    resolves = [entry for entry in read_fake_log(data["run_id"]) if entry["phase"] == "resolve"]
    assert len(resolves) == 1, resolves
    assert Path(resolves[0]["cwd"]).resolve() == worktree.resolve()

    # `done` only after the integrate workflow's own verify phase passed.
    run = _load_run(root, data["run_id"])
    assert run.status == "done"
    (integrate_story,) = [story for story in run.stories if story.card_id == "integrate"]
    assert integrate_story.title == "Integrate"
    assert integrate_story.status == "done"
    (resolver_row,) = integrate_story.subtasks
    assert resolver_row.card_id == stories["B"]
    assert [phase.name for phase in resolver_row.phases] == ["resolve", "verify"]
    assert {phase.status for phase in resolver_row.phases} == {"done"}

    _assert_base_untouched(root, main_before)
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py tests/e2e/test_integrate.py -v -k "resolve or resolver or conflict_files or merge_tip or same_line"`
Expected: FAIL. The fake tests error with `AttributeError: module 'e2e_fake_claude' has no attribute 'RESOLVER_ENV'`. The e2e scenario fails with exit code 1 (an Integrate escalation), because the real child exits with `fake-claude: no behaviour for phase 'resolve'`.

- [ ] **Step 4: Add the resolver constants**

In `tests/e2e/fake_claude.py`, directly after `IMPLEMENT_EDITS_MARKER` and its docstring, add:

```python
RESOLVER_ENV = "FAKE_CLAUDE_RESOLVER"
"""Test scaffolding, never in a brief: how the resolve phase behaves.

Unset or empty resolves the merge. `refuse` claims `resolved: true` and leaves
the merge exactly as it found it (no edit, no add, no commit), so the
production `merge_completed_gate`, which asks git and never reads the flag,
has to catch it. Any other value is a typo and fails the fake."""

RESOLVER_REFUSE = "refuse"

REFUSE_SUMMARY = (
    "the fake claude executable was told to refuse: it claims the merge is "
    "resolved but left MERGE_HEAD, the conflict markers and the index exactly "
    "as it found them."
)
```

- [ ] **Step 5: Add the resolver helpers**

Directly after `keep_both_sides`, add:

```python
def resolver_mode():
    """`"resolve"` or `RESOLVER_REFUSE`, from `RESOLVER_ENV`. Anything else is refused."""
    raw = os.environ.get(RESOLVER_ENV, "")
    if raw == "":
        return "resolve"
    if raw == RESOLVER_REFUSE:
        return RESOLVER_REFUSE
    raise FakeClaudeError(
        f"{RESOLVER_ENV} must be unset, empty or {RESOLVER_REFUSE!r}, got {raw!r}"
    )


def conflict_files_of(found, phase):
    """The brief's `## conflict_files`: a JSON list of non-empty path strings."""
    raw = _section(found, "conflict_files", phase)
    try:
        files = json.loads(raw)
    except json.JSONDecodeError:
        raise FakeClaudeError(
            f"the {phase!r} brief's `## conflict_files` is not JSON: {raw!r}"
        ) from None
    if not isinstance(files, list) or not all(
        isinstance(name, str) and name for name in files
    ):
        raise FakeClaudeError(
            f"the {phase!r} brief's `## conflict_files` is not a JSON list of "
            f"paths: {raw!r}"
        )
    return files


def check_merge_head(cwd, tip):
    """Refuse unless `MERGE_HEAD` in `cwd` is the commit the brief's `merge_tip` names."""
    probe = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise FakeClaudeError(
            f"no merge is in progress in {cwd} (no MERGE_HEAD), yet the brief's "
            f"merge_tip is {tip!r}"
        )
    merge_head = probe.stdout.strip()
    wanted = git(cwd, "rev-parse", "--verify", f"{tip}^{{commit}}").strip()
    if merge_head != wanted:
        raise FakeClaudeError(
            f"MERGE_HEAD in {cwd} is {merge_head}, not the brief's merge_tip "
            f"{tip!r} ({wanted})"
        )
```

- [ ] **Step 6: Add the `resolve` branch to `build_result`**

In `build_result`, directly before the final `raise FakeClaudeError(f"no behaviour for phase {phase!r}")`, add:

```python
    if phase == "resolve":
        # Everything is checked before anything is touched: the env switch, the
        # two brief sections, that the merge in the cwd is the brief's, and
        # that every listed file is there.
        mode = resolver_mode()
        tip = _section(found, "merge_tip", phase)
        files = conflict_files_of(found, phase)
        check_merge_head(cwd, tip)
        missing = [name for name in files if not (Path(cwd) / name).is_file()]
        if missing:
            raise FakeClaudeError(
                f"the brief's `## conflict_files` names paths that are not files "
                f"in {cwd}: {missing}"
            )
        if mode == RESOLVER_REFUSE:
            # The advisory flag lies; git, through `merge_completed_gate`, judges.
            return override(payload, resolved=True, summary=REFUSE_SUMMARY)
        rewritten = {
            name: keep_both_sides((Path(cwd) / name).read_bytes().decode("utf-8"))
            for name in files
        }
        for name, text in rewritten.items():
            (Path(cwd) / name).write_bytes(text.encode("utf-8"))
            git(cwd, "add", "--", name)
        git(cwd, "commit", "--no-edit")
        return override(payload, resolved=True, summary=SUMMARY)
```

- [ ] **Step 7: Update the module docstring to four inputs**

In `tests/e2e/fake_claude.py`, replace the docstring paragraph you wrote in Task 1 Step 5 (from `fail, because that failure is the test's whole point. There are exactly three` through `wait for other lanes and changes nothing it writes.`) with:

```
fail, because that failure is the test's whole point. There are exactly four
test-controlled inputs, and none tells the fake anything the brief owns:
`REVIEW_FAIL_MARKER`, a file in the repo's git common dir that the fake finds
from its own cwd and compares with the brief's `## branch`;
`IMPLEMENT_EDITS_MARKER`, a JSON file beside it giving the files an implement
writes for the brief's `## branch`; the implement-only rendezvous
(`RENDEZVOUS_DIR_ENV` / `RENDEZVOUS_COUNT_ENV`), which only makes implement
wait for other lanes and changes nothing it writes; and `RESOLVER_ENV`, which
only makes the resolve phase leave the merge it was given unfinished while
still claiming `resolved`, so git has to catch the lie. The resolve phase
learns the tip and the conflicting files from the brief's `## merge_tip` and
`## conflict_files` and nowhere else.
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py tests/e2e/test_integrate.py -v`
Expected: PASS for both files. If the e2e scenario fails on the product side (for example `resolved` lists the wrong story, or the integrate story is not recorded `done`), stop and report it. Do not edit `src/`.

- [ ] **Step 9: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_fake_claude.py tests/e2e/test_integrate.py
git commit -m "test(e2e): teach the fake a brief-driven resolve phase and prove a same-line conflict integrates"
```

---

### Task 5: The resolver refuses and a human finishes

**Files:**
- Modify: `tests/e2e/conftest.py` (`FAKE_*` block; new `FakeResolver` dataclass and `fake_resolver` fixture after the `rendezvous` fixture, ~line 325)
- Test: `tests/e2e/test_fake_claude.py`, `tests/e2e/test_integrate.py`

**Interfaces:**
- Consumes: `fake_claude.RESOLVER_ENV` (Task 4), `_conftest_constant` (Task 1), `_same_line_setup`, `SHARED`, `A_LINE`, `B_LINE` and the Task 2 helpers.
- Produces: `conftest.FAKE_RESOLVER_ENV = "FAKE_CLAUDE_RESOLVER"`; `conftest.FakeResolver(monkeypatch)` with `refuse() -> None` and `reset() -> None`; fixture `fake_resolver -> FakeResolver`.

- [ ] **Step 1: Write the failing twin pin**

Append to `tests/e2e/test_fake_claude.py`:

```python
def test_the_resolver_env_var_is_the_conftest_twin():
    """The fixture sets `FAKE_RESOLVER_ENV`; the script reads `RESOLVER_ENV`."""
    assert fake_claude.RESOLVER_ENV == _conftest_constant("FAKE_RESOLVER_ENV")
```

- [ ] **Step 2: Write the failing e2e scenario**

Append to `tests/e2e/test_integrate.py` (its imports already cover `cli` and `Path`):

```python
def test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete(
    two_story_board, fake_resolver, run_milestone_cli, read_fake_log
):
    """Scenario 2: git, not the resolver's `resolved` flag, decides."""
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    main_before = _same_line_setup(two_story_board)
    worktree = _integration_worktree(root)
    fake_resolver.refuse()

    first = run_milestone_cli(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    data = _envelope(first)
    assert data["escalated"] is True, data
    assert data["phase"] == "integrate"
    assert data["story"] == stories["B"]
    assert SHARED in data["files"]
    assert data["run_id"]
    assert str(worktree) in data["detail"]
    assert "integrated" not in data
    assert _merge_in_progress(worktree)
    resolves = [entry for entry in read_fake_log(data["run_id"]) if entry["phase"] == "resolve"]
    assert resolves  # non-vacuity: the resolver really was dispatched
    assert {Path(entry["cwd"]).resolve() for entry in resolves} == {worktree.resolve()}
    run = _load_run(root, data["run_id"])
    assert run.status == "escalated"
    (integrate_story,) = [story for story in run.stories if story.card_id == "integrate"]
    assert integrate_story.status == "escalated"
    _assert_base_untouched(root, main_before)

    # A human finishes the merge the resolver left, where the detail says to.
    (worktree / SHARED).write_text(A_LINE + B_LINE, encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--no-edit")
    fake_resolver.reset()
    finished_tip = _git(root, "rev-parse", INTEGRATION_BRANCH).strip()

    second = run_milestone_cli(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    done = _envelope(second)
    assert done["done"] is True, done
    assert done["run_id"] != data["run_id"]
    assert done["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(worktree),
        "merged": [stories["A"], stories["B"]],
        "resolved": [],
    }
    assert not _merge_in_progress(worktree)
    assert _git(root, "rev-parse", INTEGRATION_BRANCH).strip() == finished_tip
    assert "resolve" not in _phases(read_fake_log(done["run_id"]))
    assert _load_run(root, done["run_id"]).status == "done"
    _assert_base_untouched(root, main_before)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py::test_the_resolver_env_var_is_the_conftest_twin "tests/e2e/test_integrate.py::test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete" -v`
Expected: the pin FAILS with `AssertionError: tests/e2e/conftest.py defines no FAKE_RESOLVER_ENV`, and the scenario ERRORS with `fixture 'fake_resolver' not found`.

- [ ] **Step 4: Add the twin and the fixture**

In `tests/e2e/conftest.py`, directly after `FAKE_IMPLEMENT_EDITS_MARKER` and its docstring, add:

```python
FAKE_RESOLVER_ENV = "FAKE_CLAUDE_RESOLVER"
"""Must equal `fake_claude.RESOLVER_ENV`, which `test_fake_claude.py` pins."""
```

Directly after the `rendezvous` fixture (after line 324), add:

```python
@dataclass
class FakeResolver:
    """Switches the fake's resolve phase between resolving and refusing for one test.

    The env var goes through the test's own function-scoped `monkeypatch`, so it
    is undone when the test ends. Child processes inherit it: `run_direct`
    calls `Popen` with no `env=` (`harness/launcher.py:132`).
    """

    monkeypatch: pytest.MonkeyPatch

    def refuse(self) -> None:
        self.monkeypatch.setenv(FAKE_RESOLVER_ENV, "refuse")

    def reset(self) -> None:
        self.monkeypatch.delenv(FAKE_RESOLVER_ENV, raising=False)


@pytest.fixture
def fake_resolver(monkeypatch) -> FakeResolver:
    """The test's resolver switch, starting in resolve mode."""
    resolver = FakeResolver(monkeypatch=monkeypatch)
    resolver.reset()
    return resolver
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py tests/e2e/test_integrate.py -v`
Expected: PASS for both files. If the relaunch does not complete, or it re-dispatches a resolver, that is a product finding: stop and report it. Do not edit `src/`.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_fake_claude.py tests/e2e/test_integrate.py
git commit -m "test(e2e): prove a lying resolver escalates at integrate and a human finish relaunches clean"
```

---

### Task 6: Relaunching an integrated milestone moves nothing

**Files:**
- Test: `tests/e2e/test_integrate.py`

**Interfaces:**
- Consumes: `_same_line_setup`, `INTEGRATION_BRANCH`, the Task 2 helpers, `two_story_board`, `run_milestone_cli`, `read_fake_log`.
- Produces: nothing new.

This task has no production or fixture change. It characterises product behaviour (addendum I6: "a finished milestone's relaunch is a no-op merge"), so its RED is the test not existing. If it fails when first run, that is a product finding: stop and report it rather than editing `src/`.

- [ ] **Step 1: Write the test**

Append to `tests/e2e/test_integrate.py`:

```python
def test_relaunching_an_integrated_milestone_moves_no_branch(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Scenario 5: after a resolved Integrate, a relaunch re-merges nothing."""
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    subtasks = two_story_board["subtasks"]
    branches = two_story_board["branches"]
    main_before = _same_line_setup(two_story_board)
    worktree = _integration_worktree(root)

    first = run_milestone_cli(root, milestone)

    assert first.exit_code == 0, (first.output, first.exception)
    first_data = _envelope(first)
    assert first_data["integrated"]["resolved"] == [stories["B"]]  # non-vacuity
    watched = [INTEGRATION_BRANCH, "main", branches[subtasks["A"][0]], branches[subtasks["B"][0]]]
    tips_before = {ref: _git(root, "rev-parse", ref).strip() for ref in watched}

    second = run_milestone_cli(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    data = _envelope(second)
    assert data["done"] is True, data
    assert data["run_id"] != first_data["run_id"]
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(worktree),
        "merged": [stories["A"], stories["B"]],
        "resolved": [],
    }
    assert {ref: _git(root, "rev-parse", ref).strip() for ref in watched} == tips_before
    assert "resolve" not in _phases(read_fake_log(data["run_id"]))
    assert not _merge_in_progress(worktree)
    assert _git(worktree, "status", "--porcelain") == ""
    _assert_base_untouched(root, main_before)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest "tests/e2e/test_integrate.py::test_relaunching_an_integrated_milestone_moves_no_branch" -v`
Expected: PASS. Before it was written the test did not exist, which is this task's RED. A failure here means product code re-merges or re-resolves on relaunch: stop and report it.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_integrate.py
git commit -m "test(e2e): prove relaunching an integrated milestone moves no branch"
```

---

### Task 7: A clean merge that breaks the suite

**Files:**
- Modify: `tests/e2e/conftest.py:366-394` (`run_milestone_cli`)
- Test: `tests/e2e/test_integrate.py`

**Interfaces:**
- Consumes: `VERIFY_COMMANDS`, `MILESTONE_PREFIX`, the Task 2 helpers.
- Produces: `run_milestone_cli(root, milestone, max_concurrent=None, verify=None)`. `verify` is a sequence of command strings, and `None` means `VERIFY_COMMANDS`. In `test_integrate.py`: `CALC_SOURCE`, `TEST_CALC_SOURCE`, `RENAMED_CALC_SOURCE`, `RENAMED_TEST_SOURCE`, `EXTRA_TEST_SOURCE`, `CHECK_SOURCE`, `CHECK_COMMAND`, `_check(cwd) -> subprocess.CompletedProcess`.

- [ ] **Step 1: Write the failing scenario**

In `tests/e2e/test_integrate.py`, extend the imports to:

```python
import json
import shlex
import subprocess
import sys
from pathlib import Path
```

Add below `B_LINE`:

```python
CALC_SOURCE = "def add(a, b):\n    return a + b\n"

TEST_CALC_SOURCE = (
    "from calc import add\n"
    "\n"
    "\n"
    "def test_add():\n"
    "    assert add(2, 3) == 5\n"
)

RENAMED_CALC_SOURCE = "def plus(a, b):\n    return a + b\n"
"""Story A renames `add` to `plus`..."""

RENAMED_TEST_SOURCE = (
    "from calc import plus\n"
    "\n"
    "\n"
    "def test_plus():\n"
    "    assert plus(2, 3) == 5\n"
)
"""...and updates the base test that used the old name."""

EXTRA_TEST_SOURCE = (
    "from calc import add\n"
    "\n"
    "\n"
    "def test_add_negative():\n"
    "    assert add(-1, 1) == 0\n"
)
"""Story B adds a test that imports the old name: green on base, red once A lands."""

CHECK_SOURCE = '''"""A stdlib-only suite runner: every test_* function in every test_*.py here."""
import importlib
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
failed = []
ran = 0
for path in sorted(ROOT.glob("test_*.py")):
    try:
        module = importlib.import_module(path.stem)
    except Exception as error:
        print(f"{path.name}: {type(error).__name__}: {error}", file=sys.stderr)
        failed.append(f"{path.name} ({type(error).__name__})")
        continue
    for name, test in sorted(vars(module).items()):
        if name.startswith("test_") and callable(test):
            ran += 1
            try:
                test()
            except Exception as error:
                print(f"{path.name}::{name}: {type(error).__name__}: {error}", file=sys.stderr)
                failed.append(f"{path.name}::{name} ({type(error).__name__})")
if failed:
    print("check.py: FAILED " + ", ".join(failed), file=sys.stderr)
    sys.exit(1)
if ran == 0:
    print("check.py: FAILED no test ran", file=sys.stderr)
    sys.exit(1)
print(f"check.py: {ran} passed")
'''
"""The repo's own suite. It does not depend on `pytest` being on the child's
`PATH`, and it writes no bytecode, so it never dirties a worktree."""

CHECK_COMMAND = shlex.join([sys.executable, "-B", "check.py"])
"""`verify.run_suite` splits with `shlex.split` and runs without a shell, so
the interpreter path is quoted."""
```

Add below `_same_line_setup`:

```python
def _check(cwd: Path) -> subprocess.CompletedProcess:
    """Run the repo's own suite in `cwd`, the way the verify phase does."""
    return subprocess.run(
        [sys.executable, "-B", "check.py"], cwd=cwd, capture_output=True, text=True
    )
```

Append to the module:

```python
def test_a_clean_merge_that_breaks_the_suite_escalates_at_integrate(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Scenario 4: each story is green alone; together the suite is red."""
    root = two_story_board["root"]
    subtasks = two_story_board["subtasks"]
    branches = two_story_board["branches"]
    main_before = _seed(
        root,
        {"calc.py": CALC_SOURCE, "test_calc.py": TEST_CALC_SOURCE, "check.py": CHECK_SOURCE},
    )
    assert _check(root).returncode == 0  # the base is green
    _write_edits(
        two_story_board,
        {
            "A": {"calc.py": RENAMED_CALC_SOURCE, "test_calc.py": RENAMED_TEST_SOURCE},
            "B": {"test_calc_extra.py": EXTRA_TEST_SOURCE},
        },
    )

    result = run_milestone_cli(root, two_story_board["milestone"], verify=[CHECK_COMMAND])

    assert result.exit_code == cli.EXIT_ESCALATED, (result.output, result.exception)
    data = _envelope(result)
    assert data["escalated"] is True, data
    assert data["phase"] == "integrate"
    assert data["story"] is None  # the final verification, not a tip
    assert data["files"] == []
    assert "check.py" in data["detail"]
    assert "test_calc_extra.py" in data["detail"]
    assert "integrated" not in data

    worktree = _integration_worktree(root)
    assert not _merge_in_progress(worktree)
    for key in ("A", "B"):
        branch = branches[subtasks[key][0]]
        assert _is_ancestor(root, branch, INTEGRATION_BRANCH), key
        # Each story passed its own verify phase, and is still green alone.
        assert _check(cli.worktree_for(root, branch)).returncode == 0, key
    red = _check(worktree)
    assert red.returncode != 0
    assert "test_calc_extra.py" in red.stderr

    # The merge was textually clean, so no resolver was ever dispatched.
    assert "resolve" not in _phases(read_fake_log(data["run_id"]))
    assert _load_run(root, data["run_id"]).status == "escalated"
    _assert_base_untouched(root, main_before)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest "tests/e2e/test_integrate.py::test_a_clean_merge_that_breaks_the_suite_escalates_at_integrate" -v`
Expected: FAIL with `TypeError: ... invoke() got an unexpected keyword argument 'verify'`.

- [ ] **Step 3: Add the `verify` argument to `run_milestone_cli`**

In `tests/e2e/conftest.py`, replace the inner `invoke` of `run_milestone_cli` (lines 375-392) with:

```python
    def invoke(
        root: Path,
        milestone: str,
        max_concurrent: int | None = None,
        verify: Sequence[str] | None = None,
    ):
        argv = [
            "run",
            "--milestone",
            milestone,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            MILESTONE_PREFIX,
        ]
        # `None` keeps existing callers' argv byte-identical.
        commands = VERIFY_COMMANDS if verify is None else tuple(verify)
        for command in commands:
            argv += ["--verify", command]
        # Only when asked: existing callers keep their exact argv.
        if max_concurrent is not None:
            argv += ["--max-concurrent", str(max_concurrent)]
        return runner.invoke(cli.app, argv)
```

`Sequence` is already imported from `collections.abc` at the top of `conftest.py`.

- [ ] **Step 4: Run it to verify it passes, with the other callers**

Run: `uv run pytest tests/e2e/test_integrate.py tests/e2e/test_milestone_run.py tests/e2e/test_parallel_milestone.py -v`
Expected: PASS for all three modules. The two existing modules show that the default argv did not change. If scenario 4 escalates somewhere other than `integrate` (for example a story's own verify phase fails), check `CHECK_COMMAND` and the seeded files first. A product-side failure should be reported, not patched.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_integrate.py
git commit -m "test(e2e): prove a clean merge that breaks the suite escalates at integrate"
```

---

### Task 8: Nothing left armed, and the whole suite

**Files:**
- Test: `tests/e2e/test_integrate.py`

**Interfaces:**
- Consumes: the `fake_resolver` fixture's use of `monkeypatch` (Task 5).
- Produces: nothing new.

- [ ] **Step 1: Write the guard**

In `tests/e2e/test_integrate.py`, add `import os` to the imports (after `import json`). Append this as the **last** test in the module:

```python
def test_no_resolver_mode_is_left_armed_for_later_tests():
    """Review focus: scenario 2 arms `FAKE_CLAUDE_RESOLVER` through the
    function-scoped `monkeypatch`; it must be gone once that test ends, or every
    later resolve in the session would refuse. Kept last in the module."""
    assert "FAKE_CLAUDE_RESOLVER" not in os.environ
```

- [ ] **Step 2: Run the module**

Run: `uv run pytest tests/e2e/test_integrate.py -v`
Expected: PASS for all seven tests (the unmarked guard, scenarios 1-5, and this guard).

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest`
Expected: PASS with no failures. The `e2e`-marked real-harness modules are deselected by `addopts`, and `tests/e2e/test_integrate.py` is collected and run.

- [ ] **Step 4: Confirm nothing under `src/` changed**

Run: `git diff --stat m5/task-run-integrate-at-the-a74f2cd6 -- src/`
Expected: empty output.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_integrate.py
git commit -m "test(e2e): guard that no resolver mode is left armed after the Integrate scenarios"
```
