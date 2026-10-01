"""Outcome comment bodies for brd cards (board-comments design B2-B5).

Pure: no I/O, no `brd`, no store, no clock. Each `compose_*` turns one
outcome's data into a `Comment` that a caller later enqueues and flushes
(Task 2.1); this module never posts anything. Agent text enters only
through `agent_reason`'s three failure fields, quoted, `[[`-escaped and
cut first when a body would exceed `CAP`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_manager.runtime.walk import SubtaskSummary

CAP = 1500
"""Hard cap on one comment body, in characters (B4)."""

ELLIPSIS = "…"


@dataclass(frozen=True)
class Comment:
    """One composed outcome comment. Internal state, so a dataclass (CLAUDE.md)."""

    card_id: str
    key: str
    body: str


def key(run_id: str, card_id: str, event: str) -> str:
    """The idempotency key `<run-id>/<card-id>/<event>` (B5)."""
    return f"{run_id}/{card_id}/{event}"


_REASON_FIELDS = {
    "validate_spec": "reason",
    "validate_plan": "reason",
    "implement": "blocked_reason",
    "review": "unresolved_blockers",
}
"""The only agent-written field each failing phase may put on the board (B3)."""


def _field(value: Any, name: str) -> Any:
    """`name` off a mapping or an object, or None. Never raises.

    `SubtaskSummary.results` values are decoded pydantic models or plain
    dicts (`runtime/context.decode`); run payloads are plain dicts. Anything
    else reads as absent.
    """
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def agent_reason(results: Mapping[str, Any], failed_phase: str) -> str | None:
    """The agent's own explanation of `failed_phase`'s failure, or None.

    Exactly one field per phase (`_REASON_FIELDS`); review's list of
    unresolved blockers is joined with `"; "`. Another phase, a missing
    entry, or an empty value gives None.
    """
    name = _REASON_FIELDS.get(failed_phase)
    if name is None:
        return None
    value = _field(results.get(failed_phase), name)
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(item) for item in value if item)
    if not isinstance(value, str) or not value.strip():
        return None
    return value
