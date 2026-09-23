"""Commit the spec and the plan, tagged with the plan's Plan-Hash trailer.

A deterministic step (design §4 `steps/`, §6): git and the filesystem only --
no model call, no board access, no `brd`. It runs after `mark_validated` and
before `implement`, because the hash it stamps is the hash of the plan file
*with* the validated marker already on disk, which is exactly the hash `review`
recomputes independently (design §9).

Every invocation is an argument list handed to `subprocess` through the
injected `GitRunner` (the seam `steps/worktree.py` established): there is no
shell string and nothing to quote.
"""

import hashlib
import os
from pathlib import Path

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

    `engine.subtask_context` binds `card_details` to `None` when the caller
    supplied no card, so `None` must not surface as an `AttributeError`.
    """
    title = getattr(card_details, "title", None)
    if not isinstance(title, str) or title.strip() == "":
        raise ValueError(
            "docs_commit.commit_documents needs card_details carrying a non-blank "
            f"title for the commit subject, got {card_details!r}"
        )
    return title


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


def commit_documents(
    card_details: object,
    spec_path: str,
    plan_path: str,
    worktree: str | Path,
    git_runner: GitRunner = run_git,
) -> dict[str, object]:
    """Commit the spec and the plan, tagged with the plan's Plan-Hash.

    Parameter names are the engine's binding table's names, so the document's
    phase needs no `args:` at all. Returns a plain dict (design §6), stored in
    the context under the phase name.
    """
    worktree_path = _required_worktree(worktree)
    spec_path = _required_relative_path(spec_path, "spec_path")
    plan_path = _required_relative_path(plan_path, "plan_path")
    title = _required_title(card_details)

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

    # `--` and then exactly two literal pathspecs. Never `-A`, never `.`.
    git_runner(["-C", worktree_path, "add", "--", spec_path, plan_path])
    staged = git_runner(
        ["-C", worktree_path, "diff", "--cached", "--name-only", "--", spec_path, plan_path]
    )
    if staged.strip() == "":
        # The resume path (design §9): "these two paths hold no change", so a
        # plan edited between runs still earns its own commit and hash.
        if _branch_carries(git_runner, worktree_path, digest):
            return {"plan_hash": digest}
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
    return {"plan_hash": digest}
