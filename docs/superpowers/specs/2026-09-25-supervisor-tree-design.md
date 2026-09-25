# The supervisor tree — design addendum

Date: 2026-09-25
Extends: `2026-09-25-pygents-engine-design.md` (G1, G5, G8), `2026-09-25-integrate-design.md` (I2, I3),
`2026-09-24-parallel-stories-design.md` (P4 stop), `2026-09-24-orchestration-design.md` (O2 roots, O3 dry run)
Status: milestone 7 scope; decisions T1–T10. Builds on milestone 6 (pygents engine) merged.

## 1. Why this shape

`orchestrate.run_milestone` runs a milestone level by level on a thread pool. Three limits:

- **Levels are barriers.** A story waits for every story of the previous level, not just its own
  blocker: in `A ███ / B █████████ → C`, C (blocked by A) idles until B finishes.
- **A story may have only one blocker in the milestone.** Two or more is refused (exit 3), because
  a stack roots on one branch and nothing builds a merged base. Users are told to chain stories,
  which costs parallelism.
- **`am resume` is not milestone-aware.** A killed or stopped milestone can only be relaunched as a
  new run.

Milestone 6 left the subtask engine on pygents with checkpoints and a temporary stop bridge (G8)
read from `orchestrate`'s `threading.Event`. This milestone replaces the thread pool with one event
loop: stories become a grafo tree (the user's DAG executor), each story's subtasks run on M6's
pygents subtask agents, the stop becomes `pause()`, stories with several blockers get a merged
base, and `am resume` continues a whole milestone.

## 2. What was found

- **grafo 0.3.4** (`paulomtts/grafo`, `main` at `f50428e`, same as PyPI): `Node(coroutine, uuid)`,
  `await parent.connect(child, forward="<param>")`, `TreeExecutor(uuid, roots).run()`.
  - A node is queued only when **all** its parents' events are set (`executor.py`, the worker's
    child loop), and a parent's event is set only on success (`components.py` `_run`, inside the
    `try`). So a node never starts after a failed parent, and waits for every parent.
  - `forward` puts the parent's output into the child's kwargs under the given name; a child
    coroutine taking `**kwargs` accepts any name.
  - On any exception the executor records it in `executor.errors`, sets its stop, and workers stop
    taking new nodes; **running nodes are not interrupted**. Nodes never started are not recorded.
  - Workers scale with the queue; there is **no concurrency parameter** in 0.3.4.
  - `Node(timeout=60.0)` is the **default**, enforced with `asyncio.wait_for` around the node's
    coroutine: a lane left at the default would be cancelled after one minute.
  - Its logger (`grafo`) writes to stderr at WARNING+, and logs each node error with a traceback.
  - 0.3.4 declares dev tools (`pytest`, `ruff`, `mkdocs*`, `bump2version`) as runtime
    dependencies; a grafo release that moves them to dependency groups is a prerequisite (T10).
- A sketch against grafo 0.3.4 confirmed: a story blocked by A and B started only after both,
  receiving both tips as kwargs; a story blocked only by A started as soon as A finished while B
  still ran; when B raised, its dependent never started.
- pygents' `pause()` takes effect between turns; M6's subtask agent turns are phases, so a pause
  parks a subtask at a phase boundary. A story's or the milestone's "turns" would be whole
  subtasks or lanes, too coarse to park at; sketches of story and milestone agents added
  ceremony (a module-level registry of live agents; a re-entry loop around `run()` because an
  agent's run ends whenever its queue is momentarily empty) without behaviour.
- `integration._resolve_conflict` already drives `integrate` for one conflicting tip under a
  synthetic `integrate` story, with `extra_context` `merge_tip`, `conflict_files` and the gate
  context; `steps/integrate.merge_tip` is idempotent (`already_merged`) and raises
  `MergeInProgressError` on an unfinished merge.

## 3. Decisions

**T1 — Stories are a grafo tree.** One `Node(coroutine=lane(story), uuid=story.id, timeout=None)` per
story of the milestone (done ones included; `timeout=None` is required, see section 2), one
edge per in-milestone blocker, `forward=f"tip_{short_id(blocker)}"`. Roots are the stories with no
in-milestone blocker. `TreeExecutor(uuid=run_id, roots=...).run()` schedules them: a story starts
the moment all its blockers succeeded, never after a failed one. Levels stop being barriers; they
remain as *waves* for the dry run and reports.

**T2 — Only the subtask is a pygents Agent.** The supervisor and the lanes are plain async
functions (grafo node coroutines). pygents stays where its turns are phases.

**T3 — One event loop per `am` process.** `run_milestone` keeps its synchronous signature and calls
`asyncio.run(supervise(...))`. `ThreadPoolExecutor` and `RunStop` are deleted. Blocking work still
goes through M6's `to_thread` bridge, so `board.WRITE_LOCK` and the repo locks stay valid.

**T4 — `--max-concurrent` is a semaphore inside the lane.** A lane takes a slot only after its
blockers are done (grafo guarantees that by starting it then), so a waiting story never holds a
slot and chains cannot deadlock. Default 4, meaning unchanged: stories running at once.

**T5 — The stop is layered.** `StopSignal` (new, `runtime/stop.py`): `trigger(story_id)` records
the first caller as primary and pauses every registered subtask agent; `register(agent)` pauses at
once if already triggered. M6's subtask agent registers on creation; an `ON_PAUSE` hook saves the
`parked` checkpoint and raises `Parked`. grafo's own stop (any lane raising) keeps new stories from
starting. M6's `BEFORE_TURN` stop bridge and the `should_stop` parameter are deleted.

**T6 — Lanes raise to fail.** A lane returns its tip on success. It raises `LaneEscalated(outcome)`
on escalation (after `stop.trigger`) and `LaneStopped(outcome)` when parked or when it sees the
stop before a subtask, so grafo never releases dependents of a lane that did not finish clean.
Outcomes are read after `run()`: output → `done`; `LaneEscalated` → `escalated` (first in
`executor.errors` is primary, the rest `also_escalated`); `LaneStopped` → `stopped`; any other
exception → `escalated` with `"<Type>: <msg>"`; no output and no error → `pending`. The `grafo`
logger is set to CRITICAL for the duration of `supervise`.

**T7 — Two or more blockers build a merged base.** Section 5.

**T8 — `am resume <run-id>` continues a milestone by re-deriving.** No story or milestone state is
checkpointed. Section 6.

**T9 — Replace in place.** No side-by-side scheduler. `test_orchestrate.py` and the parallel e2e
tests are the oracle; assertions about level barriers become dataflow assertions, the two-blocker
refusal tests become merged-base tests, everything else passes unchanged.

**T10 — grafo is a runtime dependency from the release that empties its runtime dependencies.**
`grafo>=0.3.5` (the dependency-groups release), imported only by `orchestrate.py`.

## 4. Architecture

```
 am run --milestone / am resume <run-id>                        (CLI surface unchanged)
   │
   ▼
 orchestrate.run_milestone(...)  ── asyncio.run(supervise(...)) ───────────── one loop
   │ census → dag.assert_no_blocker_cycles → dag.story_root(...) → RootPlan per story
   │ nodes = {s.id: Node(coroutine=lane(s), uuid=s.id, timeout=None)}
   │ for s, for b in in-milestone blockers of s: await nodes[b].connect(nodes[s], forward=f"tip_{b8}")
   │ await TreeExecutor(uuid=run_id, roots=[unblocked]).run()
   │ outcomes ← node outputs + executor.errors                  (T6)
   │ clean? → integration.integrate_milestone via asyncio.to_thread   (M5, unchanged)
   ▼
 lane(story)(**tips)                                             (a grafo node coroutine)
   nothing left to run → return existing tip
   async with slots:                                             Semaphore(max_concurrent)
     root = base_branch | the one tip | await bases.build(story, tips, ...)   (section 5)
     for subtask in remaining (stacked):
       stop.triggered → raise LaneStopped
       summary = await cli.drive_subtask_async(..., stop=stop, resume_from=<open checkpoint>)
       escalated → stop.trigger(story.id); raise LaneEscalated
       stopped   → raise LaneStopped
   return tip
```

| File | Change |
|---|---|
| `pyproject.toml` | + `grafo>=0.3.5` |
| `runtime/stop.py` | NEW `StopSignal` |
| `runtime/engine.py` | `run_subtask_async(...)` public (the coroutine `run_subtask` wraps); `stop: StopSignal \| None` replaces `should_stop` |
| `runtime/checkpoint.py` | `ON_PAUSE` hook saves `parked`, raises `Parked`; the `BEFORE_TURN` stop branch is removed (it still saves `turn`) |
| `cli.py` | `drive_subtask_async(...)`; `resume_run` dispatches on the run's workflow (`task` \| `milestone`) |
| `dag.py` | `story_root` returns a `RootPlan`; two or more blockers allowed; cycles still refused |
| `bases.py` | NEW merged-base builder |
| `orchestrate.py` | `supervise`, `lane`, outcome collection; thread pool, `RunStop`, level loop deleted |
| `cli.py` dry run | merged bases in the `root` column (`merged_from`) |

`RootPlan` (frozen dataclass in `dag.py`): `kind: Literal["base", "tip", "merged"]`, `branch: str`
(the branch the story's first subtask builds on), `blockers: tuple[str, ...]` (in census order).
For `merged`, `branch` is `<prefix>/base-<short id of the story>`.

## 5. Merged bases

For a story with two or more in-milestone blockers, all of them `done`:

```
 bases.build(story, tips, *, repo_dir, branch_prefix, commands, allow_no_verification, store, run_id, runner_factory, stop)
   branch   = <prefix>/base-<short id of story>, worktree .claude/worktrees/<branch>
   first    = tips of the blockers in census order; branch cut from tip[0] (worktree.ensure, to_thread)
   for tip in tips[1:]:
     r = merge_tip(repo_dir, worktree, branch, tip[0], tip)        (to_thread)
     r.already_merged  → continue                                  (resume and relaunch reuse the base)
     r.conflict        → run_subtask_async(INTEGRATE, extra_context={merge_tip, conflict_files,
                                          **gate_context}, stop=stop)  under the synthetic story
     MergeInProgressError → escalate: "an earlier conflict in <worktree> was never resolved; finish
                            the merge there by hand, then resume"
   verify.run_suite(commands, worktree) once, judged by verification_gate + verification_passed_gate
   return BaseResult(branch, merged=[...], resolved=[...])
```

- Journal: a synthetic story `bases` ("Merged bases"), recorded once per run when first needed,
  and one synthetic subtask per story with card id `base-<story id>`, so its checkpoints never mix
  with an Integrate resolver working on the same story in the same run. `am status` shows the
  merge, resolve and verify rows there.
- Any failure (resolver escalated or parked, verification failed, merge in progress) makes the lane
  fail: escalation → `LaneEscalated` with `failed_phase: "base"`, `subtask: null`; parked →
  `LaneStopped`. The base branch is left as it is for a human.
- One blocker stays the fast path: root directly on that tip, no base branch, no extra verify.
- Integrate later finds the story's tip already containing its blockers' tips; those merges are
  no-ops. Integrate itself is unchanged.
- The base branch never moves the milestone's base branch and is never pushed.

## 6. Milestone-wide resume

```
 am resume <run-id> [--verify ...]          (the suite is not recorded, as today)
   run row: workflow 'milestone' → branch_prefix, base_branch, max_concurrent_stories, repo
     'done' → refuse (exit 3): "run <id> finished; start new work with am run --milestone"
   refresh_git (fetch origin when present, worktree prune)
   census fresh from the board; done cards skipped
   open checkpoints: newest turn/parked/escalated row per open subtask of THIS run
     (subtasks and base-<story> resolvers); any digest ≠ TASK/INTEGRATE digest → refuse the whole
     resume, exit 3, nothing written
   orphan attempts → harness_error (M6)
   rows: stopped/escalated/started → started; run → started
   supervise(...) as a fresh run, same run id; each subtask with an open checkpoint continues from
   it; bases re-derive (section 5); Integrate runs when everything is clean
```

- Resume is strict (a stale digest refuses the whole milestone); relaunch (`am run --milestone`)
  stays lenient per card, as M6 defined.
- An escalated subtask resumes at its failed phase with its loop count.
- The report is a fresh run's shape plus `resumed: true`; `completed` lists what finished in this
  invocation.
- `am resume` on a `task` run keeps M6's behaviour.
- Not detected: resuming a run whose original process is still alive ("one `am` process per
  repository" stays a stated limit).

## 7. Lane states and the stop

```
 blocker failed / grafo stopped before start ─────────────────────────────► pending
 started by grafo ─► waiting for a slot ─┬─ stop triggered ───────────────► stopped (LaneStopped)
                                         └─ slot ─► running
 running ─► all subtasks done ────────────────────────────────────────────► done (returns tip)
         ─► subtask escalated ─► stop.trigger ────────────────────────────► escalated (LaneEscalated)
         ─► parked / stop seen before a subtask ──────────────────────────► stopped (LaneStopped)
 finally: slot released
```

The first escalation: running subtasks park at their next phase boundary (a phase in flight
finishes); no new story starts (grafo); a lane between subtasks ends `stopped`; a base under
construction parks at its resolver or finishes its atomic merge step; Integrate does not run.
`BaseException` (Ctrl-C, SIGTERM): `asyncio.run` cancels everything, M6's bridge kills running
`claude -p` trees, the latest checkpoints stand for `am resume`.

## 8. Reports

| Where | Change (additive) |
|---|---|
| run `data` | `levels` stays (waves). New `bases: [{"story", "branch", "blockers"}]` when non-empty. `resumed: true` on resume. |
| escalation payload | `level` is the story's wave; `failed_phase` may be `"base"` with `subtask: null`; `also_escalated` and `stopped` unchanged. |
| `--dry-run` | story rows gain `"merged_from": [ids]` when their root is a merged base, and `root` names it; two or more blockers are no longer refused; cycles still are; `concurrent` stays `min(len(wave), max_concurrent)`. |

## 9. Testing

No test sleeps to prove ordering; fake drivers block on `asyncio.Event`s.

- Dataflow: B's fake driver blocks; C (blocked by A) is observed started while B is blocked.
- Multi-blocker: C blocked by A and B starts only after both, and its fake driver receives both
  tips; a failed B leaves C `pending`.
- Slots: with `max_concurrent=2` and five ready stories, at most two lanes run at once; a chain
  A←B←C with `max_concurrent=1` finishes (no deadlock).
- Stop: with real M6 subtask agents and a fake runner, one lane escalating parks the other's running
  subtask before its next phase (a `parked` checkpoint exists), a waiting story ends `pending`, two
  same-tick escalations give one primary and one `also_escalated`.
- Merged bases on real temporary git repos (M5's fixtures): clean two-tip merge; conflict resolved
  by the fake-claude resolver; resolver failure escalates `base`; `MergeInProgressError`; resume
  reuses the base without merging again; the milestone's base branch never moves.
- Resume, end to end under the fake claude: a `BaseException` mid-milestone then `am resume` → same
  run id, no finished phase re-dispatched, parked and escalated subtasks continue; stale digest →
  exit 3; resuming a `done` run → exit 3; a `task` run's resume unchanged.
- Everything in `test_orchestrate.py` and `tests/e2e/test_parallel_milestone.py` that does not
  assert a level barrier or the two-blocker refusal passes unchanged.

## 10. Deferred

- Verification discovery (orchestrator.js's Detect); live `am pause`/`am cancel`; `watch`/`retry`.
- More than one `am` process per repository.
- A grafo `max_workers` option (would replace T4's semaphore).
- leave-me-alone's multi-blocker support (issue `a676f178` in that project's board).
