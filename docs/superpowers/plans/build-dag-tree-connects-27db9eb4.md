<!-- Spec prepended verbatim from docs/superpowers/specs/build-dag-tree-connects-27db9eb4.md -->

# `build_dag_tree` connects every blocker; roots are zero-blocker items only — design

Date: 2026-10-03
Card: `27db9eb4` (story `c8f098a0`)
Status: approved scope (card), pre-plan

## 0. Parent spec and where to find it

The card cites `docs/superpowers/specs/2026-10-03-merged-root-real-edges-design.md`
§3.1 and §4 items 1-2, 4-7. **That file is not in this worktree**: it was
committed to `master` as `cbe680a` ("Add design spec: merged-root stories get
real grafo edges"), after this branch's base (`m18-integrate`). Read it with
`git show cbe680a:docs/superpowers/specs/2026-10-03-merged-root-real-edges-design.md`.
Every "parent L<n>" below is a line number in that blob. This card does not
copy the file into the worktree; the planner must not either (a sibling card
or the integrate merge brings it).

## 1. Purpose

Today `build_dag_tree` (`src/agent_manager/orchestrate.py:1415-1460`) wires an
item with exactly one blocker by a real `grafo.Node.connect()` edge, and makes
an item with two or more blockers an unconnected executor root
(`orchestrate.py:1451-1459`). That root's coroutine then waits on
`story_done`/`story_ok` events inside a grafo worker. The worker counts as
busy to grafo's pool-resize logic, but it makes no progress. If one of the
blockers is an edge-child that is ready only after the pool shrank, the
ready node is stuck behind queued exit sentinels and `executor.run()` never
returns (parent L47-60).

grafo already supports a multi-parent join. Each `connect()` appends one
event to the child's `_parent_events`. A child is enqueued only once every
event is set (parent L23-35; the pinned grafo's `executor.py`, the `all(e.is_set()
for e in child._parent_events)` check in `__worker`). So the fix is to wire a
real edge from every blocker and make only blocker-free items roots (parent
§3.1, L101-135).

### 1.1 The hang shape, checked while writing this spec

The card describes the shape as "A, B independent; C blocked by both; B
finishes before A". **That three-node shape alone does not hang.** It was
run against the pinned grafo with today's `build_dag_tree`, and it completes.
The parent calls it "four-node" and says the hang needs "one of the merged
story's own blockers [to be] a single-blocker edge-child" (parent L50-56).
The shape that reliably hangs today, checked 5/5 times against the real
`build_dag_tree` and real grafo, is:

```
A (no blocker)        B (no blocker)
  |                      |
  v                      |
D (blocked by A)         |
   \                     /
    v                   v
     C (blocked by B and D)
```

B finishes, then A finishes after a real wall-clock gap (`asyncio.sleep(0.05)`
in A's coroutine), so D becomes ready only after B's worker shrank the pool.
Today: C sits in a worker waiting on D's event, D is queued behind `None`
sentinels, hang. With the §3.1 fix: completes, all four outputs set.

A sleep is needed for this to fail first. A purely event-ordered version
(A waits on B's done event, then yields any number of `sleep(0)` ticks, or
sleeps ≤20 ms after that event) did **not** reproduce the hang. The timing
window is wall-clock, not ordering. This is the one place the tests in §5 may
use a real `asyncio.sleep`. It must stay short (≤0.05 s) and carry a comment
saying why.

## 2. Scope

**In scope (this card):**

1. `build_dag_tree` wires one `parent.connect(child, forward=...)` per blocker,
   for any blocker count, in one loop. Only a zero-blocker item is a root
   (parent §3.1, L101-120).
2. `build_dag_tree`'s docstring is rewritten to state the new contract. The
   current text (`orchestrate.py:1424-1444`) would be false after the change:
   "An item with two or more blockers gets no incoming edge and is one of the
   executor's roots". A docstring that contradicts its function counts as a
   defect of this card, not a sibling's.
3. The one sentence in `supervise`'s docstring that describes the *tree
   shape* (`orchestrate.py:1480-1486`) is corrected: every blocker now gets an
   edge, and a merged story is no root. The following sentences about
   `story_done`/`story_ok`/`blocker_tips` (`:1486-1490`) stay as they are.
   They still describe live code (see Out of scope). The only change is that
   they must no longer claim the merged story is an "extra executor root".
4. The tests in §5.

**Out of scope (sibling cards under story `c8f098a0`, parent §3.2-3.4):**

- `lane`'s merged-root branch (`orchestrate.py:1198-1203`), `blocker_tips`
  (`:871-894`), the `story_done`/`story_ok` dicts and their writes in
  `supervise` (`:1512-1513, 1536-1549`), and `lane`'s parameters for them.
  All stay. Under this card's change they become a redundant wait that
  returns at once. A blocker's `story_done` is set in `node_coroutine.run`'s
  `finally`, and that runs before grafo's own `_event.set()` for the same
  node, so by the time grafo enqueues C every blocker's `story_done` is
  already set. This is not a behavior change.
- The stale "grafo join-starvation" rationale in `blocker_tips`'s docstring
  (`:877-889`) and `lane`'s docstring (`:1162-1167`), and the
  `2026-10-01-run-board-design.md` pointer (parent §3.4, L188-200).
- `dag.py`'s `RootPlan.kind == "merged"` classification, `builds_a_base_alone`,
  `base_only_lane`, `bases.build_merged_base`. All untouched (parent §3.5,
  L202-214).
- The grafo pool-resize delay itself. It is a grafo defect, filed against
  `grafo` and not fixed here (parent L87-91, L263-268).
- `run_board` / `_run_board_loop` (`orchestrate.py:2194-2288`). This card
  changes no code there, but they call the same `build_dag_tree`, so a
  2+-blocker milestone now gets real edges too (parent L209-214). Their
  existing tests are a regression gate (§5, R4).

**Never:** pushing, PRs, touching `main`/`master`. Never `git commit` the
spec here; the workflow's `docs_commit` step commits it.

## 3. Observable behavior

`build_dag_tree(items, *, id_of, blockers_of, node_factory, forward=None)`. Its
signature and return type `tuple[dict[str, grafo.Node], list[grafo.Node]]` are
unchanged.

- **B1 — nodes.** There is one `grafo.Node` per item, with `uuid=id_of(item)`
  and `timeout=None`. `nodes` is keyed by `id_of(item)` in `items` order. This
  is unchanged.
- **B2 — edges.** For every item and every id in `blockers_of(item)`, in
  `blockers_of` order, there is one
  `await nodes[blocker].connect(nodes[id_of(item)], forward=name)` call, where
  `name = None if forward is None else forward(items_by_id[blocker])`.
  `forward` is called once per edge and handed the blocker *item*. An item
  with N blockers therefore appears in N parents' `children` and has
  `len(node._parent_events) == N`.
- **B3 — roots.** `roots` holds exactly the items with zero blockers, in
  `items` order. An item with one or more blockers is never in `roots`.
- **B4 — forwarding.** Each edge forwards under its own name. A child with
  two forwarding parents receives both kwargs, e.g.
  `{"from_a": "out-a", "from_b": "out-b"}`. A parent whose `forward` returns
  `None` forwards nothing on that edge only.
- **B5 — scheduling.** Under `grafo.TreeExecutor(roots=roots).run()`, a node
  with blockers runs only after every blocker's coroutine returned normally.
  If any blocker raised an `Exception`, the node's coroutine is never called.
  That is grafo's own gate, and this module adds no waiting.
- **B6 — no new validation.** A blocker id not among `items` is still the
  caller's to avoid (it raises `KeyError`, as today). Duplicate blocker ids
  are not de-duplicated. `supervise`'s blockers come from
  `plan.roots[...].blockers` and `run_board`'s from `dict.fromkeys(...)`, so
  neither passes duplicates. Empty `items` gives `({}, [])`.

Through `supervise`:

- **S1.** A merged-root story (2+ in-milestone blockers) is dispatched only
  after every blocker's lane returned. Its base is built from its blockers'
  tips exactly as today. The four tests in R1-R4 below assert this unchanged.
- **S2.** A merged-root story with one escalated blocker is never driven. No
  base is built, none of its subtasks reaches the driver, and it and its
  subtasks are reported and recorded `pending`. This matches what
  `test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending`
  already pins for a single-blocker dependent.
- **S3.** The §1.1 shape completes. `run_milestone` returns `done: True`
  within the hang guard, and every story, C included, is recorded `done`.

## 4. Error paths

- A blocker's coroutine raises `Exception`: grafo records it in
  `executor.errors`, sets its stop, and never enqueues the blocker's
  children. No dependent node runs, for any blocker count (B5).
  `collect_outcomes` reports each such dependent `pending`, unchanged.
- A blocker raises a non-`Exception` `BaseException`: unchanged.
  `run_until_killed` re-raises it out of `supervise`.
- `forward` raises: it propagates out of `build_dag_tree` before any executor
  runs, as today. No change.

## 5. Tests

All in `tests/test_orchestrate.py`, next to the existing `build_dag_tree` tests
(`:206-312`), using the `_DagItem`/`_dag_factory`/`_dag_forward`/`_build`
harness (`:164-204`), or next to the merged-base tests for the `supervise`
ones. Tier is chosen by what the test spawns (`CLAUDE.md`, "Test tiers").

### Unit tier (unmarked; pure grafo + asyncio, no subprocess; ≤0.5 s each)

- **U1 — rewrite `test_build_dag_tree_multi_blocker_is_root_not_edge` →
  `test_build_dag_tree_multi_blocker_gets_an_edge_from_every_parent`**
  (parent §4 item 2, L224-228). Use `[a, b, c(a, b)]` and assert:
  - `nodes["a"].children == [nodes["c"]]` and `nodes["b"].children == [nodes["c"]]`;
  - `nodes["c"] not in roots` and `roots == [nodes["a"], nodes["b"]]`;
  - `len(nodes["c"]._parent_events) == 2`, and the entries are
    `nodes["a"]._event` and `nodes["b"]._event`;
  - after `TreeExecutor.run()`, `calls["c"] == {"from_a": "out-a", "from_b": "out-b"}`.

  This test fails before the fix, because today C is a root with no edges.
- **U2 — rewrite `test_build_dag_tree_roots_follow_items_order`** (parent §4
  item 4, L232-234). Use items `[a, c(a, b), b, e]`, where `e` has no blocker,
  and assert `roots == [nodes["a"], nodes["b"], nodes["e"]]` and
  `nodes["c"] not in roots`. The zero-blocker items keep `items` order, and
  `e` after `b` proves the order is not alphabetical by accident. This fails
  before the fix.
- **U3 — keep `test_build_dag_tree_merged_item_still_forwards_to_its_dependent`**
  (parent §4 item 3, L229-231). Its `roots` assertion
  (`[a, b, joined]`) contradicts B3, so it changes to `[a, b]`. Its docstring
  drops "A merged item is a root". The forwarding assertion
  (`calls["d"] == {"from_joined": "out-joined"}`) and the
  `nodes["joined"].children == [nodes["d"]]` assertion stay as they are. The
  card says to keep the test, and the parent says it is "still true, now via
  real edges". The behavior it exists for, forwarding through a merged item,
  is what is kept.
- **U4 — keep `test_build_dag_tree_single_blocker_wires_edge`** unchanged
  (parent §4 item 1, L222-223). Also keep
  `test_build_dag_tree_no_forward_connects_without_kwarg`,
  `test_build_dag_tree_forward_returning_none_connects_without_kwarg` and
  `test_build_dag_tree_empty_items` unchanged. They hold under B2-B6.
- **U5 — new `test_build_dag_tree_never_runs_an_item_whose_blocker_raised`**
  (B5; the unit-level pin for parent §4 item 5). Use `[a, b, c(a, b)]`, with a
  factory whose `a` coroutine raises `RuntimeError`. After
  `TreeExecutor.run()`, assert `"c" not in calls`, `nodes["c"].output is None`,
  and the executor's `errors` holds the `RuntimeError`. This fails before the
  fix, because today C is a root and is called.
- **U6 — new `test_build_dag_tree_four_node_shape_completes`** (§1.1; the
  default-suite pin for parent §4 item 6). Use items in order
  `[a, b, d(a,), c(b, d)]`, with a factory that reproduces `lane`'s current
  discipline: a 2+-blocker item's coroutine first awaits each blocker's own
  `asyncio.Event`, every coroutine sets its own event in a `finally`, and
  `a`'s coroutine `await asyncio.sleep(0.05)` before returning (comment: the
  pool-shrink window is wall-clock, §1.1). Wrap
  `TreeExecutor(...).run()` in `asyncio.wait_for(..., 2.0)`. A `TimeoutError`
  fails the test with a message naming the hang. Assert all four nodes have
  output. This was checked while writing this spec: it hangs (times out)
  against today's `build_dag_tree` and completes with the fix. Unit tier
  because it spawns nothing; its worst case is 2 s on the red run, and well
  under 0.5 s green.

### `brd` + `git` tier (opt-in `uv run pytest -m brd`; real `brd` board via the `project` fixture, real git in `tmp_path`)

These go through `run_milestone` → `supervise` with the production wiring, so
they need the real board (`_milestone(project, ...)`). That is why they are
`@pytest.mark.brd @pytest.mark.git`, like every other `supervise`-level test
around them (e.g. `:2690`, `:3700`).

- **I1 — new `test_a_merged_root_story_with_one_escalated_blocker_is_never_driven`**
  (S2; parent §4 item 5, L235-240). Use `_milestone(project, {"A": 1, "B": 1,
  "C": 1}, blocked_by={"C": ["A", "B"]})` with the `fake_bases` fixture. Set
  `GatedDriver(outcomes={a1: ("review", "reviewer found a blocker")},
  gates={a1: <await b1's returned event via _within>}, returned={b1: Event()})`
  so B finishes clean before A escalates. Assert:
  - `c1` is never in `driver.calls` and `fake_bases.calls == []`;
  - the result is escalated on A with `subtask == a1`;
  - `_statuses(...)[story_c] == "pending"` and `[c1] == "pending"`, and B is `done`.

  This passes before and after the fix, by design. Today's early return in
  `lane` and grafo's gate after the fix give the same outcome, and the test
  pins that they agree (parent L159-166). It is a regression pin, not a red
  test, and the plan must say so instead of faking a red step.
- **I2 — new `test_a_merged_root_story_behind_an_edge_child_completes_instead_of_hanging`**
  (S3, §1.1; parent §4 item 6, L241-246). Use `_milestone(project, {"A": 1,
  "B": 1, "D": 1, "C": 1}, blocked_by={"D": ["A"], "C": ["B", "D"]})` with
  `fake_bases`. The `GatedDriver` gates `a1` on b1's `returned` event and
  then `asyncio.sleep(0.05)`, with the same §1.1 comment. Run it through
  `_run_or_fail_if_it_hangs(lambda: _run(project, shape["milestone"], driver,
  max_concurrent=2))` (`:2957`, bounded at `WAIT * 3`). Assert
  `result["done"] is True`, that C's base was built once, and that C, c1 and D
  are `done`. The plan's red step must run this against today's code and
  record whether it hangs (the guard's `pytest.fail`). A real lane does store
  and git I/O between B's return and A's, which may change the window. If it
  does not hang on today's code, the planner widens the sleep within the
  0.05 s cap or adds stories in parallel with B (more shrink sentinels), and
  says in the plan which variant reproduced. It must not ship a "red" step
  that was never observed red. U6 stays the default-suite pin either way. The
  hung daemon thread left by a red run is the established cost of this guard
  (`:2957-2979`).

### Regression gate — unchanged, must stay green (parent §4 item 7, L247-254)

- **R1** `test_only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone` (`:753`, brd+git)
- **R2** `test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it` (`:3702`, brd+git)
- **R3** `test_a_run_with_no_merged_root_builds_no_base_and_reports_no_bases` (`:3790`, brd+git)
- **R4** `test_the_open_cards_are_the_remaining_subtasks_and_every_open_merged_roots_resolver` (`:4406`, brd+git)
- **R5** `test_run_board_runs_a_two_blocker_milestone_only_after_both_finish_done`
  and `test_run_board_blocks_a_two_blocker_milestone_on_only_its_unclean_blocker`
  (`:6945`, `:6961`, unit). These are not named by the card but are affected
  through the shared function (parent L209-214). `run_board`'s node never
  raises an `Exception`, so its edges fire even on an unclean blocker, and the
  existing `milestone_ok` check still produces `blocked`. Both must pass
  unchanged.

## 6. Verification

- `uv run pytest` (unit + git): U1-U6 and R5 green, nothing else regressed.
- `uv run pytest -m brd tests/test_orchestrate.py`: I1, I2, R1-R4 green.
- `uv run pytest -m e2e_fake` (from the card's explore findings): green.

## 7. Risks

- **I2 may not reproduce red under real lanes** (§5, I2). U6 is the
  dependable pin, and I2's red step must be observed, not assumed.
- **Duplicated blocker ids** would now add two edges and two parent events
  for one blocker (B6). This is harmless in grafo, because both events are
  the same parent's, but no caller passes duplicates and this card adds no
  de-duplication.
- **The uncommitted repro behind `82c4e3b`** (parent L269-275). If a new hang
  shows up with real edges, that is evidence against the parent's premise.
  It reopens the parent spec and is not patched here.

---

# `build_dag_tree` connects every blocker; roots are zero-blocker items only — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `build_dag_tree` wire one real `grafo.Node.connect()` edge per blocker (any count), so only zero-blocker items are executor roots, and pin the result with unit and supervisor-level tests.

**Architecture:** One loop change in `src/agent_manager/orchestrate.py::build_dag_tree` (drop the `len(blockers) == 1` branch; every blocker gets an edge, an item is a root only when it has no blocker), plus two docstring corrections (`build_dag_tree`, `supervise`). Tests go in `tests/test_orchestrate.py`: unit tests beside the existing `build_dag_tree` tests, and two `brd`+`git` supervisor tests beside the merged-base tests. Nothing else in `lane`, `blocker_tips`, `story_done`/`story_ok`, `dag.py` or `bases.py` changes.

**Tech Stack:** Python 3.12, `grafo` 0.3.5 (pinned in `uv.lock`), `pytest` with `asyncio_mode = "auto"`, `uv`.

**Spec:** `docs/superpowers/specs/build-dag-tree-connects-27db9eb4.md` (prepended in full below). Parent spec: `git show cbe680a:docs/superpowers/specs/2026-10-03-merged-root-real-edges-design.md` (not in this worktree; do not copy it in).

## Global Constraints

- `build_dag_tree`'s signature and return type `tuple[dict[str, grafo.Node], list[grafo.Node]]` are unchanged.
- Every node keeps `timeout=None`.
- Out of scope, do not touch: `lane`'s merged-root branch (`orchestrate.py:1198-1203`), `blocker_tips` (`:871-894`), the `story_done`/`story_ok` dicts and their writes in `supervise`, the `blocker_tips`/`lane` docstrings' "grafo join-starvation" text, `dag.py`, `bases.py`, `run_board`/`_run_board_loop`, and grafo itself.
- No new validation in `build_dag_tree`: unknown blocker ids still raise `KeyError`; duplicates are not de-duplicated.
- The only real `asyncio.sleep` with a non-zero delay allowed in the new tests is the ≤0.05 s one that opens grafo's pool-shrink window, and it carries a comment saying why.
- Never push, open a PR, or touch `main`/`master`. Never `git commit` the spec (the workflow's `docs_commit` step does that).
- Test tier is chosen by what the test spawns (`CLAUDE.md`, "Test tiers"): unit tests unmarked, supervisor tests `@pytest.mark.brd` + `@pytest.mark.git` like their neighbours.

## Review Focus

1. **An unrelated sibling lane is still running when a merged story's last blocker finishes.** A person expects the merged story to start as soon as its blockers are done. With real edges, grafo's pool-shrink delay holds C until the unrelated sibling finishes. **This was observed while planning and breaks four existing `brd`-tier tests and one `e2e_fake` test (see "Planning finding" below).** Task 3, Steps 2-3 run them and are the stop condition.
2. **A blocker escalates while the merged story's other blocker finishes clean.** Expected: the merged story is never driven, no base is built, and it stays `pending`. Pinned by I1 (Task 2).
3. **A blocker raises an `Exception` at grafo level.** Expected: no dependent node runs, whatever its blocker count. Pinned by U5 (Task 1).
4. **The §1.1 four-node shape.** Expected: the run completes instead of hanging. Pinned by U6 (Task 1, unit) and I2 (Task 2, supervisor).
5. **A `run_board` milestone with two blockers, one unclean.** Expected: still `blocked`, via the existing `milestone_ok` check, now with real edges. Pinned by R5 (existing tests, run in Task 1, Step 8).

## Planning finding — read before starting

The planner applied the spec's §3.1 change to a scratch copy and ran every tier. Results:

- **Default suite (`uv run pytest`):** all green except the three `build_dag_tree` tests this plan rewrites (U1-U3). R5 is green.
- **New tests, observed red on today's code and green with the fix:** U5 (with a one-tick `await asyncio.sleep(0)` before `a` raises; without that yield `a` raises before any worker dequeues C and the test passes on today's code), U6 (with a `sleep(0)` yield at the top of every coroutine, see below), and I2 (the spec's own variant: `a1` gated on `b1`'s `returned` event, then `asyncio.sleep(0.05)`; it hit `_run_or_fail_if_it_hangs`'s `pytest.fail` after 30 s, on 1 of 1 runs). I1 passes before and after, as the spec predicts.
- **U6 needs one change from the spec's wording.** As the spec words it (only `a` sleeps), the shape does **not** hang on today's code: `b` returns without suspending, so its worker shrinks the pool while C is still queued, and C is then picked up by the one worker that survives. Adding `await asyncio.sleep(0)` at the top of every coroutine lets a worker take C before `b` returns. That is the shape the spec describes ("C sits in a worker waiting on D's event"), and it timed out on 5 of 5 runs on today's code. With the fix it completes in under 0.5 s. `sleep(0)` is a scheduling yield, not a wall-clock wait, so the ≤0.05 s rule still holds.
- **`uv run pytest -m brd tests/test_orchestrate.py` with the fix: 4 previously green tests fail, deterministically (2 of 2 runs). The spec's regression gate (R1-R5) does not list them:**
  - `test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling`
  - `test_a_base_whose_resolver_was_stopped_ends_stopped_not_escalated`
  - `test_a_base_whose_resolver_was_stopped_gets_no_base_failed_comment`
  - `test_a_merged_lane_that_finds_the_stop_fired_never_builds_its_base`

  All four are green on today's code (135 passed). The mechanism is the grafo pool-shrink defect the parent spec calls "bounded". Take roots A, B and D, with C blocked by A and B. When A returns, the pool shrinks to zero live workers and queues three `None`s; A's own worker consumes one. When B returns, C is enqueued behind the two remaining `None`s, and grafo adds only `len(B.children) == 1` worker. That worker and B's worker consume the two `None`s, so C waits until D's worker frees up. The first three tests hold D (or need D) in flight until C's base acts, so C never runs and they time out ("timed out waiting for the run's stop" / "C's base to start building"). The fourth (`max_concurrent=1`) no longer drives `joined` at all, so `result` has no `"stopped"` key. In production this would not hang forever. But a merged story would no longer run beside an unrelated in-flight lane: it waits for that lane to finish, which can take hours.

  The spec's §7 says: "If a new hang shows up with real edges, that is evidence against the parent's premise. It reopens the parent spec and is not patched here." **Task 3, Step 2 is therefore a stop condition.** If those four tests fail, do not edit them, do not weaken them, and do not work around grafo in `build_dag_tree`. Escalate with the failing output and point to this section. Tasks 1 and 2 are still worth executing and committing first. Their tiers (unit + git, and the new brd tests) are green, and the evidence they produce is what the human needs to decide whether to reopen the parent spec.
- **`uv run pytest -m e2e_fake` with the fix: 62 passed, 1 failed.** `tests/e2e/test_milestone_resume.py::test_a_killed_milestone_resumes_where_it_stopped` fails with `Failed: DID NOT RAISE _Killed`, 2 of 2 runs, and passes on today's code (7.8 s). Same mechanism: its kill needs C's `plan` (C is merged, blocked by A and B) in flight at the same time as D's `implement` (an unrelated sibling lane). With real edges, C is starved until D's lane finishes, so the kill never fires. This is a fifth regression the spec's gate does not list, and it is part of the same stop condition.

---

## File Structure

- Modify: `src/agent_manager/orchestrate.py`
  - `build_dag_tree` body (`:1445-1460`): the edge loop.
  - `build_dag_tree` docstring (`:1423-1444`): the new contract.
  - `supervise` docstring, first paragraph (`:1480-1490`): the tree-shape sentence.
- Modify: `tests/test_orchestrate.py`
  - Rewrite `test_build_dag_tree_multi_blocker_is_root_not_edge` (`:227-243`) → U1.
  - Rewrite `test_build_dag_tree_roots_follow_items_order` (`:284-294`) → U2.
  - Edit `test_build_dag_tree_merged_item_still_forwards_to_its_dependent` (`:297-312`) → U3.
  - Add U5 and U6 after `test_build_dag_tree_empty_items` (`:315-316`).
  - Add I1 and I2 after `test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it` (`:3702`), before `test_a_given_runner_factory_reaches_the_base_builder`.

Line numbers are as of this plan's base (`41f7c95`). Locate by function name if they drift.

---

### Task 1: `build_dag_tree` wires an edge from every blocker

**Files:**
- Modify: `src/agent_manager/orchestrate.py:1415-1460` (`build_dag_tree`), `:1480-1490` (`supervise` docstring)
- Test: `tests/test_orchestrate.py:206-316`

**Interfaces:**
- Consumes: existing test harness in `tests/test_orchestrate.py:164-204`: `_DagItem(id: str, blockers: tuple[str, ...] = ())`, `_dag_factory(calls) -> node_factory`, `_dag_forward(item) -> f"from_{item.id}"`, `async _build(items, calls, forward=_dag_forward) -> (nodes, roots)`.
- Produces: `build_dag_tree(items, *, id_of, blockers_of, node_factory, forward=None) -> tuple[dict[str, grafo.Node], list[grafo.Node]]`. Same signature; new contract: one edge per blocker, `roots` = zero-blocker items in `items` order. Task 2 relies on this through `supervise`.

- [ ] **Step 1: Rewrite U1 (multi-blocker gets an edge from every parent)**

In `tests/test_orchestrate.py`, replace the whole `test_build_dag_tree_multi_blocker_is_root_not_edge` function with:

```python
async def test_build_dag_tree_multi_blocker_gets_an_edge_from_every_parent():
    """A and B both block C: C gets one edge from each, waits on both parents'
    events, is no root, and receives both blockers' forwarded outputs."""
    a, b = _DagItem("a"), _DagItem("b")
    c = _DagItem("c", ("a", "b"))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b, c], calls)

    assert nodes["a"].children == [nodes["c"]]
    assert nodes["b"].children == [nodes["c"]]
    assert roots == [nodes["a"], nodes["b"]]
    assert nodes["c"] not in roots
    assert len(nodes["c"]._parent_events) == 2
    assert nodes["c"]._parent_events[0] is nodes["a"]._event
    assert nodes["c"]._parent_events[1] is nodes["b"]._event

    await grafo.TreeExecutor(uuid="merged", roots=roots).run()

    assert calls["c"] == {"from_a": "out-a", "from_b": "out-b"}
```

- [ ] **Step 2: Rewrite U2 (roots are the zero-blocker items in items order)**

Replace the whole `test_build_dag_tree_roots_follow_items_order` function with:

```python
async def test_build_dag_tree_roots_follow_items_order():
    """Roots are exactly the zero-blocker items, in items order: `e` after `b`
    proves the order is the items', not alphabetical, and the merged `c`
    listed between them is no root."""
    a, b, e = _DagItem("a"), _DagItem("b"), _DagItem("e")
    c = _DagItem("c", ("a", "b"))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, c, b, e], calls)

    assert list(nodes) == ["a", "c", "b", "e"]
    assert roots == [nodes["a"], nodes["b"], nodes["e"]]
    assert nodes["c"] not in roots
```

- [ ] **Step 3: Edit U3 (merged item still forwards to its dependent)**

Replace the whole `test_build_dag_tree_merged_item_still_forwards_to_its_dependent` function with this. Only the docstring and the `roots` assertion change:

```python
async def test_build_dag_tree_merged_item_still_forwards_to_its_dependent():
    """A single-blocker item behind a merged item is reached by an edge
    forwarding the merged item's output."""
    a, b = _DagItem("a"), _DagItem("b")
    joined = _DagItem("joined", ("a", "b"))
    d = _DagItem("d", ("joined",))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b, joined, d], calls)

    assert nodes["joined"].children == [nodes["d"]]
    assert roots == [nodes["a"], nodes["b"]]

    await grafo.TreeExecutor(uuid="joined", roots=roots).run()

    assert calls["d"] == {"from_joined": "out-joined"}
```

Leave `test_build_dag_tree_single_blocker_wires_edge`, `test_build_dag_tree_no_forward_connects_without_kwarg`, `test_build_dag_tree_forward_returning_none_connects_without_kwarg` and `test_build_dag_tree_empty_items` exactly as they are (U4).

- [ ] **Step 4: Add U5 and U6 after `test_build_dag_tree_empty_items`**

Insert directly after `test_build_dag_tree_empty_items` (and before `test_story_tips_name_every_story_with_subtasks_in_census_order`):

```python
async def test_build_dag_tree_never_runs_an_item_whose_blocker_raised():
    """A raises, so grafo never enqueues C (blocked by A and B): C's coroutine
    is never called and C has no output. grafo's own gate, no waiting here."""
    a, b = _DagItem("a"), _DagItem("b")
    c = _DagItem("c", ("a", "b"))
    calls: dict[str, dict[str, Any]] = {}

    def factory(item: _DagItem) -> Callable[..., Awaitable[str]]:
        async def run(**forwarded: Any) -> str:
            calls[item.id] = forwarded
            # One scheduling tick before A raises, so every root has been
            # picked up by a worker first: were C a root, it would be called.
            await asyncio.sleep(0)
            if item.id == "a":
                raise RuntimeError("a failed")
            return f"out-{item.id}"

        return run

    nodes, roots = await orchestrate.build_dag_tree(
        [a, b, c],
        id_of=lambda item: item.id,
        blockers_of=lambda item: item.blockers,
        node_factory=factory,
        forward=_dag_forward,
    )
    executor = grafo.TreeExecutor(uuid="raised", roots=roots)

    await executor.run()

    assert "c" not in calls
    assert nodes["c"].output is None
    assert [type(error) for error in executor.errors] == [RuntimeError]


async def test_build_dag_tree_four_node_shape_completes():
    """Spec §1.1: A and B are roots, D is blocked by A, C by B and D. Every
    coroutine sets its own event on exit, and a 2+-blocker one first awaits
    its blockers' events, as `lane` does today. B returns, then A after a
    wall-clock gap, so D is ready only after grafo shrank its pool. With C an
    unconnected root, C sits in a worker waiting on D while D is queued
    behind exit sentinels, and the run never returns."""
    a, b = _DagItem("a"), _DagItem("b")
    d = _DagItem("d", ("a",))
    c = _DagItem("c", ("b", "d"))
    done = {item.id: asyncio.Event() for item in (a, b, c, d)}

    def factory(item: _DagItem) -> Callable[..., Awaitable[str]]:
        async def run(**forwarded: Any) -> str:
            try:
                # One scheduling tick first, so a worker takes C before B
                # returns and shrinks the pool (spec §1.1).
                await asyncio.sleep(0)
                if len(item.blockers) >= 2:
                    for blocker in item.blockers:
                        await done[blocker].wait()
                if item.id == "a":
                    # The pool-shrink window is wall-clock, not ordering
                    # (spec §1.1): A must return after B's worker shrank it.
                    await asyncio.sleep(0.05)
                return f"out-{item.id}"
            finally:
                done[item.id].set()

        return run

    nodes, roots = await orchestrate.build_dag_tree(
        [a, b, d, c],
        id_of=lambda item: item.id,
        blockers_of=lambda item: item.blockers,
        node_factory=factory,
    )

    try:
        await asyncio.wait_for(grafo.TreeExecutor(uuid="four", roots=roots).run(), 2.0)
    except TimeoutError:
        pytest.fail("the four-node shape hung: D is queued behind grafo's exit sentinels")

    assert {uuid: node.output for uuid, node in nodes.items()} == {
        "a": "out-a",
        "b": "out-b",
        "d": "out-d",
        "c": "out-c",
    }
```

(`asyncio`, `grafo`, `pytest`, `Any`, `Awaitable`, `Callable` and `orchestrate` are already imported at the top of `tests/test_orchestrate.py`.)

- [ ] **Step 5: Run the `build_dag_tree` tests to verify the new ones fail**

Run: `uv run pytest tests/test_orchestrate.py -k build_dag_tree -v`

Expected (observed while planning):
- FAIL `test_build_dag_tree_multi_blocker_gets_an_edge_from_every_parent`: `assert [] == [<Node c>]` on `nodes["a"].children`.
- FAIL `test_build_dag_tree_roots_follow_items_order`: `roots` also contains `nodes["c"]`.
- FAIL `test_build_dag_tree_merged_item_still_forwards_to_its_dependent`: `roots` also contains `nodes["joined"]`.
- FAIL `test_build_dag_tree_never_runs_an_item_whose_blocker_raised`: `assert 'c' not in {'a': {}, 'b': {}, 'c': {}}`.
- FAIL `test_build_dag_tree_four_node_shape_completes`: `Failed: the four-node shape hung ...` after about 2 s.
- PASS the four U4 tests.

If U5 or U6 passes here, stop: the red was not reproduced, and the test is not pinning anything.

- [ ] **Step 6: Implement the edge loop**

In `src/agent_manager/orchestrate.py`, in `build_dag_tree`, replace:

```python
    roots: list[grafo.Node] = []
    for item in items:
        blockers = blockers_of(item)
        if len(blockers) == 1:
            (blocker,) = blockers
            parent = nodes[blocker]
            name = None if forward is None else forward(items_by_id[blocker])
            await parent.connect(nodes[id_of(item)], forward=name)
        else:
            roots.append(nodes[id_of(item)])
    return nodes, roots
```

with:

```python
    roots: list[grafo.Node] = []
    for item in items:
        blockers = blockers_of(item)
        if not blockers:
            roots.append(nodes[id_of(item)])
            continue
        for blocker in blockers:
            parent = nodes[blocker]
            name = None if forward is None else forward(items_by_id[blocker])
            await parent.connect(nodes[id_of(item)], forward=name)
    return nodes, roots
```

- [ ] **Step 7: Rewrite `build_dag_tree`'s docstring**

Replace the docstring of `build_dag_tree` (from `"""Build the grafo nodes and edges` through the closing `"""`) with:

```python
    """Build the grafo nodes and edges for `items`, and pick the executor's roots.

    One `grafo.Node` per item, `uuid=id_of(item)`, `timeout=None` always
    (grafo's 60 s default would cancel a long-running node). `nodes_by_id` is
    keyed by `id_of(item)`, in `items` order.

    Every blocker of an item gets one edge to it, in `blockers_of` order,
    whatever the blocker count: grafo enqueues a node only once every parent
    returned, and never once a parent raised, so an item with two or more
    blockers is a real join. Each edge forwards its blocker's output as
    `forward(blocker_item)` -- `forward` is handed the blocker item, not its
    id, once per edge -- or nothing on that edge when `forward` is `None` or
    returns `None`. This helper creates no events and does no waiting.

    `roots` is, in `items` order, every item with no blocker; an item with
    one or more blockers is never a root. A blocker id not among `items` is
    the caller's to avoid; nothing is filtered, validated or de-duplicated.
    Empty `items` gives `({}, [])`.
    """
```

- [ ] **Step 8: Correct `supervise`'s tree-shape sentence**

In `supervise`'s docstring, replace this paragraph:

```
    The tree comes from `build_dag_tree` over `plan.stories`, each story's
    blockers being its `plan.roots` in-milestone blockers: one node per story,
    one edge per single-blocker story forwarding the blocker's tip as
    `tip_<short id>`, and every story rooted on a `merged` base (two or more
    in-milestone blockers) as an extra executor root, for the grafo
    join-starvation reason `build_dag_tree` documents (a grafo limitation,
    not a `dag`/`bases` defect). Such a story's lane waits on each blocker's
    own completion, signalled by `story_done`/`story_ok` below, and reads the
    blocker's tip off `plan.tips` (`blocker_tips`); every lane sets its own
    signal on exit, success or not, so this never hangs. A milestone with no
    story has no tree to run.
```

with:

```
    The tree comes from `build_dag_tree` over `plan.stories`, each story's
    blockers being its `plan.roots` in-milestone blockers: one node per story
    and one edge per blocker, each forwarding the blocker's tip as
    `tip_<short id>`, so a story rooted on a `merged` base (two or more
    in-milestone blockers) is a grafo join, not an executor root. Such a
    story's lane waits on each blocker's own completion, signalled by
    `story_done`/`story_ok` below, and reads the blocker's tip off
    `plan.tips` (`blocker_tips`); every lane sets its own signal on exit,
    success or not, so this never hangs. A milestone with no story has no
    tree to run.
```

The `story_done`/`story_ok`/`blocker_tips` sentences stay word for word (spec §2 item 3). Do not touch `blocker_tips`'s or `lane`'s docstrings.

- [ ] **Step 9: Run the `build_dag_tree` tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k build_dag_tree -v`
Expected: 9 passed. U6 well under 0.5 s.

- [ ] **Step 10: Run R5 and the default suite**

Run: `uv run pytest tests/test_orchestrate.py -k "two_blocker_milestone" -v`
Expected: PASS `test_run_board_runs_a_two_blocker_milestone_only_after_both_finish_done` and `test_run_board_blocks_a_two_blocker_milestone_on_only_its_unclean_blocker`, unchanged.

Run: `uv run pytest`
Expected: all pass (planning run: 0 failures outside the tests rewritten here).

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "fix: build_dag_tree connects every blocker; roots are zero-blocker items only (27db9eb4)"
```

---

### Task 2: supervisor pins: an escalated blocker parks the merged story, and the four-node shape completes

**Files:**
- Test: `tests/test_orchestrate.py`. Insert after `test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it`, before `test_a_given_runner_factory_reaches_the_base_builder`.

**Interfaces:**
- Consumes: Task 1's `build_dag_tree` contract (through `supervise`). Existing test helpers in `tests/test_orchestrate.py`: `_milestone(project, stories: dict[str, int], blocked_by: dict[str, list[str]] | None) -> {"milestone", "stories", "subtasks"}`, `GatedDriver(outcomes=..., gates=..., returned=...)` with `.calls: list[dict]` (`"card"` key), `_within(awaitable, what)`, `_run(project, milestone, driver, **overrides) -> dict`, `_run_or_fail_if_it_hangs(call) -> Any`, `_load(project, run_id) -> models.Run`, `_statuses(run) -> dict[str, str]`, the `project` and `fake_bases` fixtures (`fake_bases.calls: list[dict]` with a `"story_id"` key), `StopSignal`.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write I1 and I2**

Insert:

```python
@pytest.mark.brd
@pytest.mark.git
def test_a_merged_root_story_with_one_escalated_blocker_is_never_driven(project, fake_bases):
    """C is blocked by A and B. B finishes clean, then A escalates: C is never
    driven, no base is built, and C and c1 stay `pending`, as a
    single-blocker dependent of an escalated story does. A regression pin
    (spec §5 I1): today's early return in `lane` and grafo's own gate after
    the fix give the same outcome."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    b_returned = asyncio.Event()

    async def after_b_returned(stop: StopSignal | None) -> None:
        await _within(b_returned.wait(), "b1 to return")

    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: after_b_returned},
        returned={b1: b_returned},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert c1 not in [call["card"] for call in driver.calls]
    assert fake_bases.calls == []
    assert (result["escalated"], result["story"], result["subtask"]) == (True, story_a, a1)
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("pending", "pending")
    assert (statuses[story_b], statuses[b1]) == ("done", "done")


@pytest.mark.brd
@pytest.mark.git
def test_a_merged_root_story_behind_an_edge_child_completes_instead_of_hanging(
    project, fake_bases
):
    """Spec §1.1 through `supervise`: D is blocked by A, C by B and D. B
    returns, then A after a wall-clock gap, so D is ready only after grafo
    shrank its pool. The run completes and C is built and driven."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "D": 1, "C": 1},
        blocked_by={"D": ["A"], "C": ["B", "D"]},
    )
    story_c, story_d = shape["stories"]["C"], shape["stories"]["D"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    (d1,) = shape["subtasks"]["D"]
    b_returned = asyncio.Event()

    async def after_b_returned_and_a_gap(stop: StopSignal | None) -> None:
        await _within(b_returned.wait(), "b1 to return")
        # The pool-shrink window is wall-clock, not ordering (spec §1.1): A
        # must return after B's worker shrank grafo's pool.
        await asyncio.sleep(0.05)

    driver = GatedDriver(gates={a1: after_b_returned_and_a_gap}, returned={b1: b_returned})

    result = _run_or_fail_if_it_hangs(
        lambda: _run(project, shape["milestone"], driver, max_concurrent=2)
    )

    assert result["done"] is True, result
    assert [call["story_id"] for call in fake_bases.calls] == [story_c]
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("done", "done")
    assert (statuses[story_d], statuses[d1]) == ("done", "done")
```

- [ ] **Step 2: Observe I2 red against today's `build_dag_tree`**

Task 1 already landed the fix, so put back the pre-fix `orchestrate.py` for this one run. Use `git checkout` from the previous commit, not `git stash`:

```bash
git checkout HEAD~1 -- src/agent_manager/orchestrate.py
uv run pytest -m brd tests/test_orchestrate.py -k "one_escalated_blocker_is_never_driven or behind_an_edge_child_completes" -v
git checkout HEAD -- src/agent_manager/orchestrate.py
git status --short src/agent_manager/orchestrate.py
```

Expected (observed while planning, 1 of 1 runs):
- FAIL `test_a_merged_root_story_behind_an_edge_child_completes_instead_of_hanging` with `Failed: the run hung instead of leaving on the lane's BaseException` after about 30 s (`WAIT * 3`). The message text is the existing guard's; here it means the run hung. A daemon thread is left behind, which is the guard's established cost.
- PASS `test_a_merged_root_story_with_one_escalated_blocker_is_never_driven`. **It is a regression pin, not a red test: it passes before and after the fix by design (spec §5 I1). No red step is claimed for it.**
- The final `git status --short` prints nothing (the fix is restored).

If I2 does **not** hang here, do not ship it as a red test. First try the spec's escape hatch: add a fifth story `E` with one subtask and no blocker, which puts one more shrink sentinel in flight. Rerun this step, and say in the commit message which variant reproduced. If neither reproduces, keep I2 as a green pin, say so in the commit message, and rely on U6 as the red pin.

- [ ] **Step 3: Run I1 and I2 green on the fix**

Run: `uv run pytest -m brd tests/test_orchestrate.py -k "one_escalated_blocker_is_never_driven or behind_an_edge_child_completes" -v`
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_orchestrate.py
git commit -m "test: pin merged-root dispatch through supervise under real edges (27db9eb4)"
```

---

### Task 3: Full verification and the regression-gate stop condition

**Files:** none modified, unless a step below says to stop.

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: the verification evidence for the card.

- [ ] **Step 1: Default suite**

Run: `uv run pytest`
Expected: all pass.

- [ ] **Step 2: `brd` tier for the orchestrator, R1-R4 and the four at-risk tests**

Run: `uv run pytest -m brd tests/test_orchestrate.py -v`

Expected for this card's own gate: PASS I1, I2, and R1-R4:
- `test_only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone`
- `test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it`
- `test_a_run_with_no_merged_root_builds_no_base_and_reports_no_bases`
- `test_the_open_cards_are_the_remaining_subtasks_and_every_open_merged_roots_resolver`

**Stop condition.** In the planning run, these four failed with the fix and pass without it:
- `test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling`
- `test_a_base_whose_resolver_was_stopped_ends_stopped_not_escalated`
- `test_a_base_whose_resolver_was_stopped_gets_no_base_failed_comment`
- `test_a_merged_lane_that_finds_the_stop_fired_never_builds_its_base`

If any of them fails, **stop and escalate**. Report the failing output and point to "Planning finding" in this plan. Per spec §7, a merged story starved by grafo's pool-shrink delay while an unrelated sibling is in flight is evidence against the parent spec's premise. It reopens the parent spec; it is not patched here. Do not edit these tests, do not change their gates or `max_concurrent`, and do not add a grafo workaround to `build_dag_tree`. Tasks 1 and 2 stay committed on this branch as the evidence.

If all of them pass, continue.

- [ ] **Step 3: `e2e_fake` tier**

Run: `uv run pytest -m e2e_fake`
Expected: all pass. **Stop condition, same as Step 2:** in the planning run, `tests/e2e/test_milestone_resume.py::test_a_killed_milestone_resumes_where_it_stopped` failed with the fix (`DID NOT RAISE _Killed`) and passed without it. If it fails, stop and escalate with its output. Do not edit the test.

- [ ] **Step 4: Confirm nothing out of scope moved**

Run: `git diff 41f7c95 --stat`
Expected: only `src/agent_manager/orchestrate.py` and `tests/test_orchestrate.py` (plus the spec and plan docs the workflow commits).

Run: `git diff 41f7c95 -- src/agent_manager/orchestrate.py`
Expected: changes only inside `build_dag_tree` (body and docstring) and the first paragraph of `supervise`'s docstring. `blocker_tips`, `lane`, `story_done`/`story_ok` are untouched.
<!-- task-pipeline: validated -->
