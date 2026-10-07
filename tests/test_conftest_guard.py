"""Unit tier: the real-data-dir snapshot in `tests/conftest.py`.

`_snapshot` only walks a `tmp_path` tree (no subprocess), so these tests carry
no tier marker and run in the default suite. This file sits at the top of
`tests/`, so the auto-mark hook leaves it unmarked.
"""

from __future__ import annotations

from pathlib import Path

from conftest import _snapshot


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_snapshot_ignores_the_machine_db_and_its_sidecars_at_the_root(tmp_path):
    for name in ("am.db", "am.db-wal", "am.db-shm"):
        _touch(tmp_path / name)

    assert _snapshot(tmp_path) == frozenset()


def test_snapshot_still_reports_other_new_paths(tmp_path):
    for rel in (
        "am.db.bak",
        "am.db-journal",
        "other",
        "sub/am.db",
        "projects/am.db",
        "projects/x.db",
    ):
        _touch(tmp_path / rel)

    assert _snapshot(tmp_path) == frozenset({
        "am.db.bak",
        "am.db-journal",
        "other",
        "sub",
        "sub/am.db",
        "projects",
        "projects/am.db",
        "projects/x.db",
    })


def test_snapshot_still_excludes_runs_and_is_empty_for_a_missing_root(tmp_path):
    _touch(tmp_path / "runs" / "r" / "x")

    assert "runs/r/x" not in _snapshot(tmp_path)
    assert _snapshot(tmp_path) == frozenset()
    assert _snapshot(tmp_path / "missing") == frozenset()


def test_snapshot_reports_the_contents_of_an_am_db_directory(tmp_path):
    # The exclusion is by exact root-level name: a leak written *inside* a
    # directory that happens to be called am.db is still a leak.
    _touch(tmp_path / "am.db" / "x")

    assert _snapshot(tmp_path) == frozenset({"am.db/x"})
