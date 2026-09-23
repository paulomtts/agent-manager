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
