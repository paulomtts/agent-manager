# Prove parallel stories under a fake claude with a rendezvous (card 1976123f)

Parent: story 4633be8c ("Prove it: parallel under a fake claude, against a real harness, and documented"), milestone cdbfa10d (Milestone 4). Governing docs: the main design spec `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (section 14, test tiers) and the parallel-stories addendum (decisions P1, P4, P5, P7; section 5 lists what is out of scope).

Note on inputs: the exploration summary for this card was cut off at its 8000-character cap, so this spec relies only on the part that came through. I checked the cited code in this worktree, which contains the merged m4 code: `orchestrate.run_milestone(..., max_concurrent=...)`, the `stopped` status, `should_stop`, `--max-concurrent`, and the escalation payload keys `also_escalated` and `stopped`, whose entries are `{story, subtask, before_phase}`.

## Scope

Only test code, all under `tests/e2e/` (P7). No file under `src/` changes. This card does not cover the real-harness `-m e2e` test (sibling 2af0e413) or the README and main-spec section 11 docs (sibling a2dad516). Also out of scope: Integrate, per-story readiness, milestone-aware resume, watch/retry/cancel, cost capture, the reviewer Plan-Hash brief, and Ctrl-C.

## A. Rendezvous in the fake claude (`tests/e2e/fake_claude.py`, extended in place, not copied)

- New module constants: `RENDEZVOUS_DIR_ENV = "FAKE_CLAUDE_RENDEZVOUS_DIR"`, `RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"`, and a timeout of 20 seconds.
- In the `implement` phase only, and only when `FAKE_CLAUDE_RENDEZVOUS_DIR` is set, the fake does three things:
  1. It writes a marker file in that dir. The file name comes from its own cwd, is filesystem-safe, and is the same on every run for the same cwd.
  2. It polls until the number of marker files is at least `FAKE_CLAUDE_RENDEZVOUS_COUNT`.
  3. It then continues with the normal implement behaviour.
- If the count is still short after the timeout, the fake raises `FakeClaudeError` naming the dir, the count it saw and the count it needed, so `__main__` exits 1. A missing or non-integer count while the dir is set is also a `FakeClaudeError`.
- When the env var is unset, the fake behaves exactly as it does today: no wait and no files.
- The existing design rule still holds. The fake stays stdlib-only, imports nothing from `agent_manager`, takes every other input (result path, plan hash, branch) from the brief, and never commits the spec or plan. The rendezvous dir and the review-fail marker are the only inputs the test controls.
- Markers accumulate for the whole run. After a count is reached once, every later implement in the same run passes straight through.
- Pin the two env-var names in `tests/e2e/conftest.py`, next to `FAKE_LOG_NAME` and `FAKE_REVIEW_FAIL_MARKER`, as mirrored constants (`FAKE_RENDEZVOUS_DIR_ENV`, `FAKE_RENDEZVOUS_COUNT_ENV`). Add equality pins in `test_fake_claude.py` in the same way the existing ones are written.

## B. Parallel milestone tests (new module `tests/e2e/test_parallel_milestone.py`)

- **Production wiring:** no `runner_factory` or `driver` override. The run goes through `cli.default_runner_factory`, the real `ClaudeAdapter`, `harness.launcher.run_direct`, and the fake first on `PATH` (the `fake_claude_bin` fixture).
- **How the tests run a milestone:** either call `orchestrate.run_milestone(..., max_concurrent=N)` directly or invoke `am run --milestone ... --max-concurrent N`. For the CLI route, extend `run_milestone_cli` with an optional `max_concurrent` argument, or add a sibling fixture; the existing callers must keep their current argv.
- **Environment:** tests set both env vars with `monkeypatch.setenv`. `run_direct` calls `Popen` without `env=`, so the child inherits `os.environ`; this is confirmed at `src/agent_manager/harness/launcher.py:132`.
- **Board:** a new function-scoped fixture (for example `parallel_board`) built on `fresh_project`, which gives each test its own repo, board and `XDG_DATA_HOME`. It uses `_add_card(..., blocked_by=[...])`, `dag.task_branch(MILESTONE_PREFIX, ...)` and `MILESTONE_PREFIX` from conftest.
  - Story A has subtasks a1 -> a2.
  - Story B is independent of A and has subtasks b1 -> b2.
  - Story C is blocked by A and has one subtask, c1.
  - The fixture also exposes a review-fail marker path, `<root>/.git/fake-claude-review-fail`. The existing `review_fail_marker` fixture depends on `milestone_board`, so it cannot be reused here.
- **Unmarked:** the module is not marked `e2e` and carries a guard test like `test_this_module_runs_in_the_default_suite_unmarked` in `test_milestone_run.py`.

### Observable behaviour to prove

1. **Two lanes, count 2.** `max_concurrent=2`, rendezvous count 2, and the run completes.
   - The run cannot complete unless a1 and b1 were in implement at the same time, so completion proves the overlap.
   - Every subtask branch contains its predecessor (`_is_ancestor`).
   - c1's branch roots on A's tip.
   - Board status for stories A, B and C and for the milestone is `done` (`board.show(...).status`).
   - `git rev-parse main` is the same before and after the run.
2. **One lane, count 1.** `max_concurrent=1`, rendezvous count 1, and the run completes. The recorded `PhaseRun.started_at`/`ended_at` intervals are loaded through `store.open_db(cli.resolve_repo_dir(root))` and `store.load_run`. The span of story A's subtasks and the span of story B's subtasks do not overlap. This shows that `--max-concurrent 1` still behaves as the sequential runner did.
3. **Journal integrity.** After the run in test 1:
   - the journal's sequence numbers are unique and contiguous;
   - `Store.rebuild_from_journal(run_id)` equals the DB projection loaded by `load_run`.
4. **Escalation in one lane.** `max_concurrent=2`, rendezvous count 2, and a1's branch written into the review-fail marker.
   - Exit code is `cli.EXIT_ESCALATED` (via the CLI), or the report says `escalated: true` (direct call).
   - The report's `story` is A.
   - `stopped` contains exactly one entry for B's lane: `{story: B, subtask: <b1 or b2>, before_phase: <non-null phase>}`.
   - That subtask's recorded status is `stopped`. No attempt exists for its `before_phase` or any later phase.
   - Story C never started: `cli.worktree_for(root, c1_branch)` does not exist, `c1_branch` does not exist, and there is no c1 attempt and no fake-log entry with c1's cwd.
5. **Relaunch.** Test 4 continues: remove the marker and run the same milestone again.
   - The milestone completes.
   - Subtasks that finished `done` in the first run are reported in `completed` or as skipped, and the second run's `read_fake_log(run_id)` has no entry with their cwds.
   - c1 runs, and the stories and milestone end `done`.

### Determinism of test 4 (an open point for the plan)

A count-based rendezvous lets every waiting process go at once, so on its own it cannot hold B inside implement while A goes on to review. The rendezvous in test 4 does guarantee three things:

- a1 and b1 overlap;
- B has more work left after that point than A does (b1's remaining phases plus all of b2, against a1's verify and review);
- the stop is observed at B's next phase boundary.

The assertions above are therefore written to be correct at whichever boundary B stops: they check `before_phase` and "no later attempt", not "stopped in implement". The plan must either accept this ordering margin, adding more subtasks to B if needed, or find a strictly deterministic hold that still uses only the rendezvous dir and the review-fail marker. It must not add a new test input to the fake.

### Error paths

- A rendezvous that times out fails the fake, and so fails the attempt. Tests must never depend on the timeout to pass: a passing run is fast and a failing one takes about 20 s.
- The whole default suite (`uv run pytest`) stays green.
- Threads share one process. Nothing starts two `am` processes.

## Test list

Tier rule: main spec section 14. In this repo, `tests/e2e/` holds the unmarked production-wiring tier (the fake claude on `PATH` with the real adapter and launcher). Real-harness tests are marked `e2e`. The injected-driver tier is `tests/test_orchestrate.py` and is not used here.

| Test | File | Tier |
|---|---|---|
| env-var constants pinned equal to conftest mirrors | tests/e2e/test_fake_claude.py | e2e dir, production-wiring tier (unmarked), fake self-test |
| rendezvous unset: implement does not wait and writes no marker | tests/e2e/test_fake_claude.py | same |
| rendezvous count reached: marker named for cwd written, implement proceeds | tests/e2e/test_fake_claude.py | same |
| rendezvous unmet: fake fails with `FakeClaudeError`, exit 1 (short timeout injected via a module-level constant patched in-process, so the test stays fast) | tests/e2e/test_fake_claude.py | same |
| module runs in default suite unmarked (guard) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| two lanes overlap and complete (1) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| one lane never overlaps (2) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| journal contiguous, rebuild equals projection (3) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| escalation stops the other lane, later level never starts (4) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| relaunch after escalation completes and skips done subtasks (5) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
