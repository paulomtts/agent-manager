# Correct the stale grafo-starvation rationale: design

Date: 2026-10-03
Card: `010d3744` (story `c8f098a0`), blocked by `738c9fd0` (landed: `8b1e014`, `33f810c`)
Parent spec: `docs/superpowers/specs/2026-10-03-merged-root-real-edges-design.md`, §3.4 (L188-200)

## 1. Purpose

Parent §3.4 (L188-200) asks for two things:

1. Every docstring or comment that gives "grafo's dynamic worker pool can starve
   a 2+-parent join" as the reason for the old merged-root workaround is
   corrected. The corrected text says what is true: a 2+-blocker item gets one
   real edge per blocker, the same as a single-blocker item gets one, and grafo's
   own all-parents-done gate makes it wait. The named locations are the
   `build_dag_tree`, `supervise` and `lane` docstrings in
   `src/agent_manager/orchestrate.py`.
2. `docs/superpowers/specs/2026-10-01-run-board-design.md` gets **one sentence**
   pointing at parent §3.4 where it repeats the stale reasoning. Parent L198-200
   says "one sentence added there, pointing at this spec, since that file is a
   dated, already-approved design and is not rewritten wholesale".

The card says this is a docs card and "must read the actual landed code, not
restate this spec's plan for it". The findings below come from reading the code
at `HEAD` (`33f810c`).

## 2. Findings: what the landed code already says

Item 1 has already been done by the blocker card's commits `8b1e014` and
`33f810c`. Nothing in `src/` or `tests/` repeats the stale rationale:

- `grep -rniE "starv|dynamic worker|story_done|story_ok|blocker_tips" src/ tests/`
  finds nothing. The only near-hit is the unrelated
  `test_the_supervisor_plan_roots_and_tips_every_census_story_done_ones_included`
  in `tests/test_orchestrate.py:760`.
- `build_dag_tree` docstring (`orchestrate.py:1389-1407`): "Every blocker of an
  item gets one edge to it ... whatever the blocker count: grafo enqueues a node
  only once every parent returned, and never once a parent raised, so an item
  with two or more blockers is a real join." It also says: "This helper creates
  no events and does no waiting." This is the parent §3.4 statement.
- `supervise` docstring (`:1441-1448`): "one node per story and one edge per
  blocker ... a story rooted on a `merged` base ... is a grafo join, not an
  executor root: its lane runs only after every blocker succeeded". This is
  correct.
- `lane` docstring (`:1136-1138`): "A story whose root is `merged` is reached
  through one grafo edge per blocker, so its lane runs only after every blocker
  succeeded; it reads the blockers' tips from `plan.tips`". This is correct.
- The code matches the docstrings. `build_dag_tree` (`:1408-1423`) connects every
  blocker with `parent.connect(...)` and makes roots only from items with no
  blocker.

Item 2 is still open. `2026-10-01-run-board-design.md` (header L3-4: "Date:
2026-10-01 / Status: approved design, pre-implementation") repeats the stale
reasoning in three places:

- **L102-108** ("What's generic vs. story-specific"): "because of grafo's
  confirmed dynamic-worker-pool starvation on a real 2+-parent join — a separate
  path for anything with 2+ blockers, where the node becomes one of the
  executor's own roots and waits on its blockers' own completion signals instead
  of a grafo edge."
- **L127-131** ("The extraction"): "multi-blocker items as additional roots
  waiting on their blockers' completion events — the same
  `story_done`/`story_ok` mechanism `lane`/`blocker_tips` use today".
- **L251-254** (§4 Risks): "grafo's join-starvation workaround in particular".

All three describe the same superseded mechanism. One sentence can cover all of
them.

**Branch note.** The parent spec was added by commit `cbe680a`. That commit is on
`master`, not on this card's branch. The pointer names the parent spec by
filename, so the reference resolves once the branches are integrated. This card
does not copy or create the parent spec.

## 3. Required behavior

### 3.1 The pointer sentence

`docs/superpowers/specs/2026-10-01-run-board-design.md` gains exactly one new
sentence:

- **Placement.** It is a paragraph of its own, directly after the paragraph that
  ends at L111 ("...and it is not touched or generalized."). That paragraph is
  the first place the stale reasoning appears, and the new paragraph comes
  before "**The extraction.**".
- **Form.** It is one sentence of plain prose, prefixed with `**Superseded
  (2026-10-03).**` so a reader skimming the dated doc sees it.
- **Required content.** The sentence must:
  - name the parent spec by its repo-relative filename,
    `2026-10-03-merged-root-real-edges-design.md`, and its section, §3.4;
  - say that the 2+-blocker "separate path" and the `story_done`/`story_ok`
    completion-event wait described in this section, in "The extraction", and in
    the §4 risk no longer exist;
  - say that a 2+-blocker item now gets one real grafo edge per blocker, the same
    as a single-blocker item, and grafo's own all-parents-done gate makes it
    wait;
  - say that the "starvation" rationale for the workaround does not hold.
- **Illustrative wording.** The planner may adjust the wording but must keep
  every content point above:

  > **Superseded (2026-10-03).** The 2+-blocker "separate path" described here,
  > in "The extraction" below and in §4's first risk (an executor root waiting on
  > `story_done`/`story_ok` completion events, justified by grafo
  > "join-starvation") no longer exists and its rationale does not hold: as
  > `2026-10-03-merged-root-real-edges-design.md` §3.4 records,
  > `build_dag_tree` now gives a 2+-blocker item one real grafo edge per blocker,
  > the same as a single-blocker item gets one, and grafo's own
  > all-parents-done gate is what makes it wait.

- **Nothing else in the file changes.** The original sentences at L102-108,
  L127-131 and L251-254 stay as written. So do the header, `Status:` line, the
  `build_dag_tree` signature block, and every other line. `git diff` on the file
  shows only added lines: the sentence plus at most one blank separator line.

### 3.2 Docstrings

No source edit is required. The three docstrings in §2 already state the parent
§3.4 rationale. The implementer must still confirm this against `HEAD` before
finishing. Run the grep in §4 check 1 and read the three docstrings at the lines
cited in §2.

If either check finds a stale sentence that landed after this spec was written,
correct only that sentence. The corrected text must state the §1 item 1
rationale. A docstring that is already correct is not reworded.

### 3.3 Error paths

None. This change touches documentation only. No runtime behavior, CLI output or
error envelope changes.

## 4. Verification

This card adds no new pytest test. The change is one Markdown sentence. A test
that asserts the prose of a dated design doc would pin wording, not behavior, and
the repo has no doc-content test tier (CLAUDE.md "Test tiers"; design spec §14).
The card is verified by these checks:

1. **Stale rationale absent from live code.** Run
   `grep -rniE "starv|dynamic.worker|story_done|story_ok|blocker_tips" src/ tests/`.
   The only hit is the unrelated test name at `tests/test_orchestrate.py:760`.
   Not a pytest test; it is a review check.
2. **Pointer present exactly once.** Run
   `grep -c "merged-root-real-edges-design.md" docs/superpowers/specs/2026-10-01-run-board-design.md`.
   The result is `1`. The matching line also contains `§3.4`.
3. **Additive-only edit.** Run
   `git diff --numstat -- docs/superpowers/specs/2026-10-01-run-board-design.md`.
   It reports `0` deleted lines. Every other file in the diff is either this
   spec or its plan.
4. **Default suite unchanged.** `uv run pytest` (`unit` + `git` tiers, CLAUDE.md
   "Verification") passes. It runs to show the change touched no code.
5. **Fake-claude suite unchanged.** `uv run pytest -m e2e_fake` passes. The
   card's verification list requires it. It is expected to pass because no
   source file changes.

The `brd`, `soak` and `e2e` tiers are not run. Nothing they observe changes.

## 5. Out of scope

- **Rewriting `2026-10-01-run-board-design.md`.** Its three stale passages stay
  as written (parent L198-200). The file gets one pointer sentence and nothing
  more.
- **Other dated specs and plans that use starvation language.**
  `2026-09-30-architecture-cleanup-design.md` (L74, L81, L199),
  `task-extract-build-dag-tree-c0a1345c-design.md` (L28), and the
  `docs/superpowers/plans/*.md` files belong to completed cards. Neither the card
  nor parent §3.4 names them.
- **Creating or copying the parent spec onto this branch.** It already exists on
  `master` (`cbe680a`).
- **`build_dag_tree`, `supervise` and `lane` code.** The real-edge wiring is
  owned by sibling `27db9eb4` (parent §3.1). The `plan.tips` read and the
  deletion of `blocker_tips`/`story_done`/`story_ok` are owned by sibling
  `738c9fd0` (parent §3.2-3.3). Both have landed.
- **The grafo pin** (`pyproject.toml:24`, `grafo>=0.3.6`). Owned by `27db9eb4`.
- **Tests.** There are no new, rewritten or re-tiered tests. Parent §4's test
  list belongs to the sibling cards.
