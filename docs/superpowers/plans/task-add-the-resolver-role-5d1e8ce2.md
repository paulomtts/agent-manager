<!-- task-pipeline: validated -->
# Subtask 5d1e8ce2: Add the resolver role and ResolveResult

Parent story 5216cbee ("The resolver: a role, a result and a document"), decision I3 in `docs/superpowers/specs/2026-09-25-integrate-design.md` (lines 60-73). This subtask narrows I3 to two artifacts: the `resolver` role bundle and the `ResolveResult` model. The design spec is the source of truth.

## Scope

In scope:

1. A new role bundle at `src/agent_manager/roles/bundles/resolver/`, with three files:
   - `system.md`: the resolver's system prompt (contents below).
   - `policy.toml`: exactly this content, laid out like `reviewer/policy.toml`:
     ```toml
     allowed_tools = ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]
     max_attempts = 2
     required_capabilities = []

     [default_model]
     claude = "opus"
     ```
   - `VENDORED.lock`: the same 14-byte content as the critic's and reviewer's locks, `vendored = []`.
   - No `methodology/` directory. The loader already accepts a bundle without one, as it does for critic and reviewer.
2. `class ResolveResult(_Result)` in `src/agent_manager/results.py`, with fields `resolved: bool` and `summary: str`. It inherits `extra="forbid"` and `strict=True` from `_Result`. It has no `serialization_alias`, because no reducer reads it.
3. Register `"ResolveResult": ResolveResult` in `RESULT_MODELS`. Update the module docstring, which lists the declared names, and the `RESULT_MODELS` docstring so both mention the new name. Once the sibling card lands, the name is declared by `builtin/integrate.yaml` instead of `task.yaml`, and the docstrings should say that.

Out of scope, because sibling b4bd3795 owns it: the prompt inputs `merge_tip` and `conflict_files`, `workflow/builtin/integrate.yaml`, `merge_completed_gate`, and every engine or end-to-end test against a real git conflict. This card also leaves `prompt.py` and the steps modules alone. Everything listed in section 6 of the integrate addendum stays out of scope too.

## system.md behaviour

The prompt tells the agent the following, in plain instructions:

- A merge is in progress in the current directory. The brief lists the conflicting files.
- Read both sides' real diffs, not just the conflict markers. For example, run `git diff HEAD...<merge_tip>` and look at what is already merged.
- Make each conflicted file correct for both stories' intent, not for whichever side wins visually.
- Stage every resolved file with `git add`, then finish with `git commit --no-edit`.
- Never run `git merge --abort`, `git reset`, `git checkout`, or anything else that rewrites or discards history.
- Do not touch files that are not conflicted.
- Then write the result file.

The prompt names no methodology file. The agent's `resolved` flag is advisory only. Git judges the real outcome through the sibling's gate, and the prompt must not suggest otherwise.

## Error paths

These are all `pydantic.ValidationError` from `ResolveResult.model_validate`:

- `resolved` is missing.
- `resolved` has the wrong type. Under strict mode, `"yes"` or `1` must be rejected, not coerced.
- There is an unknown extra key.

An invalid `policy.toml` is a `RoleBundleError` from the loader. The file above must load cleanly.

## Tests

Test placement follows section 14 of `2026-09-23-agent-manager-design.md`: models and pure loaders get unit tests. No step, engine or e2e test belongs to this card. The whole default suite must stay green, `tests/e2e` included.

Unit tests in `tests/test_results.py`, which mirrors `results.py`:
- `ResolveResult` accepts `{"resolved": True, "summary": "..."}`.
- It rejects a missing `resolved`.
- It rejects `resolved` of the wrong type (`"yes"` and/or `1`).
- It rejects an unknown field.
- Update `test_the_shipped_table_holds_exactly_the_six_declared_names` (line 36): add `"ResolveResult"` and rename it to say seven.
- Update `test_an_unknown_name_now_lists_the_six_registered_names` (lines 65-77): the expected string becomes `"CriticResult, ExploreResult, ImplementResult, PlanResult, ResolveResult, ReviewResult, SpecResult"`. Rename it to say seven.

Unit tests in `tests/roles/test_loader.py`, which mirrors `roles/loader.py`:
- `SHIPPED` (line 449) becomes `["coder", "critic", "explorer", "planner", "resolver", "reviewer", "spec_author"]`, sorted to match `list_roles`.
- `DEFAULT_MODELS` gains `"resolver": "opus"`.
- Rename `test_list_roles_returns_exactly_the_six_shipped_roles` to say seven, and fix the "six shipped" wording in the module docstring (line 9).
- Add a new test that the resolver's policy has `max_attempts == 2`, `default_model["claude"] == "opus"`, and `allowed_tools == ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]`.
- These tests are parametrized and pick up the resolver on their own: bundle loads, pins its claude model, vendored hash and lock/methodology agreement. So does `tests/test_prompt.py`'s `parametrize("name", sorted(results.RESULT_MODELS))`, which is a unit test. None of them need editing.

## Notes on the findings

- The exploration findings said to put a `vendored = []` line after `[default_model]` in `policy.toml`. That is wrong, and the policy above leaves it out. No existing `policy.toml` has that line: `vendored = []` lives only in `VENDORED.lock`. `Policy` is `extra="forbid"`, and TOML would parse the line as a key inside `default_model`, so it would break the bundle.
- The exploration summary was cut off at 8000 characters, mid-way through "VERIFICATION COMMANDS". Per `CLAUDE.md`, the verification command is `uv run pytest`. There is no separate lint or typecheck command.

---

# Resolver Role and ResolveResult Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `resolver` role bundle (system prompt, policy, empty vendor lock, no methodology) and a strict `ResolveResult{resolved: bool, summary: str}` model registered in `RESULT_MODELS`.

**Architecture:** Two independent additions. `results.py` gets one more `_Result` subclass plus a registry entry; its strictness (`extra="forbid"`, `strict=True`) is inherited, so no validators are written. The role bundle is pure data under `src/agent_manager/roles/bundles/resolver/`; `roles/loader.py` discovers it from the directory (no package-data config is needed), so no loader code changes. Only tests and data files change apart from `results.py`.

**Tech Stack:** Python, Pydantic v2, TOML (`tomllib`), pytest, run through `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-add-the-resolver-role-5d1e8ce2/docs/superpowers/specs/task-add-the-resolver-role-5d1e8ce2-design.md` (reproduced verbatim above). Parent addendum: `docs/superpowers/specs/2026-09-25-integrate-design.md` I3, lines 60-73.

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-add-the-resolver-role-5d1e8ce2`. Run every command from that directory. The branch is `m5/task-add-the-resolver-role-5d1e8ce2`. Do not assume `workflow/builtin/integrate.yaml`, `merge_tip`/`conflict_files` inputs or `merge_completed_gate` exist here: they belong to sibling card b4bd3795 and are not on this branch.

## Global Constraints

- Do NOT edit `src/agent_manager/prompt.py`, anything under `src/agent_manager/steps/`, `src/agent_manager/workflow/`, or `src/agent_manager/roles/loader.py`.
- `ResolveResult` fields: exactly `resolved: bool` and `summary: str`, no defaults, no `serialization_alias`.
- `resolver/policy.toml` holds exactly: `allowed_tools = ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]`, `max_attempts = 2`, `required_capabilities = []`, `[default_model]` `claude = "opus"`. It must NOT contain a `vendored` key.
- `resolver/VENDORED.lock` is exactly the 14 bytes `vendored = []\n`.
- No `resolver/methodology/` directory, and `system.md` names no methodology file.
- Test placement: unit tier only, in `tests/test_results.py` and `tests/roles/test_loader.py`. No step, engine or e2e tests.
- Verification: `uv run pytest` (whole default suite, `tests/e2e` included, must be green). No lint or typecheck command exists.

## Review Focus

- An agent writes `"resolved": "true"` or `"resolved": null` instead of a JSON boolean. It must be rejected, not coerced (strict mode). Pinned in Task 1 by `test_resolve_result_does_not_coerce_a_non_bool_into_resolved`, parametrized over `"yes"`, `"true"`, `1`, `None`.
- An agent omits `summary` and reports only `resolved`. That must be rejected, because a result with no account of what changed is useless to a human reading the journal. Pinned in Task 1 by `test_resolve_result_rejects_a_missing_summary`.
- The schema embedded in the agent's brief lets extra keys through or marks a field optional, so the agent is told a looser contract than validation enforces. Pinned in Task 1 by `test_resolve_result_schema_requires_both_fields_and_forbids_extras`.
- A later edit to `resolver/system.md` drops a guardrail (the never-abort/reset/checkout rule, or `git commit --no-edit`), so the agent may discard a merge. Pinned in Task 2 by `test_the_resolver_prompt_states_the_merge_rules`.
- Someone adds a `methodology/` directory or a lock entry to the resolver. The spec says it vendors nothing. Pinned in Task 2 by `test_the_resolver_vendors_no_methodology`.

---

### Task 1: `ResolveResult` model and its registry entry

**Files:**
- Modify: `src/agent_manager/results.py:1-15` (module docstring), `:106-137` (new class after `ReviewResult`, registry entry, registry docstring)
- Test: `tests/test_results.py:36-46`, `:65-78`, plus new tests appended after `test_review_result_dumps_the_two_counts_in_camel_case_only_under_by_alias` (currently ending at line 521)

**Interfaces:**
- Consumes: `results._Result` (existing base, `ConfigDict(extra="forbid", strict=True)`).
- Produces: `agent_manager.results.ResolveResult` with fields `resolved: bool`, `summary: str`, and `results.RESULT_MODELS["ResolveResult"] is results.ResolveResult`. Sibling b4bd3795 will declare `result: ResolveResult` in `builtin/integrate.yaml`.

- [ ] **Step 1: Update the two count-pinning tests in `tests/test_results.py`**

Replace lines 36-46 (the whole `test_the_shipped_table_holds_exactly_the_six_declared_names` function) with:

```python
def test_the_shipped_table_holds_exactly_the_seven_declared_names():
    # An equality, not a superset: a stray eighth key is a name the engine would
    # happily resolve for a phase that has no business declaring it.
    assert set(results.RESULT_MODELS) == {
        "ExploreResult",
        "CriticResult",
        "SpecResult",
        "PlanResult",
        "ImplementResult",
        "ReviewResult",
        "ResolveResult",
    }
```

Replace lines 65-78 (the whole `test_an_unknown_name_now_lists_the_six_registered_names` function) with:

```python
def test_an_unknown_name_now_lists_the_seven_registered_names():
    # The addendum's opening failure ends "(registered: nothing)". That exact
    # phrasing must be gone, and the seven sorted names must be what it offers.
    with pytest.raises(EngineError) as caught:
        results.resolve_result_model("VerifyResult", results.RESULT_MODELS, phase="verify")

    message = str(caught.value)
    assert caught.value.phase == "verify"
    assert "registered: nothing" not in message
    assert (
        "CriticResult, ExploreResult, ImplementResult, PlanResult, ResolveResult, "
        "ReviewResult, SpecResult"
        in message
    )
```

- [ ] **Step 2: Add the `ResolveResult` model tests to `tests/test_results.py`**

Insert this block directly after `test_review_result_dumps_the_two_counts_in_camel_case_only_under_by_alias` (after its last line, `assert review.model_dump()["commit_count"] == 3`) and before the `# --- The one place the snake_case/camelCase mismatch is reconciled` comment:

```python
def test_resolve_result_accepts_a_full_payload():
    resolve = results.ResolveResult.model_validate(
        {"resolved": True, "summary": "kept both stories' edits to results.py"}
    )

    assert resolve.resolved is True
    assert resolve.summary == "kept both stories' edits to results.py"


def test_resolve_result_rejects_a_missing_resolved():
    with pytest.raises(ValidationError) as caught:
        results.ResolveResult.model_validate({"summary": "merged both sides"})

    assert "resolved" in str(caught.value)


def test_resolve_result_rejects_a_missing_summary():
    # A bare verdict with no account of what changed is useless in the journal.
    with pytest.raises(ValidationError) as caught:
        results.ResolveResult.model_validate({"resolved": True})

    assert "summary" in str(caught.value)


@pytest.mark.parametrize("value", ["yes", "true", 1, None])
def test_resolve_result_does_not_coerce_a_non_bool_into_resolved(value):
    # Strict mode: an agent's "yes", "true" or 1 is sloppiness to catch, not
    # something to read as True; null is not a boolean either.
    with pytest.raises(ValidationError) as caught:
        results.ResolveResult.model_validate(
            {"resolved": value, "summary": "merged both sides"}
        )

    assert "resolved" in str(caught.value)


def test_resolve_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.ResolveResult.model_validate(
            {"resolved": True, "summary": "merged both sides", "conflict_files": []}
        )

    assert "conflict_files" in str(caught.value)


def test_resolve_result_schema_requires_both_fields_and_forbids_extras():
    # R2 embeds this schema in the resolver's brief: it must promise exactly
    # what validation enforces.
    schema = results.ResolveResult.model_json_schema()

    assert set(schema["required"]) == {"resolved", "summary"}
    assert set(schema["properties"]) == {"resolved", "summary"}
    assert schema["additionalProperties"] is False


def test_the_resolve_result_name_resolves_to_its_class():
    assert results.RESULT_MODELS["ResolveResult"] is results.ResolveResult
    assert (
        results.resolve_result_model(
            "ResolveResult", results.RESULT_MODELS, phase="resolve"
        )
        is results.ResolveResult
    )
```

- [ ] **Step 3: Run the new and updated tests to verify they fail**

Run: `uv run pytest tests/test_results.py -v -k "seven or resolve_result"`
Expected: FAIL. `test_the_shipped_table_holds_exactly_the_seven_declared_names` fails on the set comparison (`ResolveResult` missing). `test_an_unknown_name_now_lists_the_seven_registered_names` fails because the message lacks `ResolveResult`. Every `test_resolve_result_*` test and `test_the_resolve_result_name_resolves_to_its_class` fail with `AttributeError: module 'agent_manager.results' has no attribute 'ResolveResult'` (or `KeyError: 'ResolveResult'`).

- [ ] **Step 4: Add the model to `src/agent_manager/results.py`**

Insert after the `ReviewResult` class (after line 122, `plan_hash: str`) and before `RESULT_MODELS`:

```python


class ResolveResult(_Result):
    """The `resolve` phase's result file (`builtin/integrate.yaml`, addendum I3).

    `resolved` is advisory. Whether the merge really completed is something git
    can measure, so `merge_completed_gate` judges it from the repository, not from
    this flag. No `serialization_alias` on either field, because no reducer reads a
    resolve result under a camelCase name.
    """

    resolved: bool
    summary: str
```

- [ ] **Step 5: Register it and update both docstrings in `src/agent_manager/results.py`**

Replace the `RESULT_MODELS` block and its docstring (currently lines 125-137) with:

```python
RESULT_MODELS: dict[str, type[BaseModel]] = {
    "ExploreResult": ExploreResult,
    "CriticResult": CriticResult,
    "SpecResult": SpecResult,
    "PlanResult": PlanResult,
    "ImplementResult": ImplementResult,
    "ReviewResult": ReviewResult,
    "ResolveResult": ResolveResult,
}
"""Every `result:` name a builtin workflow declares, keyed by class name.

`builtin/task.yaml` declares the first six; `builtin/integrate.yaml` declares
`ResolveResult` for its `resolve` phase. `Verification` is absent on purpose: no
phase declares it, and it is reachable only as `ExploreResult.verification`.
"""
```

Replace lines 7-10 of the module docstring:

```python
`RESULT_MODELS` is that mapping: every `result:` name `builtin/task.yaml`
declares -- `ExploreResult`, `CriticResult`, `SpecResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` -- against the model that validates that
phase's `result.json`.
```

with:

```python
`RESULT_MODELS` is that mapping: every `result:` name a builtin workflow
declares -- `ExploreResult`, `CriticResult`, `SpecResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` from `builtin/task.yaml`, and `ResolveResult`
from `builtin/integrate.yaml` -- against the model that validates that phase's
`result.json`.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_results.py tests/test_prompt.py -v`
Expected: PASS, all tests. `tests/test_prompt.py::test_every_shipped_result_model_embeds_its_own_schema_in_the_contract[ResolveResult]` now appears and passes.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/results.py tests/test_results.py
git commit -m "Add ResolveResult and register it in RESULT_MODELS"
```

---

### Task 2: The `resolver` role bundle

**Files:**
- Create: `src/agent_manager/roles/bundles/resolver/system.md`
- Create: `src/agent_manager/roles/bundles/resolver/policy.toml`
- Create: `src/agent_manager/roles/bundles/resolver/VENDORED.lock`
- Test: `tests/roles/test_loader.py:9` (docstring), `:449-458` (`SHIPPED`, `DEFAULT_MODELS`), `:468-469` (rename), plus new tests appended at end of file

**Interfaces:**
- Consumes: `agent_manager.roles.loader.load_role(name) -> RoleBundle`, `loader.list_roles() -> list[str]`, `loader.bundles_dir() -> Path` (all existing, unchanged).
- Produces: `load_role("resolver")` returns a `RoleBundle` with `policy.allowed_tools == ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]`, `policy.max_attempts == 2`, `policy.default_model == {"claude": "opus"}`, `methodology == {}`. Sibling b4bd3795 will reference `role: resolver` from `integrate.yaml`.

- [ ] **Step 1: Update the shipped-set pins in `tests/roles/test_loader.py`**

Line 9, change:

```python
Synthetic bundles are built in `tmp_path` for every error path; the six shipped
```

to:

```python
Synthetic bundles are built in `tmp_path` for every error path; the seven shipped
```

Replace lines 449-458 with:

```python
SHIPPED = [
    "coder",
    "critic",
    "explorer",
    "planner",
    "resolver",
    "reviewer",
    "spec_author",
]

DEFAULT_MODELS = {
    "explorer": "sonnet",
    "spec_author": "opus",
    "planner": "opus",
    "critic": "sonnet",
    "coder": "sonnet",
    "reviewer": "opus",
    "resolver": "opus",
}
```

Replace:

```python
def test_list_roles_returns_exactly_the_six_shipped_roles():
    assert loader.list_roles() == SHIPPED
```

with:

```python
def test_list_roles_returns_exactly_the_seven_shipped_roles():
    assert len(SHIPPED) == 7
    assert loader.list_roles() == SHIPPED
```

- [ ] **Step 2: Append the resolver-specific tests to the end of `tests/roles/test_loader.py`**

```python
def test_the_resolver_policy_allows_editing_and_retries_once_on_opus():
    policy = loader.load_role("resolver").policy

    assert policy.allowed_tools == ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]
    assert policy.max_attempts == 2
    assert policy.default_model == {"claude": "opus"}
    assert policy.required_capabilities == []


def test_the_resolver_vendors_no_methodology():
    directory = loader.bundles_dir() / "resolver"

    assert loader.load_role("resolver").methodology == {}
    assert not (directory / "methodology").exists()
    assert (directory / "VENDORED.lock").read_bytes() == b"vendored = []\n"


@pytest.mark.parametrize(
    "rule",
    [
        "merge is in progress",
        "git diff HEAD...",
        "both stories",
        "git add",
        "git commit --no-edit",
        "git merge --abort",
        "git reset",
        "git checkout",
        "not conflicted",
        "result file",
    ],
)
def test_the_resolver_prompt_states_the_merge_rules(rule):
    # Each phrase carries one rule from the spec's "system.md behaviour" list;
    # losing one lets the agent discard a merge or stop short of committing it.
    assert rule in loader.load_role("resolver").system
```

- [ ] **Step 3: Run the loader tests to verify they fail**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: FAIL. `test_list_roles_returns_exactly_the_seven_shipped_roles` fails (list lacks `"resolver"`). The `[resolver]` cases of `test_every_shipped_bundle_loads`, `test_every_shipped_bundle_pins_its_claude_model`, `test_a_shipped_bundle_vendors_only_methodology_its_system_prompt_names` and all new resolver tests fail with `RoleBundleError: role 'resolver': no such role bundle`. The `[resolver]` cases of `test_shipped_vendored_files_match_their_recorded_hashes` and `test_shipped_lock_and_methodology_directory_agree` fail with `FileNotFoundError` on `VENDORED.lock`. Every other test passes.

- [ ] **Step 4: Create `src/agent_manager/roles/bundles/resolver/VENDORED.lock`**

Exact content (one line, trailing newline, 14 bytes total):

```toml
vendored = []
```

- [ ] **Step 5: Create `src/agent_manager/roles/bundles/resolver/policy.toml`**

Exact content (no `vendored` key; `Policy` is `extra="forbid"`):

```toml
allowed_tools = ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]
max_attempts = 2
required_capabilities = []

[default_model]
claude = "opus"
```

- [ ] **Step 6: Create `src/agent_manager/roles/bundles/resolver/system.md`**

Exact content:

```markdown
# Resolver

A merge is in progress in the current directory. The brief lists the conflicting
files. Your job is to finish that merge so both stories survive it.

- Read both sides' real diffs, not just the conflict markers. Run
  `git diff HEAD...<merge_tip>` to see what the incoming story changed, and read
  the history on `HEAD` to see what is already merged.
- Make each conflicted file correct for both stories' intent, not for whichever
  side wins visually. Keep every change that both stories need, and remove every
  conflict marker.
- Stage every resolved file with `git add`, then finish the merge with
  `git commit --no-edit`.
- Never run `git merge --abort`, `git reset`, `git checkout`, or anything else
  that rewrites or discards history.
- Do not touch files that are not conflicted.
- Then write the result file. Say honestly whether you resolved the merge, and
  summarize what you did in each file. Git, not your report, decides whether the
  merge is complete.
```

- [ ] **Step 7: Run the loader tests to verify they pass**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: PASS, all tests, including the `[resolver]` parametrized cases and the three new resolver tests.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/roles/bundles/resolver tests/roles/test_loader.py
git commit -m "Add the resolver role bundle"
```

---

### Task 3: Full-suite verification

**Files:** none changed.

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.
- Produces: nothing new. This task is evidence that the whole default suite, `tests/e2e` included, is green.

- [ ] **Step 1: Run the full default suite**

Run: `uv run pytest`
Expected: PASS, zero failures and zero errors. In particular `tests/test_dispatch.py` (compares against `RESULT_MODELS` itself), `tests/workflow/test_builtin_task.py` (resolves only names `task.yaml` declares), `tests/e2e/test_production_wiring.py` and `tests/e2e/test_real_harness.py` (slow real-harness test stays skipped/opt-in) are unaffected.

- [ ] **Step 2: If anything fails, fix it inside this card's scope and re-run**

A failure outside `results.py`, the resolver bundle, `tests/test_results.py` or `tests/roles/test_loader.py` most likely means a test pins the six-name or six-role set in a way the exploration missed. Search with `rg -n "six|ReviewResult, SpecResult|spec_author\"\]" tests` and update that pin to seven in the same style as Task 1 Step 1 and Task 2 Step 1. Do not touch `prompt.py`, `steps/`, `workflow/` or `loader.py`. Then run `uv run pytest` again and commit only if something changed:

```bash
git add tests
git commit -m "Update remaining six-name pins for the resolver"
```
