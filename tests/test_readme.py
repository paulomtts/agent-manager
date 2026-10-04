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

from agent_manager import cli, detach, models, orchestrate, store

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


def test_detach_section_documents_the_board_form():
    """Card 03f027ea: `--board --detach` is documented in prose, with no second
    JSON example (`test_detach_section_documents_envelope` pins exactly one)."""
    section = _section("Running detached with `--detach`")
    assert "`--card`, `--milestone` and `--board`" in section
    assert "<data dir>/boards/" in section
    assert detach.BOARD_LOG_SUFFIX in section
    assert detach.BOARD_REPORT_SUFFIX in section
    for key in ("board", "detached", "pid", "log", "report", "levels"):
        assert f"`{key}`" in section
    assert "no `run_id`" in section
    assert "am watch --all" in section
    assert len(_fenced_json_lines(section)) == 1
    text = README.read_text(encoding="utf-8")
    assert "`--detach` with `--board`" not in text
    assert "or with `--board`" not in text


def test_board_section_documents_stacking():
    """Card 072e1f6c: the `--board` section documents milestone stacking: the
    table, the `MilestoneBlockersError` refusal, chaining, and which base a
    rerun or a resume uses."""
    section = _section("Running every open milestone with `--board`")
    lines = section.splitlines()
    name = orchestrate.MilestoneBlockersError.__name__

    # B3: the refusal list is exactly 1.-5., item 4 is the new refusal, before
    # the claim check, and the closing sentence still follows the list.
    numbered = [index for index, line in enumerate(lines) if re.match(r"\d+\. ", line)]
    assert [lines[index].split(".")[0] for index in numbered] == ["1", "2", "3", "4", "5"]
    fourth, fifth = lines[numbered[3]], lines[numbered[4]]
    assert name in fourth
    assert "ClaimedError" in fifth
    assert lines[numbered[4] + 2].startswith(
        "A refused board run leaves no run row, no run directory and no lease"
    )

    # B1: the stacking table, a well-formed two-column pipe table.
    table = [line for line in lines if line.startswith("|")]
    for line in table:
        assert line.count("|") == 3, line
    rows = [line for line in table[1:] if not re.fullmatch(r"\|[\s:|-]+\|", line)]
    assert len(rows) >= 5
    assert any(all(word in row for word in ("`merged`", "`canceled`", "`archived`")) for row in rows)
    assert any("`--base-branch`" in row for row in rows)
    assert any("-integrate" in row for row in rows)
    assert any("local" in row for row in rows)
    assert any(name in row for row in rows)
    assert "`am` never merges into `--base-branch`." in section
    assert "Stacking changes where a milestone starts, not when." in section

    # B2: a satisfied blocker can still be the base.
    assert "it counts as satisfied for scheduling" in section

    # B4: chaining, and marking a landed blocker merged.
    assert "chain" in section
    assert "A ← B ← C" in section or "A <- B <- C" in section
    assert "`brd block B --by A`" in section
    assert "`brd unblock C --by A`" in section
    assert "`brd update <id> --status merged`" in section
    assert any(all(word in line for word in ("mark", "`merged`", "chain")) for line in lines)

    # B5: the Recovery passage says which base a rerun and a resume use.
    recovery = section[section.index("Recovery. ") :]
    assert "computes each milestone's base again" in recovery
    assert any("am resume" in line and "`base_branch`" in line for line in recovery.splitlines())

    # Every in-section link lands on a heading.
    anchors = re.findall(r"\]\(#([^)]+)\)", section)
    slugs = {_slug(title) for _, _, title in _headings()}
    assert set(anchors) <= slugs, f"dangling anchors: {set(anchors) - slugs}"


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
    assert (
        "A line that was still being written when the command started"
        " is printed once it is complete." in section
    )
    # The section's pre-existing hello example already holds `"schema":1`,
    # so pin the --from-now paragraph's own sentence, not the bare token.
    assert 'The hello line is the same, `"schema":1`.' in section


def test_logs_section_shape_line():
    section = _section(LOGS_TITLE)
    assert (
        "The shape is `am logs RUN_ID CARD [--phase P] [--attempt N] [--follow]"
        " [--since-offset BYTES] [--repo-dir DIR]`:" in section
    )
    titles = [title for _, _, title in _headings()]
    assert (
        titles.index("Watching a run")
        < titles.index(LOGS_TITLE)
        < titles.index("Resuming: what runs again")
    )
    for refusal in (
        "- an unknown run, card, phase or attempt, as for the one-shot;",
        "- a `--since-offset` below 0;",
        "- `--since-offset` without `--follow`, whatever its value, 0 included;",
        "- an agent attempt that recorded no stdout path, since there is no file to follow.",
    ):
        assert refusal in section, refusal
    assert 'only a stream starts with `"event":"logs"`' in section
    assert "`am logs: <message>`" in section
    assert "stdout.log" in section
    assert "U+FFFD" in section
    assert "next chunk's `offset` is always exact" in section
    assert "takes no lease, no claim and no lock" in section
    assert ADDITIVE in section
    assert IGNORE_UNKNOWN in section


def test_logs_examples_match_code():
    examples = _fenced_json_lines(_section(LOGS_TITLE))
    hellos = [item for item in examples if item[1].get("event") == "logs"]
    ends = [item for item in examples if item[1].get("event") == "end"]
    chunks = [item for item in examples if "event" not in item[1]]
    assert len(hellos) == 1
    assert len(ends) == 1
    assert len(chunks) >= 2
    assert len(examples) == len(hellos) + len(ends) + len(chunks)
    assert examples[0] is hellos[0]
    assert examples[-1] is ends[0]

    hello_raw, hello = hellos[0]
    assert set(hello) == set(cli._logs_hello(Path("x"), 0))
    assert hello["schema"] == 1
    assert hello["path"].endswith("/stdout.log")
    assert hello_raw == cli.render(cli._logs_hello(Path(hello["path"]), hello["offset"]))

    expected = hello["offset"]
    for raw, chunk in chunks:
        assert set(chunk) == {"offset", "text"}
        assert raw == cli.render(chunk)
        assert chunk["offset"] == expected
        expected += len(chunk["text"].encode("utf-8"))

    end_raw, end = ends[0]
    assert set(end) == {"event", "status"}
    assert end["event"] == "end"
    terminal = set(typing.get_args(models.AttemptStatus)) - {"started"}
    assert end["status"] in terminal
    assert end_raw == cli.render(end)
