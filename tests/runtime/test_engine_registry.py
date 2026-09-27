"""Freeing agent names with pygents' public `AgentRegistry.unregister` (card 59fd7f22, decision A2).

Engine tier (agent-manager design §14: the engine driven with fake steps):
`runtime.engine.run_subtask` runs plain-function steps over a real temp store,
built as tests/runtime/test_resume.py builds it. Registry isolation between
tests is the autouse `fresh_pygents` fixture in tests/runtime/conftest.py.
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, AgentRegistry
from pygents.errors import UnregisteredAgentError

from agent_manager import models, store as store_module
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow.phases import Step, Workflow

RUN_ID = "run-2026-09-27-01"
STORY_ID = "dd4a87d5"
CARD_ID = "59fd7f22"
REPO = Path("/repo")
FIXED = datetime(2026, 9, 27, tzinfo=timezone.utc)
AGENT_NAME = f"{RUN_ID}:{CARD_ID}"
SRC = Path(__file__).resolve().parents[2] / "src" / "agent_manager"
PRIVATE_REGISTRY_ACCESS = re.compile(r"(AgentRegistry|ToolRegistry|HookRegistry)\._")


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m8/task-raise-the-pygents-floor-{CARD_ID}",
        base_branch="m8/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _go(workflow: Workflow, opened):
    return runtime_engine.run_subtask(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
    )


def _one(card: str) -> dict[str, Any]:
    return {"one": "ONE"}


def _boom(card: str) -> dict[str, Any]:
    raise RuntimeError("boom")


def _assert_free(name: str) -> None:
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(name)


def test_a_finished_run_frees_its_agent_name(store):
    wf = Workflow("single", (Step("one", _one),))

    first = _go(wf, store)

    assert first.status == "done"
    _assert_free(AGENT_NAME)
    second = _go(wf, store)
    assert second.status == "done"
    assert second.results == {"one": {"one": "ONE"}}
    _assert_free(AGENT_NAME)


def test_an_escalated_run_frees_its_agent_name(store):
    # Review Focus 3: the `finally` frees the name on an escalation too.
    summary = _go(Workflow("escalates", (Step("boom", _boom),)), store)

    assert summary.status == "escalated"
    _assert_free(AGENT_NAME)
    Agent(AGENT_NAME, "a relaunch of the same card", [])


def test_cleanup_of_an_agent_that_was_never_registered_does_not_raise():
    # Review Focus 1: the resume path forgets a checkpointed name that this
    # process never registered.
    _assert_free("never-registered")

    runtime_engine._forget("never-registered")

    _assert_free("never-registered")


def test_forget_frees_a_registered_name():
    Agent("left-behind", "a stale registration", [])

    runtime_engine._forget("left-behind")

    _assert_free("left-behind")
    Agent("left-behind", "the name is reusable", [])


def test_forget_lets_other_errors_through(monkeypatch):
    # Review Focus 2: only `UnregisteredAgentError` is suppressed; a bare
    # `KeyError` (its base class) is a real fault and must surface.
    def broken(name: str) -> None:
        raise KeyError(name)

    monkeypatch.setattr(AgentRegistry, "unregister", broken)

    with pytest.raises(KeyError) as caught:
        runtime_engine._forget("anything")

    assert type(caught.value) is KeyError


def test_no_private_registry_access_in_src():
    sources = sorted(SRC.rglob("*.py"))
    # Review Focus 5: a wrong path must not make this pass vacuously.
    assert sources, f"no Python sources found under {SRC}"

    hits = [
        f"{path.relative_to(SRC.parent)}:{number}: {line.strip()}"
        for path in sources
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if PRIVATE_REGISTRY_ACCESS.search(line)
    ]

    assert hits == []
