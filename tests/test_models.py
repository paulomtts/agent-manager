"""Behaviour of the run state models (design spec §9).

These models are pure data: nothing here touches the filesystem, git, brd or a
harness, so every test is a plain construction/validation assertion in the
"pure functions" tier of spec §14 and runs in the default `uv run pytest` suite.

The assertions lean on the *messages* pydantic produces, not just on the fact
that it raised: the validation error is what a retrying agent reads, so a status
typo has to name the statuses that would have worked.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_manager import models


def _dispatch() -> models.Dispatch:
    return models.Dispatch(
        harness="claude",
        model="opus",
        role="coder",
        cwd=Path("/repo/.claude/worktrees/m1/task-add-the-run-state-models-1535b285"),
        prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
        result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
    )


def test_dispatch_carries_the_six_launch_coordinates():
    dispatch = _dispatch()
    assert dispatch.harness == "claude"
    assert dispatch.model == "opus"
    assert dispatch.role == "coder"
    assert dispatch.cwd.name == "task-add-the-run-state-models-1535b285"
    assert dispatch.prompt_path.name == "prompt.txt"
    assert dispatch.result_path.name == "result.json"


def test_dispatch_requires_harness_and_role():
    with pytest.raises(ValidationError) as excinfo:
        models.Dispatch(
            model="opus",
            cwd=Path("/repo/wt"),
            prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
            result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
        )
    message = str(excinfo.value)
    assert "harness" in message
    assert "role" in message


def test_attempt_in_flight_has_no_terminal_fields():
    # §9 resume: an attempt the crash caught mid-run is `started` with nothing
    # terminal recorded, and must load back so the engine can discard it.
    attempt = models.Attempt(n=1, dispatch=_dispatch())
    assert attempt.status == "started"
    assert attempt.exit_code is None
    assert attempt.duration is None
    assert attempt.tokens_in is None
    assert attempt.tokens_out is None
    assert attempt.cost is None
    assert attempt.stdout_path is None


def test_attempt_records_a_finished_outcome():
    attempt = models.Attempt(
        n=2,
        dispatch=_dispatch(),
        status="ok",
        exit_code=0,
        duration=12.5,
        tokens_in=1200,
        tokens_out=340,
        cost=0.42,
        prompt_path=Path("/runs/run-1/1535b285/implement.2/prompt.txt"),
        result_path=Path("/runs/run-1/1535b285/implement.2/result.json"),
        stdout_path=Path("/runs/run-1/1535b285/implement.2/stdout.log"),
    )
    assert attempt.n == 2
    assert attempt.status == "ok"
    assert attempt.stdout_path.name == "stdout.log"


def test_attempt_requires_n_and_dispatch():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt()
    message = str(excinfo.value)
    assert "n" in message
    assert "dispatch" in message


def test_attempt_rejects_a_non_numeric_n():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n="first", dispatch=_dispatch())
    assert "n" in str(excinfo.value)


def test_attempt_rejects_a_bool_n():
    # `True` is an int in Python; an attempt numbered True is an upstream bug,
    # not attempt 1.
    with pytest.raises(ValidationError):
        models.Attempt(n=True, dispatch=_dispatch())


def test_attempt_numbers_start_at_one():
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            models.Attempt(n=bad, dispatch=_dispatch())


def test_attempt_rejects_negative_usage_numbers():
    for field, value in (
        ("cost", -0.01),
        ("duration", -5.0),
        ("tokens_in", -1),
        ("tokens_out", -1),
    ):
        with pytest.raises(ValidationError):
            models.Attempt(n=1, dispatch=_dispatch(), **{field: value})


def test_attempt_rejects_nan_and_infinite_usage_numbers():
    # `inf >= 0` is true and every nan comparison is false, so a bad usage parse
    # would sail past a plain lower bound.
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            models.Attempt(n=1, dispatch=_dispatch(), cost=bad)
        with pytest.raises(ValidationError):
            models.Attempt(n=1, dispatch=_dispatch(), duration=bad)


def test_attempt_keeps_a_signal_exit_code():
    # A harness killed on timeout exits -9. That is data, not a validation error.
    attempt = models.Attempt(
        n=1, dispatch=_dispatch(), status="harness_error", exit_code=-9
    )
    assert attempt.exit_code == -9


def test_attempt_rejects_an_unknown_status_naming_the_allowed_set():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n=1, dispatch=_dispatch(), status="finished")
    message = str(excinfo.value)
    for allowed in ("started", "ok", "schema_invalid", "gate_failed", "harness_error"):
        assert allowed in message
