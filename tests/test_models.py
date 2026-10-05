"""Behaviour of the run state models (design spec §9).

These models are pure data: nothing here touches the filesystem, git, brd or a
harness, so every test is a plain construction/validation assertion in the
"pure functions" tier of spec §14 and runs in the default `uv run pytest` suite.

The assertions lean on the *messages* pydantic produces, not just on the fact
that it raised: the validation error is what a retrying agent reads, so a status
typo has to name the statuses that would have worked.
"""

import importlib
import os
import sys
import typing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

import agent_manager
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


def test_dispatch_carries_an_explicit_timeout():
    # §8 line 315: a Dispatch carries the prompt, the role, the cwd, the result
    # path, the model *and a timeout*. Seconds, because that is what the
    # launcher hands subprocess.
    dispatch = models.Dispatch(
        harness="claude",
        model="opus",
        role="coder",
        cwd=Path("/repo/wt"),
        prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
        result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
        timeout=90.0,
    )
    assert dispatch.timeout == 90.0


def test_dispatch_defaults_its_timeout_so_older_journal_lines_still_load():
    # A journal line written before this field existed has no `timeout` key.
    # `extra="forbid"` protects against a dropped key; a *new* field has to
    # carry a default or every stored line stops loading.
    payload = _dispatch().model_dump(mode="json")
    del payload["timeout"]
    restored = models.Dispatch.model_validate(payload)
    assert restored.timeout == 1800.0


def test_dispatch_rejects_a_useless_timeout():
    # Zero or negative would kill the harness before it started; inf and nan
    # would sail past a plain lower bound and reach subprocess.wait().
    for bad in (0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ValidationError) as excinfo:
            models.Dispatch(
                harness="claude",
                model="opus",
                role="coder",
                cwd=Path("/repo/wt"),
                prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
                result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
                timeout=bad,
            )
        assert [error["loc"] for error in excinfo.value.errors()] == [("timeout",)]


def test_attempt_in_flight_has_no_terminal_fields():
    # §9 resume: an attempt the crash caught mid-run is `started` with nothing
    # terminal recorded, and must load back so the engine can discard it.
    attempt = models.Attempt(n=1, dispatch=_dispatch())
    assert attempt.status == "started"
    assert attempt.exit_code is None
    assert attempt.duration is None
    assert attempt.stdout_path is None


def test_attempt_records_a_finished_outcome():
    attempt = models.Attempt(
        n=2,
        dispatch=_dispatch(),
        status="ok",
        exit_code=0,
        duration=12.5,
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
    # Both field names are substrings of pydantic's boilerplate ("validation",
    # "Input"), so match the error locations, not the rendered message.
    assert {error["loc"] for error in excinfo.value.errors()} == {("n",), ("dispatch",)}


def test_attempt_rejects_a_non_numeric_n():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n="first", dispatch=_dispatch())
    assert [error["loc"] for error in excinfo.value.errors()] == [("n",)]


def test_attempt_rejects_a_bool_n():
    # `True` is an int in Python; an attempt numbered True is an upstream bug,
    # not attempt 1.
    with pytest.raises(ValidationError):
        models.Attempt(n=True, dispatch=_dispatch())


def test_attempt_numbers_start_at_one():
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            models.Attempt(n=bad, dispatch=_dispatch())


def test_attempt_rejects_a_negative_duration():
    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n=1, dispatch=_dispatch(), duration=-5.0)
    assert [error["loc"] for error in excinfo.value.errors()] == [("duration",)]


def test_attempt_rejects_a_nan_or_infinite_duration():
    # `inf >= 0` is true and every nan comparison is false, so a bad clock
    # reading would sail past a plain lower bound.
    for bad in (float("nan"), float("inf")):
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


def test_attempt_carries_exactly_eight_fields_and_no_usage():
    # Remove-cost-tracking §5.3 item 3: nothing ever populated tokens or cost,
    # so the fields are gone rather than left defaulted to None.
    assert set(models.Attempt.model_fields) == {
        "n",
        "dispatch",
        "status",
        "exit_code",
        "duration",
        "prompt_path",
        "result_path",
        "stdout_path",
    }


@pytest.mark.parametrize("retired", ["tokens_in", "tokens_out", "cost"])
def test_attempt_itself_still_forbids_a_retired_usage_key(retired):
    # Old journal lines are reconciled in `store.replay`, never by loosening
    # the model: validating an Attempt payload with a retired key still fails.
    payload = models.Attempt(n=1, dispatch=_dispatch()).model_dump(mode="json")
    payload[retired] = None

    with pytest.raises(ValidationError) as excinfo:
        models.Attempt.model_validate(payload)

    assert [error["loc"] for error in excinfo.value.errors()] == [(retired,)]


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
    assert run.config.max_concurrent_stories == 4
    assert run.config.dry_run is False
    assert run.config.launcher == "direct"
    assert run.config.harness_map == {}


MILESTONE_ID = "9c44c2fb-0000-4000-8000-000000000000"


def test_a_run_without_a_milestone_id_defaults_to_none_and_a_given_one_round_trips():
    # S2: the milestone a run drives is recorded by full card id. It is
    # optional because task runs have none and journal lines from before the
    # field carry no such key, which `extra="forbid"` would otherwise reject.
    identity = dict(
        id="20260924T120000Z-9c44c2fb",
        workflow="milestone",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    assert models.Run(**identity).milestone_id is None

    stamped = models.Run(**identity, milestone_id=MILESTONE_ID)
    restored = models.Run.model_validate(stamped.model_dump(mode="json"))
    assert restored.milestone_id == MILESTONE_ID
    assert restored == stamped

    old_line = stamped.model_dump(mode="json", exclude={"stories", "milestone_id"})
    assert "milestone_id" not in old_line
    assert models.Run.model_validate(old_line).milestone_id is None


def test_run_requires_an_id():
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
        )
    # "id" is a substring of "validation", so assert on the error location.
    assert [error["loc"] for error in excinfo.value.errors()] == [("id",)]


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
    assert [error["loc"] for error in excinfo.value.errors()] == [("id",)]


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


def test_run_config_story_id_defaults_to_none():
    assert models.RunConfig().story_id is None
    run = models.Run(
        id="run-2026-09-23-01",
        workflow="task",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    assert run.config.story_id is None


def test_run_config_accepts_a_story_id():
    config = models.RunConfig(story_id="2aeb8b6e-b24f-4d4e-ab81-138f8d7dfbae")
    assert config.story_id == "2aeb8b6e-b24f-4d4e-ab81-138f8d7dfbae"
    assert config.model_dump(mode="json")["story_id"] == "2aeb8b6e-b24f-4d4e-ab81-138f8d7dfbae"
    assert models.RunConfig().model_dump(mode="json")["story_id"] is None


@pytest.mark.parametrize("value", [123, ["2aeb8b6e"]])
def test_run_config_rejects_a_non_string_story_id(value):
    with pytest.raises(ValidationError) as excinfo:
        models.RunConfig(story_id=value)
    assert "story_id" in str(excinfo.value)


def test_story_rejects_a_negative_level():
    with pytest.raises(ValidationError) as excinfo:
        models.StoryRun(card_id="8831189b", title="Foundations", level=-1)
    assert "level" in str(excinfo.value)


_IDENTITY_BASELINES = {
    "Dispatch": dict(
        harness="claude",
        model="opus",
        role="coder",
        cwd=Path("/repo/wt"),
        prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
        result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
    ),
    "PhaseRun": dict(name="implement", kind="agent"),
    "SubtaskRun": dict(card_id="1535b285", branch="m1/x-1535b285", base_branch="main"),
    "StoryRun": dict(card_id="8831189b", title="Foundations", level=0),
    "HarnessAssignment": dict(harness="claude", model="sonnet"),
    "Run": dict(
        id="run-2026-09-23-01",
        workflow="task",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    ),
}

_EMPTY_IDENTITY_FIELDS = [
    ("Dispatch", "harness"),
    ("Dispatch", "model"),
    ("Dispatch", "role"),
    ("PhaseRun", "name"),
    ("SubtaskRun", "card_id"),
    ("SubtaskRun", "branch"),
    ("SubtaskRun", "base_branch"),
    ("StoryRun", "card_id"),
    ("HarnessAssignment", "harness"),
    ("HarnessAssignment", "model"),
    ("Run", "id"),
    ("Run", "workflow"),
    ("Run", "base_branch"),
    ("Run", "branch_prefix"),
]


@pytest.mark.parametrize(
    ("model_name", "field"),
    _EMPTY_IDENTITY_FIELDS,
    ids=[f"{model}.{field}" for model, field in _EMPTY_IDENTITY_FIELDS],
)
def test_empty_identity_strings_are_rejected_everywhere(model_name, field):
    # Every string that names a thing -- a card, a branch, a phase, a harness --
    # is a coordinate something downstream resolves. An empty one loses the
    # coordinate exactly as a missing one does, only later and more quietly, so
    # each of these fields carries `min_length=1` and each one is pinned here.
    model = getattr(models, model_name)
    with pytest.raises(ValidationError) as excinfo:
        model(**{**_IDENTITY_BASELINES[model_name], field: ""})
    assert [error["loc"] for error in excinfo.value.errors()] == [(field,)]


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


def test_full_run_round_trips_through_json():
    # D5: the DB is a projection, the journal is truth. Rebuilding the
    # projection must not lose a field on the way through JSON.
    run = _full_run()
    assert models.Run.model_validate(run.model_dump(mode="json")) == run


def test_round_trip_survives_a_naive_started_at():
    # A timestamp written without an offset must come back as the same instant,
    # not shifted and not rejected.
    naive = datetime(2026, 9, 23, 10, 0)
    run = _full_run().model_copy(update={"started_at": naive})
    restored = models.Run.model_validate(run.model_dump(mode="json"))
    assert restored.started_at == naive
    assert restored == run


def test_round_trip_keeps_an_in_flight_attempt_in_flight():
    run = _full_run()
    restored = models.Run.model_validate(run.model_dump(mode="json"))
    attempt = restored.stories[0].subtasks[1].phases[1].attempts[0]
    assert attempt.status == "started"
    assert attempt.exit_code is None
    assert attempt.duration is None


def test_journal_coordinates_are_reachable_from_the_tree():
    # A journal line carries run id, card, phase, attempt and a sequence number;
    # the first four must locate a node here (the sequence number lives only in
    # the journal).
    run = _full_run()
    subtask = run.stories[0].subtasks[1]
    phase = subtask.phases[1]
    attempt = phase.attempts[0]
    assert (run.id, subtask.card_id, phase.name, attempt.n) == (
        "run-2026-09-23-01",
        "1535b285",
        "implement",
        1,
    )


def test_unknown_fields_are_rejected_at_every_level():
    with pytest.raises(ValidationError) as excinfo:
        models.Run(
            id="run-2026-09-23-01",
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m1/",
            sequence=7,
        )
    assert "sequence" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        models.Attempt(n=1, dispatch=_dispatch(), tokens=10)
    assert "tokens" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        models.PhaseRun(name="verify", kind="deterministic", finished_at=None)
    assert "finished_at" in str(excinfo.value)


def test_nested_collection_defaults_are_per_instance():
    first = models.Run(
        id="run-1",
        workflow="task",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    second = models.Run(
        id="run-2",
        workflow="task",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    first.stories.append(
        models.StoryRun(card_id="8831189b", title="Foundations", level=0)
    )
    first.config.harness_map["coder"] = models.HarnessAssignment(
        harness="claude", model="sonnet"
    )
    assert second.stories == []
    assert second.config.harness_map == {}
    assert first.config is not second.config


def test_importing_models_needs_no_environment_and_no_disk():
    # Pure data module: no env reads, no sqlite, no paths.py, so a fresh import
    # with an empty environment must still succeed.
    with mock.patch.dict(os.environ, {}, clear=True):
        sys.modules.pop("agent_manager.models", None)
        try:
            fresh = importlib.import_module("agent_manager.models")
            assert fresh.Run.__name__ == "Run"
            assert not hasattr(fresh, "os")
            assert not hasattr(fresh, "sqlite3")
            assert not hasattr(fresh, "paths")
        finally:
            sys.modules["agent_manager.models"] = models
            agent_manager.models = models


def test_card_parses_a_brd_show_payload_and_ignores_unknown_fields():
    # `brd show`'s data carries blocked_by/children/timestamps too. A brd schema
    # addition must not break a running milestone, so unknown keys are ignored
    # rather than forbidden -- the opposite of the _Model journal types above.
    card = models.Card.model_validate(
        {
            "id": "141c96e6-4a08-4905-b7a6-c0c993d1c20d",
            "title": "Add the brd board adapter",
            "description": "the only caller of brd",
            "status": "todo",
            "parent_id": "492ac463-8d23-4699-847c-31dc0aebd80f",
            "created_at": "2026-09-23T10:00:00+00:00",
            "updated_at": "2026-09-23T10:00:00+00:00",
            "blocked_by": [],
            "children": [],
        }
    )
    assert card.id == "141c96e6-4a08-4905-b7a6-c0c993d1c20d"
    assert card.title == "Add the brd board adapter"
    assert card.status == "todo"
    assert card.parent_id == "492ac463-8d23-4699-847c-31dc0aebd80f"
    assert card.description == "the only caller of brd"


def test_card_status_is_an_open_string_not_the_run_lifecycle_literal():
    # Board statuses are brd's to define (todo/in_progress/done/blocked and
    # whatever it adds next); `Status` here is the *run* lifecycle and is a
    # different vocabulary.
    for board_status in ("todo", "in_progress", "done", "blocked", "on_hold"):
        assert models.Card(id="c1", title="t", status=board_status).status == (
            board_status
        )


def test_card_defaults_a_top_level_card_to_no_parent():
    card = models.Card(id="352e955b", title="Milestone 1", status="todo")
    assert card.parent_id is None
    assert card.description is None


def test_card_requires_id_title_and_status():
    with pytest.raises(ValidationError) as excinfo:
        models.Card.model_validate({"description": "no identity at all"})
    assert {error["loc"] for error in excinfo.value.errors()} == {
        ("id",),
        ("title",),
        ("status",),
    }


def test_card_rejects_empty_id_and_status():
    with pytest.raises(ValidationError) as excinfo:
        models.Card(id="", title="t", status="todo")
    assert [error["loc"] for error in excinfo.value.errors()] == [("id",)]

    with pytest.raises(ValidationError) as excinfo:
        models.Card(id="c1", title="t", status="")
    assert [error["loc"] for error in excinfo.value.errors()] == [("status",)]


def test_card_node_nests_milestone_story_subtask_depth():
    node = models.CardNode.model_validate(
        {
            "id": "352e955b",
            "title": "Milestone 1",
            "description": None,
            "status": "in_progress",
            "blocked_by": [],
            "created_at": "2026-09-23T10:00:00+00:00",
            "updated_at": "2026-09-23T10:00:00+00:00",
            "children": [
                {
                    "id": "492ac463",
                    "title": "Naming and the brd board adapter",
                    "description": None,
                    "status": "in_progress",
                    "blocked_by": [],
                    "children": [
                        {
                            "id": "141c96e6",
                            "title": "Add the brd board adapter",
                            "description": None,
                            "status": "todo",
                            "blocked_by": [],
                            "children": [],
                        }
                    ],
                }
            ],
        }
    )
    assert node.id == "352e955b"
    assert [child.id for child in node.children] == ["492ac463"]
    assert [grandchild.id for grandchild in node.children[0].children] == ["141c96e6"]
    assert node.children[0].children[0].title == "Add the brd board adapter"


def test_card_node_has_no_parent_id_field():
    # brd's tree nodes carry no parent_id; nesting under `children` is what
    # preserves the structure. An accidental parent_id field would be fiction.
    assert "parent_id" not in models.CardNode.model_fields
    assert models.CardNode(id="c1", title="t", status="todo").children == []


def test_card_node_children_default_is_per_instance():
    first = models.CardNode(id="c1", title="t", status="todo")
    second = models.CardNode(id="c2", title="t", status="todo")
    first.children.append(models.CardNode(id="c3", title="t", status="todo"))
    assert second.children == []


def test_phase_carries_the_reason_it_failed():
    assert models.PhaseRun(name="verify", kind="deterministic").detail is None
    failed = models.PhaseRun(
        name="verify", kind="deterministic", status="failed", detail="OSError: gone"
    )
    assert failed.detail == "OSError: gone"
    assert failed.model_dump(mode="json")["detail"] == "OSError: gone"


_BRD_MODELS = (models.Card, models.CardNode)


@pytest.mark.parametrize("model", _BRD_MODELS, ids=["Card", "CardNode"])
def test_brd_models_default_blocked_by_to_empty_and_created_at_to_none(model):
    # Existing callers build cards without these keys; the defaults keep them working.
    instance = model(id="c1", title="t", status="todo")
    assert instance.blocked_by == []
    assert instance.created_at is None


@pytest.mark.parametrize("model", _BRD_MODELS, ids=["Card", "CardNode"])
def test_brd_models_blocked_by_default_is_per_instance(model):
    first = model(id="c1", title="t", status="todo")
    second = model(id="c2", title="t", status="todo")
    first.blocked_by.append("c9")
    assert second.blocked_by == []


def test_card_parses_blocked_by_and_created_at_from_a_brd_show_payload():
    card = models.Card.model_validate(
        {
            "id": "c1",
            "title": "t",
            "status": "blocked",
            "parent_id": "p1",
            "description": None,
            "blocked_by": ["b1", "b2"],
            "created_at": "2026-09-23T10:00:00+00:00",
            "updated_at": "2026-09-23T11:00:00+00:00",
        }
    )
    assert card.blocked_by == ["b1", "b2"]
    # brd's ISO string, kept as a string: not parsed into a datetime.
    assert card.created_at == "2026-09-23T10:00:00+00:00"
    assert isinstance(card.created_at, str)


def test_card_node_parses_blocked_by_and_created_at_at_every_depth():
    node = models.CardNode.model_validate(
        {
            "id": "m1",
            "title": "Milestone 1",
            "status": "todo",
            "blocked_by": [],
            "created_at": "2026-09-23T10:00:00+00:00",
            "children": [
                {
                    "id": "s1",
                    "title": "story",
                    "status": "blocked",
                    "blocked_by": ["s0"],
                    "created_at": "2026-09-23T10:01:00+00:00",
                    "children": [
                        {
                            "id": "t1",
                            "title": "subtask",
                            "status": "blocked",
                            "blocked_by": ["t0", "x9"],
                            "created_at": "2026-09-23T10:02:00+00:00",
                            "children": [],
                        }
                    ],
                }
            ],
        }
    )
    assert node.blocked_by == []
    assert node.created_at == "2026-09-23T10:00:00+00:00"
    story = node.children[0]
    assert story.blocked_by == ["s0"]
    assert story.created_at == "2026-09-23T10:01:00+00:00"
    subtask = story.children[0]
    assert subtask.blocked_by == ["t0", "x9"]
    assert subtask.created_at == "2026-09-23T10:02:00+00:00"


@pytest.mark.parametrize("model", _BRD_MODELS, ids=["Card", "CardNode"])
def test_brd_models_still_ignore_unknown_keys_alongside_the_new_fields(model):
    instance = model.model_validate(
        {
            "id": "c1",
            "title": "t",
            "status": "todo",
            "blocked_by": ["b1"],
            "created_at": "2026-09-23T10:00:00+00:00",
            "updated_at": "2026-09-23T11:00:00+00:00",
            "assignee": "paulo",
        }
    )
    assert instance.blocked_by == ["b1"]
    assert instance.created_at == "2026-09-23T10:00:00+00:00"
    assert not hasattr(instance, "assignee")
    assert "updated_at" not in instance.model_dump()
    assert "assignee" not in instance.model_dump()


def test_every_status_carrying_model_accepts_stopped():
    assert models.PhaseRun(name="implement", kind="agent", status="stopped").status == "stopped"
    assert (
        models.SubtaskRun(
            card_id="a3dd82f4", branch="m4/x-a3dd82f4", base_branch="main", status="stopped"
        ).status
        == "stopped"
    )
    assert (
        models.StoryRun(card_id="9bfb5ac2", title="Stop cleanly", level=0, status="stopped").status
        == "stopped"
    )
    assert (
        models.Run(
            id="run-2026-09-24-01",
            workflow="task",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m4/",
            status="stopped",
        ).status
        == "stopped"
    )


def test_status_is_exactly_the_lifecycle_set_and_still_rejects_unknown_values():
    assert typing.get_args(models.Status) == (
        "pending",
        "started",
        "done",
        "failed",
        "escalated",
        "stopped",
        "cancelled",
        "canceled",
    )
    with pytest.raises(ValidationError) as excinfo:
        models.SubtaskRun(
            card_id="a3dd82f4", branch="m4/x-a3dd82f4", base_branch="main", status="halted"
        )
    message = str(excinfo.value)
    for allowed in (
        "pending",
        "started",
        "done",
        "failed",
        "escalated",
        "stopped",
        "cancelled",
        "canceled",
    ):
        assert f"'{allowed}'" in message


def test_attempt_status_does_not_pick_up_stopped():
    with pytest.raises(ValidationError):
        models.Attempt(n=1, dispatch=_dispatch(), status="stopped")


def test_canceled_constants():
    assert models.CANCELED == "canceled"
    assert models.LEGACY_CANCELED == "cancelled"
    assert type(models.CANCELED) is str
    assert type(models.LEGACY_CANCELED) is str
    assert models.CANCELED_STATUSES == frozenset({"canceled", "cancelled"})
    assert type(models.CANCELED_STATUSES) is frozenset


@pytest.mark.parametrize("status", ["canceled", "cancelled"])
def test_is_canceled_true(status):
    assert models.is_canceled(status) is True


@pytest.mark.parametrize(
    "status",
    [
        None,
        "",
        "pending",
        "started",
        "done",
        "failed",
        "escalated",
        "stopped",
        "Canceled",
        "CANCELLED",
        " canceled",
        "canceled ",
    ],
)
def test_is_canceled_false(status):
    assert models.is_canceled(status) is False


@pytest.mark.parametrize("name", ["CANCELED", "LEGACY_CANCELED"])
def test_status_accepts_both_cancel_spellings(name):
    status = getattr(models, name)
    phase = models.PhaseRun(name="implement", kind="agent", status=status)
    subtask = models.SubtaskRun(
        card_id="a3dd82f4",
        branch="m9/x-a3dd82f4",
        base_branch="main",
        status=status,
        phases=[phase],
    )
    story = models.StoryRun(
        card_id="9bfb5ac2", title="Cancel", level=0, status=status, subtasks=[subtask]
    )
    run = models.Run(
        id="run-2026-09-29-01",
        workflow="milestone",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m9/",
        status=status,
        stories=[story],
    )
    assert phase.status == status
    assert subtask.status == status
    assert story.status == status
    assert run.status == status

    reloaded = models.Run.model_validate_json(run.model_dump_json())
    assert reloaded.status == status
    assert reloaded.stories[0].status == status
    assert reloaded.stories[0].subtasks[0].status == status
    assert reloaded.stories[0].subtasks[0].phases[0].status == status


@pytest.mark.parametrize("status", ["canceled", "cancelled"])
def test_attempt_status_does_not_pick_up_cancelled(status):
    assert typing.get_args(models.AttemptStatus) == (
        "started",
        "ok",
        "schema_invalid",
        "gate_failed",
        "harness_error",
    )
    with pytest.raises(ValidationError):
        models.Attempt(n=1, dispatch=_dispatch(), status=status)
