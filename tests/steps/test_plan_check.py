"""Behaviour of the plan-check step (design §4 `steps/`, spec card d3feb87e).

Placement follows design §14: `plan_check.py` is a Steps component, so its
behaviour is exercised against real temporary directories and files created in
`tmp_path` -- no network, and no faking of the filesystem except where a test
must force an outcome a real filesystem will not produce on demand (an
unreadable file), exactly as the ported `plan-check.test.mjs` uses its `fakeFs`.
"""

from agent_manager.steps import plan_check
from agent_manager.steps.plan_check import VALIDATED_MARKER, matches_card


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
