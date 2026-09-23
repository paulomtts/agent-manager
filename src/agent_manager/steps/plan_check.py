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


_SHORT_ID = re.compile(r"[0-9a-f]{8}")


def _card_short_id(card: object) -> str:
    """The card's eight-character short id, whatever shape the card arrives in.

    `dag.short_id` owns the derivation and is never duplicated here; this only
    dispatches on shape, because `short_id` accepts a full UUID string and
    nothing else -- not a mapping, not an object, not an already-short id.
    Anything it rejects raises `ValueError` here too, before any filesystem
    touch: a typo must not read as "no plan found".
    """
    if isinstance(card, str):
        # `fullmatch`, never `match` with a `$`: `$` also matches BEFORE a
        # trailing newline, so an id read off a file or command output would
        # pass through carrying it and then match no plan on disk.
        if _SHORT_ID.fullmatch(card):
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
    try:
        content = read(path)
    except (OSError, UnicodeDecodeError) as exc:
        # Different from a missing directory: the plan IS there, so the journal
        # must say why the run re-planned instead of pretending it was not.
        # UnicodeDecodeError is a ValueError, not an OSError, so a corrupt or
        # binary plan needs naming here or it escapes as a crash.
        return {
            "found": True,
            "path": path,
            "validated": False,
            "error": f"could not read {path}: {exc}",
        }
    return {"found": True, "path": path, "validated": VALIDATED_MARKER in content}


def has_validated_plan(result: object) -> bool:
    """The workflow's `when:` gate: skip to `implement` (design §5 lines 169-173).

    Pure -- it only reads the dict `find_validated_plan` returned. Both flags
    are required, and anything that is not a result mapping reads as closed: a
    phase that failed before producing a result must re-plan, never skip.
    """
    if not isinstance(result, Mapping):
        return False
    return bool(result.get("found")) and bool(result.get("validated"))


def _resolved_plan_path(plan_path: object, worktree: object | None) -> Path:
    """The plan file to mark: absolute as given, else rooted at `worktree`.

    `prompt.expand_writes` hands the engine a repo-RELATIVE posix path, so a
    relative `plan_path` with no worktree would resolve against whatever the
    process CWD happens to be and stamp the marker into the wrong file (or
    create nothing anyone reads). That is a caller bug, raised up front in the
    style of `_plans_dir` above and `verify._required_worktree`.
    """
    text = "" if plan_path is None else str(plan_path).strip()
    if text == "":
        raise ValueError(
            f"plan_check.mark_validated needs a plan_path, got {plan_path!r}"
        )
    path = Path(text)
    if path.is_absolute():
        return path
    root = "" if worktree is None else str(worktree).strip()
    if root == "":
        raise ValueError(
            f"plan_check.mark_validated got the relative plan_path {text!r} and no "
            f"worktree to root it at (worktree={worktree!r})"
        )
    return Path(root) / path


def mark_validated(
    plan_path: str | Path, worktree: object | None = None
) -> dict[str, object]:
    """Record that Validate signed this plan, by appending `VALIDATED_MARKER`.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    Filesystem only, like the rest of this module: the plan file is the only
    thing touched, and no hash is computed here (sibling ba15da20 owns that).

    Idempotent by literal containment, the same test `find_validated_plan`
    applies, so the writer and the reader can never disagree: an already-marked
    plan is left BYTE-IDENTICAL rather than rewritten, which is what makes a
    resumed run (design §9) safe. Appending instead of rewriting is deliberate
    -- it cannot re-encode or re-terminate a single existing byte.

    No `try` here on purpose: a missing, unreadable or non-UTF-8 plan must reach
    `engine._run_deterministic`, which is total, records the phase failed and
    escalates. A silent "nothing to mark" success would hand the sibling a
    Plan-Hash over a file that was never marked.
    """
    path = _resolved_plan_path(plan_path, worktree)
    text = path.read_text(encoding="utf-8")
    if VALIDATED_MARKER in text:
        return {"path": str(path), "appended": False}
    # `newline="\n"` so the two characters written are exactly the two intended,
    # on any platform; append mode so every byte already in the file survives.
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if text and not text.endswith("\n"):
            handle.write("\n")
        handle.write(f"{VALIDATED_MARKER}\n")
    return {"path": str(path), "appended": True}
