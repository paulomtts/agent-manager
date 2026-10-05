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
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, models, paths, store

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: the board fixture derives its branches with it."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the `m3` prefix; a story run never creates it."""


AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)
"""Must equal the conftest's `AGENT_PHASES`: `TASK`'s seven agent phases, in order."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


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


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _lease(root: Path, run_id: str) -> store.LeaseRow | None:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return store.read_lease(conn, run_id)
    finally:
        conn.close()


def _card_phase_counts(entries: Iterable[Mapping]) -> Counter:
    """How many times the fake ran each (card id, phase).

    The fake logs its result path, `<run dir>/<card>/<phase>.<n>/result.json`,
    so the card is read off the path, never off the fake.
    """
    return Counter(
        (Path(entry["result_path"]).parents[1].name, entry["phase"]) for entry in entries
    )


def _counts(full: Iterable[str]) -> Counter:
    """Every agent phase once per card in `full`."""
    return Counter((card, phase) for card in full for phase in AGENT_PHASES)


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


@pytest.mark.e2e_fake
def test_a_story_run_walks_only_its_story_and_the_next_story_roots_on_its_tip(
    milestone_board, fake_claude_bin, read_fake_log
):
    """`am run --story A` (by id) drives a1 and a2 only, on A's own stack, and
    ends done with no Integrate; `am run --story` with a title piece of B then
    drives b1 only, rooted on A's tip, without moving A's branches. Each run
    is recorded under the parent milestone with its own `story_id`."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    stories = milestone_board["stories"]
    branches = milestone_board["branches"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    main_before = _git(root, "rev-parse", "main").strip()

    # S2a: story A, selected by id.
    first = _run_story(root, stories["A"])

    assert first.exit_code == 0, (first.output, first.exception)
    done_a = _envelope(first)
    assert done_a["done"] is True, done_a
    assert "integrated" not in done_a, done_a
    assert "escalated" not in done_a, done_a
    assert set(done_a["completed"]) == {a1, a2}, done_a
    run_a = done_a["run_id"]
    assert _card_phase_counts(read_fake_log(run_a)) == _counts([a1, a2])
    local = _local_branches(root)
    assert branches[a1] in local, local
    assert branches[a2] in local, local
    assert _is_ancestor(root, branches[a1], branches[a2])
    for absent in (branches[b1], branches[c1], INTEGRATION_BRANCH):
        assert absent not in local, (absent, local)
    assert _git(root, "rev-parse", "main").strip() == main_before
    assert _statuses(root, [a1, a2, stories["A"]]) == dict.fromkeys(
        [a1, a2, stories["A"]], "done"
    )
    # Untouched: A done unblocks B and b1 (todo); C and c1 still wait on B (blocked).
    assert _statuses(root, [stories["B"], b1, stories["C"], c1]) == {
        stories["B"]: "todo",
        b1: "todo",
        stories["C"]: "blocked",
        c1: "blocked",
    }
    run = _load_run(root, run_a)
    assert run.config.story_id == stories["A"], run.config
    assert run.milestone_id == milestone
    assert run.status == "done"
    assert _lease(root, run_a) is None
    a_tips = {card: _git(root, "rev-parse", branches[card]).strip() for card in (a1, a2)}

    # S2b: story B, selected by a title piece that matches B only.
    second = _run_story(root, "blocked by story A")

    assert second.exit_code == 0, (second.output, second.exception)
    done_b = _envelope(second)
    assert done_b["done"] is True, done_b
    assert "integrated" not in done_b, done_b
    assert "escalated" not in done_b, done_b
    assert set(done_b["completed"]) == {b1}, done_b
    run_b = done_b["run_id"]
    assert run_b != run_a
    assert _card_phase_counts(read_fake_log(run_b)) == _counts([b1])
    # B rooted on the done blocker's tip, not on main.
    assert _is_ancestor(root, branches[a2], branches[b1])
    assert _git(root, "rev-parse", branches[b1]).strip() != main_before
    local = _local_branches(root)
    for absent in (branches[c1], INTEGRATION_BRANCH):
        assert absent not in local, (absent, local)
    assert _statuses(root, [b1, stories["B"]]) == dict.fromkeys([b1, stories["B"]], "done")
    # B done unblocks C and c1; neither was run.
    assert _statuses(root, [stories["C"], c1]) == dict.fromkeys([stories["C"], c1], "todo")
    run = _load_run(root, run_b)
    assert run.config.story_id == stories["B"], run.config
    assert run.milestone_id == milestone
    assert run.status == "done"
    assert _lease(root, run_b) is None
    # A's stack was not moved by B's run, and main was not either.
    assert {card: _git(root, "rev-parse", branches[card]).strip() for card in (a1, a2)} == a_tips
    assert _git(root, "rev-parse", "main").strip() == main_before
    # The listing keeps each run's own story under the parent milestone.
    listing = {
        row["id"]: (row["story_id"], row["milestone_id"])
        for row in cli.runs_for(repo_dir=root)["runs"]
    }
    assert listing == {
        run_a: (stories["A"], milestone),
        run_b: (stories["B"], milestone),
    }, listing
    assert sorted(_run_ids(root)) == sorted([run_a, run_b])
