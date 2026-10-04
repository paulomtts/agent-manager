# Board runs: milestone stacking and `--detach` — design

Status: proposed (S4 items 7 and 8; follows `2026-10-03-plugin-integration-design.md`).
Motivation: the Omarchy plugin's remaining milestones depend on each other's code
(controls need the monitor, dispatch needs the controls). `am run --board` orders
milestones by `blocked_by`, but every milestone is dispatched on the same
`--base-branch`, so a blocked milestone never sees its blocker's work.

## Problem

1. **No stacking across milestones.** In `_run_board_async`
   (`src/agent_manager/orchestrate.py`) every milestone is run with the same
   `base_branch`. Stories inside a milestone stack on their blocker's tip; the
   milestones themselves do not. A milestone blocked by another therefore starts
   without the blocker's code unless a human merges the blocker's
   `<prefix>-integrate` into the base branch between runs.
2. **No `--detach` for `--board`.** `cli.py` refuses `--detach` with `--board`. A
   board run holds a terminal for hours and dies with it.

## Goals

- A milestone blocked by an earlier milestone starts from that milestone's
  integrate branch, so one `am run --board` can run a chain of dependent milestones.
- `am run --board --detach` starts the whole board in the background and returns
  at once.
- Both are backwards compatible for boards that do not use them.

## Non-goals

- No merging into the base branch, ever. `am` still never touches `--base-branch`.
- No merged-base resolution for milestones with two or more blockers (stories
  have it; milestones stay refused, see below). Chain them instead (A ← B ← C).
- No board-level run record or run id: a board is still one run per milestone.

## Design

### 1. Milestone stacking

For each milestone M dispatched by a board run, `base_branch(M)` is:

| blockers of M (its `blocked_by` among milestone roots) | `base_branch(M)` |
|---|---|
| none that is open, or every blocker is `merged`/`canceled`/`archived` | `--base-branch` (unchanged) |
| exactly one open blocker B | `integrate_branch(B)` = `<prefix(B)>-integrate` |
| one blocker B that is `done` (not `merged`) **and** whose integrate branch exists locally | `integrate_branch(B)` |
| a `done` blocker without a local integrate branch | `--base-branch` (assume landed; today's behaviour) |
| two or more open blockers | refused before anything is written |

- "Open" is `dag.board_levels`' notion: not finished and some card under it is not.
  `merged` is finished and means the human has landed it; `canceled`/`archived`
  are out of play (`census.FINISHED_STATUSES` / `OUT_OF_PLAY_STATUSES`).
- The refusal is a new error, `MilestoneBlockersError`, raised in `run_board`'s
  refusals section (after cycle detection and prefix derivation, before the
  claims check). Its message names the milestone and its open blockers and says
  to chain them. Exit 3 with the usual envelope. It leaves no run row, run
  directory or lease.
- `prefix(B)` is derived exactly as `board_prefixes` derives it for open milestones
  (`<title slug>-<short id>`, or `<P>-<stem>` with `--branch-prefix P`). It must be
  computable for a blocker that is no longer open, so the derivation takes any
  milestone card, not only the open set. A blocker that is a milestone but not a
  root of the board's open set still uses the same function.
- The decision is a **pure function** (no I/O): `milestone_bases(milestones,
  prefixes, branch_exists, base_branch) -> dict[id, str]`, with the
  local-branch-exists check injected, so it is unit-testable. `_run_board_async`
  passes each milestone its own entry instead of the shared `base_branch`.
- Stacked milestones run exactly as before afterwards: stories root on the
  milestone's `base_branch` (which is now the blocker's integrate branch), and the
  milestone's own Integrate merges into its own `<prefix>-integrate`.
- A relaunch or `am resume` keeps the base branch the run recorded
  (`resumed.base_branch`), so a stacked milestone does not silently re-root.
- `--dry-run --board` reports `base_branch` on each milestone entry
  (`{"milestone_id","title","branch_prefix","base_branch","plan"}`), and each
  milestone's `plan` is computed against that base, so the `base` column shows
  the stacking. The new key is additive.

### 2. `am run --board --detach`

- Same shape as `--detach` for `--card`/`--milestone` (README "`--detach`"): the
  command first runs the board's whole **pre-flight in the foreground**: argument
  checks, board read, cycle check, prefix derivation, `MilestoneBlockersError`, and
  the up-front claim check over every milestone. Any refusal is the usual
  `{"ok": false, ...}` envelope, exit 3, and nothing starts.
- It then starts a detached child (own session, stdin closed) that owns the whole
  board run, writes its output to `<data dir>/boards/<stamp>-<digest>.log` (mode
  0600, `<digest>` = the repository digest used elsewhere), and writes the final
  board payload to `<data dir>/boards/<stamp>-<digest>.report.json` when it ends.
- The command prints one envelope and exits 0:
  `{"ok": true, "data": {"board": true, "detached": true, "pid", "log", "report", "levels"}}`.
  It has **no `run_id`**: each milestone's run is created when that milestone is
  dispatched, so the caller finds them with `am runs` or `am watch --all` (their
  first `run_upsert` carries `milestone_id`, never null on a board run).
- `--detach` with `--board --dry-run` stays refused. `--detach` without `--board`
  is unchanged. The README sentence refusing `--detach` with `--board` is removed.
- The claim race already handled by `run_board` (a claim another process takes
  after the up-front check becomes an `escalated` entry) applies unchanged and
  shows in the report file.

## Compatibility

- Boards with no inter-milestone `blocked_by` edges behave exactly as today.
- A `done` blocker with no local integrate branch behaves as today.
- All JSON changes are additive; journal and watch schema stay 1.
- `--detach` with `--board` stops being a usage error.

## Testing

- Unit: `milestone_bases` for every row of the table (including the two-blocker
  refusal, a done blocker with and without the branch, a merged blocker, a
  canceled/archived blocker, a chain A←B←C); prefix derivation for a non-open
  blocker; `run_board` refusal leaves nothing behind; dry-run `base_branch` and
  `base` column.
- `e2e_fake` (fake harness, no real agents): a two-milestone board where the second
  is blocked by the first: the second's worktree contains the first's commits;
  a three-milestone chain; a detached board run followed with `am watch --all`,
  paused and resumed per milestone.
- Detach: refusals happen in the foreground before detaching; the child survives
  the parent; log and report files exist with mode 0600; envelope shape.
- README: the `--board` section documents stacking and the refusal, and the
  `--detach` section documents `--board`.

## Open questions

- Milestones with two or more blockers are refused rather than merged-based. If
  that proves too strict, a merged base for milestones is a follow-up.
- `<data dir>/boards/` is new. If a board-level directory is unwelcome, the log and
  report can live under `<data dir>/runs/` with a synthetic id instead.
