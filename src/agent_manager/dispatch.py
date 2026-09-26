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

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_manager import engine, models, paths, prompt, results
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.harness.base import HarnessAdapter, Outcome, Usage
from agent_manager.harness.launcher import LauncherFn
from agent_manager.harness.registry import DEFAULT_HARNESS, default_adapters
from agent_manager.roles.loader import RoleBundle, load_role
from agent_manager.store import Store
from agent_manager.workflow import phases as phase_model
from agent_manager.workflow.loader import AgentPhase, Workflow

AnyAgentPhase = AgentPhase | phase_model.AgentPhase
"""Either agent-phase type: the YAML one (`result` and gates as names) or the
declared phase model (`result` a class, gates callables). Dispatch accepts both
while the YAML engine exists; nothing here imports pygents (rule 1)."""

RESULT_NAME = "result.json"
"""The result file §6 step 3 puts in every attempt directory."""

STDOUT_NAME = "stdout.log"
"""The captured harness log of one attempt (§6 step 4). A log, not a channel."""

FEEDBACK_HEADING = prompt.FEEDBACK_HEADING
"""Re-exported from `prompt` so callers and tests keep this name.

The string itself moved to `prompt.py` with the brief composer that also emits
it; `dispatch` imports `prompt`, so binding the name here costs nothing and
makes it impossible for the two to disagree.
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


def _append_feedback(brief: str, feedback: Sequence[str]) -> str:
    """One `FEEDBACK_HEADING` section per accumulated complaint, after the brief.

    `prompt.compose_brief` takes a single feedback string and this loop needs one
    heading per attempt's complaint, so the sections are appended here instead.
    The joining matches `prompt._join_sections`: one blank line between
    neighbours, one newline at the end, interior text untouched.
    """
    parts = [brief.strip("\n")]
    for block in feedback:
        parts.append(FEEDBACK_HEADING + "\n" + block.strip("\n"))
    return "\n\n".join(parts) + "\n"


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
    outcome: Outcome, result_path: Path | None, model: type[BaseModel] | None
) -> Verdict:
    """One attempt's outcome, from the launcher's report and the result file.

    The order is §6 line 278's, and it is load-bearing: a timeout or a non-zero
    exit is a `harness_error` whatever is on disk, and a missing file after a
    clean exit is a `harness_error` too -- nothing is parsed in either case.
    Only past those does the file get read, and from there every failure is
    `schema_invalid`, because a file that exists and cannot be validated is
    exactly what re-dispatching with the validator's text can fix.

    A phase that declares no result model is judged on exit status alone: there
    is no contract in its brief, so there is nothing to read or validate, and a
    file the harness wrote anyway is not adopted as a result. `result_path` is
    never dereferenced on that path, so `None` is accepted and it never raises.

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
    if model is None:
        return Verdict("ok", result=None)
    if result_path is None or not result_path.is_file():
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
    try:
        validated = model.model_validate(data)
    except ValidationError as error:
        return Verdict("schema_invalid", detail=str(error))
    return Verdict("ok", result=validated.model_dump(mode="json"))


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
    phase: AnyAgentPhase,
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

    A gate entry is either a name, resolved through `workflow.function` as the
    YAML document declares it, or -- on a declared `phases.AgentPhase` -- the
    callable itself, used as-is and named by its `__name__` (its `repr` when it
    has none) in every message.
    """
    for entry in phase.gates:
        if isinstance(entry, str):
            name, gate = entry, workflow.function(entry)
        else:
            name, gate = getattr(entry, "__name__", repr(entry)), entry
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


def _render_error(error: BaseException) -> str:
    """`engine._render_error`'s format, so both phase kinds fail the same way."""
    return f"{type(error).__name__}: {error}"


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
        phase: AnyAgentPhase,
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
        # A declared phase-model phase carries its result model as the class
        # itself, which is used as-is; a YAML phase carries a name, looked up
        # in the table exactly as before.
        if phase.result is None:
            model = None
        elif isinstance(phase.result, type):
            model = phase.result
        else:
            model = results.resolve_result_model(
                phase.result, self.result_models, phase=phase.name
            )
        cwd = self._worktree(context, phase.name)

        started_at = self.clock()
        self._record_phase(phase, "started", started_at, None, None)
        budget = 1 if phase.retry is None else phase.retry.max_attempts
        retry_on = () if phase.retry is None else tuple(phase.retry.on)
        feedback: list[str] = []
        verdict = Verdict("harness_error", detail="no attempt was made")

        try:
            for _ in range(budget):
                verdict = self._attempt(
                    phase, context, rendered, tuple(feedback), target, role, cwd, model
                )
                if verdict.status == "ok":
                    self._record_phase(phase, "done", started_at, self.clock(), None)
                    return verdict.result
                if verdict.fatal or verdict.status not in retry_on:
                    break
                # Carried as data, not folded into `rendered`: `_attempt` composes
                # the whole brief from the base prompt every time, so re-feeding a
                # composed brief would duplicate the result contract.
                feedback.append(verdict.detail or verdict.status)
        except Exception as error:
            # Symmetric with `engine._run_deterministic`, which records its own
            # phase `failed` when a step raises: §9's state tree has no edge for
            # "the process gave up here", so a phase left `started` is what a
            # resume reads as work still in flight. The exception itself still
            # propagates -- `run_subtask` is the one that decides to escalate.
            self._record_phase(
                phase, "failed", started_at, self.clock(), _render_error(error)
            )
            raise

        detail = verdict.detail or verdict.status
        self._record_phase(phase, "failed", started_at, self.clock(), detail)
        raise AgentPhaseFailed(phase.name, outcome=verdict.status, detail=detail)

    def _attempt(
        self,
        phase: AnyAgentPhase,
        context: Mapping[str, Any],
        rendered: prompt.RenderedPrompt,
        feedback: Sequence[str],
        target: Target,
        role: RoleBundle,
        cwd: Path,
        model: type[BaseModel] | None,
    ) -> Verdict:
        """One dispatch: directory, brief, argv, launcher, result, gates.

        The brief is composed here rather than by the caller because addendum R2
        puts this attempt's own `result.json` in it, and that path only exists
        once `next_attempt` and `paths.attempt_dir` have fixed the directory.
        """
        n = next_attempt(self.run_id, self.card_id, phase.name)
        attempt_dir = paths.attempt_dir(self.run_id, self.card_id, phase.name, n)
        brief = prompt.compose_brief(
            role,
            rendered,
            result_path=None if model is None else attempt_dir / RESULT_NAME,
            result_model=model,
        )
        # `replace` rather than a second writer: `RenderedPrompt.write` stays the
        # one place a prompt write can fail, with the `EngineError` it already
        # raises, and it keeps the phase name this rendering came from.
        prompt_path = replace(rendered, text=_append_feedback(brief, feedback)).write(
            attempt_dir
        )
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
        # The same `None if model is None` the brief uses: the two halves of
        # "this phase has no result" must agree. The dispatch and the journalled
        # attempt keep the concrete path (the adapter builds argv from it and
        # `cli.read_artifact` reads it).
        verdict = classify(
            outcome, None if model is None else dispatch_record.result_path, model
        )
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
        phase: AnyAgentPhase,
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

    def _record_attempt(self, phase: AnyAgentPhase, attempt: models.Attempt) -> None:
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
