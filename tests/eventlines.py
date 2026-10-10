"""A run's node events read back from `am.db`, where tests used to read the
run's live journal file, and where that file is without creating it.

Importable as `eventlines` because `pyproject.toml` puts `tests/` on
`pythonpath`. Everything here is stdlib `sqlite3` and path arithmetic on the
test's data directory; nothing spawns a process, so callers keep their tier.
"""

import json
from pathlib import Path

from agent_manager import paths
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal


def run_lines(run_id: str) -> list[store_journal.JournalLine]:
    """`store_events.run_lines` of `run_id` on a fresh read-only connection to
    `paths.db_path()`: the run's committed node events, ascending by
    `run_seq`. Usable whether a `Store` of the run is open or closed."""
    conn = store_db.open_reader(paths.db_path())
    try:
        return store_events.run_lines(conn, run_id)
    finally:
        conn.close()


def run_line_texts(run_id: str) -> list[str]:
    """Each of `run_lines(run_id)` as the text of a journal file line:
    `json.dumps` of its JSON-mode dump with sorted keys, no newline."""
    return [
        json.dumps(line.model_dump(mode="json"), sort_keys=True)
        for line in run_lines(run_id)
    ]


def journal_file(run_id: str) -> Path:
    """`<data dir>/runs/<run_id>/journal.jsonl`, where an `am` before 3.1.1
    wrote the run's journal. Creates nothing."""
    return paths.data_path() / "runs" / run_id / store_journal.JOURNAL_NAME
