<!-- task-pipeline: validated -->
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

---

# Run Agent Phases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `engine.AgentPhaseRunner` seam — run one agent phase to one of four journalled outcomes (`ok`, `schema_invalid`, `gate_failed`, `harness_error`), with document-driven retry and escalation — and wire its failure signal back into `engine.run_subtask`.

**Architecture:** A new module `src/agent_manager/dispatch.py` holds the runner: it picks the next free attempt number, writes `prompt.txt` into `paths.attempt_dir(...)`, builds a `models.Dispatch`, asks the resolved `HarnessAdapter` for an argv, runs it through the **injected** `LauncherFn`, classifies the resulting `harness.base.Outcome` plus `result.json` into a verdict, runs the phase's gates over the validated result, and loops per the phase's `retry:` policy. Two small resolution seams the tree lacks are added alongside it: `harness/registry.py` (harness name -> adapter instance) and `results.py` (`result:` name -> pydantic model class). `engine.run_subtask` gains one symmetric addition: it catches the runner's terminal failure and escalates the subtask, exactly as the deterministic branch already does for its own step. It lives in its own module rather than in `engine.py` because `tests/test_engine.py` is already ~1785 lines and the two concerns (walking phases vs. running one dispatch) split cleanly.

**Tech Stack:** Python >=3.12, pydantic v2, pytest (`uv run pytest`), stdlib `json`/`pathlib`/`dataclasses`. No new dependencies.

**Spec:** `docs/superpowers/specs/task-run-agent-phases-bf8e415b-design.md` (prepended verbatim above), which narrows `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §6 lines 261-278, §8 lines 306-318, §9 lines 365-368, §12 lines 429-433, §14 lines 477-492.

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md): new module `src/agent_manager/dispatch.py` -> new test file `tests/test_dispatch.py`; changes to `src/agent_manager/engine.py` -> `tests/test_engine.py`.
- Pydantic models for anything validated at a process boundary (harness result files); plain dataclasses for internal-only state (CLAUDE.md).
- Verification is `uv run pytest`. There is no separate lint or typecheck command.
- Every test added by this plan is **Engine tier** per spec §14:486-488 — a fake adapter and a fake launcher that write canned result files. No harness process is ever started in these tests, and no end-to-end test is added.
- D7 / §8 line 317: `dispatch.py` must never import or call `subprocess`; the only way a process starts is the injected `LauncherFn`.
- D4 / §6 step 5: `stdout.log` is a log, never a channel. The contract is read from `result.json` only.
- §9 line 365: journal before store row. This is satisfied by calling `store.record_phase` / `store.record_attempt`, which already append then write — no new write path may bypass them.
- §6 step 3: the attempt directory and `result.json` come from `paths.attempt_dir(run_id, card, phase, attempt)`, which is rooted under `paths.data_dir()` and therefore outside every worktree. Nothing in this plan derives a run path any other way.
- The four outcome strings are exactly `models.AttemptStatus`'s terminal members: `ok`, `schema_invalid`, `gate_failed`, `harness_error`.
- `retry.on` is already restricted by `workflow.loader.RetryPolicy` to `schema_invalid` / `gate_failed`; no retry count is ever hardcoded.

## Review Focus

- `result.json` holding a JSON array or scalar for a phase that declares no `result:` model — must be `schema_invalid`, not a "result" later phases cannot read. Test in Task 3.
- `result.json` whose bytes are not decodable UTF-8 — must be `schema_invalid` carrying the decode error, not an uncaught `UnicodeDecodeError` out of the walk. Test in Task 3.
- A phase with no `retry:` block at all (`builtin/task.yaml`'s `validate_spec`, `spec`, `plan`, `implement`, `review`) — exactly one dispatch, no retry on any outcome. Test in Task 5.
- A gate that raises instead of returning a verdict, on a phase whose `retry.on` contains `gate_failed` — must escalate immediately and never be retried or swallowed. Tests in Task 4 and Task 5.
- A store write that fails mid-attempt — the journal must already hold the edge, and the walk must escalate rather than report `done`. Tests in Task 5 and Task 6.

---

### Task 1: Attempt directories and feedback-appended prompts

**Files:**
- Create: `src/agent_manager/dispatch.py`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `paths.run_dir(run_id) -> Path` and `paths.attempt_dir(run_id, card, phase, attempt) -> Path` (`src/agent_manager/paths.py:29-40`); `prompt.RenderedPrompt` (frozen dataclass with `phase: str`, `text: str`, `sections: tuple[tuple[str, str], ...]`, and `write(attempt_dir) -> Path`, `src/agent_manager/prompt.py:38-73`).
- Produces: `dispatch.RESULT_NAME = "result.json"`, `dispatch.STDOUT_NAME = "stdout.log"`, `dispatch.next_attempt(run_id: str, card: str, phase: str) -> int`, `dispatch.with_feedback(rendered: prompt.RenderedPrompt, feedback: str) -> prompt.RenderedPrompt`, `dispatch.FEEDBACK_HEADING`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch.py`:

```python
"""Behaviour of one agent phase's dispatch, validation, retry and escalation
(design §6 lines 261-278, §9 lines 365-368, §12 line 429).

Engine tier per design §14 lines 486-488: a fake adapter and a fake launcher
that write canned result files (valid, invalid, gate-failing, absent) stand in
for a harness, and the store is a real temp SQLite projection plus a real temp
JSONL journal. No process is ever started -- the launcher is injected, and one
test asserts `subprocess.Popen` is never reached.
"""

from pathlib import Path

import pytest

from agent_manager import dispatch, paths, prompt

RUN_ID = "run-2026-09-23-01"
CARD = "bf8e415b"


@pytest.fixture
def data_home(monkeypatch, tmp_path):
    """Root every `paths.*` write under tmp_path, as tests/test_paths.py does."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _rendered(text: str = "# phase: explore\n# role: explorer\n") -> prompt.RenderedPrompt:
    return prompt.RenderedPrompt(phase="explore", text=text, sections=(("card", "{}"),))


def test_the_first_attempt_is_numbered_one(data_home):
    assert dispatch.next_attempt(RUN_ID, CARD, "explore") == 1


def test_an_existing_attempt_directory_is_never_reused(data_home):
    # A resumed run may find `explore.1` already on disk; §"Error paths" keeps
    # the discarded attempt as evidence and takes the next free number.
    paths.attempt_dir(RUN_ID, CARD, "explore", 1)
    paths.attempt_dir(RUN_ID, CARD, "explore", 2)

    assert dispatch.next_attempt(RUN_ID, CARD, "explore") == 3


def test_attempt_numbers_are_per_phase(data_home):
    paths.attempt_dir(RUN_ID, CARD, "explore", 1)

    assert dispatch.next_attempt(RUN_ID, CARD, "implement") == 1


def test_the_attempt_directory_is_outside_the_worktree(data_home):
    worktree = data_home / "repo" / ".claude" / "worktrees" / "m1" / "task-bf8e415b"
    worktree.mkdir(parents=True)

    attempt = paths.attempt_dir(RUN_ID, CARD, "explore", 1)

    assert not attempt.resolve().is_relative_to(worktree.resolve())
    assert attempt.name == "explore.1"
    assert attempt.parent.name == CARD
    assert attempt.parent.parent.parent.name == "runs"


def test_feedback_is_appended_below_the_original_prompt_text():
    rendered = _rendered()

    second = dispatch.with_feedback(rendered, "summary: Field required")

    assert second.text.startswith(rendered.text)
    assert dispatch.FEEDBACK_HEADING in second.text
    assert "summary: Field required" in second.text
    assert second.phase == "explore"
    assert rendered.text == "# phase: explore\n# role: explorer\n"


def test_feedback_accumulates_across_attempts():
    once = dispatch.with_feedback(_rendered(), "first complaint")

    twice = dispatch.with_feedback(once, "second complaint")

    assert "first complaint" in twice.text
    assert twice.text.index("first complaint") < twice.text.index("second complaint")


def test_a_written_prompt_lands_in_the_attempt_directory(data_home):
    attempt = paths.attempt_dir(RUN_ID, CARD, "explore", 1)

    written = dispatch.with_feedback(_rendered(), "try again").write(attempt)

    assert written == attempt / "prompt.txt"
    assert "try again" in written.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_manager.dispatch'` (collection error).

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/dispatch.py`:

```python
"""Run one agent phase to a terminal outcome (design §6 lines 261-278).

`engine.run_subtask` resolves an agent phase's `inputs` and renders its prompt,
then hands `(phase, context, rendered)` to an injected `AgentPhaseRunner`
(`engine.py` lines 211-222). This module is that runner: the attempt directory,
the dispatch, the result file, the gates and the retry loop.

Three rules shape everything here, and none of them is negotiable:

- D7 / §8 line 317: nothing in this module starts a process. The argv comes
  from the adapter's `build_command` and is run through the injected
  `LauncherFn`, which is what keeps `bwrap` a later swap and every test above
  the launcher process-free. `subprocess` is deliberately not imported.
- D4 / §6 step 5: the contract is `result.json`. `stdout.log` is captured as a
  log and is only ever handed to `parse_usage`, never parsed for a result.
- §6 step 3: the attempt directory comes from `paths.attempt_dir`, which is
  rooted under `paths.data_dir()` and therefore outside every worktree -- a
  result file written inside the worktree would fail the verify step's
  clean-tree check or be swept into a commit.
"""

from dataclasses import replace

from agent_manager import paths, prompt

RESULT_NAME = "result.json"
"""The result file §6 step 3 puts in every attempt directory."""

STDOUT_NAME = "stdout.log"
"""The captured harness log of one attempt (§6 step 4). A log, not a channel."""

FEEDBACK_HEADING = "## feedback on the previous attempt"
"""Heading of the block §6 step 7 appends before a re-dispatch.

A `##` section, matching `prompt._assemble`'s section format, so the retry
prompt reads as one more input section rather than as a stray paragraph.
"""


def next_attempt(run_id: str, card: str, phase: str) -> int:
    """The lowest attempt number this phase has no directory for yet.

    Scanned from disk rather than counted in memory: a resumed run finds the
    attempts a previous process made, and overwriting one would destroy the
    prompt, result and log that are the only evidence of what happened.
    """
    card_dir = paths.run_dir(run_id) / card
    attempt = 1
    while (card_dir / f"{phase}.{attempt}").exists():
        attempt += 1
    return attempt


def with_feedback(
    rendered: prompt.RenderedPrompt, feedback: str
) -> prompt.RenderedPrompt:
    """The same prompt with one feedback block appended (§6 step 7).

    Appended to whatever it is handed, so a third attempt carries both earlier
    complaints: the spec's "the prior prompt plus the feedback block". Dispatch
    is stateless (D1), so the whole input of every attempt has to be the file on
    disk -- nothing is carried in the harness's head between attempts.
    """
    return replace(
        rendered,
        text=f"{rendered.text}\n{FEEDBACK_HEADING}\n{feedback}\n",
        sections=rendered.sections + (("feedback", feedback),),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "feat: attempt directory numbering and feedback-appended prompts"
```

---

### Task 2: Resolve the harness adapter and the result model

**Files:**
- Create: `src/agent_manager/harness/registry.py`
- Create: `src/agent_manager/results.py`
- Modify: `src/agent_manager/dispatch.py` (add `Target` and `resolve_target`)
- Test: `tests/test_dispatch.py`, `tests/harness/test_registry.py`, `tests/test_results.py`

**Interfaces:**
- Consumes: `harness.base.HarnessAdapter` Protocol with `name: str`, `capabilities: frozenset[str]`, `build_command(d: Dispatch) -> list[str]`, `parse_usage(stdout: str) -> Usage | None` (`src/agent_manager/harness/base.py:75-98`); `harness.claude.ClaudeAdapter` (`name = "claude"`); `models.HarnessAssignment(harness: str, model: str)` (`src/agent_manager/models.py:131-135`); `roles.loader.load_role(name, *, root=None) -> RoleBundle` with `RoleBundle.name` and `RoleBundle.policy.default_model: dict[str, str]`; `errors.EngineError(reason, *, phase=None, function=None, parameter=None)`.
- Produces: `harness.registry.DEFAULT_HARNESS = "claude"`, `harness.registry.default_adapters() -> dict[str, HarnessAdapter]`; `results.RESULT_MODELS: dict[str, type[BaseModel]]`, `results.resolve_result_model(name: str, table: Mapping[str, type[BaseModel]], *, phase: str) -> type[BaseModel]`; `dispatch.Target(adapter: HarnessAdapter, model: str)` and `dispatch.resolve_target(role, harness_map, adapters, *, phase) -> Target`.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/test_registry.py`:

```python
"""Which adapter instance a harness name resolves to (design §6 step 1, §8).

Adapters tier per design §14 line 484 -- this table is pure data about
adapters, and resolving a name starts nothing.
"""

from agent_manager.harness import registry
from agent_manager.harness.claude import ClaudeAdapter


def test_the_default_table_holds_the_claude_adapter_under_its_own_name():
    adapters = registry.default_adapters()

    assert set(adapters) == {"claude"}
    assert isinstance(adapters["claude"], ClaudeAdapter)
    assert adapters["claude"].name == "claude"


def test_the_default_harness_is_the_one_adapter_that_ships():
    assert registry.DEFAULT_HARNESS in registry.default_adapters()


def test_each_call_returns_a_fresh_table_nobody_else_can_poison():
    first = registry.default_adapters()
    first["claude"] = "not an adapter"

    assert isinstance(registry.default_adapters()["claude"], ClaudeAdapter)
```

Create `tests/test_results.py`:

```python
"""Resolving a phase's `result:` name to the model that validates result.json
(design §6 step 5).

Engine tier per design §14 lines 486-488: this is the table the engine's
validation step reads, exercised with a canned model rather than a harness.
"""

import pytest
from pydantic import BaseModel

from agent_manager import results
from agent_manager.errors import EngineError


class Canned(BaseModel):
    summary: str


def test_a_named_model_resolves_to_the_class():
    table = {"Canned": Canned}

    assert results.resolve_result_model("Canned", table, phase="explore") is Canned


def test_an_unknown_result_name_is_a_named_engine_error():
    with pytest.raises(EngineError) as caught:
        results.resolve_result_model("ExploreResult", {"Canned": Canned}, phase="explore")

    assert caught.value.phase == "explore"
    message = str(caught.value)
    assert "'ExploreResult'" in message
    assert "Canned" in message


def test_the_shipped_table_is_empty_and_says_why():
    # The five names in builtin/task.yaml have no field schema anywhere in the
    # design spec; inventing one is not this card's decision. Validating
    # nothing would be worse -- an unknown name fails loudly instead.
    assert results.RESULT_MODELS == {}
```

Append to `tests/test_dispatch.py`:

```python
from agent_manager import models
from agent_manager.errors import EngineError
from agent_manager.harness.base import Usage
from agent_manager.roles.loader import load_role

POLICY = """\
allowed_tools = ["Read"]
max_attempts = 2
required_capabilities = []

[default_model]
claude = "sonnet"
fake = "fake-model"
"""


def make_role(root: Path, name: str = "explorer", *, policy: str = POLICY) -> Path:
    """A synthetic role bundle, built the way tests/roles/test_loader.py does."""
    directory = root / name
    (directory / "methodology").mkdir(parents=True, exist_ok=True)
    (directory / "system.md").write_text(f"Standing instructions for {name}.\n", encoding="utf-8")
    (directory / "policy.toml").write_text(policy, encoding="utf-8")
    (directory / "VENDORED.lock").write_text("vendored = []\n", encoding="utf-8")
    return directory


class FakeAdapter:
    """A `HarnessAdapter` by shape, whose argv names a program nothing runs."""

    capabilities = frozenset({"bash", "edit"})

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.dispatches: list[models.Dispatch] = []

    def build_command(self, d: models.Dispatch) -> list[str]:
        self.dispatches.append(d)
        return ["fake-harness", "--model", d.model, "--result", str(d.result_path)]

    def parse_usage(self, stdout: str) -> Usage | None:
        return Usage(tokens_in=11, tokens_out=22, cost=0.5) if "usage" in stdout else None


def test_an_explicit_harness_assignment_wins(tmp_path):
    role = load_role("explorer", root=make_role(tmp_path).parent)
    adapter = FakeAdapter()

    target = dispatch.resolve_target(
        role,
        {"explorer": models.HarnessAssignment(harness="fake", model="chosen-model")},
        {"fake": adapter},
        phase="explore",
    )

    assert target.adapter is adapter
    assert target.model == "chosen-model"


def test_an_unassigned_role_falls_back_to_the_default_harness_and_its_policy_model(tmp_path):
    role = load_role("explorer", root=make_role(tmp_path).parent)
    adapter = FakeAdapter(name="claude")

    target = dispatch.resolve_target(role, {}, {"claude": adapter}, phase="explore")

    assert target.adapter is adapter
    assert target.model == "sonnet"


def test_a_harness_with_no_adapter_is_a_named_engine_error(tmp_path):
    role = load_role("explorer", root=make_role(tmp_path).parent)

    with pytest.raises(EngineError) as caught:
        dispatch.resolve_target(
            role,
            {"explorer": models.HarnessAssignment(harness="codex", model="o-whatever")},
            {"fake": FakeAdapter()},
            phase="explore",
        )

    assert caught.value.phase == "explore"
    assert "'codex'" in str(caught.value)


def test_a_role_with_no_default_model_for_the_harness_is_a_named_engine_error(tmp_path):
    policy = POLICY.replace('claude = "sonnet"\n', "")
    role = load_role("explorer", root=make_role(tmp_path, policy=policy).parent)

    with pytest.raises(EngineError) as caught:
        dispatch.resolve_target(role, {}, {"claude": FakeAdapter(name="claude")}, phase="explore")

    assert caught.value.phase == "explore"
    assert "default model" in str(caught.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_registry.py tests/test_results.py tests/test_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_manager.harness.registry'`, `No module named 'agent_manager.results'`, and `AttributeError: module 'agent_manager.dispatch' has no attribute 'resolve_target'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/harness/registry.py`:

```python
"""Harness name -> adapter instance (design §6 step 1, §8 lines 306-318).

`Dispatch.harness` and `Policy.default_model`'s keys are both harness names, and
something has to turn one into the object whose `build_command` the engine
calls. That is all this module is. It deliberately does not route roles: which
harness a role gets is `RunConfig.harness_map`'s answer, read by
`dispatch.resolve_target`.

A fresh dict per call, for the reason `workflow.registry.default_registry`
gives: a module-level singleton is mutable global state any importer could
rebind an adapter in.
"""

from agent_manager.harness.base import HarnessAdapter
from agent_manager.harness.claude import ClaudeAdapter

DEFAULT_HARNESS = "claude"
"""The harness a role with no `harness_map` entry is dispatched to.

`claude` because it is the only adapter implemented (§4 line 131 names `codex`
and `pi` as later cards). Named as a constant so adding the second adapter is a
one-line decision in one place rather than a scattered default.
"""


def default_adapters() -> dict[str, HarnessAdapter]:
    """Every adapter that ships, keyed by its own `name`."""
    return {ClaudeAdapter.name: ClaudeAdapter()}
```

Create `src/agent_manager/results.py`:

```python
"""`result:` names -> the pydantic model that validates one result file (§6 step 5).

`workflow/loader.py` keeps `AgentPhase.result` a string on purpose ("mapping it
to a class is the agent-dispatch sibling's job"). This module is that mapping,
and nothing more: no validation happens here, and importing it reads no file.

The table ships empty, and that is deliberate. `builtin/task.yaml` names five
result models -- `ExploreResult`, `CriticResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` -- and the design spec gives a field schema
for none of them, so inventing one here would be a design decision this card
was not given. An unresolved name fails loudly at dispatch time instead, which
is strictly better than validating nothing and calling the result `ok`.
"""

from collections.abc import Mapping

from pydantic import BaseModel

from agent_manager.errors import EngineError

RESULT_MODELS: dict[str, type[BaseModel]] = {}
"""Every `result:` name with a model behind it. Empty on this branch."""


def resolve_result_model(
    name: str, table: Mapping[str, type[BaseModel]], *, phase: str
) -> type[BaseModel]:
    """The model class `name` refers to, or an `EngineError` naming the phase."""
    model = table.get(name)
    if model is None:
        raise EngineError(
            f"declares result {name!r}, which no result model is registered for "
            f"(registered: {', '.join(sorted(table)) or 'nothing'}); a result file "
            "cannot be validated against a model that does not exist",
            phase=phase,
        )
    return model
```

Append to `src/agent_manager/dispatch.py` (imports at the top of the module, code below `with_feedback`):

```python
from collections.abc import Mapping
from dataclasses import dataclass, replace

from agent_manager import models, paths, prompt
from agent_manager.errors import EngineError
from agent_manager.harness.base import HarnessAdapter
from agent_manager.harness.registry import DEFAULT_HARNESS
from agent_manager.roles.loader import RoleBundle
```

```python
@dataclass(frozen=True)
class Target:
    """Where one phase's dispatch is going: which adapter, which model."""

    adapter: HarnessAdapter
    model: str


def resolve_target(
    role: RoleBundle,
    harness_map: Mapping[str, models.HarnessAssignment],
    adapters: Mapping[str, HarnessAdapter],
    *,
    phase: str,
) -> Target:
    """§6 step 1: the harness and model assigned to this phase's role.

    `RunConfig.harness_map` is the run's explicit answer and wins outright. With
    no entry, the role falls back to `DEFAULT_HARNESS` and to the model its own
    `policy.toml` records for that harness -- D6 puts the default model in the
    bundle precisely so a run that configures nothing still dispatches.

    Both failures are `EngineError` naming the phase rather than a retryable
    outcome: no re-dispatch fixes a missing adapter or a missing default model.
    """
    assignment = harness_map.get(role.name)
    harness = DEFAULT_HARNESS if assignment is None else assignment.harness
    adapter = adapters.get(harness)
    if adapter is None:
        raise EngineError(
            f"role {role.name!r} is routed to harness {harness!r}, which has no "
            f"adapter (adapters: {', '.join(sorted(adapters)) or 'none'})",
            phase=phase,
        )
    if assignment is not None:
        return Target(adapter=adapter, model=assignment.model)
    model = role.policy.default_model.get(harness)
    if model is None:
        raise EngineError(
            f"role {role.name!r} has no default model for harness {harness!r} in its "
            f"policy.toml (it has: "
            f"{', '.join(sorted(role.policy.default_model)) or 'nothing'}) and the "
            "run's harness_map assigns none",
            phase=phase,
        )
    return Target(adapter=adapter, model=model)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_registry.py tests/test_results.py tests/test_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/harness/registry.py src/agent_manager/results.py src/agent_manager/dispatch.py tests/harness/test_registry.py tests/test_results.py tests/test_dispatch.py
git commit -m "feat: resolve a phase's harness adapter and result model"
```

---

### Task 3: One attempt — build the dispatch, launch it, classify the outcome

**Files:**
- Modify: `src/agent_manager/dispatch.py`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `models.Dispatch(harness, model, role, cwd, prompt_path, result_path, timeout)` (`src/agent_manager/models.py:53-70`); `harness.base.Outcome(argv: list[str], exit_code: int | None, timed_out: bool, duration: float, stdout_path: Path)` (`src/agent_manager/harness/base.py:53-72`); `harness.launcher.LauncherFn.__call__(argv, *, cwd, timeout, stdout_path) -> Outcome`; `models.AttemptStatus`.
- Produces: `dispatch.DEFAULT_TIMEOUT = 1800.0`, `dispatch.Verdict(status: models.AttemptStatus, result: Any = None, detail: str | None = None, fatal: bool = False)`, `dispatch.build_dispatch(*, target, role, cwd, prompt_path, attempt_dir, timeout) -> models.Dispatch`, `dispatch.classify(outcome, result_path, model) -> Verdict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch.py`:

```python
import json
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from agent_manager.harness.base import Outcome


class FakeResult(BaseModel):
    """The canned result model the fake phases in this file declare."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    ok: bool = True


VALID_RESULT = json.dumps({"summary": "explored the tree", "ok": True})
INVALID_RESULT = json.dumps({"ok": True})
NOT_JSON = "I could not produce JSON, sorry."


@dataclass
class FakeLauncher:
    """A `LauncherFn` double that writes canned files instead of running anything.

    `results[i]` is attempt i+1's `result.json` text, or `None` to write no
    result file at all; the last entry repeats for any further attempt.
    """

    results: list[str | None]
    stdout: str = "usage: tokens\n"
    exit_code: int | None = 0
    timed_out: bool = False
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        self.calls.append(list(argv))
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(self.stdout, encoding="utf-8")
        canned = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if canned is not None:
            result_path = Path(argv[argv.index("--result") + 1])
            result_path.write_text(canned, encoding="utf-8")
        return Outcome(
            argv=list(argv),
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            duration=1.25,
            stdout_path=stdout_path,
        )


def _outcome(tmp_path: Path, *, exit_code: int | None = 0, timed_out: bool = False) -> Outcome:
    log = tmp_path / "stdout.log"
    log.write_text("usage: tokens\n", encoding="utf-8")
    return Outcome(
        argv=["fake-harness"],
        exit_code=exit_code,
        timed_out=timed_out,
        duration=1.25,
        stdout_path=log,
    )


def test_a_dispatch_points_at_the_attempt_directorys_result_file(data_home, tmp_path):
    role = load_role("explorer", root=make_role(tmp_path / "bundles").parent)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    attempt = paths.attempt_dir(RUN_ID, CARD, "explore", 1)
    prompt_path = _rendered().write(attempt)

    built = dispatch.build_dispatch(
        target=dispatch.Target(adapter=FakeAdapter(), model="fake-model"),
        role=role,
        cwd=worktree,
        prompt_path=prompt_path,
        attempt_dir=attempt,
        timeout=90.0,
    )

    assert built.harness == "fake"
    assert built.model == "fake-model"
    assert built.role == "explorer"
    assert built.cwd == worktree
    assert built.prompt_path == attempt / "prompt.txt"
    assert built.result_path == attempt / "result.json"
    assert built.timeout == 90.0


def test_a_valid_result_file_classifies_ok(data_home, tmp_path):
    result = tmp_path / "result.json"
    result.write_text(VALID_RESULT, encoding="utf-8")

    verdict = dispatch.classify(_outcome(tmp_path), result, FakeResult)

    assert verdict.status == "ok"
    assert verdict.result == {"summary": "explored the tree", "ok": True}


def test_a_result_that_fails_the_model_classifies_schema_invalid(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(INVALID_RESULT, encoding="utf-8")

    verdict = dispatch.classify(_outcome(tmp_path), result, FakeResult)

    assert verdict.status == "schema_invalid"
    assert "summary" in verdict.detail


def test_a_non_json_result_classifies_schema_invalid(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(NOT_JSON, encoding="utf-8")

    verdict = dispatch.classify(_outcome(tmp_path), result, FakeResult)

    assert verdict.status == "schema_invalid"
    assert "not valid JSON" in verdict.detail


def test_a_result_file_that_is_not_utf8_classifies_schema_invalid(tmp_path):
    # Review Focus: a harness that writes latin-1 bytes must not crash the walk.
    result = tmp_path / "result.json"
    result.write_bytes(b'{"summary": "caf\xe9"}')

    verdict = dispatch.classify(_outcome(tmp_path), result, FakeResult)

    assert verdict.status == "schema_invalid"
    assert "UTF-8" in verdict.detail


def test_a_phase_with_no_result_model_takes_the_json_object_as_its_result(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"anything": [1, 2]}), encoding="utf-8")

    verdict = dispatch.classify(_outcome(tmp_path), result, None)

    assert verdict.status == "ok"
    assert verdict.result == {"anything": [1, 2]}


def test_a_json_array_with_no_result_model_classifies_schema_invalid(tmp_path):
    # Review Focus: later phases and every gate read a mapping; a list would
    # bind as a phase result nothing downstream can read.
    result = tmp_path / "result.json"
    result.write_text("[1, 2, 3]", encoding="utf-8")

    verdict = dispatch.classify(_outcome(tmp_path), result, None)

    assert verdict.status == "schema_invalid"
    assert "JSON object" in verdict.detail


def test_a_missing_result_file_after_exit_zero_classifies_harness_error(tmp_path):
    verdict = dispatch.classify(_outcome(tmp_path), tmp_path / "absent.json", FakeResult)

    assert verdict.status == "harness_error"
    assert "no result file" in verdict.detail


def test_a_non_zero_exit_classifies_harness_error(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(VALID_RESULT, encoding="utf-8")

    verdict = dispatch.classify(_outcome(tmp_path, exit_code=2), result, FakeResult)

    assert verdict.status == "harness_error"
    assert "exited 2" in verdict.detail


def test_a_timeout_classifies_harness_error_and_never_reads_the_result(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(VALID_RESULT, encoding="utf-8")

    verdict = dispatch.classify(
        _outcome(tmp_path, exit_code=None, timed_out=True), result, FakeResult
    )

    assert verdict.status == "harness_error"
    assert "timed out" in verdict.detail
    assert verdict.result is None


def test_stdout_is_never_the_channel(tmp_path):
    # Spec test 15: a perfectly good result in the log does not rescue a bad
    # result file (D4).
    result = tmp_path / "result.json"
    result.write_text(INVALID_RESULT, encoding="utf-8")
    outcome = _outcome(tmp_path)
    outcome.stdout_path.write_text(VALID_RESULT, encoding="utf-8")

    verdict = dispatch.classify(outcome, result, FakeResult)

    assert verdict.status == "schema_invalid"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: FAIL — `AttributeError: module 'agent_manager.dispatch' has no attribute 'build_dispatch'` / `'classify'`.

- [ ] **Step 3: Write the minimal implementation**

Add to the imports of `src/agent_manager/dispatch.py`:

```python
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_manager.harness.base import Outcome
```

Append to `src/agent_manager/dispatch.py`:

```python
DEFAULT_TIMEOUT = 1800.0
"""Wall-clock seconds one attempt gets, matching `models.Dispatch.timeout`'s own
default (§8 line 315): a ceiling that stops a wedged process, not a budget."""


@dataclass(frozen=True)
class Verdict:
    """How one attempt ended, before the retry policy is consulted.

    Internal-only state, so a dataclass rather than a pydantic model (CLAUDE.md).

    `status` is one of §6 line 277's four outcomes and is the value journalled.
    `fatal` marks a failure no `retry.on` list can make retryable -- a gate that
    raised instead of returning a verdict, per the spec's error paths -- and is
    kept separate from `status` because the journalled outcome name is part of
    the contract and must stay one of the four.
    """

    status: models.AttemptStatus
    result: Any = None
    detail: str | None = None
    fatal: bool = False


def build_dispatch(
    *,
    target: Target,
    role: RoleBundle,
    cwd: Path,
    prompt_path: Path,
    attempt_dir: Path,
    timeout: float,
) -> models.Dispatch:
    """Everything one harness process needs, for one attempt (§8 lines 315-318).

    The result path is inside the attempt directory and therefore outside the
    worktree (§6 step 3); `cwd` is the subtask worktree, which is where D7 pins
    the harness.
    """
    return models.Dispatch(
        harness=target.adapter.name,
        model=target.model,
        role=role.name,
        cwd=cwd,
        prompt_path=prompt_path,
        result_path=attempt_dir / RESULT_NAME,
        timeout=timeout,
    )


def classify(
    outcome: Outcome, result_path: Path, model: type[BaseModel] | None
) -> Verdict:
    """One attempt's outcome, from the launcher's report and the result file.

    The order is §6 line 278's, and it is load-bearing: a timeout or a non-zero
    exit is a `harness_error` whatever is on disk, and a missing file after a
    clean exit is a `harness_error` too -- nothing is parsed in either case.
    Only past those does the file get read, and from there every failure is
    `schema_invalid`, because a file that exists and cannot be validated is
    exactly what re-dispatching with the validator's text can fix.

    A verdict of `ok` here means "the result file is good"; the gates run after
    and may still turn it into `gate_failed`.

    A validated result is returned as its JSON-mode dump rather than as the
    model instance: the ported gates read it with `Mapping.get`
    (`steps/reducers.py`), and §7 inlines it into a later phase's prompt as
    JSON. Handing them a `BaseModel` would make every gate silently read `None`.
    """
    if outcome.timed_out:
        return Verdict(
            "harness_error",
            detail=f"the harness timed out and was killed after {outcome.duration:.1f}s",
        )
    if outcome.exit_code != 0:
        return Verdict("harness_error", detail=f"the harness exited {outcome.exit_code}")
    if not result_path.is_file():
        return Verdict(
            "harness_error", detail=f"the harness wrote no result file at {result_path}"
        )
    try:
        text = result_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return Verdict(
            "schema_invalid", detail=f"{result_path} is not valid UTF-8 text: {error}"
        )
    except OSError as error:
        return Verdict(
            "schema_invalid",
            detail=f"{result_path} cannot be read: {type(error).__name__}: {error}",
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return Verdict("schema_invalid", detail=f"{result_path} is not valid JSON: {error}")
    if model is None:
        if not isinstance(data, dict):
            return Verdict(
                "schema_invalid",
                detail=(
                    f"{result_path} must hold a JSON object, got "
                    f"{type(data).__name__}: later phases and every gate read the "
                    "result by key"
                ),
            )
        return Verdict("ok", result=data)
    try:
        validated = model.model_validate(data)
    except ValidationError as error:
        return Verdict("schema_invalid", detail=str(error))
    return Verdict("ok", result=validated.model_dump(mode="json"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "feat: build one attempt's dispatch and classify its outcome"
```

---

### Task 4: Gates over the validated result

**Files:**
- Modify: `src/agent_manager/dispatch.py`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `engine.RESERVED_CONTEXT_KEYS` and `engine.bind_arguments(fn, values, args=None, *, phase, function)` (`src/agent_manager/engine.py:37-48`, `153-202`); `workflow.loader.Workflow.function(name) -> Function`; `workflow.loader.AgentPhase.gates: list[str]`. `dispatch.py` imports `engine`; `engine.py` must never import `dispatch` (that is what keeps the import one-way).
- Produces: `dispatch.gate_values(context, phase_name, result) -> dict[str, Any]`, `dispatch.evaluate_gates(phase, workflow, values, warnings) -> Verdict | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch.py`:

```python
from agent_manager import engine
from agent_manager.workflow.loader import load_workflow
from agent_manager.workflow.registry import FunctionRegistry

AGENT_DOCUMENT = """
name: agentic
phases:
  - name: explore
    kind: agent
    role: explorer
    result: FakeResult
    gates: [output_gate]
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }
"""


def _workflow(document: str, functions: dict[str, object]):
    registry = FunctionRegistry()
    for name, fn in functions.items():
        registry.register(name, fn)
    return load_workflow(document, registry)


def test_a_passing_gate_returns_no_verdict():
    seen: list[object] = []

    def output_gate(result):
        seen.append(result)
        return None

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})
    phase = workflow.phase("explore")
    warnings: list[str] = []

    verdict = dispatch.evaluate_gates(
        phase,
        workflow,
        dispatch.gate_values({"card": CARD}, "explore", {"summary": "ok"}),
        warnings,
    )

    assert verdict is None
    assert seen == [{"summary": "ok"}]
    assert warnings == []


def test_the_result_is_bound_under_both_result_and_the_phase_name():
    values = dispatch.gate_values({"card": CARD}, "explore", {"summary": "ok"})

    assert values["result"] == {"summary": "ok"}
    assert values["explore"] == {"summary": "ok"}
    assert values["card"] == CARD


def test_a_reserved_key_is_not_overwritten_by_a_same_named_phase():
    values = dispatch.gate_values({"worktree": Path("/repo/wt")}, "worktree", {"created": True})

    assert values["worktree"] == Path("/repo/wt")
    assert values["result"] == {"created": True}
    assert "worktree" in engine.RESERVED_CONTEXT_KEYS


def test_a_failing_gate_returns_a_retryable_gate_failed_verdict():
    def output_gate(result):
        return {"blocked": "exploration", "detail": "summary is a placeholder"}

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})

    verdict = dispatch.evaluate_gates(
        workflow.phase("explore"),
        workflow,
        dispatch.gate_values({}, "explore", {"summary": "test"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is False
    assert "summary is a placeholder" in verdict.detail
    assert "'output_gate'" in verdict.detail


def test_a_warning_gate_is_recorded_and_does_not_fail_the_attempt():
    def output_gate(result):
        return {"warn": "counts unusable, plan-hash check skipped"}

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})
    warnings: list[str] = []

    verdict = dispatch.evaluate_gates(
        workflow.phase("explore"),
        workflow,
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        warnings,
    )

    assert verdict is None
    assert warnings == [
        "phase 'explore' gate 'output_gate' warned: counts unusable, plan-hash check skipped"
    ]


def test_a_gate_that_raises_is_a_fatal_gate_failure():
    # Review Focus / spec error paths: never swallowed, never retried, even
    # though this phase lists gate_failed in retry.on.
    def output_gate(result):
        raise RuntimeError("the gate itself is broken")

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})

    verdict = dispatch.evaluate_gates(
        workflow.phase("explore"),
        workflow,
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is True
    assert "RuntimeError" in verdict.detail
    assert "the gate itself is broken" in verdict.detail


def test_a_gate_returning_a_non_mapping_is_a_fatal_gate_failure():
    def output_gate(result):
        return "looks fine to me"

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})

    verdict = dispatch.evaluate_gates(
        workflow.phase("explore"),
        workflow,
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is True
    assert "str" in verdict.detail


def test_a_gate_whose_parameter_nothing_supplies_is_a_named_engine_error():
    def output_gate(result, provided_verification):
        return None

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})

    with pytest.raises(EngineError) as caught:
        dispatch.evaluate_gates(
            workflow.phase("explore"),
            workflow,
            dispatch.gate_values({}, "explore", {"summary": "ok"}),
            [],
        )

    assert caught.value.parameter == "provided_verification"
    assert caught.value.function == "output_gate"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: FAIL — `AttributeError: module 'agent_manager.dispatch' has no attribute 'gate_values'` / `'evaluate_gates'`.

- [ ] **Step 3: Write the minimal implementation**

Add to the imports of `src/agent_manager/dispatch.py`:

```python
from agent_manager import engine
from agent_manager.workflow.loader import AgentPhase, Workflow
```

Append to `src/agent_manager/dispatch.py`:

```python
def gate_values(
    context: Mapping[str, Any], phase_name: str, result: Any
) -> dict[str, Any]:
    """The binding table this phase's gates see.

    The same table `engine._gate_values` builds for a deterministic phase, and
    for the same two reasons: the result appears under `result` (the parameter
    name the ported gates in `steps/reducers.py` declare) and under the phase's
    own name (how §6 says later phases read it), except where that name is one
    of the keys the engine owns.
    """
    values = {**context, "result": result}
    if phase_name not in engine.RESERVED_CONTEXT_KEYS:
        values[phase_name] = result
    return values


def _render_verdict(verdict: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(verdict.items()))


def evaluate_gates(
    phase: AgentPhase,
    workflow: Workflow,
    values: Mapping[str, Any],
    warnings: list[str],
) -> Verdict | None:
    """`None` when every gate passes, else the `gate_failed` verdict (§6 step 6).

    A gate returns `None` to pass, a mapping with `warn` to warn, or any other
    mapping to fail -- the contract `_evaluate_gates` already applies to
    deterministic phases. No per-gate retryable flag exists and this subtask
    does not add one: whether a `gate_failed` is retried is `retry.on`'s answer
    alone.

    The two ways a gate can be *wrong* rather than unhappy -- raising, or
    returning something that is not a mapping -- come back `fatal`, so no
    `retry.on` list can re-dispatch into a situation the harness cannot change.
    A binding failure is different again and propagates as `EngineError`: it
    means the document names a gate whose parameters nothing supplies, which is
    a bug in the document, not in the attempt.
    """
    for name in phase.gates:
        gate = workflow.function(name)
        kwargs = engine.bind_arguments(gate, values, phase=phase.name, function=name)
        try:
            verdict = gate(**kwargs)
        except Exception as error:
            return Verdict(
                "gate_failed",
                detail=(
                    f"phase {phase.name!r} gate {name!r} raised "
                    f"{type(error).__name__}: {error}; a gate returns None to pass or "
                    "a mapping verdict to fail, so this is a broken gate rather than a "
                    "failed attempt"
                ),
                fatal=True,
            )
        if verdict is None:
            continue
        if not isinstance(verdict, Mapping):
            return Verdict(
                "gate_failed",
                detail=(
                    f"phase {phase.name!r} gate {name!r} returned "
                    f"{type(verdict).__name__}; a gate returns None to pass or a "
                    "mapping verdict to fail, and anything else would be read as a "
                    "pass by accident"
                ),
                fatal=True,
            )
        if "warn" in verdict:
            warnings.append(
                f"phase {phase.name!r} gate {name!r} warned: {verdict['warn']}"
            )
            continue
        return Verdict(
            "gate_failed",
            detail=(
                f"phase {phase.name!r} gate {name!r} failed: {_render_verdict(verdict)}"
            ),
        )
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "feat: run an agent phase's gates over the validated result"
```

---

### Task 5: The runner — journalled attempts, retry policy, escalation

**Files:**
- Modify: `src/agent_manager/errors.py`
- Modify: `src/agent_manager/dispatch.py`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `store.Store.record_phase(story_id, card_id, models.PhaseRun) -> JournalLine` and `store.Store.record_attempt(story_id, card_id, phase_name, models.Attempt) -> JournalLine` (both journal-then-row, `src/agent_manager/store.py:382-407`); `models.PhaseRun(name, kind, status, started_at, ended_at, detail)`; `models.Attempt(n, dispatch, status, exit_code, duration, tokens_in, tokens_out, cost, prompt_path, result_path, stdout_path)`; `harness.base.Usage(tokens_in, tokens_out, cost)`; `workflow.loader.RetryPolicy(max_attempts: int, on: list[...])`; everything produced by Tasks 1-4.
- Produces: `errors.AgentPhaseFailed(phase, *, outcome, detail)` with attributes `phase`, `outcome`, `detail`; `dispatch.AgentRunner` dataclass satisfying `engine.AgentPhaseRunner`, constructed as `AgentRunner(workflow=..., store=..., launcher=..., run_id=..., story_id=..., card_id=..., adapters=..., result_models=..., harness_map=..., role_root=..., timeout=..., clock=...)` with a public `warnings: list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch.py`:

```python
import subprocess

from agent_manager import models, store as store_module
from agent_manager.errors import AgentPhaseFailed

STORY_ID = "2143808b"


@pytest.fixture
def store(data_home, tmp_path):
    """A real temp projection plus a real temp journal, writing nowhere real."""
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


@pytest.fixture
def worktree(tmp_path):
    path = tmp_path / "worktree"
    path.mkdir()
    return path


def _runner(store, workflow, launcher, tmp_path, worktree, **overrides):
    """A runner wired to the fakes, plus the adapter it was wired to.

    `overrides` replaces any keyword (e.g. `result_models={}`) rather than
    adding a second one, so a test can knock out exactly one seam.
    """
    adapter = overrides.pop("adapter", FakeAdapter())
    make_role(tmp_path / "bundles")
    kwargs = {
        "workflow": workflow,
        "store": store,
        "launcher": launcher,
        "run_id": RUN_ID,
        "story_id": STORY_ID,
        "card_id": CARD,
        "adapters": {adapter.name: adapter},
        "result_models": {"FakeResult": FakeResult},
        "harness_map": {
            "explorer": models.HarnessAssignment(harness=adapter.name, model="fake-model")
        },
        "role_root": tmp_path / "bundles",
        "timeout": 45.0,
    }
    kwargs.update(overrides)
    return dispatch.AgentRunner(**kwargs), adapter


def _context(worktree: Path) -> dict[str, object]:
    return {"card": CARD, "worktree": worktree, "repo_dir": worktree.parent}


def _attempt_statuses(opened) -> list[tuple[int | None, str]]:
    return [
        (line.attempt, line.payload["status"])
        for line in opened.journal.read()
        if line.event == "attempt_upsert"
    ]


def _phase_statuses(opened) -> list[tuple[str | None, str]]:
    return [
        (line.phase, line.payload["status"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def test_a_valid_result_with_passing_gates_is_the_phase_result(store, tmp_path, worktree):
    # Spec tests 1 and 2.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, adapter = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    attempt = paths.attempt_dir(RUN_ID, CARD, "explore", 1)
    assert (attempt / "prompt.txt").is_file()
    assert (attempt / "result.json").is_file()
    assert (attempt / "stdout.log").is_file()
    assert not attempt.resolve().is_relative_to(worktree.resolve())
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "done")]
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_the_injected_launcher_is_the_only_way_a_process_could_start(
    store, tmp_path, worktree, monkeypatch
):
    # Spec test 3. The argv is the adapter's, verbatim, and nothing reaches
    # subprocess -- D7 keeps process spawning behind the launcher seam.
    def explode(*args, **kwargs):
        raise AssertionError("dispatch.py must never spawn a process itself")

    monkeypatch.setattr(subprocess, "Popen", explode)
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, adapter = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert len(launcher.calls) == 1
    assert launcher.calls[0] == adapter.build_command(adapter.dispatches[0])
    assert launcher.calls[0][0] == "fake-harness"


def test_usage_parsed_from_the_log_is_journalled_on_the_attempt(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    runner, _ = _runner(store, workflow, FakeLauncher(results=[VALID_RESULT]), tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    terminal = [
        line.payload
        for line in store.journal.read()
        if line.event == "attempt_upsert" and line.payload["status"] == "ok"
    ][0]
    assert terminal["tokens_in"] == 11
    assert terminal["tokens_out"] == 22
    assert terminal["cost"] == 0.5
    assert terminal["duration"] == 1.25
    assert terminal["exit_code"] == 0


def test_a_persistently_invalid_result_retries_to_max_attempts_then_fails(
    store, tmp_path, worktree
):
    # Spec tests 4, 10 and 12.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.phase == "explore"
    assert caught.value.outcome == "schema_invalid"
    assert len(launcher.calls) == 2
    second = paths.attempt_dir(RUN_ID, CARD, "explore", 2) / "prompt.txt"
    text = second.read_text(encoding="utf-8")
    assert text.startswith("# phase: explore")
    assert dispatch.FEEDBACK_HEADING in text
    assert "summary" in text.split(dispatch.FEEDBACK_HEADING, 1)[1]
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "schema_invalid"), (2, "started"), (2, "schema_invalid")
    ]
    assert _phase_statuses(store)[-1] == ("explore", "failed")


def test_a_non_json_result_is_schema_invalid_and_retried(store, tmp_path, worktree):
    # Spec test 5.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[NOT_JSON, VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "schema_invalid"), (2, "started"), (2, "ok")
    ]


def test_a_missing_result_file_is_harness_error_and_is_not_retried(store, tmp_path, worktree):
    # Spec tests 6 and 13: harness_error can never appear in retry.on, so
    # attempts remaining does not mean a re-dispatch.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "harness_error")]


def test_a_non_zero_exit_is_harness_error(store, tmp_path, worktree):
    # Spec test 7.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], exit_code=3)
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert "exited 3" in caught.value.detail


def test_a_timeout_is_harness_error_and_the_log_survives(store, tmp_path, worktree):
    # Spec test 8.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None], exit_code=None, timed_out=True)
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert (paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "stdout.log").is_file()


def test_a_retryable_gate_failure_re_dispatches_with_the_gate_detail(
    store, tmp_path, worktree
):
    # Spec tests 9 and 11.
    verdicts = [{"blocked": "exploration", "detail": "summary is a placeholder"}, None]

    def output_gate(result):
        return verdicts.pop(0)

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    second = (paths.attempt_dir(RUN_ID, CARD, "explore", 2) / "prompt.txt").read_text(
        encoding="utf-8"
    )
    assert "summary is a placeholder" in second
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "gate_failed"), (2, "started"), (2, "ok")
    ]


def test_a_gate_failure_outside_retry_on_is_not_retried(store, tmp_path, worktree):
    # Spec test 13, the gate_failed half.
    document = AGENT_DOCUMENT.replace(
        "on: [schema_invalid, gate_failed]", "on: [schema_invalid]"
    )
    workflow = _workflow(document, {"output_gate": lambda result: {"blocked": "exploration"}})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert len(launcher.calls) == 1


def test_a_gate_that_raises_stops_after_one_dispatch(store, tmp_path, worktree):
    # Review Focus: fatal beats retry.on, which lists gate_failed here.
    def output_gate(result):
        raise RuntimeError("the gate itself is broken")

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert "RuntimeError" in caught.value.detail
    assert len(launcher.calls) == 1


def test_a_phase_with_no_retry_block_dispatches_exactly_once(store, tmp_path, worktree):
    # Review Focus: builtin/task.yaml's spec, plan, implement and review phases
    # carry no retry: block at all.
    document = """
name: agentic
phases:
  - name: explore
    kind: agent
    role: explorer
    result: FakeResult
"""
    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "schema_invalid"
    assert len(launcher.calls) == 1


def test_a_phase_with_no_result_model_declared_needs_no_table_entry(
    store, tmp_path, worktree
):
    document = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    writes: docs/superpowers/specs/{stem}.md
"""
    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[json.dumps({"wrote": "docs/spec.md"})])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result == {"wrote": "docs/spec.md"}


def test_an_unregistered_result_model_is_a_named_engine_error(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(
        store, workflow, launcher, tmp_path, worktree, result_models={}
    )

    with pytest.raises(EngineError) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.phase == "explore"
    assert launcher.calls == []


def test_a_context_with_no_worktree_is_a_named_engine_error(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(EngineError) as caught:
        runner(workflow.phase("explore"), {"card": CARD, "worktree": None}, _rendered())

    assert caught.value.phase == "explore"
    assert "worktree" in str(caught.value)
    assert launcher.calls == []


def test_the_journal_holds_the_edge_even_when_the_row_write_fails(
    data_home, tmp_path, worktree
):
    # Spec test 16 (§9 line 365: journal first, row second, journal is truth).
    class ExplodingStore(store_module.Store):
        def _write_attempt_row(self, *args, **kwargs):
            raise RuntimeError("the projection is on fire")

    opened = ExplodingStore.open(tmp_path / "repo", RUN_ID)
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(opened, workflow, launcher, tmp_path, worktree)

    try:
        with pytest.raises(RuntimeError, match="the projection is on fire"):
            runner(workflow.phase("explore"), _context(worktree), _rendered())

        assert _attempt_statuses(opened) == [(1, "started")]
        assert opened.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    finally:
        opened.close()


def test_all_four_outcome_names_are_journalled_as_distinct_values(
    store, tmp_path, worktree
):
    # Spec test 17: one runner, four phases, four different journalled outcomes.
    document = """
name: four
phases:
  - name: explore
    kind: agent
    role: explorer
    result: FakeResult
    gates: [output_gate]
"""
    gate_verdicts = {"good": None, "bad": {"blocked": "exploration"}}
    mode = {"gate": "good"}

    def output_gate(result):
        return gate_verdicts[mode["gate"]]

    workflow = _workflow(document, {"output_gate": output_gate})
    phase = workflow.phase("explore")

    seen: list[str] = []
    for canned, gate, exit_code in (
        (VALID_RESULT, "good", 0),
        (INVALID_RESULT, "good", 0),
        (VALID_RESULT, "bad", 0),
        (None, "good", 0),
    ):
        mode["gate"] = gate
        launcher = FakeLauncher(results=[canned], exit_code=exit_code)
        runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)
        try:
            runner(phase, _context(worktree), _rendered())
            seen.append("ok")
        except AgentPhaseFailed as failure:
            seen.append(failure.outcome)

    assert seen == ["ok", "schema_invalid", "gate_failed", "harness_error"]
    journalled = {status for _n, status in _attempt_statuses(store)}
    assert journalled == {"started", "ok", "schema_invalid", "gate_failed", "harness_error"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: FAIL — `ImportError: cannot import name 'AgentPhaseFailed' from 'agent_manager.errors'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/errors.py`:

```python
class AgentPhaseFailed(RuntimeError):
    """An agent phase ended on a terminal failure, with its attempts recorded.

    Raised rather than returned because `engine.AgentPhaseRunner` is typed
    `(phase, context, rendered) -> result`: there is no second channel in that
    signature, and returning a sentinel result would be indistinguishable from a
    phase whose harness genuinely produced one. `run_subtask` catches it and
    turns it into `summary.status = "escalated"` -- the same edge the
    deterministic branch reaches through `_Outcome(ok=False, ...)`.

    `outcome` is one of §6 line 277's four journalled names, so an operator
    reading the message knows whether to fix a prompt, a gate or a harness.
    """

    def __init__(self, phase: str, *, outcome: str, detail: str) -> None:
        self.phase = phase
        self.outcome = outcome
        self.detail = detail
        super().__init__(f"phase {phase!r} ended {outcome}: {detail}")
```

Add to the imports of `src/agent_manager/dispatch.py`:

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from agent_manager import models, paths, prompt, results
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.harness.base import HarnessAdapter, Outcome, Usage
from agent_manager.harness.launcher import LauncherFn
from agent_manager.harness.registry import DEFAULT_HARNESS, default_adapters
from agent_manager.roles.loader import RoleBundle, load_role
from agent_manager.store import Store
```

Append to `src/agent_manager/dispatch.py`:

```python
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


Clock = Callable[[], datetime]


@dataclass
class AgentRunner:
    """One agent phase, run to a terminal outcome: `engine.AgentPhaseRunner`.

    A callable object rather than a function because the seam's signature is
    `(phase, context, rendered)` and a dispatch needs six more things -- the
    store to journal into, the run and card ids the attempt directory is keyed
    by, the injected launcher, the adapter table and the result-model table.
    They are bound once, at run start, and the walk stays ignorant of all of it.

    `warnings` is the one out-of-band channel: gate warnings have nowhere to go
    in a signature that returns a result, and dropping them would reproduce the
    exact failure §12 calls out -- a run that reports success while something it
    was told about never happened. The caller that constructs the runner reads
    this list when the walk returns.
    """

    workflow: Workflow
    store: Store
    launcher: LauncherFn
    run_id: str
    story_id: str
    card_id: str
    adapters: Mapping[str, HarnessAdapter] = field(default_factory=default_adapters)
    result_models: Mapping[str, type[BaseModel]] = field(
        default_factory=lambda: dict(results.RESULT_MODELS)
    )
    harness_map: Mapping[str, models.HarnessAssignment] = field(default_factory=dict)
    role_root: Path | None = None
    timeout: float = DEFAULT_TIMEOUT
    clock: Clock = _utcnow
    warnings: list[str] = field(default_factory=list)

    def __call__(
        self,
        phase: AgentPhase,
        context: Mapping[str, Any],
        rendered: prompt.RenderedPrompt,
    ) -> Any:
        """§6 steps 1-8 for one phase. Returns its result, or raises.

        The whole resolution half (role bundle, harness, result model, worktree)
        happens before the phase is recorded `started`-and-dispatched, so a
        document bug costs nothing and journals no attempt.
        """
        role = load_role(phase.role, root=self.role_root)
        target = resolve_target(role, self.harness_map, self.adapters, phase=phase.name)
        model = (
            None
            if phase.result is None
            else results.resolve_result_model(
                phase.result, self.result_models, phase=phase.name
            )
        )
        cwd = self._worktree(context, phase.name)

        started_at = self.clock()
        self._record_phase(phase, "started", started_at, None, None)
        budget = 1 if phase.retry is None else phase.retry.max_attempts
        retry_on = () if phase.retry is None else tuple(phase.retry.on)
        text = rendered
        verdict = Verdict("harness_error", detail="no attempt was made")

        for _ in range(budget):
            verdict = self._attempt(phase, context, text, target, role, cwd, model)
            if verdict.status == "ok":
                self._record_phase(phase, "done", started_at, self.clock(), None)
                return verdict.result
            if verdict.fatal or verdict.status not in retry_on:
                break
            text = with_feedback(text, verdict.detail or verdict.status)

        detail = verdict.detail or verdict.status
        self._record_phase(phase, "failed", started_at, self.clock(), detail)
        raise AgentPhaseFailed(phase.name, outcome=verdict.status, detail=detail)

    def _attempt(
        self,
        phase: AgentPhase,
        context: Mapping[str, Any],
        rendered: prompt.RenderedPrompt,
        target: Target,
        role: RoleBundle,
        cwd: Path,
        model: type[BaseModel] | None,
    ) -> Verdict:
        """One dispatch: directory, prompt, argv, launcher, result, gates."""
        n = next_attempt(self.run_id, self.card_id, phase.name)
        attempt_dir = paths.attempt_dir(self.run_id, self.card_id, phase.name, n)
        prompt_path = rendered.write(attempt_dir)
        stdout_path = attempt_dir / STDOUT_NAME
        dispatch_record = build_dispatch(
            target=target,
            role=role,
            cwd=cwd,
            prompt_path=prompt_path,
            attempt_dir=attempt_dir,
            timeout=self.timeout,
        )
        # Recorded `started` before the launcher runs, because that is the row
        # resume reads when the manager dies mid-attempt (§9).
        self._record_attempt(
            phase,
            models.Attempt(
                n=n,
                dispatch=dispatch_record,
                status="started",
                prompt_path=prompt_path,
                result_path=dispatch_record.result_path,
                stdout_path=stdout_path,
            ),
        )
        argv = target.adapter.build_command(dispatch_record)
        outcome = self.launcher(
            argv, cwd=cwd, timeout=self.timeout, stdout_path=stdout_path
        )
        verdict = classify(outcome, dispatch_record.result_path, model)
        if verdict.status == "ok":
            failure = evaluate_gates(
                phase,
                self.workflow,
                gate_values(context, phase.name, verdict.result),
                self.warnings,
            )
            if failure is not None:
                verdict = failure
        usage = _usage(target.adapter, outcome)
        self._record_attempt(
            phase,
            models.Attempt(
                n=n,
                dispatch=dispatch_record,
                status=verdict.status,
                exit_code=outcome.exit_code,
                duration=outcome.duration,
                tokens_in=None if usage is None else usage.tokens_in,
                tokens_out=None if usage is None else usage.tokens_out,
                cost=None if usage is None else usage.cost,
                prompt_path=prompt_path,
                result_path=dispatch_record.result_path,
                stdout_path=stdout_path,
            ),
        )
        return verdict

    def _worktree(self, context: Mapping[str, Any], phase_name: str) -> Path:
        """The cwd D7 pins the harness to: the subtask's own worktree."""
        worktree = context.get("worktree")
        if worktree is None:
            raise EngineError(
                "cannot dispatch: the context has no worktree to run the harness in "
                "(the worktree phase runs before any agent phase that writes)",
                phase=phase_name,
            )
        return Path(worktree)

    def _record_phase(
        self,
        phase: AgentPhase,
        status: models.Status,
        started_at: datetime,
        ended_at: datetime | None,
        detail: str | None,
    ) -> None:
        self.store.record_phase(
            self.story_id,
            self.card_id,
            models.PhaseRun(
                name=phase.name,
                kind="agent",
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                detail=detail,
            ),
        )

    def _record_attempt(self, phase: AgentPhase, attempt: models.Attempt) -> None:
        self.store.record_attempt(self.story_id, self.card_id, phase.name, attempt)


def _usage(adapter: HarnessAdapter, outcome: Outcome) -> Usage | None:
    """What the attempt cost, as far as `stdout.log` says. Never raises.

    The only thing the log is ever read for (D4). `errors="replace"` and the
    swallowed `OSError` are deliberate: a truncated or unreadable log must not
    turn an attempt that produced a perfectly good result file into a failure.
    """
    try:
        text = outcome.stdout_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return adapter.parse_usage(text)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/errors.py src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "feat: run one agent phase with journalled attempts, retry and escalation"
```

---

### Task 6: Escalate the walk when an agent phase fails

**Files:**
- Modify: `src/agent_manager/engine.py:356-400` (the `run_subtask` loop)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `errors.AgentPhaseFailed` (Task 5); `engine._record_subtask_status(store, story_id, subtask, status)` and `engine._render_error(error)` (`src/agent_manager/engine.py:477-508`).
- Produces: `engine._escalate(summary, store, story_id, subtask, phase_name, detail) -> SubtaskSummary`; `run_subtask` returning `SubtaskSummary(status="escalated", failed_phase=<agent phase>, detail=...)` instead of propagating a runner failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
from agent_manager.errors import AgentPhaseFailed


def test_a_failed_agent_phase_escalates_the_subtask_and_stops(store):
    # Spec test 14 (§12 line 429: escalation stops the run -- no later phase is
    # started, and the subtask is recorded escalated).
    calls: list[str] = []

    def work(card: str) -> dict[str, Any]:
        calls.append("work")
        return {}

    def agent_runner(phase, context, rendered):
        calls.append(f"agent:{phase.name}")
        raise AgentPhaseFailed(
            phase.name,
            outcome="gate_failed",
            detail="phase 'explore' gate 'exploration_output_gate' failed: blocked=exploration",
        )

    workflow = _workflow(MIXED, {"step.work": work})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
    )

    assert calls == ["agent:explore"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "explore"
    assert "blocked=exploration" in summary.detail
    assert summary.results == {}
    subtasks = [
        line.payload["status"]
        for line in store.journal.read()
        if line.event == "subtask_upsert"
    ]
    assert subtasks == ["escalated"]


def test_an_unexpected_error_from_the_agent_runner_escalates_rather_than_crashing(store):
    # Symmetric with `_run_deterministic`'s deliberately total except: an
    # exception escaping the walk would leave the subtask recorded `started`
    # forever, which is exactly what resume mistakes for work in flight.
    def work(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the failed one may start")

    def agent_runner(phase, context, rendered):
        raise OSError("the run directory went away")

    workflow = _workflow(MIXED, {"step.work": work})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "explore"
    assert "OSError: the run directory went away" in summary.detail


def test_a_successful_agent_phase_still_advances_the_walk(store):
    """The existing happy path must not change shape under the new try/except."""
    def work(card: str, explore: dict[str, Any]) -> dict[str, Any]:
        return {"saw": explore["summary"]}

    workflow = _workflow(MIXED, {"step.work": work})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=lambda phase, context, rendered: {"summary": "explored"},
    )

    assert summary.status == "done"
    assert summary.results["work"] == {"saw": "explored"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -k "escalates or still_advances" -v`
Expected: FAIL — the first two error out with `AgentPhaseFailed` / `OSError` propagating out of `run_subtask` instead of returning a summary.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/engine.py`, add to the imports:

```python
from agent_manager.errors import AgentPhaseFailed, EngineError
```

Replace the agent branch of `run_subtask` (`engine.py:358-369`):

```python
        if not isinstance(phase, DeterministicPhase):
            if agent_runner is None:
                raise EngineError(
                    "is an agent phase, but no agent runner was injected",
                    phase=phase.name,
                )
            rendered = prompt.render_prompt(phase, context)
            try:
                result = agent_runner(phase, dict(context), rendered)
            except AgentPhaseFailed as failure:
                # §6 step 8 / §12 line 429: exhausted retries or a non-retryable
                # gate failure ends the subtask here. The runner has already
                # journalled every attempt and the phase's terminal status.
                return _escalate(
                    summary, store, story_id, subtask, phase.name, failure.detail
                )
            except Exception as error:
                # Total, for the reason `_run_deterministic` is: the runner is
                # the one place a harness, a gate and the filesystem all meet,
                # and an exception escaping the walk would leave the subtask
                # recorded `started` forever -- which resume reads as work in
                # flight. `render_prompt` stays outside the try: a document that
                # declares an unresolvable input is a load-time bug, and its
                # `EngineError` must still reach the caller.
                return _escalate(
                    summary, store, story_id, subtask, phase.name, _render_error(error)
                )
            _bind_result(context, phase.name, result)
            summary.results[phase.name] = result
            index += 1
            continue
```

Replace the deterministic branch's escalation (`engine.py:385-389`) with the same helper:

```python
            summary.status = "escalated"
            summary.failed_phase = phase.name
            summary.detail = outcome.detail
            _record_subtask_status(store, story_id, subtask, "escalated")
            return summary
```

becomes:

```python
            return _escalate(
                summary, store, story_id, subtask, phase.name, outcome.detail
            )
```

And add the helper next to `_record_subtask_status`:

```python
def _escalate(
    summary: SubtaskSummary,
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase_name: str,
    detail: str | None,
) -> SubtaskSummary:
    """Record the subtask `escalated` and hand the walk's summary back.

    One helper for both phase kinds, because §12's "escalation stops the run" is
    one rule: the summary is returned rather than raised so the caller can still
    read the results and warnings of everything that ran before it.
    """
    summary.status = "escalated"
    summary.failed_phase = phase_name
    summary.detail = detail
    _record_subtask_status(store, story_id, subtask, "escalated")
    return summary
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS — the three new tests plus every pre-existing test in the file.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, no failures, no errors.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat: escalate the subtask when an agent phase ends failed"
```

---

## Notes for the implementer

- The plan builds `dispatch.py` incrementally across Tasks 1-5. Keep one import block at the top of the module and grow it as each task's imports are introduced; the final set is exactly: `json`, `collections.abc.{Callable, Mapping}`, `dataclasses.{dataclass, field, replace}`, `datetime.{datetime, timezone}`, `pathlib.Path`, `typing.Any`, `pydantic.{BaseModel, ValidationError}`, and from this package `engine`, `models`, `paths`, `prompt`, `results`, `errors.{AgentPhaseFailed, EngineError}`, `harness.base.{HarnessAdapter, Outcome, Usage}`, `harness.launcher.LauncherFn`, `harness.registry.{DEFAULT_HARNESS, default_adapters}`, `roles.loader.{RoleBundle, load_role}`, `store.Store`, `workflow.loader.{AgentPhase, Workflow}`.
- `dispatch.py` imports `engine`; `engine.py` must never import `dispatch`. The failure signal travels through `errors.AgentPhaseFailed`, which both import, precisely so that stays true (the same reason `EngineError` already lives in `errors.py`).
- `subprocess` is not in that list and must not appear in `dispatch.py`.
- `tests/test_dispatch.py` grows across Tasks 1-5 too. Each task shows the imports its own tests need; consolidate them into one block at the top of the file as you go rather than repeating a line (`from agent_manager import models` appears in both Task 2 and Task 5). The final set is: `json`, `subprocess`, `dataclasses.{dataclass, field}`, `pathlib.Path`, `pytest`, `pydantic.{BaseModel, ConfigDict}`, and from this package `dispatch`, `engine`, `models`, `paths`, `prompt`, `store as store_module`, `errors.{AgentPhaseFailed, EngineError}`, `harness.base.{Outcome, Usage}`, `roles.loader.load_role`, `workflow.loader.load_workflow`, `workflow.registry.FunctionRegistry`.
- `tests/test_dispatch.py`'s fixtures and helpers (`data_home`, `_rendered`, `make_role`, `FakeAdapter`, `FakeLauncher`, `FakeResult`, `_workflow`, `store`, `worktree`, `_runner`, `_context`) are each introduced once, by the task whose tests first need them, and reused after.
