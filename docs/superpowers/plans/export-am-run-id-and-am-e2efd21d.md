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


---

# Export AM_RUN_ID and AM_CARD_ID into verify commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every command the walk's `verify` step runs sees `AM_RUN_ID` and `AM_CARD_ID` in its environment. A direct `verify.run_suite` call with no ids runs its commands with neither variable set.

**Architecture:** `verify.run_command` builds the child environment: it copies `os.environ`, removes both variables, then applies an optional overlay. `verify.run_suite` takes keyword-only `run_id`/`card`, turns them into that overlay, and passes it to the runner as `env=` only when at least one id is present. `walk.run_one_step` injects the store's run id into any step that declares `run_id`. This uses the `_with_log_dir` rule. `card` already reaches the step by name from the binding table.

**Tech Stack:** Python 3, `subprocess`, pytest (`monkeypatch`, `tmp_path`), `uv`.

**Spec:** `docs/superpowers/specs/export-am-run-id-and-am-e2efd21d.md` (also prepended above).

## Global Constraints

- The variable names are exactly `AM_RUN_ID` and `AM_CARD_ID`.
- `run_suite`'s returned dict must not change shape: `{"passed", "verified", "detail"}`.
- `verify.run_suite` must not write to the repository (design §9).
- Plain dataclasses or dicts for internal state. No new Pydantic model.
- With no ids, the runner is called with exactly two positional arguments `(argv, worktree_path)`.
- `run_id` is NOT added to `walk.RESERVED_CONTEXT_KEYS`.
- `bases.py:146` and `integration.py:114` are not edited.
- `tests/conftest.py` is not edited.
- The tier rule: `tests/steps/` is auto-marked `git`. `tests/runtime/` and `tests/workflow/` tests here are `unit` (no spawn).
- Verification: `uv run pytest` (default tiers). There is no lint or typecheck command.

## Review Focus

1. **A parent `am` that already has `AM_RUN_ID`/`AM_CARD_ID` set** (dogfooding, or a nested `am`). A no-id child must see neither variable. An id child must see the new values, not `"stale"`. Pinned in Task 1 (T7, plus the T6 stale variant).
2. **The existing two-parameter fake runners in `tests/steps/test_verify.py`** (`_recorder` and others) must pass unmodified. Pinned in Task 2 by T2 and by running the whole file.
3. **The `integrate` workflow's verify step** now gets `card` = the synthetic subtask's `card_id`, plus `run_id`. Pinned in Task 3 by a binding test in `tests/workflow/test_integrate.py`, and in Task 5 by `uv run pytest -m e2e_fake`.
4. **`PATH` and other inherited variables** must still reach the child now that `env=` is passed explicitly. Pinned in Task 1 by the sentinel in T6/T7 and the `PATH` assertion.
5. **A step's document `args` naming `run_id`**: `bind_arguments` accepts it, since `run_suite` declares the parameter, and the engine overrides it. Pinned in Task 3 by T10.

---

### Task 1: `run_command` builds the child's environment

**Files:**
- Modify: `src/agent_manager/steps/verify.py:19-25` (imports), `:136-154` (`run_command`)
- Test: `tests/steps/test_verify.py` (append a new section at the end of the file)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `verify.RUN_ID_ENV: str = "AM_RUN_ID"`
  - `verify.CARD_ID_ENV: str = "AM_CARD_ID"`
  - `verify.run_command(argv: list[str], cwd: str, env: Mapping[str, str] | None = None) -> CommandResult`

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/steps/test_verify.py`. `json` is a new import: add `import json` between `import inspect` and `import os` at the top of the file.

```python
# ── AM_RUN_ID / AM_CARD_ID in a verification command's environment (e2efd21d) ─
#
# The real-subprocess tests spawn `sys.executable` only, `tmp_path` only: git
# tier by this directory's auto-mark, like the file's other real-process tests.

_ENV_PROBE = (
    "import json, os; print(json.dumps({k: os.environ.get(k) for k in "
    "('AM_RUN_ID', 'AM_CARD_ID', 'AM_TEST_SENTINEL', 'PATH')}))"
)
"""A child that prints the four variables these tests care about as JSON."""


def _child_env(tmp_path: Path, env=None) -> dict[str, object]:
    """Run `_ENV_PROBE` through `run_command` and decode what the child saw."""
    argv = [sys.executable, "-c", _ENV_PROBE]
    if env is None:
        completed = verify.run_command(argv, str(tmp_path))
    else:
        completed = verify.run_command(argv, str(tmp_path), env=env)
    assert completed.exit_code == 0, completed.stderr
    return json.loads(completed.stdout)


def test_run_command_overlays_both_ids_on_the_inherited_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AM_TEST_SENTINEL", "kept")
    monkeypatch.delenv("AM_RUN_ID", raising=False)
    monkeypatch.delenv("AM_CARD_ID", raising=False)

    seen = _child_env(tmp_path, env={"AM_RUN_ID": "r-1", "AM_CARD_ID": "c-1"})

    assert seen["AM_RUN_ID"] == "r-1"
    assert seen["AM_CARD_ID"] == "c-1"
    assert seen["AM_TEST_SENTINEL"] == "kept"
    assert seen["PATH"] == os.environ["PATH"]


def test_run_command_overlay_replaces_a_stale_inherited_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Review Focus 1: an `am` running as some outer run's verification.
    monkeypatch.setenv("AM_RUN_ID", "stale")
    monkeypatch.setenv("AM_CARD_ID", "stale")
    monkeypatch.setenv("AM_TEST_SENTINEL", "kept")

    seen = _child_env(tmp_path, env={"AM_RUN_ID": "r-1", "AM_CARD_ID": "c-1"})

    assert seen["AM_RUN_ID"] == "r-1"
    assert seen["AM_CARD_ID"] == "c-1"
    assert seen["AM_TEST_SENTINEL"] == "kept"


def test_run_command_without_env_never_leaks_an_inherited_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AM_RUN_ID", "stale")
    monkeypatch.setenv("AM_CARD_ID", "stale")
    monkeypatch.setenv("AM_TEST_SENTINEL", "kept")

    seen = _child_env(tmp_path)

    assert seen["AM_RUN_ID"] is None
    assert seen["AM_CARD_ID"] is None
    assert seen["AM_TEST_SENTINEL"] == "kept"
    assert seen["PATH"] == os.environ["PATH"]


def test_run_command_overlay_with_one_id_still_drops_the_other_inherited_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AM_RUN_ID", "stale")
    monkeypatch.setenv("AM_CARD_ID", "stale")

    seen = _child_env(tmp_path, env={"AM_CARD_ID": "c-1"})

    assert seen["AM_RUN_ID"] is None
    assert seen["AM_CARD_ID"] == "c-1"


def test_run_command_does_not_change_the_parents_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AM_RUN_ID", "outer")
    monkeypatch.delenv("AM_CARD_ID", raising=False)

    _child_env(tmp_path, env={"AM_RUN_ID": "r-1", "AM_CARD_ID": "c-1"})

    assert os.environ["AM_RUN_ID"] == "outer"
    assert "AM_CARD_ID" not in os.environ
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -k "run_command" -v`
Expected: FAIL. The overlay tests fail with `TypeError: run_command() got an unexpected keyword argument 'env'`. `test_run_command_without_env_never_leaks_an_inherited_id` fails with `assert 'stale' is None`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/verify.py`, add `import os` to the imports, keeping them alphabetical:

```python
import os
import re
import shlex
import signal
import subprocess
```

Replace `run_command` (lines 136-154) with:

```python
RUN_ID_ENV = "AM_RUN_ID"
CARD_ID_ENV = "AM_CARD_ID"
# The two variables a verification command reads to learn which run and card
# it is verifying (spec e2efd21d). Only `run_suite` called by the walk sets them.


def _child_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    """The parent's environment minus both ids, with `env` applied on top.

    Both ids are removed first even when inherited: an `am` that is itself
    some outer run's verification must never report that outer id as this
    command's.
    """
    child = {
        key: value
        for key, value in os.environ.items()
        if key not in (RUN_ID_ENV, CARD_ID_ENV)
    }
    if env is not None:
        child.update(env)
    return child


def run_command(
    argv: list[str], cwd: str, env: Mapping[str, str] | None = None
) -> CommandResult:
    """The default `CommandRunner`: really run `argv` in `cwd`.

    `shell=False` (the default) is the whole point -- see the module docstring.
    `errors="replace"` keeps a command that emits non-UTF-8 bytes from crashing
    the step; its diagnostic still has to reach a human. `env` is only an
    overlay: the child inherits everything else, never an inherited
    `AM_RUN_ID`/`AM_CARD_ID` (`_child_environment`).
    """
    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        env=_child_environment(env),
    )
    return CommandResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: PASS, the whole file, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/verify.py tests/steps/test_verify.py
git commit -m "feat: verify.run_command overlays an env and never leaks inherited AM ids (e2efd21d)"
```

---

### Task 2: `run_suite` takes `run_id`/`card` and hands the overlay to the runner

**Files:**
- Modify: `src/agent_manager/steps/verify.py`: the `CommandRunner` alias and docstring (lines 114-119), and `run_suite` (signature, docstring, the id check, the runner call).
- Test: `tests/steps/test_verify.py`: update `test_run_suite_takes_explore_by_name_before_the_keyword_only_runner` (line 546) and append new tests at the end.

**Interfaces:**
- Consumes: `verify.RUN_ID_ENV`, `verify.CARD_ID_ENV`, `verify.run_command(argv, cwd, env=None)` from Task 1.
- Produces: `verify.run_suite(commands, worktree, explore=None, *, runner=run_command, log_dir=None, run_id: str | None = None, card: str | None = None) -> dict[str, object]`. Task 3 relies on the parameter names `run_id` and `card`.

- [ ] **Step 1: Write the failing tests**

In `tests/steps/test_verify.py`, replace the body of `test_run_suite_takes_explore_by_name_before_the_keyword_only_runner` with:

```python
def test_run_suite_takes_explore_by_name_before_the_keyword_only_runner():
    # `walk.bind_arguments` binds strictly by parameter name, so the name
    # `explore` is what wires the Explore phase's result in -- no YAML edit.
    # `log_dir` is the name `walk.run_one_step` looks for to hand over the
    # attempt directory (spec e1b1e7d5, Decision 1). `run_id` is injected the
    # same way, and `card` is bound from the table (spec e2efd21d).
    parameters = inspect.signature(verify.run_suite).parameters
    assert list(parameters) == [
        "commands",
        "worktree",
        "explore",
        "runner",
        "log_dir",
        "run_id",
        "card",
    ]
    assert parameters["explore"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["explore"].default is None
    assert parameters["runner"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["log_dir"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["log_dir"].default is None
    for name in ("run_id", "card"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is None
```

Append to the end of `tests/steps/test_verify.py`:

```python
def _env_recorder() -> tuple[list[tuple[tuple, dict]], verify.CommandRunner]:
    """A runner that accepts `env` and records each call's args and kwargs."""
    calls: list[tuple[tuple, dict]] = []

    def runner(*args, **kwargs) -> CommandResult:
        calls.append((args, kwargs))
        return CommandResult(exit_code=0, stdout="fine\n", stderr="")

    return calls, runner


def test_both_ids_reach_every_planned_command_including_explore_extras(
    tmp_path: Path,
):
    # T1
    calls, runner = _env_recorder()
    explore = {"verification": {"typecheck": "mypy .", "lint": ["ruff check ."]}}

    result = verify.run_suite(
        ["uv run pytest"],
        str(tmp_path),
        explore,
        runner=runner,
        run_id="run-7",
        card="e2efd21d",
    )

    assert result["passed"] is True
    expected_env = {"AM_RUN_ID": "run-7", "AM_CARD_ID": "e2efd21d"}
    assert calls == [
        ((["uv", "run", "pytest"], str(tmp_path)), {"env": expected_env}),
        ((["mypy", "."], str(tmp_path)), {"env": expected_env}),
        ((["ruff", "check", "."], str(tmp_path)), {"env": expected_env}),
    ]


def test_no_ids_call_a_strict_two_parameter_runner_exactly_as_before(
    tmp_path: Path,
):
    # T2: a direct `run_suite` call (bases.py, integration.py) at the seam.
    calls, runner = _recorder()

    result = verify.run_suite(["a b", "c"], str(tmp_path), runner=runner)

    assert result["passed"] is True
    assert calls == [(["a", "b"], str(tmp_path)), (["c"], str(tmp_path))]


@pytest.mark.parametrize(
    ("ids", "overlay"),
    [
        ({"card": "c-1"}, {"AM_CARD_ID": "c-1"}),
        ({"run_id": "r-1"}, {"AM_RUN_ID": "r-1"}),
        ({"run_id": "", "card": "c-1"}, {"AM_CARD_ID": "c-1"}),
        ({"run_id": "r-1", "card": "   "}, {"AM_RUN_ID": "r-1"}),
    ],
)
def test_the_overlay_holds_only_the_present_ids(
    tmp_path: Path, ids: dict[str, str], overlay: dict[str, str]
):
    # T3
    calls, runner = _env_recorder()

    verify.run_suite(["a"], str(tmp_path), runner=runner, **ids)

    assert calls == [((["a"], str(tmp_path)), {"env": overlay})]


@pytest.mark.parametrize(
    "ids",
    [
        {"run_id": "", "card": ""},
        {"run_id": "   ", "card": "\t"},
        {"run_id": None, "card": "  "},
    ],
)
def test_blank_ids_count_as_absent(tmp_path: Path, ids: dict[str, object]):
    # T4: the runner gets two positional arguments and nothing else.
    calls, runner = _recorder()

    result = verify.run_suite(["a"], str(tmp_path), runner=runner, **ids)

    assert result["passed"] is True
    assert calls == [(["a"], str(tmp_path))]


@pytest.mark.parametrize(
    ("name", "value"), [("card", 123), ("run_id", ["r-1"]), ("card", b"c-1")]
)
def test_a_non_string_id_raises_before_anything_runs(
    tmp_path: Path, name: str, value: object
):
    # T5
    calls, runner = _env_recorder()

    with pytest.raises(ValueError) as excinfo:
        verify.run_suite(["a"], str(tmp_path), runner=runner, **{name: value})

    assert calls == []
    assert name in str(excinfo.value)
    assert repr(value) in str(excinfo.value)


def test_a_non_string_id_raises_before_the_logs_are_started(tmp_path: Path):
    calls, runner = _env_recorder()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with pytest.raises(ValueError):
        verify.run_suite(
            ["a"], str(tmp_path), runner=runner, log_dir=log_dir, run_id=7
        )

    assert calls == []
    assert list(log_dir.iterdir()) == []


def test_ids_do_not_change_the_result_or_the_logs(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    plain_logs = tmp_path / "plain"
    plain_logs.mkdir()
    id_logs = tmp_path / "ids"
    id_logs.mkdir()
    commands = [_py("print('ok')"), _py("print('bad'); raise SystemExit(1)")]

    without = verify.run_suite(commands, str(worktree), log_dir=plain_logs)
    with_ids = verify.run_suite(
        commands, str(worktree), log_dir=id_logs, run_id="r-1", card="c-1"
    )

    assert with_ids == without
    assert _logs(id_logs) == _logs(plain_logs)


def test_the_default_runner_exports_both_ids_to_a_real_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # T8: B2 and B1 composed, end to end through a real process. The
    # no-id half runs under a stale parent value too (Review Focus 1).
    monkeypatch.setenv("AM_RUN_ID", "stale")
    monkeypatch.setenv("AM_CARD_ID", "stale")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with_ids = tmp_path / "with-ids.json"
    without_ids = tmp_path / "without-ids.json"

    def probe(target: Path) -> str:
        return _py(
            "import json, os, pathlib; "
            f"pathlib.Path({str(target)!r}).write_text(json.dumps("
            "{k: os.environ.get(k) for k in ('AM_RUN_ID', 'AM_CARD_ID')}))"
        )

    first = verify.run_suite(
        [probe(with_ids)], str(worktree), run_id="run-7", card="e2efd21d"
    )
    second = verify.run_suite([probe(without_ids)], str(worktree))

    assert first["passed"] is True and second["passed"] is True
    assert json.loads(with_ids.read_text()) == {
        "AM_RUN_ID": "run-7",
        "AM_CARD_ID": "e2efd21d",
    }
    assert json.loads(without_ids.read_text()) == {
        "AM_RUN_ID": None,
        "AM_CARD_ID": None,
    }
```

(`_logs` is the existing helper at line 856. It returns `(stdout_text, stderr_text)` for a log dir. `_recorder` is the existing strict two-parameter fake at line 459.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -k "ids or overlay or two_parameter or explore_by_name or exports" -v`
Expected: FAIL. The signature test fails on the parameter list. Every call that passes `run_id=`/`card=` fails with `TypeError: run_suite() got an unexpected keyword argument`. `test_no_ids_call_a_strict_two_parameter_runner_exactly_as_before` passes already: it pins the behaviour as it is today.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/verify.py`, replace the `CommandRunner` alias and its docstring (lines 114-119) with:

```python
CommandRunner = Callable[..., CommandResult]
"""Takes an argv and a working directory, returns a `CommandResult`.

Called as `runner(argv, cwd)`, and as `runner(argv, cwd, env=overlay)` when the
walk supplied a run or card id (spec e2efd21d): `overlay` holds only the
present `AM_RUN_ID`/`AM_CARD_ID` keys, never a whole environment, so a runner
that never sees ids may keep a strict two-parameter signature.

Raises `FileNotFoundError` or `PermissionError` if the command cannot be
launched at all; a non-zero exit is a return value, not an exception.
"""
```

Add this helper immediately above `def run_suite(`:

```python
def _id_overlay(run_id: object, card: object) -> dict[str, str]:
    """The `AM_RUN_ID`/`AM_CARD_ID` overlay for the ids that are present.

    `None` and a blank string both mean absent and are left out. Anything
    else that is not a `str` is a caller bug, raised up front like
    `run_suite`'s other `ValueError`s, before a single process starts.
    """
    overlay: dict[str, str] = {}
    for name, key, value in (("run_id", RUN_ID_ENV, run_id), ("card", CARD_ID_ENV, card)):
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(
                f"verify.run_suite needs {name} to be a string, got {value!r}"
            )
        if value.strip() == "":
            continue
        overlay[key] = value
    return overlay
```

Replace the `run_suite` signature and the code from its first line through the runner call with the following. The docstring keeps every existing paragraph and gains the last one.

```python
def run_suite(
    commands: object,
    worktree: object,
    explore: object = None,
    *,
    runner: CommandRunner = run_command,
    log_dir: Path | None = None,
    run_id: str | None = None,
    card: str | None = None,
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

    `run_id` (injected by `walk.run_one_step`) and `card` (bound from the
    table) become `AM_RUN_ID`/`AM_CARD_ID` in every command's environment,
    explore extras included (spec e2efd21d). With neither present -- a direct
    call -- the runner is called as `runner(argv, cwd)`, exactly as before,
    and `run_command` then runs the command with neither variable set.
    """
    planned = [*_plan_commands(commands), *_plan_explore_commands(explore)]
    worktree_path = _required_worktree(worktree)
    overlay = _id_overlay(run_id, card)
    if log_dir is not None:
        _start_logs(Path(log_dir))
    result: dict[str, object] = {"passed": False, "verified": [], "detail": ""}
    verified: list[dict[str, object]] = result["verified"]  # type: ignore[assignment]

    for command, argv in planned:
        try:
            if overlay:
                completed = runner(argv, worktree_path, env=overlay)
            else:
                completed = runner(argv, worktree_path)
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
```

Leave everything after the `except` line unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -v`
Expected: PASS, the whole file. All the pre-existing two-parameter fakes stay unmodified and green (Review Focus 2).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/verify.py tests/steps/test_verify.py
git commit -m "feat: verify.run_suite exports run_id and card to every command (e2efd21d)"
```

---

### Task 3: the walk injects `run_id`; `card` binds from the table

**Files:**
- Modify: `src/agent_manager/runtime/walk.py`: add `RUN_ID_PARAMETER` and `_with_run_id` right after `_with_log_dir` (ends at line 455), and call it in `run_one_step` right after the `_with_log_dir` line (line 483).
- Test: `tests/runtime/test_walk.py` (append at the end), `tests/workflow/test_integrate.py` (append at the end)

**Interfaces:**
- Consumes: `verify.run_suite(..., run_id=None, card=None)` from Task 2. The parameter names are `run_id` and `card`.
- Produces: `walk.RUN_ID_PARAMETER: str = "run_id"` and `walk._with_run_id(fn: Callable[..., Any], kwargs: dict[str, Any], store: Any) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/runtime/test_walk.py`:

```python
# ── the engine-supplied run id (spec e2efd21d, B3) ───────────────────────────
#
# Unit tier: fake steps (and the real verify step with a fake runner via the
# document's `args`), a real temp Store, no spawn.


def _run_id_step(seen: list[object]):
    def run_id_step(run_id=None):
        seen.append(run_id)
        return {}

    return run_id_step


def test_run_one_step_hands_a_declaring_step_the_stores_run_id(store):
    # T9
    seen: list[object] = []

    outcome = _run(store, Step("verify", _run_id_step(seen)))

    assert outcome.ok is True
    assert seen == [RUN_ID]


def test_the_engines_run_id_overrides_document_args_and_the_binding_table(store):
    # T10 / Review Focus 5: `bind_arguments` accepts the args key, since the
    # step declares it, and the engine's value still wins.
    seen: list[object] = []
    step = Step("verify", _run_id_step(seen), args={"run_id": "other"})

    outcome = _run(store, step, table={"run_id": "from-the-table"})

    assert outcome.ok is True
    assert seen == [RUN_ID]


def test_a_store_with_no_run_id_leaves_the_steps_run_id_default():
    # T11
    seen: list[object] = []

    outcome = walk.run_one_step(
        phase=Step("verify", _run_id_step(seen), args={"run_id": "other"}),
        table={"run_id": "from-the-table"},
        store=_RunlessStore(),
        story_id=STORY_ID,
        subtask=_subtask(),
        clock=lambda: FIXED,
    )

    assert outcome.ok is True
    assert seen == [None]


def test_a_step_that_does_not_declare_run_id_is_called_without_it(store):
    # T12
    seen: list[dict[str, object]] = []

    def step(**kwargs):
        seen.append(kwargs)
        return {}

    def plain(card):
        seen.append({"card": card})
        return {}

    first = _run(store, Step("verify", step), table={"run_id": "from-the-table"})
    second = _run(
        store, Step("verify", plain), table={"run_id": "x", "card": CARD_ID}
    )

    assert first.ok is True and second.ok is True
    assert seen == [{}, {"card": CARD_ID}]


def test_the_real_verify_step_hands_its_runner_the_walks_run_and_card_ids(
    store, tmp_path
):
    """Card test (T13): a fake runner sees both variables with the walk's ids."""
    calls: list[tuple[list[str], str, object]] = []

    def runner(argv, cwd, env=None):
        calls.append((argv, cwd, env))
        return verify.CommandResult(exit_code=0, stdout="ok\n", stderr="")

    worktree = tmp_path / "wt"
    worktree.mkdir()
    step = Step(
        "verify",
        verify.run_suite,
        args={"runner": runner},
        gates=(reducers.verification_passed_gate,),
    )

    outcome = _run(
        store,
        step,
        table={
            "commands": ["uv run pytest"],
            "worktree": str(worktree),
            "card": CARD_ID,
        },
    )

    assert outcome.ok is True
    assert set(outcome.result) == {"passed", "verified", "detail"}
    assert calls == [
        (
            ["uv", "run", "pytest"],
            str(worktree),
            {"AM_RUN_ID": RUN_ID, "AM_CARD_ID": CARD_ID},
        )
    ]
```

Append to the end of `tests/workflow/test_integrate.py` (Review Focus 3):

```python
def test_integrates_verify_binds_the_synthetic_subtasks_card_id():
    """Spec e2efd21d B3: the integrate walk's verify step gets `card` from the
    table -- the synthetic subtask's `card_id` -- and exports it as
    `AM_CARD_ID`. `run_id` is not in the table; the engine injects it."""
    phase = INTEGRATE.phase("verify")

    kwargs = walk.bind_arguments(
        phase.run, _context(), phase.args, phase="verify", function="verify.run_suite"
    )

    assert kwargs["card"] == SUBTASK.card_id
    assert "run_id" not in kwargs
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_walk.py tests/workflow/test_integrate.py -k "run_id or run_and_card or synthetic" -v`
Expected: FAIL for T9, T10 and T13. T9 gives `assert [None] == ['run-2026-09-30-01']`. T10 gives `['other']`. T13 gives `env == {'AM_CARD_ID': '4957ac74'}`, with no `AM_RUN_ID`. T11, T12 and the integrate binding test already pass: they pin behaviour the new code must keep.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/runtime/walk.py`, insert immediately after `_with_log_dir` (after its final `return bound`):

```python
RUN_ID_PARAMETER = "run_id"
"""The parameter a deterministic step declares to be handed the store's run id."""


def _with_run_id(
    fn: Callable[..., Any], kwargs: dict[str, Any], store: Any
) -> dict[str, Any]:
    """`kwargs` with the store's run id, for a step that declares `run_id`.

    Spec e2efd21d B3, the `_with_log_dir` rule: only a step whose signature
    names `run_id` gets it, and the engine's value wins over anything the
    binding table or the document's `args` said. A store with no run id
    supplies nothing, and the step's own default applies. Not a reserved
    context key, for the same reason `log_dir` is not: this injection
    already wins.
    """
    if RUN_ID_PARAMETER not in inspect.signature(fn).parameters:
        return kwargs
    bound = {key: value for key, value in kwargs.items() if key != RUN_ID_PARAMETER}
    run_id = getattr(store, "run_id", None)
    if run_id:
        bound[RUN_ID_PARAMETER] = run_id
    return bound
```

In `run_one_step`, right after

```python
        kwargs = _with_log_dir(phase.run, kwargs, store, subtask.card_id, phase.name)
```

add

```python
        kwargs = _with_run_id(phase.run, kwargs, store)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_walk.py tests/workflow/test_integrate.py tests/test_engine.py -v`
Expected: PASS, every test in all three files.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/walk.py tests/runtime/test_walk.py tests/workflow/test_integrate.py
git commit -m "feat: run_one_step injects the store's run_id into a declaring step (e2efd21d)"
```

---

### Task 4: README names the variables

**Files:**
- Modify: `README.md`: insert a paragraph after the one that ends "`--repo-dir` defaults to `.` and `--base-branch` to `master`." (line 73).

**Interfaces:**
- Consumes: the behaviour from Tasks 1-3.
- Produces: nothing in code.

This change is documentation only. There is no test to write, because nothing in the suite reads the README.

- [ ] **Step 1: Edit the README**

Right after the line

```
`master`.
```

that closes the `--branch-prefix` / `--verify` paragraph, insert a blank line and then:

```markdown
Each `--verify` command runs with `AM_RUN_ID` (the run's id) and
`AM_CARD_ID` (the card being verified) added to its environment.
The base-branch and final integration checks run their commands with neither set.
```

The blank line before the next paragraph ("Some combinations are refused…") stays.

- [ ] **Step 2: Check the placement**

Run: `grep -n -B2 -A4 "AM_RUN_ID" README.md`
Expected: the three new lines appear between "`master`." and "Some combinations are refused", each separated from its neighbours by one blank line.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README names AM_RUN_ID and AM_CARD_ID for --verify commands (e2efd21d)"
```

---

### Task 5: Full verification

**Files:** none modified.

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest`
Expected: PASS (the `unit` + `git` tiers).

- [ ] **Step 2: Run the e2e_fake tier (Review Focus 3: the integrate workflow's verify step)**

Run: `uv run pytest -m e2e_fake`
Expected: PASS. The existing integrate scenarios stay green with `card` and `run_id` now reaching `run_suite`.

- [ ] **Step 3: Confirm the direct callers were not touched**

Run: `git diff master --stat -- src/agent_manager/bases.py src/agent_manager/integration.py tests/conftest.py`
Expected: no output.

If any step fails, fix the cause in the task that owns it and commit that fix there. Do not commit anything for this task.
<!-- task-pipeline: validated -->
