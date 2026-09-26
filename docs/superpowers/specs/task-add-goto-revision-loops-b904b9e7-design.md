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
