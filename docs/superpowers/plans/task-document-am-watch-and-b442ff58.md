<!-- task-pipeline: validated -->
# Document `am watch` and `JournalLine` as a public v1 contract (b442ff58)

Subtask of 0adbb38b ("am watch exposes the journal as a public, documented stream"), milestone 3b6b4fee. Narrows `docs/superpowers/specs/2026-10-02-am-watch-design.md` sections 3.1, 3.3 and 3.6 to the README. Blocked by cba3e48f (`--follow`), which is done; this card documents what 43f4f076 and cba3e48f actually landed, not the design's plan.

## Scope

Docs only, in this worktree's `README.md` (which already carries the whole am-watch stack: `watch` at `src/agent_manager/cli.py:1718-1753`, tolerant reading in `store.py`). Master's `README.md`/`cli.py` have no `watch` yet; do not work from master.

1. Amend the envelope rule at `README.md:45-46`. Keep the existing sentence and add one stating that `am watch --follow` prints one JSON object per line until stopped (design 3.6: every terminating command prints one line of JSON).
2. Drop `watch` from the `#### Not there yet` bullet at `README.md:410` (currently "`watch` and `retry` do not exist."), since this worktree's `watch` exists; keep the `retry` claim, which is still true. Leaving the stale bullet would contradict the new section.
3. Add a new `### Watching a run` section under `## Usage`. Put it after the `### Milestone runs` subtree and before `## Resuming: what runs again`, as a peer of `### Milestone runs`, because watching is not milestone-specific. It must cover:
   - Command shape: `am watch RUN_ID | --all [--since SEQ] [--follow]`. Exactly one of RUN_ID or `--all`. Without `--follow`, one envelope `{"ok": true, "data": {"events": [...]}}`, ordered by `(run_id, seq)`. `--since SEQ` keeps only lines whose `seq` is greater than SEQ, per run. `--all` reads every run under `<data dir>/runs/`: a run with no journal yet is skipped, and a missing or wrong data directory gives no events, not an error.
   - Refusals: an unknown or invalid run id, both RUN_ID and `--all` (or neither), a negative `--since`, or a corrupt journal each print the `{"ok": false, "error": {...}}` envelope and exit 3. With `--follow`, this happens before any stream line is written, so the first line tells a stream from a refusal. Watching a run id that does not exist creates no run directory.
   - `--follow` stream: the first line is the hello object `{"event": "watch", "schema": 1, "am": "<version>", "runs_dir": "<data dir>/runs"}`. After it comes the backlog above `--since`, then each newly appended line. Every line is a bare `JournalLine` object (no envelope), compact, and flushed as it is written. With `--all`, runs that appear later are picked up. Ctrl-C or a closed pipe ends the stream with exit 0. A journal that turns corrupt mid-stream puts a message on stderr and exits 3. Polling (about 250ms) is internal and not part of the contract.
   - `JournalLine` shape: `seq, ts, run_id, event, story, card, phase, attempt, payload`. Include one example line in the style of design 3.3, and the five `event` kinds with what each covers (`run_upsert`, `story_upsert`, `subtask_upsert`, `phase_upsert`, `attempt_upsert`), condensed from the table in design 3.1. A status change is the same node recorded again with a new status. Run-finished and escalation are derived from `run_upsert` and its terminal `payload.status` (`done`/`escalated`/`stopped`/`cancelled`).
   - Consumer contract, mirroring design 3.3 almost verbatim:
     - Cursor by `(run_id, seq)`, never by time or line count. This cursor survives a lease takeover, which continues the same file at a higher `seq`.
     - Ignore any `event` value or `payload` key you don't recognize.
     - An unterminated final line is a write in flight, not a malformed file. `am watch` skips it and emits it once it is complete.
     - Synthetic ids: story `"integrate"`, story `"bases"`, and subtask `"base-<story id>"`. A run's `repo_dir` and `milestone_id` are in its first (`run_upsert`) line's payload.
     - The hello line's `schema` field is where a future schema bump is signaled.

No code, test or other doc changes. Do not re-describe internals (`_for_reading`, `reseek`, `ignore_torn_tail` by name are optional, not required). Do not document row-only transitions (design 3.5) as events.

## Observable behavior

`README.md` gains the one-sentence envelope-rule addition, the new section, and loses `watch` from the stale `#### Not there yet` bullet. Every claim in the section must match the code in this worktree's `cli.py` (`watch_for`, `_watch_hello`, `_stream_watch`, `watch`), `store.py` (`JournalLine`, `EventKind`), `integration.py:41`, `bases.py:45-57` and `__init__.py`. If the code and the design disagree, the code wins.

## Error paths

None at runtime. The one risk is the documentation drifting from the code, so check each documented flag, exit code and line shape against the files above.

## Tests

None. The card is docs-only by its own description, so there is no test in any tier of design §14 (the test-placement rule in force; the proposed test-tier addendum is not adopted). The gate is that `uv run pytest` still passes unchanged.

---

# Document `am watch` in the README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the already-landed `am watch` command and the `JournalLine` v1 consumer contract in `README.md`, matching the code in this worktree exactly.

**Architecture:** Three edits to one file, `README.md`: one sentence added to the envelope rule, one stale `#### Not there yet` bullet narrowed, and a new `### Watching a run` section inserted between the end of the `### Milestone runs` subtree and `## Resuming: what runs again`. No source or test file changes. Because the card is docs-only, each task's "failing check" is a `grep` against `README.md` that fails before the edit and passes after it, and the regression gate is the unchanged full suite.

**Tech Stack:** Markdown; Python 3 / Pydantic / Typer only as the source of truth being described; `uv run pytest` as the gate.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m16/task-document-am-watch-and-b442ff58/docs/superpowers/specs/task-document-am-watch-and-b442ff58-design.md` (prepended above).

**Worktree:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m16/task-document-am-watch-and-b442ff58`, branch `m16/task-document-am-watch-and-b442ff58`, cut from `m16/task-add-follow-streaming-to-cba3e48f`. Every path below is relative to that worktree. Run every command from that directory.

## Global Constraints

- Docs only: the only file modified is `README.md`. No change under `src/` or `tests/`.
- Test tier rule in force is design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`); this card adds no test in any tier. The proposed test-tier addendum is not adopted.
- If the code and the design disagree, the code wins. The code facts this plan relies on, verified in this worktree:
  - `watch` command: `src/agent_manager/cli.py:1718-1753`. `watch_for` (`cli.py:1553-1587`) refuses with `CliError` unless exactly one of RUN_ID and `--all` is given, and for `--since` below 0; `_check_watch_run_id` (`cli.py:1528-1538`) refuses `""`, `.`, `..` and any id that is not a single path component with `UnknownRunError`; a run id with no journal is `UnknownRunError`.
  - `--all` uses `paths.list_run_ids()` (`paths.py:75-87`): a missing `runs` directory is an empty list; a run with no journal is skipped.
  - Envelope and stream rendering: `render` (`cli.py:176-185`) prints compact JSON with `sort_keys=True`; `--pretty` only applies to envelopes, stream lines go through `_emit_stream_line` (`cli.py:1621-1625`), compact and flushed.
  - Hello line: `_watch_hello` (`cli.py:1610-1618`) returns `{"event": "watch", "schema": 1, "am": __version__, "runs_dir": str(paths.data_dir() / "runs")}`; `__version__` is `"0.1.0"` (`src/agent_manager/__init__.py:1`). Rendered with sorted keys it reads `{"am":"0.1.0","event":"watch","runs_dir":"...","schema":1}`.
  - `--follow`: `watch_for` runs first, so every refusal (including a corrupt journal at start) is an envelope at exit 3 before any stream line. `_stream_watch` (`cli.py:1692-1715`): `KeyboardInterrupt` returns (exit 0), `BrokenPipeError` returns (exit 0), and a `WATCH_HANDLED` error mid-stream writes `am watch: <message>` to stderr and exits 3. Poll interval `WATCH_POLL_SECONDS = 0.25` (`cli.py:1590`), internal.
  - `JournalLine` (`store.py:251-268`): `seq` (> 0), `ts`, `run_id`, `event`, `story`, `card`, `phase`, `attempt`, `payload`. `EventKind` (`store.py:232-234`): `run_upsert`, `story_upsert`, `subtask_upsert`, `phase_upsert`, `attempt_upsert`. Which coordinates each kind sets: `store.py:1168-1220` (`run_upsert` none; `story_upsert` `story`; `subtask_upsert` `story`, `card`; `phase_upsert` `story`, `card`, `phase`; `attempt_upsert` all four, `attempt` is the attempt number). `payload` is the node's own dump without its children.
  - Tolerant reading: `Journal._scan` (`store.py:341-392`) skips a line with an unrecognised `event` string, and with `ignore_torn_tail=True` skips a final non-JSON line with no trailing newline; a non-JSON line that is newline-terminated or not last is `CorruptJournalError`.
  - Synthetic ids: `INTEGRATE_STORY_ID = "integrate"` (`integration.py:41`), `BASES_STORY_ID = "bases"` (`bases.py:45`), subtask `f"base-{story_id}"` (`bases.py:57`).
  - `Run` payload carries `repo_dir` and `milestone_id` (`models.py:157,163`); `milestone_id` is `null` on a `--card` run.
- No hard-wrapped prose in the new README text: one paragraph per line.
- Verification command: `uv run pytest`.

## Review Focus

- A consumer reading the hello line literally: the README must show the hello line as `am` actually prints it (sorted keys, compact), not the design's key order, or a byte-matching consumer breaks. Task 2 Step 1 checks the exact string.
- The `ts` format in the example line: Pydantic serializes UTC datetimes with a `Z` suffix, not `+00:00` as the design's example shows. Task 2 Step 3 prints a real `JournalLine` dump so the example is copied from code, not from the design.
- `--pretty` with `--follow`: a reader may expect indented stream lines. The code indents only envelopes; the section must say stream lines are always compact (Task 2 Step 1 checks the phrase).
- A refusal under `--follow` versus a stream: a consumer that only inspects the first line needs to know that `"ok"` is present only on a refusal and `"event": "watch"` only on a stream. Task 2 Step 1 checks the sentence.
- The stale `#### Not there yet` bullet: leaving "`watch` ... do not exist" contradicts the new section. Task 1 Step 1 checks it is gone.

---

### Task 1: Envelope rule and the stale "Not there yet" bullet

**Files:**
- Modify: `README.md:45-46` (envelope rule)
- Modify: `README.md:410` (`#### Not there yet` bullet)

**Interfaces:**
- Consumes: nothing.
- Produces: a link `[Watching a run](#watching-a-run)` in the envelope rule. Its target heading is created in Task 2 as exactly `### Watching a run`, which GitHub anchors as `#watching-a-run`.

- [ ] **Step 1: Write the failing check**

Run:

```bash
grep -c 'The one exception is `am watch --follow`, which prints one JSON object per line until stopped' README.md; grep -c '^- `retry` does not exist\.$' README.md; grep -c '`watch` and `retry` do not exist' README.md
```

- [ ] **Step 2: Run it to verify it fails**

Expected output, one number per line: `0`, `0`, `1` (the addition is missing, the narrowed bullet is missing, the stale bullet is present).

- [ ] **Step 3: Amend the envelope rule**

In `README.md`, replace exactly:

```markdown
Every command prints one line of JSON — `{"ok": true, "data": ...}` on success,
`{"ok": false, "error": {...}}` on a refusal. Add `--pretty` to indent it.
```

with:

```markdown
Every command prints one line of JSON — `{"ok": true, "data": ...}` on success,
`{"ok": false, "error": {...}}` on a refusal. Add `--pretty` to indent it.
The one exception is `am watch --follow`, which prints one JSON object per line until stopped (see [Watching a run](#watching-a-run)).
```

- [ ] **Step 4: Narrow the stale bullet**

In `README.md`, under the first `#### Not there yet` (the one inside `### Milestone runs`, around line 410), replace exactly:

```markdown
- `watch` and `retry` do not exist.
```

with:

```markdown
- `retry` does not exist.
```

Leave the other two bullets of that list, and the second `#### Not there yet` under `## What the board records`, untouched.

- [ ] **Step 5: Run the check to verify it passes**

Run the same command as Step 1.
Expected output: `1`, `1`, `0`.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: am watch --follow is the one exception to the one-line envelope rule"
```

---

### Task 2: The `### Watching a run` section

**Files:**
- Modify: `README.md`, insert after the line `for everything deferred.` that ends the `### Milestone runs` subtree's `#### Not there yet` (around line 423 after Task 1) and before `## Resuming: what runs again`.

**Interfaces:**
- Consumes: Task 1's link target `#watching-a-run`; the existing README anchor `#several-am-processes` (heading `#### Several am processes`, README.md:199), whose "Locks" paragraph defines `<data dir>`.
- Produces: heading `### Watching a run` (anchor `#watching-a-run`) and its subsections `#### Following with --follow`, `#### The journal line`, `#### Reading the stream safely`.

- [ ] **Step 1: Write the failing check**

Run:

```bash
for s in \
  '### Watching a run' \
  '#### Following with `--follow`' \
  '#### The journal line' \
  '#### Reading the stream safely' \
  '{"am":"0.1.0","event":"watch","runs_dir":"/home/you/.local/share/agent-manager/runs","schema":1}' \
  'Stream lines are always compact' \
  'only a refusal has an `"ok"` key' \
  'Cursor by `(run_id, seq)`, never by time or line count' \
  'Ignore any `event` value, and any `payload` key, you do not recognize' \
  'An unterminated final line is a write in flight, not a malformed file' \
  'Story `"integrate"`' \
  'story `"bases"`' \
  'subtask `"base-<story id>"`' \
  'where a future schema bump is signaled' \
  'Ctrl-C, or the reader closing the pipe, ends the stream with exit code 0' \
  'am watch: <message>' \
; do printf '%s\t%s\n' "$(grep -cF -- "$s" README.md)" "$s"; done
```

(Every pattern is a fixed string, `grep -F`. No other README heading contains these heading strings, so each matches at most one line.)

- [ ] **Step 2: Run it to verify it fails**

Expected: every line starts with `0`.

- [ ] **Step 3: Confirm the example line's exact serialization from the code**

Run:

```bash
uv run python -c '
from datetime import datetime, timezone
from agent_manager.store import JournalLine
from agent_manager.cli import render
line = JournalLine(seq=17, ts=datetime(2026,10,2,14,3,11,412000,tzinfo=timezone.utc), run_id="20261002T140000Z-19efcddc", event="phase_upsert", story="<story-id>", card="<subtask-id>", phase="implement", attempt=None, payload={"detail": None, "ended_at": None, "kind": "agent", "name": "implement", "started_at": "2026-10-02T14:03:11.410000Z", "status": "started"})
print(render(line.model_dump(mode="json")))
'
```

Expected:

```
{"attempt":null,"card":"<subtask-id>","event":"phase_upsert","payload":{"detail":null,"ended_at":null,"kind":"agent","name":"implement","started_at":"2026-10-02T14:03:11.410000Z","status":"started"},"phase":"implement","run_id":"20261002T140000Z-19efcddc","seq":17,"story":"<story-id>","ts":"2026-10-02T14:03:11.412000Z"}
```

If the printed `ts` differs (for example `+00:00` instead of `Z`), use the printed line verbatim in Step 4's example instead of the one shown there, and also change the `payload.started_at` suffix in the example to match. The code wins.

- [ ] **Step 4: Insert the section**

In `README.md`, find this exact text, which ends the `### Milestone runs` subtree:

```markdown
and section 10 of the
[supervisor-tree addendum](docs/superpowers/specs/2026-09-25-supervisor-tree-design.md#10-deferred)
for everything deferred.

## Resuming: what runs again
```

and replace it with:

````markdown
and section 10 of the
[supervisor-tree addendum](docs/superpowers/specs/2026-09-25-supervisor-tree-design.md#10-deferred)
for everything deferred.

### Watching a run

`am watch` prints a run's journal: the append-only log, one JSON object per line, that every run writes to `<data dir>/runs/<run-id>/journal.jsonl` (`<data dir>` is defined under [Several am processes](#several-am-processes)). It takes no lease, no claim and no lock, so it works beside any number of live runs, and since every run on the machine writes under the same `<data dir>/runs/`, one `am watch --all` sees the runs of every repository at once.

```bash
am watch 20260923T140506Z-19efcddc
am watch --all --since 40
am watch 20260923T140506Z-19efcddc --follow
```

The shape is `am watch RUN_ID | --all [--since SEQ] [--follow]`:

- Give exactly one of `RUN_ID` and `--all`.
- `--all` reads every run under `<data dir>/runs/`. A run with no journal yet is skipped. A missing data directory, or a different one (for example under another `XDG_DATA_HOME`), gives no events, not an error.
- `--since SEQ` keeps only the lines whose `seq` is greater than `SEQ`. It filters each run by its own `seq`, so with `--all` the same `SEQ` applies to every run. It defaults to 0, every line.

Without `--follow`, `am watch` prints one envelope and exits 0: `{"ok": true, "data": {"events": [...]}}`. Each event is one [journal line](#the-journal-line), and the list is ordered by `(run_id, seq)`.

These are refused with `{"ok": false, "error": {"type", "message"}}` and exit code 3:

- both `RUN_ID` and `--all`, or neither;
- a `--since` below 0;
- a run id with no journal, or one that is not a single directory name (`.`, `..`, or anything with a `/`), as `UnknownRunError`;
- a corrupt journal: a line that is not JSON (other than a final line still being written, see below), or a line that does not have the journal line's shape.

Watching a run id that does not exist creates no run directory.

#### Following with `--follow`

`--follow` turns the output into a stream. The first line is a hello line, the only line that is not a journal line:

```
{"am":"0.1.0","event":"watch","runs_dir":"/home/you/.local/share/agent-manager/runs","schema":1}
```

`am` is the version of `am` printing the stream, and `runs_dir` is the `<data dir>/runs` it reads. After the hello line comes every journal line above `--since` (the backlog), then each line as it is appended, one JSON object per line, until stopped. Each is a bare journal line with no envelope, flushed as soon as it is written. With `--all`, a run that starts after the stream began is picked up. Stream lines are always compact: `--pretty` only indents a refusal's envelope.

Every refusal listed above, a corrupt journal included, comes as the usual envelope with exit code 3 before any stream line is written. So the first line tells a stream from a refusal: only a refusal has an `"ok"` key, and only a stream starts with `"event": "watch"`.

Ctrl-C, or the reader closing the pipe, ends the stream with exit code 0 and nothing on stderr. A journal that turns corrupt after the stream has started cannot get an envelope, because every line after the hello line must be a journal line: `am watch` prints `am watch: <message>` on stderr and exits 3.

`am watch --follow` checks for new lines about every 250 ms. That interval is internal and is not part of the contract.

#### The journal line

Every event, in the envelope's `events` and on the stream, is one journal line (`JournalLine` in `src/agent_manager/store.py`):

```
{"attempt":null,"card":"<subtask-id>","event":"phase_upsert","payload":{"detail":null,"ended_at":null,"kind":"agent","name":"implement","started_at":"2026-10-02T14:03:11.410000Z","status":"started"},"phase":"implement","run_id":"20261002T140000Z-19efcddc","seq":17,"story":"<story-id>","ts":"2026-10-02T14:03:11.412000Z"}
```

| Field | What it holds |
|---|---|
| `seq` | the line's number in its run's journal, from 1, increasing |
| `ts` | when the line was written, ISO 8601 in UTC |
| `run_id` | the run |
| `event` | which kind of node the line records, one of the five below |
| `story` | the story id; `null` on a `run_upsert` |
| `card` | the subtask id on subtask, phase and attempt lines; otherwise `null` |
| `phase` | the phase name on phase and attempt lines; otherwise `null` |
| `attempt` | the attempt number on an attempt line; otherwise `null` |
| `payload` | the node itself, as the run recorded it, without its children |

Every line records one node of the run's tree. A status change is the same node recorded again with its new status; there is no separate transition event.

| `event` | Covers |
|---|---|
| `run_upsert` | the run starting and finishing: `started`, then `done`, `escalated`, `stopped` or `cancelled` |
| `story_upsert` | a story's own progress: `pending`, `started`, `done`, `stopped`, `escalated` |
| `subtask_upsert` | a subtask's status: `pending`, `started`, `done`, `stopped`, `escalated` (recorded `started` again on a resume) |
| `phase_upsert` | a phase of a subtask: `started`, `done` or `failed`, with `detail` saying why a phase failed |
| `attempt_upsert` | one dispatch of a phase: `started`, then `ok`, `schema_invalid`, `gate_failed` or `harness_error`, with its cost, token and duration fields |

There is no separate "run finished" or "escalation" event. A run has finished when a `run_upsert` line's `payload.status` is `done`, `escalated`, `stopped` or `cancelled`, and it escalated when that status is `escalated`.

#### Reading the stream safely

The journal line is a public contract, version 1. A consumer that follows these rules keeps working across `am` versions:

- Cursor by `(run_id, seq)`, never by time or line count. To pick up where you left off, pass the highest `seq` you have seen as `--since`. The cursor survives a lease takeover: the process that takes a run over keeps appending to the same journal at a higher `seq`.
- Ignore any `event` value, and any `payload` key, you do not recognize. A newer `am` may write either.
- An unterminated final line is a write in flight, not a malformed file. `am watch` skips it, and emits it once it is complete.
- Know the synthetic ids. Story `"integrate"` is [Integrate](#integrate)'s resolver, story `"bases"` holds the [merged-base](#multiple-blockers) resolvers, and under it each resolver is subtask `"base-<story id>"`. A run's `repo_dir` and `milestone_id` (`null` on a `--card` run) are in the `payload` of its first line, a `run_upsert`.
- The hello line's `schema` field is where a future schema bump is signaled. It is `1` today.

## Resuming: what runs again
````

Notes for the implementer:

- If Step 3 printed a different line, the example under `#### The journal line` must be that printed line, not the one above.
- Do not mention control requests, lease liveness or checkpoints as events: they are not journaled (design 3.5).

- [ ] **Step 5: Run the check to verify it passes**

Run the same command as Step 1.
Expected: every line starts with `1`. If Step 3 changed the example's `ts` format, the check list is unaffected (no check matches the `ts` value).

- [ ] **Step 6: Check every in-README link target exists**

Run:

```bash
for a in watching-a-run several-am-processes the-journal-line integrate multiple-blockers; do printf '%s\t' "$a"; grep -ciE "^#+ $(printf '%s' "$a" | sed 's/-/ /g')\$" README.md; done
```

Expected: each anchor prints `1` (`watching-a-run` matches `### Watching a run`, `several-am-processes` matches `#### Several am processes`, `the-journal-line` matches `#### The journal line`, `integrate` matches `#### Integrate`, `multiple-blockers` matches `#### Multiple blockers`).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no failures and no errors. No test was added or removed, so nothing new is collected.

- [ ] **Step 8: Confirm only README.md changed**

Run: `git status --porcelain -- src tests README.md`
Expected: exactly ` M README.md` (no change under `src/` or `tests/`). Untracked spec or plan files under `docs/superpowers/` may also exist in the worktree; they are not this task's to stage, so leave them alone.

- [ ] **Step 9: Commit**

```bash
git add README.md
git commit -m "docs: document am watch and the JournalLine v1 consumer contract"
```
