# Subtask 736d6728 — `am reset RUN_ID`, writing `cancelled` through the lease

Narrows `docs/superpowers/specs/2026-10-03-am-reset-design.md` (§3.1–3.4, the §3.4 `DeadRunError` pointer, and the envelope skeleton of §3.5) to one subtask under story 816cb8b3. The design is settled there; this document only fixes what this subtask delivers and what it leaves to its siblings.

## Scope

In scope:

- A new command `am reset RUN_ID [--repo-dir] [--pretty]` in `src/agent_manager/cli.py`. It does exactly this: load the run read-only, refuse, take the run's lease, write `cancelled`, release the lease.
- A new `NotResettableError(CliError)` in `cli.py`, next to `DeadRunError`/`RunIsLiveError` (cli.py:137-149). Those are the run-control refusals that only `cli.py` raises. `UnknownRunError`/`NotResumableError` live in `runs.py` because `orchestrate` raises them too, and nothing outside the CLI raises this one.
- Adding `store_module.CorruptJournalError` (store.py:284, a `JournalError` subclass) to `HANDLED` (cli.py:1142-1149). A crashed run's torn journal then becomes an exit-3 envelope instead of a traceback. Add only that subclass, not `JournalError` as a whole.
- Both `DeadRunError` wordings in `_controllable_lease` (cli.py:2150-2153 and 2155-2159) gain "or `am reset <run-id>` closes it" after the existing `am resume <run-id>` pointer. This is the only change to an existing command.

Out of scope, owned by siblings:

- af52db54 owns `Store.checkpoint_cards(run_id)`, the `latest_open_checkpoint`-based `open_in` computation, chain detection, and spec §4 test 10. This subtask always emits `cards: []` as a placeholder so the envelope key exists, and the sibling fills it in. This subtask does not call `latest_open_checkpoint`.
- 522adfb5 owns the relaunch/resume proofs (spec §4 tests 8, 9), the `git`-tier `worktree.ensure` companion, the `e2e_fake` incident replay (test 12) and the README update (§3.7).

Unchanged (§2): no raw SQL against the projection, no write outside `_fenced()` once a token is bound, no git of any kind, no push, no touching main/master, no card or board writes.

## Observable behavior

1. Read-only phase. Mirror `resume_run` (cli.py:1982-2057): `resolve_repo_dir`, `open_db`, `load_run`, then the refusals below on that same connection, in this order, all before `Store.open`. A refusal therefore leaves no run directory, lease row or journal line behind.
   1. The run id is not in the projection: `UnknownRunError`, with the same message style as `resume_run`'s (`am runs` lists the ones that are).
   2. A lease row is present and `control.lease_is_live`: `RunIsLiveError`. `_run_is_live_error` (cli.py:718) cannot be reused as-is here — its wording ("wait for it to exit, or `am status <run-id>`") has neither an `am cancel` pointer nor "nobody driving" framing, and is also what `run_lease`'s translation raises for step 3's race (acceptable there per §3.4, since that is only the rare post-read-only-check race). This refusal needs its own message, matching `_run_is_live_error`'s pid/host/heartbeat-age content but saying: the run is running, so `am cancel <run-id>` is the command; `am reset` is for a run nobody is driving. It must not point at `am resume`.
   3. `status == "done"`: `NotResettableError`. The message says the run finished, there is nothing to close, and new work starts with `am run`.

   Each refusal is an `{"ok": false, "error": {"type", "message"}}` envelope at exit 3.
2. Already cancelled. `status == "cancelled"` is not a refusal. The result is exit 0 with `already_cancelled: true`, `previous_status: "cancelled"`, and no journal line or row written. The design allows the check to sit inside the lease block (one code path, lease taken and released) or before it. Either way, nothing reaches the journal.
3. Write phase, for every other status (`stopped`, `escalated`, or `started` with no lease or a dead lease):
   - Call `Store.open(root, run_id)`, then `run_lease(store)` (cli.py:767) with **no claims**.
   - `take_lease` re-checks liveness atomically. A live holder that appeared after the read-only check surfaces as `RunIsLiveError` through `run_lease`'s translation (generic wording is acceptable there).
   - A dead holder is displaced and reported as `took_over: {pid, host, heartbeat_at}`, exactly as `_resume_from_checkpoint` reports it (cli.py:1970-1976).
   - Inside the block there is exactly one write: `store.record_run(run.model_copy(update={"status": "cancelled"}))` (store.py:1196). That is one fenced `run_upsert` journal line.
   - No checkpoint row is written or deleted. Story, subtask, phase and attempt rows are untouched.
   - When the block exits, the lease row is released and the store closed.
   - The command never looks at the filesystem beyond the store: worktree presence is irrelevant.
4. A run with no checkpoints resets like any other, with `cards: []`. There is no "nothing to reset" refusal.
5. Success envelope: `{"ok": true, "data": {"run_id", "previous_status", "status": "cancelled", "already_cancelled", "cards": [], "message", ["took_over"]}}`. `took_over` is present only when a dead lease was displaced. The message follows §3.5: the run is cancelled, `am resume` refuses it, and a relaunch starts its cards from their first phase.

## Tests

All tests go in `tests/test_cli.py`. Concurrency tests either go there or sit beside `tests/test_control.py`'s two-store pattern.

All are **`unit`** tier, unmarked, per the placement rule (design spec §14 / CLAUDE.md "Test tiers": a test's tier is chosen by what it actually spawns). They are driven through the `projection` fixture (tests/test_cli.py:3901) and the store, with `brd`/`git`/`claude` stubbed off `PATH`, and spawn no subprocess. None lives under `tests/steps/` or `tests/e2e/`, so no auto-marking applies, and none needs an explicit mark.

1. **Reset of a `stopped` run with an open `parked` checkpoint** (unit). Expected:
   - exit 0, and the run row reads `cancelled`;
   - the journal gained exactly one `run_upsert` line with `payload.status == "cancelled"` and the next `seq`;
   - `rebuild_from_journal` yields `cancelled`;
   - the checkpoint row is unchanged, and `latest_open_checkpoint` for the card returns `None`;
   - the envelope has `previous_status: "stopped"` and `already_cancelled: false`.

   Run it twice, once with the worktree directory present and once with it removed. The store write must be identical. The `cards`/`open_in` assertions are af52db54's.
2. **Refused: live lease** (unit). Setup: a lease row with a fresh heartbeat and this process's pid. Expected: `RunIsLiveError`, exit 3, a message naming `am cancel`, and status, journal length and lease row all unchanged.
3. **Started run with a dead lease** (unit). Setup: a stale heartbeat or a dead pid. Expected: success, `took_over` names the displaced pid/host/heartbeat_at, and no `run_leases` row for the run remains.
4. **Already cancelled** (unit). Expected: exit 0, `already_cancelled: true`, journal length unchanged.
5. **Refused: `done`** (unit). Expected: `NotResettableError`, exit 3, nothing written.
6. **Refused: unknown run** (unit). Expected: `UnknownRunError`, exit 3, and no run directory created.
7. **Run with no checkpoints** (unit). Expected: success with `cards: []` and the status reads `cancelled`. The follow-on `am resume` refusal is 522adfb5's test 9.
8. **Concurrency** (unit). Use two stores on one database, as `tests/test_control.py` does. Two cases:
   - Another store holds the run's lease live and `lease_is_live` is monkeypatched so the read-only check passes. The reset must still be refused at `take_lease` with `RunIsLiveError`, with nothing written.
   - Two sequential resets of one run leave exactly one `run_upsert` line, and the second reports `already_cancelled: true`.
9. **`CorruptJournalError` is handled** (unit). A run whose journal has a torn line in its middle produces an exit-3 `ok: false` envelope, not a traceback.
10. **`DeadRunError` pointer** (unit). `am cancel` of a `started` run gets `am reset <run-id>` in its message in both cases: with no lease and with a dead lease. Extend the existing `DeadRunError` tests if they assert on the message.
