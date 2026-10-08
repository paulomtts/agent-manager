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
import json
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
    # `log_dir` is the name `walk.run_one_step` looks for to hand over the
    # attempt directory (spec e1b1e7d5, Decision 1). `run_id` is injected the
    # same way, and `card` is bound from the table (spec e2efd21d).
    parameters = inspect.signature(verify.run_suite).parameters
    assert list(parameters) == [
        "commands",
        "worktree",
        "explore",
        "runner",
        "log_dir",
        "run_id",
        "card",
    ]
    assert parameters["explore"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["explore"].default is None
    assert parameters["runner"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["log_dir"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["log_dir"].default is None
    for name in ("run_id", "card"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is None


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
    "none_like",
    [
        "none",
        "None",
        "NONE (CLAUDE.md: there is no separate lint or typecheck command)",
        "none - this repo has no typecheck",
        "n/a",
        "N/A: nothing to run",
        "null",
        "nil",
    ],
)
def test_a_none_like_typecheck_or_lint_entry_is_skipped_not_run(tmp_path: Path, none_like: str):
    # Explore is an LLM: for a repo with no typecheck it may write prose such as
    # "none (CLAUDE.md: ...)" instead of "". Running its first word as a program
    # escalated a whole run (VerifyError: could not run none).
    calls, runner = _recorder()
    explore = {"verification": {"typecheck": none_like, "lint": [none_like, "uv run ruff check"]}}
    result = verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "ruff", "check"],
    ]
    assert result["passed"] is True
    assert [entry["command"] for entry in result["verified"]] == [
        "uv run pytest",
        "uv run ruff check",
    ]


def test_a_real_command_that_merely_starts_with_none_letters_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("shutil.which", lambda head: f"/usr/bin/{head}")
    calls, runner = _recorder()
    explore = {"verification": {"typecheck": "nonexistent-checker --strict", "lint": []}}
    verify.run_suite([], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [["nonexistent-checker", "--strict"]]


def test_a_none_like_verify_command_from_the_cli_is_still_run(tmp_path: Path):
    # Only Explore's untrusted fields get the none-like leniency: a --verify
    # command the user typed is run exactly as given.
    calls, runner = _recorder()
    verify.run_suite(["none"], str(tmp_path), None, runner=runner)
    assert [argv for argv, _ in calls] == [["none"]]


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


def test_an_unlaunchable_typecheck_raises_verify_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("shutil.which", lambda head: f"/usr/bin/{head}")
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


# ── persisted output (spec e1b1e7d5) ─────────────────────────────────────────
#
# With `log_dir`, every command that ran gets one headed section in each of
# `stdout.log` and `stderr.log`, verbatim. Real processes where a real process
# can produce the stream; a fake runner for the signal code and the
# unlaunchable command, as the rest of this module does.


def _logs(log_dir: Path) -> tuple[str, str]:
    return (
        (log_dir / "stdout.log").read_text(encoding="utf-8"),
        (log_dir / "stderr.log").read_text(encoding="utf-8"),
    )


def _tree(root: Path) -> list[str]:
    """Every path under `root`, relative and sorted; `[]` when absent."""
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def test_a_red_commands_full_streams_are_logged_verbatim_under_its_header(
    tmp_path: Path,
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    command = _py(
        "import sys; "
        "sys.stdout.write('A' * 700 + '\\nline two\\n7 passed\\n'); "
        "sys.stderr.write('E first\\nE second\\n'); "
        "raise SystemExit(1)"
    )

    result = verify.run_suite([command], str(worktree), log_dir=log_dir)

    assert result["passed"] is False
    stdout, stderr = _logs(log_dir)
    assert stdout == f"==> {command} (exit 1)\n" + "A" * 700 + "\nline two\n7 passed\n"
    assert stderr == f"==> {command} (exit 1)\nE first\nE second\n"


def test_a_clean_suite_keeps_both_logs_with_one_section_per_command_in_order(
    tmp_path: Path,
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Stale content from an earlier writer is truncated, never appended to.
    (log_dir / "stdout.log").write_text("stale\n", encoding="utf-8")
    (log_dir / "stderr.log").write_text("stale\n", encoding="utf-8")
    first = _py("import sys; print('one'); sys.stderr.write('warn one\\n')")
    second = _py("print('two')")

    result = verify.run_suite([first, second], str(worktree), log_dir=log_dir)

    assert result["passed"] is True
    stdout, stderr = _logs(log_dir)
    assert stdout == f"==> {first} (exit 0)\none\n==> {second} (exit 0)\ntwo\n"
    assert stderr == f"==> {first} (exit 0)\nwarn one\n==> {second} (exit 0)\n"


def test_a_command_after_a_red_one_never_runs_and_gets_no_section(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    red = _py("print('red'); raise SystemExit(2)")
    never = _py("print('never')")

    verify.run_suite([red, never], str(worktree), log_dir=log_dir)

    stdout, stderr = _logs(log_dir)
    assert stdout == f"==> {red} (exit 2)\nred\n"
    assert stderr == f"==> {red} (exit 2)\n"
    assert stdout.count("==> ") == 1
    assert stderr.count("==> ") == 1


def test_no_log_dir_writes_nothing_and_returns_the_same_result(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    data_home = Path(os.environ["XDG_DATA_HOME"])
    commands = [_py("print('ok')"), _py("print('bad'); raise SystemExit(1)")]
    worktree_before = _tree(worktree)
    data_before = _tree(data_home)

    without = verify.run_suite(commands, str(worktree))

    assert _tree(worktree) == worktree_before
    assert _tree(data_home) == data_before
    with_logs = verify.run_suite(commands, str(worktree), log_dir=log_dir)
    assert with_logs == without
    assert set(with_logs) == {"passed", "verified", "detail"}


def test_a_stream_without_a_trailing_newline_still_ends_its_section_on_a_line_of_its_own(
    tmp_path: Path,
):
    outcomes = {
        "first": CommandResult(exit_code=0, stdout="no newline", stderr=""),
        "second": CommandResult(exit_code=-9, stdout="", stderr="killed"),
    }

    def runner(argv: list[str], cwd: str) -> CommandResult:
        return outcomes[argv[0]]

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    verify.run_suite(["first", "second"], str(tmp_path), runner=runner, log_dir=log_dir)

    stdout, stderr = _logs(log_dir)
    assert stdout == (
        "==> first (exit 0)\nno newline\n==> second (exit -9 (signal SIGKILL))\n"
    )
    assert stderr == (
        "==> first (exit 0)\n==> second (exit -9 (signal SIGKILL))\nkilled\n"
    )


def test_an_unlaunchable_command_keeps_the_sections_of_the_commands_before_it(
    tmp_path: Path,
):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        if argv[0] == "missing-cmd":
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        return CommandResult(exit_code=0, stdout="fine\n", stderr="warn\n")

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with pytest.raises(VerifyError) as excinfo:
        verify.run_suite(
            ["ok-cmd", "missing-cmd"], str(tmp_path), runner=runner, log_dir=log_dir
        )

    assert "missing-cmd" in str(excinfo.value)
    stdout, stderr = _logs(log_dir)
    assert stdout == "==> ok-cmd (exit 0)\nfine\n"
    assert stderr == "==> ok-cmd (exit 0)\nwarn\n"


def test_an_unlaunchable_first_command_leaves_both_logs_present_and_empty(
    tmp_path: Path,
):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with pytest.raises(VerifyError):
        verify.run_suite(["missing-cmd"], str(tmp_path), runner=runner, log_dir=log_dir)

    assert _logs(log_dir) == ("", "")


def test_explore_typecheck_and_lint_get_sections_after_the_suite(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    suite = _py("print('suite')")
    typecheck = _py("print('types')")
    lint = _py("print('lint')")
    explore = {"verification": {"typecheck": typecheck, "lint": [lint]}}

    verify.run_suite([suite], str(worktree), explore, log_dir=log_dir)

    stdout, stderr = _logs(log_dir)
    assert stdout == (
        f"==> {suite} (exit 0)\nsuite\n"
        f"==> {typecheck} (exit 0)\ntypes\n"
        f"==> {lint} (exit 0)\nlint\n"
    )
    assert stderr == (
        f"==> {suite} (exit 0)\n==> {typecheck} (exit 0)\n==> {lint} (exit 0)\n"
    )


def test_a_huge_output_is_logged_in_full(tmp_path: Path):
    # Review Focus 1: the 300/600-character caps belong to `tail`/`detail`
    # only; the log is the place the whole stream survives.
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    command = _py("import sys; sys.stdout.write('y' * 200000)")

    result = verify.run_suite([command], str(worktree), log_dir=log_dir)

    assert result["passed"] is True
    stdout, _ = _logs(log_dir)
    assert stdout == f"==> {command} (exit 0)\n" + "y" * 200000 + "\n"


def test_undecodable_output_is_logged_with_replacement_characters(tmp_path: Path):
    # Review Focus 2: `run_command` already replaced the bad byte; writing the
    # replacement character back out must not raise.
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    command = _py("import sys; sys.stdout.buffer.write(b'ok \\xff done\\n')")

    verify.run_suite([command], str(worktree), log_dir=log_dir)

    stdout, _ = _logs(log_dir)
    assert stdout == f"==> {command} (exit 0)\nok � done\n"


def test_an_unwritable_log_dir_raises_before_any_command_runs(tmp_path: Path):
    # Review Focus 5: a log that cannot be written is an error, never a green
    # suite with its evidence silently missing.
    calls, runner = _recorder()

    with pytest.raises(OSError):
        verify.run_suite(
            ["uv run pytest"], str(tmp_path), runner=runner, log_dir=tmp_path / "absent"
        )

    assert calls == []


# ── AM_RUN_ID / AM_CARD_ID in a verification command's environment (e2efd21d) ─
#
# The real-subprocess tests spawn `sys.executable` only, `tmp_path` only: git
# tier by this directory's auto-mark, like the file's other real-process tests.

_ENV_PROBE = (
    "import json, os; print(json.dumps({k: os.environ.get(k) for k in "
    "('AM_RUN_ID', 'AM_CARD_ID', 'AM_TEST_SENTINEL', 'PATH')}))"
)
"""A child that prints the four variables these tests care about as JSON."""


def _child_env(tmp_path: Path, env=None) -> dict[str, object]:
    """Run `_ENV_PROBE` through `run_command` and decode what the child saw."""
    argv = [sys.executable, "-c", _ENV_PROBE]
    if env is None:
        completed = verify.run_command(argv, str(tmp_path))
    else:
        completed = verify.run_command(argv, str(tmp_path), env=env)
    assert completed.exit_code == 0, completed.stderr
    return json.loads(completed.stdout)


def test_run_command_overlays_both_ids_on_the_inherited_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AM_TEST_SENTINEL", "kept")
    monkeypatch.delenv("AM_RUN_ID", raising=False)
    monkeypatch.delenv("AM_CARD_ID", raising=False)

    seen = _child_env(tmp_path, env={"AM_RUN_ID": "r-1", "AM_CARD_ID": "c-1"})

    assert seen["AM_RUN_ID"] == "r-1"
    assert seen["AM_CARD_ID"] == "c-1"
    assert seen["AM_TEST_SENTINEL"] == "kept"
    assert seen["PATH"] == os.environ["PATH"]


def test_run_command_overlay_replaces_a_stale_inherited_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Review Focus 1: an `am` running as some outer run's verification.
    monkeypatch.setenv("AM_RUN_ID", "stale")
    monkeypatch.setenv("AM_CARD_ID", "stale")
    monkeypatch.setenv("AM_TEST_SENTINEL", "kept")

    seen = _child_env(tmp_path, env={"AM_RUN_ID": "r-1", "AM_CARD_ID": "c-1"})

    assert seen["AM_RUN_ID"] == "r-1"
    assert seen["AM_CARD_ID"] == "c-1"
    assert seen["AM_TEST_SENTINEL"] == "kept"


def test_run_command_without_env_never_leaks_an_inherited_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AM_RUN_ID", "stale")
    monkeypatch.setenv("AM_CARD_ID", "stale")
    monkeypatch.setenv("AM_TEST_SENTINEL", "kept")

    seen = _child_env(tmp_path)

    assert seen["AM_RUN_ID"] is None
    assert seen["AM_CARD_ID"] is None
    assert seen["AM_TEST_SENTINEL"] == "kept"
    assert seen["PATH"] == os.environ["PATH"]


def test_run_command_overlay_with_one_id_still_drops_the_other_inherited_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AM_RUN_ID", "stale")
    monkeypatch.setenv("AM_CARD_ID", "stale")

    seen = _child_env(tmp_path, env={"AM_CARD_ID": "c-1"})

    assert seen["AM_RUN_ID"] is None
    assert seen["AM_CARD_ID"] == "c-1"


def test_run_command_does_not_change_the_parents_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AM_RUN_ID", "outer")
    monkeypatch.delenv("AM_CARD_ID", raising=False)

    _child_env(tmp_path, env={"AM_RUN_ID": "r-1", "AM_CARD_ID": "c-1"})

    assert os.environ["AM_RUN_ID"] == "outer"
    assert "AM_CARD_ID" not in os.environ


def _env_recorder() -> tuple[list[tuple[tuple, dict]], verify.CommandRunner]:
    """A runner that accepts `env` and records each call's args and kwargs."""
    calls: list[tuple[tuple, dict]] = []

    def runner(*args, **kwargs) -> CommandResult:
        calls.append((args, kwargs))
        return CommandResult(exit_code=0, stdout="fine\n", stderr="")

    return calls, runner


def test_both_ids_reach_every_planned_command_including_explore_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # T1
    monkeypatch.setattr("shutil.which", lambda head: f"/usr/bin/{head}")
    calls, runner = _env_recorder()
    explore = {"verification": {"typecheck": "mypy .", "lint": ["ruff check ."]}}

    result = verify.run_suite(
        ["uv run pytest"],
        str(tmp_path),
        explore,
        runner=runner,
        run_id="run-7",
        card="e2efd21d",
    )

    assert result["passed"] is True
    expected_env = {"AM_RUN_ID": "run-7", "AM_CARD_ID": "e2efd21d"}
    assert calls == [
        ((["uv", "run", "pytest"], str(tmp_path)), {"env": expected_env}),
        ((["mypy", "."], str(tmp_path)), {"env": expected_env}),
        ((["ruff", "check", "."], str(tmp_path)), {"env": expected_env}),
    ]


def test_no_ids_call_a_strict_two_parameter_runner_exactly_as_before(
    tmp_path: Path,
):
    # T2: a direct `run_suite` call (bases.py, integration.py) at the seam.
    calls, runner = _recorder()

    result = verify.run_suite(["a b", "c"], str(tmp_path), runner=runner)

    assert result["passed"] is True
    assert calls == [(["a", "b"], str(tmp_path)), (["c"], str(tmp_path))]


@pytest.mark.parametrize(
    ("ids", "overlay"),
    [
        ({"card": "c-1"}, {"AM_CARD_ID": "c-1"}),
        ({"run_id": "r-1"}, {"AM_RUN_ID": "r-1"}),
        ({"run_id": "", "card": "c-1"}, {"AM_CARD_ID": "c-1"}),
        ({"run_id": "r-1", "card": "   "}, {"AM_RUN_ID": "r-1"}),
    ],
)
def test_the_overlay_holds_only_the_present_ids(
    tmp_path: Path, ids: dict[str, str], overlay: dict[str, str]
):
    # T3
    calls, runner = _env_recorder()

    verify.run_suite(["a"], str(tmp_path), runner=runner, **ids)

    assert calls == [((["a"], str(tmp_path)), {"env": overlay})]


@pytest.mark.parametrize(
    "ids",
    [
        {"run_id": "", "card": ""},
        {"run_id": "   ", "card": "\t"},
        {"run_id": None, "card": "  "},
    ],
)
def test_blank_ids_count_as_absent(tmp_path: Path, ids: dict[str, object]):
    # T4: the runner gets two positional arguments and nothing else.
    calls, runner = _recorder()

    result = verify.run_suite(["a"], str(tmp_path), runner=runner, **ids)

    assert result["passed"] is True
    assert calls == [(["a"], str(tmp_path))]


@pytest.mark.parametrize(
    ("name", "value"), [("card", 123), ("run_id", ["r-1"]), ("card", b"c-1")]
)
def test_a_non_string_id_raises_before_anything_runs(
    tmp_path: Path, name: str, value: object
):
    # T5
    calls, runner = _env_recorder()

    with pytest.raises(ValueError) as excinfo:
        verify.run_suite(["a"], str(tmp_path), runner=runner, **{name: value})

    assert calls == []
    assert name in str(excinfo.value)
    assert repr(value) in str(excinfo.value)


def test_a_non_string_id_raises_before_the_logs_are_started(tmp_path: Path):
    calls, runner = _env_recorder()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with pytest.raises(ValueError):
        verify.run_suite(
            ["a"], str(tmp_path), runner=runner, log_dir=log_dir, run_id=7
        )

    assert calls == []
    assert list(log_dir.iterdir()) == []


def test_ids_do_not_change_the_result_or_the_logs(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    plain_logs = tmp_path / "plain"
    plain_logs.mkdir()
    id_logs = tmp_path / "ids"
    id_logs.mkdir()
    commands = [_py("print('ok')"), _py("print('bad'); raise SystemExit(1)")]

    without = verify.run_suite(commands, str(worktree), log_dir=plain_logs)
    with_ids = verify.run_suite(
        commands, str(worktree), log_dir=id_logs, run_id="r-1", card="c-1"
    )

    assert with_ids == without
    assert _logs(id_logs) == _logs(plain_logs)


def test_the_default_runner_exports_both_ids_to_a_real_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # T8: B2 and B1 composed, end to end through a real process. The
    # no-id half runs under a stale parent value too (Review Focus 1).
    monkeypatch.setenv("AM_RUN_ID", "stale")
    monkeypatch.setenv("AM_CARD_ID", "stale")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with_ids = tmp_path / "with-ids.json"
    without_ids = tmp_path / "without-ids.json"

    def probe(target: Path) -> str:
        return _py(
            "import json, os, pathlib; "
            f"pathlib.Path({str(target)!r}).write_text(json.dumps("
            "{k: os.environ.get(k) for k in ('AM_RUN_ID', 'AM_CARD_ID')}))"
        )

    first = verify.run_suite(
        [probe(with_ids)], str(worktree), run_id="run-7", card="e2efd21d"
    )
    second = verify.run_suite([probe(without_ids)], str(worktree))

    assert first["passed"] is True and second["passed"] is True
    assert json.loads(with_ids.read_text()) == {
        "AM_RUN_ID": "run-7",
        "AM_CARD_ID": "e2efd21d",
    }
    assert json.loads(without_ids.read_text()) == {
        "AM_RUN_ID": None,
        "AM_CARD_ID": None,
    }
