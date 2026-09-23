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

from agent_manager import board, prompt, results
from agent_manager.steps import docs_commit
from agent_manager.workflow import load_builtin

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

    `mark_done` is `best_effort: true` (`builtin/task.yaml:75-79`), so a run can
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
    workflow = load_builtin("task")

    for name in AGENT_PHASES:
        attempt = agent_attempts[name]
        text = Path(attempt.prompt_path).read_text(encoding="utf-8")
        declared = workflow.phase(name).result
        model = results.RESULT_MODELS[declared]
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
    workflow = load_builtin("task")
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
    workflow = load_builtin("task")
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


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Spec "Suite placement": this test costs nothing and must keep running by
    default. Sibling 34d3388b registers the `e2e` marker and adds
    `-m "not e2e"` to addopts; when it lands, nothing in THIS module may carry
    that marker, or the production wiring stops being checked on every run.

    Asserted against the collected node's markers rather than the file's text:
    a text scan would trip over its own assertion strings, and a marker applied
    from a conftest would not appear in this file at all.
    """
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_the_engine_authored_the_docs_commit_before_the_coder_ran(
    project, completed_run, worktree
):
    """R4: the fake harness knows no more than its brief and does no work the
    engine owes. The spec and the plan are committed by the `docs_commit` step,
    with the Plan-Hash trailer, before `implement` ever starts -- so the docs
    commit is OLDER than the fake's implementation commit."""
    workflow = load_builtin("task")
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
