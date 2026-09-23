<!-- task-pipeline: validated -->
# Subtask 9c3b1ffb — Add `verify.run_suite`

Parent story 653382db "Deterministic steps: worktree, plan-check, verify". Blocked by d3feb87e (done). Siblings 0816e239 (`steps/worktree.py`) and d3feb87e (`steps/plan_check.py`) are done and are not touched by this card.

## Scope

One new module, `src/agent_manager/steps/verify.py`, and one new test file, `tests/steps/test_verify.py`. The module ports the verification half of the leave-me-alone plugin's `scripts/ship.mjs` (`ship()` at :70-90, `verifyError()` at :62-67) plus the `plainText`/`lastLine` text-flattening helpers from `scripts/gh.mjs` (:40-48, :70-73).

`run_suite` is the `run` target of the `verify` phase declared in design §5 task.yaml (lines 215-218: `kind: deterministic`, `run: verify.run_suite`, `gates: [verification_passed_gate]`). It runs each verification command inside the subtask worktree and reports, per command, whether it passed and one usable line of diagnostic.

Out of scope, explicitly:

- The `verification_passed_gate` named by task.yaml. It does not exist in `steps/reducers.py` and is not in design §5's "Reducers to port faithfully" list; adding or wiring it is another card's work. This card delivers only the step function.
- Pushing, opening a PR, committing, tagging — `ship.mjs`'s name is historical; that half never ports.
- The dirty-worktree check at `ship.mjs`:76-80. Design §5 (lines 243-244) gives "dirty worktree, zero commits, or untagged commits stop the run" to `review_gate`. Duplicating it here would split ownership of one rule.
- Any harness or model call, any network access (design §6: "No network, no model"), and any mutation of the worktree or repo. Design §9 states plainly: "`verify.run_suite` is read-only."

## Public surface

- `run_suite(commands, worktree, *, runner=run_command) -> dict[str, object]` — the phase result. A plain dict, not a Pydantic model: it crosses no process boundary, so per `CLAUDE.md` and both sibling steps it stays a dict.
- `run_command` — the default runner, a module-level callable that executes one argv list with `cwd` set to the worktree, captures stdout and stderr as text, and returns the completed process (exit code, stdout, stderr). It is a parameter so tests can force outcomes a real process will not produce on demand, mirroring the `GitRunner` seam in `worktree.py` and the `DirLister`/`FileReader` seam in `plan_check.py`.
- `last_line(text) -> str` and `plain_text(text, max=300) -> str` — pure ports of `gh.mjs`'s `lastLine` and `plainText`, exported because they are the specified behaviour and are asserted directly.
- `command_diagnostic(stdout, stderr, fallback) -> str` — the pure port of `verifyError`'s preference order, separated from the subprocess so the rule can be tested without running anything.
- A `VerifyError` (or equivalently named) exception for the one case that is a bug rather than a red suite: a command that cannot be launched at all (executable missing or unrunnable). Malformed input (`commands`, or `worktree`) is `ValueError`, not `VerifyError` — see Error paths below.

## Observable behaviour

**Commands.** Each entry of `commands` is a verification command as it appears in a card's `verification.fullSuite` — in practice a single string such as `"uv run pytest"`. Commands are run as argument lists, never through a shell (design §5 line 252: "the program runs commands itself with argument lists"; `shell_quote` does not port). A string command is split into an argv list with `shlex.split`; a command that is already a list or tuple is used as-is. `ship.mjs` passed `shell: true`, which this port deliberately drops — the observed command shapes carry no shell metacharacters, and a shell adds a quoting hazard the design refuses. Empty and whitespace-only entries are skipped, as `verify.filter(Boolean)` did. A command that is neither a string nor a sequence of strings, or that splits to an empty argv, raises `ValueError` before anything runs.

**Worktree.** `worktree` must be an existing absolute directory; otherwise `ValueError` before anything runs, the same pre-flight shape `worktree.ensure` uses. Every command runs with `cwd` set to it. The step neither creates nor alters anything under it.

**Stop on first red.** Commands run in order. The first non-zero exit stops the suite: later commands are not run, exactly as `ship.mjs` returned early so that "nothing is marked done after a red command".

**Result dict.**

- `passed: bool` — true only when every non-empty command exited zero. An empty command list yields `passed: true` with an empty `verified` list; refusing to verify nothing is `verification_gate`'s job (design §5 lines 240-242), not this step's.
- `verified: list[dict]` — one entry per command actually run, in order, each `{"command": <the command as given>, "ok": bool, "tail": str}`. `tail` for a green command is `plain_text(last_line(stdout))`; for a red command it is `plain_text(command_diagnostic(...))`.
- `detail: str` — `""` when everything passed; on failure, `plain_text(f"verification failed: {command} — {diagnostic}", 600)`. Always a string, never `None`, so a consumer formats it without a guard (the same rule `plan_check` applies to its `path`).

**Diagnostic preference order** (`ship.mjs`:53-67 records the incident): prefer the last non-empty line of *stderr*; if that is empty, fall back to the last non-empty line of *stdout*; if both are empty, fall back to a bare message naming the command and its exit code. Reading only stderr produced seven content-free failures in the original, because linters and gate scripts print their diagnostic to stdout and exit non-zero. This order is the point of the card and is asserted directly.

**Text flattening** (`plain_text`, ported from `gh.mjs`:40-48): drop ANSI escape sequences, replace remaining control characters with a space, trim, and truncate to `max` characters with a single-character ellipsis appended. A tail is a human hint, not a payload. Default cap 300 for per-command tails, 600 for `detail`.

## Error paths

| Situation | Behaviour |
| --- | --- |
| Command exits non-zero | Not an exception. `passed: false`, the command's entry has `ok: false` and a diagnostic tail, `detail` names the command, remaining commands are skipped. |
| Command exits non-zero with empty stdout and stderr | Same, with a synthesised fallback message naming the command and exit code — never an empty `tail` or `detail`. |
| Executable not found / cannot be launched (`FileNotFoundError`, `PermissionError`) | `VerifyError`. A missing binary is a misconfigured card, not a failed test run, and must not read as an ordinary red suite. |
| `commands` not iterable, or an entry of the wrong type / splitting to empty argv | `ValueError`, raised before any process starts. |
| `worktree` missing, relative, or not a directory | `ValueError`, raised before any process starts. |
| Binary or undecodable output | Decoded leniently (errors replaced) and flattened by `plain_text`; never crashes the step. |

## Test list

All tests live in `tests/steps/test_verify.py`. Design §14 (lines 477-493) places `verify.py` in the **Steps** tier, tested "against temporary git repositories and a temporary `brd` board; no network" — so behaviour is exercised with real subprocess execution of trivial real commands (`sys.executable -c "..."`) inside a real `tmp_path` directory, and a fake injected runner is reserved only for outcomes a real process will not produce on demand. `tests/steps/test_plan_check.py`'s docstring states the same rule for its own module; this file carries the equivalent docstring.

Pure helpers (`last_line`, `plain_text`, `command_diagnostic`) are unit-tested in the same file: they are pure ports of `.mjs` helpers whose behaviour is the specification (design §14, Pure-functions tier), and they live in a Steps module, so they are asserted directly rather than only through a subprocess.

1. **Pure tier** — `last_line` returns the last non-empty trimmed line, ignoring tool-manager banners above it; returns `""` for empty, whitespace-only, and `None` input.
2. **Pure tier** — `plain_text` strips ANSI sequences, collapses control characters to a space, trims, and truncates past the cap with an ellipsis; text at exactly the cap is untruncated.
3. **Pure tier** — `command_diagnostic` prefers stderr's last non-empty line; falls back to stdout's last non-empty line when stderr is blank (the `gate-frontend.sh` incident); falls back to the bare command/exit-code message when both are blank.
4. **Steps tier, real subprocess** — a single real green command in `tmp_path`: `passed` is true, one `verified` entry with `ok: true`, `tail` is the command's last stdout line, `detail` is `""`.
5. **Steps tier, real subprocess** — several real green commands all run, in order, and all appear in `verified`.
6. **Steps tier, real subprocess** — a real command that prints to stderr and exits non-zero: `passed` false, `ok` false, the stderr line is the tail, `detail` names the command.
7. **Steps tier, real subprocess** — a real command that prints its diagnostic to **stdout only** and exits non-zero: the stdout line is reported. This is the card's reason to exist and is proved against a real process, not a fake.
8. **Steps tier, real subprocess** — a red command stops the suite: a following command that would create a marker file in `tmp_path` never runs, proved by the file's absence, and `verified` has only the first entry.
9. **Steps tier, real subprocess** — commands run with `cwd` set to the given worktree, proved by a command that prints its own working directory.
10. **Steps tier, real git repo** — after `run_suite` over a real temporary git worktree, `git status --porcelain` is still empty and `HEAD` is unchanged: the step is read-only (design §9).
11. **Steps tier, real subprocess** — a command exiting non-zero with no output at all yields a non-empty `tail` and `detail`.
12. **Steps tier, injected fake runner** — a runner whose output carries ANSI colour and an over-long line proves the flattening and truncation reach the result; a real tool cannot be relied on to emit these on demand.
13. **Steps tier, injected fake runner** — a runner raising `FileNotFoundError` surfaces as `VerifyError`, not as `passed: false`.
14. **Steps tier** — input validation: empty and whitespace-only command entries are skipped; an empty `commands` list gives `passed: true` with an empty `verified`; a non-string/non-sequence entry and a missing or relative `worktree` each raise `ValueError` with no process started (asserted with a runner that records calls).
15. **Steps tier** — no shell: a command string is split with `shlex.split`, and a command containing shell metacharacters is passed through as literal argv rather than interpreted (asserted with a recording runner).

Verification command for this card: `uv run pytest`. No separate lint or typecheck step exists.

---

# verify.run_suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/steps/verify.py`, a read-only deterministic step that runs a card's verification commands inside its worktree and reports, per command, pass/fail plus one usable line of diagnostic.

**Architecture:** One module with three pure helpers (`last_line`, `plain_text`, `command_diagnostic`), an injectable runner seam (`CommandRunner`/`run_command`, mirroring `worktree.GitRunner`), and `run_suite`, which validates its inputs, splits command strings into argv with `shlex.split`, runs each with `cwd` set to the worktree, stops at the first non-zero exit, and returns a plain dict `{"passed", "verified", "detail"}`. Tests live in the Steps tier at `tests/steps/test_verify.py` and drive real `sys.executable -c "..."` subprocesses in `tmp_path`, with a fake runner reserved for outcomes a real process will not produce on demand.

**Tech Stack:** Python 3, `subprocess`, `shlex`, `re`, `dataclasses`; pytest with `tmp_path`; `uv run pytest` is the only verification command.

**Spec:** `docs/superpowers/specs/task-add-verify-run-suite-9c3b1ffb-design.md` (reproduced verbatim above)

## Global Constraints

- Branch `m1/task-add-verify-run-suite-9c3b1ffb`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-add-verify-run-suite-9c3b1ffb`. Only `src/agent_manager/steps/verify.py` and `tests/steps/test_verify.py` are created; `steps/worktree.py`, `steps/plan_check.py` and `steps/reducers.py` are not modified.
- The whole verification command set for this repo is `uv run pytest`. There is no lint or typecheck command (`CLAUDE.md`).
- The result of `run_suite` is a plain `dict`, never a Pydantic model: it crosses no process boundary (`CLAUDE.md`, spec "Public surface").
- Commands are never run through a shell: no `shell=True`, no shell string building (design §5 line 252, spec "Observable behaviour / Commands").
- The step is read-only: it never creates, deletes, commits, pushes, tags, or modifies anything under the worktree (design §9: "`verify.run_suite` is read-only").
- No network, no model or harness call anywhere in this module (design §6: "No network, no model").
- `verification_passed_gate` and the dirty-worktree check are explicitly out of scope and must not appear in this module.
- `detail` is always a `str` — `""` on success, never `None`.
- Text caps: 300 characters for a per-command `tail`, 600 for `detail`; the truncation marker is the single character `…` (U+2026).

## Review Focus

- **CRLF output.** A tool that emits `first\r\nsecond\r\n` must yield `second`, not `second\r` — pinned in Task 1.
- **Quoted arguments in a command string.** `uv run pytest -k 'not slow'` must split to five argv elements with `not slow` intact, not six — pinned in Task 5.
- **Output that is not valid UTF-8.** A command writing raw bytes must be decoded with replacement and still produce a tail, never a `UnicodeDecodeError` — pinned in Task 4.
- **Megabytes of output from a real green command.** The tail must be capped at 300 characters plus the ellipsis, not carried whole into the result — pinned in Task 4.
- **`commands` as a tuple of argv tuples and `worktree` as a `pathlib.Path`.** Both are natural call shapes and must be accepted, with `cwd` handed to the runner as a `str` — pinned in Task 5.

---

### Task 1: Pure text helpers — `last_line` and `plain_text`

**Files:**
- Create: `src/agent_manager/steps/verify.py`
- Test: `tests/steps/test_verify.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `last_line(text: object) -> str`; `plain_text(text: object, max_chars: int = 300) -> str`; module constant `ELLIPSIS = "…"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/steps/test_verify.py` with exactly this content:

```python
"""Behaviour of the verification step (design §4 `steps/`, spec card 9c3b1ffb).

Placement follows design §14: `verify.py` is a Steps component, so its
behaviour is exercised against real subprocesses (`sys.executable -c ...`) and
real directories created in `tmp_path` -- no network, and no faking of the
runner except where a test must force an outcome a real process will not
produce on demand (ANSI-coloured over-long output, an unlaunchable
executable), exactly as `tests/steps/test_plan_check.py` reserves its fake
filesystem.

The pure helpers ported from `gh.mjs` (`last_line`, `plain_text`) and from
`ship.mjs` (`command_diagnostic`) are asserted directly: their behaviour is the
specification (design §14, Pure-functions tier).
"""

from agent_manager.steps.verify import last_line, plain_text


def test_last_line_is_the_last_non_empty_trimmed_line():
    # Tool managers (mise, direnv, nvm) print an activation banner above the
    # output we actually want; the value is the LAST line (gh.mjs:68-73).
    assert last_line("mise tools: python@3.12\n  42 passed  \n\n") == "42 passed"


def test_last_line_is_empty_for_empty_whitespace_and_none():
    assert last_line("") == ""
    assert last_line("   \n\t\n") == ""
    assert last_line(None) == ""


def test_last_line_drops_the_carriage_return_of_crlf_output():
    assert last_line("first\r\nsecond\r\n") == "second"


def test_plain_text_strips_ansi_sequences_and_control_characters():
    assert plain_text("\x1b[31mred\x1b[0m\tfail\x07") == "red fail"


def test_plain_text_truncates_past_the_cap_with_one_ellipsis():
    assert plain_text("x" * 301, 300) == "x" * 300 + "…"
    assert plain_text("x" * 300, 300) == "x" * 300
    assert plain_text("y" * 400) == "y" * 300 + "…"


def test_plain_text_is_the_empty_string_for_none():
    assert plain_text(None) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'agent_manager.steps.verify'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/steps/verify.py` with exactly this content:

```python
"""Run a card's verification commands in its worktree and report what happened.

A deterministic step (design §4 `steps/`, §6 "The engine calls `run(ctx) -> dict`.
No network, no model."): it launches the commands the card names and reads their
output. Ported from `ship()` and `verifyError()` in the leave-me-alone plugin's
`scripts/ship.mjs` (:70-90, :62-67), plus the `plainText`/`lastLine` flattening
helpers from `scripts/gh.mjs` (:40-48, :70-73).

Read-only (design §9: "`verify.run_suite` is read-only"). It never commits,
pushes, tags or opens a PR -- `ship.mjs`'s name is historical and that half does
not port -- and it does NOT check for a dirty worktree: design §5 gives that
rule to `review_gate`, and one rule lives in one place.

Every invocation is an argument list handed to `subprocess` (design §5 line
252). `ship.mjs` passed `shell: true`; this port deliberately drops it, so
there is no shell string and nothing to quote.
"""

import re

ELLIPSIS = "…"
"""One character, appended to text `plain_text` had to cut."""

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def last_line(text: object) -> str:
    """The last non-empty, trimmed line of `text`, or `""`.

    Ported from `gh.mjs`'s `lastLine`: tool managers print activation banners
    above real output, so the value asked for is the last line, not the first.
    `.strip()` also removes the `\\r` of CRLF output, which would otherwise
    travel into the result.
    """
    raw = "" if text is None else str(text)
    lines = [line.strip() for line in raw.split("\n")]
    hits = [line for line in lines if line]
    return hits[-1] if hits else ""


def plain_text(text: object, max_chars: int = 300) -> str:
    """`text` flattened to printable, length-capped text.

    Ported from `gh.mjs`'s `plainText`: ANSI sequences removed, remaining
    control characters collapsed to a single space, trimmed, then truncated
    with one `ELLIPSIS`. A raw ESC byte surviving into a reported field once
    failed a whole milestone after its PRs were already open, and a tail is a
    human hint rather than a payload -- hence both halves.
    """
    raw = "" if text is None else str(text)
    flat = _CONTROL.sub(" ", _ANSI.sub("", raw)).strip()
    return flat if len(flat) <= max_chars else flat[:max_chars] + ELLIPSIS
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add tests/steps/test_verify.py src/agent_manager/steps/verify.py
git commit -m "feat(verify): port last_line and plain_text from gh.mjs"
```

---

### Task 2: The diagnostic preference order — `command_diagnostic`

**Files:**
- Modify: `src/agent_manager/steps/verify.py`
- Test: `tests/steps/test_verify.py`

**Interfaces:**
- Consumes: `last_line` from Task 1.
- Produces: `command_diagnostic(stdout: object, stderr: object, fallback: object) -> str`, never returning `""`.

- [ ] **Step 1: Write the failing tests**

In `tests/steps/test_verify.py`, replace the import line

```python
from agent_manager.steps.verify import last_line, plain_text
```

with

```python
from agent_manager.steps.verify import command_diagnostic, last_line, plain_text
```

and append these tests to the end of the file:

```python
def test_command_diagnostic_prefers_the_last_stderr_line():
    assert (
        command_diagnostic("out line", "boom: failed\n", "fallback")
        == "boom: failed"
    )


def test_command_diagnostic_falls_back_to_stdout_when_stderr_is_blank():
    # The reason this card exists: linters and gate scripts print their
    # diagnostic to stdout and exit non-zero. Reading only stderr produced
    # seven content-free "Command failed: ./scripts/gate-frontend.sh" failures
    # in the original (ship.mjs:53-67).
    assert (
        command_diagnostic("banner\nERROR: 3 lint problems\n", "  \n", "fallback")
        == "ERROR: 3 lint problems"
    )


def test_command_diagnostic_falls_back_to_the_bare_message_when_both_are_blank():
    assert (
        command_diagnostic("", "", "./gate.sh exited with code 2")
        == "./gate.sh exited with code 2"
    )


def test_command_diagnostic_never_returns_an_empty_string():
    # A blank tail reads as "we do not know why it failed", which is exactly
    # the outcome this port was written to end.
    assert command_diagnostic("", "", "   ") == "no output"
    assert command_diagnostic(None, None, None) == "no output"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: collection error — `ImportError: cannot import name 'command_diagnostic' from 'agent_manager.steps.verify'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/steps/verify.py`:

```python
NO_OUTPUT = "no output"
"""Last-resort diagnostic, so no reported tail or detail is ever blank."""


def command_diagnostic(stdout: object, stderr: object, fallback: object) -> str:
    """One line saying why a command failed, from whichever stream carries it.

    Ported from `ship.mjs`'s `verifyError`. `gh.mjs`'s `ghError` read only
    stderr, which is right for `gh` and `git` and wrong here: verification
    commands are arbitrary repo scripts, and linters and gate scripts routinely
    print their diagnostic to STDOUT and exit non-zero. So: last non-empty line
    of stderr, else last non-empty line of stdout, else `fallback`.
    """
    return (
        last_line(stderr)
        or last_line(stdout)
        or ("" if fallback is None else str(fallback).strip())
        or NO_OUTPUT
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add tests/steps/test_verify.py src/agent_manager/steps/verify.py
git commit -m "feat(verify): port verifyError's stderr-then-stdout preference order"
```

---

### Task 3: `run_suite` green path, the runner seam, and read-only proof

**Files:**
- Modify: `src/agent_manager/steps/verify.py`
- Test: `tests/steps/test_verify.py`

**Interfaces:**
- Consumes: `last_line`, `plain_text` from Task 1.
- Produces: `CommandResult` (frozen dataclass with fields `exit_code: int`, `stdout: str`, `stderr: str`); `CommandRunner = Callable[[list[str], str], CommandResult]`; `run_command(argv: list[str], cwd: str) -> CommandResult`; `run_suite(commands, worktree, *, runner: CommandRunner = run_command) -> dict[str, object]` returning `{"passed": bool, "verified": list[dict], "detail": str}`.

- [ ] **Step 1: Write the failing tests**

In `tests/steps/test_verify.py`, replace the import block

```python
from agent_manager.steps.verify import command_diagnostic, last_line, plain_text
```

with

```python
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_manager.steps import verify
from agent_manager.steps.verify import (
    CommandResult,
    command_diagnostic,
    last_line,
    plain_text,
)

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the verify step's read-only test",
)


def _py(script: str) -> str:
    """A verification command, as a card writes one: a single shell-free string.

    Quoted with `shlex.quote` so `run_suite`'s own `shlex.split` reconstructs
    exactly this argv -- the round trip a real card's `"uv run pytest"` makes.
    """
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd` for test setup or assertions, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout
```

and append these tests to the end of the file:

```python
def test_a_single_green_command_passes_with_its_last_stdout_line(tmp_path: Path):
    command = _py("print('banner'); print('7 passed')")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is True
    assert result["detail"] == ""
    assert result["verified"] == [{"command": command, "ok": True, "tail": "7 passed"}]


def test_every_green_command_runs_in_order(tmp_path: Path):
    first = _py("print('one')")
    second = _py("print('two')")
    third = _py("print('three')")
    result = verify.run_suite([first, second, third], str(tmp_path))
    assert result["passed"] is True
    assert [entry["command"] for entry in result["verified"]] == [first, second, third]
    assert [entry["tail"] for entry in result["verified"]] == ["one", "two", "three"]


def test_commands_run_with_cwd_set_to_the_worktree(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    command = _py("import os; print(os.path.realpath(os.getcwd()))")
    result = verify.run_suite([command], str(worktree))
    assert result["verified"][0]["tail"] == os.path.realpath(worktree)


def test_an_empty_command_list_passes_with_nothing_verified(tmp_path: Path):
    # Refusing to verify nothing belongs to `verification_gate` (design §5),
    # not to this step.
    assert verify.run_suite([], str(tmp_path)) == {
        "passed": True,
        "verified": [],
        "detail": "",
    }


@requires_git
def test_run_suite_leaves_the_worktree_and_the_repo_untouched(tmp_path: Path):
    # Design §9: "`verify.run_suite` is read-only."
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "agent-manager tests")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("hello")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    result = verify.run_suite([_py("print('green')")], str(repo))

    assert result["passed"] is True
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: collection error — `ImportError: cannot import name 'CommandResult' from 'agent_manager.steps.verify'`

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/verify.py`, replace the import line

```python
import re
```

with

```python
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
```

and append to the end of the file:

```python
@dataclass(frozen=True)
class CommandResult:
    """One finished command: exit code plus its captured streams.

    A plain dataclass, not a Pydantic model: internal-only state that crosses
    no process boundary (`CLAUDE.md`).
    """

    exit_code: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str], str], CommandResult]
"""Takes an argv and a working directory, returns a `CommandResult`.

Raises `FileNotFoundError` or `PermissionError` if the command cannot be
launched at all; a non-zero exit is a return value, not an exception.
"""


def run_command(argv: list[str], cwd: str) -> CommandResult:
    """The default `CommandRunner`: really run `argv` in `cwd`.

    `shell=False` (the default) is the whole point -- see the module docstring.
    `errors="replace"` keeps a command that emits non-UTF-8 bytes from crashing
    the step; its diagnostic still has to reach a human.
    """
    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return CommandResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_suite(
    commands: object,
    worktree: object,
    *,
    runner: CommandRunner = run_command,
) -> dict[str, object]:
    """Run each verification command in `worktree` and report what happened.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    `runner` defaults to real execution and exists to be swapped in tests, the
    same callable-injection seam `worktree.py` uses for git.
    """
    worktree_path = str(worktree)
    result: dict[str, object] = {"passed": False, "verified": [], "detail": ""}
    verified: list[dict[str, object]] = result["verified"]  # type: ignore[assignment]

    for command in commands:
        argv = shlex.split(command) if isinstance(command, str) else [str(p) for p in command]
        completed = runner(argv, worktree_path)
        verified.append(
            {
                "command": command,
                "ok": True,
                "tail": plain_text(last_line(completed.stdout)),
            }
        )

    result["passed"] = True
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add tests/steps/test_verify.py src/agent_manager/steps/verify.py
git commit -m "feat(verify): add run_suite's green path and the runner seam"
```

---

### Task 4: Red commands — diagnostics, stop-on-first-red, and `VerifyError`

**Files:**
- Modify: `src/agent_manager/steps/verify.py`
- Test: `tests/steps/test_verify.py`

**Interfaces:**
- Consumes: `CommandResult`, `run_suite`, `plain_text`, `last_line`, `command_diagnostic` from Tasks 1-3.
- Produces: `VerifyError(message: str, *, argv: list[str])` (subclass of `RuntimeError`, attributes `message` and `argv`); `run_suite` now returns `passed: False` with a populated `detail` on a red command.

- [ ] **Step 1: Write the failing tests**

In `tests/steps/test_verify.py`, replace the import block

```python
from agent_manager.steps.verify import (
    CommandResult,
    command_diagnostic,
    last_line,
    plain_text,
)
```

with

```python
from agent_manager.steps.verify import (
    CommandResult,
    VerifyError,
    command_diagnostic,
    last_line,
    plain_text,
)
```

and append these tests to the end of the file:

```python
def test_a_red_command_reports_its_stderr_line(tmp_path: Path):
    command = _py("import sys; sys.stderr.write('AssertionError: boom\\n'); sys.exit(1)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"] == [
        {"command": command, "ok": False, "tail": "AssertionError: boom"}
    ]
    assert result["detail"] == f"verification failed: {command} — AssertionError: boom"


def test_a_red_command_with_a_stdout_only_diagnostic_reports_that_stdout_line(
    tmp_path: Path,
):
    # The card's reason to exist, proved against a real process rather than a
    # fake: the tool prints its diagnostic to stdout and exits non-zero.
    command = _py("print('ERROR: 3 lint problems'); raise SystemExit(2)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is False
    assert result["verified"][0]["ok"] is False
    assert result["verified"][0]["tail"] == "ERROR: 3 lint problems"
    assert "ERROR: 3 lint problems" in result["detail"]


def test_a_red_command_stops_the_commands_after_it(tmp_path: Path):
    marker = tmp_path / "second-ran.txt"
    red = _py("raise SystemExit(1)")
    second = _py(f"open({str(marker)!r}, 'w').write('ran')")
    result = verify.run_suite([red, second], str(tmp_path))
    assert result["passed"] is False
    assert [entry["command"] for entry in result["verified"]] == [red]
    assert not marker.exists()


def test_a_silent_red_command_still_carries_a_tail_and_a_detail(tmp_path: Path):
    command = _py("raise SystemExit(3)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["verified"][0]["tail"] == f"{command} exited with code 3"
    assert result["detail"].startswith("verification failed:")
    assert "exited with code 3" in result["detail"]


def test_undecodable_output_is_replaced_rather_than_crashing_the_step(tmp_path: Path):
    command = _py("import sys; sys.stdout.buffer.write(b'\\xff\\xfe done\\n')")
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is True
    assert result["verified"][0]["tail"].endswith("done")


def test_a_huge_real_output_is_capped_at_three_hundred_characters(tmp_path: Path):
    command = _py("print('z' * 5000)")
    result = verify.run_suite([command], str(tmp_path))
    assert result["verified"][0]["tail"] == "z" * 300 + "…"


def test_ansi_colour_and_over_long_lines_are_flattened_in_the_result(tmp_path: Path):
    # A fake runner, because no real tool can be relied on to emit ANSI on
    # demand -- and a raw ESC byte in a reported field once failed a milestone.
    noisy = "\x1b[31m" + "E" * 400 + "\x1b[0m"

    def runner(argv: list[str], cwd: str) -> CommandResult:
        return CommandResult(exit_code=1, stdout="", stderr=noisy)

    result = verify.run_suite(["fake-linter"], str(tmp_path), runner=runner)
    assert result["verified"][0]["tail"] == "E" * 300 + "…"
    assert "\x1b" not in result["detail"]
    assert len(result["detail"]) <= 601


def test_a_command_that_cannot_be_launched_raises_verify_error(tmp_path: Path):
    # A missing binary is a misconfigured card, not a failed test run, and must
    # not read as an ordinary red suite.
    def runner(argv: list[str], cwd: str) -> CommandResult:
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    with pytest.raises(VerifyError) as excinfo:
        verify.run_suite(
            ["definitely-not-a-real-binary --version"], str(tmp_path), runner=runner
        )
    assert "definitely-not-a-real-binary" in str(excinfo.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: collection error — `ImportError: cannot import name 'VerifyError' from 'agent_manager.steps.verify'`

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/verify.py`, insert this class immediately after the `CommandRunner` type alias and its docstring:

```python
class VerifyError(RuntimeError):
    """A verification command that could not be launched at all.

    Distinct from a red suite on purpose: a missing or unrunnable executable is
    a misconfigured card, and reporting it as `passed: false` would send a
    human hunting for a test failure that never happened.
    """

    def __init__(self, message: str, *, argv: list[str]) -> None:
        self.message = message
        self.argv = list(argv)
        super().__init__(f"{message} (argv={self.argv!r})")
```

Then replace the whole body of `run_suite` (keeping its signature and docstring) with:

```python
    worktree_path = str(worktree)
    result: dict[str, object] = {"passed": False, "verified": [], "detail": ""}
    verified: list[dict[str, object]] = result["verified"]  # type: ignore[assignment]

    for command in commands:
        argv = shlex.split(command) if isinstance(command, str) else [str(p) for p in command]
        try:
            completed = runner(argv, worktree_path)
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
            raise VerifyError(
                f"could not run {_display(command, argv)}: {exc}", argv=argv
            ) from exc

        if completed.exit_code == 0:
            verified.append(
                {
                    "command": command,
                    "ok": True,
                    "tail": plain_text(last_line(completed.stdout)),
                }
            )
            continue

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

    result["passed"] = True
    return result
```

and add these two definitions immediately above `run_suite`:

```python
DETAIL_MAX = 600
"""Cap for `detail`, which names the command as well as the diagnostic."""


def _display(command: object, argv: list[str]) -> str:
    """The command as a human reads it: the original string, else its argv."""
    return command if isinstance(command, str) else " ".join(argv)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add tests/steps/test_verify.py src/agent_manager/steps/verify.py
git commit -m "feat(verify): stop on first red and report a usable diagnostic"
```

---

### Task 5: Input validation and the no-shell guarantee

**Files:**
- Modify: `src/agent_manager/steps/verify.py`
- Test: `tests/steps/test_verify.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: `run_suite` now raises `ValueError` before starting any process for a non-iterable `commands`, an entry that is neither a string nor a sequence of strings, an entry splitting to an empty argv, or a `worktree` that is missing, relative, or not a directory; blank string entries and empty sequence entries are skipped; `worktree` may be a `str` or a `pathlib.Path` and is handed to the runner as a `str`.

- [ ] **Step 1: Write the failing tests**

Append these tests to the end of `tests/steps/test_verify.py`:

```python
def _recorder() -> tuple[list[tuple[list[str], str]], verify.CommandRunner]:
    """A runner that records every call and always reports green."""
    calls: list[tuple[list[str], str]] = []

    def runner(argv: list[str], cwd: str) -> CommandResult:
        calls.append((argv, cwd))
        return CommandResult(exit_code=0, stdout="fine\n", stderr="")

    return calls, runner


def test_blank_and_empty_command_entries_are_skipped(tmp_path: Path):
    # `verify.filter(Boolean)` in the original: a card with a blank slot in its
    # fullSuite is not a broken card.
    calls, runner = _recorder()
    result = verify.run_suite(["", "   ", "echo hi", []], str(tmp_path), runner=runner)
    assert [argv for argv, _ in calls] == [["echo", "hi"]]
    assert result["passed"] is True
    assert [entry["command"] for entry in result["verified"]] == ["echo hi"]


def test_a_command_of_the_wrong_type_raises_before_anything_runs(tmp_path: Path):
    calls, runner = _recorder()
    with pytest.raises(ValueError):
        verify.run_suite(["echo hi", 7], str(tmp_path), runner=runner)
    with pytest.raises(ValueError):
        verify.run_suite([["echo", 7]], str(tmp_path), runner=runner)
    assert calls == []


def test_commands_that_are_not_iterable_raise_value_error(tmp_path: Path):
    with pytest.raises(ValueError):
        verify.run_suite(7, str(tmp_path))


def test_a_missing_relative_or_non_directory_worktree_raises(tmp_path: Path):
    calls, runner = _recorder()
    with pytest.raises(ValueError):
        verify.run_suite(["echo hi"], "relative/path", runner=runner)
    with pytest.raises(ValueError):
        verify.run_suite(["echo hi"], str(tmp_path / "missing"), runner=runner)
    a_file = tmp_path / "file.txt"
    a_file.write_text("not a directory")
    with pytest.raises(ValueError):
        verify.run_suite(["echo hi"], str(a_file), runner=runner)
    assert calls == []


def test_a_command_string_is_split_into_argv_and_never_handed_to_a_shell(
    tmp_path: Path,
):
    calls, runner = _recorder()
    verify.run_suite(
        ["uv run pytest -k 'not slow' > /tmp/out"], str(tmp_path), runner=runner
    )
    # Quoted arguments survive as one element; the redirection is literal argv,
    # not something a shell will act on.
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest", "-k", "not slow", ">", "/tmp/out"]
    ]


def test_shell_metacharacters_are_not_interpreted_by_a_real_process(tmp_path: Path):
    marker = tmp_path / "shell-ran.txt"
    command = (
        f"{_py('print(1)')} ; touch {shlex.quote(str(marker))}"
    )
    result = verify.run_suite([command], str(tmp_path))
    assert result["passed"] is True
    assert not marker.exists()


def test_argv_sequences_and_a_path_worktree_are_accepted(tmp_path: Path):
    calls, runner = _recorder()
    result = verify.run_suite((("echo", "hi"),), tmp_path, runner=runner)
    assert calls == [(["echo", "hi"], str(tmp_path))]
    assert result["verified"][0]["command"] == ("echo", "hi")
    assert result["passed"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: 4 failures among the new tests — `test_blank_and_empty_command_entries_are_skipped` fails because the blank entries are still run (`shlex.split("")` gives `[]`, so `calls` records extra empty argvs); `test_a_command_of_the_wrong_type_raises_before_anything_runs`, `test_commands_that_are_not_iterable_raise_value_error` and `test_a_missing_relative_or_non_directory_worktree_raises` fail with `Failed: DID NOT RAISE <class 'ValueError'>` or a `TypeError` instead

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/verify.py`, add `Iterable` and `Sequence` to the `collections.abc` import and `Path` to the imports, so the import block reads:

```python
import re
import shlex
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
```

Add these three helpers immediately above `run_suite` (after `_display`):

```python
def _argv_for(command: object) -> list[str] | None:
    """`command` as an argv, or `None` if it is a blank entry to skip.

    A string is split with `shlex.split` -- never handed to a shell, so a `>`
    or `;` inside it stays a literal argument. An already-split sequence is
    used as given. Anything else is a malformed card, raised before a single
    process starts so a typo can never read as a passing suite.
    """
    if isinstance(command, str):
        if command.strip() == "":
            return None
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ValueError(
                f"verify.run_suite could not split command {command!r}: {exc}"
            ) from exc
    elif isinstance(command, Sequence) and not isinstance(
        command, (bytes, bytearray)
    ):
        parts = list(command)
        if not parts:
            return None
        if not all(isinstance(part, str) for part in parts):
            raise ValueError(
                f"verify.run_suite needs a command of strings, got {command!r}"
            )
        argv = [str(part) for part in parts]
    else:
        raise ValueError(
            f"verify.run_suite needs a command string or argv, got {command!r}"
        )

    if not argv:
        raise ValueError(
            f"verify.run_suite got a command that splits to nothing: {command!r}"
        )
    return argv


def _plan_commands(commands: object) -> list[tuple[object, list[str]]]:
    """Every runnable command paired with its argv, validated up front."""
    if isinstance(commands, (str, bytes, bytearray)) or not isinstance(
        commands, Iterable
    ):
        raise ValueError(
            f"verify.run_suite needs a sequence of commands, got {commands!r}"
        )
    planned: list[tuple[object, list[str]]] = []
    for command in commands:
        argv = _argv_for(command)
        if argv is not None:
            planned.append((command, argv))
    return planned


def _required_worktree(worktree: object) -> str:
    """An existing absolute directory as a string, or `ValueError` up front.

    The same pre-flight shape `worktree.ensure` uses: a bad path must fail
    loudly here, not as a confusing failure from every command in the suite.
    """
    if not isinstance(worktree, (str, Path)):
        raise ValueError(
            f"verify.run_suite needs an absolute worktree path, got {worktree!r}"
        )
    text = str(worktree).strip()
    if text == "" or not Path(text).is_absolute() or not Path(text).is_dir():
        raise ValueError(
            f"verify.run_suite needs an existing absolute worktree directory, "
            f"got {worktree!r}"
        )
    return text
```

Then replace the head of `run_suite`'s body

```python
    worktree_path = str(worktree)
    result: dict[str, object] = {"passed": False, "verified": [], "detail": ""}
    verified: list[dict[str, object]] = result["verified"]  # type: ignore[assignment]

    for command in commands:
        argv = shlex.split(command) if isinstance(command, str) else [str(p) for p in command]
        try:
```

with

```python
    planned = _plan_commands(commands)
    worktree_path = _required_worktree(worktree)
    result: dict[str, object] = {"passed": False, "verified": [], "detail": ""}
    verified: list[dict[str, object]] = result["verified"]  # type: ignore[assignment]

    for command, argv in planned:
        try:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: 30 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: all tests pass, including the pre-existing `tests/steps/test_worktree.py`, `tests/steps/test_plan_check.py` and `tests/steps/test_reducers.py`

- [ ] **Step 6: Commit**

```bash
git add tests/steps/test_verify.py src/agent_manager/steps/verify.py
git commit -m "feat(verify): validate commands and worktree before anything runs"
```
