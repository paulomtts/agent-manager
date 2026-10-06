"""Behaviour of `agent_manager.store.journal`: a run's append-only JSONL journal,
its line envelope and event kinds, appending and reading it.

Real JSONL files under `tmp_path`, and threads in one process; nothing spawns a
process, so these are unit tests. The `repo` fixture redirects `XDG_DATA_HOME`
and `HOME`.
"""

import ast
import json
import re
import sys
import threading
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from agent_manager import paths, store
from agent_manager.store import journal as store_journal

RUN_ID = "run-2026-09-23-01"

_REPO = Path(__file__).resolve().parents[2]

_JOURNAL_NAMES = (
    "Journal",
    "JournalLine",
    "EventKind",
    "JournalError",
    "MissingJournalError",
    "CorruptJournalError",
    "JOURNAL_NAME",
    "_EVENT_KINDS",
    "_UnknownEventLine",
)


def test_journal_is_a_leaf_module_of_the_store_package():
    for cls in (
        store_journal.Journal,
        store_journal.JournalLine,
        store_journal.JournalError,
        store_journal.MissingJournalError,
        store_journal.CorruptJournalError,
    ):
        assert cls.__module__ == "agent_manager.store.journal"
    assert issubclass(store_journal.JournalError, RuntimeError)
    assert issubclass(store_journal.MissingJournalError, store_journal.JournalError)
    assert issubclass(store_journal.CorruptJournalError, store_journal.JournalError)
    assert set(get_args(store_journal.EventKind)) == {
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
        "phase_upsert",
        "attempt_upsert",
    }
    assert store_journal.JOURNAL_NAME == "journal.jsonl"


def test_the_store_package_does_not_re_export_journal_names():
    assert [name for name in _JOURNAL_NAMES if hasattr(store, name)] == []
    # `append` fsyncs through `store_journal.os`. Were `os` still bound on the
    # package, a test patching `store.os.fsync` would patch the shared module
    # and pass while naming the wrong one.
    assert not hasattr(store, "os")


def test_journal_imports_only_the_stdlib_pydantic_and_paths():
    # The AST, not `sys.modules`: importing `agent_manager.store.journal` always
    # runs the package `__init__` first, so `sys.modules` cannot tell them apart.
    tree = ast.parse(Path(store_journal.__file__).read_text())
    outside: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in sys.stdlib_module_names:
                    outside.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                outside.append("." * node.level + module)
            elif module == "agent_manager":
                outside.extend(
                    f"agent_manager.{alias.name}"
                    for alias in node.names
                    if alias.name != "paths"
                )
            elif module.split(".")[0] not in (*sys.stdlib_module_names, "pydantic"):
                outside.append(module)
    assert outside == []


_THROUGH_THE_PACKAGE = re.compile(
    r"store(_module)?\.(Journal|JournalLine|EventKind|JournalError|MissingJournalError"
    r"|CorruptJournalError|JOURNAL_NAME|_EVENT_KINDS|_UnknownEventLine)\b"
    r"|from agent_manager\.store import (?!journal\b).*\b(Journal|JournalLine|EventKind"
    r"|JournalError|MissingJournalError|CorruptJournalError|JOURNAL_NAME)\b"
)


def test_no_caller_reaches_a_journal_name_through_the_store_package():
    # The opt-in tiers (e2e_fake, soak, e2e) never run in the default suite,
    # and `--collect-only` does not run test bodies, so a stale call through
    # the package there would only fail when someone runs that tier.
    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import JournalError, Store")
    assert _THROUGH_THE_PACKAGE.search("lines = store.Journal(run_id).read()")
    assert _THROUGH_THE_PACKAGE.search("except store_module.MissingJournalError:")
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import journal as store_journal"
    )
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store.journal import JournalError"
    )
    assert not _THROUGH_THE_PACKAGE.search("lines = store_journal.Journal(run_id).read()")
    me = Path(__file__).resolve()
    hits = [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for root in (_REPO / "src", _REPO / "tests")
        for path in sorted(root.rglob("*.py"))
        if path.resolve() != me and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _THROUGH_THE_PACKAGE.search(line)
    ]
    assert hits == []


_PRIVATE_JOURNAL_NAME = re.compile(
    r"\bstore_journal\._\w|from agent_manager\.store\.journal import .*\b_\w"
)


def test_no_source_module_reads_a_private_journal_name():
    # `_RETIRED_ATTEMPT_KEYS` and `_current_attempt_payload` stay in the package
    # with their readers; moving them here would make `__init__` read a private
    # name off this module.
    assert _PRIVATE_JOURNAL_NAME.search("store_journal._EVENT_KINDS")
    assert not _PRIVATE_JOURNAL_NAME.search("store_journal.Journal._for_reading(run_id)")
    hits = [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for path in sorted((_REPO / "src").rglob("*.py"))
        if "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _PRIVATE_JOURNAL_NAME.search(line)
    ]
    assert hits == []
