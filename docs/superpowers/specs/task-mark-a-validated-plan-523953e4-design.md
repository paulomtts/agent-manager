# Mark a validated plan deterministically (card 523953e4)

Parent story: 695b0514 "Own the commit trail: validated marker, docs commit and the Plan-Hash trailer" (milestone 7aa00a90).

## Why

Today nothing on the pipeline writes `VALIDATED_MARKER` into a plan. `plan_check.find_validated_plan` can only ever answer `validated: false`, so the `plan_check` → `skip_to: implement` shortcut (design §5, lines 169-173) is dead code on a re-run, and the Plan-Hash trailer the siblings add would be computed over a file that does not record that Validate signed it. This card adds the one deterministic step that stamps the marker, and wires it into the builtin document between `validate_plan` and `implement`. It is the first of three: sibling ba15da20 commits the spec and plan with the `Plan-Hash:` trailer, sibling f26b377d feeds the hash to the coder.

## Scope

In scope: one new function `plan_check.mark_validated`, its registry binding, one new phase in `builtin/task.yaml`, and the tests for both.

Explicitly out of scope, owned by siblings or by other cards: computing or writing the plan hash, the docs commit, `git` of any kind, `prompt.py`, `roles/`, `fake_claude.py`, `tests/e2e/`, milestone orchestration, parallel stories, integrate, non-Claude harnesses, ancestor roll-up, and counting reviews with git. This card commits nothing and runs no subprocess.

## Observable behaviour

### `plan_check.mark_validated`

Lives in `src/agent_manager/steps/plan_check.py`, beside the constant it uses. The module's contract holds unchanged: filesystem only — no git, no `brd`, no subprocess, no model. Its only I/O is the plan file.

Signature: `mark_validated(plan_path: str | Path, worktree: object | None = None) -> dict[str, object]`.

Both parameter names are bound from the engine context by name. `bind_arguments` (engine.py:163) passes only parameters the function declares, taking them from the context overlaid with the phase's `args:`; the phase therefore needs no `args:`. `plan_path` is in the context because `_document_paths` (engine.py:120) expands the `plan` phase's `writes:` template once at subtask start, for every `plan_path` declared as an input by some `AgentPhase` (`implement`, `validate_plan` and `review` all do). `prompt.expand_writes` returns a **repo-relative** POSIX path, so the step roots a relative `plan_path` at `worktree` — the reserved context key `subtask_context` always supplies — exactly as `verify.run_suite(commands, worktree, ...)` does. An absolute `plan_path` is used as given and `worktree` is then irrelevant; `worktree` defaults to `None` so unit tests can pass a real `tmp_path` file directly.

Behaviour:

- Reads the plan as UTF-8 and writes it back as UTF-8, explicitly, matching `read_file` (plan_check.py:110). No `encoding=None` platform default anywhere.
- If `VALIDATED_MARKER` already appears in the text, the file is left **byte-identical** — not rewritten, not re-terminated, not re-marked. Containment is the same literal-substring test `find_validated_plan` uses, never a regex; the module docstring already accepts that a plan whose prose discusses the marker reads as validated.
- Otherwise it appends the marker as its own final line: if the existing text is non-empty and does not end with `\n`, one `\n` is added first, then `VALIDATED_MARKER` and a trailing `\n`. An empty plan becomes exactly the marker line.
- The literal `<!-- task-pipeline: validated -->` is never re-typed. The existing `VALIDATED_MARKER` constant (plan_check.py:21) is the single source, so the writer and `find_validated_plan`'s reader cannot drift.
- Returns a plain dict (`CLAUDE.md`: Pydantic only at process boundaries; deterministic steps return plain dicts per design §6) of the shape `{"path": <path used, str>, "appended": <bool>}`. It **must** be a Mapping or `_run_deterministic` fails the phase (engine.py:494). The engine folds it into the context under the phase name `mark_validated`.

### The `mark_validated` phase

`src/agent_manager/workflow/builtin/task.yaml` gains, immediately after `validate_plan` and immediately before `implement`:

```yaml
  - name: mark_validated
    kind: deterministic
    run: plan_check.mark_validated
```

No `args:`, no `gates:`, no `best_effort:`. It is not best-effort on purpose: an unmarked plan makes the next run re-plan and makes the sibling's Plan-Hash trailer meaningless, so a failure here must escalate like any other deterministic phase.

It must not run when `validate_plan` blocked, and placement alone achieves that. `validate_plan` is an agent phase gated by `critic_blockers_gate`; a non-retryable gate failure raises `AgentPhaseFailed`, and the walk in `run_subtask` returns `_escalate(...)` before ever reaching the next index (engine.py:396-402). No extra guard is added here, and none should be.

### Registry

`src/agent_manager/workflow/registry.py`: `registry.register("plan_check.mark_validated", plan_check.mark_validated)` goes in the "Deterministic steps that already ship on this branch" block of `default_registry()` — the real imported callable, not a placeholder, so `resolve(name) is the_function`. The name is added to `BUILTIN_FUNCTION_NAMES` in sorted position, between `plan_check.has_validated_plan` and `plan_hash_gate`.

## Error paths

- Plan file missing, or unreadable: the `OSError` propagates. `_run_deterministic` is deliberately total (engine.py:509), records the phase `failed` with the rendered error and escalates the subtask. The step adds no `try` of its own and invents no "nothing to mark" success — a silent pass would hand the sibling a hash over a file that was never marked.
- Plan not valid UTF-8: `UnicodeDecodeError` propagates the same way. (`find_validated_plan` catches it because *its* answer is "re-plan"; here there is no benign answer.)
- `plan_path` relative and `worktree` absent or empty: raise `ValueError` naming both, in the style of `_plans_dir` (plan_check.py:92) and `verify._required_worktree`, rather than writing a marker into a file resolved against whatever the process CWD happens to be.
- A `plan_path` the context does not carry (a workflow document whose agent phases never declare `plan_path`): `bind_arguments` already raises `EngineError` naming the phase, the function and the parameter. Nothing new is needed.

## Test list

Tiering follows `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 (line 477): Steps are tested against temporary directories/files with no network; pure functions and workflow documents are unit-tested; the Engine tier uses a fake adapter; end-to-end is one slow opt-in test. No test here belongs to the engine or e2e tier — `tests/e2e/` does not exist on this branch and arrives with sibling ba15da20 — and the whole default suite (`uv run pytest`) must stay green.

**Steps tier — `tests/steps/test_plan_check.py`, real files under `tmp_path`, no fake filesystem** (the module docstring already states this placement):

1. Marking a plan with no marker appends it once: the text ends with `VALIDATED_MARKER + "\n"`, the marker occurs exactly once, the original body is still a prefix, and the result is `{"path": ..., "appended": True}`.
2. A second call on the same file leaves the bytes unchanged (compare `read_bytes()` before and after) and returns `"appended": False`; the marker still occurs exactly once.
3. A plan with no trailing newline gains one before the marker — the marker is on its own line, and the last body character is not glued to `<!--`.
4. After marking, `find_validated_plan` for the same card reports `found: True` and `validated: True` (plan file named so `matches_card` accepts it, listed from a real `tmp_path` plans directory).
5. A relative `plan_path` is rooted at `worktree`: the file inside `tmp_path/worktree/docs/superpowers/plans/...` is the one that gets the marker.
6. A relative `plan_path` with no `worktree` raises `ValueError` and writes nothing.
7. A missing plan file raises `OSError`/`FileNotFoundError` (the step does not swallow it).

**Pure/document tier — `tests/workflow/test_builtin_task.py`:**

8. `EXPECTED_PHASES` gains `("mark_validated", "deterministic")` between `validate_plan` and `implement`, giving **13** entries; `test_builtin_task_has_the_twelve_phases_in_spec_order` is renamed to say thirteen.
9. A new document test: the phase's `run` is `plan_check.mark_validated` with no `args`, and its index is greater than `validate_plan`'s and less than `implement`'s.
10. `test_every_resolved_function_is_the_registry_binding` must keep passing unchanged — it asserts the document's names and `default_registry()` names match exactly, so it is the drift guard for the registry binding and the YAML edit together.

**Registry tier — `tests/workflow/test_registry.py`:** its existing assertions over `BUILTIN_FUNCTION_NAMES` and `default_registry().names()` cover the new entry; add `"plan_check.mark_validated"` to the pinned `TASK_YAML_NAMES` tuple (between `plan_check.has_validated_plan` and `plan_hash_gate`, sorted), and add an assertion to `test_default_registry_resolves_implemented_steps_to_the_real_callables` that `registry.resolve("plan_check.mark_validated") is plan_check.mark_validated`. Engine references above (`engine.py`) mean `src/agent_manager/engine.py`.

## Verification

`uv run pytest` (full suite). There is no lint or typecheck command.
