# 1.5 e2e_fake: dependent milestones really stack — spec

Card: `d8b6ed12` (subtask of story `8fd3e3de`, blocked by `5bfe746d`).
Parent design: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`
(cited below as **D**, by section and line).

## Purpose

The stacking decision (`orchestrate.milestone_bases`) and its wiring into
`run_board` / `_run_board_async` are already implemented and unit-pinned
(cards `5b772688`, `5bfe746d`; `tests/test_orchestrate.py`). Nothing yet shows,
through the production wiring and real `git`/`brd` under the fake `claude`,
that a blocked milestone's branches **actually contain** its blocker's commits.
This card adds that proof, at the `e2e_fake` tier, as D §Testing (lines 112-114)
asks: "a two-milestone board where the second is blocked by the first: the
second's worktree contains the first's commits; a three-milestone chain", plus
the D §1 table row "one blocker B that is `done` (not `merged`) **and** whose
integrate branch exists locally → `integrate_branch(B)`" (line 45) exercised as
a relaunch.

This card is **tests only**. No production source changes. If a test written to
this spec fails against the current code, that is a real defect: stop and
report it rather than bending the assertion.

## Inherited constraints

- A milestone with exactly one open blocker B runs on `<prefix(B)>-integrate`
  (D §1 table, line 44).
- A `done` (not `merged`) blocker whose integrate branch exists locally is a
  stack target too (D line 45); "open" is `dag.board_levels`' notion and
  `merged`/`canceled`/`archived` are landed (D lines 49-51).
- Stories of a stacked milestone root on the milestone's base, and its own
  Integrate merges into its own `<prefix>-integrate` (D lines 66-68).
- `am` never touches `--base-branch` (D §Non-goals, line 30): `main` must be
  byte-identical (same `rev-parse`) before and after every board run.
- Chain, not merged base, for multi-blocker milestones (D lines 31-32): the
  three-chain must be strictly linear, each link on its immediate blocker.
- Tier rules (CLAUDE.md "Test tiers"): `@pytest.mark.e2e_fake` = production
  wiring under the fake `claude`, opt-in with `uv run pytest -m e2e_fake`, tier
  budget ≤8 min. The default `uv run pytest` must remain green and unaffected.
- JSON keys additive only; journal stays schema 1 (card text). This card adds
  no keys.

## Where

All new tests go in `tests/e2e/test_run_board.py` (the existing `run_board`
e2e_fake module; tests mirror `src/agent_manager/orchestrate.py`'s board run).

- Each new test carries an explicit `@pytest.mark.e2e_fake`. The module stays
  unmarked at module level (`test_this_module_runs_in_the_default_suite_unmarked`
  checks this and must keep passing).
- Insert the new tests **after**
  `test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front` and
  **before** `test_no_rendezvous_is_left_armed_for_later_tests`, which must stay
  last in the module.
- Reuse the module's helpers: `board_root` fixture, `_milestone`, `_run_board`,
  `_entries`, `_load_run`, `_git`, `_local_branches`. Use
  `integration.integration_branch(prefix)` for integrate names and
  `cli.worktree_for(root, branch)` for worktree paths. One small shared helper
  is allowed, e.g. `_is_ancestor(root, ancestor, descendant) -> bool` wrapping
  `git merge-base --is-ancestor` (exit 0 → True, exit 1 → False, anything else
  raises). Do not duplicate existing helpers.
- Keep each milestone at one story / one subtask (`_milestone`) to stay inside
  the tier budget.

## Observable behavior each test pins

Notation: for milestone `m`, `I(m) = f"{m['prefix']}-integrate"`, `S(m) = m['branch']`
(its only subtask's branch). "Contains X" means `git merge-base --is-ancestor X <ref>`
succeeds in the repo at `root`.

### T1 — two milestones, B blocked by A, B stacks on A

`test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch` (e2e_fake).

Setup: `a = _milestone(root, "A", "ba")`, `b = _milestone(root, "B", "bb", blocked_by=(a["id"],))`;
record `main_before = rev-parse main`. Run `_run_board(root, a, b)`.

Must observe:
1. `result["ok"] is True`; levels are `[[A], [B]]` (as
   `test_a_blocked_by_pair_runs_in_order` asserts); both entries `done`.
2. A's run row: `_load_run(..A..).base_branch == "main"`.
   B's run row: `_load_run(..B..).base_branch == I(a)` (`"ba-integrate"`).
   Every subtask row in B's run (`run.stories[*].subtasks[*]`) whose story is
   B's root story has `base_branch == I(a)`.
3. Stacking is real in git: `S(b)` contains `S(a)` and contains the tip of
   `I(a)`; `I(b)` contains `I(a)`.
4. If `cli.worktree_for(root, S(b))` still exists after the run, its `HEAD`
   contains `S(a)` and `IMPLEMENTATION.md` content that A's coder wrote is
   reachable there (`git log` of `S(b)` includes A's subtask commit). The
   ancestry assertions in (3) are the binding check; this one is applied only
   when the worktree is present, so the test does not depend on worktree
   cleanup policy.
5. Negative control: `main` does not contain `S(a)` nor `I(a)`, and
   `rev-parse main == main_before`.

### T2 — three-milestone chain A ← B ← C

`test_a_three_milestone_chain_stacks_each_link_on_the_one_before` (e2e_fake).

Setup: `a` (`"ba"`), `b` (`"bb"`, blocked by a), `c` (`"bc"`, blocked by b).
Run `_run_board(root, a, b, c, max_concurrent=2)` (two slots, so only the
dependency edges order them).

Must observe:
1. `ok`; levels `[[A], [B], [C]]`; all three entries `done`.
2. Run bases: A `"main"`, B `I(a)`, C `I(b)` — C is on its **immediate**
   blocker, not on `I(a)` and not on `main` (no flattening).
3. Transitive containment: `S(c)` contains `S(a)`, `S(b)`, `I(a)`, `I(b)`;
   `I(c)` contains `I(b)`, which contains `I(a)`.
4. The chain is ordered: `S(b)` does **not** contain `S(c)`, and `S(a)` does
   not contain `S(b)` (the stack points one way only).
5. `rev-parse main == main_before`.

### T3 — relaunch with a `done` blocker whose integrate branch survives

`test_a_relaunch_stacks_a_new_milestone_on_a_done_blockers_integrate_branch` (e2e_fake).

Setup and first run: `a = _milestone(root, "A", "ba")`; `first = _run_board(root, a)`.
Then:
- `first["ok"] is True`; `I(a)` is in `_local_branches(root)`.
- Precondition pin: A's milestone card is finished but not landed —
  `board.show(a["id"]).status == "done"` (the rollup sets it; it must not be
  `merged`). If the rollup leaves it at another non-landed status, the test
  sets it to `done` explicitly through `board.set_status` before continuing,
  so the scenario is exactly D line 45's row.

Second run: `b = _milestone(root, "B", "bb", blocked_by=(a["id"],))`;
`second = _run_board(root, a, b)` — `a` is passed so the helper's
`branch_prefix_of` can name A's prefix, which `board_prefixes(roots=...)`
derives for a non-open blocker root.

Must observe:
1. `second["ok"] is True`; `second["levels"] == [{"level": 0, "milestones": [b["id"]]}]`
   — A dropped out as done, so B is level 0, and only B has an entry.
2. B's run row `base_branch == I(a)` (not `"main"`), although A was not
   dispatched in this run.
3. `S(b)` contains `S(a)` and `I(a)`; `I(b)` contains `I(a)`.
4. A's branches were not rewritten: `rev-parse I(a)` after the second run
   equals its value after the first run.
5. `rev-parse main` is unchanged across both runs.

Out of this test (already unit-pinned in `tests/test_orchestrate.py`): the
sibling rows "done blocker without a local integrate branch → `main`" and
"`merged` blocker → `main`". They may be added here only if they cost no extra
board run; they are not required.

## Error paths

No new error path is introduced. The existing e2e refusal
(`test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front`) covers
`MilestoneBlockersError` and must keep passing untouched.

## Test list and tiers

| Test | Tier | Why this tier |
|---|---|---|
| T1 `test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch` | `e2e_fake` | Needs production wiring end to end (real `brd`, real `git` worktrees/merges, fake `claude` coder commits); unit fakes cannot show commits really landing on a stacked branch. |
| T2 `test_a_three_milestone_chain_stacks_each_link_on_the_one_before` | `e2e_fake` | Same; transitive ancestry across three real Integrates only exists with real git under the production walk. |
| T3 `test_a_relaunch_stacks_a_new_milestone_on_a_done_blockers_integrate_branch` | `e2e_fake` | Two real board runs; the second's base depends on `_local_branch_exists` reading a branch the first run's real Integrate left behind. |
| existing `test_this_module_runs_in_the_default_suite_unmarked` | unit (default) | Unchanged; must still pass, proving no module-level marker was added. |

Verification: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py` green
(new tests included), and `uv run pytest` (default tiers) green with the same
test count as before (the new tests are opt-in).

TDD note: these are characterization tests of shipped behavior, so they are
expected to pass on first run. To prove each one can fail, the implementer
temporarily breaks the wiring (e.g. pass `base_branch` instead of
`bases[card.id]` in `_run_board_async`), observes T1–T3 fail on the base and
ancestry assertions, then reverts. The temporary break is never committed.

## Out of scope

- `am run --board --detach` and everything in D §2 (lines 76-96), including the
  detached `am watch --all` / pause / resume e2e scenario in D line 114-115 —
  sibling cards.
- README changes (D lines 118-119).
- Any production code change in `orchestrate.py`, `dag.py`, `census.py`, `cli.py`.
- Merged-base resolution for multi-blocker milestones (D Non-goals, line 31).
- New fixtures or changes to `tests/e2e/fake_claude.py` / `tests/conftest.py`.

---

# 1.5 e2e_fake: dependent milestones really stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, through the production board wiring with real `git`/`brd` and the fake `claude`, that a blocked milestone's branches really contain its blocker's commits — for a pair, a three-chain, and a relaunch on a `done` blocker.

**Architecture:** Three new `@pytest.mark.e2e_fake` characterization tests plus one small `_is_ancestor` helper, all in `tests/e2e/test_run_board.py`. They call `orchestrate.run_board` through the module's existing `_run_board` helper and read the outcome back from the run rows (`_load_run`), the board (`board.show`) and git ancestry (`git merge-base --is-ancestor`). No production source changes.

**Tech Stack:** Python 3, pytest, real `git`, real `brd`, the fake `claude` (`tests/e2e/fake_claude.py` via the `fake_claude_bin` fixture), `uv`.

**Spec:** `docs/superpowers/specs/1-5-e2e-fake-dependent-d8b6ed12.md` (reproduced verbatim above).

## Global Constraints

- Tests only: no change to anything under `src/agent_manager/` is committed (`orchestrate.py`, `dag.py`, `census.py`, `cli.py` untouched).
- No change to `tests/e2e/fake_claude.py` or `tests/conftest.py`; no new fixtures.
- Every new test carries an explicit `@pytest.mark.e2e_fake`; the module stays unmarked at module level (`test_this_module_runs_in_the_default_suite_unmarked` must keep passing).
- New tests go after `test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front` and before `test_no_rendezvous_is_left_armed_for_later_tests`, which stays last in the module.
- Reuse `board_root`, `_milestone`, `_run_board`, `_entries`, `_load_run`, `_git`, `_local_branches`; use `integration.integration_branch(prefix)` for integrate names and `cli.worktree_for(root, branch)` for worktree paths. Only one new helper: `_is_ancestor(root, ancestor, descendant) -> bool` (exit 0 → True, 1 → False, anything else raises).
- One story / one subtask per milestone (`_milestone`), to stay inside the `e2e_fake` tier budget (≤8 min).
- `main` must have the same `rev-parse` before and after every board run.
- Default suite unaffected: `uv run pytest` must report `3460/3620 tests collected (160 deselected)` before the work and `3460/3623 tests collected (163 deselected)` after (three new opt-in tests, all deselected), and stay green.
- No new JSON keys; journal stays schema 1.
- If a test written to the spec fails against current code, that is a real defect: stop and report it; do not bend the assertion.

## Review Focus

1. A git ref typo in an ancestry assertion (exit 128 from `merge-base --is-ancestor`) must not read as "not an ancestor", or every negative assertion (`main` does not contain `S(a)`, the chain points one way) passes vacuously — Task 1 pins that `_is_ancestor` raises on an unknown ref.
2. A stacked milestone's own Integrate must merge into its **own** `<prefix>-integrate`, never into its blocker's — a person relying on `ba-integrate` as "A's work only" would be surprised to find B in it. Task 1 pins `entries[B]["integrated"]["branch"] == I(b)` and that `I(a)` does not contain `S(b)`.
3. Stacking must not cut the stack off from `main`'s history: the positive control `main` ⊂ `S(b)` (and ⊂ `S(c)`) guards against a base that is an orphan or unrelated ref. Tasks 1 and 2 pin it.
4. A relaunch must not re-dispatch the already-`done` blocker A: a person re-running `am run --board` would not expect A's work to run again. Task 3 pins that the second run created no new run row for A (exactly one run id ends with A's short id).
5. A relaunch must not rewrite A's subtask branch any more than its integrate branch: Task 3 pins `rev-parse S(a)` unchanged across the second run, alongside the spec's `I(a)` check.

---

### Task 1: `_is_ancestor` helper and T1 — a blocked milestone stacks on its blocker's integrate branch

**Files:**
- Modify: `tests/e2e/test_run_board.py:142-143` (add `_is_ancestor` right after `_local_branches`)
- Modify: `tests/e2e/test_run_board.py:374-376` (insert T1 between `test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front` and `test_no_rendezvous_is_left_armed_for_later_tests`)
- Test: `tests/e2e/test_run_board.py`

**Interfaces:**
- Consumes (existing, in the module): `_git(cwd: Path, *args: str) -> str`, `_milestone(root, label, prefix, blocked_by=()) -> dict[str, str]` with keys `id`, `story`, `subtask`, `prefix`, `branch`; `_run_board(root, *milestones, max_concurrent=1) -> dict[str, Any]`; `_entries(result) -> dict[str, dict]`; `_load_run(root, run_id) -> models.Run`; `_local_branches(root) -> list[str]`; `integration.integration_branch(prefix: str) -> str` (returns `f"{prefix}-integrate"`); `cli.worktree_for(repo_dir: Path, branch: str) -> Path`. `models.Run` has `base_branch: str` and `stories: list[StoryRun]`; `StoryRun` has `card_id: str` and `subtasks: list[SubtaskRun]`; `SubtaskRun` has `base_branch: str`.
- Produces: `_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool` used by Tasks 2 and 3.

- [ ] **Step 1: Record the default-suite baseline**

Run: `uv run pytest --collect-only -q 2>&1 | tail -1`
Expected: `3460/3620 tests collected (160 deselected) in ...s`

- [ ] **Step 2: Add the `_is_ancestor` helper**

In `tests/e2e/test_run_board.py`, directly after `_local_branches` (currently lines 142-143), add:

```python
def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 is True, exit 1 is False, anything else raises.

    Anything else (exit 128: an unknown ref) is not an answer, so a typo in a
    ref can never pass a "does not contain" assertion by accident.
    """
    argv = ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant]
    completed = subprocess.run(argv, capture_output=True, text=True)
    if completed.returncode in (0, 1):
        return completed.returncode == 0
    raise subprocess.CalledProcessError(
        completed.returncode, argv, completed.stdout, completed.stderr
    )
```

- [ ] **Step 3: Write T1**

Insert after `test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front` and before `test_no_rendezvous_is_left_armed_for_later_tests`:

```python
@pytest.mark.e2e_fake
def test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch(board_root):
    """Card d8b6ed12, T1: B is blocked by A, so B runs on A's integrate branch
    and B's branches really contain A's commits; `main` is never touched."""
    root = board_root
    a = _milestone(root, "A", "ba")
    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))
    a_integrate = integration.integration_branch(a["prefix"])
    b_integrate = integration.integration_branch(b["prefix"])
    main_before = _git(root, "rev-parse", "main").strip()

    result = _run_board(root, a, b)

    assert result["ok"] is True, result
    assert result["levels"] == [
        {"level": 0, "milestones": [a["id"]]},
        {"level": 1, "milestones": [b["id"]]},
    ]
    entries = _entries(result)
    assert entries[a["id"]]["status"] == "done", entries[a["id"]]
    assert entries[b["id"]]["status"] == "done", entries[b["id"]]
    # B's own Integrate merges into B's own integrate branch, never A's.
    assert entries[b["id"]]["integrated"]["branch"] == b_integrate
    a_run = _load_run(root, entries[a["id"]]["run_id"])
    b_run = _load_run(root, entries[b["id"]]["run_id"])
    assert a_run.base_branch == "main"
    assert b_run.base_branch == a_integrate
    (b_story,) = [story for story in b_run.stories if story.card_id == b["story"]]
    assert b_story.subtasks, b_story  # non-vacuity: B's story recorded its subtask
    for subtask in b_story.subtasks:
        assert subtask.base_branch == a_integrate, subtask
    # Stacking is real in git.
    assert _is_ancestor(root, a["branch"], b["branch"])
    assert _is_ancestor(root, a_integrate, b["branch"])
    assert _is_ancestor(root, a_integrate, b_integrate)
    assert not _is_ancestor(root, b["branch"], a_integrate)
    # Positive control: the stack still sits on main's history.
    assert _is_ancestor(root, "main", b["branch"])
    # Only when the worktree survived the run (cleanup policy is not pinned here).
    b_worktree = cli.worktree_for(root, b["branch"])
    if b_worktree.exists():
        assert _is_ancestor(b_worktree, a["branch"], "HEAD")
        a_commit = _git(root, "log", "-1", "--format=%H", a["branch"], "--", "IMPLEMENTATION.md")
        assert a_commit.strip(), a["branch"]
        assert a_commit.strip() in _git(b_worktree, "log", "--format=%H", "HEAD").split()
    # Negative control: main gained nothing and did not move.
    assert not _is_ancestor(root, a["branch"], "main")
    assert not _is_ancestor(root, a_integrate, "main")
    assert _git(root, "rev-parse", "main").strip() == main_before
    # The helper refuses an unknown ref instead of answering False.
    with pytest.raises(subprocess.CalledProcessError):
        _is_ancestor(root, "no-such-branch", "main")
```

- [ ] **Step 4: Run T1 (characterization: expected to pass)**

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py::test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch -v`
Expected: PASS. If it FAILS on any assertion, stop: per the spec that is a real defect in shipped behavior — report the failing assertion and output; do not change the assertion.

- [ ] **Step 5: Prove T1 can fail (temporary break, never committed)**

In `src/agent_manager/orchestrate.py`, inside `run_board`, temporarily replace the line

```python
    bases = milestone_bases(all_roots, prefixes, _local_branch_exists(root), base_branch)
```

with

```python
    bases = {card.id: base_branch for card in milestones}
```

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py::test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch -v`
Expected: FAIL at `assert b_run.base_branch == a_integrate` (`'main' == 'ba-integrate'`).

- [ ] **Step 6: Revert the break**

Run: `git checkout -- src/agent_manager/orchestrate.py && git status --porcelain src/`
Expected: no output (the source tree is clean).

- [ ] **Step 7: Run T1 again plus the module's default-suite test**

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py::test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch -v && uv run pytest tests/e2e/test_run_board.py -v`
Expected: T1 PASS; the second command runs only `test_this_module_runs_in_the_default_suite_unmarked`, PASS (the rest deselected).

- [ ] **Step 8: Commit**

```bash
git add tests/e2e/test_run_board.py
git commit -m "test: pin that a blocked milestone stacks on its blocker's integrate branch (card d8b6ed12)"
```

---

### Task 2: T2 — a three-milestone chain stacks each link on the one before

**Files:**
- Modify: `tests/e2e/test_run_board.py` (insert T2 right after T1, still before `test_no_rendezvous_is_left_armed_for_later_tests`)
- Test: `tests/e2e/test_run_board.py`

**Interfaces:**
- Consumes: `_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool` from Task 1; the module helpers listed in Task 1.
- Produces: nothing later tasks use.

- [ ] **Step 1: Write T2**

Insert directly after `test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch`:

```python
@pytest.mark.e2e_fake
def test_a_three_milestone_chain_stacks_each_link_on_the_one_before(board_root):
    """Card d8b6ed12, T2: A <- B <- C with two free slots, so only the edges
    order them; C stacks on B's integrate branch, never flattened onto A's or main."""
    root = board_root
    a = _milestone(root, "A", "ba")
    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))
    c = _milestone(root, "C", "bc", blocked_by=(b["id"],))
    a_integrate = integration.integration_branch(a["prefix"])
    b_integrate = integration.integration_branch(b["prefix"])
    c_integrate = integration.integration_branch(c["prefix"])
    main_before = _git(root, "rev-parse", "main").strip()

    result = _run_board(root, a, b, c, max_concurrent=2)

    assert result["ok"] is True, result
    assert result["levels"] == [
        {"level": 0, "milestones": [a["id"]]},
        {"level": 1, "milestones": [b["id"]]},
        {"level": 2, "milestones": [c["id"]]},
    ]
    entries = _entries(result)
    for milestone in (a, b, c):
        assert entries[milestone["id"]]["status"] == "done", entries[milestone["id"]]
    assert _load_run(root, entries[a["id"]]["run_id"]).base_branch == "main"
    assert _load_run(root, entries[b["id"]]["run_id"]).base_branch == a_integrate
    # C is on its immediate blocker: not on A's integrate branch, not on main.
    assert _load_run(root, entries[c["id"]]["run_id"]).base_branch == b_integrate
    # Transitive containment.
    for ancestor in (a["branch"], b["branch"], a_integrate, b_integrate):
        assert _is_ancestor(root, ancestor, c["branch"]), ancestor
    assert _is_ancestor(root, b_integrate, c_integrate)
    assert _is_ancestor(root, a_integrate, b_integrate)
    # Positive control: the chain still sits on main's history.
    assert _is_ancestor(root, "main", c["branch"])
    # The stack points one way only.
    assert not _is_ancestor(root, c["branch"], b["branch"])
    assert not _is_ancestor(root, b["branch"], a["branch"])
    assert _git(root, "rev-parse", "main").strip() == main_before
```

- [ ] **Step 2: Run T2 (characterization: expected to pass)**

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py::test_a_three_milestone_chain_stacks_each_link_on_the_one_before -v`
Expected: PASS. If it FAILS, stop and report the failing assertion: it is a real defect, not a test to bend.

- [ ] **Step 3: Prove T2 can fail (temporary break, never committed)**

In `src/agent_manager/orchestrate.py`, inside `run_board`, temporarily replace

```python
    bases = milestone_bases(all_roots, prefixes, _local_branch_exists(root), base_branch)
```

with

```python
    bases = {card.id: base_branch for card in milestones}
```

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py::test_a_three_milestone_chain_stacks_each_link_on_the_one_before -v`
Expected: FAIL at `assert _load_run(root, entries[b["id"]]["run_id"]).base_branch == a_integrate` (`'main' == 'ba-integrate'`).

- [ ] **Step 4: Revert the break**

Run: `git checkout -- src/agent_manager/orchestrate.py && git status --porcelain src/`
Expected: no output.

- [ ] **Step 5: Run T2 again**

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py::test_a_three_milestone_chain_stacks_each_link_on_the_one_before -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/test_run_board.py
git commit -m "test: pin that a three-milestone chain stacks each link on the one before (card d8b6ed12)"
```

---

### Task 3: T3 — a relaunch stacks a new milestone on a `done` blocker's surviving integrate branch

**Files:**
- Modify: `tests/e2e/test_run_board.py:31` (add `census` to the `from agent_manager import ...` line)
- Modify: `tests/e2e/test_run_board.py` (insert T3 right after T2, still before `test_no_rendezvous_is_left_armed_for_later_tests`)
- Test: `tests/e2e/test_run_board.py`

**Interfaces:**
- Consumes: `_is_ancestor` from Task 1; the module helpers listed in Task 1; `_run_ids(root) -> list[str]` (existing); `dag.short_id(card_id) -> str` (existing; run ids end with the milestone's short id, as `test_an_escalated_milestone_blocks_its_dependent_and_not_its_sibling` relies on); `census.is_landed(status: str | None) -> bool` (True for `merged`/`canceled`/`archived`); `board.show(card_id, *, repo_dir) -> models.Card` (has `.status`); `board.set_status(card_id, status, *, repo_dir) -> models.Card`.
- Produces: nothing.

- [ ] **Step 1: Import `census`**

Change line 31 of `tests/e2e/test_run_board.py` from

```python
from agent_manager import bases, board, cli, dag, integration, models, orchestrate, paths, store
```

to

```python
from agent_manager import (
    bases,
    board,
    census,
    cli,
    dag,
    integration,
    models,
    orchestrate,
    paths,
    store,
)
```

(The single line would exceed the module's line length with `census` added.)

- [ ] **Step 2: Write T3**

Insert directly after `test_a_three_milestone_chain_stacks_each_link_on_the_one_before`:

```python
@pytest.mark.e2e_fake
def test_a_relaunch_stacks_a_new_milestone_on_a_done_blockers_integrate_branch(board_root):
    """Card d8b6ed12, T3: A finished `done` (not `merged`) in a first run and
    left its integrate branch behind; B, added later and blocked by A, stacks
    on that branch in a second run that never dispatches A again."""
    root = board_root
    a = _milestone(root, "A", "ba")
    a_integrate = integration.integration_branch(a["prefix"])
    main_before = _git(root, "rev-parse", "main").strip()

    first = _run_board(root, a)

    assert first["ok"] is True, first
    assert a_integrate in _local_branches(root)
    a_status = board.show(a["id"], repo_dir=root).status
    assert not census.is_landed(a_status), a_status
    if a_status != "done":
        board.set_status(a["id"], "done", repo_dir=root)
    assert board.show(a["id"], repo_dir=root).status == "done"
    a_integrate_after_first = _git(root, "rev-parse", a_integrate).strip()
    a_branch_after_first = _git(root, "rev-parse", a["branch"]).strip()

    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))
    b_integrate = integration.integration_branch(b["prefix"])
    # `a` is passed so `branch_prefix_of` can name A's prefix for its blocker root.
    second = _run_board(root, a, b)

    assert second["ok"] is True, second
    assert second["levels"] == [{"level": 0, "milestones": [b["id"]]}]
    entries = _entries(second)
    assert set(entries) == {b["id"]}
    assert entries[b["id"]]["status"] == "done", entries[b["id"]]
    assert _load_run(root, entries[b["id"]]["run_id"]).base_branch == a_integrate
    # A was not dispatched again: it still has exactly its first run.
    a_short = dag.short_id(a["id"])
    assert len([run_id for run_id in _run_ids(root) if run_id.endswith(a_short)]) == 1
    assert _is_ancestor(root, a["branch"], b["branch"])
    assert _is_ancestor(root, a_integrate, b["branch"])
    assert _is_ancestor(root, a_integrate, b_integrate)
    # A's branches were not rewritten.
    assert _git(root, "rev-parse", a_integrate).strip() == a_integrate_after_first
    assert _git(root, "rev-parse", a["branch"]).strip() == a_branch_after_first
    assert _git(root, "rev-parse", "main").strip() == main_before
```

- [ ] **Step 3: Run T3 (characterization: expected to pass)**

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py::test_a_relaunch_stacks_a_new_milestone_on_a_done_blockers_integrate_branch -v`
Expected: PASS. If it FAILS, stop and report the failing assertion: it is a real defect, not a test to bend.

- [ ] **Step 4: Prove T3 can fail (temporary break, never committed)**

In `src/agent_manager/orchestrate.py`, inside `run_board`, temporarily replace

```python
    bases = milestone_bases(all_roots, prefixes, _local_branch_exists(root), base_branch)
```

with

```python
    bases = {card.id: base_branch for card in milestones}
```

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py::test_a_relaunch_stacks_a_new_milestone_on_a_done_blockers_integrate_branch -v`
Expected: FAIL at `assert _load_run(root, entries[b["id"]]["run_id"]).base_branch == a_integrate` (`'main' == 'ba-integrate'`).

- [ ] **Step 5: Revert the break**

Run: `git checkout -- src/agent_manager/orchestrate.py && git status --porcelain src/`
Expected: no output.

- [ ] **Step 6: Run the whole module's e2e_fake tier**

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py -v`
Expected: 11 passed (the 8 existing e2e_fake tests plus T1, T2, T3), 1 deselected; `test_no_rendezvous_is_left_armed_for_later_tests` is still listed last.

- [ ] **Step 7: Run the default suite and confirm it is unaffected**

Run: `uv run pytest --collect-only -q 2>&1 | tail -1 && uv run pytest`
Expected: `3460/3623 tests collected (163 deselected)` — the same 3460 selected as the Task 1 baseline, with the three new tests among the deselected — and the full default run green.

- [ ] **Step 8: Confirm no production source changed on the branch**

Run: `git status --porcelain src/ && git diff --stat HEAD -- src/`
Expected: no output from either.

- [ ] **Step 9: Commit**

```bash
git add tests/e2e/test_run_board.py
git commit -m "test: pin that a relaunch stacks a new milestone on a done blocker's integrate branch (card d8b6ed12)"
```
<!-- task-pipeline: validated -->
