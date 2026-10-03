"""Behaviour of the docs-commit step (design §4 `steps/`, spec card ba15da20).

Placement follows design §14: `plan_hash` is a pure function and is unit-tested
here directly, while `commit_documents` is a Steps component and is exercised
against a real temporary git repository under `tmp_path` -- no network, and git
is faked only where a test must observe argv that a real run would also produce.
"""

import hashlib
import os
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
    """Call the step against `root` with the standard arguments.

    `base_branch` is `main`: every test that never leaves `main` has an empty
    backfill range, so the backfill is a no-op there.
    """
    arguments = {
        "card_details": FakeCard(title=TITLE),
        "spec_path": SPEC_RELATIVE,
        "plan_path": PLAN_RELATIVE,
        "worktree": str(root),
        "base_branch": "main",
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


OTHER_HASH = "0123abcd"
"""A valid Plan-Hash that is not the plan's: a commit stamped by an earlier plan."""

DRAFT_AUTHOR = "Draft Author <draft@example.com>"


def _task_branch(root: Path) -> None:
    """Create and check out `task` from `main`."""
    _git(root, "checkout", "-q", "-b", "task", "main")


def _commit_file(
    root: Path,
    name: str,
    message: str,
    *,
    author: str | None = None,
    author_date: str | None = None,
    committer_date: str | None = None,
) -> str:
    """Write `name`, commit it alone with `message`, return the new full sha.

    Fixed dates in a foreign timezone make "the rewrite kept the dates" a real
    check: a rewrite that stamped "now" would differ in every field.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{name}: {message}\n", encoding="utf-8")
    _git(root, "add", "--", name)
    env = dict(os.environ)
    if author_date is not None:
        env["GIT_AUTHOR_DATE"] = author_date
    if committer_date is not None:
        env["GIT_COMMITTER_DATE"] = committer_date
    argv = ["git", "-C", str(root), "commit", "-q", "-m", message]
    if author is not None:
        argv += ["--author", author]
    subprocess.run(argv, capture_output=True, text=True, check=True, env=env)
    return _rev(root, "HEAD")


def _rev(root: Path, revision: str) -> str:
    return _git(root, "rev-parse", "--verify", revision).strip()


def _range(root: Path, tip: str = "HEAD") -> list[str]:
    """`main..tip`, oldest first."""
    return _git(root, "rev-list", "--reverse", f"main..{tip}").split()


def _digest(root: Path) -> str:
    return hashlib.sha256((root / PLAN_RELATIVE).read_bytes()).hexdigest()[:8]


def _identity(root: Path, revision: str) -> str:
    """Author and committer name, email and raw date (with timezone)."""
    return _git(
        root, "show", "-s", "--date=raw", "--format=%an%n%ae%n%ad%n%cn%n%ce%n%cd", revision
    )


def _drafts_scenario(root: Path) -> dict[str, str]:
    """`task` off `main`: unstamped draft A (ending in a Co-Authored-By block),
    B stamped with another valid hash, unstamped draft C. The documents are
    written but not committed. Returns the original shas."""
    _task_branch(root)
    a = _commit_file(
        root,
        "drafts/a.md",
        "draft spec\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        author=DRAFT_AUTHOR,
        author_date="2001-02-03T04:05:06+0530",
        committer_date="2002-03-04T05:06:07-0700",
    )
    b = _commit_file(
        root,
        "drafts/b.md",
        f"stamped by an earlier plan\n\nPlan-Hash: {OTHER_HASH}",
        author_date="2003-04-05T06:07:08+0100",
        committer_date="2004-05-06T07:08:09+0000",
    )
    c = _commit_file(
        root,
        "drafts/c.md",
        "draft plan",
        author_date="2005-06-07T08:09:10-0300",
        committer_date="2006-07-08T09:10:11+0900",
    )
    _write_documents(root)
    return {"A": a, "B": b, "C": c}


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
    assert result == {"plan_hash": expected, "backfilled": []}
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


@pytest.mark.parametrize("scenario", ["documents_only", "unstamped_drafts"])
def test_the_step_runs_no_forbidden_git_verb(repo: Path, scenario: str) -> None:
    """Design §9: this step may add and commit. Sweeping (`add -A`, `add .`) or
    destroying (`reset`, `clean`, `checkout -f`) or publishing (`push`) is how a
    resumed run loses a human's work. The backfill rewrites history, so it
    must do that without `rebase`, `filter-branch`, `cherry-pick` or
    `commit --amend` too."""
    if scenario == "unstamped_drafts":
        _drafts_scenario(repo)
    else:
        _write_documents(repo)
    calls: list[list[str]] = []

    _run(repo, git_runner=_recorder(calls, docs_commit.run_git))

    assert calls  # non-vacuity: a step that ran no git at all would pass emptily
    if scenario == "unstamped_drafts":
        # non-vacuity: the backfill's own argv went through the guard below
        assert any("update-ref" in argv for argv in calls), calls
    for argv in calls:
        assert "-A" not in argv, argv
        assert "--all" not in argv, argv
        assert "--amend" not in argv, argv
        for verb in (
            "reset",
            "clean",
            "push",
            "rm",
            "restore",
            "rebase",
            "filter-branch",
            "cherry-pick",
        ):
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
        ({"base_branch": ""}, "needs a non-empty base_branch"),
        ({"base_branch": "   "}, "needs a non-empty base_branch"),
        ({"base_branch": None}, "needs a non-empty base_branch"),
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


def test_unstamped_drafts_gain_the_current_hash_and_the_stamped_one_is_untouched(
    repo: Path,
) -> None:
    """The Milestone 14/17 hand backfill, made deterministic: A and C gain the
    current trailer; B keeps its own message byte-for-byte; trees, authors,
    committers and dates all survive; `main` never moves."""
    original = _drafts_scenario(repo)
    main_before = _rev(repo, "main")
    old_tip = _rev(repo, "HEAD")
    before_messages = {
        key: _git(repo, "show", "-s", "--format=%B", sha) for key, sha in original.items()
    }
    before_identity = {key: _identity(repo, sha) for key, sha in original.items()}
    digest = _digest(repo)

    result = _run(repo)

    assert result == {"plan_hash": digest, "backfilled": [original["A"], original["C"]]}
    a_new, b_new, c_new, docs = _range(repo)
    rewritten = {"A": a_new, "B": b_new, "C": c_new}
    for key in ("A", "C"):
        message = _git(repo, "show", "-s", "--format=%B", rewritten[key])
        assert message.rstrip("\n").endswith(f"Plan-Hash: {digest}"), message
        parsed = _git(
            repo, "show", "-s", "--format=%(trailers:key=Plan-Hash,valueonly)", rewritten[key]
        )
        assert parsed.strip() == digest
    co_authored = _git(
        repo, "show", "-s", "--format=%(trailers:key=Co-Authored-By,valueonly)", a_new
    )
    assert co_authored.strip() == "Claude <noreply@anthropic.com>"
    b_message = _git(repo, "show", "-s", "--format=%B", b_new)
    assert b_message == before_messages["B"]
    assert digest not in b_message
    assert b_new != original["B"]  # its parent changed, so its sha did too
    assert _git(repo, "diff", old_tip, c_new) == ""
    for key, sha in rewritten.items():
        assert _identity(repo, sha) == before_identity[key], key
    assert _message(repo, docs).splitlines()[0] == f"docs: add spec and plan for {TITLE}"
    assert _message(repo, docs).splitlines()[-1] == f"Plan-Hash: {digest}"
    assert _rev(repo, "main") == main_before
    # Review's own count: every commit on the branch now carries the trailer.
    log = _git(repo, "log", "main..HEAD", "--format=%B")
    assert sum(line == f"Plan-Hash: {digest}" for line in log.splitlines()) == 3


def test_a_branch_with_nothing_unstamped_is_left_exactly_as_it_was(repo: Path) -> None:
    _task_branch(repo)
    _write_documents(repo)
    digest = _digest(repo)
    _commit_file(repo, "a.txt", f"current\n\nPlan-Hash: {digest}")
    _commit_file(repo, "b.txt", f"earlier\n\nPlan-Hash: {OTHER_HASH}")
    old_tip = _rev(repo, "task")
    before = _range(repo)

    result = _run(repo)

    assert result["backfilled"] == []
    assert _rev(repo, "HEAD~1") == old_tip
    assert _range(repo, "HEAD~1") == before


def test_commits_below_the_first_unstamped_keep_their_shas(repo: Path) -> None:
    _task_branch(repo)
    stamped = _commit_file(repo, "s.txt", f"stamped\n\nPlan-Hash: {OTHER_HASH}")
    unstamped = _commit_file(repo, "u.txt", "unstamped")
    _write_documents(repo)

    result = _run(repo)

    assert result["backfilled"] == [unstamped]
    s_new, u_new, _docs = _range(repo)
    assert s_new == stamped
    assert u_new != unstamped


def test_drafts_that_already_hold_the_documents_are_stamped_instead_of_raising(
    repo: Path,
) -> None:
    """The Milestone 14/17 scenario: a role committed the final spec and plan
    itself. Nothing is staged, so `_branch_carries` decides -- and after the
    backfill it finds the trailer instead of raising UntaggedDocumentsError."""
    _task_branch(repo)
    _write_documents(repo)
    _git(repo, "add", "--", SPEC_RELATIVE, PLAN_RELATIVE)
    _git(repo, "commit", "-q", "-m", "docs: drafts committed by the role")
    draft = _rev(repo, "HEAD")
    before = _commit_count(repo)

    result = _run(repo)

    assert result == {"plan_hash": _digest(repo), "backfilled": [draft]}
    assert _commit_count(repo) == before
    assert _message(repo).splitlines()[-1] == f"Plan-Hash: {_digest(repo)}"


def test_the_backfill_leaves_index_and_worktree_as_they_were(repo: Path) -> None:
    """Identical trees mean the index and the working tree cannot tell the
    rewrite happened: a staged file stays staged (and out of the docs commit),
    an unstaged edit stays unstaged, and no temp file lands in the worktree."""
    _task_branch(repo)
    unstamped = _commit_file(repo, "notes.txt", "unstamped draft")
    _write_documents(repo)
    (repo / "README.md").write_text("staged by somebody else\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    (repo / "notes.txt").write_text("edited, not staged\n", encoding="utf-8")
    documents = {f"?? {SPEC_RELATIVE}", f"?? {PLAN_RELATIVE}"}
    before = set(_git(repo, "status", "--porcelain", "--untracked-files=all").splitlines())
    assert documents <= before

    result = _run(repo)

    assert result["backfilled"] == [unstamped]
    after = set(_git(repo, "status", "--porcelain", "--untracked-files=all").splitlines())
    assert after == before - documents
    assert "M  README.md" in after
    assert " M notes.txt" in after
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted([SPEC_RELATIVE, PLAN_RELATIVE])


def test_a_second_call_after_a_backfill_is_a_no_op(repo: Path) -> None:
    _drafts_scenario(repo)
    first = _run(repo)
    head = _rev(repo, "HEAD")

    second = _run(repo)

    assert first["backfilled"] != []  # non-vacuity: the first call rewrote
    assert second == {"plan_hash": first["plan_hash"], "backfilled": []}
    assert _rev(repo, "HEAD") == head


def test_the_backfill_moves_the_ref_once_and_records_it_in_the_reflog(repo: Path) -> None:
    _drafts_scenario(repo)
    old_tip = _rev(repo, "HEAD")
    digest = _digest(repo)
    calls: list[list[str]] = []

    _run(repo, git_runner=_recorder(calls, docs_commit.run_git))

    updates = [argv for argv in calls if "update-ref" in argv]
    assert len(updates) == 1, updates
    update = updates[0]
    assert "refs/heads/task" in update
    assert update[-1] == old_tip  # the expected old value: a compare-and-swap
    reflog = _git(repo, "reflog", "--format=%gs", "task").splitlines()
    assert f"docs_commit: backfill Plan-Hash {digest}" in reflog
    # task@{0} is the docs commit, task@{1} the backfilled tip, task@{2} the
    # pre-rewrite tip -- still recoverable.
    assert _rev(repo, "task@{2}") == old_tip


def test_a_malformed_plan_hash_does_not_count_as_stamped(repo: Path) -> None:
    """Review's grep would not count `Plan-Hash: zzz`, so the backfill must
    not treat it as stamped either."""
    _task_branch(repo)
    malformed = _commit_file(repo, "m.txt", "draft\n\nPlan-Hash: zzz")
    _write_documents(repo)
    digest = _digest(repo)

    result = _run(repo)

    assert result["backfilled"] == [malformed]
    m_new, _docs = _range(repo)
    lines = _message(repo, m_new).splitlines()
    assert "Plan-Hash: zzz" in lines
    assert lines[-1] == f"Plan-Hash: {digest}"


def test_a_branch_moved_meanwhile_makes_the_update_ref_refuse(repo: Path) -> None:
    """The final `update-ref` names the old tip, so a writer that moved the
    branch between the step's read and its write wins: git refuses and the
    `GitError` propagates."""
    _drafts_scenario(repo)

    def racing_runner(argv: list[str]) -> str:
        if "update-ref" in argv:
            _git(repo, "commit", "-q", "--allow-empty", "-m", "racer")
        return docs_commit.run_git(argv)

    with pytest.raises(docs_commit.GitError):
        _run(repo, git_runner=racing_runner)

    assert _message(repo) == "racer"


def _assert_untouched_and_docs_landed(repo: Path, old_tip: str, result: dict) -> None:
    """No rewrite: the pre-call tip is the docs commit's parent, unchanged."""
    assert result["backfilled"] == []
    assert _rev(repo, "HEAD~1") == old_tip
    assert _message(repo).splitlines()[0] == f"docs: add spec and plan for {TITLE}"


def test_a_merge_commit_in_range_leaves_the_branch_untouched(repo: Path) -> None:
    """Out of scope by the card: review's gate message already covers a branch
    carrying merges, so the backfill must not half-handle one."""
    _task_branch(repo)
    _commit_file(repo, "x.txt", "unstamped on task")
    _git(repo, "checkout", "-q", "-b", "side", "main")
    _commit_file(repo, "y.txt", "unstamped on side")
    _git(repo, "checkout", "-q", "task")
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge side")
    _commit_file(repo, "u.txt", "unstamped after the merge")
    _write_documents(repo)
    old_tip = _rev(repo, "HEAD")

    result = _run(repo)

    _assert_untouched_and_docs_landed(repo, old_tip, result)


def test_a_root_commit_in_range_leaves_the_branch_untouched(repo: Path) -> None:
    _git(repo, "checkout", "-q", "--orphan", "task")
    _commit_file(repo, "orphan.txt", "unstamped root")
    _write_documents(repo)
    old_tip = _rev(repo, "HEAD")

    result = _run(repo)

    _assert_untouched_and_docs_landed(repo, old_tip, result)


def test_an_unresolvable_base_leaves_the_branch_untouched(repo: Path) -> None:
    _task_branch(repo)
    _commit_file(repo, "u.txt", "unstamped")
    _write_documents(repo)
    old_tip = _rev(repo, "HEAD")

    result = _run(repo, base_branch="no-such-branch")

    _assert_untouched_and_docs_landed(repo, old_tip, result)


def test_a_detached_head_leaves_the_branch_untouched(repo: Path) -> None:
    """The step does not rewrite a ref it cannot name."""
    _task_branch(repo)
    _commit_file(repo, "u.txt", "unstamped")
    _git(repo, "checkout", "-q", "--detach", "task")
    _write_documents(repo)
    old_tip = _rev(repo, "HEAD")

    result = _run(repo)

    _assert_untouched_and_docs_landed(repo, old_tip, result)
    assert _rev(repo, "task") == old_tip


def test_commits_reachable_from_origin_base_are_never_rewritten(repo: Path) -> None:
    """`worktree.ensure` may branch from `origin/<base>` while the local base
    lags behind it; upstream commits must never be rewritten."""
    _git(repo, "checkout", "-q", "-b", "upstream", "main")
    u0 = _commit_file(repo, "u0.txt", "unstamped upstream commit")
    _git(repo, "update-ref", "refs/remotes/origin/main", u0)
    _git(repo, "checkout", "-q", "-b", "task", "upstream")
    u1 = _commit_file(repo, "u1.txt", "unstamped task commit")
    _write_documents(repo)
    digest = _digest(repo)

    result = _run(repo)

    assert result["backfilled"] == [u1]
    assert _rev(repo, "HEAD~2") == u0
    assert _message(repo, "HEAD~1").splitlines()[-1] == f"Plan-Hash: {digest}"
    assert f"Plan-Hash: {digest}" not in _message(repo, u0)


def test_a_signed_commit_is_rewritten_unsigned(repo: Path, tmp_path: Path) -> None:
    """A rewritten commit's old signature would no longer verify, so it is not
    carried over -- the header line and its continuation lines both go, as
    after a rebase. The signed object is crafted by hand: no gpg needed."""
    _task_branch(repo)
    base = _commit_file(repo, "before.txt", "unstamped before the signed one")
    tree = _rev(repo, "HEAD^{tree}")
    raw = (
        f"tree {tree}\n"
        f"parent {base}\n"
        "author Signer <signer@example.com> 1000000000 +0200\n"
        "committer Signer <signer@example.com> 1000000000 +0200\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " \n"
        " not-a-real-signature\n"
        " -----END PGP SIGNATURE-----\n"
        "\n"
        "signed draft\n"
    )
    object_file = tmp_path / "signed-commit"
    object_file.write_text(raw, encoding="utf-8")
    signed = _git(repo, "hash-object", "-t", "commit", "-w", str(object_file)).strip()
    _git(repo, "update-ref", "refs/heads/task", signed)
    _write_documents(repo)
    digest = _digest(repo)

    result = _run(repo)

    assert result["backfilled"] == [base, signed]
    _before_new, signed_new, _docs = _range(repo)
    rewritten = _git(repo, "cat-file", "commit", signed_new)
    assert "gpgsig" not in rewritten
    assert "not-a-real-signature" not in rewritten
    assert "author Signer <signer@example.com> 1000000000 +0200\n" in rewritten
    assert _message(repo, signed_new).splitlines()[-1] == f"Plan-Hash: {digest}"
