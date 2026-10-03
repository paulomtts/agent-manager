# Verify's detail always names the exit code (bc0b99e0)

Subtask of story 9f445791 "Verify failures name the real failure". This card covers one function's red branch:
`run_suite` in `src/agent_manager/steps/verify.py` (lines 303-316 today), plus its tests in
`tests/steps/test_verify.py`.

## Why

Milestone 17 escalated three times on a verify "failure" whose only reported diagnostic was pytest's green
summary line. `run_suite` decides pass/fail by exit code (`verify.py:293`). For a red command, though, it
reports only `command_diagnostic`'s line: the last line of stderr, else the last line of stdout
(`verify.py:65-79`). The exit code appears only when both streams are blank (`verify.py:307`). So a runner
that prints `7 passed` and then exits 1 produced `tail: "7 passed"` and
`detail: "verification failed: uv run pytest — 7 passed"`. Nothing in the result contradicts the green
line.

## Inherited constraints

- `verify.run_suite` stays read-only. It never commits, pushes, or touches the worktree (design §9,
  `2026-09-23-agent-manager-design.md:383`).
- Deterministic step: no network, no model (design §6, quoted in the `verify.py:3-4` module docstring).
- Commands run as argv lists and never through a shell (design §5 line 252, per `verify.py:14-16`). This
  card does not change that.
- `verification_passed_gate` owns turning red into a stop. It reads `result["detail"]` as an opaque string
  and `result["passed"]` by identity (`steps/reducers.py:322-343`). So the wording of `detail` is free,
  but `passed`'s type and meaning are not.
- The result stays a plain dict with exactly the keys `passed`, `verified` and `detail`. It crosses no
  process boundary, so it gets no Pydantic model (`CLAUDE.md` Conventions; `verify.py:268-269`).
- Test tiers are chosen by what a test spawns (design §14, `2026-09-23-agent-manager-design.md:513-525`;
  `CLAUDE.md` "Test tiers").

## Observable behaviour

### Exit label

A red command's exit code is rendered as an **exit label**:

| `exit_code` | label |
|---|---|
| positive `n` | `exit n`, e.g. `exit 1`, `exit 2` |
| negative `-n` where `n` is a known signal number on the platform | `exit -n (signal NAME)`, e.g. `exit -9 (signal SIGKILL)`, `exit -15 (signal SIGTERM)`, `exit -11 (signal SIGSEGV)` |
| negative `-n` where `n` is not a known signal | `exit -n (signal n)`, e.g. `exit -200 (signal 200)` |

Negative means the child was killed by a signal. This is `subprocess.run`'s `returncode` convention, and
`run_command` copies it straight through at `verify.py:132`. NAME is `signal.Signals(n).name`. A number
that `signal.Signals` rejects falls back to the bare number. It never raises.

`exit_code == 0` never reaches the red branch, so it has no label.

### A red command's `verified[]` row

```python
{"command": <command as given>, "ok": False, "exit_code": <int>, "tail": <str>}
```

- `exit_code` is a new key, set to the integer exactly as the runner returned it (for example `1`, `-9`).
  It is an `int`, not a string.
- `tail` is `plain_text(f"{label} — {diagnostic}")`, using the default 300-character cap. Examples:
  `"exit 1 — 7 passed"`, `"exit -9 (signal SIGKILL) — no output"`.
- `diagnostic` is still `command_diagnostic`'s line: the last non-empty line of stderr, else the last
  non-empty line of stdout. When both streams are blank it is `NO_OUTPUT` (`"no output"`). `run_suite`
  stops passing the `"{shown} exited with code N"` fallback, because the label already carries the code.
  Keeping the fallback would print the code twice.

### `detail` on a red run

```text
verification failed: <shown> — <label> — <diagnostic>
```

This is flattened and capped by `plain_text(..., DETAIL_MAX)` (600), as today. For example:
`verification failed: uv run pytest — exit 1 — 7 passed`. The `verification failed:` prefix is unchanged,
so `"verification failed" in detail` assertions elsewhere still hold (`tests/test_integration.py:421`,
`tests/test_integrate_workflow.py:434`).

The label comes before the diagnostic. Truncation cuts from the end, so stream content of any length or
shape (ANSI, control bytes, 5000-character lines) can never push the exit code out of `tail` or `detail`.
This is what "whatever the streams contained" in the card means.

### Unchanged

- **Green path.** A command with exit 0 still yields `{"command", "ok": True, "tail": <last stdout line>}`.
  It gets **no** `exit_code` key, and a fully green run keeps `detail == ""`. The card says "the passing
  path is unchanged", and `tests/steps/test_verify.py:124`, `:389-391`, `:641` pin the exact green row
  dict.
- Short-circuit: the first red command stops the suite, and later commands (including Explore's
  typecheck/lint) do not run.
- `VerifyError` for unlaunchable commands, and `ValueError` for malformed input before anything runs.
- `command_diagnostic`, `last_line`, `plain_text`, `CommandResult` and `run_command` keep their
  signatures and behaviour. Their direct tests (`test_verify.py:57-116`) stay as they are.
- `passed` stays `False` on a red run, and `result` keeps exactly three keys.

### Error paths

None are new. A runner that returns a non-int `exit_code` is a broken fake, not a supported input. The
label formats whatever `int` it gets, and this card does not validate the type.

## Tests

All tests live in `tests/steps/test_verify.py`. **Tier: `git`**, by the `tests/steps/` directory default
in `tests/conftest.py` (`_DIRECTORY_TIERS = {"steps": "git"}`, line 115). That is the right tier for the
real-subprocess tests: they spawn `sys.executable`, and the `unit` tier forbids any subprocess (design §14
table row `unit`). The fake-runner tests are unit-shaped. They still inherit `git` the way the file's
existing fake-runner tests do (`:234-246`, `:249-270`), because `conftest.py` has no marker that opts a
`tests/steps/` item back into `unit`. All of them sit well inside the 2s per-test budget. None needs
`brd` or `claude`.

### New tests

1. **A failing runner with a green-looking last line reports both the code and the line.** Real process:
   `_py("print('7 passed'); raise SystemExit(1)")`. Assert `passed is False` and
   `verified == [{"command": cmd, "ok": False, "exit_code": 1, "tail": "exit 1 — 7 passed"}]`. Assert
   `detail == f"verification failed: {cmd} — exit 1 — 7 passed"`. This is the M17 incident pinned. The
   test uses a real process because the incident came from a real process.
2. **A signal death is shown as a signal (real process).**
   `_py("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")`. Assert `exit_code == -9`,
   `tail == "exit -9 (signal SIGKILL) — no output"`, and that `detail` ends with
   `"— exit -9 (signal SIGKILL) — no output"`. This proves the negative-code convention end to end
   through `run_command`. It is real rather than faked because the sign convention belongs to
   `subprocess`, and only a real child shows it. POSIX only, which matches the project's platforms.
3. **A signal-style code from the runner is labelled with its name (fake runner).** A runner returns
   `CommandResult(exit_code=-11, stdout="", stderr="Segmentation fault\n")`. Assert the row is
   `{"command": "fake-suite", "ok": False, "exit_code": -11, "tail": "exit -11 (signal SIGSEGV) — Segmentation fault"}`.
   Assert `detail == "verification failed: fake-suite — exit -11 (signal SIGSEGV) — Segmentation fault"`.
   This uses a fake because it pins the formatting for a specific signal without making a child segfault.
4. **An unknown negative code falls back to the bare number (fake runner).** `exit_code=-200`, streams
   blank. Assert `tail == "exit -200 (signal 200) — no output"`, and that nothing raises.
5. **The exit code survives over-long and ANSI stream content (fake runner).** `exit_code=2` and stdout of
   `"\x1b[32m" + "P" * 5000 + "\x1b[0m"`. Assert `tail == "exit 2 — " + "P" * 291 + "…"` (300
   characters, then the ellipsis). Assert that `detail` starts with
   `"verification failed: fake-suite — exit 2 — "`, has length 601, and contains no `\x1b`.
6. **The green path is unchanged.** The existing `test_a_single_green_command_passes_with_its_last_stdout_line`
   (`:119-124`) already pins the exact green row with no `exit_code` key. Add one assertion to the
   mixed-suite test below (or a new test) that the green rows that come before a red row carry no
   `exit_code` key.

### Existing tests whose expected strings change

These assert today's red-path strings exactly. Update them to the new format and do not delete them:

| Test (line) | New expectation |
|---|---|
| `test_a_red_command_reports_its_stderr_line` (:180-187) | row gains `"exit_code": 1` and `tail: "exit 1 — AssertionError: boom"`; `detail == f"verification failed: {command} — exit 1 — AssertionError: boom"` |
| `test_a_red_command_with_a_stdout_only_diagnostic_reports_that_stdout_line` (:190-200) | `tail == "exit 2 — ERROR: 3 lint problems"`; `exit_code == 2` |
| `test_a_silent_red_command_still_carries_a_tail_and_a_detail` (:213-218) | `tail == "exit 3 — no output"`; `detail == f"verification failed: {command} — exit 3 — no output"` |
| `test_ansi_colour_and_over_long_lines_are_flattened_in_the_result` (:234-246) | `tail == "exit 1 — " + "E" * 291 + "…"`; `detail == f"verification failed: fake-linter — exit 1 — {'E' * 400}"` (still under 600) |
| `test_a_failing_typecheck_fails_the_suite_and_stops_lint` (:519-536) | red row `{"command": typecheck, "ok": False, "exit_code": 1, "tail": "exit 1 — error: 2 type errors"}`; detail gains `exit 1 — ` |
| `test_a_silent_failing_typecheck_still_carries_a_tail_and_a_detail` (:539-551) | row `{"command": typecheck, "ok": False, "exit_code": 3, "tail": "exit 3 — no output"}`; detail contains `"— exit 3 — no output"` |
| `test_a_failing_lint_after_a_green_typecheck_names_the_lint_command` (:554-567) | `tail == "exit 1 — E501 line too long"`; detail gains `exit 1 — ` |

Tests outside this file need no change. `tests/test_comments.py` and `tests/steps/test_reducers.py` build
their own `detail` strings by hand. `comments.py:207-210` reads only `command`/`ok` from rows, so the new
key is ignored there. The e2e_fake assertions are substring checks on `verification failed`.

Verification: `uv run pytest`, then `uv run pytest -m e2e_fake`. The repo has no typecheck or lint command.

## Out of scope

- **Persisting verify's stdout/stderr** under the run directory, and `am logs --phase verify`. That is the
  next card, e1b1e7d5 "Persist verify output under the run directory".
- **Resume announcing the kept `--verify` suite.** That is sibling card 5b19aa93.
- Changing which suite a checkpointed walk runs (the parent story's stated exclusion).
- Changing the green row, adding `exit_code` to green rows, or changing `command_diagnostic`'s preference
  order.
- `verification_passed_gate`, `comments.py`, `cli.py`, `runtime/`, and workflow YAML. None of them read
  the format this card changes.
- Capping or abbreviating an over-long *command* in `detail`. A command display longer than about 560
  characters can still push the label past `DETAIL_MAX`. The row's `exit_code` key always holds the code
  regardless. Card commands are short (`uv run pytest`), and the card targets stream content, not command
  length.

## Planner handoff

**Global Constraints**
- Red `verified[]` row keys: exactly `command`, `ok`, `exit_code`, `tail`. Green row keys: exactly
  `command`, `ok`, `tail`.
- Label: `exit {n}`, or `exit {n} (signal {NAME})` for `n < 0`, with NAME from `signal.Signals(-n).name`,
  else the bare number.
- Separator: ` — ` (space, U+2014 EM DASH, space), the same character `verify.py:313` already uses.
- `tail` cap 300 (`plain_text` default); `detail` cap `DETAIL_MAX` = 600.
- Blank-stream diagnostic: `"no output"` (`NO_OUTPUT`). The red branch no longer uses
  `"{shown} exited with code N"`.
- `result` keys stay `passed`, `verified`, `detail`; `verify.py` imports nothing new beyond the stdlib
  `signal`.

**Suggested shape:** one task. Add a small pure helper, for example `exit_label(code: int) -> str`, in
`verify.py` next to `command_diagnostic`. Rewrite the red branch to use it. Update the seven existing
tests and add the five new ones. The helper can be asserted directly in the same way as `last_line` and
`command_diagnostic` (`test_verify.py:57-116`). A reviewer could not usefully accept the helper while
rejecting the branch change, so splitting it into its own task gains nothing.

**Review Focus candidates** (inputs the tests above do not all pin):
1. Stdout ends in a green summary and stderr has an unrelated last line, such as a deprecation warning. The
   stderr line still wins, exactly as today, and the exit label is what keeps the row honest.
2. CRLF or trailing-whitespace output: the diagnostic is still trimmed (`last_line`), so the tail must not
   end in `\r`.
3. `exit_code` above 255 or other odd positive values from a fake runner: formatted verbatim, never
   clamped.
4. A red command given as an argv list rather than a string: `shown` is the space-joined argv, and the row's
   `command` is the original list.
5. A red command after several green ones: only the red row gains `exit_code`, and green rows keep their
   exact dict.
