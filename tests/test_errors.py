"""Behaviour of the package's exception classes (`src/agent_manager/errors.py`).

Unit tier: constructing exceptions and reading source; nothing is spawned.
"""

import ast
import inspect

from agent_manager import cli, errors
from agent_manager.errors import IsolationUnavailableError


def test_isolation_unavailable_names_the_mode_the_probe_and_the_way_out():
    detail = "bwrap --bind / / --new-session true exited 1"
    error = IsolationUnavailableError("bwrap", detail)
    assert error.mode == "bwrap"
    assert error.detail == detail
    message = str(error)
    assert "bwrap" in message
    assert detail in message
    assert "--isolation none" in message
    assert isinstance(error, ValueError)


def test_isolation_unavailable_is_an_envelope_and_exit_3_not_a_traceback():
    # cli.HANDLED is what main() turns into `ok: false` and exit 3.
    assert isinstance(IsolationUnavailableError("bwrap", "x"), cli.HANDLED)


def test_errors_imports_nothing_from_the_package():
    # Every layer may raise these, so this module must sit below all of them.
    tree = ast.parse(inspect.getsource(errors))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("agent_manager"), alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in errors.py"
            assert not (node.module or "").startswith("agent_manager"), node.module
