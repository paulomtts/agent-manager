"""Production-wiring tier (spec "Tests"): `cli.run_card` with no injected
runner, the real `ClaudeAdapter`, the real `launcher.run_direct`, a real child
process -- and a fake `claude` first on `PATH` as the only stand-in.

None of the five tiers in design §14 lines 477-492 covers this, so the card
assigns it here. It launches no model and costs nothing, so it carries **no
marker and no skip** and runs in the default suite; registering an `e2e` marker
is sibling 34d3388b's job (spec "Suite placement").
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, prompt
from agent_manager.steps import docs_commit
from agent_manager.workflow import task as task_workflow

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done(
    project, completed_run, agent_attempts
):
    """R7: the card reads `done` ON THE BOARD, not merely in the payload.

    `mark_done` is `best_effort=True` (`workflow.task.TASK`), so a run can
    report `done` while the card never moved -- which is exactly the bug this
    assertion exists to catch.
    """
    assert completed_run["status"] == "done", (
        completed_run["failed_phase"],
        completed_run["detail"],
        completed_run["warnings"],
    )

    card = board.show(completed_run["card_id"], repo_dir=project)
    assert card.status == "done"

    assert sorted(agent_attempts) == sorted(AGENT_PHASES)
    for name in AGENT_PHASES:
        assert agent_attempts[name].status == "ok", (name, agent_attempts[name])


def test_every_agent_ran_in_the_subtask_worktree(fake_log, worktree):
    """R6 / D7: every agent phase, explore included, is dispatched with the
    subtask worktree as its cwd. Read off the fake's own log, which is the only
    witness of where the child process actually stood."""
    entries = [
        json.loads(line)
        for line in fake_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert {entry["phase"] for entry in entries} == set(AGENT_PHASES)
    for entry in entries:
        assert Path(entry["cwd"]).resolve() == worktree.resolve(), entry


def test_the_worktree_is_clean_after_the_run(worktree):
    """Design §14 / D4: run state lives under `paths.data_dir()`. A result file,
    a prompt or a stdout log inside the worktree would show up here."""
    assert _git(worktree, "status", "--porcelain") == ""
    assert list(worktree.rglob("result.json")) == []
    assert list(worktree.rglob("prompt.txt")) == []
    assert list(worktree.rglob("stdout.log")) == []


def test_the_brief_carries_the_result_path_and_the_schema(agent_attempts):
    """Addendum R2: the brief is one on-disk document that states the absolute
    result path and embeds `model_json_schema()`.

    The fake has no other source for either, so this assertion is what turns a
    prompt-composition regression into a named failure instead of a mysterious
    missing result file.
    """
    workflow = task_workflow.TASK

    for name in AGENT_PHASES:
        attempt = agent_attempts[name]
        text = Path(attempt.prompt_path).read_text(encoding="utf-8")
        model = workflow.phase(name).result
        schema = json.dumps(
            model.model_json_schema(), indent=2, ensure_ascii=False
        )

        assert prompt.RESULT_HEADING in text, name
        assert str(attempt.result_path) in text, name
        assert schema in text, name


def test_the_spec_and_plan_documents_exist_where_the_phases_declared_them(
    project, completed_run, worktree
):
    """The `writes:` templates resolved to real files inside the worktree and
    were committed. Paths come from the document and `prompt.expand_writes`,
    never from a convention retyped here."""
    workflow = task_workflow.TASK
    card = board.show(completed_run["card_id"], repo_dir=project)
    spec_relative = prompt.expand_writes(
        workflow.phase("spec").writes, card, phase="spec", input_name="spec_path"
    )
    plan_relative = prompt.expand_writes(
        workflow.phase("plan").writes, card, phase="plan", input_name="plan_path"
    )

    assert (worktree / spec_relative).is_file()
    assert (worktree / plan_relative).is_file()

    tracked = _git(worktree, "ls-files").split("\n")
    assert spec_relative in tracked
    assert plan_relative in tracked


def test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with(
    project, completed_run, worktree, agent_attempts
):
    """`review_gate` and `plan_hash_gate` passed for a real reason: the branch's
    commits all carry the trailer, and implement's hash is review's hash is the
    sha256 of the plan file on disk (`reducers.is_plan_hash`: 8 lowercase hex)."""
    workflow = task_workflow.TASK
    card = board.show(completed_run["card_id"], repo_dir=project)
    plan_relative = prompt.expand_writes(
        workflow.phase("plan").writes, card, phase="plan", input_name="plan_path"
    )

    revisions = _git(worktree, "rev-list", "main..HEAD").split()
    assert revisions  # non-vacuity: a branch with no commits would pass emptily
    for revision in revisions:
        assert "Plan-Hash:" in _git(worktree, "show", "-s", "--format=%B", revision)

    implement = json.loads(
        Path(agent_attempts["implement"].result_path).read_text(encoding="utf-8")
    )
    review = json.loads(
        Path(agent_attempts["review"].result_path).read_text(encoding="utf-8")
    )
    expected = hashlib.sha256((worktree / plan_relative).read_bytes()).hexdigest()[:8]

    assert implement["plan_hash"] == expected
    assert review["plan_hash"] == expected
    assert review["porcelain"] == ""
    assert review["commit_count"] == len(revisions)
    assert review["tagged_count"] == review["commit_count"]
    # The value reached the AGENT, not merely the result file: the recorded
    # brief on disk carries it as its own section (spec "Tests" item 12).
    brief = Path(agent_attempts["implement"].prompt_path).read_text(encoding="utf-8")
    assert f"\n## plan_hash\n{expected}\n" in brief, brief


def test_the_engine_authored_the_docs_commit_before_the_coder_ran(
    project, completed_run, worktree
):
    """R4: the fake harness knows no more than its brief and does no work the
    engine owes. The spec and the plan are committed by the `docs_commit` step,
    with the Plan-Hash trailer, before `implement` ever starts -- so the docs
    commit is OLDER than the fake's implementation commit."""
    workflow = task_workflow.TASK
    card = board.show(completed_run["card_id"], repo_dir=project)
    plan_relative = prompt.expand_writes(
        workflow.phase("plan").writes, card, phase="plan", input_name="plan_path"
    )
    spec_relative = prompt.expand_writes(
        workflow.phase("spec").writes, card, phase="spec", input_name="spec_path"
    )
    expected_hash = hashlib.sha256(
        (worktree / plan_relative).read_bytes()
    ).hexdigest()[:8]
    subject = docs_commit.SUBJECT_TEMPLATE.format(title=card.title)

    # `rev-list` is newest-first, so the docs commit must come LAST.
    revisions = _git(worktree, "rev-list", "main..HEAD").split()
    subjects = [
        _git(worktree, "show", "-s", "--format=%s", revision).strip()
        for revision in revisions
    ]
    assert subject in subjects, subjects
    assert subjects.index(subject) == len(subjects) - 1, subjects

    docs_revision = revisions[subjects.index(subject)]
    message = _git(worktree, "show", "-s", "--format=%B", docs_revision).rstrip("\n")
    assert message.splitlines()[-1] == f"{docs_commit.TRAILER_PREFIX}{expected_hash}"

    named = _git(worktree, "show", "--name-only", "--format=", docs_revision).split()
    assert sorted(named) == sorted([spec_relative, plan_relative])


def test_the_run_went_through_the_pygents_walk(project, completed_run, checkpoint_rows):
    """Non-vacuity: the pygents walk is the only one that checkpoints."""
    rows = checkpoint_rows(project, completed_run["run_id"])
    assert rows > 0, "the run wrote no checkpoint: it never reached the pygents walk"


CRITIC_BLOCKS_ENV = "FAKE_CLAUDE_CRITIC_BLOCKS"
"""Must equal `fake_claude.CRITIC_BLOCKS_ENV`, which `test_fake_claude.py` pins."""

CRITIC_BLOCK_REASON = (
    "fake-claude critic: the critic-blocks budget told this critic to block"
)
"""Must equal `fake_claude.CRITIC_BLOCK_REASON`, which `test_fake_claude.py` pins."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

FEEDBACK_SECTION = "\n## feedback\n"
"""How `prompt._assemble` heads the `feedback` input's section."""


def _arm_critic_blocks(tmp_path: Path, monkeypatch, table: dict[str, int]) -> Path:
    """Write the fake's critic budget beside the repo and point the env var at it.

    Through the test's own function-scoped `monkeypatch`, so it is undone when
    the test ends and never reaches the shared `completed_run`. Child processes
    inherit it: `run_direct` calls `Popen` with no `env=`."""
    budget = tmp_path / "critic-blocks.json"
    budget.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setenv(CRITIC_BLOCKS_ENV, str(budget))
    return budget


def _run_one_card(root: Path, card: str):
    """`am run --card` through `CliRunner`, with no runner_factory anywhere."""
    return CliRunner().invoke(
        cli.app,
        [
            "run",
            "--card",
            card,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m1",
            "--verify",
            VERIFY,
        ],
    )


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _brief_of(entry: dict) -> str:
    """The brief the fake was given for one logged dispatch: `prompt.txt` sits
    beside the attempt's `result.json`."""
    return (Path(entry["result_path"]).parent / "prompt.txt").read_text(encoding="utf-8")


def _feedback_of(brief: str) -> str | None:
    """The body of the brief's `## feedback` section, or `None` when it has none."""
    start = brief.find(FEEDBACK_SECTION)
    if start < 0:
        return None
    body = brief[start + len(FEEDBACK_SECTION) :]
    end = body.find("\n## ")
    return body if end < 0 else body[:end]


@pytest.mark.parametrize(
    ("critic", "author", "walked"),
    [
        (
            "validate_spec",
            "spec",
            [
                "explore", "spec", "validate_spec", "spec", "validate_spec",
                "plan", "validate_plan", "implement", "review",
            ],
        ),
        (
            "validate_plan",
            "plan",
            [
                "explore", "spec", "validate_spec", "plan", "validate_plan",
                "plan", "validate_plan", "implement", "review",
            ],
        ),
    ],
)
def test_a_critic_that_blocks_once_loops_back_and_the_run_finishes_done(
    milestone_board, fake_claude_bin, read_fake_log, tmp_path, monkeypatch,
    critic, author, walked,
):
    """Spec test 4 (and G4's "validate_plan loops back to plan in the same way"):
    under pygents one block sends the run back to the phase the critic judged,
    whose second brief carries the critic's reason, and the run ends done."""
    root = milestone_board["root"]
    card = milestone_board["subtasks"]["A"][0]
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {critic: 1})

    result = _run_one_card(root, card)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["status"] == "done", (data["failed_phase"], data["detail"])
    assert board.show(card, repo_dir=root).status == "done"
    # Non-vacuity: the block really was spent, so the loop really happened.
    assert json.loads(budget.read_text(encoding="utf-8")) == {critic: 0}

    entries = read_fake_log(data["run_id"])
    assert [entry["phase"] for entry in entries] == walked
    first, second = [entry for entry in entries if entry["phase"] == author]
    assert Path(first["result_path"]).parent.name == f"{author}.1"
    assert Path(second["result_path"]).parent.name == f"{author}.2"

    feedback = _feedback_of(_brief_of(second))
    assert feedback is not None, _brief_of(second)
    assert feedback.splitlines()[0] == prompt.FEEDBACK_TITLE
    assert f"- {critic}: " in feedback
    assert CRITIC_BLOCK_REASON in feedback
    # Review focus 5: feedback reaches the looped-to phase's second brief only.
    for entry in entries:
        if entry is not second:
            assert _feedback_of(_brief_of(entry)) is None, entry


def test_a_critic_that_blocks_twice_escalates_validation_at_the_critic(
    milestone_board, fake_claude_bin, read_fake_log, tmp_path, monkeypatch
):
    """Spec test 5: the one loop is spent, so the second block escalates at the
    critic -- never at the looped-to phase -- the way it does today."""
    root = milestone_board["root"]
    card = milestone_board["subtasks"]["A"][0]
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_spec": 2})

    result = _run_one_card(root, card)

    assert result.exit_code == cli.EXIT_ESCALATED == 1, (result.output, result.exception)
    data = _envelope(result)
    assert data["status"] == "escalated"
    assert data["failed_phase"] == "validate_spec"
    assert "blocked=validation" in data["detail"], data["detail"]
    assert CRITIC_BLOCK_REASON in data["detail"], data["detail"]
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_spec": 0}
    assert [entry["phase"] for entry in read_fake_log(data["run_id"])] == [
        "explore", "spec", "validate_spec", "spec", "validate_spec",
    ]
    assert board.show(card, repo_dir=root).status != "done"


def test_both_critics_blocking_once_each_both_loop(
    milestone_board, fake_claude_bin, read_fake_log, tmp_path, monkeypatch
):
    """Review focus 1 / G4 "each at most once": the spec loop does not spend
    validate_plan's, so both critics loop and the run ends done."""
    root = milestone_board["root"]
    card = milestone_board["subtasks"]["A"][0]
    budget = _arm_critic_blocks(
        tmp_path, monkeypatch, {"validate_spec": 1, "validate_plan": 1}
    )

    result = _run_one_card(root, card)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["status"] == "done", (data["failed_phase"], data["detail"])
    assert json.loads(budget.read_text(encoding="utf-8")) == {
        "validate_plan": 0,
        "validate_spec": 0,
    }
    assert [entry["phase"] for entry in read_fake_log(data["run_id"])] == [
        "explore", "spec", "validate_spec", "spec", "validate_spec",
        "plan", "validate_plan", "plan", "validate_plan", "implement", "review",
    ]


def test_with_no_critic_block_no_brief_carries_a_feedback_section(
    completed_run, agent_attempts
):
    """Spec "No block" / review focus 3: an empty `feedback` renders nothing,
    so a clean run's briefs are what they were before."""
    assert completed_run["status"] == "done", completed_run["detail"]
    for name in AGENT_PHASES:
        text = Path(agent_attempts[name].prompt_path).read_text(encoding="utf-8")
        assert FEEDBACK_SECTION not in text, name
