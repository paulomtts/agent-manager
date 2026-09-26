"""Harness name -> adapter instance (design §6 step 1, §8 lines 306-318).

`Dispatch.harness` and `Policy.default_model`'s keys are both harness names, and
something has to turn one into the object whose `build_command` the engine
calls. That is all this module is. It deliberately does not route roles: which
harness a role gets is `RunConfig.harness_map`'s answer, read by
`dispatch.resolve_target`.

A fresh dict per call: a module-level singleton is mutable global state any
importer could rebind an adapter in.
"""

from agent_manager.harness.base import HarnessAdapter
from agent_manager.harness.claude import ClaudeAdapter

DEFAULT_HARNESS = "claude"
"""The harness a role with no `harness_map` entry is dispatched to.

`claude` because it is the only adapter implemented (§4 line 131 names `codex`
and `pi` as later cards). Named as a constant so adding the second adapter is a
one-line decision in one place rather than a scattered default.
"""


def default_adapters() -> dict[str, HarnessAdapter]:
    """Every adapter that ships, keyed by its own `name`."""
    return {ClaudeAdapter.name: ClaudeAdapter()}
