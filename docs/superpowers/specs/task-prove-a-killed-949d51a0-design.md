# Prove a killed milestone resumes under the fake claude (card 949d51a0)

Parent story: 84802b0b "Milestone-wide resume". Sibling 54e4ec29 ("Continue a milestone run with `am resume`") owns the production code: `cli.resume_run` dispatching on `run.workflow`, and `orchestrate.run_milestone(..., resume_run_id=...)` with `resumable_milestone_run`, `find_run_milestone`, `open_cards`, `resume_point`, `resume_checkpoints`, `_refuse_changed_workflow`. That code is present in this worktree (`src/agent_manager/cli.py:1389`, `src/agent_manager/orchestrate.py:1211`). Source of truth: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §6, §7, §9; plan `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 4.2.

## Scope

This card adds tests only. Its deliverable is a new `tests/e2e/test_milestone_resume.py`. If needed, `tests/e2e/fake_claude.py` gets one new kill-switch env var, and `tests/e2e/conftest.py` gets small fixtures or helpers. It does not reimplement or restructure 54e4ec29's resume code. If the new test exposes a bug, the fix lands here, and the failing test must be committed first. Commit message: `test(e2e): a killed milestone resumes where it stopped`.

Out of scope, per spec §10: verification discovery, live pause/cancel/watch/retry, more than one `am` process per repo, grafo `max_workers`, and the multi-blocker handling of leave-me-alone. Unit coverage that already exists stays where it is and must not be duplicated here. `tests/test_orchestrate.py` already covers a `BaseException` propagating rather than escalating (around lines 1643 and 2144-2177). It also covers `run_milestone(resume_run_id=...)` with a `FakeDriver` (3613+).

## Observable behaviour under test

The test uses a milestone with one multi-blocker story. The `merged_base_board` fixture fits: A (a1) and B (b1) are independent, C (c1) is blocked by both, and D (d1 -> d2 -> d3) is a sibling lane. The card text says "three-story", but a multi-blocker story plus a second lane that is live while it runs needs the D sibling. Reuse this fixture rather than adding a new board. The run goes through `am run --milestone` (the `run_milestone_cli` fixture) with the real `ClaudeAdapter`, the real `launcher.run_direct` and the fake `claude` on PATH.

1. Kill point. The manager dies with a plain `BaseException` subclass. It must not be `KeyboardInterrupt`, which follows the precedent `_Killed` in `tests/e2e/test_milestone_run.py:245-294`. At that moment, C's merged base has already been built and one lane is inside `plan` while another is inside `implement`. The natural choice is c1 in `plan` and a D subtask in `implement`. The kill must be deterministic and must not use sleeps or timing. Allowed mechanisms:
   - A one-shot `run_direct` monkeypatch in the manager process, like `_kill_after`.
   - A new fake env var that makes the fake exit abruptly in a named phase (per-card/phase or per-branch). It follows the shape of the existing whitelist: `FAKE_CLAUDE_*` naming, strict parsing, a documented no-op when unset, an entry in the module docstring, and it is set through the test's `monkeypatch`.
   - Existing synchronisation (the rendezvous env vars) to hold the second lane in `implement` until the kill.

   The kill-switch must never appear in a brief or prompt. `am run` raises the exception out of `CliRunner`, and the run is left non-`done` with checkpoints written.
2. Resume. `am resume <run-id>` goes through `CliRunner` against the same repo and exits 0. It keeps the same run id and reuses the recorded prefix, base and `max_concurrent`.
3. Assertions after resume. The fake's per-run log (`read_fake_log(run_id)`), counted per (card, phase), shows the following:
   - Every phase that finished before the kill ran exactly once across both invocations. This covers a1 and b1 end to end, and the earlier phases of c1 and the D subtask.
   - The two interrupted phases ran exactly twice: once killed, once resumed.
   - Everything after them ran once.

   The merged base for C is not rebuilt: its branch tip is unchanged across the resume, and no second merge of `merged_from` happens. Assert this through git (the ref and merge commits) or the run's recorded attempts, not through timing. The run ends `done`, Integrate has run, and the report says `integrated`. The report carries `resumed: true`, and its `completed` lists only what finished during the resume invocation. `main` has not moved and nothing has been pushed.
4. The whole default suite stays green, including `tests/e2e`. `tests/e2e/test_parallel_milestone.py` and `tests/test_orchestrate.py` pass unchanged. So does the fake's own contract test, `tests/e2e/test_fake_claude.py`, which gets a case for the new env var if one is added.

## Error paths

- Resume refusals (a stale digest exits 3 and writes nothing, and resuming a `done` run exits 3) are named in spec §9. Cover them end to end here only if they are not already covered in e2e by 54e4ec29. If they are covered, do not duplicate them. If added, they belong in `tests/e2e/test_milestone_resume.py`.
- A new fake env var with a malformed value must fail loudly (`FakeClaudeError`), like `CRITIC_BLOCKS_ENV` and `RENDEZVOUS_COUNT_ENV`.

## Tests

Tier rule: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 and supervisor-tree spec §9. Anything that must run through the real CLI, the real adapter and launcher, the fake `claude` subprocess and real git worktrees belongs in `tests/e2e/`. In-process `FakeDriver` coverage stays in `tests/test_orchestrate.py` and `tests/test_cli.py`.

| Test | Tier |
|---|---|
| `test_a_killed_milestone_resumes_where_it_stopped`: kill with one lane in plan and one in implement after C's merged base is built, then `am resume`. It asserts the same run id, per-(card, phase) counts (finished phases once, interrupted phases twice), the merged base not merged again, `done` + `integrated`, `resumed: true`, and `main` unmoved. | e2e (`tests/e2e/test_milestone_resume.py`) |
| Only if a kill-switch env var is added: the fake exits abruptly in the named phase, is a no-op when unset, and rejects malformed values. | e2e (`tests/e2e/test_fake_claude.py`, the fake's contract tests) |
| Only if not already covered by 54e4ec29 in e2e: stale digest gives exit 3 with nothing written; resuming a `done` milestone run gives exit 3. | e2e (`tests/e2e/test_milestone_resume.py`) |
| Any regression test for a bug the e2e test exposes | the tier of the module the fix touches (unit test beside it under `tests/`), written failing first |

## Invariants

- Only `orchestrate.py` imports grafo, and every Node is built with `timeout=None`.
- Only the subtask is a pygents Agent.
- No test sleeps to prove ordering.
- A fake `claude` knows nothing beyond its brief and the explicit env-var whitelist.
- The milestone's base branch never moves and nothing is pushed.
