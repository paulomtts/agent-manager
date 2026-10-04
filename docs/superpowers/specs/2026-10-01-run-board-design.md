# `am run --board` — design

Date: 2026-10-01
Status: approved design, pre-implementation

## 1. Purpose

Today `am run` drives either one subtask (`--card`) or every remaining
subtask of one milestone (`--milestone`), end to end, with the milestone's
stories dispatched as a grafo DAG by level
(`orchestrate.run_milestone`/`supervise`, `src/agent_manager/orchestrate.py`).

There is no way to drive a whole board — every open milestone — in one
invocation. Today that means scripting a loop of `am run --milestone` calls
by hand, with no shared concurrency cap, no cross-milestone dependency
awareness, and no single payload to read when it's done.

`--board` adds that: a new `am run --board` mode that discovers every open
milestone on the board, orders them by their own `blocked_by` edges (root
cards can block each other exactly as stories do), and drives them
concurrently, levels of independent milestones running at once, up to a
shared story-concurrency cap.

## 2. Scope

**In scope.** A `--board` mode for `am run`; a generic DAG-to-grafo tree
builder extracted from `orchestrate.supervise` and reused at both the story
level (unchanged behavior) and the new milestone level; per-milestone branch
prefix derivation; board-level dry-run output; failure isolation across
milestones.

**Out of scope.** A new "board run" record or `am resume --board` — each
milestone keeps its own independent `models.Run` row, exactly as a solo
`am run --milestone` produces today, and is resumed the same way
(`am resume <run-id>`). Cross-repo boards. Changing `--card` or
single-milestone `--milestone` behavior or payload shape.

**Never** (unchanged from the rest of `am`). Pushing to a remote, opening
PRs, touching `main`/`master`.

## 3. Decisions

### 3.1 CLI surface

`am run --board [--branch-prefix TEMPLATE] --base-branch BASE [--max-concurrent N] [--verify ...] [--dry-run]`

- `--board` is a boolean flag, mutually exclusive with `--card` and
  `--milestone` (extends `_check_run_targets`'s existing combo refusal,
  `src/agent_manager/cli.py:962`, with the same `typer.BadParameter` /
  exit-2 treatment as the `--card`/`--milestone` conflict).
- `--branch-prefix` becomes optional in board mode (still required for
  `--milestone`). See 3.2.
- `--max-concurrent` keeps its name, flag, and default
  (`DEFAULT_MAX_CONCURRENT`, `cli.py:846`). Its meaning in board mode is
  "stories running at once, board-wide," not "per milestone" — see 3.4.
- `--dry-run --board` previews the board's milestone-level plan the same
  way `--dry-run --milestone` previews a story-level plan today
  (`dry_run_milestone`/`dry_run_payload`, `cli.py:879-937`): nested, nothing
  written, no `Store` opened.

### 3.2 Branch prefix derivation

A literal `--branch-prefix` cannot be reused as-is across milestones without
risking integration-branch collisions. In board mode:

- Omitted: each milestone's prefix is `dag.task_stem(milestone_card)` — the
  same slug-plus-short-id format (`dag.py:74-78`) every other derived name in
  this program already uses.
- Given: it's joined as a prefix, `f"{branch_prefix}-{dag.task_stem(card)}"`,
  never reused verbatim — so a collision stays impossible either way, and a
  caller who wants a recognizable namespace (`sprint9-...`) still gets one.

### 3.3 Board-level leveling (display only)

A new pure function, structurally identical to `dag.topological_levels`
(`dag.py:143-166`) but operating on open root `CardNode`s instead of
`census.StoryPlan`s:

```python
def board_levels(roots: list[CardNode]) -> list[list[CardNode]]
```

Groups every root card that still has work (not `done`, or with a remaining
milestone to run — mirroring `dag.compute_levels`'s pending filter) into
dependency levels by the cards' own `blocked_by` edges, ignoring edges to
cards outside the root set. A cycle raises the same shape of error
`dag.DependencyCycleError` does today, reworded for milestones.

This function is used for exactly two things: `--dry-run --board`'s display,
and computing the full claim set up front (3.6). It is **not** used to drive
execution — see 3.4.

### 3.4 Dispatch architecture

The naive approach — step through `board_levels` and `asyncio.gather` each
level before starting the next — would need its own scheduling loop
alongside the grafo tree `supervise` already runs per milestone, and a
separately-threaded semaphore to cap stories globally across concurrently
running milestones. Instead, this reuses grafo itself, one level up.

**What's generic vs. story-specific.** Of `supervise`'s body
(`orchestrate.py:1306-1402`), the tree/edge construction is generic: one
`grafo.Node` per item, `connect()` from a blocker to its single-parent
dependent, and — because of grafo's confirmed dynamic-worker-pool starvation
on a real 2+-parent join — a separate path for anything with 2+ blockers,
where the node becomes one of the executor's own roots and waits on its
blockers' own completion signals instead of a grafo edge. None of that cares
what a node's coroutine does. `lane()` (`orchestrate.py:1077`) is the
opposite: git base-stacking, branch-tip forwarding, brd row recording — all
story-specific, with no milestone equivalent (there is no "merged base" at
the milestone level), and it is not touched or generalized.

**Superseded (2026-10-03).** The 2+-blocker "separate path" described here,
in "The extraction" below and in §4's first risk (an executor root waiting on
`story_done`/`story_ok` completion events, justified by grafo
"join-starvation") no longer exists and its rationale does not hold: as
`2026-10-03-merged-root-real-edges-design.md` §3.4 records, `build_dag_tree`
now gives a 2+-blocker item one real grafo edge per blocker, the same as a
single-blocker item gets one, and grafo's own all-parents-done gate is what
makes it wait.

**The extraction.** Pull the generic half out of `supervise` into a new
helper, e.g. `orchestrate.build_dag_tree`:

```python
def build_dag_tree(
    items: Sequence[T],
    *,
    id_of: Callable[[T], str],
    blockers_of: Callable[[T], Sequence[str]],
    node_factory: Callable[[T], Callable[..., Awaitable[Any]]],
    forward: Callable[[T], str | None] | None = None,
) -> tuple[dict[str, grafo.Node], list[grafo.Node]]
```

returning every node keyed by id and the executor's roots (single-blocker
items reached by edge, multi-blocker items as additional roots waiting on
their blockers' completion events — the same `story_done`/`story_ok`
mechanism `lane`/`blocker_tips` use today, generalized to "wait for every
parent's signal," independent of what a parent's coroutine actually did).
`supervise` is refactored to call this with `items=plan.stories`,
`node_factory` producing the existing `lane(...)` closures, and
`forward` producing the `tip_<short id>` kwarg name — a behavior-preserving
refactor, verified by the existing story-level test suite before anything
board-level is built on it (see 3.8).

**`run_board`.** A new `orchestrate.run_board(...)`:

1. Validates args (board mode needs a base branch; `--card`/`--milestone`
   absent), reads `board.roots()`, computes `board_levels` for the claims
   check and the dry-run path.
2. Pre-flight: computes `milestone_claims` (`orchestrate.py:1417`) for every
   open milestone across the whole board and calls `cli.refuse_claimed` once,
   up front, for the full set — fail fast, before anything starts, same
   spirit as a single milestone run's own pre-flight refusal.
3. One shared `asyncio.Semaphore(max_concurrent)`, created once.
4. One `asyncio.run(...)` for the entire board: `build_dag_tree` over the
   open root cards, with `node_factory` producing a coroutine per milestone
   that runs the extracted async core of today's `run_milestone` (3.5),
   passed the shared semaphore so it threads down into that milestone's own
   nested `supervise()` call. One `grafo.TreeExecutor` runs every milestone;
   grafo's edges — not an explicit level-stepping loop — enforce that a
   milestone only starts once everything that blocks it is done.
5. Collects one outcome per milestone and builds the board payload (3.7).

### 3.5 `run_milestone`'s async core

`orchestrate.run_milestone` (`orchestrate.py:1437`) is today a synchronous
function that calls `asyncio.run(control.controlled(supervise(...)))` once
and blocks. It's split into:

- `async def _run_milestone_async(...)` — everything `run_milestone`
  currently does after argument validation: claims, lease, `Store.open`,
  `record_run`, `supervise`, Integrate, payload construction. Gains one new
  parameter, `slots: asyncio.Semaphore | None = None`, forwarded into
  `supervise` (which already creates its own semaphore when none is passed,
  preserving solo-run behavior exactly).
- `run_milestone` becomes a thin sync wrapper: validates arguments then
  `asyncio.run(_run_milestone_async(...))`. Its signature, docstring
  contract, and payload shape are unchanged — every existing caller and test
  is unaffected.
- `run_board`'s `node_factory` calls `_run_milestone_async` directly inside
  its own single `asyncio.run(...)`, passing the board's shared semaphore.

### 3.6 Claims and pre-flight

Unchanged per-milestone: each milestone still takes its own lease with its
own `milestone_claims` keys (`card:<milestone>`, `card:<id>` for every
remaining subtask, `branch:<prefix>-integrate`) at the point `run_milestone`
already does it. Added for board mode: a single upfront
`cli.refuse_claimed` over the union of every open milestone's claims, before
the board's `asyncio.run` starts, so a conflict anywhere on the board is a
clean refusal with no run directories created for the milestones that would
have started first. A claim taken by another process *during* a long board
run (after this pre-flight) is still caught by that milestone's own
`refuse_claimed` at its own dispatch point, same as today.

### 3.7 Failure handling and payload

A milestone's own outcome keeps today's shapes
(`controlled_payload`/`escalated_payload`/the `done` success payload,
`orchestrate.py:127-283`). `run_board` never lets one milestone's exception
propagate into its siblings' tree execution (grafo's own worker already
isolates a node's `Exception` into `executor.errors`, per
`executor.py:152-158`; `build_dag_tree`'s milestone node coroutine catches
and records rather than re-raising, matching the existing `collect_outcomes`
contract at the story level). A milestone blocked by one that escalated or
was cancelled is never dispatched — grafo never fires its edge, or (2+
blockers) it reads a non-clean blocker signal, as the equivalent story case
already does — and is reported `"blocked"` in the board payload rather than
attempted.

Board payload shape:

```json
{
  "ok": false,
  "board": true,
  "levels": [{"level": 0, "milestones": ["<id>", "..."]}],
  "milestones": [
    {"milestone_id": "<id>", "status": "done", "run_id": "...", "...": "..."}
  ]
}
```

`ok` is `false` if any milestone's status is not `done`. Each entry under
`milestones` carries that milestone's own full payload (as a solo
`am run --milestone` would have returned) plus its `milestone_id` and
`status`, or `{"milestone_id": "...", "status": "blocked", "blocked_by": [...]}`
for one never dispatched.

**Named limitation.** There is no board-level `Run` record (3, Scope), so a
crash mid-board-run loses the bookkeeping of "which milestones were already
dispatched" beyond what each individual milestone's own `Run` row shows.
Recovery is re-running `am run --board`: milestones already `done` are
skipped (their claims show no remaining subtasks), and any individually
`stopped`/`escalated` milestone is resumed by hand with
`am resume <run-id>`, exactly as it would be outside board mode.

### 3.8 Testing

- `board_levels`: unit tests mirroring `dag.topological_levels`'s existing
  cases — independent cards, a chain, a cycle, edges pointing outside the
  root set ignored.
- `build_dag_tree`: extracted with its own unit tests (single-blocker edge
  wiring, 2+-blocker root-and-wait wiring), then `supervise`'s *existing*
  full test suite re-run unchanged against the refactored code as a
  behavior-preservation gate, before any board-level code is written against
  it.
- `run_board`: e2e tests mirroring `tests/e2e/test_parallel_milestone.py`'s
  shape — two independent milestones both finish; a `blocked_by` pair runs
  in order; one milestone escalating doesn't block an independent sibling
  but does block (and reports `"blocked"` for) its dependent; the shared
  semaphore measurably caps total concurrent stories across two milestones
  at once (reusing that test file's concurrency-tracking fake driver
  pattern).

## 4. Risks

- The `supervise` extraction touches delicate, heavily-commented existing
  code with subtle correctness properties (grafo's join-starvation
  workaround in particular). Mitigated by doing the extraction as its own
  change, gated on the existing story-level test suite passing unchanged,
  before board-level work starts.
- No board-level run record means no single "resume the whole board" command
  and no durable record of in-flight board state beyond per-milestone runs.
  Accepted for now (3.7); revisit if a later need for board-level
  pause/resume/cancel-everything emerges.
