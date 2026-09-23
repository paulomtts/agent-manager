# Subtask 5524ae72 — `agent-manager resume <run-id>`

Parent story 1a46ab5a ("The CLI: run, status, logs, resume"), milestone 352e955b. Siblings `run --card`, `status`/`runs` and `logs` are done; this card adds the fourth verb. Source of truth for the semantics: design spec §9 ("Resume semantics", lines 370-386) and §10's grammar line `agent-manager resume <run-id>`.

## Scope

Owns `src/agent_manager/cli.py` only. No change to `store.py`, `engine.py`, `models.py`, `dispatch.py` or `workflow/builtin/task.yaml`, and no change to the observable behaviour of `run`, `status`, `runs` or `logs`.

In particular, "discarding" an in-flight attempt is done with the existing `Store.record_attempt`, re-recording the orphan `Attempt` with a terminal `harness_error` status. That keeps the journal-before-row ordering, needs no new `EventKind`, and leaves `replay`/`rebuild_from_journal` unchanged — the attempt is just one more `attempt_upsert` keyed by `(phase, n)`. No new store method, no delete path, no journal schema change.

Out of scope, explicitly: `retry`, `cancel`, `watch`, `--harness`, `--dry-run`, `--workflow`, milestone orchestration, non-Claude harnesses, and persisting new fields on `RunConfig`.

## Behaviour

`agent-manager resume <RUN_ID> [--repo-dir .] [--allow-no-verification] [--pretty]`, plus a testable `resume_run(run_id, *, repo_dir, allow_no_verification=False, commands=(), runner_factory=None, clock=_utcnow) -> dict[str, Any]` mirroring `run_card` (cli.py:458) so the tests drive the function and the Typer command stays a thin wrapper like `run` (cli.py:585).

Sequence:

1. `resolve_repo_dir(--repo-dir)`, then load the run out of the project projection the way `logs_for` does (`store_module.open_db` + `store_module.load_run`, closed on every path) — never `Store.open`, which would mint a run directory for a run that does not exist. Missing run → `UnknownRunError`, with `logs_for`'s wording.
2. Pick the subtask: the single `SubtaskRun` in the run tree whose status is `started`. Nothing `started` → `NotResumableError` (new `CliError` subclass) naming the subtask's actual status: a `done` run has nothing to resume, and an `escalated` run is `retry`'s job, not this card's. More than one `started` subtask → `NotResumableError` too: multi-subtask runs are the milestone runner's and this command drives one subtask, the way `run --card` does.
3. Re-fetch the cards with `board.show` for the subtask's `card_id` and its story's `card_id` — `engine.run_subtask` needs real `models.Card` values for `card`/`parent_story`, and the projection stores only ids. Board errors ride `HANDLED` as they already do in `run_card`.
4. Load the workflow with `load_builtin(run.workflow)` (`run` only ever writes `task`; anything else surfaces as the loader's own `WorkflowLoadError`).
5. Open `Store.open(root, run.id)` — the same run id, so journal lines continue the existing sequence and `dispatch.next_attempt` keeps numbering attempt directories forward instead of overwriting the crashed one.
6. Discard in-flight attempts: for every phase of that subtask, every `Attempt` with `status == "started"` is re-recorded via `store.record_attempt(story_id, card_id, phase.name, attempt.model_copy(update={"status": "harness_error"}))`. Journal first, row after, as `Store.record_attempt` already does. Attempt directories on disk are left alone — their prompt/stdout are the evidence `logs` prints.
7. Choose the restart phase: the interrupted phase, then backed off over result dependencies (see below).
8. Re-record run/story/subtask as `started` (`record_run`/`record_story`/`record_subtask`, same values with the status forced) before the walk, for the same reason `run_card` writes them first: a resume that dies on its first phase must still be visible to `status`.
9. Build the runner through the `RunnerFactory` seam (`default_runner_factory` unless injected, cli.py:393-435) with the existing `run_id`/`story_id`/`card_id`, and call `engine.run_subtask(..., subtask=<the stored SubtaskRun>, repo_dir=root, commands=commands, card=card, parent_story=parent, extra_context=gate_context(commands, allow_no_verification), agent_runner=runner, start_phase=<chosen phase>)`. Branch, base branch and worktree path come from the stored `SubtaskRun`, not from flags: that is what §9 means by the run recording what it was started with.
10. Record the final status on run, story and subtask exactly as `run_card` does, close the store in a `finally`, and return the payload.

### Choosing the restart phase

Two pure helpers in `cli.py`, both operating on a `models.SubtaskRun` plus the loaded `Workflow`:

- **Interrupted phase.** The first phase of the workflow that is recorded `started` (no terminal status) — the crash signature §9 describes. If no phase is `started` (the process died between phases), it is the first workflow phase not recorded `done`, except that `skip_to` phases are never recorded (engine.py:432 only appends to the in-memory `summary.skipped`), so when the first not-done phase lies after a recorded-`done` phase carrying `skip_to` (`plan_check`), the restart phase is that `skip_to` phase, letting its `when` re-decide the skip instead of re-running `spec`/`plan` over a validated plan. If every phase is `done`, `NotResumableError`, because step 2's `started` subtask with a complete phase list means only the final status write was lost and re-running `mark_done` would be a fresh run, not a resume.
- **Back-off over result dependencies.** Earlier phases' results live only in the in-memory binding table `engine.run_subtask` builds (`_bind_result`, engine.py:439) and are not replayed from the journal. So a restart phase whose declared `inputs` name an earlier *phase* (rather than a key `subtask_context`/`_document_paths`/`gate_context` supplies) must be backed off to that producing phase. In `workflow/builtin/task.yaml` the only such edge is `spec`'s `inputs: [card, explore]`, so an interrupted `spec` restarts at `explore`; `spec_path`/`plan_path` are computed per subtask by `_document_paths` and create no edge, and `plan_check`'s `when` reads only its own result. The helper derives this from the document rather than hard-coding the pair, and backs off transitively. Re-running `explore` is safe under §9: every phase is idempotent or re-entrant (`worktree.ensure` never resets, `rollup.set_status` is idempotent, `verify.run_suite` is read-only, `spec`/`plan` overwrite their file, `implement` re-enters through the Plan-Hash trailer).

### Configuration not recoverable from the record

`models.RunConfig` carries `max_concurrent_stories`, `dry_run`, `launcher`, `harness_map` — not the suite commands and not `--allow-no-verification`. Rather than grow the model (out of scope, and it would change what `run` journals), `resume` accepts `--allow-no-verification` itself and, like `run`, discovers no suite: `commands` defaults to empty and `gate_context(commands, allow_no_verification)` is built the same way. The flag's absence therefore means the same on resume as on a fresh `run`.

### Output

The `{ok, data}` envelope, one line by default and indented under `--pretty`, via the existing `ok_envelope`/`error_envelope`/`render`. The payload is `run_card`'s keys (`run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`) plus two resume-specific ones: `resumed_from` (the phase the walk restarted at) and `discarded_attempts` (a list of `{"phase": ..., "n": ...}` for the orphans closed in step 6). Warnings are collected the same way, summary warnings plus `getattr(runner, "warnings", [])`.

## Error paths

- Unknown run id, or a run absent from this project's projection → `UnknownRunError`, `ok: false`, exit `EXIT_ERROR` (3).
- Nothing to resume (no `started` subtask, subtask `done`/`escalated`, or every phase `done`), or more than one `started` subtask → `NotResumableError` (new `CliError` subclass, so it rides `HANDLED`), exit 3, message naming the status found and pointing at `agent-manager status <run-id>`.
- `--repo-dir` not a directory → `RepoDirError`, exit 3, as everywhere else.
- Board failure re-fetching the cards, workflow load failure, `EngineError`, `ValueError` → exit 3 via the existing `HANDLED` tuple (cli.py:569). Nothing new is added to `HANDLED` beyond what the new `CliError` subclass inherits.
- A resumed walk that escalates prints an `ok: true` envelope with `status == "escalated"` and exits `EXIT_ESCALATED`, exactly as `run` does.
- Refusals must write nothing: no `runs/<run-id>` directory, no journal, no rows — the store is only opened after the run, subtask and cards have all resolved.

## Tests

Tier rule is design §14 (lines 477-492) as applied in `tests/test_cli.py`'s module docstring. Everything below lands in `tests/test_cli.py`; there are no `tests/test_store.py` or `tests/test_engine.py` additions, because no store or engine code changes.

Unit tier (hand-built `models` objects, no clock, no filesystem, no subprocess — the `_pure_run`/`_pure_dispatch` style at test_cli.py:95-115):

1. Interrupted-phase selection returns the phase recorded `started` with a non-terminal attempt.
2. Interrupted-phase selection with no `started` phase returns the first phase not recorded `done` (crash between phases).
3. Interrupted-phase selection with every phase `done` reports "nothing to resume" (the condition `resume_run` turns into `NotResumableError`).
4. Back-off: an interrupted `spec` yields `explore`, because `spec` declares `explore` as an input; an interrupted `implement` yields `implement`, because its inputs are all context-supplied.
5. Orphan selection lists exactly the attempts recorded `started`, across phases, and leaves terminal ones alone.

Engine tier (fake runner injected via `runner_factory=lambda **kw: ...`, on Steps-tier fixtures: `project`, `cards`, `projection`, `requires_git`, `requires_brd`, `XDG_DATA_HOME` → `tmp_path`; never a real harness):

6. **The §14 crash-and-resume test.** Drive `cli.run_card` with a fake runner that, on the `implement` phase, records an attempt `started` through the real store and then raises `KeyboardInterrupt` — a `BaseException`, so it escapes `engine.run_subtask`'s `except Exception` the way a killed process does, leaving the subtask `started`, the phase `started` and an attempt `started`. Then call `resume_run` with a well-behaved fake runner and assert: payload `status == "done"`, `resumed_from == "implement"`, `discarded_attempts` names that attempt, and `_attempt_rows(project)` (test_cli.py:1872) holds no row with status `started`.
7. Resume numbers new attempts forward: after the crash-and-resume of test 6, the crashed attempt's row and directory still exist and the resumed dispatch used a higher `n` (no attempt directory was overwritten).
8. Resume launches no harness: `cli.run_direct` and `cli.dispatch.AgentRunner` monkeypatched to raise, resume completes on the injected fake — the pattern of `test_no_harness_is_ever_launched` (test_cli.py:1262).
9. Resume of a run whose `spec` phase was in flight restarts at `explore`, and the fake runner is asked for `explore` before `spec` (the back-off rule observed end to end, so the binding table `spec` needs is populated).
10. CLI envelope and exits, invoked through `runner.invoke(cli.app, ["resume", ...])`: unknown run id → exit 3 with `error.type == "UnknownRunError"`; a `done` run and an `escalated` run → exit 3 with `error.type == "NotResumableError"`; a resumed walk that escalates → `ok: true`, `status == "escalated"`, exit `EXIT_ESCALATED`; `--pretty` indents the envelope.
11. Refusals write nothing: an unknown-run resume leaves the `runs` tree and the `attempts` rows byte-identical (`_runs_snapshot` / `_attempt_rows`, the style of `test_logs_writes_nothing`, test_cli.py:1883).

No end-to-end real-harness test is added here; §14 keeps that slow, opt-in and out of the default suite. Verification is `uv run pytest`.
