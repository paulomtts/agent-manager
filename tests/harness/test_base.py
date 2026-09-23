"""Behaviour of the harness adapter protocol and its data types (design §8
lines 306-318, card 55e503e0).

Unit tier per design §14 line 484 ("Adapters -- `build_command` is pure and
asserted per harness; the launcher is injected, so no harness is executed in
unit tests"). Nothing here spawns a process or touches disk: the module is a
Protocol and two value types. The stub adapter below is the interface's only
consumer in this card -- the real adapters are sibling cards, and the fake
adapter that returns canned result files belongs to the engine card (§14
line 486).
"""

import dataclasses
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_manager.harness import base
from agent_manager.models import Dispatch


class StubAdapter:
    """The smallest thing that is a `HarnessAdapter`. Structural only: it
    inherits from nothing."""

    name = "stub"
    capabilities = frozenset({"browser"})

    def build_command(self, d: Dispatch) -> list[str]:
        return [self.name, "--model", d.model, "--result", str(d.result_path)]

    def parse_usage(self, stdout: str) -> base.Usage | None:
        return None


def _dispatch() -> Dispatch:
    return Dispatch(
        harness="stub",
        model="opus",
        role="coder",
        cwd=Path("/repo/wt"),
        prompt_path=Path("/runs/run-1/55e503e0/implement.1/prompt.txt"),
        result_path=Path("/runs/run-1/55e503e0/implement.1/result.json"),
        timeout=60.0,
    )


def test_the_protocol_declares_exactly_the_four_members_the_spec_prints():
    # §8 lines 306-313 are the contract every adapter card codes against. A
    # fifth member added here, or a rename, would silently break a sibling
    # adapter written against the printed version.
    assert set(base.HarnessAdapter.__protocol_attrs__) == {
        "name",
        "capabilities",
        "build_command",
        "parse_usage",
    }


def test_the_protocol_methods_have_the_signatures_the_spec_prints():
    # `base.py` has no `from __future__ import annotations`, so these come back
    # evaluated rather than as strings. That is deliberate: the objects are
    # what a sibling adapter card has to match.
    build = inspect.signature(base.HarnessAdapter.build_command)
    assert list(build.parameters) == ["self", "d"]
    assert build.parameters["d"].annotation is Dispatch
    assert build.return_annotation == list[str]

    parse = inspect.signature(base.HarnessAdapter.parse_usage)
    assert list(parse.parameters) == ["self", "stdout"]
    assert parse.parameters["stdout"].annotation is str
    assert parse.return_annotation == base.Usage | None


def test_a_structural_stub_satisfies_the_adapter_interface():
    adapter: base.HarnessAdapter = StubAdapter()
    # The annotation above is for the type-checker only -- a local annotation
    # is never evaluated at run time -- so conformance is asserted here, or
    # this test would only be exercising the stub it defines itself.
    for member in base.HarnessAdapter.__protocol_attrs__:
        assert hasattr(adapter, member), member
    assert adapter.name == "stub"
    assert adapter.capabilities == frozenset({"browser"})
    argv = adapter.build_command(_dispatch())
    assert argv == [
        "stub",
        "--model",
        "opus",
        "--result",
        "/runs/run-1/55e503e0/implement.1/result.json",
    ]
    # §5 line 252: an argv list, never a shell string.
    assert all(isinstance(word, str) for word in argv)
    assert adapter.parse_usage("no usage in this log") is None


def test_the_protocol_is_not_runtime_checkable():
    # Deliberate: structural isinstance() checks are not a goal, and a
    # runtime_checkable Protocol only checks member *presence*, which would
    # read as a guarantee it cannot give.
    with pytest.raises(TypeError):
        isinstance(StubAdapter(), base.HarnessAdapter)


def test_usage_round_trips_a_full_payload():
    usage = base.Usage(tokens_in=8000, tokens_out=1500, cost=0.31)
    assert usage.tokens_in == 8000
    assert usage.tokens_out == 1500
    assert usage.cost == 0.31
    assert base.Usage.model_validate(usage.model_dump(mode="json")) == usage


def test_usage_is_empty_when_a_harness_reports_nothing():
    # D4: stdout is a log, not a channel. A harness that prints no usage line
    # still ran fine, so every field is optional.
    usage = base.Usage()
    assert usage.tokens_in is None
    assert usage.tokens_out is None
    assert usage.cost is None


def test_usage_fields_match_the_attempt_fields_they_are_copied_into():
    # The engine's journalling is a straight copy; a rename here would make it
    # a translation nobody wrote.
    assert set(base.Usage.model_fields) == {"tokens_in", "tokens_out", "cost"}


def test_usage_is_frozen():
    usage = base.Usage(tokens_in=10)
    with pytest.raises(ValidationError):
        usage.tokens_in = 20


def test_usage_rejects_negative_counts_and_cost():
    for field, value in (("tokens_in", -1), ("tokens_out", -1), ("cost", -0.01)):
        with pytest.raises(ValidationError) as excinfo:
            base.Usage(**{field: value})
        assert [error["loc"] for error in excinfo.value.errors()] == [(field,)]


def test_usage_rejects_an_infinite_or_nan_cost():
    # A garbled stdout line can parse to inf or nan. `inf >= 0` is true and
    # every nan comparison is false, so a plain lower bound would let both
    # through into the journal.
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            base.Usage(cost=bad)


def test_usage_rejects_an_unknown_key():
    # A harness that renames its usage field must fail loudly, not report zero.
    with pytest.raises(ValidationError) as excinfo:
        base.Usage(total_tokens=9500)
    assert "total_tokens" in str(excinfo.value)


def test_outcome_carries_what_the_engine_classifies_on():
    outcome = base.Outcome(
        argv=["claude", "-p"],
        exit_code=0,
        timed_out=False,
        duration=12.5,
        stdout_path=Path("/runs/run-1/55e503e0/implement.1/stdout.log"),
    )
    assert outcome.argv == ["claude", "-p"]
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert outcome.duration == 12.5
    assert outcome.stdout_path.name == "stdout.log"


def test_a_timed_out_outcome_has_no_exit_code():
    # §6 line 278: timeout is one of the three harness_error triggers, and a
    # killed process has no exit status of its own to report.
    outcome = base.Outcome(
        argv=["claude", "-p"],
        exit_code=None,
        timed_out=True,
        duration=1800.0,
        stdout_path=Path("/runs/run-1/55e503e0/implement.1/stdout.log"),
    )
    assert outcome.timed_out is True
    assert outcome.exit_code is None


def test_outcome_is_frozen():
    outcome = base.Outcome(
        argv=["claude"],
        exit_code=1,
        timed_out=False,
        duration=0.5,
        stdout_path=Path("/tmp/stdout.log"),
    )
    assert dataclasses.is_dataclass(outcome)
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.exit_code = 0


def test_outcome_carries_no_result_payload_and_no_usage():
    # The launcher never opens the result file and never parses usage; keeping
    # both off this type is what lets one launcher serve every adapter.
    fields = {field.name for field in dataclasses.fields(base.Outcome)}
    assert fields == {"argv", "exit_code", "timed_out", "duration", "stdout_path"}
