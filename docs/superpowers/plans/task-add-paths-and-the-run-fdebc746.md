<!-- task-pipeline: validated -->
# Spec: Add paths and the run artifact layout

Subtask `fdebc746-1042-4965-8a19-40a48c747973`, under story `8831189b` ("Foundations: paths, run store and journal").

## Scope

One new module, `src/agent_manager/paths.py`, plus its unit tests in `tests/test_paths.py`. The module is the single place that decides where anything `agent-manager` writes lives on disk: the per-user data directory, the per-project database file, and the run/attempt artifact tree described in section 9 of the design spec (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:346-387`).

It mirrors `/home/paulomtts/Code/brd/src/brd/paths.py` for the first two functions and extends it with the two run-tree functions that `brd` has no equivalent of.

Explicitly **not** in this subtask: run state models (`models.py`, sibling `1535b285`), the SQLite store and journal (`store.py`, sibling `ef248597`), phase result models, and anything to do with milestone orchestration or non-Claude harnesses. `paths.py` computes and creates directories; it never writes a file, never opens a database, and never shells out to git or `brd`.

No Pydantic here. Per CLAUDE.md, Pydantic is for values validated at a process boundary; this is pure filesystem logic, so plain module-level functions returning `pathlib.Path` are the right shape.

## Observable behaviour

`data_dir() -> Path` — returns `$XDG_DATA_HOME/agent-manager` when `XDG_DATA_HOME` is set to a non-empty value, otherwise `$HOME/.local/share/agent-manager`. An empty-string `XDG_DATA_HOME` is treated as unset, matching `brd`. The directory is created with `mkdir(parents=True, exist_ok=True)` before it is returned, so callers may assume it exists. Calling it repeatedly is idempotent.

`project_db_path(root: Path) -> Path` — returns `data_dir()/projects/<digest>.db`, where `<digest>` is the hex sha256 of `str(root.resolve())`. The `projects/` parent is created before the path is returned; the `.db` file itself is not created. Resolution happens before hashing, so two spellings of the same repo (relative path, symlink, trailing `..`) key to the same database, and two distinct repo roots key to different ones. The parameter is named `root` (the `brd` original calls it `root_path`); behaviour is otherwise identical.

`run_dir(run_id: str) -> Path` — returns `data_dir()/runs/<run_id>`, created with `mkdir(parents=True, exist_ok=True)`. This is the root of the artifact tree for a single run: the journal JSONL and every card's attempt directories hang off it. Because it is anchored at `data_dir()`, it is by construction outside any repository worktree.

`attempt_dir(run_id: str, card: str, phase: str, attempt: int) -> Path` — returns `run_dir(run_id)/<card>/<phase>.<attempt>`, created with `mkdir(parents=True, exist_ok=True)`. This is the directory the engine hands to a dispatch and into which the three per-attempt artifacts land: `prompt.txt`, `result.json`, `stdout.log` (design spec line 362). `attempt` is formatted as a plain decimal integer, so attempt 2 of the implement phase for card `abc123` is `.../abc123/implement.2`.

### The load-bearing invariant

Attempt directories must live **outside the repository worktree**. The design spec is explicit about why (lines 265-268): the harness runs with cwd set to the subtask worktree, and a `result.json` or `stdout.log` written inside that worktree would either fail the verify step's clean-tree check or be swept into a commit. Anchoring the whole run tree at `data_dir()` — never at the repo root, never at a path derived from a worktree — is what enforces this. No function in this module accepts a worktree path or a repo root as the base of the run tree; `project_db_path` takes a repo root only to hash it, never to write under it.

Paths are computed, not validated: the module makes no assertion that a run id or card id is well-formed, and does no escaping of them. That is the caller's concern, consistent with `brd`.

## Error paths

- `XDG_DATA_HOME` unset (or empty) and `HOME` unset: `KeyError` from `os.environ["HOME"]`. Same as `brd`; not caught or reworded here.
- `data_dir()` or any `mkdir` fails because of a permissions problem or a non-directory in the way: the underlying `OSError` propagates unchanged. This module adds no error envelope; CLI-level `{"ok": false}` formatting belongs to the CLI layer, not here.
- `root.resolve()` on a non-existent path: `pathlib` resolves non-strictly, so this returns a path rather than raising. A project database may therefore be keyed for a root that does not exist yet; that is intentional and matches `brd`.

## Test list

All tests below are **Pure functions tier** per section 14 of the design spec (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`). `paths.py` is pure computation over environment variables plus idempotent directory creation, with no git repository, no `brd` board, no harness and no network — so none of the Steps, Adapters, Engine or End-to-end tiers apply. They live in `tests/test_paths.py` and use `monkeypatch` and `tmp_path` in the style of `/home/paulomtts/Code/brd/tests/test_paths.py`; every test sets `XDG_DATA_HOME` (or `HOME`) into `tmp_path` so nothing touches the real user data directory.

1. `data_dir` honours a set `XDG_DATA_HOME`: returns `tmp_path/"agent-manager"` and the directory exists.
2. `data_dir` falls back to `$HOME/.local/share/agent-manager` when `XDG_DATA_HOME` is deleted, and the directory exists.
3. `data_dir` treats an empty-string `XDG_DATA_HOME` as unset and falls back to `$HOME`.
4. `data_dir` is idempotent: two calls return the same path and neither raises when the directory already exists.
5. `project_db_path` is deterministic for one root: two calls are equal, the parent is `<data_dir>/projects`, and that parent exists.
6. `project_db_path` differs across two distinct roots.
7. `project_db_path` resolves before hashing: a relative or symlinked spelling of the same root yields the same path as the absolute one.
8. `run_dir` returns `<data_dir>/runs/<run_id>`, the directory exists, and a second call with the same id is a no-op returning the same path.
9. `attempt_dir` returns `<run_dir>/<card>/<phase>.<attempt>` with the attempt number rendered as a decimal integer, and the directory exists.
10. `attempt_dir` separates attempts and phases: attempts 1 and 2 of one phase differ, and two phases of one card differ, while both sit under the same card directory.
11. The run tree is outside any worktree: `run_dir` and `attempt_dir` for a run are not relative to a `tmp_path` repo root used as a stand-in worktree — asserted by checking the returned paths are under `data_dir()` and that the repo directory stays empty after the calls.

## Verification

`uv run pytest`. There is no separate lint or typecheck command in this repo.

---

# Paths and Run Artifact Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/paths.py`, the single module that decides where `agent-manager` writes on disk — the XDG data directory, the per-project database path, and the run/attempt artifact tree that must live outside any repository worktree.

**Architecture:** Four plain module-level functions over `pathlib.Path`, `os.environ` and `hashlib.sha256`, mirroring `/home/paulomtts/Code/brd/src/brd/paths.py` and extending it with `run_dir` and `attempt_dir`. Every write base is derived from `data_dir()`; no function takes a worktree path or repo root as a write base, which is what structurally keeps attempt artifacts out of the git worktree. Directory creation is idempotent (`mkdir(parents=True, exist_ok=True)`) and every other error propagates unchanged.

**Tech Stack:** Python 3.12+, stdlib only (`os`, `hashlib`, `pathlib`), pytest with `monkeypatch`/`tmp_path`, `uv` for packaging and test running.

**Spec:** `docs/superpowers/specs/task-add-paths-and-the-run-fdebc746-design.md` (reproduced verbatim above). Upstream source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` sections 6 (lines 255-280), 9 (lines 346-387) and 14 (lines 477-492).

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md).
- Verification is `uv run pytest` and nothing else; there is no lint or typecheck command (CLAUDE.md).
- `requires-python = ">=3.12"`; stdlib only for this module — do not add dependencies to `pyproject.toml`.
- No Pydantic in `paths.py`: Pydantic is for values validated at a process boundary, and this module is not one.
- The namespace directory is exactly `agent-manager` (hyphen), not `agent_manager`.
- The digest is the hex sha256 of `str(root.resolve())`, with a `.db` suffix — byte-for-byte compatible with `brd`'s scheme.
- All run/attempt paths are anchored at `data_dir()`. No function in this module may accept a worktree path or a repo root as a write base.
- This subtask owns `paths.py` only. Do not create `models.py`, `store.py`, journal code, or any run-state schema — those are siblings `1535b285` and `ef248597`.
- All tests in this plan are **Pure functions tier** (design spec section 14) and live in the flat `tests/` directory alongside the existing `tests/test_package.py`.

## Review Focus

Failure modes the spec implies that the spec's own test list does not exercise. Each has been given a test in the task that owns the code.

1. `XDG_DATA_HOME` empty **and** `HOME` unset — the spec promises a bare `KeyError`, but nothing pins it; a well-meaning `os.environ.get("HOME", ...)` would silently write into the process cwd. Pinned in Task 1, Step 6.
2. A regular file already sitting at the `agent-manager` data directory path — `mkdir(exist_ok=True)` still raises `FileExistsError`, and the spec says that `OSError` must propagate unwrapped rather than be swallowed. Pinned in Task 1, Step 6.
3. `project_db_path` on a root that does not exist yet — non-strict `resolve()` must return a path instead of raising, since a project may be keyed before it is cloned. Pinned in Task 2, Step 6.
4. The exact digest format — a later `store.py` or an operator inspecting `~/.local/share/agent-manager/projects/` needs `sha256(str(resolved)).hexdigest() + ".db"`, not merely "some deterministic string". Pinned in Task 2, Step 6.
5. Attempt number rendering for `0` and for multi-digit values — `implement.10` must not become `implement.010` or sort-collide with `implement.1`, because resume matches directories by name. Pinned in Task 3, Step 6.

---

### Task 1: `data_dir()` — the XDG-aware data directory

**Files:**
- Create: `src/agent_manager/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing (first task; the branch has only `src/agent_manager/__init__.py` and `tests/test_package.py`).
- Produces: `data_dir() -> pathlib.Path` — returns an existing directory, `$XDG_DATA_HOME/agent-manager` or `$HOME/.local/share/agent-manager`. Tasks 2 and 3 call it with no arguments.

- [ ] **Step 1: Write the failing tests for `data_dir`**

Create `tests/test_paths.py` with exactly this content:

```python
from pathlib import Path

from agent_manager import paths


def test_data_dir_uses_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = paths.data_dir()
    assert result == tmp_path / "agent-manager"
    assert result.is_dir()


def test_data_dir_defaults_to_home_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = paths.data_dir()
    assert result == tmp_path / ".local" / "share" / "agent-manager"
    assert result.is_dir()


def test_data_dir_treats_empty_xdg_data_home_as_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    result = paths.data_dir()
    assert result == tmp_path / ".local" / "share" / "agent-manager"
    assert result.is_dir()


def test_data_dir_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = paths.data_dir()
    second = paths.data_dir()
    assert first == second
    assert second.is_dir()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'agent_manager.paths'` (or `ImportError: cannot import name 'paths'`).

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/paths.py`:

```python
"""Filesystem locations for agent-manager.

Every path this tool writes to is derived from :func:`data_dir`. Nothing here
takes a repository worktree as a write base: that is what keeps run artifacts
out of the worktree, where they would break the verify step's clean-tree check
or be swept into a commit.
"""

import os
from pathlib import Path


def data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path(os.environ["HOME"]) / ".local" / "share"
    result = base / "agent-manager"
    result.mkdir(parents=True, exist_ok=True)
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/paths.py tests/test_paths.py
git commit -m "feat(paths): add XDG-aware data_dir"
```

- [ ] **Step 6: Write the failing error-path tests (Review Focus 1 and 2)**

Append to `tests/test_paths.py`:

```python
def test_data_dir_raises_key_error_when_home_and_xdg_are_unset(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(KeyError):
        paths.data_dir()


def test_data_dir_propagates_oserror_when_a_file_is_in_the_way(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    (tmp_path / "agent-manager").write_text("not a directory")
    with pytest.raises(OSError):
        paths.data_dir()
```

and add the `pytest` import at the top of the file, so the import block reads:

```python
from pathlib import Path

import pytest

from agent_manager import paths
```

- [ ] **Step 7: Run the new tests**

Run: `uv run pytest tests/test_paths.py -v`
Expected: 6 passed. These two already pass against the Step 3 implementation — they are regression pins on behaviour the spec's "Error paths" section requires (a bare `KeyError`, an unwrapped `OSError`). If either fails, the implementation is catching something it must not; fix `paths.py` to let the exception escape rather than changing the test.

- [ ] **Step 8: Commit**

```bash
git add tests/test_paths.py
git commit -m "test(paths): pin data_dir error paths"
```

---

### Task 2: `project_db_path()` — the per-project database path

**Files:**
- Modify: `src/agent_manager/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: `data_dir() -> Path` from Task 1.
- Produces: `project_db_path(root: Path) -> Path` — `data_dir()/projects/<sha256 hex of str(root.resolve())>.db`. The parameter is named `root`. The `.db` file is not created; its parent directory is.

- [ ] **Step 1: Write the failing tests for `project_db_path`**

Append to `tests/test_paths.py`:

```python
def test_project_db_path_is_deterministic_per_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    result = paths.project_db_path(project_root)
    assert result == paths.project_db_path(project_root)
    assert result.parent == tmp_path / "data" / "agent-manager" / "projects"
    assert result.parent.is_dir()
    assert not result.exists()


def test_project_db_path_differs_per_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    repo1 = tmp_path / "repo1"
    repo1.mkdir()
    repo2 = tmp_path / "repo2"
    repo2.mkdir()

    assert paths.project_db_path(repo1) != paths.project_db_path(repo2)


def test_project_db_path_resolves_relative_spelling(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.chdir(tmp_path)

    assert paths.project_db_path(Path("repo")) == paths.project_db_path(project_root)
    assert paths.project_db_path(project_root / "sub" / "..") == paths.project_db_path(
        project_root
    )


def test_project_db_path_resolves_symlinked_spelling(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project_root)

    assert paths.project_db_path(link) == paths.project_db_path(project_root)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -k project_db_path -v`
Expected: 4 failed with `AttributeError: module 'agent_manager.paths' has no attribute 'project_db_path'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/paths.py`, change the import block to add `hashlib`:

```python
import hashlib
import os
from pathlib import Path
```

and append below `data_dir`:

```python
def project_db_path(root: Path) -> Path:
    projects_dir = data_dir() / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
    return projects_dir / f"{digest}.db"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/paths.py tests/test_paths.py
git commit -m "feat(paths): add project_db_path keyed by resolved root"
```

- [ ] **Step 6: Write the failing tests for the digest format and absent roots (Review Focus 3 and 4)**

Append to `tests/test_paths.py`:

```python
def test_project_db_path_digest_is_sha256_of_resolved_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    expected = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()
    result = paths.project_db_path(project_root)
    assert result.name == f"{expected}.db"


def test_project_db_path_accepts_a_root_that_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    absent = tmp_path / "not-cloned-yet"

    result = paths.project_db_path(absent)
    assert result.parent == tmp_path / "data" / "agent-manager" / "projects"
    assert result.name.endswith(".db")
    assert not absent.exists()
```

and add `hashlib` to the test file's import block, so it reads:

```python
import hashlib
from pathlib import Path

import pytest

from agent_manager import paths
```

- [ ] **Step 7: Run the new tests**

Run: `uv run pytest tests/test_paths.py -k "digest or does_not_exist" -v`
Expected: 2 passed against the Step 3 implementation — they pin the wire-format of the digest and the non-strict `resolve()` contract from the spec's "Error paths" section. If the absent-root test errors with `FileNotFoundError`, the implementation is passing `strict=True` to `resolve()`; remove it.

- [ ] **Step 8: Commit**

```bash
git add tests/test_paths.py
git commit -m "test(paths): pin project_db_path digest format and absent roots"
```

---

### Task 3: `run_dir()` and `attempt_dir()` — the run artifact tree

**Files:**
- Modify: `src/agent_manager/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: `data_dir() -> Path` from Task 1.
- Produces:
  - `run_dir(run_id: str) -> Path` — `data_dir()/runs/<run_id>`, created.
  - `attempt_dir(run_id: str, card: str, phase: str, attempt: int) -> Path` — `run_dir(run_id)/<card>/<phase>.<attempt>`, created. This is the directory into which a dispatch writes `prompt.txt`, `result.json` and `stdout.log`.

- [ ] **Step 1: Write the failing tests for the run tree**

Append to `tests/test_paths.py`:

```python
def test_run_dir_is_under_data_dir_and_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = paths.run_dir("run-abc")
    assert result == tmp_path / "agent-manager" / "runs" / "run-abc"
    assert result.is_dir()


def test_run_dir_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = paths.run_dir("run-abc")
    (first / "journal.jsonl").write_text("{}\n")
    second = paths.run_dir("run-abc")
    assert first == second
    assert (second / "journal.jsonl").read_text() == "{}\n"


def test_attempt_dir_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = paths.attempt_dir("run-abc", "abc123", "implement", 2)
    assert result == (
        tmp_path / "agent-manager" / "runs" / "run-abc" / "abc123" / "implement.2"
    )
    assert result.is_dir()


def test_attempt_dir_separates_attempts_and_phases(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = paths.attempt_dir("run-abc", "abc123", "implement", 1)
    second = paths.attempt_dir("run-abc", "abc123", "implement", 2)
    review = paths.attempt_dir("run-abc", "abc123", "review", 1)

    assert first != second
    assert first != review
    assert first.parent == second.parent == review.parent
    assert first.parent == paths.run_dir("run-abc") / "abc123"


def test_run_tree_never_lands_inside_a_worktree(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.chdir(worktree)

    run = paths.run_dir("run-abc")
    attempt = paths.attempt_dir("run-abc", "abc123", "implement", 1)

    assert run.is_relative_to(paths.data_dir())
    assert attempt.is_relative_to(paths.data_dir())
    assert not run.is_relative_to(worktree)
    assert not attempt.is_relative_to(worktree)
    assert list(worktree.iterdir()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -k "run_dir or attempt_dir or run_tree" -v`
Expected: 5 failed with `AttributeError: module 'agent_manager.paths' has no attribute 'run_dir'` / `'attempt_dir'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/paths.py`:

```python
def run_dir(run_id: str) -> Path:
    """Root of one run's artifact tree, always outside any repository worktree."""
    result = data_dir() / "runs" / run_id
    result.mkdir(parents=True, exist_ok=True)
    return result


def attempt_dir(run_id: str, card: str, phase: str, attempt: int) -> Path:
    """Directory holding one attempt's prompt.txt, result.json and stdout.log."""
    result = run_dir(run_id) / card / f"{phase}.{attempt}"
    result.mkdir(parents=True, exist_ok=True)
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: 17 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/paths.py tests/test_paths.py
git commit -m "feat(paths): add run_dir and attempt_dir artifact tree"
```

- [ ] **Step 6: Write the failing test for attempt-number rendering (Review Focus 5)**

Append to `tests/test_paths.py`:

```python
def test_attempt_dir_renders_attempt_as_plain_decimal(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    card_dir = paths.run_dir("run-abc") / "abc123"

    assert paths.attempt_dir("run-abc", "abc123", "implement", 0) == (
        card_dir / "implement.0"
    )
    assert paths.attempt_dir("run-abc", "abc123", "implement", 10) == (
        card_dir / "implement.10"
    )
    assert paths.attempt_dir("run-abc", "abc123", "implement", 1) != paths.attempt_dir(
        "run-abc", "abc123", "implement", 10
    )
```

- [ ] **Step 7: Run the new test**

Run: `uv run pytest tests/test_paths.py::test_attempt_dir_renders_attempt_as_plain_decimal -v`
Expected: PASS against the Step 3 implementation — it pins that the attempt number is not zero-padded and not truncated, which resume relies on when matching attempt directories by name.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: 19 passed (18 in `tests/test_paths.py`, 1 in `tests/test_package.py`).

- [ ] **Step 9: Commit**

```bash
git add tests/test_paths.py
git commit -m "test(paths): pin attempt number rendering"
```

---

## Final state

`src/agent_manager/paths.py` contains exactly four public functions — `data_dir`, `project_db_path`, `run_dir`, `attempt_dir` — with stdlib-only imports (`hashlib`, `os`, `pathlib.Path`). `tests/test_paths.py` contains 18 Pure-functions-tier tests. No other file on the branch is touched: no `models.py`, no `store.py`, no `pyproject.toml` change.
