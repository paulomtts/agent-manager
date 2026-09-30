# Claim a milestone run's milestone, cards and integration branch (card 1a3fdd73)

Narrows Task 2.3 of `docs/superpowers/plans/2026-09-27-multi-process.md` (milestone 10, decisions X2–X11 of `docs/superpowers/specs/2026-09-27-multi-process-design.md`). Story f67dc4e4 "Leases that own runs, cards and branches". Branch prefix `m10`. Last of the story's three subtasks.

Note: the exploration summary handed to this stage was truncated at 8000 of 11782 characters, after the "What siblings own" section. That means the upstream stage wrote more than it was asked to. Nothing below relies on the missing text. The test-placement rule was re-read from the design spec, and the milestone-10 plan and spec could not be found in this worktree, so the plan's Task 2.3 wording is taken from the findings.

## Dependency

This work builds on siblings a7ed6c11 (store: `take_lease`, `claim_conflicts`, `release_claims`, `LeaseLostError`, ...) and ec7ae954 (`control.card_claim`, `control.branch_claim`, `control.Lease(store, claims=...)` with `.displaced`, and `cli.ClaimedError`, `cli.refuse_claimed`, `cli.run_lease`, `took_over` in `_resume_from_checkpoint`, `LeaseLostError` in `HANDLED`). Both are already in this worktree (cli.py:836-892, control.py:79-135), but neither is on master yet. Use them as they are. Do not change `store.py`, `control.py`, `cli.py`'s card/task-resume paths, `HANDLED` or the schema.

## Scope

All changes are in `src/agent_manager/orchestrate.py`:

- New pure function `milestone_claims(milestone_id: str, stories: Sequence[census.StoryPlan], branch_prefix: str) -> list[str]`. It returns `[control.card_claim(milestone_id)]`, then `control.card_claim(s.id)` for each `s` in `dag.remaining_subtasks(story)` for each story in census order, then `control.branch_claim(integration.integration_branch(branch_prefix))`. The list has no duplicates, keeping the first occurrence in order. This is spec X6's claim set for `am run --milestone M`: `card:M`, `card:<each remaining subtask>`, `branch:<prefix>-integrate`. A done subtask or a closed story adds no key.
- `run_milestone` (orchestrate.py:1299-), changing only how its lease and claims are taken:
  - Compute `keys = milestone_claims(milestone_card.id, plan.stories, branch_prefix)` once the plan is derived and after every existing refusal (`max_concurrent`, `resumable_milestone_run`, `find_milestone`/`find_run_milestone`, `flatten_milestone`, `plan_levels`).
  - Call `cli.refuse_claimed(root, keys, run_id=None if resumed is None else resumed.id)` before `refresh_git` on a fresh run and before `Store.open` on both paths. On a resume, passing `run_id` excludes the run's own rows.
  - Replace `with control.Lease(store) as lease:` (orchestrate.py:1423) with `with cli.run_lease(store, claims=keys) as lease:`, entered right after `Store.open` inside the existing `try` that closes the store. The same `lease` still goes to `control.controlled(...)`. The lease and claims are released before `store.close()`.
  - On a resume, `resume_checkpoints` only reads, so it may stay before the lease or move inside it. `refresh_git` (currently at orchestrate.py:1420, before the lease) and every `record_*`/`reopen_rows` must run inside the `run_lease` block.
  - On a resume, if `lease.displaced is not None`, every payload also gets `took_over = {"pid": lease.displaced.pid, "host": lease.displaced.host, "heartbeat_at": lease.displaced.heartbeat_at.isoformat()}`, copying cli.py:1655-1661. Add this in the existing `report` helper so every outcome shape gets it (done, escalated, paused, cancelled, Integrate-escalated).
  - Update the `run_milestone` docstring to describe the claims.

Out of scope: messages (`_claimed_error` is generic "{kind} {name} is being driven by run R ...", used as built), store/control/cli internals, dry-run and reader paths (these never call `milestone_claims`, `refuse_claimed` or `run_lease`), ProcessLock, and cross-process e2e proofs (spec §7 "End to end" bullets are not assigned to this card).

## Observable behaviour and error paths

- Ordering invariant (M7 + X5): every refusal, `refuse_claimed` included, comes before `refresh_git`, and on a fresh run `refresh_git` comes before `Store.open`. A refused `am run --milestone` leaves no run row, journal, fetch, prune or worktree. The one allowed leftover is an empty run directory, when the preflight passed but `take_lease` lost the race.
- Another live run claims the milestone, a remaining subtask, or `<prefix>-integrate`: `ClaimedError`, exit 3, in the unchanged envelope (`{"ok": false, "error": {"type", "message"}}`). The message names the first conflicting key's kind and name and the holder's run id and pid. X11 words the branch case as "belongs to run R of milestone M; use another --branch-prefix", but ec7ae954 built a generic message and changing it is out of scope. Tests assert the type and the key/run id, not that wording.
- A claim held by a dead holder (per `lease_is_live`) does not refuse. The run proceeds, and a resume reports `took_over`.
- Resuming a milestone run that is still live: `RunIsLiveError`, unchanged (C10).
- Losing the lease mid-run: `LeaseLostError` reaches the envelope at exit 3, unchanged. The run writes nothing more.
- Claims and lease are released on every exit: done, escalated, paused, cancelled, Integrate escalation, and an exception from `supervise`/Integrate.
- `am status` of a live milestone run lists its keys under `control.claims`. This comes from ec7ae954's `status_for`, and here it is only observed.

## Tests

Placement rule (design spec §14 "Testing", as applied by sibling ec7ae954): pure functions get plain unit tests. Anything touching git or board state is a Steps test against a real temp git repo and a temp `brd` board, with no network and no filesystem mocking. `tests/e2e/` holds only the single opt-in real-harness test, so none of these tests go there. Tests never sleep to prove ordering; use pipes, marker files, fake-driver rendezvous or exit codes. Child processes inherit the test `XDG_DATA_HOME`. Plant live or dead holders the way ec7ae954's `_plant_lease` does: `run_leases` and `run_claims` rows written over a second `open_db` connection.

`tests/test_orchestrate.py`:
- `test_milestone_claims_lists_milestone_remaining_subtasks_then_integration_branch`: census order. Done subtasks and closed stories are left out. The branch key is `branch:<prefix>-integrate`. Unit (pure).
- `test_milestone_claims_has_no_duplicates`: Unit (pure).
- `test_a_milestone_run_is_refused_while_a_live_run_claims_one_of_its_cards`: exit/`ClaimedError` naming the holder. Asserts no run row, no worktree, no fetch (a `refresh_git` spy was never called), and no run directory. Steps (default suite).
- `test_a_milestone_run_is_refused_while_a_live_run_claims_its_integration_branch`: same assertions, with key `branch:<prefix>-integrate`. Steps.
- `test_a_dead_claim_does_not_refuse_a_milestone_run`: Steps.
- `test_a_milestone_run_holds_its_claims_while_driving`: a fake driver reads `held_claims`/`am status` mid-run and sees exactly `milestone_claims(...)`. Steps.
- `test_a_milestone_run_releases_its_claims_on_every_exit`: done, escalated, and a driver that raises. Steps.
- `test_refresh_git_and_first_write_run_inside_the_lease_on_resume`: spies on `refresh_git` and `Store.record_run` see `store._token` set. Steps.
- `test_a_milestone_resume_excludes_its_own_claims_and_reports_took_over`: dead lease plus the run's own claims planted. The resume proceeds, and `took_over.pid` equals the dead pid. Steps.

## Verification

`uv run pytest` (full suite; there is no separate lint or typecheck, per CLAUDE.md).
