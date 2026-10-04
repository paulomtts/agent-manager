# `lane` reads a merged story's tips directly; `blocker_tips`, `story_done` and `story_ok` are deleted — design

Date: 2026-10-03
Card: `738c9fd0` (story `c8f098a0`, blocked by `27db9eb4`)
Status: approved scope (card), pre-plan

## 0. Parent spec and where to find it

The card cites `docs/superpowers/specs/2026-10-03-merged-root-real-edges-design.md`
§3.2 and §3.3. **That file is not in this worktree.** It was committed to
`master` as `cbe680a`, after this branch's base. Read it with
`git show cbe680a:docs/superpowers/specs/2026-10-03-merged-root-real-edges-design.md`.
Every "parent L<n>" below is a line number in that blob. Do not copy the file
into the worktree. The sibling card `27db9eb4` made the same call
(`docs/superpowers/specs/build-dag-tree-connects-27db9eb4.md` §0).

Line numbers for `src/agent_manager/orchestrate.py` are from this branch's
HEAD (`f389f4d`). The parent spec's line numbers for the same file are a few
lines off, because `27db9eb4` landed in between. Where they disagree, this
spec's numbers are the ones to use.

## 1. Purpose

`27db9eb4` (commits `40e4445`, `4f69330`, `efe671e`, `f389f4d`) made
`build_dag_tree` wire one real `grafo.Node.connect()` edge per blocker, for
any number of blockers (`orchestrate.py:1415-1457`). A merged-root story
(two or more in-milestone blockers) is now a grafo join, not an executor
root. grafo starts a join only after every parent has returned. A parent
that raised (`LaneEscalated`/`LaneStopped`) never sets the event its child
waits for, so the child is never started (parent L155-166).

That leaves the lane-side wait as dead machinery:

- `blocker_tips` (`orchestrate.py:871-894`) awaits `story_done[blocker]` for
  every blocker. By the time grafo calls the lane, every one of those events
  is already set, because each blocker's `node_coroutine.run` sets its event
  in a `finally` before its grafo node returns.
- It returns `None` when some `story_ok[blocker]` is falsy. That cannot
  happen any more: a failed blocker means the merged lane is never called.
- The lane's `if tips is None: return plan.tips[story.id]` early return
  (`orchestrate.py:1202-1203`) is therefore unreachable.

This card deletes that machinery. It is a **pure refactor**: nothing a user,
the run store, the board or the report can observe changes (parent §3.2
L155-166, §3.3 L177-186, §3.5 L202-207).

## 2. Scope

**In scope (this card):**

1. **`lane`'s merged-root branch** (`orchestrate.py:1195-1203`; parent §3.2
   L137-153). When `root_plan.kind == "merged"`, `tips` is
   `[plan.tips[blocker] for blocker in root_plan.blockers]`, computed with
   no `await`, and there is no `None` check or early return. The comment at
   `:1199-1200` ("Waited for even with nothing left to run…") goes. It
   describes a wait that no longer exists. `tips` keeps one meaning: `None`
   for a non-merged root, the blockers' tips (in `root_plan.blockers` order)
   for a merged one. The later `assert tips is not None` (`:1233`) may stay
   or go. Either is fine, because `tips` is always set when that line runs.
2. **`lane`'s signature** (`orchestrate.py:1130-1147`; parent L168-169).
   Drop the keyword-only parameters `story_done: Mapping[str, asyncio.Event]`
   and `story_ok: Mapping[str, bool]`. Every other parameter stays, with
   the same name, type and order.
3. **`blocker_tips` is deleted** (`orchestrate.py:871-894`; parent L177-178).
   Delete the function outright. Do not deprecate it or leave a shim. It has
   exactly one caller (`:1201`), and no test names it.
4. **`supervise`** (`orchestrate.py:1460-1559`; parent L178-186):
   - Delete the `story_done` and `story_ok` dict constructions (`:1508-1509`).
   - Delete `story_done=story_done, story_ok=story_ok` from the `lane(...)`
     call inside `node_coroutine.run` (`:1532-1533`).
   - Delete `story_ok[story.id] = False` (`:1536`) and, inside the `else:`
     branch, only `story_ok[story.id] = True` (`:1542`). The `else:` keyword
     and its `return result` (`:1541`, `:1543`) stay -- that `return` is how
     `run` hands the lane's result back to grafo, which forwards it as
     `tip_<short id>` to every dependent's node. Deleting it along with the
     `story_ok` line would make `run` return `None` on every success. Delete
     the `finally:` that calls `story_done[story.id].set()` (`:1544-1545`).
   - **Keep** the fatal-`BaseException` bookkeeping in `run`'s `except`
     (`:1537-1540`): a `BaseException` that is neither an `Exception` nor an
     `asyncio.CancelledError` is still appended to `fatal`, still sets
     `killed`, and is still re-raised. Every error is still re-raised, and
     the lane's return value is still what `run` returns. `run` keeps
     accepting and ignoring `**tips: str` (parent L127-135).
   - `finished`, `fatal`, `killed`, `build_dag_tree`'s call, the executor,
     `run_until_killed` and `collect_outcomes` are untouched.
5. **Text that names a deleted symbol or describes the deleted wait.** If a
   docstring or comment would name `blocker_tips`, `story_done` or
   `story_ok`, or would say the merged lane waits for its blockers or
   returns early when one failed, it is false after this card. A false
   docstring counts as this card's defect, not a sibling's. The same rule
   was applied in `build-dag-tree-connects-27db9eb4.md` §2 item 2. The
   affected places are:
   - the module docstring, `orchestrate.py:16-21` ("its lane waits for every
     blocker to finish clean, then awaits `bases.build` with their tips
     (`blocker_tips`)");
   - `build_merged_base`'s docstring, `:844-845` ("already resolved by the
     caller in `root_plan.blockers` order (`blocker_tips`)");
   - `lane`'s docstring, `:1150-1152` ("a `merged` one still waits for its
     blockers first") and `:1163-1171` (the whole "A story whose root is
     `merged` is one of `supervise`'s grafo roots … `blocker_tips` returns
     None …" passage, up to and including "Once every blocker is clean, the
     lane takes its slot");
   - `supervise`'s docstring, `:1482-1486` ("Such a story's lane waits on
     each blocker's own completion, signalled by `story_done`/`story_ok` …
     so this never hangs").

   Each is rewritten as a **minimal statement of what is now true**, and
   nothing more:
   - a merged story's lane is reached through one grafo edge per blocker, so
     it runs only after every blocker succeeded;
   - it reads the blockers' tips from `plan.tips` in `root_plan.blockers`
     order;
   - the rest of the merged-base passage in `lane`'s docstring is kept: slot,
     stop check, `build_merged_base`, the `BaseFailed` paths, and `base` on
     the outcome.

   Do **not** add the new rationale (why real edges are safe, a citation of
   grafo's `executor.py` gate, the history of `82c4e3b`). That belongs to
   sibling `010d3744` (§3 below).

**Verification gate (card).** The existing suite is the green-alone gate.
`27db9eb4` already rewrote it to pin the real-edge behavior (parent §4 item 7,
L247-254). A failure means the deletion removed something load-bearing.

## 3. Out of scope

- **`build_dag_tree`** body and docstring. Owned by `27db9eb4`, already
  landed (parent §3.1, L101-135).
- **The "grafo join-starvation" rationale and its correction** (parent §3.4,
  L188-200): stating the real reason in the `build_dag_tree`, `supervise`
  and `lane` docstrings, and the one-sentence pointer added to
  `docs/superpowers/specs/2026-10-01-run-board-design.md`. That file still
  names `story_done`/`story_ok`/`blocker_tips` at L104, L129-130 and L252.
  It is a dated, approved design doc, and the pointer is sibling
  `010d3744`'s job (card description: "must read the actual landed code").
  This card touches only the sentences in §2 item 5, and only far enough to
  stop them being false. The text inside `blocker_tips`'s own docstring
  (`:877-889`) disappears with the function.
- **`base_only_lane`** (`orchestrate.py:1056`). It still receives `tips` as
  its third positional argument, unchanged (parent L170-173).
- **`bases.py`, `dag.py`, `build_merged_base`'s body, `builds_a_base_alone`,
  `StoryRecorder`, `collect_outcomes`, `run_board`/`_run_board_loop`,
  grafo** (parent §3.5, L202-214).
- **Test tier markers.** The supervisor-level merged-root tests in
  `tests/test_orchestrate.py` are marked `@pytest.mark.brd` even though the
  `project` fixture uses `FakeBoard` and starts no `brd` (`:981-990`).
  Re-tiering them is a separate change. This card only has to run them
  (§5).
- Any new test (§5 says why none is needed).

## 4. Observable behavior (unchanged; listed so the gate has something to check)

These all hold today, and each must still hold after the change:

- **B1.** A merged story with every blocker successful builds its base once,
  after both blockers returned. `bases.build` gets the tips as
  `[tip(b) for b in root_plan.blockers]`. The first subtask stacks on
  `<prefix>/base-<short id>` and the next on the one before it. The report
  lists the base.
- **B2.** A merged story with one escalated or stopped blocker is never
  driven and builds no base. Its outcome is `pending`, and so is every
  dependent of it, with or without subtasks.
- **B3.** The four-node shape (A; D blocked by A; B; C blocked by B and D)
  completes and does not hang.
- **B4.** A merged lane that finds the stop already fired builds nothing.
  It is recorded `stopped` at its first remaining subtask.
- **B5.** A subtask-less merged story builds its base alone (`base_only_lane`)
  and a dependent stacks on that base. A closed subtask-less story builds no
  base.
- **B6.** A merged story that is already done holds its dependents until
  its blockers finish, through the edges.
- **B7.** A `BaseException` that is neither an `Exception` nor a
  cancellation, raised inside a lane, still ends `supervise` at once through
  `run_until_killed`. Ordinary lane failures are still reported as outcomes,
  not raised.
- **B8.** On a resume, a merged base is reused and not merged again, and a
  subtask-less story's resolver checkpoint reaches its base.
- **B9 (static).** After the change, `grep -nE
  'blocker_tips|story_done|story_ok' src/` prints nothing.

## 5. Tests that prove it

No new test is added. Every behavior in §4 is already pinned. A test that
checks `blocker_tips` is gone, or that `lane` has no `story_done` parameter,
would only restate the diff, and B9's grep does that job more cheaply.

| Behavior | Existing test (`tests/test_orchestrate.py`) | Tier (as marked) | Why it belongs there |
|---|---|---|---|
| B1 | `test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it` (`:3796`) | `brd`+`git` | Runs `supervise` against real git worktrees (FakeBoard). |
| B2 | `test_a_merged_root_story_with_one_escalated_blocker_is_never_driven` (`:3847`), `test_a_failed_blocker_leaves_the_merged_story_pending_and_builds_no_base` (`:4178`), `test_a_merged_storys_dependent_stays_pending_when_a_blocker_failed` (`:4303`), `test_a_subtask_less_merged_storys_dependent_stays_pending_when_a_blocker_failed` (`:4329`) | `brd`+`git` | Same: the whole tree runs over real branches. |
| B3 | `test_build_dag_tree_four_node_shape_completes` (`:359`) | unit | Pure grafo + `build_dag_tree`, no subprocess. |
| B3 | `test_a_merged_root_story_behind_an_edge_child_completes_instead_of_hanging` (`:3881`) | `brd`+`git` | Production `supervise` wiring with the same shape. |
| B4 | `test_a_merged_lane_that_finds_the_stop_fired_never_builds_its_base` (`:4123`) | `brd`+`git` | Lane path through `supervise`. |
| B5 | `test_only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone` (`:847`) | unit | Pure predicate. |
| B5 | `test_a_subtask_less_story_on_two_blockers_builds_its_base_and_its_dependent_stacks_on_it` (`:4201`), `test_a_closed_subtask_less_story_builds_no_base` (`:4284`) | `brd`+`git` | `base_only_lane` gets the tips from the simplified branch. |
| B6 | `test_a_done_merged_storys_dependent_waits_for_its_blockers` (`:4356`) | `brd`+`git` | This replaces the deleted "waited for even with nothing left to run" comment. |
| B7 | `test_a_keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates` (`:2991`), `test_a_plain_base_exception_in_one_lane_cancels_the_other_and_propagates` (`:3078`), `test_a_keyboard_interrupt_from_the_driver_is_not_swallowed` (`:2049`) | as marked | Pins the `except` bookkeeping this card keeps. |
| B8 | `test_a_merged_base_from_the_interrupted_run_is_reused_and_not_merged_again` (`:4924`), `test_a_resume_hands_a_subtask_less_storys_resolver_checkpoint_to_its_base` (`:4852`) | `brd`+`git` | Resume path through the merged branch. |

**Verification commands.** Run all three, and all must be green.
`uv run pytest` **alone does not run most of the table**, because `addopts`
excludes `brd` (`pyproject.toml:61`):

```bash
uv run pytest                 # unit + git
uv run pytest -m brd          # the merged-root supervisor tests above
uv run pytest -m e2e_fake     # production wiring under the fake claude
grep -nE 'blocker_tips|story_done|story_ok' src/   # B9: must print nothing
```

## 6. Risks and edge cases for the plan's Review Focus

- **Collapsing the `try` too far.** If the `except BaseException` in
  `node_coroutine.run` is removed along with the `story_ok` writes, fatal
  `BaseException`s stop reaching `run_until_killed`, and B7 breaks. Only the
  three `story_*` statements and the now-empty `else`/`finally` go.
- **A merged story whose blocker has no entry in `plan.tips`.** The old code
  read `plan.tips[blocker]` the same way (`:894`). Its `KeyError` exposure
  is unchanged, and this card must not add a `.get` fallback that would hide
  it.
- **Tip order.** `tips` must follow `root_plan.blockers` order, not
  `story.blocked_by` order or `plan.stories` order. B1 pins this.
- **Forwarded kwargs.** `run(**tips: str)` still receives one `tip_<short
  id>` per edge. The lane must keep ignoring them and reading `plan.tips`,
  since `plan.tips` is computed at plan time (parent L127-135, L151-153).
- **Leftover imports.** `Mapping` and `asyncio` are still used elsewhere in
  `orchestrate.py`, so neither import is removed.

---

# `lane` reads a merged story's tips directly — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the dead lane-side wait for a merged-root story's blockers (`blocker_tips`, `story_done`, `story_ok`) so `lane` reads the blockers' tips straight from `plan.tips`, with no observable behavior change.

**Architecture:** `build_dag_tree` already wires one real grafo edge per blocker (card `27db9eb4`), so a merged story's node runs only after every blocker returned successfully. This plan removes the now-unreachable event/flag plumbing from `lane` and `supervise` in `src/agent_manager/orchestrate.py`, then rewrites the four docstrings that still describe the deleted wait. It is a pure refactor. The existing test suite (all three tiers) is the gate, plus a static grep.

**Tech Stack:** Python 3, asyncio, `grafo` 0.3.6, pytest (run through `uv run pytest`).

**Spec:** `docs/superpowers/specs/lane-reads-a-merged-738c9fd0.md` (prepended above). Parent spec: `git show cbe680a:docs/superpowers/specs/2026-10-03-merged-root-real-edges-design.md`. Do **not** copy the parent into the worktree.

## Global Constraints

- Pure refactor: nothing a user, the run store, the board or the report can observe changes (spec §1).
- Only `src/agent_manager/orchestrate.py` is modified. Do not touch `build_dag_tree` (body or docstring), `base_only_lane`, `bases.py`, `dag.py`, `build_merged_base`'s body, `builds_a_base_alone`, `StoryRecorder`, `collect_outcomes`, `run_board`/`_run_board_loop`, grafo, or `docs/superpowers/specs/2026-10-01-run-board-design.md` (spec §3).
- No new test is added, and no test tier marker is changed (spec §3, §5).
- `blocker_tips` is deleted outright: no deprecation and no shim (spec §2 item 3).
- `lane` keeps every other parameter, with the same name, type and order. Only `story_done` and `story_ok` go (spec §2 item 2).
- Docstrings are rewritten as a **minimal statement of what is now true**. Do not add the new rationale (why real edges are safe, grafo's `executor.py` gate, the history of `82c4e3b`), because that is sibling `010d3744`'s job (spec §2 item 5).
- Do not remove the `Mapping` or `asyncio` imports, since both are still used elsewhere (spec §6).
- Verification: `uv run pytest`, `uv run pytest -m brd`, `uv run pytest -m e2e_fake` all green, and `grep -nE 'blocker_tips|story_done|story_ok' src/` prints nothing (spec §5).

## Review Focus

1. **The `except BaseException` in `node_coroutine.run` gets collapsed along with the `story_ok` writes.** If that happens, a `KeyboardInterrupt`/plain `BaseException` inside a lane no longer reaches `run_until_killed`, and `supervise` hangs or swallows it. Expected: it still ends `supervise` at once. Pinned in Task 1 Step 5 by running the B7 tests by name.
2. **`run` returns `None` on success** because the `else:` branch's `return result` was deleted together with `story_ok[story.id] = True`. Expected: every dependent still gets `tip_<short id>` and stacks on its blocker's tip. Pinned in Task 1 Step 5 (B1, B5 dependents) and Task 1 Step 3's exact code.
3. **Tip order.** `tips` must be in `root_plan.blockers` order, not `story.blocked_by` order or `plan.stories` order. Expected: `bases.build` receives `[tip(b) for b in root_plan.blockers]`. Pinned by the B1 test in Task 1 Step 5.
4. **A blocker missing from `plan.tips`.** Expected: a `KeyError` exactly as before, with no `.get` fallback that would silently hide it. Pinned in Task 1 Step 6 by a grep that requires the plain subscript.
5. **A failed blocker.** Expected: the merged story is never driven, builds no base, and it and every dependent stay `pending`. The edges carry this now, not the lane. Pinned by the B2 tests in Task 1 Step 5.

## File Structure

- Modify: `src/agent_manager/orchestrate.py`, the only file touched.
  - Module docstring, lines 16-21.
  - `build_merged_base` docstring, lines 844-845.
  - `blocker_tips`, lines 871-894 (deleted, along with the two blank lines after it).
  - `lane` signature, lines 1145-1146; docstring, lines 1150-1152 and 1163-1171; merged branch, lines 1197-1203.
  - `supervise` docstring, lines 1482-1486; body, lines 1508-1509 and 1532-1545.
- Tests: none created or modified. Merged-root behavior is pinned in `tests/test_orchestrate.py` (spec §5 table).

Task boundaries: Task 1 is the code deletion. The signature change, the caller and the function deletion must land together or the module will not import. Task 2 is the docstring rewrite, which a reviewer could reject on wording while approving the code.

---

### Task 1: Delete the lane-side wait (`blocker_tips`, `story_done`, `story_ok`) from the code

**Files:**
- Modify: `src/agent_manager/orchestrate.py:871-896` (delete `blocker_tips`), `:1145-1146` (`lane` signature), `:1197-1203` (`lane` merged branch), `:1508-1509` and `:1532-1545` (`supervise`)
- Test: existing `tests/test_orchestrate.py` (no edits)

**Interfaces:**
- Consumes: `SupervisorPlan.tips: Mapping[str, str]` (story id → tip branch), `dag.RootPlan.kind` (`"merged"` among others), `dag.RootPlan.blockers: Sequence[str]`.
- Produces: `async def lane(story: census.StoryPlan, *, plan: SupervisorPlan, store: Store, run_id: str, lease_token: str, root: Path, drive: Driver, commands: Sequence[str], allow_no_verification: bool, runner_factory: runs.RunnerFactory | None, slots: asyncio.Semaphore, stop: StopSignal, finished: dict[str, LaneOutcome]) -> str`. That is the same signature minus `story_done` and `story_ok`. `blocker_tips` no longer exists. `supervise`'s signature is unchanged.

- [ ] **Step 1: Write the failing check (RED)**

This is a pure refactor, so the "failing test" is a static check over the code (not docstrings) that the deleted machinery is gone. Run:

```bash
grep -nE 'def blocker_tips|await blocker_tips|story_done[:\[=.]|story_ok[:\[=.]|story_done: |story_ok: ' src/agent_manager/orchestrate.py
```

- [ ] **Step 2: Verify it fails**

Expected: matches are printed (RED), at least at lines 871, 874, 875, 891, 892, 1145, 1146, 1201, 1508, 1509, 1532, 1533, 1536, 1542 and 1545.

Also record the baseline so a later failure can be attributed correctly. Run:

```bash
uv run pytest -q 2>&1 | tail -3
uv run pytest -m brd -q 2>&1 | tail -3
```

Expected: both green. If either is already red before you change anything, stop and report it. Do not fix unrelated failures.

- [ ] **Step 3: Implement the deletion**

3a. Delete `blocker_tips` entirely: lines 871-894, plus the two blank lines that follow, so `build_merged_base` is followed by exactly two blank lines and then `def builds_a_base_alone`. This is the whole block to remove:

```python
async def blocker_tips(
    root_plan: dag.RootPlan,
    plan: SupervisorPlan,
    story_done: Mapping[str, asyncio.Event],
    story_ok: Mapping[str, bool],
) -> list[str] | None:
    """Every blocker's tip, in `root_plan.blockers` order, once each is done.

    A merged-root story is itself one of `supervise`'s grafo roots (T1's own
    dependency edges are not used for a 2+-blocker join: grafo's dynamic
    worker pool can starve a join node forever when an unrelated sibling lane
    is still in flight, a defect in grafo itself, confirmed outside this
    module and out of scope to fix there). This lane instead waits on each
    blocker's own completion signal, then reads its tip off `plan.tips` --
    the same value the blocker's own lane would have returned, computed at
    plan time (`dag.story_tip`), so no data is lost by not using grafo's
    runtime forwarding for this edge. None means a blocker did not finish
    clean (escalated or stopped): the caller must not build the base.
    """
    for blocker in root_plan.blockers:
        await story_done[blocker].wait()
    if not all(story_ok.get(blocker, False) for blocker in root_plan.blockers):
        return None
    return [plan.tips[blocker] for blocker in root_plan.blockers]
```

After the deletion the region reads:

```python
        stop=stop,
        **extra,
    )


def builds_a_base_alone(story: census.StoryPlan, root_plan: dag.RootPlan) -> bool:
```

3b. In `lane`'s signature, replace

```python
    finished: dict[str, LaneOutcome],
    story_done: Mapping[str, asyncio.Event],
    story_ok: Mapping[str, bool],
) -> str:
    """One story's node coroutine (T1, T4, T6): drive its remaining subtasks, return its tip.
```

with

```python
    finished: dict[str, LaneOutcome],
) -> str:
    """One story's node coroutine (T1, T4, T6): drive its remaining subtasks, return its tip.
```

3c. In `lane`'s body, replace

```python
    tips: list[str] | None = None
    if root_plan.kind == "merged":
        # Waited for even with nothing left to run: grafo would have held a
        # blocked story, done or not, behind its blockers' edges.
        tips = await blocker_tips(root_plan, plan, story_done, story_ok)
        if tips is None:
            return plan.tips[story.id]
    if planned is None and not builds_a_base_alone(story, root_plan):
```

with

```python
    tips: list[str] | None = None
    if root_plan.kind == "merged":
        tips = [plan.tips[blocker] for blocker in root_plan.blockers]
    if planned is None and not builds_a_base_alone(story, root_plan):
```

Keep the plain `plan.tips[blocker]` subscript. Do **not** use `.get` (Review Focus 4). Leave the later `assert tips is not None` inside the `async with slots:` block as it is.

3d. In `supervise`'s body, replace

```python
        finished: dict[str, LaneOutcome] = {}
        story_done: dict[str, asyncio.Event] = {story.id: asyncio.Event() for story in plan.stories}
        story_ok: dict[str, bool] = {}
        # A lane's `BaseException` that is neither an `Exception` nor a
```

with

```python
        finished: dict[str, LaneOutcome] = {}
        # A lane's `BaseException` that is neither an `Exception` nor a
```

3e. In `supervise`'s `node_coroutine.run`, replace

```python
                        finished=finished,
                        story_done=story_done,
                        story_ok=story_ok,
                    )
                except BaseException as error:
                    story_ok[story.id] = False
                    if not isinstance(error, (Exception, asyncio.CancelledError)):
                        fatal.append(error)
                        killed.set()
                    raise
                else:
                    story_ok[story.id] = True
                    return result
                finally:
                    story_done[story.id].set()

            return run
```

with

```python
                        finished=finished,
                    )
                except BaseException as error:
                    if not isinstance(error, (Exception, asyncio.CancelledError)):
                        fatal.append(error)
                        killed.set()
                    raise
                else:
                    return result

            return run
```

The `except BaseException` block, the `else:` and its `return result` all stay (Review Focus 1 and 2). `run(**tips: str)` keeps its signature and keeps ignoring `tips`.

- [ ] **Step 4: Run the static check to verify it passes (GREEN)**

```bash
grep -nE 'def blocker_tips|await blocker_tips|story_done[:\[=.]|story_ok[:\[=.]|story_done: |story_ok: ' src/agent_manager/orchestrate.py
```

Expected: no output (exit status 1). The docstrings still mention `blocker_tips`/`story_done`/`story_ok` in prose. Task 2 removes those.

Also confirm the module still imports and the kept imports are still used:

```bash
uv run python -c "import agent_manager.orchestrate as o; assert not hasattr(o, 'blocker_tips'); import inspect; p = inspect.signature(o.lane).parameters; assert 'story_done' not in p and 'story_ok' not in p; print(list(p))"
grep -cE '\bMapping\[' src/agent_manager/orchestrate.py
grep -cE '\basyncio\.' src/agent_manager/orchestrate.py
```

Expected: the parameter list prints as `['story', 'plan', 'store', 'run_id', 'lease_token', 'root', 'drive', 'commands', 'allow_no_verification', 'runner_factory', 'slots', 'stop', 'finished']`. Both counts are ≥ 1, so do not touch the `Mapping`/`asyncio` imports.

- [ ] **Step 5: Run the behavior gate**

First the named tests that pin the spec's B1-B8 and the Review Focus lines (the `brd` ones need `-m brd` because `addopts` excludes them):

```bash
uv run pytest -m brd tests/test_orchestrate.py -v -k "merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it or merged_root_story_with_one_escalated_blocker_is_never_driven or failed_blocker_leaves_the_merged_story_pending_and_builds_no_base or merged_storys_dependent_stays_pending_when_a_blocker_failed or subtask_less_merged_storys_dependent_stays_pending_when_a_blocker_failed or merged_root_story_behind_an_edge_child_completes_instead_of_hanging or merged_lane_that_finds_the_stop_fired_never_builds_its_base or subtask_less_story_on_two_blockers_builds_its_base_and_its_dependent_stacks_on_it or closed_subtask_less_story_builds_no_base or done_merged_storys_dependent_waits_for_its_blockers or merged_base_from_the_interrupted_run_is_reused_and_not_merged_again or resume_hands_a_subtask_less_storys_resolver_checkpoint_to_its_base"
uv run pytest -m "brd or not brd" tests/test_orchestrate.py -v -k "build_dag_tree_four_node_shape_completes or only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone or keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates or plain_base_exception_in_one_lane_cancels_the_other_and_propagates or keyboard_interrupt_from_the_driver_is_not_swallowed"
```

Expected: every selected test PASSES. The first command selects 12 tests and the second selects 5, and neither may report `0 selected` or any deselection of a named test. If a count is short, a test was renamed: find it by its line number in spec §5 and run it by node id.

Then the three full tiers:

```bash
uv run pytest
uv run pytest -m brd
uv run pytest -m e2e_fake
```

Expected: all three green. If any test fails, the deletion removed something load-bearing (spec §2 "Verification gate"). Restore it and do not edit the test.

- [ ] **Step 6: Pin the plain subscript (Review Focus 4)**

```bash
grep -nF 'tips = [plan.tips[blocker] for blocker in root_plan.blockers]' src/agent_manager/orchestrate.py
grep -nE 'plan\.tips\.get\(' src/agent_manager/orchestrate.py
```

Expected: the first prints exactly one line (inside `lane`). The second prints nothing.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/orchestrate.py
git commit -m "refactor: lane reads a merged story's tips from plan.tips; delete blocker_tips, story_done, story_ok (738c9fd0)"
```

---

### Task 2: Rewrite the docstrings that describe the deleted wait

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (module docstring near line 16; `build_merged_base` docstring near line 844; `lane` docstring near lines 1124-1145 after Task 1's deletion; `supervise` docstring near line 1455 after Task 1's deletion)
- Test: none (static grep B9 + the full suites)

**Interfaces:**
- Consumes: Task 1's code (`lane` without `story_done`/`story_ok`, no `blocker_tips`).
- Produces: nothing executable. The docstrings name no deleted symbol and no longer describe a lane-side wait.

Line numbers after Task 1 have shifted by about 26 lines below line 871. Locate each passage by its exact text, which is quoted below.

- [ ] **Step 1: Write the failing check (RED)**

```bash
grep -nE 'blocker_tips|story_done|story_ok' src/
grep -nE 'waits for every blocker|still waits for its blockers|waits for its own blockers|waits on each blocker' src/agent_manager/orchestrate.py
```

- [ ] **Step 2: Verify it fails**

Expected: both print matches (RED). The first finds the module docstring (≈:18), `build_merged_base` (≈:845), `lane` (≈:1138-1139 after Task 1) and `supervise` (≈:1457-1458 after Task 1). The second finds the module docstring, `lane` (two places) and `supervise`.

- [ ] **Step 3: Rewrite the four docstrings**

3a. Module docstring. Replace

```
A story with two or more in-milestone blockers roots on a merged base
(supervisor-tree §5): its lane waits for every blocker to finish clean, then
awaits `bases.build` with their tips (`blocker_tips`), after it took its slot
and before its first subtask, so that
subtask stacks on `<prefix>/base-<short id>`. A lone-blocker story stays the
fast path: no base branch and no extra verify.
```

with

```
A story with two or more in-milestone blockers roots on a merged base
(supervisor-tree §5): it is reached through one grafo edge per blocker, so its
lane runs only after every blocker succeeded. The lane reads the blockers' tips
from `plan.tips` in `root_plan.blockers` order and awaits `bases.build` with
them, after it took its slot and before its first subtask, so that subtask
stacks on `<prefix>/base-<short id>`. A lone-blocker story stays the fast path:
no base branch and no extra verify.
```

3b. `build_merged_base` docstring. Replace

```
    `tips` are the blockers' tips, already resolved by the caller in
    `root_plan.blockers` order (`blocker_tips`). `bases.build` is read off its
    module at call time so a test can replace it. A `None` factory is
```

with

```
    `tips` are the blockers' tips, read by the caller from `plan.tips` in
    `root_plan.blockers` order. `bases.build` is read off its module at call
    time so a test can replace it. A `None` factory is
```

3c. `lane` docstring, first passage. Replace

```
    A story with nothing left to run returns its tip without taking a slot,
    unless `builds_a_base_alone` says it must first build its merged base
    (`base_only_lane`); a `merged` one still waits for its blockers first. Otherwise the lane takes a slot -- grafo started it, so
    every blocker already succeeded -- and drives the remaining subtasks in
```

with

```
    A story with nothing left to run returns its tip without taking a slot,
    unless `builds_a_base_alone` says it must first build its merged base
    (`base_only_lane`). Otherwise the lane takes a slot -- grafo started it, so
    every blocker already succeeded -- and drives the remaining subtasks in
```

3d. `lane` docstring, merged passage. Replace

```
    A story whose root is `merged` is one of `supervise`'s grafo roots (not
    reached through a blocker's edge; `blocker_tips` explains why), so it
    waits for its own blockers here, before taking a slot: `blocker_tips`
    returns None when a blocker did not finish clean, and this lane then
    returns its tip without ever taking a slot, exactly as a story whose
    blocker's edge grafo never fired would (T1's existing contract). Once
    every blocker is clean, the lane takes its slot, checks the stop (fired:
    `stopped` at its first subtask, nothing built), then awaits
    `build_merged_base` before its first subtask, whose recorded base is the
```

with

```
    A story whose root is `merged` is reached through one grafo edge per
    blocker, so its lane runs only after every blocker succeeded; it reads the
    blockers' tips from `plan.tips` in `root_plan.blockers` order. The lane
    takes its slot, checks the stop (fired: `stopped` at its first subtask,
    nothing built), then awaits
    `build_merged_base` before its first subtask, whose recorded base is the
```

The rest of that paragraph (from "merged base branch. `BaseFailed(stopped=False)`…" to "…carries the story's `RootPlan` as `base`, whatever happens after.") is kept verbatim.

3e. `supervise` docstring. Replace

```
    `tip_<short id>`, so a story rooted on a `merged` base (two or more
    in-milestone blockers) is a grafo join, not an executor root. Such a
    story's lane waits on each blocker's own completion, signalled by
    `story_done`/`story_ok` below, and reads the blocker's tip off
    `plan.tips` (`blocker_tips`); every lane sets its own signal on exit,
    success or not, so this never hangs. A milestone with no story has no
    tree to run.
```

with

```
    `tip_<short id>`, so a story rooted on a `merged` base (two or more
    in-milestone blockers) is a grafo join, not an executor root: its lane
    runs only after every blocker succeeded, and reads the blockers' tips from
    `plan.tips` in `root_plan.blockers` order. A milestone with no story has
    no tree to run.
```

Do not add anything about why real edges are safe, grafo's `executor.py`, worker-pool starvation, or `82c4e3b`. That is sibling `010d3744`'s job.

- [ ] **Step 4: Run the static checks to verify they pass (GREEN)**

```bash
grep -nE 'blocker_tips|story_done|story_ok' src/
grep -nE 'waits for every blocker|still waits for its blockers|waits for its own blockers|waits on each blocker|returns early' src/agent_manager/orchestrate.py
git diff HEAD --stat
```

Expected: the first two print nothing (B9). The diff stat lists only `src/agent_manager/orchestrate.py`. Check that `build_dag_tree`'s docstring is untouched: `git diff HEAD -- src/agent_manager/orchestrate.py | grep -n 'def build_dag_tree'` prints nothing.

- [ ] **Step 5: Run the full verification**

```bash
uv run pytest
uv run pytest -m brd
uv run pytest -m e2e_fake
```

Expected: all three green (docstring-only change, so this is a guard against an accidental code edit).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py
git commit -m "docs: orchestrate docstrings state a merged lane runs after its blockers' edges and reads plan.tips (738c9fd0)"
```

---

## Self-review (against the spec)

- §2.1 merged branch → Task 1 Step 3c. §2.2 signature → Task 1 Step 3b. §2.3 delete `blocker_tips` → Task 1 Step 3a. §2.4 `supervise` (dicts, kwargs, `story_ok` writes, `finally`; keep `except` + `else: return result`) → Task 1 Steps 3d-3e. §2.5 the four docstrings → Task 2 Steps 3a-3e.
- §4 B1-B8 → Task 1 Step 5 (named tests + full tiers). B9 → Task 2 Step 4.
- §5 three commands + grep → Task 1 Step 5, Task 2 Steps 4-5.
- §6 risks → Review Focus 1-5, each with a pinning step.
- §3 out of scope → Global Constraints, plus Task 2 Step 4's diff-stat and `build_dag_tree` checks.
- Placeholders: none. Every code edit quotes the exact before/after text.
- Types: `lane`'s produced signature in Task 1 Interfaces matches Step 3b and Step 4's printed parameter list.
<!-- task-pipeline: validated -->
