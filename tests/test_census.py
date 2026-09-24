"""Behaviour of `census.find_milestone`, ported from `census.test.mjs:70-104`.

Tier: pure-function unit tests, per design §14 lines 477-492. `census` is a
pure module, so these tests build `CardNode` values in memory and call the
function directly -- no `tmp_path`, no subprocess, no `brd`, no fake claude.
Real-board census tests belong to sibling cards, not here.
"""

import ast
import inspect

import pytest

from agent_manager import census
from agent_manager.census import MilestoneNotFoundError, find_milestone
from agent_manager.models import CardNode


def ID(n: int) -> str:
    return f"{n}0000000-0000-4000-8000-000000000000"[:36]


def node(n: int, title: str, **extra) -> CardNode:
    fields = {
        "id": ID(n),
        "title": title,
        "status": "todo",
        "blocked_by": [],
        "created_at": f"2026-01-0{n}T00:00:00Z",
        "children": [],
    }
    fields.update(extra)
    return CardNode(**fields)


TREE = node(
    1,
    "Milestone 12: CSV export",
    children=[
        node(
            2,
            "Story: CSV writer",
            children=[
                node(4, "feat: write rows"),
                node(5, "feat: quoting", blocked_by=[ID(4)]),
            ],
        ),
        node(
            3,
            "Story: Document it",
            blocked_by=[ID(2)],
            status="blocked",
            children=[node(6, "docs: usage", status="blocked")],
        ),
    ],
)


# --- census.test.mjs:70-104, one for one ---------------------------------


def test_matches_a_root_card_by_exact_id():
    assert find_milestone([TREE], ID(1)).title == "Milestone 12: CSV export"


def test_matches_by_case_insensitive_title_substring():
    assert find_milestone([TREE], "csv export").id == ID(1)


def test_fails_loudly_when_nothing_matches():
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([TREE], "nonexistent")


def test_fails_loudly_on_ambiguity_rather_than_guessing():
    other = node(9, "Milestone 13: CSV import")
    with pytest.raises(MilestoneNotFoundError, match="ambiguous"):
        find_milestone([TREE, other], "csv")


def test_exact_title_match_wins_outright_over_a_longer_title_containing_it():
    longer = node(9, "Milestone 12: CSV export, revisited")
    assert find_milestone([longer, TREE], "Milestone 12: CSV export").id == ID(1)
    # Case-insensitive too.
    assert find_milestone([longer, TREE], "milestone 12: csv export").id == ID(1)


# --- spec additions ------------------------------------------------------


def test_zero_match_message_lists_every_root_title():
    other = node(9, "Milestone 13: CSV import")
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone([TREE, other], "nonexistent")
    assert str(caught.value) == (
        'no milestone card matching "nonexistent" — root cards are: '
        "Milestone 12: CSV export, Milestone 13: CSV import"
    )


def test_zero_match_message_says_none_when_there_are_no_roots():
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone([], "anything")
    assert str(caught.value) == (
        'no milestone card matching "anything" — root cards are: (none)'
    )


def test_two_match_message_lists_both_matching_titles():
    other = node(9, "Milestone 13: CSV import")
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone([TREE, other], "csv")
    assert str(caught.value) == (
        'ambiguous milestone "csv" — matches: '
        "Milestone 12: CSV export, Milestone 13: CSV import"
    )


# --- review focus --------------------------------------------------------


def test_needle_is_stripped_before_matching():
    assert find_milestone([TREE], "  csv export  ").id == ID(1)
    assert find_milestone([TREE], f" {ID(1)}\n").id == ID(1)


def test_none_roots_is_treated_as_empty():
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone(None, "anything")
    assert str(caught.value).endswith("root cards are: (none)")


def test_exact_title_returns_first_of_duplicate_titles():
    first = node(7, "Milestone 3")
    second = node(8, "Milestone 3")
    assert find_milestone([first, second], "milestone 3").id == ID(7)


# --- the contract with cli.HANDLED and the purity rule -------------------


def test_milestone_not_found_error_is_a_value_error():
    # ValueError is already in cli.HANDLED, so a later CLI caller gets an
    # ok:false envelope with no change to cli.py.
    assert issubclass(MilestoneNotFoundError, ValueError)


def test_census_imports_neither_cli_nor_subprocess():
    tree = ast.parse(inspect.getsource(census))
    imported: set[str] = set()
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            imported.update(alias.name for alias in stmt.names)
        elif isinstance(stmt, ast.ImportFrom):
            imported.add(stmt.module or "")
            imported.update(
                f"{stmt.module}.{alias.name}" for alias in stmt.names
            )
    assert not any(
        name == "agent_manager.cli" or name.endswith(".cli") for name in imported
    )
    assert "subprocess" not in imported


# --- the numeric digit-run guard (census.mjs:76-92) ----------------------


def test_numeric_needle_does_not_resolve_via_a_longer_digit_run():
    # census.test.mjs:87-92. "2" must not silently resolve
    # "Milestone 12: CSV export" -- the "2" typed is not this card's "12".
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([TREE], 2)
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([TREE], "2")


def test_numeric_needle_resolves_a_card_whose_whole_digit_run_equals_it():
    # census.test.mjs:101-104.
    twelve = node(9, "Milestone 12")
    assert find_milestone([twelve], 12).id == ID(9)


def test_two_does_not_resolve_milestone_twelve():
    twelve = node(9, "Milestone 12")
    with pytest.raises(MilestoneNotFoundError) as caught:
        find_milestone([twelve], "2")
    assert str(caught.value) == (
        'no milestone card matching "2" — root cards are: Milestone 12'
    )


def test_numeric_needle_rejects_run_extending_right():
    twenty_three = node(9, "Milestone 23: import")
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([twenty_three], "2")


def test_numeric_guard_inspects_only_the_first_hit():
    # Port-exact: the JS checks only the first indexOf hit. Here that hit is
    # inside "12", so the later standalone "2" is never considered.
    card = node(9, "Milestone 12, part 2")
    with pytest.raises(MilestoneNotFoundError, match="no milestone card"):
        find_milestone([card], "2")


def test_numeric_needle_still_picks_the_single_standalone_run():
    twelve = node(8, "Milestone 12: CSV export")
    two = node(9, "Milestone 2: JSON export")
    assert find_milestone([twelve, two], "2").id == ID(9)
