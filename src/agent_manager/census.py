"""Find one milestone among the board's root cards.

`--milestone` used to be a small integer. A UUID is not typeable, so a title
substring is accepted too -- but never guessed at: zero matches or two matches
is an error naming what was on the board, and the caller decides what to type
next. Ported exactly from `census.mjs:62-98` in the leave-me-alone plugin,
including its messages, so the two tools refuse in the same words.

`MilestoneNotFoundError` subclasses `ValueError` on purpose: `ValueError` is
already in `cli.HANDLED`, so a CLI caller turns it into an `ok: false`
envelope without this module importing `cli` (which will import this module).

This module is pure: no I/O, no subprocesses, no ``brd``.
"""

from agent_manager.models import CardNode


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

    matches = [root for root in pool if lowered in root.title.lower()]
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
