# docs_commit backfills the trailer on unstamped commits (card 70b703d9)

Subtask of story 32cd1358. It is blocked by c06c167b, which told the four document roles never to run `git commit`. This card adds the check behind that instruction. If a draft commit lands on the task branch before `docs_commit` runs anyway, `docs_commit` stamps it with the current `Plan-Hash` trailer. It does this by rewriting the unmerged task branch, before anything depends on its SHAs. Humans did the same backfill by hand in Milestones 14 and 17. This card makes it deterministic.

## Inherited constraints

- `docs_commit` is a deterministic step: git and the filesystem only, with no model call, no board access and no `brd` (design §4 package layout, `docs/superpowers/specs/2026-09-23-agent-manager-design.md:74-137`; module docstring `src/agent_manager/steps/docs_commit.py:1-12`). It runs after `mark_validated` and before `implement` (pygents addendum §7, `docs/superpowers/specs/2026-09-25-pygents-engine-design.md:315-321`; wiring `src/agent_manager/workflow/task.py:95-96`).
- `plan_hash` is "the `docs_commit` step's digest of the validated plan" (design §7, `…/2026-09-23-agent-manager-design.md:297`). The digest is still computed exactly as it is today (`docs_commit.py:187`).
- The `Plan-Hash` trailer is how a resumed `implement` tells work to keep from debris. A commit carrying the current hash is resumed from. An untagged commit is treated as debris and can be hard-reset away (design §9, `…/2026-09-23-agent-manager-design.md:384-387`; `review_gate`, design §5 `:244-245` and `src/agent_manager/steps/reducers.py:198-225`).
- Review counts trailers with `git log <base>..HEAD --format=%B | grep -c "^Plan-Hash: $PLAN_HASH"` (pygents addendum §7, `…/2026-09-25-pygents-engine-design.md:340-345`). That pattern is anchored at the start of the line, so the backfilled trailer must start at column 0 of its own line, written as `TRAILER_PREFIX` + digest exactly (`docs_commit.py:42-47`).
- A plan hash is exactly 8 lowercase hex characters (`reducers.is_plan_hash`, `src/agent_manager/steps/reducers.py:235-237`).
- Every phase must be idempotent or explicitly re-entrant (design §9, `…/2026-09-23-agent-manager-design.md:377-389`). This step may add and commit. It must never sweep (`add -A`, `add .`), destroy (`reset`, `clean`, `checkout -f`, `rm`, `restore`) or publish (`push`). `tests/steps/test_docs_commit.py:180-199` pins that rule, and it covers every argv the step issues, the backfill's included.
- Every git invocation goes through the injected `GitRunner` (`Callable[[list[str]], str]`, `src/agent_manager/steps/worktree.py:36`), as argument lists with no shell string (`docs_commit.py:9-11`). This card leaves the `GitRunner` alias and `run_git` unchanged.
- Engine binding is by parameter name. `walk.subtask_context` already sets `base_branch` to `subtask.base_branch` (`src/agent_manager/runtime/walk.py:100-110`), so adding a `base_branch` parameter to `commit_documents` binds it with no change to `workflow/task.py` or `runtime/walk.py`.
- Test tiers come from design §14 (`…/2026-09-23-agent-manager-design.md:507-527`) and CLAUDE.md "Test tiers": real git in `tmp_path` is the `git` tier, and `tests/steps/` is auto-marked `git` (`tests/conftest.py:128`).

## Behaviour

### Signature

```python
def commit_documents(
    card_details: object,
    spec_path: str,
    plan_path: str,
    worktree: str | Path,
    base_branch: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]
```

`base_branch` is required and has no default. A value that is not a `str`, or that is blank after stripping, raises `ValueError` before any git runs. The message contains `needs a non-empty base_branch`. This joins the existing pre-flight checks (`docs_commit.py:171-185`).

### Order inside the step

1. Pre-flight validation (unchanged, plus `base_branch`).
2. `digest = plan_hash(plan_file.read_bytes())` (unchanged).
3. **Backfill** (new; defined below).
4. Today's add / staged-check / commit / `_branch_carries` logic, unchanged.

The backfill must run before step 4. In the Milestone 14/17 case, a role committed the spec and plan drafts itself, so nothing is staged and `_branch_carries` decides. Once those drafts are stamped, `_branch_carries` finds the trailer and the step returns the hash. Without the backfill, the step raises `UntaggedDocumentsError`.

### Return value

`{"plan_hash": digest, "backfilled": [<sha>, ...]}`. `backfilled` lists the pre-rewrite SHAs (full 40-hex) of the commits that gained the trailer, oldest first. It is `[]` when the backfill did nothing, for whatever reason. `plan_hash` keeps its current meaning and key. The prompt's `INPUT_PRODUCERS` (`tests/test_prompt.py:547`) and `plan_hash_gate` read only that key.

### Which commits are candidates

- **The range** is every commit reachable from `HEAD` and not reachable from `base_branch`. When `origin/<base_branch>` resolves, commits reachable from it are excluded too. Review uses the bare `base_branch` (pygents §7 `:344`). This range deliberately differs: `worktree.ensure` may have branched from `origin/<base>` (`worktree.py:155-170`), and a local base behind its remote would otherwise put upstream commits in range. Rewriting upstream commits must never happen. Review then still counts them as untagged and blocks with its existing message, which is the correct outcome.
- **Stamped**: a commit whose full message (`%B`) has at least one line equal to `Plan-Hash: ` followed by a value that `reducers.is_plan_hash` accepts. Any such hash counts, including a stale one from an earlier plan (the card: "Commits already carrying ANY Plan-Hash trailer are left alone"). Matching is whole-line, as in `_branch_carries`.
- **Unstamped**: every other commit in the range. That includes a commit carrying a malformed value such as `Plan-Hash: zzz`, which review's grep would not count either.

### When the backfill does nothing (branch ref, index and working tree untouched, `backfilled == []`)

- The range is empty, or no commit in it is unstamped.
- `base_branch` does not resolve to a commit (`rev-parse --verify --quiet <base>^{commit}` fails). Step 4 then behaves exactly as today. If review's range does not resolve either, review reports that failure, not this step.
- Some commit in the range has more than one parent (a merge) or no parent (a root). The card puts branches carrying merges from other tasks out of scope, and `review_gate`'s "only N of M commits … carry their Plan-Hash trailer … Do NOT re-run" message (`reducers.py:217-225`) already covers them.
- `HEAD` is detached (`symbolic-ref -q HEAD` fails). The step does not rewrite a ref it cannot name.

### The rewrite

The range is walked oldest first (`rev-list --reverse --topo-order`).

- Commits before the first unstamped commit keep their SHAs. They are not recreated.
- From the first unstamped commit on, each commit is recreated with:
  - the same tree (byte-identical content);
  - its parent replaced by the recreated parent (the first rewritten commit keeps its original parent);
  - the same `author` and `committer` header lines: name, email, timestamp and timezone, all preserved;
  - **unstamped**: the original message plus the trailer `Plan-Hash: <digest>` (rule below);
  - **stamped**: the original message, byte-for-byte unchanged. Its SHA still changes, because its parent did.
  - Any `gpgsig` / `gpgsig-sha256` header is not carried over. A rewritten commit is unsigned, as after a rebase.
- The branch `HEAD` points at moves exactly once: `update-ref -m "docs_commit: backfill Plan-Hash <digest>" <ref> <new-tip> <old-tip>`. Passing the expected old value makes the move a compare-and-swap. If the ref moved meanwhile, git refuses and the `GitError` propagates. The reflog entry keeps the pre-rewrite tip recoverable.
- The step never runs `reset`, `checkout`, `rebase`, `filter-branch`, `cherry-pick` or `commit --amend`. Because every tree is identical, `git status --porcelain` and the index are the same before and after. A file already staged by someone else stays staged and is still not swept into the docs commit (the existing partial-commit test, `tests/steps/test_docs_commit.py:164-177`).
- Any temporary file the mechanism needs lives outside the worktree and is removed afterwards. An extra file in the worktree would fail `review_gate`'s porcelain check.

**Trailer rule.** This is a pure function from (message, digest) to message. Strip trailing newlines from the message. If the message has more than one paragraph and its last paragraph is a git trailer block, append `\n` + `Plan-Hash: <digest>`. A trailer block here means every line matches `^[A-Za-z0-9-]+: `, for example `Co-Authored-By: …`. Otherwise append `\n\n` + `Plan-Hash: <digest>`. End with a single `\n`. Two observable checks pin the result: git parses it as a trailer (`%(trailers:key=Plan-Hash,valueonly)` yields the digest), and review's `^Plan-Hash: <digest>` grep counts it.

**Mechanism note (non-binding).** `GitRunner` cannot pass environment variables, so `commit-tree` cannot carry over the author and committer dates. One argv-only route that keeps both lines exactly: read `cat-file commit <sha>`, rebuild the raw object text, write it to a temp file outside the worktree, and run `hash-object -t commit -w <file>`. The planner may choose another route, provided it meets every rule above and goes through `git_runner`.

### Error paths

- Invalid `base_branch` → `ValueError` before any git (above).
- Any `GitError` during the backfill propagates and the step fails. Before the final `update-ref`, the branch ref, index and working tree are untouched. At worst, unreachable objects are left for `gc`.
- `update-ref` refuses because the ref moved concurrently → `GitError` propagates. The branch keeps whatever the other writer put there.
- `UntaggedDocumentsError` remains reachable in every case where the backfill does nothing: a merge in range, an unresolvable base, a detached HEAD, or the untagged documents commit sitting at or below `base_branch`.

### Idempotence

A second call on the same branch finds nothing unstamped. The backfill is a no-op, the commit count is unchanged, and the result is `{"plan_hash": <same>, "backfilled": []}`.

## Tests

All behaviour tests live in `tests/steps/test_docs_commit.py`, which mirrors `src/agent_manager/steps/docs_commit.py`. Real `git` in `tmp_path` puts them in the `git` tier. The directory is auto-marked `git` by `tests/conftest.py` (CLAUDE.md "Test tiers"). No `brd`, no `claude`, ≤2s each. The trailer-rule tests (test 13) are pure string functions and spawn nothing. They sit in the same file as the existing pure `plan_hash` tests (`:23-45`) and, like those, take the directory's auto `git` mark. There is no `unit` marker to opt back out with (`tests/conftest.py:128-132`), and a pure test easily fits the `git` budget. Give them no explicit marker.

Test-harness changes:

- `_run`'s default `arguments` gains `"base_branch": "main"`. Every existing test runs with `HEAD` on `main`, so the range is empty and the backfill is a no-op. That keeps every existing test's assertions valid unchanged. This explicitly includes `test_documents_committed_without_a_trailer_raise_instead_of_lying` (`:233-249`): its untagged commit is at the base and out of range.
- A helper (for example `_task_branch(repo)`) creates and checks out `task` from `main`. A helper commits a file with a given message. Backfill tests then pass `base_branch="main"`.
- The return value gains the `backfilled` key on every call, including a no-op one (above), so `test_the_returned_hash_is_the_hash_of_the_plan_file_on_disk` (`:138-145`) changes its exact-equality assertion from `result == {"plan_hash": expected}` to `result == {"plan_hash": expected, "backfilled": []}`. No other existing assertion reads the full dict; the rest key into `result["plan_hash"]` or compare two calls' results to each other, both unaffected by the added key.

New tests:

1. `test_unstamped_drafts_gain_the_current_hash_and_the_stamped_one_is_untouched` (git). On `task`, commit unstamped draft A, then a commit B whose message ends `Plan-Hash: 0123abcd` (a different valid hash), then unstamped draft C. Write the documents and run. Then:
   - The `%B` of A′ and C′ each end with `Plan-Hash: <digest>`, and git parses it as a trailer.
   - B′'s `%B` equals B's byte-for-byte and does not contain the new digest.
   - `git diff <pre-rewrite tip> <rewritten C′>` is empty.
   - Author name, email and date are equal per commit, and so are committer name, email and date.
   - `backfilled == [A, C]`, the old SHAs, oldest first.
   - The docs commit sits on top with its trailer.
   - The `main` ref is unchanged.
2. `test_a_branch_with_nothing_unstamped_is_left_exactly_as_it_was` (git). `task` holds only commits stamped with the digest, or with another valid hash. Before the call, record `rev-parse task` and the ordered list of SHAs. After the call, the pre-existing SHAs are identical (the only new commit is the docs commit) and `backfilled == []`.
3. `test_commits_below_the_first_unstamped_keep_their_shas` (git). The order is stamped S, then unstamped U. S′ == S (same SHA). U is rewritten.
4. `test_drafts_that_already_hold_the_documents_are_stamped_instead_of_raising` (git). This is the Milestone 14/17 scenario. On `task`, commit the final spec and plan with no trailer. `_run(base_branch="main")` returns `plan_hash` without raising. No new commit is made. The draft's rewritten message carries the trailer.
5. `test_a_merge_commit_in_range_leaves_the_branch_untouched` (git). `task` contains a merge commit and an unstamped commit. After the call, every pre-existing SHA is unchanged and `backfilled == []`. The docs commit still lands.
6. `test_an_unresolvable_base_leaves_the_branch_untouched` (git). `base_branch="no-such-branch"`. No rewrite, `backfilled == []`, and the docs commit still lands.
7. `test_commits_reachable_from_origin_base_are_never_rewritten` (git). Create `refs/remotes/origin/main` pointing at an unstamped commit U0 that is ahead of local `main`, with `task` branched from it plus one unstamped commit U1. U0 keeps its SHA. Only U1 is rewritten.
8. `test_the_backfill_leaves_index_and_worktree_as_they_were` (git). A staged unrelated file and an unstaged edit exist before the call. Afterwards both are still present (`diff --cached --name-only`, `status --porcelain` for that path), no file outside the two documents was committed, and no temp file appears in the worktree.
9. `test_a_second_call_after_a_backfill_is_a_no_op` (git). Run twice on the scenario from test 1. The second result has `backfilled == []`, the same `plan_hash`, and an unchanged `rev-parse HEAD`.
10. `test_the_backfill_moves_the_ref_once_and_records_it_in_the_reflog` (git). On the test 1 scenario, use the recording runner. Exactly one `update-ref` argv is issued, and it names the old tip as the expected value. `reflog -1 task` contains `docs_commit: backfill Plan-Hash`.
11. Extend `test_the_step_runs_no_forbidden_git_verb` to run on the test 1 scenario as well as today's, so the backfill's argv go through the guard. Also forbid `rebase`, `filter-branch`, `cherry-pick` and `--amend`.
12. Extend the pre-flight `ValueError` parametrize (`:252-289`) with `({"base_branch": ""}, "needs a non-empty base_branch")`, `({"base_branch": "   "}, …)` and `({"base_branch": None}, …)`, still using `_exploding_runner`.
13. `test_trailer_rule[…]` (pure; `git` by directory auto-mark, see above). Parametrized over: subject only (`"wip"` → `"wip\n\nPlan-Hash: d\n"`), subject + body, a message ending in a `Co-Authored-By:` block (appended to the block with no blank line), extra trailing newlines, and a body whose last paragraph is prose containing a colon mid-line (blank-line separated).

Existing tests that must stay green, modulo the one-line change to `test_the_returned_hash_is_the_hash_of_the_plan_file_on_disk` above: the whole of `tests/steps/test_docs_commit.py`, `tests/workflow/test_task.py:95-115` (wiring), `tests/test_engine.py` (fakes bind by name and are unaffected), `tests/test_prompt.py`, and the `e2e_fake` `tests/e2e/test_production_wiring.py:180-205`. Under the fake `claude`, nothing commits before `docs_commit`, so the docs commit stays last and is tagged.

## Out of scope

- Branches with merge commits, or root commits, in range. They are left untouched. Review's gate message covers them (card).
- Re-stamping commits that carry a stale but valid `Plan-Hash`. They are left alone (card). `plan_hash_gate` and `review_gate` own that case.
- Non-UTF-8 commit objects, or ones with an `encoding` header. `run_git` decodes text, so such a commit makes the step fail loudly with the branch untouched. Nothing handles them specially.
- Any change to `GitRunner`, `run_git`, `worktree.ensure`, `review_gate`, `plan_hash_gate`, `workflow/task.py`, `runtime/walk.py` or the reviewer bundle's commands.
- The role-bundle instruction itself, which is sibling card c06c167b (done).
- Rewriting anything other than the branch `HEAD` points at in this worktree. Pushing anything.

## Verification

`uv run pytest` (default `unit` + `git` tiers) and `uv run pytest -m e2e_fake` both pass. There is no lint or typecheck command.

---

# docs_commit Backfills the Plan-Hash Trailer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before committing the documents, `docs_commit.commit_documents` stamps every unstamped commit on the unmerged task branch with the current `Plan-Hash` trailer. It rewrites the branch through raw commit objects and one compare-and-swap `update-ref`.

**Architecture:** Everything lives in `src/agent_manager/steps/docs_commit.py`. A pure `with_trailer(message, digest)` implements the trailer rule. A private `_backfill(...)` lists the range with `rev-list`, reads each commit with `cat-file commit`, rebuilds the raw object text from the first unstamped commit onwards (same tree, same author/committer lines, new parent, signature headers dropped), writes each object with `hash-object -t commit -w <tempfile outside the worktree>`, and moves the branch once with `update-ref -m … <ref> <new> <old>`. `commit_documents` gains a required `base_branch` parameter, runs `_backfill` between computing the digest and today's add/commit logic, and returns `{"plan_hash": …, "backfilled": [...]}`.

**Tech Stack:** Python 3.12, pytest, real `git` (2.55 on the dev box) in `tmp_path`, `uv`.

**Spec:** `docs/superpowers/specs/docs-commit-backfills-70b703d9.md` (reproduced verbatim above this plan).

## Global Constraints

- `docs_commit` is a deterministic step: git and the filesystem only, with no model call, no board access and no `brd`.
- Every git invocation goes through the injected `GitRunner` (`Callable[[list[str]], str]`), as argument lists with no shell string. `GitRunner` and `run_git` stay unchanged.
- This step may add and commit. It must never sweep (`add -A`, `add .`), destroy (`reset`, `clean`, `checkout -f`, `rm`, `restore`) or publish (`push`). It never runs `reset`, `checkout`, `rebase`, `filter-branch`, `cherry-pick` or `commit --amend`.
- The backfilled trailer is `TRAILER_PREFIX` + digest exactly (`Plan-Hash: <8 lowercase hex>`), at column 0 of its own line.
- A plan hash is exactly 8 lowercase hex characters (`reducers.is_plan_hash`).
- `base_branch` is required, no default; a non-`str` or blank value raises `ValueError` containing `needs a non-empty base_branch` before any git runs.
- Return value: `{"plan_hash": digest, "backfilled": [<full 40-hex pre-rewrite sha>, ...]}`, oldest first, `[]` on a no-op.
- The branch ref moves exactly once: `update-ref -m "docs_commit: backfill Plan-Hash <digest>" <ref> <new-tip> <old-tip>`.
- Any temporary file lives outside the worktree and is removed afterwards.
- No change to `workflow/task.py`, `runtime/walk.py`, `worktree.ensure`, `review_gate`, `plan_hash_gate` or the reviewer bundle. Engine binding is by parameter name; `walk.subtask_context` already supplies `base_branch`.
- Tests live in `tests/steps/test_docs_commit.py` (auto-marked `git`), no explicit markers, ≤2s each. Verification: `uv run pytest` and `uv run pytest -m e2e_fake`.

## Review Focus

- A draft whose message already ends in a `Co-Authored-By:` block (what every agent commit in this repo looks like): `Plan-Hash` must join that block with no blank line, and git must still parse both as trailers. Pinned by `test_trailer_rule[co-authored-block]` (Task 1) and by draft A in `test_unstamped_drafts_gain_the_current_hash_and_the_stamped_one_is_untouched` (Task 3).
- A one-line message that itself looks like a trailer (`fix: the thing`): it is a subject, not a trailer block, so the trailer goes after a blank line. If it were glued on, the subject would turn into `fix: the thing\nPlan-Hash: …`. Pinned by `test_trailer_rule[subject-that-looks-like-a-trailer]` (Task 1).
- A signed commit (`gpgsig` header, multi-line continuation) in range: the rewritten commit carries no signature header and no stray continuation lines, and is still stamped. Pinned by `test_a_signed_commit_is_rewritten_unsigned` (Task 5).
- A commit carrying a malformed `Plan-Hash: zzz`: it counts as unstamped (review's grep would not count it) and gains the real trailer. Pinned by `test_a_malformed_plan_hash_does_not_count_as_stamped` (Task 3).
- Another writer moves the branch between the step's read and its `update-ref`: the CAS refuses, the `GitError` propagates, and the other writer's commit stays the tip. Pinned by `test_a_branch_moved_meanwhile_makes_the_update_ref_refuse` (Task 3).

---

## File Structure

- Modify: `src/agent_manager/steps/docs_commit.py`. Add the trailer rule, the stamped predicate, raw-commit helpers, `_backfill`, the `base_branch` parameter and the new return key. This is still one responsibility, since the backfill exists only to serve this step, and the spec places it here.
- Modify: `tests/steps/test_docs_commit.py`. Harness default `base_branch`, new helpers, new tests and two extended tests.
- Modify (comment only): `tests/workflow/test_task.py:107-108`. The binding comment lists the parameters the engine binds by name; add `base_branch`.
- Modify: `tests/test_engine.py:265-296`. `test_the_engine_can_bind_the_docs_commit_step_out_of_the_subtask_context` asserts the exact bound dict, so it must supply and expect `base_branch`. **This contradicts the spec's line that `tests/test_engine.py` is "unaffected".** Running the suite against the finished plan showed the test fails with `EngineError: … parameter 'base_branch': no value for a required parameter`. The spec's intent holds: binding is by name, and production's `walk.subtask_context` supplies `base_branch`. Only this test's hand-built context lacks it.

---

### Task 1: The trailer rule (pure)

**Files:**
- Modify: `src/agent_manager/steps/docs_commit.py` (imports at `:14-18`; new function after `TRAILER_PREFIX`, `:42-47`)
- Test: `tests/steps/test_docs_commit.py` (after `test_plan_hash_changes_when_one_byte_of_the_plan_changes`, `:35-42`)

**Interfaces:**
- Consumes: `docs_commit.TRAILER_PREFIX = "Plan-Hash: "`.
- Produces: `docs_commit.with_trailer(message: str, digest: str) -> str`.

- [ ] **Step 1: Write the failing test**

Insert into `tests/steps/test_docs_commit.py` directly after `test_plan_hash_changes_when_one_byte_of_the_plan_changes` (ends at line 42):

```python
TRAILER_DIGEST = "a1b2c3d4"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param("wip", "wip\n\nPlan-Hash: a1b2c3d4\n", id="subject-only"),
        pytest.param(
            "subject\n\nbody line one\nbody line two\n",
            "subject\n\nbody line one\nbody line two\n\nPlan-Hash: a1b2c3d4\n",
            id="subject-and-body",
        ),
        pytest.param(
            "subject\n\nbody\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
            "subject\n\nbody\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
            "Plan-Hash: a1b2c3d4\n",
            id="co-authored-block",
        ),
        pytest.param(
            "subject\n\nbody\n\n\n\n",
            "subject\n\nbody\n\nPlan-Hash: a1b2c3d4\n",
            id="extra-trailing-newlines",
        ),
        pytest.param(
            "subject\n\nNote this: the colon is mid-line prose\n",
            "subject\n\nNote this: the colon is mid-line prose\n\nPlan-Hash: a1b2c3d4\n",
            id="prose-with-a-colon",
        ),
        pytest.param(
            "fix: the thing",
            "fix: the thing\n\nPlan-Hash: a1b2c3d4\n",
            id="subject-that-looks-like-a-trailer",
        ),
    ],
)
def test_trailer_rule(message: str, expected: str) -> None:
    """Pure: (message, digest) -> message. A trailer block is the LAST paragraph
    of a multi-paragraph message whose every line is `Token: value`; the new
    trailer joins it. Anything else gets a blank line first. A lone subject is
    never a trailer block, however much it looks like one."""
    assert docs_commit.with_trailer(message, TRAILER_DIGEST) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/steps/test_docs_commit.py -k test_trailer_rule -v`
Expected: 6 FAIL with `AttributeError: module 'agent_manager.steps.docs_commit' has no attribute 'with_trailer'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/steps/docs_commit.py`, change the imports (lines 14-18) to:

```python
import hashlib
import os
import re
from pathlib import Path

from agent_manager.steps.worktree import GitError, GitRunner, run_git
```

Then insert directly after the `TRAILER_PREFIX` docstring (after line 47, before `class UntaggedDocumentsError`):

```python
_TRAILER_LINE = re.compile(r"[A-Za-z0-9-]+: ")
"""A git trailer line's shape: `Token: value`, matched at the line start."""

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
"""A blank (or whitespace-only) line between two paragraphs of a message."""


def with_trailer(message: str, digest: str) -> str:
    """`message` with `Plan-Hash: <digest>` appended as a git trailer.

    Pure. Trailing newlines are stripped first. If the message has more than
    one paragraph and its last paragraph is a trailer block (every line
    `Token: value`, e.g. `Co-Authored-By: ...`), the trailer joins that block;
    otherwise it starts a new paragraph. The result ends with one newline, and
    the trailer sits at column 0 of its own line so review's anchored
    `^Plan-Hash: <hash>` grep counts it.
    """
    body = message.rstrip("\n")
    paragraphs = _PARAGRAPH_BREAK.split(body)
    last = paragraphs[-1].split("\n")
    joins_block = len(paragraphs) > 1 and all(
        _TRAILER_LINE.match(line) for line in last
    )
    separator = "\n" if joins_block else "\n\n"
    return f"{body}{separator}{TRAILER_PREFIX}{digest}\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/steps/test_docs_commit.py -k test_trailer_rule -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py
git commit -m "feat: docs_commit trailer rule appends Plan-Hash as a git trailer (70b703d9)"
```

---

### Task 2: `base_branch` parameter and the `backfilled` key

**Files:**
- Modify: `src/agent_manager/steps/docs_commit.py` (new validator after `_required_title`, `:121-133`; signature and body of `commit_documents`, `:158-219`)
- Modify: `tests/steps/test_docs_commit.py` (`_run` `:96-105`; `test_the_returned_hash_is_the_hash_of_the_plan_file_on_disk` `:138-145`; the pre-flight parametrize `:259-290`)
- Modify: `tests/workflow/test_task.py:107-108` (comment only)
- Modify: `tests/test_engine.py:265-296`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `commit_documents(card_details, spec_path, plan_path, worktree, base_branch: str, git_runner: GitRunner = run_git) -> dict[str, object]`, returning `{"plan_hash": str, "backfilled": list[str]}`. Also `_required_base_branch(value: object) -> str`. In tests, `_run(root, **overrides)` now defaults `base_branch="main"`.

- [ ] **Step 1: Update the harness and write the failing tests**

In `tests/steps/test_docs_commit.py`, replace `_run` (lines 96-105) with:

```python
def _run(root: Path, **overrides):
    """Call the step against `root` with the standard arguments.

    `base_branch` is `main`: every test that never leaves `main` has an empty
    backfill range, so the backfill is a no-op there.
    """
    arguments = {
        "card_details": FakeCard(title=TITLE),
        "spec_path": SPEC_RELATIVE,
        "plan_path": PLAN_RELATIVE,
        "worktree": str(root),
        "base_branch": "main",
    }
    arguments.update(overrides)
    return docs_commit.commit_documents(**arguments)
```

In `test_the_returned_hash_is_the_hash_of_the_plan_file_on_disk`, replace the line

```python
    assert result == {"plan_hash": expected}
```

with

```python
    assert result == {"plan_hash": expected, "backfilled": []}
```

In the `test_a_bad_argument_raises_value_error_before_any_git_runs` parametrize list, after the line `({"spec_path": "../escape.md"}, "outside the worktree"),`, add:

```python
        ({"base_branch": ""}, "needs a non-empty base_branch"),
        ({"base_branch": "   "}, "needs a non-empty base_branch"),
        ({"base_branch": None}, "needs a non-empty base_branch"),
```

In `tests/test_engine.py`, replace `test_the_engine_can_bind_the_docs_commit_step_out_of_the_subtask_context` (lines 265-296) with:

```python
def test_the_engine_can_bind_the_docs_commit_step_out_of_the_subtask_context():
    """The phase carries no `args`, so all five parameters have to come from the
    context by name -- `card_details`, `worktree` and `base_branch` from
    `walk.subtask_context`, `spec_path` and `plan_path` from
    `walk._document_paths`. `git_runner` has a default and must NOT be bound
    out of a context that happens to hold no such key."""
    card = models.Card(
        id="6f1a2f2e-1f1c-4f0e-9a6d-0c2f3b4a5d6e",
        title="Commit the spec and plan with the Plan-Hash trailer",
        status="todo",
    )
    bound = walk.bind_arguments(
        docs_commit.commit_documents,
        {
            "card": "ba15da20",
            "card_details": card,
            "worktree": Path("/repo/.claude/worktrees/m2/task-docs-ba15da20"),
            "base_branch": "m2/story-docs",
            "plan_path": "docs/superpowers/plans/task-docs-ba15da20.md",
            "spec_path": "docs/superpowers/specs/task-docs-ba15da20.md",
            "commands": ["uv run pytest"],
        },
        None,
        phase="docs_commit",
        function="docs_commit.commit_documents",
    )

    assert bound == {
        "card_details": card,
        "spec_path": "docs/superpowers/specs/task-docs-ba15da20.md",
        "plan_path": "docs/superpowers/plans/task-docs-ba15da20.md",
        "worktree": Path("/repo/.claude/worktrees/m2/task-docs-ba15da20"),
        "base_branch": "m2/story-docs",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/steps/test_docs_commit.py tests/test_engine.py -k "docs_commit or bad_argument or returned_hash or step_makes" -v`
Expected: the `commit_documents` tests FAIL with `TypeError: commit_documents() got an unexpected keyword argument 'base_branch'`, and the engine binding test FAILS on the dict comparison (no `base_branch` key bound). The `plan_hash` and `test_trailer_rule` tests still PASS.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/steps/docs_commit.py`, insert after `_required_title` (after line 133):

```python
def _required_base_branch(value: object) -> str:
    """The non-blank base branch name, or `ValueError` before any git call."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(
            f"docs_commit.commit_documents needs a non-empty base_branch, got {value!r}"
        )
    return value.strip()
```

Change the signature of `commit_documents` to:

```python
def commit_documents(
    card_details: object,
    spec_path: str,
    plan_path: str,
    worktree: str | Path,
    base_branch: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
```

After `title = _required_title(card_details)` add:

```python
    base_branch = _required_base_branch(base_branch)
```

After `digest = plan_hash(plan_file.read_bytes())` add:

```python
    backfilled: list[str] = []
```

Change both `return {"plan_hash": digest}` lines (inside the `staged.strip() == ""` branch and at the end) to:

```python
            return {"plan_hash": digest, "backfilled": backfilled}
```

and

```python
    return {"plan_hash": digest, "backfilled": backfilled}
```

In `tests/workflow/test_task.py`, change the comment lines 107-108 from

```python
    # No args: `bind_arguments` takes card_details, spec_path, plan_path and
    # worktree from the context by parameter name. Not best-effort and not
```

to

```python
    # No args: `bind_arguments` takes card_details, spec_path, plan_path,
    # worktree and base_branch from the context by parameter name. Not best-effort and not
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/steps/test_docs_commit.py tests/workflow/test_task.py tests/test_engine.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py tests/workflow/test_task.py tests/test_engine.py
git commit -m "feat: docs_commit takes a required base_branch and reports backfilled shas (70b703d9)"
```

---

### Task 3: The backfill rewrite (core path)

**Files:**
- Modify: `src/agent_manager/steps/docs_commit.py` (module docstring `:1-12`; imports; new helpers before `commit_documents`; call site after the digest)
- Test: `tests/steps/test_docs_commit.py` (imports `:9-16`; new helpers after `_recorder` `:116-123`; replace `test_the_step_runs_no_forbidden_git_verb` `:180-199`; new tests appended at the end of the file)

**Interfaces:**
- Consumes: `with_trailer(message: str, digest: str) -> str` (Task 1); `commit_documents(..., base_branch, git_runner)` and the `backfilled` list (Task 2); `reducers.is_plan_hash(value: object) -> bool`.
- Produces: `_is_stamped(message: str) -> bool`, `_split_commit(raw: str) -> tuple[list[str], str]`, `_rebuild_commit(headers: list[str], parent: str, message: str) -> str`, `_backfill(git_runner: GitRunner, worktree_path: str, base_branch: str, digest: str) -> list[str]`. Also these test helpers: `_task_branch(root)`, `_commit_file(root, name, message, *, author=None, author_date=None, committer_date=None) -> str`, `_rev(root, revision) -> str`, `_range(root, tip="HEAD") -> list[str]`, `_digest(root) -> str`, `_identity(root, revision) -> str`, `_drafts_scenario(root) -> dict[str, str]`, and the constant `OTHER_HASH = "0123abcd"`.

- [ ] **Step 1: Add the test helpers**

In `tests/steps/test_docs_commit.py`, change the imports (lines 9-16) to:

```python
import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_manager.steps import docs_commit, reducers
```

Insert directly after `_recorder` (ends at line 123):

```python
OTHER_HASH = "0123abcd"
"""A valid Plan-Hash that is not the plan's: a commit stamped by an earlier plan."""

DRAFT_AUTHOR = "Draft Author <draft@example.com>"


def _task_branch(root: Path) -> None:
    """Create and check out `task` from `main`."""
    _git(root, "checkout", "-q", "-b", "task", "main")


def _commit_file(
    root: Path,
    name: str,
    message: str,
    *,
    author: str | None = None,
    author_date: str | None = None,
    committer_date: str | None = None,
) -> str:
    """Write `name`, commit it alone with `message`, return the new full sha.

    Fixed dates in a foreign timezone make "the rewrite kept the dates" a real
    check: a rewrite that stamped "now" would differ in every field.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{name}: {message}\n", encoding="utf-8")
    _git(root, "add", "--", name)
    env = dict(os.environ)
    if author_date is not None:
        env["GIT_AUTHOR_DATE"] = author_date
    if committer_date is not None:
        env["GIT_COMMITTER_DATE"] = committer_date
    argv = ["git", "-C", str(root), "commit", "-q", "-m", message]
    if author is not None:
        argv += ["--author", author]
    subprocess.run(argv, capture_output=True, text=True, check=True, env=env)
    return _rev(root, "HEAD")


def _rev(root: Path, revision: str) -> str:
    return _git(root, "rev-parse", "--verify", revision).strip()


def _range(root: Path, tip: str = "HEAD") -> list[str]:
    """`main..tip`, oldest first."""
    return _git(root, "rev-list", "--reverse", f"main..{tip}").split()


def _digest(root: Path) -> str:
    return hashlib.sha256((root / PLAN_RELATIVE).read_bytes()).hexdigest()[:8]


def _identity(root: Path, revision: str) -> str:
    """Author and committer name, email and raw date (with timezone)."""
    return _git(
        root, "show", "-s", "--date=raw", "--format=%an%n%ae%n%ad%n%cn%n%ce%n%cd", revision
    )


def _drafts_scenario(root: Path) -> dict[str, str]:
    """`task` off `main`: unstamped draft A (ending in a Co-Authored-By block),
    B stamped with another valid hash, unstamped draft C. The documents are
    written but not committed. Returns the original shas."""
    _task_branch(root)
    a = _commit_file(
        root,
        "drafts/a.md",
        "draft spec\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        author=DRAFT_AUTHOR,
        author_date="2001-02-03T04:05:06+0530",
        committer_date="2002-03-04T05:06:07-0700",
    )
    b = _commit_file(
        root,
        "drafts/b.md",
        f"stamped by an earlier plan\n\nPlan-Hash: {OTHER_HASH}",
        author_date="2003-04-05T06:07:08+0100",
        committer_date="2004-05-06T07:08:09+0000",
    )
    c = _commit_file(
        root,
        "drafts/c.md",
        "draft plan",
        author_date="2005-06-07T08:09:10-0300",
        committer_date="2006-07-08T09:10:11+0900",
    )
    _write_documents(root)
    return {"A": a, "B": b, "C": c}
```

- [ ] **Step 2: Write the failing tests**

Replace `test_the_step_runs_no_forbidden_git_verb` (lines 180-199) with:

```python
@pytest.mark.parametrize("scenario", ["documents_only", "unstamped_drafts"])
def test_the_step_runs_no_forbidden_git_verb(repo: Path, scenario: str) -> None:
    """Design §9: this step may add and commit. Sweeping (`add -A`, `add .`) or
    destroying (`reset`, `clean`, `checkout -f`) or publishing (`push`) is how a
    resumed run loses a human's work. The backfill rewrites history, so it
    must do that without `rebase`, `filter-branch`, `cherry-pick` or
    `commit --amend` too."""
    if scenario == "unstamped_drafts":
        _drafts_scenario(repo)
    else:
        _write_documents(repo)
    calls: list[list[str]] = []

    _run(repo, git_runner=_recorder(calls, docs_commit.run_git))

    assert calls  # non-vacuity: a step that ran no git at all would pass emptily
    if scenario == "unstamped_drafts":
        # non-vacuity: the backfill's own argv went through the guard below
        assert any("update-ref" in argv for argv in calls), calls
    for argv in calls:
        assert "-A" not in argv, argv
        assert "--all" not in argv, argv
        assert "--amend" not in argv, argv
        for verb in (
            "reset",
            "clean",
            "push",
            "rm",
            "restore",
            "rebase",
            "filter-branch",
            "cherry-pick",
        ):
            assert verb not in argv, argv
        if "add" in argv:
            assert "." not in argv, argv
            assert "*" not in " ".join(argv), argv
        if "checkout" in argv:
            assert "-f" not in argv, argv
```

Append to the end of `tests/steps/test_docs_commit.py`:

```python
def test_unstamped_drafts_gain_the_current_hash_and_the_stamped_one_is_untouched(
    repo: Path,
) -> None:
    """The Milestone 14/17 hand backfill, made deterministic: A and C gain the
    current trailer; B keeps its own message byte-for-byte; trees, authors,
    committers and dates all survive; `main` never moves."""
    original = _drafts_scenario(repo)
    main_before = _rev(repo, "main")
    old_tip = _rev(repo, "HEAD")
    before_messages = {
        key: _git(repo, "show", "-s", "--format=%B", sha) for key, sha in original.items()
    }
    before_identity = {key: _identity(repo, sha) for key, sha in original.items()}
    digest = _digest(repo)

    result = _run(repo)

    assert result == {"plan_hash": digest, "backfilled": [original["A"], original["C"]]}
    a_new, b_new, c_new, docs = _range(repo)
    rewritten = {"A": a_new, "B": b_new, "C": c_new}
    for key in ("A", "C"):
        message = _git(repo, "show", "-s", "--format=%B", rewritten[key])
        assert message.rstrip("\n").endswith(f"Plan-Hash: {digest}"), message
        parsed = _git(
            repo, "show", "-s", "--format=%(trailers:key=Plan-Hash,valueonly)", rewritten[key]
        )
        assert parsed.strip() == digest
    co_authored = _git(
        repo, "show", "-s", "--format=%(trailers:key=Co-Authored-By,valueonly)", a_new
    )
    assert co_authored.strip() == "Claude <noreply@anthropic.com>"
    b_message = _git(repo, "show", "-s", "--format=%B", b_new)
    assert b_message == before_messages["B"]
    assert digest not in b_message
    assert b_new != original["B"]  # its parent changed, so its sha did too
    assert _git(repo, "diff", old_tip, c_new) == ""
    for key, sha in rewritten.items():
        assert _identity(repo, sha) == before_identity[key], key
    assert _message(repo, docs).splitlines()[0] == f"docs: add spec and plan for {TITLE}"
    assert _message(repo, docs).splitlines()[-1] == f"Plan-Hash: {digest}"
    assert _rev(repo, "main") == main_before
    # Review's own count: every commit on the branch now carries the trailer.
    log = _git(repo, "log", "main..HEAD", "--format=%B")
    assert sum(line == f"Plan-Hash: {digest}" for line in log.splitlines()) == 3


def test_a_branch_with_nothing_unstamped_is_left_exactly_as_it_was(repo: Path) -> None:
    _task_branch(repo)
    _write_documents(repo)
    digest = _digest(repo)
    _commit_file(repo, "a.txt", f"current\n\nPlan-Hash: {digest}")
    _commit_file(repo, "b.txt", f"earlier\n\nPlan-Hash: {OTHER_HASH}")
    old_tip = _rev(repo, "task")
    before = _range(repo)

    result = _run(repo)

    assert result["backfilled"] == []
    assert _rev(repo, "HEAD~1") == old_tip
    assert _range(repo, "HEAD~1") == before


def test_commits_below_the_first_unstamped_keep_their_shas(repo: Path) -> None:
    _task_branch(repo)
    stamped = _commit_file(repo, "s.txt", f"stamped\n\nPlan-Hash: {OTHER_HASH}")
    unstamped = _commit_file(repo, "u.txt", "unstamped")
    _write_documents(repo)

    result = _run(repo)

    assert result["backfilled"] == [unstamped]
    s_new, u_new, _docs = _range(repo)
    assert s_new == stamped
    assert u_new != unstamped


def test_drafts_that_already_hold_the_documents_are_stamped_instead_of_raising(
    repo: Path,
) -> None:
    """The Milestone 14/17 scenario: a role committed the final spec and plan
    itself. Nothing is staged, so `_branch_carries` decides -- and after the
    backfill it finds the trailer instead of raising UntaggedDocumentsError."""
    _task_branch(repo)
    _write_documents(repo)
    _git(repo, "add", "--", SPEC_RELATIVE, PLAN_RELATIVE)
    _git(repo, "commit", "-q", "-m", "docs: drafts committed by the role")
    draft = _rev(repo, "HEAD")
    before = _commit_count(repo)

    result = _run(repo)

    assert result == {"plan_hash": _digest(repo), "backfilled": [draft]}
    assert _commit_count(repo) == before
    assert _message(repo).splitlines()[-1] == f"Plan-Hash: {_digest(repo)}"


def test_the_backfill_leaves_index_and_worktree_as_they_were(repo: Path) -> None:
    """Identical trees mean the index and the working tree cannot tell the
    rewrite happened: a staged file stays staged (and out of the docs commit),
    an unstaged edit stays unstaged, and no temp file lands in the worktree."""
    _task_branch(repo)
    unstamped = _commit_file(repo, "notes.txt", "unstamped draft")
    _write_documents(repo)
    (repo / "README.md").write_text("staged by somebody else\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    (repo / "notes.txt").write_text("edited, not staged\n", encoding="utf-8")
    documents = {f"?? {SPEC_RELATIVE}", f"?? {PLAN_RELATIVE}"}
    before = set(_git(repo, "status", "--porcelain", "--untracked-files=all").splitlines())
    assert documents <= before

    result = _run(repo)

    assert result["backfilled"] == [unstamped]
    after = set(_git(repo, "status", "--porcelain", "--untracked-files=all").splitlines())
    assert after == before - documents
    assert "M  README.md" in after
    assert " M notes.txt" in after
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted([SPEC_RELATIVE, PLAN_RELATIVE])


def test_a_second_call_after_a_backfill_is_a_no_op(repo: Path) -> None:
    _drafts_scenario(repo)
    first = _run(repo)
    head = _rev(repo, "HEAD")

    second = _run(repo)

    assert first["backfilled"] != []  # non-vacuity: the first call rewrote
    assert second == {"plan_hash": first["plan_hash"], "backfilled": []}
    assert _rev(repo, "HEAD") == head


def test_the_backfill_moves_the_ref_once_and_records_it_in_the_reflog(repo: Path) -> None:
    _drafts_scenario(repo)
    old_tip = _rev(repo, "HEAD")
    digest = _digest(repo)
    calls: list[list[str]] = []

    _run(repo, git_runner=_recorder(calls, docs_commit.run_git))

    updates = [argv for argv in calls if "update-ref" in argv]
    assert len(updates) == 1, updates
    update = updates[0]
    assert "refs/heads/task" in update
    assert update[-1] == old_tip  # the expected old value: a compare-and-swap
    reflog = _git(repo, "reflog", "--format=%gs", "task").splitlines()
    assert f"docs_commit: backfill Plan-Hash {digest}" in reflog
    # task@{0} is the docs commit, task@{1} the backfilled tip, task@{2} the
    # pre-rewrite tip -- still recoverable.
    assert _rev(repo, "task@{2}") == old_tip


def test_a_malformed_plan_hash_does_not_count_as_stamped(repo: Path) -> None:
    """Review's grep would not count `Plan-Hash: zzz`, so the backfill must
    not treat it as stamped either."""
    _task_branch(repo)
    malformed = _commit_file(repo, "m.txt", "draft\n\nPlan-Hash: zzz")
    _write_documents(repo)
    digest = _digest(repo)

    result = _run(repo)

    assert result["backfilled"] == [malformed]
    m_new, _docs = _range(repo)
    lines = _message(repo, m_new).splitlines()
    assert "Plan-Hash: zzz" in lines
    assert lines[-1] == f"Plan-Hash: {digest}"


def test_a_branch_moved_meanwhile_makes_the_update_ref_refuse(repo: Path) -> None:
    """The final `update-ref` names the old tip, so a writer that moved the
    branch between the step's read and its write wins: git refuses and the
    `GitError` propagates."""
    _drafts_scenario(repo)

    def racing_runner(argv: list[str]) -> str:
        if "update-ref" in argv:
            _git(repo, "commit", "-q", "--allow-empty", "-m", "racer")
        return docs_commit.run_git(argv)

    with pytest.raises(docs_commit.GitError):
        _run(repo, git_runner=racing_runner)

    assert _message(repo) == "racer"
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected FAIL:
- `test_the_step_runs_no_forbidden_git_verb[unstamped_drafts]`: no `update-ref` in `calls`.
- `test_unstamped_drafts_gain_…`, `test_commits_below_…`, `test_the_backfill_leaves_…`, `test_a_second_call_after_a_backfill_…`, `test_a_malformed_plan_hash_…`: `backfilled` is `[]`.
- `test_drafts_that_already_hold_the_documents_…`: `UntaggedDocumentsError`.
- `test_the_backfill_moves_the_ref_once_…`: `len(updates) == 0`.
- `test_a_branch_moved_meanwhile_…`: `DID NOT RAISE`.

`test_a_branch_with_nothing_unstamped_…` already PASSES, because nothing needs stamping. It pins that the backfill keeps it that way. Every other test PASSES.

- [ ] **Step 4: Write the implementation**

In `src/agent_manager/steps/docs_commit.py`, replace the module docstring (lines 1-12) with:

```python
"""Commit the spec and the plan, tagged with the plan's Plan-Hash trailer.

A deterministic step (design §4 `steps/`, §6): git and the filesystem only --
no model call, no board access, no `brd`. It runs after `mark_validated` and
before `implement`, because the hash it stamps is the hash of the plan file
*with* the validated marker already on disk, which is exactly the hash `review`
recomputes independently (design §9).

Before committing, it backfills: any commit on the unmerged task branch that
carries no Plan-Hash trailer (a draft a role committed despite being told not
to) is rewritten to carry the current one, so `review` does not read it as
debris. The rewrite rebuilds raw commit objects -- same tree, same author and
committer lines -- and moves the branch once with a compare-and-swap
`update-ref`. Nothing is reset, checked out or rebased, so the index and the
working tree never notice.

Every invocation is an argument list handed to `subprocess` through the
injected `GitRunner` (the seam `steps/worktree.py` established): there is no
shell string and nothing to quote.
"""
```

Change the imports to:

```python
import hashlib
import os
import re
import tempfile
from pathlib import Path

from agent_manager.steps import reducers
from agent_manager.steps.worktree import GitError, GitRunner, run_git
```

Insert directly before `def commit_documents(` (after `_document_paths`):

```python
def _is_stamped(message: str) -> bool:
    """Whether some line of `message` is `Plan-Hash: ` plus a well-formed hash.

    Any valid hash counts, a stale one included: such commits are left alone.
    `Plan-Hash: zzz` does not count, exactly as review's grep would not.
    """
    return any(
        line.startswith(TRAILER_PREFIX)
        and reducers.is_plan_hash(line[len(TRAILER_PREFIX) :].rstrip())
        for line in message.split("\n")
    )


def _split_commit(raw: str) -> tuple[list[str], str]:
    """A raw `cat-file commit` object as (header lines, message)."""
    headers, _, message = raw.partition("\n\n")
    return headers.split("\n"), message


def _rebuild_commit(headers: list[str], parent: str, message: str) -> str:
    """The raw text of a commit object: `headers` with the parent replaced.

    Every other header line -- `tree`, `author`, `committer` -- is carried
    over verbatim, which is how name, email, timestamp and timezone survive.
    """
    kept = [f"parent {parent}" if line.startswith("parent ") else line for line in headers]
    return "\n".join(kept) + "\n\n" + message


def _backfill(
    git_runner: GitRunner, worktree_path: str, base_branch: str, digest: str
) -> list[str]:
    """Stamp every unstamped commit in `base_branch..HEAD` with `digest`.

    Returns the pre-rewrite shas of the commits that gained the trailer,
    oldest first. Commits before the first unstamped one keep their shas;
    from there on each is recreated on its recreated parent. The branch moves
    once, as a compare-and-swap against the tip read here.
    """
    ref = git_runner(["-C", worktree_path, "symbolic-ref", "-q", "HEAD"]).strip()
    old_tip = git_runner(
        ["-C", worktree_path, "rev-parse", "--verify", "HEAD^{commit}"]
    ).strip()
    listing = git_runner(
        [
            "-C",
            worktree_path,
            "rev-list",
            "--reverse",
            "--topo-order",
            "--parents",
            old_tip,
            f"^{base_branch}",
            "--",
        ]
    )
    rows = [line.split() for line in listing.splitlines() if line.strip()]
    commits = [
        (row[0], git_runner(["-C", worktree_path, "cat-file", "commit", row[0]]))
        for row in rows
    ]
    first = next(
        (
            index
            for index, (_, raw) in enumerate(commits)
            if not _is_stamped(_split_commit(raw)[1])
        ),
        None,
    )
    if first is None:
        return []

    parent = rows[first][1]
    backfilled: list[str] = []
    # Outside the worktree: an extra file there would fail review's porcelain
    # check. `GitRunner` has no stdin, so `hash-object` reads a file.
    with tempfile.TemporaryDirectory(prefix="agent-manager-docs-commit-") as scratch:
        object_file = Path(scratch) / "commit"
        for sha, raw in commits[first:]:
            headers, message = _split_commit(raw)
            if not _is_stamped(message):
                message = with_trailer(message, digest)
                backfilled.append(sha)
            object_file.write_bytes(_rebuild_commit(headers, parent, message).encode("utf-8"))
            parent = git_runner(
                ["-C", worktree_path, "hash-object", "-t", "commit", "-w", str(object_file)]
            ).strip()

    git_runner(
        [
            "-C",
            worktree_path,
            "update-ref",
            "-m",
            f"docs_commit: backfill Plan-Hash {digest}",
            ref,
            parent,
            old_tip,
        ]
    )
    return backfilled
```

In `commit_documents` (not inside `_backfill`, which has a line that looks the same), replace the line `backfilled: list[str] = []` that Task 2 added right after `digest = plan_hash(...)` with:

```python
    # Before the add/commit below: when a role already committed the documents
    # themselves, nothing is staged and `_branch_carries` decides -- which it
    # can only answer yes to once those drafts carry the trailer.
    backfilled = _backfill(git_runner, worktree_path, base_branch, digest)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py
git commit -m "feat: docs_commit backfills Plan-Hash onto unstamped task-branch commits (70b703d9)"
```

---

### Task 4: When the backfill must do nothing

**Files:**
- Modify: `src/agent_manager/steps/docs_commit.py` (`_backfill` from Task 3; new `_resolves` helper right above it)
- Test: `tests/steps/test_docs_commit.py` (append at the end)

**Interfaces:**
- Consumes: `_backfill(git_runner, worktree_path, base_branch, digest) -> list[str]` (Task 3); test helpers `_task_branch`, `_commit_file`, `_rev`, `_range`, `_message`, `_digest`, `_write_documents`, `_run` (Tasks 2-3).
- Produces: `_resolves(git_runner: GitRunner, worktree_path: str, revision: str) -> bool`. `_backfill` returns `[]` with the branch untouched when the base does not resolve, `HEAD` is detached, or the range holds a merge or a root commit. It also excludes `origin/<base_branch>` from the range when that resolves.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_docs_commit.py`:

```python
def _assert_untouched_and_docs_landed(repo: Path, old_tip: str, result: dict) -> None:
    """No rewrite: the pre-call tip is the docs commit's parent, unchanged."""
    assert result["backfilled"] == []
    assert _rev(repo, "HEAD~1") == old_tip
    assert _message(repo).splitlines()[0] == f"docs: add spec and plan for {TITLE}"


def test_a_merge_commit_in_range_leaves_the_branch_untouched(repo: Path) -> None:
    """Out of scope by the card: review's gate message already covers a branch
    carrying merges, so the backfill must not half-handle one."""
    _task_branch(repo)
    _commit_file(repo, "x.txt", "unstamped on task")
    _git(repo, "checkout", "-q", "-b", "side", "main")
    _commit_file(repo, "y.txt", "unstamped on side")
    _git(repo, "checkout", "-q", "task")
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge side")
    _commit_file(repo, "u.txt", "unstamped after the merge")
    _write_documents(repo)
    old_tip = _rev(repo, "HEAD")

    result = _run(repo)

    _assert_untouched_and_docs_landed(repo, old_tip, result)


def test_a_root_commit_in_range_leaves_the_branch_untouched(repo: Path) -> None:
    _git(repo, "checkout", "-q", "--orphan", "task")
    _commit_file(repo, "orphan.txt", "unstamped root")
    _write_documents(repo)
    old_tip = _rev(repo, "HEAD")

    result = _run(repo)

    _assert_untouched_and_docs_landed(repo, old_tip, result)


def test_an_unresolvable_base_leaves_the_branch_untouched(repo: Path) -> None:
    _task_branch(repo)
    _commit_file(repo, "u.txt", "unstamped")
    _write_documents(repo)
    old_tip = _rev(repo, "HEAD")

    result = _run(repo, base_branch="no-such-branch")

    _assert_untouched_and_docs_landed(repo, old_tip, result)


def test_a_detached_head_leaves_the_branch_untouched(repo: Path) -> None:
    """The step does not rewrite a ref it cannot name."""
    _task_branch(repo)
    _commit_file(repo, "u.txt", "unstamped")
    _git(repo, "checkout", "-q", "--detach", "task")
    _write_documents(repo)
    old_tip = _rev(repo, "HEAD")

    result = _run(repo)

    _assert_untouched_and_docs_landed(repo, old_tip, result)
    assert _rev(repo, "task") == old_tip


def test_commits_reachable_from_origin_base_are_never_rewritten(repo: Path) -> None:
    """`worktree.ensure` may branch from `origin/<base>` while the local base
    lags behind it; upstream commits must never be rewritten."""
    _git(repo, "checkout", "-q", "-b", "upstream", "main")
    u0 = _commit_file(repo, "u0.txt", "unstamped upstream commit")
    _git(repo, "update-ref", "refs/remotes/origin/main", u0)
    _git(repo, "checkout", "-q", "-b", "task", "upstream")
    u1 = _commit_file(repo, "u1.txt", "unstamped task commit")
    _write_documents(repo)
    digest = _digest(repo)

    result = _run(repo)

    assert result["backfilled"] == [u1]
    assert _rev(repo, "HEAD~2") == u0
    assert _message(repo, "HEAD~1").splitlines()[-1] == f"Plan-Hash: {digest}"
    assert f"Plan-Hash: {digest}" not in _message(repo, u0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/steps/test_docs_commit.py -v -k "merge_commit or root_commit or unresolvable_base or detached_head or origin_base"`
Expected FAIL:
- merge: `backfilled` non-empty, or a `GitError` from `hash-object`, because the merge is rebuilt with duplicate parents.
- root: `IndexError` at `rows[first][1]`.
- unresolvable base: `GitError` from `rev-list` (`^no-such-branch`).
- detached: `GitError` from `symbolic-ref -q HEAD`.
- origin: `backfilled == [u0, u1]`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/steps/docs_commit.py`, insert directly above `def _backfill(`:

```python
def _resolves(git_runner: GitRunner, worktree_path: str, revision: str) -> bool:
    """Whether `revision` names a commit in this repository."""
    try:
        git_runner(
            ["-C", worktree_path, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"]
        )
    except GitError:
        return False
    return True
```

Replace the start of `_backfill`, from its docstring's closing `"""` through the `commits = [...]` assignment, with:

```python
    """
    if not _resolves(git_runner, worktree_path, base_branch):
        # Today's behaviour; if review's range cannot resolve either, review
        # reports that, not this step.
        return []
    try:
        ref = git_runner(["-C", worktree_path, "symbolic-ref", "-q", "HEAD"]).strip()
    except GitError:
        # Detached HEAD: there is no branch to rewrite by name.
        return []
    old_tip = git_runner(
        ["-C", worktree_path, "rev-parse", "--verify", "HEAD^{commit}"]
    ).strip()
    # `origin/<base>` too: `worktree.ensure` may have branched from it, and a
    # local base behind its remote must not put upstream commits in range.
    excluded = [f"^{base_branch}"]
    if _resolves(git_runner, worktree_path, f"origin/{base_branch}"):
        excluded.append(f"^origin/{base_branch}")
    listing = git_runner(
        [
            "-C",
            worktree_path,
            "rev-list",
            "--reverse",
            "--topo-order",
            "--parents",
            old_tip,
            *excluded,
            "--",
        ]
    )
    rows = [line.split() for line in listing.splitlines() if line.strip()]
    if any(len(row) != 2 for row in rows):
        # A merge (several parents) or a root (none): out of scope, and
        # review_gate's "only N of M commits" message already covers it.
        return []
    commits = [
        (row[0], git_runner(["-C", worktree_path, "cat-file", "commit", row[0]]))
        for row in rows
    ]
```

After the edit the complete `_backfill` reads:

```python
def _backfill(
    git_runner: GitRunner, worktree_path: str, base_branch: str, digest: str
) -> list[str]:
    """Stamp every unstamped commit in `base_branch..HEAD` with `digest`.

    Returns the pre-rewrite shas of the commits that gained the trailer,
    oldest first. Commits before the first unstamped one keep their shas;
    from there on each is recreated on its recreated parent. The branch moves
    once, as a compare-and-swap against the tip read here.
    """
    if not _resolves(git_runner, worktree_path, base_branch):
        # Today's behaviour; if review's range cannot resolve either, review
        # reports that, not this step.
        return []
    try:
        ref = git_runner(["-C", worktree_path, "symbolic-ref", "-q", "HEAD"]).strip()
    except GitError:
        # Detached HEAD: there is no branch to rewrite by name.
        return []
    old_tip = git_runner(
        ["-C", worktree_path, "rev-parse", "--verify", "HEAD^{commit}"]
    ).strip()
    # `origin/<base>` too: `worktree.ensure` may have branched from it, and a
    # local base behind its remote must not put upstream commits in range.
    excluded = [f"^{base_branch}"]
    if _resolves(git_runner, worktree_path, f"origin/{base_branch}"):
        excluded.append(f"^origin/{base_branch}")
    listing = git_runner(
        [
            "-C",
            worktree_path,
            "rev-list",
            "--reverse",
            "--topo-order",
            "--parents",
            old_tip,
            *excluded,
            "--",
        ]
    )
    rows = [line.split() for line in listing.splitlines() if line.strip()]
    if any(len(row) != 2 for row in rows):
        # A merge (several parents) or a root (none): out of scope, and
        # review_gate's "only N of M commits" message already covers it.
        return []
    commits = [
        (row[0], git_runner(["-C", worktree_path, "cat-file", "commit", row[0]]))
        for row in rows
    ]
    first = next(
        (
            index
            for index, (_, raw) in enumerate(commits)
            if not _is_stamped(_split_commit(raw)[1])
        ),
        None,
    )
    if first is None:
        return []

    parent = rows[first][1]
    backfilled: list[str] = []
    # Outside the worktree: an extra file there would fail review's porcelain
    # check. `GitRunner` has no stdin, so `hash-object` reads a file.
    with tempfile.TemporaryDirectory(prefix="agent-manager-docs-commit-") as scratch:
        object_file = Path(scratch) / "commit"
        for sha, raw in commits[first:]:
            headers, message = _split_commit(raw)
            if not _is_stamped(message):
                message = with_trailer(message, digest)
                backfilled.append(sha)
            object_file.write_bytes(_rebuild_commit(headers, parent, message).encode("utf-8"))
            parent = git_runner(
                ["-C", worktree_path, "hash-object", "-t", "commit", "-w", str(object_file)]
            ).strip()

    git_runner(
        [
            "-C",
            worktree_path,
            "update-ref",
            "-m",
            f"docs_commit: backfill Plan-Hash {digest}",
            ref,
            parent,
            old_tip,
        ]
    )
    return backfilled
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: all PASS. That includes `test_documents_committed_without_a_trailer_raise_instead_of_lying`: its untagged commit is on `main`, so it is out of range and the step still raises.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py
git commit -m "feat: docs_commit backfill skips merges, roots, detached HEAD, unknown base and origin commits (70b703d9)"
```

---

### Task 5: Signed commits are rewritten unsigned

**Files:**
- Modify: `src/agent_manager/steps/docs_commit.py` (`_rebuild_commit` from Task 3; new constant above it)
- Test: `tests/steps/test_docs_commit.py` (append at the end)

**Interfaces:**
- Consumes: `_rebuild_commit(headers: list[str], parent: str, message: str) -> str` (Task 3); test helpers `_task_branch`, `_commit_file`, `_rev`, `_range`, `_message`, `_digest`, `_write_documents`, `_run`.
- Produces: `_SIGNATURE_HEADERS = ("gpgsig", "gpgsig-sha256")`; `_rebuild_commit` drops those headers and their continuation lines.

- [ ] **Step 1: Write the failing test**

Append to `tests/steps/test_docs_commit.py`:

```python
def test_a_signed_commit_is_rewritten_unsigned(repo: Path, tmp_path: Path) -> None:
    """A rewritten commit's old signature would no longer verify, so it is not
    carried over -- the header line and its continuation lines both go, as
    after a rebase. The signed object is crafted by hand: no gpg needed."""
    _task_branch(repo)
    base = _commit_file(repo, "before.txt", "unstamped before the signed one")
    tree = _rev(repo, "HEAD^{tree}")
    raw = (
        f"tree {tree}\n"
        f"parent {base}\n"
        "author Signer <signer@example.com> 1000000000 +0200\n"
        "committer Signer <signer@example.com> 1000000000 +0200\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " \n"
        " not-a-real-signature\n"
        " -----END PGP SIGNATURE-----\n"
        "\n"
        "signed draft\n"
    )
    object_file = tmp_path / "signed-commit"
    object_file.write_text(raw, encoding="utf-8")
    signed = _git(repo, "hash-object", "-t", "commit", "-w", str(object_file)).strip()
    _git(repo, "update-ref", "refs/heads/task", signed)
    _write_documents(repo)
    digest = _digest(repo)

    result = _run(repo)

    assert result["backfilled"] == [base, signed]
    _before_new, signed_new, _docs = _range(repo)
    rewritten = _git(repo, "cat-file", "commit", signed_new)
    assert "gpgsig" not in rewritten
    assert "not-a-real-signature" not in rewritten
    assert "author Signer <signer@example.com> 1000000000 +0200\n" in rewritten
    assert _message(repo, signed_new).splitlines()[-1] == f"Plan-Hash: {digest}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/steps/test_docs_commit.py -v -k signed_commit`
Expected: FAIL on `assert "gpgsig" not in rewritten`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/steps/docs_commit.py`, replace `_rebuild_commit` with:

```python
_SIGNATURE_HEADERS = ("gpgsig", "gpgsig-sha256")
"""Commit headers holding a signature, which a rewrite would invalidate."""


def _rebuild_commit(headers: list[str], parent: str, message: str) -> str:
    """The raw text of a commit object: `headers` with the parent replaced.

    Every other header line -- `tree`, `author`, `committer` -- is carried
    over verbatim, which is how name, email, timestamp and timezone survive.
    A signature header and its continuation lines (those starting with a
    space) are dropped: the rewritten commit is unsigned, as after a rebase.
    """
    kept: list[str] = []
    dropping = False
    for line in headers:
        if line.startswith(" "):
            if not dropping:
                kept.append(line)
            continue
        key = line.split(" ", 1)[0]
        dropping = key in _SIGNATURE_HEADERS
        if dropping:
            continue
        kept.append(f"parent {parent}" if key == "parent" else line)
    return "\n".join(kept) + "\n\n" + message
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/steps/test_docs_commit.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full verification**

Run: `uv run pytest`
Expected: all PASS (default `unit` + `git` tiers).

Run: `uv run pytest -m e2e_fake`
Expected: all PASS, including `tests/e2e/test_production_wiring.py::test_the_engine_authored_the_docs_commit_before_the_coder_ran`. Under the fake `claude` nothing commits before `docs_commit`, so the backfill is a no-op there.

Run: `git status --porcelain`
Expected: no untracked files besides what this plan commits.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/docs_commit.py tests/steps/test_docs_commit.py
git commit -m "feat: docs_commit backfill drops signature headers from rewritten commits (70b703d9)"
```
<!-- task-pipeline: validated -->
