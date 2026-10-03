# Merged-root stories get real grafo edges — design

Date: 2026-10-03
Status: approved design, pre-implementation

## 1. Purpose

A 2+-blocker ("merged-root") story is wired as an unconnected `grafo.TreeExecutor`
root today, and waits for its blockers with hand-rolled `asyncio.Event`s
(`story_done`/`story_ok`) read through `blocker_tips`
(`orchestrate.py:871-894`), rather than with real `grafo.Node.connect()` edges.
The reason given, in both the code and `2026-10-01-run-board-design.md`, is
that "grafo's dynamic worker pool can starve a 2+-parent join forever when an
unrelated sibling lane is still in flight" — a confirmed grafo defect, taken as
reason enough to avoid grafo's own edges for this one case.

That reasoning was checked today by reading grafo (v0.3.5, pinned, and
identical at the sibling source checkout `~/Code/grafo`, confirmed by diff)
end to end rather than from the comment that describes it, and by reproducing
both the claim and the alternative against the real library. Three things
came out of it, in order of how much they change the picture:

1. **`grafo.Node.connect()` already supports a multi-parent join.** It is a
   documented, tested, intended pattern — the grafo README states "a node can
   only start executing once all its parents have finished running";
   `docs/user-guide/building-trees.md` shows fan-in and diamond shapes built
   with it; `tests/test_executor.py::test_repr_two_roots_conjoined` and
   `tests/test_yielding.py::test_yielding_results_with_multiple_parents`
   exercise it. `Node._add_event` (`components.py:187-191`) appends to a
   *list*, `_parent_events`, and the worker loop enqueues a child only once
   `all(e.is_set() for e in child._parent_events)`
   (`executor.py:143-148`). Calling `parent.connect(child)` from two different
   parents accumulates two events; a repro (`r1_fanin.py`, see the
   investigation's transcript) confirms the child starts only once the later
   of the two finishes, with both forwarded values present.
2. **The real grafo defect is a general pool-resize delay, not a join-specific
   starvation.** `__adjust_dynamic_workers`'s shrink branch
   (`executor.py:99-118`) counts *all* workers, busy ones included, when
   deciding how many `None` "exit" sentinels to queue, and those sentinels can
   land ahead of a node that becomes ready moments later. This delays *any*
   newly-ready node — single-parent or multi-parent alike — behind an
   unrelated sibling's completion; it is not specific to a join and, fuzzed
   across 300 random DAGs built with plain `connect()` (204 of them containing
   a join), it produced zero hangs, only bounded delays. `am` already accepts
   this same exposure for every single-blocker story today and has never
   treated it as a defect worth working around.
3. **`am`'s own workaround is the thing that actually deadlocks.** A
   merged-root lane sits inside a grafo worker while it awaits `story_done` —
   that worker counts as *busy* to the pool's resize logic but makes no
   progress. If one of the merged story's own blockers is a single-blocker
   edge-child that gets delayed behind the resize bug above, the only live
   worker is the one stuck waiting, and `executor.run()` never returns. A
   repro built directly on `am`'s `build_dag_tree`/`lane` wait discipline
   (`r3_am_pattern.py`) hangs on exactly this four-node shape — two
   independent stories, one blocked by both, the first finishing after the
   second — and the same shape, rebuilt with plain `connect()` calls instead
   (`r4_plain_connect.py`), completes cleanly. Fuzzed: the workaround hung on
   36 of 300 random DAGs; plain `connect()` hung on 0. This directly
   contradicts `supervise`'s own docstring, "so this never hangs"
   (`orchestrate.py:1489`).

Git history confirms this was not always the design: before commit `82c4e3b`,
`supervise` called `nodes[blocker].connect(...)` for every blocker, with no
special case. That commit introduced the root-list workaround, citing "a
grafo-only reproduction outside this module" — a repro that was never
committed and cannot be checked today. This spec reverts to the pre-`82c4e3b`
shape, now that the premise behind the change has been checked and found
wrong, and fixes the real bug it introduced.

## 2. Scope

**In scope.** `build_dag_tree` connects every blocker with a real
`grafo.Node.connect()` edge, regardless of count; only a zero-blocker item is
a root. `lane`'s merged-root branch stops waiting on anything — grafo's own
edges already guarantee the node is never enqueued unless every blocker
succeeded, the exact contract a single-blocker story already relies on today
— and reads its blockers' tips from `plan.tips` directly, since that value is
known at plan time and needs no runtime forwarding. `blocker_tips`,
`story_done` and `story_ok` are deleted. The stale "grafo join-starvation"
rationale is removed from every docstring and comment that states it
(`orchestrate.py:877-889`, `:1162-1167`, `:1434-1440`, `:1484-1490`) and from
`2026-10-01-run-board-design.md`'s mention of the same reasoning (a pointer to
this spec, not a rewrite of an already-approved, dated design doc). The
existing `build_dag_tree` tests that assert the root-list structure for a
merged item are rewritten to assert the new one.

**Out of scope.** The real grafo pool-resize delay (point 2 above) is a
defect in grafo itself, not in `am`, and is filed as an issue against the
sibling `grafo` project, not fixed here. `am` already tolerates its bounded
delay for every single-blocker story; nothing in this spec changes that
exposure, only removes the second, unbounded, `am`-side failure mode stacked
on top of it. `bases.build_merged_base` and the git mechanics of building a
merged base branch are untouched — this spec only changes *how the lane is
scheduled and told its blockers are done*, never how the base is built once
it runs.

**Never** (unchanged). Pushing, opening PRs, touching `main`/`master`.

## 3. Decisions

### 3.1 `build_dag_tree` connects every blocker; roots are zero-blocker items only

```python
nodes = {
    id_of(item): grafo.Node(coroutine=node_factory(item), uuid=id_of(item), timeout=None)
    for item in items
}
items_by_id = {id_of(item): item for item in items}
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

The branch on `len(blockers) == 1` disappears entirely; a 2+-blocker item now
takes the same loop body as a 1-blocker item, once per blocker. `forward` is
called once per edge exactly as it already is for the single-blocker case
(`f"tip_{dag.short_id(blocker.id)}"` at the `supervise` call site, unchanged)
— each edge forwards under its own distinct kwarg name, so two parents
forwarding to the same child never collide. The forwarded values are received
by `node_coroutine`'s existing `async def run(**tips: str)`
(`orchestrate.py:1520`) exactly as a single-blocker story's forwarded tip
already is today — and, exactly as today, `lane` does not read them (3.2):
the forwarding exists because `build_dag_tree` is generic infrastructure
shared with the board-level milestone tree (`run_board`'s use of the same
function, unaffected by this change, since every milestone-level edge is
already single-blocker in practice today), not because this call site needs
the data.

### 3.2 `lane`'s merged-root branch stops waiting; it reads `plan.tips` directly

Today (`orchestrate.py:1198-1203`):

```python
if root_plan.kind == "merged":
    tips = await blocker_tips(root_plan, plan, story_done, story_ok)
    if tips is None:
        return plan.tips[story.id]
```

Becomes:

```python
if root_plan.kind == "merged":
    tips = [plan.tips[blocker] for blocker in root_plan.blockers]
```

No wait, and no `None` case. By the time grafo invokes this node's
coroutine, every one of its blockers has already run and set its own node's
completion event (`Node._run`, `components.py`) — exactly the same guarantee
a single-blocker story's dependent already relies on to know its one blocker
is done. A blocker that escalated or stopped raises
(`LaneEscalated`/`LaneStopped`), which skips the `self._event.set()` grafo
needs to ever enqueue a child of that node — so a merged story with one
failed blocker is simply **never enqueued**, the identical "stays pending"
outcome a single-blocker story's dependent already has today (never recorded
in `finished`, reported as pending by `collect_outcomes`). The early-return
branch existed only to produce that same outcome by hand when the story was
an unconnected root; with a real edge, grafo produces it for free.

`lane`'s own signature drops `story_done: Mapping[str, asyncio.Event]` and
`story_ok: Mapping[str, bool]` — nothing else in its body reads them once the
branch above is simplified. `base_only_lane` (`orchestrate.py:1056`), which
also special-cases a subtask-less merged story, is checked against the same
change: it is called only after the tips above are already resolved, so it
needs no change of its own.

### 3.3 `blocker_tips`, `story_done` and `story_ok` are deleted

`blocker_tips` (`orchestrate.py:871-894`) has no remaining caller once 3.2
lands and is deleted outright, not deprecated. In `supervise`
(`orchestrate.py:1511-1513, 1536-1537, 1549`): the `story_done`/`story_ok`
dict construction, their keyword arguments into `lane` inside
`node_coroutine`, and the `story_done[story.id].set()` in the `finally` are
all deleted — nothing downstream of `lane`'s return needs them once `lane`
itself no longer accepts them. `story_ok[story.id] = False`/`True` on the
exception/success paths of `node_coroutine.run` are deleted for the same
reason; `finished[story.id] = done` (set only on the success path, inside
`lane`, unaffected) remains the sole bookkeeping `collect_outcomes` reads.

### 3.4 Docstrings: remove the stale rationale, state the real one

Every comment or docstring citing "grafo's dynamic worker pool can starve a
2+-parent join" as the reason for the workaround is corrected to state what
is actually true: a 2+-blocker story gets a real edge from each of its
blockers, the same as a single-blocker story gets one; grafo's own
all-parents-done gate (`executor.py:143-148`) is what makes a merged story
wait, with no code in this module involved. The specific locations: the
`build_dag_tree` docstring (`:1424-1444`), `supervise`'s docstring
(`:1480-1490`), `lane`'s docstring (`:1162-1167`), and
`2026-10-01-run-board-design.md`'s own mention of the same reasoning (one
sentence added there, pointing at this spec, since that file is a dated,
already-approved design and is not rewritten wholesale).

### 3.5 What does not change

`build_merged_base` (`bases.py`), the git mechanics of cutting a merged base
branch from its blockers' tips, `builds_a_base_alone`, `StoryRecorder`, and
every outcome/warning/escalation shape `collect_outcomes` produces are
untouched. This spec changes *how a merged-root node is reached and told its
blockers are done*; it does not change what happens once it runs. The
milestone-level DAG `run_board` builds with the same `build_dag_tree`
(`2026-10-01-run-board-design.md`) is affected only in that a milestone
blocked by 2+ others — not exercised by any milestone on this board today —
would now also get real edges instead of the root-and-wait path; nothing
about `run_board`'s own code changes, since it already calls the same shared
function.

## 4. Testing

`tests/test_orchestrate.py`'s existing `build_dag_tree` tests that assert the
root-list structure for a merged item are rewritten to assert the new
contract, not left to fail by design:

1. **`test_build_dag_tree_single_blocker_wires_edge`** — unaffected; still the
   one-edge case.
2. **`test_build_dag_tree_multi_blocker_is_root_not_edge`** — rewritten to
   `test_build_dag_tree_multi_blocker_gets_an_edge_from_every_parent`: a
   2+-blocker item receives one `connect()`-wired edge from each of its
   blockers (not zero), is not in `roots`, and its node's `_parent_events`
   has one entry per blocker.
3. **`test_build_dag_tree_merged_item_still_forwards_to_its_dependent`** —
   kept; still true, now via real edges instead of explained away as
   incidental.
4. **`test_build_dag_tree_roots_follow_items_order`** — rewritten: `roots` is
   now exactly the zero-blocker items, in item order; a 2+-blocker item is
   asserted absent from `roots`.
5. New: **a merged story with one failed blocker is never enqueued.** Through
   `supervise` with a `FakeDriver` lane that fails one of two blockers: the
   merged story's node never runs (absent from `finished`), reported pending
   by `collect_outcomes`, exactly as a single-blocker dependent of a failed
   blocker already is today — proving 3.2's claim that grafo's own gate
   reproduces the old early-return's outcome.
6. New: **the four-node deadlock shape from the investigation now completes.**
   Two independent stories A and B with no blockers, C blocked by both A and
   B, B finishing before A: through `supervise`, the run completes (not just
   "does not hang" — assert `executor.run()` returns within a short timeout
   and C's outcome is recorded), where it would hang today under the
   root-list workaround.
7. `test_only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone`,
   `test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it`,
   `test_a_run_with_no_merged_root_builds_no_base_and_reports_no_bases`,
   `test_the_open_cards_are_the_remaining_subtasks_and_every_open_merged_roots_resolver`
   — re-run unchanged as the suite's own regression gate: nothing about a
   successfully-built merged base's observable behaviour should change,
   since 3.5 states the base-building path is untouched; a failure here means
   the rewrite broke something 3.5 claims it does not.

## 5. Risks

- **A multi-parent edge's `forward` kwarg collision was checked, not
  assumed.** Each blocker forwards under its own `tip_<short id>` name, so
  two blockers of one merged story can never write the same kwarg; this is
  the same mechanism single-blocker stories already use today, just invoked
  more than once per child.
- **The real grafo defect (pool-resize delay) is not fixed by this spec** and
  still bounds how quickly any newly-ready node — merged or not — is
  dequeued when an unrelated sibling is in flight. `am` already lives with
  this for every single-blocker story; this spec removes the one place it
  compounded into an unbounded hang, not the underlying grafo bug. Filed
  separately, against `grafo`, not against this repository.
- **The uncommitted repro behind `82c4e3b` cannot be checked.** It is possible
  that repro demonstrated a real failure under conditions this spec's testing
  does not reproduce. The fuzz evidence (0 hangs in 300 random DAGs with
  plain `connect()`, versus 36 hangs in 300 with the old workaround) is
  strong but not exhaustive; if a new failure surfaces, it is evidence
  against this spec's premise and should reopen it, not be patched around
  silently.
