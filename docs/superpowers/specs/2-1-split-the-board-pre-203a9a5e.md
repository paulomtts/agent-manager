# 2.1 Split the board pre-flight from the board run — spec

Card: `203a9a5e` (subtask of story `22561886`).
Parent design: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`
(cited below as **D**, by section and line).

## Purpose

D §2 (lines 76-96) has `am run --board --detach` run the board's **whole
pre-flight in the foreground** (lines 78-82): "argument checks, board read,
cycle check, prefix derivation, `MilestoneBlockersError`, and the up-front claim
check over every milestone". Any refusal there is the usual envelope with nothing
started. Only then does a detached child own the board run (lines 83-86). The
foreground also needs the board's `levels` for its envelope (line 88).

Today `orchestrate.run_board` (`src/agent_manager/orchestrate.py`, def at line
2394, body 2455-2486) does all of that inline and then calls
`asyncio.run(_run_board_async(...))`. No caller can stop between "pre-flight
passed" and "execute the board". This card splits `run_board` into two stages,
as card `5daa944e` split the milestone run (`MilestonePreflight` /
`preflight_milestone` at orchestrate.py 1559-1660, with `run_milestone` as the
thin wrapper):

1. `preflight_board(...) -> BoardPreflight`: every argument check, read and
   refusal, in today's order. It returns the state the execute stage needs.
2. `run_board_engine(pre, ...) -> dict`: runs the board from a `BoardPreflight`
   and returns today's payload.

`run_board` becomes `preflight_board` followed by `run_board_engine`. It keeps
its signature, docstring contract, refusal order and payload.

This is a **behaviour-preserving refactor**. Every existing test stays as it is
and must pass under `uv run pytest`: `tests/test_orchestrate.py`,
`tests/test_cli.py` (which patches `orchestrate.run_board`) and
`tests/e2e/test_run_board.py`. New tests cover the new seam only.

## Inherited constraints

- The pre-flight is argument checks, then the board read, the cycle check,
  prefix derivation, `MilestoneBlockersError`, and the up-front claim check over
  every milestone. A refusal starts nothing (D §2, lines 78-82).
- `MilestoneBlockersError` is raised after cycle detection and prefix
  derivation and before the claims check. It leaves no run row, run directory or
  lease (D §1, lines 52-56).
- Claims taken after the up-front check are still handled inside the run, as
  `escalated` entries (D lines 94-96). This card must not change that: each
  milestone's own pre-flight in `_run_milestone_async` stays.
- No board-level run record or run id (D §Non-goals, line 33). `BoardPreflight`
  holds no run id and writes nothing.
- All JSON changes are additive, and the journal and watch schema stay 1 (D
  §Compatibility, line 102; card text). This card adds **no** JSON keys and no
  CLI surface.
- `am` never touches `--base-branch` (D line 30). The refactor adds no git
  writes.
- Tiers come from CLAUDE.md "Test tiers". An unmarked (`unit`) test uses only
  injected fakes and spawns no subprocess, at ≤0.5s per test. Tests mirror
  `src` under `tests/`.

## Required behaviour

### `BoardPreflight` (new, `orchestrate.py`)

A `@dataclass(frozen=True)` that is internal state, so a dataclass (CLAUDE.md
Conventions). It goes in a new section right after `_local_branch_exists` and
before `run_board`, headed `# ── the two stages of a board run (card 203a9a5e) ──`.
Fields:

| field | type | meaning |
|---|---|---|
| `root` | `Path` | `runs.resolve_repo_dir(repo_dir)` |
| `base_branch` | `str` | the validated `--base-branch` |
| `max_concurrent` | `int` | the validated lane bound (≥ 1) |
| `levels` | `list[list[models.CardNode]]` | `dag.board_levels(all_roots)` |
| `milestones` | `list[models.CardNode]` | the open milestones in level order (the levels flattened) |
| `prefixes` | `dict[str, str]` | `board_prefixes(milestones, branch_prefix_of, roots=all_roots)` |
| `bases` | `dict[str, str]` | `milestone_bases(all_roots, prefixes, _local_branch_exists(root), base_branch)` |
| `levels_payload` | `list[dict[str, Any]]` | `[{"level": i, "milestones": [ids]}, ...]`, exactly today's `levels` value |

### `preflight_board(*, repo_dir, base_branch, branch_prefix_of, max_concurrent=1) -> BoardPreflight`

It runs these steps in this order, which is today's order (orchestrate.py
2455-2470). Each refusal propagates unchanged and leaves nothing behind:

1. `max_concurrent < 1` raises `ValueError("max_concurrent must be at least 1, got N")`.
   This happens before the board is read.
2. A falsy `base_branch` raises `ValueError("a board run needs a base branch")`.
   This also happens before the board is read.
3. `root = runs.resolve_repo_dir(repo_dir)`, then `board.roots(repo_dir=root)`
   is read **once**.
4. `dag.board_levels` runs. A blocker cycle raises `dag.DependencyCycleError`.
5. `board_prefixes(..., roots=all_roots)` runs. A blank or shared prefix raises
   `ValueError`.
6. `milestone_bases(...)` runs. Two stackable blockers raise
   `MilestoneBlockersError`. A `worktree.GitError` from `_local_branch_exists`
   other than exit 1 propagates.
7. **Only when `milestones` is non-empty**, `cli.refuse_claimed(root,
   board_claims(milestones, prefixes))` is called once. A live conflict raises
   `cli.ClaimedError`. On an empty board it is not called, which is today's
   behaviour: the empty-board return at line 2469 comes before the claim check.

It never calls `asyncio.run`, never opens a store, never calls
`_run_board_async` or `_run_milestone_async`, and makes no git or board writes.
It looks up the module globals `board.roots`, `cli.refuse_claimed` and
`_local_branch_exists` **at call time**, as today, because the existing
`board_seams` fixture patches them.

### `run_board_engine(pre, *, commands=(), allow_no_verification=False, runner_factory=None, driver=None, clock=_utcnow, control_interval=control.CONTROL_POLL_SECONDS) -> dict[str, Any]`

- If `pre.milestones` is empty, it returns
  `{"ok": True, "board": True, "levels": pre.levels_payload, "milestones": []}`
  without starting an event loop or calling `_run_board_async`.
- Otherwise it returns today's payload,
  `{"ok": all(status == "done"), "board": True, "levels": pre.levels_payload, "milestones": entries}`,
  where `entries` comes from a single
  `asyncio.run(_run_board_async(pre.milestones, prefixes=pre.prefixes, bases=pre.bases, root=pre.root, ..., max_concurrent=pre.max_concurrent, ...))`.
- It does **not** re-read the board, re-derive prefixes or bases, or re-run the
  up-front claim check. The pre-flight is the caller's, and `pre` is its only
  input. The per-milestone pre-flight inside `_run_milestone_async` is
  unchanged, so a claim taken after `preflight_board` is still an `escalated`
  entry.
- It is synchronous, like the milestone `engine` closure in `detach_milestone`
  (orchestrate.py ~2170). The later detach card calls it inside the child.
- It does not mutate `pre`.

### `run_board` (unchanged contract)

Same keyword-only signature, defaults and return value. Its body becomes:

```python
pre = preflight_board(
    repo_dir=repo_dir,
    base_branch=base_branch,
    branch_prefix_of=branch_prefix_of,
    max_concurrent=max_concurrent,
)
return run_board_engine(
    pre,
    commands=commands,
    allow_no_verification=allow_no_verification,
    runner_factory=runner_factory,
    driver=driver,
    clock=clock,
    control_interval=control_interval,
)
```

Its docstring keeps every sentence of the refusal and payload contract. It may
add one sentence saying that it composes `preflight_board` and
`run_board_engine` (card 203a9a5e). `cli.py` still calls `orchestrate.run_board`
(cli.py ~1763). `cli.dry_run_board` (cli.py 1315) is **not** changed.

## Tests

All new tests go in `tests/test_orchestrate.py`, in a new section headed
`# ── board pre-flight / engine seam (card 203a9a5e) ──`, placed right after the
`run_board bases (card 5b772688)` section and before `_local_branch_exists`.
They reuse the existing `board_seams` fixture, `BoardSeams`,
`FakeMilestoneRuns`, `_board_milestone`, `_prefix_of`, `_board` and `OTHER_RUN_ID`.

Every test below is **unmarked (`unit` tier)**. `board_seams` replaces
`board.roots`, `cli.refuse_claimed`, `_run_milestone_async` and
`_local_branch_exists` with in-process fakes, so no `git`, `brd` or `claude`
process runs. That is the definition of the unit tier in CLAUDE.md. They must
not carry `@pytest.mark.git`. Add one helper,
`_preflight(seams, **overrides) -> orchestrate.BoardPreflight`, which mirrors
`_board`'s defaults (`repo_dir=seams.root`, `base_branch="main"`,
`branch_prefix_of=_prefix_of`, `max_concurrent=2`).

1. **`test_preflight_board_returns_the_state_the_engine_runs_on`.** Board: `a`
   done with done children (a non-open blocker root), `b` open and blocked by
   `a` with `p00000001-integrate` in `seams.branches`, `c` open and blocked by
   `b`, given in the order `[c, b, a]`. Assert `pre.root ==
   runs.resolve_repo_dir(seams.root)`, `pre.base_branch == "main"` and
   `pre.max_concurrent == 2`. Assert `[ids of pre.milestones] == [b, c]` (level
   order) and that `pre.levels` matches it. Assert `pre.prefixes` keys `b`, `c`
   and `a`, `pre.bases == {b: "p00000001-integrate", c: "p00000002-integrate"}`,
   and `pre.levels_payload == [{"level": 0, "milestones": [b]}, {"level": 1, "milestones": [c]}]`.
   Assert `seams.claims` holds exactly one call, equal to
   `orchestrate.board_claims(pre.milestones, pre.prefixes)`, and that
   `seams.runs.calls == []` (nothing dispatched).
2. **`test_preflight_board_refuses_bad_arguments_before_it_reads_the_board`**,
   parametrized over `max_concurrent=0`, `base_branch=None` and
   `base_branch=""`. `board.roots` is patched to `pytest.fail`. The test expects
   `ValueError`, `claims == []` and `runs.calls == []`.
3. **`test_preflight_board_refuses_each_board_problem_before_the_claim_check`**,
   parametrized over three cases: a cycle (`DependencyCycleError`), a shared
   prefix (`ValueError`, match `"branch prefix"`) and a two-open-blocker
   milestone (`MilestoneBlockersError`). Each case expects its error from
   `preflight_board` alone, with `claims == []` and `runs.calls == []`.
4. **`test_preflight_board_lets_a_claim_conflict_propagate_with_nothing_started`.**
   `cli.refuse_claimed` is patched to raise `cli.ClaimedError(..., run_id=OTHER_RUN_ID)`.
   The test expects `ClaimedError` and `runs.calls == []`.
5. **`test_preflight_board_lets_a_git_failure_from_the_branch_check_propagate`.**
   `_local_branch_exists` is patched so its `exists` raises
   `worktree.GitError("not a git repository", argv=["git"], exit_code=128)`
   (`agent_manager.steps.worktree`). The board has a done (unlanded) blocker
   and an open dependent, so `exists` is asked. The test expects `GitError` and
   `claims == []`. This is a Review Focus item: a broken repository must never
   pass the pre-flight silently.
6. **`test_preflight_board_on_a_board_with_nothing_open_skips_the_claim_check`.**
   One done milestone with done children. The test expects
   `pre.milestones == []`, `pre.levels == []`, `pre.levels_payload == []`,
   `pre.bases == {}` and `claims == []`.
7. **`test_preflight_board_never_starts_an_event_loop`.** `asyncio.run` (as seen
   by `orchestrate`, i.e. `monkeypatch.setattr(orchestrate.asyncio, "run", fail)`,
   undone by monkeypatch) is patched to fail, and so is
   `orchestrate._run_board_async`. `preflight_board` on a two-milestone board
   returns normally.
8. **`test_run_board_engine_runs_a_preflight_without_reading_the_board_again`.**
   The test builds `pre` with `_preflight` on `a` and `b` (blocked by `a`). It
   then patches `board.roots` to `pytest.fail` and resets `seams.claims = []`.
   `run_board_engine(pre)` is expected to return `ok: True`, with
   `levels == pre.levels_payload` and entries in the order `[a, b]`. `b` must be
   dispatched with `base_branch="p00000001-integrate"`, and `seams.claims == []`
   (no second up-front claim check).
9. **`test_run_board_engine_gives_the_same_payload_run_board_gives`.** On one
   board (an outcome map where `a` escalates and `b` is blocked by `a`), the test
   compares `run_board_engine(_preflight(seams))` with `_board(seams)`, after
   resetting `seams.runs` between the two. The payloads must be equal (`ok`
   false, `b` reported `blocked`).
10. **`test_run_board_engine_uses_the_preflights_lane_bound_and_forwards_run_arguments`.**
    Build `pre` with `max_concurrent=3`, then call `run_board_engine(pre,
    commands=("git status",), allow_no_verification=True, runner_factory=sentinel_factory,
    driver=sentinel_driver, clock=sentinel_clock, control_interval=0.25)`. Each
    `_run_milestone_async` call must receive `max_concurrent=3`,
    `repo_dir=pre.root`, `branch_prefix=pre.prefixes[id]`,
    `base_branch=pre.bases[id]` and the forwarded values. All milestones share
    one `slots` object.
11. **`test_run_board_engine_on_an_empty_preflight_starts_no_event_loop`.** `pre`
    comes from a board with nothing open. Then `orchestrate._run_board_async` is
    patched to fail. The result must equal
    `{"ok": True, "board": True, "levels": [], "milestones": []}`.

No test from the existing suite is edited, moved or deleted. The existing
`run_board` tests (orchestrate 7402-7980), the `tests/test_cli.py` board tests
and `tests/e2e/test_run_board.py` together prove that `run_board`'s observable
behaviour is unchanged.

No `git`, `brd`, `e2e_fake`, `soak` or `e2e` test is added. The seam is pure
composition over injected fakes, and production wiring is already pinned by
`tests/e2e/test_run_board.py`.

## Review Focus (for the planner)

1. **Empty-board ordering.** The claim check must stay skipped when nothing is
   open, and the engine must not start a loop for it. Tests 6 and 11 cover this.
2. **Call-time lookup.** `preflight_board` must look up `board.roots`,
   `cli.refuse_claimed` and `_local_branch_exists` as module attributes at call
   time, not bind them at import or as default arguments. Otherwise
   `board_seams` silently stops faking them, and the existing suite would reach
   real git and the store. Tests 1-5 cover this.
3. **No double pre-flight in the engine.** Re-reading the board in the engine
   would let a detached child see a board that differs from the one the
   foreground approved. Test 8 covers this.
4. **`GitError` propagation.** A non-exit-1 git failure in the branch check is a
   refusal of the pre-flight, never a silent fallback to `base_branch`. Test 5
   covers this.
5. **`levels_payload` identity.** The engine's `levels` must be the
   pre-flight's, so a later detached envelope (D line 88) and the child's report
   agree. Tests 8 and 9 cover this.

## Out of scope

- `am run --board --detach` itself: the CLI flag, the `<data dir>/boards/` log
  and report files, the child process and the envelope (D §2, lines 83-93).
  Those belong to later sibling cards of story `22561886`. This card only makes
  the seam they will use.
- Any change to `cli.dry_run_board`, which keeps mirroring the refusals on its
  own, or to `cli.py`'s `run_board` call.
- Any change to `_run_board_async`, `_run_milestone_async`, `milestone_bases`,
  `board_prefixes`, `board_claims` or `milestone_status`.
- A board-level run record or run id (D line 33).
- README changes: there is no user-visible change.
