"""Behaviour of the roll-up step (design §4 `steps/`, subtask cards 43008688, bf26f482).

Placement follows design §14: `rollup.py` is a Steps component whose behaviour
is brd reads and writes -- write the card, then walk its ancestors -- so it is
exercised against a real temporary brd board over subprocess. brd is not
mocked, and neither is any `board` function, with one exception: the
`fake_brd` fixture, used only by the process-lock tests (spec X7, card
43043f10). Those tests must show that no `brd` call ran while another process
held the board lock, and a real `brd` gives no way to observe from outside that
an `update` has not yet started. The fake logs every call together with whether
the release marker file existed and whether the board lock's flock was held at
that moment. The pure status computation (`stored_status`, `rollup_status`)
gets plain unit tests at the end of the file.

`tests/steps/` has no `conftest.py` (`test_verify.py` defines its own
`requires_git` marker locally), so the brd helpers are lifted from
`tests/test_board.py`: the `requires_brd` marker, the `temp_board` fixture,
`_add_card` and `_brd_json`.

Two lock-scope tests wrap `board.show` and `board.tree` in pass-through spies
that only record whether `board.WRITE_LOCK` is held; the real functions still
run against the real board.
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from lockhelpers import _holder, _probe, _reap, _release

from agent_manager import board, locks, paths
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


@pytest.mark.soak
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


@pytest.mark.soak
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


@pytest.mark.soak
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


# --- Process-wide board lock (spec X7, card 43043f10): a fake brd on PATH. ---

FAKE_BRD_SOURCE = textwrap.dedent(
    """
    import fcntl, json, os, sys
    from pathlib import Path

    home = Path(os.environ["FAKE_BRD_HOME"])
    state_file = home / "board.json"
    argv = sys.argv[1:]

    fd = os.open(os.environ["FAKE_BRD_LOCK"], os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        flock = "busy"
    else:
        flock = "free"
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

    with (home / "calls.log").open("a") as log:
        entry = {"argv": argv, "marker": (home / "released").exists(), "flock": flock}
        print(json.dumps(entry), file=log)

    cards = json.loads(state_file.read_text())

    def card(card_id):
        stored = cards[card_id]
        return {
            "id": card_id,
            "title": stored["title"],
            "status": stored["status"],
            "parent_id": stored["parent_id"],
        }

    def node(card_id):
        stored = cards[card_id]
        return {
            "id": card_id,
            "title": stored["title"],
            "status": stored["status"],
            "children": [
                node(child) for child, row in cards.items() if row["parent_id"] == card_id
            ],
        }

    verb, card_id = argv[0], argv[1]
    if card_id not in cards:
        error = {"type": "CardNotFoundError", "message": f"no card {card_id}"}
        print(json.dumps({"ok": False, "error": error}))
        sys.exit(1)
    if verb == "show":
        data = card(card_id)
    elif verb == "tree":
        data = [node(card_id)]
    elif verb == "update":
        cards[card_id]["status"] = argv[3]
        state_file.write_text(json.dumps(cards))
        data = card(card_id)
    else:
        print(json.dumps({"ok": False, "error": {"type": "Usage", "message": verb}}))
        sys.exit(2)
    print(json.dumps({"ok": True, "data": data}))
    """
)

CANNED_BOARD = {
    "m1": {"title": "Milestone 10", "status": "todo", "parent_id": None},
    "st1": {"title": "Process-wide locks", "status": "todo", "parent_id": "m1"},
    "sub1": {"title": "Put board writes under the lock", "status": "todo", "parent_id": "st1"},
    "sub2": {"title": "A sibling subtask", "status": "todo", "parent_id": "st1"},
}
"""sub1 going done rolls st1 and m1 up to in_progress: three writes, one walk."""


@dataclass
class FakeBrd:
    """Handle on the `fake_brd` fixture: the project dir brd runs in, and its log."""

    root: Path
    home: Path

    @property
    def marker(self) -> Path:
        """The release marker; outside `root`, so nothing lands in the project."""
        return self.home / "released"

    def calls(self) -> list[dict[str, object]]:
        """Every logged call, in order: `argv`, `marker` (bool), `flock` ("busy"/"free")."""
        log = self.home / "calls.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def fake_brd(tmp_path, monkeypatch) -> FakeBrd:
    """A `brd` on PATH answering from `CANNED_BOARD` and logging every call.

    Each log line records whether the release marker existed and whether the
    project's board flock was held (probed non-blocking) when that call ran.
    The lock path is computed here, after tests/conftest.py pointed
    XDG_DATA_HOME at this test's own directory, and handed to the script.
    """
    home = tmp_path / "fake-brd"
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    root = tmp_path / "project"
    root.mkdir()
    (home / "board.json").write_text(json.dumps(CANNED_BOARD))
    script = bin_dir / "brd"
    script.write_text(f"#!{sys.executable}\n{FAKE_BRD_SOURCE}")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_BRD_HOME", str(home))
    monkeypatch.setenv("FAKE_BRD_LOCK", str(paths.project_lock_path(root, "board")))
    return FakeBrd(root=root, home=home)


def _signal_first_flock_miss(monkeypatch) -> threading.Event:
    """An Event set the first time any ProcessLock finds its flock taken.

    `locks._flock` asks `_backoff` for a delay only after a failed
    non-blocking attempt, so the Event means "a thread is now waiting on
    another process's flock" -- no timing involved.
    """
    progressed = threading.Event()
    real_backoff = locks._backoff

    def signalling_backoff(attempt: int) -> float:
        progressed.set()
        return real_backoff(attempt)

    monkeypatch.setattr(locks, "_backoff", signalling_backoff)
    return progressed


def _roll_in_thread(
    fake_brd: FakeBrd, card: str, status: str, done: threading.Event | None = None
) -> tuple[threading.Thread, dict[str, object]]:
    """Start `rollup.set_status` on a daemon thread; its outcome lands in the dict.

    `done`, when given, is set as the call returns or raises.
    """
    outcome: dict[str, object] = {}

    def roll() -> None:
        try:
            outcome["result"] = rollup.set_status(card, status, repo_dir=fake_brd.root)
        except BaseException as exc:  # surfaced by the caller's assertions
            outcome["error"] = exc
        finally:
            if done is not None:
                done.set()

    worker = threading.Thread(target=roll, daemon=True)
    worker.start()
    return worker, outcome


def test_a_rollup_waits_for_another_process_holding_the_board_lock(
    fake_brd, monkeypatch
):
    # `progressed` fires on the worker's first failed flock attempt (it is now
    # waiting on the child) or, if nothing locks, when the worker finishes.
    # Only then is the marker written and the child released, so a call logged
    # without the marker can only have run while the child held the lock.
    progressed = _signal_first_flock_miss(monkeypatch)
    child = _holder(fake_brd.root, "board")
    try:
        worker, outcome = _roll_in_thread(fake_brd, "sub1", "done", done=progressed)
        assert progressed.wait(timeout=30)
        fake_brd.marker.touch()
        _release(child)
        worker.join(timeout=60)
        assert not worker.is_alive()
    finally:
        _reap(child)

    assert "error" not in outcome, outcome
    calls = fake_brd.calls()
    assert calls
    assert all(call["marker"] for call in calls), calls


def test_a_nested_rollup_walk_runs_every_brd_call_under_one_flock(fake_brd):
    # Three writes (sub1, st1, m1) re-enter the lock the walk already holds; a
    # second flock on a new descriptor would block its own process forever,
    # so the worker thread's join timeout is the deadlock detector.
    worker, outcome = _roll_in_thread(fake_brd, "sub1", "done")
    worker.join(timeout=60)
    assert not worker.is_alive()

    assert "error" not in outcome, outcome
    assert outcome["result"] == {
        "card": "sub1",
        "status": "done",
        "rolled_up": [
            {"card": "st1", "status": "in_progress"},
            {"card": "m1", "status": "in_progress"},
        ],
    }
    calls = fake_brd.calls()
    assert [call["argv"][0] for call in calls] == [
        "update", "show", "tree", "update", "show", "tree", "update", "show",
    ]
    assert all(call["flock"] == "busy" for call in calls), calls
    assert _probe(fake_brd.root, "board") == "free"
    assert list(fake_brd.root.iterdir()) == []  # no lock file in the project


def test_a_board_lock_timeout_propagates_before_any_brd_call(fake_brd, monkeypatch):
    lock = board.write_lock(fake_brd.root)
    monkeypatch.setattr(lock, "_timeout", 0)
    child = _holder(fake_brd.root, "board")
    try:
        with pytest.raises(locks.LockTimeoutError):
            rollup.set_status("sub1", "done", repo_dir=fake_brd.root)
    finally:
        _reap(child)

    assert fake_brd.calls() == []
    assert not _write_lock_held_by_another_thread()


def test_a_failed_rollup_releases_the_board_flock(fake_brd):
    with pytest.raises(board.BoardError):
        rollup.set_status("no-such-card", "done", repo_dir=fake_brd.root)

    assert _probe(fake_brd.root, "board") == "free"
    assert not _write_lock_held_by_another_thread()


def test_the_board_lock_is_one_per_resolved_repository_and_defaults_to_the_cwd(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()

    lock = board.write_lock(repo)

    assert lock is locks.project_lock(repo, "board")
    assert board.write_lock(repo / "x" / "..") is lock
    assert board.write_lock(Path(f"{repo}{os.sep}")) is lock
    assert lock.path == paths.project_lock_path(repo, "board")
    assert lock._local is board.WRITE_LOCK
    monkeypatch.chdir(repo)
    assert board.write_lock(None) is lock


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
