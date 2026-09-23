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
