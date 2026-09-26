"""Default-suite e2e tier: `am run --milestone` through the production wiring.

Addendum O8 and main spec §14: the command runs through `typer.testing.CliRunner`
on the real `cli.app`, with no `runner_factory`. So `orchestrate.run_milestone`
reaches `cli.drive_subtask`, `cli.default_runner_factory`, the real
`ClaudeAdapter` and `launcher.run_direct`, and the only stand-in is the fake
`claude` first on `PATH`. Unmarked on purpose: this costs no model and must run
on every `uv run pytest`.

Each test builds its own repo and board (`milestone_board`), because a
milestone run moves every card it touches.
"""

import json
import subprocess
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, models, orchestrate, store
from agent_manager.harness import launcher


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _all_cards(milestone_board) -> list[str]:
    return [
        *(card for chain in milestone_board["subtasks"].values() for card in chain),
        *milestone_board["stories"].values(),
        milestone_board["milestone"],
    ]


INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""


def _branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


@pytest.fixture(scope="module", params=["yaml", "pygents"])
def engine(request) -> str:
    """Overrides the conftest's `engine` (spec test 6). Fixture params, not a
    parametrize mark, so no marker reaches this module."""
    return request.param


def test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line(
    milestone_board,
    run_milestone_cli,
    engine,
    tmp_path,
    engine_parity,
    board_card_labels,
    checkpoint_rows,
):
    """Spec test 1 / acceptance 2."""
    root = milestone_board["root"]
    subtasks = milestone_board["subtasks"]
    stories = milestone_board["stories"]
    branches = milestone_board["branches"]
    order = [*subtasks["A"], *subtasks["B"], *subtasks["C"]]
    main_before = _git(root, "rev-parse", "main").strip()

    result = run_milestone_cli(root, milestone_board["milestone"])

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert data["completed"] == order
    assert [level["stories"] for level in data["levels"]] == [
        [stories["A"]],
        [stories["B"]],
        [stories["C"]],
    ]
    assert data["integrated"] == {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(cli.worktree_for(root, INTEGRATION_BRANCH)),
        "merged": [stories["A"], stories["B"], stories["C"]],
        "resolved": [],
    }
    assert _is_ancestor(root, branches[subtasks["C"][-1]], INTEGRATION_BRANCH)

    # Done ON THE BOARD, subtasks by `mark_done` and stories and the milestone
    # by the rollup walk. This test writes no status itself.
    for card_id in _all_cards(milestone_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id

    # One stacked line: each branch contains its predecessor's commits.
    chain = [branches[card_id] for card_id in order]
    for earlier, later in zip(chain, chain[1:]):
        assert _is_ancestor(root, earlier, later), (earlier, later)
    assert _is_ancestor(root, branches[subtasks["A"][-1]], branches[subtasks["B"][0]])

    # Every commit the run made, on every subtask branch, carries the trailer.
    for branch in chain:
        revisions = _git(root, "rev-list", f"main..{branch}").split()
        assert revisions, branch  # non-vacuity
        for revision in revisions:
            message = _git(root, "show", "-s", "--format=%B", revision)
            assert any(
                line.startswith("Plan-Hash: ") for line in message.splitlines()
            ), (branch, revision, message)

    assert _git(root, "rev-parse", "main").strip() == main_before

    rows = checkpoint_rows(root, data["run_id"])
    assert (rows > 0) is (engine == "pygents"), (engine, rows)
    engine_parity(
        "milestone-clean", engine, data, tmp=tmp_path, cards=board_card_labels(milestone_board)
    )


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_production_wiring.py`'s guard: no `e2e` marker may reach this
    module, or the milestone wiring stops being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _cwds(entries) -> set[Path]:
    return {Path(entry["cwd"]).resolve() for entry in entries}


def test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it(
    milestone_board,
    review_fail_marker,
    run_milestone_cli,
    read_fake_log,
    engine,
    tmp_path,
    engine_parity,
    board_card_labels,
):
    """Spec test 2 / acceptances 3 and 5."""
    labels = board_card_labels(milestone_board)
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    stories = milestone_board["stories"]
    branches = milestone_board["branches"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    c_worktree = cli.worktree_for(root, branches[c1])
    b_worktree = cli.worktree_for(root, branches[b1])
    c_status_before = board.show(c1, repo_dir=root).status
    review_fail_marker.write_text(f"{branches[b1]}\n", encoding="utf-8")

    # First launch: B's review fails through the production review gate.
    first = run_milestone_cli(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["escalated"] is True, stopped
    assert stopped["story"] == stories["B"]
    assert stopped["subtask"] == b1
    assert stopped["failed_phase"] == "review"
    assert "review_blockers_gate" in stopped["detail"]
    assert "review-fail marker" in stopped["detail"]
    # A lane escalation never reaches Integrate.
    assert "integrated" not in stopped and "phase" not in stopped, stopped
    assert INTEGRATION_BRANCH not in _branches(root)
    # The marker never dirtied B's worktree.
    assert _git(b_worktree, "status", "--porcelain") == ""

    # Story C never started: no worktree, no branch, no phase, no agent.
    assert not c_worktree.exists()
    assert branches[c1] not in _git(root, "branch", "--format=%(refname:short)").split()
    rows = {
        subtask.card_id: subtask
        for story in _load_run(root, stopped["run_id"]).stories
        for subtask in story.subtasks
    }
    assert rows[b1].status == "escalated"
    assert rows[c1].status == "pending"
    assert rows[c1].phases == []
    first_entries = read_fake_log(stopped["run_id"])
    assert first_entries  # non-vacuity: agents did run in this run
    assert c_worktree.resolve() not in _cwds(first_entries)
    # Never started: the run wrote no status to it. brd derives `blocked` (its
    # story is blocked by B) or reports `todo`, never `in-progress` or `done`,
    # and whichever it is, it is the status C had before the launch.
    c_status_after = board.show(c1, repo_dir=root).status
    assert c_status_after in ("todo", "blocked")
    assert c_status_after == c_status_before
    engine_parity("milestone-relaunch-stopped", engine, stopped, tmp=tmp_path, cards=labels)

    # Fix the fake, then relaunch the same command.
    review_fail_marker.unlink()
    second = run_milestone_cli(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    finished = _envelope(second)
    assert finished["done"] is True, finished
    assert finished["run_id"] != stopped["run_id"]
    assert finished["completed"] == [b1, c1]
    assert finished["integrated"]["merged"] == [stories["A"], stories["B"], stories["C"]]
    assert finished["integrated"]["resolved"] == []
    second_entries = read_fake_log(finished["run_id"])
    assert second_entries
    a_worktrees = {cli.worktree_for(root, branches[card]).resolve() for card in (a1, a2)}
    assert not (_cwds(second_entries) & a_worktrees)
    for card_id in _all_cards(milestone_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
    assert _is_ancestor(root, branches[a2], branches[b1])
    assert _is_ancestor(root, branches[b1], branches[c1])
    # On pygents too, the relaunch re-drives b1 from its first phase: its
    # newest checkpoint is `escalated` with no turn left, which
    # `cli.continuable_checkpoint` never continues (card 02890d5d).
    engine_parity("milestone-relaunch-finished", engine, finished, tmp=tmp_path, cards=labels)

    # Review focus: relaunching a finished milestone drives nothing.
    third = run_milestone_cli(root, milestone)

    assert third.exit_code == 0, (third.output, third.exception)
    idle = _envelope(third)
    assert idle["done"] is True
    assert idle["completed"] == []
    assert idle["integrated"] == finished["integrated"]
    assert read_fake_log(idle["run_id"]) == []
    engine_parity("milestone-relaunch-idle", engine, idle, tmp=tmp_path, cards=labels)


# ── pygents resume and relaunch from checkpoints (card 02890d5d) ─────────────

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: the board fixtures derive their branches with it."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""


class _Killed(BaseException):
    """The manager process dying mid-phase. A plain `BaseException`, so neither
    the engine nor `CliRunner` swallows it, and not `KeyboardInterrupt`, which
    asyncio re-raises out of the event loop before the engine unwinds."""


def _attempt_of(stdout_path: Path) -> tuple[str, str]:
    """(card id, phase) of the attempt a launch belongs to.

    `paths.attempt_dir` is `<run dir>/<card>/<phase>.<n>` and the dispatcher
    hands the launcher `<attempt dir>/stdout.log`, so the launch names its own
    attempt; the fake is never asked.
    """
    attempt = stdout_path.parent
    return attempt.parent.name, attempt.name.rsplit(".", 1)[0]


def _latest_run_id(root: Path) -> str:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _kill_after(monkeypatch, card_id: str, phase: str) -> None:
    """Kill the manager once, right after `card_id`'s `phase` launch returns.

    Test scaffolding in the manager process: `cli.default_runner_factory`
    reads `cli.run_direct` at call time, so the real launcher still spawns the
    real fake, which writes its result and logs the phase as always. Raising
    after it returns and before the dispatcher records the outcome leaves the
    attempt and the phase `started`, the crash signature a real kill leaves.
    One-shot, so the resume launches through the real launcher.
    """
    real = launcher.run_direct
    armed = {"on": True}

    def killing(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        outcome = real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )
        if armed["on"] and _attempt_of(stdout_path) == (card_id, phase):
            armed["on"] = False
            raise _Killed(f"killed after the {phase} launch of {card_id} returned")
        return outcome

    monkeypatch.setattr(cli, "run_direct", killing)


def test_a_pygents_run_killed_in_plan_resumes_without_redispatching_explore_or_spec(
    milestone_board, fake_claude_bin, read_fake_log, monkeypatch
):
    """Spec test 12: explore, spec and validate_spec are dispatched once in
    total, plan twice (the killed attempt and the resumed one)."""
    root = milestone_board["root"]
    a1 = milestone_board["subtasks"]["A"][0]
    _kill_after(monkeypatch, a1, "plan")
    invoke = CliRunner().invoke

    with pytest.raises(_Killed):
        invoke(
            cli.app,
            [
                "run",
                "--card",
                a1,
                "--repo-dir",
                str(root),
                "--base-branch",
                "main",
                "--branch-prefix",
                PREFIX,
                "--engine",
                "pygents",
                "--verify",
                VERIFY,
            ],
        )
    run_id = _latest_run_id(root)
    assert Counter(entry["phase"] for entry in read_fake_log(run_id)) == {
        "explore": 1,
        "spec": 1,
        "validate_spec": 1,
        "plan": 1,
    }

    result = invoke(
        cli.app,
        [
            "resume",
            run_id,
            "--repo-dir",
            str(root),
            "--engine",
            "pygents",
            "--verify",
            VERIFY,
        ],
    )

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["status"] == "done", data
    assert data["resumed_from"] == "plan"
    assert data["discarded_attempts"] == [{"phase": "plan", "n": 1}]
    assert Counter(entry["phase"] for entry in read_fake_log(run_id)) == {
        "explore": 1,
        "spec": 1,
        "validate_spec": 1,
        "plan": 2,
        "validate_plan": 1,
        "implement": 1,
        "review": 1,
    }
    assert board.show(a1, repo_dir=root).status == "done"


REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""Must equal the conftest's `FAKE_REVIEW_FAIL_MARKER` (and `fake_claude.REVIEW_FAIL_MARKER`)."""

WAIT = 120.0
"""Seconds a held launch waits for the other lane before giving up. Generous:
it only bounds a broken run, a healthy one never waits this long."""


def _phases_in(entries, worktree: Path) -> list[str]:
    """The phases the fake ran in `worktree`, in log order."""
    return [entry["phase"] for entry in entries if Path(entry["cwd"]).resolve() == worktree]


def _hold_b1_in_plan_until_a1_escalates(monkeypatch, board_shape) -> dict[str, bool]:
    """Make the first launch park b1 before `validate_plan`, deterministically.

    Test scaffolding in the manager process, never seen by the fake. a1's
    `review` launch waits until b1 is inside `plan`; b1's `plan` launch waits
    until the run's stop is set, which a1's review failure (the review-fail
    marker) does. When b1's plan returns, the stop is set, so the next
    `BEFORE_TURN` parks b1 before `validate_plan`. `RunStop` is captured by a
    subclass because `run_milestone` builds it at call time. Returns the
    switch that turns the hold off for the relaunch.
    """
    stops: list[orchestrate.RunStop] = []

    @dataclass
    class CapturedStop(orchestrate.RunStop):
        def __post_init__(self) -> None:
            stops.append(self)

    monkeypatch.setattr(orchestrate, "RunStop", CapturedStop)
    (a1,) = board_shape["subtasks"]["A"]
    (b1,) = board_shape["subtasks"]["B"]
    b1_in_plan = threading.Event()
    hold = {"on": True}
    real = launcher.run_direct

    def held(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        if hold["on"]:
            attempt = _attempt_of(stdout_path)
            if attempt == (b1, "plan"):
                b1_in_plan.set()
                if not stops[-1].event.wait(WAIT):
                    raise AssertionError("a1's escalation never set the run's stop")
            elif attempt == (a1, "review"):
                if not b1_in_plan.wait(WAIT):
                    raise AssertionError("b1 never reached plan")
        return real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )

    monkeypatch.setattr(cli, "run_direct", held)
    return hold


def _pygents_milestone(root: Path, milestone: str):
    return CliRunner().invoke(
        cli.app,
        [
            "run",
            "--milestone",
            milestone,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            PREFIX,
            "--engine",
            "pygents",
            "--verify",
            VERIFY,
            "--max-concurrent",
            "2",
        ],
    )


def test_a_pygents_relaunch_continues_the_parked_card_from_its_checkpoint(
    two_story_board, fake_claude_bin, read_fake_log, monkeypatch
):
    """Spec test 13, plus Review Focus 1: the parked b1 continues at
    validate_plan and dispatches nothing it had finished; the escalated a1
    (newest row `escalated`, no turn left) starts fresh at explore."""
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    branches = two_story_board["branches"]
    (a1,) = two_story_board["subtasks"]["A"]
    (b1,) = two_story_board["subtasks"]["B"]
    a1_worktree = cli.worktree_for(root, branches[a1]).resolve()
    b1_worktree = cli.worktree_for(root, branches[b1]).resolve()
    marker = root / ".git" / REVIEW_FAIL_MARKER
    marker.write_text(f"{branches[a1]}\n", encoding="utf-8")
    hold = _hold_b1_in_plan_until_a1_escalates(monkeypatch, two_story_board)

    first = _pygents_milestone(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["subtask"] == a1, stopped
    assert stopped["failed_phase"] == "review"
    assert stopped["stopped"] == [
        {"story": stories["B"], "subtask": b1, "before_phase": "validate_plan"}
    ]
    assert _phases_in(read_fake_log(stopped["run_id"]), b1_worktree) == [
        "explore",
        "spec",
        "validate_spec",
        "plan",
    ]

    marker.unlink()
    hold["on"] = False
    second = _pygents_milestone(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    finished = _envelope(second)
    assert finished["done"] is True, finished
    assert finished["run_id"] != stopped["run_id"]
    assert finished["completed"] == [a1, b1]
    second_entries = read_fake_log(finished["run_id"])
    assert _phases_in(second_entries, b1_worktree) == ["validate_plan", "implement", "review"]
    assert _phases_in(second_entries, a1_worktree)[0] == "explore"
    for card_id in (a1, b1, *stories.values(), milestone):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
