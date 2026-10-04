# 2.2 `am runs`: lease (card 6bf47e74)

Parent story: e187d6f8 "Richer `am runs`". Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §1 ("Richer `am runs`"). This document narrows that section to the `lease` key only.

## Prerequisite

This card builds on 2.1 (0b5a15d7, done). 2.1 added `milestone_id` / `card_id` to `RunSummary` and changed `list_runs`. That code lives on branch `ami/task-2-1-am-runs-milestone-0b5a15d7` (commits d33a193, bc33efb, cecc008) and is NOT on master at 09e8b80. The working branch `ami/task-2-2-am-runs-lease-6bf47e74` is based on it (HEAD cecc008, verified). Before editing, run `git log --oneline` to confirm this still holds; if it does not, stop and report the problem. Do not reimplement 2.1.

## Scope

- `RunSummary` (`src/agent_manager/store.py`, `extra="forbid"`) gains one optional field, `lease`, which defaults to `None`, following 2.1's pattern for `milestone_id` / `card_id`. Its value is either `null` or an object with exactly these keys:
  `{ live: bool, pid: int | null, host: str | null, heartbeat_at: str | null, accepting: bool }`
  Use a small pydantic sub-model (also `extra="forbid"`) or an equivalent typed shape that validates under `extra="forbid"`. `LeaseRow` has non-null `pid`, `host` and `heartbeat_at`, so real output always has them populated; the `| null` in the type is the source design's wording. Declare the sub-model fields as `pid: int | None`, `host: str | None`, `heartbeat_at: str | None` to match it.
- `am runs` (`runs_for` in `src/agent_manager/cli.py`) fills `lease` for each run in `data.runs[]`. `store.list_runs` stays unchanged (it returns summaries with `lease=None`). `runs_for` then, for each summary, reads `store_module.read_lease(conn, summary.id)` on the same connection, with one `now = _utcnow()` for the whole listing, and attaches the result with `summary.model_copy(update={"lease": ...})` before `model_dump()`. A nested sub-model instance dumps to a plain dict, so the envelope stays JSON.
- `live` comes from the same computation as `am status`'s `control.lease`, which is `control.lease_is_live(lease, now=now)` (fresh heartbeat within 30s inclusive, and the pid alive or the lease on another host). It is computed at read time and never stored. `heartbeat_at` is emitted as an ISO string (`.isoformat()`), the same as `control_view`. `acquired_at` is not included.
- Extract a small shared helper in `cli.py` that turns a `LeaseRow` plus `now` into the common fields (pid, host, heartbeat_at, accepting, live). Both `control_view`'s `lease` and the runs `lease` use it, so the two outputs cannot drift. `control_view` adds `acquired_at` on top, and its output must not change.
- Layering: `store.py` must not import `control` (`control.py` already imports `store`, so that would be circular). Compute `live` in `cli.py`. `store.list_runs` takes no part in computing `live`.
- README `am runs` section: document `lease` next to 2.1's `milestone_id` / `card_id`, and keep or add the sentence telling consumers to ignore unknown keys.

## Observable behavior

- A run with a lease row has `lease` as the object above. `live` is true or false according to `lease_is_live` at the time of the call.
- A run with no lease row has `lease: null`.
- The change is additive only. The journal stays schema 1. No existing `runs[]` key is renamed, retyped or removed. Existing fixtures without lease rows still produce the same output as before, plus `"lease": null`.
- No error paths are added. A missing lease row is `null`, not an error.

## Non-goals

- `progress` (2.3, 882b212b). Do not add it.
- Redoing `milestone_id` / `card_id` (2.1).
- `--detach`, `logs --follow` and `--from-now` belong to other stories.
- Any change to the lease, claim, control-request or journal-line contracts. Do not change `am status` output either.

## Tests (write first)

All of these read and write SQLite in tmp dirs through injected or planted state, with no subprocess. Under the CLAUDE.md "Test tiers" placement rule they are unmarked `unit` tests.

`tests/test_cli.py` (next to the existing `runs` tests at about lines 4120-4200, using the lease helpers at about lines 6233-6290: `_plant_run`, `_plant_lease`, `_at`, `_freeze_clock`, `CONTROL_NOW`, and the frozen-clock pattern from the status `control.lease` tests at about lines 6649-6720):
1. `runs` live lease: the default `_plant_lease` (this pid and host, fresh heartbeat) gives `lease == {live: true, pid, host, heartbeat_at: <iso str>, accepting}` with exactly those keys. Tier: unit.
2. `runs` dead lease: a stale heartbeat (`_at(-31)`) and/or a dead pid on the same host gives `live: false`, with the other fields still populated. Tier: unit.
3. `runs` no lease row: `lease is None` (JSON `null`). Tier: unit.
4. Parity: for the same planted lease and frozen clock, the `am runs` lease equals `am status`'s `control.lease` minus `acquired_at`. Tier: unit.
5. Shape pin: the `runs[]` lease key set is exactly `{live, pid, host, heartbeat_at, accepting}`, placed next to the existing contract or shape tests. The existing `runs` tests keep passing, with `lease` null on old fixtures. Tier: unit.

`tests/test_store.py` (the list_runs tests at about lines 1265-1340, `_record_summary`):
6. `RunSummary` accepts `lease=None` (the default) and a valid lease object, and rejects an unknown key inside `lease` (`extra="forbid"`). Tier: unit.
