"""Resolving a phase's `result:` name to the model that validates result.json
(design §6 step 5).

Engine tier per design §14 lines 486-488: this is the table the engine's
validation step reads, exercised with a canned model rather than a harness.
"""

import pytest
from pydantic import BaseModel

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
