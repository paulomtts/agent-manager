"""The harness adapter interface and the value type it trades in.

Design §8 lines 306-313 print `HarnessAdapter` as a Protocol, and this module
is that Protocol verbatim plus the `Outcome` the launcher hands back. It is
pure interface and pure data: no adapter is implemented here (`claude.py`,
`codex.py`, `pi.py` are their own cards), nothing is launched here
(`launcher.py` owns that), and nothing reads the result file (§6 step 5 is the
engine's).

`Outcome` is built in-process from a subprocess that has already finished, so
it is a plain frozen dataclass rather than a pydantic model: the CLAUDE.md rule
keeps pydantic for what crosses a process boundary, and validating `Outcome`
would only re-check values this program just produced.

`Outcome` deliberately carries no result payload and no log contents. The
launcher never opens the result path and never reads the log it wrote --
nothing does (D4) -- which is what lets one launcher serve every adapter, and
what leaves §6 line 278's third harness_error trigger -- a missing result
file -- to the engine.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent_manager.harness.limits import LimitHit
from agent_manager.models import Dispatch


@dataclass(frozen=True)
class Outcome:
    """The result of running one harness process, before anyone classifies it.

    The engine turns this into one of §6 line 277's four journalled outcomes:
    `timed_out or exit_code != 0` is `harness_error`, and the remaining
    distinctions (`ok`, `schema_invalid`, `gate_failed`, and the missing-result
    -file flavour of `harness_error`) come from the result file, which this
    type never sees.

    `exit_code` is `None` exactly when `timed_out` is true: a process the
    launcher killed has no exit status of its own to report, and `-9` would be
    indistinguishable from a harness that genuinely died of SIGKILL.
    """

    argv: list[str]
    exit_code: int | None
    timed_out: bool
    duration: float
    stdout_path: Path


class HarnessAdapter(Protocol):
    """What every harness adapter must provide (§8 lines 306-313, verbatim).

    Pure interface: no default implementations and no base class anyone
    inherits from, so an adapter is a `HarnessAdapter` by shape alone. Not
    `runtime_checkable` -- a runtime check would only assert member presence,
    which reads as a guarantee it cannot give, and nothing in this program
    needs to ask.

    `build_command` returns an argv list, never a shell string: §5 line 252 is
    explicit that nothing here builds a command string for anything to run
    verbatim. It is the adapter's whole job: D4 makes the harness's stdout a
    log rather than a channel, so no adapter reads anything back.

    `capabilities` is data for the engine's plan-time capability check
    (§8 lines 336-339); methodology is never a capability, that is what
    vendoring is for.
    """

    name: str
    capabilities: frozenset[str]

    def build_command(self, d: Dispatch) -> list[str]: ...


class LimitReporting(Protocol):
    """The optional capability of an adapter that can tell a usage-limit hit.

    An adapter that cannot simply omits `limit_hit`; the engine then treats
    every failed exit as an ordinary `harness_error`. The format of whatever
    the harness prints is the adapter's alone.
    """

    def limit_hit(self, stdout_path: Path, now: datetime) -> LimitHit | None:
        """The hit the log at `stdout_path` reports after a failed exit, else `None`.

        `now` anchors a reset time the harness states without a date.
        """
        ...
