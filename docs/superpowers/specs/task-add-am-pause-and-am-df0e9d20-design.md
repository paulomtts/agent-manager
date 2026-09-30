# Subtask df0e9d20: `am pause`, `am cancel`, control in `am status`, guarded `am resume`

Parent story 09a1fc18 (Honour pause and cancel in milestone and card runs), milestone 9 (bdc5838b). This narrows `docs/superpowers/specs/2026-09-27-live-control-design.md` decisions C3, C6, C8-C12 (which is on branch `docs/live-control`, not master) to the CLI layer. It does not add design.

## Scope

- Change only `src/agent_manager/cli.py`. Test only `tests/test_cli.py`.
- Build on the store/control layer that is already in this worktree: `store.run_status`, `store.read_lease` -> `LeaseRow|None`, `store.control_requests(conn, run_id, *, lease=None)` (seq order), `store.immediate(conn)`, `store.add_control(conn, run_id, *, lease, command, requested_at)` (does not commit, so it must run inside `immediate`), and `control.lease_is_live(lease, *, now, host=..., alive=..., stale_after=...)`.
- Do not touch: `orchestrate.py` (sibling 0e1edf31), `run_card`/`_resume_from_checkpoint` Lease/controlled wiring for `--card` runs (sibling 9f5467e3, C11), `control.py`, `store.py`, and the schema. Also out of scope: `am pause --wait`, sockets/fifos/signal handlers, pausing Integrate or one story, and resetting board cards on cancel.
- Add no new runtime dependency. SQLite is the only channel (C1). Nothing journals or rebuilds `run_controls` or `run_leases`.

## Behavior

### New errors

`NotRunningError`, `DeadRunError`, `NotAcceptingError` and `RunIsLiveError` all subclass `CliError`, so they go through the existing `HANDLED` tuple and are reported as `{ok:false, error:{type,message}}` with exit 3.

### `request_control(run_id, command, *, repo_dir, clock=_utcnow) -> dict`

1. Resolve `repo_dir` and open the db with `store_module.open_db`. Close the connection on every path.
2. Run all of the steps below inside one `store_module.immediate(conn)`. Refusals leave no rows.
3. Check in this order (C8), raising the first error that applies:
   1. The run is not in the projection: `UnknownRunError`.
   2. `run_status != "started"`: `NotRunningError`. The message names the actual status and the next step (for example `am resume` or `am status`).
   3. There is no lease, or `control.lease_is_live(lease, now=clock())` is false: `DeadRunError`. The message names the pid, the host and the heartbeat age in seconds (or says there is no lease).
   4. `lease.accepting` is false: `NotAcceptingError`.
4. Idempotence is checked against the rows of this life, meaning `control_requests(conn, run_id, lease=lease.token)`:
   - A `pause` when a `pause` or `cancel` is already recorded is a no-op.
   - A `cancel` when a `cancel` is already recorded is a no-op.
   - A `cancel` after a `pause` is inserted, which upgrades the request.
   - Otherwise, `add_control(..., lease=lease.token, command=command, requested_at=now)`.
5. Return `{run_id, command, effective, requested_at, already_requested, message}`:
   - `effective` is the strongest command recorded for this life (`cancel` > `pause`).
   - `requested_at` is the new row's time, or the existing row's time when the call was a no-op.
   - `already_requested` is a bool.
   - `message` is a one-line human summary.

Rows under older leases do not count toward idempotence, so a resumed run starts clean.

### `am pause RUN_ID` / `am cancel RUN_ID`

- Both take `--repo-dir` (default `.`) and `--pretty`.
- Both follow `resume`'s pattern (cli.py:1477-1519): print `render(ok_envelope(payload))` and exit 0, or on a `HANDLED` error print `render(error_envelope(error))` and exit `EXIT_ERROR` (3).
- A no-op is still exit 0 with `already_requested: true`.

### `am status`

- `status_payload(run, control=None)` gains a `control` key. The key is always present, and `None` input renders as `{"lease": None, "requests": []}`.
- `status_for` (cli.py:1174) reads the key on its own connection, after the run loads and before the connection closes:
  - `lease` is `read_lease`, rendered as `{pid, host, acquired_at, heartbeat_at, accepting, live}` or `null`. Timestamps are ISO strings. `live` is `control.lease_is_live(lease, now=_utcnow())`, computed at read time.
  - `requests` is `control_requests(conn, run_id)` across every life in seq order, rendered as `[{command, requested_at, handled_at|null}]`.
- The shape follows C12 exactly. `status_for` stays read-only.

### `am resume` guard (C9, C10)

In `resume_run` (cli.py:1407), right after the run loads and while the same connection is still open, and before any `Store.open`, checkpoint read or workflow dispatch:

- If `run.status == "cancelled"`, raise `NotResumableError("run was cancelled; start new work with `am run --milestone`")` (the message may be prefixed with the run id). This applies to both the task and milestone workflows.
- If `read_lease` returns a lease and `lease_is_live` is true, raise `RunIsLiveError("run <id> is still running in pid <pid> on <host> (heartbeat <n>s ago); wait for it to exit, or `am status <id>`")`.

Both refusals are read-only: no run directory, row or journal entry is written.

## Test list

All of these tests belong in `tests/test_cli.py`. Section 7 of the live-control spec puts CLI-level refusals, idempotence, status and the resume guard there. They use `CliRunner` against a temporary project, with the run, lease and control rows planted directly through `store.open_db` connections. A "second process" is just a second connection. There are no sleeps, and liveness is made deterministic by planting `heartbeat_at` relative to the injected clock, plus `host`/pid choices (for example the current pid on this host for live, and a stale heartbeat for dead). Engine, milestone and e2e tiers are owned by siblings and the story.

1. Pause on an unknown run id: exit 3, `UnknownRunError`.
2. Pause on a run whose status is not `started` (for example `stopped`): exit 3, `NotRunningError`, and the message names the status.
3. Pause on a started run with no lease, and again with a stale heartbeat: exit 3, `DeadRunError`, and the message names the pid, host and age. No `run_controls` row is written.
4. Pause on a live lease with `accepting=0`: exit 3, `NotAcceptingError`, no row.
5. Pause on a live accepting lease: exit 0. One row is addressed to `lease.token`, with `effective: "pause"` and `already_requested: false`.
6. A second pause is a no-op (`already_requested: true`, still one row). A pause after a cancel is also a no-op.
7. A cancel after a pause inserts a second row, with `effective: "cancel"`. A repeated cancel is a no-op.
8. Rows under an old lease token do not make a pause under the new token a no-op.
9. `--pretty` output parses and has the same envelope for `pause` and `cancel`.
10. `am status` with no lease gives `control == {"lease": null, "requests": []}`. With a planted lease and rows from two lives, it gives the exact C12 keys, `live` true or false as planted, and requests in seq order including `handled_at`.
11. `am resume` on a `cancelled` run (both the task and milestone workflows): exit 3, `NotResumableError` with the C9 message. `Store.open`, `_resume_from_checkpoint` and `orchestrate.run_milestone` are not reached (patched to fail), and no new run directory appears.
12. `am resume` on a run with a live lease: exit 3, `RunIsLiveError` with the pid, host and heartbeat age. It is read-only in the same way as test 11. A dead lease does not block resume.

## Note

The exploration findings handed to this stage were truncated at 8000 characters, cutting off mid-reference in the store.py line list. This spec relies only on signatures that were checked directly in this worktree's `store.py`, `control.py` and `cli.py`.
