"""e2e_fake tier: the `brd` shim behind the board-down scenario (card 649a8e88).

Board-comments design B8 and its §6 "Testing". The outcome-comment scenarios
(clean run, escalation and `am resume`, cancel, a run during which
`brd comment` was down) are proven through `FakeDriver` in
tests/test_orchestrate.py (test-tier V6). What stays here is the
real-subprocess proof that a `brd` first on `PATH` which fails only
`brd comment ...` calls surfaces as `BoardError` through the real `board`
module, against a real temporary board (`milestone_board`), while card reads
and status writes still go through.
"""

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_manager import board

KEY_LINE = "am-key: "
"""How every outcome comment's last line starts (board-comments B5)."""

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


def _on(root: Path, card_id: str) -> list[board.BoardComment]:
    """`brd comment list <card>`, oldest first, through the real board module."""
    return board.comment_list(card_id, repo_dir=root)


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


def test_no_brd_shim_is_left_armed_for_later_tests():
    """Review Focus 2: the shim's env var and `PATH` entry are the test's own
    `monkeypatch`, so both are gone once its test ends. Kept last in the module."""
    assert os.environ.get(BRD_FAIL_COMMENTS_ENV) is None
    found = shutil.which("brd")
    assert found is None or Path(found).parent.name != "brd-shim", found
