"""Does this card already have a plan that Validate signed off?

A deterministic step (design §4 `steps/`, §6 "The engine calls `run(ctx) -> dict`.
No network, no model."): it reads the filesystem and nothing else -- no git, no
`brd`, no subprocess, no model call. Ported from `planCheck` in the
leave-me-alone plugin's `scripts/plan-check.mjs`, whose `plan-check.test.mjs` is
the behavioural specification.

The agent this replaces did `ls` plus `grep` and cost a dispatch every run. The
answer decides one thing: whether the workflow's `plan_check` phase skips
straight to `implement` (design §5 lines 169-173).
"""

import re
from collections.abc import Mapping
from pathlib import Path

from agent_manager.dag import short_id

VALIDATED_MARKER = "<!-- task-pipeline: validated -->"
"""What Validate writes into a plan it has signed off.

Matched by literal substring containment, NEVER by regex. A plan whose prose
merely discusses the marker therefore reads as validated: that is the accepted
cost of a check that can never mistake a real marker for prose, which is the
failure that matters.
"""

_MD = ".md"


def matches_card(filename: object, card: str) -> bool:
    """Whether `filename` is a plan file belonging to short id `card`.

    The short id must be the LAST dash-delimited segment of the stem, so a
    longer hex run ending in the same eight characters cannot match. The old
    check tested that the preceding character was not a digit, which is the
    wrong anchor for hex -- `deadbeefa32af745` would have slipped through it.
    """
    name = "" if filename is None else str(filename)
    if not name.endswith(_MD):
        return False
    stem = name[: -len(_MD)]
    return stem.split("-")[-1] == card


def pick_plan(filenames: object, card: str) -> str | None:
    """The newest of the plan files matching `card`, or `None`.

    Newest is the lexicographically last name, exactly as the `.mjs` does:
    plan filenames are date-prefixed in practice, so sorting them is a date
    order, and no `stat` call is needed. A missing or empty listing is no
    match rather than an error.
    """
    if filenames is None:
        return None
    hits = sorted(name for name in filenames if matches_card(name, card))
    return hits[-1] if hits else None


_SHORT_ID = re.compile(r"^[0-9a-f]{8}$")


def _card_short_id(card: object) -> str:
    """The card's eight-character short id, whatever shape the card arrives in.

    `dag.short_id` owns the derivation and is never duplicated here; this only
    dispatches on shape, because `short_id` accepts a full UUID string and
    nothing else -- not a mapping, not an object, not an already-short id.
    Anything it rejects raises `ValueError` here too, before any filesystem
    touch: a typo must not read as "no plan found".
    """
    if isinstance(card, str):
        if _SHORT_ID.match(card):
            return card
        return short_id(card)
    # The same read `dag._field` performs, reimplemented rather than imported:
    # `_field` is private to that module and not part of its public surface.
    raw = card.get("id") if isinstance(card, Mapping) else getattr(card, "id", None)
    return short_id(raw)


def _plans_dir(plans_dir: object | None, repo_dir: object | None) -> str:
    """The directory to list: the explicit one, else `<repo_dir>/.claude/plans`."""
    if plans_dir is not None:
        return str(plans_dir)
    if repo_dir is None:
        raise ValueError(
            "plan_check.find_validated_plan needs plans_dir or repo_dir, got neither"
        )
    return str(Path(repo_dir) / ".claude" / "plans")
