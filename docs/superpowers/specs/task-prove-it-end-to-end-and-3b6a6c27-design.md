# Prove exactly-once end to end and document the guarantee (card 3b6a6c27)

Milestone 11 "exactly-once phases" (4e0d2a6c) → story 5716184f "Proof and documentation" → this subtask (the story's only child). Branch prefix `m11`. This subtask adds no mechanism. It proves the mechanism that sibling stories 5bb73149 (checkpoint floors) and fd673816 (adoption) already built, and it writes down the guarantee.

## Preconditions and gaps

- **Prerequisite code is not on master.** `checkpoint_floors`, `Store.replay_journal`, `AgentRunner.adopt`, `dispatch.read_result`, and the `compile.py` adoption wiring live only on branches `m11/task-save-a-floor-atomically-e600b86a`, `m11/task-compute-the-floor-at-94088f7e`, `m11/task-let-the-agent-runner-4aed8212`, and `m11/task-adopt-the-resumed-head-6ecbe6e2`. There is no `m11-integrate` branch yet. The e2e test below can only pass once those branches are merged underneath it. Do not reimplement any of that mechanism here. If it is missing at implementation time, say so in the result rather than re-creating it.
- **Cited design docs are missing.** `2026-09-27-exactly-once-design.md` (along with `-live-control-` and `-multi-process-`) and the plan `2026-09-27-exactly-once.md` exist nowhere in `docs/superpowers/`. The decision labels E1, E2, and E9 can only be taken from the card text. The E1 table content below is therefore narrowed from the card and its sibling stories, not quoted from a file.
- **Exploration findings were truncated.** The upstream summary was cut at 8000 characters, in the middle of the e2e-helper references. The test-placement rule used below comes from `pyproject.toml` (`addopts = -m "not e2e"`; the `e2e` marker is reserved for the paid real-`claude` test) and from the sibling `tests/e2e/test_milestone_resume.py` docstring ("Default-suite e2e tier … Unmarked on purpose"). It was not taken from the missing text.
- **Code references must be checked against the built code.** References come from master plus the M9/M10 plans. Read the code as built, adapt to it, and state any divergence in the result.

## Scope (four files plus one new test)

1. **`tests/e2e/test_exactly_once.py` (new)**
   - Drives `am run --card <id>` and then `am resume <run-id>` through `CliRunner().invoke(cli.app, ...)` against the fake `claude` on `PATH`: the existing `fake_claude_bin` fixture and a fresh project or board fixture from `tests/e2e/conftest.py`.
   - Kill: monkeypatch `runtime.bridge.call_agent` with a wrapper that has the real signature `(runner, phase, context, rendered)`. The wrapper awaits the real function. When `phase` is `implement`, it raises `class _Killed(BaseException)` after the real call returns. That point is after the dispatch completed and its attempt was journalled `ok`, but before the next turn's checkpoint recorded completion. This follows the `_Crash(BaseException)` convention in `tests/runtime/test_resume.py:42`. No sleeps.
   - The wrapper is removed (or disarmed) before `am resume`.
   - The basename clashes with the sibling's `tests/runtime/test_exactly_once.py`. `--import-mode=importlib` already tolerates that, as `pyproject.toml` notes for `test_integrate.py`.
2. **`README.md`**: add a `## Resuming: what runs again` section next to the existing resume/usage material (before `## Develop`). It contains:
   - The E1 per-phase-kind table:
     - Agent phase whose `ok` attempt was recorded: adopted, not dispatched again. The result file is re-validated and the gates re-run.
     - Agent phase with no `ok` attempt, or only a started or orphaned attempt: dispatched again.
     - Step: may run again (at-least-once) and must be idempotent.
   - A note that the harness's own effects (for example commits or files an agent wrote before a kill) are never transactional. Exactly-once covers am's dispatch of a phase, not what the harness did.
   - A statement that adoption surfaces only as one warnings line, and that the envelope and exit codes are unchanged.
3. **`src/agent_manager/workflow/phases.py`**: give `class Step` (lines 40–48) the docstring: "A step may run more than once for one walk (a resume re-runs a step whose completion was not checkpointed); it must be idempotent." No behavior change.
4. **`docs/superpowers/specs/2026-09-25-pygents-engine-design.md`**:
   - Append to §6 "Known limit, accepted" (around lines 302–305): "Closed for agent phases by milestone 11 (`2026-09-27-exactly-once-design.md`); steps stay at-least-once by contract."
   - In §11 "Deferred", update the bullet "Exactly-once phases (checkpoint on the next turn's `put`)" (around line 422). Mark it delivered for agent phases by milestone 11, mechanism: the floor saved with each checkpoint plus adoption, not an AFTER_PUT checkpoint. Note that steps stay at-least-once. Do not leave it reading as open.
5. **Board**: close nothing by hand.

## Observable behavior the test pins

- The first invoke dies with `_Killed`. The run is left resumable: not `done`, and an open checkpoint exists.
- `am resume <run-id>` exits 0 with envelope `{"ok": true, ...}` and the run status is `done`.
- The fake log (`read_fake_log` or the fake-claude log) shows `implement` dispatched **exactly once** across both invocations. Every agent phase after `implement` (`review`) is dispatched once.
- The worktree branch holds **exactly one** commit with subject `IMPLEMENT_SUBJECT` (`"feat: implement this card"`, from `fake_claude.build_result`).
- The resume envelope's warnings include exactly one line containing `"not dispatched again"` that names `implement`. There are no other new report fields.

## Error paths

- If the resume dispatches `implement` again, the test fails on the dispatch count and on the commit count. That is the regression this test exists to catch.
- If the adopted result fails re-validation or its gates, the resume must re-dispatch, per the sibling stories. This test does not exercise that path; it is covered by the sibling unit and runtime tests.
- The test must fail only when something is miswired. It must not depend on timing.

## Tests

| Test | Tier | Rationale |
|---|---|---|
| `tests/e2e/test_exactly_once.py::test_resume_after_implement_returns_adopts_it` (kill after `implement`'s `call_agent` returns, resume, then assert `done`, one implement dispatch, one implement commit, one "not dispatched again" warning) | Default-suite e2e tier: `tests/e2e/`, **unmarked** (no `@pytest.mark.e2e`) | It uses the fake `claude` through the real CLI, `ClaudeAdapter`, and launcher, like `test_milestone_resume.py`. The `e2e` marker is reserved for the opt-in paid real-`claude` run that `addopts` excludes. |

The README, the `Step` docstring, and the engine-spec edits get no new tests. `uv run pytest` must stay green.

## Out of scope

- Adopting started or orphaned attempts.
- Persisting step results.
- AFTER_PUT or AFTER_TURN checkpoints.
- New report fields.
- Any schema, store, dispatch, or compile change.
- Writing the missing `2026-09-27-*` design docs.
