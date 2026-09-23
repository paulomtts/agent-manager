"""Behaviour of the docs-commit step (design §4 `steps/`, spec card ba15da20).

Placement follows design §14: `plan_hash` is a pure function and is unit-tested
here directly, while `commit_documents` is a Steps component and is exercised
against a real temporary git repository under `tmp_path` -- no network, and git
is faked only where a test must observe argv that a real run would also produce.
"""

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_manager.steps import docs_commit, reducers

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the docs-commit step's steps-tier tests",
)

SPEC_RELATIVE = "docs/superpowers/specs/task-commit-the-spec-and-ba15da20.md"
PLAN_RELATIVE = "docs/superpowers/plans/task-commit-the-spec-and-ba15da20.md"
TITLE = "Commit the spec and plan with the Plan-Hash trailer"


def test_plan_hash_is_the_first_eight_hex_of_the_sha256() -> None:
    content = b"# plan\n\n<!-- task-pipeline: validated -->\n"
    assert docs_commit.plan_hash(content) == hashlib.sha256(content).hexdigest()[:8]


def test_plan_hash_has_the_shape_the_reducer_accepts() -> None:
    digest = docs_commit.plan_hash(b"anything at all")
    assert len(digest) == 8
    assert digest == digest.lower()
    assert reducers.is_plan_hash(digest)


def test_plan_hash_changes_when_one_byte_of_the_plan_changes() -> None:
    """The validated marker is part of the hashed bytes, which is why the phase
    has to run AFTER `mark_validated` -- a hash taken before the marker would
    never match what `review` recomputes."""
    before = b"# plan\n\nbody\n"
    after = b"# plan\n\nbody\n<!-- task-pipeline: validated -->\n"
    assert docs_commit.plan_hash(before) != docs_commit.plan_hash(after)
    assert docs_commit.plan_hash(b"a") != docs_commit.plan_hash(b"b")
