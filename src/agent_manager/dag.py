"""Card identity in every derived name: branches, plan files, spec files.

The two halves of a derived name are not equal. The short id is load-bearing
and the slug is decoration: a card's title can be edited after its branch
exists, and if matching keyed on the slug that edit would orphan the branch —
the run would report finished work as not done. So everything that MATCHES
uses the id, and the slug exists only so ``git branch`` output is readable.

Eight hex characters collides with probability that does not matter inside one
milestone. The check is deliberately strict: a card id is a UUID, and anything
else is a bug worth surfacing loudly rather than inventing a plausible short
id.

This module is pure: no I/O, no subprocesses, no ``brd``.
"""

import re

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")


def short_id(card_id: object) -> str:
    """First eight hex characters of a card UUID, dashes stripped, lowercased."""
    if not isinstance(card_id, str):
        raise ValueError(f"not a card id: {card_id!r}")
    hex_only = card_id.replace("-", "")
    if not _HEX32.match(hex_only):
        raise ValueError(f"not a card id: {card_id!r}")
    return hex_only[:8].lower()


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title: object, max: int = 24) -> str:
    """Lowercase dash-joined slug of a title, cut at a word boundary."""
    flat = _NON_SLUG.sub("-", str("" if title is None else title).lower()).strip("-")
    if len(flat) <= max:
        return flat
    # Cut at a word boundary rather than mid-word: a trailing "-uv" fragment
    # makes a branch name harder to read, not easier.
    cut = flat[:max]
    last_dash = cut.rfind("-")
    kept = cut[:last_dash] if last_dash > 0 else cut
    return kept.rstrip("-")
