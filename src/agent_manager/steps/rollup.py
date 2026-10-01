"""Write one card's board status and roll it up its ancestors.

Design §4 `steps/`, §6; subtask cards 43008688 and bf26f482; orchestration
addendum O5 (`docs/superpowers/specs/2026-09-24-orchestration-design.md:71-79`).

A deterministic step: no model call, no network beyond whatever `brd` itself
does, no filesystem work of its own. It writes the card with `board.set_status`
(`brd update <id> --status <status>`), then walks up the parent chain: for each
ancestor it reads the parent id with `board.show`, re-reads the parent's subtree
with `board.tree`, computes the parent's status from its direct children by
progress (`rollup_status`: all todo -> todo, all done -> done, anything else ->
in_progress, `blocked` counted as todo), and writes it only when it differs from
what is stored. The walk always reaches the root, so a stale grandparent is
repaired, and is capped at `MAX_ANCESTRY_DEPTH` ancestors. The status rule is a
port of `rollupStatus`/`storedStatus` from leave-me-alone's `scripts/rollup.mjs`.

Nothing is cached: every parent is read fresh, because sibling work can change
a shared ancestor between one level and the next.

The whole call -- the card's write and every level of the walk -- runs under
`board.write_lock(path)`, the project's process-wide board lock (spec X7), one
critical section, because a rollup reads, then modifies, then writes.
Concurrent calls on sibling subtasks, in this process or another `am` process,
therefore run one after another, and the last one sees every sibling's final
status. The lock is reentrant, so the nested `board.set_status` calls re-take
it on the same thread without a second flock.

The first parameter is named `card`, not `card_id`, because the engine binds
arguments by parameter name out of the run context and the context key holding
the bare id string is `card` (`runtime.walk.subtask_context` and
`bind_arguments`). There is no `card_id` key, so a parameter by that name
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
    """Set `card`'s board status, roll it up its ancestors, and report both.

    Called by the two `best_effort=True` steps of `TASK`
    (`mark_in_progress`, `mark_done`), which supply `status` through the
    workflow's `args`.

    First the card itself is written. Then the walk climbs one ancestor at a
    time: `board.show` names the parent (tree nodes carry no `parent_id`),
    `board.tree` reads that parent and its children fresh -- nothing is cached,
    so a concurrent write elsewhere is seen as late as possible -- and
    `rollup_status` computes its status from the direct children. The parent is
    written only when that differs from its `stored_status`. The walk always
    continues to the root, even past an unchanged parent, so a grandparent left
    stale by an interrupted earlier run is repaired. More than
    `MAX_ANCESTRY_DEPTH` ancestors raises `board.BoardError`.

    All of it -- the card's write and the whole walk -- holds
    `board.write_lock(path)`, so a concurrent call on a sibling, from any
    thread or process, cannot interleave its reads and writes with this one.
    The lock is released by a `with` block, so a `board.BoardError` from
    anywhere inside, the depth guard included, never leaves it held. A
    `locks.LockTimeoutError` while waiting for it propagates uncaught, before
    any `brd` call.

    Idempotency is inherited, not implemented: `brd update --status` stores the
    value it is given, so a repeated identical call is another successful write
    of the same card and, with the ancestors already correct, writes none of
    them. There is no "already in this status" short-circuit for the card,
    because resume re-runs whole phases and that must stay harmless.

    `board.BoardError` is not caught, from any call. Tolerance is the engine's
    policy -- both call sites are `best_effort`, so the engine journals the
    warning and the run continues; swallowing it here would make a failed board
    write invisible. Writes made before a failure are not undone; the next call
    repairs the rest.

    The returned mapping reports the statuses brd answered with, not the
    requested literals: the board's copy is the truth. `rolled_up` lists only
    the ancestors this call changed, nearest first, and is `[]` when the card
    has no parent or every ancestor was already right. A plain
    `dict[str, object]`, like `worktree.ensure`'s result -- it crosses no
    process boundary, so it needs no pydantic model (`CLAUDE.md`).
    """
    path = Path(repo_dir) if repo_dir is not None else None
    with board.write_lock(path):
        written = board.set_status(card, status, repo_dir=path)

        rolled_up: list[dict[str, str]] = []
        current = written.id
        depth = 0
        while True:
            parent_id = board.show(current, repo_dir=path).parent_id
            if not parent_id:
                break
            depth += 1
            if depth > MAX_ANCESTRY_DEPTH:
                raise board.BoardError(
                    "exceeded maximum ancestry depth",
                    argv=board.show_argv(current),
                )
            node = board.tree(parent_id, repo_dir=path)
            target = rollup_status(child.status for child in node.children)
            if target is not None and stored_status(node.status) != target:
                parent = board.set_status(parent_id, target, repo_dir=path)
                rolled_up.append({"card": parent.id, "status": parent.status})
            current = parent_id

        return {
            "card": written.id,
            "status": written.status,
            "rolled_up": rolled_up,
        }
