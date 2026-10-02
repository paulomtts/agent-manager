# Subtask 96fc310f: Loosen `Journal.read` and `replay_journal` to skip unrecognized event lines

Parent story: 487ba681 ("Journal reading tolerates event kinds it doesn't recognize"). Source design: `docs/superpowers/specs/2026-10-02-am-watch-design.md` §3.4 and §4. This subtask narrows that section to one change in `src/agent_manager/store.py` plus its tests.

## Scope

In scope:
- `Journal.read` (`store.py:320-354`): a JSON line whose `event` is a string that is not one of the current `EventKind` values (`store.py:232-234`) is skipped instead of raising `ValidationError`.
- `Journal.last_seq` (`store.py:304-308`), which is built on `read()`: it must still count a skipped line's `seq`, so that `append` never reuses a number that is already on disk. The `append` docstring promises "no seq is ever repeated on disk". Without this, an older `am` resuming a run that a newer `am` wrote would break that promise.
  Implementation note: `last_seq`'s current body is `max(line.seq for line in self.read())`, and `read()`'s return value is also handed straight to `replay()` by `replay_journal`/`rebuild_from_journal`, so `read()` must keep returning `list[JournalLine]` containing only the *recognized*-event lines — it cannot also smuggle skipped lines through that same list. Give `read()` and `last_seq()` a shared private helper (e.g. one that yields each line's `seq` together with the parsed `JournalLine` when the event is recognized, or `None` when it is skipped) so `last_seq()` can take the max over every line's `seq` while `read()` keeps returning only the recognized ones. The exact shape is an implementation choice; the constraint is that `read()`'s public signature and return type do not change.
- `replay_journal` (`store.py:1789-1804`) and `rebuild_from_journal` (`store.py:1746+`) need no code of their own. They get the new behaviour because they call `read()` and pass the result to `replay()`.

Out of scope (do not change):
- `JournalLine.model_config` (`extra="forbid"`, `store.py:258`), the `EventKind` Literal, and `Journal.append` (`store.py:356+`). Writing stays strict.
- `replay()` (`store.py:435`) and `models._Model` (`extra="forbid"`). These still raise on unknown keys inside a payload.
- `Journal._for_reading` (`store.py:288-302`) and its rule that it creates no directory.
- `am watch` itself, the documentation of `JournalLine` as a public contract, and am-watch spec §3.5-3.7.

## Observable behavior

- Lines whose `event` is a string outside `EventKind` are dropped from the list `read()` returns. All other lines come back exactly as they do now, validated and sorted by `seq`. For journals that contain only known events, the output is unchanged.
- An unknown-event line is skipped whatever `payload` and the other envelope fields contain, with one exception: `seq` must still parse as the positive integer `JournalLine.seq` already requires, because `last_seq()` depends on recovering it (see above). A line whose `event` is unrecognized but whose `seq` is missing or not a positive integer is not an "unrecognized event kind" for this purpose and still raises `ValidationError`; the only condition that is tolerated by itself is `event` falling outside `EventKind`.
- Unknown keys inside `payload` already pass through `read()`, because `JournalLine.payload` is an untyped `dict[str, Any]`. `read()` leaves them as they are, so §3.4's "ignore unknown `payload` keys" already holds at the reading layer.
- `replay_journal` / `rebuild_from_journal` on a journal that contains an unknown-event line return the same tree they would return if that line were absent.
- `last_seq()` returns the highest `seq` on disk, including the `seq` of any skipped line. The next `append` numbers its line above that.
- `ignore_torn_tail` works as before: a torn final line in another run's journal is still skipped, and an unknown-event line elsewhere in the same file is skipped too.

## Error paths (unchanged; these must still raise)

- A missing journal raises `MissingJournalError`.
- A non-JSON line raises `CorruptJournalError` naming `path:line`, except for a torn tail when `ignore_torn_tail=True`.
- A line with a known `event` but an unknown envelope key (outside `payload`) raises `ValidationError`, as tested by `test_an_envelope_with_an_unknown_key_is_rejected`, `tests/test_store.py:449`. §3.4 relaxes `event` and `payload` only, not the envelope.
- A line with no `event` key, a non-string `event`, or a JSON value that is not an object raises `ValidationError`. None of these is an "unrecognized event kind".
- An unknown payload key on a known event still raises `ValidationError` out of `replay()`, as tested by `test_a_line_with_an_unknown_payload_key_raises_out_of_rebuild`, `tests/test_store.py:875`. The parent story requires the existing store and resume suite to stay green without changes, and that test requires this behavior. The parenthetical in §3.4 about payload keys is therefore satisfied only at the `read()` layer (see above), not inside `replay()`.
- `Journal.append` with an event outside `EventKind` raises `ValidationError`, writes nothing and spends no `seq`, as tested by `tests/test_store.py:407`.

## Tests

All of these go in `tests/test_store.py`, next to the existing journal tests. Per the governing placement rule (agent-manager design §14, `docs/superpowers/specs/2026-09-23-agent-manager-design.md:507-522`), they are pure-module tests against temp JSONL/SQLite files with no `git`, `brd` or subprocess. That puts them in the default unmarked suite. Do not mark them `e2e`. The proposed test-tier addendum is not yet in force, and it would place these tests in `unit` anyway.

1. **`read` skips an unknown event** (default tier, unmarked). Append a `run_upsert`, write a raw line with `event: "future_upsert"` and a valid envelope, then append another `run_upsert`. `read()` returns only the two known lines, without raising.
2. **`replay_journal` skips an unknown event** (default tier, unmarked). Record a full run through `Store`, insert a raw unknown-event line, and check that `replay_journal(RUN_ID)` equals the tree from before the insert. Repeat for another run's journal through the `_for_reading` path with `ignore_torn_tail=True`.
3. **`append` still refuses an unknown event** (default tier, unmarked). After test 1's setup, `append("future_upsert", {})` raises `ValidationError` and the file is unchanged. This may reuse or extend the existing test at `:407`.
4. **`last_seq` counts a skipped line** (default tier, unmarked). After a raw unknown-event line at seq N, the next `append` gets seq N+1, and reopening the `Journal` gives `last_seq() == N+1`.
5. **An unknown event is skipped whatever its other fields contain** (default tier, unmarked). An unknown-event line with an extra envelope key and an arbitrary payload is skipped by `read()` and does not raise.

The existing tests at `tests/test_store.py:407`, `:420`, `:433`, `:449`, `:875` and `:905` must pass unchanged.

## Verification

`uv run pytest` (full suite). There is no lint or typecheck step.
