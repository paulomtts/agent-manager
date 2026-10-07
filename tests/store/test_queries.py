"""Where `agent_manager.store.queries`' names live and what the module may
import: run summaries with their progress, and one run's assembled tree, read
over an open connection.

Query behaviour is tested in `tests/test_store.py`, whose tests build their
inputs through `Store`. Everything here imports modules, reads source files or
opens a SQLite file under tmp_path; nothing spawns a process, so these are
unit tests.
"""

import ast
import inspect
import re
import sys
from pathlib import Path
from typing import get_args

import pytest

from agent_manager import store
from agent_manager.store import queries as store_queries
from agent_manager.store.writer import Store

_REPO = Path(__file__).resolve().parents[2]

_PUBLIC_QUERY_NAMES = (
    "RunLease",
    "ProgressCount",
    "ProgressCurrent",
    "RunProgress",
    "RunSummary",
    "list_runs",
    "latest_run_id",
    "load_run",
    "run_status",
    "run_project_id",
)

_QUERY_NAMES = (
    *_PUBLIC_QUERY_NAMES,
    "_progress_count",
    "_CURRENT_PHASE_SQL",
    "_run_progress",
)


def test_queries_is_a_leaf_module_of_the_store_package():
    for name in _PUBLIC_QUERY_NAMES:
        assert getattr(store_queries, name).__module__ == "agent_manager.store.queries", name
    # `cli.runs_for` builds a `store_queries.RunLease`; `RunSummary.lease` must
    # validate against that very class, not a leftover copy.
    lease = store_queries.RunSummary.model_fields["lease"].annotation
    assert get_args(lease) == (store_queries.RunLease, type(None))


def test_the_store_package_does_not_re_export_query_names():
    assert [name for name in _QUERY_NAMES if hasattr(store, name)] == []
    # Importing the submodule binds it as the package's `queries` attribute.
    assert inspect.ismodule(store.queries)
    assert store.queries is store_queries


def test_queries_imports_only_the_stdlib_pydantic_and_models():
    # The AST, not `sys.modules`: importing `agent_manager.store.queries` always
    # runs the package `__init__` first, so `sys.modules` cannot tell them apart.
    tree = ast.parse(Path(store_queries.__file__).read_text())
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
                    if alias.name != "models"
                )
            elif module.split(".")[0] not in (*sys.stdlib_module_names, "pydantic"):
                outside.append(module)
    assert outside == []


_THROUGH_THE_PACKAGE = re.compile(
    r"\bstore(_module)?\.(RunLease|ProgressCount|ProgressCurrent|RunProgress|RunSummary"
    r"|_progress_count|_CURRENT_PHASE_SQL|_run_progress|list_runs|latest_run_id"
    r"|run_status)\b"
    r"|\bstore_module\.load_run\b"
    r"|\bstore\.load_run\(\s*[^,()]+,"
    r"|from agent_manager\.store import (?!queries\b).*\b(RunLease|ProgressCount"
    r"|ProgressCurrent|RunProgress|RunSummary|list_runs|latest_run_id|load_run"
    r"|run_status)\b"
)


def test_no_caller_reaches_a_query_name_through_the_store_package():
    # The opt-in tiers (e2e_fake, soak, e2e) never run in the default suite,
    # and `--collect-only` does not run test bodies, so a stale call through
    # the package there would only fail when someone runs that tier.
    # `store.load_run(run_id)` with one argument is the `Store` method and is
    # allowed; two or more arguments is the free function.
    assert _THROUGH_THE_PACKAGE.search("run = store.load_run(conn, run_id)")
    assert _THROUGH_THE_PACKAGE.search("summaries = store_module.list_runs(conn)")
    assert _THROUGH_THE_PACKAGE.search("real = store_module.load_run")
    assert _THROUGH_THE_PACKAGE.search("assert set(store.RunSummary.model_fields)")
    assert _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import Store, load_run"
    )
    assert not _THROUGH_THE_PACKAGE.search("run = store.load_run(RUN_ID)")
    assert not _THROUGH_THE_PACKAGE.search("run = store_queries.load_run(conn, x)")
    assert not _THROUGH_THE_PACKAGE.search("current = store.load_run(run.id) or run")
    assert not _THROUGH_THE_PACKAGE.search(
        "from agent_manager.store import queries as store_queries"
    )
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


def test_store_load_run_reads_through_the_queries_module(repo, monkeypatch):
    # `Store.load_run` and `rebuild_from_events` must look `load_run` up on
    # `store_queries` at call time, or a patch of it would never be seen.
    loaded = object()
    seen: list[tuple[object, str]] = []

    def spy(conn, run_id):
        seen.append((conn, run_id))
        return loaded

    monkeypatch.setattr(store_queries, "load_run", spy)
    st = Store.open(repo, "run-2026-10-06-01")
    try:
        assert st.load_run("run-2026-10-06-01") is loaded
        assert seen == [(st.read_connection, "run-2026-10-06-01")]
    finally:
        st.close()


@pytest.mark.parametrize("module", ["cli.py", "orchestrate.py"])
def test_callers_call_load_run_through_the_queries_module(module):
    # `test_cli`'s reset test patches `store_queries.load_run`. A caller that
    # bound `load_run` by name would never see the patch, and that test would
    # still pass.
    tree = ast.parse((_REPO / "src" / "agent_manager" / module).read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load_run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "store_queries"
    ]
    bound = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in ("agent_manager.store", "agent_manager.store.queries")
        for alias in node.names
    ]
    assert calls != []
    assert "load_run" not in bound


def _insert_run(conn, run_id: str, *, project_id: int, started_at: str) -> None:
    conn.execute(
        "INSERT INTO runs (project_id, id, workflow, repo_dir, base_branch,"
        " branch_prefix, status, started_at, config)"
        " VALUES (?, ?, 'milestone', '/repo', 'main', 'm1/', 'started', ?, '{}')",
        (project_id, run_id, started_at),
    )


def test_list_runs_lists_only_the_given_projects_runs(conns, project_id, other_project_id):
    conn, _ = conns
    _insert_run(conn, "run-mine", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    _insert_run(
        conn, "run-theirs", project_id=other_project_id, started_at="2026-10-02T09:00:00+00:00"
    )
    conn.commit()

    assert [s.id for s in store_queries.list_runs(conn, project_id=project_id)] == ["run-mine"]
    assert [s.id for s in store_queries.list_runs(conn, project_id=other_project_id)] == [
        "run-theirs"
    ]
    assert store_queries.list_runs(conn, project_id=None) == []
    # The newer run belongs to the other project: "latest" never crosses over.
    assert store_queries.latest_run_id(conn, project_id=project_id) == "run-mine"
    assert store_queries.latest_run_id(conn, project_id=None) is None


def test_run_project_id_is_the_runs_project_or_none(conns, project_id, other_project_id):
    conn, _ = conns
    _insert_run(conn, "run-mine", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    _insert_run(
        conn, "run-theirs", project_id=other_project_id, started_at="2026-10-02T09:00:00+00:00"
    )
    conn.commit()

    assert store_queries.run_project_id(conn, "run-mine") == project_id
    assert store_queries.run_project_id(conn, "run-theirs") == other_project_id
    assert store_queries.run_project_id(conn, "no-such-run") is None


@pytest.mark.parametrize("name", ["list_runs", "latest_run_id"])
def test_project_id_is_keyword_only_with_no_default(name):
    parameter = inspect.signature(getattr(store_queries, name)).parameters["project_id"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
