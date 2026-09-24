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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import board, census, cli, dag, engine, models, orchestrate, paths
from agent_manager import store as store_module
from agent_manager.steps import rollup, worktree


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
