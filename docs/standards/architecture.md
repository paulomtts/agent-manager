# Architecture: module layering

## 1. Purpose and status

- This is the layering standard for `src/agent_manager/`. It decides which module may import which, where each kind of code lives, and which libraries and processes sit behind which module. It is the source of truth for layering.
- Specs and plans under `docs/superpowers/` are local, untracked working history. They are not a source of truth, and architecture guards and audits ignore them. Where this document names a cleanup item (`S1`-`S11`), the step it appears in says everything that is needed.
- Rules are cited by section number (for example "§4.3"). A rule cites the code it responds to as `file:line`, measured against `master` at `3aa57bf` (2026-10-04). Line numbers drift, so re-read the code before acting on one.
- "Measured" means it was read from the AST import graph or with `wc`/`git`. "Inferred" means it is a judgment.
- The decisions behind this document are recorded in §13. Until the moves in §6.5 land, the current violations are the known exceptions in §11.

## 2. The organizing idea

A workflow engine built as a functional core inside an imperative shell:

- **Declared data.** A workflow is data (`workflow/`), checked and digested in `workflow/phases.py`.
- **Interpreter.** An engine walks that data (`runtime/`, the only home of pygents). The agent-phase runner (`dispatch.py`) is injected into it.
- **Pure core.** Card derivations (`census`, `dag`), prompt resolution (`prompt`), result models (`results`) and pure gates (`steps/reducers.py`).
- **Deterministic steps.** `steps/` holds git, verify and board writes. There are no model calls there.
- **Truth and projection.** An append-only journal is the truth, with a SQLite projection (`store/`) next to it. Every write appends the journal line first, then writes the row, and is fenced by the lease.
- **Thin adapters at the edges.** `board.py` (the `brd` CLI), `harness/` (agent processes), `locks.py`, `detach.py`, `argv_guard.py`.
- **Use cases above all of it.** Run, resume, milestone and board runs, Integrate. A Typer shell (`cli/`) sits on top of the use cases and only parses options and renders output.

## 3. Layer order

### 3.1 The target table

Layers are listed from lowest to highest. **A module may import only modules in a strictly lower layer (§4.1).** Modules in the same layer are siblings and do not import each other.

New modules are those §6 creates. Their positions follow the imports their code makes today. They are inferred, and each one is confirmed against the import graph when it lands (§4.8). Every package's `__init__.py` is L0 and contains no code.

| L | Band | Modules | Responsibility |
|---|---|---|---|
| 0 | Kernel | every `__init__.py` (the root one holds `__version__`), `models`, `errors` (absorbs `runtime.errors`), `paths`, `runtime.stop`, `harness.limits`, *`clock`* | Types, errors, path derivation, the stop signal, the generic usage-limit hit, the wall clock. No `agent_manager` imports. |
| 1 | Core | `census`, `results`, `roles.loader`, `steps.reducers`, `harness.claude_limits` | Pure derivations, result models, role bundles, pure gates; the Claude adapter's usage-limit line parser |
| 2 | Core | `dag` | Card identity, branch names, levels, stacking, `merge_order` |
| 3 | Core | `prompt` | Resolves a phase's inputs and renders its prompt |
| 4 | Core | `workflow.phases` | The phase model: frozen data, `validate()`, `digest()` |
| 5 | Adapters | `locks`, `detach`, `argv_guard`, `harness.base`, `harness.claude`, *`store.db`*, *`store.journal`* | File locks; process fork; re-exec with a neutral argv; the harness Protocol (with its optional `limit_hit`) and Claude argv; the SQLite connection and DDL; the journal (schema 1) |
| 6 | Adapters | *`store.replay`*, *`store.queries`*, *`store.leases`*, *`store.checkpoints`*, *`store.outbox`*, *`store.projects`*, *`store.events`* | Replay and divergence; read models; per-table row types and SQL; `projects` rows resolved or created by `repo_dir`; append-only `events` rows: insert, reads by `seq`, head (they never commit) |
| 7 | Adapters | *`store.writer`* | `Store`: every write, as a job on one writer thread, under the fence (§6.4) |
| 8 | Adapters | `board`, `control`, `harness.launcher`, `harness.registry` | `brd`; the lease, claims and controls, both ends; process launch; harness lookup |
| 9 | Steps | `steps.worktree`, `steps.verify`, `steps.plan_check`, `steps.rollup` | Deterministic steps |
| 10 | Steps | `steps.docs_commit`, `steps.integrate` | Steps built on `steps.worktree` |
| 11 | Workflow | `workflow.task` | The shipped `task` workflow, as data |
| 12 | Workflow | `workflow.integrate` | The shipped `integrate` workflow |
| 13 | Runtime | `runtime.walk`, `runtime.bridge`, `runtime.state` | What the walk shares below pygents; the thread/process bridge; `RunDeps` |
| 14 | Runtime | `runtime.context`, `dispatch` | The pool codec; the agent-phase runner |
| 15 | Runtime | `runtime.checkpoint`, `runtime.compile` | Checkpoint hooks; the workflow compiled into pygents tools |
| 16 | Runtime | `runtime.engine` | One subtask, one agent, one loop |
| 17 | Application | `runs`, `comments`, *`envelope`* | Run identity and the shared subtask driver; the comment outbox; the envelope and exit-code contract |
| 18 | Application | *`handoff`*, *`resolver`*, *`reset`* | The generic detach hand-off; the one conflict resolver; `am reset` |
| 19 | Application | `bases`, `integration`, *`card_run`* | The merged base; Integrate; the `--card` run |
| 20 | Application | *`milestone.plan`*, *`milestone.payloads`* | Pure story plan; lane outcome types and report shapes |
| 21 | Application | *`milestone.lane`* | The per-story state machine |
| 22 | Application | *`milestone.schedule`*, *`milestone.reopen`* | grafo scheduling; reopening a milestone run |
| 23 | Application | *`milestone.run`* | The `--milestone` run |
| 24 | Application | *`board_run`* | The `--board` run (milestone stacking) |
| 25 | Application | *`resume`*, *`preview`* | `am resume` for both run kinds; `--dry-run` |
| 26 | Interface | *`cli.views`*, *`cli.output`*, *`cli.options`* | Read views for `status`/`runs`/`logs`; envelope to stdout plus exit-code mapping; shared Typer options |
| 27 | Interface | *`cli.streams`* | The `am watch` and `am logs --follow` protocols |
| 28 | Interface | *`cli.run_command`*, *`cli.read_commands`*, *`cli.control_commands`* | `run`; `status`/`runs`/`logs`/`watch`; `resume`/`pause`/`cancel`/`reset` |
| 29 | Interface | *`cli.app`* | `app = typer.Typer()`, the `main` callback, command registration; the `am` entry point |

Band rules (inferred, and they are what the table encodes):

- **Core** (L1-L4) never imports any adapter, does no SQLite work, and spawns no process. It reads files only at paths it is handed or in shipped package data. `prompt.py:97` writes only to a path its caller passes in.
- **Steps** may use adapters, never the runtime.
- **Workflow** is data. It never imports `runtime/` or `dispatch`.
- **Runtime** imports `workflow.phases` only, never a concrete workflow.
- **Application** never imports `typer` or `cli`.
- **Interface** contains only `cli/`. Each command module calls use cases and renders their results. It holds no business logic.

### 3.2 Today's files on the target order (measured)

All 62 `.py` files (55 modules plus 7 `__init__.py`) appear here exactly once. `find src/agent_manager -name '*.py'` confirms the count. A file that §6 splits is placed at the layer of its highest part. Checked against the AST import graph (module-level, function-local and `TYPE_CHECKING` edges), the only imports that violate this order are the three in §11.1.

| L | Current files |
|---|---|
| 0 | `__init__`, `harness/__init__`, `roles/__init__`, `runtime/__init__`, `steps/__init__`, `store/__init__`, `workflow/__init__`, `models`, `errors`, `runtime.errors`, `paths`, `runtime.stop`, `harness.limits` |
| 1-4 | `census`, `results`, `roles.loader`, `steps.reducers`, `harness.claude_limits` (L1); `dag` (L2); `prompt` (L3); `workflow.phases` (L4) |
| 5 | `locks`, `detach`, `argv_guard`, `harness.base`, `harness.claude`, `store.db`, `store.journal` |
| 6 | `store.replay`, `store.queries`, `store.leases`, `store.checkpoints`, `store.outbox`, `store.projects`, `store.events` |
| 7 | `store.writer` |
| 8 | `board`, `control`, `harness.launcher`, `harness.registry` |
| 9-10 | `steps.worktree`, `steps.verify`, `steps.plan_check`, `steps.rollup` (L9); `steps.docs_commit`, `steps.integrate` (L10) |
| 11-12 | `workflow.task` (L11); `workflow.integrate` (L12) |
| 13-16 | `runtime.walk`, `runtime.bridge`, `runtime.state` (L13); `runtime.context`, `dispatch` (L14); `runtime.checkpoint`, `runtime.compile` (L15); `runtime.engine` (L16) |
| 17 | `runs`, `comments` |
| 19 | `bases`, `integration` |
| 24 | `orchestrate` (splits into L19-L24) |
| 29 | `cli` (splits into L17-L29) |

### 3.3 Moves relative to the measured depth

"Measured" here is the longest-path depth in the import DAG, with `cli` and `orchestrate` collapsed into one node because of their cycle.

| Module | Measured | Target | Why |
|---|---|---|---|
| `workflow.task`, `workflow.integrate` | above `dispatch` | below all of Runtime | `workflow/task.py:27,31` imports `dispatch` only for `DEFAULT_TIMEOUT`: declared data depending on a runner (M1) |
| `runs` | in a cycle with `integration` | below `bases`/`integration` | `runs.py:282` imports `integration` function-locally for `merge_order`, a pure function of `dag` (`integration.py:78-97`) (M2). The comment at `runs.py:279-281` gives a reason that no longer holds. |
| `bases` | one level above `integration` | sibling of `integration` | It sat higher only because of the `runs` cycle |
| `comments` | beside `dispatch` | Application | Only `cli` and `orchestrate` use it. Its `TYPE_CHECKING` import of `runtime.walk.SubtaskSummary` (`comments.py:29`) makes it depend on Runtime, so it belongs above Runtime. |
| `control` | L2 | L8, above `store.writer` | It owns the lease side of the store and gains the CLI's request side (§6.1) |
| `detach` | L1 | Adapters | A process adapter (`detach.py:149`). The lease hand-off around it moves to `handoff` |
| `dag`, `prompt`, `workflow.phases` | beside the adapters | Core, below all adapters | None of them imports an adapter (measured) |
| `runtime.state` | L1 | Runtime | Only Runtime reads it |
| `orchestrate` / `cli` | one cycle at the top | Application / Interface | `orchestrate.py:65` imports `cli` at module level (M4-M7) |

## 4. Import rules

1. **Direction.** A module imports only modules in a strictly lower layer of §3.1. Siblings in the same layer do not import each other.
2. **No module-level cycles.** The import graph is a DAG at module level.
3. **Function-local imports** are allowed only when the target is in a strictly lower layer **and** a one-line comment states the reason (import cost, optional dependency). A function-local import must never hide a cycle.
4. **`TYPE_CHECKING` imports** obey §4.1 like any other import.
5. **No re-exports.** Import a name from the module that defines it. A module does not import a name so that others can read it off that module. The `cli.X is runs.X` block (`cli.py:61-85`) is legacy. Package `__init__.py` files contain no code and re-export nothing. Callers import leaf modules (`agent_manager.store.writer`, not `agent_manager.store`).
6. **Private names stay private.** A name starting with `_` is never imported from, or read off, another module, including within one sub-package. If another module needs it, make it public or move it.
7. **Workflow identity.** Before moving or renaming anything that `workflow/task.py` or `workflow/integrate.py` references, see §10.3.
8. **The table is the source.** A PR that adds, moves or deletes a module updates §3.1 and §3.2 in the same PR.

## 5. Library and process confinement

| # | Only here | What | Measured today |
|---|---|---|---|
| 5.1 | `runtime/{checkpoint,compile,context,engine}.py` | `import pygents` | holds |
| 5.2 | `milestone/schedule.py` (today `orchestrate.py`) | `import grafo` | holds (`orchestrate.py:59`) |
| 5.3 | `cli/` | `import typer` | holds (`cli.py:34`) |
| 5.4 | `store/` | `import sqlite3`, SQL, transaction boundaries (`immediate`, `commit`) | **violated:** `cli.py:24,3129` |
| 5.5 | `board.py` | builds or runs a `brd` argv | holds (`board.py:44-135`) |
| 5.6 | `harness/launcher.py` | spawns a harness (`subprocess.Popen`). `harness/<name>.py` only builds the argv. | holds (`harness/launcher.py:150`) |
| 5.7 | `steps/worktree.py` (`run_git`) | runs `git`. Everyone else calls `worktree.run_git`. | holds |
| 5.8 | `steps/verify.py` | runs the card's verification commands | holds |
| 5.9 | `detach.py` | `os.fork`, `os.setsid` | holds (`detach.py:149,170`) |
| 5.10 | `locks.py` | `fcntl` | holds |
| 5.11 | `paths.py` | derives every path under `<data dir>`. Directories are created only by `paths.ensure`. | **violated:** `cli.py:2411` and `store.py:406` build paths by hand; `paths.py:18,33,57` call `mkdir` |
| 5.12 | `clock.py` | reads the wall clock (`datetime.now`). Use cases take `now` or a clock callable. | **violated:** `_utcnow` copies at `cli.py:201`, `orchestrate.py:310`, `control.py:43`, `dispatch.py:323`, `runtime/walk.py:220`; inline reads at `comments.py:391`, `store.py:528` and `store/writer.py` `Store.open` |
| 5.15 | `argv_guard.py` | `os.execve` | holds (`argv_guard.py:91`) |

Other rules:

- **5.13** `subprocess` may be imported only by the modules in 5.5-5.8. `runtime/bridge.py` may also import it, but only to name `subprocess.Popen` as a type.
- **5.14** Seams, and how a test substitutes each one:

| Seam | Production | Test fake | How it gets in |
|---|---|---|---|
| Board | `board` functions | fake `board_api` / `FakeBoard` | a parameter; `milestone.run.Collaborators.board` |
| Launcher | `harness.launcher.run_direct` | `FakeLauncher` | `RunnerFactory` (`runs.py:110`). Patching `cli.run_direct` is legacy (§11.4). |
| Agent-phase runner | `dispatch.AgentRunner` | `FakeDriver`, fake runner | `RunnerFactory`, `driver=` |
| Store | `store.writer.Store` | the real `Store` under `tmp_path` | a parameter. There is no fake store: the journal-then-row rule is tested on the real one. |
| Clock | `clock.utcnow` | a fixed `now` | a parameter at the use-case entry |
| Git | `worktree.run_git` | real git in `tmp_path` | `Collaborators.run_git` |

## 6. Target decomposition of the three large modules

Measured: `cli.py` has 3329 lines, `orchestrate.py` 2784 and `store.py` 2352. Together they are 45% of the package's 18608 lines, with 118, 87 and 54 commits. Everything below is a destination, not a migration plan. Positions refer to §3.1.

### 6.1 `cli.py` into use cases and a `cli/` package

| Target module | L | Takes from `cli.py` |
|---|---|---|
| `clock` | 0 | `_utcnow` (and the copies listed in 5.12) |
| `errors` | 0 | every `CliError` subclass (`cli.py:93-176`, and `runs.py`'s), merged with `runtime/errors.py`. Class names unchanged (§10.2). |
| `control` | 8 | `refuse_claimed`, `run_lease`, `_claimed_error`, `_run_is_live_error`, and the request side (`CONTROL_*`, `_controllable_lease`, `_record_control`, `request_control`, ...) |
| `runs` | 17 | the shared subtask driver: `default_runner_factory`, `SubtaskDrive`, `drive_subtask_async`, `drive_subtask` (`cli.py:672-805`) |
| `envelope` | 17 | `ok_envelope`, `error_envelope`, `render`, `EXIT_ESCALATED`, `EXIT_ERROR`, `HANDLED`, `WATCH_HANDLED`. It sits below the Interface band because detached children write `report.json` with the same envelope (§13 D6). |
| `handoff` | 18 | `release_handed_off`, `run_detached_child`, `hand_off_to_child` (`cli.py:1411-1496`) |
| `reset` | 18 | `reset_run` and its messages (`cli.py:3187-3307`) |
| `card_run` | 19 | `card_run_status`, `card_outcome_comment`, `CardPreflight`, `preflight_card`, `RecordedRun`, `recorded_card_run`, `run_card_engine`, `run_card`, `detach_card` |
| `resume` | 25 | `checkpoint_resume_phase`, `_resume_from_checkpoint`, `resume_run` (`cli.py:608-649, 2724-2946`) |
| `preview` | 25 | `already_done_entries` ... `dry_run_board` (`cli.py:1207-1378`), plus `runs.DryRunPlan` and `compute_dry_run_plan` |
| `cli/views.py` | 26 | `RUN_IDENTITY` ... `step_logs_payload` (`cli.py:205-605`, without `checkpoint_resume_phase`), `status_for`, `runs_for`, `LogsSelection`, `select_logs`, `logs_for`, `logs_end_status` |
| `cli/output.py` | 26 | printing an envelope (`--pretty`) and mapping `HANDLED` to exit 3. The only exit-code site. |
| `cli/options.py` | 26 | shared `typer.Option`/`Argument` declarations (`--repo-dir`, `--pretty`, ...) and `RUN_EXAMPLES` |
| `cli/streams.py` | 27 | the watch and logs-follow protocols: `cli.py:2301-2660` (hello lines, poll/follow, UTF-8 chunking, `WATCH_*`) |
| `cli/run_command.py` | 28 | `run`, `_check_run_targets` (`cli.py:1548-1861`) |
| `cli/read_commands.py` | 28 | `status`, `runs`, `logs`, `watch` |
| `cli/control_commands.py` | 28 | `resume`, `pause`, `cancel`, `reset` |
| `cli/app.py` | 29 | `app`, `main`, registration. The entry point becomes `am = "agent_manager.cli.app:app"`. |

### 6.2 `orchestrate.py` into a `milestone/` package and `board_run.py`

| Target module | L | Takes |
|---|---|---|
| `comments` | 17 | `post_comment`, `post_comment_async`. A `--card` run uses them too (`cli.py:1124`), so they cannot live in `milestone/`. |
| `resolver` | 18 | the one async, stop-aware conflict resolver for `bases` and `integration` (S4) |
| `milestone/plan.py` | 20 | `PlannedStory`, `plan_levels`, `story_tips`, `SupervisorPlan`, `supervisor_plan`, `builds_a_base_alone`, `milestone_claims`, `milestone_card_ids` |
| `milestone/payloads.py` | 20 | `LaneKind`, `LaneOutcome`, `LaneEscalated`, `LaneStopped`, `stopped_row`, `escalation_row`, `*_payload`, `with_bases` (`orchestrate.py:92-307`) |
| `milestone/lane.py` | 21 | `Driver`, `StoryRecorder`, `lane`, `base_only_lane`, `build_merged_base`, `collect_outcomes` |
| `milestone/schedule.py` | 22 | `GRAFO_LOGGER`, `run_until_killed`, `build_dag_tree`, `supervise`, `GRAFO_NODE_TIMEOUT`, the logger-silencing context manager (S6) |
| `milestone/reopen.py` | 22 | `REOPENED_STATUSES`, `resumable_milestone_run`, `find_run_milestone`, `open_cards`, `_refuse_changed_workflow`, `resume_point`, `resume_checkpoints`, `reopen_rows`, `stale_story_anchors`, `reroll_stale_stories`, `refresh_git` |
| `milestone/run.py` | 23 | `MILESTONE_WORKFLOW`, `MilestonePreflight`, `preflight_milestone`, `RecordedMilestoneRun`, `recorded_milestone_run`, `record_plan`, `run_milestone_engine`, `run_milestone`, `_in_thread_to_completion`, `_run_milestone_async`, `detach_milestone`, `Collaborators` (S10) |
| `board_run.py` | 24 | `BoardStatus` ... `_run_board_async` (`orchestrate.py:2194-2784`). `_local_branch_exists` becomes public. |

### 6.3 `store.py` into a `store/` package

| Target module | L | Takes | Note |
|---|---|---|---|
| `store/db.py` | 5 | `_SCHEMA`, WAL setup, `_ADDED_COLUMNS`, `open_db`, `immediate`, `iso`, `BUSY_TIMEOUT_SECONDS`, `STORE_ID_KEY`, `store_id`, the retry primitive `run_with_retry` with `RETRY_ATTEMPTS`, `RETRY_DEADLINE_SECONDS`, `RETRY_FIRST_PAUSE` and `RETRY_PAUSE_CAP`, and a `StoreBusyError` that replaces `sqlite3.OperationalError` | |
| `store/journal.py` | 5 | `Journal`, `JournalLine`, `EventKind`, `NODE_KINDS`, the `JournalError` family | the schema contract (§10.1) |
| `store/replay.py` | 6 | `replay`, `diverging`, `Mismatch`, `ProjectionDivergedError`, `_RETIRED_ATTEMPT_KEYS`, `_walk` and its helpers | pure over journal lines and rows |
| `store/queries.py` | 6 | `RunLease`, `RunSummary`, `RunProgress`, `ProgressCount`, `ProgressCurrent`, `list_runs`, `latest_run_id`, `load_run`, `run_status` | take a connection |
| `store/leases.py` | 6 | `LeaseRow`, `ClaimRow`, `ControlRow`, `LeaseTake`, their readers, `claim_conflicts`, `held_claims`, `control_requests`, `add_control`, the lease/claim errors, the SQL behind lease writes | never commits |
| `store/checkpoints.py` | 6 | `TurnFloor`, `Checkpoint`, readers, the SQL behind `save_checkpoint` | never commits |
| `store/outbox.py` | 6 | `CommentRow`, `COMMENT_ATTEMPTS`, the SQL behind enqueue/pending/mark | never commits |
| `store/projects.py` | 6 | `resolve`, `lookup`: the `projects` rows, resolved or created by `repo_dir` | never commits |
| `store/events.py` | 6 | `EventRow`, `insert`, `read`, `head`, `run_lines`, `journal_line`: the append-only `events` rows and the journal lines they are; the table's DDL and triggers live in `store/db.py` | never commits; imports only `store/journal.py` from the store |
| `store/writer.py` | 7 | `Store` | §6.4 |

### 6.4 What stays in one piece

- **`Store`** keeps every write method, together with its writer thread and job queue and its bound lease token. That covers the run-tree `record_*` methods, `save_checkpoint`, the comment outbox writes, the lease writes and `rebuild_from_events`. Each write is one job on the writer thread, one `BEGIN IMMEDIATE` transaction under the fence; heartbeat writes waiting together share one transaction, each in its own savepoint; each `record_*` job covers both the journal append and the row write (`store.py:1582-1647`). Only `Store` commits (`store.py:1568-1571`). A forked child gets an inert copy of every `Store` the parent still had open: its every write and read raises `sqlite3.ProgrammingError`, and the child opens a `Store` of its own.
- **`lane` and `StoryRecorder`.** The order in which story and subtask rows are recorded, and the escalation path through `stop.trigger`, form one state machine.
- **`supervise`, `run_until_killed` and `build_dag_tree`.** These own the grafo executor's lifetime and the workarounds for its hangs.
- **`control.Lease`** with its heartbeat, its watcher and `run_lease`. Token binding into `Store` happens here.
- **`cli/streams.py`.** Each stream's hello line, poll and follow are one protocol.

### 6.5 Order of moves

Each step can ship on its own and leaves `uv run pytest` green. No step changes the envelope, exit codes, the journal or the workflow digest. The `canceled` migration is separate (§9).

- **M1.** Move `DEFAULT_TIMEOUT` to `models` as the default of `Dispatch.timeout` (the literal `1800.0` is at both `models.py:67` and `dispatch.py:131`). The value is unchanged, so the digest is unchanged. Add a reason comment to `workflow/phases.py:93-94`.
- **M2.** Move `integration.merge_order` to `dag.merge_order` and delete `runs.py:282`.
- **M3.** Add `clock.utcnow` and replace the copies listed in 5.12.
- **M4.** Add `envelope.py`, and move the error classes into `errors.py` (no renames). `orchestrate` stops reading `cli.render`, `cli.ok_envelope`, `cli.error_envelope` and `cli.HANDLED`.
- **M5.** Move `refuse_claimed`, `run_lease` and the control request side into `control.py`. Add `StoreBusyError`.
- **M6.** Move the subtask driver into `runs.py`. First switch the six tests in §11.4 from patching `cli.run_direct` to injecting a `runner_factory`.
- **M7.** Add `handoff.py`, and move `post_comment(_async)` into `comments.py`. `orchestrate` then reads nothing from `cli`: delete `orchestrate.py:65` and `cli.py:1122`. This breaks the cycle.
- **M8.** Turn `cli.py` into the `cli/` package: `app.py`, `options.py`, `output.py`, `views.py`, `streams.py`, and the three command modules, with the entry point repointed. Tests that patch `cli.X` move to the defining module.
- **M9.** Move `card_run.py`, `reset.py`, `resume.py` and `preview.py` out of `cli/`, and delete the alias block at `cli.py:61-85`.
- **M10.** Split `orchestrate.py` into `milestone/` and `board_run.py`, adding `resolver.py` and `Collaborators`.
- **M11.** Split `store.py` into `store/`.
- **M12.** Remove the private-name uses in §11.3. Having a single gate evaluator retires `walk._render_error`.
- **M13.** Make `paths.py` derivation-only: add `paths.runs_dir()` and `paths.ensure()`.

## 7. Where new code goes

| New ... | Goes in | Never in |
|---|---|---|
| CLI command | A function in a `cli/*_command(s).py` module, registered in `cli/app.py`. Behaviour goes in one Application module; a read-only view goes in `cli/views.py`; a stream goes in `cli/streams.py`. | Business logic in `cli/` |
| Phase | Declare it in `workflow/<wf>.py`. A deterministic step is `steps/<name>.py`. An agent phase needs a role bundle, a result model in `results.py`, and its input names in `prompt.py`. | `runtime/` |
| Gate | `steps/reducers.py`. It must be pure. A check that needs I/O is a step. | an adapter, the runtime |
| Harness | `harness/<name>.py` implementing `HarnessAdapter` (argv only), registered in `harness/registry.py` | spawning outside `harness/launcher.py` |
| Journal field | A payload key on the node model in `models.py` (additive). A column goes in `store/db.py` `_ADDED_COLUMNS` plus `store/replay.py`. A new `JournalLine` field is a schema change (§10.1). | a row written without its journal line |
| `brd` interaction | `board.py`. A status write with rollup goes in `steps/rollup.py`. | any other module running `brd` |
| Status value | `models.Status` plus the README journal table. A new run status is a contract change (§10.1). | a bare string literal |
| Use case | A new Application module that takes its collaborators as parameters | `cli/` |
| Exception | `errors.py`. Its class name is the envelope's `error.type` (§10.2). | `cli/` |
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

## 9. Status vocabulary: the `canceled` migration (landed)

The migration has landed. Items 1 and 2 are the rules in force; items 3-6 are the record of what changed and in what order.

1. **Spelling.** The canonical spelling is `canceled` everywhere, matching `brd` (`census.py:45`). New code writes only `canceled`.
2. **Reading.** Readers accept both `canceled` and `cancelled` forever, in stored journals, SQLite rows and checkpoint queries. Journals are append-only and `am watch` replays stored lines unchanged, so a consumer can still meet `cancelled` long after the switch.
3. **What changes in `agent-manager`** (16 string literals, measured):
   - `models.py:26`: `Status` takes both values, and a validator normalises `cancelled` to `canceled` on read. Add the constants `CANCELED` and `LEGACY_CANCELED`.
   - SQL that filters on the status (`store.py:1957,1964`) matches both values.
   - Comparisons and writes switch to `CANCELED`: `orchestrate.py:605,1844,2359-2360`, `cli.py:817,2915,3270,3272`, `comments.py:260,289`.
   - Envelope keys: `cancelled: true` becomes `canceled: true` (`orchestrate.py:184`). `"status": "cancelled"` and `already_cancelled` become `canceled` and `already_canceled` (`cli.py:3295-3296`). The `BoardStatus` value changes (`orchestrate.py:2194`). No release emits both spellings.
   - **Not changed:** the comment idempotency key at `comments.py:235` keeps the literal `cancelled`. Changing it would re-post comments that were already posted.
   - The `am watch` hello line moves to `"schema": 2` (`cli.py:2404-2412`). The `am logs --follow` stream carries attempt statuses only, so it stays at schema 1.
   - The README's journal table, report shapes and hello-line text are updated, with a note that older journals carry `cancelled`.
4. **What changes in `omarchy-project-manager`** (it reads run statuses and the watch hello line):
   - `core/backend/runs/runs-snapshot.py:33`: `TERMINAL` gains `canceled`.
   - `core/domain/runs.js:70` (`runState`) and `:455` (`glyphStateOf`) map both spellings to one state, and its `runGlyphs.js:11` key becomes `canceled`.
   - `core/backend/runs/runs-watch.py:121-125`: `check_schema` accepts schema 1 and schema 2.
   - The footer text `am · schema 1` shows the schema it read.
   - Tests pin both spellings and both schemas: `tests/core/domain/tst_runs.qml`, `tests/core/backend/runs/test_runs_snapshot.py`, `tests/contract/test_am_shapes.py`.
5. **`leave-me-alone`** was checked at `6d21b88`. It reads neither run statuses nor the watch hello line, so it needs no change.
6. **Order.** Each step is released before the next starts, so no consumer ever meets a value it rejects:
   1. **Readers in the plugin** (§9.4): released first.
   2. **Readers in `am`**: both values are accepted on input, while output still writes `cancelled`.
   3. **Writers in `am`**: `canceled` is written to the journal, rows and envelopes, and stored values are normalised on read.
   4. **The hello schema bump in `am`**: last.

   Steps 3 and 4 ship in the same `am` release, with step 4 as its last commit. That way no released `am` writes `canceled` under a schema-1 hello.

## 10. Frozen contracts

These outrank every layering move. A move that would change one of them is not a layering move: it needs its own compatibility plan, as §9 has.

1. **The journal line** (`JournalLine`, `store.py:321-338`; `extra="forbid"`), its event kinds, `(run_id, seq)` cursoring, and the `am watch` / `am logs --follow` streams with their hello lines. The watch hello is `schema: 2`; the `am logs --follow` hello stays `schema: 1`.
2. **The envelope** `{"ok": true, "data"}` / `{"ok": false, "error": {"type", "message"}}`, and exit codes 0 / 1 / 3 (2 is Typer's usage errors). `error.type` is the exception's class name (`cli.py:183-186`), so moving an exception class is safe and renaming one is a contract change.
3. **Workflow identity.** The digest names callables by `module.qualname` (`workflow/phases.py:67-68,131-149`), and the checkpoint pool tags pydantic models as `module:qualname` (`runtime/context.py:31`). Moving or renaming any step, gate, `when` predicate, or result model in `results.py` that a shipped workflow references invalidates resumes of checkpointed runs.
4. **Write order and fencing.** The journal line is written before the row. Every run write is fenced by the lease token (§6.4).
5. **`steps/reducers.py` behaviour**, including the camelCase/snake_case dual read.
6. **The documented data-dir layout** (`<data dir>/runs/<run-id>/{journal.jsonl,run.log,report.json}`, documented in the README). Inferred: the README documents it to users.

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
| `dispatch.py:446,643`, `runtime/compile.py:186`, `runtime/engine.py:277,340` | `walk._render_error` | M12 |
| `runtime/engine.py:90,129`, `runtime/checkpoint.py:61` | `walk._utcnow` | M3 |
| `runtime/engine.py:172,309,321,334,353` | `walk._document_paths`, `_stop`, `_escalate`, `_record_subtask_status` | M12 |

### 11.4 Re-exports, confinement and test seams

| Where | Problem | Gone at |
|---|---|---|
| `cli.py:61-85` | `cli.X is runs.X` aliases (§4.5) | M9 |
| `cli.py:24,3011,3062,3129` | `sqlite3` and a transaction outside `store` (5.4) | M5 |
| 5.12 sites | wall clock outside `clock` | M3 |
| `cli.py:2411`, `store.py:406`, `paths.py:18,33,57` | hand-joined data path; `mkdir` in derivation (5.11) | M13 |
| `tests/e2e/test_live_control.py:181`, `test_milestone_resume.py:241`, `test_milestone_run.py:229,356`, `tests/test_cli.py:2254,7679` | patch `cli.run_direct` instead of injecting (5.14) | M6 |

## 12. How this will be enforced

Design only. The architecture test is written separately. It scans `src/agent_manager/` and ignores `docs/superpowers/`.

| Rule | Kind | Check |
|---|---|---|
| §3.1, §3.2 complete | mechanical | every `.py` under `src/agent_manager/` appears exactly once |
| §4.1, §4.2, §4.4 | mechanical | AST import graph, counting module-level, function-local and `TYPE_CHECKING` edges, checked against the layer table |
| §4.3 | mechanical | a function-local import points downward and has a comment on the line above |
| §4.5, §4.6 | mechanical | non-empty package `__init__.py` files; an `ImportFrom` or attribute of a `_name` across modules |
| §5.1-5.13 | mechanical | per-library allowlists on imports; allowlists for the string `"brd"` as an argv head, `datetime.now` and `mkdir` |
| §11 | mechanical (ratchet) | the exceptions are an allowlist. A new violation fails. A fixed one must be removed from the list. |
| §3.1 band rules, §6, §7 | judgment (audit) | placement of new code, purity of the Core band |
| §8 | judgment (audit), partly greppable | `§`, `card [0-9a-f]{8}`, "ported from" in docstrings |
| §9, §10 | judgment (review) | any diff touching the journal, envelope, `steps/`, `workflow/`, `results.py` |

## 13. Decisions

| # | Decision | Resolution | Rejected alternative |
|---|---|---|---|
| D1 | Same-layer imports | Forbidden. Siblings are independent and layers are fine-grained. | Allowing imports within a band in a declared order: coarser layers that hide order inside each band. |
| D2 | Home of `DEFAULT_TIMEOUT` | `models`, beside `Dispatch.timeout`'s default | `harness/launcher.py` or `workflow/phases.py`: neither already owns the value. |
| D3 | `comments` | Whole, in Application | Splitting the pure composition into Core: it would need `SubtaskSummary` moved, for no current user. |
| D4 | `dispatch` | Stays `agent_manager/dispatch.py` (L14) | Moving it into `runtime/`: churn with no rule gained. |
| D5 | Package shape | `store/` and `milestone/` packages imported by leaf module; `bases`/`integration` stay flat | Flat sibling modules: crowds the top-level namespace. |
| D6 | `cli` shape | A `cli/` package with sub-layers L26-L29. `envelope` stays in Application (L17), because detached children write the same envelope to `report.json`. | One Typer module of about 900 lines. |
| D7 | `Store` writes | `Store` keeps every write; concern modules supply SQL and never commit | Per-concern writer classes sharing a lock and fence: splits one critical section. |
| D8 | Private names | Forbidden across modules, including within one sub-package | Allowing them inside `runtime/` and `steps/`. |
| D9 | Clock | `clock.utcnow` plus `now` parameters | A `Clock` protocol injected everywhere: heavier, and no test needs it. |
| D10 | `canceled` in the journal | The journal writes `canceled`; the watch hello moves to schema 2; both spellings are read forever (§9) | Keeping schema 1, or keeping `cancelled` on the wire. |
| D11 | Envelope keys | `canceled: true` and `already_canceled`, with no dual-key period | Emitting both keys for one release. |
| D12 | `InvalidCardIdError` | Accepted. `error.type` for a bad card id changes from `ValueError`, documented as a contract change. | Keeping bare `ValueError` in `HANDLED`. |
| D13 | `CLAUDE.md` | Points to this document for layering | Leaving the frozen design spec as the source of truth. |
| D14 | `runtime.walk` → `store` | Keep the concrete import and type it later through a `RunDeps` Protocol | Making Runtime import no adapter: moves the record helpers out of the walk. |
