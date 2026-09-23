# Run agent phases: dispatch, validate, retry, escalate (bf8e415b)

Subtask of story 2143808b, "The workflow document and the engine". Source of truth for every decision below: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§6 lines 261-278, §7, §8 lines 306-318, §9 lines 365-368, §12 lines 429-433, §14 lines 477-492). Nothing here is a new design decision; this narrows the agreed one to the agent-dispatch seam of the already-built engine (see the corrected "Scope" section below for exactly what that seam is).

## Scope

**Correction from planning-stage verification:** `src/agent_manager/engine.py`
already exists, and its phase loop (`run_subtask`) already handles both phase
kinds — it does not need a new `kind: agent` branch. For an `AgentPhase` it
already resolves `inputs` and renders the prompt (`prompt.render_prompt`), then
hands `(phase, context, rendered)` to an **injected callable** of type
`AgentPhaseRunner`, and folds whatever that callable returns into the context
under the phase's name. `engine.py` names this explicitly: "The seam sibling
bf8e415b fills." This subtask's job is therefore to **implement that callable**
(run one agent phase to a terminal outcome, with its retry loop) and wire it in
as `run_subtask`'s `agent_runner=` argument — not to add a branch to the loop
itself. Whether the implementation lives inside `engine.py` or a new module
(e.g. a dispatch/runner module) is a planning-stage call; either way it must
satisfy the existing `AgentPhaseRunner` signature `(phase: AgentPhase, context:
Mapping[str, Any], rendered: prompt.RenderedPrompt) -> Any`.

**One part of the loop is not, in fact, already handled, and this review flags
it rather than resolving it:** as currently written, `run_subtask`'s agent-phase
branch unconditionally treats whatever `agent_runner` returns as the phase
result, binds it into the context, and advances to the next phase — there is no
branch that reads a failure/escalation signal back out of the call the way
`_run_deterministic`'s `_Outcome(ok=False, ...)` does for the deterministic
side, and no `try/except` around the call (contrast `_run_deterministic`,
which is deliberately total: "an exception escaping the walk would leave the
subtask recorded `started` forever"). So exhausting `max_attempts` or hitting a
non-retryable gate failure cannot, today, turn into `summary.status =
"escalated"` and a graceful return purely inside an injected callable — either
the callable's exception propagates uncaught out of `run_subtask` (crashing the
walk rather than escalating it gracefully), or `run_subtask`'s loop needs a
small, symmetric addition (catch what the runner raises on a terminal failure,
same as the deterministic branch already does for its own step). Deciding which
of these — and whether that addition to `run_subtask` is this subtask's to make
despite the "opaque call" framing above, since no other subtask currently owns
it — is a planning-stage decision, not something this review can settle by
reading files alone.

Pieces this subtask consumes, confirmed present in the tree (do not re-create
them):

- `paths.attempt_dir(run_id, card, phase, attempt) -> Path`, already implemented
  and unit-tested in `tests/test_paths.py` — this subtask only owns picking the
  next free attempt number and calling it.
- `prompt.RenderedPrompt.write(attempt_dir) -> Path`, which writes `prompt.txt`
  into a directory this subtask creates.
- `harness/launcher.py`: the `LauncherFn` protocol, `run_direct`, and
  `get_launcher(kind)` — the injected launcher this subtask must call through,
  never `subprocess` directly.
- `harness/claude.py`: a concrete `HarnessAdapter` with `build_command` and
  `parse_usage`; `harness/base.py`'s `HarnessAdapter` Protocol, `Dispatch`,
  `Outcome`, `Usage`.
- `roles/loader.py`'s `load_role(name) -> RoleBundle`, for the role's system
  prompt, policy and vendored methodology.
- `workflow/loader.py`'s `AgentPhase` (fields: `role`, `inputs`, `result`,
  `writes`, `retry: RetryPolicy | None`, `gates`) and `RetryPolicy`
  (`max_attempts`, `on: list[Literal["schema_invalid", "gate_failed"]]` — the
  literal already restricts `on` to exactly the two retryable outcomes this
  spec describes).
- `store.Store.record_phase(...)` and `store.Store.record_attempt(story_id,
  card_id, phase_name, attempt: models.Attempt)`, and `models.AttemptStatus =
  Literal["started", "ok", "schema_invalid", "gate_failed", "harness_error"]` —
  the four outcome names plus `started` are already the type, not something
  this subtask invents.

One thing genuinely does **not** exist yet and is fair to build here as a
minimal seam, or to flag for the plan to decide: a function that turns a
phase's `role` (plus `RunConfig.harness_map`) into a concrete `HarnessAdapter`
instance. Only the `claude` adapter is implemented; there is no adapter
registry keyed by harness name yet, and no function resolves
`phase.result` (a string) to the Pydantic model class it names. Both
resolutions are small and are naturally this subtask's to own unless a sibling
claims them first — confirm against the current board/sibling plans before
assuming either.

In scope:

- Creating the attempt directory `runs/<run-id>/<card>/<phase>.<attempt>/` (via `paths.attempt_dir`, picking the next free attempt number) and the `result.json` path inside it, outside the worktree (§6 step 3).
- Writing `prompt.txt` into that attempt directory: `engine.run_subtask` renders the prompt in memory (`prompt.render_prompt`) and hands this subtask's runner the resulting `RenderedPrompt` object, not a file on disk — calling `rendered.write(attempt_dir)` (or appending feedback and writing that) is this subtask's job, every attempt, not a precondition it can assume.
- Building the `Dispatch` (the prompt path just written, role bundle, cwd = subtask worktree, result path, model, timeout), handing it to the resolved `HarnessAdapter.build_command`, and running the argv through the **injected launcher** — engine.py never spawns a process itself (D7, §8 line 317).
- Capturing harness stdout to `stdout.log` in the attempt directory, as a log only (D4, §6 step 4).
- Reading `result.json` and validating it against the phase's declared Pydantic result model (D4 — stdout is never parsed for the contract).
- Running the phase's registry-resolved gates over the validated result.
- The four terminal outcomes and the retry/escalate policy below.
- Journalling every state edge before the store row is updated (§9 line 365).

Explicitly out of scope, because a sibling or a later milestone owns it and it is already built in `engine.py`/`prompt.py` in this tree: parsing the workflow YAML or resolving gate/run names (`workflow/loader.py`, `workflow/registry.py`); the deterministic branch, phase ordering, `when`/`skip_to`, `best_effort`, and the phase loop itself, including the call that resolves this subtask's inputs and renders the prompt before invoking it (`engine.run_subtask`); milestone orchestration — census, levels, parallel stories, integrate; non-Claude harnesses; the `bwrap`/`container` launchers; resume replay itself (this subtask only guarantees the journal lines resume will need). Still open and only conditionally this subtask's, as flagged below: resolving a phase's `role` to a concrete `HarnessAdapter` instance, and resolving a phase's `result` name to its Pydantic model class.

## Observable behaviour

For a phase with `kind: agent`, attempts are numbered from 1 and each gets its own directory, so a retry never overwrites a prior attempt's `prompt.txt`, `result.json` or `stdout.log`. Every attempt ends in exactly one of four journalled outcomes, and the outcome name is the value recorded — these four strings are part of the contract, since the workflow document's `retry.on` list names them:

- `ok` — the launcher exited zero, `result.json` parsed and validated against the phase's model, and every gate passed. The validated result is recorded as the phase result and becomes available to later phases by name (§6).
- `schema_invalid` — the result file exists and is readable but fails Pydantic validation (including malformed JSON).
- `gate_failed` — the result validated but at least one gate returned a failure. Whether *this* `gate_failed` is retried or escalates is decided the same way as `schema_invalid` — by whether `gate_failed` appears in the phase's `retry.on` — not by anything the gate itself reports; the existing gate contract used for deterministic phases (`workflow.function`'s verdict: `None` passes, a `"warn"` key warns, anything else fails) carries no separate per-gate retryable flag, and this subtask does not add one.
- `harness_error` — non-zero exit code, timeout, or `result.json` missing after the launcher returned (§6 line 278).

Retry is driven entirely by the phase's `retry: {max_attempts, on: [...]}` from the workflow document — no hardcoded counts, and an outcome absent from `on:` is never retried even if attempts remain. On a retried `schema_invalid` the validator's error text is appended to the next attempt's prompt; on a retried `gate_failed` the gate's detail is appended (§6 step 7). Appending means the next attempt directory's `prompt.txt` is the prior prompt plus the feedback block — dispatch stays stateless, and the whole input of every attempt is on disk (D1).

Exhausting `max_attempts` on a retryable outcome, and any non-retryable gate failure, both end the phase unsuccessfully. A non-retryable gate failure marks the subtask `escalated` and stops the run: the engine schedules no new work, and any in-flight story is allowed to finish its current phase and is then parked (§12 line 429). Because this milestone runs one story at a time, "stop scheduling" here means the phase loop for this subtask returns without advancing, leaving the run in a state a later resume or `retry` can pick up.

Ordering is fixed and testable: for each edge (attempt started, attempt outcome, phase terminal status, subtask escalated) the journal line is appended first and the SQLite/store row is written second. If the process dies between the two, the journal is truth (§9 line 368).

## Error paths

- `result.json` missing after a zero exit — `harness_error`, not `schema_invalid`. Nothing is parsed.
- Result file present but not valid JSON — `schema_invalid`, carrying the decode error as the feedback text.
- Launcher timeout — `harness_error`; whatever `stdout.log` was captured is kept.
- Gate function raises rather than returning a verdict — treated as a non-retryable failure and escalated, never swallowed.
- Attempt directory already exists (a resumed attempt number) — the engine uses the next free attempt number rather than overwriting; discarded in-flight attempts stay on disk as evidence.
- `max_attempts` reached — the phase's terminal status records the last outcome; the subtask does not silently continue to the next phase.

## Test list

All tests below are **Engine tier** per the placement rule in spec §14 lines 486-488 ("driven with a fake adapter that returns canned result files, including invalid ones, gate-failing ones"), which is verbatim what this card asks for. None belong to the Adapters tier (that tier asserts `build_command` purity only) or the Steps tier (no deterministic step is touched here). No end-to-end test is written for this subtask; that tier is reserved for the single opt-in slow test.

Location: `tests/test_engine.py` if the runner is implemented inside `engine.py`; otherwise the test module that mirrors wherever the plan places the code, per CLAUDE.md's flat-mirror convention (e.g. a new `agent_manager/<name>.py` gets `tests/test_<name>.py`, not `tests/test_engine.py`) — `tests/test_engine.py` already holds ~1785 lines covering the existing loop and seam-injection behaviour, so this is a real choice, not a formality. The fake adapter and a fake launcher that writes a canned `result.json` and canned stdout live as fixtures alongside whichever tests they serve.

1. Happy path — canned valid result, all gates pass: outcome `ok`, phase result recorded, attempt directory contains `prompt.txt`, `result.json`, `stdout.log`. (Engine)
2. Attempt directory is created under `runs/<run-id>/<card>/<phase>.1/` and is outside the subtask worktree path. (Engine)
3. Engine calls through the injected launcher, never `subprocess` — asserted by a launcher double that records its invocations and by the adapter's `build_command` being the only argv source. (Engine)
4. Canned result that fails the phase's Pydantic model: outcome `schema_invalid`. (Engine)
5. Canned non-JSON result file: outcome `schema_invalid`. (Engine)
6. Missing result file with exit 0: outcome `harness_error`. (Engine)
7. Non-zero exit from the launcher: outcome `harness_error`. (Engine)
8. Launcher timeout: outcome `harness_error`, `stdout.log` still written. (Engine)
9. Valid result, gate returns retryable failure: outcome `gate_failed`. (Engine)
10. Retry on `schema_invalid` — second attempt directory `<phase>.2` exists and its `prompt.txt` contains the validator error appended to the original prompt. (Engine)
11. Retry on a retryable `gate_failed` — second attempt's prompt contains the gate detail. (Engine)
12. `max_attempts` honoured from the phase object: with `max_attempts: 2` and a persistently invalid result, exactly two dispatches occur and the phase ends failed. (Engine)
13. An outcome not listed in `retry.on` is not retried even with attempts remaining (e.g. `harness_error` under `on: [schema_invalid]`). (Engine)
14. Non-retryable gate failure: subtask status is `escalated`, no further dispatch occurs, and no subsequent phase is started. (Engine)
15. Stdout is never a channel — a canned run whose stdout contains a well-formed result JSON but whose `result.json` is invalid still yields `schema_invalid`. (Engine)
16. Journal-before-store ordering — with a store double that raises on write, the journal already contains the edge line. (Engine)
17. All four outcome names appear as distinct journalled values across the cases above. (Engine)

## Flag: exploration's claim about the tree was stale — corrected here

An earlier exploration pass reported that the repository contained no
`engine.py`, `workflow/`, `steps/` or `harness/` at all. This review verified
that claim against the actual worktree and found it false: `src/agent_manager/`
already contains `engine.py`, `models.py`, `store.py`, `paths.py`, `prompt.py`,
`errors.py`, plus `workflow/` (`loader.py`, `registry.py`, `builtin/`),
`steps/` (`worktree.py`, `verify.py`, `plan_check.py`, `reducers.py`),
`harness/` (`base.py`, `claude.py`, `launcher.py`), and `roles/` (`loader.py`,
`bundles/`), each with its own passing-shaped test file under `tests/`. The
"Scope" section above has been corrected to name exactly what this subtask
consumes from that tree and the one seam type (`AgentPhaseRunner`) it must
implement. Do not re-open the "pieces may not exist" question during planning
— it is resolved: they exist, as enumerated above.

What is still genuinely open, and is a fair planning-stage decision rather than
something this review can resolve by reading files: whether resolving a
phase's `role` to a concrete `HarnessAdapter` instance (there is currently only
one adapter, `claude`, and no registry keyed by harness name) and resolving a
phase's `result` string to its Pydantic model class belong to this subtask or
to a sibling. Both are small enough to build here as minimal seams if no
sibling plan already claims them.
