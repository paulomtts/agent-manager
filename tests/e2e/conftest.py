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
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_manager import board, census, cli, dag, models, paths, store

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
"""`TASK`'s seven agent phases, in document order."""

MILESTONE_PREFIX = "m3"
"""The `--branch-prefix` every milestone-run test uses."""

FAKE_REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""Must equal `fake_claude.REVIEW_FAIL_MARKER`, which `test_fake_claude.py` pins."""

FAKE_IMPLEMENT_EDITS_MARKER = "fake-claude-implement-edits"
"""Must equal `fake_claude.IMPLEMENT_EDITS_MARKER`, which `test_fake_claude.py` pins.

A JSON file in the repo's git common dir mapping a branch to
`{relative path: full file content}`: what that branch's implement writes."""

FAKE_RESOLVER_ENV = "FAKE_CLAUDE_RESOLVER"
"""Must equal `fake_claude.RESOLVER_ENV`, which `test_fake_claude.py` pins."""

FAKE_RENDEZVOUS_DIR_ENV = "FAKE_CLAUDE_RENDEZVOUS_DIR"
"""Must equal `fake_claude.RENDEZVOUS_DIR_ENV`, which `test_fake_claude.py` pins."""

FAKE_RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"
"""Must equal `fake_claude.RENDEZVOUS_COUNT_ENV`, which `test_fake_claude.py` pins."""

FAKE_HOLD_DIR_ENV = "FAKE_CLAUDE_HOLD_DIR"
"""Must equal `fake_claude.HOLD_DIR_ENV`, which `test_fake_claude.py` pins."""

FAKE_HOLD_PHASE_ENV = "FAKE_CLAUDE_HOLD_PHASE"
"""Must equal `fake_claude.HOLD_PHASE_ENV`, which `test_fake_claude.py` pins."""

FAKE_HOLD_SUFFIX = ".held"
"""Must equal `fake_claude.HOLD_SUFFIX`: the marker a held phase writes."""

FAKE_RELEASE_SUFFIX = ".release"
"""Must equal `fake_claude.RELEASE_SUFFIX`: the file that lets a held phase go on."""

FAKE_IMPLEMENTATION_NAME = "IMPLEMENTATION.md"
"""Must equal `fake_claude.IMPLEMENTATION_NAME`: the one file the fake coder writes."""

UNION_ATTRIBUTE = f"{FAKE_IMPLEMENTATION_NAME} merge=union\n"
"""Git's built-in union merge driver for the fake coder's file.

Integrate (card a74f2cd6) merges every story tip into one branch. On
`parallel_board`, A and B are independent roots and the fake coder writes
`IMPLEMENTATION.md` on both, so their tips would conflict and need a resolver
the fake does not have (its `resolve` phase is sibling a37460b9's, as are the
conflict scenarios). Written to the git common dir's `info/attributes`, it
applies in every linked worktree, is in no tree and never shows in
`git status`, and it tells the fake nothing: it only lets git fold the two
versions, so these lane tests stay about lanes."""


def git(cwd: Path, *args: str) -> str:
    """Run one git command for fixture setup or assertion, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _block(root: Path, card_id: str, blocker: str) -> None:
    """`brd block <id> --by <blocker>`, as `tests/test_orchestrate.py::_block` does."""
    subprocess.run(
        ["brd", "block", card_id, "--by", blocker],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _add_card(
    root: Path,
    title: str,
    parent: str | None = None,
    blocked_by: Sequence[str] = (),
) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    card_id = json.loads(completed.stdout)["data"]["id"]
    for blocker in blocked_by:
        _block(root, card_id, blocker)
    return card_id


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


def _init_project(root: Path, board_name: str) -> Path:
    """Make `root` both a real git repo on `main` and a real brd board.

    Shared by the module-scoped `project` and the per-test `fresh_project`, so
    the two tiers' repos cannot drift apart. The caller points `XDG_DATA_HOME`
    into tmp first.
    """
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
        ["brd", "init", "--name", board_name],
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
def project(tmp_path_factory, module_monkeypatch, toolchain) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board."""
    base = tmp_path_factory.mktemp("e2e")
    module_monkeypatch.setenv("XDG_DATA_HOME", str(base / "xdg"))
    return _init_project(base / "project", "e2e-board")


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


@pytest.fixture
def fresh_project(tmp_path, monkeypatch, toolchain) -> Path:
    """A new repo+board per test, with its own `XDG_DATA_HOME`.

    Function-scoped because a milestone run moves every card and branch it
    touches, so two scenarios cannot share one board.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return _init_project(tmp_path / "project", "e2e-milestone-board")


@pytest.fixture
def milestone_board(fresh_project) -> dict[str, Any]:
    """One milestone and three stories: A (a1 -> a2), B blocked by A (b1), C blocked by B (c1).

    A's subtasks are chained with `brd block`, so the census order does not
    depend on timestamps. Branch names come from `dag`, never retyped here.
    """
    root = fresh_project
    milestone = _add_card(root, "Milestone 3: run a milestone under a fake claude")
    a = _add_card(root, "Story A: the first level", milestone)
    b = _add_card(root, "Story B: blocked by story A", milestone, blocked_by=[a])
    c = _add_card(root, "Story C: blocked by story B", milestone, blocked_by=[b])
    a1 = _add_card(root, "a1: first subtask of story A", a)
    a2 = _add_card(root, "a2: second subtask of story A", a, blocked_by=[a1])
    b1 = _add_card(root, "b1: only subtask of story B", b)
    c1 = _add_card(root, "c1: only subtask of story C", c)
    subtasks = {"A": [a1, a2], "B": [b1], "C": [c1]}
    branches = {
        card_id: dag.task_branch(MILESTONE_PREFIX, board.show(card_id, repo_dir=root))
        for chain in subtasks.values()
        for card_id in chain
    }
    return {
        "root": root,
        "milestone": milestone,
        "stories": {"A": a, "B": b, "C": c},
        "subtasks": subtasks,
        "branches": branches,
    }


@dataclass
class Rendezvous:
    """Arms and disarms the fake's implement-only rendezvous for one test.

    Env vars go through the test's own function-scoped `monkeypatch`, so they
    are undone when the test ends. Child processes inherit them: `run_direct`
    calls `Popen` with no `env=` (`harness/launcher.py:132`).
    """

    directory: Path
    monkeypatch: pytest.MonkeyPatch

    def arm(self, count: int) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.monkeypatch.setenv(FAKE_RENDEZVOUS_DIR_ENV, str(self.directory))
        self.monkeypatch.setenv(FAKE_RENDEZVOUS_COUNT_ENV, str(count))
        return self.directory

    def disarm(self) -> None:
        self.monkeypatch.delenv(FAKE_RENDEZVOUS_DIR_ENV, raising=False)
        self.monkeypatch.delenv(FAKE_RENDEZVOUS_COUNT_ENV, raising=False)

    def markers(self) -> list[Path]:
        """The markers the fake left, one per distinct implement cwd."""
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.iterdir())


@pytest.fixture
def rendezvous(tmp_path, monkeypatch) -> Rendezvous:
    """The test's rendezvous, unarmed. Its dir is beside the repo, never inside it."""
    return Rendezvous(directory=tmp_path / "rendezvous", monkeypatch=monkeypatch)


@dataclass
class FakeResolver:
    """Switches the fake's resolve phase between resolving and refusing for one test.

    The env var goes through the test's own function-scoped `monkeypatch`, so it
    is undone when the test ends. Child processes inherit it: `run_direct`
    calls `Popen` with no `env=` (`harness/launcher.py:132`).
    """

    monkeypatch: pytest.MonkeyPatch

    def refuse(self) -> None:
        self.monkeypatch.setenv(FAKE_RESOLVER_ENV, "refuse")

    def reset(self) -> None:
        self.monkeypatch.delenv(FAKE_RESOLVER_ENV, raising=False)


@pytest.fixture
def fake_resolver(monkeypatch) -> FakeResolver:
    """The test's resolver switch, starting in resolve mode."""
    resolver = FakeResolver(monkeypatch=monkeypatch)
    resolver.reset()
    return resolver


@pytest.fixture
def parallel_board(fresh_project) -> dict[str, Any]:
    """One milestone: A (a1 -> a2) and B (b1 -> b2) independent, C (c1) blocked by A.

    Level 0 is A and B, level 1 is C. Subtasks are chained with `brd block`, so
    the census order does not depend on timestamps. Branch names come from
    `dag`, never retyped here. `review_fail_marker` is where the fake looks for
    branches whose review must fail; the test writes it and removes it.
    The union attribute (`UNION_ATTRIBUTE`) lets Integrate fold A's and B's `IMPLEMENTATION.md` without a resolver.
    """
    root = fresh_project
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    milestone = _add_card(root, "Milestone 4: parallel stories under a fake claude")
    a = _add_card(root, "Story A: an independent root story", milestone)
    b = _add_card(root, "Story B: independent of story A", milestone)
    c = _add_card(root, "Story C: blocked by story A", milestone, blocked_by=[a])
    a1 = _add_card(root, "a1: first subtask of story A", a)
    a2 = _add_card(root, "a2: second subtask of story A", a, blocked_by=[a1])
    b1 = _add_card(root, "b1: first subtask of story B", b)
    b2 = _add_card(root, "b2: second subtask of story B", b, blocked_by=[b1])
    c1 = _add_card(root, "c1: only subtask of story C", c)
    subtasks = {"A": [a1, a2], "B": [b1, b2], "C": [c1]}
    branches = {
        card_id: dag.task_branch(MILESTONE_PREFIX, board.show(card_id, repo_dir=root))
        for chain in subtasks.values()
        for card_id in chain
    }
    return {
        "root": root,
        "milestone": milestone,
        "stories": {"A": a, "B": b, "C": c},
        "subtasks": subtasks,
        "branches": branches,
        "review_fail_marker": root / ".git" / FAKE_REVIEW_FAIL_MARKER,
    }


@pytest.fixture
def merged_base_board(fresh_project) -> dict[str, Any]:
    """One milestone: A (a1) and B (b1) independent, C (c1) blocked by BOTH, D (d1 -> d2 -> d3) independent.

    C is the only multi-blocker story, so its lane builds the merged base from
    A's and B's tips before c1 runs (supervisor-tree §5). D is a sibling lane
    with more work than A and B, so it is still running when C's base is
    built. This is a new fixture beside `parallel_board`, which keeps covering
    the lone-blocker fast path. `base_branch` and `merged_from` come from
    `dag.story_root` over the census, never retyped: `merged_from` is C's
    blockers in the order brd reports them, the order their tips are merged
    in. `UNION_ATTRIBUTE` folds the fake coder's `IMPLEMENTATION.md`, so only
    the implement-edits marker's own files can make A's and B's tips conflict.
    """
    root = fresh_project
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    milestone = _add_card(root, "Milestone 7: a merged base under a fake claude")
    a = _add_card(root, "Story A: one blocker of story C", milestone)
    b = _add_card(root, "Story B: the other blocker of story C", milestone)
    c = _add_card(root, "Story C: blocked by stories A and B", milestone, blocked_by=[a, b])
    d = _add_card(root, "Story D: a sibling lane beside them", milestone)
    a1 = _add_card(root, "a1: only subtask of story A", a)
    b1 = _add_card(root, "b1: only subtask of story B", b)
    c1 = _add_card(root, "c1: only subtask of story C", c)
    d1 = _add_card(root, "d1: first subtask of story D", d)
    d2 = _add_card(root, "d2: second subtask of story D", d, blocked_by=[d1])
    d3 = _add_card(root, "d3: third subtask of story D", d, blocked_by=[d2])
    subtasks = {"A": [a1], "B": [b1], "C": [c1], "D": [d1, d2, d3]}
    branches = {
        card_id: dag.task_branch(MILESTONE_PREFIX, board.show(card_id, repo_dir=root))
        for chain in subtasks.values()
        for card_id in chain
    }
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=root))
    by_id = {story.id: story for story in plan.stories}
    c_root = dag.story_root(by_id[c], by_id, MILESTONE_PREFIX, "main")
    assert c_root.kind == "merged", c_root
    return {
        "root": root,
        "milestone": milestone,
        "stories": {"A": a, "B": b, "C": c, "D": d},
        "subtasks": subtasks,
        "branches": branches,
        "base_branch": c_root.branch,
        "merged_from": list(c_root.blockers),
        "review_fail_marker": root / ".git" / FAKE_REVIEW_FAIL_MARKER,
        "implement_edits_marker": root / ".git" / FAKE_IMPLEMENT_EDITS_MARKER,
    }


@pytest.fixture
def two_story_board(fresh_project) -> dict[str, Any]:
    """One milestone with two independent stories, A (a1) and B (b1), for Integrate.

    Both stories are level-0 roots, so Integrate merges A's tip and then B's
    into `m3-integrate`. `UNION_ATTRIBUTE` folds the fake coder's
    `IMPLEMENTATION.md` and covers only that file, so any conflict a scenario
    wants comes from the implement-edits marker's own files. The test writes
    that marker (`implement_edits_marker`) keyed by `branches`.
    """
    root = fresh_project
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    milestone = _add_card(root, "Milestone 5: integrate under a fake claude")
    a = _add_card(root, "Story A: one side of the merge", milestone)
    b = _add_card(root, "Story B: the other side of the merge", milestone)
    a1 = _add_card(root, "a1: only subtask of story A", a)
    b1 = _add_card(root, "b1: only subtask of story B", b)
    subtasks = {"A": [a1], "B": [b1]}
    branches = {
        card_id: dag.task_branch(MILESTONE_PREFIX, board.show(card_id, repo_dir=root))
        for chain in subtasks.values()
        for card_id in chain
    }
    return {
        "root": root,
        "milestone": milestone,
        "stories": {"A": a, "B": b},
        "subtasks": subtasks,
        "branches": branches,
        "implement_edits_marker": root / ".git" / FAKE_IMPLEMENT_EDITS_MARKER,
    }


@pytest.fixture
def two_milestone_board(fresh_project) -> dict[str, Any]:
    """Two milestones on one board, each with two independent one-subtask stories.

    First: A (a1) and B (b1). Second: C (c1) and D (d1). Nothing is shared, so
    two `am` processes can drive them at once; which prefix each uses is the
    test's choice, so branch names are derived in the test, never here.
    `UNION_ATTRIBUTE` lets each Integrate fold its two stories'
    `IMPLEMENTATION.md` without a resolver.
    """
    root = fresh_project
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    first = _add_card(root, "Milestone 10a: the first of two concurrent milestones")
    a = _add_card(root, "Story A: one side of the first milestone", first)
    b = _add_card(root, "Story B: other side of the first milestone", first)
    second = _add_card(root, "Milestone 10b: the second of two concurrent milestones")
    c = _add_card(root, "Story C: one side of the second milestone", second)
    d = _add_card(root, "Story D: other side of the second milestone", second)
    a1 = _add_card(root, "a1: only subtask of story A", a)
    b1 = _add_card(root, "b1: only subtask of story B", b)
    c1 = _add_card(root, "c1: only subtask of story C", c)
    d1 = _add_card(root, "d1: only subtask of story D", d)
    return {
        "root": root,
        "milestones": {"first": first, "second": second},
        "stories": {"first": [a, b], "second": [c, d]},
        "subtasks": {"first": [a1, b1], "second": [c1, d1]},
    }


@pytest.fixture
def run_milestone_cli(fake_claude_bin) -> Callable[..., Any]:
    """`am run --milestone` through `CliRunner`, with no runner_factory anywhere.

    Depends on `fake_claude_bin` so the fake is first on `PATH`: the real
    `ClaudeAdapter` resolves `claude` to it through the real `run_direct`.
    """
    runner = CliRunner()

    def invoke(
        root: Path,
        milestone: str,
        max_concurrent: int | None = None,
        verify: Sequence[str] | None = None,
    ):
        argv = [
            "run",
            "--milestone",
            milestone,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            MILESTONE_PREFIX,
        ]
        commands = VERIFY_COMMANDS if verify is None else tuple(verify)
        for command in commands:
            argv += ["--verify", command]
        # Only when asked: existing callers keep their exact argv.
        if max_concurrent is not None:
            argv += ["--max-concurrent", str(max_concurrent)]
        return runner.invoke(cli.app, argv)

    return invoke


@pytest.fixture
def read_fake_log() -> Callable[[str], list[dict[str, Any]]]:
    """The fake's cwd log for one run id, or `[]` for a run that launched no agent."""

    def read(run_id: str) -> list[dict[str, Any]]:
        log = paths.run_dir(run_id) / FAKE_LOG_NAME
        if not log.is_file():
            return []
        return [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return read


@pytest.fixture
def review_fail_marker(milestone_board) -> Path:
    """Where the fake looks for branches whose review must fail.

    The repo's git common dir, which the fake reaches from any worktree's cwd
    through `git rev-parse --git-common-dir`. Inside `.git`, so it is in no
    worktree's tree and never in `git status`. The test writes it and removes it.
    """
    return milestone_board["root"] / ".git" / FAKE_REVIEW_FAIL_MARKER


@pytest.fixture
def checkpoint_rows() -> Callable[[Path, str], int]:
    """How many `checkpoints` rows one run wrote.

    A run with none never reached the pygents walk's BEFORE_TURN hook, and
    every assertion about that run would have passed on something else.
    """

    def count(root: Path, run_id: str) -> int:
        conn = store.open_db(cli.resolve_repo_dir(root))
        try:
            (rows,) = conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        return int(rows)

    return count


AM_ENTRY = "from agent_manager.cli import app; app()"
"""What a spawned `am` child runs: the real Typer app, argv from its own `sys.argv`."""

AM_WAIT = 240.0
"""Seconds a spawned `am` or a marker wait may take. It only bounds a broken
run; a healthy one never waits this long."""

MARKER_POLL = 0.05
"""Seconds between checks for a marker file. A polling cadence, never an ordering."""


def _describe(child: subprocess.Popen) -> str:
    return " ".join(str(part) for part in child.args)


@dataclass
class AmProcesses:
    """Every child process one test started, killed and reaped by `close`.

    stdout is a pipe (the envelope); stderr goes to a per-child file under
    `log_dir`, so a chatty child can never fill a pipe nobody reads.
    """

    log_dir: Path
    children: list[subprocess.Popen] = field(default_factory=list)
    stderr_paths: dict[int, Path] = field(default_factory=dict)

    def child_env(self, env: Mapping[str, str] | None) -> dict[str, str]:
        """The test's own environment, with `env` laid over it."""
        return {**os.environ, **(env or {})}

    def track(self, child: subprocess.Popen) -> subprocess.Popen:
        self.children.append(child)
        return child

    def spawn(self, *args: str, env: Mapping[str, str] | None = None) -> subprocess.Popen:
        """Start `am *args` as a real child process and track it."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stderr_path = self.log_dir / f"am-{len(self.children)}.stderr"
        with stderr_path.open("w", encoding="utf-8") as stderr:
            child = subprocess.Popen(
                [sys.executable, "-c", AM_ENTRY, *args],
                env=self.child_env(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
            )
        self.stderr_paths[child.pid] = stderr_path
        return self.track(child)

    def stderr_of(self, child: subprocess.Popen) -> str:
        path = self.stderr_paths.get(child.pid)
        if path is None or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def finish(
        self, child: subprocess.Popen, timeout: float = AM_WAIT
    ) -> tuple[int, dict[str, Any]]:
        """Wait for `child` and return its exit code and parsed envelope."""
        try:
            stdout, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            pytest.fail(
                f"`{_describe(child)}` did not exit within {timeout}s\n"
                f"stderr: {self.stderr_of(child)}"
            )
        try:
            envelope = json.loads(stdout)
        except (TypeError, json.JSONDecodeError):
            pytest.fail(
                f"`{_describe(child)}` exited {child.returncode} without a JSON envelope\n"
                f"stdout: {stdout!r}\nstderr: {self.stderr_of(child)}"
            )
        return child.returncode, envelope

    def wait_for_file(
        self, path: Path, child: subprocess.Popen, timeout: float = AM_WAIT
    ) -> Path:
        """`path` once it exists; fail at once if `child` exits first, or at the deadline."""
        deadline = time.monotonic() + timeout
        while not path.exists():
            if child.poll() is not None:
                if path.exists():
                    break
                stdout, _ = child.communicate()
                pytest.fail(
                    f"{path} never appeared: `{_describe(child)}` exited "
                    f"{child.returncode} first\nstdout: {stdout!r}\n"
                    f"stderr: {self.stderr_of(child)}"
                )
            if time.monotonic() >= deadline:
                pytest.fail(
                    f"{path} did not appear within {timeout}s; "
                    f"`{_describe(child)}` is still running"
                )
            time.sleep(MARKER_POLL)
        return path

    def close(self) -> None:
        """Kill every child still running, reap every child, close every pipe."""
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()
            if child.stdout is not None and not child.stdout.closed:
                child.stdout.close()


@pytest.fixture
def am_processes(tmp_path) -> Any:
    """The test's child processes; every one is killed and reaped at teardown."""
    processes = AmProcesses(log_dir=tmp_path / "am-logs")
    yield processes
    processes.close()


@pytest.fixture
def spawn_am(am_processes) -> Callable[..., subprocess.Popen]:
    """`spawn_am(*args, env=None)`: start a real `am` child and return it."""
    return am_processes.spawn


@pytest.fixture
def finish_am(am_processes) -> Callable[..., tuple[int, dict[str, Any]]]:
    """`finish_am(child)`: wait for a spawned child, return `(exit code, envelope)`."""
    return am_processes.finish


@pytest.fixture
def am(am_processes) -> Callable[..., tuple[int, dict[str, Any]]]:
    """`am(*args, env=None)`: run one `am` child to completion, return `(exit code, envelope)`."""

    def run(*args: str, env: Mapping[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        return am_processes.finish(am_processes.spawn(*args, env=env))

    return run


@pytest.fixture
def wait_for_file(am_processes) -> Callable[..., Path]:
    """`wait_for_file(path, child, timeout=AM_WAIT)`: order by a marker, never by a sleep."""
    return am_processes.wait_for_file


@dataclass
class Hold:
    """Arms the fake's env-only hold for one test and releases held cards.

    Env vars go through the test's function-scoped `monkeypatch`, so they are
    undone when the test ends; `am` children inherit them, and the fake
    inherits them from `am` (`run_direct` passes no `env=`).
    """

    directory: Path
    monkeypatch: pytest.MonkeyPatch

    def arm(self, phase: str | None = None) -> Path:
        """Hold `phase` (the fake's default, `implement`, when `None`)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        self.monkeypatch.setenv(FAKE_HOLD_DIR_ENV, str(self.directory))
        if phase is None:
            self.monkeypatch.delenv(FAKE_HOLD_PHASE_ENV, raising=False)
        else:
            self.monkeypatch.setenv(FAKE_HOLD_PHASE_ENV, phase)
        return self.directory

    def held_marker(self, card_id: str) -> Path:
        """Where the fake announces that `card_id`'s phase is held."""
        return self.directory / f"{dag.short_id(card_id)}{FAKE_HOLD_SUFFIX}"

    def release(self, *card_ids: str) -> None:
        """Let each card's held phase go on (or pass straight through later)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        for card_id in card_ids:
            (self.directory / f"{dag.short_id(card_id)}{FAKE_RELEASE_SUFFIX}").write_text(
                "", encoding="utf-8"
            )

    def release_all(self) -> None:
        """Release every card that has announced a hold."""
        if not self.directory.is_dir():
            return
        for marker in self.directory.glob(f"*{FAKE_HOLD_SUFFIX}"):
            marker.with_suffix(FAKE_RELEASE_SUFFIX).write_text("", encoding="utf-8")

    def holder_pid(self, card_id: str) -> int:
        """The pid of the fake `claude` holding `card_id`, from its marker."""
        return int(self.held_marker(card_id).read_text(encoding="utf-8").strip())


@pytest.fixture
def hold(tmp_path, monkeypatch) -> Any:
    """The test's hold, unarmed. Its dir is beside the repo, never inside it.
    Every held card is released at teardown, so no fake polls past its test."""
    held = Hold(directory=tmp_path / "hold", monkeypatch=monkeypatch)
    yield held
    held.release_all()
