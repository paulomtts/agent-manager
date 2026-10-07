"""A stale local base and a branch left over from an older plan, in real repos."""

import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from agent_manager.steps import docs_commit, reducers, worktree

PLAN = "docs/superpowers/plans/p.md"
SPEC = "docs/superpowers/specs/p.md"
OLD, NEW = "11111111", "22222222"


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _init(path: Path, *args: str) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", *args, str(path)], check=True)
    for key, value in (("user.email", "t@e.c"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(path, "config", key, value)
    return path


def _commit(cwd: Path, name: str, trailer: str | None = None) -> str:
    (cwd / name).write_text(name, encoding="utf-8")
    _git(cwd, "add", name)
    message = name if trailer is None else f"{name}\n\nPlan-Hash: {trailer}"
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def clone(tmp_path: Path) -> tuple[Path, Path]:
    """(local clone behind origin by one commit, a second clone to push from)."""
    origin = _init(tmp_path / "origin.git", "--bare")
    seed = _init(tmp_path / "seed")
    _commit(seed, "a")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True)
    for key, value in (("user.email", "t@e.c"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(local, "config", key, value)
    _commit(seed, "upstream-only")
    _git(seed, "push", "-q", "origin", "main")
    _git(local, "fetch", "-q", "origin")
    return local, seed


@pytest.mark.git
def test_a_stale_checked_out_base_is_fast_forwarded_and_the_worktree_matches(clone, tmp_path):
    local, _ = clone
    upstream = _git(local, "rev-parse", "origin/main")
    assert _git(local, "rev-parse", "main") != upstream

    wt = tmp_path / "wt"
    result = worktree.ensure("task-1", "main", wt, local, fast_forward=True)

    assert result["base_fast_forwarded"] is True
    assert _git(local, "rev-parse", "main") == upstream
    assert _git(wt, "rev-parse", "HEAD") == upstream
    assert _git(wt, "rev-list", "--count", "main..HEAD") == "0"


@pytest.mark.git
def test_a_base_that_is_not_checked_out_is_moved_by_update_ref(clone, tmp_path):
    local, _ = clone
    _git(local, "switch", "-q", "-c", "other")
    upstream = _git(local, "rev-parse", "origin/main")

    worktree.ensure("task-1", "main", tmp_path / "wt", local, fast_forward=True)

    assert _git(local, "rev-parse", "main") == upstream


@pytest.mark.git
def test_a_base_with_local_only_commits_is_never_touched(clone, tmp_path):
    local, _ = clone
    mine = _commit(local, "local-only")

    result = worktree.ensure("task-1", "main", tmp_path / "wt", local, fast_forward=True)

    assert "base_fast_forwarded" not in result
    assert _git(local, "rev-parse", "main") == mine


@pytest.mark.git
def test_a_dirty_checked_out_base_is_left_alone(clone, tmp_path):
    local, _ = clone
    before = _git(local, "rev-parse", "main")
    (local / "a").write_text("edited", encoding="utf-8")

    result = worktree.ensure("task-1", "main", tmp_path / "wt", local, fast_forward=True)

    assert "base_fast_forwarded" not in result
    assert _git(local, "rev-parse", "main") == before
    assert (local / "a").read_text(encoding="utf-8") == "edited"


@pytest.mark.git
def test_without_the_flag_the_base_never_moves(clone, tmp_path):
    local, _ = clone
    before = _git(local, "rev-parse", "main")

    worktree.ensure("task-1", "main", tmp_path / "wt", local)

    assert _git(local, "rev-parse", "main") == before


# --- stale branch -----------------------------------------------------------


def _documents(wt: Path, plan_text: str = "plan") -> None:
    (wt / PLAN).parent.mkdir(parents=True, exist_ok=True)
    (wt / SPEC).parent.mkdir(parents=True, exist_ok=True)
    (wt / PLAN).write_text(plan_text, encoding="utf-8")
    (wt / SPEC).write_text("spec", encoding="utf-8")


def _commit_documents(wt: Path) -> dict:
    return docs_commit.commit_documents(SimpleNamespace(title="T"), SPEC, PLAN, wt, "main")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = _init(tmp_path / "repo")
    _commit(root, "base")
    _git(root, "switch", "-q", "-c", "task-1")
    return root


@pytest.mark.git
def test_a_branch_holding_only_an_older_plans_commits_is_preserved_and_recreated(repo):
    old_tip = _commit(repo, "old-work", OLD)
    _documents(repo)

    result = _commit_documents(repo)

    ref = result["preserved_ref"]
    assert ref.startswith(f"refs/am/stale/task-1/{old_tip[:8]}-")
    assert _git(repo, "rev-parse", ref) == old_tip
    assert _git(repo, "rev-parse", "task-1~1") == _git(repo, "rev-parse", "main")
    assert not (repo / "old-work").exists()
    assert _git(repo, "log", "-1", "--format=%B", "task-1").endswith(f"Plan-Hash: {result['plan_hash']}")
    assert reducers.stale_branch_gate(result)["warn"].count(ref) == 1


@pytest.mark.git
def test_a_branch_with_some_commits_of_this_plan_resumes_untouched(repo):
    _documents(repo)
    digest = docs_commit.plan_hash(b"plan")
    _commit(repo, "old-work", OLD)
    mine = _commit(repo, "my-work", digest)

    result = _commit_documents(repo)

    assert "preserved_ref" not in result
    assert _git(repo, "rev-parse", "HEAD") != ""
    assert (repo / "old-work").exists() and (repo / "my-work").exists()
    assert _git(repo, "merge-base", "--is-ancestor", mine, "HEAD") == ""


@pytest.mark.git
def test_a_dirty_worktree_keeps_a_stale_branch_as_it_is(repo):
    old_tip = _commit(repo, "old-work", OLD)
    _documents(repo)
    (repo / "scratch.txt").write_text("uncommitted", encoding="utf-8")

    result = _commit_documents(repo)

    assert "preserved_ref" not in result
    assert (repo / "old-work").exists()
    assert _git(repo, "merge-base", "--is-ancestor", old_tip, "HEAD") == ""
    assert _git(repo, "for-each-ref", "refs/am/stale") == ""


@pytest.mark.git
def test_a_stale_branch_is_recreated_on_the_fetched_base(clone, tmp_path):
    local, _ = clone
    _git(local, "switch", "-q", "-c", "task-1", "main")
    _commit(local, "old-work", OLD)
    _documents(local)
    upstream = _git(local, "rev-parse", "origin/main")

    result = docs_commit.commit_documents(SimpleNamespace(title="T"), SPEC, PLAN, local, "main")

    assert "preserved_ref" in result
    assert _git(local, "rev-parse", "task-1~1") == upstream
