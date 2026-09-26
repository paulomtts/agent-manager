"""Fresh pygents registries and compile cache around every runtime test.

pygents' `ToolRegistry` and `AgentRegistry` are process-wide and refuse a
second entry under a name they hold, and `compile_workflow` caches per
`(name, digest)`. Clearing all three before and after each test keeps one
test's compilation or agent from colliding with the next.
"""

import pytest
from pygents import AgentRegistry, ToolRegistry

from agent_manager.runtime import compile as compile_mod


@pytest.fixture(autouse=True)
def fresh_pygents():
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()
    yield
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()
