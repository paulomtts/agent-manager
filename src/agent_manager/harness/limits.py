"""What a harness adapter reports when its account's usage limit is spent.

The limit is shared by every process on the machine, so a hit says nothing
about the attempt that met it. An adapter that can recognise one implements
`HarnessAdapter.limit_hit` and returns a `LimitHit`; the message format is
the adapter's alone, and nothing outside the adapter reads it.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

LimitKind = Literal["session", "weekly", "other"]

TAIL_BYTES = 16384
"""How much of a log's end `read_tail` returns."""


@dataclass(frozen=True)
class LimitHit:
    """A usage-limit hit.

    `resets_at` is timezone-aware, or `None` when the adapter could not tell
    when the limit resets. `raw` is the adapter's own text for the hit.
    """

    kind: LimitKind
    resets_at: datetime | None
    raw: str


def read_tail(path: Path) -> str | None:
    """The last `TAIL_BYTES` of the log at `path` as text, or `None` when it cannot be read."""
    try:
        with path.open("rb") as log:
            log.seek(0, 2)
            log.seek(max(0, log.tell() - TAIL_BYTES))
            return log.read().decode("utf-8", errors="replace")
    except OSError:
        return None
