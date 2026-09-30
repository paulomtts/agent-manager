# Subtask d147d97a — Prove pause, resume and cancel under the fake claude

Parent story: 49e7aa15 "Prove and document live control" (milestone bdc5838b). Narrows Task 3.1 of `docs/superpowers/plans/2026-09-27-live-control.md` and §7 "End to end" of `docs/superpowers/specs/2026-09-27-live-control-design.md`. Both documents live on branch `docs/live-control` (worktree `.claude/worktrees/docs-live-control`, commit 7cf1263), not on `master`.

## Base branch

The code under test (`control.py`, `StopSignal.request`/`.requested`, `am pause`/`am cancel`, `request_control`, the `run_milestone` Lease + `controlled` wiring, `controlled_payload`, and the cancelled/live refusals in `resume_run`/`resumable_milestone_run`) is not on `master`. It is on `m9/task-honour-pause-and-cancel-9f5467e3`, which sits on top of the earlier m9 task branches. This subtask's branch must be based on that tip, or on wherever the workflow stacks m9 work. The implementer checks this before writing anything. Read the code as built first. If a name differs from the spec (for example `latest_run_id`, the `effective` key or the `paused`/`cancelled` payload keys), follow the code and say so in the commit or PR notes.

## Scope

- Create one new file, `tests/e2e/test_live_control.py`, containing exactly the two tests below. No production code changes are expected.
- Reuse the board fixtures and fake-`claude` wiring in `tests/e2e/conftest.py` without modifying them, unless a change is strictly necessary.
- Follow the scaffolding pattern in `tests/e2e/test_milestone_resume.py` (`_kill_with_c1_in_plan_and_d3_in_implement`): use `monkeypatch.setattr(cli, "run_direct", ...)`, look up the real `run_direct` at call time, and block on a `threading.Event`. The difference is that this test *holds* the launch instead of killing it. A local `_hold(monkeypatch, card, phase, entered, release)` helper wraps `cli.run_direct`. When the named card's `plan` launch arrives, it sets `entered`, waits on `release`, and then calls the real `run_direct`. The hold is one-shot: it fires once only and passes every other call straight through, so nothing stays armed for `am resume` or the relaunch.
- Out of scope: any unit or integration coverage already owned by the sibling stories (`tests/test_store.py`, `tests/runtime/test_stop.py`, `tests/test_control.py`, `tests/test_orchestrate.py`, `tests/test_cli.py`). That includes the §6 refusal table, lease liveness, `--card` pause/cancel, and `am status`'s `control` key. Documentation is out of scope too; it belongs to sibling Task 3.2.

## Observable behaviour to prove

1. **Pause then resume** (`test_a_paused_milestone_resumes_with_nothing_dispatched_twice`):
   - A worker thread runs `am run --milestone <ms>` through `CliRunner`.
   - The test waits on `entered` with a bounded wait, then gets the run id from a second `store.open_db(project)` connection. That second connection is the "other process"; it is also `am pause`'s real path.
   - It runs `am pause <run_id> --repo-dir <project>` from the test thread. This must exit 0 with `data.effective == "pause"`.
   - It sets `release` and joins the worker. The run must exit 0 with `data.paused is True`, and `am status <run_id> --repo-dir <project>` must report `data["run"]["status"] == "stopped"` (the run's own status sits under the `run` key, not at the payload's top level; see `cli.status_payload`).
   - It records the fake-invocation counts, then runs `am resume <run_id> --repo-dir <project> --verify <VERIFY>`. This must exit 0 and its data must contain `integrated`.
   - Comparing the counts before and after resume must show no phase dispatched twice. In particular, the held card's `plan` completes exactly once.
2. **Cancel then relaunch** (`test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch`):
   - Set up the same hold, but run `am cancel <run_id>` instead. This must exit 0.
   - After release, the run must exit 0 with `data.cancelled is True`, with no `resume`, `escalated`, `failed_phase` or `integrated` key. `am status <run_id> --repo-dir <project>` must report `data["run"]["status"] == "cancelled"`.
   - `am resume <run_id>` must fail with exit 3 and the `{"ok": false, "error": {...}}` envelope (`NotResumableError`).
   - A fresh `am run --milestone <ms>` must dispatch the held card again from `explore`, as shown by the fake's invocation record. The cancelled run's parked checkpoint must not be continued.

## Error paths covered

- `am resume` of a cancelled run gives exit 3 (C9).
- A control never produces `escalated` or a `failed_phase`, and a paused or cancelled invocation never runs Integrate (C6). The tests assert this on the controlled payloads.

## Constraints (from the story, verbatim intent)

- No sleeps to prove ordering. Use `threading.Event` with bounded waits, and always set `release` in a `finally` so a failed assertion cannot hang the thread.
- The only control channel is the per-project SQLite. Do not add sockets, signals, or runtime dependencies.
- The CLI envelope stays unchanged.
- Run `uv run pytest` and get the whole suite green, `tests/e2e` included. Any bug the tests expose is first reproduced by its own failing test (in the appropriate existing test module) and then fixed in this task.
- Make one commit: `test(e2e): pause, resume and cancel a milestone under the fake claude`. Use the `m9` branch prefix. Push nothing, and never move the base branch.

## Test list and tier

The tier follows the repo's placement rule. `pyproject.toml` `addopts` has `-m "not e2e"`. Only tests that drive a real `claude` subprocess (`tests/e2e/test_real_harness*.py`) are marked `@pytest.mark.e2e`. Fake-`claude` end-to-end tests go in `tests/e2e/` unmarked and run in the default suite.

| Test | File | Tier |
|---|---|---|
| `test_a_paused_milestone_resumes_with_nothing_dispatched_twice` | `tests/e2e/test_live_control.py` | e2e directory, default suite, **unmarked** (fake `claude`) |
| `test_a_cancelled_milestone_is_refused_by_resume_and_relaunched_from_scratch` | `tests/e2e/test_live_control.py` | e2e directory, default suite, **unmarked** (fake `claude`) |
| (only if a bug is found) a regression test for that bug | the existing unit/integration module that owns the faulty code (`tests/test_control.py`, `tests/test_orchestrate.py`, `tests/test_cli.py`, `tests/test_store.py`, or `tests/runtime/test_stop.py`) | default suite, unmarked |

No `pytestmark = pytest.mark.e2e` in the new file.
