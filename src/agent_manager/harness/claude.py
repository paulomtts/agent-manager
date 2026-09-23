"""The Claude adapter: one `Dispatch` in, one `claude -p` argv out (card
9a2524a8).

Design §8 lines 306-313 prints the `HarnessAdapter` Protocol; this module is
its first concrete implementation. It satisfies that Protocol structurally and
inherits from nothing -- the Protocol is pure interface and deliberately not
`runtime_checkable` (`base.py` lines 75-92), so a base class would buy nothing
and would invite default implementations the other adapters do not want.

Two rules shape everything here. `build_command` is pure: it reads no file,
starts no process and consults no clock, so `launcher.py` (injected by the
engine, §14 line 485) stays the only thing in the program that runs anything.
And `parse_usage` never raises: D4 makes stdout a log rather than a channel, so
a chatty, truncated or usage-free log must not fail an attempt that produced a
perfectly good result file.
"""

from agent_manager.harness.base import Usage
from agent_manager.models import Dispatch


class ClaudeAdapter:
    """Turns a `Dispatch` into a `claude -p` argv and reads usage back out."""

    name = "claude"
    """The key `Dispatch.harness` carries and `Policy.default_model` is indexed
    by (`roles/bundles/coder/policy.toml` -> `default_model.claude`). One
    spelling, so routing, policy lookup and journalling cannot drift apart."""

    capabilities = frozenset({"bash", "edit"})
    """What this harness can genuinely do, for the plan-time capability check
    (§8 lines 336-339).

    `browser` is deliberately absent: Claude Code only drives a browser through
    an extension that a headless runner is not guaranteed to have, and claiming
    it would make the capability check pass for a phase that then cannot run.
    Claiming less than the truth refuses a phase early, which is the safe
    direction. Methodology is never listed -- D6 makes it vendored prompt text.
    """

    def build_command(self, d: Dispatch) -> list[str]:
        raise NotImplementedError  # Task 2

    def parse_usage(self, stdout: str) -> Usage | None:
        raise NotImplementedError  # Task 3
