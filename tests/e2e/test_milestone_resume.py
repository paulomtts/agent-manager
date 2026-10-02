"""Default-suite e2e tier: a killed milestone resumes where it stopped (card 949d51a0).

Supervisor-tree spec §6, §7 and §9 ("Resume, end to end under the fake
claude"). `am run --milestone` and `am resume` run through `CliRunner` on the
real `cli.app` with no `runner_factory` and no `driver`, so every launch goes
through `cli.drive_subtask_async`, `cli.default_runner_factory`, the real
`ClaudeAdapter` and `launcher.run_direct` to the fake `claude` first on `PATH`.

On `merged_base_board`, A (a1) and B (b1) are independent, C (c1) is blocked by
both and runs on their merged base, and D (d1 -> d2 -> d3) is a sibling lane.
The manager is killed with a plain `BaseException` while c1 is inside `plan`
and d3 inside `implement`, after C's merged base was built. The kill is test
scaffolding in the manager process (`cli.run_direct`, read at call time); the
fake is never told anything. Unmarked on purpose: it must run on every
`uv run pytest`.
"""

import json
import subprocess
import threading
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, models, paths, store
from agent_manager.harness import launcher
from agent_manager.workflow import task as task_workflow

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: the board fixtures derive their branches with it."""

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
"""`integration.integration_branch` for the `m3` prefix."""

IMPLEMENT_SUBJECT = "feat: implement this card"
"""The subject line of the commit the fake coder makes (`fake_claude.build_result`)."""

REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""Must equal the conftest's `FAKE_REVIEW_FAIL_MARKER` (and `fake_claude.REVIEW_FAIL_MARKER`)."""

MAX_CONCURRENT = 3
"""A, B and D run at once; C waits for its blockers without holding a slot."""

WAIT = 120.0
"""Seconds a held launch waits for the other lane before giving up. Generous:
it only bounds a broken run, a healthy one never waits this long."""


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _merge_count(root: Path, branch: str) -> int:
    """How many merge commits `branch` holds that `main` does not."""
    return int(_git(root, "rev-list", "--merges", "--count", f"main..{branch}").strip())


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


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _latest_run_id(root: Path) -> str:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _run_ids(root: Path) -> list[str]:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return [row["id"] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _all_cards(shape) -> list[str]:
    return [
        *(card for chain in shape["subtasks"].values() for card in chain),
        *shape["stories"].values(),
        shape["milestone"],
    ]


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
    """How many times the fake ran each (card id, phase).

    The fake logs its result path, `<run dir>/<card>/<phase>.<n>/result.json`
    (`paths.attempt_dir`), so the card is read off the path, never off the fake.
    """
    return Counter(
        (Path(entry["result_path"]).parents[1].name, entry["phase"]) for entry in entries
    )


def _counts(
    full: Iterable[str],
    partial: Mapping[str, str] | None = None,
    twice: Iterable[tuple[str, str]] = (),
) -> Counter:
    """Every agent phase once per `full` card; for each `partial` card, its
    phases up to and including the named one once; then one more launch of
    each (card, phase) in `twice`."""
    counts: Counter = Counter()
    for card in full:
        counts.update((card, phase) for phase in AGENT_PHASES)
    for card, last in (partial or {}).items():
        counts.update(
            (card, phase) for phase in AGENT_PHASES[: AGENT_PHASES.index(last) + 1]
        )
    counts.update(twice)
    return counts


class _Killed(BaseException):
    """The manager process dying mid-phase. A plain `BaseException`, so neither
    the engine nor `CliRunner` swallows it, and not `KeyboardInterrupt`, which
    asyncio re-raises out of the event loop before the engine unwinds."""


def _kill_with_c1_in_plan_and_d3_in_implement(monkeypatch, c1: str, d3: str) -> None:
    """Kill the manager once, while c1 is inside `plan` and d3 inside `implement`.

    Test scaffolding in the manager process: `cli.default_runner_factory`
    reads `cli.run_direct` at call time, so the real launcher still spawns the
    real fake, which writes its result and logs the phase as always. Both held
    launches raise after the real launch returned and before the dispatcher
    records the outcome, which leaves each attempt and phase `started`: the
    crash signature a real kill leaves.

    d3's `implement` launch, once returned, announces itself and waits for
    the kill. c1's `plan` launch, once returned, waits until d3 is held, then
    fires the kill and raises. So at the kill both lanes are inside their
    phase, whichever reached it first, and c1 can only be in `plan` after its
    lane built C's merged base. The waits are `threading.Event`s, since each
    launch runs in a `to_thread` worker; no sleep. One-shot: once the kill has
    fired, every later launch, the resume's included, passes straight through.
    """
    real = launcher.run_direct
    armed = {"on": True}
    d3_in_implement = threading.Event()
    killed = threading.Event()

    def killing(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        outcome = real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )
        if not armed["on"]:
            return outcome
        attempt = _attempt_of(stdout_path)
        if attempt == (d3, "implement"):
            d3_in_implement.set()
            if not killed.wait(WAIT):
                raise AssertionError("c1 never reached plan while d3 was in implement")
            raise _Killed(f"killed while {d3} was in implement")
        if attempt == (c1, "plan"):
            if not d3_in_implement.wait(WAIT):
                raise AssertionError("d3 never reached implement while c1 was in plan")
            armed["on"] = False
            killed.set()
            raise _Killed(f"killed while {c1} was in plan")
        return outcome

    monkeypatch.setattr(cli, "run_direct", killing)


def test_a_killed_milestone_resumes_where_it_stopped(
    merged_base_board, run_milestone_cli, read_fake_log, checkpoint_rows, monkeypatch
):
    """Spec test 1: killed with c1 in plan and d3 in implement, after C's
    merged base was built; `am resume` finishes it under the same run id,
    re-dispatching only those two phases, and never merges the base again."""
    root = merged_base_board["root"]
    milestone = merged_base_board["milestone"]
    stories = merged_base_board["stories"]
    branches = merged_base_board["branches"]
    base = merged_base_board["base_branch"]
    (a1,) = merged_base_board["subtasks"]["A"]
    (b1,) = merged_base_board["subtasks"]["B"]
    (c1,) = merged_base_board["subtasks"]["C"]
    d1, d2, d3 = merged_base_board["subtasks"]["D"]
    main_before = _git(root, "rev-parse", "main").strip()
    _kill_with_c1_in_plan_and_d3_in_implement(monkeypatch, c1, d3)

    # First invocation: the manager dies.
    with pytest.raises(_Killed):
        run_milestone_cli(root, milestone, max_concurrent=MAX_CONCURRENT)

    run_id = _latest_run_id(root)
    # Review Focus 2: a killed run is neither recorded done nor integrated.
    assert _load_run(root, run_id).status == "started"
    assert INTEGRATION_BRANCH not in _local_branches(root)
    assert checkpoint_rows(root, run_id) > 0
    # C's merged base was built before c1 ran: one --no-ff merge of the other blocker.
    assert base in _local_branches(root)
    base_tip = _git(root, "rev-parse", base).strip()
    assert _merge_count(root, base) == 1
    assert _card_phase_counts(read_fake_log(run_id)) == _counts(
        full=(a1, b1, d1, d2), partial={d3: "implement", c1: "plan"}
    )

    # Second invocation: am resume <run-id>.
    result = _resume(root, run_id)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert data["run_id"] == run_id
    assert data["resumed"] is True
    assert sorted(data["completed"]) == sorted([c1, d3])
    assert data["bases"] == [
        {"story": stories["C"], "branch": base, "blockers": merged_base_board["merged_from"]}
    ]
    assert set(data["integrated"]["merged"]) == set(stories.values())
    assert data["integrated"]["resolved"] == []
    assert _run_ids(root) == [run_id]
    run = _load_run(root, run_id)
    assert run.status == "done"
    assert (run.branch_prefix, run.base_branch) == (PREFIX, "main")
    assert run.config.max_concurrent_stories == MAX_CONCURRENT

    # Finished phases once in total; the two interrupted phases twice.
    assert _card_phase_counts(read_fake_log(run_id)) == _counts(
        full=(a1, b1, c1, d1, d2, d3), twice=[(c1, "plan"), (d3, "implement")]
    )

    # The merged base was not merged again: same tip, still one merge commit.
    assert _git(root, "rev-parse", base).strip() == base_tip
    assert _merge_count(root, base) == 1
    assert _is_ancestor(root, base, branches[c1])
    # Review Focus 3: d3's re-dispatched implement made no second commit.
    subjects = _git(root, "log", "--format=%s", f"{branches[d2]}..{branches[d3]}").splitlines()
    assert subjects.count(IMPLEMENT_SUBJECT) == 1, subjects
    # Review Focus 4: the resumed subtasks' worktrees are clean.
    for card in (c1, d3):
        assert _git(cli.worktree_for(root, branches[card]), "status", "--porcelain") == ""
    for card_id in _all_cards(merged_base_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
    assert _git(root, "rev-parse", "main").strip() == main_before
    assert _git(root, "remote").strip() == ""  # nowhere to push to, so nothing was pushed

    # Spec §9 and Review Focus 1: resuming the now-done run is refused, exit 3,
    # and launches nothing.
    launches = len(read_fake_log(run_id))
    again = _resume(root, run_id)

    assert again.exit_code == cli.EXIT_ERROR, (again.output, again.exception)
    refusal = _error(again)
    assert refusal["type"] == "NotResumableError"
    assert "finished" in refusal["message"]
    assert len(read_fake_log(run_id)) == launches
    assert _load_run(root, run_id).status == "done"


STALE_DIGEST = "saved-under-another-task"
"""The digest a planted checkpoint claims: no `TASK` ever has it."""


def _tree(directory: Path) -> dict[str, bytes]:
    """Every path under `directory`, with file contents: the journal and the fake log included."""
    return {
        str(path.relative_to(directory)): path.read_bytes() if path.is_file() else b"<dir>"
        for path in sorted(directory.rglob("*"))
    }


def _checkpoints(root: Path, run_id: str) -> list[tuple]:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT card_id, seq, reason, digest FROM checkpoints"
                " WHERE run_id = ? ORDER BY card_id, seq",
                (run_id,),
            )
        ]
    finally:
        conn.close()


def _plant_stale_checkpoint(root: Path, run_id: str, card_id: str) -> None:
    """A newest `turn` checkpoint of `card_id` in `run_id`, saved under `STALE_DIGEST`.

    Stands for a `TASK` that changed between the interrupt and the resume. It
    is the card's highest `seq`, so it is the row `orchestrate.resume_point`
    judges first.
    """
    opened = store.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        opened.save_checkpoint(
            card_id,
            workflow=task_workflow.TASK.name,
            digest=STALE_DIGEST,
            reason="turn",
            agent={"current_turn": None, "queue": []},
            saved_at=datetime.now(timezone.utc),
        )
    finally:
        opened.close()


def test_a_checkpoint_saved_under_another_task_refuses_the_resume_and_writes_nothing(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Spec §9 error path: a stale digest refuses the whole milestone resume
    with exit 3, and nothing is written: no journal line, no row, no status,
    no branch, no launch."""
    root = two_story_board["root"]
    branches = two_story_board["branches"]
    (a1,) = two_story_board["subtasks"]["A"]
    marker = root / ".git" / REVIEW_FAIL_MARKER
    marker.write_text(f"{branches[a1]}\n", encoding="utf-8")

    first = run_milestone_cli(root, two_story_board["milestone"])

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["subtask"] == a1, stopped
    run_id = stopped["run_id"]
    marker.unlink()
    _plant_stale_checkpoint(root, run_id, a1)
    before = (
        _tree(paths.run_dir(run_id)),
        _load_run(root, run_id).status,
        _checkpoints(root, run_id),
        _local_branches(root),
        _git(root, "rev-parse", "main").strip(),
        len(read_fake_log(run_id)),
    )

    result = _resume(root, run_id)

    assert result.exit_code == cli.EXIT_ERROR, (result.output, result.exception)
    refusal = _error(result)
    assert refusal["type"] == "CheckpointMismatchError"
    assert refusal["message"].startswith("workflow changed since checkpoint")
    assert a1 in refusal["message"]
    assert STALE_DIGEST in refusal["message"]
    assert task_workflow.TASK.digest() in refusal["message"]
    assert (
        _tree(paths.run_dir(run_id)),
        _load_run(root, run_id).status,
        _checkpoints(root, run_id),
        _local_branches(root),
        _git(root, "rev-parse", "main").strip(),
        len(read_fake_log(run_id)),
    ) == before


def test_no_kill_switch_is_left_armed_for_later_tests():
    """Review Focus 5: the kill switch is set through the function-scoped
    `monkeypatch`; it must be gone once its test ends, or every later launch
    in the session could be killed. Kept last in the module."""
    assert cli.run_direct is launcher.run_direct
