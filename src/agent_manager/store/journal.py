"""A run's JSONL journal: the line envelope, its event kinds, reading a
journal file, and reading it verbatim for `am migrate`. `am` no longer writes
journal files; the files an older `am` wrote are read, never changed."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agent_manager import paths

JOURNAL_NAME = "journal.jsonl"

EventKind = Literal[
    "run_upsert",
    "story_upsert",
    "subtask_upsert",
    "phase_upsert",
    "attempt_upsert",
    "control_requested",
    "control_handled",
    "lease_acquired",
    "lease_taken_over",
    "claim_conflict",
]
"""Every kind of event a run records. The five `*_upsert`s (`NODE_KINDS`) each
record one node of the §9 tree: a status transition is the same node recorded
again with a new status. The other five record lease and control facts in the
`events` table only: no tree reader folds them, and none is ever written to a
journal file."""


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

NODE_KINDS: frozenset[str] = frozenset(
    {"run_upsert", "story_upsert", "subtask_upsert", "phase_upsert", "attempt_upsert"}
)
"""The event kinds that upsert one node of the §9 tree: the only events
`replay` folds. Other kinds may be recorded for a run; no tree reader reads
them."""


class _UnknownEventLine(BaseModel):
    """What a line with an unrecognised `event` must still carry: its `seq`.

    A newer `am` may have journalled an event kind this version's `EventKind`
    does not list. `Journal.read` skips such a line, but only one that still
    carries a positive `seq`: a line with no usable `seq` is an error whatever
    its event. Every other field belongs to a schema this version does not
    know and is ignored.
    """

    model_config = ConfigDict(extra="ignore")

    seq: int = Field(gt=0)


class Journal:
    """A reader of one run's journal file, `<data dir>/runs/<run_id>/journal.jsonl`,
    as an older `am` wrote it.

    Constructing one creates nothing and reads nothing, so a missing, torn or
    corrupt file raises only from `read`.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = paths.data_path() / "runs" / run_id / JOURNAL_NAME

    def _scan(
        self, *, ignore_torn_tail: bool = False
    ) -> list[tuple[int, JournalLine | None]]:
        """Every non-blank line's `seq`, in file order, with its `JournalLine`.

        The `JournalLine` is `None` for a line whose `event` is a string this
        version's `EventKind` does not list: `read` skips it. Such a line must
        still carry a positive `seq`; everything else on it is ignored. Any
        other line is validated strictly as a `JournalLine`, so a missing or
        non-string `event`, a non-object line, or an unknown envelope key on a
        known event still raises.

        Blank lines are skipped: a crash between the write and the flush could
        leave one. Anything else that is not JSON is an error naming the line.

        With `ignore_torn_tail`, a final line that is not JSON and has no
        trailing newline -- an append cut short -- is skipped. A non-JSON line
        that is newline-terminated, or that is not the last, is still
        `CorruptJournalError`. Lines are ASCII (`json.dumps` escapes), so a
        cut can never split a character.
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

        No file raises `MissingJournalError`. A line whose `event` is a string
        outside `EventKind` (written by a newer `am`) is skipped rather than
        raising; see `_scan` for what is still an error and for
        `ignore_torn_tail`. Unknown keys inside a known line's `payload` pass
        through untouched: `replay` judges payloads.
        """
        scanned = self._scan(ignore_torn_tail=ignore_torn_tail)
        lines = [line for _, line in scanned if line is not None]
        lines.sort(key=lambda line: line.seq)
        return lines


_ENVELOPE_KEYS: frozenset[str] = frozenset(JournalLine.model_fields)
"""Every key a journal line may carry: an `events` row has a column for each."""


@dataclass(frozen=True)
class VerbatimLine:
    """One journal line as `read_verbatim` read it: `ts` and `payload` exactly
    as the file holds them, `kind` the line's `event` whatever its value,
    `run_seq` its `seq`, and `line` its 1-based number in the file."""

    line: int
    run_seq: int
    ts: str
    kind: str
    story_id: str | None
    card_id: str | None
    phase: str | None
    attempt: int | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class VerbatimJournal:
    """Every importable line of the journal of `run_id` at `path`, ascending
    by `run_seq`. `torn_line` is the number of the unparseable final line
    that was skipped, `None` when there was none."""

    run_id: str
    path: Path
    lines: tuple[VerbatimLine, ...]
    torn_line: int | None


class UnimportableLineError(JournalError):
    """Line `line` (1-based) of the journal at `path` cannot be imported as it
    is; `why` is the reason, the message without its `path:line: ` prefix."""

    def __init__(self, path: Path, line: int, why: str) -> None:
        super().__init__(f"{path}:{line}: {why}")
        self.path = path
        self.line = line
        self.why = why


def _is_int(value: object) -> bool:
    """A JSON integer: `bool` is an `int` in Python, and is not one here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _verbatim(record: object, run_id: str, number: int) -> VerbatimLine:
    """`record`, decoded from line `number` of `run_id`'s journal, as a
    `VerbatimLine`. Raises `ValueError` naming the first rule it breaks."""
    if not isinstance(record, dict):
        raise ValueError("line is not a JSON object")
    unknown = sorted(set(record) - _ENVELOPE_KEYS)
    if unknown:
        raise ValueError(f"unknown key {unknown[0]!r}")
    for key in ("seq", "ts", "run_id", "event"):
        if key not in record:
            raise ValueError(f"{key} is missing")
    seq, ts, event = record["seq"], record["ts"], record["event"]
    if not _is_int(seq) or seq <= 0:
        raise ValueError(f"seq is {seq!r}, expected a positive integer")
    if not isinstance(ts, str):
        raise ValueError(f"ts is {ts!r}, expected a string")
    try:
        instant = datetime.fromisoformat(ts)
    except ValueError:
        raise ValueError(f"ts {ts!r} is not an ISO-8601 timestamp") from None
    if instant.utcoffset() is None:
        raise ValueError(f"ts {ts!r} has no UTC offset")
    if record["run_id"] != run_id:
        raise ValueError(f"run_id is {record['run_id']!r}, expected {run_id!r}")
    if not isinstance(event, str) or not event:
        raise ValueError(f"event is {event!r}, expected a non-empty string")
    for key in ("story", "card", "phase"):
        value = record.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{key} is {value!r}, expected a string or null")
    attempt = record.get("attempt")
    if attempt is not None and not _is_int(attempt):
        raise ValueError(f"attempt is {attempt!r}, expected an integer or null")
    payload = record.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError(f"payload is a {type(payload).__name__}, expected a JSON object")
    return VerbatimLine(
        line=number,
        run_seq=seq,
        ts=ts,
        kind=event,
        story_id=record.get("story"),
        card_id=record.get("card"),
        phase=record.get("phase"),
        attempt=attempt,
        payload=payload,
    )


def read_verbatim(path: Path, run_id: str) -> VerbatimJournal:
    """The journal of `run_id` at `path` as `am migrate` imports it, every
    value as the file holds it.

    Reads `path` once, as bytes, and creates nothing. Lines are split on
    `\\n`; a blank one is skipped. A final line with no `\\n` that is not
    UTF-8 JSON is a torn tail: skipped, its number kept as `torn_line`. Any
    other line that is not UTF-8 JSON, whose envelope `_verbatim` refuses,
    or whose `seq` an earlier line already used, raises
    `UnimportableLineError`. Any `event` string is kept, known to
    `EventKind` or not. No file at `path` raises `MissingJournalError`; any
    other `OSError` propagates.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError as error:
        raise MissingJournalError(f"no journal for run {run_id!r} at {path}") from error
    segments = data.split(b"\n")
    if segments[-1] == b"":
        segments.pop()
    torn: int | None = None
    lines: list[VerbatimLine] = []
    first_seen: dict[int, int] = {}
    for number, segment in enumerate(segments, start=1):
        if not segment.strip():
            continue
        try:
            record = json.loads(segment.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            if number == len(segments) and not data.endswith(b"\n"):
                torn = number
                continue
            why = (
                "line is not UTF-8"
                if isinstance(error, UnicodeDecodeError)
                else f"line is not JSON: {error}"
            )
            raise UnimportableLineError(path, number, why) from error
        try:
            line = _verbatim(record, run_id, number)
        except ValueError as error:
            raise UnimportableLineError(path, number, str(error)) from error
        if line.run_seq in first_seen:
            raise UnimportableLineError(
                path, number, f"seq {line.run_seq} repeats line {first_seen[line.run_seq]}"
            )
        first_seen[line.run_seq] = number
        lines.append(line)
    lines.sort(key=lambda line: line.run_seq)
    return VerbatimJournal(run_id=run_id, path=path, lines=tuple(lines), torn_line=torn)
