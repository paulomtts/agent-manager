# Report the resume point from `SubtaskSummary.resumed_at`, and document the worktree re-ensure — subtask dd932306

Parent story ab65eee1 ("A resume survives a worktree deleted outside am's bookkeeping"). Milestone design: `docs/superpowers/specs/2026-10-03-resume-worktree-reensure-design.md` §3.4, §3.5, §3.6, §3.7, §4 (docs paragraph), §5 (first risk). Builds on siblings cf03b236 (`steps/worktree.py` `ensure` re-adds a stale-registered missing path) and f76af5b2 (the engine's resume branch, `_worktree_kept`, the `ensure_worktree` seam, and `SubtaskSummary.resumed_at`), both already present in this worktree.

## Scope

Two read-site substitutions, one test-fake adjustment, and README text. Nothing else.

1. `src/agent_manager/cli.py`, the `am resume` payload of a `task` run (line 1965): `"resumed_from": phase` becomes `"resumed_from": summary.resumed_at`. The `phase = checkpoint_resume_phase(...)` call at 1893-1895 stays: it still refuses a done / phase-escalated / digest-mismatched checkpoint before any write. Only its value stops being reported. If `phase` is then unused, drop the binding but keep the call.
2. `src/agent_manager/orchestrate.py`, the lane's `comments.compose_done(...)` (1316-1326): `resumed_at=None if checkpoint is None else runtime_engine.pending_phase(checkpoint)` becomes `resumed_at=summary.resumed_at`. Update the adjacent comment to say it is where the walk actually continued, `None` on a fresh or declined walk.
3. README.md (this worktree):
   - In "## Resuming: what runs again" (line 514 on), add a paragraph and the three-case table: worktree intact (nothing runs, no warning, continues at the checkpoint's pending phase); worktree missing, branch survives (worktree added again for the branch, one warning, continues at the pending phase); worktree missing and branch gone, or the re-add fails (checkpoint not resumed, one warning, the subtask is walked from its first phase, `worktree`, as a fresh walk, whose own step reports a real git failure as an ordinary escalation at `worktree`). Quote the three warning lines exactly as `engine._worktree_kept` produces them:
     - `checkpoint #<seq> of run <run-id>: worktree <path> was missing and was added again for branch '<branch>'; resuming at '<phase>'`
     - `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and branch '<branch>' no longer exists); starting from the first phase`
     - `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and could not be added again: GitError: <message>); starting from the first phase`
     State that after a decline `data.resumed_from` is `null` and a milestone lane's `done` comment carries no `(resumed at <phase>)` line, and that the declined checkpoint row is not deleted, only superseded by the fresh walk's newer checkpoint.
   - In the `am resume` prose (line 39), after "keeps the suite and the opt-out the run started with", add the decline exception: when the checkpoint is declined because its worktree could not be kept, the fresh walk uses the `--verify` / `--allow-no-verification` passed now, not the kept suite, and a `verification: kept from checkpoint: [...]` warning shown beside the decline warning is then stale (spec §5, first risk).
   - Docs are written from the landed code in this worktree (`runtime/engine.py` `run_subtask_async` / `_worktree_kept`, `steps/worktree.py` `ensure`), not paraphrased from the milestone spec.

Out of scope: `runtime/engine.py` (any worktree logic), `runtime/walk.py` (the field already exists; do not move it to `SubtaskDrive`, `bases.py` reads the summary), `steps/worktree.py`, `bases.py`, `cli.checkpoint_resume_phase`, `runs.continuable_checkpoint`, `orchestrate.resume_checkpoints`, `kept_warning` ordering, and every test sibling f76af5b2 already fixed (`tests/runtime/test_resume.py`, `test_exactly_once.py`, `test_cancellation.py`, `test_stop_bridge.py`, task-run resume fixtures). `cli.py` / `orchestrate.py` must not learn about worktrees (§3.7 "one call site"). README line 567 (`(resumed at <phase>)` follows "when a milestone run picked the subtask up from a checkpoint") stays as is.

## Observable behaviour

- Intact worktree (every existing resume): unchanged. `resumed_from` / `(resumed at <phase>)` equal the checkpoint's pending phase, because the engine sets `resumed_at = pending_phase(resume_from)` on the kept path.
- Worktree re-added: same pending phase reported; the re-added warning rides `data.warnings` / the lane's warnings through the existing plumbing.
- Declined: `data.resumed_from` is `null`; the lane's `done` comment has no `(resumed at ...)` line.
- Error paths: none new. Refusals (`CheckpointMismatch`, done / escalated checkpoints) still happen before any write via `checkpoint_resume_phase` and the engine's digest check; a missing worktree is a recovery, never a refusal (§3.7), which is why the reported value is read from the post-walk summary.

## Correction to the exploration findings

The findings claimed `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` (tests/test_orchestrate.py:6242) passes unchanged. It does not: it drives `CheckpointDriver` (test_orchestrate.py:3624), a `FakeDriver` that never reaches the engine and returns `SubtaskSummary(status="done")` with `resumed_at=None`, so after substitution 2 the `(resumed at review)` line disappears. `CheckpointDriver` must mirror the engine's kept path: when `resume_from` is a checkpoint (not `_ABSENT` / `None`), set the returned summary's `resumed_at` to `runtime_engine.pending_phase(resume_from)`. The findings also called the `tests/test_cli.py` resume tests git-tier; they are marked `@pytest.mark.brd` + `@pytest.mark.git`, i.e. the opt-in `brd` tier. The exploration summary was truncated at 8000 characters mid-sentence ("Any README-only doc change n..."), a sign the upstream stage over-ran its brief; nothing past that point was relied on.

## Tests

Tier per the CLAUDE.md placement rule (chosen by what the test spawns; the `project` / `cards` fixtures in test_cli.py and test_orchestrate.py run the real `brd` binary, hence `brd`).

1. `brd` — existing `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` (test_orchestrate.py) passes with the adjusted `CheckpointDriver`; the existing "no `(resumed at`" assertion at test_orchestrate.py:6067 still holds.
2. `brd` — new, test_orchestrate.py: a lane handed a checkpoint whose driver reports a declined walk (summary `resumed_at=None`, e.g. a `CheckpointDriver` option or subclass) posts a `done` comment with no `(resumed at` line. Proves the lane reads the summary, not the checkpoint.
3. `brd` — existing test_cli.py assertions `resumed_from == "plan"` / `"validate_spec"` (5319, 5724, 5746, 6921, 7134) pass unchanged through the real engine's fast path.
4. `brd` — new, test_cli.py: `am resume` of a `task` run crashed after a checkpoint, with the worktree removed (`shutil.rmtree`) and its branch deleted (`git update-ref -d refs/heads/<branch>`, since `git branch -D` is refused while the stale registration stands), returns `data.resumed_from is None` and the "no longer exists" decline line in `data.warnings`.
5. Unit — existing `tests/test_comments.py` `compose_done` tests are untouched (the comment formatter does not change).
6. README changes need no test.

Default `uv run pytest` must stay green; run `uv run pytest -m brd` for 1-4.
