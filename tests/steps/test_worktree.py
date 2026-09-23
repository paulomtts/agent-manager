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
