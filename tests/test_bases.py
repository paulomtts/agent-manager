"""Behaviour of `bases.build` (supervisor-tree plan Task 2.1, card 06bf46bb).

Placement follows design §14: `bases.py` wraps the worktree, merge and verify
steps, so it is a Steps component and is exercised against real temporary git
repositories created in `tmp_path` -- no network and no mocks of git. The repo
helpers are ported from `tests/steps/test_integrate.py` (there is no
`tests/conftest.py`). Every scenario asserts the milestone's base branch,
`master`, never moves. No test sleeps.
"""

import dataclasses
import inspect
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager import bases
from agent_manager.dag import RootPlan

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
async def test_a_conflict_is_not_resolved_yet(conflicting_repo: Path, MASTER_BEFORE: str):
    repo = conflicting_repo

    with pytest.raises(bases.BaseFailed, match="conflict.*resolver not wired") as excinfo:
        await _build(repo, ["m7/a", "m7/b"])

    assert excinfo.value.stopped is False
    assert "m7/b" in excinfo.value.detail
    assert "shared.txt" in excinfo.value.detail
    # Left in progress for Task 2.2's resolver or a human: never aborted.
    wt = base_worktree(repo)
    assert _merge_head(wt) == rev(repo, "m7/b")
    assert "<<<<<<< " in (wt / "shared.txt").read_text()
    assert rev(repo, BASE) == rev(repo, "m7/a")
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
