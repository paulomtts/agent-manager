# README: milestone stacking, the refusal, and `--detach --board` (072e1f6c)

Subtask of story 22d2d99c. Docs card: the code for milestone stacking
(`orchestrate.milestone_bases`, `MilestoneBlockersError`) and for
`am run --board --detach` is already merged on this branch. This card makes
`README.md` say what that code does, and pins the new text in the README shape
test, `tests/test_readme.py`.

Parent design: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`
(cited below as "parent", by line).

## Where the README stands today

Exploration found the card mostly half-done by earlier cards:

- **Done, keep as is.** The `--detach` section (`README.md:83-101`) already
  documents `--board` (line 101: foreground pre-flight, envelope keys `board`,
  `detached`, `pid`, `log`, `report`, `levels`, no `run_id`,
  `<data dir>/boards/<stamp>-<digest>.log` and `.report.json`, mode 0600,
  `am runs` / `am watch --all`, the late claim as an `escalated` milestone).
  It is pinned by `test_detach_section_documents_the_board_form`
  (`tests/test_readme.py:135`, commit d3f1311).
- **Done, keep as is.** The sentence refusing `--detach` with `--board` is gone:
  the usage-error list at `README.md:80` ends with "`--detach` with `--dry-run`"
  only, and the same test asserts the old wording is absent.
- **Done, keep as is.** The `--board --dry-run` paragraph (`README.md:182`)
  documents the `base_branch` key, the one-blocker stacking and the dry-run
  `MilestoneBlockersError` refusal.
- **Missing.** The section `#### Running every open milestone with --board`
  (`README.md:145-198`) never says that a milestone can start from another
  milestone's integrate branch outside the dry-run paragraph. It has no stacking
  table. Its numbered refusal list (`README.md:162-167`) does not name
  `MilestoneBlockersError`. It does not say how to chain milestones, and it does
  not say which base a rerun or `am resume` uses.

So the deliverable is the stacking half of the `--board` section, plus one test.

## Inherited constraints

- `am` never merges into `--base-branch` (parent:30). The README must not imply
  it does.
- Two or more stackable blockers are refused, never merged-based. The fix is to
  chain them (A ← B ← C) (parent:31-32, 47, 123-124).
- The stacking table is the one in parent:41-47. "Open" is `dag.board_levels`'
  notion. `merged` means landed. `canceled`/`archived` are out of play
  (parent:49-51; `census.LANDED_STATUSES`, `census.py:50`; `census.is_landed`,
  `census.py:66`).
- `MilestoneBlockersError` comes after cycle detection and prefix derivation and
  before the claims check. It exits 3 with the usual envelope and leaves no run
  row, run directory or lease (parent:52-56; `orchestrate.py:2530-2547`).
- A stacked milestone runs as before: its stories root on its base, and its
  Integrate merges into its own `<prefix>-integrate` (parent:66-68).
- `am resume` keeps the recorded base branch (parent:69-70;
  `orchestrate.py:1612-1614`, `base_branch = resumed.base_branch`).
- The README test is unit tier: it reads files and imports pure code, and
  spawns nothing (`CLAUDE.md` "Test tiers"; design §14,
  `docs/superpowers/specs/2026-09-23-agent-manager-design.md`;
  `tests/test_readme.py:1-6`).
- No source change and no JSON change. This card touches `README.md` and
  `tests/test_readme.py` only. The journal stays schema 1 (card text).

## Code facts the README must match

These are what the code does. Where the parent spec words something
differently, the code wins, and the README follows the code.

1. **The rule** (`orchestrate.milestone_bases`, `orchestrate.py:2274-2330`).
   For each open milestone M, look at each id in M's `blocked_by` that is a
   milestone root on the board. Ids that are not milestone roots are ignored.
   Each such blocker is exactly one of:
   - **open** (`dag.milestone_is_open`): it can be stacked on, using its
     `<prefix>-integrate`. Its own run in this board creates that branch.
   - **landed** (`merged`, `canceled` or `archived`, compared case-insensitively):
     ignored.
   - **unlanded** (anything else that is not open: in practice `done`, or a
     milestone with nothing open under it): it can be stacked on only when its
     `<prefix>-integrate` exists as a local branch. Otherwise it is assumed
     landed and ignored, which is how things worked before stacking.

   If no blocker can be stacked on, M's base is `--base-branch`. If exactly one
   can, M's base is that blocker's `<prefix>-integrate`. If two or more can, the
   run is refused with `MilestoneBlockersError`.
2. **Prefix of the blocker.** This is the same derivation as the milestone's own
   (`<title slug>-<first 8 hex>`, or `P-<stem>` with `--branch-prefix P`). It
   also applies to a blocker that is no longer open (parent:57-61).
3. **The refusal message** (`orchestrate.py:2318-2327`) is:
   `milestone <id> is blocked by <n> milestones that are not landed (<ids>); a
   milestone stacks on at most one: chain them (A <- B <- C)`. When any of the
   blockers is unlanded (not open), the message adds
   `, or mark <ids> merged if that work has already landed`.
4. **Scheduling is unchanged.** M still waits for every open blocker to finish
   `done`. If a blocker ends any other way, M is reported `blocked`
   (`README.md:154-156`). Stacking changes where M starts, not when.
5. **Rerunning `am run --board`** computes each milestone's base again from the
   current board and the current local branches (`run_board` → `milestone_bases`
   every time; the board passes no `resume_run_id`). After a blocker A finishes
   `done`, a rerun still stacks B on A's `<prefix>-integrate` while that branch
   exists locally and A is not marked `merged`. Once a human lands A and marks it
   `merged`, B starts from `--base-branch`.
6. **`am resume <run-id>`** on one milestone's run reuses the `base_branch` that
   run recorded. It does not recompute it (`orchestrate.py:1614`). So a stacked
   milestone does not silently move to a new root. (`README.md:403` already
   says that resume reuses the recorded `base_branch`. This card points to it
   from the board section.)

## Observable behaviour: what the README must say

All edits go in the section `#### Running every open milestone with --board`.
Nothing in any other section changes, unless that section restates the old
wording (see "Out of scope").

### B1. Stacking subsection or paragraph, with the table

Put a stacking passage after the "Across milestones" bullets. It explains that
each milestone starts from a branch of its own, and gives this table. The
wording may differ, but each row must carry the meaning shown:

| blockers of the milestone (its `blocked_by` milestone roots) | the milestone starts from |
|---|---|
| none, or every blocker is `merged`, `canceled` or `archived` | `--base-branch` |
| exactly one open blocker B | `<prefix of B>-integrate` |
| exactly one blocker B that is `done` but not `merged`, and whose `<prefix of B>-integrate` exists locally | `<prefix of B>-integrate` |
| a `done` blocker with no local `<prefix>-integrate` | treated as landed, so it does not count |
| two or more blockers from the two "B" rows above | refused (`MilestoneBlockersError`) |

Around the table, the README says:

- The table is written as a Markdown pipe table, so `|` rows appear in the
  section.
- Stories of a stacked milestone root on its base, which is now the blocker's
  integrate branch. The milestone's own Integrate still merges into its own
  `<prefix>-integrate`. `am` never merges into `--base-branch`.
- Stacking changes where a milestone starts, not when. It still waits for its
  open blockers to finish `done` (fact 4).

### B2. Fix the "counts as satisfied" bullet

The first "Across milestones" bullet (`README.md:154`) says a blocker that is
not an open milestone "counts as satisfied". Keep that for scheduling. Add that
such a blocker may still be the milestone's base: a `done`, unmerged blocker
whose integrate branch is still local. Point to the table.

### B3. Refusal list gains `MilestoneBlockersError`

The numbered "Refusals" list becomes:

1. A blank `--base-branch`.
2. A blocker cycle between milestones (`DependencyCycleError`).
3. A blank prefix, or a prefix two milestones share.
4. **New:** a milestone with two or more blockers it could stack on
   (`MilestoneBlockersError`). The entry names what the message names and says
   to chain the milestones, or to mark an already-landed blocker `merged`.
5. A claim another live run holds (`ClaimedError`, …). Unchanged text.

The sentence after the list ("A refused board run leaves no run row, no run
directory and no lease for any milestone") still covers all five refusals.

### B4. How to chain milestones

A short passage, for example in or next to refusal item 4, says how to get out
of the refusal:

- Chain them: make the blockers depend on each other (A ← B ← C, i.e.
  `brd block B --by A`, then `brd unblock C --by A` so C is blocked by B only;
  `brd block` and `brd unblock` both exist), so each milestone has
  one blocker to stack on. One `am run --board` then runs the whole chain, each
  milestone starting from the previous one's `<prefix>-integrate`.
- Or, if a blocker's work has already landed, mark that milestone `merged`
  (a human's step, which `am` never takes). It then no longer counts.

### B5. Which base a rerun or a resume uses

In the "Recovery" paragraph (`README.md:193-198`), add:

- A rerun of `am run --board` computes each base again (fact 5).
- `am resume <run-id>` keeps the base that run recorded (fact 6).

### B6. Dry-run paragraph

`README.md:182` already says all it needs to. It may link to the new table,
but it must keep its current facts (`base_branch` key, `MilestoneBlockersError`
in dry-run, `plan` computed against `base_branch`).

## Tests

One new test in `tests/test_readme.py`, placed next to
`test_detach_section_documents_the_board_form`. **Tier: unit (no marker).** It
reads `README.md` and imports `agent_manager.orchestrate` for a class name. It
spawns no subprocess, and git, brd and claude are never touched (`CLAUDE.md`
"Test tiers": unmarked = unit; the module docstring at `tests/test_readme.py:1-6`
already says so). Add `orchestrate` to the existing
`from agent_manager import ...` line.

`test_board_section_documents_stacking`. Let
`section = _section("Running every open milestone with `--board`")`. Assert:

1. `orchestrate.MilestoneBlockersError.__name__` appears in the section
   **outside** the dry-run paragraph. Concretely, it appears inside the numbered
   refusal list: there is a line starting `4. ` that contains it. In the
   section's text, it comes before the line starting `5. ` that contains
   `ClaimedError`. This pins B3 and its order, and fails today, since today's
   item 4 is `ClaimedError`.
2. The section contains at least five Markdown table rows (lines starting with
   `|`) besides the header and separator lines. Among the table rows, one names
   `merged`, `canceled` and `archived`. One names `` `--base-branch` ``. One
   names `-integrate`. One names `locally` or `local`. One names
   `MilestoneBlockersError`. This pins B1.
3. `"chain"` and `"A ← B ← C"` (or the ASCII `A <- B <- C` the error message
   uses; the test accepts either) appear in the section. This pins B4.
4. `"mark"` and `` "`merged`" `` appear in one sentence (the same line) that
   also contains `MilestoneBlockersError` or "chain". The test can check that
   some line contains all of `merged`, `mark` and `chain`. This pins B4's
   second option.
5. `"am resume"` and `"base"` appear in the Recovery passage. For example,
   check that a line contains both `am resume` and `` `base_branch` ``. This
   pins B5.
6. "never merges into `--base-branch`", or the README's equivalent: pin the
   exact sentence chosen. This pins B1's second bullet.
7. The existing negative checks stay untouched: they live in
   `test_detach_section_documents_the_board_form`, and this card adds no
   refusal sentence for `--detach` with `--board`.

Asserts 1 and 2 must fail on today's README. Run the test first to confirm the
red, then edit the README (TDD). Choose the exact README sentences first, and
pin those strings exactly. Keep the README prose in the existing style: short
declarative sentences, backticked identifiers, no marketing.

The whole suite stays green: `uv run pytest`. No other test file changes.

## Out of scope

- Any change under `src/`: the code, its messages and its JSON. If the README
  and the code disagree, the README follows the code. A code bug found while
  writing is reported, not fixed here.
- The `--detach` section, already done by card 03f027ea. Do not add a second
  JSON example there: `test_detach_section_documents_envelope` pins exactly one.
- Sibling cards' work: the stacking and detach implementation, their unit
  tests, and the `e2e_fake` scenarios (parent:107-117, commits b84e6bc, 1b55793).
- Merged-base resolution for milestones, and a board-level run record
  (parent:31-33). Neither is documented as existing.
- Rewording other README sections, except a sentence that the new text makes
  false.

---

# README: milestone stacking, the refusal, and chaining — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `#### Running every open milestone with `--board`` section of `README.md` document milestone stacking (table, refusal, chaining, rerun/resume base), pinned by one new unit test in `tests/test_readme.py`.

**Architecture:** Docs-only card. One new test, `test_board_section_documents_stacking`, reads the section through the existing `_section` helper and pins exact sentences; it is written first and must fail (RED) on today's README. Then six exact text edits to `README.md` make it pass (GREEN). No file under `src/` changes.

**Tech Stack:** Markdown, Python 3, pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/3-1-readme-stacking-the-072e1f6c.md` (prepended above).

## Global Constraints

- Touch `README.md` and `tests/test_readme.py` only. No change under `src/`, no JSON change, journal stays schema 1.
- The new test is unit tier: no marker, no subprocess, no git, brd or claude. It reads `README.md` and imports `agent_manager.orchestrate` for a class name only.
- Add `orchestrate` to the existing `from agent_manager import ...` line; no other import style.
- `am` never merges into `--base-branch`; the README must not imply it does.
- Two or more stackable blockers are refused, never merged-based; the fix is to chain them (A ← B ← C).
- Where the parent spec and the code disagree, the README follows the code (`orchestrate.milestone_bases`, `orchestrate.py:2274-2330`).
- Do not touch the `--detach` section; do not add a JSON example anywhere (`test_detach_section_documents_envelope` pins exactly one there).
- Keep README prose style: short declarative sentences, backticked identifiers, no marketing.
- No new heading: the stacking passage is a paragraph led by `Stacking.`, matching the section's existing `Flags.`, `Refusals.` and `Recovery.` leads (a new heading would change `_headings()` for every test).
- Whole suite stays green: `uv run pytest`.

## Review Focus

1. A Markdown link added in the new text points at an anchor that does not exist (e.g. a typo in `#relaunching-resumes`) — a reader expects every in-section link to land on a heading. Pinned in Task 1 Step 1 (anchors resolve).
2. A table row whose cell contains a stray `|` or is missing one renders as a broken table on GitHub — a reader expects a clean two-column table. Pinned in Task 1 Step 1 (every table line has exactly three `|`).
3. Renumbering the refusal list leaves a duplicate or skipped number (two `4.` items, or no `3.`) — a reader expects refusals `1.`–`5.` once each, in order. Pinned in Task 1 Step 1 (list is exactly `1.`…`5.`).
4. The sentence after the refusal list ("A refused board run leaves no run row…") gets dropped or moved above the new item while editing — a reader expects it to still cover all five refusals. Pinned in Task 1 Step 1 (it follows item 5).
5. The chaining recipe names the wrong `brd` commands or direction (e.g. `brd block A --by B`) — a reader copying it would build the wrong chain. Pinned in Task 1 Step 1 (exact `brd block B --by A` / `brd unblock C --by A` / `brd update <id> --status merged` strings; all three commands verified to exist in `brd --help`).

---

## File Structure

- Modify: `tests/test_readme.py` — import line (line 15) and one new test after `test_detach_section_documents_the_board_form` (ends at line 151).
- Modify: `README.md` — section `#### Running every open milestone with `--board`` (lines 145-198 today): the first "Across milestones" bullet (line 154), a new `Stacking.` passage after the bullets (after line 158), the numbered refusal list (lines 162-167), and the `Recovery.` paragraph (lines 193-198).

One task: the test and the README edits it pins are one reviewable deliverable.

---

### Task 1: Document milestone stacking in the `--board` section

**Files:**
- Modify: `tests/test_readme.py:15` (import), insert after line 151 (new test)
- Modify: `README.md:154`, after `README.md:158`, `README.md:162-167`, `README.md:193-198`

**Interfaces:**
- Consumes: `tests/test_readme.py` helpers `_section(title: str) -> str`, `_headings() -> list[tuple[int, int, str]]`, `_slug(title: str) -> str`, module constant `README`; `agent_manager.orchestrate.MilestoneBlockersError` (class, `orchestrate.py:2249`).
- Produces: test `tests/test_readme.py::test_board_section_documents_stacking`. Nothing else depends on it.

- [ ] **Step 1: Write the failing test**

In `tests/test_readme.py`, change line 15 from:

```python
from agent_manager import cli, detach, models, store
```

to:

```python
from agent_manager import cli, detach, models, orchestrate, store
```

Then insert this test directly after `test_detach_section_documents_the_board_form` (after its last line, `    assert "or with `--board`" not in text`), with two blank lines before and after:

```python
def test_board_section_documents_stacking():
    """Card 072e1f6c: the `--board` section documents milestone stacking: the
    table, the `MilestoneBlockersError` refusal, chaining, and which base a
    rerun or a resume uses."""
    section = _section("Running every open milestone with `--board`")
    lines = section.splitlines()
    name = orchestrate.MilestoneBlockersError.__name__

    # B3: the refusal list is exactly 1.-5., item 4 is the new refusal, before
    # the claim check, and the closing sentence still follows the list.
    numbered = [index for index, line in enumerate(lines) if re.match(r"\d+\. ", line)]
    assert [lines[index].split(".")[0] for index in numbered] == ["1", "2", "3", "4", "5"]
    fourth, fifth = lines[numbered[3]], lines[numbered[4]]
    assert name in fourth
    assert "ClaimedError" in fifth
    assert lines[numbered[4] + 2].startswith(
        "A refused board run leaves no run row, no run directory and no lease"
    )

    # B1: the stacking table, a well-formed two-column pipe table.
    table = [line for line in lines if line.startswith("|")]
    for line in table:
        assert line.count("|") == 3, line
    rows = [line for line in table[1:] if not re.fullmatch(r"\|[\s:|-]+\|", line)]
    assert len(rows) >= 5
    assert any(all(word in row for word in ("`merged`", "`canceled`", "`archived`")) for row in rows)
    assert any("`--base-branch`" in row for row in rows)
    assert any("-integrate" in row for row in rows)
    assert any("local" in row for row in rows)
    assert any(name in row for row in rows)
    assert "`am` never merges into `--base-branch`." in section
    assert "Stacking changes where a milestone starts, not when." in section

    # B2: a satisfied blocker can still be the base.
    assert "it counts as satisfied for scheduling" in section

    # B4: chaining, and marking a landed blocker merged.
    assert "chain" in section
    assert "A ← B ← C" in section or "A <- B <- C" in section
    assert "`brd block B --by A`" in section
    assert "`brd unblock C --by A`" in section
    assert "`brd update <id> --status merged`" in section
    assert any(all(word in line for word in ("mark", "`merged`", "chain")) for line in lines)

    # B5: the Recovery passage says which base a rerun and a resume use.
    recovery = section[section.index("Recovery. ") :]
    assert "computes each milestone's base again" in recovery
    assert any("am resume" in line and "`base_branch`" in line for line in recovery.splitlines())

    # Every in-section link lands on a heading.
    anchors = re.findall(r"\]\(#([^)]+)\)", section)
    slugs = {_slug(title) for _, _, title in _headings()}
    assert set(anchors) <= slugs, f"dangling anchors: {set(anchors) - slugs}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_readme.py::test_board_section_documents_stacking -v`
Expected: FAIL at the first assertion block — the section has only items `1.`–`4.`, so the list comparison fails with `AssertionError` showing `['1', '2', '3', '4'] == ['1', '2', '3', '4', '5']`.

- [ ] **Step 3: Edit the first "Across milestones" bullet (B2)**

In `README.md`, replace this line (line 154):

```markdown
- Milestones are leveled by the `blocked_by` edges between them. A milestone that is marked done, or that has nothing open under it, drops out, and so does a blocker that is not an open milestone: it counts as satisfied.
```

with:

```markdown
- Milestones are leveled by the `blocked_by` edges between them. A milestone that is marked done, or that has nothing open under it, drops out, and so does a blocker that is not an open milestone: it counts as satisfied for scheduling. Such a blocker can still be the milestone's base, when it is `done` but not `merged` and its `<prefix>-integrate` branch is still local (see Stacking below).
```

- [ ] **Step 4: Add the `Stacking.` passage and table after the bullets (B1)**

In `README.md`, find this line (line 158, the last "Across milestones" bullet) and the blank line after it:

```markdown
- A board with no open milestone is `ok`, runs nothing and exits 0.
```

Insert after it (so there is one blank line between the bullet and `Stacking.`, and one blank line between the closing paragraph and the existing `Flags.` paragraph):

```markdown

Stacking. Each milestone starts from one branch, its base. Only the ids in its `blocked_by` that are milestone roots on the board count here; any other blocker id is ignored. A blocker's prefix is derived the same way as the milestone's own, even when the blocker is no longer open. A blocker counts as landed when its status is `merged`, `canceled` or `archived`, in any case.

| blockers of the milestone (its `blocked_by` milestone roots) | the milestone starts from |
|---|---|
| none, or every blocker is `merged`, `canceled` or `archived` | `--base-branch` |
| exactly one open blocker B | `<prefix of B>-integrate`, which B's own run in this board creates |
| exactly one blocker B that is not open and not landed (in practice `done` but not `merged`), whose `<prefix of B>-integrate` exists as a local branch | `<prefix of B>-integrate` |
| a blocker that is not open and not landed, with no local `<prefix>-integrate` branch | nothing: it is treated as landed and does not count |
| two or more blockers from the two `<prefix of B>-integrate` rows above | refused (`MilestoneBlockersError`, see Refusals below) |

A stacked milestone runs exactly as before from its base: its stories root on the blocker's `<prefix>-integrate` instead of `--base-branch`, and its own Integrate still merges into its own `<prefix>-integrate`. `am` never merges into `--base-branch`. Stacking changes where a milestone starts, not when. It still waits for every open blocker to finish `done`, and is reported `blocked` if one ends any other way.
```

- [ ] **Step 5: Renumber the refusal list and add item 4 (B3, B4)**

In `README.md`, replace this line (line 167):

```markdown
4. A claim another live run holds, checked once over the claims of every open milestone together (`ClaimedError`, see [Several am processes](#several-am-processes)).
```

with these two lines (no blank line between them, so the list stays one list):

```markdown
4. A milestone with two or more blockers it could stack on: open, or not landed with a local `<prefix>-integrate` branch (`MilestoneBlockersError`). The message names the milestone and those blockers. A milestone stacks on at most one, so chain them (A ← B ← C): if C is blocked by both A and B, run `brd block B --by A`, then `brd unblock C --by A`, so that C is blocked by B only. One `am run --board` then runs the whole chain, each milestone starting from the previous one's `<prefix>-integrate`. When the message also says to mark blockers `merged`, their work may already have landed: instead of chaining, mark each such blocker `merged` with `brd update <id> --status merged`, a human's step `am` never takes. It then no longer counts.
5. A claim another live run holds, checked once over the claims of every open milestone together (`ClaimedError`, see [Several am processes](#several-am-processes)).
```

Leave the next paragraph, `A refused board run leaves no run row, no run directory and no lease for any milestone. ...`, exactly as it is: it now covers all five refusals.

- [ ] **Step 6: Add the base rule to the `Recovery.` paragraph (B5)**

In `README.md`, find this line (line 198, the last line of the Recovery passage):

```markdown
A `blocked` milestone has no run to resume. It starts on a later `am run --board`, once its blockers are done.
```

Insert after it, with one blank line before:

```markdown

Which base a milestone resumes from. A rerun of `am run --board` computes each milestone's base again from the current board and the current local branches. A blocker that finished `done` still stacks its dependents on its `<prefix>-integrate` while that branch is local and the blocker is not marked `merged`. Once you land it and mark it `merged`, they start from `--base-branch`. `am resume <run-id>` keeps the `base_branch` that run recorded and does not compute it again, so a stacked milestone does not move to a new base (see [Relaunching resumes](#relaunching-resumes)).
```

Leave the `--dry-run` paragraph (B6, the one starting `` `--dry-run` with `--board` is read-only.``) unchanged: it already names `base_branch`, the dry-run `MilestoneBlockersError` refusal and the plan computed against `base_branch`.

- [ ] **Step 7: Run the new test to verify it passes**

Run: `uv run pytest tests/test_readme.py::test_board_section_documents_stacking -v`
Expected: PASS

- [ ] **Step 8: Run the README tests and the default suite**

Run: `uv run pytest tests/test_readme.py -v`
Expected: 8 passed (the 7 existing tests, including `test_detach_section_documents_the_board_form` and `test_detach_section_documents_envelope`, plus the new one).

Run: `uv run pytest`
Expected: all pass, no failures.

Run: `git diff --stat`
Expected: only `README.md` and `tests/test_readme.py` changed.

- [ ] **Step 9: Commit**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: README documents milestone stacking on --board (card 072e1f6c)"
```

---

## Self-Review

- **Spec coverage:** B1 → Step 4 (table, stories root on base, own Integrate, never merges into `--base-branch`, "where not when"). B2 → Step 3. B3 → Step 5 (item 4 names the milestone and blockers, chain or mark `merged`; item 5 unchanged text; closing sentence untouched). B4 → Step 5 (chain with `brd block`/`brd unblock`, one board run runs the chain; mark `merged` as a human step). B5 → Step 6 (rerun recomputes, fact 5; resume keeps recorded `base_branch`, fact 6). B6 → left unchanged (Step 6 note). Tests 1–6 → Step 1 assertions; test 7 → existing negative checks untouched, rerun in Step 8. Code facts 1–3 reflected in the table and item 4 (message adds the `mark ... merged` clause only for unlanded blockers, so item 4 says "when the message also says"). Fact 4 → Step 4's last two sentences.
- **Placeholders:** none; every edit gives exact old and new text.
- **Consistency:** pinned strings in Step 1 match Steps 3–6 verbatim: "it counts as satisfied for scheduling", "`am` never merges into `--base-branch`.", "Stacking changes where a milestone starts, not when.", "A ← B ← C", "`brd block B --by A`", "`brd unblock C --by A`", "`brd update <id> --status merged`", "Recovery. " (existing lead), "computes each milestone's base again", a line with `am resume` and `` `base_branch` ``, anchor `#relaunching-resumes` (heading `#### Relaunching resumes`, README line 399). Item 4's single line carries `mark`, `` `merged` `` and `chain`. Table lines each have exactly three `|` (no `|` inside any cell). The line two after item 5 is the unchanged "A refused board run…" paragraph.
- **Review Focus:** each of the five lines has an assertion in Step 1.
<!-- task-pipeline: validated -->
