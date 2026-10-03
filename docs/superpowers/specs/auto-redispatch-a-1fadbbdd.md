# Auto-redispatch a `harness_error` attempt once (1fadbbdd)

Subtask of story f1a29ec3 "A harness that dies without a result gets one
retry". The story's motivating failure: an implement harness backgrounded a test
run and ended its one-shot `claude -p` turn "waiting for the notification", so
the process exited with no result file, the attempt was journalled
`harness_error`, and the whole run escalated. This card makes the engine give
such an attempt exactly one more dispatch. The sibling card (f78d6855) makes the
failure less likely in the first place through role prompt text.

## Where the code lives

The card and its parent story both name `src/agent_manager/runtime/dispatch.py`.
That file does not exist. The attempt loop is `AgentRunner.__call__` in
`src/agent_manager/dispatch.py` (lines 388-441), and each dispatch is
`AgentRunner._attempt` (lines 443-555). All production changes in this card are
in `src/agent_manager/dispatch.py`.

## Inherited constraints

- **Four outcomes, unchanged.** Every attempt is journalled as one of `ok`,
  `schema_invalid`, `gate_failed`, `harness_error`, and `harness_error` means
  "non-zero exit, timeout, missing result file" (design spec §6, lines 280-281).
  No new attempt status is added; the redispatch is recorded with the existing
  vocabulary (`models.py:38`).
- **`retry.on` stays the schema/gate retry contract.** §6 step 7 (lines
  276-277) re-dispatches "on schema failure or a retryable gate failure ... up
  to `max_attempts`". This card does not touch that loop's semantics, does not
  make `harness_error` a legal or meaningful member of `retry.on`, and does not
  change `Retry` (`workflow/phases.py:35-37`).
- **Attempts are numbered from disk, never reused.** §6 step 3 (lines 268-271)
  gives each attempt its own `<phase>.<attempt>/` directory; `next_attempt`
  (`dispatch.py:59-66`) numbers by scanning disk. The redispatch is just another
  `_attempt` call and takes the next free number.
- **Journalled `started` before launch.** §9 (resume reads an orphaned
  `started` row; spec lines 350-394, and `dispatch.py:483-495`). The redispatch
  writes its own `started` row and terminal row exactly as any attempt does.
- **Warnings are the out-of-band channel.** `AgentRunner.warnings`
  (`dispatch.py:386`, docstring 372-377) exists so a run never reports success
  while something it was told about is invisible — the §12 principle "a run
  never reports success while the board silently never moved" (lines 463-466).
  A phase that succeeded only on a redispatch must say so there.
- **Launcher retryability line.** `harness/launcher.py:14-19` already draws the
  line this card relies on: a non-zero exit and a timeout are returned as
  `Outcome`s "because ... a re-dispatch might well succeed", while
  unrecoverable conditions (missing worktree, empty argv, bad timeout) raise.
  Raised exceptions are not `harness_error` verdicts and are not redispatched.
- **Test tiers.** §14 table (lines 517-524): anything driven through the
  injected `FakeLauncher` with no subprocess is the `unit` tier.

## Decisions

**D1. Every in-process `harness_error` verdict is eligible, not only "missing
result file".** `classify` (`dispatch.py:231-263`) produces `harness_error` for
three causes: timeout, non-zero exit, and clean exit with no result file. The
card's trigger is the status ("when an attempt ends `harness_error`"), its
parenthetical describes that status, and the launcher module already classes
non-zero exit and timeout as worth a re-dispatch. All three get the one
redispatch. Accepted cost: a phase that times out twice now spends up to two
timeouts of wall-clock before failing.

**D2. The redispatch is on top of the phase's retry budget, not inside it.**
A phase with `retry=None` (budget 1) still gets it; a phase with
`Retry(max_attempts=k, on=...)` can make at most `k + 1` attempts in one
invocation. The redispatch does not consume one of the `k`.

**D3. Once per phase invocation.** "Phase invocation" is one call of
`AgentRunner.__call__`. The first `harness_error` verdict in that call — whether
it is attempt 1 or comes after a `schema_invalid`/`gate_failed` retry — is
redispatched; any later `harness_error` in the same call fails the phase exactly
as today. A fresh `__call__` (a later run, a resume re-entering the phase)
starts with the allowance unspent.

**D4. The redispatch carries the same feedback as the failed attempt.** No
entry is appended to the schema/gate feedback list for a `harness_error`: the
harness never produced anything for the validator's text to correct, and the
prompt text for this failure is the sibling card's job. The redispatched brief
is therefore byte-identical in content to the attempt it replaces, except for
its own `result.json` path (addendum R2, `_attempt` docstring at 453-456).

**D5. Warning wording.** On redispatch, exactly one string is appended to
`self.warnings`:

```
phase {phase.name!r}: attempt {n} ended harness_error ({detail}); dispatching once more
```

where `n` is the journalled attempt number of the attempt that failed (which on
a resumed run need not be 1) and `detail` is that verdict's `detail`. This
matches the tone of `_decline`'s warning (`dispatch.py:681-684`). The warning is
appended whether the redispatch then succeeds or fails.

## Observable behavior

For one `AgentRunner.__call__(phase, context, rendered)`:

1. **Ok first attempt.** The launcher is called once; journal shows attempt 1
   `started` then `ok`; phase `done`; no warning added. (Unchanged.)
2. **`harness_error` then `ok`.** The launcher is called twice; journal shows
   `(1, started), (1, harness_error), (2, started), (2, ok)`; phase rows are
   `started` then `done`; the call returns attempt 2's validated result;
   `runner.warnings` gains exactly one entry, the D5 string for attempt 1.
3. **`harness_error` twice.** The launcher is called twice; journal shows
   `(1, started), (1, harness_error), (2, started), (2, harness_error)`; phase
   rows are `started` then `failed`, the `failed` row's detail being attempt
   2's detail; `AgentPhaseFailed` is raised with `outcome == "harness_error"`,
   `detail` = attempt 2's detail, `result is None`; exactly one warning (for
   attempt 1). A third dispatch is never made.
4. **`harness_error` then a result-file outcome.** The redispatched attempt is
   judged like any other: `schema_invalid`/`gate_failed` are retried only if in
   `retry.on` and the retry budget (D2) allows; otherwise the phase fails with
   that outcome as today. A broken gate (`fatal`) on the redispatch fails
   immediately as today.
5. **Result-file outcome first, then `harness_error`.** E.g. with
   `Retry(2, ("schema_invalid",))`: `schema_invalid`, `harness_error`, then a
   third attempt (the redispatch). The schema feedback from attempt 1 is still
   in attempt 3's brief (D4). A further `harness_error` would fail the phase.
6. **Each flavour of `harness_error`.** Missing result file after exit 0,
   non-zero exit, and timeout each trigger the redispatch (D1).
7. **Result-file outcomes are untouched.** A first attempt ending `ok`,
   `schema_invalid` or `gate_failed` behaves exactly as before this card: same
   launcher call count, same journal, no new warning.
8. **Exceptions are not redispatched.** If `_attempt` or the launcher raises
   (e.g. `UnsupportedLauncherError`, an `EngineError` writing the prompt), the
   existing `except` path records the phase `failed` and re-raises; no second
   dispatch, no redispatch warning.
9. **Phase with no result model.** Such a phase is judged on exit status alone
   (`classify` docstring, 241-244); a non-zero exit or timeout is
   `harness_error` and gets the redispatch like any other.

### Error paths and edge conditions

- **Cancellation.** When a bridge call is cancelled, the processes it spawned
  are killed and any process spawned afterwards is killed on spawn
  (`runtime/bridge.py:5-12`). A cancelled attempt may therefore surface as a
  `harness_error` and trigger the redispatch in the abandoned worker thread;
  the redispatched process is killed on spawn by the existing hook and ends
  `harness_error`, failing the phase. Net effect: up to one extra attempt
  directory and pair of attempt rows after a stop. This is accepted; the card
  adds no cancellation check.
- **Resume.** The orphan-`started` handling on resume (`orchestrate.py:724-738`,
  `cli.py:1803-1820`) marks rows `harness_error` without an `Outcome` and is out
  of scope; it neither consumes nor grants a redispatch. `AgentRunner.adopt`
  only adopts `ok` attempts and is unchanged.

## Tests

All tests below are **`unit` tier**: they drive `AgentRunner` through the
injected `FakeLauncher` (`tests/test_dispatch.py:289-320`) with `Store(tmp_path)`
fixtures and spawn no subprocess (§14 table, line 519; CLAUDE.md "Test tiers").
They live in `tests/test_dispatch.py`. `FakeLauncher(results=[...])` writes
`results[i]` for attempt i+1 (`None` = no file) and repeats the last entry;
`exit_code`/`timed_out` apply to every call. Where a test needs a failing then a
succeeding exit status, the plan adds a minimal per-call override to
`FakeLauncher` (e.g. an optional `exit_codes: list[int | None]` with the same
last-entry-repeats rule) — a test-only change.

New tests:

1. `test_a_harness_error_is_redispatched_once_and_the_second_attempt_can_succeed`
   — `results=[None, VALID_RESULT]`: behavior 2 (two calls, four attempt rows,
   phase `done`, returns the result, exactly one warning equal to the D5 string
   with `n=1` and the missing-file detail).
2. `test_two_harness_errors_fail_the_phase_as_today` — `results=[None]`:
   behavior 3 (two calls, four rows, `AgentPhaseFailed` with
   `outcome="harness_error"` and attempt 2's detail, phase `failed`, one
   warning).
3. `test_an_ok_first_attempt_dispatches_once` — `results=[VALID_RESULT]`:
   behavior 1 (one call, two rows, `warnings == []`).
4. `test_a_redispatch_does_not_consume_the_retry_budget` — phase
   `Retry(2, ("schema_invalid", "gate_failed"))`,
   `results=[None, NOT_JSON, VALID_RESULT]`: three calls, statuses
   `harness_error, schema_invalid, ok`, phase `done`.
5. `test_a_harness_error_after_a_schema_retry_is_still_redispatched` —
   `Retry(2, ("schema_invalid",))`, `results=[NOT_JSON, None, VALID_RESULT]`:
   three calls, phase `done`, attempt 3's prompt contains attempt 1's validator
   text (D4), the warning names attempt 2.
6. `test_a_redispatch_brief_carries_no_harness_error_feedback` —
   `results=[None, VALID_RESULT]`: `launcher.prompts[1]` contains no text of
   attempt 1's `harness_error` detail.
7. `test_a_non_zero_exit_is_redispatched` and
   `test_a_timeout_is_redispatched` — behavior 6 for the other two flavours
   (using the per-call exit-status override for the exit-then-ok case, or
   asserting two calls and `failed` for the always-failing case).
8. `test_a_phase_with_no_retry_block_still_gets_the_redispatch` — phase built
   with `retry=None` via `_agentic(retry=None)`, `results=[None, VALID_RESULT]`:
   phase `done` after two calls (D2).
9. `test_a_launcher_exception_is_not_redispatched` — a launcher that raises on
   its first call: one call, the exception propagates, phase `failed`, no
   warning (behavior 8).

Existing tests that pin the old single-dispatch behavior and must be updated
(not deleted silently):

- `test_a_missing_result_file_is_harness_error_and_is_not_retried`
  (`tests/test_dispatch.py:713-725`) — asserts one call and two rows. Its point
  (harness_error is not a `retry.on` retry) survives; rename it and change its
  expectations to the two-call, four-row shape of new test 2, or fold it into
  that test.
- `test_a_non_zero_exit_is_harness_error` (728-739) and
  `test_a_timeout_is_harness_error_and_the_log_survives` (741-751) — still
  raise `harness_error`; their assertions stay true but they now make two
  launcher calls. Add no call-count assertion that contradicts this; the
  timeout test's `stdout.log` check on attempt 1 still holds.

The implementer must run `uv run pytest` and `uv run pytest -m e2e_fake` and
update any other test that counted exactly one dispatch for a `harness_error`
attempt, with a one-line note in the commit message per updated test.

## Out of scope

- Role prompt text discouraging backgrounded work or early turn-ends — sibling
  card f78d6855 (`roles/bundles/coder/`, `resolver/`).
- Retrying `schema_invalid` or `gate_failed` differently — they keep their own
  `retry.on` loop (parent story's out-of-scope line).
- More than one redispatch, or a configurable redispatch count.
- Any change to `Retry`, `retry.on` validation, or workflow documents.
- The resume-time orphan `harness_error` marking (`orchestrate.py:724-738`,
  `cli.py:1803-1820`) and `AgentRunner.adopt`.
- A cancellation check before redispatching (see Error paths).
- Renaming or moving `dispatch.py` to match the card's stated path.
- Design-spec (§6) prose updates.

## Handoff to the planner

Plans follow the writing-plans format (header, Global Constraints, Review
Focus, bite-sized TDD tasks with exact code). Suggested shape:

**Files**
- Modify: `src/agent_manager/dispatch.py` — `AgentRunner.__call__` loop
  control (lines 404-435); `_attempt` may need to expose the attempt number it
  used so the warning can name it (e.g. return `(n, verdict)` or carry `n` on
  `Verdict`) — choose the narrowest change and keep `_attempt`'s journalling
  untouched.
- Modify: `tests/test_dispatch.py` — the new tests above, the updated existing
  ones, and the optional `FakeLauncher` per-call exit-status override.

**Loop contract the plan must implement**
- The retry budget counts only non-redispatch iterations; the total number of
  attempts in one call is at most `budget + 1`.
- A one-shot flag local to `__call__` records whether the redispatch has been
  spent.
- The check for "redispatch this harness_error" happens before the existing
  `verdict.fatal or verdict.status not in retry_on` break; `harness_error` is
  never `fatal`.
- No feedback is appended on a redispatch.

**Global Constraints (verbatim for the plan)**
- Attempt statuses remain exactly `started, ok, schema_invalid, gate_failed,
  harness_error`.
- At most one redispatch per `AgentRunner.__call__`.
- Warning text: `phase {name!r}: attempt {n} ended harness_error ({detail}); dispatching once more`.
- New tests are `unit` tier (no marker), ≤0.5s each.
- Verification: `uv run pytest` and `uv run pytest -m e2e_fake` both pass.

**Review Focus candidates**
1. Off-by-one in the loop bound: a phase with `max_attempts=2` must not reach
   four attempts, nor stop at two when a redispatch happened.
2. The warning naming the wrong attempt on a resumed run where attempt numbers
   start above 1.
3. A second `harness_error` after a schema retry being redispatched again
   (allowance must be per call, not per streak).
4. Feedback list accidentally gaining the `harness_error` detail.
5. A launcher exception being swallowed into a redispatch.
