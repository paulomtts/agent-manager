"""Run one agent phase to a terminal outcome (design §6 lines 261-278).

The pygents walk's `agent_phase` tool (`runtime/compile.py`) resolves an agent
phase's `inputs` and renders its prompt, then hands `(phase, context, rendered)`
to an injected `AgentPhaseRunner`. This module is that runner: the attempt
directory, the dispatch, the result file, the gates and the retry loop.

Three rules shape everything here, and none of them is negotiable:

- D7 / §8 line 317: nothing in this module starts a process. The argv comes
  from the adapter's `build_command` and is run through the injected
  `LauncherFn`, which is what keeps `bwrap` a later swap and every test above
  the launcher process-free. `subprocess` is deliberately not imported.
- D4 / §6 step 5: the contract is `result.json`. `stdout.log` is captured as a
  log and never read by the engine.
- §6 step 3: the attempt directory comes from `paths.attempt_dir`, which is
  rooted under `paths.data_dir()` and therefore outside every worktree -- a
  result file written inside the worktree would fail the verify step's
  clean-tree check or be swept into a commit.
"""

import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_manager import models, paths, prompt, results
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime import walk
from agent_manager.harness.base import HarnessAdapter, Outcome
from agent_manager.harness.launcher import LauncherFn
from agent_manager.harness.registry import DEFAULT_HARNESS, default_adapters
from agent_manager.roles.loader import RoleBundle, load_role
from agent_manager.runtime import bridge
from agent_manager.store.journal import JournalError
from agent_manager.store.writer import Store
from agent_manager.workflow import phases as phase_model

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
    return paths.highest_attempt(run_id, card, phase) + 1


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

    `timed_out` is set only by `classify` reading `outcome.timed_out`, never by
    a gate or `read_result` -- it marks a `harness_error` whose attempt ran the
    launcher's full timeout rather than failing fast, which
    `AgentRunner.__call__` reads to refuse the one-shot redispatch (G2's margin
    assumes at most one launcher-timeout-length attempt per phase; redispatching
    a second one can run the turn past its own timeout).
    """

    status: models.AttemptStatus
    result: Any = None
    detail: str | None = None
    fatal: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class Adopted:
    """An earlier attempt's result, reused instead of dispatching again.

    Internal-only state, so a dataclass (CLAUDE.md). `result` is the
    re-validated JSON-mode dump (or `None` for a result-less phase), `attempt`
    the recorded attempt number it came from, `source_run` the run whose
    journal recorded it.
    """

    result: Any
    attempt: int
    source_run: str


def _recorded_phase(
    run: models.Run, card_id: str, phase_name: str
) -> models.PhaseRun | None:
    """This card's phase `phase_name` in `run`, under whichever story holds it.

    The source run's story structure is not assumed to match the current
    run's, so every story is searched.
    """
    for story in run.stories:
        for subtask in story.subtasks:
            if subtask.card_id != card_id:
                continue
            for recorded in subtask.phases:
                if recorded.name == phase_name:
                    return recorded
    return None


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
            timed_out=True,
        )
    if outcome.exit_code != 0:
        return Verdict("harness_error", detail=f"the harness exited {outcome.exit_code}")
    return read_result(result_path, model)


def read_result(result_path: Path | None, model: type[BaseModel] | None) -> Verdict:
    """The file half of `classify`: judge a result file with no launcher report.

    Split out so `AgentRunner.adopt` can re-judge an attempt recorded by an
    earlier process, which left a file on disk but no `Outcome` in memory. The
    order and every message are `classify`'s, unchanged: no model is `ok` with
    no result and the path is never touched, a missing file is a
    `harness_error`, and every way an existing file fails to validate is
    `schema_invalid`. An `ok` result is the JSON-mode dump, for the reason
    `classify`'s docstring gives.
    """
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


def _broken_gate_message(phase_name: str, detail: Mapping[str, Any]) -> str:
    """The detail a broken gate on an agent phase records (architecture-cleanup S3).

    `walk.evaluate_gates` reports a gate that raised, or returned something
    other than `None` or a mapping, as `broken` and leaves the wording to each
    caller. This is the text dispatch has always journalled for one, rebuilt
    from the verdict's `gate`, `reason`, `error` and `returned_type`, so the
    attempt and phase rows an operator reads do not change with the evaluator.
    """
    gate = detail["gate"]
    if detail["reason"] == "raised":
        error = detail["error"]
        return (
            f"phase {phase_name!r} gate {gate!r} raised "
            f"{type(error).__name__}: {error}; a gate returns None to pass or "
            "a mapping verdict to fail, so this is a broken gate rather than a "
            "failed attempt"
        )
    return (
        f"phase {phase_name!r} gate {gate!r} returned "
        f"{detail['returned_type']}; a gate returns None to pass or a "
        "mapping verdict to fail, and anything else would be read as a "
        "pass by accident"
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _spawn_kwargs(launcher: LauncherFn) -> dict[str, Any]:
    """`on_spawn` for a launcher that declares it, bound to the bridge call in flight.

    Inside `bridge.call_agent` the hook records every process this attempt
    starts, so a cancelled turn can kill it (pygents-engine design G2);
    anywhere else it is `None`, which `run_direct` treats as absent. A launcher
    that does not declare the keyword -- every fake launcher in the tests --
    is called exactly as before.
    """
    try:
        parameters = inspect.signature(launcher).parameters
    except (TypeError, ValueError):
        return {}
    if "on_spawn" not in parameters:
        return {}
    return {"on_spawn": bridge.current_spawn_hook()}


Clock = Callable[[], datetime]


@dataclass
class AgentRunner:
    """One agent phase, run to a terminal outcome: `walk.AgentPhaseRunner`.

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
        phase: phase_model.AgentPhase,
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
        model = self._result_model(phase)
        cwd = self._worktree(context, phase.name)

        started_at = self.clock()
        self._record_phase(phase, "started", started_at, None, None)
        budget = 1 if phase.retry is None else phase.retry.max_attempts
        retry_on = () if phase.retry is None else tuple(phase.retry.on)
        feedback: list[str] = []
        # The first harness_error of this call gets one more dispatch, outside
        # the retry budget (1fadbbdd): a harness that died without a result --
        # a turn ended early, a crash -- may well succeed on a fresh process,
        # and `retry.on` is the schema/gate contract, not this one. Never for a
        # verdict that timed out, though: that attempt already spent the whole
        # launcher timeout, and G2's turn-timeout floor only has room for one
        # of those per phase -- a second would risk the turn timing out while
        # the redispatch is still running, leaving an abandoned `to_thread`
        # worker to write its attempt after the walk has moved on or the store
        # closed.
        redispatched = False
        counted = 0

        try:
            while True:
                n, verdict = self._attempt(
                    phase, context, rendered, tuple(feedback), target, role, cwd, model
                )
                if verdict.status == "ok":
                    self._record_phase(phase, "done", started_at, self.clock(), None)
                    return verdict.result
                if verdict.status == "harness_error" and not redispatched and not verdict.timed_out:
                    # No feedback: the harness produced nothing for a complaint
                    # to correct, so the brief is re-sent as it was.
                    redispatched = True
                    self.warnings.append(
                        f"phase {phase.name!r}: attempt {n} ended harness_error "
                        f"({verdict.detail}); dispatching once more"
                    )
                    continue
                counted += 1
                if verdict.fatal or verdict.status not in retry_on or counted >= budget:
                    break
                # Carried as data, not folded into `rendered`: `_attempt` composes
                # the whole brief from the base prompt every time, so re-feeding a
                # composed brief would duplicate the result contract.
                feedback.append(verdict.detail or verdict.status)
        except Exception as error:
            # Symmetric with `walk.run_one_step`, which records its own
            # phase `failed` when a step raises: §9's state tree has no edge for
            # "the process gave up here", so a phase left `started` is what a
            # resume reads as work still in flight. The exception itself still
            # propagates -- `run_subtask` is the one that decides to escalate.
            self._record_phase(
                phase, "failed", started_at, self.clock(), walk._render_error(error)
            )
            raise

        detail = verdict.detail or verdict.status
        self._record_phase(phase, "failed", started_at, self.clock(), detail)
        raise AgentPhaseFailed(
            phase.name, outcome=verdict.status, detail=detail, result=verdict.result
        )

    def _attempt(
        self,
        phase: phase_model.AgentPhase,
        context: Mapping[str, Any],
        rendered: prompt.RenderedPrompt,
        feedback: Sequence[str],
        target: Target,
        role: RoleBundle,
        cwd: Path,
        model: type[BaseModel] | None,
    ) -> tuple[int, Verdict]:
        """One dispatch: directory, brief, argv, launcher, result, gates.

        The brief is composed here rather than by the caller because addendum R2
        puts this attempt's own `result.json` in it, and that path only exists
        once `next_attempt` and `paths.attempt_dir` have fixed the directory.
        The attempt number is returned with the verdict so the caller can name
        it: on a resumed run it need not be the loop's iteration count.
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
            argv,
            cwd=cwd,
            timeout=self.timeout,
            stdout_path=stdout_path,
            **_spawn_kwargs(self.launcher),
        )
        # The same `None if model is None` the brief uses: the two halves of
        # "this phase has no result" must agree. The dispatch and the journalled
        # attempt keep the concrete path (the adapter builds argv from it and
        # `cli.read_artifact` reads it).
        verdict = classify(
            outcome, None if model is None else dispatch_record.result_path, model
        )
        if verdict.status == "ok":
            # The one evaluator both phase kinds share (S3). `pass` and `warn`
            # keep the `ok` verdict -- `evaluate_gates` has already appended any
            # warning to `self.warnings`, so nothing is appended here. `fail` is
            # retryable if `retry.on` says so; `broken` never is.
            gates = walk.evaluate_gates(
                phase,
                walk.gate_values(context, phase.name, verdict.result),
                self.warnings,
            )
            # Both gate-failure verdicts keep the dispatch's own result: a gate
            # failing does not mean nothing was produced, and the agent's own
            # explanation (e.g. review's `unresolved_blockers`) is read off this
            # result by `comments.agent_reason` once the phase escalates.
            if gates.kind == "fail":
                verdict = Verdict(
                    "gate_failed",
                    detail=gates.detail["message"],
                    result=verdict.result,
                )
            elif gates.kind == "broken":
                verdict = Verdict(
                    "gate_failed",
                    detail=_broken_gate_message(phase.name, gates.detail),
                    fatal=True,
                    result=verdict.result,
                )
        self._record_attempt(
            phase,
            models.Attempt(
                n=n,
                dispatch=dispatch_record,
                status=verdict.status,
                exit_code=outcome.exit_code,
                duration=outcome.duration,
                prompt_path=prompt_path,
                result_path=dispatch_record.result_path,
                stdout_path=stdout_path,
            ),
        )
        return n, verdict

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
        phase: phase_model.AgentPhase,
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

    def _record_attempt(self, phase: phase_model.AgentPhase, attempt: models.Attempt) -> None:
        self.store.record_attempt(self.story_id, self.card_id, phase.name, attempt)

    def _result_model(self, phase: phase_model.AgentPhase) -> type[BaseModel] | None:
        """The model this phase's result is validated against, or `None`.

        A declared phase carries its result model as the class itself, which
        is used as-is; a result given by name is looked up in
        `result_models`, and a name the table lacks is refused here.
        """
        if phase.result is None:
            return None
        if isinstance(phase.result, type):
            return phase.result
        return results.resolve_result_model(
            phase.result, self.result_models, phase=phase.name
        )

    def adopt(
        self,
        phase: phase_model.AgentPhase,
        context: Mapping[str, Any],
        *,
        source_run: str,
        floor: int,
    ) -> Adopted | None:
        """Reuse `source_run`'s recorded `ok` attempt instead of dispatching.

        Attempts are read from the source run's journal, never from the
        `attempts` projection, and only attempts numbered above `floor` with
        status `ok` qualify: an orphaned `started` one, even with a valid
        file on disk, never does. The highest such attempt's result file is
        re-read and its gates re-run against the resumed context. Nothing to
        adopt returns `None` silently; a recorded result that no longer holds
        up declines with a warning and writes no row. Only an adoption records
        anything: this phase `done`, in the current run.
        """
        model = self._result_model(phase)
        try:
            run = self.store.replay_journal(source_run)
        except (JournalError, ValidationError) as error:
            return self._decline(
                phase, None, source_run,
                f"its journal cannot be read: {walk._render_error(error)}",
            )
        found = _recorded_phase(run, self.card_id, phase.name)
        if found is None:
            return None
        candidates = [
            attempt
            for attempt in found.attempts
            if attempt.n > floor and attempt.status == "ok"
        ]
        if not candidates:
            return None
        attempt = max(candidates, key=lambda candidate: candidate.n)
        verdict = read_result(None if model is None else attempt.result_path, model)
        if verdict.status != "ok":
            return self._decline(
                phase, attempt.n, source_run, verdict.detail or verdict.status
            )
        gates = walk.evaluate_gates(
            phase, walk.gate_values(context, phase.name, verdict.result), self.warnings
        )
        if gates.kind == "fail":
            return self._decline(phase, attempt.n, source_run, gates.detail["message"])
        if gates.kind == "broken":
            return self._decline(
                phase, attempt.n, source_run, _broken_gate_message(phase.name, gates.detail)
            )
        self._record_phase(
            phase, "done", found.started_at or self.clock(), self.clock(), None
        )
        self.warnings.append(
            f"phase {phase.name!r} was not dispatched again: attempt {attempt.n} of "
            f"run {source_run} had already succeeded (result reused)"
        )
        return Adopted(verdict.result, attempt.n, source_run)

    def _decline(
        self,
        phase: phase_model.AgentPhase,
        n: int | None,
        source_run: str,
        why: str,
    ) -> None:
        """Warn that a recorded attempt is not reused; write nothing.

        `n` is `None` when the journal could not be read, before any attempt
        was found; the number is then shown as `?`.
        """
        shown = "?" if n is None else n
        self.warnings.append(
            f"phase {phase.name!r}: attempt {shown} of run {source_run} was not "
            f"reused ({why}); dispatching again"
        )
        return None
