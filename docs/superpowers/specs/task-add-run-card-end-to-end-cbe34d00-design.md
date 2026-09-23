# Add `run --card` end to end (cbe34d00)

## What this subtask delivers

The first `src/agent_manager/cli.py`: a Typer app whose single command is `run`, in its card-scoped form. It is the first thing in the project that drives `workflow/builtin/task.yaml` to completion for one real card, so it is also the first place where `board.py`, `dag.py`, `paths.py`, `store.py`, `workflow/loader.py`, `engine.py` and `dispatch.py` are composed. Per §4, `cli.py` is "typer app" only: it resolves inputs, constructs the `Run`/`StoryRun`/`SubtaskRun` records, injects collaborators, calls `engine.run_subtask` once, and renders the envelope. No step logic, no gate logic, no branch-string building, no journal or SQLite writing that does not go through `Store`.

Out of scope, owned elsewhere: `status`/`runs` (3c39ae43), `logs` (be23d3c0), `resume` (5524ae72) — this card creates the `Run` record those three later read, and nothing more; milestone orchestration (census, levels, parallel stories, integrate) and non-Claude harnesses belong to later milestones; `watch`, `retry`, `cancel`, `--dry-run`, `--workflow`, `--harness`, `--max-concurrent` from the §10 grammar are not implemented here.

## Command surface

```
agent-manager run --card <id> [--repo-dir .] [--base-branch master]
                  [--branch-prefix m1] [--allow-no-verification] [--pretty]
```

`--card` is required. `--repo-dir` defaults to the current directory and is resolved to an absolute path before anything else; it is both the directory `brd` is run in (`board.show(card_id, repo_dir=...)`) and the `root` that keys the SQLite projection (`Store.open(root, run_id)` → `paths.project_db_path`). `--pretty` is the §10 human switch: JSON is the default, on one line; `--pretty` indents it. Both forms use brd's envelope: `{"ok": true, "data": ...}` or `{"ok": false, "error": {"type": ..., "message": ...}}`, printed to stdout. Diagnostics, if any, go to stderr.

## Observable behaviour

On a successful invocation the command, in order:

1. Resolves the card with `board.show(card_id, repo_dir=repo_dir)` and its parent with `board.show(card.parent_id, ...)`. These are the §7 `card` / `parent_story` cached-at-run-start reads; nothing else in the run talks to the board except the two `best_effort` `rollup.set_status` phases.
2. Derives the branch with `dag.task_branch(branch_prefix, card)` — never by string concatenation in `cli.py` — and the worktree path as `<repo_dir>/.claude/worktrees/<branch>`, absolute, matching the layout `steps/worktree.ensure` expects (it requires an absolute `worktree` and refuses anything else).
3. Mints a run id (`<UTC timestamp>-<dag.short_id(card_id)>`, from an injectable clock so tests are deterministic) and opens `Store.open(repo_dir, run_id)`. Every artifact path comes from `paths.py`; nothing is written under the worktree (D4/§9).
4. Loads the document with `workflow.loader.load_builtin("task")`.
5. Records, before the walk and through `Store` only, so `status`/`logs`/`resume` have something to read even if the process dies immediately: `models.Run(id=run_id, workflow="task", repo_dir=..., base_branch=..., branch_prefix=..., status="started", started_at=..., config=models.RunConfig())` → `record_run`; `models.StoryRun(card_id=parent.id, title=parent.title, level=0, status="started", tip_branch=branch)` → `record_story`; `models.SubtaskRun(card_id=card.id, branch=..., base_branch=..., status="started", worktree_path=...)` → `record_subtask`. Field names and types are used verbatim as models.py:89-159 declares them.
6. Builds the real agent runner — `dispatch.AgentRunner(workflow=..., store=..., launcher=<the direct launcher>, run_id=..., story_id=parent.id, card_id=card.id)`, leaving `adapters` at `harness.registry.default_adapters()` and `harness_map` empty so roles fall back to `DEFAULT_HARNESS` — and calls `engine.run_subtask(workflow, store, story_id=parent.id, subtask=subtask, repo_dir=repo_dir, commands=(), card=card, parent_story=parent, agent_runner=runner)`. The runner is a constructor parameter of the command's implementation function, not a hard-coded import inside it: that injection seam is what lets the tests pass a fake and launch no harness.
7. Re-records the run, story and subtask with their terminal status from the returned `SubtaskSummary` (`done` → `done`; `escalated` → `escalated`), then renders the envelope.

Success payload (`data`): `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status` (`done` | `escalated`), `failed_phase`, `detail`, `skipped`, `warnings` (the summary's, including best-effort board-write failures — §12 forbids reporting a clean success when the board never moved). Exit code `0` when the summary is `done`, `1` when it is `escalated`: an escalated subtask is a truthful result, so the envelope stays `ok: true` and the exit code carries the bad news.

### Verification context and `--allow-no-verification`

§12's escape hatch is `steps/reducers.verification_gate(suite_cmds, allow_no_verification, caller_provided)`, and `builtin/task.yaml` puts that gate on the `explore` phase. Gate arguments are bound by name from the phase's binding table (`dispatch.gate_values` over the engine's context), and `engine.subtask_context` supplies none of those three names — so running `task.yaml` today would fail the gate's binding with `EngineError: parameter 'suite_cmds': no value for a required parameter`. This card is where that surfaces, and the narrowest fix it owns is a seam, not a reimplementation: `engine.run_subtask` gains an optional extra-context mapping that is folded into the binding table after `subtask_context` and before `_document_paths`, and `cli.py` passes exactly `{"suite_cmds": list(commands), "allow_no_verification": <flag>, "caller_provided": False}`. The flag is forwarded as a real `bool` because the gate tests identity (`allow_no_verification is True`). `commands` stays `()` for this card (verification discovery per run is not this subtask's), so with the flag absent an empty suite blocks `explore`, and with the flag present the walk proceeds — which is precisely the behaviour §12 describes. No gate wording, no gate logic and no reducer duplication enters `cli.py`.

## Error paths

Every one of these prints `{"ok": false, "error": {"type": <exception class name>, "message": <str>}}` and exits `3` (Typer keeps `2` for its own usage errors, so `3` distinguishes "the tool could not run this" from "the subtask escalated"):

- `board.BoardError` — `brd` missing from PATH, a non-zero exit, a non-envelope stdout, an unknown card id. The message is brd's verbatim; the CLI invents no "not found" semantics.
- The card has no `parent_id`. `run --card` drives a subtask of a story; a card with no parent has no `StoryRun` to key `record_phase`/`record_subtask` by. Refused before any run id is minted, with a message naming the card and the missing parent.
- `--repo-dir` does not exist, or is not a directory.
- `workflow.loader` failures from `load_builtin("task")` (a broken document is a load-time bug and must reach the operator unchanged).
- `errors.EngineError` escaping the walk — e.g. an unresolvable declared input, or an unbindable gate parameter. `engine.run_subtask` deliberately lets these out rather than journalling them as a phase failure.

Errors raised *after* the `Run` row exists still leave that row and the journal in place; the CLI does not delete run state on failure. `AgentPhaseFailed` never reaches the CLI — the engine converts it to an `escalated` summary.

## Tests

All CLI tests live in `tests/test_cli.py` (mirroring `src/agent_manager/cli.py`) and run under the default suite: `uv run pytest`. Tiering follows §14 of the design spec (lines 477-492). The card's own text — "a fake adapter, a temporary git repo and a temporary brd board, no real harness is launched" — puts the integration tests in the **Engine tier** (fake agent runner returning canned results) built on **Steps-tier fixtures** (temp git repo, temp brd board, `XDG_DATA_HOME` pointed at a tmp dir so `paths.data_dir()` never touches the real one). Explicitly *not* the End-to-end tier: that tier is the single opt-in real-harness toy milestone, out of scope here.

1. **Happy path.** `run --card` over the temp board drives `task.yaml` to `done` with a fake agent runner supplying valid canned results: envelope is `{"ok": true, ...}` with `status: "done"`, exit code `0`. *Engine tier (Steps-tier fixtures).*
2. **Branch and worktree derivation.** The recorded branch equals `dag.task_branch("m1", card)` for the default prefix and equals the prefixed form for a custom `--branch-prefix`; the worktree path is `<repo_dir>/.claude/worktrees/<branch>` and absolute; `--base-branch` lands in both `Run.base_branch` and `SubtaskRun.base_branch`. *Engine tier.*
3. **State is durable and outside the worktree.** After a run, the project DB at `paths.project_db_path(repo_dir)` holds the run, story and subtask rows, `paths.run_dir(run_id)/journal.jsonl` holds the matching lines in journal-before-row order, and `git status` in the temp repo is clean — no artifact was written inside it. *Engine tier (Steps-tier fixtures).*
4. **Rows exist before the walk.** With a fake runner that raises on its first call, the run/story/subtask rows are already recorded — the guarantee `status`/`resume` depend on. *Engine tier.*
5. **Escalation.** A fake adapter whose result fails a non-retryable gate yields `status: "escalated"`, a populated `failed_phase` and `detail`, exit code `1`, and an `escalated` subtask row. *Engine tier.*
6. **Best-effort warning surfaces.** A board write that fails (brd stub exiting non-zero for `update`) still reaches `done`, and the warning appears in the payload's `warnings`. *Engine tier (Steps-tier fixtures).*
7. **`--allow-no-verification`.** Without the flag and with no discovered suite, `explore`'s `verification_gate` blocks and the subtask escalates; with the flag, the same fixture walks past `explore`. *Engine tier.* (The gate's own truth table is already unit-tested in `tests/steps/test_reducers.py` and is not re-tested here.)
8. **Unknown card.** `brd show` failing produces `{"ok": false, "error": {...}}` carrying brd's message, exit code `3`, and no run directory under the tmp data dir. *Engine tier (Steps-tier fixtures).*
9. **Parentless card.** A board card with no `parent_id` is refused with an `ok: false` envelope naming the card, exit code `3`. *Engine tier (Steps-tier fixtures).*
10. **No harness is launched.** The injected fake runner is the only thing called; the real `dispatch.AgentRunner`/launcher is never constructed in any test above — asserted once, directly, by running with a launcher that fails the test if invoked. *Engine tier (the adapter-tier rule that the launcher is injected, applied at the CLI seam).*
11. **Envelope rendering.** The pure renderer produces single-line JSON by default and indented JSON under `--pretty`, with the `{ok, data}` / `{ok, error}` shapes. *Unit tier (pure function).*
12. **Run-id and worktree-path derivation.** The pure helpers: a fixed clock and card id give a stable run id; the worktree path composes from repo dir and branch. *Unit tier (pure functions).*
