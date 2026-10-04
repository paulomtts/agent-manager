"""`orchestrate.run_board` through the production wiring (card baef4f94).

Board-run spec §3.8 and main spec §14. `orchestrate.run_board` is called
directly (`am run --board` is sibling d78b3118's) with no `runner_factory`
and no `driver`, so every milestone reaches `_run_milestone_async`,
`supervise`, `cli.drive_subtask_async`, `cli.default_runner_factory`, the
real `ClaudeAdapter` and `launcher.run_direct`. The only stand-in is the fake
`claude` first on `PATH` (`fake_claude_bin`). The `e2e` marker is the
real-money tier; everything here is `e2e_fake` instead (exempted from the
directory auto-mark in `tests/conftest.py`, same as `test_fake_claude.py`),
except `test_this_module_runs_in_the_default_suite_unmarked` below, which
stays in the default suite on purpose so board wiring is checked on every
`uv run pytest`.

Every milestone here has one story holding one subtask, so a milestone's lane
holds one slot of the board's shared semaphore for its whole run. Each test
builds its own repo and board (`fresh_project`), because a run moves every
card and branch it touches.
"""

import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import bases, board, cli, dag, integration, models, orchestrate, paths, store

VERIFY_COMMANDS = ("git rev-parse --verify HEAD",)
"""Must equal the conftest's `VERIFY_COMMANDS`: a real, green command for this toy repo."""

UNION_ATTRIBUTE = "IMPLEMENTATION.md merge=union\n"
"""Must equal the conftest's `UNION_ATTRIBUTE`."""

REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""Must equal the conftest's `FAKE_REVIEW_FAIL_MARKER`."""

OTHER_RUN_ID = "20260930T080000Z-a1b2c3d4"
"""Another run, in another `am` process, that holds a claim."""

HERE = socket.gethostname()
"""This host, as `control.Lease` records it."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _add_card(
    root: Path, title: str, parent: str | None = None, blocked_by: tuple[str, ...] = ()
) -> str:
    """`brd add`, then `brd block` per blocker, as the conftest's `_add_card` does."""
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True)
    card_id = json.loads(completed.stdout)["data"]["id"]
    for blocker in blocked_by:
        subprocess.run(
            ["brd", "block", card_id, "--by", blocker],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    return card_id


def _milestone(
    root: Path, label: str, prefix: str, blocked_by: tuple[str, ...] = ()
) -> dict[str, str]:
    """A milestone with one story and one subtask; `branch` is the subtask's, from `dag`."""
    milestone = _add_card(
        root, f"Milestone {label}: a board run under a fake claude", blocked_by=blocked_by
    )
    story = _add_card(root, f"Story {label}: the only story of milestone {label}", milestone)
    subtask = _add_card(root, f"{label.lower()}1: the only subtask of story {label}", story)
    return {
        "id": milestone,
        "story": story,
        "subtask": subtask,
        "prefix": prefix,
        "branch": dag.task_branch(prefix, board.show(subtask, repo_dir=root)),
    }


@pytest.fixture
def board_root(fresh_project, fake_claude_bin) -> Path:
    """A fresh repo and board, the fake `claude` first on `PATH`, and the union
    attribute so each milestone's Integrate folds `IMPLEMENTATION.md`."""
    attributes = fresh_project / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    return fresh_project


def _run_board(root: Path, *milestones: dict[str, str], max_concurrent: int = 1) -> dict[str, Any]:
    prefixes = {milestone["id"]: milestone["prefix"] for milestone in milestones}
    return orchestrate.run_board(
        repo_dir=root,
        base_branch="main",
        branch_prefix_of=lambda card: prefixes[card.id],
        commands=VERIFY_COMMANDS,
        max_concurrent=max_concurrent,
    )


def _entries(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["milestone_id"]: entry for entry in result["milestones"]}


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _run_ids(root: Path) -> list[str]:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return [row["id"] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _run_dirs() -> list[Path]:
    runs_root = paths.data_dir() / "runs"
    return sorted(runs_root.iterdir()) if runs_root.exists() else []


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _story_span(run: models.Run, story_id: str):
    """The earliest phase start and the latest phase end across one story's subtasks."""
    (story,) = [story for story in run.stories if story.card_id == story_id]
    phases = [phase for subtask in story.subtasks for phase in subtask.phases]
    starts = [phase.started_at for phase in phases if phase.started_at is not None]
    ends = [phase.ended_at for phase in phases if phase.ended_at is not None]
    assert starts and ends, story_id  # non-vacuity: the story really ran phases
    return min(starts), max(ends)


def _plant_lease(root: Path, key: str) -> None:
    """A live `run_leases` row and its claim, as another `am` process's `Lease` leaves them.

    Live by C2: this process's pid, this host, a fresh heartbeat.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        with store.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (OTHER_RUN_ID, "other-life", os.getpid(), HERE, now, now),
            )
            conn.execute(
                "INSERT INTO run_claims (key, run_id, token, claimed_at) VALUES (?, ?, ?, ?)",
                (key, OTHER_RUN_ID, "other-life", now),
            )
    finally:
        conn.close()


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or board wiring stops being
    checked on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


@pytest.mark.e2e_fake
def test_two_independent_milestones_both_finish(board_root):
    """Spec test 1."""
    root = board_root
    x = _milestone(root, "X", "bx")
    y = _milestone(root, "Y", "by")
    main_before = _git(root, "rev-parse", "main").strip()

    result = _run_board(root, x, y, max_concurrent=2)

    assert result["ok"] is True, result
    assert result["board"] is True
    (level,) = result["levels"]
    assert level["level"] == 0
    assert sorted(level["milestones"]) == sorted([x["id"], y["id"]])
    entries = _entries(result)
    assert set(entries) == {x["id"], y["id"]}
    for milestone in (x, y):
        entry = entries[milestone["id"]]
        assert entry["status"] == "done", entry
        assert entry["done"] is True, entry
        assert entry["integrated"]["branch"] == f"{milestone['prefix']}-integrate"
        assert _load_run(root, entry["run_id"]).milestone_id == milestone["id"]
        assert board.show(milestone["subtask"], repo_dir=root).status == "done"
    assert entries[x["id"]]["run_id"] != entries[y["id"]]["run_id"]
    # Card a7fcc076: each milestone's own journal names its milestone at the
    # head and every line carries that milestone's own run id, so no line
    # needs a milestone key. Synthetic story ids (`integrate`, `bases`) are
    # fixed names that any milestone's journal may record, so only the real
    # story card ids are checked for disjointness across journals.
    synthetic = {integration.INTEGRATE_STORY_ID, bases.BASES_STORY_ID}
    real_stories: dict[str, set[str]] = {}
    for milestone in (x, y):
        run_id = entries[milestone["id"]]["run_id"]
        lines = store.Journal(run_id).read()
        assert lines, run_id
        assert lines[0].event == "run_upsert", lines[0]
        assert lines[0].payload["milestone_id"] == milestone["id"]
        assert {line.run_id for line in lines} == {run_id}
        for line in lines:
            if line.event == "run_upsert":
                assert line.payload["milestone_id"] == milestone["id"], line
            if line.event == "story_upsert":
                assert "milestone_id" not in line.payload, line
        stories = {line.story for line in lines if line.story is not None}
        real_stories[milestone["id"]] = stories - synthetic
        assert milestone["story"] in real_stories[milestone["id"]], stories
    assert real_stories[x["id"]].isdisjoint(real_stories[y["id"]])
    assert _git(root, "rev-parse", "main").strip() == main_before


@pytest.mark.e2e_fake
def test_a_blocked_by_pair_runs_in_order(board_root):
    """Spec test 2: two slots are free, so only the dependency can order them."""
    root = board_root
    a = _milestone(root, "A", "ba")
    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))

    result = _run_board(root, a, b, max_concurrent=2)

    assert result["ok"] is True, result
    assert result["levels"] == [
        {"level": 0, "milestones": [a["id"]]},
        {"level": 1, "milestones": [b["id"]]},
    ]
    assert [entry["milestone_id"] for entry in result["milestones"]] == [a["id"], b["id"]]
    entries = _entries(result)
    assert entries[a["id"]]["status"] == "done"
    assert entries[b["id"]]["status"] == "done"
    _a_start, a_end = _story_span(_load_run(root, entries[a["id"]]["run_id"]), a["story"])
    b_start, _b_end = _story_span(_load_run(root, entries[b["id"]]["run_id"]), b["story"])
    assert a_end <= b_start, (a_end, b_start)


@pytest.mark.e2e_fake
def test_an_escalated_milestone_blocks_its_dependent_and_not_its_sibling(board_root):
    """Spec test 3: A's review fails through the production gate."""
    root = board_root
    a = _milestone(root, "A", "ba")
    c = _milestone(root, "C", "bc")
    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))
    b_status_before = board.show(b["subtask"], repo_dir=root).status
    (root / ".git" / REVIEW_FAIL_MARKER).write_text(f"{a['branch']}\n", encoding="utf-8")

    result = _run_board(root, a, b, c, max_concurrent=2)

    assert result["ok"] is False, result
    assert sorted(result["levels"][0]["milestones"]) == sorted([a["id"], c["id"]])
    assert result["levels"][1] == {"level": 1, "milestones": [b["id"]]}
    entries = _entries(result)
    escalated = entries[a["id"]]
    assert escalated["status"] == "escalated", escalated
    assert escalated["escalated"] is True
    assert (escalated["story"], escalated["subtask"], escalated["failed_phase"]) == (
        a["story"],
        a["subtask"],
        "review",
    )
    assert entries[c["id"]]["status"] == "done", entries[c["id"]]
    assert entries[b["id"]] == {
        "milestone_id": b["id"],
        "status": "blocked",
        "blocked_by": [a["id"]],
    }
    # B was never dispatched: no Run row, no run directory, no worktree, no branch.
    b_short = dag.short_id(b["id"])
    assert not [run_id for run_id in _run_ids(root) if run_id.endswith(b_short)]
    assert not [path for path in _run_dirs() if path.name.endswith(b_short)]
    assert not cli.worktree_for(root, b["branch"]).exists()
    assert b["branch"] not in _local_branches(root)
    assert board.show(b["subtask"], repo_dir=root).status == b_status_before


@pytest.mark.e2e_fake
def test_one_shared_slot_runs_two_independent_milestones_one_after_the_other(board_root):
    """Spec test 4, cap 1: one semaphore across both milestones, so their lanes never overlap."""
    root = board_root
    x = _milestone(root, "X", "bx")
    y = _milestone(root, "Y", "by")

    result = _run_board(root, x, y, max_concurrent=1)

    assert result["ok"] is True, result
    entries = _entries(result)
    x_start, x_end = _story_span(_load_run(root, entries[x["id"]]["run_id"]), x["story"])
    y_start, y_end = _story_span(_load_run(root, entries[y["id"]]["run_id"]), y["story"])
    assert x_end <= y_start or y_end <= x_start, (x_start, x_end, y_start, y_end)


@pytest.mark.e2e_fake
def test_two_shared_slots_let_two_milestones_meet_inside_implement(board_root, rendezvous):
    """Spec test 4, cap 2: the rendezvous at count 2 completes only if both
    milestones' implements were running at the same time."""
    root = board_root
    x = _milestone(root, "X", "bx")
    y = _milestone(root, "Y", "by")
    rendezvous.arm(2)

    result = _run_board(root, x, y, max_concurrent=2)

    assert result["ok"] is True, result
    assert len(rendezvous.markers()) == 2


@pytest.mark.e2e_fake
def test_a_claim_on_a_later_milestone_refuses_the_whole_board_up_front(board_root):
    """Spec test 5: the single upfront `refuse_claimed` runs before any milestone starts."""
    root = board_root
    a = _milestone(root, "A", "ba")
    b = _milestone(root, "B", "bb", blocked_by=(a["id"],))
    key = f"card:{b['subtask']}"
    _plant_lease(root, key)
    statuses_before = {
        card: board.show(card, repo_dir=root).status for card in (a["subtask"], b["subtask"])
    }

    with pytest.raises(cli.ClaimedError) as caught:
        _run_board(root, a, b)

    assert caught.value.key == key
    assert caught.value.run_id == OTHER_RUN_ID
    assert _run_ids(root) == []
    assert _run_dirs() == []
    assert _local_branches(root) == ["main"]
    assert {
        card: board.show(card, repo_dir=root).status for card in statuses_before
    } == statuses_before


@pytest.mark.e2e_fake
def test_no_rendezvous_is_left_armed_for_later_tests():
    """Kept last in the module: the rendezvous is armed through the
    function-scoped `monkeypatch`, so it must be gone once a test ends."""
    assert "FAKE_CLAUDE_RENDEZVOUS_DIR" not in os.environ
    assert "FAKE_CLAUDE_RENDEZVOUS_COUNT" not in os.environ
