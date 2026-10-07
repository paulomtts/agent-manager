"""The Claude adapter: one `Dispatch` in, one `claude -p` argv out (card
9a2524a8).

Design §8 lines 306-313 prints the `HarnessAdapter` Protocol; this module is
its first concrete implementation. It satisfies that Protocol structurally and
inherits from nothing -- the Protocol is pure interface and deliberately not
`runtime_checkable` (see its docstring in `base.py`), so a base class would buy
nothing and would invite default implementations the other adapters do not
want.

Two rules shape everything here. `build_command` is pure: it reads no file,
starts no process and consults no clock, so `launcher.py` (injected by the
engine, §14 line 485) stays the only thing in the program that runs anything.
And the adapter reads nothing back for results: D4 makes the harness's stdout
a log rather than a channel. The one exception is `limit_hit`, which only tells
the engine that a failed exit was a usage limit.
"""

from datetime import datetime
from pathlib import Path

from agent_manager.harness.claude_limits import read_limit
from agent_manager.harness.limits import LimitHit
from agent_manager.models import Dispatch

COMMAND = "claude"
"""The executable. A bare name, resolved on PATH by the launcher's Popen: the
adapter has no business knowing where a machine installed its harness."""

PROMPT_INSTRUCTION = (
    "Read {path} and follow the instructions in it exactly. It is your "
    "complete brief for this task."
)
"""The `-p` argument: a pointer at the materialized prompt, not the prompt.

§7 lines 296-298 pass documents by path rather than inlining them, and D4 puts
the result path inside that rendered prompt text upstream -- so this one
sentence is the whole bridge between the file the engine wrote and the process
the launcher starts. Inlining the file instead would re-bill it, invite a stale
copy, and make `build_command` read the disk.
"""


class ClaudeAdapter:
    """Turns a `Dispatch` into a `claude -p` argv. Reads nothing back (D4)."""

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

    def limit_hit(self, stdout_path: Path, now: datetime) -> LimitHit | None:
        """The usage-limit hit the `claude` log reports, else `None` (`harness.claude_limits`)."""
        return read_limit(stdout_path, lambda: now)

    def build_command(self, d: Dispatch) -> list[str]:
        """The argv for one attempt. Pure: no disk, no clock, no process.

        The two checks are the ones `Dispatch` cannot make for itself. Both are
        `ValueError` rather than a bespoke class because no retry can fix
        either, and neither is a harness failure to journal -- they are bugs
        above the adapter.
        """
        if d.harness != self.name:
            raise ValueError(
                f"dispatch is routed to harness {d.harness!r}, not "
                f"{self.name!r}: building a {self.name!r} argv for it would run "
                f"the wrong program against a real worktree"
            )
        for field in ("cwd", "prompt_path", "result_path"):
            path = getattr(d, field)
            if not path.is_absolute():
                raise ValueError(
                    f"Dispatch.{field} must be absolute, got {str(path)!r}: the "
                    f"harness starts in the worktree, so a relative path would "
                    f"resolve inside it -- and a result file written there gets "
                    f"committed (D4 keeps it outside)"
                )
        return [
            COMMAND,
            "--model",
            d.model,
            # D7: v1 launches full-auto. Confinement is the launcher's seam,
            # not a flag the adapter negotiates.
            "--dangerously-skip-permissions",
            "-p",
            PROMPT_INSTRUCTION.format(path=d.prompt_path),
        ]
