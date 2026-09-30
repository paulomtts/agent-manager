# Subtask 64babfa2 — Move dry_run_payload's pure plan computation into runs.py

Parent story: 4bc0a3e0 "cli.py sheds its collaborator role" (milestone 9c44c2fb, architecture cleanup S1). Source of truth: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` §3 "S1", §5 "Compatibility", §6 "Testing". Builds on the state left by `1c21b3dd` (this card's `blocked_by`). Line numbers below are from this worktree at spec time and drift; re-read before editing.

## Scope

In scope:

- Add a new pure function to `src/agent_manager/runs.py`, `compute_dry_run_plan(stories, *, repo_dir, branch_prefix, base_branch, max_concurrent)`, holding the computation currently inline in `cli.dry_run_payload` (`cli.py:776-862`): the `dag.assert_no_blocker_cycles` check, `dag.compute_levels`, the `stories_by_id` map, the per-story row assembly (`dag.stack_bases`, `dag.story_root`, `dag.subtask_branch`, `dag.remaining_subtasks`, the `merged_from` key only when `root.kind == "merged"`), the per-level `concurrent = min(len(level), max_concurrent)`, and the Integrate plan (`integration.integration_branch`, `worktree_for(repo_dir, ...)`, `integration.merge_order`). It returns the computed level rows and the integrate plan (exact return shape — e.g. a small dataclass or a dict with `levels`/`integrate` — is a planning choice; plain data, no Pydantic, since it is internal-only state).
- `max_concurrent` is a required argument of `compute_dry_run_plan`; the `DEFAULT_MAX_CONCURRENT` constant and default stay on `cli.dry_run_payload` (`tests/test_cli.py:3147` asserts `cli.DEFAULT_MAX_CONCURRENT`).
- `cli.dry_run_payload` keeps its exact signature, calls `runs.compute_dry_run_plan(...)`, and only assembles the envelope dict: `max_concurrent`, `levels`, `already_done`, `integrate`, in that key order. Its current docstring (`cli.py:776-800`) describes the pure computation's own details (the cycle check ordering, `stories_by_id`, `stack_bases`, the merged-root rule, `concurrent`'s formula) — that content moves onto `runs.compute_dry_run_plan`'s docstring, next to the code it now describes. `cli.dry_run_payload` keeps a short docstring describing only what it still does: delegate to `compute_dry_run_plan` and assemble the envelope (mentioning `already_done_entries` and the envelope key order, since those still live here). Splitting the docstring this way, rather than copying it verbatim onto both functions, is what "docstring intent" means here — a verbatim copy would leave `cli.dry_run_payload`'s docstring describing `dag` calls it no longer makes.
- The `integration` import inside the new `runs.py` function stays **deferred (in-function)**, with a comment mirroring the existing one at `cli.py:808-810`. A top-level import would create `runs → integration → cli → runs` because `integration.py:29` still imports `cli` and `cli.py:51` imports `runs` at top level. `cli.dry_run_payload` no longer needs its own deferred `integration` import once the computation moves; removing that now-dead import from `dry_run_payload` is fine, but the deferred imports elsewhere in `cli.py` are untouched.
- `already_done_entries` (`cli.py:749`) — judgment call, not settled by the card text: it stays in `cli.py` and `dry_run_payload` keeps calling it when assembling the envelope. The card names "levels, bases, roots and the Integrate plan", not the already-done listing. Moving it too would be defensible, but it is left out so this card stays inside what the card text says.

Out of scope (owned elsewhere or excluded by the spec):

- Repointing `bases.py:34`, `integration.py:29`, `orchestrate.py:54` at `runs`, deleting cli.py's other deferred in-function imports, dropping re-exports, and the static "bases/integration never import cli" test — all belong to sibling `61a0d9be`.
- `default_runner_factory` stays in `cli.py` (exemption set by `46244d0e`); helpers already moved by `46244d0e`/`1c21b3dd` are not touched again.
- No change to the pygents turn/phase model, checkpoint format, harness adapter contract; no typecheck/CI gate; no rewriting of existing monkeypatch calls; no grafo change.
- `runs.py` gains no Typer import.

## Observable behavior

None changes (§5 Compatibility). `am run --dry-run` output is byte-for-byte identical: same envelope, same exit codes, same key order at every level (top-level `max_concurrent, levels, already_done, integrate`; level row `level, concurrent, stories`; story row `story, title, root, subtasks[, merged_from]`; subtask row `id, title, status, branch, base`; integrate `branch, worktree, order`; order entries `story, tip`). `merged_from` is still present only on merged-root rows, in `blocked_by` order. `worktree` is still `str(worktree_for(repo_dir, integrate_branch))`, and `repo_dir` is still only joined onto, never read.

## Error paths

- A blocker cycle still raises from `dag.assert_no_blocker_cycles` as the first thing done, now inside `runs.compute_dry_run_plan`, and propagates through `cli.dry_run_payload` unchanged (same exception type and message). The CLI error envelope and exit code are the same as before.
- No new error paths; a `max_concurrent < 1` is still refused by the caller, not here.

## Tests

Tier rule: this repo has no tier taxonomy. Tests mirror source one file per module, flat under `tests/`; only real-harness end-to-end tests go in `tests/e2e/` (marked `e2e`, excluded by `-m "not e2e"` in `pyproject.toml`). This is a pure-function extraction, so nothing goes in `tests/e2e/`.

- Existing `cli.dry_run_payload` tests in `tests/test_cli.py` (around lines 880-1110, e.g. `test_the_dry_run_payload_lists_remaining_subtasks_on_full_list_bases`, `test_the_dry_run_payload_plans_integrate_over_every_story_in_integrate_order`, `test_the_dry_run_payload_defaults_to_four_lanes`, the cycle-refusal test near line 967) — tier: flat `tests/test_cli.py`. Must pass **unchanged**; they are the byte-for-byte guard for the envelope.
- Existing CLI-level `--dry-run` tests and the `DEFAULT_MAX_CONCURRENT` assertion in `tests/test_cli.py` — tier: flat `tests/test_cli.py`. Pass unchanged.
- Optional: one direct test of `runs.compute_dry_run_plan` (e.g. levels and integrate order for a small census, `concurrent` honoring the passed `max_concurrent`) — tier: flat `tests/test_runs.py` (exists, mirrors `runs.py`). Add only if planning finds it useful; the existing `test_cli.py` coverage already exercises the function through the wrapper.

## Verification

- fullSuite: `uv run pytest` (green)
- typecheck: none (CLAUDE.md: no separate lint or typecheck command)
- lint: none
