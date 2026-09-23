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
from collections.abc import Mapping

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


def _field(card: object, name: str) -> object:
    """Read ``name`` off a card given either as a mapping or as an object."""
    if isinstance(card, Mapping):
        return card.get(name)
    return getattr(card, name, None)


def task_stem(card: object) -> str:
    """Readable slug plus the load-bearing short id, or the short id alone."""
    slug = slugify(_field(card, "title"))
    card_short_id = short_id(_field(card, "id"))
    return f"{slug}-{card_short_id}" if slug else card_short_id


def task_branch(prefix: str, card: object) -> str:
    """Branch name for one card's task worktree."""
    return f"{prefix}/task-{task_stem(card)}"


def ref_matches_card(ref: object, card_id: object) -> bool:
    """True when a branch or ref carries this card's short id anywhere in it.

    Keys on the id and never on the slug, so editing a card's title cannot
    orphan the branch that was named from the old title.
    """
    return short_id(card_id) in str("" if ref is None else ref)
