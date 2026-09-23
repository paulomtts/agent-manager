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
