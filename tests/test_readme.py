"""README contract checks for the plugin-facing surface (card 9f0d5c8c).

Pure file reading plus pure builders from `cli` and the Pydantic models in
`store.queries`: no subprocess, no git, brd or claude, so every test here is unit
tier and carries no marker.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import typing
from datetime import datetime, timezone
from pathlib import Path

import pytest
import typer

from agent_manager import (
    argv_guard,
    cli,
    detach,
    dispatch,
    errors,
    migrate,
    models,
    orchestrate,
    paths,
    prompt,
    runs,
)
from agent_manager.harness import launcher
from agent_manager.store import backup as store_backup
from agent_manager.store import db as store_db
from agent_manager.store import journal as store_journal
from agent_manager.store import queries as store_queries

README = Path(__file__).resolve().parents[1] / "README.md"
IGNORE_UNKNOWN = "Consumers should ignore any key they do not recognize."
ADDITIVE = "never removes or renames one"
LOGS_TITLE = "Reading an attempt's output"
STORY_TITLE = "Running one story with `--story`"
STORY_SHAPE = (
    "The shape is `am run --story STORY --branch-prefix P [--base-branch B]"
    " [--verify CMD ...] [--allow-no-verification] [--dry-run] [--detach]"
    " [--repo-dir D]`."
)
ISOLATION_TITLE = "Isolating agents with `--isolation`"
ARGV_TITLE = "Verification commands stay out of `ps`"

CHECKOUT_TITLE = "Installing `am` from a checkout"
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


def _assert_anchors_resolve(text: str) -> list[str]:
    """Every `](#anchor)` in `text`, each asserted to be some heading's slug."""
    anchors = re.findall(r"\]\(#([^)]+)\)", text)
    slugs = {_slug(title) for _, _, title in _headings()}
    assert set(anchors) <= slugs, f"dangling anchors: {set(anchors) - slugs}"
    return anchors


def _command_param(command: str, name: str):
    """The Click parameter `name` of the `am` subcommand `command`, introspected in-process."""
    group = typer.main.get_command(cli.app)
    return next(param for param in group.commands[command].params if param.name == name)


def _long_options(command: str) -> set[str]:
    """Every `--long` option of the `am` subcommand `command`, introspected in-process."""
    group = typer.main.get_command(cli.app)
    return {
        opt
        for param in group.commands[command].params
        for opt in param.opts
        if opt.startswith("--")
    }


def _backticked_flags(text: str) -> set[str]:
    """Every `--flag` token inside a backtick span of `text`."""
    return {
        flag
        for span in re.findall(r"`([^`\n]+)`", text)
        for flag in re.findall(r"--[a-z][a-z-]*", span)
    }


def _paragraph(opening: str) -> str:
    """The README paragraph that starts at the first occurrence of `opening`."""
    text = README.read_text(encoding="utf-8")
    start = text.index(opening)
    end = text.find("\n\n", start)
    return text[start : len(text) if end == -1 else end]


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
    new_keys = ("milestone_id", "card_id", "story_id", "lease", "progress", "project")
    assert set(new_keys) <= set(store_queries.RunSummary.model_fields)
    for key in new_keys:
        assert f"`{key}`" in section, key
    for model in (
        store_queries.RunLease,
        store_queries.RunProgress,
        store_queries.ProgressCount,
        store_queries.ProgressCurrent,
        store_queries.RunProject,
    ):
        for field in model.model_fields:
            assert re.search(rf"\b{field}\b", section), f"{model.__name__}.{field}"
    assert "It is `null` on a `--card` run" in section
    assert "It is `null` on a milestone run" in section
    assert "the story card an `am run --story` run drives" in section
    assert "It is `null` on any other run" in section
    assert "its `milestone_id` is still the story's parent milestone" in section
    assert "`null` if no process has a lease row for it" in section
    assert "`am status <run-id>` shows in `control.lease`" in section
    assert "`project`: `{id, repo_dir}`" in section
    for flag in ("`--all-projects`", "`--limit N`", "`--before X`"):
        assert flag in section, flag
    assert "pass the last run id of the previous page" in section
    assert ADDITIVE in section
    assert IGNORE_UNKNOWN in section


def test_stream_section_names_the_head_lines_story_id():
    section = _section("Reading the stream safely")
    assert "`payload.config.story_id`" in section
    assert "`null` unless the run is an `am run --story` run" in section


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


WATCH_SHAPE = (
    "The shape is `am watch RUN_ID | --all | --all-projects | --project PATH"
    " [--since SEQ] [--since-seq GSEQ] [--follow [--from-now]]`:"
)


def test_watch_documents_from_now():
    section = _section("Watching a run")
    assert WATCH_SHAPE in section
    assert "am watch --all --follow --from-now" in section
    assert "- `--from-now` together with `--since`, any value, 0 included;" in section
    assert "- `--from-now` together with `--since-seq`, any value, 0 included;" in section
    assert "- `--from-now` without `--follow`;" in section
    assert "only events recorded after the command started" in section
    # The section's pre-existing hello example already holds `"schema":2`,
    # so pin the --from-now paragraph's own sentence, not the bare token.
    assert 'The hello line is the same, `"schema":2`.' in section


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


def test_story_section_documents_the_story_flag():
    """Card 2cc5e2a5: the `--story` section documents matching, the root,
    the `StoryBlockedError` refusal, no Integrate, claims, and pause, resume,
    detach and dry run."""
    section = _section(STORY_TITLE)
    assert STORY_SHAPE in section
    assert "am run --story" in section and "--branch-prefix m3" in section
    for name in (
        errors.StoryBlockedError.__name__,
        errors.StoryNotFoundError.__name__,
        cli.ClaimedError.__name__,
    ):
        assert f"`{name}`" in section, name
    # Matching (census.find_story).
    assert "The exact id of a milestone or of a subtask is refused: it is not a story." in section
    assert "Milestone and subtask titles never match." in section
    # The blocker list, one line per status.
    lines = section.splitlines()
    for start in ("- `merged`: ", "- `done`: ", "- `canceled` or `archived`: ignored."):
        assert any(line.startswith(start) for line in lines), start
    assert "- anything else: open, and the run is refused (see below)." in section
    assert "<prefix>/base-<short id>" in section
    # The refusal.
    assert "— run them first, or run the milestone" in section
    assert "no claim, no `git fetch`, no run row and no run directory" in section
    assert "A story with no subtask left to run is not judged on its blockers." in section
    assert "exit code 3" in section
    # No Integrate.
    assert "<prefix>-integrate" in section
    assert "minus `integrated`" in section
    assert "`tips` names this story alone" in section
    assert "exits 0" in section
    # Run record, claims, resume, dry run.
    assert "`story_id`" in section
    assert "never `branch:<prefix>-integrate`" in section
    assert "`config.story_id`" in section
    assert '"integrate": null' in section
    assert '{"max_concurrent": 1, "levels", "already_done", "integrate": null}' in section
    # Every in-section link lands on a heading.
    anchors = re.findall(r"\]\(#([^)]+)\)", section)
    assert "multiple-blockers" in anchors
    assert "what-a-clean-run-leaves-behind" in anchors
    assert "listing-runs" in anchors
    assert "several-am-processes" in anchors
    slugs = {_slug(title) for _, _, title in _headings()}
    assert set(anchors) <= slugs, f"dangling anchors: {set(anchors) - slugs}"


def test_story_section_sits_next_to_milestone():
    heads = _headings()
    titles = [title for _, _, title in heads]
    assert titles.count(STORY_TITLE) == 1
    assert (
        titles.index("Preview with `--dry-run`")
        < titles.index(STORY_TITLE)
        < titles.index("Running every open milestone with `--board`")
    )
    position = titles.index(STORY_TITLE)
    assert heads[position][1] == 4
    parent = next(head for head in reversed(heads[:position]) if head[1] < 4)
    assert parent[1:] == (3, "Milestone runs")
    assert _slug(STORY_TITLE) == "running-one-story-with---story"


def test_run_refusals_name_story():
    text = README.read_text(encoding="utf-8")
    start = text.index("Some combinations are refused")
    refusals = text[start : text.index("\n\n", start)]
    for phrase in (
        "`--story` together with `--card`, `--milestone` or `--board`",
        "none of the four",
        "a blank `--story`",
        "a missing `--branch-prefix` with `--card`, `--milestone` or `--story`",
        "`--max-concurrent` with `--card` or `--story` (whatever its value)",
        "`--board` together with `--card` or with `--milestone`",
        "a `--max-concurrent` below 1 (with `--milestone` or `--board`)",
        "`--detach` with `--dry-run`",
        "the exit code is 2",
    ):
        assert phrase in refusals, phrase
    assert "none of the three" not in text
    intro = _section("Milestone runs").split("\n#### ")[0]
    assert "`--branch-prefix` is required with `--card`, `--milestone` and `--story`" in intro
    assert "](#running-one-story-with---story)" in intro
    flags = _section("Running every open milestone with `--board`")
    assert "Give exactly one of `--card`, `--milestone`, `--story` and `--board`." in flags
    assert "`--board` with `--card`, `--milestone` or `--story`" in flags


def test_claims_table_has_story_rows():
    section = _section("Several am processes")
    rows = section.splitlines()
    story = [row for row in rows if row.startswith("| `am run --story S` |")]
    assert len(story) == 1
    assert "`card:S`" in story[0]
    assert "-integrate" not in story[0]
    assert any(row.startswith("| `am resume R` of a story run |") for row in rows)
    assert (
        "A refused `am run --card`, `am run --milestone` or `am run --story`"
        " leaves no run directory" in section
    )


def test_detach_and_resume_name_story():
    detach_section = _section("Running detached with `--detach`")
    assert "`--story`" in detach_section
    relaunch = _section("Relaunching resumes")
    assert "am run --story" in relaunch
    assert "`config.story_id`" in relaunch
    assert f"`{runs.NotResumableError.__name__}`" in relaunch
    assert f"`{errors.StoryBlockedError.__name__}`" in relaunch
    assert "A story run never runs Integrate, on a resume either." in relaunch
    not_yet = _section("Milestone runs")
    assert "`am run --story` runs one story with no Integrate." in not_yet


LEGACY_JOURNAL_NOTE = (
    "Journals written before this version of `am` record a canceled run as"
    " `cancelled`, and those lines are never rewritten. Read both spellings as"
    " the same status. `am status` and `am runs` report such a run as `canceled`."
)
WATCH_SCHEMA_NOTE = (
    "- The hello line's `schema` field is where a schema bump is signaled."
    " It is `2` today. Schema 1 became 2 when `am` started writing a canceled"
    " run's status as `canceled` instead of `cancelled`; nothing else changed."
    " Lines are replayed as stored, so a schema-2 stream still carries"
    " `cancelled` for a run canceled by an older `am`: accept both, whatever"
    " the schema."
)
LEGACY_REPORT_NOTE = (
    "A report written before this version of `am`, `report.json` included,"
    " carries `cancelled` (`true`) instead."
)
KEY_SPELLING_NOTE = (
    "The `cancelled` event keeps its old spelling while the comment's first"
    " line says `canceled`: the key is how `am` recognizes a comment it"
    " already posted, so a cancel comment an older `am` queued or posted still"
    " matches and is never posted twice."
)


def test_readme_documents_legacy_cancelled():
    journal = _section("The journal line")
    assert "`started`, then `done`, `escalated`, `stopped` or `canceled` |" in journal
    assert (
        "`done`, `escalated`, `stopped` or `canceled`, and it escalated"
        " when that status is `escalated`." in journal
    )
    assert LEGACY_JOURNAL_NOTE in journal

    stream = _section("Reading the stream safely")
    assert WATCH_SCHEMA_NOTE in stream.splitlines()
    assert "It is `1` today." not in stream

    control = _section("Pausing and cancelling a run")
    assert "`data` holds `canceled` (`true`)" in control
    assert "The run is recorded `canceled`." in control
    assert '"status": "canceled", "already_canceled"' in control
    assert "`already_canceled: true`" in control
    assert LEGACY_REPORT_NOTE in control

    comments = _section("What each comment looks like").splitlines()
    assert "am · canceled · run <run-id>" in comments
    assert "am-key: <run-id>/<subtask-id>/cancelled" in comments

    key = _section("The `am-key` line")
    assert KEY_SPELLING_NOTE in key
    assert "`escalated:<lease-token>`, `cancelled`, `base-failed`" in key


def test_readme_spells_canceled_outside_legacy_sites():
    # `cancelled` survives only as the asyncio wording, the comment key, and
    # a backticked value in a line that says it is the legacy spelling.
    legacy_markers = ("before this version of `am`", "an older `am`", "where the event is")
    for line in _lines():
        if "cancelled" not in line:
            continue
        if "its lanes are cancelled" in line or "/cancelled" in line:
            continue
        assert "`cancelled`" in line, line
        assert any(marker in line for marker in legacy_markers), line
    assert "already_cancelled" not in README.read_text(encoding="utf-8")


def test_requires_names_bwrap_and_unshare():
    section = _section("Requires")
    assert "`bwrap`" in section
    assert "`unshare`" in section
    assert "un-isolated with a warning" in section
    assert _slug(ISOLATION_TITLE) in _assert_anchors_resolve(section)


def test_isolation_section_names_every_mode():
    section = _section(ISOLATION_TITLE)
    values = typing.get_args(launcher.IsolationRequest)
    for value in values:
        assert f"`{value}`" in section, value
    assert "`auto` (the default)" in section
    for form in ("`--card`", "`--milestone`", "`--story`", "`--board`", "`--detach`"):
        assert form in section, form
    assert "It is ignored with `--dry-run`" in section
    assert "am run --milestone" in section and "--isolation bwrap" in section

    option = _command_param("run", "isolation")
    assert option.default == "auto"
    assert list(option.type.choices) == list(values)

    heads = _headings()
    titles = [title for _, _, title in heads]
    assert (
        titles.index("Running detached with `--detach`")
        < titles.index(ISOLATION_TITLE)
        < titles.index("Preview with `--dry-run`")
    )
    position = titles.index(ISOLATION_TITLE)
    assert heads[position][1] == 4
    parent = next(head for head in reversed(heads[:position]) if head[1] < 4)
    assert parent[1:] == (3, "Milestone runs")
    assert _slug(ISOLATION_TITLE) == "isolating-agents-with---isolation"


def test_isolation_section_quotes_the_warning_and_refusal():
    section = _section(ISOLATION_TITLE)
    tail = "pass --isolation none to run without it"
    assert tail in str(errors.IsolationUnavailableError("bwrap", "x"))
    assert launcher.ISOLATION_NONE_WARNING in section
    assert f"`{errors.IsolationUnavailableError.__name__}`" in section
    assert "exit code 3" in section
    assert "before anything is written" in section
    assert f"isolation <mode> is unavailable: <reason> — {tail}" in section
    assert "starts with the exact command the probe ran" in section
    assert f"timed out after {launcher.PROBE_TIMEOUT:g}s" in section
    assert f"{launcher.PROBE_TIMEOUT:g}-second timeout" in section
    assert "at most once per process" in section
    assert "`auto` never refuses" in section
    assert "there is no fallback" in section


def test_isolation_section_names_where_the_mode_is_recorded():
    section = _section(ISOLATION_TITLE)
    assert {"launcher", "isolation_warning"} <= set(models.RunConfig.model_fields)
    assert {"direct", "bwrap", "unshare"} <= set(typing.get_args(models.Launcher))
    for phrase in (
        "`config.launcher`",
        "`config.isolation_warning`",
        "`direct`",
        "`payload.config`",
        "`data.warnings`",
        "`am status <run-id>` always has a `warnings` key",
    ):
        assert phrase in section, phrase


def test_isolation_section_describes_each_mode():
    section = _section(ISOLATION_TITLE)
    bwrap = launcher.wrap_argv("bwrap", ["true"], Path("/"))
    unshare = launcher.wrap_argv("unshare", ["true"], Path("/"))
    assert "--unshare-pid" in bwrap
    assert "--map-root-user" in unshare
    # The full prefixes, quoted, so the docs cannot outlive a flag.
    assert f"`{' '.join(bwrap[:-1])}`" in section
    assert f"`{' '.join(unshare[:-1])}`" in section
    assert "PID namespace" in section
    assert "uid 0" in section
    assert "read-write" in section
    assert "the network is untouched" in section
    assert "not a filesystem or network sandbox" in section

    assert prompt.PROCESS_SAFETY_BLOCK.startswith("## Process safety")
    assert '"Process safety" block' in section
    for command in ("`pkill -f`", "`killall`", "`kill -1`"):
        assert command in prompt.PROCESS_SAFETY_BLOCK
        assert command in section, command
    assert "isolation is the guarantee" in section
    assert "advice" in section
    _assert_anchors_resolve(section)


def test_neutral_argv_section():
    section = _section(ARGV_TITLE)
    assert argv_guard.COMMANDS == ("run", "resume")
    for text in (
        f"`{argv_guard.FROM_ENV_FLAG}`",
        f"`{argv_guard.VERIFY_ENV}`",
        argv_guard.ARGV_VISIBLE_WARNING,
        "`am run`",
        "`am resume`",
        "`--detach`",
        "`--milestone`",
        "`--story`",
        "`--branch-prefix`",
        "`--verify=X`",
        "exit code 2",
    ):
        assert text in section, text
    assert "matches command lines, never environments" in section
    assert "removes it from its own environment" in section
    assert "not an option to type" in section
    for reason in (
        "combined with `--verify`",
        "when `AM_VERIFY_JSON` is unset",
        "not a JSON list of strings",
    ):
        assert reason in section, reason
    assert "never echoed" in section
    assert "`data.warnings` ends with" in section
    for command in ("run", "resume"):
        assert _command_param(command, "verify_from_env").hidden is True

    titles = [title for _, _, title in _headings()]
    assert titles.index(ARGV_TITLE) == titles.index(ISOLATION_TITLE) + 1
    assert _slug(ARGV_TITLE) == "verification-commands-stay-out-of-ps"
    _assert_anchors_resolve(section)


RESUME_OPENING = "Pick a run back up where it was interrupted"
REPLACED = "verification: replaced in run record:"


def test_resume_documents_the_recorded_suite():
    assert REPLACED in inspect.getsource(cli.resume_run)
    assert {"verify", "allow_no_verification"} <= set(models.RunConfig.model_fields)

    text = README.read_text(encoding="utf-8")
    assert "The verification suite is not recorded" not in text
    assert "the suite is not recorded" not in text
    assert "pass your `--verify` commands again" not in text.lower()
    assert "pass the same `--verify` commands" not in text

    usage = _paragraph(RESUME_OPENING)
    assert usage in _section("Usage")
    assert "So are the verification suite and the opt-out." in usage
    assert f"`{REPLACED} [...]`" in usage
    assert "can only add the opt-out" in usage
    assert "verification: kept from checkpoint: [...]" in usage
    assert (
        "with the run's recorded suite (or the `--verify` passed now, which replaces it)"
        in usage
    )

    relaunch = _section("Relaunching resumes")
    assert f"`{REPLACED} [...]`" in relaunch
    assert "The run's recorded suite is restored" in relaunch

    story = _section(STORY_TITLE)
    assert (
        "the recorded suite is restored; a `--verify` passed now replaces it,"
        " as on a milestone run" in story
    )
    assert STORY_SHAPE in story


RESUME_ISOLATION_OPENING = "`am resume` restores the run's recorded isolation mode"
STATUS_WARNINGS_OPENING = "`am status <run-id>` also always has a `warnings` key"


def test_resume_documents_isolation():
    section = _section("Relaunching resumes")
    paragraph = _paragraph(RESUME_ISOLATION_OPENING)
    assert paragraph in section
    for phrase in (
        "accepts only `none`",
        "usage error (exit 2)",
        f"`{errors.IsolationUnavailableError.__name__}`",
        "exit code 3, nothing written",
        "never silently resumes un-isolated",
        "keeps its recorded warning",
        "`--isolation none`",
        "nothing is probed, there is no warning",
        "`config.launcher` is `direct`",
        "before any `verification: replaced in run record: [...]` entry",
    ):
        assert phrase in paragraph, phrase
    assert _slug(ISOLATION_TITLE) in _assert_anchors_resolve(paragraph)

    name = errors.IsolationUnavailableError.__name__
    bullets = [
        line for line in section.splitlines() if line.startswith("- ") and name in line
    ]
    assert len(bullets) == 2, bullets
    assert all("`--isolation none`" in line for line in bullets)

    option = _command_param("resume", "isolation")
    assert list(option.type.choices) == ["none"]
    assert option.default is None


def test_status_documents_warnings():
    paragraph = _paragraph(STATUS_WARNINGS_OPENING)
    assert paragraph in _section("Pausing and cancelling a run")
    assert "`[]`" in paragraph
    assert launcher.ISOLATION_NONE_WARNING in paragraph
    assert _slug(ISOLATION_TITLE) in _assert_anchors_resolve(paragraph)

    run = models.Run(
        id="20260923T140506Z-cbe34d00",
        workflow="task",
        repo_dir=Path("/repo"),
        base_branch="main",
        branch_prefix="m1",
        status="started",
        started_at=datetime(2026, 9, 23, 14, 5, 6, tzinfo=timezone.utc),
        stories=[],
    )
    assert cli.status_payload(run)["warnings"] == []
    run.config = models.RunConfig(isolation_warning=launcher.ISOLATION_NONE_WARNING)
    assert cli.status_payload(run)["warnings"] == [launcher.ISOLATION_NONE_WARNING]


def test_isolation_and_resume_passages_link_only_to_headings():
    for text in (
        _section("Requires"),
        _section(ISOLATION_TITLE),
        _section(ARGV_TITLE),
        _section(STORY_TITLE),
        _section("Relaunching resumes"),
        _paragraph(RESUME_OPENING),
        _paragraph(STATUS_WARNINGS_OPENING),
    ):
        _assert_anchors_resolve(text)
    assert "relaunching-resumes" in _assert_anchors_resolve(_section(ISOLATION_TITLE))
    assert "resuming-what-runs-again" in _assert_anchors_resolve(_paragraph(RESUME_OPENING))


HARNESS_FLAG = "`--harness-timeout [PHASE=]SECONDS`"
KEEPS_TIMEOUTS = "keeps the harness timeouts the run recorded"
REPLACES_BOTH = "replaces both the recorded default and the recorded per-phase overrides"


def test_usage_documents_the_harness_timeout_flag():
    usage = _section("Usage")
    assert HARNESS_FLAG in usage
    assert f"{cli.HARNESS_TIMEOUT_MIN} to {cli.HARNESS_TIMEOUT_MAX}" in usage
    assert f"{dispatch.DEFAULT_TIMEOUT:.0f}" in usage
    task_phases = ", ".join(f"`{phase}`" for phase in cli.TASK_AGENT_PHASES)
    assert task_phases in usage
    assert "`resolve`" in usage
    assert "exit 2" in usage
    assert "`--dry-run`" in usage
    assert "`harness_error`" in usage


def test_resume_text_keeps_or_replaces_the_harness_timeouts():
    for text in (_paragraph(RESUME_OPENING), _section("Relaunching resumes")):
        assert KEEPS_TIMEOUTS in text
        assert REPLACES_BOTH in text
        assert "not merged" in text
        assert "`resolve`" in text
        assert "exit 2" in text
    assert (
        "There is no `--base-branch`, no `--branch-prefix` and no `--max-concurrent` here"
        in _paragraph(RESUME_OPENING)
    )
    assert "takes only the `--harness-timeout` values given to it" in _section(
        "Relaunching resumes"
    )


def test_install_documents_the_non_editable_checkout_install():
    """Card b78f7935: the checkout-install subsection states the five P1 rules."""
    section = _section(CHECKOUT_TITLE)
    for pinned in INSTALL_PINNED:
        assert pinned in section, f"README.md {CHECKOUT_TITLE!r} lacks {pinned!r}"
    paragraphs = section.split("\n\n")
    assert any("never" in p and LIVE_RUN in p for p in paragraphs), (
        f"README.md {CHECKOUT_TITLE!r}: no paragraph says never ... {LIVE_RUN!r}"
    )
    assert "it prints nothing" in section or "must print nothing" in section
    install = _section("Install")
    assert "run `uv sync`" in install
    assert "it does not touch the installed `am`" in install


def test_checkout_install_sits_under_install():
    heads = [head for head in _headings() if head[2] == CHECKOUT_TITLE]
    assert len(heads) == 1
    assert heads[0][1] == 3
    assert f"### {CHECKOUT_TITLE}" in _section("Install")


def test_checkout_install_never_shows_an_editable_command():
    fenced_lines: list[str] = []
    fenced = False
    for line in _section("Install").splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            fenced_lines.append(line)
    assert "uv tool install --reinstall ." in fenced_lines
    for line in fenced_lines:
        assert "-e " not in line, line
        assert "--editable" not in line, line


MIGRATE_TITLE = "Migrating from per-project databases"


def test_migrate_section():
    section = _section(MIGRATE_TITLE)
    group = typer.main.get_command(cli.app)
    # "takes no arguments" stays true only while `--pretty` is its one parameter.
    assert [param.name for param in group.commands["migrate"].params] == ["pretty"]
    assert _long_options("migrate") <= _backticked_flags(section)
    assert (
        "The shape is `am migrate [--pretty]`; it takes no arguments and has no `--dry-run`."
        in section
    )
    assert "in one transaction" in section
    assert "`already_migrated: true`" in section
    assert "left untouched" in section

    for field in dataclasses.fields(migrate.MigrationReport):
        assert f"- `{field.name}`: " in section, field.name
    for model in (migrate.MigratedProject, migrate.ImportedJournal):
        for field in dataclasses.fields(model):
            assert re.search(rf"\b{field.name}\b", section), f"{model.__name__}.{field.name}"
    assert "`journals[].torn_line`" in section

    required = store_db.MigrationRequiredError.__name__
    assert f"`{required}`" in section
    tail = "run `am migrate` first. Nothing has been changed"
    assert tail in str(store_db.MigrationRequiredError([]))
    assert tail in section

    refused = migrate.MigrationRefusedError.__name__
    assert f"`{refused}`" in section
    template = str(migrate.MigrationRefusedError("<reason>", "<detail>", files=()))
    assert f"`{template}`" in section
    lines = section.splitlines()
    for reason in typing.get_args(migrate.RefusalReason):
        assert any(line.startswith(f"- `{reason}`: ") for line in lines), reason
    assert "wait for it to finish, or stop it" in section

    assert "run `am backup` first" in section
    assert "XDG_DATA_HOME=" in section

    heads = _headings()
    titles = [title for _, _, title in heads]
    assert titles.index(LOGS_TITLE) < titles.index(MIGRATE_TITLE) < titles.index(
        "Resuming: what runs again"
    )
    assert heads[titles.index(MIGRATE_TITLE)][1] == 3
    _assert_anchors_resolve(section)


DATA_DIR_TITLE = "The data directory"


def test_data_directory_notes():
    section = _section(DATA_DIR_TITLE)
    assert paths.data_path().name == "agent-manager"
    assert paths.db_path().name == "am.db"
    assert store_journal.JOURNAL_NAME == "journal.jsonl"
    for name in (
        "`$XDG_DATA_HOME/agent-manager`",
        "`~/.local/share/agent-manager`",
        "`am.db`",
        "`am.db-wal`",
        "`am.db-shm`",
        "`backups/`",
        "`runs/<run-id>/`",
        f"`{detach.RUN_LOG_NAME}`",
        f"`{detach.REPORT_NAME}`",
        f"`{store_journal.JOURNAL_NAME}`",
        "`boards/`",
        "`projects/<digest>.board.lock`",
        "`projects/<digest>.git.lock`",
        "legacy `projects/<digest>.db`",
    ):
        assert name in section, name
    assert "read only by `am migrate`" in section
    assert "still appends to but no longer reads" in section
    anchors = _assert_anchors_resolve(section)
    assert "several-am-processes" in anchors
    assert _slug(MIGRATE_TITLE) in anchors

    heads = _headings()
    titles = [title for _, _, title in heads]
    assert titles.index(MIGRATE_TITLE) < titles.index(DATA_DIR_TITLE) < titles.index(
        "Resuming: what runs again"
    )
    assert heads[titles.index(DATA_DIR_TITLE)][1] == 3

    several = _section("Several am processes")
    assert "the project database `<digest>.db`" not in several
    assert "with its own database" not in several
    lines = several.splitlines()
    one_dir = next(line for line in lines if line.startswith("- **One data directory per machine.**"))
    assert "`am.db`" in one_dir
    known = next(
        line for line in lines if line.startswith("- **A repository is known by its resolved path.**")
    )
    assert "a different project in `am.db`" in known


BACKUP_TITLE = "Backing up and restoring `am.db`"
RESNAPSHOT = "must drop its cursors and read a snapshot again"


def test_backup_and_restore_section():
    section = _section(BACKUP_TITLE)
    assert "The shape is `am backup [--out FILE] [--pretty]`:" in section
    assert _long_options("backup") <= _backticked_flags(section)
    assert "online-backup API" in section
    assert "safe while runs are live" in section
    assert "`<data dir>/backups/am-<YYYYMMDDTHHMMSSZ>.db`" in section
    for field in dataclasses.fields(store_backup.BackupResult):
        assert f"`{field.name}`" in section, field.name

    assert f"`{store_backup.BackupRefusedError.__name__}`" in section
    template = str(store_backup.BackupRefusedError("<reason>", Path("<path>")))
    assert f"`{template}`" in section
    lines = section.splitlines()
    for reason in typing.get_args(store_backup.BackupRefusal):
        assert any(line.startswith(f"- `{reason}`: ") for line in lines), reason

    assert "There is no `am restore` command" in section
    numbered = [line for line in lines if re.match(r"\d+\. ", line)]
    assert [line.split(".")[0] for line in numbered] == ["1", "2", "3"]
    assert "`lease.live`" in numbered[0]
    assert "`am.db-wal`" in numbered[1] and "`am.db-shm`" in numbered[1]
    assert "Do not delete them" in numbered[1]
    assert "No `am.db-wal` or `am.db-shm` may remain beside it." in numbered[2]
    assert "`store_id` is the one the backup was taken with" in section
    assert "possibly lower than before" in section
    assert "`am journal-check`" in section
    assert RESNAPSHOT in section
    assert "`am resume` takes it over" in section

    heads = _headings()
    titles = [title for _, _, title in heads]
    assert titles.index(LOGS_TITLE) < titles.index(BACKUP_TITLE) < titles.index(MIGRATE_TITLE)
    assert heads[titles.index(BACKUP_TITLE)][1] == 3
    assert _slug(BACKUP_TITLE) == "backing-up-and-restoring-amdb"
    assert "several-am-processes" in _assert_anchors_resolve(section)


SNAPSHOTS_TITLE = "Snapshots and cursors"


def test_snapshots_and_cursors_section():
    section = _section(SNAPSHOTS_TITLE)
    for name in (
        "`as_of_seq`",
        "`head`",
        "`gseq`",
        "`store_id`",
        "`cursor_reset`",
        "`--since-seq`",
        "`--after-seq`",
        "`--from-now`",
    ):
        assert name in section, name
    assert "increasing, may skip" in section
    for line in section.splitlines():
        if "contiguous" in line:
            assert "never assume" in line, line
    assert "am runs --all-projects" in section
    assert "am watch --all --follow --since-seq 1187" in section
    assert "no gap and no repeat" in section
    assert "`cursor_reset: true`" in section
    assert "keeps the `store_id` it was taken with" in section
    assert "gets no `cursor_reset`" in section
    assert RESNAPSHOT in section
    anchors = _assert_anchors_resolve(section)
    assert "listing-runs" in anchors
    assert _slug(BACKUP_TITLE) in anchors

    heads = _headings()
    titles = [title for _, _, title in heads]
    position = titles.index(SNAPSHOTS_TITLE)
    assert heads[position][1] == 4
    parent = next(head for head in reversed(heads[:position]) if head[1] < 4)
    assert parent[1:] == (3, "Watching a run")
    assert titles.index("Reading the stream safely") < position < titles.index(LOGS_TITLE)


WATCH_STALE = (
    "ordered by `(run_id, seq)`",
    "that every run writes to",
    "reads every run under",
    "a corrupt journal",
    "a run id with no journal",
)
WATCH_REFUSALS = (
    ("- `RUN_ID` together with `--project`;", {"run_id": "r", "project": Path("p")}),
    (
        "- `--project` together with `--all` or `--all-projects`;",
        {"run_id": None, "all_runs": True, "project": Path("p")},
    ),
    (
        "- none of `RUN_ID`, `--all`, `--all-projects` and `--project`,"
        " or `RUN_ID` together with `--all` or `--all-projects`;",
        {"run_id": None},
    ),
    ("- a `--since` below 0;", {"run_id": "r", "since": -1}),
    ("- a `--since-seq` below 0;", {"run_id": "r", "since_seq": -1}),
    (
        "- `--from-now` together with `--since`, any value, 0 included;",
        {"run_id": "r", "follow": True, "from_now": True, "since_given": True},
    ),
    (
        "- `--from-now` together with `--since-seq`, any value, 0 included;",
        {"run_id": "r", "follow": True, "from_now": True, "since_seq": 0},
    ),
    ("- `--from-now` without `--follow`;", {"run_id": "r", "from_now": True}),
)


def test_watch_documents_selectors_and_since_seq():
    section = _section("Watching a run")
    intro = section.split("\n#### ")[0]
    assert _long_options("watch") <= _backticked_flags(section)
    for flag in ("`--all-projects`", "`--project PATH`", "`--since-seq GSEQ`"):
        assert flag in intro, flag
    assert "`--all` and `--all-projects` are the same set" in intro
    assert "a path `am` has never run in gives no events, not an error" in intro
    assert "With both `--since` and `--since-seq`, a line must pass both." in intro
    assert "the list is ordered by `gseq`" in intro
    assert "A `--since-seq` at or above head gives `[]`, not an error." in intro
    assert "- a `RUN_ID` with no event and no run row in `am.db`, as `UnknownRunError`." in intro
    for phrase in WATCH_STALE:
        assert phrase not in intro, phrase

    # Every refusal bullet is a real refusal, in the order the code checks them.
    positions = []
    for bullet, call in WATCH_REFUSALS:
        assert bullet in intro, bullet
        positions.append(intro.index(bullet))
        kwargs = dict(call)
        with pytest.raises(cli.CliError):
            cli.watch_for(kwargs.pop("run_id"), **kwargs)
    assert positions == sorted(positions)

    anchors = _assert_anchors_resolve(intro)
    assert _slug(DATA_DIR_TITLE) in anchors
    assert _slug(SNAPSHOTS_TITLE) in anchors
