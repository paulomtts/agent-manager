"""docs/standards/architecture.md contract checks for the single store.

Pure file reading, unit tier, no marker: no subprocess, no git, brd or claude.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_MD = ROOT / "docs" / "standards" / "architecture.md"
PACKAGE = ROOT / "src" / "agent_manager"
CITE = re.compile(r"`([\w./]+\.py):[\d,\-]+`")
SEPARATOR = re.compile(r"\|[\s:|-]+\|")

ORGANIZING = "2. The organizing idea"
TARGET_TABLE = "3.1 The target table"
TODAYS_FILES = "3.2 Today's files on the target order (measured)"
CONFINEMENT = "5. Library and process confinement"
STORE_SPLIT = "6.3 `store.py` into a `store/` package"
ONE_PIECE = "6.4 What stays in one piece"
NEW_CODE = "7. Where new code goes"
FROZEN = "10. Frozen contracts"
SEAM_EXCEPTIONS = "11.4 Re-exports, confinement and test seams"
DECISIONS = "13. Decisions"

PATHS_ROW_FILES = {
    "cli.py",
    "store/journal.py",
    "migrate.py",
    "export.py",
    "store/db.py",
    "paths.py",
}


def _lines() -> list[str]:
    return ARCHITECTURE_MD.read_text(encoding="utf-8").splitlines()


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
    of the same or a higher level."""
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


def _cells(row: str) -> list[str]:
    """The stripped cells of one one-line Markdown table row."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _rows(section: str) -> list[str]:
    """Every table row of `section`, header separators left out."""
    return [
        line
        for line in section.splitlines()
        if line.startswith("|") and not SEPARATOR.fullmatch(line)
    ]


def _row(section: str, first_cell: str) -> str:
    """The one table row of `section` whose first cell is `first_cell`."""
    matches = [row for row in _rows(section) if _cells(row)[0] == first_cell]
    assert len(matches) == 1, f"expected one row {first_cell!r}, found {len(matches)}"
    return matches[0]


def _item(section: str, number: int) -> str:
    """The one-line numbered list item `number.` of `section`."""
    matches = [line for line in section.splitlines() if line.startswith(f"{number}. ")]
    assert len(matches) == 1, f"expected one item {number}, found {len(matches)}"
    return matches[0]


def _cited_files(text: str) -> set[str]:
    """The file of every `file:lines` cite in `text`, e.g. `paths.py:24,44` -> paths.py."""
    return set(CITE.findall(text))


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=\.)\s+", text)


def test_organizing_idea_names_am_db_as_the_truth():
    body = _section(ORGANIZING)
    for phrase in ("`am.db`", "one transaction", "`am export`"):
        assert phrase in body, phrase
    assert "journal is the truth" not in body
    assert "journal line first" not in body


def test_file_count_matches_the_package():
    section = _section(TODAYS_FILES)
    match = re.search(
        r"All (\d+) `\.py` files \((\d+) modules plus (\d+) `__init__\.py`\)", section
    )
    assert match is not None, "§3.2 lost its file-count sentence"
    files = list(PACKAGE.rglob("*.py"))
    inits = [path for path in files if path.name == "__init__.py"]
    assert int(match[1]) == len(files)
    assert int(match[3]) == len(inits)
    assert int(match[2]) == len(files) - len(inits)


def test_store_journal_is_described_as_a_reader():
    layer = _row(_section(TARGET_TABLE), "5")
    module = _row(_section(STORE_SPLIT), "`store/journal.py`")
    for row in (layer, module):
        assert "an older `am` wrote" in row, row
        assert "the journal (schema 1)" not in row, row


def test_paths_row_names_db_path_and_cites_live_files():
    row = _row(_section(CONFINEMENT), "5.11")
    assert "`db_path()`" in row
    cited = _cited_files(row)
    assert cited == PATHS_ROW_FILES
    assert "store.py" not in cited
    for name in cited:
        assert (PACKAGE / name).is_file(), name

    exceptions = [
        row
        for row in _rows(_section(SEAM_EXCEPTIONS))
        if any(cell.endswith("(5.11)") for cell in _cells(row))
    ]
    assert len(exceptions) == 1
    assert _cited_files(exceptions[0]) == cited


def test_store_seam_is_tested_for_one_transaction():
    row = _row(_section(CONFINEMENT), "Store")
    assert "one transaction" in row
    assert "journal-then-row" not in row


def test_store_stays_whole_as_a_writer_queue():
    body = _section(ONE_PIECE)
    for phrase in ("writer thread", "queue", "one transaction", "inert"):
        assert phrase in body, phrase
    assert "journal append" not in body
    assert "store.py:" not in body
    for sentence in _sentences(body):
        if "RLock" in sentence:
            assert "replaces" in sentence, sentence


def test_where_new_code_goes_has_no_journal_first_rule():
    section = _section(NEW_CODE)
    event = _cells(_row(section, "Event field"))
    assert any("transaction" in cell for cell in event[1:]), event
    status = _row(section, "Status value")
    assert 'the README\'s "The journal line" tables' in status
    for row in _rows(section):
        for cell in _cells(row):
            assert "without its journal line" not in cell, cell


def test_event_line_contract_covers_export_and_watch():
    item = _item(_section(FROZEN), 1)
    for phrase in (
        "**The event line**",
        "`store/journal.py:",
        "`gseq`",
        "`am export`",
        "`am events`",
        "`am watch`",
        "`--since-seq`",
        "`--since`",
        "`schema: 2`",
        "`schema: 1`",
    ):
        assert phrase in item, phrase
    assert "store.py:" not in item


def test_write_order_is_one_transaction():
    item = _item(_section(FROZEN), 4)
    assert "one transaction" in item
    assert "lease" in item
    assert "before the row" not in item


def test_data_dir_layout_has_am_db_and_no_live_journal():
    item = _item(_section(FROZEN), 6)
    for phrase in ("am.db", "backups/", "boards/", "`am migrate`"):
        assert phrase in item, phrase
    assert "runs/<run-id>/{journal.jsonl" not in item
    for sentence in _sentences(item):
        if "journal.jsonl" in sentence:
            assert "am migrate" in sentence, sentence


def test_decisions_record_the_single_store():
    rows = [row for row in _rows(_section(DECISIONS)) if re.match(r"\| D\d+ \|", row)]
    ids = [_cells(row)[0] for row in rows]
    assert ids == [f"D{number}" for number in range(1, 21)]
    by_id = {_cells(row)[0]: _cells(row) for row in rows}
    for number in range(15, 21):
        cells = by_id[f"D{number}"]
        assert len(cells) == 4 and all(cells), cells
    assert "`am.db`" in by_id["D15"][2]
    assert "queue" in by_id["D17"][2]
    assert "`gseq`" in by_id["D18"][2]
    assert "checker" in by_id["D19"][2]
    assert "deleted" in by_id["D19"][2] or "removed" in by_id["D19"][2]
    assert "backup API" in by_id["D20"][2]
    assert "writer thread" in by_id["D7"][2] or "queue" in by_id["D7"][2]


def test_no_journal_first_claim_anywhere():
    text = ARCHITECTURE_MD.read_text(encoding="utf-8")
    for phrase in ("journal is the truth", "journal line first", "journal line is written before"):
        assert phrase not in text, phrase
