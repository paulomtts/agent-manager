"""Unit tier: the files `am run --detach` leaves (card aff9fdbf).

`fork_detacher` really forks, so it is exercised only by the e2e_fake test
`tests/e2e/test_detached_run.py`; nothing here starts a process.
"""

import ast
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from agent_manager import detach, paths

RUN_ID = "20261004T090000Z-1a2b3c4d"


@pytest.fixture(autouse=True)
def data_home(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg"


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_create_run_log_makes_an_empty_0600_file_in_the_run_directory():
    log = detach.create_run_log(RUN_ID)

    assert log == paths.data_dir() / "runs" / RUN_ID / detach.RUN_LOG_NAME
    assert log.is_file()
    assert log.read_bytes() == b""
    assert _mode(log) == 0o600


def test_create_run_log_is_0600_whatever_the_umask_or_an_existing_file_said():
    existing = paths.run_dir(RUN_ID) / detach.RUN_LOG_NAME
    existing.write_text("earlier\n", encoding="utf-8")
    existing.chmod(0o644)
    old = os.umask(0)
    try:
        log = detach.create_run_log(RUN_ID)
    finally:
        os.umask(old)

    assert _mode(log) == 0o600
    assert log.read_text(encoding="utf-8") == "earlier\n"


def test_write_report_writes_one_line_at_0600_and_leaves_no_temp_file():
    path = detach.write_report(RUN_ID, json.dumps({"ok": True, "data": {"done": True}}))

    assert path == paths.data_dir() / "runs" / RUN_ID / detach.REPORT_NAME
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True, "data": {"done": True}}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert _mode(path) == 0o600
    assert sorted(entry.name for entry in path.parent.iterdir()) == [detach.REPORT_NAME]


def test_write_report_replaces_an_earlier_report_whole():
    detach.write_report(RUN_ID, '{"ok":false}')

    path = detach.write_report(RUN_ID, '{"ok":true}')

    assert path.read_text(encoding="utf-8") == '{"ok":true}\n'
    assert sorted(entry.name for entry in path.parent.iterdir()) == [detach.REPORT_NAME]


def test_spawned_carries_the_pid_and_the_two_signals():
    events: list[str] = []
    spawned = detach.Spawned(pid=4242, go=lambda: events.append("go"), abort=lambda: events.append("abort"))

    spawned.go()
    spawned.abort()

    assert spawned.pid == 4242
    assert events == ["go", "abort"]


def test_detach_module_imports_only_paths_and_the_stdlib():
    tree = ast.parse(Path(detach.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base == "agent_manager":
                modules |= {f"agent_manager.{alias.name}" for alias in node.names}
            else:
                modules.add(base)
    ours = {name for name in modules if name.split(".")[0] == "agent_manager"}
    theirs = {name.split(".")[0] for name in modules} - {"agent_manager"}
    assert ours <= {"agent_manager.paths"}
    assert theirs <= set(sys.stdlib_module_names) | {"__future__"}
