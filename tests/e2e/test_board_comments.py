"""Default-suite e2e tier: outcome comments on a real brd board (card 649a8e88).

Board-comments design B2, B5, B7, B8, B9 and its §6 "Testing": under the fake
`claude` against a real temporary board, `brd comment list` after a clean run,
after an escalation and `am resume`, after a cancel, and after a run during
which `brd comment` was down. Every launch goes through `CliRunner` on the
real `cli.app` with no `runner_factory`, so the real `ClaudeAdapter` reaches
the fake `claude` first on `PATH`. Comments are read back through the real
`board.comment_list`, never through the store's outbox, except where the
board-down scenario checks what the outbox still owes.

Each test builds its own board (`milestone_board`): a1 -> a2 in story A,
B (b1) blocked by A, C (c1) blocked by B. Unmarked on purpose: it must run on
every `uv run pytest`.
"""

import json
import os
import re
import shlex
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, comments, control, store
from agent_manager.harness import launcher

KEY_LINE = "am-key: "
"""How every outcome comment's last line starts (board-comments B5)."""

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

RESUMED_LINE = re.compile(r"\(resumed at [a-z_]+\)")
"""A resumed done's second line: where the walk picked up (`compose_done`)."""


def _resume(root: Path, run_id: str):
    """`am resume <run-id>`: no prefix, base or bound, only what the record lacks."""
    return CliRunner().invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(root), "--verify", VERIFY]
    )


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


def test_clean_milestone_run_posts_one_done_per_subtask_and_one_run_end(
    milestone_board, run_milestone_cli
):
    """Spec scenario 1: one `am · done` per subtask, one run-end on the
    milestone, nothing on any story."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    branches = milestone_board["branches"]

    result = run_milestone_cli(root, milestone)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    run_id = data["run_id"]
    assert _comment_warnings(data) == []

    for card in _subtasks(milestone_board):
        (done,) = _on(root, card)
        lines = _assert_shape(
            done, outcome="done", run_id=run_id, key=comments.key(run_id, card, "done")
        )
        assert f"branch: {branches[card]}" in lines, done.body
        assert not any(line.startswith("(resumed at") for line in lines), done.body

    for story in milestone_board["stories"].values():
        assert _on(root, story) == [], story

    (end,) = _on(root, milestone)
    lines, _key = _assert_scoped(
        end,
        outcome="done",
        run_id=run_id,
        prefix=comments.key(run_id, milestone, "run-end:"),
    )
    assert "done: 4 of 4" in lines, end.body
    assert f"integrated: {INTEGRATION_BRANCH}" in lines, end.body
    assert f"next: `git merge {INTEGRATION_BRANCH}`" in lines, end.body


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_milestone_run.py`'s guard: no `e2e` marker may reach this
    module, or outcome comments stop being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_escalation_then_resume_keeps_escalation_and_appends_resumed_done(
    milestone_board, review_fail_marker, run_milestone_cli
):
    """Spec scenario 2: b1's review escalates; its escalation comment quotes
    only `unresolved_blockers` and stays after `am resume`, which appends
    exactly one `done (resumed at …)`. The milestone gets one run-end per
    life, with distinct lease-token-scoped keys (B2, B5, B9)."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    branches = milestone_board["branches"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    review_fail_marker.write_text(f"{branches[b1]}\n", encoding="utf-8")

    first = run_milestone_cli(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["subtask"] == b1, stopped
    assert stopped["failed_phase"] == "review", stopped
    run_id = stopped["run_id"]

    (escalation,) = _on(root, b1)
    escalation_lines, escalation_key = _assert_scoped(
        escalation,
        outcome="escalated",
        run_id=run_id,
        prefix=comments.key(run_id, b1, "escalated:"),
    )
    assert "phase: review" in escalation_lines, escalation.body
    # Only the review's own field, quoted (B3): the fake's one unresolved blocker.
    assert [line for line in escalation_lines if line.startswith("reason: ")] == [
        f'reason: "the review-fail marker names {branches[b1]}"'
    ], escalation.body
    assert "[[" not in escalation.body, escalation.body
    assert f"next: `am resume {run_id}`" in escalation_lines, escalation.body

    first_done = {}
    for card in (a1, a2):
        (done,) = _on(root, card)
        _assert_shape(done, outcome="done", run_id=run_id, key=comments.key(run_id, card, "done"))
        first_done[card] = done.id
    assert _on(root, c1) == []  # never started: no comment of any kind
    for story in milestone_board["stories"].values():
        assert _on(root, story) == [], story

    (first_end,) = _on(root, milestone)
    first_end_lines, first_end_key = _assert_scoped(
        first_end,
        outcome="escalated",
        run_id=run_id,
        prefix=comments.key(run_id, milestone, "run-end:"),
    )
    assert f"escalated: [[{b1}]] at review" in first_end_lines, first_end.body
    assert f"next: `am resume {run_id}`" in first_end_lines, first_end.body

    # Fix the fake, then resume the same run.
    review_fail_marker.unlink()
    resumed = _resume(root, run_id)

    assert resumed.exit_code == 0, (resumed.output, resumed.exception)
    data = _envelope(resumed)
    assert data["done"] is True, data
    assert data["resumed"] is True, data
    assert data["run_id"] == run_id
    assert sorted(data["completed"]) == sorted([b1, c1]), data

    # The escalation stays (never deleted, B2); one resumed done follows (B9).
    after = _on(root, b1)
    assert len(after) == 2, [comment.body for comment in after]
    assert (after[0].id, after[0].body) == (escalation.id, escalation.body)
    done_lines = _assert_shape(
        after[1], outcome="done", run_id=run_id, key=comments.key(run_id, b1, "done")
    )
    assert RESUMED_LINE.fullmatch(done_lines[1]), after[1].body

    (c1_done,) = _on(root, c1)
    c1_lines = _assert_shape(
        c1_done, outcome="done", run_id=run_id, key=comments.key(run_id, c1, "done")
    )
    assert not any(line.startswith("(resumed at") for line in c1_lines), c1_done.body
    for card in (a1, a2):
        assert [comment.id for comment in _on(root, card)] == [first_done[card]], card

    ends = _on(root, milestone)
    assert len(ends) == 2, [comment.body for comment in ends]
    assert ends[0].id == first_end.id
    _lines, second_end_key = _assert_scoped(
        ends[1],
        outcome="done",
        run_id=run_id,
        prefix=comments.key(run_id, milestone, "run-end:"),
    )
    assert second_end_key != first_end_key  # a new life, a new lease token (B5)
    assert escalation_key != comments.key(run_id, b1, "done")

    every_key = [_key_of(comment) for card in _all_cards(milestone_board) for comment in _on(root, card)]
    assert len(every_key) == len(set(every_key)), every_key
