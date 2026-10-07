"""Resolving a phase's declared inputs and rendering its prompt (spec §6 step 2, §7).

Pure-function tier per design §14 lines 477-492: `render_prompt` is a pure
function of a phase and a binding table, so these tests build both by hand. No
store, no git, no `brd`, no harness process. The only filesystem these tests
touch is pytest's `tmp_path`, and only where the module itself reads files
(`repo_docs`) or writes one (`RenderedPrompt.write`).
"""

import ast
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from agent_manager import dag, models, prompt, results
from agent_manager.runtime.errors import EngineError
from agent_manager.roles import loader as roles_loader
from agent_manager.workflow import phases

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

PLAN_HASH = "9f3a12bc"
"""`docs_commit.plan_hash()`'s shape: 8 lowercase hex characters."""


def _phase(inputs, *, name="implement", role="coder", **extra) -> phases.AgentPhase:
    return phases.AgentPhase(name=name, role=role, inputs=tuple(inputs), result=None, **extra)


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
        "docs_commit": {"plan_hash": PLAN_HASH},
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


def test_spec_path_and_plan_path_render_as_the_path_alone(tmp_path):
    """§7: documents are passed by path. The file's contents must not leak in."""
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("SENTINEL-DO-NOT-INLINE\n", encoding="utf-8")

    rendered = prompt.render_prompt(
        _phase(["spec_path", "plan_path"]),
        _context(spec_path=str(spec_file), plan_path="docs/superpowers/plans/x.md"),
    )

    assert _section(rendered, "spec_path") == str(spec_file)
    assert _section(rendered, "plan_path") == "docs/superpowers/plans/x.md"
    assert "SENTINEL-DO-NOT-INLINE" not in rendered.text


def test_expand_writes_substitutes_the_dag_task_stem():
    expanded = prompt.expand_writes(
        "docs/superpowers/specs/{stem}.md", CARD, phase="spec", input_name="spec_path"
    )

    assert expanded == f"docs/superpowers/specs/{dag.task_stem(CARD)}.md"
    assert expanded == "docs/superpowers/specs/resolve-phase-inputs-968fba15.md"


def test_expand_writes_refuses_a_placeholder_it_does_not_know():
    with pytest.raises(EngineError) as caught:
        prompt.expand_writes(
            "docs/{kind}/{stem}.md", CARD, phase="spec", input_name="spec_path"
        )

    assert caught.value.phase == "spec"
    assert caught.value.parameter == "spec_path"
    assert "'kind'" in str(caught.value)
    assert "{stem}" in str(caught.value)


@pytest.mark.parametrize(
    "template", ["../{stem}.md", "/etc/{stem}.md", "docs/../../{stem}.md"]
)
def test_expand_writes_refuses_a_template_that_leaves_the_worktree(template):
    """Review Focus: a document path input must name a file inside the worktree."""
    with pytest.raises(EngineError) as caught:
        prompt.expand_writes(template, CARD, phase="spec", input_name="spec_path")

    assert "leaves the worktree" in str(caught.value)


def _repo_docs_phase():
    return _phase(["repo_docs"], name="explore", role="explorer")


def test_repo_docs_renders_the_path_and_the_whole_file_when_it_is_short(tmp_path):
    """Review Focus: a file at or under the limit is not labelled truncated."""
    (tmp_path / "CLAUDE.md").write_text(
        "\n".join(f"line {n}" for n in range(1, prompt.REPO_DOC_LINES + 1)) + "\n",
        encoding="utf-8",
    )

    rendered = prompt.render_prompt(_repo_docs_phase(), _context(repo_dir=tmp_path))

    body = _section(rendered, "repo_docs")
    assert str(tmp_path / "CLAUDE.md") in body
    assert "shown in full" in body
    assert "more" not in body
    assert f"line {prompt.REPO_DOC_LINES}" in body


def test_repo_docs_truncates_a_long_file_and_says_so(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "\n".join(f"line {n}" for n in range(1, 101)) + "\n", encoding="utf-8"
    )

    rendered = prompt.render_prompt(_repo_docs_phase(), _context(repo_dir=tmp_path))

    body = _section(rendered, "repo_docs")
    assert f"first {prompt.REPO_DOC_LINES} of 100 lines" in body
    assert f"line {prompt.REPO_DOC_LINES}" in body
    assert f"line {prompt.REPO_DOC_LINES + 1}" not in body
    assert "60 more" in body


def test_repo_docs_includes_both_files_in_a_fixed_order(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("the claude doc\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("the agents doc\n", encoding="utf-8")

    body = _section(
        prompt.render_prompt(_repo_docs_phase(), _context(repo_dir=tmp_path)), "repo_docs"
    )

    assert body.index("the claude doc") < body.index("the agents doc")


def test_repo_docs_reads_repo_dir_while_the_subtask_has_no_worktree_yet(tmp_path):
    """`explore` is the first phase; the `worktree` phase runs two phases later."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("from the repo\n", encoding="utf-8")

    body = _section(
        prompt.render_prompt(
            _repo_docs_phase(),
            _context(repo_dir=repo, worktree=tmp_path / "worktrees" / "not-made-yet"),
        ),
        "repo_docs",
    )

    assert "from the repo" in body


def test_repo_docs_reads_the_worktree_root_once_it_exists(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("from the repo\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "CLAUDE.md").write_text("from the worktree\n", encoding="utf-8")

    body = _section(
        prompt.render_prompt(
            _repo_docs_phase(), _context(repo_dir=repo, worktree=worktree)
        ),
        "repo_docs",
    )

    assert "from the worktree" in body
    assert "from the repo" not in body


def test_repo_docs_with_neither_file_states_the_absence(tmp_path):
    body = _section(
        prompt.render_prompt(_repo_docs_phase(), _context(repo_dir=tmp_path)), "repo_docs"
    )

    assert body == f"no CLAUDE.md or AGENTS.md at {tmp_path}"


def test_an_unreadable_repo_doc_is_an_engine_error_naming_the_phase(tmp_path):
    (tmp_path / "CLAUDE.md").write_bytes(b"\xff\xfe not utf-8 \xff")

    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(_repo_docs_phase(), _context(repo_dir=tmp_path))

    assert caught.value.phase == "explore"
    assert caught.value.parameter == "repo_docs"
    assert "CLAUDE.md" in str(caught.value)


def test_plan_hash_renders_the_digest_the_docs_commit_step_returned():
    """§7: a hash is a short string, so it is inlined exactly like `branch`."""
    rendered = prompt.render_prompt(_phase(["plan_hash"]), _context())

    assert _section(rendered, "plan_hash") == PLAN_HASH
    assert rendered.text.endswith(f"\n## plan_hash\n{PLAN_HASH}\n")


def test_a_phase_that_does_not_declare_plan_hash_needs_no_docs_commit_key():
    """Lazy per declared name: `explore`, `spec`, `plan`, `validate_*` and
    `review` all run before or without `docs_commit`, so resolution must never
    be attempted for a name the phase did not ask for."""
    context = _context()
    del context["docs_commit"]

    rendered = prompt.render_prompt(
        _phase(["spec_path", "plan_path"], name="plan", role="planner"), context
    )

    assert rendered.inputs == ("spec_path", "plan_path")
    assert "plan_hash" not in rendered.text


def test_plan_hash_without_a_docs_commit_result_names_the_context_key():
    context = _context()
    del context["docs_commit"]

    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(_phase(["plan_hash"]), context)

    assert caught.value.phase == "implement"
    assert caught.value.parameter == "plan_hash"
    assert "nothing in the context supplies it" in str(caught.value)
    assert "docs_commit" in str(caught.value)


@pytest.mark.parametrize("result", [{}, {"plan_hash": None}, {"digest": "9f3a12bc"}])
def test_a_docs_commit_result_without_the_digest_is_a_step_contract_breach(result):
    """Distinct from the missing-key case: the phase ran and returned something,
    but that something did not carry `plan_hash`."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(_phase(["plan_hash"]), _context(docs_commit=result))

    assert caught.value.phase == "implement"
    assert caught.value.parameter == "plan_hash"
    assert "supplied no 'plan_hash'" in str(caught.value)
    assert "nothing in the context supplies it" not in str(caught.value)


def test_a_docs_commit_result_that_is_not_a_mapping_is_an_engine_error():
    """Review Focus: a step that started returning a bare string must produce a
    named EngineError, not an AttributeError out of the renderer."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(_phase(["plan_hash"]), _context(docs_commit="9f3a12bc"))

    assert caught.value.parameter == "plan_hash"
    assert "not a mapping" in str(caught.value)


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_plan_hash_from_docs_commit_is_refused(blank):
    """Review Focus: an empty section would be stamped as a blank trailer."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(
            _phase(["plan_hash"]), _context(docs_commit={"plan_hash": blank})
        )

    assert caught.value.parameter == "plan_hash"
    assert "supplied no 'plan_hash'" in str(caught.value)


THIRTEEN_INPUTS = [
    "card",
    "parent_story",
    "repo_docs",
    "explore",
    "spec_path",
    "plan_path",
    "branch",
    "base_branch",
    "verification",
    "plan_hash",
    "merge_tip",
    "conflict_files",
    "feedback",
]

MERGE_TIP = "m5/story-the-resolver-5216cbee"
CONFLICT_FILES = ["src/agent_manager/prompt.py", "tests/test_prompt.py"]

FEEDBACK_ITEM = {"for": "spec", "from": "validate_spec", "detail": "no error path"}


def test_the_table_carries_exactly_the_thirteen_names_section_7_integrate_and_feedback():
    """§7's table is fixed. A fourteenth name is a design change, not a code change.

    `plan_hash` is the tenth, added by card f26b377d together with its row in
    §7's table in `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.
    `merge_tip` and `conflict_files` are the eleventh and twelfth, the resolver's
    inputs, which the Integrate addendum
    (`docs/superpowers/specs/2026-09-25-integrate-design.md` §2) says must be
    added to this table (card b4bd3795). `feedback` is the thirteenth, the
    critic's reason on a Goto loop-back, which the pygents-engine design
    (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md` G4, §5) adds
    (card b904b9e7). It is given one item here, because an empty list omits
    its section.
    """
    rendered = prompt.render_prompt(
        _phase(THIRTEEN_INPUTS),
        _context(
            merge_tip=MERGE_TIP,
            conflict_files=list(CONFLICT_FILES),
            feedback=[dict(FEEDBACK_ITEM)],
        ),
    )

    assert rendered.inputs == tuple(THIRTEEN_INPUTS)
    assert sorted(prompt._TABLE) == sorted(rendered.inputs)


def test_merge_tip_renders_the_tip_ref_verbatim():
    rendered = prompt.render_prompt(
        _phase(["merge_tip"], name="resolve", role="resolver"),
        _context(merge_tip=MERGE_TIP),
    )

    assert _section(rendered, "merge_tip") == MERGE_TIP
    assert f"\n## merge_tip\n{MERGE_TIP}\n" in rendered.text


def test_conflict_files_inlines_the_list_as_json():
    rendered = prompt.render_prompt(
        _phase(["conflict_files"], name="resolve", role="resolver"),
        _context(conflict_files=list(CONFLICT_FILES)),
    )

    body = _section(rendered, "conflict_files")
    assert json.loads(body) == CONFLICT_FILES
    assert body == json.dumps(CONFLICT_FILES, indent=2, ensure_ascii=False)


def test_conflict_file_names_with_spaces_quotes_and_non_ascii_round_trip_exactly():
    """Review Focus: the resolver parses this list back out of its brief, so a
    path git reports verbatim must come back out of the JSON verbatim."""
    files = ["docs/release notes.md", "src/café.py", 'notes/"quoted".txt']
    rendered = prompt.render_prompt(
        _phase(["conflict_files"], name="resolve", role="resolver"),
        _context(conflict_files=files),
    )

    body = _section(rendered, "conflict_files")
    assert json.loads(body) == files
    assert "café" in body  # ensure_ascii=False: no \u escapes in the brief


def test_a_conflict_files_key_that_was_never_populated_is_named_as_such():
    """Review Focus: `None` is a caller bug, never a `null` in the brief."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(
            _phase(["conflict_files"], name="resolve", role="resolver"),
            _context(conflict_files=None),
        )

    assert caught.value.phase == "resolve"
    assert caught.value.parameter == "conflict_files"
    assert "never populated" in str(caught.value)


def test_a_context_without_merge_tip_names_the_key_it_reads():
    """Spec error path: an `extra_context` that omits `merge_tip` fails with the
    resolver's existing missing-key error, nothing new."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(
            _phase(["merge_tip"], name="resolve", role="resolver"), _context()
        )

    assert caught.value.phase == "resolve"
    assert caught.value.parameter == "merge_tip"
    assert "nothing in the context supplies it" in str(caught.value)


def test_a_context_without_conflict_files_names_the_key_it_reads():
    """Spec error path: an `extra_context` that omits `conflict_files` fails
    with the resolver's existing missing-key error, nothing new."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(
            _phase(["conflict_files"], name="resolve", role="resolver"), _context()
        )

    assert caught.value.phase == "resolve"
    assert caught.value.parameter == "conflict_files"
    assert "nothing in the context supplies it" in str(caught.value)


def test_the_producer_map_is_derived_from_the_table_it_describes():
    """A resolver that reads another phase's result is a resume dependency:
    `cli.resume_start_phase` has to back off over that phase. The mapping is
    published from the table itself, so it cannot disagree with the resolver."""
    assert prompt.INPUT_PRODUCERS == {"plan_hash": "docs_commit"}
    assert set(prompt.INPUT_PRODUCERS) <= set(prompt._TABLE)


def test_write_puts_utf8_prompt_text_in_the_attempt_directory(tmp_path):
    rendered = prompt.render_prompt(_phase(["branch"]), _context())

    written = rendered.write(tmp_path)

    assert written == tmp_path / "prompt.txt"
    assert written.read_text(encoding="utf-8") == rendered.text
    assert written.read_bytes() == rendered.text.encode("utf-8")


def test_write_overwrites_a_prompt_left_by_an_earlier_attempt(tmp_path):
    (tmp_path / "prompt.txt").write_text("from attempt 1\n", encoding="utf-8")
    rendered = prompt.render_prompt(_phase(["branch"]), _context())

    written = rendered.write(tmp_path)

    assert written.read_text(encoding="utf-8") == rendered.text
    assert "from attempt 1" not in written.read_text(encoding="utf-8")


def test_write_does_not_create_the_directory_and_says_which_one_was_missing(tmp_path):
    missing = tmp_path / "runs" / "r1" / "explore.1"
    rendered = prompt.render_prompt(_phase(["branch"]), _context())

    with pytest.raises(EngineError) as caught:
        rendered.write(missing)

    assert str(missing) in str(caught.value)
    assert caught.value.phase == "implement"
    assert not missing.exists()


def test_the_feedback_heading_lives_in_prompt_and_dispatch_reuses_it():
    from agent_manager import dispatch

    assert prompt.FEEDBACK_HEADING == "## feedback on the previous attempt"
    assert dispatch.FEEDBACK_HEADING is prompt.FEEDBACK_HEADING


BRIEF_POLICY = roles_loader.Policy(
    allowed_tools=["Read", "Edit"],
    default_model={"claude": "sonnet"},
    max_attempts=2,
)

TDD_BODY = "# Test-driven development\n\n## Red\n\nWrite the failing test.\n"
PLANS_BODY = "# Writing plans\n\nBite-sized steps, real code in every step.\n"


def _role(
    name: str = "coder",
    *,
    system: str = "# Coder\n\nYou execute an implementation plan under strict TDD.\n",
    methodology: dict[str, str] | None = None,
) -> roles_loader.RoleBundle:
    """A synthetic bundle: the composer only reads `system` and `methodology`."""
    return roles_loader.RoleBundle(
        name=name,
        system=system,
        policy=BRIEF_POLICY,
        methodology=dict(methodology or {}),
    )


def _rendered(inputs=("branch", "base_branch")) -> prompt.RenderedPrompt:
    return prompt.render_prompt(_phase(list(inputs)), _context())


def test_the_brief_orders_system_then_methodology_then_the_rendered_prompt():
    role = _role(
        methodology={
            "test-driven-development.md": TDD_BODY,
            "writing-plans.md": PLANS_BODY,
        }
    )
    rendered = _rendered()

    brief = prompt.compose_brief(role, rendered)

    positions = [
        brief.index("# Coder"),
        brief.index("## methodology: test-driven-development.md"),
        brief.index("## methodology: writing-plans.md"),
        brief.index("# phase: implement"),
    ]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)


def test_each_methodology_file_gets_its_own_heading_and_a_verbatim_body():
    role = _role(
        methodology={
            "test-driven-development.md": TDD_BODY,
            "writing-plans.md": PLANS_BODY,
        }
    )

    brief = prompt.compose_brief(role, _rendered())

    assert "## methodology: test-driven-development.md\n# Test-driven development" in brief
    assert "## methodology: writing-plans.md\n# Writing plans" in brief
    assert TDD_BODY.strip("\n") in brief
    assert PLANS_BODY.strip("\n") in brief


def test_a_role_with_no_methodology_puts_the_rendered_prompt_after_system_and_the_process_safety_block():
    role = _role(name="reviewer", system="# Reviewer\n\nYou review finished work.\n")

    brief = prompt.compose_brief(role, _rendered())

    assert prompt.METHODOLOGY_HEADING_PREFIX not in brief
    assert brief.startswith(
        "# Reviewer\n\nYou review finished work.\n\n"
        + prompt.PROCESS_SAFETY_BLOCK
        + "\n\n# phase: implement\n"
    )


def test_the_brief_carries_the_rendered_text_verbatim_and_ends_in_one_newline():
    rendered = _rendered()

    brief = prompt.compose_brief(_role(), rendered)

    assert rendered.text.strip("\n") in brief
    assert brief.endswith("\n")
    assert not brief.endswith("\n\n")


def test_two_roles_produce_different_briefs_from_the_same_rendered_prompt():
    rendered = _rendered()
    coder = _role(methodology={"test-driven-development.md": TDD_BODY})
    critic = _role(name="spec_critic", system="# Spec critic\n\nYou adversarially review.\n")

    coder_brief = prompt.compose_brief(coder, rendered)
    critic_brief = prompt.compose_brief(critic, rendered)

    assert coder_brief != critic_brief
    assert "# Coder" in coder_brief and "# Spec critic" not in coder_brief
    assert "# Spec critic" in critic_brief and "# Coder" not in critic_brief


def test_composing_twice_is_byte_identical():
    role = _role(methodology={"writing-plans.md": PLANS_BODY})
    rendered = _rendered()

    first = prompt.compose_brief(role, rendered)
    second = prompt.compose_brief(role, rendered)

    assert first == second


def test_trailing_blank_lines_in_system_text_collapse_to_one_separator():
    role = _role(system="# Coder\n\nDo the work.\n\n\n\n")

    brief = prompt.compose_brief(role, _rendered())

    assert brief.startswith("# Coder\n\nDo the work.\n\n" + prompt.PROCESS_SAFETY_BLOCK + "\n\n")


def test_system_text_with_no_trailing_newline_still_gets_one_blank_line():
    role = _role(system="# Coder\n\nDo the work.")

    brief = prompt.compose_brief(role, _rendered())

    assert brief.startswith("# Coder\n\nDo the work.\n\n" + prompt.PROCESS_SAFETY_BLOCK + "\n\n")


def test_a_methodology_body_keeps_its_own_headings_and_interior_blank_lines():
    body = "# Writing plans\n\n## Step one\n\nWrite the test.\n\n## Step two\n\nRun it.\n"
    role = _role(methodology={"writing-plans.md": body})

    brief = prompt.compose_brief(role, _rendered())

    assert "## methodology: writing-plans.md\n" + body.strip("\n") in brief


CARD_ROLES = {"coder", "resolver", "explorer", "reviewer"}
"""The roles card 913e748a names; parametrizing over `list_roles()` must keep them."""

assert CARD_ROLES <= set(roles_loader.list_roles())

PHASE_LINE = re.compile(r"^# phase: ", re.M)
"""Any line fake claude's `PHASE_HEADER` (`tests/e2e/fake_claude.py`) could anchor on."""


def test_the_process_safety_block_names_every_forbidden_kill_and_the_safe_alternatives():
    block = prompt.PROCESS_SAFETY_BLOCK

    assert block.startswith("## Process safety\n")
    assert block == block.strip("\n")
    for literal in (
        "`pkill -f`",
        "`pkill` by name",
        "`killall`",
        "`kill -1`",
        "`kill` with a pattern",
        "`timeout`",
        "PID",
        "started",
        "recorded",
    ):
        assert literal in block, literal


def test_the_process_safety_block_cannot_be_mistaken_for_brief_structure():
    block = prompt.PROCESS_SAFETY_BLOCK

    lines = block.splitlines()
    assert not any(line.startswith("# phase:") for line in lines)
    assert not any(line.startswith("# role:") for line in lines)
    assert prompt.RESULT_HEADING not in block
    assert "```json" not in block
    assert prompt.METHODOLOGY_HEADING_PREFIX not in block
    assert prompt.FEEDBACK_HEADING not in block
    assert "${" not in block


@pytest.mark.parametrize("name", roles_loader.list_roles())
def test_every_shipped_role_brief_carries_the_process_safety_block_once(name):
    role = roles_loader.load_role(name)
    rendered = _rendered()

    brief = prompt.compose_brief(role, rendered)

    block = prompt.PROCESS_SAFETY_BLOCK
    assert brief.count(block) == 1
    assert brief.index(role.system.strip("\n")) == 0
    assert 0 < brief.index(block) < brief.index(rendered.text.strip("\n"))
    if role.methodology:
        first_heading = prompt.METHODOLOGY_HEADING_PREFIX + next(iter(role.methodology))
        # Searched after the system text: shipped system.md files mention the
        # `## methodology:` heading inline when they tell the agent to follow it.
        assert brief.index(block) < brief.index(
            "\n\n" + first_heading + "\n", len(role.system.strip("\n"))
        )
    assert len(PHASE_LINE.findall(brief)) == 1
    assert brief == prompt.compose_brief(role, rendered)


def test_the_process_safety_block_sits_between_system_and_methodology():
    role = _role(
        methodology={
            "test-driven-development.md": TDD_BODY,
            "writing-plans.md": PLANS_BODY,
        }
    )

    brief = prompt.compose_brief(role, _rendered())

    positions = [
        brief.index("# Coder"),
        brief.index(prompt.PROCESS_SAFETY_BLOCK),
        brief.index("## methodology: test-driven-development.md"),
        brief.index("# phase: implement"),
    ]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)


def test_the_process_safety_block_is_present_without_methodology_contract_or_feedback():
    system = "# Reviewer\n\nYou review finished work."
    role = _role(name="reviewer", system=system)

    brief = prompt.compose_brief(role, _rendered())

    assert brief.startswith(
        system + "\n\n" + prompt.PROCESS_SAFETY_BLOCK + "\n\n# phase: implement\n"
    )
    assert brief.count(prompt.PROCESS_SAFETY_BLOCK) == 1


@pytest.mark.parametrize("system", ["\n", "\n\n"])
def test_an_empty_system_text_brief_starts_with_the_process_safety_block(system):
    brief = prompt.compose_brief(_role(system=system), _rendered())

    assert brief.startswith(prompt.PROCESS_SAFETY_BLOCK + "\n\n# phase: implement\n")
    assert brief.count(prompt.PROCESS_SAFETY_BLOCK) == 1


def _shipped_coder_brief() -> str:
    """The real coder bundle's brief for a real `implement` render.

    Asserted through `compose_brief` rather than by reading `system.md`, so the
    loader path (`roles/loader.py` -> `RoleBundle.system` -> the brief's first
    part) is covered too: standing instructions that never reach the brief are
    standing instructions no agent ever reads.
    """
    rendered = prompt.render_prompt(
        _phase(["plan_path", "spec_path", "branch", "base_branch", "plan_hash"]),
        _context(),
    )
    return prompt.compose_brief(roles_loader.load_role("coder"), rendered)


def test_the_coder_brief_states_the_trailer_rule_beside_the_hash_it_must_use():
    brief = _shipped_coder_brief()

    assert f"\n## plan_hash\n{PLAN_HASH}\n" in brief
    assert "`Plan-Hash: <hash>`" in brief
    assert "Never compute the hash yourself" in brief
    assert "Never leave a commit untagged" in brief


def test_the_coder_brief_states_the_resume_rule_and_who_commits_the_documents():
    """Main design §9 line 379: commits carrying the current hash are resumed
    from, untagged ones are debris the coder reports rather than rewrites."""
    brief = _shipped_coder_brief()

    assert "git log <base_branch>..HEAD" in brief
    assert "Continue from the next uncompleted plan step" in brief
    assert "`blocked: true`" in brief
    assert "`blocked_reason`" in brief
    assert "Never rewrite, amend, squash or delete them." in brief
    assert "committed by the engine" in brief
    assert "unless this repository git-ignores them" in brief
    assert "do not add, force-add, commit or amend" in brief


class StandInResult(BaseModel):
    """Stands in for a `results.RESULT_MODELS` entry, which is story 5cc741ec's."""

    summary: str
    files: list[str] = []


def _fenced_json(brief: str) -> dict:
    """The one ```json block the contract embeds, parsed back."""
    body = brief.split("```json\n", 1)[1].split("\n```", 1)[0]
    return json.loads(body)


def test_the_contract_states_the_absolute_path_and_embeds_the_real_schema():
    brief = prompt.compose_brief(
        _role(),
        _rendered(),
        result_path=Path("/var/agent-manager/runs/r1/card/implement.1/result.json"),
        result_model=StandInResult,
    )

    assert prompt.RESULT_HEADING == "## Result contract"
    assert "\n## Result contract\n" in brief
    assert "/var/agent-manager/runs/r1/card/implement.1/result.json" in brief
    assert _fenced_json(brief) == StandInResult.model_json_schema()
    assert "summary" in _fenced_json(brief)["properties"]


def test_the_spec_authors_contract_names_its_path_and_embeds_the_real_schema():
    brief = prompt.compose_brief(
        _role("spec_author"),
        _rendered(),
        result_path=Path("/var/agent-manager/runs/r1/card/spec.1/result.json"),
        result_model=results.SpecResult,
    )

    assert "\n## Result contract\n" in brief
    assert "/var/agent-manager/runs/r1/card/spec.1/result.json" in brief
    assert _fenced_json(brief) == results.SpecResult.model_json_schema()
    assert set(_fenced_json(brief)["properties"]) == {"path", "note"}


@pytest.mark.parametrize("name", sorted(results.RESULT_MODELS))
def test_every_shipped_result_model_embeds_its_own_schema_in_the_contract(name):
    model = results.RESULT_MODELS[name]
    result_path = f"/var/agent-manager/runs/r1/card/{name}.1/result.json"

    brief = prompt.compose_brief(
        _role(), _rendered(), result_path=Path(result_path), result_model=model
    )

    assert result_path in brief
    assert _fenced_json(brief) == model.model_json_schema()


def test_the_contract_says_write_valid_json_and_stay_out_of_the_worktree():
    brief = prompt.compose_brief(
        _role(),
        _rendered(),
        result_path=Path("/var/agent-manager/runs/r1/card/implement.1/result.json"),
        result_model=StandInResult,
    )
    contract = brief.split(prompt.RESULT_HEADING, 1)[1]

    assert "valid JSON" in contract
    assert "outside the worktree" in contract


def test_the_contract_lands_after_the_rendered_prompt():
    brief = prompt.compose_brief(
        _role(methodology={"writing-plans.md": PLANS_BODY}),
        _rendered(),
        result_path=Path("/runs/r1/implement.1/result.json"),
        result_model=StandInResult,
    )

    assert brief.index("# phase: implement") < brief.index(prompt.RESULT_HEADING)


def test_a_phase_with_no_result_gets_no_contract_section():
    brief = prompt.compose_brief(_role(), _rendered())

    assert prompt.RESULT_HEADING not in brief
    assert "```json" not in brief


def test_a_result_path_given_as_a_string_is_accepted_and_rendered():
    brief = prompt.compose_brief(
        _role(),
        _rendered(),
        result_path="/runs/r1/implement.1/result.json",
        result_model=StandInResult,
    )

    assert "/runs/r1/implement.1/result.json" in brief


def test_a_schema_with_non_ascii_prose_is_embedded_unescaped():
    class Unicode(BaseModel):
        summary: str = Field(description="the finding — in one line")

    brief = prompt.compose_brief(
        _role(),
        _rendered(),
        result_path="/runs/r1/implement.1/result.json",
        result_model=Unicode,
    )

    assert "the finding — in one line" in brief
    assert "\\u2014" not in brief
    assert _fenced_json(brief) == Unicode.model_json_schema()


def test_a_result_path_without_a_model_is_refused():
    with pytest.raises(EngineError) as error:
        prompt.compose_brief(
            _role(), _rendered(), result_path="/runs/r1/implement.1/result.json"
        )

    assert "implement" in str(error.value)
    assert error.value.phase == "implement"
    assert "result_model" in str(error.value)


def test_a_result_model_without_a_path_is_refused():
    with pytest.raises(EngineError) as error:
        prompt.compose_brief(_role(), _rendered(), result_model=StandInResult)

    assert error.value.phase == "implement"
    assert "result_path" in str(error.value)


def test_a_relative_result_path_is_refused():
    with pytest.raises(EngineError) as error:
        prompt.compose_brief(
            _role(),
            _rendered(),
            result_path="attempts/implement.1/result.json",
            result_model=StandInResult,
        )

    assert error.value.phase == "implement"
    assert "attempts/implement.1/result.json" in str(error.value)


def test_composing_a_contract_creates_no_file(tmp_path):
    prompt.compose_brief(
        _role(),
        _rendered(),
        result_path=tmp_path / "implement.1" / "result.json",
        result_model=StandInResult,
    )

    assert list(tmp_path.iterdir()) == []


FEEDBACK = "The verify step failed: two tests error on a missing fixture."


def test_feedback_is_the_last_section_and_comes_after_the_contract():
    brief = prompt.compose_brief(
        _role(),
        _rendered(),
        result_path="/runs/r1/implement.1/result.json",
        result_model=StandInResult,
        feedback=FEEDBACK,
    )

    assert brief.index(prompt.RESULT_HEADING) < brief.index(prompt.FEEDBACK_HEADING)
    assert brief.endswith(f"{prompt.FEEDBACK_HEADING}\n{FEEDBACK}\n")


def test_without_feedback_the_heading_is_absent():
    brief = prompt.compose_brief(
        _role(),
        _rendered(),
        result_path="/runs/r1/implement.1/result.json",
        result_model=StandInResult,
    )

    assert prompt.FEEDBACK_HEADING not in brief


def test_the_brief_without_feedback_is_a_prefix_of_the_brief_with_it():
    role = _role(methodology={"test-driven-development.md": TDD_BODY})
    rendered = _rendered()
    contract = {
        "result_path": "/runs/r1/implement.1/result.json",
        "result_model": StandInResult,
    }

    base = prompt.compose_brief(role, rendered, **contract)
    retry = prompt.compose_brief(role, rendered, **contract, feedback=FEEDBACK)

    assert retry.startswith(base)
    assert retry.count(prompt.RESULT_HEADING) == 1


@pytest.mark.parametrize("blank", ["", "   ", "\n\n", " \t\n "])
def test_blank_feedback_produces_no_feedback_section(blank):
    brief = prompt.compose_brief(_role(), _rendered(), feedback=blank)

    assert prompt.FEEDBACK_HEADING not in brief
    assert brief == prompt.compose_brief(_role(), _rendered())


def test_feedback_that_already_ends_in_newlines_does_not_accumulate_blank_lines():
    role = _role()
    rendered = _rendered()

    base = prompt.compose_brief(role, rendered)
    retry = prompt.compose_brief(role, rendered, feedback=FEEDBACK + "\n\n\n")

    assert retry.startswith(base)
    assert retry.endswith(f"{prompt.FEEDBACK_HEADING}\n{FEEDBACK}\n")


def test_feedback_still_appends_after_the_process_safety_block():
    from agent_manager import dispatch

    role = _role(methodology={"test-driven-development.md": TDD_BODY})
    rendered = _rendered()

    base = prompt.compose_brief(role, rendered)
    retry = prompt.compose_brief(role, rendered, feedback=FEEDBACK)
    dispatched_retry = dispatch._append_feedback(base, [FEEDBACK, FEEDBACK])

    assert retry.startswith(base)
    assert retry.count(prompt.PROCESS_SAFETY_BLOCK) == 1
    assert retry.index(prompt.PROCESS_SAFETY_BLOCK) < retry.index(prompt.FEEDBACK_HEADING)
    assert dispatched_retry.startswith(base)
    assert dispatched_retry.count(prompt.PROCESS_SAFETY_BLOCK) == 1


def test_input_names_are_exactly_the_resolver_table():
    assert isinstance(prompt.INPUT_NAMES, frozenset)
    assert prompt.INPUT_NAMES == frozenset(prompt._TABLE)
    assert "card" in prompt.INPUT_NAMES


def test_prompt_reads_phases_through_a_protocol_not_the_yaml_type():
    # Spec: render_prompt, _assemble and _Request are retyped to a Protocol
    # carrying name, role and inputs, and the loader import is dropped.
    assert "AgentPhase" not in vars(prompt)
    assert inspect.signature(prompt.render_prompt).parameters["phase"].annotation is (
        prompt.PromptPhase
    )
    assert all(
        isinstance(getattr(prompt.PromptPhase, attr), property)
        for attr in ("name", "role", "inputs")
    )


FEEDBACK_PHASE = SimpleNamespace(name="spec", role="spec_author", inputs=("feedback",))


def test_feedback_renders_each_item_and_nothing_when_empty():
    text = prompt.render_prompt(FEEDBACK_PHASE, {"feedback": [dict(FEEDBACK_ITEM)]}).text
    assert "Feedback from review" in text and "validate_spec: no error path" in text

    empty = prompt.render_prompt(FEEDBACK_PHASE, {"feedback": []})
    assert "Feedback from review" not in empty.text
    assert "## feedback" not in empty.text
    assert "feedback" not in dict(empty.sections)
    assert empty.text == "# phase: spec\n# role: spec_author\n"

    two = prompt.render_prompt(
        FEEDBACK_PHASE,
        {
            "feedback": [
                dict(FEEDBACK_ITEM),
                {"for": "spec", "from": "validate_spec", "detail": "no rollback step"},
            ]
        },
    )
    assert _section(two, "feedback") == (
        "Feedback from review\n"
        "- validate_spec: no error path\n"
        "- validate_spec: no rollback step"
    )

    for context in ({}, {"feedback": None}):
        missing = prompt.render_prompt(FEEDBACK_PHASE, context)
        assert missing.sections == ()
        assert "feedback" not in dict(missing.sections)
        assert missing.text == "# phase: spec\n# role: spec_author\n"


def test_feedback_renders_under_its_own_section_heading_and_omits_the_for_key():
    rendered = prompt.render_prompt(FEEDBACK_PHASE, {"feedback": [dict(FEEDBACK_ITEM)]})

    assert rendered.inputs == ("feedback",)
    assert rendered.text == (
        "# phase: spec\n"
        "# role: spec_author\n"
        "\n"
        "## feedback\n"
        "Feedback from review\n"
        "- validate_spec: no error path\n"
    )


def test_feedback_keeps_a_multi_line_detail_verbatim():
    """Review Focus: gate verdicts span lines; the bullet must carry all of them."""
    detail = "no error path\nand the rollback section is empty"
    rendered = prompt.render_prompt(
        FEEDBACK_PHASE,
        {"feedback": [{"for": "spec", "from": "validate_spec", "detail": detail}]},
    )

    assert _section(rendered, "feedback") == (
        "Feedback from review\n- validate_spec: no error path\nand the rollback section is empty"
    )


def test_an_omitted_feedback_section_leaves_its_neighbours_untouched():
    """Review Focus: an old-engine context has no `feedback` key at all."""
    with_feedback = prompt.render_prompt(_phase(["branch", "feedback", "base_branch"]), _context())
    without = prompt.render_prompt(_phase(["branch", "base_branch"]), _context())

    assert with_feedback.text == without.text
    assert with_feedback.sections == without.sections


@pytest.mark.parametrize(
    "item",
    [
        {"for": "spec", "detail": "no error path"},
        {"for": "spec", "from": "validate_spec"},
        "validate_spec: no error path",
    ],
)
def test_a_feedback_item_without_from_or_detail_is_refused(item):
    """Review Focus: a malformed item is a codec bug, never a blank bullet."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(FEEDBACK_PHASE, {"feedback": [item]})

    assert caught.value.phase == "spec"
    assert caught.value.parameter == "feedback"
    assert "'from'" in str(caught.value) and "'detail'" in str(caught.value)


def test_feedback_is_not_a_producer_input_and_prompt_imports_no_pygents():
    assert "feedback" in prompt.INPUT_NAMES
    assert "feedback" not in prompt.INPUT_PRODUCERS

    tree = ast.parse(Path(prompt.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(name.split(".")[0] == "pygents" for name in imported)


def test_the_real_review_brief_carries_the_hash_docs_commit_recorded():
    from agent_manager.workflow.task import TASK

    review = TASK.phase("review")
    rendered = prompt.render_prompt(review, _context())

    assert _section(rendered, "plan_hash") == PLAN_HASH
