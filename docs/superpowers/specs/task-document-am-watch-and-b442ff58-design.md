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
