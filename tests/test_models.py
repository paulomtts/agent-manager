"""Behaviour of the run state models (design spec §9).

These models are pure data: nothing here touches the filesystem, git, brd or a
harness, so every test is a plain construction/validation assertion in the
"pure functions" tier of spec §14 and runs in the default `uv run pytest` suite.

The assertions lean on the *messages* pydantic produces, not just on the fact
that it raised: the validation error is what a retrying agent reads, so a status
typo has to name the statuses that would have worked.
"""

from datetime import datetime, timezone
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


def test_phase_in_flight_has_no_end_time():
    phase = models.PhaseRun(
        name="implement",
        kind="agent",
        status="started",
        started_at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        attempts=[models.Attempt(n=1, dispatch=_dispatch())],
    )
    assert phase.ended_at is None
    assert phase.attempts[0].status == "started"
    assert phase.attempts[0].exit_code is None


def test_phase_defaults_to_pending_with_no_attempts():
    phase = models.PhaseRun(name="verify", kind="deterministic")
    assert phase.status == "pending"
    assert phase.started_at is None
    assert phase.ended_at is None
    assert phase.attempts == []


def test_phase_rejects_an_unknown_status_naming_the_allowed_set():
    with pytest.raises(ValidationError) as excinfo:
        models.PhaseRun(name="implement", kind="agent", status="in_flight")
    message = str(excinfo.value)
    for allowed in ("pending", "started", "done", "failed", "escalated"):
        assert allowed in message


def test_phase_rejects_an_unknown_kind():
    with pytest.raises(ValidationError) as excinfo:
        models.PhaseRun(name="implement", kind="wizard")
    message = str(excinfo.value)
    assert "agent" in message
    assert "deterministic" in message


def test_phase_requires_a_name():
    with pytest.raises(ValidationError) as excinfo:
        models.PhaseRun(kind="agent")
    assert "name" in str(excinfo.value)


def test_subtask_holds_its_phases_in_order():
    subtask = models.SubtaskRun(
        card_id="1535b285",
        branch="m1/task-add-the-run-state-models-1535b285",
        base_branch="m1/task-add-paths-and-the-run-fdebc746",
        worktree_path=Path("/repo/.claude/worktrees/m1/task-add-the-run-state-models-1535b285"),
        status="started",
        phases=[
            models.PhaseRun(name="explore", kind="agent", status="done"),
            models.PhaseRun(name="implement", kind="agent", status="started"),
            models.PhaseRun(name="verify", kind="deterministic"),
        ],
    )
    assert [phase.name for phase in subtask.phases] == [
        "explore",
        "implement",
        "verify",
    ]
    assert subtask.base_branch == "m1/task-add-paths-and-the-run-fdebc746"


def test_subtask_defaults_to_pending_with_no_worktree_yet():
    subtask = models.SubtaskRun(
        card_id="1535b285",
        branch="m1/task-add-the-run-state-models-1535b285",
        base_branch="main",
    )
    assert subtask.status == "pending"
    assert subtask.worktree_path is None
    assert subtask.phases == []


def test_subtask_requires_a_card_id():
    with pytest.raises(ValidationError) as excinfo:
        models.SubtaskRun(branch="m1/x-1535b285", base_branch="main")
    assert "card_id" in str(excinfo.value)


def test_subtask_rejects_an_empty_card_id():
    # An empty id loses the journal coordinate just as thoroughly as a missing
    # one, and fails further downstream.
    with pytest.raises(ValidationError) as excinfo:
        models.SubtaskRun(card_id="", branch="m1/x-1535b285", base_branch="main")
    assert "card_id" in str(excinfo.value)


def test_minimal_run_needs_only_its_identity_fields():
    run = models.Run(
        id="run-2026-09-23-01",
        workflow="task",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    assert run.status == "pending"
    assert run.started_at is None
    assert run.stories == []
    assert run.config.max_concurrent_stories == 1
    assert run.config.dry_run is False
    assert run.config.launcher == "direct"
    assert run.config.harness_map == {}


def test_run_requires_an_id():
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
        )
    assert "id" in str(excinfo.value)


def test_run_rejects_an_empty_id():
    # The run id is the first coordinate on every journal line; "" is not one.
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            id="",
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
        )
    assert "id" in str(excinfo.value)


def test_run_rejects_a_non_list_stories():
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            id="run-2026-09-23-01",
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
            stories="8831189b",
        )
    assert "stories" in str(excinfo.value)


def test_run_config_accepts_a_populated_harness_map():
    config = models.RunConfig(
        max_concurrent_stories=3,
        dry_run=True,
        launcher="bwrap",
        harness_map={
            "coder": models.HarnessAssignment(harness="claude", model="sonnet"),
            "reviewer": {"harness": "claude", "model": "opus"},
        },
    )
    assert config.max_concurrent_stories == 3
    assert config.dry_run is True
    assert config.launcher == "bwrap"
    assert config.harness_map["coder"].model == "sonnet"
    assert config.harness_map["reviewer"].harness == "claude"


def test_run_config_rejects_zero_concurrency():
    with pytest.raises(ValidationError) as excinfo:
        models.RunConfig(max_concurrent_stories=0)
    assert "max_concurrent_stories" in str(excinfo.value)


def test_run_config_rejects_an_unknown_launcher():
    with pytest.raises(ValidationError) as excinfo:
        models.RunConfig(launcher="docker")
    message = str(excinfo.value)
    for allowed in ("direct", "bwrap", "container"):
        assert allowed in message


def test_run_config_rejects_a_harness_map_entry_missing_its_model():
    with pytest.raises(ValidationError) as excinfo:
        models.RunConfig(harness_map={"coder": {"harness": "claude"}})
    assert "model" in str(excinfo.value)


def test_story_rejects_a_negative_level():
    with pytest.raises(ValidationError) as excinfo:
        models.StoryRun(card_id="8831189b", title="Foundations", level=-1)
    assert "level" in str(excinfo.value)


def _full_run() -> models.Run:
    """A run mid-flight: one story, one finished subtask, one in progress."""
    return models.Run(
        id="run-2026-09-23-01",
        workflow="milestone",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
        status="started",
        started_at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        config=models.RunConfig(
            max_concurrent_stories=2,
            harness_map={
                "coder": models.HarnessAssignment(harness="claude", model="sonnet")
            },
        ),
        stories=[
            models.StoryRun(
                card_id="8831189b",
                title="Foundations: paths, run store and journal",
                level=0,
                status="started",
                tip_branch="m1/task-add-paths-and-the-run-fdebc746",
                subtasks=[
                    models.SubtaskRun(
                        card_id="fdebc746",
                        branch="m1/task-add-paths-and-the-run-fdebc746",
                        base_branch="main",
                        worktree_path=Path("/repo/.claude/worktrees/m1/task-add-paths-and-the-run-fdebc746"),
                        status="done",
                        phases=[
                            models.PhaseRun(
                                name="verify",
                                kind="deterministic",
                                status="done",
                                started_at=datetime(2026, 9, 23, 10, 5, tzinfo=timezone.utc),
                                ended_at=datetime(2026, 9, 23, 10, 6, tzinfo=timezone.utc),
                            )
                        ],
                    ),
                    models.SubtaskRun(
                        card_id="1535b285",
                        branch="m1/task-add-the-run-state-models-1535b285",
                        base_branch="m1/task-add-paths-and-the-run-fdebc746",
                        worktree_path=Path("/repo/.claude/worktrees/m1/task-add-the-run-state-models-1535b285"),
                        status="started",
                        phases=[
                            models.PhaseRun(
                                name="explore",
                                kind="agent",
                                status="done",
                                started_at=datetime(2026, 9, 23, 10, 10, tzinfo=timezone.utc),
                                ended_at=datetime(2026, 9, 23, 10, 12, tzinfo=timezone.utc),
                                attempts=[
                                    models.Attempt(
                                        n=1,
                                        dispatch=_dispatch(),
                                        status="ok",
                                        exit_code=0,
                                        duration=31.25,
                                        tokens_in=8000,
                                        tokens_out=1500,
                                        cost=0.31,
                                        prompt_path=Path("/runs/run-1/1535b285/explore.1/prompt.txt"),
                                        result_path=Path("/runs/run-1/1535b285/explore.1/result.json"),
                                        stdout_path=Path("/runs/run-1/1535b285/explore.1/stdout.log"),
                                    )
                                ],
                            ),
                            models.PhaseRun(
                                name="implement",
                                kind="agent",
                                status="started",
                                started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
                                attempts=[models.Attempt(n=1, dispatch=_dispatch())],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def test_full_tree_preserves_nesting_and_subtask_order():
    run = _full_run()
    assert [story.card_id for story in run.stories] == ["8831189b"]
    story = run.stories[0]
    assert story.level == 0
    assert [subtask.card_id for subtask in story.subtasks] == ["fdebc746", "1535b285"]
    in_progress = story.subtasks[1]
    assert [phase.name for phase in in_progress.phases] == ["explore", "implement"]
    assert in_progress.phases[1].attempts[0].dispatch.role == "coder"
    assert in_progress.phases[1].ended_at is None
