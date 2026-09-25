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
