"""Which adapter instance a harness name resolves to (design §6 step 1, §8).

Adapters tier per design §14 line 484 -- this table is pure data about
adapters, and resolving a name starts nothing.
"""

from agent_manager.harness import registry
from agent_manager.harness.claude import ClaudeAdapter


def test_the_default_table_holds_the_claude_adapter_under_its_own_name():
    adapters = registry.default_adapters()

    assert set(adapters) == {"claude"}
    assert isinstance(adapters["claude"], ClaudeAdapter)
    assert adapters["claude"].name == "claude"


def test_the_default_harness_is_the_one_adapter_that_ships():
    assert registry.DEFAULT_HARNESS in registry.default_adapters()


def test_each_call_returns_a_fresh_table_nobody_else_can_poison():
    first = registry.default_adapters()
    first["claude"] = "not an adapter"

    assert isinstance(registry.default_adapters()["claude"], ClaudeAdapter)
