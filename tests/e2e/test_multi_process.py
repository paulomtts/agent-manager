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
