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
from pathlib import Path

from agent_manager.steps.worktree import GitRunner, run_git

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
    worktree_path = str(worktree)
    title = getattr(card_details, "title", None)
    spec_file, plan_file = _document_paths(worktree_path, spec_path, plan_path)

    digest = plan_hash(plan_file.read_bytes())

    # `--` and then exactly two literal pathspecs. Never `-A`, never `.`.
    git_runner(["-C", worktree_path, "add", "--", spec_path, plan_path])
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
