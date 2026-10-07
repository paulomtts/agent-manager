"""Create a subtask's worktree, idempotently, and report what was already there.

A deterministic step (design §4 `steps/`, §6): no model calls, no board access,
no network beyond whatever git itself does. Ported from `prepare` in the
leave-me-alone plugin's `scripts/worktree.mjs`, whose `worktree.test.mjs` is the
behavioural specification.

It creates and reports. It never resets, never deletes, never commits: deciding
RESUME vs RESET needs the plan hash, which does not exist at this point in the
run, so the forbidden-operations list (`reset`, `checkout -f`, `clean`,
`commit`, `push`, `worktree remove`, `prune`) is asserted by the tests.

Every invocation is an argument list handed to `subprocess` (design §5 line
252): there is no shell string and nothing to quote.

Parallel lanes (parallel-stories decision P3) share one repository, and so may
separate `am` processes, so `git worktree add` runs under `git_lock`, the
repository's process-wide `git` lock (spec X7 of the multi-process design),
re-checking registration inside it so two lanes ensuring the same worktree both
succeed. A `locks.LockTimeoutError` from it is never caught here. There is
deliberately no `git worktree prune`: it is a global sweep that could remove
another lane's not-yet-populated worktree.
"""

import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from agent_manager import locks

GIT = "git"
"""Executable name, resolved on PATH. Argv element zero of every call."""

GitRunner = Callable[[list[str]], str]
"""Takes a git argv (without the leading `git`), returns stdout, raises on failure."""


class GitError(RuntimeError):
    """A git invocation that exited non-zero, carrying enough to journal it."""

    def __init__(
        self,
        message: str,
        *,
        argv: list[str],
        exit_code: int | None = None,
    ) -> None:
        self.message = message
        self.argv = list(argv)
        self.exit_code = exit_code
        super().__init__(f"{message} (argv={self.argv!r}, exit_code={exit_code!r})")


def run_git(argv: list[str]) -> str:
    """The default `GitRunner`: run `git <argv>` and return its stdout."""
    try:
        completed = subprocess.run(
            [GIT, *argv],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise GitError(f"could not run {GIT}: {exc.strerror}", argv=argv) from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise GitError(detail, argv=argv, exit_code=completed.returncode)

    return completed.stdout


_WORKTREE_PREFIX = "worktree "


def worktree_paths(porcelain: str) -> list[str]:
    """The paths from `git worktree list --porcelain`.

    Only lines starting with the literal `worktree ` are entries; the
    `HEAD <sha>`, `branch refs/heads/...` and blank lines between records are
    not paths and must not be read as one.
    """
    return [
        line[len(_WORKTREE_PREFIX) :].strip()
        for line in porcelain.split("\n")
        if line.startswith(_WORKTREE_PREFIX)
    ]


def branch_exists(ref_list: str, branch: str) -> bool:
    """Whether `branch` is one whole line of a `%(refname:short)` listing.

    Exact line equality, never a prefix test: `m1/task-` must not match
    `m1/task-9`, or a resumed run would check out the wrong branch. Blank lines
    are dropped so an empty `branch` can never match one.
    """
    refs = [line.strip() for line in ref_list.split("\n") if line.strip()]
    return branch in refs


def _required_name(value: object, field: str) -> str:
    """A non-blank ref name, or `ValueError` before anything is run."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(
            f"worktree.ensure needs a non-empty {field} name, got {value!r}"
        )
    return value


def _required_absolute(value: object, field: str) -> str:
    """An absolute path as a string, or `ValueError` before anything is run."""
    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"worktree.ensure needs an absolute path for {field}, got {value!r}"
        )
    text = str(value).strip()
    if text == "" or not Path(text).is_absolute():
        raise ValueError(
            f"worktree.ensure needs an absolute path for {field}, got {value!r}"
        )
    return text


def _is_registered(candidate: str, registered: list[str]) -> bool:
    """Whether `candidate` is one of the paths git already has registered.

    A plain string match first, as the JS did. Falling back to `realpath`
    matters because the caller's path and git's recorded path can differ by a
    trailing slash, a `.` component or a symlinked parent -- and a false
    negative here would run `worktree add` onto a live directory and abort the
    run.
    """
    if candidate in registered:
        return True
    real = os.path.realpath(candidate)
    return any(os.path.realpath(path) == real for path in registered)


def _is_live_worktree(candidate: str, registered: list[str]) -> bool:
    """Whether `candidate` is registered with git AND still a directory on disk.

    A worktree deleted by hand (`rm -rf`) stays registered until something
    prunes it; that stale registration must count as not existing, or
    `ensure` would report a worktree that is not there.
    """
    return _is_registered(candidate, registered) and Path(candidate).is_dir()


def _branch_live_elsewhere(candidate: str, porcelain: str, branch: str) -> bool:
    """Whether `branch` is checked out in a live worktree other than `candidate`.

    `worktree add -f` also overrides git's "branch already checked out"
    refusal, so a stale path whose dead registration held some OTHER branch
    must not be forced while `branch` is live elsewhere: that would check one
    branch out twice.
    """
    real = os.path.realpath(candidate)
    path: str | None = None
    for line in porcelain.split("\n"):
        if line.startswith(_WORKTREE_PREFIX):
            path = line[len(_WORKTREE_PREFIX) :].strip()
        elif line.strip() == f"branch refs/heads/{branch}" and path is not None:
            if os.path.realpath(path) != real and Path(path).is_dir():
                return True
    return False


def _commit_count(git_runner: GitRunner, worktree_path: str, resolved_base: str) -> int:
    """Commits on HEAD that are not on `resolved_base`, or 0 if unreadable.

    Mirrors the JS `Number(...) || 0`: an empty or non-numeric answer is no
    reason to fail a worktree that was just prepared successfully.
    """
    raw = git_runner(
        ["-C", worktree_path, "rev-list", "--count", f"{resolved_base}..HEAD"]
    )
    try:
        return int(str(raw).strip())
    except ValueError:
        return 0


def _resolve_base(git_runner: GitRunner, repo_path: str, base: str) -> str:
    """`origin/<base>` when it resolves, else the bare local `<base>`.

    `base` names a real remote branch only when it IS the milestone's own base
    branch -- every other base is another subtask's or story's local branch,
    which this run created and never pushes. A missing `origin/<base>` is the
    expected case, not an error, so the probe's failure is swallowed here and
    nowhere else.
    """
    try:
        git_runner(
            ["-C", repo_path, "rev-parse", "--verify", "--quiet", f"origin/{base}"]
        )
    except GitError:
        return base
    return f"origin/{base}"


def _ok(git_runner: GitRunner, argv: list[str]) -> bool:
    try:
        git_runner(argv)
    except GitError:
        return False
    return True


def _checkout_of(git_runner: GitRunner, repo_path: str, branch: str) -> str | None:
    """The path of the worktree that has `branch` checked out, or `None`."""
    porcelain = git_runner(["-C", repo_path, "worktree", "list", "--porcelain"])
    path = None
    for line in porcelain.split("\n"):
        if line.startswith(_WORKTREE_PREFIX):
            path = line[len(_WORKTREE_PREFIX) :].strip()
        elif line.strip() == f"branch refs/heads/{branch}":
            return path
    return None


def fast_forward_base(git_runner: GitRunner, repo_path: str, base: str) -> bool:
    """Fast-forward local `base` to `origin/<base>` when that loses nothing.

    Only when the local branch is a strict ancestor of its upstream, so a
    branch holding local-only commits is never touched. A checked-out `base` is
    advanced with `merge --ff-only`, and only when that checkout is clean;
    otherwise the ref is moved with a compare-and-swap `update-ref`. Returns
    whether it moved. Never resets, never rewrites.
    """
    local, remote = f"refs/heads/{base}", f"refs/remotes/origin/{base}"
    if not (_ok(git_runner, ["-C", repo_path, "rev-parse", "--verify", "--quiet", local])
            and _ok(git_runner, ["-C", repo_path, "rev-parse", "--verify", "--quiet", remote])):
        return False
    old = git_runner(["-C", repo_path, "rev-parse", local]).strip()
    new = git_runner(["-C", repo_path, "rev-parse", remote]).strip()
    if old == new or not _ok(
        git_runner, ["-C", repo_path, "merge-base", "--is-ancestor", old, new]
    ):
        return False
    with git_lock(repo_path):
        checkout = _checkout_of(git_runner, repo_path, base)
        if checkout is None:
            return _ok(git_runner, ["-C", repo_path, "update-ref", local, new, old])
        if git_runner(["-C", checkout, "status", "--porcelain"]).strip():
            return False
        return _ok(git_runner, ["-C", checkout, "merge", "--ff-only", "-q", new])


_REPO_LOCKS: dict[str, threading.RLock] = {}
"""One lock per repository, keyed by its resolved path, created on first use."""

_REPO_LOCKS_GUARD = threading.Lock()
"""Guards lookup-or-create in `_REPO_LOCKS`, so one repository never gets two locks."""


def _repo_lock(repo_path: str | Path) -> threading.RLock:
    """The in-process layer of `git_lock` for the repository at `repo_path`.

    Keyed by the resolved path, so a trailing slash, a `..` hop or a symlinked
    spelling of the same repository all share one lock. An `RLock` because a
    `ProcessLock` re-acquires its in-process layer on every nested acquire;
    `ensure` itself never re-enters it. Other `am` processes on the repository
    are coordinated by `git_lock`'s flock (spec X7).
    """
    key = str(Path(repo_path).resolve())
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REPO_LOCKS[key] = lock
        return lock


def git_lock(repo_path: str | Path) -> locks.ProcessLock:
    """The repository's process-wide `git` lock (spec X7).

    Serializes `git worktree add` (with its re-check) here and
    `orchestrate.refresh_git`'s `remote`/`fetch`/`prune` across threads and
    `am` processes. Keyed by the resolved repository path; its in-process
    layer is `_repo_lock(repo_path)`. Never taken while the board lock is held,
    nor the reverse.
    """
    return locks.project_lock(
        Path(repo_path).resolve(), "git", local=_repo_lock(repo_path)
    )


def ensure(
    branch: str,
    base: str,
    worktree: str | Path,
    repo_dir: str | Path,
    git_runner: GitRunner = run_git,
    fast_forward: bool = False,
) -> dict[str, object]:
    """Make sure `branch`'s worktree exists at `worktree`, and report what was there.

    `branch` and `base` arrive already computed by `dag.py` and inlined
    (design §7); this module never derives a branch name. The return value is
    the deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).

    A path git still has registered but whose directory is gone counts as not
    existing: it is re-added with `worktree add -f`, checking a surviving
    branch out rather than re-cutting it (card cf03b236).
    """
    branch = _required_name(branch, "branch")
    base = _required_name(base, "base")
    worktree_path = _required_absolute(worktree, "worktree")
    repo_path = _required_absolute(repo_dir, "repo_dir")

    branch_existed = branch_exists(
        git_runner(
            [
                "-C",
                repo_path,
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/",
            ]
        ),
        branch,
    )
    registered = worktree_paths(
        git_runner(["-C", repo_path, "worktree", "list", "--porcelain"])
    )
    worktree_existed = _is_live_worktree(worktree_path, registered)

    base_fast_forwarded = fast_forward and fast_forward_base(git_runner, repo_path, base)
    resolved_base = _resolve_base(git_runner, repo_path, base)

    created = False
    if not worktree_existed:
        # git must never run two `worktree add` on one repository at once,
        # from any thread or process. Only the re-check and the add are held
        # under the lock; the reads above and the commit count below stay
        # unlocked.
        with git_lock(repo_path):
            # Another thread may have created this very worktree between the
            # unlocked read and now: look again before adding.
            porcelain = git_runner(
                ["-C", repo_path, "worktree", "list", "--porcelain"]
            )
            registered = worktree_paths(porcelain)
            if _is_live_worktree(worktree_path, registered):
                worktree_existed = True
            else:
                # Still registered but not a directory: deleted by hand. `-f`
                # is git's override for exactly that "missing but already
                # registered" refusal; it touches bookkeeping, never branch
                # content. A clean add never gets it, so git still refuses a
                # branch that is checked out live at another path -- and
                # neither does a stale add whose branch is live elsewhere,
                # since `-f` would override that refusal too.
                force = (
                    ["-f"]
                    if _is_registered(worktree_path, registered)
                    and not _branch_live_elsewhere(worktree_path, porcelain, branch)
                    else []
                )
                if branch_existed:
                    # Check the existing branch out. Never re-cut it from
                    # base: a killed run's commits live on that branch and
                    # re-cutting would silently discard them.
                    argv = [
                        "-C",
                        repo_path,
                        "worktree",
                        "add",
                        *force,
                        worktree_path,
                        branch,
                    ]
                else:
                    argv = [
                        "-C",
                        repo_path,
                        "worktree",
                        "add",
                        *force,
                        worktree_path,
                        "-b",
                        branch,
                        resolved_base,
                    ]
                git_runner(argv)
                created = True

    return {
        "branch": branch,
        "worktree": worktree_path,
        "branch_existed": branch_existed,
        "worktree_existed": worktree_existed,
        "created": created,
        "commit_count": _commit_count(git_runner, worktree_path, resolved_base),
        **({"base_fast_forwarded": True} if base_fast_forwarded else {}),
    }
