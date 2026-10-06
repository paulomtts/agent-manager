"""e2e_fake tier: outcome comments on a real brd board while `brd comment` is down (card 649a8e88).

Board-comments design B7, B8, B9 and its §6 "Testing". The clean-run,
escalation-and-`am resume` and cancel comment scenarios are proven through
`FakeDriver` in tests/test_orchestrate.py (test-tier V6). What stays here is
the real-subprocess proof that a `brd` first on `PATH` which fails only
`brd comment ...` calls surfaces as `BoardError` through the real `board`
module, and the board-down scenario: a run under the fake `claude` during
which `brd comment` was down, read back through the real
`board.comment_list` and the store's outbox. It is kept because it alone
asserts the retry warning's `(attempt 1 of N)` wording and that a second
relaunch posts nothing twice.

Each test builds its own board (`milestone_board`): a1 -> a2 in story A,
B (b1) blocked by A, C (c1) blocked by B.
"""

import json
import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_manager import board, cli, comments, store
from agent_manager.store import db as store_db
from agent_manager.store import queries as store_queries

KEY_LINE = "am-key: "
"""How every outcome comment's last line starts (board-comments B5)."""

def _run_status(root: Path, run_id: str) -> str:
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        run = store_queries.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run.status



BRD_FAIL_COMMENTS_ENV = "AM_E2E_BRD_FAIL_COMMENTS"
"""Set (to anything non-empty) and the shim fails every `brd comment ...` call.
Must equal the variable name written into `BRD_SHIM`."""

BRD_SHIM = """#!/bin/sh
# Test scaffolding (card 649a8e88): the board is "down" for comments only.
if [ -n "${AM_E2E_BRD_FAIL_COMMENTS:-}" ] && [ "$1" = "comment" ]; then
    echo "brd shim: the board is down for comments" >&2
    exit 1
fi
exec @REAL_BRD@ "$@"
"""
"""A `brd` that exits 1 with stderr only (what `board._run` turns into
`BoardError`) on `brd comment ...` while the env var is set, and otherwise
`exec`s the real `brd` with the same argv and stdin. Card reads and status
writes always pass, so a run can proceed (B8 makes only comments best-effort)."""


@dataclass
class BrdShim:
    """Switches the `brd` shim between down (comments fail) and up.

    The env var goes through the test's own function-scoped `monkeypatch`,
    so it is undone when the test ends. `am` and `brd` children inherit it:
    `board._run` passes no `env=`.
    """

    path: Path
    monkeypatch: pytest.MonkeyPatch

    def down(self) -> None:
        self.monkeypatch.setenv(BRD_FAIL_COMMENTS_ENV, "1")

    def up(self) -> None:
        self.monkeypatch.delenv(BRD_FAIL_COMMENTS_ENV, raising=False)


@pytest.fixture
def brd_shim(tmp_path, monkeypatch, toolchain, fake_claude_bin) -> BrdShim:
    """The shim, first on `PATH` (ahead of the fake `claude`'s dir too), up.

    The real `brd` is resolved before the shim's dir is on `PATH` and baked
    into the script, so the shim can never call itself. The `PATH` change is
    the test's own `monkeypatch`, undone at teardown.
    """
    real = shutil.which("brd")
    assert real is not None
    bin_dir = tmp_path / "brd-shim"
    bin_dir.mkdir()
    shim = bin_dir / "brd"
    shim.write_text(BRD_SHIM.replace("@REAL_BRD@", shlex.quote(real)), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    switch = BrdShim(path=shim, monkeypatch=monkeypatch)
    switch.up()
    return switch


def _envelope(result) -> dict[str, Any]:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _subtasks(shape: dict[str, Any]) -> list[str]:
    """Every subtask of the board, census order: a1, a2, b1, c1."""
    return [card for chain in shape["subtasks"].values() for card in chain]


def _all_cards(shape: dict[str, Any]) -> list[str]:
    return [*_subtasks(shape), *shape["stories"].values(), shape["milestone"]]


def _on(root: Path, card_id: str) -> list[board.BoardComment]:
    """`brd comment list <card>`, oldest first, through the real board module."""
    return board.comment_list(card_id, repo_dir=root)


def _key_of(comment: board.BoardComment) -> str:
    """The comment's `am-key`, read off its last non-blank line."""
    last = comment.body.rstrip().splitlines()[-1]
    assert last.startswith(KEY_LINE), comment.body
    return last.removeprefix(KEY_LINE)


def _assert_shape(
    comment: board.BoardComment, *, outcome: str, run_id: str, key: str
) -> list[str]:
    """The four checks every outcome comment must pass; its lines for more."""
    lines = comment.body.rstrip().splitlines()
    assert comment.author == "am", comment
    assert lines[0] == f"am · {outcome} · run {run_id}", comment.body
    assert lines[-1] == f"{KEY_LINE}{key}", comment.body
    assert len(comment.body) <= comments.CAP, len(comment.body)
    return lines


def _assert_scoped(
    comment: board.BoardComment, *, outcome: str, run_id: str, prefix: str
) -> tuple[list[str], str]:
    """`_assert_shape` for a lease-token-scoped key (B5): `prefix` is
    `comments.key(run_id, card, "<event>:")` and the token after it is non-empty."""
    found = _key_of(comment)
    assert found.startswith(prefix) and len(found) > len(prefix), (found, prefix)
    return _assert_shape(comment, outcome=outcome, run_id=run_id, key=found), found


def _comment_warnings(payload: dict[str, Any]) -> list[str]:
    """The payload warnings `comments._warning` wrote, one per failed post."""
    return [warning for warning in payload["warnings"] if warning.startswith("board comment ")]


def test_the_brd_shim_fails_only_comment_calls_while_down(milestone_board, brd_shim):
    """Scaffolding check: while down, `brd comment list/add` fail the way a
    dead board does (`BoardError`) and nothing is posted; card reads and a
    status write still go through; once up, comments work again."""
    root = milestone_board["root"]
    a1 = milestone_board["subtasks"]["A"][0]
    assert Path(shutil.which("brd")) == brd_shim.path

    brd_shim.down()
    with pytest.raises(board.BoardError):
        board.comment_list(a1, repo_dir=root)
    with pytest.raises(board.BoardError):
        board.comment_add(a1, f"shim check\n{KEY_LINE}shim/{a1}/check", repo_dir=root)
    status = board.show(a1, repo_dir=root).status
    assert board.set_status(a1, status, repo_dir=root).status == status
    assert board.tree(milestone_board["milestone"], repo_dir=root).id == milestone_board["milestone"]

    brd_shim.up()
    assert _on(root, a1) == []  # the add while down never reached the board
    assert os.environ.get(BRD_FAIL_COMMENTS_ENV) is None


def _outbox(root: Path, run_id: str) -> dict[str, tuple[str, str, int]]:
    """`run_id`'s outbox rows, key -> (card id, state, failed attempts).

    Read on a fresh connection, like `am status`. Only the board-down
    scenario reads it, to show what the board still owes; every other
    assertion reads the board itself.
    """
    conn = store_db.open_db(cli.resolve_repo_dir(root))
    try:
        rows = conn.execute(
            "SELECT key, card_id, state, failed_attempts FROM board_comments"
            " WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    return {row[0]: (row[1], row[2], int(row[3])) for row in rows}


def test_board_down_run_ends_done_with_warnings_and_next_life_flushes(
    milestone_board, brd_shim, run_milestone_cli
):
    """Spec scenario 4 (B7, B8, B9): with `brd comment` down the run still
    ends done, exit 0, with one warning per unposted key and every row
    failed once. A relaunch (the next life; `am resume` refuses a done run)
    posts them with the clean-run shape, and a second relaunch posts none of
    them again."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    branches = milestone_board["branches"]
    order = _subtasks(milestone_board)

    brd_shim.down()
    down = run_milestone_cli(root, milestone)
    brd_shim.up()

    # A board failure changes nothing about the run (B8).
    assert down.exit_code == 0, (down.output, down.exception)
    data = _envelope(down)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert data["completed"] == order
    run_id = data["run_id"]
    assert _run_status(root, run_id) == "done"

    # What the outbox still owes: one done per subtask and one run-end, each
    # failed exactly once, so none is near abandonment.
    owed = _outbox(root, run_id)
    end_prefix = comments.key(run_id, milestone, "run-end:")
    end_keys = [key for key in owed if key.startswith(end_prefix)]
    assert len(end_keys) == 1, owed
    done_keys = {comments.key(run_id, card, "done"): card for card in order}
    assert set(owed) == {*done_keys, *end_keys}, owed
    warnings = _comment_warnings(data)
    assert len(warnings) == len(owed), warnings
    for key, (card, state, attempts) in owed.items():
        assert (state, attempts) == ("pending", 1), (key, state, attempts)
        assert attempts < store.COMMENT_ATTEMPTS
        named = [warning for warning in warnings if f"board comment {key} on card {card} " in warning]
        assert len(named) == 1, (key, warnings)
        assert f"(attempt 1 of {store.COMMENT_ATTEMPTS})" in named[0], named[0]

    # Same statuses as the clean run, and nothing on the board yet.
    for card in _all_cards(milestone_board):
        assert board.show(card, repo_dir=root).status == "done", card
        assert _on(root, card) == [], card

    # The next life: a relaunch's start-of-run flush (orchestrate.py:1599).
    relaunch = run_milestone_cli(root, milestone)

    assert relaunch.exit_code == 0, (relaunch.output, relaunch.exception)
    later = _envelope(relaunch)
    assert later["done"] is True, later
    assert later["completed"] == [], later
    later_id = later["run_id"]
    assert later_id != run_id
    assert _comment_warnings(later) == []
    assert {state for _card, state, _attempts in _outbox(root, run_id).values()} == {"posted"}

    for card in order:
        (done,) = _on(root, card)
        lines = _assert_shape(
            done, outcome="done", run_id=run_id, key=comments.key(run_id, card, "done")
        )
        assert f"branch: {branches[card]}" in lines, done.body
    for story in milestone_board["stories"].values():
        assert _on(root, story) == [], story
    ends = _on(root, milestone)
    assert len(ends) == 2, [comment.body for comment in ends]
    _lines, flushed_end_key = _assert_scoped(
        ends[0], outcome="done", run_id=run_id, prefix=end_prefix
    )
    assert flushed_end_key == end_keys[0]
    _assert_scoped(
        ends[1],
        outcome="done",
        run_id=later_id,
        prefix=comments.key(later_id, milestone, "run-end:"),
    )

    # The same entry point again: nothing of the earlier runs is posted twice;
    # only the new run's own run-end is added to the milestone card.
    before = {card: [comment.id for comment in _on(root, card)] for card in _all_cards(milestone_board)}
    again = run_milestone_cli(root, milestone)

    assert again.exit_code == 0, (again.output, again.exception)
    third = _envelope(again)
    assert third["done"] is True, third
    assert _comment_warnings(third) == []
    for card in [*order, *milestone_board["stories"].values()]:
        assert [comment.id for comment in _on(root, card)] == before[card], card
    final = _on(root, milestone)
    assert [comment.id for comment in final[:2]] == before[milestone]
    assert len(final) == 3, [comment.body for comment in final]
    _assert_scoped(
        final[2],
        outcome="done",
        run_id=third["run_id"],
        prefix=comments.key(third["run_id"], milestone, "run-end:"),
    )
    every_key = [_key_of(comment) for card in _all_cards(milestone_board) for comment in _on(root, card)]
    assert len(every_key) == len(set(every_key)), every_key


def test_no_brd_shim_is_left_armed_for_later_tests():
    """Review Focus 2: the shim's env var and `PATH` entry are the test's own
    `monkeypatch`, so both are gone once its test ends. Kept last in the module."""
    assert os.environ.get(BRD_FAIL_COMMENTS_ENV) is None
    found = shutil.which("brd")
    assert found is None or Path(found).parent.name != "brd-shim", found
