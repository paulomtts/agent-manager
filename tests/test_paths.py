import hashlib
from pathlib import Path

import pytest

from agent_manager import paths


def test_data_dir_uses_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = paths.data_dir()
    assert result == tmp_path / "agent-manager"
    assert result.is_dir()


def test_data_dir_defaults_to_home_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = paths.data_dir()
    assert result == tmp_path / ".local" / "share" / "agent-manager"
    assert result.is_dir()


def test_data_dir_treats_empty_xdg_data_home_as_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    result = paths.data_dir()
    assert result == tmp_path / ".local" / "share" / "agent-manager"
    assert result.is_dir()


def test_data_dir_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = paths.data_dir()
    second = paths.data_dir()
    assert first == second
    assert second.is_dir()


def test_data_dir_raises_key_error_when_home_and_xdg_are_unset(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(KeyError):
        paths.data_dir()


def test_data_dir_propagates_oserror_when_a_file_is_in_the_way(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    (tmp_path / "agent-manager").write_text("not a directory")
    with pytest.raises(OSError):
        paths.data_dir()


def test_project_db_path_is_deterministic_per_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    result = paths.project_db_path(project_root)
    assert result == paths.project_db_path(project_root)
    assert result.parent == tmp_path / "data" / "agent-manager" / "projects"
    assert result.parent.is_dir()
    assert not result.exists()


def test_project_db_path_differs_per_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    repo1 = tmp_path / "repo1"
    repo1.mkdir()
    repo2 = tmp_path / "repo2"
    repo2.mkdir()

    assert paths.project_db_path(repo1) != paths.project_db_path(repo2)


def test_project_db_path_resolves_relative_spelling(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.chdir(tmp_path)

    assert paths.project_db_path(Path("repo")) == paths.project_db_path(project_root)
    assert paths.project_db_path(project_root / "sub" / "..") == paths.project_db_path(
        project_root
    )


def test_project_db_path_resolves_symlinked_spelling(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project_root)

    assert paths.project_db_path(link) == paths.project_db_path(project_root)


def test_project_db_path_digest_is_sha256_of_resolved_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    expected = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()
    result = paths.project_db_path(project_root)
    assert result.name == f"{expected}.db"


def test_project_db_path_accepts_a_root_that_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    absent = tmp_path / "not-cloned-yet"

    result = paths.project_db_path(absent)
    assert result.parent == tmp_path / "data" / "agent-manager" / "projects"
    assert result.name.endswith(".db")
    assert not absent.exists()


def test_run_dir_is_under_data_dir_and_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = paths.run_dir("run-abc")
    assert result == tmp_path / "agent-manager" / "runs" / "run-abc"
    assert result.is_dir()


def test_run_dir_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = paths.run_dir("run-abc")
    (first / "journal.jsonl").write_text("{}\n")
    second = paths.run_dir("run-abc")
    assert first == second
    assert (second / "journal.jsonl").read_text() == "{}\n"


def test_attempt_dir_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = paths.attempt_dir("run-abc", "abc123", "implement", 2)
    assert result == (
        tmp_path / "agent-manager" / "runs" / "run-abc" / "abc123" / "implement.2"
    )
    assert result.is_dir()


def test_attempt_dir_separates_attempts_and_phases(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = paths.attempt_dir("run-abc", "abc123", "implement", 1)
    second = paths.attempt_dir("run-abc", "abc123", "implement", 2)
    review = paths.attempt_dir("run-abc", "abc123", "review", 1)

    assert first != second
    assert first != review
    assert first.parent == second.parent == review.parent
    assert first.parent == paths.run_dir("run-abc") / "abc123"


def test_run_tree_never_lands_inside_a_worktree(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.chdir(worktree)

    run = paths.run_dir("run-abc")
    attempt = paths.attempt_dir("run-abc", "abc123", "implement", 1)

    assert run.is_relative_to(paths.data_dir())
    assert attempt.is_relative_to(paths.data_dir())
    assert not run.is_relative_to(worktree)
    assert not attempt.is_relative_to(worktree)
    assert list(worktree.iterdir()) == []
