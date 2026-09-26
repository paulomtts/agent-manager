<!-- task-pipeline: validated -->
# Verify also runs Explore's typecheck and lint (cf8b3888)

Subtask of story be007353 "Close the task.js gaps". Narrows pygents-engine design G9 item 4 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:361-363`, also listed under §9 Tests at line 398) and plan Task 2.4 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:570-610`) to one function.

## Scope

Owns only `src/agent_manager/steps/verify.py` (`run_suite`) and `tests/steps/test_verify.py`. Does not touch `steps/reducers.py`, role bundles, `workflow/builtin/task.yaml` / `integrate.yaml`, `workflow/registry.py`, or anything under `runtime/`. `verify.py` must not import pygents.

No workflow edit: `verify`'s phase in `task.yaml` declares no `inputs:` at all — deterministic phases don't need to, since `engine._run_deterministic` (`engine.py:487-499`) calls `bind_arguments` with the whole running context, not a phase's declared `inputs:`. `bind_arguments` (`engine.py:163-198`) binds parameters by name from that context and falls back to a parameter's default when the name is absent; `_bind_result` (`engine.py:460-470`) already writes every earlier phase's result into the context under its own phase name, so the context handed to `verify` already holds a key literally named `explore`. Declaring an `explore` parameter makes the engine hand `run_suite` the Explore phase's dumped result automatically (same pattern as `plan_hash_gate_adapter`, `steps/reducers.py:447-469`; registered unchanged at `workflow/registry.py:243`).

## Interface

`run_suite(commands, worktree, explore: object = None, *, runner: CommandRunner = run_command) -> dict[str, object]`

`explore` is positional-or-keyword and goes before the `*`. `runner` stays keyword-only.

## Observable behaviour

- The run order is: every entry of `commands`, then Explore's `verification.typecheck` if it is a non-blank string, then each entry of `verification.lint` in list order. Design wording: "Verify runs `--verify` commands, then Explore's `typecheck` (when non-empty) and each `lint` command, in that order; any non-zero exit fails `verification_passed_gate` as today."
- `explore` is read tolerantly with a local `_field`-style accessor in the style of `reducers._field` / `dag._field`. If `explore` is not a mapping, has no `verification` mapping, or is `None`, no extra commands run. `typecheck` and `lint` have no alias (`results.Verification`, `results.py:47-51`: only `full_suite` carries `serialization_alias="fullSuite"`), so the snake_case model dump the engine actually hands `run_suite` (`Verdict.result = validated.model_dump(mode="json")`, `dispatch.py:255`, no `by_alias=True`) and the camelCase hand-written fixture in this file's tests both use the same keys for `typecheck`/`lint`. `fullSuite`/`full_suite` inside `explore` is ignored, because `commands` is still the source of the suite.
- Extra commands go through the existing pipeline unchanged. `_argv_for` / `shlex.split` is used and there is never a shell (`shell=False`, module docstring lines 1-16). Blank entries are skipped. Malformed entries raise `ValueError` up front, before any process starts — the suite's commands, the typecheck command and every lint command are all planned into argvs before any of them runs, the same as a bad `commands` entry today. Each extra command gets its own `verified` entry (`command`/`ok`/`tail`) in the same form as the others.
- The failure path is the same as for a red `--verify` command (`verify.py:258-271`). A non-zero extra command appends an `ok: False` entry with the diagnostic tail and sets `detail` to `"verification failed: <cmd> — <diagnostic>"`. It also leaves `passed` False and stops, so no later command runs. A launch failure (`FileNotFoundError`/`PermissionError`/`NotADirectoryError`) raises `VerifyError` as it does today.
- Backward compatibility: when no `explore` is given, or when it has an empty typecheck and empty lint, the behaviour and result shape are identical to today. That includes the exact `{"passed": True, "verified": [], "detail": ""}` for an empty `commands`. The result keys (`passed`/`verified`/`detail`) do not change. No G10-protected shape (SubtaskSummary, journal lines, phase/attempt rows, escalation payloads) is altered.

## Tests

All tests go in `tests/steps/test_verify.py` and belong to the **Steps** tier (agent-manager design §14, `2026-09-23-agent-manager-design.md:497-513`: "against temporary git repositories and a temporary `brd` board; no network"; file docstring lines 1-14). Use real `_py(...)` subprocesses in `tmp_path` where a real process can produce the outcome. Use the injected `runner` (e.g. `_recorder()`, which receives `argv: list[str], cwd: str`) only where a test has to observe or force the ordering. The plan's fixtures compare command strings, so adapt them to argv lists.

1. **Typecheck and lint run after the suite, in order** (Steps, recorder runner): `explore = {"verification": {"fullSuite": [...], "typecheck": "uv run mypy", "lint": ["uv run ruff check"]}}`. The recorded argvs are the suite, then `["uv","run","mypy"]`, then `["uv","run","ruff","check"]`, and `passed` is True.
2. **No explore means only the suite** (Steps, recorder runner): calling without `explore` runs only `commands`.
3. **An empty typecheck is skipped** (Steps, recorder runner): `{"verification": {"typecheck": "", "lint": []}}` runs only `commands`.
4. **A failing extra command fails the suite like a red `--verify` command** (Steps, real `_py` subprocesses): the suite is green, typecheck is `_py("raise SystemExit(3)")` (or it writes to stderr and exits non-zero), and lint holds a command that would leave a marker file. Check that `passed` is False, the last `verified` entry is `ok: False` with the diagnostic, and `detail` starts with `"verification failed:"`. The lint marker must be absent, which shows the short-circuit.
5. **Malformed explore input is tolerated or rejected consistently** (Steps, recorder runner): if `explore` is a non-mapping or has no `verification`, only the suite runs. A lint entry of the wrong type (e.g. `7`) raises `ValueError` before any command runs, and the recorder sees no calls.

Existing tests (including the pass path at 122-156 and the red / short-circuit path at 184-223) must stay green unchanged. Verification: `uv run pytest` (whole default suite, including `tests/e2e`, on both engines while `--engine` exists). There is no typecheck or lint command for this repo.

---

# Verify Also Runs Explore's Typecheck and Lint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `verify.run_suite` runs Explore's `verification.typecheck` (when non-blank) and each `verification.lint` command after the `--verify` commands, failing the suite on any red one exactly as a red `--verify` command does.

**Architecture:** One new optional parameter `explore` on `run_suite`, placed before the keyword-only `*`. A module-local `_field` accessor (a copy of `reducers._field`) reads `explore["verification"]` tolerantly; a new `_plan_explore_commands` turns typecheck and lint into `(command, argv)` pairs through the existing `_argv_for` / `_plan_commands`, and `run_suite` concatenates them after the suite's own planned commands before any process starts. The existing run/record/short-circuit loop is reused unchanged, so the failure path, `verified` entry shape and `VerifyError` behaviour are identical for the extra commands. The engine binds `explore` by parameter name from the running context (`engine.bind_arguments`, `src/agent_manager/engine.py:163-198`), so no YAML, registry or reducer change is needed; the integrate workflow has no `explore` phase and falls back to the default `None`.

**Tech Stack:** Python 3, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-verify-also-runs-cf8b3888/docs/superpowers/specs/task-verify-also-runs-cf8b3888-design.md` (reproduced verbatim above).

**Working directory for every command:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-verify-also-runs-cf8b3888` (branch `m6/task-verify-also-runs-cf8b3888`). Do not assume any other subtask's code beyond what is already on this branch.

## Global Constraints

- Only `src/agent_manager/steps/verify.py` and `tests/steps/test_verify.py` change. No edit to `steps/reducers.py`, role bundles, `workflow/builtin/task.yaml`, `workflow/builtin/integrate.yaml`, `workflow/registry.py`, or anything under `runtime/`.
- `verify.py` must not import pygents.
- Signature is exactly `run_suite(commands, worktree, explore: object = None, *, runner: CommandRunner = run_command) -> dict[str, object]`; the parameter must be named `explore` (the engine binds by name) and `runner` stays keyword-only.
- Never a shell: every command goes through `_argv_for` (`shlex.split`) and `subprocess.run` with `shell=False`. The module docstring (lines 1-17) is not changed.
- Run order: `commands`, then `verification.typecheck` if non-blank, then each `verification.lint` entry in list order. `fullSuite` / `full_suite` inside `explore` is ignored.
- A red extra command: `ok: False` entry with the diagnostic tail, `detail` = `"verification failed: <cmd> — <diagnostic>"`, `passed` False, stop. A launch failure raises `VerifyError`.
- Malformed entries raise `ValueError` before any process starts.
- With no `explore`, or empty typecheck and empty lint, the result is identical to today, including `{"passed": True, "verified": [], "detail": ""}` for empty `commands`.
- Tests live in `tests/steps/test_verify.py` (Steps tier, design §14): real `_py(...)` subprocesses in `tmp_path` where a real process can produce the outcome; the `_recorder()` fake runner only to observe ordering or force an outcome; compare argv lists, not command strings.
- Existing tests in `tests/steps/test_verify.py` stay green unchanged. Verification: `uv run pytest` (whole suite, including `tests/e2e`).

## Review Focus

1. The shape the engine really binds is the snake_case model dump (`{"verification": {"full_suite": [...], "typecheck": ..., "lint": [...]}}`), not the camelCase fixture; it must be read the same, and `full_suite` must not be run a second time.
2. A whitespace-only typecheck (`"   "`) and blank lint entries (`""`, `"  "`) are "empty" to a person reading the card and must be skipped, not launched or rejected.
3. `lint` given as a bare string (`"uv run ruff check"`) instead of a list must raise `ValueError` up front, never be iterated character by character into one-letter commands.
4. A red `--verify` command must stop the run before typecheck and lint start (the short-circuit covers the extra commands too), and a red lint after a green typecheck must name the lint command in `detail`.
5. A typecheck binary that cannot be launched must raise `VerifyError` (misconfigured card), not read as `passed: False`.

Each line above has a test in Task 1.

---

### Task 1: `run_suite` runs Explore's typecheck and lint after the suite

**Files:**
- Modify: `src/agent_manager/steps/verify.py:22` (import), `src/agent_manager/steps/verify.py:187-200` (add helpers after `_plan_commands`), `src/agent_manager/steps/verify.py:222-274` (`run_suite`)
- Test: `tests/steps/test_verify.py` (imports at lines 16-21; append new tests at the end of the file, after line 354)

**Interfaces:**
- Consumes: existing `verify._argv_for(command: object) -> list[str] | None`, `verify._plan_commands(commands: object) -> list[tuple[object, list[str]]]`, `verify.CommandResult`, `verify.VerifyError`, and the test helpers `_py(script: str) -> str` and `_recorder() -> tuple[list[tuple[list[str], str]], verify.CommandRunner]` already in `tests/steps/test_verify.py`.
- Produces: `verify.run_suite(commands: object, worktree: object, explore: object = None, *, runner: CommandRunner = run_command) -> dict[str, object]`; module-private `verify._field(mapping: object, name: str) -> object` and `verify._plan_explore_commands(explore: object) -> list[tuple[object, list[str]]]`.

- [ ] **Step 1: Add the `inspect` import to the test file**

In `tests/steps/test_verify.py`, replace:

```python
import os
import shlex
```

with:

```python
import inspect
import os
import shlex
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/steps/test_verify.py`:

```python
# --- Explore's typecheck and lint (card cf8b3888, pygents design G9 item 4) ---
#
# The engine binds `explore` by parameter name from the running context, and
# hands `run_suite` the Explore phase's dumped `ExploreResult`. Recorder tests
# observe ordering; the failure path is proved against real processes.


def test_run_suite_takes_explore_by_name_before_the_keyword_only_runner():
    # `engine.bind_arguments` binds strictly by parameter name, so the name
    # `explore` is what wires the Explore phase's result in -- no YAML edit.
    parameters = inspect.signature(verify.run_suite).parameters
    assert list(parameters) == ["commands", "worktree", "explore", "runner"]
    assert parameters["explore"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["explore"].default is None
    assert parameters["runner"].kind is inspect.Parameter.KEYWORD_ONLY


def test_typecheck_and_lint_run_after_the_suite_in_order(tmp_path: Path):
    calls, runner = _recorder()
    explore = {
        "verification": {
            "fullSuite": ["uv run pytest"],
            "typecheck": "uv run mypy",
            "lint": ["uv run ruff check"],
        }
    }
    result = verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "mypy"],
        ["uv", "run", "ruff", "check"],
    ]
    assert all(cwd == str(tmp_path) for _, cwd in calls)
    assert result["passed"] is True
    assert result["detail"] == ""
    assert result["verified"] == [
        {"command": "uv run pytest", "ok": True, "tail": "fine"},
        {"command": "uv run mypy", "ok": True, "tail": "fine"},
        {"command": "uv run ruff check", "ok": True, "tail": "fine"},
    ]


def test_every_lint_command_runs_in_list_order(tmp_path: Path):
    calls, runner = _recorder()
    explore = {
        "verification": {
            "typecheck": "uv run mypy",
            "lint": ["uv run ruff check", "uv run ruff format --check"],
        }
    }
    verify.run_suite(["uv run pytest"], str(tmp_path), explore=explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "mypy"],
        ["uv", "run", "ruff", "check"],
        ["uv", "run", "ruff", "format", "--check"],
    ]


def test_the_snake_case_dump_the_engine_binds_is_read_and_full_suite_is_ignored(
    tmp_path: Path,
):
    # `dispatch.py` dumps `ExploreResult` without `by_alias=True`, so the real
    # bound value says `full_suite`. `commands` stays the suite's only source.
    calls, runner = _recorder()
    explore = {
        "verification": {
            "full_suite": ["uv run pytest", "uv run pytest tests/e2e"],
            "typecheck": "uv run mypy",
            "lint": ["uv run ruff check"],
        }
    }
    verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "mypy"],
        ["uv", "run", "ruff", "check"],
    ]


def test_no_explore_means_only_the_suite(tmp_path: Path):
    calls, runner = _recorder()
    result = verify.run_suite(["uv run pytest"], str(tmp_path), runner=runner)
    assert [argv for argv, _ in calls] == [["uv", "run", "pytest"]]
    assert result["passed"] is True


def test_an_empty_typecheck_and_empty_lint_run_only_the_suite(tmp_path: Path):
    calls, runner = _recorder()
    explore = {"verification": {"typecheck": "", "lint": []}}
    result = verify.run_suite(["x"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [["x"]]
    assert result["passed"] is True
    # The empty-suite shape is unchanged when Explore named nothing either.
    assert verify.run_suite([], str(tmp_path), explore, runner=runner) == {
        "passed": True,
        "verified": [],
        "detail": "",
    }


def test_a_blank_typecheck_and_blank_lint_entries_are_skipped(tmp_path: Path):
    calls, runner = _recorder()
    explore = {"verification": {"typecheck": "   ", "lint": ["", "  ", "uv run ruff check"]}}
    result = verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [
        ["uv", "run", "pytest"],
        ["uv", "run", "ruff", "check"],
    ]
    assert [entry["command"] for entry in result["verified"]] == [
        "uv run pytest",
        "uv run ruff check",
    ]


@pytest.mark.parametrize(
    "explore",
    [
        None,
        "nonsense",
        7,
        [],
        {},
        {"other": {"typecheck": "uv run mypy"}},
        {"verification": None},
        {"verification": "uv run mypy"},
        {"verification": ["uv run mypy"]},
        {"verification": {}},
        {"verification": {"typecheck": None, "lint": None}},
    ],
)
def test_a_malformed_or_absent_verification_runs_only_the_suite(
    tmp_path: Path, explore: object
):
    calls, runner = _recorder()
    result = verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert [argv for argv, _ in calls] == [["uv", "run", "pytest"]]
    assert result["passed"] is True


@pytest.mark.parametrize(
    "verification",
    [
        {"typecheck": "", "lint": [7]},
        {"typecheck": "", "lint": ["uv run ruff check", ["ruff", 7]]},
        {"typecheck": 7, "lint": []},
        {"typecheck": "", "lint": 7},
        # A bare string is not a list of commands: never split it into letters.
        {"typecheck": "", "lint": "uv run ruff check"},
        {"typecheck": "uv run 'mypy", "lint": []},
    ],
)
def test_a_wrong_type_explore_entry_raises_before_anything_runs(
    tmp_path: Path, verification: dict[str, object]
):
    calls, runner = _recorder()
    with pytest.raises(ValueError):
        verify.run_suite(
            ["uv run pytest"],
            str(tmp_path),
            {"verification": verification},
            runner=runner,
        )
    assert calls == []


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
        {"command": typecheck, "ok": False, "tail": "error: 2 type errors"},
    ]
    assert result["detail"] == f"verification failed: {typecheck} — error: 2 type errors"
    assert not marker.exists()


def test_a_silent_failing_typecheck_still_carries_a_tail_and_a_detail(tmp_path: Path):
    typecheck = _py("raise SystemExit(3)")
    explore = {"verification": {"typecheck": typecheck, "lint": []}}
    result = verify.run_suite([_py("print('ok')")], str(tmp_path), explore)
    assert result["passed"] is False
    assert result["verified"][-1] == {
        "command": typecheck,
        "ok": False,
        "tail": f"{typecheck} exited with code 3",
    }
    assert result["detail"].startswith("verification failed:")
    assert "exited with code 3" in result["detail"]


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
    assert result["verified"][-1]["tail"] == "E501 line too long"
    assert result["detail"] == f"verification failed: {first_lint} — E501 line too long"
    assert not marker.exists()


def test_a_red_suite_command_stops_before_typecheck_runs(tmp_path: Path):
    marker = tmp_path / "typecheck-ran.txt"
    red = _py("raise SystemExit(1)")
    typecheck = _py(f"open({str(marker)!r}, 'w').write('ran')")
    explore = {"verification": {"typecheck": typecheck, "lint": []}}

    result = verify.run_suite([red], str(tmp_path), explore)

    assert result["passed"] is False
    assert [entry["command"] for entry in result["verified"]] == [red]
    assert not marker.exists()


def test_an_unlaunchable_typecheck_raises_verify_error(tmp_path: Path):
    # Like a missing `--verify` binary: a misconfigured card, not a red suite.
    ran: list[list[str]] = []

    def runner(argv: list[str], cwd: str) -> CommandResult:
        ran.append(argv)
        if argv[0] == "definitely-not-mypy":
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        return CommandResult(exit_code=0, stdout="fine\n", stderr="")

    explore = {"verification": {"typecheck": "definitely-not-mypy .", "lint": []}}
    with pytest.raises(VerifyError) as excinfo:
        verify.run_suite(["uv run pytest"], str(tmp_path), explore, runner=runner)
    assert "definitely-not-mypy" in str(excinfo.value)
    assert ran == [["uv", "run", "pytest"], ["definitely-not-mypy", "."]]
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/steps/test_verify.py -v`

Expected: `test_run_suite_takes_explore_by_name_before_the_keyword_only_runner` FAILS on the `list(parameters)` assertion (`['commands', 'worktree', 'runner']`); every other new test that passes `explore` FAILS with `TypeError: run_suite() takes 2 positional arguments but 3 were given` or `TypeError: run_suite() got an unexpected keyword argument 'explore'`. `test_no_explore_means_only_the_suite` PASSES already (it is the backward-compatibility guard and never passes `explore`). Every pre-existing test PASSES.

- [ ] **Step 4: Import `Mapping` in `verify.py`**

In `src/agent_manager/steps/verify.py`, replace:

```python
from collections.abc import Callable, Iterable, Sequence
```

with:

```python
from collections.abc import Callable, Iterable, Mapping, Sequence
```

- [ ] **Step 5: Add the tolerant accessor and the Explore planner**

In `src/agent_manager/steps/verify.py`, insert directly after the end of `_plan_commands` (after its `return planned` line, before `def _required_worktree`):

```python
def _field(mapping: object, name: str) -> object:
    """Read ``name`` off a mapping, or ``None`` if it is not a mapping at all.

    The same tolerant read as `reducers._field`: `explore` arrives as whatever
    the Explore phase's result was bound to, and a missing or odd-shaped value
    means "Explore named nothing extra", not a crash.
    """
    return mapping.get(name) if isinstance(mapping, Mapping) else None


def _plan_explore_commands(explore: object) -> list[tuple[object, list[str]]]:
    """Explore's `verification.typecheck` then each `verification.lint` entry.

    Pygents design G9 item 4: these run after the `--verify` commands, in that
    order. `fullSuite`/`full_suite` is deliberately not read -- `commands` is
    the suite's only source. A blank typecheck or blank lint entry is skipped;
    a wrong-typed one raises `ValueError` here, before any process starts,
    exactly like a malformed `--verify` command. `typecheck` and `lint` carry
    no alias in `results.Verification`, so the engine's snake_case dump and a
    hand-written camelCase result use the same two keys.
    """
    verification = _field(explore, "verification")
    planned: list[tuple[object, list[str]]] = []

    typecheck = _field(verification, "typecheck")
    if typecheck is not None:
        argv = _argv_for(typecheck)
        if argv is not None:
            planned.append((typecheck, argv))

    lint = _field(verification, "lint")
    if lint is not None:
        planned.extend(_plan_commands(lint))

    return planned
```

- [ ] **Step 6: Extend `run_suite`'s signature and planning**

In `src/agent_manager/steps/verify.py`, replace:

```python
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
    planned = _plan_commands(commands)
    worktree_path = _required_worktree(worktree)
```

with:

```python
def run_suite(
    commands: object,
    worktree: object,
    explore: object = None,
    *,
    runner: CommandRunner = run_command,
) -> dict[str, object]:
    """Run each verification command in `worktree` and report what happened.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    `runner` defaults to real execution and exists to be swapped in tests, the
    same callable-injection seam `worktree.py` uses for git.

    `explore` is the Explore phase's result, bound by parameter name by
    `engine.bind_arguments` (no workflow edit needed; the integrate workflow
    has no such phase and gets `None`). Its `verification.typecheck` and each
    `verification.lint` command run after `commands` and are reported, and fail
    the suite, exactly like `--verify` commands (pygents design G9 item 4).
    Every command, extra or not, is planned before the first one runs.
    """
    planned = [*_plan_commands(commands), *_plan_explore_commands(explore)]
    worktree_path = _required_worktree(worktree)
```

The loop below (`for command, argv in planned:` through `return result`) stays exactly as it is.

- [ ] **Step 7: Run the verify tests to verify they pass**

Run: `uv run pytest tests/steps/test_verify.py -v`

Expected: every test in the file PASSES, old and new.

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest`

Expected: all tests PASS (including `tests/e2e`, `tests/test_engine.py`, `tests/workflow/test_registry.py`, `tests/workflow/test_builtin_integrate.py`), with no new failures or errors.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/steps/verify.py tests/steps/test_verify.py
git commit -m "feat(verify): also run Explore's typecheck and lint"
```
