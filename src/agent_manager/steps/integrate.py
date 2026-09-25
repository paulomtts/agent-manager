"""Merge one story tip into the integration branch, leaving any conflict for a human.

A deterministic step (design §4 `steps/`, §6; Integrate addendum decisions I1
and I2): no model calls, no board access, no network. Ported from the merge
half of the leave-me-alone plugin's `scripts/integrate.mjs`, whose
`integrate.test.mjs` is the behavioural specification.

The integration branch and its worktree come from `worktree.ensure`, so base
resolution (`origin/<base>`, else local `<base>`) and reuse are not repeated
here. git judges everything git can measure: whether a merge is already in
progress (`MERGE_HEAD`), whether the tip is already merged
(`merge-base --is-ancestor`), and which files conflict
(`diff --diff-filter=U`).

A conflict is left in progress -- `MERGE_HEAD` set, markers in the tree --
because that is exactly the state a resolver needs. So this module never runs
`merge --abort`, `reset`, `checkout -f`, `clean`, `commit` or `push`, and never
writes the base branch; the tests assert all of it.

Every invocation is an argument list handed to the git runner: there is no
shell string and nothing to quote.
"""

from collections.abc import Iterable
from pathlib import Path

from agent_manager.steps.worktree import (
    GitError,
    GitRunner,
    _required_absolute,
    _required_name,
    ensure,
    run_git,
)


class MergeInProgressError(RuntimeError):
    """A merge is already under way in the integration worktree; a human must finish it."""

    def __init__(self, worktree: str) -> None:
        self.worktree = worktree
        super().__init__(
            f"a merge is already in progress in {worktree}: an earlier conflict "
            "was never resolved. A human must finish it -- resolve the conflict "
            f"and commit in {worktree} -- then relaunch."
        )


def _required_tip(value: object) -> str:
    """A non-blank tip ref, or `ValueError` before anything is run."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"merge_tip needs a non-empty tip ref, got {value!r}")
    return value


def _result(
    *,
    created: bool,
    conflict: bool = False,
    files: Iterable[str] = (),
    merged: str | None = None,
    already_merged: bool = False,
    detail: str = "",
) -> dict[str, object]:
    """The step's result: every key always present (spec "Result dict")."""
    return {
        "created": created,
        "conflict": conflict,
        "files": list(files),
        "merged": merged,
        "already_merged": already_merged,
        "detail": detail,
    }


def _already_contains(git_runner: GitRunner, worktree_path: str, tip: str) -> bool:
    """Whether HEAD already contains `tip`, as git measures it.

    `merge-base --is-ancestor` exits 0 for yes and 1 for no. Anything else
    (a bad ref is 128) is a real failure and propagates.
    """
    try:
        git_runner(["-C", worktree_path, "merge-base", "--is-ancestor", tip, "HEAD"])
    except GitError as error:
        if error.exit_code == 1:
            return False
        raise
    return True


def _says_already_up_to_date(output: str) -> bool:
    """Whether `git merge` reported a no-op ("Already up to date." / "up-to-date")."""
    return "already up to date" in output.lower().replace("-", " ")


def merge_tip(
    repo_dir: str | Path,
    worktree: str | Path,
    integration_branch: str,
    base_branch: str,
    tip: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Merge `tip` into `integration_branch`'s worktree with `--no-ff`, and report it.

    A tip already contained in HEAD is a no-op, so a relaunch never
    re-merges. The return value is the deterministic phase's result -- a
    plain dict, since it crosses no process boundary and so needs no Pydantic
    model (`CLAUDE.md`).
    """
    tip = _required_tip(tip)
    integration_branch = _required_name(integration_branch, "integration_branch")
    base_branch = _required_name(base_branch, "base_branch")
    worktree_path = _required_absolute(worktree, "worktree")
    repo_path = _required_absolute(repo_dir, "repo_dir")

    ensured = ensure(integration_branch, base_branch, worktree_path, repo_path, git_runner)
    created = bool(ensured["created"])

    if _already_contains(git_runner, worktree_path, tip):
        return _result(created=created, merged=tip, already_merged=True)

    # --no-edit: take git's generated message; never wait on an editor.
    output = git_runner(["-C", worktree_path, "merge", "--no-ff", "--no-edit", tip])
    if _says_already_up_to_date(output):
        return _result(created=created, merged=tip, already_merged=True)
    return _result(created=created, merged=tip)
