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
