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
