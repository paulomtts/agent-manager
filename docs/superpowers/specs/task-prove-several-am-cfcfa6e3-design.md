# Subtask cfcfa6e3: Prove several `am` processes on one repository end to end

Card `cfcfa6e3-13b1-4bc4-ba99-a925f5d95c57`, story f88c5d7d ("Proof and documentation"), Milestone 10 ("several am processes per repository"). This narrows Task 3.1 of `docs/superpowers/plans/2026-09-27-multi-process.md` (on the `docs-multi-process` worktree), following §7 "Testing" of `docs/superpowers/specs/2026-09-27-multi-process-design.md`.

## Scope

This subtask adds tests and test scaffolding only. Production code changes only if a test finds a bug. In that case the fix goes in this subtask and gets its own failing test first, placed in the tier that the §7 placement rule assigns to it. The subtask builds on the milestone-10 code as it exists in this worktree: `locks.py`, `run_claims`, `Store.take_lease`, `control.card_claim` and `branch_claim`, `cli.ClaimedError` and `RunIsLiveError`, `orchestrate.milestone_claims`, and `am status`'s `data.control.claims`. Where the design doc's names differ from that code, the code wins.

Files:

- **New:** `tests/e2e/test_multi_process.py`. The scenarios below are unmarked, so they run in the default `uv run pytest`.
- **Modified:** `tests/e2e/fake_claude.py` gets a hold knob.
- **Modified:** `tests/e2e/conftest.py` gets `spawn_am`, `am` and `wait_for_file`, plus the hold env-name constants.
- **Modified:** `tests/e2e/test_fake_claude.py` pins the new constants.

Commit: `test(e2e): several am processes on one repository`. The branch prefix is `m10`. Nothing is pushed and the base branch does not move.

## Scaffolding behaviour

- **`fake_claude.py` hold knob**
  - Add the constants `HOLD_DIR_ENV = "FAKE_CLAUDE_HOLD_DIR"` and `HOLD_PHASE_ENV = "FAKE_CLAUDE_HOLD_PHASE"`. The phase defaults to `"implement"`.
  - When `HOLD_DIR_ENV` is set and the invocation enters the hold phase, the fake:
    1. writes `<dir>/<short_id(card_id)>.held`;
    2. polls for `<dir>/<short_id(card_id)>.release`, reusing the existing rendezvous poll interval and timeout (`RENDEZVOUS_POLL` / `RENDEZVOUS_TIMEOUT`).
  - If the timeout expires, the fake exits non-zero with a message naming the missing file, the same way the rendezvous does.
  - When `HOLD_DIR_ENV` is unset, nothing changes.
  - The knob is read only from the environment, never from the brief or prompt.
  - `short_id` is `agent_manager.dag.short_id`, or an equivalent local copy if the fake must stay import-free. The test and the fake must compute the same name.
- **`conftest.py`**
  - `spawn_am(*args, env=None) -> subprocess.Popen` runs `[sys.executable, "-c", "from agent_manager.cli import app; app()", *args]` with `stdout=PIPE` and `text=True`. By default it inherits `os.environ`, so the monkeypatched `XDG_DATA_HOME`, the `PATH` pointing at the fake `claude`, and the hold/rendezvous vars all reach the child. An explicit `env` overlays the inherited environment.
  - `am(*args) -> tuple[int, dict]` runs one child to completion and returns its exit code and the parsed JSON envelope.
  - `wait_for_file(path, child)` polls for `path` until a deadline. If `child.poll()` shows the child has exited before the file appears, it fails immediately and includes the child's captured output. If the deadline passes, it fails.
  - Ordering between processes is always proven by events such as marker files and exits. The tests never use `sleep` to order anything.
  - Add `FAKE_HOLD_DIR_ENV` and `FAKE_HOLD_PHASE_ENV` constants, mirroring the existing `FAKE_RENDEZVOUS_*` pair.
- **Cleanup:** every spawned child is killed and reaped in teardown (a fixture or `try/finally`), even when an assertion fails. No child outlives its test.

## Observable behaviour proven (e2e tier)

Error envelopes have the shape `{"ok": false, "error": {"type", "message"}}`. The `key` and `run_id` attributes of `ClaimedError` are not in the envelope. `cli._claimed_error` renders a key `kind:name` as `"{kind} {name} is being driven by run ..."` — a space, not the key's colon (pinned by `tests/test_cli.py::test_refuse_claimed_names_the_kind_and_the_live_holder`) — and never names the `--branch-prefix` flag. So tests check `message` for the kind and name as that space-joined substring (e.g. `f"card {a2} is"` or `f"branch {branch} is"`), never the colon-joined key, and never assert a `--branch-prefix` mention. (The multi-process design doc's §6 table says a branch conflict's message adds "use another --branch-prefix"; the code as built does not do this, and per this spec's Scope note the code wins — that phrasing is stale.)

1. **A card claimed by a live milestone.**
   - Setup: a milestone run is spawned and held in `implement` on a1. `wait_for_file` waits for a1's `.held` marker.
   - `am run --card a2` exits 3 with type `ClaimedError`, and `am runs` shows no new row.
   - `am resume <milestone-run>` exits 3 with type `RunIsLiveError`.
   - `am status <milestone-run>` has `f"card:{a2}"` in `data.control.claims`.
   - After a1's `.release` file is written, the milestone child exits 0 and its run is `done`.
2. **Two milestones with different prefixes run concurrently.**
   - Setup: two milestones on one repository, one with `--branch-prefix m10a` and one with `m10b`. The existing `rendezvous` fixture with count 2 forces their implement phases to overlap.
   - Both runs finish `done` and integrate.
   - Each `<prefix>-integrate` branch contains only its own milestone's task tips.
   - Every story and milestone card's board status equals `rollup_status` of its children.
3. **Two milestones with the same prefix.** Setup: the first milestone is held live. The second milestone, using the same `--branch-prefix`, exits 3 with type `ClaimedError`, and its message contains `f"branch {prefix}-integrate is"` (the space-joined kind and name; no `--branch-prefix` mention — see the note above).
4. **Taking over a killed milestone.**
   - Setup: a milestone run is held in `implement` and then killed with SIGKILL. The test records its pid and reaps it.
   - `am resume <run>` exits 0 with `data.took_over.pid == dead_pid`.
   - The run then finishes: `done: true` and run status `done`.

## Error paths covered

- A second run that needs a card claimed by a live run is refused (`ClaimedError`, exit 3) and leaves no row behind.
- Resuming a run another process holds is refused (`RunIsLiveError`, exit 3).
- Reusing a branch prefix that is in use is refused (`ClaimedError` naming the branch claim, per the note under "Observable behaviour proven").
- A dead holder's lease is taken over rather than refused.
- On the scaffolding side:
  - a child that exits early makes `wait_for_file` fail fast and show that child's output;
  - a hold that is never released makes the fake time out and exit non-zero, instead of hanging the suite.

## Test list and tiers

The tiers follow the placement rule in multi-process design §7. Only the end-to-end tier uses real `subprocess.Popen` `am` children. The lock, store, CLI and orchestrate tiers are already covered by earlier milestone-10 subtasks and are not added to here.

| Test | File | Tier |
|---|---|---|
| Scenario 1: live milestone claims a2; `--card` is refused, no new row, `resume` is refused, `status` lists the claim, the milestone finishes after release | `tests/e2e/test_multi_process.py` | e2e (default suite, unmarked, real `am` children + fake `claude`) |
| Scenario 2: `m10a`/`m10b` overlap via rendezvous; both `done`; integrate branches isolated; rollups consistent | `tests/e2e/test_multi_process.py` | e2e (unmarked) |
| Scenario 3: same prefix is refused with `ClaimedError` whose message contains `f"branch {prefix}-integrate is"` | `tests/e2e/test_multi_process.py` | e2e (unmarked) |
| Scenario 4: SIGKILLed holder; `resume` takes over (`took_over.pid == dead_pid`) and finishes | `tests/e2e/test_multi_process.py` | e2e (unmarked) |
| Hold env-name constants equal the conftest's (`FAKE_CLAUDE_HOLD_DIR`, `FAKE_CLAUDE_HOLD_PHASE`) and the phase default is `implement` | `tests/e2e/test_fake_claude.py` | fake-harness unit tests, alongside the existing rendezvous-name pin (test_fake_claude.py:635-640) |
| Hold behaviour: writes `.held` on the hold phase, returns once `.release` exists, times out non-zero without it, is inert when unset or in another phase | `tests/e2e/test_fake_claude.py` | fake-harness unit tests (same file and tier as the existing rendezvous tests) |
| Any bug fix uncovered by the scenarios | `tests/test_locks.py`, `tests/test_store.py`, `tests/test_cli.py` or `tests/test_orchestrate.py`, as §7 assigns | the tier that owns the faulty layer (for CLI/orchestrate, leases/claims planted over a second connection, no subprocesses) |

No test here carries the `e2e` marker. That marker is reserved for `tests/e2e/test_real_harness*.py`, which drive the real `claude` (see pyproject `markers` and the `test_live_control.py` docstring).

## Note on inputs

The exploration findings given to this stage were cut off at 8000 characters, partway through the Global Constraints section. This spec relies only on the parts that were received and on the code in this worktree. The Global Constraints on the card should be re-read at the planning stage.
