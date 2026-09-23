"""Behaviour of the plan-check step (design §4 `steps/`, spec card d3feb87e).

Placement follows design §14: `plan_check.py` is a Steps component, so its
behaviour is exercised against real temporary directories and files created in
`tmp_path` -- no network, and no faking of the filesystem except where a test
must force an outcome a real filesystem will not produce on demand (an
unreadable file), exactly as the ported `plan-check.test.mjs` uses its `fakeFs`.
"""

from pathlib import Path

import pytest

from agent_manager.steps import plan_check
from agent_manager.steps.plan_check import VALIDATED_MARKER, matches_card, pick_plan


def test_the_marker_is_the_exact_literal_validate_writes():
    assert VALIDATED_MARKER == "<!-- task-pipeline: validated -->"


def test_a_plan_matches_when_the_short_id_is_its_final_segment():
    assert matches_card("task-write-rows-a32af745.md", "a32af745") is True
    assert matches_card("task-a32af745.md", "a32af745") is True


def test_one_cards_plan_never_answers_for_another():
    # The hazard the old non-digit boundary could not express: hex ids may be
    # preceded by hex characters.
    assert matches_card("task-deadbeefa32af745.md", "a32af745") is False
    assert matches_card("task-rows-a32af746.md", "a32af745") is False
    assert matches_card("task-rows-a32af745-old.md", "a32af745") is False


def test_only_md_files_match():
    assert matches_card("task-rows-a32af745.txt", "a32af745") is False
    assert matches_card("task-rows-a32af745", "a32af745") is False


def test_a_missing_or_non_string_filename_matches_nothing():
    assert matches_card(None, "a32af745") is False
    assert plan_check.matches_card("", "a32af745") is False


def test_the_newest_matching_plan_wins():
    # A re-planned subtask leaves the old file behind; the stale one must not
    # decide whether Spec/Plan/Validate re-run. "Newest" is lexicographic on
    # the date-prefixed filename -- no `stat` call is made.
    assert (
        pick_plan(
            [
                "2026-01-task-rows-a32af745.md",
                "2026-08-task-rows-a32af745.md",
                "task-other-deadbeef.md",
            ],
            "a32af745",
        )
        == "2026-08-task-rows-a32af745.md"
    )


def test_pick_plan_is_none_when_nothing_matches():
    assert pick_plan(["task-other-deadbeef.md"], "a32af745") is None
    assert pick_plan([], "a32af745") is None
    assert pick_plan(None, "a32af745") is None


CARD_UUID = "a32af745-0322-4a49-9dd9-44630af9632d"


def test_an_already_short_lowercase_id_passes_straight_through():
    assert plan_check._card_short_id("a32af745") == "a32af745"


def test_a_full_uuid_resolves_through_dag_short_id():
    assert plan_check._card_short_id(CARD_UUID) == "a32af745"
    assert plan_check._card_short_id(CARD_UUID.replace("-", "")) == "a32af745"
    # dag.short_id accepts either case in a full id and lowercases it, so an
    # uppercase FULL uuid is normalised rather than rejected (spec §3).
    assert plan_check._card_short_id(CARD_UUID.upper()) == "a32af745"


def test_a_card_mapping_or_object_resolves_via_its_id_field():
    class Card:
        id = CARD_UUID

    assert plan_check._card_short_id({"id": CARD_UUID}) == "a32af745"
    assert plan_check._card_short_id(Card()) == "a32af745"


def test_an_uppercase_or_malformed_card_id_raises_rather_than_matching_nothing():
    # Accepting it would turn a caller's typo into "no plan found" -- a gate
    # failing open in the direction that halts a run for a reason that is not
    # true.
    for bad in ["A32AF745", "42", "zzzzzzzz", "", "not-a-uuid", None, 42]:
        with pytest.raises(ValueError):
            plan_check._card_short_id(bad)
    with pytest.raises(ValueError):
        plan_check._card_short_id({"title": "no id here"})


def test_the_plans_dir_defaults_under_the_repo_and_an_explicit_one_wins():
    assert plan_check._plans_dir(None, "/abs/repo") == "/abs/repo/.claude/plans"
    assert plan_check._plans_dir("/p", "/abs/repo") == "/p"
    assert plan_check._plans_dir(Path("/p"), None) == "/p"


def test_neither_plans_dir_nor_repo_dir_is_a_caller_bug_not_a_first_run():
    with pytest.raises(ValueError, match="plans_dir"):
        plan_check._plans_dir(None, None)
