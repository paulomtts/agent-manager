"""Behaviour of the subtask worktree step (design §4 `steps/`, spec card 0816e239).

Placement follows design §14: `worktree.py` is a Steps component, so its
behaviour is exercised against real temporary git repositories created with
`git init` / `git worktree` in `tmp_path` -- no network, and no faking of git
except where a test must force an output git itself would never print.
"""

import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from lockhelpers import _holder, _probe, _reap, _release

from agent_manager import locks, paths
from agent_manager.steps import worktree
from agent_manager.steps.worktree import GitError


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
    """Create a real git repo at `root` on `main` with one commit."""
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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `main` with one commit, isolated in tmp_path."""
    return _init_repo(tmp_path / "repo")


def _recorder(calls: list[list[str]], inner=None):
    """A git runner that records every argv, optionally delegating to `inner`."""

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        return "" if inner is None else inner(argv)

    return runner


def _repo_lock_is_free(repo: Path) -> bool:
    """Whether another thread can take `repo`'s in-process git lock right now.

    Probed with a non-blocking acquire from a fresh thread, so it never blocks
    and works for a `Lock` or an `RLock` alike (`RLock.locked()` only exists
    from Python 3.14; this project runs 3.12).
    """
    lock = worktree._repo_lock(str(repo))
    outcome: list[bool] = []

    def take() -> None:
        got = lock.acquire(blocking=False)
        if got:
            lock.release()
        outcome.append(got)

    prober = threading.Thread(target=take)
    prober.start()
    prober.join(timeout=30)
    assert not prober.is_alive()
    return outcome == [True]


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


def test_run_git_returns_stdout(repo: Path):
    out = worktree.run_git(["-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
    assert out.strip() == "main"


def test_run_git_raises_git_error_carrying_argv_and_exit_code(tmp_path: Path):
    argv = ["-C", str(tmp_path), "rev-parse", "--verify", "origin/nope"]
    with pytest.raises(GitError) as excinfo:
        worktree.run_git(argv)
    assert excinfo.value.argv == argv
    assert excinfo.value.exit_code not in (None, 0)


def test_a_missing_git_executable_becomes_a_git_error(monkeypatch, tmp_path: Path):
    # git is invoked by name off PATH; when it is not there at all, the phase
    # must fail as a GitError it can journal, not as a bare OSError.
    monkeypatch.setattr(worktree, "GIT", str(tmp_path / "no-such-git"))
    argv = ["-C", str(tmp_path), "rev-parse", "HEAD"]

    with pytest.raises(GitError) as excinfo:
        worktree.run_git(argv)

    assert excinfo.value.argv == argv
    assert excinfo.value.exit_code is None


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
        ({"branch": None}, "branch"),
        ({"base": None}, "base"),
        ({"worktree": None}, "worktree"),
        ({"repo_dir": 42}, "repo_dir"),
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
        "none-branch",
        "none-base",
        "none-worktree",
        "non-path-repo-dir",
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
    # Every add above is a clean add (no stale registration anywhere), so none
    # may carry `-f`: it would mask git's refusal of a branch live elsewhere.
    assert len(_adds(calls)) == 2
    assert not any("-f" in argv or "--force" in argv for argv in calls), calls


def test_every_spelling_of_one_repository_shares_one_lock(repo: Path, tmp_path: Path):
    # Trailing slash, a `..` hop and a symlinked path all name the same
    # repository, so they must serialize on the same lock.
    link = tmp_path / "repo-link"
    link.symlink_to(repo, target_is_directory=True)

    lock = worktree._repo_lock(str(repo))

    assert worktree._repo_lock(f"{repo}{os.sep}") is lock
    assert worktree._repo_lock(str(repo / ".git" / "..")) is lock
    assert worktree._repo_lock(str(link)) is lock


def test_different_repositories_get_different_locks(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    assert worktree._repo_lock(str(a)) is not worktree._repo_lock(str(b))


def test_concurrent_first_lookups_all_get_the_same_lock(tmp_path: Path):
    fresh = tmp_path / "fresh-repo"
    fresh.mkdir()
    lanes = 8
    barrier = threading.Barrier(lanes)

    def lookup(_: int) -> threading.Lock:
        barrier.wait(timeout=30)
        return worktree._repo_lock(str(fresh))

    with ThreadPoolExecutor(max_workers=lanes) as pool:
        locks = list(pool.map(lookup, range(lanes)))

    assert all(lock is locks[0] for lock in locks)


def _is_add(argv: list[str]) -> bool:
    return "worktree" in argv and "add" in argv


def test_eight_distinct_lanes_in_parallel_all_succeed(repo: Path, tmp_path: Path):
    lanes = 8
    barrier = threading.Barrier(lanes)
    state = {"in_flight": 0, "peak": 0}
    state_lock = threading.Lock()

    def runner(argv: list[str]) -> str:
        if not _is_add(argv):
            return worktree.run_git(argv)
        with state_lock:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
        try:
            time.sleep(0.05)
            return worktree.run_git(argv)
        finally:
            with state_lock:
                state["in_flight"] -= 1

    def lane(index: int) -> dict[str, object]:
        barrier.wait(timeout=30)
        return worktree.ensure(
            branch=f"m4/lane-{index}",
            base="main",
            worktree=str(tmp_path / f"wt-{index}"),
            repo_dir=str(repo),
            git_runner=runner,
        )

    with ThreadPoolExecutor(max_workers=lanes) as pool:
        futures = [pool.submit(lane, index) for index in range(lanes)]
        errors = [future.exception(timeout=120) for future in futures]

    assert errors == [None] * lanes
    results = [future.result() for future in futures]
    assert all(result["created"] is True for result in results)
    assert state["peak"] == 1

    registered = worktree.worktree_paths(
        _git(repo, "worktree", "list", "--porcelain")
    )
    assert len(registered) == lanes + 1
    assert list((repo / ".git").rglob("*.lock")) == []

    main_head = _git(repo, "rev-parse", "main").strip()
    for index in range(lanes):
        wt = tmp_path / f"wt-{index}"
        assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == (
            f"m4/lane-{index}"
        )
        assert _git(wt, "merge-base", "HEAD", "main").strip() == main_head
        assert _git(wt, "rev-parse", "HEAD").strip() == main_head


def test_two_threads_ensuring_the_same_worktree_both_succeed(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    start = threading.Barrier(2)
    after_reads = threading.Barrier(2)

    def runner(argv: list[str]) -> str:
        if "--verify" in argv:
            try:
                return worktree.run_git(argv)
            finally:
                after_reads.wait(timeout=30)
        return worktree.run_git(argv)

    def lane(_: int) -> dict[str, object]:
        start.wait(timeout=30)
        return worktree.ensure(
            branch="m4/same",
            base="main",
            worktree=str(wt),
            repo_dir=str(repo),
            git_runner=runner,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(lane, index) for index in range(2)]
        errors = [future.exception(timeout=120) for future in futures]

    assert errors == [None, None]
    results = [future.result() for future in futures]
    assert sorted(result["created"] for result in results) == [False, True]
    loser = next(result for result in results if result["created"] is False)
    assert loser["worktree_existed"] is True

    registered = worktree.worktree_paths(
        _git(repo, "worktree", "list", "--porcelain")
    )
    real_wt = os.path.realpath(wt)
    assert sum(os.path.realpath(p) == real_wt for p in registered) == 1
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "m4/same"


def _finishes_while_held(lock: threading.Lock, call) -> tuple[bool, object]:
    """Run `call` in a thread while `lock` is held; report whether it finished."""
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["result"] = call()
        except BaseException as exc:  # surfaced to the test, not swallowed
            outcome["result"] = exc

    with lock:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=10)
        finished = not thread.is_alive()
    thread.join(timeout=30)
    return finished, outcome.get("result")


def test_a_failed_add_releases_the_repository_lock(repo: Path, tmp_path: Path):
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "stray.txt").write_text("not a worktree\n")

    with pytest.raises(GitError):
        worktree.ensure(
            branch="m4/stray",
            base="main",
            worktree=str(stray),
            repo_dir=str(repo),
        )

    assert _repo_lock_is_free(repo)
    assert _probe(repo, "git") == "free"
    result = worktree.ensure(
        branch="m4/after",
        base="main",
        worktree=str(tmp_path / "wt-after"),
        repo_dir=str(repo),
    )
    assert result["created"] is True


def test_a_same_branch_at_a_different_path_surfaces_gits_error_and_frees_the_lock(
    repo: Path, tmp_path: Path
):
    worktree.ensure(
        branch="m4/shared",
        base="main",
        worktree=str(tmp_path / "wt-one"),
        repo_dir=str(repo),
    )

    with pytest.raises(GitError) as excinfo:
        worktree.ensure(
            branch="m4/shared",
            base="main",
            worktree=str(tmp_path / "wt-two"),
            repo_dir=str(repo),
        )

    assert "add" in excinfo.value.argv
    assert _repo_lock_is_free(repo)
    assert _probe(repo, "git") == "free"


def test_a_different_repository_is_never_blocked_by_this_ones_lock(
    repo: Path, tmp_path: Path
):
    other = _init_repo(tmp_path / "other-repo")

    finished, result = _finishes_while_held(
        worktree._repo_lock(str(repo)),
        lambda: worktree.ensure(
            branch="m4/other",
            base="main",
            worktree=str(tmp_path / "wt-other"),
            repo_dir=str(other),
        ),
    )

    assert finished is True
    assert isinstance(result, dict) and result["created"] is True


def test_an_existing_worktree_takes_no_lock(repo: Path, tmp_path: Path):
    args = {
        "branch": "m4/resume",
        "base": "main",
        "worktree": str(tmp_path / "wt-resume"),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)

    finished, result = _finishes_while_held(
        worktree._repo_lock(str(repo)),
        lambda: worktree.ensure(**args),
    )

    assert finished is True
    assert isinstance(result, dict) and result["created"] is False


# --- Process-wide git lock (spec X7, card 43043f10) -------------------------


def test_the_repository_lock_is_reentrant(tmp_path: Path):
    # It is the in-process layer of a ProcessLock, which re-acquires it on
    # every nested acquire; a plain Lock would deadlock its own thread.
    lock = worktree._repo_lock(str(tmp_path))
    assert lock.acquire(blocking=False)
    try:
        assert lock.acquire(blocking=False)
        lock.release()
    finally:
        lock.release()


def test_git_lock_is_keyed_by_the_resolved_repository(repo: Path):
    lock = worktree.git_lock(repo)

    assert worktree.git_lock(str(repo)) is lock
    assert worktree.git_lock(f"{repo}{os.sep}") is lock
    assert worktree.git_lock(repo / "x" / "..") is lock
    assert lock is locks.project_lock(repo, "git")
    assert lock.path == paths.project_lock_path(repo, "git")
    assert lock._local is worktree._repo_lock(str(repo))


def test_worktree_add_waits_for_the_git_lock_of_another_process(
    repo: Path, tmp_path: Path, monkeypatch
):
    # `progressed` fires on the lane's first failed flock attempt (it is now
    # waiting on the child) or, if nothing locks, when the lane finishes. Only
    # then is the marker written and the child released, so an `add` that saw
    # no marker ran while the child held the git lock.
    marker = tmp_path / "released"
    progressed = threading.Event()
    real_backoff = locks._backoff

    def signalling_backoff(attempt: int) -> float:
        progressed.set()
        return real_backoff(attempt)

    monkeypatch.setattr(locks, "_backoff", signalling_backoff)
    adds: list[bool] = []

    def runner(argv: list[str]) -> str:
        if _is_add(argv):
            adds.append(marker.exists())
        return worktree.run_git(argv)

    outcome: dict[str, object] = {}

    def lane() -> None:
        try:
            outcome["result"] = worktree.ensure(
                branch="m10/waits",
                base="main",
                worktree=str(tmp_path / "wt"),
                repo_dir=str(repo),
                git_runner=runner,
            )
        except BaseException as exc:  # surfaced by the assertions below
            outcome["error"] = exc
        finally:
            progressed.set()

    child = _holder(repo, "git")
    try:
        worker = threading.Thread(target=lane, daemon=True)
        worker.start()
        assert progressed.wait(timeout=30)
        marker.touch()
        _release(child)
        worker.join(timeout=60)
        assert not worker.is_alive()
    finally:
        _reap(child)

    assert "error" not in outcome, outcome
    assert outcome["result"]["created"] is True
    assert adds == [True]
    assert _probe(repo, "git") == "free"
    assert list(repo.rglob("*.lock")) == []  # no lock file in the repository


def test_a_git_lock_timeout_propagates_and_no_worktree_is_added(
    repo: Path, tmp_path: Path, monkeypatch
):
    lock = worktree.git_lock(repo)
    monkeypatch.setattr(lock, "_timeout", 0)
    calls: list[list[str]] = []
    child = _holder(repo, "git")
    try:
        with pytest.raises(locks.LockTimeoutError):
            worktree.ensure(
                branch="m10/timeout",
                base="main",
                worktree=str(tmp_path / "wt"),
                repo_dir=str(repo),
                git_runner=_recorder(calls, worktree.run_git),
            )
    finally:
        _reap(child)

    assert not any(_is_add(argv) for argv in calls)
    assert not (tmp_path / "wt").exists()
    assert _repo_lock_is_free(repo)


# --- Stale-registered-but-missing worktrees (card cf03b236) -----------------


def _adds(calls: list[list[str]]) -> list[list[str]]:
    """The recorded `worktree add` argvs, in call order."""
    return [argv for argv in calls if _is_add(argv)]


def test_a_cleanly_removed_worktree_and_branch_take_the_plain_add(
    repo: Path, tmp_path: Path
):
    # `git worktree remove` + `git branch -D` leave no registration behind, so
    # this is an ordinary clean add: today's argv exactly, and never `-f`.
    wt = tmp_path / "wt"
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }
    worktree.ensure(**args)
    _git(repo, "worktree", "remove", str(wt))
    _git(repo, "branch", "-D", "m1/task-9")

    calls: list[list[str]] = []
    result = worktree.ensure(**args, git_runner=_recorder(calls, worktree.run_git))

    assert result["branch_existed"] is False
    assert result["worktree_existed"] is False
    assert result["created"] is True
    assert _adds(calls) == [
        ["-C", str(repo), "worktree", "add", str(wt), "-b", "m1/task-9", "main"]
    ]
    assert not any("-f" in argv or "--force" in argv for argv in calls), calls
    assert wt.is_dir()


def _rm_rf_after_a_commit(repo: Path, wt: Path, branch: str) -> str:
    """Ensure `branch` at `wt`, commit on it, then `rm -rf` the directory.

    git keeps the registration (it shows up `prunable`), which is exactly what
    a worktree deleted outside am's bookkeeping looks like. Returns the
    commit's sha.
    """
    worktree.ensure(branch=branch, base="main", worktree=str(wt), repo_dir=str(repo))
    head = _commit(wt, "prior.txt", "work from before the rm -rf\n")
    shutil.rmtree(wt)
    assert "prunable" in _git(repo, "worktree", "list", "--porcelain")
    return head


@pytest.mark.git
def test_an_rm_rf_worktree_whose_branch_survives_is_re_added_with_its_commits(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    prior_head = _rm_rf_after_a_commit(repo, wt, "m1/task-9")
    args = {
        "branch": "m1/task-9",
        "base": "main",
        "worktree": str(wt),
        "repo_dir": str(repo),
    }

    calls: list[list[str]] = []
    result = worktree.ensure(**args, git_runner=_recorder(calls, worktree.run_git))

    assert result == {
        "branch": "m1/task-9",
        "worktree": str(wt),
        "branch_existed": True,
        "worktree_existed": False,
        "created": True,
        "commit_count": 1,
    }
    assert wt.is_dir()
    # Checked out, never re-cut from base: the commit is still there.
    assert _head(wt) == prior_head
    assert "add prior.txt" in _git(wt, "log", "--format=%s")
    assert (wt / "prior.txt").read_text() == "work from before the rm -rf\n"
    assert _adds(calls) == [
        ["-C", str(repo), "worktree", "add", "-f", str(wt), "m1/task-9"]
    ]
    assert "prunable" not in _git(repo, "worktree", "list", "--porcelain")
    _assert_no_forbidden_git(calls)

    # Once recovered, the next call is the ordinary no-op.
    again_calls: list[list[str]] = []
    again = worktree.ensure(**args, git_runner=_recorder(again_calls, worktree.run_git))
    assert again["worktree_existed"] is True
    assert again["created"] is False
    assert _adds(again_calls) == []


@pytest.mark.git
def test_an_rm_rf_worktree_whose_branch_is_also_gone_is_re_cut_from_base(
    repo: Path, tmp_path: Path
):
    wt = tmp_path / "wt"
    _rm_rf_after_a_commit(repo, wt, "m1/task-9")
    # git refuses `branch -D` while the stale registration still claims the
    # branch, which is why the branch is deleted through `update-ref` here.
    refused = subprocess.run(
        ["git", "-C", str(repo), "branch", "-D", "m1/task-9"],
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    _git(repo, "update-ref", "-d", "refs/heads/m1/task-9")

    calls: list[list[str]] = []
    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=_recorder(calls, worktree.run_git),
    )

    assert result == {
        "branch": "m1/task-9",
        "worktree": str(wt),
        "branch_existed": False,
        "worktree_existed": False,
        "created": True,
        "commit_count": 0,
    }
    assert _adds(calls) == [
        ["-C", str(repo), "worktree", "add", "-f", str(wt), "-b", "m1/task-9", "main"]
    ]
    assert _head(wt) == _head(repo)
    assert not (wt / "prior.txt").exists()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "m1/task-9"
    assert "prunable" not in _git(repo, "worktree", "list", "--porcelain")
    _assert_no_forbidden_git(calls)


def test_two_threads_recovering_the_same_rm_rf_worktree_both_succeed(
    repo: Path, tmp_path: Path
):
    # Both lanes see the stale registration unlocked; the re-check under the
    # git lock must make the second one see the first one's live worktree.
    wt = tmp_path / "wt"
    prior_head = _rm_rf_after_a_commit(repo, wt, "m4/same")
    start = threading.Barrier(2)
    after_reads = threading.Barrier(2)
    adds: list[list[str]] = []
    adds_lock = threading.Lock()

    def runner(argv: list[str]) -> str:
        if _is_add(argv):
            with adds_lock:
                adds.append(list(argv))
        if "--verify" in argv:
            try:
                return worktree.run_git(argv)
            finally:
                after_reads.wait(timeout=30)
        return worktree.run_git(argv)

    def lane(_: int) -> dict[str, object]:
        start.wait(timeout=30)
        return worktree.ensure(
            branch="m4/same",
            base="main",
            worktree=str(wt),
            repo_dir=str(repo),
            git_runner=runner,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(lane, index) for index in range(2)]
        errors = [future.exception(timeout=120) for future in futures]

    assert errors == [None, None]
    results = [future.result() for future in futures]
    assert sorted(result["created"] for result in results) == [False, True]
    loser = next(result for result in results if result["created"] is False)
    assert loser["worktree_existed"] is True
    assert adds == [["-C", str(repo), "worktree", "add", "-f", str(wt), "m4/same"]]
    assert _head(wt) == prior_head
    registered = worktree.worktree_paths(
        _git(repo, "worktree", "list", "--porcelain")
    )
    real_wt = os.path.realpath(wt)
    assert sum(os.path.realpath(p) == real_wt for p in registered) == 1


def test_a_stale_path_now_occupied_by_a_file_surfaces_gits_error_and_frees_the_lock(
    repo: Path, tmp_path: Path
):
    # `-f` overrides git's bookkeeping, never something on disk: a file now
    # sitting at the stale path must be refused by git and left untouched.
    wt = tmp_path / "wt"
    _rm_rf_after_a_commit(repo, wt, "m4/occupied")
    wt.write_text("someone else's file\n")

    with pytest.raises(GitError) as excinfo:
        worktree.ensure(
            branch="m4/occupied",
            base="main",
            worktree=str(wt),
            repo_dir=str(repo),
        )

    assert "add" in excinfo.value.argv
    assert "-f" in excinfo.value.argv
    assert wt.read_text() == "someone else's file\n"
    assert _repo_lock_is_free(repo)
    assert _probe(repo, "git") == "free"


def test_recovering_a_stale_worktree_leaves_every_other_worktree_registered(
    repo: Path, tmp_path: Path
):
    # `-f` replaces only this path's dead registration. A live sibling and an
    # unrelated stale worktree must both survive: no prune-style sweep.
    sibling = tmp_path / "sibling-wt"
    _git(repo, "worktree", "add", str(sibling), "-b", "m1/task-8")
    other_stale = tmp_path / "other-stale-wt"
    _git(repo, "worktree", "add", str(other_stale), "-b", "m1/task-7")
    shutil.rmtree(other_stale)
    wt = tmp_path / "wt"
    _rm_rf_after_a_commit(repo, wt, "m1/task-9")

    calls: list[list[str]] = []
    result = worktree.ensure(
        branch="m1/task-9",
        base="main",
        worktree=str(wt),
        repo_dir=str(repo),
        git_runner=_recorder(calls, worktree.run_git),
    )

    assert result["created"] is True
    registered = [
        os.path.realpath(p)
        for p in worktree.worktree_paths(_git(repo, "worktree", "list", "--porcelain"))
    ]
    assert os.path.realpath(sibling) in registered
    assert os.path.realpath(other_stale) in registered
    assert os.path.realpath(wt) in registered
    assert sibling.is_dir()
    assert not other_stale.exists()
    _assert_no_forbidden_git(calls)


def test_a_stale_path_never_forces_a_branch_already_live_at_another_path(
    repo: Path, tmp_path: Path
):
    # `-f` also overrides git's "branch already checked out" refusal. When the
    # stale registration at this path belongs to a different branch and the
    # requested branch is live elsewhere, forcing would check one branch out
    # twice. git's refusal must surface instead, and nothing may be added.
    wt = tmp_path / "wt"
    _rm_rf_after_a_commit(repo, wt, "m1/other")
    live = tmp_path / "live-wt"
    _git(repo, "worktree", "add", str(live), "-b", "m1/task-9")

    calls: list[list[str]] = []
    with pytest.raises(GitError) as excinfo:
        worktree.ensure(
            branch="m1/task-9",
            base="main",
            worktree=str(wt),
            repo_dir=str(repo),
            git_runner=_recorder(calls, worktree.run_git),
        )

    assert "add" in excinfo.value.argv
    assert not any("-f" in argv or "--force" in argv for argv in calls), calls
    assert not wt.exists()
    porcelain = _git(repo, "worktree", "list", "--porcelain")
    assert porcelain.count("branch refs/heads/m1/task-9\n") == 1
    assert _repo_lock_is_free(repo)
