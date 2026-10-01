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


def _unlink(text: str) -> str:
    """`text` with every `[[` broken to `[ [`, so it cannot create a ref (B4).

    Repeated until none is left: `str.replace` is non-overlapping, so one
    pass turns `[[[` into `[ [[`, which still opens a link.
    """
    while "[[" in text:
        text = text.replace("[[", "[ [")
    return text


def _cmd(command: str) -> str:
    """A command as the board shows it: in backticks (B4)."""
    return f"`{command}`"


def _render(
    first: str,
    before: Sequence[str],
    last: str,
    *,
    see: str,
    quote: tuple[str, str] | None = None,
    after: Sequence[str] = (),
) -> str:
    """One body of at most `CAP` characters (B4).

    Lines are `first`, `before`, the quoted agent text (`quote` is
    `(label, text)`), `after`, `last`. Agent text is escaped before anything
    is cut, then cut first, with the truncation marker naming `see`. If cutting
    it to nothing is still too long, the agent line is dropped and the
    joined `before` lines are cut instead. `first`, `after` and `last` are
    never cut.
    """
    marker = f"{ELLIPSIS} (truncated; see {_cmd(see)})"
    if quote is not None:
        label, text = quote
        text = _unlink(text)
        body = "\n".join([first, *before, f'{label}: "{text}"', *after, last])
        if len(body) <= CAP:
            return body
        empty = "\n".join([first, *before, f'{label}: "" {marker}', *after, last])
        room = CAP - len(empty)
        if room > 0:
            cut = f'{label}: "{text[:room]}" {marker}'
            return "\n".join([first, *before, cut, *after, last])
    else:
        body = "\n".join([first, *before, *after, last])
        if len(body) <= CAP:
            return body
    kept = "\n".join([first, marker, *after, last])
    room = CAP - len(kept) - 1
    head = "\n".join(before)[: max(room, 0)]
    return "\n".join([first, head, marker, *after, last])


def compose_escalated(
    *,
    run_id: str,
    card_id: str,
    token: str,
    failed_phase: str,
    detail: str | None,
    reason: str | None,
) -> Comment:
    """The subtask's escalation: phase, detail, the agent's reason, what next (B2)."""
    comment_key = key(run_id, card_id, f"escalated:{token}")
    logs = f"am logs {run_id} {card_id} --phase {failed_phase}"
    before = [f"phase: {failed_phase}"]
    if detail:
        before.append(f"detail: {_unlink(detail)}")
    body = _render(
        f"am · escalated · run {run_id}",
        before,
        f"am-key: {comment_key}",
        see=logs,
        quote=("reason", reason) if reason else None,
        after=[f"next: {_cmd(f'am resume {run_id}')}", f"why: {_cmd(logs)}"],
    )
    return Comment(card_id=card_id, key=comment_key, body=body)
