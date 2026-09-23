"""Behaviour of the verification step (design §4 `steps/`, spec card 9c3b1ffb).

Placement follows design §14: `verify.py` is a Steps component, so its
behaviour is exercised against real subprocesses (`sys.executable -c ...`) and
real directories created in `tmp_path` -- no network, and no faking of the
runner except where a test must force an outcome a real process will not
produce on demand (ANSI-coloured over-long output, an unlaunchable
executable), exactly as `tests/steps/test_plan_check.py` reserves its fake
filesystem.

The pure helpers ported from `gh.mjs` (`last_line`, `plain_text`) and from
`ship.mjs` (`command_diagnostic`) are asserted directly: their behaviour is the
specification (design §14, Pure-functions tier).
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_manager.steps import verify
from agent_manager.steps.verify import (
    CommandResult,
    VerifyError,
    command_diagnostic,
    last_line,
    plain_text,
)

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the verify step's read-only test",
)


def _py(script: str) -> str:
    """A verification command, as a card writes one: a single shell-free string.

    Quoted with `shlex.quote` so `run_suite`'s own `shlex.split` reconstructs
    exactly this argv -- the round trip a real card's `"uv run pytest"` makes.
    """
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup or assertions, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def test_last_line_is_the_last_non_empty_trimmed_line():
    # Tool managers (mise, direnv, nvm) print an activation banner above the
    # output we actually want; the value is the LAST line (gh.mjs:68-73).
    assert last_line("mise tools: python@3.12\n  42 passed  \n\n") == "42 passed"


def test_last_line_is_empty_for_empty_whitespace_and_none():
    assert last_line("") == ""
    assert last_line("   \n\t\n") == ""
    assert last_line(None) == ""


def test_last_line_drops_the_carriage_return_of_crlf_output():
    assert last_line("first\r\nsecond\r\n") == "second"


def test_plain_text_strips_ansi_sequences_and_control_characters():
    assert plain_text("\x1b[31mred\x1b[0m\tfail\x07") == "red fail"


def test_plain_text_truncates_past_the_cap_with_one_ellipsis():
    assert plain_text("x" * 301, 300) == "x" * 300 + "…"
    assert plain_text("x" * 300, 300) == "x" * 300
    assert plain_text("y" * 400) == "y" * 300 + "…"


def test_plain_text_is_the_empty_string_for_none():
    assert plain_text(None) == ""


def test_command_diagnostic_prefers_the_last_stderr_line():
    assert (
        command_diagnostic("out line", "boom: failed\n", "fallback")
        == "boom: failed"
    )


def test_command_diagnostic_falls_back_to_stdout_when_stderr_is_blank():
    # The reason this card exists: linters and gate scripts print their
    # diagnostic to stdout and exit non-zero. Reading only stderr produced
    # seven content-free "Command failed: ./scripts/gate-frontend.sh" failures
    # in the original (ship.mjs:53-67).
    assert (
        command_diagnostic("banner\nERROR: 3 lint problems\n", "  \n", "fallback")
        == "ERROR: 3 lint problems"
    )


def test_command_diagnostic_falls_back_to_the_bare_message_when_both_are_blank():
    assert (
        command_diagnostic("", "", "./gate.sh exited with code 2")
        == "./gate.sh exited with code 2"
    )


def test_command_diagnostic_never_returns_an_empty_string():
    # A blank tail reads as "we do not know why it failed", which is exactly
    # the outcome this port was written to end.
    assert command_diagnostic("", "", "   ") == "no output"
    assert command_diagnostic(None, None, None) == "no output"


def test_a_single_green_command_passes_with_its_last_stdout_line(tmp_path: Path):
    command = _py("print('banner'); print('7 passed')")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is True
    assert result["detail"] == ""
    assert result["verified"] == [{"command": command, "ok": True, "tail": "7 passed"}]


def test_every_green_command_runs_in_order(tmp_path: Path):
    first = _py("print('one')")
    second = _py("print('two')")
    third = _py("print('three')")
    result = verify.run_suite([first, second, third], str(tmp_path))
    assert result["passed"] is True
    assert [entry["command"] for entry in result["verified"]] == [first, second, third]
    assert [entry["tail"] for entry in result["verified"]] == ["one", "two", "three"]


def test_commands_run_with_cwd_set_to_the_worktree(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    command = _py("import os; print(os.path.realpath(os.getcwd()))")
    result = verify.run_suite([command], str(worktree))
    assert result["verified"][0]["tail"] == os.path.realpath(worktree)


def test_an_empty_command_list_passes_with_nothing_verified(tmp_path: Path):
    # Refusing to verify nothing belongs to `verification_gate` (design §5),
    # not to this step.
    assert verify.run_suite([], str(tmp_path)) == {
        "passed": True,
        "verified": [],
        "detail": "",
    }


@requires_git
def test_run_suite_leaves_the_worktree_and_the_repo_untouched(tmp_path: Path):
    # Design §9: "`verify.run_suite` is read-only."
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "agent-manager tests")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("hello")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    result = verify.run_suite([_py("print('green')")], str(repo))

    assert result["passed"] is True
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before


def test_a_red_command_reports_its_stderr_line(tmp_path: Path):
    command = _py("import sys; sys.stderr.write('AssertionError: boom\\n'); sys.exit(1)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"] == [
        {"command": command, "ok": False, "tail": "AssertionError: boom"}
    ]
    assert result["detail"] == f"verification failed: {command} — AssertionError: boom"


def test_a_red_command_with_a_stdout_only_diagnostic_reports_that_stdout_line(
    tmp_path: Path,
):
    # The card's reason to exist, proved against a real process rather than a
    # fake: the tool prints its diagnostic to stdout and exits non-zero.
    command = _py("print('ERROR: 3 lint problems'); raise SystemExit(2)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"][0]["ok"] is False
    assert result["verified"][0]["tail"] == "ERROR: 3 lint problems"
    assert "ERROR: 3 lint problems" in result["detail"]


def test_a_red_command_stops_the_commands_after_it(tmp_path: Path):
    marker = tmp_path / "second-ran.txt"
    red = _py("raise SystemExit(1)")
    second = _py(f"open({str(marker)!r}, 'w').write('ran')")
    result = verify.run_suite([red, second], str(tmp_path))
    assert result["passed"] is False
    assert [entry["command"] for entry in result["verified"]] == [red]
    assert not marker.exists()


def test_a_silent_red_command_still_carries_a_tail_and_a_detail(tmp_path: Path):
    command = _py("raise SystemExit(3)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["verified"][0]["tail"] == f"{command} exited with code 3"
    assert result["detail"].startswith("verification failed:")
    assert "exited with code 3" in result["detail"]


def test_undecodable_output_is_replaced_rather_than_crashing_the_step(tmp_path: Path):
    command = _py("import sys; sys.stdout.buffer.write(b'\\xff\\xfe done\\n')")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is True
    assert result["verified"][0]["tail"].endswith("done")


def test_a_huge_real_output_is_capped_at_three_hundred_characters(tmp_path: Path):
    command = _py("print('z' * 5000)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["verified"][0]["tail"] == "z" * 300 + "…"


def test_ansi_colour_and_over_long_lines_are_flattened_in_the_result(tmp_path: Path):
    # A fake runner, because no real tool can be relied on to emit ANSI on
    # demand -- and a raw ESC byte in a reported field once failed a milestone.
    noisy = "\x1b[31m" + "E" * 400 + "\x1b[0m"

    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=1, stdout="", stderr=noisy)

    result = verify.run_suite(["fake-linter"], str(tmp_path), runner=runner)
    assert result["verified"][0]["tail"] == "E" * 300 + "…"
    assert "\x1b" not in result["detail"]
    assert len(result["detail"]) <= 601


def test_a_command_that_cannot_be_launched_raises_verify_error(tmp_path: Path):
    # A missing binary is a misconfigured card, not a failed test run, and must
    # not read as an ordinary red suite.
    def runner(argv: list[str], cwd: str) -> CommandResult:
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    with pytest.raises(VerifyError) as excinfo:
        verify.run_suite(
            ["definitely-not-a-real-binary --version"], str(tmp_path), runner=runner
        )
    assert "definitely-not-a-real-binary" in str(excinfo.value)
