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
