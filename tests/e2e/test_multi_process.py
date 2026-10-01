"""Default-suite e2e tier: several `am` processes on one repository (card cfcfa6e3).

Multi-process design §7, end-to-end tier. Every `am` here is a real child
process (`python -c "from agent_manager.cli import app; app()" ...`), never
`CliRunner` in a thread, so leases, claims and locks meet across genuine
process boundaries. Each child inherits the test's `XDG_DATA_HOME`, the `PATH`
with the fake `claude` first, and the hold/rendezvous env vars. The only
stand-in is the fake `claude`; its env-only hold parks one card's `implement`
until the test writes that card's release file, which is how a milestone run
is kept live. Order is proven by marker files and exits, never by sleeping.

Unmarked on purpose: fake-claude e2e tests run on every `uv run pytest`; only
`tests/e2e/test_real_harness*.py` carry the `e2e` marker.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from agent_manager import board, cli, dag
from agent_manager.steps import rollup

PREFIX = "m10"
"""The `--branch-prefix` of every single-prefix scenario."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

SOME_CARD = "0123abcd-4567-89ef-0123-456789abcdef"
"""A card id shaped like brd's, for the hold fixture's own tests."""


def _common(root: Path, prefix: str) -> list[str]:
    """The flags `am run` needs for this repo, prefix and suite."""
    return [
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        prefix,
        "--verify",
        VERIFY,
    ]


def _milestone_argv(
    root: Path, milestone: str, prefix: str, *, max_concurrent: int = 1
) -> list[str]:
    """`am run --milestone`, one story at a time unless asked, so an overlap
    can only come from another process."""
    return [
        "run",
        "--milestone",
        milestone,
        *_common(root, prefix),
        "--max-concurrent",
        str(max_concurrent),
    ]


def _data(code: int, envelope: dict) -> dict:
    """The `data` of an ok envelope from a child that exited 0."""
    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _error(code: int, envelope: dict) -> dict:
    """The `error` of a failure envelope from a child that exited 3."""
    assert code == cli.EXIT_ERROR, envelope
    assert envelope["ok"] is False, envelope
    return envelope["error"]


def _run_ids(am, root: Path) -> list[str]:
    """`am runs`: every run id this repo has recorded, newest first."""
    data = _data(*am("runs", "--repo-dir", str(root)))
    return [row["id"] for row in data["runs"]]


def _only_run_id(am, root: Path) -> str:
    ids = _run_ids(am, root)
    assert len(ids) == 1, ids
    return ids[0]


def _sleeper() -> subprocess.Popen:
    """A child that outlives any short wait; the caller tracks it for teardown."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.PIPE,
        text=True,
    )


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or the multi-process proof stops
    running on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_am_returns_the_exit_code_and_the_parsed_envelope(tmp_path, am):
    code, envelope = am("runs", "--repo-dir", str(tmp_path / "no-such-repo"))

    assert _error(code, envelope)["type"] == "RepoDirError"


def test_a_child_that_prints_no_envelope_fails_with_its_output(am_processes, finish_am):
    """Review focus: a traceback instead of an envelope names what the child said."""
    child = am_processes.track(
        subprocess.Popen(
            [sys.executable, "-c", "print('not json at all')"],
            stdout=subprocess.PIPE,
            text=True,
        )
    )

    with pytest.raises(pytest.fail.Exception) as caught:
        finish_am(child)

    assert "without a JSON envelope" in str(caught.value)
    assert "not json at all" in str(caught.value)


def test_the_child_env_inherits_the_tests_and_overlays_the_given_one(
    am_processes, monkeypatch
):
    monkeypatch.setenv("AM_TEST_INHERITED", "from-the-test")

    plain = am_processes.child_env(None)
    overlaid = am_processes.child_env({"AM_TEST_INHERITED": "over", "AM_TEST_NEW": "1"})

    assert plain["AM_TEST_INHERITED"] == "from-the-test"
    assert plain["PATH"] == os.environ["PATH"]
    assert overlaid["AM_TEST_INHERITED"] == "over"
    assert overlaid["AM_TEST_NEW"] == "1"
    assert overlaid["XDG_DATA_HOME"] == os.environ["XDG_DATA_HOME"]


def test_wait_for_file_fails_fast_with_the_output_of_a_child_that_exited(
    tmp_path, spawn_am, wait_for_file
):
    """The child exits 3 at once; the wait must not sit out its deadline."""
    child = spawn_am("runs", "--repo-dir", str(tmp_path / "no-such-repo"))

    with pytest.raises(pytest.fail.Exception) as caught:
        wait_for_file(tmp_path / "never.held", child)

    message = str(caught.value)
    assert "exited 3" in message
    assert "RepoDirError" in message


def test_wait_for_file_fails_at_its_deadline_while_the_child_lives(
    tmp_path, am_processes, wait_for_file
):
    child = am_processes.track(_sleeper())

    with pytest.raises(pytest.fail.Exception) as caught:
        wait_for_file(tmp_path / "never.held", child, timeout=0.2)

    assert "within 0.2s" in str(caught.value)
    assert child.poll() is None


def test_wait_for_file_returns_the_path_once_it_exists(tmp_path, am_processes, wait_for_file):
    child = am_processes.track(_sleeper())
    marker = tmp_path / "here.held"
    marker.write_text("1\n", encoding="utf-8")

    assert wait_for_file(marker, child, timeout=0.2) == marker


def test_closing_kills_and_reaps_every_live_child(am_processes):
    """Review focus: no child outlives its test."""
    child = am_processes.track(_sleeper())

    am_processes.close()

    assert child.returncode == -signal.SIGKILL


def test_the_hold_names_its_markers_by_the_fakes_short_id(hold):
    directory = hold.arm()

    assert os.environ["FAKE_CLAUDE_HOLD_DIR"] == str(directory)
    assert "FAKE_CLAUDE_HOLD_PHASE" not in os.environ
    assert hold.held_marker(SOME_CARD) == directory / f"{dag.short_id(SOME_CARD)}.held"


def test_release_all_releases_every_held_card(hold):
    """Review focus: the fixture's teardown lets any still-held fake go on."""
    directory = hold.arm("plan")
    hold.held_marker(SOME_CARD).write_text("4242\n", encoding="utf-8")

    hold.release_all()

    assert os.environ["FAKE_CLAUDE_HOLD_PHASE"] == "plan"
    assert hold.holder_pid(SOME_CARD) == 4242
    assert (directory / f"{dag.short_id(SOME_CARD)}.release").is_file()


def test_a_card_a_live_milestone_claims_is_refused_to_every_other_process(
    milestone_board, fake_claude_bin, hold, spawn_am, am, finish_am, wait_for_file
):
    """Spec scenario 1: `run --card`, `resume` and `status` from other processes
    while a real milestone process holds a1 in `implement`."""
    root = milestone_board["root"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)  # only a1 is held; the rest pass straight through
    milestone = spawn_am(*_milestone_argv(root, milestone_board["milestone"], PREFIX))
    wait_for_file(hold.held_marker(a1), milestone)
    run_id = _only_run_id(am, root)

    error = _error(*am("run", "--card", a2, *_common(root, PREFIX)))
    assert error["type"] == "ClaimedError", error
    assert f"card {a2} is" in error["message"]
    assert run_id in error["message"]
    assert _run_ids(am, root) == [run_id]
    a2_branch = dag.task_branch(PREFIX, board.show(a2, repo_dir=root))
    assert not cli.worktree_for(root, a2_branch).exists()

    error = _error(*am("resume", run_id, "--repo-dir", str(root), "--verify", VERIFY))
    assert error["type"] == "RunIsLiveError", error

    live = _data(*am("status", run_id, "--repo-dir", str(root)))
    assert live["run"]["status"] == "started"
    assert live["control"]["lease"]["live"] is True
    assert live["control"]["lease"]["pid"] == milestone.pid
    assert f"card:{a2}" in live["control"]["claims"]

    hold.release(a1)
    finished = _data(*finish_am(milestone))
    assert finished["done"] is True, finished
    assert finished["run_id"] == run_id
    assert finished["integrated"]["branch"] == f"{PREFIX}-integrate"
    after = _data(*am("status", run_id, "--repo-dir", str(root)))
    assert after["run"]["status"] == "done"
    assert after["control"]["claims"] == []


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _local_branches(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.split()


def _assert_rolled_up(root: Path, parents) -> None:
    """Every parent's board status is `rollup_status` of its direct children."""
    for card_id in parents:
        node = board.tree(card_id, repo_dir=root)
        children = [child.status for child in node.children]
        assert children, card_id
        assert node.status == rollup.rollup_status(children), (card_id, node.status, children)


def test_two_milestones_with_different_prefixes_run_at_once_and_stay_apart(
    two_milestone_board, fake_claude_bin, rendezvous, spawn_am, finish_am
):
    """Spec scenario 2. Each milestone drives one story at a time, so the
    count-2 rendezvous can only be met by the two processes' implements
    overlapping."""
    root = two_milestone_board["root"]
    prefixes = {"first": "m10a", "second": "m10b"}
    branches = {
        side: [
            dag.task_branch(prefixes[side], board.show(card_id, repo_dir=root))
            for card_id in two_milestone_board["subtasks"][side]
        ]
        for side in prefixes
    }
    rendezvous.arm(2)

    children = {
        side: spawn_am(
            *_milestone_argv(root, two_milestone_board["milestones"][side], prefix)
        )
        for side, prefix in prefixes.items()
    }
    results = {side: _data(*finish_am(child)) for side, child in children.items()}

    assert results["first"]["run_id"] != results["second"]["run_id"]
    for side, prefix in prefixes.items():
        data = results[side]
        other = "second" if side == "first" else "first"
        integrate = f"{prefix}-integrate"
        assert data["done"] is True, data
        assert data["completed"] == two_milestone_board["subtasks"][side]
        assert data["integrated"]["branch"] == integrate
        assert data["integrated"]["merged"] == two_milestone_board["stories"][side]
        for tip in branches[side]:
            assert _is_ancestor(root, tip, integrate), (tip, integrate)
        for tip in branches[other]:
            assert not _is_ancestor(root, tip, integrate), (tip, integrate)
    assert {"m10a-integrate", "m10b-integrate"} <= set(_local_branches(root))
    # One marker per implement cwd: all four subtasks reached the rendezvous.
    assert len(rendezvous.markers()) == 4

    for side in prefixes:
        for card_id in two_milestone_board["subtasks"][side]:
            assert board.show(card_id, repo_dir=root).status == "done", card_id
    parents = [
        *(story for side in prefixes for story in two_milestone_board["stories"][side]),
        *two_milestone_board["milestones"].values(),
    ]
    _assert_rolled_up(root, parents)
    for card_id in parents:
        assert board.show(card_id, repo_dir=root).status == "done", card_id


def test_a_second_milestone_on_a_prefix_in_use_is_refused_by_its_integration_branch(
    two_milestone_board, fake_claude_bin, hold, spawn_am, am, finish_am, wait_for_file
):
    """Spec scenario 3. The two milestones share no card, so the only key they
    both need is `branch:<prefix>-integrate`."""
    root = two_milestone_board["root"]
    a1, b1 = two_milestone_board["subtasks"]["first"]
    c1, d1 = two_milestone_board["subtasks"]["second"]
    hold.arm()
    hold.release(b1)
    live = spawn_am(
        *_milestone_argv(root, two_milestone_board["milestones"]["first"], PREFIX)
    )
    wait_for_file(hold.held_marker(a1), live)
    run_id = _only_run_id(am, root)

    error = _error(
        *am(*_milestone_argv(root, two_milestone_board["milestones"]["second"], PREFIX))
    )

    assert error["type"] == "ClaimedError", error
    assert f"branch {PREFIX}-integrate is" in error["message"]
    assert run_id in error["message"]
    assert _run_ids(am, root) == [run_id]
    for card_id in (c1, d1):
        branch = dag.task_branch(PREFIX, board.show(card_id, repo_dir=root))
        assert branch not in _local_branches(root), branch

    hold.release(a1)
    finished = _data(*finish_am(live))
    assert finished["done"] is True, finished
    assert finished["integrated"]["branch"] == f"{PREFIX}-integrate"


def _kill_orphaned_fake(pid: int) -> None:
    """SIGKILL the held fake `claude` a killed `am` left behind.

    `run_direct` starts the fake in its own session, so killing `am` does not
    reach it. Left alive it would wake on the release and commit in the same
    worktree as the resumed run's implement. The pid is the one the fake wrote
    into its `.held` marker, and it is still polling because no release
    exists yet, so the pid is still that fake's.
    """
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_resume_takes_over_a_killed_milestone_and_finishes_it(
    milestone_board, fake_claude_bin, hold, spawn_am, am, wait_for_file
):
    """Spec scenario 4: a dead holder's lease is taken over, not refused."""
    root = milestone_board["root"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)
    milestone = spawn_am(*_milestone_argv(root, milestone_board["milestone"], PREFIX))
    wait_for_file(hold.held_marker(a1), milestone)
    run_id = _only_run_id(am, root)
    fake_pid = hold.holder_pid(a1)
    assert fake_pid != milestone.pid

    dead_pid = milestone.pid
    milestone.kill()
    milestone.wait()
    assert milestone.returncode == -signal.SIGKILL
    _kill_orphaned_fake(fake_pid)
    hold.release(a1)

    resumed = _data(*am("resume", run_id, "--repo-dir", str(root), "--verify", VERIFY))

    assert resumed["done"] is True, resumed
    assert resumed["resumed"] is True
    assert resumed["run_id"] == run_id
    assert resumed["took_over"]["pid"] == dead_pid
    status = _data(*am("status", run_id, "--repo-dir", str(root)))
    assert status["run"]["status"] == "done"
    assert status["control"]["claims"] == []
