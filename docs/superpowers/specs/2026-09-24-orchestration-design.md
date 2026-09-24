# Orchestration — design addendum

Date: 2026-09-24
Extends: `2026-09-23-agent-manager-design.md` (§5 milestone workflow, §10 CLI, §11 concurrency, §12 escalation)
Status: milestone 3 scope; decisions O1–O8

## 1. Why this shape

`am run --card` drives one subtask. The old `orchestrator.js` drives a whole
board. This milestone gives `am` that second ability, in the smallest slice that
is really usable: **a whole milestone, run one story at a time, stacked on local
branches, statuses rolled up the board, stopping on the first escalation.**

Milestone 2's lesson shapes the cut. Its cards each verified their own slice and
deferred the joins to a neighbour, so a merged, green milestone could not
dispatch anything, and the seams surfaced one run at a time. Here, every story
ends in a test through the production path, and the seams below were found by
reading the code before any card was cut.

Parallel stories, Integrate and the operator commands are a follow-up milestone
(section 4). They are large, they share one risky prerequisite (a thread-safe
store), and none of them is needed to run a milestone.

## 2. Decisions

**O1 — The census is pure and lives in `census.py`.** `models.Card` and
`CardNode` gain `blocked_by: list[str]` (default empty) and `created_at`, both
already emitted by `brd tree`. `board.roots()` reads `brd tree` with no id.
`census.py` holds three pure functions ported from `scripts/census.mjs`, whose
`census.test.mjs` is the behavioural specification:

- `find_milestone(roots, needle)`: exact id wins; then exact case-insensitive
  title; then a unique substring. A numeric needle matches only a digit run of
  its own length (`2` must not resolve to "Milestone 12"). Two matches is an
  error that lists them, never a guess.
- `order_siblings(cards)`: topological order by `blocked_by` edges *among the
  siblings*; edges leaving the sibling set are ignored; ties by `created_at` then
  id; a cycle is an error naming the cards.
- `flatten_milestone(root)`: `Census(milestone_title, stories)` where each
  `StoryPlan` carries `id, title, status, blocked_by, subtasks` and each
  `SubtaskPlan` carries `id, title, status`. brd derives `blocked` at read time;
  the census flattens it to `todo` in this one place.

**O2 — Geometry is derived, never discovered, and lives in `dag.py`.** Ported
from `orchestrator.js`: dependency levels over pending stories, cycle detection
that runs *before* geometry (a cycle between two populated stories never trips
the root walk's own guard), a story's root (its single in-milestone blocker's
tip, else the base branch), a story's tip (its last subtask's branch), and each
subtask's base (the previous subtask in the story's *full* ordered list, never
the remaining list). A story with two or more in-milestone blockers is refused
with a message that names them; a stack can only root on one parent. A story
marked `done`, or with no remaining subtasks, is never re-dispatched. A blocker
being `done` does not mean its code landed anywhere, so a story blocked by a
finished story still roots on that story's tip.

**O3 — `am run --milestone` has a dry run that writes nothing.** `--card` and
`--milestone` are mutually exclusive and one is required. `--milestone` takes a
card id or a title substring (O1). `--dry-run` prints `{levels: [{level,
stories: [{story, title, root, subtasks: [{id, title, status, branch, base}]}]}],
already_done: [...]}` and performs no write of any kind: no run directory, no
store, no worktree, no board write, no fetch, no prune. The `base` column is the
review artifact: a story's first subtask should root on its blocker's tip, and a
blocked story rooted on the base branch means a missing edge.

**O4 — One driver drives a subtask.** The per-subtask half of `run_card`
(resolve the runner, build the gate context, call `engine.run_subtask`, collect
the runner's out-of-band warnings) becomes a function that takes an existing run
and store. `run_card` calls it and keeps its envelope, its exit codes and every
existing test unchanged. The driver holds no module-level mutable state.

**O5 — Status rolls up the ancestors.** `rollup.set_status` keeps its name and
signature, so `task.yaml` is unchanged, and after writing the card it walks to
the root recomputing each ancestor from its real children *by progress*: all
`todo` is `todo`, all `done` is `done`, anything else is `in_progress`, with
`blocked` treated as `todo`. It rereads each parent fresh, writes only where the
computed status differs, and always walks to the root even past an unchanged
parent, so a run that was interrupted last time repairs a stale grandparent this
time. Depth is capped at 16 against a corrupted parent chain. It returns the
card's own result plus `rolled_up`, the list of ancestors it changed.

**O6 — The runner is sequential, and relaunching is resuming.** `run_milestone`
resolves the milestone, builds the census, refuses cycles and two-blocker
stories, and computes levels and bases, all *before any write*. It then does
once per run: `git fetch origin` when an `origin` remote exists (a repo with none
skips it) and `git worktree prune`. It records one `Run` (workflow `milestone`)
with every pending story and remaining subtask as `pending` so `status` shows the
plan, then walks levels in order, stories within a level in census order,
subtasks strictly in order, skipping any already `done`. The first subtask that
does not finish `done`, or any exception from the driver, records the subtask,
story and run `escalated`, stops scheduling, and reports `{escalated, level,
story, subtask, failed_phase, detail}`. A story with nothing left to run but
whose card status is stale is re-rolled up through one of its `done` subtasks.

Relaunching the same command continues a partly done milestone: `done` cards are
skipped by board status, a killed subtask re-enters through `worktree.ensure`'s
idempotence, `plan_check`'s skip and the Plan-Hash re-entrancy already built. A
milestone-aware `am resume <run-id>` is the follow-up's.

Without Integrate, a clean run leaves each story as a stack of local branches and
reports every story's tip. For a linear milestone the last tip contains
everything, and merging it is the human's.

**O7 — A blocked coder stops the subtask.** Nothing reads
`ImplementResult.blocked`, so a coder that stopped because the baseline suite was
already red, or because the plan hash could not be computed, is followed by a
`review` phase anyway, and can end `done` on partial work if that work is green.
`task.js` stopped on it. A new `implement_blocked_gate` returns a `blocked:
implement` verdict carrying `blocked_reason` when `blocked` is true, and is added
to the `implement` phase's `gates`. It is not retryable: the coder said it cannot
proceed, and re-dispatching the same brief repeats the answer.

**O8 — Every story ends in a test through the production path.** The default
suite drives `run_milestone` and `am run --milestone` with a fake `claude`
executable first on `PATH`. The rule from milestone 2 stands: **the fake must
never know more than the brief tells it.** It may not compute the plan hash,
commit the spec or plan, or learn the result path any way but the prompt text.
One opt-in test (`pytest -m e2e`) runs a two-story milestone against a real
`claude -p`. It is a human step, since it spends money.

## 3. Acceptance

1. `am run --milestone <m> --branch-prefix p --dry-run` prints the levels and
   the base column for a real board and writes nothing (asserted: no run
   directory appears).
2. The fake-claude milestone test drives at least three stories through the
   production wiring: every subtask `done`, story and milestone cards `done` by
   rollup, and every subtask branch contains its predecessor's commit.
3. An escalation in one story stops the run before the next story starts.
4. A coder reporting `blocked` ends its subtask `escalated` at `implement`, and
   `review` never runs.
5. Relaunching after an escalation resumes: cards already `done` are skipped.
6. By hand, after the milestone: a real two-story milestone against a real
   harness reaches `done` on the board.

## 4. Deferred to the follow-up milestone (found now, not cut yet)

- **Parallel stories.** `store.open_db` opens one connection with the default
  `check_same_thread=True`, so a worker thread using it raises; nothing locks it.
  `Journal.append` computes the next sequence number by re-reading the *whole*
  journal on every line (quadratic, and two threads reading at once take the same
  number). Needs a lock, a cached sequence, and a stress test. Also: a repo lock
  around `git worktree add`, a board lock around read-modify-write rollups, a
  deliberate concurrent-`brd update` test (main-spec §17), a cooperative stop
  polled between phases, a `stopped` status, and `max_concurrent_stories`
  defaulting to 4 as the main spec says (it is 1 today).
- **Integrate.** Port `integrate.mjs` with its no-`origin` fallback, a `resolver`
  role and `ResolveResult`, and verify the resolution with `verify.run_suite`
  rather than a model call. Merge order is the levels over *all* stories, done or
  not.
- **Operator commands.** A milestone-aware `resume` (`select_resumable` refuses
  any run with several started subtasks), plus `watch`, `retry` and `cancel`.
- **Review counts measured by `git`.** `ReviewResult`'s `porcelain`,
  `commit_count` and `tagged_count` are measurements an agent is asked to run and
  report verbatim. A deterministic step can take them with `git` and remove the
  agent's chance to misreport.
- **Verification discovery.** `--verify` is required here; the old pipeline
  discovered the commands once per run.
