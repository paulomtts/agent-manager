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

---

# Verify's Detail Always Names the Exit Code Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every red `verify.run_suite` row and `detail` leads with an exit label (`exit 1`, `exit -9 (signal SIGKILL)`), and red rows carry the raw integer as `exit_code`, so a runner that prints a green summary and exits non-zero can never read as green.

**Architecture:** Add one pure helper, `exit_label(exit_code: int) -> str`, to `src/agent_manager/steps/verify.py` beside `command_diagnostic`. Rewrite only `run_suite`'s red branch to put that label before the diagnostic in both `tail` and `detail`, and stop passing the `"{shown} exited with code N"` fallback (the label already carries the code; blank streams fall through to `NO_OUTPUT`). The green branch, `command_diagnostic`, `plain_text`, `last_line`, `CommandResult` and `run_command` do not change.

**Tech Stack:** Python 3 stdlib (`signal`), pytest, `uv`.

**Spec:** `docs/superpowers/specs/verify-s-detail-always-bc0b99e0.md` (prepended above).

## Global Constraints

- Red `verified[]` row keys: exactly `command`, `ok`, `exit_code`, `tail`. Green row keys: exactly `command`, `ok`, `tail`.
- Label: `exit {n}`, or `exit {n} (signal {NAME})` for `n < 0`, with NAME from `signal.Signals(-n).name`, else the bare number.
- Separator: ` — ` (space, U+2014 EM DASH, space), the same character `verify.py:313` already uses.
- `tail` cap 300 (`plain_text` default); `detail` cap `DETAIL_MAX` = 600.
- Blank-stream diagnostic: `"no output"` (`NO_OUTPUT`). The red branch no longer uses `"{shown} exited with code N"`.
- `result` keys stay `passed`, `verified`, `detail`; `verify.py` imports nothing new beyond the stdlib `signal`.
- `run_suite` stays read-only, deterministic (no network, no model), and runs argv lists with no shell.
- Tests live in `tests/steps/test_verify.py` and take the `git` tier from the directory default; add no marker.
- Verification: `uv run pytest`, then `uv run pytest -m e2e_fake`. No lint/typecheck command exists.

## Review Focus

1. Stdout ends in a green summary while stderr's last line is unrelated (a `DeprecationWarning`): the stderr line still wins as today, and the leading `exit 1` is what keeps the row honest — pinned by `test_a_green_stdout_under_an_unrelated_stderr_line_still_leads_with_the_code`.
2. CRLF / trailing-whitespace output: the diagnostic is trimmed by `last_line`, so `tail` never ends in `\r` — pinned by `test_a_crlf_diagnostic_leaves_no_carriage_return_in_the_tail`.
3. A fake runner's odd positive code (e.g. `300`): formatted verbatim, never clamped — pinned by `test_exit_label_formats_codes_verbatim` and `test_an_out_of_range_positive_code_is_reported_verbatim`.
4. A red command given as an argv sequence: `detail` shows the space-joined argv, the row's `command` is the original sequence — pinned by `test_a_red_argv_command_keeps_its_sequence_and_shows_its_joined_argv`.
5. A red command after several green ones: only the red row gains `exit_code`; green rows keep their exact dicts — pinned by `test_only_the_red_row_after_green_rows_gains_an_exit_code`.

---

### Task 1: Exit label on every red verify row and detail

**Files:**
- Modify: `src/agent_manager/steps/verify.py:19-22` (imports), `:61-79` (add `exit_label` after `command_diagnostic`), `:303-316` (red branch of `run_suite`)
- Test: `tests/steps/test_verify.py` (imports `:28-34`; update tests at `:180-187`, `:190-200`, `:213-218`, `:234-246`, `:519-536`, `:539-551`, `:554-567`; add new tests)

**Interfaces:**
- Consumes: existing `verify.plain_text(text, max_chars=300)`, `verify.command_diagnostic(stdout, stderr, fallback)`, `verify.NO_OUTPUT`, `verify.DETAIL_MAX`, `verify.CommandResult(exit_code: int, stdout: str, stderr: str)`, `verify._display(command, argv) -> str`.
- Produces: `verify.exit_label(exit_code: int) -> str`. Red row shape `{"command": object, "ok": False, "exit_code": int, "tail": str}`.

- [ ] **Step 1: Write the failing `exit_label` tests**

In `tests/steps/test_verify.py`, change the import block at lines 28-34 to:

```python
from agent_manager.steps.verify import (
    CommandResult,
    VerifyError,
    command_diagnostic,
    exit_label,
    last_line,
    plain_text,
)
```

Then insert these tests directly after `test_command_diagnostic_never_returns_an_empty_string` (after line 116):

```python
@pytest.mark.parametrize(
    ("exit_code", "label"),
    [
        (1, "exit 1"),
        (2, "exit 2"),
        # A fake runner's out-of-range code is shown as given, never clamped.
        (300, "exit 300"),
    ],
)
def test_exit_label_formats_codes_verbatim(exit_code: int, label: str):
    assert exit_label(exit_code) == label


@pytest.mark.parametrize(
    ("exit_code", "label"),
    [
        (-9, "exit -9 (signal SIGKILL)"),
        (-15, "exit -15 (signal SIGTERM)"),
        (-11, "exit -11 (signal SIGSEGV)"),
    ],
)
def test_exit_label_names_the_signal_of_a_negative_code(exit_code: int, label: str):
    # `subprocess.run` reports a child killed by signal N as returncode -N.
    assert exit_label(exit_code) == label


def test_exit_label_falls_back_to_the_bare_number_for_an_unknown_signal():
    assert exit_label(-200) == "exit -200 (signal 200)"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -q`
Expected: collection ERROR — `ImportError: cannot import name 'exit_label' from 'agent_manager.steps.verify'`.

- [ ] **Step 3: Implement `exit_label`**

In `src/agent_manager/steps/verify.py`, change the imports at lines 19-22 to:

```python
import re
import shlex
import signal
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
```

Then insert, directly after the `command_diagnostic` function (after line 79, before `@dataclass(frozen=True)`):

```python
def exit_label(exit_code: int) -> str:
    """A red command's exit code as a human reads it.

    `exit 1` for a positive code. A negative code is `subprocess.run`'s way of
    saying the child was killed by that signal, so it is named:
    `exit -9 (signal SIGKILL)`, else `exit -200 (signal 200)` for a number the
    platform does not know. It never raises.
    """
    if exit_code >= 0:
        return f"exit {exit_code}"
    number = -exit_code
    try:
        name = signal.Signals(number).name
    except ValueError:
        name = str(number)
    return f"exit {exit_code} (signal {name})"
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -q -k exit_label`
Expected: 7 passed.

- [ ] **Step 5: Write the failing red-branch tests (new ones)**

Insert these tests in `tests/steps/test_verify.py` directly after `test_ansi_colour_and_over_long_lines_are_flattened_in_the_result` (before `test_a_command_that_cannot_be_launched_raises_verify_error`):

```python
def test_a_red_command_with_a_green_looking_last_line_names_its_exit_code(
    tmp_path: Path,
):
    # The milestone-17 incident, against a real process: the runner printed a
    # green summary and exited 1, and the result used to say only "7 passed".
    command = _py("print('7 passed'); raise SystemExit(1)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"] == [
        {"command": command, "ok": False, "exit_code": 1, "tail": "exit 1 — 7 passed"}
    ]
    assert result["detail"] == f"verification failed: {command} — exit 1 — 7 passed"


def test_a_command_killed_by_a_signal_is_reported_as_that_signal(tmp_path: Path):
    # Real, not faked: the negative-returncode convention belongs to
    # `subprocess`, and only a real child shows it. POSIX only.
    command = _py("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    row = result["verified"][0]
    assert row["exit_code"] == -9
    assert row["tail"] == "exit -9 (signal SIGKILL) — no output"
    assert result["detail"].endswith("— exit -9 (signal SIGKILL) — no output")


def test_a_signal_style_code_from_the_runner_is_labelled_with_its_name(
    tmp_path: Path,
):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=-11, stdout="", stderr="Segmentation fault\n")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["passed"] is False
    assert result["verified"] == [
        {
            "command": "fake-suite",
            "ok": False,
            "exit_code": -11,
            "tail": "exit -11 (signal SIGSEGV) — Segmentation fault",
        }
    ]
    assert (
        result["detail"]
        == "verification failed: fake-suite — exit -11 (signal SIGSEGV) — Segmentation fault"
    )


def test_an_unknown_negative_code_falls_back_to_the_bare_number(tmp_path: Path):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=-200, stdout="", stderr="")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["exit_code"] == -200
    assert result["verified"][0]["tail"] == "exit -200 (signal 200) — no output"


def test_the_exit_code_survives_over_long_and_ansi_stream_content(tmp_path: Path):
    # Truncation cuts from the end, and the label comes first, so no stream
    # content can push the code out of `tail` or `detail`.
    noisy = "\x1b[32m" + "P" * 5000 + "\x1b[0m"

    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=2, stdout=noisy, stderr="")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["exit_code"] == 2
    assert result["verified"][0]["tail"] == "exit 2 — " + "P" * 291 + "…"
    detail = result["detail"]
    assert detail.startswith("verification failed: fake-suite — exit 2 — ")
    assert len(detail) == 601
    assert "\x1b" not in detail


def test_a_green_stdout_under_an_unrelated_stderr_line_still_leads_with_the_code(
    tmp_path: Path,
):
    # stderr's last line still wins over stdout (unchanged preference order);
    # the leading exit label is what stops the green stdout from misleading.
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(
            exit_code=1,
            stdout="7 passed\n",
            stderr="DeprecationWarning: old API\n",
        )

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["tail"] == "exit 1 — DeprecationWarning: old API"
    assert (
        result["detail"]
        == "verification failed: fake-suite — exit 1 — DeprecationWarning: old API"
    )


def test_a_crlf_diagnostic_leaves_no_carriage_return_in_the_tail(tmp_path: Path):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=1, stdout="", stderr="boom  \r\n\r\n")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["tail"] == "exit 1 — boom"
    assert result["detail"] == "verification failed: fake-suite — exit 1 — boom"


def test_an_out_of_range_positive_code_is_reported_verbatim(tmp_path: Path):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=300, stdout="", stderr="weird\n")

    result = verify.run_suite(["fake-suite"], str(tmp_path), runner=runner)
    assert result["verified"][0]["exit_code"] == 300
    assert result["verified"][0]["tail"] == "exit 300 — weird"


def test_a_red_argv_command_keeps_its_sequence_and_shows_its_joined_argv(
    tmp_path: Path,
):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=1, stdout="", stderr="")

    result = verify.run_suite([("fake", "suite")], str(tmp_path), runner=runner)
    assert result["verified"] == [
        {
            "command": ("fake", "suite"),
            "ok": False,
            "exit_code": 1,
            "tail": "exit 1 — no output",
        }
    ]
    assert result["detail"] == "verification failed: fake suite — exit 1 — no output"


def test_only_the_red_row_after_green_rows_gains_an_exit_code(tmp_path: Path):
    # The passing path is unchanged: green rows keep their exact three keys.
    def runner(argv: list[str], cwd: str) -> CommandResult:
        if argv == ["red"]:
            return CommandResult(exit_code=1, stdout="1 failed\n", stderr="")
        return CommandResult(exit_code=0, stdout="fine\n", stderr="")

    result = verify.run_suite(["one", "two", "red"], str(tmp_path), runner=runner)
    assert result["passed"] is False
    assert result["verified"] == [
        {"command": "one", "ok": True, "tail": "fine"},
        {"command": "two", "ok": True, "tail": "fine"},
        {"command": "red", "ok": False, "exit_code": 1, "tail": "exit 1 — 1 failed"},
    ]
    assert set(result) == {"passed", "verified", "detail"}
```

- [ ] **Step 6: Update the seven existing red-path tests to the new format**

Replace `test_a_red_command_reports_its_stderr_line` (lines 180-187) with:

```python
def test_a_red_command_reports_its_stderr_line(tmp_path: Path):
    command = _py("import sys; sys.stderr.write('AssertionError: boom\\n'); sys.exit(1)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"] == [
        {
            "command": command,
            "ok": False,
            "exit_code": 1,
            "tail": "exit 1 — AssertionError: boom",
        }
    ]
    assert (
        result["detail"]
        == f"verification failed: {command} — exit 1 — AssertionError: boom"
    )
```

Replace `test_a_red_command_with_a_stdout_only_diagnostic_reports_that_stdout_line` (lines 190-200) with:

```python
def test_a_red_command_with_a_stdout_only_diagnostic_reports_that_stdout_line(
    tmp_path: Path,
):
    # The card's reason to exist, proved against a real process rather than a
    # fake: the tool prints its diagnostic to stdout and exits non-zero.
    command = _py("print('ERROR: 3 lint problems'); raise SystemExit(2)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"][0]["ok"] is False
    assert result["verified"][0]["exit_code"] == 2
    assert result["verified"][0]["tail"] == "exit 2 — ERROR: 3 lint problems"
    assert "ERROR: 3 lint problems" in result["detail"]
```

Replace `test_a_silent_red_command_still_carries_a_tail_and_a_detail` (lines 213-218) with:

```python
def test_a_silent_red_command_still_carries_a_tail_and_a_detail(tmp_path: Path):
    command = _py("raise SystemExit(3)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["verified"][0]["tail"] == "exit 3 — no output"
    assert result["detail"] == f"verification failed: {command} — exit 3 — no output"
```

Replace `test_ansi_colour_and_over_long_lines_are_flattened_in_the_result` (lines 234-246) with:

```python
def test_ansi_colour_and_over_long_lines_are_flattened_in_the_result(tmp_path: Path):
    # A fake runner, because no real tool can be relied on to emit ANSI on
    # demand -- and a raw ESC byte in a reported field once failed a milestone.
    noisy = "\x1b[31m" + "E" * 400 + "\x1b[0m"

    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=1, stdout="", stderr=noisy)

    result = verify.run_suite(["fake-linter"], str(tmp_path), runner=runner)
    assert result["verified"][0]["tail"] == "exit 1 — " + "E" * 291 + "…"
    # `detail` is flattened too, and carries its own larger cap (600), so it is
    # not silently clipped to a per-command tail's 300.
    assert result["detail"] == f"verification failed: fake-linter — exit 1 — {'E' * 400}"
```

Replace `test_a_failing_typecheck_fails_the_suite_and_stops_lint` (lines 519-536) with:

```python
def test_a_failing_typecheck_fails_the_suite_and_stops_lint(tmp_path: Path):
    marker = tmp_path / "lint-ran.txt"
    suite = _py("print('5 passed')")
    typecheck = _py(
        "import sys; sys.stderr.write('error: 2 type errors\\n'); sys.exit(1)"
    )
    lint = _py(f"open({str(marker)!r}, 'w').write('ran')")
    explore = {"verification": {"typecheck": typecheck, "lint": [lint]}}

    result = verify.run_suite([suite], str(tmp_path), explore)

    assert result["passed"] is False
    assert result["verified"] == [
        {"command": suite, "ok": True, "tail": "5 passed"},
        {
            "command": typecheck,
            "ok": False,
            "exit_code": 1,
            "tail": "exit 1 — error: 2 type errors",
        },
    ]
    assert (
        result["detail"]
        == f"verification failed: {typecheck} — exit 1 — error: 2 type errors"
    )
    assert not marker.exists()
```

Replace `test_a_silent_failing_typecheck_still_carries_a_tail_and_a_detail` (lines 539-551) with:

```python
def test_a_silent_failing_typecheck_still_carries_a_tail_and_a_detail(tmp_path: Path):
    typecheck = _py("raise SystemExit(3)")
    explore = {"verification": {"typecheck": typecheck, "lint": []}}
    result = verify.run_suite([_py("print('ok')")], str(tmp_path), explore)
    assert result["passed"] is False
    assert result["verified"][-1] == {
        "command": typecheck,
        "ok": False,
        "exit_code": 3,
        "tail": "exit 3 — no output",
    }
    assert result["detail"].startswith("verification failed:")
    assert "— exit 3 — no output" in result["detail"]
```

Replace `test_a_failing_lint_after_a_green_typecheck_names_the_lint_command` (lines 554-567) with:

```python
def test_a_failing_lint_after_a_green_typecheck_names_the_lint_command(tmp_path: Path):
    typecheck = _py("print('0 errors')")
    first_lint = _py("print('E501 line too long'); raise SystemExit(1)")
    marker = tmp_path / "second-lint-ran.txt"
    second_lint = _py(f"open({str(marker)!r}, 'w').write('ran')")
    explore = {"verification": {"typecheck": typecheck, "lint": [first_lint, second_lint]}}

    result = verify.run_suite([_py("print('ok')")], str(tmp_path), explore)

    assert result["passed"] is False
    assert [entry["ok"] for entry in result["verified"]] == [True, True, False]
    assert result["verified"][-1]["command"] == first_lint
    assert result["verified"][-1]["tail"] == "exit 1 — E501 line too long"
    assert (
        result["detail"]
        == f"verification failed: {first_lint} — exit 1 — E501 line too long"
    )
    assert not marker.exists()
```

Leave every other test (including `test_command_diagnostic_falls_back_to_the_bare_message_when_both_are_blank`, which tests the helper directly, and all green-path tests) untouched.

- [ ] **Step 7: Run the file to verify the red-path tests fail**

Run: `uv run pytest tests/steps/test_verify.py -q`
Expected: FAIL — the 10 new red-branch tests and the 7 updated tests fail (e.g. `KeyError: 'exit_code'` or tail `'7 passed' != 'exit 1 — 7 passed'`); the `exit_label` tests and all green-path tests pass.

- [ ] **Step 8: Rewrite the red branch of `run_suite`**

In `src/agent_manager/steps/verify.py`, replace lines 303-316:

```python
        shown = _display(command, argv)
        diagnostic = command_diagnostic(
            completed.stdout,
            completed.stderr,
            f"{shown} exited with code {completed.exit_code}",
        )
        verified.append(
            {"command": command, "ok": False, "tail": plain_text(diagnostic)}
        )
        result["detail"] = plain_text(
            f"verification failed: {shown} — {diagnostic}", DETAIL_MAX
        )
        # Nothing is marked done after a red command (ship.mjs:89).
        return result
```

with:

```python
        shown = _display(command, argv)
        # The label leads, so no stream content -- a green-looking summary, or
        # a line long enough to be truncated -- can hide that the command
        # failed. No fallback: the label already carries the code.
        label = exit_label(completed.exit_code)
        diagnostic = command_diagnostic(completed.stdout, completed.stderr, None)
        verified.append(
            {
                "command": command,
                "ok": False,
                "exit_code": completed.exit_code,
                "tail": plain_text(f"{label} — {diagnostic}"),
            }
        )
        result["detail"] = plain_text(
            f"verification failed: {shown} — {label} — {diagnostic}", DETAIL_MAX
        )
        # Nothing is marked done after a red command (ship.mjs:89).
        return result
```

- [ ] **Step 9: Run the file to verify everything passes**

Run: `uv run pytest tests/steps/test_verify.py -q`
Expected: all passed, 0 failed.

- [ ] **Step 10: Run the default suite and the e2e_fake tier**

Run: `uv run pytest`
Expected: all passed (no failures; `tests/test_comments.py`, `tests/steps/test_reducers.py`, `tests/test_integration.py`, `tests/test_integrate_workflow.py` unaffected).

Run: `uv run pytest -m e2e_fake`
Expected: all passed.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/steps/verify.py tests/steps/test_verify.py
git commit -m "fix: verify's red row and detail always name the exit code (bc0b99e0)"
```

---

## Self-Review

- **Spec coverage:** exit-label table → Step 1/3 (`exit_label` + parametrized tests, incl. unknown signal); red row with integer `exit_code` and `label — diagnostic` tail → Step 5/8; `detail` format and 600 cap → Steps 5/6/8; dropped `"exited with code"` fallback → Step 8 (fallback `None` → `NO_OUTPUT`), Steps 6 (silent tests); new tests 1-5 → Step 5 (first five tests); new test 6 (green rows carry no `exit_code`) → `test_only_the_red_row_after_green_rows_gains_an_exit_code`; all seven listed existing tests → Step 6; unchanged green path / short-circuit / `VerifyError` / `ValueError` → existing tests left untouched and re-run in Steps 9-10; only stdlib `signal` imported → Step 3; verification commands → Step 10.
- **Placeholders:** none; every code step shows full code.
- **Type consistency:** `exit_label(exit_code: int) -> str` used identically in Steps 1, 3, 8; row key `exit_code` holds `completed.exit_code` (int).
- **Review Focus:** each of the five lines has a named test in Step 1 or Step 5.
<!-- task-pipeline: validated -->
