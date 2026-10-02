# agent-manager — design

Date: 2026-09-23
Status: approved design, pre-implementation

## 1. Purpose

`agent-manager` is a local-first Python CLI that drives a `brd` milestone to
completion by dispatching AI harness instances (Claude Code, Codex, Pi) as
subprocesses.

It inverts today's arrangement. The `leave-me-alone` plugin ships
`orchestrator.js` and `task.js`, which are JavaScript but run *inside* the
Claude Code harness through the Workflow tool. The script is a guest there: the
only thing it can do is call `agent()`. So a step that merely runs
`bun worktree.mjs` and reports what it printed costs a whole model
invocation, and the scripts cannot be distributed by the plugin at all — a
SessionStart hook copies them into `~/.claude/workflows/`, which is why
updates land one session late.

`agent-manager` makes the program the process. It owns the dependency graph,
the git mechanics, the gates, the retries and the persistence, and it launches
a harness only for the steps that genuinely need a model.

### Motivations, in priority order

1. **Cost and latency.** Deterministic steps stop costing tokens.
2. **Harness portability.** The same workflow runs on Claude, Codex or Pi; a
   harness can be chosen per phase, and two harnesses can be compared on the
   same card.
3. **Unattended durability.** A milestone runs for hours, survives a crash or
   reboot, and resumes mid-graph without a human session holding it open.
4. **Control and observability.** Real logs, a queryable run state, and the
   ability to pause, retry or intervene on one node without restarting.

None of the four is traded away for another.

## 2. Scope

**In scope.** The engine, the workflow format, the harness adapters, the role
bundles, the run store, the CLI, and Python ports of the deterministic helpers
that today live as `scripts/*.mjs` (`detect`, `census`, `naming`, `worktree`,
`plan-check`, `rollup`, `ship`, `integrate`).

**Out of scope, unchanged.** `brd` itself. The spec and plan markdown
conventions under `docs/superpowers/`. The human-facing authoring skills in the
`leave-me-alone` plugin — `setup-milestone`, `setup-project`, `setup-report`,
`qa`, `smoke`, `brief`, `explain`, `tree`, `dispatch`, `build`, `bench-prompt`.
Those are interactive and belong in the harness where the human is sitting.

**Removed from the plugin once this lands.** `workflows/orchestrator.js`,
`workflows/task.js`, `workflows/load-pure.mjs`, `scripts/*.mjs`, the
`agents/*.md` agent definitions, and the `sync-workflows.sh` hook. Their
behaviour moves here.

**Never.** Pushing to a remote, opening PRs, or touching `main`/`master`. All
work lands on local branches; a human merges.

## 3. Decisions

These were settled during brainstorming. Each is recorded with the reason,
because the reasons constrain later work.

| # | Decision | Reason |
|---|---|---|
| D1 | **Stateless dispatch; context travels as artifacts on disk.** Every agent invocation is a fresh process. Prior findings reach it as files plus a digest in the prompt. | Portable across harnesses, and it makes crash-resume nearly free because every input is still on disk. Shaped so per-harness session reuse can be added later behind the same adapter interface. |
| D2 | **Workflow = declarative skeleton + named pure reducers.** The phase graph, gates and retry policy are a document; judgement lives in tested Python functions. | Matches the existing split ("agents decide as little as possible"). A state machine gives resume and a live graph view without writing a replay engine. No expression language — `when:` names a function. |
| D3 | **Python, Typer, mirroring `brd`.** | One toolchain, one idiom, same storage conventions. |
| D4 | **Result-file contract.** The program creates a result path outside the worktree, passes it in the prompt, then reads and validates it with Pydantic. Stdout is a log, not a channel. Validation failure re-dispatches with the error appended, capped. | Uniform across every harness, survives chatty output, and yields a durable diffable artifact. Harness-native structured output may be used inside an adapter as an optimisation. |
| D5 | **Run state in SQLite per project, plus an append-only journal.** `~/.local/share/agent-manager/<project>.db` for the queryable projection; `runs/<run-id>/journal.jsonl` for the audit trail. `brd` receives status transitions and a bounded set of code-authored, append-only, idempotent outcome comments. `am` never reads comments back to decide anything: the board is not a second source of truth. (Amended by B1 of the [board-comments addendum](2026-09-29-board-comments-design.md).) | Keeps churn out of the board, which stays stable and snapshot-able. The two stores are independent: `brd forget`/`purge` and agent-manager cleanup must not affect each other. |
| D6 | **Roles owned by agent-manager; methodology vendored.** A role is a directory with a system prompt, a tool policy, a model hint and the inlined text of any methodology it needs. No plugin or skill is resolved at runtime. A thin capability matrix refuses genuinely non-portable phases on harnesses that lack what they need. | Converts "a Claude skill fired" into "these instructions were in the prompt", which is the only way a cross-harness comparison means anything. Also removes the hard dependency the current README documents as a stop condition. |
| D7 | **Full-auto dispatch, isolation by worktree, confinement behind a seam.** v1 launches harnesses with permissions bypassed, cwd pinned to the subtask worktree. The adapter takes a launcher (`direct` \| `bwrap` \| `container`); only `direct` is implemented. | Cost is priority 1 and the work already happens on disposable local branches that never push. The risk is stated plainly in §12. |

## 4. Architecture

```
                    ┌──────────────────────────────────────────┐
   you ──► CLI ────►│  engine (state machine)                  │
  (typer)           │  · resolves DAG → levels                 │
   run/status/      │  · runs one phase at a time per subtask  │
   watch/resume/    │  · writes journal + SQLite on every edge │
   retry/cancel     └───┬──────────────────────┬───────────────┘
                        │                      │
              deterministic phase        agent phase
                        │                      │
                        ▼                      ▼
              ┌──────────────────┐   ┌─────────────────────────┐
              │ steps/ (Python)  │   │ harness adapter         │
              │ worktree, rollup │   │  claude │ codex │ pi    │
              │ verify, integrate│   │   └── launcher: direct  │
              │ census, dag      │   │        (bwrap/container │
              │ — no model calls │   │         behind the seam)│
              └────────┬─────────┘   └───────────┬─────────────┘
                       │                         │
                       │              role bundle (prompt + tool
                       │              policy + model + vendored
                       │              methodology) ──► materialized
                       │                         │
                       ▼                         ▼
              ┌────────────────┐        ┌──────────────────┐
              │ brd (external) │        │ worktree on disk │
              │ status only    │        │ + $RESULT_PATH   │
              └────────────────┘        │   (outside repo) │
                                        └──────────────────┘
```

The load-bearing distinction is the **two phase kinds**. Of `task.js`'s twelve
dispatches, five exist only because a Workflow script cannot execute a command:
`worktree`, `plan-check`, `ship`, and two `rollup` calls. Here those are
function calls.

### Package layout

```
src/agent_manager/
├── cli.py              typer app
├── engine.py           state machine: schedule, gate, retry, journal
├── dag.py              pure: cycles, levels, branch names, bases, matching
├── board.py            the only caller of `brd`
├── store.py            SQLite tables + journal writer
├── models.py           pydantic models (§9)
├── workflow/
│   ├── loader.py       parse + validate the workflow document
│   ├── registry.py     name -> reducer / step function
│   └── builtin/        task.yaml, milestone.yaml
├── steps/              deterministic phases + reducers
│   ├── worktree.py verify.py integrate.py plan_check.py rollup.py census.py
│   └── reducers.py     the gates ported from task.js
├── harness/
│   ├── base.py         adapter protocol, Dispatch / Outcome types
│   ├── claude.py codex.py pi.py
│   └── launcher.py     direct | bwrap | container (seam)
└── roles/
    ├── loader.py
    └── bundles/<role>/{system.md, policy.toml, methodology/*.md}
        + VENDORED.lock
```

## 5. Workflow format

A workflow is YAML. It names phases, their kind, their role, their gates and
their retry policy. Every `when`, `gate` and `run` value is the name of a
registered Python function — there is no expression language, and adding one is
explicitly out of bounds.

```yaml
name: task
description: Drive one subtask card end to end in its own worktree.

phases:
  - name: worktree
    kind: deterministic
    run: worktree.ensure

  - name: explore
    kind: agent
    role: explorer
    inputs: [card, parent_story, repo_docs, verification]
    result: ExploreResult
    gates: [exploration_output_gate, verification_gate]
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }

  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
    best_effort: true

  - name: plan_check
    kind: deterministic
    run: plan_check.find_validated_plan
    skip_to: implement
    when: plan_check.has_validated_plan

  - name: spec
    kind: agent
    role: spec_author
    inputs: [card, explore]
    result: SpecResult
    writes: docs/superpowers/specs/{stem}.md

  - name: validate_spec
    kind: agent
    role: critic
    inputs: [card, spec_path]
    result: CriticResult
    gates: [critic_blockers_gate]

  - name: plan
    kind: agent
    role: planner
    inputs: [spec_path]
    result: PlanResult
    writes: docs/superpowers/plans/{stem}.md

  - name: validate_plan
    kind: agent
    role: critic
    inputs: [spec_path, plan_path]
    result: CriticResult
    gates: [critic_blockers_gate]

  - name: implement
    kind: agent
    role: coder
    inputs: [plan_path, spec_path, branch, base_branch]
    result: ImplementResult

  - name: review
    kind: agent
    role: reviewer
    inputs: [branch, base_branch, plan_path]
    result: ReviewResult
    gates: [review_gate, plan_hash_gate]

  - name: verify
    kind: deterministic
    run: verify.run_suite
    gates: [verification_passed_gate]

  - name: mark_done
    kind: deterministic
    run: rollup.set_status
    args: { status: done }
    best_effort: true
```

`milestone.yaml` is the outer workflow: census, cycle check, level computation,
per-level parallel stories, sequential subtasks within a story (each running
`task.yaml`), then a terminal `integrate` phase.

### Reducers to port faithfully

These exist because each was learned from a real failure, and the comments in
`task.js` record the incident. They port as pure functions with the existing
tests as the specification:

- `exploration_output_gate` — rejects schema-valid but degenerate Explore
  output (short or placeholder summary; `verification.fullSuite` absent, or
  altered when the caller supplied it).
- `verification_gate` — refuses to proceed when no full-suite command exists,
  unless `allow_no_verification` is set, because every downstream gate would
  otherwise be vacuous.
- `review_gate` — dirty worktree, zero commits, or untagged commits stop the
  run. Unusable counts downgrade to a warning and skip the hash half.
- `plan_hash_gate` — Implement writes Plan-Hash trailers; Review recomputes the
  hash independently; a mismatch means the plan changed mid-run and every
  trailer is stale.
- `count_of` — an absent count is unusable, not zero.
- `short_id`, `stem_of` — branch and artifact naming; the branch is the single
  source of truth for both.

`shell_quote` does not port. Nothing here builds a shell command string for an
agent to run verbatim; the program runs commands itself with argument lists.

## 6. The phase contract

> **Superseded by the pygents engine.** This section and §9 describe the original yaml-engine phase and resume model. The pygents engine replaced that model. For phases, the [pygents addendum's §5 "Phase model and compiler"](2026-09-25-pygents-engine-design.md#5-phase-model-and-compiler) is authoritative, and for checkpoints and resume, its [§6 "Checkpoints and resume"](2026-09-25-pygents-engine-design.md#6-checkpoints-and-resume) is, with one correction: the addendum's §6 says a resume after an escalation re-runs the failed phase, but `am resume` refuses an escalated subtask (exit 3), as the README's "Relaunching resumes" section states.

**Deterministic phase.** The engine calls `run(ctx) -> dict`. No network, no
model. Unit-tested directly. Its return value is recorded as the phase result
and becomes available to later phases by name.

**Agent phase.** The engine:

1. resolves the role bundle and the harness assigned to that role;
2. renders the prompt from the phase's declared `inputs` (§7);
3. creates `runs/<run-id>/<card>/<phase>.<attempt>/` and a `result.json` path
   inside it, **outside the worktree** — the verify step's clean-tree check
   would otherwise fail on a stray file, or the file would be swept into a
   commit;
4. launches the harness with cwd set to the subtask worktree, capturing stdout
   to `stdout.log`;
5. reads `result.json`, validates it against the phase's Pydantic model;
6. runs the phase's gates over the validated result;
7. on schema failure or a retryable gate failure, re-dispatches with the
   validation error or the gate detail appended, up to `max_attempts`;
8. on a non-retryable gate failure, marks the subtask `escalated` and stops.

Four distinguishable outcomes, each journalled: `ok`, `schema_invalid`,
`gate_failed`, `harness_error` (non-zero exit, timeout, missing result file).

## 7. Context plumbing

Nothing is carried in memory between phases. A phase declares `inputs`, and
each input name resolves through a fixed table:

| input | resolves to | form in the prompt |
|---|---|---|
| `card` | `brd show <id>` at run start, cached in the run | inlined JSON — small |
| `parent_story` | the parent card | inlined JSON |
| `repo_docs` | `CLAUDE.md`/`AGENTS.md` at the worktree root | path + first N lines |
| `explore` | the validated `ExploreResult` of this subtask's explore phase | inlined JSON |
| `spec_path`, `plan_path` | paths in the repo, already committed | **path only** |
| `branch`, `base_branch` | computed by `dag.py` | inlined string |
| `verification` | commands discovered once per run | inlined JSON |
| `plan_hash` | the `docs_commit` step's digest of the validated plan | inlined string |

The rule: **small structured results are inlined; documents are passed by
path.** A spec or plan is something the agent should open in the worktree it is
already sitting in, and inlining it would both re-bill it every phase and
invite the agent to work from a stale copy of a file it can read live.

This is what makes resume cheap. To re-run `implement`, the engine needs the
plan path and the branch — both recorded — and the plan is on disk. No
transcript is required.

## 8. Harness adapters and roles

```python
class HarnessAdapter(Protocol):
    name: str
    capabilities: frozenset[str]

    def build_command(self, d: Dispatch) -> list[str]: ...
    def parse_usage(self, stdout: str) -> Usage | None: ...
```

`Dispatch` carries the prompt text, the role bundle, the cwd, the result path,
the model, and a timeout. The adapter turns that into an argv; the launcher
runs it. Session reuse, if ever added, is an adapter-internal optimisation that
does not change this shape (D1).

**Role bundles** live under `roles/bundles/<role>/`:

- `system.md` — the role's standing instructions.
- `policy.toml` — allowed tools, default model per harness, default
  `max_attempts`, required capabilities.
- `methodology/*.md` — vendored text: the writing-plans format, the TDD
  discipline, the adversarial-review checklist.

Roles: `explorer`, `spec_author`, `planner`, `critic`, `coder`, `reviewer`.

**Vendoring discipline.** `VENDORED.lock` records, per methodology file, the
upstream path and a content hash of the upstream source. A test fails when the
upstream file's hash moves, so a superpowers update becomes a decision to
re-sync rather than a silent divergence. This replaces the current run-time
check where Plan self-reports whether it invoked the skill.

**Capability matrix.** A phase may declare `needs: [browser]`. A harness
declares what it has. The engine refuses to route a phase to a harness lacking
a required capability, at plan time, before anything runs. Methodology is never
a capability — that is what vendoring is for.

**Harness assignment** is config: a `harness_map` of role to
`{harness, model}`, defaulting to Claude with the models the current phases
already pin (explore sonnet, spec/plan opus, critic sonnet, coder sonnet,
review opus).

## 9. State, durability and resume

> **Superseded by the pygents engine.** This section and §6 describe the original yaml-engine phase and resume model. The pygents engine replaced that model. For phases, the [pygents addendum's §5 "Phase model and compiler"](2026-09-25-pygents-engine-design.md#5-phase-model-and-compiler) is authoritative, and for checkpoints and resume, its [§6 "Checkpoints and resume"](2026-09-25-pygents-engine-design.md#6-checkpoints-and-resume) is, with one correction: the addendum's §6 says a resume after an escalation re-runs the failed phase, but `am resume` refuses an escalated subtask (exit 3), as the README's "Relaunching resumes" section states.

```
Run
├── id, workflow, repo_dir, base_branch, branch_prefix, status, started_at
├── config: max_concurrent_stories, dry_run, launcher, harness_map
├── journal: JSONL (append-only, truth)
└── stories: list[StoryRun]
    ├── card_id, title, level, status, tip_branch
    └── subtasks: list[SubtaskRun]      # sequential within a story
        ├── card_id, branch, base_branch, worktree_path, status
        └── phases: list[PhaseRun]
            ├── name, kind, status, started_at, ended_at
            └── attempts: list[Attempt]
                ├── n, exit_code, duration, tokens_in, tokens_out, cost
                ├── dispatch: harness, model, role, cwd, prompt_path, result_path
                └── artifacts: prompt.txt, result.json, stdout.log
```

**Write ordering.** The journal is appended *before* the SQLite row is updated,
and every journal line carries the run id, the card, the phase, the attempt and
a monotonic sequence number. The DB is a projection and can be rebuilt from the
journal; if the two disagree, the journal wins.

**Resume semantics.** `resume <run-id>` reloads the run, discards any attempt
that was in flight when the process died (recorded as `started` with no
terminal event), and re-runs that phase from the top. Every phase must
therefore be idempotent or explicitly re-entrant:

- `worktree.ensure` is already idempotent — it never resets, deletes or commits.
- `rollup.set_status` is idempotent.
- `verify.run_suite` is read-only.
- `implement` is re-entrant through the existing Plan-Hash trailer mechanism:
  commits carrying the current plan hash are resumed from, untagged ones are
  treated as debris. This is exactly why `plan_hash_gate` must be ported
  faithfully — resume correctness depends on it.
- `spec` and `plan` are re-run and overwrite their file.

**Crash of the manager itself** loses only in-flight attempts. A harness
subprocess orphaned by a crash is detected on resume by its missing terminal
journal line; its partial work is on the branch and the phase re-runs.

## 10. CLI surface

```
agent-manager run --milestone <id|title> [--repo-dir .] [--base-branch main]
                  [--branch-prefix m12] [--workflow task|milestone]
                  [--harness claude] [--max-concurrent 4] [--dry-run]
                  [--allow-no-verification]
agent-manager status [<run-id>]      # table: story/subtask/phase/attempt/state
agent-manager watch [<run-id>]       # live tail of the journal
agent-manager logs <run-id> <card> [--phase implement] [--attempt 2]
agent-manager resume <run-id>
agent-manager retry <run-id> <card> --from <phase>
agent-manager cancel <run-id>
agent-manager runs                   # history for this project
```

JSON by default, `--pretty` for humans — the same convention as `brd`.

`--dry-run` resolves the census, the levels and every branch and base, and
writes nothing. The base column is the review artifact: each subtask should
build on the previous one's branch, and a story's first subtask on its
blocker's tip. A blocked story rooted at the milestone base means a missing
`blockedBy` edge.

**Status (milestone 3).** The synopsis above is the target surface, not what
exists today. Exists: `run` (with `--card`, or `--milestone` and an optional
`--dry-run`, plus `--repo-dir`, `--base-branch`, `--branch-prefix`, `--verify` and
`--allow-no-verification`), `status`, `runs`, `logs`, and `resume` for a
single-card run. With `--milestone`, a level's stories run on up to
`--max-concurrent` lanes (default 4); see P1 of the parallel-stories addendum,
`2026-09-24-parallel-stories-design.md`. Deferred: `watch`, `retry`, `cancel`,
`--workflow`, `--harness`, and a milestone-aware `resume`. See section 4 of the orchestration addendum,
`2026-09-24-orchestration-design.md`.

**Status (milestone 9):** `pause` and `cancel` exist, as the live-control addendum, `2026-09-27-live-control-design.md`, specifies: `am pause <run-id>` parks a running run at its next phase boundary for `am resume`, and `am cancel <run-id>` stops it there and closes it for good. The synopsis above lists `cancel` but not `pause`; both exist. `watch` and `retry` remain deferred.

## 11. Concurrency

**Status:** as of milestone 4, stories in a dependency level run in parallel,
bounded by `max_concurrent_stories` (default 4, set with
`am run --milestone --max-concurrent N`). Levels are barriers: the next level
starts only after every story of the current one has finished. See the
parallel-stories addendum, `2026-09-24-parallel-stories-design.md`.

**Status (milestone 7):** superseded by the supervisor-tree addendum, `2026-09-25-supervisor-tree-design.md`: levels are no longer barriers (a story starts once its own blockers finish), a story with two or more blockers roots on a merged base, and `am resume` continues a milestone run.

**Status (milestone 10):** superseded by the multi-process addendum, `2026-09-27-multi-process-design.md`: several `am` processes may now run on one repository at once, on disjoint cards and branches, each run holding a lease and claims that fence its writes, with board and git operations serialised by process-wide locks.

Stories within a dependency level run in parallel, bounded by
`max_concurrent_stories` (default 4). Subtasks within a story run
**sequentially**, each branch stacked on the previous subtask's branch. This is
the current model and it is kept.

Parallelism is process-level: one thread per story driving subprocesses, a
single writer for the journal, and SQLite in WAL mode. Worktrees are disjoint
by construction, so no two concurrent agents share a working directory. The one
shared git resource is the repository's object store and ref namespace; branch
names are derived up front from the card id and are unique per subtask.

The run performs exactly one `git fetch` and one worktree prune, at census time.

## 12. Failure, escalation, and blast radius

**Escalation stops the run.** A non-retryable gate failure marks the subtask
`escalated` and the engine stops scheduling new work; in-flight stories are
allowed to finish their current phase and are then parked. This matches
today's full-stop behaviour and is what makes an overnight run safe to read in
the morning.

**Best-effort phases** (`best_effort: true`, i.e. the board status writes)
record their failure and do not sink a subtask whose work is otherwise sound —
but the failure is surfaced in the run summary, so a run never reports success
while the board silently never moved.

**Blast radius, stated plainly.** Under D7 each agent runs with the user's full
filesystem and network access. A misbehaving or prompt-injected agent can reach
other repositories, the `brd` boards and the user's keys. What is actually
contained is *committed* damage: all work happens in disposable worktrees on
local branches, nothing is pushed, and `main`/`master` is never touched. The
launcher seam exists so `bwrap` can close the rest later; that is deliberately
not v1.

**Status:** as of milestone 5, Integrate merges every story tip into one local `<prefix>-integrate` branch, in its own worktree, after the last level, then runs the verification suite on it once. A merge the resolver does not finish, a merge already in progress, or a failed final verification escalates like any other gate: the run stops, and the branch and worktree are left for a human. The blast-radius guarantee holds: Integrate never checks out, merges into or moves `main`/`master` or the base branch, and never pushes (Integrate addendum I5, `2026-09-25-integrate-design.md`).

## 13. Where this is more efficient

**Eliminated model calls.** Five of twelve dispatches per subtask disappear.
Today each is a full agent turn whose only job is to run one command and echo a
line of JSON — and the prompts spend real effort begging the model not to
reformat, summarise or truncate that line. That entire failure mode goes away.

**Smaller prompts on the calls that remain.** Under D1 each phase receives
exactly its declared inputs. The current pipeline re-establishes context in
free text because it has no other way to hand a finding forward.

**Latency.** A deterministic step drops from a model round trip to a function
call. On a milestone with ten subtasks that is fifty fewer round trips.

**Retries get cheaper and more precise.** A schema failure re-dispatches one
phase with the validator's error, instead of the current pattern of retrying an
entire agent turn with a hand-written paragraph explaining what went wrong.

**Model choice becomes per-phase and per-harness.** A cheap harness can own the
mechanical phases while an expensive one owns spec and review, and because the
instructions are byte-identical across harnesses (D6), the comparison is real.

**The costs, honestly.** No prompt-cache reuse across phases, since every
dispatch is cold — for short phases the cold-start context may exceed what a
warm session would have re-billed, which is what the (c)-style session
optimisation is held in reserve for. Process startup per dispatch is real but
small against a coding agent's runtime. And the project now owns scheduling,
retry and persistence code that the Workflow runtime provided for free.

## 14. Testing

- **Pure functions** (`dag.py`, `steps/reducers.py`) — unit tests ported
  alongside the logic from the existing `.test.mjs` files, which are the
  behavioural specification for naming, census and every gate.
- **Steps** — against temporary git repositories and a temporary `brd` board;
  no network.
- **Adapters** — `build_command` is pure and asserted per harness; the launcher
  is injected, so no harness is executed in unit tests.
- **Engine** — driven with a fake adapter that returns canned result files,
  including invalid ones, gate-failing ones, and a simulated crash mid-phase to
  assert resume.
- **End to end** — one slow, opt-in test that runs a two-subtask toy milestone
  with a real harness, marked and excluded from the default suite.

The project verifies with `pytest`, matching `brd`.

## 15. Migration

1. Build `agent-manager` to parity with `task.yaml` on a single subtask.
2. Add `milestone.yaml` and the integrate phase.
3. Run both systems on the same milestone in a scratch clone and compare
   branches, artifacts and cost.
4. Remove the workflow scripts, helper scripts, agent definitions and the sync
   hook from the plugin; leave the authoring skills, and point their
   documentation at `agent-manager`.
5. Keep the plugin's `auto-allow.sh` — it is about the human's own session, not
   the engine.

## 16. Deferred

Session reuse per harness (D1's optimisation path). `bwrap` and container
launchers. A TUI. Cross-harness A/B as a first-class command rather than two
runs and a diff. Any expression language in the workflow document.

## 17. Open questions

- **Cost accounting on non-Claude harnesses.** `parse_usage` may return nothing
  where a harness does not report tokens. Per-phase cost then has gaps. Live
  with the gaps in v1, or require a usage source per adapter?
- **Timeouts.** A per-phase default is needed, and a long `implement` on a
  large subtask is legitimately slow. Start with a generous per-role timeout in
  `policy.toml` and tune from recorded durations.
- **`brd` concurrency.** Parallel stories mean concurrent `brd update` calls
  against one SQLite board. Expected to be fine, but worth a deliberate test
  before relying on it.
