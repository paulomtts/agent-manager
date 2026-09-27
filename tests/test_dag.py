import dataclasses

import pytest

from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.dag import (
    DependencyCycleError,
    RootPlan,
    StackRootError,
    assert_no_blocker_cycles,
    base_branch_name,
    compute_integrate_levels,
    compute_levels,
    is_story_closed,
    is_subtask_done,
    ref_matches_card,
    remaining_subtasks,
    short_id,
    slugify,
    stack_bases,
    story_root,
    story_tip,
    subtask_branch,
    task_branch,
    task_stem,
    topological_levels,
)

CARD = {"id": "a32af745-15ef-45cd-b52c-64c19ae82c17", "title": "40.1 feat: write rows"}


def test_short_id_is_first_eight_hex_chars_dashes_ignored():
    assert short_id(CARD["id"]) == "a32af745"


def test_short_id_lowercases_an_uppercase_uuid():
    assert short_id("A32AF745-15EF-45CD-B52C-64C19AE82C17") == "a32af745"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        None,
        "nope",
        12345678,
        1234567890123456,
        "a32af745",
        "deadbeefdeadbeef",
        {},
    ],
)
def test_short_id_rejects_anything_that_is_not_a_card_id(bad):
    with pytest.raises(ValueError, match="not a card id"):
        short_id(bad)


def test_slugify_lowercases_and_collapses_punctuation_to_single_dashes():
    assert slugify("40.1 feat: write rows") == "40-1-feat-write-rows"


def test_slugify_trims_surrounding_whitespace_and_inner_runs():
    assert slugify("  Hello,   World!  ") == "hello-world"


def test_slugify_truncates_at_a_word_boundary_without_a_trailing_dash():
    assert slugify("abcdefghij klmnopqrst uvwxyz", 24) == "abcdefghij-klmnopqrst"


def test_slugify_keeps_a_slug_exactly_max_long_whole():
    assert slugify("abcdefghij klmnopqrst", 21) == "abcdefghij-klmnopqrst"


def test_slugify_defaults_to_a_max_of_twenty_four():
    assert slugify("abcdefghij klmnopqrst uvwxyz") == "abcdefghij-klmnopqrst"


def test_slugify_hard_cuts_a_single_long_word_with_no_dash_to_fall_back_to():
    assert slugify("a" * 30, 24) == "a" * 24


def test_slugify_strips_non_ascii_to_dashes_leaving_a_branch_safe_slug():
    result = slugify("Café ☕ résumé")
    assert result == "caf-r-sum"
    assert result.isascii()


def test_slugify_treats_none_as_the_empty_string_not_the_word_none():
    assert slugify(None) == ""


def test_slugify_never_exceeds_max_and_never_ends_in_a_dash():
    for title in ["40.1 feat: write rows", "a" * 30, "one two three four five six"]:
        for limit in [4, 8, 24]:
            result = slugify(title, limit)
            assert len(result) <= limit
            assert not result.endswith("-")


def test_task_stem_is_slug_then_short_id_so_the_id_is_a_stable_suffix():
    assert task_stem(CARD) == "40-1-feat-write-rows-a32af745"


def test_task_stem_of_an_unslugifiable_title_is_the_bare_short_id():
    assert task_stem({"id": CARD["id"], "title": "???"}) == "a32af745"


def test_task_branch_prefixes_the_stem():
    assert task_branch("m12", CARD) == "m12/task-40-1-feat-write-rows-a32af745"


def test_ref_matches_card_keys_on_the_short_id_so_a_rename_still_matches():
    branch = task_branch("m12", CARD)
    renamed = {**CARD, "title": "completely different title"}
    assert ref_matches_card(branch, renamed["id"]) is True


def test_ref_matches_card_does_not_confuse_two_different_cards():
    assert ref_matches_card("m12/task-quoting-03a6dc10", CARD["id"]) is False


class _CardObject:
    """Stand-in for the future models.Card, which another subtask owns."""

    def __init__(self, id: str, title: str) -> None:
        self.id = id
        self.title = title


def test_task_stem_accepts_an_attribute_bearing_card_as_well_as_a_mapping():
    obj = _CardObject(CARD["id"], CARD["title"])
    assert task_stem(obj) == task_stem(CARD) == "40-1-feat-write-rows-a32af745"
    assert task_branch("m12", obj) == "m12/task-40-1-feat-write-rows-a32af745"


def test_task_stem_of_a_card_with_no_title_field_at_all_is_the_bare_short_id():
    assert task_stem({"id": CARD["id"]}) == "a32af745"
    assert task_stem(_CardObject(CARD["id"], None)) == "a32af745"


def test_task_stem_propagates_the_short_id_error_for_a_missing_or_bad_id():
    with pytest.raises(ValueError, match="not a card id"):
        task_stem({"title": "no id here"})
    with pytest.raises(ValueError, match="not a card id"):
        task_branch("m12", {"id": "nope", "title": "bad id"})


def test_ref_matches_card_treats_a_none_ref_as_the_empty_string():
    assert ref_matches_card(None, CARD["id"]) is False


def test_ref_matches_card_still_validates_the_card_id_for_an_empty_ref():
    with pytest.raises(ValueError, match="not a card id"):
        ref_matches_card("", "nope")


# ── doneness, levels and cycles ─────────────────────────────────────────────


def _sub(id: str, status: str = "todo") -> SubtaskPlan:
    return SubtaskPlan(id=id, title=f"subtask {id}", status=status)


def _story(
    id: str,
    blocked_by: list[str] | None = None,
    status: str = "todo",
    subtasks: list[SubtaskPlan] | None = None,
) -> StoryPlan:
    """A story; by default open with one todo subtask, so it is pending."""
    return StoryPlan(
        id=id,
        title=f"story {id}",
        status=status,
        blocked_by=list(blocked_by or []),
        subtasks=[_sub(f"{id}1")] if subtasks is None else subtasks,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [("done", True), ("DONE", True), ("Done", True), ("todo", False), ("in_progress", False)],
)
def test_doneness_is_case_insensitive_for_subtasks_and_stories(status, expected):
    assert is_subtask_done(_sub("s", status)) is expected
    assert is_story_closed(_story("a", status=status)) is expected


def test_doneness_treats_a_missing_status_as_not_done():
    assert is_subtask_done(SubtaskPlan(id="s", title="s", status=None)) is False
    story = StoryPlan(id="a", title="a", status=None, blocked_by=[], subtasks=[])
    assert is_story_closed(story) is False


def test_a_closed_story_has_no_remaining_subtasks_even_if_they_are_todo():
    story = _story("a", status="done", subtasks=[_sub("a1"), _sub("a2", "in_progress")])
    assert remaining_subtasks(story) == []


def test_an_open_story_keeps_its_not_done_subtasks_in_order():
    a1, a2, a3, a4 = _sub("a1", "done"), _sub("a2"), _sub("a3", "DONE"), _sub("a4", "in_progress")
    story = _story("a", subtasks=[a1, a2, a3, a4])
    assert remaining_subtasks(story) == [a2, a4]


def _ids(levels: list[list[StoryPlan]]) -> list[list[str]]:
    return [[story.id for story in level] for level in levels]


def _diamond() -> list[StoryPlan]:
    return [
        _story("a"),
        _story("b", ["a"]),
        _story("c", ["a"]),
        _story("d", ["b", "c"]),
    ]


def test_linear_chain_is_one_story_per_level():
    stories = [_story("a"), _story("b", ["a"]), _story("c", ["b"])]
    assert _ids(compute_levels(stories)) == [["a"], ["b"], ["c"]]


def test_diamond_groups_the_two_middle_stories_in_input_order():
    assert _ids(compute_levels(_diamond())) == [["a"], ["b", "c"], ["d"]]


def test_diamond_keeps_census_order_not_id_order_in_a_shared_level():
    stories = [_story("a"), _story("c", ["a"]), _story("b", ["a"]), _story("d", ["b", "c"])]
    assert _ids(compute_levels(stories)) == [["a"], ["c", "b"], ["d"]]


def test_independent_roots_share_level_zero_in_input_order():
    assert _ids(compute_levels([_story("y"), _story("x")])) == [["y", "x"]]


def test_a_done_story_is_dropped_from_dispatch_but_kept_for_integrate():
    stories = [_story("a", status="done"), _story("b", ["a"])]
    assert _ids(compute_levels(stories)) == [["b"]]
    assert _ids(compute_integrate_levels(stories)) == [["a"], ["b"]]


def test_an_open_story_whose_subtasks_are_all_done_is_dropped_from_dispatch():
    stories = [
        _story("a", subtasks=[_sub("a1", "done"), _sub("a2", "DONE")]),
        _story("b", ["a"]),
    ]
    assert _ids(compute_levels(stories)) == [["b"]]
    assert _ids(compute_integrate_levels(stories)) == [["a"], ["b"]]


def test_an_open_story_with_no_subtasks_is_dropped_from_dispatch():
    stories = [_story("a", subtasks=[]), _story("b")]
    assert _ids(compute_levels(stories)) == [["b"]]


def test_blocked_by_a_finished_story_and_an_external_id_lands_in_level_zero():
    stories = [_story("a", status="done"), _story("b", ["a", "outside"])]
    assert _ids(compute_levels(stories)) == [["b"]]


def test_an_external_blocker_is_ignored_by_the_level_engine():
    stories = [_story("a", ["not-in-this-milestone"]), _story("b", ["a"])]
    assert _ids(topological_levels(stories)) == [["a"], ["b"]]


def test_levels_return_the_same_objects_and_leave_the_input_list_alone():
    stories = _diamond()
    before = list(stories)
    levels = topological_levels(stories)
    assert stories == before
    assert [id(story) for level in levels for story in level] == [id(s) for s in stories]


def test_empty_input_has_no_levels():
    assert topological_levels([]) == []
    assert compute_levels([]) == []
    assert compute_integrate_levels([]) == []


def test_a_two_story_cycle_stops_the_level_engine_naming_both():
    stories = [_story("a", ["b"]), _story("b", ["a"])]
    with pytest.raises(DependencyCycleError, match="dependency cycle among stories #a, #b"):
        topological_levels(stories)


def test_the_level_engine_lists_a_cycle_in_input_order_not_id_order():
    stories = [_story("b", ["a"]), _story("a", ["b"])]
    with pytest.raises(DependencyCycleError, match="dependency cycle among stories #b, #a"):
        topological_levels(stories)


def test_the_level_engine_names_only_the_unplaced_stories_of_a_cycle():
    stories = [_story("root"), _story("a", ["root", "b"]), _story("b", ["a"])]
    with pytest.raises(DependencyCycleError) as caught:
        compute_integrate_levels(stories)
    message = str(caught.value)
    assert "#a, #b" in message
    assert "#root" not in message


def test_a_self_blocking_story_stops_the_level_engine():
    with pytest.raises(DependencyCycleError, match="#a"):
        topological_levels([_story("a", ["a"])])


def test_a_dependency_cycle_error_is_a_value_error():
    assert issubclass(DependencyCycleError, ValueError)


def test_a_two_story_cycle_is_reported_as_a_trail_from_the_first_story_walked():
    stories = [_story("a", ["b"]), _story("b", ["a"])]
    with pytest.raises(DependencyCycleError) as caught:
        assert_no_blocker_cycles(stories)
    assert "dependency cycle among stories #a -> #b -> #a" in str(caught.value)


def test_the_cycle_trail_follows_input_order_for_where_it_starts():
    stories = [_story("b", ["a"]), _story("a", ["b"])]
    with pytest.raises(DependencyCycleError, match="#b -> #a -> #b"):
        assert_no_blocker_cycles(stories)


def test_the_cycle_trail_omits_a_non_cyclic_story_that_led_into_it():
    stories = [_story("c", ["a"]), _story("a", ["b"]), _story("b", ["a"])]
    with pytest.raises(DependencyCycleError) as caught:
        assert_no_blocker_cycles(stories)
    message = str(caught.value)
    assert "dependency cycle among stories #a -> #b -> #a" in message
    assert "#c" not in message


def test_a_self_blocking_story_is_a_one_story_cycle():
    with pytest.raises(DependencyCycleError, match="#a -> #a"):
        assert_no_blocker_cycles([_story("a", ["a"])])


def test_a_cycle_between_finished_stories_is_still_caught():
    stories = [_story("a", ["b"], status="done"), _story("b", ["a"], status="done")]
    with pytest.raises(DependencyCycleError, match="#a -> #b -> #a"):
        assert_no_blocker_cycles(stories)


def test_an_external_blocker_does_not_trip_the_cycle_check():
    stories = [_story("a", ["not-in-this-milestone"]), _story("b", ["a"])]
    assert assert_no_blocker_cycles(stories) is None


def test_an_acyclic_milestone_passes_the_cycle_check():
    assert assert_no_blocker_cycles(_diamond()) is None
    assert assert_no_blocker_cycles([]) is None


# ── stack geometry ──────────────────────────────────────────────────────────

PREFIX = "m3"
BASE = "main"


def _gsub(title: str, hex8: str, status: str = "todo") -> SubtaskPlan:
    """A subtask with a real card id, so ``task_branch`` accepts it.

    Its short id is ``hex8`` and its branch is ``m3/task-<title>-<hex8>``.
    """
    return SubtaskPlan(id=f"{hex8}-0000-4000-8000-000000000000", title=title, status=status)


def _by_id(*stories: StoryPlan) -> dict[str, StoryPlan]:
    return {story.id: story for story in stories}


def _story_id(n: int) -> str:
    """A UUID-shaped STORY id whose short id is ``n`` in eight hex digits.

    ``base_branch_name`` goes through ``short_id``, which refuses the letter
    ids ``_story`` uses elsewhere, so a story that roots on a merged base needs
    a real-shaped id of its own. Distinct from ``_gsub``'s subtask ids.
    """
    return f"{n:08x}-0000-4000-8000-000000000000"


def test_base_branch_name_is_the_prefix_then_base_then_the_short_id():
    c = _story(_story_id(0xC), subtasks=[])
    assert base_branch_name(PREFIX, c) == "m3/base-0000000c"
    assert base_branch_name(PREFIX, c) == f"{PREFIX}/base-{short_id(c.id)}"


def test_base_branch_name_refuses_a_story_whose_id_is_not_a_card_id():
    with pytest.raises(ValueError, match="not a card id"):
        base_branch_name(PREFIX, _story("c", subtasks=[]))


def test_a_root_plan_is_frozen_and_compares_by_value():
    root = RootPlan(kind="base", branch="main", blockers=())
    assert root == RootPlan("base", "main", ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        root.branch = "other"  # type: ignore[misc]


def test_subtask_branch_is_task_branch_so_names_have_one_source():
    sub = _gsub("a1", "aaaa0001")
    assert subtask_branch(PREFIX, sub) == task_branch(PREFIX, sub) == "m3/task-a1-aaaa0001"


def test_subtask_branch_passes_task_branchs_bad_id_error_through():
    with pytest.raises(ValueError, match="not a card id"):
        subtask_branch(PREFIX, SubtaskPlan(id="nope", title="bad", status="todo"))


def test_a_story_with_no_blockers_roots_on_the_base_branch():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    assert story_root(a, _by_id(a), PREFIX, BASE) == RootPlan("base", "main", ())


def test_a_story_blocked_only_outside_the_milestone_roots_on_the_base_branch():
    a = _story("a", ["not-in-this-milestone"], subtasks=[_gsub("a1", "aaaa0001")])
    assert story_root(a, _by_id(a), PREFIX, BASE) == RootPlan("base", "main", ())


def test_a_missing_blocked_by_is_read_as_no_blockers():
    a = StoryPlan(
        id="a", title="a", status="todo", blocked_by=None, subtasks=[_gsub("a1", "aaaa0001")]
    )
    assert story_root(a, _by_id(a), PREFIX, BASE) == RootPlan("base", "main", ())


def test_a_story_tip_is_the_branch_of_its_last_subtask():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001"), _gsub("a2", "aaaa0002")])
    assert story_tip(a, _by_id(a), PREFIX, BASE) == "m3/task-a2-aaaa0002"


def test_one_in_milestone_blocker_roots_on_that_blockers_last_subtask():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001"), _gsub("a2", "aaaa0002")])
    b = _story("b", ["a"], subtasks=[_gsub("b1", "bbbb0001")])
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == RootPlan(
        "tip", "m3/task-a2-aaaa0002", ("a",)
    )


def test_external_blockers_beside_one_in_milestone_blocker_are_ignored():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", ["outside-1", "a", "outside-2"], subtasks=[_gsub("b1", "bbbb0001")])
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == RootPlan(
        "tip", "m3/task-a1-aaaa0001", ("a",)
    )


def test_a_done_blocker_still_yields_its_tip_because_done_is_not_landed():
    a = _story(
        "a",
        status="done",
        subtasks=[_gsub("a1", "aaaa0001", "done"), _gsub("a2", "aaaa0002", "done")],
    )
    b = _story("b", ["a"], subtasks=[_gsub("b1", "bbbb0001")])
    assert story_tip(a, _by_id(a, b), PREFIX, BASE) == "m3/task-a2-aaaa0002"
    assert story_root(b, _by_id(a, b), PREFIX, BASE).branch == "m3/task-a2-aaaa0002"


def test_a_subtask_less_blocker_falls_through_to_its_own_root():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001"), _gsub("a2", "aaaa0002")])
    b = _story("b", ["a"], subtasks=[])
    c = _story("c", ["b"], subtasks=[_gsub("c1", "cccc0001")])
    stories = _by_id(a, b, c)
    assert story_tip(b, stories, PREFIX, BASE) == "m3/task-a2-aaaa0002"
    assert story_root(c, stories, PREFIX, BASE) == RootPlan(
        "tip", "m3/task-a2-aaaa0002", ("b",)
    )


def test_a_subtask_less_story_with_no_blockers_has_the_base_as_its_tip():
    d = _story("d", subtasks=[])
    assert story_tip(d, _by_id(d), PREFIX, BASE) == "main"


def test_the_seen_guard_stops_a_cycle_between_subtask_less_stories():
    a = _story("a", ["b"], subtasks=[])
    b = _story("b", ["a"], subtasks=[])
    with pytest.raises(
        DependencyCycleError,
        match="dependency cycle reached story #a while computing its stack root",
    ):
        story_root(a, _by_id(a, b), PREFIX, BASE)


def test_a_pre_populated_seen_containing_the_story_raises():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    with pytest.raises(DependencyCycleError, match="#a"):
        story_root(a, _by_id(a), PREFIX, BASE, seen={"a"})


def test_repeated_top_level_calls_each_get_a_fresh_seen():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", ["a"], subtasks=[])
    c = _story("c", ["b"], subtasks=[_gsub("c1", "cccc0001")])
    stories = _by_id(a, b, c)
    for _ in range(2):
        assert story_root(c, stories, PREFIX, BASE).branch == "m3/task-a1-aaaa0001"
        assert story_tip(b, stories, PREFIX, BASE) == "m3/task-a1-aaaa0001"


def test_two_in_milestone_blockers_root_on_a_merged_base_in_blocked_by_order():
    """No longer refused: the story roots on its own merged base, and the
    blockers keep the order ``blocked_by`` lists them, not id order."""
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story(_story_id(0xC), ["b", "outside", "a"], subtasks=[_gsub("c1", "cccc0001")])
    root = story_root(c, _by_id(a, b, c), PREFIX, BASE)
    assert root == RootPlan("merged", "m3/base-0000000c", ("b", "a"))


def test_two_blockers_give_a_merged_root():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story(_story_id(0xC), ["a", "outside", "b"], subtasks=[_gsub("c1", "cccc0001")])
    assert story_root(c, _by_id(a, b, c), PREFIX, BASE) == RootPlan(
        kind="merged", branch=f"{PREFIX}/base-{short_id(c.id)}", blockers=("a", "b")
    )


def test_a_duplicated_blocker_in_a_merged_root_is_listed_once():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story(_story_id(0xC), ["a", "b", "a"], subtasks=[_gsub("c1", "cccc0001")])
    assert story_root(c, _by_id(a, b, c), PREFIX, BASE).blockers == ("a", "b")


def test_three_blockers_one_done_all_count_toward_the_merged_root():
    """Done is not landed, so a done blocker still has to be merged in."""
    a = _story("a", status="done", subtasks=[_gsub("a1", "aaaa0001", "done")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    d = _story("d", subtasks=[_gsub("d1", "dddd0001")])
    c = _story(_story_id(0xC), ["a", "b", "d"], subtasks=[_gsub("c1", "cccc0001")])
    assert story_root(c, _by_id(a, b, d, c), PREFIX, BASE) == RootPlan(
        "merged", "m3/base-0000000c", ("a", "b", "d")
    )


def test_a_stack_root_error_is_a_value_error():
    assert issubclass(StackRootError, ValueError)


def test_a_blocker_listed_twice_counts_once():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", ["a", "a"], subtasks=[_gsub("b1", "bbbb0001")])
    assert story_root(b, _by_id(a, b), PREFIX, BASE) == RootPlan(
        "tip", "m3/task-a1-aaaa0001", ("a",)
    )


def test_stack_bases_anchor_on_the_full_list_even_past_a_done_first_subtask():
    a1 = _gsub("a1", "aaaa0001", "done")
    a2 = _gsub("a2", "aaaa0002")
    a3 = _gsub("a3", "aaaa0003")
    a = _story("a", subtasks=[a1, a2, a3])
    bases = stack_bases(a, _by_id(a), PREFIX, BASE)
    assert bases == {
        a1.id: "main",
        a2.id: "m3/task-a1-aaaa0001",
        a3.id: "m3/task-a2-aaaa0002",
    }
    assert list(bases) == [a1.id, a2.id, a3.id]


def test_stack_bases_of_a_closed_story_still_maps_every_subtask():
    a1 = _gsub("a1", "aaaa0001", "done")
    a2 = _gsub("a2", "aaaa0002", "done")
    a = _story("a", status="done", subtasks=[a1, a2])
    assert stack_bases(a, _by_id(a), PREFIX, BASE) == {
        a1.id: "main",
        a2.id: "m3/task-a1-aaaa0001",
    }


def test_stack_bases_of_a_story_with_no_subtasks_is_empty():
    a = _story("a", subtasks=[])
    assert stack_bases(a, _by_id(a), PREFIX, BASE) == {}


def test_a_subtask_less_story_on_two_blockers_has_no_bases_and_its_merged_base_as_tip():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c = _story(_story_id(0xC), ["a", "b"], subtasks=[])
    stories = _by_id(a, b, c)
    assert stack_bases(c, stories, PREFIX, BASE) == {}
    assert story_root(c, stories, PREFIX, BASE).kind == "merged"
    assert story_tip(c, stories, PREFIX, BASE) == "m3/base-0000000c"


def test_stack_bases_of_a_two_blocker_story_root_its_first_subtask_on_the_merged_base():
    a = _story("a", subtasks=[_gsub("a1", "aaaa0001")])
    b = _story("b", subtasks=[_gsub("b1", "bbbb0001")])
    c1, c2 = _gsub("c1", "cccc0001"), _gsub("c2", "cccc0002")
    c = _story(_story_id(0xC), ["a", "b"], subtasks=[c1, c2])
    assert stack_bases(c, _by_id(a, b, c), PREFIX, BASE) == {
        c1.id: "m3/base-0000000c",
        c2.id: "m3/task-c1-cccc0001",
    }


def test_stack_bases_surfaces_a_malformed_subtask_id():
    a = _story(
        "a",
        subtasks=[SubtaskPlan(id="nope", title="bad", status="todo"), _gsub("a2", "aaaa0002")],
    )
    with pytest.raises(ValueError, match="not a card id"):
        stack_bases(a, _by_id(a), PREFIX, BASE)


def test_the_milestone_two_board_stacks_each_story_on_the_previous_ones_tip():
    s1a, s1b = _gsub("s1a", "11110001"), _gsub("s1b", "11110002")
    s2a, s2b, s2c = _gsub("s2a", "22220001"), _gsub("s2b", "22220002"), _gsub("s2c", "22220003")
    s3a, s3b = _gsub("s3a", "33330001"), _gsub("s3b", "33330002")
    s1 = _story("s1", subtasks=[s1a, s1b])
    s2 = _story("s2", ["s1"], subtasks=[s2a, s2b, s2c])
    s3 = _story("s3", ["s2"], subtasks=[s3a, s3b])
    stories = _by_id(s1, s2, s3)
    assert_no_blocker_cycles([s1, s2, s3])

    assert stack_bases(s1, stories, PREFIX, BASE) == {
        s1a.id: "main",
        s1b.id: "m3/task-s1a-11110001",
    }
    assert stack_bases(s2, stories, PREFIX, BASE) == {
        s2a.id: "m3/task-s1b-11110002",
        s2b.id: "m3/task-s2a-22220001",
        s2c.id: "m3/task-s2b-22220002",
    }
    assert stack_bases(s3, stories, PREFIX, BASE) == {
        s3a.id: "m3/task-s2c-22220003",
        s3b.id: "m3/task-s3a-33330001",
    }
