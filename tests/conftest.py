"""Suite-wide isolation from the user's real agent-manager data directory.

`paths.data_dir()` resolves `XDG_DATA_HOME` (else `~/.local/share`) at call time.
A test that opens a store or a run directory without pointing `XDG_DATA_HOME`
somewhere temporary writes into the real directory, and can read real run data.
The guard below fails the session if the real directory changed during it.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_manager import board


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
    """Fail loudly on a `[[link]]`: real brd indexes it as a ref, FakeBoard does not."""
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
