<!-- task-pipeline: validated -->
# Resolve base conflicts with the Integrate resolver (card 8fe30578)

Subtask of "Merged bases" (f7b2edd1), milestone c2a981a3. This narrows an agreed design to one subtask. It does not add a new one. Sources: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (T7, §5, §9 Testing) and `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 2.2. Where this doc and those disagree, those win.

## Scope

In `src/agent_manager/bases.py`, replace the conflict branch of `build` with a call to the Integrate resolver. That branch currently raises `BaseFailed("conflict ... resolver not wired ...")`. Nothing else in `bases.py` changes. The clean-merge path, the missing-tip check, the `MergeInProgressError` handling and verification all belong to sibling 06bf46bb, which is done. `build`'s signature does not change: it already accepts `store`, `run_id`, `story_id`, `runner_factory` and `stop`.

Out of scope:
- lane and supervisor handling of `BaseFailed` (`LaneEscalated` with `failed_phase: "base"`, `LaneStopped`)
- resuming a parked base resolver from its checkpoint (`resume_from`)
- `orchestrate.py` wiring
- dry-run `merged_from` output
- the run `data.bases` field

All of those belong to later Story 3 and Story 4 cards.

Dependency: this card needs `runtime.stop.StopSignal` and `runtime.engine.run_subtask_async`. They were built on `m7/task-add-stopsignal-and-park-364babde`, which brd does not list as a blocker. Both are already present in this worktree (`src/agent_manager/runtime/stop.py`, `src/agent_manager/runtime/engine.py:94`). Confirm they are there before writing tests. If they are missing, stop and flag it.

## Observable behavior

New module constants: `BASES_STORY_ID = "bases"` and `BASES_STORY_TITLE = "Merged bases"`.

When `merge_tip` reports `conflict` for a tip in `tips[1:]`, `build` does the following:

1. **Records the synthetic story, on first need only.** It records `StoryRun(card_id="bases", title="Merged bases", level=0, status="started")`. This follows the lazy `story_recorded` pattern in `integration.integrate_milestone` (integration.py:213-234).
   - A build with only clean merges records nothing.
   - A run gets one `bases` story row, even when several stories build bases or one base hits several conflicts. `record_story` is an upsert keyed on `(run_id, card_id)`.
2. **Records the synthetic subtask.** It records `SubtaskRun(card_id=f"base-{story_id}", branch=root.branch, base_branch=tips[0], status="started", worktree_path=<base worktree>)` with `store.record_subtask("bases", ...)`. This must happen before the engine journals its first phase, as in integration.py:151.
3. **Awaits the resolver on the running loop.** It awaits `runtime.engine.run_subtask_async(workflow.integrate.INTEGRATE, store, ...)` with these arguments:
   - `story_id="bases"` and `subtask=` the row from step 2
   - `repo_dir=` the resolved repo and `commands=` the suite
   - `extra_context={"merge_tip": tip, "conflict_files": [str(f) for f in result["files"]], **cli.gate_context(commands, allow_no_verification)}`
   - `agent_runner=runner_factory(store=store, run_id=run_id, story_id="bases", card_id=f"base-{story_id}")`
   - `stop=stop`

   The resolver is awaited directly, not wrapped in `to_thread`, because it is already async. Every git and verify call that stays in `build` still goes through `asyncio.to_thread`.
4. **Branches on `summary.status`:**
   - `"done"`: the tip is appended to both `merged` and `resolved`, and the loop continues. `BaseResult.resolved` lists exactly the tips a resolver fixed, in the order given. `merged` still lists every tip merged after the first, resolved or not, the same way `integrate_milestone` appends to both lists.
   - `"stopped"` (parked by `stop`): raise `BaseFailed(detail, stopped=True)`. `detail` names the tip, the base branch and the engine's `stopped before <phase>`. The engine has already saved a `parked` checkpoint for card `base-<story id>` and recorded the subtask as `stopped`.
   - Anything else (escalated): raise `BaseFailed(detail, stopped=False)`. `detail` follows `integration._resolver_detail`. It names the tip, the base branch, `failed_phase`, the status and the engine's detail. It also says the branch and worktree are left as they are, and that a human must finish the merge there and then relaunch.

After a resolved conflict, final verification runs exactly as it does today, once, after all tips. A verification failure still raises `BaseFailed(..., stopped=False)`.

Invariants:
- `bases.py` stays a plain async function: no grafo import, not a pygents Agent (milestone rules 1 and 2).
- `BaseResult` stays a frozen dataclass.
- The milestone's base branch is never checked out, merged into or moved, and nothing is pushed (rule 6).
- On failure, the base branch and worktree are left as they are for a human.

## Error paths

| Case | Result |
|---|---|
| Resolver escalates (for example, refuse mode leaves `MERGE_HEAD`, so `merge_completed_gate` blocks) | `BaseFailed(stopped=False)`, resolver detail as above; subtask row `escalated` |
| `stop.trigger` fires while the resolver's first phase is in flight | That turn finishes, the agent parks before the next phase, `parked` checkpoint for `base-<story id>`, `BaseFailed(stopped=True)` |
| Conflict when `store`, `story_id` or `runner_factory` is `None` | `BaseFailed(stopped=False)`: the resolver cannot run, the merge is left in progress in the worktree for a human; nothing is recorded. `stop=None` is valid and means no stop |
| `MergeInProgressError`, missing tip, failed or absent verification | Unchanged from 06bf46bb |

## Tests

Placement follows design §14 as refined for this repo:
- `bases.py` is a **Steps** component, so its tests go in `tests/test_bases.py` against real temporary git repos (the existing `conflicting_repo` / `two_story_repo` fixtures), with no mocks of git.
- The resolver is supplied through `runner_factory` as an injected launcher double, the way `tests/test_integration.py`'s `FakeResolver` factory does it. That double plays fake `claude`'s resolver mode: resolve means keep both sides and commit; refuse means claim resolved but leave `MERGE_HEAD`. It knows only what its brief (prompt) tells it (rule 5).
- No test sleeps. The stop test triggers from inside the launcher double's first call (rule 4).

In `tests/test_bases.py` (Steps tier):
1. `test_a_conflict_is_resolved_by_the_integrate_resolver` replaces `test_a_conflict_is_not_resolved_yet`. With conflicting tips A and B for story C, `build` checks all of the following:
   - It returns `resolved == [B tip]`, with B also in `merged`.
   - The base branch contains both tips' commits.
   - The worktree has no `MERGE_HEAD`.
   - The milestone base branch is unchanged.
   - The resolver was called once with the conflict files.
2. `test_the_resolver_walk_is_journalled_under_the_bases_story`: after a resolved conflict, `store.load_run(run_id)`, which is what `am status` reads, has one story `bases` titled "Merged bases". That story has subtask `base-<C id>` with the INTEGRATE phase rows. The subtask row exists before the first phase row (journal order).
3. `test_a_clean_build_records_no_bases_story`: a clean two-tip build leaves no `bases` story in the store.
4. `test_two_bases_in_one_run_share_one_bases_story`: two conflicting builds for different stories in the same store give one `bases` story with two subtasks, `base-<C id>` and `base-<D id>`.
5. `test_a_resolver_that_gives_up_fails_the_base`: in refuse mode, `build` raises `BaseFailed` with `stopped is False` and a detail naming the tip, the base branch and the failed phase. The subtask is `escalated`, the merge is left in the worktree, and the milestone base is unchanged.
6. `test_a_stop_during_the_resolver_parks_it`: the launcher double calls `stop.trigger(...)` during the resolver's first phase. `build` raises `BaseFailed` with `stopped is True`, `store.latest_checkpoint(f"base-{C}")` has kind `parked`, the subtask is `stopped`, and no later phase ran.
7. `test_a_conflict_without_a_resolver_fails_for_a_human`: a conflict with `runner_factory=None` raises `BaseFailed(stopped=False)`, records nothing and leaves the merge in progress.
8. Extend the existing `test_every_git_and_verify_call_runs_off_the_event_loop_thread`, or add a conflict-path twin of it, so that the git and verify calls around a resolved conflict are also shown to run off the loop thread.
9. The existing `test_build_is_a_plain_coroutine_that_does_not_import_grafo` and the clean-merge tests stay as they are, and must stay green.

In `tests/e2e/test_fake_claude.py` (production-wiring tier, unmarked, default suite): reuse M5's resolver-mode tests (`test_the_resolver_*`, around lines 1064-1204) unchanged. They pin the resolve and refuse behavior that the test_bases.py launcher double copies. Do not add a test marked `e2e`, and do not add anything to `tests/test_orchestrate.py`.

## Verification

- fullSuite: `uv run pytest` (the whole default suite, including `tests/e2e`, must be green)
- typecheck: none
- lint: none

---

# Resolve Base Conflicts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `bases.build` hits a conflicting blocker tip, hand the conflict to `workflow.integrate.INTEGRATE` through `runtime.engine.run_subtask_async` under a synthetic `bases` story, and turn the resolver's done / escalated / stopped outcome into `BaseResult.resolved` or `BaseFailed(stopped=...)`.

**Architecture:** `bases.build` stays a plain async function. A new `async def _resolve_conflict` in `bases.py` mirrors `integration._resolve_conflict` (integration.py:123-170): it records the synthetic subtask `base-<story id>` under story `bases`, builds the runner through `runner_factory`, and awaits `run_subtask_async` on the running loop with the run's `StopSignal`. `build` records the `bases` story lazily on the first conflict, branches on `summary.status`, and otherwise leaves the clean-merge path, missing-tip check, `MergeInProgressError` handling and final verification exactly as sibling 06bf46bb built them.

**Tech Stack:** Python 3, asyncio, pytest (asyncio auto mode, already used by `tests/test_bases.py`), real `git` in `tmp_path`, `agent_manager.store.Store`, `dispatch.AgentRunner` with an injected launcher double.

**Spec:** `docs/superpowers/specs/task-resolve-base-conflicts-8fe30578-design.md` (reproduced verbatim above).

## Global Constraints

- Only `orchestrate.py` imports grafo; `bases.py` must contain no line starting with `import grafo` or `from grafo` (milestone rule 1).
- `bases.build` is a plain coroutine, not a pygents Agent (milestone rule 2).
- Every card leaves the WHOLE default suite green, including `tests/e2e` (rule 3). Verify with `uv run pytest`.
- No test sleeps to prove ordering; cross-thread handoffs use `threading.Event` / `loop.call_soon_threadsafe` (rule 4).
- The launcher double never knows more than its brief tells it (rule 5): it reads the conflict files and result path only from the prompt file.
- The milestone's base branch (`master` in these tests) never moves and nothing is pushed (rule 6).
- `BASES_STORY_ID = "bases"`, `BASES_STORY_TITLE = "Merged bases"`, subtask card id `f"base-{story_id}"`.
- `BaseResult` stays `@dataclass(frozen=True)` with fields `branch, merged, already_merged, resolved`; `build`'s signature does not change.
- Every git and verify call made by `build` itself stays inside `asyncio.to_thread`; the resolver is awaited directly.
- Tests for this card live in `tests/test_bases.py` only (Steps tier). No new `e2e`-marked test, nothing in `tests/test_orchestrate.py`. `tests/e2e/test_fake_claude.py`'s `test_the_resolver_*` tests are reused unchanged.
- No commands besides `uv run pytest` (there is no lint or typecheck).

## Review Focus

1. Two conflicting tips in one base (`[a, b, c]`, all rewriting `shared.txt`): both are resolved under the same card `base-<C>`, the engine reuses the agent name `run:base-<C>` safely, and `resolved == ["m7/b", "m7/c"]`. Pinned by `test_two_conflicts_in_one_base_are_each_resolved` in Task 1.
2. Relaunch after a resolved conflict: the second build re-merges nothing, reports the tip as `already_merged`, calls no resolver and records nothing new. Pinned by `test_a_relaunch_after_a_resolved_conflict_dispatches_nothing` in Task 1.
3. `run_id=None` with a real store: the runner factory still gets the store's own run id rather than `None`. Pinned by `test_a_missing_run_id_falls_back_to_the_stores` in Task 1.
4. Relaunch after an escalation without a human fix: the leftover `MERGE_HEAD` fails the base through the existing `MergeInProgressError` path and the resolver is not dispatched again. Pinned by `test_a_relaunch_after_an_escalation_does_not_re_dispatch` in Task 2.
5. The resolver fixes the merge but the suite is red: the resolver's own `verify` phase escalates, so `build` raises `BaseFailed(stopped=False)` naming phase `'verify'`, and the resolved merge commit stays on the base branch. Pinned by `test_a_red_suite_after_a_resolved_conflict_fails_the_base` in Task 2.

---

## File Structure

- Modify: `src/agent_manager/bases.py` — add the two constants, `_resolve_conflict`, `_resolver_detail`, `_stopped_detail`, and replace the conflict branch of `build` (currently lines 150-156). Update the module, `BaseResult` and `build` docstrings.
- Modify: `tests/test_bases.py` — add the store fixture, the launcher double (`_FakeAdapter`, `FakeResolver`, `FakeFactory`), a `_resolve_build` helper, and the conflict-path tests; delete `test_a_conflict_is_not_resolved_yet` (currently lines 335-350).

No other file changes.

---

### Task 1: Hand a conflict to the Integrate resolver and record it under the `bases` story

**Files:**
- Modify: `src/agent_manager/bases.py:1-170`
- Test: `tests/test_bases.py` (add after the `conflicting_repo` fixture; delete lines 335-350)

**Interfaces:**
- Consumes (already in this worktree):
  - `runtime.engine.run_subtask_async(workflow, store, *, story_id, subtask, repo_dir, commands=(), extra_context=None, agent_runner=None, stop=None, ...) -> walk.SubtaskSummary` (`src/agent_manager/runtime/engine.py:94`)
  - `walk.SubtaskSummary` with `status: Literal["done", "escalated", "stopped"]`, `failed_phase: str | None`, `detail: str | None`
  - `workflow.integrate.INTEGRATE` (phases `resolve`, `verify`)
  - `cli.gate_context(commands, allow_no_verification) -> dict`, `cli.RunnerFactory` (`(*, store, run_id, story_id, card_id) -> AgentPhaseRunner`)
  - `models.StoryRun`, `models.SubtaskRun`, `Store.record_story`, `Store.record_subtask`, `Store.load_run`, `Store.journal.read()`, `Store.run_id`
- Produces:
  - `bases.BASES_STORY_ID: str = "bases"`, `bases.BASES_STORY_TITLE: str = "Merged bases"`
  - `async def bases._resolve_conflict(*, story_id: str, tip: str, files: list[str], branch: str, base_branch: str, worktree: Path, repo_dir: Path, commands: list[str], allow_no_verification: bool, store: Store, run_id: str, runner_factory: cli.RunnerFactory, stop: StopSignal | None) -> SubtaskSummary`
  - `BaseResult.resolved` now lists resolved tips.
  - Test helpers in `tests/test_bases.py`: `RUN_ID`, `STORY_C`, `STORY_D`, fixture `store`, `FakeResolver(refuse: bool = False, during: Callable[[], None] | None = None)`, `FakeFactory(resolver: FakeResolver)`, `async _resolve_build(repo, tips, *, store, factory, root=ROOT, story_id=STORY_C, run_id=RUN_ID, commands=("true",), stop=None)`, `_bases_story(store)`.

- [ ] **Step 1: Confirm the dependency is present**

Open `src/agent_manager/runtime/stop.py` and `src/agent_manager/runtime/engine.py`. `class StopSignal` must exist in the first and `async def run_subtask_async(` in the second (line 94). If either is missing, stop and flag it: this card depends on `m7/task-add-stopsignal-and-park-364babde`.

- [ ] **Step 2: Add the imports, constants and helpers the new tests need**

In `tests/test_bases.py`, replace the import block (lines 11-21) with:

```python
import asyncio
import dataclasses
import inspect
import json
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_manager import bases, dispatch, models
from agent_manager.dag import RootPlan
from agent_manager.harness.base import Outcome
from agent_manager.runtime.stop import StopSignal
from agent_manager.store import Store
```

Then, directly below the existing `conflicting_repo` fixture (after line 296), add:

```python
# ── the resolver path (card 8fe30578) ────────────────────────────────────────

RUN_ID = "run-2026-09-26-merged-bases"
STORY_C = "cccccccc-0000-4000-8000-00000000000c"
STORY_D = "dddddddd-0000-4000-8000-00000000000d"
CARD_C = f"base-{STORY_C}"
CARD_D = f"base-{STORY_D}"
BASE_D = "m7/base-dddddddd"
ROOT_D = RootPlan("merged", BASE_D, ("A", "B"))
_MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")


@pytest.fixture
def store(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    """A real store with the run recorded, as the milestone runner records it."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = Store.open(repo, RUN_ID)
    opened.record_run(
        models.Run(
            id=RUN_ID,
            workflow="milestone",
            repo_dir=repo,
            base_branch="master",
            branch_prefix="m7",
            status="started",
        )
    )
    yield opened
    opened.close()


class _FakeAdapter:
    """A `HarnessAdapter` by shape. Its argv names the brief and nothing else."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return ["fake-resolver", "--prompt", str(d.prompt_path)]

    def parse_usage(self, stdout: str) -> None:
        return None


_CONFLICT_HEADING = "\n## conflict_files\n"
_RESULT_LEAD = "write your result as valid JSON to exactly this path:\n\n"


def _conflict_files_from(brief: str) -> list[str]:
    start = brief.index(_CONFLICT_HEADING) + len(_CONFLICT_HEADING)
    files, _end = json.JSONDecoder().raw_decode(brief, start)
    return files


def _result_path_from(brief: str) -> Path:
    start = brief.index(_RESULT_LEAD) + len(_RESULT_LEAD)
    return Path(brief[start : brief.index("\n", start)])


def _keep_both_sides(text: str) -> str:
    """Fake claude's resolve mode: drop the conflict markers, keep every side's lines."""
    return "".join(
        line for line in text.splitlines(keepends=True) if not line.startswith(_MARKERS)
    )


@dataclass
class FakeResolver:
    """A `LauncherFn` double playing fake `claude`'s resolver mode in its cwd.

    It learns the conflicting files and its result path only from the brief
    (rule 5). Resolve keeps both sides, stages and commits the merge. Refuse
    claims `resolved: true` but touches nothing, so `MERGE_HEAD` stays and
    `merge_completed_gate` blocks. `during` runs in the launcher's thread after
    the work is done, before the call returns: the stop test triggers there.
    """

    refuse: bool = False
    during: Callable[[], None] | None = None
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        brief = Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
        files = _conflict_files_from(brief)
        self.calls.append(files)
        worktree = Path(cwd)
        if self.refuse:
            result: dict[str, Any] = {"resolved": True, "summary": "said it was resolved"}
        else:
            for name in files:
                path = worktree / name
                path.write_text(_keep_both_sides(path.read_text(encoding="utf-8")), encoding="utf-8")
            _git(worktree, "add", *files)
            _git(worktree, "commit", "--no-edit")
            result = {"resolved": True, "summary": f"kept both sides of {', '.join(files)}"}
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        _result_path_from(brief).write_text(json.dumps(result), encoding="utf-8")
        if self.during is not None:
            self.during()
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.1,
            stdout_path=stdout_path,
        )


@dataclass
class FakeFactory:
    """A `cli.RunnerFactory` that records every call and wires in `FakeResolver`."""

    resolver: FakeResolver = field(default_factory=FakeResolver)
    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, *, store, run_id, story_id, card_id):
        self.calls.append({"run_id": run_id, "story_id": story_id, "card_id": card_id})
        adapter = _FakeAdapter()
        return dispatch.AgentRunner(
            store=store,
            launcher=self.resolver,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            adapters={adapter.name: adapter},
            harness_map={
                "resolver": models.HarnessAssignment(harness=adapter.name, model="fake-model")
            },
        )


async def _resolve_build(
    repo: Path,
    tips: list[str],
    *,
    store: Store | None,
    factory: FakeFactory | None,
    root: RootPlan = ROOT,
    story_id: str | None = STORY_C,
    run_id: str | None = RUN_ID,
    commands: tuple[str, ...] | list[str] = ("true",),
    stop: StopSignal | None = None,
) -> bases.BaseResult:
    """`bases.build` with the resolver parameters filled in."""
    return await bases.build(
        root,
        list(tips),
        repo_dir=repo,
        commands=list(commands),
        allow_no_verification=False,
        store=store,
        run_id=run_id,
        story_id=story_id,
        runner_factory=factory,
        stop=stop,
    )


def _bases_story(store: Store) -> models.StoryRun:
    run = store.load_run(RUN_ID)
    assert [story.card_id for story in run.stories] == ["bases"]
    return run.stories[0]
```

- [ ] **Step 3: Replace the old conflict test and write the failing tests**

Delete `test_a_conflict_is_not_resolved_yet` (the whole function, currently lines 335-350 including its `@requires_git` decorator). In its place add:

```python
@requires_git
async def test_a_conflict_is_resolved_by_the_integrate_resolver(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    factory = FakeFactory()

    result = await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=factory)

    assert result == bases.BaseResult(
        branch=BASE, merged=["m7/b"], already_merged=[], resolved=["m7/b"]
    )
    assert is_ancestor(repo, "m7/a", BASE) and is_ancestor(repo, "m7/b", BASE)
    wt = base_worktree(repo)
    assert _merge_head(wt) is None
    assert _git(wt, "show", f"{BASE}:shared.txt") == "from story a\nfrom story b\n"
    assert factory.resolver.calls == [["shared.txt"]]
    assert factory.calls == [{"run_id": RUN_ID, "story_id": "bases", "card_id": CARD_C}]
    assert _git(repo, "symbolic-ref", "HEAD").strip() == "refs/heads/master"
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_the_resolver_walk_is_journalled_under_the_bases_story(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    assert (bases.BASES_STORY_ID, bases.BASES_STORY_TITLE) == ("bases", "Merged bases")

    await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=FakeFactory())

    story = _bases_story(store)
    assert (story.title, story.level, story.status) == ("Merged bases", 0, "started")
    [subtask] = story.subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "done")
    assert subtask.branch == BASE
    assert subtask.base_branch == "m7/a"
    assert subtask.worktree_path == base_worktree(repo)
    assert [phase.name for phase in subtask.phases] == ["resolve", "verify"]
    events = [(line.event, line.story, line.card) for line in store.journal.read()]
    story_at = events.index(("story_upsert", "bases", None))
    subtask_at = events.index(("subtask_upsert", "bases", CARD_C))
    first_phase_at = next(
        index
        for index, (event, _story, card) in enumerate(events)
        if event == "phase_upsert" and card == CARD_C
    )
    assert story_at < subtask_at < first_phase_at
    assert store.latest_checkpoint(CARD_C).reason == "done"
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_clean_build_records_no_bases_story(
    two_story_repo: Path, store: Store, MASTER_BEFORE: str
):
    factory = FakeFactory()

    result = await _resolve_build(two_story_repo, ["m7/a", "m7/b"], store=store, factory=factory)

    assert result.resolved == []
    assert factory.calls == []
    assert store.load_run(RUN_ID).stories == []
    assert rev(two_story_repo, "master") == MASTER_BEFORE


@requires_git
async def test_two_bases_in_one_run_share_one_bases_story(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    factory = FakeFactory()

    first = await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=factory)
    second = await _resolve_build(
        repo, ["m7/a", "m7/b"], store=store, factory=factory, root=ROOT_D, story_id=STORY_D
    )

    assert first.resolved == ["m7/b"] and second.resolved == ["m7/b"]
    assert second.branch == BASE_D
    story = _bases_story(store)
    assert [(s.card_id, s.status) for s in story.subtasks] == [
        (CARD_C, "done"),
        (CARD_D, "done"),
    ]
    assert [call["card_id"] for call in factory.calls] == [CARD_C, CARD_D]
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_conflict_without_a_resolver_fails_for_a_human(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo

    with pytest.raises(bases.BaseFailed, match="no resolver is available") as excinfo:
        await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=None)

    assert excinfo.value.stopped is False
    assert "m7/b" in excinfo.value.detail
    assert "shared.txt" in excinfo.value.detail
    wt = base_worktree(repo)
    assert str(wt) in excinfo.value.detail
    # Left in progress for a human: never aborted, and nothing recorded.
    assert _merge_head(wt) == rev(repo, "m7/b")
    assert "<<<<<<< " in (wt / "shared.txt").read_text()
    assert store.load_run(RUN_ID).stories == []
    assert rev(repo, BASE) == rev(repo, "m7/a")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_conflict_with_no_store_fails_for_a_human(
    conflicting_repo: Path, MASTER_BEFORE: str
):
    repo = conflicting_repo

    with pytest.raises(bases.BaseFailed, match="no resolver is available") as excinfo:
        await _build(repo, ["m7/a", "m7/b"])

    assert excinfo.value.stopped is False
    assert _merge_head(base_worktree(repo)) == rev(repo, "m7/b")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_two_conflicts_in_one_base_are_each_resolved(
    conflicting_repo: Path, tmp_path: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 1: the same card, `base-<C>`, walks INTEGRATE twice.
    repo = conflicting_repo
    _make_tip(repo, tmp_path, "m7/c", {"shared.txt": "from story c\n"})
    factory = FakeFactory()

    result = await _resolve_build(
        repo, ["m7/a", "m7/b", "m7/c"], store=store, factory=factory
    )

    assert result.merged == ["m7/b", "m7/c"]
    assert result.resolved == ["m7/b", "m7/c"]
    assert factory.resolver.calls == [["shared.txt"], ["shared.txt"]]
    assert [call["card_id"] for call in factory.calls] == [CARD_C, CARD_C]
    for tip in ("m7/a", "m7/b", "m7/c"):
        assert is_ancestor(repo, tip, BASE)
    assert _merge_head(base_worktree(repo)) is None
    [subtask] = _bases_story(store).subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "done")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_relaunch_after_a_resolved_conflict_dispatches_nothing(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 2.
    repo = conflicting_repo
    await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=FakeFactory())
    built = rev(repo, BASE)
    again = FakeFactory()

    result = await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=again)

    assert result == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/b"], resolved=[]
    )
    assert again.calls == [] and again.resolver.calls == []
    assert rev(repo, BASE) == built
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_missing_run_id_falls_back_to_the_stores(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 3: `run_id` is typed `str | None`; the runner needs a real id.
    repo = conflicting_repo
    factory = FakeFactory()

    result = await _resolve_build(
        repo, ["m7/a", "m7/b"], store=store, factory=factory, run_id=None
    )

    assert result.resolved == ["m7/b"]
    assert factory.calls == [{"run_id": RUN_ID, "story_id": "bases", "card_id": CARD_C}]
    assert rev(repo, "master") == MASTER_BEFORE
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_bases.py -v -k "resolved_by_the_integrate or journalled_under or clean_build_records or share_one_bases or without_a_resolver or with_no_store or two_conflicts_in_one or relaunch_after_a_resolved or missing_run_id"`

Expected: FAIL for every test except `test_a_clean_build_records_no_bases_story` (a regression guard; it passes on today's code because the clean path records nothing). The failures are `BaseFailed: conflict merging m7/b into the merged base m7/base-cccccccc (shared.txt): resolver not wired ...`, or for the two "no resolver" tests a `match` failure because the message does not contain `no resolver is available`.

- [ ] **Step 5: Implement the resolver hand-off in `bases.py`**

In `src/agent_manager/bases.py`, replace the import block (lines 24-35) with:

```python
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_manager import cli, models
from agent_manager.dag import RootPlan
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.runtime.walk import SubtaskSummary
from agent_manager.steps import reducers, verify
from agent_manager.steps.integrate import MergeInProgressError, _ref_exists, merge_tip
from agent_manager.steps.worktree import ensure, run_git
from agent_manager.store import Store
from agent_manager.workflow import integrate as integrate_workflow

BASES_STORY_ID = "bases"
"""The synthetic story every base-resolver subtask hangs from."""

BASES_STORY_TITLE = "Merged bases"
```

Directly after `_verify` (after line 96), add:

```python
async def _resolve_conflict(
    *,
    story_id: str,
    tip: str,
    files: list[str],
    branch: str,
    base_branch: str,
    worktree: Path,
    repo_dir: Path,
    commands: list[str],
    allow_no_verification: bool,
    store: Store,
    run_id: str,
    runner_factory: cli.RunnerFactory,
    stop: StopSignal | None,
) -> SubtaskSummary:
    """Walk `workflow.integrate.INTEGRATE` once for one conflicting tip.

    Mirrors `integration._resolve_conflict`, awaited on the running loop and
    stop-aware. The synthetic subtask `base-<story id>` is recorded before the
    engine journals its first phase, because `store.rebuild_from_journal`
    refuses a phase whose subtask no earlier line created. The caller has
    already recorded the `bases` story.
    """
    card_id = f"base-{story_id}"
    subtask = models.SubtaskRun(
        card_id=card_id,
        branch=branch,
        base_branch=base_branch,
        status="started",
        worktree_path=worktree,
    )
    store.record_subtask(BASES_STORY_ID, subtask)
    runner = runner_factory(
        store=store, run_id=run_id, story_id=BASES_STORY_ID, card_id=card_id
    )
    return await runtime_engine.run_subtask_async(
        integrate_workflow.INTEGRATE,
        store,
        story_id=BASES_STORY_ID,
        subtask=subtask,
        repo_dir=repo_dir,
        commands=commands,
        extra_context={
            "merge_tip": tip,
            "conflict_files": list(files),
            **cli.gate_context(commands, allow_no_verification),
        },
        agent_runner=runner,
        stop=stop,
    )
```

In `build`, replace the lines from `merged: list[str] = []` through the end of the function (currently lines 137-170) with:

```python
    merged: list[str] = []
    already_merged: list[str] = []
    resolved: list[str] = []
    story_recorded = False
    for tip in tips[1:]:
        try:
            result = await asyncio.to_thread(
                merge_tip, repo, worktree, root.branch, tips[0], tip, git_runner=run_git
            )
        except MergeInProgressError as error:
            # The error already says the earlier conflict was never resolved
            # and a human must finish it in the worktree.
            raise BaseFailed(
                f"cannot build the merged base {root.branch}: {error}"
            ) from error
        if result["conflict"]:
            files = [str(name) for name in result["files"]]
            if store is None or story_id is None or runner_factory is None:
                raise BaseFailed(
                    f"conflict merging {tip} into the merged base {root.branch} "
                    f"({', '.join(files)}): no resolver is available, so the merge "
                    f"is left in progress in {worktree} for a human"
                )
            if not story_recorded:
                store.record_story(
                    models.StoryRun(
                        card_id=BASES_STORY_ID,
                        title=BASES_STORY_TITLE,
                        level=0,
                        status="started",
                    )
                )
                story_recorded = True
            await _resolve_conflict(
                story_id=story_id,
                tip=tip,
                files=files,
                branch=root.branch,
                base_branch=tips[0],
                worktree=worktree,
                repo_dir=repo,
                commands=suite,
                allow_no_verification=allow_no_verification,
                store=store,
                run_id=run_id if run_id is not None else store.run_id,
                runner_factory=runner_factory,
                stop=stop,
            )
            resolved.append(tip)
        if result["already_merged"]:
            already_merged.append(tip)
        else:
            merged.append(tip)

    failure = await asyncio.to_thread(
        _verify, suite, allow_no_verification, root.branch, worktree
    )
    if failure is not None:
        raise BaseFailed(failure)

    return BaseResult(
        branch=root.branch,
        merged=merged,
        already_merged=already_merged,
        resolved=resolved,
    )
```

(The resolver's `summary.status` is branched on in Task 2; this step only wires the hand-off and the recording.)

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_bases.py -v`
Expected: every test in the file PASSES, including the untouched clean-merge tests and `test_build_is_a_plain_coroutine_that_does_not_import_grafo`.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/bases.py tests/test_bases.py
git commit -m "feat(bases): hand base conflicts to the Integrate resolver

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

---

### Task 2: Fail the base when the resolver escalates or is stopped

**Files:**
- Modify: `src/agent_manager/bases.py` (the `await _resolve_conflict(...)` block inside `build` added in Task 1; add `_resolver_detail` and `_stopped_detail` after `_resolve_conflict`)
- Test: `tests/test_bases.py` (append after the Task 1 tests)

**Interfaces:**
- Consumes: `bases._resolve_conflict(...) -> SubtaskSummary` and the Task 1 test helpers (`store` fixture, `FakeResolver(refuse=..., during=...)`, `FakeFactory`, `_resolve_build`, `_bases_story`, `CARD_C`, `STORY_C`, `RUN_ID`); `StopSignal.trigger(story_id: str) -> bool`; `Store.latest_checkpoint(card_id) -> Checkpoint | None` whose `.reason` is `"turn" | "parked" | "done" | "escalated"`.
- Produces:
  - `bases._resolver_detail(tip: str, branch: str, worktree: Path, summary: SubtaskSummary) -> str`
  - `bases._stopped_detail(tip: str, branch: str, worktree: Path, summary: SubtaskSummary) -> str`
  - `build` raises `BaseFailed(detail, stopped=True)` for `summary.status == "stopped"` and `BaseFailed(detail, stopped=False)` for any other non-`"done"` status.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bases.py`:

```python
@requires_git
async def test_a_resolver_that_gives_up_fails_the_base(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    factory = FakeFactory(resolver=FakeResolver(refuse=True))

    with pytest.raises(bases.BaseFailed) as excinfo:
        await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=factory)

    failed = excinfo.value
    assert failed.stopped is False
    assert "m7/b" in failed.detail
    assert BASE in failed.detail
    assert "'resolve'" in failed.detail
    assert "escalated" in failed.detail
    assert "MERGE_HEAD exists" in failed.detail
    wt = base_worktree(repo)
    assert str(wt) in failed.detail
    assert "relaunch" in failed.detail
    # One dispatch; the runner's own gate retry is inside it.
    assert [call["card_id"] for call in factory.calls] == [CARD_C]
    assert factory.resolver.calls == [["shared.txt"], ["shared.txt"]]
    [subtask] = _bases_story(store).subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "escalated")
    assert store.latest_checkpoint(CARD_C).reason == "escalated"
    # Left exactly as it is for a human.
    assert _merge_head(wt) == rev(repo, "m7/b")
    assert rev(repo, BASE) == rev(repo, "m7/a")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_stop_during_the_resolver_parks_it(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    fired = threading.Event()

    def fire() -> None:
        # On the loop, where the signal lives.
        stop.trigger(STORY_C)
        fired.set()

    def during() -> None:
        # In the launcher's thread, while `resolve` is in flight.
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")

    factory = FakeFactory(resolver=FakeResolver(during=during))

    with pytest.raises(bases.BaseFailed) as excinfo:
        await _resolve_build(
            repo, ["m7/a", "m7/b"], store=store, factory=factory, stop=stop
        )

    failed = excinfo.value
    assert failed.stopped is True
    assert "m7/b" in failed.detail
    assert BASE in failed.detail
    assert "stopped before verify" in failed.detail
    assert stop.primary == STORY_C
    assert factory.resolver.calls == [["shared.txt"]]
    assert store.latest_checkpoint(CARD_C).reason == "parked"
    [subtask] = _bases_story(store).subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "stopped")
    # The turn in flight finished; the next phase never started.
    assert [phase.name for phase in subtask.phases] == ["resolve"]
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_relaunch_after_an_escalation_does_not_re_dispatch(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 4: nobody finished the merge, so the relaunch finds it in progress.
    repo = conflicting_repo
    with pytest.raises(bases.BaseFailed):
        await _resolve_build(
            repo,
            ["m7/a", "m7/b"],
            store=store,
            factory=FakeFactory(resolver=FakeResolver(refuse=True)),
        )
    again = FakeFactory()

    with pytest.raises(bases.BaseFailed, match="never resolved") as excinfo:
        await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=again)

    assert excinfo.value.stopped is False
    assert again.calls == [] and again.resolver.calls == []
    assert _merge_head(base_worktree(repo)) == rev(repo, "m7/b")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_red_suite_after_a_resolved_conflict_fails_the_base(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 5: the resolver's own `verify` phase runs the red suite first.
    repo = conflicting_repo
    factory = FakeFactory()

    with pytest.raises(bases.BaseFailed) as excinfo:
        await _resolve_build(
            repo, ["m7/a", "m7/b"], store=store, factory=factory, commands=["false"]
        )

    failed = excinfo.value
    assert failed.stopped is False
    assert "'verify'" in failed.detail
    assert factory.resolver.calls == [["shared.txt"]]
    # The resolved merge commit stands; only the verdict failed.
    assert is_ancestor(repo, "m7/b", BASE)
    assert _merge_head(base_worktree(repo)) is None
    [subtask] = _bases_story(store).subtasks
    assert subtask.status == "escalated"
    assert rev(repo, "master") == MASTER_BEFORE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bases.py -v -k "gives_up or stop_during or after_an_escalation or red_suite_after"`

Expected:
- `test_a_resolver_that_gives_up_fails_the_base`: FAIL with `Failed: DID NOT RAISE <class 'agent_manager.bases.BaseFailed'>` (Task 1 ignores the summary and the final `true` suite passes with `MERGE_HEAD` still set).
- `test_a_stop_during_the_resolver_parks_it`: FAIL with `DID NOT RAISE`.
- `test_a_relaunch_after_an_escalation_does_not_re_dispatch`: FAIL with `DID NOT RAISE` on the first `pytest.raises`.
- `test_a_red_suite_after_a_resolved_conflict_fails_the_base`: FAIL, because the final verification raises `BaseFailed` whose detail says `failed its verification` and does not contain `'verify'`.

- [ ] **Step 3: Implement the status branches**

In `src/agent_manager/bases.py`, directly after `_resolve_conflict`, add:

```python
def _resolver_detail(
    tip: str, branch: str, worktree: Path, summary: SubtaskSummary
) -> str:
    """Why a resolver that escalated failed the base. Modelled on `integration._resolver_detail`."""
    return (
        f"the resolver did not finish merging {tip} into the merged base {branch}: "
        f"phase {summary.failed_phase!r} ended {summary.status} ({summary.detail}). "
        f"The branch and worktree are left as they are in {worktree}; a human must "
        "finish the merge there, then relaunch."
    )


def _stopped_detail(
    tip: str, branch: str, worktree: Path, summary: SubtaskSummary
) -> str:
    """Why a stopped resolver cut the base short. `summary.detail` is `stopped before <phase>`."""
    return (
        f"the resolver merging {tip} into the merged base {branch} was stopped "
        f"({summary.detail}). The branch and worktree are left as they are in "
        f"{worktree}, with a parked checkpoint to continue from."
    )
```

In `build`, replace:

```python
            await _resolve_conflict(
```

with:

```python
            summary = await _resolve_conflict(
```

and replace the line directly after that call's closing `)`:

```python
            resolved.append(tip)
```

with:

```python
            if summary.status == "stopped":
                raise BaseFailed(
                    _stopped_detail(tip, root.branch, worktree, summary), stopped=True
                )
            if summary.status != "done":
                raise BaseFailed(_resolver_detail(tip, root.branch, worktree, summary))
            resolved.append(tip)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bases.py -v`
Expected: every test in the file PASSES.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/bases.py tests/test_bases.py
git commit -m "feat(bases): fail the base when its resolver escalates or is stopped

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

---

### Task 3: Prove the conflict path stays off the loop thread, update docstrings, run the full suite

**Files:**
- Modify: `src/agent_manager/bases.py:1-22` (module docstring), `:38-50` (`BaseResult` docstring), `build` docstring
- Modify: `tests/test_bases.py:1-9` (module docstring); append one test

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.
- Produces: nothing new.

- [ ] **Step 1: Write the conflict-path twin of the off-loop test**

Append to `tests/test_bases.py`:

```python
@requires_git
async def test_git_and_verify_around_a_resolved_conflict_run_off_the_loop_thread(
    conflicting_repo: Path,
    store: Store,
    MASTER_BEFORE: str,
    monkeypatch: pytest.MonkeyPatch,
):
    # The conflict-path twin of the test above: same spies, real git, a real
    # suite, and the resolver in between.
    repo = conflicting_repo
    loop_thread = threading.get_ident()
    calls: dict[str, list[int]] = {}

    def spy(name: str, real):
        def wrapper(*args, **kwargs):
            calls.setdefault(name, []).append(threading.get_ident())
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(bases, "_ref_exists", spy("_ref_exists", bases._ref_exists))
    monkeypatch.setattr(bases, "ensure", spy("ensure", bases.ensure))
    monkeypatch.setattr(bases, "merge_tip", spy("merge_tip", bases.merge_tip))
    monkeypatch.setattr(
        bases.verify, "run_suite", spy("run_suite", bases.verify.run_suite)
    )
    factory = FakeFactory()

    result = await _resolve_build(
        repo, ["m7/a", "m7/b"], store=store, factory=factory, commands=["true"]
    )

    assert result.resolved == ["m7/b"]
    assert factory.resolver.calls == [["shared.txt"]]
    assert sorted(calls) == ["_ref_exists", "ensure", "merge_tip", "run_suite"]
    assert all(
        thread != loop_thread for threads in calls.values() for thread in threads
    ), calls
    assert rev(repo, "master") == MASTER_BEFORE
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_bases.py::test_git_and_verify_around_a_resolved_conflict_run_off_the_loop_thread -v`
Expected: PASS. This is a regression guard for an invariant Tasks 1 and 2 already keep (every call `build` makes itself is still wrapped in `asyncio.to_thread`); there is no RED state to reach without breaking the code on purpose. To confirm the test has teeth, temporarily change `await asyncio.to_thread(_verify, suite, allow_no_verification, root.branch, worktree)` in `build` to `_verify(suite, allow_no_verification, root.branch, worktree)`, rerun, see it FAIL on the `thread != loop_thread` assertion, then revert that change.

- [ ] **Step 3: Update the docstrings**

In `src/agent_manager/bases.py`, replace the module docstring paragraph:

```python
Task 2.2 (card 8fe30578) wires conflicts to the Integrate resolver and is the
first user of `store`, `run_id`, `story_id`, `runner_factory` and `stop`,
which `build` already accepts so its call site never changes.
```

with:

```python
A conflicting tip is handed to `workflow.integrate.INTEGRATE` (resolve, then
verify) through `runtime.engine.run_subtask_async`, awaited on the running
loop with the run's `StopSignal` (plan Task 2.2, card 8fe30578). It runs under
a synthetic "Merged bases" story (`BASES_STORY_ID`), recorded on the first
conflict only, with one synthetic subtask `base-<story id>` recorded before
the engine journals a phase. A resolver that escalates fails the base; one
parked by the stop fails it with `stopped=True`. Either way the merge is left
in the worktree for a human or a later resume.
```

Replace the `BaseResult` docstring:

```python
    """The merged base is built and verified.

    `merged` and `already_merged` list the tips after the first, in the order
    given; `resolved` lists the tips a resolver fixed (always empty until
    Task 2.2).
    """
```

with:

```python
    """The merged base is built and verified.

    `merged` and `already_merged` list the tips after the first, in the order
    given; `resolved` lists the tips a resolver fixed, which are in `merged`
    too.
    """
```

Replace the `BaseFailed` docstring:

```python
    """The merged base could not be built. A human reads `detail`.

    `stopped` is True only when a stop signal cut the build short (Task 2.2);
    every failure a clean-merge build raises is a real failure.
    """
```

with:

```python
    """The merged base could not be built. A human reads `detail`.

    `stopped` is True only when a stop signal parked the base's resolver;
    every other failure is a real failure.
    """
```

In `build`'s docstring, replace exactly:

```python
    `already_merged`, so a relaunch re-merges nothing. `store`, `run_id`,
    `story_id`, `runner_factory` and `stop` are unused until Task 2.2.
    """
```

with:

```python
    `already_merged`, so a relaunch re-merges nothing. A conflict needs
    `store`, `story_id` and `runner_factory` to reach the resolver, and fails
    for a human without them; `run_id` defaults to the store's, and `stop=None`
    means nothing can stop the resolver.
    """
```

In `tests/test_bases.py`, replace the first line of the module docstring:

```python
"""Behaviour of `bases.build` (supervisor-tree plan Task 2.1, card 06bf46bb).
```

with:

```python
"""Behaviour of `bases.build` (supervisor-tree plan Tasks 2.1 and 2.2, cards 06bf46bb and 8fe30578).
```

and add this paragraph before the closing `"""` of that docstring:

```python

The resolver path uses a real `Store` and `dispatch.AgentRunner` with an
injected launcher double, `FakeResolver`, ported from
`tests/test_integration.py`. It plays fake `claude`'s resolver mode (pinned by
`tests/e2e/test_fake_claude.py`'s `test_the_resolver_*`) and learns what to do
only from its brief.
```

- [ ] **Step 4: Run the file, then the full suite**

Run: `uv run pytest tests/test_bases.py -v`
Expected: every test PASSES.

Run: `uv run pytest`
Expected: the whole default suite PASSES, including `tests/e2e/test_fake_claude.py`'s `test_the_resolver_*` tests, `tests/test_integration.py` and the existing clean-merge tests in `tests/test_bases.py`. No `e2e`-marked test runs (they are deselected by `addopts`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/bases.py tests/test_bases.py
git commit -m "test(bases): keep the resolved-conflict path off the loop thread; docs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```
