"""Resolving a phase's `result:` name to the model that validates result.json
(design §6 step 5).

Engine tier per design §14 lines 486-488: this is the table the engine's
validation step reads, exercised with a canned model rather than a harness.
"""

import pytest
from pydantic import BaseModel, ValidationError

from agent_manager import results
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
    # The five names in builtin/task.yaml have no field schema anywhere in the
    # design spec; inventing one is not this card's decision. Validating
    # nothing would be worse -- an unknown name fails loudly instead.
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
