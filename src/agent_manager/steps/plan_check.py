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

import os
import re
from collections.abc import Callable, Mapping
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


DirLister = Callable[[str], list[str]]
"""Takes a directory path, returns its entry names, raises `OSError` if it cannot."""

FileReader = Callable[[str], str]
"""Takes a file path, returns its text, raises `OSError` if it cannot."""


def list_dir(path: str) -> list[str]:
    """The default `DirLister`: the real entry names of a real directory."""
    return os.listdir(path)


def read_file(path: str) -> str:
    """The default `FileReader`: the real UTF-8 text of a real file."""
    return Path(path).read_text(encoding="utf-8")


def find_validated_plan(
    card: object,
    plans_dir: object | None = None,
    *,
    repo_dir: object | None = None,
    list: DirLister = list_dir,
    read: FileReader = read_file,
) -> dict[str, object]:
    """Whether `card` already has a plan on disk, and whether Validate signed it.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    `path` is `""` and never `None` when nothing was found, so a consumer can
    format it without a guard.

    `list` and `read` default to the real filesystem and exist to be swapped in
    tests, the same callable-injection seam `worktree.py` uses for git and the
    ported `.mjs` uses for `readdir`/`readFile`.
    """
    card_id = _card_short_id(card)
    directory = _plans_dir(plans_dir, repo_dir)

    try:
        entries = list(directory)
    except OSError:
        # No plans directory is a normal answer on a first run, not a failure.
        return {"found": False, "path": "", "validated": False}

    name = pick_plan(entries, card_id)
    if name is None:
        return {"found": False, "path": "", "validated": False}

    path = os.path.join(directory, name)
    content = read(path)
    return {"found": True, "path": path, "validated": VALIDATED_MARKER in content}
