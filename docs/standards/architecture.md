# Architecture: module layering

## 1. Purpose and status

- This is the layering standard for `src/agent_manager/`. It decides which module may import which, where each kind of code lives, and which libraries and processes sit behind which module.
- For layering it replaces the design spec `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. Files under `docs/superpowers/` are frozen history: guards and audits ignore them.
- Rules are cited by section number (for example "§4.3"). A rule cites the code it responds to as `file:line`, measured against `master` at `3aa57bf` (2026-10-04). Line numbers drift, so re-read the code before acting on one.
- "Measured" means it was read from the AST import graph or with `wc`/`git`. "Inferred" means it is a judgment.
- Proposed choices that still need the maintainer are listed in §13. Until the moves in §6.5 land, the current violations are the known exceptions in §11.

## 2. The organizing idea

A workflow engine built as a functional core inside an imperative shell:

- **Declared data.** A workflow is data (`workflow/`), checked and digested in `workflow/phases.py`.
- **Interpreter.** An engine walks that data (`runtime/`, the only home of pygents). The agent-phase runner (`dispatch.py`) is injected into it.
- **Pure core.** Card derivations (`census`, `dag`), prompt resolution (`prompt`), result models (`results`) and pure gates (`steps/reducers.py`).
- **Deterministic steps.** `steps/` holds git, verify and board writes. There are no model calls there.
- **Truth and projection.** An append-only journal is the truth, with a SQLite projection (`store.py`) next to it. Every write appends the journal line first, then writes the row, and is fenced by the lease.
- **Thin adapters at the edges.** `board.py` (the `brd` CLI), `harness/` (agent processes), `locks.py`, `detach.py`.
- **Use cases above all of it.** Run, resume, milestone and board runs, Integrate. A Typer shell (`cli.py`) sits on top of the use cases and only parses options and renders output.

## 3. Layer order

### 3.1 The table

Layers are listed from lowest to highest. **A module may import only modules in a strictly lower layer (§4.1).** Modules in the same layer are siblings and do not import each other. This table lists all 49 `.py` files under `src/agent_manager/` (43 modules plus 6 package `__init__.py`). `find src/agent_manager -name '*.py'` confirms the count. Checked against the AST import graph, the only imports that break this table are the three listed in §11.1.

| L | Band | Modules | Responsibility |
|---|---|---|---|
| 0 | Kernel | `__init__` (holds `__version__`), `harness/__init__`, `roles/__init__`, `runtime/__init__`, `steps/__init__`, `workflow/__init__`, `models`, `errors`, `runtime.errors`, `paths`, `runtime.stop` | Types, errors, path derivation, the stop signal. No `agent_manager` imports. |
| 1 | Core | `census`, `results`, `roles.loader`, `steps.reducers` | Pure derivations, result models, role bundles, pure gates. |
| 2 | Core | `dag` | Card identity, branch names, levels, stacking. |
| 3 | Core | `prompt` | Resolves a phase's inputs and renders its prompt. |
| 4 | Core | `workflow.phases` | The phase model: frozen data, `validate()`, `digest()`. |
| 5 | Adapters | `locks`, `store`, `detach`, `harness.base`, `harness.claude` | File locks; the journal and SQLite projection; process fork; the harness Protocol and the Claude argv. |
| 6 | Adapters | `board`, `control`, `harness.launcher`, `harness.registry` | `brd`; the lease, heartbeat and control watcher; process launch; harness lookup by name. |
| 7 | Steps | `steps.worktree`, `steps.verify`, `steps.plan_check`, `steps.rollup` | Deterministic steps: git worktree, verify suite, plan check, board status rollup. |
| 8 | Steps | `steps.docs_commit`, `steps.integrate` | Steps built on `steps.worktree`. |
| 9 | Workflow | `workflow.task` | The shipped `task` workflow, as data. |
| 10 | Workflow | `workflow.integrate` | The shipped `integrate` workflow (uses `task`'s timeout floor). |
| 11 | Runtime | `runtime.walk`, `runtime.bridge`, `runtime.state` | What the walk shares below pygents (binding table, step run, summary); the thread/process bridge; `RunDeps`. |
| 12 | Runtime | `runtime.context`, `dispatch` | The pool codec; the agent-phase runner (`AgentPhaseRunner` implementation). |
| 13 | Runtime | `runtime.checkpoint`, `runtime.compile` | Checkpoint hooks; the workflow compiled into two pygents tools. |
| 14 | Runtime | `runtime.engine` | One subtask, one agent, one loop. |
| 15 | Application | `runs`, `comments` | Run identity, worktree, gate context, resume selection; the outcome-comment outbox. |
| 16 | Application | `bases`, `integration` | The merged base of a multi-blocker story; Integrate. |
| 17 | Application | `orchestrate` | Milestone and board runs (target split in §6.2). |
| 18 | Interface | `cli` | The Typer app: options, one use-case call, envelope and exit code. |

Band rules (inferred, and they are what the table encodes):

- **Core** (L1-L4) never imports any adapter, does no SQLite work, and spawns no process. It reads files only at paths it is handed or in shipped package data (`roles.loader`). `prompt.py:97` writes only to a path its caller passes in.
- **Steps** may use adapters (`steps.rollup` uses `board`), never the runtime.
- **Workflow** (L9-L10) is data. It references step and gate callables and never imports `runtime/` or `dispatch`.
- **Runtime** imports `workflow.phases` only, never a concrete workflow (`workflow.task`, `workflow.integrate`). Callers pass the workflow in.
- **Application** never imports `typer` and never imports `cli`.

### 3.2 Moves relative to the measured depth

"Measured" here is the longest-path depth in the import DAG, counting function-local and `TYPE_CHECKING` edges, with `cli` and `orchestrate` collapsed into one node because of their cycle.

| Module | Measured | Target | Why |
|---|---|---|---|
| `workflow.task`, `workflow.integrate` | L7, L8 (above `dispatch`) | L9-L10, below all of Runtime | `workflow/task.py:27,31` imports `dispatch` only for `DEFAULT_TIMEOUT`. Declared data depends on a runner: an inversion. Fixed by §6.5 M1. |
| `runs` | L9, in a cycle with `integration` | L15, below `bases`/`integration` | `runs.py:282` imports `integration` function-locally for `merge_order`, a pure function of `dag` (`integration.py:78-97`). It moves to `dag` (M2). The comment at `runs.py:279-281` gives a reason that no longer holds: `integration` imports `runs`, not `cli`. |
| `bases` | L10 | L16, sibling of `integration` | It sat above `integration` only because of the `runs` cycle. Neither imports the other. |
| `comments` | L6 (beside `dispatch`) | L15 | Only `cli` and `orchestrate` use it. Its `TYPE_CHECKING` import of `runtime.walk.SubtaskSummary` (`comments.py:29`) makes it depend on Runtime, so it belongs above Runtime. See §13 D3. |
| `control` | L2 | L6 | Grouped with adapters, as the owner of the `run_leases`/`run_controls` side of the lease. The CLI's request side joins it (§6.1). |
| `detach` | L1 | L5 | A process adapter (`os.fork` at `detach.py:149`). The lease hand-off that `cli` does around it moves to the Application band (`handoff.py`, §6.1). |
| `dispatch` | L6 | L12 | Unchanged in substance. It stays above `runtime.walk`/`runtime.bridge` and nothing in Runtime imports it: it is injected as `AgentPhaseRunner`. Only the Application band builds it. |
| `orchestrate` / `cli` | L11, one cycle | L17 / L18 | `orchestrate.py:65` imports `cli` at module level. §6.5 M4-M7 remove every use. |
| `dag`, `prompt`, `workflow.phases` | L2, L3, L4 (beside adapters) | L2-L4, Core | Core is placed below all adapters. None of these imports an adapter (measured), so the order costs nothing. |
| `runtime.state` | L1 | L11 | It holds the run's dependencies for the compiled tools, so it belongs to Runtime. Only Runtime imports it. |

## 4. Import rules

1. **Direction.** A module imports only modules in a strictly lower layer of §3.1. Siblings in the same layer do not import each other.
2. **No module-level cycles.** The import graph is a DAG at module level.
3. **Function-local imports** are allowed only when the target is in a strictly lower layer **and** a one-line comment states the reason (import cost, optional dependency). A function-local import must never hide a cycle. The current violations are `runs.py:282` and `cli.py:1122`, which repeats an import `cli.py:47` already makes at module level.
4. **`TYPE_CHECKING` imports** obey §4.1 like any other import. They exist to avoid import cost at runtime, never to reach upward.
5. **No re-exports.** Import a name from the module that defines it. A module does not import a name so that others can read it off that module. The `cli.X is runs.X` block (`cli.py:61-85`) is legacy. Package `__init__.py` files re-export nothing (`harness/__init__.py`, `workflow/__init__.py` already say so).
6. **Private names stay private.** A name starting with `_` is never imported from, or read off, another module, even within one sub-package. If another module needs it, make it public or move it. Current uses are listed in §11.3.
7. **Workflow identity.** A module named in `workflow/task.py` or `workflow/integrate.py` (steps, gates, result models) is imported by `module.qualname` identity. See §10.3 before moving or renaming anything there.
8. **The table is the source.** A PR that adds, moves or deletes a module updates §3.1 in the same PR.

## 5. Library and process confinement

| # | Only here | What | Measured today |
|---|---|---|---|
| 5.1 | `runtime/{checkpoint,compile,context,engine}.py` | `import pygents` | holds |
| 5.2 | the milestone scheduler (`orchestrate.py`; target `milestone/schedule.py`) | `import grafo` | holds (`orchestrate.py:59`) |
| 5.3 | `cli.py` | `import typer` | holds (`cli.py:34`) |
| 5.4 | `store.py` (target `store/`) | `import sqlite3`, SQL, transaction boundaries (`immediate`, `commit`) | **violated:** `cli.py:24,3129`, `control.py:20,210,251` |
| 5.5 | `board.py` | builds or runs a `brd` argv | holds (`board.py:44-135`) |
| 5.6 | `harness/launcher.py` | spawns a harness (`subprocess.Popen`). `harness/<name>.py` only builds the argv. | holds (`harness/launcher.py:146`) |
| 5.7 | `steps/worktree.py` (`run_git`) | runs `git`. Everyone else calls `worktree.run_git`. | holds |
| 5.8 | `steps/verify.py` | runs the card's verification commands | holds |
| 5.9 | `detach.py` | `os.fork`, `os.setsid` | holds (`detach.py:149,170`) |
| 5.10 | `locks.py` | `fcntl` | holds |
| 5.11 | `paths.py` | derives every path under `<data dir>`. Nobody joins a data-dir path by hand. Directories are created only by `paths.ensure` (cleanup S9). | **violated:** `cli.py:2411` and `store.py:406` build `data_dir() / "runs"` by hand; `paths.py:18,33,57` call `mkdir` |
| 5.12 | `clock.py` (new, L0) | reads the wall clock (`datetime.now`). Use cases take `now` or a clock callable. | **violated:** private `_utcnow` in `cli.py:201`, `orchestrate.py:310`, `control.py:43`, `dispatch.py:323`, `runtime/walk.py:220`; inline reads at `comments.py:391` and `store.py:528` |

Other rules:

- **5.13** `subprocess` may be imported only by the modules in 5.5-5.8. `runtime/bridge.py` may also import it, but only to name `subprocess.Popen` as a type.
- **5.14** Seams, and how a test substitutes each one:

| Seam | Production | Test fake | How it gets in |
|---|---|---|---|
| Board | `board` functions | fake `board_api` / `FakeBoard` | a parameter; target `Collaborators.board` (S10) |
| Launcher | `harness.launcher.run_direct` | `FakeLauncher` | through `RunnerFactory` (`runs.py:110`). Patching `cli.run_direct` is legacy (§11.4). |
| Agent-phase runner | `dispatch.AgentRunner` | `FakeDriver`, fake runner | `RunnerFactory`, `driver=` |
| Store | `Store` on a temp data dir | the real `Store` under `tmp_path` | a parameter. There is no fake store: the journal-then-row rule is tested on the real one. |
| Clock | `clock.utcnow` | a fixed `now` | a parameter at the use-case entry |
| Git | `worktree.run_git` | real git in `tmp_path` (`git` tier) | target `Collaborators.run_git` (S10) |

## 6. Target decomposition of the three large modules

Measured: `cli.py` has 3329 lines, `orchestrate.py` 2784 and `store.py` 2352. Together they are 45% of the package's 18608 lines. They have 118, 87 and 54 commits in `git log`. Everything below is a destination, not a migration plan. Every new module obeys §4. Names are proposals (§13 D5).

### 6.1 `cli.py` into the Interface band and use cases

| Target module | Band (position) | Takes from `cli.py` |
|---|---|---|
| `clock.py` | Kernel (L0) | `_utcnow` (and the copies listed in 5.12) |
| `errors.py` | Kernel (L0) | every `CliError` subclass (`cli.py:93-176`, plus `runs.py`'s), merged with `runtime/errors.py` (S7). Class names unchanged (§10.2). |
| `envelope.py` | Application, bottom | `ok_envelope`, `error_envelope`, `render`, `EXIT_ESCALATED`, `EXIT_ERROR`, `HANDLED`, `WATCH_HANDLED`. This is the envelope and exit-code contract (§10.2). |
| `control.py` (existing) | Adapters (L6) | `refuse_claimed`, `run_lease`, `_claimed_error`, `_run_is_live_error`, and the request side (`CONTROL_*`, `_controllable_lease`, `_record_control`, `request_control`, ...). One module owns both ends of `run_leases`/`run_controls`. |
| `views.py` | Application, with `runs` | read models for `status`/`runs`/`logs`: `RUN_IDENTITY` ... `step_logs_payload` (`cli.py:205-605`, without `checkpoint_resume_phase`), `status_for`, `runs_for`, `LogsSelection`, `select_logs`, `logs_for`, `logs_end_status` |
| `streams.py` | Application, above `views` | the `am watch` and `am logs --follow` protocols (schema 1): `cli.py:2301-2660` (hello lines, poll/follow, UTF-8 chunking, `WATCH_*`) |
| `handoff.py` | Application, bottom | the generic detach hand-off: `release_handed_off`, `run_detached_child`, `hand_off_to_child` (`cli.py:1411-1496`) |
| `runs.py` (existing) | Application, bottom | the shared subtask driver: `default_runner_factory`, `SubtaskDrive`, `drive_subtask_async`, `drive_subtask` (`cli.py:672-805`) |
| `card_run.py` | Application | the `--card` use case: `card_run_status`, `card_outcome_comment`, `CardPreflight`, `preflight_card`, `RecordedRun`, `recorded_card_run`, `run_card_engine`, `run_card`, `detach_card` |
| `reset.py` | Application | `reset_run` and its messages (`cli.py:3187-3307`) |
| `resume.py` | Application, above `milestone/` | `checkpoint_resume_phase`, `_resume_from_checkpoint`, `resume_run` (`cli.py:608-649, 2724-2946`) |
| `preview.py` | Application, above `board_run` | `--dry-run`: `already_done_entries` ... `dry_run_board` (`cli.py:1207-1378`), plus `runs.DryRunPlan`/`compute_dry_run_plan` |
| `cli.py` | Interface (top) | `app`, `main`, every `@app.command`, `RUN_EXAMPLES`, `_check_run_targets`. Each command parses options, calls one use case, renders through `envelope`, and maps `HANDLED` to exit 3. |

### 6.2 `orchestrate.py` into a `milestone/` package and `board_run.py`

| Target module | Position | Takes |
|---|---|---|
| `milestone/plan.py` | lowest in the package (pure) | `PlannedStory`, `plan_levels`, `story_tips`, `SupervisorPlan`, `supervisor_plan`, `builds_a_base_alone`, `milestone_claims`, `milestone_card_ids` |
| `milestone/payloads.py` | pure | the report `data` shapes: `stopped_row`, `escalation_row`, `*_payload`, `with_bases` (`orchestrate.py:146-307`) |
| `milestone/lane.py` | above `plan`/`payloads` | `Driver`, `LaneKind`, `LaneOutcome`, `LaneEscalated`, `LaneStopped`, `StoryRecorder`, `lane`, `base_only_lane`, `build_merged_base`, `post_comment(_async)`, `collect_outcomes` |
| `milestone/schedule.py` | above `lane`; the only grafo importer (5.2) | `GRAFO_LOGGER`, `run_until_killed`, `build_dag_tree`, `supervise`, plus S6's `GRAFO_NODE_TIMEOUT` and the logger context manager |
| `milestone/reopen.py` | beside `lane` | `REOPENED_STATUSES`, `resumable_milestone_run`, `find_run_milestone`, `open_cards`, `_refuse_changed_workflow`, `resume_point`, `resume_checkpoints`, `reopen_rows`, `stale_story_anchors`, `reroll_stale_stories`, `refresh_git` |
| `milestone/run.py` | top of the package | `MILESTONE_WORKFLOW`, `MilestonePreflight`, `preflight_milestone`, `RecordedMilestoneRun`, `recorded_milestone_run`, `record_plan`, `run_milestone_engine`, `run_milestone`, `_in_thread_to_completion`, `_run_milestone_async`, `detach_milestone`, S10's `Collaborators` |
| `board_run.py` | above `milestone/` | `BoardStatus` ... `_run_board_async` (`orchestrate.py:2194-2784`). `_local_branch_exists` becomes public. |
| `resolver.py` (S4) | between `runs` and `bases`/`integration` | the one async, stop-aware conflict resolver |

### 6.3 `store.py` into a `store/` package

| Target module | Takes | Note |
|---|---|---|
| `store/db.py` | `_SCHEMA`, WAL setup, `_ADDED_COLUMNS` migrations, `open_db`, `immediate`, `BUSY_TIMEOUT_SECONDS`. `sqlite3.OperationalError` is translated to a `StoreBusyError` here. | the only `sqlite3` importer outside `store/` today is a violation (5.4) |
| `store/journal.py` | `Journal`, `JournalLine`, `EventKind`, `JournalError` family, `_RETIRED_ATTEMPT_KEYS` | the schema-1 contract (§10.1) |
| `store/replay.py` | `replay`, `diverging`, `Mismatch`, `ProjectionDivergedError`, `_walk` and its helpers | pure over journal lines and rows |
| `store/queries.py` | `RunSummary`, `RunProgress`, `list_runs`, `latest_run_id`, `load_run`, `run_status` | read models; take a connection |
| `store/leases.py` | `RunLease`, `LeaseRow`, `ClaimRow`, `ControlRow`, their readers, `claim_conflicts`, `held_claims`, `control_requests`, `add_control`, lease/claim errors, the SQL that `take_lease`/`beat`/`adopt_lease` run | SQL helpers never commit |
| `store/checkpoints.py` | `TurnFloor`, `Checkpoint`, readers, the SQL that `save_checkpoint` runs | SQL helpers never commit |
| `store/outbox.py` | `CommentRow`, `COMMENT_ATTEMPTS`, the SQL behind enqueue/pending/mark | SQL helpers never commit |
| `store/writer.py` | `Store` | see §6.4 |

### 6.4 What stays in one piece

- **`Store`** keeps every write method, together with its `RLock`, its bound lease token and `_fenced()`. That covers the run-tree `record_*` methods, `save_checkpoint`, the comment outbox writes, the lease writes and `rebuild_from_journal`. Each `record_*` holds the lock and the fence across both the journal append and the row write (`store.py:1582-1647`). The lease token is bound by `take_lease` and checked by every fenced write. Splitting writers across classes would split one critical section. Concern modules supply SQL and never commit. Only `Store` commits (`store.py:1568-1571`).
- **`lane` and `StoryRecorder`.** The order in which story and subtask rows are recorded, and the escalation path through `stop.trigger`, form one state machine.
- **`supervise`, `run_until_killed` and `build_dag_tree`.** These three own the grafo executor's lifetime and the workarounds for its hangs (S6).
- **`control.Lease`** and its heartbeat and watcher, with `run_lease`. Token binding into `Store` happens here.
- **`streams.py`.** The hello line, poll and follow of each stream form one protocol and change together.

### 6.5 Order of moves

Each step can ship on its own and leaves `uv run pytest` green. No step changes the envelope, exit codes, the journal or the workflow digest. Several steps are already named in the cleanup spec S1-S11.

- **M1.** Move `DEFAULT_TIMEOUT` to `models` as the default of `Dispatch.timeout` (today the literal `1800.0` appears twice: `models.py:67` and `dispatch.py:131`). `dispatch` and `workflow.task` read it from `models`. The value is unchanged, so the digest is unchanged. In the same step, add a reason comment to the lazy imports at `workflow/phases.py:93-94`.
- **M2.** Move `integration.merge_order` to `dag.merge_order` and delete the local import at `runs.py:282`.
- **M3.** Add `clock.utcnow` and replace the copies listed in 5.12.
- **M4.** Add `envelope.py`, and move error classes into `errors.py` (S7, no renames). `orchestrate` stops reading `cli.render`, `cli.ok_envelope`, `cli.error_envelope` and `cli.HANDLED`.
- **M5.** Move `refuse_claimed`, `run_lease` and the control request side into `control.py`. Add `StoreBusyError`, which ends the `sqlite3` imports outside `store`.
- **M6.** Move the subtask driver into `runs.py`. First, the six tests that patch `cli.run_direct` (§11.4) switch to injecting a `runner_factory`. This removes S1's `default_runner_factory` exemption.
- **M7.** Add `handoff.py`. After this step `orchestrate` reads nothing from `cli`: delete `orchestrate.py:65`'s `cli` import and `cli.py:1122`. This breaks the cycle.
- **M8.** Move `views.py` and `streams.py` out of `cli.py`.
- **M9.** Move `card_run.py`, `reset.py`, `resume.py` and `preview.py` out of `cli.py`. Repoint tests to the defining modules and delete the alias block at `cli.py:61-85`.
- **M10.** Split `orchestrate.py` into `milestone/` and `board_run.py` (S4 `resolver.py`, S6, S10 ride along).
- **M11.** Split `store.py` into `store/` (S2/S11 items ride along).
- **M12.** Remove the private-name uses in §11.3. S3's single gate evaluator retires `walk._render_error`.
- **M13.** `paths.py` derives paths only (S9): add `paths.runs_dir()` and `paths.ensure()`.

## 7. Where new code goes

| New ... | Goes in | Never in |
|---|---|---|
| CLI command | Options and rendering go in `cli.py`. Behaviour goes in one Application module (`views` if it only reads; `streams` if it streams). | Business logic in `cli.py` |
| Phase | Declare it in `workflow/<wf>.py`. A deterministic step is `steps/<name>.py` (L7/L8). An agent phase needs a role bundle, a result model in `results.py`, and its input names in `prompt.py`. | `runtime/` |
| Gate | `steps/reducers.py`. It must be pure. A check that needs I/O is a step. | an adapter, the runtime |
| Harness | `harness/<name>.py` implementing `harness.base.HarnessAdapter` (argv only), registered in `harness/registry.py` | spawning outside `harness/launcher.py` |
| Journal field | Add a payload key on the node model in `models.py`. It is additive under schema 1. A projection column goes in `store/db.py` `_ADDED_COLUMNS` plus `replay`. A new `JournalLine` field is a schema change (§10.1). | a row written without its journal line |
| `brd` interaction | `board.py` (argv plus parsing). A status write with rollup goes in `steps/rollup.py`. | any other module running `brd` |
| Status value | `models.Status` plus §9 plus the README journal table. A new run status is a contract change (§10.1). | a bare string literal |
| Use case | A new Application module. It takes its collaborators as parameters and has no `typer` and no `cli` import. | `cli.py` |
| Exception | `errors.py`. Its class name is the envelope's `error.type` (§10.2). | `cli.py` |
| Third-party library | one module, added to §5 | spread across modules |

## 8. Comments and docstrings

1. A docstring states the contract: what the function does, its inputs and outputs, its invariants, and what it raises. A module docstring says what the module owns, in a few lines.
2. A comment explains only a non-obvious *why*, in one or two lines.
3. Docstrings and comments contain no narrative: no spec or section citations (`§`, `D5`, `S1`, `T2`), no card ids, no "ported from X", no history, and no rationale walls. Those belong in the PR, the spec or the commit.
4. The existing narrative style is legacy. Remove it from a module when that module is touched for another reason, not in one sweep.

Bad (`bases.py:1-20`, first lines):

```python
"""Build a multi-blocker story's merged base from its blockers' tips.

Supervisor-tree design §5 (`docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`),
plan Task 2.1 (card 06bf46bb). A story blocked by two or more in-milestone
stories roots on `dag.base_branch_name`, a branch this module builds: ...
```

Good (`dag.py:46`):

```python
    """First eight hex characters of a card UUID, dashes stripped, lowercased."""
```

## 9. Status vocabulary

1. The vocabulary is `canceled` (American spelling), matching `brd`'s card status (`census.py:45`).
2. The run status `cancelled` (16 string literals, measured) is not a free rename. It is part of the journal (`run_upsert` `payload.status`, the README journal table), stored state (`runs.status`, filtered at `store.py:1957,1964`), the envelope (the `cancelled: true` key at `orchestrate.py:184`, `status` at `cli.py:3295`, `BoardStatus` at `orchestrate.py:2194`), and comment identity (`comments.py:235`).
3. The compatibility plan, landed in one change:
   - `models` holds two constants, `CANCELED = "canceled"` and `LEGACY_CANCELED = "cancelled"`. `Status` accepts both, and a validator normalises `cancelled` to `canceled` on read, so code only ever sees `canceled`.
   - Writers write `canceled`. Journals are append-only and never rewritten, so the read normalisation is permanent.
   - SQL filters match both values until a projection rebuild normalises the rows.
   - The comment idempotency key keeps the literal `cancelled`, because changing it would re-post comments that were already posted.
   - The README lists `canceled` and notes that older journals carry `cancelled`. Whether the `am watch` hello schema is bumped, and how the envelope key changes, are §13 D10 and D11.

## 10. Frozen contracts

These outrank every layering move. A move that would change one of them is not a layering move: it needs its own compatibility plan.

1. **Journal line, schema 1** (`JournalLine`, `store.py:321-338`; `extra="forbid"`), its event kinds, `(run_id, seq)` cursoring, and the `am watch` / `am logs --follow` streams with their hello `schema: 1` lines (`cli.py:2404-2412`).
2. **The envelope** `{"ok": true, "data"}` / `{"ok": false, "error": {"type", "message"}}`, and exit codes 0 / 1 / 3 (2 is Typer's usage errors). `error.type` is the exception's class name (`cli.py:183-186`), so moving an exception class is safe and renaming one is a contract change.
3. **Workflow identity.** The digest names callables by `module.qualname` (`workflow/phases.py:67-68,131-149`), and the checkpoint pool tags pydantic models as `module:qualname` (`runtime/context.py:31`). Moving or renaming any step, gate, `when` predicate, or result model in `results.py` that a shipped workflow references invalidates resumes of checkpointed runs.
4. **Write order and fencing.** The journal line is written before the row. Every run write is fenced by the lease token (§6.4).
5. **`steps/reducers.py` behaviour**, including the camelCase/snake_case dual read (S11).
6. **The documented data-dir layout** (`<data dir>/runs/<run-id>/{journal.jsonl,run.log,report.json}`, README). Inferred: it is not in the brief's list, but the README documents it to users.

## 11. Known exceptions

This list may only shrink. Each entry must disappear when the named step of §6.5 lands.

### 11.1 Direction and cycles (measured; the only §4.1 violations)

| Where | Import | Reason today | Gone at |
|---|---|---|---|
| `orchestrate.py:65` | `cli`, module-level (used at `:364,856,1625,1632,1715,1882,2186,2454,2658-2661`) | reuses the driver, lease, hand-off and envelope helpers | M7 |
| `runs.py:282` | `integration`, function-local, hides a cycle | `merge_order` for the dry-run plan | M2 |
| `workflow/task.py:27,31` | `dispatch` | `DEFAULT_TIMEOUT` | M1 |

### 11.2 Function-local imports (§4.3)

| Where | Problem | Gone at |
|---|---|---|
| `cli.py:1122` | duplicates the module-level `cli.py:47` import, and its comment claims a cycle | M7 |
| `workflow/phases.py:93-94` | downward and lazy on purpose, but no reason comment | M1 |

### 11.3 Private names across modules (§4.6)

| Where | Name | Gone at |
|---|---|---|
| `bases.py:40` | `steps.integrate._ref_exists` | M12 |
| `cli.py:1351` | `orchestrate._local_branch_exists` | M10 |
| `steps/integrate.py:32` | `steps.worktree._is_registered`, `_required_absolute`, `_required_name` | M12 |
| `dispatch.py:446,643`, `runtime/compile.py:186`, `runtime/engine.py:277,340` | `walk._render_error` | M12 (S3) |
| `runtime/engine.py:90,129`, `runtime/checkpoint.py:61` | `walk._utcnow` | M3 |
| `runtime/engine.py:172,309,321,334,353` | `walk._document_paths`, `_stop`, `_escalate`, `_record_subtask_status` | M12 |

### 11.4 Re-exports, confinement and test seams

| Where | Problem | Gone at |
|---|---|---|
| `cli.py:61-85` | `cli.X is runs.X` aliases (§4.5) | M9 |
| `cli.py:24,3011,3062,3129` | `sqlite3` and a transaction outside `store` (5.4) | M5 |
| `control.py:20,210,251` | catches `sqlite3.OperationalError` (5.4) | M5 |
| 5.12 sites | wall clock outside `clock` | M3 |
| `cli.py:2411`, `store.py:406`, `paths.py:18,33,57` | hand-joined data path; `mkdir` in derivation (5.11) | M13 |
| `tests/e2e/test_live_control.py:181`, `test_milestone_resume.py:241`, `test_milestone_run.py:229,356`, `tests/test_cli.py:2254,7679` | patch `cli.run_direct` instead of injecting (5.14) | M6 |

## 12. How this will be enforced

Design only. The architecture test is written after this document is agreed. It scans `src/agent_manager/` and ignores `docs/superpowers/`.

| Rule | Kind | Check |
|---|---|---|
| §3.1 complete | mechanical | every `.py` under `src/agent_manager/` appears exactly once in the table, which the test parses or mirrors |
| §4.1, §4.2, §4.4 | mechanical | AST import graph, counting module-level, function-local and `TYPE_CHECKING` edges, checked against §3.1 |
| §4.3 | mechanical | a function-local import points downward and has a comment on the line above |
| §4.5, §4.6 | mechanical | an `ImportFrom` or attribute of a `_name` across modules; an imported name that other modules read off this one |
| §5.1-5.13 | mechanical | per-library allowlists on imports; the string `"brd"` as an argv head, `datetime.now`, and `mkdir` allowlists |
| §11 | mechanical (ratchet) | the exceptions are an allowlist. A new violation fails. A fixed one must be removed from the list. |
| §3.1 band rules, §6, §7 | judgment (audit) | placement of new code, purity of the Core band |
| §8 | judgment (audit), partly greppable | `§`, `card [0-9a-f]{8}`, "ported from" in docstrings |
| §9, §10 | judgment (review) | any diff touching the journal, envelope, `steps/`, `workflow/`, `results.py` |

## 13. Open decisions

| # | Decision | Recommendation | Alternative |
|---|---|---|---|
| D1 | Same-layer imports | Forbidden: siblings are independent and layers are fine-grained (19 levels) | Allow imports within a band in a declared order (fewer, coarser layers) |
| D2 | Home of `DEFAULT_TIMEOUT` | `models`, beside `Dispatch.timeout`'s default (which duplicates it) | `harness/launcher.py` (the process it bounds) or `workflow/phases.py` |
| D3 | `comments` placement | Keep it whole in Application (L15) | Split the pure `compose_*`/`key` into Core and keep the outbox in Application, with `SubtaskSummary` moved down to `models` |
| D4 | `dispatch` location | Stay `agent_manager/dispatch.py` at L12 | Move it into `runtime/` (it imports no pygents) |
| D5 | Packages for the splits | `store/` and `milestone/` packages imported by leaf module (§4.5); `bases`/`integration` stay flat | Flat sibling modules (`journal.py`, `milestone_lane.py`, ...), or also move `bases`/`integration` into `milestone/` |
| D6 | `cli.py` shape after the split | One module (Typer only, estimated about 900 lines) | A `cli/` package split by command group |
| D7 | `Store` write discipline | `Store` keeps every write; concern modules supply SQL and never commit | Per-concern writer classes sharing one lock/fence object |
| D8 | Private names inside a sub-package | Forbidden everywhere (rename to public) | Allowed within `runtime/` and within `steps/` |
| D9 | Clock seam | `clock.utcnow` plus `now` parameters at use-case entry | A `Clock` protocol injected everywhere |
| D10 | `canceled` on the journal wire | Write `canceled` and bump the `am watch` hello to `schema: 2` in the same release; readers accept both forever | Keep schema 1 and document the new value, or keep `cancelled` on the wire and use `canceled` only inside the code |
| D11 | Envelope key `cancelled: true` | Switch to `canceled: true` with D10, no dual keys | Emit both keys for one release |
| D12 | S7's `InvalidCardIdError` | Accept it: `error.type` for a bad card id changes from `ValueError`. Document it as a contract change. | Keep raising `ValueError` and narrow `HANDLED` another way |
| D13 | `CLAUDE.md` "source of truth" line | Point it at this document for layering, in this PR once agreed | Leave `CLAUDE.md` as is |
| D14 | `runtime.walk` → `store` | Keep the concrete import; type it through S8's `RunDeps` Protocol later | Make Runtime import no adapter at all (move the `record_*` helpers into the Application band) |
