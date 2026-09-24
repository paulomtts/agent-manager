# Making the skeleton real — design addendum

Date: 2026-09-23
Amends: `2026-09-23-agent-manager-design.md` (§6 step 2, §8, §14)
Status: implemented and merged (milestone 2). R1–R4 follow from D4 and D6 of the main spec; R5–R10 were found by running it (section 5)

## 1. Why this exists

Milestone 1 merged with 942 passing tests, and `am run --card` cannot run a
single agent phase in production. A smoke test on a toy repo, calling
`run_card` with the production wiring, died in one second:

```
EngineError: phase 'explore': declares result 'ExploreResult', which no
result model is registered for (registered: nothing)
```

Reading the code found three open seams, each invisible to per-card tests
because every test injects its own fakes:

1. **No result models.** `results.RESULT_MODELS` ships empty. `results.py`
   says why: "the design spec gives a field schema for none of them". The main
   spec named five models (`ExploreResult`, `CriticResult`, `PlanResult`,
   `ImplementResult`, `ReviewResult`) and never specified their fields.
2. **The prompt carries no instructions.** `dispatch._attempt` writes
   `prompt.render_prompt(...)` as the whole brief: a two-line header plus the
   phase's inputs. `RoleBundle.system` and `RoleBundle.methodology` are read by
   nothing outside `roles/loader.py`, and no text tells the agent where to
   write `result.json` or in what shape. D4 and D6 are unwired.
3. **Gates that cannot bind.** `critic_blockers_gate` is a placeholder stub in
   `workflow/registry.py`. `plan_hash_gate(impl_hash, review_hash)` needs two
   parameters nothing supplies. `review_gate` needs `base_branch`, which the
   context may not carry under that name.

There is also one CLI gap: `run_card` accepts `commands` (the verification
suite) but `am run` exposes no way to pass it, so a verified run is
impossible from the command line.

The root cause is a breakdown flaw, not a coding flaw: the main spec's §14
called for an end-to-end test with a real harness, and the M1 cards never
included one. Every card verified its own slice and deferred the seam to a
neighbour.

## 2. Decisions

**R1 — Result models port the old schemas, minus what D6 deleted.** Each model
carries the field set `workflow/task.js` required from the same phase, in
snake_case, and spelled the way `steps/reducers.py` reads them. The implementer
reads `reducers.py` first and reconciles the spelling in one place, with a test.

| model | fields |
|---|---|
| `ExploreResult` | `refused: bool`, `reason: str \| None`, `summary: str`, `verification: {full_suite: list[str], typecheck: str, lint: list[str]}` |
| `CriticResult` | `blockers: bool`, `reason: str \| None`, `summary: str` |
| `SpecResult` | `path: str`, `note: str \| None` (added by R5) |
| `PlanResult` | `path: str`, `self_reviewed: bool`, `note: str \| None` |
| `ImplementResult` | `blocked: bool`, `blocked_reason: str \| None`, `resumed: bool`, `plan_hash: str`, `report: str` |
| `ReviewResult` | `findings: list[str]`, `unresolved_blockers: list[str]`, `fix_summary: str`, `porcelain: str`, `commit_count: int`, `tagged_count: int`, `plan_hash: str` |

`PlanResult` drops the old `skillInvoked` flag. It existed to detect an agent
that skipped a Claude skill; under D6 the methodology is text in the prompt, so
there is no skill to skip.

**R2 — The brief is one composed document on disk.** The engine writes a single
`prompt.txt` per attempt, in this order: the role's `system.md`; each
methodology file under its own heading; the phase's rendered inputs; a **Result
contract** section; and, on a retry, the feedback section. The result contract
states the absolute `result.json` path, says to write valid JSON there and to
keep it out of the worktree, and embeds the JSON Schema produced by the
phase's model (`model_json_schema()`), so the model and the instruction cannot
drift apart.

This deviates from main-spec §8's wording, which had Claude receive the role via
`--append-system-prompt`. A composed on-disk brief is byte-identical across
harnesses, is reproducible from the attempt directory (D1, D4), and keeps the
adapter a pure argv builder.

**R3 — The CLI carries what a run needs.** `am run` and `am resume` accept a
repeatable `--verify <command>` and pass it as `commands`. `--branch-prefix`
becomes required: the `m1` default was a leftover from building this milestone.

**R4 — The seam gets a test that cannot be faked around.** Two tests:

- **Production wiring, default suite.** Drives `run_card` through
  `default_runner_factory`, the real `ClaudeAdapter` and the real
  `run_direct` launcher, with a fake `claude` executable first on `PATH`. The
  fake reads the prompt file it is pointed at, extracts the result path and the
  embedded schema *from the prompt text*, and writes a conforming result. If the
  brief fails to say where to write, the test fails. Costs nothing.
- **Real harness, opt-in.** `pytest -m e2e` runs the same toy card against a real
  `claude -p`. Excluded by default. Run by a human.

## 3. Acceptance

1. The fake-`claude` production-wiring test passes in the default suite.
2. Every gate named in `builtin/task.yaml` binds against a real result object,
   asserted by a test that loads the document rather than by inspection.
3. The real-harness toy card reaches `done` on a local branch with
   `Plan-Hash` trailers on every commit. This is checked by hand after the
   milestone; the pipeline cannot verify it, since the test is excluded by
   default.

## 4. Deferred to the orchestration milestone

Found while scoping, none of it needed here:

- `models.Card` and `CardNode` carry no `blocked_by` or `created_at`, though
  `brd tree` emits both and sibling ordering needs them.
- `store.Store` holds one SQLite connection with no lock and no
  `busy_timeout`; parallel stories need it made thread-safe.
- `board.set_status` sets one card. The ancestor roll-up in `rollup.mjs`
  (story and milestone status from children) was never ported.
- `run_card` takes verification commands from the caller; per-run discovery
  belongs to the milestone runner.
- `RunConfig.max_concurrent_stories` defaults to 1; the main spec says 4.
- **Candidate improvement:** `ReviewResult`'s `porcelain`, `commit_count` and
  `tagged_count` are measurements an agent is asked to run and report verbatim.
  A deterministic step can measure them with `git`, which removes an agent's
  chance to misreport them. Not in scope here; worth doing.

## 5. Amendments found by running it

The wiring test and two real-harness runs on a toy repo found six more open
seams after R1–R4 shipped. Each was verified against the code before it was
fixed, and each is now built.

- **R5 — Every agent phase has a result contract**, `spec` included, via
  `SpecResult`. `dispatch._attempt` used to pass `result_path=None` for a phase
  with no model, and `classify` called `.is_file()` on it. A phase with no
  result model is now judged on exit status alone and cannot crash the runner.
- **R6 — The worktree exists before any agent phase.** `task.yaml` ran
  `explore` first, but the runner refuses to dispatch without a worktree, and
  an explore in the main checkout would read the wrong code for a subtask
  stacked on another branch.
- **R7 — `rollup.set_status` is a real step** calling `board.set_status`. It was
  a registry placeholder, and both status phases are `best_effort`, so a run
  would have reported success while the board never moved. Ancestor roll-up is
  still deferred (section 4).
- **R8 — The engine commits the spec and plan.** Nobody did: the old pipeline's
  implement agent did it through a prompt that was never ported, so the tree
  was dirty and `review_gate` blocked.
- **R9 — The coder is told the plan hash and must tag every commit.** Its first
  real commit carried no trailer, so the every-commit-tagged check could not
  pass even with the docs committed.
- **R10 — The plan gets its `<!-- task-pipeline: validated -->` marker** from a
  deterministic step, so `plan_check` can reuse a plan on resume.

The plan hash is the first 8 hex characters of sha256 of the plan file,
computed after the marker is appended, because the marker changes the bytes.

**Lesson, and the rule to keep:** a fake harness must never know more than the
brief tells it. The first wiring test passed while the real agent failed
because its fake computed the plan hash and committed the docs itself. The
plan hash, the docs commit and the marker are mechanism, so the engine does
them; agents only write code and tag commits.

**Acceptance, met:** a real `claude -p` took a toy card to `done` on the board
through `am run` (all 14 phases; two commits, both tagged; clean tree).
