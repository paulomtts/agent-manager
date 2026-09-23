"""Behaviour of one agent phase's dispatch, validation, retry and escalation
(design §6 lines 261-278, §9 lines 365-368, §12 line 429).

Engine tier per design §14 lines 486-488: a fake adapter and a fake launcher
that write canned result files (valid, invalid, gate-failing, absent) stand in
for a harness, and the store is a real temp SQLite projection plus a real temp
JSONL journal. No process is ever started -- the launcher is injected, and one
test asserts `subprocess.Popen` is never reached.
"""

from pathlib import Path

import pytest

from agent_manager import dispatch, paths, prompt

RUN_ID = "run-2026-09-23-01"
CARD = "bf8e415b"


@pytest.fixture
def data_home(monkeypatch, tmp_path):
    """Root every `paths.*` write under tmp_path, as tests/test_paths.py does."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _rendered(text: str = "# phase: explore\n# role: explorer\n") -> prompt.RenderedPrompt:
    return prompt.RenderedPrompt(phase="explore", text=text, sections=(("card", "{}"),))


def test_the_first_attempt_is_numbered_one(data_home):
    assert dispatch.next_attempt(RUN_ID, CARD, "explore") == 1


def test_an_existing_attempt_directory_is_never_reused(data_home):
    # A resumed run may find `explore.1` already on disk; §"Error paths" keeps
    # the discarded attempt as evidence and takes the next free number.
    paths.attempt_dir(RUN_ID, CARD, "explore", 1)
    paths.attempt_dir(RUN_ID, CARD, "explore", 2)

    assert dispatch.next_attempt(RUN_ID, CARD, "explore") == 3


def test_attempt_numbers_are_per_phase(data_home):
    paths.attempt_dir(RUN_ID, CARD, "explore", 1)

    assert dispatch.next_attempt(RUN_ID, CARD, "implement") == 1


def test_the_attempt_directory_is_outside_the_worktree(data_home):
    worktree = data_home / "repo" / ".claude" / "worktrees" / "m1" / "task-bf8e415b"
    worktree.mkdir(parents=True)

    attempt = paths.attempt_dir(RUN_ID, CARD, "explore", 1)

    assert not attempt.resolve().is_relative_to(worktree.resolve())
    assert attempt.name == "explore.1"
    assert attempt.parent.name == CARD
    assert attempt.parent.parent.parent.name == "runs"


def test_feedback_is_appended_below_the_original_prompt_text():
    rendered = _rendered()

    second = dispatch.with_feedback(rendered, "summary: Field required")

    assert second.text.startswith(rendered.text)
    assert dispatch.FEEDBACK_HEADING in second.text
    assert "summary: Field required" in second.text
    assert second.phase == "explore"
    assert rendered.text == "# phase: explore\n# role: explorer\n"


def test_feedback_accumulates_across_attempts():
    once = dispatch.with_feedback(_rendered(), "first complaint")

    twice = dispatch.with_feedback(once, "second complaint")

    assert "first complaint" in twice.text
    assert twice.text.index("first complaint") < twice.text.index("second complaint")


def test_a_written_prompt_lands_in_the_attempt_directory(data_home):
    attempt = paths.attempt_dir(RUN_ID, CARD, "explore", 1)

    written = dispatch.with_feedback(_rendered(), "try again").write(attempt)

    assert written == attempt / "prompt.txt"
    assert "try again" in written.read_text(encoding="utf-8")
