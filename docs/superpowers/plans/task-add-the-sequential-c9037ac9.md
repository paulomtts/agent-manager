<!-- task-pipeline: validated -->
# Add the sequential milestone runner (card c9037ac9)

Parent story: f8290fd6 "Run a milestone: rollup, the shared driver, the runner". Blocked by bf26f482 (rollup, done). This spec narrows addendum O6 (`docs/superpowers/specs/2026-09-24-orchestration-design.md`, lines 81-101), which extends `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. The spec only narrows that design and adds no new design.

## Prerequisites (already in this worktree's base)

These are present in this worktree and must be used as they are, without re-implementing them:

- `census.find_milestone`, `census.flatten_milestone`, `StoryPlan` and `SubtaskPlan`.
- `board.roots`, `board.tree`, `board.show`.
- In `dag`: `assert_no_blocker_cycles`, `compute_levels`, `remaining_subtasks`, `is_story_closed`, `is_subtask_done`, `stack_bases`, `story_tip`, `subtask_branch`, `DependencyCycleError`, `StackRootError`.
- `steps.rollup.set_status`.
- In `cli`: `drive_subtask`, `SubtaskDrive`, `RunnerFactory`, `resolve_repo_dir`, `mint_run_id`, `worktree_for`, `WORKFLOW_NAME`.
- `steps.worktree.run_git` and `GitError`.

`cli.dry_run_payload` already composes the pre-write half (cycles, then levels, then bases over `stories_by_id` of every story). The runner must derive the same geometry the same way.

## Scope

A new module `src/agent_manager/orchestrate.py` exposing:

```python
def run_milestone(milestone: str, *, repo_dir: Path, base_branch: str, branch_prefix: str,
                  commands: Sequence[str] = (), allow_no_verification: bool = False,
                  runner_factory: RunnerFactory | None = None,
                  driver: Driver | None = None,
                  clock: Callable[[], datetime] = <utcnow>) -> dict[str, Any]
```

- `Driver` does not exist yet. `orchestrate.py` defines it as a `typing.Protocol` whose `__call__` takes `cli.drive_subtask`'s keyword-only parameters and returns `cli.SubtaskDrive`. Under `TYPE_CHECKING` or as a string annotation, so that no cli name is resolved at import time.
- The clock default is a module-local `_utcnow` (`datetime.now(timezone.utc)`), not `cli._utcnow`, which is private and would break the circular-import rule.
- `milestone` is a card id or a title needle, as in O1.
- `driver` has `cli.drive_subtask`'s keyword signature and returns a `SubtaskDrive`-shaped value (`.summary.status`, `.summary.failed_phase`, `.summary.detail`, `.warnings`). When it is `None`, it resolves to `cli.drive_subtask` at call time, not at definition time. The sibling card will make `cli` import `orchestrate`, so this module must tolerate the circular import: use `from agent_manager import cli` with attribute access at call time, and do not bind cli names in default arguments.
- The module holds no module-level mutable state (O4).

Out of scope:

- The CLI wiring, the removal of `MilestoneRunNotImplementedError` and any tests/e2e work. All of these belong to 3e0ab2b9.
- Parallel stories, Integrate, a milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, and verification discovery.
- Any change to `run_card` or `drive_subtask` behaviour.

## Observable behaviour, in order

1. **Before any write** (no run directory, store, fetch, prune, worktree or board write):
   - Resolve the repo dir.
   - Resolve the milestone with `census.find_milestone(board.roots(...), milestone)`.
   - Build the census with `flatten_milestone(board.tree(milestone.id))`.
   - Call `dag.assert_no_blocker_cycles(stories)`.
   - Compute `dag.compute_levels(stories)`.
   - Compute `stack_bases` and `story_tip` for every pending story, using `stories_by_id` over all stories, done ones included.
   - Preflight `load_builtin(cli.WORKFLOW_NAME)`, as `run_card` does.

   `DependencyCycleError`, `StackRootError` (two or more in-milestone blockers), `MilestoneNotFoundError` and `BoardError` propagate unchanged from this step.
2. **Once per run:**
   - Run `git fetch origin` only if `git -C <root> remote` lists `origin`. A repo with no `origin` skips the fetch silently.
   - Then run `git worktree prune`.

   Both go through `steps.worktree.run_git`. A `GitError` from either propagates.
3. **Record the plan.**
   - `run_id = cli.mint_run_id(milestone.id, clock())`, then `Store.open(root, run_id)`. The store is closed in a `finally`.
   - Write one `models.Run` with `workflow="milestone"`, `config=models.RunConfig()`, `status="started"` and `started_at` from the clock.
   - Every pending story gets a `StoryRun`: `level` is its level index, `tip_branch` is its tip, `status="pending"`.
   - Every remaining subtask of those stories gets a `SubtaskRun`: `branch` is the derived branch, `base_branch` is from `stack_bases`, `worktree_path` is `cli.worktree_for(root, branch)`, `status="pending"`.
   - All rows are written through `record_run`, `record_story` and `record_subtask`.
4. **Re-roll stale stories.**
   - The target is a story that is not closed, has no remaining subtasks, and whose card status is not `done`. It is re-rolled with `rollup.set_status(anchor, "done", repo_dir=root)`.
   - The anchor is one of its individually done subtasks: the last one in census order. This ports `storyRollupAnchor`. Writing a subtask that is already done is harmless, and the rollup's walk to the root repairs the story and the milestone.
   - When no subtask is individually done, skip the story. A story with no subtasks is one example.
   - A `BoardError` here is appended to `warnings` and does not stop the run, because rollup is best effort, like `mark_done` in `task.yaml`.
5. **Walk the levels.** Levels run in order, stories within a level run in census order, and subtasks run strictly in the full census order. A subtask already `done` by census status is skipped and never driven, but it still anchors the next subtask's base. The bases come from step 1 and are not recomputed. For each remaining subtask:
   - Read `card = board.show(subtask.id)` and `parent = board.show(story.id)` fresh.
   - Record the subtask `started` and the story `started`, the story on its first subtask only.
   - Call `driver(store=, run_id=, card=, parent=, subtask=<the SubtaskRun>, repo_dir=root, commands=, allow_no_verification=, runner_factory=)`.
   - Append the driver's `warnings` to the run's warnings.
6. **Success of a subtask.**
   - When `summary.status == "done"`, record the subtask `done` and append its id to `completed`.
   - When it was the story's last remaining subtask, record the story `done`.
7. **Escalation.**
   - The trigger is the first subtask whose summary status is not `done`, or any `Exception` raised by the driver (not `BaseException`, so `KeyboardInterrupt` still propagates).
   - Record that subtask, its story and the run as `escalated`, and stop scheduling.
   - Return `{"escalated": True, "run_id", "level", "story", "subtask", "failed_phase", "detail", "warnings"}`.
   - For an exception, `failed_phase` is `None` and `detail` is `"<ExceptionType>: <message>"`.
8. **Clean run.** Record the run `done` and return `{"done": True, "run_id", "levels", "completed", "tips", "warnings"}`:
   - `levels`: `[{level, stories: [story ids]}]`.
   - `completed`: the subtask ids driven to done in this run, in order.
   - `tips`: `[{story, tip}]` for every census story that has subtasks, in census order, so the human knows what to merge.

   A milestone with nothing pending still records a run and returns `done`, with empty levels.

The runner returns plain dicts. The `{"ok": true, "data": ...}` envelope belongs to the CLI.

## Tests: `tests/test_orchestrate.py`

**Tier.** Per design spec §14 (the only testing reference; CLAUDE.md indexes no separate standard), a module that composes steps is tested against a real temporary git repo and a real temporary brd board, with no network. The harness is replaced at the injected seam. Here that seam is a fake `driver`, so no runner, adapter or `claude` is involved. That puts these tests at the top level, mirroring `src/agent_manager/orchestrate.py`, next to `tests/test_cli.py`. They are not in `tests/e2e`: production wiring with a fake `claude` is 3e0ab2b9's work. Reuse the fixture pattern from `test_cli.py`: the `project` fixture (git on `main` plus `brd init`, with `XDG_DATA_HOME` under tmp_path), `_add_card`, and the `requires_git`/`requires_brd` skips. Set `blocked_by` edges and `done` statuses through `brd` itself.

**The fake driver.** It records each call's `card.id`, `subtask.branch`, `subtask.base_branch` and `subtask.worktree_path`. It returns a canned `SubtaskDrive`, `done` by default and scriptable per card to escalate or raise. It never touches git or the board.

| # | Test | Asserts |
|---|------|---------|
| 1 | call order and stacking | Story A has two subtasks. Story B is blocked by A and has one subtask. The call order is A1, A2, B1. A1's base is `main`, A2's base is A1's branch, and B1's base is A2's branch (A's tip). The worktree comes from `worktree_for`. The result is `done`, `completed` holds all three, and `tips` names both stories. The store shows the run, both stories and all subtasks `done`. |
| 2 | done subtask skipped but anchors | A1 is already `done` on the board. The driver is called only for A2, and A2's base is still A1's branch. A1 never appears in the recorded remaining subtasks. |
| 3 | escalation stops before the next story | A1 escalates at a named phase. B1 is never called. The result carries `escalated`, `level`, `story`=A, `subtask`=A1, `failed_phase` and `detail`. The store has A1, A and the run `escalated`, and B1 `pending`. |
| 4 | raising driver is an escalation | The driver raises `RuntimeError`. The runner returns the escalation payload (no exception escapes) with `failed_phase` None and a detail naming the exception, and the store records `escalated`. |
| 5 | no origin skips the fetch | The fixture repo has no remote, and the run succeeds. Assert no fetch was attempted, either by wrapping `run_git` via monkeypatch or by checking that the recorded argv lists contain `worktree prune` but no `fetch`. A companion case adds a local bare repo as `origin` (still offline) and asserts that the fetch ran exactly once. |
| 6 | two-blocker refusal writes nothing | Story C is blocked by both A and B. `run_milestone` raises `StackRootError` naming both, the driver is never called, no run directory appears under the data dir, no store is created, and no fetch or prune ran. |
| 7 | stale story re-rolled | A story has all subtasks `done` but the story card is `todo`. After the run, the story is `done` on the board, and the milestone is too when nothing else remains. The driver is never called. |

**Verification.** `uv run pytest` must stay green, including tests/e2e and every existing `run --card` test. There is no lint or typecheck step.

---

# Sequential Milestone Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/orchestrate.py` with `run_milestone`, which drives every remaining subtask of one brd milestone one at a time through an injected driver, stacking each subtask's branch on the one before and stopping at the first escalation (addendum O6).

**Architecture:** One new module that only composes existing collaborators. Pure planning helpers (`plan_levels`, `story_tips`, `stale_story_anchors`) derive levels, stack bases, tips and stale-story anchors from the census with `dag`. `run_milestone` does every refusal before any write, then runs a git refresh once (`git fetch origin` only when an `origin` remote exists, then `git worktree prune`), then records a `milestone` run with its whole plan `pending`, re-rolls stale stories, and walks the levels, calling the driver once per remaining subtask. `cli` is imported as a module and read only at call time, so the sibling card can make `cli` import this module without a circular-import failure.

**Tech Stack:** Python 3.12, pydantic models from `agent_manager.models`, `Store` (SQLite projection plus journal), real `git` and `brd` CLIs in tests, pytest (`--import-mode=importlib`, `-m "not e2e"` by default).

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-add-the-sequential-c9037ac9/docs/superpowers/specs/task-add-the-sequential-c9037ac9-design.md` (reproduced above), narrowing O6 of `docs/superpowers/specs/2026-09-24-orchestration-design.md`.

**Working directory for every command:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-add-the-sequential-c9037ac9` (branch `m3/task-add-the-sequential-c9037ac9`, cut from `m3/task-roll-status-up-the-bf26f482`). The base already contains `census.py` (`find_milestone`, `flatten_milestone`, `StoryPlan`, `SubtaskPlan`), `board.roots`, the `dag` level and geometry functions, `steps/rollup.py` and `cli.drive_subtask` / `cli.SubtaskDrive`. This plan uses them and does not assume any other subtask's code exists.

## Global Constraints

- New code lives in `src/agent_manager/orchestrate.py`. Its tests live in `tests/test_orchestrate.py` (top level, next to `tests/test_cli.py`), not in `tests/e2e`.
- Do not modify `src/agent_manager/cli.py`: no CLI wiring, no removal of `MilestoneRunNotImplementedError`, no change to `run_card` or `drive_subtask`. That is sibling card 3e0ab2b9's work.
- Do not touch `tests/e2e`. No fake `claude` in this card. Milestone-2 rule for any fake: it must never know more than the brief tells it.
- `orchestrate.py` imports `cli` as `from agent_manager import cli` and reads every `cli.<name>` at call time. No `cli` name is bound in a default argument or at module level, and annotations are strings (`from __future__ import annotations`).
- The clock default is a module-local `_utcnow` returning `datetime.now(timezone.utc)`.
- No module-level mutable state in `orchestrate.py` (O4). The only module-level constant is the string `MILESTONE_WORKFLOW = "milestone"`.
- Run workflow name: `"milestone"`. Run config: `models.RunConfig()`.
- Git calls go through `steps.worktree.run_git` read as a module attribute (`worktree.run_git(...)`), with argv `["-C", str(root), "remote"]`, `["-C", str(root), "fetch", "origin"]` and `["-C", str(root), "worktree", "prune"]`.
- The runner returns plain dicts. No `{"ok": ..., "data": ...}` envelope.
- Out of scope: parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, verification discovery.
- Verification: `uv run pytest` (whole default suite, including tests/e2e). No lint, no typecheck.

## Review Focus

- A remote that is not literally `origin` (for example `upstream` or `origin-mirror`): no fetch runs, because the check is exact-name equality on `git remote`'s lines. Pinned in Task 4.
- `git fetch origin` failing (offline, auth): the `GitError` propagates and no run directory or store is created, because the git refresh runs before `Store.open`. Pinned in Task 4.
- The operator pressing Ctrl-C while a subtask is driven: `KeyboardInterrupt` propagates and is not recorded as an escalation, because the catch is `Exception` only. Pinned in Task 3.
- Warnings from the subtask that escalated: they are kept in the escalation payload, not dropped. Pinned in Task 3.
- `brd` failing while a stale story is re-rolled: the run continues, and the failure appears in `warnings` naming the story and anchor subtask. Pinned in Task 5.

---

### Task 1: Pure planning helpers: levels, stack bases and tips

**Files:**
- Create: `src/agent_manager/orchestrate.py`
- Test: `tests/test_orchestrate.py`

**Interfaces:**
- Consumes: `dag.assert_no_blocker_cycles(stories)`, `dag.compute_levels(stories) -> list[list[StoryPlan]]`, `dag.stack_bases(story, stories_by_id, prefix, base_branch) -> dict[str, str]`, `dag.story_tip(story, stories_by_id, prefix, base_branch) -> str`, `dag.remaining_subtasks(story) -> list[SubtaskPlan]`, `census.StoryPlan`, `census.SubtaskPlan`.
- Produces:
  - `orchestrate.PlannedStory`: frozen dataclass with `story: census.StoryPlan`, `level: int`, `bases: dict[str, str]`, `tip: str`, and a `remaining` property returning `list[census.SubtaskPlan]`.
  - `orchestrate.plan_levels(stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str) -> list[list[PlannedStory]]`. It raises `dag.DependencyCycleError` or `dag.StackRootError`.
  - `orchestrate.story_tips(stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str) -> list[dict[str, str]]`, returning `[{"story": id, "tip": branch}]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrate.py` with this full content. The header imports everything later tasks use, so later tasks only append.

```python
"""Behaviour of the sequential milestone runner (orchestration addendum O6).

Two tiers, per design §14:

- `plan_levels`, `story_tips` and `stale_story_anchors` are pure over the
  census and get unit tests on hand-built plans;
- `run_milestone` runs on Steps-tier fixtures -- a real temporary git repo and a
  real temporary brd board, with `XDG_DATA_HOME` under `tmp_path` so
  `paths.data_dir()` never touches the developer's own -- with the harness
  replaced at the injected `driver` seam. No runner, adapter or `claude` is
  involved; production wiring under a fake `claude` belongs to tests/e2e.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import board, census, cli, dag, engine, models, orchestrate, paths
from agent_manager import store as store_module
from agent_manager.steps import rollup, worktree


# ── pure plans ──────────────────────────────────────────────────────────────


def _plan_id(n: int) -> str:
    """A UUID-shaped card id whose short id is `n` in eight hex digits.

    `dag.subtask_branch` goes through `dag.short_id`, which refuses anything
    that is not 32 hex characters, so the pure plans need real-shaped ids.
    """
    return f"{n:08x}-0000-4000-8000-000000000000"


def _plan_subtask(n: int, status: str = "todo") -> census.SubtaskPlan:
    return census.SubtaskPlan(id=_plan_id(n), title=f"subtask {n}", status=status)


def _plan_story(
    n: int,
    subtasks: list[census.SubtaskPlan],
    *,
    status: str = "todo",
    blocked_by: tuple[str, ...] | list[str] = (),
) -> census.StoryPlan:
    return census.StoryPlan(
        id=_plan_id(n),
        title=f"story {n}",
        status=status,
        blocked_by=list(blocked_by),
        subtasks=list(subtasks),
    )


def _branch_of(subtask: census.SubtaskPlan) -> str:
    return dag.subtask_branch("m3", subtask)


def test_plan_levels_stacks_on_the_full_list_and_roots_on_a_done_blockers_tip():
    """O2: a done first subtask still anchors the second, and a story blocked by
    a done story roots on that story's tip."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="done")
    b = _plan_story(2, [_plan_subtask(21, "done"), _plan_subtask(22)], blocked_by=[a.id])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[b.id])

    levels = orchestrate.plan_levels([a, b, c], branch_prefix="m3", base_branch="main")

    assert [[planned.story.id for planned in level] for level in levels] == [[b.id], [c.id]]
    b_plan, c_plan = levels[0][0], levels[1][0]
    assert (b_plan.level, c_plan.level) == (0, 1)
    assert b_plan.bases == {
        _plan_id(21): _branch_of(a.subtasks[-1]),
        _plan_id(22): _branch_of(b.subtasks[0]),
    }
    assert b_plan.tip == _branch_of(b.subtasks[-1])
    assert [subtask.id for subtask in b_plan.remaining] == [_plan_id(22)]
    assert c_plan.bases == {_plan_id(31): _branch_of(b.subtasks[-1])}
    assert c_plan.tip == _branch_of(c.subtasks[-1])


def test_plan_levels_refuses_a_story_with_two_in_milestone_blockers():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id, b.id])

    with pytest.raises(dag.StackRootError) as caught:
        orchestrate.plan_levels([a, b, c], branch_prefix="m3", base_branch="main")

    assert f"#{a.id}" in str(caught.value)
    assert f"#{b.id}" in str(caught.value)


def test_plan_levels_refuses_a_blocker_cycle_before_any_geometry():
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    with pytest.raises(dag.DependencyCycleError):
        orchestrate.plan_levels([a, b], branch_prefix="m3", base_branch="main")


def test_a_milestone_with_nothing_pending_plans_no_levels():
    a = _plan_story(1, [_plan_subtask(11, "done")], status="done")

    assert orchestrate.plan_levels([a], branch_prefix="m3", base_branch="main") == []


def test_story_tips_name_every_story_with_subtasks_in_census_order():
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="done")
    empty = _plan_story(2, [], blocked_by=[a.id])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id])

    tips = orchestrate.story_tips([a, empty, c], branch_prefix="m3", base_branch="main")

    assert tips == [
        {"story": a.id, "tip": _branch_of(a.subtasks[-1])},
        {"story": c.id, "tip": _branch_of(c.subtasks[-1])},
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'orchestrate' from 'agent_manager'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/orchestrate.py`:

```python
"""The sequential milestone runner (orchestration addendum O6).

`run_milestone` drives every remaining subtask of one milestone, one at a time,
through the shared per-subtask driver (O4). Every derivation belongs to a
collaborator: the milestone and its census to `census`, levels, stack bases and
tips to `dag`, board reads to `board`, rollup to `steps.rollup`, git to
`steps.worktree.run_git`, run state to `Store`. This module decides only the
order of those calls and what a run records.

The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle, a story with two in-milestone blockers, a workflow that will
not load -- runs before the first write, so a refusal leaves no run directory,
no store, no fetch and no prune behind.

`cli` is imported as a module and every name on it is read at call time: the
CLI wiring card makes `cli` import this module, and binding a `cli` name at
import or definition time would break under that circular import. The clock
default is this module's own `_utcnow` for the same reason.

The module holds no mutable state of its own (O4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_manager import census, dag


@dataclass(frozen=True)
class PlannedStory:
    """One pending story with its level and its derived stack geometry.

    Internal state that crosses no process boundary, so a dataclass
    (CLAUDE.md). `bases` covers the story's FULL ordered subtask list, done
    ones included, so a done first subtask still anchors the second (O2).
    """

    story: census.StoryPlan
    level: int
    bases: dict[str, str]
    tip: str

    @property
    def remaining(self) -> list[census.SubtaskPlan]:
        """The subtasks still to drive, in census order."""
        return dag.remaining_subtasks(self.story)


def plan_levels(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[list[PlannedStory]]:
    """Dispatch levels with each pending story's bases and tip, derived before any write.

    The same composition as `cli.dry_run_payload`: the cycle check runs first,
    because a cycle is what breaks the geometry, and `stories_by_id` covers
    every story, done ones included, so a story blocked by a done story still
    roots on that story's tip. A story with two in-milestone blockers raises
    `dag.StackRootError` here, from `stack_bases`.
    """
    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    stories_by_id = {story.id: story for story in stories}
    planned: list[list[PlannedStory]] = []
    for index, level in enumerate(dag.compute_levels(stories)):
        planned.append(
            [
                PlannedStory(
                    story=story,
                    level=index,
                    bases=dag.stack_bases(story, stories_by_id, branch_prefix, base_branch),
                    tip=dag.story_tip(story, stories_by_id, branch_prefix, base_branch),
                )
                for story in level
            ]
        )
    return planned


def story_tips(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[dict[str, str]]:
    """Every census story that has subtasks, with the branch its stack ends on.

    What a human merges after a clean run, since this card has no Integrate.
    A story with no subtasks contributes no branch of its own, so it is left
    out.
    """
    stories = list(stories)
    stories_by_id = {story.id: story for story in stories}
    return [
        {
            "story": story.id,
            "tip": dag.story_tip(story, stories_by_id, branch_prefix, base_branch),
        }
        for story in stories
        if story.subtasks
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): derive a milestone's levels, stack bases and tips"
```

---

### Task 2: `run_milestone`: refuse before writing, record the plan, walk the levels

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (the import block, plus new code appended after `story_tips`)
- Test: `tests/test_orchestrate.py` (append)

**Interfaces:**
- Consumes: `plan_levels`, `story_tips`, `PlannedStory` from Task 1. `cli.resolve_repo_dir(Path) -> Path`, `cli.mint_run_id(card_id, now) -> str`, `cli.worktree_for(root, branch) -> Path`, `cli.WORKFLOW_NAME`, `cli.drive_subtask`, `cli.SubtaskDrive(summary, warnings)`, `cli.RunnerFactory`. `census.find_milestone(roots, needle) -> CardNode`, `census.flatten_milestone(root) -> Census`. `board.roots(repo_dir=)`, `board.tree(id, repo_dir=)`, `board.show(id, repo_dir=) -> models.Card`. `Store.open(root, run_id)`, `Store.record_run/record_story/record_subtask`, `Store.close()`. `load_builtin(name)`.
- Produces:
  - `orchestrate.MILESTONE_WORKFLOW = "milestone"`.
  - `orchestrate.Driver`: a Protocol whose `__call__(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None) -> cli.SubtaskDrive`.
  - `orchestrate.record_plan(store: Store, levels: list[list[PlannedStory]], *, root: Path, branch_prefix: str) -> dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]`.
  - `orchestrate.run_milestone(milestone: str, *, repo_dir: Path, base_branch: str, branch_prefix: str, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory: cli.RunnerFactory | None = None, driver: Driver | None = None, clock: Callable[[], datetime] = _utcnow) -> dict[str, Any]`. On a clean run it returns `{"done": True, "run_id", "levels", "completed", "tips", "warnings"}`.
  - Test helpers used by Tasks 3-5: `FakeDriver`, `project` fixture, `_milestone`, `_branch`, `_run`, `_load`, `_statuses`, `_record_git`, `_git`, `_add_card`, `_block`, `requires_git`, `requires_brd`, `STARTED_AT`, `PREFIX`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrate.py`:

```python
# ── the runner, on a real repo and a real board ─────────────────────────────


requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the runner's steps-tier fixtures",
)
requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the runner's steps-tier fixtures",
)

STARTED_AT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
PREFIX = "m3"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)["data"]["id"]


def _block(root: Path, card_id: str, blocker: str) -> None:
    subprocess.run(
        ["brd", "block", card_id, "--by", blocker],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board.

    XDG_DATA_HOME points into tmp_path, which isolates brd's own database and
    `paths.data_dir()`, so no run artifact can land in the developer's home.
    The repo has no remote.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True, text=True
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "brd init")
    return root


def _milestone(
    project: Path,
    stories: dict[str, int],
    blocked_by: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """A milestone whose stories each hold a `brd block` chain of subtasks.

    `stories` maps a story key to its subtask count, in creation order.
    `blocked_by` maps a story key to the keys of the stories blocking it.
    Subtasks are chained so the census order never depends on timestamps.
    """
    milestone = _add_card(project, "Milestone 3: orchestration")
    story_ids: dict[str, str] = {}
    subtask_ids: dict[str, list[str]] = {}
    for key, count in stories.items():
        story = _add_card(project, f"Story {key}", milestone)
        chain: list[str] = []
        for n in range(1, count + 1):
            subtask = _add_card(project, f"{key.lower()}{n}: subtask {n} of story {key}", story)
            if chain:
                _block(project, subtask, chain[-1])
            chain.append(subtask)
        story_ids[key] = story
        subtask_ids[key] = chain
    for key, blockers in (blocked_by or {}).items():
        for blocker in blockers:
            _block(project, story_ids[key], story_ids[blocker])
    return {"milestone": milestone, "stories": story_ids, "subtasks": subtask_ids}


def _branch(project: Path, card_id: str) -> str:
    return dag.task_branch(PREFIX, board.show(card_id, repo_dir=project))


@dataclass
class FakeDriver:
    """Stands in for `cli.drive_subtask`. Never touches git or the board.

    `outcomes` scripts a card: missing means `done`, a `(phase, detail)` tuple
    means escalated at that phase, and an exception instance is raised.
    `warnings` gives a card's canned warnings. Every call is recorded, with a
    snapshot of the store's view of the run at that moment.
    """

    outcomes: dict[str, Any] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[models.Run | None] = field(default_factory=list)

    def __call__(
        self,
        *,
        store,
        run_id,
        card,
        parent,
        subtask,
        repo_dir,
        commands=(),
        allow_no_verification=False,
        runner_factory=None,
    ) -> cli.SubtaskDrive:
        self.calls.append(
            {
                "card": card.id,
                "parent": parent.id,
                "branch": subtask.branch,
                "base": subtask.base_branch,
                "worktree": subtask.worktree_path,
                "status": subtask.status,
                "run_id": run_id,
                "repo_dir": repo_dir,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "runner_factory": runner_factory,
            }
        )
        self.snapshots.append(store.load_run(run_id))
        outcome = self.outcomes.get(card.id)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is None:
            summary = engine.SubtaskSummary(status="done")
        else:
            phase, detail = outcome
            summary = engine.SubtaskSummary(
                status="escalated", failed_phase=phase, detail=detail
            )
        return cli.SubtaskDrive(summary=summary, warnings=list(self.warnings.get(card.id, [])))


def _run(project: Path, milestone: str, driver: Any, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "repo_dir": project,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "driver": driver,
        "clock": lambda: STARTED_AT,
    }
    kwargs.update(overrides)
    return orchestrate.run_milestone(milestone, **kwargs)


def _load(project: Path, run_id: str) -> models.Run:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    return run


def _statuses(run: models.Run) -> dict[str, str]:
    """`{"run": status, <story id>: status, <subtask id>: status, ...}`."""
    found = {"run": run.status}
    for story in run.stories:
        found[story.card_id] = story.status
        for subtask in story.subtasks:
            found[subtask.card_id] = subtask.status
    return found


def _record_git(monkeypatch, fail_on: str | None = None) -> list[list[str]]:
    """Wrap `worktree.run_git` so every argv is recorded; optionally fail one verb."""
    calls: list[list[str]] = []
    real = worktree.run_git

    def recording(argv: list[str]) -> str:
        calls.append(list(argv))
        if fail_on is not None and fail_on in argv:
            raise worktree.GitError("could not reach origin", argv=argv, exit_code=128)
        return real(argv)

    monkeypatch.setattr(worktree, "run_git", recording)
    return calls


@requires_git
@requires_brd
def test_subtasks_run_in_order_each_stacked_on_the_one_before(project):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    root = cli.resolve_repo_dir(project)
    driver = FakeDriver()
    factory = object()

    result = _run(
        project,
        shape["milestone"],
        driver,
        commands=["uv run pytest"],
        allow_no_verification=True,
        runner_factory=factory,
    )

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    branches = {card: _branch(project, card) for card in (a1, a2, b1)}
    assert [call["card"] for call in driver.calls] == [a1, a2, b1]
    assert [call["parent"] for call in driver.calls] == [story_a, story_a, story_b]
    assert [call["branch"] for call in driver.calls] == [branches[a1], branches[a2], branches[b1]]
    assert [call["base"] for call in driver.calls] == ["main", branches[a1], branches[a2]]
    assert [call["worktree"] for call in driver.calls] == [
        cli.worktree_for(root, branches[card]) for card in (a1, a2, b1)
    ]
    assert [call["status"] for call in driver.calls] == ["started"] * 3
    for call in driver.calls:
        assert call["run_id"] == run_id
        assert call["repo_dir"] == root
        assert call["commands"] == ["uv run pytest"]
        assert call["allow_no_verification"] is True
        assert call["runner_factory"] is factory

    assert result == {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": [story_a]}, {"level": 1, "stories": [story_b]}],
        "completed": [a1, a2, b1],
        "tips": [
            {"story": story_a, "tip": branches[a2]},
            {"story": story_b, "tip": branches[b1]},
        ],
        "warnings": [],
    }

    run = _load(project, run_id)
    assert run.workflow == "milestone"
    assert run.config == models.RunConfig()
    assert (run.base_branch, run.branch_prefix, run.repo_dir) == ("main", PREFIX, root)
    assert _statuses(run) == {
        "run": "done",
        story_a: "done",
        a1: "done",
        a2: "done",
        story_b: "done",
        b1: "done",
    }
    assert [(story.card_id, story.level, story.tip_branch) for story in run.stories] == [
        (story_a, 0, branches[a2]),
        (story_b, 1, branches[b1]),
    ]


@requires_git
@requires_brd
def test_the_whole_plan_is_recorded_pending_before_the_first_subtask_is_driven(project):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver()

    _run(project, shape["milestone"], driver)

    first = driver.snapshots[0]
    assert first is not None
    assert _statuses(first) == {
        "run": "started",
        story_a: "started",
        a1: "started",
        a2: "pending",
        story_b: "pending",
        b1: "pending",
    }
    assert {
        subtask.card_id: subtask.base_branch
        for story in first.stories
        for subtask in story.subtasks
    } == {a1: "main", a2: _branch(project, a1), b1: _branch(project, a2)}


@requires_git
@requires_brd
def test_a_done_subtask_is_skipped_but_still_anchors_the_next_base(project):
    shape = _milestone(project, {"A": 2})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    board.set_status(a1, "done", repo_dir=project)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert [call["card"] for call in driver.calls] == [a2]
    assert driver.calls[0]["base"] == _branch(project, a1)
    assert result["completed"] == [a2]
    run = _load(project, result["run_id"])
    assert [subtask.card_id for story in run.stories for subtask in story.subtasks] == [a2]
    assert _statuses(run) == {"run": "done", story_a: "done", a2: "done"}


@requires_git
@requires_brd
def test_a_story_blocked_by_a_done_story_roots_on_that_storys_tip(project):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    for card in (a1, a2, story_a):
        board.set_status(card, "done", repo_dir=project)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert [call["card"] for call in driver.calls] == [b1]
    assert driver.calls[0]["base"] == _branch(project, a2)
    assert result["levels"] == [{"level": 0, "stories": [story_b]}]
    assert result["tips"] == [
        {"story": story_a, "tip": _branch(project, a2)},
        {"story": story_b, "tip": _branch(project, b1)},
    ]


@requires_git
@requires_brd
def test_every_drivers_warnings_reach_the_result_in_order(project):
    shape = _milestone(project, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    driver = FakeDriver(warnings={a1: ["a1 warned"], a2: ["a2 warned", "a2 again"]})

    result = _run(project, shape["milestone"], driver)

    assert result["warnings"] == ["a1 warned", "a2 warned", "a2 again"]


@requires_git
@requires_brd
def test_no_driver_resolves_to_cli_drive_subtask_at_call_time(project, monkeypatch):
    """The sibling card makes `cli` import this module, so the default driver
    must be read off `cli` when the run starts, never bound at import."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    fake = FakeDriver()
    monkeypatch.setattr(cli, "drive_subtask", fake)

    result = _run(project, shape["milestone"], None)

    assert [call["card"] for call in fake.calls] == [a1]
    assert result["done"] is True


@requires_git
@requires_brd
def test_a_milestone_with_nothing_pending_still_records_a_done_run(project):
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    for card in (a1, story_a):
        board.set_status(card, "done", repo_dir=project)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert result == {
        "done": True,
        "run_id": cli.mint_run_id(shape["milestone"], STARTED_AT),
        "levels": [],
        "completed": [],
        "tips": [{"story": story_a, "tip": _branch(project, a1)}],
        "warnings": [],
    }
    run = _load(project, result["run_id"])
    assert _statuses(run) == {"run": "done"}


@requires_git
@requires_brd
def test_a_story_with_two_blockers_is_refused_before_anything_is_written(project, monkeypatch):
    milestone = _add_card(project, "Milestone 3: orchestration")
    first = _add_card(project, "Story one", milestone)
    second = _add_card(project, "Story two", milestone)
    joined = _add_card(project, "Story three", milestone)
    for story in (first, second, joined):
        _add_card(project, f"only subtask of {story}", story)
    _block(project, joined, first)
    _block(project, joined, second)
    porcelain_before = _git(project, "status", "--porcelain")
    git_calls = _record_git(monkeypatch)
    driver = FakeDriver()

    with pytest.raises(dag.StackRootError) as caught:
        _run(project, milestone, driver)

    assert f"#{first}" in str(caught.value)
    assert f"#{second}" in str(caught.value)
    assert driver.calls == []
    assert git_calls == []
    assert list(paths.data_dir().iterdir()) == []
    assert not (project / ".claude").exists()
    assert _git(project, "status", "--porcelain") == porcelain_before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: the 5 Task 1 tests pass. The 8 new tests FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'run_milestone'`.

- [ ] **Step 3: Replace the import block of `src/agent_manager/orchestrate.py`**

Replace:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_manager import census, dag
```

with:

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from agent_manager import board, census, cli, dag, models
from agent_manager.store import Store
from agent_manager.workflow.loader import load_builtin

MILESTONE_WORKFLOW = "milestone"
"""The run's `workflow` field: a milestone run, distinct from `run --card`'s `task`."""


def _utcnow() -> datetime:
    """This module's own clock default. `cli._utcnow` is private, and binding a
    `cli` name at definition time would break under the circular import."""
    return datetime.now(timezone.utc)


class Driver(Protocol):
    """`cli.drive_subtask`'s keyword signature: drive one subtask, report the result.

    The seam the tests replace. Annotations are strings (`from __future__ import
    annotations`), so no `cli` name is resolved when this module is imported.
    """

    def __call__(
        self,
        *,
        store: Store,
        run_id: str,
        card: models.Card,
        parent: models.Card,
        subtask: models.SubtaskRun,
        repo_dir: Path,
        commands: Sequence[str] = (),
        allow_no_verification: bool = False,
        runner_factory: cli.RunnerFactory | None = None,
    ) -> cli.SubtaskDrive: ...
```

- [ ] **Step 4: Append `record_plan` and `run_milestone` to `src/agent_manager/orchestrate.py`**

Append after `story_tips`:

```python
def record_plan(
    store: Store,
    levels: list[list[PlannedStory]],
    *,
    root: Path,
    branch_prefix: str,
) -> dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]:
    """Record every pending story and its remaining subtasks `pending`, and return the rows.

    Written before the walk so `status` shows the whole plan even for a run
    that dies on its first phase. The rows come back keyed by story id, each
    with its subtask rows keyed by subtask id, so the walk records transitions
    as copies of exactly what was planned.
    """
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]] = {}
    for level in levels:
        for planned in level:
            story_row = models.StoryRun(
                card_id=planned.story.id,
                title=planned.story.title,
                level=planned.level,
                status="pending",
                tip_branch=planned.tip,
            )
            store.record_story(story_row)
            subtask_rows: dict[str, models.SubtaskRun] = {}
            for subtask in planned.remaining:
                branch = dag.subtask_branch(branch_prefix, subtask)
                subtask_row = models.SubtaskRun(
                    card_id=subtask.id,
                    branch=branch,
                    base_branch=planned.bases[subtask.id],
                    status="pending",
                    worktree_path=cli.worktree_for(root, branch),
                )
                store.record_subtask(planned.story.id, subtask_row)
                subtask_rows[subtask.id] = subtask_row
            rows[planned.story.id] = (story_row, subtask_rows)
    return rows


def run_milestone(
    milestone: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: cli.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Drive every remaining subtask of `milestone`, one at a time, and report (O6).

    `milestone` is a card id or a title needle (O1). Everything that can refuse
    runs before the store is opened. Then one `milestone` run is recorded with
    its whole plan `pending`, and levels, stories and subtasks are walked in
    order. A subtask already `done` on the board is never driven, but its
    branch still anchors the next subtask's base. The card and its story are
    read fresh from the board before each subtask.
    """
    root = cli.resolve_repo_dir(repo_dir)
    milestone_card = census.find_milestone(board.roots(repo_dir=root), milestone)
    plan = census.flatten_milestone(board.tree(milestone_card.id, repo_dir=root))
    levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    tips = story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    # Fail-fast preflight, as `run_card` does: a workflow that will not load
    # must leave no run directory. The driver loads its own copy.
    load_builtin(cli.WORKFLOW_NAME)
    drive = cli.drive_subtask if driver is None else driver

    started_at = clock()
    run_id = cli.mint_run_id(milestone_card.id, started_at)
    store = Store.open(root, run_id)
    try:
        run_record = models.Run(
            id=run_id,
            workflow=MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(),
        )
        store.record_run(run_record)
        rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
        warnings: list[str] = []
        completed: list[str] = []

        for level in levels:
            for planned in level:
                story_id = planned.story.id
                story_row, subtask_rows = rows[story_id]
                for position, subtask in enumerate(planned.remaining):
                    card = board.show(subtask.id, repo_dir=root)
                    parent = board.show(story_id, repo_dir=root)
                    started = subtask_rows[subtask.id].model_copy(update={"status": "started"})
                    store.record_subtask(story_id, started)
                    if position == 0:
                        store.record_story(story_row.model_copy(update={"status": "started"}))
                    result = drive(
                        store=store,
                        run_id=run_id,
                        card=card,
                        parent=parent,
                        subtask=started,
                        repo_dir=root,
                        commands=list(commands),
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                    )
                    warnings.extend(result.warnings)
                    store.record_subtask(story_id, started.model_copy(update={"status": "done"}))
                    completed.append(subtask.id)
                store.record_story(story_row.model_copy(update={"status": "done"}))

        store.record_run(run_record.model_copy(update={"status": "done"}))
        return {
            "done": True,
            "run_id": run_id,
            "levels": [
                {"level": index, "stories": [planned.story.id for planned in level]}
                for index, level in enumerate(levels)
            ],
            "completed": completed,
            "tips": tips,
            "warnings": warnings,
        }
    finally:
        store.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: 13 passed.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): record a milestone's plan and drive its subtasks in stacked order"
```

---

### Task 3: Escalation stops the run

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (the drive block inside `run_milestone`)
- Test: `tests/test_orchestrate.py` (append)

**Interfaces:**
- Consumes: `run_milestone` and the test helpers from Task 2. `cli.SubtaskDrive.summary` is an `engine.SubtaskSummary` with `status` (`"done"` or `"escalated"`), `failed_phase: str | None` and `detail: str | None`.
- Produces: on escalation, `run_milestone` returns `{"escalated": True, "run_id": str, "level": int, "story": str, "subtask": str, "failed_phase": str | None, "detail": str | None, "warnings": list[str]}` and records the subtask, story and run `escalated`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_an_escalation_stops_the_run_before_the_next_story(project):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        warnings={a1: ["gate warned before the escalation"]},
    )

    result = _run(project, shape["milestone"], driver)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 0,
        "story": story_a,
        "subtask": a1,
        "failed_phase": "review",
        "detail": "reviewer found a blocker",
        "warnings": ["gate warned before the escalation"],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        a2: "pending",
        story_b: "pending",
        b1: "pending",
    }


@requires_git
@requires_brd
def test_an_escalation_in_a_later_level_reports_that_level(project):
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver(outcomes={b1: ("verify", "suite red")})

    result = _run(project, shape["milestone"], driver)

    assert (result["level"], result["story"], result["subtask"]) == (1, story_b, b1)
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "escalated",
        b1: "escalated",
    }


@requires_git
@requires_brd
def test_a_driver_that_raises_is_recorded_as_an_escalation(project):
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver(outcomes={a1: RuntimeError("harness vanished")})

    result = _run(project, shape["milestone"], driver)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result["escalated"] is True
    assert (result["story"], result["subtask"]) == (story_a, a1)
    assert result["failed_phase"] is None
    assert result["detail"] == "RuntimeError: harness vanished"
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "pending",
        b1: "pending",
    }


@requires_git
@requires_brd
def test_a_keyboard_interrupt_from_the_driver_is_not_swallowed(project):
    """The catch is `Exception`, not `BaseException`: Ctrl-C stops the process,
    it is not an escalation a human should go and read."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    driver = FakeDriver(outcomes={a1: KeyboardInterrupt()})

    with pytest.raises(KeyboardInterrupt):
        _run(project, shape["milestone"], driver)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v -k "escalation or raises or keyboard"`
Expected: `test_an_escalation_stops_the_run_before_the_next_story` and `test_an_escalation_in_a_later_level_reports_that_level` FAIL on their assertions (the driver is still called for later subtasks, and the result has no `escalated` key). `test_a_driver_that_raises_is_recorded_as_an_escalation` FAILS with `RuntimeError: harness vanished`. `test_a_keyboard_interrupt_from_the_driver_is_not_swallowed` already PASSES. It pins that the handler added in Step 3 catches only `Exception`.

- [ ] **Step 3: Replace the drive block in `run_milestone`**

In `src/agent_manager/orchestrate.py`, replace:

```python
                    result = drive(
                        store=store,
                        run_id=run_id,
                        card=card,
                        parent=parent,
                        subtask=started,
                        repo_dir=root,
                        commands=list(commands),
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                    )
                    warnings.extend(result.warnings)
                    store.record_subtask(story_id, started.model_copy(update={"status": "done"}))
                    completed.append(subtask.id)
```

with:

```python
                    try:
                        result = drive(
                            store=store,
                            run_id=run_id,
                            card=card,
                            parent=parent,
                            subtask=started,
                            repo_dir=root,
                            commands=list(commands),
                            allow_no_verification=allow_no_verification,
                            runner_factory=runner_factory,
                        )
                    except Exception as error:  # not BaseException: Ctrl-C must still stop
                        status = "escalated"
                        failed_phase: str | None = None
                        detail: str | None = f"{type(error).__name__}: {error}"
                    else:
                        warnings.extend(result.warnings)
                        status = result.summary.status
                        failed_phase = result.summary.failed_phase
                        detail = result.summary.detail

                    if status != "done":
                        store.record_subtask(
                            story_id, started.model_copy(update={"status": "escalated"})
                        )
                        store.record_story(story_row.model_copy(update={"status": "escalated"}))
                        store.record_run(run_record.model_copy(update={"status": "escalated"}))
                        return {
                            "escalated": True,
                            "run_id": run_id,
                            "level": planned.level,
                            "story": story_id,
                            "subtask": subtask.id,
                            "failed_phase": failed_phase,
                            "detail": detail,
                            "warnings": warnings,
                        }

                    store.record_subtask(story_id, started.model_copy(update={"status": "done"}))
                    completed.append(subtask.id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: 17 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): stop a milestone at the first escalated or raising subtask"
```

---

### Task 4: Fetch `origin` if it exists, then prune worktrees, once per run

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (the import block, a new `refresh_git` function, and one call inside `run_milestone`)
- Test: `tests/test_orchestrate.py` (append)

**Interfaces:**
- Consumes: `worktree.run_git(argv: list[str]) -> str` and `worktree.GitError`, both read off `agent_manager.steps.worktree` as module attributes so the tests' monkeypatch takes effect. The test helper `_record_git(monkeypatch, fail_on=None)` comes from Task 2.
- Produces: `orchestrate.refresh_git(root: Path) -> None`, called once in `run_milestone` after every refusal and before `Store.open`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_a_repo_with_no_origin_prunes_worktrees_and_never_fetches(project, monkeypatch):
    shape = _milestone(project, {"A": 1})
    root = cli.resolve_repo_dir(project)
    calls = _record_git(monkeypatch)

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True
    assert [argv[2:] for argv in calls] == [["remote"], ["worktree", "prune"]]
    assert all(argv[:2] == ["-C", str(root)] for argv in calls)


@requires_git
@requires_brd
def test_an_origin_remote_is_fetched_exactly_once_before_the_prune(
    project, tmp_path, monkeypatch
):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True
    )
    _git(project, "remote", "add", "origin", str(origin))
    shape = _milestone(project, {"A": 2})
    calls = _record_git(monkeypatch)

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True
    assert [argv[2:] for argv in calls] == [
        ["remote"],
        ["fetch", "origin"],
        ["worktree", "prune"],
    ]


@requires_git
@requires_brd
def test_a_remote_that_is_not_literally_origin_is_not_fetched(project, tmp_path, monkeypatch):
    other = tmp_path / "other.git"
    subprocess.run(
        ["git", "init", "--bare", str(other)], check=True, capture_output=True, text=True
    )
    _git(project, "remote", "add", "upstream", str(other))
    _git(project, "remote", "add", "origin-mirror", str(other))
    shape = _milestone(project, {"A": 1})
    calls = _record_git(monkeypatch)

    _run(project, shape["milestone"], FakeDriver())

    assert [argv[2:] for argv in calls] == [["remote"], ["worktree", "prune"]]


@requires_git
@requires_brd
def test_a_failed_fetch_propagates_and_leaves_no_run_behind(project, tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True
    )
    _git(project, "remote", "add", "origin", str(origin))
    shape = _milestone(project, {"A": 1})
    _record_git(monkeypatch, fail_on="fetch")
    driver = FakeDriver()

    with pytest.raises(worktree.GitError):
        _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v -k "origin or fetch"`
Expected: the three recording tests FAIL on `assert [] == [["remote"], ...]` (no git call is made yet). `test_a_failed_fetch_propagates_and_leaves_no_run_behind` FAILS with `Failed: DID NOT RAISE <class 'agent_manager.steps.worktree.GitError'>`.

- [ ] **Step 3: Add the `worktree` import**

In `src/agent_manager/orchestrate.py`, replace:

```python
from agent_manager import board, census, cli, dag, models
from agent_manager.store import Store
```

with:

```python
from agent_manager import board, census, cli, dag, models
from agent_manager.steps import worktree
from agent_manager.store import Store
```

- [ ] **Step 4: Add `refresh_git` after `story_tips`**

Insert this function immediately after `story_tips` and before `record_plan`:

```python
def refresh_git(root: Path) -> None:
    """Once per run: `git fetch origin` if an `origin` remote exists, then `git worktree prune`.

    A repo with no `origin` skips the fetch silently. The name must equal
    `origin` exactly: `upstream` or `origin-mirror` is not it. Both calls go
    through `worktree.run_git`, read at call time, and a `GitError` from any
    of them propagates.
    """
    remotes = worktree.run_git(["-C", str(root), "remote"]).split()
    if "origin" in remotes:
        worktree.run_git(["-C", str(root), "fetch", "origin"])
    worktree.run_git(["-C", str(root), "worktree", "prune"])
```

- [ ] **Step 5: Call it in `run_milestone` before the store opens**

In `run_milestone`, replace:

```python
    drive = cli.drive_subtask if driver is None else driver

    started_at = clock()
```

with:

```python
    drive = cli.drive_subtask if driver is None else driver

    # The first side effect. It runs after every refusal and before the store
    # is opened, so a failed fetch leaves no run directory behind.
    refresh_git(root)

    started_at = clock()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: 21 passed. This includes `test_a_story_with_two_blockers_is_refused_before_anything_is_written`, whose `git_calls == []` now pins that the refusal comes before the refresh.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): fetch origin when it exists and prune worktrees once per run"
```

---

### Task 5: Re-roll stale stories through their last done subtask

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (the import block, two new functions, and one line inside `run_milestone`)
- Test: `tests/test_orchestrate.py` (append)

**Interfaces:**
- Consumes: `rollup.set_status(card: str, status: str, repo_dir=None) -> dict`, read off `agent_manager.steps.rollup` as a module attribute. `board.BoardError(message, *, argv, exit_code=None, error_type=None)`. `dag.is_story_closed`, `dag.remaining_subtasks`, `dag.is_subtask_done`.
- Produces:
  - `orchestrate.stale_story_anchors(stories: Sequence[census.StoryPlan]) -> list[tuple[census.StoryPlan, census.SubtaskPlan]]`.
  - `orchestrate.reroll_stale_stories(stories: Sequence[census.StoryPlan], root: Path) -> list[str]`, returning warnings.
  - In `run_milestone`, `warnings` now starts as the list `reroll_stale_stories` returns. It runs after `record_plan` and before the walk.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrate.py`:

```python
def test_only_a_stale_story_is_anchored_and_on_its_last_done_subtask():
    """Port of `storyRollupAnchor`: a story that is not closed and has nothing
    left to run is re-rolled through its last individually done subtask. A
    closed story, a story with work left and a story with no subtasks are not."""
    stale = _plan_story(
        1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="in_progress"
    )
    closed = _plan_story(2, [_plan_subtask(21, "done")], status="done")
    pending = _plan_story(3, [_plan_subtask(31, "done"), _plan_subtask(32)])
    empty = _plan_story(4, [])

    anchors = orchestrate.stale_story_anchors([stale, closed, pending, empty])

    assert [(story.id, anchor.id) for story, anchor in anchors] == [(stale.id, _plan_id(12))]


@requires_git
@requires_brd
def test_a_stale_story_is_rolled_up_to_done_and_so_is_the_milestone(project):
    shape = _milestone(project, {"A": 2})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    board.set_status(a1, "done", repo_dir=project)
    board.set_status(a2, "done", repo_dir=project)
    assert board.show(story_a, repo_dir=project).status == "todo"
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert result["done"] is True
    assert result["levels"] == []
    assert result["warnings"] == []
    assert board.show(story_a, repo_dir=project).status == "done"
    assert board.show(shape["milestone"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_a_failed_stale_rollup_is_a_warning_and_the_run_goes_on(project, monkeypatch):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    board.set_status(a1, "done", repo_dir=project)
    board.set_status(a2, "done", repo_dir=project)

    def failing(card: str, status: str, repo_dir: Any = None) -> dict[str, object]:
        raise board.BoardError("brd is down", argv=["brd", "update", card])

    monkeypatch.setattr(rollup, "set_status", failing)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert [call["card"] for call in driver.calls] == [b1]
    assert result["done"] is True
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert story_a in warning
    assert a2 in warning
    assert "brd is down" in warning
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v -k "stale"`
Expected: `test_only_a_stale_story_is_anchored_and_on_its_last_done_subtask` FAILS with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'stale_story_anchors'`. `test_a_stale_story_is_rolled_up_to_done_and_so_is_the_milestone` FAILS on `assert 'todo' == 'done'`. `test_a_failed_stale_rollup_is_a_warning_and_the_run_goes_on` FAILS on `assert 0 == 1` (no warning is produced).

- [ ] **Step 3: Add the `rollup` import**

In `src/agent_manager/orchestrate.py`, replace:

```python
from agent_manager.steps import worktree
```

with:

```python
from agent_manager.steps import rollup, worktree
```

- [ ] **Step 4: Add the two functions after `refresh_git`**

Insert immediately after `refresh_git` and before `record_plan`:

```python
def stale_story_anchors(
    stories: Sequence[census.StoryPlan],
) -> list[tuple[census.StoryPlan, census.SubtaskPlan]]:
    """Each stale story paired with the subtask its rollup is re-run through.

    A port of `storyRollupAnchor`. A story is stale when it is not closed but
    has no remaining subtasks: every subtask is done and the story card never
    caught up, for example because an earlier run died between the last
    `mark_done` and its rollup. The anchor is its last individually done
    subtask in census order. A story with no done subtask, such as one with no
    subtasks at all, has no anchor and is skipped.
    """
    anchors: list[tuple[census.StoryPlan, census.SubtaskPlan]] = []
    for story in stories:
        if dag.is_story_closed(story) or dag.remaining_subtasks(story):
            continue
        done = [subtask for subtask in story.subtasks if dag.is_subtask_done(subtask)]
        if done:
            anchors.append((story, done[-1]))
    return anchors


def reroll_stale_stories(stories: Sequence[census.StoryPlan], root: Path) -> list[str]:
    """Re-run the rollup through each stale story's anchor, and return warnings.

    Writing `done` to a subtask that is already done is harmless, and the
    rollup's walk to the root repairs the story and the milestone above it.
    Best effort, like `mark_done` in `task.yaml`: a `BoardError` becomes a
    warning naming the story and its anchor, and the run goes on.
    """
    warnings: list[str] = []
    for story, anchor in stale_story_anchors(stories):
        try:
            rollup.set_status(anchor.id, "done", repo_dir=root)
        except board.BoardError as error:
            warnings.append(
                f"could not re-roll stale story {story.id} through subtask {anchor.id}: {error}"
            )
    return warnings
```

- [ ] **Step 5: Start the run's warnings with the re-roll's**

In `run_milestone`, replace:

```python
        rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
        warnings: list[str] = []
```

with:

```python
        rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
        warnings = reroll_stale_stories(plan.stories, root)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: 24 passed.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): re-roll a stale story through its last done subtask"
```

---

### Task 6: Full-suite verification

**Files:**
- None modified.

**Interfaces:**
- Consumes: everything above.
- Produces: a green default suite.

- [ ] **Step 1: Confirm `cli.py` and `tests/e2e` are untouched**

Run: `git diff --stat m3/task-roll-status-up-the-bf26f482 -- src/agent_manager/cli.py tests/e2e`
Expected: no output.

- [ ] **Step 2: Run the whole default suite**

Run: `uv run pytest`
Expected: every test passes (the e2e-marked real-harness test stays deselected by `addopts`), including every existing `run --card` test in `tests/test_cli.py` and all of `tests/e2e`. If anything outside `tests/test_orchestrate.py` fails, stop and use superpowers:systematic-debugging. This card changes no existing module, so a failure elsewhere is new information, not something to patch around.

- [ ] **Step 3: Commit (only if Step 2 required a fix)**

```bash
git add -A
git commit -m "fix(orchestrate): keep the default suite green"
```
