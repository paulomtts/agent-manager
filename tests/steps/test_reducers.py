"""Unit tests for the gates ported from task.js.

Ported case-by-case from the sibling plugin's `workflows/task.test.mjs`
(lines 30-186), which is the behavioural specification for these gates.
"""

import pytest

from agent_manager.steps import reducers
from agent_manager.steps.reducers import exploration_output_gate, verification_gate


def test_a_discovered_suite_proceeds():
    assert verification_gate(["npm test"], None, False) is None


def test_an_empty_suite_is_a_hard_stop_not_a_warning():
    gate = verification_gate([], None, False)
    assert gate["blocked"] == "verification"
    assert "still report success" in gate["detail"]


def test_the_empty_suite_stop_names_the_caller_when_the_caller_supplied_the_empty_list():
    # Which half of the pipeline to go fix differs entirely: an empty list the
    # orchestrator passed down means the BASE BRANCH documents no commands.
    caller = verification_gate([], None, True)
    exploration = verification_gate([], None, False)
    assert "orchestrator discovers these from origin" in caller["detail"]
    assert "Exploration found none" in exploration["detail"]


def test_allow_no_verification_true_is_the_only_way_past_an_empty_suite():
    assert verification_gate([], True, False) is None


@pytest.mark.parametrize("sloppy", ["true", 1, {}, "yes"])
def test_a_truthy_stand_in_does_not_open_the_gate(sloppy):
    # The opt-out is deliberate, so the check is strict identity with True.
    assert verification_gate([], sloppy, False)["blocked"] == "verification"


def test_a_discovered_suite_proceeds_whatever_the_other_two_arguments_say():
    assert verification_gate(["uv run pytest"], False, True) is None
    assert verification_gate(["uv run pytest"], None, None) is None


@pytest.mark.parametrize("caller_provided", [True, False])
def test_the_blocked_detail_always_offers_the_three_remedies(caller_provided):
    detail = verification_gate([], None, caller_provided)["detail"]
    assert detail.endswith(
        " Document the command, pass verification.fullSuite explicitly, or set "
        "allowNoVerification: true to proceed unverified on purpose."
    )


# ── exploration_output_gate ──────────────────────────────────────────────────
# Ported from task.test.mjs:151-187. Added upstream after a live run (#1296)
# where the explore agent did real work, then gave up and submitted a
# placeholder that trivially satisfies the schema: summary="test",
# verification.fullSuite=["a"].

REAL_SUMMARY = (
    "graph_canvas.js renderEdges (lines 228-253) needs a transparent hit-path emitted "
    "before the visible path, per issue #1296; graph_shell.css needs the matching "
    "cursor rule."
)
REAL_VERIFICATION = {"fullSuite": ["uv run pytest tests/unit -q", "uv run ruff check ."]}


def test_a_real_summary_and_plausible_verification_pass_the_gate():
    explore = {"summary": REAL_SUMMARY, "verification": REAL_VERIFICATION}
    assert exploration_output_gate(explore, None) is None


def test_the_1296_placeholder_output_is_caught_as_a_short_summary():
    gate = exploration_output_gate({"summary": "test", "verification": {"fullSuite": ["a"]}}, None)
    assert "implausibly short" in gate["detail"]
    assert "blocked" not in gate


@pytest.mark.parametrize("word", ["todo", "TBD", "n/a", "None", "placeholder"])
def test_a_summary_that_is_exactly_a_placeholder_word_is_caught(word):
    # These are all far under MIN_SUMMARY_LENGTH, so it is the length half of
    # the check that fires here; the placeholder set is pinned separately below.
    gate = exploration_output_gate({"summary": word, "verification": REAL_VERIFICATION}, None)
    assert gate is not None


def test_the_placeholder_set_is_what_rejects_a_placeholder_once_the_floor_is_lowered(monkeypatch):
    # Every member of PLACEHOLDER_SUMMARIES is shorter than MIN_SUMMARY_LENGTH,
    # so the set is unreachable at the shipped floor. Drop the floor and the
    # set must still do its job case-insensitively — otherwise the second half
    # of the condition could be deleted with no test noticing.
    monkeypatch.setattr(reducers, "MIN_SUMMARY_LENGTH", 0)
    for word in sorted(reducers.PLACEHOLDER_SUMMARIES):
        for spelling in (word, word.upper(), f"  {word}  "):
            gate = exploration_output_gate(
                {"summary": spelling, "verification": REAL_VERIFICATION}, None
            )
            assert gate is not None, spelling
            assert "implausibly short/placeholder" in gate["detail"]
    assert (
        exploration_output_gate(
            {"summary": "not a placeholder", "verification": REAL_VERIFICATION}, None
        )
        is None
    )


def test_the_summary_floor_is_exactly_sixty_characters():
    # Pins MIN_SUMMARY_LENGTH itself: without this, any floor between the
    # longest placeholder word and the length of REAL_SUMMARY passes the suite.
    at_floor = "x" * 60
    below_floor = "x" * 59
    assert (
        exploration_output_gate({"summary": at_floor, "verification": REAL_VERIFICATION}, None)
        is None
    )
    gate = exploration_output_gate(
        {"summary": below_floor, "verification": REAL_VERIFICATION}, None
    )
    assert "implausibly short" in gate["detail"]


def test_a_padded_placeholder_word_is_stripped_before_the_lookup():
    gate = exploration_output_gate({"summary": "  todo  ", "verification": REAL_VERIFICATION}, None)
    assert "implausibly short" in gate["detail"]
    # The detail carries the STRIPPED summary, JSON-quoted.
    assert '"todo"' in gate["detail"]


def test_a_long_whitespace_only_summary_is_short_once_stripped():
    gate = exploration_output_gate({"summary": " " * 100, "verification": REAL_VERIFICATION}, None)
    assert gate["detail"].endswith('subtask: ""')


@pytest.mark.parametrize("explore", [None, {}, {"summary": None}, {"summary": 7}])
def test_a_missing_or_non_string_summary_returns_a_verdict_instead_of_raising(explore):
    gate = exploration_output_gate(explore, None)
    assert "implausibly short" in gate["detail"]


def test_a_verification_mapping_without_full_suite_is_caught_not_raised():
    gate = exploration_output_gate({"summary": REAL_SUMMARY, "verification": {}}, None)
    assert gate["detail"] == "exploration did not return an array for verification.fullSuite"


def test_caller_provided_verification_must_come_back_exactly_unchanged():
    provided = {"fullSuite": ["make test", "make lint"]}
    explore = {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["make test", "make lint"]}}
    assert exploration_output_gate(explore, provided) is None


def test_any_deviation_from_caller_provided_verification_fails():
    provided = {"fullSuite": ["make test", "make lint"]}
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["a"]}}, provided
    )
    assert "did not return the caller-provided verification" in gate["detail"]
    assert '(expected ["make test","make lint"], got ["a"])' in gate["detail"]


def test_an_implausible_but_exactly_matching_full_suite_passes():
    # Pins the deliberate early return: in the caller-provided branch the
    # implausible-command check is NOT applied (task.js line 146).
    provided = {"fullSuite": ["a"]}
    explore = {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["a"]}}
    assert exploration_output_gate(explore, provided) is None


def test_a_caller_provided_mapping_without_full_suite_expects_an_empty_list():
    assert (
        exploration_output_gate(
            {"summary": REAL_SUMMARY, "verification": {"fullSuite": []}}, {"note": "none found"}
        )
        is None
    )
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": REAL_VERIFICATION}, {"note": "none found"}
    )
    assert "(expected [], got " in gate["detail"]


def test_a_python_equal_but_json_different_list_is_still_a_deviation():
    # JSON renders True as `true` and 1 as `1`; the JS compares the rendered
    # strings, so [True] is not [1] here even though Python says it is.
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": [True]}}, {"fullSuite": [1]}
    )
    assert "(expected [1], got [true])" in gate["detail"]


def test_an_explore_with_no_verification_key_at_all_is_caught_not_raised():
    gate = exploration_output_gate({"summary": REAL_SUMMARY}, None)
    assert gate["detail"] == "exploration did not return an array for verification.fullSuite"


@pytest.mark.parametrize("not_a_list", ["uv run pytest", {"0": "uv run pytest"}, 3, None])
def test_a_non_array_full_suite_is_caught(not_a_list):
    # A bare string is iterable in Python but is not an array in JS; it must be
    # rejected, never scanned character by character.
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": not_a_list}}, None
    )
    assert gate["detail"] == "exploration did not return an array for verification.fullSuite"


def test_a_single_letter_full_suite_command_is_caught():
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["a"]}}, None
    )
    assert "implausible command" in gate["detail"]
    assert gate["detail"].endswith('["a"]')


@pytest.mark.parametrize("bad", [3, None, ["uv run pytest"], "  x  ", ""])
def test_a_non_string_or_too_short_entry_is_caught_not_raised(bad):
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["uv run pytest", bad]}}, None
    )
    assert "implausible command" in gate["detail"]


def test_an_entry_json_cannot_serialise_still_produces_a_verdict():
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": [object()]}}, None
    )
    assert "implausible command" in gate["detail"]


def test_an_empty_full_suite_is_not_an_implausible_command():
    # Emptiness is verification_gate's business, not this gate's.
    assert (
        exploration_output_gate({"summary": REAL_SUMMARY, "verification": {"fullSuite": []}}, None)
        is None
    )


def test_an_empty_caller_provided_mapping_takes_the_plausibility_branch():
    # {} is truthy in JS but falsy in Python, so the port checks plausibility
    # here rather than exact equality. Recorded deliberately.
    explore_ok = {"summary": REAL_SUMMARY, "verification": REAL_VERIFICATION}
    assert exploration_output_gate(explore_ok, {}) is None
    explore_bad = {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["a"]}}
    assert "implausible command" in exploration_output_gate(explore_bad, {})["detail"]
