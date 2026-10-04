<!-- task-pipeline: validated -->
# Subtask cf03b236 — `worktree.ensure` recreates a stale-registered-but-missing path

Parent: ab65eee1 ("A resume survives a worktree deleted outside am's bookkeeping"), milestone 246a77c3. Narrows §3.3 and §4 items 7-10 of `docs/superpowers/specs/2026-10-03-resume-worktree-reensure-design.md` to one function.

## Scope

Only `src/agent_manager/steps/worktree.py` (`ensure`) and its tests: new and extended tests in `tests/steps/test_worktree.py`, plus one new test in `tests/test_bases.py` that exercises the shared seam. No edits to `bases.py`, `runtime/engine.py`, `walk.py`, `cli.py`, `orchestrate.py`, the README, or any other test file. Those belong to siblings f76af5b2 (engine resume re-ensure/decline) and dd932306 (`SubtaskSummary.resumed_at`).

## Behavior

`ensure` gets one new rule: a registered worktree path that is not a directory on disk counts as not existing.

- `worktree_existed = _is_registered(path, registered) and Path(path).is_dir()`. The same rule applies at the unlocked initial check and again at the re-check under `git_lock(repo_path)`, so the double-checked locking against racing lanes keeps working. The add itself stays inside the lock.
- When the re-check under the lock sees a stale registration (registered, but no directory), the add carries `-f`:
  - branch survives: `["-C", repo, "worktree", "add", "-f", path, branch]`. This checks out the existing branch and never re-cuts it from base, so the commits of a hand-`rm -rf`'d worktree survive.
  - branch also gone: `["-C", repo, "worktree", "add", "-f", path, "-b", branch, resolved_base]`.
- `-f` is passed only in the stale-registration case. A clean add (path not registered at all) uses exactly today's argv. That way git still refuses when the same branch is checked out live at another path, and that error still surfaces as `GitError` with the lock released.
- A registered path whose directory exists keeps today's behavior: `worktree_existed=True`, `created=False`, no add, no lock taken.
- The result dict keeps exactly its current keys (`branch`, `worktree`, `branch_existed`, `worktree_existed`, `created`, `commit_count`). In the recovery case it reports `worktree_existed=False, created=True`. No key is added, and callers read nothing new.

## Invariants (unchanged, must still hold)

- This step never runs `worktree prune`, `worktree remove`, `reset`, `checkout -f`, `clean`, `commit`, or `push`. `-f` on `worktree add` is not on that list. It is git's documented override for a "missing but already registered" path, and it overrides bookkeeping only, never branch content.
- It stays deterministic: no model calls and no board access.
- It returns a plain dict, not Pydantic, because the result crosses no process boundary.
- Every `worktree add` runs serialized under `git_lock(repo_path)`.

## Error paths

- Any git failure on the `-f` add propagates as `GitError` and releases the lock, the same as the existing add.
- Under `-f`, a branch checked out live elsewhere is not a concern for the stale case: the only other registration of the branch is the dead one being replaced.

## Tests

Placement rule (CLAUDE.md): a test's tier is set by what it spawns, not by its directory. Every test below spawns real `git` in `tmp_path` and nothing else (no `brd`, no `claude`), so all of them are **`git` tier**. In `tests/steps/` the conftest auto-marks them `git`, which matches. In `tests/test_bases.py` the test needs an explicit `@pytest.mark.git`, as its neighbours already have.

1. `test_worktree.py`, new (git tier): **`rm -rf`'d worktree, branch survives.** `ensure` once, commit on the branch, `shutil.rmtree` the worktree dir, then `ensure` again through a recording runner. Expect `branch_existed=True, worktree_existed=False, created=True`. Also expect: the directory exists, `git -C <wt> log` shows the commit (not re-cut), the recorded `worktree add` argv contains `-f` and no `-b`, and `git worktree list --porcelain` has no `prunable` line afterwards.
2. `test_worktree.py`, new (git tier): **`rm -rf`'d worktree, branch also deleted.** Same setup, then `git update-ref -d refs/heads/<branch>`. The test also asserts that `git branch -D` is refused while the stale registration stands, which explains why `update-ref` is used. Expect `branch_existed=False, created=True`, argv with both `-f` and `-b`, and the branch re-cut from base (`commit_count == 0`).
3. `test_worktree.py`, new (git tier): **cleanly removed worktree** (`git worktree remove` + `git branch -D`). Expect the plain `-b` add with no `-f` in any argv. This pins the existing behavior.
4. `test_worktree.py`, extend `test_no_forbidden_git_operation_runs_on_any_path` (git tier): additionally assert that no recorded argv contains `-f` across the fresh-add, existing-branch-resume and second-identical-call paths. The existing forbidden-token assertions stay as they are.
5. `tests/test_bases.py`, new, `@pytest.mark.git`: **merged-base worktree recovered through `bases.build`.** Build once so the merged-base worktree exists, `shutil.rmtree` it, then build again. The resolver at `bases.py:293` calls the same `ensure` and recreates the worktree with the merged-base branch intact. `bases.py` gets zero code changes.

---

# `worktree.ensure` Stale-Registration Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `worktree.ensure` treat a worktree path that git still has registered but whose directory is gone as not existing, and re-add it with `worktree add -f` (keeping a surviving branch intact), without changing any clean-add argv.

**Architecture:** One new private predicate `_is_live_worktree(candidate, registered)` (`_is_registered(...) and Path(candidate).is_dir()`) replaces `_is_registered` at both the unlocked check and the re-check under `git_lock` in `ensure`. Inside the lock, when the path is still registered but not live, a single `-f` is spliced into the existing `worktree add` argv right after `add`; a clean add keeps exactly today's argv. `bases.build` calls the same `ensure` at `bases.py:293`, so it inherits the recovery with no code change there.

**Tech Stack:** Python 3.12, pytest (asyncio auto mode, already used by `tests/test_bases.py`), real `git` in `tmp_path`, `uv`.

**Spec:** `docs/superpowers/specs/task-worktree-ensure-cf03b236-design.md` (prepended verbatim above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-worktree-ensure-cf03b236` (branch `m19/task-worktree-ensure-cf03b236`, cut fresh from `origin/master`). Run every command from that directory. Nothing from sibling subtasks f76af5b2 or dd932306 exists on this branch and nothing here depends on them.

## Global Constraints

- Files that may change: `src/agent_manager/steps/worktree.py`, `tests/steps/test_worktree.py`, `tests/test_bases.py`. Nothing else — not `bases.py`, `runtime/engine.py`, `walk.py`, `cli.py`, `orchestrate.py`, the README, or any other test file.
- `ensure` never runs `worktree prune`, `worktree remove`, `reset`, `checkout -f`, `clean`, `commit`, or `push`.
- `-f` is passed only when the re-check under the lock sees the path registered but not a directory. A clean add uses exactly today's argv.
- A surviving branch is never re-cut from base: stale + branch exists → `["-C", repo, "worktree", "add", "-f", path, branch]`; stale + branch gone → `["-C", repo, "worktree", "add", "-f", path, "-b", branch, resolved_base]`.
- Result dict keys stay exactly `branch`, `worktree`, `branch_existed`, `worktree_existed`, `created`, `commit_count`; recovery reports `worktree_existed=False, created=True`.
- A registered path whose directory exists: `worktree_existed=True`, `created=False`, no add, no lock taken.
- Every `worktree add` runs under `git_lock(repo_path)`; any git failure propagates as `GitError` with the lock released.
- Deterministic step: no model calls, no board access; plain dict return (no Pydantic).
- Test tier: every test in this plan spawns only real `git` in `tmp_path` → `git` tier. `tests/steps/` is auto-marked `git` by `tests/conftest.py`; tests in `tests/test_bases.py` carry an explicit `@pytest.mark.git`.
- Verification: `uv run pytest`.

## Review Focus

- Two lanes ensuring the same `rm -rf`'d worktree at once: both must succeed, exactly one `worktree add -f` runs, the loser reports `worktree_existed=True`, and the branch's commits survive. Pinned by `test_two_threads_recovering_the_same_rm_rf_worktree_both_succeed` in Task 2.
- The stale path is now occupied by a regular file (someone put something there): `-f` must not clobber it; git's "already exists" refusal surfaces as `GitError` with the `-f` add argv, the file is untouched, and both lock layers are free. Pinned by `test_a_stale_path_now_occupied_by_a_file_surfaces_gits_error_and_frees_the_lock` in Task 2.
- Recovering one stale worktree while a live sibling and a second, unrelated stale worktree exist: neither may be pruned or touched (no global sweep). Pinned by `test_recovering_a_stale_worktree_leaves_every_other_worktree_registered` in Task 2.
- A call right after a recovery must be the ordinary no-op (no second add, `created=False`). Pinned by the trailing assertions of `test_an_rm_rf_worktree_whose_branch_survives_is_re_added_with_its_commits` in Task 2.

---

### Task 1: Pin the clean-add argv (no `-f` on any clean path)

These are characterization tests: they pass on today's code and must keep passing after Task 2. They exist so the Task 2 change cannot silently add `-f` to a clean add (which would mask git's refusal when the same branch is live at another path).

**Files:**
- Modify: `tests/steps/test_worktree.py` (imports at lines 9-14; `test_no_forbidden_git_operation_runs_on_any_path` at lines 545-581; append new test at end of file, after line 931)

**Interfaces:**
- Consumes: `worktree.ensure(branch, base, worktree, repo_dir, git_runner=run_git) -> dict[str, object]`; existing test helpers `_git`, `_recorder`, `_is_add` (line 622), `_assert_no_forbidden_git` (line 537).
- Produces: test helper `_adds(calls: list[list[str]]) -> list[list[str]]` (the recorded `worktree add` argvs, in order), used again in Task 2.

- [ ] **Step 1: Write the clean-removal pin test and the `_adds` helper**

Append to the end of `tests/steps/test_worktree.py`:

```python
# --- Stale-registered-but-missing worktrees (card cf03b236) -----------------


def _adds(calls: list[list[str]]) -> list[list[str]]:
    """The recorded `worktree add` argvs, in call order."""
    return [argv for argv in calls if _is_add(argv)]


def test_a_cleanly_removed_worktree_and_branch_take_the_plain_add(
    repo: Path, tmp_path: Path
):
    # `git worktree remove` + `git branch -D` leave no registration behind, so
    # this is an ordinary clean add: today's argv exactly, and never `-f`.
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)
    _git(repo, "worktree", "remove", str(wt))
    _git(repo, "branch", "-D", "m1/task-9")

    calls: list[list[str]] = []
    result = worktree.ensure(**args, git_runner=_recorder(calls, worktree.run_git))

    assert result["branch_existed"] is False
    assert result["worktree_existed"] is False
    assert result["created"] is True
    assert _adds(calls) == [
        ["-C", str(repo), "worktree", "add", str(wt), "-b", "m1/task-9", "main"]
    ]
    assert not any("-f" in argv or "--force" in argv for argv in calls), calls
    assert wt.is_dir()
```

- [ ] **Step 2: Extend `test_no_forbidden_git_operation_runs_on_any_path`**

In `tests/steps/test_worktree.py`, replace the tail of that test (lines 577-581):

```python
    assert fresh["created"] is True
    assert resumed["branch_existed"] is True
    assert again["created"] is False
    _assert_no_forbidden_git(calls)
    assert (tmp_path / "wt-8" / "prior.txt").is_file()
```

with:

```python
    assert fresh["created"] is True
    assert resumed["branch_existed"] is True
    assert again["created"] is False
    _assert_no_forbidden_git(calls)
    assert (tmp_path / "wt-8" / "prior.txt").is_file()
    # Every add above is a clean add (no stale registration anywhere), so none
    # may carry `-f`: it would mask git's refusal of a branch live elsewhere.
    assert len(_adds(calls)) == 2
    assert not any("-f" in argv or "--force" in argv for argv in calls), calls
```

(`_adds` is defined lower in the module; it is resolved at call time, so the order is fine.)

- [ ] **Step 3: Run the pin tests (expected PASS on today's code)**

Run: `uv run pytest tests/steps/test_worktree.py::test_a_cleanly_removed_worktree_and_branch_take_the_plain_add tests/steps/test_worktree.py::test_no_forbidden_git_operation_runs_on_any_path -v`
Expected: 2 passed. These are characterization pins of current behavior, so they pass now; if either fails, stop — the baseline is not what the spec assumes.

- [ ] **Step 4: Commit**

```bash
git add tests/steps/test_worktree.py
git commit -m "test: pin that a clean worktree add never carries -f"
```

---

### Task 2: `ensure` re-adds a stale-registered-but-missing worktree

**Files:**
- Modify: `src/agent_manager/steps/worktree.py:125-137` (add `_is_live_worktree` after `_is_registered`), `src/agent_manager/steps/worktree.py:212-291` (`ensure`)
- Modify: `tests/steps/test_worktree.py` (add `import shutil` to the imports at lines 9-14; append tests at end of file, after Task 1's test)
- Modify: `tests/test_bases.py` (add `import shutil` to the imports at lines 17-28; add one test after `test_a_removed_base_worktree_is_re_added_and_nothing_is_re_merged`, which ends at line 284)

**Interfaces:**
- Consumes: `_adds(calls)` from Task 1; existing helpers in `tests/steps/test_worktree.py` (`_git`, `_head`, `_commit`, `_recorder`, `_is_add`, `_assert_no_forbidden_git`, `_repo_lock_is_free`, `_probe`); existing helpers in `tests/test_bases.py` (`_build`, `_git`, `rev`, `base_worktree`, `BASE`, fixtures `two_story_repo`, `MASTER_BEFORE`).
- Produces: `worktree._is_live_worktree(candidate: str, registered: list[str]) -> bool`. `ensure`'s signature and return keys are unchanged.

- [ ] **Step 1: Add the `shutil` import to `tests/steps/test_worktree.py`**

Change lines 9-14 from:

```python
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
```

to:

```python
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
```

- [ ] **Step 2: Write the failing recovery tests in `tests/steps/test_worktree.py`**

Append to the end of `tests/steps/test_worktree.py` (after Task 1's test):

```python
def _rm_rf_after_a_commit(repo: Path, wt: Path, branch: str) -> str:
    """Ensure `branch` at `wt`, commit on it, then `rm -rf` the directory.

    git keeps the registration (it shows up `prunable`), which is exactly what
    a worktree deleted outside am's bookkeeping looks like. Returns the
    commit's sha.
    """
    worktree.ensure(branch=branch, base="main", worktree=str(wt), repo_dir=str(repo))
    head = _commit(wt, "prior.txt", "work from before the rm -rf\n")
    shutil.rmtree(wt)
    assert "prunable" in _git(repo, "worktree", "list", "--porcelain")
    return head


def test_an_rm_rf_worktree_whose_branch_survives_is_re_added_with_its_commits(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    prior_head = _rm_rf_after_a_commit(repo, wt, "m1/task-9")
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }

    calls: list[list[str]] = []
    result = worktree.ensure(**args, git_runner=_recorder(calls, worktree.run_git))

    assert result == {
        "branch": "m1/task-9",
        "worktree": str(wt),
        "branch_existed": True,
        "worktree_existed": False,
        "created": True,
        "commit_count": 1,
    }
    assert wt.is_dir()
    # Checked out, never re-cut from base: the commit is still there.
    assert _head(wt) == prior_head
    assert "add prior.txt" in _git(wt, "log", "--format=%s")
    assert (wt / "prior.txt").read_text() == "work from before the rm -rf\n"
    assert _adds(calls) == [
        ["-C", str(repo), "worktree", "add", "-f", str(wt), "m1/task-9"]
    ]
    assert "prunable" not in _git(repo, "worktree", "list", "--porcelain")
    _assert_no_forbidden_git(calls)

    # Once recovered, the next call is the ordinary no-op.
    again_calls: list[list[str]] = []
    again = worktree.ensure(**args, git_runner=_recorder(again_calls, worktree.run_git))
    assert again["worktree_existed"] is True
    assert again["created"] is False
    assert _adds(again_calls) == []


def test_an_rm_rf_worktree_whose_branch_is_also_gone_is_re_cut_from_base(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    _rm_rf_after_a_commit(repo, wt, "m1/task-9")
    # git refuses `branch -D` while the stale registration still claims the
    # branch, which is why the branch is deleted through `update-ref` here.
    refused = subprocess.run(
        ["git", "-C", str(repo), "branch", "-D", "m1/task-9"],
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    _git(repo, "update-ref", "-d", "refs/heads/m1/task-9")

    calls: list[list[str]] = []
    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=_recorder(calls, worktree.run_git),
    )

    assert result == {
        "branch": "m1/task-9",
        "worktree": str(wt),
        "branch_existed": False,
        "worktree_existed": False,
        "created": True,
        "commit_count": 0,
    }
    assert _adds(calls) == [
        ["-C", str(repo), "worktree", "add", "-f", str(wt), "-b", "m1/task-9", "main"]
    ]
    assert _head(wt) == _head(repo)
    assert not (wt / "prior.txt").exists()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "m1/task-9"
    assert "prunable" not in _git(repo, "worktree", "list", "--porcelain")
    _assert_no_forbidden_git(calls)


def test_two_threads_recovering_the_same_rm_rf_worktree_both_succeed(
    repo: Path, tmp_path: Path
):
    # Both lanes see the stale registration unlocked; the re-check under the
    # git lock must make the second one see the first one's live worktree.
    wt = tmp_path / "wt"
    prior_head = _rm_rf_after_a_commit(repo, wt, "m4/same")
    start = threading.Barrier(2)
    after_reads = threading.Barrier(2)
    adds: list[list[str]] = []
    adds_lock = threading.Lock()

    def runner(argv: list[str]) -> str:
        if _is_add(argv):
            with adds_lock:
                adds.append(list(argv))
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
    assert adds == [["-C", str(repo), "worktree", "add", "-f", str(wt), "m4/same"]]
    assert _head(wt) == prior_head
    registered = worktree.worktree_paths(
        _git(repo, "worktree", "list", "--porcelain")
    )
    real_wt = os.path.realpath(wt)
    assert sum(os.path.realpath(p) == real_wt for p in registered) == 1


def test_a_stale_path_now_occupied_by_a_file_surfaces_gits_error_and_frees_the_lock(
    repo: Path, tmp_path: Path
):
    # `-f` overrides git's bookkeeping, never something on disk: a file now
    # sitting at the stale path must be refused by git and left untouched.
    wt = tmp_path / "wt"
    _rm_rf_after_a_commit(repo, wt, "m4/occupied")
    wt.write_text("someone else's file\n")

    with pytest.raises(GitError) as excinfo:
        worktree.ensure(
            branch="m4/occupied",
            base="main",
            worktree=str(wt),
            repo_dir=str(repo),
        )

    assert "add" in excinfo.value.argv
    assert "-f" in excinfo.value.argv
    assert wt.read_text() == "someone else's file\n"
    assert _repo_lock_is_free(repo)
    assert _probe(repo, "git") == "free"


def test_recovering_a_stale_worktree_leaves_every_other_worktree_registered(
    repo: Path, tmp_path: Path
):
    # `-f` replaces only this path's dead registration. A live sibling and an
    # unrelated stale worktree must both survive: no prune-style sweep.
    sibling = tmp_path / "sibling-wt"
    _git(repo, "worktree", "add", str(sibling), "-b", "m1/task-8")
    other_stale = tmp_path / "other-stale-wt"
    _git(repo, "worktree", "add", str(other_stale), "-b", "m1/task-7")
    shutil.rmtree(other_stale)
    wt = tmp_path / "wt"
    _rm_rf_after_a_commit(repo, wt, "m1/task-9")

    calls: list[list[str]] = []
    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=_recorder(calls, worktree.run_git),
    )

    assert result["created"] is True
    registered = [
        os.path.realpath(p)
        for p in worktree.worktree_paths(_git(repo, "worktree", "list", "--porcelain"))
    ]
    assert os.path.realpath(sibling) in registered
    assert os.path.realpath(other_stale) in registered
    assert os.path.realpath(wt) in registered
    assert sibling.is_dir()
    assert not other_stale.exists()
    _assert_no_forbidden_git(calls)
```

- [ ] **Step 3: Add the `shutil` import to `tests/test_bases.py`**

Change lines 22-23 from:

```python
import subprocess
import sys
```

to:

```python
import shutil
import subprocess
import sys
```

- [ ] **Step 4: Write the failing `bases.build` recovery test**

In `tests/test_bases.py`, insert directly after `test_a_removed_base_worktree_is_re_added_and_nothing_is_re_merged` (which ends at line 284) and before `test_a_tip_given_as_a_sha_is_merged_and_reported_as_given`:

```python
@pytest.mark.git
async def test_an_rm_rf_base_worktree_is_re_added_and_nothing_is_re_merged(
    two_story_repo: Path, MASTER_BEFORE: str
):
    # The merged-base worktree deleted outside am's bookkeeping: git still has
    # it registered. `build` reaches the same `worktree.ensure` seam, which
    # re-adds it on the intact base branch; bases.py itself is unchanged.
    repo = two_story_repo
    await _build(repo, ["m7/a", "m7/b"])
    built = rev(repo, BASE)
    shutil.rmtree(base_worktree(repo))
    assert "prunable" in _git(repo, "worktree", "list", "--porcelain")

    result = await _build(repo, ["m7/a", "m7/b"])

    assert result == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/b"], resolved=[]
    )
    wt = base_worktree(repo)
    assert wt.is_dir()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == BASE
    assert rev(repo, BASE) == built
    assert rev(repo, "master") == MASTER_BEFORE
    assert "prunable" not in _git(repo, "worktree", "list", "--porcelain")
```

- [ ] **Step 5: Run the new tests to verify they fail**

Run: `uv run pytest tests/steps/test_worktree.py -k "rm_rf or stale_path_now_occupied or recovering_a_stale" tests/test_bases.py::test_an_rm_rf_base_worktree_is_re_added_and_nothing_is_re_merged -v`
Expected: 6 failed. Today `ensure` sees the path registered, reports `worktree_existed=True` without adding, then `_commit_count` runs `git -C <missing wt> rev-list ...` and raises `GitError` (`cannot change to '<wt>'`). The occupied-file test fails its `"add" in excinfo.value.argv` assertion for the same reason (the `GitError` comes from `rev-list`, not an add).

- [ ] **Step 6: Add the `_is_live_worktree` predicate**

In `src/agent_manager/steps/worktree.py`, insert directly after `_is_registered` (after line 137, before `def _commit_count`):

```python
def _is_live_worktree(candidate: str, registered: list[str]) -> bool:
    """Whether `candidate` is registered with git AND still a directory on disk.

    A worktree deleted by hand (`rm -rf`) stays registered until something
    prunes it; that stale registration must count as not existing, or
    `ensure` would report a worktree that is not there.
    """
    return _is_registered(candidate, registered) and Path(candidate).is_dir()
```

- [ ] **Step 7: Use it in `ensure` and add `-f` only for the stale case**

In `src/agent_manager/steps/worktree.py`, replace line 246:

```python
    worktree_existed = _is_registered(worktree_path, registered)
```

with:

```python
    worktree_existed = _is_live_worktree(worktree_path, registered)
```

Then replace lines 259-282:

```python
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

with:

```python
            registered = worktree_paths(
                git_runner(["-C", repo_path, "worktree", "list", "--porcelain"])
            )
            if _is_live_worktree(worktree_path, registered):
                worktree_existed = True
            else:
                # Still registered but not a directory: deleted by hand. `-f`
                # is git's override for exactly that "missing but already
                # registered" refusal; it touches bookkeeping, never branch
                # content. A clean add never gets it, so git still refuses a
                # branch that is checked out live at another path.
                force = ["-f"] if _is_registered(worktree_path, registered) else []
                if branch_existed:
                    # Check the existing branch out. Never re-cut it from
                    # base: a killed run's commits live on that branch and
                    # re-cutting would silently discard them.
                    argv = [
                        "-C",
                        repo_path,
                        "worktree",
                        "add",
                        *force,
                        worktree_path,
                        branch,
                    ]
                else:
                    argv = [
                        "-C",
                        repo_path,
                        "worktree",
                        "add",
                        *force,
                        worktree_path,
                        "-b",
                        branch,
                        resolved_base,
                    ]
                git_runner(argv)
                created = True
```

Then extend the `ensure` docstring (lines 219-225) so it reads:

```python
    """Make sure `branch`'s worktree exists at `worktree`, and report what was there.

    `branch` and `base` arrive already computed by `dag.py` and inlined
    (design §7); this module never derives a branch name. The return value is
    the deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).

    A path git still has registered but whose directory is gone counts as not
    existing: it is re-added with `worktree add -f`, checking a surviving
    branch out rather than re-cutting it (card cf03b236).
    """
```

- [ ] **Step 8: Run the new tests to verify they pass**

Run: `uv run pytest tests/steps/test_worktree.py -k "rm_rf or stale_path_now_occupied or recovering_a_stale" tests/test_bases.py::test_an_rm_rf_base_worktree_is_re_added_and_nothing_is_re_merged -v`
Expected: 6 passed.

- [ ] **Step 9: Run the whole worktree and bases files, including Task 1's pins**

Run: `uv run pytest tests/steps/test_worktree.py tests/test_bases.py -v`
Expected: all pass, in particular `test_a_cleanly_removed_worktree_and_branch_take_the_plain_add`, `test_no_forbidden_git_operation_runs_on_any_path`, `test_a_same_branch_at_a_different_path_surfaces_gits_error_and_frees_the_lock`, `test_an_existing_worktree_takes_no_lock` and `test_two_threads_ensuring_the_same_worktree_both_succeed`.

- [ ] **Step 10: Run the default suite**

Run: `uv run pytest`
Expected: all pass (default `unit` + `git` tiers).

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/steps/worktree.py tests/steps/test_worktree.py tests/test_bases.py
git commit -m "fix: worktree.ensure re-adds a stale-registered-but-missing worktree with -f"
```
