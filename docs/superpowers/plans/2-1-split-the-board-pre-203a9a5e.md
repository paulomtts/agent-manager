# 2.1 Split the board pre-flight from the board run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `orchestrate.run_board` into `preflight_board` (every check, read and refusal, returning a frozen `BoardPreflight`) and `run_board_engine` (runs the board from a `BoardPreflight`). `run_board` becomes the composition of the two, and its behaviour does not change.

**Architecture:** This is a pure refactor inside `src/agent_manager/orchestrate.py`, modelled on the milestone split (`MilestonePreflight` / `preflight_milestone`, card 5daa944e). Task 1 extracts the pre-flight stage and its dataclass, and `run_board` calls it. Task 2 extracts the engine stage, and `run_board` becomes two calls. New unit tests drive both stages through the existing `board_seams` fixture.

**Tech Stack:** Python 3, `dataclasses`, `asyncio`, pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2-1-split-the-board-pre-203a9a5e.md`. Its full text is prepended below, and executors read both.

---

## The spec (prepended verbatim)

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

---

## Global Constraints

- A behaviour-preserving refactor. No existing test in `tests/test_orchestrate.py`, `tests/test_cli.py` or `tests/e2e/test_run_board.py` is edited, moved or deleted, and all of them must pass under `uv run pytest`.
- No new JSON keys, no CLI surface, and the journal and watch schema stay 1.
- No board-level run record or run id. `BoardPreflight` holds no run id and writes nothing.
- `am` never touches `--base-branch`. The refactor adds no git writes.
- `cli.py` still calls `orchestrate.run_board`. `cli.dry_run_board` is not changed.
- `_run_board_async`, `_run_milestone_async`, `milestone_bases`, `board_prefixes`, `board_claims` and `milestone_status` are not changed.
- `preflight_board` looks up `board.roots`, `cli.refuse_claimed` and `_local_branch_exists` as module attributes **at call time**. Never bind them at import time or as default arguments.
- Every new test is unmarked (`unit` tier). No `@pytest.mark.git`, no subprocess, ≤0.5s per test.
- New code sits in a section headed `# ── the two stages of a board run (card 203a9a5e) ──`, between `_local_branch_exists` and `run_board`. New tests sit in a section headed `# ── board pre-flight / engine seam (card 203a9a5e) ──`, between the `run_board bases (card 5b772688)` section and the `_local_branch_exists (card 5b772688)` section.

## Review Focus

1. **Empty-board ordering.** On a board with nothing open, the claim check stays skipped and the engine starts no event loop. Pinned by test 6 (Task 1) and test 11 (Task 2).
2. **Call-time lookup.** `board.roots`, `cli.refuse_claimed` and `_local_branch_exists` are looked up when `preflight_board` runs, so `board_seams` really fakes them. Pinned by tests 1-5 (Task 1), each of which observes a patched seam.
3. **No double pre-flight in the engine.** The engine must not re-read the board or re-run the up-front claim check, or a detached child could run a board the foreground never approved. Pinned by test 8 (Task 2).
4. **`GitError` propagation.** A git failure other than exit 1 in the branch check is a pre-flight refusal, never a silent fallback to `base_branch`. Pinned by test 5 (Task 1).
5. **`levels_payload` identity.** The engine's `levels` is the pre-flight's, and the engine's payload equals `run_board`'s. Pinned by tests 8 and 9 (Task 2).

---

### Task 1: `BoardPreflight` and `preflight_board`

**Files:**
- Modify: `src/agent_manager/orchestrate.py`. Insert the new section between the end of `_local_branch_exists` (the `return exists` line, ~2391) and `def run_board(` (~2394), then replace the first part of `run_board`'s body (~2455-2470).
- Test: `tests/test_orchestrate.py`. Insert immediately above the line `# ── _local_branch_exists (card 5b772688) ────────────────────────────────────` (~7980).

**Interfaces:**
- Consumes (existing, unchanged): `runs.resolve_repo_dir(Path) -> Path`, `board.roots(*, repo_dir) -> list[models.CardNode]`, `dag.board_levels(list[CardNode]) -> list[list[CardNode]]`, `board_prefixes(milestones, branch_prefix_of, *, roots=()) -> dict[str, str]`, `milestone_bases(roots, prefixes, branch_exists, base_branch) -> dict[str, str]`, `_local_branch_exists(root) -> Callable[[str], bool]`, `board_claims(milestones, prefixes) -> list[str]`, `cli.refuse_claimed(root, keys)`.
- Produces:
  - `orchestrate.BoardPreflight`, a frozen dataclass with fields `root: Path`, `base_branch: str`, `max_concurrent: int`, `levels: list[list[models.CardNode]]`, `milestones: list[models.CardNode]`, `prefixes: dict[str, str]`, `bases: dict[str, str]`, `levels_payload: list[dict[str, Any]]`.
  - `orchestrate.preflight_board(*, repo_dir: Path, base_branch: str | None, branch_prefix_of: Callable[[models.CardNode], str], max_concurrent: int = 1) -> BoardPreflight`.
  - Test helper `_preflight(seams: BoardSeams, **overrides: Any) -> "orchestrate.BoardPreflight"` in `tests/test_orchestrate.py`, which Task 2's tests also use.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, insert this block immediately above the line `# ── _local_branch_exists (card 5b772688) ────────────────────────────────────`. The file already has every name it uses: `asyncio`, `pytest`, `Any`, `Path`, `board`, `cli`, `dag`, `models`, `orchestrate`, `runs`, `worktree`, `BoardSeams`, `board_seams`, `_board_milestone`, `_prefix_of` and `OTHER_RUN_ID`.

```python
# ── board pre-flight / engine seam (card 203a9a5e) ──────────────────────────
#
# `preflight_board` and `run_board_engine`, the two stages `run_board` now
# composes, driven through the same `board_seams` fakes: no git, brd or harness.


def _preflight(seams: BoardSeams, **overrides: Any) -> "orchestrate.BoardPreflight":
    """`orchestrate.preflight_board` with `_board`'s defaults.

    The return annotation is a string: this file has no `from __future__ import
    annotations`, and a bare one would fail at import before the class exists."""
    kwargs: dict[str, Any] = {
        "repo_dir": seams.root,
        "base_branch": "main",
        "branch_prefix_of": _prefix_of,
        "max_concurrent": 2,
    }
    kwargs.update(overrides)
    return orchestrate.preflight_board(**kwargs)


def test_preflight_board_returns_the_state_the_engine_runs_on(board_seams):
    """A done, unlanded blocker `a` (its branch exists), then `b` <- `c`, given out of order."""
    a = _board_milestone(1, status="done", done_children=True)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    board_seams.cards = [c, b, a]
    board_seams.branches.add("p00000001-integrate")

    pre = _preflight(board_seams)

    root = runs.resolve_repo_dir(board_seams.root)
    assert pre.root == root
    assert pre.base_branch == "main"
    assert pre.max_concurrent == 2
    assert [card.id for card in pre.milestones] == [b.id, c.id]
    assert [[card.id for card in level] for level in pre.levels] == [[b.id], [c.id]]
    assert list(pre.prefixes) == [b.id, c.id, a.id]
    assert pre.prefixes[a.id] == "p00000001"
    assert pre.bases == {b.id: "p00000001-integrate", c.id: "p00000002-integrate"}
    assert pre.levels_payload == [
        {"level": 0, "milestones": [b.id]},
        {"level": 1, "milestones": [c.id]},
    ]
    assert board_seams.asked == [(root, "p00000001-integrate")]
    assert board_seams.claims == [orchestrate.board_claims(pre.milestones, pre.prefixes)]
    assert board_seams.runs.calls == []


@pytest.mark.parametrize(
    "overrides", [{"max_concurrent": 0}, {"base_branch": None}, {"base_branch": ""}]
)
def test_preflight_board_refuses_bad_arguments_before_it_reads_the_board(
    board_seams, monkeypatch, overrides
):
    def no_read(*, repo_dir=None):
        pytest.fail("preflight_board read the board before refusing its arguments")

    monkeypatch.setattr(board, "roots", no_read)

    with pytest.raises(ValueError):
        _preflight(board_seams, **overrides)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


@pytest.mark.parametrize(
    ("cards", "overrides", "error", "match"),
    [
        pytest.param(
            lambda: [_board_milestone(1, blocked_by=(2,)), _board_milestone(2, blocked_by=(1,))],
            {},
            dag.DependencyCycleError,
            None,
            id="cycle",
        ),
        pytest.param(
            lambda: [_board_milestone(1), _board_milestone(2)],
            {"branch_prefix_of": lambda card: "same"},
            ValueError,
            "branch prefix",
            id="shared-prefix",
        ),
        pytest.param(
            lambda: [
                _board_milestone(1),
                _board_milestone(2),
                _board_milestone(3, blocked_by=(1, 2)),
            ],
            {},
            orchestrate.MilestoneBlockersError,
            "chain them",
            id="two-open-blockers",
        ),
    ],
)
def test_preflight_board_refuses_each_board_problem_before_the_claim_check(
    board_seams, cards, overrides, error, match
):
    board_seams.cards = cards()

    with pytest.raises(error, match=match):
        _preflight(board_seams, **overrides)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_preflight_board_lets_a_claim_conflict_propagate_with_nothing_started(
    board_seams, monkeypatch
):
    board_seams.cards = [_board_milestone(1), _board_milestone(2, blocked_by=(1,))]

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        raise cli.ClaimedError("held elsewhere", key=keys[-1], run_id=OTHER_RUN_ID)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)

    with pytest.raises(cli.ClaimedError) as caught:
        _preflight(board_seams)

    assert caught.value.run_id == OTHER_RUN_ID
    assert board_seams.runs.calls == []


def test_preflight_board_lets_a_git_failure_from_the_branch_check_propagate(
    board_seams, monkeypatch
):
    """Review Focus 4: a broken repository never passes the pre-flight silently."""
    done = _board_milestone(1, status="done", done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [done, later]
    asked: list[str] = []

    def broken_branch_exists(root: Path) -> Callable[[str], bool]:
        def exists(branch: str) -> bool:
            asked.append(branch)
            raise worktree.GitError("not a git repository", argv=["git"], exit_code=128)

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", broken_branch_exists)

    with pytest.raises(worktree.GitError) as caught:
        _preflight(board_seams)

    assert caught.value.exit_code == 128
    assert asked == ["p00000001-integrate"]
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_preflight_board_on_a_board_with_nothing_open_skips_the_claim_check(board_seams):
    """Review Focus 1: today's empty-board return comes before the claim check."""
    board_seams.cards = [_board_milestone(1, status="done", done_children=True)]

    pre = _preflight(board_seams)

    assert pre.milestones == []
    assert pre.levels == []
    assert pre.levels_payload == []
    assert pre.bases == {}
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_preflight_board_never_starts_an_event_loop(board_seams, monkeypatch):
    board_seams.cards = [_board_milestone(1), _board_milestone(2, blocked_by=(1,))]

    def fail(*args: Any, **kwargs: Any) -> None:
        pytest.fail("preflight_board started the board run")

    monkeypatch.setattr(orchestrate.asyncio, "run", fail)
    monkeypatch.setattr(orchestrate, "_run_board_async", fail)

    pre = _preflight(board_seams)

    assert [card.id for card in pre.milestones] == [_board_milestone(1).id, _board_milestone(2).id]
    assert board_seams.runs.calls == []
```

`Callable` is already imported in the test file (`from collections.abc import Awaitable, Callable`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k preflight_board -v`
Expected: every `test_preflight_board_*` item (11 of them) FAILs with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'preflight_board'`. Test 3's parametrize list names `orchestrate.MilestoneBlockersError`, which already exists, so collection succeeds.

- [ ] **Step 3: Add `BoardPreflight` and `preflight_board`**

In `src/agent_manager/orchestrate.py`, immediately after the `return exists` that ends `_local_branch_exists` and before `def run_board(`, insert:

```python
# ── the two stages of a board run (card 203a9a5e) ───────────────────────────


@dataclass(frozen=True)
class BoardPreflight:
    """What `preflight_board` read and decided for one board run.

    Everything `run_board_engine` reads afterwards, so the engine never reads
    the board again. `milestones` is `levels` flattened, in level order.
    `levels_payload` is the payload's `levels` value, so a foreground envelope
    and the run's report name the same levels. No run id: a board run has no
    Run record. Internal state, so a dataclass.
    """

    root: Path
    base_branch: str
    max_concurrent: int
    levels: list[list[models.CardNode]]
    milestones: list[models.CardNode]
    prefixes: dict[str, str]
    bases: dict[str, str]
    levels_payload: list[dict[str, Any]]


def preflight_board(
    *,
    repo_dir: Path,
    base_branch: str | None,
    branch_prefix_of: Callable[[models.CardNode], str],
    max_concurrent: int = 1,
) -> BoardPreflight:
    """Stage 1 of a board run: every argument check, read and refusal (card 203a9a5e).

    `run_board`'s refusals, in its order, each propagating unchanged and
    leaving nothing behind: `max_concurrent < 1` and a missing `base_branch`
    (`ValueError`, before the board is read); `board.roots()`, read once, then
    `dag.board_levels` (`DependencyCycleError`); `board_prefixes` with
    `roots=` (`ValueError`); `milestone_bases` over `_local_branch_exists`
    (`MilestoneBlockersError`, or a `GitError` that is not exit 1); last,
    only when something is open, one `cli.refuse_claimed` over `board_claims`
    (`ClaimedError`). `board.roots`, `cli.refuse_claimed` and
    `_local_branch_exists` are read at call time. Starts no event loop, opens
    no store and writes nothing.
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
    if milestones:
        cli.refuse_claimed(root, board_claims(milestones, prefixes))
    return BoardPreflight(
        root=root,
        base_branch=base_branch,
        max_concurrent=max_concurrent,
        levels=levels,
        milestones=milestones,
        prefixes=prefixes,
        bases=bases,
        levels_payload=levels_payload,
    )


```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k preflight_board -v`
Expected: PASS for all 11 collected items (tests 1, 4, 5, 6 and 7 once each, and tests 2 and 3 three times each).

- [ ] **Step 5: Make `run_board` call `preflight_board`**

In `run_board`'s body, replace everything from `if max_concurrent < 1:` down to and including `cli.refuse_claimed(root, board_claims(milestones, prefixes))` with the following. Leave the docstring alone in this task.

```python
    pre = preflight_board(
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix_of=branch_prefix_of,
        max_concurrent=max_concurrent,
    )
    if not pre.milestones:
        return {"ok": True, "board": True, "levels": pre.levels_payload, "milestones": []}
    entries = asyncio.run(
        _run_board_async(
            pre.milestones,
            prefixes=pre.prefixes,
            bases=pre.bases,
            root=pre.root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            driver=driver,
            clock=clock,
            max_concurrent=pre.max_concurrent,
            control_interval=control_interval,
        )
    )
    return {
        "ok": all(entry["status"] == "done" for entry in entries),
        "board": True,
        "levels": pre.levels_payload,
        "milestones": entries,
    }
```

The old `entries = asyncio.run(...)` call and the old `return {...}` that followed it are replaced too. The whole body after the docstring is exactly the block above.

- [ ] **Step 6: Run the board tests to verify nothing moved**

Run: `uv run pytest tests/test_orchestrate.py tests/test_cli.py -k "board" -q`
Expected: PASS, with no failures or errors.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "refactor: split the board pre-flight out of run_board as preflight_board (card 203a9a5e)"
```

---

### Task 2: `run_board_engine`, and `run_board` as the composition

**Files:**
- Modify: `src/agent_manager/orchestrate.py`. Add `run_board_engine` right after `preflight_board`, inside the card-203a9a5e section and before `def run_board(`. Then replace `run_board`'s body and add one sentence to its docstring.
- Test: `tests/test_orchestrate.py`. Append below `test_preflight_board_never_starts_an_event_loop` (Task 1), still above `# ── _local_branch_exists (card 5b772688)`.

**Interfaces:**
- Consumes: `orchestrate.BoardPreflight` and `orchestrate.preflight_board` (Task 1, signatures above). The test helper `_preflight(seams, **overrides)` (Task 1). Existing `_run_board_async(milestones, *, prefixes, bases, root, commands, allow_no_verification, runner_factory, driver, clock, max_concurrent, control_interval) -> list[dict[str, Any]]`. Existing test helpers `_board`, `_by_id` and `_bases`.
- Produces: `orchestrate.run_board_engine(pre: BoardPreflight, *, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory: runs.RunnerFactory | None = None, driver: Driver | None = None, clock: Callable[[], datetime] = _utcnow, control_interval: float = control.CONTROL_POLL_SECONDS) -> dict[str, Any]`, which a later detach card calls inside the child.

- [ ] **Step 1: Write the failing tests**

Append to the card-203a9a5e test section in `tests/test_orchestrate.py`, directly below `test_preflight_board_never_starts_an_event_loop`:

```python
def test_run_board_engine_runs_a_preflight_without_reading_the_board_again(
    board_seams, monkeypatch
):
    """Review Focus 3: the engine runs the approved pre-flight, never a fresh read."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    pre = _preflight(board_seams)

    def no_read(*, repo_dir=None):
        pytest.fail("run_board_engine read the board again")

    monkeypatch.setattr(board, "roots", no_read)
    board_seams.claims = []

    result = orchestrate.run_board_engine(pre)

    assert result["ok"] is True
    assert result["board"] is True
    assert result["levels"] == pre.levels_payload
    assert [entry["milestone_id"] for entry in result["milestones"]] == [a.id, b.id]
    assert _bases(board_seams) == {a.id: "main", b.id: "p00000001-integrate"}
    assert board_seams.claims == []
    assert pre.levels_payload == [
        {"level": 0, "milestones": [a.id]},
        {"level": 1, "milestones": [b.id]},
    ]
    assert pre.bases == {a.id: "main", b.id: "p00000001-integrate"}


def test_run_board_engine_gives_the_same_payload_run_board_gives(board_seams):
    """Review Focus 5: one board, both entry points, one payload."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    board_seams.runs.outcomes[a.id] = {"escalated": True, "run_id": "r"}

    engine_result = orchestrate.run_board_engine(_preflight(board_seams))
    board_seams.runs.calls.clear()
    board_result = _board(board_seams)

    assert engine_result == board_result
    assert engine_result["ok"] is False
    assert _by_id(engine_result)[b.id] == {
        "milestone_id": b.id,
        "status": "blocked",
        "blocked_by": [a.id],
    }
    assert board_seams.runs.called() == [a.id]


def test_run_board_engine_uses_the_preflights_lane_bound_and_forwards_run_arguments(
    board_seams,
):
    one, two = _board_milestone(1), _board_milestone(2)
    board_seams.cards = [one, two]
    pre = _preflight(board_seams, max_concurrent=3)
    runner_factory = object()
    driver = object()

    def clock() -> datetime:
        return datetime(2026, 10, 4, tzinfo=timezone.utc)

    orchestrate.run_board_engine(
        pre,
        commands=("git status",),
        allow_no_verification=True,
        runner_factory=runner_factory,
        driver=driver,
        clock=clock,
        control_interval=0.25,
    )

    assert sorted(board_seams.runs.called()) == sorted([one.id, two.id])
    slots = [kwargs["slots"] for _milestone, kwargs in board_seams.runs.calls]
    assert isinstance(slots[0], asyncio.Semaphore)
    assert all(semaphore is slots[0] for semaphore in slots)
    for milestone, kwargs in board_seams.runs.calls:
        assert kwargs["max_concurrent"] == 3
        assert kwargs["repo_dir"] == pre.root
        assert kwargs["branch_prefix"] == pre.prefixes[milestone]
        assert kwargs["base_branch"] == pre.bases[milestone]
        assert list(kwargs["commands"]) == ["git status"]
        assert kwargs["allow_no_verification"] is True
        assert kwargs["runner_factory"] is runner_factory
        assert kwargs["driver"] is driver
        assert kwargs["clock"] is clock
        assert kwargs["control_interval"] == 0.25


def test_run_board_engine_on_an_empty_preflight_starts_no_event_loop(board_seams, monkeypatch):
    """Review Focus 1: nothing open, so nothing to run and no loop to start."""
    board_seams.cards = [_board_milestone(1, status="done", done_children=True)]
    pre = _preflight(board_seams)

    def fail(*args: Any, **kwargs: Any) -> None:
        pytest.fail("run_board_engine started a run on an empty board")

    monkeypatch.setattr(orchestrate, "_run_board_async", fail)
    monkeypatch.setattr(orchestrate.asyncio, "run", fail)

    result = orchestrate.run_board_engine(pre)

    assert result == {"ok": True, "board": True, "levels": [], "milestones": []}
    assert board_seams.runs.calls == []
```

`datetime` and `timezone` are already imported in the test file (`from datetime import datetime, timezone`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k run_board_engine -v`
Expected: all four FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'run_board_engine'`.

- [ ] **Step 3: Add `run_board_engine`**

In `src/agent_manager/orchestrate.py`, directly after `preflight_board` (its `return BoardPreflight(...)`) and before `def run_board(`, insert:

```python
def run_board_engine(
    pre: BoardPreflight,
    *,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Stage 2 of a board run: run the board `pre` approved, and report (card 203a9a5e).

    `pre` is the only input. The board is not read again, prefixes and bases
    are not derived again, and the up-front claim check is not repeated: that
    pre-flight is the caller's. Each milestone's own pre-flight inside
    `_run_milestone_async` still runs, so a claim taken since `pre` is an
    `escalated` entry. With nothing open it returns `ok` with no milestones and
    starts no event loop. Otherwise one `asyncio.run(_run_board_async(...))`
    on `pre.max_concurrent`, and `run_board`'s payload with `pre.levels_payload`
    as its `levels`. Synchronous; does not mutate `pre`.
    """
    if not pre.milestones:
        return {"ok": True, "board": True, "levels": pre.levels_payload, "milestones": []}
    entries = asyncio.run(
        _run_board_async(
            pre.milestones,
            prefixes=pre.prefixes,
            bases=pre.bases,
            root=pre.root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            driver=driver,
            clock=clock,
            max_concurrent=pre.max_concurrent,
            control_interval=control_interval,
        )
    )
    return {
        "ok": all(entry["status"] == "done" for entry in entries),
        "board": True,
        "levels": pre.levels_payload,
        "milestones": entries,
    }
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "run_board_engine or preflight_board" -v`
Expected: PASS (15 items).

- [ ] **Step 5: Make `run_board` the composition**

Replace `run_board`'s whole body after the docstring (the block Task 1 Step 5 wrote) with exactly:

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

In `run_board`'s docstring, keep every existing sentence. Change the first line from:

```python
    """Drive every open milestone on the board as one grafo tree, and report.
```

to these two lines, followed by the existing blank line:

```python
    """Drive every open milestone on the board as one grafo tree, and report.

    It composes `preflight_board` and `run_board_engine` (card 203a9a5e).
```

The rest of the docstring, starting `Refusals come first, in this order, ...`, stays as it is.

- [ ] **Step 6: Run the full default suite**

Run: `uv run pytest`
Expected: PASS. This covers every existing `run_board` test (`tests/test_orchestrate.py` ~7402-7980), the `tests/test_cli.py` board tests and the 15 new seam items. `tests/e2e/test_run_board.py` is `e2e_fake` and opt-in. Run it too, since it pins production wiring:

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py -q`
Expected: PASS.

- [ ] **Step 7: Confirm the untouched surfaces are untouched**

Run: `git diff --stat HEAD~1 -- src/agent_manager/cli.py tests/test_cli.py tests/e2e/`
Expected: empty output. Neither task touches these files.

Run: `git diff HEAD~1 -- tests/test_orchestrate.py | grep '^-[^-]'`
Expected: empty output. No existing test line is removed.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "refactor: run_board composes preflight_board and run_board_engine (card 203a9a5e)"
```
<!-- task-pipeline: validated -->
