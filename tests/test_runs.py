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

from agent_manager import runs

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
    "NotResumableError",
    "CheckpointMismatchError",
)


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_cli_re_exports_the_moved_name_as_the_same_object(name):
    from agent_manager import cli

    assert getattr(cli, name) is getattr(runs, name)


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
