"""Behaviour of the conftest `FakeBoard`, the in-memory stand-in for the `brd` subprocess.

Unit tier (test-tier design §3 V1): every test here runs `board.py` through the
`fake_board` fixture, which replaces `board.run_brd`, so no subprocess is ever
spawned. FakeBoard's envelopes are pinned against the real binary by the
`brd`-tier test in `tests/test_board.py`.
"""

import json
import subprocess

import pytest

from agent_manager import board, models


def test_the_fixture_installs_the_fake_as_board_run_brd(fake_board):
    assert board.run_brd is fake_board


def test_fake_board_answers_a_real_completed_process(fake_board):
    card = fake_board.add_card("Add FakeBoard")

    completed = fake_board(board.show_argv(card), None, None)

    assert isinstance(completed, subprocess.CompletedProcess)
    assert completed.args == ["brd", "show", card]
    assert completed.returncode == 0
    assert completed.stderr == ""
    envelope = json.loads(completed.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["id"] == card


def test_show_through_fake_board_returns_the_seeded_card(fake_board):
    parent = fake_board.add_card("Milestone 1")
    child = fake_board.add_card(
        "Add FakeBoard", parent_id=parent, description="the fake"
    )

    card = board.show(child)

    assert isinstance(card, models.Card)
    assert (card.id, card.title, card.status, card.parent_id) == (
        child,
        "Add FakeBoard",
        "todo",
        parent,
    )
    assert card.description == "the fake"
    assert card.blocked_by == []
    assert isinstance(card.created_at, str) and card.created_at


def test_show_answers_brds_card_detail_shape(fake_board):
    parent = fake_board.add_card("Milestone 1")
    first = fake_board.add_card("first", parent_id=parent)
    second = fake_board.add_card("second", parent_id=parent)
    fake_board.add_comment(parent, "older")
    fake_board.add_comment(parent, "newer", author="paulo")

    data = json.loads(fake_board(board.show_argv(parent), None, None).stdout)["data"]

    assert set(data) == {
        "id", "kind", "title", "description", "status", "parent_id",
        "created_at", "updated_at", "blocked_by", "children", "comments",
        "refs", "referenced_by",
    }
    assert data["kind"] == "card"
    assert data["children"] == [first, second]
    assert data["refs"] == [] and data["referenced_by"] == []
    assert [(c["body"], c["author"]) for c in data["comments"]] == [
        ("older", "am"),
        ("newer", "paulo"),
    ]
    assert all(
        set(c) == {"id", "entity_id", "author", "body", "created_at"}
        and c["entity_id"] == parent
        for c in data["comments"]
    )


def test_tree_through_fake_board_nests_children_in_creation_order(fake_board):
    milestone = fake_board.add_card("Milestone 1")
    story = fake_board.add_card("story", parent_id=milestone)
    first = fake_board.add_card("first", parent_id=story)
    second = fake_board.add_card("second", parent_id=story)

    node = board.tree(milestone)

    assert isinstance(node, models.CardNode)
    assert node.id == milestone
    assert [child.id for child in node.children] == [story]
    assert [grand.id for grand in node.children[0].children] == [first, second]
    assert node.children[0].children[1].children == []


def test_tree_nodes_carry_brds_node_keys_and_no_parent_id(fake_board):
    milestone = fake_board.add_card("Milestone 1")
    fake_board.add_card("story", parent_id=milestone)

    data = json.loads(fake_board(board.tree_argv(milestone), None, None).stdout)["data"]

    assert len(data) == 1
    keys = {"id", "title", "description", "status", "blocked_by", "created_at",
            "updated_at", "children"}
    assert set(data[0]) == keys
    assert set(data[0]["children"][0]) == keys


def test_roots_through_fake_board_lists_every_top_level_card(fake_board):
    first = fake_board.add_card("Milestone 1")
    story = fake_board.add_card("story", parent_id=first)
    second = fake_board.add_card("Milestone 2")

    nodes = board.roots()

    assert [node.id for node in nodes] == [first, second]
    assert [child.id for child in nodes[0].children] == [story]
    assert nodes[1].children == []


def test_roots_of_an_empty_fake_board_is_an_empty_list(fake_board):
    assert board.roots() == []


def test_children_are_ordered_by_created_at_not_insertion(fake_board):
    parent = fake_board.add_card("parent", created_at="2026-01-01T00:00:00.000000+00:00")
    later = fake_board.add_card(
        "later", parent_id=parent, created_at="2026-03-01T00:00:00.000000+00:00"
    )
    earlier = fake_board.add_card(
        "earlier", parent_id=parent, created_at="2026-02-01T00:00:00.000000+00:00"
    )

    assert [child.id for child in board.tree(parent).children] == [earlier, later]
    raw = json.loads(fake_board(board.show_argv(parent), None, None).stdout)["data"]
    assert raw["children"] == [earlier, later]


def test_blocked_status_is_derived_like_brd(fake_board):
    blocker = fake_board.add_card("blocker")
    blocked = fake_board.add_card("blocked", blocked_by=[blocker])
    under_blocked = fake_board.add_card("under blocked", parent_id=blocked)
    finished = fake_board.add_card("finished", status="done")
    free = fake_board.add_card("free", blocked_by=[finished])

    assert board.show(blocker).status == "todo"
    assert board.show(blocked).status == "blocked"
    assert board.show(blocked).blocked_by == [blocker]
    assert board.show(under_blocked).status == "blocked"
    assert board.show(free).status == "todo"
    assert board.tree(blocked).status == "blocked"


def test_show_of_an_unknown_card_raises_brds_card_not_found(fake_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.show("no-such-card")
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.message == "no card, issue, or document with id no-such-card"
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == ["brd", "show", "no-such-card"]


def test_tree_of_an_unknown_card_raises_brds_card_not_found(fake_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.tree("no-such-card")
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.message == "no card with id no-such-card"
    assert excinfo.value.exit_code == 1


@pytest.mark.parametrize(
    ("argv", "stdin"),
    [
        (["brd", "list"], None),
        (["brd", "show"], None),
        (["brd", "show", "c1", "--pretty"], None),
        (["brd", "show", "c1"], "unexpected body"),
        (["brd", "tree", "c1", "c2"], None),
        (["git", "show", "c1"], None),
    ],
    ids=["unknown-subcommand", "show-without-id", "extra-flag", "stdin-on-a-read",
         "two-tree-ids", "not-brd"],
)
def test_an_unrecognised_argv_fails_loudly(fake_board, argv, stdin):
    with pytest.raises(AssertionError, match="FakeBoard does not answer argv"):
        fake_board(argv, None, stdin)


def test_seeding_under_an_unknown_parent_fails_loudly(fake_board):
    with pytest.raises(AssertionError, match="unknown parent"):
        fake_board.add_card("orphan", parent_id="no-such-card")


def test_seeding_a_description_with_a_link_fails_loudly(fake_board):
    # Real brd indexes [[links]] into refs; FakeBoard always answers refs: [].
    with pytest.raises(AssertionError, match=r"\[\[link\]\]"):
        fake_board.add_card("linked", description="see [[other-card]]")

