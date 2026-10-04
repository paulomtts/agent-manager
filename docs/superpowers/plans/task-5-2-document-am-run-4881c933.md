<!-- task-pipeline: validated -->
# Task 5.2: Document `am run --board` (card 4881c933)

Parent story: 3a859c52 "am run --board contract". Blocked by sibling 5.1 (a7fcc076, done), whose tests are the contract this README describes. Sources: `docs/superpowers/specs/2026-10-01-run-board-design.md`, `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §5, and `docs/superpowers/specs/task-5-1-pin-the-board-a7fcc076-design.md`.

Branch state: this worktree (`ami/task-5-2-document-am-run-4881c933`) already contains 5.1's tests (`tests/test_cli.py:3639` `test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id`, `:3847` `test_a_board_run_envelope_wraps_run_boards_keys_unchanged`, `:3902` `test_a_board_run_exits_escalated_only_when_some_milestone_escalated`), so it is stacked on 5.1. 5.1 is not on master. Write against this worktree's code, not master's.

## Scope

README.md only. No source change, no new JSON key, the journal stays schema 1. Add one new `####` subsection inside `### Milestone runs`, titled something like "Running every open milestone with `--board`", placed after "Preview with `--dry-run`" (README.md:107) and before "What a clean run leaves behind" (:138). It cross-links to Parallel runs (:203), Relaunching resumes (:337) and Reading the stream safely (:543). Also make two small edits elsewhere: the usage-error list (:80-85), and the `milestone_id` sentence in "Reading the stream safely" (:550).

The subsection must cover the following. Every fact was checked against `src/agent_manager/cli.py` and `src/agent_manager/orchestrate.py` in this worktree.

1. **What a board run is.** `am run --board --verify ...` drives every open milestone as one dependency graph (`orchestrate.run_board`, orchestrate.py:2262). Milestones are leveled by their `blocked_by` edges (`dag.board_levels`). A done milestone with nothing open under it drops out. A milestone starts once every open milestone blocking it has finished `done`. If a blocker ends in any other status, the milestone is never started and is reported `blocked`. A milestone whose run raises is reported `escalated` and does not disturb the other milestones. Inside each milestone, everything works as on a `--milestone` run (stories, Integrate, `<prefix>-integrate`). An empty board is `ok` and runs nothing.
2. **Flags.** Exactly one of `--card`, `--milestone` or `--board` is given. `--board` with `--card` or `--milestone` is a usage error (exit 2), and so are `--detach` with `--board` and a blank `--branch-prefix` with `--board`. `--branch-prefix` is optional with `--board`. When it is omitted, each milestone's prefix is its own card stem. When it is given, the prefix is `<prefix>-<stem>` per milestone (`board_prefix_of`, cli.py:1237). `--verify`, `--allow-no-verification`, `--base-branch` (default `master`) and `--repo-dir` apply to every milestone.
3. **Refusals.** These come in order, before anything is written, as a `{"ok": false}` envelope (exit code as for other handled refusals, 3): a blank `--base-branch` (the code's `not base_branch` check; the README must not say the branch is looked up in git), a blocker cycle between milestones, a blank prefix or a prefix two milestones share, then a claim held by another live run over the union of every open milestone's claims. Each refusal leaves no run row, no run directory and no lease for any milestone.
4. **Run payload.** `data` is `{"ok", "board": true, "levels": [{"level", "milestones": [ids]}], "milestones": [entry...]}`, with no top-level `run_id`. Entries are in level order. Each entry is one of three shapes. A dispatched milestone is `{"milestone_id", "status", ...that milestone's own --milestone report, including its run_id}`, with `status` one of `done`, `escalated`, `stopped` or `cancelled`. An undispatched one is `{"milestone_id", "status": "blocked", "blocked_by": [the blocker ids that did not finish done]}`. A milestone whose run raised is `{"milestone_id", "status": "escalated", "error": "<Type>: <message>"}`. `data.ok` is true only when every entry is `done`, and the outer envelope `ok` is still `true`. Exit code: **1 only when some entry is `escalated`**. A board whose milestones are only `blocked`, `stopped` or `cancelled` exits 0 with `data.ok` false (cli.py:1758-1767). The exploration note said "exit 1 when not all are done", but the code and the `:3902` test show the narrower rule, so the README follows the code.
5. **Dry run.** `am run --board --dry-run` is read-only. It opens no store, checks no claim, writes nothing, and exits 0. It still refuses a cycle or a bad prefix. `data` is `{"board": true, "max_concurrent", "levels": [{"level", "milestones": [{"milestone_id", "title", "branch_prefix", "plan"}]}]}`, with no `ok` and no `run_id` inside `data`. Each `plan` is exactly that milestone's `--milestone --dry-run` data (`max_concurrent`, `levels`, `already_done`, `integrate`). Link to "Preview with `--dry-run`" for how to read it, rather than repeating that section.
6. **`--max-concurrent` semantics.** One slot pool is shared by the story lanes of every milestone (`asyncio.Semaphore` in `_run_board_async`, orchestrate.py:2381), so N caps the stories running at once across the whole board, not per milestone. The default is 4, as with `--milestone`. With `--max-concurrent 1`, one story runs at a time on the whole board. Integrate merges stay outside the bound, as already stated in Integrate. This must agree with `tests/e2e/test_run_board.py` (`:276` and `:292`, shared slots).
7. **How the journal represents several milestones.** There is no board-level run. Each dispatched milestone gets its own run: its own run id, `journal.jsonl` and lease, exactly as a solo `--milestone` run does. List a board run's runs from each entry's `run_id` (or from `am runs`), and read each journal separately. Each journal's first line is a `run_upsert` whose `payload.milestone_id` is that milestone's full card id. It is never `null` on a board run. Every line in that journal shares its `run_id`. `story_upsert` lines carry the story card id in `story` and have no milestone key. A story's milestone is that of its journal's run. A real story id never appears in two milestones' journals. The synthetic ids `"integrate"`, `"bases"` and `"base-<story id>"` are fixed names that can recur in several milestones' journals, so they identify a story only together with `run_id`.
8. **Recovery.** Nothing records the board run as a whole. Re-run the same `am run --board` command: done milestones drop out. Alternatively, continue a stopped or escalated milestone on its own with `am resume <run-id>`, using that entry's `run_id`. A `blocked` milestone has no run. It starts on the next `--board` run, once its blocker is done.

Small edits outside the new subsection:

- README.md:80-85. The list already ends with "`--detach` with `--dry-run` or with `--board`" (README.md:83), so do not add that again. Add `--board` together with `--card` or `--milestone`, none of the three, and a blank `--branch-prefix` with `--board` to the refused combinations. Also keep the `--max-concurrent` below 1 item as is (it applies to `--board` too). Also say there that `--branch-prefix` is required except with `--board`. Line 68's "`--branch-prefix` is required" sentence should get the same qualifier or a pointer to the new subsection.
- README.md:550. Extend the `milestone_id` parenthetical so that it reads "`null` on a `--card` run, the milestone's id on a `--milestone` or `--board` run". Add one clause saying that a board run has one journal per milestone (link to the new subsection). Do not add new "ignore unknown keys" wording: the `runs`/`logs` sections belong to other cards, and line 461 (Listing runs) is already correct, so leave it.
- Optionally show one short example command for each of the run and the dry run. Do not include a full JSON sample unless it is copied from a shape the 5.1 tests pin.

## Error paths

None are new. The README states only the existing refusals (usage errors, exit 2; handled refusals, exit 3 envelope) and the `blocked` and `escalated` entry shapes above. If, while writing, the implementer finds that the code disagrees with a fact above, the README follows the code and the discrepancy is noted in the card. Code is not changed.

## Tests

No new test. This is a docs-only card, and there is no README docs test in `tests/` to extend. The integration spec's fallback ("add the commands to the contract tests") is already covered: every shape the README states is pinned by existing tests in this worktree.

- `tests/test_cli.py:3639`, `:3847`, `:3902`, `:3624`, `:3699`, `:3792`, `:3806`. **Unit** (fake board, `run_board` patched, no subprocess).
- 5.1's `tests/test_orchestrate.py` board payload key-set test and `tests/test_store.py` journal test. **Unit** (fake seams, and a real `Store` in `tmp_path` with no subprocess).
- `tests/e2e/test_run_board.py` (`:186`, `:214`, `:237`, `:276`, `:292`, `:307`). **e2e_fake** (production wiring under the fake claude, auto-marked by directory and correct for what they spawn).

Verification: `uv run pytest` stays green, which proves nothing changed. Then review the README text against the facts listed above. If a reviewer insists on a text assertion, it would be a pure string check on README.md: unmarked **unit**, in `tests/test_cli.py`. The default is not to add one.

## Out of scope

`--detach` (beyond the one-line usage-error mention that already exists), `am runs` fields, `am logs --follow`, `--from-now`, the "Listing runs" section, and any code, payload or journal change.

Note: the exploration summary handed to this stage was truncated at 8000 characters, in the middle of the list of 5.1 test names. The missing names were recovered from 5.1's design doc and from this worktree's `tests/test_cli.py`.

---

# Document `am run --board` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a README subsection that documents `am run --board` and `am run --board --dry-run` (what a board run is, its flags, refusals, run and dry-run payloads, `--max-concurrent`, one journal per milestone, recovery), plus two small edits elsewhere in README.md. No code changes.

**Architecture:** A README-only change. The facts come from this worktree's code and are already pinned by existing unit tests (`tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_store.py`) and e2e_fake tests (`tests/e2e/test_run_board.py`). Per the spec there is no new test file. Each task's "failing check" is a `grep` against README.md that fails before the edit and passes after it. The checks are run by hand, never committed, and are not tests. The suite (`uv run pytest`) is the regression guard, and it shows that no code changed.

**Tech Stack:** Markdown (GitHub-flavoured, `README.md`), `grep`, `uv run pytest`.

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933/docs/superpowers/specs/task-5-2-document-am-run-4881c933-design.md` (reproduced verbatim above).

## Global Constraints

- README.md only. No source change, no new JSON key, the journal stays schema 1.
- No new test. If a reviewer insists on a README text assertion, it is an unmarked **unit** test in `tests/test_cli.py`, a pure string check with no subprocess. The default is not to add one.
- Do not add new "ignore unknown keys" wording. Leave README.md:461 ("Listing runs" `milestone_id` bullet) unchanged.
- Out of scope: `--detach` (beyond the existing one-line usage-error mention), `am runs` fields, `am logs --follow`, `--from-now`, the "Listing runs" section.
- Do not say the base branch is looked up in git. The code only checks `not base_branch`.
- Exit code for a board run: 1 only when some entry is `escalated`. `blocked`, `stopped` and `cancelled` only means exit 0 with `data.ok` false.
- No full JSON sample unless it is copied from a shape the 5.1 tests pin. This plan uses the README's existing brace notation (`{"level", "milestones"}`) instead.
- No hard-wrapped prose in new README paragraphs. Each paragraph or bullet is one line, as in the newer sections of README.md (for example :93-105 and :203-223).
- If the code turns out to disagree with a fact here, the README follows the code and the discrepancy is noted on card 4881c933. Code is not changed.
- Verification: `uv run pytest` (unit + git tiers) stays green.

## Review Focus

These are the five things a reader of the new docs is most likely to get wrong, so the text must state each one plainly. Each has a grep check in Task 3, Step 2.

1. A board where milestones are only `blocked`, `stopped` or `cancelled` exits **0**, with `data.ok` false and the outer envelope `ok` still true. A script that checks only the exit code would treat that as success, so the README must say to read `data.ok` (pinned by `tests/test_cli.py::test_a_board_run_exits_escalated_only_when_some_milestone_escalated`).
2. `--max-concurrent N` is one pool shared by the **whole board**, not N per milestone. A reader who expects N stories per milestone would size it wrong (pinned by `tests/test_orchestrate.py::test_run_board_runs_every_milestone_on_one_shared_semaphore` and the e2e_fake shared-slot tests).
3. A given `--branch-prefix P` is never used verbatim. It becomes `P-<stem>` for each milestone, so the integration branch is `P-<stem>-integrate`, not `P-integrate` (pinned by `tests/test_cli.py::test_board_prefix_of_with_a_prefix_joins_it_to_the_stem_and_never_reuses_it_verbatim`).
4. The synthetic story ids `integrate`, `bases` and `base-<story id>` recur across the milestones' journals. A consumer that keys stories by `story` alone would merge them, so the key must be `(run_id, story)` (pinned by `tests/e2e/test_run_board.py::test_two_independent_milestones_both_finish`).
5. There is no top-level `run_id` and no board-level journal. A consumer that looks for `data.run_id` finds nothing. It must take each entry's `run_id` (pinned by `tests/test_cli.py::test_a_board_run_envelope_wraps_run_boards_keys_unchanged` and `tests/test_orchestrate.py::test_run_board_payload_has_one_run_per_milestone_and_no_board_level_run`).

---

## File Structure

- Modify: `README.md`
  - `:68` the "`--branch-prefix` is required." sentence gets a `--board` qualifier.
  - `:80-85` the usage-error list gains the `--board` combinations.
  - insert a new `#### Running every open milestone with `--board`` subsection between the end of "Preview with `--dry-run`" (last line `:136`) and "#### What a clean run leaves behind" (`:138`).
  - `:550` the synthetic-ids / `milestone_id` bullet in "Reading the stream safely".
- No other file changes. No test file is created or modified.

The new heading's GitHub anchor is `#running-every-open-milestone-with---board`: GitHub lowercases the text, drops the backticks, turns each space into `-` and keeps the two dashes of `--board`. Every cross-link below uses that anchor.

Existing anchors linked from the new text, all already in README.md: `#preview-with---dry-run` (:107), `#integrate` (:163), `#parallel-runs` (:203), `#several-am-processes` (:225), `#relaunching-resumes` (:337), `#reading-the-stream-safely` (:543), `#the-journal-line` (:511), `#multiple-blockers` (:278).

---

### Task 1: The new `--board` subsection

**Files:**
- Modify: `README.md`, inserted before line 138 (`#### What a clean run leaves behind`)
- Test: none new. Existing contract tests are run as the guard (unit tier, default suite).

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the heading `#### Running every open milestone with `--board`` and its anchor `#running-every-open-milestone-with---board`. Task 2 links to that anchor.

- [ ] **Step 1: Confirm the contract the text will describe is green before writing**

Run:

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
uv run pytest tests/test_cli.py tests/test_orchestrate.py tests/test_store.py -k "board or journal_names_its_milestone" -q
```

Expected: all selected tests PASS. They include `test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id`, `test_a_board_run_envelope_wraps_run_boards_keys_unchanged`, `test_a_board_run_exits_escalated_only_when_some_milestone_escalated`, `test_run_board_payload_has_one_run_per_milestone_and_no_board_level_run` and `test_a_milestone_runs_journal_names_its_milestone_once_at_the_head`. If any fail, stop: the branch is not the 5.1-stacked state the spec assumes.

- [ ] **Step 2: Write the failing check**

Run:

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
grep -c -F '#### Running every open milestone with `--board`' README.md
grep -c -F 'one slot pool for the whole board' README.md
```

Expected: both print `0` (grep exits 1). The section does not exist yet.

- [ ] **Step 3: Insert the subsection**

In `README.md`, use Edit with `old_string` set to the exact line

```
#### What a clean run leaves behind
```

(it appears once, at :138), and `new_string` set to the block below followed by a blank line and that same heading line. The block, verbatim:

````markdown
#### Running every open milestone with `--board`

```bash
am run --board --verify "uv run pytest" [--branch-prefix P] [--max-concurrent N]
am run --board --dry-run --pretty
```

`--board` drives every open milestone on the board in one command, as one dependency graph. Each milestone runs exactly as `am run --milestone` would run it: its stories, its [merged bases](#multiple-blockers) and its own [Integrate](#integrate) into its own `<prefix>-integrate`. Across milestones:

- Milestones are leveled by the `blocked_by` edges between them. A milestone that is done, with nothing open under it, drops out, and so does a blocker that is not an open milestone: it counts as satisfied.
- A milestone starts once every open milestone blocking it has finished `done`.
- If a blocker ends in any other status (`escalated`, `stopped`, `cancelled`, or `blocked` itself), the milestone is never started: no run, no branch, no worktree, no board change. It is reported `blocked`.
- A milestone whose run raises an error is reported `escalated`, and the other milestones carry on.
- A board with no open milestone is `ok`, runs nothing and exits 0.

Flags. Give exactly one of `--card`, `--milestone` and `--board`. `--verify`, `--allow-no-verification`, `--base-branch` (default `master`) and `--repo-dir` apply to every milestone. `--branch-prefix` is optional with `--board`. Without it, each milestone's prefix is its own card stem, `<title slug>-<first 8 hex of the card id>`. With `--branch-prefix P`, each milestone's prefix is `P-<stem>`, never `P` itself, so milestone M's integration branch is `P-<stem of M>-integrate`. `--board` with `--card` or `--milestone`, a blank `--branch-prefix` with `--board`, and `--detach` with `--board` are usage errors (exit 2).

Refusals. These come in this order, before anything is written. Each prints `{"ok": false, "error": {"type", "message"}}` and exits 3:

1. A blank `--base-branch`.
2. A blocker cycle between milestones (`DependencyCycleError`).
3. A blank prefix, or a prefix two milestones share.
4. A claim another live run holds, checked once over the claims of every open milestone together (`ClaimedError`, see [Several am processes](#several-am-processes)).

A refused board run leaves no run row, no run directory and no lease for any milestone. A board that cannot be read is refused the same way, as on a `--milestone` run.

The run's `data` is `{"ok", "board": true, "levels", "milestones"}`. It has no `run_id` of its own.

- `levels` is a list of `{"level", "milestones"}`, where `milestones` lists milestone ids. As with `--milestone`, levels are a way to read the plan: a milestone waits only for its own blockers.
- `milestones` has one entry per open milestone, in level order. Each entry has one of three shapes:
  - A milestone that ran: `{"milestone_id", "status", ...}`, followed by every key of that milestone's own `--milestone` report (see [What a clean run leaves behind](#what-a-clean-run-leaves-behind) and [What an escalation report contains](#what-an-escalation-report-contains)), its `run_id` included. `status` is `done`, `escalated`, `stopped` (a pause) or `cancelled`.
  - A milestone that never started: `{"milestone_id", "status": "blocked", "blocked_by"}`. `blocked_by` lists the ids of its blockers that did not finish `done`.
  - A milestone whose run raised an error: `{"milestone_id", "status": "escalated", "error"}`, with `error` reading `"<Type>: <message>"`. It has no `run_id` key. One example is a claim that another run took after the board's up-front check.
- `ok` is `true` only when every entry is `done`.

The outer envelope's `ok` is `true` whatever the outcome, because the report itself is a true result. The exit code is 1 only when some entry is `escalated`. A board whose milestones are only `done`, `blocked`, `stopped` or `cancelled` exits 0, even when `data.ok` is `false`. Read `data.ok`, not the exit code, to know whether everything finished.

`--dry-run` with `--board` is read-only. It opens no store, checks no claim, writes nothing, and exits 0. It still refuses a blocker cycle, a bad prefix and a board that cannot be read, the same way as above. Its `data` is `{"board": true, "max_concurrent", "levels"}`, with no `ok` and no `run_id`. `levels` is a list of `{"level", "milestones"}`, and each milestone is `{"milestone_id", "title", "branch_prefix", "plan"}`. `branch_prefix` is the prefix that milestone will run under. `plan` is exactly that milestone's own `--milestone --dry-run` data (`max_concurrent`, `levels`, `already_done`, `integrate`). Read each plan as described in [Preview with `--dry-run`](#preview-with---dry-run), `base` column included.

`--max-concurrent N` is one slot pool for the whole board, not N per milestone. Every story of every milestone takes a slot from the same N, so N caps the stories running at once across the board. It defaults to 4, as with `--milestone`, and `--max-concurrent 1` runs one story at a time on the whole board. Integrate merges do not take a slot (see [Integrate](#integrate)). Everything else in [Parallel runs](#parallel-runs) holds inside each milestone.

One run and one journal per milestone. A board run is not a run itself: nothing records it as a whole. Each milestone it starts is a run of its own, with its own run id, `<data dir>/runs/<run-id>/journal.jsonl` and lease, exactly as a `--milestone` run would be. To follow a board run, take each entry's `run_id` (or find the runs in `am runs`) and read each journal separately with `am watch <run-id>`, or read them all with `am watch --all`. In each journal:

- The first line is a `run_upsert` whose `payload.milestone_id` is that milestone's full card id. It is never `null` on a board run.
- Every line has that run's `run_id`.
- A `story_upsert` line has the story card id in `story`, and its `payload` has no milestone key. A story belongs to the milestone named on the first line of its journal.
- A real story id appears in only one milestone's journal. The synthetic ids `"integrate"`, `"bases"` and `"base-<story id>"` (see [Reading the stream safely](#reading-the-stream-safely)) are fixed names that may appear in several milestones' journals, so identify a story by `(run_id, story)`, never by `story` alone.

Recovery. Because nothing records the board run as a whole, there is nothing to resume at the board level. After a fix, either:

- run the same `am run --board` command again. Done milestones drop out, and each other open milestone starts again as a relaunch would (see [Relaunching resumes](#relaunching-resumes)), or
- continue one `stopped` or `escalated` milestone on its own with `am resume <run-id>`, using that entry's `run_id`.

A `blocked` milestone has no run to resume. It starts on a later `am run --board`, once its blockers are done.
````

Notes for the implementer, checked against this worktree's code:
- The exit-code rule follows `cli.py:1758-1767` (`any(entry.get("status") == "escalated" ...)`).
- The entry shapes follow `orchestrate.py:2411-2417` (dispatch) and `:2429-2434` (blocked).
- `stopped` for a paused milestone follows `milestone_status`, `orchestrate.py:2244-2259`.
- The refusal order follows `orchestrate.py:2311-2325`. The `max_concurrent < 1` ValueError is left out because the CLI already refuses it as a usage error (`cli.py:1546`), and that refusal is covered in Task 2's list.
- The stem format follows `dag.task_stem` (`dag.py:77-81`).
- "Integrate merges do not take a slot" restates the existing README:200 ("`--max-concurrent` does not apply").

- [ ] **Step 4: Run the check to verify it passes**

Run:

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
grep -c -F '#### Running every open milestone with `--board`' README.md
grep -c -F 'one slot pool for the whole board' README.md
grep -n -F '#### What a clean run leaves behind' README.md
grep -n -F '#### Running every open milestone with `--board`' README.md
```

Expected: the first two print `1`. The new heading's line number is lower than that of "What a clean run leaves behind", and higher than that of `#### Preview with `--dry-run`` (107).

- [ ] **Step 5: Run the suite to confirm nothing else changed**

Run: `cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933 && uv run pytest -q`
Expected: PASS (same count as before the edit; only README.md changed).

- [ ] **Step 6: Commit**

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
git add README.md
git commit -m "docs(readme): document am run --board and its dry run (card 4881c933)"
```

---

### Task 2: Usage-error list, the `--branch-prefix` sentence and the journal bullet

**Files:**
- Modify: `README.md:68` (`--branch-prefix` sentence), `README.md:80-85` (usage errors), and the bullet at `README.md:550` ("Know the synthetic ids"; its line number has moved after Task 1, so match it by text)
- Test: none new.

**Interfaces:**
- Consumes: the anchor `#running-every-open-milestone-with---board` from Task 1.
- Produces: nothing other tasks use.

- [ ] **Step 1: Write the failing check**

Run:

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
grep -c -F '`--board` together with `--card` or with `--milestone`' README.md
grep -c -F 'the milestone'"'"'s id on a `--milestone` or `--board` run' README.md
grep -c -F 'optional with `--board`' README.md
```

Expected: the first two print `0`. The third prints `1`, from Task 1's subsection. After this task it prints `2`.

- [ ] **Step 2: Qualify the `--branch-prefix` sentence at README.md:68**

Edit `README.md`. `old_string`:

```
`--branch-prefix` is required. Every branch the run cuts is named
```

`new_string`:

```
`--branch-prefix` is required with `--card` and `--milestone`, and optional with `--board` (see [Running every open milestone with `--board`](#running-every-open-milestone-with---board)). Every branch the run cuts is named
```

- [ ] **Step 3: Extend the usage-error list at README.md:80-85**

Edit `README.md`. `old_string` (exact, five lines):

```
Some combinations are refused before anything is read: `--card` together with
`--milestone`, neither of them, a blank `--milestone`, `--dry-run` with
`--card`, `--max-concurrent` with `--card`, a `--max-concurrent` below 1, and
`--detach` with `--dry-run` or with `--board`.
These are usage errors, like a missing `--branch-prefix`: Typer prints the
message on stderr, nothing is printed on stdout, and the exit code is 2.
```

`new_string`:

```
Some combinations are refused before anything is read: `--card` together with `--milestone`, `--board` together with `--card` or with `--milestone`, none of the three, a blank `--milestone`, a missing `--branch-prefix` with `--card` or `--milestone`, a blank `--branch-prefix` with `--board`, `--dry-run` with `--card`, `--max-concurrent` with `--card`, a `--max-concurrent` below 1 (with `--milestone` or `--board`), and `--detach` with `--dry-run` or with `--board`.
These are usage errors: Typer prints the message on stderr, nothing is printed on stdout, and the exit code is 2.
```

(This follows `cli._check_run_targets`, `cli.py:1496-1555`. The `--detach` item is kept once, not added again.)

- [ ] **Step 4: Extend the synthetic-ids / `milestone_id` bullet in "Reading the stream safely"**

Edit `README.md`. `old_string`:

```
A run's `repo_dir` and `milestone_id` (`null` on a `--card` run) are in the `payload` of its first line, a `run_upsert`.
```

`new_string`:

```
A run's `repo_dir` and `milestone_id` (`null` on a `--card` run, the milestone's id on a `--milestone` or `--board` run) are in the `payload` of its first line, a `run_upsert`. A `--board` run has no journal of its own: each milestone it starts is a run with its own journal, and the synthetic ids can recur across them, so key them by `(run_id, story)` (see [Running every open milestone with `--board`](#running-every-open-milestone-with---board)).
```

Leave the "Listing runs" `milestone_id` bullet (README.md:461 before Task 1) unchanged, and add no "ignore unknown keys" wording.

- [ ] **Step 5: Run the check to verify it passes**

Run:

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
grep -c -F '`--board` together with `--card` or with `--milestone`' README.md
grep -c -F 'the milestone'"'"'s id on a `--milestone` or `--board` run' README.md
grep -c -F 'optional with `--board`' README.md
grep -c -F 'like a missing `--branch-prefix`' README.md
grep -c -F 'It is `null` on a `--card` run, and on a run recorded by an `am` too old to store it.' README.md
```

Expected: `1`, `1`, `2`, `0`, `1` (the last shows that the Listing runs bullet is untouched).

- [ ] **Step 6: Run the suite**

Run: `cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933 && uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
git add README.md
git commit -m "docs(readme): list the --board usage errors and its per-milestone journals (card 4881c933)"
```

---

### Task 3: Fact review against the code and the pinned tests

**Files:**
- Read only: `README.md`, `src/agent_manager/cli.py`, `src/agent_manager/orchestrate.py`, `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_store.py`, `tests/e2e/test_run_board.py`
- Modify: `README.md`, only if a check below fails

**Interfaces:**
- Consumes: the README text from Tasks 1 and 2.
- Produces: nothing.

- [ ] **Step 1: Check the links resolve**

Run:

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
for h in '#### Preview with `--dry-run`' '#### Integrate' '#### Parallel runs' '#### Several am processes' '#### Relaunching resumes' '#### Reading the stream safely' '#### Multiple blockers' '#### What a clean run leaves behind' '#### What an escalation report contains' '#### Running every open milestone with `--board`'; do printf '%s -> ' "$h"; grep -c -F -x "$h" README.md; done
```

Expected: every heading prints `1`. If one prints `0`, its link in the new text points nowhere. Correct the link text to match the real heading.

- [ ] **Step 2: Check each Review Focus item is stated**

Run:

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
grep -c -F 'Read `data.ok`, not the exit code' README.md
grep -c -F 'not N per milestone' README.md
grep -c -F 'never `P` itself' README.md
grep -c -F 'never by `story` alone' README.md
grep -c -F 'It has no `run_id` of its own.' README.md
```

Expected: each prints `1`.

- [ ] **Step 3: Re-read the new text against the code, fact by fact**

Open README.md at the new subsection and check each claim against its source. Fix any wording that does not match the code, and note the discrepancy on card 4881c933. Do not change code.

| README claim | Source to compare |
|---|---|
| exit 1 only when some entry is `escalated` | `src/agent_manager/cli.py:1758-1767`; `tests/test_cli.py:3886-3915` |
| outer envelope `ok` true either way | `tests/test_cli.py:3847` `test_a_board_run_envelope_wraps_run_boards_keys_unchanged` |
| three entry shapes; `stopped` is a pause | `src/agent_manager/orchestrate.py:2244-2259`, `:2411-2434` |
| refusal order and nothing left behind | `src/agent_manager/orchestrate.py:2311-2325`; `tests/test_orchestrate.py:6915`, `:6933`, `:6945`, `:6991` |
| empty board is ok and runs nothing | `src/agent_manager/orchestrate.py:2323-2324`; `tests/test_orchestrate.py:6958`; `tests/test_cli.py:3624` |
| dry-run keys, no `ok`/`run_id`, refusals | `src/agent_manager/cli.py:1253-1300`; `tests/test_cli.py:3639`, `:3699` |
| prefix `<stem>` or `P-<stem>` | `src/agent_manager/cli.py:1237-1250`; `src/agent_manager/dag.py:77-81`; `tests/test_cli.py:3297`, `:3305` |
| usage errors | `src/agent_manager/cli.py:1496-1555`; `tests/test_cli.py:3379`, `:3431`, `:10551` |
| one shared slot pool, default 4 | `src/agent_manager/orchestrate.py:2381`, `:2409`; `src/agent_manager/cli.py:1667`; `tests/test_orchestrate.py:7007`; `tests/e2e/test_run_board.py:299`, `:315` |
| one run/journal per milestone, `milestone_id` at head, no milestone key on `story_upsert`, synthetic ids recur | `tests/test_orchestrate.py:7156`; `tests/test_store.py:3112`; `tests/e2e/test_run_board.py:186-232` |
| recovery: relaunch or `am resume <run-id>` | `src/agent_manager/orchestrate.py:2304-2309` (docstring) |

- [ ] **Step 4: (Optional, opt-in tier) Run the e2e_fake board tests**

Run: `cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933 && uv run pytest -m e2e_fake tests/e2e/test_run_board.py -q`
Expected: PASS. This tier is opt-in (up to about 8 minutes) and is not part of `uv run pytest`. Run it when the fake `claude` is available, to confirm the shared-slot and journal facts the README states.

- [ ] **Step 5: Final verification**

Run: `cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933 && uv run pytest -q && git status --short && git diff master --stat -- src tests`
Expected:
- pytest PASSES.
- `git status --short` lists nothing under `src/` or `tests/`. It shows only `README.md` if Step 3 changed it, and this plan or spec under `docs/superpowers/` if the workflow has not committed them yet.
- the `src`/`tests` diff against master shows only 5.1's test changes, which this branch inherits. Compare it with `git diff ami/task-5-1-pin-the-board-a7fcc076 --stat -- src tests`, which must be empty.

- [ ] **Step 6: Commit (only if Step 3 changed README.md)**

```bash
cd /home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-2-document-am-run-4881c933
git add README.md
git commit -m "docs(readme): align the --board section with the code (card 4881c933)"
```
