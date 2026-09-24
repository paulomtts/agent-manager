"""Write one card's board status (design §4 `steps/`, §6; subtask card 43008688).

A deterministic step: no model call, no network beyond whatever `brd` itself
does, no filesystem work of its own. The whole of it is one `board.set_status`
call, which runs `brd update <id> --status <status>`.

Ancestor roll-up -- propagating a subtask's completion up to its story and
milestone -- is deliberately not here: the real-harness addendum §4 defers it,
and this step writes exactly the one card the phase names.

Nothing is cached and nothing is read back: `board.show` before or after the
write would double the brd invocations for an answer the write already returns.

The first parameter is named `card`, not `card_id`, because the engine binds
arguments by parameter name out of the run context and the context key holding
the bare id string is `card` (`engine.py:98`, `bind_arguments` at
`engine.py:163-212`). There is no `card_id` key, so a parameter by that name
would fail to bind and the engine would report a missing required parameter.
"""

from collections.abc import Iterable
from pathlib import Path

from agent_manager import board

MAX_ANCESTRY_DEPTH = 16
"""Most ancestors the walk will visit; a guard against a corrupted parent chain."""


def stored_status(status: str) -> str:
    """The status as brd would store it: `blocked` is derived, so it reads as `todo`.

    brd computes `blocked` at read time from the dependency graph and refuses
    to store it, so every comparison flattens it here, in one place.
    """
    return "todo" if status == "blocked" else status


def rollup_status(children_statuses: Iterable[str]) -> str | None:
    """A parent's status computed from its direct children, by progress.

    `None` when there are no children (the parent is not written); `todo` when
    every child is unstarted; `done` when every child is done; otherwise
    `in_progress` -- one finished child among unstarted ones means the parent
    is under way, not unstarted. A port of leave-me-alone's `rollupStatus`.
    """
    statuses = [stored_status(status) for status in children_statuses]
    if not statuses:
        return None
    if all(status == "todo" for status in statuses):
        return "todo"
    if all(status == "done" for status in statuses):
        return "done"
    return "in_progress"


def set_status(
    card: str, status: str, repo_dir: str | Path | None = None
) -> dict[str, object]:
    """Set `card`'s board status to `status`, and report what brd stored.

    Called by the two `best_effort: true` phases of `builtin/task.yaml`
    (`mark_in_progress`, `mark_done`), which supply `status` through the
    document's `args`.

    Idempotency is inherited, not implemented: `brd update --status` stores the
    value it is given, so a repeated identical call is another successful write
    of the same value (`board.py:207-211`). There is no "already in this status"
    short-circuit, because resume re-runs whole phases and that must stay
    harmless.

    `board.BoardError` is not caught. Tolerance is the engine's policy -- both
    call sites are `best_effort`, so the engine journals the warning and the run
    continues; swallowing it here would make a failed board write invisible.

    The returned mapping reports the status on the `models.Card` brd answered
    with, not the requested literal: the board's copy is the truth. A plain
    `dict[str, object]`, like `worktree.ensure`'s result -- it crosses no
    process boundary, so it needs no pydantic model (`CLAUDE.md`).
    """
    written = board.set_status(
        card, status, repo_dir=Path(repo_dir) if repo_dir is not None else None
    )
    return {"card": written.id, "status": written.status}
