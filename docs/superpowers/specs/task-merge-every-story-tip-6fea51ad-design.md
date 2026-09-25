# Subtask 6fea51ad — Merge every story tip and verify the result

Parent story 006d0a0e "Integrate in the runner" (milestone db5b5a3b). This narrows decisions I1, I3, I4 and I5 of `docs/superpowers/specs/2026-09-25-integrate-design.md` to one new module. I2 (the merge step) and the resolver role, `ResolveResult`, `merge_completed_gate` and `workflow/builtin/integrate.yaml` already exist on this base (`src/agent_manager/steps/integrate.py`, `src/agent_manager/workflow/builtin/integrate.yaml`) and are reused unchanged.

## Scope

A new `src/agent_manager/integration.py` exposing:

```python
def integrate_milestone(
    stories, repo_dir, base_branch, branch_prefix, commands,
    allow_no_verification, store, run_id, runner_factory,
) -> IntegrateOutcome
```

- `stories`: the census `StoryPlan`s of the milestone (all of them, done or not).
- `runner_factory`: a `cli.RunnerFactory`.
- The outcome is a plain dataclass (internal state, per CLAUDE.md, so not Pydantic). It is either a success or an escalation:
  - success: `branch`, `worktree`, `merged` (story ids whose tip is in the integration branch, in merge order, already-merged ones included) and `resolved` (story ids whose conflict a resolver fixed).
  - escalation: `phase = "integrate"`, `story` (the story id involved, or `None` for the final verification), `files` (conflicting files, or empty) and `detail` (a human-readable reason).

No edits to `orchestrate.py` or `cli.py`. It only reuses `cli.worktree_for` and `cli.gate_context`. Wiring into `run_milestone`, the payload keys, the run status, the exit code, relaunch behaviour and `--dry-run` belong to sibling a74f2cd6. The e2e tests and the fake claude `resolve` phase belong to sibling a37460b9.

## Observable behaviour

1. **Order (I1).** Levels come from `dag.compute_integrate_levels(stories)`, over all stories. Within a level the order is census order. Each story's tip comes from `dag.story_tip(story, stories_by_id, branch_prefix, base_branch)`. A story with no subtasks is skipped: it is not merged and not listed in `merged`.
2. **Branch and worktree.** The integration branch is `f"{branch_prefix}-integrate"`. The worktree is `cli.worktree_for(repo_dir, branch)`, which gives `.claude/worktrees/<branch>`. Merges run one at a time in that worktree.
3. **Merging.** For each tip, call `steps.integrate.merge_tip(repo_dir, worktree, integration_branch, base_branch, tip)`. A result that is clean or `already_merged` moves on to the next tip.
4. **Conflict (I3).** When `merge_tip` reports `conflict`:
   - Record the synthetic story in the store, if it is not already recorded: `StoryRun(card_id="integrate", title="Integrate", ...)`.
   - Record one synthetic `SubtaskRun` for this story, with `card_id` = the conflicting story's id, `branch` = the integration branch, `base_branch` = the base branch and `worktree_path` = the integration worktree.
   - Both records go through `store.record_story` / `store.record_subtask` before the engine journals any phase. `store.rebuild_from_journal` rejects a phase whose story or subtask was not created by an earlier line.
   - Then load `workflow.loader.load_builtin("integrate", registry)` and call `engine.run_subtask(workflow, store, story_id="integrate", subtask=..., repo_dir=..., commands=..., extra_context=..., agent_runner=...)`. `agent_runner` is `runner_factory(workflow=..., store=..., run_id=run_id, story_id="integrate", card_id=<story id>)`. `extra_context` is `{"merge_tip": tip, "conflict_files": files}` merged with `cli.gate_context(commands, allow_no_verification)`.
   - If the subtask ends `done`, add the story id to `merged` and `resolved`, then continue. Otherwise stop and escalate with the story, the files and a detail. The branch and worktree are left exactly as they are, with `MERGE_HEAD` still in place. Integrate never aborts, resets or cleans up.
5. **Final verification (I4).** After the last tip, call `steps.verify.run_suite(commands, worktree)` on the integration worktree and judge the result with `steps.reducers.verification_passed_gate`. A blocked verdict escalates (`story=None`, with the verdict's reason as `detail`). The check is skipped only when `commands` is empty and `allow_no_verification` is set, which is the same rule as the subtask gate. Empty commands without the flag escalate through `steps.reducers.verification_gate(list(commands), allow_no_verification, True)`, checked before `run_suite` (`verification_passed_gate` alone cannot catch an empty suite, since running zero commands reports passed); its verdict's `detail` becomes the escalation detail. With commands present, `run_suite` runs and `verification_passed_gate` judges it. Pass/fail is taken from the suite's measured result, never from an agent.
6. **Already integrated.** When every tip is already contained, every `merge_tip` returns `already_merged`, no agent is dispatched and the integration branch HEAD does not move. The final verification still runs under rule 5. The outcome is a success.
7. **Safety (I5).** Integrate never checks out, merges into, resets or pushes the base branch or any story branch, and never pushes anything. Every write happens in the integration worktree.

## Error paths

- `steps.integrate.MergeInProgressError` from `merge_tip` becomes an escalation. Its `story` is the tip's story, and its `detail` is the error's own message, which tells the human to finish the merge in the worktree and relaunch. Nothing is dispatched and nothing is recorded in the store.
- A resolver subtask that does not end `done` (the agent refused, `merge_completed_gate` failed after retries, or the `verify` phase failed) escalates as in rule 4, leaving `MERGE_HEAD` in place.
- A final verification failure escalates as in rule 5.
- Any other git failure from `merge_tip` (a bad ref, an I/O error) propagates as an exception. It is not turned into an escalation.

## Tests

All of these go in `tests/test_integration.py`, which mirrors `src/agent_manager/integration.py`. Per the test-placement rule in the findings, they are ordinary default-suite tests: not under `tests/e2e/` (that folder is for production wiring through the fake claude on PATH, which is sibling a37460b9) and not marked `e2e` (that marker is the opt-in real-agent tier). Each test uses real temporary git repos (a base branch plus story branches built with `git`), a real `Store` and a fake `runner_factory` whose agent runner counts dispatches.

The fake resolver knows only what its brief tells it. It reads the conflict list and the result path from the prompt text alone, with no plan hash and no committing of a spec or plan. It resolves by editing the files and committing the merge. An env-var switch that makes it refuse is allowed as test scaffolding. Every outcome that git can measure (`MERGE_HEAD`, HEAD, branch tips, file contents) is asserted through git, not through the agent's report.

1. **No conflict dispatches no agent.** Two stories touching different files: both are merged in level/census order, the factory is never called, the outcome is a success with an empty `resolved` list, and verification ran.
2. **A conflict dispatches exactly once per conflicting tip.** Two stories editing the same line: exactly one dispatch, for the conflicting story. `resolved` holds that story. The synthetic "Integrate" story and subtask are in the store, and `store.rebuild_from_journal` replays cleanly. git shows no `MERGE_HEAD` and both tips as ancestors of the integration HEAD.
3. **A failing resolver escalates with `MERGE_HEAD` left in place.** The refusing fake leads to an escalation with `phase="integrate"`, the story and the files. git shows `MERGE_HEAD` still present and the branch/worktree still there.
4. **An already-in-progress merge escalates.** Set up the worktree with an unresolved merge beforehand. The outcome is an escalation whose `detail` says a human must finish the merge and relaunch, and there are zero dispatches.
5. **A final verification failure escalates.** Clean merges plus a failing command in `commands` give an escalation at `integrate` whose detail names the verification failure.
6. **An already-integrated milestone is a no-op.** Run once to success, record the integration HEAD, run again: zero dispatches, HEAD unchanged, success.
7. **The base branch is untouched and nothing is pushed.** In every scenario above, the base branch tip SHA is the same before and after. Tests use a repo with a bare `origin` remote where needed, and assert that `origin` has no integration branch and no moved refs.

Also required: the whole default suite, `tests/e2e` included, stays green.
