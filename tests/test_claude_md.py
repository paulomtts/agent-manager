"""CLAUDE.md contract checks for the installed-`am` dev note (card b78f7935).

Pure file reading: no subprocess, no git, brd, claude or uv, so every test
here is unit tier and carries no marker.
"""

from __future__ import annotations

import re
from pathlib import Path

CLAUDE_MD = Path(__file__).resolve().parents[1] / "CLAUDE.md"
INSTALLED_TITLE = "Installed `am`"
INSTALL_PINNED = (
    "uv tool install --reinstall .",
    "uv build",
    "uv tool install --reinstall dist/*.whl",
    "clean checkout of the verified commit",
    "non-editable",
    'grep editable "$(uv tool dir)/agent-manager/uv-receipt.toml"',
    "am runs",
)
LIVE_RUN = "while any `am` run is live"


def _lines() -> list[str]:
    return CLAUDE_MD.read_text(encoding="utf-8").splitlines()


def _headings() -> list[tuple[int, int, str]]:
    """(line index, level, title) of every Markdown heading outside a code fence."""
    found: list[tuple[int, int, str]] = []
    fenced = False
    for index, line in enumerate(_lines()):
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = re.match(r"(#{1,6}) (.+)$", line)
        if match:
            found.append((index, len(match.group(1)), match.group(2)))
    return found


def _section(title: str) -> str:
    """The body under the one heading titled `title`, up to the next heading
    of the same or a higher level."""
    lines = _lines()
    heads = _headings()
    matches = [head for head in heads if head[2] == title]
    assert len(matches) == 1, f"expected one heading {title!r}, found {len(matches)}"
    start, level, _ = matches[0]
    end = next(
        (index for index, other, _ in heads if index > start and other <= level),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def _paragraphs(section: str) -> list[str]:
    """Blank-line-separated paragraphs, each bullet counted as its own paragraph,
    with whitespace (hard wraps included) collapsed to single spaces."""
    found: list[str] = []
    for block in section.split("\n\n"):
        items: list[list[str]] = []
        for line in block.splitlines():
            if line.startswith("- ") or not items:
                items.append([])
            items[-1].append(line)
        found.extend(" ".join(" ".join(item).split()) for item in items)
    return [paragraph for paragraph in found if paragraph]


def test_claude_md_documents_the_installed_am():
    paragraphs = _paragraphs(_section(INSTALLED_TITLE))
    body = " ".join(paragraphs)
    for pinned in INSTALL_PINNED:
        assert pinned in body, f"CLAUDE.md {INSTALLED_TITLE!r} lacks {pinned!r}"
    assert any("never" in p and LIVE_RUN in p for p in paragraphs), (
        f"CLAUDE.md {INSTALLED_TITLE!r}: no bullet says never ... {LIVE_RUN!r}"
    )
    assert any(
        "uv tool install" in p and "~/.local" in p and "must not" in p
        for p in paragraphs
    ), f"CLAUDE.md {INSTALLED_TITLE!r}: no bullet forbids an agent uv tool install / ~/.local"


def test_claude_md_installed_am_sits_before_conventions():
    titles = [title for _, level, title in _headings() if level == 2]
    assert titles.count(INSTALLED_TITLE) == 1
    assert (
        titles.index("Test tiers")
        < titles.index(INSTALLED_TITLE)
        < titles.index("Conventions")
    )
