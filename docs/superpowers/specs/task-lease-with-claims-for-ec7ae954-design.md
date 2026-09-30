# Lease with claims for `--card` runs and task-run resumes (card ec7ae954)

Narrows Task 2.2 of `docs/superpowers/plans/2026-09-27-multi-process.md` (milestone 10, decisions X1–X11 in `docs/superpowers/specs/2026-09-27-multi-process-design.md`). Story f67dc4e4 "Leases that own runs, cards and branches". Branch prefix `m10`.

## Dependency

This task consumes the store API of sibling task a7ed6c11 (branch `m10/task-take-the-lease-a7ed6c11`, not yet on master): `take_lease`, `bind_lease`, `release_claims`, `claim_conflicts`, `held_claims`, `LeaseHeldError`, `ClaimHeldError`, `LeaseLostError`, `ClaimRow`, `LeaseTake`. Read those as built on that branch; do not change `store.py` or the schema (`run_claims` stays row-only). Current master's `control.Lease` (control.py:79-133) still calls `store.acquire_lease` and must be rewritten against the new API.

## Scope

`src/agent_manager/control.py`:
- `card_claim(card_id) -> str` returning `"card:<id>"`; `branch_claim(branch) -> str` returning `"branch:<name>"` (the latter is produced here, consumed by sibling 1a3fdd73).
- `Lease(store, *, claims: Sequence[str] = (), clock=_utcnow, heartbeat=HEARTBEAT_SECONDS, pid=None, host=None)`. `__enter__` computes `now = clock()` and calls `store.take_lease(token=..., pid=..., host=..., now=now, is_live=lambda row: lease_is_live(row, now=now), claims=claims)`, sets `self.displaced` from the result (the dead holder taken over, or `None`), then starts the heartbeat. `__exit__` stops the heartbeat, then `store.release_claims(token)`, then `store.release_lease(token)` — only this token's rows — on every exit path including exceptions. `lease_is_live` (control.py:65-74) is reused unchanged; no second liveness mechanism.

`src/agent_manager/cli.py`:
- `class ClaimedError(CliError)` with attributes `.key` and `.run_id`.
- `@contextmanager run_lease(store, *, claims=()) -> Iterator[control.Lease]`: a thin wrapper — enters `control.Lease`, translates `store.LeaseHeldError` to the existing `RunIsLiveError` (cli.py:162) and `store.ClaimHeldError` to `ClaimedError`, and forwards exit (including exceptions) to `Lease.__exit__`.
- `refuse_claimed(root, keys, *, run_id=None) -> None`: read-only preflight over `store.claim_conflicts` filtered by `control.lease_is_live`; raises `ClaimedError` for the first live conflict. Takes no lease, claim or lock and writes nothing. `run_id` excludes the run's own (resumed) rows. The message is built generically from the conflicting key, not hardcoded to "card": split the key on its first `:` into a kind ("card" or "branch") and an id/name, so the same code produces a correct message when sibling 1a3fdd73 later calls `refuse_claimed` with branch claims. Shape: "{kind} {id} is being driven by run {run_id} (pid {pid} on {host}, heartbeat {age}s ago); wait for it, or `am pause {run_id}`" (X11), reusing `_heartbeat_age` (cli.py:1680) for the age.
- `HANDLED` (cli.py:1078-1083) gains `store.LeaseLostError` (a `BaseException`, so it must be listed explicitly). `locks.LockTimeoutError` is already there, from the ProcessLock sibling task; leave it alone.
- `run_card` (cli.py:807-920): after its board reads and before `Store.open`, call `refuse_claimed(root, [card_claim(card.id)])`; right after `Store.open` and before `record_run`, enter `run_lease(store, claims=[card_claim(card.id)])` in place of the current unconditional `control.Lease(store)` (M9 entered the lease after `record_run`).
- `_resume_from_checkpoint` (cli.py:1434-1520): same pattern with the resumable subtask's card — `refuse_claimed(root, [card_claim(subtask.card_id)], run_id=run.id)` after its board reads and before `Store.open`, then `run_lease(store, claims=[card_claim(subtask.card_id)])` in place of the current `control.Lease(store)`. When `lease.displaced` is set, the payload gains `"took_over": {"pid", "host", "heartbeat_at"}` of the displaced holder. M9's C10 read-only check in `resume_run` stays first.
- `control_view` (cli.py:282-311, called from `status_for` at cli.py:1305) gains a `claims: Sequence[str]` parameter and puts it under a new `"claims"` key in its returned dict. `status_for` computes it as `held_claims(conn, wanted, lease.token)` when `lease` is not `None` and `control.lease_is_live(lease, now=now)`, else `()`. `status_payload`'s own fallback dict for "no control" (cli.py:332, `{"lease": None, "requests": []}`) also gains `"claims": []`, so the key is always present either way.

Out of scope: `orchestrate.py`, `milestone_claims`, and `run_milestone`'s use of `run_lease`/`refuse_claimed` (sibling 1a3fdd73); any `store.py`/schema change (a7ed6c11); ProcessLock (sibling ProcessLock task).

## Observable behaviour and error paths

- Envelope unchanged: `{"ok": true, "data": ...}` / `{"ok": false, "error": {"type", "message"}}`, exit 3 on refusal, `--pretty` still works.
- Card held by a live lease: exit 3, type `ClaimedError`, message shaped as `refuse_claimed` builds it (X11, see Scope above). A refusal leaves no run row, journal, fetch, prune or worktree; the only allowed leftover is an empty run directory when preflight passed but `take_lease` lost the race.
- Resume against a live lease: `RunIsLiveError` with M9 C10's message unchanged.
- Holder dead (per `lease_is_live`): no refusal; the lease is taken over and, on resume, reported in `took_over`.
- Lease lost mid-walk: the run stops without writing; `LeaseLostError` reaches the envelope at exit 3 unchanged (it is a `store.py` type, out of scope here), with `error_envelope`'s existing `str(error)` giving `"this process lost the lease of run '<run_id>': pid <pid> on <host> holds it now"` (or `"...: no process holds it now"` with no new holder).
- Claims and lease are released on success, on a failed step, and when the walk raises.
- Readers (`am status`, `am runs`, `am logs`, `am run --dry-run`) never take a lease, claim or lock.
- Lock ordering rules (in-process lock before flock, board and git locks never together, no store transaction under a ProcessLock) must not be violated by the new code.

## Tests

Placement rule (design spec §14 "Testing"): pure functions get plain unit tests; anything touching git or board state is a "Steps" test against real temporary git repos and a temporary brd board, no network, no filesystem mocking; `tests/e2e/` is reserved for the single opt-in real-harness test. None of these belong in e2e. No test sleeps for cross-process ordering; use pipes, marker files, fake-claude rendezvous or exit codes, and children inherit the test `XDG_DATA_HOME`. `_plant_lease` writes `run_leases` and `run_claims` rows over a second `open_db` connection inside `store.immediate` with `heartbeat_at = now`.

`tests/test_control.py`:
- `card_claim` / `branch_claim` return `"card:<id>"` / `"branch:<name>"` — unit (pure functions).
- `Lease` passes `claims`, `now`, and an `is_live` built from `lease_is_live` to `take_lease` and exposes `displaced` — Steps-style default suite (real temp store).
- `Lease.__exit__` releases claims then the lease, only for its own token, also on exception — Steps-style default suite (real temp store).

`tests/test_cli.py` (all Steps-style default suite: real temp git repo, real board, `CliRunner`, fake harness):
- `test_a_card_run_is_refused_while_another_live_run_claims_the_card` — exit 3 `ClaimedError`, message names the other run and pid, no new run, worktree or run directory.
- `test_a_dead_claim_does_not_refuse` — planted pid of a reaped child; the run proceeds.
- `test_the_lease_is_bound_before_the_first_journal_line` — spy on `Store.record_run`: `store._token` is not `None` on its first call.
- `test_a_card_run_releases_its_claims_on_every_exit` — including when the walk raises.
- `test_resume_takes_over_a_dead_lease_and_says_so` — `took_over.pid` equals the dead pid.
- `test_a_lease_lost_mid_walk_is_an_envelope_at_exit_3` — the runner's first phase takes the lease over from a second connection; error type `LeaseLostError`.
- `test_status_lists_the_claims_of_the_live_lease` — `control.claims` lists the keys; `[]` with no live lease.
- `test_readers_never_take_a_lease_or_a_lock` — status/runs/logs/`run --dry-run` with `store.Store.take_lease` and `locks.ProcessLock.acquire` (both already on this base branch) patched to raise all still succeed.

## Verification

`uv run pytest` (full suite; no separate lint or typecheck, per CLAUDE.md).
