# Persist verify output under the run directory (e1b1e7d5)

Subtask of story 9f445791 "Verify failures name the real failure". It follows bc0b99e0, which made
verify's red row and `detail` always name the exit code. This card stops verify from throwing away the
streams it already captures.

## Why

`verify.run_suite` (`src/agent_manager/steps/verify.py:278-343`) captures each command's full stdout and
stderr in a `CommandResult` (`verify.py:101-112`). Then it keeps one line of them: a `tail` per command
and a `detail` capped at `DETAIL_MAX` (`verify.py:312-338`). The rest is gone when the function returns.
The engine persists nothing for a deterministic phase. `walk.run_one_step` (`runtime/walk.py:424-483`)
records only `PhaseRun` rows (`walk.py:490-511`) and never an `Attempt` or an attempt directory. As a
result, `am logs <run> <card> --phase verify` always raises `UnknownAttemptError` (`cli.py:362-366`).
Milestone 17 needed a PATH shim to see a single line of output that had already been captured and
discarded.

## Inherited constraints

- Run artifacts live under `runs/<run-id>/<card>/<phase>.<attempt>/`, **outside the worktree**. Inside
  it they would trip the clean-tree check or end up in a commit (design §6,
  `2026-09-23-agent-manager-design.md:268-273`; `paths.py:1-7`). Agent phases capture stdout to
  `stdout.log` there (design `:273`, `:368`).
- `verify.run_suite` stays read-only with respect to the repository: it never commits, pushes, or writes
  to the worktree (design §9, `:383`). Writing logs under the data directory does not break this.
- A deterministic phase is `run(ctx) -> dict`, with no network and no model (design §6, `:260-262`). It
  runs through `walk.run_one_step`, which is called by `step_phase` (pygents addendum §5,
  `2026-09-25-pygents-engine-design.md:235-236`).
- `§10`'s `logs <run-id> <card> [--phase …] [--attempt …]` (design `:403`) prints JSON by default and
  supports `--pretty`, using the `brd` envelope (`CLAUDE.md` Conventions).
- `logs` is read-only. It must not create a run or attempt directory (`cli.py:412-415`, `cli.py:1459-1461`).
- The result stays a plain dict with exactly the keys `passed`, `verified` and `detail`. Its values do not
  change (`CLAUDE.md` Conventions; bc0b99e0 spec "Inherited constraints").
- Commands still run as argv lists, never through a shell (design §5 `:254-256`; `verify.py:14-16`).
- Test tiers are chosen by what a test spawns (design §14, `:507-539`; `CLAUDE.md` "Test tiers").

## Decisions

1. **The engine supplies the directory, and only on request.** A deterministic step that declares a
   parameter named `log_dir` receives `paths.attempt_dir(store.run_id, subtask.card_id, phase.name, n)`,
   where `n = paths.highest_attempt(store.run_id, subtask.card_id, phase.name) + 1`. This is the same
   numbering agent phases use (`dispatch.py:66`). A step that does not declare `log_dir` gets nothing, and
   no directory is created for it. `verify.run_suite` is the only step that declares it, which keeps every
   other deterministic step out of scope. If the store has no run id (`getattr(store, "run_id", None)` is
   falsy, as in `runtime/checkpoint.py:87-89`), `log_dir` is not supplied and the step's default applies.
   Whatever the binding table or the document's `args` say, the engine's value is the one passed.
2. **No `Attempt` row for a deterministic phase.** `models.Attempt.dispatch` is a required `Dispatch`
   whose harness, model and role must be non-empty (`models.py:58-67, 82`). The `attempts.dispatch` SQL
   column is `NOT NULL` (`store.py:80-96`). Adding a deterministic attempt would mean inventing dispatch
   values or doing a non-additive migration. It would also pull these attempts into `orphan_attempts` and
   resume. Instead, the files on disk at the derived path are the record, and `logs` finds them there.
3. **Clean verifies keep their logs too.** This is the cheap choice: one write path, no deletion branch,
   and a green run that later looks suspicious can still be inspected.
4. **The no-flag `am logs <run> <card>` default does not change.** It still picks the last phase that has
   recorded `Attempt`s, which is always an agent phase. Disk-backed deterministic logs are reachable only
   through `--phase`. This keeps `select_attempt` pure over the tree (`cli.py:333-336`).

## Behavior

### `verify.run_suite(..., *, log_dir: Path | None = None)`

- With `log_dir=None` (the default) the behavior is exactly as today, and nothing is written anywhere.
- With `log_dir` set to an existing directory:
  - Before the first command runs, `log_dir/stdout.log` and `log_dir/stderr.log` are created (or
    truncated) as empty files. A suite that dies on its first command still leaves both files.
  - After each command that ran (exit code returned by the runner), one section is appended to **each**
    file:
    - a header line `==> <display> (<exit label>)\n`, where `<display>` is the same `_display(command,
      argv)` string `detail` uses and `<exit label>` is `exit_label(exit_code)` (`exit 0`, `exit 1`,
      `exit -9 (signal SIGKILL)`);
    - then that command's stdout (in `stdout.log`) or stderr (in `stderr.log`), verbatim: no ANSI
      stripping, no truncation, no `plain_text`;
    - then a single `\n` if the stream is non-empty and does not already end in one, so the next header
      always starts its own line.
  - Sections are appended in run order and flushed before the next command starts. If the process dies
    mid-suite, the commands that finished are still on disk.
  - Commands that never ran because an earlier one went red (`verify.py:341`) get no section.
  - An unlaunchable command (`VerifyError`, `verify.py:300-304`) gets no section. The error is still
    raised unchanged. The sections of the commands that ran before it stay on disk.
  - Extra commands from `explore` (typecheck, lint) get sections exactly like `--verify` commands.
  - An `OSError` while writing a log propagates. `run_one_step`'s catch-all (`walk.py:473-481`) records
    the phase `failed` with `OSError: …` as its detail. A green suite whose logs could not be written is
    reported, not hidden.
- The returned dict is identical with and without `log_dir`.

### Engine (`walk.run_one_step`)

- Decision 1, applied at binding time. Successive runs of the `verify` phase for one card in one run land
  in `verify.1`, `verify.2`, …. That includes a re-run after resume, because `highest_attempt` scans the
  disk.
- The integrate workflow's `verify` step (`workflow/integrate.py:30`) goes through the same
  `run_one_step`, so it gets the same treatment under its own card id. No extra work is needed.

### `am logs <run-id> <card> --phase <deterministic phase> [--attempt N]`

- Applies when the named phase exists and its `kind` is `"deterministic"`.
- The attempt numbers are the contiguous `<phase>.1`, `<phase>.2`, … directories that exist under
  `<data dir>/runs/<run-id>/<card>/`. They are found by a read-only scan in `paths.py` that creates
  nothing: not the run directory, the card directory, or any attempt directory.
- With no `--attempt`, the highest number is chosen. `--attempt N` chooses `N`.
- No directories → `UnknownAttemptError`, `"phase 'verify' of card '<card>' has no recorded attempt yet"`
  (today's wording, `cli.py:363-366`), exit 3, error envelope.
- `--attempt N` that is not among them → `UnknownAttemptError`,
  `"phase 'verify' of card '<card>' has no attempt N; recorded attempts: 1, 2"` (or `none`), using
  today's wording (`cli.py:372-376`).
- On success the payload has today's shape with one addition, `artifacts.stderr`:

  ```json
  {"run_id": "...", "story_id": "...", "card": "...", "phase": "verify", "attempt": 2,
   "status": null, "exit_code": null,
   "artifacts": {"prompt": {"path": null, "present": false, "text": null},
                 "result": {"path": null, "present": false, "text": null},
                 "stdout": {"path": ".../verify.2/stdout.log", "present": true, "text": "..."},
                 "stderr": {"path": ".../verify.2/stderr.log", "present": true, "text": "..."}}}
  ```

  `status` and `exit_code` are `null` because no `Attempt` row exists to carry them. The per-command exit
  labels are in the log headers. A directory whose files are missing reads as `present: false` through
  the existing `read_artifact` (`cli.py:379-403`).
- Agent-phase payloads gain `artifacts.stderr`, which is always `{"path": null, "present": false, "text":
  null}`. The launcher merges stderr into stdout (`harness/launcher.py`). With this key, both payload
  kinds have one shape. The change is additive, and no other key changes.
- `--phase` on a deterministic phase other than `verify` uses the same disk lookup. Since no other step
  writes logs, it gets the same `UnknownAttemptError` it gets today.

## Out of scope

- Logs for any other deterministic step (`worktree`, `docs_commit`, `plan_check`, `rollup`, …), as the
  card states. The `log_dir` mechanism is opt-in, and no other step opts in.
- Recording deterministic phases as `Attempt` rows. Also any change to `status`, `watch`, `orphan_attempts`
  or resume.
- Making the no-flag `am logs` default consider deterministic phases (Decision 4).
- Retention or cleanup of `runs/` content.
- Any change to `tail`, `detail`, `passed` or the gate. Those belong to bc0b99e0 (done) and the gate.

## Tests

| # | Test | File | Tier and why |
|---|------|------|--------------|
| 1 | A red command's full stdout and stderr (multi-line, longer than `DETAIL_MAX`, containing a green-looking last line) appear verbatim in `log_dir/stdout.log` / `stderr.log` under a header `==> <display> (exit 1)`. | `tests/steps/test_verify.py` | `git` (module default): real `sys.executable -c` subprocess. |
| 2 | A clean suite of two commands keeps both logs. Each file has two sections in order, headed `(exit 0)`. | `tests/steps/test_verify.py` | `git`: real subprocesses. |
| 3 | After a red first command, the second command has no section, and the files hold exactly one header each. | `tests/steps/test_verify.py` | `git`: real subprocesses. |
| 4 | `log_dir=None` writes nothing (the `tmp_path` used as cwd and the data dir gain no files), and the result dict equals the `log_dir` run's dict. | `tests/steps/test_verify.py` | `git`: real subprocesses. |
| 5 | Stream without a trailing newline: the next header starts its own line. Signal exit renders `exit -9 (signal SIGKILL)` in the header. | `tests/steps/test_verify.py` | Fake `runner`, no subprocess, so unit by content. Auto-marked `git` by the module's directory default, like the module's other fake-runner tests. |
| 6 | A passing command, then an unlaunchable one: `VerifyError` is raised, and both files exist holding the passing command's section only. Also: an unlaunchable first command leaves both files present and empty. | `tests/steps/test_verify.py` | Fake runner raising `FileNotFoundError` for the second argv, so unit by content. Auto-marked `git`, as row 5. |
| 7 | `explore` typecheck/lint commands get sections after the `--verify` commands. | `tests/steps/test_verify.py` | `git`: real subprocesses. |
| 8 | `run_one_step` passes a step that declares `log_dir` the path `paths.attempt_dir(RUN_ID, card, phase, 1)`, and that directory exists. A second call gets `.2`. A step that does not declare it gets no directory: `runs/<RUN_ID>/<card>/<phase>.1` does not exist. A document `args: {"log_dir": ...}` is overridden by the engine's value. | `tests/runtime/test_walk.py` | unit: fake step functions, real temp `Store` (file I/O only, no subprocess), matching the module's existing `run_one_step` tests. |
| 9 | **Card test:** a red `verify.run_suite` run through `run_one_step` (phase `verify`, real `Store`) leaves `stdout.log`/`stderr.log` at `paths.run_dir(RUN_ID)/<card>/verify.1/`, holding the failing command's output. The phase is recorded `failed`. | `tests/runtime/test_walk.py` | `git` (explicit mark): spawns a real `sys.executable` subprocess. |
| 10 | **Card test:** with a projection holding a `verify` phase (`kind="deterministic"`) and files written at `verify.1/` and `verify.2/`, `am logs RUN card --phase verify` returns attempt 2 with `stdout`/`stderr` text, `prompt`/`result` absent, and `status`/`exit_code` `null`. `--attempt 1` returns attempt 1. | `tests/test_cli.py` | unit: hand-built projection and files, `CliRunner`, no subprocess. Extends `_record_for_logs` (`test_cli.py:4280-4288`). |
| 11 | `--phase verify` with no directories on disk gives the error envelope `UnknownAttemptError` "has no recorded attempt yet", exit 3, and `runs/<RUN>/` was not created. `--attempt 7` with `verify.1`, `verify.2` on disk gives `UnknownAttemptError` "recorded attempts: 1, 2". | `tests/test_cli.py` | unit: as row 10. |
| 12 | The existing no-flag test (`test_cli.py:4292`) still selects `implement`. Agent-phase payloads now carry `artifacts.stderr` absent. | `tests/test_cli.py` | unit: as row 10. |
| 13 | The read-only `paths` scan returns `[]` for a missing run and stops at the first gap (`.1`, `.3` → `[1]`), creating nothing. | `tests/test_paths.py` | unit: files only. |

No `e2e_fake` test is added. Row 9 exercises the production `run_one_step` wiring with the real step.
`compile.py:202-203` calls it without any verify-specific path.

## Hand-off notes for the planner

- Follow the `writing-plans` format: header, Global Constraints copied from "Inherited constraints",
  Review Focus, TDD tasks.
- Natural task split: (a) `verify.run_suite` log writing (rows 1-7); (b) the `paths` read-only scan
  (row 13); (c) the `run_one_step` `log_dir` binding (rows 8-9); (d) `logs` deterministic lookup and the
  `stderr` artifact (rows 10-12).
- Review Focus candidates: huge outputs (verbatim, no cap); non-UTF-8 output (already replaced by
  `run_command`'s `errors="replace"`, `verify.py:139-148`); a re-run after resume landing in `verify.2`
  rather than overwriting `verify.1`; a `logs` call for a run whose `runs/` directory is absent; the
  concurrent-lane data-dir guard in `tests/conftest.py`, which already excludes `runs/` (c9e1730).

---

# Persist Verify Output Under the Run Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `verify.run_suite` writes each command's full stdout and stderr to `stdout.log` / `stderr.log` in a per-attempt directory under the run tree, the engine hands it that directory, and `am logs <run> <card> --phase verify` reads it back.

**Architecture:** `run_suite` gains a keyword-only `log_dir: Path | None = None` and appends one headed section per command that ran. `walk.run_one_step` supplies `log_dir` only to a step that declares that parameter, numbering `<phase>.N` by the same on-disk scan agent phases use. `paths` gains a read-only scan (`recorded_attempts`) and a non-creating path helper (`attempt_path`), and `cli.logs_for` uses them for a phase whose `kind` is `"deterministic"`. No `Attempt` row is ever recorded for a deterministic phase.

**Tech Stack:** Python 3, Typer CLI, Pydantic models, pytest, `uv`.

**Spec:** `docs/superpowers/specs/persist-verify-output-e1b1e7d5.md` (reproduced in full above this plan).

## Global Constraints

- Run artifacts live under `runs/<run-id>/<card>/<phase>.<attempt>/`, **outside the worktree** (design §6, `2026-09-23-agent-manager-design.md:268-273`; `paths.py:1-7`).
- `verify.run_suite` stays read-only with respect to the repository: it never commits, pushes, or writes to the worktree (design §9, `:383`). Writing logs under the data directory does not break this.
- A deterministic phase is `run(ctx) -> dict`, with no network and no model (design §6, `:260-262`). It runs through `walk.run_one_step`.
- `logs` prints JSON by default and supports `--pretty`, using the `brd` envelope (`{"ok": true, "data": ...}`).
- `logs` is read-only. It must not create a run or attempt directory (`cli.py:412-415`, `cli.py:1459-1461`).
- The result stays a plain dict with exactly the keys `passed`, `verified` and `detail`. Its values do not change.
- Commands still run as argv lists, never through a shell (design §5 `:254-256`; `verify.py:14-16`).
- Test tiers are chosen by what a test spawns (design §14; `CLAUDE.md` "Test tiers"). `tests/steps/` is auto-marked `git`; `tests/runtime/`, `tests/test_cli.py`, `tests/test_paths.py` are unit unless marked.
- Header line format, exactly: `==> <display> (<exit label>)\n`, where `<display>` is `verify._display(command, argv)` and `<exit label>` is `verify.exit_label(exit_code)`.
- Logs are verbatim: no ANSI stripping, no truncation, no `plain_text`.
- Error wording is today's: `"phase 'verify' of card '<card>' has no recorded attempt yet"` and `"phase 'verify' of card '<card>' has no attempt N; recorded attempts: 1, 2"` (or `none`).
- Verification command: `uv run pytest` (no separate lint or typecheck).

## Review Focus

1. **A huge output** (hundreds of KB on one stream): the log holds it byte for byte, with no 300/600-char cap leaking in from `plain_text`/`DETAIL_MAX`. Pinned by `test_a_huge_output_is_logged_in_full` (Task 1).
2. **Non-UTF-8 bytes on a stream:** `run_command`'s `errors="replace"` already turns them into U+FFFD; the log write must not raise on it. Pinned by `test_undecodable_output_is_logged_with_replacement_characters` (Task 1).
3. **A re-run of `verify` after resume** when `verify.1/` already exists from a previous process: the new run lands in `verify.2/` and `verify.1/`'s files are untouched. Pinned by `test_run_one_step_never_overwrites_an_earlier_attempt_directory` (Task 3).
4. **`am logs --phase verify` for a run whose `runs/` directory is absent** (data dir wiped, projection kept): an `UnknownAttemptError` envelope, and `runs/` is not recreated. Pinned by `test_logs_for_a_deterministic_phase_with_no_runs_directory_creates_nothing` (Task 4).
5. **A `log_dir` that cannot be written** (missing directory): the `OSError` propagates before any command runs, so a green suite is never reported with its logs silently lost. Pinned by `test_an_unwritable_log_dir_raises_before_any_command_runs` (Task 1).

---

### Task 1: `verify.run_suite` writes `stdout.log` / `stderr.log` when given `log_dir`

**Files:**
- Modify: `src/agent_manager/steps/verify.py:157-163` (add constants and helpers after `_display`), `src/agent_manager/steps/verify.py:278-343` (`run_suite`)
- Test: `tests/steps/test_verify.py` (modify `test_run_suite_takes_explore_by_name_before_the_keyword_only_runner` at lines 546-553; append new tests at end of file)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `verify.STDOUT_LOG = "stdout.log"`, `verify.STDERR_LOG = "stderr.log"` (module constants, `str`).
  - `verify.run_suite(commands: object, worktree: object, explore: object = None, *, runner: CommandRunner = run_command, log_dir: Path | None = None) -> dict[str, object]`. The parameter name `log_dir` is what Task 3's engine looks for.

- [ ] **Step 1: Update the signature test and write the failing log tests**

In `tests/steps/test_verify.py`, replace the body of `test_run_suite_takes_explore_by_name_before_the_keyword_only_runner` (lines 546-553) with:

```python
def test_run_suite_takes_explore_by_name_before_the_keyword_only_runner():
    # `walk.bind_arguments` binds strictly by parameter name, so the name
    # `explore` is what wires the Explore phase's result in -- no YAML edit.
    # `log_dir` is the name `walk.run_one_step` looks for to hand over the
    # attempt directory (spec e1b1e7d5, Decision 1).
    parameters = inspect.signature(verify.run_suite).parameters
    assert list(parameters) == ["commands", "worktree", "explore", "runner", "log_dir"]
    assert parameters["explore"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["explore"].default is None
    assert parameters["runner"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["log_dir"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["log_dir"].default is None
```

Then append to the end of `tests/steps/test_verify.py`:

```python
# ── persisted output (spec e1b1e7d5) ─────────────────────────────────────────
#
# With `log_dir`, every command that ran gets one headed section in each of
# `stdout.log` and `stderr.log`, verbatim. Real processes where a real process
# can produce the stream; a fake runner for the signal code and the
# unlaunchable command, as the rest of this module does.


def _logs(log_dir: Path) -> tuple[str, str]:
    return (
        (log_dir / "stdout.log").read_text(encoding="utf-8"),
        (log_dir / "stderr.log").read_text(encoding="utf-8"),
    )


def _tree(root: Path) -> list[str]:
    """Every path under `root`, relative and sorted; `[]` when absent."""
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def test_a_red_commands_full_streams_are_logged_verbatim_under_its_header(
    tmp_path: Path,
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    command = _py(
        "import sys; "
        "sys.stdout.write('A' * 700 + '\\nline two\\n7 passed\\n'); "
        "sys.stderr.write('E first\\nE second\\n'); "
        "raise SystemExit(1)"
    )

    result = verify.run_suite([command], str(worktree), log_dir=log_dir)

    assert result["passed"] is False
    stdout, stderr = _logs(log_dir)
    assert stdout == f"==> {command} (exit 1)\n" + "A" * 700 + "\nline two\n7 passed\n"
    assert stderr == f"==> {command} (exit 1)\nE first\nE second\n"


def test_a_clean_suite_keeps_both_logs_with_one_section_per_command_in_order(
    tmp_path: Path,
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Stale content from an earlier writer is truncated, never appended to.
    (log_dir / "stdout.log").write_text("stale\n", encoding="utf-8")
    (log_dir / "stderr.log").write_text("stale\n", encoding="utf-8")
    first = _py("import sys; print('one'); sys.stderr.write('warn one\\n')")
    second = _py("print('two')")

    result = verify.run_suite([first, second], str(worktree), log_dir=log_dir)

    assert result["passed"] is True
    stdout, stderr = _logs(log_dir)
    assert stdout == f"==> {first} (exit 0)\none\n==> {second} (exit 0)\ntwo\n"
    assert stderr == f"==> {first} (exit 0)\nwarn one\n==> {second} (exit 0)\n"


def test_a_command_after_a_red_one_never_runs_and_gets_no_section(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    red = _py("print('red'); raise SystemExit(2)")
    never = _py("print('never')")

    verify.run_suite([red, never], str(worktree), log_dir=log_dir)

    stdout, stderr = _logs(log_dir)
    assert stdout == f"==> {red} (exit 2)\nred\n"
    assert stderr == f"==> {red} (exit 2)\n"
    assert stdout.count("==> ") == 1
    assert stderr.count("==> ") == 1


def test_no_log_dir_writes_nothing_and_returns_the_same_result(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    data_home = Path(os.environ["XDG_DATA_HOME"])
    commands = [_py("print('ok')"), _py("print('bad'); raise SystemExit(1)")]
    worktree_before = _tree(worktree)
    data_before = _tree(data_home)

    without = verify.run_suite(commands, str(worktree))

    assert _tree(worktree) == worktree_before
    assert _tree(data_home) == data_before
    with_logs = verify.run_suite(commands, str(worktree), log_dir=log_dir)
    assert with_logs == without
    assert set(with_logs) == {"passed", "verified", "detail"}


def test_a_stream_without_a_trailing_newline_still_ends_its_section_on_a_line_of_its_own(
    tmp_path: Path,
):
    outcomes = {
        "first": CommandResult(exit_code=0, stdout="no newline", stderr=""),
        "second": CommandResult(exit_code=-9, stdout="", stderr="killed"),
    }

    def runner(argv: list[str], cwd: str) -> CommandResult:
        return outcomes[argv[0]]

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    verify.run_suite(["first", "second"], str(tmp_path), runner=runner, log_dir=log_dir)

    stdout, stderr = _logs(log_dir)
    assert stdout == (
        "==> first (exit 0)\nno newline\n==> second (exit -9 (signal SIGKILL))\n"
    )
    assert stderr == (
        "==> first (exit 0)\n==> second (exit -9 (signal SIGKILL))\nkilled\n"
    )


def test_an_unlaunchable_command_keeps_the_sections_of_the_commands_before_it(
    tmp_path: Path,
):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        if argv[0] == "missing-cmd":
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        return CommandResult(exit_code=0, stdout="fine\n", stderr="warn\n")

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with pytest.raises(VerifyError) as excinfo:
        verify.run_suite(
            ["ok-cmd", "missing-cmd"], str(tmp_path), runner=runner, log_dir=log_dir
        )

    assert "missing-cmd" in str(excinfo.value)
    stdout, stderr = _logs(log_dir)
    assert stdout == "==> ok-cmd (exit 0)\nfine\n"
    assert stderr == "==> ok-cmd (exit 0)\nwarn\n"


def test_an_unlaunchable_first_command_leaves_both_logs_present_and_empty(
    tmp_path: Path,
):
    def runner(argv: list[str], cwd: str) -> CommandResult:
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with pytest.raises(VerifyError):
        verify.run_suite(["missing-cmd"], str(tmp_path), runner=runner, log_dir=log_dir)

    assert _logs(log_dir) == ("", "")


def test_explore_typecheck_and_lint_get_sections_after_the_suite(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    suite = _py("print('suite')")
    typecheck = _py("print('types')")
    lint = _py("print('lint')")
    explore = {"verification": {"typecheck": typecheck, "lint": [lint]}}

    verify.run_suite([suite], str(worktree), explore, log_dir=log_dir)

    stdout, stderr = _logs(log_dir)
    assert stdout == (
        f"==> {suite} (exit 0)\nsuite\n"
        f"==> {typecheck} (exit 0)\ntypes\n"
        f"==> {lint} (exit 0)\nlint\n"
    )
    assert stderr == (
        f"==> {suite} (exit 0)\n==> {typecheck} (exit 0)\n==> {lint} (exit 0)\n"
    )


def test_a_huge_output_is_logged_in_full(tmp_path: Path):
    # Review Focus 1: the 300/600-character caps belong to `tail`/`detail`
    # only; the log is the place the whole stream survives.
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    command = _py("import sys; sys.stdout.write('y' * 200000)")

    result = verify.run_suite([command], str(worktree), log_dir=log_dir)

    assert result["passed"] is True
    stdout, _ = _logs(log_dir)
    assert stdout == f"==> {command} (exit 0)\n" + "y" * 200000 + "\n"


def test_undecodable_output_is_logged_with_replacement_characters(tmp_path: Path):
    # Review Focus 2: `run_command` already replaced the bad byte; writing the
    # replacement character back out must not raise.
    worktree = tmp_path / "wt"
    worktree.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    command = _py("import sys; sys.stdout.buffer.write(b'ok \\xff done\\n')")

    verify.run_suite([command], str(worktree), log_dir=log_dir)

    stdout, _ = _logs(log_dir)
    assert stdout == f"==> {command} (exit 0)\nok � done\n"


def test_an_unwritable_log_dir_raises_before_any_command_runs(tmp_path: Path):
    # Review Focus 5: a log that cannot be written is an error, never a green
    # suite with its evidence silently missing.
    calls, runner = _recorder()

    with pytest.raises(OSError):
        verify.run_suite(
            ["uv run pytest"], str(tmp_path), runner=runner, log_dir=tmp_path / "absent"
        )

    assert calls == []
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -k "log or section or keyword_only_runner or huge_output or undecodable_output_is_logged" -v`
Expected: FAIL — every new test with `TypeError: run_suite() got an unexpected keyword argument 'log_dir'`; the signature test with an `AssertionError` on the parameter list. (`test_no_log_dir_writes_nothing...` fails at the second call for the same `TypeError`.)

- [ ] **Step 3: Implement the log writing**

In `src/agent_manager/steps/verify.py`, directly after `_display` (after line 163), add:

```python
STDOUT_LOG = "stdout.log"
STDERR_LOG = "stderr.log"
"""The two files `run_suite` writes in `log_dir`; `cli.logs` reads them back."""


def _start_logs(log_dir: Path) -> None:
    """Create (or truncate) both logs, so a suite that dies early still leaves them."""
    for name in (STDOUT_LOG, STDERR_LOG):
        (log_dir / name).write_text("", encoding="utf-8")


def _append_section(path: Path, header: str, text: str) -> None:
    """One command's section: its header line, then `text` verbatim.

    A trailing newline is added only when `text` lacks one, so the next header
    always starts its own line. The file is closed -- flushed -- before the
    next command starts, so a process that dies mid-suite keeps what finished.
    `errors="replace"` matches `run_command`'s read side: a stream can never
    make the log write itself raise anything but an `OSError`.
    """
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(header)
        handle.write(text)
        if text and not text.endswith("\n"):
            handle.write("\n")


def _log_command(log_dir: Path, shown: str, completed: CommandResult) -> None:
    """Append one section per stream for a command that ran."""
    header = f"==> {shown} ({exit_label(completed.exit_code)})\n"
    _append_section(log_dir / STDOUT_LOG, header, completed.stdout)
    _append_section(log_dir / STDERR_LOG, header, completed.stderr)
```

Then replace `run_suite` (lines 278-343) with:

```python
def run_suite(
    commands: object,
    worktree: object,
    explore: object = None,
    *,
    runner: CommandRunner = run_command,
    log_dir: Path | None = None,
) -> dict[str, object]:
    """Run each verification command in `worktree` and report what happened.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    `runner` defaults to real execution and exists to be swapped in tests, the
    same callable-injection seam `worktree.py` uses for git.

    `explore` is the Explore phase's result, bound by parameter name by
    `walk.bind_arguments` (no workflow edit needed; the integrate workflow
    has no such phase and gets `None`). Its `verification.typecheck` and each
    `verification.lint` command run after `commands` and are reported, and fail
    the suite, exactly like `--verify` commands (pygents design G9 item 4).
    Every command, extra or not, is planned before the first one runs.

    `log_dir` is the attempt directory `walk.run_one_step` hands a step that
    declares it (spec e1b1e7d5). When set, `stdout.log` and `stderr.log` are
    created there before the first command, and every command that ran
    appends a `==> <command> (<exit label>)` section with its full stream,
    verbatim. It lives under the data directory, never the worktree, so this
    step stays read-only with respect to the repository. An `OSError` writing
    it propagates. The returned dict is the same with or without it.
    """
    planned = [*_plan_commands(commands), *_plan_explore_commands(explore)]
    worktree_path = _required_worktree(worktree)
    if log_dir is not None:
        _start_logs(Path(log_dir))
    result: dict[str, object] = {"passed": False, "verified": [], "detail": ""}
    verified: list[dict[str, object]] = result["verified"]  # type: ignore[assignment]

    for command, argv in planned:
        try:
            completed = runner(argv, worktree_path)
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
            raise VerifyError(
                f"could not run {_display(command, argv)}: {exc}", argv=argv
            ) from exc

        shown = _display(command, argv)
        if log_dir is not None:
            _log_command(Path(log_dir), shown, completed)

        if completed.exit_code == 0:
            verified.append(
                {
                    "command": command,
                    "ok": True,
                    "tail": plain_text(last_line(completed.stdout)),
                }
            )
            continue

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

    result["passed"] = True
    return result
```

Note: `_log_command` sits outside the `try`, so an `OSError` from a log write (including `FileNotFoundError`) is never turned into a `VerifyError`.

- [ ] **Step 4: Run the whole verify module to verify it passes**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: PASS (all old tests unchanged, all new ones green).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/verify.py tests/steps/test_verify.py
git commit -m "feat: verify.run_suite persists full command output to log_dir (e1b1e7d5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A read-only attempt scan in `paths`

**Files:**
- Modify: `src/agent_manager/paths.py` (add two functions after `highest_attempt`, line 72)
- Test: `tests/test_paths.py` (append)

**Interfaces:**
- Consumes: `paths.data_dir()`.
- Produces:
  - `paths.attempt_path(run_id: str, card: str, phase: str, attempt: int) -> Path` — the same location `attempt_dir` returns, creating nothing.
  - `paths.recorded_attempts(run_id: str, card: str, phase: str) -> list[int]` — `[1, 2, …, k]` for the contiguous `<phase>.N` directories that exist; `[]` when none; creates nothing under `runs/`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paths.py`:

```python
def test_attempt_path_names_the_attempt_dir_location_without_creating_it(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    located = paths.attempt_path("run-abc", "abc123", "verify", 2)

    assert located == tmp_path / "agent-manager" / "runs" / "run-abc" / "abc123" / "verify.2"
    assert not (tmp_path / "agent-manager" / "runs").exists()
    assert paths.attempt_dir("run-abc", "abc123", "verify", 2) == located


def test_recorded_attempts_is_empty_and_creates_nothing_for_a_missing_run(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert paths.recorded_attempts("run-abc", "abc123", "verify") == []
    assert not (tmp_path / "agent-manager" / "runs").exists()


def test_recorded_attempts_lists_the_contiguous_attempts_and_creates_nothing(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for n in (1, 2, 3):
        paths.attempt_dir("run-abc", "abc123", "verify", n)
    paths.attempt_dir("run-abc", "abc123", "implement", 1)
    card_dir = tmp_path / "agent-manager" / "runs" / "run-abc" / "abc123"
    before = sorted(p.name for p in card_dir.iterdir())

    assert paths.recorded_attempts("run-abc", "abc123", "verify") == [1, 2, 3]
    assert paths.recorded_attempts("run-abc", "abc123", "review") == []
    assert sorted(p.name for p in card_dir.iterdir()) == before


def test_recorded_attempts_stops_at_the_first_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    paths.attempt_dir("run-abc", "abc123", "verify", 1)
    paths.attempt_dir("run-abc", "abc123", "verify", 3)

    assert paths.recorded_attempts("run-abc", "abc123", "verify") == [1]


def test_recorded_attempts_ignores_a_plain_file_in_an_attempts_place(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    card_dir = paths.run_dir("run-abc") / "abc123"
    card_dir.mkdir()
    (card_dir / "verify.1").write_text("not a directory\n")

    assert paths.recorded_attempts("run-abc", "abc123", "verify") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -k "attempt_path or recorded_attempts" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.paths' has no attribute 'attempt_path'` / `'recorded_attempts'`.

- [ ] **Step 3: Implement**

In `src/agent_manager/paths.py`, after `highest_attempt` (line 72), add:

```python
def attempt_path(run_id: str, card: str, phase: str, attempt: int) -> Path:
    """Where `attempt_dir` puts one attempt, without creating anything.

    For readers (`am logs`): `attempt_dir` and `run_dir` mkdir as a side
    effect, and a read-only command must not mint a run directory.
    """
    return data_dir() / "runs" / run_id / card / f"{phase}.{attempt}"


def recorded_attempts(run_id: str, card: str, phase: str) -> list[int]:
    """The attempt numbers `phase` of `card` has a directory for: `[1, ..., k]`.

    The same contiguous scan as `highest_attempt` -- it stops at the first
    absent `{phase}.N` -- but read-only: it creates neither the `runs`
    directory, the run's directory, nor the card's. A plain file where an
    attempt directory would be is not an attempt.
    """
    numbers: list[int] = []
    while attempt_path(run_id, card, phase, len(numbers) + 1).is_dir():
        numbers.append(len(numbers) + 1)
    return numbers
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/paths.py tests/test_paths.py
git commit -m "feat: paths.recorded_attempts, a read-only attempt scan (e1b1e7d5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `walk.run_one_step` hands a `log_dir`-declaring step its attempt directory

**Files:**
- Modify: `src/agent_manager/runtime/walk.py:31` (import), `src/agent_manager/runtime/walk.py:424-483` (`run_one_step`), plus a new helper `_with_log_dir` placed directly above `run_one_step`
- Test: `tests/runtime/test_walk.py` (imports at lines 11-22; append tests at end of file)

**Interfaces:**
- Consumes: `paths.highest_attempt(run_id, card, phase) -> int`, `paths.attempt_dir(run_id, card, phase, attempt) -> Path` (both existing); `verify.run_suite(..., *, log_dir: Path | None = None)` from Task 1; `verify.STDOUT_LOG`, `verify.STDERR_LOG` from Task 1.
- Produces: `walk._with_log_dir(fn: Callable[..., Any], kwargs: dict[str, Any], store: Any, card: str, phase_name: str) -> dict[str, Any]` (private). Behavioural contract later tasks rely on: verify logs land in `paths.run_dir(run_id)/<card>/verify.N/`.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_walk.py`, add `import shlex` after `import functools` (keeping the stdlib imports alphabetical: `functools`, `shlex`, `subprocess`, `sys`), and change line 19 to:

```python
from agent_manager import models, paths, store as store_module
from agent_manager.steps import reducers, verify
```

Then append to the end of the file:

```python
# ── the engine-supplied log directory (spec e1b1e7d5, Decision 1) ────────────
#
# Unit tier: fake step functions and a real temp Store (file I/O only), like
# the run_one_step tests above. The one test that runs the real verify step
# spawns a real interpreter and is marked `git` explicitly.


def _attempt(n: int, phase: str = "verify") -> Path:
    """Where the engine puts attempt `n`; computed without creating it."""
    return paths.run_dir(RUN_ID) / CARD_ID / f"{phase}.{n}"


def _logging_step(seen: list[object]):
    def logging_step(log_dir=None):
        seen.append(log_dir)
        return {}

    return logging_step


def test_run_one_step_hands_a_declaring_step_a_fresh_attempt_dir_each_call(store):
    seen: list[object] = []
    step = Step("verify", _logging_step(seen))

    first = _run(store, step)
    second = _run(store, step)

    assert first.ok is True and second.ok is True
    assert seen == [_attempt(1), _attempt(2)]
    assert _attempt(1).is_dir()
    assert _attempt(2).is_dir()


def test_run_one_step_creates_no_attempt_dir_for_a_step_that_does_not_declare_log_dir(
    store,
):
    outcome = _run(store, Step("verify", lambda: {}))

    assert outcome.ok is True
    assert not _attempt(1).exists()


def test_the_engines_log_dir_overrides_document_args_and_the_binding_table(store):
    seen: list[object] = []
    step = Step("verify", _logging_step(seen), args={"log_dir": "/elsewhere"})

    _run(store, step, table={"log_dir": "/from-the-table"})

    assert seen == [_attempt(1)]


def test_run_one_step_never_overwrites_an_earlier_attempt_directory(store):
    # Review Focus 3: a re-run after resume finds `verify.1` from the previous
    # process on disk and lands in `verify.2`.
    earlier = paths.attempt_dir(RUN_ID, CARD_ID, "verify", 1)
    (earlier / "stdout.log").write_text("the first run's evidence\n", encoding="utf-8")
    seen: list[object] = []

    _run(store, Step("verify", _logging_step(seen)))

    assert seen == [_attempt(2)]
    assert (earlier / "stdout.log").read_text(encoding="utf-8") == (
        "the first run's evidence\n"
    )


class _RunlessStore:
    """A store with no run id: only `record_phase`, which the walk calls."""

    def record_phase(self, story_id, card_id, phase) -> None:
        pass


def test_a_store_with_no_run_id_leaves_the_steps_default(tmp_path):
    seen: list[object] = []

    outcome = walk.run_one_step(
        phase=Step("verify", _logging_step(seen), args={"log_dir": "/elsewhere"}),
        table={},
        store=_RunlessStore(),
        story_id=STORY_ID,
        subtask=_subtask(),
        clock=lambda: FIXED,
    )

    assert outcome.ok is True
    assert seen == [None]


@pytest.mark.git
def test_a_red_verify_through_run_one_step_leaves_its_logs_under_the_run_dir(
    store, tmp_path
):
    """Card test: production wiring with the real step and the real gate."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    script = (
        "import sys; print('7 passed'); sys.stderr.write('E boom\\n'); "
        "raise SystemExit(1)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    step = Step("verify", verify.run_suite, gates=(reducers.verification_passed_gate,))

    outcome = _run(
        store, step, table={"commands": [command], "worktree": str(worktree)}
    )

    assert outcome.ok is False
    assert _phase_rows(store)[-1][:2] == ("verify", "failed")
    log_dir = _attempt(1)
    assert (log_dir / verify.STDOUT_LOG).read_text(encoding="utf-8") == (
        f"==> {command} (exit 1)\n7 passed\n"
    )
    assert (log_dir / verify.STDERR_LOG).read_text(encoding="utf-8") == (
        f"==> {command} (exit 1)\nE boom\n"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_walk.py -m "" -k "attempt_dir or log_dir or run_id or logs_under" -v`
Expected: FAIL — `seen == [None, None]` instead of the attempt paths (or `['/elsewhere']`); the card test fails because `verify.1/stdout.log` does not exist. `test_run_one_step_creates_no_attempt_dir...` already passes (it pins that the change stays opt-in).

(`-m ""` replaces the default `-m` expression so the `git`-marked card test is selected too; it is also in the default suite.)

- [ ] **Step 3: Implement**

In `src/agent_manager/runtime/walk.py`, change line 31 to:

```python
from agent_manager import models, paths, prompt
```

Add this helper directly above `def run_one_step(`:

```python
LOG_DIR_PARAMETER = "log_dir"
"""The parameter a deterministic step declares to be handed its attempt directory."""


def _with_log_dir(
    fn: Callable[..., Any],
    kwargs: dict[str, Any],
    store: Any,
    card: str,
    phase_name: str,
) -> dict[str, Any]:
    """`kwargs` with the engine's `log_dir`, for a step that declares one.

    Spec e1b1e7d5 Decision 1: only a step whose signature names `log_dir`
    gets a directory, and only then is one created -- every other step is
    untouched and nothing appears on disk for it. The directory is
    `paths.attempt_dir(run, card, phase, n)` with `n` one past the highest
    already on disk, the numbering agent phases use (`dispatch.next_attempt`),
    so a re-run after resume lands in a fresh `<phase>.N`. The engine's value
    wins over anything the binding table or the document's `args` said. A
    store with no run id (`checkpoint._floor` makes the same check) supplies
    nothing, and the step's own default applies.
    """
    if LOG_DIR_PARAMETER not in inspect.signature(fn).parameters:
        return kwargs
    bound = {key: value for key, value in kwargs.items() if key != LOG_DIR_PARAMETER}
    run_id = getattr(store, "run_id", None)
    if not run_id:
        return bound
    attempt = paths.highest_attempt(run_id, card, phase_name) + 1
    bound[LOG_DIR_PARAMETER] = paths.attempt_dir(run_id, card, phase_name, attempt)
    return bound
```

In `run_one_step`, replace

```python
        kwargs = bind_arguments(
            phase.run, table, phase.args, phase=phase.name, function=label
        )
        result = phase.run(**kwargs)
```

with

```python
        kwargs = bind_arguments(
            phase.run, table, phase.args, phase=phase.name, function=label
        )
        kwargs = _with_log_dir(phase.run, kwargs, store, subtask.card_id, phase.name)
        result = phase.run(**kwargs)
```

It sits inside the `try`, so an `OSError` creating the directory is recorded as the phase's `failed` detail by the existing catch-all.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_walk.py -m "" -v`
Expected: PASS.

- [ ] **Step 5: Run the default suite to catch fallout from new `verify.N` directories**

Run: `uv run pytest`
Expected: PASS. If a test that snapshots a run tree now sees a `verify.N/` directory, that is this change working; update that test's expectation to include it rather than suppressing the directory.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/runtime/walk.py tests/runtime/test_walk.py
git commit -m "feat: run_one_step hands a log_dir-declaring step its attempt dir (e1b1e7d5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `am logs --phase <deterministic phase>` reads the logs from disk; payloads gain `artifacts.stderr`

**Files:**
- Modify: `src/agent_manager/cli.py:406-437` (`logs_payload`, plus two new functions after it), `src/agent_manager/cli.py:1449-1489` (`logs_for`), `src/agent_manager/cli.py:1492-1520` (`logs` help text)
- Test: `tests/test_cli.py` (append after `test_logs_writes_nothing`, which ends near line 4466; add one assertion to `test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase` at line 4292)

**Interfaces:**
- Consumes: `paths.recorded_attempts(run_id, card, phase) -> list[int]` and `paths.attempt_path(run_id, card, phase, attempt) -> Path` from Task 2; the `verify.N/stdout.log` / `stderr.log` layout from Tasks 1 and 3; existing `read_artifact`, `select_attempt`, `UnknownAttemptError`.
- Produces:
  - `cli.select_step_attempt(subtask: models.SubtaskRun, phase: models.PhaseRun, recorded: Sequence[int], attempt: int | None = None) -> int`
  - `cli.step_logs_payload(run: models.Run, story: models.StoryRun, subtask: models.SubtaskRun, phase: models.PhaseRun, attempt: int, directory: Path) -> dict[str, Any]`
  - `logs_payload(...)["artifacts"]["stderr"] == {"path": None, "present": False, "text": None}` for agent phases.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add `import shutil` to the stdlib imports (between `import os` and `import socket`).

In `test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase` (line 4292), add after the `stdout` assertion:

```python
    assert data["artifacts"]["stderr"] == {"path": None, "present": False, "text": None}
```

Then append directly after `test_logs_writes_nothing`:

```python
def _write_step_logs(run_id: str, n: int) -> Path:
    """`verify.<n>/` as `run_one_step` + `verify.run_suite` leave it.

    The *test* calls `paths.attempt_dir`, which creates the directory; `logs`
    must only read it.
    """
    directory = paths.attempt_dir(run_id, "card-1", "verify", n)
    (directory / "stdout.log").write_text(
        f"==> uv run pytest (exit 1)\nstdout of verify.{n}\n", encoding="utf-8"
    )
    (directory / "stderr.log").write_text(
        f"==> uv run pytest (exit 1)\nstderr of verify.{n}\n", encoding="utf-8"
    )
    return directory


def test_logs_for_a_deterministic_phase_reads_its_highest_attempt_off_disk(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)
    second = _write_step_logs(LOGS_RUN_ID, 2)
    base = ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]

    result = runner.invoke(cli.app, [*base, "--phase", "verify"])

    assert result.exit_code == 0, result.stdout
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["run_id"] == LOGS_RUN_ID
    assert data["story_id"] == "story-1"
    assert data["card"] == "card-1"
    assert data["phase"] == "verify"
    assert data["attempt"] == 2
    assert data["status"] is None
    assert data["exit_code"] is None
    artifacts = data["artifacts"]
    assert artifacts["prompt"] == {"path": None, "present": False, "text": None}
    assert artifacts["result"] == {"path": None, "present": False, "text": None}
    assert artifacts["stdout"] == {
        "path": str(second / "stdout.log"),
        "present": True,
        "text": "==> uv run pytest (exit 1)\nstdout of verify.2\n",
    }
    assert artifacts["stderr"] == {
        "path": str(second / "stderr.log"),
        "present": True,
        "text": "==> uv run pytest (exit 1)\nstderr of verify.2\n",
    }

    earlier = runner.invoke(cli.app, [*base, "--phase", "verify", "--attempt", "1"])

    assert earlier.exit_code == 0, earlier.stdout
    earlier_data = json.loads(earlier.stdout)["data"]
    assert earlier_data["attempt"] == 1
    assert earlier_data["artifacts"]["stdout"]["text"] == (
        "==> uv run pytest (exit 1)\nstdout of verify.1\n"
    )


def test_logs_for_a_deterministic_phase_reports_a_missing_log_file_as_absent(
    projection,
):
    _record_for_logs(projection, LOGS_RUN_ID)
    directory = _write_step_logs(LOGS_RUN_ID, 1)
    (directory / "stderr.log").unlink()

    result = runner.invoke(
        cli.app,
        ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)],
    )

    assert result.exit_code == 0, result.stdout
    artifacts = json.loads(result.stdout)["data"]["artifacts"]
    assert artifacts["stdout"]["present"] is True
    assert artifacts["stderr"] == {
        "path": str(directory / "stderr.log"),
        "present": False,
        "text": None,
    }


def test_logs_for_a_deterministic_phase_with_no_attempt_on_disk_is_an_envelope(
    projection,
):
    _record_for_logs(projection, LOGS_RUN_ID)
    tree_before = _runs_snapshot()

    result = runner.invoke(
        cli.app,
        ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)],
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownAttemptError"
    assert envelope["error"]["message"] == (
        "phase 'verify' of card 'card-1' has no recorded attempt yet"
    )
    assert _runs_snapshot() == tree_before
    assert not (paths.data_dir() / "runs" / LOGS_RUN_ID / "card-1" / "verify.1").exists()


def test_logs_for_a_deterministic_phase_names_the_attempts_it_has(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)
    _write_step_logs(LOGS_RUN_ID, 2)
    base = ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)]

    for wanted in ("7", "0"):
        result = runner.invoke(cli.app, [*base, "--attempt", wanted])

        assert result.exit_code == cli.EXIT_ERROR
        envelope = json.loads(result.stdout)
        assert envelope["error"]["type"] == "UnknownAttemptError"
        assert envelope["error"]["message"] == (
            f"phase 'verify' of card 'card-1' has no attempt {wanted};"
            " recorded attempts: 1, 2"
        )


def test_logs_for_a_deterministic_phase_with_no_runs_directory_creates_nothing(
    projection,
):
    """Review Focus 4: the projection survives but `runs/` is gone. `logs`
    refuses, and leaves `runs/` absent rather than minting it."""
    _record_for_logs(projection, LOGS_RUN_ID)
    runs_root = paths.data_dir() / "runs"
    shutil.rmtree(runs_root)
    base = ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)]

    latest = runner.invoke(cli.app, base)
    numbered = runner.invoke(cli.app, [*base, "--attempt", "1"])

    assert latest.exit_code == cli.EXIT_ERROR
    assert json.loads(latest.stdout)["error"]["message"] == (
        "phase 'verify' of card 'card-1' has no recorded attempt yet"
    )
    assert numbered.exit_code == cli.EXIT_ERROR
    assert json.loads(numbered.stdout)["error"]["message"] == (
        "phase 'verify' of card 'card-1' has no attempt 1; recorded attempts: none"
    )
    assert not runs_root.exists()


def test_logs_with_no_flags_still_skips_a_deterministic_phase_with_logs_on_disk(
    projection,
):
    """Decision 4: the no-flag default stays pure over recorded `Attempt` rows."""
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)["data"]
    assert data["phase"] == "implement"
    assert data["artifacts"]["stderr"] == {"path": None, "present": False, "text": None}


def test_logs_for_a_deterministic_phase_writes_nothing(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    _write_step_logs(LOGS_RUN_ID, 1)
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    result = runner.invoke(
        cli.app,
        ["logs", LOGS_RUN_ID, "card-1", "--phase", "verify", "--repo-dir", str(projection)],
    )

    assert result.exit_code == 0
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "logs" -v`
Expected: FAIL — the no-flag tests with `KeyError: 'stderr'`; `--phase verify` tests with today's `"has no recorded attempt yet"` even when `verify.1`/`verify.2` exist (because `select_attempt` sees no `Attempt` rows), so the success tests fail on `exit_code`; the `--attempt 7` test fails on `"recorded attempts: none"` instead of `"1, 2"`. The two "no attempt on disk" tests may already pass; they pin today's wording.

- [ ] **Step 3: Implement**

In `src/agent_manager/cli.py`, in `logs_payload` (lines 406-437), change the docstring's first line to `"""§10's `logs` output: what was selected, and the artifacts of it.` and the `"artifacts"` block to:

```python
        "artifacts": {
            "prompt": read_artifact(attempt.prompt_path),
            "result": read_artifact(attempt.result_path),
            "stdout": read_artifact(attempt.stdout_path),
            # The launcher merges an agent's stderr into stdout; the key is
            # here so agent and deterministic payloads share one shape.
            "stderr": read_artifact(None),
        },
```

Directly after `logs_payload`, add:

```python
def select_step_attempt(
    subtask: models.SubtaskRun,
    phase: models.PhaseRun,
    recorded: Sequence[int],
    attempt: int | None = None,
) -> int:
    """Which attempt of a deterministic phase `logs` should report, or a refusal.

    A deterministic phase has no `Attempt` rows (spec e1b1e7d5 Decision 2), so
    its attempts are the `<phase>.N` directories on disk, which the caller
    scans (`paths.recorded_attempts`) and passes in -- keeping this, like
    `select_attempt`, pure. Same defaults and the same wording as
    `select_attempt`: the highest number with no `attempt`.
    """
    if attempt is None:
        if not recorded:
            raise UnknownAttemptError(
                f"phase {phase.name!r} of card {subtask.card_id!r} has no recorded"
                " attempt yet"
            )
        return max(recorded)
    if attempt in recorded:
        return attempt
    numbers = ", ".join(str(n) for n in recorded) or "none"
    raise UnknownAttemptError(
        f"phase {phase.name!r} of card {subtask.card_id!r} has no attempt {attempt};"
        f" recorded attempts: {numbers}"
    )


def step_logs_payload(
    run: models.Run,
    story: models.StoryRun,
    subtask: models.SubtaskRun,
    phase: models.PhaseRun,
    attempt: int,
    directory: Path,
) -> dict[str, Any]:
    """`logs_payload`'s shape for a deterministic phase's on-disk attempt.

    `status` and `exit_code` are `None`: no `Attempt` row exists to carry
    them, and each command's exit label is in its log header instead. A step
    writes no prompt or result, so those two are always absent.
    """
    return {
        "run_id": run.id,
        "story_id": story.card_id,
        "card": subtask.card_id,
        "phase": phase.name,
        "attempt": attempt,
        "status": None,
        "exit_code": None,
        "artifacts": {
            "prompt": read_artifact(None),
            "result": read_artifact(None),
            "stdout": read_artifact(directory / "stdout.log"),
            "stderr": read_artifact(directory / "stderr.log"),
        },
    }
```

(`Sequence` is already imported from `collections.abc` at the top of `cli.py`.)

In `logs_for` (lines 1449-1489), replace

```python
        story, subtask = found
        chosen_phase, chosen_attempt = select_attempt(
            subtask, phase=phase, attempt=attempt
        )
        return logs_payload(run, story, subtask, chosen_phase, chosen_attempt)
```

with

```python
        story, subtask = found
        step = next(
            (
                item
                for item in subtask.phases
                if item.name == phase and item.kind == "deterministic"
            ),
            None,
        )
        if step is not None:
            # Read-only on disk: `recorded_attempts` and `attempt_path` create
            # nothing, unlike `attempt_dir` and `run_dir`.
            recorded = paths.recorded_attempts(run_id, card, step.name)
            n = select_step_attempt(subtask, step, recorded, attempt)
            directory = paths.attempt_path(run_id, card, step.name, n)
            return step_logs_payload(run, story, subtask, step, n, directory)
        chosen_phase, chosen_attempt = select_attempt(
            subtask, phase=phase, attempt=attempt
        )
        return logs_payload(run, story, subtask, chosen_phase, chosen_attempt)
```

Add one paragraph to the end of `logs_for`'s docstring:

```
    A `--phase` naming a deterministic phase is answered from disk: its
    attempts are the `<phase>.N` directories `run_one_step` created, and the
    payload carries that attempt's `stdout.log` and `stderr.log` (spec
    e1b1e7d5). With no `--phase`, only recorded `Attempt` rows count.
```

In the `logs` command (line ~1517), change the docstring to:

```python
    """Print one attempt's prompt, result and captured stdout/stderr."""
```

Confirm `paths` is already imported in `cli.py` (it is, in the `from agent_manager import (...)` block); add it there if a later edit removed it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "logs or payload or artifact" -v`
Expected: PASS, including the pre-existing `logs_payload` unit tests (they assert individual keys, so the added `stderr` key does not break them).

- [ ] **Step 5: Run the full default suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: am logs --phase verify reads the persisted verify output (e1b1e7d5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.**
- `run_suite(..., *, log_dir=None)` behaviour, every bullet → Task 1: default writes nothing (`test_no_log_dir_writes_nothing...`); files created/truncated up front (`test_a_clean_suite...` stale content, `test_an_unlaunchable_first_command...`); header + verbatim stream + newline rule (`test_a_red_commands_full_streams...`, `test_a_stream_without_a_trailing_newline...`); run order, flushed per command (`_append_section` closes the file each time); no section after red (`test_a_command_after_a_red_one...`); unlaunchable → no section, error unchanged, earlier sections kept (`test_an_unlaunchable_command_keeps...`); explore extras (`test_explore_typecheck_and_lint...`); `OSError` propagates (`test_an_unwritable_log_dir...`); identical dict (`test_no_log_dir...`).
- Engine Decision 1 → Task 3: declaring step gets `.1` then `.2`; non-declaring gets no directory; args/table overridden; no run id → default; resume lands in `.2`; real verify card test (spec row 9). Integrate's `verify` uses the same `run_one_step` — no extra task.
- Decision 2 (no `Attempt` row) → nothing records one; Task 4 tests assert `_attempt_rows` unchanged.
- Decision 3 (clean verifies keep logs) → `test_a_clean_suite_keeps_both_logs...`.
- Decision 4 (no-flag default unchanged) → `test_logs_with_no_flags_still_skips_a_deterministic_phase...` plus the amended existing no-flag test.
- `am logs --phase <deterministic>`: highest default, `--attempt N`, both refusals with today's wording, `stderr` key, absent files via `read_artifact`, other deterministic phases share the lookup (they have no directories, so they get "no recorded attempt yet") → Task 4. Read-only scan → Task 2 (spec row 13) and Task 4's `_runs_snapshot` checks.
- Spec row 11 says "`runs/<RUN>/` was not created", but `_record_for_logs` opens a real `Store`, whose `Journal` creates `runs/<RUN>/` before `logs` runs. The plan pins the intent two ways: `test_logs_for_a_deterministic_phase_with_no_attempt_on_disk...` asserts the run tree is byte-identical and no `verify.1` appeared, and `test_logs_for_a_deterministic_phase_with_no_runs_directory_creates_nothing` removes `runs/` and asserts `logs` does not recreate it.
- Spec rows 1-13 each map to a named test above; row 12's "existing no-flag test still selects `implement`" is the unchanged assertion in that test plus the new `stderr` line.

**Placeholder scan.** No TBD/TODO; every code step carries full code; no "similar to Task N".

**Type consistency.** `log_dir: Path | None` (Task 1) is what `_with_log_dir` passes (`paths.attempt_dir` returns `Path`). `STDOUT_LOG`/`STDERR_LOG` (Task 1) are used by Task 3's card test; Task 4 reads the literal names `stdout.log`/`stderr.log`, which equal those constants. `paths.recorded_attempts -> list[int]` and `paths.attempt_path -> Path` (Task 2) match their use in `logs_for` (Task 4). `select_step_attempt(subtask, phase, recorded, attempt)` argument order matches its single call site.

**Review Focus.** Each of the five lines names its pinning test and owning task: huge output (Task 1), non-UTF-8 (Task 1), resume re-run (Task 3), absent `runs/` (Task 4), unwritable `log_dir` (Task 1).
<!-- task-pipeline: validated -->
