"""Where `agent_manager.store.writer`'s `Store` lives, what the module may
import, and that no caller reaches `Store` through the `agent_manager.store`
package, which holds no code.

`Store` behaviour is tested in `tests/test_store.py`. Everything here imports
modules or reads source files; nothing spawns a process, so these are unit
tests.
"""

import ast
import inspect
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import (
    bases,
    cli,
    control,
    dispatch,
    integration,
    models,
    orchestrate,
    runs,
    store,
)
from agent_manager.runtime import walk as runtime_walk
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import db as store_db
from agent_manager.store import journal as store_journal
from agent_manager.store import projects as store_projects
from agent_manager.store import writer as store_writer

_REPO = Path(__file__).resolve().parents[2]

_STORE_LEAVES = (
    "db",
    "journal",
    "replay",
    "queries",
    "leases",
    "checkpoints",
    "outbox",
    "projects",
)

_WRITER_MAY_IMPORT = frozenset(
    {"agent_manager.models", *(f"agent_manager.store.{leaf}" for leaf in _STORE_LEAVES)}
)


def test_store_is_defined_in_the_writer_module():
    assert inspect.isclass(store_writer.Store)
    assert store_writer.Store.__module__ == "agent_manager.store.writer"
    assert store_writer._text(None) is None
    assert store_writer._text(Path("/a/b")) == "/a/b"


def test_the_store_package_holds_no_code_and_re_exports_nothing():
    body = ast.parse(Path(store.__file__).read_text()).body
    assert len(body) == 1, [type(node).__name__ for node in body]
    assert isinstance(body[0], ast.Expr)
    assert isinstance(body[0].value, ast.Constant)
    assert isinstance(body[0].value.value, str)
    # `store_writer` is imported above, so the package has loaded it by now.
    assert not hasattr(store, "Store")
    assert not hasattr(store, "_text")
    assert store.writer is store_writer
    names = {name for name in vars(store) if not name.startswith("__")}
    assert names <= {*_STORE_LEAVES, "writer"}, sorted(names)


def _imported_modules(path: Path) -> list[str]:
    """Every module `path` imports, anywhere in its AST, as a dotted name.

    `from agent_manager import models` resolves to `agent_manager.models` and
    `from agent_manager.store import db` to `agent_manager.store.db`. A
    relative import resolves against the `agent_manager.store` package.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = ["agent_manager", "store"][: 3 - node.level]
                module = ".".join([*package, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if module in ("agent_manager", "agent_manager.store"):
                found.extend(f"{module}.{alias.name}" for alias in node.names)
            else:
                found.append(module)
    return found


def test_writer_imports_only_models_and_the_store_leaves():
    imported = _imported_modules(Path(store_writer.__file__))
    ours = {name for name in imported if name.split(".")[0] == "agent_manager"}
    theirs = [
        name
        for name in imported
        if name.split(".")[0] not in (*sys.stdlib_module_names, "agent_manager")
    ]
    assert sorted(ours - _WRITER_MAY_IMPORT) == []
    assert theirs == []


def test_no_store_leaf_imports_the_writer():
    package = Path(store.__file__).parent
    importers = [
        path.name
        for path in sorted(package.glob("*.py"))
        if path.name != "writer.py"
        and "agent_manager.store.writer" in _imported_modules(path)
    ]
    assert importers == []


_THROUGH_THE_PACKAGE = re.compile(
    r"\bstore(_module)?\.Store\b"
    r"|^\s*from agent_manager\.store import (?!writer\b)[^#]*\bStore\b"
)


def _hits(pattern: re.Pattern[str]) -> list[str]:
    """Every line under `src/` and `tests/`, outside this file, that `pattern` matches."""
    me = Path(__file__).resolve()
    return [
        f"{path.relative_to(_REPO)}:{number}: {line.strip()}"
        for root in (_REPO / "src", _REPO / "tests")
        for path in sorted(root.rglob("*.py"))
        if path.resolve() != me and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]


def test_no_caller_reaches_store_through_the_store_package():
    # The opt-in tiers (e2e_fake, soak, e2e) never run in the default suite,
    # so a stale reference there would only fail when someone runs that tier.
    # The import form is anchored at the line start, so the sibling scans'
    # self-check literals, which start with `assert` or a quote, are not hits.
    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import Store")
    assert _THROUGH_THE_PACKAGE.search("    from agent_manager.store import Store")
    # Split across two lines: the outbox and replay scans read this file too,
    # and either literal whole on one line is a hit for them.
    assert _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import "
        "CommentRow, Store"
    )
    assert _THROUGH_THE_PACKAGE.search("st = store.Store.open(root, run_id)")
    assert _THROUGH_THE_PACKAGE.search('monkeypatch.setattr(store_module.Store, "open", spy)')
    assert not _THROUGH_THE_PACKAGE.search("from agent_manager.store.writer import Store")
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import writer as store_writer"
    )
    assert not _THROUGH_THE_PACKAGE.search("st = store_writer.Store.open(root, run_id)")
    assert not _THROUGH_THE_PACKAGE.search("rows = store.latest_checkpoint()")
    assert not _THROUGH_THE_PACKAGE.search(
        '    assert _THROUGH_THE_PACKAGE.search("from agent_manager.store import '
        'Mismatch, Store")'
    )
    assert _hits(_THROUGH_THE_PACKAGE) == []


def test_writer_module_docstring_states_a_contract():
    doc = store_writer.__doc__
    assert doc is not None and doc.strip()
    assert "§" not in doc


def test_every_source_caller_binds_the_one_store_class():
    # A patch on the class (`monkeypatch.setattr(store_writer.Store, ...)`)
    # reaches every caller only because each binds this same class object.
    callers = (bases, cli, control, dispatch, integration, orchestrate, runs, runtime_walk)
    assert [
        module.__name__ for module in callers if module.Store is not store_writer.Store
    ] == []


RUN_A = "run-2026-10-07-01"
RUN_B = "run-2026-10-07-02"


def test_store_open_resolves_the_project_once_per_repo(repo):
    first = store_writer.Store.open(repo, RUN_A)
    second = store_writer.Store.open(repo, RUN_B)
    try:
        ids = (first.project_id, second.project_id)
    finally:
        first.close()
        second.close()

    # A fresh connection sees the row: `Store.open` committed it.
    observer = store_db.open_db(repo)
    try:
        rows = [tuple(row) for row in observer.execute("SELECT id, repo_dir FROM projects")]
    finally:
        observer.close()

    assert ids[0] == ids[1]
    assert rows == [(ids[0], str(repo.resolve()))]


def test_store_open_through_a_symlink_is_the_same_project(repo):
    # Review Focus 1: two spellings of one directory are one project.
    link = repo.parent / "repo-link"
    link.symlink_to(repo, target_is_directory=True)

    direct = store_writer.Store.open(repo, RUN_A)
    linked = store_writer.Store.open(link, RUN_B)
    try:
        assert linked.project_id == direct.project_id
        count = direct.connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    finally:
        direct.close()
        linked.close()

    assert count == 1


def test_store_project_id_is_read_only_and_set_by_the_constructor(repo):
    st = store_writer.Store(store_db.open_db(repo), store_journal.Journal(RUN_A), 7)
    try:
        assert st.project_id == 7
        with pytest.raises(AttributeError):
            st.project_id = 8  # type: ignore[misc]
    finally:
        st.close()


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

_TREE_TABLES = ("runs", "stories", "subtasks", "phases", "attempts")
_STORE_WRITTEN_TABLES = (
    *_TREE_TABLES,
    "checkpoints",
    "checkpoint_floors",
    "run_leases",
    "run_claims",
    "board_comments",
)


def _project_ids(conn, table: str) -> list[int]:
    return [row[0] for row in conn.execute(f"SELECT project_id FROM {table}")]


def _record_tree(st: store_writer.Store, repo: Path) -> None:
    st.record_run(
        models.Run(
            id=RUN_A,
            workflow="milestone",
            repo_dir=repo,
            base_branch="main",
            branch_prefix="m1/",
            status="started",
            started_at=NOW,
        )
    )
    st.record_story(models.StoryRun(card_id="story-a", title="Story", level=0, status="started"))
    st.record_subtask(
        "story-a",
        models.SubtaskRun(
            card_id="card-a", branch="m1/task-card-a", base_branch="main", status="started"
        ),
    )
    st.record_phase(
        "story-a",
        "card-a",
        models.PhaseRun(name="implement", kind="agent", status="started", started_at=NOW),
    )
    st.record_attempt(
        "story-a",
        "card-a",
        "implement",
        models.Attempt(
            n=1,
            dispatch=models.Dispatch(
                harness="claude",
                model="sonnet",
                role="coder",
                cwd=repo,
                prompt_path=repo / "prompt.txt",
                result_path=repo / "result.json",
            ),
        ),
    )


def test_every_row_the_store_writes_carries_its_project_id(repo):
    # Another project is seen first, so this store's id is not the first one
    # a fresh table hands out: a hard-coded or defaulted id cannot pass.
    seed = store_db.open_db(repo)
    try:
        elsewhere = store_projects.resolve(seed, repo.parent / "elsewhere", now=NOW)
        seed.commit()
    finally:
        seed.close()
    st = store_writer.Store.open(repo, RUN_A)
    try:
        _record_tree(st, repo)
        st.save_checkpoint(
            "card-a",
            workflow="task",
            digest="d",
            reason="turn",
            agent={},
            saved_at=NOW,
            floor=store_checkpoints.TurnFloor(phase="implement", loop=0, source_run=RUN_A, floor=0),
        )
        st.enqueue_comment(run_id=RUN_A, card_id="card-a", key="k1", body="b", now=NOW)
        st.take_lease(
            token="t1", pid=1, host="h", now=NOW, is_live=lambda row: False, claims=["card:card-a"]
        )
        written = {table: _project_ids(st.connection, table) for table in _STORE_WRITTEN_TABLES}
        st.rebuild_from_journal(RUN_A)
        rebuilt = {table: _project_ids(st.connection, table) for table in _TREE_TABLES}
        project_id = st.project_id
    finally:
        st.close()

    assert project_id != elsewhere
    assert written == {table: [project_id] for table in _STORE_WRITTEN_TABLES}
    assert rebuilt == {table: [project_id] for table in _TREE_TABLES}
