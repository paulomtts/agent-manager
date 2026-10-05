"""Default-suite e2e tier: pause, resume and cancel a milestone under the fake claude (card d147d97a).

Live-control spec §7 "End to end". `am run --milestone`, `am pause`,
`am cancel`, `am status` and `am resume` run through `CliRunner` on the real
`cli.app` with no `runner_factory`, so every launch goes through
`cli.default_runner_factory`, the real `ClaudeAdapter` and `cli.run_direct` to
the fake `claude` first on `PATH`.

The milestone runs in a worker thread. A one-shot hold (`_hold`) blocks a1's
`plan` launch on a `threading.Event` before the real launch. While it is held,
the test thread reads the run id on a second `store.open_db` connection, which
stands for another process and is also `am pause`'s real path, and records the
control through the CLI. It then waits until the running process has applied
the request (`_signal_when_applied`, a pass-through wrapper on
`control.apply_pending`), and only then releases the hold. No sleep proves any
ordering.

Unmarked on purpose: fake-claude e2e tests run on every `uv run pytest`; only
`tests/e2e/test_real_harness*.py` carry the `e2e` marker.
"""

import json
import shutil
import subprocess
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_manager import cli, control, store
from agent_manager.harness import launcher

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

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

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""

HELD_PHASE = "plan"
"""The phase whose launch is held while the control is sent."""

WAIT = 120.0
"""Seconds any bounded wait gives up after. Generous: it only bounds a broken
run; a healthy one never waits this long."""

CONTROL_KEYS_NEVER_PRESENT = {"escalated", "failed_phase", "integrated", "done"}
"""A controlled payload never escalates, never names a failed phase and never
reaches Integrate (live control C6)."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


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


def _latest_run_id(root: Path) -> str:
    """The run id, read on a second connection: the "other process" of spec §7."""
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _status(root: Path, run_id: str) -> str:
    """`am status <run-id>`'s recorded run status (`data["run"]["status"]`)."""
    result = CliRunner().invoke(cli.app, ["status", run_id, "--repo-dir", str(root)])
    assert result.exit_code == 0, (result.output, result.exception)
    return _envelope(result)["run"]["status"]


def _resume(root: Path, run_id: str):
    """`am resume <run-id>`: no prefix, base or bound, only what the record lacks."""
    return CliRunner().invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(root), "--verify", VERIFY]
    )


def _attempt_of(stdout_path: Path) -> tuple[str, str]:
    """(card id, phase) of the attempt a launch belongs to.

    `paths.attempt_dir` is `<run dir>/<card>/<phase>.<n>` and the dispatcher
    hands the launcher `<attempt dir>/stdout.log`, so the launch names its own
    attempt; the fake is never asked.
    """
    attempt = Path(stdout_path).parent
    return attempt.parent.name, attempt.name.rsplit(".", 1)[0]


def _card_phase_counts(entries: Iterable[Mapping]) -> Counter:
    """How many times the fake ran each (card id, phase), read off its result path."""
    return Counter(
        (Path(entry["result_path"]).parents[1].name, entry["phase"]) for entry in entries
    )


def _counts(full: Iterable[str] = (), partial: Mapping[str, str] | None = None) -> Counter:
    """Every agent phase once per `full` card; for each `partial` card, its
    phases up to and including the named one once."""
    counts: Counter = Counter()
    for card in full:
        counts.update((card, phase) for phase in AGENT_PHASES)
    for card, last in (partial or {}).items():
        counts.update(
            (card, phase) for phase in AGENT_PHASES[: AGENT_PHASES.index(last) + 1]
        )
    return counts


def _only(counts: Counter, card: str) -> Counter:
    """The part of `counts` that belongs to `card`."""
    return Counter({key: value for key, value in counts.items() if key[0] == card})


def _hold(
    monkeypatch,
    card: str,
    phase: str,
    entered: threading.Event,
    release: threading.Event,
) -> None:
    """Hold `card`'s `phase` launch once: announce it, wait for `release`, then launch.

    Test scaffolding in the manager process: `cli.default_runner_factory`
    reads `cli.run_direct` at call time. The launch runs in a `to_thread`
    worker, so blocking here leaves the event loop, and so the control
    watcher, free. One-shot: the first matching launch disarms it, and every
    other launch (`am resume`'s and the relaunch's included) passes straight
    through. Undone with the test's function-scoped `monkeypatch`.
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
    """Set `applied` once the running process has applied a control request.

    A pass-through wrapper on `control.apply_pending`, which `control.watch`
    reads as a module global on every tick. It returns the rows it applied;
    a non-empty list means `StopSignal.request` has run. Undone with the
    test's function-scoped `monkeypatch`.
    """
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
    """Run the milestone in a worker, send `am <command>` while the hold is in, and finish it.

    Returns (run id, the control command's `data`, the milestone run's
    `CliRunner` result). `release` is set in a `finally`, so a failed
    assertion never leaves the worker hanging. The control command's own
    `CliRunner.invoke` finishes before `release` is set. Both invocations swap
    `sys.stdout`, so they must nest and never interleave, and at that point
    the worker writes nothing because its envelope comes after the run
    returns.
    """
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


def test_a_paused_milestone_resumes_with_nothing_dispatched_twice(
    two_story_board, run_milestone_cli, read_fake_log, monkeypatch, request
):
    """Spec §7 pause: held in a1's plan, paused from another connection, parked
    `stopped` without Integrate; `am resume` finishes it `integrated` under the
    same run id and no phase is dispatched twice."""
    # The tier: default suite, never the opt-in `e2e` marker.
    assert request.node.get_closest_marker("e2e") is None
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    (a1,) = two_story_board["subtasks"]["A"]
    (b1,) = two_story_board["subtasks"]["B"]
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    _hold(monkeypatch, a1, HELD_PHASE, entered, release)
    _signal_when_applied(monkeypatch, applied)

    run_id, requested, first = _control_while_held(
        root,
        milestone,
        "pause",
        run_milestone_cli=run_milestone_cli,
        entered=entered,
        release=release,
        applied=applied,
    )

    # `am pause` recorded the request for this life of the run.
    assert requested["run_id"] == run_id
    assert requested["command"] == "pause"
    assert requested["effective"] == "pause"
    assert requested["already_requested"] is False

    # The run parked: exit 0, `paused`, never an escalation, never Integrate (C6).
    assert first.exit_code == 0, (first.output, first.exception)
    paused = _envelope(first)
    assert paused["paused"] is True, paused
    assert paused["run_id"] == run_id
    assert paused["resume"] == f"am resume {run_id}"
    assert "canceled" not in paused and "cancelled" not in paused, paused
    assert not CONTROL_KEYS_NEVER_PRESENT & set(paused), paused
    assert _status(root, run_id) == "stopped"
    assert INTEGRATION_BRANCH not in _local_branches(root)

    # The held plan finished once and a1 parked before validate_plan: a
    # control never cancels a running phase.
    before = _card_phase_counts(read_fake_log(run_id))
    assert _only(before, a1) == _counts(partial={a1: HELD_PHASE}), before

    resumed = _resume(root, run_id)

    assert resumed.exit_code == 0, (resumed.output, resumed.exception)
    data = _envelope(resumed)
    assert data["done"] is True, data
    assert data["resumed"] is True
    assert data["run_id"] == run_id
    assert "escalated" not in data
    assert a1 in data["completed"]
    assert set(data["integrated"]["merged"]) == set(stories.values())
    assert _status(root, run_id) == "done"
    assert INTEGRATION_BRANCH in _local_branches(root)

    # Nothing dispatched twice: every phase of both cards exactly once over
    # both invocations, and no phase counted before the resume grew.
    after = _card_phase_counts(read_fake_log(run_id))
    assert after == _counts(full=(a1, b1)), after
    assert not set(before) & set(after - before), (before, after)
    assert after[(a1, HELD_PHASE)] == 1


def test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch(
    two_story_board, run_milestone_cli, read_fake_log, monkeypatch, request
):
    """Spec §7 cancel: held in a1's plan, cancelled from another connection,
    recorded `canceled` without Integrate; `am resume` refuses it at exit 3
    and launches nothing; a fresh `am run --milestone` drives a1 again from
    `explore`, never continuing the cancelled run's parked checkpoint (C9)."""
    assert request.node.get_closest_marker("e2e") is None
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    (a1,) = two_story_board["subtasks"]["A"]
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    _hold(monkeypatch, a1, HELD_PHASE, entered, release)
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

    assert requested["run_id"] == run_id
    assert requested["command"] == "cancel"
    assert requested["effective"] == "cancel"
    assert requested["already_requested"] is False

    # Cancelled: exit 0, no resume hint, never an escalation, never Integrate (C6).
    assert first.exit_code == 0, (first.output, first.exception)
    canceled = _envelope(first)
    assert canceled["canceled"] is True, canceled
    assert "cancelled" not in canceled, canceled
    assert canceled["run_id"] == run_id
    assert "resume" not in canceled and "paused" not in canceled, canceled
    assert not CONTROL_KEYS_NEVER_PRESENT & set(canceled), canceled
    assert _status(root, run_id) == "canceled"
    assert INTEGRATION_BRANCH not in _local_branches(root)
    # a1 parked after its held plan: explore..plan once, nothing after.
    cancelled_counts = _card_phase_counts(read_fake_log(run_id))
    assert _only(cancelled_counts, a1) == _counts(partial={a1: HELD_PHASE})

    # `am resume` of a cancelled run is refused at exit 3, launching and writing nothing.
    launches = len(read_fake_log(run_id))
    refused = _resume(root, run_id)

    assert refused.exit_code == cli.EXIT_ERROR == 3, (refused.output, refused.exception)
    refusal = _error(refused)
    assert refusal["type"] == "NotResumableError"
    assert "canceled" in refusal["message"]
    assert len(read_fake_log(run_id)) == launches
    assert _status(root, run_id) == "canceled"

    # A fresh relaunch is new work: a new run id, a1 driven again from explore.
    relaunch = run_milestone_cli(root, milestone)

    assert relaunch.exit_code == 0, (relaunch.output, relaunch.exception)
    finished = _envelope(relaunch)
    assert finished["done"] is True, finished
    assert "escalated" not in finished
    assert finished["run_id"] != run_id
    assert a1 in finished["completed"]
    assert set(finished["integrated"]["merged"]) == set(stories.values())
    relaunched = _card_phase_counts(read_fake_log(finished["run_id"]))
    # Every a1 phase exactly once, `explore` included: the cancelled run's
    # parked checkpoint (pending validate_plan) was not continued.
    assert _only(relaunched, a1) == _counts(full=(a1,)), relaunched
    assert relaunched[(a1, "explore")] == 1
    # The cancelled run stays cancelled.
    assert _status(root, run_id) == "canceled"


def _subtask_of(root: Path, run_id: str, card_id: str):
    """`card_id`'s recorded `SubtaskRun` in `run_id`, phases in the order the walk recorded them."""
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    for story in run.stories:
        for subtask in story.subtasks:
            if subtask.card_id == card_id:
                return subtask
    raise AssertionError(f"{card_id} is not recorded in run {run_id}")


@pytest.mark.e2e_fake
def test_a_reset_of_a_paused_milestone_whose_worktree_was_removed_relaunches_it_from_worktree(
    two_story_board, run_milestone_cli, read_fake_log, monkeypatch, request
):
    """am-reset spec test 12, the 2026-10-03 incident replayed: a milestone
    paused in a1's plan, a1's worktree removed by hand, `am reset` closes the
    run, `am resume` refuses it, and a fresh `am run --milestone` drives a1
    from `worktree` to `done` with every agent phase run once, `explore`
    included: nothing is continued from the reset run's checkpoint."""
    assert request.node.get_closest_marker("e2e") is None
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    (a1,) = two_story_board["subtasks"]["A"]
    branch = two_story_board["branches"][a1]
    worktree = cli.worktree_for(root, branch)
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    _hold(monkeypatch, a1, HELD_PHASE, entered, release)
    _signal_when_applied(monkeypatch, applied)

    run_id, _requested, first = _control_while_held(
        root,
        milestone,
        "pause",
        run_milestone_cli=run_milestone_cli,
        entered=entered,
        release=release,
        applied=applied,
    )

    # Paused: a1 parked after its held plan, the run recorded `stopped`.
    assert first.exit_code == 0, (first.output, first.exception)
    assert _envelope(first)["paused"] is True
    assert _status(root, run_id) == "stopped"
    paused_counts = _card_phase_counts(read_fake_log(run_id))
    assert _only(paused_counts, a1) == _counts(partial={a1: HELD_PHASE}), paused_counts

    # The incident: a1's worktree removed by hand; its branch survives.
    assert worktree.is_dir()
    shutil.rmtree(worktree)
    assert not worktree.exists()
    assert branch in _local_branches(root)

    reset = CliRunner().invoke(cli.app, ["reset", run_id, "--repo-dir", str(root)])

    assert reset.exit_code == 0, (reset.output, reset.exception)
    closed = _envelope(reset)
    assert set(closed) == {
        "run_id",
        "previous_status",
        "status",
        "already_canceled",
        "cards",
        "message",
    }, closed
    assert closed["run_id"] == run_id
    assert closed["previous_status"] == "stopped"
    assert closed["status"] == "canceled"
    assert closed["already_canceled"] is False
    assert {"card_id": a1, "workflow": "task", "open_in": None} in closed["cards"], closed
    assert all(card["open_in"] is None for card in closed["cards"]), closed["cards"]
    assert closed["message"] == (
        f"run {run_id} is canceled; `am resume {run_id}` refuses it,"
        " and a relaunch starts its cards from their first phase"
    )
    assert _status(root, run_id) == "canceled"

    # `am resume` of the reset run is refused at exit 3, launching nothing.
    launches = len(read_fake_log(run_id))
    refused = _resume(root, run_id)

    assert refused.exit_code == cli.EXIT_ERROR == 3, (refused.output, refused.exception)
    assert _error(refused) == {
        "type": "NotResumableError",
        "message": f"run {run_id} was canceled; start new work with `am run --milestone`",
    }
    assert len(read_fake_log(run_id)) == launches

    relaunch = run_milestone_cli(root, milestone)

    assert relaunch.exit_code == 0, (relaunch.output, relaunch.exception)
    finished = _envelope(relaunch)
    assert finished["done"] is True, finished
    assert "escalated" not in finished
    new_run = finished["run_id"]
    assert new_run != run_id
    assert a1 in finished["completed"]
    assert set(finished["integrated"]["merged"]) == set(stories.values())
    # Driven from `worktree` to `done`: the walk's first recorded phase is the
    # worktree step, and it recreated the removed directory on a1's branch.
    subtask = _subtask_of(root, new_run, a1)
    assert subtask.status == "done", subtask
    assert subtask.phases[0].name == "worktree", [p.name for p in subtask.phases]
    assert subtask.phases[0].status == "done"
    assert worktree.is_dir()
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    # Nothing continued from the reset run's parked checkpoint: every agent
    # phase of a1 exactly once in the relaunch, `explore` included.
    relaunched = _card_phase_counts(read_fake_log(new_run))
    assert _only(relaunched, a1) == _counts(full=(a1,)), relaunched
    assert relaunched[(a1, "explore")] == 1
    # The reset run stays canceled.
    assert _status(root, run_id) == "canceled"
