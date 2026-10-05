"""e2e_fake: one story run end to end, refused, walked, detached, paused and resumed (card 6d6ea99c).

Production wiring under the fake `claude`, with real git and brd, on the
conftest's `milestone_board`: A (a1 -> a2), B blocked by A (b1), C blocked
by B (c1). `am run --story` runs through `CliRunner` on the real `cli.app`
with no runner factory and no driver, or as real `am` child processes for
the detached run. A story with an open blocker is refused and nothing is
written; a story run drives only its own story's subtasks and never
integrates; the next story roots on its done blocker's tip; a detached
story run pauses, and `am resume` with no `--story` finishes that story
alone under the same run id.

Order comes from the hold marker files, the pause row's `handled_at` (read
through `am status`), `report.json` and the lease row, never from sleeps.
Every wait is bounded and fails naming its step.
"""

import json
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, paths, store

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: the board fixture derives its branches with it."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the `m3` prefix; a story run never creates it."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _error(result) -> dict:
    """The `error` of brd's failure envelope, `{"type": ..., "message": ...}`."""
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False, envelope
    return envelope["error"]


def _run_ids(root: Path) -> list[str]:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return [row["id"] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _all_cards(shape: Mapping[str, Any]) -> list[str]:
    """Every card of the board fixture: subtasks, stories, then the milestone."""
    return [
        *(card for chain in shape["subtasks"].values() for card in chain),
        *shape["stories"].values(),
        shape["milestone"],
    ]


def _statuses(root: Path, cards: Iterable[str]) -> dict[str, str]:
    """Each card's brd status, keyed by card id."""
    return {card: board.show(card, repo_dir=root).status for card in cards}


def _run_story(root: Path, story: str):
    """`am run --story STORY` through `CliRunner`, with no runner factory anywhere."""
    return CliRunner().invoke(
        cli.app,
        [
            "run",
            "--story",
            story,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            PREFIX,
            "--verify",
            VERIFY,
        ],
    )


@pytest.mark.e2e_fake
def test_a_story_with_an_open_blocker_is_refused_before_anything_is_written(
    milestone_board, fake_claude_bin
):
    """A story whose blocker is still open is refused with `StoryBlockedError`
    and exit 3, naming both stories, and writes nothing: no run row, no run
    directory, no branch, no card status change."""
    root = milestone_board["root"]
    stories = milestone_board["stories"]
    cards = _all_cards(milestone_board)
    before = _statuses(root, cards)
    main_before = _git(root, "rev-parse", "main").strip()

    result = _run_story(root, stories["B"])

    assert result.exit_code == cli.EXIT_ERROR, (result.output, result.exception)
    refusal = _error(result)
    assert refusal["type"] == "StoryBlockedError", refusal
    assert stories["A"] in refusal["message"], refusal
    assert stories["B"] in refusal["message"], refusal
    runs_dir = paths.data_dir() / "runs"
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == [], list(runs_dir.iterdir())
    assert _run_ids(root) == []
    assert _local_branches(root) == ["main"]
    assert _git(root, "rev-parse", "main").strip() == main_before
    assert _statuses(root, cards) == before
