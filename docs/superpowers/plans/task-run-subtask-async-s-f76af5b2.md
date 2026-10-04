<!-- task-pipeline: validated -->
# Resume re-ensures the worktree in `run_subtask_async` (subtask f76af5b2) — design

Parent story: ab65eee1 "A resume survives a worktree deleted outside am's bookkeeping". Narrowed from `docs/superpowers/specs/2026-10-03-resume-worktree-reensure-design.md` §§3.1, 3.2, 3.4, the engine-side half of §3.5, §3.7 and §4 items 1-6. That document stays the source of truth; this one only fixes what this subtask delivers.

## Scope

In scope:

- `src/agent_manager/runtime/engine.py`: the worktree re-check inside `run_subtask_async`'s `resume_from is not None` branch, and the new `ensure_worktree` keyword on `run_subtask` and `run_subtask_async`.
- `src/agent_manager/runtime/walk.py`: the new field `SubtaskSummary.resumed_at: str | None = None`, set by the engine.
- `tests/runtime/test_resume.py`: the six new unit tests below.
- Existing unit tests that resume with a worktree path that does not exist on disk: `tests/runtime/test_resume.py` (`Path("/w")` in `_subtask()`), `tests/runtime/test_exactly_once.py`, `tests/runtime/test_cancellation.py`, `tests/runtime/test_stop_bridge.py`, and the `task`-run resume tests in `tests/test_cli.py` built on `Path("/repo/...")`. Each one gets a real `tmp_path` directory or an injected fake `ensure_worktree`, so it keeps testing the path it tested before and does not fall into the decline path. `tests/test_orchestrate.py` needs no change because it never reaches the engine.

Out of scope:

- `src/agent_manager/steps/worktree.py`. The `-f` fix for a path that is registered but missing already landed in sibling cf03b236 (done). This subtask reads `ensure`'s result keys as they are: the five keys are unchanged and only `branch_existed` is read.
- Reading `resumed_at` (`am resume`'s `resumed_from`, the lane's `(resumed at <phase>)` comment via `compose_done`) and the README "Resuming: what runs again" docs. Sibling dd932306 owns these.
- The git-tier `worktree.py` tests (§4 items 7-10), which belong to cf03b236. Note: the exploration summary was cut off at this point (truncated at 8000 chars). It did not say who owns the `e2e_fake` scenario (§4 item 11). This spec does not claim it. Its assertion on `data.resumed_from` depends on dd932306's plumbing, so it fits that subtask or a later one better than this one.
- `cli.py`, `orchestrate.py` and `bases.py` do not change (§3.7, one call site).

## Observable behavior

`run_subtask(...)` and `run_subtask_async(...)` gain the keyword `ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure`. It sits next to `agent_runner`/`clock`, and `run_subtask` forwards it. No caller in `src/` passes it.

In the resume branch the order is: (1) the digest check, unchanged, raising `CheckpointMismatch` before anything else; (2) the new worktree re-check; (3) the existing resume path (`_forget`, `Agent.from_dict`, `agent.resume()`, the adoption floor) if the checkpoint is kept, or (4) the existing fresh-walk path, used verbatim, if it is declined. The fresh-walk path builds a new `Agent`, adds the seed item from `binding` and `compiled.first_turn()`, and sets `adopt=None`.

With `W = subtask.worktree_path` and `B = subtask.branch`:

| State | Git run | Outcome | Warning | `summary.resumed_at` |
|---|---|---|---|---|
| `W is None` | none | checkpoint kept | none | pending phase |
| `W` is a directory (`Path.is_dir()`) | none (fast path) | checkpoint kept | none | pending phase |
| `W` missing; `ensure_worktree(B, subtask.base_branch, W, repo_dir)` returns `branch_existed=True` | one call, on a thread via `asyncio.to_thread` | checkpoint kept; walk continues at its pending phase | re-added | pending phase |
| `W` missing; seam returns `branch_existed=False` | one call | declined; fresh walk from phase 0 | declined, branch gone | `None` |
| `W` missing; seam raises any `Exception` | one call | declined; fresh walk from phase 0, whose own `ensure` step reports the real error | declined, re-add failed | `None` |

The pending phase is `pending_phase(resume_from)`. A fresh walk with no checkpoint leaves `resumed_at` as `None`.

Warnings are worded exactly as in the milestone spec §3.4. `<seq>`/`<run-id>` follow `checkpoint_resume_phase`'s naming, `<path>` is `W`, `<phase>` is the pending phase, and the error goes through `walk._render_error`:

- `checkpoint #<seq> of run <run-id>: worktree <path> was missing and was added again for branch '<branch>'; resuming at '<phase>'`
- `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and branch '<branch>' no longer exists); starting from the first phase`
- `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and could not be added again: GitError: <message>); starting from the first phase`

Each outcome adds exactly one line, and the fast path adds none. The line is appended to the warnings list passed into `RunDeps(warnings=...)`, so `_collect` carries it into `summary.warnings` with no new plumbing.

## Error paths and invariants

- A missing worktree is a recovery, not a refusal. The re-check never raises. Every exception from the seam is caught and turned into a decline plus a warning.
- The engine does not escalate before the agent exists. When the re-add fails, phase 0 of the fresh walk runs `ensure` again under the normal step machinery. If that fails too, the subtask is escalated at `worktree` with a `GitError: ...` detail and a `failed` phase row.
- A declined checkpoint row is neither deleted nor rewritten. The fresh walk's first `turn` save at a higher `seq` replaces it as the card's newest open checkpoint.
- The digest check stays first, and a mismatch raises before the seam is called.
- The fast path spawns no subprocess. In tests every non-fast path goes through the injected seam, never through real git.

## Tests

The tier for each test follows the placement rule in CLAUDE.md "Test tiers": a test's tier is set by what it spawns or touches, not by its directory. Every test below drives the engine through `run_subtask` with an injected recording fake `ensure_worktree` and `tmp_path` directories, and spawns no subprocess. All of them are therefore unmarked (`unit`). They live in `tests/runtime/test_resume.py` and use the `_five` workflow. `_subtask()`'s worktree becomes a `tmp_path` directory instead of `Path("/w")`.

1. Intact worktree, unaffected (unit). Crash in `c`, then resume with the directory present. Assert: fake never called; `ran` is `["a","b","c"] + ["c","d","e"]`; `summary.warnings == []`; `summary.resumed_at == "c"`.
2. Worktree deleted, branch survives (unit). Remove the directory; the fake returns `branch_existed=True, worktree_existed=False, created=True`. Assert: fake called once with `(branch, base_branch, worktree_path, repo_dir)`; walk continues at `c`; exactly one warning, the re-added line; `resumed_at == "c"`; the carried adoption floor is unchanged (same assertion shape as `test_a_carried_floor_survives_a_resume`).
3. Worktree and branch both gone (unit). The fake returns `branch_existed=False, created=True`. Assert: `a` through `e` all run; exactly one warning, the branch-gone declined line; `resumed_at is None`; `deps.adopt is None`; the card's `latest_checkpoint` is newer than the declined row, and `latest_open_checkpoint` no longer returns that row.
4. Re-add raised (unit). The fake raises `GitError`, and phase 0 of the workflow is a step backed by the same raising fake. Assert: exactly one warning, the "could not be added again" line; the subtask ends `escalated` at phase 0 with a `detail` starting `GitError:`; the phase row is recorded `failed`; no exception escapes the engine.
5. No worktree path (unit). With `worktree_path=None`, assert the fake is never called and the resume is unchanged (continues at `c`, no warnings, `resumed_at == "c"`).
6. Digest first (unit). With a mismatched digest and a missing worktree, assert `CheckpointMismatch` is raised and the fake is never called.

Existing-test fixes (unit, stays unit): `test_resume.py`, `test_exactly_once.py`, `test_cancellation.py`, `test_stop_bridge.py`, and the `Path("/repo/...")` resume tests in `tests/test_cli.py` get a real `tmp_path` worktree directory or a fake `ensure_worktree`. Their existing assertions must pass unchanged. None of them may start spawning git, because that would move them out of the unit tier.

---

# Resume Re-ensures the Worktree — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `run_subtask_async` resumes a checkpoint whose worktree directory is gone, it re-adds the worktree through an injected `ensure_worktree` seam and keeps the checkpoint if the branch survived. If the branch is gone or the re-add fails, it declines the checkpoint and walks fresh from phase 0. In every case it reports one warning line and sets the new `SubtaskSummary.resumed_at`.

**Architecture:** There is one new private coroutine, `engine._worktree_kept`, called in the resume branch right after the digest check. It returns `True` (keep) or `False` (decline) and appends at most one warning to a list that is then handed to `RunDeps(warnings=...)`. A decline rebinds `resume_from = None`, so the existing fresh-walk code runs unchanged. `run_subtask_async` sets `summary.resumed_at` on the summary `_run` returns.

**Tech Stack:** Python 3, asyncio (`asyncio.to_thread`), pygents `Agent`, pytest (unit tier only).

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-run-subtask-async-s-f76af5b2/docs/superpowers/specs/task-run-subtask-async-s-f76af5b2-design.md` (prepended above). The milestone source of truth is `docs/superpowers/specs/2026-10-03-resume-worktree-reensure-design.md` §§3.1, 3.2, 3.4, 3.5, 3.7, 4.

**Branch / worktree:** `m19/task-run-subtask-async-s-f76af5b2` at `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-run-subtask-async-s-f76af5b2`, cut from `m19/task-worktree-ensure-cf03b236`. All paths below are relative to that worktree. The only code this plan relies on from another subtask is cf03b236's `steps/worktree.ensure`, which is already on the base branch. Nothing from dd932306 exists here.

**Notes on upstream inputs:**
- Both upstream summaries this plan was given were truncated: the spec summary at 2000 chars and the exploration summary at 8000 chars, cut off mid-sentence about test ownership. That upstream stage over-ran its brief. This plan works only from the spec on disk and the code it read, and does not guess at the missing text.
- The exploration findings named `tests/test_cli.py:297` and `:1576` as resume tests that need fixing. Reading the code shows neither one reaches the real engine with a missing worktree:
  - Line 297 (`test_the_status_payload_survives_render_with_its_paths`) is a pure status-render test.
  - Lines 1573-1598 replace `runtime_engine.run_subtask_async` with a stub.
  - Every `test_cli.py` test that resumes through the real engine is `brd`+`git` tier, and its worktree was created on disk by the earlier, real run, so it takes the fast path.
  - Task 1 Step 6 therefore checks this instead of editing `test_cli.py`.
- `worktree.ensure` actually returns six keys (`branch`, `worktree`, `branch_existed`, `worktree_existed`, `created`, `commit_count`), not five as the spec says. This changes nothing: the engine reads only `branch_existed`.
- The existing `tests/runtime/test_stop_bridge.py::test_the_walk_takes_a_stop_signal_and_no_other_stop` pins the exact parameter list of both walks (`WALK_PARAMETERS`). Task 2 updates it, with `ensure_worktree` placed right after `clock`.

## Global Constraints

- New keyword, exact: `ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure`, on both `run_subtask` and `run_subtask_async`, positioned right after `clock`; `run_subtask` forwards it.
- New field, exact: `SubtaskSummary.resumed_at: str | None = None`.
- The seam is called as `ensure_worktree(subtask.branch, subtask.base_branch, subtask.worktree_path, repo_dir)`, via `asyncio.to_thread`.
- Warning wording, exact (with `<seq>` = `checkpoint.seq`, `<run-id>` = `checkpoint.run_id`, `<path>` = `subtask.worktree_path`, `<phase>` = `pending_phase(checkpoint)`, `<error>` = `walk._render_error(error)`):
  - `checkpoint #<seq> of run <run-id>: worktree <path> was missing and was added again for branch '<branch>'; resuming at '<phase>'`
  - `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and branch '<branch>' no longer exists); starting from the first phase`
  - `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and could not be added again: <error>); starting from the first phase`
- At most one warning per resume; zero on the fast path (`worktree_path is None` or `Path(worktree_path).is_dir()`).
- The digest check (`CheckpointMismatch`) stays first; the seam is never called on a mismatch.
- The re-check never raises: every `Exception` from the seam becomes a decline plus a warning.
- `cli.py`, `orchestrate.py`, `bases.py` and `steps/worktree.py` are not modified.
- Every test in this plan is unit tier (unmarked): no subprocess. The unit tier's stub `git` on `PATH` exits 99, so an accidental real `worktree.ensure` call shows up as a decline warning and a failing assertion.
- Verification: `uv run pytest`.

## Review Focus

1. A checkpoint parked before phase 0, so its worktree was never created: the seam is called, the branch does not exist, and the walk declines and runs every phase from the first. A person expects the subtask to finish normally, not to be refused. Pinned in Task 3, `test_a_checkpoint_parked_before_the_worktree_existed_walks_from_the_start`.
2. The seam raises something other than `GitError`, such as `ValueError` from `ensure`'s argument validation or a lock timeout. It must still decline, warn with that error's rendering, and never escape the engine. Pinned in Task 3, `test_any_error_from_the_reensure_declines_and_never_escapes`.
3. A stale `AgentRegistry` entry under the declined checkpoint's agent name, left in the same process by a run that died before its `finally`. The keep path already `_forget`s it. The decline path must too, or the fresh `Agent(...)` is refused its name. Pinned in Task 3, `test_a_stale_registry_entry_does_not_block_a_declined_resume`. This adds one `_forget` call on the decline path. It is not a change to the fresh-walk code itself.
4. `worktree_path` exists but is a regular file, not a directory. That is not the fast path: the seam is called. Pinned in Task 3, `test_a_file_where_the_worktree_should_be_is_not_the_fast_path`.
5. A kept resume that then stops instead of finishing (`stopped` or `escalated`) still reports where it continued. `resumed_at` is set on every summary `_run` returns, not only on `done`. Pinned in Task 2, `test_resumed_at_is_reported_when_the_resumed_walk_parks_again`.

---

### Task 1: Give the engine-tier resume tests a real worktree directory

The new re-check only runs git when the worktree path is missing. Today four test files resume with `worktree_path=Path("/w")`, which does not exist. Once Task 3 lands, those tests would call the real `worktree.ensure`, hit the stub `git` (exit 99), get declined, and fail. This task moves them onto a real `tmp_path` directory first. This is a test-only refactor with no behaviour change, so there is no RED step: every touched test must pass before and after.

**Files:**
- Modify: `tests/runtime/test_resume.py:57-64`
- Modify: `tests/runtime/test_exactly_once.py:270-277`
- Modify: `tests/runtime/test_cancellation.py:76-83`
- Modify: `tests/runtime/test_stop_bridge.py:47-54`
- Verify only (no edit): `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: in each of the four files, a module global `WORKTREE: Path | None` read by `_subtask()`, and an autouse fixture `worktree_dir(tmp_path, monkeypatch) -> Path` that creates `tmp_path / "worktree"` and sets `WORKTREE` to it. Tasks 2 and 3 request `worktree_dir` by name in `tests/runtime/test_resume.py`, `rmdir()` it to simulate a deletion, and use `monkeypatch.setitem(globals(), "WORKTREE", None)` for the no-path case.

- [ ] **Step 1: Replace `_subtask()` in `tests/runtime/test_resume.py`**

Replace lines 57-64 (the current `_subtask` function) with:

```python
WORKTREE: Path | None = None
"""The subtask's worktree path. The autouse `worktree_dir` fixture sets it to a
real `tmp_path` directory for every test, so a resume takes the engine's
no-git fast path; a test may `rmdir()` that directory, or set this to `None`."""


@pytest.fixture(autouse=True)
def worktree_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    monkeypatch.setitem(globals(), "WORKTREE", path)
    return path


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m6/task-resume-a-subtask-from-{CARD_ID}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=WORKTREE,
    )
```

- [ ] **Step 2: Replace `_subtask()` in `tests/runtime/test_exactly_once.py`**

Replace lines 270-277 with:

```python
WORKTREE: Path | None = None
"""The subtask's worktree path. The autouse `worktree_dir` fixture sets it to a
real `tmp_path` directory, so a resume takes the engine's no-git fast path."""


@pytest.fixture(autouse=True)
def worktree_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    monkeypatch.setitem(globals(), "WORKTREE", path)
    return path


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m11/task-adopt-the-resumed-head-{CARD_ID}",
        base_branch="m11/story-base",
        status="started",
        worktree_path=WORKTREE,
    )
```

- [ ] **Step 3: Replace `_subtask()` in `tests/runtime/test_cancellation.py`**

Replace lines 76-83 with:

```python
WORKTREE: Path | None = None
"""The subtask's worktree path. The autouse `worktree_dir` fixture sets it to a
real `tmp_path` directory, so a resume takes the engine's no-git fast path."""


@pytest.fixture(autouse=True)
def worktree_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    monkeypatch.setitem(globals(), "WORKTREE", path)
    return path


def _subtask(card_id: str = CARD_ID) -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m8/task-pin-pygents-{card_id}",
        base_branch="m8/story-base",
        status="started",
        worktree_path=WORKTREE,
    )
```

- [ ] **Step 4: Replace `_subtask()` in `tests/runtime/test_stop_bridge.py`**

Replace lines 47-54 with:

```python
WORKTREE: Path | None = None
"""The subtask's worktree path. The autouse `worktree_dir` fixture sets it to a
real `tmp_path` directory, so a resume takes the engine's no-git fast path."""


@pytest.fixture(autouse=True)
def worktree_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    monkeypatch.setitem(globals(), "WORKTREE", path)
    return path


def _subtask(card_id: str = CARD_ID) -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m6/task-checkpoint-every-turn-{card_id}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=WORKTREE,
    )
```

- [ ] **Step 5: Run the four files and confirm nothing changed**

Run: `uv run pytest tests/runtime/test_resume.py tests/runtime/test_exactly_once.py tests/runtime/test_cancellation.py tests/runtime/test_stop_bridge.py -q`
Expected: PASS, with the same number of tests as before the edit and no new failures.

- [ ] **Step 6: Confirm `tests/test_cli.py` needs no change**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS. No edit to `tests/test_cli.py`. Here is why, so a reviewer can check it:
- `Path("/repo/.claude/worktrees/m1/a")` at line 297 is in a status-render test that never resumes.
- `DRIVE_SUBTASK` at line 1582 feeds a stubbed `run_subtask_async` (line 1598).
- The tests that resume through the real engine (`_resume_card_run`, `_park_pygents`, the `resume` CLI tests) are `@pytest.mark.brd`/`@pytest.mark.git`. They build `worktree_path=cli.worktree_for(root, branch)` under a real git `project`, and the earlier, real run created it on disk, so the engine takes the fast path.

- [ ] **Step 7: Commit**

```bash
git add tests/runtime/test_resume.py tests/runtime/test_exactly_once.py tests/runtime/test_cancellation.py tests/runtime/test_stop_bridge.py
git commit -m "test: resume engine-tier tests from a real worktree directory"
```

---

### Task 2: `SubtaskSummary.resumed_at` and the `ensure_worktree` seam (worktree present or absent-path)

This task adds the field, the keyword and the reporting of where a kept resume continued. It does not yet look at the disk. That arrives in Task 3, whose tests are the first that need it.

**Files:**
- Modify: `src/agent_manager/runtime/walk.py:241-257` (`SubtaskSummary`)
- Modify: `src/agent_manager/runtime/engine.py:12-27` (imports), `:77-111` (`run_subtask`), `:114-214` (`run_subtask_async`)
- Modify: `tests/runtime/test_stop_bridge.py:66-80` (`WALK_PARAMETERS`)
- Test: `tests/runtime/test_resume.py` (append; unit tier, unmarked)

**Interfaces:**
- Consumes: `WORKTREE`, `worktree_dir` from Task 1 (`tests/runtime/test_resume.py`).
- Produces:
  - `walk.SubtaskSummary.resumed_at: str | None = None`.
  - `engine.run_subtask(..., agent_runner=None, clock=walk._utcnow, ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure, stop=None, resume_from=None)`, and the same keyword on `engine.run_subtask_async`.
  - In `tests/runtime/test_resume.py`: `BRANCH`, `BASE_BRANCH`, `KEPT`, `GONE`, `class _FakeEnsure(result: dict[str, object] = KEPT, error: BaseException | None = None)` with `.calls: list[tuple[str, str, Path | None, Path]]` and `.error` (mutable), `__call__(branch, base, worktree, repo_dir) -> dict[str, object]`.

- [ ] **Step 1: Pin the new parameter list in `tests/runtime/test_stop_bridge.py`**

Replace the `WALK_PARAMETERS` list (lines 66-80) with:

```python
WALK_PARAMETERS = [
    "workflow",
    "store",
    "story_id",
    "subtask",
    "repo_dir",
    "commands",
    "card",
    "parent_story",
    "extra_context",
    "agent_runner",
    "clock",
    "ensure_worktree",
    "stop",
    "resume_from",
]
```

- [ ] **Step 2: Append the fake and the Task 2 tests to `tests/runtime/test_resume.py`**

Add one import next to the existing `from agent_manager.store import TurnFloor` line:

```python
from agent_manager.steps.worktree import GitError
```

Append at the end of the file:

```python
# ── a resume re-ensures a missing worktree (card f76af5b2) ───────────────────

BRANCH = f"m6/task-resume-a-subtask-from-{CARD_ID}"
BASE_BRANCH = "m6/story-base"
KEPT = {"branch_existed": True, "worktree_existed": False, "created": True}
"""What `worktree.ensure` reports after re-adding a worktree whose branch survived."""
GONE = {"branch_existed": False, "worktree_existed": False, "created": True}
"""What `worktree.ensure` reports after cutting a branch that no longer existed."""


class _FakeEnsure:
    """A recording `ensure_worktree` that never runs git.

    Called with `worktree.ensure`'s four positional arguments `(branch, base,
    worktree, repo_dir)`; records each call, then raises `error` if one is set,
    else returns `result` filled out to `ensure`'s full shape. `error` may be
    set after construction, so one fake can succeed for a first run and fail
    for the resume that follows.
    """

    def __init__(
        self, result: dict[str, object] = KEPT, error: BaseException | None = None
    ) -> None:
        self.result = dict(result)
        self.error = error
        self.calls: list[tuple[str, str, Path | None, Path]] = []

    def __call__(self, branch, base, worktree, repo_dir) -> dict[str, object]:
        self.calls.append((branch, base, worktree, repo_dir))
        if self.error is not None:
            raise self.error
        return {"branch": branch, "worktree": str(worktree), **self.result, "commit_count": 0}


def _crash_in_c(opened) -> tuple[list[str], Workflow, Any]:
    """Run `_five` until it dies in `c`; what ran, the workflow and the `turn` row."""
    ran: list[str] = []
    wf = _five(ran, {"c"})
    with pytest.raises(_Crash):
        _go(wf, opened)
    crashed = opened.latest_checkpoint(CARD_ID)
    assert crashed.reason == "turn"
    assert _head(crashed.agent) == "c"
    return ran, wf, crashed


def test_a_fresh_walk_reports_no_resume_point(store):
    summary = _go(_five([], set()), store)

    assert summary.status == "done"
    assert summary.resumed_at is None


def test_an_intact_worktree_resumes_without_touching_git(store):
    """Spec test 1: the fast path -- the directory is there, the seam is never called."""
    ran, wf, crashed = _crash_in_c(store)
    fake = _FakeEnsure()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    assert fake.calls == []
    assert ran == ["a", "b", "c", "c", "d", "e"]
    assert summary.status == "done"
    assert summary.warnings == []
    assert summary.resumed_at == "c"


def test_a_subtask_with_no_worktree_path_resumes_unchanged(store, monkeypatch):
    """Spec test 5: `worktree_path is None` is the fast path too."""
    monkeypatch.setitem(globals(), "WORKTREE", None)
    ran, wf, crashed = _crash_in_c(store)
    fake = _FakeEnsure()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    assert fake.calls == []
    assert ran == ["a", "b", "c", "c", "d", "e"]
    assert summary.status == "done"
    assert summary.warnings == []
    assert summary.resumed_at == "c"


def test_resumed_at_is_reported_when_the_resumed_walk_parks_again(store):
    """Review Focus 5: a kept resume that stops still says where it continued."""
    ran, wf, _ = _park_after_a(store)
    parked = store.latest_checkpoint(CARD_ID)
    still = StopSignal()
    still.trigger("elsewhere")

    summary = _go(wf, store, resume_from=parked, stop=still, ensure_worktree=_FakeEnsure())

    assert summary.status == "stopped"
    assert summary.resumed_at == "b"
```

- [ ] **Step 3: Run the new tests and the signature test to verify they fail**

Run: `uv run pytest tests/runtime/test_resume.py -k "resume_point or intact_worktree or no_worktree_path or parks_again" tests/runtime/test_stop_bridge.py::test_the_walk_takes_a_stop_signal_and_no_other_stop -v`
Expected: FAIL.
- `test_a_fresh_walk_reports_no_resume_point` fails with `AttributeError: 'SubtaskSummary' object has no attribute 'resumed_at'`.
- The three resume tests fail with `TypeError: run_subtask() got an unexpected keyword argument 'ensure_worktree'`.
- Both `test_the_walk_takes_a_stop_signal_and_no_other_stop` cases fail on the list comparison.

- [ ] **Step 4: Add the field to `SubtaskSummary` in `src/agent_manager/runtime/walk.py`**

After the `before_phase` field and its docstring (line 256-257), add:

```python
    resumed_at: str | None = None
    """The phase a resumed walk actually continued at; `None` for a fresh walk,
    or for a resume whose checkpoint was declined and started over from the
    first phase (resume worktree re-ensure §3.5). Set by `runtime.engine`."""
```

- [ ] **Step 5: Import the seam's default in `src/agent_manager/runtime/engine.py`**

After `from agent_manager.runtime.stop import StopSignal` (line 26), add:

```python
from agent_manager.steps import worktree
```

- [ ] **Step 6: Add and forward the keyword in `run_subtask`**

In `run_subtask`'s signature, between `clock: Callable[[], Any] = walk._utcnow,` and `stop: StopSignal | None = None,`, add:

```python
    ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure,
```

In its `run_subtask_async(...)` call, between `clock=clock,` and `stop=stop,`, add:

```python
            ensure_worktree=ensure_worktree,
```

- [ ] **Step 7: Add the keyword to `run_subtask_async`, record where a kept resume continues, and set it on the summary**

In `run_subtask_async`'s signature, between `clock: Callable[[], Any] = walk._utcnow,` and `stop: StopSignal | None = None,`, add:

```python
    ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure,
```

Append to the docstring, after the `resume_from` paragraph:

```python

    `ensure_worktree` is `steps.worktree.ensure` unless a test injects a fake:
    a resume calls it, on a worker thread, only when the subtask's worktree
    directory is missing (resume worktree re-ensure §3.1). `summary.resumed_at`
    names the phase a kept checkpoint continued at, else `None`.
```

Then replace the block from `compiled = C.compile_workflow(workflow)` through the end of the function (current lines 167-214) with:

```python
    compiled = C.compile_workflow(workflow)
    resumed_at: str | None = None
    if resume_from is None:
        agent = Agent(
            f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}",
            workflow.name,
            [compiled.agent_phase, compiled.step_phase],
            context_pool=ContextPool(),
            context_queue=ContextQueue(limit=10),
            tags=["subtask"],
        )
    else:
        digest = workflow.digest()
        if resume_from.digest != digest:
            raise CheckpointMismatch(
                f"checkpoint {resume_from.card_id}#{resume_from.seq} was saved under "
                f"digest {resume_from.digest}, but workflow {workflow.name!r} "
                f"has digest {digest}"
            )
        resumed_at = pending_phase(resume_from)
        # A run that died before its `finally` may have left its agent
        # registered under this name; `from_dict` would be refused it.
        _forget(resume_from.agent["name"])
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
        # A row parked by a `StopSignal` was saved while its agent was paused,
        # and `from_dict` restores that pause; left in place, ON_PAUSE would
        # park the resumed agent again before it ran anything. That pause
        # belonged to the stopped run: only this run's `stop`, registered in
        # `_run` after this line, may pause the agent now.
        agent.resume()
    try:
        if resume_from is None:
            await agent.context_pool.add(context.seed_item(binding))
            await agent.put(compiled.first_turn())
        # The resume checkpoint's floor is carried as the run's adoption, so a
        # re-save of that turn writes it unchanged (exactly-once E4/E5). A fresh
        # run, or a row saved with no floor, starts with none.
        adopt = (
            None
            if resume_from is None or resume_from.floor is None
            else Adoption(**vars(resume_from.floor))
        )
        deps = RunDeps(
            workflow, store, story_id, subtask, agent_runner, clock, stop=stop, adopt=adopt
        )
        summary = await _run(agent, deps)
        summary.resumed_at = resumed_at
        return summary
    finally:
        _forget(agent.name)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_resume.py tests/runtime/test_stop_bridge.py -v`
Expected: PASS, all tests, including the four new ones and both `test_the_walk_takes_a_stop_signal_and_no_other_stop` cases.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/runtime/walk.py src/agent_manager/runtime/engine.py tests/runtime/test_resume.py tests/runtime/test_stop_bridge.py
git commit -m "feat: SubtaskSummary.resumed_at and an injectable ensure_worktree seam on the engine"
```

---

### Task 3: Re-ensure a missing worktree on resume — keep or decline

**Files:**
- Modify: `src/agent_manager/runtime/engine.py` (the resume branch of `run_subtask_async`; new private `_worktree_kept`, placed directly above `_run`)
- Test: `tests/runtime/test_resume.py` (append; unit tier, unmarked)

**Interfaces:**
- Consumes: `_FakeEnsure`, `KEPT`, `GONE`, `BRANCH`, `BASE_BRANCH`, `_crash_in_c` (Task 2); `worktree_dir`, `WORKTREE` (Task 1); `SubtaskSummary.resumed_at`, `ensure_worktree` keyword (Task 2); existing helpers `_go`, `_five`, `_park_with_a_triggered_stop`, `_phase_rows`, `_head`, `_extra`, `ALL_RESULTS`, `FIVE`, `REPO`, `RUN_ID`.
- Produces: `async def _worktree_kept(checkpoint: Checkpoint, subtask: Any, repo_dir: Path, ensure_worktree: Callable[..., Mapping[str, object]], warnings: list[str]) -> bool` in `engine.py` (private). `True` means keep the checkpoint, `False` means decline. It appends at most one line to `warnings`.

- [ ] **Step 1: Append the Task 3 tests to `tests/runtime/test_resume.py`**

```python
def _re_added(seq: int, path: Path, phase: str) -> str:
    return (
        f"checkpoint #{seq} of run {RUN_ID}: worktree {path} was missing and was"
        f" added again for branch '{BRANCH}'; resuming at '{phase}'"
    )


def _branch_gone(seq: int, path: Path) -> str:
    return (
        f"checkpoint #{seq} of run {RUN_ID} was not resumed (worktree {path} is"
        f" missing and branch '{BRANCH}' no longer exists); starting from the first phase"
    )


def _re_add_failed(seq: int, path: Path, rendered: str) -> str:
    return (
        f"checkpoint #{seq} of run {RUN_ID} was not resumed (worktree {path} is"
        f" missing and could not be added again: {rendered}); starting from the first phase"
    )


def _record_adoptions(monkeypatch) -> list[tuple[str, Adoption | None, Adoption | None]]:
    """Wrap `RunDeps.take_adoption`: `(phase, adopt before the take, what it returned)` per call."""
    seen: list[tuple[str, Adoption | None, Adoption | None]] = []
    take = RunDeps.take_adoption

    def recording_take(self, phase, loop):
        before = self.adopt
        taken = take(self, phase, loop)
        seen.append((phase, before, taken))
        return taken

    monkeypatch.setattr(RunDeps, "take_adoption", recording_take)
    return seen


def test_a_deleted_worktree_whose_branch_survives_is_re_added_and_resumed(
    store, worktree_dir, monkeypatch
):
    """Spec test 2: the checkpoint is kept, and so is its carried floor."""
    ran, wf, crashed = _crash_in_c(store)
    assert _next_turn(crashed.agent)["kwargs"] == {"phase": "c", "loop": 0}
    carried = TurnFloor("c", 0, "run-earlier", 7)
    worktree_dir.rmdir()
    fake = _FakeEnsure(KEPT)
    adoptions = _record_adoptions(monkeypatch)
    ran.clear()

    summary = _go(
        wf,
        store,
        resume_from=dataclasses.replace(crashed, floor=carried),
        ensure_worktree=fake,
    )

    assert fake.calls == [(BRANCH, BASE_BRANCH, worktree_dir, REPO)]
    assert ran == ["c", "d", "e"]
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
    assert summary.warnings == [_re_added(crashed.seq, worktree_dir, "c")]
    assert summary.resumed_at == "c"
    # Same shape as test_a_carried_floor_survives_a_resume: the resumed head
    # takes the carried floor, unchanged.
    assert adoptions[0][2] == Adoption("c", 0, "run-earlier", 7)


def test_a_deleted_worktree_whose_branch_is_gone_starts_over(store, worktree_dir, monkeypatch):
    """Spec test 3: the checkpoint is declined and the walk runs from the first phase."""
    ran, wf, crashed = _crash_in_c(store)
    worktree_dir.rmdir()
    fake = _FakeEnsure(GONE)
    adoptions = _record_adoptions(monkeypatch)
    ran.clear()

    summary = _go(
        wf,
        store,
        # A floor on the declined row must not be carried into the fresh walk.
        resume_from=dataclasses.replace(crashed, floor=TurnFloor("c", 0, "run-earlier", 7)),
        ensure_worktree=fake,
    )

    assert fake.calls == [(BRANCH, BASE_BRANCH, worktree_dir, REPO)]
    assert ran == list(FIVE)
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
    assert summary.warnings == [_branch_gone(crashed.seq, worktree_dir)]
    assert summary.resumed_at is None
    # deps.adopt is None: the fresh walk's first take sees no carried floor.
    assert adoptions[0][:2] == ("a", None)
    # The declined row stays, superseded by the fresh walk's newer rows.
    assert store.latest_checkpoint(CARD_ID).seq > crashed.seq
    reopened = store.latest_open_checkpoint(CARD_ID, wf.name)
    assert reopened is None or reopened.seq != crashed.seq


def test_a_failed_re_add_starts_over_and_phase_0_escalates(store, worktree_dir):
    """Spec test 4: the engine declines and warns, it does not escalate itself;
    phase 0 of the fresh walk runs the same `ensure` and fails as an ordinary step."""
    fake = _FakeEnsure(KEPT)
    ran: list[str] = []

    def ensure_step(branch: str, base: str, worktree: Path, repo_dir: Path) -> dict[str, Any]:
        ran.append("worktree")
        return fake(branch, base, worktree, repo_dir)

    wf = Workflow("guarded", (Step("worktree", ensure_step),) + _five(ran, {"c"}).phases)
    with pytest.raises(_Crash):
        _go(wf, store)
    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "c"
    worktree_dir.rmdir()
    error = GitError("fatal: could not create work tree dir", argv=["worktree", "add"], exit_code=128)
    fake.error = error
    fake.calls.clear()
    ran.clear()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    # Once for the re-check, once as phase 0 of the fresh walk.
    assert fake.calls == [(BRANCH, BASE_BRANCH, worktree_dir, REPO)] * 2
    assert ran == ["worktree"]
    assert summary.warnings == [_re_add_failed(crashed.seq, worktree_dir, f"GitError: {error}")]
    assert summary.status == "escalated"
    assert summary.failed_phase == "worktree"
    assert summary.detail.startswith("GitError:")
    assert summary.resumed_at is None
    assert _phase_rows(store)[-2:] == [("worktree", "started"), ("worktree", "failed")]


def test_the_digest_check_runs_before_the_worktree_re_check(store, worktree_dir):
    """Spec test 6: a changed workflow is refused before the seam is ever called."""
    _, wf, crashed = _crash_in_c(store)
    changed = Workflow("five", wf.phases + (Step("f", _extra),))
    worktree_dir.rmdir()
    fake = _FakeEnsure(KEPT)

    with pytest.raises(runtime_engine.CheckpointMismatch):
        _go(changed, store, resume_from=crashed, ensure_worktree=fake)

    assert fake.calls == []


def test_a_checkpoint_parked_before_the_worktree_existed_walks_from_the_start(
    store, worktree_dir
):
    """Review Focus 1: parked before phase 0, no worktree and no branch were ever made."""
    ran: list[str] = []
    wf = _five(ran, set())
    parked = _park_with_a_triggered_stop(wf, store)
    worktree_dir.rmdir()
    fake = _FakeEnsure(GONE)

    summary = _go(wf, store, resume_from=parked, ensure_worktree=fake)

    assert len(fake.calls) == 1
    assert ran == list(FIVE)
    assert summary.status == "done"
    assert summary.warnings == [_branch_gone(parked.seq, worktree_dir)]
    assert summary.resumed_at is None


def test_any_error_from_the_reensure_declines_and_never_escapes(store, worktree_dir):
    """Review Focus 2: not only `GitError` -- `ensure`'s own `ValueError`, a lock timeout."""
    ran, wf, crashed = _crash_in_c(store)
    worktree_dir.rmdir()
    error = ValueError("worktree.ensure needs a non-empty base name, got ''")
    fake = _FakeEnsure(error=error)
    ran.clear()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    assert ran == list(FIVE)
    assert summary.status == "done"
    assert summary.warnings == [_re_add_failed(crashed.seq, worktree_dir, f"ValueError: {error}")]
    assert summary.resumed_at is None


def test_a_stale_registry_entry_does_not_block_a_declined_resume(store, worktree_dir):
    """Review Focus 3: the decline path frees the checkpoint's agent name too."""
    ran, wf, crashed = _crash_in_c(store)
    Agent(crashed.agent["name"], "left behind by a dead run", [])
    worktree_dir.rmdir()
    ran.clear()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=_FakeEnsure(GONE))

    assert ran == list(FIVE)
    assert summary.status == "done"
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(crashed.agent["name"])


def test_a_file_where_the_worktree_should_be_is_not_the_fast_path(store, worktree_dir):
    """Review Focus 4: only a directory counts as an intact worktree."""
    ran, wf, crashed = _crash_in_c(store)
    worktree_dir.rmdir()
    worktree_dir.write_text("not a worktree\n", encoding="utf-8")
    fake = _FakeEnsure(KEPT)
    ran.clear()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    assert fake.calls == [(BRANCH, BASE_BRANCH, worktree_dir, REPO)]
    assert ran == ["c", "d", "e"]
    assert summary.warnings == [_re_added(crashed.seq, worktree_dir, "c")]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/runtime/test_resume.py -k "deleted_worktree or failed_re_add or digest_check_runs or parked_before_the_worktree or any_error_from or declined_resume or file_where" -v`
Expected: FAIL for every test except `test_the_digest_check_runs_before_the_worktree_re_check`.
- The engine never calls the seam yet, so the `fake.calls == [...]` assertions fail (`[] == [(...)]`).
- The warnings assertions fail (`[] == ['checkpoint #2 of run ...']`).
- `test_a_failed_re_add_starts_over_and_phase_0_escalates` fails on `fake.calls` (resumes at `c`, never calls the step).
- `test_a_stale_registry_entry_does_not_block_a_declined_resume` fails on `ran == list(FIVE)`, because the resume is kept and runs only `c`, `d`, `e`.
- `test_the_digest_check_runs_before_the_worktree_re_check` PASSES already. It is a guard that pins the order once the re-check exists; it cannot be RED here, because the seam is not called at all yet.

- [ ] **Step 3: Add `_worktree_kept` to `src/agent_manager/runtime/engine.py`**

Insert directly above `async def _run(agent: Agent, deps: RunDeps) -> walk.SubtaskSummary:`:

```python
async def _worktree_kept(
    checkpoint: Checkpoint,
    subtask: Any,
    repo_dir: Path,
    ensure_worktree: Callable[..., Mapping[str, object]],
    warnings: list[str],
) -> bool:
    """Whether `checkpoint` may be resumed as saved, its worktree being there.

    Resume worktree re-ensure §3.1-§3.4. No path, or a path that is a
    directory, keeps the checkpoint and runs nothing: no git, no warning. A
    missing directory gets one `ensure_worktree` call on a worker thread (it
    is sync git): a branch that survived keeps the checkpoint, the worktree
    re-added; a branch that is gone, or any error, declines it, so the walk
    starts over from the first phase, whose own `ensure` step reports a real
    failure the ordinary way. Never raises: a missing worktree is a recovery,
    not a refusal. Each non-fast outcome appends exactly one line to `warnings`.
    """
    path = subtask.worktree_path
    if path is None or Path(path).is_dir():
        return True
    label = f"checkpoint #{checkpoint.seq} of run {checkpoint.run_id}"
    try:
        report = await asyncio.to_thread(
            ensure_worktree, subtask.branch, subtask.base_branch, path, repo_dir
        )
    except Exception as error:
        warnings.append(
            f"{label} was not resumed (worktree {path} is missing and could not be"
            f" added again: {walk._render_error(error)}); starting from the first phase"
        )
        return False
    if not report.get("branch_existed"):
        warnings.append(
            f"{label} was not resumed (worktree {path} is missing and branch"
            f" '{subtask.branch}' no longer exists); starting from the first phase"
        )
        return False
    warnings.append(
        f"{label}: worktree {path} was missing and was added again for branch"
        f" '{subtask.branch}'; resuming at '{pending_phase(checkpoint)}'"
    )
    return True
```

- [ ] **Step 4: Call it from the resume branch, after the digest check**

In `run_subtask_async`, replace the block from `compiled = C.compile_workflow(workflow)` through the `if resume_from is None:` / `else:` agent construction (the code Task 2 Step 7 wrote, up to and including `agent.resume()`) with:

```python
    compiled = C.compile_workflow(workflow)
    resumed_at: str | None = None
    # Out-of-band lines for `summary.warnings`, handed to `RunDeps` below.
    warnings: list[str] = []
    if resume_from is not None:
        digest = workflow.digest()
        if resume_from.digest != digest:
            raise CheckpointMismatch(
                f"checkpoint {resume_from.card_id}#{resume_from.seq} was saved under "
                f"digest {resume_from.digest}, but workflow {workflow.name!r} "
                f"has digest {digest}"
            )
        # A run that died before its `finally` may have left its agent
        # registered under this name; `from_dict` -- or, on a decline, the
        # fresh `Agent` of the same name -- would be refused it.
        _forget(resume_from.agent["name"])
        if await _worktree_kept(resume_from, subtask, repo_dir, ensure_worktree, warnings):
            resumed_at = pending_phase(resume_from)
        else:
            # Declined: the walk starts over exactly as a fresh run does. The
            # row is neither deleted nor rewritten; the fresh walk's first
            # `turn` save, at a higher seq, supersedes it.
            resume_from = None
    if resume_from is None:
        agent = Agent(
            f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}",
            workflow.name,
            [compiled.agent_phase, compiled.step_phase],
            context_pool=ContextPool(),
            context_queue=ContextQueue(limit=10),
            tags=["subtask"],
        )
    else:
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
        # A row parked by a `StopSignal` was saved while its agent was paused,
        # and `from_dict` restores that pause; left in place, ON_PAUSE would
        # park the resumed agent again before it ran anything. That pause
        # belonged to the stopped run: only this run's `stop`, registered in
        # `_run` after this line, may pause the agent now.
        agent.resume()
```

Then, inside the `try:` block, change the `RunDeps(...)` construction to pass the list:

```python
        deps = RunDeps(
            workflow,
            store,
            story_id,
            subtask,
            agent_runner,
            clock,
            stop=stop,
            adopt=adopt,
            warnings=warnings,
        )
```

The rest of the `try`/`finally` (`adopt`, `summary = await _run(agent, deps)`, `summary.resumed_at = resumed_at`, `return summary`, `_forget(agent.name)`) stays exactly as Task 2 wrote it.

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: PASS, all tests in the file, old and new.

- [ ] **Step 6: Run the full default suite**

Run: `uv run pytest`
Expected: PASS. In particular `tests/runtime/test_exactly_once.py`, `tests/runtime/test_cancellation.py`, `tests/runtime/test_stop_bridge.py` and `tests/test_cli.py` stay green with no new warnings in their asserted `summary.warnings`/payload `warnings`. Each of them resumes from a directory that exists, so the fast path adds nothing. No unit-tier test hits the stub `git` (exit 99).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/runtime/engine.py tests/runtime/test_resume.py
git commit -m "feat: a resume re-ensures a missing worktree, keeping or declining its checkpoint"
```
