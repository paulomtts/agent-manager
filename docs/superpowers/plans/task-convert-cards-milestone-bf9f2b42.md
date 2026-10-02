<!-- task-pipeline: validated -->
# Convert `cards` / `milestone_board` / `resume_board` to FakeBoard (bf9f2b42)

Card: bf9f2b42-ae9d-4201-88da-2d43974fb55a. Parent story: a0b987c1 "Move test_cli.py off the real board". Milestone 15: test tiers (66ed75cd).
Narrows: `docs/superpowers/specs/2026-10-02-test-tier-design.md` V5 (lines 145-150), using the seam from V3 (lines 127-136) and the tier rule from V1 (lines 84-92).

## Starting state (checked in this worktree, not master)

- This worktree already has the V3 seam: `board.run_brd` (src/agent_manager/board.py:168), which every public board function calls. It also has `FakeBoard` and the `fake_board` fixture (tests/conftest.py:135, :401), plus the V1 markers and addopts in pyproject.toml:33-48. The exploration notes said these exist only on unmerged branches. That is true of master, but this worktree already contains them, so nothing has to be merged first. If `board.run_brd` or `fake_board` is missing when work starts, stop and report it. Do not rebuild them here.
- Find the fixtures by name, not by line number. In this worktree they are: `cards` at tests/test_cli.py:1254, `milestone_board` at :2573 (with `_block` at :2554 and `M2_SHAPE` at :2564), and `resume_board` at :4952. These numbers do not match the card's (:1148-1195, :2502-2532) or the exploration notes' (:5544).
- The exploration findings were cut off at 8000 chars, partway through "STEP 6 — test-tier PLACEMENT RULE". The upstream stage wrote more than its limit. The tier assignments below come straight from spec V1, not from the missing text.

## Scope

Change only the bodies of the three fixtures in tests/test_cli.py:

1. **`cards`**: request `fake_board` as well as `project`. Seed the milestone -> story -> subtask chain with `fake_board.add_card(title, parent_id=...)`. Keep the same titles and return the same `{"milestone", "story", "subtask"}` dict.
2. **`milestone_board`**: request `fake_board`. Seed the decoy root "Milestone 1: walking skeleton", then "Milestone 2: make the skeleton real" with the `M2_SHAPE` stories and subtasks. Build the B-after-A and C-after-B story chain and each story's subtask chain at creation time with `add_card(..., blocked_by=[previous_id])`, not with `_block`. Return the same `{"milestone", "stories", "subtasks", "titles"}` dict with the same keys and contents.
3. **`resume_board`**: same approach. Milestone 4, story A with a1 then a2 (a2 `blocked_by=[a1]`), and story B with b1. Story B is `blocked_by=[story_a]`, so story A must be seeded before story B. The returned dict keeps the same keys.

All three keep depending on `project`, because the CLI still needs a real git repo and directory. Any fixture docstring that says "a real brd board" or mentions `brd block` must be updated to describe the FakeBoard seeding.

## Constraints that come from FakeBoard

- `brd block` and `brd add` are not argv shapes FakeBoard answers (any argv outside show/tree/roots/update --status/comment add/comment list raises AssertionError). This is why blocking edges are seeded with `blocked_by` at creation instead of added afterwards.
- `add_card` rejects `status="blocked"`. Blocked status is derived from `blocked_by`, the same way real brd does it.
- No seeded title or description may contain `[[`.
- Seeding is not recorded in `fake_board.writes`. Tests that check that nothing was written must still pass.

## Out of scope

- The `project` fixture, including its real `git init` and `brd init`.
- The `_add_card` and `_block` helpers. Inline `_add_card`/`_block` calls still use them at test_cli.py:1413-1416, :2923, :2968 and :2989-2996, so they must stay. Converting those tests is not this card's work.
- The 5 fixture-less tests and the 8 `--pretty` tests. Sibling card 94580a42 is blocked on this card and owns them.
- Adding, removing or changing tier markers or the `requires_git`/`requires_brd` decorators. `requires_brd` still applies because `project` runs `brd init`.
- Everything listed in spec §8: xdist as the verify command, changes to the pygents engine, checkpoint format, harness adapter contract or `dispatch.py`'s LauncherFn, changes to the e2e tier, and M14's `am run --board`.

## Observable behaviour and error paths

- Every test that requests `cards`, `milestone_board` or `resume_board` (80 tests: 43 with a single-line signature, 37 with a multi-line one — counting by signature text, not by `def test_` line alone, catches both) keeps all of its assertions unchanged. It must pass or fail exactly as it did before the change.
- Board reads and writes inside those tests, including `board.show(...).status` checks such as test_run_card_really_moves_the_card_on_the_board (:1329) and status changes during setup, now go to the in-memory FakeBoard.
- If a test using these fixtures hits an argv FakeBoard does not answer, that AssertionError is a real finding. Report it. Do not weaken the assertion and do not extend FakeBoard in this card.
- If a test compares census or tree order and that order changes because FakeBoard's `created_at` values differ from real brd timestamps, that is drift. Fix it in the fixture seeding (pass explicit `created_at` values or blocking edges), not in the test.

## Test list and tier (spec V1 rule)

V1 rule: `unit` means no subprocess of any kind and driven through an injected fake. `git` means real git in `tmp_path` and no `brd`. `brd` (opt-in) means the real-brd adapter contract.

- **No new tests.** The card's deliverable is that the existing tests are unchanged.
- **Existing tests using the three fixtures: target tier `git`.** Their card data now comes from FakeBoard and their remaining subprocess use is real git through `project`. One leftover is outside this card: `project` still runs `brd init` once per test, so these tests do not fully meet `git`'s "no brd" condition yet. That is acceptable here because `project` is out of scope. Do not mark them `brd`, which would remove them from the default run. Do not mark them `unit`, because they spawn git.
- **Verification (as the card states it):**
  - `uv run pytest tests/test_cli.py -v` gives the same pass/fail/skip result for every test as a run on the pre-change tree.
  - Full `uv run pytest` is green.
  - Before changing anything, run `uv run pytest tests/test_cli.py -v` on the pre-change tree and record its wall time and test count as the baseline (an exploration note's figure of 234 tests in 257s, 72s of it fixture setup, is stale for this worktree -- `--collect-only` here currently shows 302 tests in the file -- so measure fresh rather than trusting that number). After the change, record the new wall time on the card next to that freshly measured baseline.

---

# Convert `cards` / `milestone_board` / `resume_board` to FakeBoard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed the card data of the `cards`, `milestone_board` and `resume_board` fixtures in `tests/test_cli.py` into the in-memory `fake_board` instead of spawning `brd add`/`brd block`, with every test that uses them keeping the exact same outcome.

**Architecture:** `board.run_brd` (src/agent_manager/board.py:168) is a module-global seam looked up at call time; the `fake_board` fixture (tests/conftest.py:400-405) monkeypatches it with a `FakeBoard`. Each converted fixture requests `fake_board` next to `project` and calls `fake_board.add_card(...)`; blocking edges that `_block` used to add after creation are passed as `blocked_by=[...]` at creation. The real git repo from `project` stays, so the CLI still runs in a real directory.

**Tech Stack:** Python 3.12, pytest 9 (`--import-mode=importlib`), uv, the repo-local `FakeBoard` in tests/conftest.py.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42/docs/superpowers/specs/task-convert-cards-milestone-bf9f2b42-design.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42` unless absolute. Every shell command starts with `cd` into that root because the shell's cwd is not preserved between commands. Scratch artifacts (logs, id lists) go to `/tmp/am-bf9f2b42/`, outside the repo, and are never committed.

## Global Constraints

- Only the bodies, signatures and docstrings of the fixtures `cards`, `milestone_board`, `resume_board` in `tests/test_cli.py` change. No other line of any file changes (except this plan file, which already exists).
- No test assertion changes. No test is added, removed, renamed or re-marked.
- `_add_card` (tests/test_cli.py:1210) and `_block` (tests/test_cli.py:2554) stay exactly as they are: inline calls at :1413-1416, :2923, :2968 and :2989-2996 still use them.
- The `project` fixture (tests/test_cli.py:1218-1250) is not touched; all three fixtures keep requesting it.
- `requires_git` / `requires_brd` decorators and tier markers are not added, removed or changed.
- `FakeBoard` (tests/conftest.py:135-397) and `board.run_brd` are not changed. If a test needs an argv FakeBoard does not answer, report it; do not extend FakeBoard.
- Never seed `status="blocked"`; never put `[[` in a seeded title or description.
- Verification command for the repo: `uv run pytest`.

## Review Focus

The spec forbids new tests, so each line below is pinned by a named verification step (an existing test run or a grep) in the owning task rather than by a new test function.

1. A test that requests one of the three fixtures and also shells out to real `brd` (via `_add_card`, `_block` or `subprocess`) with a fixture-seeded id would now get "card not found" from real brd. Expected: no such test exists. Pinned by Task 1 Step 4 (grep that the inline `_add_card`/`_block` call sites belong to tests that request none of the three fixtures).
2. Sibling order in `brd tree` / `brd show` children follows `created_at`. FakeBoard's clock is a monotonic microsecond counter from 2026-01-01, so seeding order must equal the old creation order (in `resume_board`, story B after a1 and a2). Expected: census/level/`completed` orders identical. Pinned by Task 4 Step 4 and Task 3 Step 4 (`completed == [a2, b1]` in `test_a_milestone_that_escalated_resumes_under_its_own_run_id`).
3. Derived `blocked` status: B's and C's subtasks in `milestone_board` and b1 in `resume_board` are `blocked` only through their parent; FakeBoard's `_resolved_status` derives that like brd's `resolve_status`. Expected: dry-run level assignment unchanged. Pinned by Task 3 Step 4 (`test_the_milestone_dry_run_stacks_each_story_on_the_previous_ones_tip` asserts levels `[[A],[B],[C]]`).
4. Tests that assert "nothing was written" (`_forbid_writes`, porcelain checks) must not see seeding as a write. FakeBoard does not record seeding in `writes`, and `_forbid_writes` patches `cli.board.set_status` above the seam. Expected: those tests still pass. Pinned by Task 3 Step 4 and the per-fixture outcome diff in Task 3 Step 5.
5. A board read that happens after a status write inside a test (e.g. `board.show(subtask).status == "done"` in `test_run_card_really_moves_the_card_on_the_board`) must see the write. FakeBoard mutates in place. Pinned by Task 2 Step 4.

---

### Task 1: Preconditions and baseline

**Files:**
- Read only: `src/agent_manager/board.py:168`, `tests/conftest.py:400-405`, `tests/test_cli.py`
- Create (scratch, not committed): `/tmp/am-bf9f2b42/list_fixture_tests.py`, `/tmp/am-bf9f2b42/outcomes.sh`, `/tmp/am-bf9f2b42/subset.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: `/tmp/am-bf9f2b42/before.log` (raw `-v` log of the pre-change file run), `/tmp/am-bf9f2b42/before.outcomes` (sorted `nodeid OUTCOME` lines), `/tmp/am-bf9f2b42/cards.ids`, `/tmp/am-bf9f2b42/milestone_board.ids`, `/tmp/am-bf9f2b42/resume_board.ids` (one node id per line of every collected test that uses that fixture, directly or transitively), and the two helper scripts `outcomes.sh <log>` and `subset.sh <ids> <outcomes>` used by Tasks 2-5.

- [ ] **Step 1: Confirm the seam and the fixture exist**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && grep -n '^run_brd' src/agent_manager/board.py && grep -n -E '^class FakeBoard|^def fake_board' tests/conftest.py && git status --porcelain
```
Expected: `168:run_brd: Callable[`, `135:class FakeBoard:`, `401:def fake_board(...`, and an empty porcelain. If either definition is missing, STOP and report "board.run_brd / fake_board missing on this branch"; do not build them.

- [ ] **Step 2: Write the scratch helpers**

Create `/tmp/am-bf9f2b42/list_fixture_tests.py`:
```python
"""List every collected tests/test_cli.py test that uses each converted fixture."""

import sys

import pytest

FIXTURES = ("cards", "milestone_board", "resume_board")
OUT = "/tmp/am-bf9f2b42"


class Lister:
    def pytest_collection_finish(self, session):
        for name in FIXTURES:
            ids = [
                item.nodeid
                for item in session.items
                if name in getattr(item, "fixturenames", ())
            ]
            with open(f"{OUT}/{name}.ids", "w", encoding="utf-8") as fh:
                fh.write("".join(f"{nodeid}\n" for nodeid in ids))


sys.exit(pytest.main(["tests/test_cli.py", "--collect-only", "-q"], plugins=[Lister()]))
```

Create `/tmp/am-bf9f2b42/outcomes.sh`:
```bash
#!/usr/bin/env bash
# Normalise a `pytest -v` log into sorted "nodeid OUTCOME" lines.
grep -E '^tests/test_cli\.py::' "$1" | sed -E 's/ +\[ *[0-9]+%\]$//' | sort
```

Create `/tmp/am-bf9f2b42/subset.sh`:
```bash
#!/usr/bin/env bash
# Print the lines of outcomes file $2 whose nodeid is listed in ids file $1.
awk 'NR == FNR { want[$0] = 1; next }
     { id = $0; sub(/ [A-Z]+$/, "", id); if (id in want) print }' "$1" "$2"
```

Run: `mkdir -p /tmp/am-bf9f2b42` before writing them if the directory does not exist.

- [ ] **Step 3: List the tests per fixture**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run python /tmp/am-bf9f2b42/list_fixture_tests.py > /dev/null; wc -l /tmp/am-bf9f2b42/*.ids; sort -u /tmp/am-bf9f2b42/*.ids | wc -l
```
Expected: three non-empty files; the unique total is about 80 (the spec's signature count; parametrized tests can push it higher). Record the three counts and the unique total for Task 5.

- [ ] **Step 4: Confirm no fixture-using test also seeds real brd (Review Focus 1)**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && grep -n -E '_add_card\(|_block\(|\["brd"' tests/test_cli.py
```
Expected: besides the helper definitions (:1210-1215, :2554-2561), `project` (:1239) and the three fixture bodies, the only call sites are :1413-1416, :2923, :2968, :2989-2996, and the `argv=["brd", ...]` literals inside `BoardError(...)` constructions (:2193, :3286, :4665, :7036). Open each of :1409, :2917, :2963, :2988 and confirm their signatures request `project` but none of `cards`, `milestone_board`, `resume_board`. Also confirm none of those four test names appear in any `/tmp/am-bf9f2b42/*.ids`:
```bash
grep -E 'test_drive_subtask_drives_two_subtasks_under_one_store_and_run|test_a_story_cycle_from_the_board_is_an_envelope_naming_both_stories|test_a_story_cycle_in_the_census_is_named_as_a_trail_by_the_dag_check|test_a_story_blocked_by_two_stories_dry_runs_on_a_merged_base' /tmp/am-bf9f2b42/*.ids
```
Expected: no output. If any line matches, STOP and report it: that test mixes real-brd seeding with a converted fixture and is outside this card.

- [ ] **Step 5: Record the baseline**

Run (brd and git must be on PATH; this takes several minutes):
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest tests/test_cli.py -v > /tmp/am-bf9f2b42/before.log 2>&1; bash /tmp/am-bf9f2b42/outcomes.sh /tmp/am-bf9f2b42/before.log > /tmp/am-bf9f2b42/before.outcomes; wc -l < /tmp/am-bf9f2b42/before.outcomes; grep -E '^=+ .* in [0-9.]+s' /tmp/am-bf9f2b42/before.log | tail -n 1
```
Expected: a line count close to 302 minus deselected tests, and a summary line such as `===== N passed, M deselected in T.TTs (h:mm:ss) =====`. Write down that summary line verbatim; it is the baseline wall time and count. If any fixture-using test is `SKIPPED` because `brd` is absent, STOP: the comparison is meaningless without brd installed.

No commit in this task (nothing in the repo changed).

---

### Task 2: Convert `cards`

**Files:**
- Modify: `tests/test_cli.py:1253-1259` (the `cards` fixture)
- Test: existing tests listed in `/tmp/am-bf9f2b42/cards.ids`, chiefly `tests/test_cli.py::test_run_card_really_moves_the_card_on_the_board` (:1329). Tier: `git` (no marker change, per spec).

**Interfaces:**
- Consumes: `fake_board` fixture -> `FakeBoard`; `FakeBoard.add_card(title, *, parent_id=None, card_id=None, status="todo", description=None, blocked_by=(), created_at=None, updated_at=None) -> str` (tests/conftest.py:170-208). Scratch files from Task 1.
- Produces: `cards(project, fake_board) -> dict[str, str]` with keys `"milestone"`, `"story"`, `"subtask"`, ids of cards that exist in the `FakeBoard` installed for that test.

- [ ] **Step 1: Route reads to FakeBoard without moving the seeding (RED)**

In `tests/test_cli.py`, change only the `cards` signature from:
```python
@pytest.fixture
def cards(project) -> dict[str, str]:
```
to:
```python
@pytest.fixture
def cards(project, fake_board) -> dict[str, str]:
```
Leave the body (`_add_card(...)` calls) as it is for now. Board reads in the tests now hit the empty FakeBoard while the cards still go to real brd, which proves the tests are driven through the seam.

- [ ] **Step 2: Run the anchor test to verify it fails**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest "tests/test_cli.py::test_run_card_really_moves_the_card_on_the_board" -v
```
Expected: FAIL, with the output naming `CardNotFoundError` or `no card, issue, or document with id` (the subtask id exists only in real brd). If it PASSES, the test is not reading through `board.run_brd`: STOP and report it.

- [ ] **Step 3: Seed the chain into FakeBoard (GREEN)**

Replace the whole `cards` fixture with:
```python
@pytest.fixture
def cards(project, fake_board) -> dict[str, str]:
    """A milestone -> story -> subtask chain, the shape `run --card` requires.

    The cards live in the in-memory `fake_board` (test-tier V5); `project` still
    supplies the real git repo the CLI runs in.
    """
    milestone = fake_board.add_card("Milestone 1: walking skeleton")
    story = fake_board.add_card("The CLI: run, status, logs, resume", parent_id=milestone)
    subtask = fake_board.add_card("Add run --card end to end", parent_id=story)
    return {"milestone": milestone, "story": story, "subtask": subtask}
```

- [ ] **Step 4: Run the anchor test to verify it passes (Review Focus 5)**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest "tests/test_cli.py::test_run_card_really_moves_the_card_on_the_board" -v
```
Expected: PASS (it reads `board.show(...).status` after `run_card` set it, so this pins that FakeBoard writes are visible to later reads).

- [ ] **Step 5: Run every `cards` test and diff against the baseline**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && xargs -d '\n' -a /tmp/am-bf9f2b42/cards.ids uv run pytest -v > /tmp/am-bf9f2b42/cards.after.log 2>&1; bash /tmp/am-bf9f2b42/outcomes.sh /tmp/am-bf9f2b42/cards.after.log > /tmp/am-bf9f2b42/cards.after.outcomes; diff <(bash /tmp/am-bf9f2b42/subset.sh /tmp/am-bf9f2b42/cards.ids /tmp/am-bf9f2b42/before.outcomes) /tmp/am-bf9f2b42/cards.after.outcomes && echo SAME
```
Expected: `SAME`. If a line differs: an `AssertionError: FakeBoard does not answer argv [...]` is a finding to report (do not extend FakeBoard, do not edit the test); an ordering difference is fixed in the fixture seeding only (e.g. explicit `created_at=`), never in the test.

- [ ] **Step 6: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && git add tests/test_cli.py && git commit -m "test: seed the cards fixture into FakeBoard instead of real brd"
```

---

### Task 3: Convert `milestone_board`

**Files:**
- Modify: `tests/test_cli.py:2572-2602` (the `milestone_board` fixture). `_block` (:2554-2561) and `M2_SHAPE` (:2564-2569) stay unchanged.
- Test: existing tests listed in `/tmp/am-bf9f2b42/milestone_board.ids`, chiefly `tests/test_cli.py::test_the_milestone_dry_run_stacks_each_story_on_the_previous_ones_tip` (:2692). Tier: `git` (no marker change).

**Interfaces:**
- Consumes: `fake_board` / `FakeBoard.add_card(...)` as in Task 2; `M2_SHAPE: tuple[tuple[str, str, tuple[str, ...]], ...]` (tests/test_cli.py:2564).
- Produces: `milestone_board(project, fake_board) -> dict[str, Any]` with keys `"milestone": str`, `"stories": dict[str, str]` (keys `"A"`, `"B"`, `"C"`), `"subtasks": dict[str, list[str]]` (same keys, each list in chain order), `"titles": dict[str, str]` (card id -> title, every story and subtask).

- [ ] **Step 1: Route reads to FakeBoard without moving the seeding (RED)**

Change only the signature from:
```python
@pytest.fixture
def milestone_board(project) -> dict[str, Any]:
```
to:
```python
@pytest.fixture
def milestone_board(project, fake_board) -> dict[str, Any]:
```
Leave the body (real `_add_card`/`_block`) unchanged.

- [ ] **Step 2: Run the anchor test to verify it fails**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest "tests/test_cli.py::test_the_milestone_dry_run_stacks_each_story_on_the_previous_ones_tip" -v
```
Expected: FAIL at `assert result.exit_code == 0, result.output`, with the output naming the milestone id as not found (FakeBoard is empty). If it PASSES, STOP and report that the dry run does not read through `board.run_brd`.

- [ ] **Step 3: Seed milestone 2 into FakeBoard with blocking edges at creation (GREEN)**

Replace the whole `milestone_board` fixture (decorator through `return`) with:
```python
@pytest.fixture
def milestone_board(project, fake_board) -> dict[str, Any]:
    """A board shaped like milestone 2, next to a decoy milestone, seeded into
    the in-memory `fake_board` (test-tier V5).

    B is blocked by A and C by B. Each story's subtasks are chained the same
    way. FakeBoard answers no `brd block`, so every edge is seeded with
    `blocked_by` when the card is created, and the census order does not
    depend on creation timestamps. The decoy root shares the word "skeleton",
    so only a longer substring names milestone 2.
    """
    fake_board.add_card("Milestone 1: walking skeleton")
    milestone = fake_board.add_card("Milestone 2: make the skeleton real")
    stories: dict[str, str] = {}
    subtasks: dict[str, list[str]] = {}
    titles: dict[str, str] = {}
    previous_story: str | None = None
    for key, story_title, subtask_titles in M2_SHAPE:
        story = fake_board.add_card(
            story_title,
            parent_id=milestone,
            blocked_by=[previous_story] if previous_story is not None else [],
        )
        titles[story] = story_title
        chain: list[str] = []
        for subtask_title in subtask_titles:
            subtask = fake_board.add_card(
                subtask_title, parent_id=story, blocked_by=chain[-1:]
            )
            titles[subtask] = subtask_title
            chain.append(subtask)
        stories[key] = story
        subtasks[key] = chain
        previous_story = story
    return {"milestone": milestone, "stories": stories, "subtasks": subtasks, "titles": titles}
```
(`chain[-1:]` is `[]` for the first subtask and `[previous subtask]` after that, matching the old `if chain: _block(...)`.)

- [ ] **Step 4: Run the anchor tests to verify they pass (Review Focus 2, 3, 4)**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest "tests/test_cli.py::test_the_milestone_dry_run_stacks_each_story_on_the_previous_ones_tip" "tests/test_cli.py::test_an_unknown_milestone_is_an_envelope" "tests/test_cli.py::test_readers_never_take_a_lease_or_a_lock" -v
```
Expected: all three PASS. The first pins level order `[[A], [B], [C]]` (derived blocking) and runs under `_forbid_writes` with a porcelain check (seeding is not a write).

- [ ] **Step 5: Run every `milestone_board` test and diff against the baseline**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && xargs -d '\n' -a /tmp/am-bf9f2b42/milestone_board.ids uv run pytest -v > /tmp/am-bf9f2b42/milestone_board.after.log 2>&1; bash /tmp/am-bf9f2b42/outcomes.sh /tmp/am-bf9f2b42/milestone_board.after.log > /tmp/am-bf9f2b42/milestone_board.after.outcomes; diff <(bash /tmp/am-bf9f2b42/subset.sh /tmp/am-bf9f2b42/milestone_board.ids /tmp/am-bf9f2b42/before.outcomes) /tmp/am-bf9f2b42/milestone_board.after.outcomes && echo SAME
```
Expected: `SAME`. Same handling as Task 2 Step 5 for any difference: unanswered argv -> report; order drift -> fix seeding (explicit `created_at=` or `blocked_by=`), never the test.

- [ ] **Step 6: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && git add tests/test_cli.py && git commit -m "test: seed milestone_board into FakeBoard, blocking edges at creation"
```

---

### Task 4: Convert `resume_board`

**Files:**
- Modify: `tests/test_cli.py:4951-4969` (the `resume_board` fixture)
- Test: existing tests listed in `/tmp/am-bf9f2b42/resume_board.ids`, chiefly `tests/test_cli.py::test_a_milestone_that_escalated_resumes_under_its_own_run_id` (:5044). Tier: `git` (no marker change).

**Interfaces:**
- Consumes: `fake_board` / `FakeBoard.add_card(...)` as in Task 2.
- Produces: `resume_board(project, fake_board) -> dict[str, str]` with keys `"milestone"`, `"story_a"`, `"a1"`, `"a2"`, `"story_b"`, `"b1"`; consumed unchanged by `_escalate_milestone(project, shape)` (tests/test_cli.py:4972).

- [ ] **Step 1: Route reads to FakeBoard without moving the seeding (RED)**

Change only the signature from:
```python
@pytest.fixture
def resume_board(project) -> dict[str, str]:
```
to:
```python
@pytest.fixture
def resume_board(project, fake_board) -> dict[str, str]:
```

- [ ] **Step 2: Run the anchor test to verify it fails**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest "tests/test_cli.py::test_a_milestone_that_escalated_resumes_under_its_own_run_id" -v
```
Expected: FAIL inside `_escalate_milestone` or at the first payload assertion, with the milestone id reported as not found by FakeBoard. If it PASSES, STOP and report it.

- [ ] **Step 3: Seed milestone 4 into FakeBoard (GREEN)**

Replace the whole `resume_board` fixture (decorator through `return`) with:
```python
@pytest.fixture
def resume_board(project, fake_board) -> dict[str, str]:
    """Milestone 4: story A (a1 then a2) and story B (b1), B blocked by A.

    Seeded into the in-memory `fake_board` (test-tier V5) in the same order the
    real board created them; each blocking edge is set with `blocked_by` at
    creation, since FakeBoard answers no `brd block`.
    """
    milestone = fake_board.add_card("Milestone 4: resume")
    story_a = fake_board.add_card("Story A: first", parent_id=milestone)
    a1 = fake_board.add_card("a1: first of A", parent_id=story_a)
    a2 = fake_board.add_card("a2: second of A", parent_id=story_a, blocked_by=[a1])
    story_b = fake_board.add_card(
        "Story B: second", parent_id=milestone, blocked_by=[story_a]
    )
    b1 = fake_board.add_card("b1: only of B", parent_id=story_b)
    return {
        "milestone": milestone,
        "story_a": story_a,
        "a1": a1,
        "a2": a2,
        "story_b": story_b,
        "b1": b1,
    }
```

- [ ] **Step 4: Run the anchor test to verify it passes (Review Focus 2)**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest "tests/test_cli.py::test_a_milestone_that_escalated_resumes_under_its_own_run_id" -v
```
Expected: PASS, including `payload["completed"] == [shape["a2"], shape["b1"]]` (creation/blocking order preserved).

- [ ] **Step 5: Run every `resume_board` test and diff against the baseline**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && xargs -d '\n' -a /tmp/am-bf9f2b42/resume_board.ids uv run pytest -v > /tmp/am-bf9f2b42/resume_board.after.log 2>&1; bash /tmp/am-bf9f2b42/outcomes.sh /tmp/am-bf9f2b42/resume_board.after.log > /tmp/am-bf9f2b42/resume_board.after.outcomes; diff <(bash /tmp/am-bf9f2b42/subset.sh /tmp/am-bf9f2b42/resume_board.ids /tmp/am-bf9f2b42/before.outcomes) /tmp/am-bf9f2b42/resume_board.after.outcomes && echo SAME
```
Expected: `SAME`. Differences are handled as in Task 2 Step 5.

- [ ] **Step 6: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && git add tests/test_cli.py && git commit -m "test: seed resume_board into FakeBoard, blocking edges at creation"
```

---

### Task 5: Whole-file comparison, full suite, and timing record

**Files:**
- Read only: `tests/test_cli.py`, scratch logs in `/tmp/am-bf9f2b42/`
- No repo file changes in this task.

**Interfaces:**
- Consumes: `/tmp/am-bf9f2b42/before.log`, `/tmp/am-bf9f2b42/before.outcomes`, `outcomes.sh` from Task 1; the converted fixtures from Tasks 2-4.
- Produces: `/tmp/am-bf9f2b42/after.log`, `/tmp/am-bf9f2b42/after.outcomes`, and a comment on card bf9f2b42 recording before/after wall time.

- [ ] **Step 1: Confirm the diff touches only the three fixtures and the helpers survive**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && git diff --stat HEAD~3 -- . ':!docs' && git diff HEAD~3 -- tests/test_cli.py | grep -E '^[-+]' | grep -v -E '^(\+\+\+|---)' | grep -E 'assert|pytest\.mark|requires_(git|brd)|^-def _add_card|^-def _block|^-def project' ; grep -n -E '^def _add_card|^def _block|^def project' tests/test_cli.py
```
Expected: the stat lists only `tests/test_cli.py`; the middle grep prints nothing (no assertion, marker or helper line added or removed); the last grep prints the three definitions (`_add_card`, `project`, `_block`) still present.

- [ ] **Step 2: Run the whole file verbose and record the new time**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest tests/test_cli.py -v > /tmp/am-bf9f2b42/after.log 2>&1; bash /tmp/am-bf9f2b42/outcomes.sh /tmp/am-bf9f2b42/after.log > /tmp/am-bf9f2b42/after.outcomes; grep -E '^=+ .* in [0-9.]+s' /tmp/am-bf9f2b42/before.log | tail -n 1; grep -E '^=+ .* in [0-9.]+s' /tmp/am-bf9f2b42/after.log | tail -n 1
```
Expected: two summary lines with identical pass/fail/skip/deselect counts; the second wall time lower than the first.

- [ ] **Step 3: Diff every test's outcome against the baseline**

Run:
```bash
diff /tmp/am-bf9f2b42/before.outcomes /tmp/am-bf9f2b42/after.outcomes && echo SAME
```
Expected: `SAME` (every test in the file, not only the fixture users, has the same outcome). Any difference is a finding: fix seeding or report it; never edit an assertion.

- [ ] **Step 4: Run the full suite**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-cards-milestone-bf9f2b42 && uv run pytest
```
Expected: exit 0, no failures or errors, and no `DATA-DIR GUARD FAILED` line.

- [ ] **Step 5: Record the timing on the card**

Run (from the main checkout, where the brd board for this project resolves):
```bash
cd /home/paulomtts/Code/agent-manager && { echo "tests/test_cli.py wall time, uv run pytest tests/test_cli.py -v"; echo "before (real brd fixtures): $(grep -E '^=+ .* in [0-9.]+s' /tmp/am-bf9f2b42/before.log | tail -n 1)"; echo "after (cards/milestone_board/resume_board on FakeBoard): $(grep -E '^=+ .* in [0-9.]+s' /tmp/am-bf9f2b42/after.log | tail -n 1)"; echo "per-test outcomes identical (diff of before/after -v outcome lists is empty); project still runs brd init per test (out of scope)."; } | brd comment add bf9f2b42-ae9d-4201-88da-2d43974fb55a - --author am
```
Expected: a JSON envelope with `"ok": true`. If brd refuses (e.g. board not found from this directory), include the two summary lines in the hand-off report instead and say the card comment was not written.

No commit in this task (no repo file changed).
