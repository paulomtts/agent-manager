"""e2e_fake: a configured harness timeout kills the harness, and a resume keeps it (card eee43099).

Real wiring: no `runner_factory` and no `driver`, so every launch goes through
`cli.default_runner_factory` as `cli.runner_factory_for` binds it, the real
`ClaudeAdapter` and `launcher.run_direct`, to the fake `claude` first on
`PATH`. The fake's hold parks a1's `explore`, and nothing ever releases it, so
the fake would wait its full give-up time. The run's 2 s timeout must kill it
first. The 2 s goes in through the Python API (`run_milestone(harness_timeout=...)`
→ `RunConfig`), the test seam the parent spec names, because the CLI's floor
is 60 s.

A milestone run because a milestone resume re-runs the failed phase
(README "Relaunching resumes"), while an escalated `task` run is never resumed.
"""

from pathlib import Path

import pytest

from agent_manager import cli, models, orchestrate
from agent_manager.store import db as store_db
from agent_manager.store import queries as store_queries

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: the board fixture derives its branches with it."""

VERIFY = ("git rev-parse --verify HEAD",)
"""Must equal the conftest's `VERIFY_COMMANDS`."""

TIMEOUT = 2.0
"""The run's harness timeout, far below the CLI's 60 s floor on purpose."""

GIVE_UP = 20.0
"""`fake_claude.RENDEZVOUS_TIMEOUT`: how long a held fake waits on its own.
An attempt that took this long was not killed by the timeout."""


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _explore(run: models.Run, card_id: str) -> models.PhaseRun:
    (subtask,) = [
        subtask
        for story in run.stories
        for subtask in story.subtasks
        if subtask.card_id == card_id
    ]
    (phase,) = [phase for phase in subtask.phases if phase.name == "explore"]
    return phase


def _assert_killed(attempt: models.Attempt) -> None:
    assert attempt.status == "harness_error"
    assert attempt.exit_code is None, "a killed harness has no exit status"
    assert attempt.dispatch.timeout == TIMEOUT
    assert attempt.duration is not None
    assert attempt.duration < GIVE_UP


@pytest.mark.e2e_fake
def test_a_harness_past_its_timeout_is_killed_and_a_resume_keeps_the_timeout(
    milestone_board, fake_claude_bin, hold
):
    root = milestone_board["root"]
    a1, _a2 = milestone_board["subtasks"]["A"]
    hold.arm("explore")

    first = orchestrate.run_milestone(
        milestone_board["milestone"],
        repo_dir=root,
        base_branch="main",
        branch_prefix=PREFIX,
        commands=list(VERIFY),
        harness_timeout=TIMEOUT,
    )

    assert first["escalated"] is True, first
    assert (first["subtask"], first["failed_phase"]) == (a1, "explore")
    assert hold.held_marker(a1).exists(), "the fake never started a1's explore"
    run_id = first["run_id"]
    run = _load_run(root, run_id)
    assert run.config.harness_timeout == TIMEOUT
    phase = _explore(run, a1)
    assert phase.status == "failed"
    assert phase.detail is not None and "timed out" in phase.detail
    (attempt,) = phase.attempts  # no second dispatch of a timed-out attempt
    _assert_killed(attempt)

    resumed = cli.resume_run(run_id, repo_dir=root, commands=list(VERIFY))

    assert resumed["escalated"] is True, resumed
    assert resumed["resumed"] is True
    run = _load_run(root, run_id)
    assert run.config.harness_timeout == TIMEOUT
    phase = _explore(run, a1)
    assert [attempt.n for attempt in phase.attempts] == [1, 2]
    _assert_killed(phase.attempts[1])
    assert phase.detail is not None and "timed out" in phase.detail
