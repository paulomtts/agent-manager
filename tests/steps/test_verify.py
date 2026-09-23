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

from agent_manager.steps.verify import command_diagnostic, last_line, plain_text


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
