# ed77a917 — Run deterministic phases in the engine

Date: 2026-09-23
Status: spec, pre-plan
Card: ed77a917 (subtask of story 2143808b, milestone 352e955b)
Source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§6 phase contract, §9 write ordering and resume, §12 escalation and best-effort, §14 testing)

## Scope

This subtask adds `src/agent_manager/engine.py`: the walk over one subtask's phases, and the execution of the `kind: deterministic` ones. It is the first code in the engine module; everything it needs already exists in this worktree (`workflow/loader.py`, `workflow/registry.py`, `workflow/builtin/task.yaml`, `models.py`, `store.py`, `paths.py`, `board.py`, `dag.py`, `steps/{worktree,verify,plan_check,reducers}.py` from dependency 2399fdcc and its predecessors), so no integration work is part of this card.

In scope:

- Walking `Workflow.phases` in document order for one subtask.
- Executing a `DeterministicPhase` by calling the callable the loader already bound in `Workflow.functions[phase.run]`, with arguments reconciled against the phase's declared `args` and the subtask's context.
- Recording every state edge through `Store.record_phase`, which already performs the §9 journal-before-DB ordering.
- Evaluating the phase's `gates` over the phase result; a failing gate escalates the subtask and stops the walk.
- Honouring `when` / `skip_to` (the `plan_check` short-circuit to `implement`).
- Honouring `best_effort`: a failed board write is recorded and surfaced in the returned summary, and never sinks an otherwise-sound subtask.

Explicitly not in scope, owned by siblings: input resolution and prompt rendering (968fba15), and agent-phase dispatch, schema validation, retry and escalation (bf8e415b). When the walk reaches a `kind: agent` phase it calls an injected callable — the seam bf8e415b fills — and does nothing else with it. Journal reading, replay and `resume <run-id>` reconstruction are also out of scope; the walk only accepts an optional starting phase name so a later resume subtask can re-enter it.

## The deterministic-phase contract, reconciled

§6 states the engine calls `run(ctx) -> dict`. The real step functions take named keyword arguments (`worktree.ensure(branch, base, worktree, repo_dir, git_runner=...)`, `verify.run_suite(commands, worktree, *, runner=...)`, `plan_check.find_validated_plan(card, plans_dir=None, *, repo_dir=None, ...)`), not a single `ctx`. The engine reconciles the two rather than rewriting the steps: it holds a context mapping for the subtask, overlays the phase's declared `args` from the document, and binds by parameter name against the callable's signature. Only parameters the callable actually declares are passed; a parameter that is required and cannot be supplied from `args` or the context is an engine error raised before the call, naming the phase, the function and the missing parameter — never a bare `TypeError` from the call site.

The context mapping's keys are the callables' exact parameter names, not the model's field names, because binding is by name and none of the builtin document's deterministic phases declares `args` to bridge a difference: `card`, `branch`, `repo_dir`, and each completed phase's result under the phase's name, plus two required renames off `SubtaskRun` — `base` (the model's `base_branch`) for `worktree.ensure`'s `base` parameter, and `worktree` (the model's `worktree_path`) for `worktree.ensure`'s and `verify.run_suite`'s `worktree` parameter — and `commands` (the run's configured verification commands) for `verify.run_suite`'s `commands` parameter. Without these renames the builtin `worktree` phase, which carries no `args`, would fail to bind `base` and `worktree` and the engine would raise its own "missing required parameter" error on the first deterministic phase of every run.

The walk's entry point also needs `story_id`, alongside the `SubtaskRun` (or its card id, branch and base) and the workflow: `Store.record_phase(story_id, card_id, phase)` and `Store.record_subtask(story_id, subtask)` both require it, and nothing else in this subtask's inputs supplies it. `story_id` is passed in by the caller (the run-level scheduler that owns story and subtask sequencing, out of scope here) and is not part of the per-subtask context mapping above, since none of the registered deterministic functions take it as a parameter.

The return value is the phase result. It is recorded and becomes available to later phases and to gates under the phase name, per §6. A deterministic step that returns something other than a mapping is an engine error, for the same reason: a later `when` or gate would read it as closed or empty and the run would take a silently wrong branch.

`DeterministicPhase` has no `retry` field. A deterministic phase therefore has exactly one attempt, and any gate failure on it is terminal.

## Observable behaviour

For each phase, in order:

1. **Agent phase** — delegate to the injected agent-phase runner and take its result as the phase result. If no runner was injected, that is an engine error naming the phase (this card does not stub agent behaviour).
2. **Deterministic phase** — record the phase as `started` (`PhaseRun(name, kind="deterministic", status="started", started_at=...)`) through `Store.record_phase`, bind arguments, call the function, then record the terminal status. No `Attempt` row is written: `models.Attempt` requires a `Dispatch` (harness, model, role, cwd, prompt_path, result_path), none of which exists for a function call, and fabricating one would put fiction in the journal. `PhaseRun` alone carries the whole story of a deterministic phase.
3. **Gates** — every name in `phase.gates` is resolved from `Workflow.functions` and called with arguments bound the same way, with the phase result in scope. A gate returns `None` to pass, `{"warn": ...}` to pass with a warning (recorded and surfaced in the summary, the walk continues), or a verdict dict to fail.
4. **`when` / `skip_to`** — if the phase declares both, the `when` function is called with the phase result; when it returns truthy, the walk jumps to the phase named by `skip_to` (the loader has already proved that target exists and is later in the document). A `when` that returns falsy continues to the next phase. `plan_check` with a validated plan on disk therefore skips `spec`, `validate_spec`, `plan` and `validate_plan` and resumes at `implement`; those skipped phases are not recorded as run.
5. The walk ends when the phases are exhausted, or when an escalation stops it.

The return value is a summary object for one subtask: the terminal subtask status (`done` or `escalated`), the phase results by name, the names of phases skipped by `skip_to`, and the list of warnings — best-effort failures and gate warnings — so a run never reports success while the board silently never moved (§12).

## Error paths

- **Gate failure on a non-best-effort deterministic phase.** The phase is recorded `failed`, the subtask is recorded `escalated` (both through `Store.record_phase` / `Store.record_subtask`, journal first), the walk stops, and the summary carries the gate name and the verdict detail. There is no retry, because `DeterministicPhase` has no `retry` field.
- **The step function raises.** Any exception from the callable — `steps.worktree.GitError`, an `OSError`, anything — is caught, the phase is recorded `failed` with the exception rendered into the journal payload, and the subtask escalates exactly as for a gate failure. The exception does not propagate out of the walk.
- **Best-effort phase fails**, whether by raising or by a failing gate: the phase is recorded `failed`, a warning is appended to the summary, the subtask does *not* escalate, and the walk continues with the next phase. This is the `mark_in_progress` / `mark_done` board write of §12.
- **Argument binding failure** (a required parameter with no value, or an `args` key the callable does not declare): an engine error naming phase, function and parameter, raised before the call; if the phase is best-effort it is treated as a best-effort failure like any other.
- **Non-mapping step result**: engine error, phase `failed`, escalation (or warning if best-effort).
- **A `when` function that raises**: treated as a failure of the phase it belongs to — never as "do not skip", because a silently-not-skipped `plan_check` re-plans work that was already validated.
- **Unknown starting phase name** passed to the walk: an engine error before anything is recorded.

Registry note: `registry.default_registry()` still binds `rollup.set_status`, `critic_blockers_gate` and `verification_passed_gate` to placeholders that raise `NotImplementedError`. Engine tests must construct their own `FunctionRegistry` with canned functions and load a workflow document against it; they must not drive the default registry.

## Tests

Tier per `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 (lines 477-492). The Pure tier there names `dag.py` and `steps/reducers.py`; the Steps tier names work against temp git repos and a temp `brd` board; the Adapters tier names `build_command`. None of those describe loop logic, so every test below is **Engine tier** — driven with canned fakes (here, a fake registry of deterministic functions standing in for §14's fake adapter with canned result files), against a temp store and journal, with no git, no `brd` and no harness. They land in `tests/test_engine.py`, mirroring `src/agent_manager/engine.py` per `CLAUDE.md`.

1. **Phases run in document order** — a workflow of three canned deterministic phases; the call order and the recorded phase order both match the document. *Engine tier.*
2. **A phase result is recorded and reaches a later phase** — phase B's bound argument is phase A's returned dict. *Engine tier.*
3. **Declared `args` are passed and override context** — `args: {status: in_progress}` arrives as the `status` keyword. *Engine tier.*
4. **Only declared parameters are bound** — a step taking two parameters is called with exactly those, from a context holding many more. *Engine tier.*
5. **A missing required parameter is an engine error naming phase, function and parameter**, raised without calling the step. *Engine tier.*
6. **Every state edge is journalled before the row is written** — the journal holds `started` then the terminal status for each phase, in sequence order, and the SQLite projection agrees. *Engine tier.*
7. **A passing gate lets the walk continue**; a gate returning `{"warn": ...}` also continues and the warning is in the summary. *Engine tier.*
8. **A failing gate on a deterministic phase escalates and stops** — phase `failed`, subtask `escalated`, no later phase runs, the verdict detail is in the journal and in the summary. *Engine tier.*
9. **A raising step escalates the same way** and the exception does not propagate. *Engine tier.*
10. **`when` truthy jumps to `skip_to`** — the `plan_check` shape: the intervening phases never run and are absent from the recorded phases, and the walk resumes at the target. *Engine tier.*
11. **`when` falsy continues to the next phase.** *Engine tier.*
12. **A best-effort failure does not sink the subtask** — the board-write phase raises, the phase is `failed`, the walk continues, the subtask ends `done`, and the failure is in the summary's warnings. *Engine tier.*
13. **A best-effort phase whose gate fails behaves identically** — warning, no escalation. *Engine tier.*
14. **The builtin `task.yaml` walks against a fake registry** — loading the shipped document with every name bound to a canned function drives the full twelve-phase order, with the agent phases going to the injected agent runner seam and the deterministic ones executing. *Engine tier.*
15. **Re-entering the walk at a named later phase** runs only from that phase onward; an unknown name is an engine error before anything is recorded. *Engine tier.*

Verification: `uv run pytest`. No separate lint or typecheck command.
