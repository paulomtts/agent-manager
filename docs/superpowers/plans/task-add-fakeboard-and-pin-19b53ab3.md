<!-- task-pipeline: validated -->
# Subtask 19b53ab3 — Add FakeBoard and pin it against real brd

Parent story: 13c63fea ("Give board.py an injection seam and a FakeBoard fake"), milestone 66ed75cd ("Milestone 15: test tiers"). Narrows decision V3 of `docs/superpowers/specs/2026-10-02-test-tier-design.md` to its fixture-and-oracle half; the seam half is sibling `cf9f8555` (done).

## Prerequisites (reconcile before building)

- The `run_brd` seam from `cf9f8555` (branch `m15/task-add-the-run-brd-cf9f8555`, commits `2cb9349`, `aadfb0d`) must be in this branch. Master's `board.py` still calls `_run` directly, so without it FakeBoard has nothing to patch. Do not re-add or re-shape the seam.
- The `brd` marker and `addopts` tier exclusion from `19b291e9` (branch `m15/task-register-the-markers-19b291e9`) must be in this branch so that `-m brd` selects the pinning test and the default run skips it. Do not register markers here; consume them.

## Scope

1. A `FakeBoard` in `tests/conftest.py`, plus a fixture that builds one and installs it with `monkeypatch.setattr(board, "run_brd", fake)`. This is a different fake from the module-private `FakeBoard` already in `tests/test_comments.py` (which stands in for `board`'s Python call surface -- `comment_list`/`comment_add`/`write_lock` -- for `comments.flush`'s `board_api` seam). The new one stands in for the `brd` subprocess itself, one level lower, and is unrelated; do not merge or reuse the two.
   - It holds an in-memory card tree: cards with `id, title, status, parent_id, description, blocked_by, created_at`, plus per-card comments (`id, entity_id, body, author, created_at`). Tests seed it through a small helper API (for example, adding a card under a parent, or adding a comment).
   - It is callable as `run_brd(argv, repo_dir, stdin)` and returns a real `subprocess.CompletedProcess[str]` whose `stdout` is a JSON envelope and whose `returncode` matches what real brd returns.
   - It answers exactly the argv shapes `board.py` builds: `show <id>`, `tree <id>`, `tree` (no id; roots), `update <id> --status <s>`, `comment add <id> - --author <a>` (body read from `stdin`), and `comment list <id>`.
   - Success answers `{"ok": true, "data": ...}`, with data shaped like real brd's: a card object for `show`/`update`; a one-element list of nested nodes (`children`) for `tree <id>`; a list of roots for `tree`; a comment object with a string `id` for `comment add`; and the comments oldest first for `comment list`. Payloads must validate as `models.Card` and `models.CardNode` (`src/agent_manager/models.py:175-211`) and as `BoardComment` via `comment_list`.
   - Writes mutate the in-memory tree, so a later `show` sees the new status and a later `comment list` sees the new comment. Every write is also recorded in order, as `set_status` and `comment_add` entries carrying the card id, status or body, and author, so that tests can assert on them.
2. One `brd`-tier pinning test: it builds the same small card tree on a real temporary brd board and in a FakeBoard, then asserts that FakeBoard's envelopes match real `brd show`, `brd tree` and `brd comment list` output. This follows the "conftest twin" spirit of `tests/e2e/test_fake_claude.py:39-50`: the fake's shape is checked against its real counterpart so it cannot drift silently.
   - The tree has a parent, at least one child, and a comment. The FakeBoard is seeded from the real board's ids and fields.
   - "Match" means the same JSON structure at every level: the same key sets, the same JSON value types, the same list lengths and order, and equal values for every non-volatile field (`title, status, parent_id, description, blocked_by, body, author`, nesting). Volatile values such as generated ids and timestamps are compared by type and presence only, unless seeding makes them identical.
   - It reuses the existing real-board helpers in `tests/test_board.py` (`temp_board` around :241-260, `_add_card`/`_brd_json` around :264-274), or places the test beside them.

## Error paths

- An unknown card id in `show`, `tree <id>`, `update` or `comment add`/`list` answers `{"ok": false, "error": {"type", "message"}}` with a non-zero `returncode`, using the error type real brd actually uses for that argv -- verified against this worktree's installed `brd`: `CardNotFoundError` for `show`, `tree <id>` and `update`; `EntityNotFoundError` for `comment add` and `comment list`. The result is that `_decode` raises `BoardError` with that `error_type`, as it does against real brd.
- `comment add` with an empty body answers an `ok: false` envelope, mirroring brd's rejection.
- Any argv FakeBoard does not recognise fails loudly (an `AssertionError` naming the argv). It never returns a guessed envelope.

## Out of scope

The following are out of scope:

- Moving `test_orchestrate.py` or `test_cli.py` onto FakeBoard (V4/V5).
- Any change to `board.py`'s public signatures, envelopes or behaviour.
- The seam itself (`cf9f8555`).
- Marker registration (`19b291e9`).
- Auto-marking, the PATH-shim/duration guard, and e2e cap consolidation (`b4edda6b`, `3202b0b0`, `3aa663b8`).
- xdist.
- The engine, checkpoint, harness, `dispatch.py`.
- Milestone 14.

## Tests

Tier placement follows `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V1: a test belongs to the tier of whatever it actually spawns.

| Test | Tier |
|---|---|
| FakeBoard pins against real brd `show`/`tree`/`comment list` for the same card tree (structural match as defined above) | `brd` (`@pytest.mark.brd`, because it spawns the real `brd` binary) |
| `board.show`/`tree`/`roots` through FakeBoard return the seeded `Card`/`CardNode` values, with nesting and order intact | `unit` (no marker; fake only, no subprocess) |
| `board.set_status` and `board.comment_add` through FakeBoard are recorded in order and visible to later `show`/`comment_list` | `unit` (no marker) |
| An unknown card id through FakeBoard raises `BoardError` with brd's error type; an empty comment body is rejected | `unit` (no marker) |
| An unrecognised argv makes FakeBoard fail loudly | `unit` (no marker) |

Unit-tier tests must stay well under the 0.5s per-test budget and must never touch a subprocess.

## Verification

- `uv run pytest -m brd` passes, including the new pinning test.
- `uv run pytest` (default) stays green, with the pinning test deselected and the FakeBoard unit tests running.

---

# FakeBoard and its real-brd pin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-memory `FakeBoard` that stands in for the `brd` subprocess behind `board.run_brd`, with unit tests for it, and one `brd`-tier test that pins its envelopes against the real `brd` binary.

**Architecture:** `FakeBoard` lives in `tests/conftest.py` with a `fake_board` fixture that installs it via `monkeypatch.setattr(board, "run_brd", fake)`. It is a callable `(argv, repo_dir, stdin) -> subprocess.CompletedProcess[str]` that matches the six argv shapes `board.py` builds with a `match` statement, answers brd's exact `{"ok", "data"}` / `{"ok": false, "error"}` envelopes (shapes copied from the installed brd's `views.card_detail`, `core._build_node`, `comments.Comment`), and records successful writes as tuples in `fake.writes`. Tests reach FakeBoard only through the fixture: under `--import-mode=importlib`, conftest names are not importable (see `tests/e2e/test_fake_claude.py:39-50`), so the write records are plain tuples, not conftest classes.

**Tech Stack:** Python 3.12, pytest (importlib mode), `uv`, the `brd` CLI installed as a uv tool.

**Spec:** `docs/superpowers/specs/task-add-fakeboard-and-pin-19b53ab3-design.md` (prepended verbatim above).

## Global Constraints

- Worktree: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-fakeboard-and-pin-19b53ab3`, branch `m15/task-add-fakeboard-and-pin-19b53ab3`, cut from `m15/task-add-the-run-brd-cf9f8555`. Every path below is relative to this worktree.
- No change to `src/agent_manager/board.py` at all: not its public signatures, envelopes, behaviour, nor the `run_brd` seam (owned by `cf9f8555`).
- Do not register markers or edit `addopts` in `pyproject.toml` by hand; take them from `m15/task-register-the-markers-19b291e9` (owned by `19b291e9`).
- Do not touch `tests/test_comments.py`'s module-private `FakeBoard`; the new one is unrelated.
- Unit-tier FakeBoard tests carry no marker, never spawn a subprocess, and stay under 0.5s each. The pinning test carries `@pytest.mark.brd`.
- The `run_brd` call convention is positional `run_brd(argv, repo_dir, stdin)`; `stdin` is the comment body for `comment add` and `None` everywhere else.
- Real brd's error types for an unknown id: `CardNotFoundError` for `show`, `tree <id>`, `update`; `EntityNotFoundError` for `comment add`, `comment list`. Exit code 1 on every `ok: false`.
- Verification commands: `uv run pytest -m brd` and `uv run pytest`.

## Review Focus

- A comment body or card description containing a `[[link]]`: real brd indexes it and `show` lists it under `refs`/`referenced_by`, while FakeBoard always answers `[]`. Expected: FakeBoard fails loudly with an `AssertionError` instead of silently answering a wrong `refs` list (tests in Task 2 and Task 3).
- A FakeBoard seeded with real brd timestamps (later than the fake's own clock) then written to: expected: a comment added afterwards still lists last and a status write still moves `updated_at` forward (test in Task 3).
- `board.set_status(card, "blocked")`: real brd refuses with `InvalidStatusError` because `blocked` is derived. Expected: FakeBoard refuses the same way, records no write, and derives `blocked` itself from unfinished blockers and blocked parents (tests in Tasks 2 and 3; pinned in Task 4).
- The same `set_status` issued twice (resume re-runs phases): expected: both succeed, both are recorded, the status is stored once (test in Task 3).
- Children seeded out of creation order: real brd orders children and roots by `created_at`, not insertion. Expected: FakeBoard does the same (test in Task 2).

---

### Task 1: Bring in the marker registration prerequisite

**Files:**
- Modify (by merge only): `pyproject.toml`

**Interfaces:**
- Consumes: the `run_brd` seam already on this branch (`src/agent_manager/board.py:168-181`), and branch `m15/task-register-the-markers-19b291e9`.
- Produces: the registered `brd` marker and `addopts = '--import-mode=importlib -m "not brd and not e2e_fake and not soak and not e2e" --durations=15 --durations-min=0.5'`, so `@pytest.mark.brd` tests are deselected by default and selected by `-m brd`.

- [ ] **Step 1: Confirm the seam prerequisite is present**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-fakeboard-and-pin-19b53ab3
git merge-base --is-ancestor 2cb9349 HEAD && git merge-base --is-ancestor aadfb0d HEAD && echo seam-present
grep -n '^run_brd' src/agent_manager/board.py
```
Expected: `seam-present`, and `168:run_brd: Callable[`. If either is missing, stop and escalate: this plan does not re-add the seam.

- [ ] **Step 2: Confirm the marker is not yet registered here**

Run: `grep -n '"brd:' pyproject.toml`
Expected: no output (exit 1).

- [ ] **Step 3: Merge the marker branch**

Run:
```bash
git merge --no-edit m15/task-register-the-markers-19b291e9
```
Expected: a clean merge touching `pyproject.toml`. If it conflicts in `pyproject.toml` only, resolve `[tool.pytest.ini_options]` by taking the `m15/task-register-the-markers-19b291e9` side verbatim (`git checkout --theirs pyproject.toml && git add pyproject.toml && git commit --no-edit`). If it conflicts in any other file, `git merge --abort` and escalate.

- [ ] **Step 4: Verify the marker and tier exclusion arrived**

Run: `grep -n '"brd:\|^addopts' pyproject.toml`
Expected:
```
36:    "brd: executes the real brd binary; opt-in (-m brd)",
48:addopts = '--import-mode=importlib -m "not brd and not e2e_fake and not soak and not e2e" --durations=15 --durations-min=0.5'
```

- [ ] **Step 5: Run the default suite**

Run: `uv run pytest -q`
Expected: PASS (green; no test carries `brd` yet, so nothing new is deselected).

---

### Task 2: FakeBoard core, seeding API, reads and the fixture

**Files:**
- Modify: `tests/conftest.py:9-14` (imports) and append after line 72
- Create: `tests/test_fake_board.py`

**Interfaces:**
- Consumes: `board.run_brd`, `board.show`, `board.tree`, `board.roots`, `board.show_argv`, `board.tree_argv`, `board.roots_argv`, `board.BoardError`, `models.Card`, `models.CardNode`.
- Produces (used by Tasks 3 and 4):
  - fixture `fake_board` -> a `FakeBoard` instance already installed as `board.run_brd`.
  - `FakeBoard.__call__(argv: Sequence[str], repo_dir: Path | None, stdin: str | None) -> subprocess.CompletedProcess[str]`
  - `FakeBoard.add_card(title: str, *, parent_id: str | None = None, card_id: str | None = None, status: str = "todo", description: str | None = None, blocked_by: Sequence[str] = (), created_at: str | None = None, updated_at: str | None = None) -> str`
  - `FakeBoard.add_comment(card_id: str, body: str, *, author: str = "am", comment_id: str | None = None, created_at: str | None = None) -> str`
  - `FakeBoard.writes: list[tuple[str, ...]]` (filled in Task 3)
  - private helpers Task 3 calls: `_answer`, `_now`, `_require_card`, `_detail`, `_comments_of`, `_comment_dict`, `_FakeBrdError`, `_FakeComment`, `_refuse_links`.

- [ ] **Step 1: Write the failing unit tests**

Create `tests/test_fake_board.py`:

```python
"""Behaviour of the conftest `FakeBoard`, the in-memory stand-in for the `brd` subprocess.

Unit tier (test-tier design §3 V1): every test here runs `board.py` through the
`fake_board` fixture, which replaces `board.run_brd`, so no subprocess is ever
spawned. FakeBoard's envelopes are pinned against the real binary by the
`brd`-tier test in `tests/test_board.py`.
"""

import json
import subprocess

import pytest

from agent_manager import board, models


def test_the_fixture_installs_the_fake_as_board_run_brd(fake_board):
    assert board.run_brd is fake_board


def test_fake_board_answers_a_real_completed_process(fake_board):
    card = fake_board.add_card("Add FakeBoard")

    completed = fake_board(board.show_argv(card), None, None)

    assert isinstance(completed, subprocess.CompletedProcess)
    assert completed.args == ["brd", "show", card]
    assert completed.returncode == 0
    assert completed.stderr == ""
    envelope = json.loads(completed.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["id"] == card


def test_show_through_fake_board_returns_the_seeded_card(fake_board):
    parent = fake_board.add_card("Milestone 1")
    child = fake_board.add_card(
        "Add FakeBoard", parent_id=parent, description="the fake"
    )

    card = board.show(child)

    assert isinstance(card, models.Card)
    assert (card.id, card.title, card.status, card.parent_id) == (
        child,
        "Add FakeBoard",
        "todo",
        parent,
    )
    assert card.description == "the fake"
    assert card.blocked_by == []
    assert isinstance(card.created_at, str) and card.created_at


def test_show_answers_brds_card_detail_shape(fake_board):
    parent = fake_board.add_card("Milestone 1")
    first = fake_board.add_card("first", parent_id=parent)
    second = fake_board.add_card("second", parent_id=parent)
    fake_board.add_comment(parent, "older")
    fake_board.add_comment(parent, "newer", author="paulo")

    data = json.loads(fake_board(board.show_argv(parent), None, None).stdout)["data"]

    assert set(data) == {
        "id", "kind", "title", "description", "status", "parent_id",
        "created_at", "updated_at", "blocked_by", "children", "comments",
        "refs", "referenced_by",
    }
    assert data["kind"] == "card"
    assert data["children"] == [first, second]
    assert data["refs"] == [] and data["referenced_by"] == []
    assert [(c["body"], c["author"]) for c in data["comments"]] == [
        ("older", "am"),
        ("newer", "paulo"),
    ]
    assert all(
        set(c) == {"id", "entity_id", "author", "body", "created_at"}
        and c["entity_id"] == parent
        for c in data["comments"]
    )


def test_tree_through_fake_board_nests_children_in_creation_order(fake_board):
    milestone = fake_board.add_card("Milestone 1")
    story = fake_board.add_card("story", parent_id=milestone)
    first = fake_board.add_card("first", parent_id=story)
    second = fake_board.add_card("second", parent_id=story)

    node = board.tree(milestone)

    assert isinstance(node, models.CardNode)
    assert node.id == milestone
    assert [child.id for child in node.children] == [story]
    assert [grand.id for grand in node.children[0].children] == [first, second]
    assert node.children[0].children[1].children == []


def test_tree_nodes_carry_brds_node_keys_and_no_parent_id(fake_board):
    milestone = fake_board.add_card("Milestone 1")
    fake_board.add_card("story", parent_id=milestone)

    data = json.loads(fake_board(board.tree_argv(milestone), None, None).stdout)["data"]

    assert len(data) == 1
    keys = {"id", "title", "description", "status", "blocked_by", "created_at",
            "updated_at", "children"}
    assert set(data[0]) == keys
    assert set(data[0]["children"][0]) == keys


def test_roots_through_fake_board_lists_every_top_level_card(fake_board):
    first = fake_board.add_card("Milestone 1")
    story = fake_board.add_card("story", parent_id=first)
    second = fake_board.add_card("Milestone 2")

    nodes = board.roots()

    assert [node.id for node in nodes] == [first, second]
    assert [child.id for child in nodes[0].children] == [story]
    assert nodes[1].children == []


def test_roots_of_an_empty_fake_board_is_an_empty_list(fake_board):
    assert board.roots() == []


def test_children_are_ordered_by_created_at_not_insertion(fake_board):
    parent = fake_board.add_card("parent", created_at="2026-01-01T00:00:00.000000+00:00")
    later = fake_board.add_card(
        "later", parent_id=parent, created_at="2026-03-01T00:00:00.000000+00:00"
    )
    earlier = fake_board.add_card(
        "earlier", parent_id=parent, created_at="2026-02-01T00:00:00.000000+00:00"
    )

    assert [child.id for child in board.tree(parent).children] == [earlier, later]
    raw = json.loads(fake_board(board.show_argv(parent), None, None).stdout)["data"]
    assert raw["children"] == [earlier, later]


def test_blocked_status_is_derived_like_brd(fake_board):
    blocker = fake_board.add_card("blocker")
    blocked = fake_board.add_card("blocked", blocked_by=[blocker])
    under_blocked = fake_board.add_card("under blocked", parent_id=blocked)
    finished = fake_board.add_card("finished", status="done")
    free = fake_board.add_card("free", blocked_by=[finished])

    assert board.show(blocker).status == "todo"
    assert board.show(blocked).status == "blocked"
    assert board.show(blocked).blocked_by == [blocker]
    assert board.show(under_blocked).status == "blocked"
    assert board.show(free).status == "todo"
    assert board.tree(blocked).status == "blocked"


def test_show_of_an_unknown_card_raises_brds_card_not_found(fake_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.show("no-such-card")
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.message == "no card, issue, or document with id no-such-card"
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == ["brd", "show", "no-such-card"]


def test_tree_of_an_unknown_card_raises_brds_card_not_found(fake_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.tree("no-such-card")
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.message == "no card with id no-such-card"
    assert excinfo.value.exit_code == 1


@pytest.mark.parametrize(
    ("argv", "stdin"),
    [
        (["brd", "list"], None),
        (["brd", "show"], None),
        (["brd", "show", "c1", "--pretty"], None),
        (["brd", "show", "c1"], "unexpected body"),
        (["brd", "tree", "c1", "c2"], None),
        (["git", "show", "c1"], None),
    ],
    ids=["unknown-subcommand", "show-without-id", "extra-flag", "stdin-on-a-read",
         "two-tree-ids", "not-brd"],
)
def test_an_unrecognised_argv_fails_loudly(fake_board, argv, stdin):
    with pytest.raises(AssertionError, match="FakeBoard does not answer argv"):
        fake_board(argv, None, stdin)


def test_seeding_under_an_unknown_parent_fails_loudly(fake_board):
    with pytest.raises(AssertionError, match="unknown parent"):
        fake_board.add_card("orphan", parent_id="no-such-card")


def test_seeding_a_description_with_a_link_fails_loudly(fake_board):
    # Real brd indexes [[links]] into refs; FakeBoard always answers refs: [].
    with pytest.raises(AssertionError, match=r"\[\[link\]\]"):
        fake_board.add_card("linked", description="see [[other-card]]")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fake_board.py -q`
Expected: FAIL / ERROR on every test with `fixture 'fake_board' not found`.

- [ ] **Step 3: Add the imports to `tests/conftest.py`**

Replace lines 11-14:

```python
import os
from pathlib import Path

import pytest
```

with:

```python
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
```

- [ ] **Step 4: Append FakeBoard and its fixture to `tests/conftest.py`**

Append after the last line (`session.exitstatus = pytest.ExitCode.TESTS_FAILED`):

```python


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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fake_board.py -q`
Expected: PASS (all tests; no duration line at or above 0.5s in the `--durations` report).

- [ ] **Step 6: Run the default suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_fake_board.py
git commit -m "test: add the conftest FakeBoard behind board.run_brd, with its reads"
```

---

### Task 3: FakeBoard writes, comment list, recording and write error paths

**Files:**
- Modify: `tests/conftest.py` (the `FakeBoard._answer` method from Task 2, and new methods after `FakeBoard._show`)
- Modify: `tests/test_fake_board.py` (append)

**Interfaces:**
- Consumes (from Task 2): `FakeBoard._answer`, `_now`, `_require_card`, `_detail`, `_comments_of`, `_comment_dict`, `_FakeBrdError`, `_FakeComment`, `_refuse_links`, `FakeBoard.writes`, fixture `fake_board`.
- Produces (used by Task 4): FakeBoard answers `["brd", "update", id, "--status", s]`, `["brd", "comment", "add", id, "-", "--author", a]` (body on stdin) and `["brd", "comment", "list", id]`; successful writes append `("set_status", id, s)` / `("comment_add", id, body, author)` to `writes`.

- [ ] **Step 1: Write the failing unit tests**

Append to `tests/test_fake_board.py`:

```python


def test_set_status_is_recorded_and_visible_to_a_later_show(fake_board):
    card = fake_board.add_card("Add FakeBoard")

    returned = board.set_status(card, "in_progress")

    assert isinstance(returned, models.Card)
    assert returned.status == "in_progress"
    assert board.show(card).status == "in_progress"
    assert fake_board.writes == [("set_status", card, "in_progress")]


def test_set_status_answers_brds_card_detail_and_moves_updated_at(fake_board):
    card = fake_board.add_card(
        "Add FakeBoard",
        created_at="2026-09-01T00:00:00.000000+00:00",
        updated_at="2026-09-01T00:00:00.000000+00:00",
    )

    completed = fake_board(board.set_status_argv(card, "done"), None, None)

    data = json.loads(completed.stdout)["data"]
    assert completed.returncode == 0
    assert data["kind"] == "card" and data["status"] == "done"
    assert data["updated_at"] > "2026-09-01T00:00:00.000000+00:00"
    assert data["created_at"] == "2026-09-01T00:00:00.000000+00:00"


def test_set_status_twice_succeeds_and_records_both(fake_board):
    # Resume re-runs whole phases, so the same transition can arrive twice.
    card = fake_board.add_card("Add FakeBoard")

    board.set_status(card, "done")
    board.set_status(card, "done")

    assert board.show(card).status == "done"
    assert fake_board.writes == [("set_status", card, "done"), ("set_status", card, "done")]


def test_comment_add_is_recorded_and_listed_oldest_first(fake_board):
    card = fake_board.add_card("Add FakeBoard")

    first = board.comment_add(card, "first")
    second = board.comment_add(card, "second", author="paulo")

    assert isinstance(first, str) and first
    assert first != second
    assert board.comment_list(card) == [
        board.BoardComment(id=first, body="first", author="am"),
        board.BoardComment(id=second, body="second", author="paulo"),
    ]
    assert fake_board.writes == [
        ("comment_add", card, "first", "am"),
        ("comment_add", card, "second", "paulo"),
    ]


def test_comment_add_answers_brds_comment_object(fake_board):
    card = fake_board.add_card("Add FakeBoard")

    completed = fake_board(board.comment_add_argv(card, "am"), None, "an outcome")

    data = json.loads(completed.stdout)["data"]
    assert set(data) == {"id", "entity_id", "author", "body", "created_at"}
    assert (data["entity_id"], data["author"], data["body"]) == (card, "am", "an outcome")


def test_writes_are_recorded_in_call_order_across_kinds(fake_board):
    card = fake_board.add_card("Add FakeBoard")

    board.set_status(card, "in_progress")
    board.comment_add(card, "started")
    board.set_status(card, "done")

    assert fake_board.writes == [
        ("set_status", card, "in_progress"),
        ("comment_add", card, "started", "am"),
        ("set_status", card, "done"),
    ]
    assert board.show(card).status == "done"


def test_a_comment_added_after_seeded_comments_lists_last(fake_board):
    # Seeded timestamps (e.g. copied from a real board) can be later than the
    # fake's own clock; a write afterwards must still sort last.
    card = fake_board.add_card("Add FakeBoard", created_at="2030-01-01T00:00:00.000000+00:00")
    fake_board.add_comment(card, "seeded", created_at="2030-01-02T00:00:00.000000+00:00")

    added = board.comment_add(card, "written")

    assert [c.body for c in board.comment_list(card)] == ["seeded", "written"]
    assert board.comment_list(card)[-1].id == added


def test_a_comment_body_reaches_the_fake_byte_for_byte(fake_board):
    card = fake_board.add_card("Add FakeBoard")
    body = "  leading space\n$(touch pwned)\n`echo x`\n\ttab, accents é, check ✓\n"

    board.comment_add(card, body)

    assert [c.body for c in board.comment_list(card)] == [body]


def test_comment_list_of_a_card_with_no_comments_is_empty(fake_board):
    card = fake_board.add_card("Add FakeBoard")
    other = fake_board.add_card("Other card")
    fake_board.add_comment(other, "not mine")

    assert board.comment_list(card) == []


@pytest.mark.parametrize(
    ("call", "error_type", "message"),
    [
        (lambda: board.set_status("no-such-card", "done"), "CardNotFoundError",
         "no card with id no-such-card"),
        (lambda: board.comment_add("no-such-card", "orphan"), "EntityNotFoundError",
         "no entity with id no-such-card"),
        (lambda: board.comment_list("no-such-card"), "EntityNotFoundError",
         "no entity with id no-such-card"),
    ],
    ids=["set_status", "comment_add", "comment_list"],
)
def test_an_unknown_card_raises_board_error_with_brds_error_type(
    fake_board, call, error_type, message
):
    with pytest.raises(board.BoardError) as excinfo:
        call()
    assert excinfo.value.error_type == error_type
    assert excinfo.value.message == message
    assert excinfo.value.exit_code == 1
    assert fake_board.writes == []


@pytest.mark.parametrize("body", ["", "  \n\t"], ids=["empty", "whitespace"])
def test_an_empty_comment_body_is_rejected_and_not_recorded(fake_board, body):
    card = fake_board.add_card("Add FakeBoard")

    with pytest.raises(board.BoardError) as excinfo:
        board.comment_add(card, body)

    assert excinfo.value.error_type == "EmptyCommentError"
    assert excinfo.value.message == "comment body is empty"
    assert excinfo.value.exit_code == 1
    assert board.comment_list(card) == []
    assert fake_board.writes == []


def test_set_status_blocked_is_refused_like_brd(fake_board):
    card = fake_board.add_card("Add FakeBoard")

    with pytest.raises(board.BoardError) as excinfo:
        board.set_status(card, "blocked")

    assert excinfo.value.error_type == "InvalidStatusError"
    assert "blocked" in excinfo.value.message
    assert board.show(card).status == "todo"
    assert fake_board.writes == []


def test_a_comment_body_with_a_link_fails_loudly(fake_board):
    card = fake_board.add_card("Add FakeBoard")
    with pytest.raises(AssertionError, match=r"\[\[link\]\]"):
        board.comment_add(card, "see [[other-card]]")
    assert fake_board.writes == []


@pytest.mark.parametrize(
    ("argv", "stdin"),
    [
        (["brd", "comment", "add", "c1", "-", "--author", "am"], None),
        (["brd", "comment", "add", "c1", "inline body", "--author", "am"], None),
        (["brd", "comment", "add", "c1", "-"], "body"),
        (["brd", "update", "c1", "--status", "done"], "body"),
        (["brd", "update", "c1", "--title", "renamed"], None),
        (["brd", "comment", "list", "c1"], "body"),
    ],
    ids=["add-without-stdin", "add-inline-body", "add-without-author",
         "update-with-stdin", "update-title", "list-with-stdin"],
)
def test_a_write_argv_board_py_never_builds_fails_loudly(fake_board, argv, stdin):
    with pytest.raises(AssertionError, match="FakeBoard does not answer argv"):
        fake_board(argv, None, stdin)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fake_board.py -q`
Expected: FAIL. The new write/list tests fail with `AssertionError: FakeBoard does not answer argv ['brd', 'update', ...]` (or `['brd', 'comment', ...]`), propagated out of `board.set_status`/`comment_add`/`comment_list`; the Task 2 tests and `test_a_write_argv_board_py_never_builds_fails_loudly` still pass.

- [ ] **Step 3: Replace `FakeBoard._answer` in `tests/conftest.py`**

Replace the whole `_answer` method from Task 2 with:

```python
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
```

- [ ] **Step 4: Add the write methods to `FakeBoard`**

Insert directly after the `_show` method (before `_children_of`):

```python
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
        _refuse_links(body, "comment add body")
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fake_board.py -q`
Expected: PASS (all tests, each well under 0.5s).

- [ ] **Step 6: Run the default suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_fake_board.py
git commit -m "test: let FakeBoard answer status and comment writes, recording each"
```

---

### Task 4: Pin FakeBoard against the real brd binary (`brd` tier)

**Files:**
- Modify: `tests/test_board.py` (append after line 1229, the end of `test_every_public_function_runs_brd_through_the_run_brd_seam`)

**Interfaces:**
- Consumes: fixtures `temp_board` (`tests/test_board.py:241-261`) and `fake_board` (conftest, Tasks 2-3); helpers `_add_card`, `_brd_json` (`tests/test_board.py:264-278`); `requires_brd` (`tests/test_board.py:22-25`); `FakeBoard.add_card(...)` and `FakeBoard.__call__(argv, repo_dir, stdin)`; argv builders `board.show_argv`, `tree_argv`, `roots_argv`, `set_status_argv`, `comment_add_argv`, `comment_list_argv`.
- Produces: `test_fake_board_answers_every_argv_like_real_brd_for_the_same_card_tree`, marker `brd`.

- [ ] **Step 1: Write the pinning test**

Append to `tests/test_board.py`:

```python


_VOLATILE_KEYS = frozenset({"id", "created_at", "updated_at"})
"""Keys whose string values brd generates (uuid4s, now() timestamps); checked by type only."""


def _assert_same_shape(fake: object, real: object, where: str) -> None:
    """FakeBoard's JSON matches real brd's: same key sets, value types, list
    lengths and order at every level, and equal values everywhere except a
    generated id or timestamp, which only has to be a non-empty string."""
    assert type(fake) is type(real), (
        f"{where}: fake {type(fake).__name__} != real {type(real).__name__}"
    )
    if isinstance(real, dict):
        assert set(fake) == set(real), f"{where}: keys {sorted(fake)} != {sorted(real)}"
        for key, real_value in real.items():
            if key in _VOLATILE_KEYS and isinstance(real_value, str):
                assert isinstance(fake[key], str) and fake[key], (
                    f"{where}.{key}: {fake[key]!r} is not a non-empty string"
                )
            else:
                _assert_same_shape(fake[key], real_value, f"{where}.{key}")
    elif isinstance(real, list):
        assert len(fake) == len(real), f"{where}: {len(fake)} items != {len(real)}"
        for index, (fake_item, real_item) in enumerate(zip(fake, real)):
            _assert_same_shape(fake_item, real_item, f"{where}[{index}]")
    else:
        assert fake == real, f"{where}: fake {fake!r} != real {real!r}"


@pytest.mark.brd
@requires_brd
def test_fake_board_answers_every_argv_like_real_brd_for_the_same_card_tree(
    temp_board, fake_board
):
    # Test-tier V3: the conftest FakeBoard is the unit tier's brd, so it is
    # pinned against the real binary -- the same "conftest twin" idea as
    # tests/e2e/test_fake_claude.py:39-50 -- and cannot drift silently.
    parent = _add_card(temp_board, "Milestone 1")
    child = _add_card(temp_board, "Add FakeBoard", parent)
    sibling = _add_card(temp_board, "Pin it against real brd", parent)
    _brd_json(temp_board, "block", sibling, "--by", child)

    # Seed the fake from the real board's own ids and fields, oldest first.
    for card_id in (parent, child, sibling):
        raw = _brd_json(temp_board, "show", card_id)
        fake_board.add_card(
            raw["title"],
            card_id=raw["id"],
            parent_id=raw["parent_id"],
            description=raw["description"],
            blocked_by=raw["blocked_by"],
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
        )

    exchanges: list[tuple[list[str], str | None]] = [
        # Writes first, on both boards, so the reads below see them.
        (board.set_status_argv(child, "in_progress"), None),
        (board.comment_add_argv(child, "am"), "first outcome\nwith a second line\n"),
        # Reads: show (with the comment embedded), tree, roots, comment list.
        (board.show_argv(parent), None),
        (board.show_argv(child), None),
        (board.show_argv(sibling), None),
        (board.tree_argv(parent), None),
        (board.tree_argv(sibling), None),
        (board.roots_argv(), None),
        (board.comment_list_argv(child), None),
        (board.comment_list_argv(parent), None),
        # Error paths: brd's error type and message, verbatim, with exit 1.
        (board.show_argv("no-such-card"), None),
        (board.tree_argv("no-such-card"), None),
        (board.set_status_argv("no-such-card", "done"), None),
        (board.set_status_argv(child, "blocked"), None),
        (board.comment_add_argv("no-such-card", "am"), "orphan"),
        (board.comment_add_argv(child, "am"), "  \n\t"),
        (board.comment_list_argv("no-such-card"), None),
    ]

    for argv, stdin in exchanges:
        where = " ".join(argv)
        real = subprocess.run(
            argv, cwd=temp_board, capture_output=True, text=True, input=stdin
        )
        fake = fake_board(argv, temp_board, stdin)
        assert fake.returncode == real.returncode, (
            f"{where}: fake exit {fake.returncode} != real {real.returncode} "
            f"(real stdout {real.stdout!r}, stderr {real.stderr!r})"
        )
        _assert_same_shape(json.loads(fake.stdout), json.loads(real.stdout), where)

    # The writes reached the fake as recorded entries, in order.
    assert fake_board.writes == [
        ("set_status", child, "in_progress"),
        ("comment_add", child, "first outcome\nwith a second line\n", "am"),
    ]
```

- [ ] **Step 2: Run the pinning test against real brd**

Run: `uv run pytest -m brd tests/test_board.py::test_fake_board_answers_every_argv_like_real_brd_for_the_same_card_tree -v`
Expected: PASS. If it fails, the assertion names the argv and JSON path where FakeBoard differs from real brd; fix `FakeBoard` in `tests/conftest.py` to match brd (never loosen `_assert_same_shape` or drop an exchange), then rerun `uv run pytest tests/test_fake_board.py -q` too.

- [ ] **Step 3: Prove the pin catches drift (RED by mutation)**

In `tests/conftest.py`, inside `FakeBoard._detail`, temporarily change `"kind": "card",` to `"kind": "Card",`.

Run: `uv run pytest -m brd tests/test_board.py::test_fake_board_answers_every_argv_like_real_brd_for_the_same_card_tree -q`
Expected: FAIL with `brd update ... --status in_progress.kind: fake 'Card' != real 'card'`.

Then temporarily change the `EntityNotFoundError` message in `FakeBoard._require_entity` from `f"no entity with id {entity_id}"` to `f"no card with id {entity_id}"`, revert the `kind` change, and rerun the same command.
Expected: FAIL with `brd comment add no-such-card - --author am.error.message: fake 'no card with id no-such-card' != real 'no entity with id no-such-card'`.

Revert both mutations: `git diff tests/conftest.py` must print nothing.

- [ ] **Step 4: Run the whole brd tier**

Run: `uv run pytest -m brd -v`
Expected: PASS, with `test_fake_board_answers_every_argv_like_real_brd_for_the_same_card_tree` among the selected tests.

- [ ] **Step 5: Run the default suite and check the tiering**

Run: `uv run pytest -q`
Expected: PASS, with the pinning test deselected (the summary's `deselected` count includes it) and every `tests/test_fake_board.py` test run.

Run: `uv run pytest tests/test_board.py -q -k fake_board_answers`
Expected: `1 deselected` and no tests run (the default `addopts` excludes `brd`).

- [ ] **Step 6: Commit**

```bash
git add tests/test_board.py
git commit -m "test(board): pin FakeBoard's envelopes against the real brd binary"
```
