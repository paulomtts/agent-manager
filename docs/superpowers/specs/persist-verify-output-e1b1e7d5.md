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
