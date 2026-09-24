"""Find one milestone among the board's root cards, and order card siblings.

Both are ported exactly from the leave-me-alone plugin's `census.mjs`,
including their messages, so the two tools refuse in the same words.

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

`MilestoneNotFoundError` and `CensusOrderError` subclass `ValueError` on
purpose: `ValueError` is already in `cli.HANDLED`, so a CLI caller turns them
into an `ok: false` envelope without this module importing `cli` (which will
import this module).

This module is pure: no I/O, no subprocesses, no ``brd``. It imports
`agent_manager.models` and nothing from `cli` or `board`.
"""

import re

from agent_manager.models import CardNode

_NUMERIC = re.compile(r"[0-9]+")


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
