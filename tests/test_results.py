"""Resolving a phase's `result:` name to the model that validates result.json
(design §6 step 5).

Engine tier per design §14 lines 486-488: this is the table the engine's
validation step reads, exercised with a canned model rather than a harness.
"""

import pytest
from pydantic import BaseModel, ValidationError

from agent_manager import results
from agent_manager.steps import reducers
from agent_manager.errors import EngineError


class Canned(BaseModel):
    summary: str


def test_a_named_model_resolves_to_the_class():
    table = {"Canned": Canned}

    assert results.resolve_result_model("Canned", table, phase="explore") is Canned


def test_an_unknown_result_name_is_a_named_engine_error():
    with pytest.raises(EngineError) as caught:
        results.resolve_result_model("ExploreResult", {"Canned": Canned}, phase="explore")

    assert caught.value.phase == "explore"
    message = str(caught.value)
    assert "'ExploreResult'" in message
    assert "Canned" in message


def test_the_shipped_table_is_empty_and_says_why():
    # The five names in builtin/task.yaml now have models below, but putting
    # them in the table is a sibling card's decision, not this one's.
    # Validating nothing would be worse -- an unknown name fails loudly instead.
    assert results.RESULT_MODELS == {}


# --- The five R1 result models (design §2). Pure tier per design §14 lines
# --- 477-492: plain pydantic validation, no fake adapter and no canned result
# --- file, unlike the engine-tier resolve_result_model tests above.


def test_explore_result_accepts_a_full_payload():
    explore = results.ExploreResult(
        refused=False,
        reason=None,
        summary="read results.py and reducers.py; the gates read camelCase keys",
        verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
    )

    assert explore.refused is False
    assert explore.reason is None
    assert explore.summary.startswith("read results.py")
    assert explore.verification.full_suite == ["uv run pytest"]
    assert explore.verification.typecheck == ""
    assert explore.verification.lint == []


def test_explore_result_rejects_a_missing_summary():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
        )

    assert "summary" in str(caught.value)


def test_explore_result_does_not_coerce_one_into_refused():
    # A JSON `1` where the agent was asked for a boolean is the sloppiness
    # strict mode exists to catch, not something to read as True.
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=1,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
        )

    assert "refused" in str(caught.value)


def test_explore_result_rejects_none_for_the_non_nullable_summary():
    # Reading None as "None" would hand the gate a 4-character summary.
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary=None,
            verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
        )

    assert "summary" in str(caught.value)


def test_explore_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
            skill_invoked=True,
        )

    assert "skill_invoked" in str(caught.value)


def test_verification_rejects_a_missing_full_suite_by_its_nested_path():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"typecheck": "", "lint": []},
        )

    message = str(caught.value)
    assert "verification" in message
    assert "full_suite" in message


def test_verification_rejects_a_non_list_full_suite():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"full_suite": "uv run pytest", "typecheck": "", "lint": []},
        )

    message = str(caught.value)
    assert "verification" in message
    assert "full_suite" in message


def test_verification_rejects_a_non_string_inside_full_suite():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"full_suite": ["uv run pytest", 3], "typecheck": "", "lint": []},
        )

    assert "full_suite" in str(caught.value)


def test_critic_result_accepts_a_full_payload():
    critic = results.CriticResult(
        blockers=True,
        reason="the spec's alias choice contradicts the reducers",
        summary="reviewed the design against reducers.py",
    )

    assert critic.blockers is True
    assert critic.reason == "the spec's alias choice contradicts the reducers"
    assert critic.summary == "reviewed the design against reducers.py"


def test_critic_result_rejects_a_missing_reason():
    # `reason` is nullable but required: the agent says "no reason" explicitly.
    with pytest.raises(ValidationError) as caught:
        results.CriticResult(blockers=False, summary="nothing blocking")

    assert "reason" in str(caught.value)


def test_critic_result_does_not_coerce_a_string_into_blockers():
    with pytest.raises(ValidationError) as caught:
        results.CriticResult(blockers="true", reason=None, summary="nothing blocking")

    assert "blockers" in str(caught.value)


def test_critic_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.CriticResult(
            blockers=False, reason=None, summary="nothing blocking", verdict="ok"
        )

    assert "verdict" in str(caught.value)


def test_plan_result_accepts_a_full_payload():
    plan = results.PlanResult(
        path="docs/superpowers/plans/task-define-the-five-phase-c873fc52.md",
        self_reviewed=True,
        note=None,
    )

    assert plan.path.endswith("c873fc52.md")
    assert plan.self_reviewed is True
    assert plan.note is None


def test_plan_result_rejects_a_missing_path():
    with pytest.raises(ValidationError) as caught:
        results.PlanResult(self_reviewed=True, note=None)

    assert "path" in str(caught.value)


def test_plan_result_does_not_coerce_a_path_object_into_str():
    with pytest.raises(ValidationError) as caught:
        results.PlanResult(path=["a", "b"], self_reviewed=True, note=None)

    assert "path" in str(caught.value)


def test_plan_result_has_no_skill_invoked_field():
    # D6 inlines the methodology into the prompt: there is no skill to detect,
    # so a result file claiming one is an unknown key.
    with pytest.raises(ValidationError) as caught:
        results.PlanResult(
            path="docs/superpowers/plans/p.md",
            self_reviewed=True,
            note=None,
            skill_invoked=True,
        )

    assert "skill_invoked" in str(caught.value)
    assert "skill_invoked" not in results.PlanResult.model_fields


def test_implement_result_accepts_a_full_payload():
    implement = results.ImplementResult(
        blocked=False,
        blocked_reason=None,
        resumed=True,
        plan_hash="a1b2c3d4",
        report="tasks 1-3 done, suite green",
    )

    assert implement.blocked is False
    assert implement.blocked_reason is None
    assert implement.resumed is True
    assert implement.plan_hash == "a1b2c3d4"
    assert implement.report == "tasks 1-3 done, suite green"


def test_implement_result_rejects_a_missing_plan_hash():
    with pytest.raises(ValidationError) as caught:
        results.ImplementResult(
            blocked=False, blocked_reason=None, resumed=False, report="done"
        )

    assert "plan_hash" in str(caught.value)


def test_implement_result_does_not_coerce_a_number_into_plan_hash():
    with pytest.raises(ValidationError) as caught:
        results.ImplementResult(
            blocked=False,
            blocked_reason=None,
            resumed=False,
            plan_hash=12345678,
            report="done",
        )

    assert "plan_hash" in str(caught.value)


def test_implement_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.ImplementResult(
            blocked=False,
            blocked_reason=None,
            resumed=False,
            plan_hash="a1b2c3d4",
            report="done",
            commits=3,
        )

    assert "commits" in str(caught.value)


def test_implement_result_keeps_plan_hash_an_unconstrained_string():
    # No format constraint here: reducers.is_plan_hash owns that judgement, and
    # a short hash must reach it as data rather than dying as a ValidationError.
    assert (
        results.ImplementResult(
            blocked=False,
            blocked_reason=None,
            resumed=False,
            plan_hash="nope",
            report="done",
        ).plan_hash
        == "nope"
    )


def test_review_result_accepts_a_full_payload():
    review = results.ReviewResult(
        findings=["the alias only applies on dump"],
        unresolved_blockers=[],
        fix_summary="added the serialisation aliases",
        porcelain="",
        commit_count=3,
        tagged_count=3,
        plan_hash="a1b2c3d4",
    )

    assert review.findings == ["the alias only applies on dump"]
    assert review.unresolved_blockers == []
    assert review.fix_summary == "added the serialisation aliases"
    assert review.porcelain == ""
    assert review.commit_count == 3
    assert review.tagged_count == 3
    assert review.plan_hash == "a1b2c3d4"


def test_review_result_rejects_a_missing_tagged_count():
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=[],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count=3,
            plan_hash="a1b2c3d4",
        )

    assert "tagged_count" in str(caught.value)


def test_review_result_does_not_coerce_a_numeric_string_into_commit_count():
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=[],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count="3",
            tagged_count=3,
            plan_hash="a1b2c3d4",
        )

    assert "commit_count" in str(caught.value)


def test_review_result_does_not_read_true_as_the_commit_count_one():
    # bool is an int subclass in Python; a JSON `true` must not become 1 commit.
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=[],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count=True,
            tagged_count=3,
            plan_hash="a1b2c3d4",
        )

    assert "commit_count" in str(caught.value)


def test_review_result_rejects_a_non_string_inside_findings():
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=["ok", None],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count=3,
            tagged_count=3,
            plan_hash="a1b2c3d4",
        )

    assert "findings" in str(caught.value)


def test_review_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=[],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count=3,
            tagged_count=3,
            plan_hash="a1b2c3d4",
            commitCount=3,
        )

    assert "commitCount" in str(caught.value)


def test_review_result_dumps_the_two_counts_in_camel_case_only_under_by_alias():
    review = results.ReviewResult(
        findings=[],
        unresolved_blockers=[],
        fix_summary="nothing to fix",
        porcelain="",
        commit_count=3,
        tagged_count=3,
        plan_hash="a1b2c3d4",
    )

    aliased = review.model_dump(by_alias=True)
    assert aliased["commitCount"] == 3
    assert aliased["taggedCount"] == 3
    assert "commit_count" not in aliased
    # Only those two are renamed -- porcelain and plan_hash keep one spelling.
    assert aliased["porcelain"] == ""
    assert aliased["plan_hash"] == "a1b2c3d4"

    assert review.model_dump()["commit_count"] == 3


# --- The one place the snake_case/camelCase mismatch is reconciled (design §3).
# --- Still pure tier: the reducers are pure functions, called directly.

_CLEAN_REVIEW = {
    "findings": [],
    "unresolved_blockers": [],
    "fix_summary": "nothing to fix",
    "porcelain": "",
    "commit_count": 2,
    "tagged_count": 2,
    "plan_hash": "a1b2c3d4",
}


def test_a_dumped_clean_review_passes_the_real_review_gate():
    dumped = results.ReviewResult(**_CLEAN_REVIEW).model_dump(by_alias=True)

    assert reducers.review_gate(dumped, "m2/task-x", "master") is None


def test_a_dump_without_by_alias_is_judged_by_the_real_review_gate_too():
    # `dispatch.py` dumps without `by_alias=True`, so the snake_case spelling is
    # what really reaches the gate. The gate reads both spellings (reducers.
    # _either_field): the plain dump passes rather than falling to the warn.
    dumped = results.ReviewResult(**_CLEAN_REVIEW).model_dump()

    assert "commit_count" in dumped and "commitCount" not in dumped
    assert reducers.review_gate(dumped, "m2/task-x", "master") is None


def test_a_dumped_review_with_no_commits_blocks_on_implement():
    # The gate is reading the real commitCount, not falling through _field's None.
    dumped = results.ReviewResult(**{**_CLEAN_REVIEW, "commit_count": 0}).model_dump(
        by_alias=True
    )

    verdict = reducers.review_gate(dumped, "m2/task-x", "master")
    assert verdict["blocked"] == "implement"
    assert "no commits on top of master" in verdict["detail"]


_REAL_SUMMARY = (
    "results.py holds the result-name table; steps/reducers.py holds the ported "
    "gates and reads camelCase keys off the dumped result mapping"
)


def test_a_dumped_explore_result_passes_the_real_exploration_output_gate():
    assert len(_REAL_SUMMARY) > reducers.MIN_SUMMARY_LENGTH
    dumped = results.ExploreResult(
        refused=False,
        reason=None,
        summary=_REAL_SUMMARY,
        verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
    ).model_dump(by_alias=True)

    assert dumped["verification"]["fullSuite"] == ["uv run pytest"]
    assert reducers.exploration_output_gate(dumped, None) is None


def test_the_models_leave_an_empty_summary_for_the_gate_to_judge():
    # No min_length on the models on purpose: plausibility is the gate's job,
    # so an empty summary must validate and then be stopped by the gate.
    dumped = results.ExploreResult(
        refused=True,
        reason="refused to explore",
        summary="",
        verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
    ).model_dump(by_alias=True)

    verdict = reducers.exploration_output_gate(dumped, None)
    assert "implausibly short/placeholder" in verdict["detail"]


def test_the_embedded_json_schema_names_snake_case_only():
    # R2 embeds model_json_schema() in the agent's prompt, and validation
    # accepts snake_case only -- so the schema must name exactly that spelling.
    review_schema = results.ReviewResult.model_json_schema()
    assert "commit_count" in review_schema["properties"]
    assert "tagged_count" in review_schema["properties"]
    assert "commitCount" not in review_schema["properties"]
    assert "commitCount" not in review_schema["required"]
    assert set(review_schema["required"]) == {
        "findings",
        "unresolved_blockers",
        "fix_summary",
        "porcelain",
        "commit_count",
        "tagged_count",
        "plan_hash",
    }

    explore_schema = results.ExploreResult.model_json_schema()
    assert set(explore_schema["required"]) == {
        "refused",
        "reason",
        "summary",
        "verification",
    }
    verification_schema = explore_schema["$defs"]["Verification"]
    assert set(verification_schema["required"]) == {"full_suite", "typecheck", "lint"}

    assert set(results.CriticResult.model_json_schema()["required"]) == {
        "blockers",
        "reason",
        "summary",
    }
    assert set(results.PlanResult.model_json_schema()["required"]) == {
        "path",
        "self_reviewed",
        "note",
    }
    assert set(results.ImplementResult.model_json_schema()["required"]) == {
        "blocked",
        "blocked_reason",
        "resumed",
        "plan_hash",
        "report",
    }
