"""Golden brief: the coder and resolver forbid background-and-wait (card f78d6855).

Placement: pygents-engine-design.md §9 (lines 379-402) tests roles as golden
briefs -- the shipped bundle is loaded through the public loader and its
system text is checked for the instruction a one-shot dispatch depends on. No
model call, no git repository, no harness, same tier as `test_loader.py`.
"""

import pytest

from agent_manager.roles.loader import load_role

ONE_SHOT_RULE = """\
- You run headless and one-shot: the process exits the moment your turn ends.
  Run every command to completion in the foreground, however long it takes. If
  a command outlasts your shell tool's default timeout, raise the timeout
  rather than backgrounding it. Never background a command and end your turn
  to wait for a notification. No notification ever comes: the process dies
  without writing the result file, and the dispatch is lost.
"""

ROLES = ("coder", "resolver")

# The bullet each role's rule sits directly after (spec B2).
PRECEDING_LINE = {
    "coder": "  report its real output.",
    "resolver": "  merge is complete.",
}


def _normalised(text: str) -> str:
    return " ".join(text.split())


def _rule_span(text: str) -> tuple[list[str], int, int]:
    """Return the file's lines and the first/last line index of the rule."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("- You run headless")]
    assert len(starts) == 1, f"expected one rule bullet, found {len(starts)}"
    start = starts[0]
    ends = [i for i in range(start, len(lines)) if lines[i].endswith("dispatch is lost.")]
    assert ends, "the rule bullet never reaches 'dispatch is lost.'"
    return lines, start, ends[0]


def test_one_shot_rule_constant_is_ascii():
    assert ONE_SHOT_RULE.isascii()


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_is_in_the_brief(role):
    assert _normalised(ONE_SHOT_RULE) in _normalised(load_role(role).system)


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_names_the_forbidden_pattern_and_its_cost(role):
    text = _normalised(load_role(role).system)
    for phrase in (
        "headless and one-shot",
        "in the foreground",
        "raise the timeout",
        "Never background a command",
        "wait for a notification",
        "dispatch is lost",
    ):
        assert phrase in text, phrase


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_is_harness_neutral(role):
    # D6: a role's text is byte-identical whatever harness runs it. Scoped to
    # this card's rule; test_one_shot_rule_is_in_the_brief ties it to the file.
    assert _normalised(ONE_SHOT_RULE) in _normalised(load_role(role).system)
    for name in ("run_in_background", "Monitor", "Bash"):
        assert name not in ONE_SHOT_RULE, name


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_sits_in_the_opening_list(role):
    text = load_role(role).system
    lines, start, end = _rule_span(text)

    assert lines[start - 1] == PRECEDING_LINE[role]
    if role == "coder":
        assert text.index("- You run headless") < text.index("## The Plan-Hash trailer")
    else:
        assert end == len(lines) - 1, "the rule is not the resolver's last bullet"


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_is_one_unbroken_list_item(role):
    lines, start, end = _rule_span(load_role(role).system)

    assert lines[start - 1].strip(), "a blank line separates the rule from the list"
    for line in lines[start + 1 : end + 1]:
        assert line.startswith("  ") and line.strip(), repr(line)
