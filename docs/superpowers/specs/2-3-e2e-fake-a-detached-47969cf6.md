# 2.3 e2e_fake: a detached board run, watched, paused and resumed — spec

Card `47969cf6` (subtask of story `22561886`; blocked by `03f027ea`, which
landed `am run --board --detach`).
Parent design: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`
(cited below as "parent", by section and line).

## Summary

This card adds **one test**. It is the parent's `e2e_fake` scenario "a detached
board run followed with `am watch --all`, paused and resumed per milestone"
(parent §Testing, lines 112-115). Everything the scenario uses already exists on
this branch: `orchestrate.detach_board`, the `boards/` log and report, `am watch
--all [--follow]`, `milestone_id` on every board run's `run_upsert`, and per-run
`am pause` / `am resume`. **No file under `src/` changes.** If the test shows a
gap in `src/`, the implementer stops and reports it. Fixing it is not part of
this card.

## Inherited constraints

- A board has no board-level run record or run id; it is one run per milestone
  (parent §Non-goals, line 33). The test therefore never looks for a board run id.
  It finds each milestone's run through the `--all` stream.
- The detach envelope is `{"ok": true, "data": {"board": true, "detached": true,
  "pid", "log", "report", "levels"}}` and has no `run_id`. Each milestone's run is
  created when that milestone is dispatched. Its first `run_upsert` carries
  `milestone_id`, which is never null on a board run (parent §Design 2, lines
  87-91).
- The child writes the final board payload to `<data dir>/boards/<stamp>-<digest>.report.json`
  when the board ends (parent §Design 2, lines 83-86).
- Journal and watch schema stay 1, and JSON changes are additive only (parent
  §Compatibility, line 102; card text). The hello line's `schema` must be `1`.
- Boards with no inter-milestone `blocked_by` behave as before stacking existed
  (parent §Compatibility, line 100). The fixture's two milestones are independent,
  so both are on `--base-branch main`.
- Tier rules come from CLAUDE.md "Test tiers": `@pytest.mark.e2e_fake` means
  production wiring under the fake `claude`. It is opt-in with
  `uv run pytest -m e2e_fake`, has one test per scenario family, and the tier
  budget is ≤8 min.

## Observable behavior the test pins

The test uses the `two_milestone_board` fixture (`tests/e2e/conftest.py:507`).
It has milestones `first` (stories A, B → subtasks a1, b1) and `second` (stories
C, D → c1, d1). There are no blockers, and `UNION_ATTRIBUTE` is set so Integrate
needs no resolver. The test also uses `fake_claude_bin`, `hold`, `am`,
`spawn_am`, `am_processes`, and a module-local `detached_pids` fixture that
kills each session with `killpg` at teardown. It runs under the default
`--max-concurrent` (1). It passes no `--branch-prefix`, so each milestone uses
its own card-derived prefix. The test never hardcodes a branch name.

`hold.arm()`, then `hold.release(b1, c1, d1)`. Only a1's `implement` is held.

1. **Detach.** `am run --board --repo-dir ROOT --base-branch main --verify VERIFY --detach`
   - exits 0 with `ok: true`.
   - `data["levels"] == [{"level": 0, "milestones": [first, second]}]`. The
     order is `dag.board_levels`' input order. The implementer confirms this by
     running the test. If the order differs, compare the set of milestone ids
     instead of hardcoding the order.
   - `data["pid"]` is appended to `detached_pids`.
   - The other envelope keys, file modes, `getsid` and the `am runs` row are
     already pinned by `test_detached_run.py::test_a_detached_board_run_outlives_its_parent_and_leaves_its_report`,
     so this test does **not** assert them again.
2. **Watch every run.** `spawn_am("watch", "--all", "--follow")` starts right
   after the detach returns, before any milestone run necessarily exists.
   - The first line is the hello line `{"event": "watch", "schema": 1, "am": ..., "runs_dir": str(paths.data_dir() / "runs")}`.
     It has no `ok` key.
   - Every later line is one JournalLine. Its keys are exactly
     `store.JournalLine.model_fields`, and its `event` is in
     `get_args(store.EventKind)`. Its `run_id` may be either milestone's run.
     The `_Stream` helper must not pin one run id (see "Test helpers").
   - Runs that appear after the watcher started are picked up (`_poll_watch`
     lists the runs again on every pass).
3. **Each milestone's first `run_upsert` carries its `milestone_id`.** The test
   drains the stream until it has seen a `run_upsert` for two distinct
   `run_id`s. For each run, the **first** `run_upsert` in stream order has
   `payload["milestone_id"]` set to a milestone's full card id. The two values are
   exactly `{first, second}`. This gives the map `run_of[milestone] -> run_id`,
   and the two run ids differ.
4. **Held.** The test waits on `hold.held_marker(a1)`. Then `am status run_of[first]`
   shows `run.status == "started"` and a live lease.
5. **Pause one milestone's run only.** `am pause run_of[first] --repo-dir ROOT`
   exits 0. Its data is `{run_id: run_of[first], command: "pause", effective: "pause",
   already_requested: false}`. The test waits until `am status run_of[first]`
   `control.requests` is exactly one `pause` row with `handled_at` set. Only then
   does it call `hold.release(a1)`. Nothing is sent to `second`'s run.
6. **The board ends with one milestone stopped and the other done.** The test waits
   for the file at `data["report"]` to exist, then for both runs' leases to be
   released (`store.read_lease(...) is None`). The report holds:
   - `ok: true` (the envelope), `data.board: true`, `data.ok: false`. `ok` is true
     only when every entry is `done` (`orchestrate.py:2506`).
   - `data.levels` equals the detach envelope's `levels`.
   - `data.milestones` has two entries, in level order. The `first` entry has
     `status == "stopped"` (`milestone_status`, `orchestrate.py:2350-2366`),
     `paused: true`, `run_id == run_of[first]`, `resume == f"am resume {run_of[first]}"`,
     a1 **not** in `completed`, and a1 present in `stopped` as
     `{"story": A, "subtask": a1, "before_phase": "review"}`. None of
     `escalated`, `failed_phase`, `integrated` or `done` is present
     (`controlled_payload`, `orchestrate.py:165-198`). The test does **not** pin
     `pending` or b1's place: with one shared slot, whether lane B ran before the
     pause depends on scheduling.
   - The `second` entry has `status == "done"`, `done: true`, `run_id == run_of[second]`,
     and `set(integrated.merged) == {C, D}`.
   - `am status` reports `stopped` for `first`'s run and `done` for `second`'s.
   - The stream is drained until `first`'s run has a `run_upsert` with status
     `stopped`.
7. **Resume the paused milestone in the foreground.** `am resume run_of[first] --repo-dir ROOT --verify VERIFY`
   exits 0. Its data has `done: true`, `resumed: true`, `run_id == run_of[first]`,
   no `escalated` key, a1 in `completed`, and `set(integrated.merged) == {A, B}`.
   Afterwards `am status` shows `done` and the lease is gone. The board report
   file is **not** rewritten by the resume: re-reading it gives the same bytes as
   in step 6. The board's child has ended, and resume is a per-run command.
8. **One stream, both runs, exactly the journals.** A one-shot `am watch --all`
   exits 0 with `data.events`. The test checks:
   - The set of `run_id`s in it is exactly `{run_of[first], run_of[second]}`.
     The data dir is per-test (`tests/conftest.py:91`).
   - The test drains the follow stream until, for each run, its last streamed
     `seq` reaches that run's highest `seq` in the one-shot.
   - Then, per run, the follow stream's events (in stream order) equal the
     one-shot's events for that run, and their `seq`s are `1..N`. A `seq` is per
     run. The test never asserts a global order across runs.
   - Collapsed `run_upsert` statuses: `first`'s run contains the subsequence
     `started, stopped, started, done`. `second`'s run ends in `done` and has no
     `stopped`.
9. **Ctrl-C.** `SIGINT` to the watcher makes it exit 0 within `DEADLINE`, with
   empty stderr.

### Error paths the test must surface (not handle)

Each wait goes through `_until(predicate, what)` or `_Stream.drain_until(..., what)`,
bounded by `DEADLINE = 240.0`. A run that never reaches the expected state fails
with `pytest.fail` and a message that names the step. It never hangs. The
following each fail with their own message:
- the hello line is a refusal envelope;
- the watch stream reaches EOF early;
- fewer than two runs ever appear;
- a run's first `run_upsert` has a null or unexpected `milestone_id`;
- the pause is never handled;
- the report never appears.

The order of steps comes only from hold marker files, `handled_at`, the report
file, lease rows and stream contents. No step uses `time.sleep` to order events.

## Test helpers (module-local, copied rather than shared)

The test copies these from `tests/e2e/test_detached_pause_resume.py`, matching
that module's style (no `PREFIX`: a board derives its own): constants `VERIFY = "git rev-parse --verify HEAD"`
(must equal the e2e conftest's `VERIFY_COMMANDS[0]`), `DEADLINE`, `POLL`,
`CONTROL_KEYS_NEVER_PRESENT`, `EVENT_KINDS`, `JOURNAL_KEYS`, and the helpers
`_until`, `_lease`, `_status`, `_run_statuses`, `_collapse`, `_is_subsequence`,
plus the `detached_pids` fixture. Two helpers change:
- `_Stream(child)` takes no `run_id`. `drain_until` checks keys and kind but not
  `run_id`. The class adds `by_run() -> dict[str, list[dict]]`, which groups
  `events` by `run_id` in stream order.
- `_run_statuses(events)` is applied to one run's events (from `by_run()`).

## Tests

| Test | Tier | Why this tier |
|---|---|---|
| `tests/e2e/test_detached_board_pause_resume.py::test_a_detached_board_is_watched_with_all_and_one_milestone_is_paused_and_resumed` | `e2e_fake` (explicit `@pytest.mark.e2e_fake`) | It spawns real `am` child processes (a detached child in its own session, a follow watcher), real git and brd, and the fake `claude` on `PATH`: production wiring under the fake harness. That is the definition of `e2e_fake`, and it is the parent's named `e2e_fake` scenario (parent line 114-115). It is too slow and spawns too much for `unit`/`git`. It needs no real `claude`, so it is not `e2e`. |

There are no other tests. The detach mechanics already have their own `e2e_fake`
test (card 03f027ea), and the pure functions have `unit` tests.

TDD note: the behavior already exists, so the new test is expected to pass on its
first full run. To show it is not vacuous, the implementer briefly inverts one
key assertion and records that it fails, then restores it. Suitable assertions
are step 3's `milestone_id` equality or step 6's `status == "stopped"`. The
inverted version is not committed.

## Verification

- `uv run pytest -m e2e_fake tests/e2e/test_detached_board_pause_resume.py` passes.
- `uv run pytest` (default tiers) stays green. It does not collect this test
  (`pyproject.toml` `addopts` excludes `e2e_fake`).
- The new test's wall time is well inside the 8-minute `e2e_fake` tier budget
  (aim: under 60 s).

## Out of scope

- Any change under `src/` (see Summary).
- Re-asserting what card 03f027ea's test pins: envelope key set, `boards/` path
  shape and stamp, 0600 modes, `getsid`, `am runs` lease row.
- Milestone stacking scenarios: "the second's worktree contains the first's
  commits" and the three-milestone chain (parent lines 112-114). Those belong to
  sibling cards.
- Pausing or resuming the whole board, and `am cancel` on a board run. The parent
  defines neither.
- Running with `--max-concurrent > 1`, `--branch-prefix`, or `--dry-run`.
- Editing `tests/e2e/test_detached_run.py`'s module docstring line "Watch, pause
  and resume are sibling 3.3's scenario". It is left alone, and this module's
  docstring states that it covers the board form of that scenario.

## Plan handoff notes (for the plan author)

- This is a single task. The new module is
  `tests/e2e/test_detached_board_pause_resume.py`. Follow writing-plans format:
  write the test, run it, do the sanity-fail, restore, run the default suite,
  commit.
- Module docstring in the sibling style. It names the card (`47969cf6`), the tier,
  production wiring, that order comes from markers / `handled_at` / report /
  lease rows and never from sleeps, and what test_detached_run.py already pins.

## Review Focus candidates

1. **A run's directory appears before its journal's first line.** `--all` must
   skip such a run and pick it up later, not fail. The stream only ever carrying
   one run is the symptom.
2. **The slot order is not fixed.** With one shared slot, `second` may finish
   before a1 is ever held, or after the pause. The test must pass under either
   order. It must not assert `second`'s status before the report exists.
3. **The lease of the paused run is released while the board child keeps running
   the other milestone.** Resume happens only after the report exists, so the
   resume cannot race the child's store or git lock.
4. **The report file is read once complete.** It is written atomically
   (card 03f027ea), so `exists()` and then a read is safe. A partial read would be
   a regression in `detach.write_board_report`, not something this test retries.
5. **Watcher teardown on failure.** If an assertion fails mid-test, the
   `am_processes` / `detached_pids` fixtures must still kill the watcher and the
   detached session, so a failed run leaves no stray `am` processes.
