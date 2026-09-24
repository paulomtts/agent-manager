<!-- task-pipeline: validated -->
# f26b377d — Give the coder its plan hash and the trailer rule

Parent story 695b0514. Blocked by ba15da20 (done), which added `steps/docs_commit.py` and its `docs_commit` phase. This card narrows the already-agreed milestone design to one seam: the hash that `docs_commit` computed must reach the coder's brief as a declared input, and the coder's standing instructions must state what to do with it.

## 1. Scope

In scope, and nothing else:

- a `plan_hash` row in `prompt._TABLE`, resolving from the `docs_commit` phase result the engine bound into the context;
- `plan_hash` added to the `implement` phase's `inputs` in `src/agent_manager/workflow/builtin/task.yaml`;
- a rewritten `src/agent_manager/roles/bundles/coder/system.md` stating the trailer rule and the resume rule;
- `tests/e2e/fake_claude.py`'s `implement` branch reading the hash out of the brief instead of computing it;
- a `plan_hash` row in §7's table in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (the design-change flag that `test_the_table_carries_exactly_the_nine_names_section_7_fixes` guards).

Explicitly out of scope: `docs_commit`'s own logic (sibling ba15da20 owns it — this card only consumes its result), `plan_check.mark_validated` (523953e4), the reviewer role and the `review` phase's inputs, milestone orchestration, parallel stories, `integrate`, non-Claude harnesses, ancestor status roll-up, measuring review counts with git.

## 2. The value being plumbed

The plan hash has exactly one definition in the program: `docs_commit.plan_hash()` — the first 8 lowercase hex characters of the sha256 of the plan file's bytes, read *after* `plan_check.mark_validated` appended the validated marker. `reducers.is_plan_hash` accepts exactly that shape. The commit trailer prefix is `docs_commit.TRAILER_PREFIX` (`"Plan-Hash: "`, with the trailing space). This card introduces no second definition and no second computation of the digest anywhere.

`commit_documents` returns `{"plan_hash": digest}`, and `engine._bind_result` stores a phase's result in the context under the phase's own name, so the value lives at `context["docs_commit"]["plan_hash"]`. Input names are the document's vocabulary and context keys are the callees' names, so the declared input is `plan_hash` while the lookup is nested under `docs_commit` — the same name/key split `base_branch` → `base` and `card` → `card_details` already have.

A hash is a short string, so §7's rule ("small structured results are inlined; documents are passed by path") inlines it, rendered the same way `branch` is. The §7 table gains one row: `plan_hash` | the `docs_commit` step's digest of the validated plan | inlined string.

## 3. Observable behaviour

1. `prompt.render_prompt` on a phase declaring `plan_hash`, given a context whose `docs_commit` entry is a mapping carrying `plan_hash`, produces a `## plan_hash` section whose body is that digest and nothing else (no prefix, no trailer, no quotes).
2. The table's key set becomes exactly ten names; `prompt.render_prompt` on an undeclared name still fails with the existing "not an input this engine knows how to resolve" error, now listing ten.
3. Resolution is lazy per declared name — a phase that does not declare `plan_hash` renders fine with no `docs_commit` key in the context at all. This falls out of `render_prompt` only invoking resolvers for declared names; the point is that it must stay true, since `explore`, `spec`, `plan`, `validate_*` and `review` all run without one.
4. `builtin/task.yaml`'s `implement` phase declares `inputs: [plan_path, spec_path, branch, base_branch, plan_hash]`, in that order, so the hash renders last, after the documents and the branches. Its `role`, `result` (`ImplementResult`) and absence of gates are unchanged.
5. The composed brief for `implement` (role system text + methodology + rendered inputs + result contract) states, in the coder's standing instructions: end EVERY commit message with the `Plan-Hash: <hash>` trailer using exactly the value in the brief's `plan_hash` section; never compute the hash yourself; never leave a commit untagged; the spec and the plan are committed by the engine, not by you. It also states the resume rule from main design §9 line 379: if the branch already carries commits with this hash, continue from the next uncompleted plan step; if it carries commits *without* it, stop and report blocked (`ImplementResult.blocked` / `blocked_reason`) rather than rewriting or amending them. "The branch's commits" means only those on the branch but not on `base_branch` (`git log <base_branch>..HEAD`); commits inherited from the base are untagged by nature and are never grounds for blocking. Main design §9 line 380 calls untagged commits "debris" and §5 line 244 has `review_gate` stop the run on them; the coder's instruction is the early, non-destructive form of that same rule (never delete or rewrite the debris, just report it).
6. `tests/e2e/fake_claude.py` for `implement` takes the digest from the brief's `## plan_hash` section and uses it both in the commit trailer and in the result's `plan_hash` field. It computes no sha256 for `implement`. The `review` branch is untouched (the reviewer is a later card's business) and `plan_hash_of` stays in the module for it.
7. End to end, `tests/e2e/test_production_wiring.py`'s existing independent oracle — recomputing sha256 of the plan file on disk — still equals the implement result's `plan_hash`, the review result's `plan_hash`, and the trailer on every commit on the branch.

## 4. Error paths

- **`docs_commit` absent from the context** while `plan_hash` is declared: `EngineError` naming the phase and the input, in the style `_present` already uses (`phase=`, `parameter="plan_hash"`, message listing the context keys that *are* present). This is the case a document hits when it declares `plan_hash` on a phase that runs before `docs_commit`.
- **`docs_commit` present but carrying no `plan_hash` key**, or carrying `None`: `EngineError`, same phase/parameter attribution, saying the `docs_commit` result did not supply `plan_hash`. That is a step-contract breach, not a missing input, and the message must distinguish it from the case above.
- **Fake claude's `implement` brief has no `## plan_hash` section**: `FakeClaudeError`, via the existing `_section(found, "plan_hash", phase)` helper, which already produces "the 'implement' brief has no `## plan_hash` section (it has: ...)" and exits 1. This is the mechanism that makes the wiring test fail when the input is dropped from `task.yaml`.
- Unchanged: an unknown input name is still an `EngineError` listing the fixed table; `prompt.py` still performs no disk read, clock read, board call or subprocess for this input, and there is still no fallback lookup and no expression language.

## 5. Tests

Tier per the placement rule in main design §14 (lines 477-492) and real-harness addendum R4 (lines 81-90): pure functions get unit tests mirroring their module; the default-suite "production wiring" tier exists so a brief that omits something fails; the slow real-harness test is opt-in and gets nothing here.

**`tests/test_prompt.py` — pure unit tier** (mirrors `src/agent_manager/prompt.py`; these must not go in e2e):

1. A phase declaring `plan_hash` renders a `## plan_hash` section whose body is exactly the digest from `context["docs_commit"]["plan_hash"]`.
2. A phase that does not declare `plan_hash` renders successfully with no `docs_commit` key in the context (lazy resolution).
3. A context with no `docs_commit` raises `EngineError` with `phase` and `parameter == "plan_hash"` set, and a message naming the `docs_commit` key.
4. A `docs_commit` result without a `plan_hash` key (and, separately, with `None`) raises `EngineError` with the same attribution and a distinguishable message.
5. `test_the_table_carries_exactly_the_nine_names_section_7_fixes` becomes the ten-name test: `plan_hash` added to both the declared list and the expected `rendered.inputs` tuple, and the docstring's design-change note updated to point at the §7 row this card adds.
6. `compose_brief` for the coder bundle contains the `Plan-Hash:` trailer instruction and the "never compute it yourself" rule. Asserted against the **composed brief**, not against `system.md`'s text, so the assertion covers the loader path (`roles/loader.py` → `RoleBundle.system` → `compose_brief`'s first part) as well as the file.
7. The composed coder brief states the resume rule (continue on commits carrying this hash; report blocked on untagged commits) and that the engine, not the coder, commits the spec and the plan.

**`tests/workflow/test_builtin_task.py` — document-load unit tier** (mirrors `workflow/`, alongside the existing `inputs` assertions at lines 165, 180, 204):

8. The `implement` phase's `inputs` equal `["plan_path", "spec_path", "branch", "base_branch", "plan_hash"]`, with `role == "coder"` and `result == "ImplementResult"` still asserted.

**`tests/e2e/test_fake_claude.py` — fake-harness behaviour pins** (this file currently pins nothing about `plan_hash_of`, so the pins below are new):

9. `build_result` for `implement` on a brief carrying `## plan_hash` uses that value in the trailer and in the result, *including when the value deliberately disagrees with the sha256 of the plan file on disk* — the disagreement is what proves the fake reads rather than computes.
10. `build_result` for `implement` on a brief with no `## plan_hash` section raises `FakeClaudeError` naming the section.

**`tests/e2e/test_production_wiring.py` — default-suite wiring tier** (unmarked, per `test_this_module_runs_in_the_default_suite_unmarked`):

11. Keep the independent sha256 oracle at lines ~155 and ~193 exactly as it is: it is the only check in the suite that is not derived from the brief, and it is what catches the fake and the engine agreeing on a wrong value.
12. Add an assertion that the recorded `implement` brief on disk contains a `## plan_hash` section whose body is that same oracle digest — i.e. the value reached the agent, not merely the result file.

**Manual, one-off proof (not committed as a test):** temporarily delete `plan_hash` from `implement`'s `inputs` in `task.yaml`, run `tests/e2e/test_production_wiring.py`, and confirm it fails (the fake's `_section` raises, the harness exits 1, the run does not complete). Restore the file and report the observed failure in the run notes. This is R4's stated purpose — a brief that omits something must fail the wiring test — demonstrated once rather than pinned as a permanently-red-if-inverted test.

**Suite:** the whole default suite, including `tests/e2e/test_production_wiring.py`, is green when this card lands (`uv run pytest`).

---

# Give the coder its plan hash and the trailer rule — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plumb the `docs_commit` plan hash into the coder's brief as a declared `plan_hash` input, and rewrite the coder's standing instructions so it stamps that exact hash on every commit and resumes or blocks on what the branch already carries.

**Architecture:** One new resolver row in `prompt._TABLE` reads `context["docs_commit"]["plan_hash"]` — the mapping `steps/docs_commit.commit_documents` returns and `engine._bind_result` binds under the phase name — and renders it as an inlined string, exactly as `branch` renders. `builtin/task.yaml`'s `implement` phase declares that input last. The coder bundle's `system.md` gains the trailer rule, the resume rule and the "the engine commits the documents" rule, and the fake `claude` of the production-wiring tier stops computing sha256 for `implement` and reads the digest out of the brief instead, so a brief that omits the section makes the wiring test fail.

**Tech Stack:** Python 3 (`src/agent_manager/`), pydantic for boundary models, plain dataclasses for internal state, pytest driven by `uv run pytest`, YAML workflow documents, git as a subprocess in the e2e tiers.

**Spec:** `docs/superpowers/specs/task-give-the-coder-its-plan-f26b377d-design.md` (reproduced verbatim above this line).

**Worktree:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-give-the-coder-its-plan-f26b377d`, on branch `m2/task-give-the-coder-its-plan-f26b377d`, cut from `origin/m2/task-commit-the-spec-and-ba15da20`. Every path below is relative to that worktree root. Run every command from that root.

## Global Constraints

- The plan hash has exactly one definition in the program: `docs_commit.plan_hash()` — the first 8 lowercase hex characters of the sha256 of the plan file's bytes, read after the validated marker was appended. This card introduces no second computation of the digest anywhere.
- The commit trailer prefix is `docs_commit.TRAILER_PREFIX` — `"Plan-Hash: "`, with the trailing space.
- `prompt.py` stays pure: no clock, no randomness, no board, no process, and no disk read for this input. No fallback lookup and no expression language — a name the table does not carry is a document bug.
- Input names are the document's vocabulary; context keys are the callees' names. The declared input is `plan_hash`; the context key it reads is `docs_commit`.
- §7's rule: small structured results are inlined, documents are passed by path. A hash is a short string, so it is inlined like `branch`.
- Agents decide as little as possible. The fake `claude` may know only what the brief says: it may not compute the plan hash and may not commit the spec or the plan.
- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md). Pydantic models at process boundaries, dataclasses for internal-only state.
- Do not touch `steps/docs_commit.py`'s logic, `plan_check.mark_validated`, the reviewer role, or the `review` phase's inputs — those are siblings' or later cards'.
- The whole default suite, `tests/e2e/test_production_wiring.py` included, must be green when this card lands: `uv run pytest`.
- The commit commands below give the subject and body only. If you are an agent whose own brief carries a `## plan_hash` section, append your `Plan-Hash: ` trailer to each of these commits as that brief instructs.

## Review Focus

- `context["docs_commit"]` bound to something that is not a mapping (a string, a list — a future change to the step's return type): must raise `EngineError` naming the phase and the input, never an `AttributeError` escaping the renderer. → Task 1, `test_a_docs_commit_result_that_is_not_a_mapping_is_an_engine_error`.
- `context["docs_commit"]["plan_hash"]` present but empty or whitespace: must raise the step-contract `EngineError`, not render an empty `## plan_hash` section that the fake would then stamp as a blank trailer. → Task 1, `test_a_blank_plan_hash_from_docs_commit_is_refused`.
- A document declaring `plan_hash` on a phase that runs *before* `docs_commit`: the shipped `task.yaml` must never do it, and the ordering has to be asserted against the loaded document rather than trusted. → Task 2, `test_no_phase_declares_plan_hash_before_docs_commit_runs`.
- `review` (and every other agent phase) keeping its inputs unchanged, so lazy resolution stays the thing that lets them run with no `docs_commit` in the context. → Task 2, the extended `test_review_carries_both_of_its_gates`.
- An `implement` brief whose `## plan_hash` body is padded with blank lines: the fake must still write a single-line trailer, or the commit message ends in a blank `Plan-Hash:` line that `review_gate` reads as debris. → Task 4, `test_a_padded_plan_hash_section_still_produces_a_single_line_trailer`.

---

## Task 1: The `plan_hash` row in the §7 resolution table

**Files:**
- Modify: `src/agent_manager/prompt.py` (add `_phase_field` beside `_verbatim` at lines 127-139; add one row to `_TABLE` at lines 225-235)
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:286-294` (the §7 table)
- Test: `tests/test_prompt.py` (the `_context()` helper at lines 39-54, the table test at lines 348-378, and new tests after `test_an_unreadable_repo_doc_is_an_engine_error_naming_the_phase`)

**Interfaces:**
- Consumes: `prompt._Request` (`name`, `phase`, `context`), `prompt._required(request, key)`, `prompt.Resolver = Callable[[_Request], str]`, `errors.EngineError(message, *, phase=..., parameter=...)`. All already exist.
- Produces: `prompt._phase_field(phase_key: str, field: str) -> Resolver` and the `_TABLE` key `"plan_hash"`. Task 2 relies on that key existing; nothing else imports `_phase_field`.

- [ ] **Step 1: Rewrite the nine-name table test as the ten-name test**

In `tests/test_prompt.py`, replace the whole of `test_the_table_carries_exactly_the_nine_names_section_7_fixes` (lines 348-378) with:

```python
def test_the_table_carries_exactly_the_ten_names_section_7_fixes():
    """§7's table is fixed. An eleventh name is a design change, not a code change.

    `plan_hash` is the tenth, added by card f26b377d together with its row in
    §7's table in `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.
    """
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
                "plan_hash",
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
        "plan_hash",
    )
    assert sorted(prompt._TABLE) == sorted(rendered.inputs)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_prompt.py::test_the_table_carries_exactly_the_ten_names_section_7_fixes -v`
Expected: FAIL — `EngineError: implement/plan_hash: 'plan_hash' is not an input this engine knows how to resolve; the fixed §7 table is: base_branch, branch, card, explore, parent_story, plan_path, repo_docs, spec_path, verification`

- [ ] **Step 3: Add the digest to the shared context helper**

In `tests/test_prompt.py`, add a module constant just below the `PARENT` card definition (after line 32):

```python
PLAN_HASH = "9f3a12bc"
"""`docs_commit.plan_hash()`'s shape: 8 lowercase hex characters."""
```

and add one entry to the dict inside `_context()` (lines 40-52), immediately after the `"explore"` entry:

```python
        "docs_commit": {"plan_hash": PLAN_HASH},
```

- [ ] **Step 4: Write the failing resolver tests**

In `tests/test_prompt.py`, insert these six tests immediately after `test_an_unreadable_repo_doc_is_an_engine_error_naming_the_phase` (which ends at line 345) and before the table test:

```python
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
```

- [ ] **Step 5: Run the new tests and watch them fail**

Run: `uv run pytest tests/test_prompt.py -k "plan_hash or docs_commit" -v`
Expected: FAIL — every one of them raises `EngineError: ... 'plan_hash' is not an input this engine knows how to resolve ...`, including the two that expect a *different* `EngineError` message.

- [ ] **Step 6: Add the resolver and the table row**

In `src/agent_manager/prompt.py`, insert this function immediately after `_verbatim` (which ends at line 139) and before `_inline_json`:

```python
def _phase_field(phase_key: str, field: str) -> Resolver:
    """One field of an earlier phase's result, inlined as its own string.

    `engine._bind_result` stores a phase's result in the context under the
    phase's own name, so `docs_commit`'s `{"plan_hash": digest}` lands at
    `context["docs_commit"]["plan_hash"]`. Input names are the document's
    vocabulary and context keys are the callees' names, so the declared input
    stays `plan_hash` while the lookup is nested -- the same split
    `base_branch` -> `base` already has.

    Two failures, deliberately distinguished. The phase never ran, so its key
    is absent: `_required` reports that, and it is the case a document hits by
    declaring the input on a phase that precedes the producer. The phase ran
    but its result does not carry the field: that is a step-contract breach,
    which no reordering of the document fixes.
    """

    def resolve(request: _Request) -> str:
        result = _required(request, phase_key)
        if not isinstance(result, Mapping):
            raise EngineError(
                f"is declared as an input, but the {phase_key!r} entry in the "
                f"context is a {type(result).__name__}, not a mapping, so it can "
                f"supply no {field!r}",
                phase=request.phase.name,
                parameter=request.name,
            )
        value = result.get(field)
        if value is None or not str(value).strip():
            raise EngineError(
                f"is declared as an input, but the {phase_key!r} result supplied "
                f"no {field!r} (that result carries: "
                f"{', '.join(sorted(str(key) for key in result)) or 'nothing'})",
                phase=request.phase.name,
                parameter=request.name,
            )
        return str(value)

    return resolve
```

Then add one row at the end of `_TABLE` (line 234, after `"base_branch"`):

```python
    "plan_hash": _phase_field("docs_commit", "plan_hash"),
```

`Mapping` is already imported at line 26; add no import.

- [ ] **Step 7: Run the prompt tier and watch it pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS — all tests, including the rewritten ten-name test.

- [ ] **Step 8: Add the §7 row to the main design document**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace the last row of §7's table (line 294):

```markdown
| `verification` | commands discovered once per run | inlined JSON |
```

with:

```markdown
| `verification` | commands discovered once per run | inlined JSON |
| `plan_hash` | the `docs_commit` step's digest of the validated plan | inlined string |
```

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — the table row is not yet declared by any phase, so nothing else moves.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "feat: resolve plan_hash from the docs_commit result (§7 row ten)"
```

---

## Task 2: `implement` declares the input

**Files:**
- Modify: `src/agent_manager/workflow/builtin/task.yaml:65-69` (the `implement` phase)
- Test: `tests/workflow/test_builtin_task.py` (new test beside `test_review_carries_both_of_its_gates` at lines 200-204)
- Test: `tests/e2e/test_production_wiring.py:132-161` (extend `test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with`)

**Interfaces:**
- Consumes: `prompt._TABLE["plan_hash"]` from Task 1; `load_builtin("task")`, `AgentPhase.inputs`, `Workflow.phase_names` (all existing); the module-scoped `agent_attempts` fixture from `tests/e2e/conftest.py:166-175`, whose `models.Attempt` carries `prompt_path` and `result_path`.
- Produces: `implement.inputs == ["plan_path", "spec_path", "branch", "base_branch", "plan_hash"]`. Task 4's fake reads the `## plan_hash` section that this declaration puts in the brief.

- [ ] **Step 1: Write the failing document assertions**

In `tests/workflow/test_builtin_task.py`, insert these two tests immediately after `test_review_carries_both_of_its_gates` (which ends at line 204):

```python
def test_implement_is_handed_the_plan_hash_last_after_the_documents() -> None:
    """Card f26b377d: the coder cannot stamp a trailer it was never told. The
    hash renders last, after the documents and the branches, because that order
    is the document author's emphasis and `render_prompt` preserves it."""
    phase = load_builtin("task").phase("implement")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "coder"
    assert phase.result == "ImplementResult"
    assert phase.inputs == [
        "plan_path",
        "spec_path",
        "branch",
        "base_branch",
        "plan_hash",
    ]
    assert phase.gates == []


def test_no_phase_declares_plan_hash_before_docs_commit_runs() -> None:
    """Review focus: `plan_hash` reads `context["docs_commit"]`, which the
    engine only binds once that phase has run. A phase declaring it earlier
    would raise at render time, mid-run. Driven from the loaded document, never
    a hardcoded list."""
    workflow = load_builtin("task")
    names = workflow.phase_names
    declaring = [
        phase.name
        for phase in workflow.phases
        if isinstance(phase, AgentPhase) and "plan_hash" in phase.inputs
    ]

    assert declaring == ["implement"]  # non-vacuity
    for name in declaring:
        assert names.index("docs_commit") < names.index(name)
```

Extend `test_review_carries_both_of_its_gates` (lines 200-204) with one line, so the reviewer's inputs stay pinned as unchanged by this card:

```python
def test_review_carries_both_of_its_gates() -> None:
    phase = load_builtin("task").phase("review")
    assert isinstance(phase, AgentPhase)
    assert phase.gates == ["review_gate", "plan_hash_gate"]
    assert phase.inputs == ["branch", "base_branch", "plan_path"]
    # The reviewer recomputes the hash from the plan file; card f26b377d gives
    # the input to the coder only.
    assert "plan_hash" not in phase.inputs
```

- [ ] **Step 2: Write the failing wiring assertion**

In `tests/e2e/test_production_wiring.py`, inside `test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with`, add these three lines at the very end of the function (after line 161, `assert review["tagged_count"] == review["commit_count"]`):

```python
    # The value reached the AGENT, not merely the result file: the recorded
    # brief on disk carries it as its own section (spec "Tests" item 12).
    brief = Path(agent_attempts["implement"].prompt_path).read_text(encoding="utf-8")
    assert f"\n## plan_hash\n{expected}\n" in brief, brief
```

Leave the `hashlib.sha256(...)` oracle at line 155 exactly as it is: it is the only check in the suite that is not derived from the brief.

- [ ] **Step 3: Run both and watch them fail**

Run: `uv run pytest tests/workflow/test_builtin_task.py -k plan_hash tests/e2e/test_production_wiring.py::test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with -v`
Expected: FAIL — `test_implement_is_handed_the_plan_hash_last_after_the_documents` on `assert ['plan_path', 'spec_path', 'branch', 'base_branch'] == [... 'plan_hash']`, `test_no_phase_declares_plan_hash_before_docs_commit_runs` on `assert [] == ['implement']`, and the wiring test on the new `## plan_hash` assertion.

- [ ] **Step 4: Declare the input**

In `src/agent_manager/workflow/builtin/task.yaml`, replace line 68:

```yaml
    inputs: [plan_path, spec_path, branch, base_branch]
```

with:

```yaml
    inputs: [plan_path, spec_path, branch, base_branch, plan_hash]
```

Change nothing else in the phase: `role: coder`, `result: ImplementResult` and the absence of gates all stay.

- [ ] **Step 5: Run them and watch them pass**

Run: `uv run pytest tests/workflow/test_builtin_task.py tests/e2e/test_production_wiring.py -v`
Expected: PASS — the brief now carries the section, and the fake still computes its own (identical) digest, so nothing else in the wiring run changes yet.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/workflow/builtin/task.yaml tests/workflow/test_builtin_task.py tests/e2e/test_production_wiring.py
git commit -m "feat: declare plan_hash on the implement phase"
```

---

## Task 3: The coder's trailer rule and resume rule

**Files:**
- Rewrite: `src/agent_manager/roles/bundles/coder/system.md` (currently 12 lines of TDD bullets)
- Test: `tests/test_prompt.py` (new tests after `test_a_methodology_body_keeps_its_own_headings_and_interior_blank_lines`, which ends at line 550)

**Interfaces:**
- Consumes: `roles.loader.load_role("coder")` → `RoleBundle` with `.system` and `.methodology` (the shipped bundle already carries `policy.toml`, `VENDORED.lock` and `methodology/test-driven-development.md`); `prompt.compose_brief(role, rendered)`; `prompt.render_prompt`; the `_phase`, `_context` and `PLAN_HASH` helpers from Task 1.
- Produces: nothing importable. The bundle text is asserted through the composed brief only.

- [ ] **Step 1: Write the failing brief tests**

In `tests/test_prompt.py`, insert these tests immediately after `test_a_methodology_body_keeps_its_own_headings_and_interior_blank_lines` (which ends at line 550):

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_prompt.py -k shipped_coder -v`
Expected: FAIL — `test_the_coder_brief_states_the_trailer_rule_beside_the_hash_it_must_use` on `assert "`Plan-Hash: <hash>`" in brief`, and the resume test on `assert "git log <base_branch>..HEAD" in brief`. The current `system.md` says nothing about either.

- [ ] **Step 3: Rewrite the coder's standing instructions**

Replace the entire contents of `src/agent_manager/roles/bundles/coder/system.md` with:

```markdown
# Coder

You execute an implementation plan, one task at a time, under strict TDD.

- Follow the `## methodology: test-driven-development.md` section of this brief:
  write the failing test, watch it fail for the right reason, write the minimum
  code that passes, watch it pass, commit.
- Never weaken an assertion to make a test pass, and never delete a failing test
  you did not write.
- Implement the task in front of you and nothing else. Work the plan's steps
  fully rather than skipping ahead.
- Run the project's verification command before claiming a task is done, and
  report its real output.

## The Plan-Hash trailer

The `## plan_hash` section of this brief carries the hash of the plan you are
implementing.

- End EVERY commit message you write with the trailer `Plan-Hash: <hash>`, on
  its own last line, using exactly the value in the `## plan_hash` section.
- Never compute the hash yourself. Do not hash the plan file, do not shorten
  anything, and do not copy a hash out of an existing commit. The only hash you
  may write is the one this brief states.
- Never leave a commit untagged. An untagged commit is debris, and the review
  phase stops the whole run on it.

## Resuming a branch that already has commits

Look only at the commits on this branch that are not on the base branch
(`git log <base_branch>..HEAD`). Commits inherited from the base branch are
untagged by nature and are never grounds for blocking.

- If those commits carry the hash in the `## plan_hash` section, an earlier
  attempt got part of the way through this same plan.
  Continue from the next uncompleted plan step, and do not redo committed work.
- If any of those commits has no `Plan-Hash:` trailer, or carries a different
  hash, stop immediately. Report `blocked: true` with a `blocked_reason` that
  names those commits. Never rewrite, amend, squash or delete them.

## What you do not commit

The spec and the plan are committed by the engine before you are dispatched.
Do not add, commit or amend anything under `docs/superpowers/specs/` or
`docs/superpowers/plans/`, and do not sweep those files into a commit of your
own.
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/test_prompt.py -k shipped_coder -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — `tests/roles/test_loader.py` builds synthetic bundles under `tmp_path` and pins nothing about the shipped coder text, so the rewrite moves nothing there.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/roles/bundles/coder/system.md tests/test_prompt.py
git commit -m "feat: give the coder the trailer rule and the resume rule"
```

---

## Task 4: The fake `claude` reads the hash instead of computing it

**Files:**
- Modify: `tests/e2e/fake_claude.py:270-285` (the `implement` branch of `build_result`)
- Test: `tests/e2e/test_fake_claude.py` (new tests at the end of the file, after `test_a_feedback_block_after_the_contract_does_not_hide_the_contract`, which ends at line 325)

**Interfaces:**
- Consumes: `fake_claude.build_result(phase, payload, text, cwd)`, `fake_claude.payload_from_schema(schema)`, `fake_claude.sections(text)`, `fake_claude._section(found, name, phase)`, `fake_claude.plan_hash_of(path)`, `fake_claude.FakeClaudeError`. All already exist; the module is loaded by path at lines 22-26 of the test file.
- Produces: no new names. `plan_hash_of` stays in the module — the `review` branch at lines 286-304 keeps using it, and this card does not touch the reviewer.

- [ ] **Step 1: Write the failing fake-harness pins**

Append to `tests/e2e/test_fake_claude.py`:

```python
IMPLEMENT_SCHEMA = {
    "properties": {
        "blocked": {"type": "boolean"},
        "blocked_reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "resumed": {"type": "boolean"},
        "plan_hash": {"type": "string"},
        "report": {"type": "string"},
    },
    "type": "object",
}
"""`results.ImplementResult`'s shape, written out here rather than imported:
the fake is a standalone script and learns a schema only from a brief."""

PLAN_RELATIVE = "docs/superpowers/plans/x-00000001.md"
BRIEF_HASH = "0badcafe"
"""Deliberately NOT the sha256 of the plan file the fixture writes. The
disagreement is what proves the fake reads the brief rather than hashing."""


def _implement_repo(tmp_path):
    """A real git repo with a committed plan file, as `implement` finds one."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    for key, value in (
        ("user.email", "tests@example.com"),
        ("user.name", "agent-manager tests"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "-C", str(root), "config", key, value],
            check=True,
            capture_output=True,
        )
    plan = root / PLAN_RELATIVE
    plan.parent.mkdir(parents=True)
    plan.write_text("# plan\n\nvalidated: yes\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "docs: the spec and the plan"],
        check=True,
        capture_output=True,
    )
    return root


def _implement_brief_text(digest=None):
    """An `implement` brief, optionally without its `## plan_hash` section."""
    section = "" if digest is None else f"\n## plan_hash\n{digest}\n"
    return (
        "# Coder\n\nstanding instructions\n\n"
        "# phase: implement\n# role: coder\n"
        f"\n## plan_path\n{PLAN_RELATIVE}\n"
        f"{section}"
    )


def _head_message(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%B", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_the_fake_coder_takes_its_trailer_hash_from_the_brief_not_the_plan_file(
    tmp_path,
):
    """R4: the fake may know only what the brief says. It must not hash the plan."""
    repo = _implement_repo(tmp_path)
    on_disk = fake_claude.plan_hash_of(repo / PLAN_RELATIVE)
    assert on_disk != BRIEF_HASH  # non-vacuity: the two really do disagree

    payload = fake_claude.build_result(
        "implement",
        fake_claude.payload_from_schema(IMPLEMENT_SCHEMA),
        _implement_brief_text(BRIEF_HASH),
        repo,
    )

    assert payload["plan_hash"] == BRIEF_HASH
    message = _head_message(repo)
    assert message.rstrip("\n").endswith(f"Plan-Hash: {BRIEF_HASH}")
    assert on_disk not in message


def test_an_implement_brief_with_no_plan_hash_section_stops_the_fake(tmp_path):
    """The mechanism that makes the production-wiring test fail if the input is
    ever dropped from `implement`'s `inputs` in `builtin/task.yaml`."""
    repo = _implement_repo(tmp_path)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.build_result(
            "implement",
            fake_claude.payload_from_schema(IMPLEMENT_SCHEMA),
            _implement_brief_text(),
            repo,
        )

    assert "plan_hash" in str(caught.value)
    assert "implement" in str(caught.value)


def test_a_padded_plan_hash_section_still_produces_a_single_line_trailer(tmp_path):
    """Review focus: a body padded with blank lines must not end the commit
    message in a blank `Plan-Hash:` line that `review_gate` reads as debris."""
    repo = _implement_repo(tmp_path)
    text = _implement_brief_text(BRIEF_HASH).replace(
        f"\n## plan_hash\n{BRIEF_HASH}\n", f"\n## plan_hash\n\n{BRIEF_HASH}\n\n"
    )

    payload = fake_claude.build_result(
        "implement", fake_claude.payload_from_schema(IMPLEMENT_SCHEMA), text, repo
    )

    assert payload["plan_hash"] == BRIEF_HASH
    assert _head_message(repo).rstrip("\n").splitlines()[-1] == (
        f"Plan-Hash: {BRIEF_HASH}"
    )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -k "implement or plan_hash or trailer" -v`
Expected: FAIL — the first test on `assert payload["plan_hash"] == "0badcafe"` (it is the sha256 of the plan file instead), the second because `build_result` returns a payload rather than raising, the third for the same reason as the first.

- [ ] **Step 3: Read the digest out of the brief**

In `tests/e2e/fake_claude.py`, replace the `implement` branch of `build_result` (lines 270-285):

```python
    if phase == "implement":
        relative = _section(found, "plan_path", phase)
        digest = plan_hash_of(Path(cwd) / relative)
        (Path(cwd) / IMPLEMENTATION_NAME).write_text(
            f"# implementation\n\n{SUMMARY}\n", encoding="utf-8"
        )
```

with:

```python
    if phase == "implement":
        # Card f26b377d: the hash comes from the brief's `## plan_hash` section,
        # never from hashing the plan. A fake that computed it would keep the
        # wiring test green with the input missing from `builtin/task.yaml`,
        # which is the one thing this tier exists to catch (R4).
        digest = _section(found, "plan_hash", phase)
        (Path(cwd) / IMPLEMENTATION_NAME).write_text(
            f"# implementation\n\n{SUMMARY}\n", encoding="utf-8"
        )
```

Leave the `git add -A`, the `git commit` with `f"Plan-Hash: {digest}"`, and the `override(...)` call exactly as they are. Leave `plan_hash_of` (line 223) and the `review` branch (lines 286-304) untouched — the reviewer still recomputes, and this card does not touch it.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — in particular `test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with` still holds, because the brief's digest and the independent sha256 oracle are now checked against each other for real.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_fake_claude.py
git commit -m "test: the fake coder reads its plan hash out of the brief"
```

---

## Task 5: Prove once that the wiring test fails without the input

**Files:**
- Temporarily modify, then restore: `src/agent_manager/workflow/builtin/task.yaml:68`

**Interfaces:**
- Consumes: everything from Tasks 1-4. Produces no code and no test — this is the one-off demonstration the spec's "Manual, one-off proof" paragraph calls for, reported in the run notes rather than pinned as a permanently-inverted test.

- [ ] **Step 1: Drop the input from the document**

In `src/agent_manager/workflow/builtin/task.yaml`, temporarily change line 68 back to:

```yaml
    inputs: [plan_path, spec_path, branch, base_branch]
```

- [ ] **Step 2: Run the wiring tier and record the failure**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: FAIL. The fake's `_section(found, "plan_hash", "implement")` raises `FakeClaudeError: the 'implement' brief has no `## plan_hash` section (it has: [...])`, the child process exits 1, the `implement` attempt ends as `harness_error`, and `test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done` fails with a non-`done` status.

Copy the failing test names and the `fake-claude:` stderr line into the run notes verbatim — that observation is the deliverable of this task.

- [ ] **Step 3: Restore the document**

In `src/agent_manager/workflow/builtin/task.yaml`, put line 68 back to:

```yaml
    inputs: [plan_path, spec_path, branch, base_branch, plan_hash]
```

- [ ] **Step 4: Confirm the restore with git**

Run: `git diff --stat`
Expected: no output — the working tree matches the last commit, so the proof left nothing behind.

- [ ] **Step 5: Run the whole suite green**

Run: `uv run pytest`
Expected: PASS, with no failures, no errors and no skips other than the toolchain skips `tests/e2e/conftest.py:72-77` already declares.

- [ ] **Step 6: Report**

Record in the card's run notes: the five deliverables that landed (the `plan_hash` table row, `implement`'s inputs, the coder's `system.md`, the fake's `implement` branch, the §7 row), the observed failure from Step 2, and the green `uv run pytest` output from Step 5. There is nothing to commit in this task.
