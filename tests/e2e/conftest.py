"""Production-wiring tier fixtures (spec "Fixtures"), shared with sibling 34d3388b.

Steps-tier ingredients (design §14 lines 477-492, as used by
`tests/test_cli.py:996-1052` and `tests/steps/test_worktree.py:47-62`): a real
temporary git repo, a real temporary brd board, and `XDG_DATA_HOME` inside
tmp, so `paths.data_dir()` and brd's own database never touch a developer's
home. Everything is module-scoped because one `cli.run_card` launches seven
real child processes and every test in a module reads the same finished run.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_manager import cli, models, paths, store

FAKE_CLAUDE_SOURCE = Path(__file__).with_name("fake_claude.py")
"""The script copied to a tmp dir as the `claude` the adapter will find."""

FAKE_LOG_NAME = "fake-claude.log"
"""Must equal `fake_claude.LOG_NAME`; the script and the tests meet across a
process boundary, and `test_fake_claude.py` pins the script's own constant."""

VERIFY_COMMANDS = ("git rev-parse --verify HEAD",)
"""A real, green command for this toy repo. Non-empty, so `verification_gate`
passes without `allow_no_verification`, and long enough per element that
`exploration_output_gate`'s plausibility check is satisfied."""

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)
"""`builtin/task.yaml`'s seven agent phases, in document order."""


def git(cwd: Path, *args: str) -> str:
    """Run one git command for fixture setup or assertion, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]["id"]


@pytest.fixture(scope="module")
def module_monkeypatch():
    """A module-scoped `monkeypatch`: the built-in one is function-scoped."""
    with pytest.MonkeyPatch.context() as patch:
        yield patch


@pytest.fixture(scope="module")
def toolchain() -> None:
    """Skip the whole module when the real CLIs this tier needs are missing."""
    for tool in ("git", "brd"):
        if shutil.which(tool) is None:
            pytest.skip(f"the {tool} CLI must be installed for the e2e tier")


@pytest.fixture(scope="module")
def project(tmp_path_factory, module_monkeypatch, toolchain) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board."""
    base = tmp_path_factory.mktemp("e2e")
    module_monkeypatch.setenv("XDG_DATA_HOME", str(base / "xdg"))
    root = base / "project"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(root, "config", "user.email", "tests@example.com")
    git(root, "config", "user.name", "agent-manager tests")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "base")
    subprocess.run(
        ["brd", "init", "--name", "e2e-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    # brd leaves its `.gitignore`/`.brd` markers untracked; committing them keeps
    # the baseline clean, so the later porcelain check reflects only the run.
    git(root, "add", "-A")
    git(root, "commit", "-m", "brd init")
    return root


@pytest.fixture(scope="module")
def cards(project) -> dict[str, str]:
    """A milestone -> story -> subtask chain, the shape `run --card` requires."""
    milestone = _add_card(project, "Milestone 1: the production wiring")
    story = _add_card(project, "Prove the wiring under a fake claude", milestone)
    subtask = _add_card(project, "Drive run --card with a fake harness", story)
    return {"milestone": milestone, "story": story, "subtask": subtask}


@pytest.fixture(scope="module")
def fake_claude_bin(tmp_path_factory, module_monkeypatch) -> Path:
    """The fake `claude`, first on `PATH` and the only one the run can find."""
    bin_dir = tmp_path_factory.mktemp("fake-bin")
    launcher = bin_dir / "claude"
    launcher.write_text(
        f"#!{sys.executable}\n" + FAKE_CLAUDE_SOURCE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    module_monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return launcher


@pytest.fixture(scope="module")
def completed_run(project, cards, fake_claude_bin) -> dict[str, Any]:
    """One real `cli.run_card`, with NO `runner_factory`.

    That omission is the point: `cli.default_runner_factory` (`cli.py:588`)
    builds the real `dispatch.AgentRunner` with the real
    `harness/launcher.py:94` `run_direct`, which `Popen`s the real
    `ClaudeAdapter` argv -- resolved on `PATH` to `fake_claude_bin`.
    """
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        commands=list(VERIFY_COMMANDS),
    )


@pytest.fixture(scope="module")
def run_tree(project, completed_run) -> models.Run:
    """The run's `Store` projection, read back the way `status` reads it."""
    conn = store.open_db(project)
    try:
        run = store.load_run(conn, completed_run["run_id"])
    finally:
        conn.close()
    assert run is not None, completed_run["run_id"]
    return run


@pytest.fixture(scope="module")
def agent_attempts(run_tree) -> dict[str, models.Attempt]:
    """The last recorded attempt of each agent phase, keyed by phase name."""
    found: dict[str, models.Attempt] = {}
    for story in run_tree.stories:
        for subtask in story.subtasks:
            for phase in subtask.phases:
                if phase.kind == "agent" and phase.attempts:
                    found[phase.name] = phase.attempts[-1]
    return found


@pytest.fixture(scope="module")
def worktree(project, completed_run) -> Path:
    """The subtask worktree: `<repo>/.claude/worktrees/<branch>` (`cli.py:142`)."""
    path = cli.worktree_for(project, completed_run["branch"])
    assert path == Path(completed_run["worktree"])
    return path


@pytest.fixture(scope="module")
def fake_log(completed_run) -> Path:
    """The fake's cwd log, under the run directory and outside every worktree."""
    return paths.run_dir(completed_run["run_id"]) / FAKE_LOG_NAME
