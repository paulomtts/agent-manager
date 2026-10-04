<!-- task-pipeline: validated -->
# 6.1 README: runs fields, --detach, --from-now, logs --follow (card 9f0d5c8c)

Parent story 9e8758a0 "Document the new surface", milestone fbf624d6 "am interfaces for the Omarchy plugin". This narrows `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` sections 1-4 and "Compatibility and versioning" to README text. Section 5 (`--board`) belongs to sibling 5.2 and is out of scope.

## Where this card starts

The exploration findings say master (09e8b80) has none of the implementation. That is still true of master, but this card's worktree branch (`.claude/worktrees/ami/task-6-1-readme-runs-fields-9f0d5c8c`) already contains the merged sibling work: `src/agent_manager/detach.py`, `--from-now` in `watch_for`/`_follow_watch`, `--follow`/`--since-offset` in `logs` (`logs_follow_for`, `_logs_hello`, `_follow_logs`, `logs_end_status`, `step_end_status`), and the 5.2 `--board` README section. The README on this branch also already has two of the four items:

- `#### Running detached with --detach` (README ~line 83) documents the envelope, `run.log`/`report.json`, mode 0600, exit 0, refusals before hand-off, and ends with "Later versions may add keys to these envelopes. Ignore keys you do not know."
- `### Listing runs` (README ~line 501) documents `milestone_id`, `card_id`, `lease` and `progress`, and ends with the additive-keys / ignore-unknown-keys sentence.

So the work is: check those two sections against the code and fix only what is wrong, and write what is missing. The README follows the code on this branch, not the milestone spec, where the two differ. One known difference: the spec says `--dry-run --detach` "is refused", and the code makes it a Typer usage error (exit 2, nothing on stdout), not an exit-3 envelope. The README already says exit 2. Keep that.

## Scope

Edit `README.md` only, plus one new unit test file. No source changes, no `--board` text.

1. **Usage, the streaming exception (README line 47).** This line currently says `am watch --follow` is the one exception to "one line of JSON". Change it to name both `am watch --follow` and `am logs --follow`, linking to their sections.
2. **`am runs` (Listing runs).** Check each key against `RunSummary` in `store.py` and `runs_for` in `cli.py`: `milestone_id` (null on a `--card` run), `card_id` (null on a milestone run), `lease` `{live, pid, host, heartbeat_at, accepting}` or `null` when there is no lease row (same computation as `am status` `control.lease`), and `progress` `{stories:{done,total}, subtasks:{done,total}, current:{card,phase,attempt}|null}`. The ignore-unknown-keys sentence is already there. Change nothing unless the code disagrees.
3. **`am run --detach`.** Check the existing subsection against `detach.py` and the `run` command: pre-flight in the foreground, refusals as exit-3 envelopes, lease taken, then a child in its own session, `<data dir>/runs/<run-id>/run.log` (0600), one envelope `{"ok":true,"data":{"run_id","pid","log","detached":true}}` at exit 0, `report.json` at the end, `am status` summarises it, and non-detached runs unchanged. Change nothing unless the code disagrees.
4. **`am watch --from-now` (Watching a run / Following with --follow).** Update the shape line to `am watch RUN_ID | --all [--since SEQ] [--follow [--from-now]]` and add `--from-now` to the bullet list. In "Following with --follow", say that with `--from-now` the hello line comes first, then only lines appended after the command started (no backlog). Add the two new refusals (exit 3 envelope, before any stream line) to the refusal list: `--from-now` with any `--since` (0 included), and `--from-now` without `--follow`. Add one example command. The hello line stays `schema: 1`.
5. **New `### Reading an attempt's output` section for `am logs`**, placed after "Watching a run" and its subsections, before `## Resuming`. It covers:
   - The shape: `am logs RUN_ID CARD [--phase P] [--attempt N] [--follow] [--since-offset BYTES] [--repo-dir DIR]`. Without `--follow` it is one envelope with the attempt's prompt, result and captured output (the behaviour is unchanged; give a one-line summary only). It reads the projection and takes no lease, claim or lock.
   - `--follow`: the hello line `{"event":"logs","schema":1,"path":...,"offset":B}`, where `path` is the attempt's stdout file (for a deterministic phase, `<phase>.N/stdout.log`) and `offset` is the starting byte. Then `{"offset":B,"text":"..."}` chunks, one JSON object per line, compact and flushed as written. They are contiguous, and each chunk's `offset` is the byte position of its first byte. A file not written yet simply yields nothing until it appears. Once the attempt has a terminal status and the file has stopped growing, the stream ends with `{"event":"end","status":S}` and exit 0. `S` is the attempt status (`ok`, `schema_invalid`, `gate_failed`, `harness_error`). For a deterministic phase it is `ok`, or `gate_failed` for a failed or superseded attempt.
   - `--since-offset B` resumes from byte `B`, the same way `am watch --since` resumes. To resume, pass the last chunk's `offset` plus the UTF-8 byte length of its `text`. Verified against `_follow_logs` / `_utf8_complete_length`: a character split across a read is held back and arrives whole in the next chunk, so for valid UTF-8 this sum is exact. A byte that is not valid UTF-8 is decoded with `errors="replace"` into U+FFFD (3 bytes), so the sum then overcounts by 2 per such byte; the README must say the sum is exact for valid UTF-8 and that the `offset` of the next chunk is always the exact position. Also state that a trailing partial character is emitted as a replacement-decoded chunk just before the `end` line. Leave the rest of the wording to the implementer.
   - Ctrl-C or a closed pipe ends the stream with exit 0 and nothing on stderr. If an error happens after the hello line (for example the run or attempt disappearing from the projection), it is printed as `am logs: <message>` on stderr with exit 3, as for `am watch`.
   - Refusals are the usual `{"ok": false, ...}` envelope with exit 3 before any stream line: an unknown run, card, phase or attempt (as for the one-shot), a negative `--since-offset`, `--since-offset` without `--follow`, and an agent attempt that recorded no stdout path. As with `watch`, only a refusal has an `"ok"` key, and only a stream starts with `"event":"logs"`.
   - The polling interval is internal and not part of the contract.
   - The hello line has its own `schema` (1), independent of the journal's and of `watch`'s.
   - The ignore-unknown-keys sentence, worded like the `am runs` one: a newer `am` may add keys to the hello, chunk and end lines, never removes or renames one, and consumers should ignore keys they do not recognize.

All JSON examples must be valid, compact JSON whose keys exactly match what the code emits. Do not change the journal-line contract, the journal schema (1) or the watch hello schema (1).

## Error paths the README must state

- `watch`: `--from-now` together with `--since` gives exit 3. `--from-now` without `--follow` gives exit 3.
- `logs`: the refusals listed in item 5 give exit 3 before the stream starts. A failure mid-stream goes to stderr with exit 3. Ctrl-C ends with exit 0.
- `run --detach`: already documented (exit-3 pre-flight refusals, and exit 2 usage errors for `--dry-run`/`--board` with `--detach`). Keep it consistent.

## Tests (write first, TDD)

All of these go in a new `tests/test_readme.py`. They read `README.md` from the repo root (`Path(__file__).resolve().parents[1] / "README.md"`) and, where noted, call pure builders in `cli.py`. They spawn no subprocess and touch no git, brd or claude, so per the placement rule (CLAUDE.md "Test tiers", design spec §14) they are **unit (unmarked)**. They must not carry `git`, `e2e_fake` or any other marker, and `tests/test_tier_guards.py` / `tests/test_conftest_tiers.py` must stay green. No README-checking test exists today, and the spec says to add one in that case.

1. `test_usage_names_both_streaming_commands`: the Usage paragraph names both `am watch --follow` and `am logs --follow` as the streaming exceptions. Unit.
2. `test_runs_section_documents_new_keys`: the "Listing runs" section names `milestone_id`, `card_id`, `lease` with `live`, `pid`, `host`, `heartbeat_at` and `accepting`, and `progress` with `stories`, `subtasks` and `current`, and it contains the ignore-unknown-keys sentence. Unit.
3. `test_detach_section_documents_envelope`: the `--detach` subsection's JSON example parses, its `data` keys are exactly `{run_id, pid, log, detached}` with `detached` true, and the section mentions `run.log`, `report.json` and `0600`. Unit.
4. `test_watch_documents_from_now`: the watch shape line contains `--from-now`, and the section states that it is exclusive with `--since` and needs `--follow`. Unit.
5. `test_logs_section_shape_line`: a `logs` section exists with the shape line containing `--follow` and `--since-offset`, and it contains the ignore-unknown-keys sentence. Unit.
6. `test_logs_examples_match_code`: each JSON example line in the logs section parses. The hello example's key set equals `_logs_hello(Path("x"), 0)`'s key set, with `schema == 1`. The chunk example's keys are `{offset, text}`. The end example's keys are `{event, status}` with `event == "end"`. These are pure calls, no subprocess. Unit.

Run them with `uv run pytest tests/test_readme.py`, then the default suite with `uv run pytest`.

---

# 6.1 README: runs fields, --detach, --from-now, logs --follow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `README.md` in line with the code on this branch for `am runs`, `am run --detach`, `am watch --follow --from-now` and `am logs --follow`, pinned by six unit tests in a new `tests/test_readme.py`.

**Architecture:** Docs-only change plus one pure test module. The tests parse `README.md` into heading-bounded sections (fence-aware), assert the contract sentences and shape lines, and compare the `am logs --follow` JSON examples byte-for-byte against the pure builders `cli._logs_hello` and `cli.render`, and the `am runs` keys against the Pydantic models in `store.py`. No source file under `src/` changes.

**Tech Stack:** Python 3, pytest, Pydantic models (`agent_manager.store`), Typer CLI module (`agent_manager.cli`), `uv`.

**Spec:** `docs/superpowers/specs/task-6-1-readme-runs-fields-9f0d5c8c-design.md` (prepended above).

All paths below are relative to the worktree root `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-6-1-readme-runs-fields-9f0d5c8c`. Run every command from that directory.

## Global Constraints

- Edit `README.md` only, plus the new `tests/test_readme.py`. No change under `src/`.
- No `--board` text changes (sibling 5.2 owns it).
- The README follows the code on this branch where the code and the milestone spec differ. `--dry-run --detach` stays documented as a usage error, exit 2.
- JSON keys are additive only. Do not change the journal-line contract, the journal schema (1) or the watch hello schema (1).
- All JSON examples you write must be valid, compact JSON whose keys exactly match what the code emits (`cli.render` output: `separators=(",", ":")`, `sort_keys=True`).
- Every test in `tests/test_readme.py` is unit tier: unmarked, no subprocess, no git/brd/claude, ≤0.5s each. `tests/test_tier_guards.py` and `tests/test_conftest_tiers.py` must stay green.
- No hard-wrapped prose in new README paragraphs (one paragraph per line, matching the surrounding newer sections).
- Verification: `uv run pytest`.

## Review Focus

- Resuming `am logs --follow` after invalid UTF-8: a consumer that computes `offset + len(text.encode())` over a chunk containing U+FFFD overcounts. The README must say the sum is exact only for valid UTF-8 and that the next chunk's `offset` is always exact. Pinned in Task 4 Step 1 (`test_logs_section_shape_line` asserts the U+FFFD and "always exact" sentences).
- `am watch --follow --from-now --since 0`: a user expects `--since 0` to be harmless, but the code refuses any given `--since`. The README must say "0 included". Pinned in Task 3 Step 1.
- A consumer telling an `am logs` refusal from a stream by its first line: only a refusal has `"ok"`, only a stream starts with `"event":"logs"`. Pinned in Task 4 Step 1.
- A broken in-page link: the Usage paragraph links to the new section by anchor; a heading rename would silently break it. Pinned in Task 1 Step 1 (every `(#anchor)` in the paragraph must match a heading slug).
- The chunk examples drifting from contiguity (an example whose second `offset` is not the first `offset` plus the first text's byte length teaches the wrong resume rule). Pinned in Task 4 Step 1 (`test_logs_examples_match_code` walks the offsets).

---

### Task 1: README test harness and the Usage streaming sentence

**Files:**
- Create: `tests/test_readme.py`
- Modify: `README.md:47`

**Interfaces:**
- Consumes: `agent_manager.cli.render(envelope, *, pretty=False) -> str`, `agent_manager.cli._logs_hello(path: Path, offset: int) -> dict`, `agent_manager.detach.RUN_LOG_NAME`, `agent_manager.detach.REPORT_NAME`, `agent_manager.detach.FILE_MODE`, `agent_manager.store.RunSummary`, `agent_manager.store.RunLease`, `agent_manager.store.RunProgress`, `agent_manager.store.ProgressCount`, `agent_manager.store.ProgressCurrent`, `agent_manager.models.AttemptStatus` (all existing on this branch).
- Produces (module-level helpers in `tests/test_readme.py` that Tasks 2-4 use): `README: Path`, `IGNORE_UNKNOWN: str`, `ADDITIVE: str`, `LOGS_TITLE: str`, `_lines() -> list[str]`, `_headings() -> list[tuple[int, int, str]]`, `_section(title: str) -> str`, `_slug(title: str) -> str`, `_fenced_json_lines(section: str) -> list[tuple[str, dict]]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_readme.py` with this content:

```python
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
```

The imports of `typing`, `detach`, `models` and `store` are used by Tasks 2-4. Leave them in now so later tasks only append tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_readme.py::test_usage_names_both_streaming_commands -v`
Expected: FAIL on `assert "`am logs --follow`" in paragraph` (line 47 names only `am watch --follow`).

- [ ] **Step 3: Edit README.md line 47**

Replace this exact line in `README.md`:

```markdown
The one exception is `am watch --follow`, which prints one JSON object per line until stopped (see [Watching a run](#watching-a-run)).
```

with:

```markdown
The two exceptions are the streams. `am watch --follow` prints one JSON object per line until stopped (see [Watching a run](#watching-a-run)), and `am logs --follow` prints one JSON object per line until the attempt it follows is over (see [Reading an attempt's output](#reading-an-attempts-output)).
```

The anchor `#reading-an-attempts-output` will not resolve until Task 4 adds the heading. So this step alone still fails the `dangling anchors` assertion. That is expected. Task 1 is green only once Step 4 below passes, and Step 4 needs the heading. To keep Task 1 self-contained, add the heading stub now, at the place Task 4 fills in. Insert it immediately before the line `## Resuming: what runs again`, with one blank line on each side:

```markdown
### Reading an attempt's output

```

Task 4 writes this section's body under that heading.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_readme.py::test_usage_names_both_streaming_commands -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_readme.py README.md
git commit -m "docs(readme): name am logs --follow as the second stream"
```

---

### Task 2: Pin the existing `am runs` and `--detach` sections against the code

These two sections already exist on this branch and match the code (checked while writing this plan: `RunLease` fields `live, pid, host, heartbeat_at, accepting`; `RunProgress` `stories, subtasks, current`; `ProgressCount` `done, total`; `ProgressCurrent` `card, phase, attempt`; the detached hand-off in `src/agent_manager/cli.py` (the `return` at about line 1413) returns `{"run_id": run_id, "pid": spawned.pid, "log": str(log), "detached": True}`; `detach.RUN_LOG_NAME == "run.log"`, `REPORT_NAME == "report.json"`, `FILE_MODE == 0o600`). The spec says to change them only if the code disagrees. So these tests are expected to pass on first run. They are the spec's "check against the code", made permanent. If either fails, the README is what is wrong: fix the README text to match the code named in the failure. Do not change the code.

**Files:**
- Modify: `tests/test_readme.py` (append)
- Modify (only if Step 2 fails): `README.md` section `### Listing runs` or `#### Running detached with `--detach``

**Interfaces:**
- Consumes: `_section`, `_fenced_json_lines`, `IGNORE_UNKNOWN`, `ADDITIVE` from Task 1; `store.RunSummary`, `store.RunLease`, `store.RunProgress`, `store.ProgressCount`, `store.ProgressCurrent`; `detach.RUN_LOG_NAME`, `detach.REPORT_NAME`, `detach.FILE_MODE`.
- Produces: nothing new.

- [ ] **Step 1: Write the tests**

Append to `tests/test_readme.py`:

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_readme.py::test_runs_section_documents_new_keys tests/test_readme.py::test_detach_section_documents_envelope -v`
Expected: PASS for both. The README on this branch already says `null` on a `--card` run, `null` on a milestone run, `null` if no process has a lease row for it, `` `am status <run-id>` shows in `control.lease` ``, `mode 0600`, `exit code 3`, `exits 0` and `usage error (exit 2)`.

If one fails, the assertion message names the key or phrase. Edit only that phrase in the named README section so that it states what the code does, then re-run Step 2 until both pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_readme.py README.md
git commit -m "test(readme): pin am runs keys and the --detach envelope to the code"
```

(If README.md was not changed, `git add` of it is a no-op.)

---

### Task 3: `am watch --from-now`

**Files:**
- Modify: `tests/test_readme.py` (append)
- Modify: `README.md` section `### Watching a run` and its `#### Following with `--follow`` subsection

**Interfaces:**
- Consumes: `_section` from Task 1.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_readme.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_readme.py::test_watch_documents_from_now -v`
Expected: FAIL on the shape-line assertion (the README still says `[--follow]`).

- [ ] **Step 3: Edit README.md, five replacements in `### Watching a run`**

3a. In the `bash` example block at the top of `### Watching a run`, directly after the line `am watch 20260923T140506Z-19efcddc --follow` and before the block's closing fence, add one line:

```text
am watch --all --follow --from-now
```

3b. Replace the shape line:

```markdown
The shape is `am watch RUN_ID | --all [--since SEQ] [--follow]`:
```

with:

```markdown
The shape is `am watch RUN_ID | --all [--since SEQ] [--follow [--from-now]]`:
```

3c. After the bullet that starts with ``- `--since SEQ` keeps only the lines whose `seq` is greater than `SEQ`.`` (it ends with `It defaults to 0, every line.`), add a new bullet on the next line:

```markdown
- `--from-now` needs `--follow` and skips the backlog: the stream prints only lines appended after the command started. It cannot be combined with `--since`, whatever its value.
```

3d. In the refusal list, replace:

```markdown
- a `--since` below 0;
```

with:

```markdown
- a `--since` below 0;
- `--from-now` together with `--since`, any value, 0 included;
- `--from-now` without `--follow`;
```

3e. In `#### Following with `--follow``, after the paragraph that starts with `` `am` is the version of `am` printing the stream`` (it ends with `--pretty` only indents a refusal's envelope.), insert a blank line and then this paragraph:

```markdown
With `--from-now`, the hello line comes first as always, then no backlog: only lines appended after the command started. A line that was still being written when the command started is printed once it is complete. A run with no complete line yet when the command started, and with `--all` a run that starts later, is printed from its first line. The hello line is the same, `"schema":1`.
```

These statements match `_follow_watch` on this branch: the backlog pass seeds each run's cursor to its highest complete `seq` and yields nothing; a torn last line is not read, so it is emitted once complete; a run with no cursor is emitted from `since`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_readme.py::test_watch_documents_from_now -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_readme.py README.md
git commit -m "docs(readme): document am watch --follow --from-now and its refusals"
```

---

### Task 4: The `am logs` section

**Files:**
- Modify: `tests/test_readme.py` (append)
- Modify: `README.md`, the body under the `### Reading an attempt's output` heading added in Task 1 (just before `## Resuming: what runs again`)

**Interfaces:**
- Consumes: `_section`, `_headings`, `_fenced_json_lines`, `LOGS_TITLE`, `IGNORE_UNKNOWN`, `ADDITIVE` from Task 1; `cli._logs_hello(path: Path, offset: int) -> dict[str, Any]` returning `{"event": "logs", "schema": 1, "path": str(path), "offset": offset}`; `cli.render(obj) -> str`; `models.AttemptStatus` (`Literal["started", "ok", "schema_invalid", "gate_failed", "harness_error"]`).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_readme.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_readme.py::test_logs_section_shape_line tests/test_readme.py::test_logs_examples_match_code -v`
Expected: both FAIL. `test_logs_section_shape_line` fails on the shape-line assertion (the section body is empty), and `test_logs_examples_match_code` fails on `assert len(hellos) == 1` (no examples yet).

- [ ] **Step 3: Write the section body**

In `README.md`, replace the stub added in Task 1:

```markdown
### Reading an attempt's output

## Resuming: what runs again
```

with the following (the last line is the existing `## Resuming` heading, kept as is):

````markdown
### Reading an attempt's output

`am logs` prints what one attempt of one phase of one subtask was given and wrote. Like `am status` and `am runs`, it reads the projection and takes no lease, no claim and no lock, so it works beside any number of live runs.

```bash
am logs 20261002T140000Z-19efcddc <subtask-id> --phase implement
am logs 20261002T140000Z-19efcddc <subtask-id> --phase implement --follow
am logs 20261002T140000Z-19efcddc <subtask-id> --phase implement --follow --since-offset 20
```

The shape is `am logs RUN_ID CARD [--phase P] [--attempt N] [--follow] [--since-offset BYTES] [--repo-dir DIR]`:

- `--phase` defaults to the last phase with attempts, and `--attempt` to that phase's highest recorded attempt.
- Without `--follow`, `am logs` prints one envelope with the attempt's prompt, result and captured output, and exits 0.

#### Following an attempt with `--follow`

`--follow` turns the output into a stream of the attempt's stdout file, one JSON object per line:

```
{"event":"logs","offset":0,"path":"/home/you/.local/share/agent-manager/runs/20261002T140000Z-19efcddc/<subtask-id>/implement.1/stdout.log","schema":1}
{"offset":0,"text":"Reading the plan...\n"}
{"offset":20,"text":"Running uv run pytest\n"}
{"event":"end","status":"ok"}
```

- The first line is the hello line. `path` is the file being followed: the stdout file an agent attempt recorded (its stderr is merged into it), or `<phase>.N/stdout.log` for a deterministic phase such as `verify`. `offset` is the byte the stream starts at: 0, or the `--since-offset` you passed.
- Then come the file's bytes as `{"offset", "text"}` lines: what is already in the file first, then each append, flushed as soon as it is written. Chunks are contiguous, and each chunk's `offset` is the byte position of its first byte in the file. `text` is decoded as UTF-8. A character split across two reads is held back and arrives whole in the next chunk. A byte that is not valid UTF-8 comes out as U+FFFD.
- A file that is not written yet gives no chunk. The stream waits until it appears.
- Once the attempt has a terminal status and the file has stopped growing, the stream ends. A partial character still held back at the very end of the file comes out first, as one last chunk decoded with U+FFFD. Then the last line is `{"event":"end","status":S}`, and the exit code is 0. `S` is the attempt's status: `ok`, `schema_invalid`, `gate_failed` or `harness_error`, the vocabulary of `attempt_upsert` in the [journal](#the-journal-line). A deterministic phase has no attempt status of its own, so `S` is `ok` when the attempt is the phase's latest and the phase is `done`, and `gate_failed` when a later attempt superseded it or the phase failed, escalated, stopped or was cancelled.

To pick up where you left off, pass `--since-offset B`, the way `--since` resumes `am watch`. `B` is the last chunk's `offset` plus the UTF-8 byte length of its `text`. That sum is exact for valid UTF-8. A U+FFFD stands for invalid bytes of the file but is 3 bytes in `text`, so after invalid bytes the sum can overcount. The next chunk's `offset` is always exact, so prefer it when you have one.

Ctrl-C, or the reader closing the pipe, ends the stream with exit code 0 and nothing on stderr. An error after the hello line cannot get an envelope, for example the run, card, phase or attempt no longer being in the projection. `am logs` then prints `am logs: <message>` on stderr and exits 3, as `am watch` does.

These are refused with the usual `{"ok": false, "error": {"type", "message"}}` envelope and exit code 3, before any stream line is written:

- an unknown run, card, phase or attempt, as for the one-shot;
- a `--since-offset` below 0;
- `--since-offset` without `--follow`, whatever its value, 0 included;
- an agent attempt that recorded no stdout path, since there is no file to follow.

So the first line tells a stream from a refusal: only a refusal has an `"ok"` key, and only a stream starts with `"event":"logs"`. Stream lines are always compact: `--pretty` only indents a refusal's envelope.

`am logs --follow` checks the file about every 250 ms. That interval is internal and is not part of the contract.

The hello line's `schema` is the stream's own version, `1` today. It is independent of the journal line's version and of the `am watch` hello line's `schema`, and a change to the chunk or end lines is signaled there.

New keys are additive: a newer `am` may add keys to the hello, chunk and end lines, but never removes or renames one. Consumers should ignore any key they do not recognize.

## Resuming: what runs again
````

Checks on the example block before moving on (the test enforces them too): keys are in sorted order and compact, as `cli.render` prints them. `"Reading the plan...\n"` is 20 bytes, so the second chunk's `offset` is 20. The `--since-offset 20` command example resumes exactly at that second chunk.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_readme.py -v`
Expected: all six tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_readme.py README.md
git commit -m "docs(readme): document am logs --follow, its end line and refusals"
```

---

### Task 5: Full verification

**Files:** none changed.

**Interfaces:** none.

- [ ] **Step 1: Confirm no source file changed**

Run: `git diff --stat ami/task-5-2-document-am-run-4881c933 -- src/`
Expected: no output.

- [ ] **Step 2: Confirm the new tests carry no tier marker**

Run: `uv run pytest tests/test_readme.py -v -m "not git and not brd and not e2e_fake and not soak and not e2e"`
Expected: 6 passed, 0 deselected.

- [ ] **Step 3: Run the default suite**

Run: `uv run pytest`
Expected: PASS, including `tests/test_tier_guards.py` and `tests/test_conftest_tiers.py`.

- [ ] **Step 4: Commit (only if anything changed while fixing Step 3)**

```bash
git add README.md tests/test_readme.py
git commit -m "test(readme): keep the default suite green"
```
