# Dispatch each board milestone on its own base branch — design

Date: 2026-10-04
Card: `5b772688` (subtask 1.3 of story `8fd3e3de`)
Blocked by: `8198b0b4` (1.2, `board_prefixes(..., roots=)`), done on this branch
Status: approved scope (card), pre-plan

## 0. Parent spec and line references

Parent: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`.
"parent L<n>" below means a line in that file. Source line numbers are from
this branch's HEAD (`bfda17c`).

Inherited constraints:

| constraint | parent |
|---|---|
| A milestone blocked by an earlier milestone starts from that milestone's integrate branch | Goals, L22-23 |
| The base table: no open/unlanded blocker → `--base-branch`; one open blocker → its `<prefix>-integrate`; one `done` blocker whose integrate branch **exists locally** → that branch; `done` blocker without it → `--base-branch`; two or more → refused | §1, L41-47 |
| `MilestoneBlockersError` is raised in `run_board`'s refusals section, **after cycle detection and prefix derivation, before the claims check** | L52-54 |
| Its message names the milestone and its open blockers and says to chain them | L54-55 |
| Exit 3 with the usual envelope; it leaves **no run row, run directory or lease** | L55-56 |
| `milestone_bases(milestones, prefixes, branch_exists, base_branch)` is pure, with the local-branch-exists check injected | L62-64 |
| `_run_board_async` passes each milestone its own entry instead of the shared `base_branch` | L64-65 |
| Stacked milestones run exactly as before afterwards (stories root on the milestone's base; its Integrate merges into its own `<prefix>-integrate`) | L66-68 |
| A relaunch or `am resume` keeps the recorded `resumed.base_branch` | L69-70 |
| `am` never merges into or otherwise touches `--base-branch` | Non-goals, L30 |
| Boards with no inter-milestone `blocked_by` edges behave exactly as today; a `done` blocker with no local integrate branch behaves as today | Compatibility, L100-101 |
| JSON changes additive only; journal and watch schema stay 1 | L102 |
| Unit: "`run_board` refusal leaves nothing behind" | Testing, L107-111 |

Already in place (cards 1.1 and 1.2), consumed unchanged by this card:

- `orchestrate.board_prefixes(milestones, branch_prefix_of, *, roots=())`
  (`orchestrate.py:2198-2245`): keys the given milestones, then their direct
  blocker roots from `roots`, of any status; blank or shared prefix is
  `ValueError`.
- `orchestrate.MilestoneBlockersError(ValueError)` (`orchestrate.py:2248-2255`).
- `orchestrate.milestone_bases(milestones, prefixes, branch_exists, base_branch)`
  (`orchestrate.py:2273-2329`): `milestones` is every root, open or not; only
  open ones get a key; asks `branch_exists` only for an unlanded, non-open
  blocker.
- `cli.HANDLED` contains `ValueError` (`cli.py:1365-1373`), so a
  `MilestoneBlockersError` reaching the CLI is already `ok: false`, exit 3.

## 1. Purpose

Today `run_board` (`orchestrate.py:2367-2451`) dispatches every open
milestone with the one `--base-branch` (`_run_board_async`'s `dispatch`,
`orchestrate.py:2499-2505`). Nothing calls `milestone_bases` yet, and no
production `branch_exists` exists (`steps/worktree.py:91` `branch_exists` is
a pure matcher over a ref listing, not a `Callable[[str], bool]`).

This card wires 1.1 and 1.2 into the real board run:

1. a git-backed "does this local branch exist" check;
2. `run_board` computes each milestone's base with `milestone_bases`, in the
   refusal order the parent fixes, so `MilestoneBlockersError` is an ordinary
   up-front refusal;
3. `_run_board_async` dispatches each milestone on its own base.

`am resume` already keeps the recorded base (`preflight_milestone`,
`orchestrate.py:1611-1614`); this card adds no new path to it and pins it.

## 2. Behavior

### 2.1 Local branch check: `orchestrate._local_branch_exists`

```python
def _local_branch_exists(root: Path) -> Callable[[str], bool]
```

Returns a closure `exists(branch: str) -> bool` bound to repository `root`.
Each call runs, through `worktree.run_git` read at call time (the seam
`refresh_git`, `orchestrate.py:558-575`, already uses):

```
git -C <root> rev-parse --verify --quiet refs/heads/<branch>
```

- Exit 0 → `True`.
- `worktree.GitError` with `exit_code == 1` → `False` (the ref is absent).
  This is the convention of `steps/integrate.py:205-217` `_ref_exists` and
  `bases.py:105-129`.
- Any other `GitError` (for example exit 128, "not a git repository") →
  propagates unchanged. A broken repository is not "the branch is missing";
  silently answering `False` would make a stacked milestone fall back to
  `--base-branch` without saying so.
- Only `refs/heads/<branch>` counts. A tag, or a remote-tracking ref
  `origin/<branch>`, with the same short name is **not** a local branch
  (parent L45 "exists locally").
- No `worktree.git_lock(root)`: the call is a read-only ref lookup, and a
  lock would add a `LockTimeoutError` path to a refusal check.
- It is a module-level function, looked up as `orchestrate._local_branch_exists`
  at call time by `run_board`, so unit tests replace it with a fake and never
  spawn `git`.

### 2.2 `run_board`: bases among the refusals

New sequence in `run_board` (replacing `orchestrate.py:2420-2445`):

1. Argument checks: unchanged (`max_concurrent < 1`, missing `base_branch`).
2. `root = runs.resolve_repo_dir(repo_dir)`; `all_roots = board.roots(repo_dir=root)`
   — read **once** and kept in a variable.
3. `levels = dag.board_levels(all_roots)`; `milestones` flattened as today.
   A cycle is still `DependencyCycleError` here.
4. `prefixes = board_prefixes(milestones, branch_prefix_of, roots=all_roots)`.
   New consequence: a blank prefix, or a prefix shared with another entry,
   **on a non-open blocker root** is now `ValueError` here (1.2's checks).
5. **New:** `bases = milestone_bases(all_roots, prefixes,
   _local_branch_exists(root), base_branch)`. Two or more candidate blockers
   for one milestone raise `MilestoneBlockersError` with 1.1's message; it
   propagates out of `run_board` untouched.
6. Empty board (no open milestone) → `{"ok": True, "board": True, "levels":
   [...], "milestones": []}`, unchanged. (With no open milestone,
   `milestone_bases` asks nothing and returns `{}`.)
7. `cli.refuse_claimed(root, board_claims(milestones, prefixes))`: unchanged.
   `board_claims` still covers only open `milestones`; a blocker root gets a
   prefix but claims nothing.
8. `asyncio.run(_run_board_async(milestones, prefixes=prefixes, bases=bases, ...))`.
   The shared `base_branch` is no longer passed.

Because step 5 precedes step 7 and step 8, a `MilestoneBlockersError` means
no claim check ran, `_run_milestone_async` was never called, and therefore no
run row, run directory, journal, lease or branch was created (all of those are
made inside `_run_milestone_async` → `preflight_milestone` /
`recorded_milestone_run`). No board card changes status.

`branch_exists` is consulted only for a blocker that is neither open nor
landed (`done`, or in play with nothing open under it); for a board with no
inter-milestone `blocked_by` edges, `git` is never invoked by this step
(Compatibility, parent L100).

**Docstring.** `run_board`'s refusal paragraph (`orchestrate.py:2382-2394`)
gains, between the prefix sentence and the claims sentence: each open
milestone's base from `milestone_bases` (its one open blocker's, or one
unlanded blocker's whose integrate branch exists locally, `<prefix>-integrate`;
else `base_branch`), and two such blockers is `MilestoneBlockersError` before
the claims check. The prefix sentence mentions that non-open blocker roots are
keyed too. The dispatch paragraph says each milestone runs on its own base.

### 2.3 `_run_board_async`: one base per milestone

- Signature: drop the `base_branch: str` keyword, add `bases: Mapping[str, str]`
  (keyword-only, alongside `prefixes`).
- `dispatch(card)` calls `_run_milestone_async(card.id, ..., base_branch=bases[card.id], ...)`.
  Every other forwarded argument is unchanged, and `resume_run_id` is still
  never passed.
- Every dispatched card is in `milestones` (the open set from
  `dag.board_levels`), and `milestone_bases` keys exactly the open roots with
  the same predicate (`dag.milestone_is_open`, `dag.py:212`), so `bases[card.id]`
  always exists. A missing key is a caller bug; it surfaces as that
  milestone's `escalated` entry (`KeyError`) through `dispatch`'s existing
  `except Exception`, and is not specially handled.
- Gating is unchanged: a milestone dispatches only when every open blocker
  finished `done`; otherwise it is reported `blocked`. Since a dispatched
  milestone has at most one stacking blocker, its base branch was created by
  that blocker's own Integrate in this board run (open blocker) or already
  existed (unlanded `done` blocker).
- Docstring: say each milestone is dispatched on its own entry in `bases`.

### 2.4 Payload and CLI

- The `run_board` payload shape is unchanged; no new key. (The per-milestone
  `base_branch` key belongs to the dry-run payload, card 1.4.)
- `cli.py`: no code change. `MilestoneBlockersError` reaches `HANDLED` as a
  `ValueError` and becomes `{"ok": false, "error": {"type":
  "MilestoneBlockersError", "message": <1.1's message>}}`, exit 3.

### 2.5 Resume and relaunch keep the recorded base

- `run_board` never resumes; it never passes `resume_run_id` (pinned already
  at `tests/test_orchestrate.py:7581`).
- `am resume <run-id>` goes through `run_milestone` → `preflight_milestone`,
  which sets `base_branch = resumed.base_branch` (`orchestrate.py:1613`)
  whatever base the caller passed. `milestone_bases` is **not** called on that
  path. No code change; a pinning test is added (§3, test 9).
- A relaunched `am run --board` re-derives bases from the board as it is then.
  A milestone whose earlier run was `escalated`/`stopped` is still open, so it
  is dispatched as a fresh run on the freshly derived base; that is today's
  relaunch behaviour, not a re-root of the recorded run.

## 3. Tests

### 3.1 Fixture change (unit tier)

`tests/test_orchestrate.py`, `BoardSeams` / `board_seams`
(`:7397-7445`):

- `BoardSeams` gains `branches: set[str]` (default empty) and
  `asked: list[tuple[Path, str]]` (default empty).
- `board_seams` monkeypatches `orchestrate._local_branch_exists` with a fake
  that, for `root`, returns `lambda branch: (asked.append((root, branch)),
  branch in seams.branches)[1]` (any equivalent recording closure).

Default "no branch exists" keeps every existing board test subprocess-free
(unit tier forbids subprocess; the stub `git` exits 99, which §2.1 would
propagate as a `GitError`). Existing tests with a `done` blocker
(`test_run_board_treats_an_already_done_blocker_as_satisfied`, `:7693`) keep
their current result: no branch → base `main`.

### 3.2 Existing tests that change

Two existing tests give one milestone **two open blockers**
(`test_run_board_runs_a_two_blocker_milestone_only_after_both_finish_done`,
`:7665`, and `test_run_board_blocks_a_two_blocker_milestone_on_only_its_unclean_blocker`,
`:7680`). Under this card `run_board` refuses that board. Both are retargeted,
not deleted, because `_run_board_async`'s multi-blocker gating is unchanged
code worth keeping covered:

- Each calls `asyncio.run(orchestrate._run_board_async([a, b, c],
  prefixes=..., bases={a.id: "main", b.id: "main", c.id: "main"}, root=...,
  commands=(), allow_no_verification=False, runner_factory=None, driver=None,
  clock=..., max_concurrent=2, control_interval=...))` with the `board_seams`
  fake for `_run_milestone_async`, and asserts what it asserts today about
  order (`c` last, `done`) and about `blocked_by: [b.id]`. Names become
  `test_run_board_async_runs_a_two_blocker_milestone_only_after_both_finish_done`
  and `test_run_board_async_blocks_a_two_blocker_milestone_on_only_its_unclean_blocker`.
  Assertions on `result["levels"]` are dropped (that is `run_board`'s
  payload, not `_run_board_async`'s).

All other existing `run_board` tests stay unchanged and green, including
`test_run_board_runs_every_milestone_on_one_shared_semaphore` (`:7557`),
whose unblocked milestones still get `base_branch == "main"`.

### 3.3 New tests

Unless marked otherwise, tests are **unit tier, unmarked**, in
`tests/test_orchestrate.py` after the `run_board` tests (`~:7760`): they
drive `run_board` through `board_seams` fakes (`board.roots`,
`_run_milestone_async`, `cli.refuse_claimed`, `_local_branch_exists`) and
spawn nothing (CLAUDE.md "Test tiers": "anything driven through an injected
fake"). Use `_board_milestone`, `_prefix_of`, `_board`, `_by_id`,
`integration.integration_branch`. Each must fail before the change.

1. **chain stacks the second on the first's integrate branch** (card test 1):
   `a = _board_milestone(1)`, `b = _board_milestone(2, blocked_by=(1,))`.
   `run_board` dispatches both; the recorded kwargs give `a` →
   `base_branch == "main"`, `b` → `base_branch ==
   integration.integration_branch(_prefix_of(a))` (`"p00000001-integrate"`).
   `board_seams.asked == []` (an open blocker never asks git).
2. **three-milestone chain** A ← B ← C, all open: A on `main`, B on A's
   integrate branch, C on B's.
3. **two open blockers refuse and leave nothing behind** (card test 2):
   `a, b` open, `c = _board_milestone(3, blocked_by=(1, 2))`.
   `pytest.raises(orchestrate.MilestoneBlockersError)`; the message contains
   `c.id`, `a.id`, `b.id` and `"chain them"`; `board_seams.claims == []`
   (the claims check never ran) and `board_seams.runs.calls == []` (no
   milestone dispatched, so no run row, run directory or lease could exist).
4. **an unlanded done blocker with its integrate branch stacks; without it,
   `main`**, parametrized over the branch being in `board_seams.branches` or
   not: `done = _board_milestone(1, status="done", done_children=True)`,
   `later = _board_milestone(2, blocked_by=(1,))`. With the branch, `later`'s
   `base_branch == "p00000001-integrate"`; without, `"main"`. Either way
   `board_seams.asked == [(runs.resolve_repo_dir(board_seams.root),
   "p00000001-integrate")]` — the check is bound to the resolved repository
   and asked once. This also proves `roots=` reaches `board_prefixes` (without
   it, `milestone_bases` raises "has no branch prefix").
5. **a landed blocker is never asked about**, parametrized over `merged`,
   `canceled`, `archived`: blocker with that status and `done_children=True`,
   its branch present in `board_seams.branches` → dependent on `"main"`,
   `board_seams.asked == []`.
6. **an open blocker plus an unlanded blocker with a branch refuses**: `a`
   open, `d = _board_milestone(4, status="done", done_children=True)` with
   `"p00000004-integrate"` in `branches`, `c` blocked by `(1, 4)` →
   `MilestoneBlockersError` whose message also says to mark `d.id` merged;
   `claims == []`, `runs.calls == []`.
7. **refusal order**: a board that has both a blocker cycle and a two-blocker
   milestone raises `DependencyCycleError`, not `MilestoneBlockersError`; a
   board with a shared prefix **and** a two-blocker milestone raises the
   prefix `ValueError` (`match="share the branch prefix"`), not
   `MilestoneBlockersError` (prefixes before bases); and a two-blocker board
   whose `cli.refuse_claimed` fake would raise `ClaimedError` raises
   `MilestoneBlockersError` (bases before claims).
8. **a blocker root's prefix is checked**: a `done` blocker whose
   `branch_prefix_of` result is `""` → `ValueError` matching
   `has no branch prefix`, with `claims == []` and `runs.calls == []`.
9. **resume keeps the recorded base** (parent L69-70): extend
   `_record_resume_run` (`:4455`) with a keyword `base_branch: str = "main"`;
   record `"pstack-integrate"`; `orchestrate.preflight_milestone(None,
   repo_dir=root, base_branch="main", resume_run_id=RESUME_RUN_ID)` (same
   setup as `test_a_resumed_milestone_preflight_leaves_refresh_git_to_the_recorded_stage`,
   `:7959`) → `pre.base_branch == "pstack-integrate"`. Unit tier: sqlite store
   in `tmp_path` and the fake board, no subprocess. It passes immediately; the
   plan labels it a pinning test rather than staging a fake red.

`tests/test_orchestrate.py`, **`@pytest.mark.git`** (real `git` in
`tmp_path`, no `brd`/`claude`; uses the module's `_git` helper, `:913`):

10. **`_local_branch_exists` against a real repository**: in a fresh repo
    with one commit on `main`, branch `m-integrate`, tag `t-integrate`, and a
    ref `refs/remotes/origin/r-integrate` (`git update-ref`):
    `exists("m-integrate") is True`; `exists("main") is True`;
    `exists("absent-integrate") is False`; `exists("t-integrate") is False`;
    `exists("r-integrate") is False`.
11. **a non-answer propagates**: `_local_branch_exists(tmp_path / "not-a-repo")`
    (an existing empty directory outside any repo; set
    `GIT_CEILING_DIRECTORIES` to `tmp_path` via `monkeypatch.setenv` so git
    cannot find an enclosing repo) → calling it raises `worktree.GitError`
    with `exit_code != 1`.

`tests/test_cli.py`, unit tier:

12. Add `orchestrate.MilestoneBlockersError("milestone X is blocked by 2
    milestones that are not landed (...); ... chain them (A <- B <- C)")` to
    the parametrize list of `test_a_handled_error_from_a_board_run_is_an_envelope`
    (`tests/test_cli.py:3898-3916`), id `"MilestoneBlockersError"`: envelope
    `ok: false`, exit 3, `type == "MilestoneBlockersError"`, message verbatim.
    Pins that the subclass stays inside `HANDLED`.

`tests/e2e/test_run_board.py`, **`@pytest.mark.e2e_fake`** (opt-in; the only
tier where the real store, data dir and git exist under production wiring):

13. **two open blockers leave nothing behind, for real**: mirroring
    `test_a_claim_on_a_later_milestone_refuses_the_whole_board_up_front`
    (`:330-352`): milestones A, B open and C blocked by both;
    `pytest.raises(orchestrate.MilestoneBlockersError)` from `_run_board(root,
    a, b, c)`; then `_run_ids(root) == []`, `_run_dirs() == []`,
    `_local_branches(root) == ["main"]`, and every subtask card's status is
    unchanged. No fake `claude` process is started (the refusal comes before
    any dispatch). Mark it explicitly `@pytest.mark.e2e_fake`, like its
    neighbours.

Verification: `uv run pytest` (unit + git tiers) green. Additionally run
`uv run pytest -m e2e_fake tests/e2e/test_run_board.py` once: test 13 is
there, and `test_a_blocked_by_pair_runs_in_order` (`:237`) now dispatches B
on `ba-integrate` and must stay green. There is no lint or typecheck command
(CLAUDE.md).

## 4. Out of scope

- **1.4** (`5bfe746d`): `cli.dry_run_board` (`cli.py:1315-1360`) passing
  `roots=`/computing bases, the per-milestone `base_branch` key and the `base`
  column in the dry-run payload. It keeps the shared base here.
- **1.5** (`d8b6ed12`): the `e2e_fake` stacking scenarios (the second
  milestone's worktree contains the first's commits; three-milestone chain).
  Test 13 above is a refusal test, not a stacking scenario.
- `am run --board --detach` (parent §2) and the README.
- Any change to `milestone_bases`, `_blocker_branch`, `board_prefixes`,
  `board_claims`, `dag.board_levels`, `MilestoneBlockersError`'s message, or
  `preflight_milestone`.
- Any merge into `--base-branch`; any JSON, journal or watch schema change.

## 5. For the planner

**Files:**

- Modify `src/agent_manager/orchestrate.py`:
  - add `_local_branch_exists` near `refresh_git` (`:558`) or just above
    `run_board` (§2.1);
  - `run_board` body and docstring (`:2380-2445`, §2.2);
  - `_run_board_async` signature, `dispatch`, docstring (`:2454-2505`, §2.3).
- Tests: `tests/test_orchestrate.py` (fixture §3.1, retargets §3.2, tests
  1-11), `tests/test_cli.py` (test 12), `tests/e2e/test_run_board.py` (test 13).

**Interfaces:**

- Consumes (unchanged): `board_prefixes(milestones, branch_prefix_of, *, roots=())`,
  `milestone_bases(milestones, prefixes, branch_exists, base_branch) -> dict[str, str]`,
  `MilestoneBlockersError`, `integration.integration_branch(prefix)`,
  `worktree.run_git(argv: list[str]) -> str`, `worktree.GitError.exit_code`.
- Produces: `orchestrate._local_branch_exists(root: Path) -> Callable[[str], bool]`;
  `orchestrate._run_board_async(milestones, *, prefixes: Mapping[str, str],
  bases: Mapping[str, str], root: Path, commands, allow_no_verification,
  runner_factory, driver, clock, max_concurrent, control_interval)` (no
  `base_branch`).

Suggested split: Task 1 `_local_branch_exists` (tests 10-11, git tier);
Task 2 fixture change + `run_board`/`_run_board_async` wiring with the §3.2
retargets and tests 1-8 (strict TDD; the fixture change lands first so the
suite stays subprocess-free); Task 3 pinning tests 9 and 12 and the
`e2e_fake` refusal test 13.

**Review Focus seeds** (failure modes most likely to bite):

1. `milestone_bases` called with the open `milestones` instead of
   `all_roots`: a `done` blocker is then not a root, is ignored, and the
   dependent silently stays on `main` (test 4 with the branch present).
2. `board_prefixes` called without `roots=all_roots`: a `done` blocker with an
   existing integrate branch has no prefix and the board crashes with
   "has no branch prefix" instead of stacking (test 4).
3. Bases computed after `refuse_claimed` or inside `_run_board_async`: a
   refusal then leaves a claim check or a run behind (tests 3, 7, 13).
4. `_local_branch_exists` treating every `GitError` as `False`, or matching a
   tag or remote ref: a broken repo or a same-named tag silently decides the
   base (tests 10-11).
5. `board_seams` not faking the git seam: unit tests that touch a `done`
   blocker would spawn the stub `git` and fail with `GitError` exit 99, or a
   developer "fixes" that by swallowing all `GitError`s (§3.1, finding 4).

---

# Dispatch each board milestone on its own base branch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am run --board` computes each open milestone's base branch with `milestone_bases` (stacking a milestone on its one blocker's `<prefix>-integrate`), refuses a two-blocker milestone up front with `MilestoneBlockersError`, and dispatches each milestone on its own base.

**Architecture:** A new module-level `orchestrate._local_branch_exists(root)` returns a git-backed `Callable[[str], bool]` (`rev-parse --verify --quiet refs/heads/<b>`; exit 1 → `False`, any other `GitError` propagates). `run_board` reads `board.roots()` once, passes it as `roots=` to `board_prefixes` and as the full root list to `milestone_bases`, all before `cli.refuse_claimed`. `_run_board_async` drops its shared `base_branch` keyword for `bases: Mapping[str, str]` and dispatches `bases[card.id]`.

**Tech Stack:** Python 3, pytest (tiers: unmarked unit, `@pytest.mark.git`, `@pytest.mark.e2e_fake`), `uv`.

**Spec:** `docs/superpowers/specs/1-3-dispatch-each-5b772688.md` (prepended above).

## Global Constraints

- `MilestoneBlockersError` is raised in `run_board`'s refusals **after cycle detection and prefix derivation, before the claims check**.
- A refusal leaves **no run row, run directory or lease** (exit 3 with the usual envelope at the CLI).
- `milestone_bases(milestones, prefixes, branch_exists, base_branch)` is consumed unchanged; it gets **every root**, not just the open ones.
- Only `refs/heads/<branch>` counts as "exists locally"; a tag or `refs/remotes/origin/<branch>` does not.
- Boards with no inter-milestone `blocked_by` edges behave exactly as today, and never invoke `git` for bases.
- JSON changes additive only (here: none); journal and watch schema stay 1.
- `am` never merges into or touches `--base-branch`.
- Do not change `milestone_bases`, `_blocker_branch`, `board_prefixes`, `board_claims`, `dag.board_levels`, `MilestoneBlockersError`'s message, `preflight_milestone`, or `cli.py`.
- Unit tier tests spawn no subprocess (stub `git` on `PATH` exits 99). Verification: `uv run pytest`; no lint/typecheck command.

## Review Focus

1. `milestone_bases` handed the open `milestones` instead of `all_roots`: a `done` blocker with its integrate branch is ignored and the dependent silently stays on `main` → Task 2 test `test_run_board_stacks_on_an_unlanded_done_blockers_integrate_branch_only_when_it_exists[present]`.
2. `board_prefixes` called without `roots=all_roots`: a `done` blocker crashes the board with "has no branch prefix" instead of stacking → same Task 2 test (both parametrizations), plus `test_run_board_checks_a_non_open_blocker_roots_prefix_before_any_claim_check`.
3. Bases computed after `refuse_claimed` or inside `_run_board_async`: a refusal leaves a claim check or a run behind → Task 2 `test_run_board_refuses_a_two_open_blocker_milestone_before_any_claim_check`, `test_run_board_refuses_two_blockers_before_the_claims_check`; Task 3 e2e `test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front`.
4. `_local_branch_exists` treating every `GitError` as `False`, or matching a tag/remote ref → Task 1 `test_local_branch_exists_answers_only_for_local_branches` and `test_local_branch_exists_propagates_a_git_error_that_is_not_an_answer`.
5. `board_seams` not faking the git seam: any unit board test with a `done` blocker would spawn the stub `git` (exit 99 `GitError`), tempting someone to swallow all `GitError`s → Task 2 Step 1 fixture change, exercised by `test_run_board_treats_an_already_done_blocker_as_satisfied` and the landed-blocker test asserting `asked == []`.

---

### Task 1: `orchestrate._local_branch_exists`

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (insert just above `def run_board(`, after `milestone_status`, ~line 2366)
- Test: `tests/test_orchestrate.py` (append a new section right after `test_run_board_ends_on_a_base_exception_instead_of_hanging`, before the `# ── run pre-flight, recorded stage and engine seam (card 5daa944e)` banner, ~line 7770)

**Interfaces:**
- Consumes: `worktree.run_git(argv: list[str]) -> str` (read at call time as `worktree.run_git`), `worktree.GitError` with `.exit_code: int | None`.
- Produces: `orchestrate._local_branch_exists(root: Path) -> Callable[[str], bool]`. Task 2 looks it up as `orchestrate._local_branch_exists` at call time and monkeypatches it.

- [ ] **Step 1: Write the failing git-tier tests**

Append to `tests/test_orchestrate.py` after `test_run_board_ends_on_a_base_exception_instead_of_hanging`:

```python
# ── _local_branch_exists (card 5b772688) ────────────────────────────────────


def _branch_repo(tmp_path: Path) -> Path:
    """A repo with one commit on `main`, branch `m-integrate`, tag
    `t-integrate` and remote-tracking ref `origin/r-integrate`."""
    root = tmp_path / "repo"
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
    _git(root, "branch", "m-integrate")
    _git(root, "tag", "t-integrate")
    _git(root, "update-ref", "refs/remotes/origin/r-integrate", "HEAD")
    return root


@pytest.mark.git
def test_local_branch_exists_answers_only_for_local_branches(tmp_path):
    exists = orchestrate._local_branch_exists(_branch_repo(tmp_path))

    assert exists("m-integrate") is True
    assert exists("main") is True
    assert exists("absent-integrate") is False
    assert exists("t-integrate") is False
    assert exists("r-integrate") is False


@pytest.mark.git
def test_local_branch_exists_propagates_a_git_error_that_is_not_an_answer(
    tmp_path, monkeypatch
):
    """A broken repository is not "the branch is missing": exit 128 propagates."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    exists = orchestrate._local_branch_exists(not_a_repo)

    with pytest.raises(worktree.GitError) as caught:
        exists("m-integrate")

    assert caught.value.exit_code != 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k local_branch_exists -v`
Expected: 2 FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute '_local_branch_exists'`.

- [ ] **Step 3: Implement `_local_branch_exists`**

In `src/agent_manager/orchestrate.py`, insert immediately above `def run_board(` (after `milestone_status`'s `return "escalated"`, keeping two blank lines on each side):

```python
def _local_branch_exists(root: Path) -> Callable[[str], bool]:
    """`run_board`'s `branch_exists` for `milestone_bases`: is `<branch>` a local branch of `root`?

    Each call runs `git -C <root> rev-parse --verify --quiet refs/heads/<branch>`
    through `worktree.run_git`, read at call time. Only `refs/heads/` counts: a
    tag or a remote-tracking ref with the same short name is not a local
    branch. Exit 1 is the ref being absent, so `False`; any other `GitError`
    (exit 128, "not a git repository") is not an answer and propagates, so a
    broken repository never silently drops a stacked milestone onto the base
    branch. No `git_lock`: a read-only ref lookup is not worth a
    `LockTimeoutError` path in a refusal check.
    """

    def exists(branch: str) -> bool:
        try:
            worktree.run_git(
                ["-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]
            )
        except worktree.GitError as error:
            if error.exit_code == 1:
                return False
            raise
        return True

    return exists
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k local_branch_exists -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: orchestrate._local_branch_exists asks git for a local branch only (card 5b772688)"
```

---

### Task 2: `run_board` computes bases among its refusals; `_run_board_async` dispatches each on its own

**Files:**
- Modify: `tests/test_orchestrate.py` — `BoardSeams` / `board_seams` (~lines 7425-7445); the two two-blocker tests (~lines 7665-7690); new tests appended after `test_run_board_ends_on_a_base_exception_instead_of_hanging` and before Task 1's `# ── _local_branch_exists` banner
- Modify: `src/agent_manager/orchestrate.py` — `run_board` docstring and body (~lines 2367-2451), `_run_board_async` signature, docstring and `dispatch` (~lines 2454-2505)

**Interfaces:**
- Consumes: `orchestrate._local_branch_exists(root: Path) -> Callable[[str], bool]` (Task 1); unchanged `board_prefixes(milestones, branch_prefix_of, *, roots=())`, `milestone_bases(milestones, prefixes, branch_exists, base_branch) -> dict[str, str]`, `MilestoneBlockersError`, `integration.integration_branch(prefix) -> str` (`f"{prefix}-integrate"`).
- Produces: `orchestrate._run_board_async(milestones, *, prefixes: Mapping[str, str], bases: Mapping[str, str], root: Path, commands, allow_no_verification, runner_factory, driver, clock, max_concurrent, control_interval)` — no `base_branch`. `BoardSeams.branches: set[str]`, `BoardSeams.asked: list[tuple[Path, str]]` (Task 3 does not use them).

- [ ] **Step 1: Fake the git seam in `board_seams` (fixture change, lands first)**

In `tests/test_orchestrate.py`, replace the `BoardSeams` dataclass and `board_seams` fixture with:

```python
@dataclass
class BoardSeams:
    root: Path
    runs: FakeMilestoneRuns
    cards: list[models.CardNode] = field(default_factory=list)
    claims: list[list[str]] = field(default_factory=list)
    branches: set[str] = field(default_factory=set)
    """The local branches `orchestrate._local_branch_exists` reports; none by default."""
    asked: list[tuple[Path, str]] = field(default_factory=list)
    """Every `(root, branch)` the faked `_local_branch_exists` was asked about."""


@pytest.fixture
def board_seams(tmp_path, monkeypatch) -> BoardSeams:
    seams = BoardSeams(root=tmp_path, runs=FakeMilestoneRuns())
    monkeypatch.setattr(board, "roots", lambda *, repo_dir=None: list(seams.cards))
    monkeypatch.setattr(orchestrate, "_run_milestone_async", seams.runs)

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        seams.claims.append(list(keys))

    monkeypatch.setattr(cli, "refuse_claimed", refuse)

    def local_branch_exists(root: Path) -> Callable[[str], bool]:
        def exists(branch: str) -> bool:
            seams.asked.append((root, branch))
            return branch in seams.branches

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", local_branch_exists)
    return seams
```

Also extend the section comment above `FakeMilestoneRuns` (`# \`board.roots\`, \`cli.refuse_claimed\` and \`orchestrate._run_milestone_async\``) to read:

```python
# ── run_board at its seams (card baef4f94) ──────────────────────────────────
#
# `board.roots`, `cli.refuse_claimed`, `orchestrate._run_milestone_async` and
# `orchestrate._local_branch_exists` are replaced, so these exercise
# `run_board`'s own validation, leveling, bases, claim union, tree, isolation
# and payload with no git, brd or harness.
# Production wiring is `tests/e2e/test_run_board.py`'s.
```

- [ ] **Step 2: Run the board tests to confirm the fixture change alone is green**

Run: `uv run pytest tests/test_orchestrate.py -k run_board -v`
Expected: all PASS (nothing calls `_local_branch_exists` yet).

- [ ] **Step 3: Retarget the two two-blocker tests to `_run_board_async`**

Replace `test_run_board_runs_a_two_blocker_milestone_only_after_both_finish_done` and `test_run_board_blocks_a_two_blocker_milestone_on_only_its_unclean_blocker` with:

```python
def _board_async(seams: BoardSeams, milestones: list[models.CardNode]) -> list[dict[str, Any]]:
    """`_run_board_async` straight, every milestone on `main`: its gating, not
    `run_board`'s refusals (which refuse a two-blocker milestone)."""
    return asyncio.run(
        orchestrate._run_board_async(
            milestones,
            prefixes={card.id: _prefix_of(card) for card in milestones},
            bases={card.id: "main" for card in milestones},
            root=seams.root,
            commands=(),
            allow_no_verification=False,
            runner_factory=None,
            driver=None,
            clock=orchestrate._utcnow,
            max_concurrent=2,
            control_interval=control.CONTROL_POLL_SECONDS,
        )
    )


def test_run_board_async_runs_a_two_blocker_milestone_only_after_both_finish_done(
    board_seams,
):
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))

    entries = _board_async(board_seams, [a, b, c])

    assert board_seams.runs.called()[-1] == c.id
    assert {entry["milestone_id"]: entry for entry in entries}[c.id]["status"] == "done"


def test_run_board_async_blocks_a_two_blocker_milestone_on_only_its_unclean_blocker(
    board_seams,
):
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.runs.outcomes[b.id] = {"escalated": True, "run_id": "r"}

    entries = _board_async(board_seams, [a, b, c])

    assert {entry["milestone_id"]: entry for entry in entries}[c.id] == {
        "milestone_id": c.id,
        "status": "blocked",
        "blocked_by": [b.id],
    }
    assert c.id not in board_seams.runs.called()
```

- [ ] **Step 4: Write the new `run_board` base tests**

Append after `test_run_board_ends_on_a_base_exception_instead_of_hanging` (and before Task 1's `# ── _local_branch_exists` banner):

```python
# ── run_board bases (card 5b772688) ─────────────────────────────────────────


def _bases(seams: BoardSeams) -> dict[str, str]:
    """Each dispatched milestone's `base_branch`, keyed by milestone id."""
    return {milestone: kwargs["base_branch"] for milestone, kwargs in seams.runs.calls}


def test_run_board_stacks_a_blocked_milestone_on_its_open_blockers_integrate_branch(
    board_seams,
):
    """Spec test 1: an open blocker's branch is this board run's to create; git is never asked."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]

    result = _board(board_seams)

    assert result["ok"] is True
    assert _bases(board_seams) == {
        a.id: "main",
        b.id: integration.integration_branch(_prefix_of(a)),
    }
    assert _bases(board_seams)[b.id] == "p00000001-integrate"
    assert board_seams.asked == []


def test_run_board_stacks_a_three_milestone_chain_each_on_the_one_before(board_seams):
    """Spec test 2: A <- B <- C, all open."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    board_seams.cards = [c, b, a]  # board order is not chain order

    _board(board_seams)

    assert _bases(board_seams) == {
        a.id: "main",
        b.id: "p00000001-integrate",
        c.id: "p00000002-integrate",
    }
    assert board_seams.runs.called() == [a.id, b.id, c.id]


def test_run_board_refuses_a_two_open_blocker_milestone_before_any_claim_check(board_seams):
    """Spec test 3: nothing was claimed or dispatched, so no run row, run
    directory or lease can exist."""
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.cards = [a, b, c]

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        _board(board_seams)

    message = str(caught.value)
    for card_id in (c.id, a.id, b.id):
        assert card_id in message
    assert "chain them" in message
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


@pytest.mark.parametrize("present", [True, False], ids=["present", "absent"])
def test_run_board_stacks_on_an_unlanded_done_blockers_integrate_branch_only_when_it_exists(
    board_seams, present
):
    """Spec test 4: the check is bound to the resolved repository and asked once.
    Also proves `roots=` reaches `board_prefixes` (else "has no branch prefix")."""
    done = _board_milestone(1, status="done", done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [done, later]
    if present:
        board_seams.branches.add("p00000001-integrate")

    result = _board(board_seams)

    assert result["ok"] is True
    assert result["levels"] == [{"level": 0, "milestones": [later.id]}]
    assert _bases(board_seams) == {later.id: "p00000001-integrate" if present else "main"}
    assert board_seams.asked == [
        (runs.resolve_repo_dir(board_seams.root), "p00000001-integrate")
    ]


@pytest.mark.parametrize("status", ["merged", "canceled", "archived"])
def test_run_board_never_asks_git_about_a_landed_blocker(board_seams, status):
    """Spec test 5: a landed blocker's work is in the base already, branch or not."""
    landed = _board_milestone(1, status=status, done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [landed, later]
    board_seams.branches.add("p00000001-integrate")

    _board(board_seams)

    assert _bases(board_seams) == {later.id: "main"}
    assert board_seams.asked == []


def test_run_board_refuses_an_open_and_an_unlanded_blocker_with_its_branch(board_seams):
    """Spec test 6: the unlanded blocker's branch makes it a second candidate."""
    a = _board_milestone(1)
    d = _board_milestone(4, status="done", done_children=True)
    c = _board_milestone(3, blocked_by=(1, 4))
    board_seams.cards = [a, d, c]
    board_seams.branches.add("p00000004-integrate")

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        _board(board_seams)

    message = str(caught.value)
    assert c.id in message and a.id in message
    assert f"mark {d.id} merged" in message
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_refuses_a_cycle_before_two_blockers(board_seams):
    """Spec test 7, cycle first (a guard: passes before and after the change)."""
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    x = _board_milestone(4, blocked_by=(5,))
    y = _board_milestone(5, blocked_by=(4,))
    board_seams.cards = [a, b, c, x, y]

    with pytest.raises(dag.DependencyCycleError):
        _board(board_seams)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_refuses_a_shared_prefix_before_two_blockers(board_seams):
    """Spec test 7, prefixes before bases (a guard: passes before and after the change)."""
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.cards = [a, b, c]

    with pytest.raises(ValueError, match="share the branch prefix") as caught:
        _board(board_seams, branch_prefix_of=lambda card: "same")

    assert not isinstance(caught.value, orchestrate.MilestoneBlockersError)
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_refuses_two_blockers_before_the_claims_check(board_seams, monkeypatch):
    """Spec test 7, bases before claims: the claim refusal is never reached."""
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.cards = [a, b, c]

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        raise cli.ClaimedError("held elsewhere", key=keys[-1], run_id=OTHER_RUN_ID)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)

    with pytest.raises(orchestrate.MilestoneBlockersError):
        _board(board_seams)

    assert board_seams.runs.calls == []


def test_run_board_checks_a_non_open_blocker_roots_prefix_before_any_claim_check(
    board_seams,
):
    """Spec test 8: 1.2's prefix check now covers a `done` blocker root too."""
    done = _board_milestone(1, status="done", done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [done, later]

    def prefix_of(card: models.CardNode) -> str:
        return "" if card.id == done.id else _prefix_of(card)

    with pytest.raises(ValueError, match="has no branch prefix"):
        _board(board_seams, branch_prefix_of=prefix_of)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []
```

- [ ] **Step 5: Run them to verify the right ones fail**

Run: `uv run pytest tests/test_orchestrate.py -k "run_board" -v`
Expected FAIL:
- both `test_run_board_async_*` — `TypeError: _run_board_async() got an unexpected keyword argument 'bases'`;
- `..._on_its_open_blockers_integrate_branch` and `..._three_milestone_chain_...` — base is `"main"`, not `p0000000N-integrate`;
- `..._two_open_blocker_milestone_before_any_claim_check`, `..._open_and_an_unlanded_blocker_...`, `..._two_blockers_before_the_claims_check` — `DID NOT RAISE MilestoneBlockersError` (or `ClaimedError` raised instead);
- `..._unlanded_done_blockers_integrate_branch_...[present]` and `[absent]` — `asked == []` / base `"main"`;
- `..._non_open_blocker_roots_prefix_...` — `DID NOT RAISE ValueError`.
Expected PASS already (guards, by design): `..._never_asks_git_about_a_landed_blocker[*]`, `..._cycle_before_two_blockers`, `..._shared_prefix_before_two_blockers`, and every pre-existing `run_board` test.

- [ ] **Step 6: Wire `run_board`**

In `src/agent_manager/orchestrate.py`, replace `run_board`'s docstring and body from `"""Drive every open milestone on the board as one grafo tree, and report.` through its `return {...}` with:

```python
    """Drive every open milestone on the board as one grafo tree, and report.

    Refusals come first, in this order, and each leaves nothing behind. Bad
    arguments are `ValueError` before the board is read: `max_concurrent < 1`
    and a missing `base_branch`, as `run_milestone` refuses them. Then come the
    open milestones: `board.roots()`, read once, leveled by `dag.board_levels`,
    so a done milestone with nothing open under it drops out and a blocker
    cycle is `DependencyCycleError`. Next, each milestone's prefix from
    `branch_prefix_of`, and each non-open blocker root's too (`board_prefixes`
    with `roots=`: blank or shared is `ValueError`). Next, each open
    milestone's base (`milestone_bases` over every root): its one open
    blocker's `<prefix>-integrate`, or its one unlanded blocker's when that
    branch exists locally (`_local_branch_exists`), else `base_branch`; two
    such blockers is `MilestoneBlockersError`, before the claims check. Last,
    one `cli.refuse_claimed` over every open milestone's `milestone_claims`,
    unioned in level order (`board_claims`): a key another live run holds is
    `ClaimedError` before any milestone starts, so there is no run row, run
    directory or lease for any of them. Each milestone's own pre-flight inside
    `_run_milestone_async` still runs and catches a claim taken after this one.

    Then one `asyncio.run` covers the whole board with one
    `asyncio.Semaphore(max_concurrent)` that every milestone's lanes share
    (`_run_board_async`), each milestone on its own base. A milestone runs
    once every open blocker finished `done`. A milestone whose blocker did
    not finish `done` is never dispatched and is reported `blocked`. A
    milestone that raises is reported `escalated` with `"<Type>: <msg>"` and
    never disturbs its siblings.

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
    all_roots = board.roots(repo_dir=root)
    levels = dag.board_levels(all_roots)
    milestones = [card for level in levels for card in level]
    prefixes = board_prefixes(milestones, branch_prefix_of, roots=all_roots)
    bases = milestone_bases(all_roots, prefixes, _local_branch_exists(root), base_branch)
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
            bases=bases,
            root=root,
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
```

- [ ] **Step 7: Rewire `_run_board_async`**

In the same file, change `_run_board_async`'s signature from

```python
    prefixes: Mapping[str, str],
    root: Path,
    base_branch: str,
    commands: Sequence[str],
```

to

```python
    prefixes: Mapping[str, str],
    bases: Mapping[str, str],
    root: Path,
    commands: Sequence[str],
```

Change the first paragraph of its docstring from

```python
    """`run_board`'s one event loop: one entry per milestone, in `milestones` order.

    The tree comes from `build_dag_tree`, with each milestone's blockers being
```

to

```python
    """`run_board`'s one event loop: one entry per milestone, in `milestones` order.

    Each milestone is dispatched on its own entry in `bases` (`milestone_bases`'
    answer), never on one shared base; `bases` keys every open milestone, so a
    missing key is a caller bug and surfaces as that milestone's `escalated`
    entry. The tree comes from `build_dag_tree`, with each milestone's blockers being
```

And in `dispatch`, change `base_branch=base_branch,` to:

```python
                    base_branch=bases[card.id],
```

- [ ] **Step 8: Run the board tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "run_board or local_branch_exists" -v`
Expected: all PASS (including the unchanged `test_run_board_runs_every_milestone_on_one_shared_semaphore`, still asserting `base_branch == "main"`, and `test_run_board_treats_an_already_done_blocker_as_satisfied`).

- [ ] **Step 9: Run the default suite**

Run: `uv run pytest`
Expected: all PASS, no new failures.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: run_board stacks each milestone on its own base and refuses two blockers up front (card 5b772688)"
```

---

### Task 3: Pin resume, the CLI envelope, and the production refusal

**Files:**
- Modify: `tests/test_orchestrate.py` — `_record_resume_run` (~line 4455); new test right after `test_a_resumed_milestone_preflight_leaves_refresh_git_to_the_recorded_stage` (~line 7985)
- Modify: `tests/test_cli.py` — parametrize list of `test_a_handled_error_from_a_board_run_is_an_envelope` (~lines 3898-3906)
- Modify: `tests/e2e/test_run_board.py` — new test right after `test_a_claim_on_a_later_milestone_refuses_the_whole_board_up_front` (before `test_no_rendezvous_is_left_armed_for_later_tests`, which must stay last)

**Interfaces:**
- Consumes: `orchestrate.preflight_milestone(milestone, *, repo_dir, base_branch=None, ..., resume_run_id=None)` (unchanged); `orchestrate.MilestoneBlockersError`; Task 2's `run_board` refusal.
- Produces: `_record_resume_run(root, run_id=RESUME_RUN_ID, *, workflow="milestone", status="escalated", base_branch="main")`.

These are pinning tests: the behaviour already exists (resume, `HANDLED`) or was delivered in Task 2 (e2e refusal), so each passes on first run. A RED is not staged; to confirm each test bites, the engineer may temporarily break the pinned line (noted per step) and see it fail, then revert.

- [ ] **Step 1: Let `_record_resume_run` record any base**

Replace `_record_resume_run` with:

```python
def _record_resume_run(
    root: Path,
    run_id: str = RESUME_RUN_ID,
    *,
    workflow: str = "milestone",
    status: str = "escalated",
    base_branch: str = "main",
) -> None:
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=root,
                base_branch=base_branch,
                branch_prefix=PREFIX,
                status=status,
                config=models.RunConfig(max_concurrent_stories=3),
            )
        )
    finally:
        opened.close()
```

- [ ] **Step 2: Write the resume pinning test**

Add right after `test_a_resumed_milestone_preflight_leaves_refresh_git_to_the_recorded_stage`:

```python
def test_a_resumed_milestone_keeps_its_recorded_stacked_base(tmp_path, monkeypatch, fake_board):
    """Card 5b772688 (parent L69-70): `am resume` keeps the base the run was
    stacked on, whatever base the caller passes; `milestone_bases` is not consulted."""
    root = _resume_root(tmp_path, monkeypatch)
    _seam_resume_board(fake_board)
    _record_resume_run(root, base_branch="pstack-integrate")
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    pre = orchestrate.preflight_milestone(
        None, repo_dir=root, base_branch="main", resume_run_id=RESUME_RUN_ID
    )

    assert pre.base_branch == "pstack-integrate"
```

- [ ] **Step 3: Add `MilestoneBlockersError` to the CLI envelope parametrize**

In `tests/test_cli.py`, replace the parametrize of `test_a_handled_error_from_a_board_run_is_an_envelope` with:

```python
@pytest.mark.parametrize(
    "error",
    [
        ValueError("max_concurrent must be at least 1, got 0"),
        dag.DependencyCycleError("dag: dependency cycle among milestones #a, #b"),
        board.BoardError("brd refused", argv=["brd", "tree"]),
        cli.CliError("run 20261001T000000Z-00000001 already claims branch:m-integrate"),
        orchestrate.MilestoneBlockersError(
            "milestone X is blocked by 2 milestones that are not landed (A, B); "
            "a milestone stacks on at most one: chain them (A <- B <- C)"
        ),
    ],
    ids=["ValueError", "DependencyCycleError", "BoardError", "CliError", "MilestoneBlockersError"],
)
```

(`orchestrate` is already imported in `tests/test_cli.py`'s `from agent_manager import (...)` block, line 51.)

- [ ] **Step 4: Run the two unit pins**

Run: `uv run pytest tests/test_orchestrate.py::test_a_resumed_milestone_keeps_its_recorded_stacked_base "tests/test_cli.py::test_a_handled_error_from_a_board_run_is_an_envelope" -v`
Expected: all PASS (6 items). Optional bite check: temporarily delete `base_branch = resumed.base_branch` in `preflight_milestone` → the resume test fails with `'main' == 'pstack-integrate'`; revert.

- [ ] **Step 5: Write the e2e_fake refusal test**

In `tests/e2e/test_run_board.py`, insert right after `test_a_claim_on_a_later_milestone_refuses_the_whole_board_up_front`:

```python
@pytest.mark.e2e_fake
def test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front(board_root):
    """Card 5b772688: `MilestoneBlockersError` comes before the claims check
    and any dispatch, so nothing is left behind and no fake claude starts."""
    root = board_root
    a = _milestone(root, "A", "ba")
    b = _milestone(root, "B", "bb")
    c = _milestone(root, "C", "bc", blocked_by=(a["id"], b["id"]))
    subtasks = (a["subtask"], b["subtask"], c["subtask"])
    statuses_before = {card: board.show(card, repo_dir=root).status for card in subtasks}

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        _run_board(root, a, b, c)

    assert c["id"] in str(caught.value)
    assert "chain them" in str(caught.value)
    assert _run_ids(root) == []
    assert _run_dirs() == []
    assert _local_branches(root) == ["main"]
    assert {card: board.show(card, repo_dir=root).status for card in subtasks} == statuses_before
```

- [ ] **Step 6: Run the e2e_fake board module**

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py -v`
Expected: all PASS, including the new test and `test_a_blocked_by_pair_runs_in_order` (B now dispatches on `ba-integrate`). Optional bite check: temporarily move the `bases = milestone_bases(...)` line in `run_board` to after `cli.refuse_claimed(...)` — the new test still raises but the unit test `test_run_board_refuses_two_blockers_before_the_claims_check` fails; revert.

- [ ] **Step 7: Run the default suite**

Run: `uv run pytest`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/test_orchestrate.py tests/test_cli.py tests/e2e/test_run_board.py
git commit -m "test: pin resume's recorded base, the MilestoneBlockersError envelope and the e2e refusal (card 5b772688)"
```
<!-- task-pipeline: validated -->
