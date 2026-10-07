"""Unit tier: the directory-to-tier decision in `tests/conftest.py`.

Both helpers are pure (no subprocess, no collection), so these tests carry
no tier marker and run in the default suite. This file sits at the top of
`tests/`, so the auto-mark hook leaves it unmarked.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from conftest import (
    TESTS_DIR,
    TIER_MARKERS,
    default_tier_marker,
    pytest_collection_modifyitems,
    relative_to_tests,
    stubs_isolation_probe,
)


def test_tier_markers_match_the_markers_registered_in_pyproject(pytestconfig):
    registered = {line.split(":", 1)[0].strip() for line in pytestconfig.getini("markers")}
    assert TIER_MARKERS <= registered
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


def test_default_tier_marker_exempts_fake_claude_module():
    assert default_tier_marker(PurePosixPath("e2e/test_fake_claude.py"), set()) is None


@pytest.mark.parametrize(
    ("rel_path", "tier"),
    [
        ("e2e/test_fake_claude_other.py", "e2e_fake"),
        ("e2e/sub/test_fake_claude.py", "e2e_fake"),
        ("steps/test_fake_claude.py", "git"),
    ],
)
def test_default_tier_marker_exemption_is_file_scoped(rel_path, tier):
    assert default_tier_marker(PurePosixPath(rel_path), set()) == tier


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


class _Mark:
    def __init__(self, name):
        self.name = name


class _FakeItem:
    """Just the surface the hook touches: `path`, the marker chain, `add_marker`.

    `own_markers` stays empty while `chain` stands in for markers inherited from a
    class or a module `pytestmark`, so a hook reading only `own_markers` is caught.
    """

    def __init__(self, path, chain=()):
        self.path = path
        self.own_markers = []
        self._chain = [_Mark(name) for name in chain]
        self.added = []

    def iter_markers(self, name=None):
        return iter(m for m in self._chain if name is None or m.name == name)

    def add_marker(self, marker):
        self.added.append(marker)


def test_hook_marks_an_unmarked_e2e_item_e2e_fake():
    item = _FakeItem(TESTS_DIR / "e2e" / "test_x.py")
    pytest_collection_modifyitems(None, [item])
    assert item.added == ["e2e_fake"]


def test_hook_marks_an_unmarked_steps_item_git():
    item = _FakeItem(TESTS_DIR / "steps" / "test_worktree.py")
    pytest_collection_modifyitems(None, [item])
    assert item.added == ["git"]


def test_hook_reads_the_inherited_marker_chain_not_only_own_markers():
    item = _FakeItem(TESTS_DIR / "e2e" / "test_real_harness.py", chain=["skipif", "e2e"])
    pytest_collection_modifyitems(None, [item])
    assert item.added == []


def test_hook_leaves_top_level_and_outside_items_alone():
    top = _FakeItem(TESTS_DIR / "test_cli.py")
    outside = _FakeItem(TESTS_DIR.parent / "e2e" / "test_x.py")
    pytest_collection_modifyitems(None, [top, outside])
    assert top.added == []
    assert outside.added == []


def test_hook_decides_each_item_in_a_shared_module_independently():
    path = TESTS_DIR / "steps" / "test_rollup.py"
    plain = _FakeItem(path)
    brd = _FakeItem(path, chain=["brd"])
    pytest_collection_modifyitems(None, [plain, brd])
    assert plain.added == ["git"]
    assert brd.added == []


def test_hook_leaves_unmarked_fake_claude_items_unmarked():
    """The real hook, not only the helper: the module's pure tests stay unit tier."""
    item = _FakeItem(TESTS_DIR / "e2e" / "test_fake_claude.py")
    pytest_collection_modifyitems(None, [item])
    assert item.added == []


@pytest.mark.parametrize("tier", ["e2e_fake", "e2e", "soak"])
def test_the_isolation_probe_is_real_in_the_process_tiers(tier):
    """A5 test harness rule: those tiers run the real probe."""
    assert stubs_isolation_probe({tier}, PurePosixPath("e2e/test_x.py")) is False


def test_the_isolation_probe_is_real_in_the_launcher_tests():
    """`tests/harness/test_launcher.py` manages the cache and tests the real runner."""
    assert stubs_isolation_probe(set(), PurePosixPath("harness/test_launcher.py")) is False


@pytest.mark.parametrize(
    ("markers", "rel_path"),
    [
        (set(), PurePosixPath("test_cli.py")),
        ({"git"}, PurePosixPath("test_cli.py")),
        ({"brd"}, PurePosixPath("test_board.py")),
        (set(), PurePosixPath("harness/test_claude.py")),
        (set(), None),
    ],
)
def test_the_isolation_probe_is_stubbed_everywhere_else(markers, rel_path):
    assert stubs_isolation_probe(markers, rel_path) is True


def test_the_stubbed_probe_says_every_mode_is_available_without_a_process():
    """The autouse fixture is live for this unit test: `auto` lands on bwrap."""
    from agent_manager.harness import launcher

    assert launcher.default_probe_runner(["/nonexistent/probe"]) == 0
    assert launcher.resolve_isolation("auto") == launcher.Isolation("bwrap", None)
