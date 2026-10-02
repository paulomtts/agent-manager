"""Suite-wide isolation from the user's real agent-manager data directory.

`paths.data_dir()` resolves `XDG_DATA_HOME` (else `~/.local/share`) at call time.
A test that opens a store or a run directory without pointing `XDG_DATA_HOME`
somewhere temporary writes into the real directory, and can read real run data.
The guard below fails the session if the real directory changed during it.

It also gives directory-conventional tests a default tier marker: items under
`tests/e2e/` get `e2e_fake` and items under `tests/steps/` get `git`, unless the
item already carries a tier marker anywhere on its marker chain or its module is
in `_AUTO_MARK_EXEMPT` (tests/e2e/test_fake_claude.py, which marks its tests one
by one). The hook only adds markers; the addopts `-m` expression in
pyproject.toml does the deselecting.

Unit-tier items (no tier marker after collection) run with stub `brd`, `git` and
`claude` scripts first on `PATH`; each stub prints `<name>: forbidden in the unit
tier` to stderr and exits 99, so an accidental real spawn fails loudly.

A passing call phase over its tier's budget (unit 0.5s, `git` 2s; the opt-in tiers
have none) is turned into a failure naming the tier, budget and measured time.

At collection, before `-m` deselection, the run fails with a usage error when more
than 5 items carry `e2e` or any `e2e` item's docstring has no line starting
`justification:`, so the check also fires in the default run.

An item marked `git` or `brd` is skipped at setup when that binary is not on
`PATH`; this is the suite's only such check (tests/e2e/conftest.py's `toolchain`
calls the same `missing_binary`).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Generator, Iterable
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

# Modules the directory auto-mark skips, as posix paths relative to tests/. One
# exact file each, never a prefix. Their tests carry their tier markers one by
# one, and unmarked ones stay in the unit tier: see the module docstring of
# tests/e2e/test_fake_claude.py for why that module is the exception.
_AUTO_MARK_EXEMPT = frozenset({"e2e/test_fake_claude.py"})


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

    None when its first directory is neither `e2e` nor `steps`, when it is one of
    the `_AUTO_MARK_EXEMPT` modules, or when `existing` (every marker name on the
    item's chain) already holds a tier.
    """
    if len(rel_path.parts) < 2:
        return None
    if rel_path.as_posix() in _AUTO_MARK_EXEMPT:
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


E2E_CAP = 5
JUSTIFICATION_PREFIX = "justification:"


def has_justification(docstring: str | None) -> bool:
    """True when some line of `docstring`, leading whitespace stripped, starts `justification:`."""
    if not docstring:
        return False
    return any(line.lstrip().startswith(JUSTIFICATION_PREFIX) for line in docstring.splitlines())


def e2e_tier_violations(items: Iterable[tuple[str, Iterable[str], str | None]]) -> list[str]:
    """Every e2e-tier rule `items` break, as messages; empty when none.

    Each item is a (nodeid, marker names on its chain, docstring) triple. Only the
    exact `e2e` marker counts (`e2e_fake` is another tier). Over the cap gives one
    message naming the count, the cap and every e2e nodeid; each e2e item with no
    `justification:` line gives one more, in input order.
    """
    e2e = [(nodeid, doc) for nodeid, markers, doc in items if "e2e" in set(markers)]
    violations: list[str] = []
    if len(e2e) > E2E_CAP:
        nodeids = ", ".join(nodeid for nodeid, _ in e2e)
        violations.append(
            f"the e2e tier is capped at {E2E_CAP} tests, but {len(e2e)} carry the e2e marker: {nodeids}"
        )
    for nodeid, doc in e2e:
        if not has_justification(doc):
            violations.append(
                f"{nodeid}: an e2e test's docstring needs a line starting `{JUSTIFICATION_PREFIX}`"
            )
    return violations


def item_docstring(item: object) -> str | None:
    """The docstring of the Python function behind `item`, or None.

    Each parametrization of a function is its own item with that same function,
    so all of them share its docstring. Non-function items have no docstring.
    """
    return getattr(getattr(item, "function", None), "__doc__", None)


class E2ETierCap:
    """The collection-time e2e cap and justification check, as its own plugin.

    A plugin object because this module already defines
    `pytest_collection_modifyitems` for the directory auto-mark. `tryfirst` puts
    it ahead of pytest's own `-m` deselection, so the default run (which
    deselects `e2e`) still sees every e2e item. It reads only the `e2e` marker,
    which the auto-mark never adds, so its order against the auto-mark does not
    matter.
    """

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        violations = e2e_tier_violations(
            (item.nodeid, [mark.name for mark in item.iter_markers()], item_docstring(item))
            for item in items
        )
        if violations:
            raise pytest.UsageError("e2e tier check failed:\n" + "\n".join(violations))


E2E_CAP_PLUGIN_NAME = "agent-manager-e2e-tier-cap"


def pytest_configure(config: pytest.Config) -> None:
    """Register the e2e cap check once per session."""
    if not config.pluginmanager.has_plugin(E2E_CAP_PLUGIN_NAME):
        config.pluginmanager.register(E2ETierCap(), E2E_CAP_PLUGIN_NAME)


UNIT_BUDGET_S = 0.5
GIT_BUDGET_S = 2.0

# Opt-in tiers have no per-test budget, and win over `git` when an item has both.
_UNBUDGETED_TIERS = TIER_MARKERS - {"git"}


def tier_budget_violation(markers: Iterable[str], duration: float) -> str | None:
    """The budget failure message for a call phase of `duration` seconds, or None.

    `markers` is every marker name on the item's chain. No tier marker means the
    unit budget; `git` alone means the git budget; any opt-in tier means none.
    A duration exactly on the budget passes.
    """
    names = set(markers)
    if names & _UNBUDGETED_TIERS:
        return None
    tier, budget = ("git", GIT_BUDGET_S) if "git" in names else ("unit", UNIT_BUDGET_S)
    if duration <= budget:
        return None
    return f"{tier}-tier budget exceeded: {duration:.3f}s > {budget:g}s"


STUB_NAMES = ("brd", "git", "claude")
STUB_EXIT_CODE = 99
STUB_DIR_PREFIX = "unit-tier-stubs"


def stub_script(name: str) -> str:
    """A shell script that refuses to be `name`: one stderr line, exit 99, no delegation."""
    return f"#!/bin/sh\necho '{name}: forbidden in the unit tier' >&2\nexit {STUB_EXIT_CODE}\n"


@pytest.fixture(scope="session")
def unit_tier_stub_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One directory of `brd`/`git`/`claude` stubs, built once per session."""
    bin_dir = tmp_path_factory.mktemp(STUB_DIR_PREFIX)
    for name in STUB_NAMES:
        script = bin_dir / name
        script.write_text(stub_script(name))
        script.chmod(0o755)
    return bin_dir


@pytest.fixture(autouse=True)
def unit_tier_path_shim(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the stubs first on `PATH` for items with no tier marker.

    The same prepend-a-bin-dir technique as `fake_brd` (tests/steps/test_rollup.py)
    and `brd_shim` (tests/e2e/test_board_comments.py), except the stubs never
    hand off to a real binary. Items with any tier marker keep `PATH` exactly as
    inherited, and the stub directory is only built once a unit item needs it. A
    test that sets `PATH` itself runs after this and wins, as monkeypatch calls
    stack. A binary invoked by absolute path bypasses the shim.
    """
    if TIER_MARKERS.intersection(mark.name for mark in request.node.iter_markers()):
        return
    bin_dir = request.getfixturevalue("unit_tier_stub_dir")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', os.defpath)}")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Fail a passing call phase that ran over its tier's per-test budget.

    `tryfirst` makes this the outermost wrapper, so it sees the report after the
    other plugins (xfail handling) have settled it. Setup and teardown are not
    counted, and a failed or skipped report keeps its own outcome and text.
    """
    report = yield
    if report.when == "call" and report.passed:
        violation = tier_budget_violation((mark.name for mark in item.iter_markers()), call.duration)
        if violation is not None:
            report.outcome = "failed"
            report.longrepr = violation
    return report


BINARY_TIERS = ("git", "brd")
"""The tiers whose marker means "needs this binary on PATH", in skip-reason order."""


def missing_binary(
    markers: Iterable[str], which: Callable[[str], str | None] | None = None
) -> str | None:
    """The first of `git`, `brd` that `markers` names and `which` cannot find, or None.

    `markers` is every marker name on the item's chain. `which` defaults to
    `shutil.which`, looked up at call time. Items with neither marker are never
    reported, whatever `which` says.
    """
    names = set(markers)
    lookup = shutil.which if which is None else which
    for name in BINARY_TIERS:
        if name in names and lookup(name) is None:
            return name
    return None


def binary_skip_reason(name: str) -> str:
    return f"the {name} CLI must be installed for the {name} tier"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip a `git`- or `brd`-marked item whose binary is not on PATH.

    Runs after collection, so the auto-added `git` on tests/steps/ items counts,
    and before fixture setup, so no fixture spawns a missing binary first.
    """
    missing = missing_binary(mark.name for mark in item.iter_markers())
    if missing is not None:
        pytest.skip(binary_skip_reason(missing))
