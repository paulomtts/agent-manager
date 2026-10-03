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

---

# Auto-redispatch a `harness_error` attempt once — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `AgentRunner.__call__` gives the first `harness_error` attempt of each phase invocation exactly one extra dispatch, on top of the `retry.on` budget, and reports it in `runner.warnings`.

**Architecture:** `AgentRunner._attempt` returns the attempt number it used alongside its `Verdict` (`tuple[int, Verdict]`), with its journalling untouched. The `for _ in range(budget)` loop in `__call__` becomes a `while True` loop with two locals: `redispatched` (the one-shot flag) and `counted` (non-redispatch attempts, compared against `budget`). A `harness_error` while `redispatched` is false sets the flag, appends the D5 warning and `continue`s with no feedback appended; everything else follows the existing break/retry rules.

**Tech Stack:** Python 3, pytest, `uv`. No new dependencies.

**Spec:** `docs/superpowers/specs/auto-redispatch-a-1fadbbdd.md` (prepended above).

## Global Constraints

- Attempt statuses remain exactly `started, ok, schema_invalid, gate_failed, harness_error`.
- At most one redispatch per `AgentRunner.__call__`.
- Warning text: `phase {name!r}: attempt {n} ended harness_error ({detail}); dispatching once more`.
- New tests are `unit` tier (no marker), ≤0.5s each.
- Verification: `uv run pytest` and `uv run pytest -m e2e_fake` both pass.
- All production changes are in `src/agent_manager/dispatch.py`; `Retry`, `retry.on` validation, `classify`, `read_result`, `adopt` and the resume-time orphan marking are not touched.

## Review Focus

1. **Loop bound off-by-one** — `Retry(2, ...)` with a redispatch makes at most 3 attempts, never 4, and does not stop at 2 when the redispatch happened. Pinned by `test_a_redispatch_does_not_consume_the_retry_budget` and `test_a_redispatch_plus_a_spent_retry_budget_stops_at_budget_plus_one`.
2. **Warning names the wrong attempt on a resumed run** — with `explore.1`/`explore.2` already on disk the warning says `attempt 3`. Pinned by `test_the_redispatch_warning_names_the_journalled_attempt_number`.
3. **Allowance per call, not per streak** — `schema_invalid`, `harness_error`, `harness_error` fails on attempt 3 with no fourth dispatch. Pinned by `test_a_second_harness_error_after_a_schema_retry_fails_the_phase`.
4. **Feedback list gaining the `harness_error` detail** — the redispatched brief equals the failed one apart from its own attempt directory. Pinned by `test_a_redispatch_brief_carries_no_harness_error_feedback`.
5. **A launcher exception swallowed into a redispatch** — one launcher call, the exception propagates, phase `failed`, no warning. Pinned by `test_a_launcher_exception_is_not_redispatched`.

---

### Task 1: Redispatch the first `harness_error` of a phase invocation once

**Files:**
- Modify: `src/agent_manager/dispatch.py:388-441` (`AgentRunner.__call__`) and `:443-555` (`AgentRunner._attempt` — return type and final `return` only)
- Test: `tests/test_dispatch.py` — `FakeLauncher` (lines 289-320), the existing test at lines 713-725, and new tests inserted directly after `test_a_timeout_is_harness_error_and_the_log_survives` (ends line 751)

**Interfaces:**
- Consumes: existing `dispatch.Verdict(status, result=None, detail=None, fatal=False)`, `paths.attempt_dir(run_id, card, phase, n) -> Path`, test helpers `_workflow`, `AGENT_DOCUMENT`, `_agentic`, `_runner`, `_context`, `_rendered`, `_attempt_statuses`, `_phase_statuses`, fixtures `store`, `worktree`, constants `RUN_ID`, `CARD`, `VALID_RESULT`, `NOT_JSON`.
- Produces:
  - `AgentRunner._attempt(...) -> tuple[int, Verdict]` (was `-> Verdict`); `n` is the attempt number `next_attempt` chose and journalled.
  - `FakeLauncher.exit_codes: list[int | None] | None = None` — when set, call i (0-based) returns `exit_codes[min(i, len(exit_codes) - 1)]` instead of `exit_code`.

- [ ] **Step 1: Add the per-call exit-status override to `FakeLauncher`**

In `tests/test_dispatch.py`, replace the `FakeLauncher` class (lines 289-320) with:

```python
@dataclass
class FakeLauncher:
    """A `LauncherFn` double that writes canned files instead of running anything.

    `results[i]` is attempt i+1's `result.json` text, or `None` to write no
    result file at all; the last entry repeats for any further attempt.
    `exit_codes`, when given, overrides `exit_code` per call by the same rule.
    """

    results: list[str | None]
    stdout: str = "usage: tokens\n"
    exit_code: int | None = 0
    timed_out: bool = False
    exit_codes: list[int | None] | None = None
    calls: list[list[str]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        self.calls.append(list(argv))
        if "--prompt" in argv:
            self.prompts.append(
                Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
            )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(self.stdout, encoding="utf-8")
        canned = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if canned is not None:
            result_path = Path(argv[argv.index("--result") + 1])
            result_path.write_text(canned, encoding="utf-8")
        exit_code = self.exit_code
        if self.exit_codes is not None:
            exit_code = self.exit_codes[min(len(self.calls) - 1, len(self.exit_codes) - 1)]
        return Outcome(
            argv=list(argv),
            exit_code=exit_code,
            timed_out=self.timed_out,
            duration=1.25,
            stdout_path=stdout_path,
        )
```

- [ ] **Step 2: Update the existing single-dispatch `harness_error` test**

Replace `test_a_missing_result_file_is_harness_error_and_is_not_retried` (lines 713-725) with:

```python
def test_two_harness_errors_fail_the_phase_as_today(store, tmp_path, worktree):
    # Spec tests 6 and 13 still hold -- harness_error is never a `retry.on`
    # retry -- but the first one gets the single redispatch (1fadbbdd), so
    # the phase fails after two dispatches, on attempt 2's detail.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    second = paths.attempt_dir(RUN_ID, CARD, "explore", 2) / "result.json"
    assert caught.value.outcome == "harness_error"
    assert caught.value.detail == f"the harness wrote no result file at {second}"
    assert caught.value.result is None
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "harness_error"), (2, "started"), (2, "harness_error")
    ]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    failed_detail = [
        line.payload["detail"]
        for line in store.journal.read()
        if line.event == "phase_upsert"
    ][-1]
    assert failed_detail == caught.value.detail
    assert len(runner.warnings) == 1
    assert "attempt 1 ended harness_error" in runner.warnings[0]
```

Leave `test_a_non_zero_exit_is_harness_error` and `test_a_timeout_is_harness_error_and_the_log_survives` unchanged: they still raise `harness_error` and assert no call count.

- [ ] **Step 3: Write the core redispatch tests**

Insert directly after `test_a_timeout_is_harness_error_and_the_log_survives`:

```python
def _redispatch_warning(n: int, detail: str) -> str:
    return (
        f"phase 'explore': attempt {n} ended harness_error ({detail}); "
        "dispatching once more"
    )


def _no_result_file(n: int) -> str:
    path = paths.attempt_dir(RUN_ID, CARD, "explore", n) / "result.json"
    return f"the harness wrote no result file at {path}"


def test_a_harness_error_is_redispatched_once_and_the_second_attempt_can_succeed(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "harness_error"), (2, "started"), (2, "ok")
    ]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "done")]
    assert runner.warnings == [_redispatch_warning(1, _no_result_file(1))]


def test_an_ok_first_attempt_dispatches_once(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]
    assert runner.warnings == []


def test_a_redispatch_brief_carries_no_harness_error_feedback(store, tmp_path, worktree):
    # D4: nothing is appended for a harness_error, so the redispatched brief is
    # the failed one with only its own attempt directory swapped in.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    first_dir = str(paths.attempt_dir(RUN_ID, CARD, "explore", 1))
    second_dir = str(paths.attempt_dir(RUN_ID, CARD, "explore", 2))
    assert dispatch.FEEDBACK_HEADING not in launcher.prompts[1]
    assert "wrote no result file" not in launcher.prompts[1]
    assert launcher.prompts[1] == launcher.prompts[0].replace(first_dir, second_dir)


def test_the_redispatch_warning_names_the_journalled_attempt_number(
    store, tmp_path, worktree
):
    # A resumed run finds explore.1 and explore.2 on disk, so its first
    # attempt here is 3 -- the warning must say 3, not 1.
    paths.attempt_dir(RUN_ID, CARD, "explore", 1)
    paths.attempt_dir(RUN_ID, CARD, "explore", 2)
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert _attempt_statuses(store) == [
        (3, "started"), (3, "harness_error"), (4, "started"), (4, "ok")
    ]
    assert runner.warnings == [_redispatch_warning(3, _no_result_file(3))]
```

- [ ] **Step 4: Write the retry-budget interplay tests**

Append after the Step 3 tests:

```python
def test_a_redispatch_does_not_consume_the_retry_budget(store, tmp_path, worktree):
    # D2: Retry(2, ...) still has both of its attempts after the redispatch.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, NOT_JSON, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 3
    assert [status for _n, status in _attempt_statuses(store) if status != "started"] == [
        "harness_error", "schema_invalid", "ok"
    ]
    assert _phase_statuses(store)[-1] == ("explore", "done")


def test_a_redispatch_plus_a_spent_retry_budget_stops_at_budget_plus_one(
    store, tmp_path, worktree
):
    # Review Focus 1: budget 2 plus the redispatch is three attempts, never four.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, NOT_JSON])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "schema_invalid"
    assert len(launcher.calls) == 3
    assert [status for _n, status in _attempt_statuses(store) if status != "started"] == [
        "harness_error", "schema_invalid", "schema_invalid"
    ]


def test_a_harness_error_after_a_schema_retry_is_still_redispatched(
    store, tmp_path, worktree
):
    def document(functions):
        return _agentic(functions["output_gate"], retry=phases.Retry(2, ("schema_invalid",)))

    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[NOT_JSON, None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 3
    # D4: attempt 1's validator text is still in attempt 3's brief.
    third = launcher.prompts[2]
    assert dispatch.FEEDBACK_HEADING in third
    assert "not valid JSON" in third.split(dispatch.FEEDBACK_HEADING, 1)[1]
    assert third.count(dispatch.FEEDBACK_HEADING) == 1
    assert runner.warnings == [_redispatch_warning(2, _no_result_file(2))]


def test_a_second_harness_error_after_a_schema_retry_fails_the_phase(
    store, tmp_path, worktree
):
    # Review Focus 3: the allowance is per call, not per streak.
    def document(functions):
        return _agentic(functions["output_gate"], retry=phases.Retry(2, ("schema_invalid",)))

    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[NOT_JSON, None])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert caught.value.detail == _no_result_file(3)
    assert len(launcher.calls) == 3
    assert len(runner.warnings) == 1


def test_a_phase_with_no_retry_block_still_gets_the_redispatch(store, tmp_path, worktree):
    def document(functions):
        return _agentic(functions["output_gate"], retry=None)

    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 2
    assert _phase_statuses(store)[-1] == ("explore", "done")


def test_a_broken_gate_on_the_redispatch_fails_at_once(store, tmp_path, worktree):
    # Behavior 4: the redispatch is judged like any attempt, and a broken gate
    # is still fatal however much budget is left.
    def output_gate(result):
        raise RuntimeError("the gate itself is broken")

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": output_gate})
    launcher = FakeLauncher(results=[None, VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert len(launcher.calls) == 2
```

- [ ] **Step 5: Write the flavour and exception tests**

Append after the Step 4 tests:

```python
def test_a_non_zero_exit_is_redispatched(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], exit_codes=[3, 0])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 2
    assert runner.warnings == [_redispatch_warning(1, "the harness exited 3")]


def test_a_timeout_is_redispatched(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[None], exit_code=None, timed_out=True)
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "harness_error"), (2, "started"), (2, "harness_error")
    ]
    assert len(runner.warnings) == 1
    assert "timed out" in runner.warnings[0]


def test_a_result_less_phase_redispatches_a_bad_exit(store, tmp_path, worktree):
    # Behavior 9: judged on exit status alone, a non-zero exit is still a
    # harness_error and still gets the redispatch.
    def document(functions):
        return _agentic(
            name="spec", result=None, retry=None, writes="docs/superpowers/specs/{stem}.md"
        )

    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[None], exit_codes=[2, 0])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result is None
    assert len(launcher.calls) == 2
    assert runner.warnings == [
        "phase 'spec': attempt 1 ended harness_error (the harness exited 2); "
        "dispatching once more"
    ]


def test_a_launcher_exception_is_not_redispatched(store, tmp_path, worktree):
    # Behavior 8 / Review Focus 5: a raise is not a harness_error verdict.
    calls = []

    def explode(argv, *, cwd, timeout, stdout_path):
        calls.append(list(argv))
        raise OSError("the harness binary vanished")

    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    runner, _ = _runner(store, explode, tmp_path, worktree)

    with pytest.raises(OSError, match="the harness binary vanished"):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert len(calls) == 1
    assert _attempt_statuses(store) == [(1, "started")]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    assert runner.warnings == []
```

- [ ] **Step 6: Run the new and updated tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v -k "redispatch or harness_errors_fail or dispatches_once or launcher_exception or broken_gate_on_the_redispatch or second_harness_error"`
Expected: FAIL for every test whose shape needs a second dispatch (e.g. `test_a_harness_error_is_redispatched_once_and_the_second_attempt_can_succeed` raises `AgentPhaseFailed`, `test_two_harness_errors_fail_the_phase_as_today` fails `assert 1 == 2` on `len(launcher.calls)`). `test_an_ok_first_attempt_dispatches_once` and `test_a_launcher_exception_is_not_redispatched` pin unchanged behavior and PASS already; that is expected.

- [ ] **Step 7: Make `_attempt` return the attempt number**

In `src/agent_manager/dispatch.py`, `AgentRunner._attempt`: change the return annotation from `-> Verdict:` to `-> tuple[int, Verdict]:`, add one sentence to its docstring, and change the final `return verdict` to `return n, verdict`. The signature and docstring become:

```python
    def _attempt(
        self,
        phase: phase_model.AgentPhase,
        context: Mapping[str, Any],
        rendered: prompt.RenderedPrompt,
        feedback: Sequence[str],
        target: Target,
        role: RoleBundle,
        cwd: Path,
        model: type[BaseModel] | None,
    ) -> tuple[int, Verdict]:
        """One dispatch: directory, brief, argv, launcher, result, gates.

        The brief is composed here rather than by the caller because addendum R2
        puts this attempt's own `result.json` in it, and that path only exists
        once `next_attempt` and `paths.attempt_dir` have fixed the directory.
        The attempt number is returned with the verdict so the caller can name
        it: on a resumed run it need not be the loop's iteration count.
        """
```

and the last line of the method:

```python
        return n, verdict
```

Nothing else in `_attempt` changes.

- [ ] **Step 8: Rewrite the attempt loop in `__call__`**

In `AgentRunner.__call__`, replace this block:

```python
        budget = 1 if phase.retry is None else phase.retry.max_attempts
        retry_on = () if phase.retry is None else tuple(phase.retry.on)
        feedback: list[str] = []
        verdict = Verdict("harness_error", detail="no attempt was made")

        try:
            for _ in range(budget):
                verdict = self._attempt(
                    phase, context, rendered, tuple(feedback), target, role, cwd, model
                )
                if verdict.status == "ok":
                    self._record_phase(phase, "done", started_at, self.clock(), None)
                    return verdict.result
                if verdict.fatal or verdict.status not in retry_on:
                    break
                # Carried as data, not folded into `rendered`: `_attempt` composes
                # the whole brief from the base prompt every time, so re-feeding a
                # composed brief would duplicate the result contract.
                feedback.append(verdict.detail or verdict.status)
```

with:

```python
        budget = 1 if phase.retry is None else phase.retry.max_attempts
        retry_on = () if phase.retry is None else tuple(phase.retry.on)
        feedback: list[str] = []
        verdict = Verdict("harness_error", detail="no attempt was made")
        # The first harness_error of this call gets one more dispatch, outside
        # the retry budget (1fadbbdd): a harness that died without a result --
        # a turn ended early, a timeout, a crash -- may well succeed on a fresh
        # process, and `retry.on` is the schema/gate contract, not this one.
        redispatched = False
        counted = 0

        try:
            while True:
                n, verdict = self._attempt(
                    phase, context, rendered, tuple(feedback), target, role, cwd, model
                )
                if verdict.status == "ok":
                    self._record_phase(phase, "done", started_at, self.clock(), None)
                    return verdict.result
                if verdict.status == "harness_error" and not redispatched:
                    # No feedback: the harness produced nothing for a complaint
                    # to correct, so the brief is re-sent as it was.
                    redispatched = True
                    self.warnings.append(
                        f"phase {phase.name!r}: attempt {n} ended harness_error "
                        f"({verdict.detail}); dispatching once more"
                    )
                    continue
                counted += 1
                if verdict.fatal or verdict.status not in retry_on or counted >= budget:
                    break
                # Carried as data, not folded into `rendered`: `_attempt` composes
                # the whole brief from the base prompt every time, so re-feeding a
                # composed brief would duplicate the result contract.
                feedback.append(verdict.detail or verdict.status)
```

The `except Exception` block and the code after the `try` are unchanged.

- [ ] **Step 9: Run the dispatch tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS, all tests, including `test_a_non_zero_exit_is_harness_error`, `test_a_timeout_is_harness_error_and_the_log_survives`, `test_a_spec_attempt_that_writes_no_result_file_is_a_harness_error` and `test_all_four_outcome_names_are_journalled_as_distinct_values` (they now make two launcher calls but assert no call count).

- [ ] **Step 10: Run the full default suite and the e2e_fake tier**

Run: `uv run pytest`
Expected: PASS. (A prototype of this exact loop broke only the test replaced in Step 2.)

Run: `uv run pytest -m e2e_fake`
Expected: PASS (63 tests in the prototype run). If any other test fails because it counted exactly one dispatch for a `harness_error` attempt, update its expectation to two dispatches and list it in the commit message with a one-line note.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "$(cat <<'EOF'
feat: redispatch the first harness_error of a phase invocation once (1fadbbdd)

AgentRunner gives the first harness_error attempt in each __call__ one
more dispatch, outside the retry.on budget, with no feedback appended,
and records a warning naming the failed attempt.

Updated tests:
- test_a_missing_result_file_is_harness_error_and_is_not_retried ->
  test_two_harness_errors_fail_the_phase_as_today: one harness_error is
  now redispatched, so the phase fails after two dispatches.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```
<!-- task-pipeline: validated -->
