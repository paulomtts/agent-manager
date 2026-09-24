<!-- task-pipeline: validated -->
# Serialize `git worktree add` per repository — subtask 88150552

Parent story 19b21273 "Serialize the shared git and board resources" (milestone cdbfa10d). This subtask narrows decision P3 of `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` (lines 58-64) to the git half: "`git worktree add` runs under a per-repository lock." The board lock, the rollup critical section, the `brd` stress test and any `board._run` retry belong to sibling daf14164 and are out of scope here.

## Scope

Only `src/agent_manager/steps/worktree.py` and `tests/steps/test_worktree.py` change. `board.py` and `rollup.py` are not touched.

- A private per-repository lock registry in `worktree.py`: a module-level `dict` mapping the resolved repository path (`Path(repo_path).resolve()`, as a string) to a `threading.Lock`, plus one module-level guard `threading.Lock`. Locks are created lazily; lookup-or-create happens under the guard, so two threads can never create two different locks for the same repository. Two spellings of the same repository (trailing slash, `..`, symlink) resolve to the same key and therefore the same lock.
- `ensure` holds that repository's lock around the worktree-creation step only. The initial unlocked reads stay unlocked and in their current order: `for-each-ref`, `worktree list --porcelain`, and the `origin/<base>` probe. `_commit_count` after creation stays unlocked.
- Same-path race handling: when the unlocked read found the worktree not registered, then inside the lock `ensure` re-runs `worktree list --porcelain` and checks registration again before adding. If the path is now registered (another thread created it in between), it skips the add and reports `created=False` and `worktree_existed=True`. Otherwise it runs the same `worktree add` argv it runs today and reports `created=True`. When the unlocked read already found the worktree registered, no lock is taken and nothing changes from today.
- The signature stays `ensure(branch, base, worktree, repo_dir, git_runner=run_git)`, and so does the return dict's key set (`branch`, `worktree`, `branch_existed`, `worktree_existed`, `created`, `commit_count`). Both are consumed by `workflow/registry.py:259`, `workflow/builtin/task.yaml:7` and `engine.py`.
- No `git worktree prune` is added anywhere. Prune is a global sweep and could remove another lane's worktree that is not yet populated. The module's promise (it never resets, deletes, cleans, commits or pushes) is kept unchanged.
- In-process threads only. Two `am` processes on one repository are not supported, and there is no file lock or cross-process mechanism.

## Observable behavior

- Sequential callers (`--max-concurrent 1`, and every existing test) see the same results as today. The only difference on the create path is one extra `worktree list --porcelain` call inside the lock. Existing index-based assertions still hold: the origin probe is still `calls[2]`, and no `add` runs for an existing worktree.
- N threads ensuring distinct branches at distinct paths in one repository all succeed, because git never runs two `worktree add` commands on that repository at once.
- Two threads ensuring the same branch at the same path both succeed. Exactly one reports `created=True`.
- Different repositories get different locks and never block each other.

## Error paths

- A failing `worktree add` raises `GitError` from inside the lock, as it does today. The lock is released no matter what (context manager), so a failed lane never wedges other lanes on that repository.
- Argument validation (`_required_name` / `_required_absolute`) still raises before any git call or lock acquisition. `test_bad_arguments_raise_before_any_git_invocation` stays green.
- A same-branch/different-path collision still surfaces git's own error. This subtask does not handle it.

## Tests

All tests below go in the **Steps tier**, in `tests/steps/test_worktree.py`. The test-placement rule is design spec §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:488`): "Steps -- against temporary git repositories and a temporary brd board; no network". These tests run against a real `tmp_path` git repo, reuse the existing `repo` fixture (line 47) and the `_git`/`_commit` helpers, and carry the `requires_git` marker. They are not unit tests with a fake runner, and they are not e2e.

1. **Eight distinct lanes in parallel** (Steps tier). Eight threads start together behind a `threading.Barrier(8)`. Each calls `ensure` with its own branch and its own `tmp_path` worktree path, all against `repo` with base `main`. Assert:
   - every thread returns without an exception and with `created=True`;
   - `git worktree list --porcelain` shows 9 entries (8 plus the main checkout);
   - walking `repo/.git` finds no `*.lock` file;
   - in each worktree, `rev-parse --abbrev-ref HEAD` equals its requested branch;
   - in each worktree, `merge-base HEAD main` equals `main`'s commit, and so does `HEAD` (the branch was cut from the requested base).
2. **Same branch, same path, two threads** (Steps tier). Two threads behind a `threading.Barrier(2)` call `ensure` with the identical branch, base and worktree path. Assert that both return without an exception, that exactly one result has `created=True` and the other `created=False`, and that the worktree is registered exactly once.
3. **Existing guarantees stay green** (Steps tier, existing tests unchanged). These are `test_an_existing_worktree_add_is_never_attempted_a_second_time` (line 266), `test_the_origin_probe_is_recorded_and_its_failure_is_not_an_error` (line 507) and `test_no_forbidden_git_operation_runs_on_any_path` (lines 525-572, forbidden tokens: reset, clean, commit, push, prune).

Acceptance also requires the whole default `uv run pytest` suite, including `tests/e2e`, to pass.

---

# Serialize `git worktree add` per repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `worktree.ensure` safe to call from many threads at once on one repository by serializing `git worktree add` under a lazily created per-repository lock, with an in-lock re-check so two threads ensuring the same worktree both succeed.

**Architecture:** `src/agent_manager/steps/worktree.py` gains a private registry (`_REPO_LOCKS: dict[str, threading.Lock]` guarded by `_REPO_LOCKS_GUARD: threading.Lock`) and a helper `_repo_lock(repo_path: str) -> threading.Lock` keyed by `str(Path(repo_path).resolve())`. `ensure` keeps its three unlocked reads, then, only when the worktree was not registered, takes the repository lock, re-runs `worktree list --porcelain`, and either skips (another thread made it) or runs the unchanged `worktree add` argv. `_commit_count` runs after the lock is released.

**Tech Stack:** Python 3 stdlib (`threading`, `pathlib`, `subprocess`, `concurrent.futures` in tests), pytest, real `git` CLI, `uv`.

**Spec:** `docs/superpowers/specs/task-serialize-git-worktree-88150552-design.md` (prepended verbatim above).

## Global Constraints

- Only `src/agent_manager/steps/worktree.py` and `tests/steps/test_worktree.py` change. `board.py` and `rollup.py` are not touched.
- Signature stays `ensure(branch, base, worktree, repo_dir, git_runner=run_git)`; return dict keys stay exactly `branch`, `worktree`, `branch_existed`, `worktree_existed`, `created`, `commit_count`.
- Lock key is `str(Path(repo_path).resolve())`; lookup-or-create happens under one module-level guard `threading.Lock`.
- The lock covers only the in-lock `worktree list --porcelain` re-check and `worktree add`. `for-each-ref`, the first `worktree list --porcelain`, the `origin/<base>` probe and `_commit_count` stay unlocked and in their current order (the origin probe stays `calls[2]`).
- No `git worktree prune` anywhere. Never reset, delete, clean, commit or push.
- In-process threads only; no file lock, no cross-process mechanism.
- Argument validation still raises before any git call or lock acquisition.
- All new tests are Steps tier, in `tests/steps/test_worktree.py`, against real `tmp_path` git repos, marked `@requires_git`.
- Verification: `uv run pytest` (whole suite, including `tests/e2e`) must pass.

## Review Focus

- Same branch, different worktree paths, called one after the other: the second call must surface git's own `GitError` (spec "Error paths"), and the repository lock must be free afterwards. Pinned in Task 2 (`test_a_same_branch_at_a_different_path_surfaces_gits_error_and_frees_the_lock`).
- A failing `worktree add` (stray non-worktree directory) must release the repository lock so other lanes are not wedged. Pinned in Task 2 (`test_a_failed_add_releases_the_repository_lock`).
- Two different repositories must never block each other: holding repo A's lock must not stall `ensure` on repo B. Pinned in Task 2 (`test_a_different_repository_is_never_blocked_by_this_ones_lock`).
- An already-registered worktree must take no lock at all (resume path stays unchanged): `ensure` on an existing worktree completes while another thread holds that repository's lock. Pinned in Task 2 (`test_an_existing_worktree_takes_no_lock`).
- Different spellings of one repository (trailing slash, `.git/..`, symlinked path) must share one lock, and concurrent first lookups must all get the same lock object. Pinned in Task 1.

---

## File Structure

- Modify `src/agent_manager/steps/worktree.py`: add `import threading` (imports at lines 17-20), add the lock registry and `_repo_lock` after `_resolve_base` (ends line 159), rewrite the creation block of `ensure` (lines 200-219), and add one paragraph to the module docstring (lines 1-15).
- Modify `tests/steps/test_worktree.py`: add imports (lines 9-14), extract `_init_repo` from the `repo` fixture (lines 47-62), and append the new Steps-tier tests at the end of the file (after line 573).

---

### Task 1: Per-repository lock registry

**Files:**
- Modify: `src/agent_manager/steps/worktree.py:17-20` (imports) and insert after line 159 (end of `_resolve_base`)
- Test: `tests/steps/test_worktree.py` (imports at lines 9-14, append tests at end)

**Interfaces:**
- Consumes: nothing new.
- Produces: `worktree._repo_lock(repo_path: str) -> threading.Lock` — returns the same `threading.Lock` object for every spelling of one repository path (key `str(Path(repo_path).resolve())`), different objects for different repositories. Module globals `_REPO_LOCKS: dict[str, threading.Lock]` and `_REPO_LOCKS_GUARD: threading.Lock`.

- [ ] **Step 1: Add test imports**

In `tests/steps/test_worktree.py`, replace the import block at lines 9-14:

```python
import os
import shutil
import subprocess
from pathlib import Path

import pytest
```

with:

```python
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/steps/test_worktree.py`:

```python
@requires_git
def test_every_spelling_of_one_repository_shares_one_lock(repo: Path, tmp_path: Path):
    # Trailing slash, a `..` hop and a symlinked path all name the same
    # repository, so they must serialize on the same lock.
    link = tmp_path / "repo-link"
    link.symlink_to(repo, target_is_directory=True)

    lock = worktree._repo_lock(str(repo))

    assert worktree._repo_lock(f"{repo}{os.sep}") is lock
    assert worktree._repo_lock(str(repo / ".git" / "..")) is lock
    assert worktree._repo_lock(str(link)) is lock


def test_different_repositories_get_different_locks(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    assert worktree._repo_lock(str(a)) is not worktree._repo_lock(str(b))


def test_concurrent_first_lookups_all_get_the_same_lock(tmp_path: Path):
    # The registry is created lazily; without the guard two threads racing on
    # a fresh key could each install their own lock.
    fresh = tmp_path / "fresh-repo"
    fresh.mkdir()
    lanes = 8
    barrier = threading.Barrier(lanes)

    def lookup(_: int) -> threading.Lock:
        barrier.wait(timeout=30)
        return worktree._repo_lock(str(fresh))

    with ThreadPoolExecutor(max_workers=lanes) as pool:
        locks = list(pool.map(lookup, range(lanes)))

    assert all(lock is locks[0] for lock in locks)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_worktree.py -k "lock" -v`
Expected: the three new tests FAIL with `AttributeError: module 'agent_manager.steps.worktree' has no attribute '_repo_lock'`.

- [ ] **Step 4: Add the `threading` import**

In `src/agent_manager/steps/worktree.py`, replace lines 17-20:

```python
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
```

with:

```python
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
```

- [ ] **Step 5: Add the registry and `_repo_lock`**

In `src/agent_manager/steps/worktree.py`, insert directly after the end of `_resolve_base` (after the line `    return f"origin/{base}"`) and before `def ensure(`:

```python
_REPO_LOCKS: dict[str, threading.Lock] = {}
"""One lock per repository, keyed by its resolved path, created on first use."""

_REPO_LOCKS_GUARD = threading.Lock()
"""Guards lookup-or-create in `_REPO_LOCKS`, so one repository never gets two locks."""


def _repo_lock(repo_path: str) -> threading.Lock:
    """The lock serializing `git worktree add` on the repository at `repo_path`.

    Keyed by the resolved path, so a trailing slash, a `..` hop or a symlinked
    spelling of the same repository all share one lock. In-process threads
    only: two `am` processes on one repository are not supported.
    """
    key = str(Path(repo_path).resolve())
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _REPO_LOCKS[key] = lock
        return lock
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: all tests PASS (the three new ones and every existing one).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/steps/worktree.py tests/steps/test_worktree.py
git commit -m "Add a per-repository lock registry to the worktree step"
```

---

### Task 2: Serialize `worktree add` with an in-lock re-check

**Files:**
- Modify: `src/agent_manager/steps/worktree.py:1-15` (module docstring) and `:200-219` (creation block of `ensure`)
- Test: `tests/steps/test_worktree.py:47-62` (extract `_init_repo`), append tests at end

**Interfaces:**
- Consumes: `worktree._repo_lock(repo_path: str) -> threading.Lock` from Task 1; existing `worktree.worktree_paths(porcelain: str) -> list[str]`, `worktree._is_registered(candidate: str, registered: list[str]) -> bool`, `worktree.run_git(argv: list[str]) -> str`, `worktree.GitError`.
- Produces: `ensure(branch, base, worktree, repo_dir, git_runner=run_git) -> dict[str, object]` — unchanged signature and keys; now thread-safe per repository. Test helper `_init_repo(root: Path) -> Path` in `tests/steps/test_worktree.py`.

- [ ] **Step 1: Extract `_init_repo` from the `repo` fixture**

In `tests/steps/test_worktree.py`, replace the fixture at lines 47-62:

```python
@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `main` with one commit, isolated in tmp_path."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, "README.md", "base\n")
    return root
```

with:

```python
def _init_repo(root: Path) -> Path:
    """Create a real git repo at `root` on `main` with one commit."""
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, "README.md", "base\n")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `main` with one commit, isolated in tmp_path."""
    return _init_repo(tmp_path / "repo")
```

- [ ] **Step 2: Write the failing eight-lane test**

Append to the end of `tests/steps/test_worktree.py`:

```python
def _is_add(argv: list[str]) -> bool:
    return "worktree" in argv and "add" in argv


@requires_git
def test_eight_distinct_lanes_in_parallel_all_succeed(repo: Path, tmp_path: Path):
    # Each `worktree add` is held open briefly so that, without the lock, the
    # eight lanes started together would overlap; `peak` records how many adds
    # were ever in flight at once. Every call still goes to real git.
    lanes = 8
    barrier = threading.Barrier(lanes)
    state = {"in_flight": 0, "peak": 0}
    state_lock = threading.Lock()

    def runner(argv: list[str]) -> str:
        if not _is_add(argv):
            return worktree.run_git(argv)
        with state_lock:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
        try:
            time.sleep(0.05)
            return worktree.run_git(argv)
        finally:
            with state_lock:
                state["in_flight"] -= 1

    def lane(index: int) -> dict[str, object]:
        barrier.wait(timeout=30)
        return worktree.ensure(
            branch=f"m4/lane-{index}",
            base="main",
            worktree=str(tmp_path / f"wt-{index}"),
            repo_dir=str(repo),
            git_runner=runner,
        )

    with ThreadPoolExecutor(max_workers=lanes) as pool:
        futures = [pool.submit(lane, index) for index in range(lanes)]
        errors = [future.exception(timeout=120) for future in futures]

    assert errors == [None] * lanes
    results = [future.result() for future in futures]
    assert all(result["created"] is True for result in results)
    assert state["peak"] == 1

    registered = worktree.worktree_paths(
        _git(repo, "worktree", "list", "--porcelain")
    )
    assert len(registered) == lanes + 1
    assert list((repo / ".git").rglob("*.lock")) == []

    main_head = _head(repo)
    for index in range(lanes):
        wt = tmp_path / f"wt-{index}"
        assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == (
            f"m4/lane-{index}"
        )
        assert _git(wt, "merge-base", "HEAD", "main").strip() == main_head
        assert _head(wt) == main_head
```

- [ ] **Step 3: Write the failing same-path test**

Append to the end of `tests/steps/test_worktree.py`:

```python
@requires_git
def test_two_threads_ensuring_the_same_worktree_both_succeed(
    repo: Path, tmp_path: Path
):
    # Both threads are held at the origin probe -- the last unlocked read --
    # until both have seen "not registered", so both reach the creation step.
    # Only one may add; the other must find it registered and report so.
    wt = tmp_path / "wt"
    start = threading.Barrier(2)
    after_reads = threading.Barrier(2)

    def runner(argv: list[str]) -> str:
        if "--verify" in argv:
            try:
                return worktree.run_git(argv)
            finally:
                after_reads.wait(timeout=30)
        return worktree.run_git(argv)

    def lane(_: int) -> dict[str, object]:
        start.wait(timeout=30)
        return worktree.ensure(
            branch="m4/same",
            base="main",
            worktree=str(wt),
            repo_dir=str(repo),
            git_runner=runner,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(lane, index) for index in range(2)]
        errors = [future.exception(timeout=120) for future in futures]

    assert errors == [None, None]
    results = [future.result() for future in futures]
    assert sorted(result["created"] for result in results) == [False, True]
    loser = next(result for result in results if result["created"] is False)
    assert loser["worktree_existed"] is True

    registered = worktree.worktree_paths(
        _git(repo, "worktree", "list", "--porcelain")
    )
    real_wt = os.path.realpath(wt)
    assert sum(os.path.realpath(p) == real_wt for p in registered) == 1
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "m4/same"
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/steps/test_worktree.py -k "eight_distinct_lanes or same_worktree_both_succeed" -v`
Expected: both FAIL. `test_eight_distinct_lanes_in_parallel_all_succeed` fails on `assert state["peak"] == 1` (peak is > 1 because adds overlap), or earlier on `errors` if git itself collided. `test_two_threads_ensuring_the_same_worktree_both_succeed` fails on `assert errors == [None, None]` because the second `worktree add` raises `GitError` (`'.../wt' already exists` or `branch 'm4/same' already exists`).

- [ ] **Step 5: Serialize the creation step in `ensure`**

In `src/agent_manager/steps/worktree.py`, replace the creation block (currently lines 200-219):

```python
    created = False
    if not worktree_existed:
        if branch_existed:
            # Check the existing branch out. Never re-cut it from base: a
            # killed run's commits live on that branch and re-cutting would
            # silently discard them.
            argv = ["-C", repo_path, "worktree", "add", worktree_path, branch]
        else:
            argv = [
                "-C",
                repo_path,
                "worktree",
                "add",
                worktree_path,
                "-b",
                branch,
                resolved_base,
            ]
        git_runner(argv)
        created = True
```

with:

```python
    created = False
    if not worktree_existed:
        # git must never run two `worktree add` on one repository at once.
        # Only the re-check and the add are held under the lock; the reads
        # above and the commit count below stay unlocked.
        with _repo_lock(repo_path):
            # Another thread may have created this very worktree between the
            # unlocked read and now: look again before adding.
            registered = worktree_paths(
                git_runner(["-C", repo_path, "worktree", "list", "--porcelain"])
            )
            if _is_registered(worktree_path, registered):
                worktree_existed = True
            else:
                if branch_existed:
                    # Check the existing branch out. Never re-cut it from
                    # base: a killed run's commits live on that branch and
                    # re-cutting would silently discard them.
                    argv = ["-C", repo_path, "worktree", "add", worktree_path, branch]
                else:
                    argv = [
                        "-C",
                        repo_path,
                        "worktree",
                        "add",
                        worktree_path,
                        "-b",
                        branch,
                        resolved_base,
                    ]
                git_runner(argv)
                created = True
```

- [ ] **Step 6: Document the lock in the module docstring**

In `src/agent_manager/steps/worktree.py`, replace the last paragraph of the module docstring (lines 13-15):

```python
Every invocation is an argument list handed to `subprocess` (design §5 line
252): there is no shell string and nothing to quote.
"""
```

with:

```python
Every invocation is an argument list handed to `subprocess` (design §5 line
252): there is no shell string and nothing to quote.

Parallel lanes (parallel-stories decision P3) share one repository, so
`git worktree add` runs under a per-repository lock, re-checking registration
inside it so two threads ensuring the same worktree both succeed. Threads in
one process only. There is deliberately no `git worktree prune`: it is a global
sweep that could remove another lane's not-yet-populated worktree.
"""
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/steps/test_worktree.py -k "eight_distinct_lanes or same_worktree_both_succeed" -v`
Expected: both PASS.

- [ ] **Step 8: Write the Review Focus guard tests**

These pin error paths and lock scoping. They exercise behavior that must hold after Step 5; the lock-scoping ones use a worker thread with a join timeout so a regression fails instead of hanging. Append to the end of `tests/steps/test_worktree.py`:

```python
def _finishes_while_held(lock: threading.Lock, call) -> tuple[bool, object]:
    """Run `call` in a thread while `lock` is held; report whether it finished."""
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["result"] = call()
        except BaseException as exc:  # surfaced to the test, not swallowed
            outcome["result"] = exc

    with lock:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=10)
        finished = not thread.is_alive()
    thread.join(timeout=30)
    return finished, outcome.get("result")


@requires_git
def test_a_failed_add_releases_the_repository_lock(repo: Path, tmp_path: Path):
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "stray.txt").write_text("not a worktree\n")

    with pytest.raises(GitError):
        worktree.ensure(
            branch="m4/stray",
            base="main",
            worktree=str(stray),
            repo_dir=str(repo),
        )

    assert worktree._repo_lock(str(repo)).locked() is False
    result = worktree.ensure(
        branch="m4/after",
        base="main",
        worktree=str(tmp_path / "wt-after"),
        repo_dir=str(repo),
    )
    assert result["created"] is True


@requires_git
def test_a_same_branch_at_a_different_path_surfaces_gits_error_and_frees_the_lock(
    repo: Path, tmp_path: Path
):
    worktree.ensure(
        branch="m4/shared",
        base="main",
        worktree=str(tmp_path / "wt-one"),
        repo_dir=str(repo),
    )

    with pytest.raises(GitError) as excinfo:
        worktree.ensure(
            branch="m4/shared",
            base="main",
            worktree=str(tmp_path / "wt-two"),
            repo_dir=str(repo),
        )

    assert "add" in excinfo.value.argv
    assert worktree._repo_lock(str(repo)).locked() is False


@requires_git
def test_a_different_repository_is_never_blocked_by_this_ones_lock(
    repo: Path, tmp_path: Path
):
    other = _init_repo(tmp_path / "other-repo")

    finished, result = _finishes_while_held(
        worktree._repo_lock(str(repo)),
        lambda: worktree.ensure(
            branch="m4/other",
            base="main",
            worktree=str(tmp_path / "wt-other"),
            repo_dir=str(other),
        ),
    )

    assert finished is True
    assert isinstance(result, dict) and result["created"] is True


@requires_git
def test_an_existing_worktree_takes_no_lock(repo: Path, tmp_path: Path):
    args = {
        "branch": "m4/resume",
        "base": "main",
        "worktree": str(tmp_path / "wt-resume"),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)

    finished, result = _finishes_while_held(
        worktree._repo_lock(str(repo)),
        lambda: worktree.ensure(**args),
    )

    assert finished is True
    assert isinstance(result, dict) and result["created"] is False
```

- [ ] **Step 9: Run the whole worktree test file**

Run: `uv run pytest tests/steps/test_worktree.py -v`
Expected: all PASS, including the untouched `test_an_existing_worktree_add_is_never_attempted_a_second_time`, `test_the_origin_probe_is_recorded_and_its_failure_is_not_an_error` (probe still `calls[2]`), `test_no_forbidden_git_operation_runs_on_any_path` and `test_bad_arguments_raise_before_any_git_invocation`.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/steps/worktree.py tests/steps/test_worktree.py
git commit -m "Serialize git worktree add per repository with an in-lock re-check"
```

---

### Task 3: Full-suite verification

**Files:**
- None modified.

**Interfaces:**
- Consumes: everything from Tasks 1-2.
- Produces: nothing new.

- [ ] **Step 1: Confirm no prune was introduced**

Run: `git grep -n "prune" -- src/agent_manager/steps/worktree.py`
Expected: only the module docstring lines (the forbidden-operations list and the new "no `git worktree prune`" paragraph); no argv contains `"prune"`.

- [ ] **Step 2: Confirm scope**

Run: `git diff --stat m4/task-make-the-sqlite-5657f0d4...HEAD`
Expected: only `src/agent_manager/steps/worktree.py`, `tests/steps/test_worktree.py` and the docs under `docs/superpowers/` changed.

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest`
Expected: all tests PASS, including `tests/e2e` and any `--max-concurrent 1` behavior tests. If anything outside `tests/steps/test_worktree.py` fails, compare against the base branch before touching code: a failure there that is independent of `worktree.py` is not in scope for this card.

- [ ] **Step 4: Run the concurrency tests repeatedly to check for flakiness**

Run: `for i in 1 2 3 4 5; do uv run pytest tests/steps/test_worktree.py -k "eight_distinct_lanes or same_worktree_both_succeed or lock" -q || break; done`
Expected: every run PASS.
