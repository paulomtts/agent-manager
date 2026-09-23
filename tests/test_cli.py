"""Behaviour of the `run --card` command (design §10, spec card cbe34d00).

Two tiers live here, per design §14 lines 477-492 and the spec's Tests section:

- the pure helpers (`render`, `mint_run_id`, `worktree_for`, `resolve_repo_dir`)
  are unit tests -- no clock, no filesystem beyond `tmp_path`, no subprocess;
- `run_card` and the Typer command are **Engine tier**: a fake
  `engine.AgentPhaseRunner` returning canned results, on **Steps-tier fixtures**
  (a real temporary git repo, a real temporary brd board, and `XDG_DATA_HOME`
  pointed at `tmp_path` so `paths.data_dir()` never touches the developer's own).
  No harness process is ever launched: the real `dispatch.AgentRunner` and its
  launcher are never constructed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import cli


def test_render_is_one_line_of_json_by_default():
    text = cli.render({"ok": True, "data": {"status": "done"}})
    assert "\n" not in text
    assert json.loads(text) == {"ok": True, "data": {"status": "done"}}


def test_render_indents_under_pretty():
    text = cli.render({"ok": True, "data": {"status": "done"}}, pretty=True)
    assert "\n" in text
    assert json.loads(text) == {"ok": True, "data": {"status": "done"}}


def test_ok_envelope_matches_brds_shape():
    assert cli.ok_envelope({"run_id": "r1"}) == {"ok": True, "data": {"run_id": "r1"}}


def test_error_envelope_carries_the_exception_class_name_and_message():
    envelope = cli.error_envelope(ValueError("not a card id: 'nope'"))
    assert envelope == {
        "ok": False,
        "error": {"type": "ValueError", "message": "not a card id: 'nope'"},
    }


def test_render_survives_a_path_in_the_payload():
    """`worktree` is a Path and `json.dumps` refuses one. A renderer that raised
    would turn a finished run into a traceback with no envelope at all."""
    text = cli.render(cli.ok_envelope({"worktree": Path("/repo/.claude/worktrees/m1/x")}))
    assert json.loads(text)["data"]["worktree"] == "/repo/.claude/worktrees/m1/x"


def test_mint_run_id_is_the_timestamp_and_the_cards_short_id():
    run_id = cli.mint_run_id(
        "cbe34d00-9d8d-4f41-9c94-f99e665771b0",
        datetime(2026, 9, 23, 14, 5, 6, tzinfo=timezone.utc),
    )
    assert run_id == "20260923T140506Z-cbe34d00"


def test_mint_run_id_refuses_something_that_is_not_a_card_id():
    with pytest.raises(ValueError):
        cli.mint_run_id("not-a-uuid", datetime(2026, 9, 23, tzinfo=timezone.utc))


def test_worktree_for_is_absolute_and_under_dot_claude_worktrees():
    worktree = cli.worktree_for(Path("/repo"), "m1/task-add-run-card-cbe34d00")
    assert worktree == Path("/repo/.claude/worktrees/m1/task-add-run-card-cbe34d00")
    assert worktree.is_absolute()


def test_resolve_repo_dir_returns_an_absolute_path(tmp_path, monkeypatch):
    """`steps/worktree.ensure` refuses a relative path outright, so the CLI has
    to resolve `--repo-dir` -- whose default is `.` -- before deriving anything."""
    monkeypatch.chdir(tmp_path)
    resolved = cli.resolve_repo_dir(Path("."))
    assert resolved.is_absolute()
    assert resolved == tmp_path.resolve()


def test_resolve_repo_dir_refuses_a_path_that_is_not_a_directory(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(cli.RepoDirError) as caught:
        cli.resolve_repo_dir(missing)
    assert "nope" in str(caught.value)
