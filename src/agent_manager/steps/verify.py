"""Run a card's verification commands in its worktree and report what happened.

A deterministic step (design §4 `steps/`, §6 "The engine calls `run(ctx) -> dict`.
No network, no model."): it launches the commands the card names and reads their
output. Ported from `ship()` and `verifyError()` in the leave-me-alone plugin's
`scripts/ship.mjs` (:70-90, :62-67), plus the `plainText`/`lastLine` flattening
helpers from `scripts/gh.mjs` (:40-48, :70-73).

Read-only (design §9: "`verify.run_suite` is read-only"). It never commits,
pushes, tags or opens a PR -- `ship.mjs`'s name is historical and that half does
not port -- and it does NOT check for a dirty worktree: design §5 gives that
rule to `review_gate`, and one rule lives in one place.

Every invocation is an argument list handed to `subprocess` (design §5 line
252). `ship.mjs` passed `shell: true`; this port deliberately drops it, so
there is no shell string and nothing to quote.
"""

import re

ELLIPSIS = "…"
"""One character, appended to text `plain_text` had to cut."""

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def last_line(text: object) -> str:
    """The last non-empty, trimmed line of `text`, or `""`.

    Ported from `gh.mjs`'s `lastLine`: tool managers print activation banners
    above real output, so the value asked for is the last line, not the first.
    `.strip()` also removes the `\\r` of CRLF output, which would otherwise
    travel into the result.
    """
    raw = "" if text is None else str(text)
    lines = [line.strip() for line in raw.split("\n")]
    hits = [line for line in lines if line]
    return hits[-1] if hits else ""


def plain_text(text: object, max_chars: int = 300) -> str:
    """`text` flattened to printable, length-capped text.

    Ported from `gh.mjs`'s `plainText`: ANSI sequences removed, remaining
    control characters collapsed to a single space, trimmed, then truncated
    with one `ELLIPSIS`. A raw ESC byte surviving into a reported field once
    failed a whole milestone after its PRs were already open, and a tail is a
    human hint rather than a payload -- hence both halves.
    """
    raw = "" if text is None else str(text)
    flat = _CONTROL.sub(" ", _ANSI.sub("", raw)).strip()
    return flat if len(flat) <= max_chars else flat[:max_chars] + ELLIPSIS
