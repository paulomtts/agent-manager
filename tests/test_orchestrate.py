"""Behaviour of the milestone runner (orchestration addendum O6, Integrate I6).

Two tiers, per design §14:

- `plan_levels`, `story_tips`, `stale_story_anchors`, `build_dag_tree` and the
  payload helpers are pure and get unit tests on hand-built plans, outcomes or
  plain items;
- `run_milestone` runs on git-tier fixtures -- a real temporary git repo and an
  in-memory `FakeBoard` (tests/conftest.py) behind `board.run_brd`, with
  `XDG_DATA_HOME` under `tmp_path` so `paths.data_dir()` never touches the
  developer's own -- with the harness
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
import os
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import grafo
import pytest
from typer.testing import CliRunner

from lockhelpers import _holder, _probe, _reap

from agent_manager import bases, board, census, cli, comments, control, dag, detach, errors, integration, locks, models, orchestrate, paths, runs
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


@dataclass(frozen=True)
class _DagItem:
    """A plain item for `build_dag_tree`: an id and the ids blocking it."""

    id: str
    blockers: tuple[str, ...] = ()


def _dag_factory(
    calls: dict[str, dict[str, Any]],
) -> Callable[[_DagItem], Callable[..., Awaitable[str]]]:
    """A `node_factory` whose coroutine records the kwargs it got and returns `out-<id>`."""

    def factory(item: _DagItem) -> Callable[..., Awaitable[str]]:
        async def run(**forwarded: Any) -> str:
            calls[item.id] = forwarded
            return f"out-{item.id}"

        return run

    return factory


def _dag_forward(item: _DagItem) -> str:
    """Reads `.id`, so it fails loudly if handed a blocker id instead of the item."""
    return f"from_{item.id}"


async def _build(
    items: list[_DagItem],
    calls: dict[str, dict[str, Any]],
    forward: Callable[[_DagItem], str | None] | None = _dag_forward,
) -> tuple[dict[str, grafo.Node], list[grafo.Node]]:
    return await orchestrate.build_dag_tree(
        items,
        id_of=lambda item: item.id,
        blockers_of=lambda item: item.blockers,
        node_factory=_dag_factory(calls),
        forward=forward,
    )


async def test_build_dag_tree_single_blocker_wires_edge():
    """A <- B: one timeout-less node per item, keyed and ordered by item; B is
    reached from A by an edge forwarding A's output as `forward(A)`; A alone
    is a root."""
    a, b = _DagItem("a"), _DagItem("b", ("a",))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b], calls)

    assert list(nodes) == ["a", "b"]
    assert [node.uuid for node in nodes.values()] == ["a", "b"]
    assert [node._timeout for node in nodes.values()] == [None, None]
    assert nodes["a"].children == [nodes["b"]]
    assert nodes["b"].children == []
    assert roots == [nodes["a"]]

    await grafo.TreeExecutor(uuid="single", roots=roots).run()

    assert calls == {"a": {}, "b": {"from_a": "out-a"}}


async def test_build_dag_tree_multi_blocker_gets_an_edge_from_every_parent():
    """A and B both block C: C gets one edge from each, waits on both parents'
    events, is no root, and receives both blockers' forwarded outputs."""
    a, b = _DagItem("a"), _DagItem("b")
    c = _DagItem("c", ("a", "b"))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b, c], calls)

    assert nodes["a"].children == [nodes["c"]]
    assert nodes["b"].children == [nodes["c"]]
    assert roots == [nodes["a"], nodes["b"]]
    assert nodes["c"] not in roots
    assert len(nodes["c"]._parent_events) == 2
    assert nodes["c"]._parent_events[0] is nodes["a"]._event
    assert nodes["c"]._parent_events[1] is nodes["b"]._event

    await grafo.TreeExecutor(uuid="merged", roots=roots).run()

    assert calls["c"] == {"from_a": "out-a", "from_b": "out-b"}


async def test_build_dag_tree_no_forward_connects_without_kwarg():
    """With `forward=None` the single-blocker edge still exists, but nothing is
    forwarded along it."""
    a, b = _DagItem("a"), _DagItem("b", ("a",))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b], calls, forward=None)

    assert nodes["a"].children == [nodes["b"]]
    assert nodes["a"]._forward_map == {}
    assert roots == [nodes["a"]]

    await grafo.TreeExecutor(uuid="unforwarded", roots=roots).run()

    assert calls == {"a": {}, "b": {}}


async def test_build_dag_tree_forward_returning_none_connects_without_kwarg():
    """A `forward` that returns `None` for a blocker behaves as no `forward` for
    that edge only."""
    a, b = _DagItem("a"), _DagItem("b", ("a",))
    c, d = _DagItem("c"), _DagItem("d", ("c",))
    calls: dict[str, dict[str, Any]] = {}

    def only_a(item: _DagItem) -> str | None:
        return "from_a" if item.id == "a" else None

    nodes, roots = await _build([a, b, c, d], calls, forward=only_a)

    assert nodes["c"].children == [nodes["d"]]
    assert nodes["c"]._forward_map == {}
    assert roots == [nodes["a"], nodes["c"]]

    await grafo.TreeExecutor(uuid="partial", roots=roots).run()

    assert calls == {"a": {}, "b": {"from_a": "out-a"}, "c": {}, "d": {}}


async def test_build_dag_tree_roots_follow_items_order():
    """Roots are exactly the zero-blocker items, in items order: `e` after `b`
    proves the order is the items', not alphabetical, and the merged `c`
    listed between them is no root."""
    a, b, e = _DagItem("a"), _DagItem("b"), _DagItem("e")
    c = _DagItem("c", ("a", "b"))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, c, b, e], calls)

    assert list(nodes) == ["a", "c", "b", "e"]
    assert roots == [nodes["a"], nodes["b"], nodes["e"]]
    assert nodes["c"] not in roots


async def test_build_dag_tree_merged_item_still_forwards_to_its_dependent():
    """A single-blocker item behind a merged item is reached by an edge
    forwarding the merged item's output."""
    a, b = _DagItem("a"), _DagItem("b")
    joined = _DagItem("joined", ("a", "b"))
    d = _DagItem("d", ("joined",))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b, joined, d], calls)

    assert nodes["joined"].children == [nodes["d"]]
    assert roots == [nodes["a"], nodes["b"]]

    await grafo.TreeExecutor(uuid="joined", roots=roots).run()

    assert calls["d"] == {"from_joined": "out-joined"}


async def test_build_dag_tree_empty_items():
    assert await _build([], {}) == ({}, [])


async def test_build_dag_tree_never_runs_an_item_whose_blocker_raised():
    """A raises, so grafo never enqueues C (blocked by A and B): C's coroutine
    is never called and C has no output. grafo's own gate, no waiting here."""
    a, b = _DagItem("a"), _DagItem("b")
    c = _DagItem("c", ("a", "b"))
    calls: dict[str, dict[str, Any]] = {}

    def factory(item: _DagItem) -> Callable[..., Awaitable[str]]:
        async def run(**forwarded: Any) -> str:
            calls[item.id] = forwarded
            # One scheduling tick before A raises, so every root has been
            # picked up by a worker first: were C a root, it would be called.
            await asyncio.sleep(0)
            if item.id == "a":
                raise RuntimeError("a failed")
            return f"out-{item.id}"

        return run

    nodes, roots = await orchestrate.build_dag_tree(
        [a, b, c],
        id_of=lambda item: item.id,
        blockers_of=lambda item: item.blockers,
        node_factory=factory,
        forward=_dag_forward,
    )
    executor = grafo.TreeExecutor(uuid="raised", roots=roots)

    await executor.run()

    assert "c" not in calls
    assert nodes["c"].output is None
    assert [type(error) for error in executor.errors] == [RuntimeError]


async def test_build_dag_tree_four_node_shape_completes():
    """Spec §1.1: A and B are roots, D is blocked by A, C by B and D. Nothing
    here hand-rolls the join -- grafo's own edges hold C back until both B
    and D's parent events are set, exactly as `lane` relies on today (no
    manual Event-waiting of its own). B returns, then A after a wall-clock
    gap, so D is ready only after grafo shrank its pool. Under grafo 0.3.5
    with C an unconnected root, C sat in a worker waiting on D while D was
    queued behind exit sentinels, and the run never returned. grafo 0.3.6
    fixed that pool bug, so this is now a regression pin for the shape, not a
    red test for `build_dag_tree`'s edges (those are pinned by the edge,
    roots and raised-blocker tests above)."""
    a, b = _DagItem("a"), _DagItem("b")
    d = _DagItem("d", ("a",))
    c = _DagItem("c", ("b", "d"))

    def factory(item: _DagItem) -> Callable[..., Awaitable[str]]:
        async def run(**forwarded: Any) -> str:
            # One scheduling tick first, so a worker takes C before B
            # returns and shrinks the pool (spec §1.1).
            await asyncio.sleep(0)
            if item.id == "a":
                # The pool-shrink window is wall-clock, not ordering
                # (spec §1.1): A must return after B's worker shrank it.
                await asyncio.sleep(0.05)
            return f"out-{item.id}"

        return run

    nodes, roots = await orchestrate.build_dag_tree(
        [a, b, d, c],
        id_of=lambda item: item.id,
        blockers_of=lambda item: item.blockers,
        node_factory=factory,
    )

    try:
        await asyncio.wait_for(grafo.TreeExecutor(uuid="four", roots=roots).run(), 2.0)
    except TimeoutError:
        pytest.fail("the four-node shape hung: D is queued behind grafo's exit sentinels")

    assert {uuid: node.output for uuid, node in nodes.items()} == {
        "a": "out-a",
        "b": "out-b",
        "d": "out-d",
        "c": "out-c",
    }


def test_story_tips_name_every_story_with_subtasks_in_census_order():
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="done")
    empty = _plan_story(2, [], blocked_by=[a.id])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id])

    tips = orchestrate.story_tips([a, empty, c], branch_prefix="m3", base_branch="main")

    assert tips == [
        {"story": a.id, "tip": _branch_of(a.subtasks[-1])},
        {"story": c.id, "tip": _branch_of(c.subtasks[-1])},
    ]


def test_milestone_claims_lists_milestone_remaining_subtasks_then_integration_branch():
    """X6: `card:M`, every remaining subtask in census order, then
    `branch:<prefix>-integrate`. A done subtask, a closed story and a
    subtask-less story add no key."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    closed = _plan_story(2, [_plan_subtask(21)], status="done")
    c = _plan_story(3, [_plan_subtask(31), _plan_subtask(32)], blocked_by=[a.id])
    empty = _plan_story(4, [])

    keys = orchestrate.milestone_claims(_plan_id(99), [a, closed, c, empty], "m3")

    assert keys == [
        f"card:{_plan_id(99)}",
        f"card:{_plan_id(12)}",
        f"card:{_plan_id(31)}",
        f"card:{_plan_id(32)}",
        "branch:m3-integrate",
    ]


def test_milestone_claims_has_no_duplicates():
    """A card listed under two stories is claimed once, at its first place."""
    shared = _plan_subtask(11)
    a = _plan_story(1, [shared, _plan_subtask(12)])
    b = _plan_story(2, [shared])

    keys = orchestrate.milestone_claims(_plan_id(99), [a, b], "m3")

    assert keys == [
        f"card:{_plan_id(99)}",
        f"card:{_plan_id(11)}",
        f"card:{_plan_id(12)}",
        "branch:m3-integrate",
    ]


def test_story_claims_lists_milestone_story_remaining_subtasks_then_their_branches():
    """`card:M`, `card:<story>`, every remaining subtask's card in census
    order, then one `branch:` key per remaining subtask, same order. A done
    or out-of-play subtask adds neither key, and no integration branch is
    claimed: a story run does not integrate."""
    story = _plan_story(
        1,
        [
            _plan_subtask(11, "done"),
            _plan_subtask(12),
            _plan_subtask(13, "canceled"),
            _plan_subtask(14),
        ],
    )

    keys = orchestrate.story_claims(_plan_id(99), story, "m3")

    assert keys == [
        f"card:{_plan_id(99)}",
        f"card:{_plan_id(1)}",
        f"card:{_plan_id(12)}",
        f"card:{_plan_id(14)}",
        f"branch:{_branch_of(story.subtasks[1])}",
        f"branch:{_branch_of(story.subtasks[3])}",
    ]
    assert "branch:m3-integrate" not in keys


def test_story_claims_of_a_closed_story_are_the_milestone_and_story_cards_only():
    closed = _plan_story(1, [_plan_subtask(11), _plan_subtask(12)], status="done")
    finished = _plan_story(2, [_plan_subtask(21, "done"), _plan_subtask(22, "merged")])

    assert orchestrate.story_claims(_plan_id(99), closed, "m3") == [
        f"card:{_plan_id(99)}",
        f"card:{_plan_id(1)}",
    ]
    assert orchestrate.story_claims(_plan_id(99), finished, "m3") == [
        f"card:{_plan_id(99)}",
        f"card:{_plan_id(2)}",
    ]


def test_story_claims_has_no_duplicates():
    """A subtask listed twice is claimed once, card and branch, at its first place."""
    shared = _plan_subtask(11)
    story = _plan_story(1, [shared, _plan_subtask(12), shared])

    keys = orchestrate.story_claims(_plan_id(99), story, "m3")

    assert keys == [
        f"card:{_plan_id(99)}",
        f"card:{_plan_id(1)}",
        f"card:{_plan_id(11)}",
        f"card:{_plan_id(12)}",
        f"branch:{_branch_of(shared)}",
        f"branch:{_branch_of(story.subtasks[1])}",
    ]


def test_milestone_card_ids_cover_the_milestone_every_story_and_every_subtask():
    """Board-comments B7: the start flush covers done and closed cards too,
    since an earlier run may have left a pending comment on any of them."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    closed = _plan_story(2, [_plan_subtask(21)], status="done")
    empty = _plan_story(3, [])
    shared = _plan_story(4, [_plan_subtask(12)])

    ids = orchestrate.milestone_card_ids(_plan_id(99), [a, closed, empty, shared])

    assert ids == [
        _plan_id(99),
        _plan_id(1),
        _plan_id(11),
        _plan_id(12),
        _plan_id(2),
        _plan_id(21),
        _plan_id(3),
        _plan_id(4),
    ]


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
        "canceled": True,
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
        "canceled", "run_id", "stopped", "completed", "pending", "warnings", "escalations"
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
    canceled = orchestrate.controlled_payload("run-1", "cancel", [parked], [])
    assert canceled["canceled"] is True
    assert "cancelled" not in canceled
    assert "escalations" not in canceled
    assert "resume" not in canceled
    assert "paused" not in canceled
    paused = orchestrate.controlled_payload("run-1", "pause", [parked], [])
    assert "canceled" not in paused
    assert "cancelled" not in paused


def test_a_controlled_cancel_payload_reads_as_cancelled_on_board_and_comment():
    """The real cancel payload, not a hand-written one, still reads as the
    `cancelled` board status and the `cancelled` run-end comment."""
    parked = orchestrate.LaneOutcome(
        kind="stopped", story="A", level=0, subtask="a1", before_phase="plan"
    )
    payload = orchestrate.controlled_payload("run-1", "cancel", [parked], [])

    assert orchestrate.milestone_status(payload) == "cancelled"
    comment = comments.compose_run_end(
        run_id="run-1", milestone_id="ms-1", token="tok-1", payload=payload
    )
    assert comment.body.splitlines()[0] == "am · cancelled · run run-1"
    assert "next: `am run --milestone ms-1`" in comment.body.splitlines()


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


# ── the runner, on a real repo and a FakeBoard ──────────────────────────────


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


def _active_fake_board() -> Any:
    """The FakeBoard the `project` fixture installed as `board.run_brd`.

    Duck-typed: conftest classes are not importable from a test module under
    `--import-mode=importlib`, so `isinstance(..., FakeBoard)` is unavailable.
    """
    fake = board.run_brd
    if not (hasattr(fake, "add_card") and hasattr(fake, "cards")):
        raise AssertionError(
            "_add_card/_block seed the FakeBoard that the `project` fixture installs "
            f"as board.run_brd; board.run_brd is {fake!r} -- request `project`"
        )
    return fake


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    """Seed one card on the project's FakeBoard and return its id.

    `root` is kept so call sites read as before; one FakeBoard is one board.
    """
    return _active_fake_board().add_card(title, parent_id=parent)


def _block(root: Path, card_id: str, blocker: str) -> None:
    """Make `blocker` block `card_id`, as `brd block card_id --by blocker` would.

    Appends to the seeded card's `blocked_by` after the fact, because story
    blocks are added once both stories exist. Never stores `blocked` -- it is
    derived -- and is seeding, so it is not a `FakeBoard.writes` entry.
    """
    fake = _active_fake_board()
    for wanted in (card_id, blocker):
        if wanted not in fake.cards:
            raise AssertionError(f"_block: unknown card {wanted!r}")
    blocked_by = fake.cards[card_id].blocked_by
    if blocker not in blocked_by:
        blocked_by.append(blocker)


@pytest.fixture
def project(tmp_path, monkeypatch, fake_board) -> Path:
    """A real git repo on `main`, with a fresh FakeBoard as its board.

    `fake_board` (tests/conftest.py) installs an empty in-memory board as
    `board.run_brd`; `_add_card`/`_block` seed it, and every `board.*` call --
    from `run_milestone` or from a test body -- is answered by it. No `brd`
    process starts. Git stays real: worktrees and branches are what these
    tests are about.

    XDG_DATA_HOME points into tmp_path, so `paths.data_dir()` never lands a
    run artifact in the developer's home. The repo has no remote and one
    commit, "base".
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
                    status="stopped",
                    detail="stopped before implement",
                    before_phase="implement",
                )
            else:
                summary = SubtaskSummary(status="done")
            return cli.SubtaskDrive(summary=summary, warnings=warnings)
        finally:
            self.in_flight -= 1
            if card.id in self.returned:
                self.returned[card.id].set()


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_every_drivers_warnings_reach_the_result_in_order(project):
    shape = _milestone(project, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    driver = FakeDriver(warnings={a1: ["a1 warned"], a2: ["a2 warned", "a2 again"]})

    result = _run(project, shape["milestone"], driver)

    assert result["warnings"] == ["a1 warned", "a2 warned", "a2 again"]


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_a_keyboard_interrupt_from_the_driver_is_not_swallowed(project):
    """The catch is `Exception`, not `BaseException`: Ctrl-C stops the process,
    it is not an escalation a human should go and read."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    driver = FakeDriver(outcomes={a1: KeyboardInterrupt()})

    with pytest.raises(KeyboardInterrupt):
        _run(project, shape["milestone"], driver)


@pytest.mark.git
def test_a_repo_with_no_origin_prunes_worktrees_and_never_fetches(project, monkeypatch):
    shape = _milestone(project, {"A": 1})
    root = cli.resolve_repo_dir(project)
    calls = _record_git(monkeypatch)

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True
    assert [argv[2:] for argv in calls] == [["remote"], ["worktree", "prune"]]
    assert all(argv[:2] == ["-C", str(root)] for argv in calls)


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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
    # No run was left behind: the data directory holds nothing but the `git`
    # ProcessLock's own lock file, the one thing spec X7 does put there even on
    # this early a failure (paths.project_lock_path creates its `projects`
    # directory as soon as the lock object exists), and the project's
    # projection, which the read-only claims preflight (`cli.refuse_claimed`,
    # X5) opens before the fetch. That projection records no run.
    data = paths.data_dir()
    projects = data / "projects"
    db_name = paths.project_db_path(cli.resolve_repo_dir(project)).name
    written = sorted(
        str(entry.relative_to(data))
        for entry in data.rglob("*")
        if entry != projects
        and not (entry.parent == projects and entry.suffix == ".lock")
        and not (entry.parent == projects and entry.name.startswith(db_name))
    )
    assert written == []
    assert _run_ids(project) == []


def test_refresh_git_prunes_under_the_git_lock(tmp_path, monkeypatch):
    # Spec X7: `remote`, `fetch origin` and `worktree prune` all run while this
    # process holds the repository's git flock, so another process's probe
    # finds it busy at each call.
    seen: list[tuple[list[str], str]] = []

    def probing_run_git(argv: list[str]) -> str:
        seen.append((argv[2:], _probe(tmp_path, "git")))
        return "origin\n" if argv[2:] == ["remote"] else ""

    monkeypatch.setattr(worktree, "run_git", probing_run_git)

    orchestrate.refresh_git(tmp_path)

    assert seen == [
        (["remote"], "busy"),
        (["fetch", "origin"], "busy"),
        (["worktree", "prune"], "busy"),
    ]
    assert _probe(tmp_path, "git") == "free"


def test_a_git_lock_timeout_in_refresh_git_propagates_before_any_git_call(
    tmp_path, monkeypatch
):
    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run_git", lambda argv: calls.append(argv) or "")
    lock = worktree.git_lock(tmp_path)
    monkeypatch.setattr(lock, "_timeout", 0)
    child = _holder(tmp_path, "git")
    try:
        with pytest.raises(locks.LockTimeoutError):
            orchestrate.refresh_git(tmp_path)
    finally:
        _reap(child)

    assert calls == []


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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_the_bound_is_recorded_in_the_run_config(project):
    shape = _milestone(project, {"A": 1})

    result = _run(project, shape["milestone"], FakeDriver(), max_concurrent=2)

    assert result["done"] is True
    assert _load(project, result["run_id"]).config == models.RunConfig(max_concurrent_stories=2)


@pytest.mark.git
def test_a_fresh_run_records_its_milestones_full_id(project):
    shape = _milestone(project, {"A": 1})

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert _load(project, result["run_id"]).milestone_id == shape["milestone"]


@pytest.mark.git
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


@pytest.mark.git
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


def _supervised_run(
    project: Path, milestone: str
) -> tuple[store_module.Store, str, orchestrate.SupervisorPlan]:
    """What `run_milestone` sets up before it calls `supervise`, without the
    lease, the git refresh or Integrate: a recorded run, its planned rows, and
    the plan built from them. The caller closes the store."""
    root = cli.resolve_repo_dir(project)
    stories = census.flatten_milestone(board.tree(milestone, repo_dir=root)).stories
    levels = orchestrate.plan_levels(stories, branch_prefix=PREFIX, base_branch="main")
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    store = store_module.Store.open(root, run_id)
    store.record_run(
        models.Run(
            id=run_id,
            workflow=orchestrate.MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch="main",
            branch_prefix=PREFIX,
            status="started",
            started_at=STARTED_AT,
            config=models.RunConfig(max_concurrent_stories=3),
            milestone_id=milestone,
        )
    )
    rows = orchestrate.record_plan(store, levels, root=root, branch_prefix=PREFIX)
    plan = orchestrate.supervisor_plan(
        stories, levels, rows, branch_prefix=PREFIX, base_branch="main"
    )
    return store, run_id, plan


async def _supervise_shared(
    project: Path,
    store: store_module.Store,
    run_id: str,
    plan: orchestrate.SupervisorPlan,
    driver: Any,
    slots: asyncio.Semaphore,
) -> list[orchestrate.LaneOutcome]:
    """One `supervise` call on the shared `slots`, with its own `StopSignal`.
    `max_concurrent=3` is deliberately larger than any shared semaphore these
    tests pass: the caller's semaphore, not `max_concurrent`, is the bound."""
    return await orchestrate.supervise(
        plan,
        store=store,
        run_id=run_id,
        lease_token=f"{run_id}-lease",
        root=cli.resolve_repo_dir(project),
        drive=driver,
        commands=[],
        allow_no_verification=True,
        runner_factory=None,
        max_concurrent=3,
        stop=StopSignal(),
        slots=slots,
    )


async def _refill(slots: asyncio.Semaphore, capacity: int) -> None:
    """Acquire `slots` `capacity` times, then release them all: fails after
    WAIT seconds if any lane left a slot held."""
    for _ in range(capacity):
        await _within(slots.acquire(), "a slot a finished lane should have released")
    for _ in range(capacity):
        slots.release()


@pytest.mark.git
def test_two_supervise_calls_sharing_one_semaphore_never_exceed_it_combined(project):
    """Two milestones of three ready stories each, one shared two-slot
    semaphore, `max_concurrent=3` per call. Every lane stays in flight until a
    third lane enters the driver or the window expires: if each call used its
    own semaphore, a third (and more) would arrive inside the window; shared,
    only two lanes across both calls can ever be in flight."""
    shape_a = _milestone(project, {"A": 1, "B": 1, "C": 1})
    shape_b = _milestone(project, {"D": 1, "E": 1, "F": 1})
    subtasks = {**_subtasks_by_story(shape_a), **_subtasks_by_story(shape_b)}
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
    store_a, run_a, plan_a = _supervised_run(project, shape_a["milestone"])
    store_b, run_b, plan_b = _supervised_run(project, shape_b["milestone"])

    async def scenario() -> tuple[list[orchestrate.LaneOutcome], list[orchestrate.LaneOutcome]]:
        shared = asyncio.Semaphore(2)
        outcomes_a, outcomes_b = await asyncio.gather(
            _supervise_shared(project, store_a, run_a, plan_a, driver, shared),
            _supervise_shared(project, store_b, run_b, plan_b, driver, shared),
        )
        await _refill(shared, 2)
        return outcomes_a, outcomes_b

    # Two overlapping calls each save and restore grafo's level; restore it
    # here so this test can never leave grafo silenced for later tests.
    grafo_logger = logging.getLogger(orchestrate.GRAFO_LOGGER)
    level_before = grafo_logger.level
    try:
        outcomes_a, outcomes_b = asyncio.run(scenario())
    finally:
        grafo_logger.setLevel(level_before)
        store_a.close()
        store_b.close()

    assert driver.high_water == 2
    assert sorted(call["card"] for call in driver.calls) == sorted(
        card for cards in subtasks.values() for card in cards
    )
    for shape, outcomes, run_id in (
        (shape_a, outcomes_a, run_a),
        (shape_b, outcomes_b, run_b),
    ):
        assert [outcome.kind for outcome in outcomes] == ["done", "done", "done"]
        assert {outcome.story for outcome in outcomes} == set(shape["stories"].values())
        statuses = _statuses(_load(project, run_id))
        del statuses["run"]  # only run_milestone records the run's final status
        assert set(statuses.values()) == {"done"}


@pytest.mark.git
def test_an_escalation_in_one_sharing_call_leaves_the_other_its_slots(project):
    """Milestone X's only lane escalates; milestone Y's three lanes share the
    same two-slot semaphore. The escalation stops only X (each call has its own
    StopSignal), every Y lane still runs to done, the combined peak is exactly
    two, and both slots are free once both calls return. Every lane stays in
    flight until a third lane enters the driver or the window expires, so a
    call that ignored the shared semaphore would push the peak past two."""
    shape_x = _milestone(project, {"X": 1})
    shape_y = _milestone(project, {"P": 1, "Q": 1, "R": 1})
    (x1,) = shape_x["subtasks"]["X"]
    y_subtasks = _subtasks_by_story(shape_y)
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
        gates={
            card: hold_until_a_third_lane_arrives
            for card in [x1, *(cards[0] for cards in y_subtasks.values())]
        },
        outcomes={x1: ("review", "reviewer found a blocker")},
    )
    store_x, run_x, plan_x = _supervised_run(project, shape_x["milestone"])
    store_y, run_y, plan_y = _supervised_run(project, shape_y["milestone"])

    async def scenario() -> tuple[list[orchestrate.LaneOutcome], list[orchestrate.LaneOutcome]]:
        shared = asyncio.Semaphore(2)
        outcomes_x, outcomes_y = await asyncio.gather(
            _supervise_shared(project, store_x, run_x, plan_x, driver, shared),
            _supervise_shared(project, store_y, run_y, plan_y, driver, shared),
        )
        await _refill(shared, 2)
        return outcomes_x, outcomes_y

    grafo_logger = logging.getLogger(orchestrate.GRAFO_LOGGER)
    level_before = grafo_logger.level
    try:
        outcomes_x, outcomes_y = asyncio.run(scenario())
    finally:
        grafo_logger.setLevel(level_before)
        store_x.close()
        store_y.close()

    assert driver.high_water == 2
    assert [(outcome.kind, outcome.subtask) for outcome in outcomes_x] == [("escalated", x1)]
    assert [outcome.kind for outcome in outcomes_y] == ["done", "done", "done"]
    assert sorted(call["card"] for call in driver.calls) == sorted(
        [x1, *(card for cards in y_subtasks.values() for card in cards)]
    )


def test_the_async_core_takes_run_milestones_parameters_plus_slots():
    """`_run_milestone_async` is a coroutine function taking exactly
    `run_milestone`'s parameters, same kinds and defaults, plus a keyword-only
    `slots=None`; `run_milestone` itself gains nothing."""
    assert inspect.iscoroutinefunction(orchestrate._run_milestone_async)
    wrapper = inspect.signature(orchestrate.run_milestone).parameters
    core = dict(inspect.signature(orchestrate._run_milestone_async).parameters)
    slots = core.pop("slots")
    assert slots.kind is inspect.Parameter.KEYWORD_ONLY
    assert slots.default is None
    assert "slots" not in wrapper
    assert list(core) == list(wrapper)
    for name, parameter in wrapper.items():
        assert core[name].kind is parameter.kind, name
        assert core[name].default == parameter.default, name


def test_run_milestone_refuses_bad_arguments_before_starting_an_event_loop(
    tmp_path, monkeypatch
):
    """Validation stays in the sync wrapper: a bad bound is refused before
    `asyncio.run` is ever called."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    def no_loop(*args: Any, **kwargs: Any) -> Any:
        for arg in args:
            if inspect.iscoroutine(arg):
                arg.close()
        pytest.fail("an event loop was started before validation")

    monkeypatch.setattr(orchestrate.asyncio, "run", no_loop)

    with pytest.raises(ValueError, match="max_concurrent"):
        orchestrate.run_milestone(
            "Milestone 3",
            repo_dir=tmp_path,
            base_branch="main",
            branch_prefix=PREFIX,
            max_concurrent=0,
        )


@pytest.mark.git
def test_the_async_core_awaited_in_a_running_loop_is_bounded_by_the_callers_semaphore(
    project, integrate_recorder
):
    """Three ready stories, `max_concurrent=3`, but the caller hands the core a
    one-slot semaphore from its own running loop. Every lane stays in flight
    until a second lane enters the driver or the window expires: bounded by
    `max_concurrent`, a second lane would arrive inside the window; bounded by
    the caller's semaphore, only one lane is ever in flight. The run still
    finishes `done` with `run_milestone`'s payload and frees its slot."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    subtasks = _subtasks_by_story(shape)
    arrivals = 0
    second_arrived = asyncio.Event()

    async def hold_until_a_second_lane_arrives(stop: StopSignal | None) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals >= 2:
            second_arrived.set()
        try:
            await asyncio.wait_for(second_arrived.wait(), OVERSHOOT_WINDOW)
        except TimeoutError:
            pass

    driver = GatedDriver(
        gates={cards[0]: hold_until_a_second_lane_arrives for cards in subtasks.values()}
    )

    async def scenario() -> dict[str, Any]:
        shared = asyncio.Semaphore(1)
        result = await orchestrate._run_milestone_async(
            shape["milestone"],
            repo_dir=project,
            base_branch="main",
            branch_prefix=PREFIX,
            driver=driver,
            clock=lambda: STARTED_AT,
            max_concurrent=3,
            slots=shared,
        )
        await _refill(shared, 1)
        return result

    grafo_logger = logging.getLogger(orchestrate.GRAFO_LOGGER)
    level_before = grafo_logger.level
    try:
        result = asyncio.run(scenario())
    finally:
        grafo_logger.setLevel(level_before)

    order = _census_stories(project, shape["milestone"])
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert driver.high_water == 1
    assert sorted(call["card"] for call in driver.calls) == sorted(
        card for cards in subtasks.values() for card in cards
    )
    assert set(result) == {
        "done",
        "run_id",
        "levels",
        "completed",
        "tips",
        "warnings",
        "integrated",
    }
    assert result["done"] is True
    assert result["run_id"] == run_id
    assert result["levels"] == [{"level": 0, "stories": order}]
    assert result["completed"] == [card for story in order for card in subtasks[story]]
    assert set(result["integrated"]["merged"]) == set(order)
    (integrate_call,) = integrate_recorder.calls
    assert integrate_call["run_status"] == "started"
    run = _load(project, run_id)
    assert run.config == models.RunConfig(max_concurrent_stories=3)
    assert set(_statuses(run).values()) == {"done"}


@pytest.mark.git
def test_cancelling_the_async_core_mid_integrate_waits_for_integrate_before_closing_the_store(
    project, integrate_recorder, monkeypatch
):
    """Integrate runs on a worker thread that a cancel cannot stop. A caller
    cancelling the core while Integrate is in flight must not unwind it (closing
    the store, releasing the lease) until Integrate returns: Integrate still
    reads and writes the run's store, and must stay under the run's lease."""
    shape = _milestone(project, {"A": 1})
    entered = threading.Event()
    release = threading.Event()
    store_errors: list[BaseException] = []

    def slow_integrate(**kwargs: Any) -> Any:
        entered.set()
        release.wait(10)
        try:
            kwargs["store"].load_run(kwargs["run_id"])
        except Exception as error:  # noqa: BLE001 - the assertion reports it
            store_errors.append(error)
        return integrate_recorder(**kwargs)

    monkeypatch.setattr(integration, "integrate_milestone", slow_integrate)

    async def scenario() -> bool:
        task = asyncio.create_task(
            orchestrate._run_milestone_async(
                shape["milestone"],
                repo_dir=project,
                base_branch="main",
                branch_prefix=PREFIX,
                driver=FakeDriver(),
                clock=lambda: STARTED_AT,
            )
        )
        while not entered.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.2)
        unwound_early = task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return unwound_early

    grafo_logger = logging.getLogger(orchestrate.GRAFO_LOGGER)
    level_before = grafo_logger.level
    try:
        unwound_early = asyncio.run(scenario())
    finally:
        release.set()
        grafo_logger.setLevel(level_before)

    assert unwound_early is False
    assert store_errors == []
    assert len(integrate_recorder.calls) == 1


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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
    """`FakeDriver` that also takes `resume_from` and records it per card.

    Like the engine's kept path, a card handed a checkpoint comes back with
    `summary.resumed_at` set to that checkpoint's pending phase. A card in
    `declined` stands for a walk whose checkpoint was declined (its worktree
    could not be kept) and started over: `resumed_at` stays `None`.
    """

    resumed: dict[str, Any] = field(default_factory=dict)
    declined: set[str] = field(default_factory=set)

    async def __call__(self, *, resume_from: Any = _ABSENT, **kwargs: Any) -> cli.SubtaskDrive:
        card_id = kwargs["card"].id
        self.resumed[card_id] = resume_from
        drive = await super().__call__(**kwargs)
        if resume_from is _ABSENT or resume_from is None or card_id in self.declined:
            return drive
        summary = replace(
            drive.summary, resumed_at=runtime_engine.pending_phase(resume_from)
        )
        return replace(drive, summary=summary)


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


@pytest.mark.git
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


@pytest.mark.git
def test_a_checkpoint_lookup_that_fails_escalates_that_subtask(project, monkeypatch):
    """Review Focus 4: the lookup runs inside the lane's `try`, so a broken
    store escalates the subtask it was for and never crashes the run."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]

    def broken(store, card_id):
        raise RuntimeError("checkpoints table unreadable")

    monkeypatch.setattr(runs, "continuable_checkpoint", broken)
    driver = CheckpointDriver()

    result = _run(project, shape["milestone"], driver)

    assert result["escalated"] is True
    assert result["subtask"] == a1
    assert result["detail"] == "RuntimeError: checkpoints table unreadable"
    assert driver.calls == []


# ── a relaunch after `am reset` starts the card fresh (card 522adfb5) ───────
#
# am-reset spec §3.6 / test 8, through the lane with `FakeDriver`. Git tier,
# not unit: the `project` fixture and the worktree under test need real git;
# the board is the in-memory FakeBoard, so no `brd` marker.

FIRST_PHASE = "worktree"
"""`TASK`'s first phase: where a walk handed no `resume_from` begins."""


def _continuable(project: Path, run_id: str, card_id: str) -> store_module.Checkpoint | None:
    """What a relaunch's lane would continue `card_id` from, read as the lane reads it."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        return runs.continuable_checkpoint(opened, card_id)
    finally:
        opened.close()


def _escalated_first_run(project: Path) -> tuple[str, str, str, Path]:
    """A one-story, one-subtask milestone run once and escalated at a1's review.

    Returns (milestone id, a1, the run id, a1's recorded worktree path). The
    run ended, so its lease is released and `am reset` finds nobody driving it.
    `FakeDriver` saves no checkpoint: each test plants the rows it needs.
    """
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first_driver = FakeDriver(outcomes={a1: ("review", "boom")})
    first = _run(project, shape["milestone"], first_driver)
    assert first["escalated"] is True, first
    return shape["milestone"], a1, first["run_id"], Path(first_driver.calls[0]["worktree"])


@pytest.mark.git
@pytest.mark.parametrize("reason", ["parked", "turn"])
@pytest.mark.parametrize(
    "worktree_present", [True, False], ids=["worktree-present", "worktree-removed"]
)
def test_a_relaunch_after_am_reset_starts_the_card_fresh_at_worktree(
    project, worktree_present, reason
):
    """am-reset spec test 8: a1's newest checkpoint is an open row of run X.
    Before the reset a relaunch would continue it; after `cli.reset_run(X)`
    the lane hands a1 no `resume_from`, so its walk begins at `worktree`,
    whether a1's worktree directory survived or was removed by hand. Review
    Focus 3: a crash's `turn` row closes the same way as a pause's `parked`."""
    milestone, a1, reset_id, wt = _escalated_first_run(project)
    planted = _plant(project, reset_id, a1, reason, queue=("review",))
    _git(project, "worktree", "add", "-b", _branch(project, a1), str(wt), "main")
    if not worktree_present:
        shutil.rmtree(wt)
    found = _continuable(project, reset_id, a1)
    assert found is not None
    assert (found.run_id, found.seq) == (reset_id, planted.seq)

    reset = cli.reset_run(reset_id, repo_dir=project)

    assert reset["status"] == "canceled"
    assert reset["already_canceled"] is False
    assert reset["cards"] == [
        {"card_id": a1, "workflow": task_workflow.TASK.name, "open_in": None}
    ]
    assert _continuable(project, reset_id, a1) is None
    driver = CheckpointDriver()

    result = _run(project, milestone, driver, clock=lambda: LATER)

    assert result["done"] is True, result
    assert result["run_id"] != reset_id
    assert result["completed"] == [a1]
    assert [call["card"] for call in driver.calls] == [a1]
    assert driver.resumed[a1] is _ABSENT
    assert driver.calls[0]["worktree"] == wt
    assert task_workflow.TASK.phases[0].name == FIRST_PHASE
    assert wt.is_dir() is worktree_present


@pytest.mark.git
def test_a_relaunch_after_am_reset_does_not_fall_back_to_an_older_runs_open_row(project):
    """Review Focus 1: an earlier run with no `runs` row (so not cancelled)
    holds an older open row of a1, and the reset run holds the newest. The
    newest row decides, so a1 is closed: `open_in` is null and the relaunch
    starts a1 fresh rather than continuing the older row."""
    milestone, a1, reset_id, _wt = _escalated_first_run(project)
    older = cli.mint_run_id(milestone, EARLIER)
    _plant(project, older, a1, "parked", minute=0)
    _plant(project, reset_id, a1, "parked", queue=("review",), minute=5)

    reset = cli.reset_run(reset_id, repo_dir=project)

    assert reset["cards"] == [
        {"card_id": a1, "workflow": task_workflow.TASK.name, "open_in": None}
    ]
    driver = CheckpointDriver()
    result = _run(project, milestone, driver, clock=lambda: LATER)
    assert result["done"] is True, result
    assert driver.resumed[a1] is _ABSENT


@pytest.mark.git
def test_a_relaunch_after_am_reset_continues_the_run_open_in_names(project):
    """Review Focus 2: another run (no `runs` row, so not cancelled) saved a
    newer open row of a1 than the reset run did. The reset reports that run
    in `open_in`, and the relaunch does continue a1 from exactly that row:
    the envelope tells the truth about what a relaunch adopts."""
    milestone, a1, reset_id, _wt = _escalated_first_run(project)
    _plant(project, reset_id, a1, "parked", queue=("review",), minute=0)
    other = cli.mint_run_id(milestone, EARLIER.replace(minute=30))
    newer = _plant(project, other, a1, "parked", queue=("implement",), minute=5)

    reset = cli.reset_run(reset_id, repo_dir=project)

    assert reset["cards"] == [
        {"card_id": a1, "workflow": task_workflow.TASK.name, "open_in": other}
    ]
    driver = CheckpointDriver()
    result = _run(project, milestone, driver, clock=lambda: LATER)
    assert result["done"] is True, result
    got = driver.resumed[a1]
    assert got is not _ABSENT
    assert (got.run_id, got.seq) == (other, newer.seq)


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


@pytest.mark.git
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


@pytest.mark.git
def test_a_merged_root_story_with_one_escalated_blocker_is_never_driven(project, fake_bases):
    """C is blocked by A and B. B finishes clean, then A escalates: C is never
    driven, no base is built, and C and c1 stay `pending`, as a
    single-blocker dependent of an escalated story does. A regression pin
    (spec §5 I1): today's early return in `lane` and grafo's own gate after
    the fix give the same outcome."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    b_returned = asyncio.Event()

    async def after_b_returned(stop: StopSignal | None) -> None:
        await _within(b_returned.wait(), "b1 to return")

    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: after_b_returned},
        returned={b1: b_returned},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert c1 not in [call["card"] for call in driver.calls]
    assert fake_bases.calls == []
    assert (result["escalated"], result["story"], result["subtask"]) == (True, story_a, a1)
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("pending", "pending")
    assert (statuses[story_b], statuses[b1]) == ("done", "done")


@pytest.mark.git
def test_a_merged_root_story_behind_an_edge_child_completes_instead_of_hanging(
    project, fake_bases
):
    """Spec §1.1 through `supervise`: D is blocked by A, C by B and D. B
    returns, then A after a wall-clock gap, so D is ready only after grafo
    shrank its pool. The run completes and C is built and driven."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "D": 1, "C": 1},
        blocked_by={"D": ["A"], "C": ["B", "D"]},
    )
    story_c, story_d = shape["stories"]["C"], shape["stories"]["D"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    (d1,) = shape["subtasks"]["D"]
    b_returned = asyncio.Event()

    async def after_b_returned_and_a_gap(stop: StopSignal | None) -> None:
        await _within(b_returned.wait(), "b1 to return")
        # The pool-shrink window is wall-clock, not ordering (spec §1.1): A
        # must return after B's worker shrank grafo's pool.
        await asyncio.sleep(0.05)

    driver = GatedDriver(gates={a1: after_b_returned_and_a_gap}, returned={b1: b_returned})

    result = _run_or_fail_if_it_hangs(
        lambda: _run(project, shape["milestone"], driver, max_concurrent=2)
    )

    assert result["done"] is True, result
    assert [call["story_id"] for call in fake_bases.calls] == [story_c]
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("done", "done")
    assert (statuses[story_d], statuses[d1]) == ("done", "done")


@pytest.mark.git
def test_a_given_runner_factory_reaches_the_base_builder(project, fake_bases):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})

    result = _run(project, shape["milestone"], FakeDriver(), runner_factory=_no_resolver)

    assert result["done"] is True, result
    (call,) = fake_bases.calls
    assert call["runner_factory"] is _no_resolver


@pytest.mark.git
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


@pytest.mark.git
def test_a_run_with_no_merged_root_builds_no_base_and_reports_no_bases(project, fake_bases):
    """Spec: a lone-blocker story stays the fast path, and the key is absent
    when no base was built."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"B": ["A"]})

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert fake_bases.calls == []
    assert "bases" not in result


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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
    root: Path,
    run_id: str = RESUME_RUN_ID,
    *,
    workflow: str = "milestone",
    status: str = "escalated",
    base_branch: str = "main",
) -> None:
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=root,
                base_branch=base_branch,
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


@pytest.mark.parametrize("status", ["cancelled", "canceled"], ids=["cancelled", "canceled"])
def test_resume_refuses_run_canceled_in_either_spelling(tmp_path, monkeypatch, status):
    """C9: unknown run, then wrong workflow, then canceled in either spelling --
    the earlier refusals still win for a run that is also canceled."""
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root, status=status)

    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)

    assert str(caught.value) == (
        f"run {RESUME_RUN_ID} was canceled; start new work with am run --milestone"
    )
    task_run = "20260924T120000Z-00000008"
    _record_resume_run(root, task_run, workflow="task", status=status)
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
    pytest.fail("a resume consulted the lenient relaunch lookup runs.continuable_checkpoint")


@pytest.mark.git
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
    monkeypatch.setattr(runs, "continuable_checkpoint", _never_consulted)
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_a_run_with_no_control_integrates_as_before(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]

    result = _run(project, shape["milestone"], FakeDriver(), control_interval=0)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result["done"] is True, result
    assert not {"paused", "canceled", "cancelled", "control", "escalated"} & set(result)
    assert len(integrate_recorder.calls) == 1
    assert _statuses(_load(project, run_id)) == {"run": "done", story_a: "done", a1: "done"}
    assert _controls(project, run_id) == []


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
def test_an_integrate_that_raises_still_releases_the_lease(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    integrate_recorder.outcome = RuntimeError("integrate blew up")

    with pytest.raises(RuntimeError, match="integrate blew up"):
        _run(project, shape["milestone"], FakeDriver(), control_interval=0)

    assert _lease(project, run_id) is None
    assert _load(project, run_id).status == "started"


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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


@pytest.mark.git
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
        "canceled": True,
        "run_id": run_id,
        "stopped": [{"story": story_a, "subtask": a1, "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "canceled",
        story_a: "stopped",
        a1: "stopped",
        a2: "pending",
    }
    assert integrate_recorder.calls == []


def _run_upserts(run_id: str) -> list[str]:
    """The raw `run_upsert` lines of `run_id`'s journal, in order."""
    raw = (paths.run_dir(run_id) / "journal.jsonl").read_text(encoding="utf-8")
    return [line for line in raw.splitlines() if json.loads(line)["event"] == "run_upsert"]


def _raw_run_status(project: Path, run_id: str) -> str:
    """`runs.status` as stored, before any reader normalises it."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, run_id
    return row["status"]


@pytest.mark.git
def test_milestone_cancel_journals_canceled(project, integrate_recorder):
    """A canceled milestone run journals its final `run_upsert` with status
    `canceled`, never the legacy spelling, and stores `canceled` in its row."""
    shape = _milestone(project, {"A": 2})
    a1, _ = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "cancel")})

    _run(project, shape["milestone"], driver, control_interval=0)

    upserts = _run_upserts(run_id)
    assert upserts, run_id
    assert json.loads(upserts[-1])["payload"]["status"] == "canceled", upserts[-1]
    assert not [line for line in upserts if "cancelled" in line], upserts
    assert _raw_run_status(project, run_id) == "canceled"


@pytest.mark.git
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

    assert result["canceled"] is True, result
    assert "paused" not in result and "resume" not in result
    assert _load(project, run_id).status == "canceled"
    assert json.loads(_run_upserts(run_id)[-1])["payload"]["status"] == "canceled"
    assert [(row.command, row.handled_at is not None) for row in _controls(project, run_id)] == [
        ("pause", True),
        ("cancel", True),
    ]
    assert integrate_recorder.calls == []


@pytest.mark.git
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


@pytest.mark.git
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
        "canceled": True,
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
        "run": "canceled",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
    }
    assert integrate_recorder.calls == []


@pytest.mark.git
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


@pytest.mark.git
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


# ── claims: a milestone run's milestone, cards and integration branch (card 1a3fdd73) ──


HERE = socket.gethostname()
"""This host, as `control.Lease` records it."""

OTHER_RUN_ID = "20260930T080000Z-a1b2c3d4"
"""Another run, driven by another `am` process, that holds a claim."""


def _plant_lease(
    project: Path,
    *,
    run_id: str,
    token: str,
    pid: int,
    heartbeat_at: datetime,
    claims: tuple[str, ...] = (),
) -> None:
    """A `run_leases` row and its `run_claims`, as another process's `Lease` would leave them.

    Written over a second `open_db` connection inside `store.immediate`, on
    this host, window open. Live by C2 when `pid` is alive and `heartbeat_at`
    is fresh; dead when `pid` is `_reaped_pid()`.
    """
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        with store_module.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, 1)"
                " ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,"
                " pid=excluded.pid, host=excluded.host, acquired_at=excluded.acquired_at,"
                " heartbeat_at=excluded.heartbeat_at, accepting=1",
                (run_id, token, pid, HERE, heartbeat_at.isoformat(), heartbeat_at.isoformat()),
            )
            for key in claims:
                conn.execute(
                    "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " run_id=excluded.run_id, token=excluded.token,"
                    " claimed_at=excluded.claimed_at",
                    (key, run_id, token, heartbeat_at.isoformat()),
                )
    finally:
        conn.close()


def _reaped_pid() -> int:
    """The pid of a child that has exited and been waited for: dead by `pid_alive`."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _claim_rows(project: Path) -> list[tuple[str, str, str]]:
    """Every `run_claims` row as `(key, run_id, token)`, in key order."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [
            (row["key"], row["run_id"], row["token"])
            for row in conn.execute("SELECT key, run_id, token FROM run_claims ORDER BY key")
        ]
    finally:
        conn.close()


def _held_keys(project: Path, run_id: str) -> list[str]:
    """The keys `run_id`'s current lease holds, in key order, read as `am status` would."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        lease = store_module.read_lease(conn, run_id)
        if lease is None:
            return []
        return [claim.key for claim in store_module.held_claims(conn, run_id, lease.token)]
    finally:
        conn.close()


def _run_dirs() -> list[Path]:
    runs_root = paths.data_dir() / "runs"
    return sorted(runs_root.iterdir()) if runs_root.exists() else []


def _worktree_count(project: Path) -> int:
    return sum(
        1
        for line in _git(project, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    )


def _forbidden(name: str) -> Callable[..., Any]:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        pytest.fail(f"{name} ran before the claim refusal")

    return refuse


def _expected_claims(milestone: str, cards: list[str]) -> list[str]:
    """`milestone_claims`' keys spelled out, in `held_claims`' key order."""
    return sorted(
        [f"card:{milestone}", *(f"card:{card}" for card in cards), f"branch:{INTEGRATION_BRANCH}"]
    )


def _recording(
    project: Path, run_id: str, during: list[list[str]], then: Gate | None = None
) -> Gate:
    """A gate that records the run's held keys mid-subtask, then runs `then`."""

    async def gate(stop: StopSignal | None) -> None:
        during.append(_held_keys(project, run_id))
        if then is not None:
            await then(stop)

    return gate


@pytest.mark.parametrize("claimed", ["milestone", "subtask"])
@pytest.mark.git
def test_a_milestone_run_is_refused_while_a_live_run_claims_one_of_its_cards(
    project, monkeypatch, claimed
):
    """X5/X6: the preflight refuses before git is refreshed and before the
    store opens, so nothing is fetched, pruned, recorded or made. `milestone`
    is another milestone run of M under another prefix: only `card:M` is shared."""
    shape = _milestone(project, {"A": 2})
    _a1, a2 = shape["subtasks"]["A"]
    card = shape["milestone"] if claimed == "milestone" else a2
    key = f"card:{card}"
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _forbidden("refresh_git"))
    driver = FakeDriver()

    with pytest.raises(cli.ClaimedError) as caught:
        _run(project, shape["milestone"], driver)

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert str(caught.value).startswith(f"card {card} is being driven by run {OTHER_RUN_ID}")
    assert driver.calls == []
    assert _run_ids(project) == []
    assert _run_dirs() == []
    assert _worktree_count(project) == 1
    assert _local_branches(project) == ["main"]
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@pytest.mark.git
def test_a_milestone_run_is_refused_while_a_live_run_claims_its_integration_branch(
    project, monkeypatch
):
    shape = _milestone(project, {"A": 1})
    key = f"branch:{INTEGRATION_BRANCH}"
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _forbidden("refresh_git"))
    driver = FakeDriver()

    with pytest.raises(cli.ClaimedError) as caught:
        _run(project, shape["milestone"], driver)

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert str(caught.value).startswith(
        f"branch {INTEGRATION_BRANCH} is being driven by run {OTHER_RUN_ID}"
    )
    assert driver.calls == []
    assert _run_ids(project) == []
    assert _run_dirs() == []
    assert _worktree_count(project) == 1
    assert _local_branches(project) == ["main"]
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@pytest.mark.git
def test_a_dead_claim_does_not_refuse_a_milestone_run(project):
    """A dead holder's claims are taken over by `take_lease`, then released
    with this run's lease; the dead holder's own lease row is left alone."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="dead-life",
        pid=_reaped_pid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(f"card:{a1}", f"branch:{INTEGRATION_BRANCH}"),
    )

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert _claim_rows(project) == []
    other = _lease(project, OTHER_RUN_ID)
    assert other is not None and other.token == "dead-life"


@pytest.mark.git
def test_a_milestone_run_holds_its_claims_while_driving(project):
    """Mid-run the lease holds exactly `milestone_claims`: the milestone, the
    remaining subtasks (a1 is done on the board, so it is not claimed) and
    the integration branch. A fresh run never reports `took_over`."""
    shape = _milestone(project, {"A": 2, "B": 1})
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    rollup.set_status(a1, "done", repo_dir=project)
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    during: list[list[str]] = []
    driver = GatedDriver(
        gates={
            a2: _recording(project, run_id, during),
            b1: _recording(project, run_id, during),
        }
    )

    result = _run(project, shape["milestone"], driver)

    assert result["done"] is True, result
    assert "took_over" not in result
    expected = _expected_claims(shape["milestone"], [a2, b1])
    assert during == [expected, expected]
    assert _claim_rows(project) == []


@pytest.mark.parametrize(
    "exit_by", ["done", "escalated", "paused", "cancelled", "killed", "integrate_raises"]
)
@pytest.mark.git
def test_a_milestone_run_releases_its_claims_on_every_exit(project, integrate_recorder, exit_by):
    """X5: the claims are held mid-run and gone, with the lease, however the
    run ends -- a lane's `BaseException` and a raising Integrate included."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    during: list[list[str]] = []
    outcomes: dict[str, Any] = {}
    then: Gate | None = None
    if exit_by == "escalated":
        outcomes[a1] = ("review", "boom")
    elif exit_by == "paused":
        then = _send_then_await_stop(project, run_id, "pause")
    elif exit_by == "cancelled":
        then = _send_then_await_stop(project, run_id, "cancel")
    elif exit_by == "killed":
        outcomes[a1] = _LaneKilled("the manager died mid-lane")
    elif exit_by == "integrate_raises":
        integrate_recorder.outcome = RuntimeError("integrate blew up")
    driver = GatedDriver(outcomes=outcomes, gates={a1: _recording(project, run_id, during, then)})

    def go() -> dict[str, Any]:
        return _run(project, shape["milestone"], driver, control_interval=0)

    if exit_by == "killed":
        with pytest.raises(_LaneKilled):
            _run_or_fail_if_it_hangs(go)
    elif exit_by == "integrate_raises":
        with pytest.raises(RuntimeError, match="integrate blew up"):
            go()
    else:
        go()

    assert during == [_expected_claims(shape["milestone"], [a1])]
    assert _claim_rows(project) == []
    assert _lease(project, run_id) is None


@pytest.mark.git
def test_a_milestone_resume_is_refused_before_the_store_opens_while_a_live_run_claims_its_card(
    project, monkeypatch
):
    """Review Focus 1: another live run took a1 since the interrupt. The
    resume refuses read-only, before `Store.open` and before git."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    key = f"card:{a1}"
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(store_module.Store, "open", _forbidden("Store.open"))
    monkeypatch.setattr(orchestrate, "refresh_git", _forbidden("refresh_git"))
    driver = FakeDriver()

    with pytest.raises(cli.ClaimedError) as caught:
        _resume(project, run_id, driver)

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert driver.calls == []
    assert _load(project, run_id).status == "escalated"
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@pytest.mark.git
def test_refresh_git_and_first_write_run_inside_the_lease_on_resume(project, monkeypatch):
    """X5: on a resume the fetch/prune and the first journal line both happen
    under this life's lease, which already holds every claim."""
    shape = _milestone(project, {"A": 1, "B": 1})
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    seen_by_git: list[store_module.LeaseRow | None] = []
    real_refresh = orchestrate.refresh_git

    def refresh_spy(root: Path) -> None:
        seen_by_git.append(_lease(project, run_id))
        real_refresh(root)

    first_write: list[tuple[str | None, list[str]]] = []
    real_record_run = store_module.Store.record_run

    def record_spy(self, run):
        if not first_write:
            token = self._token
            held = (
                []
                if token is None
                else [
                    claim.key
                    for claim in store_module.held_claims(self.connection, self.run_id, token)
                ]
            )
            first_write.append((token, held))
        return real_record_run(self, run)

    monkeypatch.setattr(orchestrate, "refresh_git", refresh_spy)
    monkeypatch.setattr(store_module.Store, "record_run", record_spy)

    result = _resume(project, run_id, FakeDriver())

    assert result["done"] is True, result
    ((token, held),) = first_write
    assert token is not None
    assert held == _expected_claims(shape["milestone"], [a1, b1])
    (git_saw,) = seen_by_git
    assert git_saw is not None, "git was refreshed before the resume took its lease"
    assert (git_saw.token, git_saw.pid) == (token, os.getpid())


@pytest.mark.parametrize("outcome", ["done", "escalated"])
@pytest.mark.git
def test_a_milestone_resume_excludes_its_own_claims_and_reports_took_over(project, outcome):
    """The interrupted life's lease is dead and its own claims are still
    planted: neither refuses, the resume takes them over, and every payload
    shape names the dead holder under `took_over`."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    dead = _reaped_pid()
    beat = datetime.now(timezone.utc)
    _plant_lease(
        project,
        run_id=run_id,
        token="crashed-life",
        pid=dead,
        heartbeat_at=beat,
        claims=tuple(_expected_claims(shape["milestone"], [a1])),
    )
    driver = FakeDriver(outcomes={} if outcome == "done" else {a1: ("review", "still")})

    result = _resume(project, run_id, driver)

    assert result.get(outcome) is True, result
    assert result["resumed"] is True
    assert result["took_over"] == {
        "pid": dead,
        "host": HERE,
        "heartbeat_at": beat.isoformat(),
    }
    assert _claim_rows(project) == []
    assert _lease(project, run_id) is None


@pytest.mark.git
def test_a_still_live_milestone_run_refuses_its_resume_before_touching_git(project, monkeypatch):
    """Review Focus 2 (C10): the run's own lease is live and holds its own
    claims. The preflight skips the run's own rows (so no `ClaimedError`
    about them), `run_lease` refuses with `RunIsLiveError`, and git, the
    journal and the rows are untouched."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    own = _expected_claims(shape["milestone"], [a1])
    _plant_lease(
        project,
        run_id=run_id,
        token="still-running",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=tuple(own),
    )
    git_calls = _record_git(monkeypatch)
    before = (_runs_tree(), _statuses(_load(project, run_id)))
    driver = FakeDriver()

    with pytest.raises(cli.RunIsLiveError):
        _resume(project, run_id, driver)

    assert driver.calls == []
    assert git_calls == []
    assert (_runs_tree(), _statuses(_load(project, run_id))) == before
    assert _claim_rows(project) == [(key, run_id, "still-running") for key in own]


# ── board comments (card 65ed3c70) ──────────────────────────────────────────


def _comments(project: Path, card_id: str) -> list[board.BoardComment]:
    """`card_id`'s comments on the temporary board, oldest first."""
    return board.comment_list(card_id, repo_dir=project)


def _keys(found: list[board.BoardComment]) -> list[str]:
    """Each comment's `am-key:` value, read off its last line, in board order."""
    return [
        comment.body.rstrip().rsplit("\n", 1)[-1].removeprefix("am-key: ")
        for comment in found
    ]


def _comment_states(project: Path) -> list[tuple[str, str]]:
    """Every outbox row as `(key, state)`, in insertion order."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [
            (row["key"], row["state"])
            for row in conn.execute("SELECT key, state FROM board_comments ORDER BY rowid")
        ]
    finally:
        conn.close()


@pytest.mark.git
def test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story(project):
    """Spec test 1, subtask half; Review Focus 2: two lanes finish at once,
    yet each subtask gets exactly one done comment and nothing stays pending."""
    shape = _milestone(project, {"A": 2, "B": 1})
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]

    result = _run(project, shape["milestone"], FakeDriver(), max_concurrent=2)

    assert result["done"] is True, result
    run_id = result["run_id"]
    for subtask in (a1, a2, b1):
        found = _comments(project, subtask)
        assert _keys(found) == [f"{run_id}/{subtask}/done"], subtask
        (comment,) = found
        assert comment.author == "am"
        assert f"branch: {_branch(project, subtask)}" in comment.body
        assert "(resumed at" not in comment.body
    for story in shape["stories"].values():
        assert _comments(project, story) == [], story
    assert [state for _key, state in _comment_states(project) if "/done" in _key] == [
        "posted",
        "posted",
        "posted",
    ]
    assert result["warnings"] == []


@pytest.mark.git
def test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone(project):
    """Spec test 1, milestone half; Review Focus 5: `total` reaches the comment only."""
    shape = _milestone(project, {"A": 2, "B": 1})
    milestone = shape["milestone"]

    result = _run(project, milestone, FakeDriver())

    assert result["done"] is True, result
    run_id = result["run_id"]
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    assert (key, "posted") in _comment_states(project)
    (comment,) = found
    assert comment.author == "am"
    assert "done: 3 of 3" in comment.body
    assert f"integrated: {INTEGRATION_BRANCH}" in comment.body
    assert "total" not in result
    assert result["warnings"] == []


@pytest.mark.git
def test_an_integrate_escalation_leaves_an_integrate_failed_run_end_comment(
    project, integrate_recorder
):
    """Spec test 6."""
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    milestone, story_b = shape["milestone"], shape["stories"]["B"]
    integrate_recorder.outcome = integration.IntegrateEscalation(
        story=story_b, files=["shared.txt"], detail="the resolver did not finish"
    )

    result = _run(project, milestone, FakeDriver())

    assert result["escalated"] is True, result
    run_id = result["run_id"]
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    assert (key, "posted") in _comment_states(project)
    assert (
        f"integrate failed at integrate on [[{story_b}]]: the resolver did not finish"
        in found[0].body
    )
    assert "total" not in result
    assert result["warnings"] == []


@pytest.mark.git
def test_a_lane_escalation_leaves_an_escalated_run_end_comment_naming_the_parked(project):
    """Spec test 2, milestone half: the run-end names the escalated subtask and
    the parked one, and is posted (its resume hint is `test_comments.py`'s job)."""
    shape = _milestone(project, {"A": 1, "B": 2})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    b1, _b2 = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, milestone, driver, max_concurrent=2)

    assert result["escalated"] is True, result
    run_id = result["run_id"]
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    assert (key, "posted") in _comment_states(project)
    body = found[0].body
    assert f"escalated: [[{a1}]] at review" in body
    assert f"parked: [[{b1}]]" in body


@dataclass
class WithResults:
    """Wraps a fake driver and merges canned phase results into a card's
    summary, as the real walk returns them, so `agent_reason` has its field."""

    inner: Any
    results: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def __call__(self, **kwargs: Any) -> cli.SubtaskDrive:
        drive = await self.inner(**kwargs)
        extra = self.results.get(kwargs["card"].id)
        if extra is None:
            return drive
        summary = replace(drive.summary, results={**drive.summary.results, **extra})
        return replace(drive, summary=summary)


@pytest.mark.git
def test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone(project):
    """Spec test 2: the escalated subtask gets phase, detail and the agent's
    one reason field (quoted, `[[` broken); parked b1, pending c1 and every
    story get nothing."""
    shape = _milestone(project, {"A": 1, "B": 2, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = asyncio.Barrier(2)
    driver = WithResults(
        GatedDriver(
            outcomes={a1: ("review", "reviewer found a blocker")},
            gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
        ),
        results={
            a1: {"review": {"unresolved_blockers": ["the [[parser]] still drops input", "no test"]}}
        },
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["escalated"] is True, result
    run_id = result["run_id"]
    found = _comments(project, a1)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{a1}/escalated:")
    assert (key, "posted") in _comment_states(project)
    body = found[0].body
    assert found[0].author == "am"
    assert "phase: review" in body
    assert "detail: reviewer found a blocker" in body
    assert 'reason: "the [ [parser]] still drops input; no test"' in body
    for quiet in (story_a, story_b, b1, b2, story_c, c1):
        assert _comments(project, quiet) == [], quiet
    assert len(_comments(project, shape["milestone"])) == 1
    assert result["warnings"] == []


@pytest.mark.git
def test_a_second_life_escalating_at_the_same_phase_adds_a_second_escalation_comment(project):
    """Spec test 4 (Review Focus 3 of the card); Review Focus 4 here: no
    review result at all gives no `reason:` line and no crash."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, milestone, FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]

    again = _resume(project, run_id, FakeDriver(outcomes={a1: ("review", "still")}))

    assert again["escalated"] is True, again
    found = _comments(project, a1)
    keys = _keys(found)
    assert len(keys) == 2 and len(set(keys)) == 2, keys
    assert all(key.startswith(f"{run_id}/{a1}/escalated:") for key in keys)
    assert "detail: boom" in found[0].body
    assert "detail: still" in found[1].body
    assert all("reason:" not in comment.body for comment in found)
    milestone_keys = _keys(_comments(project, milestone))
    assert len(milestone_keys) == 2 and len(set(milestone_keys)) == 2, milestone_keys


@pytest.mark.git
def test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review(project):
    """Spec test 3: the first life's escalation stays; the second life adds
    `done` with `(resumed at review)` and its own, distinct run-end."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, milestone, FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",))
    driver = CheckpointDriver()

    result = _resume(project, run_id, driver)

    assert result["done"] is True, result
    assert driver.resumed[a1] is not _ABSENT
    found = _comments(project, a1)
    keys = _keys(found)
    assert len(keys) == 2, keys
    assert keys[0].startswith(f"{run_id}/{a1}/escalated:")
    assert keys[1] == f"{run_id}/{a1}/done"
    assert "(resumed at review)" in found[1].body.split("\n")
    assert (keys[1], "posted") in _comment_states(project)
    assert first["escalated"] is True, first
    milestone_keys = _keys(_comments(project, milestone))
    assert len(milestone_keys) == 2 and len(set(milestone_keys)) == 2, milestone_keys
    assert all(key.startswith(f"{run_id}/{milestone}/run-end:") for key in milestone_keys)
    run_end_rows = [row for row in _comment_states(project) if "/run-end:" in row[0]]
    assert run_end_rows == [(key, "posted") for key in milestone_keys]


@pytest.mark.git
def test_a_lane_whose_walk_declined_its_checkpoint_posts_done_without_resumed_at(project):
    """Resume worktree re-ensure §3.6: the lane hands a1 a checkpoint, but the
    walk declined it and started over (`summary.resumed_at` is None), so the
    `done` comment names no resume point. The lane reads the summary, not the
    checkpoint it handed in."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, milestone, FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",))
    driver = CheckpointDriver(declined={a1})

    result = _resume(project, run_id, driver)

    assert result["done"] is True, result
    assert driver.resumed[a1] is not _ABSENT
    found = _comments(project, a1)
    keys = _keys(found)
    assert keys[-1] == f"{run_id}/{a1}/done", keys
    assert "(resumed at" not in found[-1].body


@pytest.mark.git
def test_a_failed_merged_base_comments_on_its_story(project, fake_bases):
    """Spec test 5, `lane` case: one `base-failed` comment on C, none on c1."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (c1,) = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    fake_bases.outcomes[story_c] = bases.BaseFailed("conflict nobody could resolve")

    result = _run(project, shape["milestone"], FakeDriver())

    assert (result["story"], result["failed_phase"]) == (story_c, "base"), result
    run_id = result["run_id"]
    found = _comments(project, story_c)
    assert _keys(found) == [f"{run_id}/{story_c}/base-failed"]
    assert (f"{run_id}/{story_c}/base-failed", "posted") in _comment_states(project)
    body = found[0].body
    assert found[0].author == "am"
    assert f"base branch: {root_plan.branch}" in body
    assert "detail: conflict nobody could resolve" in body
    assert _comments(project, c1) == []
    assert result["warnings"] == []


@pytest.mark.git
def test_a_subtask_less_storys_failed_base_comments_on_that_story(project, fake_bases):
    """Spec test 5, `base_only_lane` case: J has no store row, yet its story
    card gets the comment."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_j = shape["stories"]["J"]
    root_plan = _root_plan(project, shape["milestone"], story_j)
    fake_bases.outcomes[story_j] = bases.BaseFailed("J's base broke")

    result = _run(project, shape["milestone"], FakeDriver())

    assert (result["story"], result["failed_phase"]) == (story_j, "base"), result
    run_id = result["run_id"]
    found = _comments(project, story_j)
    assert _keys(found) == [f"{run_id}/{story_j}/base-failed"]
    assert (f"{run_id}/{story_j}/base-failed", "posted") in _comment_states(project)
    assert f"base branch: {root_plan.branch}" in found[0].body
    assert "detail: J's base broke" in found[0].body
    assert result["warnings"] == []


@pytest.mark.git
def test_a_base_whose_resolver_was_stopped_gets_no_base_failed_comment(project, fake_bases):
    """Spec test 5, stopped case: `BaseFailed(stopped=True)` is a park, not a failure."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 1}, blocked_by={"C": ["A", "B"]}
    )
    story_c = shape["stories"]["C"]
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

    assert result["stopped"] == [{"story": story_c, "subtask": None, "before_phase": None}]
    assert _comments(project, story_c) == []


def _board_down(monkeypatch) -> dict[str, bool]:
    """Fail `board.comment_list` -- the first `brd` call a flush makes per
    row -- while `state["down"]`; every other board call stays real."""
    state = {"down": True}
    real = board.comment_list

    def flaky(card_id: str, *, repo_dir: Path | None = None) -> list[board.BoardComment]:
        if state["down"]:
            raise board.BoardError(
                "brd is down", argv=["brd", "comment", "list", card_id], exit_code=1
            )
        return real(card_id, repo_dir=repo_dir)

    monkeypatch.setattr(board, "comment_list", flaky)
    return state


@pytest.mark.git
def test_a_board_that_is_down_changes_only_warnings_and_a_relaunch_posts_the_rows(
    project, monkeypatch
):
    """Spec test 7; Review Focus 1 and 3: the run still ends done with its usual
    keys, each failed row is one warning and stays pending, and a relaunch
    under a new run id posts both before anything else, exactly once."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    state = _board_down(monkeypatch)

    first = _run(project, milestone, BranchingDriver())

    run_id = first["run_id"]
    assert first["done"] is True, first
    assert first["completed"] == [a1]
    assert set(first) == {"done", "run_id", "levels", "completed", "tips", "warnings", "integrated"}
    assert _load(project, run_id).status == "done"
    assert len(first["warnings"]) == 2, first["warnings"]
    assert f"board comment {run_id}/{a1}/done on card {a1} not posted" in first["warnings"][0]
    assert f"board comment {run_id}/{milestone}/run-end:" in first["warnings"][1]
    assert all("will retry" in warning for warning in first["warnings"])
    assert [row_state for _key, row_state in _comment_states(project)] == ["pending", "pending"]

    state["down"] = False
    second = _run(project, milestone, BranchingDriver(), clock=lambda: LATER)

    assert second["done"] is True, second
    assert second["warnings"] == []
    assert _keys(_comments(project, a1)) == [f"{run_id}/{a1}/done"]
    milestone_keys = _keys(_comments(project, milestone))
    assert len(milestone_keys) == 2, milestone_keys
    assert milestone_keys[0].startswith(f"{run_id}/{milestone}/run-end:")
    assert milestone_keys[1].startswith(f"{second['run_id']}/{milestone}/run-end:")
    assert all(row_state == "posted" for _key, row_state in _comment_states(project))


@pytest.mark.git
def test_a_start_flush_that_fails_reports_its_warnings_and_the_run_goes_on(
    project, monkeypatch
):
    """Board-comments B7/B8: a relaunch whose start flush meets a board that
    is still down reports the earlier run's unposted rows as warnings and
    still ends done. a1 is not driven again, so only the start flush can
    report a warning on its row."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    _board_down(monkeypatch)
    first = _run(project, milestone, BranchingDriver())
    run_id = first["run_id"]

    second = _run(project, milestone, BranchingDriver(), clock=lambda: LATER)

    assert second["done"] is True, second
    assert second["completed"] == []
    on_a1 = [w for w in second["warnings"] if f"board comment {run_id}/{a1}/done " in w]
    assert len(on_a1) == 1, second["warnings"]
    assert "not posted" in on_a1[0] and "brd is down" in on_a1[0]


@pytest.mark.git
def test_an_escalation_comment_the_board_refuses_is_a_warning_on_the_run(
    project, monkeypatch
):
    """Board-comments B8: the escalated subtask's unposted comment surfaces
    in the escalated report's `warnings`, and the run still escalates."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    _board_down(monkeypatch)

    result = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))

    assert result["escalated"] is True, result
    run_id = result["run_id"]
    assert len(
        [w for w in result["warnings"] if f"board comment {run_id}/{a1}/escalated:" in w]
    ) == 1, result["warnings"]


@pytest.mark.git
def test_a_failed_base_comment_the_board_refuses_is_a_warning_on_the_run(
    project, fake_bases, monkeypatch
):
    """Board-comments B8, `lane` case: the story's unposted base-failed
    comment surfaces in the escalated report's `warnings`."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    fake_bases.outcomes[story_c] = bases.BaseFailed("conflict nobody could resolve")
    _board_down(monkeypatch)

    result = _run(project, shape["milestone"], FakeDriver())

    assert (result["story"], result["failed_phase"]) == (story_c, "base"), result
    run_id = result["run_id"]
    assert len(
        [w for w in result["warnings"] if f"board comment {run_id}/{story_c}/base-failed " in w]
    ) == 1, result["warnings"]


@pytest.mark.git
def test_a_subtask_less_storys_refused_base_comment_is_a_warning_on_the_run(
    project, fake_bases, monkeypatch
):
    """Board-comments B8, `base_only_lane` case: J's unposted base-failed
    comment surfaces in the escalated report's `warnings`."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_j = shape["stories"]["J"]
    fake_bases.outcomes[story_j] = bases.BaseFailed("J's base broke")
    _board_down(monkeypatch)

    result = _run(project, shape["milestone"], FakeDriver())

    assert (result["story"], result["failed_phase"]) == (story_j, "base"), result
    run_id = result["run_id"]
    assert len(
        [w for w in result["warnings"] if f"board comment {run_id}/{story_j}/base-failed " in w]
    ) == 1, result["warnings"]


# ── board comments on cancel and pause (card 5d9a875f) ─────────────────────


@pytest.mark.git
def test_a_cancel_comments_each_parked_subtask_and_the_milestone(project):
    """Spec test 1: a2 and b1 park under the cancel and each get one
    `cancelled` comment; a1 keeps only its done comment; stories get none;
    the milestone gets one `cancelled` run-end. Comments go out in the
    order `stopped` lists the parked lanes (wave order)."""
    shape = _milestone(project, {"A": 2, "B": 1})
    milestone = shape["milestone"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_then_cancel(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a2 and b1 in flight together")
        _send(project, run_id, "cancel")
        await _await_stop(stop)

    driver = GatedDriver(gates={a2: meet_then_cancel, b1: _meet_then_await_stop(pair)})

    result = _run(project, milestone, driver, max_concurrent=2, control_interval=0)

    assert result["canceled"] is True, result
    assert _load(project, run_id).status == "canceled"
    parked = [row["subtask"] for row in result["stopped"]]
    assert sorted(parked) == sorted([a2, b1]), result
    for subtask in (a2, b1):
        found = _comments(project, subtask)
        assert _keys(found) == [f"{run_id}/{subtask}/cancelled"], subtask
        (comment,) = found
        assert comment.author == "am"
        assert "stopped before: implement" in comment.body
        assert f"branch: {_branch(project, subtask)}" in comment.body
    assert _keys(_comments(project, a1)) == [f"{run_id}/{a1}/done"]
    for story in shape["stories"].values():
        assert _comments(project, story) == [], story
    on_milestone = _comments(project, milestone)
    (key,) = _keys(on_milestone)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    body = on_milestone[0].body
    assert "done: 1 of 3" in body
    assert "parked: " + ", ".join(f"[[{card}]]" for card in parked) in body
    cancelled_keys = [key for key, _state in _comment_states(project) if key.endswith("/cancelled")]
    assert cancelled_keys == [f"{run_id}/{card}/cancelled" for card in parked]
    assert all(state == "posted" for _key, state in _comment_states(project))
    assert "total" not in result
    assert result["warnings"] == []


@pytest.mark.git
def test_a_cancel_with_an_escalated_lane_comments_only_the_parked_subtask_as_cancelled(project):
    """Review Focus 3: a1's lane already posted its escalation; the cancel
    adds no `cancelled` comment for it, only for parked b1, and the run-end
    names both."""
    shape = _milestone(project, {"A": 1, "B": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_cancel_then_escalate(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a1 and b1 in flight together")
        _send(project, run_id, "cancel")
        await _await_stop(stop)

    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: meet_cancel_then_escalate, b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, milestone, driver, max_concurrent=2, control_interval=0)

    assert result["canceled"] is True, result
    a1_keys = _keys(_comments(project, a1))
    assert len(a1_keys) == 1 and a1_keys[0].startswith(f"{run_id}/{a1}/escalated:"), a1_keys
    assert _keys(_comments(project, b1)) == [f"{run_id}/{b1}/cancelled"]
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    states = _comment_states(project)
    assert (f"{run_id}/{b1}/cancelled", "posted") in states
    assert (key, "posted") in states
    assert f"escalated: [[{a1}]] at review" in found[0].body
    assert f"parked: [[{b1}]]" in found[0].body


@pytest.mark.git
def test_a_cancel_on_a_lane_waiting_for_a_slot_comments_its_subtask_without_a_phase(project):
    """Review Focus 1: `queued` never reached the driver, so its stopped
    outcome has no `before_phase`; q1 still gets one `cancelled` comment,
    with its branch, posted, and no `stopped before:` line (the relaunch
    hint text is `test_comments.py`'s job)."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    milestone = shape["milestone"]
    (first, second, queued) = _census_levels(project, milestone)[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,), (q1,) = subtasks[first], subtasks[second], subtasks[queued]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_then_cancel(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "both slotted lanes in flight")
        _send(project, run_id, "cancel")
        await _await_stop(stop)

    driver = GatedDriver(gates={f1: meet_then_cancel, s1: _meet_then_await_stop(pair)})

    result = _run(project, milestone, driver, max_concurrent=2, control_interval=0)

    assert result["canceled"] is True, result
    assert {"story": queued, "subtask": q1, "before_phase": None} in result["stopped"]
    found = _comments(project, q1)
    assert _keys(found) == [f"{run_id}/{q1}/cancelled"]
    body = found[0].body
    assert "stopped before:" not in body
    assert f"branch: {_branch(project, q1)}" in body
    assert (f"{run_id}/{q1}/cancelled", "posted") in _comment_states(project)
    for subtask in (f1, s1):
        assert "stopped before: implement" in _comments(project, subtask)[0].body


@pytest.mark.git
def test_a_cancel_while_a_base_builds_comments_no_story_and_still_ends_the_run(
    project, fake_bases
):
    """Review Focus 2: C's lane parks while its merged base builds, so its
    stopped outcome names no subtask. Neither C nor c1 gets a comment, and
    the milestone still gets its `cancelled` run-end."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    milestone = shape["milestone"]
    story_c = shape["stories"]["C"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    fake_bases.gates[story_c] = _send_then_await_stop(project, run_id, "cancel")
    fake_bases.outcomes[story_c] = bases.BaseFailed("the resolver was stopped", stopped=True)

    result = _run(project, milestone, FakeDriver(), control_interval=0)

    assert result["canceled"] is True, result
    assert result["stopped"] == [{"story": story_c, "subtask": None, "before_phase": None}]
    assert _comments(project, story_c) == []
    assert _comments(project, c1) == []
    assert _keys(_comments(project, a1)) == [f"{run_id}/{a1}/done"]
    assert _keys(_comments(project, b1)) == [f"{run_id}/{b1}/done"]
    (key,) = _keys(_comments(project, milestone))
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    assert (key, "posted") in _comment_states(project)
    assert result["warnings"] == []


@pytest.mark.git
def test_a_cancel_whose_comments_the_board_refuses_is_still_cancelled_with_warnings(
    project, monkeypatch
):
    """Spec test 3 (error path B8): the board refuses both the parked
    subtask's and the milestone's comments. The run is still recorded
    `cancelled`, the payload keeps its shape, each refusal is one warning,
    and both rows stay pending for a later flush."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    _board_down(monkeypatch)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "cancel")})

    result = _run(project, milestone, driver, control_interval=0)

    assert result["canceled"] is True, result
    assert set(result) == {"canceled", "run_id", "stopped", "completed", "pending", "warnings"}
    assert _load(project, run_id).status == "canceled"
    assert len(result["warnings"]) == 2, result["warnings"]
    assert (
        f"board comment {run_id}/{a1}/cancelled on card {a1} not posted" in result["warnings"][0]
    )
    assert f"board comment {run_id}/{milestone}/run-end:" in result["warnings"][1]
    assert [state for _key, state in _comment_states(project)] == ["pending", "pending"]


@pytest.mark.git
def test_a_pause_leaves_exactly_one_paused_run_end_on_the_milestone(project):
    """Spec test 2: one comment in total, on the milestone, posted (its
    resume hint is `test_comments.py`'s job); parked a1, pending a2 and b1,
    and every story get nothing."""
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    milestone = shape["milestone"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "pause")})

    result = _run(project, milestone, driver, control_interval=0)

    assert result["paused"] is True, result
    assert len(_comment_states(project)) == 1
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    assert _comment_states(project) == [(key, "posted")]
    body = found[0].body
    assert found[0].author == "am"
    assert "done: 0 of 3" in body
    assert f"parked: [[{a1}]]" in body
    for quiet in (a1, a2, b1, *shape["stories"].values()):
        assert _comments(project, quiet) == [], quiet
    assert "total" not in result
    assert result["warnings"] == []


@pytest.mark.git
def test_a_paused_then_resumed_run_leaves_a_paused_then_a_done_run_end(project):
    """Review Focus 4: the resumed life has its own lease token, so its done
    run-end is a second comment with its own key, after the paused one."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "pause")})
    first = _run(project, milestone, driver, control_interval=0)
    assert first["paused"] is True, first

    again = _resume(project, run_id, FakeDriver(), control_interval=0)

    assert again["done"] is True, again
    keys = _keys(_comments(project, milestone))
    assert len(keys) == 2 and len(set(keys)) == 2, keys
    assert all(key.startswith(f"{run_id}/{milestone}/run-end:") for key in keys)
    run_end_rows = [row for row in _comment_states(project) if "/run-end:" in row[0]]
    assert run_end_rows == [(key, "posted") for key in keys]


# ── run_board helpers (card baef4f94) ───────────────────────────────────────


def _board_milestone(
    n: int,
    *,
    blocked_by: tuple[int, ...] = (),
    status: str = "todo",
    done_children: bool = False,
) -> models.CardNode:
    """A milestone root with one story holding one subtask, ids from `_plan_id`.

    Milestone `n` is `_plan_id(n)`, its story `_plan_id(n * 100 + 1)`, its
    subtask `_plan_id(n * 100 + 2)`. `blocked_by` names other milestones by `n`.
    """
    child_status = "done" if done_children else "todo"
    subtask = models.CardNode(
        id=_plan_id(n * 100 + 2), title=f"subtask of milestone {n}", status=child_status
    )
    story = models.CardNode(
        id=_plan_id(n * 100 + 1),
        title=f"story of milestone {n}",
        status=child_status,
        children=[subtask],
    )
    return models.CardNode(
        id=_plan_id(n),
        title=f"milestone {n}",
        status=status,
        blocked_by=[_plan_id(blocker) for blocker in blocked_by],
        children=[story],
    )


def _prefix_of(card: models.CardNode) -> str:
    """A distinct branch prefix per milestone, as d78b3118's caller would hand in."""
    return f"p{dag.short_id(card.id)}"


def test_board_prefixes_maps_each_milestone_to_its_callers_prefix_in_order():
    one, two = _board_milestone(1), _board_milestone(2)

    prefixes = orchestrate.board_prefixes([one, two], _prefix_of)

    assert list(prefixes.items()) == [(one.id, "p00000001"), (two.id, "p00000002")]


@pytest.mark.parametrize("prefix", ["", "   ", None])
def test_board_prefixes_refuses_a_blank_prefix(prefix):
    with pytest.raises(ValueError, match="has no branch prefix"):
        orchestrate.board_prefixes([_board_milestone(1)], lambda card: prefix)


def test_board_prefixes_refuses_two_milestones_on_one_prefix():
    """Review Focus 1: a shared prefix means one shared `<prefix>-integrate` claim."""
    with pytest.raises(ValueError, match="share the branch prefix 'm14'"):
        orchestrate.board_prefixes(
            [_board_milestone(1), _board_milestone(2)], lambda card: "m14"
        )


# ── board_prefixes over blocker roots (card 8198b0b4) ───────────────────────


def _prefix_recording(calls: list[str], prefix_of: Callable[[models.CardNode], str] = _prefix_of):
    """`prefix_of`, recording the id of every card it is called on in `calls`."""

    def prefix(card: models.CardNode) -> str:
        calls.append(card.id)
        return prefix_of(card)

    return prefix


def test_board_prefixes_keys_a_done_blocker_root_after_the_given_milestones():
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))

    prefixes = orchestrate.board_prefixes([blocked], _prefix_of, roots=[blocker, blocked])

    assert list(prefixes.items()) == [(blocked.id, "p00000002"), (blocker.id, "p00000001")]


@pytest.mark.parametrize(
    "status, done_children",
    [
        ("done", True),
        ("merged", True),
        ("canceled", False),
        ("archived", False),
        ("todo", True),
    ],
)
def test_board_prefixes_keys_a_blocker_root_of_any_non_open_status(status, done_children):
    """Review Focus 5: which blockers matter is `milestone_bases`' call, not this one's."""
    blocker = _board_milestone(1, status=status, done_children=done_children)
    blocked = _board_milestone(2, blocked_by=(1,))
    assert not dag.milestone_is_open(blocker)

    prefixes = orchestrate.board_prefixes([blocked], _prefix_of, roots=[blocker, blocked])

    assert prefixes[blocker.id] == _prefix_of(blocker)


def test_board_prefixes_never_derives_a_root_nobody_blocks_on():
    """Review Focus 1: an unrelated old milestone cannot newly break a board."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    unrelated = _board_milestone(3, status="done", done_children=True)
    calls: list[str] = []

    def prefix_of(card: models.CardNode) -> str:
        calls.append(card.id)
        if card.id == unrelated.id:
            raise ValueError(f"not a card id: {card.id!r}")
        return _prefix_of(card)

    prefixes = orchestrate.board_prefixes(
        [blocked], prefix_of, roots=[blocker, blocked, unrelated]
    )

    assert list(prefixes) == [blocked.id, blocker.id]
    assert unrelated.id not in calls


def test_board_prefixes_keys_only_direct_blockers_of_the_given_milestones():
    """A(done) <- B(done) <- C(open): B is C's blocker and gets a key, A does not."""
    a = _board_milestone(1, status="done", done_children=True)
    b = _board_milestone(2, blocked_by=(1,), status="done", done_children=True)
    c = _board_milestone(3, blocked_by=(2,))

    prefixes = orchestrate.board_prefixes([c], _prefix_of, roots=[a, b, c])

    assert list(prefixes) == [c.id, b.id]


def test_board_prefixes_ignores_a_blocker_id_that_is_not_a_root():
    blocked = models.CardNode(
        id=_plan_id(2), title="milestone 2", status="todo", blocked_by=[_plan_id(99)]
    )

    prefixes = orchestrate.board_prefixes([blocked], _prefix_of, roots=[blocked])

    assert prefixes == {blocked.id: "p00000002"}


def test_board_prefixes_keys_a_blocker_named_twice_once():
    """Review Focus 3: one entry, one derivation, no collision with itself."""
    blocker = _board_milestone(1, status="done", done_children=True)
    two = _board_milestone(2, blocked_by=(1, 1))
    three = _board_milestone(3, blocked_by=(1,))
    calls: list[str] = []

    prefixes = orchestrate.board_prefixes(
        [two, three], _prefix_recording(calls), roots=[blocker, two, three, blocker]
    )

    assert list(prefixes.items()) == [
        (two.id, "p00000002"),
        (three.id, "p00000003"),
        (blocker.id, "p00000001"),
    ]
    assert calls.count(blocker.id) == 1


@pytest.mark.parametrize("prefix", ["", "   ", None])
def test_board_prefixes_refuses_a_blank_prefix_for_a_blocker_root(prefix):
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))

    def prefix_of(card: models.CardNode) -> str:
        return prefix if card.id == blocker.id else _prefix_of(card)

    with pytest.raises(ValueError, match=f"milestone {blocker.id} has no branch prefix"):
        orchestrate.board_prefixes([blocked], prefix_of, roots=[blocker, blocked])


def test_board_prefixes_refuses_a_blocker_root_sharing_an_open_milestones_prefix():
    """Review Focus 4: both would name one integrate branch; stacking would use the wrong one."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))

    with pytest.raises(
        ValueError,
        match=f"milestones {blocked.id} and {blocker.id} share the branch prefix 'm14'",
    ):
        orchestrate.board_prefixes([blocked], lambda card: "m14", roots=[blocker, blocked])


@pytest.mark.parametrize(
    "milestones",
    [
        [_board_milestone(1), _board_milestone(2)],
        [_board_milestone(1), _board_milestone(2, blocked_by=(1,))],
    ],
    ids=["independent", "open-blocker"],
)
def test_board_prefixes_with_roots_but_no_non_open_blocker_is_todays_result(milestones):
    """Review Focus 2: an open blocker already given is not re-added; order is untouched."""
    calls: list[str] = []

    today = orchestrate.board_prefixes(milestones, _prefix_of)
    with_roots = orchestrate.board_prefixes(milestones, _prefix_recording(calls), roots=milestones)

    assert list(with_roots.items()) == list(today.items())
    assert list(today.items()) == [
        (milestones[0].id, "p00000001"),
        (milestones[1].id, "p00000002"),
    ]
    assert calls == [milestones[0].id, milestones[1].id]


def test_a_blocker_keeps_its_prefix_once_done_and_milestone_bases_stacks_on_it():
    """Spec 2.3: the prefix a blocker ran under names the integrate branch it left behind."""
    prefix_of = cli.board_prefix_of(None)
    open_blocker = _board_milestone(1)
    blocked = _board_milestone(2, blocked_by=(1,))
    done_blocker = _board_milestone(1, status="done", done_children=True)
    assert done_blocker.id == open_blocker.id and done_blocker.title == open_blocker.title

    while_open = orchestrate.board_prefixes([open_blocker, blocked], prefix_of)
    once_done = orchestrate.board_prefixes([blocked], prefix_of, roots=[done_blocker, blocked])

    assert once_done[done_blocker.id] == while_open[open_blocker.id] == prefix_of(done_blocker)
    assert orchestrate.milestone_bases(
        [done_blocker, blocked], once_done, lambda branch: True, "master"
    ) == {blocked.id: f"{prefix_of(done_blocker)}-integrate"}


def test_board_claims_unions_each_milestones_claims_first_occurrence_first():
    one, two = _board_milestone(1), _board_milestone(2)
    prefixes = {one.id: "pa", two.id: "pb"}

    keys = orchestrate.board_claims([one, two, one], prefixes)

    assert keys == [
        f"card:{one.id}",
        f"card:{_plan_id(102)}",
        "branch:pa-integrate",
        f"card:{two.id}",
        f"card:{_plan_id(202)}",
        "branch:pb-integrate",
    ]


def test_board_claims_takes_each_milestones_keys_from_milestone_claims():
    one = _board_milestone(1)
    expected = orchestrate.milestone_claims(
        one.id, census.flatten_milestone(one).stories, "pa"
    )

    assert orchestrate.board_claims([one], {one.id: "pa"}) == expected


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"done": True, "run_id": "r"}, "done"),
        ({"escalated": True, "run_id": "r"}, "escalated"),
        ({"escalated": True, "control": "pause", "run_id": "r"}, "escalated"),
        ({"paused": True, "run_id": "r", "resume": "am resume r"}, "stopped"),
        ({"cancelled": True, "run_id": "r"}, "cancelled"),
        ({"run_id": "r"}, "escalated"),
    ],
)
def test_milestone_status_reads_a_run_milestone_payload(payload, status):
    assert orchestrate.milestone_status(payload) == status


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"canceled": True, "run_id": "r"}, "cancelled"),
        ({"cancelled": True, "run_id": "r"}, "cancelled"),
        ({"cancelled": False, "canceled": True, "run_id": "r"}, "cancelled"),
        ({"canceled": True, "escalated": True, "run_id": "r"}, "cancelled"),
        ({"canceled": True, "paused": True, "run_id": "r"}, "cancelled"),
        ({"done": True, "canceled": True, "run_id": "r"}, "done"),
        ({"canceled": False, "paused": True, "run_id": "r"}, "stopped"),
        ({"canceled": "yes", "run_id": "r"}, "escalated"),
    ],
)
def test_milestone_status_reads_either_cancel_key(payload, status):
    assert orchestrate.milestone_status(payload) == status


# ── milestone_bases (card 40ac07f3) ─────────────────────────────────────────


def _prefixes(*ns: int) -> dict[str, str]:
    """`{_plan_id(n): f"p{n}"}`: milestone `n`'s integrate branch is `p<n>-integrate`."""
    return {_plan_id(n): f"p{n}" for n in ns}


def _recording_exists(*present: str) -> tuple[Callable[[str], bool], list[str]]:
    """A `branch_exists` that answers from `present` and records every call."""
    calls: list[str] = []

    def exists(branch: str) -> bool:
        calls.append(branch)
        return branch in present

    return exists, calls


def test_milestone_bases_puts_unblocked_open_milestones_on_the_base_branch_in_input_order():
    """Spec test 1: no blockers -> base_branch; keys are the open ones, input order."""
    two, one, done = _board_milestone(2), _board_milestone(1), _board_milestone(3, status="done")
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases([two, one, done], _prefixes(1, 2, 3), exists, "master")

    assert list(bases.items()) == [(two.id, "master"), (one.id, "master")]
    assert calls == []


def test_milestone_bases_of_no_open_milestones_is_empty():
    exists, _ = _recording_exists()
    assert orchestrate.milestone_bases([], {}, exists, "master") == {}
    assert (
        orchestrate.milestone_bases(
            [_board_milestone(1, status="done")], _prefixes(1), exists, "master"
        )
        == {}
    )


@pytest.mark.parametrize(
    ("status", "done_children"),
    [("merged", True), ("canceled", False), ("archived", False)],
)
def test_milestone_bases_ignores_a_landed_blocker_without_checking_its_branch(
    status, done_children
):
    """Spec tests 2-3, Review Focus 5: a canceled/archived blocker with open work
    under it is still landed, never a stack candidate."""
    blocker = _board_milestone(1, status=status, done_children=done_children)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists("p1-integrate")

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocked.id: "master"}
    assert calls == []


def test_milestone_bases_stacks_on_one_open_blocker_without_checking_its_branch():
    """Spec test 4: the open blocker's run creates its branch; existence is not asked."""
    blocker, blocked = _board_milestone(1), _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocker.id: "master", blocked.id: "p1-integrate"}
    assert calls == []


def test_milestone_bases_stacks_on_a_done_blocker_whose_integrate_branch_exists():
    """Spec test 5."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists("p1-integrate")

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocked.id: "p1-integrate"}
    assert calls == ["p1-integrate"]


def test_milestone_bases_treats_a_done_blocker_without_its_branch_as_landed():
    """Spec test 6: "assume landed; today's behaviour"."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocked.id: "master"}
    assert calls == ["p1-integrate"]


def test_milestone_bases_refuses_a_milestone_with_two_open_blockers():
    """Spec test 7."""
    one, two = _board_milestone(1), _board_milestone(2)
    blocked = _board_milestone(3, blocked_by=(1, 2))
    exists, _ = _recording_exists()

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        orchestrate.milestone_bases([one, two, blocked], _prefixes(1, 2, 3), exists, "master")

    assert isinstance(caught.value, ValueError)
    message = str(caught.value)
    assert blocked.id in message
    assert one.id in message and two.id in message
    assert "chain" in message
    assert "merged" not in message, "the mark-merged hint is only for unlanded blockers"


def test_milestone_bases_refuses_an_open_blocker_plus_a_done_blocker_with_its_branch():
    """Spec test 8: picking one would drop the other's unmerged work."""
    one = _board_milestone(1)
    two = _board_milestone(2, status="done", done_children=True)
    blocked = _board_milestone(3, blocked_by=(1, 2))
    exists, _ = _recording_exists("p2-integrate")

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        orchestrate.milestone_bases([one, two, blocked], _prefixes(1, 2, 3), exists, "master")

    message = str(caught.value)
    assert blocked.id in message
    assert one.id in message and two.id in message
    assert "chain" in message
    assert "merged" in message
    assert message.count(two.id) == 2, "the done blocker is named again in the hint"
    assert message.count(one.id) == 1, "the open blocker cannot be marked merged"


def test_milestone_bases_stacks_on_the_one_open_blocker_when_the_others_are_satisfied():
    """Spec test 9: open + done-without-branch + merged -> the open one."""
    one = _board_milestone(1)
    two = _board_milestone(2, status="done", done_children=True)
    three = _board_milestone(3, status="merged", done_children=True)
    blocked = _board_milestone(4, blocked_by=(1, 2, 3))
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases(
        [one, two, three, blocked], _prefixes(1, 2, 3, 4), exists, "master"
    )

    assert bases == {one.id: "master", blocked.id: "p1-integrate"}
    assert calls == ["p2-integrate"]


def test_milestone_bases_stacks_a_chain_of_open_milestones():
    """Spec test 10 / §2.4: A <- B <- C, all open."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases([a, b, c], _prefixes(1, 2, 3), exists, "master")

    assert list(bases.items()) == [
        (a.id, "master"),
        (b.id, "p1-integrate"),
        (c.id, "p2-integrate"),
    ]


def test_milestone_bases_stacks_a_chain_whose_head_is_done_with_its_branch():
    """Spec test 11 / §2.4: A done with its branch, B and C open."""
    a = _board_milestone(1, status="done", done_children=True)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    exists, _ = _recording_exists("p1-integrate")

    bases = orchestrate.milestone_bases([a, b, c], _prefixes(1, 2, 3), exists, "master")

    assert list(bases.items()) == [(b.id, "p1-integrate"), (c.id, "p2-integrate")]


def test_milestone_bases_ignores_a_blocker_that_is_not_a_milestone():
    """Spec test 12: an unknown id needs no prefix and counts as satisfied."""
    blocked = _board_milestone(1, blocked_by=(99,))
    exists, calls = _recording_exists()

    bases = orchestrate.milestone_bases([blocked], _prefixes(1), exists, "master")

    assert bases == {blocked.id: "master"}
    assert calls == []


def test_milestone_bases_counts_a_duplicated_blocker_once():
    """Spec test 13: blocked_by=(1, 1) is S1, not a false S2 refusal."""
    blocker, blocked = _board_milestone(1), _board_milestone(2, blocked_by=(1, 1))
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases[blocked.id] == "p1-integrate"


def test_milestone_bases_reads_blocker_statuses_in_any_case():
    """Spec test 14: MERGED is landed; Done goes through branch_exists."""
    merged = _board_milestone(1, status="MERGED", done_children=True)
    on_merged = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists("p1-integrate")
    assert orchestrate.milestone_bases(
        [merged, on_merged], _prefixes(1, 2), exists, "master"
    ) == {on_merged.id: "master"}
    assert calls == []

    done = _board_milestone(1, status="Done", done_children=True)
    exists, calls = _recording_exists("p1-integrate")
    assert orchestrate.milestone_bases(
        [done, on_merged], _prefixes(1, 2), exists, "master"
    ) == {on_merged.id: "p1-integrate"}
    assert calls == ["p1-integrate"]


def test_milestone_bases_gives_non_open_milestones_no_key():
    """Spec test 15: the keys are exactly what board_levels would dispatch."""
    done = _board_milestone(1, status="done")
    finished_tree = _board_milestone(2, done_children=True)
    live = _board_milestone(3)
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases(
        [done, finished_tree, live], _prefixes(1, 2, 3), exists, "master"
    )

    assert list(bases) == [live.id]
    assert list(bases) == [
        node.id for level in dag.board_levels([done, finished_tree, live]) for node in level
    ]


@pytest.mark.parametrize(
    "blocker",
    [
        pytest.param(_board_milestone(1), id="open"),
        pytest.param(_board_milestone(1, status="done", done_children=True), id="done"),
    ],
)
def test_milestone_bases_refuses_a_needed_blocker_with_no_prefix(blocker):
    """Spec test 16: a caller bug, so ValueError, not MilestoneBlockersError."""
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, _ = _recording_exists()

    with pytest.raises(ValueError) as caught:
        orchestrate.milestone_bases([blocker, blocked], _prefixes(2), exists, "master")

    assert not isinstance(caught.value, orchestrate.MilestoneBlockersError)
    message = str(caught.value)
    assert blocked.id in message and blocker.id in message
    assert "no branch prefix" in message


@pytest.mark.parametrize("prefix", ["", "   ", None])
def test_milestone_bases_refuses_a_blank_prefix_for_a_needed_blocker(prefix):
    """Review Focus 4: a blank prefix would name the branch `-integrate`."""
    blocker, blocked = _board_milestone(1), _board_milestone(2, blocked_by=(1,))
    prefixes = {blocker.id: prefix, blocked.id: "p2"}
    exists, _ = _recording_exists()

    with pytest.raises(ValueError, match="no branch prefix") as caught:
        orchestrate.milestone_bases([blocker, blocked], prefixes, exists, "master")

    assert not isinstance(caught.value, orchestrate.MilestoneBlockersError)


def test_milestone_bases_needs_no_prefix_for_a_landed_blocker():
    """Spec test 16, second half."""
    blocker = _board_milestone(1, status="merged", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(2), exists, "master")

    assert bases == {blocked.id: "master"}


@pytest.mark.parametrize(
    ("present", "expected"),
    [(("p1-integrate",), "p1-integrate"), ((), "master")],
)
def test_milestone_bases_treats_a_todo_blocker_with_nothing_open_like_done(present, expected):
    """Spec test 17: the "unlanded" non-done case."""
    blocker = _board_milestone(1, status="todo", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists(*present)

    bases = orchestrate.milestone_bases([blocker, blocked], _prefixes(1, 2), exists, "master")

    assert bases == {blocked.id: expected}
    assert calls == ["p1-integrate"]


def test_milestone_bases_a_repeated_milestone_checks_its_blocker_branch_once():
    """Review Focus 1: at most one branch_exists call per (M, B) pair."""
    blocker = _board_milestone(1, status="done", done_children=True)
    blocked = _board_milestone(2, blocked_by=(1,))
    exists, calls = _recording_exists("p1-integrate")

    bases = orchestrate.milestone_bases(
        [blocker, blocked, blocked], _prefixes(1, 2), exists, "master"
    )

    assert bases == {blocked.id: "p1-integrate"}
    assert calls == ["p1-integrate"]


def test_milestone_bases_does_not_depend_on_input_order_for_classification():
    """Review Focus 2: a chain given C, B, A gets the same bases, keyed C, B, A."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    exists, _ = _recording_exists()

    bases = orchestrate.milestone_bases([c, b, a], _prefixes(1, 2, 3), exists, "master")

    assert list(bases.items()) == [
        (c.id, "p2-integrate"),
        (b.id, "p1-integrate"),
        (a.id, "master"),
    ]


def test_milestone_bases_refuses_the_first_offending_milestone_naming_blockers_in_blocked_by_order():
    """Review Focus 3: one problem at a time, deterministically worded."""
    one, two = _board_milestone(1), _board_milestone(2)
    first = _board_milestone(3, blocked_by=(2, 1))
    second = _board_milestone(4, blocked_by=(1, 2))
    exists, _ = _recording_exists()

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        orchestrate.milestone_bases(
            [one, two, first, second], _prefixes(1, 2, 3, 4), exists, "master"
        )

    message = str(caught.value)
    assert first.id in message
    assert second.id not in message
    assert message.index(two.id) < message.index(one.id)


def test_milestone_bases_leaves_its_inputs_alone():
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1, 1))
    milestones = [a, b]
    before = [card.model_copy(deep=True) for card in milestones]
    prefixes = _prefixes(1, 2)
    exists, _ = _recording_exists()

    orchestrate.milestone_bases(milestones, prefixes, exists, "master")

    assert milestones == before
    assert prefixes == _prefixes(1, 2)


# ── run_board at its seams (card baef4f94) ──────────────────────────────────
#
# `board.roots`, `cli.refuse_claimed`, `orchestrate._run_milestone_async` and
# `orchestrate._local_branch_exists` are replaced, so these exercise
# `run_board`'s own validation, leveling, bases, claim union, tree, isolation
# and payload with no git, brd or harness.
# Production wiring is `tests/e2e/test_run_board.py`'s.


@dataclass
class FakeMilestoneRuns:
    """`orchestrate._run_milestone_async` replaced: records each call and
    answers from `outcomes` (a payload dict, or an exception to raise);
    a milestone with no entry finishes `done`."""

    outcomes: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def __call__(self, milestone: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((milestone, kwargs))
        await asyncio.sleep(0)
        outcome = self.outcomes.get(milestone, {"done": True, "run_id": f"run-{milestone[:8]}"})
        if isinstance(outcome, BaseException):
            raise outcome
        return dict(outcome)

    def called(self) -> list[str]:
        return [milestone for milestone, _kwargs in self.calls]


@dataclass
class BoardSeams:
    root: Path
    runs: FakeMilestoneRuns
    cards: list[models.CardNode] = field(default_factory=list)
    claims: list[list[str]] = field(default_factory=list)
    branches: set[str] = field(default_factory=set)
    """The local branches `orchestrate._local_branch_exists` reports; none by default."""
    asked: list[tuple[Path, str]] = field(default_factory=list)
    """Every `(root, branch)` the faked `_local_branch_exists` was asked about."""


@pytest.fixture
def board_seams(tmp_path, monkeypatch) -> BoardSeams:
    seams = BoardSeams(root=tmp_path, runs=FakeMilestoneRuns())
    monkeypatch.setattr(board, "roots", lambda *, repo_dir=None: list(seams.cards))
    monkeypatch.setattr(orchestrate, "_run_milestone_async", seams.runs)

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        seams.claims.append(list(keys))

    monkeypatch.setattr(cli, "refuse_claimed", refuse)

    def local_branch_exists(root: Path) -> Callable[[str], bool]:
        def exists(branch: str) -> bool:
            seams.asked.append((root, branch))
            return branch in seams.branches

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", local_branch_exists)
    return seams


def _board(seams: BoardSeams, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "repo_dir": seams.root,
        "base_branch": "main",
        "branch_prefix_of": _prefix_of,
        "max_concurrent": 2,
    }
    kwargs.update(overrides)
    return orchestrate.run_board(**kwargs)


def _by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["milestone_id"]: entry for entry in result["milestones"]}


@pytest.mark.parametrize(
    "overrides", [{"max_concurrent": 0}, {"base_branch": None}, {"base_branch": ""}]
)
def test_run_board_refuses_bad_arguments_before_it_reads_the_board(
    board_seams, monkeypatch, overrides
):
    def no_read(*, repo_dir=None):
        pytest.fail("run_board read the board before refusing its arguments")

    monkeypatch.setattr(board, "roots", no_read)

    with pytest.raises(ValueError):
        _board(board_seams, **overrides)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


@pytest.mark.parametrize(
    "prefix_of", [lambda card: "", lambda card: "   ", lambda card: None, lambda card: "same"]
)
def test_run_board_refuses_a_blank_or_shared_branch_prefix_before_any_claim_check(
    board_seams, prefix_of
):
    board_seams.cards = [_board_milestone(1), _board_milestone(2)]

    with pytest.raises(ValueError, match="branch prefix"):
        _board(board_seams, branch_prefix_of=prefix_of)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_lets_a_milestone_cycle_propagate_before_any_claim_check(board_seams):
    board_seams.cards = [
        _board_milestone(1, blocked_by=(2,)),
        _board_milestone(2, blocked_by=(1,)),
    ]

    with pytest.raises(dag.DependencyCycleError):
        _board(board_seams)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_on_a_board_with_nothing_open_is_ok_and_runs_nothing(board_seams):
    board_seams.cards = [_board_milestone(1, status="done", done_children=True)]

    result = _board(board_seams)

    assert result == {"ok": True, "board": True, "levels": [], "milestones": []}
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_checks_every_open_milestones_claims_once_in_level_order(board_seams):
    first = _board_milestone(1)
    second = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [second, first]  # board order is not level order

    _board(board_seams)

    expected = list(
        dict.fromkeys(
            [
                *orchestrate.milestone_claims(
                    first.id, census.flatten_milestone(first).stories, _prefix_of(first)
                ),
                *orchestrate.milestone_claims(
                    second.id, census.flatten_milestone(second).stories, _prefix_of(second)
                ),
            ]
        )
    )
    assert board_seams.claims == [expected]
    assert expected[0] == f"card:{first.id}"


def test_run_board_starts_no_milestone_when_the_upfront_claim_check_refuses(
    board_seams, monkeypatch
):
    board_seams.cards = [_board_milestone(1), _board_milestone(2, blocked_by=(1,))]

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        raise cli.ClaimedError("held elsewhere", key=keys[-1], run_id=OTHER_RUN_ID)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)

    with pytest.raises(cli.ClaimedError):
        _board(board_seams)

    assert board_seams.runs.calls == []


def test_run_board_runs_every_milestone_on_one_shared_semaphore(board_seams):
    one, two = _board_milestone(1), _board_milestone(2)
    board_seams.cards = [one, two]

    result = _board(board_seams, max_concurrent=3, commands=("git status",))

    assert result["ok"] is True
    assert result["board"] is True
    assert result["levels"] == [{"level": 0, "milestones": [one.id, two.id]}]
    assert result["milestones"] == [
        {"milestone_id": one.id, "status": "done", "done": True, "run_id": f"run-{one.id[:8]}"},
        {"milestone_id": two.id, "status": "done", "done": True, "run_id": f"run-{two.id[:8]}"},
    ]
    assert sorted(board_seams.runs.called()) == sorted([one.id, two.id])
    semaphores = [kwargs["slots"] for _milestone, kwargs in board_seams.runs.calls]
    assert isinstance(semaphores[0], asyncio.Semaphore)
    assert all(semaphore is semaphores[0] for semaphore in semaphores)
    for milestone, kwargs in board_seams.runs.calls:
        card = one if milestone == one.id else two
        assert kwargs["branch_prefix"] == _prefix_of(card)
        assert kwargs["base_branch"] == "main"
        assert kwargs["repo_dir"] == runs.resolve_repo_dir(board_seams.root)
        assert kwargs["max_concurrent"] == 3
        assert list(kwargs["commands"]) == ["git status"]
        assert "resume_run_id" not in kwargs


def test_run_board_forwards_every_run_shaping_argument_to_each_milestone(board_seams):
    """Each milestone runs with the board's own `allow_no_verification`,
    `runner_factory`, `driver`, `clock` and `control_interval`, not defaults."""
    one, two = _board_milestone(1), _board_milestone(2)
    board_seams.cards = [one, two]
    runner_factory = object()
    driver = object()

    def clock() -> datetime:
        return datetime(2026, 10, 2, tzinfo=timezone.utc)

    _board(
        board_seams,
        allow_no_verification=True,
        runner_factory=runner_factory,
        driver=driver,
        clock=clock,
        control_interval=0.25,
    )

    assert sorted(board_seams.runs.called()) == sorted([one.id, two.id])
    for _milestone, kwargs in board_seams.runs.calls:
        assert kwargs["allow_no_verification"] is True
        assert kwargs["runner_factory"] is runner_factory
        assert kwargs["driver"] is driver
        assert kwargs["clock"] is clock
        assert kwargs["control_interval"] == 0.25


def test_run_board_isolates_a_milestone_that_raises_and_blocks_only_its_dependents(
    board_seams,
):
    """Spec steps 5-6 and Review Focus 4: D is blocked by a *blocked* B."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3)
    d = _board_milestone(4, blocked_by=(2,))
    board_seams.cards = [a, b, c, d]
    board_seams.runs.outcomes[a.id] = RuntimeError("boom")
    grafo_level = logging.getLogger(orchestrate.GRAFO_LOGGER).level

    result = _board(board_seams)

    assert result["ok"] is False
    assert [entry["milestone_id"] for entry in result["milestones"]] == [a.id, c.id, b.id, d.id]
    by_id = _by_id(result)
    assert by_id[a.id] == {"milestone_id": a.id, "status": "escalated", "error": "RuntimeError: boom"}
    assert by_id[c.id]["status"] == "done"
    assert by_id[b.id] == {"milestone_id": b.id, "status": "blocked", "blocked_by": [a.id]}
    assert by_id[d.id] == {"milestone_id": d.id, "status": "blocked", "blocked_by": [b.id]}
    assert sorted(board_seams.runs.called()) == sorted([a.id, c.id])
    assert logging.getLogger(orchestrate.GRAFO_LOGGER).level == grafo_level


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"escalated": True, "run_id": "r"}, "escalated"),
        ({"paused": True, "run_id": "r", "resume": "am resume r"}, "stopped"),
        ({"cancelled": True, "run_id": "r"}, "cancelled"),
    ],
)
def test_run_board_blocks_the_dependent_of_a_milestone_that_did_not_finish_done(
    board_seams, payload, status
):
    """Review Focus 2: a paused or cancelled milestone blocks like an escalated one."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    board_seams.runs.outcomes[a.id] = payload

    result = _board(board_seams)

    assert result["ok"] is False
    assert result["milestones"] == [
        {"milestone_id": a.id, "status": status, **payload},
        {"milestone_id": b.id, "status": "blocked", "blocked_by": [a.id]},
    ]
    assert board_seams.runs.called() == [a.id]


def _board_async(seams: BoardSeams, milestones: list[models.CardNode]) -> list[dict[str, Any]]:
    """`_run_board_async` straight, every milestone on `main`: its gating, not
    `run_board`'s refusals (which refuse a two-blocker milestone)."""
    return asyncio.run(
        orchestrate._run_board_async(
            milestones,
            prefixes={card.id: _prefix_of(card) for card in milestones},
            bases={card.id: "main" for card in milestones},
            root=seams.root,
            commands=(),
            allow_no_verification=False,
            runner_factory=None,
            driver=None,
            clock=orchestrate._utcnow,
            max_concurrent=2,
            control_interval=control.CONTROL_POLL_SECONDS,
        )
    )


def test_run_board_async_runs_a_two_blocker_milestone_only_after_both_finish_done(
    board_seams,
):
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))

    entries = _board_async(board_seams, [a, b, c])

    assert board_seams.runs.called()[-1] == c.id
    assert {entry["milestone_id"]: entry for entry in entries}[c.id]["status"] == "done"


def test_run_board_async_blocks_a_two_blocker_milestone_on_only_its_unclean_blocker(
    board_seams,
):
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.runs.outcomes[b.id] = {"escalated": True, "run_id": "r"}

    entries = _board_async(board_seams, [a, b, c])

    assert {entry["milestone_id"]: entry for entry in entries}[c.id] == {
        "milestone_id": c.id,
        "status": "blocked",
        "blocked_by": [b.id],
    }
    assert c.id not in board_seams.runs.called()


def test_run_board_treats_an_already_done_blocker_as_satisfied(board_seams):
    """Review Focus 3: `board_levels` drops the done milestone, so its dependent is level 0."""
    finished = _board_milestone(1, status="done", done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [finished, later]

    result = _board(board_seams)

    assert result["ok"] is True
    assert result["levels"] == [{"level": 0, "milestones": [later.id]}]
    assert board_seams.runs.called() == [later.id]


def test_run_board_payload_has_one_run_per_milestone_and_no_board_level_run(board_seams):
    """Card a7fcc076: a board run has no board-level Run. The payload carries
    no top-level `run_id`; each dispatched milestone's entry carries its own
    run's `run_id`, distinct per milestone. An escalated (raised) entry and a
    blocked entry were never given a run, so they carry no `run_id` at all.
    A done entry is `milestone_id` and `status` plus the run payload's own
    keys, which here are the fake's `done` and `run_id`."""
    a, b, c = _board_milestone(1), _board_milestone(2), _board_milestone(3)
    d = _board_milestone(4, blocked_by=(3,))
    board_seams.cards = [a, b, c, d]
    board_seams.runs.outcomes[c.id] = RuntimeError("boom")

    result = _board(board_seams)

    assert set(result) == {"ok", "board", "levels", "milestones"}
    assert "run_id" not in result
    assert result["ok"] is False
    assert result["board"] is True
    assert result["levels"] == [
        {"level": 0, "milestones": [a.id, b.id, c.id]},
        {"level": 1, "milestones": [d.id]},
    ]
    for level in result["levels"]:
        assert set(level) == {"level", "milestones"}
    by_id = _by_id(result)
    assert set(by_id) == {a.id, b.id, c.id, d.id}
    for done in (a, b):
        assert set(by_id[done.id]) == {"milestone_id", "status", "done", "run_id"}
        assert by_id[done.id]["status"] == "done"
    assert set(by_id[c.id]) == {"milestone_id", "status", "error"}
    assert by_id[c.id]["status"] == "escalated"
    assert by_id[c.id]["error"] == "RuntimeError: boom"
    assert set(by_id[d.id]) == {"milestone_id", "status", "blocked_by"}
    assert by_id[d.id]["status"] == "blocked"
    assert by_id[d.id]["blocked_by"] == [c.id]
    run_ids = [by_id[done.id]["run_id"] for done in (a, b)]
    assert len(set(run_ids)) == len(run_ids)


def test_run_board_ends_on_a_base_exception_instead_of_hanging(board_seams):
    """Review Focus 5: grafo drops a non-`Exception` and would hang `gather()`;
    the board run re-raises it through `run_until_killed`."""

    class Fatal(BaseException):
        pass

    a, b = _board_milestone(1), _board_milestone(2)
    board_seams.cards = [a, b]
    board_seams.runs.outcomes[a.id] = Fatal("dead")

    with pytest.raises(Fatal):
        _board(board_seams)


# ── run_board bases (card 5b772688) ─────────────────────────────────────────


def _bases(seams: BoardSeams) -> dict[str, str]:
    """Each dispatched milestone's `base_branch`, keyed by milestone id."""
    return {milestone: kwargs["base_branch"] for milestone, kwargs in seams.runs.calls}


def test_run_board_stacks_a_blocked_milestone_on_its_open_blockers_integrate_branch(
    board_seams,
):
    """Spec test 1: an open blocker's branch is this board run's to create; git is never asked."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]

    result = _board(board_seams)

    assert result["ok"] is True
    assert _bases(board_seams) == {
        a.id: "main",
        b.id: integration.integration_branch(_prefix_of(a)),
    }
    assert _bases(board_seams)[b.id] == "p00000001-integrate"
    assert board_seams.asked == []


def test_run_board_stacks_a_three_milestone_chain_each_on_the_one_before(board_seams):
    """Spec test 2: A <- B <- C, all open."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    board_seams.cards = [c, b, a]  # board order is not chain order

    _board(board_seams)

    assert _bases(board_seams) == {
        a.id: "main",
        b.id: "p00000001-integrate",
        c.id: "p00000002-integrate",
    }
    assert board_seams.runs.called() == [a.id, b.id, c.id]


def test_run_board_refuses_a_two_open_blocker_milestone_before_any_claim_check(board_seams):
    """Spec test 3: nothing was claimed or dispatched, so no run row, run
    directory or lease can exist."""
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.cards = [a, b, c]

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        _board(board_seams)

    message = str(caught.value)
    for card_id in (c.id, a.id, b.id):
        assert card_id in message
    assert "chain them" in message
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


@pytest.mark.parametrize("present", [True, False], ids=["present", "absent"])
def test_run_board_stacks_on_an_unlanded_done_blockers_integrate_branch_only_when_it_exists(
    board_seams, present
):
    """Spec test 4: the check is bound to the resolved repository and asked once.
    Also proves `roots=` reaches `board_prefixes` (else "has no branch prefix")."""
    done = _board_milestone(1, status="done", done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [done, later]
    if present:
        board_seams.branches.add("p00000001-integrate")

    result = _board(board_seams)

    assert result["ok"] is True
    assert result["levels"] == [{"level": 0, "milestones": [later.id]}]
    assert _bases(board_seams) == {later.id: "p00000001-integrate" if present else "main"}
    assert board_seams.asked == [
        (runs.resolve_repo_dir(board_seams.root), "p00000001-integrate")
    ]


@pytest.mark.parametrize("status", ["merged", "canceled", "archived"])
def test_run_board_never_asks_git_about_a_landed_blocker(board_seams, status):
    """Spec test 5: a landed blocker's work is in the base already, branch or not."""
    landed = _board_milestone(1, status=status, done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [landed, later]
    board_seams.branches.add("p00000001-integrate")

    _board(board_seams)

    assert _bases(board_seams) == {later.id: "main"}
    assert board_seams.asked == []


def test_run_board_refuses_an_open_and_an_unlanded_blocker_with_its_branch(board_seams):
    """Spec test 6: the unlanded blocker's branch makes it a second candidate."""
    a = _board_milestone(1)
    d = _board_milestone(4, status="done", done_children=True)
    c = _board_milestone(3, blocked_by=(1, 4))
    board_seams.cards = [a, d, c]
    board_seams.branches.add("p00000004-integrate")

    with pytest.raises(orchestrate.MilestoneBlockersError) as caught:
        _board(board_seams)

    message = str(caught.value)
    assert c.id in message and a.id in message
    assert f"mark {d.id} merged" in message
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_refuses_a_cycle_before_two_blockers(board_seams):
    """Spec test 7, cycle first (a guard: passes before and after the change)."""
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    x = _board_milestone(4, blocked_by=(5,))
    y = _board_milestone(5, blocked_by=(4,))
    board_seams.cards = [a, b, c, x, y]

    with pytest.raises(dag.DependencyCycleError):
        _board(board_seams)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_refuses_a_shared_prefix_before_two_blockers(board_seams):
    """Spec test 7, prefixes before bases (a guard: passes before and after the change)."""
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.cards = [a, b, c]

    with pytest.raises(ValueError, match="share the branch prefix") as caught:
        _board(board_seams, branch_prefix_of=lambda card: "same")

    assert not isinstance(caught.value, orchestrate.MilestoneBlockersError)
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_run_board_refuses_two_blockers_before_the_claims_check(board_seams, monkeypatch):
    """Spec test 7, bases before claims: the claim refusal is never reached."""
    a, b = _board_milestone(1), _board_milestone(2)
    c = _board_milestone(3, blocked_by=(1, 2))
    board_seams.cards = [a, b, c]

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        raise cli.ClaimedError("held elsewhere", key=keys[-1], run_id=OTHER_RUN_ID)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)

    with pytest.raises(orchestrate.MilestoneBlockersError):
        _board(board_seams)

    assert board_seams.runs.calls == []


def test_run_board_checks_a_non_open_blocker_roots_prefix_before_any_claim_check(
    board_seams,
):
    """Spec test 8: 1.2's prefix check now covers a `done` blocker root too."""
    done = _board_milestone(1, status="done", done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [done, later]

    def prefix_of(card: models.CardNode) -> str:
        return "" if card.id == done.id else _prefix_of(card)

    with pytest.raises(ValueError, match="has no branch prefix"):
        _board(board_seams, branch_prefix_of=prefix_of)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


# ── board pre-flight / engine seam (card 203a9a5e) ──────────────────────────
#
# `preflight_board` and `run_board_engine`, the two stages `run_board` now
# composes, driven through the same `board_seams` fakes: no git, brd or harness.


def _preflight(seams: BoardSeams, **overrides: Any) -> "orchestrate.BoardPreflight":
    """`orchestrate.preflight_board` with `_board`'s defaults.

    The return annotation is a string: this file has no `from __future__ import
    annotations`, and a bare one would fail at import before the class exists."""
    kwargs: dict[str, Any] = {
        "repo_dir": seams.root,
        "base_branch": "main",
        "branch_prefix_of": _prefix_of,
        "max_concurrent": 2,
    }
    kwargs.update(overrides)
    return orchestrate.preflight_board(**kwargs)


def test_preflight_board_returns_the_state_the_engine_runs_on(board_seams):
    """A done, unlanded blocker `a` (its branch exists), then `b` <- `c`, given out of order."""
    a = _board_milestone(1, status="done", done_children=True)
    b = _board_milestone(2, blocked_by=(1,))
    c = _board_milestone(3, blocked_by=(2,))
    board_seams.cards = [c, b, a]
    board_seams.branches.add("p00000001-integrate")

    pre = _preflight(board_seams)

    root = runs.resolve_repo_dir(board_seams.root)
    assert pre.root == root
    assert pre.base_branch == "main"
    assert pre.max_concurrent == 2
    assert [card.id for card in pre.milestones] == [b.id, c.id]
    assert [[card.id for card in level] for level in pre.levels] == [[b.id], [c.id]]
    assert list(pre.prefixes) == [b.id, c.id, a.id]
    assert pre.prefixes[a.id] == "p00000001"
    assert pre.bases == {b.id: "p00000001-integrate", c.id: "p00000002-integrate"}
    assert pre.levels_payload == [
        {"level": 0, "milestones": [b.id]},
        {"level": 1, "milestones": [c.id]},
    ]
    assert board_seams.asked == [(root, "p00000001-integrate")]
    assert board_seams.claims == [orchestrate.board_claims(pre.milestones, pre.prefixes)]
    assert board_seams.runs.calls == []


@pytest.mark.parametrize(
    "overrides", [{"max_concurrent": 0}, {"base_branch": None}, {"base_branch": ""}]
)
def test_preflight_board_refuses_bad_arguments_before_it_reads_the_board(
    board_seams, monkeypatch, overrides
):
    def no_read(*, repo_dir=None):
        pytest.fail("preflight_board read the board before refusing its arguments")

    monkeypatch.setattr(board, "roots", no_read)

    with pytest.raises(ValueError):
        _preflight(board_seams, **overrides)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


@pytest.mark.parametrize(
    ("cards", "overrides", "error", "match"),
    [
        pytest.param(
            lambda: [_board_milestone(1, blocked_by=(2,)), _board_milestone(2, blocked_by=(1,))],
            {},
            dag.DependencyCycleError,
            None,
            id="cycle",
        ),
        pytest.param(
            lambda: [_board_milestone(1), _board_milestone(2)],
            {"branch_prefix_of": lambda card: "same"},
            ValueError,
            "branch prefix",
            id="shared-prefix",
        ),
        pytest.param(
            lambda: [
                _board_milestone(1),
                _board_milestone(2),
                _board_milestone(3, blocked_by=(1, 2)),
            ],
            {},
            orchestrate.MilestoneBlockersError,
            "chain them",
            id="two-open-blockers",
        ),
    ],
)
def test_preflight_board_refuses_each_board_problem_before_the_claim_check(
    board_seams, cards, overrides, error, match
):
    board_seams.cards = cards()

    with pytest.raises(error, match=match):
        _preflight(board_seams, **overrides)

    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_preflight_board_lets_a_claim_conflict_propagate_with_nothing_started(
    board_seams, monkeypatch
):
    board_seams.cards = [_board_milestone(1), _board_milestone(2, blocked_by=(1,))]

    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        raise cli.ClaimedError("held elsewhere", key=keys[-1], run_id=OTHER_RUN_ID)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)

    with pytest.raises(cli.ClaimedError) as caught:
        _preflight(board_seams)

    assert caught.value.run_id == OTHER_RUN_ID
    assert board_seams.runs.calls == []


def test_preflight_board_lets_a_git_failure_from_the_branch_check_propagate(
    board_seams, monkeypatch
):
    """Review Focus 4: a broken repository never passes the pre-flight silently."""
    done = _board_milestone(1, status="done", done_children=True)
    later = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [done, later]
    asked: list[str] = []

    def broken_branch_exists(root: Path) -> Callable[[str], bool]:
        def exists(branch: str) -> bool:
            asked.append(branch)
            raise worktree.GitError("not a git repository", argv=["git"], exit_code=128)

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", broken_branch_exists)

    with pytest.raises(worktree.GitError) as caught:
        _preflight(board_seams)

    assert caught.value.exit_code == 128
    assert asked == ["p00000001-integrate"]
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_preflight_board_on_a_board_with_nothing_open_skips_the_claim_check(board_seams):
    """Review Focus 1: today's empty-board return comes before the claim check."""
    board_seams.cards = [_board_milestone(1, status="done", done_children=True)]

    pre = _preflight(board_seams)

    assert pre.milestones == []
    assert pre.levels == []
    assert pre.levels_payload == []
    assert pre.bases == {}
    assert board_seams.claims == []
    assert board_seams.runs.calls == []


def test_preflight_board_never_starts_an_event_loop(board_seams, monkeypatch):
    board_seams.cards = [_board_milestone(1), _board_milestone(2, blocked_by=(1,))]

    def fail(*args: Any, **kwargs: Any) -> None:
        pytest.fail("preflight_board started the board run")

    monkeypatch.setattr(orchestrate.asyncio, "run", fail)
    monkeypatch.setattr(orchestrate, "_run_board_async", fail)

    pre = _preflight(board_seams)

    assert [card.id for card in pre.milestones] == [_board_milestone(1).id, _board_milestone(2).id]
    assert board_seams.runs.calls == []


def test_run_board_engine_runs_a_preflight_without_reading_the_board_again(
    board_seams, monkeypatch
):
    """Review Focus 3: the engine runs the approved pre-flight, never a fresh read."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    pre = _preflight(board_seams)

    def no_read(*, repo_dir=None):
        pytest.fail("run_board_engine read the board again")

    monkeypatch.setattr(board, "roots", no_read)
    board_seams.claims = []

    result = orchestrate.run_board_engine(pre)

    assert result["ok"] is True
    assert result["board"] is True
    assert result["levels"] == pre.levels_payload
    assert [entry["milestone_id"] for entry in result["milestones"]] == [a.id, b.id]
    assert _bases(board_seams) == {a.id: "main", b.id: "p00000001-integrate"}
    assert board_seams.claims == []
    assert pre.levels_payload == [
        {"level": 0, "milestones": [a.id]},
        {"level": 1, "milestones": [b.id]},
    ]
    assert pre.bases == {a.id: "main", b.id: "p00000001-integrate"}


def test_run_board_engine_gives_the_same_payload_run_board_gives(board_seams):
    """Review Focus 5: one board, both entry points, one payload."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    board_seams.runs.outcomes[a.id] = {"escalated": True, "run_id": "r"}

    engine_result = orchestrate.run_board_engine(_preflight(board_seams))
    board_seams.runs.calls.clear()
    board_result = _board(board_seams)

    assert engine_result == board_result
    assert engine_result["ok"] is False
    assert _by_id(engine_result)[b.id] == {
        "milestone_id": b.id,
        "status": "blocked",
        "blocked_by": [a.id],
    }
    assert board_seams.runs.called() == [a.id]


def test_run_board_engine_uses_the_preflights_lane_bound_and_forwards_run_arguments(
    board_seams,
):
    one, two = _board_milestone(1), _board_milestone(2)
    board_seams.cards = [one, two]
    pre = _preflight(board_seams, max_concurrent=3)
    runner_factory = object()
    driver = object()

    def clock() -> datetime:
        return datetime(2026, 10, 4, tzinfo=timezone.utc)

    orchestrate.run_board_engine(
        pre,
        commands=("git status",),
        allow_no_verification=True,
        runner_factory=runner_factory,
        driver=driver,
        clock=clock,
        control_interval=0.25,
    )

    assert sorted(board_seams.runs.called()) == sorted([one.id, two.id])
    slots = [kwargs["slots"] for _milestone, kwargs in board_seams.runs.calls]
    assert isinstance(slots[0], asyncio.Semaphore)
    assert all(semaphore is slots[0] for semaphore in slots)
    for milestone, kwargs in board_seams.runs.calls:
        assert kwargs["max_concurrent"] == 3
        assert kwargs["repo_dir"] == pre.root
        assert kwargs["branch_prefix"] == pre.prefixes[milestone]
        assert kwargs["base_branch"] == pre.bases[milestone]
        assert list(kwargs["commands"]) == ["git status"]
        assert kwargs["allow_no_verification"] is True
        assert kwargs["runner_factory"] is runner_factory
        assert kwargs["driver"] is driver
        assert kwargs["clock"] is clock
        assert kwargs["control_interval"] == 0.25


def test_run_board_engine_on_an_empty_preflight_starts_no_event_loop(board_seams, monkeypatch):
    """Review Focus 1: nothing open, so nothing to run and no loop to start."""
    board_seams.cards = [_board_milestone(1, status="done", done_children=True)]
    pre = _preflight(board_seams)

    def fail(*args: Any, **kwargs: Any) -> None:
        pytest.fail("run_board_engine started a run on an empty board")

    monkeypatch.setattr(orchestrate, "_run_board_async", fail)
    monkeypatch.setattr(orchestrate.asyncio, "run", fail)

    result = orchestrate.run_board_engine(pre)

    assert result == {"ok": True, "board": True, "levels": [], "milestones": []}
    assert board_seams.runs.calls == []


# ── _local_branch_exists (card 5b772688) ────────────────────────────────────


def _branch_repo(tmp_path: Path) -> Path:
    """A repo with one commit on `main`, branch `m-integrate`, tag
    `t-integrate` and remote-tracking ref `origin/r-integrate`."""
    root = tmp_path / "repo"
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
    _git(root, "branch", "m-integrate")
    _git(root, "tag", "t-integrate")
    _git(root, "update-ref", "refs/remotes/origin/r-integrate", "HEAD")
    return root


@pytest.mark.git
def test_local_branch_exists_answers_only_for_local_branches(tmp_path):
    exists = orchestrate._local_branch_exists(_branch_repo(tmp_path))

    assert exists("m-integrate") is True
    assert exists("main") is True
    assert exists("absent-integrate") is False
    assert exists("t-integrate") is False
    assert exists("r-integrate") is False


@pytest.mark.git
def test_local_branch_exists_propagates_a_git_error_that_is_not_an_answer(
    tmp_path, monkeypatch
):
    """A broken repository is not "the branch is missing": exit 128 propagates."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    exists = orchestrate._local_branch_exists(not_a_repo)

    with pytest.raises(worktree.GitError) as caught:
        exists("m-integrate")

    assert caught.value.exit_code != 1


# ── run pre-flight, recorded stage and engine seam (card 5daa944e) ──────────
#
# Unit tier: the FakeBoard (`fake_board`) answers every board call, the repo
# dir is `_resume_root`'s plain directory, `orchestrate.refresh_git` is
# patched, and `integrate_recorder` (autouse) stands in for Integrate. No git,
# brd or claude process ever starts.


def _preflight_milestone(root: Path, milestone: str | None, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "repo_dir": root,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "max_concurrent": 1,
        "clock": lambda: STARTED_AT,
    }
    kwargs.update(overrides)
    return orchestrate.preflight_milestone(milestone, **kwargs)


def _no_refresh(root: Path) -> None:
    pytest.fail("refresh_git ran before a pre-flight refusal")


def test_preflight_milestone_refuses_a_blocker_cycle_before_refreshing_git(
    tmp_path, monkeypatch, fake_board
):
    """Through the board a sibling cycle is refused even earlier, by the
    census (`CensusOrderError`), so the census is patched to hand
    `plan_levels` the cyclic stories of test_plan_levels_refuses_a_blocker_cycle_before_any_geometry."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = _add_card(root, "Milestone 3: orchestration")
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    monkeypatch.setattr(
        census,
        "flatten_milestone",
        lambda node: census.Census(milestone_title=node.title, stories=[a, b]),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(dag.DependencyCycleError):
        _preflight_milestone(root, milestone)

    assert _run_dirs() == []


def test_preflight_milestone_refuses_an_ambiguous_needle_before_refreshing_git(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    _add_card(root, "Milestone 3: orchestration")
    _add_card(root, "Milestone 3: integration")
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(census.MilestoneNotFoundError, match="ambiguous milestone"):
        _preflight_milestone(root, "Milestone 3")

    assert _run_dirs() == []


def test_preflight_milestone_refuses_a_claimed_key_before_refreshing_git(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    key = f"card:{shape['milestone']}"
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(cli.ClaimedError) as caught:
        _preflight_milestone(root, shape["milestone"])

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert _run_dirs() == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]


def test_a_fresh_milestone_preflight_refreshes_git_once_after_every_refusal(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 2})
    events: list[str] = []
    real_refuse = cli.refuse_claimed

    def refuse(at: Path, keys: Any, *, run_id: str | None = None) -> None:
        events.append("refuse_claimed")
        real_refuse(at, keys, run_id=run_id)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: events.append(f"refresh_git:{at}"))
    driver = FakeDriver()

    pre = _preflight_milestone(root, shape["milestone"], driver=driver)

    assert events == ["refuse_claimed", f"refresh_git:{root}"]
    assert pre.root == root
    assert pre.resumed is None
    assert pre.milestone_card.id == shape["milestone"]
    assert pre.run_id == runs.mint_run_id(shape["milestone"], STARTED_AT)
    assert (pre.run_record.id, pre.run_record.status) == (pre.run_id, "started")
    assert pre.run_record.workflow == orchestrate.MILESTONE_WORKFLOW
    assert pre.run_record.milestone_id == shape["milestone"]
    assert pre.run_record.config.max_concurrent_stories == 1
    assert (pre.base_branch, pre.branch_prefix, pre.max_concurrent) == ("main", PREFIX, 1)
    assert pre.keys == orchestrate.milestone_claims(shape["milestone"], pre.plan.stories, PREFIX)
    assert pre.drive is driver
    assert driver.calls == []
    assert _run_dirs() == []
    assert _run_ids(root) == []


# ── story pre-flight (card 371a79c9) ────────────────────────────────────────
#
# Unit tier, as the milestone pre-flight above: the FakeBoard answers every
# board call, `orchestrate.refresh_git` is patched, and the done-blocker
# branch lookup is the stubbed `orchestrate._local_branch_exists`.


def _preflight_story(root: Path, story: str, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "repo_dir": root,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "clock": lambda: STARTED_AT,
    }
    kwargs.update(overrides)
    return orchestrate.preflight_story(story, **kwargs)


def _seed_story(
    fake: Any,
    milestone: str,
    title: str,
    *,
    subtasks: int = 1,
    status: str = "todo",
    subtask_status: str | None = None,
    blocked_by: tuple[str, ...] | list[str] = (),
) -> tuple[str, list[str]]:
    """One story under `milestone` holding a `blocked_by` chain of subtasks.

    Returns `(story id, subtask ids in chain order)`. Subtasks default to
    `done` under a finished story, else `todo`.
    """
    if subtask_status is None:
        subtask_status = "done" if census.is_finished(status) else "todo"
    story = fake.add_card(title, parent_id=milestone, status=status, blocked_by=blocked_by)
    chain: list[str] = []
    for n in range(1, subtasks + 1):
        chain.append(
            fake.add_card(
                f"{title} subtask {n}",
                parent_id=story,
                status=subtask_status,
                blocked_by=chain[-1:],
            )
        )
    return story, chain


def _branches(monkeypatch, present: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Stub `orchestrate._local_branch_exists`: only `present` exist locally.

    Returns the list every branch the pre-flight asks about is appended to.
    """
    asked: list[str] = []

    def factory(root: Path) -> Callable[[str], bool]:
        def exists(branch: str) -> bool:
            asked.append(branch)
            return branch in present

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", factory)
    return asked


def _root_of(pre: Any, story_id: str) -> dag.RootPlan:
    """`dag.story_root` of `story_id` over the pre-flight's restricted plan."""
    by_id = {story.id: story for story in pre.plan.stories}
    return dag.story_root(by_id[story_id], by_id, PREFIX, "main")


def test_preflight_story_selects_a_story_by_exact_id_under_its_milestone(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    _seed_story(fake_board, milestone, "Story A: rows")
    story, _subtasks = _seed_story(fake_board, milestone, "Story B: cols", subtasks=2)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    driver = FakeDriver()

    pre = _preflight_story(root, story, driver=driver)

    assert pre.root == root
    assert pre.resumed is None
    assert pre.milestone_card.id == milestone
    assert pre.plan.milestone_title == "Milestone 3: orchestration"
    assert pre.plan.stories[-1].id == story
    assert pre.run_id == runs.mint_run_id(story, STARTED_AT)
    assert pre.run_id.endswith(dag.short_id(story))
    assert (pre.run_record.id, pre.run_record.status) == (pre.run_id, "started")
    assert pre.run_record.workflow == orchestrate.MILESTONE_WORKFLOW
    assert pre.run_record.started_at == STARTED_AT
    assert pre.run_record.repo_dir == root
    assert pre.run_record.milestone_id == milestone
    assert pre.run_record.config.story_id == story
    assert pre.run_record.config.max_concurrent_stories == 1
    assert (pre.run_record.base_branch, pre.run_record.branch_prefix) == ("main", PREFIX)
    assert (pre.base_branch, pre.branch_prefix, pre.max_concurrent) == ("main", PREFIX, 1)
    assert pre.drive is driver
    assert driver.calls == []


def test_preflight_story_selects_a_story_by_title_piece_with_the_default_driver(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    _seed_story(fake_board, milestone, "Story A: rows")
    story, _subtasks = _seed_story(fake_board, milestone, "Story B: cols")
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, "cols")

    assert pre.plan.stories[-1].id == story
    assert pre.run_record.config.story_id == story
    assert pre.drive is cli.drive_subtask_async


@pytest.mark.parametrize(
    ("needle", "message"),
    [
        ("ambiguous", "ambiguous story"),
        ("milestone", "is a milestone, not a story"),
        ("subtask", "is a subtask, not a story"),
    ],
)
def test_preflight_story_refuses_a_needle_that_is_not_one_story_before_refreshing_git(
    tmp_path, monkeypatch, fake_board, needle, message
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    _seed_story(fake_board, milestone, "Story A: rows")
    _story, (subtask,) = _seed_story(fake_board, milestone, "Story B: cols")
    typed = {"ambiguous": "Story", "milestone": milestone, "subtask": subtask}[needle]
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(errors.StoryNotFoundError, match=message):
        _preflight_story(root, typed)

    assert _run_dirs() == []


def test_preflight_story_refuses_a_blocker_cycle_elsewhere_in_the_milestone_before_refreshing_git(
    tmp_path, monkeypatch, fake_board
):
    """As for a milestone run, the whole milestone is cycle-checked, even when
    the selected story is outside the cycle; the census is patched as in
    test_preflight_milestone_refuses_a_blocker_cycle_before_refreshing_git."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    story, (s1,) = _seed_story(fake_board, milestone, "Story S: cols")
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    selected = census.StoryPlan(
        story, "Story S: cols", "todo", [], [census.SubtaskPlan(s1, "Story S: cols subtask 1", "todo")]
    )
    monkeypatch.setattr(
        census,
        "flatten_milestone",
        lambda node: census.Census(milestone_title=node.title, stories=[a, b, selected]),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(dag.DependencyCycleError):
        _preflight_story(root, story)

    assert _run_dirs() == []


def test_preflight_story_restricts_the_plan_levels_and_tips_to_the_selected_story(
    tmp_path, monkeypatch, fake_board
):
    """Unrelated sibling stories stay out of the plan, the waves and the tips."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    _seed_story(fake_board, milestone, "Story A: rows")
    story, (s1, s2) = _seed_story(fake_board, milestone, "Story B: cols", subtasks=2)
    _seed_story(fake_board, milestone, "Story C: cells")
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [planned.id for planned in pre.plan.stories] == [story]
    assert [[planned.story.id for planned in level] for level in pre.levels] == [[story]]
    assert [subtask.id for subtask in pre.levels[0][0].remaining] == [s1, s2]
    assert pre.tips == [{"story": story, "tip": _branch(root, s2)}]


def test_preflight_story_roots_a_story_with_no_blockers_on_the_base_branch(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    story, (s1, s2) = _seed_story(fake_board, milestone, "Story A: rows", subtasks=2)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert _root_of(pre, story) == dag.RootPlan("base", "main", ())
    assert pre.levels[0][0].bases[s1] == "main"
    assert pre.levels[0][0].bases[s2] == _branch(root, s1)


@pytest.mark.parametrize(
    ("status", "subtask_status"),
    [("done", "done"), ("merged", "merged"), ("todo", "done")],
)
def test_preflight_story_returns_nothing_to_run_for_a_finished_story(
    tmp_path, monkeypatch, fake_board, status, subtask_status
):
    """A finished story, or one whose subtasks are all done, is not an error:
    the pre-flight returns with no wave to run."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    story, _subtasks = _seed_story(
        fake_board, milestone, "Story A: rows", subtasks=2,
        status=status, subtask_status=subtask_status,
    )
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert pre.levels == []
    assert [planned.id for planned in pre.plan.stories] == [story]
    assert pre.keys == [f"card:{milestone}", f"card:{story}"]


@pytest.mark.parametrize("status", ["canceled", "archived"])
def test_preflight_story_returns_an_empty_plan_for_an_out_of_play_story(
    tmp_path, monkeypatch, fake_board, status
):
    """The census drops an out-of-play story, so there is nothing to run, and
    the run still claims the milestone and story cards."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    story, _subtasks = _seed_story(fake_board, milestone, "Story A: rows", status=status)
    _seed_story(fake_board, milestone, "Story B: cols")
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert pre.plan.stories == []
    assert pre.levels == []
    assert pre.tips == []
    assert pre.keys == [f"card:{milestone}", f"card:{story}"]
    assert pre.run_record.config.story_id == story


def test_preflight_story_claims_are_story_claims_and_never_the_integration_branch(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    story, (s1, s2) = _seed_story(fake_board, milestone, "Story A: rows", subtasks=2)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert pre.keys == orchestrate.story_claims(milestone, pre.plan.stories[-1], PREFIX)
    assert pre.keys == [
        f"card:{milestone}",
        f"card:{story}",
        f"card:{s1}",
        f"card:{s2}",
        f"branch:{_branch(root, s1)}",
        f"branch:{_branch(root, s2)}",
    ]
    assert f"branch:{INTEGRATION_BRANCH}" not in pre.keys


@pytest.mark.parametrize("claimed", ["milestone", "subtask", "branch"])
def test_preflight_story_is_refused_while_a_live_run_claims_one_of_its_keys(
    tmp_path, monkeypatch, fake_board, claimed
):
    """`milestone` is a milestone run of the parent; `subtask` and `branch`
    are a card run on one of the story's subtasks. Refused before git is
    refreshed and before any store or run directory exists."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    story, (s1,) = _seed_story(fake_board, milestone, "Story A: rows")
    key = {
        "milestone": f"card:{milestone}",
        "subtask": f"card:{s1}",
        "branch": f"branch:{_branch(root, s1)}",
    }[claimed]
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(cli.ClaimedError) as caught:
        _preflight_story(root, story)

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert _run_dirs() == []
    assert _run_ids(root) == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]


def test_a_story_preflight_refreshes_git_once_after_every_refusal(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    story, _subtasks = _seed_story(fake_board, milestone, "Story A: rows", subtasks=2)
    events: list[str] = []
    real_refuse = cli.refuse_claimed

    def refuse(at: Path, keys: Any, *, run_id: str | None = None) -> None:
        events.append("refuse_claimed")
        real_refuse(at, keys, run_id=run_id)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: events.append(f"refresh_git:{at}"))

    pre = _preflight_story(root, story)

    assert events == ["refuse_claimed", f"refresh_git:{root}"]
    assert pre.run_record.config.story_id == story
    assert _run_dirs() == []
    assert _run_ids(root) == []


def test_preflight_story_roots_on_a_done_blockers_tip_when_its_branch_exists(
    tmp_path, monkeypatch, fake_board
):
    """The blocker is carried in the plan, with its own edges cut, so
    `dag.story_root` can read its tip; it is finished, so it gets no wave.
    Only the selected story's direct blockers are looked up."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    earlier, _ = _seed_story(fake_board, milestone, "Story E: earlier", status="done")
    blocker, (_b1, b2) = _seed_story(
        fake_board, milestone, "Story A: rows", subtasks=2, status="done", blocked_by=[earlier]
    )
    story, (s1,) = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    tip = _branch(root, b2)
    asked = _branches(monkeypatch, {tip})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [(planned.id, planned.blocked_by) for planned in pre.plan.stories] == [
        (blocker, []),
        (story, [blocker]),
    ]
    assert _root_of(pre, story) == dag.RootPlan("tip", tip, (blocker,))
    assert [[planned.story.id for planned in level] for level in pre.levels] == [[story]]
    assert pre.levels[0][0].bases[s1] == tip
    assert pre.tips == [{"story": story, "tip": _branch(root, s1)}]
    assert pre.keys == [f"card:{milestone}", f"card:{story}", f"card:{s1}", f"branch:{_branch(root, s1)}"]
    assert asked == [tip]


def test_preflight_story_roots_on_a_merged_base_for_two_done_blockers_with_tips(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    a, (a1,) = _seed_story(fake_board, milestone, "Story A: rows", status="done")
    b, (b1,) = _seed_story(fake_board, milestone, "Story B: cells", status="done")
    story, (s1,) = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[b, a])
    _branches(monkeypatch, {_branch(root, a1), _branch(root, b1)})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [planned.id for planned in pre.plan.stories] == [a, b, story]
    selected = pre.plan.stories[-1]
    assert selected.blocked_by == [a, b]
    merged = dag.base_branch_name(PREFIX, selected)
    assert _root_of(pre, story) == dag.RootPlan("merged", merged, (a, b))
    assert pre.levels[0][0].bases[s1] == merged


def test_preflight_story_drops_a_done_blocker_whose_tip_branch_is_absent(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, (b1,) = _seed_story(fake_board, milestone, "Story A: rows", status="done")
    story, (s1,) = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    asked = _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [(planned.id, planned.blocked_by) for planned in pre.plan.stories] == [(story, [])]
    assert _root_of(pre, story) == dag.RootPlan("base", "main", ())
    assert pre.levels[0][0].bases[s1] == "main"
    assert asked == [_branch(root, b1)]


def test_preflight_story_drops_a_merged_blocker_without_a_branch_lookup(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, (b1,) = _seed_story(fake_board, milestone, "Story A: rows", status="merged")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    asked = _branches(monkeypatch, {_branch(root, b1)})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [planned.id for planned in pre.plan.stories] == [story]
    assert _root_of(pre, story) == dag.RootPlan("base", "main", ())
    assert asked == []


def test_preflight_story_counts_only_kept_blockers_toward_a_merged_base(
    tmp_path, monkeypatch, fake_board
):
    """Two done blockers, one tip present: a dropped blocker does not count
    toward "two or more", so the story roots on the kept one's tip."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    a, (a1,) = _seed_story(fake_board, milestone, "Story A: rows", status="done")
    b, (b1,) = _seed_story(fake_board, milestone, "Story B: cells", status="done")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[a, b])
    asked = _branches(monkeypatch, {_branch(root, a1)})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [planned.id for planned in pre.plan.stories] == [a, story]
    assert _root_of(pre, story) == dag.RootPlan("tip", _branch(root, a1), (a,))
    assert asked == [_branch(root, a1), _branch(root, b1)]


def test_preflight_story_ignores_an_out_of_play_blocker(tmp_path, monkeypatch, fake_board):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, _ = _seed_story(fake_board, milestone, "Story A: rows", status="canceled")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    asked = _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [planned.id for planned in pre.plan.stories] == [story]
    assert _root_of(pre, story) == dag.RootPlan("base", "main", ())
    assert asked == []


@pytest.mark.parametrize("status", ["todo", "started"])
def test_preflight_story_refuses_a_story_with_an_open_blocker_before_anything_is_written(
    tmp_path, monkeypatch, fake_board, status
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, _ = _seed_story(fake_board, milestone, "Story A: rows", status=status)
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    asked = _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    monkeypatch.setattr(
        cli,
        "refuse_claimed",
        lambda *args, **kwargs: pytest.fail("claims checked before the blocker refusal"),
    )

    with pytest.raises(errors.StoryBlockedError) as caught:
        _preflight_story(root, story)

    assert caught.value.story_id == story
    assert caught.value.blockers == (blocker,)
    assert f'"Story A: rows" ({blocker})' in str(caught.value)
    assert str(caught.value).endswith("run them first, or run the milestone")
    assert asked == []
    assert _run_dirs() == []
    assert _claim_rows(root) == []


def test_preflight_story_refuses_a_blocker_brd_reports_as_blocked(
    tmp_path, monkeypatch, fake_board
):
    """brd derives `blocked` for a todo story with an unfinished blocker; the
    census reads it as `todo`, so it is open. Only the direct blocker is named."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    first, _ = _seed_story(fake_board, milestone, "Story Z: first")
    blocker, _ = _seed_story(fake_board, milestone, "Story A: rows", blocked_by=[first])
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    assert board.show(blocker, repo_dir=root).status == "blocked"
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(errors.StoryBlockedError) as caught:
        _preflight_story(root, story)

    assert caught.value.blockers == (blocker,)
    assert _run_dirs() == []


def test_preflight_story_names_every_open_blocker_and_looks_up_no_branch(
    tmp_path, monkeypatch, fake_board
):
    """Two open blockers are both named, in census order; a done blocker
    beside them is never looked up, because the open check runs first."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    a, _ = _seed_story(fake_board, milestone, "Story A: rows", status="started")
    done, _ = _seed_story(fake_board, milestone, "Story D: done", status="done")
    b, _ = _seed_story(fake_board, milestone, "Story B: cells")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[b, done, a])
    asked = _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(errors.StoryBlockedError) as caught:
        _preflight_story(root, story)

    assert caught.value.blockers == (a, b)
    assert f'"Story A: rows" ({a})' in str(caught.value)
    assert f'"Story B: cells" ({b})' in str(caught.value)
    assert asked == []
    assert _run_dirs() == []
    assert _claim_rows(root) == []


def test_preflight_story_ignores_a_blocker_outside_the_milestone(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    other = fake_board.add_card("Milestone 4: later")
    outside, _ = _seed_story(fake_board, other, "Story X: elsewhere")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[outside])
    asked = _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [(planned.id, planned.blocked_by) for planned in pre.plan.stories] == [(story, [])]
    assert _root_of(pre, story) == dag.RootPlan("base", "main", ())
    assert asked == []


@pytest.mark.parametrize(
    ("status", "subtask_status"), [("done", "done"), ("todo", "done")]
)
def test_preflight_story_with_nothing_to_run_is_not_refused_for_an_open_blocker(
    tmp_path, monkeypatch, fake_board, status, subtask_status
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, _ = _seed_story(fake_board, milestone, "Story A: rows")
    story, _ = _seed_story(
        fake_board, milestone, "Story S: cols",
        status=status, subtask_status=subtask_status, blocked_by=[blocker],
    )
    asked = _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert pre.levels == []
    assert [(planned.id, planned.blocked_by) for planned in pre.plan.stories] == [(story, [])]
    assert asked == []


# Review focus (see the plan's Review Focus section).


def test_preflight_story_counts_a_blocker_listed_twice_once(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, (b1,) = _seed_story(fake_board, milestone, "Story A: rows", status="done")
    story, _ = _seed_story(
        fake_board, milestone, "Story S: cols", blocked_by=[blocker, blocker]
    )
    _branches(monkeypatch, {_branch(root, b1)})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [planned.id for planned in pre.plan.stories] == [blocker, story]
    assert _root_of(pre, story) == dag.RootPlan("tip", _branch(root, b1), (blocker,))


def test_preflight_story_reads_blocker_statuses_case_insensitively(
    tmp_path, monkeypatch, fake_board
):
    """`Done` with its tip present is kept; `MERGED` is dropped unasked."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    done, (d1,) = _seed_story(
        fake_board, milestone, "Story A: rows", status="Done", subtask_status="done"
    )
    merged, (m1,) = _seed_story(
        fake_board, milestone, "Story B: cells", status="MERGED", subtask_status="merged"
    )
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[done, merged])
    asked = _branches(monkeypatch, {_branch(root, d1), _branch(root, m1)})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [planned.id for planned in pre.plan.stories] == [done, story]
    assert _root_of(pre, story) == dag.RootPlan("tip", _branch(root, d1), (done,))
    assert asked == [_branch(root, d1)]


def test_preflight_story_drops_a_done_blocker_with_no_subtasks_without_a_lookup(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, _ = _seed_story(fake_board, milestone, "Story A: rows", subtasks=0, status="done")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    asked = _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [planned.id for planned in pre.plan.stories] == [story]
    assert _root_of(pre, story) == dag.RootPlan("base", "main", ())
    assert asked == []


def test_preflight_story_propagates_a_git_error_from_the_done_rule_lookup(
    tmp_path, monkeypatch, fake_board
):
    """A broken repository is a refusal, never a silent base-branch root."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, _ = _seed_story(fake_board, milestone, "Story A: rows", status="done")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])

    def broken(at: Path) -> Callable[[str], bool]:
        def exists(branch: str) -> bool:
            raise worktree.GitError(
                "fatal: not a git repository", argv=["rev-parse"], exit_code=128
            )

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", broken)
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(worktree.GitError):
        _preflight_story(root, story)

    assert _run_dirs() == []


def test_preflight_story_judges_only_direct_blockers(tmp_path, monkeypatch, fake_board):
    """A done blocker that is itself blocked by an open story still roots the
    story on its tip: only the selected story's own blockers are judged."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    first, _ = _seed_story(fake_board, milestone, "Story Z: first")
    blocker, (b1,) = _seed_story(
        fake_board, milestone, "Story A: rows", status="done", blocked_by=[first]
    )
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    _branches(monkeypatch, {_branch(root, b1)})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    pre = _preflight_story(root, story)

    assert [(planned.id, planned.blocked_by) for planned in pre.plan.stories] == [
        (blocker, []),
        (story, [blocker]),
    ]
    assert _root_of(pre, story) == dag.RootPlan("tip", _branch(root, b1), (blocker,))


def _close_snapshots(monkeypatch) -> list[tuple[int, int]]:
    """Patch `Store.close` to record `(claims, leases)` its run still holds as it closes.

    `(0, 0)` means the claims and the lease were released before the store
    closed. Counted over the closing store's own connection, before the real
    close runs.
    """
    seen: list[tuple[int, int]] = []
    real_close = store_module.Store.close

    def close(self) -> None:
        conn = self.connection
        claims = conn.execute(
            "SELECT COUNT(*) FROM run_claims WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        leases = conn.execute(
            "SELECT COUNT(*) FROM run_leases WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        seen.append((claims, leases))
        real_close(self)

    monkeypatch.setattr(store_module.Store, "close", close)
    return seen


def _seam_resume_board(fake_board) -> tuple[str, str, str]:
    """A milestone whose short id is RESUME_RUN_ID's (`00000009`), one story, one subtask."""
    milestone = fake_board.add_card("Milestone 9: resume", card_id=_plan_id(9))
    story = fake_board.add_card("Story R", parent_id=milestone)
    subtask = fake_board.add_card("r1: the one subtask", parent_id=story)
    return milestone, story, subtask


def test_inside_recorded_milestone_run_the_plan_is_recorded_and_leased_but_nothing_driven(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    story = shape["stories"]["A"]
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    monkeypatch.setattr(
        comments,
        "flush",
        lambda *args, **kwargs: pytest.fail("comments.flush ran in the recorded stage"),
    )
    driver = FakeDriver()
    pre = _preflight_milestone(root, shape["milestone"], driver=driver)

    with orchestrate.recorded_milestone_run(pre) as recorded:
        assert recorded.run_id == pre.run_id
        run = recorded.store.load_run(recorded.run_id)
        assert run is not None
        assert _statuses(run) == {"run": "started", story: "pending", a1: "pending", a2: "pending"}
        assert run.milestone_id == shape["milestone"]
        assert _held_keys(root, recorded.run_id) == _expected_claims(shape["milestone"], [a1, a2])
        assert list(recorded.rows) == [story]
        assert recorded.checkpoints is None
        assert driver.calls == []


def test_leaving_recorded_milestone_run_on_an_error_releases_claims_and_lease_before_closing(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    closes = _close_snapshots(monkeypatch)
    pre = _preflight_milestone(root, shape["milestone"], driver=FakeDriver())

    with pytest.raises(RuntimeError, match="engine never started"):
        with orchestrate.recorded_milestone_run(pre):
            raise RuntimeError("engine never started")

    assert closes == [(0, 0)]
    assert _claim_rows(root) == []
    assert _held_keys(root, pre.run_id) == []
    assert _load(root, pre.run_id).status == "started"


def test_a_resumed_milestone_preflight_leaves_refresh_git_to_the_recorded_stage(
    tmp_path, monkeypatch, fake_board
):
    """A resume refreshes git under its own lease (X5), not in pre-flight,
    and takes its prefix, base and bound from the recorded run."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone, _story, subtask = _seam_resume_board(fake_board)
    _record_resume_run(root)
    refreshed: list[list[str]] = []
    monkeypatch.setattr(
        orchestrate, "refresh_git", lambda at: refreshed.append(_held_keys(root, RESUME_RUN_ID))
    )

    pre = orchestrate.preflight_milestone(None, repo_dir=root, resume_run_id=RESUME_RUN_ID)

    assert refreshed == []
    assert (pre.run_id, pre.base_branch, pre.branch_prefix, pre.max_concurrent) == (
        RESUME_RUN_ID,
        "main",
        PREFIX,
        3,
    )
    assert pre.resumed is not None
    assert (pre.run_record.status, pre.run_record.milestone_id) == ("started", milestone)
    with orchestrate.recorded_milestone_run(pre) as recorded:
        assert recorded.checkpoints == {}
        assert refreshed == [_expected_claims(milestone, [subtask])]
    assert len(refreshed) == 1


def test_a_resumed_milestone_keeps_its_recorded_stacked_base(tmp_path, monkeypatch, fake_board):
    """Card 5b772688 (parent L69-70): `am resume` keeps the base the run was
    stacked on, whatever base the caller passes; `milestone_bases` is not consulted."""
    root = _resume_root(tmp_path, monkeypatch)
    _seam_resume_board(fake_board)
    _record_resume_run(root, base_branch="pstack-integrate")
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    def no_bases(*args, **kwargs):
        raise AssertionError("resume must not re-derive the base")

    monkeypatch.setattr(orchestrate, "milestone_bases", no_bases)

    pre = orchestrate.preflight_milestone(
        None, repo_dir=root, base_branch="main", resume_run_id=RESUME_RUN_ID
    )

    assert pre.base_branch == "pstack-integrate"


def test_a_resume_checkpoint_under_another_digest_is_refused_before_the_lease(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    _milestone_id, _story, subtask = _seam_resume_board(fake_board)
    _record_resume_run(root)
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, subtask, "parked", phase="plan", digest="saved-under-another-task")
    finally:
        opened.close()
    monkeypatch.setattr(
        orchestrate,
        "refresh_git",
        lambda at: pytest.fail("refresh_git ran before the checkpoint refusal"),
    )
    pre = orchestrate.preflight_milestone(None, repo_dir=root, resume_run_id=RESUME_RUN_ID)

    with pytest.raises(runs.CheckpointMismatchError):
        with orchestrate.recorded_milestone_run(pre):
            pytest.fail("the recorded stage yielded past a stale checkpoint")

    assert _claim_rows(root) == []
    assert _held_keys(root, RESUME_RUN_ID) == []
    assert _load(root, RESUME_RUN_ID).status == "escalated"


def test_the_milestone_engine_drives_a_recorded_run_under_its_lease(
    tmp_path, monkeypatch, fake_board, integrate_recorder
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    story = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    handed: dict[str, Any] = {}

    real_supervise = orchestrate.supervise

    async def spying_supervise(plan, **kwargs):
        handed["lease_token"] = kwargs["lease_token"]
        return await real_supervise(plan, **kwargs)

    real_controlled = control.controlled

    async def spying_controlled(work, **kwargs):
        handed["lease"] = kwargs["lease"]
        return await real_controlled(work, **kwargs)

    monkeypatch.setattr(orchestrate, "supervise", spying_supervise)
    monkeypatch.setattr(control, "controlled", spying_controlled)
    driver = FakeDriver()
    pre = _preflight_milestone(root, shape["milestone"], driver=driver)

    with orchestrate.recorded_milestone_run(pre) as recorded:
        result = asyncio.run(orchestrate.run_milestone_engine(pre, recorded))
        lease = recorded.lease

    assert handed["lease"] is lease
    assert handed["lease_token"] == lease.token
    assert [call["card"] for call in driver.calls] == [a1]
    assert [call["run_id"] for call in integrate_recorder.calls] == [pre.run_id]
    assert result == {
        "done": True,
        "run_id": pre.run_id,
        "levels": [{"level": 0, "stories": [story]}],
        "completed": [a1],
        "tips": [{"story": story, "tip": _branch(root, a1)}],
        "warnings": [],
        "integrated": _integrated(root, [story]),
    }
    assert _load(root, pre.run_id).status == "done"
    assert _claim_rows(root) == []


def test_a_crashing_milestone_engine_still_releases_its_lease_before_closing(
    tmp_path, monkeypatch, fake_board, integrate_recorder
):
    """Spec: a crash in the engine still propagates, still releases the lease
    and claims, and still closes the store. A characterization pin: it passes
    before the split and must keep passing after it."""
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    integrate_recorder.outcome = RuntimeError("integrate bug")
    closes = _close_snapshots(monkeypatch)

    with pytest.raises(RuntimeError, match="integrate bug"):
        _run(root, shape["milestone"], FakeDriver())

    assert closes == [(0, 0)]
    assert _claim_rows(root) == []


def test_run_milestone_hands_the_engine_the_lease_of_the_recorded_stage(
    tmp_path, monkeypatch, fake_board, integrate_recorder
):
    """`_run_milestone_async` composes the three stages: the run it reports is
    the one the recorded stage opened, and the walk and Integrate ran on that
    stage's store under that stage's lease."""
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    handed: dict[str, Any] = {}

    real_recorded = orchestrate.recorded_milestone_run

    @contextmanager
    def spying_recorded(pre):
        with real_recorded(pre) as recorded:
            handed["recorded"] = recorded
            yield recorded

    real_controlled = control.controlled

    async def spying_controlled(work, **kwargs):
        handed["controlled"] = kwargs["lease"]
        return await real_controlled(work, **kwargs)

    monkeypatch.setattr(orchestrate, "recorded_milestone_run", spying_recorded)
    monkeypatch.setattr(control, "controlled", spying_controlled)
    driver = FakeDriver()

    result = _run(root, shape["milestone"], driver)

    recorded = handed["recorded"]
    assert handed["controlled"] is recorded.lease
    assert recorded.run_id == result["run_id"] == runs.mint_run_id(shape["milestone"], STARTED_AT)
    assert [call["card"] for call in driver.calls] == [a1]
    assert [call["store"] for call in integrate_recorder.calls] == [recorded.store]
    assert result["done"] is True
    assert _claim_rows(root) == []


# ── am run --milestone --detach (card aff9fdbf) ─────────────────────────────
#
# Unit tier, like the seam tests above: FakeBoard, a plain repo dir,
# `refresh_git` patched, `integrate_recorder` autouse, and `_FakeDetacher`
# instead of `detach.fork_detacher`. Nothing forks.

FAKE_CHILD_PID = 424242
"""The pid `_FakeDetacher` reports; no such child exists."""

detach_runner = CliRunner()


class _FakeDetacher:
    """Stands in for `detach.fork_detacher`: records the call, starts nothing."""

    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.events: list[str] = []
        self.body: Any = None

    def __call__(self, body: Any, log: Path) -> detach.Spawned:
        self.calls.append(log)
        self.body = body
        return detach.Spawned(
            pid=FAKE_CHILD_PID,
            go=lambda: self.events.append("go"),
            abort=lambda: self.events.append("abort"),
        )


def _milestone_run_args(root: Path, needle: str, *extra: str) -> list[str]:
    return [
        "run",
        "--milestone",
        needle,
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        PREFIX,
        "--allow-no-verification",
        *extra,
    ]


def _no_take_lease(self, **kwargs: Any) -> Any:
    pytest.fail("the detached child took a new lease instead of adopting its own")


def test_an_ambiguous_milestone_is_refused_the_same_with_or_without_detach(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    _add_card(root, "Milestone 3: orchestration")
    _add_card(root, "Milestone 3: integration")
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    foreground = detach_runner.invoke(cli.app, _milestone_run_args(root, "Milestone 3"))
    detached = detach_runner.invoke(
        cli.app, _milestone_run_args(root, "Milestone 3", "--detach")
    )

    assert (foreground.exit_code, detached.exit_code) == (cli.EXIT_ERROR, cli.EXIT_ERROR)
    assert json.loads(detached.stdout) == json.loads(foreground.stdout)
    assert json.loads(detached.stdout)["error"]["type"] == "MilestoneNotFoundError"
    assert fake.calls == []
    assert _run_dirs() == []


def test_a_blocker_cycle_is_refused_the_same_with_or_without_detach(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = _add_card(root, "Milestone 3: orchestration")
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    monkeypatch.setattr(
        census,
        "flatten_milestone",
        lambda node: census.Census(milestone_title=node.title, stories=[a, b]),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    foreground = detach_runner.invoke(cli.app, _milestone_run_args(root, milestone))
    detached = detach_runner.invoke(cli.app, _milestone_run_args(root, milestone, "--detach"))

    assert (foreground.exit_code, detached.exit_code) == (cli.EXIT_ERROR, cli.EXIT_ERROR)
    assert json.loads(detached.stdout) == json.loads(foreground.stdout)
    assert json.loads(detached.stdout)["error"]["type"] == "DependencyCycleError"
    assert fake.calls == []
    assert _run_dirs() == []


def test_a_detached_milestone_run_records_and_leases_the_plan_and_drives_nothing_here(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    story = shape["stories"]["A"]
    refreshed: list[Path] = []
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: refreshed.append(at))
    monkeypatch.setattr(cli, "drive_subtask_async", _forbidden("drive_subtask_async"))
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = detach_runner.invoke(
        cli.app, _milestone_run_args(root, shape["milestone"], "--detach")
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert set(data) == {"run_id", "pid", "log", "detached"}
    assert (data["pid"], data["detached"]) == (FAKE_CHILD_PID, True)
    run_id = data["run_id"]
    assert _run_ids(root) == [run_id]
    assert _statuses(_load(root, run_id)) == {
        "run": "started",
        story: "pending",
        a1: "pending",
        a2: "pending",
    }
    lease = _lease(root, run_id)
    assert lease is not None and lease.pid == FAKE_CHILD_PID
    assert _held_keys(root, run_id) == _expected_claims(shape["milestone"], [a1, a2])
    log = Path(data["log"])
    assert log == paths.run_dir(run_id) / detach.RUN_LOG_NAME
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600
    assert fake.calls == [log]
    assert fake.events == ["go"]
    assert refreshed == [root]


def test_the_detached_milestone_child_drives_the_recorded_plan_and_reports_it(
    tmp_path, monkeypatch, fake_board, integrate_recorder
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    story = shape["stories"]["A"]
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    fake = _FakeDetacher()
    driver = FakeDriver()

    data = orchestrate.detach_milestone(
        shape["milestone"],
        repo_dir=root,
        base_branch="main",
        branch_prefix=PREFIX,
        detacher=fake,
        allow_no_verification=True,
        driver=driver,
        clock=lambda: STARTED_AT,
        control_interval=0.01,
    )

    run_id = data["run_id"]
    assert run_id == runs.mint_run_id(shape["milestone"], STARTED_AT)
    assert driver.calls == []
    monkeypatch.setattr(store_module.Store, "take_lease", _no_take_lease)
    closes = _close_snapshots(monkeypatch)

    fake.body()

    report = paths.run_dir(run_id) / detach.REPORT_NAME
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    expected = {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": [story]}],
        "completed": [a1],
        "tips": [{"story": story, "tip": _branch(root, a1)}],
        "warnings": [],
        "integrated": _integrated(root, [story]),
    }
    assert json.loads(report.read_text(encoding="utf-8")) == json.loads(
        cli.render(cli.ok_envelope(expected))
    )
    assert [call["card"] for call in driver.calls] == [a1]
    assert [call["run_id"] for call in integrate_recorder.calls] == [run_id]
    assert _load(root, run_id).status == "done"
    assert closes == [(0, 0)]
    assert _lease(root, run_id) is None
    assert _claim_rows(root) == []


# ── am run --board --detach (card 03f027ea) ─────────────────────────────────
#
# Unit tier: `board_seams` fakes the board, the claim check, the milestone
# runs and the branch check, and `_FakeDetacher` forks nothing; a test that
# needs the child runs `fake.body()` inline. The real fork is
# `tests/e2e/test_detached_run.py`'s.

BOARD_DETACH_AT = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
"""The fixed clock: its stamp is `20261004T090000Z`."""


def _detach_board(seams: BoardSeams, detacher: Any, **overrides: Any) -> dict[str, Any]:
    """`orchestrate.detach_board` with `_board`'s defaults and the fixed clock."""
    kwargs: dict[str, Any] = {
        "repo_dir": seams.root,
        "base_branch": "main",
        "branch_prefix_of": _prefix_of,
        "max_concurrent": 2,
        "detacher": detacher,
        "clock": lambda: BOARD_DETACH_AT,
    }
    kwargs.update(overrides)
    return orchestrate.detach_board(**kwargs)


def _board_stem(seams: BoardSeams) -> str:
    return f"20261004T090000Z-{paths.project_digest(runs.resolve_repo_dir(seams.root))}"


def _boards() -> Path:
    """`<data dir>/boards`, without creating it (unlike `paths.boards_dir`)."""
    return paths.data_dir() / "boards"


def _claim_conflict(monkeypatch) -> None:
    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        raise cli.ClaimedError("held elsewhere", key=keys[-1], run_id=OTHER_RUN_ID)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)


@pytest.mark.parametrize(
    ("cards", "overrides", "conflict", "error"),
    [
        pytest.param(
            lambda: [_board_milestone(1, blocked_by=(2,)), _board_milestone(2, blocked_by=(1,))],
            {},
            False,
            dag.DependencyCycleError,
            id="cycle",
        ),
        pytest.param(
            lambda: [
                _board_milestone(1),
                _board_milestone(2),
                _board_milestone(3, blocked_by=(1, 2)),
            ],
            {},
            False,
            orchestrate.MilestoneBlockersError,
            id="two-open-blockers",
        ),
        pytest.param(
            lambda: [_board_milestone(1)], {}, True, cli.ClaimedError, id="claim-conflict"
        ),
        pytest.param(
            lambda: [_board_milestone(1)],
            {"max_concurrent": 0},
            False,
            ValueError,
            id="max-concurrent-0",
        ),
    ],
)
def test_detach_board_refuses_in_the_foreground_and_forks_nothing(
    board_seams, monkeypatch, cards, overrides, conflict, error
):
    """Spec test 7 / Review Focus 2: `preflight_board`'s very refusal, no fork,
    and `<data dir>/boards` absent, not just empty."""
    board_seams.cards = cards()
    if conflict:
        _claim_conflict(monkeypatch)
    fake = _FakeDetacher()

    with pytest.raises(error) as detached:
        _detach_board(board_seams, fake, **overrides)
    with pytest.raises(error) as direct:
        _preflight(board_seams, **overrides)

    assert type(detached.value) is type(direct.value)
    assert str(detached.value) == str(direct.value)
    assert fake.calls == []
    assert not _boards().exists()
    assert board_seams.runs.calls == []


def test_detach_board_hands_the_board_to_a_child_and_reports_where_its_files_go(board_seams):
    """Spec test 8: the six keys, the files' names and modes, one go, and no
    milestone run in the parent."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    fake = _FakeDetacher()

    data = _detach_board(board_seams, fake)

    stem = _board_stem(board_seams)
    log = _boards() / f"{stem}{detach.BOARD_LOG_SUFFIX}"
    report = _boards() / f"{stem}{detach.BOARD_REPORT_SUFFIX}"
    assert set(data) == {"board", "detached", "pid", "log", "report", "levels"}
    assert data["board"] is True
    assert data["detached"] is True
    assert data["pid"] == FAKE_CHILD_PID
    assert data["log"] == str(log)
    assert data["report"] == str(report)
    assert data["levels"] == [
        {"level": 0, "milestones": [a.id]},
        {"level": 1, "milestones": [b.id]},
    ]
    assert data["levels"] == _preflight(board_seams).levels_payload
    assert log.read_bytes() == b""
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600
    assert not report.exists()
    assert fake.calls == [log]
    assert fake.events == ["go"]
    assert board_seams.runs.calls == []


def test_the_detached_board_child_writes_the_envelope_a_foreground_board_run_prints(
    board_seams,
):
    """Spec test 9: the report is `render(ok_envelope(run_board's payload))`
    plus a newline, mode 0600, with no temp file left beside it."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    fake = _FakeDetacher()
    data = _detach_board(board_seams, fake)

    fake.body()

    called = board_seams.runs.called()
    board_seams.runs.calls.clear()
    foreground = _board(board_seams)
    report = Path(data["report"])
    assert called == [a.id, b.id]
    assert report.read_text(encoding="utf-8") == cli.render(cli.ok_envelope(foreground)) + "\n"
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    assert sorted(entry.name for entry in _boards().iterdir()) == sorted(
        [Path(data["log"]).name, report.name]
    )


def test_the_detached_board_child_forwards_the_run_arguments(board_seams):
    one = _board_milestone(1)
    board_seams.cards = [one]
    fake = _FakeDetacher()
    runner_factory = object()
    driver = object()
    _detach_board(
        board_seams,
        fake,
        max_concurrent=3,
        commands=("git status",),
        allow_no_verification=True,
        runner_factory=runner_factory,
        driver=driver,
        control_interval=0.25,
    )

    fake.body()

    ((milestone, kwargs),) = board_seams.runs.calls
    assert milestone == one.id
    assert kwargs["max_concurrent"] == 3
    assert list(kwargs["commands"]) == ["git status"]
    assert kwargs["allow_no_verification"] is True
    assert kwargs["runner_factory"] is runner_factory
    assert kwargs["driver"] is driver
    assert kwargs["clock"]() == BOARD_DETACH_AT
    assert kwargs["control_interval"] == 0.25


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        pytest.param(RuntimeError("boom"), "RuntimeError: boom", id="milestone-raises"),
        pytest.param(
            cli.ClaimedError("claimed since the check", key="branch:x", run_id=OTHER_RUN_ID),
            "ClaimedError: claimed since the check",
            id="late-claim",
        ),
    ],
)
def test_the_detached_board_child_reports_an_escalated_milestone_inside_an_ok_envelope(
    board_seams, outcome, error
):
    """Spec test 10 / Review Focus 4: `ok_envelope` whose `data.ok` is false;
    a claim taken after the up-front check is an `escalated` entry."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    board_seams.runs.outcomes[a.id] = outcome
    fake = _FakeDetacher()
    data = _detach_board(board_seams, fake)

    fake.body()

    envelope = json.loads(Path(data["report"]).read_text(encoding="utf-8"))
    assert envelope["ok"] is True
    assert envelope["data"]["ok"] is False
    entries = {entry["milestone_id"]: entry for entry in envelope["data"]["milestones"]}
    assert entries[a.id] == {"milestone_id": a.id, "status": "escalated", "error": error}
    assert entries[b.id] == {"milestone_id": b.id, "status": "blocked", "blocked_by": [a.id]}


def test_the_detached_board_child_reports_a_handled_engine_error_as_an_error_envelope(
    board_seams, monkeypatch
):
    """Spec test 11, first half: the engine is read at call time, in the child."""
    board_seams.cards = [_board_milestone(1)]
    fake = _FakeDetacher()
    data = _detach_board(board_seams, fake)

    def engine(pre: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("x")

    monkeypatch.setattr(orchestrate, "run_board_engine", engine)
    fake.body()

    report = Path(data["report"])
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "ok": False,
        "error": {"type": "ValueError", "message": "x"},
    }
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600


def test_the_detached_board_child_writes_no_report_for_an_unhandled_engine_error(
    board_seams, monkeypatch
):
    """Spec test 11, second half: a bug propagates (its traceback goes to the log)."""
    board_seams.cards = [_board_milestone(1)]
    fake = _FakeDetacher()
    data = _detach_board(board_seams, fake)

    def engine(pre: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("engine bug")

    monkeypatch.setattr(orchestrate, "run_board_engine", engine)
    with pytest.raises(RuntimeError, match="engine bug"):
        fake.body()

    assert not Path(data["report"]).exists()
    assert [entry.name for entry in _boards().iterdir()] == [Path(data["log"]).name]


def test_detach_board_on_a_board_with_nothing_open_still_hands_off(board_seams):
    """Spec test 12 / Review Focus 3."""
    board_seams.cards = [_board_milestone(1, status="done", done_children=True)]
    fake = _FakeDetacher()

    data = _detach_board(board_seams, fake)

    assert data["levels"] == []
    assert fake.calls == [Path(data["log"])]
    assert fake.events == ["go"]
    assert board_seams.claims == []
    fake.body()
    assert json.loads(Path(data["report"]).read_text(encoding="utf-8")) == {
        "ok": True,
        "data": {"ok": True, "board": True, "levels": [], "milestones": []},
    }
    assert board_seams.runs.calls == []


def test_detach_board_refuses_a_log_that_already_exists_and_forks_nothing(board_seams):
    """Spec test 13 / Review Focus 1: two board detaches in one second."""
    board_seams.cards = [_board_milestone(1)]
    stem = _board_stem(board_seams)
    existing = paths.boards_dir() / f"{stem}{detach.BOARD_LOG_SUFFIX}"
    existing.write_text("an earlier board run\n", encoding="utf-8")
    fake = _FakeDetacher()

    with pytest.raises(orchestrate.BoardLogExistsError) as caught:
        _detach_board(board_seams, fake)

    assert isinstance(caught.value, ValueError)
    assert str(existing) in str(caught.value)
    assert fake.calls == []
    assert existing.read_text(encoding="utf-8") == "an earlier board run\n"
    assert not (_boards() / f"{stem}{detach.BOARD_REPORT_SUFFIX}").exists()
    assert board_seams.runs.calls == []


# ── run_story: one story through the milestone engine, no Integrate ─────────


def _run_story(project: Path, story: str, driver: Any, **overrides: Any) -> dict[str, Any]:
    """`run_story` on `story` from `main` under `PREFIX`, at `STARTED_AT`."""
    kwargs: dict[str, Any] = {
        "repo_dir": project,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "driver": driver,
        "clock": lambda: STARTED_AT,
    }
    kwargs.update(overrides)
    return orchestrate.run_story(story, **kwargs)


def test_run_story_propagates_a_preflight_refusal_with_nothing_written(
    tmp_path, monkeypatch, fake_board
):
    """`run_story` adds no side effect ahead of `preflight_story`."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, _ = _seed_story(fake_board, milestone, "Story A: rows")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    driver = FakeDriver()

    with pytest.raises(errors.StoryBlockedError):
        orchestrate.run_story(
            story, repo_dir=root, base_branch="main", branch_prefix=PREFIX, driver=driver
        )

    assert driver.calls == []
    assert _run_dirs() == []
    assert _run_ids(root) == []


@pytest.mark.git
def test_run_story_drives_only_the_selected_storys_subtasks_in_order(project):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    driver = FakeDriver()

    result = _run_story(project, story_a, driver)

    assert result["done"] is True, result
    assert [call["card"] for call in driver.calls] == [a1, a2]
    assert [call["parent"] for call in driver.calls] == [story_a, story_a]
    assert [call["base"] for call in driver.calls] == ["main", _branch(project, a1)]
    assert board.show(story_b, repo_dir=project).status == "todo"
    assert board.show(b1, repo_dir=project).status == "todo"


@pytest.mark.git
def test_a_story_run_never_calls_integrate(project, integrate_recorder):
    shape = _milestone(project, {"A": 2, "B": 1})

    result = _run_story(project, shape["stories"]["A"], FakeDriver())

    assert result["done"] is True, result
    assert "integrated" not in result
    assert integrate_recorder.calls == []
    assert INTEGRATION_BRANCH not in _local_branches(project)


@pytest.mark.git
def test_a_story_run_with_the_real_integrate_leaves_no_integration_branch(
    project, real_integrate
):
    """Real branches and the real Integrate: a milestone run here would make
    `m3-integrate`; a story run ends on its story's tip and makes none."""
    shape = _milestone(project, {"A": 2, "B": 1})
    a1, a2 = shape["subtasks"]["A"]
    root = cli.resolve_repo_dir(project)

    result = _run_story(
        project, shape["stories"]["A"], BranchingDriver(), runner_factory=_no_resolver
    )

    assert result["done"] is True, result
    branches = _local_branches(project)
    assert _branch(project, a1) in branches
    assert _branch(project, a2) in branches
    assert INTEGRATION_BRANCH not in branches
    assert not cli.worktree_for(root, INTEGRATION_BRANCH).exists()


@pytest.mark.git
def test_a_story_runs_report_and_record_are_a_milestone_runs_without_integrate(project):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(story_a, STARTED_AT)

    result = _run_story(project, story_a, FakeDriver(), control_interval=0)

    assert result == {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": [story_a]}],
        "completed": [a1, a2],
        "tips": [{"story": story_a, "tip": _branch(project, a2)}],
        "warnings": [],
    }
    run = _load(project, run_id)
    assert run.workflow == "milestone"
    assert run.milestone_id == shape["milestone"]
    assert run.config == models.RunConfig(max_concurrent_stories=1, story_id=story_a)
    assert _statuses(run) == {"run": "done", story_a: "done", a1: "done", a2: "done"}
    assert _lease(project, run_id) is None
    assert _claim_rows(project) == []


@pytest.mark.git
def test_a_finished_story_runs_nothing_and_is_recorded_done(project, integrate_recorder):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a = shape["stories"]["A"]
    for card in shape["subtasks"]["A"]:
        board.set_status(card, "done", repo_dir=project)
    driver = FakeDriver()

    result = _run_story(project, story_a, driver)

    assert driver.calls == []
    assert result["done"] is True, result
    assert (result["completed"], result["levels"]) == ([], [])
    assert "integrated" not in result
    assert integrate_recorder.calls == []
    assert _load(project, result["run_id"]).status == "done"


@pytest.mark.git
def test_an_out_of_play_story_runs_nothing_and_is_recorded_done(project, integrate_recorder):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a = shape["stories"]["A"]
    board.set_status(story_a, "canceled", repo_dir=project)
    driver = FakeDriver()

    result = _run_story(project, story_a, driver)

    assert driver.calls == []
    assert result["done"] is True, result
    assert (result["completed"], result["levels"], result["tips"]) == ([], [], [])
    assert "integrated" not in result
    assert integrate_recorder.calls == []
    run = _load(project, result["run_id"])
    assert run.status == "done"
    assert run.config.story_id == story_a


@pytest.mark.git
def test_a_story_run_on_a_done_blocker_names_only_its_own_story_in_levels_and_tips(
    project, integrate_recorder
):
    """A is carried in the plan only so B roots on A's tip; the report and
    the drive name B alone."""
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    for card in (a1, a2, story_a):
        board.set_status(card, "done", repo_dir=project)
    _git(project, "branch", _branch(project, a2), "main")
    driver = FakeDriver()

    result = _run_story(project, story_b, driver)

    assert [call["card"] for call in driver.calls] == [b1]
    assert driver.calls[0]["base"] == _branch(project, a2)
    assert result["levels"] == [{"level": 0, "stories": [story_b]}]
    assert result["tips"] == [{"story": story_b, "tip": _branch(project, b1)}]
    assert result["completed"] == [b1]
    assert "integrated" not in result
    assert integrate_recorder.calls == []


@pytest.mark.git
def test_a_cancelled_story_run_records_cancelled_skips_integrate_and_is_not_resumable(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(story_a, STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "cancel")})

    result = _run_story(project, story_a, driver, control_interval=0)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "canceled": True,
        "run_id": run_id,
        "stopped": [{"story": story_a, "subtask": a1, "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "canceled",
        story_a: "stopped",
        a1: "stopped",
        a2: "pending",
    }
    assert integrate_recorder.calls == []
    with pytest.raises(runs.NotResumableError, match="was canceled"):
        _resume(project, run_id, FakeDriver())


# ── resuming a story run restores the story ─────────────────────────────────
# Unit tests here run the resume pre-flight over `_resume_root` and the
# FakeBoard, with `refresh_git` failing if called and the done-blocker branch
# lookup stubbed by `_branches`; the git tests drive the engine.


def _record_story_run(
    root: Path, milestone: str, story: str, *, status: str = "escalated"
) -> None:
    """A story run of `story` under `milestone`, recorded as `preflight_story` records one."""
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        opened.record_run(
            models.Run(
                id=RESUME_RUN_ID,
                workflow="milestone",
                repo_dir=root,
                base_branch="main",
                branch_prefix=PREFIX,
                status=status,
                config=models.RunConfig(max_concurrent_stories=1, story_id=story),
                milestone_id=milestone,
            )
        )
    finally:
        opened.close()


def _resume_preflight(root: Path) -> Any:
    """`preflight_milestone` resuming `RESUME_RUN_ID`, as `am resume` reaches it."""
    return orchestrate.preflight_milestone(None, repo_dir=root, resume_run_id=RESUME_RUN_ID)


def test_a_resumed_story_run_cuts_its_plan_to_the_recorded_story(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, (_b1, b2) = _seed_story(
        fake_board, milestone, "Story A: rows", subtasks=2, status="done"
    )
    story, (s1, s2) = _seed_story(
        fake_board, milestone, "Story S: cols", subtasks=2, blocked_by=[blocker]
    )
    _seed_story(fake_board, milestone, "Story T: other")
    tip = _branch(root, b2)
    _branches(monkeypatch, {tip})
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    _record_story_run(root, milestone, story)

    pre = _resume_preflight(root)

    assert [(planned.id, planned.blocked_by) for planned in pre.plan.stories] == [
        (blocker, []),
        (story, [blocker]),
    ]
    assert [[planned.story.id for planned in level] for level in pre.levels] == [[story]]
    assert pre.levels[0][0].bases[s1] == tip
    assert pre.tips == [{"story": story, "tip": _branch(root, s2)}]
    assert pre.keys == orchestrate.story_claims(milestone, pre.plan.stories[-1], PREFIX)
    assert f"branch:{INTEGRATION_BRANCH}" not in pre.keys
    assert pre.run_id == RESUME_RUN_ID
    assert pre.resumed is not None
    assert pre.run_record.config.story_id == story
    assert (pre.run_record.status, pre.run_record.milestone_id) == ("started", milestone)
    assert (pre.base_branch, pre.branch_prefix, pre.max_concurrent) == ("main", PREFIX, 1)


def test_a_resumed_story_run_roots_on_the_base_when_its_blockers_tip_is_gone(
    tmp_path, monkeypatch, fake_board
):
    """The done blocker's tip branch was deleted after the first run: the
    blocker is dropped and the story roots on `main`, not on a missing branch."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, (_b1,) = _seed_story(fake_board, milestone, "Story A: rows", status="done")
    story, (s1,) = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    _record_story_run(root, milestone, story)

    pre = _resume_preflight(root)

    assert [(planned.id, planned.blocked_by) for planned in pre.plan.stories] == [(story, [])]
    assert pre.levels[0][0].bases[s1] == "main"


def test_a_resumed_milestone_run_without_a_story_keeps_the_whole_milestone_plan(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone, story, subtask = _seam_resume_board(fake_board)
    other, (o1,) = _seed_story(fake_board, milestone, "Story O: other")
    _record_resume_run(root)
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    def no_story_plan(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("a milestone run's resume took the story path")

    monkeypatch.setattr(orchestrate, "_story_plan", no_story_plan)

    pre = _resume_preflight(root)

    assert {planned.id for planned in pre.plan.stories} == {story, other}
    assert sorted(pre.keys) == _expected_claims(milestone, [subtask, o1])
    assert pre.run_record.config.story_id is None


@pytest.mark.parametrize("where", ["deleted", "reparented"])
def test_a_resumed_story_run_whose_story_left_the_milestone_is_not_resumable(
    tmp_path, monkeypatch, fake_board, where
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    _seed_story(fake_board, milestone, "Story A: rows")
    elsewhere = fake_board.add_card("Milestone 4: elsewhere")
    moved, _ = _seed_story(fake_board, elsewhere, "Story M: moved")
    story = {"deleted": "00000000-0000-4000-8000-00000000dead", "reparented": moved}[where]
    _record_story_run(root, milestone, story)
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    before = _runs_tree()

    with pytest.raises(runs.NotResumableError) as caught:
        _resume_preflight(root)

    assert RESUME_RUN_ID in str(caught.value)
    assert story in str(caught.value)
    assert _runs_tree() == before
    assert _load(root, RESUME_RUN_ID).status == "escalated"


def test_a_resumed_story_run_whose_blocker_was_reopened_is_refused_before_git(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, _ = _seed_story(fake_board, milestone, "Story A: rows", status="started")
    story, _ = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    _record_story_run(root, milestone, story)
    asked = _branches(monkeypatch)
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(errors.StoryBlockedError) as caught:
        _resume_preflight(root)

    assert caught.value.story_id == story
    assert caught.value.blockers == (blocker,)
    assert asked == []


@pytest.mark.git
def test_am_resume_of_an_escalated_story_run_finishes_only_that_story(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    first = _run_story(project, story_a, FakeDriver(outcomes={a1: ("review", "boom")}))
    assert first["escalated"] is True, first
    run_id = first["run_id"]
    driver = FakeDriver()

    result = _resume(project, run_id, driver)

    assert [call["card"] for call in driver.calls] == [a1, a2]
    assert result["done"] is True, result
    assert result["resumed"] is True
    assert result["levels"] == [{"level": 0, "stories": [story_a]}]
    assert "integrated" not in result
    assert integrate_recorder.calls == []
    run = _load(project, run_id)
    assert run.status == "done"
    assert run.config.story_id == story_a
    assert run.milestone_id == shape["milestone"]


@pytest.mark.git
def test_a_paused_story_run_parks_and_its_resume_finishes_only_that_story(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 2, "B": 1})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(story_a, STARTED_AT)
    gated = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "pause")})

    paused = _run_story(project, story_a, gated, control_interval=0)

    assert paused == {
        "paused": True,
        "run_id": run_id,
        "stopped": [{"story": story_a, "subtask": a1, "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
        "resume": f"am resume {run_id}",
    }
    assert _load(project, run_id).status == "stopped"
    driver = FakeDriver()

    result = _resume(project, run_id, driver, control_interval=0)

    assert [call["card"] for call in driver.calls] == [a1, a2]
    assert result["done"] is True, result
    assert result["resumed"] is True
    assert "integrated" not in result
    assert integrate_recorder.calls == []
    assert _load(project, run_id).status == "done"


@pytest.mark.git
def test_resuming_a_story_run_whose_escalated_subtask_was_finished_by_hand_is_a_no_op(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    first = _run_story(project, story_a, FakeDriver(outcomes={a1: ("review", "boom")}))
    assert first["escalated"] is True, first
    board.set_status(a1, "done", repo_dir=project)
    driver = FakeDriver()

    result = _resume(project, first["run_id"], driver)

    assert driver.calls == []
    assert result["done"] is True, result
    assert result["resumed"] is True
    assert (result["completed"], result["levels"]) == ([], [])
    assert "integrated" not in result
    assert integrate_recorder.calls == []
    assert _load(project, first["run_id"]).status == "done"


# ── story_census: the story cut a run and a dry run share ───────────────────


def _story_census(root: Path, story: str) -> census.Census:
    """`orchestrate.story_census` over the FakeBoard's milestone holding `story`."""
    match = census.find_story(board.roots(repo_dir=root), story)
    return orchestrate.story_census(
        board.tree(match.milestone.id, repo_dir=root),
        match.story,
        root=root,
        branch_prefix=PREFIX,
    )


def test_story_census_is_the_preflight_storys_plan_on_a_done_blockers_tip(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    blocker, (_b1, b2) = _seed_story(
        fake_board, milestone, "Story A: rows", subtasks=2, status="done"
    )
    _seed_story(fake_board, milestone, "Story C: cells")
    story, _subtasks = _seed_story(fake_board, milestone, "Story S: cols", blocked_by=[blocker])
    _branches(monkeypatch, {_branch(root, b2)})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    cut = _story_census(root, story)
    pre = _preflight_story(root, story)

    assert cut.milestone_title == "Milestone 3: orchestration"
    assert [(planned.id, planned.blocked_by) for planned in cut.stories] == [
        (blocker, []),
        (story, [blocker]),
    ]
    assert cut.stories == pre.plan.stories
    assert cut.milestone_title == pre.plan.milestone_title


@pytest.mark.parametrize("status", ["canceled", "archived"])
def test_story_census_of_an_out_of_play_story_is_empty_as_the_preflights_plan(
    tmp_path, monkeypatch, fake_board, status
):
    root = _resume_root(tmp_path, monkeypatch)
    milestone = fake_board.add_card("Milestone 3: orchestration")
    story, _subtasks = _seed_story(fake_board, milestone, "Story A: rows", status=status)
    _seed_story(fake_board, milestone, "Story B: cols")
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)

    cut = _story_census(root, story)

    assert cut.stories == []
    assert cut.stories == _preflight_story(root, story).plan.stories


# ── detach_story: a story run handed to a detached child ────────────────────


def test_the_detached_story_child_drives_only_the_story_and_reports_no_integrate(
    tmp_path, monkeypatch, fake_board, integrate_recorder
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1, "B": 1})
    (a1,) = shape["subtasks"]["A"]
    story = shape["stories"]["A"]
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    fake = _FakeDetacher()
    driver = FakeDriver()

    data = orchestrate.detach_story(
        story,
        repo_dir=root,
        base_branch="main",
        branch_prefix=PREFIX,
        detacher=fake,
        allow_no_verification=True,
        driver=driver,
        clock=lambda: STARTED_AT,
        control_interval=0.01,
    )

    run_id = data["run_id"]
    assert run_id == runs.mint_run_id(story, STARTED_AT)
    log = paths.run_dir(run_id) / detach.RUN_LOG_NAME
    assert data == {"run_id": run_id, "pid": FAKE_CHILD_PID, "log": str(log), "detached": True}
    assert fake.calls == [log]
    assert fake.events == ["go"]
    assert driver.calls == []
    assert _load(root, run_id).config == models.RunConfig(
        max_concurrent_stories=1, story_id=story
    )
    monkeypatch.setattr(store_module.Store, "take_lease", _no_take_lease)
    closes = _close_snapshots(monkeypatch)

    fake.body()

    report = paths.run_dir(run_id) / detach.REPORT_NAME
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    expected = {
        "done": True,
        "run_id": run_id,
        "levels": [{"level": 0, "stories": [story]}],
        "completed": [a1],
        "tips": [{"story": story, "tip": _branch(root, a1)}],
        "warnings": [],
    }
    assert json.loads(report.read_text(encoding="utf-8")) == json.loads(
        cli.render(cli.ok_envelope(expected))
    )
    assert [call["card"] for call in driver.calls] == [a1]
    assert integrate_recorder.calls == []
    assert _load(root, run_id).status == "done"
    assert closes == [(0, 0)]
    assert _lease(root, run_id) is None
    assert _claim_rows(root) == []


def test_detach_story_refuses_an_open_blocker_before_any_fork_or_write(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    fake = _FakeDetacher()

    with pytest.raises(errors.StoryBlockedError):
        orchestrate.detach_story(
            shape["stories"]["B"],
            repo_dir=root,
            base_branch="main",
            branch_prefix=PREFIX,
            detacher=fake,
            driver=FakeDriver(),
        )

    assert fake.calls == []
    assert _run_dirs() == []
    assert _run_ids(root) == []


# ── am run --story --detach through the CLI ─────────────────────────────────


def _story_run_args(root: Path, needle: str, *extra: str) -> list[str]:
    return [
        "run",
        "--story",
        needle,
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        PREFIX,
        "--allow-no-verification",
        *extra,
    ]


def test_a_detached_story_run_records_and_leases_the_plan_and_drives_nothing_here(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 2, "B": 1})
    a1, a2 = shape["subtasks"]["A"]
    story = shape["stories"]["A"]
    refreshed: list[Path] = []
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: refreshed.append(at))
    monkeypatch.setattr(cli, "drive_subtask_async", _forbidden("drive_subtask_async"))
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = detach_runner.invoke(cli.app, _story_run_args(root, story, "--detach"))

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert set(data) == {"run_id", "pid", "log", "detached"}
    assert (data["pid"], data["detached"]) == (FAKE_CHILD_PID, True)
    run_id = data["run_id"]
    assert run_id.endswith(dag.short_id(story))
    assert _run_ids(root) == [run_id]
    run = _load(root, run_id)
    assert run.milestone_id == shape["milestone"]
    assert run.config == models.RunConfig(max_concurrent_stories=1, story_id=story)
    assert _statuses(run) == {"run": "started", story: "pending", a1: "pending", a2: "pending"}
    lease = _lease(root, run_id)
    assert lease is not None and lease.pid == FAKE_CHILD_PID
    assert _held_keys(root, run_id) == sorted(
        [
            f"card:{shape['milestone']}",
            f"card:{story}",
            f"card:{a1}",
            f"card:{a2}",
            f"branch:{_branch(root, a1)}",
            f"branch:{_branch(root, a2)}",
        ]
    )
    log = Path(data["log"])
    assert log == paths.run_dir(run_id) / detach.RUN_LOG_NAME
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600
    assert fake.calls == [log]
    assert fake.events == ["go"]
    assert refreshed == [root]


@pytest.mark.parametrize(
    ("case", "error_type"),
    [("blocked", "StoryBlockedError"), ("ambiguous", "StoryNotFoundError")],
)
def test_a_refused_detached_story_run_forks_nothing(
    tmp_path, monkeypatch, fake_board, case, error_type
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    needle = {"blocked": shape["stories"]["B"], "ambiguous": "Story"}[case]
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = detach_runner.invoke(cli.app, _story_run_args(root, needle, "--detach"))

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == error_type
    assert fake.calls == []
    assert _run_dirs() == []
    assert _run_ids(root) == []
