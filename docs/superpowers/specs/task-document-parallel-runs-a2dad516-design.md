# Document parallel runs — subtask design (a2dad516)

Card: a2dad516 "Document parallel runs". Parent story 4633be8c "Prove it: parallel under a fake claude, against a real harness, and documented", milestone cdbfa10d. Blocked by 2af0e413 (done). Siblings 1976123f and 2af0e413 wrote only `tests/e2e`. This card is docs only.

Governing designs: main spec `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§11, §12) and the parallel addendum `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` (P1–P6, §4 limits, §5 deferred).

## Checkout precondition

Plain master (72a6cff) has no parallel implementation and no `--max-concurrent`. This card's worktree, `.claude/worktrees/m4/task-document-parallel-runs-a2dad516`, is stacked on the m4 tip (2af0e413's branch) and already contains it: `cli.DEFAULT_MAX_CONCURRENT = 4`, `models.RunConfig.max_concurrent_stories` defaults to 4, `orchestrate.run_milestone(max_concurrent=...)`, `escalated_payload` with `also_escalated`/`stopped`. All README claims are written and checked against the source in this worktree. If the stage finds itself on a checkout without `--max-concurrent` in `src/agent_manager/cli.py`, it must stop and report, not write docs against it.

## Scope

Files touched: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§11 only). No file under `src/` or `tests/` changes.

### 1. README.md, "Milestone runs" section

Edit the existing section (it already has `[--max-concurrent N]`, the default-4 / 1-is-sequential blurb, the dry-run `data.max_concurrent` and per-level `concurrent`, and the exit-2 refusals). Keep those. Add or fix the following:

- **Parallel runs subsection** (new, e.g. `#### Parallel runs`), covering:
  - `--max-concurrent N` bounds how many of one level's stories run at once. Default 4. `1` runs them in sequence, the same as the old sequential runner. The value is recorded in the run's config (`max_concurrent_stories`).
  - What runs together: only stories in the same dependency level. Levels are barriers, so level N+1 starts only after every story of level N has finished. Subtasks in a story always run in order, each stacked on the one before.
  - Stop and `stopped`: the first escalation, or an exception inside a lane, sets a stop. Every other lane checks it before starting its next phase. A phase already running is never interrupted, so a stop waits for the running phase (for example a long `implement`) to finish. The lane's current subtask is then recorded `stopped` and no later level starts.
  - `stopped` versus `escalated`: `escalated` is a failure: a gate gave up, or the lane raised. `stopped` is a clean park between two phases. Nothing failed, and the work done so far is kept.
  - Relaunching the same `am run --milestone` command continues a stopped subtask from where it parked and skips every card already `done`. `am resume <run-id>` on a run that has stopped subtasks is refused, and its message says to relaunch the milestone command (cli.py `find_resumable` remedy text).
  - One process per repository: two `am` processes on the same repository or the same run are not supported (addendum P2, P3).
  - Limits, from addendum §4, stated plainly: (a) the repo's tests run side by side, because each lane's `verify` runs in its own worktree at the same time. Tests that use a fixed port, a shared file or a shared database will collide, and `--max-concurrent 1` is the fix. (b) `uv run pytest` builds a `.venv` in each worktree, which costs time and disk once per lane. (c) Machine load: N lanes means up to N `claude -p` processes at once, and nothing rate-limits them.
- **"What an escalation report contains"**: replace the stale sentence "The run stops at the first subtask that does not finish, and no later subtask or story starts" with the stop semantics above (in brief, pointing to the parallel subsection). State that the top-level `level`, `story`, `subtask`, `failed_phase`, `detail` describe the **first** escalation. Document the two optional keys, each present only when non-empty: `also_escalated`, a list of `{level, story, subtask, failed_phase, detail}` for other lanes that failed before they saw the stop, and `stopped`, a list of `{story, subtask, before_phase}` for parked lanes. Say that a stopped subtask and its story are recorded `stopped`, not `escalated`.
- **"Relaunching resumes"**: extend it to cover stopped work, not only escalations and killed runs.
- **Leftover wording**: line 40, "each story's subtasks run one at a time", is correct (subtasks are sequential) but reword it so it cannot be read as stories running one at a time. Any other "one at a time" wording that refers to stories must say `--max-concurrent 1`.
- **"Not there yet"**: keep Integrate, milestone-aware `am resume`, and `watch`/`retry`/`cancel`. Link the parallel addendum's deferred section, `docs/superpowers/specs/2026-09-24-parallel-stories-design.md#5-deferred`, either in place of or next to the existing orchestration-addendum §4 link. Do not describe per-story readiness, cost capture, the reviewer Plan-Hash brief or Ctrl-C handling as present. Listing them as deferred is optional, and only by linking to §5.

Every command shown in the README must match cli.py's option names in this worktree.

### 2. Main spec §11

Under `## 11. Concurrency` (line 424 in this worktree), add one short `**Status:**` paragraph. It says that as of milestone 4, stories in a dependency level run in parallel, bounded by `max_concurrent_stories` (default 4, set with `am run --milestone --max-concurrent N`), and that levels are barriers. Change nothing else in the spec.

## Observable behavior / acceptance

- The README describes only behavior present in this worktree's `src/`. It contains no claim that a later milestone would have to make true.
- Every command block in the README runs as written against a temporary git repo plus a temporary `brd` board with a small milestone (at least two independent stories in one level, and one story blocked by another). Check by hand:
  - `am run --milestone ... --branch-prefix ... --dry-run --pretty` with no `--max-concurrent` shows `max_concurrent: 4`. With `--max-concurrent 1` and with `2`, each level's `concurrent` equals `min(len(level), N)`. Exit 0, and nothing written.
  - The refusals the README lists: `--max-concurrent 0`, and `--max-concurrent` with `--card`, each exit 2 with nothing on stdout.
  - Milestone resolution errors the README cites give exit 3.
  - Real-run commands (`am run --milestone ... --verify ...`, a relaunch, and `am resume` refusing a run with a stopped subtask) are run with the sibling fake `claude` from `tests/e2e` on `PATH`, not a real harness. If a command cannot be run that way, the README must not present it as verified output.
  - If a command does not run as written, the fix goes in the README, not in `src/`.
- Rule 2: `uv run pytest` (default suite, `-m "not e2e"`) stays green.

## Error paths

- Checkout lacks the parallel implementation: stop and report. Do not document it.
- A README command fails when run as written: fix the README text so it matches the CLI.
- An implementation fact disagrees with these findings (for example a default, a key name or an exit code): the source in this worktree wins, and the discrepancy is noted in the hand-off.

## Tests

Per main spec §14 placement (pure functions get unit tests, steps run against temp repos and a temp board, the engine is driven by a fake adapter, and e2e is opt-in and excluded from the default suite), a docs-only card needs **no new tests**. The existing behavior is already covered by `tests/test_cli.py`, `tests/test_orchestrate.py` and the `tests/e2e` parallel proofs.

- README command check: a **manual** run against a temp repo and temp board, as listed above. It is not added to e2e. If a later stage chooses to automate the dry-run/refusal checks, they belong in the default suite in **`tests/test_cli.py`** (CLI tier, temp board, no network) and not in `tests/e2e`. The real-run checks that need the fake `claude` stay manual for this card.
- Regression: the full default suite, `uv run pytest`, must pass unchanged.
