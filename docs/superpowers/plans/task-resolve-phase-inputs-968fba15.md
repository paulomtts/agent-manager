<!-- task-pipeline: validated -->
# Resolve phase inputs and render prompts (card 968fba15)

Subtask of story 2143808b "The workflow document and the engine". Blocker ed77a917 ("Run deterministic phases in the engine") is done and its `engine.py` is the base this extends. Source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §6 (the phase contract, lines 255-278) and §7 (context plumbing, lines 280-302).

## Scope

Design §6 step 2 — "renders the prompt from the phase's declared `inputs` (§7)" — and nothing either side of it. Step 1 (role bundle) is already served by `roles/loader.py`; step 3 onwards (attempt directory, `result.json` path, launching the harness, schema validation, the four journalled outcomes, retry, escalation) is sibling bf8e415b's and must not appear here.

Delivered:

1. A new module `src/agent_manager/prompt.py` holding the fixed §7 resolution table and the renderer. Given an `AgentPhase` and the engine's per-subtask binding table, it resolves every name in `phase.inputs` and returns a rendered prompt.
2. The rule §7 states, enforced mechanically: **small structured results are inlined; documents are passed by path.** Inlining a spec or a plan would re-bill it on every phase and invite the agent to work from a stale copy of a file it can read live in the worktree it is already sitting in. `spec_path` and `plan_path` therefore reach the prompt as a path string and never as file contents.
3. `spec_path` / `plan_path` population in `engine.py`, derived from the `writes:` field of the `spec` and `plan` phases of `builtin/task.yaml` (`docs/superpowers/specs/{stem}.md`, `docs/superpowers/plans/{stem}.md`) with `{stem}` expanded by `dag.task_stem`. They are computed once at subtask start from the document, not from those phases having run, because `plan_check` may `skip_to: implement` and `implement` still declares both.
4. `subtask_context` (`engine.py`) gains `card: models.Card` and `parent_story: models.Card | None` parameters, populated into the new `card_details` / `parent_story_details` context keys (see the resolution table below). Populating them is the caller's job — this module still never calls `board.show` — but the parameters and the keys they land in are this subtask's addition to `engine.py`, needed before any phase can resolve the `card` or `parent_story` input, or before `spec_path`/`plan_path` can be computed (item 3 needs the same full `Card` for `dag.task_stem`).
5. `RenderedPrompt.write(attempt_dir) -> Path` in `prompt.py`, called by whichever caller has an attempt directory in hand — that caller is bf8e415b's runner, once it creates the directory via `paths.attempt_dir` and receives the `rendered` prompt engine.py already produced. This subtask implements the method and guarantees byte-identical, UTF-8 output; it does not itself call `.write()` from `run_subtask`, and it does not create or know the run layout.

Not in scope: dispatch, `Dispatch` construction, `result.json`, gates over agent results, retry text, journalling of agent-phase outcomes.

## The resolution table

Each name in `inputs` resolves through the fixed §7 table and through nothing else. There is no expression language and no fallback lookup — the same invariant `workflow/registry.py` already enforces for `when:`/gates at load time.

| input name | source | form in the prompt |
|---|---|---|
| `card` | the run's cached `brd show <id>` `models.Card`, in the context as `card_details` | inlined JSON |
| `parent_story` | the parent card, cached the same way, in the context as `parent_story_details` | inlined JSON |
| `repo_docs` | `CLAUDE.md` / `AGENTS.md` at the worktree root | path + first 40 lines |
| `explore` | the validated `ExploreResult` of this subtask's `explore` phase, already in the context under its phase name | inlined JSON |
| `spec_path`, `plan_path` | expanded `writes:` templates | path only |
| `branch`, `base_branch` | computed by `dag.py`, in the context as `branch` / `base` | inlined string |
| `verification` | the commands discovered once per run (`commands` in the context) | inlined JSON |

Note on `card` / `parent_story`: `engine.py`'s existing context key `card` (from `subtask_context`, shipped by ed77a917) already holds the bare card-id **string** that `plan_check.find_validated_plan(card)` and other deterministic steps bind by that name — it is not, and must not become, the full `Card`. The §7 input named `card` therefore reads a *different* context key, `card_details`, and `parent_story` reads `parent_story_details`. `subtask_context` (`engine.py`) gains two new parameters to populate them — `card: models.Card` and `parent_story: models.Card | None` — supplied by the caller exactly like `commands` is today; this module still never calls `board.show` itself ("nothing here reads the board" below still holds). `card_details` and `parent_story_details` join `RESERVED_CONTEXT_KEYS` alongside `spec_path` and `plan_path`.

Input names are the *document's* vocabulary; context keys are the *callees'* parameter names, as `subtask_context` established. The table above is the translation between them, and it is not a one-to-one identity in several places: `base_branch -> base`, `worktree_path -> worktree` (the model field vs. the context key), `card -> card_details`, `parent_story -> parent_story_details`, and `verification -> commands`. New keys added to the context (`spec_path`, `plan_path`, `card_details`, `parent_story_details`) follow the same convention and are added to `RESERVED_CONTEXT_KEYS`, so a phase named `spec_path` (or `card_details`, etc.) could never overwrite the value every later phase binds.

`spec_path` and `plan_path` are computed once at subtask start from `writes:` templates whose `{stem}` is `dag.task_stem(card_details)` — the same full `Card` the `card` input reads, not the bare id string in the reserved `card` key, because `task_stem` needs the title to slug. This is why `card_details` must be populated before any phase runs, whether or not that phase declares `card` among its own `inputs`.

`repo_docs` resolves against the worktree root when the subtask has one and against `repo_dir` when it does not: `explore` is the first phase in `builtin/task.yaml` and the `worktree` phase runs two phases later, so at explore time there is no worktree to read from. Only whole files that exist contribute; a repo with neither file renders a stated absence rather than failing.

## Observable behaviour

- `render_prompt(phase, context)` returns a `RenderedPrompt` carrying the prompt text and the resolved inputs. It is pure: same phase and same context produce byte-identical text, and sections appear in the order the document declares them in `inputs`, each under a header naming the input. No clock, no randomness, no dict-ordering dependence.
- `RenderedPrompt.write(attempt_dir) -> Path` writes UTF-8 `prompt.txt` into an existing attempt directory and returns its path. It does not create the directory and does not know the run layout; `paths.attempt_dir` and its caller remain bf8e415b's.
- The role's `system.md` and methodology are *not* folded into `prompt.txt`. §8 says `Dispatch` carries the prompt text and the role bundle as separate fields, and the adapter materialises the bundle.
- The agent seam in `engine.py` widens from `(phase, context) -> result` to `(phase, context, rendered) -> result`. The engine resolves inputs and renders before calling the runner, which is exactly where §6 puts step 2, and the runner cannot dispatch without a rendered prompt it can write to disk first. Its docstring keeps saying that everything past the call belongs to bf8e415b.
- Nothing here reads the board, spawns a process or touches the network. Card and parent-story JSON arrive already cached in the context; the renderer only formats them.

## Error paths

All failures raise `engine.EngineError`, which already carries phase/function/parameter coordinates, and none of them return a partial prompt:

- An input name not in the §7 table: named, with the phase and the list of names that are.
- A declared input with nothing in the context — `explore` when the walk was started at a later phase, or `card` on a context whose `card_details` was never populated: named as such, distinguished from "unknown name".
- A `writes:` template containing a placeholder other than `{stem}`, or a phase whose `inputs` mention `spec_path`/`plan_path` while no phase in the document `writes:` a spec or a plan.
- An unreadable `CLAUDE.md`/`AGENTS.md` (permissions, non-UTF-8). Absence is not an error; unreadability is.
- `write` onto a directory that does not exist, surfaced with the path rather than as a bare `OSError`.

Because the current walk does not wrap the agent-runner call, a resolution failure propagates out of `run_subtask` as `EngineError`. Turning it into a journalled outcome is bf8e415b's choice, not a behaviour invented here.

## Tests

Tier per design §14 lines 477-492. Tests mirror `src/` 1:1 under `tests/` (CLAUDE.md); there are no `unit/` or `integration/` directories.

**Pure-function tier — `tests/test_prompt.py`, colocated with the module, no store, no git, no brd, no harness:**

1. Every one of the nine §7 names (`card`, `parent_story`, `repo_docs`, `explore`, `spec_path`, `plan_path`, `branch`, `base_branch`, `verification`) resolves to its stated form, table-driven against the row list.
2. `spec_path` and `plan_path` render as a path only: with the file present on disk holding a sentinel string, the sentinel never appears in the prompt text.
3. `card`, `parent_story`, `explore` and `verification` inline as JSON that round-trips through `json.loads`.
4. `repo_docs` renders path plus at most 40 lines, and marks the truncation.
5. `repo_docs` reads from `repo_dir` when the subtask has no worktree yet (the `explore` case) and from the worktree root once it does.
6. `repo_docs` with neither file present renders a stated absence and does not raise.
7. `base_branch` binds the context key `base`, and `branch` binds `branch`.
8. Rendering is deterministic and section order follows the declared `inputs` order.
9. `{stem}` expansion of a `writes:` template matches `dag.task_stem`; an unknown placeholder raises `EngineError`.
10. `write` produces UTF-8 `prompt.txt` at the returned path, overwriting an existing one.
11. Error paths: unknown input name, unresolvable input, missing `writes:` source, unreadable repo doc — each raising `EngineError` naming the phase and the input.

**Engine tier — `tests/test_engine.py`, the pattern already established there: canned fake functions in a hand-built `FunctionRegistry` standing in for §14's fake adapter, a real temp SQLite projection and a real temp JSONL journal, no git, no brd, no harness process:**

12. Walking `builtin/task.yaml` with a recording fake agent runner: `validate_spec`, `plan`, `validate_plan`, `implement` and `review` each receive a rendered prompt whose `spec_path`/`plan_path` equal the expanded `writes:` templates.
13. When `plan_check` opens its `skip_to: implement`, `implement` still receives both paths — they do not depend on the `spec` and `plan` phases having run.
14. Each agent phase's rendered prompt contains exactly the inputs that phase declares and no others (§13: each phase receives exactly its declared inputs).
15. A phase declaring an input the context cannot supply raises `EngineError` out of the walk and the fake runner is never called.
16. `spec_path`/`plan_path` are reserved: a phase named `spec_path` does not clobber the context value, and a deterministic step declaring a `spec_path` parameter binds the path.
17. `subtask_context(subtask, repo_dir, commands, card=..., parent_story=...)` places its `card` argument under `card_details` and its `parent_story` argument under `parent_story_details`, leaving the existing `card` key (the bare id string) untouched; the `explore` phase in a full `builtin/task.yaml` walk (test 12/14) resolves its `card` and `parent_story` inputs from these, not from the reserved `card` key.

No end-to-end tier test: nothing here executes a harness.

---

# Resolve phase inputs and render prompts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every `kind: agent` phase's declared `inputs` through the fixed §7 table and render a deterministic prompt for it, before the engine hands the phase to the injected agent runner.

**Architecture:** A new pure module `src/agent_manager/prompt.py` owns the §7 table (`_TABLE`: input name -> resolver), the renderer `render_prompt(phase, context) -> RenderedPrompt`, `RenderedPrompt.write(attempt_dir) -> Path`, and the `writes:` template expander `expand_writes`. `engine.py` supplies the binding table it resolves against: it gains `card_details` / `parent_story_details` / `spec_path` / `plan_path` context keys and widens its agent seam to `(phase, context, rendered) -> result`. `EngineError` moves to a new `src/agent_manager/errors.py` so `prompt.py` can raise it without importing the engine that imports `prompt.py`; `engine.EngineError` stays a valid name by re-export.

**Tech Stack:** Python 3.12+, pydantic v2 (`models.Card`, `workflow.loader.AgentPhase`), `uv`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/task-resolve-phase-inputs-968fba15-design.md` (prepended verbatim above). Upstream source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §6-§7 (lines 255-302) and §14 (lines 477-492).

## Global Constraints

- Verification command for every task: `uv run pytest`. There is no separate lint or typecheck command (CLAUDE.md).
- Source lives under `src/agent_manager/`, tests mirror it 1:1 under `tests/`. There are no `unit/` or `integration/` directories (CLAUDE.md).
- Pydantic models for anything validated at a process boundary; plain dataclasses are fine for internal-only state (CLAUDE.md). `RenderedPrompt` is internal-only state: a frozen dataclass.
- No expression language. An input name resolves through the fixed §7 table and through nothing else — the same invariant `workflow/registry.py` enforces for `when:`/gates at load time (spec §5).
- Binding is by **callee parameter name**: context key `base` (not `base_branch`), `worktree` (not `worktree_path`), `card_details` (not `card`). New keys follow the same convention.
- The governing rule, quoted in `prompt.py`'s module docstring verbatim: **"small structured results are inlined; documents are passed by path."**
- Out of scope, must not appear in any task: the attempt directory's creation, `Dispatch`, `result.json`, schema validation, the four journalled outcomes, retry, escalation. Those are sibling bf8e415b's.
- Every failure raises `EngineError` (`agent_manager.errors`, re-exported as `engine.EngineError`), never a bare `KeyError`/`OSError`.
- Repo-doc excerpt length is exactly 40 lines (`REPO_DOC_LINES = 40`).

## Review Focus

- A phase whose `inputs` list the same name twice (`inputs: [card, card]`) — must render one section, not two, and must not raise. Test in Task 1.
- An empty `verification` commands list — the run discovered no commands; the prompt must say `[]` plainly rather than omit the section or raise. Test in Task 2.
- A `writes:` template that escapes the worktree (`../../etc/passwd.md`, or an absolute path) — a document-path input must name a file inside the worktree the agent runs in. Test in Task 3.
- A repo doc of exactly 40 lines or fewer — must not be labelled truncated, and must not lose its last line. Test in Task 4.
- A context value that is not JSON-serialisable (a `Path` inside `commands`) — must render as a string rather than raise `TypeError` out of `json.dumps`. Test in Task 2.

---

### Task 1: `prompt.py` core — `RenderedPrompt`, the table, section order

**Files:**
- Create: `src/agent_manager/errors.py`
- Modify: `src/agent_manager/engine.py:32-59` (move `EngineError` out, import it back)
- Create: `src/agent_manager/prompt.py`
- Test: `tests/test_prompt.py` (create)

**Interfaces:**
- Consumes: `agent_manager.workflow.loader.AgentPhase` (fields `name: str`, `role: str`, `inputs: list[str]`, `writes: str | None`), `agent_manager.models.Card`.
- Produces: `errors.EngineError(reason, *, phase=None, function=None, parameter=None)` (unchanged behaviour, new home); `engine.EngineError` as a re-export of the same class object; `prompt.RenderedPrompt(phase: str, text: str, sections: tuple[tuple[str, str], ...])` with property `inputs -> tuple[str, ...]`; `prompt.render_prompt(phase: AgentPhase, context: Mapping[str, Any]) -> RenderedPrompt`; the module-private `_Request(name: str, phase: AgentPhase, context: Mapping[str, Any])`, `Resolver = Callable[[_Request], str]` and `_TABLE: dict[str, Resolver]` later tasks extend.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prompt.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_manager.errors'` at import time.

- [ ] **Step 3: Move `EngineError` into its own module**

Create `src/agent_manager/errors.py`:

```python
"""Errors raised by the engine and the helpers it calls.

`EngineError` lives here rather than in `engine.py` so `prompt.py` can raise it
without importing the engine that imports `prompt.py`. `engine.EngineError`
stays a valid name: `engine.py` imports it back and every existing caller,
including the tests, keeps working against the same class object.
"""


class EngineError(RuntimeError):
    """The engine refused to run, or could not make sense of, a phase.

    Carries the coordinates an operator needs to find the offending line of the
    workflow document: which phase, which registered function, which parameter.
    Prompt rendering reuses `parameter` for the name of the declared input it
    could not resolve -- the input name is what an operator greps the document
    for, exactly as a parameter name is.
    """

    def __init__(
        self,
        reason: str,
        *,
        phase: str | None = None,
        function: str | None = None,
        parameter: str | None = None,
    ) -> None:
        self.reason = reason
        self.phase = phase
        self.function = function
        self.parameter = parameter
        parts = []
        if phase is not None:
            parts.append(f"phase {phase!r}")
        if function is not None:
            parts.append(f"function {function!r}")
        if parameter is not None:
            parts.append(f"parameter {parameter!r}")
        prefix = ", ".join(parts)
        super().__init__(f"{prefix}: {reason}" if prefix else reason)
```

- [ ] **Step 4: Delete the class from `engine.py` and import it back**

In `src/agent_manager/engine.py`, delete the whole `class EngineError(RuntimeError): ...` block (currently lines 32-59) and add the import next to the existing ones:

```python
from agent_manager import models
from agent_manager.errors import EngineError
from agent_manager.store import Store
from agent_manager.workflow.loader import AgentPhase, DeterministicPhase, Workflow
```

Then, immediately below the `_EMPTY` / `_VARIADIC` constants, record why the name still resolves here:

```python
_EMPTY = inspect.Parameter.empty
_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)

# `EngineError` is imported, not defined, so `prompt.py` can raise it without
# importing this module back. `engine.EngineError` is still the public name.
```

- [ ] **Step 5: Write `prompt.py` with the renderer and the two string rows**

Create `src/agent_manager/prompt.py`:

```python
"""Resolve a phase's declared `inputs` and render its prompt (design §6 step 2, §7).

§7 fixes a table: a phase declares `inputs`, and each name resolves through that
table and through nothing else. There is no expression language and no fallback
lookup -- the same invariant `workflow/registry.py` enforces for `when:` and
gates at load time. A name the table does not carry is a document bug, reported
with the phase and the list of names that are.

The rule the table encodes, from §7 verbatim: **small structured results are
inlined; documents are passed by path.** Inlining a spec or a plan would re-bill
it on every phase and invite the agent to work from a stale copy of a file it can
read live in the worktree it is already sitting in.

Input names are the *document's* vocabulary; context keys are the *callees'*
parameter names, as `engine.subtask_context` established. The two are not the
same word in several rows: `base_branch` reads `base`, `card` reads
`card_details`, `verification` reads `commands`.

Pure: no clock, no randomness, no board, no process. The only files read are the
repo docs a `repo_docs` input asks for, and the only file written is the
`prompt.txt` a caller asks for by handing over an attempt directory it made.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_manager.errors import EngineError
from agent_manager.workflow.loader import AgentPhase

_MISSING = object()


@dataclass(frozen=True)
class RenderedPrompt:
    """One phase's prompt text, plus the sections it was assembled from.

    Internal-only state, so a dataclass rather than a pydantic model (CLAUDE.md):
    nothing validates it at a process boundary -- it is produced and consumed
    inside one process, and written to disk as plain text.
    """

    phase: str
    text: str
    sections: tuple[tuple[str, str], ...] = ()

    @property
    def inputs(self) -> tuple[str, ...]:
        """The input names that produced sections, in the order they appear."""
        return tuple(name for name, _body in self.sections)


@dataclass(frozen=True)
class _Request:
    """One input name being resolved, with everything a resolver may read."""

    name: str
    phase: AgentPhase
    context: Mapping[str, Any]


Resolver = Callable[[_Request], str]
"""Turns one declared input into the body of its prompt section."""


def _present(request: _Request, key: str) -> Any:
    """The context value under `key`, refusing an absent key by name."""
    value = request.context.get(key, _MISSING)
    if value is _MISSING:
        raise EngineError(
            f"is declared as an input, but nothing in the context supplies it "
            f"(it reads the context key {key!r}; the context has: "
            f"{', '.join(sorted(request.context)) or 'nothing'})",
            phase=request.phase.name,
            parameter=request.name,
        )
    return value


def _required(request: _Request, key: str) -> Any:
    """`_present`, and additionally refusing a key that is there but empty."""
    value = _present(request, key)
    if value is None:
        raise EngineError(
            f"is declared as an input, but its context key {key!r} was never populated",
            phase=request.phase.name,
            parameter=request.name,
        )
    return value


def _verbatim(key: str) -> Resolver:
    """A context value rendered as its own string: a branch name, or a path.

    Both §7 forms "inlined string" and "path only" render this way. They differ
    in the *rule*, not the formatting: a path is inlined as a path and its file
    is never opened, which is what stops a spec or a plan being re-billed on
    every phase.
    """

    def resolve(request: _Request) -> str:
        return str(_required(request, key))

    return resolve


_TABLE: dict[str, Resolver] = {
    "branch": _verbatim("branch"),
    "base_branch": _verbatim("base"),
}
"""The fixed §7 resolution table, keyed by the name a document may declare."""


def render_prompt(phase: AgentPhase, context: Mapping[str, Any]) -> RenderedPrompt:
    """Resolve every name in `phase.inputs` and assemble the prompt text.

    Sections follow the order the document declares, because that order is the
    author's emphasis and a reordering would silently change what the model reads
    first. A name declared twice contributes one section: the second mention adds
    no information and would only be billed twice.
    """
    sections: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in phase.inputs:
        if name in seen:
            continue
        seen.add(name)
        resolver = _TABLE.get(name)
        if resolver is None:
            raise EngineError(
                f"{name!r} is not an input this engine knows how to resolve; the fixed "
                f"§7 table is: {', '.join(sorted(_TABLE))}",
                phase=phase.name,
                parameter=name,
            )
        sections.append((name, resolver(_Request(name, phase, context))))
    return RenderedPrompt(
        phase=phase.name, text=_assemble(phase, sections), sections=tuple(sections)
    )


def _assemble(phase: AgentPhase, sections: list[tuple[str, str]]) -> str:
    head = f"# phase: {phase.name}\n# role: {phase.role}\n"
    return head + "".join(f"\n## {name}\n{body}\n" for name, body in sections)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 7: Run the whole suite to verify the `EngineError` move broke nothing**

Run: `uv run pytest`
Expected: PASS — `tests/test_engine.py` still resolves `engine.EngineError`.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/errors.py src/agent_manager/prompt.py src/agent_manager/engine.py tests/test_prompt.py
git commit -m "feat(prompt): render a phase's declared inputs in document order"
```

---

### Task 2: The inlined-JSON rows — `card`, `parent_story`, `explore`, `verification`

**Files:**
- Modify: `src/agent_manager/prompt.py` (add `_inline_json`, `_jsonable`, four `_TABLE` rows)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `prompt._Request`, `prompt._present`, `prompt._required`, `prompt._TABLE` from Task 1.
- Produces: `_TABLE` rows `card` (reads `card_details`), `parent_story` (reads `parent_story_details`, `None` allowed), `explore` (reads `explore`), `verification` (reads `commands`). Each body is `json.dumps(..., indent=2)` text that `json.loads` round-trips.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -k "inlines or null or populated or empty_command or json_cannot" -v`
Expected: FAIL with `EngineError: phase 'spec', parameter 'card': 'card' is not an input this engine knows how to resolve`.

- [ ] **Step 3: Add the JSON resolvers and their table rows**

In `src/agent_manager/prompt.py`, add below `_verbatim`:

```python
def _inline_json(key: str, *, allow_empty: bool = False) -> Resolver:
    """A context value inlined as JSON -- §7's form for small structured results.

    `allow_empty` is for `parent_story` alone: a subtask card genuinely may have
    no parent story, and `null` is the honest rendering of that. Everywhere else
    an unpopulated key is a bug in the caller, reported as one.
    """

    def resolve(request: _Request) -> str:
        value = _present(request, key) if allow_empty else _required(request, key)
        return json.dumps(_jsonable(value), indent=2, ensure_ascii=False, default=str)

    return resolve


def _jsonable(value: Any) -> Any:
    """A pydantic model as its JSON-mode dump; anything else unchanged.

    `default=str` on the dump catches whatever is left (a `Path` in `commands`):
    a prompt that says `/repo/scripts/check.sh` is strictly better than a
    `TypeError` escaping the renderer over a value the agent only has to read.
    """
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else value
```

and extend `_TABLE`:

```python
_TABLE: dict[str, Resolver] = {
    "card": _inline_json("card_details"),
    "parent_story": _inline_json("parent_story_details", allow_empty=True),
    "explore": _inline_json("explore"),
    "verification": _inline_json("commands"),
    "branch": _verbatim("branch"),
    "base_branch": _verbatim("base"),
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "feat(prompt): inline card, parent_story, explore and verification as JSON"
```

---

### Task 3: The path-only rows and `writes:` template expansion

**Files:**
- Modify: `src/agent_manager/prompt.py` (add `expand_writes`, two `_TABLE` rows)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `prompt._verbatim`, `prompt._TABLE` from Task 1; `agent_manager.dag.task_stem(card) -> str`.
- Produces: `_TABLE` rows `spec_path` and `plan_path` (both `_verbatim`, reading the same-named context keys); `prompt.expand_writes(template: str, card: models.Card, *, phase: str, input_name: str) -> str`, which Task 6's `engine._document_paths` calls.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -k "path_alone or expand_writes" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.prompt' has no attribute 'expand_writes'`, and the path test fails on `'spec_path' is not an input this engine knows how to resolve`.

- [ ] **Step 3: Add the rows and the expander**

In `src/agent_manager/prompt.py`, widen the imports:

```python
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from agent_manager import dag, models
from agent_manager.errors import EngineError
from agent_manager.workflow.loader import AgentPhase
```

add the two rows to `_TABLE` (between `verification` and `branch`, so the table reads in §7's order):

```python
    "verification": _inline_json("commands"),
    "spec_path": _verbatim("spec_path"),
    "plan_path": _verbatim("plan_path"),
    "branch": _verbatim("branch"),
```

and append the expander at the end of the module:

```python
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def expand_writes(
    template: str, card: models.Card, *, phase: str, input_name: str
) -> str:
    """The repo-relative path a phase's `writes:` template names, for one card.

    `{stem}` is the only placeholder, and `dag.task_stem` is the only thing that
    fills it: the stem is the load-bearing short card id plus a slug of the
    title, and every other derived name in the program comes from the same
    function. An unknown placeholder is a document bug, not something to leave
    literal in a path an agent is told to write to.
    """
    unknown = sorted({found for found in _PLACEHOLDER.findall(template) if found != "stem"})
    if unknown:
        raise EngineError(
            f"the writes template {template!r} uses "
            f"{', '.join(repr(name) for name in unknown)}; the only placeholder this "
            "engine expands is {stem}",
            phase=phase,
            parameter=input_name,
        )
    expanded = template.replace("{stem}", dag.task_stem(card))
    parts = PurePosixPath(expanded)
    if parts.is_absolute() or ".." in parts.parts:
        raise EngineError(
            f"the writes template {template!r} resolves to {expanded!r}, which leaves "
            "the worktree; a document-path input must name a file inside the worktree "
            "the agent runs in",
            phase=phase,
            parameter=input_name,
        )
    return expanded
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "feat(prompt): pass spec_path and plan_path by path and expand {stem}"
```

---

### Task 4: The `repo_docs` row

**Files:**
- Modify: `src/agent_manager/prompt.py` (add `REPO_DOC_NAMES`, `REPO_DOC_LINES`, `_repo_docs`, one `_TABLE` row)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `prompt._Request`, `prompt._required`, `prompt._TABLE` from Task 1.
- Produces: module constants `REPO_DOC_NAMES = ("CLAUDE.md", "AGENTS.md")` and `REPO_DOC_LINES = 40`; the `_TABLE` row `repo_docs`, reading the context keys `worktree` (a `Path | None`) and `repo_dir` (a `Path`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -k repo_docs -v`
Expected: FAIL — `AttributeError: module 'agent_manager.prompt' has no attribute 'REPO_DOC_LINES'`, and the rest on `'repo_docs' is not an input this engine knows how to resolve`.

- [ ] **Step 3: Add the resolver and its table row**

In `src/agent_manager/prompt.py`, add above `_TABLE`:

```python
REPO_DOC_NAMES = ("CLAUDE.md", "AGENTS.md")
"""The repo-level conventions documents, in the order they are shown."""

REPO_DOC_LINES = 40
"""§7's "first N lines". An excerpt orients the agent; the file is in the
worktree it is already sitting in, so the rest costs it one read."""


def _repo_docs(request: _Request) -> str:
    """Path plus an excerpt of each repo conventions document that exists.

    Resolves against the worktree root when there is one and against `repo_dir`
    when there is not: `explore` is the first phase of `builtin/task.yaml` and
    the `worktree` phase runs two phases later, so at explore time there is no
    worktree to read from. Absence of both files is a stated fact, not a failure
    -- plenty of repositories have neither. Unreadability *is* a failure: a file
    that is there and cannot be read is a broken checkout, not an empty one.
    """
    root = _repo_docs_root(request)
    blocks = []
    for name in REPO_DOC_NAMES:
        block = _repo_doc(request, root / name)
        if block is not None:
            blocks.append(block)
    if not blocks:
        return f"no {' or '.join(REPO_DOC_NAMES)} at {root}"
    return "\n\n".join(blocks)


def _repo_docs_root(request: _Request) -> Path:
    worktree = request.context.get("worktree")
    if worktree is not None and Path(worktree).is_dir():
        return Path(worktree)
    return Path(_required(request, "repo_dir"))


def _repo_doc(request: _Request, path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise EngineError(
            f"{path} exists but cannot be read: {type(error).__name__}: {error}",
            phase=request.phase.name,
            parameter=request.name,
        ) from error
    lines = text.splitlines()
    head = "\n".join(lines[:REPO_DOC_LINES])
    if len(lines) <= REPO_DOC_LINES:
        return f"path: {path}\n{len(lines)} line(s), shown in full:\n{head}"
    return (
        f"path: {path}\nfirst {REPO_DOC_LINES} of {len(lines)} lines "
        f"({len(lines) - REPO_DOC_LINES} more, open the file for the rest):\n{head}"
    )
```

and add the row to `_TABLE`, after `parent_story`:

```python
    "parent_story": _inline_json("parent_story_details", allow_empty=True),
    "repo_docs": _repo_docs,
    "explore": _inline_json("explore"),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS, 28 tests.

- [ ] **Step 5: Add the table-completeness test**

Append to `tests/test_prompt.py`:

```python
def test_the_table_carries_exactly_the_nine_names_section_7_fixes():
    """§7's table is fixed. A tenth name is a design change, not a code change."""
    rendered = prompt.render_prompt(
        _phase(
            [
                "card",
                "parent_story",
                "repo_docs",
                "explore",
                "spec_path",
                "plan_path",
                "branch",
                "base_branch",
                "verification",
            ]
        ),
        _context(),
    )

    assert rendered.inputs == (
        "card",
        "parent_story",
        "repo_docs",
        "explore",
        "spec_path",
        "plan_path",
        "branch",
        "base_branch",
        "verification",
    )
    assert sorted(prompt._TABLE) == sorted(rendered.inputs)
```

- [ ] **Step 6: Run it**

Run: `uv run pytest tests/test_prompt.py::test_the_table_carries_exactly_the_nine_names_section_7_fixes -v`
Expected: PASS (`repo_dir=Path("/repo")` holds no repo docs, so that section states the absence).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "feat(prompt): resolve repo_docs as a path plus a 40-line excerpt"
```

---

### Task 5: `RenderedPrompt.write(attempt_dir)`

**Files:**
- Modify: `src/agent_manager/prompt.py` (add the `write` method to `RenderedPrompt`)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `prompt.RenderedPrompt` from Task 1.
- Produces: `RenderedPrompt.write(attempt_dir: Path) -> Path`, returning `attempt_dir / "prompt.txt"`. bf8e415b calls it after creating the directory with `paths.attempt_dir(run_id, card, phase, attempt)`; nothing in this subtask calls it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -k write -v`
Expected: FAIL — `AttributeError: 'RenderedPrompt' object has no attribute 'write'`.

- [ ] **Step 3: Add the method**

In `src/agent_manager/prompt.py`, inside `class RenderedPrompt`, below the `inputs` property:

```python
    def write(self, attempt_dir: Path) -> Path:
        """Write `prompt.txt` into an existing attempt directory, and return it.

        §6 puts the attempt directory at step 3 and the dispatch at step 4, so
        the prompt is on disk before any harness runs and every dispatch is
        reproducible from the run tree alone. This method does not create the
        directory and knows nothing of the run layout: `paths.attempt_dir` and
        the caller that makes it are sibling bf8e415b's.
        """
        path = Path(attempt_dir) / "prompt.txt"
        try:
            path.write_text(self.text, encoding="utf-8")
        except OSError as error:
            raise EngineError(
                f"cannot write the prompt to {path}: {type(error).__name__}: {error}",
                phase=self.phase,
            ) from error
        return path
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS, 32 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "feat(prompt): write prompt.txt into an attempt directory"
```

---

### Task 6: The engine's binding table — card details and document paths

**Files:**
- Modify: `src/agent_manager/engine.py` (`RESERVED_CONTEXT_KEYS`, `subtask_context`, new `_DOCUMENT_INPUTS` / `_document_paths` / `_writing_phase`, `run_subtask` signature and context construction)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `prompt.expand_writes(template, card, *, phase, input_name) -> str` from Task 3; `agent_manager.models.Card`; `workflow.loader.Workflow.phases` / `AgentPhase.writes` / `AgentPhase.inputs`.
- Produces: `engine.subtask_context(subtask, repo_dir, commands=(), *, card: models.Card | None = None, parent_story: models.Card | None = None) -> dict[str, Any]` with the two new keys `card_details` and `parent_story_details`; `RESERVED_CONTEXT_KEYS` extended to ten names; `engine.run_subtask(..., card: models.Card | None = None, parent_story: models.Card | None = None, ...)`, whose context also carries `spec_path`/`plan_path` as `str` repo-relative paths whenever the document declares them.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine.py`, add the card fixtures next to `REPO` at the top of the file:

```python
CARD = models.Card(
    id="968fba15-0971-456a-ae9f-57ff2210f0ce",
    title="Resolve phase inputs",
    status="todo",
    parent_id="2143808b-b236-4cf9-b172-53809bdbc1a1",
)
PARENT = models.Card(
    id="2143808b-b236-4cf9-b172-53809bdbc1a1",
    title="The workflow document and the engine",
    status="in_progress",
)
SPEC_PATH = "docs/superpowers/specs/resolve-phase-inputs-968fba15.md"
PLAN_PATH = "docs/superpowers/plans/resolve-phase-inputs-968fba15.md"
```

Replace the existing `test_subtask_context_renames_the_model_fields_the_steps_ask_for` with the two tests below (the first is the same assertion, widened for the two new keys; the second is spec test 17):

```python
def test_subtask_context_renames_the_model_fields_the_steps_ask_for():
    context = engine.subtask_context(_subtask(), REPO, ["uv run pytest"])

    assert context == {
        "card": "ed77a917",
        "card_details": None,
        "parent_story_details": None,
        "branch": "m1/task-ed77a917",
        "base": "m1/story-base",
        "worktree": Path("/repo/.claude/worktrees/m1/task-ed77a917"),
        "repo_dir": REPO,
        "commands": ["uv run pytest"],
    }


def test_the_cards_land_under_the_details_keys_and_leave_the_id_string_alone():
    """§7's `card` input and the steps' `card` parameter are different things:
    `plan_check.find_validated_plan(card)` binds the bare id string, and turning
    that key into a `Card` would break every deterministic step at once.
    """
    context = engine.subtask_context(
        _subtask(), REPO, ["uv run pytest"], card=CARD, parent_story=PARENT
    )

    assert context["card"] == "ed77a917"
    assert context["card_details"] is CARD
    assert context["parent_story_details"] is PARENT


def test_the_new_context_keys_are_reserved_against_a_same_named_phase():
    for key in ("card_details", "parent_story_details", "spec_path", "plan_path"):
        assert key in engine.RESERVED_CONTEXT_KEYS
```

Then append the document-path tests:

```python
DOCUMENT_PATHS = """
name: paths
phases:
  - name: spec
    kind: agent
    role: spec_author
    writes: docs/superpowers/specs/{stem}.md
  - name: plan
    kind: agent
    role: planner
    writes: docs/superpowers/plans/{stem}.md
  - name: implement
    kind: agent
    role: coder
    inputs: [spec_path, plan_path]
  - name: after
    kind: deterministic
    run: step.after
"""


def test_document_paths_are_bound_from_the_writes_templates(store):
    seen: dict[str, Any] = {}

    def after(spec_path: str, plan_path: str) -> dict[str, Any]:
        seen.update(spec_path=spec_path, plan_path=plan_path)
        return {}

    workflow = _workflow(DOCUMENT_PATHS, {"step.after": after})

    engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        card=CARD,
        # `*args` so this test is indifferent to the seam Task 7 widens.
        agent_runner=lambda *args: {},
    )

    assert seen == {"spec_path": SPEC_PATH, "plan_path": PLAN_PATH}


def test_a_document_path_input_with_no_writing_phase_is_a_named_error(store):
    document = """
name: orphan
phases:
  - name: implement
    kind: agent
    role: coder
    inputs: [plan_path]
"""
    workflow = _workflow(document, {})

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            card=CARD,
            agent_runner=lambda phase, context, rendered: {},
        )

    assert caught.value.parameter == "plan_path"
    assert "'plan'" in str(caught.value)
    assert "writes" in str(caught.value)


def test_a_document_path_input_with_no_card_is_a_named_error(store):
    workflow = _workflow(DOCUMENT_PATHS, {"step.after": lambda spec_path, plan_path: {}})

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            agent_runner=lambda phase, context, rendered: {},
        )

    assert caught.value.parameter in {"plan_path", "spec_path"}
    assert "no card was supplied" in str(caught.value)


def test_a_document_with_no_path_inputs_needs_no_card(store):
    """Every existing walk in this file passes no card; none may start failing."""
    workflow = _workflow(THREE_PHASES, {
        "step.alpha": lambda card: {},
        "step.beta": lambda card: {},
        "step.gamma": lambda card: {},
    })

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -k "details_keys or reserved_against or document_path or no_path_inputs" -v`
Expected: FAIL — `TypeError: subtask_context() got an unexpected keyword argument 'card'` and `assert 'card_details' in ('card', 'branch', ...)`.

- [ ] **Step 3: Extend the context and the reserved keys**

In `src/agent_manager/engine.py`, import the new module next to the others:

```python
from agent_manager import models, prompt
```

Replace `RESERVED_CONTEXT_KEYS` and `subtask_context` with:

```python
RESERVED_CONTEXT_KEYS = (
    "card",
    "card_details",
    "parent_story_details",
    "branch",
    "base",
    "worktree",
    "repo_dir",
    "commands",
    "spec_path",
    "plan_path",
)
"""The context keys the engine itself sets, and no phase result may replace.

Named as a constant because phase results land in the same mapping under the
phase's name: a phase called `worktree` -- the shipped `builtin/task.yaml`
has exactly one -- would otherwise overwrite the real worktree path every
later step binds from, and a phase called `spec_path` would overwrite the
document path `implement` and `review` both declare. `_bind_result` uses this
to skip writing such a result back into the table rather than refuse the phase
outright.
"""


def subtask_context(
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    *,
    card: models.Card | None = None,
    parent_story: models.Card | None = None,
) -> dict[str, Any]:
    """The starting binding table for one subtask's phases.

    The keys are the *callables'* parameter names, not the model's field names:
    binding is by name, and no deterministic phase in `builtin/task.yaml`
    declares `args` that could bridge the difference. Hence `base` for
    `base_branch` and `worktree` for `worktree_path`.

    `card` stays the bare id string every deterministic step binds by that name
    (`plan_check.find_validated_plan(card)`). The full cards the §7 `card` and
    `parent_story` *inputs* render live beside it under `card_details` and
    `parent_story_details`, supplied by the caller exactly as `commands` is --
    nothing here reads the board.
    """
    return {
        "card": subtask.card_id,
        "card_details": card,
        "parent_story_details": parent_story,
        "branch": subtask.branch,
        "base": subtask.base_branch,
        "worktree": subtask.worktree_path,
        "repo_dir": repo_dir,
        "commands": list(commands),
    }
```

- [ ] **Step 4: Derive the document paths**

Still in `src/agent_manager/engine.py`, add below `subtask_context`:

```python
_DOCUMENT_INPUTS = {"spec_path": "spec", "plan_path": "plan"}
"""Which phase's `writes:` template each §7 document-path input comes from.

Keyed on the phase *name*, not on a guess about the path: `builtin/task.yaml`
names them `spec` and `plan`, and matching on the template text would make a
document whose plan phase writes into `docs/specs/` resolve backwards.
"""


def _document_paths(workflow: Workflow, card: models.Card | None) -> dict[str, str]:
    """`spec_path` / `plan_path` for the whole subtask, computed once, from the document.

    Computed at subtask start rather than when the `spec` and `plan` phases run:
    `plan_check` may `skip_to: implement`, and `implement` still declares both
    inputs. §7 calls them "paths in the repo, already committed" -- the path is a
    property of the card and the document, not of a phase having executed.
    """
    declared = {
        name
        for phase in workflow.phases
        if isinstance(phase, AgentPhase)
        for name in phase.inputs
        if name in _DOCUMENT_INPUTS
    }
    paths: dict[str, str] = {}
    for name in sorted(declared):
        source = _writing_phase(workflow, _DOCUMENT_INPUTS[name], name)
        if card is None:
            raise EngineError(
                f"is declared as an input, but no card was supplied to expand "
                f"{source.writes!r} (the stem comes from the card's id and title)",
                phase=source.name,
                parameter=name,
            )
        paths[name] = prompt.expand_writes(
            source.writes, card, phase=source.name, input_name=name
        )
    return paths


def _writing_phase(workflow: Workflow, phase_name: str, input_name: str) -> AgentPhase:
    found = next((p for p in workflow.phases if p.name == phase_name), None)
    if not isinstance(found, AgentPhase) or found.writes is None:
        raise EngineError(
            f"is declared as an input, but this workflow has no agent phase named "
            f"{phase_name!r} with a `writes:` template to take the path from "
            f"(phases: {', '.join(workflow.phase_names)})",
            parameter=input_name,
        )
    return found
```

- [ ] **Step 5: Thread the cards and the paths through `run_subtask`**

In `src/agent_manager/engine.py`, add the two parameters to `run_subtask`'s signature, after `commands`:

```python
    repo_dir: Path,
    commands: Sequence[str] = (),
    card: models.Card | None = None,
    parent_story: models.Card | None = None,
    agent_runner: AgentPhaseRunner | None = None,
```

and replace the context construction (currently `context = subtask_context(subtask, repo_dir, commands)`):

```python
    index = _start_index(workflow, start_phase)
    context = subtask_context(
        subtask, repo_dir, commands, card=card, parent_story=parent_story
    )
    context.update(_document_paths(workflow, card))
```

`_start_index` stays first: an unknown `start_phase` must still fail before anything else is computed or recorded.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -k "details_keys or reserved_against or document_path or no_path_inputs or renames_the_model" -v`
Expected: PASS, 6 tests. The two error tests never reach their runner, and the third takes `*args`, so none of them depend on the seam Task 7 widens.

- [ ] **Step 6b: Give the pre-existing builtin walk the card it now needs**

`_document_paths` demands a card only for a document that declares `spec_path`/`plan_path`. `builtin/task.yaml` declares both, so the one pre-existing test that walks it must supply the cards. In `tests/test_engine.py`, in `test_the_builtin_task_document_walks_against_a_fake_registry`, add two arguments to its `engine.run_subtask(...)` call (its runner still takes two parameters; Task 7 widens that):

```python
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=agent_runner,
```

- [ ] **Step 6c: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. Every other pre-existing `run_subtask` test passes no card and walks a document with no document-path inputs, so none of them may fail.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): bind card details and the documents' spec/plan paths"
```

---

### Task 7: Render before dispatch — the widened agent seam

**Files:**
- Modify: `src/agent_manager/engine.py:151-157` (`AgentPhaseRunner`), `src/agent_manager/engine.py:286-298` (the agent branch of the walk), module docstring
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `prompt.render_prompt(phase, context) -> RenderedPrompt` (Task 1-4), `engine._document_paths` and the widened `subtask_context` (Task 6).
- Produces: `AgentPhaseRunner = Callable[[AgentPhase, Mapping[str, Any], prompt.RenderedPrompt], Any]` — the three-argument seam bf8e415b implements. The engine renders (§6 step 2) and the runner does everything after it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine.py`, update the two existing runners to the new seam. In `test_an_agent_phase_goes_to_the_injected_runner`, replace the runner and add two assertions:

```python
    def agent_runner(phase, context, rendered):
        seen.append((phase.name, phase.role))
        assert rendered.phase == "explore"
        assert rendered.inputs == ()
        return {"summary": "explored"}
```

In `test_the_builtin_task_document_walks_against_a_fake_registry` (which Task 6 Step 6b already gave `card=CARD, parent_story=PARENT`), replace its runner with the three-argument seam:

```python
    def agent_runner(phase: AgentPhase, context: dict[str, Any], rendered) -> dict[str, Any]:
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}
```

Then append the engine-tier tests for this subtask:

```python
def _recording_runner(recorded: dict[str, Any]):
    def agent_runner(phase, context, rendered):
        recorded[phase.name] = rendered
        return {"role": phase.role}

    return agent_runner


def _builtin_functions(calls: list[str], *, validated: bool) -> dict[str, Any]:
    """The fake registry `builtin/task.yaml` needs, with no git, brd or harness."""

    def set_status(card: str, status: str) -> dict[str, Any]:
        calls.append(f"rollup.set_status:{status}")
        return {"card": card, "status": status}

    def ensure(branch: str, base: str, worktree: Any, repo_dir: Any) -> dict[str, Any]:
        calls.append("worktree.ensure")
        return {"created": True}

    def find_plan(card: str) -> dict[str, Any]:
        calls.append("plan_check.find_validated_plan")
        return {"found": validated, "validated": validated}

    def has_plan(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    def run_suite(commands: list[str], worktree: Any) -> dict[str, Any]:
        calls.append("verify.run_suite")
        return {"passed": True}

    def passed(result: dict[str, Any]) -> None:
        return None

    def agent_only_gate(**kwargs: Any) -> None:
        raise AssertionError("an agent phase's gate is the agent runner's business")

    return {
        "rollup.set_status": set_status,
        "worktree.ensure": ensure,
        "plan_check.find_validated_plan": find_plan,
        "plan_check.has_validated_plan": has_plan,
        "verify.run_suite": run_suite,
        "verification_passed_gate": passed,
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "verification_gate": agent_only_gate,
    }


def _walk_builtin(store, recorded: dict[str, Any], *, validated: bool) -> Any:
    calls: list[str] = []
    workflow = load_builtin("task", _registry(_builtin_functions(calls, validated=validated)))
    return engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=_recording_runner(recorded),
    )


def test_every_document_path_input_renders_the_expanded_writes_template(store):
    recorded: dict[str, Any] = {}

    _walk_builtin(store, recorded, validated=False)

    for phase_name in ("validate_spec", "plan", "validate_plan", "implement"):
        assert dict(recorded[phase_name].sections)["spec_path"] == SPEC_PATH
    for phase_name in ("validate_plan", "implement", "review"):
        assert dict(recorded[phase_name].sections)["plan_path"] == PLAN_PATH


def test_implement_gets_both_paths_even_when_plan_check_skipped_spec_and_plan(store):
    recorded: dict[str, Any] = {}

    summary = _walk_builtin(store, recorded, validated=True)

    assert summary.skipped == ["spec", "validate_spec", "plan", "validate_plan"]
    assert set(recorded) == {"explore", "implement", "review"}
    sections = dict(recorded["implement"].sections)
    assert sections["spec_path"] == SPEC_PATH
    assert sections["plan_path"] == PLAN_PATH


def test_each_agent_phase_receives_exactly_the_inputs_it_declares(store):
    """§13: a phase receives its declared inputs and nothing else."""
    recorded: dict[str, Any] = {}

    _walk_builtin(store, recorded, validated=False)

    assert {name: rendered.inputs for name, rendered in recorded.items()} == {
        "explore": ("card", "parent_story", "repo_docs", "verification"),
        "spec": ("card", "explore"),
        "validate_spec": ("card", "spec_path"),
        "plan": ("spec_path",),
        "validate_plan": ("spec_path", "plan_path"),
        "implement": ("plan_path", "spec_path", "branch", "base_branch"),
        "review": ("branch", "base_branch", "plan_path"),
    }


def test_the_explore_prompt_reads_the_cards_not_the_reserved_card_key(store):
    recorded: dict[str, Any] = {}

    _walk_builtin(store, recorded, validated=False)

    sections = dict(recorded["explore"].sections)
    assert json.loads(sections["card"])["title"] == "Resolve phase inputs"
    assert json.loads(sections["parent_story"])["title"] == (
        "The workflow document and the engine"
    )
    assert json.loads(sections["verification"]) == ["uv run pytest"]


def test_an_unresolvable_input_raises_out_of_the_walk_before_the_runner(store):
    """The walk does not wrap the runner call, so resolution failures propagate.
    Journalling them as an outcome is sibling bf8e415b's choice, not this one's.
    """
    called: list[str] = []

    def agent_runner(phase, context, rendered):
        called.append(phase.name)
        return {}

    document = """
name: early
phases:
  - name: spec
    kind: agent
    role: spec_author
    inputs: [explore]
"""
    workflow = _workflow(document, {})

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            card=CARD,
            agent_runner=agent_runner,
        )

    assert called == []
    assert caught.value.phase == "spec"
    assert caught.value.parameter == "explore"


def test_a_phase_named_spec_path_never_clobbers_the_document_path(store):
    seen: dict[str, Any] = {}

    def collide(card: str) -> dict[str, Any]:
        return {"not": "a path"}

    def after(spec_path: str) -> dict[str, Any]:
        seen["spec_path"] = spec_path
        return {}

    document = """
name: reserved
phases:
  - name: spec
    kind: agent
    role: spec_author
    writes: docs/superpowers/specs/{stem}.md
  - name: spec_path
    kind: deterministic
    run: step.collide
  - name: implement
    kind: agent
    role: coder
    inputs: [spec_path]
  - name: after
    kind: deterministic
    run: step.after
"""
    recorded: dict[str, Any] = {}
    workflow = _workflow(document, {"step.collide": collide, "step.after": after})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        card=CARD,
        agent_runner=_recording_runner(recorded),
    )

    assert summary.status == "done"
    assert summary.results["spec_path"] == {"not": "a path"}
    assert dict(recorded["implement"].sections)["spec_path"] == SPEC_PATH
    assert seen["spec_path"] == SPEC_PATH
```

Add `import json` to the imports at the top of `tests/test_engine.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -k "document_path_input_renders or plan_check_skipped or exactly_the_inputs or reserved_card_key or before_the_runner or never_clobbers" -v`
Expected: FAIL with `TypeError: agent_runner() missing 1 required positional argument: 'rendered'` — the walk still calls the two-argument seam.

- [ ] **Step 3: Widen the seam and render before the call**

In `src/agent_manager/engine.py`, replace the `AgentPhaseRunner` alias and its docstring:

```python
AgentPhaseRunner = Callable[
    ["AgentPhase", Mapping[str, Any], prompt.RenderedPrompt], Any
]
"""The seam sibling bf8e415b fills: `(phase, context, rendered) -> result`.

The engine resolves the phase's declared `inputs` and renders the prompt before
the call, because that is exactly where §6 puts step 2 -- and because the runner
cannot dispatch without a prompt it can write to the attempt directory first.
Everything past this call -- that directory, dispatch, schema validation, retry,
its gates -- belongs to that subtask, not here. This module only takes the
returned result into the context under the phase's name.
"""
```

and, in `run_subtask`'s agent branch:

```python
        if not isinstance(phase, DeterministicPhase):
            if agent_runner is None:
                raise EngineError(
                    "is an agent phase, but no agent runner was injected",
                    phase=phase.name,
                )
            rendered = prompt.render_prompt(phase, context)
            result = agent_runner(phase, dict(context), rendered)
            _bind_result(context, phase.name, result)
            summary.results[phase.name] = result
            index += 1
            continue
```

- [ ] **Step 4: Update the module docstring's first paragraph**

In `src/agent_manager/engine.py`, replace the second paragraph of the module docstring:

```
The walk is the only thing here. The engine resolves each agent phase's declared
`inputs` and renders its prompt (§6 step 2, `prompt.py`), then hands phase,
context and prompt to the injected runner: everything past that call --
the attempt directory, the dispatch, the result file, the retry -- belongs to a
sibling, and this module treats it as an opaque call.
```

- [ ] **Step 5: Run the engine tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS, including the two updated pre-existing runner tests.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, no failures, no errors.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): render each agent phase's prompt before the runner call"
```
