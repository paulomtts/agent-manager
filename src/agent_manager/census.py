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
