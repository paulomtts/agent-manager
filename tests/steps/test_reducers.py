"""Unit tests for the gates ported from task.js.

Ported case-by-case from the sibling plugin's `workflows/task.test.mjs`
(lines 30-186), which is the behavioural specification for these gates.
"""

import pytest

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
def test_a_summary_that_is_exactly_a_placeholder_word_is_caught_case_insensitively(word):
    gate = exploration_output_gate({"summary": word, "verification": REAL_VERIFICATION}, None)
    assert gate is not None


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
