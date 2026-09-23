"""Behaviour of the subtask worktree step (design §4 `steps/`, spec card 0816e239).

Placement follows design §14: `worktree.py` is a Steps component, so its
behaviour is exercised against real temporary git repositories created with
`git init` / `git worktree` in `tmp_path` -- no network, and no faking of git
except where a test must force an output git itself would never print.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager.steps import worktree
from agent_manager.steps.worktree import GitError

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the worktree step's steps-tier tests",
)


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _head(cwd: Path) -> str:
    return _git(cwd, "rev-parse", "HEAD").strip()


def _commit(cwd: Path, name: str, body: str) -> str:
    (Path(cwd) / name).write_text(body)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", f"add {name}")
    return _head(cwd)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `main` with one commit, isolated in tmp_path."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, "README.md", "base\n")
    return root


def _recorder(calls: list[list[str]], inner=None):
    """A git runner that records every argv, optionally delegating to `inner`."""

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        return "" if inner is None else inner(argv)

    return runner


def test_worktree_paths_takes_only_the_worktree_lines():
    porcelain = (
        "worktree /abs/repo\n"
        "HEAD 1111111111111111111111111111111111111111\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /abs/wt\n"
        "HEAD 2222222222222222222222222222222222222222\n"
        "branch refs/heads/m1/task-9\n"
    )
    assert worktree.worktree_paths(porcelain) == ["/abs/repo", "/abs/wt"]


def test_worktree_paths_of_empty_output_is_empty():
    assert worktree.worktree_paths("") == []


def test_branch_exists_matches_a_whole_line_never_a_prefix():
    refs = "main\nm1/task-9\n"
    assert worktree.branch_exists(refs, "m1/task-9") is True
    # The prefix must NOT match: this is the bug the JS test guarded.
    assert worktree.branch_exists(refs, "m1/task-") is False


def test_branch_exists_ignores_blank_and_padded_lines():
    # A blank line must not become an empty ref that matches an empty branch,
    # and padding must not stop a real ref from matching.
    assert worktree.branch_exists("main\n\n  m1/task-9  \n", "m1/task-9") is True
    assert worktree.branch_exists("main\n\n\n", "") is False


@requires_git
def test_run_git_returns_stdout(repo: Path):
    out = worktree.run_git(["-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
    assert out.strip() == "main"


@requires_git
def test_run_git_raises_git_error_carrying_argv_and_exit_code(tmp_path: Path):
    argv = ["-C", str(tmp_path), "rev-parse", "--verify", "origin/nope"]
    with pytest.raises(GitError) as excinfo:
        worktree.run_git(argv)
    assert excinfo.value.argv == argv
    assert excinfo.value.exit_code not in (None, 0)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"branch": ""}, "branch"),
        ({"branch": "   "}, "branch"),
        ({"base": ""}, "base"),
        ({"base": "   "}, "base"),
        ({"worktree": "relative/wt"}, "worktree"),
        ({"worktree": ""}, "worktree"),
        ({"repo_dir": "relative/repo"}, "repo_dir"),
        ({"repo_dir": ""}, "repo_dir"),
    ],
    ids=[
        "empty-branch",
        "blank-branch",
        "empty-base",
        "blank-base",
        "relative-worktree",
        "empty-worktree",
        "relative-repo-dir",
        "empty-repo-dir",
    ],
)
def test_bad_arguments_raise_before_any_git_invocation(kwargs, expected):
    calls: list[list[str]] = []
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": "/abs/wt",
        "repo_dir": "/abs/repo",
        **kwargs,
    }
    with pytest.raises(ValueError, match=expected):
        worktree.ensure(**args, git_runner=_recorder(calls))
    assert calls == []


@requires_git
def test_a_fresh_branch_and_missing_worktree_is_created(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert wt.is_dir()
    assert result == {
        "branch": "m1/task-9",
        "worktree": str(wt),
        "branch_existed": False,
        "worktree_existed": False,
        "created": True,
        "commit_count": 0,
    }
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "m1/task-9"


@requires_git
def test_a_directory_that_is_not_a_registered_worktree_propagates_gits_failure(
    repo: Path, tmp_path: Path
):
    # An existing directory that git does not know about is NOT "already
    # prepared": `worktree add` refuses it, and that refusal must surface
    # rather than be reported as a quiet no-op.
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "stray.txt").write_text("not a worktree\n")

    with pytest.raises(GitError) as excinfo:
        worktree.ensure(
            branch="m1/task-9",
            base="main",
            worktree=str(wt),
            repo_dir=str(repo),
        )

    assert "worktree" in excinfo.value.argv
    assert "add" in excinfo.value.argv


@requires_git
def test_an_exact_branch_match_is_required_before_checking_out(
    repo: Path, tmp_path: Path
):
    # `m1/task-9` exists; `m1/task-` is a different branch and must be cut new.
    _git(repo, "branch", "m1/task-9")
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert result["branch_existed"] is False
    assert result["created"] is True
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "m1/task-"


@requires_git
def test_a_second_identical_call_is_a_no_op(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)
    head_before = _head(wt)

    result = worktree.ensure(**args)

    assert result["branch_existed"] is True
    assert result["worktree_existed"] is True
    assert result["created"] is False
    assert _head(wt) == head_before


@requires_git
def test_an_existing_worktree_add_is_never_attempted_a_second_time(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)

    calls: list[list[str]] = []
    worktree.ensure(**args, git_runner=_recorder(calls, worktree.run_git))

    assert not any("add" in argv for argv in calls)


@requires_git
def test_uncommitted_local_changes_survive_untouched(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)
    (wt / "README.md").write_text("edited by a killed run\n")
    (wt / "scratch.txt").write_text("untracked work in progress\n")
    status_before = _git(wt, "status", "--porcelain")

    worktree.ensure(**args)

    assert (wt / "README.md").read_text() == "edited by a killed run\n"
    assert (wt / "scratch.txt").read_text() == "untracked work in progress\n"
    assert _git(wt, "status", "--porcelain") == status_before


@requires_git
def test_a_non_normalized_worktree_path_still_counts_as_existing(
    repo: Path, tmp_path: Path
):
    # The caller's string and git's recorded path routinely differ by a
    # trailing slash or a `.` component. Treating those as a missing worktree
    # would send `worktree add` at a live directory and kill the run.
    wt = tmp_path / "wt"
    worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=f"{wt}{os.sep}.{os.sep}",
        repo_dir=str(repo),
    )

    assert result["worktree_existed"] is True
    assert result["created"] is False


@requires_git
def test_a_sibling_worktree_at_another_path_does_not_count_as_this_one(
    repo: Path, tmp_path: Path
):
    sibling = tmp_path / "sibling-wt"
    _git(repo, "worktree", "add", str(sibling), "-b", "m1/task-8")
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert result["worktree_existed"] is False
    assert result["created"] is True
    assert sibling.is_dir()


@requires_git
def test_an_existing_branchs_commits_survive_the_checkout_path(
    repo: Path, tmp_path: Path
):
    # A killed run left a commit on the subtask branch and no worktree. Cutting
    # the branch again from base would silently discard that commit, so this
    # call must check the branch out instead.
    staging = tmp_path / "staging-wt"
    _git(repo, "worktree", "add", str(staging), "-b", "m1/task-9")
    _commit(staging, "prior.txt", "work from a killed run\n")
    prior_head = _head(staging)
    _git(repo, "worktree", "remove", str(staging))
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert result["branch_existed"] is True
    assert result["created"] is True
    assert result["commit_count"] == 1
    assert _head(wt) == prior_head
    assert (wt / "prior.txt").read_text() == "work from a killed run\n"


@requires_git
def test_commit_count_counts_only_the_commits_on_top_of_the_base(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)
    _commit(wt, "one.txt", "1\n")
    _commit(wt, "two.txt", "2\n")

    result = worktree.ensure(**args)

    assert result["worktree_existed"] is True
    assert result["created"] is False
    assert result["commit_count"] == 2


@requires_git
def test_an_unparseable_commit_count_is_zero_rather_than_a_crash(
    repo: Path, tmp_path: Path
):
    # The worktree was created successfully; a count git could not print is no
    # reason to abort the phase.
    wt = tmp_path / "wt"

    def runner(argv: list[str]) -> str:
        if "rev-list" in argv:
            return "\n"
        return worktree.run_git(argv)

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=runner,
    )

    assert result["commit_count"] == 0
    assert result["created"] is True


@requires_git
def test_a_non_numeric_commit_count_is_zero_rather_than_a_crash(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"

    def runner(argv: list[str]) -> str:
        if "rev-list" in argv:
            return "fatal: bad revision\n"
        return worktree.run_git(argv)

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=runner,
    )

    assert result["commit_count"] == 0


@pytest.fixture
def repo_with_origin(repo: Path, tmp_path: Path) -> Path:
    """`repo`, with a local bare `origin` holding main -- no network involved."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "main")
    _git(repo, "fetch", "origin")
    return repo


@requires_git
def test_a_base_with_no_origin_falls_back_to_the_local_ref(
    repo: Path, tmp_path: Path
):
    # The common case: every base but the milestone's own is a local branch
    # this run created and never pushed.
    local_head = _head(repo)
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
    )

    assert result["created"] is True
    assert _head(wt) == local_head


@requires_git
def test_origin_is_preferred_over_the_local_ref_when_it_resolves(
    repo_with_origin: Path, tmp_path: Path
):
    origin_head = _git(repo_with_origin, "rev-parse", "origin/main").strip()
    # Local main now moves ahead of origin/main, so the two disagree.
    _commit(repo_with_origin, "local-only.txt", "not pushed\n")
    assert _head(repo_with_origin) != origin_head
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo_with_origin),
    )

    assert result["created"] is True
    assert _head(wt) == origin_head
    assert not (wt / "local-only.txt").exists()


@requires_git
def test_the_origin_probe_is_recorded_and_its_failure_is_not_an_error(
    repo: Path, tmp_path: Path
):
    calls: list[list[str]] = []
    wt = tmp_path / "wt"

    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=_recorder(calls, worktree.run_git),
    )

    assert ["rev-parse", "--verify", "--quiet", "origin/main"] == calls[2][2:]
    assert result["created"] is True


FORBIDDEN_TOKENS = ("reset", "clean", "commit", "push", "prune")


def _assert_no_forbidden_git(calls: list[list[str]]) -> None:
    for argv in calls:
        for token in FORBIDDEN_TOKENS:
            assert token not in argv, f"forbidden git operation {token!r} in {argv!r}"
        assert not ("checkout" in argv and "-f" in argv), argv
        assert not ("worktree" in argv and "remove" in argv), argv


@requires_git
def test_no_forbidden_git_operation_runs_on_any_path(repo: Path, tmp_path: Path):
    # Create path, resume-a-branch path and already-exists path, in one run.
    staging = tmp_path / "staging-wt"
    _git(repo, "worktree", "add", str(staging), "-b", "m1/task-8")
    _commit(staging, "prior.txt", "work from a killed run\n")
    _git(repo, "worktree", "remove", str(staging))

    calls: list[list[str]] = []
    runner = _recorder(calls, worktree.run_git)

    fresh = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(tmp_path / "wt-9"),
        repo_dir=str(repo),
        git_runner=runner,
    )
    resumed = worktree.ensure(
        branch="m1/task-8",
        base="main",
        worktree=str(tmp_path / "wt-8"),
        repo_dir=str(repo),
        git_runner=runner,
    )
    again = worktree.ensure(
        branch="m1/task-8",
        base="main",
        worktree=str(tmp_path / "wt-8"),
        repo_dir=str(repo),
        git_runner=runner,
    )

    assert fresh["created"] is True
    assert resumed["branch_existed"] is True
    assert again["created"] is False
    _assert_no_forbidden_git(calls)
    assert (tmp_path / "wt-8" / "prior.txt").is_file()
