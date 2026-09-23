"""The harness adapter interface and the two value types it trades in.

Design §8 lines 306-313 print `HarnessAdapter` as a Protocol, and this module
is that Protocol verbatim plus the `Usage` and `Outcome` it names. It is pure
interface and pure data: no adapter is implemented here (`claude.py`,
`codex.py`, `pi.py` are their own cards), nothing is launched here
(`launcher.py` owns that), and nothing reads the result file (§6 step 5 is the
engine's).

The split between the two value types is the CLAUDE.md rule applied twice.
`Usage` is parsed out of another program's stdout -- a process boundary -- so
it is pydantic and validates. `Outcome` is built in-process from a subprocess
that has already finished, so it is a plain frozen dataclass; validating it
would only re-check values this program just produced.

`Outcome` deliberately carries no result payload and no parsed usage. The
launcher never opens the result path and never reads the log it wrote, which is
what lets one launcher serve every adapter, and what leaves §6 line 278's third
harness_error trigger -- a missing result file -- to the engine.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent_manager.models import Dispatch


class Usage(BaseModel):
    """What one attempt cost, as reported by the harness's own log.

    Frozen and `extra="forbid"` for the same reason the state models are: a
    harness that renames its usage field must fail loudly rather than journal a
    silent zero. Every field is optional because D4 makes stdout a log and not
    a channel -- a chatty, truncated or usage-free log must not fail an
    otherwise successful attempt, so `parse_usage` returns `Usage()` or `None`
    rather than raising.

    The field names match `Attempt.tokens_in`/`tokens_out`/`cost`
    (models.py lines 72-74) one for one, so the engine's journalling is a copy
    and not a translation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)


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
    verbatim.

    `parse_usage` returns `None` rather than raising when the log holds no
    usage. `capabilities` is data for the engine's plan-time capability check
    (§8 lines 336-339); methodology is never a capability, that is what
    vendoring is for.
    """

    name: str
    capabilities: frozenset[str]

    def build_command(self, d: Dispatch) -> list[str]: ...
    def parse_usage(self, stdout: str) -> Usage | None: ...
