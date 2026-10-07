"""Behaviour of `agent_manager.store.projects`: the `projects` rows, resolved or
created by repository root, and where the module sits.

Real SQLite files under `tmp_path` through the `repo` and `conns` fixtures of
`tests/store/conftest.py`; nothing spawns a process, so these are unit tests.
"""

import ast
import inspect
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_manager import store
from agent_manager.store import db as store_db
from agent_manager.store import projects as store_projects

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)


def _rows(conn: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(row)
        for row in conn.execute("SELECT id, repo_dir, created_at FROM projects ORDER BY id")
    ]


def test_projects_is_a_leaf_module_of_the_store_package():
    for function in (store_projects.resolve, store_projects.lookup):
        assert function.__module__ == "agent_manager.store.projects"
    assert inspect.ismodule(store.projects)
    assert store.projects is store_projects
    assert not hasattr(store, "resolve")
    assert not hasattr(store, "lookup")


def test_projects_imports_only_the_stdlib_and_store_db():
    outside: list[str] = []
    for node in ast.walk(ast.parse(Path(store_projects.__file__).read_text())):
        if isinstance(node, ast.Import):
            outside.extend(
                alias.name
                for alias in node.names
                if alias.name.split(".")[0] not in sys.stdlib_module_names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                outside.append("." * node.level + module)
            elif module == "agent_manager.store":
                outside.extend(
                    f"agent_manager.store.{alias.name}"
                    for alias in node.names
                    if (alias.name, alias.asname) != ("db", "store_db")
                )
            elif module.split(".")[0] not in sys.stdlib_module_names:
                outside.append(module)
    assert outside == []


_TRANSACTION_CALLS = frozenset({"commit", "rollback", "immediate", "open_db", "connect"})


def test_projects_never_commits_or_opens_a_transaction():
    found: list[str] = []
    for node in ast.walk(ast.parse(Path(store_projects.__file__).read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _TRANSACTION_CALLS
        ):
            found.append(f"line {node.lineno}: .{node.func.attr}()")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "BEGIN" in node.value
        ):
            found.append(f"line {node.lineno}: {node.value!r}")
    assert found == []


def test_resolve_creates_a_row_and_returns_its_id(conns, repo):
    conn, _ = conns

    project_id = store_projects.resolve(conn, repo, now=NOW)

    assert isinstance(project_id, int)
    assert _rows(conn) == [(project_id, str(repo.resolve()), store_db.iso(NOW))]


def test_resolve_is_idempotent(conns, repo):
    conn, _ = conns
    first = store_projects.resolve(conn, repo, now=NOW)
    conn.commit()

    second = store_projects.resolve(conn, repo, now=LATER)

    assert second == first
    assert _rows(conn) == [(first, str(repo.resolve()), store_db.iso(NOW))]


def test_resolve_normalises_the_path(conns, repo):
    conn, _ = conns
    (repo / "sub").mkdir()
    link = repo.parent / "repo-link"
    link.symlink_to(repo, target_is_directory=True)

    project_id = store_projects.resolve(conn, repo, now=NOW)

    assert store_projects.resolve(conn, repo / ".", now=NOW) == project_id
    assert store_projects.resolve(conn, repo / "sub" / "..", now=NOW) == project_id
    assert store_projects.resolve(conn, link, now=NOW) == project_id
    assert len(_rows(conn)) == 1


def test_two_directories_get_two_ids(conns, repo):
    conn, _ = conns
    other = repo.parent / "other-repo"
    other.mkdir()

    mine = store_projects.resolve(conn, repo, now=NOW)
    theirs = store_projects.resolve(conn, other, now=NOW)

    assert mine != theirs
    assert [row[1] for row in _rows(conn)] == [str(repo.resolve()), str(other.resolve())]


def test_resolve_never_commits(conns, repo):
    conn, other = conns

    store_projects.resolve(conn, repo, now=NOW)

    assert conn.in_transaction
    assert _rows(other) == []
    conn.rollback()
    assert _rows(conn) == []
    project_id = store_projects.resolve(conn, repo, now=NOW)
    conn.commit()
    assert _rows(other) == [(project_id, str(repo.resolve()), store_db.iso(NOW))]


def test_lookup_is_read_only(conns, repo):
    conn, _ = conns
    link = repo.parent / "repo-link"
    link.symlink_to(repo, target_is_directory=True)

    assert store_projects.lookup(conn, repo) is None
    assert not conn.in_transaction
    assert _rows(conn) == []
    project_id = store_projects.resolve(conn, repo, now=NOW)
    conn.commit()
    assert store_projects.lookup(conn, repo) == project_id
    assert store_projects.lookup(conn, link) == project_id
    assert not conn.in_transaction


def test_resolve_adopts_a_row_another_connection_inserted_first(conns, repo):
    # Review Focus 2: the insert is ON CONFLICT DO NOTHING, so a row that
    # appeared from another process is adopted, never an IntegrityError.
    conn, other = conns
    other.execute(
        "INSERT INTO projects (repo_dir, created_at) VALUES (?, ?)",
        (str(repo.resolve()), store_db.iso(NOW)),
    )
    other.commit()
    theirs = other.execute("SELECT id FROM projects").fetchone()[0]

    assert store_projects.resolve(conn, repo, now=LATER) == theirs
    conn.commit()
    assert _rows(other) == [(theirs, str(repo.resolve()), store_db.iso(NOW))]


def test_resolve_stores_a_directory_that_does_not_exist(conns, repo):
    conn, _ = conns
    missing = repo.parent / "gone"

    project_id = store_projects.resolve(conn, missing, now=NOW)

    assert store_projects.lookup(conn, missing) == project_id
    assert _rows(conn) == [(project_id, str(missing.resolve()), store_db.iso(NOW))]
