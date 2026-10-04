"""Behaviour of the pure `census` functions, ported from `census.test.mjs`.

`find_milestone` is ported from `census.test.mjs:70-104`, `order_siblings`
from `census.test.mjs:12-49` and `flatten_milestone` from
`census.test.mjs:106-131`.

Tier: pure-function unit tests, per design §14 lines 479-493. `census` is a
pure module, so these tests build `CardNode` values in memory and call the
functions directly -- no `tmp_path`, no subprocess, no `brd`, no fake claude.
The one real-board census test lives in `tests/test_board.py`, next to the
`temp_board` fixture.
"""

import ast
import inspect

import pytest

from agent_manager import census
from agent_manager.census import (
    Census,
    CensusOrderError,
    MilestoneNotFoundError,
    StoryPlan,
    SubtaskPlan,
    find_milestone,
    flatten_milestone,
    order_siblings,
)
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


def test_non_numeric_needle_skips_the_digit_run_guard():
    # Spec rule 4: the guard applies only when the needle is all digits.
    # "milestone 1" is not numeric, so its hit inside "12" still counts.
    assert find_milestone([TREE], "milestone 1").id == ID(1)


def test_digit_run_guard_widens_over_ascii_digits_only():
    # The JS guard's /[0-9]/ is ASCII-only; a non-ASCII digit such as the
    # Arabic-Indic two beside the hit does not widen the run.
    card = node(9, "Milestone ٢2")
    assert find_milestone([card], "2").id == ID(9)


# --- order_siblings: census.test.mjs:12-49, one for one ------------------


def titles(cards: list[CardNode]) -> list[str]:
    return [card.title for card in cards]


def test_chain_runs_in_dependency_order_not_creation_order():
    # b was created first but is blocked by a: a must come first.
    b = node(2, "b", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(1)])
    a = node(1, "a", created_at="2026-01-02T00:00:00Z")
    assert titles(order_siblings([b, a])) == ["a", "b"]


def test_independent_siblings_keep_creation_order():
    a = node(1, "a", created_at="2026-01-02T00:00:00Z")
    b = node(2, "b", created_at="2026-01-01T00:00:00Z")
    assert titles(order_siblings([a, b])) == ["b", "a"]


def test_three_card_chain_resolves_fully():
    c = node(3, "c", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(2)])
    b = node(2, "b", created_at="2026-01-02T00:00:00Z", blocked_by=[ID(1)])
    a = node(1, "a", created_at="2026-01-03T00:00:00Z")
    assert titles(order_siblings([c, b, a])) == ["a", "b", "c"]


def test_edge_outside_sibling_set_does_not_order_siblings():
    # Blocked by a card in another story: irrelevant to ordering HERE.
    a = node(1, "a", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(9)])
    b = node(2, "b", created_at="2026-01-02T00:00:00Z")
    assert titles(order_siblings([a, b])) == ["a", "b"]


def test_empty_and_none_give_empty_list():
    assert order_siblings([]) == []
    assert order_siblings(None) == []


def test_cycle_raises_could_not_be_ordered():
    # Defensive: a cycle cannot be persisted by brd, but truncating the list
    # would silently remove subtasks from a milestone.
    a = node(1, "a", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(2)])
    b = node(2, "b", created_at="2026-01-02T00:00:00Z", blocked_by=[ID(1)])
    with pytest.raises(CensusOrderError, match="could not be ordered") as caught:
        order_siblings([a, b])
    assert isinstance(caught.value, ValueError)


# --- order_siblings: spec additions and review focus ----------------------


def test_census_order_error_is_a_value_error():
    # ValueError is already in cli.HANDLED; no cli.py change is needed.
    assert issubclass(CensusOrderError, ValueError)


def test_returns_the_same_card_objects_and_leaves_the_input_alone():
    b = node(2, "b", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(1)])
    a = node(1, "a", created_at="2026-01-02T00:00:00Z")
    given = [b, a]
    ordered = order_siblings(given)
    assert ordered[0] is a
    assert ordered[1] is b
    assert given == [b, a]
    assert given[0] is b


def test_ready_queue_is_re_sorted_after_every_pop():
    # a (oldest) and c (newest) start ready; b is unlocked by a. b was created
    # before c, so once it is ready it must jump ahead of c.
    a = node(1, "a", created_at="2026-01-01T00:00:00Z")
    b = node(2, "b", created_at="2026-01-02T00:00:00Z", blocked_by=[ID(1)])
    c = node(3, "c", created_at="2026-01-04T00:00:00Z")
    assert titles(order_siblings([c, b, a])) == ["a", "b", "c"]


def test_missing_created_at_sorts_first_and_ties_break_on_id():
    dated = node(1, "dated", created_at="2026-01-05T00:00:00Z")
    undated = node(2, "undated", created_at=None)
    assert titles(order_siblings([dated, undated])) == ["undated", "dated"]

    same = "2026-01-03T00:00:00Z"
    later_id = node(7, "seven", created_at=same)
    earlier_id = node(4, "four", created_at=same)
    assert titles(order_siblings([later_id, earlier_id])) == ["four", "seven"]


def test_self_block_is_a_cycle():
    a = node(1, "a", blocked_by=[ID(1)])
    with pytest.raises(CensusOrderError) as caught:
        order_siblings([a])
    assert str(caught.value) == (
        "census: 1 card(s) could not be ordered — "
        f"cyclic blocked_by among siblings: {ID(1)}"
    )


def test_duplicate_blocker_id_still_orders():
    b = node(2, "b", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(1), ID(1)])
    a = node(1, "a", created_at="2026-01-02T00:00:00Z")
    assert titles(order_siblings([b, a])) == ["a", "b"]


def test_cycle_message_names_only_stuck_ids_in_input_order():
    free = node(3, "free", created_at="2026-01-01T00:00:00Z")
    b = node(2, "b", created_at="2026-01-02T00:00:00Z", blocked_by=[ID(1)])
    a = node(1, "a", created_at="2026-01-03T00:00:00Z", blocked_by=[ID(2)])
    with pytest.raises(CensusOrderError) as caught:
        order_siblings([b, free, a])
    assert str(caught.value) == (
        "census: 2 card(s) could not be ordered — "
        f"cyclic blocked_by among siblings: {ID(2)}, {ID(1)}"
    )


# --- flatten_milestone: census.test.mjs:106-131, one for one -------------


def test_flatten_produces_stories_with_ordered_subtasks():
    plan = flatten_milestone(TREE)
    assert plan.milestone_title == "Milestone 12: CSV export"
    assert [story.title for story in plan.stories] == [
        "Story: CSV writer",
        "Story: Document it",
    ]
    assert [subtask.title for subtask in plan.stories[0].subtasks] == [
        "feat: write rows",
        "feat: quoting",
    ]


def test_story_blocked_by_carries_ids_through():
    assert flatten_milestone(TREE).stories[1].blocked_by == [ID(2)]


def test_derived_blocked_reads_as_todo():
    # brd projects blocked at read time; readiness is the orchestrator's DAG
    # walk, so blocked must not survive into the census as a distinct state.
    plan = flatten_milestone(TREE)
    assert plan.stories[1].status == "todo"
    assert plan.stories[1].subtasks[0].status == "todo"


def test_in_progress_and_done_pass_through():
    tree = node(
        1,
        "M",
        children=[
            node(2, "S", status="done", children=[node(3, "T", status="in_progress")])
        ],
    )
    story = flatten_milestone(tree).stories[0]
    assert story.status == "done"
    assert story.subtasks[0].status == "in_progress"


# --- flatten_milestone: spec additions and review focus -------------------


def test_flatten_builds_exactly_the_frozen_census_shape():
    assert flatten_milestone(TREE) == Census(
        milestone_title="Milestone 12: CSV export",
        stories=[
            StoryPlan(
                id=ID(2),
                title="Story: CSV writer",
                status="todo",
                blocked_by=[],
                subtasks=[
                    SubtaskPlan(id=ID(4), title="feat: write rows", status="todo"),
                    SubtaskPlan(id=ID(5), title="feat: quoting", status="todo"),
                ],
            ),
            StoryPlan(
                id=ID(3),
                title="Story: Document it",
                status="todo",
                blocked_by=[ID(2)],
                subtasks=[
                    SubtaskPlan(id=ID(6), title="docs: usage", status="todo"),
                ],
            ),
        ],
    )


def test_census_values_are_frozen():
    plan = flatten_milestone(TREE)
    with pytest.raises(AttributeError):
        plan.milestone_title = "other"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        plan.stories[0].status = "done"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        plan.stories[0].subtasks[0].title = "other"  # type: ignore[misc]


def test_stories_are_ordered_by_blocked_by_not_by_tree_order():
    later_story = node(
        2, "Story: second", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(3)]
    )
    first_story = node(3, "Story: first", created_at="2026-01-02T00:00:00Z")
    tree = node(1, "M", children=[later_story, first_story])
    assert [story.id for story in flatten_milestone(tree).stories] == [ID(3), ID(2)]


def test_story_blocked_by_keeps_ids_outside_the_milestone():
    foreign = "ffffffff-0000-4000-8000-000000000000"
    tree = node(1, "M", children=[node(2, "S", blocked_by=[foreign])])
    assert flatten_milestone(tree).stories[0].blocked_by == [foreign]


def test_story_blocked_by_is_a_copy_not_the_nodes_list():
    story_node = TREE.children[1]
    plan = flatten_milestone(TREE)
    assert plan.stories[1].blocked_by == story_node.blocked_by
    assert plan.stories[1].blocked_by is not story_node.blocked_by


def test_other_statuses_pass_through_unchanged():
    tree = node(1, "M", children=[node(2, "S", status="review")])
    assert flatten_milestone(tree).stories[0].status == "review"


def test_milestone_with_no_stories_is_an_empty_census():
    plan = flatten_milestone(node(1, "Milestone 0: empty"))
    assert plan == Census(milestone_title="Milestone 0: empty", stories=[])


def test_subtask_cycle_propagates_from_flatten_milestone():
    tree = node(
        1,
        "M",
        children=[
            node(
                2,
                "S",
                children=[
                    node(4, "a", blocked_by=[ID(5)]),
                    node(5, "b", blocked_by=[ID(4)]),
                ],
            )
        ],
    )
    with pytest.raises(CensusOrderError, match="could not be ordered") as caught:
        flatten_milestone(tree)
    assert isinstance(caught.value, ValueError)


def test_story_cycle_propagates_from_flatten_milestone():
    tree = node(
        1,
        "M",
        children=[
            node(2, "S1", blocked_by=[ID(3)]),
            node(3, "S2", blocked_by=[ID(2)]),
        ],
    )
    with pytest.raises(CensusOrderError, match="could not be ordered"):
        flatten_milestone(tree)


# --- terminal card statuses -------------------------------------------------


@pytest.mark.parametrize("status", ["canceled", "archived", "CANCELED", "Archived"])
def test_flatten_drops_out_of_play_stories_subtasks_and_edges_to_them(status):
    tree = node(
        1,
        "M",
        children=[
            node(2, "dead story", status=status, children=[node(6, "x")]),
            node(
                3,
                "live",
                blocked_by=[ID(2)],
                children=[
                    node(4, "dead sub", status=status),
                    node(5, "live sub", blocked_by=[ID(4)]),
                ],
            ),
        ],
    )
    stories = census.flatten_milestone(tree).stories
    assert [s.id for s in stories] == [ID(3)]
    assert stories[0].blocked_by == []
    assert [t.id for t in stories[0].subtasks] == [ID(5)]


def test_flatten_keeps_a_merged_card_with_its_status():
    tree = node(1, "M", children=[node(2, "s", status="merged", children=[node(3, "t", status="merged")])])
    story = census.flatten_milestone(tree).stories[0]
    assert story.status == "merged"
    assert story.subtasks[0].status == "merged"


def test_status_sets_live_in_one_place():
    assert census.FINISHED_STATUSES == frozenset({"done", "merged"})
    assert census.OUT_OF_PLAY_STATUSES == frozenset({"canceled", "archived"})
    for status in ("done", "MERGED"):
        assert census.is_finished(status) and not census.is_out_of_play(status)
    for status in ("canceled", "ARCHIVED"):
        assert census.is_out_of_play(status) and not census.is_finished(status)
    for status in ("todo", "in_progress", "blocked", None, ""):
        assert not census.is_finished(status) and not census.is_out_of_play(status)
