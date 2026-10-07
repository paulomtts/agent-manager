"""A run's append-only JSONL journal: the line envelope, its event kinds, and
appending and reading the file."""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agent_manager import paths

JOURNAL_NAME = "journal.jsonl"

EventKind = Literal[
    "run_upsert", "story_upsert", "subtask_upsert", "phase_upsert", "attempt_upsert"
]
"""Every event is an upsert of one node of the §9 tree: a status transition is
the same node recorded again with a new status."""


class JournalError(RuntimeError):
    """The journal could not be read as an append-only log of this run."""


class MissingJournalError(JournalError):
    """There is no journal file for this run: a missing run, not an empty one."""


class CorruptJournalError(JournalError):
    """A journal line is not JSON. Names the file and the 1-based line number."""


class JournalLine(BaseModel):
    """The envelope around one journalled event.

    `extra="forbid"` for the same reason `models._Model` uses it: an envelope
    from an older schema must fail loudly rather than lose a coordinate.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(gt=0)
    ts: datetime
    run_id: str = Field(min_length=1)
    event: EventKind
    story: str | None = None
    card: str | None = None
    phase: str | None = None
    attempt: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


_TS: TypeAdapter[datetime] = TypeAdapter(datetime)


def ts_text(ts: datetime) -> str:
    """`ts` as a `JournalLine` writes it in JSON mode, e.g.
    `2026-10-07T05:48:08.123456Z`; a `JournalLine` reads it back to `ts`."""
    return _TS.dump_python(ts, mode="json")


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


class Journal:
    """Append-only JSONL log for one run: the truth the projection is built from.

    The threads of the process that holds a run's lease share one `Journal`.
    The highest sequence number on disk is read when the journal is opened and
    cached; a lock serialises appends from those threads. A process that takes
    the lease over calls `reseek`, because the previous owner may have appended
    after this journal was opened (multi-process X4).
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = paths.run_dir(run_id) / JOURNAL_NAME
        self._lock = threading.Lock()
        self._seq = self.last_seq()

    @classmethod
    def _for_reading(cls, run_id: str) -> "Journal":
        """Another run's journal, opened only to be read.

        `__init__` scans the file for its highest `seq` with the default
        `read()`, which would raise on the very torn tail
        `read(ignore_torn_tail=True)` exists to tolerate, and `paths.run_dir`
        would create a directory for a run that never existed. This instance
        is never appended to, so it needs neither: `_seq` stays 0.
        """
        journal = cls.__new__(cls)
        journal.run_id = run_id
        journal.path = paths.data_path() / "runs" / run_id / JOURNAL_NAME
        journal._lock = threading.Lock()
        journal._seq = 0
        return journal

    def last_seq(self) -> int:
        """Highest sequence number already on disk, or 0 for a fresh journal.

        Counts lines `read` skips for an unrecognised `event` too: they are on
        disk, so `append` must never number a line with one of their `seq`s.
        """
        if not self.path.exists():
            return 0
        return max((seq for seq, _ in self._scan()), default=0)

    def reseek(self) -> None:
        """Re-read the highest `seq` on disk into the cache, under the append lock.

        Called by `Store.take_lease` once the lease is this process's: a stuck
        previous owner may have appended lines after `__init__` cached `_seq`,
        and the new owner must number its first line after them.
        """
        with self._lock:
            self._seq = self.last_seq()

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

        Holds the append lock across the scan, so a line this journal is
        appending is never met half-written. The lock is not re-entrant: never
        call this while holding it.
        """
        with self._lock:
            scanned = self._scan(ignore_torn_tail=ignore_torn_tail)
        lines = [line for _, line in scanned if line is not None]
        lines.sort(key=lambda line: line.seq)
        return lines

    def append(
        self,
        event: EventKind,
        payload: dict[str, Any],
        *,
        story: str | None = None,
        card: str | None = None,
        phase: str | None = None,
        attempt: int | None = None,
    ) -> JournalLine:
        """Append one line, flushed and fsynced before returning.

        Only the process holding the run's lease writes it: after a take-over
        the new owner writes, and the old owner's writes are fenced out by the
        lease token (multi-process X4). The sequence number is cached when the
        journal is opened (and re-read by `reseek` on a take-over), not re-read
        from disk on each append, and the lock is held from numbering the line
        until it is fsynced, so the threads of that process never share a
        number or interleave their bytes. The cached number
        advances once the line has been written and flushed to the file; if
        validation, the open or the write raises, the next append retries the
        same number, and if only the fsync raises the number stays spent, so
        no seq is ever repeated on disk. The lock is released either way.
        """
        with self._lock:
            seq = self._seq + 1
            line = JournalLine(
                seq=seq,
                ts=datetime.now(timezone.utc),
                run_id=self.run_id,
                event=event,
                story=story,
                card=card,
                phase=phase,
                attempt=attempt,
                payload=payload,
            )
            text = json.dumps(line.model_dump(mode="json"), sort_keys=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
                handle.flush()
                # The line is in the file now, fsynced or not: spend its number
                # so a retry after a failed fsync cannot repeat it on disk.
                self._seq = seq
                os.fsync(handle.fileno())
            return line

    def mirror(self, line: JournalLine) -> None:
        """Append `line` exactly as given, flushed and fsynced before returning.

        The bytes are those `append` writes for the same fields. `line.seq`
        is written as given: nothing is numbered and the clock is not read.
        Once the line is written and flushed, the cached highest `seq` becomes
        the larger of itself and `line.seq`, so a later `append` numbers after
        it. Whatever the open, the write, the flush or the fsync raises
        propagates; the lock is released either way.
        """
        text = json.dumps(line.model_dump(mode="json"), sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
                handle.flush()
                self._seq = max(self._seq, line.seq)
                os.fsync(handle.fileno())
