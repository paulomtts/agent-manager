# Wire `am run --milestone` and prove it under a fake claude (card 3e0ab2b9)

Parent story f8290fd6 ("Run a milestone: rollup, the shared driver, the runner"), milestone 3. Narrows the orchestration addendum (`docs/superpowers/specs/2026-09-24-orchestration-design.md`) decisions O4, O6 and O8 and acceptance items 2, 3 and 5 to one subtask. The siblings are already built on this branch: the shared driver `cli.drive_subtask` (38809280), the ancestor rollup in `rollup.set_status` (bf26f482), and `orchestrate.run_milestone` (c9037ac9). This card wires the CLI and proves the whole path end to end. It does not redesign any of them.

## Scope

1. `src/agent_manager/cli.py`, the `run` command (currently ~L961-1031).
   - `--milestone X` without `--dry-run` calls `orchestrate.run_milestone(X, repo_dir=..., base_branch=..., branch_prefix=..., commands=list(verify), allow_no_verification=...)`. It passes no `runner_factory` and no `driver`, so production gets `cli.default_runner_factory` and `cli.drive_subtask`.
   - It uses the same `--verify`, `--allow-no-verification`, `--base-branch` and `--branch-prefix` options as `--card`. It adds no new options.
   - `MilestoneRunNotImplementedError` and the branch that raises it are removed. The `--milestone` help text drops "Needs --dry-run for now".
   - `orchestrate` already imports `cli` at module level, so `cli` must reach `orchestrate` without a circular import at load time. A function-local import in the milestone branch is one way to do that. Planning picks the mechanism.
   - `_check_run_targets`, the `--dry-run` path and the whole `--card` path stay exactly as they are (story rule: `am run --card` behaves exactly as now).
2. `tests/e2e/`: a new fake-claude milestone module, plus extensions to `conftest.py` and `fake_claude.py`. Both files are extended, not copied.
3. `orchestrate.py` and `steps/rollup.py` change only for a bug the e2e exposes. Any such fix is named in the plan and gets its own regression test in the matching unit module.

## Observable behavior

- Clean milestone: prints `{"ok": true, "data": <run_milestone payload>}` (with `done: true`, `run_id`, `levels`, `completed`, `tips`, `warnings`) and exits 0. `--pretty` indents it.
- Escalated milestone: prints `ok: true` with the escalation payload (`escalated: true`, `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail`, `warnings`) and exits `EXIT_ESCALATED` (1). This mirrors `run --card` on `status == "escalated"`: an escalation is a truthful result, not an error.
- The exit-code check must key off the payload's `escalated` flag for milestones and `status` for cards. A milestone payload has no `status` key, so it must never be indexed for one. The clean payload also has no `escalated` key (it carries `done: true`), so read the flag with `payload.get("escalated")`, never `payload["escalated"]`.

## Error paths

- Any `HANDLED` exception from `run_milestone` gives an `ok: false` `error_envelope` and `EXIT_ERROR` (3). Examples: an unknown or ambiguous milestone, a two-blocker story or a cycle (`CliError`/`ValueError`), a `board.BoardError`, a `WorkflowLoadError`, an `EngineError`. These refusals come before any write, as `run_milestone` already guarantees.
- Exceptions outside `HANDLED` keep crashing loudly, as today.
- Usage errors (`--card` with `--milestone`, a blank `--milestone`, `--dry-run` with `--card`) are unchanged Typer exit 2s.

## Fake-claude constraints (O8, milestone-2 rule)

The fake must never know more than the brief tells it. It takes the hash from `## plan_hash`, never commits the spec or plan, and learns the result path only from the prompt text. It gets no env var, argv flag or other side channel.

- **Failing B's review.** A new `review` branch in `build_result` must produce a schema-valid result that a real `review` gate blocks, so `failed_phase == "review"` comes through the production gate path.
  - The trigger comes from the review brief, specifically its `## branch` section, which carries B's subtask branch.
  - Note: `review_gate` blocks on a dirty `porcelain`, zero commits, or `tagged_count < commit_count`, and `plan_hash_gate` blocks on a hash mismatch. Nothing reads `unresolved_blockers`, so setting that field alone fails nothing.
  - Every field set must pass `override()`'s drift check.
- **Fixing it for the relaunch.** The test must switch the failure off between runs through something the fake can legitimately see. The agreed shape is a test-controlled marker file that names the branch(es) whose review should fail. The fake finds the marker from its own cwd, and it must neither sit in the worktree's tracked or untracked tree nor dirty `git status`. The fake compares the marker to `## branch`, and the test removes or empties it before relaunching. Planning fixes the exact location.
- **Re-entering B.** On relaunch, B's subtask re-enters through `worktree.ensure`, `plan_check`'s skip and Plan-Hash re-entrancy. Its `implement` must still succeed under the fake (for example, the fake must not fail on "nothing to commit" when its implementation file is already committed), and the fake still must not compute the hash. Planning must confirm this path.
- Existing e2e tests (`test_production_wiring.py`, `test_fake_claude.py`, `test_real_harness.py`) must keep passing unchanged in meaning.

## Fixtures

- `conftest.py`'s repo+board setup is reused. The current `project` fixture is one module-scoped repo, so the milestone tests need their own fresh repo+board per scenario. The fixture is factored or parameterised to allow that, not duplicated.
- `_add_card` gains blocker support through `brd block <id> --by <blocker>`, as `tests/test_orchestrate.py::_block` uses.
- Board: one milestone and three stories.
  - A has two subtasks, chained by `brd block`.
  - B is blocked by A and has one subtask.
  - C is blocked by B and has one subtask.
- The repo is on `main` with no `origin`, so `refresh_git` skips the fetch. `VERIFY_COMMANDS` is reused.
- Both tests invoke the real command through `typer.testing.CliRunner` on `cli.app` (`run --milestone <id> --repo-dir <repo> --base-branch main --branch-prefix <p> --verify ...`) with `fake_claude_bin` first on PATH. They pass no runner_factory, so the real `ClaudeAdapter` and `launcher.run_direct` run.

## Tests

The tier comes from main spec §14 "Testing" and addendum O8: a fake-claude milestone proof through production wiring belongs in the unmarked default-suite e2e tier (`tests/e2e/`), and CLI wiring assertions may go in `tests/test_cli.py`.

**tests/e2e/ (default suite, unmarked), new module, e.g. `tests/e2e/test_milestone_run.py`:**

1. *Clean three-story milestone* (acceptance 2). Asserts:
   - Exit 0 and `ok: true`, `data.done` is true.
   - Every subtask card is `done` on the board.
   - Stories A, B and C and the milestone are `done` through rollup, not written directly by the test.
   - Each subtask branch contains its predecessor's commit (`git merge-base --is-ancestor`): a1 before a2, and in particular a2 (A's last) before b1 (B's first), and b1 before c1.
   - Every commit the run made on every subtask branch, above the base, carries a `Plan-Hash:` trailer.
   - `main`'s tip SHA is identical before and after the run.
2. *Escalation stops the run, and relaunching resumes* (acceptances 3 and 5).
   - With the review-fail marker naming B's subtask branch, the first invocation exits 1 with `ok: true`.
   - `data.escalated` is true, `data.story` is B, `data.subtask` is b1, `data.failed_phase` is `"review"`.
   - Story C never started:
     - No worktree exists at `cli.worktree_for(repo, <c1 branch>)`.
     - The run's store projection shows c1 with no started phases or attempts.
     - The fake's log for that run has no entry whose cwd is C's worktree.
   - The test removes the marker, and the same command is run again. It exits 0 with `data.done` true, and `data.completed` equals `[b1, c1]`. A's subtasks are not re-driven: the second run's fake log has no entry in A's worktrees.
   - All cards, stories and the milestone end `done`.

**tests/test_cli.py (CLI wiring, `orchestrate.run_milestone` monkeypatched):**

3. `--milestone` without `--dry-run` calls `run_milestone` once with the given milestone, `--repo-dir`, `--base-branch`, `--branch-prefix`, `--verify` list (order kept) and `--allow-no-verification`, and with no `runner_factory`. It exits 0 with `ok_envelope(payload)`.
4. A payload with `escalated: true` exits 1 with an `ok: true` envelope.
5. A `HANDLED` error raised by `run_milestone`, for example a `CliError` or `board.BoardError`, gives `ok: false` at exit 3.
6. `test_a_milestone_run_without_dry_run_is_a_not_implemented_envelope` (~L2586) and the `MilestoneRunNotImplementedError` subclass assertion (~L1191) are removed or replaced by tests 3-5. Every `--card` and `--dry-run` test stays green unchanged.

**tests/e2e/test_fake_claude.py (default suite):** the new review-failure branch gets a unit-style check next to the existing ones. With the marker naming the brief's branch, the review result is one a review gate blocks. Without it, the result is unchanged. This pins the trigger to what the brief and the marker say.

## Out of scope

- Parallel stories, Integrate, milestone-aware `am resume`, and `watch`/`retry`/`cancel`.
- Review counts measured by git, and verification discovery (addendum §4).
- The `--dry-run` preview (36faf21e) and the blocked-coder gate (dd321e61, O7).
- Changes to the runner's ordering, stacking, escalation or skip logic, or to the rollup walk.
- The opt-in real-`claude` milestone test (`pytest -m e2e`) is not part of this card's default-suite proof.

Verification: `uv run pytest` (whole default suite, including `tests/e2e`) green. There is no typecheck or lint step.
