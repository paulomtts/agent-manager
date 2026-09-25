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

`measure_merge` and `merge_completed_gate` (Integrate addendum I3) judge a
merge the resolver says it finished. They only ask git questions -- `rev-parse`,
`status`, `diff` -- and read the touched files as bytes; the resolver's own
`resolved` flag is never consulted.

Every invocation is an argument list handed to the git runner: there is no
shell string and nothing to quote.
"""

from collections.abc import Iterable
from pathlib import Path

from agent_manager.steps.worktree import (
    GitError,
    GitRunner,
    _is_registered,
    _required_absolute,
    _required_name,
    ensure,
    run_git,
    worktree_paths,
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


def _unmerged_files(git_runner: GitRunner, worktree_path: str) -> list[str]:
    """The paths git holds as unmerged in `worktree_path`, in git's order."""
    out = git_runner(["-C", worktree_path, "diff", "--name-only", "--diff-filter=U"])
    return [line.strip() for line in out.split("\n") if line.strip()]


def _first_line(text: str) -> str:
    """The first non-blank line of `text`, stripped, or "" when there is none."""
    for line in text.split("\n"):
        if line.strip():
            return line.strip()
    return ""


def _refuse_unfinished_merge(
    git_runner: GitRunner, repo_path: str, worktree_path: str
) -> None:
    """Raise `MergeInProgressError` when `worktree_path` has a merge under way.

    A worktree git does not know yet cannot hold a merge, so the probe is
    skipped and the integration branch need not exist. Registration uses the
    same test as `worktree.ensure`, so a trailing slash or `.` cannot slip
    past. `rev-parse --verify --quiet` exits 1 when MERGE_HEAD is absent; any
    other failure is not an answer and propagates.
    """
    registered = worktree_paths(
        git_runner(["-C", repo_path, "worktree", "list", "--porcelain"])
    )
    if not _is_registered(worktree_path, registered):
        return
    try:
        git_runner(
            ["-C", worktree_path, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"]
        )
    except GitError as error:
        if error.exit_code == 1:
            return
        raise
    raise MergeInProgressError(worktree_path)


def merge_tip(
    repo_dir: str | Path,
    worktree: str | Path,
    integration_branch: str,
    base_branch: str,
    tip: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Merge `tip` into `integration_branch`'s worktree with `--no-ff`, and report it.

    Refuses to start on top of an unresolved merge. A tip already contained
    in HEAD is a no-op, so a relaunch never re-merges. A content conflict is
    left in progress and reported with its files; any other git failure
    raises. The return value is the deterministic phase's result -- a plain
    dict, since it crosses no process boundary and so needs no Pydantic model
    (`CLAUDE.md`).
    """
    tip = _required_tip(tip)
    integration_branch = _required_name(integration_branch, "integration_branch")
    base_branch = _required_name(base_branch, "base_branch")
    worktree_path = _required_absolute(worktree, "worktree")
    repo_path = _required_absolute(repo_dir, "repo_dir")

    # Before anything else touches git state: never merge on top of a merge.
    _refuse_unfinished_merge(git_runner, repo_path, worktree_path)

    ensured = ensure(integration_branch, base_branch, worktree_path, repo_path, git_runner)
    created = bool(ensured["created"])

    if _already_contains(git_runner, worktree_path, tip):
        return _result(created=created, merged=tip, already_merged=True)

    try:
        # --no-edit: take git's generated message; never wait on an editor.
        output = git_runner(
            ["-C", worktree_path, "merge", "--no-ff", "--no-edit", tip]
        )
    except GitError as error:
        files = _unmerged_files(git_runner, worktree_path)
        if not files:
            raise
        # Left in progress on purpose: MERGE_HEAD and the markers are what a
        # resolver works from. Never `merge --abort`.
        return _result(
            created=created,
            conflict=True,
            files=files,
            detail=_first_line(error.message),
        )

    if _says_already_up_to_date(output):
        return _result(created=created, merged=tip, already_merged=True)
    return _result(created=created, merged=tip)


_MARKER_OPEN = b"<<<<<<< "
_MARKER_CLOSE = b">>>>>>> "
"""Conflict-marker line prefixes: seven characters and a space. `=======` alone
is a markdown or rst underline as often as a marker, so it never counts."""


def _ref_exists(git_runner: GitRunner, worktree_path: str, ref: str) -> bool:
    """Whether `ref` resolves in `worktree_path`.

    `rev-parse --verify --quiet` exits 1 when the ref is absent; any other
    failure is not an answer and propagates.
    """
    try:
        git_runner(["-C", worktree_path, "rev-parse", "--verify", "--quiet", ref])
    except GitError as error:
        if error.exit_code == 1:
            return False
        raise
    return True


def _nul_separated(output: str) -> list[str]:
    """Paths from a `-z` listing: NUL-separated and never quoted by git."""
    return [name for name in output.split("\0") if name]


def _touched_files(
    git_runner: GitRunner, worktree_path: str, merge_in_progress: bool
) -> list[str]:
    """The files the merge touched: those that differ from its first parent.

    While MERGE_HEAD exists the first parent is HEAD itself, so the set is
    what differs between HEAD and the index/working tree, plus any unmerged
    path. Once committed it is HEAD^1..HEAD. A root commit touched nothing.
    """
    if merge_in_progress:
        touched = _nul_separated(
            git_runner(["-C", worktree_path, "diff", "--name-only", "-z", "HEAD"])
        )
        unmerged = _nul_separated(
            git_runner(
                ["-C", worktree_path, "diff", "--name-only", "-z", "--diff-filter=U"]
            )
        )
        return touched + [name for name in unmerged if name not in touched]
    if not _ref_exists(git_runner, worktree_path, "HEAD^1"):
        return []
    return _nul_separated(
        git_runner(["-C", worktree_path, "diff", "--name-only", "-z", "HEAD^1", "HEAD"])
    )


def _has_conflict_markers(path: Path) -> bool:
    """Both an opening and a closing marker line, read as bytes."""
    lines = path.read_bytes().splitlines()
    return any(line.startswith(_MARKER_OPEN) for line in lines) and any(
        line.startswith(_MARKER_CLOSE) for line in lines
    )


def measure_merge(
    worktree: str | Path, git_runner: GitRunner = run_git
) -> dict[str, object]:
    """What git says about the merge in `worktree`, without changing anything.

    `merge_in_progress` is whether MERGE_HEAD exists, `status` is the raw
    `git status --porcelain` text (untracked files included), and
    `marked_files` lists the touched files that still hold both conflict
    marker lines, in git's order. A touched path that no longer exists (a
    deletion) is skipped. Every git failure other than an absent ref raises.
    """
    worktree_path = str(worktree)
    in_progress = _ref_exists(git_runner, worktree_path, "MERGE_HEAD")
    status = git_runner(
        ["-C", worktree_path, "status", "--porcelain", "--untracked-files=all"]
    )
    marked = [
        name
        for name in _touched_files(git_runner, worktree_path, in_progress)
        if (Path(worktree_path) / name).is_file()
        and _has_conflict_markers(Path(worktree_path) / name)
    ]
    return {"merge_in_progress": in_progress, "status": status, "marked_files": marked}


def _dirty_paths(status: str) -> list[str]:
    """The `git status --porcelain` entries, one per dirty path, as git printed them."""
    return [line.strip() for line in status.split("\n") if line.strip()]


def merge_completed_gate(
    result: object, worktree: str | Path, *, git_runner: GitRunner = run_git
) -> dict[str, str] | None:
    """Pass (None) only when git says the merge in `worktree` is finished.

    Finished means: no MERGE_HEAD, a clean `git status --porcelain`, and no
    touched file holding both conflict-marker lines. `result` is the
    resolver's report and is deliberately never read -- its `resolved` flag is
    advisory; git judges. Otherwise the verdict is `{"detail": ...}`, written
    as feedback for the resolver because it is appended to its retry brief.
    Any git failure propagates: it is never a pass and never a verdict.
    """
    measured = measure_merge(worktree, git_runner)
    in_progress = bool(measured["merge_in_progress"])
    marked = list(measured["marked_files"])
    dirty = _dirty_paths(str(measured["status"]))
    if not in_progress and not marked and not dirty:
        return None

    lead = "The merge is not complete"
    if in_progress:
        sentences = [
            f"{lead}: a merge is still in progress (MERGE_HEAD exists); commit it."
        ]
    else:
        sentences = [f"{lead}."]
    if marked:
        sentences.append(f"Conflict markers remain in: {', '.join(marked)}.")
    if dirty:
        sentences.append(f"The working tree is not clean: {'; '.join(dirty)}.")
    return {"detail": " ".join(sentences)}
