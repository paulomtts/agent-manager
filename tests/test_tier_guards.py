"""Tier guards in `tests/conftest.py`: the unit-tier PATH shim, the per-test budget,
the e2e cap and justification check, and the git/brd binary skip.

The helpers, the stub text and the source scans are pure, so their tests carry
no tier marker and run in the unit tier. The hook-level tests start a nested
pytest in a subprocess (pytester) that execs the stub scripts, so they are
marked `git`: a subprocess cannot be unit tier, and the marker keeps them out of
the very shim they police. They touch no real git, brd or claude. This file sits
at the top of `tests/`, so the directory auto-mark leaves it alone.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from conftest import (
    BINARY_TIERS,
    E2E_CAP,
    GIT_BUDGET_S,
    STUB_EXIT_CODE,
    STUB_NAMES,
    UNIT_BUDGET_S,
    E2ETierCap,
    binary_skip_reason,
    e2e_tier_violations,
    has_justification,
    item_docstring,
    missing_binary,
    pytest_runtest_setup,
    stub_script,
    tier_budget_violation,
)

# Enabled here, not in tests/conftest.py: pytest rejects `pytest_plugins` in a
# conftest that is not loaded at startup, and a test module is always allowed.
pytest_plugins = ["pytester"]

REAL_CONFTEST = Path(__file__).resolve().parent / "conftest.py"

# The nested session has its own rootdir (pytester's tmp dir), so it does not
# read pyproject.toml; register the tier markers to keep its output clean.
NESTED_INI = """\
[pytest]
markers =
    git: real git in tmp_path
    brd: real brd
    e2e_fake: fake claude
    soak: concurrency stress
    e2e: real claude
"""


def run_nested(pytester: pytest.Pytester, source: str, *args: str) -> pytest.RunResult:
    """Run `source` as `test_nested.py` under a copy of the real tests/conftest.py.

    A subprocess, not in-process: tests/test_conftest_tiers.py has already put
    tests/conftest.py into `sys.modules["conftest"]`, so an in-process nested
    conftest import would hit ImportPathMismatchError. The child inherits this
    process's environment, including `PATH`. `args` go to the nested pytest.
    """
    pytester.makeconftest(REAL_CONFTEST.read_text())
    pytester.makeini(NESTED_INI)
    pytester.makepyfile(test_nested=source)
    return pytester.runpytest_subprocess(*args)


def test_budget_constants_match_the_v1_table():
    assert UNIT_BUDGET_S == 0.5
    assert GIT_BUDGET_S == 2.0


def test_budget_helper_unit_over_half_second_violates():
    assert tier_budget_violation(set(), 0.51) == "unit-tier budget exceeded: 0.510s > 0.5s"
    assert tier_budget_violation(set(), 0.5) is None
    assert tier_budget_violation(set(), 0.01) is None


def test_budget_helper_git_over_two_seconds_violates():
    assert tier_budget_violation({"git"}, 2.01) == "git-tier budget exceeded: 2.010s > 2s"
    assert tier_budget_violation({"git"}, 2.0) is None
    assert tier_budget_violation({"git"}, 1.9) is None


@pytest.mark.parametrize(
    "markers",
    [{"brd"}, {"e2e_fake"}, {"soak"}, {"e2e"}, {"git", "soak"}, {"git", "brd"}],
    ids=["brd", "e2e_fake", "soak", "e2e", "git+soak", "git+brd"],
)
def test_budget_helper_opt_in_tiers_have_no_budget(markers):
    assert tier_budget_violation(markers, 100.0) is None


def test_budget_helper_ignores_non_tier_markers():
    assert tier_budget_violation({"parametrize", "asyncio"}, 0.6) is not None
    assert tier_budget_violation(iter(["skipif", "git"]), 1.0) is None


def test_stub_script_forbids_the_named_binary():
    assert STUB_NAMES == ("brd", "git", "claude")
    assert STUB_EXIT_CODE == 99
    for name in STUB_NAMES:
        text = stub_script(name)
        assert text.startswith("#!/bin/sh\n")
        assert f"{name}: forbidden in the unit tier" in text
        assert f"exit {STUB_EXIT_CODE}" in text


@pytest.mark.git
def test_unmarked_test_calling_brd_fails_with_forbidden_message(pytester):
    # The card's deliberately broken test: an unmarked test that calls brd.
    # No capture_output, so the stub's stderr reaches pytest's fd capture and
    # shows up in the report instead of hiding inside CalledProcessError.
    result = run_nested(
        pytester,
        """
        import subprocess

        def test_calls_brd():
            subprocess.run(["brd", "--version"], check=True)
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*brd: forbidden in the unit tier*"])
    result.stdout.fnmatch_lines(["*non-zero exit status 99*"])


@pytest.mark.git
def test_unmarked_test_sees_stubs_for_git_and_claude(pytester):
    result = run_nested(
        pytester,
        """
        import shutil
        import subprocess
        from pathlib import Path

        import pytest

        @pytest.mark.parametrize("name", ["brd", "git", "claude"])
        def test_stub_shadows(name):
            found = shutil.which(name)
            assert found is not None
            assert Path(found).parent.name.startswith("unit-tier-stubs")
            done = subprocess.run([name, "--version"], capture_output=True, text=True)
            assert done.returncode == 99
            assert done.stderr == f"{name}: forbidden in the unit tier\\n"
            assert done.stdout == ""
        """,
    )
    result.assert_outcomes(passed=3)


@pytest.mark.git
def test_marked_test_gets_real_path(pytester, monkeypatch):
    # This outer test is `git`-marked, so its own PATH is the unshimmed one.
    monkeypatch.setenv("OUTER_PATH", os.environ["PATH"])
    result = run_nested(
        pytester,
        """
        import os

        import pytest

        def _path_is_inherited():
            return os.environ["PATH"] == os.environ["OUTER_PATH"]

        @pytest.mark.git
        def test_function_marked_git():
            assert _path_is_inherited()

        @pytest.mark.brd
        class TestClassMarkedBrd:
            def test_inside(self):
                assert _path_is_inherited()

        def test_unmarked_control():
            assert not _path_is_inherited()
            first = os.environ["PATH"].split(os.pathsep)[0]
            assert os.path.basename(first).startswith("unit-tier-stubs")
        """,
    )
    result.assert_outcomes(passed=3)


@pytest.mark.git
def test_slow_unmarked_test_fails_budget(pytester):
    result = run_nested(
        pytester,
        """
        import time

        def test_slow():
            time.sleep(0.55)
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*unit-tier budget exceeded: *s > 0.5s*"])


@pytest.mark.git
def test_failing_slow_test_keeps_original_failure(pytester):
    result = run_nested(
        pytester,
        """
        import time

        def test_slow_and_wrong():
            time.sleep(0.55)
            assert 1 == 2, "original failure"
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*original failure*"])
    result.stdout.no_fnmatch_line("*budget exceeded*")


@pytest.mark.git
def test_slow_fixture_setup_is_not_counted(pytester):
    result = run_nested(
        pytester,
        """
        import time

        import pytest

        @pytest.fixture
        def slow_setup():
            time.sleep(0.55)

        def test_fast_body(slow_setup):
            pass
        """,
    )
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*budget exceeded*")


@pytest.mark.git
def test_slow_test_that_skips_stays_skipped(pytester):
    result = run_nested(
        pytester,
        """
        import time

        import pytest

        def test_slow_then_skip():
            time.sleep(0.55)
            pytest.skip("skipped after the budget")
        """,
    )
    result.assert_outcomes(skipped=1)
    result.stdout.no_fnmatch_line("*budget exceeded*")


@pytest.mark.git
def test_hook_reads_the_items_own_tier_markers(pytester):
    # Over the unit budget but under git's, so only the item's markers decide:
    # `git` (function, class, or module-inherited) and opt-in tiers pass.
    result = run_nested(
        pytester,
        """
        import time

        import pytest

        @pytest.mark.git
        def test_git_function_over_unit_budget():
            time.sleep(0.55)

        @pytest.mark.soak
        class TestSoakClass:
            def test_inherits_no_budget(self):
                time.sleep(0.55)
        """,
    )
    result.assert_outcomes(passed=2)
    result.stdout.no_fnmatch_line("*budget exceeded*")


# ── the e2e cap and justification check ─────────────────────────────────────

JUSTIFIED = "Drives the real claude.\n\njustification: only the real binary shows this.\n"
UNJUSTIFIED_MESSAGE = "an e2e test's docstring needs a line starting `justification:`"


def _e2e(nodeid: str, doc: str | None = JUSTIFIED) -> tuple[str, list[str], str | None]:
    return (nodeid, ["e2e"], doc)


def test_e2e_cap_matches_the_v2_table():
    assert E2E_CAP == 5


def test_five_justified_e2e_items_and_any_non_e2e_items_pass():
    items = [_e2e(f"t.py::test_e2e_{i}") for i in range(5)]
    items += [(f"t.py::test_plain_{i}", ["git", "parametrize"], None) for i in range(50)]
    assert e2e_tier_violations(items) == []


def test_e2e_fake_items_never_count_toward_the_cap():
    items = [(f"t.py::test_fake_{i}", ["e2e_fake"], None) for i in range(9)]
    assert e2e_tier_violations(items) == []


def test_a_sixth_e2e_item_is_a_cap_violation_naming_every_nodeid():
    items = [_e2e(f"t.py::test_e2e_{i}") for i in range(6)]
    assert e2e_tier_violations(items) == [
        "the e2e tier is capped at 5 tests, but 6 carry the e2e marker: "
        + ", ".join(f"t.py::test_e2e_{i}" for i in range(6))
    ]


@pytest.mark.parametrize(
    "doc",
    [None, "", "Drives the real claude.\nNo reason given.\n"],
    ids=["no-docstring", "empty-docstring", "no-justification-line"],
)
def test_an_e2e_item_without_a_justification_line_is_a_violation(doc):
    assert e2e_tier_violations([_e2e("t.py::test_paid", doc)]) == [
        f"t.py::test_paid: {UNJUSTIFIED_MESSAGE}"
    ]


def test_an_indented_justification_line_passes():
    doc = "Drives the real claude.\n\n    justification: only the real binary shows this.\n    "
    assert has_justification(doc)
    assert e2e_tier_violations([_e2e("t.py::test_paid", doc)]) == []


def test_justification_mid_line_does_not_count():
    doc = "Needs a justification: but not at the start of the line.\n"
    assert not has_justification(doc)
    assert e2e_tier_violations([_e2e("t.py::test_paid", doc)]) == [
        f"t.py::test_paid: {UNJUSTIFIED_MESSAGE}"
    ]


def test_cap_and_justification_violations_are_reported_together():
    items = [_e2e(f"t.py::test_e2e_{i}") for i in range(5)] + [_e2e("t.py::test_bare", None)]
    violations = e2e_tier_violations(items)
    assert len(violations) == 2
    assert violations[0].startswith("the e2e tier is capped at 5 tests, but 6 carry the e2e marker: ")
    assert violations[1] == f"t.py::test_bare: {UNJUSTIFIED_MESSAGE}"


def test_marker_names_may_be_any_iterable():
    assert e2e_tier_violations([("t.py::test_paid", iter(["e2e"]), JUSTIFIED)]) == []
    assert e2e_tier_violations([("t.py::test_plain", iter(["skipif"]), None)]) == []


# The nested session reads NESTED_INI, not pyproject.toml, so it has no addopts;
# the cap tests pass the default selection explicitly to prove the check runs
# before deselection.
DEFAULT_SELECTION = "not brd and not e2e_fake and not soak and not e2e"


def test_default_selection_matches_pyproject_addopts(pytestconfig):
    assert DEFAULT_SELECTION in pytestconfig.getini("addopts")


class _Mark:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeItem:
    """The surface the new hooks read: `nodeid`, the marker chain, the test function."""

    def __init__(self, nodeid: str, chain: tuple[str, ...] | list[str] = (), doc: str | None = None) -> None:
        self.nodeid = nodeid
        self._chain = [_Mark(name) for name in chain]

        def function() -> None:
            pass

        function.__doc__ = doc
        self.function = function

    def iter_markers(self, name: str | None = None):
        return iter(m for m in self._chain if name is None or m.name == name)


def test_item_docstring_reads_the_underlying_function():
    assert item_docstring(_FakeItem("t.py::test_paid", doc=JUSTIFIED)) == JUSTIFIED
    assert item_docstring(_FakeItem("t.py::test_bare")) is None
    assert item_docstring(object()) is None


def test_cap_plugin_passes_five_justified_e2e_items():
    items = [_FakeItem(f"t.py::test_{i}", chain=["skipif", "e2e"], doc=JUSTIFIED) for i in range(5)]
    E2ETierCap().pytest_collection_modifyitems(items)


def test_cap_plugin_raises_one_usage_error_listing_every_violation():
    items = [_FakeItem(f"t.py::test_{i}", chain=["e2e"], doc=JUSTIFIED) for i in range(6)]
    items.append(_FakeItem("t.py::test_bare", chain=["e2e"]))
    with pytest.raises(pytest.UsageError) as excinfo:
        E2ETierCap().pytest_collection_modifyitems(items)
    lines = str(excinfo.value).splitlines()
    assert lines[0] == "e2e tier check failed:"
    assert lines[1].startswith(f"the e2e tier is capped at {E2E_CAP} tests, but 7 carry the e2e marker: ")
    assert lines[2] == f"t.py::test_bare: {UNJUSTIFIED_MESSAGE}"
    assert len(lines) == 3


@pytest.mark.git
def test_six_e2e_tests_fail_collection_under_the_default_selection(pytester):
    source = "import pytest\n" + "".join(
        f"\n@pytest.mark.e2e\ndef test_paid_{i}():\n    '''justification: only real claude shows this.'''\n"
        for i in range(6)
    )
    result = run_nested(pytester, source, "-m", DEFAULT_SELECTION)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*the e2e tier is capped at 5 tests, but 6 carry the e2e marker*"])


@pytest.mark.git
def test_parametrized_module_marked_e2e_counts_each_parametrization(pytester):
    result = run_nested(
        pytester,
        """
        import pytest

        pytestmark = pytest.mark.e2e

        @pytest.mark.parametrize("n", range(6))
        def test_paid(n):
            '''justification: only real claude shows this.'''
        """,
        "-m",
        DEFAULT_SELECTION,
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*the e2e tier is capped at 5 tests, but 6 carry the e2e marker*"])


@pytest.mark.git
def test_an_unjustified_e2e_test_fails_collection_under_the_default_selection(pytester):
    result = run_nested(
        pytester,
        """
        import pytest

        @pytest.mark.e2e
        def test_unjustified():
            '''Drives the real claude, with no reason given.'''

        @pytest.mark.e2e
        def test_justified():
            '''Drives the real claude.

            justification: only real claude shows this.
            '''

        def test_unmarked():
            pass
        """,
        "-m",
        DEFAULT_SELECTION,
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*test_nested.py::test_unjustified: {UNJUSTIFIED_MESSAGE}*"])
    result.stderr.no_fnmatch_line("*test_nested.py::test_justified:*")


@pytest.mark.git
def test_five_justified_e2e_tests_collect_and_are_deselected_by_default(pytester):
    source = "import pytest\n\ndef test_unmarked():\n    pass\n" + "".join(
        f"\n@pytest.mark.e2e\ndef test_paid_{i}():\n    '''justification: only real claude shows this.'''\n"
        for i in range(5)
    )
    result = run_nested(pytester, source, "-m", DEFAULT_SELECTION)
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, deselected=5)


# ── the git/brd binary skip ─────────────────────────────────────────────────


def _nothing_installed(name: str) -> str | None:
    return None


def _everything_installed(name: str) -> str | None:
    return f"/usr/bin/{name}"


def test_binary_tiers_are_git_then_brd():
    assert BINARY_TIERS == ("git", "brd")
    assert binary_skip_reason("brd") == "the brd CLI must be installed for the brd tier"


@pytest.mark.parametrize(
    "markers",
    [set(), {"parametrize", "skipif"}, {"soak"}, {"e2e_fake"}, {"e2e"}],
    ids=["none", "non-tier", "soak", "e2e_fake", "e2e"],
)
def test_items_without_git_or_brd_are_never_skipped(markers):
    assert missing_binary(markers, which=_nothing_installed) is None


@pytest.mark.parametrize("name", ["git", "brd"])
def test_a_marked_item_names_its_missing_binary(name):
    assert missing_binary({name, "parametrize"}, which=_nothing_installed) == name
    assert missing_binary({name}, which=_everything_installed) is None


def test_both_missing_names_git_first_and_only_the_missing_one_otherwise():
    assert missing_binary(["brd", "git"], which=_nothing_installed) == "git"
    only_brd_missing = lambda name: None if name == "brd" else f"/usr/bin/{name}"  # noqa: E731
    assert missing_binary(iter(["git", "brd"]), which=only_brd_missing) == "brd"


def test_missing_binary_defaults_to_shutil_which_at_call_time(monkeypatch):
    monkeypatch.setattr(shutil, "which", _nothing_installed)
    assert missing_binary({"git"}) == "git"
    monkeypatch.setattr(shutil, "which", _everything_installed)
    assert missing_binary({"git"}) is None


def test_the_hook_skips_a_brd_item_when_brd_is_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", _nothing_installed)
    with pytest.raises(pytest.skip.Exception, match="the brd CLI must be installed for the brd tier"):
        pytest_runtest_setup(_FakeItem("t.py::test_board", chain=["brd"]))


def test_the_hook_leaves_unmarked_and_satisfied_items_alone(monkeypatch):
    monkeypatch.setattr(shutil, "which", _nothing_installed)
    pytest_runtest_setup(_FakeItem("t.py::test_pure"))
    monkeypatch.setattr(shutil, "which", _everything_installed)
    pytest_runtest_setup(_FakeItem("t.py::test_board", chain=["brd", "git"]))


@pytest.mark.git
def test_missing_binaries_skip_their_tier_and_leave_unmarked_tests_alone(pytester, tmp_path, monkeypatch):
    # No `-m`: the nested session has no addopts, so every test is selected and
    # the unmarked one can show it is not skipped. PATH holds an empty directory,
    # so neither git nor brd is found; python is spawned by absolute path.
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    monkeypatch.setenv("PATH", str(bare))
    result = run_nested(
        pytester,
        """
        import pytest

        @pytest.mark.brd
        def test_needs_brd():
            pass

        @pytest.mark.git
        def test_needs_git():
            pass

        @pytest.mark.brd
        @pytest.mark.git
        def test_needs_both():
            pass

        def test_unmarked():
            pass
        """,
        "-rs",
    )
    result.assert_outcomes(passed=1, skipped=3)
    # pytest's `-rs` summary groups skips that share a (location, reason) pair
    # into one `SKIPPED [N] ...` line, rather than one line per skip (the hook's
    # `pytest.skip` call is always the same conftest.py line, so every skip of a
    # given reason collapses together). Sum the bracketed counts rather than
    # counting matching lines, which would always read back as 1 for a reason
    # that several items share. Deviation from the plan's literal line-count
    # assertion, verified against this pytest version's actual grouping.
    lines = result.stdout.lines

    def _skip_count(reason: str) -> int:
        total = 0
        for line in lines:
            if reason not in line:
                continue
            match = re.match(r"SKIPPED \[(\d+)\]", line)
            total += int(match.group(1)) if match else 1
        return total

    assert _skip_count("the brd CLI must be installed for the brd tier") == 1
    assert _skip_count("the git CLI must be installed for the git tier") == 2
