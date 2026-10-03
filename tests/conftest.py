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

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Generator, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePath

import pytest

from agent_manager import board


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


TESTS_DIR = Path(__file__).resolve().parent

TIER_MARKERS = frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})

_DIRECTORY_TIERS = {"e2e": "e2e_fake", "steps": "git"}

# Modules the directory auto-mark skips, as posix paths relative to tests/. One
# exact file each, never a prefix. Their tests carry their tier markers one by
# one, and unmarked ones stay in the unit tier: see the module docstring of
# tests/e2e/test_fake_claude.py for why that module is the exception, and
# tests/e2e/test_run_board.py's test_this_module_runs_in_the_default_suite_unmarked
# for why that module needs the same treatment.
_AUTO_MARK_EXEMPT = frozenset({"e2e/test_fake_claude.py", "e2e/test_run_board.py"})


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
# --- FakeBoard: an in-memory stand-in for the `brd` subprocess (test-tier V3) ---
#
# Not to be confused with tests/test_comments.py's module-private FakeBoard,
# which fakes board.py's Python surface for comments.flush. This one sits one
# level lower, behind `board.run_brd`, and answers brd's own JSON envelopes.
# Its shapes mirror the installed brd's views.card_detail (show/update),
# core._build_node (tree) and comments.Comment (comment add/list), and are
# pinned against the real binary by the brd-tier test in tests/test_board.py.

_FAKE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class _FakeCard:
    id: str
    title: str
    status: str
    parent_id: str | None
    description: str | None
    blocked_by: list[str]
    created_at: str
    updated_at: str


@dataclass
class _FakeComment:
    id: str
    entity_id: str
    author: str
    body: str
    created_at: str


class _FakeBrdError(Exception):
    """A brd domain error, answered as `{"ok": false, "error": {...}}` with exit 1."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _refuse_links(text: str | None, where: str) -> None:
    """Fail loudly on a `[[link]]` in SEEDED test data: real brd indexes it as a
    ref, FakeBoard does not. Guards against a test author accidentally writing
    seed data that assumes backlink indexing. Not called from `_comment_add`
    (the production `run_brd` write path): `comments._ref` (B4) legitimately
    writes `[[card_id]]` into real run-end comment bodies (escalated/parked/
    integrate-failed), and that is not a test-authoring mistake to catch."""
    if text is not None and "[[" in text:
        raise AssertionError(
            f"FakeBoard {where}: {text!r} contains a [[link]]; real brd would "
            "index it into refs/referenced_by, which FakeBoard does not model "
            "(it always answers refs: [])"
        )


class FakeBoard:
    """An in-memory brd board, callable as `board.run_brd(argv, repo_dir, stdin)`.

    Answers exactly the six argv shapes board.py builds with a real
    `subprocess.CompletedProcess` carrying brd's envelope and exit code. Any
    other argv, or a stdin where brd reads none (or none where it reads one),
    raises `AssertionError` naming the argv -- it never guesses an envelope.
    `repo_dir` is ignored: one FakeBoard is one board.

    Tests seed it with `add_card`/`add_comment` (not recorded as writes). Every
    write that succeeds through an argv mutates the tree and is appended to
    `writes`, in call order, as `("set_status", card_id, status)` or
    `("comment_add", card_id, body, author)`. A refused write is not recorded.
    Plain tuples, because conftest classes are not importable by test modules
    under `--import-mode=importlib`.
    """

    def __init__(self) -> None:
        self.cards: dict[str, _FakeCard] = {}
        self.comments: list[_FakeComment] = []
        self.writes: list[tuple[str, ...]] = []
        self._clock = _FAKE_EPOCH

    # -- clock --------------------------------------------------------------

    def _observe(self, timestamp: str) -> None:
        """Keep the clock past every seeded timestamp, so later writes sort last."""
        self._clock = max(self._clock, datetime.fromisoformat(timestamp))

    def _now(self) -> str:
        self._clock += timedelta(microseconds=1)
        return self._clock.isoformat(timespec="microseconds")

    # -- seeding ------------------------------------------------------------

    def add_card(
        self,
        title: str,
        *,
        parent_id: str | None = None,
        card_id: str | None = None,
        status: str = "todo",
        description: str | None = None,
        blocked_by: Sequence[str] = (),
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> str:
        """Seed one card and return its id (a fresh uuid4 unless `card_id` is given)."""
        if parent_id is not None and parent_id not in self.cards:
            raise AssertionError(f"FakeBoard.add_card: unknown parent {parent_id!r}")
        if status == "blocked":
            raise AssertionError(
                "FakeBoard.add_card: 'blocked' is derived, never stored; seed "
                "blocked_by instead"
            )
        _refuse_links(description, "add_card description")
        new_id = card_id if card_id is not None else str(uuid.uuid4())
        if new_id in self.cards:
            raise AssertionError(f"FakeBoard.add_card: duplicate card id {new_id!r}")
        for stamp in (created_at, updated_at):
            if stamp is not None:
                self._observe(stamp)
        created = created_at if created_at is not None else self._now()
        self.cards[new_id] = _FakeCard(
            id=new_id,
            title=title,
            status=status,
            parent_id=parent_id,
            description=description,
            blocked_by=list(blocked_by),
            created_at=created,
            updated_at=updated_at if updated_at is not None else created,
        )
        return new_id

    def add_comment(
        self,
        card_id: str,
        body: str,
        *,
        author: str = "am",
        comment_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        """Seed one comment on an existing card and return its id."""
        if card_id not in self.cards:
            raise AssertionError(f"FakeBoard.add_comment: unknown card {card_id!r}")
        if not body.strip():
            raise AssertionError("FakeBoard.add_comment: brd stores no empty body")
        _refuse_links(body, "add_comment body")
        if created_at is not None:
            self._observe(created_at)
        comment = _FakeComment(
            id=comment_id if comment_id is not None else str(uuid.uuid4()),
            entity_id=card_id,
            author=author,
            body=body,
            created_at=created_at if created_at is not None else self._now(),
        )
        self.comments.append(comment)
        return comment.id

    # -- the run_brd seam ---------------------------------------------------

    def __call__(
        self, argv: Sequence[str], repo_dir: Path | None, stdin: str | None
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        try:
            data = self._answer(args, stdin)
        except _FakeBrdError as exc:
            envelope = {
                "ok": False,
                "error": {"type": exc.error_type, "message": exc.message},
            }
            return subprocess.CompletedProcess(args, 1, json.dumps(envelope) + "\n", "")
        envelope = {"ok": True, "data": data}
        return subprocess.CompletedProcess(args, 0, json.dumps(envelope) + "\n", "")

    def _answer(self, argv: list[str], stdin: str | None) -> object:
        match argv:
            case ["brd", "show", card_id] if stdin is None:
                return self._show(card_id)
            case ["brd", "tree", card_id] if stdin is None:
                return [self._node(self._require_card(card_id))]
            case ["brd", "tree"] if stdin is None:
                return [self._node(card) for card in self._children_of(None)]
            case ["brd", "update", card_id, "--status", status] if stdin is None:
                return self._update_status(card_id, status)
            case ["brd", "comment", "add", card_id, "-", "--author", author] if (
                stdin is not None
            ):
                return self._comment_add(card_id, stdin, author)
            case ["brd", "comment", "list", card_id] if stdin is None:
                self._require_entity(card_id)
                return [self._comment_dict(c) for c in self._comments_of(card_id)]
        raise AssertionError(
            f"FakeBoard does not answer argv {argv!r} (stdin={stdin!r})"
        )

    # -- brd's lookups, errors and payload shapes ---------------------------

    def _require_card(self, card_id: str) -> _FakeCard:
        card = self.cards.get(card_id)
        if card is None:
            raise _FakeBrdError("CardNotFoundError", f"no card with id {card_id}")
        return card

    def _show(self, card_id: str) -> dict:
        card = self.cards.get(card_id)
        if card is None:
            raise _FakeBrdError(
                "CardNotFoundError", f"no card, issue, or document with id {card_id}"
            )
        return self._detail(card)

    def _require_entity(self, entity_id: str) -> None:
        # brd's entities.require: what `comment add`/`comment list` check first.
        if entity_id not in self.cards:
            raise _FakeBrdError("EntityNotFoundError", f"no entity with id {entity_id}")

    def _update_status(self, card_id: str, status: str) -> dict:
        # brd's core.update_card: the card must exist, then `blocked` is refused.
        card = self._require_card(card_id)
        if status == "blocked":
            raise _FakeBrdError(
                "InvalidStatusError",
                "status cannot be set to 'blocked' directly; it is derived",
            )
        card.status = status
        card.updated_at = self._now()
        self.writes.append(("set_status", card_id, status))
        return self._detail(card)

    def _comment_add(self, card_id: str, body: str, author: str) -> dict:
        # brd's comments.add: the entity must exist, then a blank body is refused.
        self._require_entity(card_id)
        if not body.strip():
            raise _FakeBrdError("EmptyCommentError", "comment body is empty")
        comment = _FakeComment(
            id=str(uuid.uuid4()),
            entity_id=card_id,
            author=author,
            body=body,
            created_at=self._now(),
        )
        self.comments.append(comment)
        self.writes.append(("comment_add", card_id, body, author))
        return self._comment_dict(comment)

    def _children_of(self, parent_id: str | None) -> list[_FakeCard]:
        # brd: ORDER BY created_at (a stable sort keeps insertion order on ties).
        return sorted(
            (card for card in self.cards.values() if card.parent_id == parent_id),
            key=lambda card: card.created_at,
        )

    def _comments_of(self, entity_id: str) -> list[_FakeComment]:
        # brd: ORDER BY created_at, rowid.
        return sorted(
            (c for c in self.comments if c.entity_id == entity_id),
            key=lambda c: c.created_at,
        )

    def _resolved_status(self, card: _FakeCard, seen: frozenset[str] = frozenset()) -> str:
        # brd's core.resolve_status: `blocked` is derived from an unfinished
        # blocker or a blocked parent, and only ever replaces a stored `todo`.
        if card.status != "todo":
            return card.status
        if card.id in seen:
            return "todo"
        seen = seen | {card.id}
        for blocker_id in card.blocked_by:
            blocker = self.cards.get(blocker_id)
            if blocker is not None and self._resolved_status(blocker, seen) != "done":
                return "blocked"
        if card.parent_id is not None:
            parent = self.cards.get(card.parent_id)
            if parent is not None and self._resolved_status(parent, seen) == "blocked":
                return "blocked"
        return "todo"

    @staticmethod
    def _comment_dict(comment: _FakeComment) -> dict:
        return {
            "id": comment.id,
            "entity_id": comment.entity_id,
            "author": comment.author,
            "body": comment.body,
            "created_at": comment.created_at,
        }

    def _detail(self, card: _FakeCard) -> dict:
        # brd's views.card_detail: what `show` and `update` answer.
        return {
            "id": card.id,
            "kind": "card",
            "title": card.title,
            "description": card.description,
            "status": self._resolved_status(card),
            "parent_id": card.parent_id,
            "created_at": card.created_at,
            "updated_at": card.updated_at,
            "blocked_by": list(card.blocked_by),
            "children": [child.id for child in self._children_of(card.id)],
            "comments": [self._comment_dict(c) for c in self._comments_of(card.id)],
            "refs": [],
            "referenced_by": [],
        }

    def _node(self, card: _FakeCard) -> dict:
        # brd's core._build_node: what `tree` answers, nested, no parent_id.
        return {
            "id": card.id,
            "title": card.title,
            "description": card.description,
            "status": self._resolved_status(card),
            "blocked_by": list(card.blocked_by),
            "created_at": card.created_at,
            "updated_at": card.updated_at,
            "children": [self._node(child) for child in self._children_of(card.id)],
        }


@pytest.fixture
def fake_board(monkeypatch: pytest.MonkeyPatch) -> FakeBoard:
    """A fresh, empty FakeBoard installed as `board.run_brd` for this test."""
    fake = FakeBoard()
    monkeypatch.setattr(board, "run_brd", fake)
    return fake
