<!-- task-pipeline: validated -->
<!-- SPEC (verbatim, prepended per the planner's instructions) -->

# Compose the full brief — subtask a1af1e04

Parent story: 760dd05c, "The brief: role, methodology, inputs and the result contract in one prompt". Milestone: 7aa00a90. Blocked by 5cc741ec (result models, R1).

Narrowing of decision R2 in `docs/superpowers/specs/2026-09-23-real-harness-design.md` §2. This card delivers the composer only. Writing the brief into an attempt's `prompt.txt`, the retry path, the claude adapter's `-p` pointer and the CLI flags are siblings c0a4bc75 and 19efcddc.

## Scope

One pure function in `src/agent_manager/prompt.py` (the composer sits beside `render_prompt`, which produces its third section; a sibling module is acceptable but buys nothing). Signature, near enough:

```python
def compose_brief(
    role: RoleBundle,
    rendered: RenderedPrompt,
    *,
    result_path: Path | str | None = None,
    result_model: type[BaseModel] | None = None,
    feedback: str | None = None,
) -> str
```

It returns the whole brief as text. It does not touch disk, the clock, randomness, the board or any process. It does not read `results.RESULT_MODELS` — the caller resolves the phase's model and hands the class (or its already-computed schema) in, which is what keeps the function pure and testable without the blocking story's models existing.

Also in scope, and only this: read all six `src/agent_manager/roles/bundles/*/system.md` (coder, critic, explorer, planner, reviewer, spec_author) and fix, minimally, any that fails to tell its role what to do. Exploration found all six already carry role instructions, so the expected diff is zero or near-zero. The one thing worth a second look is that `coder/system.md:5`, `planner/system.md:6` and `spec_author/system.md:11` cite `methodology/<file>.md` as a path; under this card those files become headings inside the same document. If the wording is edited at all, it is edited to point at the heading — not rewritten, not expanded.

Out of scope: `dispatch._attempt`, `RenderedPrompt.write`, `--append-system-prompt` (R2 deviates from main-spec §8 deliberately; the brief stays on disk), and everything in §4 of the addendum (orchestration, parallel stories, integrate, non-Claude harnesses, Store thread-safety, roll-up).

## Observable behavior

The returned text is one document, sections in exactly this order:

1. `role.system`, verbatim, first.
2. One section per entry of `role.methodology`, each under its own `##` heading naming the file (for example `## methodology: test-driven-development.md`), body verbatim. Iteration follows the dict's insertion order, which `roles/loader._read_methodology` fills from `VENDORED.lock`. A role with no methodology (critic, explorer, reviewer) contributes nothing here — no empty heading.
3. `rendered.text`, verbatim: the `# phase:` / `# role:` header plus the `## <input>` sections `render_prompt` assembled.
4. A `## Result contract` section, present **only** when a result is asked for. It states the absolute `result.json` path, says to write valid JSON to that path, says the path is deliberately outside the worktree so the file must not be created inside it, and embeds `result_model.model_json_schema()` rendered as indented JSON in a fenced block. When no result is asked for (`AgentPhase.result is None`, so the caller passes neither path nor model), the section is absent entirely — no heading, no placeholder.
5. When `feedback` is given and non-empty, a feedback section last, under the exact same heading text as today's `dispatch.FEEDBACK_HEADING` (`## feedback on the previous attempt`).

Joining: sections are separated by a blank line (normalise each section's trailing newlines so exactly one blank line separates neighbours, without altering interior text) and the brief ends with a single newline. Appending feedback must only add text after the base brief, so the no-feedback brief stays a prefix of the with-feedback one (test 9). Test 3's "immediately followed" means no methodology heading between them, not zero separator characters.

Two different roles produce different briefs for the same rendered prompt, because section 1 (and usually 2) differ.

Import direction: `dispatch` imports `prompt`, never the reverse. So the heading constant moves to `prompt.py` as `FEEDBACK_HEADING` and `dispatch` keeps its name bound to the imported constant (`FEEDBACK_HEADING = prompt.FEEDBACK_HEADING`), so `dispatch.with_feedback` and existing tests keep working and the two can never disagree. Taking the heading as a parameter is an acceptable alternative; the constant must not be duplicated.

Feedback is composed last and appended after the contract, so a retry can be produced by appending only the feedback block to the base brief. That is the arrangement sibling c0a4bc75 needs to avoid duplicating the contract on retry; this card only has to make it possible, not wire it.

Types follow CLAUDE.md: the composer consumes existing pydantic models (`RoleBundle`) and dataclasses (`RenderedPrompt`) and returns a plain `str`. It introduces no new model.

## Error paths

- `result_path` given without `result_model`, or `result_model` without `result_path`: `EngineError` carrying the phase (`rendered.phase`), naming both parameters. A contract half-specified is a caller bug, not something to render partially.
- `result_path` that is not absolute: `EngineError` naming the phase and the path. The contract's whole value is that the agent is told an unambiguous location outside the worktree it is `cd`'d into; a relative path would resolve inside it.
- `model_json_schema()` raising, or its output not being JSON-serialisable: allowed to propagate. A model that cannot describe itself is a defect in the result-models story, not a condition to paper over.
- Empty or whitespace-only `feedback`: treated as no feedback, no section. Matches the "no result, no contract section" rule — the composer never emits an empty heading.
- No new failure mode for role content: `roles/loader.load_role` already rejects an empty `system.md`, so the composer may assume `role.system` is non-empty.

## Test list

Tier, per `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 "Testing": the composer is a pure function, so **every test below is a plain unit test** calling it directly, in `tests/test_prompt.py`. No temp git repo, no temp brd board, no launcher, no fake adapter, no disk, no `pytest` marker. §14's step tier, adapter tier, engine-with-fake-adapter tier and the opt-in real-harness end-to-end test are all inapplicable here, and addendum R4's fake-`claude` wiring test belongs to sibling c0a4bc75. Fixtures are a synthetic `RoleBundle` (or `load_role` against a synthetic `root`, as `tests/roles/test_loader.py` already does) and a small local `BaseModel` standing in for a result model.

1. Composition order: with a role that has system text and two methodology files, a rendered prompt and a result contract, the index of each landmark in the output is strictly increasing — system, methodology 1, methodology 2, rendered text, `## Result contract`.
2. Each methodology filename appears as its own heading, and each body appears verbatim.
3. A role with an empty `methodology` dict yields a brief with system text immediately followed by the rendered text and no methodology heading.
4. The contract section contains the absolute result path as given, and the embedded schema contains the stand-in model's property names (round-trip the fenced JSON and compare to `model_json_schema()` rather than matching strings).
5. The contract instructs writing valid JSON and keeping the file outside the worktree (assert on the path and on the presence of the instruction, not on exact prose).
6. No result: passing neither path nor model omits `## Result contract` entirely.
7. Two roles differ: composing the same `RenderedPrompt` under two different bundles yields two different briefs, and each contains its own system text and not the other's.
8. Feedback appended: with `feedback=`, the brief ends with `prompt.FEEDBACK_HEADING` followed by the feedback text, positioned after the contract; without it, the heading is absent.
9. Retry shape: the no-feedback brief is a prefix of the with-feedback brief (the property c0a4bc75 relies on so a retry appends rather than recomposes).
10. Error — `result_path` without `result_model` and the converse each raise `EngineError` mentioning the phase.
11. Error — a relative `result_path` raises `EngineError`.
12. Purity: the same arguments produce byte-identical output on two calls, and composing does not create `prompt.txt` or any file in a `tmp_path` handed in as the result path's parent.
13. Blank feedback (`""` and `"   "`) produces no feedback section.

Existing `tests/test_prompt.py` and `tests/test_dispatch.py` must keep passing unchanged, including whatever asserts on `dispatch.FEEDBACK_HEADING`.

<!-- END SPEC -->

---

# Compose the full brief — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one pure `prompt.compose_brief(...)` that assembles a role's system text, its methodology files, the phase's rendered inputs, an optional result contract and an optional feedback block into a single brief string.

**Architecture:** Everything lands in `src/agent_manager/prompt.py`, beside `render_prompt`, whose `RenderedPrompt.text` becomes the brief's third section. `FEEDBACK_HEADING` moves from `dispatch.py` to `prompt.py` and `dispatch` rebinds the imported constant, because `dispatch` imports `prompt` and never the reverse. The composer is a pure function of its arguments: no disk, no clock, no board, no process, no `results.RESULT_MODELS` lookup — the caller hands in the result model class. Tests are therefore plain unit tests in `tests/test_prompt.py`, the tier design §14 assigns to pure functions.

**Tech Stack:** Python 3.12+, pydantic (`BaseModel`, `RoleBundle`), stdlib `json` and `pathlib`, pytest under `uv run pytest` (importlib import mode).

**Spec:** `docs/superpowers/specs/task-compose-the-full-brief-a1af1e04-design.md` (prepended above).

## Global Constraints

- Source lives under `src/agent_manager/`; tests mirror it under `tests/` (CLAUDE.md).
- Pydantic models for anything validated at a process boundary; plain dataclasses for internal-only state (CLAUDE.md). This card introduces **no new model** — the composer returns a plain `str`.
- Verification command, the only one this repo has: `uv run pytest`. There is no separate lint or typecheck command.
- Import direction is fixed: `dispatch` imports `prompt`, never the reverse. `prompt.py` must not import `dispatch`, `engine`, `results` or `store`.
- `EngineError` comes from `agent_manager.errors` (already imported by `prompt.py`), never from `engine`.
- The feedback heading string `"## feedback on the previous attempt"` must exist in exactly one place in the source tree after this work.
- The composer is pure: no disk, no clock, no randomness, no subprocess, no board access.
- Out of scope and not to be touched: `dispatch._attempt`, `RenderedPrompt.write`, the claude adapter, `cli.py`, `--append-system-prompt`, and every item in §4 of the addendum (orchestration, parallel stories, integrate, non-Claude harnesses, Store thread-safety, roll-up).
- Existing `tests/test_prompt.py` and `tests/test_dispatch.py` assertions must keep passing unchanged.

## Review Focus

Five input classes the spec implies but whose tests would otherwise be missing. Each has a test assigned to the task that owns the code.

1. **`result_path` handed in as a plain `str` rather than a `Path`** — the signature admits both, and `dispatch` builds paths with `/` but may stringify them; a `str` must be accepted and rendered identically. Test in Task 3.
2. **A `role.system` that ends in several blank lines, or none at all** — bundle authors write both; the join must still put exactly one blank line between system text and whatever follows, and must not swallow interior blank lines. Test in Task 2.
3. **A methodology body containing its own `##` headings and interior blank lines** — every shipped methodology file is a markdown document full of headings; the body must land verbatim, not reflowed or re-indented. Test in Task 2.
4. **A result model whose schema carries non-ASCII text and nested structure** — `json.dumps` defaults to `ensure_ascii=True`, which would show the agent `—` escapes instead of the prose its own schema declares. Test in Task 3.
5. **Feedback text that already ends in newlines, appended twice over** — `dispatch.with_feedback` appends to whatever it is handed, so a third attempt carries two feedback blocks; the composer's block must not accumulate blank lines or break the prefix property. Test in Task 4.

---

### Task 1: Move `FEEDBACK_HEADING` into `prompt.py`

**Files:**
- Modify: `src/agent_manager/prompt.py:35` (insert the constant after `_MISSING`)
- Modify: `src/agent_manager/dispatch.py:46-51` (rebind the name to the imported constant)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `prompt.FEEDBACK_HEADING: str == "## feedback on the previous attempt"`. `dispatch.FEEDBACK_HEADING` stays a valid name bound to the same object, so `dispatch.with_feedback` (`dispatch.py:68-82`) and `tests/test_dispatch.py:88,682,683` are untouched.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompt.py`:

```python
def test_the_feedback_heading_lives_in_prompt_and_dispatch_reuses_it():
    from agent_manager import dispatch

    assert prompt.FEEDBACK_HEADING == "## feedback on the previous attempt"
    assert dispatch.FEEDBACK_HEADING is prompt.FEEDBACK_HEADING
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_prompt.py::test_the_feedback_heading_lives_in_prompt_and_dispatch_reuses_it -v`
Expected: FAIL with `AttributeError: module 'agent_manager.prompt' has no attribute 'FEEDBACK_HEADING'`

- [ ] **Step 3: Add the constant to `prompt.py`**

In `src/agent_manager/prompt.py`, immediately after `_MISSING = object()` (line 35), insert:

```python
FEEDBACK_HEADING = "## feedback on the previous attempt"
"""Heading of the block §6 step 7 appends before a re-dispatch.

A `##` section, matching `_assemble`'s section format, so the retry block reads
as one more section rather than as a stray paragraph. It lives here, not in
`dispatch`, because `dispatch` imports `prompt` and never the reverse: the
composer needs the same heading and a second copy of the string could drift.
"""
```

- [ ] **Step 4: Rebind the name in `dispatch.py`**

In `src/agent_manager/dispatch.py`, replace lines 46-51 (the constant and its docstring) with:

```python
FEEDBACK_HEADING = prompt.FEEDBACK_HEADING
"""Re-exported from `prompt` so callers and tests keep this name.

The string itself moved to `prompt.py` with the brief composer that also emits
it; `dispatch` imports `prompt`, so binding the name here costs nothing and
makes it impossible for the two to disagree.
"""
```

`dispatch.py:31` already does `from agent_manager import engine, models, paths, prompt, results`, so no import change is needed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py tests/test_dispatch.py -v`
Expected: PASS, including the pre-existing `dispatch.FEEDBACK_HEADING` assertions at `tests/test_dispatch.py:88`, `:682` and `:683`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/prompt.py src/agent_manager/dispatch.py tests/test_prompt.py
git commit -m "refactor: move FEEDBACK_HEADING into prompt.py"
```

---

### Task 2: Compose system text, methodology sections and the rendered prompt

**Files:**
- Modify: `src/agent_manager/prompt.py` (add imports; add the composer after `_assemble`, which ends at line 258, and before `_PLACEHOLDER`)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `prompt.FEEDBACK_HEADING` from Task 1; `RenderedPrompt` (`prompt.py:38-73`) with fields `phase: str`, `text: str`, `sections: tuple[tuple[str, str], ...]`; `roles.loader.RoleBundle` (`roles/loader.py:107-114`) with fields `name: str`, `system: str`, `policy: Policy`, `methodology: dict[str, str]`.
- Produces: `prompt.compose_brief(role: RoleBundle, rendered: RenderedPrompt, *, result_path: Path | str | None = None, result_model: type[BaseModel] | None = None, feedback: str | None = None) -> str`, and `prompt.METHODOLOGY_HEADING_PREFIX: str == "## methodology: "`. Tasks 3 and 4 extend the same function; they add no new public name except `prompt.RESULT_HEADING`.

- [ ] **Step 1: Add the test fixtures**

Append to `tests/test_prompt.py` (the module already imports `json`, `Path`, `pytest`, `prompt` and `EngineError`; add the two new imports at the top of the file, next to `from agent_manager import dag, models, prompt`):

```python
from pydantic import BaseModel

from agent_manager.roles import loader as roles_loader
```

Then append these fixtures at the end of the file:

```python
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
```

- [ ] **Step 2: Write the failing tests for sections 1-3**

Append to `tests/test_prompt.py`:

```python
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


def test_a_role_with_no_methodology_puts_the_rendered_prompt_straight_after_system():
    role = _role(name="reviewer", system="# Reviewer\n\nYou review finished work.\n")

    brief = prompt.compose_brief(role, _rendered())

    assert prompt.METHODOLOGY_HEADING_PREFIX not in brief
    assert brief.startswith("# Reviewer\n\nYou review finished work.\n\n# phase: implement\n")


def test_the_brief_carries_the_rendered_text_verbatim_and_ends_in_one_newline():
    rendered = _rendered()

    brief = prompt.compose_brief(_role(), rendered)

    assert rendered.text.strip("\n") in brief
    assert brief.endswith("\n")
    assert not brief.endswith("\n\n")


def test_two_roles_produce_different_briefs_from_the_same_rendered_prompt():
    rendered = _rendered()
    coder = _role(methodology={"test-driven-development.md": TDD_BODY})
    critic = _role(name="critic", system="# Critic\n\nYou adversarially review.\n")

    coder_brief = prompt.compose_brief(coder, rendered)
    critic_brief = prompt.compose_brief(critic, rendered)

    assert coder_brief != critic_brief
    assert "# Coder" in coder_brief and "# Critic" not in coder_brief
    assert "# Critic" in critic_brief and "# Coder" not in critic_brief


def test_composing_twice_is_byte_identical_and_writes_nothing(tmp_path):
    role = _role(methodology={"writing-plans.md": PLANS_BODY})
    rendered = _rendered()

    first = prompt.compose_brief(role, rendered)
    second = prompt.compose_brief(role, rendered)

    assert first == second
    assert list(tmp_path.iterdir()) == []
```

Review-focus tests 2 and 3, appended in the same block:

```python
def test_trailing_blank_lines_in_system_text_collapse_to_one_separator():
    role = _role(system="# Coder\n\nDo the work.\n\n\n\n")

    brief = prompt.compose_brief(role, _rendered())

    assert brief.startswith("# Coder\n\nDo the work.\n\n# phase: implement\n")


def test_system_text_with_no_trailing_newline_still_gets_one_blank_line():
    role = _role(system="# Coder\n\nDo the work.")

    brief = prompt.compose_brief(role, _rendered())

    assert brief.startswith("# Coder\n\nDo the work.\n\n# phase: implement\n")


def test_a_methodology_body_keeps_its_own_headings_and_interior_blank_lines():
    body = "# Writing plans\n\n## Step one\n\nWrite the test.\n\n## Step two\n\nRun it.\n"
    role = _role(methodology={"writing-plans.md": body})

    brief = prompt.compose_brief(role, _rendered())

    assert "## methodology: writing-plans.md\n" + body.strip("\n") in brief
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: the nine new tests FAIL (Task 1's test and every pre-existing test pass), each with `AttributeError: module 'agent_manager.prompt' has no attribute 'compose_brief'`.

- [ ] **Step 4: Add the imports the composer needs**

In `src/agent_manager/prompt.py`, replace the import block at lines 24-33 with:

```python
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

from agent_manager import dag, models
from agent_manager.errors import EngineError
from agent_manager.roles.loader import RoleBundle
from agent_manager.workflow.loader import AgentPhase
```

`roles/loader.py` imports only the standard library and pydantic, so importing it here creates no cycle.

- [ ] **Step 5: Write the composer**

In `src/agent_manager/prompt.py`, after `_assemble` (which ends at line 258) and before `_PLACEHOLDER = re.compile(...)`, insert:

```python
METHODOLOGY_HEADING_PREFIX = "## methodology: "
"""Heading that introduces one vendored methodology file inside the brief.

The file is a heading in one document rather than a path on disk: the agent is
handed the text it must follow, so it cannot follow a stale copy or fail to
open it.
"""


def compose_brief(
    role: RoleBundle,
    rendered: RenderedPrompt,
    *,
    result_path: Path | str | None = None,
    result_model: type[BaseModel] | None = None,
    feedback: str | None = None,
) -> str:
    """The whole brief for one dispatch, as one document (addendum R2 §2).

    Order is fixed: the role's standing instructions, its methodology, the
    phase's rendered inputs, the result contract, the feedback. Feedback comes
    last and is only ever appended, so the brief without it is a prefix of the
    brief with it and a retry can append rather than recompose.

    Pure: no disk, no clock, no randomness, no process. The result model is
    handed in rather than looked up in `results.RESULT_MODELS`, which is what
    keeps this function testable without the engine's registry.
    """
    parts: list[str] = [role.system]
    for filename, body in role.methodology.items():
        heading = f"{METHODOLOGY_HEADING_PREFIX}{filename}"
        parts.append(heading + "\n" + body.strip("\n"))
    parts.append(rendered.text)
    return _join_sections(parts)


def _join_sections(parts: list[str]) -> str:
    """One blank line between neighbours, one newline at the end.

    Only the newlines at each section's edges are normalised; interior text is
    untouched, because a methodology document's own blank lines are part of it.
    """
    return "\n\n".join(part.strip("\n") for part in parts) + "\n"
```

The heading is built on its own line and concatenated rather than interpolated with `body.strip("\n")` inside an f-string, so the expression stays readable and free of escape-in-f-string quirks.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS for all nine new tests and every pre-existing test in the file.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "feat: compose the role, methodology and rendered prompt into one brief"
```

---

### Task 3: The result contract section and its error paths

**Files:**
- Modify: `src/agent_manager/prompt.py` (extend `compose_brief`, add `RESULT_HEADING` and `_result_contract`)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `prompt.compose_brief(...)` and `prompt._join_sections(parts)` from Task 2; `EngineError(reason, *, phase=None, function=None, parameter=None)` from `agent_manager.errors` (`errors.py:10-40`).
- Produces: `prompt.RESULT_HEADING: str == "## Result contract"`. `compose_brief` gains the behavior that passing both `result_path` and `result_model` appends that section after the rendered text.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt.py`:

```python
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

    assert prompt.RESULT_HEADING in brief
    assert "/var/agent-manager/runs/r1/card/implement.1/result.json" in brief
    assert _fenced_json(brief) == StandInResult.model_json_schema()
    assert "summary" in _fenced_json(brief)["properties"]


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
```

`test_a_schema_with_non_ascii_prose_is_embedded_unescaped` uses `Field`, so extend the pydantic import at the top of `tests/test_prompt.py` to:

```python
from pydantic import BaseModel, Field
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -k "contract or result_path or result_model or schema" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.prompt' has no attribute 'RESULT_HEADING'` and, for the error-path tests, `Failed: DID NOT RAISE <class 'agent_manager.errors.EngineError'>`.

- [ ] **Step 3: Implement the contract section**

In `src/agent_manager/prompt.py`, add the constant next to `METHODOLOGY_HEADING_PREFIX`:

```python
RESULT_HEADING = "## Result contract"
"""Heading of the section that tells the agent where its `result.json` goes.

Only present when the phase asks for a result. The schema in it comes from the
model the engine will validate against, so the instruction and the validator
cannot drift (addendum R2 §2).
"""
```

Then insert `_result_contract` after `_join_sections`:

```python
def _result_contract(
    phase: str, result_path: Path | str | None, result_model: type[BaseModel] | None
) -> str | None:
    """The contract section, or `None` when this phase asks for no result.

    Half a contract is a caller bug, not something to render partially: an
    agent told to write JSON with no schema, or handed a schema with no path,
    produces a file nothing can read. A relative path is refused for the same
    reason the attempt directory is outside every worktree -- it would resolve
    inside the worktree the agent is `cd`'d into and be swept into a commit or
    fail the verify step's clean-tree check.

    `model_json_schema()` is called straight through: a model that cannot
    describe itself is a defect in the result models, not a condition to paper
    over here.
    """
    if result_path is None and result_model is None:
        return None
    if result_path is None or result_model is None:
        raise EngineError(
            "a result contract needs both result_path and result_model; got "
            f"result_path={None if result_path is None else str(result_path)!r} and "
            f"result_model={getattr(result_model, '__name__', None)!r}",
            phase=phase,
        )
    path = Path(result_path)
    if not path.is_absolute():
        raise EngineError(
            f"the result path {str(result_path)!r} is not absolute; the brief has to "
            "name one unambiguous location outside the worktree the agent runs in, "
            "and a relative path would resolve inside it",
            phase=phase,
        )
    schema = json.dumps(
        result_model.model_json_schema(), indent=2, ensure_ascii=False
    )
    return (
        f"{RESULT_HEADING}\n"
        "When you are done, write your result as valid JSON to exactly this path:\n"
        "\n"
        f"{path}\n"
        "\n"
        "That path is deliberately outside the worktree you are working in. Do not "
        "create the file inside the worktree, and do not write it anywhere else -- "
        "nothing else is read as your result.\n"
        "\n"
        "The JSON must validate against this schema:\n"
        "\n"
        "```json\n"
        f"{schema}\n"
        "```"
    )
```

Finally, wire it into `compose_brief`, replacing the body's tail so it reads:

```python
    parts: list[str] = [role.system]
    for filename, body in role.methodology.items():
        heading = f"{METHODOLOGY_HEADING_PREFIX}{filename}"
        parts.append(heading + "\n" + body.strip("\n"))
    parts.append(rendered.text)
    contract = _result_contract(rendered.phase, result_path, result_model)
    if contract is not None:
        parts.append(contract)
    return _join_sections(parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS, all of them, including Task 2's.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "feat: embed the result contract and its schema in the brief"
```

---

### Task 4: The feedback section and the retry prefix property

**Files:**
- Modify: `src/agent_manager/prompt.py` (extend `compose_brief`)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `prompt.FEEDBACK_HEADING` (Task 1), `prompt.compose_brief(...)`, `prompt._join_sections(parts)`, `prompt.RESULT_HEADING` (Tasks 2-3).
- Produces: `compose_brief`'s `feedback` keyword behavior. No new public name. Sibling c0a4bc75 relies on the prefix property this task pins.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -k feedback -v`
Expected: FAIL — the new tests report `AssertionError` because `compose_brief` currently ignores `feedback` and the brief ends with the contract (`test_without_feedback_the_heading_is_absent` and `test_blank_feedback_produces_no_feedback_section` may already pass; the other three must fail).

- [ ] **Step 3: Append the feedback section in `compose_brief`**

In `src/agent_manager/prompt.py`, insert the feedback block into `compose_brief` between the contract append and the return, so the body's tail reads:

```python
    contract = _result_contract(rendered.phase, result_path, result_model)
    if contract is not None:
        parts.append(contract)
    if feedback is not None and feedback.strip():
        parts.append(FEEDBACK_HEADING + "\n" + feedback.strip("\n"))
    return _join_sections(parts)
```

Blank and whitespace-only feedback falls through `feedback.strip()` being falsy, so no empty heading is ever emitted; because the block is appended last and `_join_sections` only ever adds a separator *before* it, the no-feedback brief stays a byte-exact prefix of the with-feedback one.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS, every test in the file.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. `tests/test_dispatch.py` in particular must be green — `dispatch.with_feedback` still appends its own block to `RenderedPrompt.text` and is untouched by this card.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "feat: append the retry feedback block to the composed brief"
```

---

### Task 5: Review the six `system.md` files against the brief's shape

**Files:**
- Read: `src/agent_manager/roles/bundles/critic/system.md`, `explorer/system.md`, `reviewer/system.md` (expected: no change)
- Modify: `src/agent_manager/roles/bundles/coder/system.md:5`, `planner/system.md:6`, `spec_author/system.md:11`
- Test: `tests/roles/test_loader.py` already covers loading the six shipped bundles; no new test file.

**Interfaces:**
- Consumes: `prompt.METHODOLOGY_HEADING_PREFIX == "## methodology: "` from Task 2 — the wording must match the heading the composer actually emits.
- Produces: nothing importable. This is the card's "fix minimally where a role does not tell itself what to do" clause.

- [ ] **Step 1: Read all six and confirm each tells its role what to do**

Read, in full:

```
src/agent_manager/roles/bundles/coder/system.md
src/agent_manager/roles/bundles/critic/system.md
src/agent_manager/roles/bundles/explorer/system.md
src/agent_manager/roles/bundles/planner/system.md
src/agent_manager/roles/bundles/reviewer/system.md
src/agent_manager/roles/bundles/spec_author/system.md
```

Each already opens with a role title and a bulleted list of instructions. `critic`, `explorer` and `reviewer` name no file path and need no change — leave them byte-identical. The three below cite `methodology/<file>.md` as a path; under this card that text arrives as a `## methodology: <file>.md` heading in the same document, so the citation is edited to point at the heading. Nothing else in these files changes.

- [ ] **Step 2: Repoint the citation in `coder/system.md`**

Replace line 5's opening in `src/agent_manager/roles/bundles/coder/system.md`:

```markdown
- Follow the `## methodology: test-driven-development.md` section of this brief:
  write the failing test, watch it fail for the right reason, write the minimum
  code that passes, watch it pass, commit.
```

(The old text read `- Follow \`methodology/test-driven-development.md\`: write the failing test, watch` / `  it fail for the right reason, write the minimum code that passes, watch it` / `  pass, commit.`)

- [ ] **Step 3: Repoint the citation in `planner/system.md`**

Replace line 6's opening in `src/agent_manager/roles/bundles/planner/system.md`:

```markdown
- Follow the `## methodology: writing-plans.md` section of this brief exactly:
  bite-sized steps, real code in every step, RED before GREEN, a commit at the
  end of each task.
```

(The old text read `- Follow \`methodology/writing-plans.md\` exactly: bite-sized steps, real code in` / `  every step, RED before GREEN, a commit at the end of each task.`)

- [ ] **Step 4: Repoint the citation in `spec_author/system.md`**

Replace line 11's bullet in `src/agent_manager/roles/bundles/spec_author/system.md`:

```markdown
- Follow the plan format in the `## methodology: writing-plans.md` section of
  this brief for anything your spec hands to a planner.
```

(The old text read `- Follow the plan format in \`methodology/writing-plans.md\` for anything your` / `  spec hands to a planner.`)

- [ ] **Step 5: Run the suite to verify the bundles still load**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: PASS. `system.md` is not hashed by `VENDORED.lock` — only `methodology/*.md` is (`roles/loader.py:208-241`) — so editing these three files cannot trip the drift check. If a test fails here, the edit touched more than the three bullets above; revert and redo it.

- [ ] **Step 6: Run the full verification**

Run: `uv run pytest`
Expected: PASS, whole suite.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/roles/bundles
git commit -m "docs: point role methodology citations at the brief's headings"
```
