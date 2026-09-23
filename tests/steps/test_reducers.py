"""Unit tests for the gates ported from task.js.

Ported case-by-case from the sibling plugin's `workflows/task.test.mjs`
(lines 30-186), which is the behavioural specification for these gates.
"""

import math

import pytest

from agent_manager.steps import reducers
from agent_manager.steps.reducers import (
    _is_integer,
    _js_text,
    count_of,
    exploration_output_gate,
    is_plan_hash,
    plan_hash_gate,
    plan_hash_mismatch,
    review_gate,
    verification_gate,
)


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


# ── count_of ─────────────────────────────────────────────────────────────────
# No JS counterpart: Python's coercions are looser than JS's, so the port needs
# its own pins. task.js lines 79-89 explain why "unusable" must never collapse
# to zero — a Review that reported no count at all would otherwise be judged as
# having found ZERO COMMITS and stop the run blaming Implement.


@pytest.mark.parametrize(("value", "expected"), [(3, 3), (0, 0), (-2, -2), (1.5, 1.5)])
def test_a_real_number_is_returned_as_is(value, expected):
    assert count_of(value) == expected


@pytest.mark.parametrize("text", ["3", " 3 ", "3.0", "+3", "1e3", "\t3\n"])
def test_a_numeric_string_is_parsed(text):
    assert _is_integer(count_of(text))


def test_a_numeric_string_parses_to_its_value():
    assert count_of("3") == 3
    assert count_of(" 3 ") == 3
    assert count_of("1e3") == 1000


@pytest.mark.parametrize(
    "value", [None, "", "   ", "three", [], {}, object(), b"3", 3j]
)
def test_an_unusable_value_is_not_a_number_and_not_zero(value):
    result = count_of(value)
    assert math.isnan(result)
    assert result != 0
    assert not _is_integer(result)


@pytest.mark.parametrize("value", [True, False])
def test_a_bool_is_not_a_count(value):
    # typeof true !== 'number' in JS, but bool is an int subclass in Python.
    # Reading True as 1 would invent a commit that nobody counted.
    assert math.isnan(count_of(value))
    assert not _is_integer(count_of(value))


@pytest.mark.parametrize("text", ["1_0", "٣", "inf", "Infinity", "nan", "0x10", "1,0"])
def test_a_python_only_numeric_spelling_is_unusable(text):
    # Python's float()/int() accept all of these; JS Number() returns NaN for
    # every one. Accepting them would read garbage as a real count.
    assert math.isnan(count_of(text))


def test_is_integer_matches_number_is_integer():
    assert _is_integer(3)
    assert _is_integer(3.0)
    assert _is_integer(-0.0)
    assert not _is_integer(1.5)
    assert not _is_integer(math.nan)
    assert not _is_integer(math.inf)
    assert not _is_integer(True)
    assert not _is_integer("3")
    assert not _is_integer(None)


def test_js_text_renders_values_the_way_a_template_literal_does():
    assert _js_text(None) == "null"
    assert _js_text(True) == "true"
    assert _js_text(False) == "false"
    assert _js_text("three") == "three"
    assert _js_text("") == ""
    assert _js_text(3) == "3"
    assert _js_text(3.0) == "3"
    assert _js_text(1.5) == "1.5"
    assert _js_text(math.nan) == "NaN"


# ── review_gate ──────────────────────────────────────────────────────────────
# Ported from task.test.mjs:59-139. The Review -> Ship boundary: Review REPORTS
# three facts, this gate judges them, before anything is pushed.

BRANCH = "task-42"
BASE = "main"


def clean(**overrides):
    """A review result that passes every check, with overrides applied."""
    base = {"porcelain": "", "commitCount": 3, "taggedCount": 3}
    base.update(overrides)
    return base


def test_a_clean_tree_with_tagged_commits_proceeds_to_ship():
    assert review_gate(clean(), BRANCH, BASE) is None


def test_a_dirty_worktree_stops_the_run_before_anything_is_pushed():
    gate = review_gate(clean(porcelain=" M src/a.js"), BRANCH, BASE)
    assert gate["blocked"] == "tests"
    assert "nothing was pushed" in gate["detail"]
    # The evidence travels with the verdict.
    assert "M src/a.js" in gate["detail"]


@pytest.mark.parametrize("blank", ["\n", "   ", "\t\n ", ""])
def test_whitespace_only_porcelain_is_a_clean_tree(blank):
    # git prints a trailing newline even when it has nothing to say; treating
    # that as dirt would block every single run.
    assert review_gate(clean(porcelain=blank), BRANCH, BASE) is None


def test_the_dirty_tree_check_runs_before_the_commit_counts():
    # A dirty tree means the counts describe a branch that is missing work, so
    # reporting the count problem first would send someone after the wrong bug.
    gate = review_gate(
        {"porcelain": "?? new.js", "commitCount": 0, "taggedCount": 0}, BRANCH, BASE
    )
    assert gate["blocked"] == "tests"


def test_a_non_string_porcelain_is_coerced_not_raised_on():
    # String(x || '') in JS: falsy becomes "", truthy becomes its text.
    assert review_gate(clean(porcelain=0), BRANCH, BASE) is None
    assert review_gate(clean(porcelain=False), BRANCH, BASE) is None
    assert review_gate(clean(porcelain=None), BRANCH, BASE) is None
    assert review_gate(clean(porcelain=5), BRANCH, BASE)["blocked"] == "tests"
    assert review_gate(clean(porcelain=["?? a"]), BRANCH, BASE)["blocked"] == "tests"


def test_zero_commits_stops_the_run_as_an_implement_failure():
    gate = review_gate(clean(commitCount=0, taggedCount=0), BRANCH, BASE)
    assert gate["blocked"] == "implement"
    assert "task-42 has no commits on top of main" in gate["detail"]


def test_an_untagged_commit_stops_the_run_because_a_later_run_would_hard_reset_it():
    gate = review_gate(clean(commitCount=3, taggedCount=2), BRANCH, BASE)
    assert gate["blocked"] == "implement"
    assert "only 2 of 3 commits" in gate["detail"]
    # Re-running is the destructive move, so the verdict must say so.
    assert "Do NOT re-run this subtask" in gate["detail"]


def test_more_trailers_than_commits_is_not_a_failure():
    # A commit can legitimately carry the trailer twice, or a merge can inflate
    # the count. The gate only cares that nothing is MISSING one.
    assert review_gate(clean(commitCount=3, taggedCount=4), BRANCH, BASE) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"commitCount": None},
        {"commitCount": ""},
        {"commitCount": "three"},
        {"commitCount": 1.5},
        {"taggedCount": None},
        {"taggedCount": float("nan")},
        {"taggedCount": True},
        {"commitCount": True},
        {"commitCount": "1_0"},
        {"taggedCount": []},
    ],
)
def test_unusable_counts_warn_and_skip_rather_than_blocking_or_passing(bad):
    # None and "" are the sharp ones: float() would turn both into 0, which
    # would read as "zero commits" and stop the run blaming Implement for a
    # fact nobody ever measured.
    gate = review_gate(clean(**bad), BRANCH, BASE)
    assert gate["warn"]
    assert "blocked" not in gate
    assert "Plan-Hash gate skipped" in gate["warn"]


def test_the_warn_names_both_raw_reported_values_js_style():
    gate = review_gate({"commitCount": None, "taggedCount": True}, BRANCH, BASE)
    assert "(null/true)" in gate["warn"]


@pytest.mark.parametrize("missing", [None, {}, {"porcelain": ""}, "x", ["x"], 7])
def test_a_missing_or_non_mapping_review_warns_instead_of_raising(missing):
    # Reachability is a property of the caller, and the caller is exactly the
    # thing that changes, so the gate stays independently safe.
    gate = review_gate(missing, BRANCH, BASE)
    assert gate["warn"]
    assert "blocked" not in gate


def test_a_numeric_string_count_is_still_usable():
    # The schema asks for integers, but models do hand back "3". Rejecting that
    # would skip the gate on a branch that could have been checked.
    assert review_gate(clean(commitCount="3", taggedCount="3"), BRANCH, BASE) is None
    gate = review_gate(clean(commitCount="3", taggedCount="2"), BRANCH, BASE)
    assert gate["blocked"] == "implement"


def test_numeric_string_counts_are_rendered_without_a_python_float_tail():
    # float("3") is 3.0; "only 2.0 of 3.0 commits" would read as a bug report
    # about the gate rather than about the branch.
    gate = review_gate(clean(commitCount="3", taggedCount="2"), BRANCH, BASE)
    assert "only 2 of 3 commits" in gate["detail"]


def test_a_zero_count_from_a_numeric_string_still_blocks_as_implement():
    gate = review_gate(clean(commitCount="0", taggedCount="0"), BRANCH, BASE)
    assert gate["blocked"] == "implement"
    assert "no commits on top of" in gate["detail"]


# ── is_plan_hash / plan_hash_mismatch ────────────────────────────────────────
# Ported from task.test.mjs:189-220. A Plan-Hash is the first 8 hex characters
# of sha256sum(<plan file>). Implement writes the trailers; Review recomputes
# the hash independently, so comparing the two catches the plan file changing
# mid-run — which silently invalidates every trailer already written.


@pytest.mark.parametrize("good", ["a1b2c3d4", "00000000", "ffffffff", "0123456789abcdef"[:8]])
def test_a_plan_hash_is_exactly_eight_lowercase_hex_characters(good):
    assert is_plan_hash(good) is True


@pytest.mark.parametrize(
    "bad",
    [
        "A1B2C3D4",
        "a1b2c3d",
        "a1b2c3d4e",
        "a1b2c3g4",
        "",
        "  a1b2c3d4",
        "a1b2c3d4 ",
        None,
        12345678,
        b"a1b2c3d4",
        ["a1b2c3d4"],
    ],
)
def test_anything_else_is_not_a_plan_hash(bad):
    assert is_plan_hash(bad) is False


def test_a_trailing_newline_does_not_sneak_a_hash_through():
    # Python's `$` also matches before a final newline, so the pattern must be
    # anchored with fullmatch (or \Z). A hash read straight off a command's
    # stdout is exactly how this gets hit.
    assert is_plan_hash("a1b2c3d4\n") is False


def test_matching_hashes_report_no_drift():
    assert plan_hash_mismatch("a1b2c3d4", "a1b2c3d4") is None


def test_a_hash_that_changed_mid_run_is_named_as_a_modified_plan():
    # The gate downstream will say "0 of 3 commits carry their trailer", which
    # reads as an implementation failure. It is not: the plan moved underneath
    # commits that were correct when written. Only this comparison can say so.
    drift = plan_hash_mismatch("a1b2c3d4", "ffffffff")
    assert "a1b2c3d4" in drift
    assert "ffffffff" in drift
    assert "modified after implementation" in drift
    assert "hard-reset" in drift


@pytest.mark.parametrize(
    ("impl", "review"),
    [
        (None, "a1b2c3d4"),
        ("a1b2c3d4", None),
        ("a1b2c3d4", ""),
        ("not-a-hash", "a1b2c3d4"),
        ("a1b2c3d4", "A1B2C3D4"),
        (None, None),
        (12345678, "a1b2c3d4"),
    ],
)
def test_drift_is_not_claimed_when_either_hash_is_unusable(impl, review):
    # A stage that failed to report its hash tells us nothing about the other
    # one; inventing a mismatch there would send someone after a phantom.
    assert plan_hash_mismatch(impl, review) is None


def test_the_wrapper_returns_none_or_a_detail_verdict():
    assert plan_hash_gate("a1b2c3d4", "a1b2c3d4") is None
    assert plan_hash_gate(None, "a1b2c3d4") is None
    gate = plan_hash_gate("a1b2c3d4", "ffffffff")
    assert gate["detail"] == plan_hash_mismatch("a1b2c3d4", "ffffffff")
    # task.js only logs the drift (line 833); the stop is review_gate's.
    assert "blocked" not in gate
