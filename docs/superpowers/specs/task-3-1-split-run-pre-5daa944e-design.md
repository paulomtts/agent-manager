# 3.1 Split run pre-flight from the engine hand-off (card 5daa944e)

Parent story fea654ef "am run --detach". Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` section 2 (the whole pre-flight runs in the foreground with today's refusals, the run is created and its lease taken, and only then is the engine handed off) and its Compatibility section (additive only, journal schema 1).

## Scope

This is a behaviour-preserving refactor of `src/agent_manager/cli.py` (`run_card`) and `src/agent_manager/orchestrate.py` (`run_milestone` / `_run_milestone_async`). Each run path gets an explicit seam with three stages:

1. Pre-flight. Reads and refusals only, ending with the run id minted and the run record built. The one exception is today's fresh-milestone `refresh_git` (fetch and prune), which stays where it is, after the last refusal and before `Store.open`; it is not a store or run-directory write.
2. Recorded. The store is opened, the lease and claims are taken, and the run's `started` rows are written. The result is "pre-flight done, run recorded and leased".
3. Engine. Everything from the first engine-side action through the final records and the report.

The existing sync wrappers compose these three stages and behave exactly as they do today. A later caller (3.2's detached child) can then run stage 3 by itself against a run that stages 1 and 2 already produced. This card does not build that caller.

Out of scope, owned by siblings: the `--detach` flag, the child process, `run.log`, `report.json`, refusing `--dry-run` with `--detach` (3.2, aff9fdbf), and the e2e_fake test (3.3, ef6e5633). This card adds no CLI flag, makes no user-visible change, adds no JSON key, and makes no journal or schema change.

## The seam

The names below are the ones the tests use. Internal-only state uses plain frozen dataclasses (CLAUDE.md: Pydantic is only for boundary-validated models).

### `--card` path (cli.py)

- `preflight_card(card_id, *, repo_dir, branch_prefix, base_branch, clock) -> CardPreflight`. It runs, in today's order: `resolve_repo_dir`, `board.show` of the card, `ParentlessCardError`, `board.show` of the parent, `dag.task_branch`, `worktree_for`, the `card:<id>` claim, `refuse_claimed`, `clock()` and `mint_run_id`. It also builds the `models.Run`, `StoryRun` and `SubtaskRun` records with status `started`. It has no side effects: it creates no run directory and opens no store.
- `recorded_card_run(pre) -> ContextManager[RecordedRun]`. It runs `Store.open`, then `run_lease(store, claims=pre.claims)`, then `record_run`, `record_story` and `record_subtask`. It yields an object that exposes at least `run_id`, `store` and `lease`. On every exit, including an exception raised in the body, it releases the lease and claims (`Lease.__exit__`) and only then calls `store.close()`.
- Engine: the body that is left, a coroutine `run_card_engine(pre, recorded, *, commands, allow_no_verification, runner_factory, control_interval)`. It creates the `StopSignal`, runs `control.controlled(drive_subtask_async(...), lease=lease, ...)`, records the outcome, writes the `card_outcome_comment` and its warnings, and returns the payload. `lease.token` reaches `card_outcome_comment` exactly as it does today.
- `run_card` keeps its signature, return payload and exceptions. It becomes `pre = preflight_card(...)`, then `with recorded_card_run(pre) as rec:`, then the engine under `asyncio.run(...)`.

### `--milestone` path (orchestrate.py)

- `preflight_milestone(milestone, *, repo_dir, base_branch, branch_prefix, max_concurrent, clock, resume_run_id) -> MilestonePreflight`. It runs, in today's order: `resolve_repo_dir`, `resumable_milestone_run` (resume only), `board.roots`, `census.find_milestone` (the ambiguity and unknown refusals) or `find_run_milestone`, `board.tree` and `flatten_milestone`, `plan_levels` (the cycle refusal), `story_tips`, `milestone_claims`, and `cli.refuse_claimed` as the last refusal. A fresh run then continues with `refresh_git`, `clock()`, `mint_run_id` and `models.Run` (with `milestone_id` set). A resume continues with the `model_copy` of the resumed run. It never opens the store, so every refusal still leaves no fetch, no prune, no run row and no run directory.
- `recorded_milestone_run(pre) -> ContextManager[RecordedRun]`. It runs `Store.open`. On a resume it then runs `open_cards` and `resume_checkpoints` (read-only, before the lease). Then it takes `cli.run_lease(store, claims=pre.keys)`. On a resume, `refresh_git` runs under the lease. Then `record_run`, `record_plan` and, on a resume, `reopen_rows`. It yields `run_id`, `store`, `lease`, the plan `rows` and `checkpoints`. Its exit order is the same as the card's: lease and claims first, then `store.close()`.
- Engine (async), `run_milestone_engine(pre, recorded, *, commands, allow_no_verification, runner_factory, driver, max_concurrent, control_interval, slots)`. `pre` must carry everything the body reads after the recorded stage: `root`, `milestone_card`, `plan`, `levels`, `tips`, `keys`, `base_branch`, `branch_prefix`, `run_record`, `resumed` and the chosen `drive` (`cli.drive_subtask_async` unless `driver` is given). It starts at `comments.flush` and `reroll_stale_stories`, then runs `control.controlled(supervise(..., lease_token=lease.token, slots=slots), lease=lease, ...)`. After that come today's outcome handling, Integrate and `report` (including `resumed` and `took_over`). Board comment flushing is engine work, not pre-flight.
- `_run_milestone_async` keeps its signature (including `slots` and `resume_run_id`) and composes the three stages. `run_milestone` keeps its argument validation and its `asyncio.run(_run_milestone_async(...))`. `run_board` and `_run_board_async` are not split at their own level. They keep calling `_run_milestone_async` per milestone and inherit the seam. `resume_run` and `_resume_from_checkpoint` keep reaching `_run_milestone_async` through `resume_run_id`, with no change.
- The `cli.run` dispatch, `_check_run_targets`, the `HANDLED` mapping to the exit-3 envelope, and the attribute call sites `orchestrate.run_milestone` and `orchestrate.run_board` (which tests patch) are unchanged.

### Docstrings

Update the docstrings of `run_card`, `run_milestone` and `_run_milestone_async` so they name the three stages and say that the sync wrappers compose them with `asyncio.run`. The load-bearing ordering statements stay true and are moved onto the stage that now owns them:

- every refusal comes before any side effect;
- `refuse_claimed` comes before `refresh_git` and `Store.open`;
- the lease is taken before `record_run`;
- the lease and claims are released before `store.close()`.

## Behaviour that must not change

- Every refusal fires at the same point, as the same exception, with the same envelope and exit code: `ParentlessCardError`, `ClaimedError`, `RunIsLiveError`, an unknown or ambiguous milestone, a blocker cycle, a resume refusal, and a checkpoint under another workflow digest.
- The "missing verification" refusal (`reducers.verification_gate`, steps/reducers.py:30, with siblings in bases.py and integration.py) stays inside the engine's explore phase. This card does not move it. The seam forwards `allow_no_verification` to the engine unchanged. Moving it into pre-flight is a behaviour change, and no existing test proves that move safe.
- Payloads and journal rows are byte-for-byte as today. No new JSON keys are added.
- A crash in the engine still propagates, still releases the lease and claims, and still closes the store.

## Error paths for the seam itself

- When `preflight_*` raises, nothing has been written. There is no run directory under `<data dir>/runs/` and no `run_leases` row.
- When the lease is lost to a race inside `recorded_*_run`, entry raises `ClaimedError` or `RunIsLiveError` with no run row written (the empty run directory is the same as today), and the store is closed.
- When code inside the `recorded_*_run` block raises, including when the engine is never started, the lease and claims are released first and the store is closed after.

## Tests (TDD: write them first; the existing suite stays unmodified and green under `uv run pytest`)

Tier rule: CLAUDE.md "Test tiers". The tier depends on what a test spawns, not on which directory it is in. None of the tests below may start a subprocess. Use the `fake_board` fixture from tests/conftest.py, which installs FakeBoard as `board.run_brd`. Use a plain `tmp_path` directory as `repo_dir`, not the git-initialising `project` fixture. Point `XDG_DATA_HOME` into `tmp_path`. For the milestone tests, monkeypatch `orchestrate.refresh_git` to a recorder. Fake the drive with `FakeDriver` (orchestrate) or a fake `drive_subtask_async` (cli). Under those conditions every test below is **unit tier (unmarked)** and must stay within 0.5s.

tests/test_cli.py, all unit:
1. `preflight_card` on a parentless card raises `ParentlessCardError` and creates no run directory.
2. `preflight_card` on a card another live run claims raises `ClaimedError` and creates no run directory. Seed the claim the way the existing `run_leases`/`run_claims` helper near test_cli.py:6571 does.
3. `preflight_card` returns the minted run id, branch, worktree and `started` records, and writes nothing.
4. While inside `recorded_card_run`, the store holds the `started` run, story and subtask rows, the lease is live with the `card:<id>` claim, and `drive_subtask_async` has not been called (patch it to fail if called).
5. Leaving `recorded_card_run` with an exception, without running the engine, releases the claim and the lease before `store.close()`, and a second `preflight_card` for the same card is not refused.
6. `run_card` hands the engine the lease from the recorded stage: `controlled` and `card_outcome_comment` receive that lease and its token, shown through a fake drive.

tests/test_orchestrate.py, all unit:
7. `preflight_milestone` on a blocker cycle raises the same refusal as test_orchestrate.py:133, never calls `refresh_git`, and creates no run directory.
8. `preflight_milestone` on an ambiguous milestone needle raises the existing ambiguity error before `refresh_git`, and creates no run directory.
9. `preflight_milestone` with a claimed key raises `ClaimedError` before `refresh_git`.
10. A fresh `preflight_milestone` calls `refresh_git` once, after every refusal, and returns a `models.Run` with `milestone_id` set. It creates no run directory.
11. While inside `recorded_milestone_run`, the run is recorded `started`, every planned row is `pending`, the lease is live with all `milestone_claims` keys, and neither the driver nor `comments.flush` has been called.
12. Leaving `recorded_milestone_run` with an exception, without starting the engine, releases the claims and the lease before `store.close()`.
13. The milestone engine, given a recorded run (the autouse `integrate_recorder` in test_orchestrate.py stands in for Integrate, so no git runs), passes `lease.token` to `supervise` and the same lease to `controlled`. With a `FakeDriver` it ends `done` with the same payload `run_milestone` returns today.
