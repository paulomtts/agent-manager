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

import inspect
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from agent_manager.runtime import walk
from agent_manager.results import ExploreResult, Verification
from agent_manager.steps import verify
from agent_manager.steps.verify import (
    CommandResult,
    VerifyError,
    command_diagnostic,
    exit_label,
    last_line,
    plain_text,
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


@pytest.mark.parametrize(
    ("exit_code", "label"),
    [
        (1, "exit 1"),
        (2, "exit 2"),
        # A fake runner's out-of-range code is shown as given, never clamped.
        (300, "exit 300"),
    ],
)
def test_exit_label_formats_codes_verbatim(exit_code: int, label: str):
    assert exit_label(exit_code) == label


@pytest.mark.parametrize(
    ("exit_code", "label"),
    [
        (-9, "exit -9 (signal SIGKILL)"),
        (-15, "exit -15 (signal SIGTERM)"),
        (-11, "exit -11 (signal SIGSEGV)"),
    ],
)
def test_exit_label_names_the_signal_of_a_negative_code(exit_code: int, label: str):
    # `subprocess.run` reports a child killed by signal N as returncode -N.
    assert exit_label(exit_code) == label


def test_exit_label_falls_back_to_the_bare_number_for_an_unknown_signal():
    assert exit_label(-200) == "exit -200 (signal 200)"


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
        {
            "command": command,
            "ok": False,
            "exit_code": 1,
            "tail": "exit 1 — AssertionError: boom",
        }
    ]
    assert (
        result["detail"]
        == f"verification failed: {command} — exit 1 — AssertionError: boom"
    )


def test_a_red_command_with_a_stdout_only_diagnostic_reports_that_stdout_line(
    tmp_path: Path,
):
    # The card's reason to exist, proved against a real process rather than a
    # fake: the tool prints its diagnostic to stdout and exits non-zero.
    command = _py("print('ERROR: 3 lint problems'); raise SystemExit(2)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"][0]["ok"] is False
    assert result["verified"][0]["exit_code"] == 2
    assert result["verified"][0]["tail"] == "exit 2 — ERROR: 3 lint problems"
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
    assert result["verified"][0]["tail"] == "exit 3 — no output"
    assert result["detail"] == f"verification failed: {command} — exit 3 — no output"


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
    assert result["verified"][0]["tail"] == "exit 1 — " + "E" * 291 + "…"
    # `detail` is flattened too, and carries its own larger cap (600), so it is
    # not silently clipped to a per-command tail's 300.
    assert result["detail"] == f"verification failed: fake-linter — exit 1 — {'E' * 400}"


def test_a_red_command_with_a_green_looking_last_line_names_its_exit_code(
    tmp_path: Path,
):
    # The milestone-17 incident, against a real process: the runner printed a
    # green summary and exited 1, and the result used to say only "7 passed".
    command = _py("print('7 passed'); raise SystemExit(1)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"] == [
        {"command": command, "ok": False, "exit_code": 1, "tail": "exit 1 — 7 passed"}
    ]
    assert result["detail"] == f"verification failed: {command} — exit 1 — 7 passed"


def test_a_command_killed_by_a_signal_is_reported_as_that_signal(tmp_path: Path):
    # Real, not faked: the negative-returncode convention belongs to
    # `subprocess`, and only a real child shows it. POSIX only.
    command = _py("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    row = result["verified"][0]
    assert row["exit_code"] == -9
    assert row["tail"] == "exit -9 (signal SIGKILL) — no output"
    assert result["detail"].endswith("— exit -9 (signal SIGKILL) — no output")


def test_a_signal_style_code_from_the_runner_is_labelled_with_its_name(
    tmp_path: Path,
):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=-11, stdout="", stderr="Segmentation fault\n")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["passed"] is False
    assert result["verified"] == [
        {
            "command": "fake-suite",
            "ok": False,
            "exit_code": -11,
            "tail": "exit -11 (signal SIGSEGV) — Segmentation fault",
        }
    ]
    assert (
        result["detail"]
        == "verification failed: fake-suite — exit -11 (signal SIGSEGV) — Segmentation fault"
    )


def test_an_unknown_negative_code_falls_back_to_the_bare_number(tmp_path: Path):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=-200, stdout="", stderr="")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["exit_code"] == -200
    assert result["verified"][0]["tail"] == "exit -200 (signal 200) — no output"


def test_the_exit_code_survives_over_long_and_ansi_stream_content(tmp_path: Path):
    # Truncation cuts from the end, and the label comes first, so no stream
    # content can push the code out of `tail` or `detail`.
    noisy = "\x1b[32m" + "P" * 5000 + "\x1b[0m"

    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=2, stdout=noisy, stderr="")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["exit_code"] == 2
    assert result["verified"][0]["tail"] == "exit 2 — " + "P" * 291 + "…"
    detail = result["detail"]
    assert detail.startswith("verification failed: fake-suite — exit 2 — ")
    assert len(detail) == 601
    assert "\x1b" not in detail


def test_a_green_stdout_under_an_unrelated_stderr_line_still_leads_with_the_code(
    tmp_path: Path,
):
    # stderr's last line still wins over stdout (unchanged preference order);
    # the leading exit label is what stops the green stdout from misleading.
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(
            exit_code=1,
            stdout="7 passed\n",
            stderr="DeprecationWarning: old API\n",
        )

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["tail"] == "exit 1 — DeprecationWarning: old API"
    assert (
        result["detail"]
        == "verification failed: fake-suite — exit 1 — DeprecationWarning: old API"
    )


def test_a_crlf_diagnostic_leaves_no_carriage_return_in_the_tail(tmp_path: Path):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=1, stdout="", stderr="boom  \r\n\r\n")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["tail"] == "exit 1 — boom"
    assert result["detail"] == "verification failed: fake-suite — exit 1 — boom"


def test_an_out_of_range_positive_code_is_reported_verbatim(tmp_path: Path):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=300, stdout="", stderr="weird\n")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["exit_code"] == 300
    assert result["verified"][0]["tail"] == "exit 300 — weird"


def test_a_red_argv_command_keeps_its_sequence_and_shows_its_joined_argv(
    tmp_path: Path,
):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=1, stdout="", stderr="")

    result = verify.run_suite([("fake", "suite")], str(tmp_path), runner=runner)
    assert result["verified"] == [
        {
            "command": ("fake", "suite"),
            "ok": False,
            "exit_code": 1,
            "tail": "exit 1 — no output",
        }
    ]
    assert result["detail"] == "verification failed: fake suite — exit 1 — no output"


def test_only_the_red_row_after_green_rows_gains_an_exit_code(tmp_path: Path):
    # The passing path is unchanged: green rows keep their exact three keys.
    def runner(argv: list[str], cwd: str) -> CommandResult:
        if argv == ["red"]:
            return CommandResult(exit_code=1, stdout="1 failed\n", stderr="")
        return CommandResult(exit_code=0, stdout="fine\n", stderr="")

    result = verify.run_suite(["one", "two", "red"], str(tmp_path), runner=runner)
    assert result["passed"] is False
    assert result["verified"] == [
        {"command": "one", "ok": True, "tail": "fine"},
        {"command": "two", "ok": True, "tail": "fine"},
        {"command": "red", "ok": False, "exit_code": 1, "tail": "exit 1 — 1 failed"},
    ]
    assert set(result) == {"passed", "verified", "detail"}


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


def test_an_unrunnable_command_raises_verify_error_too(tmp_path: Path):
    # A non-executable file is the other half of the spec's "cannot be
    # launched" row: also a misconfigured card, never `passed: false`.
    def runner(argv: list[str], cwd: str) -> CommandResult:
        raise PermissionError(13, "Permission denied", argv[0])

    with pytest.raises(VerifyError) as excinfo:
        verify.run_suite(["./not-executable.sh"], str(tmp_path), runner=runner)
    assert "./not-executable.sh" in str(excinfo.value)


def _recorder() -> tuple[list[tuple[list[str], str]], verify.CommandRunner]:
    """A runner that records every call and always reports green."""
    calls: list[tuple[list[str], str]] = []

    def runner(argv: list[str], cwd: str) -> CommandResult:
        calls.append((argv, cwd))
        return CommandResult(exit_code=0, stdout="fine\n", stderr="")

    return calls, runner


def test_blank_and_empty_command_entries_are_skipped(tmp_path: Path):
    # `verify.filter(Boolean)` in the original: a card with a blank slot in its
    # fullSuite is not a broken card.
    calls, runner = _recorder()
    result = verify.run_suite(["", "   ", "echo hi", []], str(tmp_path), runner=runner)
    assert [argv for argv, _ in calls] == [["echo", "hi"]]
    assert result["passed"] is True
    assert [entry["command"] for entry in result["verified"]] == ["echo hi"]


def test_a_command_of_the_wrong_type_raises_before_anything_runs(tmp_path: Path):
    calls, runner = _recorder()
    with pytest.raises(ValueError):
        verify.run_suite(["echo hi", 7], str(tmp_path), runner=runner)
    with pytest.raises(ValueError):
        verify.run_suite([["echo", 7]], str(tmp_path), runner=runner)
    assert calls == []


def test_commands_that_are_not_iterable_raise_value_error(tmp_path: Path):
    with pytest.raises(ValueError):
        verify.run_suite(7, str(tmp_path))


def test_a_missing_relative_or_non_directory_worktree_raises(tmp_path: Path):
    calls, runner = _recorder()
    with pytest.raises(ValueError):
        verify.run_suite(["echo hi"], "relative/path", runner=runner)
    with pytest.raises(ValueError):
        verify.run_suite(["echo hi"], str(tmp_path / "missing"), runner=runner)
    a_file = tmp_path / "file.txt"
    a_file.write_text("not a directory")
    with pytest.raises(ValueError):
        verify.run_suite(["echo hi"], str(a_file), runner=runner)
    assert calls == []


def test_a_command_string_is_split_into_argv_and_never_handed_to_a_shell(
    tmp_path: Path,
):
    calls, runner = _recorder()
    verify.run_suite(
        ["uv run pytest -k 'not slow' > /tmp/out"], str(tmp_path), runner=runner
    )
    # Quoted arguments survive as one element; the redirection is literal argv,
    # not something a shell will act on.
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest", "-k", "not slow", ">", "/tmp/out"]
    ]


def test_shell_metacharacters_are_not_interpreted_by_a_real_process(tmp_path: Path):
    marker = tmp_path / "shell-ran.txt"
    command = (
        f"{_py('print(1)')} ; touch {shlex.quote(str(marker))}"
    )
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is True
    assert not marker.exists()


def test_argv_sequences_and_a_path_worktree_are_accepted(tmp_path: Path):
    calls, runner = _recorder()
    result = verify.run_suite((("echo", "hi"),), tmp_path, runner=runner)
    assert calls == [(["echo", "hi"], str(tmp_path))]
    assert result["verified"][0]["command"] == ("echo", "hi")
    assert result["passed"] is True


# --- Explore's typecheck and lint (card cf8b3888, pygents design G9 item 4) ---
#
# The engine binds `explore` by parameter name from the running context, and
# hands `run_suite` the Explore phase's dumped `ExploreResult`. Recorder tests
# observe ordering; the failure path is proved against real processes.


def test_run_suite_takes_explore_by_name_before_the_keyword_only_runner():
    # `walk.bind_arguments` binds strictly by parameter name, so the name
    # `explore` is what wires the Explore phase's result in -- no YAML edit.
    parameters = inspect.signature(verify.run_suite).parameters
    assert list(parameters) == ["commands", "worktree", "explore", "runner"]
    assert parameters["explore"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["explore"].default is None
    assert parameters["runner"].kind is inspect.Parameter.KEYWORD_ONLY


def test_typecheck_and_lint_run_after_the_suite_in_order(tmp_path: Path):
    calls, runner = _recorder()
    explore = {
        "verification": {
            "fullSuite": ["uv run pytest"],
            "typecheck": "uv run mypy",
            "lint": ["uv run ruff check"],
        }
    }
    result = verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "mypy"],
        ["uv", "run", "ruff", "check"],
    ]
    assert all(cwd == str(tmp_path) for _, cwd in calls)
    assert result["passed"] is True
    assert result["detail"] == ""
    assert result["verified"] == [
        {"command": "uv run pytest", "ok": True, "tail": "fine"},
        {"command": "uv run mypy", "ok": True, "tail": "fine"},
        {"command": "uv run ruff check", "ok": True, "tail": "fine"},
    ]


def test_every_lint_command_runs_in_list_order(tmp_path: Path):
    calls, runner = _recorder()
    explore = {
        "verification": {
            "typecheck": "uv run mypy",
            "lint": ["uv run ruff check", "uv run ruff format --check"],
        }
    }
    verify.run_suite(["uv run pytest"], str(tmp_path), explore=explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "mypy"],
        ["uv", "run", "ruff", "check"],
        ["uv", "run", "ruff", "format", "--check"],
    ]


def test_the_snake_case_dump_the_engine_binds_is_read_and_full_suite_is_ignored(
    tmp_path: Path,
):
    # `dispatch.py` dumps `ExploreResult` without `by_alias=True`, so the real
    # bound value says `full_suite`. `commands` stays the suite's only source.
    calls, runner = _recorder()
    explore = {
        "verification": {
            "full_suite": ["uv run pytest", "uv run pytest tests/e2e"],
            "typecheck": "uv run mypy",
            "lint": ["uv run ruff check"],
        }
    }
    verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "mypy"],
        ["uv", "run", "ruff", "check"],
    ]


def test_no_explore_means_only_the_suite(tmp_path: Path):
    calls, runner = _recorder()
    result = verify.run_suite(["uv run pytest"], str(tmp_path), runner=runner)
    assert [argv for argv, _ in calls] == [["uv", "run", "pytest"]]
    assert result["passed"] is True


def test_an_empty_typecheck_and_empty_lint_run_only_the_suite(tmp_path: Path):
    calls, runner = _recorder()
    explore = {"verification": {"typecheck": "", "lint": []}}
    result = verify.run_suite(["x"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [["x"]]
    assert result["passed"] is True
    # The empty-suite shape is unchanged when Explore named nothing either.
    assert verify.run_suite([], str(tmp_path), explore, runner=runner) == {
        "passed": True,
        "verified": [],
        "detail": "",
    }


def test_a_blank_typecheck_and_blank_lint_entries_are_skipped(tmp_path: Path):
    calls, runner = _recorder()
    explore = {"verification": {"typecheck": "   ", "lint": ["", "  ", "uv run ruff check"]}}
    result = verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "ruff", "check"],
    ]
    assert [entry["command"] for entry in result["verified"]] == [
        "uv run pytest",
        "uv run ruff check",
    ]


@pytest.mark.parametrize(
    "explore",
    [
        None,
        "nonsense",
        7,
        [],
        {},
        {"other": {"typecheck": "uv run mypy"}},
        {"verification": None},
        {"verification": "uv run mypy"},
        {"verification": ["uv run mypy"]},
        {"verification": {}},
        {"verification": {"typecheck": None, "lint": None}},
    ],
)
def test_a_malformed_or_absent_verification_runs_only_the_suite(
    tmp_path: Path, explore: object
):
    calls, runner = _recorder()
    result = verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [["uv", "run", "pytest"]]
    assert result["passed"] is True


@pytest.mark.parametrize(
    "verification",
    [
        {"typecheck": "", "lint": [7]},
        {"typecheck": "", "lint": ["uv run ruff check", ["ruff", 7]]},
        {"typecheck": 7, "lint": []},
        {"typecheck": "", "lint": 7},
        # A bare string is not a list of commands: never split it into letters.
        {"typecheck": "", "lint": "uv run ruff check"},
        {"typecheck": "uv run 'mypy", "lint": []},
    ],
)
def test_a_wrong_type_explore_entry_raises_before_anything_runs(
    tmp_path: Path, verification: dict[str, object]
):
    calls, runner = _recorder()
    with pytest.raises(ValueError):
        verify.run_suite(
            ["uv run pytest"],
            str(tmp_path),
            {"verification": verification},
            runner=runner,
        )
    assert calls == []


def test_a_failing_typecheck_fails_the_suite_and_stops_lint(tmp_path: Path):
    marker = tmp_path / "lint-ran.txt"
    suite = _py("print('5 passed')")
    typecheck = _py(
        "import sys; sys.stderr.write('error: 2 type errors\\n'); sys.exit(1)"
    )
    lint = _py(f"open({str(marker)!r}, 'w').write('ran')")
    explore = {"verification": {"typecheck": typecheck, "lint": [lint]}}

    result = verify.run_suite([suite], str(tmp_path), explore)

    assert result["passed"] is False
    assert result["verified"] == [
        {"command": suite, "ok": True, "tail": "5 passed"},
        {
            "command": typecheck,
            "ok": False,
            "exit_code": 1,
            "tail": "exit 1 — error: 2 type errors",
        },
    ]
    assert (
        result["detail"]
        == f"verification failed: {typecheck} — exit 1 — error: 2 type errors"
    )
    assert not marker.exists()


def test_a_silent_failing_typecheck_still_carries_a_tail_and_a_detail(tmp_path: Path):
    typecheck = _py("raise SystemExit(3)")
    explore = {"verification": {"typecheck": typecheck, "lint": []}}
    result = verify.run_suite([_py("print('ok')")], str(tmp_path), explore)
    assert result["passed"] is False
    assert result["verified"][-1] == {
        "command": typecheck,
        "ok": False,
        "exit_code": 3,
        "tail": "exit 3 — no output",
    }
    assert result["detail"].startswith("verification failed:")
    assert "— exit 3 — no output" in result["detail"]


def test_a_failing_lint_after_a_green_typecheck_names_the_lint_command(tmp_path: Path):
    typecheck = _py("print('0 errors')")
    first_lint = _py("print('E501 line too long'); raise SystemExit(1)")
    marker = tmp_path / "second-lint-ran.txt"
    second_lint = _py(f"open({str(marker)!r}, 'w').write('ran')")
    explore = {"verification": {"typecheck": typecheck, "lint": [first_lint, second_lint]}}

    result = verify.run_suite([_py("print('ok')")], str(tmp_path), explore)

    assert result["passed"] is False
    assert [entry["ok"] for entry in result["verified"]] == [True, True, False]
    assert result["verified"][-1]["command"] == first_lint
    assert result["verified"][-1]["tail"] == "exit 1 — E501 line too long"
    assert (
        result["detail"]
        == f"verification failed: {first_lint} — exit 1 — E501 line too long"
    )
    assert not marker.exists()


def test_a_red_suite_command_stops_before_typecheck_runs(tmp_path: Path):
    marker = tmp_path / "typecheck-ran.txt"
    red = _py("raise SystemExit(1)")
    typecheck = _py(f"open({str(marker)!r}, 'w').write('ran')")
    explore = {"verification": {"typecheck": typecheck, "lint": []}}

    result = verify.run_suite([red], str(tmp_path), explore)

    assert result["passed"] is False
    assert [entry["command"] for entry in result["verified"]] == [red]
    assert not marker.exists()


def test_an_unlaunchable_typecheck_raises_verify_error(tmp_path: Path):
    # Like a missing `--verify` binary: a misconfigured card, not a red suite.
    ran: list[list[str]] = []

    def runner(argv: list[str], cwd: str) -> CommandResult:
        ran.append(argv)
        if argv[0] == "definitely-not-mypy":
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        return CommandResult(exit_code=0, stdout="fine\n", stderr="")

    explore = {"verification": {"typecheck": "definitely-not-mypy .", "lint": []}}
    with pytest.raises(VerifyError) as excinfo:
        verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert "definitely-not-mypy" in str(excinfo.value)
    assert ran == [["uv", "run", "pytest"], ["definitely-not-mypy", "."]]


def test_the_engine_binds_the_real_explore_dump_into_the_run(tmp_path: Path):
    # The wiring itself, not just the signature: the engine's own binder, fed a
    # context holding a validated `ExploreResult` dumped the way `dispatch.py`
    # dumps it, must hand Explore's typecheck and lint to `run_suite`.
    marker = tmp_path / "lint-ran.txt"
    suite = _py("print('5 passed')")
    typecheck = _py("print('0 errors')")
    lint = _py(f"open({str(marker)!r}, 'w').write('ran'); print('clean')")
    explore = ExploreResult(
        refused=False,
        reason=None,
        summary="verify runs Explore's typecheck and lint",
        verification=Verification(
            full_suite=[_py("raise SystemExit(9)")], typecheck=typecheck, lint=[lint]
        ),
    ).model_dump(mode="json")
    context = {"commands": [suite], "worktree": str(tmp_path), "explore": explore}

    kwargs = walk.bind_arguments(
        verify.run_suite, context, phase="verify", function="verify.run_suite"
    )
    result = verify.run_suite(**kwargs)

    assert result["passed"] is True
    assert [entry["command"] for entry in result["verified"]] == [suite, typecheck, lint]
    assert marker.read_text() == "ran"


def test_the_engine_binds_no_explore_when_the_workflow_has_no_explore_phase(
    tmp_path: Path,
):
    # The integrate workflow has no Explore phase: binding must still succeed
    # and run only the suite.
    suite = _py("print('5 passed')")
    context = {"commands": [suite], "worktree": str(tmp_path)}
    kwargs = walk.bind_arguments(
        verify.run_suite, context, phase="verify", function="verify.run_suite"
    )
    result = verify.run_suite(**kwargs)
    assert result == {
        "passed": True,
        "verified": [{"command": suite, "ok": True, "tail": "5 passed"}],
        "detail": "",
    }
