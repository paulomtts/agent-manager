"""Folding a run's journal lines back into its §9 tree, and comparing that tree
with the projection."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from agent_manager import models
from agent_manager.store import journal as store_journal


def _describe_node(node: dict[str, str | int | None]) -> str:
    """`"run"` for the run, else its non-`None` coordinates as `key=value`."""
    parts = [f"{key}={value}" for key, value in node.items() if value is not None]
    return " ".join(parts) if parts else "run"


class ProjectionDivergedError(RuntimeError):
    """The projection holds values no journal line recorded (divergence §3.6).

    Raised by `Store.rebuild_from_journal` before it deletes anything, when
    `diverging` finds a `foreign` mismatch: rebuilding would overwrite what
    something other than the store wrote. Not a `JournalError`: the journal is
    fine. `mismatches` holds only the foreign ones, in tree-walk order.
    """

    def __init__(self, run_id: str, mismatches: "list[Mismatch]") -> None:
        details = "; ".join(
            f"{_describe_node(mismatch.node)} {mismatch.field or 'shape'}:"
            f" journal {mismatch.journal!r}, projection {mismatch.projection!r}"
            for mismatch in mismatches
        )
        super().__init__(
            f"projection of run {run_id!r} holds values its journal never"
            f" recorded: {details}. Nothing was changed;"
            " rebuild_from_journal(..., force=True) overwrites them."
        )
        self.run_id = run_id
        self.mismatches = mismatches


_RETIRED_ATTEMPT_KEYS: frozenset[str] = frozenset({"tokens_in", "tokens_out", "cost"})
"""Attempt payload keys dropped before validation (remove-cost-tracking design,
docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md §4.3).

Every journal written before 2026-10-03 carries them on each `attempt_upsert`
line, as null. `models.Attempt` no longer declares them and forbids unknown
keys, so without this every old journal would stop replaying: the projection
could not be rebuilt, and resume adoption would silently decline every old run.
A named allow-list rather than `extra="ignore"`: any other unknown key still
fails, and only attempt payloads are touched."""


def _current_attempt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """`payload` without `_RETIRED_ATTEMPT_KEYS`, whatever their values, as a
    new dict. The journal line's own payload is never mutated."""
    return {
        key: value for key, value in payload.items() if key not in _RETIRED_ATTEMPT_KEYS
    }


def _upsert(
    items: list[Any], key: str, node: Any, children: str | None
) -> Any:
    """Replace the sibling with the same key, keeping its children, or append."""
    for index, existing in enumerate(items):
        if getattr(existing, key) == getattr(node, key):
            if children is not None:
                node = node.model_copy(update={children: getattr(existing, children)})
            items[index] = node
            return node
    items.append(node)
    return node


def _find(items: list[Any], key: str, value: str | None, what: str, seq: int) -> Any:
    for existing in items:
        if getattr(existing, key) == value:
            return existing
    raise store_journal.JournalError(
        f"journal line {seq} names {what} {value!r}, which no earlier line created"
    )


def replay(lines: Iterable[store_journal.JournalLine]) -> models.Run:
    """Fold journal lines, in sequence order, back into the §9 tree.

    Nothing here is defensive: a line that fails `models` validation raises the
    `pydantic.ValidationError` straight out, because an old-schema line has to
    fail loudly rather than quietly drop a field from the projection. The one
    exception is `_RETIRED_ATTEMPT_KEYS`: an `attempt_upsert` payload sheds
    those three named keys, which every pre-2026-10-03 journal carries, before
    it is validated, and any other unknown key still raises.
    """
    run: models.Run | None = None

    for line in sorted(lines, key=lambda item: item.seq):
        if line.event == "run_upsert":
            fresh = models.Run.model_validate(line.payload)
            run = fresh if run is None else fresh.model_copy(
                update={"stories": run.stories}
            )
            continue

        if run is None:
            raise store_journal.JournalError(
                f"journal line {line.seq} is a {line.event} but no run_upsert"
                " preceded it: the head of the journal is missing"
            )

        if line.event == "story_upsert":
            _upsert(
                run.stories,
                "card_id",
                models.StoryRun.model_validate(line.payload),
                "subtasks",
            )
            continue

        story = _find(run.stories, "card_id", line.story, "story", line.seq)

        if line.event == "subtask_upsert":
            _upsert(
                story.subtasks,
                "card_id",
                models.SubtaskRun.model_validate(line.payload),
                "phases",
            )
            continue

        subtask = _find(story.subtasks, "card_id", line.card, "subtask", line.seq)

        if line.event == "phase_upsert":
            _upsert(
                subtask.phases,
                "name",
                models.PhaseRun.model_validate(line.payload),
                "attempts",
            )
            continue

        phase = _find(subtask.phases, "name", line.phase, "phase", line.seq)
        attempt = models.Attempt.model_validate(_current_attempt_payload(line.payload))
        _upsert(phase.attempts, "n", attempt, None)

    if run is None:
        raise store_journal.JournalError("journal contains no run_upsert line")
    return run


MismatchKind = Literal["stale", "foreign"]
"""§3.2: `stale` is a value the journal recorded for that node at some seq (or a
node the projection lacks), which a rebuild repairs; `foreign` is a value no
journal line ever recorded for that node (or a node no line created), written
outside the store."""


@dataclass(frozen=True)
class Mismatch:
    """One place the projection disagrees with the replayed journal (§3.3).

    `node` is keyed by the journal's own coordinates (`story`, `card`, `phase`,
    `attempt`, as `JournalLine` and am-watch spell them), all `None` for the
    run. `field` is `"status"` for a status mismatch and `None` for a shape
    mismatch; then `journal`/`projection` is the status on the side that has
    the node and `None` on the side that lacks it.
    """

    node: dict[str, str | int | None]
    field: Literal["status"] | None
    journal: str | None
    projection: str | None
    kind: MismatchKind


_NodeKey = tuple[str | None, str | None, str | None, int | None]
"""(story, card, phase, attempt): one node of the §9 tree in `JournalLine`'s
coordinates. The run is all `None`."""

_RUN_KEY: _NodeKey = (None, None, None, None)

_NODE_MODELS: dict[str, type[BaseModel]] = {
    "run_upsert": models.Run,
    "story_upsert": models.StoryRun,
    "subtask_upsert": models.SubtaskRun,
    "phase_upsert": models.PhaseRun,
    "attempt_upsert": models.Attempt,
}
"""The model each event's payload validates as, exactly as `replay` reads it
(an `attempt_upsert` payload first sheds `_RETIRED_ATTEMPT_KEYS`)."""

_LEVELS: tuple[tuple[str, str], ...] = (
    ("stories", "card_id"),
    ("subtasks", "card_id"),
    ("phases", "name"),
    ("attempts", "n"),
)
"""Below the run, each level's child list and the field `_upsert` matches
siblings by. The identity value of a node at level `i` fills slot `i` of its
`_NodeKey`."""


def _coords(key: _NodeKey) -> dict[str, str | int | None]:
    story, card, phase, attempt = key
    return {"story": story, "card": card, "phase": phase, "attempt": attempt}


def _child_key(key: _NodeKey, depth: int, value: Any) -> _NodeKey:
    """`key` with the child's identity `value` in the slot for level `depth`."""
    slots = list(key)
    slots[depth] = value
    return (slots[0], slots[1], slots[2], slots[3])


def _line_node(line: store_journal.JournalLine, node: Any) -> _NodeKey:
    """The node a line describes, located the way `replay` places it: the
    envelope names its ancestors, the payload's identity field names it."""
    if line.event == "run_upsert":
        return _RUN_KEY
    if line.event == "story_upsert":
        return (node.card_id, None, None, None)
    if line.event == "subtask_upsert":
        return (line.story, node.card_id, None, None)
    if line.event == "phase_upsert":
        return (line.story, line.card, node.name, None)
    return (line.story, line.card, line.phase, node.n)


def _journaled_statuses(lines: list[store_journal.JournalLine]) -> dict[_NodeKey, set[str]]:
    """Every status each node was ever journaled at, at any seq (§3.2)."""
    seen: dict[_NodeKey, set[str]] = {}
    for line in sorted(lines, key=lambda item: item.seq):
        payload = (
            _current_attempt_payload(line.payload)
            if line.event == "attempt_upsert"
            else line.payload
        )
        node: Any = _NODE_MODELS[line.event].model_validate(payload)
        seen.setdefault(_line_node(line, node), set()).add(node.status)
    return seen


def _walk(
    key: _NodeKey,
    depth: int,
    journal_node: Any,
    projection_node: Any,
    seen: dict[_NodeKey, set[str]],
    found: list[Mismatch],
) -> None:
    """Compare one node both sides have, then its children, in tree order.

    A child only the journal has is one `stale` shape mismatch; a child only
    the projection has is one `foreign` shape mismatch, listed after the
    journal's children. Neither's descendants are reported.
    """
    if journal_node.status != projection_node.status:
        found.append(
            Mismatch(
                node=_coords(key),
                field="status",
                journal=journal_node.status,
                projection=projection_node.status,
                kind=(
                    "stale"
                    if projection_node.status in seen.get(key, set())
                    else "foreign"
                ),
            )
        )
    if depth == len(_LEVELS):
        return
    children, identity = _LEVELS[depth]
    theirs = {
        getattr(child, identity): child for child in getattr(projection_node, children)
    }
    # One mismatch per missing subtree root: its descendants are not walked.
    journal_ids: set[Any] = set()
    for child in getattr(journal_node, children):
        value = getattr(child, identity)
        journal_ids.add(value)
        other = theirs.get(value)
        if other is None:
            found.append(
                Mismatch(
                    node=_coords(_child_key(key, depth, value)),
                    field=None,
                    journal=child.status,
                    projection=None,
                    kind="stale",
                )
            )
        else:
            _walk(_child_key(key, depth, value), depth + 1, child, other, seen, found)
    # Nodes only the projection has follow the journal's, in projection order.
    for child in getattr(projection_node, children):
        value = getattr(child, identity)
        if value not in journal_ids:
            found.append(
                Mismatch(
                    node=_coords(_child_key(key, depth, value)),
                    field=None,
                    journal=None,
                    projection=child.status,
                    kind="foreign",
                )
            )


def diverging(lines: list[store_journal.JournalLine], projection: models.Run) -> list[Mismatch]:
    """Every place `projection` disagrees with the journal `lines` replay to.

    Pure: the caller loads both sides; nothing here reads a file or the
    database, and neither argument is mutated. `replay`'s own errors
    (`JournalError`, pydantic `ValidationError`) propagate unchanged.

    Only `status` is compared, at every level of the §9 tree, plus shape.
    Nodes are matched as `replay` matches them: the run by itself, a story by
    `card_id`, a subtask by its story and `card_id`, a phase by `name`, an
    attempt by `n`. A differing status is `stale` if the journal ever recorded
    the projection's value for that node, else `foreign` (§3.2; a node set
    back to an earlier journaled status is therefore `stale`, by decision).
    Mismatches come out in tree walk order. An empty list means they agree.
    """
    lines = list(lines)
    journal = replay(lines)
    seen = _journaled_statuses(lines)
    found: list[Mismatch] = []
    _walk(_RUN_KEY, 0, journal, projection, seen, found)
    return found
