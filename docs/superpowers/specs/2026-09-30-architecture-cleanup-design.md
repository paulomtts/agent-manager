# Architecture cleanup — design addendum

Date: 2026-09-30
Extends: `2026-09-23-agent-manager-design.md` (§4 module layout), `2026-09-24-orchestration-design.md`
(O4 orchestrate holds no state), `2026-09-25-integrate-design.md` (I2, I3), `2026-09-25-pygents-engine-design.md`
(G1, G5, G8), `2026-09-25-supervisor-tree-design.md` (T1–T10), `2026-09-27-live-control-design.md` (C1–C12).
Status: proposed. Decisions S1–S11. No milestone scope assigned yet — see §7 for a suggested split.

## 1. Why this shape

An architecture audit (full report: internal, not checked in) read `cli.py`, `orchestrate.py`,
`store.py`, `dispatch.py`, `steps/reducers.py`, `runtime/*`, `bases.py`, `integration.py`,
`board.py`, `paths.py`, and both `errors.py` modules against their own docstrings and the specs
above. Two conclusions:

- The parts that do dangerous work — durable writes, gate judgement, process launch — hold their
  boundaries exactly as designed. `steps/reducers.py` is genuinely pure (§4 there); `dispatch.py`
  never imports `subprocess`; `store.py`'s journal-then-row write order is correct and locked.
  Nothing here proposes touching those modules' internals.
- The drift is at the seams between modules. Each milestone since M1 added a card-sized piece of
  behaviour and reached for the fastest working import, the fastest working test seam, or a
  freshly hand-written copy of logic that already existed elsewhere. None of those decisions was
  wrong in isolation; nine of them together are `orchestrate.lane`.

This addendum proposes eleven fixes for that drift. Two (S2's `detail` column, S2's
`milestone_id` column) are bug fixes with a concrete failure case. The rest are structural and
change no observable CLI behaviour: same envelope, same exit codes, same journal format (with one
additive column), same test oracles renamed rather than rewritten where practical.

## 2. What was found

Cited as `<file>:<line>` against `master` at the time of the audit; re-read the code as built before
implementing (per this project's standing rule — line numbers drift).

- **S1 — `cli.py` is a dependency hub, not a thin shell.** `bases.py:34`, `integration.py:29`,
  and `orchestrate.py:54` all `from agent_manager import cli` at module load, reaching for
  `cli.worktree_for`, `cli.gate_context`, `cli.RunnerFactory`/`cli.default_runner_factory`,
  `cli.mint_run_id`, `cli.resolve_repo_dir`, `cli.orphan_attempts`, `cli.continuable_checkpoint`,
  and the `cli.UnknownRunError`/`NotResumableError`/`CheckpointMismatchError` types. `cli.py` does
  deferred in-function imports of `orchestrate` and `integration` (`cli.py:895,1137,1460`) to
  break the resulting cycle. `cli.py`'s own docstring (`cli.py:3-8`) says it "decides nothing a
  collaborator already decides"; `run_card` (729-825), `_resume_from_checkpoint` (1324-1404),
  `checkpoint_resume_phase` (473-514), and `dry_run_payload` (861-947, which computes levels,
  bases, roots, and the Integrate plan) are business logic that does not belong to a Typer app.
- **S2 — Two data-loss / identity bugs.** `models.py:99` declares `PhaseRun.detail`; `dispatch.py`
  and `walk.py:401-414` write a failure reason into it; the `phases` table (`store.py:63-74`) has
  no `detail` column, `_write_phase_row` (742-771) never writes it, `load_run` (473-479) never
  reads it — `am status` can show `failed` but never why, and `rebuild_from_journal` throws the
  reason away on rebuild. Separately, `models.Run` (and the `runs` table, `store.py:29-38`) never
  records the milestone card it ran against; `find_run_milestone` (`orchestrate.py:532-548`)
  recovers it by parsing the run-id string and matching an 8-character short id, so two milestone
  cards that collide on that short id make earlier runs unresumable.
- **S3 — Gate evaluation exists twice, with different failure semantics.** `runtime/walk.py:277-330`
  (`_gate_values`/`_evaluate_gates`/`_render_verdict`) and `dispatch.py:262-346`
  (`gate_values`/`evaluate_gates`/`_render_verdict`) both bind gate inputs by name and treat
  `None`/`warn`/mapping alike, but a *raising* gate is a plain recorded `failed` on the walk path
  (caught by `run_one_step`'s catch-all, `walk.py:405-414`) and a `fatal` `Verdict("gate_failed")`
  on the dispatch path (`dispatch.py:283-346`, never retried).
- **S4 — Conflict resolution exists three times.** `integration._resolve_conflict`
  (`integration.py:123-170`) is synchronous and calls `runtime_engine.run_subtask`, which does its
  own `asyncio.run` — no `StopSignal` reaches it. `bases._resolve_conflict` (`bases.py:155-209`) is
  async and stop-aware, and `bases.py:14-16` says outright that it "mirrors" the other.
  `_resolver_detail` is duplicated verbatim (`integration.py:173`, `bases.py:212`);
  `integration._final_verification` (100) and `bases._verify` (131) are the same function.
  `integrate_milestone` runs after `asyncio.run(supervise(...))` returns (`orchestrate.py:1403`),
  outside the run's event loop, so Integrate is the one phase a pause or cancel cannot reach.
- **S5 — `orchestrate.lane` hand-writes one state transition nine times.**
  `orchestrate.py:877-1096` (222 lines): `store.record_story(...status=X)` at 984, 1004, 1007,
  1015, 1023, 1055, 1066, 1077, 1087; the subtask equivalent at 1021, 1054, 1065, 1075, 1083.
  Outcome fields (`completed`, `warnings`, `built`) flow through closure-captured mutable lists
  read by an inner `outcome()` builder (961-975). `base_only_lane` (816-874) repeats the same
  try/except/`stop.trigger`/raise shape.
- **S6 — grafo's workarounds are scattered rather than owned.** Merged-root stories bypass grafo's
  edges and hand-roll `asyncio.Event` waiting because of a starvation bug (`blocker_tips`,
  `orchestrate.py:775-798`); `run_until_killed` (1099-1130) works around a confirmed grafo hang
  (`BaseException`, `faulthandler`-verified per its own comment); `supervise` silences grafo's
  logger for its duration (1171-1173); `Node(timeout=None)` is required everywhere to defeat a
  60s default (1218); `run(**tips)` ignores any forwarded kwargs since tips are read from
  `plan.tips` (1185). grafo stays — it's the right call for T1's "stories are a tree, scheduled by
  their dependency edges" model, and re-deriving that scheduling by hand would just move the
  starvation/hang-shaped bugs somewhere with less test coverage than an upstream library has. The
  problem isn't grafo; it's that its five workarounds are inlined at their call sites instead of
  named as what they are.
- **S7 — No shared error hierarchy; `HANDLED` includes bare `ValueError`.** `cli.py:977-982` turns
  every `ValueError` into an `ok: false` envelope at exit 3, because `dag.short_id` raises one for
  a bad id — but so does pydantic coercion, `names.index(...)` (`runtime/compile.py:71,191`), and
  `store.record_run` itself (`store.py:591`). The docstring at that point promises anything else
  "crashes loudly with its stack intact"; a genuine bug currently does not. `CheckpointMismatchError`
  (`cli.py:129`) uses multiple inheritance (`CliError`, `runtime_engine.CheckpointMismatch`) to fit
  into that tuple. `errors.py` and `runtime/errors.py` are split purely for import-order reasons
  (`errors.py:3-4`), and five of the project's 14 exception types are control flow, not errors
  (`Parked`, `Escalated`, `LaneEscalated`, `LaneStopped`, `_GateFailed`).
- **S8 — `runtime/` types collapse to `Any` at the seam most likely to receive a test double.**
  `runtime/engine.py:57-72` (`run_subtask(store: Any, subtask: Any, card: Any = None, ...)`),
  `runtime/state.py:21-28` (`RunDeps`, all `Any`), `bridge.call_agent(runner: Callable[[Any, Any,
  Any], Any], ...)`. Warnings travel as an ad-hoc attribute (`getattr(runner, "warnings", [])`,
  `cli.py:679`) instead of a typed result.
- **S9 — `paths.py` mkdirs on every call.** `paths.py:14-40`; `Journal.__init__` calls `run_dir`
  (`store.py:194`), so opening a `Store` mints a run directory as a side effect. Four places
  (`store.py:416-423`, `cli.py:380-384,1264-1269`, `orchestrate.py:504-509`) carry comments
  explaining that a read-only command must use `open_db`/`load_run` instead of `Store` specifically
  to avoid this.
- **S10 — Test seams are monkeypatched module attributes, not injected collaborators.**
  Comments say so directly: `cli.py:1134`, `orchestrate.py:750-751,1399-1400,491` explain that a
  name is read off its module *at call time* so a test can patch it there. `run_milestone` injects
  its `driver` and `runner_factory` — real injection — but not `bases.build`,
  `integration.integrate_milestone`, `worktree.run_git`, or `board.*`. `tests/test_cli.py` (5080
  lines) and `tests/test_orchestrate.py` (4016 lines) carry 261 `monkeypatch` calls between them,
  and an autouse fixture (`test_orchestrate.py:768-773`) patches `integrate_milestone` for every
  test, so no test in that file runs the real Integrate path by default.
- **S11 — Smaller items**, folded into this cleanup rather than given their own decision:
  `steps/reducers.py:89-100`'s permanent camelCase/snake_case dual read; `orchestrate.py:76-85`
  parsing `"stopped before <phase>"` back out of free text because `SubtaskSummary` has no field
  for it; `latest_open_checkpoint` (`store.py:922-944`) picking "newest" by wall clock across every
  run in a store otherwise scoped to one; `rebuild_from_journal` (`store.py:970-987`) committing
  per row instead of in one transaction.

## 3. Decisions

**S1 — `cli.py` sheds its collaborator role.** New module `runs.py` (no Typer import) takes
`worktree_for`, `gate_context`, `RunnerFactory`, `mint_run_id`, `resolve_repo_dir`,
`orphan_attempts`, `continuable_checkpoint`, `select_resumable`, and the
`UnknownRunError`/`NotResumableError`/`CheckpointMismatchError` types. `default_runner_factory`
**stays in `cli.py`** — see the exemption below. `bases.py`, `integration.py`,
and `orchestrate.py` import `runs`, never `cli`, for every name this decision actually moves. `cli.py`
imports `runs` like everyone else and keeps only Typer command bodies plus the CLI-specific
envelope/exit-code glue (§4 there). The three deferred imports at `cli.py:895,1137,1460` are deleted
— nothing downstream of `runs` imports `cli` for the names this decision moves.

**Status (as actually landed): partial, by design, not a gap.** `orchestrate.py` still imports
`cli` as a module today, for `default_runner_factory` and `drive_subtask_async` (the
exemption below), and for four names this decision never scoped to move in the first place --
`refuse_claimed`, `run_lease`, `SubtaskDrive`, `HANDLED` -- which stayed exactly where S1 left
them. `cli.py` keeps one deferred `from agent_manager import orchestrate` for the same reason the
exemption gives: binding either name at import time, across a real circular import, breaks. Both
ends document this inline (`orchestrate.py`'s own module docstring, `cli.py`'s call site) and the
module loads cleanly in either import order
(`tests/test_cli.py::test_cli_and_orchestrate_import_cleanly_in_either_order`).
This is a deliberate, stable exception, not unfinished work -- the "after" diagram below describes
the target shape for the names S1 actually names, not a claim that every `cli` reference from
`orchestrate.py` is gone. Business logic that survives in `cli.py`
today (`dry_run_payload`'s level/base/root computation) moves to `runs.py` too, since it is a pure
function of the census and belongs next to `dag`'s other pure derivations, not the Typer app.

**`default_runner_factory` exemption (found during implementation, not in the original audit).**
`default_runner_factory`'s body calls the bare name `run_direct`, resolved against whatever module
defines the function at call time. Three unmarked, default-suite e2e tests
(`tests/e2e/test_live_control.py:179`, `tests/e2e/test_milestone_run.py:294,421`,
`tests/e2e/test_milestone_resume.py:241`) do `monkeypatch.setattr(cli, "run_direct", ...)`
specifically to intercept it. Moving the function into `runs.py` would silently read
`runs.run_direct` instead and break that interception with no test file changing — violating this
story's own "no test changes" constraint via the very move it asks for. `default_runner_factory`
therefore stays in `cli.py`, `import`ing nothing new; every other name in this decision moves as
written. `RunnerFactory` (the `Protocol` it satisfies) still moves to `runs.py` — only the
production factory function is exempt.
**S2 — Two schema additions.** `phases` gains a `detail TEXT` column; `_write_phase_row` writes
`phase.detail`, `load_run` reads it back into `PhaseRun.detail`. `runs` gains a `milestone_id TEXT`
column (nullable, for `task`-workflow runs); `run_milestone` stamps it from the census's card id at
creation; `find_run_milestone` reads the column and only falls back to the short-id parse for runs
written before this migration. Both are `ALTER TABLE ... ADD COLUMN`, guarded in `open_db` the way
the existing `CREATE TABLE IF NOT EXISTS` schema already is — no destructive migration, and old
journals replay unchanged since the journal's own JSON already carries both fields (`store.py`'s
"journal wins" contract means the column was always resurrectable; this decision just stops
throwing it away on projection rebuild).

**S3 — One gate evaluator.** `walk.py` keeps the implementation; `dispatch.py`'s
`gate_values`/`evaluate_gates`/`_render_verdict`/`_render_error` are deleted and replaced with calls
into `walk`'s versions. The shared function returns a `GateVerdict` (new, `runtime/walk.py`):
`kind: Literal["pass", "warn", "fail", "broken"]`, `detail: dict | None`. `run_one_step` maps
`"broken"` to the same catch-all path it uses today (still a recorded `failed`, not silently
swallowed — walk.py's semantics win, since a phase step failing is already the correctly
conservative choice); `AgentRunner` maps `"broken"` to `Verdict("gate_failed", fatal=True)` — dispatch's
semantics win there, since a broken gate on an agent phase should not retry. The two phase kinds
keep their existing, deliberate handling of a broken gate; what stops existing is two
implementations of *evaluating* the gate that happen to agree by accident today and could silently
diverge on the next edit.

**S4 — One resolver, async and stop-aware, called from inside the run's event loop.** New
`resolver.py` takes `_resolve_conflict` (bases' async, stop-aware version), `_resolver_detail`, and
`_final_verification`/`_verify` (already identical). `bases.build` and `integration.integrate_milestone`
both call `resolver.resolve_conflict(...)`. `integrate_milestone` becomes `async def` and gains a
`stop: StopSignal | None` parameter; `run_milestone` calls it with `await
integrate_milestone(...)` inside the same `asyncio.run(supervise(...))` block that runs everything
else, immediately after `supervise` returns clean, rather than as a separate synchronous call
afterward. This makes a pause or cancel raised during Integrate behave exactly like one raised
during any lane — `StopSignal.trigger` reaches it, because it is now registered the same way a
lane's subtask agent is.

**S5 — `StoryRecorder` owns every state transition `lane` currently writes by hand.** New class in
`orchestrate.py` (small enough not to warrant its own module): `started()`,
`subtask_done(subtask_id, tip)`, `stopped(subtask_id, before_phase)`,
`escalated(subtask_id, phase, detail)`, each doing exactly one `store.record_story`/
`store.record_subtask` pair and appending to the outcome-builder's lists internally instead of via
closure capture. `lane` and `base_only_lane` become call sequences against one `StoryRecorder`
instance per lane invocation; the 222-line function should come down to roughly a quarter of that
once the nine inlined writes collapse to four named calls. No change to what gets recorded — this
is a mechanical extraction, verified by the existing `test_orchestrate.py` assertions on `am
status`/journal shape.

**S6 — Keep grafo; name and consolidate its workarounds instead of inlining them.** grafo remains
the scheduler for T1's story tree; nothing about `Node`/`TreeExecutor`/`connect` changes. What
changes is that every one of its five known rough edges gets one owned home instead of a comment at
each call site: a new `runtime/grafo_compat.py` (or a clearly marked section at the top of
`orchestrate.py`, whichever reads better once written) holds `run_until_killed` (the confirmed-hang
workaround), a `SILENCED_LOGGER` contextmanager (replacing the inline silence/restore in
`supervise`), and a documented constant `GRAFO_NODE_TIMEOUT = None` referenced everywhere a `Node`
is built instead of the literal repeated at each call site. The merged-root `asyncio.Event` wait
(`blocker_tips`) stays exactly as it is — it's already the correct move for the one case (2+
blockers) grafo's edges can't express, not something to unify away — but its docstring gets a
one-line pointer to `grafo_compat.py` so a future reader finds all of grafo's known limits in one
place instead of rediscovering them by grep. If a grafo release lands with a `max_workers` option
or resolves the starvation/hang issues (both already flagged as deferred follow-ups in the
supervisor-tree spec, §10), `grafo_compat.py` is exactly the one place that shrinks.

**S7 — One error hierarchy, and a narrower catch in `cli.py`.** `errors.py` and `runtime/errors.py`
merge into one `errors.py` with no other in-repo imports (resolving the reason they were split).
`dag.short_id`'s failure becomes a dedicated `InvalidCardIdError(CliError)`; `cli.py`'s `HANDLED`
tuple catches that instead of bare `ValueError`, so a `ValueError` from anywhere else in the call
graph propagates with its traceback, matching the docstring's existing promise.
`CheckpointMismatchError` drops the multiple inheritance from `runtime_engine.CheckpointMismatch`
and instead wraps it (`CheckpointMismatchError(CliError)` with a `.cause` attribute), since a CLI
error type representing an engine error by inheriting from both was the symptom, not a pattern to
keep. The five control-flow exceptions (`Parked`, `Escalated`, `LaneEscalated`, `LaneStopped`,
`_GateFailed`) get a shared no-op marker base (`class _Signal(BaseException): pass`) purely so a
reader can `except _Signal` to see every non-error raise in one place; their existing individual
handling is unchanged.

**S8 — Type the runtime seam.** `runtime/state.py`'s `RunDeps` becomes a `Protocol` naming the
store surface the runtime actually calls (`record_phase`, `record_subtask`, `save_checkpoint`,
`run_id`, and whatever `grep -n "store\." runtime/*.py` turns up as the real call set — enumerate
from the code, not from memory, since this is exactly the kind of drift this addendum exists to
catch). `run_subtask`'s `store`, `subtask`, `card`, `parent_story`, and `agent_runner` parameters
take that protocol and the existing `models` types instead of `Any`. `AgentRunner.__call__` returns
a small `RunnerResult(tip: str, warnings: list[str])` instead of stashing `warnings` as an ad-hoc
attribute; `cli.py:679`'s `getattr(runner, "warnings", [])` becomes `result.warnings`.

**S9 — `paths.py` stops writing.** Every function in `paths.py` becomes pure path derivation with
no `mkdir`. A new `paths.ensure(path: Path) -> Path` is the only thing that creates a directory,
called explicitly by the three real writers: `Journal.append` (before its first write),
`RenderedPrompt.write`, and the launcher's attempt-directory setup. The four comments at
`store.py:416-423`, `cli.py:380-384,1264-1269`, `orchestrate.py:504-509` warning callers away from
`Store.open` in a read path are deleted along with the hazard they describe — `Store.open` no
longer has a side effect to avoid.

**S10 — Collaborators are injected, not patched.** New frozen dataclass
`Collaborators(build=bases.build, integrate=resolver.integrate_milestone, run_git=worktree.run_git,
board=board)` in `orchestrate.py`, with those as its defaults. `run_milestone` takes
`collaborators: Collaborators = Collaborators()` alongside its existing `driver` and
`runner_factory` parameters, and calls everything through it (`collaborators.build(...)`, etc.)
instead of `bases.build(...)`/`module.attr` lookups. Tests construct a `Collaborators` with fakes
instead of `monkeypatch.setattr(bases, "build", fake)`. This is deliberately incremental — it
targets the four collaborators identified in §2 (S10), not a rewrite of the 261 existing
monkeypatch calls, most of which patch things (`subprocess`-adjacent launchers, git, brd) that are
legitimately at a process boundary and are exactly what `monkeypatch` is for. The autouse
`integrate_recorder` fixture (`test_orchestrate.py:768-773`) is replaced by tests passing a real
`Collaborators(integrate=fake_integrate)` only where they mean to fake it, so the default test run
exercises the real Integrate path.

**S11 — Housekeeping**, done alongside whichever of S1–S10 touches the same file rather than as
its own pass: add `before_phase: str | None` to `SubtaskSummary` and stop parsing it out of
`detail` text (touches S5's `orchestrate.py` work); make `rebuild_from_journal` one transaction
(touches S2's `store.py` work); scope `latest_open_checkpoint` by `run_id` (touches S2's
`store.py` work); leave `steps/reducers.py`'s dual-read shim as is — it is a faithful, working
compatibility layer for a deliberate cross-language port, not drift.

## 4. Module graph, before and after

```
 before (S1, S6, S10)                          after
 ───────────────────                           ─────
 cli.py ──imports for Typer──> (nothing)        cli.py ──> runs.py <── orchestrate.py
   ^            ^                                              ^  ^
   |            |                                               \  \
 orchestrate.py bases.py, integration.py         bases.py ────────> resolver.py <── integration.py
   (deferred imports break the cycle)              (S4: one resolver, stop-aware)

 orchestrate.py ──schedules stories via──> grafo.TreeExecutor
   (workarounds inlined: run_until_killed,     (S6: grafo unchanged; its five
    logger silencing, timeout=None, the         workarounds move into
    merged-root Event wait — each at its        grafo_compat.py, named and
    own call site, no shared home)               documented in one place)

 test_orchestrate.py ──monkeypatch.setattr──> bases.build, integrate_milestone, run_git
                       (S10: replaced by orchestrate.Collaborators(...) passed to run_milestone)
```

| File | Change |
|---|---|
| `runs.py` | NEW — S1's extracted helpers and error types |
| `resolver.py` | NEW — S4's unified `_resolve_conflict`/`_resolver_detail`/`_verify` |
| `grafo_compat.py` | NEW — S6's named home for `run_until_killed`, logger silencing, `GRAFO_NODE_TIMEOUT` |
| `cli.py` | shrinks to Typer bodies + envelope glue; imports `runs`; narrower `HANDLED` (S7) |
| `bases.py`, `integration.py` | import `runs`, `resolver`; `integrate_milestone` becomes async (S4) |
| `orchestrate.py` | `StoryRecorder` (S5); grafo scheduling unchanged, its workarounds moved to `grafo_compat` (S6); `Collaborators` (S10) |
| `runtime/walk.py` | gains the canonical `GateVerdict`/evaluator (S3) |
| `dispatch.py` | its gate-evaluation functions deleted, calls `walk`'s (S3); `RunnerResult` (S8) |
| `store.py` | `phases.detail`, `runs.milestone_id` columns + migration (S2); one-transaction rebuild, run-scoped checkpoint lookup (S11) |
| `models.py` | `Run.milestone_id`; `SubtaskSummary.before_phase` (S11) |
| `paths.py` | pure derivation only; `ensure()` added (S9) |
| `errors.py`, `runtime/errors.py` | merge into one `errors.py`; `InvalidCardIdError`; `_Signal` marker (S7) |
| `runtime/state.py` | `RunDeps` becomes a typed `Protocol` (S8) |
| `pyproject.toml` | unchanged — `grafo` stays a runtime dependency (S6) |

## 5. Compatibility

No CLI-observable change: envelope shape, exit codes, `am status`/`am run`/`am resume` output
shapes are unchanged except that a failed phase's `detail` is now visible where it was silently
dropped before (a strict improvement, not a breaking one — nothing today depends on `detail`
being absent). Old runs (pre-migration journals) replay correctly: `phases.detail` and
`runs.milestone_id` are additive columns, and `rebuild_from_journal` already re-derives every row
from the journal, which has always carried both fields. No change to the journal line format
itself, so no version bump is needed there.

## 6. Testing

Each decision is a refactor of already-tested behaviour except S2 (new capability):

- S1: existing `test_cli.py`/`test_orchestrate.py`/`test_bases.py`/`test_integration.py` pass
  unchanged once imports are repointed at `runs`; add one test asserting `bases`/`integration`
  never import `cli` (a static/import-graph assertion, not behavioural).
- S2: new tests — a phase recorded `failed` with a `detail` round-trips through `load_run`; two
  milestone cards with colliding short ids both resume correctly by `milestone_id`.
- S3: a gate that raises is tested once against the shared evaluator; two thin tests assert each
  phase kind still maps `"broken"` to its own existing outcome.
- S4: `test_integration.py`'s conflict tests and `test_bases.py`'s move onto the same resolver
  fixture; add one test that a pause signalled during Integrate parks it, mirroring the existing
  "pause during a lane" test from milestone 9.
- S5: no new tests — `test_orchestrate.py`'s assertions on recorded story/subtask status and
  journal shape are the oracle; they must pass against the extracted `StoryRecorder` unchanged.
- S6: no behavioural test changes — `test_orchestrate.py`'s dataflow, multi-blocker, and slot tests
  keep asserting against real grafo scheduling, unchanged. Add one test importing `grafo_compat`
  directly to confirm `run_until_killed` and the logger contextmanager still do what their moved-out
  docstrings claim, so the extraction itself is covered.
- S7: a test that a bare `ValueError` raised deep in a fake `run_milestone` collaborator propagates
  out of `cli.run` with its traceback intact, rather than becoming an `ok: false` envelope.
- S8: `mypy`/type-checking (if/when introduced — today there is none per CLAUDE.md) is not required
  for this decision to land; the `Protocol` and `RunnerResult` are worth doing for readability and
  IDE support alone. If a typecheck command is added in this window, `runtime/` should pass it
  cleanly as a side effect.
- S9: existing tests that rely on `paths.*` mkdir-ing still need directories created somewhere —
  move that setup to call `paths.ensure()` explicitly; add a test that calling any `paths.*`
  function does not create a directory.
- S10: `test_orchestrate.py`'s Integrate-path tests move off the autouse fixture and construct
  `Collaborators` explicitly; add one test that runs `run_milestone` with the *real* `resolver`
  default (no monkeypatch) against the M5 git fixtures, so the real Integrate path is exercised by
  at least one test in the default run.

Verification for every task in this addendum: `uv run pytest`, full suite green, as CLAUDE.md
requires.

## 7. Suggested sequencing

Not a milestone breakdown by itself — story/subtask sizing is `setup-milestone`'s job — but an
ordering constraint worth stating here: S2 (schema) and S7 (errors) are independent of everything
else and can land first or in parallel. S1 must land before S10 (Collaborators needs `runs.py` to
exist so `orchestrate.py` isn't reaching back into `cli.py` for one of the four collaborators).
S4 depends on S1 (resolver.py sits where bases/integration/cli-helpers already got untangled) and
should land before S10 folds `integrate` into `Collaborators`. S6 is a pure extraction (no
scheduling behaviour changes) and is independent of everything else — low risk, can land anytime.
S3, S5, S8, S9 are each self-contained and can land in any order once S1 is done.

## 8. Out of scope

- Rewriting the 261 `monkeypatch` calls that patch legitimate process boundaries (git, brd, the
  harness launcher) — S10 targets only the four collaborators identified in the audit.
- Introducing a typecheck command or CI gate — CLAUDE.md states there is none today; S8 does not
  propose adding one, only typing the one seam that would benefit most if one is added later.
- Any change to the pygents engine's turn/phase model, the checkpoint format, or the harness
  adapter contract — none of S1–S11 touches `runtime/engine.py`'s or `dispatch.py`'s actual
  execution logic, only its error/type surface (S3, S8) and its callers (S1, S10).
- Milestone 9's live-control work (`C1`–`C12`) — this addendum assumes it has landed; `S4`'s
  `StopSignal` plumbing into Integrate builds on `C6`/`C12`'s existing `StopSignal`, it does not
  redesign it.
- Replacing or dropping grafo. T1's decision to schedule the story tree with grafo stands; S6 only
  relocates its known workarounds, it does not revisit whether grafo is the right tool.
