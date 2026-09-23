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
