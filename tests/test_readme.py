"""README contract checks for the plugin-facing surface (card 9f0d5c8c).

Pure file reading plus pure builders from `cli` and the Pydantic models in
`store`: no subprocess, no git, brd or claude, so every test here is unit
tier and carries no marker.
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path

from agent_manager import cli, detach, models, store

README = Path(__file__).resolve().parents[1] / "README.md"
IGNORE_UNKNOWN = "Consumers should ignore any key they do not recognize."
ADDITIVE = "never removes or renames one"
LOGS_TITLE = "Reading an attempt's output"


def _lines() -> list[str]:
    return README.read_text(encoding="utf-8").splitlines()


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
    of the same or a higher level (its own subsections included)."""
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


def _slug(title: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation but `-` dropped, spaces to `-`."""
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def _fenced_json_lines(section: str) -> list[tuple[str, dict]]:
    """(raw line, parsed object) for every line inside a code fence that starts with `{`."""
    found: list[tuple[str, dict]] = []
    fenced = False
    for line in section.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced and line.startswith("{"):
            found.append((line, json.loads(line)))
    return found


def _usage_paragraph() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("Every command prints one line of JSON")
    end = text.index("\n\n", start)
    return text[start:end]


def test_usage_names_both_streaming_commands():
    paragraph = _usage_paragraph()
    assert "`am watch --follow`" in paragraph
    assert "`am logs --follow`" in paragraph
    anchors = re.findall(r"\]\(#([^)]+)\)", paragraph)
    assert "watching-a-run" in anchors
    assert _slug(LOGS_TITLE) in anchors
    slugs = {_slug(title) for _, _, title in _headings()}
    assert set(anchors) <= slugs, f"dangling anchors: {set(anchors) - slugs}"


def test_runs_section_documents_new_keys():
    section = _section("Listing runs")
    new_keys = ("milestone_id", "card_id", "lease", "progress")
    assert set(new_keys) <= set(store.RunSummary.model_fields)
    for key in new_keys:
        assert f"`{key}`" in section, key
    for model in (
        store.RunLease,
        store.RunProgress,
        store.ProgressCount,
        store.ProgressCurrent,
    ):
        for field in model.model_fields:
            assert re.search(rf"\b{field}\b", section), f"{model.__name__}.{field}"
    assert "It is `null` on a `--card` run" in section
    assert "It is `null` on a milestone run" in section
    assert "`null` if no process has a lease row for it" in section
    assert "`am status <run-id>` shows in `control.lease`" in section
    assert ADDITIVE in section
    assert IGNORE_UNKNOWN in section


def test_detach_section_documents_envelope():
    section = _section("Running detached with `--detach`")
    examples = _fenced_json_lines(section)
    assert len(examples) == 1
    _, envelope = examples[0]
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == {"run_id", "pid", "log", "detached"}
    assert data["detached"] is True
    assert isinstance(data["pid"], int)
    assert data["log"].endswith(f"/runs/{data['run_id']}/{detach.RUN_LOG_NAME}")
    assert detach.RUN_LOG_NAME in section
    assert detach.REPORT_NAME in section
    assert f"mode {detach.FILE_MODE:04o}" in section
    assert "exit code 3" in section
    assert "exits 0" in section
    assert "usage error (exit 2)" in section


def test_watch_documents_from_now():
    section = _section("Watching a run")
    assert (
        "The shape is `am watch RUN_ID | --all [--since SEQ] [--follow [--from-now]]`:"
        in section
    )
    assert "am watch --all --follow --from-now" in section
    assert "- `--from-now` together with `--since`, any value, 0 included;" in section
    assert "- `--from-now` without `--follow`;" in section
    assert "only lines appended after the command started" in section
    assert '"schema":1' in section
