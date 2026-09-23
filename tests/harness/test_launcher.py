"""Behaviour of the direct launcher (design §6 step 4, decision D7, card
55e503e0).

Unit tier per design §14 line 484. The children below are `sys.executable -c`
one-liners -- deterministic, dependency-free, and emphatically not harnesses.
§14's rule is that no *harness* is executed in unit tests because the launcher
is injected everywhere above this module; running a real harness binary is
reserved for the single opt-in, excluded-by-default end-to-end test in §14
lines 489-490, which this card does not add. The launcher's own contract has no
other place to be tested.
"""

import math
import sys
from pathlib import Path

import pytest

from agent_manager.harness import launcher


def test_a_successful_command_reports_exit_zero_and_captures_stdout(tmp_path):
    # The stdout.log parent is deliberately absent: the engine passes
    # `attempt_dir(...) / "stdout.log"` and the launcher must be willing to
    # create the directory it was handed.
    log = tmp_path / "implement.1" / "stdout.log"
    outcome = launcher.run_direct(
        [sys.executable, "-c", "print('hello from the child')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert outcome.stdout_path == log
    assert outcome.argv[0] == sys.executable
    assert "hello from the child" in log.read_text()


def test_stderr_is_merged_into_the_same_log(tmp_path):
    # One log per attempt (§6 line 270). A harness's diagnostics are the most
    # useful thing in it when an attempt fails, so they must not go to the
    # manager's own stderr, where nothing journals them.
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('a warning\\n'); print('a result')",
        ],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    assert outcome.exit_code == 0
    text = log.read_text()
    assert "a warning" in text
    assert "a result" in text


def test_a_non_zero_exit_is_a_value_not_an_exception(tmp_path):
    # §6 line 278 wants a non-zero exit journalled as harness_error, which
    # means the engine has to receive it as data it can record.
    outcome = launcher.run_direct(
        [sys.executable, "-c", "raise SystemExit(3)"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
    )
    assert outcome.exit_code == 3
    assert outcome.timed_out is False


def test_a_timeout_kills_the_child_and_returns_a_value(tmp_path):
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [
            sys.executable,
            "-c",
            "import time; print('before the sleep', flush=True); time.sleep(30)",
        ],
        cwd=tmp_path,
        timeout=0.5,
        stdout_path=log,
    )
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    # The partial log is the whole point of leaving it on disk: it is the only
    # evidence of what the harness was doing when the clock ran out.
    assert "before the sleep" in log.read_text()
    # The kill actually happened -- we did not just wait out the 30s sleep.
    assert outcome.duration < 20.0


def test_the_child_runs_in_the_cwd_it_was_given(tmp_path):
    # D7: cwd pinned to the subtask worktree is the isolation, so a launcher
    # that silently inherited the manager's cwd would run every harness in the
    # wrong repository.
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    log = tmp_path / "stdout.log"
    launcher.run_direct(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=worktree,
        timeout=30.0,
        stdout_path=log,
    )
    assert Path(log.read_text().strip()).resolve() == worktree.resolve()


def test_duration_is_positive_and_finite(tmp_path):
    outcome = launcher.run_direct(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
    )
    assert outcome.duration > 0
    assert math.isfinite(outcome.duration)


def test_a_missing_or_non_directory_cwd_is_refused(tmp_path):
    # Refusing beats returning an Outcome: classified as harness_error it would
    # be re-dispatched max_attempts times into a directory that never appears.
    with pytest.raises(NotADirectoryError):
        launcher.run_direct(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path / "never-created",
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )

    a_file = tmp_path / "not-a-worktree.txt"
    a_file.write_text("")
    with pytest.raises(NotADirectoryError):
        launcher.run_direct(
            [sys.executable, "-c", "pass"],
            cwd=a_file,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_useless_timeout_is_refused(tmp_path):
    for bad in (0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            launcher.run_direct(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=bad,
                stdout_path=tmp_path / "stdout.log",
            )


def test_an_empty_argv_is_refused(tmp_path):
    # Without the guard this surfaces from inside subprocess as an IndexError
    # naming nothing the operator can act on.
    with pytest.raises(ValueError):
        launcher.run_direct(
            [],
            cwd=tmp_path,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_missing_executable_propagates_rather_than_looking_like_an_exit(tmp_path):
    # A harness binary that is not installed is a configuration problem. If it
    # came back as an ordinary non-zero Outcome the engine would retry it
    # max_attempts times and journal harness_error with no hint why.
    with pytest.raises(FileNotFoundError):
        launcher.run_direct(
            [str(tmp_path / "no-such-harness"), "-p"],
            cwd=tmp_path,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_child_that_reads_stdin_gets_eof_instead_of_hanging(tmp_path):
    # An interactive harness prompting for confirmation must not burn the whole
    # timeout on every attempt.
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [sys.executable, "-c", "import sys; print('stdin gave', repr(sys.stdin.read()))"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert "stdin gave ''" in log.read_text()


def test_an_existing_log_from_a_previous_attempt_is_truncated(tmp_path):
    # A resumed run can reuse an attempt directory. Two attempts concatenated
    # is worse evidence than one.
    log = tmp_path / "stdout.log"
    log.write_text("output from the attempt before the crash\n")
    launcher.run_direct(
        [sys.executable, "-c", "print('this attempt')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    text = log.read_text()
    assert "this attempt" in text
    assert "before the crash" not in text
