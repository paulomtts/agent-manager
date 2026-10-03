# Export AM_RUN_ID and AM_CARD_ID into verify commands — design

Card: `e2efd21d-5ee5-4d62-8d88-8aeab951b31b` (parent story `5785ee15`).

## Goal

When the walk runs the `verify` step, each verification command's process
sees two extra environment variables: `AM_RUN_ID` (the run doing the
verifying) and `AM_CARD_ID` (the card being verified). It also keeps
everything else it would have inherited. A `verify.run_suite` call made
outside the walk, with no ids, runs its commands with neither variable set.

A verification command can now tell which run and card it is verifying. For
example, agent-manager's own suite, run as its own verification, can tell
which run it belongs to.

## Inherited constraints

- **Deterministic phase contract.** `verify` is a deterministic step called by
  name-bound keyword arguments, and its return value is the phase result
  (design §6, `2026-09-23-agent-manager-design.md:256-281`; superseded for the
  phase model by the pygents addendum §5, as the §6 banner at line 258 says).
  The returned dict must not change shape: `reducers.verification_passed_gate`
  and the board read it.
- **Context plumbing.** Values reach a step through the binding table, by
  parameter name (design §7, lines 283-306). `card` is the bare card id that
  `walk.subtask_context` already binds (`walk.py:101`). It is a reserved key
  (`walk.py:40-52`), so no phase result can replace it.
- **Engine-supplied values win.** A value the engine derives from the store,
  rather than from the table, is injected only into a step whose signature
  declares it. It overrides the table and the document's `args`, and a store
  with no run id supplies nothing. That is the `log_dir` precedent (spec
  e1b1e7d5 Decision 1, `persist-verify-output-e1b1e7d5.md`; `walk.py:428-455`).
- **verify is read-only.** `verify.run_suite` must not write to the repository
  (design §9, quoted at `verify.py:9`). Changing a child's environment writes
  nothing.
- **Plain dataclasses for internal state.** Pydantic models are only for data
  validated at a process boundary (`CLAUDE.md` Conventions). No new model is
  needed.
- **Test tiers.** Tier is chosen by what a test spawns, and `tests/steps/` is
  auto-marked `git` by default (design §14, lines 507-538; `CLAUDE.md` Test
  tiers; `tests/conftest.py:128`).

## Observable behavior

### B1 — `run_command` sets the child's environment

`verify.run_command(argv, cwd, env=None)` gains an optional third parameter,
`env: Mapping[str, str] | None`. It holds only the overlay, not a complete
environment.

The child process's environment is built in three steps:

1. Start from a copy of the parent's `os.environ`.
2. Remove `AM_RUN_ID` and `AM_CARD_ID`, whether or not they were inherited.
3. Apply `env` on top, if it was given.

So:

- `run_command(argv, cwd)` runs the child with every inherited variable except
  `AM_RUN_ID` and `AM_CARD_ID`. This holds even if the `am` process itself has
  them set, for example because it is running as some outer run's
  verification. A stale outer id must never be reported as this command's id.
- `run_command(argv, cwd, env={"AM_RUN_ID": "r", "AM_CARD_ID": "c"})` runs the
  child with every inherited variable, plus those two values.
- Everything else stays as it is today: `cwd`, `shell=False`, captured
  streams, `errors="replace"`, the `CommandResult` that comes back, and
  `FileNotFoundError`/`PermissionError` when the command cannot be launched.

### B2 — `run_suite` takes the ids and hands them to the runner

`verify.run_suite` gains two keyword-only parameters, both defaulting to
`None`: `run_id: str | None` and `card: str | None`.

- **Building the overlay.** `run_suite` builds the overlay
  `{"AM_RUN_ID": run_id, "AM_CARD_ID": card}`. Any id that is `None` or an
  empty or whitespace-only string is treated as absent and left out.
- **Validation.** An id that is present but is not a `str` raises
  `ValueError`, naming the parameter and the value. This happens before the
  first command runs, in the same place as `run_suite`'s other `ValueError`s.
- **At least one id present.** Every planned command, including the `explore`
  typecheck and lint extras, calls `runner(argv, worktree_path, env=overlay)`
  with the same overlay. The overlay holds only the present keys, never the
  full environment: merging with the inherited environment is
  `run_command`'s job (B1).
- **No ids present.** Every command calls `runner(argv, worktree_path)` with
  exactly two positional arguments, as today. Every existing two-parameter
  fake runner in `tests/steps/test_verify.py` keeps working unmodified.
  Under the default runner, this means the command runs with neither variable
  set (B1).
- **Unchanged.** The returned dict, the `stdout.log`/`stderr.log` contents
  under `log_dir`, how commands are planned, stopping at the first red
  command, and `VerifyError` all behave exactly as they do today.

The `CommandRunner` alias and its docstring (`verify.py:114-119`) are updated
to say that a runner is called with `(argv, cwd)` and, when the walk supplied
ids, also with the keyword argument `env`.

### B3 — the walk supplies `run_id`; the table already supplies `card`

- **`card`.** No new machinery. `bind_arguments` binds the table's `card` (the
  subtask's `card_id`) to `run_suite`'s new `card` parameter by name. In the
  `task` workflow, that is the subtask card's id. In the `integrate` workflow,
  it is the `card_id` of `integration.py`'s synthetic subtask (the story id,
  `integration.py:145`), and that is the value exported.
- **`run_id`.** `walk.run_one_step` (`walk.py:458-518`) injects the store's
  run id under the parameter name `run_id`, using the same rule as
  `_with_log_dir`:
  - It is injected only into a step whose signature declares a `run_id`
    parameter. Any other step's kwargs are untouched.
  - The value is `getattr(store, "run_id", None)`. When it is non-empty, it
    replaces any `run_id` that the binding table or the step's `args` supplied.
  - When the store has no run id, any `run_id` from the table or `args` is
    dropped, and the step's own default (`None`) applies.
- **The parameter name.** It is a named module constant, `RUN_ID_PARAMETER =
  "run_id"`, next to `LOG_DIR_PARAMETER`.
- **Not reserved.** `run_id` is not added to `RESERVED_CONTEXT_KEYS`. The
  engine's injection already wins over the table, the same way `log_dir` is
  handled, and `log_dir` is not reserved either.

### B4 — direct callers are unchanged

`bases.py:146` and `integration.py:114` call `verify.run_suite(commands,
worktree)` and are not edited. Their commands run with neither variable set
(B1 + B2).

### B5 — README

Two lines go in README `## Usage`, right after the `--verify` /
`--allow-no-verification` paragraph (README.md:68-73). They name the
variables:

> Each `--verify` command runs with `AM_RUN_ID` (the run's id) and
> `AM_CARD_ID` (the card being verified) added to its environment.
> The base-branch and final integration checks run their commands with neither set.

## Out of scope

- **The `tests/conftest.py` data-dir guard.** It stays as it is. The card makes
  this optional ("include only if trivial"), and it is not trivial: `_snapshot`
  excludes the whole top-level `runs/` segment (`conftest.py:80-83`). Narrowing
  it to "this run's own id only" means changing that comparison and deciding
  what to do when `AM_RUN_ID` is unset. The forward reference in its docstring
  (`conftest.py:74-76`) stays, and that work is left to a future card.
- **Ids for `bases.py` and `integration.py`.** Neither direct caller passes
  ids (B4).
- **Other variables and other steps.** No variable beyond these two is added,
  and no step other than `verify` sets an environment. Agent-harness launches
  are untouched.
- **Board and store.** Nothing is written to the board, the store or the
  journal about these variables.

## Test list

`tests/steps/` is auto-marked `git`, and there is no unit marker to opt out
(`tests/conftest.py:128`). So the fake-runner tests in `test_verify.py` sit in
the `git` tier next to that file's existing fake-runner tests, even though
they spawn nothing. The real-subprocess tests spawn `sys.executable`: no
`brd`, no `claude`, `tmp_path` only. That is the file's existing pattern
(`test_verify.py:4`, `_py` at line 44), and `git` is the closest tier for
them. The walk tests spawn nothing and use the real `Store` on `tmp_path`, as
the existing `log_dir` walk tests do, so they are `unit`.

| # | Test | File | Tier | Why that tier |
|---|---|---|---|---|
| T1 | A fake runner that accepts `env` sees `{"AM_RUN_ID": run_id, "AM_CARD_ID": card}` for every planned command, including an `explore` lint command, when `run_suite` gets both ids | `tests/steps/test_verify.py` | git (dir auto-mark) | Fake runner, no spawn. Lives with its siblings. |
| T2 | With no ids, a strictly two-parameter fake runner is called with exactly `(argv, worktree)` and the suite passes | `tests/steps/test_verify.py` | git (dir auto-mark) | Fake runner. Pins backward compatibility. |
| T3 | `run_suite(..., card="c")` with no `run_id` hands an overlay of only `{"AM_CARD_ID": "c"}`. `run_id="r"` alone hands only `{"AM_RUN_ID": "r"}` | `tests/steps/test_verify.py` | git (dir auto-mark) | Fake runner. |
| T4 | Ids of `""` and `"   "` count as absent: the runner gets two positional arguments | `tests/steps/test_verify.py` | git (dir auto-mark) | Fake runner. |
| T5 | A non-`str` id (`card=123`) raises `ValueError` naming `card`, and the runner is never called | `tests/steps/test_verify.py` | git (dir auto-mark) | Fake runner. |
| T6 | `run_command` with an overlay: a child (`sys.executable -c` printing `os.environ` as JSON) sees both values, plus a sentinel variable set with `monkeypatch.setenv` in the parent | `tests/steps/test_verify.py` | git | Real subprocess (`sys.executable`), `tmp_path` only. |
| T7 | `run_command` with no `env`, while the parent has `AM_RUN_ID`/`AM_CARD_ID` set (`monkeypatch`): the child sees neither, and still sees the sentinel | `tests/steps/test_verify.py` | git | Real subprocess. Pins "a stale outer id never leaks". |
| T8 | `run_suite` through the default runner, with ids, end to end: a command that writes its env to a file under `tmp_path` records both values. The same command without ids records neither | `tests/steps/test_verify.py` | git | Real subprocess. Proves B2 and B1 compose. |
| T9 | `run_one_step` on a step declaring `run_id` passes it the store's `RUN_ID` | `tests/runtime/test_walk.py` | unit | No spawn, real store on `tmp_path` (as the `log_dir` tests are). |
| T10 | The engine's `run_id` overrides `args={"run_id": "other"}` and a table `run_id` | `tests/runtime/test_walk.py` | unit | Same. |
| T11 | `_RunlessStore` with a table `run_id`: the step sees its own default (`None`) | `tests/runtime/test_walk.py` | unit | Same. |
| T12 | A step not declaring `run_id` is called without it, even when the table has one | `tests/runtime/test_walk.py` | unit | Same. |
| T13 | Card test: `run_one_step(Step("verify", verify.run_suite, args={"runner": fake}), table={"commands": [...], "worktree": str(tmp_path), "card": CARD_ID})`. The fake sees `env == {"AM_RUN_ID": RUN_ID, "AM_CARD_ID": CARD_ID}` | `tests/runtime/test_walk.py` | unit | Fake runner via the document's `args`, no spawn. This is the card's "a fake runner sees both variables with the walk's ids". |

T2 is also the card's "absent ids (direct run_suite call)" test at the runner
seam, and T8's no-id half is that same test at the process level.

## Files

- Modify `src/agent_manager/steps/verify.py`: `run_command` (lines 136-154),
  the `CommandRunner` docstring (lines 114-119), and `run_suite`'s signature,
  docstring and runner call (lines 312-391).
- Modify `src/agent_manager/runtime/walk.py`: add `RUN_ID_PARAMETER` and
  `_with_run_id` beside `_with_log_dir` (lines 425-455), and call it in
  `run_one_step` right after `_with_log_dir` (line 483).
- Modify `README.md`: add the two lines from B5.
- Modify `tests/steps/test_verify.py` (T1-T8) and `tests/runtime/test_walk.py`
  (T9-T13).

## Review Focus (for the planner)

1. **A parent that already has `AM_RUN_ID` set** (dogfooding, or a nested
   `am`). The child of a no-id call must not see it (T7). The child of an id
   call must see the new value, not the inherited one (T6 variant: set the
   parent's value to `"stale"`).
2. **The existing two-parameter fakes in `test_verify.py`.** They must keep
   passing untouched. Run the whole file, not just the new tests.
3. **`integrate` workflow's verify step.** It now receives `card` (the
   synthetic subtask's id) and `run_id`. Its commands get both variables, and
   its result is unchanged. The existing integrate e2e_fake tests must stay
   green (`uv run pytest -m e2e_fake`).
4. **`PATH` and other inherited variables.** After the switch from no `env=`
   to an explicit `env=`, they must still reach the child. Otherwise every
   `uv run pytest` verification breaks (T6 and T7 sentinels).
5. **A step's `args` naming `run_id`.** The engine silently overrides it
   (T10), and `bind_arguments` must not reject it, since `run_suite` declares
   the parameter.
