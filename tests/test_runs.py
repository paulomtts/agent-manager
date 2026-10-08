"""Unit tests for `runs`, the Typer-free home of the run helpers (S1, card 46244d0e).

Pure functions only (design §14): no clock beyond a fixed `datetime`, no
filesystem beyond `tmp_path`, no subprocess except the one fresh-interpreter
import check.
"""

import ast
import inspect
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import census, dag, models, runs

CARD_ID = "46244d0e-1111-2222-3333-444455556666"


def test_run_id_time_format_and_worktree_parts_keep_their_values():
    assert runs.RUN_ID_TIME_FORMAT == "%Y%m%dT%H%M%SZ"
    assert runs.WORKTREE_PARTS == (".claude", "worktrees")


def test_repo_dir_error_is_a_cli_error_is_a_runtime_error():
    assert issubclass(runs.RepoDirError, runs.CliError)
    assert issubclass(runs.CliError, RuntimeError)


def test_resolve_repo_dir_returns_the_absolute_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "repo").mkdir()
    resolved = runs.resolve_repo_dir(Path("repo"))
    assert resolved == (tmp_path / "repo").resolve()
    assert resolved.is_absolute()


def test_resolve_repo_dir_refuses_a_missing_directory(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(runs.RepoDirError) as excinfo:
        runs.resolve_repo_dir(missing)
    assert str(excinfo.value) == (
        f"--repo-dir {str(missing)!r} is not a directory (resolved to {missing.resolve()})"
    )


def test_resolve_repo_dir_refuses_a_file(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")
    with pytest.raises(runs.RepoDirError):
        runs.resolve_repo_dir(a_file)


def test_mint_run_id_is_utc_timestamp_dash_short_id():
    now = datetime(2026, 9, 30, 12, 34, 56, tzinfo=timezone.utc)
    assert runs.mint_run_id(CARD_ID, now) == "20260930T123456Z-46244d0e"


def test_mint_run_id_refuses_a_non_uuid_card_id():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        runs.mint_run_id("not-a-card", now)


def test_worktree_for_splits_the_branch_into_path_segments(tmp_path):
    path = runs.worktree_for(tmp_path, "m13/task-x")
    assert path == tmp_path.resolve() / ".claude" / "worktrees" / "m13" / "task-x"
    assert path.is_absolute()


def test_gate_context_shape():
    assert runs.gate_context(["uv run pytest"], False) == {
        "suite_cmds": ["uv run pytest"],
        "allow_no_verification": False,
        "caller_provided": False,
        "provided_verification": None,
    }


def test_gate_context_coerces_allow_no_verification_to_bool():
    ctx = runs.gate_context(("a", "b"), 1)
    assert ctx["allow_no_verification"] is True
    assert ctx["suite_cmds"] == ["a", "b"]


def test_runner_factory_is_a_keyword_only_protocol():
    params = inspect.signature(runs.RunnerFactory.__call__).parameters
    assert list(params) == ["self", "store", "run_id", "story_id", "card_id"]
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY
        for name, p in params.items()
        if name != "self"
    )


def test_runs_source_imports_neither_typer_nor_cli_nor_launcher():
    tree = ast.parse(Path(runs.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    forbidden = {"typer", "agent_manager.cli", "agent_manager.harness.launcher"}
    assert not {name for name in imported if name.split(".")[0] == "typer"}
    assert not imported & forbidden


def test_importing_runs_loads_neither_typer_nor_cli():
    code = (
        "import sys, agent_manager.runs; "
        "assert 'typer' not in sys.modules, 'typer'; "
        "assert 'agent_manager.cli' not in sys.modules, 'cli'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


MOVED_NAMES = (
    "RUN_ID_TIME_FORMAT",
    "WORKTREE_PARTS",
    "CliError",
    "RepoDirError",
    "resolve_repo_dir",
    "mint_run_id",
    "worktree_for",
    "RunnerFactory",
    "gate_context",
    "UnknownRunError",
    "unknown_run",
    "NotResumableError",
    "CheckpointMismatchError",
    "select_resumable",
    "orphan_attempts",
    "continuable_checkpoint",
    "DryRunPlan",
    "compute_dry_run_plan",
)


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_cli_re_exports_the_moved_name_as_the_same_object(name):
    from agent_manager import cli

    assert getattr(cli, name) is getattr(runs, name)


def test_unknown_run_names_the_run_and_the_listing_command():
    error = runs.unknown_run("nope")

    assert isinstance(error, runs.UnknownRunError)
    assert str(error) == (
        "run 'nope' is not in the projection"
        " (`agent-manager runs --all-projects` lists the ones that are)"
    )


def test_cli_error_subclasses_share_the_moved_base():
    from agent_manager import cli

    assert issubclass(cli.UnknownRunError, runs.CliError)
    assert issubclass(cli.CheckpointMismatchError, runs.CliError)
    assert runs.CliError in cli.HANDLED


def test_default_runner_factory_stays_in_cli():
    from agent_manager import cli

    assert cli.default_runner_factory.__module__ == "agent_manager.cli"
    assert not hasattr(runs, "default_runner_factory")
    assert not hasattr(runs, "run_direct")


def test_resume_error_types_are_defined_in_runs():
    from agent_manager.runtime import engine as runtime_engine

    for error_type in (
        runs.UnknownRunError,
        runs.NotResumableError,
        runs.CheckpointMismatchError,
    ):
        assert error_type.__module__ == "agent_manager.runs"
        assert issubclass(error_type, runs.CliError)
    assert issubclass(runs.CheckpointMismatchError, runtime_engine.CheckpointMismatch)


def _plan_id(n: int) -> str:
    """A UUID-shaped card id whose short id is `n` in eight hex digits
    (`dag.short_id` refuses anything that is not 32 hex characters)."""
    return f"{n:08x}-0000-4000-8000-000000000000"


def _plan_subtask(n: int, status: str = "todo") -> census.SubtaskPlan:
    return census.SubtaskPlan(id=_plan_id(n), title=f"subtask {n}", status=status)


def _plan_story(
    n: int,
    subtasks: list[census.SubtaskPlan],
    *,
    status: str = "todo",
    blocked_by: tuple[str, ...] | list[str] = (),
) -> census.StoryPlan:
    return census.StoryPlan(
        id=_plan_id(n),
        title=f"story {n}",
        status=status,
        blocked_by=list(blocked_by),
        subtasks=list(subtasks),
    )


PLAN_REPO = Path("/repo")
"""`worktree_for` only joins onto the repo dir, so it need not exist."""


def _plan(stories, max_concurrent: int = 4) -> "runs.DryRunPlan":
    return runs.compute_dry_run_plan(
        stories,
        repo_dir=PLAN_REPO,
        branch_prefix="m3",
        base_branch="main",
        max_concurrent=max_concurrent,
    )


def test_dry_run_plan_is_a_plain_dataclass_of_levels_then_integrate():
    import dataclasses

    assert dataclasses.is_dataclass(runs.DryRunPlan)
    assert [field.name for field in dataclasses.fields(runs.DryRunPlan)] == [
        "levels",
        "integrate",
    ]
    assert runs.compute_dry_run_plan.__module__ == "agent_manager.runs"


def test_compute_dry_run_plan_computes_levels_bases_roots_and_the_integrate_plan():
    """Remaining subtasks only, bases from the full ordered list, a dependent
    rooted on its blocker's tip, and Integrate over every story with a tip."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    def branch(subtask: census.SubtaskPlan) -> str:
        return dag.subtask_branch("m3", subtask)

    assert _plan([a, b]) == runs.DryRunPlan(
        levels=[
            {
                "level": 0,
                "concurrent": 1,
                "stories": [
                    {
                        "story": a.id,
                        "title": "story 1",
                        "root": "main",
                        "subtasks": [
                            {
                                "id": _plan_id(12),
                                "title": "subtask 12",
                                "status": "todo",
                                "branch": branch(a.subtasks[1]),
                                "base": branch(a.subtasks[0]),
                            }
                        ],
                    }
                ],
            },
            {
                "level": 1,
                "concurrent": 1,
                "stories": [
                    {
                        "story": b.id,
                        "title": "story 2",
                        "root": branch(a.subtasks[-1]),
                        "subtasks": [
                            {
                                "id": _plan_id(21),
                                "title": "subtask 21",
                                "status": "todo",
                                "branch": branch(b.subtasks[0]),
                                "base": branch(a.subtasks[-1]),
                            }
                        ],
                    }
                ],
            },
        ],
        integrate={
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [
                {"story": a.id, "tip": branch(a.subtasks[-1])},
                {"story": b.id, "tip": branch(b.subtasks[-1])},
            ],
        },
    )


@pytest.mark.parametrize("bound, concurrent", [(1, [1, 1]), (2, [2, 1]), (10, [3, 1])])
def test_compute_dry_run_plan_bounds_each_level_by_the_passed_max_concurrent(
    bound, concurrent
):
    stories = [
        _plan_story(1, [_plan_subtask(11)]),
        _plan_story(2, [_plan_subtask(21)]),
        _plan_story(3, [_plan_subtask(31)]),
        _plan_story(4, [_plan_subtask(41)], blocked_by=[_plan_id(1)]),
    ]

    plan = _plan(stories, max_concurrent=bound)

    assert [level["concurrent"] for level in plan.levels] == concurrent


def test_compute_dry_run_plan_checks_for_blocker_cycles_first():
    """Review focus: the arrow trail is `assert_no_blocker_cycles`'s own
    message, which proves it ran before any geometry."""
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    with pytest.raises(dag.DependencyCycleError) as caught:
        _plan([a, b])

    assert f"#{a.id} -> #{b.id} -> #{a.id}" in str(caught.value)


def test_compute_dry_run_plan_marks_only_merged_roots_with_merged_from():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[b.id, "outside", a.id])

    plan = _plan([a, b, c])

    rows = {row["story"]: row for level in plan.levels for row in level["stories"]}
    assert rows[c.id]["root"] == "m3/base-00000003"
    assert rows[c.id]["merged_from"] == [b.id, a.id]
    assert list(rows[c.id]) == ["story", "title", "root", "subtasks", "merged_from"]
    assert "merged_from" not in rows[a.id]
    assert "merged_from" not in rows[b.id]


def test_compute_dry_run_plan_accepts_a_one_shot_iterator():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    assert _plan(iter([a, b])) == _plan([a, b])


def test_compute_dry_run_plan_over_an_empty_census():
    assert _plan([]) == runs.DryRunPlan(
        levels=[],
        integrate={
            "branch": "m3-integrate",
            "worktree": "/repo/.claude/worktrees/m3-integrate",
            "order": [],
        },
    )


def test_compute_dry_run_plan_runs_in_a_fresh_interpreter_that_imported_runs_first():
    """Review focus: `integration` imports `cli`, which imports from `runs`, so
    `runs` may only import `integration` at call time. Importing `runs` alone
    and then calling the function must not hit a circular import."""
    code = (
        "import sys; from pathlib import Path; import agent_manager.runs as runs; "
        "assert 'agent_manager.cli' not in sys.modules; "
        "plan = runs.compute_dry_run_plan([], repo_dir=Path('/repo'), "
        "branch_prefix='m3', base_branch='main', max_concurrent=4); "
        "assert plan.levels == [], plan; "
        "assert plan.integrate['branch'] == 'm3-integrate', plan"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_cli_dry_run_payload_delegates_the_plan_to_runs():
    """`cli.dry_run_payload` only assembles the envelope: it calls
    `compute_dry_run_plan`, makes no `dag` call and imports no `integration`.
    Checked on the function's code, not its docstring."""
    import textwrap

    from agent_manager import cli

    function = ast.parse(textwrap.dedent(inspect.getsource(cli.dry_run_payload))).body[0]
    body = function.body[1:] if ast.get_docstring(function) else function.body
    names = {
        node.id
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    }
    imports = [
        node
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert "compute_dry_run_plan" in names
    assert "already_done_entries" in names
    assert "dag" not in names
    assert "integration" not in names
    assert imports == []


def test_cli_dry_run_payload_is_the_plan_wrapped_in_the_envelope():
    from agent_manager import cli

    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    plan = _plan([a, b], max_concurrent=3)

    payload = cli.dry_run_payload(
        [a, b], repo_dir=PLAN_REPO, branch_prefix="m3", base_branch="main", max_concurrent=3
    )

    assert payload == {
        "max_concurrent": 3,
        "levels": plan.levels,
        "already_done": cli.already_done_entries([a, b]),
        "integrate": plan.integrate,
    }


def test_checkpoint_resume_phase_stays_in_cli():
    from agent_manager import cli

    assert cli.checkpoint_resume_phase.__module__ == "agent_manager.cli"
    assert not hasattr(runs, "checkpoint_resume_phase")


def _timed_run() -> models.Run:
    return models.Run(
        id="20261006T120000Z-eee43099",
        workflow="task",
        repo_dir=Path("/repo"),
        base_branch="main",
        branch_prefix="m1",
        status="escalated",
        config=models.RunConfig(harness_timeout=900.0, harness_timeouts={"implement": 3600.0}),
    )


def test_with_harness_override_none_keeps_the_run():
    run = _timed_run()

    assert runs.with_harness_override(run, None) is run


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ((600.0, {}), (600.0, {})),
        ((None, {"plan": 120.0}), (None, {"plan": 120.0})),
        ((60.0, {"review": 90.0}), (60.0, {"review": 90.0})),
    ],
)
def test_with_harness_override_replaces_both_values_wholesale(override, expected):
    """Review Focus 3: never merged -- a bare override clears the recorded map."""
    run = _timed_run()

    replaced = runs.with_harness_override(run, override)

    assert (replaced.config.harness_timeout, replaced.config.harness_timeouts) == expected
    assert replaced.config.max_concurrent_stories == run.config.max_concurrent_stories
    assert (replaced.id, replaced.status) == (run.id, run.status)
    assert (run.config.harness_timeout, run.config.harness_timeouts) == (
        900.0,
        {"implement": 3600.0},
    )


def test_with_harness_override_copies_the_map():
    per_phase = {"plan": 120.0}

    replaced = runs.with_harness_override(_timed_run(), (None, per_phase))
    per_phase["plan"] = 1.0

    assert replaced.config.harness_timeouts == {"plan": 120.0}
