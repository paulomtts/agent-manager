import pytest

from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.dag import (
    DependencyCycleError,
    compute_integrate_levels,
    compute_levels,
    is_story_closed,
    is_subtask_done,
    ref_matches_card,
    remaining_subtasks,
    short_id,
    slugify,
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
