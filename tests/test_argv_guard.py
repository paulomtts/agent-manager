"""`am run` / `am resume` with no verification text in argv (card 1b938053).

Unit tier: `neutralize` is pure, and `reexec_neutral` is driven with
`os.execve` replaced, so no test here execs.
"""

import ast
import json
import sys
from pathlib import Path

import pytest

import agent_manager
from agent_manager import argv_guard
from agent_manager.argv_guard import FROM_ENV_FLAG, VERIFY_ENV, neutralize


def _env_values(environ: dict[str, str]) -> list[str]:
    return json.loads(environ[VERIFY_ENV])


def test_a_verify_pair_moves_to_the_environment():
    """Spec test 1."""
    argv = ["am", "run", "--card", "c", "--branch-prefix", "p", "--verify", "uv run pytest"]

    new_argv, new_env = neutralize(argv, {})

    assert new_argv == ["am", "run", FROM_ENV_FLAG, "--card", "c", "--branch-prefix", "p"]
    assert _env_values(new_env) == ["uv run pytest"]


def test_the_equals_spelling_moves_to_the_environment():
    """Spec test 2."""
    argv = ["am", "run", "--card", "c", "--branch-prefix", "p", "--verify=uv run pytest"]

    new_argv, new_env = neutralize(argv, {})

    assert new_argv == ["am", "run", FROM_ENV_FLAG, "--card", "c", "--branch-prefix", "p"]
    assert _env_values(new_env) == ["uv run pytest"]


def test_repeated_and_mixed_spellings_keep_command_line_order():
    """Spec test 3."""
    argv = ["am", "run", "--verify", "a", "--card", "c", "--verify=b", "--verify", "c2"]

    new_argv, new_env = neutralize(argv, {})

    assert new_argv == ["am", "run", FROM_ENV_FLAG, "--card", "c"]
    assert _env_values(new_env) == ["a", "b", "c2"]


def test_resume_keeps_the_run_id_positional():
    """The spec's `resume R --verify=a --verify b` example; Review Focus 4."""
    new_argv, new_env = neutralize(["am", "resume", "R", "--verify=a", "--verify", "b"], {})

    assert new_argv == ["am", "resume", FROM_ENV_FLAG, "R"]
    assert _env_values(new_env) == ["a", "b"]


@pytest.mark.parametrize(
    "value",
    [
        "uv run pytest -k 'not slow'",
        'echo "double"',
        "back\\slash",
        "line one\nline two",
        "pytest -k café",
        "",
    ],
)
def test_awkward_values_round_trip_through_json(value):
    """Spec test 4; Review Focus 5. The empty string comes in as `--verify=`."""
    for argv in (
        ["am", "run", "--card", "c", "--verify", value],
        ["am", "run", "--card", "c", f"--verify={value}"],
    ):
        new_argv, new_env = neutralize(argv, {})

        assert new_argv == ["am", "run", FROM_ENV_FLAG, "--card", "c"]
        assert _env_values(new_env) == [value]


def test_the_equals_spelling_splits_on_the_first_equals_only():
    """Spec test 5."""
    _, new_env = neutralize(["am", "run", "--verify=a=b"], {})

    assert _env_values(new_env) == ["a=b"]


def test_a_bare_verify_consumes_a_following_dash_token():
    """Spec test 6: Click gives `--verify` the next token, whatever it looks like."""
    new_argv, new_env = neutralize(["am", "run", "--verify", "--card", "x"], {})

    assert new_argv == ["am", "run", FROM_ENV_FLAG, "x"]
    assert _env_values(new_env) == ["--card"]


@pytest.mark.parametrize(
    "argv",
    [
        ["am", "run", "--story", "M", "--branch-prefix", "p"],
        ["am", "resume", "R"],
        ["am", "run"],
    ],
)
def test_no_verify_returns_none(argv):
    """Spec test 7: identity options stay where they are."""
    assert neutralize(argv, {}) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["am", "run", FROM_ENV_FLAG, "--card", "c"],
        ["am", "run", FROM_ENV_FLAG, "--card", "c", "--verify", "x"],
        ["am", "resume", "R", "--verify=x", FROM_ENV_FLAG],
    ],
)
def test_an_already_neutral_argv_returns_none(argv):
    """Spec test 8: re-execing would silently drop the operator's AM_VERIFY_JSON."""
    assert neutralize(argv, {}) is None


@pytest.mark.parametrize(
    "argv",
    [["am", "status", "--verify", "x"], ["am", "logs", "--verify=x"], ["am"], []],
)
def test_other_subcommands_and_short_argv_return_none(argv):
    """Spec test 9."""
    assert neutralize(argv, {}) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["am", "run", "--card", "c", "--verify"],
        ["am", "run", "--card", "c", "--verify", "--", "x"],
    ],
)
def test_a_trailing_bare_verify_returns_none(argv):
    """Spec test 10: Typer reports the missing value as its own usage error."""
    assert neutralize(argv, {}) is None


def test_tokens_after_a_terminator_are_copied_and_never_read():
    """Spec test 11."""
    argv = ["am", "run", "--verify", "a", "--", "--verify", "b", FROM_ENV_FLAG]

    new_argv, new_env = neutralize(argv, {})

    assert new_argv == ["am", "run", FROM_ENV_FLAG, "--", "--verify", "b", FROM_ENV_FLAG]
    assert _env_values(new_env) == ["a"]


def test_only_a_terminator_after_it_returns_none():
    """Spec test 11: a `--verify` after `--` alone is no `--verify` at all."""
    assert neutralize(["am", "run", "--card", "c", "--", "--verify", "x"], {}) is None


def test_an_existing_variable_is_overwritten_and_the_rest_kept():
    """Spec test 12."""
    environ = {VERIFY_ENV: '["stale"]', "PATH": "/bin", "HOME": "/h"}

    _, new_env = neutralize(["am", "run", "--verify", "fresh"], environ)

    assert new_env == {VERIFY_ENV: '["fresh"]', "PATH": "/bin", "HOME": "/h"}


def test_neither_input_is_mutated():
    """Spec test 13."""
    argv = ["am", "run", "--card", "c", "--verify", "x"]
    environ = {VERIFY_ENV: '["stale"]', "PATH": "/bin"}
    argv_before, environ_before = list(argv), dict(environ)

    neutralize(argv, environ)

    assert argv == argv_before
    assert environ == environ_before


def test_the_module_imports_only_the_stdlib():
    """Spec test 17: an L5 adapter beside `detach`, with no `agent_manager` import."""
    tree = ast.parse(Path(argv_guard.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
    assert modules <= set(sys.stdlib_module_names) | {"__future__"}


class _Execed(Exception):
    """Stands in for `os.execve` not returning: the fake raises it after recording."""


def test_reexec_neutral_execs_the_interpreter_on_the_neutral_argv(monkeypatch):
    """Spec test 14."""
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def fake_execve(path, args, env):
        calls.append((path, list(args), dict(env)))
        raise _Execed

    monkeypatch.setattr(argv_guard.os, "execve", fake_execve)
    monkeypatch.setenv("AM_TEST_KEPT", "yes")
    argv = ["/usr/bin/am", "run", "--card", "c", "--verify", "uv run pytest"]

    with pytest.raises(_Execed):
        argv_guard.reexec_neutral(argv)

    ((path, args, env),) = calls
    assert path == sys.executable
    assert args == [sys.executable, "/usr/bin/am", "run", FROM_ENV_FLAG, "--card", "c"]
    assert _env_values(env) == ["uv run pytest"]
    assert env["AM_TEST_KEPT"] == "yes"


def test_reexec_neutral_leaves_a_neutral_argv_alone(monkeypatch):
    """Spec test 15."""

    def fake_execve(path, args, env):
        pytest.fail("reexec_neutral execed an argv with no --verify")

    monkeypatch.setattr(argv_guard.os, "execve", fake_execve)

    assert argv_guard.reexec_neutral(["/usr/bin/am", "run", "--card", "c"]) is None


def test_reexec_neutral_returns_the_warning_when_the_exec_fails(monkeypatch):
    """Spec test 16."""

    def fake_execve(path, args, env):
        raise OSError("exec format error")

    monkeypatch.setattr(argv_guard.os, "execve", fake_execve)

    warning = argv_guard.reexec_neutral(["/usr/bin/am", "run", "--verify", "x"])

    assert warning == argv_guard.ARGV_VISIBLE_WARNING
    assert warning == "argv: verification commands visible in the process command line"


def test_only_argv_guard_calls_os_execve():
    """Spec test 18: pins docs/standards/architecture.md §5 row 5.15."""
    package = Path(agent_manager.__file__).parent
    sites: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "execve") or (
                isinstance(node, ast.alias) and node.name == "execve"
            ):
                sites.add(path.relative_to(package).as_posix())
    assert sites == {"argv_guard.py"}
