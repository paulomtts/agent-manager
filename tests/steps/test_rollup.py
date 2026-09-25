"""Behaviour of the roll-up step (design §4 `steps/`, subtask cards 43008688, bf26f482).

Placement follows design §14: `rollup.py` is a Steps component whose behaviour
is brd reads and writes -- write the card, then walk its ancestors -- so it is
exercised against a real temporary brd board over subprocess. brd is not
mocked, and neither is any `board` function. The pure status computation
(`stored_status`, `rollup_status`) gets plain unit tests at the end of the file.

`tests/steps/` has no `conftest.py` (`test_verify.py` defines its own
`requires_git` marker locally), so the brd helpers are lifted from
`tests/test_board.py`: the `requires_brd` marker, the `temp_board` fixture,
`_add_card` and `_brd_json`.

Two lock-scope tests wrap `board.show` and `board.tree` in pass-through spies
that only record whether `board.WRITE_LOCK` is held; the real functions still
run against the real board.
"""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from agent_manager import board
from agent_manager.steps import rollup

requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the roll-up step's steps-tier tests",
)


@pytest.fixture
def temp_board(tmp_path, monkeypatch):
    """A real, empty brd board in a throwaway directory.

    brd keys its SQLite files off XDG_DATA_HOME and resolves the board from the
    nearest `.brd` marker at or above its cwd, so pointing XDG_DATA_HOME at
    tmp_path and running in a fresh directory isolates these tests completely
    from the developer's own board. The subprocess inherits the patched
    environment.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "board-repo"
    root.mkdir()
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]["id"]


def _brd_json(root: Path, *args: str) -> object:
    completed = subprocess.run(
        ["brd", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]


@requires_brd
def test_set_status_really_changes_the_card_on_the_board(temp_board):
    milestone = _add_card(temp_board, "Milestone 2")
    story = _add_card(temp_board, "Close the seams the wiring test found", milestone)
    subtask = _add_card(temp_board, "Implement the rollup.set_status step", story)
    assert _brd_json(temp_board, "show", subtask)["status"] == "todo"

    result = rollup.set_status(subtask, "in_progress", repo_dir=temp_board)

    stored = _brd_json(temp_board, "show", subtask)
    assert stored["status"] == "in_progress"
    # The mapping reports what brd stored, not the literal that was requested.
    assert result == {
        "card": subtask,
        "status": stored["status"],
        "rolled_up": [
            {"card": story, "status": "in_progress"},
            {"card": milestone, "status": "in_progress"},
        ],
    }
    assert _brd_json(temp_board, "show", story)["status"] == "in_progress"
    assert _brd_json(temp_board, "show", milestone)["status"] == "in_progress"


@requires_brd
def test_a_second_identical_call_is_a_harmless_no_op(temp_board):
    subtask = _add_card(temp_board, "Implement the rollup.set_status step")

    first = rollup.set_status(subtask, "done", repo_dir=temp_board)
    second = rollup.set_status(subtask, "done", repo_dir=temp_board)

    assert second == first == {"card": subtask, "status": "done", "rolled_up": []}
    assert _brd_json(temp_board, "show", subtask)["status"] == "done"


@requires_brd
def test_a_later_call_with_a_different_status_overwrites(temp_board):
    subtask = _add_card(temp_board, "Implement the rollup.set_status step")

    rollup.set_status(subtask, "in_progress", repo_dir=temp_board)
    result = rollup.set_status(subtask, "done", repo_dir=temp_board)

    assert result == {"card": subtask, "status": "done", "rolled_up": []}
    assert _brd_json(temp_board, "show", subtask)["status"] == "done"


@requires_brd
def test_a_nonexistent_card_raises_board_error(temp_board):
    with pytest.raises(board.BoardError):
        rollup.set_status("deadbeef", "done", repo_dir=temp_board)


@requires_brd
def test_an_empty_card_id_raises_board_error(temp_board):
    # A context key that was never populated must fail loudly, not write nothing.
    with pytest.raises(board.BoardError):
        rollup.set_status("", "done", repo_dir=temp_board)


@requires_brd
def test_the_first_subtask_going_in_progress_starts_story_and_milestone(temp_board):
    milestone = _add_card(temp_board, "Milestone 3")
    story = _add_card(temp_board, "Run a milestone", milestone)
    first = _add_card(temp_board, "Extract the shared driver", story)
    _add_card(temp_board, "Roll status up the ancestors", story)

    result = rollup.set_status(first, "in_progress", repo_dir=temp_board)

    assert result["rolled_up"] == [
        {"card": story, "status": "in_progress"},
        {"card": milestone, "status": "in_progress"},
    ]
    assert _brd_json(temp_board, "show", story)["status"] == "in_progress"
    assert _brd_json(temp_board, "show", milestone)["status"] == "in_progress"


@requires_brd
def test_the_last_subtask_going_done_marks_story_and_milestone_done(temp_board):
    milestone = _add_card(temp_board, "Milestone 3")
    story = _add_card(temp_board, "Run a milestone", milestone)
    earlier = _add_card(temp_board, "Extract the shared driver", story)
    last = _add_card(temp_board, "Roll status up the ancestors", story)
    # The state a real run leaves behind before the last subtask finishes.
    _brd_json(temp_board, "update", earlier, "--status", "done")
    _brd_json(temp_board, "update", last, "--status", "in_progress")
    _brd_json(temp_board, "update", story, "--status", "in_progress")
    _brd_json(temp_board, "update", milestone, "--status", "in_progress")

    result = rollup.set_status(last, "done", repo_dir=temp_board)

    assert result == {
        "card": last,
        "status": "done",
        "rolled_up": [
            {"card": story, "status": "done"},
            {"card": milestone, "status": "done"},
        ],
    }
    assert _brd_json(temp_board, "show", story)["status"] == "done"
    assert _brd_json(temp_board, "show", milestone)["status"] == "done"

    # Resume re-runs whole phases: the same transition again changes nothing.
    again = rollup.set_status(last, "done", repo_dir=temp_board)
    assert again == {"card": last, "status": "done", "rolled_up": []}


@requires_brd
def test_a_stale_grandparent_is_repaired_past_a_correct_parent(temp_board):
    milestone = _add_card(temp_board, "Milestone 3")
    story = _add_card(temp_board, "Run a milestone", milestone)
    first = _add_card(temp_board, "Extract the shared driver", story)
    _add_card(temp_board, "Roll status up the ancestors", story)
    # An interrupted earlier run: the story was rolled up, the milestone never was.
    _brd_json(temp_board, "update", first, "--status", "in_progress")
    _brd_json(temp_board, "update", story, "--status", "in_progress")
    assert _brd_json(temp_board, "show", milestone)["status"] == "todo"

    result = rollup.set_status(first, "in_progress", repo_dir=temp_board)

    assert result["rolled_up"] == [{"card": milestone, "status": "in_progress"}]
    assert _brd_json(temp_board, "show", story)["status"] == "in_progress"
    assert _brd_json(temp_board, "show", milestone)["status"] == "in_progress"


@requires_brd
def test_a_card_with_no_parent_rolls_nothing_up(temp_board):
    lone = _add_card(temp_board, "A card with no parent")

    result = rollup.set_status(lone, "in_progress", repo_dir=temp_board)

    assert result == {"card": lone, "status": "in_progress", "rolled_up": []}


@requires_brd
def test_a_blocked_sibling_counts_as_todo_when_rolling_up(temp_board):
    story = _add_card(temp_board, "Run a milestone")
    first = _add_card(temp_board, "Extract the shared driver", story)
    second = _brd_json(
        temp_board,
        "add",
        "--title",
        "Roll status up the ancestors",
        "--parent",
        story,
        "--blocked-by",
        first,
    )["id"]
    assert _brd_json(temp_board, "show", second)["status"] == "blocked"
    # A stale story: nothing under it has actually started.
    _brd_json(temp_board, "update", story, "--status", "in_progress")

    result = rollup.set_status(first, "todo", repo_dir=temp_board)

    # Children read ["todo", "blocked"]; blocked is todo, so the story is todo.
    assert result["rolled_up"] == [{"card": story, "status": "todo"}]
    assert _brd_json(temp_board, "show", story)["status"] == "todo"


@requires_brd
def test_a_parent_reported_blocked_is_not_rewritten_to_todo(temp_board):
    milestone = _add_card(temp_board, "Milestone 3")
    before = _add_card(temp_board, "An earlier story", milestone)
    story = _brd_json(
        temp_board,
        "add",
        "--title",
        "A story waiting on the earlier one",
        "--parent",
        milestone,
        "--blocked-by",
        before,
    )["id"]
    subtask = _add_card(temp_board, "A subtask of the waiting story", story)
    assert _brd_json(temp_board, "show", story)["status"] == "blocked"

    result = rollup.set_status(subtask, "todo", repo_dir=temp_board)

    # The story reads `blocked` but is stored `todo`, which is already the target.
    assert result["rolled_up"] == []


@requires_brd
def test_the_walk_is_capped_at_sixteen_ancestors(temp_board):
    # chain[0] is the root; chain[i] has exactly i ancestors.
    chain = [_add_card(temp_board, "Level 0")]
    for level in range(1, rollup.MAX_ANCESTRY_DEPTH + 2):
        chain.append(_add_card(temp_board, f"Level {level}", chain[-1]))
    at_the_cap = chain[rollup.MAX_ANCESTRY_DEPTH]
    past_the_cap = chain[rollup.MAX_ANCESTRY_DEPTH + 1]

    # Exactly 16 ancestors is allowed: every one of them is rolled up.
    result = rollup.set_status(at_the_cap, "in_progress", repo_dir=temp_board)
    assert [entry["card"] for entry in result["rolled_up"]] == list(
        reversed(chain[: rollup.MAX_ANCESTRY_DEPTH])
    )

    # A 17th ancestor raises -- and the card's own write is not undone.
    with pytest.raises(board.BoardError, match="exceeded maximum ancestry depth"):
        rollup.set_status(past_the_cap, "done", repo_dir=temp_board)
    assert _brd_json(temp_board, "show", past_the_cap)["status"] == "done"


def _write_lock_held_by_another_thread() -> bool:
    """Whether some other thread holds board.WRITE_LOCK right now.

    Probed from a fresh thread with a non-blocking acquire, so it uses only the
    lock's public API and never blocks.
    """
    acquired: list[bool] = []

    def probe() -> None:
        got = board.WRITE_LOCK.acquire(blocking=False)
        if got:
            board.WRITE_LOCK.release()
        acquired.append(got)

    prober = threading.Thread(target=probe)
    prober.start()
    prober.join()
    return not acquired[0]


@requires_brd
def test_the_whole_rollup_walk_runs_under_the_board_lock(temp_board, monkeypatch):
    # A rollup is read-modify-write: the ancestor reads must be inside the same
    # critical section as the writes, or two sibling walks can interleave.
    milestone = _add_card(temp_board, "Milestone 4")
    story = _add_card(temp_board, "Serialize the shared resources", milestone)
    subtask = _add_card(temp_board, "Serialize board writes", story)
    held_during: list[tuple[str, bool]] = []
    real_show, real_tree = board.show, board.tree

    def show_spy(card_id, **kwargs):
        held_during.append(("show", _write_lock_held_by_another_thread()))
        return real_show(card_id, **kwargs)

    def tree_spy(card_id, **kwargs):
        held_during.append(("tree", _write_lock_held_by_another_thread()))
        return real_tree(card_id, **kwargs)

    monkeypatch.setattr(board, "show", show_spy)
    monkeypatch.setattr(board, "tree", tree_spy)

    result = rollup.set_status(subtask, "done", repo_dir=temp_board)

    assert result["card"] == subtask
    # show(subtask), tree(story), show(story), tree(milestone), show(milestone)
    assert [name for name, _ in held_during] == ["show", "tree", "show", "tree", "show"]
    assert all(held for _, held in held_during)
    assert not _write_lock_held_by_another_thread()


class _CountingRLock:
    """A real RLock that counts how often it goes from free to held.

    Every acquire and release is delegated, so locking behaves exactly as
    before; the count exposes whether a call was one critical section or
    several back to back.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._depth = 0
        self.outermost_acquisitions = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        got = self._lock.acquire(blocking, timeout)
        if got:
            if self._depth == 0:
                self.outermost_acquisitions += 1
            self._depth += 1
        return got

    def release(self) -> None:
        self._depth -= 1
        self._lock.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self.release()


@requires_brd
def test_the_card_write_and_whole_walk_are_one_critical_section(
    temp_board, monkeypatch
):
    # Holding the lock per board call, or per level of the walk, would let a
    # sibling's rollup slip in between; the lock must be taken exactly once.
    milestone = _add_card(temp_board, "Milestone 4")
    story = _add_card(temp_board, "Serialize the shared resources", milestone)
    subtask = _add_card(temp_board, "Serialize board writes", story)
    counting = _CountingRLock()
    monkeypatch.setattr(board, "WRITE_LOCK", counting)

    result = rollup.set_status(subtask, "done", repo_dir=temp_board)

    # Three writes happened: the subtask, the story and the milestone.
    assert result["rolled_up"] == [
        {"card": story, "status": "done"},
        {"card": milestone, "status": "done"},
    ]
    assert counting.outermost_acquisitions == 1


@requires_brd
def test_a_failed_rollup_releases_the_board_lock(temp_board):
    with pytest.raises(board.BoardError):
        rollup.set_status("deadbeef", "done", repo_dir=temp_board)
    assert not _write_lock_held_by_another_thread()

    # The depth guard raises from inside the walk; the lock must still be free.
    chain = [_add_card(temp_board, "Level 0")]
    for level in range(1, rollup.MAX_ANCESTRY_DEPTH + 2):
        chain.append(_add_card(temp_board, f"Level {level}", chain[-1]))
    with pytest.raises(board.BoardError, match="exceeded maximum ancestry depth"):
        rollup.set_status(chain[-1], "done", repo_dir=temp_board)
    assert not _write_lock_held_by_another_thread()


@requires_brd
def test_rollup_reenters_a_board_lock_its_own_thread_already_holds(temp_board):
    story = _add_card(temp_board, "Serialize the shared resources")
    subtask = _add_card(temp_board, "Serialize board writes", story)
    outcome: dict[str, object] = {}

    def nested() -> None:
        with board.WRITE_LOCK:
            outcome["result"] = rollup.set_status(
                subtask, "done", repo_dir=temp_board
            )

    # On a worker with a timeout, so a non-reentrant lock fails the test
    # instead of hanging the suite.
    worker = threading.Thread(target=nested, daemon=True)
    worker.start()
    worker.join(timeout=60)
    assert not worker.is_alive()
    assert outcome["result"] == {
        "card": subtask,
        "status": "done",
        "rolled_up": [{"card": story, "status": "done"}],
    }


_RACE_ITERATIONS = 4
_RACE_STORIES = 2
_RACE_SUBTASKS_PER_STORY = 4


@requires_brd
def test_concurrent_rollups_reach_done(temp_board):
    # Parallel-stories P3: 8 sibling-and-cousin subtasks finishing at once must
    # not lose a story or milestone update. Fresh cards every iteration.
    for iteration in range(_RACE_ITERATIONS):
        milestone = _add_card(temp_board, f"Milestone {iteration}")
        stories = [
            _add_card(temp_board, f"Story {iteration}.{s}", milestone)
            for s in range(_RACE_STORIES)
        ]
        subtasks = [
            _add_card(temp_board, f"Subtask {iteration}.{s}.{t}", story)
            for s, story in enumerate(stories)
            for t in range(_RACE_SUBTASKS_PER_STORY)
        ]
        barrier = threading.Barrier(len(subtasks))
        results: dict[str, dict[str, object]] = {}
        errors: list[tuple[str, BaseException]] = []

        def mark_done(card: str) -> None:
            barrier.wait()
            try:
                results[card] = rollup.set_status(card, "done", repo_dir=temp_board)
            except BaseException as exc:
                errors.append((card, exc))

        threads = [
            threading.Thread(target=mark_done, args=(card,), daemon=True)
            for card in subtasks
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)

        assert not any(thread.is_alive() for thread in threads), iteration
        assert errors == [], iteration
        assert sorted(results) == sorted(subtasks), iteration
        assert all(result["status"] == "done" for result in results.values())
        for story in stories:
            assert _brd_json(temp_board, "show", story)["status"] == "done", (
                iteration,
                story,
            )
        assert _brd_json(temp_board, "show", milestone)["status"] == "done", iteration


# --- Pure-function tier (design §14): the status computation, no board. ---


def test_stored_status_flattens_blocked_to_todo():
    assert rollup.stored_status("blocked") == "todo"
    assert rollup.stored_status("todo") == "todo"
    assert rollup.stored_status("in_progress") == "in_progress"
    assert rollup.stored_status("done") == "done"


def test_rollup_status_is_none_without_children():
    assert rollup.rollup_status([]) is None


def test_rollup_status_is_todo_when_every_child_is_todo():
    assert rollup.rollup_status(["todo", "todo"]) == "todo"


def test_rollup_status_counts_blocked_children_as_todo():
    assert rollup.rollup_status(["todo", "blocked"]) == "todo"
    assert rollup.rollup_status(["blocked"]) == "todo"


def test_rollup_status_is_done_when_every_child_is_done():
    assert rollup.rollup_status(["done", "done"]) == "done"


def test_rollup_status_is_in_progress_by_progress_not_least_advanced():
    # One done child among unstarted ones means the parent is under way.
    assert rollup.rollup_status(["done", "todo"]) == "in_progress"
    assert rollup.rollup_status(["done", "blocked"]) == "in_progress"
    assert rollup.rollup_status(["in_progress"]) == "in_progress"
    assert rollup.rollup_status(["todo", "in_progress", "done"]) == "in_progress"


def test_rollup_status_accepts_a_generator():
    assert rollup.rollup_status(s for s in ["done", "done"]) == "done"
