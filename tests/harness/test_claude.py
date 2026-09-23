"""Behaviour of the Claude harness adapter (design §8 lines 304-318, card
9a2524a8).

Unit tier per design §14 line 484 ("Adapters -- `build_command` is pure and
asserted per harness; the launcher is injected, so no harness is executed in
unit tests"). Nothing here spawns a process, runs a real `claude` binary,
creates a worktree or writes a result file: `build_command` is asserted on the
argv it returns, and `parse_usage` on literal log strings held in this file.
Launcher behaviour is `test_launcher.py`'s; canned result files belong to the
engine card (§14 line 486); a real harness run is the single opt-in end-to-end
test (§14 lines 489-490).
"""

import tomllib
from pathlib import Path

from agent_manager.harness.base import HarnessAdapter
from agent_manager.harness.claude import ClaudeAdapter
from agent_manager.models import Dispatch
from agent_manager.roles.loader import bundles_dir


def _dispatch(**overrides: object) -> Dispatch:
    """A representative dispatch: absolute paths, a real role, a real model."""
    fields: dict[str, object] = {
        "harness": "claude",
        "model": "sonnet",
        "role": "coder",
        "cwd": Path("/repo/wt/9a2524a8"),
        "prompt_path": Path("/runs/run-1/9a2524a8/implement.1/prompt.md"),
        "result_path": Path("/runs/run-1/9a2524a8/implement.1/result.json"),
        "timeout": 60.0,
    }
    fields.update(overrides)
    return Dispatch(**fields)


def test_the_adapter_satisfies_the_protocol_structurally():
    # `HarnessAdapter` is deliberately not runtime_checkable (base.py lines
    # 78-82), so conformance is asserted member by member -- and the adapter
    # inherits from nothing, which is the whole point of a structural Protocol.
    adapter = ClaudeAdapter()
    for member in HarnessAdapter.__protocol_attrs__:
        assert hasattr(adapter, member), member
    assert ClaudeAdapter.__bases__ == (object,)
    assert callable(adapter.build_command)
    assert callable(adapter.parse_usage)


def test_the_name_is_the_one_key_routing_and_policy_both_use():
    # `Dispatch.harness`, `Policy.default_model`'s key and the journalled
    # harness name are one spelling; a rename here would silently strand
    # `default_model.claude` in every bundle.
    assert ClaudeAdapter.name == "claude"
    policy_path = bundles_dir() / "coder" / "policy.toml"
    policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    assert ClaudeAdapter.name in policy["default_model"]
    assert policy["default_model"][ClaudeAdapter.name] == "sonnet"


def test_capabilities_is_a_frozenset_of_non_empty_strings():
    # Data for the engine's plan-time capability check (§8 lines 336-339).
    # Frozen because it is class-level shared state, and a phase's `needs:`
    # check must not be able to mutate what a harness claims.
    assert isinstance(ClaudeAdapter.capabilities, frozenset)
    assert ClaudeAdapter.capabilities
    for capability in ClaudeAdapter.capabilities:
        assert isinstance(capability, str) and capability.strip()
