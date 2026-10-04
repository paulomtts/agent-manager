# Resume re-ensures the worktree — design

Date: 2026-10-03
Status: approved design, pre-implementation

## 1. Purpose

A resume never re-ensures the worktree. `run_subtask_async` with `resume_from`
rebuilds the agent at the pending phase (`Agent.from_dict`,
`runtime/engine.py:190`) and adds no seed and no first turn
(`engine.py:198-200` are skipped), so phase 0 of `TASK` — `Step("worktree",
worktree.ensure)`, `workflow/task.py:44` — never runs again. Every later phase
then trusts that `subtask.worktree_path` is on disk: the launcher refuses a
`cwd` that is not a directory (`harness/launcher.py:135-140`) and `verify`
refuses a worktree that is not one (`steps/verify.py:338-342`).

Run `20261003T151207Z-5930c39d` (the dogfood milestone, `am` driving itself)
escalated twice on exactly this, both recorded in its journal:

```
NotADirectoryError: launcher cwd is not a directory: …/.claude/worktrees/m18/task-spec-and-plan-bundles-c06c167b -- the worktree step runs before any dispatch, so this is a bug above the launcher, not a harness failure
ValueError: verify.run_suite needs an existing absolute worktree directory, got PosixPath('…/.claude/worktrees/m18/task-verify-s-detail-always-bc0b99e0')
```

An earlier attempt's branches and worktrees had been torn down by hand (git
level, not `am cancel`), so the store still held open `turn` checkpoints for
those cards. `runs.continuable_checkpoint` (`runs.py:216-231`) correctly
judged them continuable — nothing in a checkpoint says whether its worktree
still exists — and the relaunch adopted them. The first adopted turn was
`review` on one card and `verify` on the other, and both crashed on the
missing directory instead of noticing it and recovering.

The launcher's own error message already names the invariant: "the worktree
step runs before any dispatch". On a resume that is false today. This spec
makes it true again.

## 2. Scope

**In scope.** One check in the engine's resume branch, before
`Agent.from_dict`, that re-ensures the checkpoint's worktree and either
continues the checkpoint (worktree re-added, or already intact) or declines
it with a warning and walks the subtask from its first phase. One gap in
`steps/worktree.ensure` that the check depends on (a worktree directory
deleted by hand is still *registered* with git, and `ensure` today reads a
registered path as existing). The summary carrying the phase a walk actually
continued at, so the two callers that report it stop computing it from the
checkpoint they handed in. The README paragraph that documents it.

**Out of scope.** Why a human deletes a worktree by hand, and any command
that would do it for them: an `am reset`/cleanup command for stale branches,
worktrees and checkpoints after a stale-base problem is a separate,
already-identified fix for a related but distinct problem, and this spec only
makes a resume survive the state that fix would prevent. Also out of scope:
`runs.continuable_checkpoint` and `orchestrate.resume_checkpoints`
(`orchestrate.py:707-720`) — which checkpoint is handed to the engine is
decided upstream, exactly as today; this fix is downstream of that decision,
not a replacement for it. `cli.checkpoint_resume_phase` (`cli.py:505-545`) is
untouched for the same reason.

**Never** (unchanged). No `git worktree prune`, no `worktree remove`, no
`reset`, `checkout -f`, `clean`, `commit` or `push` from the worktree step
(`steps/worktree.py:9-11`, asserted by
`tests/steps/test_worktree.py:534-545`). A branch is never re-cut from base
when it still exists (`worktree.py:265-268`): a hand-deleted worktree's
commits live on its branch and must survive.

## 3. Decisions

### 3.1 What the resume path looks like today, and where the check goes

Every resume reaches one function. `am resume` of a `task` run calls
`drive_subtask_async(..., resume_from=checkpoint)` (`cli.py:1923-1935`); a
milestone lane, on relaunch or resume, calls its driver with
`extra["resume_from"]` (`orchestrate.py:1283-1302`), and the production
driver is the same `drive_subtask_async` (`cli.py:649-651`); a merged-base
resolver calls `run_subtask_async(..., resume_from=resume_from)` directly
(`bases.py:194-209`). All three end in `runtime.engine.run_subtask_async`,
whose `else` branch (`engine.py:177-196`) is the only place a checkpoint is
turned into a runnable agent.

**The check lives there, and only there**: in `run_subtask_async`, inside the
`resume_from is not None` branch, after the digest refusal
(`engine.py:178-184`) and before `_forget` and `Agent.from_dict`
(`engine.py:187-190`). It is the one call site the finding asked for; the
callers are not touched. The digest check stays first because a mismatched
checkpoint must be refused "before anything runs or is recorded"
(`engine.py:145-147`), and re-adding a worktree is a git write.

Concretely, the branch becomes:

1. digest check (unchanged; raises `CheckpointMismatch`);
2. `resume_from, warnings = await _reensure_worktree(resume_from, subtask,
   repo_dir, ensure_worktree)` — the new step, run on a thread because
   `worktree.ensure` is synchronous git (as `bases.build` already does at
   `bases.py:293`);
3. if `resume_from` is still a checkpoint: `_forget`, `Agent.from_dict`,
   `agent.resume()`, the adoption floor — all unchanged;
4. if it was declined (`None`): the fresh-walk branch, exactly the code at
   `engine.py:169-176` and `198-200` — a new `Agent`, the seed item from
   `binding`, `compiled.first_turn()`, `adopt=None`.

The `warnings` list is passed into `RunDeps(warnings=...)`
(`runtime/state.py:45`), so `_collect` (`engine.py:281-288`) carries it to
`summary.warnings` and from there, unchanged, to `SubtaskDrive.warnings`
(`cli.py:655`), to `recorder.add_warnings` (`orchestrate.py:1303`) and to the
report's `warnings`. Nothing new is plumbed: the existing out-of-band warning
channel is the one `AgentRunner.adopt` already uses (`dispatch.py:679-682`).

`run_subtask_async` and `run_subtask` gain one keyword,
`ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure`.
It is the injection seam, in the same position as `agent_runner` and `clock`:
the unit tier may not spawn `git` (CLAUDE.md, "Test tiers"), so a unit test
of the resume path hands in a fake. No caller in `src/` passes it.

### 3.2 The three cases, and what decides between them

With `W = subtask.worktree_path`, `B = subtask.branch`:

| State on disk | Decision | Walk continues at |
|---|---|---|
| `W` is a directory | nothing to do; no git is run | the checkpoint's pending phase, as today |
| `W` missing, `B` exists | re-add `W` for `B` (3.3); one warning | the checkpoint's pending phase |
| `W` missing, `B` gone | decline the checkpoint; one warning | phase 0 (`worktree`), as a fresh walk |
| `W` missing, re-add raised | decline the checkpoint; one warning | phase 0, whose own `ensure` reports the real error |
| `W` is `None` | nothing to do | the checkpoint's pending phase |

The first row is the fast path and the only one existing resumes take: a
single `Path.is_dir()`, no subprocess, no lock. This is what keeps a normal
resume unaffected (4, scenario 1) and keeps the unit tier pure for every
resume test whose worktree exists.

The remaining rows are decided by **one** call, `ensure_worktree(B,
subtask.base_branch, W, repo_dir)`, and by reading its result
(`worktree.py:284-291`), not by a separate branch probe:

- `branch_existed=True` → `ensure` checked the surviving branch out again
  (`worktree.py:265-269`, never re-cut); the checkpoint is kept.
- `branch_existed=False` → `ensure` has just cut `B` fresh from base
  (`worktree.py:270-280`). A checkpoint pending at `review` or `verify` is
  meaningless on a branch with no commits, so it is declined. The fresh
  walk's phase 0 then finds the worktree `ensure` just made and reports
  `worktree_existed=True, created=False` — idempotence does the rest.
- `GitError` (or any `Exception` from the seam) → declined. The fresh walk's
  phase 0 runs `ensure` again under the normal step machinery
  (`walk.run_one_step`, `walk.py:483-544`), which records the phase row and,
  if it fails again, escalates the subtask at `worktree` with
  `GitError: …` as `detail` through `compile.step_phase`
  (`compile.py:214-216`). The engine does not escalate from before the agent
  exists: it would have to invent a phase row with no turn behind it, and the
  walk already knows how to fail at phase 0. A failure that was transient
  (a `LockTimeoutError` from `git_lock`, `worktree.py:198-209`) gets exactly
  one retry this way; a persistent one (the base branch is gone too, so
  `git worktree add … -b B <base>` fails) surfaces as the fresh walk's
  escalation with git's own message, which is what an operator needs to see.

This reads `branch_existed` rather than probing the branch first because the
probe is already inside `ensure` (`worktree.py:231-242`), and because a
branch that exists but whose base is gone is not a case the engine needs to
distinguish: `ensure` checks an existing branch out without touching the
base (`_resolve_base` only matters on the `-b` path, `worktree.py:155-170`,
`279`).

The decline is the engine forgetting it was handed a checkpoint. It is not
recorded as a refusal (nothing like `CheckpointMismatch` is raised: the
callers have already written their `started` rows by the time the engine
runs, `cli.py:1916-1918`, `orchestrate.py:1277`), and the old checkpoint row
is not deleted or rewritten: the fresh walk's `BEFORE_TURN` hook saves a new
`turn` row for `worktree` at a higher `seq` (`runtime/checkpoint.py:98-101`),
which from then on is the card's newest open checkpoint for both
`Store.latest_checkpoint` and `latest_open_checkpoint`
(`store.py:1527-1536`, `1555-1588`). The attempt numbering in the run
directory continues from where it was (`paths.highest_attempt`), so a
re-dispatched phase lands in a fresh `<phase>.N`, as any resume does.

### 3.3 The gap in `ensure`: a hand-deleted worktree is still registered

The finding says "just call `worktree.ensure`, which is already idempotent".
It is idempotent, but it is not sufficient on its own, and this is the one
piece of new logic the fix needs.

`ensure` decides whether the worktree exists from `git worktree list
--porcelain` (`worktree.py:243-246`, `_is_registered` at `125-137`). After
`rm -rf <worktree>` — the dogfood cleanup, and the usual way a worktree
disappears by hand — git still lists the path, with a `prunable` line, and a
plain `git worktree add <path> <branch>` refuses:

```
fatal: '<path>' is a missing but already registered worktree;
use 'add -f' to override, or 'prune' or 'remove' to clear
```

(Confirmed against git 2.55.0.) So today `ensure` returns
`worktree_existed=True, created=False` for a directory that is not there,
and a fresh relaunch of the same card dies at `explore` with the same
`NotADirectoryError` the resume did. `git worktree prune` is the forbidden
global sweep (`worktree.py:21-22`): it would also remove another lane's
not-yet-populated worktree.

`ensure` therefore gains one rule: **a registered path that is not a
directory counts as not existing, and its add carries `-f`.** Precisely:

- `worktree_existed = _is_registered(path, registered) and
  Path(path).is_dir()`; the same re-check under `git_lock`
  (`worktree.py:259-263`).
- When the stale registration was seen, the argv is
  `["-C", repo, "worktree", "add", "-f", path, branch]` for an existing
  branch and `["-C", repo, "worktree", "add", "-f", path, "-b", branch,
  resolved_base]` for a new one. `-f` is git's documented override for
  exactly this case ("if `<path>` is already assigned to some worktree but is
  missing"); both forms are confirmed to re-create the directory, and the
  existing-branch form checks out the branch's own commits, not base. A
  single `-f` suffices; `-ff` (a locked worktree) is not used.
- `-f` is passed **only** when the stale registration was seen. A clean add
  stays as it is, so git's refusal of the same branch checked out at another
  live path still surfaces
  (`test_a_same_branch_at_a_different_path_surfaces_gits_error_and_frees_the_lock`).
- The result dict keeps its five keys; nothing reads a new one.

`-f` on `worktree add` is not on the forbidden list, and the list's reason —
never discard a killed run's commits — holds: `-f` overrides a bookkeeping
refusal about a path, never a branch's content.

This change benefits the fresh-run path as much as the resume path, which
is why it lives in `ensure` and not in the engine: after the fix, a relaunch
of a card whose worktree was `rm -rf`'d works whether or not a checkpoint is
adopted.

### 3.4 The warnings

Three new lines, shaped like the two adoption warnings at
`dispatch.py:679-682` and `698-701` and the `verification: kept from
checkpoint: […]` line at `cli.py:1900-1904`: one sentence, the checkpoint
named by `#<seq>` and run id as `checkpoint_resume_phase` names it
(`cli.py:534`), the reason in parentheses, and what happens next after a
semicolon.

Re-added (checkpoint kept):

```
checkpoint #<seq> of run <run-id>: worktree <path> was missing and was added again for branch '<branch>'; resuming at '<phase>'
```

Declined because the branch is gone:

```
checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and branch '<branch>' no longer exists); starting from the first phase
```

Declined because the re-add failed:

```
checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and could not be added again: GitError: <message>); starting from the first phase
```

`<phase>` is `runtime_engine.pending_phase(checkpoint)`, `<path>` is
`subtask.worktree_path`, and the error is rendered by `walk._render_error`
(`walk.py:547-548`), the one spelling every other `detail` uses. Exactly one
line per resume: the fast path adds none.

### 3.5 The summary says where the walk actually continued

Both callers report the resume point from the checkpoint they handed in,
computed before the walk: `am resume`'s `resumed_from`
(`cli.py:1893-1895`, `1965`) and the lane's `(resumed at <phase>)` line on
the `done` comment (`orchestrate.py:1355-1359`, via `compose_done`). After a
decline both would be wrong — the walk started at `worktree`, not at
`review`.

`SubtaskSummary` (`walk.py:241-257`) gains `resumed_at: str | None = None`:
the pending phase of the checkpoint the walk continued from, `None` on a
fresh walk and on a declined one. The engine sets it in the same place it
decides (3.1, step 3 or 4). `_resume_from_checkpoint` reports
`"resumed_from": summary.resumed_at`, and the lane passes
`resumed_at=summary.resumed_at` to `compose_done`. `checkpoint_resume_phase`
keeps its job — refusing before the first write — it just no longer supplies
the reported value. The field is on the summary, not on `SubtaskDrive`,
because the merged-base resolver has no `SubtaskDrive` and `bases` reads
the summary directly.

This is the only edit outside `engine.py`, `worktree.py` and the docs, and
it is two call-site substitutions. It is not optional: a report that names a
phase the walk did not start at is the §12 failure the warnings list exists
to prevent.

### 3.6 What this does to the three callers

- `am resume` of a `task` run (`cli.py:1841-1979`): `kept_warning` is
  computed from the checkpoint's seed (`runtime_engine.kept_commands`). On a
  decline the fresh walk is seeded from `binding` (`engine.py:151-163`),
  i.e. from the `commands` passed now, so the kept suite no longer applies.
  `kept_warning` is computed before the walk and cannot know about the
  decline, so it stays as it is, and the README states that after a decline
  the suite is the one passed, not the kept one (5, first risk).
- A milestone lane: `recorder.add_warnings(result.warnings)` already carries
  every summary warning; `resumed_at` comes from the summary (3.5). The
  lenient relaunch lookup (`runs.continuable_checkpoint`) and the strict
  resume lookup (`plan.checkpoints`) are untouched.
- A merged-base resolver (`bases.build`, `bases.py:293-330`): `ensure` runs
  before the resolver is resumed, so with 3.3 the engine's check always
  takes the fast path there. The "branch gone" decline cannot trigger for a
  resolver through the engine, because `bases.build`'s own `ensure` re-cuts
  the merged-base branch first; see 5 for the boundary.

### 3.7 Constraints this design respects

- **One call site.** The check is in the engine's resume branch, where
  `resume_from` becomes an agent, and nowhere else; `cli.py`,
  `orchestrate.py` and `bases.py` do not learn about worktrees.
- **Idempotent, at-least-once steps** (README, "Resuming: what runs again":
  "Steps are at-least-once, and every step must be idempotent"). Re-running
  `ensure` before a resume is that rule applied to phase 0, which the resume
  path had been skipping.
- **No global git sweep.** `prune` stays forbidden; the only new git
  behaviour is `-f` on one targeted `worktree add`, under `git_lock` as
  before.
- **Refusals before writes, recoveries after.** `CheckpointMismatch` stays a
  refusal raised before anything runs; a missing worktree is not a refusal,
  because by the time the engine sees it the callers have recorded the run
  `started`, and leaving that row behind with nothing driving it is the
  state resume exists to fix.
- **The unit tier spawns nothing.** The fast path runs no subprocess; every
  other path runs through the injected `ensure_worktree`.

## 4. Testing

Unit (`tests/runtime/test_resume.py`, driven through `run_subtask` with the
`_five` step workflow and a recording fake `ensure_worktree`; the subtask's
`worktree_path` becomes a directory under `tmp_path` instead of the
non-existent `Path("/w")` at `test_resume.py:63`, so the fast path applies
unless a test removes it):

1. **Intact worktree, unaffected.** Crash in `c`, resume with the directory
   present: the fake is never called, `ran` is `["a", "b", "c"]` + `["c",
   "d", "e"]` as today, `summary.warnings` is empty, `summary.resumed_at ==
   "c"`.
2. **Worktree deleted, branch survives.** Remove the directory; the fake
   returns `branch_existed=True, worktree_existed=False, created=True`. The
   fake was called once with `(branch, base_branch, worktree_path,
   repo_dir)`; the walk continues at `c` (`a` and `b` do not run again);
   one warning, the re-added line of 3.4; `resumed_at == "c"`; the carried
   adoption floor is unchanged (reuse the assertion shape of
   `test_a_carried_floor_survives_a_resume`).
3. **Worktree and branch both gone.** The fake returns
   `branch_existed=False, created=True`. The walk runs `a` through `e`
   (phase 0 included), one warning, the declined line; `resumed_at is
   None`; `deps.adopt` was `None` (no adoption fires); the card's
   `latest_checkpoint` after the walk is a row newer than the declined one,
   and `latest_open_checkpoint` no longer returns the declined row.
4. **Re-add raised.** The fake raises `GitError`; the walk starts fresh, with
   the "could not be added again" warning. With a workflow whose phase 0 is
   the same raising fake, the subtask ends `escalated` at that phase with
   `detail` starting `GitError:` and the phase row recorded `failed` — the
   normal step failure, not an engine exception.
5. **No worktree path.** `worktree_path=None`: the fake is never called and
   the resume is unchanged.
6. **Digest first.** A mismatched digest with a missing worktree raises
   `CheckpointMismatch` and never calls the fake.

Every other unit test that passes `resume_from` with a worktree path that
does not exist (`tests/runtime/test_exactly_once.py`,
`test_cancellation.py`, `test_stop_bridge.py`, and the `task`-run resume
tests in `tests/test_cli.py` built on `Path("/repo/…")`) must either create
the directory or inject a fake, or they will now exercise the decline path
and fail on their own assertions; `tests/test_orchestrate.py` drives a
`FakeDriver` and never reaches the engine, so it needs nothing.

Git tier (`tests/steps/test_worktree.py`, real git in `tmp_path`):

7. **`rm -rf`'d worktree, branch survives.** `ensure` once, commit on the
   branch, `shutil.rmtree` the worktree, `ensure` again: returns
   `branch_existed=True, worktree_existed=False, created=True`, the
   directory exists, `git -C <wt> log` shows the commit (never re-cut), the
   recorded argv contains `-f`, and `git worktree list --porcelain` has no
   `prunable` line afterwards.
8. **`rm -rf`'d worktree, branch deleted.** As 7, then `git update-ref -d
   refs/heads/<branch>` (`git branch -D` is refused by git while the stale
   registration stands — that refusal is itself worth a line in the test):
   `ensure` returns `branch_existed=False, created=True` and used `-f -b`.
9. **Cleanly removed worktree.** `git worktree remove` then `git branch -D`:
   the plain `-b` path, no `-f` in the argv (existing behaviour, pinned).
10. **`-f` never on a clean add.** Extend
    `test_no_forbidden_git_operation_runs_on_any_path` to assert `-f` is
    absent from every argv on the fresh, the existing-branch and the
    second-identical-call paths.

`e2e_fake` (`tests/e2e/`, one test for this scenario family):

11. Production wiring under the fake `claude`: `am run --card` killed after
    `implement`'s checkpoint, `rm -rf` of the worktree, `am resume` ends
    `done` with `data.resumed_from == "review"` and the re-added warning in
    `data.warnings`. The both-gone case stays in the unit tier: its
    observable (a fresh walk) is the same whether `claude` is fake or stub.

Docs: README "Resuming: what runs again" gains a paragraph with the table of
3.2 and the three warning lines; the `am resume` section's statement that a
walk "keeps the suite … the run started with" gains the decline exception
of 3.6.

## 5. Risks

- **A declined checkpoint re-dispatches every agent phase before the pending
  one.** That is the point — nothing on the fresh branch can be trusted —
  but on a card whose branch was deleted after `implement` it costs the
  explore/spec/plan/implement dispatches again. `plan_check`'s `skip_to`
  (`task.py:55-60`) still skips spec and plan when the card's validated plan
  is recorded, so the cost is bounded to what the board does not remember.
  After a decline the suite is the one passed now, not the checkpoint's
  kept one; a `task` resume's `verification: kept from checkpoint` warning
  can therefore appear beside the decline warning and is then stale. Accepted
  and documented; reordering `kept_warning` after the walk is not worth the
  restructuring.
- **A merged-base resolver's branch is re-cut without a decline.**
  `bases.build` ensures the merged-base worktree before resuming its
  resolver (`bases.py:293`) and never reads `branch_existed`, so a deleted
  merged-base branch is re-cut from `tips[0]` and the parked resolver is then
  resumed mid-merge on a branch with no merge in progress; the engine sees an
  intact directory and takes the fast path. This predates this spec and is
  at the `bases` boundary, not the engine's; the one-line fix (drop
  `resume_from` when `ensure` reports `branch_existed=False`) belongs with
  the cleanup work this spec scopes out.
- **`-f` masks a registration that is stale for a reason.** A worktree on an
  unmounted volume, or one whose `.git` file was damaged, is also "registered
  but not a directory", and `-f` will re-create it in place. On a mounted
  volume returning later that leaves two checkouts of one branch; git's own
  guard against that is the one `-f` overrides. The window is the resume of
  one card, and the alternative — refusing every hand-deleted worktree
  forever, since `prune` is forbidden — is the bug this spec fixes.
- **Existing unit tests with phantom worktree paths.** The fast path is what
  keeps them pure, and only when the path exists. Any resume test left with a
  non-existent path silently takes the decline path through the stub `git`
  (exit 99 → `GitError` → decline). The test plan names the files; the
  implementer greps `resume_from` under `tests/` and fixes every fixture, and
  the tier guard (`tests/test_tier_guards.py`) does not catch a decline that
  happens to pass.
