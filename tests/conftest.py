"""Suite-wide isolation from the user's real agent-manager data directory.

`paths.data_dir()` resolves `XDG_DATA_HOME` (else `~/.local/share`) at call time.
A test that opens a store or a run directory without pointing `XDG_DATA_HOME`
somewhere temporary writes into the real directory, and can read real run data.
The guard below fails the session if the real directory changed during it.

It also gives directory-conventional tests a default tier marker: items under
`tests/e2e/` get `e2e_fake` and items under `tests/steps/` get `git`, unless the
item already carries a tier marker anywhere on its marker chain. The hook only
adds markers; the addopts `-m` expression in pyproject.toml does the deselecting.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePath

import pytest


def _real_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path(os.environ["HOME"]) / ".local" / "share"
    return base / "agent-manager"


REAL_DATA_DIR = _real_data_dir()


def _snapshot(root: Path) -> frozenset[str]:
    """Every path under `root` with its size and mtime; empty when `root` is absent."""
    if not root.exists():
        return frozenset()
    entries = set()
    for path in root.rglob("*"):
        stat = path.stat()
        entries.add(f"{path.relative_to(root)}|{stat.st_size}|{stat.st_mtime_ns}")
    return frozenset(entries)


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


TESTS_DIR = Path(__file__).resolve().parent

TIER_MARKERS = frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})

_DIRECTORY_TIERS = {"e2e": "e2e_fake", "steps": "git"}


def relative_to_tests(path: os.PathLike[str] | str, tests_dir: Path = TESTS_DIR) -> PurePath | None:
    """`path` relative to `tests_dir`, or None when it lies outside it.

    Resolved first, so neither the invocation cwd nor the rootdir matters.
    """
    try:
        return Path(path).resolve().relative_to(tests_dir)
    except ValueError:
        return None


def default_tier_marker(rel_path: PurePath, existing: Iterable[str]) -> str | None:
    """The tier marker an item at `rel_path` (relative to tests/) should get.

    None when its first directory is neither `e2e` nor `steps`, or when
    `existing` (every marker name on the item's chain) already holds a tier.
    """
    if len(rel_path.parts) < 2:
        return None
    tier = _DIRECTORY_TIERS.get(rel_path.parts[0])
    if tier is None:
        return None
    if TIER_MARKERS.intersection(existing):
        return None
    return tier


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Add the directory default tier marker to each item that has no tier yet.

    `tryfirst` so the markers exist before pytest's own `-m` deselection runs.
    `iter_markers` walks function, class and module `pytestmark`, so a module
    marked `e2e` (tests/e2e/test_real_harness*.py) keeps `e2e` alone.
    """
    rel_paths: dict[Path, PurePath | None] = {}
    for item in items:
        path = Path(item.path)
        if path not in rel_paths:
            rel_paths[path] = relative_to_tests(path)
        rel_path = rel_paths[path]
        if rel_path is None:
            continue
        marker = default_tier_marker(rel_path, {mark.name for mark in item.iter_markers()})
        if marker is not None:
            item.add_marker(marker)
