<!-- task-pipeline: validated -->
# Add `orchestrate.run_board` — subtask design (card baef4f94)

Parent story: 488d9bf4 "am run --board drives every open milestone as one DAG" (milestone 26d1ce3b). Source of truth: `docs/superpowers/specs/2026-10-01-run-board-design.md` §§3.4 (`run_board`), 3.6, 3.7, 3.8. This narrows that agreed design to one function plus its tests.

Note on inputs: the exploration summary handed to this stage was truncated at 8000 characters mid-sentence in its test list. The test list below was completed from spec §3.8 directly, not guessed; anything else the cut-off text may have said is not reflected here.

## Prerequisites (not in scope, must be present before implementing)

`orchestrate.run_board` calls primitives that exist only on unmerged sibling branches, not on `master` (e193dd3):

- `dag.board_levels(roots)` — branch `m14/task-add-board-levels-for-34bc3c16` (card 34bc3c16, this card's `blocked_by`).
- `orchestrate.build_dag_tree(items, *, id_of, blockers_of, node_factory, forward=None)` — branch `m14/task-extract-build-dag-tree-c0a1345c`.
- `supervise(..., slots=None)` — branch `m14/task-let-supervise-accept-an-6bb4f541`.
- `orchestrate._run_milestone_async(..., slots=None)` — branch `m14/task-split-run-milestone-a3eaf615`.

The implementer brings these in by merging/rebasing those branches into this worktree. They are not re-derived, reimplemented or modified here.

## Scope

One new public function, `orchestrate.run_board(...)`, which is additive only. Its keyword arguments mirror `run_milestone`'s run-shaping ones: `repo_dir`, `base_branch`, `commands`, `allow_no_verification`, `runner_factory`, `driver`, `clock`, `max_concurrent`, `control_interval`. It also takes the per-milestone branch prefix from its caller, as a mapping or callable keyed by milestone. Deriving that prefix (`dag.task_stem`) belongs to d78b3118. `run_board` has no `milestone` and no `resume_run_id` parameter.

Out of scope: any change to `cli.py`'s `am run` command, the `--board` flag, the mutual-exclusion refusals, prefix derivation and `--dry-run --board` wiring (all d78b3118). Also out of scope: any change to `run_milestone`'s signature, docstring contract or payload, to `supervise`, or to `build_dag_tree`. No board-level `Run` record and no `am resume --board`.

## Observable behavior

1. **Validate.** The function validates once, up front, for the whole board, and does it before reading anything that writes. It replicates the pre-asyncio checks `run_milestone` makes: `max_concurrent >= 1`, a base branch is present, and each milestone's branch prefix is valid. A bad argument raises the same error type `run_milestone` raises for it.
2. **Read and level.** It reads `board.roots()`. Open milestone roots are leveled with `dag.board_levels`, which already drops done roots that have no open descendant. A `DependencyCycleError` from `board_levels` propagates before any claim check or run directory exists.
3. **Pre-flight claims.** For every open milestone it computes `milestone_claims(milestone_id, stories, branch_prefix)`, taking the stories from the same census `run_milestone` uses. It unions the keys in level order and then in-level order, deduplicating with `list(dict.fromkeys(...))` so a key's first occurrence keeps its place. It then calls `cli.refuse_claimed(repo_root, keys)` once. A `ClaimedError` propagates, and when it does no milestone has started, no run directory exists and no lease has been taken. This check adds to each milestone's own pre-flight inside `_run_milestone_async` and does not replace it.
4. **One semaphore, one loop.** One `asyncio.Semaphore(max_concurrent)` is created and one `asyncio.run(...)` covers the whole board. Inside that loop, `build_dag_tree` runs over the open milestone cards with `id_of` = card id and `blockers_of` = that card's `blocked_by` restricted to open milestones on the board. That restriction is the caller's job per `build_dag_tree`'s contract; a done blocker counts as satisfied. A single `grafo.TreeExecutor` runs the tree, so grafo's edges set the order and there is no level-stepping loop.
5. **Node coroutine.** Every milestone's coroutine runs through one `milestone_done`/`milestone_ok` pair of dicts (the same mechanism `story_done`/`story_ok` use at the story level): it first waits on every one of its own blockers' completion signals — this applies whenever a milestone has one or more blockers, not only 2+. This is deliberate and not merely mirroring `build_dag_tree`'s edge-vs-root split: the node must not raise (see below), so a single-blocker milestone's `build_dag_tree` edge always fires once its one blocker's node completes, clean or not — `build_dag_tree` only gates *scheduling*, never the blocker's outcome. The dependent's own coroutine, not the edge, is what decides whether to dispatch. Once every blocker is confirmed clean (or there are none), the coroutine awaits `_run_milestone_async(milestone_id, ..., slots=<shared semaphore>)`. It must not raise: any `Exception` becomes an `escalated` outcome whose error string is `"<Type>: <msg>"`, the same as the foreign-error convention in `collect_outcomes`. Not raising matters here specifically because the board's single `grafo.TreeExecutor` covers every milestone in one run: a node that raised would stop that executor from enqueuing any node not already in flight, which would disturb independent siblings board-wide — unlike a solo milestone's own `supervise()` tree, where that scope is already just the one milestone. A failing milestone therefore never cancels or disturbs its siblings.
6. **Blocked propagation.** A milestone counts as clean only when its payload status is `done`. If any blocker is not clean (escalated, stopped, cancelled, raised, or itself blocked), the dependent is never dispatched. That check is uniform across blocker counts: a single-blocker milestone's coroutine reads its one blocker's signal the same way a 2+-blocker milestone's coroutine reads all of its blockers' signals (step 5) — the dependent is never dispatched even though `build_dag_tree`'s edge already fired. The dependent is reported as `{"milestone_id": id, "status": "blocked", "blocked_by": [<ids of the non-clean blockers>]}`. Nothing is created for a blocked milestone: no Run row, no lease, no run directory.
7. **Payload.** The function returns a plain dict and does not wrap it in the CLI envelope:
   `{"ok": bool, "board": True, "levels": [{"level": i, "milestones": [ids...]}, ...], "milestones": [...]}`.
   - `levels` comes from step 2's `board_levels` output, which supports the dry-run preview.
   - `milestones` has one entry per open milestone, in level order then in-level order. A dispatched milestone's entry is its full `_run_milestone_async` payload plus `milestone_id` and `status`. An undispatched one gets the blocked shape above.
   - `ok` is `True` only if every entry's `status` is `"done"`. An empty board (no open milestones) returns `ok: True` with empty `levels` and `milestones`, and makes no `refuse_claimed` call that could matter.
8. **Named limitation, kept on purpose.** There is no board-level bookkeeping beyond each milestone's own Run row. The docstring states the recovery path: re-run `am run --board`, where done milestones drop out through `board_levels` and claims, or run `am resume <run-id>` per milestone.

## Error paths (summary)

| Condition | Result |
|---|---|
| Invalid args | Raises before any read or write |
| Cycle among milestones | `DependencyCycleError`, nothing started |
| Claim conflict anywhere on the board at start | `ClaimedError` from the single upfront `refuse_claimed`, nothing started |
| Claim taken mid-run | That milestone's own pre-flight refuses it; it is reported non-done, its dependents are `blocked`, independent siblings are unaffected |
| A milestone raises | Recorded as `escalated` with `"<Type>: <msg>"`, siblings unaffected, dependents `blocked` |

## Tests

Placement rule: the rule in force on this checkout is main design §14 together with the precedent in `tests/e2e/test_parallel_milestone.py`. Fake-`claude` production-wiring tests go in `tests/e2e/`, unmarked, and run in the default suite. The test-tier addendum (`2026-10-02-test-tier-design.md`, V1/V2/V6) is still only *proposed* and explicitly excludes milestone 14's files. If it lands, `tests/e2e/` gets auto-marked `e2e_fake` with no edit needed here. Do not apply the `e2e` marker, which is the real-money tier.

New module `tests/e2e/test_run_board.py`, in the same shape as `test_parallel_milestone.py`. It uses the fake `claude` first on `PATH`, a real temp git repo and a real temp brd board, and calls `orchestrate.run_board` directly with no `driver`/`runner_factory` (no CLI flag exists yet). Every test belongs to the e2e tier above.

1. Two independent milestones: both finish, `ok: True`, both entries `done`, and both appear in level 0.
2. A `blocked_by` pair (B blocked by A): both `done`, A's levels place it before B, and every one of A's subtasks finished before B's first subtask started, checked from the Run rows or the fake-claude log timestamps.
3. Escalation isolation: A escalates (fake claude armed to fail A's subtask), independent sibling C still finishes `done`, and B (blocked by A) is reported `{"status": "blocked", "blocked_by": [A]}`. B has no Run row and none of its subtasks were spawned. `ok: False`.
4. Shared semaphore cap: two independent milestones with `max_concurrent=1`. The concurrency-tracking rendezvous pattern from `test_parallel_milestone.py` shows at most one story inside implement at any moment across both milestones, and with `max_concurrent=2` the rendezvous at count 2 completes.
5. Upfront claim refusal: a live lease planted on a subtask of the second-level milestone makes `run_board` raise `ClaimedError`. No run directory or Run row exists for any milestone, including the first-level one.

Default-tier `uv run pytest` must stay green, and the existing `run_milestone` and `supervise` tests must pass unchanged.

---

# `orchestrate.run_board` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `orchestrate.run_board`, which drives every open milestone on the board as one grafo DAG in one event loop, sharing one `asyncio.Semaphore`, with a single upfront claim check and per-milestone failure isolation.

**Architecture:** Three small pure helpers (`board_prefixes`, `board_claims`, `milestone_status`) plus `run_board` (sync: validate, read, level, claim-check, then one `asyncio.run`) and `_run_board_async` (builds the milestone tree with the existing `build_dag_tree`, runs it under one `grafo.TreeExecutor` through the existing `run_until_killed`, and dispatches each milestone through the existing `_run_milestone_async(..., slots=shared)`). All new code is appended to the end of `src/agent_manager/orchestrate.py`; nothing existing is edited.

**Tech Stack:** Python 3, asyncio, grafo, Pydantic `models.CardNode`, pytest, real `git` + `brd` CLIs and the fake `claude` for the e2e tier.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94/docs/superpowers/specs/task-add-orchestrate-run-baef4f94-design.md` (reproduced verbatim above).

Input note: both upstream summaries handed to this planning stage (the spec summary and the exploration findings) were truncated mid-sentence by the pipeline's character caps. That is itself a sign those stages overran their brief. This plan was written from the spec file on disk and from the code on disk, not from the truncated text.

## Global Constraints

- Additive only: do not change `run_milestone`'s signature, docstring or payload; do not edit `supervise`, `build_dag_tree`, `_run_milestone_async`, `collect_outcomes` or `cli.py`.
- No `--board` flag, no `--card`/`--milestone` refusals, no prefix derivation, no `--dry-run --board` wiring (all d78b3118).
- No board-level `Run` record and no `am resume --board`.
- `run_board` returns the plain dict `{"ok", "board": True, "levels", "milestones"}`, never the `{"ok", "data"}` CLI envelope.
- Claim union dedup is exactly `list(dict.fromkeys(keys))`, in level order then in-level order.
- A bad argument raises `ValueError`, the type `run_milestone` raises.
- Node coroutines never raise an `Exception`; an exception from a milestone becomes `{"milestone_id", "status": "escalated", "error": "<Type>: <msg>"}`.
- E2E tests live in `tests/e2e/test_run_board.py`, unmarked (no `e2e` marker). Seam-level unit tests live in `tests/test_orchestrate.py` (the mirror of `src/agent_manager/orchestrate.py`).
- Verification: `uv run pytest` (no lint or typecheck command exists).

## Review Focus

1. Two milestones handed the same branch prefix would both claim `branch:<prefix>-integrate`; the deduplicated board claim set hides that, so the second milestone would be refused mid-run. Expected: `ValueError` up front, nothing read past the board, nothing claimed. Pinned in Task 2 (`test_board_prefixes_refuses_two_milestones_on_one_prefix`) and Task 3 (`test_run_board_refuses_a_blank_or_shared_branch_prefix_before_any_claim_check`).
2. A milestone that ends `paused` (`am pause`) or `cancelled` (`am cancel`) mid-board. Expected: its entry is `stopped`/`cancelled`, its dependents are `blocked`, `ok` is false. Pinned in Task 3 (`test_run_board_blocks_the_dependent_of_a_milestone_that_did_not_finish_done`).
3. A blocker milestone that is already `done` (dropped by `board_levels`). Expected: counted as satisfied, the dependent runs in level 0. Pinned in Task 3 (`test_run_board_treats_an_already_done_blocker_as_satisfied`).
4. Transitive blocking: a milestone blocked by a *blocked* milestone. Expected: reported `blocked` with `blocked_by` naming the blocked one, never dispatched. Pinned in Task 3 (`test_run_board_isolates_a_milestone_that_raises_and_blocks_only_its_dependents`).
5. A milestone run dying of a non-`Exception` `BaseException`. grafo drops those and would hang `gather()` forever. Expected: the board run ends with that exception instead of hanging. Pinned in Task 3 (`test_run_board_ends_on_a_base_exception_instead_of_hanging`).

---

### Task 1: Bring in the prerequisite branches

The worktree was cut from `m14/task-add-board-levels-for-34bc3c16`. On disk it already has `dag.board_levels`, `orchestrate.build_dag_tree` and `supervise(..., slots=None)`, but **not** `orchestrate._run_milestone_async`. The branch `m14/task-split-run-milestone-a3eaf615` contains all three orchestrate primitives (`build_dag_tree` at its `orchestrate.py:1310`, `supervise(slots=)` at `:1358`, `_run_milestone_async` at `:1616`). Merge it. Do not re-derive anything.

**Files:**
- Modify (by merge only): `src/agent_manager/orchestrate.py`, `tests/test_orchestrate.py`

**Interfaces:**
- Produces: `orchestrate._run_milestone_async(milestone, *, repo_dir, base_branch=None, branch_prefix=None, commands=(), allow_no_verification=False, runner_factory=None, driver=None, clock=_utcnow, max_concurrent=1, resume_run_id=None, control_interval=control.CONTROL_POLL_SECONDS, slots: asyncio.Semaphore | None = None) -> dict[str, Any]`; `orchestrate.build_dag_tree(items, *, id_of, blockers_of, node_factory, forward=None) -> tuple[dict[str, grafo.Node], list[grafo.Node]]`; `orchestrate.run_until_killed(work, killed, fatal)`; `dag.board_levels(roots: list[CardNode]) -> list[list[CardNode]]`.

- [ ] **Step 1: Merge the stacked prerequisite branch**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94
git merge --no-ff m14/task-split-run-milestone-a3eaf615 -m "Merge m14/task-split-run-milestone-a3eaf615 (prerequisite for baef4f94)"
```

Expected: a clean merge. If git reports a conflict, run `git merge --abort` and stop. Report the conflict; do not hand-resolve the prerequisite code.

- [ ] **Step 2: Confirm every prerequisite is present**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94
grep -n "^async def _run_milestone_async\|^async def build_dag_tree\|^async def run_until_killed\|slots: asyncio.Semaphore | None = None" src/agent_manager/orchestrate.py
grep -n "^def board_levels" src/agent_manager/dag.py
```

Expected: `_run_milestone_async`, `build_dag_tree` and `run_until_killed` each appear once. `slots: asyncio.Semaphore | None = None` appears twice (in `supervise` and in `_run_milestone_async`). `board_levels` appears once. If `build_dag_tree` or the `supervise` `slots` line is missing, also merge `m14/task-extract-build-dag-tree-c0a1345c` and `m14/task-let-supervise-accept-an-6bb4f541` the same way, then re-run this step.

- [ ] **Step 3: Baseline the suite**

Run: `uv run pytest`
Expected: PASS (all green). This is the baseline the later tasks must keep.

(No commit step: the merge commit from Step 1 is the commit.)

---

### Task 2: Pure board helpers (`board_prefixes`, `board_claims`, `milestone_status`)

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (append at end of file, after `_run_milestone_async`)
- Test: `tests/test_orchestrate.py` (append at end of file)

**Interfaces:**
- Consumes: `orchestrate.milestone_claims(milestone_id: str, stories: Sequence[census.StoryPlan], branch_prefix: str) -> list[str]`; `census.flatten_milestone(root: CardNode) -> Census` (`.stories`); `models.CardNode` (`id`, `title`, `status`, `blocked_by`, `children`).
- Produces:
  - `orchestrate.board_prefixes(milestones: Sequence[models.CardNode], branch_prefix_of: Callable[[models.CardNode], str]) -> dict[str, str]`, which maps milestone id to prefix in input order and raises `ValueError` on a blank or shared prefix.
  - `orchestrate.board_claims(milestones: Sequence[models.CardNode], prefixes: Mapping[str, str]) -> list[str]`
  - `orchestrate.BoardStatus = Literal["done", "escalated", "stopped", "cancelled", "blocked"]`
  - `orchestrate.milestone_status(payload: Mapping[str, Any]) -> BoardStatus`
  - Test helpers in `tests/test_orchestrate.py`: `_board_milestone(n, *, blocked_by=(), status="todo", done_children=False) -> models.CardNode` and `_prefix_of(card) -> str`. Task 3 reuses them.

- [ ] **Step 1: Write the failing tests**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94/tests/test_orchestrate.py`:

```python
# ── run_board helpers (card baef4f94) ───────────────────────────────────────


def _board_milestone(
    n: int,
    *,
    blocked_by: tuple[int, ...] = (),
    status: str = "todo",
    done_children: bool = False,
) -> models.CardNode:
    """A milestone root with one story holding one subtask, ids from `_plan_id`.

    Milestone `n` is `_plan_id(n)`, its story `_plan_id(n * 100 + 1)`, its
    subtask `_plan_id(n * 100 + 2)`. `blocked_by` names other milestones by `n`.
    """
    child_status = "done" if done_children else "todo"
    subtask = models.CardNode(
        id=_plan_id(n * 100 + 2), title=f"subtask of milestone {n}", status=child_status
    )
    story = models.CardNode(
        id=_plan_id(n * 100 + 1),
        title=f"story of milestone {n}",
        status=child_status,
        children=[subtask],
    )
    return models.CardNode(
        id=_plan_id(n),
        title=f"milestone {n}",
        status=status,
        blocked_by=[_plan_id(blocker) for blocker in blocked_by],
        children=[story],
    )


def _prefix_of(card: models.CardNode) -> str:
    """A distinct branch prefix per milestone, as d78b3118's caller would hand in."""
    return f"p{dag.short_id(card.id)}"


def test_board_prefixes_maps_each_milestone_to_its_callers_prefix_in_order():
    one, two = _board_milestone(1), _board_milestone(2)

    prefixes = orchestrate.board_prefixes([one, two], _prefix_of)

    assert list(prefixes.items()) == [(one.id, "p00000001"), (two.id, "p00000002")]


@pytest.mark.parametrize("prefix", ["", "   ", None])
def test_board_prefixes_refuses_a_blank_prefix(prefix):
    with pytest.raises(ValueError, match="has no branch prefix"):
        orchestrate.board_prefixes([_board_milestone(1)], lambda card: prefix)


def test_board_prefixes_refuses_two_milestones_on_one_prefix():
    """Review Focus 1: a shared prefix means one shared `<prefix>-integrate` claim."""
    with pytest.raises(ValueError, match="share the branch prefix 'm14'"):
        orchestrate.board_prefixes(
            [_board_milestone(1), _board_milestone(2)], lambda card: "m14"
        )


def test_board_claims_unions_each_milestones_claims_first_occurrence_first():
    one, two = _board_milestone(1), _board_milestone(2)
    prefixes = {one.id: "pa", two.id: "pb"}

    keys = orchestrate.board_claims([one, two, one], prefixes)

    assert keys == [
        f"card:{one.id}",
        f"card:{_plan_id(102)}",
        "branch:pa-integrate",
        f"card:{two.id}",
        f"card:{_plan_id(202)}",
        "branch:pb-integrate",
    ]


def test_board_claims_takes_each_milestones_keys_from_milestone_claims():
    one = _board_milestone(1)
    expected = orchestrate.milestone_claims(
        one.id, census.flatten_milestone(one).stories, "pa"
    )

    assert orchestrate.board_claims([one], {one.id: "pa"}) == expected


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"done": True, "run_id": "r"}, "done"),
        ({"escalated": True, "run_id": "r"}, "escalated"),
        ({"escalated": True, "control": "pause", "run_id": "r"}, "escalated"),
        ({"paused": True, "run_id": "r", "resume": "am resume r"}, "stopped"),
        ({"cancelled": True, "run_id": "r"}, "cancelled"),
        ({"run_id": "r"}, "escalated"),
    ],
)
def test_milestone_status_reads_a_run_milestone_payload(payload, status):
    assert orchestrate.milestone_status(payload) == status
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "board_prefixes or board_claims or milestone_status" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'board_prefixes'` (and likewise `board_claims`, `milestone_status`).

- [ ] **Step 3: Write the minimal implementation**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94/src/agent_manager/orchestrate.py`:

```python
# ── the board run (card baef4f94) ───────────────────────────────────────────


BoardStatus = Literal["done", "escalated", "stopped", "cancelled", "blocked"]
"""How one milestone of a board run ended: its own run's outcome, or `blocked`
when a blocker did not finish `done` and it was never dispatched."""


def board_prefixes(
    milestones: Sequence[models.CardNode],
    branch_prefix_of: Callable[[models.CardNode], str],
) -> dict[str, str]:
    """Each open milestone's branch prefix, keyed by milestone id, in input order.

    `branch_prefix_of` is the caller's: deriving a prefix is not this module's
    job. This only checks it. A prefix that is not a non-blank string is
    `ValueError`, as `run_milestone` refuses a missing one. So is a prefix two
    milestones share: both would claim `branch:<prefix>-integrate`, and the
    deduplicated board claim set would hide that until the second milestone's
    own pre-flight refused it mid-run.
    """
    prefixes: dict[str, str] = {}
    owners: dict[str, str] = {}
    for card in milestones:
        prefix = branch_prefix_of(card)
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError(f"milestone {card.id} has no branch prefix (got {prefix!r})")
        if prefix in owners:
            raise ValueError(
                f"milestones {owners[prefix]} and {card.id} share the branch prefix {prefix!r}"
            )
        owners[prefix] = card.id
        prefixes[card.id] = prefix
    return prefixes


def board_claims(
    milestones: Sequence[models.CardNode], prefixes: Mapping[str, str]
) -> list[str]:
    """Every open milestone's `milestone_claims`, in the order given, deduplicated.

    The stories come from `census.flatten_milestone`, the census
    `run_milestone` reads. The union keeps a key's first occurrence in its
    place (`list(dict.fromkeys(...))`), the discipline `milestone_claims`
    itself follows. Pure.
    """
    keys: list[str] = []
    for card in milestones:
        stories = census.flatten_milestone(card).stories
        keys.extend(milestone_claims(card.id, stories, prefixes[card.id]))
    return list(dict.fromkeys(keys))


def milestone_status(payload: Mapping[str, Any]) -> BoardStatus:
    """One `_run_milestone_async` payload read as a board status.

    `done` is the only clean outcome. A cancel is `cancelled`, an escalation
    (a paused one included) is `escalated`, a pause is `stopped`. Any other
    shape is not clean, so it counts as `escalated`.
    """
    if payload.get("done") is True:
        return "done"
    if payload.get("cancelled") is True:
        return "cancelled"
    if payload.get("escalated") is True:
        return "escalated"
    if payload.get("paused") is True:
        return "stopped"
    return "escalated"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "board_prefixes or board_claims or milestone_status" -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "Add the board-run helpers: board_prefixes, board_claims, milestone_status"
```

---

### Task 3: `orchestrate.run_board`

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (append after Task 2's helpers)
- Test: `tests/test_orchestrate.py` (append after Task 2's tests) for seam-level unit tests
- Create: `tests/e2e/test_run_board.py` (spec §Tests, e2e tier, unmarked)

**Interfaces:**
- Consumes:
  - Task 2's `board_prefixes`, `board_claims`, `milestone_status`, `BoardStatus`, `_board_milestone` and `_prefix_of`.
  - Task 1's `_run_milestone_async(..., slots=)`, `build_dag_tree` and `run_until_killed`.
  - `dag.board_levels`, `board.roots(*, repo_dir)`, `cli.refuse_claimed(root, keys, *, run_id=None)`, `runs.resolve_repo_dir(repo_dir) -> Path`, and `GRAFO_LOGGER`.
  - From `tests/e2e/conftest.py`, the fixtures `fresh_project`, `fake_claude_bin` and `rendezvous` (`.arm(count)`, `.markers()`).
  - From `tests/test_orchestrate.py`, `OTHER_RUN_ID` and `_plan_id`.
- Produces (for d78b3118):

```python
def run_board(
    *,
    repo_dir: Path,
    base_branch: str | None,
    branch_prefix_of: Callable[[models.CardNode], str],
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]
```

  It returns `{"ok": bool, "board": True, "levels": [{"level": int, "milestones": [str, ...]}, ...], "milestones": [entry, ...]}`. A dispatched entry is `{"milestone_id", "status", **payload}`. A blocked entry is `{"milestone_id", "status": "blocked", "blocked_by": [...]}`. A milestone that raised is `{"milestone_id", "status": "escalated", "error": "<Type>: <msg>"}`.

- [ ] **Step 1: Write the failing seam-level unit tests**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94/tests/test_orchestrate.py`:

```python
# ── run_board at its seams (card baef4f94) ──────────────────────────────────
#
# `board.roots`, `cli.refuse_claimed` and `orchestrate._run_milestone_async`
# are replaced, so these exercise `run_board`'s own validation, leveling,
# claim union, tree, isolation and payload with no git, brd or harness.
# Production wiring is `tests/e2e/test_run_board.py`'s.


@dataclass
class FakeMilestoneRuns:
    """`orchestrate._run_milestone_async` replaced: records each call and
    answers from `outcomes` (a payload dict, or an exception to raise);
    a milestone with no entry finishes `done`."""

    outcomes: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def __call__(self, milestone: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((milestone, kwargs))
        await asyncio.sleep(0)
        outcome = self.outcomes.get(milestone, {"done": True, "run_id": f"run-{milestone[:8]}"})
        if isinstance(outcome, BaseException):
            raise outcome
        return dict(outcome)

    def called(self) -> list[str]:
        return [milestone for milestone, _kwargs in self.calls]


@dataclass
class BoardSeams:
    root: Path
    runs: FakeMilestoneRuns
    cards: list[models.CardNode] = field(default_factory=list)
    claims: list[list[str]] = field(default_factory=list)


@pytest.fixture
def board_seams(tmp_path, monkeypatch) -> BoardSeams:
    seams = BoardSeams(root=tmp_path, runs=FakeMilestoneRuns())
    monkeypatch.setattr(board, "roots", lambda *, repo_dir=None: list(seams.cards))
    monkeypatch.setattr(orchestrate, "_run_milestone_async", seams.runs)

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        seams.claims.append(list(keys))

    monkeypatch.setattr(cli, "refuse_claimed", refuse)
    return seams


def _board(seams: BoardSeams, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "repo_dir": seams.root,
        "base_branch": "main",
        "branch_prefix_of": _prefix_of,
        "max_concurrent": 2,
    }
    kwargs.update(overrides)
    return orchestrate.run_board(**kwargs)


def _by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["milestone_id"]: entry for entry in result["milestones"]}


@pytest.mark.parametrize(
    "overrides", [{"max_concurrent": 0}, {"base_branch": None}, {"base_branch": ""}]
)
def test_run_board_refuses_bad_arguments_before_it_reads_the_board(
    board_seams, monkeypatch, overrides
):
    def no_read(*, repo_dir=None):
        pytest.fail("run_board read the board before refusing its arguments")

    monkeypatch.setattr(board, "roots", no_read)

    with pytest.raises(ValueError):
        _board(board_seams, **overrides)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


@pytest.mark.parametrize(
    "prefix_of", [lambda card: "", lambda card: "   ", lambda card: None, lambda card: "same"]
)
def test_run_board_refuses_a_blank_or_shared_branch_prefix_before_any_claim_check(
    board_seams, prefix_of
):
    board_seams.cards = [_board_milestone(1), _board_milestone(2)]

    with pytest.raises(ValueError, match="branch prefix"):
        _board(board_seams, branch_prefix_of=prefix_of)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_lets_a_milestone_cycle_propagate_before_any_claim_check(board_seams):
    board_seams.cards = [
        _board_milestone(1, blocked_by=(2,)),
        _board_milestone(2, blocked_by=(1,)),
    ]

    with pytest.raises(dag.DependencyCycleError):
        _board(board_seams)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_on_a_board_with_nothing_open_is_ok_and_runs_nothing(board_seams):
    board_seams.cards = [_board_milestone(1, status="done", done_children=True)]

    result = _board(board_seams)

    assert result == {"ok": True, "board": True, "levels": [], "milestones": []}
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_checks_every_open_milestones_claims_once_in_level_order(board_seams):
    first = _board_milestone(1)
    second = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [second, first]  # board order is not level order

    _board(board_seams)

    expected = list(
        dict.fromkeys(
            [
                *orchestrate.milestone_claims(
                    first.id, census.flatten_milestone(first).stories, _prefix_of(first)
                ),
                *orchestrate.milestone_claims(
                    second.id, census.flatten_milestone(second).stories, _prefix_of(second)
                ),
            ]
        )
    )
    assert board_seams.claims == [expected]
    assert expected[0] == f"card:{first.id}"


def test_run_board_starts_no_milestone_when_the_upfront_claim_check_refuses(
    board_seams, monkeypatch
):
    board_seams.cards = [_board_milestone(1), _board_milestone(2, blocked_by=(1,))]

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        raise cli.ClaimedError("held elsewhere", key=keys[-1], run_id=OTHER_RUN_ID)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)

    with pytest.raises(cli.ClaimedError):
        _board(board_seams)

    assert board_seams.runs.calls == []


def test_run_board_runs_every_milestone_on_one_shared_semaphore(board_seams):
    one, two = _board_milestone(1), _board_milestone(2)
    board_seams.cards = [one, two]

    result = _board(board_seams, max_concurrent=3, commands=("git status",))

    assert result["ok"] is True
    assert result["board"] is True
    assert result["levels"] == [{"level": 0, "milestones": [one.id, two.id]}]
    assert result["milestones"] == [
        {"milestone_id": one.id, "status": "done", "done": True, "run_id": f"run-{one.id[:8]}"},
        {"milestone_id": two.id, "status": "done", "done": True, "run_id": f"run-{two.id[:8]}"},
    ]
    assert sorted(board_seams.runs.called()) == sorted([one.id, two.id])
    semaphores = [kwargs["slots"] for _milestone, kwargs in board_seams.runs.calls]
    assert isinstance(semaphores[0], asyncio.Semaphore)
    assert all(semaphore is semaphores[0] for semaphore in semaphores)
    for milestone, kwargs in board_seams.runs.calls:
        card = one if milestone == one.id else two
        assert kwargs["branch_prefix"] == _prefix_of(card)
        assert kwargs["base_branch"] == "main"
        assert kwargs["repo_dir"] == runs.resolve_repo_dir(board_seams.root)
        assert kwargs["max_concurrent"] == 3
        assert list(kwargs["commands"]) == ["git status"]
        assert "resume_run_id" not in kwargs


def test_run_board_isolates_a_milestone_that_raises_and_blocks_only_its_dependents(
    board_seams,
):
    """Spec steps 5-6 and Review Focus 4: D is blocked by a *blocked* B."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3)
    d = _board_milestone(4, blocked_by=(2,))
    board_seams.cards = [a, b, c, d]
    board_seams.runs.outcomes[a.id] = RuntimeError("boom")
    grafo_level = logging.getLogger(orchestrate.GRAFO_LOGGER).level

    result = _board(board_seams)

    assert result["ok"] is False
    assert [entry["milestone_id"] for entry in result["milestones"]] == [a.id, c.id, b.id, d.id]
    by_id = _by_id(result)
    assert by_id[a.id] == {"milestone_id": a.id, "status": "escalated", "error": "RuntimeError: boom"}
    assert by_id[c.id]["status"] == "done"
    assert by_id[b.id] == {"milestone_id": b.id, "status": "blocked", "blocked_by": [a.id]}
    assert by_id[d.id] == {"milestone_id": d.id, "status": "blocked", "blocked_by": [b.id]}
    assert sorted(board_seams.runs.called()) == sorted([a.id, c.id])
    assert logging.getLogger(orchestrate.GRAFO_LOGGER).level == grafo_level


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"escalated": True, "run_id": "r"}, "escalated"),
        ({"paused": True, "run_id": "r", "resume": "am resume r"}, "stopped"),
        ({"cancelled": True, "run_id": "r"}, "cancelled"),
    ],
)
def test_run_board_blocks_the_dependent_of_a_milestone_that_did_not_finish_done(
    board_seams, payload, status
):
    """Review Focus 2: a paused or cancelled milestone blocks like an escalated one."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    board_seams.runs.outcomes[a.id] = payload

    result = _board(board_seams)

    assert result["ok"] is False
    assert result["milestones"] == [
        {"milestone_id": a.id, "status": status, **payload},
        {"milestone_id": b.id, "status": "blocked", "blocked_by": [a.id]},
    ]
    assert board_seams.runs.called() == [a.id]


def test_run_board_runs_a_two_blocker_milestone_only_after_both_finish_done(board_seams):
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.cards = [a, b, c]

    result = _board(board_seams)

    assert result["ok"] is True
    assert result["levels"] == [
        {"level": 0, "milestones": [a.id, b.id]},
        {"level": 1, "milestones": [c.id]},
    ]
    assert board_seams.runs.called()[-1] == c.id
    assert _by_id(result)[c.id]["status"] == "done"


def test_run_board_blocks_a_two_blocker_milestone_on_only_its_unclean_blocker(board_seams):
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.cards = [a, b, c]
    board_seams.runs.outcomes[b.id] = {"escalated": True, "run_id": "r"}

    result = _board(board_seams)

    assert _by_id(result)[c.id] == {"milestone_id": c.id, "status": "blocked", "blocked_by": [b.id]}
    assert c.id not in board_seams.runs.called()


def test_run_board_treats_an_already_done_blocker_as_satisfied(board_seams):
    """Review Focus 3: `board_levels` drops the done milestone, so its dependent is level 0."""
    finished = _board_milestone(1, status="done", done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [finished, later]

    result = _board(board_seams)

    assert result["ok"] is True
    assert result["levels"] == [{"level": 0, "milestones": [later.id]}]
    assert board_seams.runs.called() == [later.id]


def test_run_board_ends_on_a_base_exception_instead_of_hanging(board_seams):
    """Review Focus 5: grafo drops a non-`Exception` and would hang `gather()`;
    the board run re-raises it through `run_until_killed`."""

    class Fatal(BaseException):
        pass

    a, b = _board_milestone(1), _board_milestone(2)
    board_seams.cards = [a, b]
    board_seams.runs.outcomes[a.id] = Fatal("dead")

    with pytest.raises(Fatal):
        _board(board_seams)
```

- [ ] **Step 2: Run the unit tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k run_board -v`
Expected: FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'run_board'`.

- [ ] **Step 3: Write the failing e2e tests**

Create `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94/tests/e2e/test_run_board.py`:

```python
"""Default-suite e2e tier: `orchestrate.run_board` through the production wiring (card baef4f94).

Board-run spec §3.8 and main spec §14. `orchestrate.run_board` is called
directly (`am run --board` is sibling d78b3118's) with no `runner_factory`
and no `driver`, so every milestone reaches `_run_milestone_async`,
`supervise`, `cli.drive_subtask_async`, `cli.default_runner_factory`, the
real `ClaudeAdapter` and `launcher.run_direct`. The only stand-in is the fake
`claude` first on `PATH` (`fake_claude_bin`). Unmarked on purpose, like
`test_parallel_milestone.py`: the `e2e` marker is the real-money tier.

Every milestone here has one story holding one subtask, so a milestone's lane
holds one slot of the board's shared semaphore for its whole run. Each test
builds its own repo and board (`fresh_project`), because a run moves every
card and branch it touches.
"""

import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import board, cli, dag, models, orchestrate, paths, store

VERIFY_COMMANDS = ("git rev-parse --verify HEAD",)
"""Must equal the conftest's `VERIFY_COMMANDS`: a real, green command for this toy repo."""

UNION_ATTRIBUTE = "IMPLEMENTATION.md merge=union\n"
"""Must equal the conftest's `UNION_ATTRIBUTE`."""

REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""Must equal the conftest's `FAKE_REVIEW_FAIL_MARKER`."""

OTHER_RUN_ID = "20260930T080000Z-a1b2c3d4"
"""Another run, in another `am` process, that holds a claim."""

HERE = socket.gethostname()
"""This host, as `control.Lease` records it."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _add_card(
    root: Path, title: str, parent: str | None = None, blocked_by: tuple[str, ...] = ()
) -> str:
    """`brd add`, then `brd block` per blocker, as the conftest's `_add_card` does."""
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True)
    card_id = json.loads(completed.stdout)["data"]["id"]
    for blocker in blocked_by:
        subprocess.run(
            ["brd", "block", card_id, "--by", blocker],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    return card_id


def _milestone(
    root: Path, label: str, prefix: str, blocked_by: tuple[str, ...] = ()
) -> dict[str, str]:
    """A milestone with one story and one subtask; `branch` is the subtask's, from `dag`."""
    milestone = _add_card(
        root, f"Milestone {label}: a board run under a fake claude", blocked_by=blocked_by
    )
    story = _add_card(root, f"Story {label}: the only story of milestone {label}", milestone)
    subtask = _add_card(root, f"{label.lower()}1: the only subtask of story {label}", story)
    return {
        "id": milestone,
        "story": story,
        "subtask": subtask,
        "prefix": prefix,
        "branch": dag.task_branch(prefix, board.show(subtask, repo_dir=root)),
    }


@pytest.fixture
def board_root(fresh_project, fake_claude_bin) -> Path:
    """A fresh repo and board, the fake `claude` first on `PATH`, and the union
    attribute so each milestone's Integrate folds `IMPLEMENTATION.md`."""
    attributes = fresh_project / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    return fresh_project


def _run_board(root: Path, *milestones: dict[str, str], max_concurrent: int = 1) -> dict[str, Any]:
    prefixes = {milestone["id"]: milestone["prefix"] for milestone in milestones}
    return orchestrate.run_board(
        repo_dir=root,
        base_branch="main",
        branch_prefix_of=lambda card: prefixes[card.id],
        commands=VERIFY_COMMANDS,
        max_concurrent=max_concurrent,
    )


def _entries(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["milestone_id"]: entry for entry in result["milestones"]}


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _run_ids(root: Path) -> list[str]:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return [row["id"] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _run_dirs() -> list[Path]:
    runs_root = paths.data_dir() / "runs"
    return sorted(runs_root.iterdir()) if runs_root.exists() else []


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _story_span(run: models.Run, story_id: str):
    """The earliest phase start and the latest phase end across one story's subtasks."""
    (story,) = [story for story in run.stories if story.card_id == story_id]
    phases = [phase for subtask in story.subtasks for phase in subtask.phases]
    starts = [phase.started_at for phase in phases if phase.started_at is not None]
    ends = [phase.ended_at for phase in phases if phase.ended_at is not None]
    assert starts and ends, story_id  # non-vacuity: the story really ran phases
    return min(starts), max(ends)


def _plant_lease(root: Path, key: str) -> None:
    """A live `run_leases` row and its claim, as another `am` process's `Lease` leaves them.

    Live by C2: this process's pid, this host, a fresh heartbeat.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        with store.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (OTHER_RUN_ID, "other-life", os.getpid(), HERE, now, now),
            )
            conn.execute(
                "INSERT INTO run_claims (key, run_id, token, claimed_at) VALUES (?, ?, ?, ?)",
                (key, OTHER_RUN_ID, "other-life", now),
            )
    finally:
        conn.close()


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or board wiring stops being
    checked on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_two_independent_milestones_both_finish(board_root):
    """Spec test 1."""
    root = board_root
    x = _milestone(root, "X", "bx")
    y = _milestone(root, "Y", "by")
    main_before = _git(root, "rev-parse", "main").strip()

    result = _run_board(root, x, y, max_concurrent=2)

    assert result["ok"] is True, result
    assert result["board"] is True
    (level,) = result["levels"]
    assert level["level"] == 0
    assert sorted(level["milestones"]) == sorted([x["id"], y["id"]])
    entries = _entries(result)
    assert set(entries) == {x["id"], y["id"]}
    for milestone in (x, y):
        entry = entries[milestone["id"]]
        assert entry["status"] == "done", entry
        assert entry["done"] is True, entry
        assert entry["integrated"]["branch"] == f"{milestone['prefix']}-integrate"
        assert _load_run(root, entry["run_id"]).milestone_id == milestone["id"]
        assert board.show(milestone["subtask"], repo_dir=root).status == "done"
    assert entries[x["id"]]["run_id"] != entries[y["id"]]["run_id"]
    assert _git(root, "rev-parse", "main").strip() == main_before


def test_a_blocked_by_pair_runs_in_order(board_root):
    """Spec test 2: two slots are free, so only the dependency can order them."""
    root = board_root
    a = _milestone(root, "A", "ba")
    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))

    result = _run_board(root, a, b, max_concurrent=2)

    assert result["ok"] is True, result
    assert result["levels"] == [
        {"level": 0, "milestones": [a["id"]]},
        {"level": 1, "milestones": [b["id"]]},
    ]
    assert [entry["milestone_id"] for entry in result["milestones"]] == [a["id"], b["id"]]
    entries = _entries(result)
    assert entries[a["id"]]["status"] == "done"
    assert entries[b["id"]]["status"] == "done"
    _a_start, a_end = _story_span(_load_run(root, entries[a["id"]]["run_id"]), a["story"])
    b_start, _b_end = _story_span(_load_run(root, entries[b["id"]]["run_id"]), b["story"])
    assert a_end <= b_start, (a_end, b_start)


def test_an_escalated_milestone_blocks_its_dependent_and_not_its_sibling(board_root):
    """Spec test 3: A's review fails through the production gate."""
    root = board_root
    a = _milestone(root, "A", "ba")
    c = _milestone(root, "C", "bc")
    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))
    b_status_before = board.show(b["subtask"], repo_dir=root).status
    (root / ".git" / REVIEW_FAIL_MARKER).write_text(f"{a['branch']}\n", encoding="utf-8")

    result = _run_board(root, a, b, c, max_concurrent=2)

    assert result["ok"] is False, result
    assert sorted(result["levels"][0]["milestones"]) == sorted([a["id"], c["id"]])
    assert result["levels"][1] == {"level": 1, "milestones": [b["id"]]}
    entries = _entries(result)
    escalated = entries[a["id"]]
    assert escalated["status"] == "escalated", escalated
    assert escalated["escalated"] is True
    assert (escalated["story"], escalated["subtask"], escalated["failed_phase"]) == (
        a["story"],
        a["subtask"],
        "review",
    )
    assert entries[c["id"]]["status"] == "done", entries[c["id"]]
    assert entries[b["id"]] == {
        "milestone_id": b["id"],
        "status": "blocked",
        "blocked_by": [a["id"]],
    }
    # B was never dispatched: no Run row, no run directory, no worktree, no branch.
    b_short = dag.short_id(b["id"])
    assert not [run_id for run_id in _run_ids(root) if run_id.endswith(b_short)]
    assert not [path for path in _run_dirs() if path.name.endswith(b_short)]
    assert not cli.worktree_for(root, b["branch"]).exists()
    assert b["branch"] not in _local_branches(root)
    assert board.show(b["subtask"], repo_dir=root).status == b_status_before


def test_one_shared_slot_runs_two_independent_milestones_one_after_the_other(board_root):
    """Spec test 4, cap 1: one semaphore across both milestones, so their lanes never overlap."""
    root = board_root
    x = _milestone(root, "X", "bx")
    y = _milestone(root, "Y", "by")

    result = _run_board(root, x, y, max_concurrent=1)

    assert result["ok"] is True, result
    entries = _entries(result)
    x_start, x_end = _story_span(_load_run(root, entries[x["id"]]["run_id"]), x["story"])
    y_start, y_end = _story_span(_load_run(root, entries[y["id"]]["run_id"]), y["story"])
    assert x_end <= y_start or y_end <= x_start, (x_start, x_end, y_start, y_end)


def test_two_shared_slots_let_two_milestones_meet_inside_implement(board_root, rendezvous):
    """Spec test 4, cap 2: the rendezvous at count 2 completes only if both
    milestones' implements were running at the same time."""
    root = board_root
    x = _milestone(root, "X", "bx")
    y = _milestone(root, "Y", "by")
    rendezvous.arm(2)

    result = _run_board(root, x, y, max_concurrent=2)

    assert result["ok"] is True, result
    assert len(rendezvous.markers()) == 2


def test_a_claim_on_a_later_milestone_refuses_the_whole_board_up_front(board_root):
    """Spec test 5: the single upfront `refuse_claimed` runs before any milestone starts."""
    root = board_root
    a = _milestone(root, "A", "ba")
    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))
    key = f"card:{b['subtask']}"
    _plant_lease(root, key)
    statuses_before = {
        card: board.show(card, repo_dir=root).status for card in (a["subtask"], b["subtask"])
    }

    with pytest.raises(cli.ClaimedError) as caught:
        _run_board(root, a, b)

    assert caught.value.key == key
    assert caught.value.run_id == OTHER_RUN_ID
    assert _run_ids(root) == []
    assert _run_dirs() == []
    assert _local_branches(root) == ["main"]
    assert {
        card: board.show(card, repo_dir=root).status for card in statuses_before
    } == statuses_before


def test_no_rendezvous_is_left_armed_for_later_tests():
    """Kept last in the module: the rendezvous is armed through the
    function-scoped `monkeypatch`, so it must be gone once a test ends."""
    assert "FAKE_CLAUDE_RENDEZVOUS_DIR" not in os.environ
    assert "FAKE_CLAUDE_RENDEZVOUS_COUNT" not in os.environ
```

- [ ] **Step 4: Run the e2e tests to verify they fail**

Run: `uv run pytest tests/e2e/test_run_board.py -v`
Expected: the six board tests FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'run_board'`. The unmarked-module test and the rendezvous-hygiene test PASS.

- [ ] **Step 5: Write the implementation**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94/src/agent_manager/orchestrate.py`, after Task 2's `milestone_status`:

```python
def run_board(
    *,
    repo_dir: Path,
    base_branch: str | None,
    branch_prefix_of: Callable[[models.CardNode], str],
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Drive every open milestone on the board as one grafo tree, and report.

    Refusals come first, in this order, and each leaves nothing behind. Bad
    arguments are `ValueError` before the board is read: `max_concurrent < 1`
    and a missing `base_branch`, as `run_milestone` refuses them. Then come the
    open milestones: `board.roots()` leveled by `dag.board_levels`, so a done
    milestone with nothing open under it drops out and a blocker cycle is
    `DependencyCycleError`. Next, each milestone's prefix from
    `branch_prefix_of` (`board_prefixes`: blank or shared is `ValueError`).
    Last, one `cli.refuse_claimed` over every open milestone's
    `milestone_claims`, unioned in level order (`board_claims`): a key another
    live run holds is `ClaimedError` before any milestone starts, so there is
    no run row, run directory or lease for any of them. Each milestone's own
    pre-flight inside `_run_milestone_async` still runs and catches a claim
    taken after this one.

    Then one `asyncio.run` covers the whole board with one
    `asyncio.Semaphore(max_concurrent)` that every milestone's lanes share
    (`_run_board_async`). A milestone runs once every open blocker finished
    `done`. A milestone whose blocker did not finish `done` is never
    dispatched and is reported `blocked`. A milestone that raises is reported
    `escalated` with `"<Type>: <msg>"` and never disturbs its siblings.

    Returns the plain payload, not the CLI envelope:
    `{"ok", "board": True, "levels": [{"level", "milestones": [ids]}],
    "milestones": [entry, ...]}`. Entries are in level order. A dispatched
    entry is `{"milestone_id", "status", **its run payload}`. `ok` is true
    only when every entry is `done`. An empty board is `ok` with nothing run.

    Known limitation, kept on purpose: there is no board-level Run record.
    Each milestone keeps its own Run row and nothing else records what the
    board run had dispatched, so a crash mid-board loses only that
    bookkeeping. To recover, re-run `am run --board`: done milestones drop out
    through `board_levels` and the claims. Or resume a stopped or escalated
    milestone on its own with `am resume <run-id>`.
    """
    if max_concurrent < 1:
        raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
    if not base_branch:
        raise ValueError("a board run needs a base branch")
    root = runs.resolve_repo_dir(repo_dir)
    levels = dag.board_levels(board.roots(repo_dir=root))
    milestones = [card for level in levels for card in level]
    prefixes = board_prefixes(milestones, branch_prefix_of)
    levels_payload = [
        {"level": index, "milestones": [card.id for card in level]}
        for index, level in enumerate(levels)
    ]
    if not milestones:
        return {"ok": True, "board": True, "levels": levels_payload, "milestones": []}
    cli.refuse_claimed(root, board_claims(milestones, prefixes))
    entries = asyncio.run(
        _run_board_async(
            milestones,
            prefixes=prefixes,
            root=root,
            base_branch=base_branch,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            driver=driver,
            clock=clock,
            max_concurrent=max_concurrent,
            control_interval=control_interval,
        )
    )
    return {
        "ok": all(entry["status"] == "done" for entry in entries),
        "board": True,
        "levels": levels_payload,
        "milestones": entries,
    }


async def _run_board_async(
    milestones: Sequence[models.CardNode],
    *,
    prefixes: Mapping[str, str],
    root: Path,
    base_branch: str,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: runs.RunnerFactory | None,
    driver: Driver | None,
    clock: Callable[[], datetime],
    max_concurrent: int,
    control_interval: float,
) -> list[dict[str, Any]]:
    """`run_board`'s one event loop: one entry per milestone, in `milestones` order.

    The tree comes from `build_dag_tree`, with each milestone's blockers being
    its `blocked_by` restricted to `milestones` (a done blocker is not here,
    so it is satisfied). Every node waits on each of its own blockers'
    `milestone_done` and reads `milestone_ok`, whatever the blocker count.
    An edge only schedules, and a node never raises an `Exception`, so the
    edge fires whether or not the blocker finished `done`. A node dispatches
    only when every blocker is clean. Otherwise it records `blocked` with the
    unclean blockers. A non-`Exception` `BaseException` (not a cancel) ends the
    whole run through `run_until_killed`, as in `supervise`, because grafo
    would drop it and hang. The `grafo` logger is at CRITICAL for exactly this
    call.
    """
    grafo_logger = logging.getLogger(GRAFO_LOGGER)
    level_before = grafo_logger.level
    grafo_logger.setLevel(logging.CRITICAL)
    try:
        slots = asyncio.Semaphore(max_concurrent)
        open_ids = {card.id for card in milestones}
        blockers = {
            card.id: [
                blocker for blocker in dict.fromkeys(card.blocked_by) if blocker in open_ids
            ]
            for card in milestones
        }
        milestone_done = {card.id: asyncio.Event() for card in milestones}
        milestone_ok: dict[str, bool] = {}
        entries: dict[str, dict[str, Any]] = {}
        fatal: list[BaseException] = []
        killed = asyncio.Event()

        async def dispatch(card: models.CardNode) -> dict[str, Any]:
            try:
                payload = await _run_milestone_async(
                    card.id,
                    repo_dir=root,
                    base_branch=base_branch,
                    branch_prefix=prefixes[card.id],
                    commands=commands,
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    driver=driver,
                    clock=clock,
                    max_concurrent=max_concurrent,
                    control_interval=control_interval,
                    slots=slots,
                )
            except Exception as error:
                return {
                    "milestone_id": card.id,
                    "status": "escalated",
                    "error": f"{type(error).__name__}: {error}",
                }
            return {"milestone_id": card.id, "status": milestone_status(payload), **payload}

        def node_coroutine(card: models.CardNode) -> Callable[..., Awaitable[str]]:
            async def run(**_forwarded: Any) -> str:
                try:
                    for blocker in blockers[card.id]:
                        await milestone_done[blocker].wait()
                    unclean = [
                        blocker
                        for blocker in blockers[card.id]
                        if not milestone_ok.get(blocker, False)
                    ]
                    if unclean:
                        entries[card.id] = {
                            "milestone_id": card.id,
                            "status": "blocked",
                            "blocked_by": unclean,
                        }
                    else:
                        entries[card.id] = await dispatch(card)
                except BaseException as error:
                    if not isinstance(error, (Exception, asyncio.CancelledError)):
                        fatal.append(error)
                        killed.set()
                    raise
                finally:
                    milestone_ok[card.id] = entries.get(card.id, {}).get("status") == "done"
                    milestone_done[card.id].set()
                return card.id

            return run

        _nodes, roots = await build_dag_tree(
            milestones,
            id_of=lambda card: card.id,
            blockers_of=lambda card: blockers[card.id],
            node_factory=node_coroutine,
        )
        executor = grafo.TreeExecutor(uuid="board", roots=roots)
        await run_until_killed(executor.run(), killed, fatal)
        return [entries[card.id] for card in milestones]
    finally:
        grafo_logger.setLevel(level_before)
```

- [ ] **Step 6: Run the unit tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "run_board or board_prefixes or board_claims or milestone_status" -v`
Expected: PASS.

- [ ] **Step 7: Run the e2e tests to verify they pass**

Run: `uv run pytest tests/e2e/test_run_board.py -v`
Expected: PASS (8 tests).

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: PASS. The existing `run_milestone`, `supervise` and `build_dag_tree` tests pass unchanged.

- [ ] **Step 9: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-orchestrate-run-baef4f94
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py tests/e2e/test_run_board.py
git commit -m "Add orchestrate.run_board: every open milestone as one DAG on one shared semaphore"
```
