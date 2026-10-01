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


def _escalated(*, reason, detail="review blockers: 1 unresolved", phase="review", token=TOKEN):
    return comments.compose_escalated(
        run_id=RUN,
        card_id=CARD,
        token=token,
        failed_phase=phase,
        detail=detail,
        reason=reason,
    )


def test_escalated_with_a_reason_golden_body():
    comment = _escalated(reason="tests do not cover the empty list")
    assert comment.card_id == CARD
    assert comment.key == "r1/card-476f1040/escalated:tok-1"
    assert comment.body == "\n".join(
        [
            "am · escalated · run r1",
            "phase: review",
            "detail: review blockers: 1 unresolved",
            'reason: "tests do not cover the empty list"',
            "next: `am resume r1`",
            "why: `am logs r1 card-476f1040 --phase review`",
            "am-key: r1/card-476f1040/escalated:tok-1",
        ]
    )


def test_escalated_without_a_reason_has_no_quoted_text():
    comment = _escalated(reason=None)
    assert comment.body == "\n".join(
        [
            "am · escalated · run r1",
            "phase: review",
            "detail: review blockers: 1 unresolved",
            "next: `am resume r1`",
            "why: `am logs r1 card-476f1040 --phase review`",
            "am-key: r1/card-476f1040/escalated:tok-1",
        ]
    )
    assert '"' not in comment.body


def test_escalated_without_a_detail_omits_the_detail_line():
    comment = _escalated(reason=None, detail=None)
    assert "detail:" not in comment.body
    assert comment.body.split("\n")[1] == "phase: review"


def test_escalated_keys_are_lease_token_scoped():
    first = _escalated(reason=None, token="tok-1")
    second = _escalated(reason=None, token="tok-2")
    assert first.key == "r1/card-476f1040/escalated:tok-1"
    assert second.key == "r1/card-476f1040/escalated:tok-2"
    assert first.key != second.key
    assert second.body.split("\n")[-1] == "am-key: r1/card-476f1040/escalated:tok-2"


def test_a_10000_char_reason_is_cut_first_and_the_key_line_survives():
    comment = _escalated(reason="x" * 10_000, detail="implement blocked", phase="implement")
    lines = comment.body.split("\n")
    assert len(comment.body) <= comments.CAP
    assert lines[0] == "am · escalated · run r1"
    assert lines[1] == "phase: implement"
    assert lines[2] == "detail: implement blocked"
    assert lines[3].startswith('reason: "xxxx')
    assert lines[3].endswith(
        '" … (truncated; see `am logs r1 card-476f1040 --phase implement`)'
    )
    assert lines[3].count("x") > 1000
    assert lines[4] == "next: `am resume r1`"
    assert lines[5] == "why: `am logs r1 card-476f1040 --phase implement`"
    assert lines[-1] == "am-key: r1/card-476f1040/escalated:tok-1"


def test_agent_card_refs_are_broken_so_they_create_no_backlink():
    comment = _escalated(reason="see [[4f31e025-aaaa]] for context")
    assert 'reason: "see [ [4f31e025-aaaa]] for context"' in comment.body
    assert "[[" not in comment.body


def test_runs_of_three_brackets_are_fully_broken():
    comment = _escalated(reason="[[[x]]]")
    assert 'reason: "[ [ [x]]]"' in comment.body
    assert "[[" not in comment.body


def test_an_over_cap_reason_of_only_brackets_stays_escaped_after_the_cut():
    comment = _escalated(reason="[[" * 5000)
    assert len(comment.body) <= comments.CAP
    assert "[[" not in comment.body
    assert comment.body.split("\n")[-1] == "am-key: r1/card-476f1040/escalated:tok-1"


def test_an_over_cap_detail_with_no_reason_is_cut_and_keeps_the_commands():
    comment = _escalated(reason=None, detail="d" * 5000, phase="implement")
    lines = comment.body.split("\n")
    assert len(comment.body) <= comments.CAP
    assert lines[0] == "am · escalated · run r1"
    assert lines[-4] == "… (truncated; see `am logs r1 card-476f1040 --phase implement`)"
    assert lines[-3] == "next: `am resume r1`"
    assert lines[-2] == "why: `am logs r1 card-476f1040 --phase implement`"
    assert lines[-1] == "am-key: r1/card-476f1040/escalated:tok-1"
