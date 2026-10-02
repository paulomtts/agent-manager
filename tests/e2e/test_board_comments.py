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


HELD_PHASE = "plan"
"""The phase whose launch is held while the cancel is sent."""

WAIT = 120.0
"""Seconds any bounded wait gives up after. It only bounds a broken run."""


def _latest_run_id(root: Path) -> str:
    """The run id, read on a second connection: the "other process" of live-control §7."""
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _run_status(root: Path, run_id: str) -> str:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run.status


def _attempt_of(stdout_path: Path) -> tuple[str, str]:
    """(card id, phase) of the attempt a launch belongs to, from
    `<run dir>/<card>/<phase>.<n>/stdout.log`; the fake is never asked."""
    attempt = Path(stdout_path).parent
    return attempt.parent.name, attempt.name.rsplit(".", 1)[0]


def _hold(
    monkeypatch,
    card: str,
    phase: str,
    entered: threading.Event,
    release: threading.Event,
) -> None:
    """Hold `card`'s `phase` launch once: announce it, wait for `release`, then launch.

    `cli.default_runner_factory` reads `cli.run_direct` at call time; the
    launch runs in a `to_thread` worker, so the control watcher stays free.
    One-shot, and undone with the test's function-scoped `monkeypatch`.
    """
    real = launcher.run_direct
    armed = {"on": True}

    def holding(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        if armed["on"] and _attempt_of(stdout_path) == (card, phase):
            armed["on"] = False
            entered.set()
            if not release.wait(WAIT):
                raise AssertionError(f"{card}'s {phase} launch was never released")
        return real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )

    monkeypatch.setattr(cli, "run_direct", holding)


def _signal_when_applied(monkeypatch, applied: threading.Event) -> None:
    """Set `applied` once the running process has applied a control request
    (a non-empty return from `control.apply_pending`)."""
    real = control.apply_pending

    def applying(*args: Any, **kwargs: Any):
        rows = real(*args, **kwargs)
        if rows:
            applied.set()
        return rows

    monkeypatch.setattr(control, "apply_pending", applying)


def _in_background(work: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """Run `work` on a daemon thread; its result or error lands in the box."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = work()
        except BaseException as error:  # surfaced by the caller, never swallowed
            box["error"] = error

    worker = threading.Thread(target=target, name="am-run-milestone", daemon=True)
    worker.start()
    return worker, box


def _control_while_held(
    root: Path,
    milestone: str,
    command: str,
    *,
    run_milestone_cli,
    entered: threading.Event,
    release: threading.Event,
    applied: threading.Event,
):
    """Run the milestone in a worker, send `am <command>` while the hold is in,
    and finish it. Returns (run id, the control command's `data`, the run's
    `CliRunner` result). `release` is set in a `finally`, so a failed
    assertion never leaves the worker hanging."""
    worker, box = _in_background(lambda: run_milestone_cli(root, milestone))
    try:
        assert entered.wait(WAIT), "the held launch never arrived"
        run_id = _latest_run_id(root)
        requested = CliRunner().invoke(cli.app, [command, run_id, "--repo-dir", str(root)])
        assert requested.exit_code == 0, (requested.output, requested.exception)
        control_data = _envelope(requested)
        assert applied.wait(WAIT), f"the running process never applied the {command}"
    finally:
        release.set()
        worker.join(WAIT)
    assert not worker.is_alive(), "the milestone run never finished after release"
    assert "error" not in box, box.get("error")
    return run_id, control_data, box["result"]


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


def test_cancel_comments_in_progress_subtasks_and_milestone(
    milestone_board, run_milestone_cli, monkeypatch
):
    """Spec scenario 3: held in a2's plan and cancelled from another
    connection. a1 (done) keeps only its done comment, a2 (in progress) gets
    one cancelled comment, b1 and c1 (never started) get nothing, and the
    milestone gets one cancelled run-end."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    branches = milestone_board["branches"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    _hold(monkeypatch, a2, HELD_PHASE, entered, release)
    _signal_when_applied(monkeypatch, applied)

    run_id, requested, first = _control_while_held(
        root,
        milestone,
        "cancel",
        run_milestone_cli=run_milestone_cli,
        entered=entered,
        release=release,
        applied=applied,
    )

    assert requested["effective"] == "cancel", requested
    assert first.exit_code == 0, (first.output, first.exception)
    cancelled = _envelope(first)
    assert cancelled["cancelled"] is True, cancelled
    assert cancelled["run_id"] == run_id
    assert _run_status(root, run_id) == "cancelled"

    (a1_done,) = _on(root, a1)
    _assert_shape(a1_done, outcome="done", run_id=run_id, key=comments.key(run_id, a1, "done"))

    (a2_cancelled,) = _on(root, a2)
    lines = _assert_shape(
        a2_cancelled,
        outcome="cancelled",
        run_id=run_id,
        key=comments.key(run_id, a2, "cancelled"),
    )
    assert f"branch: {branches[a2]}" in lines, a2_cancelled.body
    assert f"relaunch: `am run --milestone {milestone}`" in lines, a2_cancelled.body

    for card in (b1, c1):
        assert _on(root, card) == [], card
    for story in milestone_board["stories"].values():
        assert _on(root, story) == [], story

    (end,) = _on(root, milestone)
    end_lines, _key = _assert_scoped(
        end,
        outcome="cancelled",
        run_id=run_id,
        prefix=comments.key(run_id, milestone, "run-end:"),
    )
    assert "done: 1 of 4" in end_lines, end.body
    assert f"parked: [[{a2}]]" in end_lines, end.body
    assert f"next: `am run --milestone {milestone}`" in end_lines, end.body


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
    conn = store.open_db(cli.resolve_repo_dir(root))
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
