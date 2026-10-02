"""Tier guards in `tests/conftest.py`: the unit-tier PATH shim and the per-test budget.

The budget helper and the stub text are pure, so their tests carry no tier
marker and run in the unit tier. The hook-level tests start a nested pytest in
a subprocess (pytester) that execs the stub scripts, so they are marked `git`:
a subprocess cannot be unit tier, and the marker keeps them out of the very
shim they police. They touch no real git, brd or claude. This file sits at the
top of `tests/`, so the directory auto-mark leaves it alone.
"""

from __future__ import annotations

import pytest

from conftest import GIT_BUDGET_S, UNIT_BUDGET_S, tier_budget_violation


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
