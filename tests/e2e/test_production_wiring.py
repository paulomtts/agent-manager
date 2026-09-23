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
