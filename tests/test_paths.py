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


def test_project_db_path_digest_matches_a_fixed_vector(monkeypatch, tmp_path):
    # An absolute root resolves to itself, so the on-disk name can be pinned to a
    # literal digest instead of to one recomputed the way the code computes it.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert paths.project_db_path(Path("/nonexistent/repo")).name == (
        "5b6e8e2d129e523b4fabf8a73dcdc18cb7f253565385fd9e6e5c0888ba865785.db"
    )


def test_project_db_path_accepts_a_root_that_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    absent = tmp_path / "not-cloned-yet"

    result = paths.project_db_path(absent)
    assert result.parent == tmp_path / "data" / "agent-manager" / "projects"
    assert result.name.endswith(".db")
    assert not absent.exists()


def test_project_lock_path_sits_beside_the_project_db(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    digest = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()
    result = paths.project_lock_path(project_root, "board")
    assert result.name == f"{digest}.board.lock"
    assert result.parent == paths.project_db_path(project_root).parent
    assert result.parent == tmp_path / "data" / "agent-manager" / "projects"


def test_project_lock_path_creates_the_projects_dir_but_not_the_file(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    projects_dir = tmp_path / "data" / "agent-manager" / "projects"
    assert not projects_dir.exists()

    result = paths.project_lock_path(project_root, "git")
    assert projects_dir.is_dir()
    assert not result.exists()


def test_project_lock_path_differs_per_name_and_per_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    repo1 = tmp_path / "repo1"
    repo1.mkdir()
    repo2 = tmp_path / "repo2"
    repo2.mkdir()

    assert paths.project_lock_path(repo1, "board") != paths.project_lock_path(
        repo1, "git"
    )
    assert paths.project_lock_path(repo1, "git") != paths.project_lock_path(
        repo2, "git"
    )
    assert paths.project_lock_path(repo1, "git") == paths.project_lock_path(
        repo1, "git"
    )


def test_project_lock_path_resolves_relative_and_symlinked_spelling(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project_root)
    monkeypatch.chdir(tmp_path)

    expected = paths.project_lock_path(project_root, "git")
    assert paths.project_lock_path(Path("repo"), "git") == expected
    assert paths.project_lock_path(project_root / "sub" / "..", "git") == expected
    assert paths.project_lock_path(link, "git") == expected


def test_project_lock_path_digest_matches_a_fixed_vector(monkeypatch, tmp_path):
    # Same literal digest as test_project_db_path_digest_matches_a_fixed_vector:
    # the lock file and the database share one digest per root.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert paths.project_lock_path(Path("/nonexistent/repo"), "git").name == (
        "5b6e8e2d129e523b4fabf8a73dcdc18cb7f253565385fd9e6e5c0888ba865785.git.lock"
    )


def test_project_lock_path_never_lands_inside_the_repository(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    result = paths.project_lock_path(project_root, "board")
    assert result.is_relative_to(paths.data_dir())
    assert not result.is_relative_to(project_root)
    assert list(project_root.iterdir()) == []


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


def test_project_digest_is_the_project_db_stem_and_64_hex(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    digest = paths.project_digest(project_root)

    assert paths.project_db_path(project_root).name == f"{digest}.db"
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_project_digest_matches_a_fixed_vector():
    assert paths.project_digest(Path("/nonexistent/repo")) == (
        "5b6e8e2d129e523b4fabf8a73dcdc18cb7f253565385fd9e6e5c0888ba865785"
    )


def test_boards_dir_is_under_data_dir_and_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    result = paths.boards_dir()

    assert result == tmp_path / "agent-manager" / "boards"
    assert result.is_dir()


def test_boards_dir_is_idempotent_and_keeps_what_is_inside(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = paths.boards_dir()
    (first / "a.log").write_text("x\n", encoding="utf-8")

    second = paths.boards_dir()

    assert second == first
    assert (second / "a.log").read_text(encoding="utf-8") == "x\n"


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


def test_attempt_dir_renders_attempt_as_plain_decimal(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    card_dir = paths.run_dir("run-abc") / "abc123"

    assert paths.attempt_dir("run-abc", "abc123", "implement", 0) == (
        card_dir / "implement.0"
    )
    assert paths.attempt_dir("run-abc", "abc123", "implement", 10) == (
        card_dir / "implement.10"
    )
    assert paths.attempt_dir("run-abc", "abc123", "implement", 1) != paths.attempt_dir(
        "run-abc", "abc123", "implement", 10
    )


def test_highest_attempt_is_zero_and_creates_nothing_for_a_missing_card(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert paths.highest_attempt("run-abc", "abc123", "implement") == 0
    # The run root may exist (run_dir's own side effect); the card dir must not.
    assert not (tmp_path / "agent-manager" / "runs" / "run-abc" / "abc123").exists()


def test_highest_attempt_is_zero_when_no_directory_matches_the_phase(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    paths.attempt_dir("run-abc", "abc123", "review", 1)
    card_dir = paths.run_dir("run-abc") / "abc123"
    before = sorted(p.name for p in card_dir.iterdir())

    assert paths.highest_attempt("run-abc", "abc123", "implement") == 0
    assert sorted(p.name for p in card_dir.iterdir()) == before == ["review.1"]


def test_highest_attempt_is_the_highest_consecutive_attempt_of_its_phase(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for n in (1, 2, 3):
        paths.attempt_dir("run-abc", "abc123", "implement", n)
    for n in (1, 2, 3, 4, 5):
        paths.attempt_dir("run-abc", "abc123", "review", n)

    assert paths.highest_attempt("run-abc", "abc123", "implement") == 3
    assert paths.highest_attempt("run-abc", "abc123", "review") == 5
    assert not (paths.run_dir("run-abc") / "abc123" / "implement.4").exists()


def test_highest_attempt_stops_at_the_first_gap(monkeypatch, tmp_path):
    # Same consecutive scan as dispatch.next_attempt has always done.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    paths.attempt_dir("run-abc", "abc123", "implement", 1)
    paths.attempt_dir("run-abc", "abc123", "implement", 3)

    assert paths.highest_attempt("run-abc", "abc123", "implement") == 1


def test_list_run_ids_lists_only_directories_sorted(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    runs = tmp_path / "agent-manager" / "runs"
    (runs / "run-b").mkdir(parents=True)
    (runs / "run-a").mkdir()
    (runs / "stray.txt").write_text("not a run\n")

    assert paths.list_run_ids() == ["run-a", "run-b"]
    # Listing only: nothing was added or removed under runs/.
    assert sorted(p.name for p in runs.iterdir()) == ["run-a", "run-b", "stray.txt"]


def test_list_run_ids_is_empty_and_creates_nothing_without_a_runs_directory(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert paths.list_run_ids() == []
    assert not (tmp_path / "agent-manager" / "runs").exists()


def test_attempt_path_names_the_attempt_dir_location_without_creating_it(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    located = paths.attempt_path("run-abc", "abc123", "verify", 2)

    assert located == tmp_path / "agent-manager" / "runs" / "run-abc" / "abc123" / "verify.2"
    assert not (tmp_path / "agent-manager" / "runs").exists()
    assert paths.attempt_dir("run-abc", "abc123", "verify", 2) == located


def test_recorded_attempts_is_empty_and_creates_nothing_for_a_missing_run(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert paths.recorded_attempts("run-abc", "abc123", "verify") == []
    assert not (tmp_path / "agent-manager" / "runs").exists()


def test_recorded_attempts_lists_the_contiguous_attempts_and_creates_nothing(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for n in (1, 2, 3):
        paths.attempt_dir("run-abc", "abc123", "verify", n)
    paths.attempt_dir("run-abc", "abc123", "implement", 1)
    card_dir = tmp_path / "agent-manager" / "runs" / "run-abc" / "abc123"
    before = sorted(p.name for p in card_dir.iterdir())

    assert paths.recorded_attempts("run-abc", "abc123", "verify") == [1, 2, 3]
    assert paths.recorded_attempts("run-abc", "abc123", "review") == []
    assert sorted(p.name for p in card_dir.iterdir()) == before


def test_recorded_attempts_stops_at_the_first_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    paths.attempt_dir("run-abc", "abc123", "verify", 1)
    paths.attempt_dir("run-abc", "abc123", "verify", 3)

    assert paths.recorded_attempts("run-abc", "abc123", "verify") == [1]


def test_recorded_attempts_ignores_a_plain_file_in_an_attempts_place(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    card_dir = paths.run_dir("run-abc") / "abc123"
    card_dir.mkdir()
    (card_dir / "verify.1").write_text("not a directory\n")

    assert paths.recorded_attempts("run-abc", "abc123", "verify") == []


def test_data_path_is_where_data_dir_lives_and_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = paths.data_path()

    assert result == tmp_path / "xdg" / "agent-manager"
    assert not (tmp_path / "xdg").exists()
    assert paths.data_dir() == result


def test_project_db_location_matches_project_db_path_and_creates_nothing(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    result = paths.project_db_location(project_root)

    assert not (tmp_path / "xdg").exists()
    assert result == paths.project_db_path(project_root)


def test_attempt_path_and_list_run_ids_create_no_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    paths.attempt_path("run-abc", "abc123", "verify", 1)
    assert paths.recorded_attempts("run-abc", "abc123", "verify") == []
    assert paths.list_run_ids() == []

    assert not (tmp_path / "xdg").exists()
