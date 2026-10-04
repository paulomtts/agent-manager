"""Read one milestone off the board's root cards and flatten it into a census.

Three pure functions, each ported exactly from the leave-me-alone plugin's
`census.mjs`, including its messages, so the two tools refuse in the same
words.

`find_milestone` (`census.mjs:62-98`): `--milestone` used to be a small
integer. A UUID is not typeable, so a title substring is accepted too -- but
never guessed at: zero matches or two matches is an error naming what was on
the board, and the caller decides what to type next.

`order_siblings` (`census.mjs:12-49`): execution order for one card's
children comes from the `blocked_by` edges between the siblings themselves, so
the board states the order rather than encoding it in titles. Edges pointing
outside the sibling set are ignored -- a subtask blocked by another story's
card is a dispatch concern, not a sibling-ordering one. Independent siblings
keep creation order. A cycle is an error; cards are never silently dropped.

`flatten_milestone` (`census.mjs:51-58` and `census.mjs:100-113`): the
milestone's tree becomes ordered stories, each with ordered subtasks. brd
derives `blocked` at read time, while the orchestrator decides readiness with
its own DAG walk, so `blocked` is read as `todo` here, in one place, rather
than checked for everywhere.

`MilestoneNotFoundError` and `CensusOrderError` subclass `ValueError` on
purpose: `ValueError` is already in `cli.HANDLED`, so a CLI caller turns them
into an `ok: false` envelope without this module importing `cli` (which will
import this module).

This module is pure: no I/O, no subprocesses, no ``brd``. It imports
`agent_manager.models` and nothing from `cli` or `board`.
"""

import re
from dataclasses import dataclass

from agent_manager.models import CardNode

_NUMERIC = re.compile(r"[0-9]+")

FINISHED_STATUSES = frozenset({"done", "merged"})
"""brd statuses meaning the card's work is finished (`merged` is a human's
follow-up to `done`). The one definition of "finished" in `am`."""

OUT_OF_PLAY_STATUSES = frozenset({"canceled", "archived"})
"""brd statuses meaning the card is not part of the plan at all. `archived` is
treated exactly like `canceled`. Together with `FINISHED_STATUSES` these are
brd's releasing statuses; `am` never writes either set."""


def is_finished(status: str | None) -> bool:
    """True for `done` or `merged`, in any case."""
    return (status or "").lower() in FINISHED_STATUSES


def is_out_of_play(status: str | None) -> bool:
    """True for `canceled` or `archived`, in any case."""
    return (status or "").lower() in OUT_OF_PLAY_STATUSES


def _is_digit(ch: str) -> bool:
    """ASCII 0-9 only, as the JS `/[0-9]/`; `str.isdigit` also takes e.g. '٢'."""
    return "0" <= ch <= "9"


def _substring_hit(title: str, lowered: str, is_numeric: bool) -> bool:
    """Whether `lowered` is a usable substring of the lowercased `title`.

    A numeric needle ("2") must not resolve via a longer digit run it merely
    sits inside ("Milestone 12") -- that is not the milestone the caller typed,
    and silently resolving it turns a wrong READ into wrong branches, PRs and
    status writes. So a numeric needle only matches a digit run of its own
    length. Only the first occurrence is inspected, exactly as the JS does.
    """
    start = title.find(lowered)
    if start == -1:
        return False
    if not is_numeric:
        return True
    end = start + len(lowered)
    while start > 0 and _is_digit(title[start - 1]):
        start -= 1
    while end < len(title) and _is_digit(title[end]):
        end += 1
    return end - start <= len(lowered)


class MilestoneNotFoundError(ValueError):
    """No root card, or more than one, matches the milestone the caller typed."""


def find_milestone(roots: list[CardNode] | None, needle: str | int) -> CardNode:
    """The one root card `needle` names: exact id, exact title, or one substring.

    Order matters. An exact id wins. Then an exact case-insensitive title wins
    outright, even when it is also a substring of another root's title. Only
    then is a substring tried, and it must match exactly one root.
    """
    wanted = str(needle).strip()
    pool = list(roots or [])

    for root in pool:
        if root.id == wanted:
            return root

    lowered = wanted.lower()
    for root in pool:
        if root.title.lower() == lowered:
            return root

    is_numeric = _NUMERIC.fullmatch(wanted) is not None
    matches = [
        root
        for root in pool
        if _substring_hit(root.title.lower(), lowered, is_numeric)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        listed = ", ".join(root.title for root in pool) or "(none)"
        raise MilestoneNotFoundError(
            f'no milestone card matching "{wanted}" — root cards are: {listed}'
        )
    listed = ", ".join(match.title for match in matches)
    raise MilestoneNotFoundError(
        f'ambiguous milestone "{wanted}" — matches: {listed}'
    )


class CensusOrderError(ValueError):
    """Some siblings could not be ordered: their `blocked_by` edges form a cycle."""


def order_siblings(cards: list[CardNode] | None) -> list[CardNode]:
    """`cards` in execution order, by the `blocked_by` edges among them.

    Kahn's algorithm, as `census.mjs:12-49`. Only edges whose blocker is in
    `cards` count. Ready cards go earliest-created first (`created_at or ""`,
    then id), and the ready queue is re-sorted after every pop. The same
    `CardNode` objects come back, reordered; the input list is not touched.
    """
    pool = list(cards or [])
    if not pool:
        return []

    by_id = {card.id: card for card in pool}
    indegree = {card.id: 0 for card in pool}
    unlocks: dict[str, list[str]] = {card.id: [] for card in pool}

    for card in pool:
        for blocker_id in card.blocked_by:
            if blocker_id not in by_id:
                continue
            unlocks[blocker_id].append(card.id)
            indegree[card.id] += 1

    def earliest_first(card_id: str) -> tuple[str, str]:
        return (by_id[card_id].created_at or "", card_id)

    ready = sorted(
        (card.id for card in pool if indegree[card.id] == 0), key=earliest_first
    )
    ordered: list[CardNode] = []
    while ready:
        card_id = ready.pop(0)
        ordered.append(by_id[card_id])
        for unlocked in unlocks[card_id]:
            indegree[unlocked] -= 1
            if indegree[unlocked] == 0:
                ready.append(unlocked)
        ready.sort(key=earliest_first)

    if len(ordered) != len(pool):
        placed = {id(card) for card in ordered}
        stuck = [card.id for card in pool if id(card) not in placed]
        raise CensusOrderError(
            f"census: {len(stuck)} card(s) could not be ordered — "
            f"cyclic blocked_by among siblings: {', '.join(stuck)}"
        )
    return ordered


@dataclass(frozen=True)
class SubtaskPlan:
    """One subtask of a story, as the census reports it."""

    id: str
    title: str
    status: str


@dataclass(frozen=True)
class StoryPlan:
    """One story of the milestone, with its subtasks in execution order."""

    id: str
    title: str
    status: str
    blocked_by: list[str]
    subtasks: list[SubtaskPlan]


@dataclass(frozen=True)
class Census:
    """A milestone flattened: its title and its stories in execution order."""

    milestone_title: str
    stories: list[StoryPlan]


def _stored_status(status: str) -> str:
    """brd's derived `blocked` read as `todo` (`census.mjs:51-58`)."""
    return "todo" if status == "blocked" else status


def flatten_milestone(root: CardNode) -> Census:
    """`root`'s stories and each story's subtasks, ordered by `order_siblings`.

    Port of `census.mjs:100-113`. A story's `blocked_by` is copied through
    unchanged, ids outside the milestone included, except that out-of-play
    cards (`canceled`, `archived`) are dropped at any depth together with every
    `blocked_by` edge pointing at them: they are not part of the plan. A cycle
    among the stories or among one story's subtasks raises `CensusOrderError`.
    """
    out_of_play_ids: set[str] = set()

    def collect(card: CardNode) -> None:
        if is_out_of_play(card.status):
            out_of_play_ids.add(card.id)
        for child in card.children:
            collect(child)

    collect(root)

    def live(cards: list[CardNode]) -> list[CardNode]:
        return [card for card in cards if not is_out_of_play(card.status)]

    stories = [
        StoryPlan(
            id=story.id,
            title=story.title,
            status=_stored_status(story.status),
            blocked_by=[dep for dep in story.blocked_by if dep not in out_of_play_ids],
            subtasks=[
                SubtaskPlan(
                    id=subtask.id,
                    title=subtask.title,
                    status=_stored_status(subtask.status),
                )
                for subtask in order_siblings(live(story.children))
            ],
        )
        for story in order_siblings(live(root.children))
    ]
    return Census(milestone_title=root.title, stories=stories)
