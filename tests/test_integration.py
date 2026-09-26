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

from agent_manager import cli, dag, dispatch, integration, models
from agent_manager import engine as yaml_engine
from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.harness.base import Outcome
from agent_manager.integration import (
    IntegrateEscalation,
    IntegrateSuccess,
    integrate_milestone,
)
from agent_manager.runtime import engine as runtime_engine
from agent_manager.steps.integrate import merge_tip
from agent_manager.steps.worktree import GitError
from agent_manager.store import Store
from agent_manager.workflow import integrate as integrate_workflow
from agent_manager.workflow import loader

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
    engine: str = "yaml",
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
        engine=engine,
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

# ── a merge already in progress ──────────────────────────────────────────────


def _conflicting_pair(repo: Repo) -> tuple[list[StoryPlan], list[str]]:
    """Stories A and B, both cut from the base, editing the same line of shared.txt."""
    story_a = _story(STORY_A, "Story A", SUB_A)
    story_b = _story(STORY_B, "Story B", SUB_B)
    tip_a = _story_branch(repo, story_a, {"shared.txt": "story a\n"})
    tip_b = _story_branch(repo, story_b, {"shared.txt": "story b\n"})
    return [story_a, story_b], [tip_a, tip_b]


def test_a_merge_already_in_progress_escalates_without_dispatching(
    repo: Repo, store: Store
) -> None:
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    # An earlier run left B's conflict unresolved in the integration worktree.
    assert merge_tip(repo.root, repo.worktree, INTEGRATION_BRANCH, BASE, tips[0])["conflict"] is False
    assert merge_tip(repo.root, repo.worktree, INTEGRATION_BRANCH, BASE, tips[1])["conflict"] is True
    merge_head = _merge_head(repo.worktree)
    head = _sha(repo.worktree, "HEAD")
    assert merge_head is not None
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory)

    assert isinstance(outcome, IntegrateEscalation)
    assert outcome.phase == "integrate"
    assert outcome.story == STORY_A
    assert outcome.files == []
    assert "A human must finish it" in outcome.detail
    assert "relaunch" in outcome.detail
    assert str(repo.worktree) in outcome.detail
    assert factory.calls == []
    assert factory.resolver.calls == []
    assert _merge_head(repo.worktree) == merge_head
    assert _sha(repo.worktree, "HEAD") == head
    assert store.load_run(RUN_ID).stories == []
    _assert_protected(repo, before, tips)

# ── conflicts go to the resolver ─────────────────────────────────────────────


def _integrate_story(store: Store) -> models.StoryRun:
    run = store.load_run(RUN_ID)
    assert [story.card_id for story in run.stories] == ["integrate"]
    return run.stories[0]


def test_a_conflict_dispatches_exactly_once_for_the_conflicting_tip(
    repo: Repo, store: Store
) -> None:
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory)

    assert isinstance(outcome, IntegrateSuccess), outcome
    assert outcome.merged == [STORY_A, STORY_B]
    assert outcome.resolved == [STORY_B]
    assert factory.calls == [
        {"workflow": "integrate", "run_id": RUN_ID, "story_id": "integrate", "card_id": STORY_B}
    ]
    assert factory.resolver.calls == [["shared.txt"]]
    # git judges the merge, not the resolver's report.
    assert _merge_head(repo.worktree) is None
    assert _git(repo.worktree, "status", "--porcelain") == ""
    assert _is_ancestor(repo.worktree, tips[0])
    assert _is_ancestor(repo.worktree, tips[1])
    assert _git(repo.worktree, "show", "HEAD:shared.txt") == RESOLVED
    # The synthetic story and subtask are in the store, and replay agrees.
    story = _integrate_story(store)
    assert story.title == "Integrate"
    assert story.status == "done"
    [subtask] = story.subtasks
    assert subtask.card_id == STORY_B
    assert subtask.branch == INTEGRATION_BRANCH
    assert subtask.base_branch == BASE
    assert subtask.worktree_path == repo.worktree
    assert subtask.status == "done"
    assert [phase.name for phase in subtask.phases] == ["resolve", "verify"]
    rebuilt = store.rebuild_from_journal(RUN_ID)
    assert [s.card_id for s in rebuilt.stories] == ["integrate"]
    assert [(s.card_id, s.status) for s in rebuilt.stories[0].subtasks] == [(STORY_B, "done")]
    assert [p.name for p in rebuilt.stories[0].subtasks[0].phases] == ["resolve", "verify"]
    _assert_protected(repo, before, tips)


def test_a_conflict_resolves_the_same_way_on_the_pygents_engine(
    repo: Repo, store: Store
) -> None:
    """Review Focus 5: the same scenario as the test above, walked by the
    pygents engine over `INTEGRATE` through the real `integrate_milestone`.
    Everything the yaml walk is asserted to leave behind is asserted here."""
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory, engine="pygents")

    assert isinstance(outcome, IntegrateSuccess), outcome
    assert outcome.merged == [STORY_A, STORY_B]
    assert outcome.resolved == [STORY_B]
    assert factory.calls == [
        {"workflow": "integrate", "run_id": RUN_ID, "story_id": "integrate", "card_id": STORY_B}
    ]
    assert factory.resolver.calls == [["shared.txt"]]
    assert _merge_head(repo.worktree) is None
    assert _git(repo.worktree, "status", "--porcelain") == ""
    assert _git(repo.worktree, "show", "HEAD:shared.txt") == RESOLVED
    story = _integrate_story(store)
    assert story.status == "done"
    [subtask] = story.subtasks
    assert (subtask.card_id, subtask.status) == (STORY_B, "done")
    assert [phase.name for phase in subtask.phases] == ["resolve", "verify"]
    rebuilt = store.rebuild_from_journal(RUN_ID)
    assert [p.name for p in rebuilt.stories[0].subtasks[0].phases] == ["resolve", "verify"]
    _assert_protected(repo, before, tips)


class _RecordingStore:
    """Only what `_resolve_conflict` touches on a store: `record_subtask`."""

    def __init__(self) -> None:
        self.subtasks: list[tuple[str, models.SubtaskRun]] = []

    def record_subtask(self, story_id: str, subtask: models.SubtaskRun) -> None:
        self.subtasks.append((story_id, subtask))


def _stub_both_walks(monkeypatch):
    walks: dict[str, list[tuple[Any, Any, dict[str, Any]]]] = {"yaml": [], "pygents": []}

    def recorder(name: str):
        def run_subtask(workflow, store, **kwargs):
            walks[name].append((workflow, store, kwargs))
            return yaml_engine.SubtaskSummary(status="done")

        return run_subtask

    monkeypatch.setattr(yaml_engine, "run_subtask", recorder("yaml"))
    monkeypatch.setattr(runtime_engine, "run_subtask", recorder("pygents"))
    return walks


def _resolve(store, factory, tmp_path: Path, engine: str):
    return integration._resolve_conflict(
        story_id=STORY_B,
        tip="m5/task-b",
        files=["shared.txt"],
        branch=INTEGRATION_BRANCH,
        base_branch=BASE,
        worktree=tmp_path / "integrate-worktree",
        repo_dir=tmp_path,
        commands=[PASS_CMD],
        allow_no_verification=False,
        store=store,
        run_id=RUN_ID,
        runner_factory=factory,
        engine=engine,
    )


@pytest.mark.parametrize("engine", ["yaml", "pygents"])
def test_resolve_conflict_walks_the_engine_it_is_given_with_the_same_arguments(
    monkeypatch, tmp_path: Path, engine: str
) -> None:
    """Spec test 4: `INTEGRATE` on pygents, the loaded `integrate` document on
    yaml, identical keywords (no `card`, no `parent_story`, no `resume_from`),
    and the factory gets the YAML document on both."""
    walks = _stub_both_walks(monkeypatch)
    factory_calls: list[dict[str, Any]] = []
    runner = object()

    def factory(**kwargs: Any) -> Any:
        factory_calls.append(kwargs)
        return runner

    store = _RecordingStore()

    summary = _resolve(store, factory, tmp_path, engine)

    assert summary.status == "done"
    other = "yaml" if engine == "pygents" else "pygents"
    assert walks[other] == []
    ((workflow, passed_store, kwargs),) = walks[engine]
    assert passed_store is store
    if engine == "pygents":
        assert workflow is integrate_workflow.INTEGRATE
    else:
        assert isinstance(workflow, loader.Workflow)
        assert workflow.name == "integrate"
    expected_subtask = models.SubtaskRun(
        card_id=STORY_B,
        branch=INTEGRATION_BRANCH,
        base_branch=BASE,
        status="started",
        worktree_path=tmp_path / "integrate-worktree",
    )
    assert store.subtasks == [("integrate", expected_subtask)]
    assert kwargs == {
        "story_id": "integrate",
        "subtask": expected_subtask,
        "repo_dir": tmp_path,
        "commands": [PASS_CMD],
        "extra_context": {
            "merge_tip": "m5/task-b",
            "conflict_files": ["shared.txt"],
            **cli.gate_context([PASS_CMD], False),
        },
        "agent_runner": runner,
    }
    (factory_call,) = factory_calls
    assert isinstance(factory_call["workflow"], loader.Workflow)
    assert factory_call["workflow"].name == "integrate"
    assert {key: value for key, value in factory_call.items() if key != "workflow"} == {
        "store": store,
        "run_id": RUN_ID,
        "story_id": "integrate",
        "card_id": STORY_B,
    }


@pytest.mark.parametrize("engine", ["PYGENTS", "bogus", ""])
def test_resolve_conflict_refuses_an_unknown_engine_before_recording_anything(
    monkeypatch, tmp_path: Path, engine: str
) -> None:
    """Review Focus 3: nothing recorded, no runner built, no walk."""
    walks = _stub_both_walks(monkeypatch)
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> Any:
        factory_calls.append(kwargs)
        return object()

    store = _RecordingStore()

    with pytest.raises(ValueError, match="unknown engine"):
        _resolve(store, factory, tmp_path, engine)

    assert store.subtasks == []
    assert factory_calls == []
    assert walks == {"yaml": [], "pygents": []}


def test_a_refusing_resolver_escalates_and_leaves_merge_head_in_place(
    repo: Repo, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    monkeypatch.setenv(REFUSE_ENV, "1")
    factory = FakeFactory()

    outcome = _integrate(repo, store, stories, factory=factory)

    assert isinstance(outcome, IntegrateEscalation)
    assert outcome.phase == "integrate"
    assert outcome.story == STORY_B
    assert outcome.files == ["shared.txt"]
    assert "resolve" in outcome.detail
    assert "MERGE_HEAD exists" in outcome.detail
    assert [call["card_id"] for call in factory.calls] == [STORY_B]
    # One dispatch per conflicting tip; the runner's own retry is inside it.
    assert factory.resolver.calls == [["shared.txt"], ["shared.txt"]]
    # Left exactly as it is for a human: never aborted, reset or cleaned.
    assert _merge_head(repo.worktree) == _sha(repo.root, tips[1])
    assert repo.worktree.is_dir()
    assert str(repo.worktree) in _git(repo.root, "worktree", "list", "--porcelain")
    assert _sha(repo.root, f"refs/heads/{INTEGRATION_BRANCH}")
    story = _integrate_story(store)
    assert story.status == "escalated"
    assert [(s.card_id, s.status) for s in story.subtasks] == [(STORY_B, "escalated")]
    _assert_protected(repo, before, tips)


def test_two_conflicting_tips_dispatch_once_each_under_one_integrate_story(
    repo: Repo, store: Store
) -> None:
    story_a = _story(STORY_A, "Story A", SUB_A)
    story_b = _story(STORY_B, "Story B", SUB_B)
    story_c = _story(STORY_C, "Story C", SUB_C)
    tips = [
        _story_branch(repo, story_a, {"shared.txt": "story a\n"}),
        _story_branch(repo, story_b, {"shared.txt": "story b\n"}),
        _story_branch(repo, story_c, {"shared.txt": "story c\n"}),
    ]
    before = _protected(repo, tips)
    factory = FakeFactory()

    outcome = _integrate(repo, store, [story_a, story_b, story_c], factory=factory)

    assert isinstance(outcome, IntegrateSuccess), outcome
    assert outcome.merged == [STORY_A, STORY_B, STORY_C]
    assert outcome.resolved == [STORY_B, STORY_C]
    assert [call["card_id"] for call in factory.calls] == [STORY_B, STORY_C]
    assert factory.resolver.calls == [["shared.txt"], ["shared.txt"]]
    assert _merge_head(repo.worktree) is None
    for tip in tips:
        assert _is_ancestor(repo.worktree, tip)
    story = _integrate_story(store)
    assert [(s.card_id, s.status) for s in story.subtasks] == [
        (STORY_B, "done"),
        (STORY_C, "done"),
    ]
    _assert_protected(repo, before, tips)


def test_a_relaunch_after_a_human_finished_the_merge_succeeds_without_dispatch(
    repo: Repo, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    stories, tips = _conflicting_pair(repo)
    before = _protected(repo, tips)
    monkeypatch.setenv(REFUSE_ENV, "1")
    first_factory = FakeFactory()
    first = _integrate(repo, store, stories, factory=first_factory)
    assert isinstance(first, IntegrateEscalation)
    assert len(first_factory.calls) == 1

    # The human finishes the merge in the integration worktree.
    (repo.worktree / "shared.txt").write_text("human fix\n", encoding="utf-8")
    _git(repo.worktree, "add", "shared.txt")
    _git(repo.worktree, "commit", "--no-edit")
    monkeypatch.delenv(REFUSE_ENV)
    head = _sha(repo.worktree, "HEAD")
    second_factory = FakeFactory()

    second = _integrate(repo, store, stories, factory=second_factory)

    assert isinstance(second, IntegrateSuccess), second
    assert second.merged == [STORY_A, STORY_B]
    assert second.resolved == []
    assert second_factory.calls == []
    assert _sha(repo.worktree, "HEAD") == head
    assert _git(repo.worktree, "show", "HEAD:shared.txt") == "human fix\n"
    _assert_protected(repo, before, tips)
