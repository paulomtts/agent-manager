# Remove cost/token tracking — design

Date: 2026-10-03
Status: approved design, pre-implementation

## 1. Purpose

An evaluation of `am`'s recorded runs found that cost accounting is dead:
`attempts.cost`, `tokens_in` and `tokens_out` are NULL on every recorded
attempt, and no `stdout.log` on disk carries an `input_tokens` or `cost`
figure. The mechanism is intact end to end — `ClaudeAdapter.parse_usage`
(`harness/claude.py:158`) is a well-tested regex scanner, the engine calls it
on every attempt (`dispatch.py:555`), and the three numbers it would produce
have a column each in the projection and a key each in every
`attempt_upsert` journal line — but `build_command` (`harness/claude.py:124`)
never asks `claude -p` for `--output-format json`, so the log it scans never
contains the data. Re-checked for this spec: the 13 runs under
`~/.local/share/agent-manager/runs/` hold 311 `attempt_upsert` lines, every
one of which carries all three keys, and every one of which carries them as
`null`.

The evaluation's suggested fix is a one-liner. This spec does the opposite,
deliberately. The project owner has decided that cost and token accounting
is **not a concern of `am`**: the tool's job is to drive a milestone to a
verified branch, and what a harness bills for doing so is the harness
vendor's ledger, not `am`'s. A feature that has never produced a value, that
every caller already tolerates the absence of, and that the owner does not
want, is dead code with a maintenance surface — and the right move is to
remove it cleanly, not to wire it up.

This is a reversal of a stated motivation in the original design spec, not
an oversight. `2026-09-23-agent-manager-design.md:27` lists "**Cost and
latency.** Deterministic steps stop costing tokens" as motivation one, and
§17 (`:560-562`) carries "Cost accounting on non-Claude harnesses" as an
open question. The motivation still holds — deterministic steps still cost
no tokens — but it was a reason to build the engine, never a promise that
the engine would *report* a bill. The open question is closed by this spec:
`am` keeps no per-phase cost, so there are no gaps to live with. Future
readers finding no `cost` field should read this spec, not file a regression.

## 2. Scope

**In scope.** Removing every field, type, method, column, journal key and
test that exists only to carry cost or token counts: `Attempt.tokens_in`,
`Attempt.tokens_out`, `Attempt.cost`; `harness.base.Usage`;
`HarnessAdapter.parse_usage` and its Claude implementation with its regex
helpers; `dispatch._usage` and the copy into the journalled attempt; the
three `attempts` columns in `_SCHEMA` and their reads and writes; the
documentation lines that promise these fields. Plus the one
compatibility change the removal forces on the journal *reader* (4.3), so
the 311 existing lines, and every journal any other machine has written,
still replay.

**Kept, explicitly.** `Attempt.duration` (`models.py:85`) and
`Outcome.duration` (`harness/base.py:71`). Duration is wall-clock time the
launcher measures itself (`harness/launcher.py`), is populated on every
attempt today, and is latency, not cost. It stays, as does `exit_code`.
Nothing about `stdout.log` being captured changes: the log is still written
by the launcher and still served by `am logs` (`cli.py:436`). What changes is
that nothing in the engine reads it any more.

**Out of scope.** The other three findings of the same evaluation —
worktree re-ensure on resume, an `am reset` command, journal/DB divergence
detection — are separate work with no dependency on this removal in either
direction. This spec touches no resume logic beyond the reading shim in 4.3,
adds no command, and changes no projection/journal reconciliation.

**Never** (unchanged). Nothing here pushes, opens PRs or touches
`main`/`master`; `D4` ("stdout is a log, not a channel") is strengthened,
not weakened — see 4.4.

## 3. Decisions

### 3.1 What exists: the complete inventory

Every line below was read for this spec. "Prose only" means the word
`cost` appears in a docstring or comment about something unrelated (a
dispatch "costing" a model call, a check "costing" nothing) and is **not**
part of this feature; it is listed so nobody greps for it twice.

**Source — the feature itself**

| File:lines | What | Fate |
|---|---|---|
| `src/agent_manager/models.py:86-88` | `Attempt.tokens_in`, `tokens_out`, `cost` fields (`int \| None, ge=0` ×2; `float \| None, ge=0, allow_inf_nan=False`) | delete |
| `src/agent_manager/models.py:15` | module docstring: "no exit code, duration, tokens or cost" | reword to "no exit code or duration" |
| `src/agent_manager/harness/base.py:31-50` | `class Usage(BaseModel)` — frozen, `extra="forbid"`, the same three fields | delete |
| `src/agent_manager/harness/base.py:98` | `HarnessAdapter.parse_usage(self, stdout: str) -> Usage \| None` | delete (4.4) |
| `src/agent_manager/harness/base.py:4, 11-14, 16-19, 88-89` | docstrings describing `Usage` and `parse_usage` | reword; `Outcome`'s "no parsed usage" sentence becomes "no log contents" |
| `src/agent_manager/harness/base.py:26` | `from pydantic import BaseModel, ConfigDict, Field` — only `Usage` uses it | delete the import |
| `src/agent_manager/harness/claude.py:44-102` | `_key`, `_VALUE`, `_TOKENS_IN`, `_TOKENS_OUT`, `_COST`, `_tokens`, `_cost` — the regex scanner and its two converters | delete |
| `src/agent_manager/harness/claude.py:158-188` | `ClaudeAdapter.parse_usage` | delete |
| `src/agent_manager/harness/claude.py:18-23` | `import math`, `import re`, `from pydantic import ValidationError`, `from agent_manager.harness.base import Usage` — all unused once the above goes | delete |
| `src/agent_manager/harness/claude.py:13-15, 106` | docstrings: "`parse_usage` never raises…", "reads usage back out" | reword |
| `src/agent_manager/harness/launcher.py:10` | docstring: "never parses the log it wrote (that is `parse_usage`'s job, on the adapter)" | reword: "never parses the log it wrote (nothing does: D4)" |
| `src/agent_manager/dispatch.py:705-716` | `_usage(adapter, outcome) -> Usage \| None` — reads `stdout.log` and calls `parse_usage` | delete |
| `src/agent_manager/dispatch.py:555, 564-566` | `usage = _usage(...)`; the three keyword args into `models.Attempt(...)` | delete |
| `src/agent_manager/dispatch.py:36` | `from agent_manager.harness.base import HarnessAdapter, Outcome, Usage` | drop `Usage` |
| `src/agent_manager/dispatch.py:14-15` | docstring: "`stdout.log` is captured as a log and is only ever handed to `parse_usage`, never parsed for a result" | reword: "captured as a log and never read by the engine" |
| `src/agent_manager/store.py:89-91` | `attempts` columns `tokens_in INTEGER`, `tokens_out INTEGER`, `cost REAL` in `_SCHEMA` | remove from `CREATE` (4.2) |
| `src/agent_manager/store.py:717-719` | `load_run` passes the three columns into `models.Attempt(...)` | delete the three lines |
| `src/agent_manager/store.py:1396, 1399, 1405-1407, 1422-1424` | `_write_attempt_row`: the three column names in `INSERT`, `VALUES`, `ON CONFLICT … SET`, and the parameter dict | delete |
| `src/agent_manager/store.py:526-532, 581` | `replay`: `models.Attempt.model_validate(line.payload)` under a "nothing here is defensive" docstring | **change, keep** (4.3) |

**Source — prose only, not this feature (no change)**

`prompt.py:239` ("costs it one read"), `steps/reducers.py:177, 243` ("costs
no dispatch", "costs no command"), `steps/plan_check.py:9, 26` ("cost a
dispatch every run", "cost of a check"), `cli.py:115` ("would cost an
operator that list"). `cli.py` never renders `cost`/`tokens_*`: `status`
rows (`cli.py:219-244`) carry `story/subtask/phase/attempt/state`, and
`logs_payload` (`cli.py:425-441`) carries `status`, `exit_code` and the
artifacts. No CLI output changes.

**Documentation**

| File:lines | What | Fate |
|---|---|---|
| `README.md:500` | `attempt_upsert` row: "with its cost, token and duration fields" | "with its `exit_code` and `duration`" |
| `docs/superpowers/specs/2026-09-23-agent-manager-design.md:366` | §9 tree: `n, exit_code, duration, tokens_in, tokens_out, cost` | drop the last three, with a one-line pointer to this spec |
| `docs/superpowers/specs/2026-09-23-agent-manager-design.md:560-562` | §17 open question "Cost accounting on non-Claude harnesses" | mark resolved by this spec (closed: `am` keeps no cost) |
| `docs/superpowers/specs/2026-09-23-agent-manager-design.md:27` | motivation 1, "Cost and latency" | **keep as is** — it motivates deterministic steps, not reporting; §1 above records the reversal |
| `docs/superpowers/specs/2026-10-02-am-watch-design.md:59` | event table row: "with cost/token/duration fields" | leave: a dated, approved spec is history; the README carries the live contract |
| `docs/superpowers/specs/task-*-design.md`, `docs/superpowers/plans/task-*.md` (e.g. `task-add-the-run-state-models-1535b285-design.md:22, 27, 39, 50`, `task-add-the-harness-adapter-55e503e0-design.md`, `task-add-the-claude-adapter-9a2524a8-design.md`) | per-card specs and plans naming the fields | leave: card history, not current truth |

**Tests**

| File:lines | What | Fate |
|---|---|---|
| `tests/harness/test_base.py:34-35, 96` | `StubAdapter.parse_usage`, and the assertion it returns `None` | delete |
| `tests/harness/test_base.py:50-59` | protocol declares exactly four members `{name, capabilities, build_command, parse_usage}` | change: exactly three |
| `tests/harness/test_base.py:71-74` | `parse_usage` signature assertion | delete |
| `tests/harness/test_base.py:107-156` | seven `Usage` tests (`round_trips`, `is_empty`, `fields_match_attempt`, `is_frozen`, `rejects_negative`, `rejects_inf_nan`, `rejects_unknown_key`) | delete |
| `tests/harness/test_base.py:201-205` | `test_outcome_carries_no_result_payload_and_no_usage` | keep; rename `…_and_no_log_contents`, reword the comment at `:202` |
| `tests/harness/test_claude.py:8, 22, 52` | docstring, `Usage` import, `callable(adapter.parse_usage)` | delete/reword |
| `tests/harness/test_claude.py:195-297` | `FULL_LOG` and ten `parse_usage` tests (`full_usage_report`, `no_usage`, `partial`, `last_report_wins`, `no_key_outside_the_three`, `hostile_logs_never_raise` ×7 params, `cache_counters`, `multi_megabyte`, `crlf_and_human_spelling`) | delete |
| `tests/harness/test_claude.py:300-379` | import-audit (`_imported_names`, `FORBIDDEN_IMPORTS`) | keep; unaffected (`re`/`math` were never forbidden, and removing them cannot fail it) |
| `tests/test_models.py:111-113` | in-flight attempt asserts the three are `None` | delete the three lines |
| `tests/test_models.py:124-126, 529-531` | fixtures passing `tokens_in=…, tokens_out=…, cost=…` | delete the kwargs |
| `tests/test_models.py:163-172` | `test_attempt_rejects_negative_usage_numbers` | keep `duration` only; rename `…_negative_duration` |
| `tests/test_models.py:174-181` | `test_attempt_rejects_nan_and_infinite_usage_numbers` | keep `duration` only; rename; drop the `cost=bad` branch |
| `tests/test_models.py:588` | `assert attempt.cost is None` | replace with `attempt.duration is None` |
| `tests/test_store.py:660, 676` | `cost=0.42` on the recorded attempt; `attempt_row["cost"] == approx(0.42)` | replace with `duration=12.5` / `attempt_row["duration"] == approx(12.5)` |
| `tests/test_store.py:813-815, 861, 868` | `_record_full_run` kwargs; `finished.cost == approx(0.31)`; `in_flight.cost is None` | drop kwargs; assert `duration` instead |
| `tests/test_store.py:954-956` | rebuilt in-flight attempt asserts the three are `None` | delete |
| `tests/test_dispatch.py:33` | `from agent_manager.harness.base import Outcome, Usage` | drop `Usage` |
| `tests/test_dispatch.py:218-219` | `FakeAdapter.parse_usage` returning `Usage(11, 22, 0.5)` when `"usage"` is in the log | delete |
| `tests/test_dispatch.py:298, 331` | `FakeLauncher.stdout = "usage: tokens\n"`; `_outcome` writes the same | change to a neutral line (`"fake-harness ran\n"`) — the string was bait for `parse_usage` |
| `tests/test_dispatch.py:659-675` | `test_usage_parsed_from_the_log_is_journalled_on_the_attempt` | replace (5.2) |
| `tests/test_integrate_workflow.py:192-193`, `tests/test_bases.py:351-352`, `tests/test_integration.py:259-260`, `tests/test_engine.py:2148-2149`, `tests/runtime/test_exactly_once.py:132-133` | `parse_usage` stubs on five fake adapters | delete |
| `tests/e2e/fake_claude.py:820-822` | comment: "Usage-free on purpose: the adapter scans it with `parse_usage`…" | reword: "stdout is a log, never a channel (D4); nothing reads it" |

Nothing in `tests/test_cli.py`, `tests/e2e/`, or `tests/test_watch*.py`
asserts on `cost`/`tokens_*` keys (grepped; the only `usage` hits are
Typer "usage error" tests).

### 3.2 Delete versus change-and-keep

Everything in 3.1 marked *delete* goes without replacement. Three places are
*changed and kept* because something else depends on the surrounding
structure:

1. **`store.replay` (`store.py:581`)** — must learn to drop three retired
   keys before validating (4.3). This is the only behavioural change in the
   spec and the only reason it is more than a deletion.
2. **`store._SCHEMA` (`store.py:80-97`)** — the `CREATE` loses three columns;
   `_add_missing_columns` and `_ADDED_COLUMNS` are untouched (4.2).
3. **`HarnessAdapter` (`harness/base.py:75-98`)** — loses a member, which
   changes the structural contract every fake adapter in the tests is
   written against (4.4). Six stubs lose a method; none loses behaviour.

`Attempt` itself can simply lose the three fields. Nothing constructs an
`Attempt` with them except `dispatch.py:558` (changed), `load_run`
(changed), tests (changed), and `replay` on *old* journal lines (the shim).
`models._Model` is `extra="forbid"` (`models.py:55`) and `Attempt` keeps
that: the shim strips exactly three named keys, so any *other* unexpected
key on an attempt payload still fails loudly, as the docstring at
`models.py:50-52` promises.

### 3.3 Why the journal needs a reading shim, precisely

`Attempt` inherits `extra="forbid"`. `Journal.read` (`store.py:431-444`)
validates only the `JournalLine` envelope; `payload` is `dict[str, Any]`
(`store.py:305`) and passes through untouched — the am-watch tolerant
reading (its §3.4) covers an unrecognized `event` kind via
`_UnknownEventLine` (`store.py:311-323`), and its docstring is explicit that
"unknown keys inside a known line's payload pass through untouched: `replay`
judges payloads" (`store.py:436-437`). So `am watch` keeps working on old
journals with no change: it emits the stored dict, retired keys and all.

`replay` (`store.py:526`) is where the old lines would break. It calls
`models.Attempt.model_validate(line.payload)` on every `attempt_upsert`
line, and every one of the 311 lines on disk carries `"cost": null,
"tokens_in": null, "tokens_out": null`. Under `extra="forbid"` with the
fields gone, each raises `ValidationError`. Two production paths reach it:

- `Store.rebuild_from_journal` (`store.py:1837`) — the projection rebuild
  D5 promises ("the projection can be thrown away and rebuilt",
  `store.py:1-7`). Every existing run would become unrebuildable.
- `Store.replay_journal` (`store.py:1880`), called by resume adoption at
  `dispatch.py:645`. That caller catches `ValidationError` and **silently
  declines** adoption with the warning "its journal cannot be read" —
  meaning every resume of a run recorded before this change would
  re-dispatch every phase instead of adopting its recorded `ok` attempts.
  Not a crash; a quiet, expensive regression in exactly the behaviour
  (§9 resume) the project is most careful about.

The precedent for the fix is already in `models.py`: `Dispatch.timeout`
(`models.py:67-75`) and `Run.milestone_id` carry defaults *because* old
lines lack the key and `extra="forbid"` would reject a new required field
(`tests/test_models.py:77-84, 309-329`). This is the mirror case — an old
line has a key the new model lacks — and the mirror fix is to strip it at
the one boundary that validates payloads.

## 4. Decisions, stated

### 4.1 Remove the fields, the type, the method and the parser outright

No deprecation period, no `Optional` placeholders left behind. The feature
has produced no value in 311 recorded attempts; there is nothing to
deprecate gracefully. `Attempt` keeps `n, dispatch, status, exit_code,
duration, prompt_path, result_path, stdout_path`.

### 4.2 The three SQLite columns: drop from `_SCHEMA`, never `ALTER … DROP`

`open_db`'s docstring (`store.py:235-253`) states the migration policy: "the
only migration is additive." `_add_missing_columns` (`store.py:219-232`)
adds columns an older table lacks and "touches nothing else." This spec
keeps that policy. It does **not** add a `DROP COLUMN` step, for three
reasons: SQLite's `DROP COLUMN` (3.35+) refuses on several constraint
shapes and would be the store's first destructive migration; the projection
is disposable by design (D5), so a stale column costs nothing anyone
queries; and nothing reads the columns by name once `load_run` stops.

What happens, concretely:

- **Fresh database.** `_SCHEMA`'s `CREATE TABLE attempts` (`store.py:80-97`)
  no longer lists `tokens_in`, `tokens_out`, `cost`. The table has twelve
  columns.
- **Existing database.** `CREATE TABLE IF NOT EXISTS` leaves the old
  fifteen-column table alone. `_write_attempt_row` names its columns
  explicitly in both `INSERT` and `ON CONFLICT … SET`, so an insert that no
  longer mentions the three columns leaves them `NULL` — which is the value
  every row already holds. `load_run` does `SELECT *` and reads columns by
  name, so three extra columns in the row are simply never read. No
  migration runs, no error, no data changes.
- **`_ADDED_COLUMNS`** (`store.py:207-210`) lists only `phases.detail` and
  `runs.milestone_id`; `attempts` has never had a late-added column, so the
  "added column must be last in `CREATE`" invariant is not in play.

The one cost of this choice is a *downgrade* edge: an older `am` opening a
database first created by the new code would fail its own `INSERT` (it
names columns that do not exist). Mixed `am` versions sharing one project
database is not a supported configuration anywhere in the README's
"Several am processes" section, and the recovery is the one D5 already
provides: delete the projection file and rebuild. Recorded in §6.

### 4.3 The journal reading shim: strip three named retired keys in `replay`

In `store.replay`, immediately before
`models.Attempt.model_validate(line.payload)` (`store.py:581`), remove the
keys `tokens_in`, `tokens_out` and `cost` from the payload if present,
regardless of their value. Spelled as a module constant alongside
`_EVENT_KINDS`:

```
_RETIRED_ATTEMPT_KEYS: frozenset[str] = frozenset({"tokens_in", "tokens_out", "cost"})
```

with a docstring naming this spec and the reason (every journal written
before 2026-10-03 carries them, always null). The stripping is for
`attempt_upsert` payloads only; `run_upsert`, `story_upsert`,
`subtask_upsert` and `phase_upsert` never carried them and keep strict
validation. Any other unexpected key on an attempt payload still raises —
the shim is a named allow-list, not `extra="ignore"`. `replay`'s docstring
("nothing here is defensive", `store.py:529-531`) gets one sentence: the
one exception is the retired-key allow-list, and why.

Non-null values are stripped too, not rejected: no journal on record holds
one (verified: 0 of 311), and a hypothetical foreign journal that did would
be reporting something `am` no longer models — dropping it is the
documented behaviour for a retired field, and raising would only make that
journal unrebuildable for no benefit.

`Journal.append` keeps writing strictly: `Attempt.model_dump()` no longer
has the keys, so no new line carries them, and a caller that tried to
append a payload with them would be stopped by `Attempt`'s own
`extra="forbid"` wherever that payload is next validated.

The public contract in `README.md:504-510` ("the journal line is a public
contract, version 1") says a consumer must ignore any `payload` key it does
not recognize. Removing a key is formally the other direction — a key a
consumer *might* have recognized goes away. It is accepted here without a
schema bump because the key never once carried a non-null value, so no
consumer can have been using it for anything but `None`. §6 records the
reasoning.

### 4.4 `parse_usage` leaves the adapter protocol entirely

Three choices were on the table: keep `parse_usage` as a no-op hook for a
future Codex or Pi adapter (README `:24` names both as planned); keep it
and have `ClaudeAdapter` return `None`; or remove it from the Protocol.
The decision is to remove it.

- A Protocol member nothing calls is a contract nothing can verify. `base.py`
  is explicit that the Protocol is "pure interface and pure data" with "no
  default implementations" (`harness/base.py:78-80`), and
  `tests/harness/test_base.py:50-59` pins the exact member set precisely so
  a sibling adapter cannot drift. A retained-but-dead member would be pinned
  there as a lie.
- D4 gets *stronger*. Today the engine honours "stdout is a log, not a
  channel" with one carve-out: `dispatch._usage` opens the log and hands it
  to the adapter. After this spec, nothing in `src/` reads `stdout.log`
  except `am logs` showing it to an operator. `Outcome` already carries no
  log contents (`harness/base.py:16-19`, pinned by
  `tests/harness/test_base.py:201-205`), so the launcher/engine/adapter
  split loses nothing.
- Codex and Pi do not exist as adapters. If a future adapter has a reason
  to read its harness's log, that is a new design with a new consumer —
  it should not be built on a vestigial method whose only implementation
  was a regex over a log that never contained the data.

The protocol becomes `name`, `capabilities`, `build_command`. The six fake
adapters in the tests drop their stub.

### 4.5 `duration` stays, and is the replacement assertion

Every test that today proves "a terminal attempt carries its usage" is
really proving "the engine copies the launcher's `Outcome` onto the
journalled attempt." `Outcome.duration` is the field that still makes that
copy observable (`dispatch.py:563`), alongside `exit_code`. Tests that lose
their `cost` assertion assert `duration` instead, rather than losing
coverage of the copy.

## 5. Testing

The default `uv run pytest` tiers (`unit` + `git`) cover everything here;
nothing new spawns a process.

### 5.1 Deleted

Exactly the rows marked *delete* in 3.1's test table. The headline
casualties are the ten `parse_usage` tests and `FULL_LOG` in
`tests/harness/test_claude.py:195-297`, the seven `Usage` tests in
`tests/harness/test_base.py:107-156`, and
`tests/test_dispatch.py:659-675`.

### 5.2 Replaced or rewritten

- `tests/harness/test_base.py:50-59` — the protocol declares exactly
  `{name, capabilities, build_command}`.
- `tests/test_dispatch.py:659` → `test_the_outcome_is_journalled_on_the_attempt`:
  the terminal `attempt_upsert` payload carries `duration == 1.25` and
  `exit_code == 0`, and `set(payload) & {"tokens_in", "tokens_out", "cost"}`
  is empty.
- `tests/test_models.py:163-181` — negative / inf / nan rejection kept for
  `duration` only.
- `tests/test_store.py:656-676, 839-868, 933-962` — `duration` in place of
  `cost` in both the recorded fixture and the assertions, so
  `record_attempt` → row → `load_run` → `rebuild_from_journal` are still
  each shown to carry a terminal numeric field through.

### 5.3 New

1. **Retired keys replay** (`tests/test_store.py`, `git` tier like its
   neighbours): write a journal by hand whose `attempt_upsert` line carries
   `"cost": null, "tokens_in": null, "tokens_out": null` (the exact shape on
   disk today, copied from a real line) and a second with non-null values;
   `rebuild_from_journal` and `replay_journal` both return the tree with
   the attempt intact and no such attributes on it. A third line with an
   unrelated unknown key (`"operator": "x"`, mirroring
   `tests/test_store.py:583-585`) still raises `ValidationError` naming
   that key — proving the allow-list is three names, not `extra="ignore"`.
2. **Resume adoption survives an old journal** (`tests/test_dispatch.py`,
   `unit` tier via `FakeLauncher`): a source run whose journal has retired
   keys on an `ok` attempt is still adopted, not declined with "its journal
   cannot be read". This is the regression 3.3 identifies and is the one
   test that would have caught a naive deletion.
3. **Model shape** (`tests/test_models.py`): `set(models.Attempt.model_fields)
   == {"n", "dispatch", "status", "exit_code", "duration", "prompt_path",
   "result_path", "stdout_path"}`.
4. **Fresh `attempts` columns** (`tests/test_store.py`, next to
   `test_a_fresh_phases_table_carries_detail_as_its_last_column` at
   `:1927`): `PRAGMA table_info(attempts)` lists twelve names and none of
   the three.
5. **Legacy `attempts` table still writes and loads** (`tests/test_store.py`,
   the `_LEGACY_PHASES` pattern at `:2008-2070`): recreate `attempts` with
   the fifteen-column shape, `record_attempt` into it, `load_run` back;
   the three columns read `NULL` and nothing raises on open, write or load.
6. **The engine never opens the log** (`tests/test_dispatch.py`): a
   `FakeLauncher` whose `stdout_path` is written then made unreadable (or
   simply absent — `Outcome.stdout_path` pointing at a file the launcher
   did not create) still yields an `ok` attempt. Today `_usage` swallows
   `OSError`; after removal there is no read to swallow, and this pins that.
7. **Import hygiene** (`tests/harness/test_claude.py`, alongside the
   existing import audit): `claude.py` imports neither `re` nor `math` nor
   anything from `pydantic` — cheap, and it stops the parser being quietly
   reintroduced.

## 6. Risks

- **A journal from a newer `am` that re-adds a usage key.** Not a risk
  this spec creates: any new key on an attempt payload fails `replay`
  loudly today and will tomorrow. The allow-list is for the three names
  that *were* written, not for whatever might be.
- **Public-contract key removal.** `README.md:504-510` promises consumers
  the v1 line shape; three keys disappear from `attempt_upsert` payloads.
  Accepted without a schema bump because the keys never held a non-null
  value in any journal on record, so no consumer could have depended on
  them for more than `None`. The README's `attempt_upsert` row is corrected
  in the same change so the documented shape and the written shape agree.
- **Downgrade / mixed versions on one project database.** An older `am`
  cannot write attempts into a database first created by the new `_SCHEMA`
  (4.2). Not a supported configuration; recovery is to delete the
  projection and rebuild from the journals, which D5 already promises is
  always possible.
- **Reading the original design spec cold.** Motivation 1 and the §9 tree
  are what a new reader hits first; the §9 line and the §17 open question
  are amended in this change so the source of truth does not describe
  fields that no longer exist. Motivation 1 stays because it is still
  true, and §1 of this spec is where the reversal is explained.
