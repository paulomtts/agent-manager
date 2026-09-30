# Take the lease atomically with claims, and fence every run write (a7ed6c11)

Subtask of story f67dc4e4 ("Leases that own runs, cards and branches"), milestone 619c2a8e. This is Task 2.1 of `docs/superpowers/plans/2026-09-27-multi-process.md`, governed by `docs/superpowers/specs/2026-09-27-multi-process-design.md` (X4 fencing, X5 claims, X9 `BEGIN IMMEDIATE`). Branch prefix `m10`, base `master`. M9 is already merged, and a code read found no drift from its design.

Note on inputs: the exploration summary given to this stage was truncated at 8000 of 11924 characters, partway through the test list. That means the upstream stage over-ran its brief. I recovered the missing test names and the test-tier rule directly from the plan (Task 2.1, lines 213-314) and the design (§7 "Store"). I did not guess at the rest of the lost text.

## Scope

Only `src/agent_manager/store.py` changes, and its tests go in `tests/test_store.py`. The one possible exception is described under "Open point". Out of scope:
- `control.Lease.__enter__` switching to `take_lease` with real liveness and claims belongs to sibling ec7ae954.
- Adding `LeaseLostError` to `cli.HANDLED` belongs to another subtask.
- Card/branch claim keys for `--card` runs belong to Task 2.2.

`store.py` must not import `control`. Liveness is injected as the `is_live: Callable[[LeaseRow], bool]` argument. This subtask reuses M9's `run_leases`, `LeaseRow`, `_lease_from_row`, `read_lease`, `immediate` and `beat`/`close_window`/`release_lease` without changing them. It adds no new staleness rule and no pid files.

## Observable behaviour

**Schema.** `_SCHEMA` gains `CREATE TABLE IF NOT EXISTS run_claims (key TEXT PRIMARY KEY, run_id TEXT NOT NULL, token TEXT NOT NULL, claimed_at TEXT NOT NULL)`, placed after `run_leases`. It holds rows only and never appears in the journal. No existing table gains a column, and no CHECK constraint changes. The `BUSY_TIMEOUT_SECONDS` docstring stops calling two writers "unsupported (P2)".

**Types.**
- `ClaimRow(key, run_id, token, claimed_at: datetime)` is a frozen dataclass.
- `LeaseTake(lease: LeaseRow, displaced: LeaseRow | None)` is a frozen dataclass.
- `LeaseHeldError(RuntimeError)` carries `.holder: LeaseRow`.
- `ClaimHeldError(RuntimeError)` carries `.key: str` and `.holder: LeaseRow`.
- `LeaseLostError(BaseException)` carries `.run_id: str` and `.holder: LeaseRow | None`. It deliberately does not derive from `Exception`, so no `except Exception` can swallow it.

**Free functions.**
- `claim_conflicts(conn, keys, *, is_live, run_id=None) -> list[tuple[str, LeaseRow]]` is read-only. A key conflicts only when all three of these hold:
  - its `run_claims` row exists and has a `run_id` different from the argument;
  - that run's `run_leases` row carries the claim's token;
  - `is_live(lease_row)` is true.
- `held_claims(conn, run_id, token) -> list[ClaimRow]` returns rows in key order.

**`Store.take_lease(*, token, pid, host, now, is_live, claims=()) -> LeaseTake`** runs under `self._lock` inside one `immediate(self._conn)` transaction, in this order:
1. **Check the current lease.** If this run's lease row has another token and `is_live` is true for it, raise `LeaseHeldError(row)`. Otherwise that row, or `None`, becomes `displaced`.
2. **Check claims.** Call `claim_conflicts(conn, claims, is_live=is_live, run_id=self.run_id)`. If there is a conflict, the first one raises `ClaimHeldError(key, holder)`.
3. **Write and commit.** Upsert the lease with `accepting=1` and `acquired_at=heartbeat_at=now`. Upsert every claim with this `run_id`, `token` and `claimed_at=now`. Commit.

After the commit, `take_lease` calls `bind_lease(token)` and then `self._journal.reseek()`. The outcome is all-or-nothing. On any raise it writes no lease row, no claim row and no journal line, and the bound token stays unchanged. A key already recorded under this run's own id is rewritten under the new token (the resume case). A claim held under a dead lease is overwritten.

**Other `Store` / `Journal` additions.**
- `Store.bind_lease(token: str | None)` sets or clears the fencing token.
- `Store.release_claims(token)` deletes only the rows carrying that token.
- `Journal.reseek()` re-reads the highest `seq` under the journal lock. This lets the new owner continue the sequence after lines a stuck previous owner appended once `Journal.__init__` had already cached `_seq`.
- `Store.acquire_lease` is removed. The comment at store.py:1137 that mentions it gets updated.

**Fencing (X4, X9).**
- **Which writes are fenced.** With a token bound, each of these runs its whole body inside `with self._lock, self._fenced():`: `record_run`, `record_story`, `record_subtask`, `record_phase`, `record_attempt`, `save_checkpoint` and `rebuild_from_journal`.
- **What `_fenced()` does.** It opens `immediate`, reads this run's `run_leases` row and raises `LeaseLostError(self.run_id, holder_or_None)` if the row is missing or has a different token. Otherwise it sets `self._in_fence` for the duration of the body and resets it in a `finally`.
- **Commits.** Every `_write_*_row`, and `_delete_run` (which currently calls `self._conn.commit()` at store.py:1261), commits through `self._commit()`. `self._commit()` does nothing inside a fence, so the journal append and the row write commit atomically with the fence check. Outside a fence it commits normally.
- **Journal order.** The journal line is appended inside the fence, before the row. A store that has lost its lease appends nothing.
- **Unbound stores.** With no token bound, `_fenced()` is a no-op and every write behaves exactly as it does today.

## Error paths

| Condition | Result |
|---|---|
| A live foreign lease exists | `LeaseHeldError`; nothing written |
| Any claim key is held by another run under a live lease | `ClaimHeldError`, naming the first conflicting key; the lease and every other claim are rolled back |
| A write from a bound store whose lease was taken over or deleted | `LeaseLostError`, raised before anything is appended or written; the journal file is byte-identical |
| The database is locked for longer than the busy timeout | `sqlite3.OperationalError` propagates, unhandled, as today |

## Open point: `control.Lease` still calls `acquire_lease`

`control.Lease.__enter__` (src/agent_manager/control.py:108) still calls `self._store.acquire_lease(...)`. The plan removes `acquire_lease` in this task and changes `control.py` only in sibling ec7ae954. But this task must also leave `uv run pytest` green on its own. Removing the method without touching `control.py` would break every `Lease` user.

The narrowest fix that satisfies both constraints is to change that single call in this subtask to `take_lease(token=..., pid=..., host=..., now=..., is_live=lambda row: False)` and use its `.lease`. This keeps M9's unconditional-replace semantics. The call would now bind the token, which fences that store's writes to the lease's own token, and those writes still succeed. ec7ae954 then replaces the call with real liveness and claims.

The planning stage must confirm that this edit is acceptable and that the full suite stays green under the binding. If it is not acceptable, keep `acquire_lease` temporarily and leave its removal to ec7ae954.

## Tests (all in `tests/test_store.py`)

**Tier.** Every test below is in the **Steps** tier of the base design §14 (`2026-09-23-agent-manager-design.md:505-520`). They run against a real temporary SQLite database and journal, with no network and no harness dispatch. They are not engine-parametrised. Multi-process design §7 adds one requirement: a "second process" in store tests is a real `subprocess.Popen([sys.executable, "-c", ...])` child, and ordering is proven with pipes, never with sleeps.

- `test_take_lease_refuses_a_live_foreign_lease`: t1 holds a live lease, so t2 gets `LeaseHeldError` and `.holder.token == "t1"`.
- `test_take_lease_takes_over_a_dead_lease`: the result has `displaced.token == "t1"` and `lease.token == "t2"`.
- `test_claims_are_all_or_nothing`: `ClaimHeldError.key == "card:x"`. Afterwards run-b has no lease row, and `held_claims` for run-b is empty, because `card:y` was rolled back too.
- `test_a_claim_under_a_dead_lease_is_overwritten`.
- `test_a_resume_rewrites_its_own_runs_claims`: same run id; after t1 dies, t2 owns the keys.
- `test_claim_conflicts_is_read_only_and_ignores_the_runs_own`.
- A `release_claims` test that checks it deletes only its own token's rows. The design §7 requires this behaviour, but the plan gives no test name for it.
- `test_a_taken_over_store_writes_nothing`: `record_run`, `record_subtask` and `save_checkpoint` each raise `LeaseLostError`, and the journal bytes are unchanged.
- `test_the_new_owner_continues_the_sequence`: b's first write after taking over gets `seq == 2`, because `reseek` picks up line 1, which a wrote after b opened its store.
- `test_an_unbound_store_writes_as_before`.
- `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner`:
  - It is parametrised with `attempt` in `range(20)`.
  - Two real child processes use a ready/go rendezvous over pipes, with `is_live=lambda row: row.token != "t0"`.
  - The sorted outcomes are `["LeaseHeldError", "took"]`.
- The three existing tests that call `acquire_lease` switch to `take_lease(..., is_live=lambda row: False)`, with their assertions otherwise unchanged: `test_a_lease_is_touched_only_through_its_own_token` (line 2121), `test_reacquiring_a_lease_replaces_the_old_token_and_other_runs_are_untouched` (line 2160), and the `acquire_lease` call inside `test_a_cancelled_run_round_trips_through_the_journal_and_the_listing` (line 2394). No other test in the file calls `acquire_lease`; in particular the tests in the 2191-2351 range (`test_a_request_from_another_connection_is_pending_for_its_lease_only`, `test_control_seqs_are_numbered_and_handled_per_run`, the `test_immediate_*` tests) use `lease="t1"` only as a `run_controls` column value and are unrelated to `acquire_lease`/`take_lease`.

Verification: `uv run pytest` passes for the whole suite, `tests/e2e` included.
