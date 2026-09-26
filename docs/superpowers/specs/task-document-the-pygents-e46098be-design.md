# Subtask e46098be: Document the pygents engine

Card `e46098be-302a-4d4b-811b-3abbceaebb66`, under story 1ec08da2 "Switch over". This is Task 5.4 of `docs/superpowers/plans/2026-09-25-pygents-engine.md` (Interfaces block, lines 1353-1364). The milestone's source of truth is `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (the "pygents addendum"). This card only documents. It follows the doc-only precedent of card 4c78e3ea ("Document Integrate").

## Scope

Files that may change: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. No other file changes. That means no source, test, plan or addendum file.

Write from the code as built, never from a spec. Before writing, read `src/agent_manager/runtime/engine.py`, `src/agent_manager/runtime/checkpoint.py`, `src/agent_manager/workflow/task.py`, and `cli.py`'s `resume_run`, `select_resumable`, `checkpoint_resume_phase` and `orphan_attempts`. Every sentence added must be true of that code.

Out of scope:
- Sibling cards own the following, and this card must not touch them: wiring the critic loops (058981d3), the real-harness test (a300ab2b), and the move into `runtime/` or removal of `--engine` (7a744199).
- The addendum's §11 deferred items: the supervisor tree/orchestrator milestone, exactly-once phases, benchmarking rewritten prompts, and upstream pygents fixes.

## Observable changes

### 1. README, "Relaunching resumes" section (`README.md` around lines 260-270)

The current `am resume` paragraph says a run with a stopped subtask "is refused with exit code 3". That is no longer true, so rewrite the paragraph to state the following. The same stale claim also appears earlier, in the "Parallel runs" section (`README.md` around lines 205-210, in the paragraph starting "To continue, fix the escalation and relaunch..."): fix that occurrence the same way, so the README does not contradict itself in one place while it is corrected in another.
- `am resume <run-id>` continues a stopped (parked) or killed subtask from its newest checkpoint. It no longer refuses stopped subtasks.
- It resumes at the interrupted phase. Nothing before that phase re-runs. Attempts left recorded `started` with no terminal event are marked `harness_error`.
- If the workflow changed since the checkpoint was saved (digest mismatch), resume refuses before anything runs and exits 3 with an error envelope.
- A stopped walk exits 0, and an escalated one exits 1.
- It drives exactly one subtask. When more than one subtask is in flight (a milestone-shaped run), it is still refused with exit 3, and relaunching `am run --milestone` stays the way to continue a milestone.

Optional, only if it is stated accurately: the other refusals in `checkpoint_resume_phase`. These are: no checkpoint, a newest checkpoint of `done`, and a checkpoint left by a phase escalation.

### 2. README, "What an escalation report contains" section

Add one sentence: when a critic (`validate_spec` / `validate_plan`) returns blockers, the subtask gets one revision of the spec or plan with that feedback. A second blocker escalates at that critic's phase. `review` has no revision loop. The escalation payload shape is unchanged.

### 3. README, "Not there yet" section

Keep the bullet "`am resume` is not milestone-aware." `select_resumable` still refuses more than one in-flight subtask. Leave the other bullets and the deferred-links paragraph alone.

### 4. Main design doc pointer (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`)

Add one short paragraph to §6 "The phase contract", to §9 "State, durability and resume", or to both (the same paragraph repeated or cross-referenced). It says the following:
- These sections describe the original yaml-engine phase and resume model.
- The pygents engine replaced that model.
- The pygents addendum is authoritative for phases (§5 "Phase model and compiler") and for checkpoints and resume (§6 "Checkpoints and resume").

The paragraph should link `2026-09-25-pygents-engine-design.md` with section anchors. Nothing else in the main doc changes.

### Facts to state accurately, if mentioned

- Checkpoints are `Agent.to_dict()` snapshots, written to the store's `checkpoints` table at `BEFORE_TURN`.
- Reasons: a normal turn writes `"turn"`. When the cooperative stop is set, the turn writes `"parked"` and raises `Parked`. After a run finishes, `runtime/engine.py` writes `"done"` or `"escalated"`.
- Nothing is ever checkpointed at `AFTER_TURN`.
- `agent.run()` is always consumed to the end.
- Hooks are module-level, never closures.
- SubtaskSummary, journal lines, phase/attempt rows and escalation payloads are unchanged (G10).

Style: no hard-wrapped prose in new paragraphs. Match the README's existing voice.

## Error paths

None at runtime, because this card changes no code. The documentation risk is a claim the code contradicts. Treat each of these as a defect:
- saying `am resume` is milestone-aware
- saying the mismatch exits anything other than 3
- saying critics loop more than once
- saying `review` loops
- saying earlier phases re-run on resume

## Tests

None are added. Under the test-placement rule (main design spec §14, refined by pygents addendum §9), tiers exist for pure functions, steps, adapters, the engine, and one opt-in e2e test. Prose has no tier, and precedent card 4c78e3ea added no test files.

Verification: `uv run pytest` (the whole default suite, including `tests/e2e`) stays green and unchanged. It is run only to confirm that no code file was touched.

Commit message: `docs: document the pygents engine, checkpoints and critic loops`.
