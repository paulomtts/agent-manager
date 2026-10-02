"""Tier guards in `tests/conftest.py`: the unit-tier PATH shim and the per-test budget.

The budget helper and the stub text are pure, so their tests carry no tier
marker and run in the unit tier. The hook-level tests start a nested pytest in
a subprocess (pytester) that execs the stub scripts, so they are marked `git`:
a subprocess cannot be unit tier, and the marker keeps them out of the very
shim they police. They touch no real git, brd or claude. This file sits at the
top of `tests/`, so the directory auto-mark leaves it alone.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import (
    GIT_BUDGET_S,
    STUB_EXIT_CODE,
    STUB_NAMES,
    UNIT_BUDGET_S,
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
