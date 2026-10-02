<!-- task-pipeline: validated -->
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

---

# Loosen Journal Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Journal.read` (and through it `replay_journal` / `rebuild_from_journal`) skips a journal line whose `event` is a string outside `EventKind` instead of raising, while `last_seq` still counts that line's `seq` and `Journal.append` stays strict.

**Architecture:** A private `Journal._scan(*, ignore_torn_tail)` takes over the file-parsing loop that lives in `read()` today and returns `list[tuple[int, JournalLine | None]]`: every non-blank line's `seq`, paired with its validated `JournalLine`, or `None` when the line's `event` is an unrecognised string. Such a line is validated against a small private `_UnknownEventLine` model (only `seq: int = Field(gt=0)`, `extra="ignore"`), so its `seq` is recovered while everything else is ignored. `read()` keeps its signature and returns the non-`None` lines sorted by `seq`; `last_seq()` takes the max over every `seq` that `_scan()` returns.

**Tech Stack:** Python, Pydantic v2, pytest, run with `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-loosen-journal-read-and-96fc310f-design.md` (prepended verbatim above).

## Global Constraints

- Only `src/agent_manager/store.py` and `tests/test_store.py` change.
- `JournalLine.model_config = ConfigDict(extra="forbid")`, the `EventKind` Literal, `Journal.append`, `replay()`, `models._Model` and `Journal._for_reading` are not modified.
- `read(self, *, ignore_torn_tail: bool = False) -> list[JournalLine]`: public signature and return type do not change, and the return value contains only recognised-event lines.
- An unknown event line is tolerated only when `event` is a `str` outside `EventKind` AND `seq` parses as a positive integer; otherwise it still raises `ValidationError`.
- An unknown envelope key on a known event line still raises (`tests/test_store.py:449`); an unknown payload key on a known event still raises out of `replay()` (`tests/test_store.py:875`).
- New tests are unmarked (default suite), in `tests/test_store.py`. No `e2e` / `git` / `brd` marker.
- Existing tests at `tests/test_store.py:407`, `:420`, `:433`, `:449`, `:875`, `:905` pass unchanged; no existing test is edited.
- Verification: `uv run pytest`. No lint or typecheck step exists.

## Review Focus

1. An unknown-event line whose `seq` is missing or not a positive integer (e.g. `0`): a person expects a loud `ValidationError`, not a silent skip, because `last_seq` cannot account for it. Test added in Task 1 (`test_an_unrecognised_event_without_a_valid_seq_still_raises`).
2. A line whose `event` is missing, `null` or a non-string (e.g. `5`), or a JSON value that is not an object (`[]`, `null`): a person expects `ValidationError`, not a skip and not a `TypeError`/`AttributeError` from the new `isinstance` checks. Test added in Task 1 (`test_a_line_without_a_string_event_or_not_an_object_still_raises`).
3. Another run's live journal that has both an unknown-event line and a torn final line: a person expects `read(ignore_torn_tail=True)` to skip both and return the known lines. Test added in Task 1 (`test_ignore_torn_tail_and_an_unrecognised_event_are_both_skipped`).
4. A known event whose payload carries a key this version does not know: a person expects `read()` to hand the payload through untouched (only `replay()` judges payloads). Test added in Task 1 (`test_read_passes_unknown_payload_keys_on_a_known_event_through`).
5. A run resumed through `Store.open` or taken over through `reseek` after a newer `am` wrote an unknown-event line as its last line: a person expects the next record to be numbered above that line, never reusing its `seq`. Tests added in Task 2 (`test_reseek_counts_a_skipped_line`, `test_a_resumed_store_numbers_its_next_record_after_a_skipped_line`).

---

### Task 1: `read()` skips lines whose `event` it does not recognise

**Files:**
- Modify: `src/agent_manager/store.py:23` (import `get_args`)
- Modify: `src/agent_manager/store.py:251-268` (add `_EVENT_KINDS` and `_UnknownEventLine` after `JournalLine`)
- Modify: `src/agent_manager/store.py:320-354` (`Journal.read` split into `_scan` + `read`)
- Test: `tests/test_store.py` (new section inserted immediately before the line `# -- board comment outbox ----------------------------------------------------------`, currently line 3857, so it sits after the adoption-replay section that defines `ADOPTING_RUN_ID`)

**Interfaces:**
- Consumes: existing `store.Journal`, `store.Store`, `store.EventKind`, `store.JOURNAL_NAME`, and test helpers `_append_raw(journal, record)` (`tests/test_store.py:870`), `_record_full_run(st, repo)` (`:657`), `_run(repo, run_id=RUN_ID)` (`:54`), `_story()` (`:483`), `ADOPTING_RUN_ID` (`:3719`).
- Produces: `store._EVENT_KINDS: frozenset[str]`; `store._UnknownEventLine` (Pydantic model, `seq: int = Field(gt=0)`, `extra="ignore"`); `Journal._scan(self, *, ignore_torn_tail: bool = False) -> list[tuple[int, JournalLine | None]]`; test helper `_unrecognised_line(seq: int, run_id: str = RUN_ID, **extra: object) -> dict`. Task 2 uses `Journal._scan` and `_unrecognised_line`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_store.py`, insert this block immediately before the line `# -- board comment outbox ----------------------------------------------------------`:

```python
# -- reading event kinds this version does not recognise (am-watch §3.4) ------
#
# Default suite, unmarked: real temp JSONL/SQLite files, no git, brd or
# subprocess. A newer `am` may journal an event kind this version's `EventKind`
# does not list; reading skips that line, writing stays strict.

UNRECOGNISED_EVENT = "future_upsert"


def _unrecognised_line(seq: int, run_id: str = RUN_ID, **extra: object) -> dict:
    """A well-formed envelope whose `event` this version's `EventKind` lacks."""
    assert UNRECOGNISED_EVENT not in get_args(store.EventKind)
    record: dict = {
        "seq": seq,
        "ts": "2026-10-02T10:00:00+00:00",
        "run_id": run_id,
        "event": UNRECOGNISED_EVENT,
        "payload": {"anything": "at all"},
    }
    record.update(extra)
    return record


def test_read_skips_a_line_whose_event_it_does_not_recognise(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    _append_raw(
        journal,
        {
            "seq": 3,
            "ts": "2026-10-02T10:01:00+00:00",
            "run_id": RUN_ID,
            "event": "run_upsert",
            "payload": {"i": 2},
        },
    )

    lines = journal.read()

    assert [line.seq for line in lines] == [1, 3]
    assert [line.payload["i"] for line in lines] == [0, 2]
    assert all(line.event == "run_upsert" for line in lines)


def test_an_unrecognised_event_is_skipped_whatever_else_the_line_holds(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(
        journal,
        _unrecognised_line(
            2,
            operator="someone",
            ts="not a timestamp",
            story=123,
            attempt="first",
            payload={"nested": [1, {"x": None}], "status": "whatever"},
        ),
    )

    assert [line.seq for line in journal.read()] == [1]


def test_an_unrecognised_event_without_a_valid_seq_still_raises(repo):
    # Review Focus 1: `last_seq` must recover a skipped line's seq, so a line
    # with no usable seq is not tolerated just because its event is unknown.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    without_seq = _unrecognised_line(2)
    del without_seq["seq"]
    _append_raw(journal, without_seq)

    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "seq" in str(excinfo.value)

    journal.path.write_text("", encoding="utf-8")
    _append_raw(journal, _unrecognised_line(0))
    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "seq" in str(excinfo.value)


@pytest.mark.parametrize(
    "raw",
    [
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "payload": {}}',
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "event": null, "payload": {}}',
        '{"seq": 2, "ts": "2026-10-02T10:00:00+00:00", "run_id": "r", "event": 5, "payload": {}}',
        '["future_upsert"]',
        "null",
    ],
    ids=["no-event", "null-event", "int-event", "array", "json-null"],
)
def test_a_line_without_a_string_event_or_not_an_object_still_raises(repo, raw):
    # Review Focus 2: only a *string* event outside EventKind is skipped.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(raw + "\n")

    with pytest.raises(ValidationError):
        journal.read()


def test_read_passes_unknown_payload_keys_on_a_known_event_through(repo):
    # Review Focus 4: §3.4's "ignore unknown payload keys" holds at the read
    # layer because `payload` is an untyped dict; `replay()` still judges it.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(
        journal,
        {
            "seq": 2,
            "ts": "2026-10-02T10:00:00+00:00",
            "run_id": RUN_ID,
            "event": "run_upsert",
            "payload": {"i": 1, "future_key": {"deep": True}},
        },
    )

    assert journal.read()[1].payload == {"i": 1, "future_key": {"deep": True}}


def test_ignore_torn_tail_and_an_unrecognised_event_are_both_skipped(repo):
    # Review Focus 3.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 3, "run_id"')

    assert [line.payload["i"] for line in journal.read(ignore_torn_tail=True)] == [0]
    with pytest.raises(store.CorruptJournalError) as excinfo:
        journal.read()
    assert ":3:" in str(excinfo.value)


def test_append_still_refuses_an_unrecognised_event(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))
    before = journal.path.read_bytes()

    with pytest.raises(ValidationError):
        journal.append(UNRECOGNISED_EVENT, {})  # type: ignore[arg-type]

    assert journal.path.read_bytes() == before
    assert journal._lock.locked() is False


def test_replay_and_rebuild_of_its_own_run_skip_an_unrecognised_event(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        before = st.replay_journal(RUN_ID)
        _append_raw(st.journal, _unrecognised_line(st.journal.last_seq() + 1))
        replayed = st.replay_journal(RUN_ID)
        rebuilt = st.rebuild_from_journal(RUN_ID)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert replayed == before
    assert rebuilt == before
    assert loaded == before


def test_replay_journal_of_another_run_skips_an_unrecognised_event(repo):
    other = store.Store.open(repo, ADOPTING_RUN_ID)
    try:
        other.record_run(_run(repo, ADOPTING_RUN_ID))
        other.record_story(_story())
        next_seq = other.journal.last_seq() + 1
    finally:
        other.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        before = st.replay_journal(ADOPTING_RUN_ID)
        path = paths.run_dir(ADOPTING_RUN_ID) / store.JOURNAL_NAME
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_unrecognised_line(next_seq, ADOPTING_RUN_ID)) + "\n")
            handle.write('{"seq": 99')
        after = st.replay_journal(ADOPTING_RUN_ID)
    finally:
        st.close()

    assert after == before
    assert after.id == ADOPTING_RUN_ID
    assert [story.card_id for story in after.stories] == ["8831189b"]
```

- [ ] **Step 2: Run the new tests to verify the right ones fail**

Run: `uv run pytest tests/test_store.py -v -k "unrecognised or does_not_recognise or unknown_payload_keys_on_a_known or without_a_string_event"`

Expected: FAIL with `pydantic_core._pydantic_core.ValidationError ... Input should be 'run_upsert', 'story_upsert', 'subtask_upsert', 'phase_upsert' or 'attempt_upsert'` for `test_read_skips_a_line_whose_event_it_does_not_recognise`, `test_an_unrecognised_event_is_skipped_whatever_else_the_line_holds`, `test_ignore_torn_tail_and_an_unrecognised_event_are_both_skipped`, `test_replay_and_rebuild_of_its_own_run_skip_an_unrecognised_event` and `test_replay_journal_of_another_run_skips_an_unrecognised_event`. These regression guards already PASS and must keep passing: `test_an_unrecognised_event_without_a_valid_seq_still_raises`, all five `test_a_line_without_a_string_event_or_not_an_object_still_raises[...]`, `test_read_passes_unknown_payload_keys_on_a_known_event_through`, `test_append_still_refuses_an_unrecognised_event`.

- [ ] **Step 3: Import `get_args` in `store.py`**

In `src/agent_manager/store.py` line 23, replace:

```python
from typing import Any, Literal
```

with:

```python
from typing import Any, Literal, get_args
```

- [ ] **Step 4: Add `_EVENT_KINDS` and `_UnknownEventLine` after `JournalLine`**

In `src/agent_manager/store.py`, immediately after the `JournalLine` class (after the line `    payload: dict[str, Any] = Field(default_factory=dict)`, currently line 268) and before `class Journal:`, insert:

```python


_EVENT_KINDS: frozenset[str] = frozenset(get_args(EventKind))


class _UnknownEventLine(BaseModel):
    """What a line with an unrecognised `event` must still carry: its `seq`.

    A newer `am` may journal an event kind this version's `EventKind` does not
    list. `Journal.read` skips such a line, but `last_seq` still counts it, so
    a later `append` never reuses a number already on disk. Every other field
    belongs to a schema this version does not know and is ignored.
    """

    model_config = ConfigDict(extra="ignore")

    seq: int = Field(gt=0)
```

- [ ] **Step 5: Split `Journal.read` into `_scan` and `read`**

In `src/agent_manager/store.py`, replace the whole `read` method (currently lines 320-354, from `    def read(self, *, ignore_torn_tail: bool = False) -> list[JournalLine]:` through `        return lines`) with:

```python
    def _scan(
        self, *, ignore_torn_tail: bool = False
    ) -> list[tuple[int, JournalLine | None]]:
        """Every non-blank line's `seq`, in file order, with its `JournalLine`.

        The `JournalLine` is `None` for a line whose `event` is a string this
        version's `EventKind` does not list: `read` skips it, but `last_seq`
        still counts its `seq`. Such a line must still carry a positive `seq`;
        everything else on it is ignored. Any other line is validated strictly
        as a `JournalLine`, so a missing or non-string `event`, a non-object
        line, or an unknown envelope key on a known event still raises.

        Blank lines are skipped: a crash between the write and the flush can
        leave one. Anything else that is not JSON is an error naming the line.

        `ignore_torn_tail` is for reading *another* run's journal, which a
        process elsewhere may be appending to right now: a final line that is
        not JSON and has no trailing newline is that append in flight, and is
        skipped. A non-JSON line that is newline-terminated, or that is not the
        last, is still `CorruptJournalError`. Lines are ASCII (`json.dumps`
        escapes), so a cut can never split a character.
        """
        if not self.path.exists():
            raise MissingJournalError(
                f"no journal for run {self.run_id!r} at {self.path}"
            )
        with self.path.open(encoding="utf-8") as handle:
            texts = handle.readlines()
        scanned: list[tuple[int, JournalLine | None]] = []
        for number, text in enumerate(texts, start=1):
            if not text.strip():
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as error:
                torn = number == len(texts) and not text.endswith("\n")
                if ignore_torn_tail and torn:
                    continue
                raise CorruptJournalError(
                    f"{self.path}:{number}: line is not JSON: {error}"
                ) from error
            if (
                isinstance(record, dict)
                and isinstance(record.get("event"), str)
                and record["event"] not in _EVENT_KINDS
            ):
                skipped = _UnknownEventLine.model_validate(record)
                scanned.append((skipped.seq, None))
                continue
            line = JournalLine.model_validate(record)
            scanned.append((line.seq, line))
        return scanned

    def read(self, *, ignore_torn_tail: bool = False) -> list[JournalLine]:
        """Every line whose event this version knows, validated, in `seq` order.

        A line whose `event` is a string outside `EventKind` (written by a
        newer `am`) is skipped rather than raising; see `_scan` for what is
        still an error and for `ignore_torn_tail`. Unknown keys inside a known
        line's `payload` pass through untouched: `replay` judges payloads.
        """
        lines = [
            line
            for _, line in self._scan(ignore_torn_tail=ignore_torn_tail)
            if line is not None
        ]
        lines.sort(key=lambda line: line.seq)
        return lines
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v -k "unrecognised or does_not_recognise or unknown_payload_keys_on_a_known or without_a_string_event"`

Expected: PASS (13 test items: 9 functions, one of them parametrised five ways).

- [ ] **Step 7: Run the existing journal tests the spec names, unchanged**

Run: `uv run pytest tests/test_store.py -v -k "an_invalid_line_releases_the_lock or a_non_json_line_names or a_truncated_final_line or an_envelope_with_an_unknown_key or unknown_payload_key_raises_out_of_rebuild or invalid_status_raises_out_of_rebuild or ignore_torn_tail or replay_journal"`

Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no failures.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "Skip journal lines with an unrecognised event kind when reading"
```

---

### Task 2: `last_seq` counts a skipped line's `seq`

**Files:**
- Modify: `src/agent_manager/store.py:304-308` (`Journal.last_seq`)
- Test: `tests/test_store.py` (append to the section added in Task 1, directly after `test_replay_journal_of_another_run_skips_an_unrecognised_event`, before `# -- board comment outbox ...`)

**Interfaces:**
- Consumes: `Journal._scan(self, *, ignore_torn_tail: bool = False) -> list[tuple[int, JournalLine | None]]` and test helper `_unrecognised_line(seq, run_id=RUN_ID, **extra)` from Task 1; existing `Journal.reseek()`, `Store.open(repo, run_id)`, `Store.record_run(run) -> JournalLine`.
- Produces: `Journal.last_seq(self) -> int` returning the highest `seq` on disk including skipped lines (signature unchanged).

- [ ] **Step 1: Write the failing tests**

In `tests/test_store.py`, directly after `test_replay_journal_of_another_run_skips_an_unrecognised_event` (and before `# -- board comment outbox ----------------------------------------------------------`), insert:

```python
def test_a_skipped_line_still_counts_toward_last_seq(repo):
    # `append` promises no seq is ever repeated on disk: an older `am` resuming
    # a newer `am`'s run must number its next line above the skipped one.
    first = store.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    _append_raw(first, _unrecognised_line(2))

    reopened = store.Journal(RUN_ID)
    assert reopened.last_seq() == 2
    assert reopened.append("run_upsert", {"i": 1}).seq == 3

    again = store.Journal(RUN_ID)
    assert again.last_seq() == 3
    assert [line.seq for line in again.read()] == [1, 3]


def test_reseek_counts_a_skipped_line(repo):
    # Review Focus 5: a lease take-over re-reads the highest seq on disk.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    _append_raw(journal, _unrecognised_line(2))

    journal.reseek()

    assert journal.append("run_upsert", {"i": 1}).seq == 3


def test_a_resumed_store_numbers_its_next_record_after_a_skipped_line(repo):
    # Review Focus 5: `Store.open` builds the journal from `last_seq`.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        _append_raw(st.journal, _unrecognised_line(st.journal.last_seq() + 1))
    finally:
        st.close()

    reopened = store.Store.open(repo, RUN_ID)
    try:
        line = reopened.record_run(_run(repo).model_copy(update={"status": "done"}))
    finally:
        reopened.close()

    assert line.seq == 3
    assert [line.seq for line in store.Journal(RUN_ID).read()] == [1, 3]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k "counts_toward_last_seq or reseek_counts_a_skipped_line or numbers_its_next_record_after_a_skipped_line"`

Expected: FAIL. `test_a_skipped_line_still_counts_toward_last_seq` fails with `assert 1 == 2` on `reopened.last_seq()`; `test_reseek_counts_a_skipped_line` fails with `assert 2 == 3`; `test_a_resumed_store_numbers_its_next_record_after_a_skipped_line` fails with `assert 2 == 3` (`last_seq` still takes the max over `read()`, which drops the skipped line).

- [ ] **Step 3: Make `last_seq` take the max over `_scan()`**

In `src/agent_manager/store.py`, replace the `last_seq` method (currently lines 304-308):

```python
    def last_seq(self) -> int:
        """Highest sequence number already on disk, or 0 for a fresh journal."""
        if not self.path.exists():
            return 0
        return max((line.seq for line in self.read()), default=0)
```

with:

```python
    def last_seq(self) -> int:
        """Highest sequence number already on disk, or 0 for a fresh journal.

        Counts lines `read` skips for an unrecognised `event` too: they are on
        disk, so `append` must never number a line with one of their `seq`s.
        """
        if not self.path.exists():
            return 0
        return max((seq for seq, _ in self._scan()), default=0)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v -k "counts_toward_last_seq or reseek_counts_a_skipped_line or numbers_its_next_record_after_a_skipped_line"`

Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no failures. In particular `test_appending_reads_nothing_from_disk_once_the_journal_is_open`, `test_a_reopened_journal_continues_the_sequence`, `test_a_journal_opened_on_out_of_order_lines_continues_after_the_highest`, `test_a_journal_of_only_blank_lines_opens_at_zero` and `test_opening_a_corrupt_journal_raises_at_open` still pass, since `_scan` keeps `read`'s blank-line, corrupt-line and missing-file behaviour.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "Count skipped unrecognised-event lines in Journal.last_seq"
```
