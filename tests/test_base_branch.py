"""`--base-branch` resolution: derived from the repository, refused in pre-flight when absent."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import cli, paths, runs

pytestmark = [pytest.mark.git, pytest.mark.real_base_branch]

runner = CliRunner()


def _git(root: Path, *argv: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *argv], check=True, capture_output=True, text=True
    )
    return done.stdout


def _repo(tmp_path: Path, branch: str = "main", commit: bool = True) -> Path:
    root = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "-q", "-b", branch, str(root)], check=True, capture_output=True
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "tests")
    _git(root, "config", "commit.gpgsign", "false")
    if commit:
        (root / "README.md").write_text("x\n", encoding="utf-8")
        _git(root, "add", "README.md")
        _git(root, "commit", "-q", "-m", "base")
    return root


def test_a_repository_whose_default_branch_is_main_defaults_to_main(tmp_path):
    assert runs.resolve_base_branch(_repo(tmp_path), None) == "main"


def test_origin_head_wins_over_the_checked_out_branch(tmp_path):
    root = _repo(tmp_path)
    _git(root, "branch", "trunk")
    _git(root, "update-ref", "refs/remotes/origin/trunk", "trunk")
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    _git(root, "switch", "-q", "-c", "feature")

    assert runs.resolve_base_branch(root, None) == "trunk"


def test_a_dangling_origin_head_falls_back_to_the_checked_out_branch(tmp_path):
    root = _repo(tmp_path)
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")

    assert runs.resolve_base_branch(root, None) == "main"


def test_master_is_used_only_when_it_exists(tmp_path):
    root = _repo(tmp_path, branch="master")
    assert runs.resolve_base_branch(root, None) == "master"

    empty = _repo(tmp_path / "other", branch="main", commit=False)
    with pytest.raises(runs.BaseBranchError, match="no --base-branch given"):
        runs.resolve_base_branch(empty, None)


def test_an_explicit_existing_branch_is_kept(tmp_path):
    root = _repo(tmp_path)
    _git(root, "branch", "develop")

    assert runs.resolve_base_branch(root, "develop") == "develop"


def test_an_explicit_missing_branch_is_refused_listing_the_candidates(tmp_path):
    root = _repo(tmp_path)
    _git(root, "branch", "develop")

    with pytest.raises(runs.BaseBranchError) as caught:
        runs.resolve_base_branch(root, "master")

    assert "'master'" in str(caught.value)
    assert "develop" in str(caught.value) and "main" in str(caught.value)


REFUSED_RUNS = {
    "card": ["--card", "bd34c55b", "--branch-prefix", "m1"],
    "card-dry-run-less-detach": ["--card", "bd34c55b", "--branch-prefix", "m1", "--detach"],
    "milestone": ["--milestone", "Milestone 1", "--branch-prefix", "m1"],
    "milestone-dry-run": ["--milestone", "Milestone 1", "--branch-prefix", "m1", "--dry-run"],
    "story": ["--story", "Story", "--branch-prefix", "m1"],
    "story-dry-run": ["--story", "Story", "--branch-prefix", "m1", "--dry-run"],
    "board": ["--board"],
    "board-dry-run": ["--board", "--dry-run"],
}


@pytest.mark.parametrize("flags", REFUSED_RUNS.values(), ids=REFUSED_RUNS.keys())
def test_a_missing_base_branch_is_refused_in_preflight_and_leaves_nothing(tmp_path, flags):
    root = _repo(tmp_path)

    result = runner.invoke(
        cli.app, ["run", *flags, "--repo-dir", str(root), "--base-branch", "master"]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "BaseBranchError"
    assert "main" in envelope["error"]["message"]
    assert not (root / ".claude").exists()
    assert _git(root, "branch", "--format=%(refname:short)").split() == ["main"]
    assert _git(root, "status", "--porcelain") == ""
    data = paths.data_dir()
    assert not data.exists() or not any(data.rglob("*.jsonl"))
    assert not (data / "runs").exists() or not any((data / "runs").iterdir())


def test_a_milestone_dry_run_without_base_branch_plans_against_the_default(
    tmp_path, fake_board
):
    root = _repo(tmp_path)
    milestone = fake_board.add_card("Milestone 1: walking skeleton")
    story = fake_board.add_card("A story", parent_id=milestone)
    fake_board.add_card("A subtask", parent_id=story)

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--milestone",
            "Milestone 1",
            "--dry-run",
            "--repo-dir",
            str(root),
            "--branch-prefix",
            "m1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "master" not in result.stdout
    assert '"base": "main"' in json.dumps(json.loads(result.stdout)["data"])
