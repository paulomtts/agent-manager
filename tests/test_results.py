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
