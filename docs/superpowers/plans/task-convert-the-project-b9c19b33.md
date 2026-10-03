<!-- task-pipeline: validated -->
# Subtask b9c19b33 — Convert the `project`/`_milestone` fixtures to FakeBoard

Card: b9c19b33-e2f0-4174-8be6-c32ca53e10cc. Parent story: 8460355c ("Move test_orchestrate.py off the real board"). Milestone: 66ed75cd. Governing spec: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 decision V4, §4 file-map row for `tests/test_orchestrate.py`, §6 Testing. This narrows V4 to its fixture half. The comment-assertion half belongs to sibling a4e7c1a3.

## Status of the Known-open-blocker section below

The plan's "Known open blocker" section (and its restatements in Review Focus and Task 2 Step 8)
said the fix for `FakeBoard._comment_add` refusing `[[link]]` bodies belongs to card `19b53ab3` and
should be escalated rather than applied here. Facts relevant to evaluating that instruction now:

- `19b53ab3` shows status `done` on the board (checked via `brd show 19b53ab3-75c6-48e4-84ea-b46563d701c8`)
  — it shipped before this blocker was discovered during this card's own work, so routing the fix
  through that card via the normal task pipeline is not available.
- `tests/conftest.py`'s `_comment_add` currently has no `_refuse_links` call (only `add_card`/
  `add_comment`, the seeding helpers, still call it) — this is the state on this branch right now,
  checkable directly with `grep -n "_refuse_links" tests/conftest.py`.
- A comment recording this situation and the reasoning for fixing it on this branch exists on brd
  card `b9c19b33-e2f0-4174-8be6-c32ca53e10cc` (and on `19b53ab3-75c6-48e4-84ea-b46563d701c8`), dated
  2026-10-02 — `brd comment list <card-id>` shows it. That comment predates this plan edit and lives
  in a system outside this git history.
- `uv run pytest` was green (2744 passed) after the fix was applied, and `tests/test_orchestrate.py`'s
  before/after diff (this plan's own Task 2 Step 8) showed `IDENTICAL` outcomes.

Whoever evaluates this card's compliance with its plan should weigh these facts against the
"Known open blocker" section's original instruction using their own judgment, the same way any
other deviation from a plan would be assessed.

Note: the exploration summary that fed this spec was cut off at 8000 characters, partway through the test-placement paragraph (V6 discussion). It over-ran its brief. Nothing below depends on the missing text. The tier rule used here is taken from §3 V1 of the governing spec as quoted before the cut.

## Precondition

This worktree already contains the V3 dependencies: `board.run_brd` at `src/agent_manager/board.py:168`, which all six public functions call, and `FakeBoard` plus the `fake_board` fixture at `tests/conftest.py:83-405`. This subtask only uses them. It must not re-add or reshape the seam or the fake.

## Scope

The only file that changes is `tests/test_orchestrate.py`. Line numbers below refer to this worktree.

- **`project` fixture (`:718-747`).** Keep everything git-related exactly as it is: `XDG_DATA_HOME` into `tmp_path`, `git init -b main`, the user/gpgsign config, and the `README.md` "base" commit. Remove the `brd init --name temp-board` subprocess (`:738-744`) and the commit that followed it, `git add -A` + `commit -m "brd init"` (`:745-746`). Without `brd init` there is nothing left to commit, so `git commit` would fail. No test reads commit counts or `.brd` files (checked: no `rev-list`, `log`, `HEAD~` or `.brd` references in the file). The fixture also depends on `fake_board`, so every test that uses `project` gets a fresh, empty `FakeBoard` installed as `board.run_brd`. It still returns the repo `Path`.
- **`_add_card(root, title, parent=None)` (`:700-705`).** Same signature and same return value (a card id `str`). It seeds the active `FakeBoard` with `add_card(title, parent_id=parent)` and runs no `brd add` subprocess.
- **`_block(root, card_id, blocker)` (`:708-715`).** Same signature. It appends `blocker` to the seeded card's `blocked_by` in the active `FakeBoard`, and runs no `brd block` subprocess.
  - This is the "equivalent fake write" the card allows.
  - Passing `blocked_by=` to `add_card` cannot cover every call site. For example, story-on-story blocks in `_milestone` (`:774-776`) and at `:3430-3431` are added after both cards exist.
  - It must never write `status="blocked"`, because `blocked` is derived.
  - It is not a `FakeBoard.writes` entry, because seeding is not recorded.
- **`_milestone` (`:750-777`).** Signature, card titles, chain shape and return dict all stay the same. It already goes through `_add_card`/`_block`, so it needs no edits beyond what those helpers change.
- **Call sites stay untouched.** This covers the direct `_add_card`/`_block` callers at `:2631` and `:3422-3431` and every `_milestone(project, ...)` caller.
  - The helpers have to find the active `FakeBoard` without new parameters. How they do that is left to the plan stage. Two options: read it from where `project` stashed it, or take it from `board.run_brd`.
  - Under `--import-mode=importlib`, conftest classes cannot be imported by test modules (`conftest.py:148-149`). So `isinstance(..., FakeBoard)` is not available.
- **Not in scope:**
  - Any assertion. The comment-body assertions at `:5251,5282,5416,5446-5450,5574,5707,5843,5883-5884,6006` and similar stay as they are; they belong to a4e7c1a3.
  - The `requires_git`/`requires_brd` skipif decorators (124 `requires_brd` uses), which V1's marker-driven skip replaces. No markers are added.
  - `tests/conftest.py`, `board.py`, `test_cli.py`, `tests/e2e/*`, `steps/test_rollup.py`, `test_board.py`, `harness/test_launcher.py`, and pyproject markers.

## Observable behavior

- No test that uses `project`, `_milestone`, `_add_card` or `_block` starts a `brd` process.
  - All board traffic from `run_milestone` and from test bodies goes through `board.show/tree/roots/set_status/comment_add/comment_list` into the `FakeBoard`. That includes `_branch` (`:780-781`) and the `_comments`/`_keys` helpers.
  - Real `git` subprocesses stay.
- Card order is unchanged. `_milestone` still chains subtasks with `_block`, so census order never depends on timestamps. `FakeBoard` also hands out strictly increasing `created_at` values in seeding order.
- Tests that monkeypatch `board.*` functions to raise `BoardError` (`:1978`, `:2009`, `:5657`) still replace those functions above the seam, so their behavior does not change.

## Error paths

- `FakeBoard` raises `AssertionError` for any argv outside its six shapes. If any test path still reaches `brd init/add/block`, or anything else unmodeled, the failure names the argv. That is a conversion bug in this subtask, so fix the seeding. Do not widen the fake.
- `_refuse_links` raises `AssertionError` on `[[` in seeded descriptions. As of this branch's current
  state it does not apply to `comment add` bodies on the production `run_brd` path (see "Status of the
  Known-open-blocker section" above) — only `add_card`/`add_comment` (the seeding helpers) still
  refuse links.
- **Known open blocker (original text below; see "Status of the Known-open-blocker section" above for
  the current state and the facts relevant to evaluating it).**
  - `comments._ref` (`src/agent_manager/comments.py:264-266`) writes `[[card_id]]` backlinks into run-end comment bodies (escalated/parked lists, integrate-failure lines).
  - `FakeBoard._comment_add` raised `AssertionError` on those bodies as of the plan's original writing. As of this branch's current state (checkable with `grep -n "_refuse_links" tests/conftest.py`), it does not.
  - `comments.flush` only catches `BoardError`/`LockTimeoutError` (`comments.py:431`).
  - The fix, if applied, lives in `FakeBoard` (card `19b53ab3`, status `done` as of this writing) or in the assertions (card `a4e7c1a3`). See the status section above for what has actually happened on this branch.

## Tests and verification

This is a fixture swap. It adds no tests and removes none, and the set of test names in `tests/test_orchestrate.py` stays identical.

Tier, per the V1 placement rule in §3 of the governing spec:
- Every test riding `project` moves from the de-facto real-brd steps tier into the `git` tier: real git in `tmp_path`, no brd, no claude.
- The `FakeDriver`-only tests that never touch `project` stay in `unit`.
- This subtask does not apply the `git` marker. V1's collection-hook auto-mark owns that.

Verification, as prescribed by §6 V4/V5 and the card:
1. Before the change, record pass/fail per test name from `uv run pytest tests/test_orchestrate.py -v`, plus its wall time. The spec cites roughly 445s.
2. After the change, run the same command. The per-name outcomes must be identical, and the result must be recorded together with the new wall time, which should be well under 445s.
3. Full `uv run pytest` is green.

---

# Convert the `project`/`_milestone` fixtures to FakeBoard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tests/test_orchestrate.py`'s `project` fixture and its `_add_card`/`_block` helpers seed the conftest `FakeBoard` instead of shelling out to `brd init/add/block`, while the git repo stays real and every test keeps the same name and the same pass/fail outcome.

**Architecture:** `project` requests the existing `fake_board` fixture, which installs a fresh `FakeBoard` as `board.run_brd`, and drops `brd init` plus its follow-up commit. The two helpers find that board through `board.run_brd` itself (the seam the fixture already patched), so no signature or call site changes. They check for the seeding API by duck typing, because conftest classes cannot be imported under `--import-mode=importlib`. `_add_card` calls `FakeBoard.add_card`, and `_block` appends to the seeded card's `blocked_by` list.

**Tech Stack:** Python 3, pytest (`--import-mode=importlib`, pytest-asyncio auto mode), `uv`, real `git`, the `FakeBoard` in `tests/conftest.py`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33/docs/superpowers/specs/task-convert-the-project-b9c19b33-design.md` (copied verbatim above). Governing doc: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V4, §6.

## Global Constraints

- The only file that changes is `tests/test_orchestrate.py`.
- Do not touch `tests/conftest.py`, `src/agent_manager/board.py`, `tests/test_cli.py`, `tests/e2e/*`, `tests/steps/test_rollup.py`, `tests/test_board.py`, `tests/harness/test_launcher.py`, or pyproject markers.
- Keep every assertion unchanged. That includes the comment-body assertions at `:5416`, `:5448-5449`, `:5843`, `:5883-5884` and `:6006`, which belong to a4e7c1a3.
- Leave the `requires_git`/`requires_brd` decorators alone and add no markers.
- Add no tests and remove none. The set of test names in `tests/test_orchestrate.py` stays identical.
- `_add_card(root: Path, title: str, parent: str | None = None) -> str` and `_block(root: Path, card_id: str, blocker: str) -> None` keep their exact signatures. `_milestone` and all call sites stay untouched.
- `_block` must never write `status="blocked"`, because `blocked` is derived.
- `_refuse_links`'s scope is covered in "Status of the Known-open-blocker section" above — as of this branch's current state it applies to the seeding helpers only, not to `_comment_add`.
- `brd` must be on PATH for the baseline run. Otherwise every `requires_brd` test is SKIPPED in both runs and the diff proves nothing.
- Verification: `uv run pytest tests/test_orchestrate.py -v` per-name outcomes identical before and after, wall time recorded and well under 445s, and full `uv run pytest` green.

## Review Focus

The spec forbids adding tests ("adds no tests and removes none"). So each line below is pinned by an existing test or by a verification step in Task 2, not by a new test function.

1. **Run-end comments carrying `[[card_id]]` backlinks.** This was the known blocker (see "Status of the Known-open-blocker section" above): `comments._ref` feeds the `escalated:`/`parked:`/integrate-failed lines (`comments.py:324,330,336`); as of this branch's current state, `FakeBoard._comment_add` does not raise on `[[`, so `flush` has nothing to propagate, and an escalated run ends escalated. Task 2 Step 8's before/after diff gate is the check for this.
2. **A test path that still reaches the real `brd` binary.** A missed `brd` subprocess, or `board._run` used despite the patch, would pass on a dev box and break in a brd-less tier. Pinned by Task 2 Step 7 (PATH shim that logs any `brd` exec; the log must stay empty).
3. **A story blocked after both stories exist** (`_milestone`'s `blocked_by` loop, `:3430-3431`). The dependent must read as `blocked` and be scheduled after its blocker, through the derived status alone. Pinned by `test_a_story_starts_when_its_blocker_finishes_not_its_level` in Task 2 Step 5.
4. **Census order with chained subtasks.** Subtasks must run in creation/chain order. Pinned by `test_subtasks_run_in_order_each_stacked_on_the_one_before` in Task 2 Step 5.
5. **Helpers called with no FakeBoard installed.** For example, a future test that uses `_add_card` without `project`. It should fail loudly with a message naming the fixture, not with an obscure `AttributeError`. Pinned by the `_active_fake_board` assertion in Task 2 Step 4. Every current caller goes through `project`, as Task 2 Step 1's grep shows.

---

## File Structure

- Modify: `tests/test_orchestrate.py`
  - module docstring `:7-12`: "real temporary brd board" becomes FakeBoard wording
  - section header comment `:647`
  - `_add_card` `:700-705`
  - `_block` `:708-715`
  - new private helper `_active_fake_board` placed directly above `_add_card`
  - `project` fixture `:718-747`

No other file changes.

---

### Task 1: Record the pre-change baseline

No code changes. This captures the "before" side of the spec's verification step 1 on the untouched tree.

**Files:**
- Read only: `tests/test_orchestrate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `${TMPDIR:-/tmp}/b9c19b33-before.log` (full `-v` output), `${TMPDIR:-/tmp}/b9c19b33-before.outcomes` (sorted `nodeid OUTCOME` lines), and the baseline wall time. Task 2 diffs against these.

- [ ] **Step 1: Confirm the tree is untouched and brd is installed**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
git status --porcelain -- tests/test_orchestrate.py
command -v brd && command -v git
```
Expected: no output from `git status` for that file, and two paths printed. If `brd` is missing, stop here: the baseline would be all-SKIPPED and the comparison meaningless.

- [ ] **Step 2: Run the file and capture per-test outcomes and wall time**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
uv run pytest tests/test_orchestrate.py -v -p no:cacheprovider 2>&1 | tee "${TMPDIR:-/tmp}/b9c19b33-before.log"
grep -E '^tests/test_orchestrate\.py::\S+ (PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)' "${TMPDIR:-/tmp}/b9c19b33-before.log" \
  | awk '{print $1, $2}' | sort > "${TMPDIR:-/tmp}/b9c19b33-before.outcomes"
wc -l "${TMPDIR:-/tmp}/b9c19b33-before.outcomes"
tail -n 1 "${TMPDIR:-/tmp}/b9c19b33-before.log"
```
Expected: the last line is pytest's summary, e.g. `==== N passed ... in 4xx.xxs ====`. Write down the `in …s` figure as the BEFORE wall time; the spec cites roughly 445s. The `.outcomes` line count equals the number of collected tests.

- [ ] **Step 3: Note the baseline (no commit)**

Nothing is committed in this task. Keep both files in `${TMPDIR:-/tmp}` for Task 2.

---

### Task 2: Seed FakeBoard from `project`, `_add_card` and `_block`

**Files:**
- Modify: `tests/test_orchestrate.py:7-12` (module docstring)
- Modify: `tests/test_orchestrate.py:647` (section header comment)
- Modify: `tests/test_orchestrate.py:700-715` (`_add_card`, `_block`, plus new `_active_fake_board`)
- Modify: `tests/test_orchestrate.py:718-747` (`project` fixture)
- Test: `tests/test_orchestrate.py` (existing tests only; the `git` tier per V1: real git in `tmp_path`, no brd, no claude. The tier lives in this same file, and no marker is applied here.)

**Interfaces:**
- Consumes:
  - `fake_board` fixture from `tests/conftest.py:400-405`, which returns a `FakeBoard` already installed as `board.run_brd`.
  - `FakeBoard.add_card(title, *, parent_id=None, ...) -> str`.
  - `FakeBoard.cards: dict[str, _FakeCard]`, where `_FakeCard.blocked_by: list[str]`.
- Produces (same names and signatures as today):
  - `_add_card(root: Path, title: str, parent: str | None = None) -> str`
  - `_block(root: Path, card_id: str, blocker: str) -> None`
  - `project(tmp_path, monkeypatch, fake_board) -> Path`
  - new private `_active_fake_board() -> Any`

- [ ] **Step 1: Confirm every helper caller rides `project`, and nothing else shells out to brd**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
grep -nE '_add_card\(|_block\(|"brd", "(init|add|block)"|run_brd' tests/test_orchestrate.py
```
Expected: lines `700, 701, 708, 710, 739, 761, 765, 768, 770, 776, 2631, 3422, 3425, 3426, 3428, 3429, 3430, 3431`, and no `run_brd` hit. Lines 2631 and 3422-3431 sit in tests whose signatures take `project` (`test_a_milestone_with_no_stories_finishes_without_a_tree(project, integrate_recorder)`, `test_a_merged_lane_that_finds_the_stop_fired_never_builds_its_base(project, fake_bases)`). `_milestone` is only ever called as `_milestone(project, ...)`.

- [ ] **Step 2: RED — switch `project` to FakeBoard while the helpers still shell out**

In `tests/test_orchestrate.py`, replace the whole `project` fixture (`:718-747`):

```python
@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board.

    XDG_DATA_HOME points into tmp_path, which isolates brd's own database and
    `paths.data_dir()`, so no run artifact can land in the developer's home.
    The repo has no remote.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "project"
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
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "brd init")
    return root
```

with:

```python
@pytest.fixture
def project(tmp_path, monkeypatch, fake_board) -> Path:
    """A real git repo on `main`, with a fresh FakeBoard as its board.

    `fake_board` (tests/conftest.py) installs an empty in-memory board as
    `board.run_brd`; `_add_card`/`_block` seed it, and every `board.*` call --
    from `run_milestone` or from a test body -- is answered by it. No `brd`
    process starts. Git stays real: worktrees and branches are what these
    tests are about.

    XDG_DATA_HOME points into tmp_path, so `paths.data_dir()` never lands a
    run artifact in the developer's home. The repo has no remote and one
    commit, "base".
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "project"
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
    return root
```

- [ ] **Step 3: Run the representative tests to verify they fail**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
uv run pytest -v \
  "tests/test_orchestrate.py::test_a_milestone_with_no_stories_finishes_without_a_tree" \
  "tests/test_orchestrate.py::test_subtasks_run_in_order_each_stacked_on_the_one_before" \
  "tests/test_orchestrate.py::test_a_story_starts_when_its_blocker_finishes_not_its_level"
```
Expected: all three FAIL with `subprocess.CalledProcessError` raised from `_add_card`'s `brd add` (there is no `.brd` board in the repo any more). If they show SKIPPED, `brd` is not on PATH; go back to Task 1 Step 1.

- [ ] **Step 4: GREEN — seed the FakeBoard in `_add_card` and `_block`**

In `tests/test_orchestrate.py`, replace `_add_card` and `_block` (`:700-715`):

```python
def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)["data"]["id"]


def _block(root: Path, card_id: str, blocker: str) -> None:
    subprocess.run(
        ["brd", "block", card_id, "--by", blocker],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
```

with:

```python
def _active_fake_board() -> Any:
    """The FakeBoard the `project` fixture installed as `board.run_brd`.

    Duck-typed: conftest classes are not importable from a test module under
    `--import-mode=importlib`, so `isinstance(..., FakeBoard)` is unavailable.
    """
    fake = board.run_brd
    if not (hasattr(fake, "add_card") and hasattr(fake, "cards")):
        raise AssertionError(
            "_add_card/_block seed the FakeBoard that the `project` fixture installs "
            f"as board.run_brd; board.run_brd is {fake!r} -- request `project`"
        )
    return fake


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    """Seed one card on the project's FakeBoard and return its id.

    `root` is kept so call sites read as before; one FakeBoard is one board.
    """
    return _active_fake_board().add_card(title, parent_id=parent)


def _block(root: Path, card_id: str, blocker: str) -> None:
    """Make `blocker` block `card_id`, as `brd block card_id --by blocker` would.

    Appends to the seeded card's `blocked_by` after the fact, because story
    blocks are added once both stories exist. Never stores `blocked` -- it is
    derived -- and is seeding, so it is not a `FakeBoard.writes` entry.
    """
    fake = _active_fake_board()
    for wanted in (card_id, blocker):
        if wanted not in fake.cards:
            raise AssertionError(f"_block: unknown card {wanted!r}")
    blocked_by = fake.cards[card_id].blocked_by
    if blocker not in blocked_by:
        blocked_by.append(blocker)
```

`json` stays imported because it is used elsewhere in the file. Do not remove any import.

- [ ] **Step 5: Run the representative tests to verify they pass**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
uv run pytest -v \
  "tests/test_orchestrate.py::test_a_milestone_with_no_stories_finishes_without_a_tree" \
  "tests/test_orchestrate.py::test_subtasks_run_in_order_each_stacked_on_the_one_before" \
  "tests/test_orchestrate.py::test_a_story_starts_when_its_blocker_finishes_not_its_level"
```
Expected: 3 passed. These three end `done`, so no `[[`-carrying run-end comment is posted. They cover `_add_card` alone, chained subtask `_block`, and story-on-story `_block` after both stories exist.

- [ ] **Step 6: Bring the file's prose in line with the fixture**

In `tests/test_orchestrate.py`, replace the module docstring lines `:7-9`:

```python
- `run_milestone` runs on Steps-tier fixtures -- a real temporary git repo and a
  real temporary brd board, with `XDG_DATA_HOME` under `tmp_path` so
  `paths.data_dir()` never touches the developer's own -- with the harness
```

with:

```python
- `run_milestone` runs on git-tier fixtures -- a real temporary git repo and an
  in-memory `FakeBoard` (tests/conftest.py) behind `board.run_brd`, with
  `XDG_DATA_HOME` under `tmp_path` so `paths.data_dir()` never touches the
  developer's own -- with the harness
```

Then replace the section header at `:647`:

```python
# ── the runner, on a real repo and a real board ─────────────────────────────
```

with:

```python
# ── the runner, on a real repo and a FakeBoard ──────────────────────────────
```

Leave the `requires_brd` definition and its `reason=` text unchanged (out of scope; V1 replaces it).

- [ ] **Step 7: Prove no test in the file starts a `brd` process**

A PATH shim named `brd` logs every exec and fails. `shutil.which("brd")` still finds it, so the `requires_brd` tests still run rather than skip.

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
SHIM="${TMPDIR:-/tmp}/b9c19b33-brd-shim"; mkdir -p "$SHIM"; : > "$SHIM/calls.log"
printf '#!/bin/sh\necho "$PWD :: $*" >> "%s/calls.log"\nexit 97\n' "$SHIM" > "$SHIM/brd"; chmod +x "$SHIM/brd"
PATH="$SHIM:$PATH" uv run pytest tests/test_orchestrate.py -q -p no:cacheprovider 2>&1 | tail -n 3
wc -l < "$SHIM/calls.log"
```
Expected: `0`, so the shim was never executed. If it is non-zero, `calls.log` names the argv. That is a conversion bug in this task: find the test path and fix the seeding. Do not widen `FakeBoard`. The pytest outcome of this run is not the gate; Step 8 is.

- [ ] **Step 8: Run the file and diff outcomes against the baseline (the gate)**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
uv run pytest tests/test_orchestrate.py -v -p no:cacheprovider 2>&1 | tee "${TMPDIR:-/tmp}/b9c19b33-after.log"
grep -E '^tests/test_orchestrate\.py::\S+ (PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)' "${TMPDIR:-/tmp}/b9c19b33-after.log" \
  | awk '{print $1, $2}' | sort > "${TMPDIR:-/tmp}/b9c19b33-after.outcomes"
diff "${TMPDIR:-/tmp}/b9c19b33-before.outcomes" "${TMPDIR:-/tmp}/b9c19b33-after.outcomes" && echo IDENTICAL
tail -n 1 "${TMPDIR:-/tmp}/b9c19b33-after.log"
```
Expected: `IDENTICAL`, and a summary line whose `in …s` figure (the AFTER wall time) is well under 445s.

The STOP rule below was written when `_refuse_links` still applied to `_comment_add`; see "Status of
the Known-open-blocker section" above for the current state of that code. If `diff` here is
`IDENTICAL`, the `[[link]]` failures the STOP rule describes are not present. If `diff` instead
reports PASSED→FAILED tests, check whether they match the `[[link]]` failure pattern described below
(which would mean `tests/conftest.py`'s current state differs from what "Status of the
Known-open-blocker section" describes — worth comparing against commits `ed2d7bb`/`bd20e57`/`c91460b`
on this branch) or whether they're a different, new conversion bug (covered by Step 8's own
preceding paragraph: "it is a conversion bug in this task. Fix the helpers or the fixture.").

<details><summary>Original STOP rule text, for reference</summary>

```bash
grep -nE 'contains a \[\[link\]\]|^FAILED ' "${TMPDIR:-/tmp}/b9c19b33-after.log"
```
If the failures are `AssertionError: FakeBoard comment add body: ... contains a [[link]]`, the blocker is confirmed. Do not commit. Do not edit assertions, relax `_refuse_links`, touch `tests/conftest.py`, or deselect tests. Escalate with:
- the full list of affected test node ids (the `-` lines of the diff)
- one sample traceback
- the root cause: `comments._ref` (`src/agent_manager/comments.py:264-266`) writes `[[id]]` into escalated/parked/integrate-failed run-end bodies, `FakeBoard._comment_add` refuses them, and `comments.flush` catches only `BoardError`/`LockTimeoutError` (`comments.py:431`). The fix belongs to FakeBoard (card 19b53ab3) or the assertions (card a4e7c1a3).

Code reading predicts these will appear: at least `test_an_integrate_escalation_leaves_an_integrate_failed_run_end_comment`, `test_a_lane_escalation_leaves_an_escalated_run_end_comment_naming_the_parked`, `test_a_cancel_comments_each_parked_subtask_and_the_milestone`, `test_a_cancel_with_an_escalated_lane_comments_only_the_parked_subtask_as_cancelled`, `test_a_pause_leaves_exactly_one_paused_run_end_on_the_milestone`, plus any escalated run whose run-end comment is flushed. Report what the diff actually shows, not this prediction.

</details>

If the diff shows any other kind of change, it is a conversion bug in this task. Fix the helpers or the fixture and rerun this step.

- [ ] **Step 9: Run the full suite**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
uv run pytest
```
Expected: green (exit 0), with no failures and no errors.

- [ ] **Step 10: Commit, recording both wall times**

Replace `<BEFORE>` and `<AFTER>` with the two `in …s` figures from Task 1 Step 2 and Task 2 Step 8.
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-convert-the-project-b9c19b33
git add tests/test_orchestrate.py
git commit -m "test: seed FakeBoard from the project/_milestone fixtures instead of brd" \
  -m "project drops brd init and its commit and requests fake_board; _add_card and _block seed the FakeBoard installed as board.run_brd. Git stays real. Per-test outcomes of tests/test_orchestrate.py are identical before and after; wall time <BEFORE> -> <AFTER>."
```
