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
import dataclasses
import io
import inspect
import json
import os
import re
import shutil
import socket
import stat
import sqlite3
import subprocess
import sys
import threading
import tomllib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest
import eventlines
import typer
from typer.testing import CliRunner

import agent_manager
from agent_manager import (
    argv_guard,
    board,
    census,
    cli,
    control,
    dag,
    detach,
    dispatch,
    errors,
    export,
    integration,
    locks,
    models,
    orchestrate,
    paths,
    prompt,
    runs,
)
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import leases as store_leases
from agent_manager.store import projects as store_projects
from agent_manager.store import queries as store_queries
from agent_manager.store import replay as store_replay
from agent_manager.store import writer as store_writer
from agent_manager.errors import AgentPhaseFailed
from agent_manager.harness import launcher
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


STATUS_HEADER_KEYS = {
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "story_id",
}
"""`am status`'s `data.run`: `cli.RUN_IDENTITY`'s seven names plus `story_id`."""

CONFIG_ONLY_KEYS = {"config", "max_concurrent_stories", "dry_run", "launcher", "harness_map"}
"""`config` and its fields other than `story_id`, none of which the header shows."""


def test_the_status_header_is_the_runs_identity_plus_story_id_and_not_its_config():
    """The spec's seven identity fields plus `story_id`, and `config` is not one
    of them: the header is what `runs` prints for the same run, and a
    workflow's whole config blob in it would drown the reading and let the two
    commands disagree."""
    run = _pure_run([])
    run.config = models.RunConfig(max_concurrent_stories=4, dry_run=True)

    payload = cli.status_payload(run)

    assert set(payload["run"]) == STATUS_HEADER_KEYS
    assert not CONFIG_ONLY_KEYS & set(payload["run"])
    assert payload["run"]["story_id"] is None


def test_the_status_header_of_a_story_run_carries_its_story_id_through_render():
    story = "2aeb8b6e-b24f-4d4e-ab81-138f8d7dfbae"
    run = _pure_run([])
    run.workflow = "milestone"
    run.config = models.RunConfig(max_concurrent_stories=1, story_id=story)

    payload = cli.status_payload(run)
    data = json.loads(cli.render(cli.ok_envelope(payload)))["data"]

    assert payload["run"]["story_id"] == story
    assert data["run"]["story_id"] == story
    assert set(data["run"]) == STATUS_HEADER_KEYS


FALLBACK = launcher.ISOLATION_NONE_WARNING
"""`--isolation auto`'s warning when neither bwrap nor unshare can start."""


def test_the_status_payload_shows_a_recorded_isolation_warning_at_the_top_level():
    """A5 spec test 10: `warnings` is a top-level key; the header is unchanged."""
    run = _pure_run([])
    run.config = models.RunConfig(isolation_warning=FALLBACK)

    payload = cli.status_payload(run)

    assert payload["warnings"] == [FALLBACK]
    assert set(payload["run"]) == STATUS_HEADER_KEYS
    assert not CONFIG_ONLY_KEYS & set(payload["run"])


@pytest.mark.parametrize("mode", ["direct", "bwrap", "unshare"])
def test_the_status_payload_of_a_run_without_a_warning_has_empty_warnings(mode):
    run = _pure_run([])
    run.config = models.RunConfig(launcher=mode)

    assert cli.status_payload(run)["warnings"] == []


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
    # reflects only what `run_card` itself adds to the repo. A brd that leaves
    # nothing untracked makes that commit empty, hence `--allow-empty`.
    _git(root, "add", "-A")
    _git(root, "commit", "--allow-empty", "-m", "brd init")
    return root


@pytest.fixture
def cards(project, fake_board) -> dict[str, str]:
    """A milestone -> story -> subtask chain, the shape `run --card` requires.

    The cards live in the in-memory `fake_board` (test-tier V5); `project` still
    supplies the real git repo the CLI runs in.
    """
    milestone = fake_board.add_card("Milestone 1: walking skeleton")
    story = fake_board.add_card("The CLI: run, status, logs, resume", parent_id=milestone)
    subtask = fake_board.add_card("Add run --card end to end", parent_id=story)
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.brd
@pytest.mark.git
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
    store = store_writer.Store.open(root, run_id)
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


@pytest.mark.git
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
    store = store_writer.Store.open(root, run_id)
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


@pytest.mark.git
def test_the_command_prints_an_ok_envelope_and_exits_zero(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "done"
    assert "\n" not in result.stdout.strip()


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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
    row = sqlite3.connect(paths.db_path()).execute(
        "SELECT started_at FROM runs WHERE id = ?", (payload["run_id"],)
    ).fetchone()
    assert row[0].startswith("2026-09-23T14:05:06")


@pytest.mark.git
def test_the_run_story_and_subtask_rows_land_in_the_machine_db(project, cards):
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    conn = sqlite3.connect(paths.db_path())
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


@pytest.mark.git
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
    assert not eventlines.journal_file(payload["run_id"]).exists()


@pytest.mark.git
def test_the_events_open_with_the_run_story_and_subtask_lines(project, cards):
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    lines = eventlines.run_lines(payload["run_id"])
    assert [line.event for line in lines[:3]] == [
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
    ]


@pytest.mark.git
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
    conn = sqlite3.connect(paths.db_path())
    try:
        assert conn.execute(
            "SELECT count(*) FROM runs WHERE id = ?", (payload["run_id"],)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM subtasks WHERE run_id = ?", (payload["run_id"],)
        ).fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.git
def test_an_unknown_card_is_an_envelope_with_brds_own_message(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, "no-such-card")

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "BoardError"
    assert "no-such-card" in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs").exists()


@pytest.mark.git
def test_a_parentless_card_is_refused_before_a_run_exists(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["milestone"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "ParentlessCardError"
    assert cards["milestone"] in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs").exists()


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_no_harness_is_ever_launched(project, cards, monkeypatch):
    """§14's adapter rule at the CLI seam: the launcher is injected, so a test
    that gets as far as launching one has already failed. `launcher.get_launcher` is
    the only source of what `default_runner_factory` would hand to a real
    `AgentRunner`."""

    def forbidden(*args, **kwargs):
        raise AssertionError("the CLI launched a harness process")

    monkeypatch.setattr(cli.launcher, "get_launcher", lambda kind: forbidden)
    monkeypatch.setattr(cli.dispatch, "AgentRunner", forbidden)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )
    assert payload["status"] == "done"


@pytest.mark.git
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


VERIFY_CARD_ID = "cbe34d00-9d8d-4f41-9c94-f99e665771b0"
VERIFY_STORY_ID = "story-1"
"""Literal ids for the tests that replace `run_card` outright: the card is never
looked up, so no repo or board is built for it (test-tier V5)."""


def test_repeated_verify_options_reach_run_card_in_command_line_order(tmp_path, monkeypatch):
    """§12's suite is the caller's to supply, and the engine runs the commands in
    sequence -- so the order the operator typed is behaviour, not decoration."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen["card_id"] = card_id
        seen.update(kwargs)
        return _fake_payload(card_id, VERIFY_STORY_ID)

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(
        tmp_path,
        VERIFY_CARD_ID,
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
    )

    assert result.exit_code == 0, result.output
    assert list(seen["commands"]) == ["uv run pytest", "uv run ruff check"]


def test_no_verify_option_means_an_empty_command_list_not_none(tmp_path, monkeypatch):
    """`gate_context` calls `list(commands)` and `verification_gate` tells an
    empty suite apart from a missing one, so `None` here would be a crash or a
    silently different verdict."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, VERIFY_STORY_ID)

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(tmp_path, VERIFY_CARD_ID)

    assert result.exit_code == 0, result.output
    assert seen["commands"] == []


def test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties(
    tmp_path, monkeypatch
):
    """Review Focus: one occurrence is one whole command string. The CLI does no
    word-splitting, no parsing and no validation -- whether a command is nonsense
    is the engine's business, not this layer's."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, VERIFY_STORY_ID)

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(
        tmp_path,
        VERIFY_CARD_ID,
        "--verify",
        "uv run pytest -k 'not slow'",
        "--verify",
        "",
    )

    assert result.exit_code == 0, result.output
    assert list(seen["commands"]) == ["uv run pytest -k 'not slow'", ""]


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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
def milestone_board(project, fake_board) -> dict[str, Any]:
    """A board shaped like milestone 2, next to a decoy milestone, seeded into
    the in-memory `fake_board` (test-tier V5).

    B is blocked by A and C by B. Each story's subtasks are chained the same
    way. FakeBoard answers no `brd block`, so every edge is seeded with
    `blocked_by` when the card is created, and the census order does not
    depend on creation timestamps. The decoy root shares the word "skeleton",
    so only a longer substring names milestone 2.
    """
    fake_board.add_card("Milestone 1: walking skeleton")
    milestone = fake_board.add_card("Milestone 2: make the skeleton real")
    stories: dict[str, str] = {}
    subtasks: dict[str, list[str]] = {}
    titles: dict[str, str] = {}
    previous_story: str | None = None
    for key, story_title, subtask_titles in M2_SHAPE:
        story = fake_board.add_card(
            story_title,
            parent_id=milestone,
            blocked_by=[previous_story] if previous_story is not None else [],
        )
        titles[story] = story_title
        chain: list[str] = []
        for subtask_title in subtask_titles:
            subtask = fake_board.add_card(
                subtask_title, parent_id=story, blocked_by=chain[-1:]
            )
            titles[subtask] = subtask_title
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_a_title_substring_names_the_same_milestone_as_its_id(
    project, milestone_board, monkeypatch
):
    _forbid_writes(monkeypatch)

    by_id = _dry_run(project, milestone_board["milestone"])
    by_title = _dry_run(project, "skeleton real")

    assert by_id.exit_code == 0, by_id.output
    assert by_title.exit_code == 0, by_title.output
    assert json.loads(by_title.stdout) == json.loads(by_id.stdout)


@pytest.mark.git
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


@pytest.mark.brd
@pytest.mark.git
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


@pytest.mark.brd
@pytest.mark.git
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


@pytest.mark.brd
@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.brd
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
                "launcher": "bwrap",
                "isolation_warning": None,
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

    result = _milestone_run(tmp_path)

    assert result.exit_code == cli.EXIT_ESCALATED, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(ESCALATED_MILESTONE)


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
        # Card 7ffee8c4: a write stayed busy through its whole retry budget.
        store_db.StoreBusyError("beat", 5, 10.0),
    ],
    ids=["CliError", "BoardError", "ValueError", "LockTimeoutError", "StoreBusyError"],
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


@pytest.mark.parametrize("status", ["done", "merged", "canceled"])
@pytest.mark.parametrize("branch_prefix", [None, "sprint9"])
def test_board_prefix_of_ignores_status_so_a_finished_milestone_keeps_its_prefix(
    status, branch_prefix
):
    """Card 8198b0b4: a blocker that is no longer open gets the prefix it ran under."""
    prefix_of = cli.board_prefix_of(branch_prefix)
    finished = BOARD_CARD.model_copy(update={"status": status})

    assert prefix_of(finished) == prefix_of(BOARD_CARD)


BLANK_BOARD_PREFIX = "--branch-prefix with --board needs a non-blank prefix, not a blank string"
PREFIX_REQUIRED = "--branch-prefix is required with --card, --milestone or --story"


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
            "one of --card, --milestone, --story or --board is required",
            "'--card' / '--milestone' / '--story' / '--board'",
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
    monkeypatch.setattr(store_writer, "Store", _Forbidden("store_writer.Store"))
    monkeypatch.setattr(cli, "refuse_claimed", _Forbidden("refuse_claimed"))
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    monkeypatch.setattr(orchestrate, "run_board", _Forbidden("run_board"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))


def _expected_board_milestone(
    card: models.CardNode,
    prefix: str,
    *,
    root: Path,
    max_concurrent: int,
    base_branch: str = "main",
) -> dict[str, Any]:
    return {
        "milestone_id": card.id,
        "title": card.title,
        "branch_prefix": prefix,
        "base_branch": base_branch,
        "plan": cli.dry_run_payload(
            census.flatten_milestone(card).stories,
            repo_dir=root,
            branch_prefix=prefix,
            base_branch=base_branch,
            max_concurrent=max_concurrent,
        ),
    }


def _answer_local_branches(monkeypatch, existing: set[str]) -> dict[str, list[Any]]:
    """Replace `orchestrate._local_branch_exists` with a fake that answers from `existing`.

    No `git` runs. Returns what the fake saw: `roots`, each repo root the
    factory was built for, and `asked`, each branch it was asked about, in order.
    """
    seen: dict[str, list[Any]] = {"roots": [], "asked": []}

    def factory(root: Path):
        seen["roots"].append(root)

        def exists(branch: str) -> bool:
            seen["asked"].append(branch)
            return branch in existing

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", factory)
    return seen


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


@pytest.mark.git
@pytest.mark.brd
def test_the_board_dry_run_previews_every_open_milestone_by_level_and_writes_nothing(
    project, monkeypatch
):
    """Spec test 13: two milestones, the second blocked by the first, on a real
    temporary brd board. Two levels, each milestone with its derived prefix and
    its own `dry_run_payload` nested as `plan`; the second milestone stacks on
    the first's `<prefix>-integrate`, reported as `base_branch` and planned
    against; nothing run, nothing written."""
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
    first_prefix = dag.task_stem(roots[first])
    expected = [
        {
            "level": 0,
            "milestones": [
                _expected_board_milestone(
                    roots[first],
                    first_prefix,
                    root=root,
                    max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                )
            ],
        },
        {
            "level": 1,
            "milestones": [
                _expected_board_milestone(
                    roots[second],
                    dag.task_stem(roots[second]),
                    root=root,
                    max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                    base_branch=integration.integration_branch(first_prefix),
                )
            ],
        },
    ]
    assert data["levels"] == _as_json(expected)
    for level in data["levels"]:
        for entry in level["milestones"]:
            assert set(entry) == {
                "milestone_id",
                "title",
                "branch_prefix",
                "base_branch",
                "plan",
            }
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
    # The done milestone blocks `second`, so its integrate branch is looked up;
    # the fake answers "absent" without spawning git (unit tier).
    seen = _answer_local_branches(monkeypatch, set())

    result = _board_dry_run(tmp_path, "--branch-prefix", "sprint9", "--max-concurrent", "2")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    second_prefix = f"sprint9-{dag.task_stem(second)}"
    assert data == _as_json(
        {
            "board": True,
            "max_concurrent": 2,
            "levels": [
                {
                    "level": 0,
                    "milestones": [
                        _expected_board_milestone(
                            second, second_prefix, root=root, max_concurrent=2
                        )
                    ],
                },
                {
                    "level": 1,
                    "milestones": [
                        _expected_board_milestone(
                            third,
                            f"sprint9-{dag.task_stem(third)}",
                            root=root,
                            max_concurrent=2,
                            base_branch=integration.integration_branch(second_prefix),
                        )
                    ],
                },
            ],
        }
    )
    assert seen["asked"] == [integration.integration_branch(f"sprint9-{dag.task_stem(done)}")]
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


def test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id(tmp_path, monkeypatch):
    """Card a7fcc076: the `--board --dry-run` shape, pinned for the README.

    `data` is exactly `{board, max_concurrent, levels}`; each level is
    `{level, milestones}`; each milestone is exactly `{milestone_id, title,
    branch_prefix, base_branch, plan}` (card 5bfe746d added `base_branch`, the
    branch the milestone starts from), and `plan` is that milestone's `dry_run_payload`
    (`{max_concurrent, levels, already_done, integrate}`). `ok` is only on
    the envelope, never inside `data`, and nothing carries a `run_id`: a
    preview mints no run and opens no Store."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    first = _board_milestone(1)
    second = _board_milestone(2, blocked_by=(first.id,))
    _serve_roots(monkeypatch, [first, second])
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == {"board", "max_concurrent", "levels"}
    assert "ok" not in data
    assert "run_id" not in data
    assert data["board"] is True
    assert [set(level) for level in data["levels"]] == [{"level", "milestones"}] * 2
    entries = [entry for level in data["levels"] for entry in level["milestones"]]
    assert [entry["milestone_id"] for entry in entries] == [first.id, second.id]
    for entry in entries:
        assert set(entry) == {"milestone_id", "title", "branch_prefix", "base_branch", "plan"}
        assert set(entry["plan"]) == {"max_concurrent", "levels", "already_done", "integrate"}
        assert "ok" not in entry["plan"]
        assert "run_id" not in entry["plan"]
    assert list(paths.data_dir().iterdir()) == []


def test_the_board_dry_run_puts_independent_milestones_on_the_base_branch(
    tmp_path, monkeypatch
):
    """Card 5bfe746d, spec T2: no `blocked_by` between milestones, so each entry's
    `base_branch` is `--base-branch` and its plan is today's, and git is never asked."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    first = _board_milestone(1)
    second = _board_milestone(2)
    _serve_roots(monkeypatch, [first, second])
    _forbid_board_dry_run_writes(monkeypatch)
    seen = _answer_local_branches(monkeypatch, set())

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        card,
                        dag.task_stem(card),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                    )
                    for card in (first, second)
                ],
            }
        ]
    )
    assert seen["asked"] == []
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.parametrize("given", [None, "sprint9"], ids=["derived-prefix", "given-prefix"])
def test_the_board_dry_run_stacks_a_milestone_on_its_open_blockers_integrate_branch(
    tmp_path, monkeypatch, given
):
    """Card 5bfe746d, spec T3/T10: M2 blocked by the open M1 starts from M1's
    `<prefix>-integrate`. That is its `base_branch`, its plan is computed
    against it (story `root`, first subtask `base`, integrate tips), and with
    `--branch-prefix sprint9` it reads `sprint9-<stem(M1)>-integrate`. An open
    blocker never asks git; nothing is written."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    first = _board_milestone(1)
    second = _board_milestone(2, blocked_by=(first.id,))
    _serve_roots(monkeypatch, [first, second])
    _forbid_board_dry_run_writes(monkeypatch)
    seen = _answer_local_branches(monkeypatch, set())
    extra = () if given is None else ("--branch-prefix", given)

    def prefix(card: models.CardNode) -> str:
        stem = dag.task_stem(card)
        return stem if given is None else f"{given}-{stem}"

    result = _board_dry_run(tmp_path, *extra)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    stacked = integration.integration_branch(prefix(first))
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        first,
                        prefix(first),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                    )
                ],
            },
            {
                "level": 1,
                "milestones": [
                    _expected_board_milestone(
                        second,
                        prefix(second),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                        base_branch=stacked,
                    )
                ],
            },
        ]
    )
    first_entry = data["levels"][0]["milestones"][0]
    second_entry = data["levels"][1]["milestones"][0]
    assert first_entry["base_branch"] == "main"
    assert second_entry["base_branch"] == stacked
    story_row = second_entry["plan"]["levels"][0]["stories"][0]
    assert story_row["root"] == stacked
    assert story_row["subtasks"][0]["base"] == stacked
    assert seen["asked"] == []
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.parametrize("exists", [True, False], ids=["branch-kept", "branch-gone"])
def test_the_board_dry_run_stacks_on_a_done_blockers_branch_only_while_it_exists_locally(
    tmp_path, monkeypatch, exists
):
    """Card 5bfe746d, spec T4/T10: M1 is `done` (so not previewed) and blocks M2.
    M2 stacks on `<stem(M1)>-integrate` only when that local branch exists;
    otherwise it starts from `--base-branch`, today's behaviour. The git seam is
    built for the resolved repo dir and asked exactly that one branch."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    done = _board_milestone(1, status="done")
    second = _board_milestone(2, blocked_by=(done.id,))
    _serve_roots(monkeypatch, [done, second])
    _forbid_board_dry_run_writes(monkeypatch)
    integrate = integration.integration_branch(dag.task_stem(done))
    seen = _answer_local_branches(monkeypatch, {integrate} if exists else set())

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        second,
                        dag.task_stem(second),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                        base_branch=integrate if exists else "main",
                    )
                ],
            }
        ]
    )
    assert seen == {"roots": [root], "asked": [integrate]}
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.parametrize("status", ["merged", "canceled", "archived"])
def test_the_board_dry_run_never_stacks_on_or_asks_git_about_a_landed_blocker(
    tmp_path, monkeypatch, status
):
    """Card 5bfe746d, spec T5: a landed blocker is ignored even when its integrate
    branch would answer "exists"; git is never asked and M2 starts from `main`."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    landed = _board_milestone(1, status=status)
    second = _board_milestone(2, blocked_by=(landed.id,))
    _serve_roots(monkeypatch, [landed, second])
    _forbid_board_dry_run_writes(monkeypatch)
    seen = _answer_local_branches(
        monkeypatch, {integration.integration_branch(dag.task_stem(landed))}
    )

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        second,
                        dag.task_stem(second),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                    )
                ],
            }
        ]
    )
    assert seen["asked"] == []
    assert list(paths.data_dir().iterdir()) == []


def test_the_board_dry_run_stacks_on_the_one_open_blocker_among_landed_and_unknown_ones(
    tmp_path, monkeypatch
):
    """Review focus: M3 is blocked by the open M1, the merged M2 and an id that is
    no milestone on the board. Only M1 is a stack candidate, so M3 starts from
    M1's integrate branch: no `MilestoneBlockersError`, and git is never asked."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    first = _board_milestone(1)
    merged = _board_milestone(2, status="merged")
    third = _board_milestone(3, blocked_by=(first.id, merged.id, _plan_id(99)))
    _serve_roots(monkeypatch, [first, merged, third])
    _forbid_board_dry_run_writes(monkeypatch)
    seen = _answer_local_branches(monkeypatch, set())

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    entries = {
        entry["milestone_id"]: entry
        for level in data["levels"]
        for entry in level["milestones"]
    }
    assert set(entries) == {first.id, third.id}
    assert entries[first.id]["base_branch"] == "main"
    assert entries[third.id]["base_branch"] == integration.integration_branch(
        dag.task_stem(first)
    )
    assert seen["asked"] == []
    assert list(paths.data_dir().iterdir()) == []


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
        (
            [
                _board_milestone(1),
                _board_milestone(2),
                _board_milestone(3, blocked_by=(_plan_id(1), _plan_id(2))),
            ],
            "MilestoneBlockersError",
        ),
        (
            [
                _board_milestone(1, status="done", title="Milestone twin"),
                _board_milestone(
                    2,
                    card_id="00000001-0000-4000-8000-000000000001",
                    title="Milestone twin",
                    blocked_by=(_plan_id(1),),
                ),
            ],
            "ValueError",
        ),
    ],
    ids=[
        "milestone-cycle",
        "non-uuid-milestone",
        "shared-derived-prefix",
        "two-open-blockers",
        "blocker-shares-a-prefix",
    ],
)
def test_a_board_dry_run_refusal_is_an_envelope_and_writes_nothing(
    tmp_path, monkeypatch, roots, error_type
):
    """Review focus: a cycle among milestones, a milestone id `dag.task_stem`
    cannot read, two milestones deriving one prefix, a milestone blocked by two
    open milestones (`MilestoneBlockersError`) and a done blocker root deriving
    an open milestone's prefix are each the same `HANDLED` refusal the real run
    gives: `ok: false`, exit 3. None of these cases asks git."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _serve_roots(monkeypatch, roots)
    _forbid_board_dry_run_writes(monkeypatch)

    error = _refusal(_board_dry_run(tmp_path))

    assert error["type"] == error_type
    assert list(paths.data_dir().iterdir()) == []


def _bare_main_repo(path: Path) -> Path:
    """A real git repo at `path` on `main` with one empty commit, no brd board."""
    path.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True, text=True
    )
    _git(path, "config", "user.email", "tests@example.com")
    _git(path, "config", "user.name", "agent-manager tests")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "commit", "--allow-empty", "-m", "base")
    return path


@pytest.mark.git
@pytest.mark.parametrize(
    "ref_kind, stacks", [("branch", True), ("tag", False)], ids=["local-branch", "tag-only"]
)
def test_the_board_dry_run_asks_real_git_for_a_done_blockers_local_integrate_branch(
    tmp_path, monkeypatch, ref_kind, stacks
):
    """Card 5bfe746d, spec T9: the unreplaced `_local_branch_exists` against a real
    repo. M1 is `done` and blocks M2. A local branch `<stem(M1)>-integrate`
    stacks M2 on it; a tag of the same name, with no branch, does not, and M2
    stays on `main`. The repo's branches, tags and work tree are untouched."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    repo = _bare_main_repo(tmp_path / "repo")
    done = _board_milestone(1, status="done")
    second = _board_milestone(2, blocked_by=(done.id,))
    integrate = integration.integration_branch(dag.task_stem(done))
    _git(repo, ref_kind, integrate)
    _serve_roots(monkeypatch, [done, second])
    _forbid_board_dry_run_writes(monkeypatch)
    refs_before = _git(repo, "show-ref")
    porcelain_before = _git(repo, "status", "--porcelain")

    result = _board_dry_run(repo)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        second,
                        dag.task_stem(second),
                        root=cli.resolve_repo_dir(repo),
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                        base_branch=integrate if stacks else "main",
                    )
                ],
            }
        ]
    )
    assert _git(repo, "show-ref") == refs_before
    assert _git(repo, "status", "--porcelain") == porcelain_before
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.git
def test_the_board_dry_run_lets_a_git_error_from_a_non_repository_propagate(
    tmp_path, monkeypatch
):
    """Card 5bfe746d, spec 2.3 / review focus: `--repo-dir` is no git repository and
    M2's blocker is `done`, so its integrate branch must be looked up. git exits
    128, which is not an answer: the `GitError` propagates (no envelope), as it
    does from `run_board`, rather than silently previewing M2 on `main`."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # Stop git's repository discovery at tmp_path, so an enclosing repo can't answer.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    done = _board_milestone(1, status="done")
    second = _board_milestone(2, blocked_by=(done.id,))
    _serve_roots(monkeypatch, [done, second])
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(not_a_repo)

    assert result.exit_code != 0
    assert isinstance(result.exception, orchestrate.worktree.GitError)
    assert result.exception.exit_code == 128
    assert '"ok"' not in result.stdout
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
        "launcher": "bwrap",
        "isolation_warning": None,
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


def test_a_board_run_envelope_wraps_run_boards_keys_unchanged(tmp_path, monkeypatch):
    """Card a7fcc076: `am run --board` prints `{"ok": true, "data": payload}`
    with `run_board`'s payload untouched: `data` is exactly `{ok, board,
    levels, milestones}`, with no board-level `run_id`, and the done,
    escalated and blocked entries keep their own key sets. The envelope's
    `ok` stays true when `data.ok` is false: an escalation is a truthful
    result, reported through the exit code."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    done_id, escalated_id, blocked_id = _plan_id(1), _plan_id(2), _plan_id(3)
    payload = {
        "ok": False,
        "board": True,
        "levels": [
            {"level": 0, "milestones": [done_id, escalated_id]},
            {"level": 1, "milestones": [blocked_id]},
        ],
        "milestones": [
            {
                "milestone_id": done_id,
                "status": "done",
                "done": True,
                "run_id": "20261001T000000Z-00000001",
            },
            {"milestone_id": escalated_id, "status": "escalated", "error": "RuntimeError: boom"},
            {"milestone_id": blocked_id, "status": "blocked", "blocked_by": [escalated_id]},
        ],
    }
    _patch_run_board(monkeypatch, payload)

    result = _board_run(tmp_path)

    assert result.exit_code == cli.EXIT_ESCALATED, result.output
    envelope = json.loads(result.stdout)
    # Exact equality is the whole pin: any key the CLI adds, drops or renames
    # (a board-level `run_id`, say) breaks it. The payload's own key sets are
    # pinned against the real `run_board` in test_orchestrate.py.
    assert envelope == {"ok": True, "data": payload}


@pytest.mark.parametrize(
    "statuses, exit_code",
    [
        (("done",), 0),
        (("done", "done"), 0),
        ((), 0),
        (("stopped",), 0),
        (("canceled",), 0),
        (("done", "stopped", "blocked"), 0),
        (("canceled", "blocked"), 0),
        (("escalated",), cli.EXIT_ESCALATED),
        (("done", "escalated"), cli.EXIT_ESCALATED),
        (("escalated", "blocked"), cli.EXIT_ESCALATED),
        (("stopped", "escalated", "canceled"), cli.EXIT_ESCALATED),
    ],
)
def test_a_board_run_exits_escalated_only_when_some_milestone_escalated(
    tmp_path, monkeypatch, statuses, exit_code
):
    """Spec test 11: the board-wide form of the milestone rule. A stopped,
    canceled or blocked milestone is not an escalation; an empty board is clean.
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
        orchestrate.MilestoneBlockersError(
            "milestone X is blocked by 2 milestones that are not landed (A, B); "
            "a milestone stacks on at most one: chain them (A <- B <- C)"
        ),
    ],
    ids=["ValueError", "DependencyCycleError", "BoardError", "CliError", "MilestoneBlockersError"],
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
    workflow: str = "task",
    milestone_id: str | None = None,
    story_id: str | None = None,
    verify: tuple[str, ...] = (),
    allow_no_verification: bool = False,
    launcher: models.Launcher = "direct",
    isolation_warning: str | None = None,
) -> None:
    """One run -- story, subtask, and optionally two phases and two attempts --
    in `root`'s projection, written the only way this program writes rows. The
    defaults are a `--card`-shaped run; pass `workflow="milestone"` and a
    `milestone_id` for a milestone-shaped one, plus a `story_id` for a
    story-shaped one."""
    opened = store_writer.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status=status,
                started_at=started_at,
                config=models.RunConfig(
                    story_id=story_id,
                    verify=list(verify),
                    allow_no_verification=allow_no_verification,
                    launcher=launcher,
                    isolation_warning=isolation_warning,
                ),
                milestone_id=milestone_id,
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


SNAPSHOT_RUN_ID = "20260923T090000Z-cbe34d00"


def _events_head(root: Path) -> int:
    """`store_events.head` read on a connection of its own, outside any command."""
    conn = store_db.open_db_for_reading(cli.resolve_repo_dir(root))
    try:
        return store_events.head(conn)
    finally:
        conn.close()


_HEX_ID = re.compile(r"[0-9a-f]{32}")


def _store_id(root: Path) -> str | None:
    """`store_db.store_id` read on a connection of its own, outside any command."""
    conn = store_db.open_db_for_reading(cli.resolve_repo_dir(root))
    try:
        return store_db.store_id(conn)
    finally:
        conn.close()


def _write_once_after(monkeypatch, module, name: str, write) -> list[bool]:
    """Patch `module.name` so it returns what it always did, and on its first
    call only, after the original returned, runs `write`.

    The returned list holds `True` once `write` has run, so a test can assert
    the injection actually fired.
    """
    original = getattr(module, name)
    fired: list[bool] = []

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        if not fired:
            fired.append(True)
            write()
        return result

    monkeypatch.setattr(module, name, wrapper)
    return fired


def test_status_as_of_seq_is_the_events_head(projection):
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    head = _events_head(projection)

    payload = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)

    assert isinstance(payload["as_of_seq"], int)
    assert payload["as_of_seq"] == head > 0
    assert set(payload) == {
        "run",
        "stories",
        "rows",
        "control",
        "warnings",
        "integrity",
        "as_of_seq",
        "store_id",
    }
    argv = ["status", SNAPSHOT_RUN_ID, "--repo-dir", str(projection)]
    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])
    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for result in (plain, pretty):
        assert json.loads(result.stdout)["data"]["as_of_seq"] == head


def test_status_store_id_is_the_meta_store_id(projection):
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    expected = _store_id(projection)

    payload = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)

    assert isinstance(payload["store_id"], str)
    assert _HEX_ID.fullmatch(payload["store_id"])
    assert payload["store_id"] == expected
    argv = ["status", SNAPSHOT_RUN_ID, "--repo-dir", str(projection)]
    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])
    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for result in (plain, pretty):
        assert json.loads(result.stdout)["data"]["store_id"] == expected


def test_status_store_id_is_stable(projection):
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)

    first = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)
    second = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)
    _record(
        projection,
        "20260930T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
    )
    third = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)

    assert first["store_id"] == second["store_id"] == third["store_id"]
    assert _HEX_ID.fullmatch(first["store_id"])
    assert third["as_of_seq"] > first["as_of_seq"]


def test_status_store_id_differs_across_databases(projection, tmp_path, monkeypatch):
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    first = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)["store_id"]

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "other-xdg"))
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    second = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)["store_id"]

    assert _HEX_ID.fullmatch(first)
    assert _HEX_ID.fullmatch(second)
    assert first != second


def test_status_reads_store_id_inside_the_snapshot(projection, monkeypatch):
    """Review Focus 2: on the command's own connection, inside `read_snapshot`."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    opened: list[sqlite3.Connection] = []
    seen: list[tuple[sqlite3.Connection, bool]] = []
    original_open = cli.store_db.open_db_for_reading
    original_store_id = cli.store_db.store_id

    def capture_open(root):
        conn = original_open(root)
        opened.append(conn)
        return conn

    def capture_store_id(conn):
        seen.append((conn, conn.in_transaction))
        return original_store_id(conn)

    monkeypatch.setattr(cli.store_db, "open_db_for_reading", capture_open)
    monkeypatch.setattr(cli.store_db, "store_id", capture_store_id)

    payload = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)

    assert len(opened) == 1
    assert seen == [(opened[0], True)]
    assert _HEX_ID.fullmatch(payload["store_id"])


def test_status_never_shows_state_ahead_of_as_of_seq(projection, monkeypatch):
    """The card's key test: a run and a phase change, each with its event,
    commit right after `head` returns, while `status` is still reading."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT, status="started")
    writer = store_writer.Store.open(projection, SNAPSHOT_RUN_ID)
    try:
        before = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)
        head_before = _events_head(projection)

        def fail_the_run() -> None:
            run = writer.load_run(SNAPSHOT_RUN_ID)
            writer.record_run(run.model_copy(update={"status": "failed"}))
            writer.record_phase(
                "story-1",
                "card-1",
                models.PhaseRun(name="verify", kind="deterministic", status="failed"),
            )
            writer.take_lease(
                token="snapshot-life",
                pid=os.getpid(),
                host=socket.gethostname(),
                now=datetime.now(timezone.utc),
                is_live=lambda row: False,
            )

        fired = _write_once_after(monkeypatch, cli.store_events, "head", fail_the_run)
        during = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)

        assert fired == [True]
        assert during["as_of_seq"] == head_before
        assert during["run"]["status"] == "started"
        assert during["stories"] == before["stories"]
        assert during["rows"] == before["rows"]
        assert during["control"] == before["control"]
        assert during["control"]["lease"] is None
        assert during["integrity"] == before["integrity"]

        after = cli.status_for(SNAPSHOT_RUN_ID, repo_dir=projection)
        assert after["run"]["status"] == "failed"
        assert ("story-1", "card-1", "verify", None, "failed") in [
            (row["story"], row["subtask"], row["phase"], row["attempt"], row["state"])
            for row in after["rows"]
        ]
        assert after["as_of_seq"] > during["as_of_seq"]
        assert after["control"]["lease"] is not None
    finally:
        writer.close()


def test_status_default_run_is_chosen_inside_the_snapshot(projection, monkeypatch):
    """Review Focus 1: a run recorded after `head` must not become the run
    `status` with no RUN reports."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT, status="started")
    newer_id = "20260930T090000Z-cbe34d00"
    writer = store_writer.Store.open(projection, newer_id)
    try:
        head_before = _events_head(projection)

        def start_a_newer_run() -> None:
            writer.record_run(
                models.Run(
                    id=newer_id,
                    workflow="task",
                    repo_dir=projection,
                    base_branch="main",
                    branch_prefix="m1",
                    status="started",
                    started_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
                )
            )

        fired = _write_once_after(
            monkeypatch, cli.store_events, "head", start_a_newer_run
        )
        during = cli.status_for(None, repo_dir=projection)

        assert fired == [True]
        assert during["run"]["id"] == SNAPSHOT_RUN_ID
        assert during["as_of_seq"] == head_before
        assert cli.status_for(None, repo_dir=projection)["run"]["id"] == newer_id
    finally:
        writer.close()


def test_status_error_paths_are_unchanged_and_close_the_snapshot(projection, monkeypatch):
    """Review Focus 2. A guard: the refusals predate this card and already
    close their connection; this pins that the snapshot does not change that."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    other = projection.parent / "no-runs"
    other.mkdir()
    opened: list[sqlite3.Connection] = []
    original = cli.store_db.open_db_for_reading

    def capture(root):
        conn = original(root)
        opened.append(conn)
        return conn

    monkeypatch.setattr(cli.store_db, "open_db_for_reading", capture)

    for argv in (
        ["status", "no-such-run", "--repo-dir", str(projection)],
        ["status", "--repo-dir", str(other)],
    ):
        result = runner.invoke(cli.app, argv)

        assert result.exit_code == cli.EXIT_ERROR, result.output
        envelope = json.loads(result.stdout)
        assert set(envelope) == {"ok", "error"}
        assert envelope["error"]["type"] == "UnknownRunError"
        assert "as_of_seq" not in result.stdout
        assert "store_id" not in result.stdout
    assert len(opened) == 2
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


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


@pytest.mark.parametrize(
    "run_id, exit_code, ok",
    [
        ("20260923T090000Z-cbe34d00", 0, True),
        ("no-such-run", cli.EXIT_ERROR, False),
    ],
    ids=["ok-envelope", "error-envelope"],
)
def test_pretty_renders_through_the_cli(projection, run_id, exit_code, ok):
    """Test-tier V5: the one CliRunner check of `--pretty`. Every command hands its
    envelope to the same `render(..., pretty=pretty)`, and
    `test_render_indents_under_pretty` pins what `render` does, so this checks
    only that the flag reaches it through Typer -- for an ok envelope and for a
    refusal -- on `status`, the cheapest command (no git, no brd, no subprocess)."""
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)
    argv = ["status", run_id, "--repo-dir", str(projection)]

    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])

    assert plain.exit_code == exit_code, plain.output
    assert pretty.exit_code == exit_code, pretty.output
    assert "\n" not in plain.stdout.strip()
    assert "\n  " in pretty.stdout
    envelope = json.loads(pretty.stdout)
    assert envelope == json.loads(plain.stdout)
    assert envelope["ok"] is ok


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


def test_runs_entries_carry_the_project_id_and_its_repo_dir(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    conn = store_db.open_db_for_reading(projection.resolve())
    try:
        project_id = store_projects.lookup(conn, projection.resolve())
    finally:
        conn.close()
    assert entry["project"] == {"id": project_id, "repo_dir": str(projection.resolve())}
    assert entry["repo_dir"] == str(projection.resolve())


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


RUNS_ENTRY_KEYS = {
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "milestone_id",
    "card_id",
    "story_id",
    "lease",
    "progress",
    "project",
}
"""Every `data.runs[]` entry: the seven names `am runs` always had, plus
`milestone_id` and `card_id` (card 0b5a15d7), `lease` (card 6bf47e74),
`progress` (card 882b212b), `story_id` (card 3d2a3ef8) and `project`
(card 5d9554f6)."""

RUNS_LEASE_KEYS = {"live", "pid", "host", "heartbeat_at", "accepting"}
"""A non-null `data.runs[].lease`: `am status`'s `control.lease` minus
`acquired_at`."""

RUNS_MILESTONE_ID = "9c44c2fb-0000-4000-8000-000000000000"


def test_runs_shows_a_card_runs_card_id_and_a_null_milestone_id(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["card_id"] == "card-1"
    assert entry["milestone_id"] is None
    assert entry["story_id"] is None


def test_runs_shows_a_milestone_runs_milestone_id_and_a_null_card_id(projection):
    """`_record` writes a subtask row under the milestone run too, so this also
    pins that a milestone run never reports one of its subtasks as `card_id`."""
    _record(
        projection,
        "20260923T090000Z-cbe34d00",
        started_at=RECORDED_AT,
        workflow="milestone",
        milestone_id=RUNS_MILESTONE_ID,
    )

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["workflow"] == "milestone"
    assert entry["milestone_id"] == RUNS_MILESTONE_ID
    assert entry["card_id"] is None
    assert entry["story_id"] is None


RUNS_STORY_ID = "2aeb8b6e-b24f-4d4e-ab81-138f8d7dfbae"


def test_runs_shows_a_story_runs_story_id_its_milestone_id_and_no_card_id(projection):
    """A story run is a `milestone` run with a subtask row: it lists the parent
    milestone's id and never a `card_id`."""
    _record(
        projection,
        "20260923T090000Z-cbe34d00",
        started_at=RECORDED_AT,
        workflow="milestone",
        milestone_id=RUNS_MILESTONE_ID,
        story_id=RUNS_STORY_ID,
    )

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["story_id"] == RUNS_STORY_ID
    assert entry["workflow"] == "milestone"
    assert entry["milestone_id"] == RUNS_MILESTONE_ID
    assert entry["card_id"] is None


def test_status_on_a_story_run_shows_its_story_id_and_no_config(projection):
    run_id = "20260923T090000Z-cbe34d00"
    _record(
        projection,
        run_id,
        started_at=RECORDED_AT,
        workflow="milestone",
        milestone_id=RUNS_MILESTONE_ID,
        story_id=RUNS_STORY_ID,
    )

    result = runner.invoke(cli.app, ["status", run_id, "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    header = json.loads(result.stdout)["data"]["run"]
    assert header["story_id"] == RUNS_STORY_ID
    assert header["workflow"] == "milestone"
    assert set(header) == STATUS_HEADER_KEYS


def test_am_status_shows_a_recorded_isolation_warning(projection):
    """A5 spec test 10, CLI: how a detached fallback run still says it."""
    run_id = "20260923T090000Z-cbe34d00"
    _record(
        projection, run_id, started_at=RECORDED_AT, status="started", isolation_warning=FALLBACK
    )

    result = runner.invoke(cli.app, ["status", run_id, "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["warnings"] == [FALLBACK]
    assert set(data["run"]) == STATUS_HEADER_KEYS


def test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_card_id_story_id_lease_and_progress(projection):
    _record(
        projection,
        "20260921T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
    )
    _record(
        projection,
        "20260922T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc),
        workflow="milestone",
        milestone_id=RUNS_MILESTONE_ID,
        story_id=RUNS_STORY_ID,
    )
    _record(
        projection,
        "20260923T090000Z-cbe34d00",
        started_at=RECORDED_AT,
        workflow="milestone",
        milestone_id=RUNS_MILESTONE_ID,
    )
    argv = ["runs", "--repo-dir", str(projection)]

    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])

    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for envelope in (json.loads(plain.stdout), json.loads(pretty.stdout)):
        assert set(envelope) == {"ok", "data"}
        assert envelope["ok"] is True
        assert set(envelope["data"]) == {"runs", "as_of_seq", "store_id"}
        assert isinstance(envelope["data"]["as_of_seq"], int)
        assert len(envelope["data"]["runs"]) == 3
        for entry in envelope["data"]["runs"]:
            assert set(entry) == RUNS_ENTRY_KEYS
        assert [entry["story_id"] for entry in envelope["data"]["runs"]] == [
            None,
            RUNS_STORY_ID,
            None,
        ]


SNAPSHOT_OLDER_RUN_ID = "20260922T090000Z-cbe34d00"


def _record_two_started_runs(root: Path) -> None:
    _record(
        root,
        SNAPSHOT_OLDER_RUN_ID,
        started_at=datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc),
        status="started",
    )
    _record(root, SNAPSHOT_RUN_ID, started_at=RECORDED_AT, status="started")


def _statuses(payload: dict[str, Any]) -> dict[str, str]:
    return {entry["id"]: entry["status"] for entry in payload["runs"]}


def test_runs_never_shows_state_ahead_of_as_of_seq(projection, monkeypatch):
    """A run status change, with its event, commits right after `head` returns."""
    _record_two_started_runs(projection)
    writer = store_writer.Store.open(projection, SNAPSHOT_RUN_ID)
    try:
        head_before = _events_head(projection)

        def fail_the_run() -> None:
            run = writer.load_run(SNAPSHOT_RUN_ID)
            writer.record_run(run.model_copy(update={"status": "failed"}))

        fired = _write_once_after(monkeypatch, cli.store_events, "head", fail_the_run)
        during = cli.runs_for(repo_dir=projection)

        assert fired == [True]
        assert during["as_of_seq"] == head_before
        assert _statuses(during) == {
            SNAPSHOT_OLDER_RUN_ID: "started",
            SNAPSHOT_RUN_ID: "started",
        }
        after = cli.runs_for(repo_dir=projection)
        assert _statuses(after)[SNAPSHOT_RUN_ID] == "failed"
        assert after["as_of_seq"] > during["as_of_seq"]
    finally:
        writer.close()


def test_runs_reads_every_lease_inside_the_snapshot(projection, monkeypatch):
    """Review Focus 4, the spec's second T11 variant: the write lands after
    `list_runs` returned and before the first `read_lease`, so only a
    `read_lease` outside the snapshot could see it."""
    _record_two_started_runs(projection)
    writer = store_writer.Store.open(projection, SNAPSHOT_RUN_ID)
    try:
        head_before = _events_head(projection)

        def take_over_the_run() -> None:
            run = writer.load_run(SNAPSHOT_RUN_ID)
            writer.record_run(run.model_copy(update={"status": "failed"}))
            writer.take_lease(
                token="snapshot-life",
                pid=os.getpid(),
                host=socket.gethostname(),
                now=datetime.now(timezone.utc),
                is_live=lambda row: False,
            )

        fired = _write_once_after(
            monkeypatch, cli.store_queries, "list_runs", take_over_the_run
        )
        during = cli.runs_for(repo_dir=projection)

        assert fired == [True]
        assert during["as_of_seq"] == head_before
        assert _statuses(during) == {
            SNAPSHOT_OLDER_RUN_ID: "started",
            SNAPSHOT_RUN_ID: "started",
        }
        assert [entry["lease"] for entry in during["runs"]] == [None, None]
        after = cli.runs_for(repo_dir=projection)
        leases = {entry["id"]: entry["lease"] for entry in after["runs"]}
        assert leases[SNAPSHOT_RUN_ID] is not None
        assert after["as_of_seq"] > during["as_of_seq"]
    finally:
        writer.close()

PAGED_RUN_IDS = (
    "20260923T090000Z-cccccccc",
    "20260923T090000Z-bbbbbbbb",
    "20260923T090000Z-aaaaaaaa",
    "20260921T090000Z-cbe34d00",
)
"""The listing order of `_record_paged_runs`: three runs share one
`started_at`, so only the id orders them."""


def _record_paged_runs(root: Path) -> None:
    for run_id in PAGED_RUN_IDS[:3]:
        _record(root, run_id, started_at=RECORDED_AT)
    _record(
        root, PAGED_RUN_IDS[3], started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    )


def _listed_ids(argv: list[str]) -> list[str]:
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    return [entry["id"] for entry in envelope["data"]["runs"]]


def test_runs_pages_by_last_run_id_through_equal_start_times(projection):
    _record_paged_runs(projection)
    base = ["runs", "--repo-dir", str(projection)]

    full = _listed_ids(base)
    first = _listed_ids([*base, "--limit", "2"])
    second = _listed_ids([*base, "--limit", "2", "--before", first[-1]])
    third = _listed_ids([*base, "--limit", "2", "--before", second[-1]])

    assert full == list(PAGED_RUN_IDS)
    assert first == list(PAGED_RUN_IDS[:2])
    assert first + second == full
    assert third == []


def test_runs_limit_beyond_the_listing_returns_it_all_and_the_next_page_is_empty(
    projection,
):
    """Review Focus 4."""
    _record_paged_runs(projection)
    base = ["runs", "--repo-dir", str(projection)]

    page = _listed_ids([*base, "--limit", "50"])

    assert page == list(PAGED_RUN_IDS)
    assert _listed_ids([*base, "--limit", "50", "--before", page[-1]]) == []


@pytest.mark.parametrize("pretty", [[], ["--pretty"]])
def test_runs_before_without_limit_is_refused_before_the_database_is_opened(
    projection, monkeypatch, pretty
):
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)

    def refuse(root):
        raise AssertionError("the database was opened")

    monkeypatch.setattr(cli.store_db, "open_db_for_reading", refuse)

    result = runner.invoke(
        cli.app,
        ["runs", "--repo-dir", str(projection), "--before", SNAPSHOT_RUN_ID, *pretty],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {"type": "CliError", "message": "--before requires --limit"},
    }


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_runs_limit_below_one_is_refused(projection, monkeypatch, limit):
    def refuse(root):
        raise AssertionError("the database was opened")

    monkeypatch.setattr(cli.store_db, "open_db_for_reading", refuse)

    result = runner.invoke(
        cli.app, ["runs", "--repo-dir", str(projection), "--limit", limit]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout)["error"] == {
        "type": "CliError",
        "message": f"--limit must be at least 1, got {limit}",
    }


@pytest.mark.parametrize("before", ["no-such-thing", ""])
def test_runs_before_neither_a_run_id_nor_a_timestamp_is_an_unknown_run(
    projection, before
):
    """Review Focus 2 is the empty string."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)

    result = runner.invoke(
        cli.app,
        ["runs", "--repo-dir", str(projection), "--limit", "5", "--before", before],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout)["error"] == {
        "type": "UnknownRunError",
        "message": f"--before {before!r} is neither a run id nor an ISO 8601 timestamp"
        " (pass the last run id of the previous page)",
    }


@pytest.mark.parametrize(
    "before",
    ["2026-09-22T09:00:00Z", "2026-09-22T11:00:00+02:00", "2026-09-22T09:00:00", "2026-09-22"],
    ids=["z", "offset", "naive", "date"],
)
def test_runs_before_a_timestamp_lists_runs_started_strictly_before_it(
    projection, before
):
    """Review Focus 3: every spelling is one instant (the date is midnight UTC)."""
    _record(projection, "20260921T090000Z-cbe34d00", started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record(projection, "20260922T090000Z-cbe34d00", started_at=datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc))
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)

    assert _listed_ids(
        ["runs", "--repo-dir", str(projection), "--limit", "5", "--before", before]
    ) == ["20260921T090000Z-cbe34d00"]


def test_runs_reads_the_before_cursor_inside_the_snapshot(projection, monkeypatch):
    """The cursor run's `started_at` moves after `head` returned: only a
    `run_cursor` outside the snapshot could see the new value, and it would
    then list nothing."""
    _record_two_started_runs(projection)
    writer = store_writer.Store.open(projection, SNAPSHOT_RUN_ID)
    try:
        head_before = _events_head(projection)

        def move_the_cursor_run() -> None:
            run = writer.load_run(SNAPSHOT_RUN_ID)
            writer.record_run(
                run.model_copy(
                    update={"started_at": datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)}
                )
            )

        fired = _write_once_after(
            monkeypatch, cli.store_events, "head", move_the_cursor_run
        )
        during = cli.runs_for(repo_dir=projection, limit=5, before=SNAPSHOT_RUN_ID)

        assert fired == [True]
        assert during["as_of_seq"] == head_before
        assert [entry["id"] for entry in during["runs"]] == [SNAPSHOT_OLDER_RUN_ID]
        after = cli.runs_for(repo_dir=projection, limit=5, before=SNAPSHOT_RUN_ID)
        assert after["runs"] == []
        assert after["as_of_seq"] > during["as_of_seq"]
    finally:
        writer.close()


def test_runs_before_a_run_on_a_repo_with_no_project_is_unknown(projection, tmp_path):
    """Review Focus 5: `stranger` has no `projects` row, so every run is foreign to it."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    stranger = tmp_path / "stranger"
    stranger.mkdir()

    result = runner.invoke(
        cli.app,
        ["runs", "--repo-dir", str(stranger), "--limit", "5", "--before", SNAPSHOT_RUN_ID],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout)["error"] == {
        "type": "UnknownRunError",
        "message": f"run {SNAPSHOT_RUN_ID!r} is not in the projection for {stranger.resolve()}"
        " (`agent-manager runs` lists the ones that are)",
    }


def test_runs_store_id_is_the_meta_store_id(projection):
    _record_two_started_runs(projection)
    expected = _store_id(projection)

    payload = cli.runs_for(repo_dir=projection)

    assert isinstance(payload["store_id"], str)
    assert _HEX_ID.fullmatch(payload["store_id"])
    assert payload["store_id"] == expected
    argv = ["runs", "--repo-dir", str(projection)]
    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])
    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for result in (plain, pretty):
        assert json.loads(result.stdout)["data"]["store_id"] == expected


def test_runs_store_id_is_stable(projection):
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)

    first = cli.runs_for(repo_dir=projection)
    second = cli.runs_for(repo_dir=projection)
    _record(
        projection,
        "20260930T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
    )
    third = cli.runs_for(repo_dir=projection)

    assert first["store_id"] == second["store_id"] == third["store_id"]
    assert _HEX_ID.fullmatch(first["store_id"])
    assert third["as_of_seq"] > first["as_of_seq"]


def test_runs_store_id_differs_across_databases(projection, tmp_path, monkeypatch):
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    first = cli.runs_for(repo_dir=projection)["store_id"]

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "other-xdg"))
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    second = cli.runs_for(repo_dir=projection)["store_id"]

    assert _HEX_ID.fullmatch(first)
    assert _HEX_ID.fullmatch(second)
    assert first != second


def test_runs_reads_store_id_inside_the_snapshot(projection, monkeypatch):
    """Review Focus 2: on the command's own connection, inside `read_snapshot`."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    opened: list[sqlite3.Connection] = []
    seen: list[tuple[sqlite3.Connection, bool]] = []
    original_open = cli.store_db.open_db_for_reading
    original_store_id = cli.store_db.store_id

    def capture_open(root):
        conn = original_open(root)
        opened.append(conn)
        return conn

    def capture_store_id(conn):
        seen.append((conn, conn.in_transaction))
        return original_store_id(conn)

    monkeypatch.setattr(cli.store_db, "open_db_for_reading", capture_open)
    monkeypatch.setattr(cli.store_db, "store_id", capture_store_id)

    payload = cli.runs_for(repo_dir=projection)

    assert len(opened) == 1
    assert seen == [(opened[0], True)]
    assert _HEX_ID.fullmatch(payload["store_id"])


def test_runs_store_id_on_an_empty_listing(projection):
    """No `am.db`: `null`, and nothing created. Then another repo's runs make
    the database exist: this repo's listing is still empty, but it carries
    that database's id, since `store_id` is machine-wide like `as_of_seq`."""
    assert cli.runs_for(repo_dir=projection) == {
        "runs": [],
        "as_of_seq": 0,
        "store_id": None,
    }
    assert not paths.db_path().exists()

    elsewhere = projection.parent / "elsewhere"
    elsewhere.mkdir()
    _record(elsewhere, SNAPSHOT_RUN_ID, started_at=RECORDED_AT)
    head = _events_head(elsewhere)
    expected = _store_id(elsewhere)

    payload = cli.runs_for(repo_dir=projection)

    assert payload["runs"] == []
    assert payload["as_of_seq"] == head > 0
    assert expected is not None
    assert payload["store_id"] == expected


RUNS_NEWER_RUN_ID = "20260930T090000Z-cbe34d00"
"""A second run, started after `CONTROL_RUN_ID`'s `RECORDED_AT`, so it lists first."""


@pytest.mark.parametrize(
    "heartbeat_offset, pid, host, accepting",
    [
        (0, None, None, True),
        (-30, None, None, True),
        (-5, 0, "elsewhere.invalid", True),
        (-5, None, None, False),
    ],
    ids=["fresh-here", "boundary-30s", "other-host-unprobed-pid", "not-accepting"],
)
def test_runs_shows_a_live_lease(
    projection, monkeypatch, heartbeat_offset, pid, host, accepting
):
    """Spec test 1, plus Review Focus: the 30s boundary is inclusive, another
    host's pid is never probed here, and a closed control window still reads
    live. `pid`/`host` `None` mean `_plant_lease`'s default: this process,
    this host."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(
        projection,
        pid=pid,
        host=host,
        heartbeat_at=_at(heartbeat_offset),
        accepting=accepting,
    )

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["lease"] == {
        "live": True,
        "pid": os.getpid() if pid is None else pid,
        "host": HERE if host is None else host,
        "heartbeat_at": _at(heartbeat_offset).isoformat(),
        "accepting": accepting,
    }


@pytest.mark.parametrize(
    "heartbeat_offset, pid",
    [
        (-31, None),
        (-5, 0),
    ],
    ids=["stale-heartbeat", "dead-pid-here"],
)
def test_runs_shows_a_dead_lease_with_its_fields_still_filled(
    projection, monkeypatch, heartbeat_offset, pid
):
    """Spec test 2. Pid 0 is never alive (`control.pid_alive`), so no process
    has to be spawned and reaped to get a dead pid on this host."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, pid=pid, heartbeat_at=_at(heartbeat_offset))

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert entry["lease"] == {
        "live": False,
        "pid": os.getpid() if pid is None else pid,
        "host": HERE,
        "heartbeat_at": _at(heartbeat_offset).isoformat(),
        "accepting": True,
    }


def test_runs_shows_a_null_lease_for_a_run_with_no_lease_row(projection, monkeypatch):
    """Spec test 3: no row is `null`, not an error and not a missing key."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    assert '"lease":null' in result.stdout
    [entry] = json.loads(result.stdout)["data"]["runs"]
    assert "lease" in entry
    assert entry["lease"] is None


@pytest.mark.parametrize("heartbeat_offset", [-5, -31], ids=["live", "stale"])
def test_runs_lease_is_status_control_lease_without_acquired_at(
    projection, monkeypatch, heartbeat_offset
):
    """Spec test 4: one computation, two commands, no drift."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection, heartbeat_at=_at(heartbeat_offset))

    listed = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])
    reported = runner.invoke(
        cli.app, ["status", CONTROL_RUN_ID, "--repo-dir", str(projection)]
    )

    assert listed.exit_code == 0, listed.output
    assert reported.exit_code == 0, reported.output
    [entry] = json.loads(listed.stdout)["data"]["runs"]
    status_lease = json.loads(reported.stdout)["data"]["control"]["lease"]
    assert "acquired_at" in status_lease
    assert entry["lease"] == {
        key: value for key, value in status_lease.items() if key != "acquired_at"
    }


def test_runs_lease_has_exactly_the_five_keys_plain_and_pretty(projection, monkeypatch):
    """Spec test 5: the shape pin, beside `RUNS_ENTRY_KEYS`'s own."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection)
    argv = ["runs", "--repo-dir", str(projection)]

    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])

    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for envelope in (json.loads(plain.stdout), json.loads(pretty.stdout)):
        [entry] = envelope["data"]["runs"]
        assert set(entry) == RUNS_ENTRY_KEYS
        assert set(entry["lease"]) == RUNS_LEASE_KEYS
        assert isinstance(entry["lease"]["live"], bool)
        assert isinstance(entry["lease"]["pid"], int)
        assert isinstance(entry["lease"]["host"], str)
        assert isinstance(entry["lease"]["heartbeat_at"], str)
        assert isinstance(entry["lease"]["accepting"], bool)


def test_runs_attaches_each_runs_own_lease_and_reads_the_clock_once(
    projection, monkeypatch
):
    """Review Focus: a run without a lease row never inherits its neighbour's,
    and the whole listing is judged against one instant."""
    calls: list[datetime] = []

    def counting_now() -> datetime:
        calls.append(CONTROL_NOW)
        return CONTROL_NOW

    monkeypatch.setattr(cli, "_utcnow", counting_now)
    _plant_run(projection)
    _record(
        projection,
        RUNS_NEWER_RUN_ID,
        started_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
        with_phases=False,
    )
    _plant_lease(projection, run_id=CONTROL_RUN_ID, heartbeat_at=_at(-31))

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    entries = json.loads(result.stdout)["data"]["runs"]
    assert [entry["id"] for entry in entries] == [RUNS_NEWER_RUN_ID, CONTROL_RUN_ID]
    assert entries[0]["lease"] is None
    assert entries[1]["lease"] is not None
    assert entries[1]["lease"]["live"] is False
    assert entries[1]["lease"]["heartbeat_at"] == _at(-31).isoformat()
    assert len(calls) == 1


RUNS_PROGRESS_KEYS = {"stories", "subtasks", "current"}
RUNS_PROGRESS_COUNT_KEYS = {"done", "total"}
RUNS_PROGRESS_CURRENT_KEYS = {"card", "phase", "attempt"}
"""`data.runs[].progress` (card 882b212b), its two counts, and a non-null `current`."""


def _record_started_phase(root: Path, run_id: str, *, attempts: int) -> None:
    """`_record`'s `card-1` given a third phase, `implement`, still `started`,
    with `attempts` attempt rows numbered from 1."""
    opened = store_writer.Store.open(root, run_id)
    try:
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(
                name="implement", kind="agent", status="started", started_at=RECORDED_AT
            ),
        )
        for n in range(1, attempts + 1):
            opened.record_attempt(
                "story-1",
                "card-1",
                "implement",
                models.Attempt(n=n, dispatch=_recorded_dispatch(run_id)),
            )
    finally:
        opened.close()


def _record_bare_run(root: Path, run_id: str, *, started_at: datetime) -> None:
    """A run row with nothing below it: a run that never got past starting."""
    opened = store_writer.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=started_at,
            )
        )
    finally:
        opened.close()


def test_runs_progress_has_exactly_its_keys_plain_and_pretty(projection):
    """The shape pin for `progress`, beside `RUNS_ENTRY_KEYS`'s own: one run
    with a `current`, one without."""
    in_flight = "20260923T090000Z-cbe34d00"
    _record(projection, in_flight, started_at=RECORDED_AT, status="started")
    _record_started_phase(projection, in_flight, attempts=1)
    _record(
        projection,
        "20260921T090000Z-cbe34d00",
        started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
    )
    argv = ["runs", "--repo-dir", str(projection)]

    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])

    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    for envelope in (json.loads(plain.stdout), json.loads(pretty.stdout)):
        entries = envelope["data"]["runs"]
        assert [entry["progress"]["current"] is None for entry in entries] == [False, True]
        for entry in entries:
            assert set(entry) == RUNS_ENTRY_KEYS
            progress = entry["progress"]
            assert set(progress) == RUNS_PROGRESS_KEYS
            for level in ("stories", "subtasks"):
                assert set(progress[level]) == RUNS_PROGRESS_COUNT_KEYS
                assert all(type(progress[level][key]) is int for key in RUNS_PROGRESS_COUNT_KEYS)
            if progress["current"] is not None:
                assert set(progress["current"]) == RUNS_PROGRESS_CURRENT_KEYS
                assert isinstance(progress["current"]["card"], str)
                assert isinstance(progress["current"]["phase"], str)
                assert type(progress["current"]["attempt"]) is int


def test_runs_shows_a_fixture_runs_progress(projection):
    """Exact values through the CLI: a run in flight, a finished one, and one
    with no tree rows at all, which is the zero shape and never `null`."""
    in_flight = "20260923T090000Z-cbe34d00"
    finished = "20260922T090000Z-cbe34d00"
    bare = "20260921T090000Z-cbe34d00"
    _record(projection, in_flight, started_at=RECORDED_AT, status="started")
    _record_started_phase(projection, in_flight, attempts=2)
    _record(
        projection,
        finished,
        started_at=datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc),
    )
    _record_bare_run(
        projection, bare, started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    )

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    entries = json.loads(result.stdout)["data"]["runs"]
    assert [entry["id"] for entry in entries] == [in_flight, finished, bare]
    assert {entry["id"]: entry["progress"] for entry in entries} == {
        in_flight: {
            "stories": {"done": 0, "total": 1},
            "subtasks": {"done": 0, "total": 1},
            "current": {"card": "card-1", "phase": "implement", "attempt": 2},
        },
        finished: {
            "stories": {"done": 1, "total": 1},
            "subtasks": {"done": 1, "total": 1},
            "current": None,
        },
        bare: {
            "stories": {"done": 0, "total": 0},
            "subtasks": {"done": 0, "total": 0},
            "current": None,
        },
    }


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
    be refused before the store records it as a project."""
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


EVENTS_RUN_A = "20261007T090000Z-aaaaaaaa"
EVENTS_RUN_B = "20261007T090000Z-bbbbbbbb"
EVENT_TS = "2026-10-07T12:00:00+00:00"
"""Stored with `+00:00`; a line renders it the `JournalLine` way, with `Z`."""

EVENT_LINE_KEYS = {
    "seq",
    "ts",
    "run_id",
    "event",
    "story",
    "card",
    "phase",
    "attempt",
    "payload",
    "gseq",
}

ALL_EVENT_KINDS = (
    "run_upsert",
    "story_upsert",
    "subtask_upsert",
    "phase_upsert",
    "attempt_upsert",
    "control_requested",
    "control_handled",
    "lease_acquired",
    "lease_taken_over",
    "claim_conflict",
)

EVENTS_UNKNOWN_MESSAGE = (
    "run 'no-such-run' is not in the projection"
    " (`agent-manager runs --all-projects` lists the ones that are)"
)


def _insert_events(root: Path, *specs: tuple[str, str]) -> list[store_events.EventRow]:
    """One `events` row per `(run_id, kind)`, in order, with payload `{"n": i}`,
    committed together on a connection of its own; the rows as stored."""
    conn = store_db.open_db(root)
    try:
        project_id = store_projects.resolve(conn, root, now=RECORDED_AT)
        rows = [
            store_events.insert(
                conn,
                project_id=project_id,
                run_id=run_id,
                ts=EVENT_TS,
                kind=kind,
                payload={"n": index},
                source="live",
            )
            for index, (run_id, kind) in enumerate(specs)
        ]
        conn.commit()
        return rows
    finally:
        conn.close()


def _events(argv: list[str]) -> dict[str, Any]:
    """`am events ARGV` through the CLI: exit 0, an ok envelope; its data."""
    result = runner.invoke(cli.app, ["events", *argv])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    return envelope["data"]


def _events_refusal(argv: list[str]) -> dict[str, Any]:
    """`am events ARGV` through the CLI: exit 3, an error envelope; its error."""
    result = runner.invoke(cli.app, ["events", *argv])
    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    return envelope["error"]


def test_events_returns_the_runs_lines_in_gseq_order_with_head(projection):
    """Review Focus 1: B's rows interleave A's by `seq`, and B writes last."""
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_B, "run_upsert"),
        (EVENTS_RUN_A, "story_upsert"),
        (EVENTS_RUN_B, "story_upsert"),
        (EVENTS_RUN_A, "lease_acquired"),
        (EVENTS_RUN_B, "lease_acquired"),
    )
    a_rows = [row for row in rows if row.run_id == EVENTS_RUN_A]

    data = _events([EVENTS_RUN_A])

    assert set(data) == {"events", "head"}
    lines = data["events"]
    assert [line["gseq"] for line in lines] == [row.seq for row in a_rows]
    assert [line["seq"] for line in lines] == [1, 2, 3]
    assert [line["event"] for line in lines] == [
        "run_upsert",
        "story_upsert",
        "lease_acquired",
    ]
    assert {line["run_id"] for line in lines} == {EVENTS_RUN_A}
    for line in lines:
        assert set(line) == EVENT_LINE_KEYS
    assert data["head"] == rows[-1].seq == _events_head(projection)
    assert data["head"] > lines[-1]["gseq"]


def test_events_line_is_the_journal_line_plus_gseq(projection):
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_A, "control_requested"),
    )

    data = cli.events_for(EVENTS_RUN_A)

    assert [
        {key: value for key, value in line.items() if key != "gseq"}
        for line in data["events"]
    ] == [store_events.journal_line(row).model_dump(mode="json") for row in rows]
    assert [line["gseq"] for line in data["events"]] == [row.seq for row in rows]
    assert rows[0].ts == EVENT_TS
    assert [line["ts"] for line in data["events"]] == ["2026-10-07T12:00:00Z"] * 2
    assert [line["payload"] for line in data["events"]] == [{"n": 0}, {"n": 1}]


def test_events_includes_every_event_kind(projection):
    assert set(ALL_EVENT_KINDS) == set(get_args(store_journal.EventKind))
    _insert_events(projection, *[(EVENTS_RUN_A, kind) for kind in ALL_EVENT_KINDS])

    data = _events([EVENTS_RUN_A])

    assert [line["event"] for line in data["events"]] == list(ALL_EVENT_KINDS)


def test_events_after_seq_and_limit_page_forward_without_gap_or_repeat(projection):
    _insert_events(
        projection,
        *[(run, "phase_upsert") for _ in range(3) for run in (EVENTS_RUN_A, EVENTS_RUN_B)],
    )
    full = _events([EVENTS_RUN_A])
    pages: list[list[dict[str, Any]]] = []
    after = 0

    for _ in range(10):
        page = _events([EVENTS_RUN_A, "--limit", "2", "--after-seq", str(after)])
        assert page["head"] == full["head"]
        if not page["events"]:
            break
        pages.append(page["events"])
        after = page["events"][-1]["gseq"]
    else:
        pytest.fail("paging never reached an empty page")

    assert len(full["events"]) == 3
    assert [len(page) for page in pages] == [2, 1]
    assert [line for page in pages for line in page] == full["events"]


@pytest.mark.parametrize("beyond", [0, 100])
def test_events_after_seq_beyond_head_is_an_empty_page(projection, beyond):
    """Review Focus 4."""
    _insert_events(
        projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert")
    )
    head = _events_head(projection)

    data = _events([EVENTS_RUN_A, "--after-seq", str(head + beyond)])

    assert data == {"events": [], "head": head}


def test_events_of_a_known_run_without_events_is_an_empty_page(projection):
    """`events` is append-only, so the run's rows are removed behind its
    trigger's back; the `runs` row stays."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT, with_phases=False)
    _insert_events(projection, (EVENTS_RUN_B, "run_upsert"))
    conn = store_db.open_db(projection)
    try:
        conn.execute("DROP TRIGGER events_no_delete")
        conn.execute("DELETE FROM events WHERE run_id = ?", (SNAPSHOT_RUN_ID,))
        conn.commit()
        assert store_events.has_run(conn, SNAPSHOT_RUN_ID) is False
        assert store_queries.load_run(conn, SNAPSHOT_RUN_ID) is not None
    finally:
        conn.close()

    data = _events([SNAPSHOT_RUN_ID])

    assert data == {"events": [], "head": _events_head(projection)}
    assert data["head"] > 0


def test_events_of_a_run_known_only_by_events_is_listed(projection):
    """Review Focus 3: a lease event can precede the `run_upsert`."""
    rows = _insert_events(projection, (EVENTS_RUN_A, "lease_acquired"))

    data = _events([EVENTS_RUN_A])

    assert [(line["event"], line["gseq"]) for line in data["events"]] == [
        ("lease_acquired", rows[0].seq)
    ]


def test_events_unknown_run_refuses(projection):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))

    assert _events_refusal(["no-such-run"]) == {
        "type": "UnknownRunError",
        "message": EVENTS_UNKNOWN_MESSAGE,
    }


def _refuse_to_open_the_db(monkeypatch) -> None:
    def refuse(root):
        raise AssertionError("the database was opened")

    monkeypatch.setattr(cli.store_db, "open_db_for_reading", refuse)


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_events_rejects_limit_below_one_before_opening_the_db(
    projection, monkeypatch, limit
):
    _refuse_to_open_the_db(monkeypatch)

    assert _events_refusal([EVENTS_RUN_A, "--limit", limit]) == {
        "type": "CliError",
        "message": f"--limit must be at least 1, got {limit}",
    }


@pytest.mark.parametrize("after_seq", ["-1", "-100"])
def test_events_rejects_negative_after_seq_before_opening_the_db(
    projection, monkeypatch, after_seq
):
    _refuse_to_open_the_db(monkeypatch)

    assert _events_refusal([EVENTS_RUN_A, "--after-seq", after_seq]) == {
        "type": "CliError",
        "message": f"--after-seq must be 0 or more, got {after_seq}",
    }


def _open_event_writer(root: Path) -> tuple[sqlite3.Connection, int]:
    """A writing connection on the projection, held open across the read so the
    database stays in WAL with its sidecars, plus `root`'s project id."""
    writer = store_db.open_db(root)
    project_id = store_projects.resolve(writer, root, now=RECORDED_AT)
    writer.commit()
    return writer, project_id


def test_events_never_shows_an_event_after_head(projection, monkeypatch):
    """Review Focus 2: an event of the run commits right after `head` returns."""
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))
    head_before = _events_head(projection)
    writer, project_id = _open_event_writer(projection)
    try:

        def write() -> None:
            store_events.insert(
                writer,
                project_id=project_id,
                run_id=EVENTS_RUN_A,
                ts=EVENT_TS,
                kind="story_upsert",
                payload={},
                source="live",
            )
            writer.commit()

        fired = _write_once_after(monkeypatch, cli.store_events, "head", write)
        during = cli.events_for(EVENTS_RUN_A)

        assert fired == [True]
        assert during["head"] == head_before
        assert [line["event"] for line in during["events"]] == ["run_upsert"]
        assert all(line["gseq"] <= during["head"] for line in during["events"])
        after = cli.events_for(EVENTS_RUN_A)
        assert after["head"] > head_before
        assert [line["event"] for line in after["events"]] == [
            "run_upsert",
            "story_upsert",
        ]
    finally:
        writer.close()


def test_events_unknown_run_check_is_inside_the_snapshot(projection, monkeypatch):
    """The run's first event commits right after `head` returns: the in-flight
    read still refuses, and the next one lists it."""
    _insert_events(projection, (EVENTS_RUN_B, "run_upsert"))
    writer, project_id = _open_event_writer(projection)
    try:

        def write() -> None:
            store_events.insert(
                writer,
                project_id=project_id,
                run_id=EVENTS_RUN_A,
                ts=EVENT_TS,
                kind="lease_acquired",
                payload={},
                source="live",
            )
            writer.commit()

        fired = _write_once_after(monkeypatch, cli.store_events, "head", write)
        with pytest.raises(cli.UnknownRunError) as caught:
            cli.events_for(EVENTS_RUN_A)

        assert fired == [True]
        assert str(caught.value) == (
            f"run {EVENTS_RUN_A!r} is not in the projection"
            " (`agent-manager runs --all-projects` lists the ones that are)"
        )
        after = cli.events_for(EVENTS_RUN_A)
        assert [line["event"] for line in after["events"]] == ["lease_acquired"]
    finally:
        writer.close()


def test_events_ignores_the_working_directory(projection, tmp_path, monkeypatch):
    _insert_events(
        projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "lease_acquired")
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.chdir(projection)
    here = runner.invoke(cli.app, ["events", EVENTS_RUN_A])
    monkeypatch.chdir(elsewhere)
    there = runner.invoke(cli.app, ["events", EVENTS_RUN_A])
    scoped = runner.invoke(
        cli.app, ["events", EVENTS_RUN_A, "--repo-dir", str(projection)]
    )

    assert here.exit_code == 0, here.output
    assert there.exit_code == 0, there.output
    assert here.stdout == there.stdout
    assert len(json.loads(there.stdout)["data"]["events"]) == 2
    assert scoped.exit_code == 2


def test_events_pretty_indents_the_same_envelope(projection):
    _insert_events(
        projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert")
    )

    plain = runner.invoke(cli.app, ["events", EVENTS_RUN_A])
    pretty = runner.invoke(cli.app, ["events", EVENTS_RUN_A, "--pretty"])

    assert plain.exit_code == 0, plain.output
    assert pretty.exit_code == 0, pretty.output
    assert "\n" not in plain.stdout.strip()
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


# ── am events --tail / --before-seq (card 5d2663f5) ──────────────────────────


def _interleaved_a_and_b(root: Path, a_count: int) -> list[store_events.EventRow]:
    """`a_count` rounds of one A `phase_upsert` then one B `phase_upsert`, so
    B's row is always the newest; the rows as stored."""
    return _insert_events(
        root,
        *[(run, "phase_upsert") for _ in range(a_count) for run in (EVENTS_RUN_A, EVENTS_RUN_B)],
    )


def _gseqs(data: dict[str, Any]) -> list[int]:
    return [line["gseq"] for line in data["events"]]


def test_events_tail_is_the_runs_last_lines_ascending(projection):
    """C1, Review Focus 2 and 3: B's rows interleave A's and B writes last."""
    rows = _interleaved_a_and_b(projection, 4)
    a_rows = [row for row in rows if row.run_id == EVENTS_RUN_A]

    data = _events([EVENTS_RUN_A, "--tail", "2"])

    assert set(data) == {"events", "head"}
    assert _gseqs(data) == [a_rows[2].seq, a_rows[3].seq]
    assert [line["seq"] for line in data["events"]] == [3, 4]
    assert {line["run_id"] for line in data["events"]} == {EVENTS_RUN_A}
    for line in data["events"]:
        assert set(line) == EVENT_LINE_KEYS
    assert data["head"] == rows[-1].seq == _events_head(projection)


def test_events_tail_larger_than_the_run_is_all_of_it_and_one_is_the_last(projection):
    """C2."""
    rows = _interleaved_a_and_b(projection, 3)
    a_seqs = [row.seq for row in rows if row.run_id == EVENTS_RUN_A]

    assert _gseqs(_events([EVENTS_RUN_A, "--tail", "10"])) == a_seqs
    assert _gseqs(_events([EVENTS_RUN_A, "--tail", "1"])) == a_seqs[-1:]


def test_events_before_seq_is_every_earlier_line_strictly(projection):
    """C3, Review Focus 1: the event at B itself is absent."""
    rows = _interleaved_a_and_b(projection, 4)
    a_rows = [row for row in rows if row.run_id == EVENTS_RUN_A]

    data = _events([EVENTS_RUN_A, "--before-seq", str(a_rows[2].seq)])

    assert _gseqs(data) == [a_rows[0].seq, a_rows[1].seq]
    assert data["head"] == rows[-1].seq


def test_events_before_seq_with_limit_is_the_nearest_k_before_it(projection):
    """C4, and Review Focus (plan) 2: fewer than K before B is all of them."""
    rows = _interleaved_a_and_b(projection, 5)
    a_rows = [row for row in rows if row.run_id == EVENTS_RUN_A]

    near = _events([EVENTS_RUN_A, "--before-seq", str(a_rows[4].seq), "--limit", "2"])
    short = _events([EVENTS_RUN_A, "--before-seq", str(a_rows[1].seq), "--limit", "3"])

    assert _gseqs(near) == [a_rows[2].seq, a_rows[3].seq]
    assert _gseqs(short) == [a_rows[0].seq]


def test_events_before_seq_at_another_runs_gseq_is_a_boundary_like_any_other(projection):
    """Review Focus (plan) 1: B names a row of run B, not of run A."""
    rows = _interleaved_a_and_b(projection, 3)
    b_rows = [row for row in rows if row.run_id == EVENTS_RUN_B]
    a_seqs = [row.seq for row in rows if row.run_id == EVENTS_RUN_A]

    data = _events([EVENTS_RUN_A, "--before-seq", str(b_rows[1].seq)])

    assert _gseqs(data) == [seq for seq in a_seqs if seq < b_rows[1].seq] == a_seqs[:2]
    assert {line["run_id"] for line in data["events"]} == {EVENTS_RUN_A}


def test_events_tail_then_before_seq_pages_backwards_without_gap_or_repeat(projection):
    """C5, Review Focus 1: each page's first gseq is the next --before-seq."""
    _interleaved_a_and_b(projection, 5)
    full = _events([EVENTS_RUN_A])
    pages = [_events([EVENTS_RUN_A, "--tail", "2"])]

    for _ in range(10):
        page = _events(
            [EVENTS_RUN_A, "--before-seq", str(pages[-1]["events"][0]["gseq"]), "--limit", "2"]
        )
        assert page["head"] == full["head"]
        if not page["events"]:
            assert page == {"events": [], "head": full["head"]}
            break
        pages.append(page)
    else:
        pytest.fail("paging never reached an empty page")

    assert [len(page["events"]) for page in pages] == [2, 2, 1]
    walked = [line for page in reversed(pages) for line in page["events"]]
    assert walked == full["events"]
    assert len({line["gseq"] for line in walked}) == len(walked) == 5


def test_events_before_seq_one_is_empty_and_above_head_is_everything(projection):
    """C6, and Review Focus (plan) 3: above head with --limit K is --tail K."""
    _interleaved_a_and_b(projection, 3)
    full = _events([EVENTS_RUN_A])
    head = full["head"]

    assert _events([EVENTS_RUN_A, "--before-seq", "1"]) == {"events": [], "head": head}
    assert _events([EVENTS_RUN_A, "--before-seq", str(head + 100)]) == full
    assert (
        _events([EVENTS_RUN_A, "--before-seq", str(head + 1), "--limit", "2"])
        == _events([EVENTS_RUN_A, "--tail", "2"])
    )


@pytest.mark.parametrize(
    "flags", [["--tail", "2"], ["--before-seq", "5"], ["--before-seq", "5", "--limit", "1"]]
)
def test_events_tail_and_before_seq_of_an_unknown_run_refuse(projection, flags):
    """C7."""
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))

    assert _events_refusal(["no-such-run", *flags]) == {
        "type": "UnknownRunError",
        "message": EVENTS_UNKNOWN_MESSAGE,
    }


def test_events_tail_and_before_seq_of_a_run_without_events_are_empty_pages(projection):
    """C8, and Review Focus (plan) 4: a `runs` row with its events removed
    behind the append-only trigger's back."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT, with_phases=False)
    _insert_events(projection, (EVENTS_RUN_B, "run_upsert"))
    conn = store_db.open_db(projection)
    try:
        conn.execute("DROP TRIGGER events_no_delete")
        conn.execute("DELETE FROM events WHERE run_id = ?", (SNAPSHOT_RUN_ID,))
        conn.commit()
    finally:
        conn.close()
    head = _events_head(projection)

    assert _events([SNAPSHOT_RUN_ID, "--tail", "3"]) == {"events": [], "head": head}
    assert _events([SNAPSHOT_RUN_ID, "--before-seq", str(head + 1)]) == {
        "events": [],
        "head": head,
    }


def test_events_tail_never_shows_an_event_after_head(projection, monkeypatch):
    """C12, Review Focus 5: an event of the run commits right after `head`
    returns; the --tail page is the snapshot's, not the newest."""
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))
    head_before = _events_head(projection)
    writer, project_id = _open_event_writer(projection)
    try:

        def write() -> None:
            store_events.insert(
                writer,
                project_id=project_id,
                run_id=EVENTS_RUN_A,
                ts=EVENT_TS,
                kind="story_upsert",
                payload={},
                source="live",
            )
            writer.commit()

        fired = _write_once_after(monkeypatch, cli.store_events, "head", write)
        during = cli.events_for(EVENTS_RUN_A, tail=5)

        assert fired == [True]
        assert during["head"] == head_before
        assert [line["event"] for line in during["events"]] == ["run_upsert"]
        assert all(line["gseq"] <= during["head"] for line in during["events"])
        after = cli.events_for(EVENTS_RUN_A, tail=5)
        assert after["head"] > head_before
        assert [line["event"] for line in after["events"]] == [
            "run_upsert",
            "story_upsert",
        ]
    finally:
        writer.close()


def test_events_tail_and_before_seq_help_texts():
    parameters = inspect.signature(cli.events).parameters

    assert parameters["tail"].default.help == (
        "List the run's last N events (at least 1). Not with --after-seq,"
        " --before-seq or --limit."
    )
    assert parameters["before_seq"].default.help == (
        "List events with a gseq below this (at least 1), the nearest --limit of"
        " them. To page backwards, pass the first gseq of the previous page. Not"
        " with --after-seq or --tail."
    )
    assert parameters["after_seq"].default.default is None
    assert "--before-seq" in cli.events.__doc__
    assert "--tail N" in cli.events.__doc__


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--tail", "0", "--tail must be at least 1, got 0"),
        ("--tail", "-1", "--tail must be at least 1, got -1"),
        ("--before-seq", "0", "--before-seq must be at least 1, got 0"),
        ("--before-seq", "-5", "--before-seq must be at least 1, got -5"),
    ],
)
def test_events_rejects_tail_and_before_seq_below_one_before_opening_the_db(
    projection, monkeypatch, flag, value, message
):
    """C9."""
    _refuse_to_open_the_db(monkeypatch)

    assert _events_refusal([EVENTS_RUN_A, flag, value]) == {
        "type": "CliError",
        "message": message,
    }


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--tail", "5", "--after-seq", "3"], "--tail cannot be combined with --after-seq"),
        (["--tail", "5", "--after-seq", "0"], "--tail cannot be combined with --after-seq"),
        (["--tail", "5", "--before-seq", "9"], "--tail cannot be combined with --before-seq"),
        (["--tail", "5", "--limit", "2"], "--tail cannot be combined with --limit"),
        (
            ["--before-seq", "9", "--after-seq", "3"],
            "--before-seq cannot be combined with --after-seq",
        ),
        (
            ["--before-seq", "9", "--after-seq", "0"],
            "--before-seq cannot be combined with --after-seq",
        ),
        (
            ["--tail", "5", "--before-seq", "9", "--after-seq", "3", "--limit", "2"],
            "--tail cannot be combined with --after-seq",
        ),
    ],
)
def test_events_rejects_conflicting_paging_flags_before_opening_the_db(
    projection, monkeypatch, flags, message
):
    """C10, Review Focus 4 (an explicit default `--after-seq 0` is still
    given), and Review Focus (plan) 5 for `--before-seq`."""
    _refuse_to_open_the_db(monkeypatch)

    assert _events_refusal([EVENTS_RUN_A, *flags]) == {
        "type": "CliError",
        "message": message,
    }


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--tail", "0", "--limit", "2"], "--tail must be at least 1, got 0"),
        (["--before-seq", "0", "--after-seq", "3"], "--before-seq must be at least 1, got 0"),
        (["--tail", "5", "--limit", "0"], "--limit must be at least 1, got 0"),
        (["--tail", "0", "--after-seq", "-1"], "--after-seq must be 0 or more, got -1"),
    ],
)
def test_events_value_checks_come_before_combination_checks(
    projection, monkeypatch, flags, message
):
    """C11: values 1-4 in order, then combinations."""
    _refuse_to_open_the_db(monkeypatch)

    assert _events_refusal([EVENTS_RUN_A, *flags]) == {
        "type": "CliError",
        "message": message,
    }


def test_events_before_seq_with_limit_alone_is_accepted(projection):
    """`--before-seq` with `--limit` is the backward page, not a conflict;
    `--after-seq 0` alone stays accepted."""
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert"))

    page = _events([EVENTS_RUN_A, "--before-seq", "100", "--limit", "1"])
    forward = _events([EVENTS_RUN_A, "--after-seq", "0"])

    assert [line["event"] for line in page["events"]] == ["story_upsert"]
    assert [line["event"] for line in forward["events"]] == ["run_upsert", "story_upsert"]


# ── am events --escalations (card 4b44104a) ──────────────────────────────────


def _insert_rows(*specs: tuple[Path, str, str, dict[str, Any]]) -> list[store_events.EventRow]:
    """One `events` row per `(repo, run_id, kind, payload)`, in order, each in
    `repo`'s project, committed together on a connection of its own; the rows
    as stored. No `runs` row is written."""
    conn = store_db.open_db(specs[0][0])
    try:
        rows = []
        for repo, run_id, kind, payload in specs:
            project_id = store_projects.resolve(conn, repo, now=RECORDED_AT)
            rows.append(
                store_events.insert(
                    conn,
                    project_id=project_id,
                    run_id=run_id,
                    ts=EVENT_TS,
                    kind=kind,
                    payload=payload,
                    source="live",
                )
            )
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture
def escalation_repos(projection) -> tuple[Path, Path]:
    """`projection` and a sibling repository, `other-repo`, both directories."""
    other = projection.parent / "other-repo"
    other.mkdir()
    return projection, other


def _insert_escalation_mix(root: Path, other: Path) -> list[store_events.EventRow]:
    """Run A in `root`, run B in `other`; only indexes 1, 3 and 5 are
    escalations (A, B, then A again), and the newest row is not one."""
    return _insert_rows(
        (root, EVENTS_RUN_A, "run_upsert", {"status": "running"}),
        (root, EVENTS_RUN_A, "run_upsert", {"status": "escalated"}),
        (other, EVENTS_RUN_B, "story_upsert", {"status": "escalated"}),
        (other, EVENTS_RUN_B, "run_upsert", {"status": "escalated"}),
        (root, EVENTS_RUN_A, "lease_acquired", {}),
        (root, EVENTS_RUN_A, "run_upsert", {"status": "escalated", "again": True}),
        (other, EVENTS_RUN_B, "run_upsert", {"status": "done"}),
    )


def test_escalations_for_lists_every_runs_escalations_in_gseq_order(escalation_repos):
    """Spec test 8, plan Review Focus 5: two runs in two projects, and no
    `runs` row for either."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)

    data = cli.escalations_for()

    assert set(data) == {"events", "head"}
    assert _gseqs(data) == [rows[1].seq, rows[3].seq, rows[5].seq]
    assert [line["run_id"] for line in data["events"]] == [
        EVENTS_RUN_A,
        EVENTS_RUN_B,
        EVENTS_RUN_A,
    ]
    assert [line["seq"] for line in data["events"]] == [2, 2, 4]
    for line in data["events"]:
        assert set(line) == EVENT_LINE_KEYS
        assert line["event"] == "run_upsert"
        assert line["payload"]["status"] == "escalated"
    assert data["events"] == [
        {**store_events.journal_line(row).model_dump(mode="json"), "gseq": row.seq}
        for row in (rows[1], rows[3], rows[5])
    ]
    assert data["head"] == rows[-1].seq == _events_head(root)


def test_escalations_for_pages_forward_by_gseq(escalation_repos):
    """Spec test 9, Review focus 5: --limit 1 pages visit each escalation once,
    the re-recorded run's two rows included, then an empty page."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)
    full = cli.escalations_for()

    walked = []
    after = 0
    for _ in range(10):
        page = cli.escalations_for(after_seq=after, limit=1)
        assert page["head"] == full["head"]
        if not page["events"]:
            break
        assert len(page["events"]) == 1
        walked.extend(page["events"])
        after = page["events"][-1]["gseq"]
    else:
        pytest.fail("paging never reached an empty page")

    assert walked == full["events"]
    assert _gseqs(cli.escalations_for(after_seq=rows[3].seq)) == [rows[5].seq]
    assert _gseqs(cli.escalations_for(limit=2)) == [rows[1].seq, rows[3].seq]


def test_escalations_for_project_narrows_and_head_stays_machine_wide(escalation_repos):
    """Spec test 10."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)

    only_other = cli.escalations_for(project=other)
    only_root = cli.escalations_for(project=root, after_seq=rows[1].seq)

    assert _gseqs(only_other) == [rows[3].seq]
    assert _gseqs(only_root) == [rows[5].seq]
    assert only_other["head"] == only_root["head"] == rows[-1].seq


def test_escalations_for_project_matches_every_spelling_of_the_directory(
    escalation_repos, monkeypatch
):
    """Spec test 10, Review focus 3, plan Review Focus 2: `..`, a symlink, a
    relative path and `~` all name `other`."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)
    link = root.parent / "link-to-other"
    link.symlink_to(other)
    monkeypatch.setenv("HOME", str(root.parent))
    monkeypatch.chdir(root)

    for spelling in (
        root / ".." / other.name,
        link,
        Path("..") / other.name,
        Path("~") / other.name,
    ):
        assert _gseqs(cli.escalations_for(project=spelling)) == [rows[3].seq], spelling


def test_escalations_for_an_unrecorded_project_is_an_empty_page(escalation_repos):
    """Spec test 11, Review focus 4, plan Review Focus 3: a missing path, an
    existing directory that never ran, and a regular file."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)
    never_ran = root.parent / "never-ran"
    never_ran.mkdir()
    a_file = root.parent / "README.md"
    a_file.write_text("not a repo\n", encoding="utf-8")

    for project in (root.parent / "deleted-repo", never_ran, a_file):
        assert cli.escalations_for(project=project) == {
            "events": [],
            "head": rows[-1].seq,
        }, project


def test_escalations_for_without_escalations_is_an_empty_page(projection):
    """Spec test 12: no escalations at all, and an `after_seq` above `head`."""
    _insert_events(
        projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert")
    )
    head = _events_head(projection)

    assert cli.escalations_for() == {"events": [], "head": head}
    assert cli.escalations_for(after_seq=head + 100) == {"events": [], "head": head}


def test_escalations_for_after_head_is_an_empty_page(escalation_repos):
    """Spec test 12."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)

    assert cli.escalations_for(after_seq=rows[-1].seq) == {
        "events": [],
        "head": rows[-1].seq,
    }
    assert cli.escalations_for(after_seq=rows[5].seq) == {
        "events": [],
        "head": rows[-1].seq,
    }


def test_escalations_for_never_shows_an_escalation_after_head(projection, monkeypatch):
    """Spec test 13: an escalation commits right after `head` returns."""
    _insert_rows((projection, EVENTS_RUN_A, "run_upsert", {"status": "escalated"}))
    head_before = _events_head(projection)
    writer, project_id = _open_event_writer(projection)
    try:

        def write() -> None:
            store_events.insert(
                writer,
                project_id=project_id,
                run_id=EVENTS_RUN_B,
                ts=EVENT_TS,
                kind="run_upsert",
                payload={"status": "escalated"},
                source="live",
            )
            writer.commit()

        fired = _write_once_after(monkeypatch, cli.store_events, "head", write)
        during = cli.escalations_for()

        assert fired == [True]
        assert during["head"] == head_before
        assert [line["run_id"] for line in during["events"]] == [EVENTS_RUN_A]
        assert all(line["gseq"] <= during["head"] for line in during["events"])
        after = cli.escalations_for()
        assert after["head"] > head_before
        assert [line["run_id"] for line in after["events"]] == [
            EVENTS_RUN_A,
            EVENTS_RUN_B,
        ]
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0}, "--limit must be at least 1, got 0"),
        ({"limit": -1}, "--limit must be at least 1, got -1"),
        ({"after_seq": -1}, "--after-seq must be 0 or more, got -1"),
        ({"limit": 0, "after_seq": -1}, "--limit must be at least 1, got 0"),
    ],
)
def test_escalations_for_rejects_bad_values_before_opening_the_db(
    projection, monkeypatch, kwargs, message
):
    """Spec test 14 (value half), with the same messages as `events_for`."""
    _refuse_to_open_the_db(monkeypatch)

    with pytest.raises(cli.CliError) as caught:
        cli.escalations_for(**kwargs)

    assert str(caught.value) == message


def test_events_escalations_through_the_cli_lists_every_runs_escalations(escalation_repos):
    """Spec test 8 through the command: the same envelope as `escalations_for`."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)

    data = _events(["--escalations"])

    assert data == cli.escalations_for()
    assert _gseqs(data) == [rows[1].seq, rows[3].seq, rows[5].seq]
    for line in data["events"]:
        assert set(line) == EVENT_LINE_KEYS
    assert data["head"] == rows[-1].seq


def test_events_escalations_through_the_cli_pages_forward(escalation_repos):
    """Spec test 9, plan Review Focus 4: an explicit --after-seq 0 is the bare form."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)
    full = _events(["--escalations"])

    assert _events(["--escalations", "--after-seq", "0"]) == full
    first = _events(["--escalations", "--limit", "1"])
    second = _events(
        ["--escalations", "--after-seq", str(_gseqs(first)[-1]), "--limit", "1"]
    )
    rest = _events(["--escalations", "--after-seq", str(_gseqs(second)[-1])])
    assert _gseqs(first) == [rows[1].seq]
    assert _gseqs(second) == [rows[3].seq]
    assert _gseqs(rest) == [rows[5].seq]
    assert _events(["--escalations", "--after-seq", str(rows[5].seq)]) == {
        "events": [],
        "head": full["head"],
    }


def test_events_escalations_project_through_the_cli(escalation_repos, monkeypatch):
    """Spec tests 10 and 11 through the command."""
    root, other = escalation_repos
    rows = _insert_escalation_mix(root, other)
    monkeypatch.setenv("HOME", str(root.parent))

    narrowed = _events(["--escalations", "--project", f"~/{other.name}"])
    missing = _events(["--escalations", "--project", str(root.parent / "deleted-repo")])

    assert _gseqs(narrowed) == [rows[3].seq]
    assert narrowed["head"] == rows[-1].seq
    assert missing == {"events": [], "head": rows[-1].seq}


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([EVENTS_RUN_A, "--escalations"], "--escalations cannot be combined with RUN"),
        ([], "RUN is required unless --escalations is given"),
        (["--after-seq", "3"], "RUN is required unless --escalations is given"),
        (["--project", "."], "--project requires --escalations"),
        ([EVENTS_RUN_A, "--project", "."], "--project requires --escalations"),
        (["--escalations", "--tail", "1"], "--escalations cannot be combined with --tail"),
        (
            ["--escalations", "--before-seq", "5"],
            "--escalations cannot be combined with --before-seq",
        ),
        (
            ["--escalations", "--tail", "1", "--before-seq", "5"],
            "--escalations cannot be combined with --tail",
        ),
        (["--escalations", "--limit", "0"], "--limit must be at least 1, got 0"),
        (["--escalations", "--after-seq", "-1"], "--after-seq must be 0 or more, got -1"),
        (["--escalations", "--limit", "0", "--tail", "1"], "--limit must be at least 1, got 0"),
        ([EVENTS_RUN_A, "--escalations", "--tail", "0"], "--tail must be at least 1, got 0"),
        (
            [EVENTS_RUN_A, "--escalations", "--tail", "5", "--after-seq", "3"],
            "--escalations cannot be combined with RUN",
        ),
        (
            [EVENTS_RUN_A, "--project", ".", "--tail", "5", "--limit", "2"],
            "--project requires --escalations",
        ),
    ],
)
def test_events_refuses_a_bad_form_before_opening_the_db(
    projection, monkeypatch, argv, message
):
    """Spec test 14: each form refusal with its exact message, value checks
    first, form checks before the RUN form's combination checks."""
    _refuse_to_open_the_db(monkeypatch)

    assert _events_refusal(argv) == {"type": "CliError", "message": message}


def test_events_escalations_help_texts():
    parameters = inspect.signature(cli.events).parameters

    assert parameters["run_id"].default.default is None
    assert parameters["escalations"].default.help == (
        "List every run's escalation events (a run_upsert whose status is"
        " escalated) instead of one run's. Not with RUN, --tail or --before-seq."
    )
    assert parameters["project"].default.help == (
        "With --escalations, list only this repository's escalations. A path"
        " that never ran is an empty page."
    )
    assert "--escalations" in cli.events.__doc__
    assert "--before-seq" in cli.events.__doc__
    assert "--tail N" in cli.events.__doc__


def _write_logs_attempt(
    run_id: str,
    phase: str,
    n: int,
    *,
    stdout: bool = True,
    status: str | None = None,
) -> models.Attempt:
    """One attempt's three files on disk, plus the row that points at them.

    The *test* calls `paths.attempt_dir` -- which creates the directory -- because
    in production `dispatch.AgentRunner` is what creates it. `logs` itself must
    never call it, and `test_logs_writes_nothing` is what pins that.

    `status=None` keeps the long-standing default (`gate_failed` for attempt 1,
    `ok` after it). A `started` attempt has no exit code yet.
    """
    if status is None:
        status = "ok" if n > 1 else "gate_failed"
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
        status=status,
        exit_code=None if status == "started" else (0 if status == "ok" else 1),
        prompt_path=directory / "prompt.txt",
        result_path=directory / "result.json",
        stdout_path=directory / "stdout.log",
    )


def _record_for_logs(
    root: Path,
    run_id: str,
    *,
    stdout: bool = True,
    implement_status: str | None = None,
) -> None:
    """A run with two agent phases (two attempts, then one) and a pending phase.

    The trailing `verify` phase has no attempts, so the no-flag default has to
    skip it to reach `implement`. `implement_status` sets `implement.1`'s
    status; `None` keeps the default `gate_failed`, which is terminal, so a
    `logs --follow` test that needs the stream to keep polling passes
    `"started"`.
    """
    opened = store_writer.Store.open(root, run_id)
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
            _write_logs_attempt(
                run_id, "implement", 1, stdout=stdout, status=implement_status
            ),
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
    assert data["artifacts"]["stderr"] == {"path": None, "present": False, "text": None}
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
    conn = sqlite3.connect(paths.db_path())
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


def _write_step_logs(run_id: str, n: int) -> Path:
    """`verify.<n>/` as `run_one_step` + `verify.run_suite` leave it.

    The *test* calls `paths.attempt_dir`, which creates the directory; `logs`
    must only read it.
    """
    directory = paths.attempt_dir(run_id, "card-1", "verify", n)
    (directory / "stdout.log").write_text(
        f"==> uv run pytest (exit 1)\nstdout of verify.{n}\n", encoding="utf-8"
    )
    (directory / "stderr.log").write_text(
        f"==> uv run pytest (exit 1)\nstderr of verify.{n}\n", encoding="utf-8"
    )
    return directory


def test_logs_for_a_deterministic_phase_reads_its_highest_attempt_off_disk(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)
    second = _write_step_logs(LOGS_RUN_ID, 2)
    base = ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]

    result = runner.invoke(cli.app, [*base, "--phase", "verify"])

    assert result.exit_code == 0, result.stdout
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["run_id"] == LOGS_RUN_ID
    assert data["story_id"] == "story-1"
    assert data["card"] == "card-1"
    assert data["phase"] == "verify"
    assert data["attempt"] == 2
    assert data["status"] is None
    assert data["exit_code"] is None
    artifacts = data["artifacts"]
    assert artifacts["prompt"] == {"path": None, "present": False, "text": None}
    assert artifacts["result"] == {"path": None, "present": False, "text": None}
    assert artifacts["stdout"] == {
        "path": str(second / "stdout.log"),
        "present": True,
        "text": "==> uv run pytest (exit 1)\nstdout of verify.2\n",
    }
    assert artifacts["stderr"] == {
        "path": str(second / "stderr.log"),
        "present": True,
        "text": "==> uv run pytest (exit 1)\nstderr of verify.2\n",
    }

    earlier = runner.invoke(cli.app, [*base, "--phase", "verify", "--attempt", "1"])

    assert earlier.exit_code == 0, earlier.stdout
    earlier_data = json.loads(earlier.stdout)["data"]
    assert earlier_data["attempt"] == 1
    assert earlier_data["artifacts"]["stdout"]["text"] == (
        "==> uv run pytest (exit 1)\nstdout of verify.1\n"
    )


def test_logs_for_a_deterministic_phase_reports_a_missing_log_file_as_absent(
    projection,
):
    _record_for_logs(projection, LOGS_RUN_ID)
    directory = _write_step_logs(LOGS_RUN_ID, 1)
    (directory / "stderr.log").unlink()

    result = runner.invoke(
        cli.app,
        ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)],
    )

    assert result.exit_code == 0, result.stdout
    artifacts = json.loads(result.stdout)["data"]["artifacts"]
    assert artifacts["stdout"]["present"] is True
    assert artifacts["stderr"] == {
        "path": str(directory / "stderr.log"),
        "present": False,
        "text": None,
    }


def test_logs_for_a_deterministic_phase_with_no_attempt_on_disk_is_an_envelope(
    projection,
):
    _record_for_logs(projection, LOGS_RUN_ID)
    tree_before = _runs_snapshot()

    result = runner.invoke(
        cli.app,
        ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)],
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownAttemptError"
    assert envelope["error"]["message"] == (
        "phase 'verify' of card 'card-1' has no recorded attempt yet"
    )
    assert _runs_snapshot() == tree_before
    assert not (paths.data_dir() / "runs" / LOGS_RUN_ID / "card-1" / "verify.1").exists()


def test_logs_for_a_deterministic_phase_names_the_attempts_it_has(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)
    _write_step_logs(LOGS_RUN_ID, 2)
    base = ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)]

    for wanted in ("7", "0"):
        result = runner.invoke(cli.app, [*base, "--attempt", wanted])

        assert result.exit_code == cli.EXIT_ERROR
        envelope = json.loads(result.stdout)
        assert envelope["error"]["type"] == "UnknownAttemptError"
        assert envelope["error"]["message"] == (
            f"phase 'verify' of card 'card-1' has no attempt {wanted};"
            " recorded attempts: 1, 2"
        )


def test_logs_for_a_deterministic_phase_with_no_runs_directory_creates_nothing(
    projection,
):
    """Review Focus 4: the projection survives but `runs/` is gone. `logs`
    refuses, and leaves `runs/` absent rather than minting it."""
    _record_for_logs(projection, LOGS_RUN_ID)
    runs_root = paths.data_dir() / "runs"
    shutil.rmtree(runs_root)
    base = ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)]

    latest = runner.invoke(cli.app, base)
    numbered = runner.invoke(cli.app, [*base, "--attempt", "1"])

    assert latest.exit_code == cli.EXIT_ERROR
    assert json.loads(latest.stdout)["error"]["message"] == (
        "phase 'verify' of card 'card-1' has no recorded attempt yet"
    )
    assert numbered.exit_code == cli.EXIT_ERROR
    assert json.loads(numbered.stdout)["error"]["message"] == (
        "phase 'verify' of card 'card-1' has no attempt 1; recorded attempts: none"
    )
    assert not runs_root.exists()


def test_logs_with_no_flags_still_skips_a_deterministic_phase_with_logs_on_disk(
    projection,
):
    """Decision 4: the no-flag default stays pure over recorded `Attempt` rows."""
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)["data"]
    assert data["phase"] == "implement"
    assert data["artifacts"]["stderr"] == {"path": None, "present": False, "text": None}


def test_logs_for_a_deterministic_phase_writes_nothing(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    result = runner.invoke(
        cli.app,
        ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)],
    )

    assert result.exit_code == 0
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before


def _implement_stdout() -> Path:
    """Where `_record_for_logs` puts `implement.1`'s stdout, read-only."""
    return paths.attempt_path(LOGS_RUN_ID, "card-1", "implement", 1) / "stdout.log"


def test_logs_without_follow_unchanged(projection):
    """Card 4.1: without `--follow`, `logs` is still exactly one envelope."""
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert set(envelope) == {"ok", "data"}
    assert "event" not in envelope
    assert "offset" not in envelope["data"]
    assert envelope["data"]["artifacts"]["stdout"]["text"] == "stdout of implement.1\n"


def test_select_logs_names_the_file_a_follow_reads(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    step_dir = _write_step_logs(LOGS_RUN_ID, 1)

    agent = cli.select_logs(LOGS_RUN_ID, "card-1", repo_dir=projection)
    assert agent.phase.name == "implement"
    assert agent.attempt is not None
    assert agent.attempt.n == 1
    assert agent.followed_path() == _implement_stdout()
    assert agent.payload() == cli.logs_for(LOGS_RUN_ID, "card-1", repo_dir=projection)

    step = cli.select_logs(LOGS_RUN_ID, "card-1", repo_dir=projection, phase="verify")
    assert step.phase.name == "verify"
    assert step.attempt is None
    assert step.step_attempt == 1
    assert step.followed_path() == step_dir / "stdout.log"
    assert step.payload() == cli.logs_for(
        LOGS_RUN_ID, "card-1", repo_dir=projection, phase="verify"
    )


def test_logs_selection_without_attempt_or_step_names_nothing(projection):
    """A selection carrying neither an `Attempt` row nor a step directory
    names no file to follow and refuses to build a payload."""
    _record_for_logs(projection, LOGS_RUN_ID)
    agent = cli.select_logs(LOGS_RUN_ID, "card-1", repo_dir=projection)
    empty = cli.LogsSelection(
        run=agent.run,
        story=agent.story,
        subtask=agent.subtask,
        phase=agent.phase,
        attempt=None,
    )

    assert empty.followed_path() is None
    with pytest.raises(cli.CliError, match="neither an attempt row nor a step"):
        empty.payload()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", 0),
        (b"abc", 3),
        ("é".encode(), 2),
        ("é".encode()[:1], 0),
        (b"caf\xc3", 3),
        (b"x" + "€".encode()[:2], 1),
        (b"x" + "€".encode(), 4),
        (b"ab" + "😀".encode()[:3], 2),
        (b"ab" + "😀".encode(), 6),
    ],
)
def test_utf8_complete_length_holds_back_only_a_truncated_tail(data, expected):
    assert cli._utf8_complete_length(data) == expected


@pytest.mark.parametrize(
    "data",
    [b"ok\xff", b"ok\xc0", b"\x80\x80\x80\x80", b"ok\x80"],
)
def test_utf8_complete_length_never_holds_back_invalid_bytes(data):
    """A byte that cannot start a sequence is emitted (as U+FFFD), so an
    invalid tail can never stall the stream."""
    assert cli._utf8_complete_length(data) == len(data)


def test_read_log_bytes_reads_from_the_offset(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_bytes(b"0123456789")

    assert cli._read_log_bytes(log, 0) == b"0123456789"
    assert cli._read_log_bytes(log, 4) == b"456789"
    assert cli._read_log_bytes(log, 10) == b""
    assert cli._read_log_bytes(log, 50) == b""


def test_read_log_bytes_is_empty_for_anything_unreadable(tmp_path):
    assert cli._read_log_bytes(None, 0) == b""
    assert cli._read_log_bytes(tmp_path / "missing.log", 0) == b""
    assert cli._read_log_bytes(tmp_path, 0) == b""
    assert not (tmp_path / "missing.log").exists()


def _logs_follow(monkeypatch, *args: str, actions=(), follow: bool = True):
    """Run `am logs ARGS --follow` for exactly `len(actions)` polls after the backlog.

    Modelled on `_watch_follow`: sleep `i` runs `actions[i]` (an append, a
    truncate, a Ctrl-C) before poll `i` reads. `follow=False` drops the flag
    so the refusal of `--since-offset` alone can be driven through the same
    helper. Returns the result and the seconds each sleep was asked for.
    """
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        actions[len(sleeps) - 1]()

    monkeypatch.setattr(cli, "_watch_sleep", fake_sleep)
    monkeypatch.setattr(cli, "WATCH_MAX_POLLS", len(actions))
    argv = ["logs", *args, *(["--follow"] if follow else [])]
    return runner.invoke(cli.app, argv), sleeps


def _logs_args(projection: Path, *extra: str) -> list[str]:
    return [LOGS_RUN_ID, "card-1", *extra, "--repo-dir", str(projection)]


def _logs_hello_line(path: Path, offset: int = 0) -> dict[str, Any]:
    return {"event": "logs", "schema": 1, "path": str(path), "offset": offset}


def _assert_contiguous(chunks: list[dict[str, Any]], start: int) -> int:
    """Each chunk starts where the last ended, in bytes; none is empty.

    Only for valid UTF-8 content: a U+FFFD from replacement re-encodes to a
    different byte length than the bytes it replaced.
    """
    cursor = start
    for chunk in chunks:
        assert set(chunk) == {"offset", "text"}, chunk
        assert chunk["offset"] == cursor, chunks
        assert chunk["text"], chunks
        cursor += len(chunk["text"].encode("utf-8"))
    return cursor


def test_logs_follow_hello_shape(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")

    result, sleeps = _logs_follow(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert sleeps == []
    lines = _stream(result)
    assert lines[0] == _logs_hello_line(_implement_stdout())
    assert all("ok" not in line for line in lines)
    assert all("event" not in line for line in lines[1:])
    assert result.stdout.endswith("\n")
    assert all(": " not in text for text in result.stdout.splitlines())
    assert result.stderr == ""

    # `--pretty` only shapes a refusal: the stream is byte-for-byte the same.
    pretty, _ = _logs_follow(monkeypatch, *_logs_args(projection, "--pretty"))
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == result.stdout


def test_logs_follow_streams_backlog_with_offsets(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    content = "first line\nsecond líne\n€uro\n"
    _implement_stdout().write_bytes(content.encode("utf-8"))

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines[0] == _logs_hello_line(_implement_stdout())
    chunks = lines[1:]
    assert chunks[0]["offset"] == 0
    assert "".join(chunk["text"] for chunk in chunks) == content
    assert _assert_contiguous(chunks, 0) == len(content.encode("utf-8"))


def test_logs_follow_multibyte_not_split(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    stdout = _implement_stdout()
    stdout.write_bytes(b"caf\xc3")  # the first byte of "é" only

    def finish_the_character() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"\xa9 ok\n")

    result, _ = _logs_follow(
        monkeypatch,
        *_logs_args(projection),
        actions=[finish_the_character, lambda: None],
    )

    assert result.exit_code == 0, result.output
    chunks = _stream(result)[1:]
    assert chunks == [
        {"offset": 0, "text": "caf"},
        {"offset": 3, "text": "é ok\n"},
    ]
    assert all("�" not in chunk["text"] for chunk in chunks)
    assert _assert_contiguous(chunks, 0) == stdout.stat().st_size


def test_logs_follow_since_offset_resumes(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    content = _implement_stdout().read_bytes()
    assert content == b"stdout of implement.1\n"

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection, "--since-offset", "10"))

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines[0] == _logs_hello_line(_implement_stdout(), 10)
    assert lines[1:] == [{"offset": 10, "text": content[10:].decode("utf-8")}]


def test_logs_follow_picks_up_appended_data(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    stdout = _implement_stdout()
    original = stdout.read_bytes()

    def append_more() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"more\n")

    # Poll 1 sees the append; poll 2 sees nothing new, so it must not repeat.
    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[append_more, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert sleeps == [cli.WATCH_POLL_SECONDS, cli.WATCH_POLL_SECONDS]
    lines = _stream(result)
    assert lines[1:] == [
        {"offset": 0, "text": original.decode("utf-8")},
        {"offset": len(original), "text": "more\n"},
    ]
    assert all(line.get("event") != "end" for line in lines)


def test_logs_follow_waits_for_missing_file(projection, monkeypatch):
    _record_for_logs(
        projection, LOGS_RUN_ID, stdout=False, implement_status="started"
    )
    stdout = _implement_stdout()

    def still_absent() -> None:
        assert not stdout.exists()

    def create_it() -> None:
        stdout.write_bytes(b"late\n")

    result, _ = _logs_follow(
        monkeypatch,
        *_logs_args(projection),
        actions=[still_absent, create_it, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(stdout),
        {"offset": 0, "text": "late\n"},
    ]


def test_logs_follow_deterministic_phase_follows_stdout_log(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)
    second = _write_step_logs(LOGS_RUN_ID, 2)

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection, "--phase", "verify"))

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _logs_hello_line(second / "stdout.log"),
        {"offset": 0, "text": "==> uv run pytest (exit 1)\nstdout of verify.2\n"},
    ]


def test_logs_follow_writes_nothing(projection, monkeypatch):
    """`logs --follow` is read-only like `logs`: a missing stdout file is
    waited on, never created; the per-read status re-lookup creates nothing,
    whether it finds the attempt running or over; a refusal mints no run
    directory."""
    _record_for_logs(
        projection, LOGS_RUN_ID, stdout=False, implement_status="started"
    )
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    polling, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[lambda: None]
    )
    assert polling.exit_code == 0, polling.output
    assert sleeps == [cli.WATCH_POLL_SECONDS]
    assert all(line.get("event") != "end" for line in _stream(polling))
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
    assert not _implement_stdout().exists()

    # The test, not `logs`, records the terminal status; snapshot after it.
    _set_implement_status(projection, "harness_error")
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    ended = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))
    assert ended.exit_code == 0, ended.output
    assert _stream(ended)[-1] == _end_line("harness_error")
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
    assert not _implement_stdout().exists()

    refusal, _ = _logs_follow(
        monkeypatch, "no-such-run", "card-1", "--repo-dir", str(projection)
    )
    assert refusal.exit_code == cli.EXIT_ERROR
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()


def test_logs_follow_since_offset_beyond_end_waits(projection, monkeypatch):
    """Review Focus 2: a cursor past EOF is not an error; bytes appear once
    the file grows past it, starting exactly at the cursor."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    stdout = _implement_stdout()
    size = stdout.stat().st_size

    def grow_short_of_the_cursor() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"a" * (500 - size))

    def grow_past_the_cursor() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"b" * 505)

    result, _ = _logs_follow(
        monkeypatch,
        *_logs_args(projection, "--since-offset", "1000"),
        actions=[grow_short_of_the_cursor, grow_past_the_cursor],
    )

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _logs_hello_line(stdout, 1000),
        {"offset": 1000, "text": "bbbbb"},
    ]


def test_logs_follow_truncated_file_emits_nothing_new(projection, monkeypatch):
    """Review Focus 3: a file cut below the cursor is no crash and no rewind;
    bytes below the cursor are never re-emitted."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    stdout = _implement_stdout()
    original = stdout.read_bytes()

    def truncate() -> None:
        stdout.write_bytes(b"")

    def rewrite_shorter() -> None:
        stdout.write_bytes(b"new\n")

    result, _ = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[truncate, rewrite_shorter]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(stdout),
        {"offset": 0, "text": original.decode("utf-8")},
    ]


def test_logs_follow_invalid_bytes_keep_byte_offsets(projection, monkeypatch):
    """Review Focus 1: invalid UTF-8 becomes U+FFFD, and the next offset
    still counts raw bytes, not decoded characters."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    stdout = _implement_stdout()
    stdout.write_bytes(b"ok\xff\xfe\n")

    def append_more() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"next\n")

    result, _ = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[append_more]
    )

    assert result.exit_code == 0, result.output
    assert _stream(result)[1:] == [
        {"offset": 0, "text": "ok��\n"},
        {"offset": 5, "text": "next\n"},
    ]


def _record_review_without_stdout_path(root: Path) -> None:
    """Add an agent `review` phase whose one attempt recorded no stdout path."""
    opened = store_writer.Store.open(root, LOGS_RUN_ID)
    try:
        opened.record_phase(
            "story-1", "card-1", models.PhaseRun(name="review", kind="agent", status="failed")
        )
        opened.record_attempt(
            "story-1",
            "card-1",
            "review",
            models.Attempt(
                n=1, dispatch=_recorded_dispatch(LOGS_RUN_ID), status="harness_error"
            ),
        )
    finally:
        opened.close()


@pytest.mark.parametrize(
    ("argv", "follow", "kind", "message"),
    [
        (["no-such-run", "card-1"], True, "UnknownRunError", None),
        ([LOGS_RUN_ID, "card-9"], True, "UnknownCardError", None),
        ([LOGS_RUN_ID, "card-1", "--phase", "reveiw"], True, "UnknownPhaseError", None),
        (
            [LOGS_RUN_ID, "card-1", "--phase", "explore", "--attempt", "9"],
            True,
            "UnknownAttemptError",
            None,
        ),
        ([LOGS_RUN_ID, "card-1", "--phase", "verify"], True, "UnknownAttemptError", None),
        (
            [LOGS_RUN_ID, "card-1", "--phase", "review"],
            True,
            "CliError",
            "attempt 1 of phase 'review' of card 'card-1' recorded no stdout path,"
            " so there is no file to follow",
        ),
        (
            [LOGS_RUN_ID, "card-1", "--since-offset", "-1"],
            True,
            "CliError",
            "--since-offset must be 0 or more, got -1",
        ),
        (
            [LOGS_RUN_ID, "card-1", "--since-offset", "0"],
            False,
            "CliError",
            "--since-offset needs --follow: it resumes a stream,"
            " and without --follow there is no stream",
        ),
        (
            [LOGS_RUN_ID, "card-1", "--since-offset", "5"],
            False,
            "CliError",
            "--since-offset needs --follow: it resumes a stream,"
            " and without --follow there is no stream",
        ),
    ],
)
def test_logs_follow_refusals(projection, monkeypatch, argv, follow, kind, message):
    _record_for_logs(projection, LOGS_RUN_ID)
    _record_review_without_stdout_path(projection)

    result, sleeps = _logs_follow(
        monkeypatch,
        *argv,
        "--repo-dir",
        str(projection),
        actions=[lambda: None],
        follow=follow,
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert sleeps == []
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert envelope["ok"] is False
    assert "event" not in envelope
    assert envelope["error"]["type"] == kind
    if message is not None:
        assert envelope["error"]["message"] == message


def test_logs_follow_ctrl_c_exits_zero(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")

    def press_ctrl_c() -> None:
        raise KeyboardInterrupt

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection), actions=[press_ctrl_c])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]


def test_logs_follow_closed_pipe_exits_zero_quietly(projection, monkeypatch):
    """Review Focus 4: `am logs ... --follow | head -1`."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    real_emit = cli._emit_stream_line
    emitted: list[Any] = []

    def emit_into_a_closed_pipe(obj) -> None:
        emitted.append(obj)
        if len(emitted) == 2:  # the reader went away after the hello line
            raise BrokenPipeError(32, "Broken pipe")
        real_emit(obj)

    monkeypatch.setattr(cli, "_emit_stream_line", emit_into_a_closed_pipe)

    result, _ = _logs_follow(monkeypatch, *_logs_args(projection), actions=[lambda: None])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [_logs_hello_line(_implement_stdout())]


def test_logs_follow_mid_stream_error_goes_to_stderr(projection, monkeypatch):
    """Review Focus 5: after the hello no envelope can follow, so a handled
    error is one stderr line and exit 3, as `watch --follow` does."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    real_read = cli._read_log_bytes
    reads: list[int] = []

    def read_then_fail(path, offset):
        reads.append(offset)
        if len(reads) == 2:
            raise cli.CliError("the log went away")
        return real_read(path, offset)

    monkeypatch.setattr(cli, "_read_log_bytes", read_then_fail)

    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[lambda: None, lambda: None]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert len(sleeps) == 1  # the stream ended on the failing poll
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]
    assert result.stderr == "am logs: the log went away\n"


def _verify_phase(status: str) -> tuple[models.SubtaskRun, models.PhaseRun]:
    subtask = models.SubtaskRun(card_id="card-1", branch="m1/task-x", base_branch="main")
    return subtask, models.PhaseRun(name="verify", kind="deterministic", status=status)


@pytest.mark.parametrize(
    ("status", "recorded", "n", "expected"),
    [
        ("done", [1, 2], 2, "ok"),
        ("failed", [1, 2], 2, "gate_failed"),
        ("escalated", [1], 1, "gate_failed"),
        ("stopped", [1], 1, "gate_failed"),
        ("cancelled", [1], 1, "gate_failed"),
        ("started", [1, 2], 1, "gate_failed"),
        ("done", [1, 2], 1, "gate_failed"),
        ("pending", [1], 1, None),
        ("started", [1], 1, None),
    ],
    ids=[
        "latest-done",
        "latest-failed",
        "latest-escalated",
        "latest-stopped",
        "latest-cancelled",
        "superseded-while-started",
        "superseded-while-done",
        "latest-pending",
        "latest-started",
    ],
)
def test_step_end_status(status, recorded, n, expected):
    """Card 4.2's mapping for a deterministic phase: superseded or failed is
    `gate_failed`, latest and `done` is `ok`, latest and running is `None`."""
    subtask, phase = _verify_phase(status)

    assert cli.step_end_status(subtask, phase, n, recorded) == expected


@pytest.mark.parametrize(("n", "recorded"), [(3, [1, 2]), (1, [])])
def test_step_end_status_refuses_an_attempt_no_longer_on_disk(n, recorded):
    """Review Focus 5: the followed `<phase>.N` directory is gone, so the
    re-lookup refuses instead of reporting an end it cannot know."""
    subtask, phase = _verify_phase("done")

    with pytest.raises(cli.UnknownAttemptError, match=f"has no attempt {n} any more"):
        cli.step_end_status(subtask, phase, n, recorded)


def _logs_follow_no_wait(monkeypatch, *args: str):
    """Run `am logs ARGS --follow` with no poll bound and a sleep that fails.

    For an attempt that is already over: the stream must drain and end on
    its own, so any sleep is a bug, and an unbounded loop would hang rather
    than pass. `pytest.fail` raises a `BaseException`, which `CliRunner`
    does not swallow.
    """

    def no_sleep(seconds: float) -> None:
        pytest.fail(f"the stream slept {seconds}s on an attempt that is already over")

    monkeypatch.setattr(cli, "_watch_sleep", no_sleep)
    monkeypatch.setattr(cli, "WATCH_MAX_POLLS", None)
    return runner.invoke(cli.app, ["logs", *args, "--follow"])


def _set_implement_status(root: Path, status: str) -> None:
    """Re-record `implement.1` with `status`, as the runner's terminal write
    does. Test-side only: it opens a `Store`, which `logs` must never do."""
    directory = paths.attempt_path(LOGS_RUN_ID, "card-1", "implement", 1)
    opened = store_writer.Store.open(root, LOGS_RUN_ID)
    try:
        opened.record_attempt(
            "story-1",
            "card-1",
            "implement",
            models.Attempt(
                n=1,
                dispatch=_recorded_dispatch(LOGS_RUN_ID),
                status=status,
                exit_code=None if status == "started" else (0 if status == "ok" else 1),
                prompt_path=directory / "prompt.txt",
                result_path=directory / "result.json",
                stdout_path=directory / "stdout.log",
            ),
        )
    finally:
        opened.close()


def _set_verify_status(root: Path, status: str) -> None:
    """Re-record the deterministic `verify` phase with `status`."""
    opened = store_writer.Store.open(root, LOGS_RUN_ID)
    try:
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="verify", kind="deterministic", status=status),
        )
    finally:
        opened.close()


def _end_line(status: str) -> dict[str, Any]:
    return {"event": "end", "status": status}


@pytest.mark.parametrize("status", ["ok", "schema_invalid", "gate_failed", "harness_error"])
def test_logs_follow_ends_on_terminal_status(projection, monkeypatch, status):
    """Card 4.2: bytes appended just before the status flips are still
    streamed, then `end` carries the attempt's status and the exit is 0."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    stdout = _implement_stdout()
    original = stdout.read_bytes()

    def finish_the_attempt() -> None:
        with stdout.open("ab") as handle:
            handle.write(b"last words\n")
        _set_implement_status(projection, status)

    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[finish_the_attempt]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert sleeps == [cli.WATCH_POLL_SECONDS]
    assert _stream(result) == [
        _logs_hello_line(stdout),
        {"offset": 0, "text": original.decode("utf-8")},
        {"offset": len(original), "text": "last words\n"},
        _end_line(status),
    ]
    assert result.stdout.splitlines()[-1] == f'{{"event":"end","status":"{status}"}}'


def test_logs_follow_checks_status_before_the_read_it_applies_to(
    projection, monkeypatch
):
    """The writer appends and flips the status right after a read that found
    nothing. Because the status was looked up *before* that read, it was
    still `started`: the stream polls once more, reads the last bytes, and
    only then ends. Looking the status up after the read would end here and
    lose them."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    stdout = _implement_stdout()
    size = stdout.stat().st_size
    real_read = cli._read_log_bytes
    reads: list[int] = []

    def read_then_writer_finishes(path, offset):
        data = real_read(path, offset)
        reads.append(offset)
        if len(reads) == 1:
            assert data == b""
            with stdout.open("ab") as handle:
                handle.write(b"last words\n")
            _set_implement_status(projection, "ok")
        return data

    monkeypatch.setattr(cli, "_read_log_bytes", read_then_writer_finishes)

    result, sleeps = _logs_follow(
        monkeypatch,
        *_logs_args(projection, "--since-offset", str(size)),
        actions=[lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert sleeps == [cli.WATCH_POLL_SECONDS]
    assert _stream(result) == [
        _logs_hello_line(stdout, size),
        {"offset": size, "text": "last words\n"},
        _end_line("ok"),
    ]


def test_logs_follow_already_complete_file_ends_without_waiting(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID)  # implement.1 is gate_failed
    content = "first line\nsecond líne\n€uro\n"
    _implement_stdout().write_bytes(content.encode("utf-8"))

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": content},
        _end_line("gate_failed"),
    ]
    assert result.stdout.splitlines()[-1] == '{"event":"end","status":"gate_failed"}'

    # Review Focus 4: `--pretty` changes no byte of a stream that ends.
    pretty = _logs_follow_no_wait(monkeypatch, *_logs_args(projection, "--pretty"))
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == result.stdout


def test_logs_follow_started_attempt_never_ends_on_poll_bound(projection, monkeypatch):
    """The bound stops a test stream; it is not an end. Passes before the
    change too: it pins that `end` is never emitted for a `started` attempt."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")

    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[lambda: None, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert sleeps == [cli.WATCH_POLL_SECONDS, cli.WATCH_POLL_SECONDS]
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]


def test_logs_follow_terminal_missing_file_ends(projection, monkeypatch):
    _record_for_logs(projection, LOGS_RUN_ID, stdout=False)  # gate_failed

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        _end_line("gate_failed"),
    ]
    assert not _implement_stdout().exists()


def test_logs_follow_terminal_flushes_partial_utf8_tail(projection, monkeypatch):
    """The held-back half character can never be completed once the attempt
    is over, so it goes out as U+FFFD at its own byte offset before `end`."""
    _record_for_logs(projection, LOGS_RUN_ID)  # gate_failed
    _implement_stdout().write_bytes(b"caf\xc3")  # the first byte of "é" only

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "caf"},
        {"offset": 3, "text": "�"},
        _end_line("gate_failed"),
    ]


@pytest.mark.parametrize("past_end", [0, 978], ids=["at-eof", "past-eof"])
def test_logs_follow_since_offset_at_eof_on_terminal_attempt(
    projection, monkeypatch, past_end
):
    _record_for_logs(projection, LOGS_RUN_ID)  # gate_failed
    offset = _implement_stdout().stat().st_size + past_end

    result = _logs_follow_no_wait(
        monkeypatch, *_logs_args(projection, "--since-offset", str(offset))
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout(), offset),
        _end_line("gate_failed"),
    ]


@pytest.mark.parametrize(
    ("phase_status", "attempts", "extra", "expected"),
    [
        ("done", 2, [], "ok"),
        ("failed", 2, [], "gate_failed"),
        ("started", 2, ["--attempt", "1"], "gate_failed"),
        ("pending", 1, [], None),
        ("started", 1, [], None),
    ],
    ids=["latest-done", "latest-failed", "superseded", "latest-pending", "latest-started"],
)
def test_logs_follow_deterministic_phase_end_status(
    projection, monkeypatch, phase_status, attempts, extra, expected
):
    """Card 4.2's flagged mapping, end to end through the CLI."""
    _record_for_logs(projection, LOGS_RUN_ID)
    for n in range(1, attempts + 1):
        _write_step_logs(LOGS_RUN_ID, n)
    _set_verify_status(projection, phase_status)
    followed = 1 if extra else attempts
    stdout = paths.attempt_path(LOGS_RUN_ID, "card-1", "verify", followed) / "stdout.log"
    body = {
        "offset": 0,
        "text": f"==> uv run pytest (exit 1)\nstdout of verify.{followed}\n",
    }
    args = _logs_args(projection, "--phase", "verify", *extra)

    if expected is None:
        result, sleeps = _logs_follow(monkeypatch, *args, actions=[lambda: None])
        assert result.exit_code == 0, result.output
        assert sleeps == [cli.WATCH_POLL_SECONDS]
        assert _stream(result) == [_logs_hello_line(stdout), body]
    else:
        result = _logs_follow_no_wait(monkeypatch, *args)
        assert result.exit_code == 0, result.output
        assert _stream(result) == [_logs_hello_line(stdout), body, _end_line(expected)]
    assert result.stderr == ""


def test_logs_follow_relookup_lost_attempt_errors(projection, monkeypatch):
    """The run vanishes from the projection after the hello: the re-lookup
    refuses on stderr at exit 3, as any post-hello error does."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")

    def forget_the_run() -> None:
        conn = sqlite3.connect(paths.db_path())
        try:
            conn.execute("DELETE FROM runs WHERE id = ?", (LOGS_RUN_ID,))
            conn.commit()
        finally:
            conn.close()

    result, sleeps = _logs_follow(
        monkeypatch, *_logs_args(projection), actions=[forget_the_run]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert sleeps == [cli.WATCH_POLL_SECONDS]
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]
    assert result.stderr.startswith(
        f"am logs: run {LOGS_RUN_ID!r} is not in the projection for "
    )
    assert result.stderr.endswith("\n")


@pytest.mark.parametrize(
    ("change", "error", "message"),
    [
        (
            lambda s: {"subtask": s.subtask.model_copy(update={"card_id": "card-9"})},
            cli.UnknownCardError,
            "card 'card-9' is not in run",
        ),
        (
            lambda s: {"phase": s.phase.model_copy(update={"name": "gone"})},
            cli.UnknownPhaseError,
            "card 'card-1' has no phase 'gone' any more",
        ),
        (
            lambda s: {"attempt": s.attempt.model_copy(update={"n": 9})},
            cli.UnknownAttemptError,
            "phase 'implement' of card 'card-1' has no attempt 9 any more",
        ),
        (
            lambda s: {"attempt": None, "step_attempt": None},
            cli.CliError,
            "neither an attempt row nor a step attempt",
        ),
    ],
    ids=["card", "phase", "attempt", "no-attempt-key"],
)
def test_logs_end_status_refuses_what_it_can_no_longer_find(
    projection, change, error, message
):
    """Each lookup step of the re-lookup refuses rather than guessing, so the
    stream reports it on stderr at exit 3 instead of crashing."""
    _record_for_logs(projection, LOGS_RUN_ID, implement_status="started")
    selection = cli.select_logs(LOGS_RUN_ID, "card-1", repo_dir=projection)
    assert cli.logs_end_status(selection, repo_dir=projection) is None

    stale = dataclasses.replace(selection, **change(selection))

    with pytest.raises(error, match=re.escape(message)):
        cli.logs_end_status(stale, repo_dir=projection)


@pytest.mark.parametrize(
    ("extra", "n", "status"),
    [
        (["--phase", "explore", "--attempt", "1"], 1, "gate_failed"),
        (["--phase", "explore"], 2, "ok"),
    ],
    ids=["older-attempt", "latest-attempt"],
)
def test_logs_follow_ends_with_the_followed_attempts_own_status(
    projection, monkeypatch, extra, n, status
):
    """Review Focus 1: the re-lookup is keyed by the followed attempt's
    number, never by "the latest attempt of the phase"."""
    _record_for_logs(projection, LOGS_RUN_ID)
    stdout = paths.attempt_path(LOGS_RUN_ID, "card-1", "explore", n) / "stdout.log"

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection, *extra))

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _logs_hello_line(stdout),
        {"offset": 0, "text": f"stdout of explore.{n}\n"},
        _end_line(status),
    ]


def test_logs_follow_closed_pipe_on_end_exits_zero_quietly(projection, monkeypatch):
    """Review Focus 2: the reader goes away exactly as `end` is written."""
    _record_for_logs(projection, LOGS_RUN_ID)  # gate_failed
    real_emit = cli._emit_stream_line

    def emit_until_end(obj) -> None:
        if obj.get("event") == "end":
            raise BrokenPipeError(32, "Broken pipe")
        real_emit(obj)

    monkeypatch.setattr(cli, "_emit_stream_line", emit_until_end)

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]


def test_logs_follow_ctrl_c_during_drain_exits_zero(projection, monkeypatch):
    """Review Focus 3: a terminal attempt drains without sleeping, so Ctrl-C
    lands in a read, not a sleep; it is still exit 0 and silent."""
    _record_for_logs(projection, LOGS_RUN_ID)  # gate_failed
    real_read = cli._read_log_bytes
    reads: list[int] = []

    def read_then_interrupt(path, offset):
        reads.append(offset)
        if len(reads) == 2:
            raise KeyboardInterrupt
        return real_read(path, offset)

    monkeypatch.setattr(cli, "_read_log_bytes", read_then_interrupt)

    result = _logs_follow_no_wait(monkeypatch, *_logs_args(projection))

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _logs_hello_line(_implement_stdout()),
        {"offset": 0, "text": "stdout of implement.1\n"},
    ]


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
) -> store_checkpoints.Checkpoint:
    """A hand-built checkpoint row of `TASK`: `current` is the turn in flight, `queue` the turns after it."""
    return store_checkpoints.Checkpoint(
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
    opened: store_writer.Store,
    card_id: str,
    reason: str,
    *,
    digest: str | None = None,
    queue: tuple[str, ...] = ("implement",),
    minute: int = 0,
) -> store_checkpoints.Checkpoint:
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
    opened = store_writer.Store.open(projection, "20260926T090000Z-02890d5d")
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


def _crash_pygents(
    project: Path,
    cards: dict[str, str],
    phase: str,
    *,
    commands: tuple[str, ...] = (),
    allow_no_verification: bool = False,
) -> str:
    """Drive a real pygents `run_card` until it is killed inside `phase`, and name the run.

    `commands` and `allow_no_verification` are what the run starts with: the
    seed, and so the suite a resumed walk keeps."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    with pytest.raises(_Killed):
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            commands=commands,
            allow_no_verification=allow_no_verification,
            clock=lambda: CRASHED_AT,
            runner_factory=_resume_factory(crash_at=phase, crash_with=_Killed),
        )
    return run_id


def _checkpoint_rows(root: Path) -> list[tuple]:
    conn = sqlite3.connect(paths.db_path())
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
    conn = store_db.open_db(root)
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    found = cli.find_subtask(run, card_id)
    assert found is not None
    story, subtask = found
    opened = store_writer.Store.open(root, run_id)
    try:
        opened.record_subtask(story.card_id, subtask.model_copy(update={"status": "started"}))
    finally:
        opened.close()


def _plant_changed_digest(project: Path, run_id: str, card_id: str) -> None:
    """A newer copy of the newest checkpoint, saved under a digest `TASK` does not have."""
    opened = store_writer.Store.open(cli.resolve_repo_dir(project), run_id)
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

    opened = store_writer.Store.open(root, run_id)
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


@pytest.mark.git
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


def _resumable_subtask(project: Path, run_id: str, card_id: str) -> models.SubtaskRun:
    """The subtask row `run_id` recorded for `card_id`: its branch and worktree."""
    conn = store_db.open_db(cli.resolve_repo_dir(project))
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    found = cli.find_subtask(run, card_id)
    assert found is not None
    return found[1]


def _newest_seq(project: Path, run_id: str, card_id: str) -> int:
    """The seq of `card_id`'s newest checkpoint in `run_id`: the one a resume reads."""
    return max(
        row[2]
        for row in _checkpoint_rows(cli.resolve_repo_dir(project))
        if row[0] == run_id and row[1] == card_id
    )


@pytest.mark.git
def test_a_pygents_resume_with_its_worktree_deleted_re_adds_it_and_resumes_at_implement(
    project, cards
):
    """Resume worktree re-ensure §3.2 case 2: the branch survives, so the
    worktree is added again, the checkpoint is kept and `resumed_from` still
    names its pending phase, with the re-added line in `data.warnings`."""
    run_id = _crash_pygents(project, cards, "implement")
    subtask = _resumable_subtask(project, run_id, cards["subtask"])
    seq = _newest_seq(project, run_id, cards["subtask"])
    shutil.rmtree(subtask.worktree_path)
    seen: list[str] = []

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory(seen))

    assert seen[0] == "implement"
    assert payload["status"] == "done"
    assert payload["resumed_from"] == "implement"
    assert (
        f"checkpoint #{seq} of run {run_id}: worktree {subtask.worktree_path} was missing"
        f" and was added again for branch '{subtask.branch}'; resuming at 'implement'"
    ) in payload["warnings"]


@pytest.mark.git
def test_a_pygents_resume_with_worktree_and_branch_gone_declines_the_checkpoint(
    project, cards
):
    """Resume worktree re-ensure §3.2 case 3 and §3.6: the branch is gone too,
    so the checkpoint is declined and the subtask is walked from its first
    phase. `resumed_from` is then `null`, not the checkpoint's `plan`."""
    run_id = _crash_pygents(project, cards, "plan")
    subtask = _resumable_subtask(project, run_id, cards["subtask"])
    seq = _newest_seq(project, run_id, cards["subtask"])
    root = cli.resolve_repo_dir(project)
    shutil.rmtree(subtask.worktree_path)
    # git refuses `branch -D` while the stale registration still claims the
    # branch, which is why the branch is deleted through `update-ref` here.
    _git(root, "update-ref", "-d", f"refs/heads/{subtask.branch}")

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["resumed_from"] is None
    assert payload["status"] == "done"
    assert (
        f"checkpoint #{seq} of run {run_id} was not resumed (worktree"
        f" {subtask.worktree_path} is missing and branch '{subtask.branch}' no longer"
        " exists); starting from the first phase"
    ) in payload["warnings"]
    assert set(payload) == RESUME_KEYS


@pytest.mark.git
def test_a_pygents_resume_marks_the_orphan_attempt_harness_error(project, cards):
    """Spec test 5: the orphan attempt is marked `harness_error`."""
    run_id = _crash_pygents(project, cards, "plan")

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["discarded_attempts"] == [{"phase": "plan", "n": 1}]
    plan = [row for row in _attempt_rows(project) if row[3] == "plan"]
    assert [(row[4], row[5]) for row in plan] == [(1, "harness_error"), (2, "ok")]
    assert [row for row in _attempt_rows(project) if row[5] == "started"] == []


@pytest.mark.git
def test_a_task_resume_posts_its_runs_pending_comments_before_the_walk_goes_on(
    project, cards
):
    """Board-comments B7 (card 65ed3c70): a row the killed life queued but
    never posted is on the card before the resumed walk's first phase."""
    run_id = _crash_pygents(project, cards, "plan")
    key = f"{run_id}/{cards['subtask']}/escalated:an-earlier-life"
    body = f"am · escalated · run {run_id}\nphase: plan\nam-key: {key}"
    opened = store_writer.Store.open(cli.resolve_repo_dir(project), run_id)
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


@pytest.mark.git
def test_a_task_resume_whose_start_flush_fails_warns_and_still_walks(
    project, cards, monkeypatch
):
    """Board-comments B7/B8 (card 65ed3c70): a board that refuses the resumed
    run's leftover row is a warning leading the payload's, never a refusal."""
    run_id = _crash_pygents(project, cards, "plan")
    key = f"{run_id}/{cards['subtask']}/escalated:an-earlier-life"
    opened = store_writer.Store.open(cli.resolve_repo_dir(project), run_id)
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


KEPT = "verification: kept from checkpoint"


def _kept_warnings(payload: dict[str, Any]) -> list[str]:
    return [w for w in payload["warnings"] if w.startswith(KEPT)]


@pytest.mark.git
def test_a_task_resume_with_a_different_verify_announces_the_kept_suite(project, cards):
    """Card 5b19aa93, spec T4: one warning naming the kept suite, not the passed one."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))

    payload = cli.resume_run(
        run_id,
        repo_dir=project,
        commands=["echo instrumented"],
        runner_factory=_resume_factory(),
    )

    assert _kept_warnings(payload) == ["verification: kept from checkpoint: ['true']"]
    assert not [w for w in payload["warnings"] if "echo instrumented" in w]
    assert set(payload) == RESUME_KEYS


@pytest.mark.git
def test_a_task_resume_with_the_same_verify_says_nothing_of_the_suite(project, cards):
    """Spec T5: the passed suite is the kept one, so there is nothing to announce."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))

    payload = cli.resume_run(
        run_id, repo_dir=project, commands=["true"], runner_factory=_resume_factory()
    )

    assert _kept_warnings(payload) == []
    assert set(payload) == RESUME_KEYS


@pytest.mark.git
def test_a_task_resume_with_no_verify_says_nothing_of_the_suite(project, cards):
    """Spec T6: `--verify` omitted (`commands=()`) is not a different suite."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert _kept_warnings(payload) == []
    assert set(payload) == RESUME_KEYS


@pytest.mark.git
@pytest.mark.parametrize(
    "passed",
    [
        pytest.param(["echo kept", "true"], id="reordered"),
        pytest.param(["true", "echo kept", "true"], id="one-extra"),
        pytest.param(["true ", "echo kept"], id="trailing-space"),
    ],
)
def test_a_task_resume_with_a_reordered_or_respaced_verify_announces_the_kept_suite(
    project, cards, passed
):
    """Spec T7 and Review Focus 2: equality is exact and order-sensitive."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true", "echo kept"))

    payload = cli.resume_run(
        run_id, repo_dir=project, commands=passed, runner_factory=_resume_factory()
    )

    assert _kept_warnings(payload) == [
        "verification: kept from checkpoint: ['true', 'echo kept']"
    ]
    assert set(payload) == RESUME_KEYS


@pytest.mark.git
def test_an_opted_out_task_resume_with_a_verify_announces_an_empty_kept_suite(
    project, cards
):
    """Spec T8 and Review Focus 3: an opted-out run kept `[]`, and says so."""
    run_id = _crash_pygents(project, cards, "plan", allow_no_verification=True)

    payload = cli.resume_run(
        run_id,
        repo_dir=project,
        commands=["uv run pytest"],
        runner_factory=_resume_factory(),
    )

    assert _kept_warnings(payload) == ["verification: kept from checkpoint: []"]
    assert set(payload) == RESUME_KEYS


@pytest.mark.git
def test_the_kept_suite_warning_follows_the_flush_warnings(project, cards, monkeypatch):
    """Spec T9 and Review Focus 4: B7's flush warnings lead, the kept suite is next."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))
    key = f"{run_id}/{cards['subtask']}/escalated:an-earlier-life"
    opened = store_writer.Store.open(cli.resolve_repo_dir(project), run_id)
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

    payload = cli.resume_run(
        run_id,
        repo_dir=project,
        commands=["echo instrumented"],
        runner_factory=_resume_factory(),
    )

    assert set(payload) == RESUME_KEYS
    flush_at = [
        i for i, w in enumerate(payload["warnings"]) if f"board comment {key} " in w
    ]
    kept_at = [i for i, w in enumerate(payload["warnings"]) if w.startswith(KEPT)]
    assert flush_at == [0], payload["warnings"]
    assert kept_at == [1], payload["warnings"]
    assert payload["warnings"][1] == "verification: kept from checkpoint: ['true']"


@pytest.mark.git
def test_a_refused_task_resume_with_a_differing_verify_says_nothing_of_the_suite(
    project, cards, monkeypatch
):
    """Review Focus 5: a refusal is today's error envelope, exit 3, nothing written."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))
    _plant_changed_digest(project, run_id, cards["subtask"])
    before = _resume_state(project)
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = runner.invoke(
        cli.app,
        ["resume", run_id, "--repo-dir", str(project), "--verify", "echo instrumented"],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CheckpointMismatchError"
    assert KEPT not in result.stdout
    assert _resume_state(project) == before


def _plant_recorded_suite(project: Path, run_id: str, verify: list[str]) -> None:
    """Re-record `run_id` with `verify` as its suite: what an earlier resume with
    a differing `--verify` leaves behind."""
    run = _loaded(project, run_id)
    opened = store_writer.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.record_run(
            run.model_copy(update={"config": run.config.model_copy(update={"verify": verify})})
        )
    finally:
        opened.close()


@pytest.mark.git
def test_a_task_resume_with_a_different_verify_writes_it_back_to_the_record(project, cards):
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))
    before = len(_run_upserts(run_id))

    payload = cli.resume_run(
        run_id,
        repo_dir=project,
        commands=["echo instrumented"],
        runner_factory=_resume_factory(),
    )

    assert _loaded(project, run_id).config.verify == ["echo instrumented"]
    written = _run_upserts(run_id)[before:]
    assert written
    assert all(line.payload["config"]["verify"] == ["echo instrumented"] for line in written)
    assert payload["warnings"][-1] == "verification: replaced in run record: ['true']"
    assert "verification: kept from checkpoint: ['true']" in payload["warnings"]


@pytest.mark.git
def test_a_task_resume_without_verify_after_a_replacement_says_nothing_of_the_suite(
    project, cards
):
    """Card 5b19aa93's T6 stays keyed on the flag: a record that differs from
    the checkpoint's pool is not announced when `--verify` is omitted."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))
    _plant_recorded_suite(project, run_id, ["echo instrumented"])

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert _kept_warnings(payload) == []
    assert not [w for w in payload["warnings"] if w.startswith(REPLACED)]
    assert _loaded(project, run_id).config.verify == ["echo instrumented"]


@pytest.mark.git
def test_a_refused_resume_with_a_differing_verify_leaves_the_record_unchanged(
    project, cards, monkeypatch
):
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))
    _plant_changed_digest(project, run_id, cards["subtask"])
    before = _resume_state(project)
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = runner.invoke(
        cli.app,
        ["resume", run_id, "--repo-dir", str(project), "--verify", "echo instrumented"],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout)["error"]["type"] == "CheckpointMismatchError"
    assert REPLACED not in result.stdout
    assert _resume_state(project) == before
    assert _loaded(project, run_id).config.verify == ["true"]


@pytest.mark.git
def test_a_task_resume_without_verify_hands_the_walk_the_recorded_suite(
    project, cards, monkeypatch
):
    """Review Focus 1: the walk gets the record's suite, so one that declines
    its checkpoint and starts afresh still verifies."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))
    _plant_recorded_suite(project, run_id, ["echo recorded"])
    real = cli.drive_subtask_async
    handed: list[tuple[list[str], bool]] = []

    def spy(**kwargs: Any) -> Any:
        handed.append((list(kwargs["commands"]), kwargs["allow_no_verification"]))
        return real(**kwargs)

    monkeypatch.setattr(cli, "drive_subtask_async", spy)

    cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert handed == [(["echo recorded"], False)]


def _run_upserts(run_id: str) -> list[Any]:
    """Every `run_upsert` node event of `run_id`, oldest first."""
    return [line for line in eventlines.run_lines(run_id) if line.event == "run_upsert"]


@pytest.mark.git
def test_a_card_runs_first_run_upsert_records_its_suite(project, cards):
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))

    first = _run_upserts(run_id)[0]

    assert first.payload["config"]["verify"] == ["true"]
    assert first.payload["config"]["allow_no_verification"] is False


@pytest.mark.git
def test_a_card_runs_first_run_upsert_records_its_opt_out(project, cards):
    run_id = _crash_pygents(project, cards, "plan", allow_no_verification=True)

    first = _run_upserts(run_id)[0]

    assert first.payload["config"]["verify"] == []
    assert first.payload["config"]["allow_no_verification"] is True


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_resume_launches_no_harness(project, cards, monkeypatch):
    """§14's adapter rule at the resume seam: the launcher is injected, so a
    resume that got as far as launching one has already failed."""
    run_id = _crash_pygents(project, cards, "plan")

    def forbidden(*args, **kwargs):
        raise AssertionError("resume launched a harness process")

    monkeypatch.setattr(cli.launcher, "get_launcher", lambda kind: forbidden)
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


def test_resume_help_says_the_recorded_suite_is_used():
    result = runner.invoke(cli.app, ["resume", "--help"])

    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.replace("│", " ").split())
    assert "recorded suite is used" in flat


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
    opened = store_writer.Store.open(cli.resolve_repo_dir(project), run_id)
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
    conn = sqlite3.connect(paths.db_path())
    try:
        return [row[0] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _loaded(project: Path, run_id: str) -> models.Run:
    conn = store_db.open_db(cli.resolve_repo_dir(project))
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    return run


def _record_milestone(
    root: Path,
    run_id: str,
    *,
    status: str,
    workflow: str = "milestone",
    verify: tuple[str, ...] = (),
    allow_no_verification: bool = False,
    launcher: models.Launcher = "direct",
    isolation_warning: str | None = None,
) -> None:
    opened = store_writer.Store.open(root, run_id)
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
                config=models.RunConfig(
                    verify=list(verify),
                    allow_no_verification=allow_no_verification,
                    launcher=launcher,
                    isolation_warning=isolation_warning,
                ),
            )
        )
    finally:
        opened.close()


@pytest.mark.brd
@pytest.mark.git
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


@pytest.mark.brd
@pytest.mark.git
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


@pytest.mark.brd
@pytest.mark.git
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
        return {"done": True, "run_id": run_id, "resumed": True, "warnings": []}

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

    assert payload == {
        "done": True,
        "run_id": run_id,
        "resumed": True,
        "warnings": ["verification: replaced in run record: []"],
    }
    assert calls == [
        (
            None,
            {
                "repo_dir": projection.resolve(),
                "commands": ["uv run pytest"],
                "allow_no_verification": True,
                "launcher": "direct",
                "isolation_warning": None,
                "runner_factory": factory,
                "resume_run_id": run_id,
            },
        )
    ]


REPLACED = "verification: replaced in run record"


def _replaced(payload: dict[str, Any]) -> list[str]:
    return [w for w in payload.get("warnings", []) if w.startswith(REPLACED)]


def _task_resume_spy(monkeypatch) -> list[tuple[models.Run, dict[str, Any]]]:
    """Patch `_resume_from_checkpoint` to record the run and kwargs it is handed."""
    seen: list[tuple[models.Run, dict[str, Any]]] = []

    def fake_resume(run, **kwargs):
        seen.append((run, kwargs))
        return {"status": "done", "warnings": []}

    monkeypatch.setattr(cli, "_resume_from_checkpoint", fake_resume)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))
    return seen


def _milestone_resume_spy(
    monkeypatch, returned: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Patch `orchestrate.run_milestone` to record its kwargs and return `returned`."""
    calls: list[dict[str, Any]] = []

    def fake_run_milestone(milestone, **kwargs):
        calls.append(kwargs)
        return {"done": True, "warnings": []} if returned is None else dict(returned)

    monkeypatch.setattr(orchestrate, "run_milestone", fake_run_milestone)
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    return calls


def test_a_task_resume_without_verify_drives_the_recorded_suite(projection, monkeypatch):
    run_id = "20260923T090000Z-cbe34d00"
    _record(
        projection,
        run_id,
        started_at=RECORDED_AT,
        status="started",
        verify=("uv run pytest",),
        allow_no_verification=True,
    )
    seen = _task_resume_spy(monkeypatch)

    payload = cli.resume_run(run_id, repo_dir=projection)

    [(run, kwargs)] = seen
    assert run.config.verify == ["uv run pytest"]
    assert run.config.allow_no_verification is True
    assert kwargs["allow_no_verification"] is True
    assert kwargs["commands"] == ()
    assert _replaced(payload) == []


def test_a_milestone_resume_without_verify_passes_the_recorded_suite(projection, monkeypatch):
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(
        projection,
        run_id,
        status="escalated",
        verify=("uv run pytest",),
        allow_no_verification=True,
    )
    calls = _milestone_resume_spy(monkeypatch)

    payload = cli.resume_run(run_id, repo_dir=projection)

    [kwargs] = calls
    assert kwargs["commands"] == ["uv run pytest"]
    assert kwargs["allow_no_verification"] is True
    assert kwargs["resume_run_id"] == run_id
    assert payload["warnings"] == []


def test_a_milestone_resume_with_a_different_verify_passes_it_and_warns_last(
    projection, monkeypatch
):
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="escalated", verify=("old",))
    calls = _milestone_resume_spy(
        monkeypatch, {"done": True, "warnings": ["an earlier warning"]}
    )

    payload = cli.resume_run(run_id, repo_dir=projection, commands=["new"])

    [kwargs] = calls
    assert kwargs["commands"] == ["new"]
    assert payload["warnings"] == [
        "an earlier warning",
        "verification: replaced in run record: ['old']",
    ]


@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_a_resume_with_the_recorded_verify_does_not_warn(projection, monkeypatch, workflow):
    run_id = "20260927T100000Z-cbe34d00"
    if workflow == "task":
        _record(
            projection, run_id, started_at=RECORDED_AT, status="started", verify=("uv run pytest",)
        )
        _task_resume_spy(monkeypatch)
    else:
        _record_milestone(projection, run_id, status="escalated", verify=("uv run pytest",))
        _milestone_resume_spy(monkeypatch)

    payload = cli.resume_run(run_id, repo_dir=projection, commands=["uv run pytest"])

    assert _replaced(payload) == []


def test_a_resume_of_a_run_recorded_without_verify_behaves_as_before(projection, monkeypatch):
    plain, passed = "20260923T090000Z-cbe34d00", "20260923T090000Z-cbe34d01"
    _record(projection, plain, started_at=RECORDED_AT, status="started")
    _record(projection, passed, started_at=RECORDED_AT, status="started")
    seen = _task_resume_spy(monkeypatch)

    quiet = cli.resume_run(plain, repo_dir=projection)
    loud = cli.resume_run(passed, repo_dir=projection, commands=["x"])

    (plain_run, plain_kwargs), (passed_run, passed_kwargs) = seen
    assert plain_run.config == models.RunConfig()
    assert (plain_kwargs["commands"], plain_kwargs["allow_no_verification"]) == ((), False)
    assert quiet["warnings"] == []
    assert passed_run.config.verify == ["x"]
    assert passed_kwargs["commands"] == ["x"]
    assert loud["warnings"] == ["verification: replaced in run record: []"]


def test_the_opt_out_flag_widens_a_recorded_false(projection, monkeypatch):
    widened, kept = "20260927T100000Z-cbe34d00", "20260927T100000Z-cbe34d01"
    _record_milestone(projection, widened, status="escalated", allow_no_verification=False)
    _record_milestone(projection, kept, status="escalated", allow_no_verification=True)
    calls = _milestone_resume_spy(monkeypatch)

    first = cli.resume_run(widened, repo_dir=projection, allow_no_verification=True)
    second = cli.resume_run(kept, repo_dir=projection)

    assert [kwargs["allow_no_verification"] for kwargs in calls] == [True, True]
    assert _replaced(first) == _replaced(second) == []


def test_an_empty_string_verify_replaces_the_record_verbatim(projection, monkeypatch):
    """Review Focus 2: `--verify ""` is a passed suite of one empty command."""
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="escalated", verify=("x",))
    calls = _milestone_resume_spy(monkeypatch)

    payload = cli.resume_run(run_id, repo_dir=projection, commands=[""])

    [kwargs] = calls
    assert kwargs["commands"] == [""]
    assert payload["warnings"] == ["verification: replaced in run record: ['x']"]


def test_a_refused_resume_with_a_differing_verify_keeps_the_record(projection, monkeypatch):
    """Review Focus 3: a canceled run is refused as before and nothing is written."""
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="canceled", verify=("old",))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))

    with pytest.raises(cli.NotResumableError):
        cli.resume_run(run_id, repo_dir=projection, commands=["new"])

    assert _loaded(projection, run_id).config.verify == ["old"]


def test_the_replacement_warning_creates_a_missing_warnings_key(projection, monkeypatch):
    """Review Focus 4: a payload with no `warnings` still gets the warning."""
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="escalated", verify=("old",))
    _milestone_resume_spy(monkeypatch, {"done": True})

    payload = cli.resume_run(run_id, repo_dir=projection, commands=["new"])

    assert payload == {
        "done": True,
        "warnings": ["verification: replaced in run record: ['old']"],
    }


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
# projection through `store_db.open_db`, which is exactly how a second
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

    Written over a second `open_db` connection inside `store_db.immediate`.
    Defaults to this process on this host with a heartbeat at the frozen
    clock: live by C2.
    """
    resolved = cli.resolve_repo_dir(root)
    conn = store_db.open_db(resolved)
    try:
        with store_db.immediate(conn):
            project_id = store_projects.resolve(conn, resolved, now=CONTROL_NOW)
            conn.execute(
                "INSERT INTO run_leases (project_id, run_id, token, pid, host,"
                " acquired_at, heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,"
                " pid=excluded.pid, host=excluded.host, acquired_at=excluded.acquired_at,"
                " heartbeat_at=excluded.heartbeat_at, accepting=excluded.accepting",
                (
                    project_id,
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
                    "INSERT INTO run_claims (project_id, key, run_id, token, claimed_at)"
                    " VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id, key) DO UPDATE SET"
                    " run_id=excluded.run_id, token=excluded.token,"
                    " claimed_at=excluded.claimed_at",
                    (project_id, key, run_id, token, heartbeat_at.isoformat()),
                )
    finally:
        conn.close()


OTHER_RUN_ID = "20260930T080000Z-a1b2c3d4"
"""Another run, driven by another `am` process, that holds a claim."""


def _claim_rows(root: Path) -> list[tuple[str, str, str]]:
    """Every `run_claims` row as `(key, run_id, token)`, in key order."""
    conn = store_db.open_db(cli.resolve_repo_dir(root))
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
    resolved = cli.resolve_repo_dir(root)
    conn = store_db.open_db(resolved)
    try:
        with store_db.immediate(conn):
            row = store_leases.add_control(
                conn,
                CONTROL_RUN_ID,
                project_id=store_projects.resolve(conn, resolved, now=CONTROL_NOW),
                lease=lease,
                command=command,
                requested_at=requested_at,
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
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        return [
            (row.lease, row.command)
            for row in store_leases.control_requests(conn, CONTROL_RUN_ID)
        ]
    finally:
        conn.close()


def _lease(root: Path) -> store_leases.LeaseRow | None:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        return store_leases.read_lease(conn, CONTROL_RUN_ID)
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
    assert models.canonical_status(status) in error["message"]
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


def test_request_control_writes_the_projects_id(projection):
    _plant_run(projection)
    _plant_lease(projection, heartbeat_at=_at(100))

    cli.request_control(CONTROL_RUN_ID, "pause", repo_dir=projection, clock=lambda: _at(110))

    conn = store_db.open_db(cli.resolve_repo_dir(projection))
    try:
        run_project = conn.execute(
            "SELECT project_id FROM runs WHERE id = ?", (CONTROL_RUN_ID,)
        ).fetchone()[0]
        control_projects = [
            row[0]
            for row in conn.execute(
                "SELECT project_id FROM run_controls WHERE run_id = ?", (CONTROL_RUN_ID,)
            )
        ]
        projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    finally:
        conn.close()

    assert control_projects == [run_project]
    assert projects == 1


def test_a_refused_request_control_creates_no_project_row(projection):
    """Review Focus 5: the project is resolved only after `_controllable_lease`,
    so a refusal leaves no `projects` row behind."""
    with pytest.raises(cli.UnknownRunError):
        cli.request_control(
            "no-such-run", "pause", repo_dir=projection, clock=lambda: CONTROL_NOW
        )

    conn = store_db.open_db(cli.resolve_repo_dir(projection))
    try:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    finally:
        conn.close()


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


def _control_requested_events(root: Path) -> list[store_events.EventRow]:
    """The run's committed `control_requested` events, in `seq` order."""
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        return [
            event
            for event in store_events.read(conn, run_id=CONTROL_RUN_ID)
            if event.kind == "control_requested"
        ]
    finally:
        conn.close()


def _run_project_id(root: Path) -> int:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        return conn.execute(
            "SELECT project_id FROM runs WHERE id = ?", (CONTROL_RUN_ID,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_a_recorded_pause_and_cancel_each_insert_one_control_requested_event(projection):
    _plant_run(projection)
    _plant_lease(projection)

    cli.request_control(CONTROL_RUN_ID, "pause", repo_dir=projection, clock=lambda: CONTROL_NOW)
    cli.request_control(CONTROL_RUN_ID, "cancel", repo_dir=projection, clock=lambda: _at(1))

    events = _control_requested_events(projection)
    project_id = _run_project_id(projection)
    assert [
        (event.project_id, event.source, event.schema)
        + (event.story_id, event.card_id, event.phase, event.attempt)
        for event in events
    ] == [(project_id, "live", 1, None, None, None, None)] * 2
    # `ts` is the request's clock, the same instant as `requested_at`.
    assert [event.ts for event in events] == [
        store_journal.ts_text(CONTROL_NOW),
        store_journal.ts_text(_at(1)),
    ]
    assert [event.payload for event in events] == [
        {
            "command": "pause",
            "lease": "life-2",
            "requested_at": CONTROL_NOW.isoformat(),
            "control_seq": 0,
        },
        {
            "command": "cancel",
            "lease": "life-2",
            "requested_at": _at(1).isoformat(),
            "control_seq": 1,
        },
    ]
    assert events[0].run_seq < events[1].run_seq


def test_a_no_op_request_inserts_no_control_requested_event(projection):
    _plant_run(projection)
    _plant_lease(projection)

    cli.request_control(CONTROL_RUN_ID, "pause", repo_dir=projection, clock=lambda: CONTROL_NOW)
    repeat = cli.request_control(
        CONTROL_RUN_ID, "pause", repo_dir=projection, clock=lambda: _at(5)
    )

    assert repeat["already_requested"] is True
    assert [event.payload["control_seq"] for event in _control_requested_events(projection)] == [0]


def _plant_refusal(root: Path, case: str) -> None:
    """The projection state each refused-request case starts from."""
    if case == "unknown-run":
        return
    if case == "not-started":
        _plant_run(root, status="stopped")
        _plant_lease(root)
    elif case == "dead-lease":
        _plant_run(root)
        _plant_lease(root, heartbeat_at=_at(-31))
    elif case == "window-closed":
        _plant_run(root)
        _plant_lease(root, accepting=False)
    elif case == "unknown-command":
        _plant_run(root)
        _plant_lease(root)


@pytest.mark.parametrize(
    ("case", "command", "error"),
    [
        ("unknown-run", "pause", cli.UnknownRunError),
        ("not-started", "pause", cli.NotRunningError),
        ("dead-lease", "cancel", cli.DeadRunError),
        ("window-closed", "cancel", cli.NotAcceptingError),
        ("unknown-command", "resume", ValueError),
    ],
    ids=["unknown-run", "not-started", "dead-lease", "window-closed", "unknown-command"],
)
def test_a_refused_request_inserts_no_control_requested_event(projection, case, command, error):
    _plant_refusal(projection, case)

    with pytest.raises(error):
        cli.request_control(
            CONTROL_RUN_ID, command, repo_dir=projection, clock=lambda: CONTROL_NOW
        )

    assert _control_requested_events(projection) == []
    assert _controls(projection) == []


def test_a_failed_control_requested_insert_rolls_the_request_back(projection, monkeypatch):
    _plant_run(projection)
    _plant_lease(projection)

    def failing(conn, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(cli.store_events, "insert", failing)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        cli.request_control(
            CONTROL_RUN_ID, "pause", repo_dir=projection, clock=lambda: CONTROL_NOW
        )

    assert _controls(projection) == []
    assert _control_requested_events(projection) == []


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
@pytest.mark.parametrize("status", ["cancelled", "canceled"], ids=["cancelled", "canceled"])
def test_resume_refuses_run_canceled_in_either_spelling(
    projection, monkeypatch, status, workflow, leased
):
    """Spec test 11 (C9), both workflows and both spellings. Review Focus: a
    canceled run that still holds a live lease is refused as canceled."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status=status, workflow=workflow)
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
            f"run {CONTROL_RUN_ID} was canceled;"
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
        ("cancel", "stopped", "canceled"),
        ("cancel", "escalated", "canceled"),
        ("cancel", "done", "canceled"),
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


def _card_lease(project: Path, run_id: str) -> store_leases.LeaseRow | None:
    """The run's lease row, read over a second connection as `am status` would."""
    conn = store_db.open_db(cli.resolve_repo_dir(project))
    try:
        return store_leases.read_lease(conn, run_id)
    finally:
        conn.close()


def _card_controls(project: Path, run_id: str) -> list[store_leases.ControlRow]:
    conn = store_db.open_db(cli.resolve_repo_dir(project))
    try:
        return store_leases.control_requests(conn, run_id)
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
    leases: list[store_leases.LeaseRow | None] | None = None,
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_a_cancelled_card_run_closes_the_run_and_resume_refuses_it(
    project, cards, control_applied
):
    """Spec test 3, plus Review Focus 3: the run is `canceled`, its story and
    subtask stay `stopped` as the park left them, `am resume` refuses it, and
    a pause sent afterwards is refused rather than queued."""
    seen: list[str] = []
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=seen
    )

    payload = _controlled_card_run(project, cards, factory)

    run_id = payload["run_id"]
    assert payload["status"] == "canceled", payload
    assert payload["failed_phase"] is None
    assert "validate_spec" not in seen
    assert _card_statuses(project, run_id) == {
        "run": "canceled",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _newest_checkpoint_reason(project, run_id) == "parked"
    assert _card_lease(project, run_id) is None

    resumed = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])
    assert resumed.exit_code == cli.EXIT_ERROR, resumed.output
    error = json.loads(resumed.stdout)["error"]
    assert error["type"] == "NotResumableError"
    assert "canceled" in error["message"]

    late = runner.invoke(cli.app, ["pause", run_id, "--repo-dir", str(project)])
    assert late.exit_code == cli.EXIT_ERROR, late.output
    assert json.loads(late.stdout)["error"]["type"] == "NotRunningError"
    assert [row.command for row in _card_controls(project, run_id)] == ["cancel"]


@pytest.mark.git
def test_card_cancel_journals_canceled(project, cards, control_applied):
    """A canceled `--card` run journals its final `run_upsert` with status
    `canceled`, never the legacy spelling."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[]
    )

    run_id = _controlled_card_run(project, cards, factory)["run_id"]

    raw = eventlines.run_line_texts(run_id)
    upserts = [line for line in raw if json.loads(line)["event"] == "run_upsert"]
    assert upserts, raw
    assert json.loads(upserts[-1])["payload"]["status"] == "canceled", upserts[-1]
    assert not [line for line in upserts if "cancelled" in line], upserts


@pytest.mark.git
def test_a_cancel_that_meets_an_escalation_closes_the_run_but_keeps_the_rows(
    project, cards, control_applied
):
    """Review Focus 1 at the function: C6 puts the cancel first for the run,
    while the story and subtask rows keep the escalation the walk ended in."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[], fail=True
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "canceled", payload
    assert _card_statuses(project, payload["run_id"]) == {
        "run": "canceled",
        "story": "escalated",
        "subtask": "escalated",
    }


@pytest.mark.parametrize(
    "command, fail, exit_code, status",
    [
        ("pause", False, 0, "stopped"),
        ("cancel", False, 0, "canceled"),
        ("cancel", True, 0, "canceled"),
        ("pause", True, cli.EXIT_ESCALATED, "escalated"),
    ],
    ids=["pause", "cancel", "cancel-over-escalation", "pause-keeps-escalation"],
)
@pytest.mark.git
def test_a_control_and_an_escalation_follow_c6_at_the_command(
    project, cards, control_applied, monkeypatch, command, fail, exit_code, status
):
    """Spec's exit codes and Review Focus 1-2: the command's mapping still keys
    off `escalated`, so `stopped` and `canceled` exit 0 with an ok envelope
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


@pytest.mark.git
def test_an_uncontrolled_card_run_holds_its_lease_then_releases_it(project, cards):
    """Spec tests 4 and 6: the lease is held, window open, while a phase runs;
    it is gone afterwards; the payload is today's, key for key."""
    leases: list[store_leases.LeaseRow | None] = []
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


@pytest.mark.git
def test_a_card_walk_that_raises_releases_its_lease(project, cards, monkeypatch):
    """Spec test 4 and the error path: the lease was held when the walk blew
    up, it is released on the way out, the error surfaces as is, and no final
    status row is written."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    held: list[store_leases.LeaseRow | None] = []

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


@pytest.mark.git
def test_a_resumed_card_run_paused_mid_phase_parks_and_releases_its_lease(
    project, cards, control_applied
):
    """Spec test 5: the resumed walk starts at `plan`, is paused there, finishes
    `plan`, parks before the next phase, and gives its lease back."""
    run_id = _crash_pygents(project, cards, "plan")
    seen: list[str] = []
    leases: list[store_leases.LeaseRow | None] = []
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


@pytest.mark.git
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

    assert payload["status"] == "canceled", payload
    assert _card_statuses(project, run_id) == {
        "run": "canceled",
        "story": "stopped",
        "subtask": "stopped",
    }
    assert _card_lease(project, run_id) is None

    again = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])
    assert again.exit_code == cli.EXIT_ERROR, again.output
    assert json.loads(again.stdout)["error"]["type"] == "NotResumableError"


@pytest.mark.git
def test_a_resumed_card_walk_that_raises_releases_its_lease(project, cards, monkeypatch):
    """Error path on resume: the lease was held when the walk blew up and is
    released on the way out; the run stays `started` as it does today."""
    run_id = _crash_pygents(project, cards, "plan")
    held: list[store_leases.LeaseRow | None] = []

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


def test_refuse_claimed_for_an_unseen_project_creates_no_project_row(projection, monkeypatch):
    """B5: the preflight looks the project up and never creates it. With no row
    there is no claim of this project's to conflict with, even when another
    project in the same file holds the same key under a live lease."""
    _freeze_clock(monkeypatch)
    root = cli.resolve_repo_dir(projection)
    conn = store_db.open_db(root)
    try:
        with store_db.immediate(conn):
            elsewhere = store_projects.resolve(conn, root.parent / "elsewhere", now=CONTROL_NOW)
            store_leases.take_lease(
                conn,
                OTHER_RUN_ID,
                project_id=elsewhere,
                token="other-life",
                pid=os.getpid(),
                host=HERE,
                now=CONTROL_NOW,
                is_live=lambda row: False,
                claims=["card:card-1"],
            )
    finally:
        conn.close()

    cli.refuse_claimed(root, ["card:card-1"])

    conn = store_db.open_db(root)
    try:
        assert store_projects.lookup(conn, root) is None
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    finally:
        conn.close()


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
    opened = store_writer.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
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
    opened = store_writer.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
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
    opened = store_writer.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
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
    root = cli.resolve_repo_dir(project)
    conn = store_db.open_db(root)
    try:
        return [
            summary.id
            for summary in store_queries.list_runs(
                conn, project_id=store_projects.lookup(conn, root)
            )
        ]
    finally:
        conn.close()


@pytest.mark.git
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


@pytest.mark.git
def test_a_claim_taken_after_the_preflight_is_refused_leaving_no_run_dir(
    project, cards, monkeypatch
):
    """Review Focus 1: the preflight passed, then another run claimed the card
    before `take_lease`; nothing is left under `runs/`."""
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
    assert _run_dirs() == []
    assert _recorded_run_ids(project) == []
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@pytest.mark.git
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


@pytest.mark.git
def test_the_lease_is_bound_before_the_first_journal_line(project, cards, monkeypatch):
    real_record_run = store_writer.Store.record_run
    first: list[tuple[str | None, list[str]]] = []

    def spy(self, run):
        if not first:
            token = self._token
            held = (
                []
                if token is None
                else [
                    claim.key
                    for claim in store_leases.held_claims(self.connection, self.run_id, token)
                ]
            )
            first.append((token, held))
        return real_record_run(self, run)

    monkeypatch.setattr(store_writer.Store, "record_run", spy)

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
@pytest.mark.git
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


@pytest.mark.git
def test_a_lease_lost_mid_walk_is_an_envelope_at_exit_3(project, cards, monkeypatch):
    """The first phase lets a second process take the run's lease over; the
    next fenced write raises `LeaseLostError`, which the command renders."""
    taken: list[str] = []

    def thief_factory(*, store, run_id, story_id, card_id):
        inner = fake_runner()

        def runner(phase, context, rendered):
            if phase.name == "explore" and not taken:
                thief = store_writer.Store.open(cli.resolve_repo_dir(project), run_id)
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


def _only_run_id(project: Path) -> str:
    root = cli.resolve_repo_dir(project)
    conn = store_db.open_db(root)
    try:
        run_id = store_queries.latest_run_id(
            conn, project_id=store_projects.lookup(conn, root)
        )
    finally:
        conn.close()
    assert run_id is not None
    return run_id


@pytest.mark.git
def test_a_busy_store_mid_walk_is_a_store_busy_envelope_at_exit_3(
    project, cards, monkeypatch
):
    """The `worktree` step's `done` row stays busy through its retry budget: the
    walk stops at that write and records no escalation, and the window close
    and lease release, busy too, do not replace the error the envelope names."""
    real_record_phase = store_writer.Store.record_phase

    def record_phase(self, story_id, card_id, phase):
        if (phase.name, phase.status) == ("worktree", "done"):
            raise store_db.StoreBusyError("record_phase", 5, 10.0)
        return real_record_phase(self, story_id, card_id, phase)

    def busy_close_window(self, token):
        raise store_db.StoreBusyError("close_window", 5, 10.0)

    def busy_release_lease(self, token):
        raise store_db.StoreBusyError("release_lease", 5, 10.0)

    monkeypatch.setattr(store_writer.Store, "record_phase", record_phase)
    monkeypatch.setattr(store_writer.Store, "close_window", busy_close_window)
    monkeypatch.setattr(store_writer.Store, "release_lease", busy_release_lease)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "StoreBusyError"
    assert envelope["error"]["message"].startswith("record_phase: ")
    run_id = _only_run_id(project)
    assert _loaded(project, run_id).status == "started"
    # The busy release left the lease row behind to go stale; the claims went.
    assert _card_lease(project, run_id) is not None
    assert _claim_rows(project) == []
    conn = store_db.open_db(cli.resolve_repo_dir(project))
    try:
        phases = conn.execute(
            "SELECT name, status FROM phases WHERE run_id = ? ORDER BY position", (run_id,)
        ).fetchall()
        subtasks = [
            row[0]
            for row in conn.execute(
                "SELECT status FROM subtasks WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        reasons = [
            row[0]
            for row in conn.execute(
                "SELECT reason FROM checkpoints WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        ]
    finally:
        conn.close()
    assert [tuple(row) for row in phases] == [("worktree", "started")]
    assert "escalated" not in subtasks
    assert reasons == ["turn"]


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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
    monkeypatch.setattr(store_writer.Store, "open", _Forbidden("Store.open"))

    with pytest.raises(cli.ClaimedError) as caught:
        _resume_card_run(project, run_id, _Forbidden("runner_factory"))

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@pytest.mark.git
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
    before = (_attempt_rows(project), _checkpoint_rows(project), eventlines.run_lines(run_id))

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
        eventlines.run_lines(run_id),
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


@pytest.mark.git
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

    monkeypatch.setattr(store_writer.Store, "take_lease", forbidden("Store.take_lease"))
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
    conn = store_db.open_db(cli.resolve_repo_dir(project))
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_a_cancelled_card_run_leaves_one_cancelled_comment_naming_run_card(
    project, cards, control_applied
):
    """Spec cli test 3, cancel half: where it stopped, its branch, and the
    `am run --card` relaunch."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[]
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "canceled", payload
    run_id, card = payload["run_id"], cards["subtask"]
    assert _card_comment_keys(project, card) == [f"{run_id}/{card}/cancelled"]
    (comment,) = board.comment_list(card, repo_dir=project)
    assert comment.body.startswith(f"am · canceled · run {run_id}\n")
    assert "stopped before: validate_spec" in comment.body
    assert f"branch: {payload['branch']}" in comment.body
    assert f"relaunch: `am run --card {card}`" in comment.body
    assert board.comment_list(cards["story"], repo_dir=project) == []
    assert board.comment_list(cards["milestone"], repo_dir=project) == []


@pytest.mark.git
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


@pytest.mark.git
def test_a_card_cancel_that_meets_an_escalation_comments_the_escalation(
    project, cards, control_applied
):
    """Review Focus 5: the run is `canceled` (C6) but the walk escalated;
    the comment follows `summary.status`, so it is the escalation."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[], fail=True
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "canceled", payload
    run_id, card = payload["run_id"], cards["subtask"]
    (key,) = _card_comment_keys(project, card)
    assert key.startswith(f"{run_id}/{card}/escalated:")
    assert "phase: spec" in board.comment_list(card, repo_dir=project)[0].body


@pytest.mark.git
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
# Default (unit) tier per design §14: no git, no brd, no harness. Rows are inserted with the `am events` helpers (`_insert_events`, `_insert_rows`).

def _watch_runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "xdg" / "agent-manager" / "runs"


def _watch(*args: str):
    return runner.invoke(cli.app, ["watch", *args])


def _watch_event(row: store_events.EventRow) -> dict[str, Any]:
    """The line `am watch` prints for `row`: its journal line, JSON-mode, plus
    `gseq`, the row's global `seq`."""
    return {**store_events.journal_line(row).model_dump(mode="json"), "gseq": row.seq}


def _watch_data(*argv: str) -> dict[str, Any]:
    """`am watch ARGV`, one-shot: exit 0, an ok envelope; its data."""
    result = _watch(*argv)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    return envelope["data"]


def _watch_refusal(*argv: str) -> dict[str, Any]:
    """`am watch ARGV`, one-shot: exit 3, one error envelope; its error."""
    result = _watch(*argv)
    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    return envelope["error"]


def _project_count() -> int:
    """How many `projects` rows the machine-wide database holds (0 without one)."""
    conn = store_db.open_db_for_reading(Path("."))
    try:
        return conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    finally:
        conn.close()


WATCH_PROJECT_WITH_RUN = "--project cannot be combined with RUN"
WATCH_PROJECT_WITH_ALL = "--project cannot be combined with --all or --all-projects"
WATCH_EXACTLY_ONE = (
    "give exactly one of RUN_ID, --all (or --all-projects) or --project:"
    " `am watch RUN_ID` reads one run, `am watch --all` every run of every"
    " project, `am watch --project PATH` one repository's runs"
)


def test_watch_single_run_lines_are_journal_lines_plus_gseq(projection):
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_B, "run_upsert"),
        (EVENTS_RUN_A, "story_upsert"),
        (EVENTS_RUN_B, "story_upsert"),
        (EVENTS_RUN_A, "lease_acquired"),
    )
    a_rows = [row for row in rows if row.run_id == EVENTS_RUN_A]

    data = _watch_data(EVENTS_RUN_A)

    assert data == {"events": [_watch_event(row) for row in a_rows]}
    lines = data["events"]
    assert [line["gseq"] for line in lines] == [rows[0].seq, rows[2].seq, rows[4].seq]
    assert [line["seq"] for line in lines] == [1, 2, 3]
    assert [line["event"] for line in lines] == [
        "run_upsert",
        "story_upsert",
        "lease_acquired",
    ]
    for line in lines:
        assert set(line) == EVENT_LINE_KEYS

    pretty = _watch(EVENTS_RUN_A, "--pretty")
    assert pretty.exit_code == 0, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == {"ok": True, "data": data}


def test_watch_since_filters_each_runs_own_seq(projection):
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),  # A seq 1
        (EVENTS_RUN_B, "run_upsert"),  # B seq 1
        (EVENTS_RUN_A, "story_upsert"),  # A seq 2
        (EVENTS_RUN_B, "story_upsert"),  # B seq 2
        (EVENTS_RUN_A, "subtask_upsert"),  # A seq 3
        (EVENTS_RUN_B, "subtask_upsert"),  # B seq 3
        (EVENTS_RUN_A, "phase_upsert"),  # A seq 4
    )

    one = _watch_data(EVENTS_RUN_A, "--since", "2")
    assert one == {"events": [_watch_event(rows[4]), _watch_event(rows[6])]}
    assert [line["seq"] for line in one["events"]] == [3, 4]

    every = _watch_data("--all", "--since", "2")
    assert every == {"events": [_watch_event(row) for row in rows[4:]]}
    assert [(line["run_id"], line["seq"]) for line in every["events"]] == [
        (EVENTS_RUN_A, 3),
        (EVENTS_RUN_B, 3),
        (EVENTS_RUN_A, 4),
    ]

    assert _watch_data(EVENTS_RUN_A, "--since", "99") == {"events": []}
    assert _watch_data("--all", "--since", "99") == {"events": []}


def test_watch_refuses_a_negative_since(projection):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))

    assert _watch_refusal(EVENTS_RUN_A, "--since", "-1") == {
        "type": "CliError",
        "message": "--since must be 0 or more, got -1",
    }


def test_watch_unknown_run_refuses_and_creates_nothing(projection, tmp_path):
    unknown = {"type": "UnknownRunError", "message": EVENTS_UNKNOWN_MESSAGE}

    assert _watch_refusal("no-such-run") == unknown
    assert not paths.db_path().exists()
    assert not _watch_runs_dir(tmp_path).exists()

    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))
    assert _watch_refusal("no-such-run") == unknown
    assert not (_watch_runs_dir(tmp_path) / "no-such-run").exists()


def test_watch_known_run_with_no_events_is_empty(projection):
    """`events` is append-only, so the run's rows are removed behind its
    trigger's back; the `runs` row stays."""
    _record(projection, SNAPSHOT_RUN_ID, started_at=RECORDED_AT, with_phases=False)
    _insert_events(projection, (EVENTS_RUN_B, "run_upsert"))
    _drop_events(projection, SNAPSHOT_RUN_ID)
    conn = store_db.open_db_for_reading(Path("."))
    try:
        assert store_events.has_run(conn, SNAPSHOT_RUN_ID) is False
        assert store_queries.load_run(conn, SNAPSHOT_RUN_ID) is not None
    finally:
        conn.close()

    assert _watch_data(SNAPSHOT_RUN_ID) == {"events": []}


def test_watch_run_known_only_by_lease_events_is_listed(projection):
    """Review Focus 4: lease and claim events can precede the `run_upsert`."""
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "lease_acquired"),
        (EVENTS_RUN_A, "claim_conflict"),
    )

    assert _watch_data(EVENTS_RUN_A) == {"events": [_watch_event(row) for row in rows]}


def test_events_watch_and_export_serve_a_run_with_no_journal_file(projection, tmp_path):
    """3.1.1 B3: a run recorded after the live journal was retired has events
    and no journal file; `events`, `watch` and `export` read `am.db` only."""
    _record(projection, EVENTS_RUN_A, started_at=RECORDED_AT, with_phases=False)
    assert not eventlines.journal_file(EVENTS_RUN_A).exists()
    reader = store_db.open_reader(paths.db_path())
    try:
        rows = store_events.read(reader, run_id=EVENTS_RUN_A)
    finally:
        reader.close()
    assert [row.kind for row in rows] == ["run_upsert", "story_upsert", "subtask_upsert"]
    out = tmp_path / "export.jsonl"

    listed = _events([EVENTS_RUN_A])["events"]
    watched = _watch_data(EVENTS_RUN_A)
    exported = runner.invoke(cli.app, ["export", EVENTS_RUN_A, "--out", str(out)])

    assert listed == [_watch_event(row) for row in rows]
    assert watched == {"events": [_watch_event(row) for row in rows]}
    assert exported.exit_code == 0, exported.output
    assert out.read_text(encoding="utf-8") == "".join(export.line(row) + "\n" for row in rows)
    assert not eventlines.journal_file(EVENTS_RUN_A).exists()


@pytest.mark.parametrize("run_id", ["../escape", "a/b", ".", "..", ""])
def test_watch_path_like_run_ids_are_unknown_runs(projection, tmp_path, run_id):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))
    # A journal one level above runs/: reading it would be a CorruptJournalError.
    escape = tmp_path / "xdg" / "agent-manager" / "escape"
    escape.mkdir(parents=True)
    (escape / store_journal.JOURNAL_NAME).write_text("not json\n", encoding="utf-8")

    error = _watch_refusal(run_id)

    assert error["type"] == "UnknownRunError"
    assert f"run {run_id!r} is not in the projection" in error["message"]


def test_watch_all_and_all_projects_read_every_project_in_gseq_order(escalation_repos):
    root, other = escalation_repos
    # B (in `other`) first, so the order is gseq, not run id.
    rows = _insert_rows(
        (other, EVENTS_RUN_B, "run_upsert", {"n": 0}),
        (root, EVENTS_RUN_A, "run_upsert", {"n": 1}),
        (other, EVENTS_RUN_B, "story_upsert", {"n": 2}),
        (root, EVENTS_RUN_A, "story_upsert", {"n": 3}),
    )
    assert len({row.project_id for row in rows}) == 2
    expected = {"events": [_watch_event(row) for row in rows]}

    for argv in (["--all"], ["--all-projects"], ["--all", "--all-projects"]):
        assert _watch_data(*argv) == expected, argv

    gseqs = [line["gseq"] for line in expected["events"]]
    assert gseqs == sorted(gseqs)
    assert [line["run_id"] for line in expected["events"]] == [
        EVENTS_RUN_B,
        EVENTS_RUN_A,
        EVENTS_RUN_B,
        EVENTS_RUN_A,
    ]


def test_watch_project_reads_only_that_projects_runs(escalation_repos, monkeypatch):
    root, other = escalation_repos
    rows = _insert_rows(
        (other, EVENTS_RUN_B, "run_upsert", {"n": 0}),
        (root, EVENTS_RUN_A, "run_upsert", {"n": 1}),
        (other, EVENTS_RUN_B, "story_upsert", {"n": 2}),
        (root, EVENTS_RUN_A, "story_upsert", {"n": 3}),
    )
    root_lines = [_watch_event(row) for row in rows if row.run_id == EVENTS_RUN_A]
    monkeypatch.setenv("HOME", str(root.parent))

    for spelling in (str(root), f"~/{root.name}", str(other / ".." / root.name)):
        assert _watch_data("--project", spelling) == {"events": root_lines}, spelling
    assert _watch_data("--project", str(other), "--since", "1") == {
        "events": [_watch_event(rows[2])]
    }

    deleted = root.parent / "deleted-repo"
    assert _watch_data("--project", str(deleted)) == {"events": []}
    assert _project_count() == 2


def test_watch_project_matches_relative_and_symlinked_spellings(
    escalation_repos, monkeypatch
):
    """Review Focus 2: a relative path, a symlink to the root, and a file."""
    root, other = escalation_repos
    rows = _insert_rows(
        (root, EVENTS_RUN_A, "run_upsert", {}),
        (other, EVENTS_RUN_B, "run_upsert", {}),
    )
    link = root.parent / "link-to-recorded"
    link.symlink_to(root, target_is_directory=True)
    a_file = root / "README.md"
    a_file.write_text("not a repository\n", encoding="utf-8")
    monkeypatch.chdir(root.parent)
    expected = {"events": [_watch_event(rows[0])]}

    assert _watch_data("--project", root.name) == expected
    assert _watch_data("--project", str(link)) == expected
    assert _watch_data("--project", str(a_file)) == {"events": []}
    assert _project_count() == 2


def test_watch_all_with_no_database_returns_empty_and_creates_nothing(
    projection, tmp_path
):
    for argv in (["--all"], ["--all-projects"], ["--project", str(projection)]):
        assert _watch_data(*argv) == {"events": []}, argv

    assert not paths.db_path().exists()
    assert not _watch_runs_dir(tmp_path).exists()


def test_watch_reads_past_a_writer_holding_a_write_transaction(projection):
    """Review Focus 3: an uncommitted insert neither blocks the read nor shows."""
    rows = _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))
    writer, project_id = _open_event_writer(projection)
    try:
        writer.execute("BEGIN IMMEDIATE")
        store_events.insert(
            writer,
            project_id=project_id,
            run_id=EVENTS_RUN_A,
            ts=EVENT_TS,
            kind="story_upsert",
            payload={},
            source="live",
        )

        assert _watch_data("--all") == {"events": [_watch_event(rows[0])]}
        assert _watch_data(EVENTS_RUN_A) == {"events": [_watch_event(rows[0])]}
    finally:
        writer.rollback()
        writer.close()


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["no-such-run", "--project", "."], WATCH_PROJECT_WITH_RUN),
        (["no-such-run", "--project", ".", "--all"], WATCH_PROJECT_WITH_RUN),
        (["no-such-run", "--project", ".", "--since", "-1"], WATCH_PROJECT_WITH_RUN),
        (["--project", ".", "--all"], WATCH_PROJECT_WITH_ALL),
        (["--project", ".", "--all-projects"], WATCH_PROJECT_WITH_ALL),
        (["--project", ".", "--all", "--since", "-1"], WATCH_PROJECT_WITH_ALL),
        (["run-a", "--all-projects"], WATCH_EXACTLY_ONE),
        (["run-a", "--all"], WATCH_EXACTLY_ONE),
        ([], WATCH_EXACTLY_ONE),
        (["--since", "-1"], WATCH_EXACTLY_ONE),
        (["--since", "3"], WATCH_EXACTLY_ONE),
    ],
)
def test_watch_selector_refusals(projection, monkeypatch, argv, message):
    """The first failing check wins, and every one comes before the database
    is opened, with or without --follow (no hello, no poll)."""
    _refuse_to_open_the_db(monkeypatch)

    assert _watch_refusal(*argv) == {"type": "CliError", "message": message}

    followed, sleeps = _watch_follow(monkeypatch, *argv)
    assert followed.exit_code == cli.EXIT_ERROR, followed.output
    assert sleeps == []
    lines = followed.stdout.splitlines()
    assert len(lines) == 1, followed.stdout
    envelope = json.loads(lines[0])
    assert envelope["ok"] is False
    assert envelope["error"] == {"type": "CliError", "message": message}


# ── am watch --follow (card cba3e48f) ──────────────────────────────────────
#
# Default (unit) tier per design §14, like the one-shot tests above: no git,
# no brd, no harness. Polling is driven by replacing `cli._watch_sleep` and
# bounding `cli.WATCH_MAX_POLLS`, so each poll sees exactly what the test wrote
# before it, with no wall-clock wait and no signal.


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


WATCH_HELLO_KEYS = {"event", "schema", "am", "head", "cursor_reset", "store_id", "runs_dir"}


def _hello(
    tmp_path: Path, *, head: int, store_id: str | None, cursor_reset: bool = False
) -> dict[str, Any]:
    """The hello of a stream that started at machine-wide `head` on the
    database `store_id` names (`None`: no `am.db` at start)."""
    return {
        "event": "watch",
        "schema": 2,
        "am": agent_manager.__version__,
        "head": head,
        "cursor_reset": cursor_reset,
        "store_id": store_id,
        "runs_dir": str(_watch_runs_dir(tmp_path)),
    }


def test_watch_hello_is_schema_2(projection, tmp_path, monkeypatch):
    hello = cli._watch_hello(head=7, cursor_reset=True, store_id="abc")
    assert hello == {
        "event": "watch",
        "schema": 2,
        "am": agent_manager.__version__,
        "head": 7,
        "cursor_reset": True,
        "store_id": "abc",
        "runs_dir": str(_watch_runs_dir(tmp_path)),
    }
    assert type(hello["schema"]) is int
    _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_A, "story_upsert"),
        (EVENTS_RUN_B, "run_upsert"),
    )
    store_id = _store_id(projection)
    assert isinstance(store_id, str) and store_id

    for argv in (
        [EVENTS_RUN_A],
        [EVENTS_RUN_A, "--since", "1"],
        ["--all"],
        ["--all-projects"],
        ["--project", str(projection)],
        [EVENTS_RUN_A, "--from-now"],
        ["--all", "--from-now"],
        ["--project", str(projection), "--from-now"],
    ):
        result, _ = _watch_follow(monkeypatch, *argv)
        assert result.exit_code == 0, (argv, result.output)
        lines = _stream(result)
        first = lines[0]
        assert set(first) == WATCH_HELLO_KEYS, argv
        assert type(first["schema"]) is int, argv
        assert type(first["head"]) is int, argv
        assert first["cursor_reset"] is False, argv
        assert first == _hello(tmp_path, head=3, store_id=store_id), argv
        # One hello per stream; every other line is an event with a gseq.
        assert [line for line in lines if "gseq" not in line] == [first], argv


def test_logs_hello_stays_schema_1():
    hello = cli._logs_hello(Path("x"), 0)
    assert hello["schema"] == 1
    assert hello == _logs_hello_line(Path("x"))


def test_watch_schema_2_replays_legacy_cancelled_unchanged(projection, tmp_path, monkeypatch):
    # As an `am` from before the spelling switch recorded it.
    rows = _insert_rows(
        (projection, "run-old", "run_upsert", {"run_id": "run-old", "status": "cancelled"}),
        (projection, "run-old", "phase_upsert", {"status": "started"}),
    )

    result, _ = _watch_follow(monkeypatch, "run-old")

    assert result.exit_code == 0, result.output
    lines = _stream(result)
    assert lines == [
        _hello(tmp_path, head=2, store_id=_store_id(projection)),
        *[_watch_event(row) for row in rows],
    ]
    assert lines[0]["schema"] == 2
    assert lines[1]["payload"] == {"run_id": "run-old", "status": "cancelled"}


def test_watch_follow_hello_line_shape(projection, tmp_path, monkeypatch):
    rows = _insert_events(
        projection,
        *[
            (EVENTS_RUN_A, kind)
            for kind in ("run_upsert", "story_upsert", "subtask_upsert", "phase_upsert")
        ],
    )

    result, sleeps = _watch_follow(monkeypatch, EVENTS_RUN_A, "--since", "2")

    assert result.exit_code == 0, result.output
    assert sleeps == []
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path, head=4, store_id=_store_id(projection))
    assert lines[1:] == [_watch_event(row) for row in rows[2:]]
    assert [line["seq"] for line in lines[1:]] == [3, 4]
    # Bare lines: no envelope, and compact, one object per line.
    assert all("ok" not in line for line in lines)
    assert result.stdout.endswith("\n")
    assert all(": " not in text for text in result.stdout.splitlines())
    assert result.stderr == ""

    # `--pretty` only shapes a refusal: the stream is byte-for-byte the same.
    pretty, _ = _watch_follow(monkeypatch, EVENTS_RUN_A, "--since", "2", "--pretty")
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == result.stdout


def test_watch_follow_refusal_prints_envelope_and_no_stream(projection, tmp_path, monkeypatch):
    result, sleeps = _watch_follow(monkeypatch, "no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert sleeps == []
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "UnknownRunError",
        "message": EVENTS_UNKNOWN_MESSAGE,
    }
    assert "event" not in envelope
    assert not _watch_runs_dir(tmp_path).exists()
    assert not paths.db_path().exists()

    # `--pretty` still indents a refusal, and it is still the only output.
    pretty, _ = _watch_follow(monkeypatch, "no-such-run", "--pretty")
    assert pretty.exit_code == cli.EXIT_ERROR, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == envelope

    # Every other refusal the one-shot form makes is made before the stream too.
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))
    for argv, kind in (
        (["../escape"], "UnknownRunError"),
        ([EVENTS_RUN_A, "--since", "-1"], "CliError"),
        (["--all", EVENTS_RUN_A], "CliError"),
        ([], "CliError"),
        ([EVENTS_RUN_A, "--project", str(projection)], "CliError"),
        (["--project", str(projection), "--all-projects"], "CliError"),
    ):
        refused, sleeps = _watch_follow(monkeypatch, *argv)
        assert refused.exit_code == cli.EXIT_ERROR, (argv, refused.output)
        assert sleeps == [], argv
        refusal_lines = refused.stdout.splitlines()
        assert len(refusal_lines) == 1, (argv, refused.stdout)
        refusal = json.loads(refusal_lines[0])
        assert refusal["ok"] is False, argv
        assert refusal["error"]["type"] == kind, argv


def _spy_on_event_reads(monkeypatch) -> list[int]:
    """Record the `after_seq` of every `store_events.read` call, then read as
    before; the list fills as `am watch` polls."""
    original = store_events.read
    seen: list[int] = []

    def spy(conn, **kwargs):
        seen.append(kwargs.get("after_seq", 0))
        return original(conn, **kwargs)

    monkeypatch.setattr(store_events, "read", spy)
    return seen


def test_watch_follow_observes_an_event_inserted_after_start(
    projection, tmp_path, monkeypatch
):
    backlog = _insert_events(
        projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert")
    )
    inserted: list[store_events.EventRow] = []

    def record_third() -> None:
        inserted.extend(_insert_events(projection, (EVENTS_RUN_A, "subtask_upsert")))

    # Poll 1 sees seq 3; poll 2 sees nothing new, so seq 3 must not repeat.
    result, sleeps = _watch_follow(
        monkeypatch, EVENTS_RUN_A, actions=[record_third, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert cli.WATCH_POLL_SECONDS == 0.25
    assert sleeps == [cli.WATCH_POLL_SECONDS, cli.WATCH_POLL_SECONDS]
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path, head=2, store_id=_store_id(projection))
    assert lines[1:] == [_watch_event(row) for row in backlog + inserted]
    assert [line["seq"] for line in lines[1:]] == [1, 2, 3]
    assert [line["gseq"] for line in lines[1:]] == [row.seq for row in backlog + inserted]


def test_watch_follow_one_cursor_across_runs(projection, tmp_path, monkeypatch):
    written = _insert_events(projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_B, "run_upsert"))

    def interleave() -> None:
        written.extend(
            _insert_events(
                projection,
                (EVENTS_RUN_B, "story_upsert"),
                (EVENTS_RUN_A, "story_upsert"),
                (EVENTS_RUN_B, "subtask_upsert"),
            )
        )

    def one_more_of_a() -> None:
        written.extend(_insert_events(projection, (EVENTS_RUN_A, "subtask_upsert")))

    result, sleeps = _watch_follow(
        monkeypatch,
        "--all",
        actions=[interleave, lambda: None, one_more_of_a, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert len(sleeps) == 4
    lines = _stream(result)
    assert lines[0] == _hello(tmp_path, head=2, store_id=_store_id(projection))
    # Every row exactly once, in gseq order, whichever run and poll it came in.
    assert lines[1:] == [_watch_event(row) for row in written]
    gseqs = [line["gseq"] for line in lines[1:]]
    assert gseqs == sorted(set(gseqs))
    for run_id in (EVENTS_RUN_A, EVENTS_RUN_B):
        assert [line["seq"] for line in lines[1:] if line["run_id"] == run_id] == [1, 2, 3]


def test_watch_follow_since_filtered_rows_advance_the_cursor(
    projection, tmp_path, monkeypatch
):
    """Review Focus 1: rows `--since` filters out still move the cursor, so
    no poll reads them again."""
    rows = _insert_events(
        projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert")
    )

    def poll_one() -> None:
        rows.extend(
            _insert_events(
                projection, (EVENTS_RUN_A, "subtask_upsert"), (EVENTS_RUN_B, "run_upsert")
            )
        )

    def poll_two() -> None:
        rows.extend(_insert_events(projection, (EVENTS_RUN_A, "phase_upsert")))

    reads = _spy_on_event_reads(monkeypatch)
    result, _ = _watch_follow(
        monkeypatch,
        EVENTS_RUN_A,
        "--since",
        "2",
        actions=[poll_one, poll_two, lambda: None],
    )

    assert result.exit_code == 0, result.output
    a_rows = [row for row in rows if row.run_id == EVENTS_RUN_A]
    assert _stream(result) == [
        _hello(tmp_path, head=2, store_id=_store_id(projection)),
        _watch_event(a_rows[2]),
        _watch_event(a_rows[3]),
    ]
    # Backlog from 0, then each poll above the last gseq the one before read.
    assert reads == [0, a_rows[1].seq, a_rows[2].seq, a_rows[3].seq]

    # A --since above every seq: nothing is emitted, and the cursor still moves.
    reads.clear()

    def poll_three() -> None:
        rows.extend(_insert_events(projection, (EVENTS_RUN_A, "attempt_upsert")))

    quiet_head = rows[-1].seq
    quiet, _ = _watch_follow(
        monkeypatch, EVENTS_RUN_A, "--since", "99", actions=[poll_three, lambda: None]
    )

    assert quiet.exit_code == 0, quiet.output
    assert quiet_head == 5
    assert _stream(quiet) == [_hello(tmp_path, head=quiet_head, store_id=_store_id(projection))]
    assert reads == [0, a_rows[3].seq, rows[-1].seq]


def test_watch_follow_all_picks_up_a_database_created_later(
    projection, tmp_path, monkeypatch
):
    # Nothing to watch yet: the hello line alone, and nothing created.
    idle, _ = _watch_follow(monkeypatch, "--all", actions=[lambda: None, lambda: None])
    assert idle.exit_code == 0, idle.output
    assert _stream(idle) == [_hello(tmp_path, head=0, store_id=None)]
    assert not paths.db_path().exists()

    created: list[store_events.EventRow] = []

    def create() -> None:
        created.extend(
            _insert_events(
                projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert")
            )
        )

    result, _ = _watch_follow(monkeypatch, "--all", actions=[create, lambda: None])

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _hello(tmp_path, head=0, store_id=None),
        *[_watch_event(row) for row in created],
    ]


def test_watch_follow_project_picks_up_a_project_that_appears_later(
    escalation_repos, tmp_path, monkeypatch
):
    root, other = escalation_repos
    _insert_rows((root, EVENTS_RUN_A, "run_upsert", {}))
    later: list[store_events.EventRow] = []

    def root_only() -> None:
        _insert_rows((root, EVENTS_RUN_A, "story_upsert", {}))

    def other_starts() -> None:
        later.extend(
            _insert_rows(
                (other, EVENTS_RUN_B, "run_upsert", {}),
                (root, EVENTS_RUN_A, "subtask_upsert", {}),
                (other, EVENTS_RUN_B, "story_upsert", {}),
            )
        )

    result, _ = _watch_follow(
        monkeypatch,
        "--project",
        str(other),
        actions=[root_only, other_starts, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _hello(tmp_path, head=1, store_id=_store_id(root)),
        _watch_event(later[0]),
        _watch_event(later[2]),
    ]
    assert _project_count() == 2


def test_watch_follow_mid_stream_error_ends_with_stderr_line(
    projection, tmp_path, monkeypatch
):
    backlog = _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))
    inserted: list[store_events.EventRow] = []

    def record_second() -> None:
        inserted.extend(_insert_events(projection, (EVENTS_RUN_A, "story_upsert")))

    def record_unreadable() -> None:
        _plant_raw_payload(projection, "subtask_upsert", "{not json", run_id=EVENTS_RUN_A)

    result, sleeps = _watch_follow(
        monkeypatch,
        EVENTS_RUN_A,
        actions=[record_second, record_unreadable, lambda: None],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert len(sleeps) == 2  # the stream ended on the unreadable poll
    assert _stream(result) == [
        _hello(tmp_path, head=1, store_id=_store_id(projection)),
        *[_watch_event(row) for row in backlog + inserted],
    ]
    message = result.stderr.strip()
    assert "\n" not in message
    assert message.startswith("am watch: ")


def test_watch_follow_ctrl_c_exits_zero_quietly(projection, tmp_path, monkeypatch):
    backlog = _insert_events(
        projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert")
    )

    def press_ctrl_c() -> None:
        raise KeyboardInterrupt

    result, _ = _watch_follow(monkeypatch, EVENTS_RUN_A, actions=[press_ctrl_c])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [
        _hello(tmp_path, head=2, store_id=_store_id(projection)),
        *[_watch_event(row) for row in backlog],
    ]


def test_watch_follow_closed_pipe_exits_zero_quietly(projection, tmp_path, monkeypatch):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert"))
    real_emit = cli._emit_stream_line
    emitted: list[Any] = []

    def emit_into_a_closed_pipe(obj) -> None:
        emitted.append(obj)
        if len(emitted) == 2:  # the reader went away after the hello line
            raise BrokenPipeError(32, "Broken pipe")
        real_emit(obj)

    monkeypatch.setattr(cli, "_emit_stream_line", emit_into_a_closed_pipe)

    result, _ = _watch_follow(monkeypatch, EVENTS_RUN_A, actions=[lambda: None])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert _stream(result) == [_hello(tmp_path, head=2, store_id=_store_id(projection))]


def test_watch_follow_closed_pipe_points_stdout_at_devnull(monkeypatch):
    # The interpreter flushes stdout once more at exit; once the reader has
    # gone, that flush must land on /dev/null, not on the dead pipe.
    read_end, write_end = os.pipe()
    os.close(read_end)
    with os.fdopen(write_end, "w") as dead_pipe:
        monkeypatch.setattr(sys, "stdout", dead_pipe)
        cli._silence_stdout()
        target = os.fstat(dead_pipe.fileno())
        devnull = os.stat(os.devnull)
        assert (target.st_dev, target.st_ino) == (devnull.st_dev, devnull.st_ino)
        dead_pipe.write("after the reader left\n")
        dead_pipe.flush()  # would raise BrokenPipeError on the pipe


def test_watch_follow_hello_on_an_empty_machine(projection, tmp_path, monkeypatch):
    """Review Focus 4 and 5: no `am.db`, so head 0 and a JSON null store_id,
    and the start read creates nothing."""
    result, sleeps = _watch_follow(monkeypatch, "--all", actions=[lambda: None])

    assert result.exit_code == 0, result.output
    assert sleeps == [cli.WATCH_POLL_SECONDS]
    assert result.stderr == ""
    assert '"store_id":null' in result.stdout.splitlines()[0]
    assert _stream(result) == [_hello(tmp_path, head=0, store_id=None)]
    hello = _stream(result)[0]
    assert hello["head"] == 0
    assert hello["cursor_reset"] is False
    assert hello["store_id"] is None
    assert not paths.db_path().exists()


def test_watch_follow_from_now_hello_head_is_where_the_stream_starts(
    projection, tmp_path, monkeypatch
):
    """Review Focus 2: head is read once, and the hello's head is exactly
    where --from-now starts the cursor."""
    _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_A, "story_upsert"),
        (EVENTS_RUN_A, "subtask_upsert"),
    )
    store_id = _store_id(projection)
    original_head = store_events.head
    head_reads: list[int] = []

    def counting_head(conn) -> int:
        value = original_head(conn)
        head_reads.append(value)
        return value

    monkeypatch.setattr(store_events, "head", counting_head)
    later: list[store_events.EventRow] = []

    def record_two() -> None:
        later.extend(
            _insert_events(projection, (EVENTS_RUN_A, "phase_upsert"), (EVENTS_RUN_B, "run_upsert"))
        )

    result, _ = _watch_follow(
        monkeypatch, "--all", "--from-now", actions=[record_two, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert [row.seq for row in later] == [4, 5]
    assert _stream(result) == [
        _hello(tmp_path, head=3, store_id=store_id),
        *[_watch_event(row) for row in later],
    ]
    assert head_reads == [3]


def test_watch_follow_start_read_failure_is_an_envelope(projection, tmp_path, monkeypatch):
    """Review Focus 3: the start read fails before any stream line, so it is
    a refusal: one envelope on stdout, nothing on stderr, no hello, no poll."""
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))
    raw = sqlite3.connect(paths.db_path())
    try:
        raw.execute(f"PRAGMA user_version = {store_db.SCHEMA_VERSION + 1}")
        raw.commit()
    finally:
        raw.close()

    for extra in ([], ["--pretty"]):
        result, sleeps = _watch_follow(monkeypatch, "--all", *extra, actions=[lambda: None])

        assert result.exit_code == cli.EXIT_ERROR, (extra, result.output)
        assert result.stderr == "", extra
        assert sleeps == [], extra
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is False, extra
        assert envelope["error"]["type"] == "StoreSchemaError", extra
        assert "event" not in envelope, extra
        assert '"watch"' not in result.stdout, extra
        if extra:
            assert "\n" in result.stdout.strip()
        else:
            assert len(result.stdout.splitlines()) == 1


def test_watch_one_shot_has_no_hello_fields(projection, tmp_path, monkeypatch):
    rows = _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))

    data = _watch_data("--all")

    assert set(data) == {"events"}
    assert data == {"events": [_watch_event(rows[0])]}


def test_watch_follow_silence_stdout_leaves_a_descriptorless_stdout_alone(monkeypatch):
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    cli._silence_stdout()
    assert sys.stdout is buffer


# ── am reset (card 736d6728) ─────────────────────────────────────────────────
#
# `am reset RUN_ID` closes a run nobody is driving: read-only refusals, then
# the run's own lease with no claims, one fenced `run_upsert` of `canceled`,
# and the lease released. Unit tier: the projection fixture and the store
# only, no subprocess. `cards` (card af52db54) lists each `(card_id,
# workflow)` the run checkpointed with the run a relaunch would continue it
# from (`open_in`), or `null`.

RESET_KEYS = {
    "run_id",
    "previous_status",
    "status",
    "already_canceled",
    "cards",
    "message",
}

RESET_MESSAGE = (
    f"run {CONTROL_RUN_ID} is canceled; `am resume {CONTROL_RUN_ID}` refuses it,"
    " and a relaunch starts its cards from their first phase"
)


def _invoke_reset(root: Path, run_id: str = CONTROL_RUN_ID, *extra: str):
    return runner.invoke(cli.app, ["reset", run_id, "--repo-dir", str(root), *extra])


def _journal_lines(run_id: str = CONTROL_RUN_ID) -> list[store_journal.JournalLine]:
    """`run_id`'s committed node events, ascending by `run_seq`."""
    return eventlines.run_lines(run_id)


def _recorded_status(root: Path, run_id: str = CONTROL_RUN_ID) -> str | None:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        return store_queries.run_status(conn, run_id)
    finally:
        conn.close()


# ── am watch --from-now (card db129e6a) ────────────────────────────────────
#
# Default (unit) tier per design §14, like the follow tests above: rows in the
# projection under tmp_path, polling driven by the fake `cli._watch_sleep`, no subprocess.


def _assert_one_cli_error(result, *needles: str) -> dict[str, Any]:
    """The refusal shape: exit 3, exactly one envelope line, ok false, CliError."""
    assert result.exit_code == cli.EXIT_ERROR, result.output
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    envelope = json.loads(lines[0])
    assert envelope["ok"] is False
    assert "event" not in envelope
    assert envelope["error"]["type"] == "CliError"
    for needle in needles:
        assert needle in envelope["error"]["message"], envelope
    return envelope


def test_watch_from_now_refused_with_since(projection, monkeypatch):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert"))

    for argv in (
        [EVENTS_RUN_A, "--from-now", "--since", "0"],
        [EVENTS_RUN_A, "--from-now", "--since", "2"],
        ["--all", "--from-now", "--since", "0"],
        ["--project", str(projection), "--from-now", "--since", "0"],
    ):
        result, sleeps = _watch_follow(monkeypatch, *argv)
        assert sleeps == [], argv
        _assert_one_cli_error(result, "--from-now", "--since", "exclusive")

    # `--pretty` still indents the refusal, and it is still the only output.
    pretty, sleeps = _watch_follow(
        monkeypatch, EVENTS_RUN_A, "--from-now", "--since", "0", "--pretty"
    )
    assert pretty.exit_code == cli.EXIT_ERROR, pretty.output
    assert sleeps == []
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout)["error"]["type"] == "CliError"

    # Breaking both new rules at once reports the --since conflict.
    both = _watch(EVENTS_RUN_A, "--from-now", "--since", "2")
    _assert_one_cli_error(both, "--from-now", "--since", "exclusive")


def test_watch_from_now_refused_without_follow(projection):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))

    for argv in (
        [EVENTS_RUN_A, "--from-now"],
        ["--all", "--from-now"],
        ["--project", str(projection), "--from-now"],
    ):
        result = runner.invoke(cli.app, ["watch", *argv])
        _assert_one_cli_error(result, "--from-now", "--follow")


def test_watch_from_now_keeps_existing_refusals_and_since_zero(
    projection, tmp_path, monkeypatch
):
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_A, "story_upsert"),
    )

    # RUN_ID handling is unchanged under --from-now: no hello line, no polls.
    for argv, kind in (
        (["no-such-run", "--from-now"], "UnknownRunError"),
        (["../escape", "--from-now"], "UnknownRunError"),
        ([EVENTS_RUN_A, "--all", "--from-now"], "CliError"),
        ([EVENTS_RUN_A, "--project", str(projection), "--from-now"], "CliError"),
    ):
        refused, sleeps = _watch_follow(monkeypatch, *argv)
        assert refused.exit_code == cli.EXIT_ERROR, (argv, refused.output)
        assert sleeps == [], argv
        refusal_lines = refused.stdout.splitlines()
        assert len(refusal_lines) == 1, (argv, refused.stdout)
        refusal = json.loads(refusal_lines[0])
        assert refusal["ok"] is False, argv
        assert refusal["error"]["type"] == kind, argv
    assert not (_watch_runs_dir(tmp_path) / "no-such-run").exists()

    # Without --from-now, `--since 0` is still the default and `--since -1`
    # is still refused by the old check.
    zero = _watch(EVENTS_RUN_A, "--since", "0")
    assert zero.exit_code == 0, zero.output
    assert json.loads(zero.stdout) == {
        "ok": True,
        "data": {"events": [_watch_event(row) for row in rows]},
    }
    negative = _watch(EVENTS_RUN_A, "--since", "-1")
    _assert_one_cli_error(negative, "--since must be 0 or more")


def test_watch_follow_from_now_skips_backlog(projection, tmp_path, monkeypatch):
    # The run's own backlog and another run's: neither is emitted.
    _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_B, "run_upsert"),
        (EVENTS_RUN_A, "story_upsert"),
    )

    # Nothing recorded: the hello line alone.
    idle, idle_sleeps = _watch_follow(
        monkeypatch, EVENTS_RUN_A, "--from-now", actions=[lambda: None]
    )
    assert idle.exit_code == 0, idle.output
    assert idle_sleeps == [cli.WATCH_POLL_SECONDS]
    assert _stream(idle) == [_hello(tmp_path, head=3, store_id=_store_id(projection))]

    inserted: list[store_events.EventRow] = []

    def record_third() -> None:
        inserted.extend(
            _insert_events(
                projection, (EVENTS_RUN_A, "subtask_upsert"), (EVENTS_RUN_B, "story_upsert")
            )
        )

    def record_fourth() -> None:
        inserted.extend(_insert_events(projection, (EVENTS_RUN_A, "phase_upsert")))

    # The last poll sees nothing new, so seq 4 must not repeat.
    result, sleeps = _watch_follow(
        monkeypatch,
        EVENTS_RUN_A,
        "--from-now",
        actions=[record_third, record_fourth, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert sleeps == [cli.WATCH_POLL_SECONDS] * 3
    lines = _stream(result)
    a_rows = [row for row in inserted if row.run_id == EVENTS_RUN_A]
    assert lines == [
        _hello(tmp_path, head=3, store_id=_store_id(projection)),
        *[_watch_event(row) for row in a_rows],
    ]
    assert [line["seq"] for line in lines[1:]] == [3, 4]
    assert result.stderr == ""


def test_watch_follow_all_from_now_emits_runs_created_later_in_full(
    projection, tmp_path, monkeypatch
):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert"))
    later: list[store_events.EventRow] = []

    def first_poll() -> None:
        later.extend(
            _insert_events(
                projection,
                (EVENTS_RUN_A, "subtask_upsert"),
                (EVENTS_RUN_B, "run_upsert"),  # B has no row at start
                (EVENTS_RUN_B, "story_upsert"),
            )
        )

    def second_poll() -> None:
        later.extend(
            _insert_events(
                projection, (EVENTS_RUN_B, "subtask_upsert"), (EVENTS_RUN_A, "phase_upsert")
            )
        )

    result, sleeps = _watch_follow(
        monkeypatch, "--all", "--from-now", actions=[first_poll, second_poll]
    )

    assert result.exit_code == 0, result.output
    assert len(sleeps) == 2
    lines = _stream(result)
    assert lines == [
        _hello(tmp_path, head=2, store_id=_store_id(projection)),
        *[_watch_event(row) for row in later],
    ]
    assert [line["seq"] for line in lines[1:] if line["run_id"] == EVENTS_RUN_B] == [1, 2, 3]
    assert result.stderr == ""


def test_watch_follow_from_now_on_an_empty_machine_emits_every_later_event(
    projection, tmp_path, monkeypatch
):
    """Review Focus 5: no `am.db` at start, so head is 0 and nothing is skipped."""
    later: list[store_events.EventRow] = []

    def first_poll() -> None:
        later.extend(
            _insert_events(
                projection, (EVENTS_RUN_A, "lease_acquired"), (EVENTS_RUN_A, "run_upsert")
            )
        )

    result, _ = _watch_follow(
        monkeypatch, "--all", "--from-now", actions=[first_poll, lambda: None]
    )

    assert result.exit_code == 0, result.output
    assert _stream(result) == [
        _hello(tmp_path, head=0, store_id=None),
        *[_watch_event(row) for row in later],
    ]


# ── am watch --since-seq (card b3818d9f) ───────────────────────────────────
#
# Default (unit) tier per design §14, like the --from-now tests above: rows in
# the projection under tmp_path, polling driven by the fake `cli._watch_sleep`,
# no subprocess.

WATCH_SINCE_SEQ_EXCLUSIVE = (
    "--from-now and --since-seq are exclusive: --from-now starts at head,"
    " --since-seq resumes after a cursor you already hold; give one of them"
)


def test_watch_since_seq_one_shot_resumes_after_gseq(projection, monkeypatch):
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_B, "run_upsert"),
        (EVENTS_RUN_A, "story_upsert"),
        (EVENTS_RUN_B, "story_upsert"),
    )
    assert [row.seq for row in rows] == [1, 2, 3, 4]

    # Only rows whose global gseq is above 2, ascending, for every selector.
    above_two = {"events": [_watch_event(row) for row in rows[2:]]}
    assert _watch_data("--all", "--since-seq", "2") == above_two
    assert _watch_data("--all-projects", "--since-seq", "2") == above_two
    assert _watch_data("--project", str(projection), "--since-seq", "2") == above_two
    assert _watch_data(EVENTS_RUN_A, "--since-seq", "2") == {
        "events": [_watch_event(rows[2])]
    }

    # `--since-seq 0` is the same as no flag.
    for selector in ([EVENTS_RUN_A], ["--all"], ["--project", str(projection)]):
        assert _watch_data(*selector, "--since-seq", "0") == _watch_data(*selector)

    # At head (4) and above it: no events, not an error.
    for value in ("4", "9"):
        assert _watch_data("--all", "--since-seq", value) == {"events": []}
        assert _watch_data(EVENTS_RUN_A, "--since-seq", value) == {"events": []}

    # `--pretty` indents the envelope, which is still filtered.
    pretty = _watch("--all", "--since-seq", "2", "--pretty")
    assert pretty.exit_code == 0, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == {"ok": True, "data": above_two}

    # An unknown RUN is still refused, one-shot and follow, before any hello.
    unknown = _watch("no-such-run", "--since-seq", "1")
    assert unknown.exit_code == cli.EXIT_ERROR, unknown.output
    assert len(unknown.stdout.splitlines()) == 1
    assert json.loads(unknown.stdout)["error"]["type"] == "UnknownRunError"
    followed, sleeps = _watch_follow(monkeypatch, "no-such-run", "--since-seq", "1")
    assert followed.exit_code == cli.EXIT_ERROR, followed.output
    assert sleeps == []
    assert len(followed.stdout.splitlines()) == 1
    assert json.loads(followed.stdout)["error"]["type"] == "UnknownRunError"


def test_watch_from_now_refused_with_since_seq(projection, monkeypatch):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert"))

    for selector in ([EVENTS_RUN_A], ["--all"], ["--project", str(projection)]):
        for value in ("0", "2"):
            argv = [*selector, "--from-now", "--since-seq", value]
            followed, sleeps = _watch_follow(monkeypatch, *argv)
            assert sleeps == [], argv
            envelope = _assert_one_cli_error(followed, "--from-now", "--since-seq", "exclusive")
            assert envelope["error"]["message"] == WATCH_SINCE_SEQ_EXCLUSIVE, argv
            one_shot = _watch(*argv)
            _assert_one_cli_error(one_shot, "--from-now", "--since-seq", "exclusive")

    # `--pretty` still indents the refusal, and it is still the only output.
    pretty, sleeps = _watch_follow(
        monkeypatch, "--all", "--from-now", "--since-seq", "0", "--pretty"
    )
    assert pretty.exit_code == cli.EXIT_ERROR, pretty.output
    assert sleeps == []
    assert "\n" in pretty.stdout.strip()
    envelope = json.loads(pretty.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CliError"
    assert envelope["error"]["message"] == WATCH_SINCE_SEQ_EXCLUSIVE


def test_watch_since_seq_negative_refused(projection, monkeypatch):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))

    for selector in ([EVENTS_RUN_A], ["--all"], ["--project", str(projection)]):
        one_shot = _watch(*selector, "--since-seq", "-1")
        _assert_one_cli_error(one_shot, "--since-seq must be 0 or more, got -1")
        followed, sleeps = _watch_follow(monkeypatch, *selector, "--since-seq", "-1")
        assert sleeps == [], selector
        _assert_one_cli_error(followed, "--since-seq must be 0 or more, got -1")

    # Not an integer: Typer's usage error (exit 2), like `--since abc`, no envelope.
    not_a_number = _watch("--all", "--since-seq", "abc")
    assert not_a_number.exit_code == 2, not_a_number.output
    assert '"ok"' not in not_a_number.stdout


def test_watch_since_seq_refusal_order(projection, monkeypatch):
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"))

    # --since below 0 is checked before --since-seq below 0.
    both_negative = _watch("--all", "--since", "-1", "--since-seq", "-1")
    _assert_one_cli_error(both_negative, "--since must be 0 or more, got -1")

    # --since-seq below 0 is checked before the --from-now conflicts.
    negative_from_now, sleeps = _watch_follow(
        monkeypatch, "--all", "--since-seq", "-1", "--from-now"
    )
    assert sleeps == []
    _assert_one_cli_error(negative_from_now, "--since-seq must be 0 or more, got -1")

    # --from-now with --since is reported before --from-now with --since-seq.
    since_conflict, sleeps = _watch_follow(
        monkeypatch, "--all", "--from-now", "--since", "1", "--since-seq", "1"
    )
    assert sleeps == []
    envelope = _assert_one_cli_error(since_conflict, "--from-now", "--since", "exclusive")
    assert "--since-seq" not in envelope["error"]["message"]

    # --from-now with --since-seq is reported before --from-now without --follow.
    no_follow = _watch("--all", "--from-now", "--since-seq", "1")
    envelope = _assert_one_cli_error(no_follow, "--from-now", "--since-seq", "exclusive")
    assert envelope["error"]["message"] == WATCH_SINCE_SEQ_EXCLUSIVE

    # The selector form comes first of all.
    form = _watch(EVENTS_RUN_A, "--all", "--since-seq", "-1")
    _assert_one_cli_error(form, "give exactly one of")


def test_watch_follow_since_seq_resumes_then_follows_without_repeat(
    projection, tmp_path, monkeypatch
):
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),
        (EVENTS_RUN_B, "run_upsert"),
        (EVENTS_RUN_A, "story_upsert"),
    )

    def record(*specs: tuple[str, str]):
        return lambda: rows.extend(_insert_events(projection, *specs))

    for argv, keep in (
        (["--all"], lambda row: True),
        ([EVENTS_RUN_A], lambda row: row.run_id == EVENTS_RUN_A),
        (["--project", str(projection)], lambda row: True),
    ):
        # The cursor sits two rows below head, so the backlog is not empty
        # and there is no reset.
        head = rows[-1].seq
        since_seq = head - 2
        result, sleeps = _watch_follow(
            monkeypatch,
            *argv,
            "--since-seq",
            str(since_seq),
            actions=[
                record((EVENTS_RUN_B, "subtask_upsert"), (EVENTS_RUN_A, "subtask_upsert")),
                record((EVENTS_RUN_A, "phase_upsert")),
                lambda: None,  # an idle last poll: nothing may repeat
            ],
        )

        assert result.exit_code == 0, (argv, result.output)
        assert sleeps == [cli.WATCH_POLL_SECONDS] * 3, argv
        lines = _stream(result)
        expected = [row for row in rows if row.seq > since_seq and keep(row)]
        assert lines == [
            _hello(tmp_path, head=head, store_id=_store_id(projection)),
            *[_watch_event(row) for row in expected],
        ], argv
        assert lines[0]["cursor_reset"] is False, argv
        gseqs = [line["gseq"] for line in lines[1:]]
        assert gseqs == sorted(set(gseqs)), argv
        assert all(gseq > since_seq for gseq in gseqs), argv
        if argv == [EVENTS_RUN_A]:
            assert {line["run_id"] for line in lines[1:]} == {EVENTS_RUN_A}
        assert result.stderr == ""


def test_watch_follow_since_seq_above_head_resets_the_cursor(
    projection, tmp_path, monkeypatch
):
    """Review Focus 1: a cursor equal to head is not a reset; one above it
    is, and is not an error: exit 0, nothing on stderr."""
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert"))
    head = 2
    store_id = _store_id(projection)

    # Nothing new written: the hello alone, with or without a reset.
    for selector in (["--all"], [EVENTS_RUN_A]):
        for since_seq, reset in ((head, False), (head + 5, True)):
            idle, sleeps = _watch_follow(
                monkeypatch, *selector, "--since-seq", str(since_seq), actions=[lambda: None]
            )
            assert idle.exit_code == 0, (selector, since_seq, idle.output)
            assert sleeps == [cli.WATCH_POLL_SECONDS]
            assert _stream(idle) == [
                _hello(tmp_path, head=head, store_id=store_id, cursor_reset=reset)
            ], (selector, since_seq)
            assert _stream(idle)[0]["cursor_reset"] is reset
            assert idle.stderr == ""

    later: list[store_events.EventRow] = []

    def record_seven() -> None:
        later.extend(_insert_events(projection, *[(EVENTS_RUN_A, "subtask_upsert")] * 7))

    result, _ = _watch_follow(
        monkeypatch,
        "--all",
        "--since-seq",
        str(head + 5),
        actions=[record_seven, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert [row.seq for row in later] == [3, 4, 5, 6, 7, 8, 9]
    # The reset moved the cursor to head: every later row, not only 8 and 9.
    assert _stream(result) == [
        _hello(tmp_path, head=head, store_id=store_id, cursor_reset=True),
        *[_watch_event(row) for row in later],
    ]


def test_watch_follow_since_seq_reset_respects_selector_and_since(
    projection, tmp_path, monkeypatch
):
    """A reset only moves where the cursor starts: RUN and --since still filter."""
    _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),  # gseq 1, run_seq 1
        (EVENTS_RUN_B, "run_upsert"),  # gseq 2
        (EVENTS_RUN_A, "story_upsert"),  # gseq 3, run_seq 2
    )
    head = 3
    store_id = _store_id(projection)
    later: list[store_events.EventRow] = []

    def record_later() -> None:
        later.extend(
            _insert_events(
                projection,
                (EVENTS_RUN_B, "story_upsert"),  # gseq 4: another run
                (EVENTS_RUN_A, "subtask_upsert"),  # gseq 5, run_seq 3: not above --since 3
                (EVENTS_RUN_A, "phase_upsert"),  # gseq 6, run_seq 4
            )
        )

    result, _ = _watch_follow(
        monkeypatch,
        EVENTS_RUN_A,
        "--since-seq",
        str(head + 10),
        "--since",
        "3",
        actions=[record_later, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    expected = [row for row in later if row.run_id == EVENTS_RUN_A and row.run_seq > 3]
    assert [(row.seq, row.run_seq) for row in expected] == [(6, 4)]
    assert _stream(result) == [
        _hello(tmp_path, head=head, store_id=store_id, cursor_reset=True),
        *[_watch_event(row) for row in expected],
    ]


def test_watch_follow_hello_store_id_tells_a_replaced_database(
    projection, tmp_path, monkeypatch
):
    """A replaced database whose head is at or above the consumer's cursor
    is no reset, but its hello names a different store_id."""
    _insert_events(projection, (EVENTS_RUN_A, "run_upsert"), (EVENTS_RUN_A, "story_upsert"))
    before, _ = _watch_follow(monkeypatch, "--all")
    assert before.exit_code == 0, before.output
    old = _stream(before)[0]
    assert old["head"] == 2
    old_store_id = old["store_id"]
    assert isinstance(old_store_id, str) and old_store_id

    db = paths.db_path()
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        path.unlink(missing_ok=True)
    rows = _insert_events(projection, *[(EVENTS_RUN_B, "run_upsert")] * 5)
    assert [row.seq for row in rows] == [1, 2, 3, 4, 5]
    new_store_id = _store_id(projection)
    assert isinstance(new_store_id, str) and new_store_id != old_store_id

    # The consumer holds (old_store_id, 2): 2 <= 5, so no reset...
    resumed, _ = _watch_follow(monkeypatch, "--all", "--since-seq", "2")

    assert resumed.exit_code == 0, resumed.output
    lines = _stream(resumed)
    # ...but the store_id tells it the cursor belongs to another database.
    assert lines[0] == _hello(tmp_path, head=5, store_id=new_store_id)
    assert lines[0]["cursor_reset"] is False
    assert lines[0]["store_id"] != old_store_id
    assert lines[1:] == [_watch_event(row) for row in rows[2:]]


def test_watch_since_seq_and_since_both_filter(projection, tmp_path, monkeypatch):
    rows = _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),  # gseq 1, run_seq 1
        (EVENTS_RUN_A, "story_upsert"),  # gseq 2, run_seq 2: run_seq > 1, gseq <= 2
        (EVENTS_RUN_B, "run_upsert"),  # gseq 3, run_seq 1: gseq > 2, run_seq <= 1
    )

    def record_rest() -> None:
        rows.extend(
            _insert_events(
                projection,
                (EVENTS_RUN_A, "subtask_upsert"),  # gseq 4, run_seq 3
                (EVENTS_RUN_B, "story_upsert"),  # gseq 5, run_seq 2
            )
        )

    # Follow: the backlog poll and the later poll both apply the two filters.
    result, _ = _watch_follow(
        monkeypatch,
        "--all",
        "--since", "1",
        "--since-seq", "2",
        actions=[record_rest, lambda: None],
    )

    assert result.exit_code == 0, result.output
    expected = [row for row in rows if row.seq > 2 and row.run_seq > 1]
    assert [(row.seq, row.run_seq) for row in expected] == [(4, 3), (5, 2)]
    assert _stream(result) == [
        _hello(tmp_path, head=3, store_id=_store_id(projection)),
        *[_watch_event(row) for row in expected],
    ]

    # One-shot: the same rows.
    assert _watch_data("--all", "--since", "1", "--since-seq", "2") == {
        "events": [_watch_event(row) for row in expected]
    }


def test_watch_follow_since_seq_project_created_later(projection, tmp_path, monkeypatch):
    """Review Focus 2 of card b3818d9f: a --project path with no row yet is
    looked up on every poll, and once it exists only its rows above the
    cursor are emitted. Here --since-seq 3 is above head 2, so the cursor is
    reset to head and both of the project's later rows are emitted."""
    _insert_events(
        projection,
        (EVENTS_RUN_A, "run_upsert"),  # gseq 1
        (EVENTS_RUN_A, "story_upsert"),  # gseq 2
    )
    later_root = tmp_path / "later"
    later_root.mkdir()
    later: list[store_events.EventRow] = []

    def create_project() -> None:
        later.extend(
            _insert_events(
                later_root,
                (EVENTS_RUN_B, "run_upsert"),  # gseq 3
                (EVENTS_RUN_B, "story_upsert"),  # gseq 4
            )
        )
        # Another project's row after it: never emitted.
        _insert_events(projection, (EVENTS_RUN_A, "subtask_upsert"))

    result, sleeps = _watch_follow(
        monkeypatch,
        "--project",
        str(later_root),
        "--since-seq",
        "3",
        actions=[lambda: None, create_project, lambda: None],
    )

    assert result.exit_code == 0, result.output
    assert len(sleeps) == 3
    assert [row.seq for row in later] == [3, 4]
    assert _stream(result) == [
        _hello(tmp_path, head=2, store_id=_store_id(projection), cursor_reset=True),
        _watch_event(later[0]),
        _watch_event(later[1]),
    ]
    assert result.stderr == ""


# ── run pre-flight, recorded stage and engine seam (card 5daa944e) ──────────
#
# Unit tier: the FakeBoard (`fake_board`) answers every board call, the repo
# dir is a plain directory, and no git, brd or claude process ever starts.

SEAM_AT = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
SEAM_LATER = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


def _seam_root(tmp_path: Path, monkeypatch) -> Path:
    """A plain repo directory (no git, no brd) with the data dir under tmp_path."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    return root.resolve()


def _seam_cards(fake_board) -> dict[str, str]:
    """The milestone -> story -> subtask chain `run --card` needs, on the FakeBoard."""
    milestone = fake_board.add_card("Milestone 1: walking skeleton")
    story = fake_board.add_card("The CLI: run, status, logs, resume", parent_id=milestone)
    subtask = fake_board.add_card("Add run --card end to end", parent_id=story)
    return {"milestone": milestone, "story": story, "subtask": subtask}


def _preflight(root: Path, card_id: str, at: datetime = SEAM_AT) -> Any:
    return cli.preflight_card(
        card_id, repo_dir=root, branch_prefix="m1", base_branch="main", clock=lambda: at
    )


def test_preflight_card_refuses_a_parentless_card_and_creates_no_run_directory(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    loose = fake_board.add_card("A card with no story")

    with pytest.raises(cli.ParentlessCardError):
        _preflight(root, loose)

    assert _run_dirs() == []


def test_preflight_card_refuses_a_card_another_live_run_claims(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )

    with pytest.raises(cli.ClaimedError) as caught:
        _preflight(root, cards["subtask"])

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert _run_dirs() == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]


def test_preflight_card_returns_the_run_it_would_record_and_writes_nothing(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    card = board.show(cards["subtask"], repo_dir=root)
    branch = dag.task_branch("m1", card)

    pre = _preflight(root, cards["subtask"])

    assert pre.root == root
    assert (pre.card.id, pre.parent.id) == (cards["subtask"], cards["story"])
    assert pre.run_id == cli.mint_run_id(cards["subtask"], SEAM_AT)
    assert pre.branch == branch
    assert pre.worktree == cli.worktree_for(root, branch)
    assert pre.base_branch == "main"
    assert pre.claims == [control.card_claim(cards["subtask"])]
    assert (pre.run_record.id, pre.run_record.status) == (pre.run_id, "started")
    assert (pre.run_record.workflow, pre.run_record.started_at) == (cli.WORKFLOW_NAME, SEAM_AT)
    assert (pre.run_record.base_branch, pre.run_record.branch_prefix) == ("main", "m1")
    assert (pre.story.card_id, pre.story.status, pre.story.tip_branch) == (
        cards["story"],
        "started",
        branch,
    )
    assert (pre.subtask.card_id, pre.subtask.status, pre.subtask.branch) == (
        cards["subtask"],
        "started",
        branch,
    )
    assert pre.subtask.worktree_path == cli.worktree_for(root, branch)
    assert _run_dirs() == []
    assert _recorded_run_ids(root) == []
    assert _claim_rows(root) == []
    assert fake_board.writes == []


def test_preflight_card_records_the_suite_and_the_opt_out(tmp_path, monkeypatch, fake_board):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)

    pre = cli.preflight_card(
        cards["subtask"],
        repo_dir=root,
        branch_prefix="m1",
        base_branch="main",
        commands=("a", "b"),
        allow_no_verification=True,
        clock=lambda: SEAM_AT,
    )
    default = _preflight(root, cards["subtask"])

    assert pre.run_record.config.verify == ["a", "b"]
    assert pre.run_record.config.allow_no_verification is True
    assert default.run_record.config.verify == []
    assert default.run_record.config.allow_no_verification is False


def test_preflight_card_records_the_launcher_and_its_warning(tmp_path, monkeypatch, fake_board):
    """A5 B5: omitted records `direct` and no warning, as before."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    common: dict[str, Any] = {
        "repo_dir": root,
        "branch_prefix": "m1",
        "base_branch": "main",
        "clock": lambda: SEAM_AT,
    }

    fallback = cli.preflight_card(
        cards["subtask"], launcher="direct", isolation_warning=FALLBACK, **common
    )
    isolated = cli.preflight_card(cards["subtask"], launcher="unshare", **common)
    default = cli.preflight_card(cards["subtask"], **common)

    assert fallback.run_record.config == models.RunConfig(
        launcher="direct", isolation_warning=FALLBACK
    )
    assert isolated.run_record.config == models.RunConfig(launcher="unshare")
    assert default.run_record.config == models.RunConfig()
    assert _run_dirs() == []


class _PreflightReached(Exception):
    """Raised by a patched pre-flight once it has captured its arguments."""


def test_run_card_and_its_detach_path_hand_the_suite_to_the_preflight(tmp_path, monkeypatch):
    seen: list[dict[str, Any]] = []

    def capture(card_id: str, **kwargs: Any) -> Any:
        seen.append(kwargs)
        raise _PreflightReached(card_id)

    monkeypatch.setattr(cli, "preflight_card", capture)
    common: dict[str, Any] = {
        "repo_dir": tmp_path,
        "branch_prefix": "m1",
        "base_branch": "main",
        "commands": ["a"],
        "allow_no_verification": True,
    }

    with pytest.raises(_PreflightReached):
        cli.run_card("c1", **common)
    with pytest.raises(_PreflightReached):
        cli.detach_card("c1", detacher=_Forbidden("detacher"), **common)

    assert [(kwargs["commands"], kwargs["allow_no_verification"]) for kwargs in seen] == [
        (["a"], True),
        (["a"], True),
    ]


def test_run_card_and_detach_card_hand_the_launcher_and_its_warning_to_preflight_card(
    tmp_path, monkeypatch
):
    seen: list[dict[str, Any]] = []

    def capture(card_id, **kwargs):
        seen.append(kwargs)
        raise _PreflightReached(card_id)

    monkeypatch.setattr(cli, "preflight_card", capture)
    common: dict[str, Any] = {
        "repo_dir": tmp_path,
        "branch_prefix": "m1",
        "base_branch": "main",
        "launcher": "direct",
        "isolation_warning": FALLBACK,
    }

    with pytest.raises(_PreflightReached):
        cli.run_card("c1", **common)
    with pytest.raises(_PreflightReached):
        cli.detach_card("c1", detacher=_Forbidden("detacher"), **common)

    assert [(kwargs["launcher"], kwargs["isolation_warning"]) for kwargs in seen] == [
        ("direct", FALLBACK),
        ("direct", FALLBACK),
    ]


def _close_snapshots(monkeypatch) -> list[tuple[int, int]]:
    """Patch `Store.close` to record `(claims, leases)` its run still holds as it closes.

    `(0, 0)` means the claims and the lease were released before the store
    closed. Counted over the closing store's own connection, before the real
    close runs.
    """
    seen: list[tuple[int, int]] = []
    real_close = store_writer.Store.close

    def close(self) -> None:
        conn = self.connection
        claims = conn.execute(
            "SELECT COUNT(*) FROM run_claims WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        leases = conn.execute(
            "SELECT COUNT(*) FROM run_leases WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        seen.append((claims, leases))
        real_close(self)

    monkeypatch.setattr(store_writer.Store, "close", close)
    return seen


def _no_drive(**kwargs: Any) -> Any:
    pytest.fail("drive_subtask_async ran in the recorded stage")


def test_inside_recorded_card_run_the_run_is_recorded_and_leased_but_not_driven(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    monkeypatch.setattr(cli, "drive_subtask_async", _no_drive)
    pre = _preflight(root, cards["subtask"])

    with cli.recorded_card_run(pre) as recorded:
        assert recorded.run_id == pre.run_id
        run = recorded.store.load_run(pre.run_id)
        assert run is not None
        (story,) = run.stories
        (subtask,) = story.subtasks
        assert (run.status, story.status, subtask.status) == ("started", "started", "started")
        assert (story.card_id, subtask.card_id) == (cards["story"], cards["subtask"])
        lease = _card_lease(root, pre.run_id)
        assert lease is not None
        assert lease.token == recorded.lease.token
        assert _claim_rows(root) == [
            (control.card_claim(cards["subtask"]), pre.run_id, recorded.lease.token)
        ]


def test_leaving_recorded_card_run_on_an_error_releases_the_claim_and_lease_before_closing(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    closes = _close_snapshots(monkeypatch)
    pre = _preflight(root, cards["subtask"])

    with pytest.raises(RuntimeError, match="engine never started"):
        with cli.recorded_card_run(pre):
            raise RuntimeError("engine never started")

    assert closes == [(0, 0)]
    assert _claim_rows(root) == []
    assert _card_lease(root, pre.run_id) is None
    again = _preflight(root, cards["subtask"], SEAM_LATER)
    assert again.run_id == cli.mint_run_id(cards["subtask"], SEAM_LATER)


def test_a_claim_taken_after_card_preflight_is_refused_on_entry_with_nothing_recorded(
    tmp_path, monkeypatch, fake_board
):
    """The lost race (spec, Error paths): another run claims the card between
    pre-flight and the recorded stage. `take_lease` refuses it atomically,
    nothing is recorded and the store is still closed."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    closes = _close_snapshots(monkeypatch)
    pre = _preflight(root, cards["subtask"])
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )

    with pytest.raises(cli.ClaimedError) as caught:
        with cli.recorded_card_run(pre):
            pytest.fail("the recorded stage yielded under another run's claim")

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert closes == [(0, 0)]
    assert _recorded_run_ids(root) == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]


def _done_drive(calls: list[dict[str, Any]]):
    """A fake `drive_subtask_async` that records its keywords and finishes `done`."""

    async def drive(**kwargs: Any) -> cli.SubtaskDrive:
        calls.append(kwargs)
        return cli.SubtaskDrive(summary=SubtaskSummary(status="done"), warnings=[])

    return drive


def test_run_card_hands_the_engine_the_lease_of_the_recorded_stage(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _done_drive(calls))
    handed: dict[str, Any] = {}

    real_recorded = cli.recorded_card_run

    @contextmanager
    def spying_recorded(pre):
        with real_recorded(pre) as recorded:
            handed["recorded"] = recorded
            yield recorded

    real_controlled = control.controlled

    async def spying_controlled(work, **kwargs):
        handed["controlled"] = kwargs["lease"]
        return await real_controlled(work, **kwargs)

    real_comment = cli.card_outcome_comment

    def spying_comment(**kwargs):
        handed["token"] = kwargs["token"]
        return real_comment(**kwargs)

    monkeypatch.setattr(cli, "recorded_card_run", spying_recorded)
    monkeypatch.setattr(control, "controlled", spying_controlled)
    monkeypatch.setattr(cli, "card_outcome_comment", spying_comment)

    result = cli.run_card(
        cards["subtask"],
        repo_dir=root,
        branch_prefix="m1",
        base_branch="main",
        clock=lambda: SEAM_AT,
        control_interval=0.01,
    )

    recorded = handed["recorded"]
    assert handed["controlled"] is recorded.lease
    assert handed["token"] == recorded.lease.token
    assert recorded.run_id == result["run_id"] == cli.mint_run_id(cards["subtask"], SEAM_AT)
    assert [call["run_id"] for call in calls] == [result["run_id"]]
    assert calls[0]["store"] is recorded.store
    assert result["status"] == "done"
    assert _claim_rows(root) == []


def test_a_crashing_card_engine_still_releases_the_claim_and_lease_before_closing(
    tmp_path, monkeypatch, fake_board
):
    """Spec: a crash in the engine still propagates, still releases the lease
    and claims, and still closes the store. A characterization pin: it passes
    before the split and must keep passing after it."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)

    async def crashing_drive(**kwargs: Any) -> Any:
        raise RuntimeError("drive bug")

    monkeypatch.setattr(cli, "drive_subtask_async", crashing_drive)
    closes = _close_snapshots(monkeypatch)

    with pytest.raises(RuntimeError, match="drive bug"):
        cli.run_card(
            cards["subtask"],
            repo_dir=root,
            branch_prefix="m1",
            base_branch="main",
            clock=lambda: SEAM_AT,
            control_interval=0.01,
        )

    assert closes == [(0, 0)]
    assert _claim_rows(root) == []


# ── am run --detach (card aff9fdbf) ─────────────────────────────────────────
#
# Unit tier: `detach.fork_detacher` is replaced by `_FakeDetacher`, which
# forks nothing; its `body` is run inline by the tests that need the child.

FAKE_CHILD_PID = 424242
"""The pid `_FakeDetacher` reports; no such child exists."""


class _FakeDetacher:
    """Stands in for `detach.fork_detacher`: records the call, starts nothing.

    `body` keeps what the real child would run. `at_go`, when set, runs as
    the parent writes the go byte, so a test can see the store at that moment.
    """

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[Path] = []
        self.events: list[str] = []
        self.body: Any = None
        self.at_go: Any = None

    def __call__(self, body: Any, log: Path) -> detach.Spawned:
        self.calls.append(log)
        if self.error is not None:
            raise self.error
        self.body = body
        return detach.Spawned(pid=FAKE_CHILD_PID, go=self._go, abort=self._abort)

    def _go(self) -> None:
        if self.at_go is not None:
            self.at_go()
        self.events.append("go")

    def _abort(self) -> None:
        self.events.append("abort")


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _alive_heartbeats() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "am-lease-heartbeat" and thread.is_alive()
    ]


DRY_RUN_DETACH = "--dry-run writes nothing and cannot be detached"


@pytest.mark.parametrize(
    ("kwargs", "message", "hint"),
    [
        (
            {"card": None, "milestone": "M9", "board": False, "branch_prefix": "m9", "dry_run": True},
            DRY_RUN_DETACH,
            "'--detach' / '--dry-run'",
        ),
        (
            {"card": SOME_CARD, "milestone": None, "board": False, "branch_prefix": "m9", "dry_run": True},
            DRY_RUN_DETACH,
            "'--detach' / '--dry-run'",
        ),
        (
            {"card": None, "milestone": None, "board": True, "branch_prefix": None, "dry_run": True},
            DRY_RUN_DETACH,
            "'--detach' / '--dry-run'",
        ),
    ],
)
def test_check_run_targets_refuses_detach_with_dry_run(kwargs, message, hint):
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(detach=True, **kwargs)

    assert caught.value.message == message
    assert caught.value.param_hint == hint


@pytest.mark.parametrize(
    "kwargs",
    [
        {"branch_prefix": None},
        {"branch_prefix": "p"},
        {"branch_prefix": "p", "max_concurrent": 2},
    ],
)
def test_check_run_targets_accepts_detach_with_board(kwargs):
    """Card 03f027ea: `--board --detach` is no longer a usage error."""
    assert (
        cli._check_run_targets(
            card=None, milestone=None, board=True, dry_run=False, detach=True, **kwargs
        )
        is None
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"card": SOME_CARD, "milestone": None},
        {"card": None, "milestone": "M9", "max_concurrent": 2},
    ],
)
def test_check_run_targets_accepts_detach_with_card_or_milestone(kwargs):
    assert (
        cli._check_run_targets(dry_run=False, branch_prefix="m9", detach=True, **kwargs) is None
    )


@pytest.mark.parametrize(
    "targets",
    [
        ["--milestone", "M9", "--branch-prefix", "m9", "--dry-run"],
        ["--board", "--dry-run"],
    ],
)
def test_detach_with_dry_run_is_a_usage_error_that_detaches_nothing(
    tmp_path, monkeypatch, targets
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = runner.invoke(
        cli.app, ["run", *targets, "--detach", "--repo-dir", str(tmp_path)]
    )

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert "detached" in result.output
    assert fake.calls == []
    assert not (paths.data_dir() / "runs").exists()
    assert not (paths.data_dir() / "boards").exists()


# ── am run --board --detach (card 03f027ea) ─────────────────────────────────


DETACHED_BOARD_PAYLOAD: dict[str, Any] = {
    "board": True,
    "detached": True,
    "pid": FAKE_CHILD_PID,
    "log": "/data/agent-manager/boards/20261004T090000Z-abc.log",
    "report": "/data/agent-manager/boards/20261004T090000Z-abc.report.json",
    "levels": [{"level": 0, "milestones": [SOME_CARD]}],
}


def _patch_detach_board(monkeypatch, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Replace `orchestrate.detach_board`, forbid every other run path, record calls."""
    _forbid_board_paths(monkeypatch)
    calls: list[dict[str, Any]] = []

    def fake_detach_board(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(orchestrate, "detach_board", fake_detach_board)
    return calls


@pytest.mark.parametrize(
    "error",
    [
        ValueError("max_concurrent must be at least 1, got 0"),
        dag.DependencyCycleError("dag: dependency cycle among milestones #a, #b"),
        orchestrate.MilestoneBlockersError(
            "milestone X is blocked by 2 milestones that are not landed (A, B); "
            "a milestone stacks on at most one: chain them (A <- B <- C)"
        ),
        cli.ClaimedError(
            "run 20261001T000000Z-00000001 already claims branch:m-integrate",
            key="branch:m-integrate",
            run_id="20261001T000000Z-00000001",
        ),
        board.BoardError("brd refused", argv=["brd", "tree"]),
    ],
    ids=["ValueError", "DependencyCycleError", "MilestoneBlockersError", "ClaimedError", "BoardError"],
)
def test_a_board_detach_preflight_refusal_is_an_envelope_that_forks_nothing(
    tmp_path, monkeypatch, error
):
    """Spec test 4 / Review Focus 2: the real `detach_board` over a refusing
    `preflight_board`: exit 3, no fork, `<data dir>/boards` absent."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    def refuse(**kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(orchestrate, "preflight_board", refuse)

    refusal = _refusal(_board_run(tmp_path, "--detach"))

    assert refusal == {"type": type(error).__name__, "message": str(error)}
    assert fake.calls == []
    assert not (paths.data_dir() / "boards").exists()


def test_a_board_detach_calls_detach_board_once_with_the_run_options(tmp_path, monkeypatch):
    """Spec test 5: the kwargs are compared whole, so an extra key fails;
    `detacher` is `detach.fork_detacher` read at call time."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_detach_board(monkeypatch, DETACHED_BOARD_PAYLOAD)
    sentinel = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", sentinel)

    result = _board_run(
        tmp_path,
        "--detach",
        "--verify",
        "X",
        "--max-concurrent",
        "3",
        "--branch-prefix",
        "p",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(DETACHED_BOARD_PAYLOAD)
    (kwargs,) = calls
    prefix_of = kwargs.pop("branch_prefix_of")
    assert kwargs.pop("detacher") is sentinel
    assert kwargs == {
        "repo_dir": tmp_path,
        "base_branch": "main",
        "commands": ["X"],
        "allow_no_verification": False,
        "launcher": "bwrap",
        "isolation_warning": None,
        "max_concurrent": 3,
    }
    assert prefix_of(BOARD_CARD) == cli.board_prefix_of("p")(BOARD_CARD)
    assert prefix_of(BOARD_CARD) == "p-milestone-14-run-the-cbe34d00"
    assert sentinel.calls == []


def test_a_board_detach_exits_0_even_when_its_payload_reads_as_escalated(
    tmp_path, monkeypatch
):
    """Spec test 6 / Review Focus 4: the board escalation check is skipped."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    payload = {**DETACHED_BOARD_PAYLOAD, "milestones": [{"status": "escalated"}]}
    _patch_detach_board(monkeypatch, payload)
    monkeypatch.setattr(detach, "fork_detacher", _FakeDetacher())

    result = _board_run(tmp_path, "--detach")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(payload)


def test_run_help_and_examples_document_the_board_detach():
    """Spec §3.6."""
    assert 'am run --board --verify "uv run pytest" --detach' in cli.RUN_EXAMPLES
    help_text = inspect.signature(cli.run).parameters["detach_run"].default.help
    assert "--board" in help_text
    assert "<data dir>/boards/<stamp>-<digest>.log" in help_text
    assert "<stamp>-<digest>.report.json" in help_text


def _handed_off_card_run(root: Path, card_id: str) -> tuple[Any, str]:
    """Stage 1 and 2 of a card run, then the parent's hand-off: what the child inherits."""
    pre = _preflight(root, card_id)
    with cli.recorded_card_run(pre) as recorded:
        token = recorded.lease.hand_off()
    return pre, token


def _no_take_lease(self, **kwargs: Any) -> Any:
    pytest.fail("the detached child took a new lease instead of adopting its own")


def _report_path(run_id: str) -> Path:
    return paths.data_dir() / "runs" / run_id / detach.REPORT_NAME


def test_the_detached_child_adopts_the_lease_reports_then_releases_before_closing(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    pre, token = _handed_off_card_run(root, cards["subtask"])
    monkeypatch.setattr(store_writer.Store, "take_lease", _no_take_lease)
    closes = _close_snapshots(monkeypatch)
    held_at_report: list[bool] = []
    real_write = detach.write_report

    def spying_write(run_id: str, text: str) -> Path:
        held_at_report.append(_card_lease(root, run_id) is not None)
        return real_write(run_id, text)

    monkeypatch.setattr(detach, "write_report", spying_write)
    seen: list[tuple[str, str, str, int]] = []

    def engine(store, lease):
        row = _card_lease(root, pre.run_id)
        seen.append((store.run_id, lease.token, row.token, len(_alive_heartbeats())))
        return {"run_id": pre.run_id, "status": "done"}

    cli.run_detached_child(root=pre.root, run_id=pre.run_id, token=token, engine=engine)

    assert seen == [(pre.run_id, token, token, 1)]
    report = _report_path(pre.run_id)
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "ok": True,
        "data": {"run_id": pre.run_id, "status": "done"},
    }
    assert _mode(report) == 0o600
    assert held_at_report == [True]
    assert closes == [(0, 0)]
    assert _card_lease(root, pre.run_id) is None
    assert _claim_rows(root) == []
    assert _alive_heartbeats() == []


def test_the_detached_child_reports_a_handled_error_and_releases(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    pre, token = _handed_off_card_run(root, cards["subtask"])
    closes = _close_snapshots(monkeypatch)

    def engine(store, lease):
        raise cli.UnknownCardError("the card is gone")

    cli.run_detached_child(root=pre.root, run_id=pre.run_id, token=token, engine=engine)

    assert json.loads(_report_path(pre.run_id).read_text(encoding="utf-8")) == {
        "ok": False,
        "error": {"type": "UnknownCardError", "message": "the card is gone"},
    }
    assert closes == [(0, 0)]
    assert _card_lease(root, pre.run_id) is None
    assert _claim_rows(root) == []


def test_a_crashing_detached_child_writes_no_report_and_still_releases(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    pre, token = _handed_off_card_run(root, cards["subtask"])
    closes = _close_snapshots(monkeypatch)

    def engine(store, lease):
        raise RuntimeError("engine bug")

    with pytest.raises(RuntimeError, match="engine bug"):
        cli.run_detached_child(root=pre.root, run_id=pre.run_id, token=token, engine=engine)

    assert not _report_path(pre.run_id).exists()
    assert closes == [(0, 0)]
    assert _card_lease(root, pre.run_id) is None
    assert _claim_rows(root) == []
    assert _recorded_run_ids(root) == [pre.run_id]


def test_release_handed_off_releases_the_claims_and_lease_and_closes(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    pre, token = _handed_off_card_run(root, cards["subtask"])
    assert _card_lease(root, pre.run_id) is not None
    closes = _close_snapshots(monkeypatch)

    cli.release_handed_off(pre.root, pre.run_id, token)

    assert closes == [(0, 0)]
    assert _card_lease(root, pre.run_id) is None
    assert _claim_rows(root) == []


def _card_run_args(root: Path, card_id: str, *extra: str) -> list[str]:
    return [
        "run",
        "--card",
        card_id,
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        "m1",
        "--allow-no-verification",
        *extra,
    ]


def _lease_pids(root: Path) -> list[int]:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        return [row["pid"] for row in conn.execute("SELECT pid FROM run_leases ORDER BY run_id")]
    finally:
        conn.close()


def _plant_parked_checkpoint(
    root: Path,
    *,
    run_id: str = CONTROL_RUN_ID,
    card_id: str = "card-1",
    workflow: str = task_workflow.TASK.name,
    reason: str = "parked",
    saved_at: datetime = CONTROL_NOW,
) -> None:
    """One checkpoint row, by default the open `parked` row of card-1 a paused
    walk leaves under the run. The keywords plant the other rows the `cards`
    tests need: another run's, another workflow's, an older or newer one."""
    opened = store_writer.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        opened.save_checkpoint(
            card_id,
            workflow=workflow,
            digest=task_workflow.TASK.digest(),
            reason=reason,
            agent={
                "current_turn": None,
                "queue": [{"kwargs": {"phase": "plan", "loop": 0}}],
            },
            saved_at=saved_at,
        )
    finally:
        opened.close()


def _open_checkpoint(root: Path) -> store_checkpoints.Checkpoint | None:
    opened = store_writer.Store.open(cli.resolve_repo_dir(root), CONTROL_RUN_ID)
    try:
        return opened.latest_open_checkpoint("card-1", task_workflow.TASK.name)
    finally:
        opened.close()


@pytest.mark.parametrize(
    "worktree_present", [True, False], ids=["worktree-present", "worktree-removed"]
)
def test_reset_records_a_stopped_run_canceled_through_one_journal_line(
    projection, worktree_present
):
    """Spec test 1, `cards` half from af52db54: the run's own row was the
    newest, so the card is closed and `open_in` is null."""
    _plant_run(projection, status="stopped")
    _plant_parked_checkpoint(projection)
    worktree = projection / ".claude" / "worktrees" / "m1" / "task-x"
    if worktree_present:
        worktree.mkdir(parents=True)
    assert _open_checkpoint(projection) is not None
    lines_before = _journal_lines()
    checkpoints_before = _checkpoint_rows(projection)

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == RESET_KEYS
    assert data == {
        "run_id": CONTROL_RUN_ID,
        "previous_status": "stopped",
        "status": "canceled",
        "already_canceled": False,
        "cards": [{"card_id": "card-1", "workflow": "task", "open_in": None}],
        "message": RESET_MESSAGE,
    }
    assert _recorded_status(projection) == "canceled"
    lines_after = _journal_lines()
    assert lines_after[: len(lines_before)] == lines_before
    (added,) = lines_after[len(lines_before) :]
    assert added.event == "run_upsert"
    # `am reset`'s own `lease_acquired` takes the number in between (card 1.2.7).
    assert added.seq == lines_before[-1].seq + 2
    first = next(line for line in lines_before if line.event == "run_upsert")
    # The same write whether or not the worktree exists: only `status` moved.
    assert added.payload == {**first.payload, "status": "canceled"}
    assert _checkpoint_rows(projection) == checkpoints_before
    assert _open_checkpoint(projection) is None
    assert worktree.exists() is worktree_present
    assert _lease(projection) is None
    rebuilt = store_writer.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        assert rebuilt.rebuild_from_events(CONTROL_RUN_ID).status == "canceled"
    finally:
        rebuilt.close()


def test_reset_closes_a_run_that_never_saved_a_checkpoint(projection):
    """Spec test 7: no "nothing to reset" refusal."""
    _plant_run(projection, status="stopped")
    assert _checkpoint_rows(projection) == []

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["cards"] == []
    assert data["status"] == "canceled"
    assert _recorded_status(projection) == "canceled"


def test_reset_journals_canceled(projection):
    """The `run_upsert` the reset records carries `canceled` in its raw line,
    and no line it writes carries the legacy spelling."""
    _plant_run(projection, status="stopped")
    count_before = len(eventlines.run_line_texts(CONTROL_RUN_ID))

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    raw = eventlines.run_line_texts(CONTROL_RUN_ID)
    upserts = [line for line in raw if json.loads(line)["event"] == "run_upsert"]
    assert upserts, raw
    assert json.loads(upserts[-1])["payload"]["status"] == "canceled", upserts[-1]
    written = raw[count_before:]
    assert len(written) == 1, written
    assert not [line for line in written if "cancelled" in line], written


@pytest.mark.parametrize("workflow", ["task", "milestone"])
@pytest.mark.parametrize("status", ["stopped", "escalated", "started"])
def test_reset_closes_every_resettable_status_of_either_workflow(
    projection, status, workflow
):
    """Review Focus 2: every status but `done`/canceled resets, a
    `started` run with no lease included, whatever the workflow."""
    _plant_run(projection, status=status, workflow=workflow)

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["previous_status"] == status
    assert data["already_canceled"] is False
    assert _recorded_status(projection) == "canceled"
    assert _lease(projection) is None


def test_reset_pretty_prints_an_indented_envelope(projection):
    """Review Focus 1."""
    _plant_run(projection, status="stopped")

    result = _invoke_reset(projection, CONTROL_RUN_ID, "--pretty")

    assert result.exit_code == 0, result.output
    assert "\n" in result.stdout.strip()
    data = json.loads(result.stdout)["data"]
    assert set(data) == RESET_KEYS
    assert data["status"] == "canceled"
    assert data["already_canceled"] is False


def _forbid_reset_writes(monkeypatch) -> None:
    """A refusal must come before `Store.open`, so reaching it fails the test."""
    monkeypatch.setattr(cli, "Store", _Forbidden("Store"))


def test_reset_refuses_an_unknown_run_and_creates_no_run_directory(
    projection, monkeypatch
):
    """Spec test 6."""
    _forbid_reset_writes(monkeypatch)

    result = _invoke_reset(projection, "no-such-run")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "UnknownRunError",
        "message": (
            f"run 'no-such-run' is not in the projection for"
            f" {cli.resolve_repo_dir(projection)}"
            " (`agent-manager runs` lists the ones that are)"
        ),
    }
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()


@pytest.mark.parametrize(
    "lease, pid, host",
    [
        ({}, None, None),
        ({"pid": 0, "host": "am-test-other-host.invalid"}, 0, "am-test-other-host.invalid"),
    ],
    ids=["this-host", "another-host"],
)
def test_reset_refuses_a_run_whose_lease_is_live_and_points_at_am_cancel(
    projection, monkeypatch, lease, pid, host
):
    """Spec test 2, plus Review Focus 3: a fresh heartbeat from another host
    is live whatever its pid."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="started")
    _plant_lease(projection, heartbeat_at=_at(-5), **lease)
    before = (_resume_guard_state(projection), _recorded_status(projection))
    _forbid_reset_writes(monkeypatch)

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error == {
        "type": "RunIsLiveError",
        "message": (
            f"run {CONTROL_RUN_ID} is still running in pid"
            f" {os.getpid() if pid is None else pid} on {HERE if host is None else host}"
            f" (heartbeat 5s ago); `am cancel {CONTROL_RUN_ID}` stops it,"
            " and `am reset` is for a run nobody is driving"
        ),
    }
    assert "am resume" not in error["message"]
    assert (_resume_guard_state(projection), _recorded_status(projection)) == before


@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_reset_refuses_a_finished_run_and_writes_nothing(
    projection, monkeypatch, workflow
):
    """Spec test 5."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="done", workflow=workflow)
    before = (_resume_guard_state(projection), _recorded_status(projection))
    _forbid_reset_writes(monkeypatch)

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "NotResettableError",
        "message": (
            f"run {CONTROL_RUN_ID} finished (done), so there is nothing to close;"
            " start new work with `am run`"
        ),
    }
    assert (_resume_guard_state(projection), _recorded_status(projection)) == before


def test_not_resettable_error_is_a_handled_cli_error():
    assert isinstance(cli.NotResettableError("finished"), cli.CliError)
    assert isinstance(cli.NotResettableError("finished"), cli.HANDLED)


@pytest.mark.parametrize(
    "stale, pid",
    [(True, None), (False, 0)],
    ids=["stale-heartbeat", "dead-pid-on-this-host"],
)
def test_reset_takes_over_a_dead_lease_and_names_its_holder(
    projection, monkeypatch, stale, pid
):
    """Spec test 3, the crash case. `control.Lease` judges liveness on the
    real clock, so the planted heartbeat is relative to the real now."""
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_run(projection, status="started")
    heartbeat = now - timedelta(seconds=31) if stale else now
    _plant_lease(projection, token="crashed", pid=pid, heartbeat_at=heartbeat)
    lines_before = _journal_lines()

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert set(data) == RESET_KEYS | {"took_over"}
    assert data["took_over"] == {
        "pid": os.getpid() if pid is None else pid,
        "host": HERE,
        "heartbeat_at": heartbeat.isoformat(),
    }
    assert data["previous_status"] == "started"
    assert data["already_canceled"] is False
    assert _recorded_status(projection) == "canceled"
    assert len(_journal_lines()) == len(lines_before) + 1
    assert _lease(projection) is None


@pytest.mark.parametrize("status", ["cancelled", "canceled"], ids=["cancelled", "canceled"])
def test_reset_reports_already_for_either_spelling(projection, status):
    """Spec test 4, both spellings: either stored spelling is reported as
    `previous_status` `canceled`, `status` is `canceled`, and nothing is
    written."""
    _plant_run(projection, status=status)
    lines_before = _journal_lines()

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data == {
        "run_id": CONTROL_RUN_ID,
        "previous_status": "canceled",
        "status": "canceled",
        "already_canceled": True,
        "cards": [],
        "message": f"run {CONTROL_RUN_ID} was already canceled; nothing was written",
    }
    assert _journal_lines() == lines_before
    assert _recorded_status(projection) == "canceled"
    assert _lease(projection) is None


def test_two_resets_of_one_run_leave_exactly_one_run_upsert(projection):
    """Spec test 8, second case: the second reset sees `canceled` under the
    lease and is a no-op."""
    _plant_run(projection, status="stopped")
    upserts_before = [line for line in _journal_lines() if line.event == "run_upsert"]

    first = _invoke_reset(projection)
    second = _invoke_reset(projection)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert json.loads(first.stdout)["data"]["already_canceled"] is False
    second_data = json.loads(second.stdout)["data"]
    assert second_data["already_canceled"] is True
    assert second_data["previous_status"] == "canceled"
    upserts_after = [line for line in _journal_lines() if line.event == "run_upsert"]
    assert len(upserts_after) == len(upserts_before) + 1
    assert _lease(projection) is None


def _plant_other_run(root: Path, status: str = "stopped") -> None:
    """Run Y (`OTHER_RUN_ID`): a second run in the projection, as a crashed
    earlier life of the same card leaves it."""
    _record(root, OTHER_RUN_ID, started_at=RECORDED_AT, status=status, with_phases=False)


def test_a_repeated_reset_reports_the_same_cards_and_writes_nothing(projection):
    """af52db54 spec test 5: an already-canceled reset still reports `cards`."""
    _plant_run(projection, status="stopped")
    _plant_parked_checkpoint(projection)

    first = _invoke_reset(projection)
    lines_after_first = _journal_lines()
    checkpoints_after_first = _checkpoint_rows(projection)
    second = _invoke_reset(projection)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_data = json.loads(first.stdout)["data"]
    second_data = json.loads(second.stdout)["data"]
    assert first_data["already_canceled"] is False
    assert second_data["already_canceled"] is True
    assert second_data["cards"] == first_data["cards"] == [
        {"card_id": "card-1", "workflow": "task", "open_in": None}
    ]
    assert _journal_lines() == lines_after_first
    assert _checkpoint_rows(projection) == checkpoints_after_first
    assert _lease(projection) is None


def test_reset_lists_every_pair_it_checkpointed_in_order_a_done_card_included(
    projection,
):
    """Review Focus 1: every `(card_id, workflow)` with a row under the run,
    any reason, ordered by card then workflow."""
    _plant_run(projection, status="stopped")
    _plant_parked_checkpoint(projection, card_id="card-2", reason="done", saved_at=_at(0))
    _plant_parked_checkpoint(projection, card_id="card-1", saved_at=_at(1))
    _plant_parked_checkpoint(
        projection, card_id="card-1", workflow="integrate", reason="turn", saved_at=_at(2)
    )

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["cards"] == [
        {"card_id": "card-1", "workflow": "integrate", "open_in": None},
        {"card_id": "card-1", "workflow": "task", "open_in": None},
        {"card_id": "card-2", "workflow": "task", "open_in": None},
    ]


def test_reset_reports_open_in_null_when_its_own_row_is_the_newest(projection):
    """af52db54 spec test 10, chain case: Y's open row is older than X's, so
    once X is cancelled the newest-row rule closes the card, and a relaunch
    continues nothing even though Y's row is still open. Two stores on one
    database: a reader bound to Y stays open across X's reset."""
    _plant_run(projection, status="stopped")
    _plant_other_run(projection)
    _plant_parked_checkpoint(projection, run_id=OTHER_RUN_ID, saved_at=_at(0))
    _plant_parked_checkpoint(projection, saved_at=_at(1))
    reader = store_writer.Store.open(cli.resolve_repo_dir(projection), OTHER_RUN_ID)
    try:
        before = runs.continuable_checkpoint(reader, "card-1")
        assert before is not None and before.run_id == CONTROL_RUN_ID
        checkpoints_before = _checkpoint_rows(projection)

        result = _invoke_reset(projection)

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["cards"] == [
            {"card_id": "card-1", "workflow": "task", "open_in": None}
        ]
        assert runs.continuable_checkpoint(reader, "card-1") is None
        assert reader.latest_open_checkpoint("card-1", task_workflow.TASK.name) is None
    finally:
        reader.close()
    # Y's row is still there and Y is untouched: only the newest row decided.
    assert _checkpoint_rows(projection) == checkpoints_before
    assert any(row[0] == OTHER_RUN_ID for row in checkpoints_before)
    assert _recorded_status(projection, OTHER_RUN_ID) == "stopped"


def test_reset_names_the_run_a_newer_bases_row_keeps_the_card_open_in(projection):
    """af52db54 spec test 10, variant: Y's newest row for the card is under
    `bases`, so resetting X leaves Y's older `task` row continuable
    (`open_in: Y`). Once Y is reset too, every report is null."""
    _plant_run(projection, status="stopped")
    _plant_other_run(projection)
    _plant_parked_checkpoint(projection, run_id=OTHER_RUN_ID, saved_at=_at(0))
    _plant_parked_checkpoint(projection, reason="turn", saved_at=_at(1))
    _plant_parked_checkpoint(
        projection, run_id=OTHER_RUN_ID, workflow="bases", saved_at=_at(2)
    )
    checkpoints_before = _checkpoint_rows(projection)

    first = _invoke_reset(projection)

    assert first.exit_code == 0, first.output
    # Exactly X's own pair: Y's `bases` pair is not X's to report.
    assert json.loads(first.stdout)["data"]["cards"] == [
        {"card_id": "card-1", "workflow": "task", "open_in": OTHER_RUN_ID}
    ]

    other = _invoke_reset(projection, OTHER_RUN_ID)

    assert other.exit_code == 0, other.output
    other_data = json.loads(other.stdout)["data"]
    assert other_data["previous_status"] == "stopped"
    assert other_data["cards"] == [
        {"card_id": "card-1", "workflow": "bases", "open_in": None},
        {"card_id": "card-1", "workflow": "task", "open_in": None},
    ]

    again = _invoke_reset(projection)

    assert again.exit_code == 0, again.output
    again_data = json.loads(again.stdout)["data"]
    assert again_data["already_canceled"] is True
    assert again_data["cards"] == [
        {"card_id": "card-1", "workflow": "task", "open_in": None}
    ]
    assert _checkpoint_rows(projection) == checkpoints_before
    assert _recorded_status(projection, OTHER_RUN_ID) == "canceled"


def test_reset_is_refused_at_take_lease_when_a_live_holder_slips_past_the_check(
    projection, monkeypatch
):
    """Spec test 8, first case: two stores on one database. The holder's
    lease is live; `lease_is_live` is blinded for the read-only check only,
    so `take_lease`'s atomic re-check is what refuses."""
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_run(projection, status="started")
    holder = store_writer.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        holder.take_lease(
            token="holder", pid=os.getpid(), host=HERE, now=now, is_live=lambda row: True
        )
    finally:
        holder.close()
    real = control.lease_is_live
    seen: list[str] = []

    def blind_first(lease, **kwargs):
        seen.append(lease.token)
        if len(seen) == 1:
            return False
        return real(lease, **kwargs)

    monkeypatch.setattr(control, "lease_is_live", blind_first)
    lines_before = _journal_lines()

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "RunIsLiveError"
    assert seen[:2] == ["holder", "holder"]
    assert _journal_lines() == lines_before
    assert _recorded_status(projection) == "started"
    lease = _lease(projection)
    assert lease is not None and lease.token == "holder"


def test_reset_rereads_the_status_under_the_lease_and_never_overwrites_done(
    projection, monkeypatch
):
    """Review Focus 4: the run finished between the read-only check and
    `take_lease`. The read-only load is made to see `stopped`; the load
    under the lease sees the real `done` and refuses before writing."""
    _plant_run(projection, status="done")
    real = store_queries.load_run
    calls: list[str] = []

    def stale_first(conn, run_id):
        loaded = real(conn, run_id)
        calls.append(run_id)
        if len(calls) == 1 and loaded is not None:
            return loaded.model_copy(update={"status": "stopped"})
        return loaded

    monkeypatch.setattr(store_queries, "load_run", stale_first)
    lines_before = _journal_lines()

    result = _invoke_reset(projection)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "NotResettableError"
    assert len(calls) == 2
    assert _journal_lines() == lines_before
    assert _recorded_status(projection) == "done"
    assert _lease(projection) is None


def test_reset_of_a_run_whose_journal_is_torn_mid_file_succeeds_and_leaves_the_file(
    projection,
):
    """3.1.1 B2: a journal file an `am` before 3.1.1 left, torn in its middle,
    is no longer read. `am reset` closes the run as it would one with no file,
    and the file keeps every byte."""
    _plant_run(projection, status="stopped")
    texts = eventlines.run_line_texts(CONTROL_RUN_ID)
    path = eventlines.journal_file(CONTROL_RUN_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        texts[0] + "\n{torn\n" + "".join(text + "\n" for text in texts[1:]),
        encoding="utf-8",
    )
    before = path.read_bytes()

    result = _invoke_reset(projection)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert (data["previous_status"], data["status"]) == ("stopped", "canceled")
    assert _recorded_status(projection) == "canceled"
    assert path.read_bytes() == before


def test_handled_takes_a_corrupt_journal_but_not_every_journal_error():
    """Only the torn-journal subclass is a refusal; a missing journal or
    any other `JournalError` stays a bug with its stack."""
    assert isinstance(store_journal.CorruptJournalError("torn"), cli.HANDLED)
    assert not isinstance(store_journal.MissingJournalError("gone"), cli.HANDLED)
    assert not isinstance(store_journal.JournalError("other"), cli.HANDLED)


def test_handled_takes_store_busy_error():
    """A write that stayed busy through its whole retry budget is a refusal,
    not a bug: it gets the exit-3 envelope, not a traceback."""
    assert isinstance(store_db.StoreBusyError("beat", 5, 10.0), cli.HANDLED)


@pytest.mark.parametrize(
    "lease",
    [None, {"heartbeat_at": CONTROL_NOW - timedelta(seconds=31)}],
    ids=["no-lease", "dead-lease"],
)
def test_a_cancel_of_a_dead_run_points_at_am_resume_and_am_reset(
    projection, monkeypatch, lease
):
    """Spec test 10: both `DeadRunError` wordings name `am reset <run-id>`."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    if lease is not None:
        _plant_lease(projection, **lease)

    result = _invoke_control(projection, "cancel")

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "DeadRunError"
    assert error["message"].endswith(
        f"`am resume {CONTROL_RUN_ID}` picks it up,"
        f" or `am reset {CONTROL_RUN_ID}` closes it"
    )
    assert _controls(projection) == []


# ── am resume of a reset run (card 522adfb5) ────────────────────────────────
#
# am-reset spec §3.6 / test 9: a run closed by `am reset` is refused by
# `am resume` exactly as an `am cancel`led one is, before `Store.open`. Unit
# tier: the projection fixture and the store only, no subprocess.


@pytest.mark.parametrize("resets", [1, 2], ids=["reset-once", "reset-twice"])
@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_resume_refuses_a_reset_run_as_cancelled_and_writes_nothing(
    projection, monkeypatch, workflow, resets
):
    """am-reset spec test 9, both workflows, with the assertion shape of
    `test_resume_refuses_a_cancelled_run_and_writes_nothing`. Review Focus 4:
    a second reset (`already_canceled: true`) changes nothing about the
    refusal. Review Focus 5: the refusal leaves every checkpoint row as the
    reset left it."""
    _freeze_clock(monkeypatch)
    _plant_run(projection, status="stopped", workflow=workflow)
    _plant_parked_checkpoint(projection)
    for n in range(resets):
        reset = _invoke_reset(projection)
        assert reset.exit_code == 0, reset.output
        assert json.loads(reset.stdout)["data"]["already_canceled"] is (n > 0)
    assert _recorded_status(projection) == "canceled"
    before = _resume_guard_state(projection)
    checkpoints_before = _checkpoint_rows(projection)
    _forbid_resume(monkeypatch)

    result = runner.invoke(cli.app, ["resume", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR == 3, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "type": "NotResumableError",
        "message": (
            f"run {CONTROL_RUN_ID} was canceled;"
            " start new work with `am run --milestone`"
        ),
    }
    assert _resume_guard_state(projection) == before
    assert _checkpoint_rows(projection) == checkpoints_before
    assert _recorded_status(projection) == "canceled"


# ── am status integrity (card f63036db) ─────────────────────────────────────
#
# journal/DB divergence spec §3.3, §3.5, §3.7: `status` compares the run's
# events with the projection through `store_replay.diverging` and reports it
# under an always-present `integrity` key, at exit 0, writing nothing. Unit
# tier: the projection fixture writes SQLite rows and journal files in
# `tmp_path`; no subprocess.

CLEAN_INTEGRITY = {"checked": True, "reason": None, "mismatches": []}

RUN_CANCELLED_BY_HAND = {
    "node": {"story": None, "card": None, "phase": None, "attempt": None},
    "field": "status",
    "journal": "started",
    "projection": "canceled",
    "kind": "foreign",
}
"""What `_plant_run` (journaled `started`) reports after `_hand_edit_run_status(..., "cancelled")`:
the hand-edited legacy spelling reads back as `canceled`."""


def _hand_edit_run_status(root: Path, status: str, run_id: str = CONTROL_RUN_ID) -> None:
    """Change the run's projected status behind the store's back, as a human
    with `sqlite3` would: no journal line records it."""
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        with store_db.immediate(conn):
            conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
    finally:
        conn.close()


PLANTED_TS = "2026-09-29T09:00:00+00:00"


def _plant_event(
    root: Path,
    kind: str,
    payload: dict[str, Any],
    *,
    run_id: str = CONTROL_RUN_ID,
    story: str | None = None,
) -> None:
    """One `events` row of `run_id` written behind the store's back, as a hand
    `INSERT` would: no row and no journal line record it."""
    resolved = cli.resolve_repo_dir(root)
    conn = store_db.open_db(resolved)
    try:
        with store_db.immediate(conn):
            store_events.insert(
                conn,
                project_id=store_projects.lookup(conn, resolved),
                run_id=run_id,
                ts=PLANTED_TS,
                kind=kind,
                payload=payload,
                source="live",
                story_id=story,
            )
    finally:
        conn.close()


def _plant_raw_payload(root: Path, kind: str, text: str, *, run_id: str = CONTROL_RUN_ID) -> int:
    """An `events` row of `run_id` whose `payload` column is `text` verbatim,
    which `store_events.insert` (it serialises) cannot write. Returns its `run_seq`."""
    resolved = cli.resolve_repo_dir(root)
    conn = store_db.open_db(resolved)
    try:
        with store_db.immediate(conn):
            run_seq = conn.execute(
                "SELECT COALESCE(MAX(run_seq), 0) + 1 FROM events WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO events (project_id, run_id, run_seq, ts, kind, payload, source)"
                " VALUES (?, ?, ?, ?, ?, ?, 'live')",
                (store_projects.lookup(conn, resolved), run_id, run_seq, PLANTED_TS, kind, text),
            )
    finally:
        conn.close()
    return run_seq


def _drop_events(root: Path, run_id: str = CONTROL_RUN_ID) -> None:
    """Delete every event of `run_id`, as a restored or foreign database may
    lack them while keeping the run's rows. The append-only trigger is
    dropped first so the rows can go."""
    conn = sqlite3.connect(paths.db_path())
    try:
        conn.execute("DROP TRIGGER events_no_delete")
        conn.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()


def _status_data(root: Path, *args: str) -> dict[str, Any]:
    result = runner.invoke(cli.app, ["status", *args, "--repo-dir", str(root)])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    return envelope["data"]


def _projection_snapshot(root: Path) -> dict[str, list[str]]:
    """Every table's rows, order-insensitively, over a plain read connection."""
    db = paths.db_path()
    conn = sqlite3.connect(db)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]
        return {
            name: sorted(repr(row) for row in conn.execute(f'SELECT * FROM "{name}"').fetchall())
            for name in tables
        }
    finally:
        conn.close()


def test_status_of_a_clean_run_is_checked_and_otherwise_unchanged(projection, monkeypatch):
    """Spec test 1; Review Focus: the no-RUN_ID default carries `integrity` too.
    Every key but `integrity` renders byte-for-byte as `status_payload` did
    before this card."""
    _freeze_clock(monkeypatch)
    _record(projection, CONTROL_RUN_ID, started_at=RECORDED_AT, status="started")
    conn = store_db.open_db(cli.resolve_repo_dir(projection))
    try:
        run = store_queries.load_run(conn, CONTROL_RUN_ID)
    finally:
        conn.close()
    before = cli.status_payload(run, cli.control_view(None, [], now=CONTROL_NOW))
    expected = cli.render(
        cli.ok_envelope(
            {
                **before,
                "integrity": CLEAN_INTEGRITY,
                "as_of_seq": _events_head(projection),
                "store_id": _store_id(projection),
            }
        )
    )

    for args in (["status", CONTROL_RUN_ID], ["status"]):
        result = runner.invoke(cli.app, [*args, "--repo-dir", str(projection)])

        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == expected


def test_status_reports_a_run_hand_edited_to_cancelled_as_one_foreign_mismatch(
    projection, monkeypatch
):
    """Spec test 2: report-only -- exit 0, and no control request is filed."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _hand_edit_run_status(projection, "cancelled")

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == {
        "checked": True,
        "reason": None,
        "mismatches": [RUN_CANCELLED_BY_HAND],
    }
    assert data["control"]["requests"] == []
    assert _controls(projection) == []


def test_the_integrity_check_writes_no_row_and_no_file(projection, monkeypatch):
    """Spec test 3: every table is identical, and nothing appears under
    `runs/`, after a `status` that found and reported a mismatch."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _hand_edit_run_status(projection, "cancelled")
    tables_before = _projection_snapshot(projection)
    assert not (paths.data_path() / "runs").exists()

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"]["checked"] is True
    assert data["integrity"]["mismatches"] == [RUN_CANCELLED_BY_HAND]
    assert _projection_snapshot(projection) == tables_before
    assert not (paths.data_path() / "runs").exists()


def test_status_of_a_run_with_no_events_says_so_and_never_opens_a_journal(
    projection, monkeypatch
):
    """Spec test 16: the run's rows are there, its events are not."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    run_dir = paths.data_dir() / "runs" / CONTROL_RUN_ID
    assert not run_dir.exists()
    _drop_events(projection)

    def forbidden(*args, **kwargs):
        raise AssertionError("status must not construct a Journal")

    monkeypatch.setattr(store_journal.Journal, "__init__", forbidden)

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == {"checked": False, "reason": "no events", "mismatches": []}
    assert not run_dir.exists()


def test_status_of_a_clean_run_with_its_journal_file_gone_is_still_checked_clean(
    projection, monkeypatch
):
    """Spec test 17: the events, not the file, are compared."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    assert not (paths.data_dir() / "runs" / CONTROL_RUN_ID).exists()

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == CLEAN_INTEGRITY


def test_an_event_payload_that_is_not_json_makes_the_events_unreadable(
    projection, monkeypatch
):
    """Spec test 18; Review Focus 1: a hand INSERT is reported at exit 0."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    run_seq = _plant_raw_payload(projection, "story_upsert", "{not json")

    data = _status_data(projection, CONTROL_RUN_ID)

    integrity = data["integrity"]
    assert integrity["checked"] is False
    assert integrity["reason"].startswith("events unreadable: ")
    assert f"run_seq {run_seq}" in integrity["reason"]
    assert "not JSON" in integrity["reason"]
    assert integrity["mismatches"] == []


def test_events_without_a_run_upsert_are_unreadable_not_a_traceback(projection, monkeypatch):
    """Spec test 19: `run_lines` returns a story line only, and `replay` inside
    `diverging` raises `JournalError` -- the try must cover `diverging` too."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _drop_events(projection)
    _plant_event(
        projection,
        "story_upsert",
        {"card_id": "story-1", "title": "The CLI", "level": 0, "status": "started", "tip_branch": None},
        story="story-1",
    )

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == {
        "checked": False,
        "reason": "events unreadable: journal line 1 is a story_upsert but no"
        " run_upsert preceded it: the head of the journal is missing",
        "mismatches": [],
    }


def test_an_event_payload_that_fails_validation_is_unreadable(projection, monkeypatch):
    """Spec test 20: the event is valid JSON, the run payload is not a `Run`,
    so `diverging` raises a pydantic `ValidationError`."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_event(projection, "run_upsert", {"id": CONTROL_RUN_ID, "status": "not-a-status"})

    data = _status_data(projection, CONTROL_RUN_ID)

    integrity = data["integrity"]
    assert integrity["checked"] is False
    assert integrity["reason"].startswith("events unreadable: ")
    assert "validation error" in integrity["reason"]
    assert integrity["mismatches"] == []


def test_an_event_of_another_kind_is_skipped_by_the_check(projection, monkeypatch):
    """Spec test 21; Review Focus 2: a lease or control event is not divergence."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_event(projection, "from_the_future", {"anything": "at all"})

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == CLEAN_INTEGRITY


@pytest.mark.parametrize(
    "heartbeat_at, integrity",
    [
        (
            CONTROL_NOW - timedelta(seconds=5),
            {"checked": False, "reason": "lease is live", "mismatches": []},
        ),
        (
            CONTROL_NOW - timedelta(seconds=31),
            {"checked": True, "reason": None, "mismatches": [RUN_CANCELLED_BY_HAND]},
        ),
    ],
    ids=["live", "stale"],
)
def test_a_live_lease_is_not_checked_and_a_stale_one_is(
    projection, monkeypatch, heartbeat_at, integrity
):
    """Spec test 4 (§3.5): writes in flight are noise, not divergence, even
    against a hand-edited projection; a dead lease's run is checked."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _hand_edit_run_status(projection, "cancelled")
    _plant_lease(projection, heartbeat_at=heartbeat_at)

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == integrity


def test_a_live_lease_never_reads_the_events(projection, monkeypatch):
    """Review Focus: the live-lease rule comes first, so even an unreadable
    event reads as `lease is live`, and neither `run_lines` nor `diverging`
    is called."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_raw_payload(projection, "story_upsert", "{not json")
    _plant_lease(projection, heartbeat_at=CONTROL_NOW - timedelta(seconds=5))

    def forbidden(*args, **kwargs):
        raise AssertionError("a live run's events must not be compared")

    monkeypatch.setattr(store_events, "run_lines", forbidden)
    monkeypatch.setattr(store_replay, "diverging", forbidden)

    data = _status_data(projection, CONTROL_RUN_ID)

    assert data["integrity"] == {"checked": False, "reason": "lease is live", "mismatches": []}


def test_a_detached_card_run_prints_one_envelope_and_leaves_the_lease_to_the_child(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    monkeypatch.setattr(cli, "drive_subtask_async", _no_drive)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)
    closes = _close_snapshots(monkeypatch)
    pids_at_go: list[list[int]] = []
    fake.at_go = lambda: pids_at_go.append(_lease_pids(root))

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 1
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == {"run_id", "pid", "log", "detached"}
    assert (data["pid"], data["detached"]) == (FAKE_CHILD_PID, True)
    run_id = data["run_id"]
    assert [entry["id"] for entry in cli.runs_for(repo_dir=root)["runs"]] == [run_id]
    log = Path(data["log"])
    assert log == paths.data_dir() / "runs" / run_id / detach.RUN_LOG_NAME
    assert log.is_file() and _mode(log) == 0o600
    assert fake.calls == [log]
    assert fake.events == ["go"]
    assert pids_at_go == [[FAKE_CHILD_PID]]
    lease = _card_lease(root, run_id)
    assert lease is not None
    assert (lease.pid, lease.host) == (FAKE_CHILD_PID, socket.gethostname())
    assert _claim_rows(root) == [(control.card_claim(cards["subtask"]), run_id, lease.token)]
    assert _alive_heartbeats() == []
    # The recorded stage's store, then the pid update's: both closed, both still holding.
    assert closes == [(1, 1), (1, 1)]
    assert not (log.parent / detach.REPORT_NAME).exists()


def test_detach_honours_pretty(tmp_path, monkeypatch, fake_board):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    monkeypatch.setattr(detach, "fork_detacher", _FakeDetacher())

    result = runner.invoke(
        cli.app, _card_run_args(root, cards["subtask"], "--detach", "--pretty")
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert result.stdout == cli.render(envelope, pretty=True) + "\n"
    assert envelope["data"]["detached"] is True


def test_the_detached_card_child_reports_the_foreground_payload_then_releases(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)
    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.stdout)["data"]["run_id"]

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _done_drive(calls))
    captured: list[dict[str, Any]] = []
    real_engine = cli.run_card_engine

    async def spying_engine(pre, recorded, **kwargs):
        payload = await real_engine(pre, recorded, **kwargs)
        captured.append(payload)
        return payload

    monkeypatch.setattr(cli, "run_card_engine", spying_engine)
    monkeypatch.setattr(store_writer.Store, "take_lease", _no_take_lease)
    closes = _close_snapshots(monkeypatch)

    fake.body()

    (payload,) = captured
    assert (payload["run_id"], payload["status"]) == (run_id, "done")
    assert [call["run_id"] for call in calls] == [run_id]
    report = _report_path(run_id)
    assert json.loads(report.read_text(encoding="utf-8")) == json.loads(
        cli.render(cli.ok_envelope(payload))
    )
    assert _mode(report) == 0o600
    assert closes == [(0, 0)]
    assert _card_lease(root, run_id) is None
    assert _claim_rows(root) == []


def test_a_parentless_card_is_refused_the_same_with_or_without_detach(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    loose = fake_board.add_card("A card with no story")
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    foreground = runner.invoke(cli.app, _card_run_args(root, loose))
    detached = runner.invoke(cli.app, _card_run_args(root, loose, "--detach"))

    assert (foreground.exit_code, detached.exit_code) == (cli.EXIT_ERROR, cli.EXIT_ERROR)
    assert json.loads(detached.stdout) == json.loads(foreground.stdout)
    assert json.loads(detached.stdout)["error"]["type"] == "ParentlessCardError"
    assert fake.calls == []
    assert _run_dirs() == []


def test_a_claimed_card_is_refused_the_same_with_or_without_detach(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    foreground = runner.invoke(cli.app, _card_run_args(root, cards["subtask"]))
    detached = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    def steady(stdout: str) -> dict[str, Any]:
        envelope = json.loads(stdout)
        envelope["error"]["message"] = re.sub(r"\d+s ago", "Ns ago", envelope["error"]["message"])
        return envelope

    assert (foreground.exit_code, detached.exit_code) == (cli.EXIT_ERROR, cli.EXIT_ERROR)
    assert steady(detached.stdout) == steady(foreground.stdout)
    assert json.loads(detached.stdout)["error"]["type"] == "ClaimedError"
    assert fake.calls == []
    assert _run_dirs() == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]


def test_a_failed_detach_releases_the_claim_and_lease_and_prints_no_detached_envelope(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    fake = _FakeDetacher(error=OSError("fork failed"))
    monkeypatch.setattr(detach, "fork_detacher", fake)
    closes = _close_snapshots(monkeypatch)

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    assert isinstance(result.exception, OSError)
    assert '"detached"' not in result.stdout
    assert len(fake.calls) == 1
    assert closes[-1] == (0, 0)
    assert _claim_rows(root) == []
    (run_id,) = _recorded_run_ids(root)
    assert _card_lease(root, run_id) is None


def test_a_run_log_that_cannot_be_created_releases_through_the_recorded_stage(
    tmp_path, monkeypatch, fake_board
):
    """Spec, Error paths: the failure is raised inside the recorded stage,
    before the hand-off, so 3.1's release-then-close covers it."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    def unwritable(run_id: str) -> Path:
        raise PermissionError("run.log: permission denied")

    monkeypatch.setattr(detach, "create_run_log", unwritable)
    closes = _close_snapshots(monkeypatch)

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    assert isinstance(result.exception, PermissionError)
    assert '"detached"' not in result.stdout
    assert fake.calls == []
    assert closes == [(0, 0)]
    assert _claim_rows(root) == []
    (run_id,) = _recorded_run_ids(root)
    assert _card_lease(root, run_id) is None


def test_a_failed_lease_pid_update_aborts_the_child_and_releases(
    tmp_path, monkeypatch, fake_board
):
    """Review Focus 1: the child is told to abort and never runs its body."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    def locked(self, token: str, *, pid: int, host: str) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store_writer.Store, "set_lease_holder", locked)

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"], "--detach"))

    assert isinstance(result.exception, sqlite3.OperationalError)
    assert '"detached"' not in result.stdout
    assert fake.events == ["abort"]
    assert _claim_rows(root) == []
    (run_id,) = _recorded_run_ids(root)
    assert _card_lease(root, run_id) is None


def test_a_card_run_without_detach_still_prints_its_full_payload(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _done_drive(calls))
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = runner.invoke(cli.app, _card_run_args(root, cards["subtask"]))

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["status"] == "done"
    assert "detached" not in data
    assert fake.calls == []
    assert _claim_rows(root) == []
    assert not (paths.data_dir() / "runs" / data["run_id"] / detach.RUN_LOG_NAME).exists()


def test_run_help_and_examples_document_detach():
    assert "--detach" in cli.RUN_EXAMPLES

    result = runner.invoke(cli.app, ["run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--detach" in result.output


@pytest.mark.parametrize("status", ["done", "merged", "MERGED"])
def test_already_done_lists_finished_stories_and_subtasks(status):
    closed = _plan_story(1, [_plan_subtask(11)], status=status)
    open_story = _plan_story(2, [_plan_subtask(21, status), _plan_subtask(22)])
    assert cli.already_done_entries([closed, open_story]) == [
        {"kind": "story", "id": closed.id, "title": "story 1"},
        {"kind": "subtask", "id": _plan_id(21), "title": "subtask 21", "story": open_story.id},
    ]


@pytest.mark.parametrize("status", ["canceled", "archived", "CANCELED"])
def test_already_done_omits_out_of_play_cards_and_they_never_run(status):
    dead = _plan_story(1, [_plan_subtask(11)], status=status)
    live = _plan_story(2, [_plan_subtask(21, status), _plan_subtask(22)], blocked_by=[dead.id])
    assert cli.already_done_entries([dead, live]) == []
    payload = cli.dry_run_payload(
        [dead, live], repo_dir=DRY_RUN_REPO, branch_prefix="m3", base_branch="main"
    )
    assert payload["already_done"] == []
    ran = [s["id"] for lvl in payload["levels"] for st in lvl["stories"] for s in st["subtasks"]]
    assert ran == [_plan_id(22)]


def test_already_done_omits_an_out_of_play_story_even_with_finished_subtasks():
    dead = _plan_story(1, [_plan_subtask(11, "done")], status="archived")
    assert cli.already_done_entries([dead]) == []


# ── am run --story: usage refusals ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("other", "message", "hint"),
    [
        ({"board": True}, "give --board or --story, not both", "'--board' / '--story'"),
        ({"card": SOME_CARD}, "give --card or --story, not both", "'--card' / '--story'"),
        (
            {"milestone": "M"},
            "give --milestone or --story, not both",
            "'--milestone' / '--story'",
        ),
    ],
)
def test_check_run_targets_refuses_story_with_another_target(other, message, hint):
    kwargs: dict[str, Any] = {
        "card": None,
        "milestone": None,
        "dry_run": False,
        "branch_prefix": "m3",
        **other,
    }

    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(story="S", **kwargs)

    assert caught.value.message == message
    assert caught.value.param_hint == hint


@pytest.mark.parametrize("blank", ["", "   "])
def test_check_run_targets_refuses_a_blank_story(blank):
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(
            card=None, milestone=None, dry_run=False, branch_prefix="m3", story=blank
        )

    assert caught.value.message == (
        "--story needs a card id or a title piece, not a blank string"
    )
    assert caught.value.param_hint == "'--story'"


def test_check_run_targets_refuses_story_without_branch_prefix():
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(card=None, milestone=None, dry_run=False, story="S")

    assert caught.value.message == PREFIX_REQUIRED
    assert caught.value.param_hint == "'--branch-prefix'"


@pytest.mark.parametrize(
    ("bound", "message"),
    [
        (1, "--max-concurrent applies only to --milestone or --board"),
        (4, "--max-concurrent applies only to --milestone or --board"),
        (0, "--max-concurrent must be at least 1, got 0"),
    ],
)
def test_check_run_targets_refuses_max_concurrent_with_story(bound, message):
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(
            card=None,
            milestone=None,
            dry_run=False,
            branch_prefix="m3",
            story="S",
            max_concurrent=bound,
        )

    assert caught.value.message == message
    assert caught.value.param_hint == "'--max-concurrent'"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dry_run": True},
        {"dry_run": False, "detach": True},
        {"dry_run": False},
    ],
)
def test_check_run_targets_accepts_story_with_dry_run_or_detach(kwargs):
    assert (
        cli._check_run_targets(
            card=None, milestone=None, branch_prefix="m3", story="S", **kwargs
        )
        is None
    )


def test_check_run_targets_refuses_story_dry_run_with_detach():
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(
            card=None,
            milestone=None,
            dry_run=True,
            detach=True,
            branch_prefix="m3",
            story="S",
        )

    assert caught.value.message == DRY_RUN_DETACH
    assert caught.value.param_hint == "'--detach' / '--dry-run'"


def test_check_run_targets_names_story_when_no_target_is_given():
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(card=None, milestone=None, dry_run=False, branch_prefix="m3")

    assert caught.value.message == "one of --card, --milestone, --story or --board is required"
    assert caught.value.param_hint == "'--card' / '--milestone' / '--story' / '--board'"


# ── am run --story --dry-run: the story preview ─────────────────────────────
#
# Unit tier: the FakeBoard (`fake_board`) answers every board call, the repo
# dir is a plain directory, and the done-blocker branch lookup is the stubbed
# `orchestrate._local_branch_exists`. No git, brd or claude process starts.

STORY_PREFIX = "m3"


def _story_root(tmp_path: Path, monkeypatch) -> Path:
    """A plain project directory with its projection under tmp_path; no git, no brd."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    return root.resolve()


def _story_branch(root: Path, card_id: str) -> str:
    return dag.task_branch(STORY_PREFIX, board.show(card_id, repo_dir=root))


def _story_lookups(monkeypatch, present: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Stub `orchestrate._local_branch_exists`: only `present` exist locally.

    Returns the list every branch asked about is appended to.
    """
    asked: list[str] = []

    def factory(root: Path) -> Any:
        def exists(branch: str) -> bool:
            asked.append(branch)
            return branch in present

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", factory)
    return asked


def _forbid_story_writes(monkeypatch) -> None:
    """A story preview opens no store, refreshes no git, checks no claim, runs nothing."""
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "Store", _Forbidden("orchestrate.Store"))
    monkeypatch.setattr(orchestrate, "refresh_git", _Forbidden("orchestrate.refresh_git"))
    monkeypatch.setattr(cli, "refuse_claimed", _Forbidden("refuse_claimed"))
    monkeypatch.setattr(orchestrate, "run_story", _Forbidden("run_story"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))


def _seed_story_board(fake_board) -> dict[str, str]:
    """A milestone with Story A (one todo subtask) and Story S (s1 done, s2 and s3 todo)."""
    milestone = fake_board.add_card("Milestone 3: orchestration")
    other = fake_board.add_card("Story A: rows", parent_id=milestone)
    a1 = fake_board.add_card("a1: rows", parent_id=other)
    story = fake_board.add_card("Story S: cols", parent_id=milestone)
    s1 = fake_board.add_card("s1: cols one", parent_id=story, status="done")
    s2 = fake_board.add_card("s2: cols two", parent_id=story, blocked_by=[s1])
    s3 = fake_board.add_card("s3: cols three", parent_id=story, blocked_by=[s2])
    return {
        "milestone": milestone,
        "other": other,
        "a1": a1,
        "story": story,
        "s1": s1,
        "s2": s2,
        "s3": s3,
    }


def _dry_run_story(root: Path, needle: str) -> dict[str, Any]:
    return cli.dry_run_story(
        needle, repo_dir=root, branch_prefix=STORY_PREFIX, base_branch="main"
    )


def test_dry_run_story_previews_only_that_story_with_no_integrate(
    tmp_path, monkeypatch, fake_board
):
    root = _story_root(tmp_path, monkeypatch)
    cards = _seed_story_board(fake_board)
    asked = _story_lookups(monkeypatch)
    _forbid_story_writes(monkeypatch)
    branch = {key: _story_branch(root, cards[key]) for key in ("s1", "s2", "s3")}

    data = _dry_run_story(root, cards["story"])

    assert list(data) == ["max_concurrent", "levels", "already_done", "integrate"]
    assert data == {
        "max_concurrent": 1,
        "levels": [
            {
                "level": 0,
                "concurrent": 1,
                "stories": [
                    {
                        "story": cards["story"],
                        "title": "Story S: cols",
                        "root": "main",
                        "subtasks": [
                            {
                                "id": cards["s2"],
                                "title": "s2: cols two",
                                "status": "todo",
                                "branch": branch["s2"],
                                "base": branch["s1"],
                            },
                            {
                                "id": cards["s3"],
                                "title": "s3: cols three",
                                "status": "todo",
                                "branch": branch["s3"],
                                "base": branch["s2"],
                            },
                        ],
                    }
                ],
            }
        ],
        "already_done": [
            {"kind": "subtask", "id": cards["s1"], "title": "s1: cols one", "story": cards["story"]}
        ],
        "integrate": None,
    }
    assert asked == []
    assert _run_dirs() == []


@pytest.mark.parametrize(
    ("blocker_status", "tip_exists", "rooted_on_tip", "looked_up"),
    [
        ("done", True, True, True),
        ("done", False, False, True),
        ("canceled", False, False, False),
    ],
)
def test_dry_run_story_roots_the_story_where_the_real_run_would(
    tmp_path, monkeypatch, fake_board, blocker_status, tip_exists, rooted_on_tip, looked_up
):
    """A done blocker's tip when that branch exists locally, else the base
    branch; an out-of-play blocker is never looked up. The blocker itself is
    never a level row and never listed as already done."""
    root = _story_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker = fake_board.add_card("Story A: rows", parent_id=milestone, status=blocker_status)
    b1 = fake_board.add_card(
        "a1: rows", parent_id=blocker, status="done" if blocker_status == "done" else "todo"
    )
    story = fake_board.add_card("Story S: cols", parent_id=milestone, blocked_by=[blocker])
    s1 = fake_board.add_card("s1: cols one", parent_id=story)
    tip = _story_branch(root, b1)
    asked = _story_lookups(monkeypatch, {tip} if tip_exists else set())
    _forbid_story_writes(monkeypatch)
    expected_root = tip if rooted_on_tip else "main"

    data = _dry_run_story(root, story)

    assert data["levels"] == [
        {
            "level": 0,
            "concurrent": 1,
            "stories": [
                {
                    "story": story,
                    "title": "Story S: cols",
                    "root": expected_root,
                    "subtasks": [
                        {
                            "id": s1,
                            "title": "s1: cols one",
                            "status": "todo",
                            "branch": _story_branch(root, s1),
                            "base": expected_root,
                        }
                    ],
                }
            ],
        }
    ]
    assert data["already_done"] == []
    assert data["integrate"] is None
    assert asked == ([tip] if looked_up else [])


def test_dry_run_story_roots_on_a_merged_base_for_two_done_blockers(
    tmp_path, monkeypatch, fake_board
):
    root = _story_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    a = fake_board.add_card("Story A: rows", parent_id=milestone, status="done")
    a1 = fake_board.add_card("a1: rows", parent_id=a, status="done")
    b = fake_board.add_card("Story B: cells", parent_id=milestone, status="done")
    b1 = fake_board.add_card("b1: cells", parent_id=b, status="done")
    story = fake_board.add_card("Story S: cols", parent_id=milestone, blocked_by=[b, a])
    fake_board.add_card("s1: cols one", parent_id=story)
    _story_lookups(monkeypatch, {_story_branch(root, a1), _story_branch(root, b1)})
    _forbid_story_writes(monkeypatch)
    match = census.find_story(board.roots(repo_dir=root), story)
    cut = orchestrate.story_census(
        board.tree(milestone, repo_dir=root), match.story, root=root, branch_prefix=STORY_PREFIX
    )
    merged = dag.base_branch_name(STORY_PREFIX, cut.stories[-1])

    data = _dry_run_story(root, story)

    (level,) = data["levels"]
    (row,) = level["stories"]
    assert row["story"] == story
    assert row["root"] == merged
    assert row["merged_from"] == [a, b]
    assert row["subtasks"][0]["base"] == merged
    assert data["already_done"] == []


@pytest.mark.parametrize(
    ("story_status", "subtask_status", "already_done_story"),
    [("todo", "done", True), ("done", "done", True), ("canceled", "todo", False)],
)
def test_dry_run_story_with_nothing_to_run_has_no_level(
    tmp_path, monkeypatch, fake_board, story_status, subtask_status, already_done_story
):
    """A finished story is one `kind: story` entry; an out-of-play one lists nothing."""
    root = _story_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    fake_board.add_card("Story A: rows", parent_id=milestone)
    story = fake_board.add_card("Story S: cols", parent_id=milestone, status=story_status)
    s1 = fake_board.add_card("s1: cols one", parent_id=story, status=subtask_status)
    fake_board.add_card("s2: cols two", parent_id=story, status=subtask_status, blocked_by=[s1])
    _story_lookups(monkeypatch)
    _forbid_story_writes(monkeypatch)

    data = _dry_run_story(root, story)

    assert data["levels"] == []
    assert data["already_done"] == (
        [{"kind": "story", "id": story, "title": "Story S: cols"}] if already_done_story else []
    )
    assert data["integrate"] is None
    assert _run_dirs() == []


def test_dry_run_story_refuses_an_open_blocker_before_any_lookup(
    tmp_path, monkeypatch, fake_board
):
    root = _story_root(tmp_path, monkeypatch)
    cards = _seed_story_board(fake_board)
    blocked = fake_board.add_card(
        "Story T: cells", parent_id=cards["milestone"], blocked_by=[cards["other"]]
    )
    fake_board.add_card("t1: cells", parent_id=blocked)
    asked = _story_lookups(monkeypatch)
    _forbid_story_writes(monkeypatch)

    with pytest.raises(errors.StoryBlockedError):
        _dry_run_story(root, blocked)

    assert asked == []
    assert _run_dirs() == []


@pytest.mark.parametrize("case", ["none", "several", "milestone", "milestone title", "subtask"])
def test_dry_run_story_refuses_a_needle_that_is_not_one_story(
    tmp_path, monkeypatch, fake_board, case
):
    root = _story_root(tmp_path, monkeypatch)
    cards = _seed_story_board(fake_board)
    needle = {
        "none": "no such story",
        "several": "Story",
        "milestone": cards["milestone"],
        "milestone title": "orchestration",
        "subtask": cards["s1"],
    }[case]
    _story_lookups(monkeypatch)
    _forbid_story_writes(monkeypatch)

    with pytest.raises(errors.StoryNotFoundError):
        _dry_run_story(root, needle)

    assert _run_dirs() == []


def test_dry_run_story_refuses_a_blocker_cycle_anywhere_in_the_milestone(
    tmp_path, monkeypatch, fake_board
):
    """The census is patched to hand back cyclic sibling stories, as in the
    preflight_story cycle test: the board itself cannot hold a cycle."""
    root = _story_root(tmp_path, monkeypatch)
    cards = _seed_story_board(fake_board)
    a = census.StoryPlan(
        "story-x", "Story X", "todo", ["story-y"], [census.SubtaskPlan("x1", "x1", "todo")]
    )
    b = census.StoryPlan(
        "story-y", "Story Y", "todo", ["story-x"], [census.SubtaskPlan("y1", "y1", "todo")]
    )
    selected = census.StoryPlan(
        cards["story"], "Story S: cols", "todo", [], [census.SubtaskPlan(cards["s2"], "s2", "todo")]
    )
    monkeypatch.setattr(
        census,
        "flatten_milestone",
        lambda node: census.Census(milestone_title=node.title, stories=[a, b, selected]),
    )
    _story_lookups(monkeypatch)
    _forbid_story_writes(monkeypatch)

    with pytest.raises(dag.DependencyCycleError):
        _dry_run_story(root, cards["story"])


# ── am run --story: dispatch, exit codes and refusals ───────────────────────

STORY_RUN_ID = "20260924T000000Z-0badcafe"

CLEAN_STORY = {
    "done": True,
    "run_id": STORY_RUN_ID,
    "levels": [{"level": 0, "stories": ["story-a"]}],
    "completed": ["subtask-a1"],
    "tips": [{"story": "story-a", "tip": "m3/task-a1"}],
    "warnings": [],
}
"""`run_story`'s clean payload: a milestone `done` payload without `integrated`."""

NOTHING_TO_RUN_STORY = {
    "done": True,
    "run_id": STORY_RUN_ID,
    "levels": [],
    "completed": [],
    "tips": [],
    "warnings": [],
}

PAUSED_STORY = {
    "paused": True,
    "run_id": STORY_RUN_ID,
    "stopped": [],
    "completed": [],
    "pending": ["story-a"],
    "warnings": [],
    "resume": f"am resume {STORY_RUN_ID}",
}

CANCELED_STORY = {
    "canceled": True,
    "run_id": STORY_RUN_ID,
    "stopped": [],
    "completed": [],
    "pending": ["story-a"],
    "warnings": [],
}

ESCALATED_STORY = {
    "escalated": True,
    "run_id": STORY_RUN_ID,
    "level": 0,
    "story": "story-a",
    "subtask": "subtask-a1",
    "failed_phase": "review",
    "detail": "phase 'review' gate 'review_gate' failed",
    "warnings": [],
}

DETACHED_STORY_PAYLOAD = {
    "run_id": STORY_RUN_ID,
    "pid": FAKE_CHILD_PID,
    "log": f"/data/agent-manager/runs/{STORY_RUN_ID}/run.log",
    "detached": True,
}


def _story_run(root: Path, needle: str, *extra: str):
    return runner.invoke(
        cli.app,
        [
            "run",
            "--story",
            needle,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            STORY_PREFIX,
            *extra,
        ],
    )


def _forbid_other_run_paths(monkeypatch) -> None:
    """Every run path that is not a story's own, forbidden."""
    _forbid_writes(monkeypatch)
    for name in ("run_milestone", "detach_milestone", "run_board", "detach_board"):
        monkeypatch.setattr(orchestrate, name, _Forbidden(name))
    for name in ("dry_run_milestone", "dry_run_board", "detach_card"):
        monkeypatch.setattr(cli, name, _Forbidden(name))


def _patch_run_story(monkeypatch, outcome: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Replace `orchestrate.run_story`, forbid every other run path, record calls."""
    _forbid_other_run_paths(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_story", _Forbidden("dry_run_story"))
    monkeypatch.setattr(orchestrate, "detach_story", _Forbidden("detach_story"))
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run_story(story, **kwargs):
        calls.append((story, kwargs))
        return outcome

    monkeypatch.setattr(orchestrate, "run_story", fake_run_story)
    return calls


@pytest.mark.parametrize(
    ("targets", "word"),
    [
        (["--story", "S", "--board", "--branch-prefix", "m3"], "both"),
        (["--story", "S", "--card", SOME_CARD, "--branch-prefix", "m3"], "both"),
        (["--story", "S", "--milestone", "M", "--branch-prefix", "m3"], "both"),
        (["--branch-prefix", "m3"], "required"),
        (["--story", "", "--branch-prefix", "m3"], "blank"),
        (["--story", "   ", "--branch-prefix", "m3"], "blank"),
        (["--story", "S"], "required"),
        (["--story", "S", "--branch-prefix", "m3", "--max-concurrent", "1"], "only"),
        (["--story", "S", "--branch-prefix", "m3", "--max-concurrent", "4"], "only"),
        (["--story", "S", "--branch-prefix", "m3", "--dry-run", "--detach"], "detached"),
    ],
)
def test_story_usage_errors_exit_2_and_dispatch_nothing(tmp_path, monkeypatch, targets, word):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_other_run_paths(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_story", _Forbidden("run_story"))
    monkeypatch.setattr(orchestrate, "detach_story", _Forbidden("detach_story"))
    monkeypatch.setattr(cli, "dry_run_story", _Forbidden("dry_run_story"))
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = runner.invoke(cli.app, ["run", *targets, "--repo-dir", str(tmp_path)])

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert "No such option" not in result.output
    assert word in result.output
    assert fake.calls == []
    assert not (paths.data_dir() / "runs").exists()


def test_a_story_run_calls_run_story_once_with_the_run_options(tmp_path, monkeypatch):
    """The kwargs are compared whole, so an extra key (`max_concurrent`,
    `driver`, `runner_factory`) fails."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_story(monkeypatch, CLEAN_STORY)

    result = _story_run(
        tmp_path,
        "Story 3.1",
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
        "--allow-no-verification",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(CLEAN_STORY)
    assert calls == [
        (
            "Story 3.1",
            {
                "repo_dir": tmp_path,
                "base_branch": "main",
                "branch_prefix": STORY_PREFIX,
                "commands": ["uv run pytest", "uv run ruff check"],
                "allow_no_verification": True,
                "launcher": "bwrap",
                "isolation_warning": None,
            },
        )
    ]


def test_an_escalated_story_run_exits_escalated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_story(monkeypatch, ESCALATED_STORY)

    result = _story_run(tmp_path, "Story 3.1")

    assert result.exit_code == cli.EXIT_ESCALATED, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(ESCALATED_STORY)


@pytest.mark.parametrize(
    "payload", [CLEAN_STORY, NOTHING_TO_RUN_STORY, PAUSED_STORY, CANCELED_STORY]
)
def test_a_stopped_or_nothing_to_run_story_exits_0(tmp_path, monkeypatch, payload):
    """None of these carries a `status` key: the rule must not index one."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_story(monkeypatch, payload)

    result = _story_run(tmp_path, "Story 3.1")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(payload)


@pytest.mark.parametrize(
    ("case", "error_type"),
    [
        ("none", "StoryNotFoundError"),
        ("several", "StoryNotFoundError"),
        ("milestone", "StoryNotFoundError"),
        ("subtask", "StoryNotFoundError"),
        ("blocked", "StoryBlockedError"),
        ("claimed", "ClaimedError"),
    ],
)
def test_story_refusals_are_the_error_envelope_with_exit_3(
    tmp_path, monkeypatch, fake_board, case, error_type
):
    """The real `run_story` and `preflight_story`: every refusal comes before
    git is refreshed and before any run directory exists."""
    root = _story_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker = fake_board.add_card("Story A: rows", parent_id=milestone)
    a1 = fake_board.add_card("a1: rows", parent_id=blocker)
    story = fake_board.add_card("Story S: cols", parent_id=milestone, blocked_by=[blocker])
    fake_board.add_card("s1: cols one", parent_id=story)
    needle = {
        "none": "no such story",
        "several": "Story",
        "milestone": milestone,
        "subtask": a1,
        "blocked": story,
        "claimed": blocker,
    }[case]

    def held(at: Path, keys: Any, *, run_id: str | None = None) -> None:
        key = list(keys)[0]
        raise cli.ClaimedError(
            f"{key} is claimed by run 20260101T000000Z-00000000",
            key=key,
            run_id="20260101T000000Z-00000000",
        )

    _forbid_other_run_paths(monkeypatch)
    monkeypatch.setattr(cli, "refuse_claimed", held)
    monkeypatch.setattr(orchestrate, "refresh_git", _Forbidden("orchestrate.refresh_git"))
    _story_lookups(monkeypatch)

    error = _refusal(_story_run(root, needle))

    assert error["type"] == error_type
    assert _run_dirs() == []


def test_a_story_dry_run_prints_its_preview_and_writes_nothing(
    tmp_path, monkeypatch, fake_board
):
    root = _story_root(tmp_path, monkeypatch)
    cards = _seed_story_board(fake_board)
    _story_lookups(monkeypatch)
    expected = _dry_run_story(root, cards["story"])
    _forbid_other_run_paths(monkeypatch)
    _forbid_story_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "detach_story", _Forbidden("detach_story"))

    result = _story_run(root, "cols", "--dry-run")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == json.loads(cli.render(cli.ok_envelope(expected)))
    assert json.loads(result.stdout)["data"]["integrate"] is None
    assert _run_dirs() == []


@pytest.mark.parametrize(
    ("case", "error_type"),
    [("blocked", "StoryBlockedError"), ("several", "StoryNotFoundError")],
)
def test_a_story_dry_run_refusal_is_an_envelope_with_exit_3(
    tmp_path, monkeypatch, fake_board, case, error_type
):
    root = _story_root(tmp_path, monkeypatch)
    cards = _seed_story_board(fake_board)
    blocked = fake_board.add_card(
        "Story T: cells", parent_id=cards["milestone"], blocked_by=[cards["other"]]
    )
    fake_board.add_card("t1: cells", parent_id=blocked)
    needle = {"blocked": blocked, "several": "Story"}[case]
    _story_lookups(monkeypatch)
    _forbid_other_run_paths(monkeypatch)
    _forbid_story_writes(monkeypatch)

    error = _refusal(_story_run(root, needle, "--dry-run"))

    assert error["type"] == error_type
    assert _run_dirs() == []


def test_story_detach_dispatches_detach_story_with_the_run_options(tmp_path, monkeypatch):
    """The kwargs are compared whole; `detacher` is `detach.fork_detacher`
    read at call time; a detached payload exits 0."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_other_run_paths(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_story", _Forbidden("run_story"))
    monkeypatch.setattr(cli, "dry_run_story", _Forbidden("dry_run_story"))
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_detach_story(story, **kwargs):
        calls.append((story, kwargs))
        return DETACHED_STORY_PAYLOAD

    monkeypatch.setattr(orchestrate, "detach_story", fake_detach_story)
    sentinel = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", sentinel)

    result = _story_run(tmp_path, "Story 3.1", "--detach", "--verify", "X")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(DETACHED_STORY_PAYLOAD)
    ((needle, kwargs),) = calls
    assert kwargs.pop("detacher") is sentinel
    assert (needle, kwargs) == (
        "Story 3.1",
        {
            "repo_dir": tmp_path,
            "base_branch": "main",
            "branch_prefix": STORY_PREFIX,
            "commands": ["X"],
            "allow_no_verification": False,
            "launcher": "bwrap",
            "isolation_warning": None,
        },
    )
    assert sentinel.calls == []


def test_run_help_and_examples_document_the_story_option():
    assert (
        'am run --story "Story 3.1" --branch-prefix m9 --verify "uv run pytest"'
        in cli.RUN_EXAMPLES
    )

    result = runner.invoke(cli.app, ["run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--story" in result.output


# ── --verify-from-env (card 1b938053) ────────────────────────────────────────

FROM_ENV_RUN_ID = "20260923T140506Z-cbe34d00"


def _from_env_run(project: Path, *extra: str, **invoke_kwargs: Any):
    """`am run --verify-from-env --card ...`, the argv `argv_guard.neutralize` builds."""
    return runner.invoke(
        cli.app,
        [
            "run",
            "--verify-from-env",
            "--card",
            VERIFY_CARD_ID,
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m1",
            *extra,
        ],
        **invoke_kwargs,
    )


def _recording_run_card(seen: dict[str, Any]):
    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        seen["env"] = os.environ.get(argv_guard.VERIFY_ENV)
        return _fake_payload(card_id, VERIFY_STORY_ID)

    return fake_run_card


def test_verify_from_env_reaches_run_card(tmp_path, monkeypatch):
    """Spec test 19."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "run_card", _recording_run_card(seen))
    monkeypatch.setenv(argv_guard.VERIFY_ENV, '["a b", "c"]')

    result = _from_env_run(tmp_path)

    assert result.exit_code == 0, result.output
    assert seen["commands"] == ["a b", "c"]


def test_verify_from_env_round_trips_hostile_values_to_run_card(tmp_path, monkeypatch):
    """Review Focus 5: what `neutralize` encodes is exactly what `run_card` gets."""
    values = ["pytest -k 'not slow'", 'echo "q"', "back\\slash", "a\nb", "café", ""]
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "run_card", _recording_run_card(seen))
    _, environ = argv_guard.neutralize(
        ["am", "run", *(token for value in values for token in ("--verify", value))], {}
    )
    monkeypatch.setenv(argv_guard.VERIFY_ENV, environ[argv_guard.VERIFY_ENV])

    result = _from_env_run(tmp_path)

    assert result.exit_code == 0, result.output
    assert seen["commands"] == values


def test_verify_from_env_accepts_an_empty_list(tmp_path, monkeypatch):
    """Review Focus 2: an empty suite is a suite, not a usage error."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "run_card", _recording_run_card(seen))
    monkeypatch.setenv(argv_guard.VERIFY_ENV, "[]")

    result = _from_env_run(tmp_path)

    assert result.exit_code == 0, result.output
    assert seen["commands"] == []


def test_resume_verify_from_env_reaches_resume_run(tmp_path, monkeypatch):
    """Spec test 20; Review Focus 4: the flag sits before the run id, as `neutralize` puts it."""
    seen: dict[str, Any] = {}

    def fake_resume_run(run_id, **kwargs):
        seen["run_id"] = run_id
        seen.update(kwargs)
        seen["env"] = os.environ.get(argv_guard.VERIFY_ENV)
        return {"run_id": run_id, "status": "done"}

    monkeypatch.setattr(cli, "resume_run", fake_resume_run)
    monkeypatch.setenv(argv_guard.VERIFY_ENV, '["a b", "c"]')

    result = runner.invoke(
        cli.app,
        ["resume", "--verify-from-env", FROM_ENV_RUN_ID, "--repo-dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert seen["run_id"] == FROM_ENV_RUN_ID
    assert seen["commands"] == ["a b", "c"]
    assert seen["env"] is None


def test_verify_from_env_with_the_variable_unset_is_a_usage_error(tmp_path, monkeypatch):
    """Spec test 21."""
    monkeypatch.setattr(cli, "run_card", _Forbidden("run_card"))
    monkeypatch.delenv(argv_guard.VERIFY_ENV, raising=False)

    result = _from_env_run(tmp_path)

    assert result.exit_code == 2, result.output
    assert "unset" in result.output


@pytest.mark.parametrize(
    "raw",
    ["not json SECRET", '"SECRET"', '{"SECRET": 1}', '["SECRET", 1]'],
)
def test_verify_from_env_with_a_bad_value_is_a_usage_error_that_never_echoes_it(
    tmp_path, monkeypatch, raw
):
    """Spec test 22."""
    monkeypatch.setattr(cli, "run_card", _Forbidden("run_card"))
    monkeypatch.setenv(argv_guard.VERIFY_ENV, raw)

    result = _from_env_run(tmp_path)

    assert result.exit_code == 2, result.output
    assert "SECRET" not in result.output
    assert argv_guard.VERIFY_ENV not in os.environ


def test_resume_verify_from_env_with_a_bad_value_is_a_usage_error(tmp_path, monkeypatch):
    """Spec test 22, resume half."""
    monkeypatch.setattr(cli, "resume_run", _Forbidden("resume_run"))
    monkeypatch.setenv(argv_guard.VERIFY_ENV, '["SECRET", 1]')

    result = runner.invoke(
        cli.app,
        ["resume", "--verify-from-env", FROM_ENV_RUN_ID, "--repo-dir", str(tmp_path)],
    )

    assert result.exit_code == 2, result.output
    assert "SECRET" not in result.output


def test_verify_from_env_and_verify_together_are_a_usage_error(tmp_path, monkeypatch):
    """Spec test 23."""
    monkeypatch.setattr(cli, "run_card", _Forbidden("run_card"))
    monkeypatch.setenv(argv_guard.VERIFY_ENV, '["a"]')

    result = _from_env_run(tmp_path, "--verify", "x")

    assert result.exit_code == 2, result.output
    assert "exclusive" in result.output


def test_am_verify_json_is_popped_before_run_card_runs(tmp_path, monkeypatch):
    """Spec test 24, with the flag: nothing the run spawns inherits it."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "run_card", _recording_run_card(seen))
    monkeypatch.setenv(argv_guard.VERIFY_ENV, '["a"]')

    result = _from_env_run(tmp_path)

    assert result.exit_code == 0, result.output
    assert seen["env"] is None


def test_a_stray_am_verify_json_is_popped_and_ignored_without_the_flag(tmp_path, monkeypatch):
    """Spec test 24, without the flag; Review Focus 1."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "run_card", _recording_run_card(seen))
    monkeypatch.setenv(argv_guard.VERIFY_ENV, '["stray"]')

    result = _invoke(tmp_path, VERIFY_CARD_ID, "--verify", "mine")

    assert result.exit_code == 0, result.output
    assert seen["commands"] == ["mine"]
    assert seen["env"] is None


@pytest.mark.parametrize("command", [["run"], ["resume"]])
def test_verify_from_env_is_hidden_from_help(command):
    """Spec test 25."""
    result = runner.invoke(cli.app, [*command, "--help"])

    assert result.exit_code == 0, result.output
    assert "--verify" in result.output
    assert "verify-from-env" not in result.output


# ── the `am` entry point and the argv warning (card 1b938053) ────────────────

ARGV_WARNING = argv_guard.ARGV_VISIBLE_WARNING
WARNED = {"argv_warnings": [ARGV_WARNING]}


def _warned_card_args(project: Path, *extra: str) -> list[str]:
    return [
        "run",
        "--card",
        VERIFY_CARD_ID,
        "--repo-dir",
        str(project),
        "--base-branch",
        "main",
        "--branch-prefix",
        "m1",
        *extra,
    ]


def test_the_argv_warning_ends_a_card_payloads_warnings(tmp_path, monkeypatch):
    """Spec test 26, card payload."""
    monkeypatch.setattr(
        cli,
        "run_card",
        lambda card_id, **kwargs: {
            **_fake_payload(card_id, VERIFY_STORY_ID),
            "warnings": ["earlier"],
        },
    )

    result = runner.invoke(cli.app, _warned_card_args(tmp_path), obj=WARNED)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["warnings"] == ["earlier", ARGV_WARNING]


def test_the_argv_warning_reaches_a_detached_hand_off_payload(tmp_path, monkeypatch):
    """Spec test 26, a hand-off payload that had no `warnings` key."""
    hand_off = {"run_id": FROM_ENV_RUN_ID, "pid": 4242, "log": "/tmp/run.log", "detached": True}
    monkeypatch.setattr(cli, "detach_card", lambda card_id, **kwargs: dict(hand_off))

    result = runner.invoke(cli.app, _warned_card_args(tmp_path, "--detach"), obj=WARNED)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {**hand_off, "warnings": [ARGV_WARNING]}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"run_id": FROM_ENV_RUN_ID, "status": "done"}, [ARGV_WARNING]),
        (
            {"run_id": FROM_ENV_RUN_ID, "status": "done", "warnings": ["replaced"]},
            ["replaced", ARGV_WARNING],
        ),
    ],
)
def test_the_argv_warning_ends_a_resume_payloads_warnings(tmp_path, monkeypatch, payload, expected):
    """Spec test 26, resume."""
    monkeypatch.setattr(cli, "resume_run", lambda run_id, **kwargs: dict(payload))

    result = runner.invoke(
        cli.app, ["resume", FROM_ENV_RUN_ID, "--repo-dir", str(tmp_path)], obj=WARNED
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["warnings"] == expected


def test_without_an_obj_payloads_are_unchanged(tmp_path, monkeypatch):
    """Spec test 26: `ctx.obj` is `None` under a CliRunner that passes none."""
    monkeypatch.setattr(
        cli, "run_card", lambda card_id, **kwargs: _fake_payload(card_id, VERIFY_STORY_ID)
    )
    monkeypatch.setattr(
        cli, "resume_run", lambda run_id, **kwargs: {"run_id": run_id, "status": "done"}
    )

    ran = runner.invoke(cli.app, _warned_card_args(tmp_path))
    resumed = runner.invoke(cli.app, ["resume", FROM_ENV_RUN_ID, "--repo-dir", str(tmp_path)])

    assert json.loads(ran.stdout)["data"] == _fake_payload(VERIFY_CARD_ID, VERIFY_STORY_ID)
    assert json.loads(resumed.stdout)["data"] == {"run_id": FROM_ENV_RUN_ID, "status": "done"}


def test_argv_warnings_never_reach_an_error_envelope(tmp_path, monkeypatch):
    """Review Focus 3: error envelopes are unchanged."""

    def failing_run_card(card_id, **kwargs):
        raise cli.CliError("refused")

    monkeypatch.setattr(cli, "run_card", failing_run_card)

    result = runner.invoke(cli.app, _warned_card_args(tmp_path), obj=WARNED)

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert ARGV_WARNING not in result.stdout


@pytest.mark.parametrize(("guard_result", "expected"), [(ARGV_WARNING, [ARGV_WARNING]), (None, [])])
def test_entry_runs_the_guard_first_and_hands_its_warning_to_the_command(
    tmp_path, monkeypatch, capsys, guard_result, expected
):
    """Spec test 27: entry wiring."""
    argv = ["am", *_warned_card_args(tmp_path)]
    guarded: list[list[str]] = []

    def fake_reexec_neutral(seen_argv):
        guarded.append(list(seen_argv))
        return guard_result

    monkeypatch.setattr(argv_guard, "reexec_neutral", fake_reexec_neutral)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(
        cli, "run_card", lambda card_id, **kwargs: _fake_payload(card_id, VERIFY_STORY_ID)
    )

    with pytest.raises(SystemExit) as exited:
        cli.entry()

    assert exited.value.code == 0
    assert guarded == [argv]
    assert json.loads(capsys.readouterr().out)["data"]["warnings"] == expected


def test_the_am_script_targets_entry():
    """Spec test 28."""
    pyproject = Path(agent_manager.__file__).parents[2] / "pyproject.toml"

    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]

    assert scripts["am"] == "agent_manager.cli:entry"


@pytest.mark.parametrize(
    ("target", "seam"),
    [
        (["--story", "S", "--branch-prefix", "m1"], "run_story"),
        (["--story", "S", "--branch-prefix", "m1", "--detach"], "detach_story"),
        (["--milestone", "Milestone 3", "--branch-prefix", "m3"], "run_milestone"),
        (["--milestone", "Milestone 3", "--branch-prefix", "m3", "--detach"], "detach_milestone"),
        (["--board"], "run_board"),
        (["--board", "--detach"], "detach_board"),
        (["--card", VERIFY_CARD_ID, "--branch-prefix", "m1", "--detach"], "detach_card"),
    ],
)
def test_verify_from_env_reaches_every_run_branch(tmp_path, monkeypatch, target, seam):
    """Spec §3.4 item 6: the commands flow everywhere `list(verify)` did."""
    seen: list[tuple[str, Any]] = []

    def recorder(name):
        def fake(*args, **kwargs):
            seen.append((name, kwargs["commands"]))
            return {"run_id": FROM_ENV_RUN_ID}

        return fake

    for name in (
        "run_story",
        "detach_story",
        "run_milestone",
        "detach_milestone",
        "run_board",
        "detach_board",
    ):
        monkeypatch.setattr(orchestrate, name, recorder(name))
    monkeypatch.setattr(cli, "run_card", recorder("run_card"))
    monkeypatch.setattr(cli, "detach_card", recorder("detach_card"))
    monkeypatch.setenv(argv_guard.VERIFY_ENV, '["a b", "c"]')

    result = runner.invoke(
        cli.app,
        ["run", "--verify-from-env", *target, "--repo-dir", str(tmp_path), "--base-branch", "main"],
    )

    assert result.exit_code == 0, result.output
    assert seen == [(seam, ["a b", "c"])]


# ── A5: --isolation, the recorded launcher, resume restore (card 97d4b709) ───

ISOLATION_RUN_ID = "20260923T090000Z-cbe34d00"


def test_the_production_runner_factory_launches_through_the_recorded_mode(
    projection, monkeypatch
):
    """A5 spec test 11: the recorded `RunConfig.launcher` picks the launcher; no probe."""
    _record(
        projection, ISOLATION_RUN_ID, started_at=RECORDED_AT, status="started", launcher="bwrap"
    )
    asked: list[str] = []

    def chosen(argv, *, cwd, timeout, stdout_path):
        raise AssertionError("the factory launched a harness process")

    def fake_get_launcher(kind):
        asked.append(kind)
        return chosen

    monkeypatch.setattr(cli.launcher, "get_launcher", fake_get_launcher)
    monkeypatch.setattr(
        cli.launcher, "default_probe_runner", _Forbidden("launcher.default_probe_runner")
    )
    opened = store_writer.Store.open(projection, ISOLATION_RUN_ID)
    try:
        built = cli.default_runner_factory(
            store=opened, run_id=ISOLATION_RUN_ID, story_id="story-1", card_id="card-1"
        )
    finally:
        opened.close()

    assert asked == ["bwrap"]
    assert isinstance(built, dispatch.AgentRunner)
    assert built.launcher is chosen


def test_the_production_runner_factory_refuses_a_run_with_no_row(projection):
    """A5 B2: never a silent fallback to `direct`."""
    missing = "20260923T090000Z-deadbeef"
    opened = store_writer.Store.open(projection, missing)
    try:
        with pytest.raises(cli.UnknownRunError, match=missing):
            cli.default_runner_factory(
                store=opened, run_id=missing, story_id="story-1", card_id="card-1"
            )
    finally:
        opened.close()


def test_cli_imports_the_launcher_module_and_not_run_direct():
    assert cli.launcher.__name__ == "agent_manager.harness.launcher"
    assert not hasattr(cli, "run_direct")


BWRAP_PROBE_FAILED = f"{' '.join(launcher.wrap_argv('bwrap', ['true'], Path('/')))} exited 1"
"""`probe`'s reason when the bwrap probe exits 1: its exact argv, then the code."""


def _recorded_config(root: Path, run_id: str) -> models.RunConfig:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    return run.config


def test_run_refuses_an_unknown_isolation_mode_as_a_usage_error(tmp_path, monkeypatch):
    """A5 spec test 3."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)
    monkeypatch.setattr(
        cli.launcher, "resolve_isolation", _Forbidden("launcher.resolve_isolation")
    )

    result = _milestone_run(tmp_path, "--isolation", "bogus")

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout


@pytest.mark.parametrize(
    ("given", "requested"),
    [
        ((), "auto"),
        (("--isolation", "auto"), "auto"),
        (("--isolation", "bwrap"), "bwrap"),
        (("--isolation", "unshare"), "unshare"),
        (("--isolation", "none"), "none"),
    ],
)
def test_run_hands_the_isolation_request_to_resolve_isolation(
    tmp_path, monkeypatch, given, requested
):
    """A5 spec test 4: every accepted value reaches `resolve_isolation` as given."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, {**CLEAN_MILESTONE, "warnings": []})
    asked: list[str] = []

    def fake_resolve(request, *, runner=None):
        asked.append(request)
        return launcher.Isolation("bwrap", None)

    monkeypatch.setattr(cli.launcher, "resolve_isolation", fake_resolve)

    result = _milestone_run(tmp_path, *given)

    assert result.exit_code == 0, result.output
    assert asked == [requested]


@pytest.mark.parametrize(
    "target",
    [
        ["--card", VERIFY_CARD_ID, "--branch-prefix", "m1"],
        ["--milestone", "Milestone 3", "--branch-prefix", "m3", "--detach"],
        ["--board"],
    ],
)
def test_an_isolation_mode_this_host_cannot_start_is_refused_before_anything_is_written(
    tmp_path, monkeypatch, target
):
    """A5 spec test 5: exit 3, the probe argv and the way out named, nothing written."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)
    for name in ("run_story", "detach_story", "detach_milestone", "detach_board", "refresh_git"):
        monkeypatch.setattr(orchestrate, name, _Forbidden(name))
    monkeypatch.setattr(cli, "detach_card", _Forbidden("detach_card"))
    monkeypatch.setattr(cli.board, "show", _Forbidden("board.show"))
    monkeypatch.setattr(cli.board, "roots", _Forbidden("board.roots"))
    monkeypatch.setattr(detach, "fork_detacher", _Forbidden("detach.fork_detacher"))
    monkeypatch.setattr(cli.launcher, "default_probe_runner", lambda argv: 1)

    result = runner.invoke(
        cli.app,
        [
            "run",
            *target,
            "--repo-dir",
            str(tmp_path),
            "--base-branch",
            "main",
            "--isolation",
            "bwrap",
        ],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "IsolationUnavailableError"
    assert BWRAP_PROBE_FAILED in envelope["error"]["message"]
    assert "--isolation none" in envelope["error"]["message"]
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.git
def test_a_dry_run_accepts_isolation_and_probes_nothing(project, milestone_board, monkeypatch):
    """A5 spec test 6: the preview is byte-for-byte the plain one, with no warnings."""
    _forbid_writes(monkeypatch)
    plain = _dry_run(project, milestone_board["milestone"])
    monkeypatch.setattr(
        cli.launcher, "resolve_isolation", _Forbidden("launcher.resolve_isolation")
    )
    monkeypatch.setattr(
        cli.launcher, "default_probe_runner", _Forbidden("launcher.default_probe_runner")
    )

    isolated = _dry_run(project, milestone_board["milestone"], "--isolation", "bwrap")

    assert isolated.exit_code == 0, isolated.output
    assert isolated.stdout == plain.stdout
    assert "warnings" not in json.loads(isolated.stdout)["data"]


@pytest.mark.git
@pytest.mark.parametrize(
    ("flag", "probe_exit", "recorded"),
    [
        ("none", None, ("direct", None)),
        ("bwrap", 0, ("bwrap", None)),
        ("auto", 1, ("direct", launcher.ISOLATION_NONE_WARNING)),
    ],
)
def test_a_card_run_records_the_resolved_mode_and_warning(
    project, cards, monkeypatch, flag, probe_exit, recorded
):
    """A5 spec test 7, through a real git worktree."""
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    if probe_exit is None:
        monkeypatch.setattr(
            cli.launcher, "default_probe_runner", _Forbidden("launcher.default_probe_runner")
        )
    else:
        monkeypatch.setattr(cli.launcher, "default_probe_runner", lambda argv: probe_exit)

    result = _invoke(project, cards["subtask"], "--isolation", flag)

    assert result.exit_code == 0, result.output
    run_id = json.loads(result.stdout)["data"]["run_id"]
    config = _recorded_config(project, run_id)
    assert (config.launcher, config.isolation_warning) == recorded


@pytest.mark.parametrize(
    ("target", "seam", "payload", "expected"),
    [
        (
            ["--card", VERIFY_CARD_ID, "--branch-prefix", "m1"],
            "run_card",
            lambda: {**_fake_payload(VERIFY_CARD_ID, VERIFY_STORY_ID), "warnings": ["earlier"]},
            ["earlier", FALLBACK, ARGV_WARNING],
        ),
        (
            ["--card", VERIFY_CARD_ID, "--branch-prefix", "m1", "--detach"],
            "detach_card",
            lambda: {"run_id": FROM_ENV_RUN_ID, "pid": 4242, "log": "/tmp/run.log", "detached": True},
            [FALLBACK, ARGV_WARNING],
        ),
        (
            ["--milestone", "Milestone 3", "--branch-prefix", "m3"],
            "run_milestone",
            lambda: {**CLEAN_MILESTONE, "warnings": []},
            [FALLBACK, ARGV_WARNING],
        ),
        (["--board"], "run_board", lambda: _board_payload("done", "done"), [FALLBACK, ARGV_WARNING]),
    ],
)
def test_the_fallback_warning_reaches_every_run_envelope_once(
    tmp_path, monkeypatch, target, seam, payload, expected
):
    """A5 spec test 8: once, after the run's own warnings, before argv_guard's;
    a board carries it at the top level only."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    owner = cli if seam.endswith("_card") else orchestrate
    monkeypatch.setattr(owner, seam, lambda *args, **kwargs: payload())
    monkeypatch.setattr(cli.launcher, "default_probe_runner", lambda argv: 1)

    result = runner.invoke(
        cli.app,
        ["run", *target, "--repo-dir", str(tmp_path), "--base-branch", "main"],
        obj=WARNED,
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["warnings"] == expected
    assert all("warnings" not in entry for entry in data.get("milestones", []))


@pytest.mark.parametrize(
    ("target", "seam"),
    [
        (["--card", VERIFY_CARD_ID, "--branch-prefix", "m1"], "run_card"),
        (["--card", VERIFY_CARD_ID, "--branch-prefix", "m1", "--detach"], "detach_card"),
        (["--story", "S", "--branch-prefix", "m1"], "run_story"),
        (["--story", "S", "--branch-prefix", "m1", "--detach"], "detach_story"),
        (["--milestone", "Milestone 3", "--branch-prefix", "m3"], "run_milestone"),
        (["--milestone", "Milestone 3", "--branch-prefix", "m3", "--detach"], "detach_milestone"),
        (["--board"], "run_board"),
        (["--board", "--detach"], "detach_board"),
    ],
)
def test_run_hands_the_resolved_mode_and_warning_to_every_run_branch(
    tmp_path, monkeypatch, target, seam
):
    """A5 spec test 9."""
    seen: list[tuple[str, Any, Any]] = []

    def recorder(name):
        def fake(*args, **kwargs):
            seen.append((name, kwargs["launcher"], kwargs["isolation_warning"]))
            return {"run_id": FROM_ENV_RUN_ID, "status": "done"}

        return fake

    for name in (
        "run_story",
        "detach_story",
        "run_milestone",
        "detach_milestone",
        "run_board",
        "detach_board",
    ):
        monkeypatch.setattr(orchestrate, name, recorder(name))
    monkeypatch.setattr(cli, "run_card", recorder("run_card"))
    monkeypatch.setattr(cli, "detach_card", recorder("detach_card"))
    monkeypatch.setattr(
        cli.launcher,
        "resolve_isolation",
        lambda request, *, runner=None: launcher.Isolation("unshare", "a warning"),
    )

    result = runner.invoke(
        cli.app, ["run", *target, "--repo-dir", str(tmp_path), "--base-branch", "main"]
    )

    assert result.exit_code == 0, result.output
    assert seen == [(seam, "unshare", "a warning")]


MILESTONE_ISOLATION_RUN_ID = "20260927T100000Z-cbe34d00"


def _probe_recorder(monkeypatch, exit_code: int) -> list[list[str]]:
    probes: list[list[str]] = []

    def probe_runner(argv):
        probes.append(list(argv))
        return exit_code

    monkeypatch.setattr(cli.launcher, "default_probe_runner", probe_runner)
    return probes


def _forbid_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        cli.launcher, "default_probe_runner", _Forbidden("launcher.default_probe_runner")
    )


def test_a_task_resume_re_probes_the_recorded_mode_and_keeps_it(projection, monkeypatch):
    """A5 spec test 12, task run."""
    _record(
        projection, ISOLATION_RUN_ID, started_at=RECORDED_AT, status="started", launcher="bwrap"
    )
    seen = _task_resume_spy(monkeypatch)
    probes = _probe_recorder(monkeypatch, 0)

    payload = cli.resume_run(ISOLATION_RUN_ID, repo_dir=projection)

    [(run, _kwargs)] = seen
    assert probes == [launcher.wrap_argv("bwrap", ["true"], Path("/"))]
    assert (run.config.launcher, run.config.isolation_warning) == ("bwrap", None)
    assert payload["warnings"] == []


def test_a_milestone_resume_re_probes_the_recorded_mode_and_hands_it_on(
    projection, monkeypatch
):
    """A5 spec test 12, milestone run."""
    _record_milestone(
        projection, MILESTONE_ISOLATION_RUN_ID, status="escalated", launcher="unshare"
    )
    calls = _milestone_resume_spy(monkeypatch)
    probes = _probe_recorder(monkeypatch, 0)

    cli.resume_run(MILESTONE_ISOLATION_RUN_ID, repo_dir=projection)

    assert probes == [launcher.wrap_argv("unshare", ["true"], Path("/"))]
    [kwargs] = calls
    assert (kwargs["launcher"], kwargs["isolation_warning"]) == ("unshare", None)


@pytest.mark.parametrize("workflow", ["task", "milestone"])
def test_a_resume_whose_recorded_mode_cannot_start_is_refused_and_writes_nothing(
    projection, monkeypatch, workflow
):
    """A5 spec test 13: never silently un-isolated; row, config, journal, lease untouched."""
    run_id = ISOLATION_RUN_ID if workflow == "task" else MILESTONE_ISOLATION_RUN_ID
    if workflow == "task":
        _record(projection, run_id, started_at=RECORDED_AT, status="started", launcher="bwrap")
    else:
        _record_milestone(projection, run_id, status="escalated", launcher="bwrap")
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))
    monkeypatch.setattr(cli.launcher, "default_probe_runner", lambda argv: 1)
    before = (
        _runs_snapshot(),
        _recorded_config(projection, run_id),
        len(_journal_lines(run_id)),
    )

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "IsolationUnavailableError"
    assert BWRAP_PROBE_FAILED in envelope["error"]["message"]
    assert "--isolation none" in envelope["error"]["message"]
    after = (
        _runs_snapshot(),
        _recorded_config(projection, run_id),
        len(_journal_lines(run_id)),
    )
    assert after == before
    conn = store_db.open_db(cli.resolve_repo_dir(projection))
    try:
        assert store_leases.read_lease(conn, run_id) is None
    finally:
        conn.close()


def test_resume_isolation_none_runs_an_isolated_task_run_direct_without_probing(
    projection, monkeypatch
):
    """A5 spec test 14, task run."""
    _record(
        projection, ISOLATION_RUN_ID, started_at=RECORDED_AT, status="started", launcher="bwrap"
    )
    seen = _task_resume_spy(monkeypatch)
    _forbid_probe(monkeypatch)

    payload = cli.resume_run(ISOLATION_RUN_ID, repo_dir=projection, isolation="none")

    [(run, _kwargs)] = seen
    assert (run.config.launcher, run.config.isolation_warning) == ("direct", None)
    assert payload["warnings"] == []


def test_resume_isolation_none_hands_direct_to_a_milestone_resume(projection, monkeypatch):
    """A5 spec test 14, milestone run, through the CLI flag."""
    _record_milestone(
        projection, MILESTONE_ISOLATION_RUN_ID, status="escalated", launcher="bwrap"
    )
    calls = _milestone_resume_spy(monkeypatch)
    _forbid_probe(monkeypatch)

    result = runner.invoke(
        cli.app,
        [
            "resume",
            MILESTONE_ISOLATION_RUN_ID,
            "--repo-dir",
            str(projection),
            "--isolation",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    [kwargs] = calls
    assert (kwargs["launcher"], kwargs["isolation_warning"]) == ("direct", None)


def test_resume_isolation_none_clears_a_recorded_fallback_warning(projection, monkeypatch):
    """Review Focus 3: an explicit opt-out is not a fallback, so nothing is warned."""
    _record(
        projection,
        ISOLATION_RUN_ID,
        started_at=RECORDED_AT,
        status="started",
        isolation_warning=FALLBACK,
    )
    seen = _task_resume_spy(monkeypatch)
    _forbid_probe(monkeypatch)

    payload = cli.resume_run(ISOLATION_RUN_ID, repo_dir=projection, isolation="none")

    [(run, _kwargs)] = seen
    assert (run.config.launcher, run.config.isolation_warning) == ("direct", None)
    assert payload["warnings"] == []


def test_a_resume_of_an_auto_fallback_run_keeps_its_warning_before_the_replaced_suite(
    projection, monkeypatch
):
    """A5 spec test 15 and Review Focus 2: no probe, no silent upgrade, warning kept."""
    _record(
        projection,
        ISOLATION_RUN_ID,
        started_at=RECORDED_AT,
        status="started",
        isolation_warning=FALLBACK,
    )
    seen = _task_resume_spy(monkeypatch)
    _forbid_probe(monkeypatch)

    payload = cli.resume_run(
        ISOLATION_RUN_ID, repo_dir=projection, commands=("uv run pytest",)
    )

    [(run, _kwargs)] = seen
    assert (run.config.launcher, run.config.isolation_warning) == ("direct", FALLBACK)
    assert payload["warnings"] == [FALLBACK, "verification: replaced in run record: []"]


@pytest.mark.parametrize("value", ["bwrap", "unshare", "auto", "bogus"])
def test_resume_isolation_accepts_only_none(tmp_path, monkeypatch, value):
    """A5 spec test 16: upgrading a run's isolation on resume is out of scope."""
    monkeypatch.setattr(cli, "resume_run", _Forbidden("resume_run"))

    result = runner.invoke(
        cli.app,
        ["resume", FROM_ENV_RUN_ID, "--repo-dir", str(tmp_path), "--isolation", value],
    )

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout


def test_resume_without_isolation_hands_none_to_resume_run(tmp_path, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_resume_run(run_id, **kwargs):
        seen.update(kwargs)
        return {"run_id": run_id, "status": "done"}

    monkeypatch.setattr(cli, "resume_run", fake_resume_run)

    result = runner.invoke(cli.app, ["resume", FROM_ENV_RUN_ID, "--repo-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert seen["isolation"] is None


# ── --harness-timeout parsing (card 33dc5549) ───────────────────────────────
#
# Unit tier: the parse helper is pure.

TIMEOUT_HINT = "'--harness-timeout'"
TASK_PHASES_TEXT = "explore, spec, validate_spec, plan, validate_plan, implement, review"


def _parse(*values: str, phases=None):
    return cli.parse_harness_timeouts(
        list(values), phases=cli.TASK_AGENT_PHASES if phases is None else phases
    )


def test_the_agent_phase_sets_are_the_declared_workflows_agent_phases():
    assert cli.TASK_AGENT_PHASES == (
        "explore",
        "spec",
        "validate_spec",
        "plan",
        "validate_plan",
        "implement",
        "review",
    )
    assert "worktree" not in cli.TASK_AGENT_PHASES  # a Step, not an AgentPhase
    assert cli.MILESTONE_AGENT_PHASES == (*cli.TASK_AGENT_PHASES, "resolve")
    assert cli.agent_phase_names(task_workflow.TASK) == cli.TASK_AGENT_PHASES
    assert cli.agent_phase_names(task_workflow.TASK, task_workflow.TASK) == (
        cli.TASK_AGENT_PHASES
    )


def test_harness_timeout_parse_no_flag_is_none_and_empty():
    assert _parse() == (None, {})


@pytest.mark.parametrize(
    ("value", "seconds"), [("600", 600.0), ("600.5", 600.5), ("1e3", 1000.0)]
)
def test_harness_timeout_parse_bare(value, seconds):
    assert _parse(value) == (seconds, {})


def test_harness_timeout_parse_per_phase():
    assert _parse("spec=600") == (None, {"spec": 600.0})


def test_harness_timeout_parse_repeated_mixes_bare_and_phases():
    assert _parse("spec=600", "900", "implement=3600") == (
        900.0,
        {"spec": 600.0, "implement": 3600.0},
    )


def test_harness_timeout_parse_is_order_independent():
    assert _parse("spec=600", "900") == _parse("900", "spec=600") == (900.0, {"spec": 600.0})


@pytest.mark.parametrize("value", ["60", "60.0", "86400"])
def test_harness_timeout_parse_accepts_the_bounds(value):
    assert _parse(value) == (float(value), {})
    assert _parse(f"review={value}") == (None, {"review": float(value)})


def test_harness_timeout_parse_accepts_resolve_only_with_the_milestone_phases():
    assert _parse("resolve=600", phases=cli.MILESTONE_AGENT_PHASES) == (
        None,
        {"resolve": 600.0},
    )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ([""], "'' has an empty value; expected [PHASE=]SECONDS"),
        (["spec="], "'spec=' has an empty value; expected [PHASE=]SECONDS"),
        (["abc"], "'abc': 'abc' is not a number of seconds"),
        (["spec=abc"], "'spec=abc': 'abc' is not a number of seconds"),
        (["spec=600=1"], "'spec=600=1': '600=1' is not a number of seconds"),
        (["nan"], "'nan': 'nan' is not a finite number of seconds"),
        (["inf"], "'inf': 'inf' is not a finite number of seconds"),
        (["-inf"], "'-inf': '-inf' is not a finite number of seconds"),
        (["spec=nan"], "'spec=nan': 'nan' is not a finite number of seconds"),
        (["59"], "'59': seconds must be from 60 to 86400 inclusive"),
        (["59.9"], "'59.9': seconds must be from 60 to 86400 inclusive"),
        (["86401"], "'86401': seconds must be from 60 to 86400 inclusive"),
        (["0"], "'0': seconds must be from 60 to 86400 inclusive"),
        (["-5"], "'-5': seconds must be from 60 to 86400 inclusive"),
        (["spec=59"], "'spec=59': seconds must be from 60 to 86400 inclusive"),
        (["=600"], "'=600' has an empty phase name; expected PHASE=SECONDS"),
        (
            ["bogus=600"],
            f"'bogus=600': unknown phase 'bogus'; the agent phases are: {TASK_PHASES_TEXT}",
        ),
        (
            ["Spec=600"],
            f"'Spec=600': unknown phase 'Spec'; the agent phases are: {TASK_PHASES_TEXT}",
        ),
        (
            [" spec=600"],
            f"' spec=600': unknown phase ' spec'; the agent phases are: {TASK_PHASES_TEXT}",
        ),
        (
            ["resolve=600"],
            f"'resolve=600': unknown phase 'resolve'; the agent phases are: {TASK_PHASES_TEXT}",
        ),
        (["600", "600"], "the run default is given twice; give one bare SECONDS"),
        (["600", "900"], "the run default is given twice; give one bare SECONDS"),
        (["spec=600", "spec=900"], "phase 'spec' is given twice"),
    ],
)
def test_harness_timeout_parse_refuses_each_bad_value(values, message):
    with pytest.raises(typer.BadParameter) as caught:
        _parse(*values)

    assert caught.value.message == message
    assert caught.value.param_hint == TIMEOUT_HINT


def test_harness_timeout_parse_lists_the_milestone_phases_in_declared_order():
    with pytest.raises(typer.BadParameter) as caught:
        _parse("bogus=600", phases=cli.MILESTONE_AGENT_PHASES)

    assert caught.value.message == (
        f"'bogus=600': unknown phase 'bogus'; the agent phases are: {TASK_PHASES_TEXT}, resolve"
    )


# ── harness timeouts reach the card run's RunConfig (card 33dc5549) ─────────
#
# Unit tier: FakeBoard and a plain repo dir, as the preflight seam tests above.


def test_preflight_card_records_the_harness_timeouts(tmp_path, monkeypatch, fake_board):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)

    pre = cli.preflight_card(
        cards["subtask"],
        repo_dir=root,
        branch_prefix="m1",
        base_branch="main",
        clock=lambda: SEAM_AT,
        harness_timeout=900.0,
        harness_timeouts={"implement": 3600.0},
    )
    default = cli.preflight_card(
        cards["subtask"],
        repo_dir=root,
        branch_prefix="m1",
        base_branch="main",
        clock=lambda: SEAM_AT,
    )

    assert pre.run_record.config.harness_timeout == 900.0
    assert pre.run_record.config.harness_timeouts == {"implement": 3600.0}
    assert default.run_record.config.harness_timeout is None
    assert default.run_record.config.harness_timeouts == {}


class _HaltAtPreflight(Exception):
    """Raised by a recording preflight, so the run under test stops there."""


@pytest.mark.parametrize("entry", ["run_card", "detach_card"])
def test_card_entry_points_forward_harness_timeouts_to_preflight_card(
    tmp_path, monkeypatch, entry
):
    seen: list[dict[str, Any]] = []

    def preflight(card_id, **kwargs):
        seen.append(kwargs)
        raise _HaltAtPreflight

    monkeypatch.setattr(cli, "preflight_card", preflight)
    extra = {"detacher": _Forbidden("detacher")} if entry == "detach_card" else {}

    with pytest.raises(_HaltAtPreflight):
        getattr(cli, entry)(
            VERIFY_CARD_ID,
            repo_dir=tmp_path,
            branch_prefix="m1",
            harness_timeout=900.0,
            harness_timeouts={"implement": 3600.0},
            **extra,
        )

    (kwargs,) = seen
    assert kwargs["harness_timeout"] == 900.0
    assert kwargs["harness_timeouts"] == {"implement": 3600.0}


# ── --harness-timeout on run and resume (card 33dc5549) ─────────────────────
#
# Unit tier: every run entry point is a recorder or `_Forbidden`; no board,
# store or process is reached.

RUN_ENTRY_POINTS = (
    (cli, "run_card"),
    (cli, "detach_card"),
    (orchestrate, "run_milestone"),
    (orchestrate, "detach_milestone"),
    (orchestrate, "run_story"),
    (orchestrate, "detach_story"),
    (orchestrate, "run_board"),
    (orchestrate, "detach_board"),
)
PREVIEWS = ("dry_run_milestone", "dry_run_story", "dry_run_board")
TIMEOUT_FLAGS = ("--harness-timeout=900", "--harness-timeout=implement=3600")
RESUME_RUN_ID = "20260923T140506Z-cbe34d00"


def _record_run_entry_points(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for module, name in RUN_ENTRY_POINTS:

        def record(*args: Any, _name: str = name, **kwargs: Any) -> dict[str, Any]:
            calls.append((_name, kwargs))
            return {"status": "done"}

        monkeypatch.setattr(module, name, record)
    for name in PREVIEWS:
        monkeypatch.setattr(cli, name, _Forbidden(name))
    return calls


def _forbid_every_run_path(monkeypatch) -> None:
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli.board, "show", _Forbidden("board.show"))
    monkeypatch.setattr(cli.board, "roots", _Forbidden("board.roots"))
    for module, name in RUN_ENTRY_POINTS:
        monkeypatch.setattr(module, name, _Forbidden(name))
    for name in PREVIEWS:
        monkeypatch.setattr(cli, name, _Forbidden(name))


def _run_with(tmp_path: Path, targets: list[str], *flags: str):
    return runner.invoke(
        cli.app,
        ["run", *targets, "--repo-dir", str(tmp_path), "--branch-prefix", "m3", *flags],
    )


TIMEOUT_TARGETS = [
    pytest.param(["--card", VERIFY_CARD_ID], "run_card", id="card"),
    pytest.param(["--detach", "--card", VERIFY_CARD_ID], "detach_card", id="detach-card"),
    pytest.param(["--milestone", "M"], "run_milestone", id="milestone"),
    pytest.param(["--detach", "--milestone", "M"], "detach_milestone", id="detach-milestone"),
    pytest.param(["--story", "S"], "run_story", id="story"),
    pytest.param(["--detach", "--story", "S"], "detach_story", id="detach-story"),
    pytest.param(["--board"], "run_board", id="board"),
    pytest.param(["--detach", "--board"], "detach_board", id="detach-board"),
]


@pytest.mark.parametrize(("targets", "entry"), TIMEOUT_TARGETS)
def test_run_passes_harness_timeouts_through(tmp_path, monkeypatch, targets, entry):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _record_run_entry_points(monkeypatch)

    result = _run_with(tmp_path, targets, *TIMEOUT_FLAGS)

    assert result.exit_code == 0, result.output
    ((name, kwargs),) = calls
    assert name == entry
    assert kwargs["harness_timeout"] == 900.0
    assert kwargs["harness_timeouts"] == {"implement": 3600.0}


@pytest.mark.parametrize(("targets", "entry"), TIMEOUT_TARGETS)
def test_run_without_harness_timeout_passes_no_new_keys(tmp_path, monkeypatch, targets, entry):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _record_run_entry_points(monkeypatch)

    result = _run_with(tmp_path, targets)

    assert result.exit_code == 0, result.output
    ((name, kwargs),) = calls
    assert name == entry
    assert "harness_timeout" not in kwargs
    assert "harness_timeouts" not in kwargs


@pytest.mark.parametrize(
    "values",
    [
        [""],
        ["abc"],
        ["spec=abc"],
        ["spec=600=1"],
        ["nan"],
        ["inf"],
        ["-inf"],
        ["spec=nan"],
        ["59"],
        ["59.9"],
        ["86401"],
        ["0"],
        ["-5"],
        ["spec=59"],
        ["=600"],
        ["spec="],
        ["600", "600"],
        ["600", "900"],
        ["spec=600", "spec=900"],
    ],
)
def test_run_refuses_a_bad_harness_timeout_with_exit_2_and_calls_nothing(
    tmp_path, monkeypatch, values
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_every_run_path(monkeypatch)

    result = _run_with(
        tmp_path,
        ["--card", VERIFY_CARD_ID],
        *(f"--harness-timeout={value}" for value in values),
    )

    assert result.exit_code == 2, result.output
    assert "--harness-timeout" in result.output
    assert '"ok"' not in result.stdout
    assert not (paths.data_dir() / "runs").exists()


@pytest.mark.parametrize(
    "targets", [["--card", VERIFY_CARD_ID], ["--story", "S"]], ids=["card", "story"]
)
def test_run_refuses_an_unknown_phase_and_lists_the_agent_phases(tmp_path, monkeypatch, targets):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_every_run_path(monkeypatch)

    result = _run_with(tmp_path, targets, "--harness-timeout=resolve=600")

    assert result.exit_code == 2, result.output
    assert "--harness-timeout" in result.output
    for phase in cli.TASK_AGENT_PHASES:
        assert phase in result.output
    assert not (paths.data_dir() / "runs").exists()


@pytest.mark.parametrize(
    ("targets", "entry"),
    [(["--milestone", "M"], "run_milestone"), (["--board"], "run_board")],
    ids=["milestone", "board"],
)
def test_milestone_and_board_runs_accept_resolve(tmp_path, monkeypatch, targets, entry):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _record_run_entry_points(monkeypatch)

    result = _run_with(tmp_path, targets, "--harness-timeout=resolve=600")

    assert result.exit_code == 0, result.output
    ((name, kwargs),) = calls
    assert name == entry
    assert kwargs["harness_timeouts"] == {"resolve": 600.0}
    assert kwargs["harness_timeout"] is None


def test_a_milestone_run_lists_resolve_among_the_agent_phases(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_every_run_path(monkeypatch)

    result = _run_with(tmp_path, ["--milestone", "M"], "--harness-timeout=bogus=600")

    assert result.exit_code == 2, result.output
    for phase in cli.MILESTONE_AGENT_PHASES:
        assert phase in result.output


@pytest.mark.parametrize(
    ("targets", "preview"),
    [
        (["--milestone", "M"], "dry_run_milestone"),
        (["--story", "S"], "dry_run_story"),
        (["--board"], "dry_run_board"),
    ],
    ids=["milestone", "story", "board"],
)
def test_run_dry_run_ignores_a_valid_harness_timeout(tmp_path, monkeypatch, targets, preview):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_every_run_path(monkeypatch)
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def record(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return {"levels": [], "already_done": []}

    monkeypatch.setattr(cli, preview, record)

    plain = _run_with(tmp_path, [*targets, "--dry-run"])
    timed = _run_with(tmp_path, [*targets, "--dry-run"], *TIMEOUT_FLAGS)

    assert plain.exit_code == 0, plain.output
    assert timed.exit_code == 0, timed.output
    assert timed.stdout == plain.stdout
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert not any(key.startswith("harness") for key in calls[1][1])
    assert not (paths.data_dir() / "runs").exists()


@pytest.mark.parametrize("value", ["abc", "59", "bogus=600"])
def test_run_dry_run_still_refuses_a_malformed_harness_timeout(tmp_path, monkeypatch, value):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_every_run_path(monkeypatch)

    result = _run_with(
        tmp_path, ["--milestone", "M", "--dry-run"], f"--harness-timeout={value}"
    )

    assert result.exit_code == 2, result.output
    assert "--harness-timeout" in result.output


def test_a_bad_run_target_is_refused_before_the_harness_timeout(tmp_path, monkeypatch):
    """B4: `_check_run_targets` runs first, so its error is unchanged."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_every_run_path(monkeypatch)

    result = _run_with(
        tmp_path, ["--card", VERIFY_CARD_ID, "--milestone", "M"], "--harness-timeout=abc"
    )

    assert result.exit_code == 2, result.output
    assert "both" in result.output


def test_resume_accepts_and_validates_harness_timeout(tmp_path, monkeypatch):
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_resume_run(run_id, **kwargs):
        calls.append((run_id, kwargs))
        return {"run_id": run_id, "status": "done"}

    monkeypatch.setattr(cli, "resume_run", fake_resume_run)
    base = ["resume", RESUME_RUN_ID, "--repo-dir", str(tmp_path)]

    plain = runner.invoke(cli.app, base)
    timed = runner.invoke(cli.app, [*base, *TIMEOUT_FLAGS, "--harness-timeout=resolve=600"])

    assert plain.exit_code == 0, plain.output
    assert timed.exit_code == 0, timed.output
    plain_call = (
        RESUME_RUN_ID,
        {
            "repo_dir": tmp_path,
            "allow_no_verification": False,
            "commands": [],
            "isolation": None,
        },
    )
    assert calls[0] == plain_call
    assert calls[1] == (
        RESUME_RUN_ID,
        {
            **plain_call[1],
            "harness_override": (900.0, {"implement": 3600.0, "resolve": 600.0}),
        },
    )


@pytest.mark.parametrize("values", [["59"], ["bogus=600"], ["600", "900"], ["nan"]])
def test_resume_refuses_a_bad_harness_timeout_with_exit_2_and_loads_nothing(
    tmp_path, monkeypatch, values
):
    monkeypatch.setattr(cli, "resume_run", _Forbidden("resume_run"))
    monkeypatch.setattr(store_db, "open_db", _Forbidden("store_db.open_db"))

    result = runner.invoke(
        cli.app,
        [
            "resume",
            RESUME_RUN_ID,
            "--repo-dir",
            str(tmp_path),
            *(f"--harness-timeout={value}" for value in values),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--harness-timeout" in result.output
    assert '"ok"' not in result.stdout
    if values == ["bogus=600"]:
        for phase in cli.MILESTONE_AGENT_PHASES:
            assert phase in result.output


def test_the_harness_timeout_help_texts():
    run_option = inspect.signature(cli.run).parameters["harness_timeout_values"].default
    resume_option = inspect.signature(cli.resume).parameters["harness_timeout_values"].default

    assert run_option.metavar == "[PHASE=]SECONDS"
    assert run_option.help == (
        "The harness timeout in seconds (60 to 86400), repeatable: a bare value is the "
        "run's default, PHASE=SECONDS overrides one agent phase. Recorded with the run. "
        "Ignored with --dry-run."
    )
    assert resume_option.metavar == "[PHASE=]SECONDS"
    assert resume_option.help == (
        "The harness timeout in seconds (60 to 86400), repeatable: a bare value is the "
        "run's default, PHASE=SECONDS overrides one agent phase. Replaces both recorded "
        "values and is recorded; without it the run keeps the timeouts it was started with."
    )


# ── recorded harness timeouts reach the runner (card eee43099) ──────────────
#
# Unit tier: an `AgentRunner` is built but never called, so nothing launches.

BOUND_IDS = {"run_id": "run-1", "story_id": "story-1", "card_id": "card-1"}


def _recorded_direct_store() -> Any:
    """A stand-in store whose projection holds only `run-1`'s config, recorded
    `direct`: all `default_runner_factory` reads to pick the launcher."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, config TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO runs (id, config) VALUES (?, ?)",
        (BOUND_IDS["run_id"], models.RunConfig().model_dump_json()),
    )
    return SimpleNamespace(connection=conn)


def _bound_runner(factory: Any) -> Any:
    """The runner `factory` builds for a store read only for the launcher."""
    return factory(store=_recorded_direct_store(), **BOUND_IDS)


def test_runner_factory_for_returns_an_injected_factory_unchanged():
    def injected(**kwargs: Any) -> Any:
        return None

    timed = models.RunConfig(harness_timeout=900.0, harness_timeouts={"implement": 3600.0})

    assert cli.runner_factory_for(timed, injected) is injected
    assert cli.runner_factory_for(models.RunConfig(), injected) is injected


def test_runner_factory_for_is_none_when_the_config_sets_no_timeout():
    assert cli.runner_factory_for(models.RunConfig(), None) is None


def test_runner_factory_for_binds_the_recorded_timeouts():
    config = models.RunConfig(harness_timeout=900.0, harness_timeouts={"implement": 3600.0})

    factory = cli.runner_factory_for(config, None)

    assert factory is not None
    runner = _bound_runner(factory)
    assert isinstance(runner, dispatch.AgentRunner)
    assert (runner.run_id, runner.story_id, runner.card_id) == ("run-1", "story-1", "card-1")
    assert runner.timeout_for("implement") == 3600.0
    assert runner.timeout_for("plan") == 900.0


def test_runner_factory_for_binds_a_per_phase_map_alone():
    """Review Focus 1: no run default, one override -- still bound."""
    config = models.RunConfig(harness_timeouts={"implement": 600.0})

    runner = _bound_runner(cli.runner_factory_for(config, None))

    assert runner.timeout_for("implement") == 600.0
    assert runner.timeout_for("plan") == dispatch.DEFAULT_TIMEOUT


def test_runner_factory_for_reads_default_runner_factory_at_call_time(monkeypatch):
    config = models.RunConfig(harness_timeout=900.0, harness_timeouts={"implement": 3600.0})
    factory = cli.runner_factory_for(config, None)
    seen: list[dict[str, Any]] = []

    def recording(**kwargs: Any) -> str:
        seen.append(kwargs)
        return "patched runner"

    monkeypatch.setattr(cli, "default_runner_factory", recording)
    store = object()

    assert factory(store=store, **BOUND_IDS) == "patched runner"
    assert seen == [
        {
            "store": store,
            **BOUND_IDS,
            "harness_timeout": 900.0,
            "harness_timeouts": {"implement": 3600.0},
        }
    ]


def test_default_runner_factory_without_timeouts_keeps_the_default():
    runner = cli.default_runner_factory(store=_recorded_direct_store(), **BOUND_IDS)

    assert runner.harness_timeout is None
    assert runner.harness_timeouts == {}
    assert runner.timeout_for("explore") == dispatch.DEFAULT_TIMEOUT


def _card_engine_factory(root: Path, cards: dict[str, str], monkeypatch, **kwargs: Any) -> Any:
    """Run `run_card_engine` on a freshly recorded card run and return the
    `runner_factory` it handed `drive_subtask_async`. `kwargs` go to
    `preflight_card` except `runner_factory`, which goes to the engine."""
    injected = kwargs.pop("runner_factory", None)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _done_drive(calls))
    pre = cli.preflight_card(
        cards["subtask"],
        repo_dir=root,
        branch_prefix="m1",
        base_branch="main",
        clock=lambda: SEAM_AT,
        **kwargs,
    )
    with cli.recorded_card_run(pre) as recorded:
        asyncio.run(
            cli.run_card_engine(
                pre, recorded, runner_factory=injected, control_interval=CONTROL_TICK
            )
        )
    (call,) = calls
    return call["runner_factory"]


def test_run_card_engine_hands_on_no_factory_when_no_timeout_is_recorded(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)

    assert _card_engine_factory(root, cards, monkeypatch) is None


def test_run_card_engine_binds_the_recorded_harness_timeouts(tmp_path, monkeypatch, fake_board):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)

    factory = _card_engine_factory(
        root, cards, monkeypatch, harness_timeout=2.0, harness_timeouts={"review": 5.0}
    )

    runner = _bound_runner(factory)
    assert runner.timeout_for("explore") == 2.0
    assert runner.timeout_for("review") == 5.0


def test_run_card_engine_hands_an_injected_factory_on_unchanged(
    tmp_path, monkeypatch, fake_board
):
    """Review Focus 2: the test seam wins over a recorded timeout."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)

    def injected(*, store: Any, run_id: str, story_id: str, card_id: str) -> Any:
        return None

    factory = _card_engine_factory(
        root, cards, monkeypatch, harness_timeout=2.0, runner_factory=injected
    )

    assert factory is injected


def _timed_task_run(root: Path, cards: dict[str, str], **timeouts: Any) -> str:
    """A recorded `task` run, its subtask `started` and parked before `plan`,
    with its lease released: what `am resume` finds after a kill."""
    pre = cli.preflight_card(
        cards["subtask"],
        repo_dir=root,
        branch_prefix="m1",
        base_branch="main",
        clock=lambda: SEAM_AT,
        **timeouts,
    )
    with cli.recorded_card_run(pre) as recorded:
        _saved(recorded.store, cards["subtask"], "parked", queue=("plan",))
    return pre.run_id


def _config_drive(calls: list[dict[str, Any]], configs: list[models.RunConfig]):
    """A fake `drive_subtask_async` that records its keywords and the run's
    recorded config at the moment it is driven, then finishes `done`."""

    async def drive(**kwargs: Any) -> cli.SubtaskDrive:
        calls.append(kwargs)
        run = kwargs["store"].load_run(kwargs["run_id"])
        configs.append(run.config)
        return cli.SubtaskDrive(summary=SubtaskSummary(status="done"), warnings=[])

    return drive


def _resume_task(root: Path, run_id: str, **extra: Any) -> dict[str, Any]:
    return cli._resume_from_checkpoint(
        _loaded(root, run_id),
        root=root,
        allow_no_verification=False,
        commands=(),
        runner_factory=None,
        control_interval=CONTROL_TICK,
        **extra,
    )


def test_a_resumed_task_run_keeps_its_recorded_harness_timeout(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    run_id = _timed_task_run(root, cards, harness_timeout=2.0)
    calls: list[dict[str, Any]] = []
    configs: list[models.RunConfig] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _config_drive(calls, configs))

    payload = _resume_task(root, run_id)

    assert payload["status"] == "done"
    (call,) = calls
    assert call["resume_from"] is not None
    assert _bound_runner(call["runner_factory"]).timeout_for("plan") == 2.0
    (started,) = configs
    assert (started.harness_timeout, started.harness_timeouts) == (2.0, {})
    final = _loaded(root, run_id).config
    assert (final.harness_timeout, final.harness_timeouts) == (2.0, {})


def test_a_resumed_task_run_without_a_timeout_hands_on_no_factory(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    run_id = _timed_task_run(root, cards)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _config_drive(calls, []))

    _resume_task(root, run_id)

    (call,) = calls
    assert call["runner_factory"] is None


@pytest.mark.parametrize(
    ("override", "expected", "implement", "plan"),
    [
        ((900.0, {}), (900.0, {}), 900.0, 900.0),
        ((None, {"plan": 120.0}), (None, {"plan": 120.0}), dispatch.DEFAULT_TIMEOUT, 120.0),
    ],
)
def test_a_task_resume_override_replaces_both_values_and_is_recorded(
    tmp_path, monkeypatch, fake_board, override, expected, implement, plan
):
    """Review Focus 3 and 4: replaced wholesale, in the `started` record and
    in the final one a later resume loads."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    run_id = _timed_task_run(
        root, cards, harness_timeout=600.0, harness_timeouts={"implement": 3600.0}
    )
    calls: list[dict[str, Any]] = []
    configs: list[models.RunConfig] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _config_drive(calls, configs))

    _resume_task(root, run_id, harness_override=override)

    (call,) = calls
    runner = _bound_runner(call["runner_factory"])
    assert runner.timeout_for("implement") == implement
    assert runner.timeout_for("plan") == plan
    (started,) = configs
    assert (started.harness_timeout, started.harness_timeouts) == expected
    final = _loaded(root, run_id).config
    assert (final.harness_timeout, final.harness_timeouts) == expected


# ── am resume --harness-timeout replaces and is recorded (card eee43099) ────
#
# Unit tier: the projection is written directly; `_resume_from_checkpoint`,
# `orchestrate.run_milestone` and `Store.open` are recorders or `_Forbidden`.

TASK_RUN_ID = "20260923T090000Z-cbe34d00"
OVERRIDE = (900.0, {"implement": 600.0})


def test_resume_run_hands_a_harness_override_to_the_task_path(projection, monkeypatch):
    _record(projection, TASK_RUN_ID, started_at=RECORDED_AT, status="started")
    seen: list[dict[str, Any]] = []

    def fake_resume(run, **kwargs):
        seen.append(kwargs)
        return {"status": "done"}

    monkeypatch.setattr(cli, "_resume_from_checkpoint", fake_resume)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))

    cli.resume_run(TASK_RUN_ID, repo_dir=projection, harness_override=OVERRIDE)

    (kwargs,) = seen
    assert kwargs["harness_override"] == OVERRIDE
    assert kwargs["runner_factory"] is None


def test_resume_run_hands_a_harness_override_to_the_milestone_path(projection, monkeypatch):
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="escalated")
    calls: list[dict[str, Any]] = []

    def fake_run_milestone(milestone, **kwargs):
        calls.append(kwargs)
        return {"done": True, "run_id": run_id, "resumed": True}

    monkeypatch.setattr(orchestrate, "run_milestone", fake_run_milestone)
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    override = (None, {"resolve": 600.0})

    cli.resume_run(run_id, repo_dir=projection, harness_override=override)

    (kwargs,) = calls
    assert kwargs["harness_override"] == override
    assert kwargs["resume_run_id"] == run_id


@pytest.mark.parametrize("shape", ["task", "story"])
def test_resume_run_refuses_a_phase_the_run_cannot_dispatch(projection, monkeypatch, shape):
    """`resolve` is Integrate's: a `task` run and a story run never reach it."""
    if shape == "task":
        _record(projection, TASK_RUN_ID, started_at=RECORDED_AT, status="started")
    else:
        _record(
            projection,
            TASK_RUN_ID,
            started_at=RECORDED_AT,
            status="escalated",
            workflow="milestone",
            milestone_id="milestone-1",
            story_id="story-1",
        )
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))
    monkeypatch.setattr(store_writer.Store, "open", _Forbidden("Store.open"))

    with pytest.raises(typer.BadParameter) as caught:
        cli.resume_run(
            TASK_RUN_ID, repo_dir=projection, harness_override=(None, {"resolve": 600.0})
        )

    assert caught.value.param_hint == "'--harness-timeout'"
    message = str(caught.value)
    assert "'resolve'" in message
    assert ", ".join(cli.TASK_AGENT_PHASES) in message
    assert ", ".join(cli.MILESTONE_AGENT_PHASES) not in message


def test_resume_run_accepts_resolve_on_a_milestone_run(projection, monkeypatch):
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="escalated")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        orchestrate, "run_milestone", lambda milestone, **kwargs: calls.append(kwargs) or {}
    )

    cli.resume_run(run_id, repo_dir=projection, harness_override=(None, {"resolve": 600.0}))

    assert [kwargs["harness_override"] for kwargs in calls] == [(None, {"resolve": 600.0})]


@pytest.mark.parametrize("status", [None, "canceled"])
def test_resume_run_refuses_an_unknown_or_canceled_run_before_judging_the_override(
    projection, monkeypatch, status
):
    """Review Focus 5: the existing refusal comes first and nothing is recorded."""
    if status is not None:
        _record(projection, TASK_RUN_ID, started_at=RECORDED_AT, status=status)
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    expected = cli.UnknownRunError if status is None else cli.NotResumableError

    with pytest.raises(expected):
        cli.resume_run(
            TASK_RUN_ID, repo_dir=projection, harness_override=(None, {"resolve": 600.0})
        )


def test_am_resume_of_a_task_run_refuses_resolve_with_exit_2_and_writes_nothing(
    projection, monkeypatch
):
    _record(projection, TASK_RUN_ID, started_at=RECORDED_AT, status="started")
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    monkeypatch.setattr(store_writer.Store, "open", _Forbidden("Store.open"))
    before = _runs_snapshot()

    result = runner.invoke(
        cli.app,
        [
            "resume",
            TASK_RUN_ID,
            "--repo-dir",
            str(projection),
            "--harness-timeout=resolve=600",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--harness-timeout" in result.output
    assert '"ok"' not in result.stdout
    for phase in cli.TASK_AGENT_PHASES:
        assert phase in result.output
    assert _runs_snapshot() == before
    assert _loaded(projection, TASK_RUN_ID).config == models.RunConfig()


def test_run_agent_phases_follow_the_run_shape():
    def run(workflow: str, story_id: str | None = None) -> models.Run:
        return models.Run(
            id=TASK_RUN_ID,
            workflow=workflow,
            repo_dir=Path("/repo"),
            base_branch="main",
            branch_prefix="m1",
            status="escalated",
            config=models.RunConfig(story_id=story_id),
        )

    assert cli.run_agent_phases(run("task")) == cli.TASK_AGENT_PHASES
    assert cli.run_agent_phases(run("milestone", "story-1")) == cli.TASK_AGENT_PHASES
    assert cli.run_agent_phases(run("milestone")) == cli.MILESTONE_AGENT_PHASES
