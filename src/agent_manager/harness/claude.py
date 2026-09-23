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

import math
import re

from pydantic import ValidationError

from agent_manager.harness.base import Usage
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


def _key(*words: str) -> str:
    """A usage key, tolerant of the two ways a log spells one.

    `"input_tokens":` in a JSON line and `Input tokens:` in a human-readable
    summary are the same fact, and which one a harness version prints is not
    something a cost figure should depend on.

    The lookbehind is the point of this helper: without it, `input_tokens`
    matches inside `cache_creation_input_tokens`, and a cache counter gets
    journalled as the prompt size.
    """
    return r"(?<![\w-])" + r"[ _]".join(words)


_VALUE = r'"?\s*[:=]\s*"?\$?((?:[^\s,"}\]]|,(?=\d))+)'
"""The value after a usage key: quoted or bare, `:` or `=`, `$` allowed.

A comma ends the value unless a digit follows it, so `8,123` in a human-readable
summary survives while the `,` that separates two JSON keys still terminates the
match. Each alternative consumes exactly one character and the two are disjoint,
so there is nothing for the engine to backtrack over.

Deliberately loose about *what* it captures: a negative, non-numeric, inf or nan
value is captured here and dropped by the converters below, which is what makes
the dropping testable rather than accidental."""

_TOKENS_IN = re.compile(_key("input", "tokens") + _VALUE, re.IGNORECASE)
_TOKENS_OUT = re.compile(_key("output", "tokens") + _VALUE, re.IGNORECASE)
_COST = re.compile(
    r"(?<![\w-])(?:total[ _])?cost(?:[ _]usd)?" + _VALUE, re.IGNORECASE
)
"""Every pattern is literals plus one single-character repetition -- no nested
quantifiers and no ambiguous alternation -- so scanning a multi-megabyte log is
linear and cannot backtrack catastrophically."""


def _tokens(raw: str) -> int | None:
    """A token count, or `None` if the log printed something that is not one."""
    try:
        value = int(raw.replace(",", "").rstrip(".;"))
    except ValueError:
        return None
    return value if value >= 0 else None


def _cost(raw: str) -> float | None:
    """A USD cost, or `None`.

    `inf` and `nan` parse as floats and would then be rejected by `Usage`'s
    `allow_inf_nan=False`, turning a garbled log line into an exception. They
    are dropped here instead.
    """
    try:
        value = float(raw.replace(",", "").rstrip(".;"))
    except ValueError:
        return None
    if math.isnan(value) or math.isinf(value) or value < 0:
        return None
    return value


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

    def parse_usage(self, stdout: str) -> Usage | None:
        """What the attempt cost, as far as the log says. Never raises.

        Each field is taken from its *last* match: a log that reports more than
        once is reporting cumulatively, and the final figure describes the whole
        run. A field whose last value will not convert is dropped rather than
        propagated, and a dispatch where nothing survives reports `None` -- D4
        makes stdout a log, so a usage-free log is a successful attempt.
        """
        fields: dict[str, int | float] = {}
        for field, pattern, convert in (
            ("tokens_in", _TOKENS_IN, _tokens),
            ("tokens_out", _TOKENS_OUT, _tokens),
            ("cost", _COST, _cost),
        ):
            found = pattern.findall(stdout)
            if not found:
                continue
            value = convert(found[-1])
            if value is not None:
                fields[field] = value
        if not fields:
            return None
        try:
            return Usage(**fields)
        except ValidationError:
            # Unreachable while the converters above are the only source of
            # values, and kept anyway: "never raises" is the contract the
            # engine relies on, and it must not depend on a converter staying
            # exactly as strict as `Usage` is.
            return None
