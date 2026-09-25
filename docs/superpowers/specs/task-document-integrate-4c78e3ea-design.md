# Document Integrate — subtask design (4c78e3ea)

Card: 4c78e3ea "Document Integrate". Parent story f96f2b53 "Prove it against a real harness, and document it", milestone db5b5a3b. Blocked by dca476c4 (done; it wrote only `tests/e2e/test_real_harness_integrate.py`). This card is docs only.

Governing designs: the Integrate addendum `docs/superpowers/specs/2026-09-25-integrate-design.md` (I1–I7, §4 Acceptance, §5 Limits, §6 Deferred), which extends the orchestration, parallel-stories and main (`2026-09-23-agent-manager-design.md`) specs.

Note: the exploration summary handed to this stage was truncated at 8000 characters, cut off mid-sentence at "Existing README defaults: --max-concurrent 4, --r". The missing text was not guessed. Every fact below was re-read from this worktree's source and README.

## Checkout precondition

Plain master (015d672) has no Integrate. This card's worktree, `.claude/worktrees/m5/task-document-integrate-4c78e3ea`, is stacked on the dca476c4 tip and has the whole implementation: `src/agent_manager/integration.py`, `steps/integrate.py`, the Integrate wiring in `orchestrate.py` and `cli.py`, the `resolver` bundle, and `workflow/builtin/integrate.yaml`. Its `README.md` and main spec are still as on master. If the stage finds `src/agent_manager/integration.py` missing, it must stop and report, not write docs.

## Scope

Files touched: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§12 only). No file under `src/` or `tests/` changes. Do not touch `tests/e2e`: the sibling owns it.

### 1. README.md: new `#### Integrate` subsection (under "Milestone runs", after "Parallel runs" or after "What a clean run leaves behind")

Cover, from the source in this worktree:

- **When it runs.** After the last level, and only when every level finished clean. It also runs when nothing was left to drive (orchestrate.py ~603-609), so it runs on every relaunch.
- **Where.** One local branch, `<prefix>-integrate` (`integration.integration_branch`), in the worktree `<repo>/.claude/worktrees/<prefix>-integrate`.
- **Order.** `integration.merge_order`: levels from `dag.compute_integrate_levels` over every story in the milestone, done or not, and census order within a level. A story with no subtasks is skipped because it has no branch of its own.
- **Clean merges dispatch no agent.** Each tip is merged with `git merge --no-ff`. This is idempotent, so a tip that is already merged is a no-op.
- **Conflicts.** The merge is left in progress and a resolver agent is dispatched: role `resolver`, workflow `builtin/integrate.yaml`, with an agent phase `resolve` and then a deterministic `verify`. The resolver runs as a synthetic story titled "Integrate" with one synthetic subtask per conflicting tip, so `am status <run_id>` shows it. Git judges the resolver, not its `resolved` flag. `merge_completed_gate` needs no `MERGE_HEAD`, a clean tree, and no conflict markers in the touched files. `resolve` retries up to 2 attempts on `gate_failed`/`schema_invalid`.
- **Final verification (I4).** After the last merge, the `--verify` commands run once on the integrated branch. `--allow-no-verification` skips this, and the verification gate applies as elsewhere. A textually clean merge that breaks the suite escalates here.
- **What the run reports.** Described in the "clean run" and "escalation report" edits below.
- **What the human does next.** Review `<prefix>-integrate`, then merge it into the base branch yourself (for example, from a checkout of the base branch, `git merge m3-integrate`). State plainly that `am` never merges into the base branch and never pushes (rule 4, I5).
- **After an Integrate escalation.** Open the integration worktree, finish or fix the merge, and commit it. Then relaunch the same `am run --milestone` command. Committing first is required: `merge_tip` raises `MergeInProgressError` on a merge already in progress, and Integrate escalates again rather than touch it.
- **Limits, stated plainly (addendum §5).** (a) A resolver can misjudge what two edits meant even when the result compiles and passes. The final verification narrows that risk but does not remove it, so a human still reviews the branch. (b) Merges are sequential because there is one working tree. (c) Hundreds of stories means hundreds of sequential merges, and nothing batches them.

### 2. README.md: update earlier sections that say Integrate is missing

- **Preview with `--dry-run`:** `data` gains `integrate`: `{"branch", "worktree", "order"}`, where `order` is a list of `{"story", "tip"}` in merge order (cli.py ~931-945). The preview still writes nothing and creates no integration branch or worktree.
- **What a clean run leaves behind:** add `integrated`, `{"branch", "worktree", "merged", "resolved"}`, where `merged` and `resolved` are story ids in merge order (orchestrate.py `integrated_payload`). Say that `done: true` now comes only after Integrate succeeded. Keep `tips`, since those are the story branches that were merged into `integrated.branch`. Replace "Nothing is merged … Merging the tips is left to you" with: the tips are merged into `<prefix>-integrate` only, the base branch does not move, nothing is pushed, and merging the integration branch into the base is left to you.
- **What an escalation report contains:** add the Integrate escalation payload (orchestrate.py `integrate_escalated_payload`): `escalated: true`, `phase: "integrate"`, `story` (the story whose tip was being merged, or `null` when the final verification failed), `files` (the conflicting files, and empty for a merge already in progress or a verification failure), `detail`, `run_id`, `warnings`. It exits 1. The run is recorded `escalated`, and the branch and worktree are left exactly as Integrate left them.
- **Relaunching resumes:** relaunching a finished milestone still runs Integrate. With every tip already merged it is a no-op: no agent is dispatched, `completed` is empty, and `done` is reported with `integrated`. Relaunching after an Integrate escalation retries Integrate once the human has committed.
- **Not there yet:** remove the "There is no Integrate step" bullet. Keep "`am resume` is not milestone-aware" and "`watch`, `retry` and `cancel` do not exist". Add a link to §6 of the Integrate addendum, `docs/superpowers/specs/2026-09-25-integrate-design.md#6-deferred`, next to the existing orchestration §4 and parallel §5 links. Listing the other §6 items (`--no-integrate`, cost capture, the reviewer Plan-Hash brief, marking slow tests, per-story readiness) is optional. If listed, they must be listed as absent. None of them is described as existing anywhere.

### 3. Main spec §12

Inside `## 12. Failure, escalation, and blast radius` (line 445), add one short `**Status:**` paragraph. It says that as of milestone 5, Integrate merges every story tip into a local `<prefix>-integrate` branch after the last level, and an unfinished merge or a failed final verification escalates like any other gate. It also says that the blast-radius guarantee holds: Integrate never touches `main`/`master` or the base branch and never pushes (I5). Change nothing else in the spec.

## Observable behavior / acceptance

- The README describes only behavior present in this worktree's `src/`. It makes no claim that a deferred item would have to make true.
- Every command in the README runs as written against a temporary git repo and a temporary `brd` board, with all temp files in the session scratchpad. Build the repo and board the way `tests/e2e/test_integrate.py` and `tests/e2e/conftest.py` do: a milestone with two independent stories in one level, and one story blocked by another. Check by hand:
  - `am run --milestone … --branch-prefix … --dry-run --pretty` shows `data.integrate` with `branch` `<prefix>-integrate`, the worktree path, and `order` in merge order. It exits 0, and afterwards no `<prefix>-integrate` branch or worktree exists.
  - Real runs, with the fake `claude` from `tests/e2e/fake_claude.py` on `PATH` (never a real harness). (a) A clean milestone reports `done` and `integrated`, `integrated.resolved` is empty, and the base branch ref is unchanged. (b) A relaunch changes nothing. (c) The documented human step, `git merge <prefix>-integrate` from the base checkout, works as written.
  - The Integrate escalation and recovery path (commit in the integration worktree, then relaunch) is exercised as far as the fake claude scenarios in `tests/e2e/test_integrate.py` allow. Any path that cannot be run that way must not be presented as verified output.
  - Existing README commands still run. The `[--max-concurrent N]` synopsis is placeholder syntax and is checked both with and without the option.
- If a command does not run as written, the fix goes in the README, not in `src/`.
- Rule 2: `uv run pytest` (the default suite) stays green.

## Error paths

- The checkout lacks Integrate: stop and report.
- A README command fails as written: fix the README text.
- An implementation fact disagrees with this spec (key name, path, exit code): the source in this worktree wins, and the discrepancy is noted in the hand-off.

## Tests

Test placement follows main spec §14: pure functions get unit tests, steps run against temp repos and a temp board, the engine is driven by a fake adapter, and end-to-end tests are opt-in and excluded from the default suite. A docs-only card adds **no new tests**. Integrate's behavior is already covered by the default-suite tests of the earlier m5 cards and by `tests/e2e/test_integrate.py` (fake claude) and `tests/e2e/test_real_harness_integrate.py` (opt-in, `-m e2e`).

- README command check: a **manual** run against a temp repo and temp board, as listed above. It is not added to any tier. If a later card automates the dry-run `integrate` key check, that belongs in the default suite in `tests/test_cli.py` (CLI/steps tier, temp board, no harness), not in `tests/e2e`.
- Regression: the full default suite, `uv run pytest`, passes unchanged.
