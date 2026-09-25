"""Behaviour of the Integrate merge step (Integrate addendum I1/I2, card ce288496).

Placement follows design §14: `integrate.py` is a Steps component, so its
behaviour is exercised against real temporary git repositories created with
`git init` / `git worktree` in `tmp_path` -- no network, no mocks, and no
faking of git except where a test must force an answer git itself would only
give in a race. Every scenario also asserts the base branch (and `origin`,
where one exists) is untouched: Integrate never writes the base and never
pushes.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager.steps.integrate import (
    MergeInProgressError,
    measure_merge,
    merge_completed_gate,
    merge_tip,
)
from agent_manager.steps.worktree import GitError, run_git

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the integrate step's steps-tier tests",
)

BRANCH = "m5-integrate"


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


def _init_repo(root: Path) -> Path:
    """A real git repo at `root` on `main` holding README.md, a.js and b.js."""
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
    _commit(root, "a.js", "shared line\n")
    _commit(root, "b.js", "shared line\n")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `main`, isolated in tmp_path, with no origin."""
    return _init_repo(tmp_path / "repo")


@pytest.fixture
def wt(tmp_path: Path) -> Path:
    """Where the integration worktree goes. Not created: merge_tip makes it."""
    return tmp_path / "integrate-wt"


def _make_tip(repo: Path, tmp_path: Path, branch: str, files: dict[str, str]) -> str:
    """Cut `branch` from main in a throwaway worktree, commit `files`, return its sha.

    The throwaway worktree is removed again, so the story tip exists only as a
    branch -- exactly what a finished story leaves behind -- and the main
    checkout is never touched.
    """
    scratch = tmp_path / f"scratch-{branch.replace('/', '-')}"
    _git(repo, "worktree", "add", str(scratch), "-b", branch, "main")
    for name, body in files.items():
        (scratch / name).write_text(body)
        _git(scratch, "add", name)
    _git(scratch, "commit", "-m", f"work on {branch}")
    sha = _head(scratch)
    _git(repo, "worktree", "remove", str(scratch))
    return sha


def _merge(
    repo: Path,
    wt: Path,
    tip: str,
    git_runner=run_git,
    *,
    worktree: str | None = None,
) -> dict[str, object]:
    return merge_tip(
        repo_dir=str(repo),
        worktree=str(wt) if worktree is None else worktree,
        integration_branch=BRANCH,
        base_branch="main",
        tip=tip,
        git_runner=git_runner,
    )


def _base_state(repo: Path) -> dict[str, object]:
    """Everything Integrate must never change: the base, its checkout, the origin."""
    has_origin = "origin" in _git(repo, "remote").split()
    return {
        "main": _git(repo, "rev-parse", "refs/heads/main").strip(),
        "checked_out": _git(repo, "symbolic-ref", "HEAD").strip(),
        "status": _git(repo, "status", "--porcelain"),
        "origin/main": (
            _git(repo, "rev-parse", "refs/remotes/origin/main").strip()
            if has_origin
            else None
        ),
        "origin_refs": _git(repo, "ls-remote", "origin") if has_origin else None,
    }


def _recorder(calls: list[list[str]], inner=run_git):
    """A git runner that records every argv, delegating to `inner` (or returning "")."""

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        return "" if inner is None else inner(argv)

    return runner


def _is_add(argv: list[str]) -> bool:
    return "worktree" in argv and "add" in argv


def _parents(wt: Path) -> list[str]:
    """The parents of the integration worktree's HEAD commit, in order."""
    return _git(wt, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]


def _merge_head(wt: Path) -> str | None:
    """MERGE_HEAD's sha when a merge is in progress in `wt`, else None."""
    completed = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"tip": ""}, "non-empty tip ref"),
        ({"tip": "   "}, "non-empty tip ref"),
        ({"tip": None}, "non-empty tip ref"),
        ({"integration_branch": ""}, "integration_branch"),
        ({"integration_branch": None}, "integration_branch"),
        ({"base_branch": "   "}, "base_branch"),
        ({"worktree": "relative/wt"}, "absolute path for worktree"),
        ({"repo_dir": "relative/repo"}, "absolute path for repo_dir"),
    ],
    ids=[
        "empty-tip",
        "blank-tip",
        "none-tip",
        "empty-integration-branch",
        "none-integration-branch",
        "blank-base-branch",
        "relative-worktree",
        "relative-repo-dir",
    ],
)
def test_bad_arguments_raise_before_any_git_invocation(kwargs, expected):
    calls: list[list[str]] = []
    args = {
        "repo_dir": "/abs/repo",
        "worktree": "/abs/wt",
        "integration_branch": BRANCH,
        "base_branch": "main",
        "tip": "m5/story-a",
        **kwargs,
    }

    with pytest.raises(ValueError, match=expected):
        merge_tip(**args, git_runner=_recorder(calls, inner=None))

    assert calls == []


@requires_git
def test_a_fresh_branch_is_cut_from_the_local_base_when_there_is_no_origin(
    repo: Path, wt: Path, tmp_path: Path
):
    main_sha = _head(repo)
    tip = _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    result = _merge(repo, wt, "m5/story-a")

    assert result["created"] is True
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == BRANCH
    assert _parents(wt) == [main_sha, tip]
    assert _base_state(repo) == before


@pytest.fixture
def repo_with_origin(repo: Path, tmp_path: Path) -> Path:
    """`repo` with a local bare `origin` whose main is one commit AHEAD of local main.

    No network: the origin is a bare repo in tmp_path. Origin being ahead
    makes `origin/main` and `main` disagree, so the test can tell which one
    the integration branch was cut from.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "main")
    _make_tip(repo, tmp_path, "ahead", {"remote-only.txt": "pushed elsewhere\n"})
    _git(repo, "push", "origin", "ahead:main")
    _git(repo, "branch", "-D", "ahead")
    _git(repo, "fetch", "origin")
    return repo


@requires_git
def test_a_fresh_branch_is_cut_from_origin_base_when_origin_resolves(
    repo_with_origin: Path, wt: Path, tmp_path: Path
):
    repo = repo_with_origin
    origin_main = _git(repo, "rev-parse", "origin/main").strip()
    assert origin_main != _head(repo)
    tip = _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    result = _merge(repo, wt, "m5/story-a")

    assert result["created"] is True
    assert _parents(wt) == [origin_main, tip]
    assert (wt / "remote-only.txt").is_file()
    assert _base_state(repo) == before


@requires_git
def test_a_clean_merge_reports_what_was_merged(repo: Path, wt: Path, tmp_path: Path):
    main_sha = _head(repo)
    tip = _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    result = _merge(repo, wt, "m5/story-a")

    assert result == {
        "created": True,
        "conflict": False,
        "files": [],
        "merged": "m5/story-a",
        "already_merged": False,
        "detail": "",
    }
    # --no-ff: a real merge commit with the base first and the tip second.
    assert _parents(wt) == [main_sha, tip]
    assert (wt / "a.js").read_text() == "from story a\n"
    assert _merge_head(wt) is None
    assert _base_state(repo) == before


@requires_git
def test_an_existing_branch_and_worktree_are_reused(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    tip_b = _make_tip(repo, tmp_path, "m5/story-b", {"b.js": "from story b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    prior = _head(wt)

    calls: list[list[str]] = []
    result = _merge(repo, wt, "m5/story-b", _recorder(calls))

    assert result["created"] is False
    assert result["merged"] == "m5/story-b"
    assert not any(_is_add(argv) for argv in calls)
    assert _parents(wt) == [prior, tip_b]
    assert _base_state(repo) == before


@requires_git
def test_a_removed_worktree_is_re_added_on_the_existing_branch_without_b(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    tip_b = _make_tip(repo, tmp_path, "m5/story-b", {"b.js": "from story b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    prior = _head(wt)
    _git(repo, "worktree", "remove", str(wt))

    calls: list[list[str]] = []
    result = _merge(repo, wt, "m5/story-b", _recorder(calls))

    adds = [argv for argv in calls if _is_add(argv)]
    assert adds == [["-C", str(repo), "worktree", "add", str(wt), BRANCH]]
    assert result["created"] is True
    assert result["merged"] == "m5/story-b"
    assert _parents(wt) == [prior, tip_b]
    assert (wt / "a.js").read_text() == "from story a\n"
    assert _base_state(repo) == before


@requires_git
def test_a_tip_given_as_a_commit_sha_is_merged_and_reported_as_given(
    repo: Path, wt: Path, tmp_path: Path
):
    main_sha = _head(repo)
    tip = _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    result = _merge(repo, wt, tip)

    assert result["merged"] == tip
    assert result["conflict"] is False
    assert _parents(wt) == [main_sha, tip]
    assert _base_state(repo) == before


@requires_git
def test_an_already_merged_tip_is_a_stable_no_op(repo: Path, wt: Path, tmp_path: Path):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)
    first = _merge(repo, wt, "m5/story-a")
    head = _head(wt)

    second = _merge(repo, wt, "m5/story-a")
    third = _merge(repo, wt, "m5/story-a")

    expected = {
        "created": False,
        "conflict": False,
        "files": [],
        "merged": "m5/story-a",
        "already_merged": True,
        "detail": "",
    }
    assert first["already_merged"] is False
    assert second == expected
    assert third == expected
    assert _head(wt) == head
    assert _base_state(repo) == before


@requires_git
def test_an_already_merged_tip_never_reaches_git_merge(
    repo: Path, wt: Path, tmp_path: Path
):
    # git measures containment (merge-base --is-ancestor); merge is not even tried.
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")

    calls: list[list[str]] = []
    _merge(repo, wt, "m5/story-a", _recorder(calls))

    assert any("--is-ancestor" in argv for argv in calls)
    assert not any("merge" in argv for argv in calls)
    assert _base_state(repo) == before


@requires_git
def test_git_saying_already_up_to_date_is_also_the_no_op(
    repo: Path, wt: Path, tmp_path: Path
):
    # Only a race could make the ancestry probe say "no" for a merged tip; the
    # probe is forced here so the real `git merge` prints "Already up to date."
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    head = _head(wt)

    def runner(argv: list[str]) -> str:
        if "--is-ancestor" in argv:
            raise GitError("forced: the ancestry probe answers no", argv=argv, exit_code=1)
        return run_git(argv)

    result = _merge(repo, wt, "m5/story-a", runner)

    assert result["already_merged"] is True
    assert result["merged"] == "m5/story-a"
    assert result["conflict"] is False
    assert _head(wt) == head
    assert _base_state(repo) == before


@requires_git
def test_a_conflict_is_reported_and_left_in_progress(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    tip_b = _make_tip(repo, tmp_path, "m5/story-b", {"a.js": "from story b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    head = _head(wt)

    calls: list[list[str]] = []
    result = _merge(repo, wt, "m5/story-b", _recorder(calls))

    assert result["conflict"] is True
    assert result["files"] == ["a.js"]
    assert result["merged"] is None
    assert result["already_merged"] is False
    assert result["created"] is False
    assert result["detail"].strip() != ""
    assert "\n" not in result["detail"]
    # Left in progress for a resolver: never aborted, never reset.
    assert _merge_head(wt) == tip_b
    assert "<<<<<<<" in (wt / "a.js").read_text()
    assert _head(wt) == head
    assert not any("--abort" in argv for argv in calls)
    assert _base_state(repo) == before


@requires_git
def test_a_conflict_across_several_files_lists_them_all(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "a from a\n", "b.js": "b from a\n"})
    _make_tip(repo, tmp_path, "m5/story-b", {"a.js": "a from b\n", "b.js": "b from b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")

    result = _merge(repo, wt, "m5/story-b")

    assert result["conflict"] is True
    assert result["files"] == ["a.js", "b.js"]
    assert _base_state(repo) == before


@requires_git
def test_a_bad_tip_ref_raises_rather_than_reporting_a_conflict(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)

    with pytest.raises(GitError) as excinfo:
        _merge(repo, wt, "m5/no-such-story")

    assert excinfo.value.exit_code not in (None, 0, 1)
    assert _merge_head(wt) is None
    assert _base_state(repo) == before


@requires_git
def test_local_edits_the_merge_would_overwrite_raise_and_survive(
    repo: Path, wt: Path, tmp_path: Path
):
    # git refuses the merge but leaves no unmerged paths: that is a failure to
    # re-raise, not a conflict to hand to a resolver.
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    _make_tip(repo, tmp_path, "m5/story-c", {"b.js": "from story c\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    head = _head(wt)
    (wt / "b.js").write_text("uncommitted edit\n")

    with pytest.raises(GitError) as excinfo:
        _merge(repo, wt, "m5/story-c")

    assert "merge" in excinfo.value.argv
    assert (wt / "b.js").read_text() == "uncommitted edit\n"
    assert _merge_head(wt) is None
    assert _head(wt) == head
    assert _base_state(repo) == before


def _leave_a_conflict(repo: Path, wt: Path, tmp_path: Path) -> str:
    """Merge story-a cleanly, then story-b onto the same line: return story-b's sha."""
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    tip_b = _make_tip(repo, tmp_path, "m5/story-b", {"a.js": "from story b\n"})
    _merge(repo, wt, "m5/story-a")
    conflict = _merge(repo, wt, "m5/story-b")
    assert conflict["conflict"] is True
    return tip_b


@requires_git
def test_a_merge_left_in_progress_refuses_the_next_call(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-c", {"c.txt": "from story c\n"})
    before = _base_state(repo)
    tip_b = _leave_a_conflict(repo, wt, tmp_path)
    head = _head(wt)
    status = _git(wt, "status", "--porcelain")
    marked = (wt / "a.js").read_text()

    for tip in ("m5/story-c", "m5/story-b"):
        with pytest.raises(MergeInProgressError, match="never resolved") as excinfo:
            _merge(repo, wt, tip)
        message = str(excinfo.value)
        assert "already in progress" in message
        assert str(wt) in message
        assert excinfo.value.worktree == str(wt)

    assert _merge_head(wt) == tip_b
    assert _head(wt) == head
    assert _git(wt, "status", "--porcelain") == status
    assert (wt / "a.js").read_text() == marked
    assert _base_state(repo) == before


@requires_git
def test_a_differently_spelled_worktree_path_is_still_refused_mid_merge(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    tip_b = _leave_a_conflict(repo, wt, tmp_path)

    with pytest.raises(MergeInProgressError):
        _merge(repo, wt, "m5/story-b", worktree=f"{wt}{os.sep}.{os.sep}")

    assert _merge_head(wt) == tip_b
    assert _base_state(repo) == before


@requires_git
def test_a_merge_head_probe_that_fails_otherwise_is_re_raised(
    repo: Path, wt: Path, tmp_path: Path
):
    # Exit 1 means "no MERGE_HEAD"; any other failure is not an answer and
    # must not be read as "safe to merge".
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    _make_tip(repo, tmp_path, "m5/story-b", {"b.js": "from story b\n"})
    before = _base_state(repo)
    _merge(repo, wt, "m5/story-a")
    head = _head(wt)

    def runner(argv: list[str]) -> str:
        if "MERGE_HEAD" in argv:
            raise GitError("forced: the probe itself broke", argv=argv, exit_code=128)
        return run_git(argv)

    with pytest.raises(GitError) as excinfo:
        _merge(repo, wt, "m5/story-b", runner)

    assert "MERGE_HEAD" in excinfo.value.argv
    assert _head(wt) == head
    assert _base_state(repo) == before


@requires_git
def test_a_worktree_not_yet_registered_skips_the_probe(
    repo: Path, wt: Path, tmp_path: Path
):
    # The guard does not need the integration branch to exist yet.
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    before = _base_state(repo)

    calls: list[list[str]] = []
    result = _merge(repo, wt, "m5/story-a", _recorder(calls))

    assert result["created"] is True
    assert not any("MERGE_HEAD" in argv for argv in calls)
    assert _base_state(repo) == before


@requires_git
def test_a_conflict_a_human_resolved_and_committed_is_merged_on_relaunch(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)
    (wt / "a.js").write_text("resolved by a human\n")
    _git(wt, "add", "a.js")
    _git(wt, "commit", "--no-edit")
    head = _head(wt)

    for tip in ("m5/story-a", "m5/story-b"):
        assert _merge(repo, wt, tip) == {
            "created": False,
            "conflict": False,
            "files": [],
            "merged": tip,
            "already_merged": True,
            "detail": "",
        }

    assert _head(wt) == head
    assert _merge_head(wt) is None
    assert _base_state(repo) == before


FORBIDDEN_TOKENS = ("reset", "clean", "commit", "push", "prune", "--abort")


def _assert_no_forbidden_git(calls: list[list[str]]) -> None:
    for argv in calls:
        for token in FORBIDDEN_TOKENS:
            assert token not in argv, f"forbidden git operation {token!r} in {argv!r}"
        assert not ("checkout" in argv and "-f" in argv), argv
        assert not ("worktree" in argv and "remove" in argv), argv
        assert "update-ref" not in argv, argv
        assert "branch" not in argv, argv


@requires_git
def test_no_forbidden_git_operation_runs_on_any_path(
    repo: Path, wt: Path, tmp_path: Path
):
    _make_tip(repo, tmp_path, "m5/story-a", {"a.js": "from story a\n"})
    _make_tip(repo, tmp_path, "m5/story-b", {"a.js": "from story b\n"})
    _make_tip(repo, tmp_path, "m5/story-c", {"c.txt": "from story c\n"})
    before = _base_state(repo)
    calls: list[list[str]] = []
    runner = _recorder(calls)

    fresh = _merge(repo, wt, "m5/story-a", runner)
    again = _merge(repo, wt, "m5/story-a", runner)
    conflict = _merge(repo, wt, "m5/story-b", runner)
    with pytest.raises(MergeInProgressError):
        _merge(repo, wt, "m5/story-c", runner)

    assert fresh["created"] is True
    assert again["already_merged"] is True
    assert conflict["conflict"] is True
    _assert_no_forbidden_git(calls)
    assert _base_state(repo) == before


# --- measure_merge / merge_completed_gate (card 9c6741b0, Integrate addendum I3) ---
#
# git judges whether a merge is complete. Every test below measures a real temp
# repo, records the git calls the measurement made, and asserts the base is
# untouched.

MARKED = "<<<<<<< ours\nkept\n=======\ntheirs\n>>>>>>> m5/story-b\n"
"""A file body holding both conflict-marker lines."""

READ_ONLY_SUBCOMMANDS = ("rev-parse", "status", "diff")


def _assert_read_only_git(calls: list[list[str]], wt: Path) -> None:
    """Measuring a merge only asks git questions, and only about `wt`."""
    assert calls, "git must be asked: nothing here may be judged without it"
    for argv in calls:
        assert argv[:2] == ["-C", str(wt)], argv
        assert argv[2] in READ_ONLY_SUBCOMMANDS, f"not a read-only git call: {argv!r}"
    _assert_no_forbidden_git(calls)


def _porcelain(wt: Path) -> str:
    return _git(wt, "status", "--porcelain", "--untracked-files=all")


def _merged_cleanly(repo: Path, wt: Path, tmp_path: Path, files: dict[str, str]) -> None:
    """Merge a story-a tip carrying `files` into a fresh integration worktree."""
    _make_tip(repo, tmp_path, "m5/story-a", files)
    result = _merge(repo, wt, "m5/story-a")
    assert result["conflict"] is False
    assert result["already_merged"] is False


@requires_git
def test_measure_merge_reports_a_merge_left_in_progress(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)

    calls: list[list[str]] = []
    measured = measure_merge(str(wt), git_runner=_recorder(calls))

    assert measured == {
        "merge_in_progress": True,
        "status": _porcelain(wt),
        "marked_files": ["a.js"],
    }
    assert "a.js" in measured["status"]
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_measure_merge_reports_a_finished_merge(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    calls: list[list[str]] = []
    # A Path is accepted as well as a str: the engine's context holds a Path.
    measured = measure_merge(wt, git_runner=_recorder(calls))

    assert measured == {"merge_in_progress": False, "status": "", "marked_files": []}
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@pytest.mark.parametrize(
    ("body", "marked"),
    [
        (MARKED, True),
        ("<<<<<<< ours\r\nkept\r\n>>>>>>> theirs\r\n", True),
        ("Title\n=======\n\nBody text.\n", False),
        ("<<<<<<< only the opening marker\n", False),
        (">>>>>>> only the closing marker\n", False),
        ("text <<<<<<< mid-line\ntext >>>>>>> mid-line\n", False),
        ("<<<<<<<no-space\n>>>>>>>no-space\n", False),
    ],
    ids=[
        "both-markers",
        "both-markers-crlf",
        "markdown-underline-only",
        "opening-only",
        "closing-only",
        "markers-mid-line",
        "markers-without-the-space",
    ],
)
@requires_git
def test_a_touched_file_is_marked_only_with_both_marker_lines(
    repo: Path, wt: Path, tmp_path: Path, body: str, marked: bool
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"notes.md": body})

    measured = measure_merge(str(wt))

    assert measured["marked_files"] == (["notes.md"] if marked else [])
    assert measured["merge_in_progress"] is False
    assert measured["status"] == ""
    assert _base_state(repo) == before


@requires_git
def test_markers_in_a_file_the_merge_did_not_touch_are_ignored(
    repo: Path, wt: Path, tmp_path: Path
):
    # Committed on the base before the tip: every merge carries it, none touched it.
    _commit(repo, "legacy.md", MARKED)
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})
    assert (wt / "legacy.md").read_text() == MARKED  # non-vacuity

    assert measure_merge(str(wt))["marked_files"] == []
    assert _base_state(repo) == before


@pytest.mark.parametrize("name", ["my notes.md", "café.md", 'say "hi".md'])
@requires_git
def test_a_touched_file_with_an_unusual_name_is_read_by_its_real_name(
    repo: Path, wt: Path, tmp_path: Path, name: str
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {name: MARKED})

    assert measure_merge(str(wt))["marked_files"] == [name]
    assert _base_state(repo) == before


@requires_git
def test_a_touched_binary_file_is_scanned_as_bytes(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})
    # Not valid UTF-8: a text read would raise UnicodeDecodeError.
    (wt / "blob.bin").write_bytes(b"<<<<<<< \xff\xfe\n\x00\x80\n>>>>>>> \xfe\n")
    _git(wt, "add", "blob.bin")
    _git(wt, "commit", "-m", "add a binary")

    assert measure_merge(str(wt)) == {
        "merge_in_progress": False,
        "status": "",
        "marked_files": ["blob.bin"],
    }
    assert _base_state(repo) == before


@requires_git
def test_a_touched_file_that_was_deleted_is_skipped(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})
    _git(wt, "rm", "-q", "b.js")
    _git(wt, "commit", "-m", "drop b.js")

    assert measure_merge(str(wt)) == {
        "merge_in_progress": False,
        "status": "",
        "marked_files": [],
    }
    assert _base_state(repo) == before


@requires_git
def test_a_head_with_no_parent_has_nothing_touched(tmp_path: Path):
    solo = tmp_path / "solo"
    subprocess.run(
        ["git", "init", "-b", "main", str(solo)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(solo, "config", "user.email", "tests@example.com")
    _git(solo, "config", "user.name", "agent-manager tests")
    _git(solo, "config", "commit.gpgsign", "false")
    _commit(solo, "notes.md", MARKED)

    assert measure_merge(str(solo)) == {
        "merge_in_progress": False,
        "status": "",
        "marked_files": [],
    }


RESOLVED = {"resolved": True, "files": ["a.js"], "summary": "kept both sides"}
"""What a resolver that claims success returns. The gate must not believe it."""


@requires_git
def test_the_gate_passes_a_finished_merge(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is None
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_fails_a_merge_still_in_progress(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    tip_b = _leave_a_conflict(repo, wt, tmp_path)
    head = _head(wt)

    calls: list[list[str]] = []
    verdict = merge_completed_gate(None, str(wt), git_runner=_recorder(calls))

    assert verdict is not None
    assert set(verdict) == {"detail"}
    detail = verdict["detail"]
    assert "MERGE_HEAD" in detail
    assert "commit" in detail
    assert "a.js" in detail
    # Judging changed nothing: the merge is still exactly where it was.
    assert _merge_head(wt) == tip_b
    assert _head(wt) == head
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@pytest.mark.parametrize("path", ["scratch.txt", "b.js"], ids=["untracked", "modified"])
@requires_git
def test_the_gate_fails_a_finished_merge_with_a_dirty_tree(
    repo: Path, wt: Path, tmp_path: Path, path: str
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})
    (wt / path).write_text("left behind by the resolver\n")

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is not None
    assert set(verdict) == {"detail"}
    assert "not clean" in verdict["detail"]
    assert path in verdict["detail"]
    assert "MERGE_HEAD" not in verdict["detail"]
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_fails_a_committed_resolution_that_kept_the_markers(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)
    assert "<<<<<<< " in (wt / "a.js").read_text()  # non-vacuity
    # A resolver that staged and committed the conflicted file as-is.
    _git(wt, "add", "a.js")
    _git(wt, "commit", "--no-edit")

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is not None
    assert set(verdict) == {"detail"}
    assert "Conflict markers remain in: a.js" in verdict["detail"]
    assert "MERGE_HEAD" not in verdict["detail"]
    assert "not clean" not in verdict["detail"]
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_passes_a_touched_file_with_only_an_underline(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"README.md": "Title\n=======\n\nBody.\n"})

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is None
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_a_resolver_claiming_resolved_does_not_pass_an_unfinished_merge(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)

    calls: list[list[str]] = []
    claimed = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))
    unclaimed = merge_completed_gate({"resolved": False}, str(wt))

    assert claimed is not None
    assert "MERGE_HEAD" in claimed["detail"]
    assert claimed == unclaimed
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_ignores_markers_in_a_file_the_merge_did_not_touch(
    repo: Path, wt: Path, tmp_path: Path
):
    _commit(repo, "legacy.md", MARKED)
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    calls: list[list[str]] = []
    verdict = merge_completed_gate(RESOLVED, str(wt), git_runner=_recorder(calls))

    assert verdict is None
    _assert_read_only_git(calls, wt)
    assert _base_state(repo) == before


@requires_git
def test_the_gate_names_every_failure_in_order(repo: Path, wt: Path, tmp_path: Path):
    before = _base_state(repo)
    _leave_a_conflict(repo, wt, tmp_path)
    (wt / "scratch.txt").write_text("left behind\n")

    detail = merge_completed_gate(RESOLVED, str(wt))["detail"]

    assert detail.startswith("The merge is not complete: ")
    in_progress = detail.index("MERGE_HEAD")
    markers = detail.index("Conflict markers remain in: a.js")
    dirty = detail.index("The working tree is not clean: ")
    assert in_progress < markers < dirty
    assert "?? scratch.txt" in detail[dirty:]
    assert "a.js" in detail[dirty:]  # the unmerged path is dirty too
    assert "\n" not in detail
    assert _base_state(repo) == before


@requires_git
def test_a_merge_head_probe_failing_otherwise_propagates_from_the_gate(
    repo: Path, wt: Path, tmp_path: Path
):
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    def runner(argv: list[str]) -> str:
        if "MERGE_HEAD" in argv:
            raise GitError("forced: the probe itself broke", argv=argv, exit_code=128)
        return run_git(argv)

    with pytest.raises(GitError) as excinfo:
        merge_completed_gate(RESOLVED, str(wt), git_runner=runner)

    assert excinfo.value.exit_code == 128
    assert "MERGE_HEAD" in excinfo.value.argv
    assert _base_state(repo) == before


@pytest.mark.parametrize(
    ("token", "exit_code"),
    [("status", 1), ("diff", 1), ("HEAD^1", 128)],
    ids=["status-exit-1", "diff-exit-1", "first-parent-probe-exit-128"],
)
@requires_git
def test_any_other_git_failure_propagates_from_the_gate(
    repo: Path, wt: Path, tmp_path: Path, token: str, exit_code: int
):
    # Exit 1 is "absent" only for a ref probe; anywhere else it is a failure.
    before = _base_state(repo)
    _merged_cleanly(repo, wt, tmp_path, {"a.js": "from story a\n"})

    def runner(argv: list[str]) -> str:
        if token in argv:
            raise GitError("forced failure", argv=argv, exit_code=exit_code)
        return run_git(argv)

    with pytest.raises(GitError) as excinfo:
        merge_completed_gate(RESOLVED, str(wt), git_runner=runner)

    assert token in excinfo.value.argv
    assert _base_state(repo) == before


@requires_git
def test_the_gate_raises_for_a_worktree_that_is_not_a_repository(tmp_path: Path):
    with pytest.raises(GitError) as excinfo:
        merge_completed_gate(RESOLVED, str(tmp_path / "not-a-repo"))

    assert excinfo.value.exit_code not in (None, 0, 1)
