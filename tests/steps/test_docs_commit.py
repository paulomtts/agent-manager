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


@dataclass
class FakeCard:
    """The one field the step reads off `models.Card`.

    A stand-in rather than the real model on purpose: the step must bind to any
    object carrying a `title`, and a steps-tier test should not depend on the
    board's schema.
    """

    title: str


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo on `main` with one commit, isolated in tmp_path."""
    root = tmp_path / "repo"
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
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")
    return root


def _write_documents(root: Path) -> None:
    """The spec and the plan exactly where the `writes:` templates put them."""
    for relative, body in (
        (SPEC_RELATIVE, "# spec\n\nthe design.\n"),
        (PLAN_RELATIVE, "# plan\n\nthe steps.\n<!-- task-pipeline: validated -->\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _run(root: Path, **overrides):
    """Call the step against `root` with the standard arguments."""
    arguments = {
        "card_details": FakeCard(title=TITLE),
        "spec_path": SPEC_RELATIVE,
        "plan_path": PLAN_RELATIVE,
        "worktree": str(root),
    }
    arguments.update(overrides)
    return docs_commit.commit_documents(**arguments)


def _commit_count(root: Path) -> int:
    return len(_git(root, "rev-list", "HEAD").split())


def _message(root: Path, revision: str = "HEAD") -> str:
    return _git(root, "show", "-s", "--format=%B", revision).rstrip("\n")


def _recorder(calls: list[list[str]], inner=None):
    """A git runner that records every argv, optionally delegating to `inner`."""

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        return "" if inner is None else inner(argv)

    return runner


@requires_git
def test_the_step_makes_one_commit_whose_subject_and_trailer_are_exact(repo: Path) -> None:
    _write_documents(repo)
    before = _commit_count(repo)

    result = _run(repo)

    assert _commit_count(repo) == before + 1
    message = _message(repo)
    assert message.splitlines()[0] == f"docs: add spec and plan for {TITLE}"
    assert message.splitlines()[-1] == f"Plan-Hash: {result['plan_hash']}"


@requires_git
def test_the_returned_hash_is_the_hash_of_the_plan_file_on_disk(repo: Path) -> None:
    _write_documents(repo)

    result = _run(repo)

    expected = hashlib.sha256((repo / PLAN_RELATIVE).read_bytes()).hexdigest()[:8]
    assert result == {"plan_hash": expected}
    assert reducers.is_plan_hash(result["plan_hash"])


@requires_git
def test_only_the_two_declared_documents_are_committed(repo: Path) -> None:
    """A stray untracked file and an unrelated modified tracked file both
    survive uncommitted: the step names its two pathspecs and never sweeps."""
    _write_documents(repo)
    (repo / "stray.txt").write_text("not mine\n", encoding="utf-8")
    (repo / "README.md").write_text("edited by somebody else\n", encoding="utf-8")

    _run(repo)

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted([SPEC_RELATIVE, PLAN_RELATIVE])
    porcelain = _git(repo, "status", "--porcelain")
    assert "stray.txt" in porcelain
    assert "README.md" in porcelain


@requires_git
def test_an_unrelated_already_staged_file_is_not_swept_into_the_docs_commit(
    repo: Path,
) -> None:
    """Review focus: the index may already hold somebody else's change when the
    step runs. `git commit -- <paths>` is a partial commit, so it cannot."""
    _write_documents(repo)
    (repo / "README.md").write_text("staged by somebody else\n", encoding="utf-8")
    _git(repo, "add", "README.md")

    _run(repo)

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted([SPEC_RELATIVE, PLAN_RELATIVE])
    assert "README.md" in _git(repo, "diff", "--cached", "--name-only")


@requires_git
def test_the_step_runs_no_forbidden_git_verb(repo: Path) -> None:
    """Design §9: this step may add and commit. Sweeping (`add -A`, `add .`) or
    destroying (`reset`, `clean`, `checkout -f`) or publishing (`push`) is how a
    resumed run loses a human's work."""
    _write_documents(repo)
    calls: list[list[str]] = []

    _run(repo, git_runner=_recorder(calls, docs_commit.run_git))

    assert calls  # non-vacuity: a step that ran no git at all would pass emptily
    for argv in calls:
        assert "-A" not in argv, argv
        assert "--all" not in argv, argv
        for verb in ("reset", "clean", "push", "rm", "restore"):
            assert verb not in argv, argv
        if "add" in argv:
            assert "." not in argv, argv
            assert "*" not in " ".join(argv), argv
        if "checkout" in argv:
            assert "-f" not in argv, argv
