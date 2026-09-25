"""Behaviour of the sequential milestone runner (orchestration addendum O6).

Two tiers, per design §14:

- `plan_levels`, `story_tips` and `stale_story_anchors` are pure over the
  census and get unit tests on hand-built plans;
- `run_milestone` runs on Steps-tier fixtures -- a real temporary git repo and a
  real temporary brd board, with `XDG_DATA_HOME` under `tmp_path` so
  `paths.data_dir()` never touches the developer's own -- with the harness
  replaced at the injected `driver` seam. No runner, adapter or `claude` is
  involved; production wiring under a fake `claude` belongs to tests/e2e.
"""

import json
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import board, census, cli, dag, engine, models, orchestrate, paths
from agent_manager import store as store_module
from agent_manager.steps import rollup, worktree
from agent_manager.workflow.registry import WorkflowLoadError


# ── pure plans ──────────────────────────────────────────────────────────────


def _plan_id(n: int) -> str:
    """A UUID-shaped card id whose short id is `n` in eight hex digits.

    `dag.subtask_branch` goes through `dag.short_id`, which refuses anything
    that is not 32 hex characters, so the pure plans need real-shaped ids.
    """
    return f"{n:08x}-0000-4000-8000-000000000000"


def _plan_subtask(n: int, status: str = "todo") -> census.SubtaskPlan:
    return census.SubtaskPlan(id=_plan_id(n), title=f"subtask {n}", status=status)


def _plan_story(
    n: int,
    subtasks: list[census.SubtaskPlan],
    *,
    status: str = "todo",
    blocked_by: tuple[str, ...] | list[str] = (),
) -> census.StoryPlan:
    return census.StoryPlan(
        id=_plan_id(n),
        title=f"story {n}",
        status=status,
        blocked_by=list(blocked_by),
        subtasks=list(subtasks),
    )


def _branch_of(subtask: census.SubtaskPlan) -> str:
    return dag.subtask_branch("m3", subtask)


def test_plan_levels_stacks_on_the_full_list_and_roots_on_a_done_blockers_tip():
    """O2: a done first subtask still anchors the second, and a story blocked by
    a done story roots on that story's tip."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="done")
    b = _plan_story(2, [_plan_subtask(21, "done"), _plan_subtask(22)], blocked_by=[a.id])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[b.id])

    levels = orchestrate.plan_levels([a, b, c], branch_prefix="m3", base_branch="main")

    assert [[planned.story.id for planned in level] for level in levels] == [[b.id], [c.id]]
    b_plan, c_plan = levels[0][0], levels[1][0]
    assert (b_plan.level, c_plan.level) == (0, 1)
    assert b_plan.bases == {
        _plan_id(21): _branch_of(a.subtasks[-1]),
        _plan_id(22): _branch_of(b.subtasks[0]),
    }
    assert b_plan.tip == _branch_of(b.subtasks[-1])
    assert [subtask.id for subtask in b_plan.remaining] == [_plan_id(22)]
    assert c_plan.bases == {_plan_id(31): _branch_of(b.subtasks[-1])}
    assert c_plan.tip == _branch_of(c.subtasks[-1])


def test_plan_levels_refuses_a_story_with_two_in_milestone_blockers():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id, b.id])

    with pytest.raises(dag.StackRootError) as caught:
        orchestrate.plan_levels([a, b, c], branch_prefix="m3", base_branch="main")

    assert f"#{a.id}" in str(caught.value)
    assert f"#{b.id}" in str(caught.value)


def test_plan_levels_refuses_a_blocker_cycle_before_any_geometry():
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    with pytest.raises(dag.DependencyCycleError):
        orchestrate.plan_levels([a, b], branch_prefix="m3", base_branch="main")


def test_a_milestone_with_nothing_pending_plans_no_levels():
    a = _plan_story(1, [_plan_subtask(11, "done")], status="done")

    assert orchestrate.plan_levels([a], branch_prefix="m3", base_branch="main") == []


def test_story_tips_name_every_story_with_subtasks_in_census_order():
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="done")
    empty = _plan_story(2, [], blocked_by=[a.id])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id])

    tips = orchestrate.story_tips([a, empty, c], branch_prefix="m3", base_branch="main")

    assert tips == [
        {"story": a.id, "tip": _branch_of(a.subtasks[-1])},
        {"story": c.id, "tip": _branch_of(c.subtasks[-1])},
    ]


def test_the_before_phase_is_read_out_of_a_stopped_detail():
    """`engine._stop` writes "stopped before <phase>"; the summary has no field
    of its own for that phase, so the helper reads it out of `detail`."""
    assert orchestrate.stopped_before_phase("stopped before implement") == "implement"
    assert orchestrate.stopped_before_phase("reviewer found a blocker") is None
    assert orchestrate.stopped_before_phase(None) is None


def test_the_first_escalation_is_the_primary_and_every_escalation_sets_the_stop():
    stop = orchestrate.RunStop()
    assert not stop.event.is_set()
    assert stop.primary is None

    stop.escalate("story-b")
    stop.escalate("story-a")

    assert stop.event.is_set()
    assert stop.primary == "story-b"


def test_the_escalated_payload_names_the_primary_and_lists_the_rest_in_census_order():
    also = orchestrate.LaneOutcome(
        kind="escalated",
        story="A",
        level=2,
        subtask="a1",
        failed_phase="review",
        detail="reviewer found a blocker",
    )
    parked = orchestrate.LaneOutcome(
        kind="stopped", story="B", level=2, subtask="b2", before_phase="implement"
    )
    primary = orchestrate.LaneOutcome(
        kind="escalated",
        story="C",
        level=2,
        subtask="c1",
        failed_phase=None,
        detail="RuntimeError: boom",
    )
    done = orchestrate.LaneOutcome(kind="done", story="D", level=2, completed=("d1",))
    queued = orchestrate.LaneOutcome(kind="not_started", story="E", level=2)

    payload = orchestrate.escalated_payload(
        "run-1", "C", [also, parked, primary, done, queued], ["gate warned"]
    )

    assert payload == {
        "escalated": True,
        "run_id": "run-1",
        "level": 2,
        "story": "C",
        "subtask": "c1",
        "failed_phase": None,
        "detail": "RuntimeError: boom",
        "warnings": ["gate warned"],
        "also_escalated": [
            {
                "level": 2,
                "story": "A",
                "subtask": "a1",
                "failed_phase": "review",
                "detail": "reviewer found a blocker",
            }
        ],
        "stopped": [{"story": "B", "subtask": "b2", "before_phase": "implement"}],
    }


def test_a_lone_escalation_payload_is_exactly_the_sequential_one():
    """No `also_escalated` or `stopped` key when those lists are empty, so a
    run at `max_concurrent=1` returns today's dict."""
    only = orchestrate.LaneOutcome(
        kind="escalated", story="A", level=0, subtask="a1", failed_phase="verify", detail="red"
    )
    queued = orchestrate.LaneOutcome(kind="not_started", story="B", level=0)

    payload = orchestrate.escalated_payload("run-1", "A", [only, queued], [])

    assert payload == {
        "escalated": True,
        "run_id": "run-1",
        "level": 0,
        "story": "A",
        "subtask": "a1",
        "failed_phase": "verify",
        "detail": "red",
        "warnings": [],
    }


# ── the runner, on a real repo and a real board ─────────────────────────────


requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the runner's steps-tier fixtures",
)
requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the runner's steps-tier fixtures",
)

STARTED_AT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
PREFIX = "m3"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)["data"]["id"]


def _block(root: Path, card_id: str, blocker: str) -> None:
    subprocess.run(
        ["brd", "block", card_id, "--by", blocker],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board.

    XDG_DATA_HOME points into tmp_path, which isolates brd's own database and
    `paths.data_dir()`, so no run artifact can land in the developer's home.
    The repo has no remote.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True, text=True
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "brd init")
    return root


def _milestone(
    project: Path,
    stories: dict[str, int],
    blocked_by: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """A milestone whose stories each hold a `brd block` chain of subtasks.

    `stories` maps a story key to its subtask count, in creation order.
    `blocked_by` maps a story key to the keys of the stories blocking it.
    Subtasks are chained so the census order never depends on timestamps.
    """
    milestone = _add_card(project, "Milestone 3: orchestration")
    story_ids: dict[str, str] = {}
    subtask_ids: dict[str, list[str]] = {}
    for key, count in stories.items():
        story = _add_card(project, f"Story {key}", milestone)
        chain: list[str] = []
        for n in range(1, count + 1):
            subtask = _add_card(project, f"{key.lower()}{n}: subtask {n} of story {key}", story)
            if chain:
                _block(project, subtask, chain[-1])
            chain.append(subtask)
        story_ids[key] = story
        subtask_ids[key] = chain
    for key, blockers in (blocked_by or {}).items():
        for blocker in blockers:
            _block(project, story_ids[key], story_ids[blocker])
    return {"milestone": milestone, "stories": story_ids, "subtasks": subtask_ids}


def _branch(project: Path, card_id: str) -> str:
    return dag.task_branch(PREFIX, board.show(card_id, repo_dir=project))


@dataclass
class FakeDriver:
    """Stands in for `cli.drive_subtask`. Never touches git or the board.

    `outcomes` scripts a card: missing means `done`, a `(phase, detail)` tuple
    means escalated at that phase, and an exception instance is raised.
    `warnings` gives a card's canned warnings. Every call is recorded, with a
    snapshot of the store's view of the run at that moment.
    """

    outcomes: dict[str, Any] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[models.Run | None] = field(default_factory=list)

    def __call__(
        self,
        *,
        store,
        run_id,
        card,
        parent,
        subtask,
        repo_dir,
        commands=(),
        allow_no_verification=False,
        runner_factory=None,
        should_stop=None,
    ) -> cli.SubtaskDrive:
        self.calls.append(
            {
                "card": card.id,
                "parent": parent.id,
                "branch": subtask.branch,
                "base": subtask.base_branch,
                "worktree": subtask.worktree_path,
                "status": subtask.status,
                "run_id": run_id,
                "repo_dir": repo_dir,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "runner_factory": runner_factory,
            }
        )
        self.snapshots.append(store.load_run(run_id))
        outcome = self.outcomes.get(card.id)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is None:
            summary = engine.SubtaskSummary(status="done")
        else:
            phase, detail = outcome
            summary = engine.SubtaskSummary(
                status="escalated", failed_phase=phase, detail=detail
            )
        return cli.SubtaskDrive(summary=summary, warnings=list(self.warnings.get(card.id, [])))


def _run(project: Path, milestone: str, driver: Any, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "repo_dir": project,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "driver": driver,
        "clock": lambda: STARTED_AT,
    }
    kwargs.update(overrides)
    return orchestrate.run_milestone(milestone, **kwargs)


def _load(project: Path, run_id: str) -> models.Run:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    return run


def _statuses(run: models.Run) -> dict[str, str]:
    """`{"run": status, <story id>: status, <subtask id>: status, ...}`."""
    found = {"run": run.status}
    for story in run.stories:
        found[story.card_id] = story.status
        for subtask in story.subtasks:
            found[subtask.card_id] = subtask.status
    return found


def _record_git(monkeypatch, fail_on: str | None = None) -> list[list[str]]:
    """Wrap `worktree.run_git` so every argv is recorded; optionally fail one verb."""
    calls: list[list[str]] = []
    real = worktree.run_git

    def recording(argv: list[str]) -> str:
        calls.append(list(argv))
        if fail_on is not None and fail_on in argv:
            raise worktree.GitError("could not reach origin", argv=argv, exit_code=128)
        return real(argv)

    monkeypatch.setattr(worktree, "run_git", recording)
    return calls



WAIT = 10.0
"""Seconds a lane-pool test waits on a barrier or event before failing instead of hanging."""

Gate = Callable[[Any], None]


def _await(event: threading.Event) -> None:
    assert event.wait(timeout=WAIT), "a gated test's event was never set"


def _await_stop(should_stop: Any) -> None:
    """Block until the run's stop is set, without sleeping.

    `run_milestone` hands each driver `event.is_set` (spec item 2), so the
    run's event is that bound method's `__self__`. Waiting on it is
    synchronisation by event, and it pins that wiring.
    """
    assert should_stop is not None, "the lane passed no should_stop"
    event = should_stop.__self__
    assert isinstance(event, threading.Event)
    _await(event)


def _meet(barrier: threading.Barrier) -> Gate:
    """A gate that holds a call until every party of `barrier` is in flight."""

    def gate(should_stop: Any) -> None:
        barrier.wait(timeout=WAIT)

    return gate


def _meet_then_await_stop(barrier: threading.Barrier) -> Gate:
    """A gate that meets `barrier`, then holds the call until the run's stop is set."""

    def gate(should_stop: Any) -> None:
        barrier.wait(timeout=WAIT)
        _await_stop(should_stop)

    return gate


def _census_levels(project: Path, milestone: str) -> list[list[str]]:
    """Each dispatch level's story ids in census order, the order lanes are submitted in.

    Sibling stories created in the same second are ordered by id, so a test that
    gives a lane a role by its queue position must read the order, not assume it.
    """
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    levels = orchestrate.plan_levels(plan.stories, branch_prefix=PREFIX, base_branch="main")
    return [[planned.story.id for planned in level] for level in levels]


def _subtasks_by_story(shape: dict[str, Any]) -> dict[str, list[str]]:
    return {shape["stories"][key]: shape["subtasks"][key] for key in shape["stories"]}


@dataclass
class GatedDriver:
    """A thread-safe stand-in for `cli.drive_subtask`, for the lane-pool tests.

    `gates[card]` runs first, with the driver's `should_stop`; tests put
    barriers and events there, never sleeps. Then `outcomes[card]` decides: an
    exception instance is raised, a `(phase, detail)` tuple escalates, and
    `"done"` finishes as a phase already running would. With no entry the fake
    reaches its simulated phase boundary: if `should_stop()` is true it parks
    as the engine does, with `"stopped before implement"`; otherwise it is
    done. Calls are recorded under a lock, `high_water` is the most calls ever
    in flight at once, and `returned[card]` is set when that card's call ends.
    """

    outcomes: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    returned: dict[str, threading.Event] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    high_water: int = 0
    in_flight: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(
        self,
        *,
        store,
        run_id,
        card,
        parent,
        subtask,
        repo_dir,
        commands=(),
        allow_no_verification=False,
        runner_factory=None,
        should_stop=None,
    ) -> cli.SubtaskDrive:
        with self.lock:
            self.calls.append(
                {"card": card.id, "parent": parent.id, "base": subtask.base_branch}
            )
            self.in_flight += 1
            self.high_water = max(self.high_water, self.in_flight)
        try:
            gate = self.gates.get(card.id)
            if gate is not None:
                gate(should_stop)
            outcome = self.outcomes.get(card.id)
            if isinstance(outcome, BaseException):
                raise outcome
            warnings = list(self.warnings.get(card.id, []))
            if isinstance(outcome, tuple):
                phase, detail = outcome
                summary = engine.SubtaskSummary(
                    status="escalated", failed_phase=phase, detail=detail
                )
            elif outcome != "done" and should_stop is not None and should_stop():
                summary = engine.SubtaskSummary(
                    status="stopped", detail="stopped before implement"
                )
            else:
                summary = engine.SubtaskSummary(status="done")
            return cli.SubtaskDrive(summary=summary, warnings=warnings)
        finally:
            with self.lock:
                self.in_flight -= 1
            if card.id in self.returned:
                self.returned[card.id].set()


@requires_git
@requires_brd
def test_subtasks_run_in_order_each_stacked_on_the_one_before(project):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    root = cli.resolve_repo_dir(project)
    driver = FakeDriver()
    factory = object()

    result = _run(
        project,
        shape["milestone"],
        driver,
        commands=["uv run pytest"],
        allow_no_verification=True,
        runner_factory=factory,
    )

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    branches = {card: _branch(project, card) for card in (a1, a2, b1)}
    assert [call["card"] for call in driver.calls] == [a1, a2, b1]
    assert [call["parent"] for call in driver.calls] == [story_a, story_a, story_b]
    assert [call["branch"] for call in driver.calls] == [branches[a1], branches[a2], branches[b1]]
    assert [call["base"] for call in driver.calls] == ["main", branches[a1], branches[a2]]
    assert [call["worktree"] for call in driver.calls] == [
        cli.worktree_for(root, branches[card]) for card in (a1, a2, b1)
    ]
    assert [call["status"] for call in driver.calls] == ["started"] * 3
    for call in driver.calls:
        assert call["run_id"] == run_id
        assert call["repo_dir"] == root
        assert call["commands"] == ["uv run pytest"]
        assert call["allow_no_verification"] is True
        assert call["runner_factory"] is factory

    assert result == {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": [story_a]}, {"level": 1, "stories": [story_b]}],
        "completed": [a1, a2, b1],
        "tips": [
            {"story": story_a, "tip": branches[a2]},
            {"story": story_b, "tip": branches[b1]},
        ],
        "warnings": [],
    }

    run = _load(project, run_id)
    assert run.workflow == "milestone"
    assert run.config == models.RunConfig()
    assert (run.base_branch, run.branch_prefix, run.repo_dir) == ("main", PREFIX, root)
    assert _statuses(run) == {
        "run": "done",
        story_a: "done",
        a1: "done",
        a2: "done",
        story_b: "done",
        b1: "done",
    }
    assert [(story.card_id, story.level, story.tip_branch) for story in run.stories] == [
        (story_a, 0, branches[a2]),
        (story_b, 1, branches[b1]),
    ]


@requires_git
@requires_brd
def test_the_whole_plan_is_recorded_pending_before_the_first_subtask_is_driven(project):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver()

    _run(project, shape["milestone"], driver)

    first = driver.snapshots[0]
    assert first is not None
    assert _statuses(first) == {
        "run": "started",
        story_a: "started",
        a1: "started",
        a2: "pending",
        story_b: "pending",
        b1: "pending",
    }
    assert {
        subtask.card_id: subtask.base_branch
        for story in first.stories
        for subtask in story.subtasks
    } == {a1: "main", a2: _branch(project, a1), b1: _branch(project, a2)}


@requires_git
@requires_brd
def test_a_done_subtask_is_skipped_but_still_anchors_the_next_base(project):
    shape = _milestone(project, {"A": 2})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    board.set_status(a1, "done", repo_dir=project)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert [call["card"] for call in driver.calls] == [a2]
    assert driver.calls[0]["base"] == _branch(project, a1)
    assert result["completed"] == [a2]
    run = _load(project, result["run_id"])
    assert [subtask.card_id for story in run.stories for subtask in story.subtasks] == [a2]
    assert _statuses(run) == {"run": "done", story_a: "done", a2: "done"}


@requires_git
@requires_brd
def test_a_story_blocked_by_a_done_story_roots_on_that_storys_tip(project):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    for card in (a1, a2, story_a):
        board.set_status(card, "done", repo_dir=project)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert [call["card"] for call in driver.calls] == [b1]
    assert driver.calls[0]["base"] == _branch(project, a2)
    assert result["levels"] == [{"level": 0, "stories": [story_b]}]
    assert result["tips"] == [
        {"story": story_a, "tip": _branch(project, a2)},
        {"story": story_b, "tip": _branch(project, b1)},
    ]


@requires_git
@requires_brd
def test_every_drivers_warnings_reach_the_result_in_order(project):
    shape = _milestone(project, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    driver = FakeDriver(warnings={a1: ["a1 warned"], a2: ["a2 warned", "a2 again"]})

    result = _run(project, shape["milestone"], driver)

    assert result["warnings"] == ["a1 warned", "a2 warned", "a2 again"]


@requires_git
@requires_brd
def test_no_driver_resolves_to_cli_drive_subtask_at_call_time(project, monkeypatch):
    """The sibling card makes `cli` import this module, so the default driver
    must be read off `cli` when the run starts, never bound at import."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    fake = FakeDriver()
    monkeypatch.setattr(cli, "drive_subtask", fake)

    result = _run(project, shape["milestone"], None)

    assert [call["card"] for call in fake.calls] == [a1]
    assert result["done"] is True


@requires_git
@requires_brd
def test_a_milestone_with_nothing_pending_still_records_a_done_run(project):
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    for card in (a1, story_a):
        board.set_status(card, "done", repo_dir=project)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert result == {
        "done": True,
        "run_id": cli.mint_run_id(shape["milestone"], STARTED_AT),
        "levels": [],
        "completed": [],
        "tips": [{"story": story_a, "tip": _branch(project, a1)}],
        "warnings": [],
    }
    run = _load(project, result["run_id"])
    assert _statuses(run) == {"run": "done"}


@requires_git
@requires_brd
def test_a_story_with_two_blockers_is_refused_before_anything_is_written(project, monkeypatch):
    milestone = _add_card(project, "Milestone 3: orchestration")
    first = _add_card(project, "Story one", milestone)
    second = _add_card(project, "Story two", milestone)
    joined = _add_card(project, "Story three", milestone)
    for story in (first, second, joined):
        _add_card(project, f"only subtask of {story}", story)
    _block(project, joined, first)
    _block(project, joined, second)
    porcelain_before = _git(project, "status", "--porcelain")
    git_calls = _record_git(monkeypatch)
    driver = FakeDriver()

    with pytest.raises(dag.StackRootError) as caught:
        _run(project, milestone, driver)

    assert f"#{first}" in str(caught.value)
    assert f"#{second}" in str(caught.value)
    assert driver.calls == []
    assert git_calls == []
    assert list(paths.data_dir().iterdir()) == []
    assert not (project / ".claude").exists()
    assert _git(project, "status", "--porcelain") == porcelain_before


@requires_git
@requires_brd
def test_a_workflow_that_will_not_load_is_refused_before_anything_is_written(
    project, monkeypatch
):
    """The preflight `run_card` does: a workflow that will not load refuses the
    run before the fetch, the prune, the store or the first subtask."""
    shape = _milestone(project, {"A": 1})
    monkeypatch.setattr(cli, "WORKFLOW_NAME", "no-such-workflow")
    git_calls = _record_git(monkeypatch)
    driver = FakeDriver()

    with pytest.raises(WorkflowLoadError) as caught:
        _run(project, shape["milestone"], driver)

    assert "no-such-workflow" in str(caught.value)
    assert driver.calls == []
    assert git_calls == []
    assert list(paths.data_dir().iterdir()) == []


@requires_git
@requires_brd
def test_an_escalation_stops_the_run_before_the_next_story(project):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        warnings={a1: ["gate warned before the escalation"]},
    )

    result = _run(project, shape["milestone"], driver)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 0,
        "story": story_a,
        "subtask": a1,
        "failed_phase": "review",
        "detail": "reviewer found a blocker",
        "warnings": ["gate warned before the escalation"],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        a2: "pending",
        story_b: "pending",
        b1: "pending",
    }


@requires_git
@requires_brd
def test_an_escalation_in_a_later_level_reports_that_level(project):
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver(outcomes={b1: ("verify", "suite red")})

    result = _run(project, shape["milestone"], driver)

    assert (result["level"], result["story"], result["subtask"]) == (1, story_b, b1)
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "escalated",
        b1: "escalated",
    }


@requires_git
@requires_brd
def test_a_driver_that_raises_is_recorded_as_an_escalation(project):
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver(outcomes={a1: RuntimeError("harness vanished")})

    result = _run(project, shape["milestone"], driver)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result["escalated"] is True
    assert (result["story"], result["subtask"]) == (story_a, a1)
    assert result["failed_phase"] is None
    assert result["detail"] == "RuntimeError: harness vanished"
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "pending",
        b1: "pending",
    }


@requires_git
@requires_brd
def test_a_keyboard_interrupt_from_the_driver_is_not_swallowed(project):
    """The catch is `Exception`, not `BaseException`: Ctrl-C stops the process,
    it is not an escalation a human should go and read."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    driver = FakeDriver(outcomes={a1: KeyboardInterrupt()})

    with pytest.raises(KeyboardInterrupt):
        _run(project, shape["milestone"], driver)


@requires_git
@requires_brd
def test_a_repo_with_no_origin_prunes_worktrees_and_never_fetches(project, monkeypatch):
    shape = _milestone(project, {"A": 1})
    root = cli.resolve_repo_dir(project)
    calls = _record_git(monkeypatch)

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True
    assert [argv[2:] for argv in calls] == [["remote"], ["worktree", "prune"]]
    assert all(argv[:2] == ["-C", str(root)] for argv in calls)


@requires_git
@requires_brd
def test_an_origin_remote_is_fetched_exactly_once_before_the_prune(
    project, tmp_path, monkeypatch
):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True
    )
    _git(project, "remote", "add", "origin", str(origin))
    shape = _milestone(project, {"A": 2})
    calls = _record_git(monkeypatch)

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True
    assert [argv[2:] for argv in calls] == [
        ["remote"],
        ["fetch", "origin"],
        ["worktree", "prune"],
    ]


@requires_git
@requires_brd
def test_a_remote_that_is_not_literally_origin_is_not_fetched(project, tmp_path, monkeypatch):
    other = tmp_path / "other.git"
    subprocess.run(
        ["git", "init", "--bare", str(other)], check=True, capture_output=True, text=True
    )
    _git(project, "remote", "add", "upstream", str(other))
    _git(project, "remote", "add", "origin-mirror", str(other))
    shape = _milestone(project, {"A": 1})
    calls = _record_git(monkeypatch)

    _run(project, shape["milestone"], FakeDriver())

    assert [argv[2:] for argv in calls] == [["remote"], ["worktree", "prune"]]


@requires_git
@requires_brd
def test_a_failed_fetch_propagates_and_leaves_no_run_behind(project, tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True
    )
    _git(project, "remote", "add", "origin", str(origin))
    shape = _milestone(project, {"A": 1})
    _record_git(monkeypatch, fail_on="fetch")
    driver = FakeDriver()

    with pytest.raises(worktree.GitError):
        _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert list(paths.data_dir().iterdir()) == []


def test_only_a_stale_story_is_anchored_and_on_its_last_done_subtask():
    """Port of `storyRollupAnchor`: a story that is not closed and has nothing
    left to run is re-rolled through its last individually done subtask. A
    closed story, a story with work left and a story with no subtasks are not."""
    stale = _plan_story(
        1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="in_progress"
    )
    closed = _plan_story(2, [_plan_subtask(21, "done")], status="done")
    pending = _plan_story(3, [_plan_subtask(31, "done"), _plan_subtask(32)])
    empty = _plan_story(4, [])

    anchors = orchestrate.stale_story_anchors([stale, closed, pending, empty])

    assert [(story.id, anchor.id) for story, anchor in anchors] == [(stale.id, _plan_id(12))]


@requires_git
@requires_brd
def test_a_stale_story_is_rolled_up_to_done_and_so_is_the_milestone(project):
    shape = _milestone(project, {"A": 2})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    board.set_status(a1, "done", repo_dir=project)
    board.set_status(a2, "done", repo_dir=project)
    assert board.show(story_a, repo_dir=project).status == "todo"
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert result["done"] is True
    assert result["levels"] == []
    assert result["warnings"] == []
    assert board.show(story_a, repo_dir=project).status == "done"
    assert board.show(shape["milestone"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_a_failed_stale_rollup_is_a_warning_and_the_run_goes_on(project, monkeypatch):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    board.set_status(a1, "done", repo_dir=project)
    board.set_status(a2, "done", repo_dir=project)

    def failing(card: str, status: str, repo_dir: Any = None) -> dict[str, object]:
        raise board.BoardError("brd is down", argv=["brd", "update", card])

    monkeypatch.setattr(rollup, "set_status", failing)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert [call["card"] for call in driver.calls] == [b1]
    assert result["done"] is True
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert story_a in warning
    assert a2 in warning
    assert "brd is down" in warning


@requires_git
@requires_brd
def test_a_board_read_that_fails_inside_a_lane_is_an_escalation_of_that_subtask(
    project, monkeypatch
):
    """Spec item 6: an `Exception` anywhere inside a lane after it picked up a
    subtask escalates that subtask, not only one raised by the driver."""
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    real_show = board.show

    def failing_show(card_id: str, *, repo_dir: Any = None) -> models.Card:
        if card_id == a1:
            raise board.BoardError("brd is down", argv=["brd", "show", card_id])
        return real_show(card_id, repo_dir=repo_dir)

    monkeypatch.setattr(board, "show", failing_show)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert (result["escalated"], result["level"], result["story"], result["subtask"]) == (
        True,
        0,
        story_a,
        a1,
    )
    assert result["failed_phase"] is None
    assert result["detail"].startswith("BoardError: ")
    assert "brd is down" in result["detail"]
    assert "also_escalated" not in result
    assert "stopped" not in result
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "pending",
        b1: "pending",
    }


@requires_git
@requires_brd
@pytest.mark.parametrize("bound", [0, -1])
def test_a_bound_below_one_is_refused_before_anything_is_written(project, monkeypatch, bound):
    shape = _milestone(project, {"A": 1})
    git_calls = _record_git(monkeypatch)
    driver = FakeDriver()

    with pytest.raises(ValueError, match="max_concurrent"):
        _run(project, shape["milestone"], driver, max_concurrent=bound)

    assert driver.calls == []
    assert git_calls == []
    assert list(paths.data_dir().iterdir()) == []


@requires_git
@requires_brd
def test_the_bound_is_recorded_in_the_run_config(project):
    shape = _milestone(project, {"A": 1})

    result = _run(project, shape["milestone"], FakeDriver(), max_concurrent=2)

    assert result["done"] is True
    assert _load(project, result["run_id"]).config == models.RunConfig(max_concurrent_stories=2)


@requires_git
@requires_brd
def test_every_story_of_a_level_runs_at_once_and_each_keeps_its_subtask_order(project):
    shape = _milestone(project, {"A": 2, "B": 1, "C": 1})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    all_three = threading.Barrier(3)
    driver = GatedDriver(gates={a1: _meet(all_three), b1: _meet(all_three), c1: _meet(all_three)})

    result = _run(project, shape["milestone"], driver, max_concurrent=3)

    (order,) = _census_levels(project, shape["milestone"])
    subtasks = _subtasks_by_story(shape)
    cards = [call["card"] for call in driver.calls]
    assert sorted(cards) == sorted([a1, a2, b1, c1])
    assert cards.index(a1) < cards.index(a2)
    assert {call["card"]: call["base"] for call in driver.calls} == {
        a1: "main",
        a2: _branch(project, a1),
        b1: "main",
        c1: "main",
    }
    assert {call["card"]: call["parent"] for call in driver.calls} == {
        a1: story_a,
        a2: story_a,
        b1: story_b,
        c1: story_c,
    }
    assert driver.high_water == 3
    assert result["done"] is True
    assert result["levels"] == [{"level": 0, "stories": order}]
    assert result["completed"] == [card for story in order for card in subtasks[story]]
    assert "also_escalated" not in result
    assert "stopped" not in result
    run = _load(project, result["run_id"])
    assert run.config == models.RunConfig(max_concurrent_stories=3)
    assert set(_statuses(run).values()) == {"done"}


@requires_git
@requires_brd
def test_in_flight_lanes_never_exceed_the_bound(project):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1, "D": 1})
    (order,) = _census_levels(project, shape["milestone"])
    subtasks = _subtasks_by_story(shape)
    pair = threading.Barrier(2)
    driver = GatedDriver(
        gates={subtasks[order[0]][0]: _meet(pair), subtasks[order[1]][0]: _meet(pair)}
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True
    assert sorted(call["card"] for call in driver.calls) == sorted(
        card for cards in subtasks.values() for card in cards
    )
    assert driver.high_water == 2


@requires_git
@requires_brd
def test_an_escalation_parks_the_other_lane_and_no_later_level_starts(project):
    shape = _milestone(project, {"A": 1, "B": 2, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert sorted(call["card"] for call in driver.calls) == sorted([a1, b1])
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 0,
        "story": story_a,
        "subtask": a1,
        "failed_phase": "review",
        "detail": "reviewer found a blocker",
        "warnings": [],
        "stopped": [{"story": story_b, "subtask": b1, "before_phase": "implement"}],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
        b2: "pending",
        story_c: "pending",
        c1: "pending",
    }


@requires_git
@requires_brd
def test_a_lane_whose_first_subtask_finished_parks_its_next_subtask(project):
    """After a story has started the lane never checks the stop itself: its
    next subtask is handed to the driver, and the engine parks it (P4)."""
    shape = _milestone(project, {"A": 1, "B": 2})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("verify", "suite red"), b1: "done"},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert sorted(call["card"] for call in driver.calls) == sorted([a1, b1, b2])
    assert next(call for call in driver.calls if call["card"] == b2)["base"] == _branch(
        project, b1
    )
    assert (result["story"], result["subtask"]) == (story_a, a1)
    assert result["stopped"] == [{"story": story_b, "subtask": b2, "before_phase": "implement"}]
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "done",
        b2: "stopped",
    }


@requires_git
@requires_brd
def test_two_simultaneous_escalations_give_one_primary_and_one_also_escalated(project):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("review", "a blocker"), b1: ("verify", "b suite red")},
        gates={a1: _meet(pair), b1: _meet(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    entries = {
        story_a: {
            "level": 0,
            "story": story_a,
            "subtask": a1,
            "failed_phase": "review",
            "detail": "a blocker",
        },
        story_b: {
            "level": 0,
            "story": story_b,
            "subtask": b1,
            "failed_phase": "verify",
            "detail": "b suite red",
        },
    }
    primary = result["story"]
    assert primary in entries
    (other,) = set(entries) - {primary}
    assert result == {
        "escalated": True,
        "run_id": run_id,
        **entries[primary],
        "warnings": [],
        "also_escalated": [entries[other]],
    }
    assert c1 not in [call["card"] for call in driver.calls]
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "escalated",
        b1: "escalated",
        story_c: "pending",
        c1: "pending",
    }


@requires_git
@requires_brd
def test_a_lane_that_raises_escalates_and_parks_its_sibling(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: RuntimeError("harness vanished")},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 0,
        "story": story_a,
        "subtask": a1,
        "failed_phase": None,
        "detail": "RuntimeError: harness vanished",
        "warnings": [],
        "stopped": [{"story": story_b, "subtask": b1, "before_phase": "implement"}],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
    }


@requires_git
@requires_brd
def test_a_story_queued_behind_the_bound_stays_pending_after_a_stop(project):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    (first, second, queued) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,), (q1,) = subtasks[first], subtasks[second], subtasks[queued]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={f1: ("review", "reviewer found a blocker")},
        gates={f1: _meet(pair), s1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert q1 not in [call["card"] for call in driver.calls]
    assert (result["story"], result["subtask"]) == (first, f1)
    assert result["stopped"] == [{"story": second, "subtask": s1, "before_phase": "implement"}]
    assert "also_escalated" not in result
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        first: "escalated",
        f1: "escalated",
        second: "stopped",
        s1: "stopped",
        queued: "pending",
        q1: "pending",
    }


@requires_git
@requires_brd
def test_a_keyboard_interrupt_in_one_lane_parks_the_other_and_propagates(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: KeyboardInterrupt()},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    with pytest.raises(KeyboardInterrupt):
        _run(project, shape["milestone"], driver, max_concurrent=2)

    run = _load(project, cli.mint_run_id(shape["milestone"], STARTED_AT))
    assert _statuses(run) == {
        "run": "started",
        story_a: "started",
        a1: "started",
        story_b: "stopped",
        b1: "stopped",
    }


@requires_git
@requires_brd
def test_warnings_and_completed_follow_census_order_not_finish_order(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    (first, second) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,) = subtasks[first], subtasks[second]
    second_returned = threading.Event()
    driver = GatedDriver(
        warnings={f1: ["first warned"], s1: ["second warned"]},
        gates={f1: lambda should_stop: _await(second_returned)},
        returned={s1: second_returned},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert sorted(call["card"] for call in driver.calls) == sorted([f1, s1])
    assert result["done"] is True
    assert result["completed"] == [f1, s1]
    assert result["warnings"] == ["first warned", "second warned"]
