<!-- task-pipeline: validated -->
# Resume and relaunch from checkpoints on the pygents engine (card 02890d5d)

Subtask of a6c7bff3 "Checkpoints and resume" (milestone 84c3b532). Blocked by 7fdec762 (done). Narrows plan Task 4.5 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1297-1316`) and milestone spec §6/§8/§9 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:262-401`).

Note on inputs: the exploration summary this spec was written from was cut off at 8000 characters, partway through its file:line list. The missing part probably held the rest of the references and the test-placement paragraph. The line references and tier assignments below come from reading this worktree's code directly and from design §14 of `2026-09-23-agent-manager-design.md`. They do not come from the missing text.

## Scope

In scope, all in this worktree:

- `src/agent_manager/cli.py`:
  - `resume_run` gets an `engine` parameter, and the Typer `resume` command gets `--engine {yaml,pygents}` (default `yaml`).
  - `select_resumable` accepts a `stopped` subtask on pygents only.
  - `drive_subtask` gains `resume_from`.
- `src/agent_manager/orchestrate.py`:
  - On relaunch under `--engine pygents`, look up each card's open checkpoint before driving it.
  - The `Driver` protocol gains `resume_from`.
- Tests in `tests/test_cli.py`, `tests/test_orchestrate.py` and `tests/e2e/test_milestone_run.py`, plus whatever `tests/e2e/conftest.py`/`fake_claude.py` knob the kill scenario needs.

Consumed, not redefined:

- `Store.latest_checkpoint(card_id)` and `Store.latest_open_checkpoint(card_id, workflow)` (`store.py:896-928`)
- `runtime_engine.run_subtask(..., resume_from=...)` and `CheckpointMismatch` (`runtime/engine.py:33-154`)
- the checkpoint hook (`runtime/checkpoint.py`)

Out of scope:

- `store.py`, `runtime/checkpoint.py` and the resume mechanics in `runtime/engine.py`
- `integration.py` (Integrate's own path)
- the yaml helpers `interrupted_phase`, `resume_start_phase` and `_skipped_origin`
- `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads (G10)

## Rules carried from the story

1. Only `runtime/` imports pygents. `cli.py` and `orchestrate.py` reach it only through `agent_manager.runtime.engine` (`runtime_engine`). A digest comes from `workflow.task.TASK.digest()` (`workflow/phases.py`, which has no pygents import).
2. The whole default suite stays green, `tests/e2e` included, on both engines.
3. Never break or return out of `agent.run()`. Never checkpoint at `AFTER_TURN`. Hooks are module-level only. This card adds no hooks.
4. A fake `claude` knows only what its brief and the test's env knobs tell it. It is never told run or checkpoint state.
5. G10: payload shapes, journal lines and rows stay as they are.

## Observable behavior

### `am resume <run-id> --engine yaml` (the default)

Byte-for-byte today's behavior. It still uses `select_resumable`'s current refusals, including the stopped-subtask remedy text, plus `interrupted_phase`, `resume_start_phase` and `yaml_engine.run_subtask(start_phase=...)`.

### `am resume <run-id> --engine pygents`

1. The same pre-`Store.open` refusals as yaml: unknown run, repo dir, board, and workflow load.
2. `select_resumable` on pygents returns the run's single subtask recorded `started` or `stopped`.
   - Zero candidates is refused with the existing "nothing in flight" wording, minus the relaunch remedy, which does not apply here.
   - More than one candidate is refused as today.
   - An `escalated` subtask is still refused. `retry` owns escalations; see the open question below.
3. Open the store and take `store.latest_checkpoint(subtask.card_id)`. Two cases are refused with `NotResumableError` (exit 3) before anything is written:
   - There is no row. The run was driven on yaml, or it died before its first `BEFORE_TURN`. The message says so.
   - The newest row is `done`. Only the final status write was lost, the same situation as the yaml "every phase done" refusal.
4. If the checkpoint's digest differs from `TASK.digest()`, the command prints `{"ok": false, "error": {...}}` whose message contains `workflow changed since checkpoint` and exits 3. It writes nothing: no orphan re-marking and no run/story/subtask row changes. `CheckpointMismatch` is the type this path raises or catches. The CLI must refuse before its own writes, not rely on `run_subtask` raising after they happen. The envelope's `type` is `CheckpointMismatch`, or a `CliError` wrapping it. Either way it rides `HANDLED` to exit 3.
5. Otherwise it marks orphan attempts `harness_error` exactly as today (`orphan_attempts` and `record_attempt`), records run, story and subtask `started`, and calls `runtime_engine.run_subtask(TASK, store, ..., resume_from=checkpoint)` with the same walk kwargs `drive_subtask` uses. It never calls `interrupted_phase` or `resume_start_phase`.
6. The agent is rebuilt from the checkpoint, so the pending turn and its loop count are the checkpoint's. Resuming from an `escalated` row re-runs the failed phase with the loop count it had. Resuming from a `parked` row continues the phase it parked before.
7. The payload keeps today's key set: `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`, `resumed_from` and `discarded_attempts`. `resumed_from` names the phase the checkpoint resumes at. If that cannot be read without `cli.py` touching pygents structures, the plan may add a read-only accessor inside `runtime/`; it must not change resume mechanics.
8. Exit codes are as today: 0 for done or stopped, 1 for escalated, and 3 for a refusal or mismatch. `--engine bogus` is a usage error: exit 2 and nothing on stdout.

### `am run --milestone --engine pygents` relaunch

- `done` cards are still skipped, as today.
- For every subtask a lane is about to drive, the lane calls `store.latest_open_checkpoint(card.id, TASK.name)`.
  - If that returns a row whose digest equals `TASK.digest()`, the driver gets `resume_from=<row>`, and the card continues from it inside the new run. New checkpoints are saved under the new run id.
  - If it returns `None` (no row, or the card's newest row is `done`) or the digest differs, the driver gets `resume_from=None` and the card starts fresh. No error, no warning is required, and no exit 3.
- `plan_check` still reuses a validated plan on a fresh start.
- On `--engine yaml` no lookup happens, and the driver gets no checkpoint.

`drive_subtask(..., resume_from=None)` passes `resume_from` to `runtime_engine.run_subtask`. Passing a non-`None` `resume_from` with `engine="yaml"` is refused with `ValueError`, before a runner is built. The `Driver` protocol grows the same keyword. Existing test drivers must keep working, which means either:

- the lane passes `resume_from` only when it is non-`None`, or
- the fakes are updated.

The plan picks one. No test assertion is loosened.

## Error paths

| Situation | Result |
|---|---|
| pygents, digest mismatch on `am resume` | `ok: false`, message contains "workflow changed since checkpoint", exit 3, nothing written |
| pygents, no checkpoint row for the in-flight card | `NotResumableError`, exit 3, nothing written |
| pygents, newest row `done` | `NotResumableError`, exit 3, nothing written |
| pygents, 0 or more than 1 `started`/`stopped` subtasks, or only `escalated` | `NotResumableError`, exit 3 (pre-`Store.open`) |
| pygents relaunch, mismatch or no open checkpoint | card starts fresh, no error |
| yaml, any of the above | unchanged from today |
| `--engine` not in `ENGINES` | exit 2, nothing on stdout |

## Open question, flagged rather than invented

The milestone spec says "after an escalation it re-runs the failed phase with the loop count it had". The findings only relax the `stopped` refusal. This spec therefore keeps `escalated` subtasks refused on `resume`. The escalated-row path is reached only when a `started` subtask's newest checkpoint is `escalated`, for example after a crash between the checkpoint save and the row write. If the spec critic reads §6 as making `escalated` subtasks resumable on pygents, the change is local:

- add `escalated` to step 2's candidate statuses, and
- add a test.

## Tests

Tier placement follows design §14 (`2026-09-23-agent-manager-design.md:497-512`) as applied in this repo:

- Pure: functions over hand-built models.
- Steps: a real temporary git repo and brd board, `XDG_DATA_HOME` under `tmp_path`, and the harness replaced at the `runner_factory`/`driver` seams.
- Fake-claude e2e: `tests/e2e`, the default suite, running the real `cli.app` with a fake `claude` on `PATH`.

| # | Test | File | Tier |
|---|---|---|---|
| 1 | `select_resumable` on pygents returns a lone `stopped` subtask; still refuses zero, more than one, and a lone `escalated` subtask | `tests/test_cli.py` | Pure |
| 2 | `select_resumable` on yaml (default) keeps every existing refusal and its wording (existing tests at 4494-4540 untouched) | `tests/test_cli.py` | Pure (existing) |
| 3 | A pygents subtask killed by a `BaseException` from the runner in `plan` resumes via `resume_run(engine="pygents")`. The runner sees `plan` next, not `explore` or `spec`, and the walk ends `done` | `tests/test_cli.py` | Steps |
| 4 | A stopped (parked) milestone subtask resumed with `am resume --engine pygents` continues to `done` instead of being refused | `tests/test_cli.py` | Steps |
| 5 | Orphan `started` attempts are recorded `harness_error` on the pygents branch and listed in `discarded_attempts` | `tests/test_cli.py` | Steps |
| 6 | A digest mismatch exits 3 with an `ok: false` envelope containing "workflow changed since checkpoint"; rows, attempts and checkpoints are unchanged afterwards | `tests/test_cli.py` | Steps |
| 7 | No checkpoint for the in-flight card (a yaml-driven run), and newest row `done`: each exits 3 and writes nothing | `tests/test_cli.py` | Steps |
| 8 | A `started` subtask whose newest row is `escalated` resumes at the failed phase with its loop count (the runner sees that phase next, with the recorded loop) | `tests/test_cli.py` | Steps |
| 9 | `am resume --engine bogus` exits 2 with nothing on stdout | `tests/test_cli.py` | Steps (CliRunner, as the existing `--engine bogus` test) |
| 10 | Relaunch on pygents: a card parked in run 1 gets `resume_from` equal to that row in run 2 (driver seam); a digest mismatch gets `None` and the run does not raise; a card whose newest row is `done` gets `None`; on yaml no `resume_from` is passed | `tests/test_orchestrate.py` | Steps |
| 11 | `drive_subtask` refuses `resume_from` with `engine="yaml"`, and hands it through on pygents | `tests/test_cli.py` | Steps |
| 12 | Under the fake `claude`, a pygents run killed in `plan` and then resumed with `am resume --engine pygents` does not re-dispatch `explore` or `spec`. The fake's log shows each once in total and `plan` twice. How the kill happens is a test env knob chosen in the plan; the fake is told nothing about run state | `tests/e2e/test_milestone_run.py` | Fake-claude e2e |
| 13 | A pygents milestone relaunch after a stop continues the parked card (fake log shows no re-dispatch of its finished phases) | `tests/e2e/test_milestone_run.py` | Fake-claude e2e |
| 14 | All existing resume and milestone tests pass unchanged on `--engine yaml`, and the parametrised e2e parity stays green on both engines | as existing | existing tiers |

Verification: `uv run pytest`, the whole default suite, green.

---

# Resume and Relaunch from Checkpoints on pygents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am resume --engine pygents` continues a killed or parked subtask from its newest checkpoint, and `am run --milestone --engine pygents` relaunch continues a card's open checkpoint when the workflow digest still matches, while every yaml path stays byte-for-byte as it is.

**Architecture:** One read-only accessor, `runtime_engine.pending_phase`, is added inside `runtime/` so `cli.py` never reads pygents' serialized agent. `cli.py` gains the checkpoint judgement (`checkpoint_resume_phase` for an explicit resume, which refuses; `continuable_checkpoint` for a relaunch, which silently answers `None`), a pygents branch of `resume_run` that refuses before any write and then drives through `drive_subtask(..., resume_from=...)`, and a `--engine` option on `resume`. `orchestrate.run_story_lane` asks `cli.continuable_checkpoint` before each pygents drive and passes `resume_from` only when it has one.

**Tech Stack:** Python 3.12, Typer, Pydantic, pygents 0.7.0 (reached only through `agent_manager.runtime.engine`), pytest with `--import-mode=importlib`, `uv`.

**Spec:** `docs/superpowers/specs/task-resume-and-relaunch-02890d5d-design.md` (prepended verbatim above).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-resume-and-relaunch-02890d5d`, branch `m6/task-resume-and-relaunch-02890d5d`, cut from `m6/task-select-the-engine-with-7fdec762`. Every path below is relative to that worktree. Nothing from any other subtask is assumed beyond what that base already contains (read for this plan: `runtime/engine.py`, `runtime/checkpoint.py`, `runtime/compile.py`, `store.py:506-928`, `cli.py`, `orchestrate.py`).

**Inputs note:** the orchestration prompt that commissioned this plan relayed both the spec author's summary (cut at 2000 characters) and the exploration findings (cut at 8000 characters) truncated mid-thought, so the upstream stages over-ran their briefs. This plan does not use either summary for anything the spec on disk or the code does not also say; every reference below was read from the worktree.

## Global Constraints

- Only `src/agent_manager/runtime/` imports pygents; `cli.py` and `orchestrate.py` reach it through `agent_manager.runtime.engine` only (`tests/test_orchestrate.py::test_the_engine_selecting_modules_never_import_pygents` guards it).
- The whole default suite stays green, `tests/e2e` included, on both engines: `uv run pytest`.
- Never break or return out of `agent.run()`; never checkpoint at `AFTER_TURN`; no hooks are added by this card.
- A fake `claude` knows only what its brief and the test's env knobs tell it; it is never told run or checkpoint state.
- G10: `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads are unchanged.
- `--engine yaml` (the default) on `resume` is byte-for-byte today's behavior, refusal wording included.
- Resume payload keys, exactly: `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`, `resumed_from`, `discarded_attempts`.
- Exit codes: 0 done or stopped, 1 escalated, 3 refusal or mismatch, 2 for an `--engine` outside `ENGINES` with nothing on stdout.
- A digest-mismatch refusal message contains `workflow changed since checkpoint`.
- Do not modify `store.py`, `runtime/checkpoint.py`, `integration.py`, or the resume mechanics of `runtime/engine.py` (only the read-only accessor of Task 1 is added there).

## Deviations from the spec, with evidence

1. **An `escalated` checkpoint written by a phase escalation holds no turn to continue.** Spec behavior item 6 and test 8 assume "resuming from an `escalated` row re-runs the failed phase with the loop count it had". The code cannot do that without changing resume mechanics, which the spec puts out of scope: `runtime/compile.py:128,132,158` raise `Escalated` without enqueuing a turn, pygents' `Agent.run()` clears `_current_turn` in its `finally` (`.venv/lib/python3.12/site-packages/pygents/agent.py:536-538`), and `runtime/engine.py:172-177` saves the `escalated` row only after `agent.run()` has exited. So that row's agent has `current_turn: None` and an empty `queue`. Resuming from it would run no turn, save `done` and record the subtask `done` with review never having passed. Worse, `Store.latest_open_checkpoint` counts `escalated` as open, so a pygents relaunch after the existing `test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it` scenario would mark `b1` done silently and break that parity test. This plan therefore treats "no pending turn" as not continuable: `am resume --engine pygents` refuses it (exit 3, nothing written) and a relaunch starts the card fresh, exactly as yaml does. An `escalated` row that does hold a pending turn (an error raised by the `BEFORE_TURN` hook itself, `tests/runtime/test_checkpoint.py:174-209`) is continued like any other. Spec test 8 is replaced by: Task 1's evidence test, Task 4's pure refusal tests, Task 5's Steps refusal test, and Task 7's relaunch tests (Steps and e2e).
2. **`resumed_from` comes from a read-only accessor in `runtime/`** (`runtime_engine.pending_phase`), as spec item 7 allows.
3. **The pygents resume preflight loads `WORKFLOW_NAME` (`task`), not `run.workflow`.** A milestone run records `workflow="milestone"` (`orchestrate.py:42`), for which no builtin document exists (`workflow/builtin/` holds only `task.yaml` and `integrate.yaml`); loading it would refuse every milestone subtask, including spec test 4's. Every subtask is walked through `TASK` either way. The yaml path keeps `load_builtin(run.workflow)` untouched.
4. **The lane passes `resume_from` only when it is non-`None`**, the first option the spec offers, so `FakeDriver`, `GatedDriver` and the whole-kwargs assertion in `test_drive_subtask_walks_the_engine_it_is_given_with_the_same_arguments` keep working unchanged. `drive_subtask` likewise adds `resume_from` to the pygents walk kwargs only when given.
5. **On a pygents resume, `--verify` and `--allow-no-verification` do not reach the walk, and `resume_run`'s `clock` is unused.** `runtime/engine.py:124-150` rebuilds the agent from the checkpoint and adds no seed on resume, so the gate context is the one the killed run was started with; the pygents walk stamps with its own default clock, as `drive_subtask` already does. Changing that is resume mechanics (out of scope). The options are still accepted, so the command line of a yaml resume works unchanged with `--engine pygents` appended.
6. **The kill and park knobs are test-side wrappers in the manager process, not fake-`claude` env knobs.** Spec test 12 leaves the knob to the plan. The Steps tests raise a plain `BaseException` subclass from the fake runner (the pattern `tests/runtime/test_resume.py:36-37` documents: `KeyboardInterrupt` is re-raised by asyncio out of the event loop before the engine unwinds). The e2e tests wrap `cli.run_direct`, which `cli.default_runner_factory` reads at call time: the real launcher still spawns the real fake, and the wrapper only raises after it returns (test 12) or holds a launch until the other lane's escalation (test 13). The fake is told nothing.
7. **The mismatch envelope `type` is `CheckpointMismatchError`**, a new class that is both a `CliError` (so it rides `HANDLED`) and a `runtime_engine.CheckpointMismatch` (so it is the engine's refusal by type), as spec item 4 allows.

## Review Focus

1. A card whose newest checkpoint is a phase escalation (`escalated`, no turn left) must never be "resumed" into a silent `done`: `am resume` refuses it and a relaunch starts it fresh. Pinned by Task 1 (`test_a_phase_escalation_leaves_an_escalated_row_with_no_pending_phase`), Task 4 (`test_checkpoint_resume_phase_refuses_an_escalated_row_with_no_turn_left`), Task 5 (`test_a_pygents_resume_refuses_a_phase_escalation_and_writes_nothing`), Task 7 (`b2` in `test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh`, and `a1` in the e2e relaunch test).
2. `am resume --engine pygents` on a milestone run (`run.workflow == "milestone"`) must not be refused by the workflow preflight. Pinned by Task 5 (`test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused` records its run as `milestone`).
3. A refused pygents resume must leave the orphan attempt `started`, so a later fresh run or yaml resume still sees it; nothing is re-marked before the checkpoint is judged. Pinned by Task 5 (`test_a_pygents_resume_across_a_workflow_change_writes_nothing`).
4. A failure in the relaunch's checkpoint lookup escalates only that subtask and never crashes the run. Pinned by Task 7 (`test_a_checkpoint_lookup_that_fails_escalates_that_subtask`).
5. `resume --engine` is exact: `PYGENTS` and the empty string are usage errors, never folded into a valid engine. Pinned by Task 6 (`test_resume_with_a_bad_engine_is_a_usage_error_that_starts_nothing`).

## File Structure

- `src/agent_manager/runtime/engine.py` — add `pending_phase(checkpoint) -> str | None` (read-only). Nothing else changes.
- `src/agent_manager/cli.py` — `CheckpointMismatchError`; `select_resumable(run, *, engine="yaml")`; `checkpoint_resume_phase`; `continuable_checkpoint`; `drive_subtask(..., resume_from=None)`; `_check_engine`; `resume_run(..., engine="yaml")` plus `_resume_from_checkpoint`; `resume --engine`.
- `src/agent_manager/orchestrate.py` — `Driver.__call__` gains `resume_from`; `run_story_lane` looks up `cli.continuable_checkpoint` on pygents.
- `tests/runtime/test_resume.py` — `pending_phase` tests.
- `tests/test_cli.py` — Pure and Steps tests for everything in `cli.py`; `recording_runner`/`_resume_factory` gain a `crash_with` keyword.
- `tests/test_orchestrate.py` — relaunch lookup tests.
- `tests/e2e/test_milestone_run.py` — the two fake-claude tests; one stale comment updated.

---

### Task 1: `runtime_engine.pending_phase`, the read-only accessor

**Files:**
- Modify: `src/agent_manager/runtime/engine.py:33-37` (add the function right after `class CheckpointMismatch`)
- Test: `tests/runtime/test_resume.py` (append at the end)

**Interfaces:**
- Consumes: `agent_manager.store.Checkpoint` (fields `run_id, card_id, seq, workflow, digest, reason, agent: dict, saved_at`), already imported under `TYPE_CHECKING` in `runtime/engine.py:27-30`.
- Produces: `runtime_engine.pending_phase(checkpoint: Checkpoint) -> str | None` — the phase name of `agent["current_turn"]`, else of `agent["queue"][0]`, else `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_resume.py`:

```python
# ── pending_phase (card 02890d5d) ────────────────────────────────────────────


def test_pending_phase_reads_the_turn_a_crashed_checkpoint_would_run_next(store):
    ran: list[str] = []
    with pytest.raises(_Crash):
        _go(_five(ran, {"c"}), store)

    assert runtime_engine.pending_phase(store.latest_checkpoint(CARD_ID)) == "c"


def test_pending_phase_reads_a_parked_checkpoint(store):
    ran: list[str] = []
    _go(_five(ran, set()), store, should_stop=lambda: ran == ["a"])
    parked = store.latest_checkpoint(CARD_ID)

    assert parked.reason == "parked"
    assert runtime_engine.pending_phase(parked) == "b"


def test_pending_phase_prefers_the_turn_in_flight_over_the_queue():
    checkpoint = store_module.Checkpoint(
        run_id=RUN_ID,
        card_id=CARD_ID,
        seq=0,
        workflow="five",
        digest="any",
        reason="turn",
        agent={
            "current_turn": {"kwargs": {"phase": "c", "loop": 1}},
            "queue": [{"kwargs": {"phase": "d", "loop": 0}}],
        },
        saved_at=FIXED,
    )

    assert runtime_engine.pending_phase(checkpoint) == "c"


def test_a_done_checkpoint_has_no_pending_phase(store):
    _go(_five([], set()), store)
    done = store.latest_checkpoint(CARD_ID)

    assert done.reason == "done"
    assert runtime_engine.pending_phase(done) is None


def _boom(card: str) -> dict[str, Any]:
    raise RuntimeError("boom")


def test_a_phase_escalation_leaves_an_escalated_row_with_no_pending_phase(store):
    """Why card 02890d5d refuses to resume such a row and relaunches it fresh:
    the failed turn was consumed, `Escalated` enqueued nothing, and
    `agent.run()` cleared `current_turn` on its way out, so the row holds no
    turn. Continuing from it would run nothing and record the subtask `done`."""
    summary = _go(Workflow("escalates", (Step("a", _boom), Step("b", _extra))), store)

    assert summary.status == "escalated"
    escalated = store.latest_checkpoint(CARD_ID)
    assert escalated.reason == "escalated"
    assert escalated.agent["current_turn"] is None
    assert escalated.agent["queue"] == []
    assert runtime_engine.pending_phase(escalated) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_resume.py -k pending_phase -v` and `uv run pytest tests/runtime/test_resume.py::test_a_phase_escalation_leaves_an_escalated_row_with_no_pending_phase -v`
Expected: FAIL with `AttributeError: module 'agent_manager.runtime.engine' has no attribute 'pending_phase'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/runtime/engine.py`, directly after the `CheckpointMismatch` class (after line 36), add:

```python
def pending_phase(checkpoint: Checkpoint) -> str | None:
    """The phase `checkpoint`'s agent would run next, or `None` if it holds no turn.

    Read-only: it reads the stored `Agent.to_dict()` and builds nothing, so a
    caller outside `runtime/` can name where a resume would start without
    touching a pygents structure itself (card 02890d5d). The next turn is the
    turn in flight if there was one, else the queue head -- the reading the
    `BEFORE_TURN` hook makes. A `done` row holds no turn, and neither does an
    `escalated` row written after a phase escalated: `Escalated` enqueues
    nothing and `agent.run()` clears the turn in flight on its way out.
    """
    agent = checkpoint.agent
    turn = agent.get("current_turn") or next(iter(agent.get("queue") or ()), None)
    return None if turn is None else turn["kwargs"]["phase"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: PASS (all, the pre-existing ones included).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/engine.py tests/runtime/test_resume.py
git commit -m "feat(runtime): read the phase a checkpoint would run next"
```

---

### Task 2: `select_resumable` accepts a `stopped` subtask on pygents

**Files:**
- Modify: `src/agent_manager/cli.py:396-452` (`select_resumable`)
- Test: `tests/test_cli.py` (append a new section at the end of the file)

**Interfaces:**
- Consumes: `models.Run`, `NotResumableError`, the `Engine` literal (defined later in the module at `cli.py:604`, so the annotation is the string `"Engine"`).
- Produces: `cli.select_resumable(run: models.Run, *, engine: "Engine" = "yaml") -> tuple[models.StoryRun, models.SubtaskRun]`. yaml behavior and wording unchanged; pygents counts `started` and `stopped`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
# ── pygents resume and relaunch (card 02890d5d) ──────────────────────────────


def test_select_resumable_on_pygents_returns_a_lone_stopped_subtask():
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    stopped = _pure_subtask("card-2", []).model_copy(update={"status": "stopped"})
    run = _pure_run([_pure_story("story-1", [done]), _pure_story("story-2", [stopped])])

    story, subtask = cli.select_resumable(run, engine="pygents")

    assert story.card_id == "story-2"
    assert subtask is stopped


def test_select_resumable_on_pygents_still_returns_a_lone_started_subtask():
    started = _pure_subtask("card-1", [])
    run = _pure_run([_pure_story("story-1", [started])])

    story, subtask = cli.select_resumable(run, engine="pygents")

    assert story.card_id == "story-1"
    assert subtask is started


def test_select_resumable_on_pygents_refuses_nothing_in_flight_without_the_relaunch_remedy():
    done = _pure_subtask("card-1", []).model_copy(update={"status": "done"})
    escalated = _pure_subtask("card-2", []).model_copy(update={"status": "escalated"})
    run = _pure_run([_pure_story("story-1", [done, escalated])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run, engine="pygents")

    message = str(caught.value)
    assert "no subtask recorded 'started' or 'stopped'" in message
    assert "found: card-1=done, card-2=escalated" in message
    assert "agent-manager status" in message
    assert "run --milestone" not in message


def test_select_resumable_on_pygents_refuses_a_lone_escalated_subtask():
    escalated = _pure_subtask("card-1", []).model_copy(update={"status": "escalated"})
    run = _pure_run([_pure_story("story-1", [escalated])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run, engine="pygents")

    assert "card-1=escalated" in str(caught.value)


def test_select_resumable_on_pygents_refuses_a_started_and_a_stopped_subtask_together():
    started = _pure_subtask("card-1", [])
    stopped = _pure_subtask("card-2", []).model_copy(update={"status": "stopped"})
    run = _pure_run([_pure_story("story-1", [started]), _pure_story("story-2", [stopped])])

    with pytest.raises(cli.NotResumableError) as caught:
        cli.select_resumable(run, engine="pygents")

    message = str(caught.value)
    assert "2 subtasks recorded 'started' or 'stopped' (card-1, card-2)" in message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "select_resumable_on_pygents" -v`
Expected: FAIL with `TypeError: select_resumable() got an unexpected keyword argument 'engine'`.

- [ ] **Step 3: Write the minimal implementation**

Replace `select_resumable` in `src/agent_manager/cli.py` (lines 396-452) with:

```python
def select_resumable(
    run: models.Run, *, engine: "Engine" = "yaml"
) -> tuple[models.StoryRun, models.SubtaskRun]:
    """The one subtask of `run` that was in flight, or a refusal naming why not.

    Pure over the tree `load_run` assembled, like `find_subtask`: which subtask
    is resumable is a question about recorded state, and answering it before any
    store is opened is what keeps a refusal from minting a run directory.

    Exactly one `started` subtask is the resumable shape. Zero means the run
    finished, escalated, stopped or never started, and the statuses are listed
    because the fix differs for each. A `stopped` subtask (addendum P4) stopped
    cleanly and did not fail, so the refusal names its remedy: relaunch the same
    `run --milestone` command. It never points at `retry`, which is for
    escalations. More than one is a milestone-shaped run: this
    command drives one subtask the way `run --card` does, and choosing between
    them would leave the rest recorded `started` with nothing driving them.

    `engine` (card 02890d5d): on `pygents` a `stopped` subtask counts beside a
    `started` one, because its parked checkpoint is what `resume --engine
    pygents` continues from, so the relaunch remedy can never apply there.
    `escalated` is refused on both engines. The yaml wording is unchanged to
    the character. `Engine` is quoted because it is defined further down.
    """
    resumable = ("started", "stopped") if engine == "pygents" else ("started",)
    wanted = " or ".join(repr(status) for status in resumable)
    in_flight = [
        (story, subtask)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.status in resumable
    ]
    if len(in_flight) == 1:
        return in_flight[0]
    if not in_flight:
        found = (
            ", ".join(
                f"{subtask.card_id}={subtask.status}"
                for story in run.stories
                for subtask in story.subtasks
            )
            or "no subtask at all"
        )
        stopped = [
            subtask.card_id
            for story in run.stories
            for subtask in story.subtasks
            if subtask.status == "stopped"
        ]
        remedy = (
            f"; {', '.join(stopped)} stopped cleanly and did not fail, so relaunch the"
            " same `agent-manager run --milestone` command that started this run to"
            " continue from where it stopped"
            if stopped
            else ""
        )
        raise NotResumableError(
            f"run {run.id!r} has no subtask recorded {wanted}, so there is no work"
            f" in flight to pick up (found: {found});"
            f" `agent-manager status {run.id}` shows the run as it stands{remedy}"
        )
    cards = ", ".join(subtask.card_id for _story, subtask in in_flight)
    raise NotResumableError(
        f"run {run.id!r} has {len(in_flight)} subtasks recorded {wanted} ({cards}),"
        " and `resume` drives one subtask the way `run --card` does;"
        f" `agent-manager status {run.id}` shows all of them"
    )
```

(`repr("started")` is `'started'`, so every yaml message is identical to today's; on pygents a `stopped` subtask is always in flight, so `stopped` is empty whenever the zero branch is reached and no remedy is appended.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "select_resumable" -v`
Expected: PASS, including the unchanged yaml tests `test_select_resumable_tells_a_stopped_run_to_relaunch_the_milestone`, `test_select_resumable_keeps_its_wording_when_nothing_is_stopped` and the rest (spec test 2).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): select a stopped subtask for resume on the pygents engine"
```

---

### Task 3: `drive_subtask` takes `resume_from`

**Files:**
- Modify: `src/agent_manager/cli.py:690-753` (`drive_subtask`)
- Test: `tests/test_cli.py` (append to the card 02890d5d section)

**Interfaces:**
- Consumes: `store_module.Checkpoint`; `runtime_engine.run_subtask(workflow, store, *, ..., resume_from=None)` (`runtime/engine.py:39-86`).
- Produces: `cli.drive_subtask(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, should_stop=None, engine: Engine = "yaml", resume_from: store_module.Checkpoint | None = None) -> SubtaskDrive`. On pygents, `resume_from` is added to the walk kwargs only when not `None`; with `engine="yaml"` a non-`None` `resume_from` raises `ValueError` before the runner factory is called.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
CHECKPOINT_AT = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)


def _checkpoint(
    reason: str,
    *,
    digest: str | None = None,
    current: str | None = None,
    queue: tuple[str, ...] = (),
) -> store_module.Checkpoint:
    """A hand-built checkpoint row of `TASK`: `current` is the turn in flight, `queue` the turns after it."""
    return store_module.Checkpoint(
        run_id="20260926T090000Z-02890d5d",
        card_id="card-1",
        seq=4,
        workflow=task_workflow.TASK.name,
        digest=task_workflow.TASK.digest() if digest is None else digest,
        reason=reason,
        agent={
            "current_turn": None if current is None else {"kwargs": {"phase": current, "loop": 0}},
            "queue": [{"kwargs": {"phase": name, "loop": 0}} for name in queue],
        },
        saved_at=CHECKPOINT_AT,
    )


def test_drive_subtask_hands_resume_from_to_the_pygents_walk(monkeypatch):
    walks = _record_walks(monkeypatch)
    factory, _runner = _recording_factory([])
    checkpoint = _checkpoint("parked", queue=("plan",))

    cli.drive_subtask(
        store=object(),
        run_id=DRIVE_RUN_ID,
        card=DRIVE_CARD,
        parent=DRIVE_PARENT,
        subtask=_drive_row(),
        repo_dir=DRIVE_REPO,
        runner_factory=factory,
        engine="pygents",
        resume_from=checkpoint,
    )

    assert walks["yaml"] == []
    ((workflow, _store, kwargs),) = walks["pygents"]
    assert workflow is task_workflow.TASK
    assert kwargs["resume_from"] is checkpoint


def test_drive_subtask_refuses_resume_from_on_yaml_before_building_a_runner(monkeypatch):
    walks = _record_walks(monkeypatch)
    seen: list[dict[str, Any]] = []
    factory, _ = _recording_factory(seen)

    with pytest.raises(ValueError, match="resume_from"):
        cli.drive_subtask(
            store=object(),
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=_drive_row(),
            repo_dir=DRIVE_REPO,
            runner_factory=factory,
            engine="yaml",
            resume_from=_checkpoint("parked", queue=("plan",)),
        )

    assert seen == []
    assert walks == {"yaml": [], "pygents": []}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "drive_subtask_hands_resume_from or drive_subtask_refuses_resume_from" -v`
Expected: FAIL with `TypeError: drive_subtask() got an unexpected keyword argument 'resume_from'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, change the `drive_subtask` signature (line 702) to add the keyword after `engine`:

```python
    engine: Engine = "yaml",
    resume_from: store_module.Checkpoint | None = None,
) -> SubtaskDrive:
```

Append this paragraph to its docstring, before the closing `"""`:

```python
    `resume_from` (card 02890d5d) continues a pygents walk from a saved
    checkpoint. It joins the walk's keywords only when given, so a fresh walk
    is called exactly as before. With `engine="yaml"` it is refused before a
    runner is built: the yaml walk has no checkpoints to continue from.
```

Right after the existing `if engine not in ENGINES: raise ValueError(...)` (line 723-724) add:

```python
    if resume_from is not None and engine != "pygents":
        raise ValueError(
            f"resume_from continues a pygents checkpoint, and engine {engine!r} has"
            " none; pass engine='pygents' or no resume_from"
        )
```

Replace the pygents branch (lines 745-746) with:

```python
    if engine == "pygents":
        if resume_from is not None:
            walk["resume_from"] = resume_from
        summary = runtime_engine.run_subtask(task_workflow.TASK, store, **walk)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "drive_subtask" -v`
Expected: PASS, including the unchanged `test_drive_subtask_walks_the_engine_it_is_given_with_the_same_arguments` (its whole-kwargs comparison proves no `resume_from` key appears on a fresh walk).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): hand a checkpoint to the pygents walk through drive_subtask"
```

---

### Task 4: Judge a checkpoint — `checkpoint_resume_phase`, `continuable_checkpoint`, `CheckpointMismatchError`

**Files:**
- Modify: `src/agent_manager/cli.py:120-127` (add `CheckpointMismatchError` after `NotResumableError`)
- Modify: `src/agent_manager/cli.py:566-582` (add the two functions right after `orphan_attempts`, before `app = typer.Typer(`)
- Test: `tests/test_cli.py` (append to the card 02890d5d section)

**Interfaces:**
- Consumes: `runtime_engine.pending_phase` (Task 1), `runtime_engine.CheckpointMismatch`, `task_workflow.TASK` (`.name`, `.digest()`), `Store.latest_open_checkpoint(card_id, workflow)`, `_checkpoint(...)` test helper (Task 3).
- Produces:
  - `class cli.CheckpointMismatchError(CliError, runtime_engine.CheckpointMismatch)`.
  - `cli.checkpoint_resume_phase(checkpoint: store_module.Checkpoint | None, *, card_id: str, run_id: str) -> str` — the phase to resume at; raises `NotResumableError` (no row; `done`; no turn left) or `CheckpointMismatchError` (digest differs), checked in that order: none, done, digest, no turn.
  - `cli.continuable_checkpoint(store: Store, card_id: str) -> store_module.Checkpoint | None` — the newest open row of `TASK` whose digest matches and which holds a turn, else `None`. Never raises for a mismatch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_checkpoint_resume_phase_is_the_queue_head_of_a_parked_row():
    phase = cli.checkpoint_resume_phase(
        _checkpoint("parked", queue=("validate_plan", "implement")),
        card_id="card-1",
        run_id="run-1",
    )

    assert phase == "validate_plan"


def test_checkpoint_resume_phase_prefers_the_turn_in_flight():
    phase = cli.checkpoint_resume_phase(
        _checkpoint("turn", current="plan", queue=("validate_plan",)),
        card_id="card-1",
        run_id="run-1",
    )

    assert phase == "plan"


def test_checkpoint_resume_phase_refuses_a_card_with_no_checkpoint():
    with pytest.raises(cli.NotResumableError) as caught:
        cli.checkpoint_resume_phase(None, card_id="card-1", run_id="run-1")

    message = str(caught.value)
    assert "card-1" in message
    assert "no checkpoint" in message


def test_checkpoint_resume_phase_refuses_a_done_row_before_judging_its_digest():
    with pytest.raises(cli.NotResumableError) as caught:
        cli.checkpoint_resume_phase(
            _checkpoint("done", digest="saved-under-another-task"),
            card_id="card-1",
            run_id="run-1",
        )

    assert not isinstance(caught.value, cli.CheckpointMismatchError)
    assert "'done'" in str(caught.value)


def test_checkpoint_resume_phase_refuses_a_changed_workflow_as_a_checkpoint_mismatch():
    with pytest.raises(cli.CheckpointMismatchError) as caught:
        cli.checkpoint_resume_phase(
            _checkpoint("parked", digest="saved-under-another-task", queue=("plan",)),
            card_id="card-1",
            run_id="run-1",
        )

    assert isinstance(caught.value, runtime_engine.CheckpointMismatch)
    assert isinstance(caught.value, cli.HANDLED)
    message = str(caught.value)
    assert "workflow changed since checkpoint" in message
    assert "saved-under-another-task" in message
    assert task_workflow.TASK.digest() in message


def test_checkpoint_resume_phase_refuses_an_escalated_row_with_no_turn_left():
    with pytest.raises(cli.NotResumableError) as caught:
        cli.checkpoint_resume_phase(
            _checkpoint("escalated"), card_id="card-1", run_id="run-1"
        )

    message = str(caught.value)
    assert "'escalated'" in message
    assert "no turn left" in message


def test_checkpoint_resume_phase_continues_an_escalated_row_that_still_holds_a_turn():
    """An error raised by the BEFORE_TURN hook escalates with the turn still
    queued (tests/runtime/test_checkpoint.py:174-209): that row is continuable."""
    phase = cli.checkpoint_resume_phase(
        _checkpoint("escalated", queue=("review",)), card_id="card-1", run_id="run-1"
    )

    assert phase == "review"


def _saved(
    opened: store_module.Store,
    card_id: str,
    reason: str,
    *,
    digest: str | None = None,
    queue: tuple[str, ...] = ("implement",),
    minute: int = 0,
) -> store_module.Checkpoint:
    return opened.save_checkpoint(
        card_id,
        workflow=task_workflow.TASK.name,
        digest=task_workflow.TASK.digest() if digest is None else digest,
        reason=reason,
        agent={
            "current_turn": None,
            "queue": [{"kwargs": {"phase": name, "loop": 0}} for name in queue],
        },
        saved_at=CHECKPOINT_AT.replace(minute=minute),
    )


def test_continuable_checkpoint_is_the_open_matching_row_or_none(projection):
    opened = store_module.Store.open(projection, "20260926T090000Z-02890d5d")
    try:
        parked = _saved(opened, "card-parked", "parked")
        _saved(opened, "card-changed", "parked", digest="saved-under-another-task")
        _saved(opened, "card-closed", "parked", minute=1)
        _saved(opened, "card-closed", "done", queue=(), minute=2)
        _saved(opened, "card-escalated", "escalated", queue=())
        found = {
            card: cli.continuable_checkpoint(opened, card)
            for card in (
                "card-parked",
                "card-changed",
                "card-closed",
                "card-escalated",
                "card-never-saved",
            )
        }
    finally:
        opened.close()

    got = found.pop("card-parked")
    assert got is not None
    assert (got.run_id, got.card_id, got.seq, got.reason) == (
        parked.run_id,
        "card-parked",
        parked.seq,
        "parked",
    )
    assert found == {
        "card-changed": None,
        "card-closed": None,
        "card-escalated": None,
        "card-never-saved": None,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "checkpoint_resume_phase or continuable_checkpoint" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'checkpoint_resume_phase'` (and `CheckpointMismatchError` / `continuable_checkpoint` likewise).

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, directly after `class NotResumableError(CliError)` (after line 127) add:

```python
class CheckpointMismatchError(CliError, runtime_engine.CheckpointMismatch):
    """`resume --engine pygents` found a checkpoint saved under another `TASK`.

    A `CliError`, so it rides `HANDLED` to an `ok: false` envelope at exit 3,
    and a `runtime_engine.CheckpointMismatch`, so it is the engine's own
    refusal by type (card 02890d5d). The CLI raises it itself, before any
    write, rather than letting `run_subtask` raise it after the orphan
    attempts and the `started` rows were already recorded.
    """
```

Directly after `orphan_attempts` (after line 582, before `app = typer.Typer(`) add:

```python
def checkpoint_resume_phase(
    checkpoint: store_module.Checkpoint | None, *, card_id: str, run_id: str
) -> str:
    """The phase `resume --engine pygents` continues `card_id` at, or a refusal.

    Pure over the row `Store.latest_checkpoint` returned, so every refusal is
    testable without a store, and `resume_run` calls it before its first
    write. In order: no row (a yaml run, or one that died before its first
    turn); a newest row `done` (only the final status write was lost); a
    digest other than `TASK.digest()`; a row holding no turn, which is what a
    phase escalation leaves (`runtime_engine.pending_phase`).
    """
    if checkpoint is None:
        raise NotResumableError(
            f"card {card_id} in run {run_id!r} has no checkpoint to resume from:"
            " the run was driven on the yaml engine, or it died before its first"
            " turn; resume a yaml run without `--engine pygents`"
        )
    if checkpoint.reason == "done":
        raise NotResumableError(
            f"the newest checkpoint of card {card_id} in run {run_id!r} is 'done',"
            " so there is no turn to continue -- only the final status write was"
            " lost; start a fresh run with `agent-manager run --card` if the card"
            " still needs work"
        )
    digest = task_workflow.TASK.digest()
    if checkpoint.digest != digest:
        raise CheckpointMismatchError(
            f"workflow changed since checkpoint: checkpoint #{checkpoint.seq} of card"
            f" {card_id} in run {run_id!r} was saved under digest {checkpoint.digest},"
            f" but workflow {task_workflow.TASK.name!r} now has digest {digest};"
            " start a fresh run with `agent-manager run --card`"
        )
    phase = runtime_engine.pending_phase(checkpoint)
    if phase is None:
        raise NotResumableError(
            f"the newest checkpoint of card {card_id} in run {run_id!r} is"
            f" {checkpoint.reason!r} with no turn left to run: a phase escalated and"
            " ended the walk; start a fresh run with `agent-manager run --card`"
        )
    return phase


def continuable_checkpoint(
    store: Store, card_id: str
) -> store_module.Checkpoint | None:
    """The open checkpoint a pygents relaunch continues `card_id` from, or `None`.

    `Store.latest_open_checkpoint` across every run, for `TASK`'s name. A row
    saved under another digest, or one holding no turn (a phase escalation,
    see `runtime_engine.pending_phase`), is `None` too: a relaunch never
    refuses, it starts the card from its first phase as the yaml engine does
    (card 02890d5d).
    """
    found = store.latest_open_checkpoint(card_id, task_workflow.TASK.name)
    if found is None or found.digest != task_workflow.TASK.digest():
        return None
    if runtime_engine.pending_phase(found) is None:
        return None
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "checkpoint_resume_phase or continuable_checkpoint or not_resumable" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): judge a checkpoint for resume and for relaunch"
```

---

### Task 5: `resume_run(engine="pygents")` continues from the checkpoint

**Files:**
- Modify: `src/agent_manager/cli.py:1357-1478` (`resume_run`; add `_resume_from_checkpoint` directly above it)
- Modify: `tests/test_cli.py:3974-4051` (`recording_runner` and `_resume_factory` gain `crash_with`)
- Test: `tests/test_cli.py` (append to the card 02890d5d section)

**Interfaces:**
- Consumes: `select_resumable(run, engine="pygents")` (Task 2), `drive_subtask(..., engine="pygents", resume_from=...)` (Task 3), `checkpoint_resume_phase` (Task 4), `orphan_attempts`, `Store.latest_checkpoint(card_id)`.
- Produces: `cli.resume_run(run_id, *, repo_dir, allow_no_verification=False, commands=(), runner_factory=None, clock=_utcnow, engine: Engine = "yaml") -> dict[str, Any]` with the 13-key payload; an unknown `engine` raises `ValueError` before anything is read. Test helpers: `recording_runner(..., crash_with: type[BaseException] = KeyboardInterrupt)`, `_resume_factory(seen=None, crash_at=None, crash_with=KeyboardInterrupt)`, `_Killed`, `RESUME_KEYS`, `_crash_pygents`, `_checkpoint_rows`, `_resume_state`, `_force_started`, `_plant_changed_digest`, `_park_pygents` (Task 6 reuses `_crash_pygents`, `_plant_changed_digest`, `_resume_state`).

- [ ] **Step 1: Give the fake runner a configurable crash**

In `tests/test_cli.py`, change `recording_runner` (line 3974) to:

```python
def recording_runner(
    *,
    store,
    run_id: str,
    story_id: str,
    card_id: str,
    crash_at: str | None = None,
    seen: list[str] | None = None,
    crash_with: type[BaseException] = KeyboardInterrupt,
):
```

add to its docstring, after the `crash_at` paragraph:

```python
    `crash_with` is the type raised. The pygents walk needs a plain
    `BaseException` subclass (`_Killed`): asyncio re-raises `KeyboardInterrupt`
    out of the event loop before the engine unwinds (tests/runtime/test_resume.py).
```

and change the raise (line 4021) to:

```python
        if crash_at is not None and phase.name == crash_at:
            raise crash_with(f"simulated kill during {phase.name}")
```

Change `_resume_factory` (line 4038) to:

```python
def _resume_factory(
    seen: list[str] | None = None,
    crash_at: str | None = None,
    crash_with: type[BaseException] = KeyboardInterrupt,
):
    """A `cli.RunnerFactory` handing `recording_runner` the store the CLI opened."""

    def factory(*, workflow, store, run_id, story_id, card_id):
        return recording_runner(
            store=store,
            run_id=run_id,
            story_id=story_id,
            card_id=card_id,
            crash_at=crash_at,
            seen=seen,
            crash_with=crash_with,
        )

    return factory
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_cli.py`:

```python
class _Killed(BaseException):
    """A process death mid-phase for the pygents walk. Not `KeyboardInterrupt`:
    asyncio re-raises that out of the event loop before the engine unwinds."""


RESUME_KEYS = {
    "run_id",
    "card_id",
    "story_id",
    "branch",
    "base_branch",
    "worktree",
    "status",
    "failed_phase",
    "detail",
    "skipped",
    "warnings",
    "resumed_from",
    "discarded_attempts",
}
"""Today's resume payload keys; the pygents branch adds and drops none (G10)."""


def _crash_pygents(project: Path, cards: dict[str, str], phase: str) -> str:
    """Drive a real pygents `run_card` until it is killed inside `phase`, and name the run."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    with pytest.raises(_Killed):
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            clock=lambda: CRASHED_AT,
            runner_factory=_resume_factory(crash_at=phase, crash_with=_Killed),
            engine="pygents",
        )
    return run_id


def _checkpoint_rows(root: Path) -> list[tuple]:
    conn = sqlite3.connect(paths.project_db_path(root))
    try:
        return conn.execute(
            "SELECT run_id, card_id, seq, reason, digest FROM checkpoints"
            " ORDER BY run_id, card_id, seq"
        ).fetchall()
    finally:
        conn.close()


def _resume_state(root: Path) -> tuple:
    """Everything a refused resume must leave alone: the run tree on disk (the
    journal included), the attempt rows and the checkpoint rows."""
    return (_runs_snapshot(), _attempt_rows(root), _checkpoint_rows(root))


def _force_started(project: Path, run_id: str, card_id: str) -> None:
    """Re-record the subtask `started`: what a crash between the engine's closing
    checkpoint and the caller's final status write leaves behind."""
    root = cli.resolve_repo_dir(project)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    found = cli.find_subtask(run, card_id)
    assert found is not None
    story, subtask = found
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_subtask(story.card_id, subtask.model_copy(update={"status": "started"}))
    finally:
        opened.close()


def _plant_changed_digest(project: Path, run_id: str, card_id: str) -> None:
    """A newer copy of the newest checkpoint, saved under a digest `TASK` does not have."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        newest = opened.latest_checkpoint(card_id)
        assert newest is not None
        opened.save_checkpoint(
            card_id,
            workflow=newest.workflow,
            digest="saved-under-another-task",
            reason=newest.reason,
            agent=newest.agent,
            saved_at=datetime.now(timezone.utc),
        )
    finally:
        opened.close()


def _park_pygents(project: Path, cards: dict[str, str]) -> str:
    """A milestone run whose one subtask the run's stop parked on pygents after `spec`.

    Recorded the way `orchestrate.run_story_lane` records it: the run is a
    `milestone` run, the engine records the subtask `stopped`, the lane
    records the story `stopped`.
    """
    root = cli.resolve_repo_dir(project)
    parent = board.show(cards["story"], repo_dir=root)
    card = board.show(cards["subtask"], repo_dir=root)
    run_id = cli.mint_run_id(cards["milestone"], CRASHED_AT)
    branch = dag.task_branch("m1", card)
    subtask = models.SubtaskRun(
        card_id=card.id,
        branch=branch,
        base_branch="main",
        status="started",
        worktree_path=cli.worktree_for(root, branch),
    )
    story = models.StoryRun(
        card_id=parent.id, title=parent.title, level=0, status="started", tip_branch=branch
    )
    seen: list[str] = []
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=orchestrate.MILESTONE_WORKFLOW,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="started",
                started_at=CRASHED_AT,
                config=models.RunConfig(),
            )
        )
        opened.record_story(story)
        opened.record_subtask(parent.id, subtask)
        drive = cli.drive_subtask(
            store=opened,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=root,
            runner_factory=_resume_factory(seen),
            should_stop=lambda: seen[-1:] == ["spec"],
            engine="pygents",
        )
        assert drive.summary.status == "stopped"
        assert drive.summary.detail == "stopped before validate_spec"
        opened.record_story(story.model_copy(update={"status": "stopped"}))
    finally:
        opened.close()
    return run_id


def test_resume_run_refuses_an_unknown_engine_before_reading_anything(tmp_path, monkeypatch):
    """The engine is checked first: the repo dir does not exist, and the
    refusal is the engine's `ValueError`, not the repo dir's `RepoDirError`."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    with pytest.raises(ValueError, match="unknown engine"):
        cli.resume_run(
            "20260923T140506Z-cbe34d00", repo_dir=tmp_path / "missing", engine="bogus"
        )


@requires_git
@requires_brd
def test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint(
    project, cards, monkeypatch
):
    """Spec test 3: the runner sees `plan` next, never `explore` or `spec`, and
    the yaml back-off helpers are never consulted."""
    run_id = _crash_pygents(project, cards, "plan")
    monkeypatch.setattr(cli, "interrupted_phase", _Forbidden("interrupted_phase"))
    monkeypatch.setattr(cli, "resume_start_phase", _Forbidden("resume_start_phase"))
    seen: list[str] = []

    payload = cli.resume_run(
        run_id, repo_dir=project, runner_factory=_resume_factory(seen), engine="pygents"
    )

    assert seen[0] == "plan"
    assert not {"explore", "spec", "validate_spec"} & set(seen)
    assert payload["status"] == "done"
    assert payload["resumed_from"] == "plan"
    assert payload["run_id"] == run_id
    assert payload["card_id"] == cards["subtask"]
    assert payload["story_id"] == cards["story"]
    assert set(payload) == RESUME_KEYS
    assert board.show(cards["subtask"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_a_pygents_resume_marks_the_orphan_attempt_harness_error(project, cards):
    """Spec test 5: the orphan is discarded exactly as the yaml resume does it."""
    run_id = _crash_pygents(project, cards, "plan")

    payload = cli.resume_run(
        run_id, repo_dir=project, runner_factory=_resume_factory(), engine="pygents"
    )

    assert payload["discarded_attempts"] == [{"phase": "plan", "n": 1}]
    plan = [row for row in _attempt_rows(project) if row[3] == "plan"]
    assert [(row[4], row[5]) for row in plan] == [(1, "harness_error"), (2, "ok")]
    assert [row for row in _attempt_rows(project) if row[5] == "started"] == []


@requires_git
@requires_brd
def test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused(
    project, cards
):
    """Spec test 4, and Review Focus 2: the run is a `milestone` run, which the
    yaml path cannot load and still refuses with its relaunch remedy."""
    run_id = _park_pygents(project, cards)

    with pytest.raises(cli.NotResumableError) as refused:
        cli.resume_run(run_id, repo_dir=project, runner_factory=_Forbidden("runner_factory"))
    assert "agent-manager run --milestone" in str(refused.value)

    after: list[str] = []
    payload = cli.resume_run(
        run_id, repo_dir=project, runner_factory=_resume_factory(after), engine="pygents"
    )

    assert payload["status"] == "done"
    assert payload["resumed_from"] == "validate_spec"
    assert payload["discarded_attempts"] == []
    assert after[0] == "validate_spec"
    assert not {"explore", "spec"} & set(after)
    assert board.show(cards["subtask"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_a_yaml_run_has_no_checkpoint_and_a_pygents_resume_writes_nothing(project, cards):
    """Spec test 7, first half."""
    run_id = _crash_mid_phase(project, cards, "implement")
    before = _resume_state(project)

    with pytest.raises(cli.NotResumableError) as caught:
        cli.resume_run(
            run_id,
            repo_dir=project,
            runner_factory=_Forbidden("runner_factory"),
            engine="pygents",
        )

    assert "no checkpoint" in str(caught.value)
    assert _resume_state(project) == before


@requires_git
@requires_brd
def test_a_pygents_resume_of_a_done_checkpoint_writes_nothing(project, cards):
    """Spec test 7, second half: only the final status write was lost."""
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
        engine="pygents",
    )
    assert payload["status"] == "done"
    _force_started(project, payload["run_id"], cards["subtask"])
    before = _resume_state(project)

    with pytest.raises(cli.NotResumableError) as caught:
        cli.resume_run(
            payload["run_id"],
            repo_dir=project,
            runner_factory=_Forbidden("runner_factory"),
            engine="pygents",
        )

    assert "'done'" in str(caught.value)
    assert _resume_state(project) == before


@requires_git
@requires_brd
def test_a_pygents_resume_refuses_a_phase_escalation_and_writes_nothing(project, cards):
    """Replaces spec test 8 (plan deviation 1): a phase escalation's row holds
    no turn, so continuing it would record `done` with review never passed."""
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(fail="review"),
        engine="pygents",
    )
    assert payload["status"] == "escalated"
    _force_started(project, payload["run_id"], cards["subtask"])
    before = _resume_state(project)

    with pytest.raises(cli.NotResumableError) as caught:
        cli.resume_run(
            payload["run_id"],
            repo_dir=project,
            runner_factory=_Forbidden("runner_factory"),
            engine="pygents",
        )

    message = str(caught.value)
    assert "'escalated'" in message
    assert "no turn left" in message
    assert _resume_state(project) == before


@requires_git
@requires_brd
def test_a_pygents_resume_across_a_workflow_change_writes_nothing(project, cards):
    """Spec test 6 at the function, and Review Focus 3: the orphan attempt is
    still `started` afterwards, because nothing is re-marked before the
    checkpoint is judged."""
    run_id = _crash_pygents(project, cards, "plan")
    _plant_changed_digest(project, run_id, cards["subtask"])
    before = _resume_state(project)

    with pytest.raises(cli.CheckpointMismatchError) as caught:
        cli.resume_run(
            run_id,
            repo_dir=project,
            runner_factory=_Forbidden("runner_factory"),
            engine="pygents",
        )

    assert "workflow changed since checkpoint" in str(caught.value)
    assert _resume_state(project) == before
    plan = [row for row in _attempt_rows(project) if row[3] == "plan"]
    assert [(row[4], row[5]) for row in plan] == [(1, "started")]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "resume_run_refuses_an_unknown_engine or pygents_run_killed_in_plan or pygents_resume or parked_milestone_subtask or yaml_run_has_no_checkpoint" -v`
Expected: FAIL with `TypeError: resume_run() got an unexpected keyword argument 'engine'` in every test (the `_Killed` crash itself already works: `run_card` has had `engine` since 7fdec762).

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/cli.py`, directly above `def resume_run(` (line 1357), add:

```python
def _resume_from_checkpoint(
    run: models.Run,
    *,
    root: Path,
    allow_no_verification: bool,
    commands: Sequence[str],
    runner_factory: RunnerFactory | None,
) -> dict[str, Any]:
    """`resume --engine pygents`: continue the run's one in-flight subtask from its checkpoint.

    The yaml resume's order, kept: every refusal that needs no store --
    nothing in flight, a card the board lost, a workflow that will not load --
    comes before `Store.open`. The checkpoint can only be read through the
    store, so its refusals (`checkpoint_resume_phase`) come right after it is
    opened and before the first write. Then the orphan attempts are marked
    `harness_error` and the run, story and subtask recorded `started`, as the
    yaml resume does, and `drive_subtask` walks `TASK` from the checkpoint.
    `interrupted_phase` and `resume_start_phase` are never called: the
    checkpoint's queue says where the walk goes on.

    The preflight loads `WORKFLOW_NAME`, not `run.workflow`: a milestone run
    records `milestone`, which is no document, and every subtask is walked
    through `TASK` on this engine either way (card 02890d5d).
    """
    story, subtask = select_resumable(run, engine="pygents")
    card = board.show(subtask.card_id, repo_dir=root)
    parent = board.show(story.card_id, repo_dir=root)
    load_builtin(WORKFLOW_NAME)
    orphans = orphan_attempts(subtask)
    resumed = subtask.model_copy(update={"status": "started"})

    store = Store.open(root, run.id)
    try:
        checkpoint = store.latest_checkpoint(subtask.card_id)
        phase = checkpoint_resume_phase(checkpoint, card_id=subtask.card_id, run_id=run.id)
        for orphan, attempt in orphans:
            store.record_attempt(
                story.card_id,
                subtask.card_id,
                orphan.name,
                attempt.model_copy(update={"status": "harness_error"}),
            )
        store.record_run(run.model_copy(update={"status": "started"}))
        store.record_story(story.model_copy(update={"status": "started"}))
        store.record_subtask(story.card_id, resumed)

        drive = drive_subtask(
            store=store,
            run_id=run.id,
            card=card,
            parent=parent,
            subtask=resumed,
            repo_dir=root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            engine="pygents",
            resume_from=checkpoint,
        )
        summary = drive.summary

        store.record_run(run.model_copy(update={"status": summary.status}))
        store.record_story(story.model_copy(update={"status": summary.status}))
        store.record_subtask(
            story.card_id, resumed.model_copy(update={"status": summary.status})
        )

        return {
            "run_id": run.id,
            "card_id": subtask.card_id,
            "story_id": story.card_id,
            "branch": subtask.branch,
            "base_branch": subtask.base_branch,
            "worktree": None
            if subtask.worktree_path is None
            else str(subtask.worktree_path),
            "status": summary.status,
            "failed_phase": summary.failed_phase,
            "detail": summary.detail,
            "skipped": list(summary.skipped),
            "warnings": drive.warnings,
            "resumed_from": phase,
            "discarded_attempts": [
                {"phase": orphan.name, "n": attempt.n} for orphan, attempt in orphans
            ],
        }
    finally:
        store.close()
```

Change the `resume_run` signature (lines 1357-1365) to:

```python
def resume_run(
    run_id: str,
    *,
    repo_dir: Path,
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
    engine: Engine = "yaml",
) -> dict[str, Any]:
```

Append to its docstring, before the closing `"""`:

```python
    `engine` picks the resume (card 02890d5d). `yaml` is everything described
    above, unchanged. `pygents` is `_resume_from_checkpoint`, which continues
    from the subtask's newest checkpoint instead of re-running a phase. An
    unknown engine is refused before anything is read. `clock` is the yaml
    walk's; the pygents walk stamps with its own default, as `drive_subtask`
    does.
```

Insert, as the first statement of the body (before `root = resolve_repo_dir(repo_dir)`):

```python
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; expected one of {', '.join(ENGINES)}")
```

Insert right after the `try: ... finally: conn.close()` block that loads `run` (after line 1392) and before `story, subtask = select_resumable(run)`:

```python
    if engine == "pygents":
        return _resume_from_checkpoint(
            run,
            root=root,
            allow_no_verification=allow_no_verification,
            commands=commands,
            runner_factory=runner_factory,
        )
```

Leave the rest of `resume_run` (the yaml path) exactly as it is.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "resume" -v`
Expected: PASS, the new pygents tests and every pre-existing yaml resume test (spec test 14 for this file).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): resume a pygents subtask from its newest checkpoint"
```

---

### Task 6: `am resume --engine`, and the kill-in-plan e2e

**Files:**
- Modify: `src/agent_manager/cli.py:1022-1079` (add `_check_engine` above `_check_run_targets`; `_check_run_targets` calls it)
- Modify: `src/agent_manager/cli.py:1481-1524` (the `resume` command)
- Modify: `tests/e2e/test_milestone_run.py:14-20` (imports) and append
- Test: `tests/test_cli.py` (append to the card 02890d5d section), `tests/e2e/test_milestone_run.py`

**Interfaces:**
- Consumes: `resume_run(..., engine=...)` (Task 5); test helpers `_crash_pygents`, `_plant_changed_digest`, `_resume_state`, `_Forbidden`, `runner` (all in `tests/test_cli.py`).
- Produces: `cli._check_engine(engine: str) -> None` (raises `typer.BadParameter` with `--engine must be one of yaml, pygents, got '<value>'`, `param_hint="'--engine'"`); `resume` accepts `--engine` (default `yaml`) and passes `engine=` to `resume_run` always. e2e helpers in `tests/e2e/test_milestone_run.py`: `PREFIX`, `VERIFY`, `_Killed`, `_attempt_of(stdout_path) -> tuple[str, str]`, `_latest_run_id(root) -> str`, `_kill_after(monkeypatch, card_id, phase)`.

- [ ] **Step 1: Write the failing CLI tests**

Append to `tests/test_cli.py`:

```python
@pytest.mark.parametrize("value", ["bogus", "PYGENTS", ""])
def test_resume_with_a_bad_engine_is_a_usage_error_that_starts_nothing(
    tmp_path, monkeypatch, value
):
    """Spec test 9 and Review Focus 5: exit 2, nothing on stdout, no case folding."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(cli, "resume_run", _Forbidden("resume_run"))

    result = runner.invoke(
        cli.app,
        [
            "resume",
            "20260923T140506Z-cbe34d00",
            "--repo-dir",
            str(tmp_path),
            "--engine",
            value,
        ],
    )

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "--engine must be one of yaml, pygents" in result.output


@pytest.mark.parametrize(
    "extra, passed",
    [((), "yaml"), (("--engine", "yaml"), "yaml"), (("--engine", "pygents"), "pygents")],
)
def test_the_engine_reaches_resume_run(tmp_path, monkeypatch, extra, passed):
    """No git or brd: `resume_run` is replaced. Without `--engine` it gets `yaml`."""
    seen: list[str] = []

    def fake_resume_run(run_id, **kwargs):
        seen.append(kwargs["engine"])
        return {"run_id": run_id, "status": "done"}

    monkeypatch.setattr(cli, "resume_run", fake_resume_run)

    result = runner.invoke(
        cli.app,
        ["resume", "20260923T140506Z-cbe34d00", "--repo-dir", str(tmp_path), *extra],
    )

    assert result.exit_code == 0, result.output
    assert seen == [passed]


@requires_git
@requires_brd
def test_a_pygents_resume_across_a_workflow_change_is_an_envelope_at_exit_three(
    project, cards, monkeypatch
):
    """Spec test 6 at the command: `ok: false`, exit 3, nothing written."""
    run_id = _crash_pygents(project, cards, "plan")
    _plant_changed_digest(project, run_id, cards["subtask"])
    before = _resume_state(project)
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = runner.invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(project), "--engine", "pygents"]
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CheckpointMismatchError"
    assert "workflow changed since checkpoint" in envelope["error"]["message"]
    assert _resume_state(project) == before
```

- [ ] **Step 2: Write the failing e2e test**

In `tests/e2e/test_milestone_run.py`, replace the import block (lines 14-20) with:

```python
import json
import subprocess
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, models, orchestrate, store
from agent_manager.harness import launcher
```

(`threading`, `dataclass` and `orchestrate` are used by Task 7's test in this module.)

Append to `tests/e2e/test_milestone_run.py`:

```python
# ── pygents resume and relaunch from checkpoints (card 02890d5d) ─────────────

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: the board fixtures derive their branches with it."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""


class _Killed(BaseException):
    """The manager process dying mid-phase. A plain `BaseException`, so neither
    the engine nor `CliRunner` swallows it, and not `KeyboardInterrupt`, which
    asyncio re-raises out of the event loop before the engine unwinds."""


def _attempt_of(stdout_path: Path) -> tuple[str, str]:
    """(card id, phase) of the attempt a launch belongs to.

    `paths.attempt_dir` is `<run dir>/<card>/<phase>.<n>` and the dispatcher
    hands the launcher `<attempt dir>/stdout.log`, so the launch names its own
    attempt; the fake is never asked.
    """
    attempt = stdout_path.parent
    return attempt.parent.name, attempt.name.rsplit(".", 1)[0]


def _latest_run_id(root: Path) -> str:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _kill_after(monkeypatch, card_id: str, phase: str) -> None:
    """Kill the manager once, right after `card_id`'s `phase` launch returns.

    Test scaffolding in the manager process: `cli.default_runner_factory`
    reads `cli.run_direct` at call time, so the real launcher still spawns the
    real fake, which writes its result and logs the phase as always. Raising
    after it returns and before the dispatcher records the outcome leaves the
    attempt and the phase `started`, the crash signature a real kill leaves.
    One-shot, so the resume launches through the real launcher.
    """
    real = launcher.run_direct
    armed = {"on": True}

    def killing(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        outcome = real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )
        if armed["on"] and _attempt_of(stdout_path) == (card_id, phase):
            armed["on"] = False
            raise _Killed(f"killed after the {phase} launch of {card_id} returned")
        return outcome

    monkeypatch.setattr(cli, "run_direct", killing)


def test_a_pygents_run_killed_in_plan_resumes_without_redispatching_explore_or_spec(
    milestone_board, fake_claude_bin, read_fake_log, monkeypatch
):
    """Spec test 12: explore, spec and validate_spec are dispatched once in
    total, plan twice (the killed attempt and the resumed one)."""
    root = milestone_board["root"]
    a1 = milestone_board["subtasks"]["A"][0]
    _kill_after(monkeypatch, a1, "plan")
    invoke = CliRunner().invoke

    with pytest.raises(_Killed):
        invoke(
            cli.app,
            [
                "run",
                "--card",
                a1,
                "--repo-dir",
                str(root),
                "--base-branch",
                "main",
                "--branch-prefix",
                PREFIX,
                "--engine",
                "pygents",
                "--verify",
                VERIFY,
            ],
        )
    run_id = _latest_run_id(root)
    assert Counter(entry["phase"] for entry in read_fake_log(run_id)) == {
        "explore": 1,
        "spec": 1,
        "validate_spec": 1,
        "plan": 1,
    }

    result = invoke(
        cli.app,
        [
            "resume",
            run_id,
            "--repo-dir",
            str(root),
            "--engine",
            "pygents",
            "--verify",
            VERIFY,
        ],
    )

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["status"] == "done", data
    assert data["resumed_from"] == "plan"
    assert data["discarded_attempts"] == [{"phase": "plan", "n": 1}]
    assert Counter(entry["phase"] for entry in read_fake_log(run_id)) == {
        "explore": 1,
        "spec": 1,
        "validate_spec": 1,
        "plan": 2,
        "validate_plan": 1,
        "implement": 1,
        "review": 1,
    }
    assert board.show(a1, repo_dir=root).status == "done"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "resume_with_a_bad_engine or engine_reaches_resume_run or workflow_change_is_an_envelope" -v`
Expected: FAIL — the bad-engine test's output is Click's `No such option: --engine` (not `--engine must be one of`), the others exit 2 instead of 0/3.

Run: `uv run pytest tests/e2e/test_milestone_run.py::test_a_pygents_run_killed_in_plan_resumes_without_redispatching_explore_or_spec -v`
Expected: FAIL at `assert result.exit_code == 0` with exit 2 (`No such option: --engine`).

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/cli.py`, directly above `def _check_run_targets(` add:

```python
def _check_engine(engine: str) -> None:
    """Refuse an `--engine` outside `ENGINES` as a usage error (Typer's exit 2).

    Exact and without case folding. Shared by `run` and `resume` (card
    02890d5d); `--engine` is typed `str` on both because Typer 0.27.2 cannot
    take a `Literal` annotation.
    """
    if engine not in ENGINES:
        raise typer.BadParameter(
            f"--engine must be one of {', '.join(ENGINES)}, got {engine!r}",
            param_hint="'--engine'",
        )
```

In `_check_run_targets`, replace its last block (lines 1075-1079):

```python
    if engine not in ENGINES:
        raise typer.BadParameter(
            f"--engine must be one of {', '.join(ENGINES)}, got {engine!r}",
            param_hint="'--engine'",
        )
```

with:

```python
    _check_engine(engine)
```

Replace the `resume` command (lines 1481-1524) with:

```python
@app.command("resume")
def resume(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run to pick back up."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help="Proceed even when no verification suite is available (§12's opt-out).",
    ),
    verify: list[str] = typer.Option(
        [],
        "--verify",
        help=(
            "One whole verification command, repeatable. The run record does not "
            "carry the suite, so a resume is told it the way a fresh run was."
        ),
    ),
    engine: str = typer.Option(
        "yaml",
        "--engine",
        help=(
            "Which engine resumes the subtask: `yaml` (the default) re-runs the "
            "phase the run died in; `pygents` continues from the subtask's newest "
            "checkpoint, a parked (`stopped`) subtask included."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Re-run the phase a killed run died in, and drive the subtask to the end.

    No `--base-branch` and no `--branch-prefix`: both were decided when the run
    started and are recorded on the subtask (§9). `--allow-no-verification` and
    `--verify` are offered because `models.RunConfig` carries neither the opt-out
    nor the suite commands, so both mean the same thing here as they do on a
    fresh `run`. `--engine` (card 02890d5d) is checked before anything is read;
    on `pygents` the checkpoint carries the gate context the run started with.
    """
    _check_engine(engine)
    try:
        payload = resume_run(
            run_id,
            repo_dir=repo_dir,
            allow_no_verification=allow_no_verification,
            commands=list(verify),
            engine=cast(Engine, engine),
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    # Strict equality on purpose: a `stopped` walk (addendum P4) is not an
    # escalation, so it exits 0 with an ok envelope.
    if payload["status"] == "escalated":
        raise typer.Exit(EXIT_ESCALATED)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "engine or resume" -v`
Expected: PASS, including the existing `test_a_bad_engine_is_a_usage_error_that_starts_nothing` (same message via `_check_engine`) and `test_a_resumed_walk_that_stops_is_ok_true_and_exit_zero` (its fake takes `**kwargs`).

Run: `uv run pytest tests/e2e/test_milestone_run.py -v`
Expected: PASS, the new test and both engine-parametrized existing tests.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py tests/e2e/test_milestone_run.py
git commit -m "feat(cli): add --engine to resume and prove a pygents kill-in-plan resume end to end"
```

---

### Task 7: Relaunch continues an open checkpoint on pygents

**Files:**
- Modify: `src/agent_manager/orchestrate.py:39` (import), `202-223` (`Driver`), `393-447` (`run_story_lane`)
- Modify: `tests/e2e/test_milestone_run.py:241-242` (stale comment)
- Test: `tests/test_orchestrate.py` (append at the end), `tests/e2e/test_milestone_run.py` (append)

**Interfaces:**
- Consumes: `cli.continuable_checkpoint(store, card_id)` (Task 4), `drive_subtask(..., resume_from=...)` (Task 3), `store_module.Checkpoint`.
- Produces: `orchestrate.Driver.__call__(..., engine: cli.Engine = "yaml", resume_from: Checkpoint | None = None)`. `run_story_lane` passes `resume_from=<row>` to the driver only when `engine == "pygents"` and `cli.continuable_checkpoint` returned a row; otherwise no `resume_from` keyword at all. A lookup exception is handled like any other exception inside the lane's `try` (the subtask escalates).

- [ ] **Step 1: Write the failing Steps tests**

In `tests/test_orchestrate.py`, add to the imports (after line 37):

```python
from agent_manager.workflow import task as task_workflow
```

Append to `tests/test_orchestrate.py`:

```python
# ── relaunch continues an open checkpoint (card 02890d5d) ───────────────────

_ABSENT = object()
"""What `CheckpointDriver` records when the lane passed no `resume_from` at all."""

EARLIER = datetime(2026, 9, 24, 11, 0, 0, tzinfo=timezone.utc)
"""When the earlier run saved its checkpoints: before `STARTED_AT`."""


@dataclass
class CheckpointDriver(FakeDriver):
    """`FakeDriver` that also takes `resume_from` and records it per card."""

    resumed: dict[str, Any] = field(default_factory=dict)

    def __call__(self, *, resume_from: Any = _ABSENT, **kwargs: Any) -> cli.SubtaskDrive:
        self.resumed[kwargs["card"].id] = resume_from
        return super().__call__(**kwargs)


def _plant(
    project: Path,
    run_id: str,
    card_id: str,
    reason: str,
    *,
    digest: str | None = None,
    queue: tuple[str, ...] = ("implement",),
    minute: int = 0,
) -> store_module.Checkpoint:
    """One checkpoint row of `TASK` for `card_id`, saved by an earlier run."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        return opened.save_checkpoint(
            card_id,
            workflow=task_workflow.TASK.name,
            digest=task_workflow.TASK.digest() if digest is None else digest,
            reason=reason,
            agent={
                "current_turn": None,
                "queue": [{"kwargs": {"phase": name, "loop": 0}} for name in queue],
            },
            saved_at=EARLIER.replace(minute=minute),
        )
    finally:
        opened.close()


@requires_git
@requires_brd
def test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh(
    project,
):
    """Spec test 10: a1 parked under this TASK continues; a2's row is from
    another TASK, b1's newest row is `done`, b2's is a phase escalation with no
    turn left: all three start fresh, and the run does not raise."""
    shape = _milestone(project, {"A": 2, "B": 2})
    a1, a2 = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    earlier = cli.mint_run_id(shape["milestone"], EARLIER)
    parked = _plant(project, earlier, a1, "parked")
    _plant(project, earlier, a2, "parked", digest="saved-under-another-task")
    _plant(project, earlier, b1, "parked", minute=1)
    _plant(project, earlier, b1, "done", queue=(), minute=2)
    _plant(project, earlier, b2, "escalated", queue=())
    driver = CheckpointDriver()

    result = _run(project, shape["milestone"], driver, engine="pygents")

    assert result["done"] is True
    assert result["completed"] == [a1, a2, b1, b2]
    got = driver.resumed[a1]
    assert got is not _ABSENT
    assert (got.run_id, got.card_id, got.seq, got.reason) == (earlier, a1, parked.seq, "parked")
    assert driver.resumed[a2] is _ABSENT
    assert driver.resumed[b1] is _ABSENT
    assert driver.resumed[b2] is _ABSENT


@requires_git
@requires_brd
def test_a_yaml_relaunch_looks_up_no_checkpoint(project, monkeypatch):
    """Spec test 10, yaml half. A guard: it passes before this task and must
    keep passing after it."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    _plant(project, cli.mint_run_id(shape["milestone"], EARLIER), a1, "parked")
    looked: list[str] = []
    monkeypatch.setattr(
        cli, "continuable_checkpoint", lambda store, card_id: looked.append(card_id)
    )
    driver = CheckpointDriver()

    result = _run(project, shape["milestone"], driver)

    assert result["done"] is True
    assert looked == []
    assert driver.resumed[a1] is _ABSENT


@requires_git
@requires_brd
def test_a_checkpoint_lookup_that_fails_escalates_that_subtask(project, monkeypatch):
    """Review Focus 4: the lookup runs inside the lane's `try`, so a broken
    store escalates the subtask it was for and never crashes the run."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]

    def broken(store, card_id):
        raise RuntimeError("checkpoints table unreadable")

    monkeypatch.setattr(cli, "continuable_checkpoint", broken)
    driver = CheckpointDriver()

    result = _run(project, shape["milestone"], driver, engine="pygents")

    assert result["escalated"] is True
    assert result["subtask"] == a1
    assert result["detail"] == "RuntimeError: checkpoints table unreadable"
    assert driver.calls == []
```

- [ ] **Step 2: Write the failing e2e test**

Append to `tests/e2e/test_milestone_run.py`:

```python
REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""Must equal the conftest's `FAKE_REVIEW_FAIL_MARKER` (and `fake_claude.REVIEW_FAIL_MARKER`)."""

WAIT = 120.0
"""Seconds a held launch waits for the other lane before giving up. Generous:
it only bounds a broken run, a healthy one never waits this long."""


def _phases_in(entries, worktree: Path) -> list[str]:
    """The phases the fake ran in `worktree`, in log order."""
    return [entry["phase"] for entry in entries if Path(entry["cwd"]).resolve() == worktree]


def _hold_b1_in_plan_until_a1_escalates(monkeypatch, board_shape) -> dict[str, bool]:
    """Make the first launch park b1 before `validate_plan`, deterministically.

    Test scaffolding in the manager process, never seen by the fake. a1's
    `review` launch waits until b1 is inside `plan`; b1's `plan` launch waits
    until the run's stop is set, which a1's review failure (the review-fail
    marker) does. When b1's plan returns, the stop is set, so the next
    `BEFORE_TURN` parks b1 before `validate_plan`. `RunStop` is captured by a
    subclass because `run_milestone` builds it at call time. Returns the
    switch that turns the hold off for the relaunch.
    """
    stops: list[orchestrate.RunStop] = []

    @dataclass
    class CapturedStop(orchestrate.RunStop):
        def __post_init__(self) -> None:
            stops.append(self)

    monkeypatch.setattr(orchestrate, "RunStop", CapturedStop)
    (a1,) = board_shape["subtasks"]["A"]
    (b1,) = board_shape["subtasks"]["B"]
    b1_in_plan = threading.Event()
    hold = {"on": True}
    real = launcher.run_direct

    def held(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        if hold["on"]:
            attempt = _attempt_of(stdout_path)
            if attempt == (b1, "plan"):
                b1_in_plan.set()
                if not stops[-1].event.wait(WAIT):
                    raise AssertionError("a1's escalation never set the run's stop")
            elif attempt == (a1, "review"):
                if not b1_in_plan.wait(WAIT):
                    raise AssertionError("b1 never reached plan")
        return real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )

    monkeypatch.setattr(cli, "run_direct", held)
    return hold


def _pygents_milestone(root: Path, milestone: str):
    return CliRunner().invoke(
        cli.app,
        [
            "run",
            "--milestone",
            milestone,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            PREFIX,
            "--engine",
            "pygents",
            "--verify",
            VERIFY,
            "--max-concurrent",
            "2",
        ],
    )


def test_a_pygents_relaunch_continues_the_parked_card_from_its_checkpoint(
    two_story_board, fake_claude_bin, read_fake_log, monkeypatch
):
    """Spec test 13, plus Review Focus 1: the parked b1 continues at
    validate_plan and dispatches nothing it had finished; the escalated a1
    (newest row `escalated`, no turn left) starts fresh at explore."""
    root = two_story_board["root"]
    milestone = two_story_board["milestone"]
    stories = two_story_board["stories"]
    branches = two_story_board["branches"]
    (a1,) = two_story_board["subtasks"]["A"]
    (b1,) = two_story_board["subtasks"]["B"]
    a1_worktree = cli.worktree_for(root, branches[a1]).resolve()
    b1_worktree = cli.worktree_for(root, branches[b1]).resolve()
    marker = root / ".git" / REVIEW_FAIL_MARKER
    marker.write_text(f"{branches[a1]}\n", encoding="utf-8")
    hold = _hold_b1_in_plan_until_a1_escalates(monkeypatch, two_story_board)

    first = _pygents_milestone(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["subtask"] == a1, stopped
    assert stopped["failed_phase"] == "review"
    assert stopped["stopped"] == [
        {"story": stories["B"], "subtask": b1, "before_phase": "validate_plan"}
    ]
    assert _phases_in(read_fake_log(stopped["run_id"]), b1_worktree) == [
        "explore",
        "spec",
        "validate_spec",
        "plan",
    ]

    marker.unlink()
    hold["on"] = False
    second = _pygents_milestone(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    finished = _envelope(second)
    assert finished["done"] is True, finished
    assert finished["run_id"] != stopped["run_id"]
    assert finished["completed"] == [a1, b1]
    second_entries = read_fake_log(finished["run_id"])
    assert _phases_in(second_entries, b1_worktree) == ["validate_plan", "implement", "review"]
    assert _phases_in(second_entries, a1_worktree)[0] == "explore"
    for card_id in (a1, b1, *stories.values(), milestone):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "relaunch or checkpoint_lookup" -v`
Expected: `test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh` FAILS at `assert got is not _ABSENT`; `test_a_checkpoint_lookup_that_fails_escalates_that_subtask` FAILS with `KeyError: 'escalated'` (the run finished `done`); `test_a_yaml_relaunch_looks_up_no_checkpoint` passes (guard).

Run: `uv run pytest tests/e2e/test_milestone_run.py::test_a_pygents_relaunch_continues_the_parked_card_from_its_checkpoint -v`
Expected: FAIL at the second-run assertion on b1: `['explore', 'spec', 'validate_spec', 'plan', 'validate_plan', 'implement', 'review'] != ['validate_plan', 'implement', 'review']` (b1 restarts from its first phase).

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/orchestrate.py`, change line 39:

```python
from agent_manager.store import Checkpoint, Store
```

In `Driver.__call__` (lines 209-223), add the keyword after `engine`:

```python
        engine: cli.Engine = "yaml",
        resume_from: Checkpoint | None = None,
    ) -> cli.SubtaskDrive: ...
```

and append to the `Driver` docstring:

```python
    `resume_from` (card 02890d5d) is passed only when a pygents relaunch found
    a checkpoint to continue, so a driver written before it keeps working.
```

In `run_story_lane`, replace the block from `if position == 0:` through the `drive(...)` call (lines 433-447) with:

```python
            if position == 0:
                store.record_story(story_row.model_copy(update={"status": "started"}))
            # Relaunch continuation (card 02890d5d): on pygents a card whose
            # open checkpoint was saved under this `TASK` continues from it; a
            # changed workflow, a closed card or no row starts it fresh, with
            # no error. The keyword is passed only when there is a row, so a
            # driver that predates it keeps working. Yaml looks nothing up.
            extra: dict[str, Any] = {}
            if engine == "pygents":
                checkpoint = cli.continuable_checkpoint(store, subtask.id)
                if checkpoint is not None:
                    extra["resume_from"] = checkpoint
            result = drive(
                store=store,
                run_id=run_id,
                card=card,
                parent=parent,
                subtask=row,
                repo_dir=root,
                commands=list(commands),
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                should_stop=stop.event.is_set,
                engine=engine,
                **extra,
            )
```

Append to the `run_story_lane` docstring, before the closing `"""`:

```python
    On `engine="pygents"` each subtask's open checkpoint is looked up first
    (`cli.continuable_checkpoint`), inside the same `try`, and handed to the
    driver as `resume_from` when it can be continued.
```

In `tests/e2e/test_milestone_run.py`, replace the comment at lines 241-242:

```python
    # On pygents too, the relaunch re-drives b1 from its first phase (no
    # checkpoint continuation here -- that is card 02890d5d's).
```

with:

```python
    # On pygents too, the relaunch re-drives b1 from its first phase: its
    # newest checkpoint is `escalated` with no turn left, which
    # `cli.continuable_checkpoint` never continues (card 02890d5d).
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: PASS, including `test_run_milestone_hands_its_engine_to_every_driver_call_and_to_integrate` on pygents (no checkpoints exist, so `FakeDriver` gets no `resume_from`) and `test_the_engine_selecting_modules_never_import_pygents`.

Run: `uv run pytest tests/e2e/test_milestone_run.py tests/e2e/test_parallel_milestone.py -v`
Expected: PASS on both engines; on pygents `test_a_relaunch_after_the_escalation_finishes_and_skips_done_subtasks` now continues the parked B card, and its assertions hold unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py tests/e2e/test_milestone_run.py
git commit -m "feat(orchestrate): continue a card's open checkpoint on a pygents relaunch"
```

---

### Task 8: Whole-suite verification

**Files:** none changed unless a failure points at one of the files above.

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: a green default suite (spec test 14).

- [ ] **Step 1: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, every test, `tests/e2e` included on both engines (the parity fixtures `engine_parity` compare yaml and pygents payloads of the same scenario and must still agree).

- [ ] **Step 2: Confirm the import rule by hand**

Run: `uv run pytest tests/test_orchestrate.py::test_the_engine_selecting_modules_never_import_pygents -v`
Expected: PASS (`cli.py` and `orchestrate.py` import no `pygents` module; `pending_phase` lives in `runtime/engine.py`).

- [ ] **Step 3: Commit any fix**

If Step 1 passed first time there is nothing to commit. If it failed, go back to the task whose files the failure names, fix it there, re-run that task's "run the tests to verify they pass" step and then Step 1 again, and commit the fix:

```bash
git add src/agent_manager tests
git commit -m "fix: make the full suite green after card 02890d5d"
```
