"""Unit tier: the directory-to-tier decision in `tests/conftest.py`.

Both helpers are pure (no subprocess, no collection), so these tests carry
no tier marker and run in the default suite. This file sits at the top of
`tests/`, so the auto-mark hook leaves it unmarked.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from conftest import TESTS_DIR, TIER_MARKERS, default_tier_marker, relative_to_tests


def test_tier_markers_are_the_five_registered_tiers():
    assert TIER_MARKERS == frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})


def test_e2e_dir_item_gets_e2e_fake():
    assert default_tier_marker(PurePosixPath("e2e/test_x.py"), set()) == "e2e_fake"


def test_e2e_dir_item_already_marked_e2e_is_left_alone():
    assert default_tier_marker(PurePosixPath("e2e/test_real_harness.py"), {"e2e"}) is None


def test_steps_dir_item_gets_git():
    assert default_tier_marker(PurePosixPath("steps/test_worktree.py"), set()) == "git"


@pytest.mark.parametrize("tier", ["brd", "soak"])
def test_steps_dir_item_with_other_tier_marker_is_left_alone(tier):
    assert default_tier_marker(PurePosixPath("steps/test_rollup.py"), {tier}) is None


def test_non_tier_markers_do_not_count_as_marked():
    assert default_tier_marker(PurePosixPath("steps/test_verify.py"), {"parametrize", "skipif"}) == "git"


@pytest.mark.parametrize(
    "rel_path",
    ["test_cli.py", "nested/e2e_like/test_x.py", "nested/e2e/test_x.py", "runtime/steps/test_x.py"],
)
def test_other_directories_are_not_marked(rel_path):
    assert default_tier_marker(PurePosixPath(rel_path), set()) is None


def test_nested_dir_under_e2e_still_gets_e2e_fake():
    assert default_tier_marker(PurePosixPath("e2e/sub/test_x.py"), set()) == "e2e_fake"


@pytest.mark.parametrize(
    ("rel_path", "existing"),
    [
        ("e2e/test_real_harness.py", {"skipif", "e2e"}),
        ("steps/test_rollup.py", {"parametrize", "brd"}),
        ("e2e/test_x.py", {"asyncio", "e2e_fake"}),
    ],
)
def test_tier_marker_mixed_with_non_tier_markers_still_counts(rel_path, existing):
    assert default_tier_marker(PurePosixPath(rel_path), existing) is None


def test_existing_markers_may_be_any_iterable():
    assert default_tier_marker(PurePosixPath("e2e/test_x.py"), iter(["e2e"])) is None
    assert default_tier_marker(PurePosixPath("e2e/test_x.py"), ["skipif"]) == "e2e_fake"


def test_relative_to_tests_inside_tests_dir():
    rel = relative_to_tests(TESTS_DIR / "e2e" / "test_x.py")
    assert rel is not None
    assert rel.parts == ("e2e", "test_x.py")


def test_relative_to_tests_returns_none_outside_tests_dir():
    # A sibling of tests/ whose path still contains "e2e": no substring match.
    assert relative_to_tests(TESTS_DIR.parent / "e2e" / "test_x.py") is None
    assert relative_to_tests(Path("/") / "elsewhere" / "steps" / "test_x.py") is None


def test_relative_to_tests_ignores_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = relative_to_tests(TESTS_DIR / "steps" / "test_worktree.py")
    assert rel is not None
    assert rel.parts == ("steps", "test_worktree.py")


def test_relative_to_tests_accepts_an_explicit_tests_dir(tmp_path):
    rel = relative_to_tests(str(tmp_path / "e2e" / "test_x.py"), tests_dir=tmp_path)
    assert rel is not None
    assert rel.parts == ("e2e", "test_x.py")
