"""Behaviour of one agent phase's dispatch, validation, retry and escalation
(design §6 lines 261-278, §9 lines 365-368, §12 line 429).

Engine tier per design §14 lines 486-488: a fake adapter and a fake launcher
that write canned result files (valid, invalid, gate-failing, absent) stand in
for a harness, and the store is a real temp SQLite projection plus a real temp
JSONL journal. No process is ever started -- the launcher is injected, and one
test asserts `subprocess.Popen` is never reached.
"""

import dataclasses
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from agent_manager import (
    cli,
    dispatch,
    models,
    paths,
    prompt,
    results,
    store as store_module,
)
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime.errors import EngineError
from agent_manager.harness.base import Outcome, Usage
from agent_manager.roles.loader import load_role
from agent_manager.runtime import bridge, walk
from agent_manager.workflow import phases

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


def test_next_attempt_is_one_past_paths_highest_attempt(data_home, monkeypatch):
    seen = []

    def highest(run_id, card, phase):
        seen.append((run_id, card, phase))
        return 41

    monkeypatch.setattr(paths, "highest_attempt", highest)

    assert dispatch.next_attempt(RUN_ID, CARD, "explore") == 42
    assert seen == [(RUN_ID, CARD, "explore")]


@pytest.mark.parametrize("existing", [0, 1, 3])
def test_next_attempt_equals_highest_attempt_plus_one_on_disk(data_home, existing):
    for n in range(1, existing + 1):
        paths.attempt_dir(RUN_ID, CARD, "explore", n)

    assert paths.highest_attempt(RUN_ID, CARD, "explore") == existing
    assert dispatch.next_attempt(RUN_ID, CARD, "explore") == existing + 1


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
    `exit_codes`, when given, overrides `exit_code` per call by the same rule.
    """

    results: list[str | None]
    stdout: str = "fake-harness ran\n"
    exit_code: int | None = 0
    timed_out: bool = False
    exit_codes: list[int | None] | None = None
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
        exit_code = self.exit_code
        if self.exit_codes is not None:
            exit_code = self.exit_codes[min(len(self.calls) - 1, len(self.exit_codes) - 1)]
        return Outcome(
            argv=list(argv),
            exit_code=exit_code,
            timed_out=self.timed_out,
            duration=1.25,
            stdout_path=stdout_path,
        )


def _outcome(tmp_path: Path, *, exit_code: int | None = 0, timed_out: bool = False) -> Outcome:
    log = tmp_path / "stdout.log"
    log.write_text("fake-harness ran\n", encoding="utf-8")
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


def test_read_result_with_no_model_is_ok_without_touching_the_path():
    assert dispatch.read_result(None, None) == dispatch.Verdict("ok", result=None)


def test_read_result_judges_the_file_alone(tmp_path):
    path = tmp_path / "result.json"

    missing = dispatch.read_result(path, FakeResult)
    assert missing.status == "harness_error"
    assert "wrote no result file" in missing.detail

    path.write_text(NOT_JSON, encoding="utf-8")
    assert dispatch.read_result(path, FakeResult).status == "schema_invalid"

    path.write_text(INVALID_RESULT, encoding="utf-8")
    assert dispatch.read_result(path, FakeResult).status == "schema_invalid"

    path.write_bytes(b"\xff\xfe not utf-8")
    assert dispatch.read_result(path, FakeResult).status == "schema_invalid"

    path.write_text(VALID_RESULT, encoding="utf-8")
    assert dispatch.read_result(path, FakeResult) == dispatch.Verdict(
        "ok", result={"summary": "explored the tree", "ok": True}
    )


def test_classify_after_a_clean_exit_is_read_result(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(VALID_RESULT, encoding="utf-8")

    expected = dispatch.Verdict("ok", result={"summary": "explored the tree", "ok": True})
    assert dispatch.classify(_outcome(tmp_path), path, FakeResult) == expected
    assert dispatch.read_result(path, FakeResult) == expected


def AGENT_DOCUMENT(functions: dict[str, object]) -> phases.Workflow:
    """One `explore` phase: role `explorer`, result `FakeResult`, gated by
    `functions["output_gate"]`, retried twice on `schema_invalid` and
    `gate_failed` -- the declared twin of the YAML document it replaced."""
    return _agentic(functions["output_gate"])


def _agentic(*gates, **overrides) -> phases.Workflow:
    """A one-phase workflow around `_model_phase`, carrying AGENT_DOCUMENT's
    retry policy unless `overrides` replaces it."""
    fields = {"retry": phases.Retry(2, ("schema_invalid", "gate_failed")), **overrides}
    return phases.Workflow("agentic", (_model_phase(*gates, **fields),))


def _workflow(document, functions: dict[str, object]) -> phases.Workflow:
    return document(functions)


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


def _runner(store, launcher, tmp_path, worktree, **overrides):
    """A runner wired to the fakes, plus the adapter it was wired to.

    `overrides` replaces any keyword (e.g. `result_models={}`) rather than
    adding a second one, so a test can knock out exactly one seam.
    """
    adapter = overrides.pop("adapter", FakeAdapter())
    make_role(tmp_path / "bundles", methodology=overrides.pop("methodology", None))
    kwargs = {
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
    runner, adapter = _runner(store, launcher, tmp_path, worktree)

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
    runner, adapter = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert len(launcher.calls) == 1
    assert launcher.calls[0] == adapter.build_command(adapter.dispatches[0])
    assert launcher.calls[0][0] == "fake-harness"


def _terminal_attempts(opened) -> list[dict]:
    """Every journalled attempt payload past `started`, in journal order."""
    return [
        line.payload
        for line in opened.journal.read()
        if line.event == "attempt_upsert" and line.payload["status"] != "started"
    ]


_USAGE_KEYS = frozenset({"tokens_in", "tokens_out", "cost"})
"""The attempt keys every journal written before 2026-10-03 carries, as null."""


def test_the_outcome_is_journalled_on_the_attempt(store, tmp_path, worktree):
    # §5.2: `Attempt` no longer declares the three usage fields, so the
    # journalled payload carries no such keys at all. The log is deliberately
    # the old bait -- FakeAdapter's `parse_usage` would turn it into 11/22/0.5
    # if anything still asked.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], stdout="usage: tokens\n")
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    [terminal] = _terminal_attempts(store)
    assert terminal["status"] == "ok"
    assert terminal["duration"] == 1.25
    assert terminal["exit_code"] == 0
    assert _USAGE_KEYS.isdisjoint(terminal)


@dataclass
class UnreadableLogLauncher(FakeLauncher):
    """A `FakeLauncher` that leaves `stdout_path` absent or unreadable as text.

    `log` is `"absent"` (no file), `"directory"` (a directory where the log
    should be) or `"non_utf8"` (bytes that are not UTF-8 but still contain
    the old "usage" bait once decoded with replacement).
    """

    log: str = "absent"

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        outcome = super().__call__(argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path)
        stdout_path.unlink()
        if self.log == "directory":
            stdout_path.mkdir()
        elif self.log == "non_utf8":
            stdout_path.write_bytes(b"\xff\xfeusage: \x80tokens\n")
        return outcome


@pytest.mark.parametrize("log", ["absent", "directory", "non_utf8"])
def test_the_engine_never_opens_the_harness_log(store, tmp_path, worktree, monkeypatch, log):
    # §5.3 item 6. D4: stdout.log is a log, never read by the engine. Any read
    # of a file named stdout.log is recorded, so "swallowed the OSError" and
    # "never looked" are told apart.
    reads: list[Path] = []
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes
    real_open = Path.open

    def read_text(self, *args, **kwargs):
        if self.name == dispatch.STDOUT_NAME:
            reads.append(self)
        return real_read_text(self, *args, **kwargs)

    def read_bytes(self, *args, **kwargs):
        if self.name == dispatch.STDOUT_NAME:
            reads.append(self)
        return real_read_bytes(self, *args, **kwargs)

    def open_(self, *args, **kwargs):
        # Only reads count: FakeLauncher's own `write_text` goes through
        # `Path.open(mode="w")` on some Python versions.
        mode = args[0] if args else kwargs.get("mode", "r")
        if self.name == dispatch.STDOUT_NAME and "r" in mode:
            reads.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "open", open_)
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = UnreadableLogLauncher(results=[VALID_RESULT], log=log)
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert reads == []
    [terminal] = _terminal_attempts(store)
    assert terminal["status"] == "ok"
    assert terminal["duration"] == 1.25
    assert terminal["exit_code"] == 0
    assert _USAGE_KEYS.isdisjoint(terminal)


class UsageRefusingAdapter(FakeAdapter):
    """A `FakeAdapter` whose `parse_usage` must never be reached."""

    def parse_usage(self, stdout: str) -> Usage | None:
        raise AssertionError("dispatch must never ask the adapter for usage")


def test_dispatch_never_asks_the_adapter_for_usage(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], stdout="usage: tokens\n")
    runner, _ = _runner(
        store, launcher, tmp_path, worktree, adapter=UsageRefusingAdapter()
    )

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_a_timed_out_attempt_journals_its_duration_and_no_usage(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(
        results=[None], exit_code=None, timed_out=True, stdout="usage: tokens\n"
    )
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    terminals = _terminal_attempts(store)
    assert terminals
    for terminal in terminals:
        assert terminal["status"] == "harness_error"
        assert terminal["duration"] == 1.25
        assert terminal["exit_code"] is None
        assert _USAGE_KEYS.isdisjoint(terminal)


def test_a_persistently_invalid_result_retries_to_max_attempts_then_fails(
    store, tmp_path, worktree
):
    # Spec tests 4, 10 and 12.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "schema_invalid"), (2, "started"), (2, "ok")
    ]


def test_two_harness_errors_fail_the_phase_as_today(store, tmp_path, worktree):
    # Spec tests 6 and 13 still hold -- harness_error is never a `retry.on`
    # retry -- but the first one gets the single redispatch (1fadbbdd), so
    # the phase fails after two dispatches, on attempt 2's detail.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    second = paths.attempt_dir(RUN_ID, CARD, "explore", 2) / "result.json"
    assert caught.value.outcome == "harness_error"
    assert caught.value.detail == f"the harness wrote no result file at {second}"
    assert caught.value.result is None
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "harness_error"), (2, "started"), (2, "harness_error")
    ]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    failed_detail = [
        line.payload["detail"]
        for line in store.journal.read()
        if line.event == "phase_upsert"
    ][-1]
    assert failed_detail == caught.value.detail
    assert len(runner.warnings) == 1
    assert "attempt 1 ended harness_error" in runner.warnings[0]


def test_a_non_zero_exit_is_harness_error(store, tmp_path, worktree):
    # Spec test 7.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], exit_code=3)
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert "exited 3" in caught.value.detail


def test_a_timeout_is_harness_error_and_the_log_survives(store, tmp_path, worktree):
    # Spec test 8.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None], exit_code=None, timed_out=True)
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert (paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "stdout.log").is_file()


def _redispatch_warning(n: int, detail: str) -> str:
    return (
        f"phase 'explore': attempt {n} ended harness_error ({detail}); "
        "dispatching once more"
    )


def _no_result_file(n: int) -> str:
    path = paths.attempt_dir(RUN_ID, CARD, "explore", n) / "result.json"
    return f"the harness wrote no result file at {path}"


def test_a_harness_error_is_redispatched_once_and_the_second_attempt_can_succeed(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "harness_error"), (2, "started"), (2, "ok")
    ]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "done")]
    assert runner.warnings == [_redispatch_warning(1, _no_result_file(1))]


def test_an_ok_first_attempt_dispatches_once(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]
    assert runner.warnings == []


def test_a_redispatch_brief_carries_no_harness_error_feedback(store, tmp_path, worktree):
    # D4: nothing is appended for a harness_error, so the redispatched brief is
    # the failed one with only its own attempt directory swapped in.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    first_dir = str(paths.attempt_dir(RUN_ID, CARD, "explore", 1))
    second_dir = str(paths.attempt_dir(RUN_ID, CARD, "explore", 2))
    assert dispatch.FEEDBACK_HEADING not in launcher.prompts[1]
    assert "wrote no result file" not in launcher.prompts[1]
    assert launcher.prompts[1] == launcher.prompts[0].replace(first_dir, second_dir)


def test_the_redispatch_warning_names_the_journalled_attempt_number(
    store, tmp_path, worktree
):
    # A resumed run finds explore.1 and explore.2 on disk, so its first
    # attempt here is 3 -- the warning must say 3, not 1.
    paths.attempt_dir(RUN_ID, CARD, "explore", 1)
    paths.attempt_dir(RUN_ID, CARD, "explore", 2)
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert _attempt_statuses(store) == [
        (3, "started"), (3, "harness_error"), (4, "started"), (4, "ok")
    ]
    assert runner.warnings == [_redispatch_warning(3, _no_result_file(3))]


def test_a_redispatch_does_not_consume_the_retry_budget(store, tmp_path, worktree):
    # D2: Retry(2, ...) still has both of its attempts after the redispatch.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, NOT_JSON, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 3
    assert [status for _n, status in _attempt_statuses(store) if status != "started"] == [
        "harness_error", "schema_invalid", "ok"
    ]
    assert _phase_statuses(store)[-1] == ("explore", "done")


def test_a_redispatch_plus_a_spent_retry_budget_stops_at_budget_plus_one(
    store, tmp_path, worktree
):
    # Review Focus 1: budget 2 plus the redispatch is three attempts, never four.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, NOT_JSON])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "schema_invalid"
    assert len(launcher.calls) == 3
    assert [status for _n, status in _attempt_statuses(store) if status != "started"] == [
        "harness_error", "schema_invalid", "schema_invalid"
    ]


def test_a_harness_error_after_a_schema_retry_is_still_redispatched(
    store, tmp_path, worktree
):
    def document(functions):
        return _agentic(functions["output_gate"], retry=phases.Retry(2, ("schema_invalid",)))

    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[NOT_JSON, None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 3
    # D4: attempt 1's validator text is still in attempt 3's brief.
    third = launcher.prompts[2]
    assert dispatch.FEEDBACK_HEADING in third
    assert "not valid JSON" in third.split(dispatch.FEEDBACK_HEADING, 1)[1]
    assert third.count(dispatch.FEEDBACK_HEADING) == 1
    assert runner.warnings == [_redispatch_warning(2, _no_result_file(2))]


def test_a_second_harness_error_after_a_schema_retry_fails_the_phase(
    store, tmp_path, worktree
):
    # Review Focus 3: the allowance is per call, not per streak.
    def document(functions):
        return _agentic(functions["output_gate"], retry=phases.Retry(2, ("schema_invalid",)))

    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[NOT_JSON, None])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert caught.value.detail == _no_result_file(3)
    assert len(launcher.calls) == 3
    assert len(runner.warnings) == 1


def test_a_phase_with_no_retry_block_still_gets_the_redispatch(store, tmp_path, worktree):
    def document(functions):
        return _agentic(functions["output_gate"], retry=None)

    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 2
    assert _phase_statuses(store)[-1] == ("explore", "done")


def test_a_broken_gate_on_the_redispatch_fails_at_once(store, tmp_path, worktree):
    # Behavior 4: the redispatch is judged like any attempt, and a broken gate
    # is still fatal however much budget is left.
    def output_gate(result):
        raise RuntimeError("the gate itself is broken")

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert len(launcher.calls) == 2


def test_a_non_zero_exit_is_redispatched(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], exit_codes=[3, 0])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 2
    assert runner.warnings == [_redispatch_warning(1, "the harness exited 3")]


def test_a_timeout_is_redispatched(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None], exit_code=None, timed_out=True)
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "harness_error"), (2, "started"), (2, "harness_error")
    ]
    assert len(runner.warnings) == 1
    assert "timed out" in runner.warnings[0]


def test_a_result_less_phase_redispatches_a_bad_exit(store, tmp_path, worktree):
    # Behavior 9: judged on exit status alone, a non-zero exit is still a
    # harness_error and still gets the redispatch.
    def document(functions):
        return _agentic(
            name="spec", result=None, retry=None, writes="docs/superpowers/specs/{stem}.md"
        )

    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[None], exit_codes=[2, 0])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result is None
    assert len(launcher.calls) == 2
    assert runner.warnings == [
        "phase 'spec': attempt 1 ended harness_error (the harness exited 2); "
        "dispatching once more"
    ]


def test_a_launcher_exception_is_not_redispatched(store, tmp_path, worktree):
    # Behavior 8 / Review Focus 5: a raise is not a harness_error verdict.
    calls = []

    def explode(argv, *, cwd, timeout, stdout_path):
        calls.append(list(argv))
        raise OSError("the harness binary vanished")

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    runner, _ = _runner(store, explode, tmp_path, worktree)

    with pytest.raises(OSError, match="the harness binary vanished"):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert len(calls) == 1
    assert _attempt_statuses(store) == [(1, "started")]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    assert runner.warnings == []


def test_a_retryable_gate_failure_re_dispatches_with_the_gate_detail(
    store, tmp_path, worktree
):
    # Spec tests 9 and 11.
    verdicts = [{"blocked": "exploration", "detail": "summary is a placeholder"}, None]

    def output_gate(result):
        return verdicts.pop(0)

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    def document(functions):
        return _agentic(functions["output_gate"], retry=phases.Retry(2, ("schema_invalid",)))

    workflow = _workflow(document, {"output_gate": lambda result: {"blocked": "exploration"}})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert len(launcher.calls) == 1


def test_a_gate_failure_keeps_the_dispatch_s_own_result(store, tmp_path, worktree):
    # The result a gate judges is still what the agent produced: losing it
    # when the gate fails the phase (as the pre-fix code did, replacing the
    # whole Verdict) left `comments.agent_reason` with nothing to read on a
    # real escalation, since a failed phase's ContextItem is never yielded.
    workflow = _workflow(
        AGENT_DOCUMENT, {"output_gate": lambda result: {"blocked": "exploration"}}
    )
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert caught.value.result == {"summary": "explored the tree", "ok": True}


def test_a_gate_that_raises_stops_after_one_dispatch(store, tmp_path, worktree):
    # Review Focus: fatal beats retry.on, which lists gate_failed here.
    def output_gate(result):
        raise RuntimeError("the gate itself is broken")

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert "RuntimeError" in caught.value.detail
    assert len(launcher.calls) == 1


# ── the gate seam: what AgentRunner does with a gate's answer ────────────────
# Engine tier (design §14): driven through `_runner` and `FakeLauncher` with
# canned result files, never a process. The evaluator's own behaviour is tested
# once, beside it, in tests/runtime/test_walk.py; these pin what an agent phase
# makes of each outcome, byte for byte, across the S3 rewire.

_BROKEN_RAISED = (
    "; a gate returns None to pass or a mapping verdict to fail, so this is a "
    "broken gate rather than a failed attempt"
)
_BROKEN_NOT_A_MAPPING = (
    "; a gate returns None to pass or a mapping verdict to fail, and anything "
    "else would be read as a pass by accident"
)


def raising_gate(result):
    raise RuntimeError("the gate itself is broken")


def chatty_gate(result):
    return "looks fine to me"


def empty_list_gate(result):
    return []


class _ControlSignal(BaseException):
    """Stands in for a pygents control-flow signal: a `BaseException`, not an `Exception`."""


def _last_phase_detail(opened) -> str | None:
    return [
        line.payload["detail"]
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ][-1]


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        (
            raising_gate,
            "phase 'explore' gate 'raising_gate' raised RuntimeError: "
            "the gate itself is broken" + _BROKEN_RAISED,
        ),
        (
            chatty_gate,
            "phase 'explore' gate 'chatty_gate' returned str" + _BROKEN_NOT_A_MAPPING,
        ),
        (
            empty_list_gate,
            "phase 'explore' gate 'empty_list_gate' returned list" + _BROKEN_NOT_A_MAPPING,
        ),
    ],
    ids=["raises", "returns-str", "returns-empty-list"],
)
def test_agent_runner_maps_a_broken_gate_to_a_fatal_gate_failed(
    store, tmp_path, worktree, gate, expected
):
    # S3 §6's thin test for the agent phase kind: retry.on lists gate_failed
    # and the budget is three, yet a broken gate is dispatched exactly once.
    workflow = _agentic(gate, retry=phases.Retry(3, ("schema_invalid", "gate_failed")))
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert caught.value.detail == expected
    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "gate_failed")]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    assert _last_phase_detail(store) == expected


def test_a_warning_gate_passes_and_warns_exactly_once(store, tmp_path, worktree):
    def output_gate(result):
        return {"warn": "counts unusable"}

    workflow = _agentic(output_gate)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert runner.warnings == [
        "phase 'explore' gate 'output_gate' warned: counts unusable"
    ]
    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_a_failing_gate_records_the_rendered_message_as_the_detail(
    store, tmp_path, worktree
):
    expected = "phase 'explore' gate '<lambda>' failed: blocked=x, detail=d"
    workflow = _agentic(lambda result: {"blocked": "x", "detail": "d"})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert caught.value.detail == expected
    # Not fatal: retry.on lists gate_failed, so the whole budget of two is spent
    # and the second prompt carries the rendered message as feedback.
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "gate_failed"), (2, "started"), (2, "gate_failed")
    ]
    assert _last_phase_detail(store) == expected
    second = (paths.attempt_dir(RUN_ID, CARD, "explore", 2) / "prompt.txt").read_text(
        encoding="utf-8"
    )
    assert expected in second.split(dispatch.FEEDBACK_HEADING, 1)[1]


def test_an_unbindable_gate_parameter_propagates_as_engine_error(
    store, tmp_path, worktree
):
    def output_gate(result, provided_verification):
        return None

    workflow = _agentic(output_gate)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(EngineError) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.parameter == "provided_verification"
    assert caught.value.function == "output_gate"
    assert caught.value.phase == "explore"
    assert len(launcher.calls) == 1
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    assert _last_phase_detail(store).startswith(
        "EngineError: phase 'explore', function 'output_gate', "
        "parameter 'provided_verification': no value for a required parameter"
    )


def test_a_warning_before_a_failing_gate_is_kept_once(store, tmp_path, worktree):
    def cautious(result):
        return {"warn": "coverage dipped"}

    def blocking(result):
        return {"blocked": "x"}

    workflow = _agentic(cautious, blocking, retry=None)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.detail == "phase 'explore' gate 'blocking' failed: blocked=x"
    assert runner.warnings == ["phase 'explore' gate 'cautious' warned: coverage dipped"]


def test_a_result_less_phase_still_runs_its_gates_against_none(store, tmp_path, worktree):
    seen: list[object] = []

    def output_gate(result):
        seen.append(result)

    workflow = _agentic(
        output_gate,
        name="spec",
        result=None,
        retry=None,
        writes="docs/superpowers/specs/{stem}.md",
    )
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result is None
    assert seen == [None]
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_a_control_flow_signal_from_a_gate_is_not_caught(store, tmp_path, worktree):
    def output_gate(result):
        raise _ControlSignal()

    workflow = _agentic(output_gate)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(_ControlSignal):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert len(launcher.calls) == 1


def test_agent_runner_takes_a_failing_verdict_from_the_shared_evaluator(
    store, tmp_path, worktree, monkeypatch
):
    # The seam itself (S3): the gate below would pass, so only a runner that
    # asks `walk.evaluate_gates` can see this `fail`.
    calls: list[tuple[str, object, object]] = []

    def shared(phase, values, warnings):
        calls.append((phase.name, values["result"], values["explore"]))
        return walk.GateVerdict(
            "fail",
            {"gate": "g", "verdict": {"k": "v"}, "message": "from the shared evaluator"},
        )

    monkeypatch.setattr(walk, "evaluate_gates", shared)
    workflow = _agentic(lambda result: None, retry=None)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    produced = {"summary": "explored the tree", "ok": True}
    assert caught.value.outcome == "gate_failed"
    assert caught.value.detail == "from the shared evaluator"
    assert calls == [("explore", produced, produced)]
    assert _last_phase_detail(store) == "from the shared evaluator"


def test_agent_runner_maps_a_broken_verdict_from_the_shared_evaluator_to_fatal(
    store, tmp_path, worktree, monkeypatch
):
    monkeypatch.setattr(
        walk,
        "evaluate_gates",
        lambda phase, values, warnings: walk.GateVerdict(
            "broken", {"gate": "g", "reason": "raised", "error": KeyError("gone")}
        ),
    )
    # retry.on lists gate_failed with a budget of two: only `fatal` stops it.
    workflow = _agentic(lambda result: None)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert caught.value.detail == (
        "phase 'explore' gate 'g' raised KeyError: 'gone'" + _BROKEN_RAISED
    )
    assert len(launcher.calls) == 1


def test_a_phase_with_no_retry_block_dispatches_exactly_once(store, tmp_path, worktree):
    # Review Focus: TASK's spec, plan, implement and review phases
    # carry no retry at all.
    def document(functions):
        return _agentic(retry=None)

    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "schema_invalid"
    assert len(launcher.calls) == 1


def test_a_phase_with_no_result_model_declared_needs_no_table_entry(
    store, tmp_path, worktree
):
    def document(functions):
        return _agentic(
            name="spec", result=None, retry=None, writes="docs/superpowers/specs/{stem}.md"
        )

    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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


def SPEC_DOCUMENT(functions: dict[str, object]) -> phases.Workflow:
    """One `spec` phase: role `explorer`, result `SpecResult`, writing the spec."""
    return _agentic(
        name="spec",
        result=results.SpecResult,
        retry=None,
        writes="docs/superpowers/specs/{stem}.md",
    )

VALID_SPEC_RESULT = json.dumps({"path": "docs/superpowers/specs/task-x.md", "note": None})
SPEC_RESULT_WITHOUT_PATH = json.dumps({"note": None})


def _spec_runner(store, launcher, tmp_path, worktree):
    return _runner(
        store,
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
    runner, _ = _spec_runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _spec_runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _spec_runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert "no result file" in caught.value.detail


def test_an_unregistered_result_model_is_a_named_engine_error(store, tmp_path, worktree):
    """`AgentRunner` still resolves a result given by name through
    `result_models`; a name the table lacks is refused before any launch."""
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    named = dataclasses.replace(workflow.phase("explore"), result="ExploreResult")
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree, result_models={})

    with pytest.raises(EngineError) as caught:
        runner(named, _context(worktree), _rendered())

    assert caught.value.phase == "explore"
    assert launcher.calls == []


def test_a_context_with_no_worktree_is_a_named_engine_error(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(opened, launcher, tmp_path, worktree)

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
    def document(functions):
        return _agentic(functions["output_gate"], retry=None)

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
        runner, _ = _runner(store, launcher, tmp_path, worktree)
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
    runner, _ = _runner(store, explode, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    contract = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[1]
    assert '"summary"' in contract
    assert '"required"' in contract
    assert '"additionalProperties": false' in contract


def test_a_phase_with_no_declared_result_gets_no_result_contract(
    store, tmp_path, worktree
):
    # Spec test 6 / Review Focus 4: no contract at all, not a half-contract error.
    def document(functions):
        return _agentic(
            name="spec", result=None, retry=None, writes="docs/superpowers/specs/{stem}.md"
        )

    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree, methodology={})

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    def document(functions):
        return _agentic(
            functions["output_gate"], retry=phases.Retry(3, ("schema_invalid", "gate_failed"))
        )
    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    def document(functions):
        return _agentic(
            functions["output_gate"], retry=phases.Retry(3, ("schema_invalid", "gate_failed"))
        )
    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[NOT_JSON, INVALID_RESULT, INVALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    launcher = FakeLauncher(results=[CRITIC_RESULT])
    runner, _ = _runner(
        store, launcher, tmp_path, worktree, result_models={}
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
    launcher = FakeLauncher(results=[INVALID_RESULT, CRITIC_RESULT])
    runner, _ = _runner(
        store, launcher, tmp_path, worktree, result_models={}
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
    launcher = FakeLauncher(results=[CRITIC_RESULT])
    runner, _ = _runner(
        store, launcher, tmp_path, worktree, result_models={}
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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

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
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert launcher.hooks == [None]


async def test_a_launcher_without_on_spawn_still_works_inside_a_bridge_call(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = await bridge.call_agent(
        runner, workflow.phase("explore"), _context(worktree), _rendered()
    )

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 1


# ── adoption (exactly-once Task 2.1) ─────────────────────────────────────────
# Engine tier: `adopt` is called directly on a runner that already ran the
# phase once through the counting FakeLauncher, so `launcher.calls` proves
# nothing was dispatched again. The pygents wiring is sibling 6ecbe6e2's.

OTHER_RUN_ID = "run-2"
OTHER_VALID_RESULT = json.dumps({"summary": "explored it again", "ok": True})
EXPLORED = {"summary": "explored the tree", "ok": True}


def _reused(n: int, source_run: str = RUN_ID, name: str = "explore") -> str:
    return (
        f"phase {name!r} was not dispatched again: attempt {n} of run {source_run} "
        "had already succeeded (result reused)"
    )


def _declines(runner) -> list[str]:
    return [warning for warning in runner.warnings if "was not reused" in warning]


def _seed(opened, run_id: str = RUN_ID, story_id: str = STORY_ID) -> None:
    """The run, story and subtask lines `replay` needs above any phase line."""
    opened.record_run(
        models.Run(
            id=run_id,
            workflow="agentic",
            repo_dir=Path("/repo"),
            base_branch="master",
            branch_prefix="m11/",
        )
    )
    opened.record_story(models.StoryRun(card_id=story_id, title="Adoption", level=0))
    opened.record_subtask(
        story_id,
        models.SubtaskRun(card_id=CARD, branch=f"m11/task-{CARD}", base_branch="master"),
    )


def _passing_phase() -> phases.AgentPhase:
    return _model_phase(lambda result: None)


def _succeed_once(
    store, tmp_path, worktree, phase=None, results=(VALID_RESULT,), story_id=STORY_ID
):
    """Seed the run tree, then run `phase` once: attempt 1 `ok`, phase `done`."""
    _seed(store, story_id=story_id)
    phase = _passing_phase() if phase is None else phase
    launcher = FakeLauncher(results=list(results))
    runner, _ = _runner(store, launcher, tmp_path, worktree, story_id=story_id)
    runner(phase, _context(worktree), _rendered())
    return runner, launcher, phase


@dataclass
class CrashingLauncher(FakeLauncher):
    """Writes its canned result, then dies before the attempt is judged."""

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        super().__call__(argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path)
        raise RuntimeError("the manager died after the harness wrote its result")


def test_adopt_returns_the_recorded_result_without_dispatching(store, tmp_path, worktree):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)

    adopted = runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0)

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)
    assert len(launcher.calls) == 1
    assert runner.warnings == [_reused(1)]
    assert _phase_statuses(store) == [
        ("explore", "started"), ("explore", "done"), ("explore", "done")
    ]
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_adopted_is_a_frozen_plain_value():
    adopted = dispatch.Adopted({"a": 1}, 2, RUN_ID)

    assert (adopted.result, adopted.attempt, adopted.source_run) == ({"a": 1}, 2, RUN_ID)
    with pytest.raises(dataclasses.FrozenInstanceError):
        adopted.attempt = 3


def test_an_attempt_at_or_below_the_floor_is_not_adopted(store, tmp_path, worktree):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())

    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=1) is None
    assert runner.warnings == []
    assert len(store.journal.read()) == lines
    assert len(launcher.calls) == 1


def test_an_orphaned_attempt_is_never_adopted(store, tmp_path, worktree):
    _seed(store)
    phase = _passing_phase()
    runner, _ = _runner(store, CrashingLauncher(results=[VALID_RESULT]), tmp_path, worktree)
    with pytest.raises(RuntimeError, match="the manager died"):
        runner(phase, _context(worktree), _rendered())
    assert (paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json").is_file()
    assert _attempt_statuses(store) == [(1, "started")]

    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0) is None
    assert runner.warnings == []

    # Resume later marks the orphan `harness_error`; it is still never adopted.
    started = next(
        line.payload for line in store.journal.read() if line.event == "attempt_upsert"
    )
    store.record_attempt(
        STORY_ID,
        CARD,
        "explore",
        models.Attempt.model_validate(started).model_copy(update={"status": "harness_error"}),
    )
    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0) is None
    assert runner.warnings == []


@pytest.mark.parametrize(
    ("damage", "why"),
    [
        ("delete", "the harness wrote no result file at"),
        ("not_json", "is not valid JSON"),
        ("schema", "summary"),
    ],
)
def test_a_result_that_no_longer_validates_is_declined(
    damage, why, store, tmp_path, worktree
):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)
    result_file = paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json"
    if damage == "delete":
        result_file.unlink()
    elif damage == "not_json":
        result_file.write_text(NOT_JSON, encoding="utf-8")
    else:
        result_file.write_text(INVALID_RESULT, encoding="utf-8")
    lines = len(store.journal.read())

    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0) is None

    [warning] = _declines(runner)
    assert warning.startswith(f"phase 'explore': attempt 1 of run {RUN_ID} was not reused (")
    assert warning.endswith("); dispatching again")
    assert why in warning
    assert len(store.journal.read()) == lines
    assert len(launcher.calls) == 1


def test_a_gate_that_fails_now_declines(store, tmp_path, worktree):
    runner, _, _ = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())

    failing = _model_phase(lambda result: {"blocked": True})
    assert runner.adopt(failing, _context(worktree), source_run=RUN_ID, floor=0) is None

    [warning] = _declines(runner)
    assert warning.startswith(f"phase 'explore': attempt 1 of run {RUN_ID} was not reused (")
    assert "gate '<lambda>' failed: blocked=True" in warning
    assert len(store.journal.read()) == lines


def test_a_gate_that_raises_now_declines_rather_than_raising(store, tmp_path, worktree):
    # Review Focus 3: a fatal gate verdict is a decline, never an exception
    # out of the resume.
    runner, _, _ = _succeed_once(store, tmp_path, worktree)

    def output_gate(result):
        raise RuntimeError("broken now")

    adopted = runner.adopt(
        _model_phase(output_gate), _context(worktree), source_run=RUN_ID, floor=0
    )

    assert adopted is None
    [warning] = _declines(runner)
    assert "gate 'output_gate' raised RuntimeError: broken now" in warning


def test_adopt_reads_the_journal_not_the_projection(store, tmp_path, worktree):
    runner, _, phase = _succeed_once(store, tmp_path, worktree)
    store.connection.execute("DELETE FROM attempts")
    store.connection.commit()
    assert store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0

    adopted = runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0)

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)


def test_adopt_reads_another_runs_journal(store, tmp_path, worktree):
    _succeed_once(store, tmp_path, worktree)
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        launcher = FakeLauncher(results=[VALID_RESULT])
        runner, _ = _runner(other, launcher, tmp_path, worktree, run_id=OTHER_RUN_ID)
        adopted = runner.adopt(
            _passing_phase(), _context(worktree), source_run=RUN_ID, floor=0
        )
        phase_lines = _phase_statuses(other)
        attempt_lines = _attempt_statuses(other)
        copied = other.connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id = ?", (OTHER_RUN_ID,)
        ).fetchone()[0]
    finally:
        other.close()

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)
    assert adopted.source_run == RUN_ID
    assert launcher.calls == []
    assert phase_lines == [("explore", "done")]
    assert attempt_lines == []
    assert copied == 0
    assert runner.warnings == [_reused(1)]


def test_adopt_finds_the_card_under_any_story_of_the_source_run(store, tmp_path, worktree):
    # Review Focus 2: the source run's story structure is not assumed to match.
    _succeed_once(store, tmp_path, worktree, story_id="5f0c1a2e")
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        runner, _ = _runner(
            other, FakeLauncher(results=[VALID_RESULT]), tmp_path, worktree,
            run_id=OTHER_RUN_ID,
        )
        adopted = runner.adopt(
            _passing_phase(), _context(worktree), source_run=RUN_ID, floor=0
        )
    finally:
        other.close()

    assert adopted == dispatch.Adopted(EXPLORED, 1, RUN_ID)


def test_a_phase_or_card_the_source_run_never_recorded_adopts_nothing_silently(
    store, tmp_path, worktree
):
    # Review Focus 2, the other half: nothing to adopt is not a decline.
    runner, _, phase = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())

    never_ran = _model_phase(lambda result: None, name="review")
    assert runner.adopt(never_ran, _context(worktree), source_run=RUN_ID, floor=0) is None

    stranger, _ = _runner(
        store, FakeLauncher(results=[VALID_RESULT]), tmp_path, worktree, card_id="0000aaaa"
    )
    assert stranger.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0) is None

    assert runner.warnings == []
    assert stranger.warnings == []
    assert len(store.journal.read()) == lines


def test_the_highest_ok_attempt_above_the_floor_is_adopted(store, tmp_path, worktree):
    # Review Focus 1.
    runner, launcher, phase = _succeed_once(
        store, tmp_path, worktree, results=(VALID_RESULT, OTHER_VALID_RESULT)
    )
    runner(phase, _context(worktree), _rendered())
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok"), (2, "started"), (2, "ok")]
    again = {"summary": "explored it again", "ok": True}

    assert runner.adopt(
        phase, _context(worktree), source_run=RUN_ID, floor=0
    ) == dispatch.Adopted(again, 2, RUN_ID)
    assert runner.adopt(
        phase, _context(worktree), source_run=RUN_ID, floor=1
    ) == dispatch.Adopted(again, 2, RUN_ID)
    assert runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=2) is None
    assert len(launcher.calls) == 2


def test_a_missing_source_journal_declines(store, tmp_path, worktree):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())

    assert runner.adopt(phase, _context(worktree), source_run="never-ran", floor=0) is None

    [warning] = _declines(runner)
    assert warning.startswith(
        "phase 'explore': attempt ? of run never-ran was not reused ("
        "its journal cannot be read: MissingJournalError: "
    )
    assert warning.endswith("); dispatching again")
    assert len(store.journal.read()) == lines
    assert len(launcher.calls) == 1
    # Review Focus 5: declining leaves no run directory for a run that never was.
    assert not (paths.data_dir() / "runs" / "never-ran").exists()


def test_a_phase_without_a_result_model_adopts_none(store, tmp_path, worktree):
    phase = _model_phase(
        name="spec", result=None, retry=None, writes="docs/superpowers/specs/{stem}.md"
    )
    runner, launcher, _ = _succeed_once(store, tmp_path, worktree, phase=phase, results=(None,))

    adopted = runner.adopt(phase, _context(worktree), source_run=RUN_ID, floor=0)

    assert adopted == dispatch.Adopted(None, 1, RUN_ID)
    assert adopted.result is None
    assert runner.warnings == [_reused(1, name="spec")]
    assert len(launcher.calls) == 1


def test_a_source_journal_that_fails_validation_declines(store, tmp_path, worktree):
    runner, launcher, phase = _succeed_once(store, tmp_path, worktree)
    lines = len(store.journal.read())
    broken = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        # Valid JSON, but no `models.Run`: replay raises pydantic's
        # ValidationError, which is a decline, never an exception out of resume.
        broken.journal.append("run_upsert", {"not": "a run"})
    finally:
        broken.close()

    assert runner.adopt(phase, _context(worktree), source_run=OTHER_RUN_ID, floor=0) is None

    [warning] = _declines(runner)
    assert warning.startswith(
        f"phase 'explore': attempt ? of run {OTHER_RUN_ID} was not reused ("
        "its journal cannot be read: ValidationError"
    )
    assert len(store.journal.read()) == lines
    assert len(launcher.calls) == 1


def test_an_adopted_phase_keeps_the_recorded_start(store, tmp_path, worktree):
    _succeed_once(store, tmp_path, worktree)
    recorded = store.replay_journal(RUN_ID).stories[0].subtasks[0].phases[0]
    later = recorded.started_at + timedelta(days=1)
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        runner, _ = _runner(
            other, FakeLauncher(results=[VALID_RESULT]), tmp_path, worktree,
            run_id=OTHER_RUN_ID, clock=lambda: later,
        )
        runner.adopt(_passing_phase(), _context(worktree), source_run=RUN_ID, floor=0)
        [payload] = [
            line.payload for line in other.journal.read() if line.event == "phase_upsert"
        ]
    finally:
        other.close()

    done = models.PhaseRun.model_validate(payload)
    assert done.status == "done"
    assert done.started_at == recorded.started_at
    assert done.ended_at == later
