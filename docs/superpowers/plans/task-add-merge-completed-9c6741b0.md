<!-- task-pipeline: validated -->
# Subtask 9c6741b0: `merge_completed_gate`, where git judges a merge

Parent story 9b04dd11 ("The merge step: merge a tip, and judge a merge with git"), milestone db5b5a3b. This narrows decision I3 of `docs/superpowers/specs/2026-09-25-integrate-design.md` (lines 60-73) to one deterministic gate. The resolver's `resolved` flag is advisory, and git decides whether the merge is complete.

## Base

`src/agent_manager/steps/integrate.py` and `tests/steps/test_integrate.py` come from sibling ce288496 (branch `m5/task-merge-one-story-tip-ce288496`). This worktree already contains them. Add the new functions to that file and reuse its helpers (`GitError`, `GitRunner`, `run_git`, `_first_line`, and the `rev-parse --verify --quiet MERGE_HEAD` probe pattern). Do not recreate or change `merge_tip`, `MergeInProgressError`, `_refuse_unfinished_merge` or any other sibling behaviour.

## Scope

1. `measure_merge(worktree, git_runner=run_git)` in `steps/integrate.py`. It is read-only and returns a plain dict (internal state, so no Pydantic) with every key always present:
   - `merge_in_progress: bool`: whether `MERGE_HEAD` exists. The probe is `["-C", worktree, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"]`. Exit 1 means the ref is absent. Any other `GitError` propagates.
   - `status: str`: the raw `git status --porcelain` output. Untracked files count as dirty.
   - `marked_files: list[str]`: the files the merge touched that still hold conflict markers, as worktree-relative paths in git's order.
2. The touched set is the files that differ from the first parent. While `MERGE_HEAD` exists, the first parent is `HEAD`, so the set is the files that differ between `HEAD` and the working tree/index, including unmerged paths. Once the merge is committed, it is the files that differ between `HEAD^1` and `HEAD`. If `HEAD` has no parent, the touched set is empty. Files outside the touched set are never read. A touched path that no longer exists on disk (a deletion) is skipped. Files are read as bytes, so binary or non-UTF-8 content cannot crash the scan. Paths containing spaces or unusual characters must be read correctly.
3. A file is marked only if it has BOTH a line starting with `<<<<<<< ` (seven characters and a space) and a line starting with `>>>>>>> `. A lone `=======` line, such as a markdown or rst underline, never counts.
4. `merge_completed_gate(result, worktree, *, git_runner=run_git)`. It follows the gate contract (engine.py:291-317) and never consults `result` (not even `resolved`). It returns `None` only when three things hold: no merge is in progress, `status` is empty, and `marked_files` is empty. Otherwise it returns `{"detail": <str>}`, and only that key, because `_render_verdict` prints `key=value`. The detail names every failing condition present, in this order:
   - that a merge is still in progress, naming `MERGE_HEAD`, and that it must be committed;
   - the files that still hold conflict markers;
   - that the working tree is not clean, with the dirty paths taken from `status`.

   The detail is written as direct feedback for the resolver, because it is appended to the resolver's retry brief. For example: "The merge is not complete: a merge is still in progress (MERGE_HEAD exists); commit it. Conflict markers remain in: a.txt. The working tree is not clean: ?? scratch.txt." `git_runner` stays keyword-only with a default, so `bind_arguments` never requires it.
5. `workflow/registry.py` registers the gate as `"merge_completed_gate"`. The name is bare, like every other gate. It is registered to the real imported callable, not a wrapper, so `resolve("merge_completed_gate") is integrate.merge_completed_gate`. The name goes into `BUILTIN_FUNCTION_NAMES` in sorted position (after `implement_blocked_gate`). The docstrings of the tuple and of `default_registry()` must be reworded. Today they say "every name in `builtin/task.yaml`". They should say "every name the builtin workflow documents use: `task.yaml`, plus the integrate-only names the forthcoming `integrate.yaml` will reference". `builtin/task.yaml` itself is not edited.

## Drift checks that must be updated (not deleted)

These tests currently pin the registry to exactly the task.yaml names. Each one keeps its drift protection, with the one integrate-only name made explicit:
- `tests/workflow/test_registry.py:103-105, 251`: keep `TASK_YAML_NAMES` unchanged, add `INTEGRATE_ONLY_NAMES = ("merge_completed_gate",)`, and assert that the default registry and `BUILTIN_FUNCTION_NAMES` equal the sorted union of the two.
- `tests/workflow/test_builtin_task.py:249-254`: the functions task.yaml resolves equal the registry names minus the integrate-only names, and each one still `is` the registry binding.
- `tests/test_engine.py:1163-1179`: add `"merge_completed_gate"` to the fake function map as a stand-in that raises if called, because task.yaml never references it.

## Error paths

- A `MERGE_HEAD` probe that fails with any exit code other than 1 raises `GitError`. Any other git failure while measuring also propagates. The gate never turns a git failure into a pass or into a verdict.
- The gate and `measure_merge` never run `commit`, `merge`, `merge --abort`, `reset`, `checkout`, `clean`, `add` or `push`. They never write the base branch or any other ref.

## Out of scope

The `resolver` role, `integrate.yaml`, the synthetic Integrate story and subtasks, the final verification, the report and status (I4-I7), and `merge_tip` and anything else ce288496 owns. Also out of scope: --no-integrate, milestone-aware `am resume`, watch/retry/cancel, cost capture, and anything under `tests/e2e`.

## Tests

The placement rule is design spec §14 (lines 495-510): tiers follow the module's nature. `steps/integrate.py` is a step, so its tests are **Steps tier**. They live in `tests/steps/test_integrate.py`, use REAL temp git repos with no mocks and no network, and reuse that file's `requires_git`, `_git`, `_commit`, `repo`/`wt` fixtures, `_make_tip`, `_leave_a_conflict`, `_base_state` and `_recorder`. Binding is **Engine tier**, and the registry entries are tested in the **workflow registry tests**. Nothing goes in `tests/e2e`. The whole default suite (`uv run pytest`, including tests/e2e) must stay green.

Steps tier (`tests/steps/test_integrate.py`). Each test asserts that `_base_state(repo)` is unchanged and records its git calls to show that no forbidden operation ran:
1. A finished, committed merge (a clean `merge_tip`) passes: the gate returns `None`.
2. A merge still in progress (`_leave_a_conflict`) fails, and the detail contains `MERGE_HEAD` and the conflicting file.
3. A committed merge with an extra untracked or modified file fails, and the detail names that path.
4. A resolution committed with a file still holding both `<<<<<<< ` and `>>>>>>> ` lines fails, and the detail names that file.
5. A committed merge whose touched file contains only a `=======` underline passes.
6. `result={"resolved": True, ...}` on an unfinished merge still fails exactly as in test 2.
7. A marker-bearing file that the merge did not touch (committed on the base before the tip) is ignored, and the gate passes.
8. A `MERGE_HEAD` probe that fails with an exit code other than 1 (a wrapping git runner that raises `GitError(exit_code=128)` for that argv) propagates `GitError`.
9. `measure_merge` returns all three keys with the expected values for both the in-progress state and the finished state.

Engine tier (`tests/test_engine.py`):
10. `bind_arguments(integrate.merge_completed_gate, values)` works when `values` has the shape `_gate_values` builds: context keys including `worktree`, plus `result` and the phase name. It binds exactly `result` and `worktree`, never `git_runner`, and raises no EngineError.

Workflow registry tier (`tests/workflow/test_registry.py`):
11. `default_registry().resolve("merge_completed_gate") is integrate.merge_completed_gate`. The updated drift checks above pass.

---

# `merge_completed_gate` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `measure_merge` and `merge_completed_gate` to `steps/integrate.py` so git, not the resolver's `resolved` flag, decides whether an integration merge is complete, and register the gate as `"merge_completed_gate"`.

**Architecture:** `measure_merge` asks git three read-only questions (is `MERGE_HEAD` set, what does `status --porcelain` say, which files differ from the first parent) and scans only those touched files, as bytes, for a `<<<<<<< ` line plus a `>>>>>>> ` line. `merge_completed_gate` turns that measurement into the engine's gate contract: `None` to pass, `{"detail": ...}` to fail, with every git failure propagating. The registry binds the real callable, and the three drift checks that pinned the registry to `task.yaml` gain an explicit `INTEGRATE_ONLY_NAMES` tuple.

**Tech Stack:** Python 3, pytest, git CLI through the existing `run_git` / `GitRunner` seam (argv lists, no shell).

**Spec:** `docs/superpowers/specs/task-add-merge-completed-9c6741b0-design.md` (reproduced verbatim above).

## Global Constraints

- Work on branch `m5/task-add-merge-completed-9c6741b0` in worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-add-merge-completed-9c6741b0`, cut from `m5/task-merge-one-story-tip-ce288496`. `steps/integrate.py` and `tests/steps/test_integrate.py` already exist there; no other subtask's code is assumed.
- Do not recreate or change `merge_tip`, `MergeInProgressError`, `_refuse_unfinished_merge` or any other existing function in `steps/integrate.py`. Only append.
- `measure_merge` returns a plain dict with exactly the keys `merge_in_progress`, `status`, `marked_files` (no Pydantic: internal state).
- `merge_completed_gate(result, worktree, *, git_runner=run_git)`: `git_runner` keyword-only with a default; `result` is never consulted.
- A verdict is `{"detail": <str>}` and nothing else.
- Marker rule: a line starting `<<<<<<< ` AND a line starting `>>>>>>> ` (seven characters and a space). A lone `=======` never counts.
- `MERGE_HEAD` probe argv: `["-C", worktree, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"]`. Exit 1 = absent; any other `GitError` propagates.
- Never run `commit`, `merge`, `merge --abort`, `reset`, `checkout`, `clean`, `add` or `push`; never write any ref. Tests assert `_base_state(repo)` is unchanged.
- Registry name is the bare `"merge_completed_gate"`, bound to the real `integrate.merge_completed_gate`, inserted in `BUILTIN_FUNCTION_NAMES` after `implement_blocked_gate`. `builtin/task.yaml` is not edited.
- Test tiers (design spec §14): Steps tier in `tests/steps/test_integrate.py` against real temp git repos; Engine tier in `tests/test_engine.py`; registry in `tests/workflow/test_registry.py`. Nothing under `tests/e2e`.
- Verification: `uv run pytest` (whole default suite, including `tests/e2e`) must be green. There is no lint or typecheck command.

## Review Focus

- A touched path with spaces or non-ASCII characters (`my notes.md`, `café.md`): git quotes such names in plain `--name-only` output, so a naive parse would open a non-existent file and silently pass. Expect the exact name in `marked_files`; covered by a `-z` parse and a parametrized test in Task 1.
- A touched binary or non-UTF-8 file: a text read would crash with `UnicodeDecodeError`. Expect a byte scan that still finds both markers; test in Task 1.
- A touched file the resolver deleted: reading it would raise `FileNotFoundError`. Expect it to be skipped; test in Task 1.
- A `HEAD` with no parent (root commit): `HEAD^1` does not exist. Expect an empty touched set rather than an error; test in Task 1.
- A git failure on any call other than the `MERGE_HEAD` probe, even one that exits 1 (`status`, `diff`), or a worktree path that is not a repository: must propagate, never read as "absent" or "clean"; tests in Task 2.

---

## File Structure

- Modify `src/agent_manager/steps/integrate.py`: append `measure_merge` and its private helpers (Task 1), then `merge_completed_gate` (Task 2); extend the module docstring.
- Modify `tests/steps/test_integrate.py`: import the two new functions; append Steps-tier tests (Tasks 1 and 2).
- Modify `tests/test_engine.py`: import `integrate`; add one Engine-tier binding test (Task 2); add the stand-in to the fake function map (Task 3).
- Modify `src/agent_manager/workflow/registry.py`: import `integrate`, register the gate, add the name, reword two docstrings (Task 3).
- Modify `tests/workflow/test_registry.py` and `tests/workflow/test_builtin_task.py`: drift checks with `INTEGRATE_ONLY_NAMES`, plus the resolve test (Task 3).

---

### Task 1: `measure_merge` (Steps tier)

**Files:**
- Modify: `src/agent_manager/steps/integrate.py` (docstring lines 1-22; append after line 191)
- Test: `tests/steps/test_integrate.py` (import at line 19; append at end of file, after line 642)

**Interfaces:**
- Consumes: `GitError`, `GitRunner`, `run_git` (already imported in `integrate.py` from `agent_manager.steps.worktree`); test helpers `requires_git`, `_git`, `_commit`, `repo`, `wt`, `_make_tip`, `_merge`, `_leave_a_conflict`, `_base_state`, `_recorder`, `_assert_no_forbidden_git` (all already in `tests/steps/test_integrate.py`).
- Produces: `measure_merge(worktree: str | Path, git_runner: GitRunner = run_git) -> dict[str, object]` returning `{"merge_in_progress": bool, "status": str, "marked_files": list[str]}`. Test helpers `MARKED`, `_assert_read_only_git(calls, wt)`, `_porcelain(wt)`, `_merged_cleanly(repo, wt, tmp_path, files)` used again by Task 2.

- [ ] **Step 1: Update the test import**

In `tests/steps/test_integrate.py`, replace line 19:

```python
from agent_manager.steps.integrate import MergeInProgressError, merge_tip
```

with:

```python
from agent_manager.steps.integrate import (
    MergeInProgressError,
    measure_merge,
    merge_completed_gate,
    merge_tip,
)
```

(`merge_completed_gate` is imported now so Task 2 only appends tests; it is added in Task 2, so until then the module fails to import. That is the RED state for both tasks.)

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/steps/test_integrate.py`:

```python
# --- measure_merge / merge_completed_gate (card 9c6741b0, Integrate addendum I3) ---
#
# git judges whether a merge is complete. Every test below measures a real temp
# repo, records the git calls the measurement made, and asserts the base is
# untouched.

MARKED = "<<<<<<< ours\nkept\n=======\ntheirs\n>>>>>>> m5/story-b\n"
"""A file body holding both conflict-marker lines."""

READ_ONLY_SUBCOMMANDS = ("rev-parse", "status", "diff")


def _assert_read_only_git(calls: list[list[str]], wt: Path) -> None:
    """Measuring a merge only asks git questions, and only about `wt`."""
    assert calls, "git must be asked: nothing here may be judged without it"
    for argv in calls:
        assert argv[:2] == ["-C", str(wt)], argv
        assert argv[2] in READ_ONLY_SUBCOMMANDS, f"not a read-only git call: {argv!r}"
    _assert_no_forbidden_git(calls)


def _porcelain(wt: Path) -> str:
    return _git(wt, "status", "--porcelain", "--untracked-files=all")


def _merged_cleanly(repo: Path, wt: Path, tmp_path: Path, files: dict[str, str]) -> None:
    """Merge a story-a tip carrying `files` into a fresh integration worktree."""
    _make_tip(repo, tmp_path, "m5/story-a", files)
    result = _merge(repo, wt, "m5/story-a")
    assert result["conflict"] is False
    assert result["already_merged"] is False


@requires_git
def test_measure_merge_reports_a_merge_left_in_progress(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)

    calls: list[list[str]] = []
    measured = measure_merge(str(wt), git_runner=_recorder(calls))

    assert measured == {
        "merge_in_progress": True,
        "status": _porcelain(wt),
        "marked_files": ["a.js"],
    }
    assert "a.js" in measured["status"]
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_measure_merge_reports_a_finished_merge(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    calls: list[list[str]] = []
    # A Path is accepted as well as a str: the engine's context holds a Path.
    measured = measure_merge(wt, git_runner=_recorder(calls))

    assert measured == {"merge_in_progress": False, "status": "", "marked_files": []}
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@pytest.mark.parametrize(
    ("body", "marked"),
    [
        (MARKED, True),
        ("<<<<<<< ours\r\nkept\r\n>>>>>>> theirs\r\n", True),
        ("Title\n=======\n\nBody text.\n", False),
        ("<<<<<<< only the opening marker\n", False),
        (">>>>>>> only the closing marker\n", False),
        ("text <<<<<<< mid-line\ntext >>>>>>> mid-line\n", False),
        ("<<<<<<<no-space\n>>>>>>>no-space\n", False),
    ],
    ids=[
        "both-markers",
        "both-markers-crlf",
        "markdown-underline-only",
        "opening-only",
        "closing-only",
        "markers-mid-line",
        "markers-without-the-space",
    ],
)
@requires_git
def test_a_touched_file_is_marked_only_with_both_marker_lines(
    repo: Path, wt: Path, tmp_path: Path, body: str, marked: bool
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"notes.md": body})

    measured = measure_merge(str(wt))

    assert measured["marked_files"] == (["notes.md"] if marked else [])
    assert measured["merge_in_progress"] is False
    assert measured["status"] == ""
    assert _base_state(repo) == before


@requires_git
def test_markers_in_a_file_the_merge_did_not_touch_are_ignored(
    repo: Path, wt: Path, tmp_path: Path
):
    # Committed on the base before the tip: every merge carries it, none touched it.
    _commit(repo, "legacy.md", MARKED)
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})
    assert (wt / "legacy.md").read_text() == MARKED  # non-vacuity

    assert measure_merge(str(wt))["marked_files"] == []
    assert _base_state(repo) == before


@pytest.mark.parametrize("name", ["my notes.md", "café.md", 'say "hi".md'])
@requires_git
def test_a_touched_file_with_an_unusual_name_is_read_by_its_real_name(
    repo: Path, wt: Path, tmp_path: Path, name: str
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {name: MARKED})

    assert measure_merge(str(wt))["marked_files"] == [name]
    assert _base_state(repo) == before


@requires_git
def test_a_touched_binary_file_is_scanned_as_bytes(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})
    # Not valid UTF-8: a text read would raise UnicodeDecodeError.
    (wt / "blob.bin").write_bytes(b"<<<<<<< \xff\xfe\n\x00\x80\n>>>>>>> \xfe\n")
    _git(wt, "add", "blob.bin")
    _git(wt, "commit", "-m", "add a binary")

    assert measure_merge(str(wt)) == {
        "merge_in_progress": False,
        "status": "",
        "marked_files": ["blob.bin"],
    }
    assert _base_state(repo) == before


@requires_git
def test_a_touched_file_that_was_deleted_is_skipped(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})
    _git(wt, "rm", "-q", "b.js")
    _git(wt, "commit", "-m", "drop b.js")

    assert measure_merge(str(wt)) == {
        "merge_in_progress": False,
        "status": "",
        "marked_files": [],
    }
    assert _base_state(repo) == before


@requires_git
def test_a_head_with_no_parent_has_nothing_touched(tmp_path: Path):
    solo = tmp_path / "solo"
    subprocess.run(
        ["git", "init", "-b", "main", str(solo)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(solo, "config", "user.email", "tests@example.com")
    _git(solo, "config", "user.name", "agent-manager tests")
    _git(solo, "config", "commit.gpgsign", "false")
    _commit(solo, "notes.md", MARKED)

    assert measure_merge(str(solo)) == {
        "merge_in_progress": False,
        "status": "",
        "marked_files": [],
    }
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_integrate.py -v`
Expected: FAIL — collection error `ImportError: cannot import name 'measure_merge' from 'agent_manager.steps.integrate'`.

- [ ] **Step 4: Write the implementation**

In `src/agent_manager/steps/integrate.py`, replace the module docstring's last paragraph (lines 20-22):

```python
Every invocation is an argument list handed to the git runner: there is no
shell string and nothing to quote.
"""
```

with:

```python
`measure_merge` and `merge_completed_gate` (Integrate addendum I3) judge a
merge the resolver says it finished. They only ask git questions -- `rev-parse`,
`status`, `diff` -- and read the touched files as bytes; the resolver's own
`resolved` flag is never consulted.

Every invocation is an argument list handed to the git runner: there is no
shell string and nothing to quote.
"""
```

Then append to the end of the file (after `merge_tip`, line 191):

```python


_MARKER_OPEN = b"<<<<<<< "
_MARKER_CLOSE = b">>>>>>> "
"""Conflict-marker line prefixes: seven characters and a space. `=======` alone
is a markdown or rst underline as often as a marker, so it never counts."""


def _ref_exists(git_runner: GitRunner, worktree_path: str, ref: str) -> bool:
    """Whether `ref` resolves in `worktree_path`.

    `rev-parse --verify --quiet` exits 1 when the ref is absent; any other
    failure is not an answer and propagates.
    """
    try:
        git_runner(["-C", worktree_path, "rev-parse", "--verify", "--quiet", ref])
    except GitError as error:
        if error.exit_code == 1:
            return False
        raise
    return True


def _nul_separated(output: str) -> list[str]:
    """Paths from a `-z` listing: NUL-separated and never quoted by git."""
    return [name for name in output.split("\0") if name]


def _touched_files(
    git_runner: GitRunner, worktree_path: str, merge_in_progress: bool
) -> list[str]:
    """The files the merge touched: those that differ from its first parent.

    While MERGE_HEAD exists the first parent is HEAD itself, so the set is
    what differs between HEAD and the index/working tree, plus any unmerged
    path. Once committed it is HEAD^1..HEAD. A root commit touched nothing.
    """
    if merge_in_progress:
        touched = _nul_separated(
            git_runner(["-C", worktree_path, "diff", "--name-only", "-z", "HEAD"])
        )
        unmerged = _nul_separated(
            git_runner(
                ["-C", worktree_path, "diff", "--name-only", "-z", "--diff-filter=U"]
            )
        )
        return touched + [name for name in unmerged if name not in touched]
    if not _ref_exists(git_runner, worktree_path, "HEAD^1"):
        return []
    return _nul_separated(
        git_runner(["-C", worktree_path, "diff", "--name-only", "-z", "HEAD^1", "HEAD"])
    )


def _has_conflict_markers(path: Path) -> bool:
    """Both an opening and a closing marker line, read as bytes."""
    lines = path.read_bytes().splitlines()
    return any(line.startswith(_MARKER_OPEN) for line in lines) and any(
        line.startswith(_MARKER_CLOSE) for line in lines
    )


def measure_merge(
    worktree: str | Path, git_runner: GitRunner = run_git
) -> dict[str, object]:
    """What git says about the merge in `worktree`, without changing anything.

    `merge_in_progress` is whether MERGE_HEAD exists, `status` is the raw
    `git status --porcelain` text (untracked files included), and
    `marked_files` lists the touched files that still hold both conflict
    marker lines, in git's order. A touched path that no longer exists (a
    deletion) is skipped. Every git failure other than an absent ref raises.
    """
    worktree_path = str(worktree)
    in_progress = _ref_exists(git_runner, worktree_path, "MERGE_HEAD")
    status = git_runner(
        ["-C", worktree_path, "status", "--porcelain", "--untracked-files=all"]
    )
    marked = [
        name
        for name in _touched_files(git_runner, worktree_path, in_progress)
        if (Path(worktree_path) / name).is_file()
        and _has_conflict_markers(Path(worktree_path) / name)
    ]
    return {"merge_in_progress": in_progress, "status": status, "marked_files": marked}


def merge_completed_gate(*args: object, **kwargs: object) -> None:
    """Placeholder until Task 2: keeps the test module importable."""
    raise NotImplementedError("merge_completed_gate lands in Task 2")
```

(The placeholder `merge_completed_gate` exists only so the Step 1 import resolves; Task 2 replaces it with the real gate in its first implementation step.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_integrate.py -v`
Expected: PASS — every existing `merge_tip` test plus the 16 new `measure_merge` test cases (2 + 7 parametrized + 1 + 3 parametrized + 1 + 1 + 1).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/integrate.py tests/steps/test_integrate.py
git commit -m "feat(integrate): measure a merge with git: MERGE_HEAD, status, marked files"
```

---

### Task 2: `merge_completed_gate` (Steps tier) and its binding (Engine tier)

**Files:**
- Modify: `src/agent_manager/steps/integrate.py` (replace the Task 1 placeholder at the end of the file)
- Test: `tests/steps/test_integrate.py` (append at end of file)
- Test: `tests/test_engine.py` (import at line 21; new test after `test_bind_arguments_refuses_a_positional_only_parameter`, which ends at line 200)

**Interfaces:**
- Consumes: `measure_merge(worktree, git_runner)` from Task 1; test helpers `MARKED`, `_assert_read_only_git`, `_merged_cleanly` from Task 1; `engine.bind_arguments`, `engine._gate_values`, `engine.subtask_context`, and `_subtask()` / `REPO` in `tests/test_engine.py`.
- Produces: `merge_completed_gate(result: object, worktree: str | Path, *, git_runner: GitRunner = run_git) -> dict[str, str] | None`. Task 3 registers this exact object.

- [ ] **Step 1: Write the failing Steps-tier tests**

Append to the end of `tests/steps/test_integrate.py`:

```python
RESOLVED = {"resolved": True, "files": ["a.js"], "summary": "kept both sides"}
"""What a resolver that claims success returns. The gate must not believe it."""


@requires_git
def test_the_gate_passes_a_finished_merge(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is None
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_fails_a_merge_still_in_progress(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    tip_b = _leave_a_conflict(repo, wt, tmp_path)
    head = _head(wt)

    calls: list[list[str]] = []
    verdict = merge_completed_gate(None, str(wt), git_runner=_recorder(calls))

    assert verdict is not None
    assert set(verdict) == {"detail"}
    detail = verdict["detail"]
    assert "MERGE_HEAD" in detail
    assert "commit" in detail
    assert "a.js" in detail
    # Judging changed nothing: the merge is still exactly where it was.
    assert _merge_head(wt) == tip_b
    assert _head(wt) == head
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@pytest.mark.parametrize("path", ["scratch.txt", "b.js"], ids=["untracked", "modified"])
@requires_git
def test_the_gate_fails_a_finished_merge_with_a_dirty_tree(
    repo: Path, wt: Path, tmp_path: Path, path: str
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})
    (wt / path).write_text("left behind by the resolver\n")

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is not None
    assert set(verdict) == {"detail"}
    assert "not clean" in verdict["detail"]
    assert path in verdict["detail"]
    assert "MERGE_HEAD" not in verdict["detail"]
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_fails_a_committed_resolution_that_kept_the_markers(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)
    assert "<<<<<<< " in (wt / "a.js").read_text()  # non-vacuity
    # A resolver that staged and committed the conflicted file as-is.
    _git(wt, "add", "a.js")
    _git(wt, "commit", "--no-edit")

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is not None
    assert set(verdict) == {"detail"}
    assert "Conflict markers remain in: a.js" in verdict["detail"]
    assert "MERGE_HEAD" not in verdict["detail"]
    assert "not clean" not in verdict["detail"]
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_passes_a_touched_file_with_only_an_underline(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"README.md": "Title\n=======\n\nBody.\n"})

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is None
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_a_resolver_claiming_resolved_does_not_pass_an_unfinished_merge(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)

    calls: list[list[str]] = []
    claimed = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))
    unclaimed = merge_completed_gate({"resolved": False}, str(wt))

    assert claimed is not None
    assert "MERGE_HEAD" in claimed["detail"]
    assert claimed == unclaimed
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_ignores_markers_in_a_file_the_merge_did_not_touch(
    repo: Path, wt: Path, tmp_path: Path
):
    _commit(repo, "legacy.md", MARKED)
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is None
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_names_every_failure_in_order(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)
    (wt / "scratch.txt").write_text("left behind\n")

    detail = merge_completed_gate(RESOLVED, str(wt))["detail"]

    assert detail.startswith("The merge is not complete: ")
    in_progress = detail.index("MERGE_HEAD")
    markers = detail.index("Conflict markers remain in: a.js")
    dirty = detail.index("The working tree is not clean: ")
    assert in_progress < markers < dirty
    assert "?? scratch.txt" in detail[dirty:]
    assert "a.js" in detail[dirty:]  # the unmerged path is dirty too
    assert "\n" not in detail
    assert _base_state(repo) == before


@requires_git
def test_a_merge_head_probe_failing_otherwise_propagates_from_the_gate(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    def runner(argv: list[str]) -> str:
        if "MERGE_HEAD" in argv:
            raise GitError("forced: the probe itself broke", argv=argv, exit_code=128)
        return run_git(argv)

    with pytest.raises(GitError) as excinfo:
        merge_completed_gate(RESOLVED, str(wt), git_runner=runner)

    assert excinfo.value.exit_code == 128
    assert "MERGE_HEAD" in excinfo.value.argv
    assert _base_state(repo) == before


@pytest.mark.parametrize(
    ("token", "exit_code"),
    [("status", 1), ("diff", 1), ("HEAD^1", 128)],
    ids=["status-exit-1", "diff-exit-1", "first-parent-probe-exit-128"],
)
@requires_git
def test_any_other_git_failure_propagates_from_the_gate(
    repo: Path, wt: Path, tmp_path: Path, token: str, exit_code: int
):
    # Exit 1 is "absent" only for a ref probe; anywhere else it is a failure.
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    def runner(argv: list[str]) -> str:
        if token in argv:
            raise GitError("forced failure", argv=argv, exit_code=exit_code)
        return run_git(argv)

    with pytest.raises(GitError) as excinfo:
        merge_completed_gate(RESOLVED, str(wt), git_runner=runner)

    assert token in excinfo.value.argv
    assert _base_state(repo) == before


@requires_git
def test_the_gate_raises_for_a_worktree_that_is_not_a_repository(tmp_path: Path):
    with pytest.raises(GitError) as excinfo:
        merge_completed_gate(RESOLVED, str(tmp_path / "not-a-repo"))

    assert excinfo.value.exit_code not in (None, 0, 1)
```

- [ ] **Step 2: Write the failing Engine-tier binding test**

In `tests/test_engine.py`, replace line 21:

```python
from agent_manager.steps import reducers
```

with:

```python
from agent_manager.steps import integrate, reducers
```

Then insert after `test_bind_arguments_refuses_a_positional_only_parameter` (after line 200):

```python


def test_bind_arguments_binds_the_merge_completed_gate_from_a_gate_table():
    """The gate takes `result` and `worktree` by name out of exactly the table
    `_gate_values` builds for a deterministic phase; `git_runner` is keyword-only
    with a default and must never be required or bound from the context."""
    result = {"resolved": True, "files": ["a.js"], "summary": "kept both sides"}
    context = engine.subtask_context(_subtask(), REPO, ["uv run pytest"])
    values = engine._gate_values(context, "merge", result)

    bound = engine.bind_arguments(
        integrate.merge_completed_gate,
        values,
        phase="merge",
        function="merge_completed_gate",
    )

    assert bound == {
        "result": result,
        "worktree": Path("/repo/.claude/worktrees/m1/task-ed77a917"),
    }
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_integrate.py tests/test_engine.py::test_bind_arguments_binds_the_merge_completed_gate_from_a_gate_table -v`
Expected: FAIL — the new gate tests fail with `NotImplementedError: merge_completed_gate lands in Task 2`; the binding test fails because the placeholder's `*args, **kwargs` signature binds `{}` instead of `result` and `worktree`. The Task 1 tests still pass.

- [ ] **Step 4: Write the implementation**

In `src/agent_manager/steps/integrate.py`, replace the Task 1 placeholder at the end of the file:

```python
def merge_completed_gate(*args: object, **kwargs: object) -> None:
    """Placeholder until Task 2: keeps the test module importable."""
    raise NotImplementedError("merge_completed_gate lands in Task 2")
```

with:

```python
def _dirty_paths(status: str) -> list[str]:
    """The `git status --porcelain` entries, one per dirty path, as git printed them."""
    return [line.strip() for line in status.split("\n") if line.strip()]


def merge_completed_gate(
    result: object, worktree: str | Path, *, git_runner: GitRunner = run_git
) -> dict[str, str] | None:
    """Pass (None) only when git says the merge in `worktree` is finished.

    Finished means: no MERGE_HEAD, a clean `git status --porcelain`, and no
    touched file holding both conflict-marker lines. `result` is the
    resolver's report and is deliberately never read -- its `resolved` flag is
    advisory; git judges. Otherwise the verdict is `{"detail": ...}`, written
    as feedback for the resolver because it is appended to its retry brief.
    Any git failure propagates: it is never a pass and never a verdict.
    """
    measured = measure_merge(worktree, git_runner)
    in_progress = bool(measured["merge_in_progress"])
    marked = list(measured["marked_files"])
    dirty = _dirty_paths(str(measured["status"]))
    if not in_progress and not marked and not dirty:
        return None

    lead = "The merge is not complete"
    if in_progress:
        sentences = [
            f"{lead}: a merge is still in progress (MERGE_HEAD exists); commit it."
        ]
    else:
        sentences = [f"{lead}."]
    if marked:
        sentences.append(f"Conflict markers remain in: {', '.join(marked)}.")
    if dirty:
        sentences.append(f"The working tree is not clean: {'; '.join(dirty)}.")
    return {"detail": " ".join(sentences)}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_integrate.py tests/test_engine.py -v`
Expected: PASS — all Task 1 and Task 2 Steps-tier tests, the new binding test, and every existing engine test.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/integrate.py tests/steps/test_integrate.py tests/test_engine.py
git commit -m "feat(integrate): add merge_completed_gate, judged by git not the resolver"
```

---

### Task 3: Register `merge_completed_gate` and keep the drift checks honest (workflow registry tier)

**Files:**
- Modify: `src/agent_manager/workflow/registry.py` (import line 19; `BUILTIN_FUNCTION_NAMES` and its docstring lines 210-228; `default_registry` docstring lines 232-245; registrations after line 256)
- Test: `tests/workflow/test_registry.py` (import line 10; lines 83-105; line 251; new test after line 120)
- Test: `tests/workflow/test_builtin_task.py` (lines 249-254, plus a module constant after line 58)
- Test: `tests/test_engine.py` (fake function map, lines 1160-1179)

**Interfaces:**
- Consumes: `integrate.merge_completed_gate` from Task 2.
- Produces: `default_registry().resolve("merge_completed_gate") is integrate.merge_completed_gate`; `"merge_completed_gate"` in `BUILTIN_FUNCTION_NAMES` between `"implement_blocked_gate"` and `"plan_check.find_validated_plan"`.

- [ ] **Step 1: Write the failing registry tests**

In `tests/workflow/test_registry.py`, replace line 10:

```python
from agent_manager.steps import docs_commit, plan_check, reducers, rollup, verify, worktree
```

with:

```python
from agent_manager.steps import (
    docs_commit,
    integrate,
    plan_check,
    reducers,
    rollup,
    verify,
    worktree,
)
```

Replace lines 102-105:

```python


def test_default_registry_holds_exactly_the_names_task_yaml_uses() -> None:
    assert default_registry().names() == TASK_YAML_NAMES
    assert BUILTIN_FUNCTION_NAMES == TASK_YAML_NAMES
```

with:

```python

# Names the forthcoming builtin/integrate.yaml references and task.yaml never
# does (Integrate addendum I3). Kept explicit so the drift check still pins the
# registry to exactly what the builtin documents use.
INTEGRATE_ONLY_NAMES = ("merge_completed_gate",)

BUILTIN_NAMES = tuple(sorted(TASK_YAML_NAMES + INTEGRATE_ONLY_NAMES))


def test_default_registry_holds_exactly_the_names_the_builtin_documents_use() -> None:
    assert not set(TASK_YAML_NAMES) & set(INTEGRATE_ONLY_NAMES)
    assert default_registry().names() == BUILTIN_NAMES
    assert BUILTIN_FUNCTION_NAMES == BUILTIN_NAMES
```

After `test_default_registry_resolves_the_ported_reducers_to_the_real_callables` (ends at line 120), insert:

```python


def test_default_registry_resolves_the_merge_gate_to_the_real_callable() -> None:
    """Not a wrapper: `bind_arguments` reads the real signature, and a fix to
    the gate reaches the engine without touching this table."""
    registry = default_registry()
    assert registry.resolve("merge_completed_gate") is integrate.merge_completed_gate
    assert default_registry().resolve("merge_completed_gate") is registry.resolve(
        "merge_completed_gate"
    )
```

In `test_default_registry_returns_an_independent_registry_each_call`, replace line 251:

```python
    assert second.names() == TASK_YAML_NAMES
```

with:

```python
    assert second.names() == BUILTIN_NAMES
```

- [ ] **Step 2: Update the builtin-task drift check**

In `tests/workflow/test_builtin_task.py`, after the `EXPECTED_PHASES` tuple (ends at line 58), insert:

```python

# Registered for the forthcoming builtin/integrate.yaml; task.yaml never names
# them (Integrate addendum I3).
INTEGRATE_ONLY_NAMES = ("merge_completed_gate",)
```

Replace lines 249-254:

```python
def test_every_resolved_function_is_the_registry_binding() -> None:
    workflow = load_builtin("task")
    registry = default_registry()
    assert sorted(workflow.functions) == sorted(registry.names())
    for name, fn in workflow.functions.items():
        assert fn is registry.resolve(name)
```

with:

```python
def test_every_resolved_function_is_the_registry_binding() -> None:
    workflow = load_builtin("task")
    registry = default_registry()
    assert set(INTEGRATE_ONLY_NAMES) <= set(registry.names())
    task_names = sorted(set(registry.names()) - set(INTEGRATE_ONLY_NAMES))
    assert sorted(workflow.functions) == task_names
    for name, fn in workflow.functions.items():
        assert fn is registry.resolve(name)
```

- [ ] **Step 3: Update the engine's fake function map**

In `tests/test_engine.py`, inside `test_the_builtin_task_document_walks_against_a_fake_registry`, replace:

```python
    def agent_only_gate(**kwargs: Any) -> None:
        raise AssertionError("an agent phase's gate is the agent runner's business")

```

with:

```python
    def agent_only_gate(**kwargs: Any) -> None:
        raise AssertionError("an agent phase's gate is the agent runner's business")

    def integrate_only_gate(**kwargs: Any) -> None:
        raise AssertionError("task.yaml never references an integrate-only gate")

```

and in the `functions` dict replace:

```python
        "verification_gate": agent_only_gate,
    }
    assert sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)
```

with:

```python
        "verification_gate": agent_only_gate,
        "merge_completed_gate": integrate_only_gate,
    }
    assert sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_registry.py tests/workflow/test_builtin_task.py tests/test_engine.py -v`
Expected: FAIL — `test_default_registry_holds_exactly_the_names_the_builtin_documents_use`, `test_default_registry_returns_an_independent_registry_each_call` (names mismatch), `test_default_registry_resolves_the_merge_gate_to_the_real_callable` (`UnknownFunctionError`), `test_every_resolved_function_is_the_registry_binding` (subset assertion), and `test_the_builtin_task_document_walks_against_a_fake_registry` (`sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)`).

- [ ] **Step 5: Write the implementation**

In `src/agent_manager/workflow/registry.py`, replace line 19:

```python
from agent_manager.steps import docs_commit, plan_check, reducers, rollup, verify, worktree
```

with:

```python
from agent_manager.steps import (
    docs_commit,
    integrate,
    plan_check,
    reducers,
    rollup,
    verify,
    worktree,
)
```

Replace lines 210-228:

```python
BUILTIN_FUNCTION_NAMES = (
    "critic_blockers_gate",
    "docs_commit.commit_documents",
    "exploration_output_gate",
    "implement_blocked_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
"""Every name appearing in a run/when/gate position of `builtin/task.yaml`,
sorted. Kept here as data so a test can assert the registry and the document
have not drifted apart."""
```

with:

```python
BUILTIN_FUNCTION_NAMES = (
    "critic_blockers_gate",
    "docs_commit.commit_documents",
    "exploration_output_gate",
    "implement_blocked_gate",
    "merge_completed_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
"""Every name the builtin workflow documents use in a run/when/gate position,
sorted: `task.yaml`, plus the integrate-only names the forthcoming
`integrate.yaml` will reference (`merge_completed_gate`). Kept here as data so
a test can assert the registry and the documents have not drifted apart."""
```

Replace the first paragraph of the `default_registry()` docstring (line 232):

```python
    """A fresh registry holding every name `builtin/task.yaml` references.
```

with:

```python
    """A fresh registry holding every name the builtin workflow documents use:
    `task.yaml`, plus the integrate-only names the forthcoming `integrate.yaml`
    will reference.
```

Then, after the `implement_blocked_gate` registration (lines 255-256):

```python
    # Decision O7: a coder that reports blocked stops the subtask at implement.
    registry.register("implement_blocked_gate", reducers.implement_blocked_gate)
```

insert:

```python
    # Integrate addendum I3: git, not the resolver, judges a merge complete.
    registry.register("merge_completed_gate", integrate.merge_completed_gate)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_registry.py tests/workflow/test_builtin_task.py tests/test_engine.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/workflow/registry.py tests/workflow/test_registry.py tests/workflow/test_builtin_task.py tests/test_engine.py
git commit -m "feat(registry): register merge_completed_gate as an integrate-only name"
```

---

### Task 4: Full-suite verification

**Files:** none changed.

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: a green default suite.

- [ ] **Step 1: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, including `tests/e2e` (its slow real-harness test stays excluded by the default markers exactly as before). Nothing under `tests/e2e` was changed.

- [ ] **Step 2: Confirm the base branch was not touched**

Run: `git log --oneline m5/task-merge-one-story-tip-ce288496..HEAD`
Expected: exactly the three commits from Tasks 1-3, and `git status --porcelain` prints nothing.
