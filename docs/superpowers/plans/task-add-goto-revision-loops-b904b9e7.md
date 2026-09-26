<!-- task-pipeline: validated -->
# Subtask b904b9e7: Add Goto revision loops and the feedback input

Story f9c19dc3 ("Run a workflow on pygents"), milestone 84c3b532. This narrows plan Task 3.5 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1117-1156`) and the engine design spec's G4 and §5 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:86-89, 226-231, 257-259`).

Note: the exploration summary for this card was cut off at 8000 characters, partway through its file:line reference list (it ended mid-reference to the `test_prompt` part of Task 3.5). This spec does not guess at the missing text. Anything below that goes beyond the surviving findings was checked directly against the plan and the code in this worktree.

## Scope

The loop mechanics are already on this branch. In `src/agent_manager/runtime/compile.py:120-128`, `agent_phase` catches `AgentPhaseFailed`. If `on_fail` is set and `loop < on_fail.max_loops`, it yields `ContextItem({"for", "from", "detail"})` and then `turn_for(on_fail.phase, loop + 1)`. Otherwise it raises `Escalated(phase, detail)`. `runtime/context.py:82` `binding_table` already builds `table["feedback"]`. This card owns:

1. **`prompt._TABLE["feedback"]`**, a new resolver in `src/agent_manager/prompt.py`. It has the same `Resolver` shape as `_verbatim` (`prompt.py:146-158`) and is registered in the §7 table (`prompt.py:286-299`), so `INPUT_NAMES` picks it up automatically. It is not a `_phase_field`, so it sets no `produced_by`, and `INPUT_PRODUCERS` stays unchanged.
2. **A narrow change to `render_prompt`'s section-building loop** (`prompt.py`, the `for name in phase.inputs` loop around line 337, not `_assemble`), so a resolver can opt a phase out of a section entirely rather than only control its body text. Verified against the current code: today `render_prompt` unconditionally does `sections.append((name, resolver(request)))` for every declared input, so a resolver that returns `""` for an empty feedback list would still leave a `("feedback", "")` entry in `RenderedPrompt.sections` and a bare `## feedback` heading with an empty body in `.text` — the opposite of the "Empty or missing list" behaviour required below. Add the convention that a resolver may return `None` to mean "no section for this input", and have the loop skip appending (and skip adding `name` to `seen`'s section, though dedup on `phase.inputs` is unaffected) when `resolver(request) is None`. `Resolver`'s type alias becomes `Callable[[_Request], str | None]`. This is additive only: every existing resolver in `_TABLE` always returns `str`, never `None`, so no existing input's rendering changes, and no YAML phase today declares `feedback` as an input, so the YAML engine's output is unaffected. This does not touch `_assemble`, `RenderedPrompt`'s fields, resolution order, dedup, or error paths, so it does not conflict with 1bbb532d's Protocol-typing widening of the same file (that card's own scope note already anticipates both cards editing `prompt.py`).
3. **Full-stack loop tests** proving the existing branch delivers critic feedback into the looped-to phase. It must escalate on a second failure and carry `loop=1` on the queued turn.
4. **Fixes to `compile.py`'s loop branch only if those tests expose a bug** (plan Step 3). No restructuring.

Out of scope, and owned by sibling or later cards:
- the pool codec and `binding_table` (8ae25085)
- `compile.py`'s structure, `state.py` and `bridge.py` (023d918e)
- `run_subtask` and the both-engine parity tests (2853e536)
- the Protocol typing in `render_prompt` (1bbb532d) — this card's own touch to `render_prompt` (Scope item 2) is limited to the section-omission convention and leaves the `phase` parameter's type alone
- wiring `on_fail=Goto(...)` onto `validate_spec`/`validate_plan` and adding `feedback` to the `spec`/`plan` inputs in the built-in task workflow, plus the fake-claude "critic blocks once" mode. That is a later plan task (plan line 1325). `builtin/task.yaml` is not touched here.
- The unrelated `FEEDBACK_HEADING` (`prompt.py:59`) and `dispatch.with_feedback` retry block stay as they are. The two concepts must not be merged.

## Observable behaviour

- **Normal case.** For a phase declaring `feedback` in `inputs`, the context holds a non-empty list of `{"for", "from", "detail"}` dicts. The rendered prompt then has a `feedback` section whose body starts with the title line "Feedback from review", followed by one bullet per item, in list order, formatted `- {from}: {detail}`. Example: `- validate_spec: no error path`. The `for` key is not rendered, because `binding_table` has already filtered on it.
- **Empty or missing list.** The resolver returns `None` (Scope item 2's convention) when the key is missing, `None`, or an empty list, so `render_prompt` omits the section entirely. The key is always missing on the old engine, whose context never carries it. In that case the prompt contains neither "Feedback from review" nor a dangling `## feedback` heading, and `RenderedPrompt.sections` has no `feedback` entry. The resolver must never go through `_required`/`_present`, because a missing key is normal here and not an `EngineError`. Omitting the empty section must not change how any other input renders.
- The resolver reads only plain dict/list content. `prompt.py` must not import pygents (story rule 1).
- The item shape `{for, from, detail}`, the phase rows, journal lines, `SubtaskSummary` and escalation payloads all stay unchanged (G10). A loop-back leaves the failed critic's phase row exactly as the existing escalation path would write it before re-dispatch. This card adds no new row status or payload field.

## Error paths

- Second failure of the critic after its one loop (`loop == max_loops`): `Escalated(phase=<critic>, detail=<that failure's detail>)`. The looped-to phase's successor is never dispatched.
- An item missing `from` or `detail` is a codec bug, not user input. The resolver should fail loudly with a `KeyError`/`EngineError` rather than print a blank bullet. It must not quietly fall back to a default.

## Tests

Tier rule, as the existing layout and the sibling plans in this worktree apply it: prompt-table tests go in `tests/test_prompt.py`. Runtime tests with an injected fake `agent_runner` go in `tests/runtime/`, driven through `_drive`/`_deps` in the style of `tests/runtime/test_compile.py`. All of them run in the default `uv run pytest` run. None is `e2e`-marked or uses a subprocess `claude`. Each fake runner reads only its `(phase, context, rendered)` arguments (story rule 4). No test breaks out of `agent.run()` (story rule 3).

`tests/runtime/test_loops.py` (new, runtime tier, default run). The workflow for all three tests is `spec` -> `validate_spec(on_fail=Goto("spec", 1))` -> `plan`. The failures use `AgentPhaseFailed("validate_spec", outcome="gate_failed", detail="no error path")`.
1. `test_blockers_loop_back_once_then_pass`: `validate_spec` fails once, then passes. Dispatch order is `spec, validate_spec, spec, validate_spec, plan`. The first `spec` call sees an empty `context["feedback"]`. The second sees `[{"for": "spec", "from": "validate_spec", "detail": "no error path"}]`. `validate_spec`'s own calls see no feedback. If the `spec` phase declares `feedback` in its inputs, the second call's `rendered.text` also contains "Feedback from review" and "validate_spec: no error path", and the first call's does not. This is the end-to-end proof that the mechanics reach a rendered prompt.
2. `test_second_failure_escalates_validation`: `validate_spec` fails twice. The run raises `Escalated` with `phase == "validate_spec"` and `detail` equal to the second failure's detail, and `plan` is never dispatched.
3. `test_loop_count_is_in_the_queued_turn`: after the first failure, the turn queued for the loop has `kwargs == {"phase": "spec", "loop": 1}`. The plan names `agent.to_dict()["queue"][0]["kwargs"]` as the check. The queue may already have drained by the time any observation point is reached without breaking `run()`. If so, the implementation stage may assert the same kwargs through the snapshot that is observable at that moment, for example `current_turn` during the second `spec` dispatch. The assertion must stay the same, and nothing may be registered as a closure hook or break out of the loop.

`tests/test_prompt.py` (append, prompt-table tier, default run):
4. `test_feedback_renders_each_item_and_nothing_when_empty`: this is the plan's literal body. `SimpleNamespace(name="spec", role="spec_author", inputs=("feedback",))` with one item renders "Feedback from review" and "validate_spec: no error path". With `[]` it renders neither the title nor the `## feedback` heading, and `rendered.sections` has no `"feedback"` entry (proving the section is omitted, not merely rendered empty). Also assert two items render as two bullets in order, and that a context with no `feedback` key renders nothing, has no `"feedback"` entry in `sections`, and does not raise.

Whole-suite gate: `uv run pytest` is green, including `tests/e2e` and the both-engine parity tests (story rule 2). The existing `test_on_fail_loops_back_with_feedback_then_escalates_when_loops_run_out` in `tests/runtime/test_compile.py` stays in place and unchanged.

---

# Goto Revision Loops and the Feedback Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `feedback` input resolver to the §7 prompt table, and prove end to end that a critic's `Goto` loop-back delivers its feedback into the looped-to phase's rendered prompt, escalates on a second failure, and queues its turn with `loop=1`.

**Architecture:** The loop mechanics (`runtime/compile.py:121-128`) and the feedback list assembly (`runtime/context.py:82-86`) already exist on this branch. This plan adds one resolver, `prompt._feedback`, registered as `_TABLE["feedback"]`. It also makes one additive change to `render_prompt`: a resolver that returns `None` contributes no section. Then a new runtime-tier test file drives a real pygents `Agent` over the compiled tools with a fake agent runner. `compile.py` is only touched if those tests expose a bug.

**Tech Stack:** Python 3.12, pytest with `asyncio_mode = "auto"`, pygents (runtime tier only), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-goto-revision-loops-b904b9e7/docs/superpowers/specs/task-add-goto-revision-loops-b904b9e7-design.md` (reproduced verbatim above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-goto-revision-loops-b904b9e7`, and every command runs from there. This branch is cut from `m6/task-add-runtime-run-subtask-2853e536`, and nothing from any other subtask is assumed.

Upstream truncation note: both the spec author's summary and the exploration findings that drove this pipeline were cut off by their size caps (2000 and 8000 characters). The upstream stages over-ran their briefs. This plan was written from the spec on disk and from the code itself, not from the missing text.

## Global Constraints

- Only `src/agent_manager/runtime/` imports pygents. `src/agent_manager/prompt.py` must never import it (story rule 1).
- Every commit leaves the whole default suite (`uv run pytest`) green, including `tests/e2e` and the both-engine parity tests (story rule 2).
- Never break or return out of `agent.run()`, never checkpoint at AFTER_TURN, and never register a pygents hook as a closure (story rule 3).
- A fake agent runner reads only its `(phase, context, rendered)` arguments (story rule 4).
- The feedback item shape `{for, from, detail}`, phase rows, journal lines, `SubtaskSummary` and escalation payloads stay unchanged (G10).
- Section title text: exactly `Feedback from review`. Bullet format: exactly `- {from}: {detail}`.
- `INPUT_PRODUCERS` stays exactly `{"plan_hash": "docs_commit"}`.
- `src/agent_manager/workflow/builtin/task.yaml`, `FEEDBACK_HEADING` and `dispatch.with_feedback` are not touched.
- `tests/runtime/test_compile.py::test_on_fail_loops_back_with_feedback_then_escalates_when_loops_run_out` stays unchanged.

## Review Focus

- A multi-line critic `detail` (gate verdicts often span lines). The bullet should carry the whole text verbatim, not a truncated first line. Pinned in Task 1 by `test_feedback_keeps_a_multi_line_detail_verbatim`.
- A phase that declares `feedback` but runs on the old YAML engine, whose context never has the key. It should render as if the input were not declared, and never raise. Pinned in Task 1 by `test_feedback_renders_each_item_and_nothing_when_empty` and `test_an_omitted_feedback_section_leaves_its_neighbours_untouched`.
- `feedback` declared between other inputs while the list is empty. The neighbouring sections should render byte-identically to a phase without `feedback`. Pinned in Task 1 by `test_an_omitted_feedback_section_leaves_its_neighbours_untouched`.
- A malformed feedback item (a codec bug) with no `from` or no `detail`. This should raise a loud `EngineError` naming the phase and the `feedback` input, never produce a blank bullet. Pinned in Task 1 by `test_a_feedback_item_without_from_or_detail_is_refused`.
- Feedback leaking to the wrong phase. The critic's re-run and the successor (`plan`) should see no feedback meant for `spec`. Pinned in Task 2 by the feedback column assertion in `test_blockers_loop_back_once_then_pass`.

---

### Task 1: The `feedback` resolver and section omission in `render_prompt`

**Files:**
- Modify: `src/agent_manager/prompt.py:116-117` (`Resolver` alias), `src/agent_manager/prompt.py:284-307` (new resolver before `_TABLE`, new table row, table docstring), `src/agent_manager/prompt.py:329-354` (`render_prompt` loop)
- Test: `tests/test_prompt.py` (imports at lines 10-12, the table-size test at lines 425-460, and new tests appended at the end of the file)

**Interfaces:**
- Consumes: nothing from other tasks. It reads `context["feedback"]` as `binding_table` builds it (`runtime/context.py:82-86`): a `list[dict]` with keys `for`, `from` and `detail`.
- Produces:
  - `prompt.FEEDBACK_TITLE: str = "Feedback from review"`
  - `prompt._feedback(request: _Request) -> str | None`, registered as `prompt._TABLE["feedback"]`
  - `prompt.Resolver = Callable[[_Request], str | None]`
  - `render_prompt` convention: a resolver returning `None` contributes no entry to `RenderedPrompt.sections` and no `## <name>` heading in `.text`.

- [ ] **Step 1: Add the `SimpleNamespace` and `ast` imports to `tests/test_prompt.py`**

Replace lines 10-12:

```python
import inspect
import json
from pathlib import Path
```

with:

```python
import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
```

- [ ] **Step 2: Update the fixed-table test to thirteen names (it becomes RED)**

In `tests/test_prompt.py`, replace the block from `TWELVE_INPUTS = [` (line 425) through the end of `test_the_table_carries_exactly_the_twelve_names_section_7_and_integrate_fix` (line 460) with the following. `MERGE_TIP` and `CONFLICT_FILES` keep their current definitions and position between the list and the test.

```python
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
```

- [ ] **Step 3: Append the feedback resolver tests to the end of `tests/test_prompt.py`**

```python
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
```

- [ ] **Step 4: Run the new and updated tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -v -k "feedback or thirteen"`
Expected: FAIL. Every test that renders `feedback` raises `EngineError` with the message `'feedback' is not an input this engine knows how to resolve`. The `parametrize`d refusal tests fail on the wrong message, because their assertion looks for `'from'`. `test_feedback_is_not_a_producer_input_and_prompt_imports_no_pygents` fails on `assert "feedback" in prompt.INPUT_NAMES`.

- [ ] **Step 5: Widen the `Resolver` alias in `src/agent_manager/prompt.py`**

Replace lines 116-117:

```python
Resolver = Callable[[_Request], str]
"""Turns one declared input into the body of its prompt section."""
```

with:

```python
Resolver = Callable[[_Request], str | None]
"""Turns one declared input into the body of its prompt section.

`None` means "no section for this input": `render_prompt` then adds neither a
heading nor an entry in `sections`. Only `feedback` returns it -- an empty
feedback list is the normal case, and a bare heading would read as a review
that said nothing.
"""
```

- [ ] **Step 6: Add the resolver just above `_TABLE` in `src/agent_manager/prompt.py`**

Insert immediately before the line `_TABLE: dict[str, Resolver] = {` (currently line 286):

```python
FEEDBACK_TITLE = "Feedback from review"
"""First line of the `feedback` section's body (pygents-engine design G4, §5).

Not `FEEDBACK_HEADING`: that heads `compose_brief`'s retry block for a failed
attempt of the *same* phase. This titles a critic's reason, carried back to
the phase a `Goto` loop returned to. The two must not be merged.
"""


def _feedback(request: _Request) -> str | None:
    """The critic feedback addressed to this phase, one bullet per item.

    The runtime's binding table has already kept only the items whose `for`
    names this phase, so `for` is not rendered. A missing key, `None` or an
    empty list is the normal case -- no loop has happened, or the old engine
    is running and never supplies the key -- so this never goes through
    `_required`/`_present`, and returns `None` to omit the section. An item
    without `from` or `detail` is a codec bug and is refused by name rather
    than rendered as a blank bullet.
    """
    items = request.context.get("feedback")
    if not items:
        return None
    lines = [FEEDBACK_TITLE]
    for item in items:
        if not isinstance(item, Mapping) or "from" not in item or "detail" not in item:
            raise EngineError(
                f"is declared as an input, but a feedback item is not a mapping "
                f"carrying 'from' and 'detail': {item!r}",
                phase=request.phase.name,
                parameter=request.name,
            )
        lines.append(f"- {item['from']}: {item['detail']}")
    return "\n".join(lines)


```

- [ ] **Step 7: Register the row and extend the table docstring**

In `_TABLE`, replace:

```python
    "conflict_files": _inline_json("conflict_files"),
}
"""The fixed §7 resolution table, keyed by the name a document may declare.

The last two rows are the resolver's (Integrate addendum §2 and I3,
`builtin/integrate.yaml`): the story tip being merged, inlined as a ref, and
the conflicting paths `steps.integrate.merge_tip` reported, inlined as JSON.
Neither reads another phase's result, so neither appears in `INPUT_PRODUCERS`:
the caller supplies both through `engine.run_subtask(extra_context=...)`.
"""
```

with:

```python
    "conflict_files": _inline_json("conflict_files"),
    "feedback": _feedback,
}
"""The fixed §7 resolution table, keyed by the name a document may declare.

`merge_tip` and `conflict_files` are the resolver's (Integrate addendum §2 and
I3, `builtin/integrate.yaml`): the story tip being merged, inlined as a ref,
and the conflicting paths `steps.integrate.merge_tip` reported, inlined as
JSON. Neither reads another phase's result, so neither appears in
`INPUT_PRODUCERS`: the caller supplies both through
`engine.run_subtask(extra_context=...)`.

`feedback` is the last row (pygents-engine design G4, §5): the critic's reason
for a `Goto` loop-back, supplied by the runtime's binding table. It reads the
feedback queue, not another phase's result, so it is not in `INPUT_PRODUCERS`
either.
"""
```

- [ ] **Step 8: Let `render_prompt` skip a `None` section**

In `render_prompt`, replace:

```python
        sections.append((name, resolver(_Request(name, phase, context))))
    return RenderedPrompt(
```

with:

```python
        body = resolver(_Request(name, phase, context))
        if body is None:
            continue
        sections.append((name, body))
    return RenderedPrompt(
```

Also extend the `render_prompt` docstring. Replace:

```python
    first. A name declared twice contributes one section: the second mention adds
    no information and would only be billed twice.
    """
```

with:

```python
    first. A name declared twice contributes one section: the second mention adds
    no information and would only be billed twice. A resolver that returns `None`
    contributes no section at all -- no heading, no entry in `sections`.
    """
```

- [ ] **Step 9: Run the prompt tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS. That covers every test in the file, including `test_the_producer_map_is_derived_from_the_table_it_describes` (still `{"plan_hash": "docs_commit"}`) and `test_input_names_are_exactly_the_resolver_table`.

- [ ] **Step 10: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. No YAML phase declares `feedback` and every other resolver still returns `str`, so nothing else changes.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -F - <<'EOF'
feat(prompt): add the feedback input resolver

Renders a critic's Goto loop-back reason as a "Feedback from review" section,
one bullet per item, and omits the section entirely when there is none: a
resolver may now return None to contribute no section.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```

---

### Task 2: End-to-end revision-loop tests over the compiled tools

**Files:**
- Create: `tests/runtime/test_loops.py`
- Modify (only if Step 3 fails): `src/agent_manager/runtime/compile.py:121-128` (the `except AgentPhaseFailed` branch of `agent_phase`)

**Interfaces:**
- Consumes:
  - `prompt._TABLE["feedback"]` from Task 1, and its exact rendering `"## feedback\nFeedback from review\n- {from}: {detail}\n"`.
  - `compile.compile_workflow(wf) -> Compiled`, `Compiled.first_turn() -> Turn`, `Compiled.agent_phase`, `Compiled.step_phase` and `compile.Escalated(.phase, .detail)`, all from `src/agent_manager/runtime/compile.py`.
  - `context.seed_item(binding) -> ContextItem` (`runtime/context.py:63`).
  - `state.current_run` and `state.RunDeps(workflow=, store=, story_id=, subtask=, agent_runner=, clock=)`.
  - `AgentPhase(name, role, inputs, result, ..., on_fail=Goto(phase, max_loops))` from `workflow/phases.py:31-64`.
  - The autouse `fresh_pygents` fixture in `tests/runtime/conftest.py`.
  - pygents `Agent.pause()`, `Agent.resume()` and `Agent.to_dict()["queue"]`, where each entry is `Turn.to_dict()` with a `"kwargs"` key.
- Produces: tests only.

Note on `test_loop_count_is_in_the_queued_turn`: the queued turn is observed with the plan's literal check, `agent.to_dict()["queue"][0]["kwargs"]`, without breaking `run()` or registering a hook. `run()` is consumed to the end in a task. The first `validate_spec` dispatch blocks on a `threading.Event` in its fake runner, which only signals and reads nothing but its arguments. While it is blocked, the test calls `agent.pause()`. pygents documents pause as "safe to call while running... the next [turn] is blocked until resume()". The critic is then released and fails. `agent_phase` yields the loop turn onto the queue, and the paused run loop holds it there until the test has read the queue and called `resume()`. Because the helper modules are imported under `--import-mode=importlib`, this file carries its own copies of `test_compile.py`'s small helpers rather than importing them.

- [ ] **Step 1: Write the tests**

Create `tests/runtime/test_loops.py`:

```python
"""Goto revision loops, end to end through the compiled tools (pygents-engine design G4, §5).

Runtime tier, as tests/runtime/test_compile.py: a real pygents `Agent` drives the
compiled tools, the agent runner is a fake that reads only its
`(phase, context, rendered)` arguments, and the store is a real temp SQLite
projection plus JSONL journal. No git, no board, no harness process.

The workflow is the shape the built-in task workflow will take: a critic
(`validate_spec`) that loops back to the phase it reviews (`spec`) at most once,
and a successor (`plan`) that only runs once the critic passes.
"""

import asyncio
import itertools
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, ContextPool, ContextQueue

from agent_manager import models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime import compile as C, context, state
from agent_manager.workflow.phases import AgentPhase, Goto, Workflow

RUN_ID = "run-2026-09-26-01"
STORY_ID = "f9c19dc3"
FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)
FEEDBACK_ITEM = {"for": "spec", "from": "validate_spec", "detail": "no error path"}
LOOP_ORDER = ["spec", "validate_spec", "spec", "validate_spec", "plan"]
_agent_names = itertools.count()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id="b904b9e7",
        branch="m6/task-add-goto-revision-loops-b904b9e7",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _deps(workflow, store, runner) -> state.RunDeps:
    return state.RunDeps(
        workflow=workflow,
        store=store,
        story_id=STORY_ID,
        subtask=_subtask(),
        agent_runner=runner,
        clock=lambda: FIXED,
    )


def _workflow() -> Workflow:
    return Workflow("loops", (
        AgentPhase("spec", "spec_author", ("feedback",), None),
        AgentPhase("validate_spec", "critic", (), None, on_fail=Goto("spec", 1)),
        AgentPhase("plan", "planner", (), None),
    ))


async def _agent(compiled) -> Agent:
    agent = Agent(
        f"loops:{next(_agent_names)}",
        "loop test run",
        [compiled.agent_phase, compiled.step_phase],
        context_pool=ContextPool(),
        context_queue=ContextQueue(limit=10),
        tags=["subtask"],
    )
    await agent.context_pool.add(context.seed_item({"worktree": "/w"}))
    await agent.put(compiled.first_turn())
    return agent


async def _consume(agent) -> None:
    async for _ in agent.run():
        pass


async def _drive(workflow, deps) -> Agent:
    agent = await _agent(C.compile_workflow(workflow))
    token = state.current_run.set(deps)
    try:
        await _consume(agent)
    finally:
        state.current_run.reset(token)
    return agent


def _critic_fails(*details: str):
    """A fake runner whose `validate_spec` fails with each detail in turn, then passes."""
    calls: list[tuple[str, Any, str]] = []
    remaining = list(details)

    def runner(phase, ctx, rendered):
        calls.append((phase.name, ctx.get("feedback"), rendered.text))
        if phase.name == "validate_spec" and remaining:
            raise AgentPhaseFailed(
                "validate_spec", outcome="gate_failed", detail=remaining.pop(0)
            )
        return {"ok": True}

    return runner, calls


async def test_blockers_loop_back_once_then_pass(store):
    runner, calls = _critic_fails("no error path")
    wf = _workflow()

    agent = await _drive(wf, _deps(wf, store, runner))

    assert [name for name, _, _ in calls] == LOOP_ORDER
    # Review Focus: the critic's re-run and the successor see no feedback meant for spec.
    assert [feedback for _, feedback, _ in calls] == [[], [], [FEEDBACK_ITEM], [], []]
    assert calls[0][2] == "# phase: spec\n# role: spec_author\n"
    assert calls[2][2] == (
        "# phase: spec\n"
        "# role: spec_author\n"
        "\n"
        "## feedback\n"
        "Feedback from review\n"
        "- validate_spec: no error path\n"
    )
    assert agent.context_pool.get("plan").content == {"ok": True}


async def test_second_failure_escalates_validation(store):
    runner, calls = _critic_fails("no error path", "still no error path")
    wf = _workflow()

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.phase == "validate_spec"
    assert info.value.detail == "still no error path"
    assert [name for name, _, _ in calls] == LOOP_ORDER[:4]
    assert "plan" not in [name for name, _, _ in calls]


async def _first_queued(agent, timeout: float = 5.0) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        queue = agent.to_dict()["queue"]
        if queue:
            return queue
        assert loop.time() < deadline, "no turn was queued after the critic failed"
        await asyncio.sleep(0.01)


async def test_loop_count_is_in_the_queued_turn(store):
    in_critic = threading.Event()
    release = threading.Event()
    names: list[str] = []

    def runner(phase, ctx, rendered):
        names.append(phase.name)
        if phase.name == "validate_spec" and names.count("validate_spec") == 1:
            in_critic.set()
            assert release.wait(5), "the test never released the critic"
            raise AgentPhaseFailed("validate_spec", outcome="gate_failed", detail="no error path")
        return {"ok": True}

    wf = _workflow()
    compiled = C.compile_workflow(wf)
    agent = await _agent(compiled)
    token = state.current_run.set(_deps(wf, store, runner))
    run = asyncio.create_task(_consume(agent))  # copies current_run into the task
    state.current_run.reset(token)
    try:
        assert await asyncio.to_thread(in_critic.wait, 5), "the critic was never dispatched"
        agent.pause()  # hold the next turn in the queue once the critic's turn ends
        release.set()
        queue = await _first_queued(agent)
    finally:
        release.set()
        agent.resume()
        await run

    assert queue[0]["kwargs"] == {"phase": "spec", "loop": 1}
    assert queue[0]["tool_name"] == compiled.agent_phase.__name__
    assert names == LOOP_ORDER
```

- [ ] **Step 2: Prove the loop-count test bites (mutation check)**

The loop mechanics already exist, so a real RED can't come from a missing feature. Instead, temporarily break the one line this file is uniquely positioned to catch. In `src/agent_manager/runtime/compile.py`, change line 126 from:

```python
                yield holder["compiled"].turn_for(p.on_fail.phase, loop + 1)
```

to:

```python
                yield holder["compiled"].turn_for(p.on_fail.phase, loop)
```

Run: `uv run pytest tests/runtime/test_loops.py::test_loop_count_is_in_the_queued_turn -v`
Expected: FAIL on the `queue[0]["kwargs"]` assertion, and pytest's diff shows `'loop': 0` where `'loop': 1` was expected. Run only this test here. With the mutation, `test_second_failure_escalates_validation`'s critic would loop forever.

Then revert the mutation:

```bash
git checkout -- src/agent_manager/runtime/compile.py
```

Run: `git diff --stat src/agent_manager/runtime/compile.py`
Expected: no output.

- [ ] **Step 3: Run the loop tests against the real code**

Run: `uv run pytest tests/runtime/test_loops.py -v`
Expected: PASS (3 tests). If one fails, this is plan Step 3's "fix agent_phase's loop branch until the tests pass" case. Use superpowers:systematic-debugging, and confine the fix to the `except AgentPhaseFailed as failure:` branch at `src/agent_manager/runtime/compile.py:121-128`. Keep the yielded `ContextItem(content={"for": p.on_fail.phase, "from": phase, "detail": failure.detail})` shape and the `Escalated(phase, failure.detail)` payload unchanged (G10). Do not restructure the module, and do not make any fix that returns or breaks out of `agent.run()`. Re-run this step until it passes.

- [ ] **Step 4: Confirm the sibling mechanics test is untouched and green**

Run: `uv run pytest tests/runtime/test_compile.py::test_on_fail_loops_back_with_feedback_then_escalates_when_loops_run_out -v && git diff --stat tests/runtime/test_compile.py`
Expected: PASS, and no diff output.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, including the both-engine parity tests in `tests/test_engine.py`. The `e2e`-marked tests are deselected by `addopts` as usual.

- [ ] **Step 6: Commit**

If Step 3 needed a fix, also `git add src/agent_manager/runtime/compile.py`.

```bash
git add tests/runtime/test_loops.py
git commit -F - <<'EOF'
test(runtime): revision loops carry critic feedback into the looped-to prompt

A critic that fails once loops back to the phase it reviews with its reason
rendered as "Feedback from review"; a second failure escalates and the
successor never runs; the queued loop turn carries loop=1.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```
