"""Suite-wide isolation from the user's real agent-manager data directory.

`paths.data_dir()` resolves `XDG_DATA_HOME` (else `~/.local/share`) at call time.
A test that opens a store or a run directory without pointing `XDG_DATA_HOME`
somewhere temporary writes into the real directory, and can read real run data.
The guard below fails the session if the real directory changed during it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _real_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path(os.environ["HOME"]) / ".local" / "share"
    return base / "agent-manager"


REAL_DATA_DIR = _real_data_dir()


def _snapshot(root: Path) -> frozenset[str]:
    """Every path under `root`; empty when `root` is absent.

    Paths only, not sizes or mtimes: a live `am` run heartbeats its lease into
    the real data directory every few seconds, so when THIS suite runs as that
    run's own verification (dogfooding), pre-existing files are always being
    modified by the parent process. A leaking test's signature is a path that
    appears (a fresh run directory, a journal) or disappears, and that is what
    the guard flags; pure modification of a file that already existed at
    session start is the parent run's legitimate churn, not a leak.
    """
    if not root.exists():
        return frozenset()
    return frozenset(str(path.relative_to(root)) for path in root.rglob("*"))


_ORIGINAL_XDG = os.environ.get("XDG_DATA_HOME")


@pytest.fixture(autouse=True)
def isolated_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Point `XDG_DATA_HOME` at a fresh temporary directory for every test.

    Left alone when a wider-scoped fixture already moved it (the e2e `project`
    fixture sets one per module, and its tests read artifacts back from there):
    only a value still equal to the session's original one is replaced. A test
    that sets or deletes it itself (`tests/test_paths.py`) runs after this and
    wins, as monkeypatch calls stack.
    """
    if os.environ.get("XDG_DATA_HOME") == _ORIGINAL_XDG:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("xdg-data")))


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config._real_data_snapshot = _snapshot(REAL_DATA_DIR)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    before = getattr(session.config, "_real_data_snapshot", None)
    if before is None:
        return
    after = _snapshot(REAL_DATA_DIR)
    if after != before:
        changed = sorted(p.split("|", 1)[0] for p in after ^ before)
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        message = (
            f"the test session changed the real data directory {REAL_DATA_DIR} "
            f"({len(changed)} path(s)), e.g. {changed[:5]}"
        )
        if reporter is not None:
            reporter.write_line(f"DATA-DIR GUARD FAILED: {message}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
