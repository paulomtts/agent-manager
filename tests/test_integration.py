"""integrate_milestone: every story tip merged into one branch, then verified.

Card 6fea51ad (Integrate addendum I1, I3, I4, I5). Default-suite tests that mirror
`src/agent_manager/integration.py`: not under `tests/e2e/` and not marked `e2e`.

Everything is real except the harness: temporary git repos with a bare `origin`,
a real `Store` and journal, the shipped `builtin/integrate.yaml` driven by
`engine.run_subtask`, and `dispatch.AgentRunner` as the agent runner. The runner
factory hands out an `AgentRunner` whose launcher is a fake resolver. The fake
learns the conflicting files and its result path only by parsing the brief it
is handed. It never asks git for the conflict list, never computes a plan hash
and never commits documents. `AM_TEST_FAKE_RESOLVER_REFUSE=1` makes it refuse.

Anything git can measure is asserted through git. Every test also asserts that
the base branch, the story branches, the main checkout and `origin` are
unchanged: Integrate never writes them and never pushes.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_manager import cli, dag, dispatch, models
from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.harness.base import Outcome
from agent_manager.integration import (
    IntegrateEscalation,
    IntegrateSuccess,
    integrate_milestone,
)
from agent_manager.steps.integrate import merge_tip
from agent_manager.steps.worktree import GitError
from agent_manager.store import Store

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for integrate_milestone's tests",
)

BASE = "main"
PREFIX = "m5"
INTEGRATION_BRANCH = "m5-integrate"
RUN_ID = "run-2026-09-25-integrate-milestone"
REFUSE_ENV = "AM_TEST_FAKE_RESOLVER_REFUSE"
RESOLVED = "resolved line\n"

STORY_A = "5a5a5a5a-0000-4000-8000-00000000000a"
STORY_B = "5b5b5b5b-0000-4000-8000-00000000000b"
STORY_C = "5c5c5c5c-0000-4000-8000-00000000000c"
STORY_D = "5d5d5d5d-0000-4000-8000-00000000000d"
STORY_EMPTY = "5e5e5e5e-0000-4000-8000-00000000000e"
SUB_A = "a1a1a1a1-0000-4000-8000-000000000001"
SUB_B = "b2b2b2b2-0000-4000-8000-000000000002"
SUB_C = "c3c3c3c3-0000-4000-8000-000000000003"
SUB_D = "d4d4d4d4-0000-4000-8000-000000000004"

PASS_CMD = shlex.join([sys.executable, "-c", "print('suite green')"])
FAIL_CMD = shlex.join(
    [sys.executable, "-c", "import sys; print('suite is red'); sys.exit(3)"]
)


def _cwd_recorder(marker: Path) -> str:
    """A passing command that appends the directory it ran in to `marker`."""
    code = (
        "import os, pathlib; "
        f"pathlib.Path({str(marker)!r}).open('a').write(os.getcwd() + '\\n')"
    )
    return shlex.join([sys.executable, "-c", code])


# ── git helpers ──────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _sha(cwd: Path, ref: str) -> str:
    return _git(cwd, "rev-parse", ref).strip()


def _merge_head(worktree: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _is_ancestor(worktree: Path, ref: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", ref, "HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _merged_second_parents(worktree: Path) -> list[str]:
    """The tip each first-parent merge above the base brought in, oldest first."""
    merges = _git(worktree, "rev-list", "--first-parent", "--reverse", f"{BASE}..HEAD")
    return [_sha(worktree, f"{commit}^2") for commit in merges.split()]


# ── the repo, the census and the store ───────────────────────────────────────


@dataclass
class Repo:
    root: Path
    tmp: Path

    @property
    def worktree(self) -> Path:
        return cli.worktree_for(self.root, INTEGRATION_BRANCH)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Repo:
    """A repo on `main` with a bare `origin`, holding README.md and shared.txt.

    `.claude/` is excluded the way projects ignore it, so the integration
    worktree under `.claude/worktrees/` does not show in the main checkout's
    `git status`.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv(REFUSE_ENV, raising=False)

    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", BASE, str(root)], capture_output=True, text=True, check=True
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    exclude = root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\n.claude/\n")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    (root / "shared.txt").write_text("shared line\n", encoding="utf-8")
    _git(root, "add", "README.md", "shared.txt")
    _git(root, "commit", "-m", "base")

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(root), str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "fetch", "origin")
    return Repo(root=root, tmp=tmp_path)


@pytest.fixture
def store(repo: Repo) -> Iterator[Store]:
    """A real store with the run already recorded, as the milestone runner does."""
    opened = Store.open(repo.root, RUN_ID)
    opened.record_run(
        models.Run(
            id=RUN_ID,
            workflow="task",
            repo_dir=repo.root,
            base_branch=BASE,
            branch_prefix=PREFIX,
            status="started",
        )
    )
    yield opened
    opened.close()


def _story(
    story_id: str,
    title: str,
    subtask_id: str | None = None,
    blocked_by: tuple[str, ...] = (),
) -> StoryPlan:
    subtasks = (
        []
        if subtask_id is None
        else [SubtaskPlan(id=subtask_id, title=f"{title} work", status="done")]
    )
    return StoryPlan(
        id=story_id,
        title=title,
        status="done",
        blocked_by=list(blocked_by),
        subtasks=subtasks,
    )


def _tip_branch(story: StoryPlan) -> str:
    return dag.subtask_branch(PREFIX, story.subtasks[-1])


def _story_branch(
    repo: Repo, story: StoryPlan, files: dict[str, str], start: str = BASE
) -> str:
    """Create the story's tip branch from `start` with `files` committed; return it."""
    branch = _tip_branch(story)
    scratch = repo.tmp / f"scratch-{dag.short_id(story.subtasks[-1].id)}"
    _git(repo.root, "worktree", "add", str(scratch), "-b", branch, start)
    for name, body in files.items():
        (scratch / name).write_text(body, encoding="utf-8")
    _git(scratch, "add", *files)
    _git(scratch, "commit", "-m", f"work on {branch}")
    _git(repo.root, "worktree", "remove", str(scratch))
    return branch


def _protected(repo: Repo, branches: list[str]) -> dict[str, str]:
    """Everything Integrate must never change."""
    state = {
        "base": _sha(repo.root, f"refs/heads/{BASE}"),
        "checked_out": _git(repo.root, "symbolic-ref", "HEAD").strip(),
        "status": _git(repo.root, "status", "--porcelain"),
        "origin_refs": _git(repo.root, "ls-remote", "origin"),
    }
    for branch in branches:
        state[branch] = _sha(repo.root, f"refs/heads/{branch}")
    return state


def _assert_protected(repo: Repo, before: dict[str, str], branches: list[str]) -> None:
    after = _protected(repo, branches)
    assert after == before
    assert INTEGRATION_BRANCH not in after["origin_refs"]


# ── the harness doubles ──────────────────────────────────────────────────────


class _FakeAdapter:
    """A `HarnessAdapter` by shape. Its argv names the brief and nothing else."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return ["fake-resolver", "--prompt", str(d.prompt_path)]

    def parse_usage(self, stdout: str) -> None:
        return None


_CONFLICT_HEADING = "\n## conflict_files\n"
_RESULT_LEAD = "write your result as valid JSON to exactly this path:\n\n"


def _conflict_files_from(brief: str) -> list[str]:
    start = brief.index(_CONFLICT_HEADING) + len(_CONFLICT_HEADING)
    files, _end = json.JSONDecoder().raw_decode(brief, start)
    return files


def _result_path_from(brief: str) -> Path:
    start = brief.index(_RESULT_LEAD) + len(_RESULT_LEAD)
    return Path(brief[start : brief.index("\n", start)])


@dataclass
class FakeResolver:
    """A `LauncherFn` double that plays the resolver in the worktree it runs in.

    It reads only the brief. It resolves by writing `RESOLVED` into every listed
    file, staging them and committing the merge. With `REFUSE_ENV=1` it changes
    nothing and reports `resolved: false`.
    """

    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        brief = Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
        files = _conflict_files_from(brief)
        self.calls.append(files)
        worktree = Path(cwd)
        if os.environ.get(REFUSE_ENV) == "1":
            result: dict[str, Any] = {"resolved": False, "summary": "refusing to resolve"}
        else:
            for name in files:
                (worktree / name).write_text(RESOLVED, encoding="utf-8")
            _git(worktree, "add", *files)
            _git(worktree, "commit", "--no-edit")
            result = {"resolved": True, "summary": f"resolved {', '.join(files)}"}
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        _result_path_from(brief).write_text(json.dumps(result), encoding="utf-8")
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.1,
            stdout_path=stdout_path,
        )


@dataclass
class FakeFactory:
    """A `cli.RunnerFactory` that counts dispatches and wires in `FakeResolver`."""

    resolver: FakeResolver = field(default_factory=FakeResolver)
    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, *, workflow, store, run_id, story_id, card_id):
        self.calls.append(
            {
                "workflow": workflow.name,
                "run_id": run_id,
                "story_id": story_id,
                "card_id": card_id,
            }
        )
        adapter = _FakeAdapter()
        return dispatch.AgentRunner(
            workflow=workflow,
            store=store,
            launcher=self.resolver,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            adapters={adapter.name: adapter},
            harness_map={
                "resolver": models.HarnessAssignment(harness=adapter.name, model="fake-model")
            },
        )


def _integrate(
    repo: Repo,
    store: Store,
    stories: list[StoryPlan],
    *,
    factory: FakeFactory,
    commands: list[str] | None = None,
    allow_no_verification: bool = False,
):
    return integrate_milestone(
        stories=stories,
        repo_dir=repo.root,
        base_branch=BASE,
        branch_prefix=PREFIX,
        commands=[PASS_CMD] if commands is None else commands,
        allow_no_verification=allow_no_verification,
        store=store,
        run_id=RUN_ID,
        runner_factory=factory,
    )


# ── clean merges and the final verification ─────────────────────────────────


def _clean_milestone(repo: Repo) -> tuple[list[StoryPlan], list[str]]:
    """Census [B, C, empty, A], where B is blocked by A and stacked on A's tip.

    Levels: [C, empty, A] then [B]. Merge order is therefore C, A, B, and the
    empty story is skipped.
    """
    story_a = _story(STORY_A, "Story A", SUB_A)
    story_b = _story(STORY_B, "Story B", SUB_B, blocked_by=(STORY_A,))
    story_c = _story(STORY_C, "Story C", SUB_C)
    empty = _story(STORY_EMPTY, "Empty story")
    tip_a = _story_branch(repo, story_a, {"a.txt": "from a\n"})
    tip_b = _story_branch(repo, story_b, {"b.txt": "from b\n"}, start=tip_a)
    tip_c = _story_branch(repo, story_c, {"c.txt": "from c\n"})
    return [story_b, story_c, empty, story_a], [tip_c, tip_a, tip_b]


def test_clean_tips_merge_in_level_then_census_order_and_dispatch_no_agent(
    repo: Repo, store: Store
) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)
    marker = repo.tmp / "verified.txt"
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory, commands=[_cwd_recorder(marker)])

    assert isinstance(outcome, IntegrateSuccess)
    assert outcome.branch == INTEGRATION_BRANCH
    assert outcome.worktree == repo.worktree
    assert outcome.merged == [STORY_C, STORY_A, STORY_B]
    assert outcome.resolved == []
    assert factory.calls == []
    assert factory.resolver.calls == []
    # git, not the outcome, says what was merged and in which order.
    assert _git(repo.worktree, "symbolic-ref", "--short", "HEAD").strip() == INTEGRATION_BRANCH
    assert _merge_head(repo.worktree) is None
    assert _merged_second_parents(repo.worktree) == [_sha(repo.root, tip) for tip in tips]
    # The final verification ran exactly once, in the integration worktree.
    ran_in = [Path(line).resolve() for line in marker.read_text().splitlines()]
    assert ran_in == [repo.worktree.resolve()]
    assert store.load_run(RUN_ID).stories == []
    _assert_protected(repo, before, tips)


def test_a_failing_final_verification_escalates_after_the_merges(
    repo: Repo, store: Store
) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory, commands=[FAIL_CMD])

    assert isinstance(outcome, IntegrateEscalation)
    assert outcome.phase == "integrate"
    assert outcome.story is None
    assert outcome.files == []
    assert "verification failed" in outcome.detail
    assert factory.calls == []
    for tip in tips:
        assert _is_ancestor(repo.worktree, tip)
    _assert_protected(repo, before, tips)


def test_empty_commands_without_the_opt_out_escalate_after_merging(
    repo: Repo, store: Store
) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)

    outcome = _integrate(repo, store, stories, factory=FakeFactory(), commands=[])

    assert isinstance(outcome, IntegrateEscalation)
    assert outcome.phase == "integrate"
    assert outcome.story is None
    assert "no full-suite command is available" in outcome.detail
    for tip in tips:
        assert _is_ancestor(repo.worktree, tip)
    _assert_protected(repo, before, tips)


def test_empty_commands_with_the_opt_out_skip_the_final_check(
    repo: Repo, store: Store
) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)

    outcome = _integrate(
        repo, store, stories, factory=FakeFactory(), commands=[], allow_no_verification=True
    )

    assert isinstance(outcome, IntegrateSuccess)
    assert outcome.merged == [STORY_C, STORY_A, STORY_B]
    _assert_protected(repo, before, tips)


def test_an_already_integrated_milestone_is_a_no_op(repo: Repo, store: Store) -> None:
    stories, tips = _clean_milestone(repo)
    before = _protected(repo, tips)
    marker = repo.tmp / "verified.txt"
    factory = FakeFactory()

    first = _integrate(repo, store, stories, factory=factory, commands=[_cwd_recorder(marker)])
    assert isinstance(first, IntegrateSuccess)
    head = _sha(repo.worktree, "HEAD")

    second = _integrate(repo, store, stories, factory=factory, commands=[_cwd_recorder(marker)])

    assert isinstance(second, IntegrateSuccess)
    assert second.merged == [STORY_C, STORY_A, STORY_B]
    assert second.resolved == []
    assert factory.calls == []
    assert _sha(repo.worktree, "HEAD") == head
    assert _sha(repo.root, f"refs/heads/{INTEGRATION_BRANCH}") == head
    # Rule 6: the final verification still runs on the second pass.
    assert len(marker.read_text().splitlines()) == 2
    _assert_protected(repo, before, tips)


def test_a_missing_story_tip_propagates_the_git_error(repo: Repo, store: Store) -> None:
    """A story whose last subtask never produced a branch is a bad ref. That is
    a git failure other than a merge in progress, so it propagates."""
    story_a = _story(STORY_A, "Story A", SUB_A)
    story_d = _story(STORY_D, "Story D", SUB_D)
    tip_a = _story_branch(repo, story_a, {"a.txt": "from a\n"})
    before = _protected(repo, [tip_a])
    factory = FakeFactory()

    with pytest.raises(GitError):
        _integrate(repo, store, [story_a, story_d], factory=factory)

    assert factory.calls == []
    _assert_protected(repo, before, [tip_a])
