# 3.3 e2e_fake: detach, watch, pause, resume (card ef6e5633)

Parent story fea654ef "am run --detach". Blocked by 3.2 (aff9fdbf, done). Narrows the Testing line of `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` ("starts a detached milestone run, follows its logs and watch stream, pauses it with am pause and resumes it") to the watch stream only: `am logs --follow` does not exist yet and is a separate story.

## Base

This branch (`ami/task-3-3-e2e-fake-detach-ef6e5633`) already contains 3.2's work: `src/agent_manager/detach.py` (`RUN_LOG_NAME`, `REPORT_NAME`, `fork_detacher`) and `tests/e2e/test_detached_run.py` are present. Master (09e8b80) has no `--detach`, so this work must not be rebased onto master as it stands. The test does not use `watch --from-now` or the 2.x `am runs` fields (`lease`, `progress`), even where they are present on this branch.

## Scope

The deliverable is one new file, `tests/e2e/test_detached_pause_resume.py`, holding exactly one test marked `@pytest.mark.e2e_fake`. The marker is explicit, not left to the directory auto-mark. The file mirrors `tests/e2e/test_detached_run.py`: it uses the same `PREFIX = "m3"`, `VERIFY = "git rev-parse --verify HEAD"` (must equal conftest `VERIFY_COMMANDS[0]`), `DEADLINE`/`POLL`, `_until`, `_lease`, the `detached_pids` fixture (killpg at teardown) and the same `am run --milestone ... --detach` argv. A module docstring names the card and says that ordering comes from markers, control rows and the lease row, never sleeps.

The card adds no production code. If the test exposes a bug, report it rather than widen the card. Two candidate areas: (a) `am watch RUN_ID --follow` refusing a just-detached run because it has no journal yet; (b) the paused life's `report.json` is left in place after a foreground `am resume`. The test does not assert anything about (b).

The test does not re-assert what 3.2 already covers: the detach envelope's key set, log/report file modes, `getsid`, the `am runs` row, or the child outliving its parent.

## Observable sequence (what the test asserts, in order)

Fixtures: `milestone_board`, `fake_claude_bin`, `hold`, `am`, `spawn_am`, `am_processes`, `detached_pids`. The board is A (a1 -> a2), then B blocked by A (b1), then C blocked by B (c1). So while a1 is held, nothing else is in flight.

1. **Detach.** `hold.arm()` holds `implement`, the fake's default. Then `hold.release(a2, b1, c1)`. `am run --milestone M --repo-dir R --base-branch main --branch-prefix m3 --verify VERIFY --detach` exits 0 with `ok` true. Take `run_id` and `pid` from it, and add `pid` to `detached_pids`.
2. **Watch starts.** Run `spawn_am("watch", run_id, "--follow")`. Read stdout one line at a time on a daemon reader thread that feeds a `queue.Queue`. Each `get` is bounded by `DEADLINE`, so a broken run fails instead of hanging. The first line is the hello: `event == "watch"`, `schema == 1`, with `am` and `runs_dir` present and no `ok` key (it is a stream, not a refusal). `runs_dir` equals `paths.data_dir() / "runs"`. Every later line is a JournalLine (`seq`, `ts`, `run_id`, `event` in `store.EventKind`) with `run_id == run_id`.
3. **Held.** `_until(hold.held_marker(a1).exists)`. `am status run_id --repo-dir R` reports `data.run.status == "started"` and a live `data.control.lease`.
4. **Pause.** `am pause run_id --repo-dir R` exits 0. Its `data` has `run_id == run_id`, `command == "pause"`, `effective == "pause"` and `already_requested is False`.
5. **Applied before release.** Poll with `_until` until `am status` shows `data.control.requests` holding one `pause` row with a non-null `handled_at`. That is the cross-process stand-in for `test_live_control.py`'s `_signal_when_applied`. Only after that, call `hold.release(a1)`.
6. **Parked.** `_until` `report.json` exists under `paths.data_dir()/"runs"/run_id`, then `_until(_lease(R, run_id) is None)`. The report is `ok` true, and its `data` has `paused is True`, `run_id == run_id` and `resume == f"am resume {run_id}"`. It has none of `escalated`, `failed_phase`, `integrated` or `done` (the `CONTROL_KEYS_NEVER_PRESENT` of `test_live_control.py`), and `a1` is not in `completed`. `stopped` names a1 parked before `review`, because a control never cancels the running `implement`. The row shape is `orchestrate.stopped_row`: `{"story": A, "subtask": a1, "before_phase": "review"}`. Confirm this against the run rather than assume it. `am status` now reports `run.status == "stopped"`. The watch stream has delivered a `run_upsert` whose `payload.status == "stopped"`.
7. **Resume (foreground).** `am resume run_id --repo-dir R --verify VERIFY` exits 0 (`resume_run` refuses a live lease, C10, so step 6's lease wait is load-bearing). Its `data` has `done is True`, `resumed is True`, `run_id == run_id`, no `escalated`, `a1` in `completed`, and `set(integrated.merged) == set(stories.values())`. `am status` then reports `run.status == "done"`, and `_lease` is `None`.
8. **Stream continues across lives.** Fetch the one-shot `am watch run_id` (no `--follow`). Keep draining the queue until the stream's last `seq` equals the one-shot's max `seq`. The stream's JournalLines (everything after the hello) must equal the one-shot's `data.events` exactly, in the same order. Their `seq` values must be `1..N` with no gap and no duplicate across the pause/resume boundary. The `run_upsert` statuses, in stream order and with consecutive repeats collapsed, must contain `started`, `stopped`, `started` and `done` as a subsequence.
9. **Stream ends cleanly.** Send `SIGINT` to the watch child. It exits 0 and its stderr file (`am_processes.stderr_of`) is empty.

## Error paths

The test itself exercises no refusal. The existing unit and live-control tests cover the C8 and C10 refusals. Inside the test, every wait is a bounded `_until` or queue `get` that ends in `pytest.fail` with a message naming the step. Teardown never leaves a process behind: `detached_pids` killpg's the detached session, `am_processes.close` kills the watch child, and `hold` calls `release_all`.

## Tests

| Test | File | Tier | Why this tier |
|---|---|---|---|
| `test_a_detached_milestone_run_is_watched_paused_and_resumed_to_done` | `tests/e2e/test_detached_pause_resume.py` (new) | `@pytest.mark.e2e_fake` (explicit) | Spawns real `am` processes, a detached session-leader child, real git and brd, and the fake `claude`. That is production wiring under the fake harness, which the CLAUDE.md placement rule puts in `e2e_fake`, not unit or `git`. It is the one test for this scenario family. |

Tests first (TDD): write the test and run it. If it passes on this branch with no production change, the card is done.

## Verification

- `uv run pytest -m e2e_fake tests/e2e/test_detached_pause_resume.py`: the new test passes. It is excluded from the default run by `addopts`.
- `uv run pytest`: the default unit + git suite stays green.
- There is no lint or typecheck command.
