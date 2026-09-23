import pytest

from agent_manager.dag import short_id, slugify

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
