"""Behaviour of `bases.build` (supervisor-tree plan Tasks 2.1 and 2.2, cards 06bf46bb and 8fe30578).

Placement follows design §14: `bases.py` wraps the worktree, merge and verify
steps, so it is a Steps component and is exercised against real temporary git
repositories created in `tmp_path` -- no network and no mocks of git. The repo
helpers are ported from `tests/steps/test_integrate.py` (there is no
`tests/conftest.py`). Every scenario asserts the milestone's base branch,
`master`, never moves. No test sleeps.

The resolver path uses a real `Store` and `dispatch.AgentRunner` with an
injected launcher double, `FakeResolver`, ported from
`tests/test_integration.py`. It plays fake `claude`'s resolver mode (pinned by
`tests/e2e/test_fake_claude.py`'s `test_the_resolver_*`) and learns what to do
only from its brief.
"""

import asyncio
import dataclasses
import inspect
import json
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_manager import bases, dispatch, models
from agent_manager.dag import RootPlan
from agent_manager.harness.base import Outcome
from agent_manager.runtime.stop import StopSignal
from agent_manager.store import Store

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the bases steps-tier tests",
)

BASE = "m7/base-cccccccc"
ROOT = RootPlan("merged", BASE, ("A", "B"))


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def rev(repo: Path, ref: str) -> str:
    """The sha `ref` resolves to in `repo`."""
    return _git(repo, "rev-parse", ref).strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    """Whether git says `ancestor` is contained in `descendant`."""
    completed = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _commit(cwd: Path, name: str, body: str) -> str:
    (Path(cwd) / name).write_text(body)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", f"add {name}")
    return rev(cwd, "HEAD")


def _init_repo(root: Path) -> Path:
    """A real git repo at `root` on `master` holding README.md and shared.txt."""
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "master", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, "README.md", "base\n")
    _commit(root, "shared.txt", "shared line\n")
    return root.resolve()


def _make_tip(
    repo: Path,
    tmp_path: Path,
    branch: str,
    files: dict[str, str],
    start: str = "master",
) -> str:
    """Cut `branch` from `start` in a throwaway worktree, commit `files`, return its sha.

    The throwaway worktree is removed again, so the tip exists only as a
    branch -- what a finished story leaves behind -- and master's checkout is
    never touched.
    """
    scratch = tmp_path / f"scratch-{branch.replace('/', '-')}"
    _git(repo, "worktree", "add", str(scratch), "-b", branch, start)
    for name, body in files.items():
        (scratch / name).write_text(body)
        _git(scratch, "add", name)
    _git(scratch, "commit", "-m", f"work on {branch}")
    sha = rev(scratch, "HEAD")
    _git(repo, "worktree", "remove", str(scratch))
    return sha


def base_worktree(repo: Path) -> Path:
    """Where `cli.worktree_for` puts the base branch's worktree."""
    return repo / ".claude" / "worktrees" / "m7" / "base-cccccccc"


def _merge_head(wt: Path) -> str | None:
    """MERGE_HEAD's sha when a merge is in progress in `wt`, else None."""
    completed = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


async def _build(
    repo: Path,
    tips: list[str],
    *,
    root: RootPlan = ROOT,
    commands: tuple[str, ...] | list[str] = ("true",),
    allow_no_verification: bool = False,
) -> bases.BaseResult:
    """`bases.build` with the Task 2.2-only parameters left empty."""
    return await bases.build(
        root,
        list(tips),
        repo_dir=repo,
        commands=list(commands),
        allow_no_verification=allow_no_verification,
        store=None,
        run_id=None,
        story_id=None,
        runner_factory=None,
        stop=None,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `master`, isolated in tmp_path, with no origin."""
    return _init_repo(tmp_path / "repo")


@pytest.fixture
def MASTER_BEFORE(repo: Path) -> str:
    """Master's sha, recorded at setup, before any build runs."""
    return rev(repo, "master")


@pytest.fixture
def two_story_repo(repo: Path, tmp_path: Path) -> Path:
    """`repo` plus two independent story tips: m7/a adds a.txt, m7/b adds b.txt."""
    _make_tip(repo, tmp_path, "m7/a", {"a.txt": "from story a\n"})
    _make_tip(repo, tmp_path, "m7/b", {"b.txt": "from story b\n"})
    return repo


def test_the_result_and_failure_shapes_are_the_plans():
    assert [field.name for field in dataclasses.fields(bases.BaseResult)] == [
        "branch",
        "merged",
        "already_merged",
        "resolved",
    ]
    result = bases.BaseResult(branch=BASE, merged=[], already_merged=[], resolved=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.branch = "other"  # type: ignore[misc]
    failed = bases.BaseFailed("why")
    assert isinstance(failed, Exception)
    assert (failed.detail, failed.stopped, str(failed)) == ("why", False, "why")
    assert bases.BaseFailed("why", stopped=True).stopped is True


def test_build_is_a_plain_coroutine_that_does_not_import_grafo():
    assert inspect.iscoroutinefunction(bases.build)
    source = Path(bases.__file__).read_text()
    assert not any(
        line.startswith(("import grafo", "from grafo")) for line in source.splitlines()
    )
    params = inspect.signature(bases.build).parameters
    assert list(params) == [
        "root",
        "tips",
        "repo_dir",
        "commands",
        "allow_no_verification",
        "store",
        "run_id",
        "story_id",
        "runner_factory",
        "stop",
    ]
    assert all(
        params[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in list(params)[2:]
    )


@requires_git
async def test_two_clean_tips_merge_into_the_base(two_story_repo: Path, MASTER_BEFORE: str):
    repo = two_story_repo

    result = await _build(repo, ["m7/a", "m7/b"], commands=["true"])

    assert result == bases.BaseResult(
        branch=BASE, merged=["m7/b"], already_merged=[], resolved=[]
    )
    assert result.branch == "m7/base-cccccccc"
    assert is_ancestor(repo, "m7/a", BASE) and is_ancestor(repo, "m7/b", BASE)
    wt = base_worktree(repo)
    assert wt.is_dir()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == BASE
    assert _merge_head(wt) is None
    assert _git(repo, "symbolic-ref", "HEAD").strip() == "refs/heads/master"
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_building_twice_merges_nothing_the_second_time(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo
    await _build(repo, ["m7/a", "m7/b"])
    first = rev(repo, BASE)

    second = await _build(repo, ["m7/a", "m7/b"])

    assert second == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/b"], resolved=[]
    )
    assert rev(repo, BASE) == first
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_tip_already_inside_the_other_is_already_merged(
    repo: Path, tmp_path: Path, MASTER_BEFORE: str
):
    # B is stacked on A, so B's tip already contains A's.
    _make_tip(repo, tmp_path, "m7/a", {"a.txt": "from story a\n"})
    _make_tip(repo, tmp_path, "m7/b", {"b.txt": "from story b\n"}, start="m7/a")

    result = await _build(repo, ["m7/b", "m7/a"])

    assert result == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/a"], resolved=[]
    )
    assert rev(repo, BASE) == rev(repo, "m7/b")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_removed_base_worktree_is_re_added_and_nothing_is_re_merged(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo
    await _build(repo, ["m7/a", "m7/b"])
    built = rev(repo, BASE)
    _git(repo, "worktree", "remove", str(base_worktree(repo)))

    result = await _build(repo, ["m7/a", "m7/b"])

    assert result.merged == []
    assert result.already_merged == ["m7/b"]
    assert base_worktree(repo).is_dir()
    assert rev(repo, BASE) == built
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_tip_given_as_a_sha_is_merged_and_reported_as_given(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo
    sha_b = rev(repo, "m7/b")

    result = await _build(repo, ["m7/a", sha_b])

    assert result.merged == [sha_b]
    assert result.already_merged == []
    assert is_ancestor(repo, sha_b, BASE)
    assert rev(repo, "master") == MASTER_BEFORE


@pytest.fixture
def conflicting_repo(repo: Path, tmp_path: Path) -> Path:
    """`repo` plus m7/a and m7/b, both rewriting shared.txt's one line."""
    _make_tip(repo, tmp_path, "m7/a", {"shared.txt": "from story a\n"})
    _make_tip(repo, tmp_path, "m7/b", {"shared.txt": "from story b\n"})
    return repo


# ── the resolver path (card 8fe30578) ────────────────────────────────────────

RUN_ID = "run-2026-09-26-merged-bases"
STORY_C = "cccccccc-0000-4000-8000-00000000000c"
STORY_D = "dddddddd-0000-4000-8000-00000000000d"
CARD_C = f"base-{STORY_C}"
CARD_D = f"base-{STORY_D}"
BASE_D = "m7/base-dddddddd"
ROOT_D = RootPlan("merged", BASE_D, ("A", "B"))
_MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")


@pytest.fixture
def store(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    """A real store with the run recorded, as the milestone runner records it."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = Store.open(repo, RUN_ID)
    opened.record_run(
        models.Run(
            id=RUN_ID,
            workflow="milestone",
            repo_dir=repo,
            base_branch="master",
            branch_prefix="m7",
            status="started",
        )
    )
    yield opened
    opened.close()


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


def _keep_both_sides(text: str) -> str:
    """Fake claude's resolve mode: drop the conflict markers, keep every side's lines."""
    return "".join(
        line for line in text.splitlines(keepends=True) if not line.startswith(_MARKERS)
    )


@dataclass
class FakeResolver:
    """A `LauncherFn` double playing fake `claude`'s resolver mode in its cwd.

    It learns the conflicting files and its result path only from the brief
    (rule 5). Resolve keeps both sides, stages and commits the merge. Refuse
    claims `resolved: true` but touches nothing, so `MERGE_HEAD` stays and
    `merge_completed_gate` blocks. `during` runs in the launcher's thread after
    the work is done, before the call returns: the stop test triggers there.
    """

    refuse: bool = False
    during: Callable[[], None] | None = None
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        brief = Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
        files = _conflict_files_from(brief)
        self.calls.append(files)
        worktree = Path(cwd)
        if self.refuse:
            result: dict[str, Any] = {"resolved": True, "summary": "said it was resolved"}
        else:
            for name in files:
                path = worktree / name
                path.write_text(_keep_both_sides(path.read_text(encoding="utf-8")), encoding="utf-8")
            _git(worktree, "add", *files)
            _git(worktree, "commit", "--no-edit")
            result = {"resolved": True, "summary": f"kept both sides of {', '.join(files)}"}
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        _result_path_from(brief).write_text(json.dumps(result), encoding="utf-8")
        if self.during is not None:
            self.during()
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.1,
            stdout_path=stdout_path,
        )


@dataclass
class FakeFactory:
    """A `cli.RunnerFactory` that records every call and wires in `FakeResolver`."""

    resolver: FakeResolver = field(default_factory=FakeResolver)
    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, *, store, run_id, story_id, card_id):
        self.calls.append({"run_id": run_id, "story_id": story_id, "card_id": card_id})
        adapter = _FakeAdapter()
        return dispatch.AgentRunner(
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


async def _resolve_build(
    repo: Path,
    tips: list[str],
    *,
    store: Store | None,
    factory: FakeFactory | None,
    root: RootPlan = ROOT,
    story_id: str | None = STORY_C,
    run_id: str | None = RUN_ID,
    commands: tuple[str, ...] | list[str] = ("true",),
    stop: StopSignal | None = None,
) -> bases.BaseResult:
    """`bases.build` with the resolver parameters filled in."""
    return await bases.build(
        root,
        list(tips),
        repo_dir=repo,
        commands=list(commands),
        allow_no_verification=False,
        store=store,
        run_id=run_id,
        story_id=story_id,
        runner_factory=factory,
        stop=stop,
    )


def _bases_story(store: Store) -> models.StoryRun:
    run = store.load_run(RUN_ID)
    assert [story.card_id for story in run.stories] == ["bases"]
    return run.stories[0]


@requires_git
@pytest.mark.parametrize(
    "tips",
    [["m7/a", "m7/gone"], ["m7/gone", "m7/b"]],
    ids=["missing-later-tip", "missing-first-tip"],
)
async def test_a_missing_tip_fails_naming_the_ref(
    two_story_repo: Path, tmp_path: Path, MASTER_BEFORE: str, tips: list[str]
):
    repo = two_story_repo
    # A tip deleted after an earlier run finished its story.
    _make_tip(repo, tmp_path, "m7/gone", {"gone.txt": "deleted later\n"})
    _git(repo, "branch", "-D", "m7/gone")

    with pytest.raises(bases.BaseFailed, match="m7/gone") as excinfo:
        await _build(repo, tips)

    assert excinfo.value.stopped is False
    assert "m7/gone" in excinfo.value.detail
    assert _git(repo, "branch", "--list", BASE).strip() == ""
    assert not base_worktree(repo).exists()
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_no_tips_is_refused_before_any_git(two_story_repo: Path, MASTER_BEFORE: str):
    repo = two_story_repo

    with pytest.raises(ValueError, match=BASE):
        await _build(repo, [])

    assert _git(repo, "branch", "--list", BASE).strip() == ""
    assert not base_worktree(repo).exists()
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_conflict_is_resolved_by_the_integrate_resolver(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    factory = FakeFactory()

    result = await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=factory)

    assert result == bases.BaseResult(
        branch=BASE, merged=["m7/b"], already_merged=[], resolved=["m7/b"]
    )
    assert is_ancestor(repo, "m7/a", BASE) and is_ancestor(repo, "m7/b", BASE)
    wt = base_worktree(repo)
    assert _merge_head(wt) is None
    assert _git(wt, "show", f"{BASE}:shared.txt") == "from story a\nfrom story b\n"
    assert factory.resolver.calls == [["shared.txt"]]
    assert factory.calls == [{"run_id": RUN_ID, "story_id": "bases", "card_id": CARD_C}]
    assert _git(repo, "symbolic-ref", "HEAD").strip() == "refs/heads/master"
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_the_resolver_walk_is_journalled_under_the_bases_story(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    assert (bases.BASES_STORY_ID, bases.BASES_STORY_TITLE) == ("bases", "Merged bases")

    await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=FakeFactory())

    story = _bases_story(store)
    assert (story.title, story.level, story.status) == ("Merged bases", 0, "started")
    [subtask] = story.subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "done")
    assert subtask.branch == BASE
    assert subtask.base_branch == "m7/a"
    assert subtask.worktree_path == base_worktree(repo)
    assert [phase.name for phase in subtask.phases] == ["resolve", "verify"]
    events = [(line.event, line.story, line.card) for line in store.journal.read()]
    story_at = events.index(("story_upsert", "bases", None))
    subtask_at = events.index(("subtask_upsert", "bases", CARD_C))
    first_phase_at = next(
        index
        for index, (event, _story, card) in enumerate(events)
        if event == "phase_upsert" and card == CARD_C
    )
    assert story_at < subtask_at < first_phase_at
    assert store.latest_checkpoint(CARD_C).reason == "done"
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_clean_build_records_no_bases_story(
    two_story_repo: Path, store: Store, MASTER_BEFORE: str
):
    factory = FakeFactory()

    result = await _resolve_build(two_story_repo, ["m7/a", "m7/b"], store=store, factory=factory)

    assert result.resolved == []
    assert factory.calls == []
    assert store.load_run(RUN_ID).stories == []
    assert rev(two_story_repo, "master") == MASTER_BEFORE


@requires_git
async def test_two_bases_in_one_run_share_one_bases_story(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    factory = FakeFactory()

    first = await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=factory)
    second = await _resolve_build(
        repo, ["m7/a", "m7/b"], store=store, factory=factory, root=ROOT_D, story_id=STORY_D
    )

    assert first.resolved == ["m7/b"] and second.resolved == ["m7/b"]
    assert second.branch == BASE_D
    story = _bases_story(store)
    assert [(s.card_id, s.status) for s in story.subtasks] == [
        (CARD_C, "done"),
        (CARD_D, "done"),
    ]
    assert [call["card_id"] for call in factory.calls] == [CARD_C, CARD_D]
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_conflict_without_a_resolver_fails_for_a_human(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo

    with pytest.raises(bases.BaseFailed, match="no resolver is available") as excinfo:
        await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=None)

    assert excinfo.value.stopped is False
    assert "m7/b" in excinfo.value.detail
    assert "shared.txt" in excinfo.value.detail
    wt = base_worktree(repo)
    assert str(wt) in excinfo.value.detail
    # Left in progress for a human: never aborted, and nothing recorded.
    assert _merge_head(wt) == rev(repo, "m7/b")
    assert "<<<<<<< " in (wt / "shared.txt").read_text()
    assert store.load_run(RUN_ID).stories == []
    assert rev(repo, BASE) == rev(repo, "m7/a")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_conflict_with_no_store_fails_for_a_human(
    conflicting_repo: Path, MASTER_BEFORE: str
):
    repo = conflicting_repo

    with pytest.raises(bases.BaseFailed, match="no resolver is available") as excinfo:
        await _build(repo, ["m7/a", "m7/b"])

    assert excinfo.value.stopped is False
    assert _merge_head(base_worktree(repo)) == rev(repo, "m7/b")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_two_conflicts_in_one_base_are_each_resolved(
    conflicting_repo: Path, tmp_path: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 1: the same card, `base-<C>`, walks INTEGRATE twice.
    repo = conflicting_repo
    _make_tip(repo, tmp_path, "m7/c", {"shared.txt": "from story c\n"})
    factory = FakeFactory()

    result = await _resolve_build(
        repo, ["m7/a", "m7/b", "m7/c"], store=store, factory=factory
    )

    assert result.merged == ["m7/b", "m7/c"]
    assert result.resolved == ["m7/b", "m7/c"]
    assert factory.resolver.calls == [["shared.txt"], ["shared.txt"]]
    assert [call["card_id"] for call in factory.calls] == [CARD_C, CARD_C]
    for tip in ("m7/a", "m7/b", "m7/c"):
        assert is_ancestor(repo, tip, BASE)
    assert _merge_head(base_worktree(repo)) is None
    [subtask] = _bases_story(store).subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "done")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_relaunch_after_a_resolved_conflict_dispatches_nothing(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 2.
    repo = conflicting_repo
    await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=FakeFactory())
    built = rev(repo, BASE)
    again = FakeFactory()

    result = await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=again)

    assert result == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/b"], resolved=[]
    )
    assert again.calls == [] and again.resolver.calls == []
    assert rev(repo, BASE) == built
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_missing_run_id_falls_back_to_the_stores(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 3: `run_id` is typed `str | None`; the runner needs a real id.
    repo = conflicting_repo
    factory = FakeFactory()

    result = await _resolve_build(
        repo, ["m7/a", "m7/b"], store=store, factory=factory, run_id=None
    )

    assert result.resolved == ["m7/b"]
    assert factory.calls == [{"run_id": RUN_ID, "story_id": "bases", "card_id": CARD_C}]
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_resolver_that_gives_up_fails_the_base(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    factory = FakeFactory(resolver=FakeResolver(refuse=True))

    with pytest.raises(bases.BaseFailed) as excinfo:
        await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=factory)

    failed = excinfo.value
    assert failed.stopped is False
    assert "m7/b" in failed.detail
    assert BASE in failed.detail
    assert "'resolve'" in failed.detail
    assert "escalated" in failed.detail
    assert "MERGE_HEAD exists" in failed.detail
    wt = base_worktree(repo)
    assert str(wt) in failed.detail
    assert "relaunch" in failed.detail
    # One dispatch; the runner's own gate retry is inside it.
    assert [call["card_id"] for call in factory.calls] == [CARD_C]
    assert factory.resolver.calls == [["shared.txt"], ["shared.txt"]]
    [subtask] = _bases_story(store).subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "escalated")
    assert store.latest_checkpoint(CARD_C).reason == "escalated"
    # Left exactly as it is for a human.
    assert _merge_head(wt) == rev(repo, "m7/b")
    assert rev(repo, BASE) == rev(repo, "m7/a")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_stop_during_the_resolver_parks_it(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    fired = threading.Event()

    def fire() -> None:
        # On the loop, where the signal lives.
        stop.trigger(STORY_C)
        fired.set()

    def during() -> None:
        # In the launcher's thread, while `resolve` is in flight.
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")

    factory = FakeFactory(resolver=FakeResolver(during=during))

    with pytest.raises(bases.BaseFailed) as excinfo:
        await _resolve_build(
            repo, ["m7/a", "m7/b"], store=store, factory=factory, stop=stop
        )

    failed = excinfo.value
    assert failed.stopped is True
    assert "m7/b" in failed.detail
    assert BASE in failed.detail
    assert "stopped before verify" in failed.detail
    assert stop.primary == STORY_C
    assert factory.resolver.calls == [["shared.txt"]]
    assert store.latest_checkpoint(CARD_C).reason == "parked"
    [subtask] = _bases_story(store).subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "stopped")
    # The turn in flight finished; the next phase never started.
    assert [phase.name for phase in subtask.phases] == ["resolve"]
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_relaunch_after_an_escalation_does_not_re_dispatch(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 4: nobody finished the merge, so the relaunch finds it in progress.
    repo = conflicting_repo
    with pytest.raises(bases.BaseFailed):
        await _resolve_build(
            repo,
            ["m7/a", "m7/b"],
            store=store,
            factory=FakeFactory(resolver=FakeResolver(refuse=True)),
        )
    again = FakeFactory()

    with pytest.raises(bases.BaseFailed, match="never resolved") as excinfo:
        await _resolve_build(repo, ["m7/a", "m7/b"], store=store, factory=again)

    assert excinfo.value.stopped is False
    assert again.calls == [] and again.resolver.calls == []
    assert _merge_head(base_worktree(repo)) == rev(repo, "m7/b")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_red_suite_after_a_resolved_conflict_fails_the_base(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    # Review Focus 5: the resolver's own `verify` phase runs the red suite first.
    repo = conflicting_repo
    factory = FakeFactory()

    with pytest.raises(bases.BaseFailed) as excinfo:
        await _resolve_build(
            repo, ["m7/a", "m7/b"], store=store, factory=factory, commands=["false"]
        )

    failed = excinfo.value
    assert failed.stopped is False
    assert "'verify'" in failed.detail
    assert factory.resolver.calls == [["shared.txt"]]
    # The resolved merge commit stands; only the verdict failed.
    assert is_ancestor(repo, "m7/b", BASE)
    assert _merge_head(base_worktree(repo)) is None
    [subtask] = _bases_story(store).subtasks
    assert subtask.status == "escalated"
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_merge_in_progress_fails_for_a_human(
    two_story_repo: Path, tmp_path: Path, MASTER_BEFORE: str
):
    repo = two_story_repo
    _make_tip(repo, tmp_path, "m7/c", {"c.txt": "from story c\n"})
    await _build(repo, ["m7/a", "m7/b"])
    wt = base_worktree(repo)
    head = rev(repo, BASE)
    # A merge someone started in the base worktree and never finished.
    _git(wt, "merge", "--no-ff", "--no-commit", "m7/c")
    assert _merge_head(wt) == rev(repo, "m7/c")

    with pytest.raises(bases.BaseFailed, match="never resolved") as excinfo:
        await _build(repo, ["m7/a", "m7/b"])

    assert excinfo.value.stopped is False
    assert BASE in excinfo.value.detail
    assert str(wt) in excinfo.value.detail
    assert _merge_head(wt) == rev(repo, "m7/c")
    assert rev(repo, BASE) == head
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_failing_verify_fails_the_base(two_story_repo: Path, MASTER_BEFORE: str):
    repo = two_story_repo

    with pytest.raises(bases.BaseFailed, match="failed its verification") as excinfo:
        await _build(repo, ["m7/a", "m7/b"], commands=["false"])

    assert excinfo.value.stopped is False
    assert BASE in excinfo.value.detail
    assert str(base_worktree(repo)) in excinfo.value.detail
    # The merges stand; only the verdict failed.
    assert is_ancestor(repo, "m7/b", BASE)
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_an_empty_suite_is_judged_before_running(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo

    with pytest.raises(bases.BaseFailed, match="no full-suite command") as excinfo:
        await _build(repo, ["m7/a", "m7/b"], commands=[], allow_no_verification=False)
    assert excinfo.value.stopped is False

    result = await _build(repo, ["m7/a", "m7/b"], commands=[], allow_no_verification=True)

    assert result == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/b"], resolved=[]
    )
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_verification_runs_in_the_base_worktree(
    two_story_repo: Path, MASTER_BEFORE: str
):
    repo = two_story_repo

    await _build(
        repo,
        ["m7/a", "m7/b"],
        commands=["test -f a.txt", "test -f b.txt", "touch verified-here.txt"],
    )

    assert (base_worktree(repo) / "verified-here.txt").is_file()
    assert not (repo / "verified-here.txt").exists()
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_every_git_and_verify_call_runs_off_the_event_loop_thread(
    two_story_repo: Path, MASTER_BEFORE: str, monkeypatch: pytest.MonkeyPatch
):
    # Spies, not mocks: each wrapper records the thread it was called on and
    # then delegates to the real step, so real git and a real suite still run.
    repo = two_story_repo
    loop_thread = threading.get_ident()
    calls: dict[str, list[int]] = {}

    def spy(name: str, real):
        def wrapper(*args, **kwargs):
            calls.setdefault(name, []).append(threading.get_ident())
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(bases, "_ref_exists", spy("_ref_exists", bases._ref_exists))
    monkeypatch.setattr(bases, "ensure", spy("ensure", bases.ensure))
    monkeypatch.setattr(bases, "merge_tip", spy("merge_tip", bases.merge_tip))
    monkeypatch.setattr(
        bases.verify, "run_suite", spy("run_suite", bases.verify.run_suite)
    )

    result = await _build(repo, ["m7/a", "m7/b"], commands=["true"])

    assert result.merged == ["m7/b"]
    assert sorted(calls) == ["_ref_exists", "ensure", "merge_tip", "run_suite"]
    assert all(
        thread != loop_thread for threads in calls.values() for thread in threads
    ), calls
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_git_and_verify_around_a_resolved_conflict_run_off_the_loop_thread(
    conflicting_repo: Path,
    store: Store,
    MASTER_BEFORE: str,
    monkeypatch: pytest.MonkeyPatch,
):
    # The conflict-path twin of the test above: same spies, real git, a real
    # suite, and the resolver in between.
    repo = conflicting_repo
    loop_thread = threading.get_ident()
    calls: dict[str, list[int]] = {}

    def spy(name: str, real):
        def wrapper(*args, **kwargs):
            calls.setdefault(name, []).append(threading.get_ident())
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(bases, "_ref_exists", spy("_ref_exists", bases._ref_exists))
    monkeypatch.setattr(bases, "ensure", spy("ensure", bases.ensure))
    monkeypatch.setattr(bases, "merge_tip", spy("merge_tip", bases.merge_tip))
    monkeypatch.setattr(
        bases.verify, "run_suite", spy("run_suite", bases.verify.run_suite)
    )
    factory = FakeFactory()

    result = await _resolve_build(
        repo, ["m7/a", "m7/b"], store=store, factory=factory, commands=["true"]
    )

    assert result.resolved == ["m7/b"]
    assert factory.resolver.calls == [["shared.txt"]]
    assert sorted(calls) == ["_ref_exists", "ensure", "merge_tip", "run_suite"]
    assert all(
        thread != loop_thread for threads in calls.values() for thread in threads
    ), calls
    assert rev(repo, "master") == MASTER_BEFORE
