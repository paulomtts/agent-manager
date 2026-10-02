"""Behaviour of the `run`, `status`, `runs`, `logs` and `resume` commands (§10).

Two tiers live here, per design §14 lines 477-492 and the spec's Tests section:

- the pure helpers (`render`, `mint_run_id`, `worktree_for`, `resolve_repo_dir`)
  are unit tests -- no clock, no filesystem beyond `tmp_path`, no subprocess;
- `run_card` and the Typer command are **Engine tier**: a fake
  `engine.AgentPhaseRunner` returning canned results, on **Steps-tier fixtures**
  (a real temporary git repo, a real temporary brd board, and `XDG_DATA_HOME`
  pointed at `tmp_path` so `paths.data_dir()` never touches the developer's own).
  No harness process is ever launched: the real `dispatch.AgentRunner` and its
  launcher are never constructed.
"""

import asyncio
import inspect
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import agent_manager
from agent_manager import (
    board,
    census,
    cli,
    control,
    dag,
    dispatch,
    integration,
    locks,
    models,
    orchestrate,
    paths,
    prompt,
    store as store_module,
)
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.walk import SubtaskSummary
from agent_manager.steps.reducers import verification_gate

from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow import task as task_workflow


def test_render_is_one_line_of_json_by_default():
    text = cli.render({"ok": True, "data": {"status": "done"}})
    assert "\n" not in text
    assert json.loads(text) == {"ok": True, "data": {"status": "done"}}


def test_render_indents_under_pretty():
    text = cli.render({"ok": True, "data": {"status": "done"}}, pretty=True)
    assert "\n" in text
    assert json.loads(text) == {"ok": True, "data": {"status": "done"}}


def test_ok_envelope_matches_brds_shape():
    assert cli.ok_envelope({"run_id": "r1"}) == {"ok": True, "data": {"run_id": "r1"}}


def test_error_envelope_carries_the_exception_class_name_and_message():
    envelope = cli.error_envelope(ValueError("not a card id: 'nope'"))
    assert envelope == {
        "ok": False,
        "error": {"type": "ValueError", "message": "not a card id: 'nope'"},
    }


def test_render_survives_a_path_in_the_payload():
    """`worktree` is a Path and `json.dumps` refuses one. A renderer that raised
    would turn a finished run into a traceback with no envelope at all."""
    text = cli.render(cli.ok_envelope({"worktree": Path("/repo/.claude/worktrees/m1/x")}))
    assert json.loads(text)["data"]["worktree"] == "/repo/.claude/worktrees/m1/x"


def test_mint_run_id_is_the_timestamp_and_the_cards_short_id():
    run_id = cli.mint_run_id(
        "cbe34d00-9d8d-4f41-9c94-f99e665771b0",
        datetime(2026, 9, 23, 14, 5, 6, tzinfo=timezone.utc),
    )
    assert run_id == "20260923T140506Z-cbe34d00"


def test_mint_run_id_refuses_something_that_is_not_a_card_id():
    with pytest.raises(ValueError):
        cli.mint_run_id("not-a-uuid", datetime(2026, 9, 23, tzinfo=timezone.utc))


def test_worktree_for_is_absolute_and_under_dot_claude_worktrees():
    worktree = cli.worktree_for(Path("/repo"), "m1/task-add-run-card-cbe34d00")
    assert worktree == Path("/repo/.claude/worktrees/m1/task-add-run-card-cbe34d00")
    assert worktree.is_absolute()


def test_resolve_repo_dir_returns_an_absolute_path(tmp_path, monkeypatch):
    """`steps/worktree.ensure` refuses a relative path outright, so the CLI has
    to resolve `--repo-dir` -- whose default is `.` -- before deriving anything."""
    monkeypatch.chdir(tmp_path)
    resolved = cli.resolve_repo_dir(Path("."))
    assert resolved.is_absolute()
    assert resolved == tmp_path.resolve()


def test_resolve_repo_dir_refuses_a_path_that_is_not_a_directory(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(cli.RepoDirError) as caught:
        cli.resolve_repo_dir(missing)
    assert "nope" in str(caught.value)


def _pure_dispatch() -> models.Dispatch:
    """A dispatch built by hand: these tests touch no filesystem at all."""
    return models.Dispatch(
        harness="claude",
        model="sonnet",
        role="coder",
        cwd=Path("/repo"),
        prompt_path=Path("/runs/prompt.txt"),
        result_path=Path("/runs/result.json"),
    )


def _pure_run(stories: list[models.StoryRun]) -> models.Run:
    return models.Run(
        id="20260923T140506Z-cbe34d00",
        workflow="task",
        repo_dir=Path("/repo"),
        base_branch="main",
        branch_prefix="m1",
        status="started",
        started_at=datetime(2026, 9, 23, 14, 5, 6, tzinfo=timezone.utc),
        stories=stories,
    )


def test_status_rows_are_one_row_per_attempt_in_tree_order():
    run = _pure_run(
        [
            models.StoryRun(
                card_id="story-1",
                title="One",
                level=0,
                status="done",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-1",
                        branch="m1/a",
                        base_branch="main",
                        status="done",
                        phases=[
                            models.PhaseRun(
                                name="explore",
                                kind="agent",
                                status="done",
                                attempts=[
                                    models.Attempt(
                                        n=1, dispatch=_pure_dispatch(), status="gate_failed"
                                    ),
                                    models.Attempt(n=2, dispatch=_pure_dispatch(), status="ok"),
                                ],
                            )
                        ],
                    )
                ],
            ),
            models.StoryRun(
                card_id="story-2",
                title="Two",
                level=1,
                status="started",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-2",
                        branch="m1/b",
                        base_branch="main",
                        status="started",
                        phases=[
                            models.PhaseRun(
                                name="implement",
                                kind="agent",
                                status="started",
                                attempts=[
                                    models.Attempt(
                                        n=1, dispatch=_pure_dispatch(), status="started"
                                    )
                                ],
                            )
                        ],
                    )
                ],
            ),
        ]
    )

    assert cli.status_rows(run) == [
        {
            "story": "story-1",
            "subtask": "card-1",
            "phase": "explore",
            "attempt": 1,
            "state": "gate_failed",
        },
        {
            "story": "story-1",
            "subtask": "card-1",
            "phase": "explore",
            "attempt": 2,
            "state": "ok",
        },
        {
            "story": "story-2",
            "subtask": "card-2",
            "phase": "implement",
            "attempt": 1,
            "state": "started",
        },
    ]


def test_status_rows_show_a_phase_that_has_no_attempts_yet():
    """A pending or in-flight deterministic phase has no attempt row to hang off,
    and a table that dropped it would hide exactly the phase an operator running
    `status` is asking about."""
    run = _pure_run(
        [
            models.StoryRun(
                card_id="story-1",
                title="One",
                level=0,
                status="started",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-1",
                        branch="m1/a",
                        base_branch="main",
                        status="started",
                        phases=[
                            models.PhaseRun(
                                name="verify", kind="deterministic", status="pending"
                            )
                        ],
                    )
                ],
            )
        ]
    )

    assert cli.status_rows(run) == [
        {
            "story": "story-1",
            "subtask": "card-1",
            "phase": "verify",
            "attempt": None,
            "state": "pending",
        }
    ]


def test_status_rows_of_a_run_with_no_stories_are_empty():
    assert cli.status_rows(_pure_run([])) == []


def test_the_status_payload_survives_render_with_its_paths():
    """`repo_dir` and `worktree_path` are `Path`s and `started_at` is a
    `datetime`; `json.dumps` refuses all three. A renderer that raised would turn
    a successful read into a traceback with no envelope at all."""
    run = _pure_run(
        [
            models.StoryRun(
                card_id="story-1",
                title="One",
                level=0,
                status="done",
                tip_branch="m1/a",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-1",
                        branch="m1/a",
                        base_branch="main",
                        status="done",
                        worktree_path=Path("/repo/.claude/worktrees/m1/a"),
                    )
                ],
            )
        ]
    )

    data = json.loads(cli.render(cli.ok_envelope(cli.status_payload(run))))["data"]

    assert data["run"]["id"] == "20260923T140506Z-cbe34d00"
    assert data["run"]["repo_dir"] == "/repo"
    assert "2026-09-23" in data["run"]["started_at"]
    assert data["stories"][0]["subtasks"][0]["worktree_path"] == "/repo/.claude/worktrees/m1/a"
    assert data["rows"] == []


def test_the_status_header_is_the_runs_identity_and_not_its_config():
    """The spec's seven identity fields, and `config` is not one of them: the
    header is what `runs` prints for the same run, and a workflow's whole config
    blob in it would drown the reading and let the two commands disagree."""
    run = _pure_run([])
    run.config = models.RunConfig(max_concurrent_stories=4, dry_run=True)

    payload = cli.status_payload(run)

    assert set(payload["run"]) == {
        "id",
        "workflow",
        "repo_dir",
        "base_branch",
        "branch_prefix",
        "status",
        "started_at",
    }


def _pure_subtask(card_id: str, phases: list[models.PhaseRun]) -> models.SubtaskRun:
    """A subtask carrying hand-built phases: the `logs` selection tests touch no disk."""
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m1/{card_id}",
        base_branch="main",
        status="started",
        phases=phases,
    )


def _pure_story(card_id: str, subtasks: list[models.SubtaskRun]) -> models.StoryRun:
    return models.StoryRun(
        card_id=card_id, title=card_id, level=0, status="started", subtasks=subtasks
    )


def test_find_subtask_looks_past_the_first_story():
    """`SubtaskRun` has no back-reference to its story, so the lookup returns the
    pair: `story_id` in the payload has nowhere else to come from."""
    wanted = _pure_subtask("card-2", [])
    run = _pure_run(
        [
            _pure_story("story-1", [_pure_subtask("card-1", [])]),
            _pure_story("story-2", [wanted]),
        ]
    )

    found = cli.find_subtask(run, "card-2")

    assert found is not None
    story, subtask = found
    assert story.card_id == "story-2"
    assert subtask is wanted


def test_find_subtask_returns_none_for_a_card_that_is_not_in_the_tree():
    run = _pure_run([_pure_story("story-1", [_pure_subtask("card-1", [])])])

    assert cli.find_subtask(run, "card-9") is None


def test_find_subtask_takes_the_first_match_when_a_card_id_is_duplicated():
    """A card id appears once per run in everything this program writes, so a
    duplicate means the projection is corrupt -- and `logs` must still answer the
    same way every time rather than picking arbitrarily."""
    first = _pure_subtask("card-1", [])
    second = _pure_subtask("card-1", [])
    run = _pure_run([_pure_story("story-1", [first]), _pure_story("story-2", [second])])

    found = cli.find_subtask(run, "card-1")

    assert found is not None
    story, subtask = found
    assert story.card_id == "story-1"
    assert subtask is first


def test_not_resumable_is_a_cli_error_and_rides_the_handled_tuple():
    """A refusal `resume` raises has to reach the operator as an envelope, and
    `HANDLED` is the only thing that turns an exception into one."""
    assert issubclass(cli.NotResumableError, cli.CliError)
    assert isinstance(cli.NotResumableError("nothing in flight"), cli.HANDLED)


def test_select_resumable_returns_the_single_started_subtask():
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    started = _pure_subtask("card-2", [])
    run = _pure_run([_pure_story("story-1", [done]), _pure_story("story-2", [started])])

    story, subtask = cli.select_resumable(run)

    assert story.card_id == "story-2"
    assert subtask is started


def test_select_resumable_refuses_a_run_with_nothing_in_flight():
    """A `done` run has nothing to pick up, and the message has to name the
    status found: that is what tells an operator to start a fresh run rather
    than to go looking for a lost process."""
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    run = _pure_run([_pure_story("story-1", [done])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    assert "card-1=done" in str(caught.value)
    assert "agent-manager status" in str(caught.value)


def test_select_resumable_refuses_an_escalated_subtask_by_name():
    """An escalation is a full stop a human reads (§12). Re-running it is
    `retry`'s job, not this command's, so the status is named rather than
    silently resumed."""
    escalated = _pure_subtask("card-1", []).model_copy(update={"status": "escalated"})
    run = _pure_run([_pure_story("story-1", [escalated])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    assert "card-1=escalated" in str(caught.value)


def test_select_resumable_refuses_more_than_one_subtask_in_flight():
    """Review Focus: a milestone-shaped run reaching a command that drives one
    subtask. Picking one arbitrarily would leave the others recorded `started`
    forever with nothing driving them."""
    first = _pure_subtask("card-1", [])
    second = _pure_subtask("card-2", [])
    run = _pure_run([_pure_story("story-1", [first, second])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    message = str(caught.value)
    assert "card-1" in message
    assert "card-2" in message


AGENT_PHASE_NAMES = frozenset(
    {"explore", "spec", "validate_spec", "plan", "validate_plan", "implement", "review"}
)
"""Which phases of `TASK` are `kind: agent`. `PhaseRun.kind` is a
Literal, so a hand-built phase has to name the right one."""


def _recorded(name: str, status: str, attempts: list[models.Attempt] | None = None):
    """One `PhaseRun` of the task workflow as the projection would hold it."""
    return models.PhaseRun(
        name=name,
        kind="agent" if name in AGENT_PHASE_NAMES else "deterministic",
        status=status,
        attempts=list(attempts or []),
    )


def test_orphan_attempts_are_exactly_the_ones_recorded_started():
    """`started` with no terminal event is §9's in-flight attempt. A
    `gate_failed` or `harness_error` attempt is finished history and must not be
    rewritten."""
    orphan_spec = _pure_attempt(1, status="started")
    orphan_implement = _pure_attempt(2, status="started")
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded(
                "explore",
                "done",
                [_pure_attempt(1, status="gate_failed"), _pure_attempt(2)],
            ),
            _recorded("spec", "started", [orphan_spec]),
            _recorded(
                "implement",
                "started",
                [_pure_attempt(1, status="harness_error"), orphan_implement],
            ),
        ],
    )

    assert cli.orphan_attempts(subtask) == [
        (subtask.phases[1], orphan_spec),
        (subtask.phases[2], orphan_implement),
    ]


def _pure_attempt(n: int, status: str = "ok") -> models.Attempt:
    return models.Attempt(n=n, dispatch=_pure_dispatch(), status=status)


def test_select_attempt_defaults_to_the_last_phase_with_attempts_and_its_highest_n():
    subtask = _pure_subtask(
        "card-1",
        [
            models.PhaseRun(
                name="explore", kind="agent", status="done", attempts=[_pure_attempt(1)]
            ),
            models.PhaseRun(
                name="implement",
                kind="agent",
                status="done",
                attempts=[_pure_attempt(1, "gate_failed"), _pure_attempt(2)],
            ),
        ],
    )

    phase, attempt = cli.select_attempt(subtask)

    assert phase.name == "implement"
    assert attempt.n == 2


def test_select_attempt_skips_a_trailing_phase_that_has_no_attempts():
    """A trailing `pending` phase has no artifacts to print, so defaulting to it
    would make the no-flag common case §10 names useless."""
    subtask = _pure_subtask(
        "card-1",
        [
            models.PhaseRun(
                name="implement", kind="agent", status="done", attempts=[_pure_attempt(1)]
            ),
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        ],
    )

    phase, attempt = cli.select_attempt(subtask)

    assert phase.name == "implement"
    assert attempt.n == 1


def _two_phase_subtask() -> models.SubtaskRun:
    return _pure_subtask(
        "card-1",
        [
            models.PhaseRun(
                name="explore",
                kind="agent",
                status="done",
                attempts=[_pure_attempt(1, "gate_failed"), _pure_attempt(2)],
            ),
            models.PhaseRun(
                name="implement", kind="agent", status="done", attempts=[_pure_attempt(1)]
            ),
        ],
    )


def test_an_explicit_phase_wins_over_a_later_phase_that_also_has_attempts():
    phase, attempt = cli.select_attempt(_two_phase_subtask(), phase="explore")

    assert phase.name == "explore"
    assert attempt.n == 2


def test_an_explicit_attempt_selects_that_n_and_not_the_highest():
    phase, attempt = cli.select_attempt(_two_phase_subtask(), phase="explore", attempt=1)

    assert phase.name == "explore"
    assert attempt.n == 1
    assert attempt.status == "gate_failed"


def test_an_unknown_phase_name_is_refused_and_names_the_phases_that_exist():
    with pytest.raises(cli.UnknownPhaseError) as caught:
        cli.select_attempt(_two_phase_subtask(), phase="reveiw")

    message = str(caught.value)
    assert "reveiw" in message
    assert "explore" in message
    assert "implement" in message


def test_an_unknown_attempt_number_is_refused_and_names_the_attempts_that_exist():
    with pytest.raises(cli.UnknownAttemptError) as caught:
        cli.select_attempt(_two_phase_subtask(), phase="implement", attempt=7)

    message = str(caught.value)
    assert "7" in message
    assert "implement" in message
    assert "recorded attempts: 1" in message


def test_an_empty_list_in_a_refusal_reads_as_none_rather_than_a_dangling_colon():
    """Both refusals end in a list, and both lists can be empty -- a card whose
    phases were never recorded, and an explicit `--phase --attempt` pair aimed at
    a phase with nothing in it. The word beats a trailing `: `."""
    with pytest.raises(cli.UnknownPhaseError) as no_phases:
        cli.select_attempt(_pure_subtask("card-1", []), phase="explore")

    assert str(no_phases.value).endswith("recorded phases: none")

    subtask = _pure_subtask(
        "card-1", [models.PhaseRun(name="verify", kind="deterministic", status="pending")]
    )
    with pytest.raises(cli.UnknownAttemptError) as no_attempts:
        cli.select_attempt(subtask, phase="verify", attempt=1)

    assert str(no_attempts.value).endswith("recorded attempts: none")


def test_an_explicit_phase_with_no_attempts_is_the_attempt_refusal():
    """`--phase verify` on a pending deterministic phase is an operator typing a
    real phase name. Without its own guard the default branch would reach `max()`
    over an empty list and surface a bare `ValueError` instead of the refusal
    that names the phase."""
    subtask = _pure_subtask(
        "card-1",
        [
            models.PhaseRun(
                name="implement", kind="agent", status="done", attempts=[_pure_attempt(1)]
            ),
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        ],
    )

    with pytest.raises(cli.UnknownAttemptError) as caught:
        cli.select_attempt(subtask, phase="verify")

    message = str(caught.value)
    assert "verify" in message
    assert "card-1" in message


def test_the_default_attempt_is_the_highest_n_not_the_last_recorded():
    """Ordering by `n` is `load_run`'s promise, not the model's, and this function
    is also called with trees built by hand -- so the selection is `max`, and a
    tree whose attempts arrive out of order still reports the newest one."""
    subtask = _pure_subtask(
        "card-1",
        [
            models.PhaseRun(
                name="implement",
                kind="agent",
                status="done",
                attempts=[_pure_attempt(2), _pure_attempt(1, "gate_failed")],
            )
        ],
    )

    _, attempt = cli.select_attempt(subtask)

    assert attempt.n == 2


def test_a_card_with_no_attempts_at_all_is_the_attempt_refusal():
    """No `--phase` was given and there is nothing to default to. That is the same
    fact as a missing attempt number, worded for the operator who has not run
    anything yet."""
    subtask = _pure_subtask(
        "card-1",
        [
            models.PhaseRun(name="explore", kind="agent", status="pending"),
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        ],
    )

    with pytest.raises(cli.UnknownAttemptError) as caught:
        cli.select_attempt(subtask)

    assert "no attempt has been recorded" in str(caught.value)
    assert "card-1" in str(caught.value)


@pytest.mark.parametrize("n", [0, -1])
def test_a_non_positive_attempt_number_is_refused_rather_than_defaulting(n):
    """Typer will hand over any int, and `models.Attempt.n` is `gt=0`, so no such
    attempt can exist. `--attempt 0` must refuse, not quietly report the highest
    attempt as though no flag had been passed."""
    with pytest.raises(cli.UnknownAttemptError) as caught:
        cli.select_attempt(_two_phase_subtask(), phase="explore", attempt=n)

    assert str(n) in str(caught.value)


def _artifact_attempt(directory: Path, n: int = 1, **overrides) -> models.Attempt:
    """An attempt whose three recorded paths point into `directory`."""
    fields: dict[str, Any] = {
        "prompt_path": directory / "prompt.txt",
        "result_path": directory / "result.json",
        "stdout_path": directory / "stdout.log",
    }
    fields.update(overrides)
    return models.Attempt(
        n=n, dispatch=_pure_dispatch(), status="ok", exit_code=0, **fields
    )


def _payload_for(attempt: models.Attempt) -> dict[str, Any]:
    phase = models.PhaseRun(
        name="implement", kind="agent", status="done", attempts=[attempt]
    )
    subtask = _pure_subtask("card-1", [phase])
    story = _pure_story("story-1", [subtask])
    run = _pure_run([story])
    return cli.logs_payload(run, story, subtask, phase, attempt)


def test_the_payload_names_the_story_that_actually_owns_the_card(tmp_path):
    """`story_id` is the *owning* story, which is why `find_subtask` hands the
    pair back at all. A tree whose first story is not the match would otherwise
    report a story the card was never under."""
    attempt = _artifact_attempt(tmp_path)
    phase = models.PhaseRun(
        name="implement", kind="agent", status="done", attempts=[attempt]
    )
    subtask = _pure_subtask("card-2", [phase])
    owner = _pure_story("story-2", [subtask])
    run = _pure_run([_pure_story("story-1", [_pure_subtask("card-1", [])]), owner])

    payload = cli.logs_payload(run, owner, subtask, phase, attempt)

    assert payload["story_id"] == "story-2"
    assert payload["card"] == "card-2"


def test_the_payload_reads_the_three_artifacts_off_disk(tmp_path):
    (tmp_path / "prompt.txt").write_text("you are the coder\n", encoding="utf-8")
    (tmp_path / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (tmp_path / "stdout.log").write_text("line one\nline two\n", encoding="utf-8")

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["run_id"] == "20260923T140506Z-cbe34d00"
    assert payload["story_id"] == "story-1"
    assert payload["card"] == "card-1"
    assert payload["phase"] == "implement"
    assert payload["attempt"] == 1
    assert payload["status"] == "ok"
    assert payload["exit_code"] == 0
    assert payload["artifacts"]["prompt"] == {
        "path": tmp_path / "prompt.txt",
        "present": True,
        "text": "you are the coder\n",
    }
    assert payload["artifacts"]["result"]["text"] == '{"ok": true}'
    assert payload["artifacts"]["stdout"]["text"] == "line one\nline two\n"


def test_a_missing_file_and_a_null_path_are_both_absent_not_a_refusal(tmp_path):
    """`Attempt.prompt_path` and friends are `Path | None`, and a phase can die
    between recording an attempt and writing its files. Both are facts."""
    (tmp_path / "prompt.txt").write_text("you are the coder\n", encoding="utf-8")
    attempt = _artifact_attempt(tmp_path, result_path=None)

    payload = _payload_for(attempt)

    assert payload["artifacts"]["prompt"]["present"] is True
    assert payload["artifacts"]["result"] == {"path": None, "present": False, "text": None}
    assert payload["artifacts"]["stdout"] == {
        "path": tmp_path / "stdout.log",
        "present": False,
        "text": None,
    }


def test_an_empty_artifact_is_present_with_empty_text(tmp_path):
    """`""` is falsy and must not be conflated with the `None` of a missing file:
    a harness that produced no output at all is a different diagnosis from a
    harness that never got far enough to write the file."""
    (tmp_path / "stdout.log").write_text("", encoding="utf-8")

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["stdout"] == {
        "path": tmp_path / "stdout.log",
        "present": True,
        "text": "",
    }


def test_a_recorded_path_that_names_a_directory_reads_as_absent(tmp_path):
    """`exists()` would say yes and `read_text` would raise `IsADirectoryError`,
    turning a read-only report into a traceback."""
    (tmp_path / "stdout.log").mkdir()

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["stdout"]["present"] is False
    assert payload["artifacts"]["stdout"]["text"] is None


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a 0o000 file, so the failure cannot be staged"
)
def test_an_unreadable_artifact_reads_as_absent_rather_than_raising(tmp_path):
    """`is_file()` says yes and `read_text` then raises `PermissionError`, which
    is outside `HANDLED` -- so a single unreadable artifact would take a
    read-only report down as a traceback with no envelope at all."""
    unreadable = tmp_path / "stdout.log"
    unreadable.write_text("secret\n", encoding="utf-8")
    unreadable.chmod(0o000)

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["stdout"]["present"] is False
    assert payload["artifacts"]["stdout"]["text"] is None


def test_a_result_json_that_is_not_valid_json_comes_back_as_raw_text(tmp_path):
    """The malformed result is the one that made the phase fail, and it is exactly
    what an operator runs `logs` to read. `logs` must never parse it."""
    (tmp_path / "result.json").write_text('{"summary": "half a fi', encoding="utf-8")

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["result"]["present"] is True
    assert payload["artifacts"]["result"]["text"] == '{"summary": "half a fi'


def test_undecodable_bytes_in_an_artifact_are_replaced_not_raised(tmp_path):
    """A harness that dumped raw terminal output is not a reason for a read-only
    command to die with a `UnicodeDecodeError`."""
    (tmp_path / "stdout.log").write_bytes(b"ok \xff\xfe done\n")

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["stdout"]["present"] is True
    assert payload["artifacts"]["stdout"]["text"].startswith("ok ")
    assert payload["artifacts"]["stdout"]["text"].endswith(" done\n")
    assert "�" in payload["artifacts"]["stdout"]["text"]


def test_the_logs_payload_survives_render_with_its_paths_and_newlines(tmp_path):
    """The payload carries three `Path`s that `json.dumps` refuses, and artifact
    text full of newlines that the one-line default must escape rather than
    break."""
    (tmp_path / "prompt.txt").write_text("first\nsecond\n", encoding="utf-8")

    payload = _payload_for(_artifact_attempt(tmp_path))
    text = cli.render(cli.ok_envelope(payload))

    assert "\n" not in text
    data = json.loads(text)["data"]
    assert data["artifacts"]["prompt"]["path"] == str(tmp_path / "prompt.txt")
    assert data["artifacts"]["prompt"]["text"] == "first\nsecond\n"
    assert data["artifacts"]["result"]["path"] == str(tmp_path / "result.json")


def _plan_id(n: int) -> str:
    """A UUID-shaped card id whose short id is `n` in eight hex digits.

    `dag.subtask_branch` goes through `dag.short_id`, which refuses anything
    that is not 32 hex characters, so the pure plans need real-shaped ids.
    """
    return f"{n:08x}-0000-4000-8000-000000000000"


def _plan_subtask(n: int, status: str = "todo") -> census.SubtaskPlan:
    return census.SubtaskPlan(id=_plan_id(n), title=f"subtask {n}", status=status)


def _plan_story(
    n: int,
    subtasks: list[census.SubtaskPlan],
    *,
    status: str = "todo",
    blocked_by: tuple[str, ...] | list[str] = (),
) -> census.StoryPlan:
    return census.StoryPlan(
        id=_plan_id(n),
        title=f"story {n}",
        status=status,
        blocked_by=list(blocked_by),
        subtasks=list(subtasks),
    )


DRY_RUN_REPO = Path("/repo")
"""The repo dir the pure dry-run tests pass. `worktree_for` only joins onto it,
so it need not exist, and the payload is still computed without touching disk."""


def test_the_dry_run_payload_lists_remaining_subtasks_on_full_list_bases():
    """A done story is `already_done` and roots its dependent. A pending story
    lists only its remaining subtasks, and a done first subtask still anchors
    the second one's base."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="done")
    b = _plan_story(
        2,
        [_plan_subtask(21, "done"), _plan_subtask(22), _plan_subtask(23, "in_progress")],
        status="in_progress",
        blocked_by=[a.id],
    )

    payload = cli.dry_run_payload([a, b], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")

    def branch(subtask: census.SubtaskPlan) -> str:
        return dag.subtask_branch("m3", subtask)

    assert payload == {
        "max_concurrent": 4,
        "levels": [
            {
                "level": 0,
                "concurrent": 1,
                "stories": [
                    {
                        "story": b.id,
                        "title": "story 2",
                        "root": branch(a.subtasks[-1]),
                        "subtasks": [
                            {
                                "id": _plan_id(22),
                                "title": "subtask 22",
                                "status": "todo",
                                "branch": branch(b.subtasks[1]),
                                "base": branch(b.subtasks[0]),
                            },
                            {
                                "id": _plan_id(23),
                                "title": "subtask 23",
                                "status": "in_progress",
                                "branch": branch(b.subtasks[2]),
                                "base": branch(b.subtasks[1]),
                            },
                        ],
                    }
                ],
            }
        ],
        "already_done": [
            {"kind": "story", "id": a.id, "title": "story 1"},
            {"kind": "subtask", "id": _plan_id(21), "title": "subtask 21", "story": b.id},
        ],
        "integrate": {
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [
                {"story": a.id, "tip": branch(a.subtasks[-1])},
                {"story": b.id, "tip": branch(b.subtasks[-1])},
            ],
        },
    }


def test_the_dry_run_payload_keeps_census_order_across_and_within_levels():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id])

    payload = cli.dry_run_payload([a, b, c], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")

    assert [level["level"] for level in payload["levels"]] == [0, 1]
    assert [[story["story"] for story in level["stories"]] for level in payload["levels"]] == [
        [a.id, b.id],
        [c.id],
    ]
    assert payload["levels"][0]["stories"][0]["root"] == "main"
    assert payload["levels"][0]["stories"][1]["root"] == "main"
    assert payload["levels"][1]["stories"][0]["root"] == dag.subtask_branch("m3", a.subtasks[-1])


def test_the_dry_run_checks_for_blocker_cycles_before_any_geometry():
    """`compute_levels` would also refuse, but with a comma list. The arrow
    trail is `assert_no_blocker_cycles`'s, which proves it ran first."""
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    with pytest.raises(dag.DependencyCycleError) as caught:
        cli.dry_run_payload([a, b], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")

    assert f"#{a.id} -> #{b.id} -> #{a.id}" in str(caught.value)


def test_the_dry_run_roots_a_story_with_two_in_milestone_blockers_on_a_merged_base():
    """Not refused: the joined story's root is its own merged base branch and
    `merged_from` names its in-milestone blockers in `blocked_by` order, an
    outside id left out. Rows rooted on the base or on one tip have no
    `merged_from` key."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(
        3, [_plan_subtask(31), _plan_subtask(32)], blocked_by=[a.id, "outside", b.id]
    )
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[a.id])

    payload = cli.dry_run_payload(
        [a, b, c, d], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )

    rows = {row["story"]: row for level in payload["levels"] for row in level["stories"]}
    assert rows[c.id]["root"] == f"m3/base-{dag.short_id(c.id)}" == "m3/base-00000003"
    assert rows[c.id]["merged_from"] == [a.id, b.id]
    assert [row["base"] for row in rows[c.id]["subtasks"]] == [
        "m3/base-00000003",
        dag.subtask_branch("m3", c.subtasks[0]),
    ]
    assert rows[a.id]["root"] == rows[b.id]["root"] == "main"
    assert rows[d.id]["root"] == dag.subtask_branch("m3", a.subtasks[-1])
    for story in (a, b, d):
        assert "merged_from" not in rows[story.id]
    # The row survives the one-line JSON render untouched.
    rendered = json.loads(cli.render(cli.ok_envelope(payload)))["data"]
    assert rendered == payload


def test_a_milestone_with_nothing_left_has_no_levels_and_lists_every_story_as_done():
    """Review focus: a closed story, a story whose subtasks are all done, and a
    story with no subtasks at all each become one `kind: "story"` entry, in
    census order, with their subtasks not listed separately."""
    closed = _plan_story(1, [_plan_subtask(11)], status="done")
    finished = _plan_story(2, [_plan_subtask(21, "done"), _plan_subtask(22, "Done")])
    empty = _plan_story(3, [])

    payload = cli.dry_run_payload(
        [closed, finished, empty],
        repo_dir=DRY_RUN_REPO,
        branch_prefix="m3",
        base_branch="main",
    )

    assert payload == {
        "max_concurrent": 4,
        "levels": [],
        "already_done": [
            {"kind": "story", "id": closed.id, "title": "story 1"},
            {"kind": "story", "id": finished.id, "title": "story 2"},
            {"kind": "story", "id": empty.id, "title": "story 3"},
        ],
        "integrate": {
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [
                {"story": closed.id, "tip": dag.subtask_branch("m3", closed.subtasks[-1])},
                {"story": finished.id, "tip": dag.subtask_branch("m3", finished.subtasks[-1])},
            ],
        },
    }


def test_the_dry_run_payload_plans_integrate_over_every_story_in_integrate_order():
    """Spec test 7. Integrate covers every story, done or not, level by level
    in census order within a level, and a story with no subtasks has no tip of
    its own, so it is left out of the order."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    empty = _plan_story(3, [])
    done = _plan_story(4, [_plan_subtask(41, "done")], status="done")

    payload = cli.dry_run_payload(
        [b, a, empty, done], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )

    assert payload["integrate"] == {
        "branch": "m3-integrate",
        "worktree": "/repo/.claude/worktrees/m3-integrate",
        "order": [
            {"story": a.id, "tip": dag.subtask_branch("m3", a.subtasks[-1])},
            {"story": done.id, "tip": dag.subtask_branch("m3", done.subtasks[-1])},
            {"story": b.id, "tip": dag.subtask_branch("m3", b.subtasks[-1])},
        ],
    }


def _three_then_one() -> list[census.StoryPlan]:
    """Level 0 holds stories 1, 2 and 3; level 1 holds story 4, blocked by 1."""
    return [
        _plan_story(1, [_plan_subtask(11)]),
        _plan_story(2, [_plan_subtask(21)]),
        _plan_story(3, [_plan_subtask(31)]),
        _plan_story(4, [_plan_subtask(41)], blocked_by=[_plan_id(1)]),
    ]


@pytest.mark.parametrize(
    "bound, concurrent", [(2, [2, 1]), (1, [1, 1]), (3, [3, 1]), (10, [3, 1])]
)
def test_the_dry_run_payload_reports_the_bound_and_each_levels_concurrency(
    bound, concurrent
):
    """A level runs `min(len(level), bound)` stories together; a bound larger
    than a level reports the level's size, not the bound."""
    payload = cli.dry_run_payload(
        _three_then_one(),
        repo_dir=DRY_RUN_REPO,
        branch_prefix="m3",
        base_branch="main",
        max_concurrent=bound,
    )

    assert payload["max_concurrent"] == bound
    assert [len(level["stories"]) for level in payload["levels"]] == [3, 1]
    assert [level["concurrent"] for level in payload["levels"]] == concurrent


def test_the_dry_run_payload_defaults_to_four_lanes():
    payload = cli.dry_run_payload(_three_then_one(), repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")

    assert payload["max_concurrent"] == 4
    assert [level["concurrent"] for level in payload["levels"]] == [3, 1]


def test_the_dry_run_payload_keeps_every_key_order():
    """Review focus: `--dry-run` output must stay byte-for-byte identical, and
    dict equality ignores key order, so each row type's key order is pinned
    here -- a merged-root row included, since it alone carries `merged_from`."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id, b.id])
    done = _plan_story(4, [_plan_subtask(41, "done")], status="done")
    partial = _plan_story(5, [_plan_subtask(51, "done"), _plan_subtask(52)])

    payload = cli.dry_run_payload(
        [a, b, c, done, partial], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )

    assert list(payload) == ["max_concurrent", "levels", "already_done", "integrate"]
    for level in payload["levels"]:
        assert list(level) == ["level", "concurrent", "stories"]
        for row in level["stories"]:
            expected = ["story", "title", "root", "subtasks"]
            if row["story"] == c.id:
                expected.append("merged_from")
            assert list(row) == expected
            for subtask in row["subtasks"]:
                assert list(subtask) == ["id", "title", "status", "branch", "base"]
    assert [list(entry) for entry in payload["already_done"]] == [
        ["kind", "id", "title"],
        ["kind", "id", "title", "story"],
    ]
    assert list(payload["integrate"]) == ["branch", "worktree", "order"]
    assert payload["integrate"]["order"]
    for entry in payload["integrate"]["order"]:
        assert list(entry) == ["story", "tip"]


def test_the_dry_run_payload_accepts_a_one_shot_iterator():
    """Review focus: the stories feed both the levels and `already_done`, so a
    generator must be read once and seen by both."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    done = _plan_story(3, [_plan_subtask(31, "done")], status="done")
    stories = [a, b, done]

    from_list = cli.dry_run_payload(
        stories, repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )
    from_iterator = cli.dry_run_payload(
        iter(stories), repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )

    assert from_iterator == from_list
    assert from_iterator["already_done"] != []
    assert [len(level["stories"]) for level in from_iterator["levels"]] == [1, 1]


def test_an_empty_census_gives_an_empty_dry_run_payload():
    """Review focus: a milestone with no stories at all is not an error."""
    payload = cli.dry_run_payload([], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")

    assert payload == {
        "max_concurrent": 4,
        "levels": [],
        "already_done": [],
        "integrate": {
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [],
        },
    }


def test_a_blocker_outside_the_milestone_roots_the_story_on_the_base_branch():
    """Review focus: one foreign blocker plus one in-milestone blocker is ONE
    in-milestone blocker, not a two-blocker refusal."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=["not-in-this-milestone"])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=["not-in-this-milestone", a.id])

    payload = cli.dry_run_payload([a, b, c], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main")

    roots = {
        story["story"]: story["root"]
        for level in payload["levels"]
        for story in level["stories"]
    }
    assert roots == {
        a.id: "main",
        b.id: "main",
        c.id: dag.subtask_branch("m3", a.subtasks[-1]),
    }


requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the CLI's steps-tier fixtures",
)
requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the CLI's steps-tier fixtures",
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)["data"]["id"]


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board.

    `--repo-dir` is both at once in production, so the fixture is too.
    XDG_DATA_HOME points into tmp_path, which isolates brd's own database *and*
    `paths.data_dir()`, so no run artifact can land in the developer's home.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True, text=True
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    # `brd init` leaves its own `.gitignore`/`.brd` marker untracked; committing
    # them here keeps the fixture's baseline clean so a later porcelain check
    # reflects only what `run_card` itself adds to the repo.
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "brd init")
    return root


@pytest.fixture
def cards(project) -> dict[str, str]:
    """A milestone -> story -> subtask chain, the shape `run --card` requires."""
    milestone = _add_card(project, "Milestone 1: walking skeleton")
    story = _add_card(project, "The CLI: run, status, logs, resume", milestone)
    subtask = _add_card(project, "Add run --card end to end", story)
    return {"milestone": milestone, "story": story, "subtask": subtask}


EXPLORE_RESULT = {
    "summary": "the CLI composes board, dag, store, loader and engine for one card",
    "verification": {"fullSuite": ["uv run pytest"]},
}


def _canned_agent_result(phase, context) -> dict[str, Any]:
    """The canned result a faked agent phase returns.

    The real Plan agent writes the plan file that the deterministic
    `mark_validated` phase then stamps, so the fake writes a stand-in there. The
    real `docs_commit` phase then commits the spec and the plan, so the fake
    writes a stand-in spec too.
    """
    if phase.name == "spec":
        spec = Path(context["worktree"]) / context["spec_path"]
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("# canned spec\n", encoding="utf-8")
    if phase.name == "plan":
        plan = Path(context["worktree"]) / context["plan_path"]
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text("# canned plan\n", encoding="utf-8")
    return {"phase": phase.name, "ok": True}


def fake_runner(seen: list[tuple[str, dict[str, Any]]] | None = None, fail: str | None = None):
    """An `engine.AgentPhaseRunner` that returns canned results and runs nothing.

    §14's Engine tier: the agent phases are faked at the seam `engine.run_subtask`
    already injects, so no attempt directory, no adapter and no launcher exist in
    these tests at all.
    """

    def runner(phase, context, rendered):
        if seen is not None:
            seen.append((phase.name, dict(context)))
        if fail is not None and phase.name == fail:
            raise AgentPhaseFailed(
                phase.name, outcome="gate_failed", detail="canned gate failure"
            )
        if phase.name == "explore":
            return dict(EXPLORE_RESULT)
        return _canned_agent_result(phase, context)

    return runner


@requires_git
@requires_brd
def test_run_card_drives_the_task_workflow_to_done(project, cards):
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["status"] == "done"
    assert payload["card_id"] == cards["subtask"]
    assert payload["story_id"] == cards["story"]
    assert payload["failed_phase"] is None
    assert payload["detail"] is None


@requires_git
@requires_brd
def test_run_card_really_moves_the_card_on_the_board(project, cards):
    """§12: a payload saying `done` while the card never moved is the failure
    this run is supposed to prevent. `mark_in_progress` and `mark_done` are
    `best_effort`, so a board write that never happened would be a warning at
    most -- the proof has to come from brd itself, not from the payload."""
    assert board.show(cards["subtask"], repo_dir=project).status == "todo"

    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["warnings"] == []
    assert board.show(cards["subtask"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_run_card_derives_its_branch_and_worktree_from_dag(project, cards):
    card = board.show(cards["subtask"], repo_dir=project)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m7",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["branch"] == dag.task_branch("m7", card)
    assert payload["base_branch"] == "main"
    assert Path(payload["worktree"]).is_absolute()
    assert Path(payload["worktree"]) == project.resolve() / ".claude" / "worktrees" / payload[
        "branch"
    ]
    assert Path(payload["worktree"]).is_dir()


@requires_git
@requires_brd
def test_a_relative_repo_dir_still_produces_an_absolute_worktree(project, cards, monkeypatch):
    """The option's default is `.`, and `worktree.ensure` refuses anything
    relative -- so the resolution has to happen in the CLI, not in the step."""
    monkeypatch.chdir(project)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=Path("."),
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )
    assert Path(payload["worktree"]).is_absolute()
    assert payload["status"] == "done"


@requires_git
@requires_brd
def test_run_card_hands_the_engine_the_gate_parameters_task_yaml_binds(project, cards):
    """§12's escape hatch is bound by name out of the engine's context, and
    `subtask_context` holds none of these four names."""
    seen: list[tuple[str, dict[str, Any]]] = []
    cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(seen),
    )

    _phase, context = seen[0]
    assert context["suite_cmds"] == []
    assert context["allow_no_verification"] is False
    assert context["caller_provided"] is False
    assert context["provided_verification"] is None


@requires_git
@requires_brd
def test_drive_subtask_drives_two_subtasks_under_one_store_and_run(project):
    """Addendum O4: the driver runs against a store and run id its caller already
    holds, so a milestone runner can drive every subtask of a story under one
    run. Two subtasks, one store, one run id -- and both must land `done`."""
    milestone = _add_card(project, "Milestone 3: orchestration")
    story_id = _add_card(project, "Run a milestone", milestone)
    first_id = _add_card(project, "First subtask", story_id)
    second_id = _add_card(project, "Second subtask", story_id)

    root = cli.resolve_repo_dir(project)
    parent = board.show(story_id, repo_dir=root)
    subtask_cards = [
        board.show(first_id, repo_dir=root),
        board.show(second_id, repo_dir=root),
    ]

    started_at = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    run_id = cli.mint_run_id(first_id, started_at)
    store = store_module.Store.open(root, run_id)
    try:
        store.record_run(
            models.Run(
                id=run_id,
                workflow=cli.WORKFLOW_NAME,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m3",
                status="started",
                started_at=started_at,
                config=models.RunConfig(),
            )
        )
        store.record_story(
            models.StoryRun(
                card_id=parent.id,
                title=parent.title,
                level=0,
                status="started",
                tip_branch=dag.task_branch("m3", subtask_cards[-1]),
            )
        )

        drives = []
        for card in subtask_cards:
            branch = dag.task_branch("m3", card)
            subtask = models.SubtaskRun(
                card_id=card.id,
                branch=branch,
                base_branch="main",
                status="started",
                worktree_path=cli.worktree_for(root, branch),
            )
            store.record_subtask(parent.id, subtask)
            drives.append(
                cli.drive_subtask(
                    store=store,
                    run_id=run_id,
                    card=card,
                    parent=parent,
                    subtask=subtask,
                    repo_dir=root,
                    runner_factory=lambda **kwargs: fake_runner(),
                )
            )

        run = store.load_run(run_id)
    finally:
        store.close()

    assert [drive.summary.status for drive in drives] == ["done", "done"]
    assert [drive.warnings for drive in drives] == [[], []]
    assert run is not None
    assert [story.card_id for story in run.stories] == [story_id]
    assert {sub.card_id: sub.status for sub in run.stories[0].subtasks} == {
        first_id: "done",
        second_id: "done",
    }


@requires_git
@requires_brd
def test_drive_subtask_async_hands_a_triggered_stop_to_the_engine(project, cards):
    """Addendum P4, on the one stop: the driver passes the run's `StopSignal`
    straight through. With the signal already triggered, the first phase of
    `TASK` never starts, so the fake runner is never called and no worktree is
    made."""
    root = cli.resolve_repo_dir(project)
    parent = board.show(cards["story"], repo_dir=root)
    card = board.show(cards["subtask"], repo_dir=root)

    started_at = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    run_id = cli.mint_run_id(card.id, started_at)
    branch = dag.task_branch("m1", card)
    subtask = models.SubtaskRun(
        card_id=card.id,
        branch=branch,
        base_branch="main",
        status="started",
        worktree_path=cli.worktree_for(root, branch),
    )
    seen: list[tuple[str, dict[str, Any]]] = []
    stop = StopSignal()
    stop.trigger(parent.id)
    store = store_module.Store.open(root, run_id)
    try:
        store.record_run(
            models.Run(
                id=run_id,
                workflow=cli.WORKFLOW_NAME,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=started_at,
                config=models.RunConfig(),
            )
        )
        store.record_story(
            models.StoryRun(
                card_id=parent.id,
                title=parent.title,
                level=0,
                status="started",
                tip_branch=branch,
            )
        )
        store.record_subtask(parent.id, subtask)

        drive = asyncio.run(
            cli.drive_subtask_async(
                store=store,
                run_id=run_id,
                card=card,
                parent=parent,
                subtask=subtask,
                repo_dir=root,
                runner_factory=lambda **kwargs: fake_runner(seen),
                stop=stop,
            )
        )
        run = store.load_run(run_id)
    finally:
        store.close()

    assert drive.summary.status == "stopped"
    assert drive.summary.failed_phase is None
    assert drive.summary.detail == "stopped before worktree"
    assert seen == []
    assert not subtask.worktree_path.exists()
    assert run is not None
    assert [sub.status for sub in run.stories[0].subtasks] == ["stopped"]


# ── drive_subtask's walk (card 7fdec762) ─────────────────────────────────────

DRIVE_CARD = models.Card(
    id="7fdec762-0000-4000-8000-000000000001",
    title="Select the engine with --engine",
    status="todo",
    parent_id="a6c7bff3-0000-4000-8000-000000000002",
)
DRIVE_PARENT = models.Card(
    id="a6c7bff3-0000-4000-8000-000000000002",
    title="Checkpoints and resume",
    status="in_progress",
)
DRIVE_RUN_ID = "20260926T000000Z-7fdec762"
DRIVE_REPO = Path("/repo")


def _drive_row() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=DRIVE_CARD.id,
        branch="m6/task-select-the-engine-7fdec762",
        base_branch="main",
        status="started",
        worktree_path=Path("/repo/.claude/worktrees/m6/task-select-the-engine-7fdec762"),
    )


def _record_walks(monkeypatch) -> list[tuple[Any, Any, dict[str, Any]]]:
    """Stub `runtime.engine.run_subtask_async`; every call is recorded.

    Patched on the module itself, so the stub is what `drive_subtask_async`
    (and `drive_subtask`, through it) reaches via its `runtime_engine` alias.
    """
    walks: list[tuple[Any, Any, dict[str, Any]]] = []

    async def run_subtask_async(workflow, store, **kwargs):
        walks.append((workflow, store, kwargs))
        return SubtaskSummary(status="done")

    monkeypatch.setattr(runtime_engine, "run_subtask_async", run_subtask_async)
    return walks


def _recording_factory(seen: list[dict[str, Any]]):
    """A runner factory that records its kwargs and hands back one opaque runner."""
    runner = object()

    def factory(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return runner

    return factory, runner


def test_drive_subtask_walks_task_with_the_same_arguments(monkeypatch):
    """Spec test 2: the walk is `runtime.engine.run_subtask_async` over `TASK`.
    The keywords are compared whole, so a `start_phase` or a `resume_from`
    sneaking into the call fails here. The sync driver has no `stop`, so it
    forwards `stop=None`."""
    walks = _record_walks(monkeypatch)
    seen: list[dict[str, Any]] = []
    factory, runner = _recording_factory(seen)
    store = object()
    subtask = _drive_row()

    drive = cli.drive_subtask(
        store=store,
        run_id=DRIVE_RUN_ID,
        card=DRIVE_CARD,
        parent=DRIVE_PARENT,
        subtask=subtask,
        repo_dir=DRIVE_REPO,
        commands=["uv run pytest"],
        runner_factory=factory,
    )

    ((workflow, passed_store, kwargs),) = walks
    assert passed_store is store
    assert workflow is task_workflow.TASK
    assert kwargs == {
        "story_id": DRIVE_PARENT.id,
        "subtask": subtask,
        "repo_dir": DRIVE_REPO,
        "commands": ["uv run pytest"],
        "card": DRIVE_CARD,
        "parent_story": DRIVE_PARENT,
        "extra_context": cli.gate_context(["uv run pytest"], False),
        "agent_runner": runner,
        "stop": None,
    }
    assert drive.summary.status == "done"
    assert drive.warnings == []
    (factory_call,) = seen
    assert factory_call == {
        "store": store,
        "run_id": DRIVE_RUN_ID,
        "story_id": DRIVE_PARENT.id,
        "card_id": DRIVE_CARD.id,
    }


DRIVER_KEYWORDS = [
    "store",
    "run_id",
    "card",
    "parent",
    "subtask",
    "repo_dir",
    "commands",
    "allow_no_verification",
    "runner_factory",
]


def test_the_drivers_take_a_stop_signal_and_no_other_stop():
    """T5: the `StopSignal` is the only stop. The sync driver takes none; a
    caller that must stop awaits `drive_subtask_async(stop=...)`. Any other
    keyword is a `TypeError`."""
    assert list(inspect.signature(cli.drive_subtask).parameters) == [
        *DRIVER_KEYWORDS,
        "resume_from",
    ]
    assert list(inspect.signature(cli.drive_subtask_async).parameters) == [
        *DRIVER_KEYWORDS,
        "stop",
        "resume_from",
    ]


# ── drive_subtask_async (card 9b944409) ──────────────────────────────────────


def test_drive_subtask_async_runs_inside_a_running_loop(monkeypatch):
    """T2: a supervisor lane awaits the driver on its own loop, so the driver
    must not open one. The sync form is the oracle, computed first with no loop
    running; the async form, awaited inside a running loop, must return the
    same drive and reach the engine on that caller's loop. The canned summary
    escalates and carries a warning, and the runner carries an out-of-band
    one, so the merge order is checked too."""
    loops: list[asyncio.AbstractEventLoop] = []

    async def run_subtask_async(workflow, store, **kwargs):
        loops.append(asyncio.get_running_loop())
        return SubtaskSummary(
            status="escalated",
            results={"explore": {"ok": True}},
            warnings=["summary warning"],
            failed_phase="validate_spec",
            detail="canned escalation",
        )

    monkeypatch.setattr(runtime_engine, "run_subtask_async", run_subtask_async)

    def factory(**kwargs: Any) -> Any:
        return SimpleNamespace(warnings=["runner warning"])

    drive_args: dict[str, Any] = {
        "store": object(),
        "run_id": DRIVE_RUN_ID,
        "card": DRIVE_CARD,
        "parent": DRIVE_PARENT,
        "subtask": _drive_row(),
        "repo_dir": DRIVE_REPO,
        "commands": ["uv run pytest"],
        "runner_factory": factory,
    }

    expected = cli.drive_subtask(**drive_args)

    async def inside() -> tuple[cli.SubtaskDrive, asyncio.AbstractEventLoop]:
        drive = await cli.drive_subtask_async(**drive_args)
        return drive, asyncio.get_running_loop()

    drive, outer = asyncio.run(inside())

    assert len(loops) == 2
    assert loops[1] is outer
    assert drive.summary.status == expected.summary.status == "escalated"
    assert drive.summary.results.keys() == expected.summary.results.keys() == {"explore"}
    assert drive.warnings == expected.warnings == ["summary warning", "runner warning"]
    assert drive == expected


def test_drive_subtask_async_hands_stop_to_the_engine(monkeypatch):
    """T5: the lane's `StopSignal` reaches the pygents walk as the same object,
    and every other keyword is what the sync driver sends today."""
    walks = _record_walks(monkeypatch)
    seen: list[dict[str, Any]] = []
    factory, runner = _recording_factory(seen)
    store = object()
    subtask = _drive_row()
    stop = StopSignal()

    drive = asyncio.run(
        cli.drive_subtask_async(
            store=store,
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=subtask,
            repo_dir=DRIVE_REPO,
            commands=["uv run pytest"],
            runner_factory=factory,
            stop=stop,
        )
    )

    ((workflow, passed_store, kwargs),) = walks
    assert workflow is task_workflow.TASK
    assert passed_store is store
    assert kwargs["stop"] is stop
    assert kwargs == {
        "story_id": DRIVE_PARENT.id,
        "subtask": subtask,
        "repo_dir": DRIVE_REPO,
        "commands": ["uv run pytest"],
        "card": DRIVE_CARD,
        "parent_story": DRIVE_PARENT,
        "extra_context": cli.gate_context(["uv run pytest"], False),
        "agent_runner": runner,
        "stop": stop,
    }
    assert drive.summary.status == "done"
    assert drive.warnings == []
    (factory_call,) = seen
    assert factory_call == {
        "store": store,
        "run_id": DRIVE_RUN_ID,
        "story_id": DRIVE_PARENT.id,
        "card_id": DRIVE_CARD.id,
    }


async def test_drive_subtask_async_hands_stop_and_resume_from_together(monkeypatch):
    """A lane relaunching a parked subtask passes both; both arrive untouched."""
    walks = _record_walks(monkeypatch)
    factory, _runner = _recording_factory([])
    stop = StopSignal()
    checkpoint = _checkpoint("parked", queue=("plan",))

    await cli.drive_subtask_async(
        store=object(),
        run_id=DRIVE_RUN_ID,
        card=DRIVE_CARD,
        parent=DRIVE_PARENT,
        subtask=_drive_row(),
        repo_dir=DRIVE_REPO,
        runner_factory=factory,
        stop=stop,
        resume_from=checkpoint,
    )

    ((_workflow, _store, kwargs),) = walks
    assert kwargs["stop"] is stop
    assert kwargs["resume_from"] is checkpoint


async def test_two_async_drives_on_one_loop_each_hand_their_own_stop(monkeypatch):
    """Supervisor lanes share one loop; each walk must get its own signal."""
    walks = _record_walks(monkeypatch)
    factory, _runner = _recording_factory([])
    first_row, second_row = _drive_row(), _drive_row()
    first_stop, second_stop = StopSignal(), StopSignal()

    def drive(row: models.SubtaskRun, stop: StopSignal):
        return cli.drive_subtask_async(
            store=object(),
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=row,
            repo_dir=DRIVE_REPO,
            runner_factory=factory,
            stop=stop,
        )

    drives = await asyncio.gather(drive(first_row, first_stop), drive(second_row, second_stop))

    assert [d.summary.status for d in drives] == ["done", "done"]
    stops = {id(kwargs["subtask"]): kwargs["stop"] for _w, _s, kwargs in walks}
    assert len(stops) == 2
    assert stops[id(first_row)] is first_stop
    assert stops[id(second_row)] is second_stop


async def test_drive_subtask_async_lets_an_engine_error_out(monkeypatch):
    """The driver catches nothing: an engine error escapes the `await` as is."""

    async def exploding(*args, **kwargs):
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(runtime_engine, "run_subtask_async", exploding)
    factory, _runner = _recording_factory([])

    with pytest.raises(EngineError, match="explore"):
        await cli.drive_subtask_async(
            store=object(),
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=_drive_row(),
            repo_dir=DRIVE_REPO,
            runner_factory=factory,
        )


@pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
async def test_drive_subtask_inside_a_running_loop_still_raises(monkeypatch):
    """The sync form is `asyncio.run` and stays so: inside a loop it refuses
    before the engine is reached. Callers inside a loop use the async form.
    A characterization pin: it passes before and after this card."""
    walks = _record_walks(monkeypatch)
    factory, _runner = _recording_factory([])

    with pytest.raises(RuntimeError, match="cannot be called from a running event loop"):
        cli.drive_subtask(
            store=object(),
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=_drive_row(),
            repo_dir=DRIVE_REPO,
            runner_factory=factory,
        )

    assert walks == []


runner = CliRunner()


def _invoke(project: Path, card_id: str, *extra: str):
    """Run the Typer command with the agent runner faked out.

    The factory is patched on the module rather than passed as an option: the
    injection seam is `cli.default_runner_factory`, and patching it is what
    proves the command reaches for that name instead of building an
    `AgentRunner` inline.
    """
    return runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            card_id,
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m1",
            *extra,
        ],
    )


@requires_git
@requires_brd
def test_the_command_prints_an_ok_envelope_and_exits_zero(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "done"
    assert "\n" not in result.stdout.strip()


@requires_git
@requires_brd
def test_pretty_indents_the_same_envelope(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"], "--pretty")

    assert result.exit_code == 0
    assert "\n" in result.stdout.strip()
    assert json.loads(result.stdout)["data"]["status"] == "done"


@requires_git
@requires_brd
def test_an_escalated_subtask_is_ok_true_and_exit_one(project, cards, monkeypatch):
    monkeypatch.setattr(
        cli, "default_runner_factory", lambda **kwargs: fake_runner(fail="review")
    )
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ESCALATED
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "escalated"
    assert envelope["data"]["failed_phase"] == "review"
    assert "canned gate failure" in envelope["data"]["detail"]


def _fail_board_writes(monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise cli.board.BoardError("simulated board write failure")

    monkeypatch.setattr(cli.board, "set_status", refuse)


@requires_git
@requires_brd
def test_a_failed_best_effort_board_phase_shows_up_in_warnings(
    project, cards, monkeypatch
):
    """§12: a run that says `done` while the card never moved is the exact
    failure this list exists to prevent. The board write is made to fail by
    having `board.set_status` raise, which is one honest way for it to fail."""
    _fail_board_writes(monkeypatch)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["status"] == "done"
    assert any("mark_in_progress" in warning for warning in payload["warnings"])
    assert any("mark_done" in warning for warning in payload["warnings"])


@requires_git
@requires_brd
def test_the_runners_own_warnings_join_the_summarys_in_the_payload(
    project, cards, monkeypatch
):
    """`AgentRunner` collects gate warnings on itself (dispatch.py:375) because
    an `AgentPhaseRunner` returns a result and has no second channel. §12 forbids
    a run reporting a clean success while a gate warned, so the payload has to
    carry that list too -- not just `SubtaskSummary.warnings`."""
    _fail_board_writes(monkeypatch)

    class WarningRunner:
        def __init__(self) -> None:
            self.warnings = ["explore attempt 1: gate exploration_output_gate warned"]
            self._inner = fake_runner()

        def __call__(self, phase, context, rendered):
            return self._inner(phase, context, rendered)

    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: WarningRunner(),
    )

    assert payload["status"] == "done"
    assert "explore attempt 1: gate exploration_output_gate warned" in payload["warnings"]
    # The summary's own best-effort warnings are still there: the two lists are
    # concatenated, not one replaced by the other.
    assert any("mark_done" in warning for warning in payload["warnings"])


@requires_git
@requires_brd
def test_the_run_id_is_minted_from_the_clock_the_caller_injected(project, cards):
    """The run id is a directory name and a join key, so which clock produced it
    is behaviour, not decoration: `started_at` and the id must be the same
    instant."""
    frozen = datetime(2026, 9, 23, 14, 5, 6, tzinfo=timezone.utc)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        clock=lambda: frozen,
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["run_id"] == cli.mint_run_id(cards["subtask"], frozen)
    assert payload["run_id"].startswith("20260923T140506Z-")
    row = sqlite3.connect(paths.project_db_path(project)).execute(
        "SELECT started_at FROM runs WHERE id = ?", (payload["run_id"],)
    ).fetchone()
    assert row[0].startswith("2026-09-23T14:05:06")


@requires_git
@requires_brd
def test_the_run_story_and_subtask_rows_land_in_the_project_db(project, cards):
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    conn = sqlite3.connect(paths.project_db_path(project))
    try:
        run_row = conn.execute(
            "SELECT status, base_branch, branch_prefix FROM runs WHERE id = ?",
            (payload["run_id"],),
        ).fetchone()
        story_row = conn.execute(
            "SELECT card_id, status FROM stories WHERE run_id = ?", (payload["run_id"],)
        ).fetchone()
        subtask_row = conn.execute(
            "SELECT card_id, branch, base_branch, status FROM subtasks WHERE run_id = ?",
            (payload["run_id"],),
        ).fetchone()
    finally:
        conn.close()

    assert run_row == ("done", "main", "m1")
    assert story_row == (cards["story"], "done")
    assert subtask_row == (cards["subtask"], payload["branch"], "main", "done")


@requires_git
@requires_brd
def test_no_run_artifact_is_written_inside_the_repository(project, cards):
    """D4/§9: every artifact path comes from `paths.py`, which roots under
    `data_dir()`. The only thing this run may add to the repo is the worktree
    git itself registered."""
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert list(project.rglob("journal.jsonl")) == []
    assert list(project.rglob("*.db")) == []
    porcelain = _git(project, "status", "--porcelain").splitlines()
    # Non-emptiness first: the run really does add the worktree, so an empty
    # porcelain would mean the fixture stopped showing it and the `all()` below
    # would pass on nothing.
    assert porcelain, "the run's own worktree should show up as untracked"
    assert all(".claude" in line or ".brd" in line for line in porcelain), porcelain
    assert (paths.run_dir(payload["run_id"]) / "journal.jsonl").is_file()


@requires_git
@requires_brd
def test_the_journal_opens_with_the_run_story_and_subtask_lines(project, cards):
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    lines = store_module.Journal(payload["run_id"]).read()
    assert [line.event for line in lines[:3]] == [
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
    ]


@requires_git
@requires_brd
def test_the_rows_exist_even_when_the_first_agent_phase_blows_up(project, cards):
    """The guarantee `status` and `resume` are built on: a process that dies on
    its first dispatch still left a run behind."""

    def exploding_factory(**kwargs):
        def runner(phase, context, rendered):
            raise RuntimeError("the runner died on its first call")

        return runner

    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=exploding_factory,
    )

    assert payload["status"] == "escalated"
    assert payload["failed_phase"] == "explore"
    conn = sqlite3.connect(paths.project_db_path(project))
    try:
        assert conn.execute(
            "SELECT count(*) FROM runs WHERE id = ?", (payload["run_id"],)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM subtasks WHERE run_id = ?", (payload["run_id"],)
        ).fetchone()[0] == 1
    finally:
        conn.close()


@requires_git
@requires_brd
def test_an_unknown_card_is_an_envelope_with_brds_own_message(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, "no-such-card")

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "BoardError"
    assert "no-such-card" in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs").exists()


@requires_git
@requires_brd
def test_a_parentless_card_is_refused_before_a_run_exists(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["milestone"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "ParentlessCardError"
    assert cards["milestone"] in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs").exists()


@requires_git
@requires_brd
def test_a_failing_parent_lookup_is_an_envelope_and_leaves_no_run_directory(
    project, cards, monkeypatch
):
    """`board.show` runs twice, and the second call can fail on its own."""
    real_show = board.show

    def show(card_id, *, repo_dir=None):
        if card_id == cards["story"]:
            raise board.BoardError(
                "card not found", argv=["brd", "show", card_id], exit_code=1
            )
        return real_show(card_id, repo_dir=repo_dir)

    monkeypatch.setattr(cli.board, "show", show)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    assert json.loads(result.stdout)["error"]["type"] == "BoardError"
    assert not (paths.data_dir() / "runs").exists()


def test_a_repo_dir_that_is_not_a_directory_is_an_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            "cbe34d00-9d8d-4f41-9c94-f99e665771b0",
            "--repo-dir",
            str(tmp_path / "missing"),
            "--branch-prefix",
            "m1",
        ],
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "RepoDirError"
    assert "missing" in envelope["error"]["message"]


@requires_git
@requires_brd
def test_a_card_id_that_is_not_a_uuid_is_an_envelope_not_a_traceback(
    project, cards, monkeypatch
):
    """`dag.short_id` raises a bare ValueError, and the run id is minted from the
    card id brd returned. A board that answers with a non-UUID id must not take
    the tool down with a stack trace."""

    def show(card_id, *, repo_dir=None):
        if card_id == cards["subtask"]:
            return models.Card(
                id="not-a-uuid", title="Odd card", status="todo", parent_id=cards["story"]
            )
        return models.Card(id=cards["story"], title="A story", status="todo")

    monkeypatch.setattr(cli.board, "show", show)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "ValueError"
    assert "not a card id" in envelope["error"]["message"]


@requires_git
@requires_brd
def test_an_engine_error_escaping_the_walk_reaches_the_operator(project, cards, monkeypatch):
    """`run_subtask_async` deliberately lets `EngineError` out rather than
    journalling it as a phase failure: an unbindable gate is a document bug,
    not an attempt."""

    async def exploding(*args, **kwargs):
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(cli.runtime_engine, "run_subtask_async", exploding)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "EngineError"
    assert "explore" in envelope["error"]["message"]


@requires_git
@requires_brd
def test_no_harness_is_ever_launched(project, cards, monkeypatch):
    """§14's adapter rule at the CLI seam: the launcher is injected, so a test
    that gets as far as launching one has already failed. `run_direct` is the
    only thing `default_runner_factory` would hand to a real `AgentRunner`."""

    def forbidden(*args, **kwargs):
        raise AssertionError("the CLI launched a harness process")

    monkeypatch.setattr(cli, "run_direct", forbidden)
    monkeypatch.setattr(cli.dispatch, "AgentRunner", forbidden)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )
    assert payload["status"] == "done"


@requires_git
@requires_brd
def test_allow_no_verification_flips_the_gate_the_cli_supplies_arguments_for(
    project, cards
):
    """§12's escape hatch, asserted at the seam this card owns.

    The gate itself runs inside `dispatch.AgentRunner`, which these tests replace
    with a fake -- so the honest assertion is that the context the CLI hands the
    engine drives the *real* `verification_gate` to the two verdicts §12
    describes. The gate's own truth table is unit-tested in
    `tests/steps/test_reducers.py` and is not re-tested here.
    """
    seen_off: list[tuple[str, dict[str, Any]]] = []
    cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(seen_off),
    )
    off = seen_off[0][1]
    blocked = verification_gate(
        off["suite_cmds"], off["allow_no_verification"], off["caller_provided"]
    )
    assert blocked["blocked"] == "verification"

    seen_on: list[tuple[str, dict[str, Any]]] = []
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        allow_no_verification=True,
        runner_factory=lambda **kwargs: fake_runner(seen_on),
    )
    on = seen_on[0][1]
    assert on["allow_no_verification"] is True
    assert (
        verification_gate(on["suite_cmds"], on["allow_no_verification"], on["caller_provided"])
        is None
    )
    assert payload["status"] == "done"


def _fake_payload(card_id: str, story_id: str) -> dict[str, Any]:
    """The exact `run_card` payload shape, for tests that replace `run_card`.

    Spelled out rather than built from a loop so that a key this program stops
    returning shows up here as a diff, not as a silently absent assertion.
    """
    return {
        "run_id": "20260923T140506Z-cbe34d00",
        "card_id": card_id,
        "story_id": story_id,
        "branch": "m2/task-fake-cbe34d00",
        "base_branch": "main",
        "worktree": "/tmp/agent-manager-fake-worktree",
        "status": "done",
        "failed_phase": None,
        "detail": None,
        "skipped": [],
        "warnings": [],
    }


@requires_git
@requires_brd
def test_repeated_verify_options_reach_run_card_in_command_line_order(
    project, cards, monkeypatch
):
    """§12's suite is the caller's to supply, and the engine runs the commands in
    sequence -- so the order the operator typed is behaviour, not decoration."""
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen["card_id"] = card_id
        seen.update(kwargs)
        return _fake_payload(card_id, cards["story"])

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(
        project,
        cards["subtask"],
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
    )

    assert result.exit_code == 0
    assert list(seen["commands"]) == ["uv run pytest", "uv run ruff check"]


@requires_git
@requires_brd
def test_no_verify_option_means_an_empty_command_list_not_none(project, cards, monkeypatch):
    """`gate_context` calls `list(commands)` and `verification_gate` tells an
    empty suite apart from a missing one, so `None` here would be a crash or a
    silently different verdict."""
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, cards["story"])

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == 0
    assert seen["commands"] == []


@requires_git
@requires_brd
def test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties(
    project, cards, monkeypatch
):
    """Review Focus: one occurrence is one whole command string. The CLI does no
    word-splitting, no parsing and no validation -- whether a command is nonsense
    is the engine's business, not this layer's."""
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, cards["story"])

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(
        project,
        cards["subtask"],
        "--verify",
        "uv run pytest -k 'not slow'",
        "--verify",
        "",
    )

    assert result.exit_code == 0
    assert list(seen["commands"]) == ["uv run pytest -k 'not slow'", ""]


@requires_git
@requires_brd
def test_verify_values_reach_the_gate_context_through_the_real_run_card(
    project, cards, monkeypatch
):
    """The whole chain, not just the call: `--verify` -> `run_card` ->
    `gate_context` -> the context `TASK` binds its gates out of.

    The real verify step runs these commands in the worktree, so they are ones
    that pass anywhere."""
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner(seen))

    result = _invoke(
        project,
        cards["subtask"],
        "--verify",
        "true",
        "--verify",
        "echo checked",
    )

    assert result.exit_code == 0, result.stdout
    _phase, context = seen[0]
    assert context["suite_cmds"] == ["true", "echo checked"]
    assert context["allow_no_verification"] is False
    assert context["caller_provided"] is False
    assert context["provided_verification"] is None


@requires_git
@requires_brd
def test_run_without_branch_prefix_is_a_usage_error_not_an_envelope(
    project, cards, monkeypatch
):
    """A missing required option never enters the `HANDLED` try block, so it is
    Typer's own usage error at exit 2 -- distinct from `EXIT_ERROR`, and with
    nothing written: no run id is minted because `run_card` is never called."""
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            cards["subtask"],
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
        ],
    )

    assert result.exit_code == 2
    assert "--branch-prefix" in result.output
    # Not the `ok: false` envelope: a usage error never reaches the try block.
    assert '"ok"' not in result.stdout
    assert not (paths.data_dir() / "runs").exists()


@requires_git
@requires_brd
def test_the_branch_prefix_the_operator_gave_lands_in_the_payloads_branch(
    project, cards, monkeypatch
):
    """The default is gone, so the only way a prefix reaches the branch name is
    the option -- and the branch is what every later command keys off."""
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    card = board.show(cards["subtask"], repo_dir=project)

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            cards["subtask"],
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m2",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)["data"]
    assert data["branch"] == dag.task_branch("m2", card)
    assert data["branch"].startswith("m2/")


@requires_git
@requires_brd
def test_the_run_success_envelope_keys_are_frozen(project, cards, monkeypatch):
    """A shape freeze: `status`, `logs` and every downstream consumer read these
    eleven names, so an added, dropped or renamed key is a contract break."""
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}
    assert envelope["ok"] is True
    assert set(envelope["data"]) == {
        "run_id",
        "card_id",
        "story_id",
        "branch",
        "base_branch",
        "worktree",
        "status",
        "failed_phase",
        "detail",
        "skipped",
        "warnings",
    }


def _block(root: Path, card_id: str, blocker: str) -> None:
    subprocess.run(
        ["brd", "block", card_id, "--by", blocker],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


M2_SHAPE = (
    ("A", "Story A: the board adapter", ("a1: read one card", "a2: read a subtree")),
    ("B", "Story B: the store", ("b1: the schema", "b2: the journal", "b3: replay")),
    ("C", "Story C: the CLI", ("c1: run --card", "c2: status")),
)
"""Milestone 2's shape: three chained stories with two or three subtasks each."""


@pytest.fixture
def milestone_board(project) -> dict[str, Any]:
    """A real brd board shaped like milestone 2, next to a decoy milestone.

    B is blocked by A and C by B. Each story's subtasks are chained with
    `brd block` so the census order does not depend on creation timestamps.
    The decoy root shares the word "skeleton", so only a longer substring
    names milestone 2.
    """
    _add_card(project, "Milestone 1: walking skeleton")
    milestone = _add_card(project, "Milestone 2: make the skeleton real")
    stories: dict[str, str] = {}
    subtasks: dict[str, list[str]] = {}
    titles: dict[str, str] = {}
    previous_story: str | None = None
    for key, story_title, subtask_titles in M2_SHAPE:
        story = _add_card(project, story_title, milestone)
        titles[story] = story_title
        if previous_story is not None:
            _block(project, story, previous_story)
        chain: list[str] = []
        for subtask_title in subtask_titles:
            subtask = _add_card(project, subtask_title, story)
            titles[subtask] = subtask_title
            if chain:
                _block(project, subtask, chain[-1])
            chain.append(subtask)
        stories[key] = story
        subtasks[key] = chain
        previous_story = story
    return {"milestone": milestone, "stories": stories, "subtasks": subtasks, "titles": titles}


def _m2_branch(project: Path, card_id: str) -> str:
    return dag.task_branch("m2", board.show(card_id, repo_dir=project))


def _dry_run(project: Path, needle: str, *extra: str):
    return runner.invoke(
        cli.app,
        [
            "run",
            "--milestone",
            needle,
            "--dry-run",
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m2",
            *extra,
        ],
    )


class _Forbidden:
    """Stands in for anything the dry path must never reach, and fails loudly.

    `pytest.fail` raises a `BaseException`, which `CliRunner` does not swallow
    and `HANDLED` does not catch, so reaching one of these fails the test
    instead of turning into an envelope.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        pytest.fail(f"the milestone dry run reached cli.{self._name}")

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("__"):
            raise AttributeError(attr)
        pytest.fail(f"the milestone dry run reached cli.{self._name}.{attr}")


def _forbid_writes(monkeypatch) -> None:
    monkeypatch.setattr(cli, "run_card", _Forbidden("run_card"))
    monkeypatch.setattr(cli, "Store", _Forbidden("Store"))
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))
    monkeypatch.setattr(cli.board, "set_status", _Forbidden("board.set_status"))
    # The dry run plans Integrate from `integration`'s pure helpers; the two
    # names that would write a branch or a worktree must never be reached.
    monkeypatch.setattr(
        integration, "integrate_milestone", _Forbidden("integration.integrate_milestone")
    )
    monkeypatch.setattr(integration, "merge_tip", _Forbidden("integration.merge_tip"))


def _assert_nothing_written(project: Path, porcelain_before: str) -> None:
    """No run dir or projection, no worktree, no branch, no repo change.

    `paths.data_dir()` holds nothing but process-wide lock files: a board
    write in the test's own setup (`board.set_status`) takes the project's
    board lock (spec X7) and so leaves its lock file under
    `data_dir()/projects`, which is not a run left behind. Anything else there
    -- a projection, a run directory, any stray file -- is.
    """
    data = paths.data_dir()
    projects = data / "projects"
    written = sorted(
        str(entry.relative_to(data))
        for entry in data.rglob("*")
        if entry != projects
        and not (entry.parent == projects and entry.suffix == ".lock")
    )
    assert written == []
    worktrees = [
        line
        for line in _git(project, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    assert len(worktrees) == 1, worktrees
    assert _git(project, "branch", "--format=%(refname:short)").split() == ["main"]
    assert not (project / ".claude").exists()
    assert _git(project, "status", "--porcelain") == porcelain_before


@requires_git
@requires_brd
def test_the_milestone_dry_run_stacks_each_story_on_the_previous_ones_tip(
    project, milestone_board, monkeypatch
):
    stories = milestone_board["stories"]
    subtasks = milestone_board["subtasks"]
    titles = milestone_board["titles"]
    board_before = board.roots(repo_dir=project)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    result = _dry_run(project, milestone_board["milestone"])

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == {"max_concurrent", "levels", "already_done", "integrate"}
    assert data["already_done"] == []
    assert [level["level"] for level in data["levels"]] == [0, 1, 2]
    assert [
        [story["story"] for story in level["stories"]] for level in data["levels"]
    ] == [[stories["A"]], [stories["B"]], [stories["C"]]]

    previous_tip = "main"
    for level, key in zip(data["levels"], "ABC"):
        (story,) = level["stories"]
        branches = [_m2_branch(project, subtask) for subtask in subtasks[key]]
        assert story["title"] == titles[stories[key]]
        assert story["root"] == previous_tip
        assert [row["id"] for row in story["subtasks"]] == subtasks[key]
        assert [row["title"] for row in story["subtasks"]] == [
            titles[subtask] for subtask in subtasks[key]
        ]
        assert [row["status"] for row in story["subtasks"]] == ["todo"] * len(subtasks[key])
        assert [row["branch"] for row in story["subtasks"]] == branches
        # The base column: the first subtask on the previous story's tip (or
        # the base branch), every later one on the subtask before it.
        assert [row["base"] for row in story["subtasks"]] == [previous_tip, *branches[:-1]]
        assert story["root"] == story["subtasks"][0]["base"]
        previous_tip = branches[-1]

    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


@requires_git
@requires_brd
def test_done_work_is_already_done_and_still_anchors_the_stack(
    project, milestone_board, monkeypatch
):
    stories = milestone_board["stories"]
    subtasks = milestone_board["subtasks"]
    titles = milestone_board["titles"]
    for subtask in subtasks["A"]:
        board.set_status(subtask, "done", repo_dir=project)
    board.set_status(stories["A"], "done", repo_dir=project)
    board.set_status(subtasks["B"][0], "done", repo_dir=project)
    board_before = board.roots(repo_dir=project)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    result = _dry_run(project, milestone_board["milestone"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["already_done"] == [
        {"kind": "story", "id": stories["A"], "title": titles[stories["A"]]},
        {
            "kind": "subtask",
            "id": subtasks["B"][0],
            "title": titles[subtasks["B"][0]],
            "story": stories["B"],
        },
    ]
    assert [
        [story["story"] for story in level["stories"]] for level in data["levels"]
    ] == [[stories["B"]], [stories["C"]]]

    a_tip = _m2_branch(project, subtasks["A"][-1])
    b_branches = [_m2_branch(project, subtask) for subtask in subtasks["B"]]
    (b_row,) = data["levels"][0]["stories"]
    assert b_row["root"] == a_tip
    assert [row["id"] for row in b_row["subtasks"]] == subtasks["B"][1:]
    assert [row["base"] for row in b_row["subtasks"]] == b_branches[:2]
    (c_row,) = data["levels"][1]["stories"]
    assert c_row["root"] == b_branches[-1]

    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


@requires_git
@requires_brd
def test_the_milestone_dry_run_shows_the_integrate_plan_and_writes_nothing(
    project, milestone_board, monkeypatch
):
    """Spec test 8. Story A is already done and still leads the Integrate
    order: Integrate folds in every story's tip. Nothing is written: no run
    directory, no `m2-integrate` branch, no worktree, no board change."""
    stories = milestone_board["stories"]
    subtasks = milestone_board["subtasks"]
    for subtask in subtasks["A"]:
        board.set_status(subtask, "done", repo_dir=project)
    board.set_status(stories["A"], "done", repo_dir=project)
    board_before = board.roots(repo_dir=project)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))

    result = _dry_run(project, milestone_board["milestone"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["integrate"] == {
        "branch": "m2-integrate",
        "worktree": str(cli.worktree_for(project, "m2-integrate")),
        "order": [
            {"story": stories[key], "tip": _m2_branch(project, subtasks[key][-1])}
            for key in "ABC"
        ],
    }
    assert not cli.worktree_for(project, "m2-integrate").exists()
    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


@requires_git
@requires_brd
def test_a_title_substring_names_the_same_milestone_as_its_id(
    project, milestone_board, monkeypatch
):
    _forbid_writes(monkeypatch)

    by_id = _dry_run(project, milestone_board["milestone"])
    by_title = _dry_run(project, "skeleton real")

    assert by_id.exit_code == 0, by_id.output
    assert by_title.exit_code == 0, by_title.output
    assert json.loads(by_title.stdout) == json.loads(by_id.stdout)


@requires_git
@requires_brd
def test_the_milestone_dry_run_pretty_indents_the_same_envelope(
    project, milestone_board, monkeypatch
):
    _forbid_writes(monkeypatch)

    plain = _dry_run(project, milestone_board["milestone"])
    pretty = _dry_run(project, milestone_board["milestone"], "--pretty")

    assert pretty.exit_code == 0, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


@requires_git
@requires_brd
@pytest.mark.parametrize("extra, bound", [((), 4), (("--max-concurrent", "3"), 3)])
def test_the_milestone_dry_run_echoes_the_lane_bound_and_writes_nothing(
    project, milestone_board, monkeypatch, extra, bound
):
    """`milestone_board` is three one-story levels, so each level runs one
    story whatever the bound."""
    board_before = board.roots(repo_dir=project)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))

    result = _dry_run(project, milestone_board["milestone"], *extra)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["max_concurrent"] == bound
    assert [level["concurrent"] for level in data["levels"]] == [1, 1, 1]
    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


@pytest.mark.parametrize("extra, bound", [((), 4), (("--max-concurrent", "3"), 3)])
def test_a_milestone_dry_run_passes_the_lane_bound_to_the_preview(
    tmp_path, monkeypatch, extra, bound
):
    """No git or brd needed: `dry_run_milestone` is replaced by a recorder, and
    every write path and `run_milestone` are forbidden."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_dry_run_milestone(needle, **kwargs):
        calls.append((needle, kwargs))
        return {"max_concurrent": kwargs["max_concurrent"], "levels": [], "already_done": []}

    monkeypatch.setattr(cli, "dry_run_milestone", fake_dry_run_milestone)

    result = _milestone_run(tmp_path, "--dry-run", *extra)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["max_concurrent"] == bound
    assert calls == [
        (
            "Milestone 3",
            {
                "repo_dir": tmp_path,
                "branch_prefix": "m3",
                "base_branch": "main",
                "max_concurrent": bound,
            },
        )
    ]
    assert list(paths.data_dir().iterdir()) == []


def _refusal(result) -> dict[str, Any]:
    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    return envelope["error"]


@requires_git
@requires_brd
def test_a_story_cycle_from_the_board_is_an_envelope_naming_both_stories(
    project, monkeypatch
):
    """brd refuses to store a cycle, so the tree is served by hand. The census
    orders sibling stories by `blocked_by` and meets the cycle first, so the
    refusal is `CensusOrderError`, before any geometry."""
    milestone = _add_card(project, "Milestone 9: cyclic")
    a, b = _plan_id(1), _plan_id(2)

    def tree(card_id, *, repo_dir=None):
        return models.CardNode(
            id=milestone,
            title="Milestone 9: cyclic",
            status="todo",
            children=[
                models.CardNode(
                    id=a,
                    title="story a",
                    status="todo",
                    blocked_by=[b],
                    children=[models.CardNode(id=_plan_id(11), title="a1", status="todo")],
                ),
                models.CardNode(
                    id=b,
                    title="story b",
                    status="todo",
                    blocked_by=[a],
                    children=[models.CardNode(id=_plan_id(21), title="b1", status="todo")],
                ),
            ],
        )

    monkeypatch.setattr(cli.board, "tree", tree)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(project, milestone))

    assert error["type"] == "CensusOrderError"
    assert a in error["message"]
    assert b in error["message"]
    _assert_nothing_written(project, porcelain_before)


@requires_git
@requires_brd
def test_a_story_cycle_in_the_census_is_named_as_a_trail_by_the_dag_check(
    project, monkeypatch
):
    """The spec's `DependencyCycleError` path: a cyclic census that got past
    ordering is refused by `assert_no_blocker_cycles`, through the envelope."""
    milestone = _add_card(project, "Milestone 9: cyclic")
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    monkeypatch.setattr(
        cli.census,
        "flatten_milestone",
        lambda root: census.Census(milestone_title=root.title, stories=[a, b]),
    )
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(project, milestone))

    assert error["type"] == "DependencyCycleError"
    assert f"#{a.id} -> #{b.id} -> #{a.id}" in error["message"]
    _assert_nothing_written(project, porcelain_before)


@requires_git
@requires_brd
def test_a_story_blocked_by_two_stories_dry_runs_on_a_merged_base(project, monkeypatch):
    milestone = _add_card(project, "Milestone 8: diamond")
    first = _add_card(project, "Story one", milestone)
    second = _add_card(project, "Story two", milestone)
    joined = _add_card(project, "Story three", milestone)
    for story in (first, second, joined):
        _add_card(project, f"only subtask of {story}", story)
    _block(project, joined, first)
    _block(project, joined, second)
    # `merged_from` follows the joined story's `blocked_by` as brd reports it,
    # which the census copies through untouched.
    joined_node = next(
        node for node in board.tree(milestone, repo_dir=project).children if node.id == joined
    )
    census_order = [dep for dep in joined_node.blocked_by if dep in (first, second)]
    assert sorted(census_order) == sorted([first, second])
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    result = _dry_run(project, milestone)

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    rows = {
        row["story"]: row for level in envelope["data"]["levels"] for row in level["stories"]
    }
    assert rows[joined]["merged_from"] == census_order
    assert rows[joined]["root"] == f"m2/base-{dag.short_id(joined)}"
    assert rows[joined]["subtasks"][0]["base"] == rows[joined]["root"]
    assert "merged_from" not in rows[first]
    assert "merged_from" not in rows[second]
    _assert_nothing_written(project, porcelain_before)


@requires_git
@requires_brd
def test_an_unknown_milestone_is_an_envelope(project, milestone_board, monkeypatch):
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(project, "Milestone 404"))

    assert error["type"] == "MilestoneNotFoundError"
    assert "Milestone 404" in error["message"]


def test_a_milestone_dry_run_with_a_missing_repo_dir_is_an_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(tmp_path / "missing", "2"))

    assert error["type"] == "RepoDirError"
    assert "missing" in error["message"]


@requires_brd
def test_a_milestone_dry_run_outside_a_brd_project_is_a_board_error(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    plain = tmp_path / "plain"
    plain.mkdir()
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(plain, "2"))

    assert error["type"] == "BoardError"
    assert list(paths.data_dir().iterdir()) == []


SOME_CARD = "cbe34d00-9d8d-4f41-9c94-f99e665771b0"


@pytest.mark.parametrize(
    "targets, word",
    [
        (["--card", SOME_CARD, "--milestone", "2"], "both"),
        (["--card", SOME_CARD, "--milestone", "2", "--dry-run"], "both"),
        ([], "required"),
        (["--dry-run"], "required"),
        (["--card", SOME_CARD, "--dry-run"], "previews"),
        (["--milestone", "", "--dry-run"], "blank"),
        (["--milestone", "   ", "--dry-run"], "blank"),
        (["--milestone", "2", "--max-concurrent", "0"], "least"),
        (["--milestone", "2", "--max-concurrent=-1"], "least"),
        (["--milestone", "2", "--dry-run", "--max-concurrent", "0"], "least"),
        (["--card", SOME_CARD, "--max-concurrent", "2"], "only"),
        (["--card", SOME_CARD, "--max-concurrent", "4"], "only"),
    ],
)
def test_bad_run_targets_are_usage_errors_that_start_nothing(
    tmp_path, monkeypatch, targets, word
):
    """Validation happens before the `HANDLED` try block, so these are Typer's
    exit 2 and never an envelope. Nothing is dispatched: `run_card` and
    `dry_run_milestone` are both forbidden here."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))

    result = runner.invoke(
        cli.app,
        ["run", *targets, "--repo-dir", str(tmp_path), "--branch-prefix", "m2"],
    )

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert word in result.output
    assert list(paths.data_dir().iterdir()) == []


CLEAN_MILESTONE = {
    "done": True,
    "run_id": "20260924T000000Z-0badcafe",
    "levels": [{"level": 0, "stories": ["story-a"]}],
    "completed": ["subtask-a1"],
    "tips": [{"story": "story-a", "tip": "m3/task-a1"}],
    "warnings": [],
    "integrated": {
        "branch": "m3-integrate",
        "worktree": "/repo/.claude/worktrees/m3-integrate",
        "merged": ["story-a"],
        "resolved": [],
    },
}
"""`run_milestone`'s clean payload shape: `done: true`, `integrated`, and no `status` or `escalated` key."""

ESCALATED_MILESTONE = {
    "escalated": True,
    "run_id": "20260924T000000Z-0badcafe",
    "level": 1,
    "story": "story-b",
    "subtask": "subtask-b1",
    "failed_phase": "review",
    "detail": "phase 'review' gate 'review_gate' failed",
    "warnings": [],
}
"""`run_milestone`'s escalation payload shape: no `status` key either."""

INTEGRATE_ESCALATED_MILESTONE = {
    "escalated": True,
    "phase": "integrate",
    "story": None,
    "files": [],
    "detail": (
        "the integrated branch failed its final verification in "
        "/repo/.claude/worktrees/m3-integrate: suite red"
    ),
    "run_id": "20260924T000000Z-0badcafe",
    "warnings": [],
}
"""`run_milestone`'s payload when it stopped at Integrate (addendum I5)."""


def _milestone_run(tmp_path: Path, *extra: str):
    return runner.invoke(
        cli.app,
        [
            "run",
            "--milestone",
            "Milestone 3",
            "--repo-dir",
            str(tmp_path),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m3",
            *extra,
        ],
    )


def _patch_run_milestone(monkeypatch, outcome: Any) -> list[tuple[str, dict[str, Any]]]:
    """Replace `orchestrate.run_milestone`, forbid every other run path, record calls.

    `outcome` is returned, or raised when it is an exception instance.
    """
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run_milestone(milestone, **kwargs):
        calls.append((milestone, kwargs))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(orchestrate, "run_milestone", fake_run_milestone)
    return calls


def test_a_milestone_run_calls_run_milestone_once_with_the_run_options(
    tmp_path, monkeypatch
):
    """Spec test 3: the same options as `--card`, the verify order kept, and no
    `runner_factory` or `driver`, so production gets `cli.default_runner_factory`
    and `cli.drive_subtask`. The kwargs are compared whole, so an extra key fails."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_milestone(monkeypatch, CLEAN_MILESTONE)

    result = _milestone_run(
        tmp_path,
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
        "--allow-no-verification",
    )

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    assert json.loads(result.stdout) == cli.ok_envelope(CLEAN_MILESTONE)
    assert calls == [
        (
            "Milestone 3",
            {
                "repo_dir": tmp_path,
                "base_branch": "main",
                "branch_prefix": "m3",
                "commands": ["uv run pytest", "uv run ruff check"],
                "allow_no_verification": True,
                "max_concurrent": 4,
            },
        )
    ]


@pytest.mark.parametrize("given, passed", [("2", 2), ("1", 1), ("4", 4)])
def test_an_explicit_max_concurrent_reaches_run_milestone(
    tmp_path, monkeypatch, given, passed
):
    """P1: the flag's value is what `run_milestone` gets, and `1` is passed as
    `1`, so `--max-concurrent 1` is the sequential runner."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_milestone(monkeypatch, CLEAN_MILESTONE)

    result = _milestone_run(tmp_path, "--max-concurrent", given)

    assert result.exit_code == 0, result.output
    ((_, kwargs),) = calls
    assert kwargs["max_concurrent"] == passed


def test_the_cli_default_lane_count_is_the_models_default():
    """Review focus: the flag's default and the recorded model default are the
    same number, so a run with no flag records what it ran with."""
    assert cli.DEFAULT_MAX_CONCURRENT == 4
    assert models.RunConfig().max_concurrent_stories == cli.DEFAULT_MAX_CONCURRENT


def test_a_milestone_run_without_verify_passes_an_empty_list_and_no_opt_out(
    tmp_path, monkeypatch
):
    """Review focus: `gate_context` calls `list(commands)`, so `None` would crash,
    and the opt-out must stay closed unless the flag is given."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_milestone(monkeypatch, CLEAN_MILESTONE)

    result = _milestone_run(tmp_path)

    assert result.exit_code == 0, result.output
    ((_, kwargs),) = calls
    assert kwargs["commands"] == []
    assert kwargs["allow_no_verification"] is False


def test_an_escalated_milestone_exits_one_with_an_ok_envelope(tmp_path, monkeypatch):
    """Spec test 4: an escalation is a truthful result. The payload has no
    `status` key, so the exit code must come from its `escalated` flag."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, ESCALATED_MILESTONE)

    plain = _milestone_run(tmp_path)
    pretty = _milestone_run(tmp_path, "--pretty")

    assert plain.exit_code == cli.EXIT_ESCALATED, plain.output
    assert json.loads(plain.stdout) == cli.ok_envelope(ESCALATED_MILESTONE)
    assert pretty.exit_code == cli.EXIT_ESCALATED, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


def test_an_integrate_escalation_exits_one_with_an_ok_envelope(tmp_path, monkeypatch):
    """Spec test 9: the existing `escalated is True` check covers the
    Integrate payload with no change of its own."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, INTEGRATE_ESCALATED_MILESTONE)

    result = _milestone_run(tmp_path)

    assert result.exit_code == cli.EXIT_ESCALATED, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(INTEGRATE_ESCALATED_MILESTONE)


@pytest.mark.parametrize(
    "error",
    [
        cli.CliError("no milestone matches 'Milestone 3'"),
        board.BoardError("brd refused", argv=["brd", "tree"]),
        ValueError("not a card id: 'x'"),
        # Spec X7: another process held a project lock past its timeout before
        # the run started (e.g. `refresh_git`'s git lock).
        locks.LockTimeoutError(Path("/data/projects/abc.git.lock"), 600.0),
    ],
    ids=["CliError", "BoardError", "ValueError", "LockTimeoutError"],
)
def test_a_handled_error_from_a_milestone_run_is_an_envelope(tmp_path, monkeypatch, error):
    """Spec test 5: every `HANDLED` refusal is `ok: false` at exit 3."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, error)

    refusal = _refusal(_milestone_run(tmp_path))

    assert refusal["type"] == type(error).__name__
    assert refusal["message"] == str(error)


def test_an_unhandled_error_from_a_milestone_run_crashes_loudly(tmp_path, monkeypatch):
    """Review focus: anything outside `HANDLED` is a bug and keeps its traceback."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, RuntimeError("boom"))

    result = _milestone_run(tmp_path)

    assert isinstance(result.exception, RuntimeError)
    assert '"ok"' not in result.stdout


BOARD_CARD = models.CardNode(id=SOME_CARD, title="Milestone 14: run the board", status="todo")
"""A milestone root for the prefix tests. Its id is a real UUID, so `dag.task_stem` accepts it."""


def test_board_prefix_of_without_a_prefix_is_the_milestones_own_stem():
    """Spec 3.2: with --branch-prefix omitted, each milestone's prefix is its card stem."""
    prefix_of = cli.board_prefix_of(None)

    assert prefix_of(BOARD_CARD) == dag.task_stem(BOARD_CARD)
    assert prefix_of(BOARD_CARD) == "milestone-14-run-the-cbe34d00"


def test_board_prefix_of_with_a_prefix_joins_it_to_the_stem_and_never_reuses_it_verbatim():
    """Spec 3.2: a given prefix is `<prefix>-<stem>`, so two milestones never share it."""
    prefix_of = cli.board_prefix_of("sprint9")

    assert prefix_of(BOARD_CARD) == "sprint9-milestone-14-run-the-cbe34d00"
    assert prefix_of(BOARD_CARD) != "sprint9"


BLANK_BOARD_PREFIX = "--branch-prefix with --board needs a non-blank prefix, not a blank string"
PREFIX_REQUIRED = "--branch-prefix is required with --card or --milestone"


@pytest.mark.parametrize(
    "kwargs, message, hint",
    [
        (
            {"card": SOME_CARD, "milestone": None, "board": True, "branch_prefix": "m2"},
            "give --board or --card, not both",
            "'--board' / '--card'",
        ),
        (
            {"card": None, "milestone": "2", "board": True, "branch_prefix": "m2"},
            "give --board or --milestone, not both",
            "'--board' / '--milestone'",
        ),
        (
            {"card": None, "milestone": None, "board": False, "branch_prefix": "m2"},
            "one of --card, --milestone or --board is required",
            "'--card' / '--milestone' / '--board'",
        ),
        (
            {"card": None, "milestone": "2", "board": False, "branch_prefix": None},
            PREFIX_REQUIRED,
            "'--branch-prefix'",
        ),
        (
            {"card": SOME_CARD, "milestone": None, "board": False, "branch_prefix": None},
            PREFIX_REQUIRED,
            "'--branch-prefix'",
        ),
        (
            {"card": None, "milestone": None, "board": True, "branch_prefix": ""},
            BLANK_BOARD_PREFIX,
            "'--branch-prefix'",
        ),
        (
            {"card": None, "milestone": None, "board": True, "branch_prefix": "   "},
            BLANK_BOARD_PREFIX,
            "'--branch-prefix'",
        ),
        (
            {
                "card": SOME_CARD,
                "milestone": None,
                "board": False,
                "branch_prefix": "m2",
                "max_concurrent": 2,
            },
            "--max-concurrent applies only to --milestone or --board",
            "'--max-concurrent'",
        ),
        (
            {
                "card": None,
                "milestone": None,
                "board": True,
                "branch_prefix": None,
                "max_concurrent": 0,
            },
            "--max-concurrent must be at least 1, got 0",
            "'--max-concurrent'",
        ),
    ],
)
def test_check_run_targets_words_each_board_refusal_like_the_card_milestone_conflict(
    kwargs, message, hint
):
    """Exact wording and hint, checked on the function itself so Typer's error
    box cannot wrap the text out from under the assertion."""
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(dry_run=False, **kwargs)

    assert caught.value.message == message
    assert caught.value.param_hint == hint


@pytest.mark.parametrize(
    "kwargs",
    [
        {"board": True, "branch_prefix": None, "dry_run": False, "max_concurrent": None},
        {"board": True, "branch_prefix": "sprint9", "dry_run": False, "max_concurrent": 2},
        {"board": True, "branch_prefix": None, "dry_run": True, "max_concurrent": 1},
    ],
)
def test_check_run_targets_accepts_board_with_or_without_a_prefix_and_with_dry_run(kwargs):
    """--branch-prefix is optional only in board mode; --dry-run and
    --max-concurrent both apply to --board."""
    assert cli._check_run_targets(card=None, milestone=None, **kwargs) is None


def _forbid_board_paths(monkeypatch) -> None:
    """Every run path forbidden: a refusal must start nothing at all."""
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))
    monkeypatch.setattr(orchestrate, "run_board", _Forbidden("run_board"))
    monkeypatch.setattr(cli, "dry_run_board", _Forbidden("dry_run_board"))


@pytest.mark.parametrize(
    "targets, word",
    [
        (["--board", "--card", SOME_CARD, "--branch-prefix", "m2"], "both"),
        (["--board", "--milestone", "2", "--branch-prefix", "m2"], "both"),
        (["--board", "--milestone", "2", "--dry-run"], "both"),
        (["--branch-prefix", "m2"], "required"),
        (["--milestone", "2"], "required"),
        (["--milestone", "2", "--dry-run"], "required"),
        (["--card", SOME_CARD], "required"),
        (["--board", "--branch-prefix", ""], "blank"),
        (["--board", "--branch-prefix", "   "], "blank"),
        (["--board", "--max-concurrent", "0"], "least"),
        (["--board", "--dry-run", "--max-concurrent", "0"], "least"),
        (["--card", SOME_CARD, "--branch-prefix", "m2", "--max-concurrent", "2"], "only"),
    ],
)
def test_bad_board_targets_are_usage_errors_that_start_nothing(
    tmp_path, monkeypatch, targets, word
):
    """Spec tests 1-4, 7, 9 at the command line: Typer's exit 2, never an
    envelope, and `run_board`, `run_milestone`, `run_card` all unreached."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)

    result = runner.invoke(cli.app, ["run", *targets, "--repo-dir", str(tmp_path)])

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert word in result.output
    assert list(paths.data_dir().iterdir()) == []


def _as_json(value: Any) -> Any:
    """`value` as the CLI would print it: `render` stringifies any `Path`."""
    return json.loads(cli.render({"value": value}))["value"]


def _forbid_board_dry_run_writes(monkeypatch) -> None:
    """The board preview must open no Store, check no claims and run nothing."""
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(store_module, "Store", _Forbidden("store.Store"))
    monkeypatch.setattr(cli, "refuse_claimed", _Forbidden("refuse_claimed"))
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    monkeypatch.setattr(orchestrate, "run_board", _Forbidden("run_board"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))


def _expected_board_milestone(
    card: models.CardNode, prefix: str, *, root: Path, max_concurrent: int
) -> dict[str, Any]:
    return {
        "milestone_id": card.id,
        "title": card.title,
        "branch_prefix": prefix,
        "plan": cli.dry_run_payload(
            census.flatten_milestone(card).stories,
            repo_dir=root,
            branch_prefix=prefix,
            base_branch="main",
            max_concurrent=max_concurrent,
        ),
    }


def _board_dry_run(repo_dir: Path, *extra: str):
    return runner.invoke(
        cli.app,
        [
            "run",
            "--board",
            "--dry-run",
            "--repo-dir",
            str(repo_dir),
            "--base-branch",
            "main",
            *extra,
        ],
    )


@requires_git
@requires_brd
def test_the_board_dry_run_previews_every_open_milestone_by_level_and_writes_nothing(
    project, monkeypatch
):
    """Spec test 13: two milestones, the second blocked by the first, on a real
    temporary brd board. Two levels, each milestone with its derived prefix and
    its own `dry_run_payload` nested as `plan`; nothing run, nothing written."""
    first = _add_card(project, "Milestone A: the adapter")
    first_story = _add_card(project, "Story A1: read cards", first)
    _add_card(project, "a1: read one card", first_story)
    second = _add_card(project, "Milestone B: the store")
    second_story = _add_card(project, "Story B1: the schema", second)
    _add_card(project, "b1: write the schema", second_story)
    _block(project, second, first)
    board_before = board.roots(repo_dir=project)
    roots = {card.id: card for card in board_before}
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(project)

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    # `cli.render` always serializes with `sort_keys=True` (it documents this
    # as making output diffable), so the rendered envelope's key order is
    # always alphabetical regardless of the payload dict's construction
    # order; membership, not order, is what this checks.
    assert set(data) == {"board", "max_concurrent", "levels"}
    assert data["board"] is True
    assert data["max_concurrent"] == cli.DEFAULT_MAX_CONCURRENT
    root = cli.resolve_repo_dir(project)
    expected = [
        {
            "level": index,
            "milestones": [
                _expected_board_milestone(
                    roots[card_id],
                    dag.task_stem(roots[card_id]),
                    root=root,
                    max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                )
            ],
        }
        for index, card_id in enumerate([first, second])
    ]
    assert data["levels"] == _as_json(expected)
    for level in data["levels"]:
        for entry in level["milestones"]:
            assert set(entry) == {"milestone_id", "title", "branch_prefix", "plan"}
    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


def _board_milestone(
    n: int,
    *,
    status: str = "todo",
    blocked_by: tuple[str, ...] = (),
    card_id: str | None = None,
    title: str | None = None,
) -> models.CardNode:
    """A milestone root with one open story holding one open subtask."""
    return models.CardNode(
        id=card_id or _plan_id(n),
        title=title or f"Milestone {n}",
        status=status,
        blocked_by=list(blocked_by),
        children=[
            models.CardNode(
                id=_plan_id(n * 100 + 1),
                title=f"story {n}",
                status="todo",
                children=[
                    models.CardNode(id=_plan_id(n * 100 + 2), title=f"subtask {n}", status="todo")
                ],
            )
        ],
    )


def _serve_roots(monkeypatch, roots: list[models.CardNode]) -> None:
    monkeypatch.setattr(cli.board, "roots", lambda *, repo_dir=None: list(roots))


def test_the_board_dry_run_derives_prefixes_from_a_given_prefix_and_drops_done_milestones(
    tmp_path, monkeypatch
):
    """A done milestone is not previewed and the one it blocked lands in level 0.
    A given prefix becomes `<prefix>-<stem>` in each milestone's nested plan, and
    --max-concurrent is echoed at both levels of the payload."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    done = _board_milestone(1, status="done")
    second = _board_milestone(2, blocked_by=(done.id,))
    third = _board_milestone(3, blocked_by=(second.id,))
    _serve_roots(monkeypatch, [done, second, third])
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(tmp_path, "--branch-prefix", "sprint9", "--max-concurrent", "2")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    assert data == _as_json(
        {
            "board": True,
            "max_concurrent": 2,
            "levels": [
                {
                    "level": index,
                    "milestones": [
                        _expected_board_milestone(
                            card,
                            f"sprint9-{dag.task_stem(card)}",
                            root=root,
                            max_concurrent=2,
                        )
                    ],
                }
                for index, card in enumerate([second, third])
            ],
        }
    )
    assert list(paths.data_dir().iterdir()) == []


def test_an_empty_board_dry_runs_to_no_levels_and_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _serve_roots(monkeypatch, [])
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {
        "board": True,
        "max_concurrent": cli.DEFAULT_MAX_CONCURRENT,
        "levels": [],
    }


@pytest.mark.parametrize(
    "roots, error_type",
    [
        (
            [
                _board_milestone(1, blocked_by=(_plan_id(2),)),
                _board_milestone(2, blocked_by=(_plan_id(1),)),
            ],
            "DependencyCycleError",
        ),
        ([_board_milestone(1, card_id="not-a-uuid")], "ValueError"),
        (
            [
                _board_milestone(1, title="Milestone twin"),
                _board_milestone(
                    2, card_id="00000001-0000-4000-8000-000000000001", title="Milestone twin"
                ),
            ],
            "ValueError",
        ),
    ],
    ids=["milestone-cycle", "non-uuid-milestone", "shared-derived-prefix"],
)
def test_a_board_dry_run_refusal_is_an_envelope_and_writes_nothing(
    tmp_path, monkeypatch, roots, error_type
):
    """Review focus: a cycle among milestones, a milestone id `dag.task_stem`
    cannot read, and two milestones deriving one prefix are each the same
    `HANDLED` refusal the real run gives: `ok: false`, exit 3."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _serve_roots(monkeypatch, roots)
    _forbid_board_dry_run_writes(monkeypatch)

    error = _refusal(_board_dry_run(tmp_path))

    assert error["type"] == error_type
    assert list(paths.data_dir().iterdir()) == []


def _board_payload(*statuses: str) -> dict[str, Any]:
    """`run_board`'s payload shape with one milestone entry per status."""
    entries = [
        {
            "milestone_id": _plan_id(index + 1),
            "status": status,
            "run_id": f"20261001T000000Z-{index + 1:08x}",
        }
        for index, status in enumerate(statuses)
    ]
    return {
        "ok": all(status == "done" for status in statuses),
        "board": True,
        "levels": (
            [{"level": 0, "milestones": [entry["milestone_id"] for entry in entries]}]
            if entries
            else []
        ),
        "milestones": entries,
    }


def _board_run(tmp_path: Path, *extra: str):
    return runner.invoke(
        cli.app,
        ["run", "--board", "--repo-dir", str(tmp_path), "--base-branch", "main", *extra],
    )


def _patch_run_board(monkeypatch, outcome: Any) -> list[dict[str, Any]]:
    """Replace `orchestrate.run_board`, forbid every other run path, record calls.

    `outcome` is returned, or raised when it is an exception instance.
    """
    _forbid_board_paths(monkeypatch)
    calls: list[dict[str, Any]] = []

    def fake_run_board(**kwargs):
        calls.append(kwargs)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(orchestrate, "run_board", fake_run_board)
    return calls


def test_a_board_run_calls_run_board_once_with_the_run_options(tmp_path, monkeypatch):
    """Spec tests 5 and 8: base branch, verify order, the opt-out and the default
    lane count reach `run_board` unchanged, with no `runner_factory` or `driver`
    (the kwargs are compared whole, so an extra key fails). With --branch-prefix
    omitted, each milestone's prefix is its own stem."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_board(monkeypatch, _board_payload("done"))

    result = _board_run(
        tmp_path,
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
        "--allow-no-verification",
    )

    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    prefix_of = kwargs.pop("branch_prefix_of")
    assert kwargs == {
        "repo_dir": tmp_path,
        "base_branch": "main",
        "commands": ["uv run pytest", "uv run ruff check"],
        "allow_no_verification": True,
        "max_concurrent": cli.DEFAULT_MAX_CONCURRENT,
    }
    assert prefix_of(BOARD_CARD) == "milestone-14-run-the-cbe34d00"


def test_a_board_run_with_a_prefix_hands_run_board_prefix_dash_stem(tmp_path, monkeypatch):
    """Spec test 6: never the given prefix verbatim."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_board(monkeypatch, _board_payload("done"))

    result = _board_run(tmp_path, "--branch-prefix", "sprint9")

    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    assert kwargs["branch_prefix_of"](BOARD_CARD) == "sprint9-milestone-14-run-the-cbe34d00"
    assert kwargs["branch_prefix_of"](BOARD_CARD) != "sprint9"


@pytest.mark.parametrize("given, passed", [("1", 1), ("2", 2), ("7", 7)])
def test_an_explicit_max_concurrent_reaches_run_board(tmp_path, monkeypatch, given, passed):
    """Spec test 8: in board mode the flag is the board-wide story bound."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_board(monkeypatch, _board_payload("done"))

    result = _board_run(tmp_path, "--max-concurrent", given)

    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    assert kwargs["max_concurrent"] == passed


def test_a_board_run_without_verify_passes_an_empty_list_and_no_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_board(monkeypatch, _board_payload("done"))

    result = _board_run(tmp_path)

    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    assert kwargs["commands"] == []
    assert kwargs["allow_no_verification"] is False


def test_a_board_run_prints_run_boards_payload_in_the_ok_envelope(tmp_path, monkeypatch):
    """Spec test 10: the payload unchanged under `data`; --pretty indents the same JSON."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    payload = _board_payload("done", "done")
    _patch_run_board(monkeypatch, payload)

    plain = _board_run(tmp_path)
    pretty = _board_run(tmp_path, "--pretty")

    assert plain.exit_code == 0, plain.output
    assert "\n" not in plain.stdout.strip()
    assert json.loads(plain.stdout) == cli.ok_envelope(payload)
    assert pretty.exit_code == 0, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


@pytest.mark.parametrize(
    "statuses, exit_code",
    [
        (("done",), 0),
        (("done", "done"), 0),
        ((), 0),
        (("stopped",), 0),
        (("cancelled",), 0),
        (("done", "stopped", "blocked"), 0),
        (("cancelled", "blocked"), 0),
        (("escalated",), cli.EXIT_ESCALATED),
        (("done", "escalated"), cli.EXIT_ESCALATED),
        (("escalated", "blocked"), cli.EXIT_ESCALATED),
        (("stopped", "escalated", "cancelled"), cli.EXIT_ESCALATED),
    ],
)
def test_a_board_run_exits_escalated_only_when_some_milestone_escalated(
    tmp_path, monkeypatch, statuses, exit_code
):
    """Spec test 11: the board-wide form of the milestone rule. A stopped,
    cancelled or blocked milestone is not an escalation; an empty board is clean.
    The envelope is `ok: true` either way: an escalation is a truthful result."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    payload = _board_payload(*statuses)
    _patch_run_board(monkeypatch, payload)

    result = _board_run(tmp_path)

    assert result.exit_code == exit_code, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(payload)


@pytest.mark.parametrize(
    "error",
    [
        ValueError("max_concurrent must be at least 1, got 0"),
        dag.DependencyCycleError("dag: dependency cycle among milestones #a, #b"),
        board.BoardError("brd refused", argv=["brd", "tree"]),
        cli.CliError("run 20261001T000000Z-00000001 already claims branch:m-integrate"),
    ],
    ids=["ValueError", "DependencyCycleError", "BoardError", "CliError"],
)
def test_a_handled_error_from_a_board_run_is_an_envelope(tmp_path, monkeypatch, error):
    """Spec test 12: every `HANDLED` refusal is `ok: false` at exit 3."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_board(monkeypatch, error)

    refusal = _refusal(_board_run(tmp_path))

    assert refusal["type"] == type(error).__name__
    assert refusal["message"] == str(error)


def test_an_unhandled_error_from_a_board_run_crashes_loudly(tmp_path, monkeypatch):
    """Review focus: anything outside `HANDLED` is a bug and keeps its traceback."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_board(monkeypatch, RuntimeError("boom"))

    result = _board_run(tmp_path)

    assert isinstance(result.exception, RuntimeError)
    assert '"ok"' not in result.stdout


def test_the_run_examples_and_help_show_the_board_mode():
    """Spec scope: `--board` is documented in `--help` and in the examples epilog."""
    assert "am run --board" in cli.RUN_EXAMPLES

    result = runner.invoke(cli.app, ["run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--board" in result.output


@pytest.mark.parametrize(
    "first", ["agent_manager.cli", "agent_manager.orchestrate", "agent_manager.integration"]
)
def test_cli_and_orchestrate_import_cleanly_in_either_order(first):
    """Review focus: `orchestrate` imports `cli` at module level, so `cli` must
    not import `orchestrate` at load time. A fresh interpreter, so this test
    does not depend on what earlier tests already imported."""
    code = (
        f"import {first}\n"
        "from agent_manager import cli, integration, orchestrate\n"
        "assert orchestrate.cli is cli\n"
        "assert orchestrate.integration is integration\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr


@pytest.fixture
def projection(tmp_path, monkeypatch) -> Path:
    """A project root whose SQLite projection is written directly.

    `status` and `runs` read the projection and nothing else -- no board, no git,
    no worktree -- so this steps-tier fixture is just a directory plus an
    `XDG_DATA_HOME` in `tmp_path`, and these tests need neither `git` nor `brd`.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "recorded"
    root.mkdir()
    return root


def _recorded_dispatch(run_id: str) -> models.Dispatch:
    return models.Dispatch(
        harness="claude",
        model="sonnet",
        role="coder",
        cwd=Path("/repo"),
        prompt_path=paths.run_dir(run_id) / "prompt.txt",
        result_path=paths.run_dir(run_id) / "result.json",
    )


def _record(
    root: Path,
    run_id: str,
    *,
    started_at: datetime,
    status: str = "done",
    with_phases: bool = True,
) -> None:
    """One run -- story, subtask, and optionally two phases and two attempts --
    in `root`'s projection, written the only way this program writes rows."""
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status=status,
                started_at=started_at,
            )
        )
        opened.record_story(
            models.StoryRun(card_id="story-1", title="The CLI", level=0, status=status)
        )
        opened.record_subtask(
            "story-1",
            models.SubtaskRun(
                card_id="card-1",
                branch="m1/task-x",
                base_branch="main",
                status=status,
                worktree_path=root / ".claude" / "worktrees" / "m1" / "task-x",
            ),
        )
        if not with_phases:
            return
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="explore", kind="agent", status="done"),
        )
        opened.record_attempt(
            "story-1",
            "card-1",
            "explore",
            models.Attempt(n=1, dispatch=_recorded_dispatch(run_id), status="gate_failed"),
        )
        opened.record_attempt(
            "story-1",
            "card-1",
            "explore",
            models.Attempt(n=2, dispatch=_recorded_dispatch(run_id), status="ok"),
        )
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        )
    finally:
        opened.close()


RECORDED_AT = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def test_status_prints_a_row_for_every_recorded_attempt(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(
        cli.app,
        ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)],
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["run"]["id"] == "20260923T090000Z-cbe34d00"
    assert envelope["data"]["run"]["workflow"] == "task"
    assert [
        (row["story"], row["subtask"], row["phase"], row["attempt"], row["state"])
        for row in envelope["data"]["rows"]
    ] == [
        ("story-1", "card-1", "explore", 1, "gate_failed"),
        ("story-1", "card-1", "explore", 2, "ok"),
        ("story-1", "card-1", "verify", None, "pending"),
    ]
    assert "\n" not in result.stdout.strip()


def test_status_with_no_run_id_renders_the_most_recent_run(projection):
    _record(
        projection,
        "20260921T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
    )
    _record(
        projection,
        "20260924T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc),
    )
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(cli.app, ["status", "--repo-dir", str(projection)])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["data"]["run"]["id"] == "20260924T090000Z-cbe34d00"


def test_status_for_an_unknown_run_id_is_an_envelope(projection):
    """And looking a run up must not mint the run directory a `Journal` would."""
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(
        cli.app, ["status", "no-such-run", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]
    assert str(projection.resolve()) in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()


def test_status_with_no_run_id_against_a_project_with_no_runs_is_an_envelope(projection):
    result = runner.invoke(cli.app, ["status", "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "most recent" in envelope["error"]["message"]


def test_status_of_a_run_that_died_before_its_first_phase_is_ok_with_no_rows(projection):
    """`run_card` writes the run, story and subtask rows before the walk starts
    (cli.py:228-230) exactly so `status` can see a run that died on its first
    dispatch. That reading is a fact, not an error."""
    _record(
        projection,
        "20260923T090000Z-cbe34d00",
        started_at=RECORDED_AT,
        status="escalated",
        with_phases=False,
    )

    result = runner.invoke(
        cli.app, ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["run"]["status"] == "escalated"
    assert envelope["data"]["rows"] == []
    assert envelope["data"]["stories"][0]["subtasks"][0]["card_id"] == "card-1"


def test_status_pretty_indents_the_same_envelope(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    plain = runner.invoke(
        cli.app, ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)]
    )
    pretty = runner.invoke(
        cli.app,
        ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection), "--pretty"],
    )

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


def test_runs_lists_the_projects_history_newest_first(projection):
    _record(
        projection,
        "20260921T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
    )
    _record(
        projection,
        "20260924T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc),
    )
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert [entry["id"] for entry in envelope["data"]["runs"]] == [
        "20260924T090000Z-cbe34d00",
        "20260923T090000Z-cbe34d00",
        "20260921T090000Z-cbe34d00",
    ]
    assert envelope["data"]["runs"][0]["workflow"] == "task"
    assert envelope["data"]["runs"][0]["status"] == "done"
    assert "2026-09-24" in envelope["data"]["runs"][0]["started_at"]
    assert envelope["data"]["runs"][0]["repo_dir"] == str(projection.resolve())


def test_runs_on_a_project_that_has_never_been_run_is_ok_and_empty(projection):
    """A project nobody has run yet is a fact, not a fault."""
    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["runs"] == []


def test_runs_agrees_with_status_about_the_most_recent_run(projection):
    _record(
        projection,
        "20260921T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
    )
    _record(
        projection,
        "20260924T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc),
    )

    listed = json.loads(
        runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)]).stdout
    )
    reported = json.loads(
        runner.invoke(cli.app, ["status", "--repo-dir", str(projection)]).stdout
    )

    assert listed["data"]["runs"][0]["id"] == reported["data"]["run"]["id"]


def test_runs_pretty_indents_the_same_envelope(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    plain = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])
    pretty = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection), "--pretty"])

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


def test_a_missing_repo_dir_is_an_envelope_for_both_read_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    missing = tmp_path / "missing"

    for argv in (["status", "--repo-dir", str(missing)], ["runs", "--repo-dir", str(missing)]):
        result = runner.invoke(cli.app, argv)
        assert result.exit_code == cli.EXIT_ERROR, argv
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is False
        assert envelope["error"]["type"] == "RepoDirError"
        assert "missing" in envelope["error"]["message"]


def test_a_repo_dir_that_is_a_file_is_an_envelope_for_both_commands(tmp_path, monkeypatch):
    """`resolve_repo_dir` checks `is_dir`, not `exists`: a file that exists must
    be refused before `paths.project_db_path` hashes it into a database name."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    not_a_dir = tmp_path / "README.md"
    not_a_dir.write_text("not a repo\n", encoding="utf-8")

    for argv in (
        ["status", "--repo-dir", str(not_a_dir)],
        ["runs", "--repo-dir", str(not_a_dir)],
    ):
        result = runner.invoke(cli.app, argv)
        assert result.exit_code == cli.EXIT_ERROR, argv
        assert json.loads(result.stdout)["error"]["type"] == "RepoDirError"


def _write_logs_attempt(
    run_id: str, phase: str, n: int, *, stdout: bool = True
) -> models.Attempt:
    """One attempt's three files on disk, plus the row that points at them.

    The *test* calls `paths.attempt_dir` -- which creates the directory -- because
    in production `dispatch.AgentRunner` is what creates it. `logs` itself must
    never call it, and `test_logs_writes_nothing` is what pins that.
    """
    directory = paths.attempt_dir(run_id, "card-1", phase, n)
    (directory / "prompt.txt").write_text(f"prompt for {phase}.{n}\n", encoding="utf-8")
    (directory / "result.json").write_text(
        json.dumps({"phase": phase, "attempt": n}), encoding="utf-8"
    )
    if stdout:
        (directory / "stdout.log").write_text(f"stdout of {phase}.{n}\n", encoding="utf-8")
    return models.Attempt(
        n=n,
        dispatch=_recorded_dispatch(run_id),
        status="ok" if n > 1 else "gate_failed",
        exit_code=0 if n > 1 else 1,
        prompt_path=directory / "prompt.txt",
        result_path=directory / "result.json",
        stdout_path=directory / "stdout.log",
    )


def _record_for_logs(root: Path, run_id: str, *, stdout: bool = True) -> None:
    """A run with two agent phases (two attempts, then one) and a pending phase.

    The trailing `verify` phase has no attempts, so the no-flag default has to
    skip it to reach `implement`.
    """
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="done",
                started_at=RECORDED_AT,
            )
        )
        opened.record_story(
            models.StoryRun(card_id="story-1", title="The CLI", level=0, status="done")
        )
        opened.record_subtask(
            "story-1",
            models.SubtaskRun(
                card_id="card-1", branch="m1/task-x", base_branch="main", status="done"
            ),
        )
        opened.record_phase(
            "story-1", "card-1", models.PhaseRun(name="explore", kind="agent", status="done")
        )
        opened.record_attempt(
            "story-1", "card-1", "explore", _write_logs_attempt(run_id, "explore", 1)
        )
        opened.record_attempt(
            "story-1", "card-1", "explore", _write_logs_attempt(run_id, "explore", 2)
        )
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="implement", kind="agent", status="done"),
        )
        opened.record_attempt(
            "story-1",
            "card-1",
            "implement",
            _write_logs_attempt(run_id, "implement", 1, stdout=stdout),
        )
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        )
    finally:
        opened.close()


LOGS_RUN_ID = "20260923T090000Z-cbe34d00"


def test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["run_id"] == LOGS_RUN_ID
    assert data["story_id"] == "story-1"
    assert data["card"] == "card-1"
    assert data["phase"] == "implement"
    assert data["attempt"] == 1
    assert data["artifacts"]["prompt"]["text"] == "prompt for implement.1\n"
    assert data["artifacts"]["result"]["text"] == '{"phase": "implement", "attempt": 1}'
    assert data["artifacts"]["stdout"]["text"] == "stdout of implement.1\n"
    assert "\n" not in result.stdout.strip()


def test_logs_phase_and_attempt_together_select_an_earlier_attempt(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app,
        [
            "logs",
            LOGS_RUN_ID,
            "card-1",
            "--phase",
            "explore",
            "--attempt",
            "1",
            "--repo-dir",
            str(projection),
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)["data"]
    assert data["phase"] == "explore"
    assert data["attempt"] == 1
    assert data["status"] == "gate_failed"
    assert data["exit_code"] == 1
    assert data["artifacts"]["prompt"]["text"] == "prompt for explore.1\n"


def test_logs_pretty_indents_the_same_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    plain = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )
    pretty = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection), "--pretty"]
    )

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


def test_logs_reports_an_attempt_whose_stdout_was_never_written(projection):
    """An attempt that exists is always reportable: the missing file is the
    finding, not a reason to refuse."""
    _record_for_logs(projection, LOGS_RUN_ID, stdout=False)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["artifacts"]["prompt"]["present"] is True
    assert data["artifacts"]["stdout"]["present"] is False
    assert data["artifacts"]["stdout"]["text"] is None
    assert data["artifacts"]["stdout"]["path"].endswith("stdout.log")


def test_logs_for_an_unknown_run_is_an_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app, ["logs", "no-such-run", "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]


def test_logs_for_a_card_that_is_not_in_the_run_is_an_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-9", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownCardError"
    assert "card-9" in envelope["error"]["message"]
    assert LOGS_RUN_ID in envelope["error"]["message"]


def test_logs_for_an_unknown_phase_or_attempt_is_an_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    base = ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]

    unknown_phase = runner.invoke(cli.app, [*base, "--phase", "reveiw"])
    assert unknown_phase.exit_code == cli.EXIT_ERROR
    phase_envelope = json.loads(unknown_phase.stdout)
    assert phase_envelope["error"]["type"] == "UnknownPhaseError"
    assert "reveiw" in phase_envelope["error"]["message"]
    assert "implement" in phase_envelope["error"]["message"]

    unknown_attempt = runner.invoke(cli.app, [*base, "--phase", "explore", "--attempt", "9"])
    assert unknown_attempt.exit_code == cli.EXIT_ERROR
    attempt_envelope = json.loads(unknown_attempt.stdout)
    assert attempt_envelope["error"]["type"] == "UnknownAttemptError"
    assert "9" in attempt_envelope["error"]["message"]


def test_logs_with_a_repo_dir_that_is_not_a_directory_is_an_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(tmp_path / "missing")]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "RepoDirError"
    assert "missing" in envelope["error"]["message"]


def _runs_snapshot() -> dict[str, bytes]:
    """Every path under `XDG_DATA_HOME/agent-manager/runs`, with file contents.

    Only the `runs` tree: the SQLite projection's own `-wal` and `-shm` sidecars
    come and go with any reader, including a legitimate read-only one, so the
    `attempts` table is snapshotted as rows instead.
    """
    root = paths.data_dir() / "runs"
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else b"<dir>"
        for path in sorted(root.rglob("*"))
    }


def _attempt_rows(root: Path) -> list[tuple]:
    conn = sqlite3.connect(paths.project_db_path(root))
    try:
        return conn.execute(
            "SELECT run_id, story_id, card_id, phase, n, status, prompt_path,"
            " result_path, stdout_path FROM attempts ORDER BY phase, n"
        ).fetchall()
    finally:
        conn.close()


def test_logs_writes_nothing(projection):
    """`logs` is read-only: it opens no `Journal`, records nothing, and never
    calls `paths.attempt_dir` or `paths.run_dir`, both of which mkdir as a side
    effect. A refusal for an unknown run must leave no `runs/<run-id>` behind."""
    _record_for_logs(projection, LOGS_RUN_ID)
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    success = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )
    assert success.exit_code == 0
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before

    refusal = runner.invoke(
        cli.app, ["logs", "no-such-run", "card-1", "--repo-dir", str(projection)]
    )
    assert refusal.exit_code == cli.EXIT_ERROR
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()


CRASHED_AT = datetime(2026, 9, 23, 11, 30, 0, tzinfo=timezone.utc)
"""The clock `_crash_mid_phase` injects, so the run id is known without reading
a payload the crash never produced."""


def recording_runner(
    *,
    store,
    run_id: str,
    story_id: str,
    card_id: str,
    crash_at: str | None = None,
    seen: list[str] | None = None,
    crash_with: type[BaseException] = KeyboardInterrupt,
):
    """A fake `engine.AgentPhaseRunner` that writes the rows a real one writes.

    §14's Engine tier: no adapter, no launcher, no harness process. It does
    record what `dispatch.AgentRunner` records -- the phase `started`, then an
    attempt `started` before the dispatch, then the terminal pair -- and numbers
    its attempt directories with the real `dispatch.next_attempt`, because that
    ordering is exactly what `resume` has to find and repair.

    `crash_at` raises `KeyboardInterrupt` in the window between the two writes: a
    `BaseException`, so it escapes `engine.run_subtask`'s `except Exception` the
    way `kill -INT` escapes it, leaving subtask, phase and attempt all `started`.

    `crash_with` is the type raised. The pygents walk needs a plain
    `BaseException` subclass (`_Killed`): asyncio re-raises `KeyboardInterrupt`
    out of the event loop before the engine unwinds (tests/runtime/test_resume.py).
    """

    def runner(phase, context, rendered):
        if seen is not None:
            seen.append(phase.name)
        n = dispatch.next_attempt(run_id, card_id, phase.name)
        directory = paths.attempt_dir(run_id, card_id, phase.name, n)
        prompt_path = directory / "prompt.txt"
        prompt_path.write_text(rendered.text, encoding="utf-8")
        attempt = models.Attempt(
            n=n,
            dispatch=models.Dispatch(
                harness="fake",
                model="fake",
                role=phase.role,
                cwd=Path(context["worktree"]),
                prompt_path=prompt_path,
                result_path=directory / "result.json",
            ),
            status="started",
            prompt_path=prompt_path,
        )
        store.record_phase(
            story_id, card_id, models.PhaseRun(name=phase.name, kind="agent", status="started")
        )
        store.record_attempt(story_id, card_id, phase.name, attempt)
        if crash_at is not None and phase.name == crash_at:
            raise crash_with(f"simulated kill during {phase.name}")
        store.record_attempt(
            story_id,
            card_id,
            phase.name,
            attempt.model_copy(update={"status": "ok", "exit_code": 0}),
        )
        store.record_phase(
            story_id, card_id, models.PhaseRun(name=phase.name, kind="agent", status="done")
        )
        if phase.name == "explore":
            return dict(EXPLORE_RESULT)
        return _canned_agent_result(phase, context)

    return runner


def _resume_factory(
    seen: list[str] | None = None,
    crash_at: str | None = None,
    crash_with: type[BaseException] = KeyboardInterrupt,
):
    """A `cli.RunnerFactory` handing `recording_runner` the store the CLI opened."""

    def factory(*, store, run_id, story_id, card_id):
        return recording_runner(
            store=store,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            crash_at=crash_at,
            seen=seen,
            crash_with=crash_with,
        )

    return factory


def test_resume_of_an_unknown_run_is_an_envelope(projection):
    result = runner.invoke(
        cli.app, ["resume", "no-such-run", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]


@pytest.mark.parametrize("status", ["done", "escalated"])
def test_resume_of_a_run_with_nothing_in_flight_is_an_envelope(projection, status):
    """A finished run has nothing to pick up and an escalated one is `retry`'s
    job; both are the same refusal, and the message is what tells them apart."""
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT, status=status)

    result = runner.invoke(
        cli.app, ["resume", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "NotResumableError"
    assert status in envelope["error"]["message"]


def test_resume_with_a_repo_dir_that_is_not_a_directory_is_an_envelope(tmp_path, monkeypatch):
    """Review Focus: `--repo-dir` is refused before any projection is opened, the
    same way it is for every other command."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = runner.invoke(
        cli.app, ["resume", "any-run", "--repo-dir", str(tmp_path / "missing")]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "RepoDirError"
    assert "missing" in envelope["error"]["message"]


def test_resume_writes_nothing_when_it_refuses(projection):
    """§9's refusal rule: `Store.open` constructs a `Journal` and mints a run
    directory, so a command that declined to resume must never have reached it."""
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    refusal = runner.invoke(
        cli.app, ["resume", "no-such-run", "--repo-dir", str(projection)]
    )

    assert refusal.exit_code == cli.EXIT_ERROR
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before


def test_status_rows_and_header_show_a_stopped_run_verbatim():
    run = _pure_run(
        [
            models.StoryRun(
                card_id="story-1",
                title="One",
                level=0,
                status="stopped",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-1",
                        branch="m1/a",
                        base_branch="main",
                        status="stopped",
                        phases=[
                            models.PhaseRun(name="implement", kind="agent", status="stopped")
                        ],
                    )
                ],
            )
        ]
    ).model_copy(update={"status": "stopped"})

    assert cli.status_rows(run) == [
        {
            "story": "story-1",
            "subtask": "card-1",
            "phase": "implement",
            "attempt": None,
            "state": "stopped",
        }
    ]
    assert cli.status_payload(run)["run"]["status"] == "stopped"


def test_a_stopped_card_run_is_ok_true_and_exit_zero(tmp_path, monkeypatch):
    def fake_run_card(card_id, **kwargs):
        return {**_fake_payload(card_id, "story-1"), "status": "stopped"}

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(tmp_path, "cbe34d00-9d8d-4f41-9c94-f99e665771b0")

    assert result.exit_code == 0, result.output
    assert result.exit_code != cli.EXIT_ESCALATED
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "stopped"


def test_a_resumed_walk_that_stops_is_ok_true_and_exit_zero(tmp_path, monkeypatch):
    def fake_resume_run(run_id, **kwargs):
        return {"run_id": run_id, "status": "stopped"}

    monkeypatch.setattr(cli, "resume_run", fake_resume_run)
    result = runner.invoke(
        cli.app, ["resume", "20260923T140506Z-cbe34d00", "--repo-dir", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "stopped"


# ── pygents resume and relaunch (card 02890d5d) ──────────────────────────────


def test_select_resumable_on_pygents_returns_a_lone_stopped_subtask():
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    stopped = _pure_subtask("card-2", []).model_copy(update={"status": "stopped"})
    run = _pure_run([_pure_story("story-1", [done]), _pure_story("story-2", [stopped])])

    story, subtask = cli.select_resumable(run)

    assert story.card_id == "story-2"
    assert subtask is stopped


def test_select_resumable_on_pygents_still_returns_a_lone_started_subtask():
    started = _pure_subtask("card-1", [])
    run = _pure_run([_pure_story("story-1", [started])])

    story, subtask = cli.select_resumable(run)

    assert story.card_id == "story-1"
    assert subtask is started


def test_select_resumable_on_pygents_refuses_nothing_in_flight_without_the_relaunch_remedy():
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    escalated = _pure_subtask("card-2", []).model_copy(update={"status": "escalated"})
    run = _pure_run([_pure_story("story-1", [done, escalated])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    message = str(caught.value)
    assert "no subtask recorded 'started' or 'stopped'" in message
    assert "found: card-1=done, card-2=escalated" in message
    assert "agent-manager status" in message
    assert "run --milestone" not in message


def test_select_resumable_on_pygents_refuses_a_lone_escalated_subtask():
    escalated = _pure_subtask("card-1", []).model_copy(update={"status": "escalated"})
    run = _pure_run([_pure_story("story-1", [escalated])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    assert "card-1=escalated" in str(caught.value)


def test_select_resumable_on_pygents_refuses_a_started_and_a_stopped_subtask_together():
    started = _pure_subtask("card-1", [])
    stopped = _pure_subtask("card-2", []).model_copy(update={"status": "stopped"})
    run = _pure_run([_pure_story("story-1", [started]), _pure_story("story-2", [stopped])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run)

    message = str(caught.value)
    assert "2 subtasks recorded 'started' or 'stopped' (card-1, card-2)" in message


CHECKPOINT_AT = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)


def _checkpoint(
    reason: str,
    *,
    digest: str | None = None,
    current: str | None = None,
    queue: tuple[str, ...] = (),
) -> store_module.Checkpoint:
    """A hand-built checkpoint row of `TASK`: `current` is the turn in flight, `queue` the turns after it."""
    return store_module.Checkpoint(
        run_id="20260926T090000Z-02890d5d",
        card_id="card-1",
        seq=4,
        workflow=task_workflow.TASK.name,
        digest=task_workflow.TASK.digest() if digest is None else digest,
        reason=reason,
        agent={
            "current_turn": None if current is None else {"kwargs": {"phase": current, "loop": 0}},
            "queue": [{"kwargs": {"phase": name, "loop": 0}} for name in queue],
        },
        saved_at=CHECKPOINT_AT,
    )


def test_drive_subtask_hands_resume_from_to_the_pygents_walk(monkeypatch):
    walks = _record_walks(monkeypatch)
    factory, _runner = _recording_factory([])
    checkpoint = _checkpoint("parked", queue=("plan",))

    cli.drive_subtask(
        store=object(),
        run_id=DRIVE_RUN_ID,
        card=DRIVE_CARD,
        parent=DRIVE_PARENT,
        subtask=_drive_row(),
        repo_dir=DRIVE_REPO,
        runner_factory=factory,
        resume_from=checkpoint,
    )

    ((workflow, _store, kwargs),) = walks
    assert workflow is task_workflow.TASK
    assert kwargs["resume_from"] is checkpoint


def test_checkpoint_resume_phase_is_the_queue_head_of_a_parked_row():
    phase = cli.checkpoint_resume_phase(
        _checkpoint("parked", queue=("validate_plan", "implement")),
        card_id="card-1",
        run_id="run-1",
    )

    assert phase == "validate_plan"


def test_checkpoint_resume_phase_prefers_the_turn_in_flight():
    phase = cli.checkpoint_resume_phase(
        _checkpoint("turn", current="plan", queue=("validate_plan",)),
        card_id="card-1",
        run_id="run-1",
    )

    assert phase == "plan"


def test_checkpoint_resume_phase_refuses_a_card_with_no_checkpoint():
    with pytest.raises(cli.NotResumableError) as caught:
        cli.checkpoint_resume_phase(None, card_id="card-1", run_id="run-1")

    message = str(caught.value)
    assert "card-1" in message
    assert "no checkpoint" in message


def test_checkpoint_resume_phase_refuses_a_done_row_before_judging_its_digest():
    with pytest.raises(cli.NotResumableError) as caught:
        cli.checkpoint_resume_phase(
            _checkpoint("done", digest="saved-under-another-task"),
            card_id="card-1",
            run_id="run-1",
        )

    assert not isinstance(caught.value, cli.CheckpointMismatchError)
    assert "'done'" in str(caught.value)


def test_checkpoint_resume_phase_refuses_a_changed_workflow_as_a_checkpoint_mismatch():
    with pytest.raises(cli.CheckpointMismatchError) as caught:
        cli.checkpoint_resume_phase(
            _checkpoint("parked", digest="saved-under-another-task", queue=("plan",)),
            card_id="card-1",
            run_id="run-1",
        )

    assert isinstance(caught.value, runtime_engine.CheckpointMismatch)
    assert isinstance(caught.value, cli.HANDLED)
    message = str(caught.value)
    assert "workflow changed since checkpoint" in message
    assert "saved-under-another-task" in message
    assert task_workflow.TASK.digest() in message


def test_checkpoint_resume_phase_refuses_an_escalated_row_with_no_turn_left():
    with pytest.raises(cli.NotResumableError) as caught:
        cli.checkpoint_resume_phase(
            _checkpoint("escalated"), card_id="card-1", run_id="run-1"
        )

    message = str(caught.value)
    assert "'escalated'" in message
    assert "no turn left" in message


def test_checkpoint_resume_phase_continues_an_escalated_row_that_still_holds_a_turn():
    """An error raised by the BEFORE_TURN hook escalates with the turn still
    queued (tests/runtime/test_checkpoint.py:174-209): that row is continuable."""
    phase = cli.checkpoint_resume_phase(
        _checkpoint("escalated", queue=("review",)), card_id="card-1", run_id="run-1"
    )

    assert phase == "review"


def _saved(
    opened: store_module.Store,
    card_id: str,
    reason: str,
    *,
    digest: str | None = None,
    queue: tuple[str, ...] = ("implement",),
    minute: int = 0,
) -> store_module.Checkpoint:
    return opened.save_checkpoint(
        card_id,
        workflow=task_workflow.TASK.name,
        digest=task_workflow.TASK.digest() if digest is None else digest,
        reason=reason,
        agent={
            "current_turn": None,
            "queue": [{"kwargs": {"phase": name, "loop": 0}} for name in queue],
        },
        saved_at=CHECKPOINT_AT.replace(minute=minute),
    )


def test_continuable_checkpoint_is_the_open_matching_row_or_none(projection):
    opened = store_module.Store.open(projection, "20260926T090000Z-02890d5d")
    try:
        parked = _saved(opened, "card-parked", "parked")
        _saved(opened, "card-changed", "parked", digest="saved-under-another-task")
        _saved(opened, "card-closed", "parked", minute=1)
        _saved(opened, "card-closed", "done", queue=(), minute=2)
        _saved(opened, "card-escalated", "escalated", queue=())
        found = {
            card: cli.continuable_checkpoint(opened, card)
            for card in (
                "card-parked",
                "card-changed",
                "card-closed",
                "card-escalated",
                "card-never-saved",
            )
        }
    finally:
        opened.close()

    got = found.pop("card-parked")
    assert got is not None
    assert (got.run_id, got.card_id, got.seq, got.reason) == (
        parked.run_id,
        "card-parked",
        parked.seq,
        "parked",
    )
    assert found == {
        "card-changed": None,
        "card-closed": None,
        "card-escalated": None,
        "card-never-saved": None,
    }


class _Killed(BaseException):
    """A process death mid-phase for the pygents walk. Not `KeyboardInterrupt`:
    asyncio re-raises that out of the event loop before the engine unwinds."""


RESUME_KEYS = {
    "run_id",
    "card_id",
    "story_id",
    "branch",
    "base_branch",
    "worktree",
    "status",
    "failed_phase",
    "detail",
    "skipped",
    "warnings",
    "resumed_from",
    "discarded_attempts",
}
"""Today's resume payload keys; the pygents branch adds and drops none (G10)."""


def _crash_pygents(project: Path, cards: dict[str, str], phase: str) -> str:
    """Drive a real pygents `run_card` until it is killed inside `phase`, and name the run."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    with pytest.raises(_Killed):
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            clock=lambda: CRASHED_AT,
            runner_factory=_resume_factory(crash_at=phase, crash_with=_Killed),
        )
    return run_id


def _checkpoint_rows(root: Path) -> list[tuple]:
    conn = sqlite3.connect(paths.project_db_path(root))
    try:
        return conn.execute(
            "SELECT run_id, card_id, seq, reason, digest FROM checkpoints"
            " ORDER BY run_id, card_id, seq"
        ).fetchall()
    finally:
        conn.close()


def _resume_state(root: Path) -> tuple:
    """Everything a refused resume must leave alone: the run tree on disk (the
    journal included), the attempt rows and the checkpoint rows."""
    return (_runs_snapshot(), _attempt_rows(root), _checkpoint_rows(root))


def _force_started(project: Path, run_id: str, card_id: str) -> None:
    """Re-record the subtask `started`: what a crash between the engine's closing
    checkpoint and the caller's final status write leaves behind."""
    root = cli.resolve_repo_dir(project)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    found = cli.find_subtask(run, card_id)
    assert found is not None
    story, subtask = found
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_subtask(story.card_id, subtask.model_copy(update={"status": "started"}))
    finally:
        opened.close()


def _plant_changed_digest(project: Path, run_id: str, card_id: str) -> None:
    """A newer copy of the newest checkpoint, saved under a digest `TASK` does not have."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        newest = opened.latest_checkpoint(card_id)
        assert newest is not None
        opened.save_checkpoint(
            card_id,
            workflow=newest.workflow,
            digest="saved-under-another-task",
            reason=newest.reason,
            agent=newest.agent,
            saved_at=datetime.now(timezone.utc),
        )
    finally:
        opened.close()


def _park_pygents(project: Path, cards: dict[str, str]) -> str:
    """A milestone run whose one subtask the run's `StopSignal` parked on pygents after `spec`.

    Recorded the way `orchestrate.run_story_lane` records it: the run is a
    `milestone` run, the engine records the subtask `stopped`, the lane
    records the story `stopped`.
    """
    root = cli.resolve_repo_dir(project)
    parent = board.show(cards["story"], repo_dir=root)
    card = board.show(cards["subtask"], repo_dir=root)
    run_id = cli.mint_run_id(cards["milestone"], CRASHED_AT)
    branch = dag.task_branch("m1", card)
    subtask = models.SubtaskRun(
        card_id=card.id,
        branch=branch,
        base_branch="main",
        status="started",
        worktree_path=cli.worktree_for(root, branch),
    )
    story = models.StoryRun(
        card_id=parent.id, title=parent.title, level=0, status="started", tip_branch=branch
    )
    seen: list[str] = []
    stop = StopSignal()
    record = _resume_factory(seen)

    def stopping_factory(**kwargs: Any):
        # Called by `drive_subtask_async` on its loop, where the signal lives.
        loop = asyncio.get_running_loop()
        run = record(**kwargs)

        def runner(phase, context, rendered):
            result = run(phase, context, rendered)
            if phase.name == "spec":
                # The runner is in a `to_thread` worker: hand `trigger` to the
                # loop and wait until it has run, so the agent is paused
                # before this phase returns.
                fired = threading.Event()

                def fire() -> None:
                    stop.trigger(parent.id)
                    fired.set()

                loop.call_soon_threadsafe(fire)
                if not fired.wait(5):
                    raise RuntimeError("the stop was never triggered")
            return result

        return runner

    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=orchestrate.MILESTONE_WORKFLOW,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=CRASHED_AT,
                config=models.RunConfig(),
            )
        )
        opened.record_story(story)
        opened.record_subtask(parent.id, subtask)
        drive = asyncio.run(
            cli.drive_subtask_async(
                store=opened,
                run_id=run_id,
                card=card,
                parent=parent,
                subtask=subtask,
                repo_dir=root,
                runner_factory=stopping_factory,
                stop=stop,
            )
        )
        assert drive.summary.status == "stopped"
        assert drive.summary.detail == "stopped before validate_spec"
        opened.record_story(story.model_copy(update={"status": "stopped"}))
    finally:
        opened.close()
    return run_id


@requires_git
@requires_brd
def test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint(project, cards):
    """Spec test 3: the runner sees `plan` next, never `explore` or `spec`."""
    run_id = _crash_pygents(project, cards, "plan")
    seen: list[str] = []

    payload = cli.resume_run(
        run_id, repo_dir=project, runner_factory=_resume_factory(seen)
    )

    assert seen[0] == "plan"
    assert not {"explore", "spec", "validate_spec"} & set(seen)
    assert payload["status"] == "done"
    assert payload["resumed_from"] == "plan"
    assert payload["run_id"] == run_id
    assert payload["card_id"] == cards["subtask"]
    assert payload["story_id"] == cards["story"]
    assert set(payload) == RESUME_KEYS
    assert board.show(cards["subtask"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_a_pygents_resume_marks_the_orphan_attempt_harness_error(project, cards):
    """Spec test 5: the orphan attempt is marked `harness_error`."""
    run_id = _crash_pygents(project, cards, "plan")

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["discarded_attempts"] == [{"phase": "plan", "n": 1}]
    plan = [row for row in _attempt_rows(project) if row[3] == "plan"]
    assert [(row[4], row[5]) for row in plan] == [(1, "harness_error"), (2, "ok")]
    assert [row for row in _attempt_rows(project) if row[5] == "started"] == []


@requires_git
@requires_brd
def test_a_task_resume_posts_its_runs_pending_comments_before_the_walk_goes_on(
    project, cards
):
    """Board-comments B7 (card 65ed3c70): a row the killed life queued but
    never posted is on the card before the resumed walk's first phase."""
    run_id = _crash_pygents(project, cards, "plan")
    key = f"{run_id}/{cards['subtask']}/escalated:an-earlier-life"
    body = f"am · escalated · run {run_id}\nphase: plan\nam-key: {key}"
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.enqueue_comment(
            run_id=run_id,
            card_id=cards["subtask"],
            key=key,
            body=body,
            now=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        )
    finally:
        opened.close()
    inner = _resume_factory()
    on_the_card_at_walk_start: list[list[str]] = []

    def factory(**kwargs):
        if not on_the_card_at_walk_start:
            on_the_card_at_walk_start.append(
                [c.body for c in board.comment_list(cards["subtask"], repo_dir=project)]
            )
        return inner(**kwargs)

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=factory)

    assert payload["status"] == "done"
    assert set(payload) == RESUME_KEYS
    (seen,) = on_the_card_at_walk_start
    assert [b.rstrip().endswith(f"am-key: {key}") for b in seen] == [True]
    found = board.comment_list(cards["subtask"], repo_dir=project)
    assert [c.body.rstrip().endswith(f"am-key: {key}") for c in found] == [True]
    assert found[0].author == "am"


@requires_git
@requires_brd
def test_a_task_resume_whose_start_flush_fails_warns_and_still_walks(
    project, cards, monkeypatch
):
    """Board-comments B7/B8 (card 65ed3c70): a board that refuses the resumed
    run's leftover row is a warning leading the payload's, never a refusal."""
    run_id = _crash_pygents(project, cards, "plan")
    key = f"{run_id}/{cards['subtask']}/escalated:an-earlier-life"
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.enqueue_comment(
            run_id=run_id,
            card_id=cards["subtask"],
            key=key,
            body=f"am · escalated · run {run_id}\nphase: plan\nam-key: {key}",
            now=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        )
    finally:
        opened.close()

    def down(card_id, *, repo_dir=None):
        raise board.BoardError(
            "brd is down", argv=["brd", "comment", "list", card_id], exit_code=1
        )

    monkeypatch.setattr(board, "comment_list", down)

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["status"] == "done"
    assert set(payload) == RESUME_KEYS
    ours = [w for w in payload["warnings"] if f"board comment {key} " in w]
    assert len(ours) == 1, payload["warnings"]
    assert "not posted" in ours[0] and "brd is down" in ours[0]
    assert payload["warnings"][0] == ours[0]


@requires_git
@requires_brd
def test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused(
    project, cards, monkeypatch
):
    """Spec test 1, parked half (card 54e4ec29): the run is a `milestone` run,
    so `resume` continues the milestone, and its parked subtask goes on from
    its checkpoint at `validate_spec`."""
    run_id = _park_pygents(project, cards)
    integrate = _integrate_ok(monkeypatch)

    after: list[str] = []
    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory(after))

    assert payload["done"] is True, payload
    assert payload["resumed"] is True
    assert payload["run_id"] == run_id
    assert payload["completed"] == [cards["subtask"]]
    assert after[0] == "validate_spec"
    assert not {"explore", "spec"} & set(after)
    assert [call["run_id"] for call in integrate] == [run_id]
    assert [row for row in _attempt_rows(project) if row[5] == "started"] == []
    assert board.show(cards["subtask"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_a_pygents_resume_of_a_done_checkpoint_writes_nothing(project, cards):
    """Spec test 7, second half: only the final status write was lost."""
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )
    assert payload["status"] == "done"
    _force_started(project, payload["run_id"], cards["subtask"])
    before = _resume_state(project)

    with pytest.raises(cli.NotResumableError) as caught:
        cli.resume_run(
            payload["run_id"],
            repo_dir=project,
            runner_factory=_Forbidden("runner_factory"),
        )

    assert "'done'" in str(caught.value)
    assert _resume_state(project) == before


@requires_git
@requires_brd
def test_a_pygents_resume_refuses_a_phase_escalation_and_writes_nothing(project, cards):
    """Replaces spec test 8 (plan deviation 1): a phase escalation's row holds
    no turn, so continuing it would record `done` with review never passed."""
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(fail="review"),
    )
    assert payload["status"] == "escalated"
    _force_started(project, payload["run_id"], cards["subtask"])
    before = _resume_state(project)

    with pytest.raises(cli.NotResumableError) as caught:
        cli.resume_run(
            payload["run_id"],
            repo_dir=project,
            runner_factory=_Forbidden("runner_factory"),
        )

    message = str(caught.value)
    assert "'escalated'" in message
    assert "no turn left" in message
    assert _resume_state(project) == before


@requires_git
@requires_brd
def test_a_pygents_resume_across_a_workflow_change_writes_nothing(project, cards):
    """Spec test 6 at the function, and Review Focus 3: the orphan attempt is
    still `started` afterwards, because nothing is re-marked before the
    checkpoint is judged."""
    run_id = _crash_pygents(project, cards, "plan")
    _plant_changed_digest(project, run_id, cards["subtask"])
    before = _resume_state(project)

    with pytest.raises(cli.CheckpointMismatchError) as caught:
        cli.resume_run(
            run_id,
            repo_dir=project,
            runner_factory=_Forbidden("runner_factory"),
        )

    assert "workflow changed since checkpoint" in str(caught.value)
    assert _resume_state(project) == before
    plan = [row for row in _attempt_rows(project) if row[3] == "plan"]
    assert [(row[4], row[5]) for row in plan] == [(1, "started")]


@requires_git
@requires_brd
def test_a_pygents_resume_across_a_workflow_change_is_an_envelope_at_exit_three(
    project, cards, monkeypatch
):
    """Spec test 6 at the command: `ok: false`, exit 3, nothing written."""
    run_id = _crash_pygents(project, cards, "plan")
    _plant_changed_digest(project, run_id, cards["subtask"])
    before = _resume_state(project)
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = runner.invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(project)]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CheckpointMismatchError"
    assert "workflow changed since checkpoint" in envelope["error"]["message"]
    assert _resume_state(project) == before


@requires_git
@requires_brd
def test_the_resume_command_prints_an_ok_envelope_and_exits_zero(project, cards, monkeypatch):
    """The factory is patched on the module rather than passed as an option: the
    injection seam is `cli.default_runner_factory`, and patching it is what
    proves the command reaches for that name."""
    run_id = _crash_pygents(project, cards, "plan")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "done"
    assert envelope["data"]["resumed_from"] == "plan"
    assert envelope["data"]["discarded_attempts"] == [{"phase": "plan", "n": 1}]
    assert "\n" not in result.stdout.strip()


@requires_git
@requires_brd
def test_resume_pretty_indents_the_same_envelope(project, cards, monkeypatch):
    run_id = _crash_pygents(project, cards, "plan")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(project), "--pretty"]
    )

    assert result.exit_code == 0, result.output
    assert "\n" in result.stdout.strip()
    assert json.loads(result.stdout)["data"]["status"] == "done"


@requires_git
@requires_brd
def test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one(project, cards, monkeypatch):
    """An escalation is a truthful result, so the envelope stays `ok: true` and
    the exit code carries the full stop -- exactly as `run` does."""
    run_id = _crash_pygents(project, cards, "plan")
    monkeypatch.setattr(
        cli, "default_runner_factory", lambda **kwargs: fake_runner(fail="review")
    )

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ESCALATED, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "escalated"
    assert envelope["data"]["failed_phase"] == "review"
    assert envelope["data"]["resumed_from"] == "plan"


@requires_git
@requires_brd
def test_resume_launches_no_harness(project, cards, monkeypatch):
    """§14's adapter rule at the resume seam: the launcher is injected, so a
    resume that got as far as launching one has already failed."""
    run_id = _crash_pygents(project, cards, "plan")

    def forbidden(*args, **kwargs):
        raise AssertionError("resume launched a harness process")

    monkeypatch.setattr(cli, "run_direct", forbidden)
    monkeypatch.setattr(cli.dispatch, "AgentRunner", forbidden)

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["status"] == "done"


@pytest.mark.parametrize("command", [["run", "--card", "cbe34d00"], ["resume", "any-run"]])
@pytest.mark.parametrize("value", ["yaml", "pygents"])
def test_the_removed_engine_flag_is_a_usage_error_that_starts_nothing(
    tmp_path, monkeypatch, command, value
):
    """Card 7a744199: `--engine` is gone from `run` and `resume`, so a script
    still passing it gets Typer's own exit 2, never an envelope, and nothing is
    driven or written."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "run_card", _Forbidden("run_card"))
    monkeypatch.setattr(cli, "resume_run", _Forbidden("resume_run"))

    result = runner.invoke(
        cli.app, [*command, "--repo-dir", str(tmp_path), "--engine", value]
    )

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert "--engine" in result.output
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.parametrize("command", ["run", "resume"])
def test_the_help_offers_no_engine_flag(command):
    result = runner.invoke(cli.app, [command, "--help"])

    assert result.exit_code == 0, result.output
    assert "--engine" not in result.output


# ── am resume on a milestone run (card 54e4ec29) ─────────────────────────────

MILESTONE_AT = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
"""The interrupted milestone run's clock, so its run id is known."""


def _integrate_ok(monkeypatch) -> list[dict[str, Any]]:
    """Replace `integration.integrate_milestone`, which `run_milestone` reads at
    call time, with a success that merges nothing; record every call."""
    calls: list[dict[str, Any]] = []

    def succeed(**kwargs: Any) -> integration.IntegrateSuccess:
        calls.append(kwargs)
        branch = integration.integration_branch(kwargs["branch_prefix"])
        return integration.IntegrateSuccess(
            branch=branch,
            worktree=cli.worktree_for(kwargs["repo_dir"], branch),
            merged=[story.id for story in kwargs["stories"] if story.subtasks],
        )

    monkeypatch.setattr(integration, "integrate_milestone", succeed)
    return calls


def _milestone_factory(seen: dict[str, list[str]], fail: dict[str, str] | None = None):
    """A `cli.RunnerFactory` for a whole milestone: `fake_runner` per card, each
    agent phase recorded under its card, and `fail[card]` failing that phase."""
    failing = dict(fail or {})

    def factory(*, store, run_id, story_id, card_id):
        inner = fake_runner(fail=failing.get(card_id))

        def runner(phase, context, rendered):
            seen.setdefault(card_id, []).append(phase.name)
            return inner(phase, context, rendered)

        return runner

    return factory


@pytest.fixture
def resume_board(project) -> dict[str, str]:
    """Milestone 4: story A (a1 then a2) and story B (b1), B blocked by A."""
    milestone = _add_card(project, "Milestone 4: resume")
    story_a = _add_card(project, "Story A: first", milestone)
    a1 = _add_card(project, "a1: first of A", story_a)
    a2 = _add_card(project, "a2: second of A", story_a)
    _block(project, a2, a1)
    story_b = _add_card(project, "Story B: second", milestone)
    b1 = _add_card(project, "b1: only of B", story_b)
    _block(project, story_b, story_a)
    return {
        "milestone": milestone,
        "story_a": story_a,
        "a1": a1,
        "a2": a2,
        "story_b": story_b,
        "b1": b1,
    }


def _escalate_milestone(project: Path, shape: dict[str, str]) -> str:
    """A real milestone run on the fake runner: a1 done, a2 escalated at
    `review`, B never started. Returns the run id."""
    payload = orchestrate.run_milestone(
        shape["milestone"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m4",
        runner_factory=_milestone_factory({}, fail={shape["a2"]: "review"}),
        clock=lambda: MILESTONE_AT,
        max_concurrent=2,
    )
    assert payload["escalated"] is True, payload
    assert (payload["subtask"], payload["failed_phase"]) == (shape["a2"], "review")
    return payload["run_id"]


def _plant_orphan(project: Path, run_id: str, story_id: str, card_id: str, phase: str) -> None:
    """An attempt left `started` by a kill mid-dispatch, as `dispatch.AgentRunner` records it."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.record_phase(
            story_id, card_id, models.PhaseRun(name=phase, kind="agent", status="started")
        )
        opened.record_attempt(
            story_id,
            card_id,
            phase,
            models.Attempt(n=1, dispatch=_recorded_dispatch(run_id), status="started"),
        )
    finally:
        opened.close()


def _project_run_ids(project: Path) -> list[str]:
    conn = sqlite3.connect(paths.project_db_path(project))
    try:
        return [row[0] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _loaded(project: Path, run_id: str) -> models.Run:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    return run


def _record_milestone(root: Path, run_id: str, *, status: str, workflow: str = "milestone") -> None:
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m4",
                status=status,
                started_at=RECORDED_AT,
            )
        )
    finally:
        opened.close()


@requires_git
@requires_brd
def test_a_milestone_that_escalated_resumes_under_its_own_run_id(
    project, resume_board, monkeypatch
):
    """Spec test 1, escalated half: a2 resumes at `review` with nothing before
    it re-dispatched, a1 (done) is not driven, b1 starts fresh, Integrate
    runs, and only this invocation's work is `completed`."""
    shape = resume_board
    run_id = _escalate_milestone(project, shape)
    integrate = _integrate_ok(monkeypatch)
    seen: dict[str, list[str]] = {}

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_milestone_factory(seen))

    assert payload["done"] is True, payload
    assert payload["resumed"] is True
    assert payload["run_id"] == run_id
    assert payload["completed"] == [shape["a2"], shape["b1"]]
    assert shape["a1"] not in seen
    assert seen[shape["a2"]][0] == "review"
    assert not {
        "explore",
        "spec",
        "validate_spec",
        "plan",
        "validate_plan",
        "implement",
    } & set(seen[shape["a2"]])
    assert seen[shape["b1"]][0] == "explore"
    assert [call["run_id"] for call in integrate] == [run_id]
    assert _project_run_ids(project) == [run_id]
    run = _loaded(project, run_id)
    assert run.status == "done"
    assert run.config.max_concurrent_stories == 2
    assert board.show(shape["a2"], repo_dir=project).status == "done"
    assert board.show(shape["b1"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_a_milestone_resume_marks_an_orphan_attempt_harness_error(
    project, resume_board, monkeypatch
):
    """Spec test 6: an attempt still `started` from the interrupted run."""
    shape = resume_board
    run_id = _escalate_milestone(project, shape)
    _plant_orphan(project, run_id, shape["story_a"], shape["a2"], "review")
    _integrate_ok(monkeypatch)

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_milestone_factory({}))

    assert payload["done"] is True, payload
    rows = [row for row in _attempt_rows(project) if row[2] == shape["a2"]]
    assert [(row[3], row[4], row[5]) for row in rows] == [("review", 1, "harness_error")]


@requires_git
@requires_brd
def test_a_milestone_resume_across_a_workflow_change_is_exit_three_and_writes_nothing(
    project, resume_board, monkeypatch
):
    """Spec test 3: one stale subtask refuses the whole resume; the orphan is
    still `started`, and no row, attempt, checkpoint, journal line or branch changed."""
    shape = resume_board
    run_id = _escalate_milestone(project, shape)
    _plant_orphan(project, run_id, shape["story_a"], shape["a2"], "review")
    _plant_changed_digest(project, run_id, shape["a2"])
    before = _resume_state(project)
    branches = _git(project, "branch", "--format=%(refname:short)")
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CheckpointMismatchError"
    message = envelope["error"]["message"]
    assert message.startswith("workflow changed since checkpoint")
    assert shape["a2"] in message
    assert "saved-under-another-task" in message
    assert task_workflow.TASK.digest() in message
    assert _resume_state(project) == before
    assert _git(project, "branch", "--format=%(refname:short)") == branches
    assert [row[5] for row in _attempt_rows(project) if row[2] == shape["a2"]] == ["started"]


def test_resuming_a_finished_milestone_run_is_exit_three_and_writes_nothing(
    projection, monkeypatch
):
    """Spec test 5: refused before the board is read or the store opened."""
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="done")
    before = (_runs_snapshot(), _attempt_rows(projection))
    monkeypatch.setattr(cli.board, "roots", _Forbidden("board.roots"))

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "NotResumableError"
    assert envelope["error"]["message"] == (
        f"run {run_id} finished; start new work with am run --milestone"
    )
    assert (_runs_snapshot(), _attempt_rows(projection)) == before


def test_resume_routes_a_task_run_to_the_single_subtask_path(projection, monkeypatch):
    """Spec test 8: a `task` run goes where it always went, with the same arguments."""
    run_id = "20260923T090000Z-cbe34d00"
    _record(projection, run_id, started_at=RECORDED_AT, status="started")
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def fake_resume(run, **kwargs):
        seen.append((run.id, run.workflow, kwargs))
        return {"status": "done"}

    monkeypatch.setattr(cli, "_resume_from_checkpoint", fake_resume)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))

    payload = cli.resume_run(run_id, repo_dir=projection)

    assert payload == {"status": "done"}
    assert seen == [
        (
            run_id,
            "task",
            {
                "root": projection.resolve(),
                "allow_no_verification": False,
                "commands": (),
                "runner_factory": None,
            },
        )
    ]


def test_resume_routes_a_milestone_run_to_run_milestone_under_its_own_id(
    projection, monkeypatch
):
    """Review Focus 5: `--verify` and the opt-out reach the milestone resume."""
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="escalated")
    calls: list[tuple[Any, dict[str, Any]]] = []

    def fake_run_milestone(milestone, **kwargs):
        calls.append((milestone, kwargs))
        return {"done": True, "run_id": run_id, "resumed": True}

    def factory(**kwargs):
        return None

    monkeypatch.setattr(orchestrate, "run_milestone", fake_run_milestone)
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))

    payload = cli.resume_run(
        run_id,
        repo_dir=projection,
        commands=("uv run pytest",),
        allow_no_verification=True,
        runner_factory=factory,
    )

    assert payload == {"done": True, "run_id": run_id, "resumed": True}
    assert calls == [
        (
            None,
            {
                "repo_dir": projection.resolve(),
                "commands": ["uv run pytest"],
                "allow_no_verification": True,
                "runner_factory": factory,
                "resume_run_id": run_id,
            },
        )
    ]


def test_resume_refuses_a_run_of_a_workflow_it_does_not_know(projection, monkeypatch):
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="started", workflow="integrate")
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))

    with pytest.raises(cli.NotResumableError, match="'integrate'"):
        cli.resume_run(run_id, repo_dir=projection)


@pytest.mark.parametrize(
    "payload, code",
    [
        ({"done": True, "run_id": "r", "resumed": True}, 0),
        ({"escalated": True, "run_id": "r", "resumed": True}, cli.EXIT_ESCALATED),
    ],
    ids=["done", "escalated"],
)
def test_the_resume_command_reads_a_milestone_payloads_escalated_flag(
    tmp_path, monkeypatch, payload, code
):
    """A milestone payload has no `status` key, as for `run --milestone`."""
    monkeypatch.setattr(cli, "resume_run", lambda run_id, **kwargs: payload)

    result = runner.invoke(
        cli.app, ["resume", "20260927T100000Z-cbe34d00", "--repo-dir", str(tmp_path)]
    )

    assert result.exit_code == code, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(payload)


# -- live control: am pause / am cancel / status control / resume guard -------
#
# Live control spec section 7 puts CLI refusals, idempotence, status and the
# resume guard here. Runs, leases and requests are planted straight into the
# projection through `store_module.open_db`, which is exactly how a second
# `am` process reaches them. No sleeps: `cli._utcnow` is frozen and every
# heartbeat is planted relative to it.

CONTROL_RUN_ID = "20260929T090000Z-cbe34d00"
CONTROL_NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
HERE = socket.gethostname()
CONTROL_KEYS = {
    "run_id",
    "command",
    "effective",
    "requested_at",
    "already_requested",
    "message",
}


def _at(seconds: float) -> datetime:
    return CONTROL_NOW + timedelta(seconds=seconds)


def _freeze_clock(monkeypatch, at: datetime = CONTROL_NOW) -> None:
    """Every `cli._utcnow()` call site reads the module global at call time."""
    monkeypatch.setattr(cli, "_utcnow", lambda: at)


def _plant_run(root: Path, *, status: str = "started", workflow: str = "task") -> None:
    if workflow == "task":
        _record(root, CONTROL_RUN_ID, started_at=RECORDED_AT, status=status, with_phases=False)
    else:
        _record_milestone(root, CONTROL_RUN_ID, status=status, workflow=workflow)


def _plant_lease(
    root: Path,
    *,
    run_id: str = CONTROL_RUN_ID,
    token: str = "life-2",
    pid: int | None = None,
    host: str | None = None,
    heartbeat_at: datetime = CONTROL_NOW,
    accepting: bool = True,
    claims: tuple[str, ...] = (),
) -> None:
    """A `run_leases` row and its `run_claims`, as another process's `Lease` would leave them.

    Written over a second `open_db` connection inside `store.immediate`.
    Defaults to this process on this host with a heartbeat at the frozen
    clock: live by C2.
    """
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        with store_module.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,"
                " pid=excluded.pid, host=excluded.host, acquired_at=excluded.acquired_at,"
                " heartbeat_at=excluded.heartbeat_at, accepting=excluded.accepting",
                (
                    run_id,
                    token,
                    os.getpid() if pid is None else pid,
                    HERE if host is None else host,
                    _at(-60).isoformat(),
                    heartbeat_at.isoformat(),
                    int(accepting),
                ),
            )
            for key in claims:
                conn.execute(
                    "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " run_id=excluded.run_id, token=excluded.token,"
                    " claimed_at=excluded.claimed_at",
                    (key, run_id, token, heartbeat_at.isoformat()),
                )
    finally:
        conn.close()


OTHER_RUN_ID = "20260930T080000Z-a1b2c3d4"
"""Another run, driven by another `am` process, that holds a claim."""


def _claim_rows(root: Path) -> list[tuple[str, str, str]]:
    """Every `run_claims` row as `(key, run_id, token)`, in key order."""
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        return [
            (row["key"], row["run_id"], row["token"])
            for row in conn.execute(
                "SELECT key, run_id, token FROM run_claims ORDER BY key"
            ).fetchall()
        ]
    finally:
        conn.close()


def _plant_control(
    root: Path,
    *,
    lease: str,
    command: str,
    requested_at: datetime,
    handled_at: datetime | None = None,
) -> None:
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        with store_module.immediate(conn):
            row = store_module.add_control(
                conn, CONTROL_RUN_ID, lease=lease, command=command, requested_at=requested_at
            )
            if handled_at is not None:
                conn.execute(
                    "UPDATE run_controls SET handled_at = ? WHERE run_id = ? AND seq = ?",
                    (handled_at.isoformat(), CONTROL_RUN_ID, row.seq),
                )
    finally:
        conn.close()


def _controls(root: Path) -> list[tuple[str, str]]:
    """Every `run_controls` row of the run as `(lease, command)`, in seq order."""
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        return [
            (row.lease, row.command)
            for row in store_module.control_requests(conn, CONTROL_RUN_ID)
        ]
    finally:
        conn.close()


def _lease(root: Path) -> store_module.LeaseRow | None:
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        return store_module.read_lease(conn, CONTROL_RUN_ID)
    finally:
        conn.close()


def _invoke_control(root: Path, command: str, run_id: str = CONTROL_RUN_ID, *extra: str):
    return runner.invoke(cli.app, [command, run_id, "--repo-dir", str(root), *extra])


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_a_request_to_an_unknown_run_is_refused(projection, monkeypatch, command):
    """Spec test 1."""
    _freeze_clock(monkeypatch)

    result = _invoke_control(projection, command, "no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()


@pytest.mark.parametrize("status", ["stopped", "escalated", "done", "cancelled"])
@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_a_request_to_a_run_that_is_not_started_is_refused_and_names_its_status(
    projection, monkeypatch, command, status
):
    """Spec test 2. C8 order: the status is judged before the lease, so a live
    lease left on the row does not turn this into a different refusal."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status=status)
    _plant_lease(projection)

    result = _invoke_control(projection, command)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "NotRunningError"
    assert status in error["message"]
    assert "am status" in error["message"]
    assert _controls(projection) == []


@pytest.mark.parametrize(
    "lease, expected",
    [
        (None, ["no process holds its lease"]),
        (
            {"heartbeat_at": CONTROL_NOW - timedelta(seconds=31)},
            [f"pid {os.getpid()}", HERE, "31s ago"],
        ),
        ({"pid": 0}, ["pid 0", HERE, "0s ago"]),
    ],
    ids=["no-lease", "stale-heartbeat", "dead-pid-on-this-host"],
)
def test_a_request_to_a_started_run_with_no_live_lease_is_refused(
    projection, monkeypatch, lease, expected
):
    """Spec test 3: nobody is left to honour the request, so none is recorded."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    if lease is not None:
        _plant_lease(projection, **lease)

    result = _invoke_control(projection, "pause")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "DeadRunError"
    for piece in expected:
        assert piece in error["message"]
    assert CONTROL_RUN_ID in error["message"]
    assert _controls(projection) == []


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_a_request_to_a_run_whose_window_has_closed_is_refused(
    projection, monkeypatch, command
):
    """Spec test 4 (C3): a live lease with `accepting=0` is finishing."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, accepting=False)

    result = _invoke_control(projection, command)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "NotAcceptingError"
    assert CONTROL_RUN_ID in error["message"]
    assert _controls(projection) == []


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_a_dead_lease_is_refused_as_dead_even_when_its_window_has_closed(
    projection, monkeypatch, command
):
    """C8 order: liveness is judged before the window, so a crashed run that
    had begun finishing points at `am resume`, not at `am status`."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, heartbeat_at=_at(-31), accepting=False)

    result = _invoke_control(projection, command)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "DeadRunError"
    assert _controls(projection) == []


@pytest.mark.parametrize(
    "command, lease",
    [
        ("pause", {}),
        ("cancel", {}),
        ("pause", {"heartbeat_at": CONTROL_NOW - timedelta(seconds=30)}),
        ("pause", {"pid": 0, "host": "am-test-other-host.invalid"}),
    ],
    ids=["pause", "cancel", "heartbeat-on-the-boundary", "fresh-lease-on-another-host"],
)
def test_a_request_to_a_live_accepting_run_is_recorded_under_its_lease(
    projection, monkeypatch, command, lease
):
    """Spec test 5, plus Review Focus: C2's 30s boundary is inclusive, and a
    fresh heartbeat from another host is live whatever its pid."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, **lease)

    result = _invoke_control(projection, command)

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == CONTROL_KEYS
    assert {key: data[key] for key in CONTROL_KEYS - {"message"}} == {
        "run_id": CONTROL_RUN_ID,
        "command": command,
        "effective": command,
        "requested_at": CONTROL_NOW.isoformat(),
        "already_requested": False,
    }
    assert CONTROL_RUN_ID in data["message"]
    assert _controls(projection) == [("life-2", command)]


def test_request_control_stamps_the_row_with_the_injected_clock(projection):
    _plant_run(projection)
    _plant_lease(projection, heartbeat_at=_at(100))

    data = cli.request_control(
        CONTROL_RUN_ID, "pause", repo_dir=projection, clock=lambda: _at(110)
    )

    assert data["requested_at"] == _at(110).isoformat()
    assert _controls(projection) == [("life-2", "pause")]


def test_request_control_refuses_a_command_it_does_not_know_and_records_nothing(
    projection,
):
    """Review Focus: a `ValueError` (in `HANDLED`), never the table CHECK's
    `sqlite3.IntegrityError`."""
    _plant_run(projection)
    _plant_lease(projection)

    with pytest.raises(ValueError, match="'resume'"):
        cli.request_control(
            CONTROL_RUN_ID, "resume", repo_dir=projection, clock=lambda: CONTROL_NOW
        )

    assert _controls(projection) == []


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_pause_and_cancel_pretty_indent_the_same_envelope(projection, monkeypatch, command):
    """Spec test 9."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection)

    result = _invoke_control(projection, command, CONTROL_RUN_ID, "--pretty")

    assert result.exit_code == 0, result.output
    assert "\n  " in result.stdout
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert set(envelope["data"]) == CONTROL_KEYS
    assert envelope["data"]["command"] == command

    refusal = _invoke_control(projection, command, "no-such-run", "--pretty")

    assert refusal.exit_code == cli.EXIT_ERROR, refusal.output
    assert "\n  " in refusal.stdout
    refused = json.loads(refusal.stdout)
    assert refused["ok"] is False
    assert refused["error"]["type"] == "UnknownRunError"


def test_a_repeated_pause_is_a_no_op_that_reports_the_first_request(projection, monkeypatch):
    """Spec test 6, first half."""
    _plant_run(projection)
    _plant_lease(projection)
    _freeze_clock(monkeypatch)
    assert _invoke_control(projection, "pause").exit_code == 0

    _freeze_clock(monkeypatch, _at(5))
    result = _invoke_control(projection, "pause")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["already_requested"] is True
    assert data["effective"] == "pause"
    assert data["requested_at"] == CONTROL_NOW.isoformat()
    assert _controls(projection) == [("life-2", "pause")]


def test_a_pause_after_a_cancel_is_a_no_op_and_the_cancel_stays_effective(
    projection, monkeypatch
):
    """Spec test 6, second half: a pause never weakens a cancel."""
    _plant_run(projection)
    _plant_lease(projection)
    _plant_control(projection, lease="life-2", command="cancel", requested_at=_at(-3))
    _freeze_clock(monkeypatch)

    result = _invoke_control(projection, "pause")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["already_requested"] is True
    assert data["effective"] == "cancel"
    assert data["requested_at"] == _at(-3).isoformat()
    assert _controls(projection) == [("life-2", "cancel")]


def test_a_cancel_after_a_pause_upgrades_it_and_a_repeated_cancel_is_a_no_op(
    projection, monkeypatch
):
    """Spec test 7."""
    _plant_run(projection)
    _plant_lease(projection)
    _freeze_clock(monkeypatch)
    assert _invoke_control(projection, "pause").exit_code == 0

    _freeze_clock(monkeypatch, _at(1))
    upgrade = _invoke_control(projection, "cancel")

    assert upgrade.exit_code == 0, upgrade.output
    data = json.loads(upgrade.stdout)["data"]
    assert data["already_requested"] is False
    assert data["effective"] == "cancel"
    assert data["requested_at"] == _at(1).isoformat()
    assert _controls(projection) == [("life-2", "pause"), ("life-2", "cancel")]

    _freeze_clock(monkeypatch, _at(2))
    repeat = _invoke_control(projection, "cancel")

    assert repeat.exit_code == 0, repeat.output
    data = json.loads(repeat.stdout)["data"]
    assert data["already_requested"] is True
    assert data["effective"] == "cancel"
    assert data["requested_at"] == _at(1).isoformat()
    assert _controls(projection) == [("life-2", "pause"), ("life-2", "cancel")]


def test_requests_sent_to_an_earlier_life_do_not_make_a_new_pause_a_no_op(
    projection, monkeypatch
):
    """Spec test 8: a resumed run starts clean (C4)."""
    _plant_run(projection)
    _plant_control(
        projection,
        lease="life-1",
        command="pause",
        requested_at=_at(-300),
        handled_at=_at(-299),
    )
    _plant_control(projection, lease="life-1", command="cancel", requested_at=_at(-200))
    _plant_lease(projection, token="life-2")
    _freeze_clock(monkeypatch)

    result = _invoke_control(projection, "pause")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["already_requested"] is False
    assert data["effective"] == "pause"
    assert data["requested_at"] == CONTROL_NOW.isoformat()
    assert _controls(projection) == [
        ("life-1", "pause"),
        ("life-1", "cancel"),
        ("life-2", "pause"),
    ]


def test_the_status_payload_defaults_to_an_empty_control():
    assert cli.status_payload(_pure_run([]))["control"] == {
        "lease": None,
        "requests": [],
        "claims": [],
    }


def test_status_of_a_run_with_no_lease_shows_an_empty_control(projection, monkeypatch):
    """Spec test 10, first half; Review Focus: the no-RUN_ID default too."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)

    for args in (["status", CONTROL_RUN_ID], ["status"]):
        result = runner.invoke(cli.app, [*args, "--repo-dir", str(projection)])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["control"] == {
            "lease": None,
            "requests": [],
            "claims": [],
        }


@pytest.mark.parametrize(
    "heartbeat_at, live",
    [
        (CONTROL_NOW - timedelta(seconds=5), True),
        (CONTROL_NOW - timedelta(seconds=31), False),
    ],
    ids=["live", "stale"],
)
def test_status_shows_the_lease_and_every_lifes_requests_in_seq_order(
    projection, monkeypatch, heartbeat_at, live
):
    """Spec test 10, second half: C12's exact shape, `live` worked out at read
    time, and `status` stays read-only."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_control(
        projection,
        lease="life-1",
        command="pause",
        requested_at=_at(-300),
        handled_at=_at(-299),
    )
    _plant_control(projection, lease="life-1", command="cancel", requested_at=_at(-200))
    _plant_control(projection, lease="life-2", command="pause", requested_at=_at(-10))
    _plant_lease(projection, heartbeat_at=heartbeat_at)
    before = (_controls(projection), _lease(projection))

    for args in (["status", CONTROL_RUN_ID], ["status"]):
        result = runner.invoke(cli.app, [*args, "--repo-dir", str(projection)])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["control"] == {
            "lease": {
                "pid": os.getpid(),
                "host": HERE,
                "acquired_at": _at(-60).isoformat(),
                "heartbeat_at": heartbeat_at.isoformat(),
                "accepting": True,
                "live": live,
            },
            "requests": [
                {
                    "command": "pause",
                    "requested_at": _at(-300).isoformat(),
                    "handled_at": _at(-299).isoformat(),
                },
                {
                    "command": "cancel",
                    "requested_at": _at(-200).isoformat(),
                    "handled_at": None,
                },
                {
                    "command": "pause",
                    "requested_at": _at(-10).isoformat(),
                    "handled_at": None,
                },
            ],
            "claims": [],
        }
    assert (_controls(projection), _lease(projection)) == before


def _forbid_resume(monkeypatch) -> None:
    monkeypatch.setattr(cli, "Store", _Forbidden("Store"))
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))


def _resume_guard_state(root: Path) -> tuple:
    return (_runs_snapshot(), _attempt_rows(root), _controls(root), _lease(root))


@pytest.mark.parametrize("leased", [False, True], ids=["no-lease", "live-lease"])
@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_resume_refuses_a_cancelled_run_and_writes_nothing(
    projection, monkeypatch, workflow, leased
):
    """Spec test 11 (C9), both workflows. Review Focus: a cancelled run that
    still holds a live lease is refused as cancelled."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="cancelled", workflow=workflow)
    if leased:
        _plant_lease(projection)
    before = _resume_guard_state(projection)
    _forbid_resume(monkeypatch)

    result = runner.invoke(cli.app, ["resume", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "NotResumableError",
        "message": (
            f"run {CONTROL_RUN_ID} was cancelled;"
            " start new work with `am run --milestone`"
        ),
    }
    assert _resume_guard_state(projection) == before


@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_resume_refuses_a_run_whose_lease_is_live_and_writes_nothing(
    projection, monkeypatch, workflow
):
    """Spec test 12 (C10), both workflows."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="started", workflow=workflow)
    _plant_lease(projection, heartbeat_at=_at(-5))
    before = _resume_guard_state(projection)
    _forbid_resume(monkeypatch)

    result = runner.invoke(cli.app, ["resume", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "RunIsLiveError",
        "message": (
            f"run {CONTROL_RUN_ID} is still running in pid {os.getpid()} on {HERE}"
            " (heartbeat 5s ago); wait for it to exit,"
            f" or `am status {CONTROL_RUN_ID}`"
        ),
    }
    assert _resume_guard_state(projection) == before


def test_resume_is_not_blocked_by_a_dead_lease(projection, monkeypatch):
    """Spec test 12, last clause: a stale lease is a crashed run, which is
    exactly what `resume` is for."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="started")
    _plant_lease(projection, heartbeat_at=_at(-31))
    seen: list[str] = []

    def fake_resume(run, **kwargs):
        seen.append(run.id)
        return {"status": "done"}

    monkeypatch.setattr(cli, "_resume_from_checkpoint", fake_resume)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))

    result = runner.invoke(cli.app, ["resume", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope({"status": "done"})
    assert seen == [CONTROL_RUN_ID]


# ── live control in --card runs (card 9f5467e3) ─────────────────────────────


@pytest.mark.parametrize(
    "command, summary_status, expected",
    [
        (None, "done", "done"),
        (None, "escalated", "escalated"),
        (None, "stopped", "stopped"),
        ("pause", "stopped", "stopped"),
        ("pause", "escalated", "escalated"),
        ("cancel", "stopped", "cancelled"),
        ("cancel", "escalated", "cancelled"),
        ("cancel", "done", "cancelled"),
    ],
)
def test_card_run_status_follows_c6_precedence(command, summary_status, expected):
    """A cancel closes the run whatever the walk ended as; a pause never
    changes it, so a paused escalation stays `escalated`."""
    stop = StopSignal()
    if command is not None:
        stop.request(command)

    assert cli.card_run_status(SubtaskSummary(status=summary_status), stop) == expected


CONTROL_TICK = 0.01
"""How often the run's watcher polls in these tests. The request is inserted
mid-phase and the fake waits on `control_applied`, so this sets only how soon
the watcher notices, never the ordering."""


@pytest.fixture
def control_applied(monkeypatch) -> threading.Event:
    """Set once the running `am` process's watcher has applied a request.

    `run_card` builds its `StopSignal` by the module name `cli.StopSignal`, so
    this subclass is the one it gets. `request` pauses every registered agent
    before the event is set, so a fake that waits on it returns from its phase
    with the agent already paused -- no sleep decides the order.
    """
    applied = threading.Event()

    class SignalledStop(StopSignal):
        def request(self, command):
            changed = super().request(command)
            applied.set()
            return changed

    monkeypatch.setattr(cli, "StopSignal", SignalledStop)
    return applied


def _card_lease(project: Path, run_id: str) -> store_module.LeaseRow | None:
    """The run's lease row, read over a second connection as `am status` would."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return store_module.read_lease(conn, run_id)
    finally:
        conn.close()


def _card_controls(project: Path, run_id: str) -> list[store_module.ControlRow]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return store_module.control_requests(conn, run_id)
    finally:
        conn.close()


def _card_statuses(project: Path, run_id: str) -> dict[str, str]:
    """The run, its one story and its one subtask, as recorded."""
    run = _loaded(project, run_id)
    (story,) = run.stories
    (subtask,) = story.subtasks
    return {"run": run.status, "story": story.status, "subtask": subtask.status}


def _newest_checkpoint_reason(project: Path, run_id: str) -> str:
    rows = [row for row in _checkpoint_rows(project) if row[0] == run_id]
    assert rows, f"run {run_id} saved no checkpoint"
    return rows[-1][3]


def _controlling_factory(
    project: Path,
    applied: threading.Event,
    *,
    command: str | None,
    at: str,
    seen: list[str],
    fail: bool = False,
    leases: list[store_module.LeaseRow | None] | None = None,
):
    """A `cli.RunnerFactory` whose runner, inside phase `at`, acts as a second process.

    The runner runs in the engine's `to_thread` worker, so blocking it never
    blocks the loop the watcher runs on. In phase `at` it records the lease
    as another process sees it (`leases`), sends `command` through
    `cli.request_control` (the `am pause`/`am cancel` path, over its own
    connection), waits until the watcher applied it, and then finishes the
    phase -- or, with `fail`, escalates it with a gate failure. Every other
    phase is `recording_runner`'s.
    """
    record = _resume_factory(seen)

    def factory(*, store, run_id, story_id, card_id):
        run = record(store=store, run_id=run_id, story_id=story_id, card_id=card_id)

        def runner(phase, context, rendered):
            if phase.name != at:
                return run(phase, context, rendered)
            if leases is not None:
                leases.append(_card_lease(project, run_id))
            if command is not None:
                cli.request_control(run_id, command, repo_dir=project)
                if not applied.wait(5):
                    raise RuntimeError(f"the {command} request was never applied")
            if fail:
                seen.append(phase.name)
                raise AgentPhaseFailed(
                    phase.name, outcome="gate_failed", detail="canned gate failure"
                )
            return run(phase, context, rendered)

        return runner

    return factory


def _controlled_card_run(project: Path, cards: dict[str, str], factory) -> dict[str, Any]:
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=factory,
        control_interval=CONTROL_TICK,
    )


@requires_git
@requires_brd
def test_a_paused_card_run_parks_after_the_running_phase(project, cards, control_applied):
    """Spec test 1: the running phase finishes, nothing after it is
    dispatched, the park is `parked`, every row is `stopped`, and the request
    is marked handled."""
    seen: list[str] = []
    factory = _controlling_factory(
        project, control_applied, command="pause", at="spec", seen=seen
    )

    payload = _controlled_card_run(project, cards, factory)

    run_id = payload["run_id"]
    assert payload["status"] == "stopped", payload
    assert payload["failed_phase"] is None
    assert payload["detail"] == "stopped before validate_spec"
    assert seen[-1] == "spec"
    assert "validate_spec" not in seen
    assert _card_statuses(project, run_id) == {
        "run": "stopped",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _newest_checkpoint_reason(project, run_id) == "parked"
    controls = _card_controls(project, run_id)
    assert [row.command for row in controls] == ["pause"]
    assert all(row.handled_at is not None for row in controls)
    assert _card_lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_paused_card_run_resumes_from_the_parked_phase_to_done(
    project, cards, control_applied
):
    """Spec test 2: `am resume` continues at the phase the pause parked
    before, and ends `done`."""
    factory = _controlling_factory(
        project, control_applied, command="pause", at="spec", seen=[]
    )
    run_id = _controlled_card_run(project, cards, factory)["run_id"]

    after: list[str] = []
    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory(after))

    assert payload["status"] == "done", payload
    assert payload["resumed_from"] == "validate_spec"
    assert after[0] == "validate_spec"
    assert not {"explore", "spec"} & set(after)
    assert _card_statuses(project, run_id) == {
        "run": "done",
        "story": "done",
        "subtask": "done",
    }
    assert _card_lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_cancelled_card_run_closes_the_run_and_resume_refuses_it(
    project, cards, control_applied
):
    """Spec test 3, plus Review Focus 3: the run is `cancelled`, its story and
    subtask stay `stopped` as the park left them, `am resume` refuses it, and
    a pause sent afterwards is refused rather than queued."""
    seen: list[str] = []
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=seen
    )

    payload = _controlled_card_run(project, cards, factory)

    run_id = payload["run_id"]
    assert payload["status"] == "cancelled", payload
    assert payload["failed_phase"] is None
    assert "validate_spec" not in seen
    assert _card_statuses(project, run_id) == {
        "run": "cancelled",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _newest_checkpoint_reason(project, run_id) == "parked"
    assert _card_lease(project, run_id) is None

    resumed = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])
    assert resumed.exit_code == cli.EXIT_ERROR, resumed.output
    error = json.loads(resumed.stdout)["error"]
    assert error["type"] == "NotResumableError"
    assert "cancelled" in error["message"]

    late = runner.invoke(cli.app, ["pause", run_id, "--repo-dir", str(project)])
    assert late.exit_code == cli.EXIT_ERROR, late.output
    assert json.loads(late.stdout)["error"]["type"] == "NotRunningError"
    assert [row.command for row in _card_controls(project, run_id)] == ["cancel"]


@requires_git
@requires_brd
def test_a_cancel_that_meets_an_escalation_closes_the_run_but_keeps_the_rows(
    project, cards, control_applied
):
    """Review Focus 1 at the function: C6 puts the cancel first for the run,
    while the story and subtask rows keep the escalation the walk ended in."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[], fail=True
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "cancelled", payload
    assert _card_statuses(project, payload["run_id"]) == {
        "run": "cancelled",
        "story": "escalated",
        "subtask": "escalated",
    }


@pytest.mark.parametrize(
    "command, fail, exit_code, status",
    [
        ("pause", False, 0, "stopped"),
        ("cancel", False, 0, "cancelled"),
        ("cancel", True, 0, "cancelled"),
        ("pause", True, cli.EXIT_ESCALATED, "escalated"),
    ],
    ids=["pause", "cancel", "cancel-over-escalation", "pause-keeps-escalation"],
)
@requires_git
@requires_brd
def test_a_control_and_an_escalation_follow_c6_at_the_command(
    project, cards, control_applied, monkeypatch, command, fail, exit_code, status
):
    """Spec's exit codes and Review Focus 1-2: the command's mapping still keys
    off `escalated`, so `stopped` and `cancelled` exit 0 with an ok envelope
    and a paused escalation still exits 1. `run` passes no interval, so the
    real `run_card` is wrapped to add a short one and the fake factory."""
    real_run_card = cli.run_card
    factory = _controlling_factory(
        project, control_applied, command=command, at="spec", seen=[], fail=fail
    )

    def run_card_with_control(card_id, **kwargs):
        return real_run_card(
            card_id, **kwargs, runner_factory=factory, control_interval=CONTROL_TICK
        )

    monkeypatch.setattr(cli, "run_card", run_card_with_control)

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == exit_code, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == status


@requires_git
@requires_brd
def test_an_uncontrolled_card_run_holds_its_lease_then_releases_it(project, cards):
    """Spec tests 4 and 6: the lease is held, window open, while a phase runs;
    it is gone afterwards; the payload is today's, key for key."""
    leases: list[store_module.LeaseRow | None] = []
    factory = _controlling_factory(
        project, threading.Event(), command=None, at="explore", seen=[], leases=leases
    )

    payload = _controlled_card_run(project, cards, factory)

    run_id = payload["run_id"]
    (during,) = leases
    assert during is not None, "no lease was held while the walk ran"
    assert during.run_id == run_id
    assert during.accepting is True
    assert _card_lease(project, run_id) is None
    assert payload["status"] == "done"
    assert set(payload) == {
        "run_id",
        "card_id",
        "story_id",
        "branch",
        "base_branch",
        "worktree",
        "status",
        "failed_phase",
        "detail",
        "skipped",
        "warnings",
    }
    assert _card_statuses(project, run_id) == {
        "run": "done",
        "story": "done",
        "subtask": "done",
    }
    assert _card_controls(project, run_id) == []


@requires_git
@requires_brd
def test_a_card_walk_that_raises_releases_its_lease(project, cards, monkeypatch):
    """Spec test 4 and the error path: the lease was held when the walk blew
    up, it is released on the way out, the error surfaces as is, and no final
    status row is written."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    held: list[store_module.LeaseRow | None] = []

    async def exploding(*args, **kwargs):
        held.append(_card_lease(project, run_id))
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(cli.runtime_engine, "run_subtask_async", exploding)

    with pytest.raises(EngineError, match="explore"):
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            clock=lambda: CRASHED_AT,
            runner_factory=lambda **kwargs: fake_runner(),
            control_interval=CONTROL_TICK,
        )

    (during,) = held
    assert during is not None, "no lease was held while the walk ran"
    assert _card_lease(project, run_id) is None
    assert _loaded(project, run_id).status == "started"


def _resume_card_run(project: Path, run_id: str, factory) -> dict[str, Any]:
    """`_resume_from_checkpoint` called as `resume_run` calls it, plus a short
    interval. `resume_run` passes none and is not this card's to change."""
    return cli._resume_from_checkpoint(
        _loaded(project, run_id),
        root=cli.resolve_repo_dir(project),
        allow_no_verification=False,
        commands=(),
        runner_factory=factory,
        control_interval=CONTROL_TICK,
    )


@requires_git
@requires_brd
def test_a_resumed_card_run_paused_mid_phase_parks_and_releases_its_lease(
    project, cards, control_applied
):
    """Spec test 5: the resumed walk starts at `plan`, is paused there, finishes
    `plan`, parks before the next phase, and gives its lease back."""
    run_id = _crash_pygents(project, cards, "plan")
    seen: list[str] = []
    leases: list[store_module.LeaseRow | None] = []
    factory = _controlling_factory(
        project, control_applied, command="pause", at="plan", seen=seen, leases=leases
    )

    payload = _resume_card_run(project, run_id, factory)

    assert payload["status"] == "stopped", payload
    assert payload["failed_phase"] is None
    assert payload["resumed_from"] == "plan"
    assert payload["discarded_attempts"] == [{"phase": "plan", "n": 1}]
    assert set(payload) == RESUME_KEYS
    assert seen == ["plan"]
    assert payload["detail"] == "stopped before validate_plan"
    (during,) = leases
    assert during is not None and during.run_id == run_id and during.accepting is True
    assert _card_lease(project, run_id) is None
    assert _card_statuses(project, run_id) == {
        "run": "stopped",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _newest_checkpoint_reason(project, run_id) == "parked"
    assert all(row.handled_at is not None for row in _card_controls(project, run_id))


@requires_git
@requires_brd
def test_a_resumed_card_run_cancelled_mid_phase_is_closed_for_good(
    project, cards, control_applied
):
    """Review Focus 5: a cancel reaches a resumed `task` run too, and a second
    `am resume` refuses it."""
    run_id = _crash_pygents(project, cards, "plan")
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="plan", seen=[]
    )

    payload = _resume_card_run(project, run_id, factory)

    assert payload["status"] == "cancelled", payload
    assert _card_statuses(project, run_id) == {
        "run": "cancelled",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _card_lease(project, run_id) is None

    again = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])
    assert again.exit_code == cli.EXIT_ERROR, again.output
    assert json.loads(again.stdout)["error"]["type"] == "NotResumableError"


@requires_git
@requires_brd
def test_a_resumed_card_walk_that_raises_releases_its_lease(project, cards, monkeypatch):
    """Error path on resume: the lease was held when the walk blew up and is
    released on the way out; the run stays `started` as it does today."""
    run_id = _crash_pygents(project, cards, "plan")
    held: list[store_module.LeaseRow | None] = []

    async def exploding(*args, **kwargs):
        held.append(_card_lease(project, run_id))
        raise EngineError("no value for a required parameter", phase="plan")

    monkeypatch.setattr(cli.runtime_engine, "run_subtask_async", exploding)

    with pytest.raises(EngineError, match="plan"):
        _resume_card_run(project, run_id, _resume_factory())

    (during,) = held
    assert during is not None, "no lease was held while the resumed walk ran"
    assert _card_lease(project, run_id) is None
    assert _loaded(project, run_id).status == "started"


# ── leases with claims (card ec7ae954) ──────────────────────────────────────


@pytest.mark.parametrize(
    "key, named",
    [
        ("card:card-1", "card card-1"),
        ("branch:m10/task-x-ec7ae954", "branch m10/task-x-ec7ae954"),
    ],
    ids=["card", "branch"],
)
def test_refuse_claimed_names_the_kind_and_the_live_holder(projection, monkeypatch, key, named):
    """X11's wording, built from the key so a branch claim reads as a branch
    (Review Focus 3). Read-only: no row changes, no run directory."""
    _freeze_clock(monkeypatch)
    _plant_lease(projection, heartbeat_at=_at(-7), claims=(key,))
    before = (_lease(projection), _claim_rows(projection))

    with pytest.raises(cli.ClaimedError) as caught:
        cli.refuse_claimed(cli.resolve_repo_dir(projection), [key])

    assert str(caught.value) == (
        f"{named} is being driven by run {CONTROL_RUN_ID}"
        f" (pid {os.getpid()} on {HERE}, heartbeat 7s ago);"
        f" wait for it, or `am pause {CONTROL_RUN_ID}`"
    )
    assert (caught.value.key, caught.value.run_id) == (key, CONTROL_RUN_ID)
    assert isinstance(caught.value, cli.CliError)
    assert (_lease(projection), _claim_rows(projection)) == before
    assert not (paths.data_dir() / "runs").exists()


def test_refuse_claimed_passes_the_runs_own_claims_unclaimed_keys_and_dead_ones(
    projection, monkeypatch
):
    _freeze_clock(monkeypatch)
    root = cli.resolve_repo_dir(projection)
    _plant_lease(projection, heartbeat_at=_at(-5), claims=("card:card-1",))

    cli.refuse_claimed(root, ["card:card-1"], run_id=CONTROL_RUN_ID)
    cli.refuse_claimed(root, ["card:someone-else"])

    _plant_lease(projection, heartbeat_at=_at(-31), claims=("card:card-1",))
    cli.refuse_claimed(root, ["card:card-1"])


def test_run_lease_turns_a_held_claim_into_claimed_error_and_takes_nothing(
    projection, monkeypatch
):
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_lease(
        projection,
        run_id=OTHER_RUN_ID,
        heartbeat_at=now - timedelta(seconds=7),
        claims=("card:card-1",),
    )
    opened = store_module.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        with pytest.raises(cli.ClaimedError) as caught:
            with cli.run_lease(opened, claims=["card:card-1"]):
                pytest.fail("entered a lease whose claim another live run holds")
    finally:
        opened.close()

    assert (caught.value.key, caught.value.run_id) == ("card:card-1", OTHER_RUN_ID)
    assert str(caught.value) == (
        f"card card-1 is being driven by run {OTHER_RUN_ID}"
        f" (pid {os.getpid()} on {HERE}, heartbeat 7s ago);"
        f" wait for it, or `am pause {OTHER_RUN_ID}`"
    )
    assert _lease(projection) is None
    assert _claim_rows(projection) == [("card:card-1", OTHER_RUN_ID, "life-2")]


def test_run_lease_turns_a_held_lease_into_c10s_run_is_live_error(projection, monkeypatch):
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_lease(projection, heartbeat_at=now - timedelta(seconds=5))
    opened = store_module.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        with pytest.raises(cli.RunIsLiveError) as caught:
            with cli.run_lease(opened, claims=["card:card-1"]):
                pytest.fail("entered a lease another live process holds")
    finally:
        opened.close()

    assert str(caught.value) == (
        f"run {CONTROL_RUN_ID} is still running in pid {os.getpid()} on {HERE}"
        " (heartbeat 5s ago); wait for it to exit,"
        f" or `am status {CONTROL_RUN_ID}`"
    )
    assert _claim_rows(projection) == []


def test_run_lease_releases_its_claims_and_lease_when_the_body_raises(projection):
    opened = store_module.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        with pytest.raises(ValueError, match="the walk raised"):
            with cli.run_lease(opened, claims=["card:card-1"]) as lease:
                assert _claim_rows(projection) == [("card:card-1", CONTROL_RUN_ID, lease.token)]
                assert _lease(projection) is not None
                raise ValueError("the walk raised")
    finally:
        opened.close()

    assert _lease(projection) is None
    assert _claim_rows(projection) == []


def _reaped_pid() -> int:
    """The pid of a child that has exited and been waited for: dead by `pid_alive`."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _run_dirs() -> list[Path]:
    runs_root = paths.data_dir() / "runs"
    return sorted(runs_root.iterdir()) if runs_root.exists() else []


def _recorded_run_ids(project: Path) -> list[str]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [summary.id for summary in store_module.list_runs(conn)]
    finally:
        conn.close()


@requires_git
@requires_brd
def test_a_card_run_is_refused_while_another_live_run_claims_the_card(
    project, cards, monkeypatch
):
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=now - timedelta(seconds=7),
        claims=(key,),
    )
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "type": "ClaimedError",
            "message": (
                f"card {cards['subtask']} is being driven by run {OTHER_RUN_ID}"
                f" (pid {os.getpid()} on {HERE}, heartbeat 7s ago);"
                f" wait for it, or `am pause {OTHER_RUN_ID}`"
            ),
        },
    }
    assert _run_dirs() == []
    assert _recorded_run_ids(project) == []
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]
    worktrees = [
        line
        for line in _git(project, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    assert len(worktrees) == 1, worktrees
    assert _git(project, "branch", "--format=%(refname:short)").split() == ["main"]


@requires_git
@requires_brd
def test_a_claim_taken_after_the_preflight_is_refused_with_only_an_empty_run_dir(
    project, cards, monkeypatch
):
    """Review Focus 1: the preflight passed, then another run claimed the card
    before `take_lease`; the only leftover is the empty run directory."""
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=now - timedelta(seconds=7),
        claims=(key,),
    )
    monkeypatch.setattr(cli, "refuse_claimed", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "ClaimedError"
    assert error["message"].startswith(f"card {cards['subtask']} is being driven by run {OTHER_RUN_ID}")
    (run_dir,) = _run_dirs()
    assert list(run_dir.iterdir()) == []
    assert _recorded_run_ids(project) == []
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@requires_git
@requires_brd
def test_a_dead_claim_does_not_refuse(project, cards):
    dead = _reaped_pid()
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="dead-life",
        pid=dead,
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )

    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["status"] == "done"
    # The dead claim was taken over by this run, then released with its lease.
    assert _claim_rows(project) == []


@requires_git
@requires_brd
def test_the_lease_is_bound_before_the_first_journal_line(project, cards, monkeypatch):
    real_record_run = store_module.Store.record_run
    first: list[tuple[str | None, list[str]]] = []

    def spy(self, run):
        if not first:
            token = self._token
            held = (
                []
                if token is None
                else [
                    claim.key
                    for claim in store_module.held_claims(self.connection, self.run_id, token)
                ]
            )
            first.append((token, held))
        return real_record_run(self, run)

    monkeypatch.setattr(store_module.Store, "record_run", spy)

    cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    ((token, held),) = first
    assert token is not None
    assert held == [control.card_claim(cards["subtask"])]


@pytest.mark.parametrize("outcome", ["done", "escalated", "raises"])
@requires_git
@requires_brd
def test_a_card_run_releases_its_claims_on_every_exit(project, cards, monkeypatch, outcome):
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    key = control.card_claim(cards["subtask"])
    during: list[list[tuple[str, str, str]]] = []

    if outcome == "raises":

        async def exploding(*args, **kwargs):
            during.append(_claim_rows(project))
            raise EngineError("no value for a required parameter", phase="explore")

        monkeypatch.setattr(cli.runtime_engine, "run_subtask_async", exploding)

    inner = fake_runner(fail="review" if outcome == "escalated" else None)

    def watching(phase, context, rendered):
        if phase.name == "explore":
            during.append(_claim_rows(project))
        return inner(phase, context, rendered)

    def drive() -> dict[str, Any]:
        return cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            clock=lambda: CRASHED_AT,
            runner_factory=lambda **kwargs: watching,
            control_interval=CONTROL_TICK,
        )

    if outcome == "raises":
        with pytest.raises(EngineError, match="explore"):
            drive()
    else:
        assert drive()["status"] == outcome

    ((row,),) = during
    assert row[:2] == (key, run_id)
    assert _claim_rows(project) == []
    assert _card_lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_lease_lost_mid_walk_is_an_envelope_at_exit_3(project, cards, monkeypatch):
    """The first phase lets a second process take the run's lease over; the
    next fenced write raises `LeaseLostError`, which the command renders."""
    taken: list[str] = []

    def thief_factory(*, store, run_id, story_id, card_id):
        inner = fake_runner()

        def runner(phase, context, rendered):
            if phase.name == "explore" and not taken:
                thief = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
                try:
                    thief.take_lease(
                        token="thief",
                        pid=1,
                        host="elsewhere",
                        now=datetime.now(timezone.utc),
                        is_live=lambda row: False,
                    )
                finally:
                    thief.close()
                taken.append(run_id)
            return inner(phase, context, rendered)

        return runner

    monkeypatch.setattr(cli, "default_runner_factory", thief_factory)

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    (run_id,) = taken
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "type": "LeaseLostError",
            "message": (
                f"this process lost the lease of run {run_id!r}:"
                " pid 1 on elsewhere holds it now"
            ),
        },
    }
    lease = _card_lease(project, run_id)
    assert lease is not None and lease.token == "thief"
    assert _loaded(project, run_id).status == "started"


@requires_git
@requires_brd
def test_resume_takes_over_a_dead_lease_and_says_so(project, cards):
    run_id = _crash_pygents(project, cards, "plan")
    dead = _reaped_pid()
    beat = datetime.now(timezone.utc)
    _plant_lease(
        project,
        run_id=run_id,
        token="crashed-life",
        pid=dead,
        heartbeat_at=beat,
        claims=(control.card_claim(cards["subtask"]),),
    )

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["status"] == "done", payload
    assert payload["took_over"] == {
        "pid": dead,
        "host": HERE,
        "heartbeat_at": beat.isoformat(),
    }
    assert set(payload) == RESUME_KEYS | {"took_over"}
    assert _card_lease(project, run_id) is None
    assert _claim_rows(project) == []


@requires_git
@requires_brd
def test_a_resume_refuses_a_card_another_live_run_claims_and_writes_nothing(
    project, cards, monkeypatch
):
    run_id = _crash_pygents(project, cards, "plan")
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=now - timedelta(seconds=7),
        claims=(key,),
    )
    # `resume` passes no runner factory, so without this a missing refusal
    # would reach the real `dispatch.AgentRunner`.
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))
    before = (_attempt_rows(project), _checkpoint_rows(project), _runs_snapshot())

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout)["error"] == {
        "type": "ClaimedError",
        "message": (
            f"card {cards['subtask']} is being driven by run {OTHER_RUN_ID}"
            f" (pid {os.getpid()} on {HERE}, heartbeat 7s ago);"
            f" wait for it, or `am pause {OTHER_RUN_ID}`"
        ),
    }
    assert (_attempt_rows(project), _checkpoint_rows(project), _runs_snapshot()) == before
    assert _card_lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_resume_refuses_a_claimed_card_before_opening_the_store(
    project, cards, monkeypatch
):
    """The resume preflight is read-only and runs before `Store.open` (X5):
    `run_lease` alone would refuse too, but only after opening the store."""
    run_id = _crash_pygents(project, cards, "plan")
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=now - timedelta(seconds=7),
        claims=(key,),
    )
    monkeypatch.setattr(store_module.Store, "open", _Forbidden("Store.open"))

    with pytest.raises(cli.ClaimedError) as caught:
        _resume_card_run(project, run_id, _Forbidden("runner_factory"))

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@requires_git
@requires_brd
def test_a_resume_that_loses_the_lease_race_is_run_is_live_and_writes_nothing(
    project, cards, monkeypatch
):
    """Review Focus 2: C10 in `resume_run` passed, then another `am resume`
    took the lease; `_resume_from_checkpoint` must refuse before the orphan
    writes."""
    run_id = _crash_pygents(project, cards, "plan")
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_lease(project, run_id=run_id, token="racer", heartbeat_at=now - timedelta(seconds=5))
    before = (_attempt_rows(project), _checkpoint_rows(project), store_module.Journal(run_id).read())

    with pytest.raises(cli.RunIsLiveError) as caught:
        _resume_card_run(project, run_id, _Forbidden("runner_factory"))

    assert str(caught.value) == (
        f"run {run_id} is still running in pid {os.getpid()} on {HERE}"
        " (heartbeat 5s ago); wait for it to exit,"
        f" or `am status {run_id}`"
    )
    assert (
        _attempt_rows(project),
        _checkpoint_rows(project),
        store_module.Journal(run_id).read(),
    ) == before
    lease = _card_lease(project, run_id)
    assert lease is not None and lease.token == "racer"


@pytest.mark.parametrize(
    "heartbeat_at, shown",
    [
        (CONTROL_NOW - timedelta(seconds=5), ["branch:m10/task-x", "card:card-1"]),
        (CONTROL_NOW - timedelta(seconds=31), []),
    ],
    ids=["live", "stale"],
)
def test_status_lists_the_claims_of_the_live_lease(projection, monkeypatch, heartbeat_at, shown):
    """A live lease's claims in key order; a stale lease's leftover claims are
    not shown (Review Focus 5). `status` stays read-only."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(
        projection,
        heartbeat_at=heartbeat_at,
        claims=("card:card-1", "branch:m10/task-x"),
    )
    before = (_lease(projection), _claim_rows(projection))

    result = runner.invoke(cli.app, ["status", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["control"]["claims"] == shown
    assert (_lease(projection), _claim_rows(projection)) == before


@requires_git
@requires_brd
def test_readers_never_take_a_lease_or_a_lock(project, milestone_board, monkeypatch):
    _record_for_logs(project, LOGS_RUN_ID)
    _plant_lease(
        project,
        run_id=LOGS_RUN_ID,
        token="reader-test",
        heartbeat_at=datetime.now(timezone.utc),
        claims=("card:card-1",),
    )
    before = (_card_lease(project, LOGS_RUN_ID), _claim_rows(project))

    def forbidden(name: str):
        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"a reader reached {name}")

        return refuse

    monkeypatch.setattr(store_module.Store, "take_lease", forbidden("Store.take_lease"))
    monkeypatch.setattr(locks.ProcessLock, "acquire", forbidden("ProcessLock.acquire"))
    monkeypatch.setattr(cli, "run_lease", forbidden("cli.run_lease"))

    status = runner.invoke(cli.app, ["status", LOGS_RUN_ID, "--repo-dir", str(project)])
    assert status.exit_code == 0, status.output
    assert json.loads(status.stdout)["data"]["control"]["claims"] == ["card:card-1"]

    listed = runner.invoke(cli.app, ["runs", "--repo-dir", str(project)])
    assert listed.exit_code == 0, listed.output

    logged = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(project)]
    )
    assert logged.exit_code == 0, logged.output

    previewed = _dry_run(project, "make the skeleton real")
    assert previewed.exit_code == 0, previewed.output

    assert (_card_lease(project, LOGS_RUN_ID), _claim_rows(project)) == before


# ── board comments on `run --card` (card 5d9a875f) ──────────────────────────


def _card_comment_keys(project: Path, card_id: str) -> list[str]:
    """`card_id`'s comments on the temporary board, as their `am-key:` values."""
    return [
        comment.body.rstrip().rsplit("\n", 1)[-1].removeprefix("am-key: ")
        for comment in board.comment_list(card_id, repo_dir=project)
    ]


def _card_outbox(project: Path) -> list[tuple[str, str]]:
    """Every outbox row as `(key, state)`, in insertion order."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [
            (row["key"], row["state"])
            for row in conn.execute("SELECT key, state FROM board_comments ORDER BY rowid")
        ]
    finally:
        conn.close()


def _run_card_with(project: Path, cards: dict[str, str], factory) -> dict[str, Any]:
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=factory,
    )


@requires_git
@requires_brd
def test_a_done_card_run_leaves_one_done_comment_on_the_card_only(project, cards):
    """Spec cli test 1: one done comment naming the branch; nothing on the
    story or the milestone."""
    payload = _run_card_with(project, cards, lambda **kwargs: fake_runner())

    assert payload["status"] == "done", payload
    run_id, card = payload["run_id"], cards["subtask"]
    assert _card_comment_keys(project, card) == [f"{run_id}/{card}/done"]
    (comment,) = board.comment_list(card, repo_dir=project)
    assert comment.author == "am"
    assert comment.body.startswith(f"am · done · run {run_id}\n")
    assert f"branch: {payload['branch']}" in comment.body
    assert board.comment_list(cards["story"], repo_dir=project) == []
    assert board.comment_list(cards["milestone"], repo_dir=project) == []
    assert not [w for w in payload["warnings"] if "board comment" in w], payload["warnings"]
    assert _card_outbox(project) == [(f"{run_id}/{card}/done", "posted")]


@requires_git
@requires_brd
def test_an_escalated_card_run_leaves_one_escalation_comment_with_the_phase(project, cards):
    """Spec cli test 2: keyed by this run's lease token; names the failed
    phase and its detail, and tells a human to resume."""
    payload = _run_card_with(project, cards, lambda **kwargs: fake_runner(fail="review"))

    assert payload["status"] == "escalated", payload
    run_id, card = payload["run_id"], cards["subtask"]
    (key,) = _card_comment_keys(project, card)
    assert key.startswith(f"{run_id}/{card}/escalated:")
    (comment,) = board.comment_list(card, repo_dir=project)
    assert comment.body.startswith(f"am · escalated · run {run_id}\n")
    assert "phase: review" in comment.body
    assert "canned gate failure" in comment.body
    assert f"next: `am resume {run_id}`" in comment.body
    assert board.comment_list(cards["story"], repo_dir=project) == []
    assert board.comment_list(cards["milestone"], repo_dir=project) == []


@requires_git
@requires_brd
def test_a_cancelled_card_run_leaves_one_cancelled_comment_naming_run_card(
    project, cards, control_applied
):
    """Spec cli test 3, cancel half: where it stopped, its branch, and the
    `am run --card` relaunch."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[]
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "cancelled", payload
    run_id, card = payload["run_id"], cards["subtask"]
    assert _card_comment_keys(project, card) == [f"{run_id}/{card}/cancelled"]
    (comment,) = board.comment_list(card, repo_dir=project)
    assert comment.body.startswith(f"am · cancelled · run {run_id}\n")
    assert "stopped before: validate_spec" in comment.body
    assert f"branch: {payload['branch']}" in comment.body
    assert f"relaunch: `am run --card {card}`" in comment.body
    assert board.comment_list(cards["story"], repo_dir=project) == []
    assert board.comment_list(cards["milestone"], repo_dir=project) == []


@requires_git
@requires_brd
def test_a_paused_card_run_leaves_no_comment(project, cards, control_applied):
    """Spec cli test 3, pause half: a park is resumed, not closed."""
    factory = _controlling_factory(
        project, control_applied, command="pause", at="spec", seen=[]
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "stopped", payload
    for card_id in cards.values():
        assert board.comment_list(card_id, repo_dir=project) == [], card_id
    assert _card_outbox(project) == []


@requires_git
@requires_brd
def test_a_card_cancel_that_meets_an_escalation_comments_the_escalation(
    project, cards, control_applied
):
    """Review Focus 5: the run is `cancelled` (C6) but the walk escalated;
    the comment follows `summary.status`, so it is the escalation."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[], fail=True
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "cancelled", payload
    run_id, card = payload["run_id"], cards["subtask"]
    (key,) = _card_comment_keys(project, card)
    assert key.startswith(f"{run_id}/{card}/escalated:")
    assert "phase: spec" in board.comment_list(card, repo_dir=project)[0].body


@requires_git
@requires_brd
def test_a_card_comment_the_board_refuses_is_a_warning_and_changes_nothing_else(
    project, cards, monkeypatch
):
    """Spec cli test 4 (B8): status, recorded rows and payload keys are
    unchanged; the refusal is one warning and the row stays pending."""
    real_list = board.comment_list

    def refuse(card_id: str, *, repo_dir: Path | None = None):
        if card_id == cards["subtask"]:
            raise board.BoardError(
                "brd is down", argv=["brd", "comment", "list", card_id], exit_code=1
            )
        return real_list(card_id, repo_dir=repo_dir)

    monkeypatch.setattr(board, "comment_list", refuse)

    payload = _run_card_with(project, cards, lambda **kwargs: fake_runner())

    run_id, card = payload["run_id"], cards["subtask"]
    assert payload["status"] == "done", payload
    assert set(payload) == {
        "run_id",
        "card_id",
        "story_id",
        "branch",
        "base_branch",
        "worktree",
        "status",
        "failed_phase",
        "detail",
        "skipped",
        "warnings",
    }
    assert _card_statuses(project, run_id) == {"run": "done", "story": "done", "subtask": "done"}
    refused = [w for w in payload["warnings"] if f"board comment {run_id}/{card}/done" in w]
    assert len(refused) == 1, payload["warnings"]
    assert "not posted" in refused[0] and "brd is down" in refused[0]
    assert _card_outbox(project) == [(f"{run_id}/{card}/done", "pending")]


# ── am watch, one-shot (card 43f4f076) ─────────────────────────────────────
#
# Default (unit) tier per design §14: no git, no brd, no harness. Journals are
# written straight to `XDG_DATA_HOME/agent-manager/runs/<id>/journal.jsonl`.

WATCH_TS = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _watch_runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "xdg" / "agent-manager" / "runs"


def _write_watch_journal(
    tmp_path: Path, run_id: str, seqs: list[int], *, tail: str = ""
) -> list[dict[str, Any]]:
    """Write `run_id`'s journal with one line per seq, then `tail` verbatim.

    Returns the lines as `am watch` must report them: JSON-mode dumps.
    """
    run_dir = _watch_runs_dir(tmp_path) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dumped = [
        store_module.JournalLine(
            seq=seq,
            ts=WATCH_TS,
            run_id=run_id,
            event="phase_upsert",
            card="card-1",
            phase="implement",
            attempt=1,
            payload={"status": "started", "n": seq},
        ).model_dump(mode="json")
        for seq in seqs
    ]
    text = "".join(json.dumps(line, sort_keys=True) + "\n" for line in dumped)
    (run_dir / store_module.JOURNAL_NAME).write_text(text + tail, encoding="utf-8")
    return dumped


def _watch(*args: str):
    return runner.invoke(cli.app, ["watch", *args])


def test_watch_single_run_returns_events_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(tmp_path, "run-a", [1, 2, 3])

    result = _watch("run-a")

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope == {"ok": True, "data": {"events": written}}
    assert [event["seq"] for event in envelope["data"]["events"]] == [1, 2, 3]

    pretty = _watch("run-a", "--pretty")
    assert pretty.exit_code == 0, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(result.stdout)


def test_watch_since_filters_to_later_seqs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(tmp_path, "run-a", [1, 2, 3, 4])

    result = _watch("run-a", "--since", "2")

    assert result.exit_code == 0, result.output
    events = json.loads(result.stdout)["data"]["events"]
    assert [event["seq"] for event in events] == [3, 4]
    assert events == written[2:]


def test_watch_since_past_the_last_seq_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1, 2])

    result = _watch("run-a", "--since", "99")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"ok": True, "data": {"events": []}}


def test_watch_refuses_a_negative_since(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1])

    result = _watch("run-a", "--since", "-1")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CliError"
    assert "--since" in envelope["error"]["message"]


def test_watch_unknown_run_refuses_and_creates_no_run_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = _watch("no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]
    assert not (_watch_runs_dir(tmp_path) / "no-such-run").exists()
    assert not _watch_runs_dir(tmp_path).exists()


def test_watch_tolerates_a_torn_last_line(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(
        tmp_path, "run-a", [1, 2], tail='{"seq": 3, "ts": "2026-10'
    )

    result = _watch("run-a")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["events"] == written


def test_watch_corrupt_journal_is_an_envelope_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # Newline-terminated, so it is not a torn tail: a corrupt line.
    _write_watch_journal(tmp_path, "run-a", [1], tail="not json\n")

    result = _watch("run-a")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CorruptJournalError"


@pytest.mark.parametrize("run_id", ["../escape", "a/b", ".", "..", ""])
def test_watch_refuses_a_run_id_that_is_a_path(tmp_path, monkeypatch, run_id):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # runs/ must exist for "runs/../escape" to resolve on disk, so that without
    # the guard "../escape" really would read the journal one level above it.
    _watch_runs_dir(tmp_path).mkdir(parents=True)
    escape = tmp_path / "xdg" / "agent-manager" / "escape"
    escape.mkdir(parents=True)
    line = store_module.JournalLine(
        seq=1, ts=WATCH_TS, run_id="escape", event="run_upsert"
    ).model_dump(mode="json")
    (escape / store_module.JOURNAL_NAME).write_text(
        json.dumps(line) + "\n", encoding="utf-8"
    )

    result = _watch(run_id)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    # The path guard refused it, not a journal lookup that happened to miss.
    assert "not a run directory name" in envelope["error"]["message"]
    assert list(_watch_runs_dir(tmp_path).iterdir()) == []


def test_watch_all_reads_across_more_than_one_run(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # Written b first, so the order in the output is the sort, not creation order.
    run_b = _write_watch_journal(tmp_path, "run-b", [1, 2])
    run_a = _write_watch_journal(tmp_path, "run-a", [1, 2, 3])

    result = _watch("--all")

    assert result.exit_code == 0, result.output
    events = json.loads(result.stdout)["data"]["events"]
    assert events == run_a + run_b
    assert [(event["run_id"], event["seq"]) for event in events] == [
        ("run-a", 1),
        ("run-a", 2),
        ("run-a", 3),
        ("run-b", 1),
        ("run-b", 2),
    ]


def test_watch_all_applies_since_to_each_runs_own_seq(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    run_a = _write_watch_journal(tmp_path, "run-a", [1, 2, 3])
    run_b = _write_watch_journal(tmp_path, "run-b", [1, 2, 3, 4])

    result = _watch("--all", "--since", "2")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["events"] == run_a[2:] + run_b[2:]


def test_watch_all_with_no_runs_directory_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = _watch("--all")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"ok": True, "data": {"events": []}}
    assert not _watch_runs_dir(tmp_path).exists()


def test_watch_all_skips_a_run_with_no_journal_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    run_a = _write_watch_journal(tmp_path, "run-a", [1])
    (_watch_runs_dir(tmp_path) / "run-not-started").mkdir()
    (_watch_runs_dir(tmp_path) / "stray.txt").write_text("not a run\n")

    result = _watch("--all")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["events"] == run_a
    assert sorted(p.name for p in (_watch_runs_dir(tmp_path) / "run-not-started").iterdir()) == []


def test_watch_all_corrupt_journal_is_an_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1])
    _write_watch_journal(tmp_path, "run-b", [1], tail="not json\n")

    result = _watch("--all")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CorruptJournalError"


def test_watch_rejects_run_id_with_all_and_neither(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _write_watch_journal(tmp_path, "run-a", [1])

    for argv in (["run-a", "--all"], []):
        result = _watch(*argv)
        assert result.exit_code == cli.EXIT_ERROR, (argv, result.output)
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is False, argv
        assert envelope["error"]["type"] == "CliError", argv
        message = envelope["error"]["message"]
        assert "RUN_ID" in message and "--all" in message, argv


# ── am watch --follow (card cba3e48f) ──────────────────────────────────────
#
# Default (unit) tier per design §14, like the one-shot tests above: no git,
# no brd, no harness. Polling is driven by replacing `cli._watch_sleep` and
# bounding `cli.WATCH_MAX_POLLS`, so each poll sees exactly what the test wrote
# before it, with no wall-clock wait and no signal.


def _watch_line(run_id: str, seq: int) -> dict[str, Any]:
    """One journal line in the shape `_write_watch_journal` writes, JSON-mode."""
    return store_module.JournalLine(
        seq=seq,
        ts=WATCH_TS,
        run_id=run_id,
        event="phase_upsert",
        card="card-1",
        phase="implement",
        attempt=1,
        payload={"status": "started", "n": seq},
    ).model_dump(mode="json")


def _append_watch_journal(
    tmp_path: Path, run_id: str, seqs: list[int], *, tail: str = ""
) -> list[dict[str, Any]]:
    """Append one line per seq to `run_id`'s journal, then `tail` verbatim."""
    run_dir = _watch_runs_dir(tmp_path) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    dumped = [_watch_line(run_id, seq) for seq in seqs]
    text = "".join(json.dumps(line, sort_keys=True) + "\n" for line in dumped)
    with (run_dir / store_module.JOURNAL_NAME).open("a", encoding="utf-8") as handle:
        handle.write(text + tail)
    return dumped


def _watch_follow(monkeypatch, *args: str, actions=()):
    """Run `am watch ARGS --follow` for exactly `len(actions)` polls after the backlog.

    Sleep `i` runs `actions[i]` (an append, a delete, a takeover) before poll
    `i` reads, so every poll sees a known state. Returns the result and the
    seconds each sleep was asked for.
    """
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        actions[len(sleeps) - 1]()

    monkeypatch.setattr(cli, "_watch_sleep", fake_sleep)
    monkeypatch.setattr(cli, "WATCH_MAX_POLLS", len(actions))
    return runner.invoke(cli.app, ["watch", *args, "--follow"]), sleeps


def _stream(result) -> list[dict[str, Any]]:
    """Every stdout line of a follow run, parsed; each must be one JSON object."""
    return [json.loads(line) for line in result.stdout.splitlines()]


def _hello(tmp_path: Path) -> dict[str, Any]:
    return {
        "event": "watch",
        "schema": 1,
        "am": agent_manager.__version__,
        "runs_dir": str(_watch_runs_dir(tmp_path)),
    }


def test_watch_follow_hello_line_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    written = _write_watch_journal(tmp_path, "run-a", [1, 2, 3, 4])

    result, sleeps = _watch_follow(monkeypatch, "run-a", "--since", "2")

    assert result.exit_code == 0, result.output
    assert sleeps == []
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    assert lines[1:] == written[2:]
    assert [line["seq"] for line in lines[1:]] == [3, 4]
    # Bare JournalLines: no envelope, and compact, one object per line.
    assert all("ok" not in line for line in lines)
    assert result.stdout.endswith("\n")
    assert all(": " not in text for text in result.stdout.splitlines())
    assert result.stderr == ""

    # `--pretty` only shapes a refusal: the stream is byte-for-byte the same.
    pretty, _ = _watch_follow(monkeypatch, "run-a", "--since", "2", "--pretty")
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == result.stdout


def test_watch_follow_refusal_prints_envelope_and_no_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result, sleeps = _watch_follow(monkeypatch, "no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert sleeps == []
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]
    assert "event" not in envelope
    assert not (_watch_runs_dir(tmp_path) / "no-such-run").exists()
    assert not _watch_runs_dir(tmp_path).exists()

    # `--pretty` still indents a refusal, and it is still the only output.
    pretty, _ = _watch_follow(monkeypatch, "no-such-run", "--pretty")
    assert pretty.exit_code == cli.EXIT_ERROR, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == envelope

    # Every other refusal the one-shot form makes is made before the stream too.
    _write_watch_journal(tmp_path, "run-a", [1])
    _write_watch_journal(tmp_path, "run-c", [1], tail="not json\n")
    for argv, kind in (
        (["../escape"], "UnknownRunError"),
        (["run-a", "--since", "-1"], "CliError"),
        (["--all", "run-a"], "CliError"),
        ([], "CliError"),
        (["run-c"], "CorruptJournalError"),
    ):
        refused, sleeps = _watch_follow(monkeypatch, *argv)
        assert refused.exit_code == cli.EXIT_ERROR, (argv, refused.output)
        assert sleeps == [], argv
        refusal_lines = refused.stdout.splitlines()
        assert len(refusal_lines) == 1, (argv, refused.stdout)
        refusal = json.loads(refusal_lines[0])
        assert refusal["ok"] is False, argv
        assert refusal["error"]["type"] == kind, argv


def test_watch_follow_observes_a_line_appended_after_start(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    backlog = _write_watch_journal(tmp_path, "run-a", [1, 2])
    appended: list[dict[str, Any]] = []

    def append_third() -> None:
        appended.extend(_append_watch_journal(tmp_path, "run-a", [3]))

    # Poll 1 sees seq 3; poll 2 sees nothing new, so seq 3 must not repeat.
    result, sleeps = _watch_follow(
        monkeypatch, "run-a", actions=[append_third, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert cli.WATCH_POLL_SECONDS == 0.25
    assert sleeps == [cli.WATCH_POLL_SECONDS, cli.WATCH_POLL_SECONDS]
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    assert lines[1:] == backlog + appended
    assert [line["seq"] for line in lines[1:]] == [1, 2, 3]


def test_watch_follow_survives_lease_takeover(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    old_owner = store_module.Journal("run-t")
    for _ in range(2):
        old_owner.append("phase_upsert", {"by": "old"}, card="card-1", phase="implement", attempt=1)
    # The new owner opens the journal now and caches seq 2, while the stuck
    # old owner is still appending: only `reseek` keeps it from reusing seq 3.
    new_owner = store_module.Journal("run-t")

    def old_owner_keeps_writing() -> None:
        for _ in range(2):
            old_owner.append("phase_upsert", {"by": "old"}, card="card-1", phase="implement", attempt=1)

    def new_owner_takes_over() -> None:
        new_owner.reseek()
        for _ in range(2):
            new_owner.append("phase_upsert", {"by": "new"}, card="card-1", phase="implement", attempt=1)

    result, _ = _watch_follow(
        monkeypatch,
        "run-t",
        actions=[old_owner_keeps_writing, new_owner_takes_over, lambda: None],
    )

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    events = lines[1:]
    seqs = [event["seq"] for event in events]
    assert seqs == [1, 2, 3, 4, 5, 6]  # strictly increasing, contiguous, no repeat
    assert [event["payload"]["by"] for event in events] == ["old"] * 4 + ["new"] * 2
    assert {event["run_id"] for event in events} == {"run-t"}


def test_watch_follow_all_picks_up_a_run_created_later(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    # Nothing to watch yet: the hello line alone, and nothing created under runs/.
    idle, _ = _watch_follow(monkeypatch, "--all", actions=[lambda: None, lambda: None])
    assert idle.exit_code == 0, idle.output
    assert _stream(idle) == [_hello(tmp_path)]
    assert not _watch_runs_dir(tmp_path).exists()

    third = json.dumps(_watch_line("run-new", 3), sort_keys=True)
    created: list[dict[str, Any]] = []

    def create_run_with_a_torn_tail() -> None:
        created.extend(
            _write_watch_journal(tmp_path, "run-new", [1, 2], tail=third[:20])
        )

    def finish_the_torn_line() -> None:
        journal = _watch_runs_dir(tmp_path) / "run-new" / store_module.JOURNAL_NAME
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(third[20:] + "\n")

    result, _ = _watch_follow(
        monkeypatch,
        "--all",
        actions=[lambda: None, create_run_with_a_torn_tail, finish_the_torn_line],
    )

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    # Seqs 1 and 2 on poll 2 with seq 3 held back as a write in flight, then seq 3 on poll 3.
    assert lines[1:] == created + [_watch_line("run-new", 3)]


def test_watch_follow_tolerates_a_journal_that_disappears(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    backlog = _write_watch_journal(tmp_path, "run-a", [1])
    journal = _watch_runs_dir(tmp_path) / "run-a" / store_module.JOURNAL_NAME
    recreated: list[dict[str, Any]] = []

    def delete_journal() -> None:
        journal.unlink()

    def recreate_journal() -> None:
        recreated.extend(_write_watch_journal(tmp_path, "run-a", [1, 2]))

    result, _ = _watch_follow(
        monkeypatch, "run-a", actions=[delete_journal, recreate_journal]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path)
    # Seq 1 is not repeated: the run's cursor outlived the missing file.
    assert lines[1:] == backlog + recreated[1:]
