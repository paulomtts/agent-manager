# Subtask a74f2cd6: Run Integrate at the end of `run_milestone` and show it in the dry run

Parent story 006d0a0e "Integrate in the runner". This subtask narrows decisions I1, I5 and I6 of `docs/superpowers/specs/2026-09-25-integrate-design.md` (section 3) and covers acceptance items 5 and 6 (section 4). It builds on sibling 6fea51ad (done), which delivered `src/agent_manager/integration.py` (`integrate_milestone`, `integration_branch`, `merge_order`, `IntegrateSuccess`, `IntegrateEscalation`), `steps/integrate.py`, `integrate.yaml` and the resolver role. Before coding, confirm that the working base contains those files. They exist in this worktree.

## Scope

In scope:
- `src/agent_manager/orchestrate.py`: `run_milestone` calls `integration.integrate_milestone` and uses the result to decide the run status and the payload.
- `src/agent_manager/cli.py`: `dry_run_payload` and `dry_run_milestone` add an `integrate` plan. Existing tests that assert full payloads are updated.
- Tests in `tests/test_orchestrate.py` and `tests/test_cli.py`. Any e2e test that asserts the whole milestone payload (`tests/e2e/test_milestone_run.py`, `test_parallel_milestone.py`, `test_production_wiring.py`) is updated only for the new keys.

Out of scope:
- The internals of `integrate_milestone`, `merge_tip`, the resolver role, `integrate.yaml` and the gates.
- `tests/e2e/fake_claude.py` and its `resolve` phase, and the five Integrate e2e scenarios. Those belong to sibling a37460b9.
- `--no-integrate`, a milestone-aware `am resume`, watch/retry/cancel, cost capture, per-story readiness and slow-test marking.

## Observable behaviour

1. **Where it runs.** `run_milestone` walks every level. If no lane escalated or stopped, it then calls `integrate_milestone(plan.stories, root, base_branch, branch_prefix, commands, allow_no_verification, store, run_id, runner_factory)`. Only after that call does it record the run's final status. The escalation early-return inside the level loop does not change: Integrate never runs after a lane escalation.
2. **Runner factory.** `run_milestone` accepts `runner_factory=None`, but `integrate_milestone` requires one. Resolve `None` to `cli.default_runner_factory`, the same way `cli.py:703` does. Tests inject a fake factory.
3. **Success.** Record the run as `done`. The payload keeps `done, run_id, levels, completed, tips, warnings` and adds `integrated: {branch, worktree, merged, resolved}`. `worktree` is a `str`. `merged` and `resolved` are lists of story ids.
4. **Integrate escalation.** Record the run as `escalated`. Return `{escalated: true, phase: "integrate", story, files, detail, run_id, warnings}`. The integration branch and worktree stay exactly as `integrate_milestone` left them (I5). No cleanup happens.
5. **Nothing left to run.** When every story is already `done` (the levels are empty or every lane is a no-op), Integrate still runs, and no early return may skip it. Two consequences:
   - A relaunch after an Integrate escalation retries Integrate.
   - A relaunch of a finished, integrated milestone reports `done` and leaves the integration branch tip unchanged.
6. **Base branch.** Integrate never moves the base branch tip and never pushes. Tests assert that the base tip is unchanged.
7. **Exit code.** `am run --milestone` already exits non-zero when `payload.get("escalated") is True` (around `cli.py:1119-1127`). The Integrate escalation payload carries `escalated: true`, so it exits non-zero with no change to the check. A test must assert this.
8. **Dry run.** `dry_run_payload` gains an `integrate` key with this shape: `{branch, worktree, order: [{story, tip}]}`.
   - `branch` is `integration.integration_branch(branch_prefix)`.
   - `worktree` is `str(cli.worktree_for(repo_dir, branch))`.
   - `order` comes from `integration.merge_order(stories, branch_prefix, base_branch)`. That order is the `compute_integrate_levels` order over all stories, in census order within each level. Stories with no subtasks are skipped.

   `dry_run_payload` stays pure. It gains a required keyword-only `repo_dir: Path` parameter so it can derive the worktree path (every existing `cli.dry_run_payload(...)` call in `tests/test_cli.py` is updated to pass it), and `dry_run_milestone` passes `root` for it. The dry run still writes nothing: no Store or run directory, no fetch, no worktree, no branch and no board write.
9. **Circular import.** `integration` imports `cli` at module load. So `cli` imports `integration` inside the function, as it already does for `orchestrate` (see `cli.py:1088-1094`). `orchestrate` can import `integration` directly, as long as no cycle is introduced. Check that before choosing.

## Error paths

- `integrate_milestone` returns an `IntegrateEscalation`: covered by item 4 above.
- `integrate_milestone` raises. Examples are a non-conflict git failure, or `MergeInProgressError` when the stop is not already turned into an escalation. The exception propagates, `store.close()` still runs in the existing `finally`, and the run is not recorded `done`. Do not catch or reclassify beyond what `integrate_milestone` already returns.
- A lane escalates: the existing behaviour is unchanged, and `integrate_milestone` is never called. A test asserts that it was never called.

## Tests

Tier placement follows design section 14 of `2026-09-23-agent-manager-design.md` as applied in the `tests/test_orchestrate.py` docstring:
- Pure helpers get unit tests on hand-built plans.
- `run_milestone` and CLI tests run at the Steps tier: a real temporary git repo, a real temporary brd board, `XDG_DATA_HOME` under `tmp_path`, and the harness replaced at the injected `driver` and `runner_factory` seams.
- Production wiring under a fake `claude` is the e2e tier, which belongs to sibling a37460b9.

| # | Test | File | Tier |
|---|------|------|------|
| 1 | Success payload shape. A clean two-story milestone returns the existing keys plus `integrated{branch, worktree(str), merged, resolved=[]}`. The run is recorded `done`. The integration branch contains both tips. The base tip is unchanged. | `tests/test_orchestrate.py` | Steps (real repo + board, fake driver) |
| 2 | Integrate escalation payload. Final verification fails (for example, the commands include a failing one). The payload is `{escalated: true, phase: "integrate", story, files, detail, run_id, warnings}`. The run is recorded `escalated`. The integration branch and worktree still exist and are untouched. The base tip is unchanged. | `tests/test_orchestrate.py` | Steps |
| 3 | A lane escalation skips Integrate. There is no integration branch, and the escalation payload is unchanged. | `tests/test_orchestrate.py` | Steps |
| 4 | An all-done milestone still integrates. Every story is already done on the board. `run_milestone` runs no lanes, creates `<prefix>-integrate` and returns `integrated`. | `tests/test_orchestrate.py` | Steps |
| 5 | A relaunch after an Integrate escalation retries. The first run escalates at Integrate. The cause is fixed (commands made passing). A relaunch reports `done` with `integrated`. | `tests/test_orchestrate.py` | Steps |
| 6 | Relaunching a finished, integrated milestone is a no-op. The second run returns `done`, and the integration branch tip is identical to the tip after the first run. | `tests/test_orchestrate.py` | Steps |
| 7 | `dry_run_payload` over a hand-built census includes `integrate{branch, worktree, order}`. The order follows integrate levels, keeps census order within each level, and skips a story with no subtasks. Also update the existing full-payload assertions. | `tests/test_cli.py` (the existing `dry_run_payload` unit tests live there) | Pure unit |
| 8 | `dry_run_milestone` or `am run --milestone --dry-run` on a real repo and board. The output has the `integrate` plan, and nothing was written: no run directory under `XDG_DATA_HOME`, no `<prefix>-integrate` branch, no worktree directory and no board change. | `tests/test_cli.py` | Steps |
| 9 | `am run --milestone` exits non-zero on an Integrate escalation. Patch `orchestrate.run_milestone` to return the Integrate escalation payload, or drive it for real as in test 2. The JSON envelope carries the payload. | `tests/test_cli.py` | Steps |
| 10 | Existing full-payload assertions in `tests/test_orchestrate.py`, `tests/test_cli.py` and the milestone e2e tests are updated to expect `integrated` (and the dry-run `integrate` key). The assertions are not loosened. | as existing | the existing test's tier |

The whole default suite (`uv run pytest`, including `tests/e2e`) stays green.
