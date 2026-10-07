"""Default-suite e2e tier: a finished agent phase is not dispatched again on resume (card 3b6a6c27).

Milestone 11, exactly-once phases. `am run --card` and `am resume` run
through `CliRunner` on the real `cli.app` with no `runner_factory`, so every
launch goes through `cli.default_runner_factory`, the real
`dispatch.AgentRunner`, the real `ClaudeAdapter` and `launcher.run_direct` to
the fake `claude` first on `PATH`.

The manager is killed with `_Killed`, a plain `BaseException`, right after
`implement`'s `bridge.call_agent` returned: its attempt is journalled `ok`
and its commit is on the branch, but the next turn's checkpoint was never
saved. `am resume` must adopt that recorded result instead of dispatching
`implement` again. The kill is test scaffolding in the manager process
(`runtime/compile.py` reads `bridge.call_agent` at call time); the fake is
never told anything. No sleeps. Unmarked on purpose: it must run on every
`uv run pytest`.

Note: the fake coder commits only when the tree changed
(`fake_claude.build_result`), so a second `implement` would add no second
commit. The dispatch count and the recorded attempts are what catch a
re-dispatch; the commit count pins the spec's "exactly one implement commit".
"""

import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import cli, models
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import projects as store_projects
from agent_manager.store import queries as store_queries
from agent_manager.store import writer as store_writer
from agent_manager.runtime import bridge
from agent_manager.runtime import engine as runtime_engine

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: `milestone_board` derives its branches with it."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)
"""Must equal the conftest's `AGENT_PHASES`: `TASK`'s seven agent phases, in order."""

IMPLEMENT_SUBJECT = "feat: implement this card"
"""The subject line of the commit the fake coder makes (`fake_claude.build_result`)."""

_REAL_CALL_AGENT = bridge.call_agent
"""The unpatched door, captured at import: what the kill switch wraps and is restored to."""


class _Killed(BaseException):
    """The manager process dying. A plain `BaseException`, so neither the
    engine, pygents nor `CliRunner` swallows it, and not `KeyboardInterrupt`,
    which asyncio re-raises out of the event loop before the engine unwinds."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _latest_run_id(root: Path) -> str:
    resolved = cli.resolve_repo_dir(root)
    conn = store_db.open_db(resolved)
    try:
        run_id = store_queries.latest_run_id(
            conn, project_id=store_projects.lookup(conn, resolved)
        )
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _latest_checkpoint(root: Path, run_id: str, card_id: str) -> store_checkpoints.Checkpoint:
    opened = store_writer.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        checkpoint = opened.latest_checkpoint(card_id)
    finally:
        opened.close()
    assert checkpoint is not None, (run_id, card_id)
    return checkpoint


def _attempts(root: Path, run_id: str, card_id: str, phase_name: str) -> list[tuple[int, str]]:
    """`(n, status)` of every attempt of `phase_name` the projection holds for `card_id`."""
    run = _load_run(root, run_id)
    return [
        (attempt.n, attempt.status)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.card_id == card_id
        for phase in subtask.phases
        if phase.name == phase_name
        for attempt in phase.attempts
    ]


def _implement_commits(root: Path, branch: str) -> int:
    subjects = _git(root, "log", "--format=%s", f"main..{branch}").splitlines()
    return subjects.count(IMPLEMENT_SUBJECT)


def _dispatches(entries) -> Counter:
    """How many times the fake ran each phase, over every invocation of the run."""
    return Counter(entry["phase"] for entry in entries)


def _run_card(root: Path, card_id: str):
    return CliRunner().invoke(
        cli.app,
        [
            "run",
            "--card",
            card_id,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            PREFIX,
            "--verify",
            VERIFY,
        ],
    )


def _resume(root: Path, run_id: str):
    return CliRunner().invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(root), "--verify", VERIFY]
    )


def _kill_after_implement_returns(monkeypatch) -> None:
    """Kill the manager once, right after `implement`'s dispatch returned.

    The real `call_agent` runs the real `AgentRunner`, which launches the fake,
    validates its result and records the attempt `ok` and the phase `done`;
    only then does the wrapper raise. One-shot.
    """
    armed = {"on": True}

    async def call_agent(runner, phase, context, rendered):
        result = await _REAL_CALL_AGENT(runner, phase, context, rendered)
        if armed["on"] and phase.name == "implement":
            armed["on"] = False
            raise _Killed("killed after implement's dispatch returned")
        return result

    monkeypatch.setattr(bridge, "call_agent", call_agent)


def test_resume_after_implement_returns_adopts_it(
    milestone_board, fake_claude_bin, read_fake_log, checkpoint_rows, monkeypatch
):
    """Killed after `implement` succeeded; `am resume` finishes the run `done`
    with `implement` dispatched once in total, one implement commit, and one
    "not dispatched again" warning."""
    root = milestone_board["root"]
    a1 = milestone_board["subtasks"]["A"][0]
    branch = milestone_board["branches"][a1]
    _kill_after_implement_returns(monkeypatch)

    # First invocation: the manager dies after implement's dispatch returned.
    with pytest.raises(_Killed):
        _run_card(root, a1)

    run_id = _latest_run_id(root)
    assert _load_run(root, run_id).status == "started"
    assert checkpoint_rows(root, run_id) > 0
    # Review Focus 4: the newest checkpoint is implement's own turn, with its floor.
    crashed = _latest_checkpoint(root, run_id, a1)
    assert crashed.reason == "turn"
    assert runtime_engine.pending_phase(crashed) == "implement"
    assert crashed.floor is not None
    assert (crashed.floor.phase, crashed.floor.source_run, crashed.floor.floor) == (
        "implement",
        run_id,
        0,
    )
    # Review Focus 3: the dispatch finished and was judged, not orphaned.
    assert _attempts(root, run_id, a1, "implement") == [(1, "ok")]
    assert _dispatches(read_fake_log(run_id)) == Counter(AGENT_PHASES[:6])
    assert _implement_commits(root, branch) == 1

    # Review Focus 1: the kill switch is removed, not just disarmed, before resume.
    monkeypatch.setattr(bridge, "call_agent", _REAL_CALL_AGENT)

    # Second invocation: am resume <run-id>.
    result = _resume(root, run_id)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    # The regression this test exists for: implement dispatched a second time.
    assert _dispatches(read_fake_log(run_id)) == Counter(AGENT_PHASES)
    assert _attempts(root, run_id, a1, "implement") == [(1, "ok")]
    assert data["status"] == "done", data
    assert data["resumed_from"] == "implement"
    assert data["discarded_attempts"] == []
    # Review Focus 5: exactly one adoption line, naming implement, attempt 1, this run.
    reused = [line for line in data["warnings"] if "not dispatched again" in line]
    assert reused == [
        f"phase 'implement' was not dispatched again: attempt 1 of run {run_id} "
        "had already succeeded (result reused)"
    ], data["warnings"]
    assert _implement_commits(root, branch) == 1
    assert _load_run(root, run_id).status == "done"


def test_no_kill_switch_is_left_armed_for_later_tests():
    """Review Focus 1: the kill switch goes through the function-scoped
    `monkeypatch`; it must be gone once its test ends. Kept last in the module."""
    assert bridge.call_agent is _REAL_CALL_AGENT
