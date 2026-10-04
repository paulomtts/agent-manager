"""Commit the spec and the plan, tagged with the plan's Plan-Hash trailer.

Documents git ignores in the worktree are hashed but never added or committed.

A deterministic step (design §4 `steps/`, §6): git and the filesystem only --
no model call, no board access, no `brd`. It runs after `mark_validated` and
before `implement`, because the hash it stamps is the hash of the plan file
*with* the validated marker already on disk, which is exactly the hash `review`
recomputes independently (design §9).

Before committing, it backfills: any commit on the unmerged task branch that
carries no Plan-Hash trailer (a draft a role committed despite being told not
to) is rewritten to carry the current one, so `review` does not read it as
debris. The rewrite rebuilds raw commit objects -- same tree, same author and
committer lines -- and moves the branch once with a compare-and-swap
`update-ref`. Nothing is reset, checked out or rebased, so the index and the
working tree never notice.

Every invocation is an argument list handed to `subprocess` through the
injected `GitRunner` (the seam `steps/worktree.py` established): there is no
shell string and nothing to quote.
"""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from agent_manager.steps import reducers
from agent_manager.steps.worktree import GitError, GitRunner, run_git

_HASH_LENGTH = 8
"""How many characters of the digest a Plan-Hash is.

`reducers.is_plan_hash` accepts exactly 8 lowercase hex characters and nothing
else; this constant is that contract's other half and must never drift from it.
"""


def plan_hash(content: bytes) -> str:
    """The first 8 lowercase hex characters of `content`'s SHA-256.

    Pure, and the single definition of the hash in this module: the step reads
    the plan's bytes off disk and hands them here, so a test can pin the value
    without a repository.
    """
    return hashlib.sha256(content).hexdigest()[:_HASH_LENGTH]


SUBJECT_TEMPLATE = "docs: add spec and plan for {title}"
"""The docs commit's subject line. One template, so the step that writes it and
any future reader that greps for it cannot disagree about the wording."""

TRAILER_PREFIX = "Plan-Hash: "
"""The trailer `review` and `reducers.review_gate` look for, verbatim.

The trailing space is part of it: `Plan-Hash:a1b2c3d4` is not a git trailer and
would not be counted by anything downstream.
"""


_TRAILER_LINE = re.compile(r"[A-Za-z0-9-]+: ")
"""A git trailer line's shape: `Token: value`, matched at the line start."""

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
"""A blank (or whitespace-only) line between two paragraphs of a message."""


def with_trailer(message: str, digest: str) -> str:
    """`message` with `Plan-Hash: <digest>` appended as a git trailer.

    Pure. Trailing newlines are stripped first. If the message has more than
    one paragraph and its last paragraph is a trailer block (every line
    `Token: value`, e.g. `Co-Authored-By: ...`), the trailer joins that block;
    otherwise it starts a new paragraph. The result ends with one newline, and
    the trailer sits at column 0 of its own line so review's anchored
    `^Plan-Hash: <hash>` grep counts it.
    """
    body = message.rstrip("\n")
    paragraphs = _PARAGRAPH_BREAK.split(body)
    last = paragraphs[-1].split("\n")
    joins_block = len(paragraphs) > 1 and all(
        _TRAILER_LINE.match(line) for line in last
    )
    separator = "\n" if joins_block else "\n\n"
    return f"{body}{separator}{TRAILER_PREFIX}{digest}\n"


class UntaggedDocumentsError(RuntimeError):
    """The documents are committed, but no commit on the branch carries this hash.

    Raised rather than returning the hash: `review` counts commits whose message
    holds a `Plan-Hash` trailer, so answering with a hash nothing corroborates
    would make a resumed run read a branch as tagged when it is debris
    (design §9).
    """

    def __init__(self, *, plan_hash: str, spec_path: str, plan_path: str) -> None:
        self.plan_hash = plan_hash
        self.spec_path = spec_path
        self.plan_path = plan_path
        super().__init__(
            f"{spec_path} and {plan_path} have nothing to commit, but no commit on "
            f"this branch carries the trailer {TRAILER_PREFIX}{plan_hash}. The "
            "documents are tracked and untagged, so a resumed run would read them "
            "as debris. Commit them with the trailer, or remove them, by hand."
        )


class PartlyIgnoredDocumentsError(RuntimeError):
    """Git ignores one of the two documents and not the other."""

    def __init__(self, *, ignored: str, tracked: str) -> None:
        self.ignored = ignored
        self.tracked = tracked
        super().__init__(
            f"git ignores {ignored} but not {tracked}. The spec and the plan are "
            "committed together or not at all: ignore both paths, or neither."
        )


def _branch_carries(git_runner: GitRunner, worktree_path: str, digest: str) -> bool:
    """Whether any commit reachable from HEAD carries exactly this trailer.

    Whole-line equality on the stripped line, never substring containment.
    A `GitError` (an empty repository has no `HEAD` to log) answers `False`.
    """
    try:
        log = git_runner(["-C", worktree_path, "log", "--format=%B"])
    except GitError:
        return False
    wanted = f"{TRAILER_PREFIX}{digest}"
    return any(line.strip() == wanted for line in log.splitlines())


def _is_ignored(git_runner: GitRunner, worktree_path: str, path: str) -> bool:
    """Whether git ignores `path` in this worktree (`git check-ignore`).

    Exit 1 answers `False`; any other failure propagates as `GitError`.
    A tracked path is never ignored.
    """
    try:
        git_runner(["-C", worktree_path, "check-ignore", "-q", "--", path])
    except GitError as error:
        if error.exit_code == 1:
            return False
        raise
    return True


def _required_relative_path(value: object, field: str) -> str:
    """A non-blank relative document path, or `ValueError` before any git call."""
    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"docs_commit.commit_documents needs a path string for {field}, "
            f"got {value!r}"
        )
    text = str(value).strip()
    if text == "":
        raise ValueError(
            f"docs_commit.commit_documents needs a non-empty {field}, got {value!r}"
        )
    if Path(text).is_absolute():
        raise ValueError(
            f"docs_commit.commit_documents needs a worktree-relative {field}, but "
            f"{text!r} is absolute; `git add --` would stage a file outside the run"
        )
    return text


def _required_worktree(value: object) -> str:
    """An existing absolute worktree directory, or `ValueError` up front."""
    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"docs_commit.commit_documents needs an absolute worktree path, "
            f"got {value!r}"
        )
    text = str(value).strip()
    if text == "" or not Path(text).is_absolute() or not Path(text).is_dir():
        raise ValueError(
            f"docs_commit.commit_documents needs an existing absolute worktree "
            f"directory, got {value!r}"
        )
    return text


def _required_title(card_details: object) -> str:
    """The card's non-blank title, or `ValueError` before any git call.

    `walk.subtask_context` binds `card_details` to `None` when the caller
    supplied no card, so `None` must not surface as an `AttributeError`.
    """
    title = getattr(card_details, "title", None)
    if not isinstance(title, str) or title.strip() == "":
        raise ValueError(
            "docs_commit.commit_documents needs card_details carrying a non-blank "
            f"title for the commit subject, got {card_details!r}"
        )
    return title


def _required_base_branch(value: object) -> str:
    """The non-blank base branch name, or `ValueError` before any git call."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(
            f"docs_commit.commit_documents needs a non-empty base_branch, got {value!r}"
        )
    return value.strip()


def _inside(root: str, candidate: Path, field: str) -> Path:
    """`candidate`, proven to live under `root`, or `ValueError`.

    `realpath` on both sides so a symlinked tmp directory is not rejected.
    """
    real_root = Path(os.path.realpath(root))
    real_candidate = Path(os.path.realpath(candidate))
    if real_root != real_candidate and real_root not in real_candidate.parents:
        raise ValueError(
            f"docs_commit.commit_documents refuses {field} {str(candidate)!r}: it "
            f"resolves to {str(real_candidate)!r}, which is outside the worktree "
            f"{root!r}"
        )
    return real_candidate


def _document_paths(worktree_path: str, spec_path: str, plan_path: str) -> tuple[Path, Path]:
    """The two documents as absolute paths under the worktree."""
    root = Path(worktree_path)
    return root / spec_path, root / plan_path


def _is_stamped(message: str) -> bool:
    """Whether some line of `message` is `Plan-Hash: ` plus a well-formed hash.

    Any valid hash counts, a stale one included: such commits are left alone.
    `Plan-Hash: zzz` does not count, exactly as review's grep would not.
    """
    return any(
        line.startswith(TRAILER_PREFIX)
        and reducers.is_plan_hash(line[len(TRAILER_PREFIX) :].rstrip())
        for line in message.split("\n")
    )


def _split_commit(raw: str) -> tuple[list[str], str]:
    """A raw `cat-file commit` object as (header lines, message)."""
    headers, _, message = raw.partition("\n\n")
    return headers.split("\n"), message


_SIGNATURE_HEADERS = ("gpgsig", "gpgsig-sha256")
"""Commit headers holding a signature, which a rewrite would invalidate."""


def _rebuild_commit(headers: list[str], parent: str, message: str) -> str:
    """The raw text of a commit object: `headers` with the parent replaced.

    Every other header line -- `tree`, `author`, `committer` -- is carried
    over verbatim, which is how name, email, timestamp and timezone survive.
    A signature header and its continuation lines (those starting with a
    space) are dropped: the rewritten commit is unsigned, as after a rebase.
    """
    kept: list[str] = []
    dropping = False
    for line in headers:
        if line.startswith(" "):
            if not dropping:
                kept.append(line)
            continue
        key = line.split(" ", 1)[0]
        dropping = key in _SIGNATURE_HEADERS
        if dropping:
            continue
        kept.append(f"parent {parent}" if key == "parent" else line)
    return "\n".join(kept) + "\n\n" + message


def _resolves(git_runner: GitRunner, worktree_path: str, revision: str) -> bool:
    """Whether `revision` names a commit in this repository."""
    try:
        git_runner(
            ["-C", worktree_path, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"]
        )
    except GitError:
        return False
    return True


def _backfill(
    git_runner: GitRunner, worktree_path: str, base_branch: str, digest: str
) -> list[str]:
    """Stamp every unstamped commit in `base_branch..HEAD` with `digest`.

    Returns the pre-rewrite shas of the commits that gained the trailer,
    oldest first. Commits before the first unstamped one keep their shas;
    from there on each is recreated on its recreated parent. The branch moves
    once, as a compare-and-swap against the tip read here.
    """
    if not _resolves(git_runner, worktree_path, base_branch):
        # Today's behaviour; if review's range cannot resolve either, review
        # reports that, not this step.
        return []
    try:
        ref = git_runner(["-C", worktree_path, "symbolic-ref", "-q", "HEAD"]).strip()
    except GitError:
        # Detached HEAD: there is no branch to rewrite by name.
        return []
    old_tip = git_runner(
        ["-C", worktree_path, "rev-parse", "--verify", "HEAD^{commit}"]
    ).strip()
    # `origin/<base>` too: `worktree.ensure` may have branched from it, and a
    # local base behind its remote must not put upstream commits in range.
    excluded = [f"^{base_branch}"]
    if _resolves(git_runner, worktree_path, f"origin/{base_branch}"):
        excluded.append(f"^origin/{base_branch}")
    listing = git_runner(
        [
            "-C",
            worktree_path,
            "rev-list",
            "--reverse",
            "--topo-order",
            "--parents",
            old_tip,
            *excluded,
            "--",
        ]
    )
    rows = [line.split() for line in listing.splitlines() if line.strip()]
    if any(len(row) != 2 for row in rows):
        # A merge (several parents) or a root (none): out of scope, and
        # review_gate's "only N of M commits" message already covers it.
        return []
    commits = [
        (row[0], git_runner(["-C", worktree_path, "cat-file", "commit", row[0]]))
        for row in rows
    ]
    first = next(
        (
            index
            for index, (_, raw) in enumerate(commits)
            if not _is_stamped(_split_commit(raw)[1])
        ),
        None,
    )
    if first is None:
        return []

    parent = rows[first][1]
    backfilled: list[str] = []
    # Outside the worktree: an extra file there would fail review's porcelain
    # check. `GitRunner` has no stdin, so `hash-object` reads a file.
    with tempfile.TemporaryDirectory(prefix="agent-manager-docs-commit-") as scratch:
        object_file = Path(scratch) / "commit"
        for sha, raw in commits[first:]:
            headers, message = _split_commit(raw)
            if not _is_stamped(message):
                message = with_trailer(message, digest)
                backfilled.append(sha)
            object_file.write_bytes(_rebuild_commit(headers, parent, message).encode("utf-8"))
            parent = git_runner(
                ["-C", worktree_path, "hash-object", "-t", "commit", "-w", str(object_file)]
            ).strip()

    git_runner(
        [
            "-C",
            worktree_path,
            "update-ref",
            "-m",
            f"docs_commit: backfill Plan-Hash {digest}",
            ref,
            parent,
            old_tip,
        ]
    )
    return backfilled


def commit_documents(
    card_details: object,
    spec_path: str,
    plan_path: str,
    worktree: str | Path,
    base_branch: str,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Commit the spec and the plan, tagged with the plan's Plan-Hash.

    Parameter names are the engine's binding table's names, so the document's
    phase needs no `args:` at all. Returns a plain dict (design §6), stored in
    the context under the phase name: `plan_hash`, the hash of the plan file
    on disk; `backfilled`, the pre-rewrite shas the backfill stamped; and
    `documents_committed`.

    When git ignores both documents in the worktree, nothing is added or
    committed and `documents_committed` is `False`; the hash and the backfill
    are unchanged. Otherwise the documents are committed (or found already
    committed under this hash) and it is `True`. Raises
    `PartlyIgnoredDocumentsError`, before any write, when git ignores exactly
    one of them, and `UntaggedDocumentsError` when tracked documents have
    nothing to commit and no branch commit carries the hash.
    """
    worktree_path = _required_worktree(worktree)
    spec_path = _required_relative_path(spec_path, "spec_path")
    plan_path = _required_relative_path(plan_path, "plan_path")
    title = _required_title(card_details)
    base_branch = _required_base_branch(base_branch)

    spec_file, plan_file = _document_paths(worktree_path, spec_path, plan_path)
    spec_file = _inside(worktree_path, spec_file, "spec_path")
    plan_file = _inside(worktree_path, plan_file, "plan_path")
    for field, path in (("spec_path", spec_file), ("plan_path", plan_file)):
        if not path.is_file():
            raise ValueError(
                f"docs_commit.commit_documents cannot find the {field} document at "
                f"{str(path)!r}; the `{field.removesuffix('_path')}` phase was "
                "supposed to write it"
            )

    digest = plan_hash(plan_file.read_bytes())
    spec_ignored = _is_ignored(git_runner, worktree_path, spec_path)
    plan_ignored = _is_ignored(git_runner, worktree_path, plan_path)
    if spec_ignored != plan_ignored:
        ignored, tracked = (spec_path, plan_path) if spec_ignored else (plan_path, spec_path)
        raise PartlyIgnoredDocumentsError(ignored=ignored, tracked=tracked)
    # Before the add/commit below: when a role already committed the documents
    # themselves, nothing is staged and `_branch_carries` decides -- which it
    # can only answer yes to once those drafts carry the trailer.
    backfilled = _backfill(git_runner, worktree_path, base_branch, digest)

    if spec_ignored:
        return {"plan_hash": digest, "backfilled": backfilled, "documents_committed": False}

    # `--` and then exactly two literal pathspecs. Never `-A`, never `.`.
    git_runner(["-C", worktree_path, "add", "--", spec_path, plan_path])
    staged = git_runner(
        ["-C", worktree_path, "diff", "--cached", "--name-only", "--", spec_path, plan_path]
    )
    if staged.strip() == "":
        # The resume path (design §9): "these two paths hold no change", so a
        # plan edited between runs still earns its own commit and hash.
        if _branch_carries(git_runner, worktree_path, digest):
            return {
                "plan_hash": digest,
                "backfilled": backfilled,
                "documents_committed": True,
            }
        raise UntaggedDocumentsError(
            plan_hash=digest, spec_path=spec_path, plan_path=plan_path
        )

    # The pathspec on `commit` too, so an unrelated change already in the index
    # is left out of this commit (a partial commit).
    git_runner(
        [
            "-C",
            worktree_path,
            "commit",
            "-m",
            SUBJECT_TEMPLATE.format(title=title),
            "-m",
            f"{TRAILER_PREFIX}{digest}",
            "--",
            spec_path,
            plan_path,
        ]
    )
    return {"plan_hash": digest, "backfilled": backfilled, "documents_committed": True}
