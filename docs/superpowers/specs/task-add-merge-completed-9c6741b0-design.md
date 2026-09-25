# Subtask 9c6741b0: `merge_completed_gate`, where git judges a merge

Parent story 9b04dd11 ("The merge step: merge a tip, and judge a merge with git"), milestone db5b5a3b. This narrows decision I3 of `docs/superpowers/specs/2026-09-25-integrate-design.md` (lines 60-73) to one deterministic gate. The resolver's `resolved` flag is advisory, and git decides whether the merge is complete.

## Base

`src/agent_manager/steps/integrate.py` and `tests/steps/test_integrate.py` come from sibling ce288496 (branch `m5/task-merge-one-story-tip-ce288496`). This worktree already contains them. Add the new functions to that file and reuse its helpers (`GitError`, `GitRunner`, `run_git`, `_first_line`, and the `rev-parse --verify --quiet MERGE_HEAD` probe pattern). Do not recreate or change `merge_tip`, `MergeInProgressError`, `_refuse_unfinished_merge` or any other sibling behaviour.

## Scope

1. `measure_merge(worktree, git_runner=run_git)` in `steps/integrate.py`. It is read-only and returns a plain dict (internal state, so no Pydantic) with every key always present:
   - `merge_in_progress: bool`: whether `MERGE_HEAD` exists. The probe is `["-C", worktree, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"]`. Exit 1 means the ref is absent. Any other `GitError` propagates.
   - `status: str`: the raw `git status --porcelain` output. Untracked files count as dirty.
   - `marked_files: list[str]`: the files the merge touched that still hold conflict markers, as worktree-relative paths in git's order.
2. The touched set is the files that differ from the first parent. While `MERGE_HEAD` exists, the first parent is `HEAD`, so the set is the files that differ between `HEAD` and the working tree/index, including unmerged paths. Once the merge is committed, it is the files that differ between `HEAD^1` and `HEAD`. If `HEAD` has no parent, the touched set is empty. Files outside the touched set are never read. A touched path that no longer exists on disk (a deletion) is skipped. Files are read as bytes, so binary or non-UTF-8 content cannot crash the scan. Paths containing spaces or unusual characters must be read correctly.
3. A file is marked only if it has BOTH a line starting with `<<<<<<< ` (seven characters and a space) and a line starting with `>>>>>>> `. A lone `=======` line, such as a markdown or rst underline, never counts.
4. `merge_completed_gate(result, worktree, *, git_runner=run_git)`. It follows the gate contract (engine.py:291-317) and never consults `result` (not even `resolved`). It returns `None` only when three things hold: no merge is in progress, `status` is empty, and `marked_files` is empty. Otherwise it returns `{"detail": <str>}`, and only that key, because `_render_verdict` prints `key=value`. The detail names every failing condition present, in this order:
   - that a merge is still in progress, naming `MERGE_HEAD`, and that it must be committed;
   - the files that still hold conflict markers;
   - that the working tree is not clean, with the dirty paths taken from `status`.

   The detail is written as direct feedback for the resolver, because it is appended to the resolver's retry brief. For example: "The merge is not complete: a merge is still in progress (MERGE_HEAD exists); commit it. Conflict markers remain in: a.txt. The working tree is not clean: ?? scratch.txt." `git_runner` stays keyword-only with a default, so `bind_arguments` never requires it.
5. `workflow/registry.py` registers the gate as `"merge_completed_gate"`. The name is bare, like every other gate. It is registered to the real imported callable, not a wrapper, so `resolve("merge_completed_gate") is integrate.merge_completed_gate`. The name goes into `BUILTIN_FUNCTION_NAMES` in sorted position (after `implement_blocked_gate`). The docstrings of the tuple and of `default_registry()` must be reworded. Today they say "every name in `builtin/task.yaml`". They should say "every name the builtin workflow documents use: `task.yaml`, plus the integrate-only names the forthcoming `integrate.yaml` will reference". `builtin/task.yaml` itself is not edited.

## Drift checks that must be updated (not deleted)

These tests currently pin the registry to exactly the task.yaml names. Each one keeps its drift protection, with the one integrate-only name made explicit:
- `tests/workflow/test_registry.py:103-105, 251`: keep `TASK_YAML_NAMES` unchanged, add `INTEGRATE_ONLY_NAMES = ("merge_completed_gate",)`, and assert that the default registry and `BUILTIN_FUNCTION_NAMES` equal the sorted union of the two.
- `tests/workflow/test_builtin_task.py:249-254`: the functions task.yaml resolves equal the registry names minus the integrate-only names, and each one still `is` the registry binding.
- `tests/test_engine.py:1163-1179`: add `"merge_completed_gate"` to the fake function map as a stand-in that raises if called, because task.yaml never references it.

## Error paths

- A `MERGE_HEAD` probe that fails with any exit code other than 1 raises `GitError`. Any other git failure while measuring also propagates. The gate never turns a git failure into a pass or into a verdict.
- The gate and `measure_merge` never run `commit`, `merge`, `merge --abort`, `reset`, `checkout`, `clean`, `add` or `push`. They never write the base branch or any other ref.

## Out of scope

The `resolver` role, `integrate.yaml`, the synthetic Integrate story and subtasks, the final verification, the report and status (I4-I7), and `merge_tip` and anything else ce288496 owns. Also out of scope: --no-integrate, milestone-aware `am resume`, watch/retry/cancel, cost capture, and anything under `tests/e2e`.

## Tests

The placement rule is design spec §14 (lines 495-510): tiers follow the module's nature. `steps/integrate.py` is a step, so its tests are **Steps tier**. They live in `tests/steps/test_integrate.py`, use REAL temp git repos with no mocks and no network, and reuse that file's `requires_git`, `_git`, `_commit`, `repo`/`wt` fixtures, `_make_tip`, `_leave_a_conflict`, `_base_state` and `_recorder`. Binding is **Engine tier**, and the registry entries are tested in the **workflow registry tests**. Nothing goes in `tests/e2e`. The whole default suite (`uv run pytest`, including tests/e2e) must stay green.

Steps tier (`tests/steps/test_integrate.py`). Each test asserts that `_base_state(repo)` is unchanged and records its git calls to show that no forbidden operation ran:
1. A finished, committed merge (a clean `merge_tip`) passes: the gate returns `None`.
2. A merge still in progress (`_leave_a_conflict`) fails, and the detail contains `MERGE_HEAD` and the conflicting file.
3. A committed merge with an extra untracked or modified file fails, and the detail names that path.
4. A resolution committed with a file still holding both `<<<<<<< ` and `>>>>>>> ` lines fails, and the detail names that file.
5. A committed merge whose touched file contains only a `=======` underline passes.
6. `result={"resolved": True, ...}` on an unfinished merge still fails exactly as in test 2.
7. A marker-bearing file that the merge did not touch (committed on the base before the tip) is ignored, and the gate passes.
8. A `MERGE_HEAD` probe that fails with an exit code other than 1 (a wrapping git runner that raises `GitError(exit_code=128)` for that argv) propagates `GitError`.
9. `measure_merge` returns all three keys with the expected values for both the in-progress state and the finished state.

Engine tier (`tests/test_engine.py`):
10. `bind_arguments(integrate.merge_completed_gate, values)` works when `values` has the shape `_gate_values` builds: context keys including `worktree`, plus `result` and the phase name. It binds exactly `result` and `worktree`, never `git_runner`, and raises no EngineError.

Workflow registry tier (`tests/workflow/test_registry.py`):
11. `default_registry().resolve("merge_completed_gate") is integrate.merge_completed_gate`. The updated drift checks above pass.
