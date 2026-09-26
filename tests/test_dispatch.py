"""Behaviour of one agent phase's dispatch, validation, retry and escalation
(design §6 lines 261-278, §9 lines 365-368, §12 line 429).

Engine tier per design §14 lines 486-488: a fake adapter and a fake launcher
that write canned result files (valid, invalid, gate-failing, absent) stand in
for a harness, and the store is a real temp SQLite projection plus a real temp
JSONL journal. No process is ever started -- the launcher is injected, and one
test asserts `subprocess.Popen` is never reached.
"""

import functools
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from agent_manager import (
    cli,
    dispatch,
    engine,
    models,
    paths,
    prompt,
    results,
    store as store_module,
)
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.harness.base import Outcome, Usage
from agent_manager.roles.loader import load_role
from agent_manager.runtime import bridge
from agent_manager.workflow import phases
from agent_manager.workflow.loader import load_workflow
from agent_manager.workflow.registry import FunctionRegistry

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


POLICY = """\
allowed_tools = ["Read"]
max_attempts = 2
required_capabilities = []

[default_model]
claude = "sonnet"
fake = "fake-model"
"""


METHODOLOGY = """\
# Test-driven development

## the loop

Red, green, refactor. Never write implementation code before a failing test.
"""


def make_role(
    root: Path,
    name: str = "explorer",
    *,
    policy: str = POLICY,
    methodology: dict[str, str] | None = None,
) -> Path:
    """A synthetic role bundle, built the way tests/roles/test_loader.py does.

    `methodology` defaults to one vendored document so a composed brief has a
    heading and a body to find; `{}` builds a bundle that vendors nothing.
    """
    directory = root / name
    (directory / "methodology").mkdir(parents=True, exist_ok=True)
    (directory / "system.md").write_text(
        f"Standing instructions for {name}.\n", encoding="utf-8"
    )
    (directory / "policy.toml").write_text(policy, encoding="utf-8")
    documents = (
        {"test-driven-development.md": METHODOLOGY}
        if methodology is None
        else methodology
    )
    entries = []
    for filename, text in documents.items():
        path = directory / "methodology" / filename
        path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(
            "[[vendored]]\n"
            f'file = "{filename}"\n'
            f'upstream = "skills/{filename}"\n'
            f'sha256 = "{digest}"\n'
        )
    (directory / "VENDORED.lock").write_text(
        "".join(entries) or "vendored = []\n", encoding="utf-8"
    )
    return directory


class FakeAdapter:
    """A `HarnessAdapter` by shape, whose argv names a program nothing runs."""

    capabilities = frozenset({"bash", "edit"})

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.dispatches: list[models.Dispatch] = []

    def build_command(self, d: models.Dispatch) -> list[str]:
        self.dispatches.append(d)
        return [
            "fake-harness",
            "--model",
            d.model,
            "--prompt",
            str(d.prompt_path),
            "--result",
            str(d.result_path),
        ]

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
    prompts: list[str] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        self.calls.append(list(argv))
        if "--prompt" in argv:
            self.prompts.append(
                Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
            )
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


def test_a_result_less_phase_is_ok_with_no_result_at_all(tmp_path):
    verdict = dispatch.classify(_outcome(tmp_path), None, None)

    assert verdict == dispatch.Verdict("ok", result=None)
    assert verdict.detail is None
    assert verdict.fatal is False


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "fragment"),
    [(None, True, "timed out"), (2, False, "exited 2")],
)
def test_a_result_less_phase_judges_a_bad_exit_on_status_alone(
    tmp_path, exit_code, timed_out, fragment
):
    outcome = _outcome(tmp_path, exit_code=exit_code, timed_out=timed_out)

    verdict = dispatch.classify(outcome, None, None)

    assert verdict.status == "harness_error"
    assert fragment in verdict.detail
    assert verdict.result is None


def test_a_result_less_phase_never_reads_a_result_file_that_is_there(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"wrote": "docs/spec.md"}), encoding="utf-8")

    verdict = dispatch.classify(_outcome(tmp_path), result, None)

    assert verdict.status == "ok"
    assert verdict.result is None


def test_a_result_less_phase_with_no_file_on_disk_is_still_ok(tmp_path):
    verdict = dispatch.classify(_outcome(tmp_path), tmp_path / "absent.json", None)

    assert verdict.status == "ok"
    assert verdict.result is None


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


# ── phase-model phases (workflow.phases.AgentPhase) ──────────────────────────


class _NoLookupWorkflow:
    """A workflow whose name table must never be consulted.

    A phase-model phase carries its gates as callables, so nothing about it
    should reach `workflow.function`; any call is recorded and fails loudly.
    """

    def __init__(self) -> None:
        self.looked_up: list[object] = []

    def function(self, name):
        self.looked_up.append(name)
        raise AssertionError(f"workflow.function({name!r}) was called for a callable gate")


def _model_phase(*gates, **overrides) -> phases.AgentPhase:
    """A declared `phases.AgentPhase` shaped like AGENT_DOCUMENT's explore phase."""
    fields = {
        "name": "explore",
        "role": "explorer",
        "inputs": (),
        "result": FakeResult,
        "gates": tuple(gates),
    }
    fields.update(overrides)
    return phases.AgentPhase(**fields)


def test_callable_gate_is_called_directly():
    workflow = _NoLookupWorkflow()

    verdict = dispatch.evaluate_gates(
        _model_phase(lambda result: {"blocked": "x", "detail": "d"}),
        workflow,
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is False
    assert verdict.detail == "phase 'explore' gate '<lambda>' failed: blocked=x, detail=d"
    assert workflow.looked_up == []


def test_a_passing_callable_gate_sees_the_result():
    seen: list[object] = []

    def output_gate(result):
        seen.append(result)
        return None

    verdict = dispatch.evaluate_gates(
        _model_phase(output_gate),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict is None
    assert seen == [{"summary": "ok"}]


def test_a_callable_gate_that_raises_is_fatal_and_named_by_its_function_name():
    def output_gate(result):
        raise RuntimeError("the gate itself is broken")

    verdict = dispatch.evaluate_gates(
        _model_phase(output_gate),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is True
    assert "gate 'output_gate' raised RuntimeError: the gate itself is broken" in verdict.detail


def test_a_callable_gate_returning_a_non_mapping_is_fatal_and_named_lambda():
    verdict = dispatch.evaluate_gates(
        _model_phase(lambda result: "looks fine to me"),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is True
    assert "gate '<lambda>' returned str" in verdict.detail


def test_a_callable_gate_with_an_unsupplied_parameter_is_a_named_engine_error():
    def output_gate(result, provided_verification):
        return None

    with pytest.raises(EngineError) as caught:
        dispatch.evaluate_gates(
            _model_phase(output_gate),
            _NoLookupWorkflow(),
            dispatch.gate_values({}, "explore", {"summary": "ok"}),
            [],
        )

    assert caught.value.parameter == "provided_verification"
    assert caught.value.function == "output_gate"
    assert caught.value.phase == "explore"


def test_a_callable_gate_warning_names_the_gate_by_its_function_name():
    def output_gate(result):
        return {"warn": "counts unusable"}

    warnings: list[str] = []

    verdict = dispatch.evaluate_gates(
        _model_phase(output_gate),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        warnings,
    )

    assert verdict is None
    assert warnings == ["phase 'explore' gate 'output_gate' warned: counts unusable"]


def _blocking_gate(result, blocked):
    return {"blocked": blocked}


def test_a_callable_gate_without_a_name_is_named_by_its_repr():
    # A functools.partial has no __name__; the display name falls back to repr.
    gate = functools.partial(_blocking_gate, blocked="x")
    name = repr(gate)

    verdict = dispatch.evaluate_gates(
        _model_phase(gate),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is False
    assert verdict.detail == f"phase 'explore' gate {name!r} failed: blocked=x"


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
    make_role(tmp_path / "bundles", methodology=overrides.pop("methodology", None))
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
    assert text.startswith("Standing instructions for explorer.")
    assert "# phase: explore" in text
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
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result is None
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]
    # The attempt row still records the path the attempt directory would have
    # used (cli.read_artifact reads it) even though classify was handed None.
    terminal = [
        line.payload
        for line in store.journal.read()
        if line.event == "attempt_upsert" and line.payload["status"] == "ok"
    ][0]
    assert terminal["result_path"].endswith("spec.1/result.json")


SPEC_DOCUMENT = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    result: SpecResult
    writes: docs/superpowers/specs/{stem}.md
"""

VALID_SPEC_RESULT = json.dumps({"path": "docs/superpowers/specs/task-x.md", "note": None})
SPEC_RESULT_WITHOUT_PATH = json.dumps({"note": None})


def _spec_runner(store, workflow, launcher, tmp_path, worktree):
    return _runner(
        store,
        workflow,
        launcher,
        tmp_path,
        worktree,
        result_models={"FakeResult": FakeResult, "SpecResult": results.SpecResult},
    )


def test_a_spec_phase_validates_its_result_and_returns_the_json_dump(
    store, tmp_path, worktree
):
    workflow = _workflow(SPEC_DOCUMENT, {})
    launcher = FakeLauncher(results=[VALID_SPEC_RESULT])
    runner, _ = _spec_runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result == {"path": "docs/superpowers/specs/task-x.md", "note": None}
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]
    contract = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[1]
    assert '"path"' in contract
    assert '"note"' in contract
    assert '"additionalProperties": false' in contract


def test_a_spec_result_missing_path_is_schema_invalid_and_fails_the_phase(
    store, tmp_path, worktree
):
    workflow = _workflow(SPEC_DOCUMENT, {})
    launcher = FakeLauncher(results=[SPEC_RESULT_WITHOUT_PATH])
    runner, _ = _spec_runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert caught.value.phase == "spec"
    assert caught.value.outcome == "schema_invalid"
    assert "path" in caught.value.detail
    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "schema_invalid")]


def test_a_spec_attempt_that_writes_no_result_file_is_a_harness_error(
    store, tmp_path, worktree
):
    workflow = _workflow(SPEC_DOCUMENT, {})
    launcher = FakeLauncher(results=[None])
    runner, _ = _spec_runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert "no result file" in caught.value.detail


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


def test_an_unexpected_error_mid_attempt_still_closes_the_phase(store, tmp_path, worktree):
    # §9: every state edge is journalled, and `_run_deterministic` already
    # records its phase `failed` when a step raises. Without the symmetric
    # record here a phase stays `started` forever after the walk has escalated
    # -- which is exactly what resume reads as work still in flight.
    def explode(argv, *, cwd, timeout, stdout_path):
        raise OSError("the run directory went away")

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    runner, _ = _runner(store, workflow, explode, tmp_path, worktree)

    with pytest.raises(OSError, match="the run directory went away"):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    detail = [
        line.payload["detail"]
        for line in store.journal.read()
        if line.event == "phase_upsert"
    ][-1]
    assert "OSError: the run directory went away" in detail


# ── the production default, which no other test in this file can see ─────────
# `_runner` always injects `result_models={"FakeResult": FakeResult}` and
# `overrides` can only replace that key, never omit it -- so these two
# construct their runners directly. Neither dispatches: construction is the
# whole assertion.


def test_a_default_runner_carries_the_shipped_result_model_table(store):
    runner = dispatch.AgentRunner(
        workflow=_workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None}),
        store=store,
        launcher=FakeLauncher(results=[VALID_RESULT]),
        run_id=RUN_ID,
        story_id=STORY_ID,
        card_id=CARD,
    )

    assert runner.result_models == results.RESULT_MODELS
    # A copy, not the module object: one runner must not be able to corrupt the
    # table every later runner in this process will be built from.
    assert runner.result_models is not results.RESULT_MODELS
    assert isinstance(runner.result_models, dict)
    runner.result_models.pop("ExploreResult")
    assert "ExploreResult" in results.RESULT_MODELS


def test_the_production_runner_factory_carries_the_shipped_table(store):
    # `cli.default_runner_factory` omits `result_models` on purpose. This is the
    # path the addendum section 1 smoke takes, and the only test that walks it.
    runner = cli.default_runner_factory(
        workflow=_workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None}),
        store=store,
        run_id=RUN_ID,
        story_id=STORY_ID,
        card_id=CARD,
    )

    assert isinstance(runner, dispatch.AgentRunner)
    assert runner.result_models == results.RESULT_MODELS
    assert set(runner.result_models) == {
        "ExploreResult",
        "CriticResult",
        "SpecResult",
        "PlanResult",
        "ImplementResult",
        "ReviewResult",
        "ResolveResult",
    }


# ── the composed brief (addendum R2 §2) ──────────────────────────────────────


def _gated_workflow():
    return _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})


def test_the_prompt_the_launcher_is_pointed_at_is_the_full_brief(
    store, tmp_path, worktree
):
    # Spec test 1: role system text, methodology heading and body, rendered
    # inputs, then this attempt's own result path, in that order.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    heading = f"{prompt.METHODOLOGY_HEADING_PREFIX}test-driven-development.md"
    result_path = str(paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json")
    assert brief.index("Standing instructions for explorer.") < brief.index(heading)
    assert brief.index(heading) < brief.index("Red, green, refactor.")
    assert brief.index("Red, green, refactor.") < brief.index("# phase: explore")
    assert brief.index("# phase: explore") < brief.index(prompt.RESULT_HEADING)
    assert brief.index(prompt.RESULT_HEADING) < brief.index(result_path)


def test_the_result_contract_embeds_the_phases_result_schema(store, tmp_path, worktree):
    # Spec test 2: the schema in the brief is the model the engine validates
    # against, so instruction and validator cannot drift.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    contract = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[1]
    assert '"summary"' in contract
    assert '"required"' in contract
    assert '"additionalProperties": false' in contract


def test_a_phase_with_no_declared_result_gets_no_result_contract(
    store, tmp_path, worktree
):
    # Spec test 6 / Review Focus 4: no contract at all, not a half-contract error.
    document = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    writes: docs/superpowers/specs/{stem}.md
"""
    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    assert result is None
    assert prompt.RESULT_HEADING not in brief
    assert "Standing instructions for explorer." in brief
    assert f"{prompt.METHODOLOGY_HEADING_PREFIX}test-driven-development.md" in brief
    assert "# phase: explore" in brief


def test_the_journalled_prompt_path_is_the_file_the_launcher_was_pointed_at(
    store, tmp_path, worktree
):
    # Spec test 7: Attempt.prompt_path, the adapter's argv and the composed file
    # are one and the same thing.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    argv = launcher.calls[0]
    pointed = Path(argv[argv.index("--prompt") + 1])
    terminal = [
        line.payload
        for line in store.journal.read()
        if line.event == "attempt_upsert" and line.payload["status"] == "ok"
    ][0]
    on_disk = pointed.read_text(encoding="utf-8")
    assert pointed == paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "prompt.txt"
    assert terminal["prompt_path"] == str(pointed)
    # The file itself, not `launcher.prompts[0]` -- the fake launcher read that
    # string out of this very path, so comparing the two would compare the file
    # to itself and would hold for any content whatsoever.
    assert on_disk.startswith("Standing instructions for explorer.")
    assert f"{prompt.METHODOLOGY_HEADING_PREFIX}test-driven-development.md" in on_disk
    assert "# phase: explore" in on_disk
    assert str(pointed.parent / "result.json") in on_disk.split(
        prompt.RESULT_HEADING, 1
    )[1]


def test_a_role_with_no_methodology_still_composes_a_brief(store, tmp_path, worktree):
    # Review Focus 1: RoleBundle.methodology defaults to {} and that is legal.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree, methodology={})

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    assert result == {"summary": "explored the tree", "ok": True}
    assert prompt.METHODOLOGY_HEADING_PREFIX not in brief
    assert "Standing instructions for explorer." in brief
    assert "# phase: explore" in brief
    assert prompt.RESULT_HEADING in brief


def test_a_methodology_documents_own_headings_survive_into_the_brief(
    store, tmp_path, worktree
):
    # Review Focus 2: vendored skill files are markdown with their own headings;
    # the brief hands the agent the text, byte for byte.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    assert "# Test-driven development" in brief
    assert "## the loop" in brief
    assert METHODOLOGY.strip("\n") in brief


def test_a_prompt_that_cannot_be_written_is_a_named_engine_error(
    store, tmp_path, worktree, monkeypatch
):
    # Review Focus 3 / spec error paths: RenderedPrompt.write stays the single
    # place this can fail, the phase is journalled failed, nothing is launched.
    real_write_text = Path.write_text

    def refuse(self, *args, **kwargs):
        if self.name == "prompt.txt":
            raise OSError("no space left on device")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", refuse)
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(EngineError) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.phase == "explore"
    assert "prompt.txt" in str(caught.value)
    assert launcher.calls == []
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]


def test_a_retry_prompt_carries_the_feedback_after_exactly_one_contract(
    store, tmp_path, worktree
):
    # Spec test 3: the brief is composed once per attempt from the untouched
    # base, so the contract cannot be duplicated by a re-composition.
    workflow = _gated_workflow()
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    second = launcher.prompts[1]
    assert second.count(prompt.RESULT_HEADING) == 1
    assert second.count("# phase: explore") == 1
    assert second.count(dispatch.FEEDBACK_HEADING) == 1
    assert second.index(prompt.RESULT_HEADING) < second.index(dispatch.FEEDBACK_HEADING)
    assert "summary" in second.split(dispatch.FEEDBACK_HEADING, 1)[1]


def test_a_third_attempt_accumulates_both_feedback_blocks_with_one_contract(
    store, tmp_path, worktree
):
    # Spec test 4: §6 step 7's "the prior prompt plus the feedback block",
    # preserved now that the accumulation lives in the loop rather than in the
    # RenderedPrompt.
    document = AGENT_DOCUMENT.replace("max_attempts: 2", "max_attempts: 3")
    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    third = launcher.prompts[2]
    assert len(launcher.prompts) == 3
    assert third.count(dispatch.FEEDBACK_HEADING) == 2
    assert third.count(prompt.RESULT_HEADING) == 1
    assert third.count("# phase: explore") == 1


def test_each_attempts_prompt_names_its_own_result_path(store, tmp_path, worktree):
    # Spec test 5: attempt 2 must not tell the harness to overwrite attempt 1's
    # result file -- classify() reads this attempt's path and nothing else.
    workflow = _gated_workflow()
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    first_path = str(paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json")
    second_path = str(paths.attempt_dir(RUN_ID, CARD, "explore", 2) / "result.json")
    assert first_path in launcher.prompts[0]
    assert second_path not in launcher.prompts[0]
    assert second_path in launcher.prompts[1]
    assert first_path not in launcher.prompts[1]


def test_a_retry_changes_only_the_result_path_and_the_feedback(
    store, tmp_path, worktree
):
    # Review Focus 5: the rendered-inputs body of attempt 2 is byte-identical to
    # attempt 1's, so a retry is the same brief plus one appended section.
    workflow = _gated_workflow()
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    first_head = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[0]
    second_head = launcher.prompts[1].split(prompt.RESULT_HEADING, 1)[0]
    assert first_head == second_head
    replayed = launcher.prompts[1].split(dispatch.FEEDBACK_HEADING, 1)[0].replace(
        str(paths.attempt_dir(RUN_ID, CARD, "explore", 2)),
        str(paths.attempt_dir(RUN_ID, CARD, "explore", 1)),
    )
    assert replayed.rstrip("\n") == launcher.prompts[0].rstrip("\n")


def test_accumulated_feedback_blocks_keep_the_order_they_were_produced(
    store, tmp_path, worktree
):
    # Spec invariant 5: a third attempt carries both earlier complaints, oldest
    # first, so the agent reads its own history forwards and not backwards.
    document = AGENT_DOCUMENT.replace("max_attempts: 2", "max_attempts: 3")
    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[NOT_JSON, INVALID_RESULT, INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    # Only the region below the first heading, so the schema's own "summary"
    # property in the Result contract cannot stand in for the complaint.
    blocks = launcher.prompts[2].split(dispatch.FEEDBACK_HEADING, 1)[1]
    assert launcher.prompts[2].count(dispatch.FEEDBACK_HEADING) == 2
    assert blocks.index("not valid JSON") < blocks.index("Field required")


# ── phase-model phases through the runner ────────────────────────────────────

CRITIC_RESULT = json.dumps({"blockers": False, "reason": None, "summary": "no blockers"})


def test_result_class_is_used_directly(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[CRITIC_RESULT])
    runner, _ = _runner(
        store, workflow, launcher, tmp_path, worktree, result_models={}
    )

    result = runner(
        _model_phase(result=results.CriticResult), _context(worktree), _rendered()
    )

    assert result == {"blockers": False, "reason": None, "summary": "no blockers"}
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "done")]
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]
    contract = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[1]
    assert '"blockers"' in contract


def test_a_result_class_wins_over_a_same_named_table_entry(store, tmp_path, worktree):
    # Review Focus 5: the table maps "CriticResult" to a different model; the
    # class on the phase is what the result file is validated against.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(
        store,
        workflow,
        launcher,
        tmp_path,
        worktree,
        result_models={"CriticResult": FakeResult},
    )

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(
            _model_phase(result=results.CriticResult), _context(worktree), _rendered()
        )

    assert caught.value.outcome == "schema_invalid"
    assert "blockers" in caught.value.detail
    assert len(launcher.calls) == 1


def test_phase_model_retry_is_honoured(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[INVALID_RESULT, CRITIC_RESULT])
    runner, _ = _runner(
        store, workflow, launcher, tmp_path, worktree, result_models={}
    )
    phase = _model_phase(
        result=results.CriticResult, retry=phases.Retry(2, ("schema_invalid",))
    )

    result = runner(phase, _context(worktree), _rendered())

    assert result == {"blockers": False, "reason": None, "summary": "no blockers"}
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "schema_invalid"), (2, "started"), (2, "ok")
    ]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "done")]


def test_phase_model_retry_does_not_retry_an_outcome_outside_on(store, tmp_path, worktree):
    # The `on` half of the duck-typed read: gate_failed is not in `on`, so one
    # dispatch only, even with attempts left.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[CRITIC_RESULT])
    runner, _ = _runner(
        store, workflow, launcher, tmp_path, worktree, result_models={}
    )
    phase = _model_phase(
        lambda result: {"blocked": "critic"},
        result=results.CriticResult,
        retry=phases.Retry(3, ("schema_invalid",)),
    )

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(phase, _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert len(launcher.calls) == 1
    assert _phase_statuses(store)[-1] == ("explore", "failed")


# ── the bridge's spawn hook reaches a launcher that declares it ──────────────


@dataclass
class SpawnAwareLauncher(FakeLauncher):
    """A `FakeLauncher` that declares `on_spawn`, as `launcher.run_direct` does."""

    hooks: list[object] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path, on_spawn=None) -> Outcome:
        self.hooks.append(on_spawn)
        return super().__call__(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path
        )


async def test_a_launcher_that_declares_on_spawn_gets_the_bridge_calls_hook(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = SpawnAwareLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = await bridge.call_agent(
        runner, workflow.phase("explore"), _context(worktree), _rendered()
    )

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.hooks) == 1
    assert callable(launcher.hooks[0])


def test_a_launcher_that_declares_on_spawn_gets_none_outside_a_bridge_call(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = SpawnAwareLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert launcher.hooks == [None]


async def test_a_launcher_without_on_spawn_still_works_inside_a_bridge_call(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = await bridge.call_agent(
        runner, workflow.phase("explore"), _context(worktree), _rendered()
    )

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 1
