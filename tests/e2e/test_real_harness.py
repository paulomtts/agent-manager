"""End-to-end tier (design §14 lines 477-492): the ONE slow opt-in test.

Addendum R4 and §3 of `docs/superpowers/specs/2026-09-23-real-harness-design.md`:
"Real harness, opt-in. `pytest -m e2e` runs the same toy card against a real
`claude -p`. Excluded by default. Run by a human."

This module costs real money every time it runs, so `pyproject.toml`'s
`addopts` carries `-m "not e2e"` and the marker below opts it out of the
default suite. To run it: `uv run pytest -m e2e` (a bare path invocation such
as `uv run pytest tests/e2e/test_real_harness.py` is still deselected by
`addopts` and exits 5 -- pass `-m e2e` alongside the path).

`-m e2e` pays for one real run of this module:
`uv run pytest -m e2e -v tests/e2e/test_real_harness.py`.

The marker is applied HERE and only here. Marking it from `tests/e2e/conftest.py`
would drag the sibling's free, fake-claude `test_production_wiring.py` out of the
default suite, which its own `test_this_module_runs_in_the_default_suite_unmarked`
forbids.

Everything expensive is reused from `tests/e2e/conftest.py`: the toy git repo,
the toy brd board and the milestone -> story -> subtask card chain. Only
`completed_run` is overridden, because the conftest's version pulls in
`fake_claude_bin`, which prepends a fake `claude` to `PATH` -- the exact thing
this module must not have.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_manager import board, cli
from agent_manager.workflow import task as task_workflow

pytestmark = pytest.mark.e2e

BRANCH_PREFIX = "e2e-real"
"""Distinct from the sibling's `m1`, so a real run and a fake run in the same
toy repo could never land on the same branch or worktree path."""

VERIFY_COMMANDS = ("git rev-parse --verify HEAD",)
"""Must match `tests/e2e/conftest.py`'s constant of the same name.

Re-declared rather than imported: `--import-mode=importlib` puts nothing on
`sys.path`, so a conftest's module-level names are not importable from a
sibling test module. `test_production_wiring.py:19-34` re-declares
`AGENT_PHASES` and its own `_git` for the same reason."""

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)
"""`TASK`'s seven agent phases; see `VERIFY_COMMANDS` on why this
is re-declared."""

PLAN_HASH_TRAILER = "Plan-Hash:"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _plan_hashes(message: str) -> list[str]:
    """Every `Plan-Hash:` trailer VALUE in one commit message.

    The value, not merely the presence of the string: a commit body that
    mentions the word would satisfy a substring check, and two commits with
    different hashes would satisfy it too.

    Matched at column 0, not after `.strip()`: a git trailer is unindented by
    definition, so an indented body line that happens to read `Plan-Hash: ...`
    is exactly the body mention this helper exists to reject.
    """
    return [
        line.split(":", 1)[1].strip()
        for line in message.splitlines()
        if line.startswith(PLAN_HASH_TRAILER)
    ]


@pytest.fixture(scope="module")
def real_claude() -> Path:
    """The real `claude`, or a skip that says so in as many words.

    The conftest's `toolchain` fixture only checks `git` and `brd`, so this
    guard is not redundant with it.
    """
    found = shutil.which("claude")
    if found is None:
        pytest.skip(
            "the real `claude` CLI is not on PATH; the opt-in e2e tier needs it "
            "to perform a real paid run (install claude and put it on PATH, or "
            "just run `uv run pytest`, which deselects this test)"
        )
    return Path(found)


@pytest.fixture(scope="module")
def completed_run(real_claude, project, cards) -> dict[str, Any]:
    """One real, paid `cli.run_card` -- no `runner_factory`, no
    fake on `PATH`.

    Overrides the conftest fixture of the same name, so `run_tree`,
    `agent_attempts` and `worktree` resolve against THIS run. `fake_claude_bin`
    is deliberately absent from the parameter list: `cli.default_runner_factory`
    (`cli.py:588`) builds the real `dispatch.AgentRunner` over
    `harness/launcher.py:94` `run_direct`, which resolves `claude` on `PATH`,
    and the whole point of this module is that what it finds there is real.
    """
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix=BRANCH_PREFIX,
        commands=list(VERIFY_COMMANDS),
    )


def test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch(
    project, completed_run, agent_attempts, worktree
):
    """The whole deliverable: a real `claude -p` takes the toy subtask from
    `todo` to `done`, leaving a branch whose every commit carries one and the
    same `Plan-Hash` trailer and whose every result file validates.

    justification: verifies the harness's prompt/result-file contract against
    the real `claude -p` CLI's actual behavior (argv, exit codes, tool
    permissions, result-file handshake) -- a fake-claude stand-in's
    deterministic replies cannot exercise whether the real model actually
    complies with that contract."""
    assert completed_run["status"] == "done", (
        completed_run["failed_phase"],
        completed_run["detail"],
        completed_run["warnings"],
    )

    # R7 / `mark_done` is `best_effort: true`, so the payload alone is not proof.
    card = board.show(completed_run["card_id"], repo_dir=project)
    assert card.status == "done", (
        card.status,
        completed_run["failed_phase"],
        completed_run["warnings"],
    )

    revisions = _git(worktree, "rev-list", "main..HEAD").split()
    assert revisions, "the branch carries no commits beyond main"

    hashes: set[str] = set()
    for revision in revisions:
        message = _git(worktree, "show", "-s", "--format=%B", revision)
        values = _plan_hashes(message)
        assert values, (revision, message)
        hashes.update(values)
    assert len(hashes) == 1, hashes

    validated: set[str] = set()
    for name, attempt in sorted(agent_attempts.items()):
        assert attempt.result_path is not None, name
        path = Path(attempt.result_path)
        assert path.is_file(), (name, path)
        model = task_workflow.TASK.phase(name).result
        try:
            model.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as error:
            pytest.fail(
                f"{name}: {path} does not validate against "
                f"{model.__name__}: {error}"
            )
        validated.add(name)

    # Non-vacuity: an empty `agent_attempts` would sail through the loop above.
    assert validated == set(AGENT_PHASES), sorted(validated)


def test_the_run_went_through_the_pygents_walk(project, completed_run, checkpoint_rows):
    """Non-vacuity: the pygents walk is the only one that checkpoints (its
    BEFORE_TURN hook). Reuses the module's one paid run; costs nothing extra.

    justification: none -- no real-claude-only behavior. The `e2e_fake` twin
    `test_production_wiring.py::test_the_run_went_through_the_pygents_walk`
    makes the same `checkpoint_rows > 0` assertion on the same no-injection
    `cli.run_card` path, and the checkpoint is written by the engine's
    BEFORE_TURN hook, not by anything the model does. Kept because shrinking
    the e2e tier below 5 is out of scope (test-tier spec section 8); flagged
    on brd card d4542989 instead of given an invented reason."""
    rows = checkpoint_rows(project, completed_run["run_id"])
    assert rows > 0, "the run wrote no checkpoint: it never reached the pygents walk"
