"""`runtime.state`: the `Adoption` value and `RunDeps.take_adoption` (exactly-once E4/E5).

Pure unit tier: plain values only, no store and no engine.
"""

import dataclasses

import pytest

from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.runtime.state import Adoption, RunDeps


def _deps(adopt: Adoption | None = None) -> RunDeps:
    return RunDeps(None, None, "story", None, None, lambda: None, adopt=adopt)


def test_an_adoption_is_built_from_a_turn_floor_and_is_frozen():
    adoption = Adoption(**vars(store_checkpoints.TurnFloor("explore", 1, "run-earlier", 2)))

    assert adoption == Adoption("explore", 1, "run-earlier", 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        adoption.floor = 3


def test_run_deps_start_with_no_adoption():
    assert RunDeps(None, None, "story", None, None, lambda: None).adopt is None


def test_take_adoption_returns_a_match_once():
    adoption = Adoption("explore", 0, "run-earlier", 3)
    deps = _deps(adopt=adoption)

    assert deps.take_adoption("explore", 0) == adoption
    assert deps.adopt is None
    assert deps.take_adoption("explore", 0) is None


@pytest.mark.parametrize("phase, loop", [("explore", 1), ("commit", 0)])
def test_take_adoption_clears_on_a_mismatch(phase, loop):
    deps = _deps(adopt=Adoption("explore", 0, "run-earlier", 3))

    assert deps.take_adoption(phase, loop) is None
    assert deps.adopt is None


def test_take_adoption_with_nothing_carried_returns_none():
    deps = _deps()

    assert deps.take_adoption("explore", 0) is None
    assert deps.adopt is None
