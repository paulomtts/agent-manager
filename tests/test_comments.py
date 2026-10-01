"""Outcome comment bodies (board-comments design B2-B5): pure unit tests."""

import dataclasses

import pytest

from agent_manager import comments
from agent_manager.results import (
    CriticResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    SpecResult,
)
from agent_manager.runtime.walk import SubtaskSummary

RUN = "r1"
CARD = "card-476f1040"
STORY = "story-60189137"
MILESTONE = "ms-21f4cf06"
TOKEN = "tok-1"


def _review(*, findings=("a", "b"), blockers=()):
    return ReviewResult(
        findings=list(findings),
        unresolved_blockers=list(blockers),
        fix_summary="fixed both",
        porcelain="",
        commit_count=3,
        tagged_count=1,
        plan_hash="abcd1234",
    )


def _implement(*, blocked_reason=None):
    return ImplementResult(
        blocked=blocked_reason is not None,
        blocked_reason=blocked_reason,
        resumed=False,
        plan_hash="abcd1234",
        report="did it",
    )


def test_cap_is_1500():
    assert comments.CAP == 1500


def test_comment_is_frozen():
    comment = comments.Comment(card_id=CARD, key="k", body="b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        comment.body = "x"


def test_key_joins_run_card_and_event():
    assert comments.key(RUN, CARD, "done") == "r1/card-476f1040/done"
    assert comments.key(RUN, CARD, "escalated:tok-1") == "r1/card-476f1040/escalated:tok-1"


def test_agent_reason_reads_the_critic_reason_for_both_validate_phases():
    critic = CriticResult(blockers=True, reason="spec misses the error path", summary="blocked")
    assert comments.agent_reason({"validate_spec": critic}, "validate_spec") == "spec misses the error path"
    assert comments.agent_reason({"validate_plan": critic}, "validate_plan") == "spec misses the error path"


def test_agent_reason_reads_blocked_reason_for_implement():
    results = {"implement": _implement(blocked_reason="plan step 3 contradicts the spec")}
    assert comments.agent_reason(results, "implement") == "plan step 3 contradicts the spec"


def test_agent_reason_joins_unresolved_blockers_for_review():
    results = {"review": _review(blockers=("missing test", "wrong key"))}
    assert comments.agent_reason(results, "review") == "missing test; wrong key"


def test_agent_reason_reads_plain_mappings_too():
    assert comments.agent_reason({"implement": {"blocked_reason": "x"}}, "implement") == "x"
    assert comments.agent_reason({"review": {"unresolved_blockers": ["a", "b"]}}, "review") == "a; b"


@pytest.mark.parametrize(
    ("results", "phase"),
    [
        ({"verify": {"passed": False, "detail": "red"}}, "verify"),
        ({}, "implement"),
        ({"implement": _implement(blocked_reason=None)}, "implement"),
        ({"validate_spec": CriticResult(blockers=True, reason="", summary="s")}, "validate_spec"),
        ({"validate_plan": CriticResult(blockers=True, reason=None, summary="s")}, "validate_plan"),
        ({"review": _review(blockers=())}, "review"),
        ({"implement": "not a result"}, "implement"),
    ],
    ids=["other-phase", "missing-entry", "none", "empty-string", "none-reason", "empty-list", "garbage"],
)
def test_agent_reason_is_none_without_a_failure_field(results, phase):
    assert comments.agent_reason(results, phase) is None
