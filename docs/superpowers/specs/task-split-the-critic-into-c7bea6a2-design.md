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
