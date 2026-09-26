<!-- task-pipeline: validated -->
# Split the critic into spec_critic and plan_critic (card c7bea6a2)

Subtask of story be007353 "Close the task.js gaps". Narrows plan Task 2.3 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:535-568`) and design addendum §7 item 3 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:354-360`). No new design here.

## Scope

- Create `src/agent_manager/roles/bundles/spec_critic/` and `src/agent_manager/roles/bundles/plan_critic/`, each with `system.md`, `policy.toml`, `VENDORED.lock`.
- `policy.toml` and `VENDORED.lock` in both are byte-for-byte copies of the current `critic/` ones (`allowed_tools = ["Read", "Grep", "Glob"]`, `max_attempts = 1`, `required_capabilities = []`, `default_model.claude = "sonnet"`; `vendored = []`).
- Delete `src/agent_manager/roles/bundles/critic/` entirely.
- `spec_critic/system.md` is ported from leave-me-alone's `task.js:614-629`. It covers the spec's completeness, consistency, clarity, scope and YAGNI, checked against this repo's own architecture docs and the card's sibling subtasks. It keeps the calibration paragraph and the "fold fixes" instruction. `task.js:630` reads "Return blockers=true ONLY if ..." (capitalized); lowercase that one word to "only" when porting, so the ported text reads "Return blockers=true only if ...", matching `plan_critic`'s own source line (`task.js:716`) and the shared literal-string requirement below. This is the only deviation from a verbatim port.
- `plan_critic/system.md` is ported from `task.js:700-715` verbatim (its "Return blockers=true only if ..." line, `task.js:716`, already reads lowercase). It checks the plan against the settled spec for completeness, spec alignment, decomposition and buildability. A defect that traces back to the SPEC sets `blockers=true` and is not patched around in the plan. It keeps the calibration paragraph and the "fold fixes" instruction. It must NOT contain the `<!-- task-pipeline: validated -->` line, because `plan_check.mark_validated` (`steps/plan_check.py:202`, wired at `builtin/task.yaml:57-59`) owns that marker.
- Both briefs tell the agent to: verify every suspicion against the actual files before reporting it; fold every CONFIRMED fix directly into the file under review (spec or plan); and set `blockers=true` only for what needs a human decision. Each brief must contain these literal strings, byte-for-byte including case: "Verify every suspicion", "Fold every CONFIRMED fix", "blockers=true only" (lowercase "only" in both bundles, per the previous two bullets).
- In `src/agent_manager/workflow/builtin/task.yaml`, change `validate_spec.role` from `critic` to `spec_critic` (line 38) and `validate_plan.role` from `critic` to `plan_critic` (line 52). Make the same change in `src/agent_manager/workflow/task.py` (lines 65 and 81, `role="critic"`). Per the G9 digest-pin rule, the two files must stay identical in effect. Gates (`critic_blockers_gate`) are unchanged.
- Update every test that names the old role. Re-run `grep -rn '"critic"\|role: critic' src tests` after editing, and it must come back empty. Known hits:
  - `tests/test_prompt.py:670`
  - `tests/roles/test_loader.py:451,463`. `SHIPPED` becomes the eight sorted names `coder, explorer, plan_critic, planner, resolver, reviewer, spec_author, spec_critic`. `DEFAULT_MODELS` maps both critics to `sonnet`. Rename the "seven shipped roles" test and its count to eight, and fix the module docstring's "seven shipped".
  - `tests/e2e/test_fake_claude.py:276,296,317,334`. Use `spec_critic` for these `validate_spec` fixtures. `fake_claude.py` dispatches on phase, not role, so no fake logic changes.

## Unchanged / out of scope

- `CriticResult` in `results.py:56-61` (`blockers`, `reason`, `summary`). Do not edit `results.py`. Its docstring saying "the `critic` phase" is not a grep hit and stays as it is.
- `reducers.critic_blockers_gate` and the registry names.
- `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads (G10).
- Sibling cards: the reviewer bundle (ee3cc742, done), `review_blockers_gate` (468daea9, done) and `steps/verify.py` (cf8b3888, blocked on this card).
- Supervisor tree, exactly-once phases, benchmarking prompts and upstream pygents fixes.

## Observable behavior and error paths

- `load_role("spec_critic")` and `load_role("plan_critic")` both load whole. `list_roles()` lists both and no longer lists `critic`.
- `load_role("critic")` raises `RoleBundleError` because the bundle no longer exists.
- A workflow run's `validate_spec` and `validate_plan` phases render their briefs from the new bundles. Result schema, gate behavior and the validated marker are unchanged.
- Flag, do not silently resolve: the briefs tell the critic to fold fixes into the file, but the copied policy allows only `Read`/`Grep`/`Glob` (no `Edit`/`Write`). The plan says to copy the policy literally, so this card does just that. If implementation or tests surface a conflict, raise it for a human decision. Do not add tools beyond the plan's instructions.

## Tests

Tier placement follows design spec §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:497-513`), CLAUDE.md's "tests mirror source", and addendum §9 "roles: golden briefs" (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:399-400`). Role bundles are a deterministic package-file parse, so the new tests are golden-brief unit tests.

- `tests/roles/test_critic_briefs.py::test_critic_brief[spec_critic]` (unit, golden brief) asserts the brief contains "completeness", "consistency", "clarity", "scope", "YAGNI", "sibling subtasks", plus the three shared needles.
- `tests/roles/test_critic_briefs.py::test_critic_brief[plan_critic]` (unit, golden brief) asserts the brief contains "completeness", "spec alignment", "decomposition", "buildability", "traces back to the SPEC", plus the three shared needles.
- `tests/roles/test_critic_briefs.py::test_the_generic_critic_is_gone` (unit, golden brief) asserts `load_role("critic")` raises.
- Updated existing tests, with tiers unchanged:
  - `tests/roles/test_loader.py` shipped-roles/default-model checks (unit, loader)
  - `tests/test_prompt.py` (unit, pure prompt rendering)
  - `tests/e2e/test_fake_claude.py` (fake-harness fixtures in the default suite). Per RULE (4), canned fake responses may rely only on what the new brief states.
- Optional, same unit tier: assert that `plan_critic`'s brief does not contain `task-pipeline: validated`.

## Verification

`uv run pytest` must pass with the whole default suite green, including `tests/e2e`, on both engines while `--engine` exists. There is no typecheck or lint command.

---

# Split the critic into spec_critic and plan_critic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic `critic` role bundle with two task.js-ported bundles, `spec_critic` and `plan_critic`, and point `validate_spec` / `validate_plan` at them in both `builtin/task.yaml` and `workflow/task.py`.

**Architecture:** Role bundles are plain package files (`system.md`, `policy.toml`, `VENDORED.lock`) under `src/agent_manager/roles/bundles/<role>/`, parsed whole-or-raise by `roles/loader.py`. `Workflow.validate` checks every agent phase's `role` against the shipped bundles, so the bundle deletion and the `role:` rename must land in the same commit. `workflow/task.py`'s `TASK` is digest-pinned to `builtin/task.yaml` (`tests/workflow/test_declared.py::test_task_equals_the_shipped_yaml`), so both files change identically.

**Tech Stack:** Python, pytest, Pydantic, TOML; `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-split-the-critic-into-c7bea6a2/docs/superpowers/specs/task-split-the-critic-into-c7bea6a2-design.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-split-the-critic-into-c7bea6a2`. Run every command from there. The branch `m6/task-split-the-critic-into-c7bea6a2` is cut from `m6/task-rewrite-the-reviewer-ee3cc742`; nothing from `cf8b3888` (verify) exists on it, and this plan does not need it.

## Global Constraints

- Both new `policy.toml` files are byte-for-byte copies of `src/agent_manager/roles/bundles/critic/policy.toml`: `allowed_tools = ["Read", "Grep", "Glob"]`, `max_attempts = 1`, `required_capabilities = []`, `[default_model] claude = "sonnet"`.
- Both new `VENDORED.lock` files are byte-for-byte copies of `critic/VENDORED.lock`: `vendored = []`.
- Do NOT add `Edit`/`Write` to either policy, even though the briefs say "fold fixes into the file". This is the spec's open point for a human decision; the implementer raises it, does not resolve it.
- Each brief contains, byte-for-byte: "Verify every suspicion", "Fold every CONFIRMED fix", "blockers=true only".
- `spec_critic/system.md` is a port of `task.js:614-630`; the only content deviation is `ONLY` -> `only` in the last line. `plan_critic/system.md` is a verbatim port of `task.js:700-716` minus the validated-marker sentence. In both, task.js's `${...}` interpolations are replaced by references to this brief's own input sections (`## card`, `## spec_path`, `## plan_path`), which is how the reviewer bundle (card ee3cc742) ported task.js too; this is substitution, not new content.
- `plan_critic/system.md` must not contain `<!-- task-pipeline: validated -->` (`plan_check.VALIDATED_MARKER`).
- Do not edit `src/agent_manager/results.py`, `steps/reducers.py`, `workflow/registry.py`, `steps/verify.py`, `tests/e2e/fake_claude.py`, or the `reviewer` bundle.
- After Task 2, `grep -rn '"critic"\|role: critic' src tests` prints nothing.
- Final `SHIPPED` in `tests/roles/test_loader.py` is exactly `coder, explorer, plan_critic, planner, resolver, reviewer, spec_author, spec_critic`.
- Verification: `uv run pytest` (whole default suite, including `tests/e2e`). No typecheck or lint command exists.

## Review Focus

1. A critic's system text containing brief-structure literals (`## Result contract`, a ```` ```json ```` fence, or a line starting `# phase:` / `# role:`): `prompt.compose_brief` puts the system text first and `tests/e2e/fake_claude.py:48` finds the phase header by regex, so such a line would be mistaken for the brief's own structure. Expected: none present. Pinned in Task 1 (`test_critic_system_text_cannot_be_mistaken_for_brief_structure`).
2. A `${...}` task.js interpolation left unported in a brief: the agent would be told to fold fixes into a file literally named `${SPEC_PATH}`. Expected: no `${` in either brief. Pinned in Task 1 (`test_critic_brief_has_no_unrendered_task_js_interpolation`).
3. `plan_critic` telling the agent to write the validated marker: a critic that prepends it on its own would make `plan_check.find_validated_plan` trust a plan even when the gate later blocks. Expected: the marker literal is absent. Pinned in Task 1 (`test_plan_critic_leaves_the_validated_marker_to_mark_validated`).
4. A brief pointing at an input section its phase never receives (for example `plan_critic` referring to `## card`, which `validate_plan` does not get): the agent would search for a section that is not there. Expected: every `` `## name` `` a critic's brief names is in its phase's `inputs`. Pinned in Task 2 (`test_each_critic_brief_names_only_its_phases_inputs`).
5. A policy that silently drifts from the copied critic policy (for example `Edit` added to resolve the open point without a human decision). Expected: both policies equal the old critic policy field by field. Pinned in Task 1 (`test_critic_policy_is_the_generic_critic_policy_unchanged`).

---

### Task 1: Add the spec_critic and plan_critic bundles

**Files:**
- Create: `src/agent_manager/roles/bundles/spec_critic/system.md`
- Create: `src/agent_manager/roles/bundles/spec_critic/policy.toml` (copy of `critic/policy.toml`)
- Create: `src/agent_manager/roles/bundles/spec_critic/VENDORED.lock` (copy of `critic/VENDORED.lock`)
- Create: `src/agent_manager/roles/bundles/plan_critic/system.md`
- Create: `src/agent_manager/roles/bundles/plan_critic/policy.toml` (copy of `critic/policy.toml`)
- Create: `src/agent_manager/roles/bundles/plan_critic/VENDORED.lock` (copy of `critic/VENDORED.lock`)
- Create (test): `tests/roles/test_critic_briefs.py`
- Modify (test): `tests/roles/test_loader.py:449-479`

**Interfaces:**
- Consumes: `agent_manager.roles.loader.load_role(name: str, *, root: Path | None = None) -> RoleBundle` (`RoleBundle.system: str`, `RoleBundle.policy: Policy` with `allowed_tools: list[str]`, `max_attempts: int`, `required_capabilities: list[str]`, `default_model: dict[str, str]`, `RoleBundle.methodology: dict[str, str]`); `agent_manager.roles.loader.list_roles() -> list[str]`; `agent_manager.steps.plan_check.VALIDATED_MARKER: str` (`"<!-- task-pipeline: validated -->"`).
- Produces: loadable roles `"spec_critic"` and `"plan_critic"`; module `tests/roles/test_critic_briefs.py` with the constant `CRITICS = ("spec_critic", "plan_critic")`, which Task 2 appends a test to. The `critic` bundle still exists at the end of this task (Task 2 deletes it), so `SHIPPED` temporarily holds nine names.

- [ ] **Step 1: Write the failing golden-brief tests**

Create `tests/roles/test_critic_briefs.py`:

```python
"""Golden briefs for the `spec_critic` and `plan_critic` roles (card c7bea6a2).

Placement: pygents-engine-design.md §9 (lines 399-400) tests roles as golden
briefs -- the shipped bundle is loaded through the public loader and its
system text is checked for what the validate phases depend on. No model call,
no git repository, no harness; same tier as `test_loader.py` and
`test_reviewer_brief.py`.
"""

import pytest

from agent_manager.roles.loader import load_role
from agent_manager.steps.plan_check import VALIDATED_MARKER

CRITICS = ("spec_critic", "plan_critic")

SHARED = ("Verify every suspicion", "Fold every CONFIRMED fix", "blockers=true only")

CRITERIA = {
    "spec_critic": (
        "completeness",
        "consistency",
        "clarity",
        "scope",
        "YAGNI",
        "sibling subtasks",
    ),
    "plan_critic": (
        "completeness",
        "spec alignment",
        "decomposition",
        "buildability",
        "traces back to the SPEC",
    ),
}


@pytest.mark.parametrize("role", CRITICS)
def test_critic_brief(role):
    text = load_role(role).system
    for needle in CRITERIA[role] + SHARED:
        assert needle in text, needle


def test_plan_critic_leaves_the_validated_marker_to_mark_validated():
    # plan_check.mark_validated (steps/plan_check.py:202) owns the marker; a
    # critic that wrote it would sign a plan the gate may still block.
    text = load_role("plan_critic").system
    assert VALIDATED_MARKER not in text
    assert "task-pipeline: validated" not in text


@pytest.mark.parametrize("role", CRITICS)
def test_critic_system_text_cannot_be_mistaken_for_brief_structure(role):
    # The system text opens every brief (prompt.compose_brief). These literals
    # are how the brief's own result contract and phase header are found
    # (tests/e2e/fake_claude.py:48), so the role must never carry them itself.
    text = load_role(role).system
    assert "## Result contract" not in text
    assert "```json" not in text
    assert not any(
        line.startswith(("# phase:", "# role:")) for line in text.splitlines()
    )


@pytest.mark.parametrize("role", CRITICS)
def test_critic_brief_has_no_unrendered_task_js_interpolation(role):
    assert "${" not in load_role(role).system


@pytest.mark.parametrize("role", CRITICS)
def test_critic_policy_is_the_generic_critic_policy_unchanged(role):
    # Copied byte-for-byte from the deleted critic bundle. The briefs say
    # "fold fixes into the file" yet no Edit/Write is granted: that gap is the
    # spec's open point for a human decision, so it is pinned, not resolved.
    bundle = load_role(role)
    assert bundle.policy.allowed_tools == ["Read", "Grep", "Glob"]
    assert bundle.policy.max_attempts == 1
    assert bundle.policy.required_capabilities == []
    assert bundle.policy.default_model == {"claude": "sonnet"}
    assert bundle.methodology == {}
```

- [ ] **Step 2: Run the new tests and watch them fail**

Run: `uv run pytest tests/roles/test_critic_briefs.py -v`
Expected: every test FAILS with `RoleBundleError: role 'spec_critic': no such role bundle (...)` (or `'plan_critic'`), because neither bundle exists yet.

- [ ] **Step 3: Update the shipped-roles list in `tests/roles/test_loader.py`**

Replace lines 449-479 (the `SHIPPED` list, `DEFAULT_MODELS`, and `test_list_roles_returns_exactly_the_seven_shipped_roles`) with:

```python
SHIPPED = [
    "coder",
    "critic",
    "explorer",
    "plan_critic",
    "planner",
    "resolver",
    "reviewer",
    "spec_author",
    "spec_critic",
]

DEFAULT_MODELS = {
    "explorer": "sonnet",
    "spec_author": "opus",
    "planner": "opus",
    "critic": "sonnet",
    "spec_critic": "sonnet",
    "plan_critic": "sonnet",
    "coder": "sonnet",
    "reviewer": "opus",
    "resolver": "opus",
}


def shipped_lock(role: str) -> list[dict[str, str]]:
    """`VENDORED.lock`'s entries for a shipped role, read independently of the
    loader so the drift check does not depend on the code it guards."""
    path = loader.bundles_dir() / role / "VENDORED.lock"
    return tomllib.loads(path.read_text(encoding="utf-8"))["vendored"]


def test_list_roles_returns_exactly_the_nine_shipped_roles():
    assert len(SHIPPED) == 9
    assert loader.list_roles() == SHIPPED
```

(`critic` stays in this intermediate list because its bundle still ships until Task 2.)

- [ ] **Step 4: Run the loader test and watch it fail**

Run: `uv run pytest tests/roles/test_loader.py -v -k "shipped"`
Expected: `test_list_roles_returns_exactly_the_nine_shipped_roles` FAILS (`list_roles()` returns seven names), and the `spec_critic` / `plan_critic` parametrizations of `test_every_shipped_bundle_loads` and `test_every_shipped_bundle_pins_its_claude_model` FAIL with `RoleBundleError ... no such role bundle`.

- [ ] **Step 5: Copy the policy and lock into both new bundles**

```bash
mkdir -p src/agent_manager/roles/bundles/spec_critic src/agent_manager/roles/bundles/plan_critic
cp src/agent_manager/roles/bundles/critic/policy.toml src/agent_manager/roles/bundles/spec_critic/policy.toml
cp src/agent_manager/roles/bundles/critic/VENDORED.lock src/agent_manager/roles/bundles/spec_critic/VENDORED.lock
cp src/agent_manager/roles/bundles/critic/policy.toml src/agent_manager/roles/bundles/plan_critic/policy.toml
cp src/agent_manager/roles/bundles/critic/VENDORED.lock src/agent_manager/roles/bundles/plan_critic/VENDORED.lock
```

After this, each `policy.toml` reads exactly:

```toml
allowed_tools = ["Read", "Grep", "Glob"]
max_attempts = 1
required_capabilities = []

[default_model]
claude = "sonnet"
```

and each `VENDORED.lock` reads exactly:

```toml
vendored = []
```

- [ ] **Step 6: Write `spec_critic/system.md`**

Create `src/agent_manager/roles/bundles/spec_critic/system.md` with exactly this content (ported from `task.js:615-630`; `${SPEC_PATH}` and `${repo} subtask card ${id} in ${repoDir}` become references to this brief's `## spec_path` and `## card` sections; `ONLY` becomes `only` in the last line, per the spec):

```markdown
# Spec critic

Adversarial review of the SPEC at the path in this brief's `## spec_path` section, for the subtask card in this brief's `## card` section, in this repository. No plan exists yet; do not write one.

Check it against superpowers' spec reviewer criteria:
- completeness — TODOs, placeholders, "TBD", missing sections
- consistency — internal contradictions, conflicting requirements
- clarity — anything ambiguous enough that someone would build the wrong thing
- scope — focused enough for ONE implementation plan, not several subsystems
- YAGNI — unrequested features, over-engineering

Also check it against this repo's own architecture/standards docs (the exploration findings cite them; read them) and against what sibling subtasks own, so this spec does not drift into their work.

Verify every suspicion against the actual files before reporting. Fold every CONFIRMED fix directly into that spec file, keeping its structure — the next stage plans from that file, so an unfixed spec becomes an unfixable plan.

Calibration: only flag what would cause a real problem when planning or implementing. Minor wording and stylistic preference are not issues; this stage gates a run.

Return blockers=true only if something unresolvable remains (a contradiction needing a human decision), with the reason.
```

- [ ] **Step 7: Write `plan_critic/system.md`**

Create `src/agent_manager/roles/bundles/plan_critic/system.md` with exactly this content (verbatim from `task.js:700-716`; `${plan}` and `${SPEC_PATH}` become references to this brief's `## plan_path` and `## spec_path` sections, and `${repo} subtask card ${id} in ${repoDir}` becomes "its subtask card, in this repository" because `validate_plan` receives no `card` input; the sentence "On success (blockers=false), also prepend the exact line `<!-- task-pipeline: validated -->` ... a resumed run can trust." is removed because `plan_check.mark_validated` owns that marker):

```markdown
# Plan critic

Adversarial review of the PLAN at the path in this brief's `## plan_path` section — its subtask card, in this repository. Its spec is at the path in this brief's `## spec_path` section and was already reviewed and corrected; treat it as settled and review the plan AGAINST it rather than re-litigating it.

Try to BREAK it before implementation: contradictions with this repo's architecture/standards docs (read them; the exploration cites them), decisions that bite sibling subtasks, dishonest or tautological tests, config side-effects, steps not executable verbatim. Verify every suspicion against the actual files/tools before reporting (run commands if needed).

Check it against superpowers' plan reviewer criteria:
- completeness — TODOs, placeholders, incomplete tasks, missing steps
- spec alignment — every spec requirement has a task, and no major scope creep beyond it
- task decomposition — clear boundaries, each step one actionable thing
- buildability — could an engineer follow this without getting stuck?

Calibration: only flag what would cause a real problem during implementation. An implementer building the wrong thing, or getting stuck, is an issue. Minor wording, stylistic preference and nice-to-haves are not — this stage gates a run, so treat it as a gate and not a critique.

If a plan defect traces back to the SPEC being wrong, say so in reason and set blockers=true rather than patching the plan around it: a plan that compensates for a bad spec hides the real problem from every later stage.

Fold every CONFIRMED fix directly into the plan file (edit it), keeping its structure. Explore already rolled the card's status to in_progress on a best-effort basis; do not touch card status here either way.

Return blockers=true only if something unresolvable remains (spec contradiction needing a human decision) with the reason.
```

- [ ] **Step 8: Run the new and loader tests and watch them pass**

Run: `uv run pytest tests/roles/test_critic_briefs.py tests/roles/test_loader.py -v`
Expected: all PASS (the nine-role list now matches `list_roles()`, and every golden-brief assertion holds).

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. Nothing references the new roles yet and `critic` still ships, so no other test changes.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/roles/bundles/spec_critic src/agent_manager/roles/bundles/plan_critic tests/roles/test_critic_briefs.py tests/roles/test_loader.py
git commit -m "feat(roles): add spec_critic and plan_critic ported from task.js"
```

(Add whatever commit trailers your own brief requires.)

---

### Task 2: Point the validate phases at the new critics and delete `critic`

**Files:**
- Modify: `src/agent_manager/workflow/builtin/task.yaml:38` and `:52`
- Modify: `src/agent_manager/workflow/task.py:65` and `:81`
- Delete: `src/agent_manager/roles/bundles/critic/` (`system.md`, `policy.toml`, `VENDORED.lock`)
- Modify (test): `tests/roles/test_critic_briefs.py` (append one test)
- Modify (test): `tests/roles/test_loader.py:1-12` (docstring) and the `SHIPPED` / `DEFAULT_MODELS` / list-roles test block edited in Task 1
- Modify (test): `tests/workflow/test_declared.py` (append one test)
- Modify (test): `tests/workflow/test_builtin_task.py` (imports at lines 10-40; append two tests after `test_both_validation_phases_resolve_to_the_same_critic_model`, line 316-332)
- Modify (test): `tests/test_prompt.py:667-677`
- Modify (test): `tests/e2e/test_fake_claude.py:276`, `:296`, `:317`, `:334`

**Interfaces:**
- Consumes: from Task 1, the loadable roles `"spec_critic"` / `"plan_critic"` and `tests/roles/test_critic_briefs.py` (imports `pytest`, `load_role`). `agent_manager.roles.loader.RoleBundleError` (attribute `reason: str`; `load_role` raises it with reason `"no such role bundle"` for a missing directory). `agent_manager.workflow.task.TASK` (`Workflow.phase(name)` returns the phase; `AgentPhase.role: str`). `agent_manager.workflow.load_builtin("task")` (`.phase(name)` returns a loader `AgentPhase` with `role: str` and `inputs: list[str]`).
- Produces: `validate_spec.role == "spec_critic"`, `validate_plan.role == "plan_critic"` in both declarations; no `critic` bundle.

- [ ] **Step 1: Write the failing "critic is gone" test**

Append to `tests/roles/test_critic_briefs.py` (and add `from agent_manager.roles.loader import RoleBundleError` beside the existing `load_role` import, making it `from agent_manager.roles.loader import RoleBundleError, load_role`):

```python
def test_the_generic_critic_is_gone():
    with pytest.raises(RoleBundleError) as excinfo:
        load_role("critic")

    assert excinfo.value.reason == "no such role bundle"
```

- [ ] **Step 2: Write the failing workflow-wiring tests**

Append to `tests/workflow/test_declared.py`:

```python
def test_each_validation_phase_names_its_own_critic():
    assert TASK.phase("validate_spec").role == "spec_critic"
    assert TASK.phase("validate_plan").role == "plan_critic"
```

In `tests/workflow/test_builtin_task.py`, add these imports next to the existing ones (after `import inspect` add `import re`; after the `from agent_manager.results import (...)` block add `from agent_manager.roles.loader import load_role`), then add directly after `test_both_validation_phases_resolve_to_the_same_critic_model` (which ends at line 332):

```python
def test_each_validation_phase_names_its_own_critic_in_the_yaml() -> None:
    workflow = load_builtin("task")
    assert workflow.phase("validate_spec").role == "spec_critic"
    assert workflow.phase("validate_plan").role == "plan_critic"


@pytest.mark.parametrize("phase_name", ["validate_spec", "validate_plan"])
def test_each_critic_brief_names_only_its_phases_inputs(phase_name: str) -> None:
    """A brief that points at `## card` in a phase that never renders a card
    section sends the agent looking for text that is not there."""
    phase = load_builtin("task").phase(phase_name)
    assert isinstance(phase, AgentPhase)
    named = set(re.findall(r"`## (\w+)`", load_role(phase.role).system))
    assert named, "the brief names none of its input sections"
    assert named <= set(phase.inputs), named - set(phase.inputs)
```

- [ ] **Step 3: Make `SHIPPED` the final eight names**

In `tests/roles/test_loader.py`, replace the Task 1 block with:

```python
SHIPPED = [
    "coder",
    "explorer",
    "plan_critic",
    "planner",
    "resolver",
    "reviewer",
    "spec_author",
    "spec_critic",
]

DEFAULT_MODELS = {
    "explorer": "sonnet",
    "spec_author": "opus",
    "planner": "opus",
    "spec_critic": "sonnet",
    "plan_critic": "sonnet",
    "coder": "sonnet",
    "reviewer": "opus",
    "resolver": "opus",
}
```

and replace `test_list_roles_returns_exactly_the_nine_shipped_roles` with:

```python
def test_list_roles_returns_exactly_the_eight_shipped_roles():
    assert len(SHIPPED) == 8
    assert loader.list_roles() == SHIPPED
```

In the module docstring (lines 9-11), change:

```python
Synthetic bundles are built in `tmp_path` for every error path; the seven shipped
bundles are exercised through the same public API, because §8 makes the bundles
themselves part of the contract.
```

to:

```python
Synthetic bundles are built in `tmp_path` for every error path; the eight shipped
bundles are exercised through the same public API, because §8 makes the bundles
themselves part of the contract.
```

- [ ] **Step 4: Run the new tests and watch them fail**

Run: `uv run pytest tests/roles/test_critic_briefs.py::test_the_generic_critic_is_gone tests/roles/test_loader.py::test_list_roles_returns_exactly_the_eight_shipped_roles tests/workflow/test_declared.py::test_each_validation_phase_names_its_own_critic tests/workflow/test_builtin_task.py -k "critic or eight_shipped" -v`
Expected: FAIL. `test_the_generic_critic_is_gone` fails with `DID NOT RAISE`; the eight-roles test fails because `list_roles()` still includes `critic`; both `names_its_own_critic` tests fail with `'critic' == 'spec_critic'`; `test_each_critic_brief_names_only_its_phases_inputs` fails for both phases because the old `critic` brief names no `## ` section (`the brief names none of its input sections`).

- [ ] **Step 5: Rename the roles in `builtin/task.yaml`**

In `src/agent_manager/workflow/builtin/task.yaml`, line 38 (under `- name: validate_spec`) change:

```yaml
    role: critic
```

to:

```yaml
    role: spec_critic
```

and line 52 (under `- name: validate_plan`) change:

```yaml
    role: critic
```

to:

```yaml
    role: plan_critic
```

- [ ] **Step 6: Rename the roles in `workflow/task.py`**

In `src/agent_manager/workflow/task.py`, the `validate_spec` phase (lines 63-70) becomes:

```python
    AgentPhase(
        "validate_spec",
        role="spec_critic",
        inputs=("card", "spec_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
    ),
```

and the `validate_plan` phase (lines 79-86) becomes:

```python
    AgentPhase(
        "validate_plan",
        role="plan_critic",
        inputs=("spec_path", "plan_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
    ),
```

- [ ] **Step 7: Delete the generic critic bundle**

```bash
git rm -r src/agent_manager/roles/bundles/critic
```

- [ ] **Step 8: Run the targeted tests and watch them pass**

Run: `uv run pytest tests/roles tests/workflow -v`
Expected: PASS, including `tests/workflow/test_declared.py::test_task_equals_the_shipped_yaml` (both declarations changed identically) and `test_both_validate` (both roles now resolve).

- [ ] **Step 9: Rename the role in `tests/test_prompt.py`**

Replace `test_two_roles_produce_different_briefs_from_the_same_rendered_prompt` (lines 667-677) with:

```python
def test_two_roles_produce_different_briefs_from_the_same_rendered_prompt():
    rendered = _rendered()
    coder = _role(methodology={"test-driven-development.md": TDD_BODY})
    critic = _role(name="spec_critic", system="# Spec critic\n\nYou adversarially review.\n")

    coder_brief = prompt.compose_brief(coder, rendered)
    critic_brief = prompt.compose_brief(critic, rendered)

    assert coder_brief != critic_brief
    assert "# Coder" in coder_brief and "# Spec critic" not in coder_brief
    assert "# Spec critic" in critic_brief and "# Coder" not in critic_brief
```

- [ ] **Step 10: Rename the role in the fake-claude fixtures**

In `tests/e2e/test_fake_claude.py` (only the role name changes; the canned critic payload and `CRITIC_SCHEMA` stay as they are, since `fake_claude.py:538` dispatches on phase and the result shape is unchanged):

Line 276, inside `test_the_fake_writes_a_gate_passing_critic_result_where_the_brief_says`, change:

```python
        "critic",
```

to:

```python
        "spec_critic",
```

Line 296, inside `test_the_fake_logs_its_phase_and_cwd_beside_the_run_directory`, change:

```python
        tmp_path, "validate_spec", "critic", "\n## spec_path\nx.md\n",
```

to:

```python
        tmp_path, "validate_spec", "spec_critic", "\n## spec_path\nx.md\n",
```

Line 317, inside `test_a_brief_without_a_result_contract_makes_the_fake_exit_non_zero`, change:

```python
        "# Critic\n\n# phase: validate_spec\n# role: critic\n\n## spec_path\nx.md\n",
```

to:

```python
        "# Spec critic\n\n# phase: validate_spec\n# role: spec_critic\n\n## spec_path\nx.md\n",
```

Line 334, inside `test_a_feedback_block_after_the_contract_does_not_hide_the_contract`, change:

```python
        tmp_path, "validate_spec", "critic", "\n## spec_path\nx.md\n",
```

to:

```python
        tmp_path, "validate_spec", "spec_critic", "\n## spec_path\nx.md\n",
```

- [ ] **Step 11: Confirm no reference to the old role is left**

Run: `grep -rn '"critic"\|role: critic' src tests`
Expected: no output (exit status 1). If anything prints, rename that hit to `spec_critic` for a `validate_spec` context or `plan_critic` for a `validate_plan` context, then re-run until empty. `critic_blockers_gate`, `CriticResult` and the `results.py` docstring do not match this pattern and stay unchanged.

- [ ] **Step 12: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, including `tests/e2e`. If anything fails because a critic lacks `Edit`/`Write`, do not add tools: stop and raise it for a human decision (spec, "Observable behavior and error paths").

- [ ] **Step 13: Commit**

```bash
git add -A src/agent_manager/workflow/builtin/task.yaml src/agent_manager/workflow/task.py src/agent_manager/roles/bundles tests/roles/test_critic_briefs.py tests/roles/test_loader.py tests/workflow/test_declared.py tests/workflow/test_builtin_task.py tests/test_prompt.py tests/e2e/test_fake_claude.py
git commit -m "feat(roles): split the critic into spec_critic and plan_critic"
```

(Add whatever commit trailers your own brief requires.)

---

## Notes for the implementer

- Open point (from the spec, deliberately unresolved): both briefs instruct "Fold every CONFIRMED fix directly into ... (edit it)", but the copied policy grants only `Read`/`Grep`/`Glob`, and `plan_critic`'s verbatim "run commands if needed" has no `Bash` either. This card copies the policy literally. Report the gap in your result for a human decision; do not widen `allowed_tools`.
- The phrase "the exploration findings cite them" / "the exploration cites them" is kept verbatim from task.js even though the validate phases do not receive `explore` as an input; the spec requires a verbatim port, and the spec file those critics read cites the same docs.
