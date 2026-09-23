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
"""

import subprocess
from collections.abc import Callable

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
