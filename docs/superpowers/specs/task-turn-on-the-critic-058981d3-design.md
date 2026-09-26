# Turn on the critic loops in TASK (card 058981d3)

Narrows pygents-engine design G4 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:86-89`) and plan Task 5.1 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1322-1330`) to one subtask. Parent: 1ec08da2 "Switch over". This card has no `blocked_by`; a300ab2b (real harness), 7a744199 (pygents-only engine) and e46098be (docs) come after it and are out of scope.

## Scope

Files touched, and only these:

- `src/agent_manager/workflow/task.py`
- `tests/workflow/test_declared.py`
- `tests/e2e/fake_claude.py`
- `tests/e2e/test_production_wiring.py`

Changes to `TASK` in `workflow/task.py`:

- `validate_spec` gets `on_fail=Goto("spec")`. `validate_plan` gets `on_fail=Goto("plan")`. Both use the default `max_loops=1`. `Goto` is imported from `agent_manager.workflow.phases`.
- `"feedback"` is added to `spec`'s `inputs`, which become `("card", "explore", "spec_path", "feedback")`, and to `plan`'s `inputs`, which become `("spec_path", "plan_path", "feedback")`.
- `review` gets no `on_fail`. Review does not loop. No other phase changes. Timeouts, gates and retry stay as they are.
- `task.py` still imports nothing from pygents (rule 1). Its module docstring should say that `on_fail` and `feedback`, like timeouts, are data the YAML never had.

These are declaration changes only. The following are already implemented in the base state and stay unchanged:

- the loop-back and escalate mechanics in `runtime/compile.py` `agent_phase`
- `Goto` and the `Workflow.validate()` check that a `Goto` names an earlier phase
- the `_feedback` resolver in `prompt.py`
- `runtime/context.py`'s feedback table
- `reducers.critic_blockers_gate`

Nothing in `runtime/`, `engine.py`, `workflow/loader.py`, `workflow/registry.py`, `workflow/builtin/task.yaml` or the README changes. Base the branch on the merged m6 stories 1-4 state (for example `.claude/worktrees/m6/task-resume-and-relaunch-02890d5d/`), not on `master`, which does not have `workflow/task.py`.

## Observable behaviour

Under `--engine pygents`:

- **Critic blocks once:** if `validate_spec` blocks once, the run loops back to `spec`. `spec` is dispatched a second time, and its brief has a `feedback` section that contains the critic's reason. Then `validate_spec` passes and the run finishes `done`. `validate_plan` loops back to `plan` in the same way.
- **Critic blocks twice:** if `validate_spec` blocks on two consecutive attempts, the loop budget (`max_loops=1`) is used up. The run escalates `validation` the way it does today: `am run` exits 1 and `failed_phase == "validate_spec"`.
- **No block:** when no critic blocks, the `feedback` input is empty. `_feedback` then returns no section, so the spec and plan briefs are byte-identical to today's. The existing golden briefs and the engine-parity baseline must not change.

Under `--engine yaml`, a critic block still escalates on the first block. That is existing, documented behaviour, and this card asserts it without changing it.

Rule 5 / G10: `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads keep their current shape. A loop adds rows the way a normal re-dispatch does. It adds no new fields.

## Error paths

- **Second critic failure:** escalates at the critic phase, with the critic's detail. It does not escalate at the looped-to phase.
- **Invalid workflow:** if the change breaks `TASK.validate()` (a `Goto` to a later or unknown phase, `max_loops < 1`, a timeout not above `LAUNCHER_TIMEOUT` (G2), or `feedback` rejected as an input name), `test_both_validate` must fail.
- **Fake misconfiguration:** a misconfigured "blocks" marker or env value in the fake raises `FakeClaudeError`, the same way the rendezvous count validation does. It never silently passes.

## Test scaffolding: fake_claude "critic blocks" mode

Today `build_result()` always returns `blockers=False` for `validate_spec` and `validate_plan` (`tests/e2e/fake_claude.py:538-539`). Add a trigger that the test controls from outside the brief, following the `REVIEW_FAIL_MARKER` and rendezvous scaffolding. The trigger is either a marker file in the git common dir or a `FAKE_CLAUDE_*` env var. It must say which critic phase blocks and how many times (1 or 2).

- When the trigger is active for a phase, the fake returns `blockers=True` with a fixed, module-level reason constant, so a test can assert that the reason shows up in the next `spec` or `plan` brief. It does this until it has blocked the requested number of times, then it goes back to `blockers=False`.
- The fake may keep its block count in the git common dir or in a test-supplied directory, the way rendezvous leaves markers.
- Rule 4: the trigger and the count must never come from the brief's own content.
- With no trigger, behaviour is exactly today's.

## Tests

The placement rule is pygents-engine design §9 (lines 379-401):

- Tests mirror source modules one-for-one.
- Pure declared-data checks go in `tests/workflow/`.
- Wiring and critic-loop scenarios go in the existing engine-parametrized `tests/e2e/test_production_wiring.py` tier. Do not create a new tier.
- No test calls a model.

1. **`test_task_equals_the_shipped_yaml`** (existing, updated). Tier: `tests/workflow/test_declared.py`, pure-data unit tier. Change `_shipped()` so it takes `on_fail` from `like.phase(p.name)` (the same pattern as `timeout`), and adds `"feedback"` to `inputs` wherever the declared phase has it. The declared `TASK` digest must still equal the shipped YAML plus only those additions. `INTEGRATE`'s comparison must still pass unchanged.
2. **`test_task_critics_loop_back_once`** (new). Tier: `tests/workflow/test_declared.py`, pure-data unit tier. Asserts:
   - `TASK.phase("validate_spec").on_fail == Goto("spec", 1)`
   - `TASK.phase("validate_plan").on_fail == Goto("plan", 1)`
   - `TASK.phase("review").on_fail is None`
   - every other AgentPhase has `on_fail is None`
   - `"feedback"` is in the `spec` and `plan` inputs and in no other phase's inputs
3. **`test_both_validate`** and **`test_declared_modules_never_import_pygents`** (existing, unchanged). Tier: `tests/workflow/test_declared.py`. They must stay green.
4. **Critic blocks once, run finishes done** (new, pygents). Tier: `tests/e2e/test_production_wiring.py`, pygents engine. Set the fake's trigger so `validate_spec` blocks once. Assert that:
   - the run ends `done` and the board card is `done`
   - `spec` was dispatched twice
   - the second `spec` brief's `feedback` section contains the fake's reason constant
5. **Critic blocks twice, run escalates** (new, pygents). Tier: `tests/e2e/test_production_wiring.py`, pygents engine. Set the trigger so `validate_spec` blocks twice. Assert that `am run --card` exits 1, `failed_phase == "validate_spec"`, and the escalation kind is `validation`.
6. **Critic blocks once under yaml, run escalates immediately** (new, yaml). Tier: `tests/e2e/test_production_wiring.py`, yaml engine. Set the trigger so `validate_spec` blocks once. Assert exit 1 and `failed_phase == "validate_spec"` on the first block, with `spec` dispatched only once. This is the documented yaml behaviour.
7. **Existing parametrized wiring and parity tests** (existing, unchanged). Tier: `tests/e2e/test_production_wiring.py`, both engines. With no trigger set, `test_the_run_data_is_the_same_on_both_engines` and the golden briefs stay green. This shows that an empty `feedback` adds nothing to the briefs.
8. **Fake mode self-check** (new, optional, only if the fake's other modes already have self-tests). Tier: beside the existing `fake_claude` self-tests in `tests/e2e/`. It checks that a malformed trigger raises `FakeClaudeError`.

The critic-loop scenarios in tests 4-6 each run on their own repo and board. They must not change the shared `completed_run` fixture that the parity and golden tests read.

## Verification

`uv run pytest` (full suite). There is no typecheck or lint command.
