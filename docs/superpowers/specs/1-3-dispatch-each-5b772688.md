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
