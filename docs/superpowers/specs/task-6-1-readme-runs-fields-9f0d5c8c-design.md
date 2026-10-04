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
