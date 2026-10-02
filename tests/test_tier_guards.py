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
from pathlib import Path

import pytest

from conftest import (
    E2E_CAP,
    GIT_BUDGET_S,
    STUB_EXIT_CODE,
    STUB_NAMES,
    UNIT_BUDGET_S,
    e2e_tier_violations,
    has_justification,
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


def run_nested(pytester: pytest.Pytester, source: str) -> pytest.RunResult:
    """Run `source` as `test_nested.py` under a copy of the real tests/conftest.py.

    A subprocess, not in-process: tests/test_conftest_tiers.py has already put
    tests/conftest.py into `sys.modules["conftest"]`, so an in-process nested
    conftest import would hit ImportPathMismatchError. The child inherits this
    process's environment, including `PATH`.
    """
    pytester.makeconftest(REAL_CONFTEST.read_text())
    pytester.makeini(NESTED_INI)
    pytester.makepyfile(test_nested=source)
    return pytester.runpytest_subprocess()


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
