"""Resolving a phase's declared inputs and rendering its prompt (spec §6 step 2, §7).

Pure-function tier per design §14 lines 477-492: `render_prompt` is a pure
function of a phase and a binding table, so these tests build both by hand. No
store, no git, no `brd`, no harness process. The only filesystem these tests
touch is pytest's `tmp_path`, and only where the module itself reads files
(`repo_docs`) or writes one (`RenderedPrompt.write`).
"""

import json
from pathlib import Path

import pytest

from agent_manager import dag, models, prompt
from agent_manager.errors import EngineError
from agent_manager.workflow.loader import AgentPhase

CARD = models.Card(
    id="968fba15-0971-456a-ae9f-57ff2210f0ce",
    title="Resolve phase inputs",
    status="todo",
    parent_id="2143808b-b236-4cf9-b172-53809bdbc1a1",
    description="Resolve each agent phase's declared inputs through the §7 table.",
)
PARENT = models.Card(
    id="2143808b-b236-4cf9-b172-53809bdbc1a1",
    title="The workflow document and the engine",
    status="in_progress",
)


def _phase(inputs, *, name="implement", role="coder", **extra) -> AgentPhase:
    return AgentPhase(kind="agent", name=name, role=role, inputs=list(inputs), **extra)


def _context(**overrides):
    context = {
        "card": CARD.id,
        "card_details": CARD,
        "parent_story_details": PARENT,
        "branch": "m1/task-968fba15",
        "base": "m1/story-base",
        "worktree": None,
        "repo_dir": Path("/repo"),
        "commands": ["uv run pytest"],
        "spec_path": "docs/superpowers/specs/resolve-phase-inputs-968fba15.md",
        "plan_path": "docs/superpowers/plans/resolve-phase-inputs-968fba15.md",
        "explore": {"summary": "read engine.py", "files": ["src/agent_manager/engine.py"]},
    }
    context.update(overrides)
    return context


def test_sections_follow_the_declared_inputs_order_under_a_header_each():
    rendered = prompt.render_prompt(_phase(["branch", "base_branch"]), _context())

    assert rendered.phase == "implement"
    assert rendered.inputs == ("branch", "base_branch")
    assert rendered.text == (
        "# phase: implement\n"
        "# role: coder\n"
        "\n"
        "## branch\n"
        "m1/task-968fba15\n"
        "\n"
        "## base_branch\n"
        "m1/story-base\n"
    )


def test_the_same_phase_and_context_render_byte_identical_text():
    phase = _phase(["base_branch", "branch"])

    first = prompt.render_prompt(phase, _context())
    second = prompt.render_prompt(phase, _context())

    assert first.text == second.text
    assert first.inputs == ("base_branch", "branch")


def test_a_phase_with_no_inputs_renders_the_header_alone():
    rendered = prompt.render_prompt(_phase([], name="explore", role="explorer"), _context())

    assert rendered.sections == ()
    assert rendered.text == "# phase: explore\n# role: explorer\n"


def test_an_input_named_twice_renders_one_section():
    """Review Focus: a document author repeating a name must not double the bill."""
    rendered = prompt.render_prompt(_phase(["branch", "branch"]), _context())

    assert rendered.inputs == ("branch",)
    assert rendered.text.count("## branch") == 1


def test_an_unknown_input_name_names_the_phase_the_input_and_the_table():
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(_phase(["branch", "the_whole_repo"]), _context())

    assert caught.value.phase == "implement"
    assert caught.value.parameter == "the_whole_repo"
    assert "'the_whole_repo'" in str(caught.value)
    assert "branch" in str(caught.value)


def test_engine_re_exports_the_same_error_class():
    from agent_manager import engine

    assert engine.EngineError is EngineError


def _section(rendered, name: str) -> str:
    """The body of one section, without its header or its trailing newline."""
    bodies = dict(rendered.sections)
    return bodies[name]


def test_card_inlines_the_full_card_as_json():
    rendered = prompt.render_prompt(_phase(["card"], name="spec", role="spec_author"), _context())

    payload = json.loads(_section(rendered, "card"))
    assert payload["id"] == CARD.id
    assert payload["title"] == "Resolve phase inputs"
    assert payload["status"] == "todo"


def test_parent_story_inlines_the_parent_card_as_json():
    rendered = prompt.render_prompt(_phase(["parent_story"], name="explore"), _context())

    assert json.loads(_section(rendered, "parent_story"))["title"] == (
        "The workflow document and the engine"
    )


def test_a_subtask_with_no_parent_renders_parent_story_as_null():
    rendered = prompt.render_prompt(
        _phase(["parent_story"], name="explore"), _context(parent_story_details=None)
    )

    assert json.loads(_section(rendered, "parent_story")) is None


def test_explore_inlines_the_validated_result_of_the_explore_phase():
    rendered = prompt.render_prompt(_phase(["explore"], name="spec"), _context())

    assert json.loads(_section(rendered, "explore")) == {
        "summary": "read engine.py",
        "files": ["src/agent_manager/engine.py"],
    }


def test_verification_inlines_the_runs_commands_as_json():
    rendered = prompt.render_prompt(_phase(["verification"], name="explore"), _context())

    assert json.loads(_section(rendered, "verification")) == ["uv run pytest"]


def test_an_empty_command_list_inlines_as_an_empty_json_array():
    """Review Focus: a run that discovered no commands says so, and does not raise."""
    rendered = prompt.render_prompt(
        _phase(["verification"], name="explore"), _context(commands=[])
    )

    assert json.loads(_section(rendered, "verification")) == []


def test_a_value_json_cannot_encode_is_inlined_as_its_string_form():
    """Review Focus: a `Path` in `commands` must not raise `TypeError` out of json."""
    rendered = prompt.render_prompt(
        _phase(["verification"], name="explore"),
        _context(commands=[Path("/repo/scripts/check.sh")]),
    )

    assert json.loads(_section(rendered, "verification")) == ["/repo/scripts/check.sh"]


def test_an_input_the_context_never_populated_is_named_as_such():
    context = _context()
    del context["explore"]

    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(_phase(["explore"], name="spec"), context)

    assert caught.value.phase == "spec"
    assert caught.value.parameter == "explore"
    assert "nothing in the context supplies it" in str(caught.value)


def test_a_card_input_on_a_context_with_no_card_details_is_named_as_such():
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(_phase(["card"], name="spec"), _context(card_details=None))

    assert caught.value.parameter == "card"
    assert "never populated" in str(caught.value)
    assert "card_details" in str(caught.value)
