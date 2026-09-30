"""Behaviour of the milestone runner (orchestration addendum O6, Integrate I6).

Two tiers, per design §14:

- `plan_levels`, `story_tips`, `stale_story_anchors` and the payload helpers
  are pure and get unit tests on hand-built plans or outcomes;
- `run_milestone` runs on Steps-tier fixtures -- a real temporary git repo and a
  real temporary brd board, with `XDG_DATA_HOME` under `tmp_path` so
  `paths.data_dir()` never touches the developer's own -- with the harness
  replaced at the injected `driver` seam by an awaitable fake that runs on the
  run's one event loop (supervisor-tree T3). No runner, adapter or `claude` is
  involved; production wiring under a fake `claude` belongs to tests/e2e.

`FakeDriver` makes no branches, so by default (`integrate_recorder`, autouse)
Integrate is replaced at its own call-time seam, `integration.integrate_milestone`,
by a recorder. Tests that request `real_integrate` run the real Integrate over
branches `BranchingDriver` or `_commit_branch` really commit.
"""

import ast
import asyncio
import inspect
import json
import logging
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import grafo
import pytest

from agent_manager import bases, board, census, cli, control, dag, integration, models, orchestrate, paths
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.runtime.walk import SubtaskSummary
from agent_manager import store as store_module
from agent_manager.steps import rollup, worktree
from agent_manager.workflow import integrate as integrate_workflow
from agent_manager.workflow import task as task_workflow
from agent_manager.workflow.phases import Step, Workflow


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


def test_plan_levels_roots_a_two_blocker_story_on_its_merged_base():
    """Supervisor-tree §5: a story with two in-milestone blockers is planned,
    not refused. Its first subtask stacks on its own merged base
    `<prefix>/base-<short id>`, the rest on the subtask before them."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31), _plan_subtask(32)], blocked_by=[b.id, "outside", a.id])

    levels = orchestrate.plan_levels([a, b, c], branch_prefix="m3", base_branch="main")

    assert [[planned.story.id for planned in level] for level in levels] == [[a.id, b.id], [c.id]]
    c_plan = levels[1][0]
    assert c_plan.bases == {
        _plan_id(31): "m3/base-00000003",
        _plan_id(32): _branch_of(c.subtasks[0]),
    }
    assert c_plan.tip == _branch_of(c.subtasks[-1])


def test_plan_levels_refuses_a_blocker_cycle_before_any_geometry():
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    with pytest.raises(dag.DependencyCycleError):
        orchestrate.plan_levels([a, b], branch_prefix="m3", base_branch="main")


def test_plan_levels_roots_a_story_behind_a_subtask_less_two_blocker_story_on_that_base():
    """A subtask-less story on two blockers has a merged root, and a story it
    blocks falls through to it, as `dag.story_tip` does. The joined story has
    nothing to drive, so it is in no wave, and the story behind it lands in
    wave 0 and stacks on the joined story's base."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    joined = _plan_story(3, [], blocked_by=[a.id, b.id])
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[joined.id])

    levels = orchestrate.plan_levels([a, b, joined, d], branch_prefix="m3", base_branch="main")

    assert [[planned.story.id for planned in level] for level in levels] == [[a.id, b.id, d.id]]
    d_plan = levels[0][2]
    assert d_plan.bases == {_plan_id(41): "m3/base-00000003"}


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
    """`walk._stop` writes "stopped before <phase>"; the summary has no field
    of its own for that phase, so the helper reads it out of `detail`."""
    assert orchestrate.stopped_before_phase("stopped before implement") == "implement"
    assert orchestrate.stopped_before_phase("reviewer found a blocker") is None
    assert orchestrate.stopped_before_phase(None) is None


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
    queued = orchestrate.LaneOutcome(kind="pending", story="E", level=2)

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
    queued = orchestrate.LaneOutcome(kind="pending", story="B", level=0)

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


def test_an_unnamed_primary_falls_back_to_the_first_escalation_in_census_order():
    first = orchestrate.LaneOutcome(
        kind="escalated", story="A", level=1, subtask="a1", failed_phase="review", detail="x"
    )
    second = orchestrate.LaneOutcome(
        kind="escalated", story="B", level=1, subtask="b1", failed_phase="verify", detail="y"
    )

    payload = orchestrate.escalated_payload("run-1", None, [first, second], [])

    assert (payload["story"], payload["subtask"]) == ("A", "a1")
    assert payload["also_escalated"] == [
        {"level": 1, "story": "B", "subtask": "b1", "failed_phase": "verify", "detail": "y"}
    ]


def test_controlled_payload_on_pause_lists_stopped_completed_pending_and_the_resume_hint():
    """C12: every stopped lane in census order, every lane's completed work in
    wave order (not only a stopped lane's), the pending stories, and the hint."""
    parked = orchestrate.LaneOutcome(
        kind="stopped",
        story="A",
        level=0,
        subtask="a2",
        before_phase="implement",
        completed=("a1",),
    )
    finished = orchestrate.LaneOutcome(kind="done", story="B", level=0, completed=("b1", "b2"))
    between = orchestrate.LaneOutcome(kind="stopped", story="C", level=1, subtask="c1")
    queued = orchestrate.LaneOutcome(kind="pending", story="D", level=1)

    payload = orchestrate.controlled_payload(
        "run-1", "pause", [parked, finished, between, queued], ["gate warned"]
    )

    assert payload == {
        "paused": True,
        "run_id": "run-1",
        "stopped": [
            {"story": "A", "subtask": "a2", "before_phase": "implement"},
            {"story": "C", "subtask": "c1", "before_phase": None},
        ],
        "completed": ["a1", "b1", "b2"],
        "pending": ["D"],
        "warnings": ["gate warned"],
        "resume": "am resume run-1",
    }
    assert list(payload) == [
        "paused", "run_id", "stopped", "completed", "pending", "warnings", "resume"
    ]


def test_controlled_payload_on_cancel_has_no_resume_and_lists_escalations_primary_first():
    first = orchestrate.LaneOutcome(
        kind="escalated", story="A", level=0, subtask="a1", failed_phase="review", detail="x"
    )
    parked = orchestrate.LaneOutcome(
        kind="stopped", story="B", level=0, subtask="b1", before_phase="implement"
    )
    primary = orchestrate.LaneOutcome(
        kind="escalated",
        story="C",
        level=0,
        subtask="c1",
        failed_phase="verify",
        detail="y",
        primary=True,
    )

    payload = orchestrate.controlled_payload("run-1", "cancel", [first, parked, primary], [])

    assert payload == {
        "cancelled": True,
        "run_id": "run-1",
        "stopped": [{"story": "B", "subtask": "b1", "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
        "escalations": [
            {"level": 0, "story": "C", "subtask": "c1", "failed_phase": "verify", "detail": "y"},
            {"level": 0, "story": "A", "subtask": "a1", "failed_phase": "review", "detail": "x"},
        ],
    }
    assert list(payload) == [
        "cancelled", "run_id", "stopped", "completed", "pending", "warnings", "escalations"
    ]
    # No outcome marked primary: the first in census order leads, as in `escalated_payload`.
    unmarked = orchestrate.controlled_payload(
        "run-1", "cancel", [first, replace(primary, primary=False)], []
    )
    assert [row["story"] for row in unmarked["escalations"]] == ["A", "C"]


def test_controlled_payload_on_cancel_without_escalations_omits_escalations_and_never_has_escalated():
    parked = orchestrate.LaneOutcome(
        kind="stopped", story="A", level=0, subtask="a1", before_phase="plan"
    )

    for command in ("pause", "cancel"):
        payload = orchestrate.controlled_payload("run-1", command, [parked], [])
        assert "escalated" not in payload
        assert "failed_phase" not in payload
    cancelled = orchestrate.controlled_payload("run-1", "cancel", [parked], [])
    assert "escalations" not in cancelled
    assert "resume" not in cancelled
    assert "paused" not in cancelled


def test_the_integrated_payload_is_plain_json_with_the_worktree_as_a_string():
    outcome = integration.IntegrateSuccess(
        branch="m3-integrate",
        worktree=Path("/repo/.claude/worktrees/m3-integrate"),
        merged=["A", "B"],
        resolved=["B"],
    )

    assert orchestrate.integrated_payload(outcome) == {
        "branch": "m3-integrate",
        "worktree": "/repo/.claude/worktrees/m3-integrate",
        "merged": ["A", "B"],
        "resolved": ["B"],
    }


def test_the_integrate_escalation_payload_names_the_phase_story_and_files():
    outcome = integration.IntegrateEscalation(
        story="B", files=["shared.txt"], detail="the resolver did not finish"
    )

    payload = orchestrate.integrate_escalated_payload("run-1", outcome, ["gate warned"])

    assert payload == {
        "escalated": True,
        "phase": "integrate",
        "story": "B",
        "files": ["shared.txt"],
        "detail": "the resolver did not finish",
        "run_id": "run-1",
        "warnings": ["gate warned"],
    }


def test_a_final_verification_escalation_payload_has_no_story():
    outcome = integration.IntegrateEscalation(story=None, files=[], detail="suite red")

    payload = orchestrate.integrate_escalated_payload("run-1", outcome, [])

    assert (payload["story"], payload["files"], payload["phase"]) == (None, [], "integrate")


def test_the_bases_payload_lists_every_built_base_in_outcome_order():
    """Spec, Report: one entry per lane that built its merged base, in the
    order `collect_outcomes` gave (wave order), blockers in `root_plan` order.
    An outcome with no base contributes nothing, whatever its kind."""
    first = dag.RootPlan("merged", "m3/base-00000003", (_plan_id(2), _plan_id(1)))
    second = dag.RootPlan("merged", "m3/base-00000005", (_plan_id(4), _plan_id(3)))
    outcomes = [
        orchestrate.LaneOutcome(kind="done", story=_plan_id(1), level=0),
        orchestrate.LaneOutcome(kind="done", story=_plan_id(3), level=1, base=first),
        orchestrate.LaneOutcome(
            kind="escalated", story=_plan_id(5), level=2, subtask=_plan_id(51), base=second
        ),
        orchestrate.LaneOutcome(kind="pending", story=_plan_id(6), level=2),
    ]

    assert orchestrate.bases_payload(outcomes) == [
        {"story": _plan_id(3), "branch": "m3/base-00000003", "blockers": [_plan_id(2), _plan_id(1)]},
        {"story": _plan_id(5), "branch": "m3/base-00000005", "blockers": [_plan_id(4), _plan_id(3)]},
    ]
    assert orchestrate.bases_payload([]) == []


def test_the_bases_key_is_added_only_when_a_base_was_built():
    entry = {"story": _plan_id(3), "branch": "m3/base-00000003", "blockers": [_plan_id(1)]}

    assert orchestrate.with_bases({"done": True}, []) == {"done": True}
    assert orchestrate.with_bases({"done": True}, [entry]) == {"done": True, "bases": [entry]}


def test_a_lane_outcome_has_no_base_by_default():
    assert orchestrate.LaneOutcome(kind="done", story="A", level=0).base is None


def _supervisor_plan(stories: list[census.StoryPlan]) -> orchestrate.SupervisorPlan:
    levels = orchestrate.plan_levels(stories, branch_prefix="m3", base_branch="main")
    return orchestrate.supervisor_plan(
        stories, levels, {}, branch_prefix="m3", base_branch="main"
    )


def test_lane_errors_carry_their_outcome():
    escalated = orchestrate.LaneOutcome(
        kind="escalated", story="A", level=0, subtask="a1", detail="red"
    )
    stopped = orchestrate.LaneOutcome(kind="stopped", story="B", level=0, subtask="b1")

    raised = orchestrate.LaneEscalated(escalated)
    parked = orchestrate.LaneStopped(stopped)

    assert raised.outcome is escalated
    assert parked.outcome is stopped
    assert isinstance(raised, Exception) and isinstance(parked, Exception)
    assert not isinstance(raised, orchestrate.LaneStopped)


def test_the_supervisor_plan_roots_and_tips_every_census_story_done_ones_included():
    """T1: every census story becomes a node, so every one needs its root and
    tip; only the pending ones are planned for a lane."""
    a = _plan_story(1, [_plan_subtask(11, "done")], status="done")
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[a.id])

    plan = _supervisor_plan([a, b])

    assert [story.id for story in plan.stories] == [a.id, b.id]
    assert plan.roots[a.id] == dag.RootPlan("base", "main", ())
    assert plan.roots[b.id] == dag.RootPlan("tip", _branch_of(a.subtasks[-1]), (a.id,))
    assert plan.tips == {
        a.id: _branch_of(a.subtasks[-1]),
        b.id: _branch_of(b.subtasks[-1]),
    }
    assert list(plan.planned) == [b.id]
    assert plan.planned[b.id].level == 0


def test_outcomes_follow_t6_in_wave_order():
    """Node output with a finished outcome is `done`; the first LaneEscalated in
    `errors` is primary and the next is not; LaneStopped gives its outcome; a
    story with no output and no error is `pending`."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)])
    e = _plan_story(5, [_plan_subtask(51)])
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[a.id])
    plan = _supervisor_plan([a, b, c, d, e])
    done_a = orchestrate.LaneOutcome(kind="done", story=a.id, level=0, completed=(_plan_id(11),))
    b_escalated = orchestrate.LaneOutcome(
        kind="escalated", story=b.id, level=0, subtask=_plan_id(21), failed_phase="review", detail="b"
    )
    c_escalated = orchestrate.LaneOutcome(
        kind="escalated", story=c.id, level=0, subtask=_plan_id(31), failed_phase="verify", detail="c"
    )
    e_stopped = orchestrate.LaneOutcome(
        kind="stopped", story=e.id, level=0, subtask=_plan_id(51), before_phase="implement"
    )
    nodes = {
        a.id: SimpleNamespace(output="tip of a"),
        b.id: SimpleNamespace(output=None),
        c.id: SimpleNamespace(output=None),
        d.id: SimpleNamespace(output=None),
        e.id: SimpleNamespace(output=None),
    }
    errors = [
        orchestrate.LaneEscalated(c_escalated),
        orchestrate.LaneStopped(e_stopped),
        orchestrate.LaneEscalated(b_escalated),
    ]

    outcomes = orchestrate.collect_outcomes(plan, nodes, errors, {a.id: done_a})

    assert outcomes == [
        done_a,
        b_escalated,
        replace(c_escalated, primary=True),
        e_stopped,
        orchestrate.LaneOutcome(kind="pending", story=d.id, level=1),
    ]


def test_an_error_that_is_no_lane_error_is_escalated_with_its_type_and_message():
    """T6: an exception that escaped even the lane's own catch-all is tied to no
    story, so it carries no subtask, failed phase or level."""
    a = _plan_story(1, [_plan_subtask(11)])
    plan = _supervisor_plan([a])

    outcomes = orchestrate.collect_outcomes(
        plan, {a.id: SimpleNamespace(output=None)}, [RuntimeError("grafo broke")], {}
    )

    foreign = orchestrate.LaneOutcome(
        kind="escalated", story=None, level=None, detail="RuntimeError: grafo broke"
    )
    assert outcomes == [orchestrate.LaneOutcome(kind="pending", story=a.id, level=0), foreign]
    payload = orchestrate.escalated_payload("run-1", None, outcomes, [])
    assert (payload["story"], payload["subtask"], payload["failed_phase"], payload["level"]) == (
        None,
        None,
        None,
        None,
    )
    assert payload["detail"] == "RuntimeError: grafo broke"


def test_only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone():
    merged = dag.RootPlan("merged", "m3/base-00000003", (_plan_id(1), _plan_id(2)))
    lone = dag.RootPlan("tip", "m3/some-tip", (_plan_id(1),))

    assert orchestrate.builds_a_base_alone(_plan_story(3, []), merged) is True
    assert orchestrate.builds_a_base_alone(_plan_story(3, [], status="done"), merged) is False
    assert orchestrate.builds_a_base_alone(_plan_story(3, [_plan_subtask(31)]), merged) is False
    assert orchestrate.builds_a_base_alone(_plan_story(3, []), lone) is False


def test_a_base_only_lanes_outcome_follows_the_waves_in_census_order():
    """A subtask-less story is in no wave, so its lane's outcome comes after
    every wave's, before any foreign error; a failure is not dropped."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    joined = _plan_story(3, [], blocked_by=[a.id, b.id])
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[joined.id])
    plan = _supervisor_plan([a, b, joined, d])
    done_a = orchestrate.LaneOutcome(kind="done", story=a.id, level=0)
    done_b = orchestrate.LaneOutcome(kind="done", story=b.id, level=0)
    done_d = orchestrate.LaneOutcome(kind="done", story=d.id, level=0)
    built = orchestrate.LaneOutcome(
        kind="done", story=joined.id, level=None, base=plan.roots[joined.id]
    )
    every_output = {story.id: SimpleNamespace(output="tip") for story in (a, b, joined, d)}

    assert orchestrate.collect_outcomes(
        plan, every_output, [], {a.id: done_a, b.id: done_b, d.id: done_d, joined.id: built}
    ) == [done_a, done_b, done_d, built]

    failed = orchestrate.LaneOutcome(
        kind="escalated", story=joined.id, level=None, failed_phase="base", detail="broke"
    )
    outputs = {
        a.id: SimpleNamespace(output="tip"),
        b.id: SimpleNamespace(output="tip"),
        joined.id: SimpleNamespace(output=None),
        d.id: SimpleNamespace(output=None),
    }

    assert orchestrate.collect_outcomes(
        plan, outputs, [orchestrate.LaneEscalated(failed)], {a.id: done_a, b.id: done_b}
    ) == [
        done_a,
        done_b,
        orchestrate.LaneOutcome(kind="pending", story=d.id, level=0),
        replace(failed, primary=True),
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

LATER = datetime(2026, 9, 24, 13, 0, 0, tzinfo=timezone.utc)
"""A relaunch's clock: a second run needs its own run id."""

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch(PREFIX)`, spelled out so a rename is caught."""

PASS_CMD = shlex.join([sys.executable, "-c", "print('suite green')"])
FAIL_CMD = shlex.join(
    [sys.executable, "-c", "import sys; print('suite is red'); sys.exit(3)"]
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _sha(cwd: Path, ref: str) -> str:
    return _git(cwd, "rev-parse", ref).strip()


def _is_ancestor(cwd: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _local_branches(cwd: Path) -> list[str]:
    return _git(cwd, "branch", "--format=%(refname:short)").split()


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
    """Stands in for `cli.drive_subtask_async`. Never touches git or the board.

    `outcomes` scripts a card: missing means `done`, a `(phase, detail)` tuple
    means escalated at that phase, and an exception instance is raised.
    `warnings` gives a card's canned warnings. Every call is recorded, with a
    snapshot of the store's view of the run at that moment.
    """

    outcomes: dict[str, Any] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[models.Run | None] = field(default_factory=list)

    async def __call__(
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
        stop=None,
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
                "stop": stop,
            }
        )
        self.snapshots.append(store.load_run(run_id))
        outcome = self.outcomes.get(card.id)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is None:
            summary = SubtaskSummary(status="done")
        else:
            phase, detail = outcome
            summary = SubtaskSummary(
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


REAL_INTEGRATE = integration.integrate_milestone
"""Captured at import, before `integrate_recorder` swaps it."""


@dataclass
class IntegrateRecorder:
    """Stands in for `integration.integrate_milestone`, read by `run_milestone` at call time.

    Every call is recorded with the run's status at that moment, which is how
    a test sees that Integrate ran before the run was recorded. `outcome`
    scripts the result: `None` is a success shaped by the real `merge_order`,
    an `IntegrateEscalation` is returned, an exception instance is raised.
    """

    outcome: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self,
        stories,
        repo_dir,
        base_branch,
        branch_prefix,
        commands,
        allow_no_verification,
        store,
        run_id,
        runner_factory,
    ):
        stories = list(stories)
        self.calls.append(
            {
                "stories": [story.id for story in stories],
                "repo_dir": repo_dir,
                "base_branch": base_branch,
                "branch_prefix": branch_prefix,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "store": store,
                "run_id": run_id,
                "runner_factory": runner_factory,
                "run_status": store.load_run(run_id).status,
            }
        )
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if self.outcome is not None:
            return self.outcome
        branch = integration.integration_branch(branch_prefix)
        return integration.IntegrateSuccess(
            branch=branch,
            worktree=cli.worktree_for(repo_dir, branch),
            merged=[
                story.id
                for story, _ in integration.merge_order(stories, branch_prefix, base_branch)
            ],
        )


@pytest.fixture(autouse=True)
def integrate_recorder(monkeypatch) -> IntegrateRecorder:
    recorder = IntegrateRecorder()
    monkeypatch.setattr(integration, "integrate_milestone", recorder)
    return recorder


@pytest.fixture
def real_integrate(monkeypatch, integrate_recorder) -> None:
    """Undo `integrate_recorder`: this test runs the real Integrate."""
    monkeypatch.setattr(integration, "integrate_milestone", REAL_INTEGRATE)


def _integrated(root: Path, merged: list[str]) -> dict[str, Any]:
    """The `integrated` key a clean run reports when no tip needed a resolver."""
    return {
        "branch": INTEGRATION_BRANCH,
        "worktree": str(cli.worktree_for(root, INTEGRATION_BRANCH)),
        "merged": merged,
        "resolved": [],
    }


def _no_resolver(**kwargs: Any) -> Any:
    """A runner factory for tests whose tips never conflict: a resolver is a failure."""
    pytest.fail("Integrate dispatched a resolver, but no tip in this test conflicts")


def _commit_branch(root: Path, branch: str, base: str, card_id: str) -> None:
    """Cut `branch` from `base` in its own worktree and commit one file named for the card.

    Each card writes its own file, so no two story tips ever conflict.
    """
    path = cli.worktree_for(root, branch)
    _git(root, "worktree", "add", "-b", branch, str(path), base)
    (path / f"{dag.short_id(card_id)}.txt").write_text(f"work of {card_id}\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", f"work of {card_id}")


@dataclass
class BranchingDriver(FakeDriver):
    """A `FakeDriver` whose `done` subtask leaves what the real one does.

    A branch with one commit on the subtask's base, and the card `done` on the
    board through the rollup, as `mark_done` does. Sequential runs only.
    """

    async def __call__(self, *, store, run_id, card, parent, subtask, repo_dir, **kwargs):
        drive = await super().__call__(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=repo_dir,
            **kwargs,
        )
        if drive.summary.status == "done":
            _commit_branch(repo_dir, subtask.branch, subtask.base_branch, card.id)
            rollup.set_status(card.id, "done", repo_dir=repo_dir)
        return drive


def _census_stories(project: Path, milestone: str) -> list[str]:
    """Story ids in census order: siblings made in one second are ordered by id."""
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    return [story.id for story in plan.stories]


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
"""Seconds a supervisor test waits on a barrier or event before failing instead of hanging."""

OVERSHOOT_WINDOW = 1.0
"""Seconds the bound test holds each lane in flight, so that queued lanes would
enter the driver in that window if the lanes ignored `max_concurrent`."""

PATCHED_NODE_TIMEOUT = 0.05
"""grafo's default node timeout, patched down by the timeout test."""

LONGER_THAN_PATCHED_TIMEOUT = 0.3
"""How long that test's lane stays in flight: well past `PATCHED_NODE_TIMEOUT`."""

Gate = Callable[[StopSignal | None], Awaitable[None]]


async def _within(awaitable: Awaitable[Any], what: str) -> Any:
    """Await `awaitable`, failing after WAIT seconds instead of hanging the run.

    The failure is an `AssertionError` inside the driver, so the lane turns it
    into an escalation whose detail names what never happened.
    """
    try:
        return await asyncio.wait_for(awaitable, WAIT)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {what}") from None


class _StopWatch:
    """A `pause()`-only stand-in registered on the run's `StopSignal`, as a
    pygents subtask agent is: the signal pauses it when it fires."""

    def __init__(self) -> None:
        self.paused = asyncio.Event()

    def pause(self) -> None:
        self.paused.set()


async def _await_stop(stop: StopSignal | None) -> None:
    """Block until the run's stop fires, without sleeping. Pins that the lane
    handed the driver the run's `StopSignal` (T5)."""
    assert isinstance(stop, StopSignal), "the lane passed no StopSignal"
    watch = _StopWatch()
    stop.register(watch)
    try:
        await _within(watch.paused.wait(), "the run's stop")
    finally:
        stop.unregister(watch)


def _meet(barrier: asyncio.Barrier) -> Gate:
    """A gate that holds a call until every party of `barrier` is in flight."""

    async def gate(stop: StopSignal | None) -> None:
        await _within(barrier.wait(), "every party of the barrier")

    return gate


def _meet_then_await_stop(barrier: asyncio.Barrier) -> Gate:
    """A gate that meets `barrier`, then holds the call until the run's stop fires."""

    async def gate(stop: StopSignal | None) -> None:
        await _within(barrier.wait(), "every party of the barrier")
        await _await_stop(stop)

    return gate


def _census_levels(project: Path, milestone: str) -> list[list[str]]:
    """Each wave's story ids in census order, the order the tree starts them in.

    Sibling stories created in the same second are ordered by id, so a test that
    gives a lane a role by its position must read the order, not assume it.
    """
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    levels = orchestrate.plan_levels(plan.stories, branch_prefix=PREFIX, base_branch="main")
    return [[planned.story.id for planned in level] for level in levels]


def _subtasks_by_story(shape: dict[str, Any]) -> dict[str, list[str]]:
    return {shape["stories"][key]: shape["subtasks"][key] for key in shape["stories"]}


@dataclass
class GatedDriver:
    """An awaitable stand-in for `cli.drive_subtask_async`, for the supervisor tests.

    `gates[card]` is awaited first with the lane's `stop`; tests put
    `asyncio.Barrier`s and `asyncio.Event`s there, never sleeps. Then
    `outcomes[card]` decides: an exception instance is raised, a `(phase,
    detail)` tuple escalates, and `"done"` finishes as a phase already running
    would. With no entry the fake reaches its simulated phase boundary: if the
    stop has fired it parks as the engine does, with `"stopped before
    implement"`; otherwise it is done. Everything runs on the run's one loop,
    so no lock: `high_water` is the most calls ever in flight at once, and
    `returned[card]` is set when that card's call ends.
    """

    outcomes: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    returned: dict[str, asyncio.Event] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    high_water: int = 0
    in_flight: int = 0

    async def __call__(
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
        stop=None,
    ) -> cli.SubtaskDrive:
        self.calls.append({"card": card.id, "parent": parent.id, "base": subtask.base_branch})
        self.in_flight += 1
        self.high_water = max(self.high_water, self.in_flight)
        try:
            gate = self.gates.get(card.id)
            if gate is not None:
                await gate(stop)
            outcome = self.outcomes.get(card.id)
            if isinstance(outcome, BaseException):
                raise outcome
            warnings = list(self.warnings.get(card.id, []))
            if isinstance(outcome, tuple):
                phase, detail = outcome
                summary = SubtaskSummary(
                    status="escalated", failed_phase=phase, detail=detail
                )
            elif outcome != "done" and stop is not None and stop.triggered:
                summary = SubtaskSummary(
                    status="stopped", detail="stopped before implement"
                )
            else:
                summary = SubtaskSummary(status="done")
            return cli.SubtaskDrive(summary=summary, warnings=warnings)
        finally:
            self.in_flight -= 1
            if card.id in self.returned:
                self.returned[card.id].set()


@requires_git
@requires_brd
def test_subtasks_run_in_order_each_stacked_on_the_one_before(project, integrate_recorder):
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
        "integrated": _integrated(root, [story_a, story_b]),
    }
    (integrate_call,) = integrate_recorder.calls
    assert isinstance(integrate_call.pop("store"), store_module.Store)
    assert integrate_call == {
        "stories": [story_a, story_b],
        "repo_dir": root,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "commands": ["uv run pytest"],
        "allow_no_verification": True,
        "run_id": run_id,
        "runner_factory": factory,
        "run_status": "started",
    }

    run = _load(project, run_id)
    assert run.workflow == "milestone"
    assert run.config == models.RunConfig(max_concurrent_stories=1)
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
def test_no_driver_resolves_to_cli_drive_subtask_async_at_call_time(project, monkeypatch):
    """The default driver is the awaitable one (T3), read off `cli` when the run
    starts, never bound at import. The lane hands it the run's StopSignal and
    no other stop: `FakeDriver`'s keywords are closed, so any other stop
    keyword would be a `TypeError` and the run would not finish `done`."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    fake = FakeDriver()
    monkeypatch.setattr(cli, "drive_subtask_async", fake)

    result = _run(project, shape["milestone"], None)

    assert [call["card"] for call in fake.calls] == [a1]
    assert isinstance(fake.calls[0]["stop"], StopSignal)
    assert result["done"] is True


def test_the_driver_protocol_mirrors_drive_subtask_async():
    """`Driver` is `cli.drive_subtask_async`'s keyword signature, so the one
    stop either takes is the `StopSignal`."""
    protocol = list(inspect.signature(orchestrate.Driver.__call__).parameters)

    assert protocol[0] == "self"
    assert protocol[1:] == list(inspect.signature(cli.drive_subtask_async).parameters)


@requires_git
@requires_brd
def test_no_runner_factory_gives_integrate_cli_default_runner_factory_at_call_time(
    project, monkeypatch, integrate_recorder
):
    """Integrate needs a factory for a conflict. `None` is production's, read
    off `cli` when Integrate runs, while the lanes still get `None` and resolve
    it in `drive_subtask` as before."""
    shape = _milestone(project, {"A": 1})

    def sentinel_factory(**kwargs: Any) -> Any:
        pytest.fail("the sentinel factory is only compared, never called")

    monkeypatch.setattr(cli, "default_runner_factory", sentinel_factory)
    driver = FakeDriver()

    _run(project, shape["milestone"], driver)

    assert [call["runner_factory"] for call in driver.calls] == [None]
    (integrate_call,) = integrate_recorder.calls
    assert integrate_call["runner_factory"] is sentinel_factory


def _imported_modules(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_engine_selecting_modules_never_import_pygents():
    """Pygents-engine RULE 1: these three reach pygents through
    `agent_manager.runtime.engine` only. A guard: it passes before this card
    and must keep passing after it."""
    for module in (cli, orchestrate, integration):
        imported = _imported_modules(module)
        assert not any(n == "pygents" or n.startswith("pygents.") for n in imported), (
            module.__name__
        )


@requires_git
@requires_brd
def test_an_integrate_escalation_is_recorded_and_reported_with_its_story_and_files(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    integrate_recorder.outcome = integration.IntegrateEscalation(
        story=story_b, files=["shared.txt"], detail="the resolver did not finish"
    )
    driver = FakeDriver(warnings={a1: ["a1 warned"]})

    result = _run(project, shape["milestone"], driver)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "phase": "integrate",
        "story": story_b,
        "files": ["shared.txt"],
        "detail": "the resolver did not finish",
        "run_id": run_id,
        "warnings": ["a1 warned"],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "done",
        b1: "done",
    }
    assert [call["run_status"] for call in integrate_recorder.calls] == ["started"]


@requires_git
@requires_brd
def test_an_integrate_that_raises_propagates_and_the_run_is_never_recorded_done(
    project, integrate_recorder
):
    """Error path: a git failure that is not a conflict is not reclassified."""
    shape = _milestone(project, {"A": 1})
    integrate_recorder.outcome = worktree.GitError(
        "fatal: not a valid object name", argv=["git", "merge"], exit_code=128
    )

    with pytest.raises(worktree.GitError):
        _run(project, shape["milestone"], FakeDriver())

    run = _load(project, cli.mint_run_id(shape["milestone"], STARTED_AT))
    assert run.status == "started"


@requires_git
@requires_brd
def test_a_clean_milestone_is_integrated_before_the_run_is_recorded_done(
    project, real_integrate
):
    """Spec test 1."""
    shape = _milestone(project, {"A": 1, "B": 1})
    root = cli.resolve_repo_dir(project)
    order = _census_stories(project, shape["milestone"])
    by_story = _subtasks_by_story(shape)
    main_before = _sha(project, "main")

    result = _run(
        project,
        shape["milestone"],
        BranchingDriver(),
        commands=[PASS_CMD],
        runner_factory=_no_resolver,
    )

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": order}],
        "completed": [card for story in order for card in by_story[story]],
        "tips": [
            {"story": story, "tip": _branch(project, by_story[story][-1])} for story in order
        ],
        "warnings": [],
        "integrated": _integrated(root, order),
    }
    assert _load(project, run_id).status == "done"
    for story in order:
        assert _is_ancestor(project, _branch(project, by_story[story][-1]), INTEGRATION_BRANCH)
    assert cli.worktree_for(root, INTEGRATION_BRANCH).is_dir()
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_an_integrate_escalation_records_the_run_escalated_and_leaves_the_branch(
    project, real_integrate
):
    """Spec test 2: the final verification fails."""
    shape = _milestone(project, {"A": 1, "B": 1})
    root = cli.resolve_repo_dir(project)
    order = _census_stories(project, shape["milestone"])
    by_story = _subtasks_by_story(shape)
    integrate_worktree = cli.worktree_for(root, INTEGRATION_BRANCH)
    main_before = _sha(project, "main")

    result = _run(
        project,
        shape["milestone"],
        BranchingDriver(),
        commands=[FAIL_CMD],
        runner_factory=_no_resolver,
    )

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert set(result) == {
        "escalated", "phase", "story", "files", "detail", "run_id", "warnings"
    }
    assert result["escalated"] is True
    assert result["phase"] == "integrate"
    assert result["story"] is None
    assert result["files"] == []
    assert result["detail"].startswith(
        f"the integrated branch failed its final verification in {integrate_worktree}"
    )
    assert result["run_id"] == run_id
    assert result["warnings"] == []
    expected = {"run": "escalated"}
    for story in order:
        expected[story] = "done"
        for card in by_story[story]:
            expected[card] = "done"
    assert _statuses(_load(project, run_id)) == expected
    # Left exactly as Integrate left it: both tips merged, a clean worktree.
    assert integrate_worktree.is_dir()
    assert _git(integrate_worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == (
        INTEGRATION_BRANCH
    )
    assert _git(integrate_worktree, "status", "--porcelain") == ""
    for story in order:
        assert _is_ancestor(project, _branch(project, by_story[story][-1]), INTEGRATION_BRANCH)
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_an_all_done_milestone_runs_no_lane_and_still_integrates(project, real_integrate):
    """Spec test 4."""
    shape = _milestone(project, {"A": 1, "B": 1})
    root = cli.resolve_repo_dir(project)
    order = _census_stories(project, shape["milestone"])
    by_story = _subtasks_by_story(shape)
    for story in order:
        (card,) = by_story[story]
        _commit_branch(root, _branch(project, card), "main", card)
        rollup.set_status(card, "done", repo_dir=project)
    main_before = _sha(project, "main")
    assert INTEGRATION_BRANCH not in _local_branches(project)
    driver = FakeDriver()

    result = _run(
        project, shape["milestone"], driver, commands=[PASS_CMD], runner_factory=_no_resolver
    )

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert driver.calls == []
    assert result == {
        "done": True,
        "run_id": run_id,
        "levels": [],
        "completed": [],
        "tips": [
            {"story": story, "tip": _branch(project, by_story[story][-1])} for story in order
        ],
        "warnings": [],
        "integrated": _integrated(root, order),
    }
    assert _statuses(_load(project, run_id)) == {"run": "done"}
    for story in order:
        assert _is_ancestor(project, _branch(project, by_story[story][-1]), INTEGRATION_BRANCH)
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_a_relaunch_after_an_integrate_escalation_retries_integrate(project, real_integrate):
    """Spec test 5: the cause is fixed (the suite made green) and the same
    milestone is relaunched with nothing left to drive."""
    shape = _milestone(project, {"A": 1, "B": 1})
    root = cli.resolve_repo_dir(project)
    order = _census_stories(project, shape["milestone"])
    by_story = _subtasks_by_story(shape)
    main_before = _sha(project, "main")

    first = _run(
        project,
        shape["milestone"],
        BranchingDriver(),
        commands=[FAIL_CMD],
        runner_factory=_no_resolver,
    )
    assert first["escalated"] is True
    assert first["phase"] == "integrate"

    driver = FakeDriver()
    second = _run(
        project,
        shape["milestone"],
        driver,
        commands=[PASS_CMD],
        runner_factory=_no_resolver,
        clock=lambda: LATER,
    )

    second_id = cli.mint_run_id(shape["milestone"], LATER)
    assert driver.calls == []
    assert second == {
        "done": True,
        "run_id": second_id,
        "levels": [],
        "completed": [],
        "tips": [
            {"story": story, "tip": _branch(project, by_story[story][-1])} for story in order
        ],
        "warnings": [],
        "integrated": _integrated(root, order),
    }
    assert _load(project, first["run_id"]).status == "escalated"
    assert _load(project, second_id).status == "done"
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_relaunching_a_finished_integrated_milestone_leaves_the_integration_tip(
    project, real_integrate
):
    """Spec test 6."""
    shape = _milestone(project, {"A": 1, "B": 1})
    main_before = _sha(project, "main")
    first = _run(
        project,
        shape["milestone"],
        BranchingDriver(),
        commands=[PASS_CMD],
        runner_factory=_no_resolver,
    )
    assert first["done"] is True
    tip_after_first = _sha(project, INTEGRATION_BRANCH)
    driver = FakeDriver()

    second = _run(
        project,
        shape["milestone"],
        driver,
        commands=[PASS_CMD],
        runner_factory=_no_resolver,
        clock=lambda: LATER,
    )

    assert driver.calls == []
    assert second["done"] is True
    assert second["integrated"] == first["integrated"]
    assert _sha(project, INTEGRATION_BRANCH) == tip_after_first
    assert _sha(project, "main") == main_before


@requires_git
@requires_brd
def test_a_milestone_with_nothing_pending_still_records_a_done_run(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    for card in (a1, story_a):
        board.set_status(card, "done", repo_dir=project)
    driver = FakeDriver()
    root = cli.resolve_repo_dir(project)

    result = _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert result == {
        "done": True,
        "run_id": cli.mint_run_id(shape["milestone"], STARTED_AT),
        "levels": [],
        "completed": [],
        "tips": [{"story": story_a, "tip": _branch(project, a1)}],
        "warnings": [],
        "integrated": _integrated(root, [story_a]),
    }
    # Nothing left to run still integrates, so a relaunch can retry it.
    assert [call["stories"] for call in integrate_recorder.calls] == [[story_a]]
    run = _load(project, result["run_id"])
    assert _statuses(run) == {"run": "done"}


@requires_git
@requires_brd
def test_an_escalation_stops_the_run_before_the_next_story(project, integrate_recorder):
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
    # Spec test 3: a lane escalation never reaches Integrate.
    assert integrate_recorder.calls == []
    assert INTEGRATION_BRANCH not in _local_branches(project)


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
def test_a_fresh_run_records_its_milestones_full_id(project):
    shape = _milestone(project, {"A": 1})

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert _load(project, result["run_id"]).milestone_id == shape["milestone"]


@requires_git
@requires_brd
def test_every_story_of_a_level_runs_at_once_and_each_keeps_its_subtask_order(project):
    shape = _milestone(project, {"A": 2, "B": 1, "C": 1})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    all_three = asyncio.Barrier(3)
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
def test_at_most_max_concurrent_lanes_run(project):
    """Five ready stories, two slots. Every lane stays in flight until a third
    lane enters the driver or the window expires: without the bound all five
    arrive inside the window together; under it only two can be in flight."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1, "D": 1, "E": 1})
    subtasks = _subtasks_by_story(shape)
    arrivals = 0
    third_arrived = asyncio.Event()

    async def hold_until_a_third_lane_arrives(stop: StopSignal | None) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals >= 3:
            third_arrived.set()
        try:
            await asyncio.wait_for(third_arrived.wait(), OVERSHOOT_WINDOW)
        except TimeoutError:
            pass

    driver = GatedDriver(
        gates={cards[0]: hold_until_a_third_lane_arrives for cards in subtasks.values()}
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    assert sorted(call["card"] for call in driver.calls) == sorted(
        card for cards in subtasks.values() for card in cards
    )
    assert driver.high_water == 2


@requires_git
@requires_brd
def test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending(
    project, integrate_recorder
):
    """C is blocked by A. A escalates, so grafo never releases C: C stays
    `pending` because its own blocker failed (dataflow), not because of a level."""
    shape = _milestone(project, {"A": 1, "B": 2, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = asyncio.Barrier(2)
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
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next(project):
    """The lane checks the stop before every subtask (spec, Observable
    behavior): b1 finished after A escalated, so b2 is never handed to the
    driver. It is reported stopped with no phase, and its row stays pending."""
    shape = _milestone(project, {"A": 1, "B": 2})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("verify", "suite red"), b1: "done"},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert sorted(call["card"] for call in driver.calls) == sorted([a1, b1])
    assert (result["story"], result["subtask"]) == (story_a, a1)
    assert result["stopped"] == [{"story": story_b, "subtask": b2, "before_phase": None}]
    assert result["completed"] == [b1]
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "done",
        b2: "pending",
    }


@requires_git
@requires_brd
def test_two_escalations_in_one_tick_give_one_primary(project):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = asyncio.Barrier(2)
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
    pair = asyncio.Barrier(2)
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
def test_stop_while_waiting_for_a_slot_ends_stopped(project):
    """Three ready stories, two slots. `queued` has been started by the tree and
    waits for a slot when `first` escalates: it takes the slot, sees the stop,
    and ends `stopped` without its subtask ever reaching the driver."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    (first, second, queued) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,), (q1,) = subtasks[first], subtasks[second], subtasks[queued]
    pair = asyncio.Barrier(2)
    driver = GatedDriver(
        outcomes={f1: ("review", "reviewer found a blocker")},
        gates={f1: _meet(pair), s1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert q1 not in [call["card"] for call in driver.calls]
    assert (result["story"], result["subtask"]) == (first, f1)
    assert result["stopped"] == [
        {"story": second, "subtask": s1, "before_phase": "implement"},
        {"story": queued, "subtask": q1, "before_phase": None},
    ]
    assert "also_escalated" not in result
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        first: "escalated",
        f1: "escalated",
        second: "stopped",
        s1: "stopped",
        queued: "stopped",
        q1: "pending",
    }


@requires_git
@requires_brd
def test_a_keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates(project):
    """A BaseException is not an escalation (§7): it leaves the loop, and
    `asyncio.run` cancels the other lane where it stands. The rows stay as they
    were, for `am resume`."""
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
    cancelled: list[str] = []

    async def meet_then_wait_to_be_cancelled(stop: StopSignal | None) -> None:
        # The whole body is guarded, not just the inner wait: asyncio.run's
        # cleanup cancels this coroutine wherever it is currently suspended
        # (before or after the barrier releases), a race with no bearing on
        # what this test proves -- that the sibling lane is cancelled, not
        # left hanging or recorded escalated.
        try:
            await _within(pair.wait(), "a1 and b1 in flight together")
            await _within(asyncio.Event().wait(), "the lane to be cancelled")
        except asyncio.CancelledError:
            cancelled.append(b1)
            raise

    driver = GatedDriver(
        outcomes={a1: KeyboardInterrupt()},
        gates={a1: _meet(pair), b1: meet_then_wait_to_be_cancelled},
    )

    # A known level that is not CRITICAL, so a silence left behind is visible.
    grafo_logger = logging.getLogger(orchestrate.GRAFO_LOGGER)
    level_before = grafo_logger.level
    grafo_logger.setLevel(logging.WARNING)
    try:
        with pytest.raises(KeyboardInterrupt):
            _run(project, shape["milestone"], driver, max_concurrent=2)
        # `supervise` silences grafo for its duration only, even on this exit.
        level_after = grafo_logger.level
    finally:
        grafo_logger.setLevel(level_before)

    assert level_after == logging.WARNING
    assert cancelled == [b1]
    run = _load(project, cli.mint_run_id(shape["milestone"], STARTED_AT))
    assert _statuses(run) == {
        "run": "started",
        story_a: "started",
        a1: "started",
        story_b: "started",
        b1: "started",
    }


class _LaneKilled(BaseException):
    """A process death inside a lane, as the e2e resume test injects it (card
    949d51a0). Not `KeyboardInterrupt`: asyncio re-raises that out of the loop
    by itself, but stores any other `BaseException` on the task, where grafo's
    `gather(..., return_exceptions=True)` would drop it."""


def _run_or_fail_if_it_hangs(call: Callable[[], Any]) -> Any:
    """`call()` on a daemon thread: its result or its exception, `BaseException`
    included, or a failure after a bounded wait instead of hanging the suite.

    Without the fix, grafo's `gather()` never returns once a lane dies of a plain
    `BaseException` (card 949d51a0), so an unbounded call would hang, not fail.
    """
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = call()
        except BaseException as error:  # re-raised on the test thread
            outcome["error"] = error

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(WAIT * 3)
    if worker.is_alive():
        pytest.fail("the run hung instead of leaving on the lane's BaseException")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


@requires_git
@requires_brd
def test_a_plain_base_exception_in_one_lane_cancels_the_other_and_propagates(
    project, integrate_recorder
):
    """§7 for a BaseException asyncio does not re-raise by itself: it still
    leaves the run, the other lane is cancelled where it stands, Integrate
    never runs and the rows stay as they were, for `am resume`."""
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
    cancelled: list[str] = []

    async def meet_then_wait_to_be_cancelled(stop: StopSignal | None) -> None:
        try:
            await _within(pair.wait(), "a1 and b1 in flight together")
            await _within(asyncio.Event().wait(), "the lane to be cancelled")
        except asyncio.CancelledError:
            cancelled.append(b1)
            raise

    driver = GatedDriver(
        outcomes={a1: _LaneKilled("the manager died while a1 ran")},
        gates={a1: _meet(pair), b1: meet_then_wait_to_be_cancelled},
    )

    with pytest.raises(_LaneKilled):
        _run_or_fail_if_it_hangs(
            lambda: _run(project, shape["milestone"], driver, max_concurrent=2)
        )

    assert cancelled == [b1]
    assert integrate_recorder.calls == []
    run = _load(project, cli.mint_run_id(shape["milestone"], STARTED_AT))
    assert _statuses(run) == {
        "run": "started",
        story_a: "started",
        a1: "started",
        story_b: "started",
        b1: "started",
    }


def test_cancelling_run_until_killed_cancels_the_work_it_awaits():
    """Cancelling the caller reaches the executor, as a plain `await` would:
    `run_until_killed` never leaves the tree running behind it."""

    async def scenario() -> tuple[bool, bool]:
        started = asyncio.Event()
        work_cancelled = asyncio.Event()

        async def work() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                work_cancelled.set()
                raise

        caller = asyncio.ensure_future(
            orchestrate.run_until_killed(work(), asyncio.Event(), [])
        )
        await _within(started.wait(), "the work to start")
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        return caller.cancelled(), work_cancelled.is_set()

    assert asyncio.run(scenario()) == (True, True)


@requires_git
@requires_brd
def test_warnings_and_completed_follow_census_order_not_finish_order(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    (first, second) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,) = subtasks[first], subtasks[second]
    second_returned = asyncio.Event()

    async def after_second_returns(stop: StopSignal | None) -> None:
        await _within(second_returned.wait(), "the second lane's call to end")

    driver = GatedDriver(
        warnings={f1: ["first warned"], s1: ["second warned"]},
        gates={f1: after_second_returns},
        returned={s1: second_returned},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert sorted(call["card"] for call in driver.calls) == sorted([f1, s1])
    assert result["done"] is True, result
    assert result["completed"] == [f1, s1]
    assert result["warnings"] == ["first warned", "second warned"]


# ── the supervisor tree (supervisor-tree T1-T6) ─────────────────────────────


@requires_git
@requires_brd
def test_a_story_starts_when_its_blocker_finishes_not_its_level(project):
    """T1: C (blocked by A) starts the moment A is done, while B -- in A's wave
    -- is still in flight. b1 cannot finish until c1 has started, so under a
    level barrier b1 would time out and the run would escalate instead."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A"]})
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    c_started = asyncio.Event()

    async def hold_until_c_starts(stop: StopSignal | None) -> None:
        await _within(c_started.wait(), "c1 to start while b1 is in flight")

    async def mark_c_started(stop: StopSignal | None) -> None:
        c_started.set()

    driver = GatedDriver(gates={b1: hold_until_c_starts, c1: mark_c_started})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    cards = [call["card"] for call in driver.calls]
    assert cards.index(a1) < cards.index(c1)
    assert next(call for call in driver.calls if call["card"] == c1)["base"] == _branch(
        project, a1
    )
    assert [level["stories"] for level in result["levels"]] == _census_levels(
        project, shape["milestone"]
    )


@requires_git
@requires_brd
def test_a_chain_finishes_with_one_slot(project):
    """T4: a lane takes its slot only after its blockers finished, so a chain
    never holds a slot while it waits and cannot deadlock."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1}, blocked_by={"B": ["A"], "C": ["B"]}
    )
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    driver = GatedDriver()

    result = _run(project, shape["milestone"], driver, max_concurrent=1)

    assert result["done"] is True, result
    assert [call["card"] for call in driver.calls] == [a1, b1, c1]
    assert [call["base"] for call in driver.calls] == [
        "main",
        _branch(project, a1),
        _branch(project, b1),
    ]
    assert result["completed"] == [a1, b1, c1]


@requires_git
@requires_brd
def test_a_story_behind_a_subtask_less_story_waits_for_the_blocker_beneath(project):
    """Review Focus 2: J has no subtasks, so C's stack roots on A's tip through
    it. `dag.compute_levels` puts C in wave 0 beside A, but C's node waits on
    J's, which waits on A's: c1 never starts before a1 has returned."""
    shape = _milestone(project, {"A": 1, "J": 0, "C": 1}, blocked_by={"J": ["A"], "C": ["J"]})
    (a1,) = shape["subtasks"]["A"]
    (c1,) = shape["subtasks"]["C"]
    a1_returned = asyncio.Event()

    async def a1_must_have_returned(stop: StopSignal | None) -> None:
        assert a1_returned.is_set(), "c1 started before a1, the tip it stacks on, returned"

    driver = GatedDriver(gates={c1: a1_must_have_returned}, returned={a1: a1_returned})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    assert [call["card"] for call in driver.calls] == [a1, c1]
    assert driver.calls[1]["base"] == _branch(project, a1)


@requires_git
@requires_brd
def test_a_milestone_with_no_stories_finishes_without_a_tree(project, integrate_recorder):
    """Review Focus 1: no story means no root node, and grafo's executor cannot
    run an empty tree; the run is still a clean `done` that integrates."""
    milestone = _add_card(project, "Milestone 3: nothing in it yet")
    driver = GatedDriver()

    result = _run(project, milestone, driver, max_concurrent=2)

    assert driver.calls == []
    assert result["done"] is True, result
    assert result["levels"] == []
    assert result["completed"] == []
    assert [call["stories"] for call in integrate_recorder.calls] == [[]]


@requires_git
@requires_brd
def test_a_lane_bug_becomes_escalated_with_type_and_message(project):
    """A driver that raises is a lane bug: `escalated` at the subtask it was
    driving, with `"<Type>: <msg>"`, never a crash."""
    shape = _milestone(project, {"A": 2})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    driver = GatedDriver(outcomes={a2: ValueError("lane bug")})

    result = _run(project, shape["milestone"], driver)

    assert result["escalated"] is True, result
    assert (result["story"], result["subtask"], result["failed_phase"], result["detail"]) == (
        story_a,
        a2,
        None,
        "ValueError: lane bug",
    )
    assert result["warnings"] == []
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "done",
        a2: "escalated",
    }


@requires_git
@requires_brd
def test_every_node_has_no_timeout(project, monkeypatch):
    """Review Focus 1: grafo's default node timeout (60 s) would cancel a lane
    mid-phase, so every node -- done stories' included -- is built with
    `timeout=None`."""
    built: list[grafo.Node] = []

    class RecordingNode(grafo.Node):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(grafo, "Node", RecordingNode)
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 1}, blocked_by={"C": ["A"]}
    )
    (d1,) = shape["subtasks"]["D"]
    for card in (d1, shape["stories"]["D"]):
        board.set_status(card, "done", repo_dir=project)

    result = _run(project, shape["milestone"], GatedDriver(), max_concurrent=2)

    assert result["done"] is True, result
    assert sorted(node.uuid for node in built) == sorted(shape["stories"].values())
    assert [node._timeout for node in built] == [None] * len(built)


@requires_git
@requires_brd
def test_each_blocker_forwards_its_tip_to_its_dependent(project, monkeypatch):
    """T1: one edge per in-milestone blocker, forwarding the blocker's tip as
    `tip_<short id>` (Task 3.2 reads it for merged bases). A done blocker's
    node forwards its existing tip, a pending one the tip its lane finished."""
    received: dict[str, dict[str, Any]] = {}

    class RecordingNode(grafo.Node):
        def __init__(self, *args: Any, coroutine: Any, uuid: str, **kwargs: Any) -> None:
            async def recorded(**forwarded: Any) -> Any:
                received[uuid] = forwarded
                return await coroutine(**forwarded)

            super().__init__(*args, coroutine=recorded, uuid=uuid, **kwargs)

    monkeypatch.setattr(grafo, "Node", RecordingNode)
    shape = _milestone(
        project, {"A": 1, "C": 1, "D": 1, "E": 1}, blocked_by={"C": ["A"], "E": ["D"]}
    )
    stories = shape["stories"]
    (a1,) = shape["subtasks"]["A"]
    (d1,) = shape["subtasks"]["D"]
    for card in (d1, stories["D"]):
        board.set_status(card, "done", repo_dir=project)

    result = _run(project, shape["milestone"], GatedDriver(), max_concurrent=2)

    assert result["done"] is True, result
    assert received == {
        stories["A"]: {},
        stories["D"]: {},
        stories["C"]: {f"tip_{dag.short_id(stories['A'])}": _branch(project, a1)},
        stories["E"]: {f"tip_{dag.short_id(stories['D'])}": _branch(project, d1)},
    }


def _patch_default_node_timeout(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """Make `grafo.Node`'s default `timeout` `seconds` for this test."""
    init = grafo.Node.__init__
    params = list(inspect.signature(init).parameters)
    defaults = list(init.__defaults__)
    defaults[params.index("timeout") - (len(params) - len(defaults))] = seconds
    monkeypatch.setattr(init, "__defaults__", tuple(defaults))


async def _noop() -> None:
    return None


@requires_git
@requires_brd
def test_a_lane_outlives_the_default_node_timeout(project, monkeypatch):
    """Review Focus 1: with grafo's default patched to 0.05 s, a lane still in
    flight well past it is not cancelled and the story ends `done`."""
    _patch_default_node_timeout(monkeypatch, PATCHED_NODE_TIMEOUT)
    assert grafo.Node(coroutine=_noop)._timeout == PATCHED_NODE_TIMEOUT  # the patch bites
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]

    async def outlast_the_default(stop: StopSignal | None) -> None:
        released = asyncio.Event()
        asyncio.get_running_loop().call_later(LONGER_THAN_PATCHED_TIMEOUT, released.set)
        await _within(released.wait(), "the release timer")

    driver = GatedDriver(gates={a1: outlast_the_default})

    result = _run(project, shape["milestone"], driver)

    assert result["done"] is True, result
    assert result["completed"] == [a1]


@requires_git
@requires_brd
def test_report_keeps_levels_as_waves(project):
    """Levels stop being barriers but stay in the report: `dag.compute_levels`."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 1}, blocked_by={"C": ["A"], "D": ["C"]}
    )
    plan = census.flatten_milestone(board.tree(shape["milestone"], repo_dir=project))
    waves = dag.compute_levels(plan.stories)

    result = _run(project, shape["milestone"], GatedDriver(), max_concurrent=2)

    assert result["done"] is True, result
    assert result["levels"] == [
        {"level": index, "stories": [story.id for story in wave]}
        for index, wave in enumerate(waves)
    ]
    assert len(result["levels"]) == 3


def test_only_orchestrate_imports_grafo():
    """T10: grafo is a runtime dependency of exactly one module."""
    package = Path(orchestrate.__file__).parent
    importers: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(name == "grafo" or name.startswith("grafo.") for name in names):
                importers.add(path.relative_to(package).as_posix())
    assert importers == {"orchestrate.py"}


@requires_git
@requires_brd
def test_a_lane_bug_keeps_stdout_one_json_line(project, monkeypatch, capsys):
    """Review Focus 2, through the CLI: grafo logs a failing node with a
    traceback. A handler on stdout is attached to grafo's logger for this
    test, so any grafo record would land there; stdout must still be exactly
    one JSON line, and grafo's own level is back once the run ends."""
    shape = _milestone(project, {"A": 1})

    async def buggy(**kwargs: Any) -> cli.SubtaskDrive:
        raise ValueError("lane bug")

    monkeypatch.setattr(cli, "drive_subtask_async", buggy)
    grafo_logger = logging.getLogger("grafo")
    level_before = grafo_logger.level
    loud = logging.StreamHandler(sys.stdout)  # capsys's stdout, captured here
    grafo_logger.addHandler(loud)
    try:
        with pytest.raises(SystemExit) as exited:
            cli.app(
                [
                    "run",
                    "--milestone",
                    shape["milestone"],
                    "--repo-dir",
                    str(project),
                    "--base-branch",
                    "main",
                    "--branch-prefix",
                    PREFIX,
                    "--max-concurrent",
                    "1",
                ],
                prog_name="am",
            )
    finally:
        grafo_logger.removeHandler(loud)

    assert exited.value.code == cli.EXIT_ESCALATED
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) == 1, out
    envelope = json.loads(lines[0])
    assert envelope["ok"] is True
    assert envelope["data"]["escalated"] is True
    assert envelope["data"]["detail"] == "ValueError: lane bug"
    assert "Traceback" not in out
    assert grafo_logger.level == level_before


# ── real M6 subtask agents under the tree ───────────────────────────────────


@pytest.fixture
def fresh_pygents():
    """Fresh pygents registries and compile cache, as `tests/runtime/conftest.py`
    gives every runtime test: this module's agents must not collide with others."""
    from pygents import AgentRegistry, ToolRegistry

    from agent_manager.runtime import compile as compile_mod

    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()
    yield
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()


class _ThreadWatch:
    """A `pause()`-only stand-in on the run's StopSignal whose flag a step, in
    its `to_thread` worker, can wait on."""

    def __init__(self) -> None:
        self.paused = threading.Event()

    def pause(self) -> None:
        self.paused.set()


@requires_git
@requires_brd
def test_an_escalation_parks_running_lanes_and_blocks_new_ones(project, fresh_pygents):
    """Spec test 4, on real M6 pygents subtask agents over step-only workflows
    (the fake runner): A's step fails once B's first phase is in flight; the
    lane triggers the stop; B's agent is paused, finishes its phase and parks
    before `b_second` through ON_PAUSE; C, blocked by A, is never started."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    b_in = threading.Event()
    watch = _ThreadWatch()
    ran: list[str] = []

    def a_work(card: str) -> dict[str, Any]:
        ran.append("a_work")
        if not b_in.wait(WAIT):
            raise RuntimeError("B's first phase never started")
        raise RuntimeError("a failed on purpose")

    def b_first(card: str) -> dict[str, Any]:
        ran.append("b_first")
        b_in.set()
        if not watch.paused.wait(WAIT):
            raise RuntimeError("the stop was never triggered")
        return {"b_first": 1}

    def b_second(card: str) -> dict[str, Any]:
        ran.append("b_second")
        return {"b_second": 2}

    def c_work(card: str) -> dict[str, Any]:
        ran.append("c_work")
        return {"c_work": 3}

    workflows = {
        a1: Workflow("m7_supervise_a_escalates", (Step("a_work", a_work),)),
        b1: Workflow("m7_supervise_b_parks", (Step("b_first", b_first), Step("b_second", b_second))),
        c1: Workflow("m7_supervise_c_never", (Step("c_work", c_work),)),
    }
    called: list[str] = []

    async def drive(*, store, run_id, card, parent, subtask, repo_dir, stop=None, **_: Any):
        called.append(card.id)
        if card.id == b1:
            stop.register(watch)
        try:
            summary = await runtime_engine.run_subtask_async(
                workflows[card.id],
                store,
                story_id=parent.id,
                subtask=subtask,
                repo_dir=repo_dir,
                stop=stop,
            )
        finally:
            if card.id == b1:
                stop.unregister(watch)
        return cli.SubtaskDrive(summary=summary, warnings=list(summary.warnings))

    result = _run(project, shape["milestone"], drive, max_concurrent=2)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert sorted(called) == sorted([a1, b1])
    assert sorted(ran) == ["a_work", "b_first"]
    assert result["escalated"] is True, result
    assert (result["story"], result["subtask"]) == (story_a, a1)
    assert "a failed on purpose" in result["detail"]
    assert "also_escalated" not in result
    assert result["stopped"] == [{"story": story_b, "subtask": b1, "before_phase": "b_second"}]
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
        story_c: "pending",
        c1: "pending",
    }
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        newest = opened.latest_checkpoint(b1)
    finally:
        opened.close()
    assert newest.reason == "parked"
    assert newest.agent["queue"][0]["kwargs"]["phase"] == "b_second"


# ── relaunch continues an open checkpoint (card 02890d5d) ───────────────────

_ABSENT = object()
"""What `CheckpointDriver` records when the lane passed no `resume_from` at all."""

EARLIER = datetime(2026, 9, 24, 11, 0, 0, tzinfo=timezone.utc)
"""When the earlier run saved its checkpoints: before `STARTED_AT`."""


@dataclass
class CheckpointDriver(FakeDriver):
    """`FakeDriver` that also takes `resume_from` and records it per card."""

    resumed: dict[str, Any] = field(default_factory=dict)

    async def __call__(self, *, resume_from: Any = _ABSENT, **kwargs: Any) -> cli.SubtaskDrive:
        self.resumed[kwargs["card"].id] = resume_from
        return await super().__call__(**kwargs)


def _plant(
    project: Path,
    run_id: str,
    card_id: str,
    reason: str,
    *,
    digest: str | None = None,
    queue: tuple[str, ...] = ("implement",),
    minute: int = 0,
) -> store_module.Checkpoint:
    """One checkpoint row of `TASK` for `card_id`, saved by an earlier run."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        return opened.save_checkpoint(
            card_id,
            workflow=task_workflow.TASK.name,
            digest=task_workflow.TASK.digest() if digest is None else digest,
            reason=reason,
            agent={
                "current_turn": None,
                "queue": [{"kwargs": {"phase": name, "loop": 0}} for name in queue],
            },
            saved_at=EARLIER.replace(minute=minute),
        )
    finally:
        opened.close()


@requires_git
@requires_brd
def test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh(
    project,
):
    """Spec test 10: a1 parked under this TASK continues; a2's row is from
    another TASK, b1's newest row is `done`, b2's is a phase escalation with no
    turn left: all three start fresh, and the run does not raise."""
    shape = _milestone(project, {"A": 2, "B": 2})
    a1, a2 = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    earlier = cli.mint_run_id(shape["milestone"], EARLIER)
    parked = _plant(project, earlier, a1, "parked")
    _plant(project, earlier, a2, "parked", digest="saved-under-another-task")
    _plant(project, earlier, b1, "parked", minute=1)
    _plant(project, earlier, b1, "done", queue=(), minute=2)
    _plant(project, earlier, b2, "escalated", queue=())
    driver = CheckpointDriver()

    result = _run(project, shape["milestone"], driver)

    assert result["done"] is True
    assert result["completed"] == [a1, a2, b1, b2]
    got = driver.resumed[a1]
    assert got is not _ABSENT
    assert (got.run_id, got.card_id, got.seq, got.reason) == (earlier, a1, parked.seq, "parked")
    assert driver.resumed[a2] is _ABSENT
    assert driver.resumed[b1] is _ABSENT
    assert driver.resumed[b2] is _ABSENT


@requires_git
@requires_brd
def test_a_checkpoint_lookup_that_fails_escalates_that_subtask(project, monkeypatch):
    """Review Focus 4: the lookup runs inside the lane's `try`, so a broken
    store escalates the subtask it was for and never crashes the run."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]

    def broken(store, card_id):
        raise RuntimeError("checkpoints table unreadable")

    monkeypatch.setattr(cli, "continuable_checkpoint", broken)
    driver = CheckpointDriver()

    result = _run(project, shape["milestone"], driver)

    assert result["escalated"] is True
    assert result["subtask"] == a1
    assert result["detail"] == "RuntimeError: checkpoints table unreadable"
    assert driver.calls == []


# ── merged bases (supervisor-tree §5, card 8eca88e2) ────────────────────────


@dataclass
class FakeBases:
    """Stands in for `bases.build`, which the lane reads off `bases` at call time.

    Every call is recorded. `gates[story]` is awaited first with the call's
    `stop` (Events and Barriers, never sleeps). `outcomes[story]` is an
    exception to raise; with none the base counts as built and a
    `BaseResult` naming `root.branch` comes back. It touches no git: a
    `FakeDriver` never needs the branch to exist. `resumed[story]` is the
    `resume_from` it was handed, `_ABSENT` when none was (card 54e4ec29).
    """

    outcomes: dict[str, BaseException] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    resumed: dict[str, Any] = field(default_factory=dict)

    async def __call__(
        self,
        root,
        tips,
        *,
        repo_dir,
        commands,
        allow_no_verification,
        store,
        run_id,
        story_id,
        runner_factory,
        stop,
        resume_from=_ABSENT,
    ) -> bases.BaseResult:
        self.resumed[story_id] = resume_from
        self.calls.append(
            {
                "root": root,
                "tips": list(tips),
                "repo_dir": repo_dir,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "store": store,
                "run_id": run_id,
                "story_id": story_id,
                "runner_factory": runner_factory,
                "stop": stop,
            }
        )
        gate = self.gates.get(story_id)
        if gate is not None:
            await gate(stop)
        error = self.outcomes.get(story_id)
        if error is not None:
            raise error
        return bases.BaseResult(
            branch=root.branch, merged=list(tips[1:]), already_merged=[], resolved=[]
        )


@pytest.fixture
def fake_bases(monkeypatch) -> FakeBases:
    recorder = FakeBases()
    monkeypatch.setattr(bases, "build", recorder)
    return recorder


def _root_plan(project: Path, milestone: str, story_id: str) -> dag.RootPlan:
    """The story's `RootPlan` as the run derives it: census order, `PREFIX`, `main`."""
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    by_id = {story.id: story for story in plan.stories}
    return dag.story_root(by_id[story_id], by_id, PREFIX, "main")


def _bases_entry(story_id: str, root_plan: dag.RootPlan) -> dict[str, Any]:
    return {"story": story_id, "branch": root_plan.branch, "blockers": list(root_plan.blockers)}


@requires_git
@requires_brd
def test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it(
    project, fake_bases
):
    """Spec, Engine tier: C (blocked by A and B) builds its base once, only
    after both blockers returned, from their tips in
    `root_plan.blockers` order; c1 stacks on the base, c2 on c1; the report
    lists the base."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 2}, blocked_by={"C": ["A", "B"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    c1, c2 = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    a_returned, b_returned = asyncio.Event(), asyncio.Event()

    async def both_blockers_returned(stop: StopSignal | None) -> None:
        assert a_returned.is_set() and b_returned.is_set(), (
            "C's base was built before both blockers finished"
        )

    fake_bases.gates[story_c] = both_blockers_returned
    driver = GatedDriver(returned={a1: a_returned, b1: b_returned})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    assert root_plan.kind == "merged"
    assert root_plan.branch == f"{PREFIX}/base-{dag.short_id(story_c)}"
    assert sorted(root_plan.blockers) == sorted([story_a, story_b])
    tips = {story_a: _branch(project, a1), story_b: _branch(project, b1)}
    (call,) = fake_bases.calls
    assert call["root"] == root_plan
    assert call["tips"] == [tips[blocker] for blocker in root_plan.blockers]
    assert call["story_id"] == story_c
    assert call["repo_dir"] == cli.resolve_repo_dir(project)
    assert call["run_id"] == result["run_id"]
    assert call["commands"] == []
    assert call["allow_no_verification"] is False
    assert isinstance(call["stop"], StopSignal)
    # Production passes no factory; the base's resolver gets production's.
    assert call["runner_factory"] is cli.default_runner_factory
    driven_on = {entry["card"]: entry["base"] for entry in driver.calls}
    assert driven_on[c1] == root_plan.branch
    assert driven_on[c2] == _branch(project, c1)
    assert result["bases"] == [_bases_entry(story_c, root_plan)]
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1], statuses[c2]) == ("done", "done", "done")


@requires_git
@requires_brd
def test_a_given_runner_factory_reaches_the_base_builder(project, fake_bases):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})

    result = _run(project, shape["milestone"], FakeDriver(), runner_factory=_no_resolver)

    assert result["done"] is True, result
    (call,) = fake_bases.calls
    assert call["runner_factory"] is _no_resolver


@requires_git
@requires_brd
def test_a_done_blockers_existing_tip_goes_into_the_base(project, fake_bases):
    """Review Focus 3: on a relaunch A is already done. Its lane finishes at once
    with the tip it already has, and that tip is merged in its blocker position."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    for card in (a1, story_a):
        board.set_status(card, "done", repo_dir=project)
    root_plan = _root_plan(project, shape["milestone"], story_c)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert result["done"] is True, result
    assert [call["card"] for call in driver.calls] == [b1, c1]
    tips = {story_a: _branch(project, a1), story_b: _branch(project, b1)}
    (call,) = fake_bases.calls
    assert call["tips"] == [tips[blocker] for blocker in root_plan.blockers]
    assert result["bases"] == [_bases_entry(story_c, root_plan)]


@requires_git
@requires_brd
def test_a_run_with_no_merged_root_builds_no_base_and_reports_no_bases(project, fake_bases):
    """Spec: a lone-blocker story stays the fast path, and the key is absent
    when no base was built."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"B": ["A"]})

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert fake_bases.calls == []
    assert "bases" not in result


@requires_git
@requires_brd
def test_a_lane_that_escalates_after_building_its_base_still_lists_it(project, fake_bases):
    """Review Focus 5: the base exists once built, so a lane-escalated payload
    lists it too."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (c1,) = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    driver = FakeDriver(outcomes={c1: ("verify", "suite red on the base")})

    result = _run(project, shape["milestone"], driver)

    assert result["escalated"] is True, result
    assert (result["story"], result["subtask"], result["failed_phase"]) == (story_c, c1, "verify")
    assert result["bases"] == [_bases_entry(story_c, root_plan)]


@requires_git
@requires_brd
def test_an_integrate_escalation_still_lists_the_bases_built(
    project, fake_bases, integrate_recorder
):
    """Review Focus 5: the integrate-escalated payload carries `bases` too."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    integrate_recorder.outcome = integration.IntegrateEscalation(
        story=story_c, files=["shared.txt"], detail="the resolver did not finish"
    )

    result = _run(project, shape["milestone"], FakeDriver())

    assert (result["escalated"], result["phase"]) == (True, "integrate"), result
    assert result["bases"] == [_bases_entry(story_c, root_plan)]


@requires_git
@requires_brd
def test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling(
    project, fake_bases
):
    """Spec: `BaseFailed(stopped=False)` escalates C at `base` with no
    subtask, triggers the stop, drives no subtask of C, and D -- held in
    flight on an Event until the stop fires -- is recorded `stopped`."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 2}, blocked_by={"C": ["A", "B"]}
    )
    story_a, story_b, story_c, story_d = (shape["stories"][key] for key in "ABCD")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    d1, d2 = shape["subtasks"]["D"]
    d1_in_flight = asyncio.Event()

    async def hold_d1_until_the_stop(stop: StopSignal | None) -> None:
        d1_in_flight.set()
        await _await_stop(stop)

    async def fail_once_d1_is_in_flight(stop: StopSignal | None) -> None:
        await _within(d1_in_flight.wait(), "d1 in flight beside C's base")

    fake_bases.gates[story_c] = fail_once_d1_is_in_flight
    fake_bases.outcomes[story_c] = bases.BaseFailed("conflict nobody could resolve")
    driver = GatedDriver(gates={d1: hold_d1_until_the_stop})

    result = _run(project, shape["milestone"], driver, max_concurrent=3)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 1,
        "story": story_c,
        "subtask": None,
        "failed_phase": "base",
        "detail": "conflict nobody could resolve",
        "warnings": [],
        "stopped": [{"story": story_d, "subtask": d1, "before_phase": "implement"}],
    }
    assert c1 not in [call["card"] for call in driver.calls]
    assert len(fake_bases.calls) == 1
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "done",
        b1: "done",
        story_c: "escalated",
        c1: "pending",
        story_d: "stopped",
        d1: "stopped",
        d2: "pending",
    }


@requires_git
@requires_brd
def test_a_base_whose_resolver_was_stopped_ends_stopped_not_escalated(project, fake_bases):
    """Spec: `BaseFailed(stopped=True)` -- D escalates while C's base is being
    built; C's resolver parks, and C is `stopped` with no subtask."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 1}, blocked_by={"C": ["A", "B"]}
    )
    story_a, story_b, story_c, story_d = (shape["stories"][key] for key in "ABCD")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    (d1,) = shape["subtasks"]["D"]
    c_building = asyncio.Event()

    async def park_with_the_stop(stop: StopSignal | None) -> None:
        c_building.set()
        await _await_stop(stop)

    async def escalate_once_c_builds(stop: StopSignal | None) -> None:
        await _within(c_building.wait(), "C's base to start building")

    fake_bases.gates[story_c] = park_with_the_stop
    fake_bases.outcomes[story_c] = bases.BaseFailed("the resolver was stopped", stopped=True)
    driver = GatedDriver(
        outcomes={d1: ("review", "d broke")}, gates={d1: escalate_once_c_builds}
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=3)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 0,
        "story": story_d,
        "subtask": d1,
        "failed_phase": "review",
        "detail": "d broke",
        "warnings": [],
        "stopped": [{"story": story_c, "subtask": None, "before_phase": None}],
    }
    assert c1 not in [call["card"] for call in driver.calls]
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "done",
        b1: "done",
        story_c: "stopped",
        c1: "pending",
        story_d: "escalated",
        d1: "escalated",
    }


@requires_git
@requires_brd
def test_a_merged_lane_that_finds_the_stop_fired_never_builds_its_base(project, fake_bases):
    """Review Focus 1: one slot. The three roots queue on it in census order;
    `joined` (blocked by the first two) asks for the slot only after the second
    returned, so it queues behind `last`. `last` escalates, and `joined` takes
    the slot with the stop already fired: stopped at j1, nothing built."""
    milestone = _add_card(project, "Milestone 3: orchestration")
    only_subtask: dict[str, str] = {}
    for key in ("P", "Q", "R"):
        story = _add_card(project, f"Story {key}", milestone)
        only_subtask[story] = _add_card(project, f"{key.lower()}1: only subtask of story {key}", story)
    first, second, last = _census_stories(project, milestone)
    joined = _add_card(project, "Story J: blocked by the first two", milestone)
    j1 = _add_card(project, "j1: only subtask of story J", joined)
    _block(project, joined, first)
    _block(project, joined, second)
    driver = GatedDriver(outcomes={only_subtask[last]: ("review", "the last root broke")})

    result = _run(project, milestone, driver, max_concurrent=1)

    assert fake_bases.calls == []
    assert j1 not in [call["card"] for call in driver.calls]
    assert (result["story"], result["subtask"]) == (last, only_subtask[last])
    assert result["stopped"] == [{"story": joined, "subtask": j1, "before_phase": None}]
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[joined], statuses[j1]) == ("stopped", "pending")


@requires_git
@requires_brd
def test_any_other_error_from_the_base_is_a_lane_escalation_with_no_subtask(
    project, fake_bases
):
    """Spec: a non-`BaseFailed` error goes to the catch-all: `"<Type>: <msg>"`,
    no subtask, no failed phase, and no subtask row escalated."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (c1,) = shape["subtasks"]["C"]
    fake_bases.outcomes[story_c] = RuntimeError("git fell over")

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["escalated"] is True, result
    assert (result["level"], result["story"], result["subtask"], result["failed_phase"]) == (
        1,
        story_c,
        None,
        None,
    )
    assert result["detail"] == "RuntimeError: git fell over"
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("escalated", "pending")


@requires_git
@requires_brd
def test_a_failed_blocker_leaves_the_merged_story_pending_and_builds_no_base(
    project, fake_bases
):
    """Spec: C never takes a slot when B escalated, so C is `pending` and
    `bases.build` is never called."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_b, story_c = shape["stories"]["B"], shape["stories"]["C"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    driver = FakeDriver(outcomes={b1: ("review", "b broke")})

    result = _run(project, shape["milestone"], driver)

    assert (result["story"], result["subtask"]) == (story_b, b1), result
    assert fake_bases.calls == []
    assert c1 not in [call["card"] for call in driver.calls]
    assert "bases" not in result
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("pending", "pending")


@requires_git
@requires_brd
def test_a_subtask_less_story_on_two_blockers_builds_its_base_and_its_dependent_stacks_on_it(
    project, fake_bases
):
    """Spec: J has no subtasks and two blockers; D (blocked by J) falls through
    to J's merged base. J's lane builds it before D starts, D's first subtask
    stacks on it, and the report lists it."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_a, story_b, story_j = (shape["stories"][key] for key in "ABJ")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (d1,) = shape["subtasks"]["D"]
    root_plan = _root_plan(project, shape["milestone"], story_j)

    async def the_base_is_built(stop: StopSignal | None) -> None:
        assert [call["story_id"] for call in fake_bases.calls] == [story_j], (
            "d1 started before J's base was built"
        )

    driver = GatedDriver(gates={d1: the_base_is_built})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    assert root_plan.kind == "merged"
    tips = {story_a: _branch(project, a1), story_b: _branch(project, b1)}
    (call,) = fake_bases.calls
    assert call["root"] == root_plan
    assert call["tips"] == [tips[blocker] for blocker in root_plan.blockers]
    assert next(entry for entry in driver.calls if entry["card"] == d1)["base"] == root_plan.branch
    assert result["bases"] == [_bases_entry(story_j, root_plan)]


@requires_git
@requires_brd
def test_a_subtask_less_storys_failed_base_escalates_the_run_and_its_dependent_stays_pending(
    project, fake_bases, integrate_recorder
):
    """Review Focus 2: J is in no wave and has no store row, yet its failed
    base must escalate the run, never reach Integrate."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_a, story_b, story_j, story_d = (shape["stories"][key] for key in "ABJD")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (d1,) = shape["subtasks"]["D"]
    fake_bases.outcomes[story_j] = bases.BaseFailed("J's base broke")
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": None,
        "story": story_j,
        "subtask": None,
        "failed_phase": "base",
        "detail": "J's base broke",
        "warnings": [],
    }
    assert d1 not in [call["card"] for call in driver.calls]
    assert integrate_recorder.calls == []
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "done",
        b1: "done",
        story_d: "pending",
        d1: "pending",
    }


@requires_git
@requires_brd
def test_a_closed_subtask_less_story_builds_no_base(project, fake_bases):
    """Spec, Out of scope: a done story's missing base is milestone-wide
    resume's; this lane only returns its tip."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    board.set_status(shape["stories"]["J"], "done", repo_dir=project)

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert fake_bases.calls == []
    assert "bases" not in result


@requires_git
@requires_brd
def test_a_merged_storys_dependent_stays_pending_when_a_blocker_failed(project, fake_bases):
    """T1: grafo starts no dependent of a lane that did not finish clean. C
    never ran (B escalated), so E -- blocked by C alone -- is never started
    either: `pending`, not `stopped`, and never listed as parked."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "E": 1}, blocked_by={"C": ["A", "B"], "E": ["C"]}
    )
    story_b, story_c, story_e = (shape["stories"][key] for key in "BCE")
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    (e1,) = shape["subtasks"]["E"]
    driver = FakeDriver(outcomes={b1: ("review", "b broke")})

    result = _run(project, shape["milestone"], driver)

    assert (result["story"], result["subtask"]) == (story_b, b1), result
    assert "stopped" not in result, result
    assert fake_bases.calls == []
    assert [call["card"] for call in driver.calls if call["card"] in (c1, e1)] == []
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("pending", "pending")
    assert (statuses[story_e], statuses[e1]) == ("pending", "pending")


@requires_git
@requires_brd
def test_a_subtask_less_merged_storys_dependent_stays_pending_when_a_blocker_failed(
    project, fake_bases
):
    """J (no subtasks) never builds its base when B escalated, and D -- which
    falls through to J's base -- is never started: `pending`, not `stopped`."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_b, story_d = shape["stories"]["B"], shape["stories"]["D"]
    (b1,) = shape["subtasks"]["B"]
    (d1,) = shape["subtasks"]["D"]
    driver = FakeDriver(outcomes={b1: ("review", "b broke")})

    result = _run(project, shape["milestone"], driver, max_concurrent=1)

    assert (result["story"], result["subtask"]) == (story_b, b1), result
    assert "stopped" not in result, result
    assert fake_bases.calls == []
    assert d1 not in [call["card"] for call in driver.calls]
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_d], statuses[d1]) == ("pending", "pending")


@requires_git
@requires_brd
def test_a_done_merged_storys_dependent_waits_for_its_blockers(project, fake_bases):
    """A done story rooted on a merged base still sits behind its blockers,
    as a done lone-blocker story sits behind its blocker's edge: C is done,
    B is not, so E (blocked by C) is driven only after b1 returned, and not
    at all when B escalates."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "E": 1}, blocked_by={"C": ["A", "B"], "E": ["C"]}
    )
    story_c, story_e = shape["stories"]["C"], shape["stories"]["E"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    (e1,) = shape["subtasks"]["E"]
    for card in (c1, story_c):
        board.set_status(card, "done", repo_dir=project)
    driver = FakeDriver(outcomes={b1: ("review", "b broke")})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["escalated"] is True, result
    assert e1 not in [call["card"] for call in driver.calls]
    assert "stopped" not in result, result
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_e], statuses[e1]) == ("pending", "pending")


# ── milestone-wide resume helpers (card 54e4ec29) ───────────────────────────

RESUME_RUN_ID = "20260924T120000Z-00000009"


def _resume_root(tmp_path: Path, monkeypatch) -> Path:
    """A project root with its projection under tmp_path; no git, no board."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    return root.resolve()


def _record_resume_run(
    root: Path, run_id: str = RESUME_RUN_ID, *, workflow: str = "milestone", status: str = "escalated"
) -> None:
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=root,
                base_branch="main",
                branch_prefix=PREFIX,
                status=status,
                config=models.RunConfig(max_concurrent_stories=3),
            )
        )
    finally:
        opened.close()


def _save(
    store: store_module.Store,
    card_id: str,
    reason: str,
    *,
    phase: str | None = None,
    workflow: Workflow = task_workflow.TASK,
    digest: str | None = None,
) -> store_module.Checkpoint:
    """One checkpoint of `card_id`; `phase` is the turn in flight, None for a row holding no turn."""
    return store.save_checkpoint(
        card_id,
        workflow=workflow.name,
        digest=workflow.digest() if digest is None else digest,
        reason=reason,
        agent={
            "current_turn": None if phase is None else {"kwargs": {"phase": phase, "loop": 0}},
            "queue": [],
        },
        saved_at=EARLIER,
    )


def test_a_resumable_milestone_run_is_the_recorded_run(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root)

    run = orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)

    assert (run.id, run.workflow, run.status) == (RESUME_RUN_ID, "milestone", "escalated")
    assert (run.base_branch, run.branch_prefix) == ("main", PREFIX)
    assert run.config.max_concurrent_stories == 3


def test_a_finished_milestone_run_is_refused_with_the_relaunch_remedy(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root, status="done")

    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)

    assert str(caught.value) == (
        f"run {RESUME_RUN_ID} finished; start new work with am run --milestone"
    )


def test_a_task_run_and_an_unknown_run_are_not_milestone_resumes(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root, workflow="task", status="started")

    with pytest.raises(cli.NotResumableError, match="'task'"):
        orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)
    with pytest.raises(cli.UnknownRunError, match="no-such-run"):
        orchestrate.resumable_milestone_run(root, "no-such-run")


def test_a_cancelled_milestone_run_is_refused_for_resume(tmp_path, monkeypatch):
    """C9: unknown run, then wrong workflow, then cancelled -- the earlier
    refusals still win for a run that is also cancelled."""
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root, status="cancelled")

    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)

    assert str(caught.value) == (
        f"run {RESUME_RUN_ID} was cancelled; start new work with am run --milestone"
    )
    task_run = "20260924T120000Z-00000008"
    _record_resume_run(root, task_run, workflow="task", status="cancelled")
    with pytest.raises(cli.NotResumableError, match="'task'"):
        orchestrate.resumable_milestone_run(root, task_run)
    with pytest.raises(cli.UnknownRunError, match="no-such-run"):
        orchestrate.resumable_milestone_run(root, "no-such-run")


def _milestone_run(run_id: str, milestone_id: str | None) -> models.Run:
    """A recorded milestone run as `resumable_milestone_run` hands it on."""
    return models.Run(
        id=run_id,
        workflow="milestone",
        repo_dir=Path("/repo"),
        base_branch="main",
        branch_prefix=PREFIX,
        status="escalated",
        milestone_id=milestone_id,
    )


def test_a_resumed_run_names_its_milestone_by_the_short_id_in_its_run_id():
    """A run recorded before `milestone_id` existed falls back to the short
    id `cli.mint_run_id` put at the end of its run id, errors unchanged."""
    wanted = models.CardNode(id=_plan_id(9), title="Milestone 9", status="todo")
    other = models.CardNode(id=_plan_id(8), title="Milestone 8", status="todo")

    assert (
        orchestrate.find_run_milestone([other, wanted], _milestone_run(RESUME_RUN_ID, None))
        is wanted
    )
    missing = "20260924T120000Z-00000007"
    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.find_run_milestone([other, wanted], _milestone_run(missing, None))
    assert str(caught.value) == (
        f"run {missing!r} belongs to milestone 00000007, and 0 root"
        " cards on the board have that short id"
    )


def test_two_milestones_whose_short_ids_collide_each_resume_by_their_recorded_id():
    """S2's regression case: two roots share the eight-character short id, so
    the run id alone is ambiguous, but each recorded full id picks its own."""
    first = models.CardNode(
        id="00000009-0000-4000-8000-000000000001", title="Milestone 9a", status="todo"
    )
    second = models.CardNode(
        id="00000009-0000-4000-8000-000000000002", title="Milestone 9b", status="todo"
    )
    assert dag.short_id(first.id) == dag.short_id(second.id) == "00000009"
    roots = [first, second]

    assert orchestrate.find_run_milestone(roots, _milestone_run(RESUME_RUN_ID, first.id)) is first
    assert (
        orchestrate.find_run_milestone(
            roots, _milestone_run("20260924T130000Z-00000009", second.id)
        )
        is second
    )
    # Without the record the same pair is still ambiguous: the fallback is unchanged.
    with pytest.raises(cli.NotResumableError, match="2 root cards"):
        orchestrate.find_run_milestone(roots, _milestone_run(RESUME_RUN_ID, None))


def test_a_recorded_milestone_id_no_root_carries_is_refused_without_the_short_id_fallback():
    """The recorded id is authoritative: a root that merely shares the run
    id's short id is not a stand-in for a deleted or reparented milestone."""
    lookalike = models.CardNode(id=_plan_id(9), title="Milestone 9", status="todo")
    gone = "00000009-0000-4000-8000-00000000dead"
    run = _milestone_run(RESUME_RUN_ID, gone)

    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.find_run_milestone([lookalike], run)
    assert str(caught.value) == (
        f"run {RESUME_RUN_ID!r} belongs to milestone {gone}, and no root card"
        " on the board has that id"
    )
    with pytest.raises(cli.NotResumableError, match=gone):
        orchestrate.find_run_milestone(None, run)


def _resume_stories() -> list[census.StoryPlan]:
    """A: 11 done, 12 and 13 open. B: 21 open. C on A and B: 31 open, a merged
    root. D on A and B is closed, so its base is nobody's to build."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12), _plan_subtask(13)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id, b.id])
    d = _plan_story(4, [_plan_subtask(41, "done")], status="done", blocked_by=[a.id, b.id])
    return [a, b, c, d]


def test_the_open_cards_are_the_remaining_subtasks_and_every_open_merged_roots_resolver():
    cards = orchestrate.open_cards(_resume_stories(), branch_prefix=PREFIX, base_branch="main")

    assert [(card_id, workflow.name) for card_id, workflow in cards] == [
        (_plan_id(12), "task"),
        (_plan_id(13), "task"),
        (_plan_id(21), "task"),
        (_plan_id(31), "task"),
        (bases.resolver_card_id(_plan_id(3)), "integrate"),
    ]


def test_each_open_card_resumes_from_its_newest_row_or_the_turn_it_failed_in(
    tmp_path, monkeypatch
):
    """12 escalated with no turn left: rewound to its failed `review` turn. 13
    parked: that row. 21's newest row is `done`, under another digest even
    (Review Focus 2): nothing, and no refusal. 31 has none. C's resolver:
    its parked INTEGRATE row."""
    root = _resume_root(tmp_path, monkeypatch)
    base_c = bases.resolver_card_id(_plan_id(3))
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, _plan_id(12), "turn", phase="implement")
        failed = _save(opened, _plan_id(12), "turn", phase="review")
        _save(opened, _plan_id(12), "escalated")
        parked = _save(opened, _plan_id(13), "parked", phase="validate_spec")
        _save(opened, _plan_id(21), "turn", phase="plan")
        _save(opened, _plan_id(21), "done", digest="saved-under-another-task")
        resolver = _save(
            opened, base_c, "parked", phase="verify", workflow=integrate_workflow.INTEGRATE
        )
        cards = orchestrate.open_cards(_resume_stories(), branch_prefix=PREFIX, base_branch="main")

        found = orchestrate.resume_checkpoints(opened, cards)
    finally:
        opened.close()

    assert found == {_plan_id(12): failed, _plan_id(13): parked, base_c: resolver}


def test_a_subtask_saved_under_another_task_refuses_naming_the_card_and_both_digests(
    tmp_path, monkeypatch
):
    root = _resume_root(tmp_path, monkeypatch)
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, _plan_id(13), "parked", phase="plan", digest="saved-under-another-task")
        cards = orchestrate.open_cards(_resume_stories(), branch_prefix=PREFIX, base_branch="main")

        with pytest.raises(cli.CheckpointMismatchError) as caught:
            orchestrate.resume_checkpoints(opened, cards)
    finally:
        opened.close()

    message = str(caught.value)
    assert message.startswith("workflow changed since checkpoint")
    assert _plan_id(13) in message
    assert "saved-under-another-task" in message
    assert task_workflow.TASK.digest() in message


def test_a_resolver_is_judged_against_integrate_not_task(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    base_c = bases.resolver_card_id(_plan_id(3))
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, base_c, "parked", phase="verify", workflow=task_workflow.TASK)
        cards = orchestrate.open_cards(_resume_stories(), branch_prefix=PREFIX, base_branch="main")

        with pytest.raises(cli.CheckpointMismatchError) as caught:
            orchestrate.resume_checkpoints(opened, cards)
    finally:
        opened.close()

    assert base_c in str(caught.value)
    assert integrate_workflow.INTEGRATE.digest() in str(caught.value)


def test_an_escalation_with_no_turn_row_left_starts_the_card_fresh(tmp_path, monkeypatch):
    """A phase escalation's newest row holds no turn; with no `turn` row to
    rewind to, the card is started fresh rather than handed a checkpoint
    that names no phase to continue."""
    root = _resume_root(tmp_path, monkeypatch)
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, _plan_id(31), "escalated")

        point = orchestrate.resume_point(opened, _plan_id(31), task_workflow.TASK)
    finally:
        opened.close()

    assert point is None


def _dispatch(root: Path) -> models.Dispatch:
    return models.Dispatch(
        harness="fake",
        model="fake",
        role="reviewer",
        cwd=root,
        prompt_path=root / "prompt.txt",
        result_path=root / "result.json",
    )


def test_reopening_marks_orphans_harness_error_and_open_rows_started(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    statuses = {
        "a1": "done",
        "a2": "escalated",
        "a3": "stopped",
        "a4": "started",
        "a5": "pending",
        "closed": "escalated",
    }
    _record_resume_run(root)
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        opened.record_story(models.StoryRun(card_id="story-a", title="A", level=0, status="escalated"))
        for card, status in statuses.items():
            opened.record_subtask(
                "story-a",
                models.SubtaskRun(card_id=card, branch=f"m3/{card}", base_branch="main", status=status),
            )
        opened.record_phase("story-a", "a2", models.PhaseRun(name="review", kind="agent", status="started"))
        opened.record_attempt(
            "story-a", "a2", "review", models.Attempt(n=1, dispatch=_dispatch(root), status="started")
        )
        run = opened.load_run(RESUME_RUN_ID)

        orchestrate.reopen_rows(opened, run, {"a2", "a3", "a4", "a5"})

        after = opened.load_run(RESUME_RUN_ID)
    finally:
        opened.close()

    [story] = after.stories
    assert {subtask.card_id: subtask.status for subtask in story.subtasks} == {
        "a1": "done",
        "a2": "started",
        "a3": "started",
        "a4": "started",
        "a5": "pending",
        "closed": "escalated",
    }
    [review] = [subtask for subtask in story.subtasks if subtask.card_id == "a2"][0].phases
    assert [attempt.status for attempt in review.attempts] == ["harness_error"]


# ── run_milestone(resume_run_id=...) (card 54e4ec29) ────────────────────────


def _resume(project: Path, run_id: str, driver: Any, **overrides: Any) -> dict[str, Any]:
    """`run_milestone` continuing `run_id`: no milestone, prefix, base or bound given."""
    kwargs: dict[str, Any] = {"repo_dir": project, "driver": driver, "resume_run_id": run_id}
    kwargs.update(overrides)
    return orchestrate.run_milestone(None, **kwargs)


def _plant_integrate(
    project: Path, run_id: str, story_id: str, reason: str, *, digest: str | None = None
) -> store_module.Checkpoint:
    """One `INTEGRATE` checkpoint of `story_id`'s resolver, saved by `run_id`."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        return opened.save_checkpoint(
            bases.resolver_card_id(story_id),
            workflow=integrate_workflow.INTEGRATE.name,
            digest=integrate_workflow.INTEGRATE.digest() if digest is None else digest,
            reason=reason,
            agent={"current_turn": None, "queue": [{"kwargs": {"phase": "verify", "loop": 0}}]},
            saved_at=EARLIER,
        )
    finally:
        opened.close()


def _record_bounds(monkeypatch) -> list[int]:
    """Wrap `orchestrate.supervise`, which `run_milestone` reads at call time,
    and record the lane bound it is given."""
    bounds: list[int] = []
    real = orchestrate.supervise

    async def recording(plan, **kwargs):
        bounds.append(kwargs["max_concurrent"])
        return await real(plan, **kwargs)

    monkeypatch.setattr(orchestrate, "supervise", recording)
    return bounds


def _run_ids(project: Path) -> list[str]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [row["id"] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _checkpoint_rows(project: Path) -> list[tuple]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT run_id, card_id, seq, reason, digest FROM checkpoints"
                " ORDER BY run_id, card_id, seq"
            )
        ]
    finally:
        conn.close()


def _runs_tree() -> dict[str, bytes]:
    """Every path under the data dir's `runs`, with file contents: the journals included."""
    root = paths.data_dir() / "runs"
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else b"<dir>"
        for path in sorted(root.rglob("*"))
    }


def _never_consulted(store, card_id):
    pytest.fail("a resume consulted the lenient relaunch lookup cli.continuable_checkpoint")


@requires_git
@requires_brd
def test_a_resume_reuses_the_recorded_settings_and_hands_each_open_checkpoint_on(
    project, fake_bases, monkeypatch
):
    """Spec test 2: no prefix, base or bound is given, yet the subtasks stack
    on `main` under `PREFIX` and the tree runs three lanes; a1 continues from
    its checkpoint, C's resolver checkpoint reaches `bases.build`, and the
    lenient relaunch lookup is never read."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}), max_concurrent=3)
    assert first["escalated"] is True, first
    run_id = first["run_id"]
    turn = _plant(project, run_id, a1, "turn", queue=("review",))
    resolver = _plant_integrate(project, run_id, story_c, "parked")
    bounds = _record_bounds(monkeypatch)
    monkeypatch.setattr(cli, "continuable_checkpoint", _never_consulted)
    driver = CheckpointDriver()

    result = _resume(project, run_id, driver)

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert result["run_id"] == run_id
    assert _run_ids(project) == [run_id]
    assert bounds == [3]
    by_card = {call["card"]: call for call in driver.calls}
    assert by_card[a1]["base"] == "main"
    assert by_card[a1]["branch"] == _branch(project, a1)
    root_plan = _root_plan(project, shape["milestone"], story_c)
    assert by_card[c1]["base"] == root_plan.branch
    got = driver.resumed[a1]
    assert (got.run_id, got.card_id, got.seq, got.reason) == (run_id, a1, turn.seq, "turn")
    assert driver.resumed[b1] is _ABSENT
    assert driver.resumed[c1] is _ABSENT
    base_got = fake_bases.resumed[story_c]
    assert (base_got.card_id, base_got.seq, base_got.reason) == (
        bases.resolver_card_id(story_c),
        resolver.seq,
        "parked",
    )
    run = _load(project, run_id)
    assert (run.status, run.branch_prefix, run.base_branch) == ("done", PREFIX, "main")
    assert run.config.max_concurrent_stories == 3


@requires_git
@requires_brd
def test_a_resume_hands_a_subtask_less_storys_resolver_checkpoint_to_its_base(
    project, fake_bases
):
    """J has no subtasks, so its base is built by its base-only lane, not a
    subtask lane; its resolver's checkpoint must reach `bases.build` there too."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_j = shape["stories"]["J"]
    fake_bases.outcomes[story_j] = bases.BaseFailed("J's resolver escalated")
    first = _run(project, shape["milestone"], FakeDriver())
    assert (first["escalated"], first["story"]) == (True, story_j), first
    resolver = _plant_integrate(project, first["run_id"], story_j, "parked")
    fake_bases.outcomes.clear()

    result = _resume(project, first["run_id"], FakeDriver())

    assert result["done"] is True, result
    got = fake_bases.resumed[story_j]
    assert (got.run_id, got.card_id, got.seq, got.reason) == (
        first["run_id"],
        bases.resolver_card_id(story_j),
        resolver.seq,
        "parked",
    )


@requires_git
@requires_brd
def test_a_stale_resolver_checkpoint_refuses_the_whole_resume_and_writes_nothing(
    project, fake_bases, monkeypatch
):
    """Spec test 4: one resolver saved under another INTEGRATE refuses the
    whole resume before anything is driven, recorded or fetched."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}), max_concurrent=3)
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",))
    _plant_integrate(project, run_id, story_c, "parked", digest="saved-under-another-integrate")
    before = (
        _runs_tree(),
        _statuses(_load(project, run_id)),
        _checkpoint_rows(project),
        _local_branches(project),
    )
    git_calls = _record_git(monkeypatch)
    driver = CheckpointDriver()

    with pytest.raises(cli.CheckpointMismatchError) as caught:
        _resume(project, run_id, driver)

    message = str(caught.value)
    assert message.startswith("workflow changed since checkpoint")
    assert bases.resolver_card_id(story_c) in message
    assert "saved-under-another-integrate" in message
    assert integrate_workflow.INTEGRATE.digest() in message
    assert driver.calls == [] and fake_bases.calls == []
    assert git_calls == []
    assert (
        _runs_tree(),
        _statuses(_load(project, run_id)),
        _checkpoint_rows(project),
        _local_branches(project),
    ) == before


@requires_git
@requires_brd
def test_a_merged_base_from_the_interrupted_run_is_reused_and_not_merged_again(project):
    """Spec test 7, on the real `bases.build`: A and B finished and C's base
    was merged before c1 escalated. The resume drives c1 only, on the same
    base commit, and neither the base nor `main` moves."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (c1,) = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    first = _run(
        project,
        shape["milestone"],
        BranchingDriver(outcomes={c1: ("review", "boom")}),
        commands=[PASS_CMD],
    )
    assert first["escalated"] is True, first
    assert first["bases"] == [_bases_entry(story_c, root_plan)]
    base_sha = _sha(project, root_plan.branch)
    main_sha = _sha(project, "main")
    driver = BranchingDriver()

    result = _resume(project, first["run_id"], driver, commands=[PASS_CMD])

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert [call["card"] for call in driver.calls] == [c1]
    assert driver.calls[0]["base"] == root_plan.branch
    assert result["completed"] == [c1]
    assert result["bases"] == [_bases_entry(story_c, root_plan)]
    assert _sha(project, root_plan.branch) == base_sha
    assert _sha(project, "main") == main_sha


@requires_git
@requires_brd
def test_a_card_finished_by_hand_since_the_interrupt_is_neither_checked_nor_driven(project):
    """Review Focus 1: a1's checkpoint is stale, but a human finished a1 on the
    board, so it is not open: no refusal, and only a2 is driven."""
    shape = _milestone(project, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",), digest="saved-under-another-task")
    rollup.set_status(a1, "done", repo_dir=project)
    driver = CheckpointDriver()

    result = _resume(project, run_id, driver)

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert [call["card"] for call in driver.calls] == [a2]
    assert result["completed"] == [a2]


@requires_git
@requires_brd
def test_a_resume_after_an_integrate_escalation_retries_integrate(project, integrate_recorder):
    """Review Focus 4: nothing is left to drive, so the resume runs no lane
    and retries Integrate under the same run id."""
    shape = _milestone(project, {"A": 1})
    integrate_recorder.outcome = integration.IntegrateEscalation(
        story=None, files=[], detail="the suite is red"
    )
    first = _run(project, shape["milestone"], BranchingDriver())
    assert first["escalated"] is True, first
    integrate_recorder.outcome = None
    driver = BranchingDriver()

    result = _resume(project, first["run_id"], driver)

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert result["completed"] == []
    assert driver.calls == []
    assert [call["run_id"] for call in integrate_recorder.calls] == [first["run_id"]] * 2
    assert _load(project, first["run_id"]).status == "done"


@requires_git
@requires_brd
def test_an_escalated_resume_still_says_it_resumed(project):
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))

    again = _resume(project, first["run_id"], FakeDriver(outcomes={a1: ("review", "still")}))

    assert again["escalated"] is True
    assert again["resumed"] is True
    assert again["run_id"] == first["run_id"]
    assert again["detail"] == "still"


def test_a_fresh_run_without_a_prefix_is_refused_before_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(board, "roots", lambda **kwargs: pytest.fail("the board was read"))

    with pytest.raises(ValueError, match="branch prefix"):
        orchestrate.run_milestone("Milestone 3", repo_dir=tmp_path, base_branch="main")


# ── live control: pause and cancel (card 0e1edf31) ──────────────────────────


def _lease(project: Path, run_id: str) -> store_module.LeaseRow | None:
    """The run's lease row, read over a second connection as `am status` would."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return store_module.read_lease(conn, run_id)
    finally:
        conn.close()


def _send(project: Path, run_id: str, command: str, *, token: str | None = None) -> None:
    """Insert one request over a second connection, as `am pause`/`am cancel` would.

    Addressed to the live lease's token unless `token` names another one.
    """
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        if token is None:
            lease = store_module.read_lease(conn, run_id)
            assert lease is not None and lease.accepting, "no open lease to address the request to"
            token = lease.token
        with store_module.immediate(conn):
            store_module.add_control(
                conn, run_id, lease=token, command=command, requested_at=STARTED_AT
            )
    finally:
        conn.close()


def _controls(project: Path, run_id: str) -> list[store_module.ControlRow]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return store_module.control_requests(conn, run_id)
    finally:
        conn.close()


def _send_then_await_stop(project: Path, run_id: str, command: str) -> Gate:
    """A gate that sends `command` mid-subtask, then holds the call until the
    run's watcher has applied it and the stop fired (no sleeps)."""

    async def gate(stop: StopSignal | None) -> None:
        _send(project, run_id, command)
        await _await_stop(stop)

    return gate


@requires_git
@requires_brd
def test_a_run_with_no_control_integrates_as_before(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]

    result = _run(project, shape["milestone"], FakeDriver(), control_interval=0)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result["done"] is True, result
    assert not {"paused", "cancelled", "control", "escalated"} & set(result)
    assert len(integrate_recorder.calls) == 1
    assert _statuses(_load(project, run_id)) == {"run": "done", story_a: "done", a1: "done"}
    assert _controls(project, run_id) == []


@requires_git
@requires_brd
def test_the_lease_is_released_and_its_window_closed_when_run_milestone_returns(
    project, integrate_recorder, monkeypatch
):
    """C2/C4: the lease is held through Integrate with its window already
    closed (`controlled` closed it when the tree returned), and gone once
    `run_milestone` returns."""
    shape = _milestone(project, {"A": 1})
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    seen: list[store_module.LeaseRow | None] = []

    def integrate_reading_the_lease(**kwargs: Any) -> Any:
        seen.append(_lease(project, run_id))
        return integrate_recorder(**kwargs)

    monkeypatch.setattr(integration, "integrate_milestone", integrate_reading_the_lease)

    result = _run(project, shape["milestone"], FakeDriver(), control_interval=0)

    assert result["done"] is True, result
    (during,) = seen
    assert during is not None, "no lease was held while Integrate ran"
    assert during.run_id == run_id
    assert during.accepting is False
    assert _lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_crash_after_a_pause_propagates_releases_the_lease_and_records_neither(
    project, integrate_recorder
):
    """Error paths: a lane's BaseException leaves the run as §7 says; the
    pause already applied does not turn it into `stopped`, and the lease is
    released on the way out."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    driver = GatedDriver(
        outcomes={a1: _LaneKilled("the manager died after the pause")},
        gates={a1: _send_then_await_stop(project, run_id, "pause")},
    )

    with pytest.raises(_LaneKilled):
        _run_or_fail_if_it_hangs(
            lambda: _run(project, shape["milestone"], driver, control_interval=0)
        )

    assert _load(project, run_id).status == "started"
    assert _lease(project, run_id) is None
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_refused_resume_never_takes_a_lease(project, monkeypatch):
    """Error paths: `resume_checkpoints`' refusal comes before `record_run`,
    so before the lease."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",), digest="saved-under-another-task")

    def never(self: control.Lease) -> control.Lease:
        pytest.fail("a lease was taken before the resume was refused")

    monkeypatch.setattr(control.Lease, "__enter__", never)

    with pytest.raises(cli.CheckpointMismatchError):
        _resume(project, run_id, CheckpointDriver(), control_interval=0)

    assert _lease(project, run_id) is None


@requires_git
@requires_brd
def test_resuming_a_run_recorded_before_milestone_id_stamps_it(project):
    """A pre-migration run resolves through the short-id fallback once, and
    its record carries the resolved milestone's full id from then on."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    assert first["escalated"] is True, first
    run_id = first["run_id"]
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.record_run(_load(project, run_id).model_copy(update={"milestone_id": None}))
    finally:
        opened.close()
    assert _load(project, run_id).milestone_id is None

    result = _resume(project, run_id, FakeDriver())

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert _load(project, run_id).milestone_id == shape["milestone"]


@requires_git
@requires_brd
def test_a_request_left_under_an_earlier_lease_never_reaches_the_resumed_run(project):
    """C4: a pause addressed to the interrupted process's token stays unhandled
    and the resumed run, under its own fresh lease, finishes clean."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _send(project, run_id, "pause", token="the-interrupted-processes-lease")

    result = _resume(project, run_id, FakeDriver(), control_interval=0)

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert [row.handled_at for row in _controls(project, run_id)] == [None]
    assert _load(project, run_id).status == "done"


@requires_git
@requires_brd
def test_an_integrate_that_raises_still_releases_the_lease(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    integrate_recorder.outcome = RuntimeError("integrate blew up")

    with pytest.raises(RuntimeError, match="integrate blew up"):
        _run(project, shape["milestone"], FakeDriver(), control_interval=0)

    assert _lease(project, run_id) is None
    assert _load(project, run_id).status == "started"


@requires_git
@requires_brd
def test_a_paused_milestone_parks_records_stopped_and_skips_integrate(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "pause")})

    result = _run(project, shape["milestone"], driver, control_interval=0)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "paused": True,
        "run_id": run_id,
        "stopped": [{"story": story_a, "subtask": a1, "before_phase": "implement"}],
        "completed": [],
        "pending": [story_b],
        "warnings": [],
        "resume": f"am resume {run_id}",
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "stopped",
        story_a: "stopped",
        a1: "stopped",
        a2: "pending",
        story_b: "pending",
        b1: "pending",
    }
    assert integrate_recorder.calls == []
    assert [(row.command, row.handled_at is not None) for row in _controls(project, run_id)] == [
        ("pause", True)
    ]


@requires_git
@requires_brd
def test_a_pause_applied_after_the_last_lane_already_finished_still_skips_integrate(
    project, integrate_recorder, monkeypatch
):
    """C6 case 3 applies even when every lane had already finished: the
    request lands after the watcher stopped and before the window closed, so
    only `controlled`'s final sweep applies it."""
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    real_close_window = control.Lease.close_window

    def the_request_lands_then_the_window_closes(self: control.Lease) -> None:
        _send(project, run_id, "pause")
        real_close_window(self)

    monkeypatch.setattr(control.Lease, "close_window", the_request_lands_then_the_window_closes)
    driver = GatedDriver()

    result = _run(project, shape["milestone"], driver, control_interval=0)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "paused": True,
        "run_id": run_id,
        "stopped": [],
        "completed": [a1],
        "pending": [],
        "warnings": [],
        "resume": f"am resume {run_id}",
    }
    assert _statuses(_load(project, run_id)) == {"run": "stopped", story_a: "done", a1: "done"}
    assert integrate_recorder.calls == []
    assert [row.handled_at is not None for row in _controls(project, run_id)] == [True]


@requires_git
@requires_brd
def test_a_lane_waiting_for_a_slot_ends_stopped_on_a_pause(project, integrate_recorder):
    """Three ready stories, two slots: `queued` waits for a slot when the
    pause lands, takes it, sees the stop and never reaches the driver."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    (first, second, queued) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,), (q1,) = subtasks[first], subtasks[second], subtasks[queued]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_then_pause(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "both slotted lanes in flight")
        _send(project, run_id, "pause")
        await _await_stop(stop)

    driver = GatedDriver(gates={f1: meet_then_pause, s1: _meet_then_await_stop(pair)})

    result = _run(project, shape["milestone"], driver, max_concurrent=2, control_interval=0)

    assert q1 not in [call["card"] for call in driver.calls]
    assert result["paused"] is True, result
    assert result["stopped"] == [
        {"story": first, "subtask": f1, "before_phase": "implement"},
        {"story": second, "subtask": s1, "before_phase": "implement"},
        {"story": queued, "subtask": q1, "before_phase": None},
    ]
    assert _statuses(_load(project, run_id)) == {
        "run": "stopped",
        first: "stopped",
        f1: "stopped",
        second: "stopped",
        s1: "stopped",
        queued: "stopped",
        q1: "pending",
    }
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_cancelled_milestone_records_cancelled_and_skips_integrate(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 2})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "cancel")})

    result = _run(project, shape["milestone"], driver, control_interval=0)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "cancelled": True,
        "run_id": run_id,
        "stopped": [{"story": story_a, "subtask": a1, "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "cancelled",
        story_a: "stopped",
        a1: "stopped",
        a2: "pending",
    }
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_cancel_after_pause_wins_and_records_cancelled(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)

    async def pause_then_cancel(stop: StopSignal | None) -> None:
        _send(project, run_id, "pause")
        await _await_stop(stop)
        _send(project, run_id, "cancel")

    driver = GatedDriver(gates={a1: pause_then_cancel})

    result = _run(project, shape["milestone"], driver, control_interval=0)

    assert result["cancelled"] is True, result
    assert "paused" not in result and "resume" not in result
    assert _load(project, run_id).status == "cancelled"
    assert [(row.command, row.handled_at is not None) for row in _controls(project, run_id)] == [
        ("pause", True),
        ("cancel", True),
    ]
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_an_escalation_under_pause_stays_escalated_and_carries_control_pause(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_pause_then_escalate(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a1 and b1 in flight together")
        _send(project, run_id, "pause")
        await _await_stop(stop)

    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: meet_pause_then_escalate, b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2, control_interval=0)

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
        "control": "pause",
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
    }
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_cancel_with_an_escalated_lane_records_cancelled_and_lists_escalations(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_cancel_then_escalate(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a1 and b1 in flight together")
        _send(project, run_id, "cancel")
        await _await_stop(stop)

    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: meet_cancel_then_escalate, b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2, control_interval=0)

    assert result == {
        "cancelled": True,
        "run_id": run_id,
        "stopped": [{"story": story_b, "subtask": b1, "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
        "escalations": [
            {
                "level": 0,
                "story": story_a,
                "subtask": a1,
                "failed_phase": "review",
                "detail": "reviewer found a blocker",
            }
        ],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "cancelled",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
    }
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_resumed_paused_run_reports_resumed_and_bases_through_report(project, fake_bases):
    """Every branch goes through `report`: a pause on a resume carries
    `resumed` and the merged base C built in this invocation."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    assert first["escalated"] is True, first
    run_id = first["run_id"]
    driver = GatedDriver(gates={c1: _send_then_await_stop(project, run_id, "pause")})

    result = _resume(project, run_id, driver, control_interval=0)

    assert result["paused"] is True, result
    assert result["resumed"] is True
    assert result["resume"] == f"am resume {run_id}"
    assert result["bases"] == [_bases_entry(story_c, root_plan)]
    assert result["stopped"] == [{"story": story_c, "subtask": c1, "before_phase": "implement"}]
    assert sorted(result["completed"]) == sorted([a1, b1])
    assert _load(project, run_id).status == "stopped"


@requires_git
@requires_brd
def test_a_pause_lets_the_running_phase_finish_and_parks_before_the_next(
    project, fresh_pygents, integrate_recorder
):
    """Success Criterion 1, on a real M6 pygents subtask agent over a
    step-only workflow: the pause lands while `first` runs; `first` finishes;
    the agent parks through ON_PAUSE with `second` at the queue head; `second`
    never runs; the run records `stopped`."""
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    watch = _ThreadWatch()
    ran: list[str] = []

    def first(card: str) -> dict[str, Any]:
        ran.append("first")
        _send(project, run_id, "pause")
        if not watch.paused.wait(WAIT):
            raise RuntimeError("the pause never reached the run")
        return {"first": 1}

    def second(card: str) -> dict[str, Any]:
        ran.append("second")
        return {"second": 2}

    workflow = Workflow("m9_pause_parks", (Step("first", first), Step("second", second)))

    async def drive(*, store, run_id, card, parent, subtask, repo_dir, stop=None, **_: Any):
        stop.register(watch)
        try:
            summary = await runtime_engine.run_subtask_async(
                workflow,
                store,
                story_id=parent.id,
                subtask=subtask,
                repo_dir=repo_dir,
                stop=stop,
            )
        finally:
            stop.unregister(watch)
        return cli.SubtaskDrive(summary=summary, warnings=list(summary.warnings))

    result = _run(project, shape["milestone"], drive, control_interval=0)

    assert ran == ["first"]
    assert result["paused"] is True, result
    assert result["stopped"] == [{"story": story_a, "subtask": a1, "before_phase": "second"}]
    assert result["resume"] == f"am resume {run_id}"
    assert _statuses(_load(project, run_id)) == {
        "run": "stopped",
        story_a: "stopped",
        a1: "stopped",
    }
    assert integrate_recorder.calls == []
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        newest = opened.latest_checkpoint(a1)
    finally:
        opened.close()
    assert newest.reason == "parked"
    assert newest.agent["queue"][0]["kwargs"]["phase"] == "second"
