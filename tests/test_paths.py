from pathlib import Path

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
