# The pygents engine — design addendum

Date: 2026-09-25
Extends: `2026-09-23-agent-manager-design.md` (D2, §6, §9), `2026-09-24-orchestration-design.md`
(P4 stop), `2026-09-25-integrate-design.md` (I3, `integrate.yaml`)
Status: milestone 6 scope; decisions G1–G10. Builds on milestone 5 (Integrate) merged.

## 1. Why this shape

The subtask engine (`engine.py` walking `builtin/task.yaml`) works, but two limits
have become visible:

- **Resume re-runs finished work.** Phase results live only in the in-memory binding
  table (`engine._bind_result`). `cli.resume_start_phase` therefore backs a resume
  off to every earlier phase whose result the interrupted phase reads: a crash in
  `spec` re-dispatches `explore`, a paid agent call.
- **The workflow cannot loop.** `task.yaml` only moves forward (`skip_to`). A critic
  that reports blockers ends the subtask even when one revision would settle it.

It has also drifted from the workflow it ports. `leave-me-alone`'s `task.js` has a
Review stage that fixes and commits, is handed three exact git commands, and stops on
unresolved blockers; and two critics with different briefs that fold their own fixes
in. The port has none of that (§7).

This milestone rebuilds the subtask engine on `pygents` (the user's own agent
framework: tools, turns, agents, `ContextPool`/`ContextQueue`, hooks, `to_dict`), so
that the engine's state is one serialisable object checkpointed between phases, and
closes the `task.js` gaps on the new engine. `orchestrator.js` is the next milestone;
this one only makes sure the subtask engine can be driven by it (§10).

## 2. What was found by reading the code

- `pygents` 0.6.7: `Agent.run()` is an async generator; nothing in the library starts
  an event loop or offloads blocking work (no `asyncio.run`, `to_thread` or
  `run_in_executor` in its source). `pause()` flips an `asyncio.Event` and `put()`
  feeds an `asyncio.Queue`: both are bound to one loop and not thread-safe.
- `Agent.to_dict()` taken in `AgentHook.BEFORE_TURN` is consistent: nothing is in
  flight and the next turn is at the head of the queue. Taken in `AFTER_TURN` it is
  not: the finished turn is still `current_turn`, and `from_dict` would replay it.
  Verified with a script against 0.6.7.
  Fixed in pygents 0.7.0; see the adoption addendum.
- `ContextItem.content` and `Turn.output` are serialised raw, so everything placed in
  the pool must already be JSON (a Pydantic result must be dumped first).
- Breaking out of `agent.run()` early (a `return` inside `async for`) raises
  `SafeExecutionError` when the generator closes: its `finally` resets the hooks of a
  turn still marked running. Callers must consume `run()` to the end.
  Fixed in pygents 0.7.0; see the adoption addendum.
- `HookRegistry` refuses a *different* function under an existing name, so per-agent
  hooks defined as closures break on the second agent. Global `@hook(..., tags=...)`
  functions defined at module level do not.
  Fixed in pygents 0.7.0; see the adoption addendum.
- `ToolRegistry` names are process-wide and unique; `AgentRegistry` names likewise.
- Every blocking call in agent-manager is synchronous `subprocess.run`/`Popen`:
  `harness/launcher.py`, `board.py`, `steps/worktree.py`, `steps/verify.py`. They are
  serialised by `threading` locks (`board.WRITE_LOCK`, `worktree._REPO_LOCKS`).
- `dispatch.AgentRunner` already owns an agent phase end to end: role, brief,
  attempts, retry budget, result validation and the phase's gates. It raises
  `AgentPhaseFailed` when the phase gives up.
- `am run --milestone --dry-run` (`cli.dry_run_payload`) reads only the board and
  `dag`; it never reads a workflow. Nothing here affects it.
- Milestone 5 adds a second workflow, `builtin/integrate.yaml` (`resolve` →
  `verify`), which `integration.py` drives through `engine.run_subtask`.
- In `task.js` both critics fold confirmed fixes into the spec/plan file themselves;
  `blockers=true` is reserved for what needs a human decision and stops the run
  (`task.js:636`, `:727`).

## 3. Decisions

**G1 — One pygents `Agent` per subtask.** Its turns are the workflow's phases, its
`ContextPool` holds phase results, its `ContextQueue` holds loop feedback. One agent
is one checkpoint row. The milestone and story levels stay as they are in this
milestone (§10).

**G2 — One event loop; blocking work goes through `asyncio.to_thread`.** The CLI calls
`asyncio.run` once per subtask. Tools reach the launcher, the board and the steps only
through `runtime/bridge.py`, which wraps each call in `asyncio.to_thread`, so the loop
is never blocked and the existing `threading` locks stay valid. Nothing in `board.py` or
`steps/` is rewritten. Cancelling a turn cannot stop a `to_thread` worker, so the
launcher gains an optional `on_spawn(process)` callback and the bridge kills the
process tree of any call it cancels (Ctrl-C must not orphan `claude -p`).

**G3 — The workflow is declared Python data, compiled into two generic tools.**
`workflow/phases.py` holds plain frozen dataclasses (no pygents import);
`workflow/task.py` declares `TASK` and `workflow/integrate.py` declares `INTEGRATE`.
`runtime/compile.py` turns a `Workflow` into exactly two tools, `agent_phase` and
`step_phase`, which look the current phase up in the list and yield the next `Turn`.
Control flow is decided in one place, from the list.

**G4 — Revision loops, one retry, critics only.** `AgentPhase.on_fail = Goto(phase,
max_loops)`. `validate_spec` loops to `spec` and `validate_plan` to `plan`, each at
most once; a second failure escalates `validation` as today. Review does not loop.
The looped-to phase receives the critic's reason through a new `feedback` input.

**G5 — Checkpoints are `Agent.to_dict()` rows, written at `BEFORE_TURN`.** A
`checkpoints` table in the per-project store. Resume restores the agent and continues
at the head of its queue; nothing before it re-runs. Replaces
`interrupted_phase`/`resume_start_phase`/`_skipped_origin`.

**G6 — A checkpoint is refused on a changed workflow.** Every row carries
`Workflow.digest()`. A resume whose digest differs is refused (exit 3); a milestone
relaunch starts that card fresh instead.

**G7 — Side by side until parity, then delete.** `am run`/`am resume` take
`--engine={yaml,pygents}` (default `yaml`). The behavioural engine tests run against
both. The last subtask flips the default and deletes `engine.py`, `workflow/loader.py`,
`workflow/registry.py`, `builtin/*.yaml`, the three resume helpers and the flag.

**G8 — The stop is bridged, not redesigned.** `orchestrate.py` keeps its lane threads
and `RunStop` (a `threading.Event`, safe to read from any thread). A `BEFORE_TURN`
hook reads it; when set, it checkpoints (`parked`) and raises `Parked`, which ends
`run()` cleanly. The orchestrator milestone replaces this with `pause()` (§10).

**G9 — Close the `task.js` gaps on the new engine.** Review fixes and commits and
reports three exact git commands; a new `review_blockers_gate`; `spec_critic` and
`plan_critic` replace `critic`; `verify` also runs Explore's typecheck and lint (§7).

**G10 — Same outcomes, same reports.** The new engine returns the existing
`SubtaskSummary` and writes the same `phases`/`attempts` rows and journal lines, so
`orchestrate.py`, `integration.py`, `am status`, `am logs` and every escalation
payload are unchanged.

## 4. Architecture

```
 src/agent_manager/
 ├── cli.py              CHANGED   --engine={yaml,pygents}; resume reads checkpoints
 ├── orchestrate.py      as-is     lane threads call run_subtask(...)
 ├── integration.py      CHANGED   takes the engine choice, like run_card
 ├── engine.py           as-is     old engine; deleted at switch-over
 ├── workflow/
 │   ├── loader.py, registry.py, builtin/*.yaml   old engine only; deleted at switch-over
 │   ├── phases.py       NEW   Step / AgentPhase / Goto / Retry / Workflow
 │   ├── task.py         NEW   TASK
 │   └── integrate.py    NEW   INTEGRATE
 ├── runtime/            NEW   the only package that imports pygents
 │   ├── compile.py      Workflow ─► agent_phase, step_phase (cached per digest)
 │   ├── context.py      subtask context ⇄ pool JSON; typed binding table for a phase
 │   ├── bridge.py       to_thread wrappers: AgentRunner, steps, board
 │   ├── checkpoint.py   module-level hooks: checkpoint + stop bridge
 │   └── engine.py       async drive(); sync run_subtask() = asyncio.run(drive(...))
 ├── store.py            CHANGED   + checkpoints table
 ├── pyproject.toml      CHANGED   + pygents>=0.6.7
 ├── prompt.py           CHANGED   + `feedback` input resolver; accepts either phase model
 ├── dispatch.py         CHANGED   gates/result given as names (old) or objects (new)
 ├── steps/reducers.py   CHANGED   + review_blockers_gate; plan_hash_gate_adapter moves here
 ├── steps/verify.py     CHANGED   + typecheck and lint commands
 ├── roles/bundles/      CHANGED   reviewer rewritten; spec_critic, plan_critic replace critic
 ├── harness/launcher.py CHANGED   optional on_spawn, so a cancelled turn can kill its process tree
 └── harness/ (rest), results.py    as-is
```

`runtime.engine.run_subtask` has the old engine's signature (`workflow` becomes a
`phases.Workflow`) and returns `SubtaskSummary`. Until switch-over, `runtime/` imports
`bind_arguments`, `subtask_context` and the phase/subtask recording helpers from
`engine.py`; at switch-over they move into `runtime/`.

One subtask on the new engine:

```
 run_subtask(...)  ── asyncio.run(drive(...)) ────────────────── one loop
   │ checkpoint to resume from? ─ yes ─► Agent.from_dict(row.agent)
   │                           └ no ──► Agent(f"{run_id}:{card_id}", [agent_phase, step_phase],
   │                                          tags=["subtask"])
   │                                    pool ← ContextItem(id="subtask", …fixed context…)
   │                                    put(Turn(first phase, loop=0))
   ▼
 Agent.run()   (always consumed to the end)
   │ BEFORE_TURN ─► stop set? ─► save('parked'), raise Parked
   │             └► save('turn')
   │ agent_phase(phase, loop) ─► bridge.agent(AgentRunner) ─► claude -p (worktree)
   │     yield ContextItem(id=phase, content=<result JSON>)   ─► pool
   │     yield ContextItem(content={"for", "from", "detail"}) ─► queue   (loops only)
   │     yield Turn(next phase | Goto target)                  ─► queue
   ▼
 save('done' | 'escalated'), SubtaskSummary
```

## 5. Phase model and compiler

`workflow/phases.py`:

```python
@dataclass(frozen=True)
class Goto:       phase: str; max_loops: int = 1
@dataclass(frozen=True)
class Retry:      max_attempts: int; on: tuple[str, ...]
@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[..., Any]
    args: Mapping[str, Any] = field(default_factory=dict)
    gates: tuple[Callable[..., Any], ...] = ()
    best_effort: bool = False
    when: Callable[..., bool] | None = None
    skip_to: str | None = None
@dataclass(frozen=True)
class AgentPhase:
    name: str
    role: str
    inputs: tuple[str, ...]
    result: type[BaseModel] | None
    gates: tuple[Callable[..., Any], ...] = ()
    retry: Retry | None = None
    writes: str | None = None
    timeout: timedelta = timedelta(minutes=30)
    on_fail: Goto | None = None
@dataclass(frozen=True)
class Workflow:
    name: str
    phases: tuple[Step | AgentPhase, ...]
    def phase(self, name: str) -> Step | AgentPhase: ...
    def validate(self, *, launcher_timeout: timedelta) -> None: ...
    def digest(self) -> str: ...
```

`validate()` refuses, with `WorkflowError` naming the phase: duplicate names; a
`skip_to` that is not strictly later; a `Goto` that is not strictly earlier or has
`max_loops < 1`; `when` without `skip_to` or the reverse; a role bundle that does not
load; an input no resolver or earlier phase provides; an `AgentPhase.timeout` not
greater than `launcher_timeout` (G2: the launcher must kill `claude -p` before the
turn is cancelled, since cancelling cannot stop a `to_thread` worker).
`digest()` is a sha256 over name, kind, order, role, inputs, `result` qualname, gate
and step qualnames, `skip_to`/`when`, `on_fail` and `retry` of every phase.

`runtime/compile.py` — both tools derive the next turn with one helper,
`next_turn(wf, after=..., loop=...)`, which returns `Turn(<tool of the next phase's
kind>, kwargs={"phase": name, "loop": loop}, timeout=phase.timeout)` or `None`.

- `agent_phase(phase, loop, pool, memory)`: builds the binding table with
  `context.binding_table(pool, memory, phase)`, runs `bridge.agent(...)` (which runs
  `AgentRunner` — retries and gates included — in a thread). On success it yields the
  result as `ContextItem(id=phase)` and the next turn. On `AgentPhaseFailed`: if
  `on_fail` and `loop < max_loops`, it yields a feedback item and
  `Turn(on_fail.phase, loop + 1)`; otherwise it raises `Escalated(phase, detail)`.
- `step_phase(phase, loop, pool)`: runs one deterministic step through the function
  extracted from the old engine's `_run_deterministic` (`engine.run_one_step`), so both
  engines judge a step identically; then its
  gates (a `warn` verdict is a warning, any other verdict raises `Escalated`); a
  failure of a `best_effort` step is a warning; `when`/`skip_to` yields the skip
  target and records the skipped names as a pool item `skipped`.

Tools are cached per `(workflow.name, digest)` so a process compiles each workflow
once; tests clear `ToolRegistry`, `AgentRegistry` and the cache through a fixture.

**Sharing `dispatch.py` and `prompt.py` with the old engine.** `AgentRunner` and
`render_prompt` read a phase's `name`, `role`, `inputs`, `retry`, `writes`, `result`
and `gates`, and today resolve `result` by name through `RESULT_MODELS` and each gate
by name through `workflow.function(name)`. During side by side they accept both: a
`result` that is already a model class is used as is, and a gate that is already a
callable is called as is (names still go through the old lookup). `phases.Retry` has
the same `max_attempts`/`on` fields the runner reads. At switch-over the by-name paths
are deleted. `registry.plan_hash_gate_adapter` moves to `steps/reducers.py` so both
workflow definitions can reference it; with results in the pool it always sees
`implement`'s result, including after a resume.

`runtime/context.py`: the pool holds JSON only. `seed(...)` writes
`ContextItem(id="subtask")` with the card, parent story, branch, worktree, base branch
and verification commands (`model_dump(mode="json")`). `binding_table(...)` rebuilds
the exact mapping `engine.subtask_context` + `_bind_result` produce today, with each
phase result dumped the way `dispatch.gate_values` already presents it (aliases
included, so `review_gate` still reads `commitCount`), plus `feedback`: the queue items
whose `for` is this phase. `prompt.py` gains a `feedback` resolver that renders those
items, and renders nothing when there are none.

## 6. Checkpoints and resume

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
    run_id    TEXT NOT NULL,
    card_id   TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    workflow  TEXT NOT NULL,
    digest    TEXT NOT NULL,
    reason    TEXT NOT NULL CHECK (reason IN ('turn', 'parked', 'done', 'escalated')),
    agent     TEXT NOT NULL,
    saved_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, card_id, seq)
);
```

`Store.save_checkpoint(...)` and `Store.latest_checkpoint(run_id, card_id)`;
`Store.latest_open_checkpoint(card_id, workflow)` finds the newest non-terminal row
for a card across runs. Hooks are module-level `@hook(..., tags={"subtask"})` and
find the run (store, subtask, workflow, stop) through a `ContextVar` that
`runtime/engine.py` sets around `run()`; with no run set they do nothing.

- `BEFORE_TURN`: stop set → save `parked`, raise `Parked`; else save `turn`.
- After `run()` ends: `done`, or `escalated` when `Escalated` or any other
  `Exception` ended it. A `BaseException` writes nothing; the last `turn` row stands.

Resume:

- `am resume <run-id>` loads the newest `turn`/`parked`/`escalated` row of the run's
  interrupted subtask, refuses on a digest mismatch (exit 3), marks orphan attempts
  `harness_error` as today, and continues with `Agent.from_dict`. It now continues
  stopped (parked) subtasks instead of refusing them. After an escalation it re-runs
  the failed phase with the loop count it had.
- `am run --milestone` relaunch still starts a new run and skips `done` cards. A card
  with an open checkpoint from an earlier run and a matching digest continues from it;
  otherwise it starts fresh and `plan_check` still reuses a validated plan.

Known limit, accepted: a phase's journal row is written inside its turn and the next
checkpoint at the next `BEFORE_TURN`. A crash between the two re-runs that phase:
phases are at-least-once. Inside a phase, git and the Plan-Hash trailers remain what
`implement` resumes from. Closed for agent phases by milestone 11 (`2026-09-27-exactly-once-design.md`); steps stay at-least-once by contract.

## 7. `task.js` on `TASK`

```
 task.js stage          TASK phase         kind   role / step                  notes
 ─────────────────────  ─────────────────  ─────  ───────────────────────────  ──────────────────────────
 Worktree               worktree           step   worktree.ensure
 Explore                explore            agent  explorer, ExploreResult      retry 2: gate_failed, schema_invalid
 status → in_progress   mark_in_progress   step   rollup.set_status            best_effort
 plan-check             plan_check         step   plan_check.find_validated_plan  when has_validated_plan → docs_commit
 Spec                   spec               agent  spec_author, SpecResult      inputs + feedback
 Validate (spec)        validate_spec      agent  spec_critic, CriticResult    critic_blockers_gate; Goto(spec, 1)
 Plan                   plan               agent  planner, PlanResult          inputs + feedback
 Validate (plan)        validate_plan      agent  plan_critic, CriticResult    critic_blockers_gate; Goto(plan, 1)
 validated marker       mark_validated     step   plan_check.mark_validated
 (commit spec + plan)   docs_commit        step   docs_commit.commit_documents
 Implement              implement          agent  coder, ImplementResult       implement_blocked_gate
 Review                 review             agent  reviewer, ReviewResult       review_blockers_gate, review_gate,
                                                                                plan_hash_gate
 Ship: verify           verify             step   verify.run_suite             + typecheck, lint
 Ship: status → done    mark_done          step   rollup.set_status            best_effort
```

`INTEGRATE` is `integrate.yaml` as declared data: `resolve` (resolver, ResolveResult,
merge_completed_gate, retry 2) → `verify`. No loops.

The gaps closed (G9):

1. **Reviewer.** `roles/bundles/reviewer/system.md` is rewritten from `task.js`'s
   Review prompt: read the full diff against the plan and spec; the test-integrity
   gate; fix confirmed findings in the worktree with TDD, committing each fix with
   `Co-Authored-By` and `Plan-Hash` trailers (hash computed once from the plan file);
   run the repo's lint/format commands and commit their fixes the same way; never
   weaken, skip or delete a test; then run exactly

   ```
   git status --porcelain
   git rev-list --count <base>..HEAD
   PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8); git log <base>..HEAD --format=%B | grep -c "^Plan-Hash: $PLAN_HASH"
   ```

   and report their output verbatim as `porcelain`, `commit_count`, `tagged_count`,
   and `plan_hash`, without acting on them. `unresolved_blockers` lists only
   blocker-severity findings still standing after the fix pass. The commands need no
   `-C`: the harness already runs with the subtask's worktree as its cwd (D7).
   `<base>` and `<plan>` reach the brief through the existing `base_branch` and
   `plan_path` inputs. `policy.toml` lists `Edit` and `Write`.
2. **`review_blockers_gate(result)`**: a non-empty `unresolved_blockers` →
   `{"blocked": "review", "detail": "review left N unresolved blocker(s): …"}`;
   a result that is not a mapping → blocked, "the review stage returned nothing".
   It runs before `review_gate`, matching `task.js:842-855`.
3. **Two critics.** `spec_critic` (completeness, consistency, clarity, scope, YAGNI;
   checked against the repo's own docs and sibling subtasks) and `plan_critic` (the
   plan against the settled spec: completeness, spec alignment, decomposition,
   buildability; a defect that traces to the spec sets `blockers` rather than being
   patched around). Both verify suspicions against the files, fold every confirmed
   fix into the file, and set `blockers` only for what needs a human decision.
   `CriticResult` is unchanged. The `critic` bundle is deleted.
4. **Verify** runs `--verify` commands, then Explore's `typecheck` (when non-empty)
   and each `lint` command, in that order; any non-zero exit fails
   `verification_passed_gate` as today.

## 8. Errors

| Inside `run()` | Engine does | Summary | Checkpoint |
|---|---|---|---|
| `Escalated(phase, detail)` | record subtask `escalated` | `escalated`, that phase | `escalated` |
| `Parked` | record subtask `stopped` | `stopped`, before-phase | `parked` |
| other `Exception` | escalate `"<Type>: <msg>"` at the running phase | `escalated` | `escalated` |
| `TurnTimeoutError` | escalate `"phase X exceeded <timeout>"` | `escalated` | `escalated` |
| `BaseException` | propagate, write nothing | — | last `turn` row |
| digest mismatch on resume | refuse before `run()` | — (exit 3) | — |

pygents ≥0.7.0 makes this safe; the engine still consumes `run()` and keeps global
module-level hooks by design.

## 9. Testing

All tests use fake runners and temporary git repositories; none calls a model.

- **Parity.** The behavioural tests of `run_subtask`, `am run --card`, `am resume`,
  milestone runs and Integrate are parametrised over `engine ∈ {yaml, pygents}`. Tests
  of old-engine internals stay yaml-only and are deleted with it.
- `workflow/test_phases.py`: every `validate()` refusal; `digest()` stable across
  runs, changed by reorder, rename, retarget, gate swap.
- `runtime/test_compile.py`: next turn, `skip_to` records skipped phases,
  `best_effort` warns, gate `warn`, a `Goto` loops once and escalates on the second
  failure, loop count carried in kwargs.
- `runtime/test_context.py`: seed → JSON round-trip → `binding_table` equals the old
  engine's binding table for the same inputs, per phase of `TASK` and `INTEGRATE`.
- `runtime/test_checkpoint.py`: a fake runner raising a `BaseException` in phase N;
  resume re-dispatches N once and nothing earlier; loop count survives; digest
  mismatch is refused; a parked subtask resumes; relaunch continues an open
  checkpoint and starts fresh on a digest mismatch.
- `runtime/test_stop_bridge.py`: `RunStop` set mid-phase parks before the next phase.
- `steps`: `review_blockers_gate`; verify with typecheck and lint.
- `roles`: golden briefs — the reviewer's contains the three commands and both
  trailers; each critic's contains its criteria and "fold".
- The opt-in real-harness test runs one subtask end to end on `--engine=pygents`.

## 10. Switch-over, and what the orchestrator milestone inherits

Switch-over is the milestone's last subtask: all parametrised tests green on
`pygents` and the opt-in real run passing → default flips, the old engine and its
documents are deleted (G7), the shared helpers move into `runtime/`, `--engine` goes.

The orchestrator milestone builds the supervisor tree: milestone and story agents
above the subtask agent, lanes as asyncio tasks on the one loop, the stop as
`pause()` with the `ON_PAUSE` hook taking the `parked` checkpoint, results passed up
as return values. From this milestone it needs only: a subtask agent that runs to
completion, parks at a phase boundary with a checkpoint, and returns
`SubtaskSummary`.

## 11. Deferred

- The supervisor tree and `pause()`-based stop (orchestrator milestone).
- ~~Exactly-once phases (checkpoint on the next turn's `put`).~~ Delivered for agent
  phases by milestone 11 (`2026-09-27-exactly-once-design.md`), by a different
  mechanism: a floor saved with each checkpoint, and adoption of the recorded `ok`
  attempt on resume, not a checkpoint on the next turn's `put`. Steps stay
  at-least-once by contract.
- Benchmarking the rewritten reviewer and critic prompts.
- Upstream pygents fixes: early exit from `run()` raising `SafeExecutionError`;
  closure hooks colliding in `HookRegistry`. Done (pygents 0.7.0).
