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
