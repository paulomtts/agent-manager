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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args

import pytest

from agent_manager import models, store
from agent_manager.store import queries as store_queries
from agent_manager.store.writer import Store

_REPO = Path(__file__).resolve().parents[2]

_PUBLIC_QUERY_NAMES = (
    "RunLease",
    "ProgressCount",
    "ProgressCurrent",
    "RunProgress",
    "RunProject",
    "RunSummary",
    "AllProjects",
    "RunCursor",
    "ALL_PROJECTS",
    "list_runs",
    "latest_run_id",
    "load_run",
    "run_status",
    "run_project_id",
    "run_known",
    "run_cursor",
)

_QUERY_NAMES = (
    *_PUBLIC_QUERY_NAMES,
    "_progress_count",
    "_CURRENT_PHASE_SQL",
    "_run_progress",
    "_summary",
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
    r"\bstore(_module)?\.(RunLease|ProgressCount|ProgressCurrent|RunProgress|RunProject"
    r"|RunSummary|AllProjects|RunCursor|ALL_PROJECTS|_progress_count|_CURRENT_PHASE_SQL|_run_progress|_summary|list_runs|latest_run_id"
    r"|run_status|run_cursor)\b"
    r"|\bstore_module\.load_run\b"
    r"|\bstore\.load_run\(\s*[^,()]+,"
    r"|from agent_manager\.store import (?!queries\b).*\b(RunLease|ProgressCount"
    r"|ProgressCurrent|RunProgress|RunProject|RunSummary|AllProjects|RunCursor"
    r"|ALL_PROJECTS|list_runs|latest_run_id|load_run|run_status|run_cursor)\b"
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


def _insert_run(
    conn,
    run_id: str,
    *,
    project_id: int,
    started_at: str | None,
    status: str = "started",
) -> None:
    conn.execute(
        "INSERT INTO runs (project_id, id, workflow, repo_dir, base_branch,"
        " branch_prefix, status, started_at, config)"
        " VALUES (?, ?, 'milestone', '/repo', 'main', 'm1/', ?, ?, '{}')",
        (project_id, run_id, status, started_at),
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


def test_run_known_is_true_for_an_events_row_or_a_runs_row(conns, project_id):
    conn, _ = conns
    _insert_run(conn, "run-rows", project_id=project_id, started_at=None)
    conn.execute(
        "INSERT INTO events (project_id, run_id, run_seq, ts, kind, payload, source)"
        " VALUES (?, 'run-events', 1, '2026-10-07T10:00:00+00:00', 'lease_acquired',"
        " '{}', 'live')",
        (project_id,),
    )
    conn.commit()

    assert store_queries.run_known(conn, "run-rows") is True
    assert store_queries.run_known(conn, "run-events") is True
    assert store_queries.run_known(conn, "nope") is False
    assert not conn.in_transaction


@pytest.mark.parametrize("name", ["list_runs", "latest_run_id"])
def test_project_id_is_keyword_only_with_no_default(name):
    parameter = inspect.signature(getattr(store_queries, name)).parameters["project_id"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_list_runs_rows_carry_their_project_beside_their_own_repo_dir(
    conns, repo, project_id
):
    conn, _ = conns
    _insert_run(conn, "run-mine", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    conn.commit()

    [summary] = store_queries.list_runs(conn, project_id=project_id)

    assert summary.project == store_queries.RunProject(id=project_id, repo_dir=repo.resolve())
    # The run's own column, as recorded, is not rewritten to the project's key.
    assert summary.repo_dir == Path("/repo")


def test_list_runs_lists_a_run_with_no_project_row_with_a_null_project(conns, project_id):
    conn, _ = conns
    # Foreign keys are not enforced on these connections, so a dangling
    # `project_id` is how a hand-damaged database looks.
    _insert_run(conn, "run-orphan", project_id=project_id + 1000, started_at=None)
    conn.commit()

    [summary] = store_queries.list_runs(conn, project_id=project_id + 1000)

    assert summary.id == "run-orphan"
    assert summary.project is None


def test_run_summary_project_defaults_to_none():
    field = store_queries.RunSummary.model_fields["project"]
    assert field.default is None
    assert get_args(field.annotation) == (store_queries.RunProject, type(None))
    assert store_queries.RunProject.model_config["extra"] == "forbid"


def _ids(summaries) -> list[str]:
    return [summary.id for summary in summaries]


def test_all_projects_lists_every_projects_runs_in_one_order(
    conns, project_id, other_project_id
):
    conn, _ = conns
    _insert_run(conn, "run-a1", project_id=project_id, started_at="2026-09-01T09:00:00+00:00")
    _insert_run(conn, "run-b2", project_id=other_project_id, started_at="2026-09-02T09:00:00+00:00")
    _insert_run(conn, "run-a3", project_id=project_id, started_at="2026-09-03T09:00:00+00:00")
    conn.commit()

    listed = store_queries.list_runs(conn, project_id=store_queries.ALL_PROJECTS)

    assert _ids(listed) == ["run-a3", "run-b2", "run-a1"]
    assert [summary.project.id for summary in listed] == [
        project_id,
        other_project_id,
        project_id,
    ]
    assert store_queries.list_runs(conn, project_id=None) == []


def test_all_projects_lists_a_run_with_no_project_row_with_a_null_project(
    conns, project_id
):
    conn, _ = conns
    _insert_run(conn, "run-mine", project_id=project_id, started_at="2026-09-02T09:00:00+00:00")
    _insert_run(conn, "run-orphan", project_id=project_id + 1000, started_at="2026-09-01T09:00:00+00:00")
    conn.commit()

    listed = store_queries.list_runs(conn, project_id=store_queries.ALL_PROJECTS)

    assert _ids(listed) == ["run-mine", "run-orphan"]
    assert listed[0].project is not None
    assert listed[1].project is None


def test_all_projects_lists_a_legacy_cancelled_run_as_canceled(
    conns, project_id, other_project_id
):
    conn, _ = conns
    _insert_run(
        conn,
        "run-old",
        project_id=other_project_id,
        started_at="2026-09-01T09:00:00+00:00",
        status=models.LEGACY_CANCELED,
    )
    _insert_run(conn, "run-new", project_id=project_id, started_at="2026-09-02T09:00:00+00:00")
    conn.commit()

    listed = store_queries.list_runs(conn, project_id=store_queries.ALL_PROJECTS, limit=1)
    cursor, _ = store_queries.run_cursor(conn, "run-new")
    rest = store_queries.list_runs(
        conn, project_id=store_queries.ALL_PROJECTS, limit=1, before=cursor
    )

    assert _ids(listed) == ["run-new"]
    assert [(summary.id, summary.status) for summary in rest] == [
        ("run-old", models.CANCELED)
    ]


def test_limit_returns_the_newest_rows(conns, project_id):
    conn, _ = conns
    for day in (1, 2, 3):
        _insert_run(
            conn, f"run-{day}", project_id=project_id, started_at=f"2026-09-0{day}T09:00:00+00:00"
        )
    conn.commit()

    assert _ids(store_queries.list_runs(conn, project_id=project_id, limit=2)) == [
        "run-3",
        "run-2",
    ]
    assert _ids(store_queries.list_runs(conn, project_id=project_id, limit=10)) == [
        "run-3",
        "run-2",
        "run-1",
    ]


def _page_through(conn, scope, limit: int) -> list[str]:
    """Every id `scope` lists, page by page of `limit`, each page starting
    after the last id of the one before, until an empty page."""
    seen: list[str] = []
    before = None
    for _ in range(50):
        page = store_queries.list_runs(conn, project_id=scope, limit=limit, before=before)
        if not page:
            return seen
        assert len(page) <= limit
        seen.extend(_ids(page))
        found = store_queries.run_cursor(conn, page[-1].id)
        assert found is not None
        before = found[0]
    raise AssertionError(f"paging never ended: {seen}")


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_paging_by_run_id_never_skips_or_repeats_a_run(
    conns, project_id, other_project_id, limit
):
    conn, _ = conns
    shared = "2026-10-02T09:00:00+00:00"
    _insert_run(conn, "run-a", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    _insert_run(conn, "run-b", project_id=project_id, started_at=shared)
    _insert_run(conn, "run-c", project_id=other_project_id, started_at=shared)
    _insert_run(conn, "run-d", project_id=project_id, started_at=shared)
    _insert_run(conn, "run-e", project_id=project_id, started_at="2026-10-03T09:00:00+00:00")
    _insert_run(conn, "run-n1", project_id=project_id, started_at=None)
    _insert_run(conn, "run-n2", project_id=other_project_id, started_at=None)
    conn.commit()

    for scope in (project_id, store_queries.ALL_PROJECTS):
        unpaged = _ids(store_queries.list_runs(conn, project_id=scope))
        assert _page_through(conn, scope, limit) == unpaged
    assert _ids(store_queries.list_runs(conn, project_id=store_queries.ALL_PROJECTS)) == [
        "run-e",
        "run-d",
        "run-c",
        "run-b",
        "run-a",
        "run-n2",
        "run-n1",
    ]


def test_a_dated_cursor_keeps_every_null_started_run(conns, project_id):
    conn, _ = conns
    _insert_run(conn, "run-a", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    _insert_run(conn, "run-b", project_id=project_id, started_at="2026-10-02T09:00:00+00:00")
    _insert_run(conn, "run-n", project_id=project_id, started_at=None)
    conn.commit()

    cursor, _ = store_queries.run_cursor(conn, "run-b")

    assert _ids(store_queries.list_runs(conn, project_id=project_id, before=cursor)) == [
        "run-a",
        "run-n",
    ]


def test_a_null_started_cursor_keeps_only_null_started_runs_with_a_smaller_id(
    conns, project_id
):
    conn, _ = conns
    _insert_run(conn, "run-dated", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    for run_id in ("run-n1", "run-n2", "run-n3"):
        _insert_run(conn, run_id, project_id=project_id, started_at=None)
    conn.commit()

    cursor, _ = store_queries.run_cursor(conn, "run-n2")

    assert cursor == store_queries.RunCursor(started_at=None, id="run-n2")
    assert _ids(store_queries.list_runs(conn, project_id=project_id, before=cursor)) == [
        "run-n1"
    ]


def test_the_last_row_as_cursor_is_an_empty_page(conns, project_id):
    conn, _ = conns
    _insert_run(conn, "run-a", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    conn.commit()

    cursor, _ = store_queries.run_cursor(conn, "run-a")

    assert store_queries.list_runs(conn, project_id=project_id, limit=5, before=cursor) == []


def _insert_instants(conn, project_id: int) -> None:
    _insert_run(conn, "run-early", project_id=project_id, started_at="2026-10-01T08:59:59+00:00")
    _insert_run(conn, "run-exact", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    _insert_run(
        conn, "run-half", project_id=project_id, started_at="2026-10-01T09:00:00.500000+00:00"
    )
    _insert_run(conn, "run-null", project_id=project_id, started_at=None)
    conn.commit()


@pytest.mark.parametrize(
    "before",
    [
        datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 1, 11, 0, tzinfo=timezone(timedelta(hours=2))),
        datetime(2026, 10, 1, 9, 0),
        datetime.fromisoformat("2026-10-01T09:00:00Z"),
    ],
    ids=["utc", "offset", "naive", "z"],
)
def test_before_an_instant_is_strict_and_compared_in_utc(conns, project_id, before):
    conn, _ = conns
    _insert_instants(conn, project_id)

    assert _ids(store_queries.list_runs(conn, project_id=project_id, before=before)) == [
        "run-early"
    ]


def test_before_an_instant_counts_fractional_seconds_and_drops_null_starts(
    conns, project_id
):
    conn, _ = conns
    _insert_instants(conn, project_id)
    half = datetime(2026, 10, 1, 9, 0, 0, 500000, tzinfo=timezone.utc)
    later = datetime(2026, 10, 2, tzinfo=timezone.utc)

    assert _ids(store_queries.list_runs(conn, project_id=project_id, before=half)) == [
        "run-exact",
        "run-early",
    ]
    assert _ids(store_queries.list_runs(conn, project_id=project_id, before=later)) == [
        "run-half",
        "run-exact",
        "run-early",
    ]


def test_list_runs_pages_fractional_seconds_in_instant_order(conns, project_id):
    """Review Focus 1: `started_at` compares as text, and `'+'` sorts below
    `'.'`, so `09:00:00+00:00` stays before `09:00:00.500000+00:00`."""
    conn, _ = conns
    _insert_instants(conn, project_id)

    assert _ids(store_queries.list_runs(conn, project_id=project_id)) == [
        "run-half",
        "run-exact",
        "run-early",
        "run-null",
    ]
    assert _page_through(conn, project_id, 1) == [
        "run-half",
        "run-exact",
        "run-early",
        "run-null",
    ]


def test_run_cursor_is_the_stored_started_at_id_and_project(
    conns, project_id, other_project_id
):
    conn, _ = conns
    _insert_run(conn, "run-mine", project_id=project_id, started_at="2026-10-01T09:00:00+00:00")
    _insert_run(conn, "run-theirs", project_id=other_project_id, started_at=None)
    conn.commit()

    assert store_queries.run_cursor(conn, "run-mine") == (
        store_queries.RunCursor(started_at="2026-10-01T09:00:00+00:00", id="run-mine"),
        project_id,
    )
    assert store_queries.run_cursor(conn, "run-theirs") == (
        store_queries.RunCursor(started_at=None, id="run-theirs"),
        other_project_id,
    )
    assert store_queries.run_cursor(conn, "no-such-run") is None


@pytest.mark.parametrize("name", ["limit", "before"])
def test_list_runs_paging_parameters_are_keyword_only_and_default_to_none(name):
    parameter = inspect.signature(store_queries.list_runs).parameters[name]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


def test_all_projects_is_the_one_sentinel_instance():
    assert isinstance(store_queries.ALL_PROJECTS, store_queries.AllProjects)
    assert store_queries.AllProjects() == store_queries.ALL_PROJECTS
