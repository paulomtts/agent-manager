"""Behaviour of the docs-commit step (design §4 `steps/`, spec card ba15da20).

Placement follows design §14: `plan_hash` is a pure function and is unit-tested
here directly, while `commit_documents` is a Steps component and is exercised
against a real temporary git repository under `tmp_path` -- no network, and git
is faked only where a test must observe argv that a real run would also produce.
"""

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_manager.steps import docs_commit, reducers

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


TRAILER_DIGEST = "a1b2c3d4"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param("wip", "wip\n\nPlan-Hash: a1b2c3d4\n", id="subject-only"),
        pytest.param(
            "subject\n\nbody line one\nbody line two\n",
            "subject\n\nbody line one\nbody line two\n\nPlan-Hash: a1b2c3d4\n",
            id="subject-and-body",
        ),
        pytest.param(
            "subject\n\nbody\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
            "subject\n\nbody\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
            "Plan-Hash: a1b2c3d4\n",
            id="co-authored-block",
        ),
        pytest.param(
            "subject\n\nbody\n\n\n\n",
            "subject\n\nbody\n\nPlan-Hash: a1b2c3d4\n",
            id="extra-trailing-newlines",
        ),
        pytest.param(
            "subject\n\nNote this: the colon is mid-line prose\n",
            "subject\n\nNote this: the colon is mid-line prose\n\nPlan-Hash: a1b2c3d4\n",
            id="prose-with-a-colon",
        ),
        pytest.param(
            "fix: the thing",
            "fix: the thing\n\nPlan-Hash: a1b2c3d4\n",
            id="subject-that-looks-like-a-trailer",
        ),
    ],
)
def test_trailer_rule(message: str, expected: str) -> None:
    """Pure: (message, digest) -> message. A trailer block is the LAST paragraph
    of a multi-paragraph message whose every line is `Token: value`; the new
    trailer joins it. Anything else gets a blank line first. A lone subject is
    never a trailer block, however much it looks like one."""
    assert docs_commit.with_trailer(message, TRAILER_DIGEST) == expected


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


def test_the_step_makes_one_commit_whose_subject_and_trailer_are_exact(repo: Path) -> None:
    _write_documents(repo)
    before = _commit_count(repo)

    result = _run(repo)

    assert _commit_count(repo) == before + 1
    message = _message(repo)
    assert message.splitlines()[0] == f"docs: add spec and plan for {TITLE}"
    assert message.splitlines()[-1] == f"Plan-Hash: {result['plan_hash']}"


def test_the_returned_hash_is_the_hash_of_the_plan_file_on_disk(repo: Path) -> None:
    _write_documents(repo)

    result = _run(repo)

    expected = hashlib.sha256((repo / PLAN_RELATIVE).read_bytes()).hexdigest()[:8]
    assert result == {"plan_hash": expected}
    assert reducers.is_plan_hash(result["plan_hash"])


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


def test_a_second_call_commits_nothing_and_returns_the_same_hash(repo: Path) -> None:
    """Design §9 resume: re-running the phase after a kill is a no-op, and the
    commit count does not grow."""
    _write_documents(repo)
    first = _run(repo)
    after_first = _commit_count(repo)

    second = _run(repo)

    assert second == first
    assert _commit_count(repo) == after_first


def test_a_plan_edited_between_runs_gets_its_own_commit_with_the_new_hash(
    repo: Path,
) -> None:
    """Review focus: the resume path is "nothing staged", not "ran before". A
    plan whose bytes changed has a different hash and must be committed again,
    or every trailer on the branch would be stale."""
    _write_documents(repo)
    first = _run(repo)
    (repo / PLAN_RELATIVE).write_text("# plan\n\nrewritten.\n", encoding="utf-8")
    after_first = _commit_count(repo)

    second = _run(repo)

    assert second["plan_hash"] != first["plan_hash"]
    assert _commit_count(repo) == after_first + 1
    assert _message(repo).splitlines()[-1] == f"Plan-Hash: {second['plan_hash']}"


def test_documents_committed_without_a_trailer_raise_instead_of_lying(
    repo: Path,
) -> None:
    """Nothing to commit AND no commit carrying this hash is not a silent
    success: `review`'s untagged-commit branch would be reading a lie."""
    _write_documents(repo)
    _git(repo, "add", "--", SPEC_RELATIVE, PLAN_RELATIVE)
    _git(repo, "commit", "-m", "docs: committed by a human, untagged")
    before = _commit_count(repo)

    with pytest.raises(docs_commit.UntaggedDocumentsError) as caught:
        _run(repo)

    expected = hashlib.sha256((repo / PLAN_RELATIVE).read_bytes()).hexdigest()[:8]
    assert caught.value.plan_hash == expected
    message = str(caught.value)
    assert expected in message
    assert SPEC_RELATIVE in message
    assert PLAN_RELATIVE in message
    assert _commit_count(repo) == before


def _exploding_runner(argv: list[str]) -> str:
    raise AssertionError(f"pre-flight must run no git command, but ran: {argv!r}")


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        (
            {"plan_path": "docs/superpowers/plans/absent.md"},
            "cannot find the plan_path document",
        ),
        (
            {"spec_path": "docs/superpowers/specs/absent.md"},
            "cannot find the spec_path document",
        ),
        ({"spec_path": ""}, "needs a non-empty spec_path"),
        ({"plan_path": "   "}, "needs a non-empty plan_path"),
        ({"plan_path": None}, "needs a path string for plan_path"),
        ({"spec_path": "/etc/passwd"}, "needs a worktree-relative spec_path"),
        (
            {"plan_path": "/tmp/absolute-plan.md"},
            "needs a worktree-relative plan_path",
        ),
        (
            {"worktree": "relative/worktree"},
            "needs an existing absolute worktree directory",
        ),
        (
            {"worktree": "/nonexistent/agent-manager-docs-commit"},
            "needs an existing absolute worktree directory",
        ),
        ({"card_details": None}, "needs card_details carrying a non-blank"),
        ({"card_details": FakeCard(title="  ")}, "needs card_details carrying a non-blank"),
        ({"spec_path": "../escape.md"}, "outside the worktree"),
    ],
)
def test_a_bad_argument_raises_value_error_before_any_git_runs(
    repo: Path, overrides: dict, needle: str
) -> None:
    """Each needle is the offending guard's OWN wording, never a substring that
    a later guard would also produce. A loose needle here is how a deleted
    pre-flight check passes: with `worktree` unvalidated, for instance, the
    missing-document error still says "worktree" somewhere in the path it
    prints, so "worktree" alone would never have caught the deletion.
    """
    _write_documents(repo)

    with pytest.raises(ValueError) as caught:
        _run(repo, git_runner=_exploding_runner, **overrides)

    assert needle in str(caught.value)


def test_a_document_path_resolving_outside_the_worktree_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    """Review focus: `prompt.expand_writes` already refuses `..`, and the step
    does not trust that twice over. The escape target EXISTS here, so the
    missing-document guard cannot fire and only the containment check can
    refuse it -- without it, `git add -- ../escape.md` would stage a file in
    the parent repository.
    """
    _write_documents(repo)
    outside = tmp_path / "escape.md"
    outside.write_text("# not in the worktree\n", encoding="utf-8")
    assert outside.is_file() and not outside.is_relative_to(repo)

    with pytest.raises(ValueError) as caught:
        _run(repo, git_runner=_exploding_runner, spec_path="../escape.md")

    message = str(caught.value)
    assert "outside the worktree" in message
    assert str(outside) in message


def test_a_worktree_reached_through_a_symlink_still_works(
    repo: Path, tmp_path: Path
) -> None:
    """Review focus: `tmp_path` (and `/tmp` on macOS) can sit behind a symlink,
    so containment has to be decided on `realpath`, not on the literal string."""
    _write_documents(repo)
    link = tmp_path / "link-to-repo"
    link.symlink_to(repo, target_is_directory=True)

    result = _run(repo, worktree=str(link))

    assert reducers.is_plan_hash(result["plan_hash"])
    assert _commit_count(repo) == 2
