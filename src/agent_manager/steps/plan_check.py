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
