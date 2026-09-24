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

import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_manager import (
    board,
    census,
    cli,
    dag,
    dispatch,
    models,
    paths,
    prompt,
    store as store_module,
)
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.steps.reducers import verification_gate
from agent_manager.workflow.loader import load_builtin
from agent_manager.workflow.registry import WorkflowLoadError


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
"""Which phases of `builtin/task.yaml` are `kind: agent`. `PhaseRun.kind` is a
Literal, so a hand-built phase has to name the right one."""


def _task_workflow():
    """The shipped `builtin/task.yaml`, loaded and resolved.

    Still unit tier: the only thing read is a packaged document that ships with
    the source. No run state, no clock, no subprocess -- and the back-off rule
    these tests pin is a property of *that* document, so substituting a
    hand-written one would test the wrong thing.
    """
    return load_builtin("task")


def _recorded(name: str, status: str, attempts: list[models.Attempt] | None = None):
    """One `PhaseRun` of the task workflow as the projection would hold it."""
    return models.PhaseRun(
        name=name,
        kind="agent" if name in AGENT_PHASE_NAMES else "deterministic",
        status=status,
        attempts=list(attempts or []),
    )


def test_the_interrupted_phase_is_the_one_recorded_started():
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded("worktree", "done"),
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
            _recorded("plan_check", "done"),
            _recorded("spec", "done", [_pure_attempt(1)]),
            _recorded("validate_spec", "done", [_pure_attempt(1)]),
            _recorded("plan", "done", [_pure_attempt(1)]),
            _recorded("validate_plan", "done", [_pure_attempt(1)]),
            _recorded("implement", "started", [_pure_attempt(1, status="started")]),
        ],
    )

    assert cli.interrupted_phase(subtask, _task_workflow()) == "implement"


def test_a_crash_between_phases_restarts_at_the_first_phase_not_done():
    """No phase is `started`, so the process died between two of them: the first
    phase the projection does not hold as `done` is the one that never ran."""
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded("worktree", "done"),
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
        ],
    )

    assert cli.interrupted_phase(subtask, _task_workflow()) == "plan_check"


def test_a_skipped_stretch_restarts_at_the_phase_whose_when_decided_the_skip():
    """`plan_check` may `skip_to: implement`, and a skipped phase is never
    recorded (engine.py:432 only appends to the in-memory summary). Restarting at
    `spec` would re-author a spec over a plan Validate already signed; restarting
    at `plan_check` lets its own `when` decide the jump again."""
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded("worktree", "done"),
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
            _recorded("plan_check", "done"),
        ],
    )

    assert cli.interrupted_phase(subtask, _task_workflow()) == "plan_check"


def test_a_run_that_lost_everything_after_the_first_phase_restarts_at_explore():
    """The first phase of the document is `worktree` (R6), so the cheapest real
    crash there is -- the process dying right after the worktree was created --
    must restart at `explore` and never re-create the worktree."""
    workflow = _task_workflow()
    assert workflow.phase_names[0] == "worktree"

    subtask = _pure_subtask("card-1", [_recorded("worktree", "done")])

    assert cli.interrupted_phase(subtask, workflow) == "explore"


def test_a_run_that_only_lost_its_last_phase_restarts_there_and_not_at_plan_check():
    """The skip-aware branch must stay bounded by the `skip_to` target: with
    `implement`, `review` and `verify` all recorded `done`, no jump can explain a
    missing `mark_done`, and re-running the whole tail would be a fresh run."""
    workflow = _task_workflow()
    subtask = _pure_subtask(
        "card-1",
        [_recorded(name, "done") for name in workflow.phase_names if name != "mark_done"],
    )

    assert cli.interrupted_phase(subtask, workflow) == "mark_done"


SKIPPED_STRETCH = ("spec", "validate_spec", "plan", "validate_plan", "mark_validated")
"""The phases `plan_check: skip_to docs_commit` jumps over, which the engine
records nowhere at all (engine.py:431-433 only appends to the in-memory
summary), so an unrecorded stretch reads the same as one that never ran."""


def test_a_skipped_stretch_the_walk_ran_past_does_not_drag_the_restart_back():
    """The commonest resume there is: a card whose plan was already validated
    took `plan_check`'s jump, `implement` ran to `done`, and the process died
    before `review`. `spec` is unrecorded because it was skipped, not because it
    was interrupted -- and restarting at `plan_check` would re-dispatch a
    finished `implement`, the single most expensive phase of the document."""
    workflow = _task_workflow()
    subtask = _pure_subtask(
        "card-1",
        [
            *[
                _recorded(name, "done")
                for name in workflow.phase_names[:4]
                if name not in SKIPPED_STRETCH
            ],
            _recorded("docs_commit", "done"),
            _recorded("implement", "done", [_pure_attempt(1)]),
        ],
    )

    assert cli.interrupted_phase(subtask, workflow) == "review"


def test_a_skipped_stretch_with_only_the_final_write_lost_restarts_at_mark_done():
    """Same jump, run out to `verify`: nothing but `mark_done` is left, so the
    skip explains nothing and the restart must not walk the tail again."""
    workflow = _task_workflow()
    subtask = _pure_subtask(
        "card-1",
        [
            _recorded(name, "done")
            for name in workflow.phase_names
            if name not in SKIPPED_STRETCH and name != "mark_done"
        ],
    )

    assert cli.interrupted_phase(subtask, workflow) == "mark_done"


def test_a_subtask_with_every_phase_done_has_no_phase_to_resume():
    """The condition `resume_run` turns into `NotResumableError`: only the final
    status write was lost, and re-running `mark_done` would not be a resume."""
    workflow = _task_workflow()
    subtask = _pure_subtask(
        "card-1", [_recorded(name, "done") for name in workflow.phase_names]
    )

    assert cli.interrupted_phase(subtask, workflow) is None


def test_an_interrupted_spec_backs_off_to_the_phase_whose_result_it_binds():
    """`spec` declares `inputs: [card, explore]`, and `explore`'s result lives
    only in the in-memory binding table `run_subtask` builds -- so a walk started
    at `spec` could not render its prompt at all."""
    assert cli.resume_start_phase(_task_workflow(), "spec") == "explore"


def test_an_interrupted_implement_backs_off_to_the_phase_that_binds_its_plan_hash():
    """`implement` declares `[plan_path, spec_path, branch, base_branch,
    plan_hash]`. The first four come from the record; `plan_hash` is
    `docs_commit`'s result, which lives only in the in-memory binding table, so
    a walk started at `implement` could not render its brief. `docs_commit` is
    idempotent (nothing to commit and the branch already carries the hash
    returns the same digest), so re-running it is the whole recovery. Card
    f26b377d."""
    assert cli.resume_start_phase(_task_workflow(), "implement") == "docs_commit"


def test_the_resume_walk_reads_its_producer_map_out_of_the_prompt_table(monkeypatch):
    """`input name -> producing phase` has one home: the nested resolvers in
    `prompt._TABLE`, which are what make the dependency real. A second,
    hand-written copy in `cli.py` would go stale the day a resolver reads a
    different phase, and the walk would restart somewhere that cannot render."""
    monkeypatch.setitem(prompt.INPUT_PRODUCERS, "plan_hash", "mark_validated")

    assert cli.resume_start_phase(_task_workflow(), "implement") == "mark_validated"


def test_a_deterministic_phase_killed_mid_suite_restarts_at_itself_with_no_orphans():
    """Review Focus: the process died inside `verify.run_suite`. A deterministic
    phase dispatches nothing, so there is no attempt to discard, and
    `verify.run_suite` is read-only -- re-running it is the whole recovery."""
    workflow = _task_workflow()
    subtask = _pure_subtask(
        "card-1",
        [
            *[_recorded(name, "done") for name in workflow.phase_names[:11]],
            _recorded("verify", "started"),
        ],
    )

    assert cli.interrupted_phase(subtask, workflow) == "verify"
    assert cli.resume_start_phase(workflow, "verify") == "verify"
    assert cli.orphan_attempts(subtask) == []


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

    payload = cli.dry_run_payload([a, b], branch_prefix="m3", base_branch="main")

    def branch(subtask: census.SubtaskPlan) -> str:
        return dag.subtask_branch("m3", subtask)

    assert payload == {
        "levels": [
            {
                "level": 0,
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
    }


def test_the_dry_run_payload_keeps_census_order_across_and_within_levels():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id])

    payload = cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")

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
        cli.dry_run_payload([a, b], branch_prefix="m3", base_branch="main")

    assert f"#{a.id} -> #{b.id} -> #{a.id}" in str(caught.value)


def test_the_dry_run_refuses_a_story_with_two_in_milestone_blockers():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id, b.id])

    with pytest.raises(dag.StackRootError) as caught:
        cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")

    assert f"#{a.id}" in str(caught.value)
    assert f"#{b.id}" in str(caught.value)


def test_a_milestone_with_nothing_left_has_no_levels_and_lists_every_story_as_done():
    """Review focus: a closed story, a story whose subtasks are all done, and a
    story with no subtasks at all each become one `kind: "story"` entry, in
    census order, with their subtasks not listed separately."""
    closed = _plan_story(1, [_plan_subtask(11)], status="done")
    finished = _plan_story(2, [_plan_subtask(21, "done"), _plan_subtask(22, "Done")])
    empty = _plan_story(3, [])

    payload = cli.dry_run_payload(
        [closed, finished, empty], branch_prefix="m3", base_branch="main"
    )

    assert payload == {
        "levels": [],
        "already_done": [
            {"kind": "story", "id": closed.id, "title": "story 1"},
            {"kind": "story", "id": finished.id, "title": "story 2"},
            {"kind": "story", "id": empty.id, "title": "story 3"},
        ],
    }


def test_a_blocker_outside_the_milestone_roots_the_story_on_the_base_branch():
    """Review focus: one foreign blocker plus one in-milestone blocker is ONE
    in-milestone blocker, not a two-blocker refusal."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=["not-in-this-milestone"])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=["not-in-this-milestone", a.id])

    payload = cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")

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
    """`run_subtask` deliberately lets `EngineError` out rather than journalling
    it as a phase failure: an unbindable gate is a document bug, not an attempt."""

    def exploding(*args, **kwargs):
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(cli.engine, "run_subtask", exploding)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "EngineError"
    assert "explore" in envelope["error"]["message"]


@requires_git
@requires_brd
def test_a_workflow_that_will_not_load_reaches_the_operator_unchanged(
    project, cards, monkeypatch
):
    """A broken document is a load-time bug: the loader's own message names the
    workflow, the phase and the field, and the CLI must not paraphrase it."""

    def exploding(name, registry=None):
        raise WorkflowLoadError(
            "names 1 function(s) nobody registered", workflow="task", phase="verify"
        )

    monkeypatch.setattr(cli, "load_builtin", exploding)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "WorkflowLoadError"
    assert "verify" in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs").exists()


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
    `gate_context` -> the context `builtin/task.yaml` binds its gates out of.

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


def _assert_nothing_written(project: Path, porcelain_before: str) -> None:
    """No run dir or projection, no worktree, no branch, no repo change."""
    assert list(paths.data_dir().iterdir()) == []
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
    assert set(data) == {"levels", "already_done"}
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
def test_a_story_blocked_by_two_stories_is_an_envelope_naming_both(project, monkeypatch):
    milestone = _add_card(project, "Milestone 8: diamond")
    first = _add_card(project, "Story one", milestone)
    second = _add_card(project, "Story two", milestone)
    joined = _add_card(project, "Story three", milestone)
    for story in (first, second, joined):
        _add_card(project, f"only subtask of {story}", story)
    _block(project, joined, first)
    _block(project, joined, second)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(project, milestone))

    assert error["type"] == "StackRootError"
    assert f"#{first}" in error["message"]
    assert f"#{second}" in error["message"]
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

    result = runner.invoke(
        cli.app,
        ["run", *targets, "--repo-dir", str(tmp_path), "--branch-prefix", "m2"],
    )

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert word in result.output
    assert list(paths.data_dir().iterdir()) == []


def test_a_milestone_run_without_dry_run_is_a_not_implemented_envelope(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))

    error = _refusal(
        runner.invoke(
            cli.app,
            [
                "run",
                "--milestone",
                "2",
                "--repo-dir",
                str(tmp_path),
                "--branch-prefix",
                "m2",
            ],
        )
    )

    assert error["type"] == "MilestoneRunNotImplementedError"
    assert "not implemented" in error["message"]
    assert "--dry-run" in error["message"]
    assert not (paths.data_dir() / "runs").exists()
    assert list(paths.data_dir().iterdir()) == []


def test_the_not_implemented_refusal_is_a_cli_error():
    assert issubclass(cli.MilestoneRunNotImplementedError, cli.CliError)


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
            raise KeyboardInterrupt(f"simulated kill during {phase.name}")
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


def _resume_factory(seen: list[str] | None = None, crash_at: str | None = None):
    """A `cli.RunnerFactory` handing `recording_runner` the store the CLI opened."""

    def factory(*, workflow, store, run_id, story_id, card_id):
        return recording_runner(
            store=store,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            crash_at=crash_at,
            seen=seen,
        )

    return factory


def _crash_mid_phase(project: Path, cards: dict[str, str], phase: str) -> str:
    """Drive a real `run_card` until it is killed inside `phase`, and name the run."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    with pytest.raises(KeyboardInterrupt):
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            clock=lambda: CRASHED_AT,
            runner_factory=_resume_factory(crash_at=phase),
        )
    return run_id


@requires_git
@requires_brd
def test_a_run_killed_mid_implement_resumes_and_leaves_no_started_attempt(project, cards):
    """§14's required test: a simulated crash mid-phase, then a resume.

    The crash leaves the subtask, the phase and the attempt all recorded
    `started`; §9 says resume discards the in-flight attempt and re-runs that
    phase from the top. An attempt row left `started` after the run finished is
    the corruption this whole command exists to prevent.
    """
    run_id = _crash_mid_phase(project, cards, "implement")
    assert [
        row for row in _attempt_rows(project) if row[3] == "implement" and row[5] == "started"
    ] != []

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["status"] == "done"
    assert payload["resumed_from"] == "docs_commit"
    assert {"phase": "implement", "n": 1} in payload["discarded_attempts"]
    assert payload["run_id"] == run_id
    assert payload["card_id"] == cards["subtask"]
    assert payload["story_id"] == cards["story"]
    assert [row for row in _attempt_rows(project) if row[5] == "started"] == []


@requires_git
@requires_brd
def test_the_resumed_dispatch_numbers_past_the_attempt_the_crash_left(project, cards):
    """`dispatch.next_attempt` scans directories on disk precisely so a resumed
    run cannot overwrite the prompt and log of the attempt that died -- they are
    the only record of what the killed process was doing."""
    run_id = _crash_mid_phase(project, cards, "implement")
    crashed_dir = paths.run_dir(run_id) / cards["subtask"] / "implement.1"
    prompt_before = (crashed_dir / "prompt.txt").read_bytes()

    cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert (crashed_dir / "prompt.txt").read_bytes() == prompt_before
    assert (paths.run_dir(run_id) / cards["subtask"] / "implement.2").is_dir()
    implement = [row for row in _attempt_rows(project) if row[3] == "implement"]
    assert [(row[4], row[5]) for row in implement] == [(1, "harness_error"), (2, "ok")]


@requires_git
@requires_brd
def test_resume_launches_no_harness(project, cards, monkeypatch):
    """§14's adapter rule at the resume seam: the launcher is injected, so a
    resume that got as far as launching one has already failed."""
    run_id = _crash_mid_phase(project, cards, "implement")

    def forbidden(*args, **kwargs):
        raise AssertionError("resume launched a harness process")

    monkeypatch.setattr(cli, "run_direct", forbidden)
    monkeypatch.setattr(cli.dispatch, "AgentRunner", forbidden)

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["status"] == "done"


@requires_git
@requires_brd
def test_a_run_killed_in_spec_restarts_at_explore_so_specs_input_is_bound(project, cards):
    """The back-off rule observed end to end: `spec` declares `explore` as an
    input, `explore`'s result was only ever in the dead process's memory, so the
    resumed walk has to produce it again before `spec` can render at all."""
    run_id = _crash_mid_phase(project, cards, "spec")
    seen: list[str] = []

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory(seen))

    assert payload["resumed_from"] == "explore"
    assert payload["status"] == "done"
    assert seen[0] == "explore"
    assert seen.index("explore") < seen.index("spec")


@requires_git
@requires_brd
def test_resume_passes_its_own_allow_no_verification_into_the_gate_context(project, cards):
    """`RunConfig` records neither the suite commands nor §12's opt-out, so the
    flag is the command's own -- and it has to reach the same four context keys
    `run_card` supplies (cli.py:438-455)."""
    run_id = _crash_mid_phase(project, cards, "spec")
    contexts: list[dict[str, Any]] = []

    def factory(*, workflow, store, run_id, story_id, card_id):
        def collect(phase, context, rendered):
            contexts.append(dict(context))
            if phase.name == "explore":
                return dict(EXPLORE_RESULT)
            return _canned_agent_result(phase, context)

        return collect

    cli.resume_run(
        run_id, repo_dir=project, allow_no_verification=True, runner_factory=factory
    )

    assert contexts[0]["allow_no_verification"] is True
    assert contexts[0]["suite_cmds"] == []
    assert contexts[0]["caller_provided"] is False
    assert contexts[0]["provided_verification"] is None


def _record_interrupted(
    project: Path,
    cards: dict[str, str],
    run_id: str,
    *,
    workflow: str = "task",
    done: tuple[str, ...] = (),
    started: str | None = None,
) -> None:
    """A run the projection holds as killed in flight, written row by row.

    The board and the git repo stay the `project` fixture's real ones, so
    `resume` can re-fetch the cards; only the run state is hand-built, because
    the two states these tests need -- a stretch a `skip_to` jumped over, and a
    workflow name no builtin matches -- are not states `run_card` can be driven
    into. Written through `Store`, which is the only way this program writes
    rows.
    """
    branch = dag.task_branch("m1", board.show(cards["subtask"], repo_dir=project))
    opened = store_module.Store.open(project, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=project,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=CRASHED_AT,
            )
        )
        opened.record_story(
            models.StoryRun(
                card_id=cards["story"],
                title="The CLI",
                level=0,
                status="started",
                tip_branch=branch,
            )
        )
        opened.record_subtask(
            cards["story"],
            models.SubtaskRun(
                card_id=cards["subtask"],
                branch=branch,
                base_branch="main",
                status="started",
                worktree_path=cli.worktree_for(project, branch),
            ),
        )
        for name in done:
            opened.record_phase(cards["story"], cards["subtask"], _recorded(name, "done"))
        if started is not None:
            opened.record_phase(
                cards["story"], cards["subtask"], _recorded(started, "started")
            )
    finally:
        opened.close()


@requires_git
@requires_brd
def test_a_restart_at_plan_check_that_finds_no_plan_is_an_engine_error_not_a_traceback(
    project, cards
):
    """Review Focus: restarting at `plan_check` re-asks its `when`, and a `when`
    that now says "no validated plan" drops the walk into `spec`, whose `explore`
    input no fresh context supplies. The honest outcome is the engine's own
    refusal naming the input -- which `HANDLED` turns into an envelope at exit 3
    -- and never an unhandled traceback."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    _record_interrupted(
        project,
        cards,
        run_id,
        done=("worktree", "explore", "mark_in_progress", "plan_check"),
    )

    with pytest.raises(EngineError) as caught:
        cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert "explore" in str(caught.value)
    assert isinstance(caught.value, cli.HANDLED)


@requires_git
@requires_brd
def test_resume_refuses_a_subtask_whose_every_phase_is_already_done(project, cards):
    """`interrupted_phase` returning `None` is a refusal of its own inside
    `resume_run`: every phase finished and only the closing status write was
    lost, so there is nothing to re-run. Without that branch the `None` falls
    straight into `resume_start_phase`, which would answer with the loader's
    "no phase named None" instead of the one thing an operator needs to hear.
    """
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    _record_interrupted(
        project, cards, run_id, done=tuple(load_builtin("task").phase_names)
    )

    with pytest.raises(cli.NotResumableError) as caught:
        cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert cards["subtask"] in str(caught.value)
    assert "no phase to re-run" in str(caught.value)


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


@requires_git
@requires_brd
def test_the_resume_command_prints_an_ok_envelope_and_exits_zero(project, cards, monkeypatch):
    """The factory is patched on the module rather than passed as an option: the
    injection seam is `cli.default_runner_factory`, and patching it is what
    proves the command reaches for that name."""
    run_id = _crash_mid_phase(project, cards, "implement")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "done"
    assert envelope["data"]["resumed_from"] == "docs_commit"
    assert envelope["data"]["discarded_attempts"] == [{"phase": "implement", "n": 1}]
    assert "\n" not in result.stdout.strip()


@requires_git
@requires_brd
def test_resume_pretty_indents_the_same_envelope(project, cards, monkeypatch):
    run_id = _crash_mid_phase(project, cards, "implement")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(project), "--pretty"]
    )

    assert result.exit_code == 0
    assert "\n" in result.stdout.strip()
    assert json.loads(result.stdout)["data"]["status"] == "done"


@requires_git
@requires_brd
def test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one(project, cards, monkeypatch):
    """An escalation is a truthful result, so the envelope stays `ok: true` and
    the exit code carries the full stop -- exactly as `run` does."""
    run_id = _crash_mid_phase(project, cards, "implement")
    monkeypatch.setattr(
        cli, "default_runner_factory", lambda **kwargs: fake_runner(fail="review")
    )

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ESCALATED
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "escalated"
    assert envelope["data"]["failed_phase"] == "review"
    assert envelope["data"]["resumed_from"] == "docs_commit"


@requires_git
@requires_brd
def test_resume_passes_its_repeated_verify_options_into_the_gate_context(
    project, cards, monkeypatch
):
    """`models.RunConfig` carries neither the suite commands nor the opt-out, so
    a resume has to be told the same things a fresh `run` was told -- and in the
    same order, since the engine runs the commands in sequence."""
    run_id = _crash_mid_phase(project, cards, "implement")
    contexts: list[dict[str, Any]] = []

    def collecting_factory(**kwargs):
        inner = fake_runner()

        def collect(phase, context, rendered):
            contexts.append(dict(context))
            return inner(phase, context, rendered)

        return collect

    monkeypatch.setattr(cli, "default_runner_factory", collecting_factory)

    result = runner.invoke(
        cli.app,
        [
            "resume",
            run_id,
            "--repo-dir",
            str(project),
            "--verify",
            "true",
            "--verify",
            "echo checked",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["resumed_from"] == "docs_commit"
    assert contexts[0]["suite_cmds"] == ["true", "echo checked"]
    assert contexts[0]["allow_no_verification"] is False


@requires_git
@requires_brd
def test_a_run_recorded_with_an_unknown_workflow_is_an_envelope(project, cards, monkeypatch):
    """Review Focus: the workflow name comes off the record, and a projection
    row naming a document no builtin matches has to reach the operator as the
    loader's own refusal rather than as a traceback."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    _record_interrupted(project, cards, run_id, workflow="nope", started="implement")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "WorkflowLoadError"
