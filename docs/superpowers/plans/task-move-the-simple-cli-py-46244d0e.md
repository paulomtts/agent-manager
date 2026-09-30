<!-- task-pipeline: validated -->
# Move the simple cli.py helpers into runs.py — subtask design (46244d0e)

Parent story: 4bc0a3e0 "cli.py sheds its collaborator role" (milestone 9c44c2fb). Spec of record: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, decision S1 and its "`default_runner_factory` exemption" paragraph. This document narrows S1 to its first step; it adds no new design.

## Scope

Create `src/agent_manager/runs.py`, a plain module with no `typer` import, and move these five definitions into it verbatim (same signatures, bodies, and docstrings):

- `resolve_repo_dir` (cli.py:166)
- `mint_run_id` (cli.py:181)
- `worktree_for` (cli.py:191)
- `RunnerFactory` Protocol (cli.py:618)
- `gate_context` (cli.py:660)

Required supporting definitions. The five names reference module-level definitions in `cli.py`. `runs.py` must not import `cli`: `cli` will import `runs` at top level, so a `runs -> cli` import would be circular, and S1 says nothing downstream of `runs` imports `cli`. So the definitions they depend on move with them, also verbatim:

- `RUN_ID_TIME_FORMAT` (cli.py:55), used by `mint_run_id`
- `WORKTREE_PARTS` (cli.py:58), used by `worktree_for`
- `CliError` (cli.py:63) and `RepoDirError` (cli.py:67), raised by `resolve_repo_dir`. `CliError` has to come too because `RepoDirError` subclasses it, and sibling 1c21b3dd will need it in `runs.py` for the run-error types it moves there.

`runs.py` imports only what those bodies need (`dag`, `Path`, `datetime`, `Sequence`, `Any`, `Protocol`, `Store`, `AgentPhaseRunner`) and does not import `cli`, `typer`, or `harness.launcher`.

In `cli.py`, delete the moved definitions and replace them with a single `from agent_manager.runs import ...` that re-exports every moved name: the five helpers plus the four supporting names. After this, `cli.X is runs.X` for every moved `X`. That keeps these working unchanged:

- attribute access such as `cli.worktree_for(...)`, `cli.RepoDirError`, and `cli.CliError`
- the `HANDLED` tuple
- the remaining `CliError` subclasses defined in `cli.py`
- `except CliError`
- bare-name calls inside `cli.py`

`default_runner_factory` (cli.py:637) stays in `cli.py` unchanged. Its body resolves the bare name `run_direct` (imported at cli.py:44) from `cli`'s globals at call time. `tests/e2e/test_live_control.py:179`, `tests/e2e/test_milestone_run.py:294,421` and `tests/e2e/test_milestone_resume.py:241` intercept it with `monkeypatch.setattr(cli, "run_direct", ...)`. The `run_direct` import at cli.py:44 also stays.

## Out of scope

- Repointing any caller: `bases.py`, `integration.py`, `orchestrate.py` and all tests keep importing `cli`. That is 61a0d9be's job.
- Deleting the deferred imports at cli.py:895,1137,1460, dropping re-exports, or adding the import-graph test (also 61a0d9be).
- Moving `orphan_attempts`, `continuable_checkpoint`, `select_resumable`, `UnknownRunError`, `NotResumableError` or `CheckpointMismatchError` (that is 1c21b3dd). Moving any other `CliError` subclass.
- Touching `dry_run_payload` (that is 64babfa2).
- The S7 error-hierarchy merge into `errors.py`.
- From both cards: pygents' turn/phase model, checkpoint format, the harness adapter contract, adding a typecheck/CI gate, rewriting the ~261 existing monkeypatch calls, and replacing grafo.

## Observable behavior and error paths

Nothing observable changes. Every CLI command gives the same JSON/`--pretty` envelopes, exit codes and run ids as before. `resolve_repo_dir` still raises `RepoDirError` with the same message for a non-directory. `isinstance`/`issubclass` checks against `cli.CliError`/`cli.RepoDirError` still hold because the classes are the same objects. The only side effect of importing `runs` is that its own definitions exist.

## Tests

No test file changes. The acceptance check is `uv run pytest` green on the unmodified suite. These existing tests are the evidence:

- **Unit tier** (`tests/test_cli.py`, e.g. :117 `pytest.raises(cli.RepoDirError)`, :389 `issubclass(cli.NotResumableError, cli.CliError)`; `tests/workflow/test_task.py:429` `cli.gate_context`). These are pure-function and re-export checks.
- **Step/engine tier with temp git repos and a temp board** (`tests/test_orchestrate.py`, `tests/test_bases.py`, `tests/test_integration.py`, which call `cli.worktree_for`, `cli.mint_run_id`, `cli.resolve_repo_dir` and `cli.RunnerFactory`). These show the re-exports behave identically.
- **Default-suite e2e tier, fake harness** (`tests/e2e/test_live_control.py:179`, `tests/e2e/test_milestone_run.py:294,421`, `tests/e2e/test_milestone_resume.py:241`). These show `default_runner_factory` still picks up the patched `cli.run_direct`.
- The opt-in real-harness e2e tests (`tests/e2e/test_real_harness_*.py`) are outside the default suite and are not part of acceptance.

No new test is required. Under design spec §14, a new test would be a pure-function unit test placed as `tests/test_runs.py`, not an e2e test. Asserting `cli.<name> is runs.<name>` there is optional, and the import-graph test is 61a0d9be's, not this card's.

---

# Move the simple cli.py helpers into runs.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `src/agent_manager/runs.py` (no Typer) holding `resolve_repo_dir`, `mint_run_id`, `worktree_for`, `RunnerFactory`, `gate_context` and their four supporting definitions, moved verbatim out of `cli.py`, which re-exports all nine names so `cli.X is runs.X`.

**Architecture:** Two tasks. Task 1 creates `runs.py` as a verbatim copy of the nine definitions and pins its behavior and its "no typer, no cli" import rule with a new pure-function unit test file `tests/test_runs.py`. Task 2 deletes the originals from `cli.py` and replaces them with one `from agent_manager.runs import (...)` re-export, pinned by an identity test (`cli.X is runs.X`), then runs the whole unmodified suite. `default_runner_factory` and the `run_direct` import are not touched.

**Tech Stack:** Python, `uv`, `pytest`, Typer (only in `cli.py`).

**Spec:** `docs/superpowers/specs/task-move-the-simple-cli-py-46244d0e-design.md` (prepended above); spec of record `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` decision S1.

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-move-the-simple-cli-py-46244d0e` on branch `m13/task-move-the-simple-cli-py-46244d0e`. Nothing from sibling cards (1c21b3dd, 64babfa2, 61a0d9be) exists on this branch.

## Global Constraints

- Moves are verbatim: same signatures, bodies and docstrings; no behavior change.
- `src/agent_manager/runs.py` imports neither `typer`, `agent_manager.cli`, nor `agent_manager.harness.launcher`.
- `cli.py` re-exports all nine moved names: `resolve_repo_dir`, `mint_run_id`, `worktree_for`, `RunnerFactory`, `gate_context`, `RUN_ID_TIME_FORMAT`, `WORKTREE_PARTS`, `CliError`, `RepoDirError`.
- `default_runner_factory` (cli.py:637) and `from agent_manager.harness.launcher import run_direct` (cli.py:44) stay in `cli.py` unchanged.
- No existing test file is modified. No caller (`bases.py`, `integration.py`, `orchestrate.py`) is repointed. The deferred imports inside `cli.py` are not touched. `dry_run_payload` is not touched. No other `CliError` subclass moves.
- Verification: `uv run pytest` (no lint/typecheck command exists).

## Review Focus

1. `default_runner_factory` must still pick up a `monkeypatch.setattr(cli, "run_direct", ...)` patch — pinned by the existing unmodified e2e tests `tests/e2e/test_live_control.py`, `tests/e2e/test_milestone_run.py`, `tests/e2e/test_milestone_resume.py` run in Task 2 Step 6; the plan never edits that function.
2. `cli.CliError` subclasses (`UnknownRunError`, `CheckpointMismatchError`, ...) must still be subclasses of the same `CliError` object that `HANDLED` / `except CliError` see — pinned in Task 2 by `test_cli_error_subclasses_share_the_moved_base`.
3. Importing `agent_manager.runs` in a fresh interpreter must not drag in `typer` or `agent_manager.cli` (a hidden circular import would surface only at 61a0d9be) — pinned in Task 1 by `test_importing_runs_loads_neither_typer_nor_cli`.
4. A non-directory `--repo-dir` (a missing path or a regular file) must raise `RepoDirError` with the exact existing message, and a relative directory must resolve to an absolute path — pinned in Task 1 by `test_resolve_repo_dir_*`.
5. A non-UUID card id must still be refused by `mint_run_id` with `ValueError` rather than produce a run id; a truthy non-bool `allow_no_verification` must become exactly `True` — pinned in Task 1 by `test_mint_run_id_refuses_a_non_uuid_card_id` and `test_gate_context_coerces_allow_no_verification_to_bool`.

---

### Task 1: Create `runs.py` with the nine moved definitions

**Files:**
- Create: `src/agent_manager/runs.py`
- Test: `tests/test_runs.py` (unit tier; top-level `tests/test_<module>.py` mirrors `src/agent_manager/<module>.py`, like `tests/test_dag.py`, `tests/test_cli.py`)

**Interfaces:**
- Consumes: `agent_manager.dag.short_id(card_id: object) -> str` (raises `ValueError` for a non-UUID); `agent_manager.store.Store`; `agent_manager.runtime.walk.AgentPhaseRunner`.
- Produces (module `agent_manager.runs`):
  - `RUN_ID_TIME_FORMAT: str = "%Y%m%dT%H%M%SZ"`
  - `WORKTREE_PARTS: tuple[str, str] = (".claude", "worktrees")`
  - `class CliError(RuntimeError)`, `class RepoDirError(CliError)`
  - `resolve_repo_dir(repo_dir: Path) -> Path`
  - `mint_run_id(card_id: str, now: datetime) -> str`
  - `worktree_for(repo_dir: Path, branch: str) -> Path`
  - `class RunnerFactory(Protocol)` with `__call__(self, *, store: Store, run_id: str, story_id: str, card_id: str) -> AgentPhaseRunner`
  - `gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_runs.py`:

```python
"""Unit tests for `runs`, the Typer-free home of the run helpers (S1, card 46244d0e).

Pure functions only (design §14): no clock beyond a fixed `datetime`, no
filesystem beyond `tmp_path`, no subprocess except the one fresh-interpreter
import check.
"""

import ast
import inspect
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import runs

CARD_ID = "46244d0e-1111-2222-3333-444455556666"


def test_run_id_time_format_and_worktree_parts_keep_their_values():
    assert runs.RUN_ID_TIME_FORMAT == "%Y%m%dT%H%M%SZ"
    assert runs.WORKTREE_PARTS == (".claude", "worktrees")


def test_repo_dir_error_is_a_cli_error_is_a_runtime_error():
    assert issubclass(runs.RepoDirError, runs.CliError)
    assert issubclass(runs.CliError, RuntimeError)


def test_resolve_repo_dir_returns_the_absolute_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "repo").mkdir()
    resolved = runs.resolve_repo_dir(Path("repo"))
    assert resolved == (tmp_path / "repo").resolve()
    assert resolved.is_absolute()


def test_resolve_repo_dir_refuses_a_missing_directory(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(runs.RepoDirError) as excinfo:
        runs.resolve_repo_dir(missing)
    assert str(excinfo.value) == (
        f"--repo-dir {str(missing)!r} is not a directory (resolved to {missing.resolve()})"
    )


def test_resolve_repo_dir_refuses_a_file(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")
    with pytest.raises(runs.RepoDirError):
        runs.resolve_repo_dir(a_file)


def test_mint_run_id_is_utc_timestamp_dash_short_id():
    now = datetime(2026, 9, 30, 12, 34, 56, tzinfo=timezone.utc)
    assert runs.mint_run_id(CARD_ID, now) == "20260930T123456Z-46244d0e"


def test_mint_run_id_refuses_a_non_uuid_card_id():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        runs.mint_run_id("not-a-card", now)


def test_worktree_for_splits_the_branch_into_path_segments(tmp_path):
    path = runs.worktree_for(tmp_path, "m13/task-x")
    assert path == tmp_path.resolve() / ".claude" / "worktrees" / "m13" / "task-x"
    assert path.is_absolute()


def test_gate_context_shape():
    assert runs.gate_context(["uv run pytest"], False) == {
        "suite_cmds": ["uv run pytest"],
        "allow_no_verification": False,
        "caller_provided": False,
        "provided_verification": None,
    }


def test_gate_context_coerces_allow_no_verification_to_bool():
    ctx = runs.gate_context(("a", "b"), 1)
    assert ctx["allow_no_verification"] is True
    assert ctx["suite_cmds"] == ["a", "b"]


def test_runner_factory_is_a_keyword_only_protocol():
    params = inspect.signature(runs.RunnerFactory.__call__).parameters
    assert list(params) == ["self", "store", "run_id", "story_id", "card_id"]
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY
        for name, p in params.items()
        if name != "self"
    )


def test_runs_source_imports_neither_typer_nor_cli_nor_launcher():
    tree = ast.parse(Path(runs.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    forbidden = {"typer", "agent_manager.cli", "agent_manager.harness.launcher"}
    assert not {name for name in imported if name.split(".")[0] == "typer"}
    assert not imported & forbidden


def test_importing_runs_loads_neither_typer_nor_cli():
    code = (
        "import sys, agent_manager.runs; "
        "assert 'typer' not in sys.modules, 'typer'; "
        "assert 'agent_manager.cli' not in sys.modules, 'cli'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runs.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'runs' from 'agent_manager'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agent_manager/runs.py`. The bodies and docstrings are copied verbatim from `src/agent_manager/cli.py` lines 55-60, 63-68, 166-198, 618-634 and 660-677:

```python
"""Run helpers with no Typer in them (architecture cleanup, decision S1).

`cli.py` composes and renders; the plain pieces a run needs -- where its repo
is, what it is called, where its worktree goes, how a runner is made, and which
gate parameters it binds -- live here so the modules downstream of `cli` can
reach them without importing the Typer app. `cli.py` re-exports every name
defined here, so `cli.X is runs.X`. This module never imports `cli`.
"""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from agent_manager import dag
from agent_manager.runtime.walk import AgentPhaseRunner
from agent_manager.store import Store

RUN_ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
"""Sortable, path-safe, second-resolution UTC. Run ids are directory names."""

WORKTREE_PARTS = (".claude", "worktrees")
"""Where a subtask's worktree lives under the repo, matching the layout the rest
of this project already uses."""


class CliError(RuntimeError):
    """The command refused to start a run. One base type for the envelope."""


class RepoDirError(CliError):
    """`--repo-dir` does not name a directory this tool can work in."""


def resolve_repo_dir(repo_dir: Path) -> Path:
    """`--repo-dir` as an existing absolute directory, or `RepoDirError`.

    Resolved before anything is derived from it: `steps/worktree.ensure` refuses
    a relative `worktree` or `repo_dir` outright, and the default value of the
    option is `.`.
    """
    resolved = Path(repo_dir).expanduser().resolve()
    if not resolved.is_dir():
        raise RepoDirError(
            f"--repo-dir {str(repo_dir)!r} is not a directory (resolved to {resolved})"
        )
    return resolved


def mint_run_id(card_id: str, now: datetime) -> str:
    """`<UTC timestamp>-<short card id>`: unique, sortable, and greppable.

    The short id comes from `dag`, like every other derived name in the program,
    which also means a card id that is not a UUID is refused here rather than
    producing a run directory nobody can trace back to a card.
    """
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{dag.short_id(card_id)}"


def worktree_for(repo_dir: Path, branch: str) -> Path:
    """`<repo_dir>/.claude/worktrees/<branch>`, absolute.

    Absolute because `steps/worktree.ensure` requires it, and built by joining
    the branch's own segments so a branch like `m1/task-x` becomes two path
    components rather than one with a slash in its name.
    """
    return Path(repo_dir).resolve().joinpath(*WORKTREE_PARTS, *branch.split("/"))


class RunnerFactory(Protocol):
    """How the command gets its `AgentPhaseRunner`.

    A factory rather than a runner, because a real `dispatch.AgentRunner` needs
    the store and three ids that do not exist until the run is
    already half set up -- and because a factory is the seam the tests replace
    to launch no harness at all (§14: the launcher is injected).
    """

    def __call__(
        self,
        *,
        store: Store,
        run_id: str,
        story_id: str,
        card_id: str,
    ) -> AgentPhaseRunner: ...


def gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]:
    """The gate parameters `TASK`'s gates bind and `subtask_context` lacks.

    `explore` gates on `verification_gate(suite_cmds, allow_no_verification,
    caller_provided)` and `exploration_output_gate(explore,
    provided_verification)`. Three of those four names come from the caller, and
    this is the caller. `bool()` is deliberate: the reducer tests
    `allow_no_verification is True`, so a truthy stand-in must not open the
    opt-out by accident. `caller_provided` is `False` and
    `provided_verification` is `None` because this card discovers no suite --
    per-run verification discovery is the milestone runner's, not this command's.
    """
    return {
        "suite_cmds": list(commands),
        "allow_no_verification": bool(allow_no_verification),
        "caller_provided": False,
        "provided_verification": None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runs.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runs.py tests/test_runs.py
git commit -m "feat(runs): add Typer-free runs module with the simple run helpers"
```

---

### Task 2: Make `cli.py` re-export the moved names instead of defining them

**Files:**
- Modify: `src/agent_manager/cli.py:27` (typing import), `:46-68` (add re-export, delete constants and two error classes), `:166-198` (delete three helpers), `:618-634` (delete `RunnerFactory`), `:660-677` (delete `gate_context`)
- Test: `tests/test_runs.py` (append identity tests; same unit tier as Task 1)

**Interfaces:**
- Consumes: the nine names produced by Task 1 in `agent_manager.runs`.
- Produces: `agent_manager.cli.<name> is agent_manager.runs.<name>` for all nine names; `cli.default_runner_factory` unchanged and still resolving `run_direct` from `cli`'s globals.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runs.py`:

```python
MOVED_NAMES = (
    "RUN_ID_TIME_FORMAT",
    "WORKTREE_PARTS",
    "CliError",
    "RepoDirError",
    "resolve_repo_dir",
    "mint_run_id",
    "worktree_for",
    "RunnerFactory",
    "gate_context",
)


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_cli_re_exports_the_moved_name_as_the_same_object(name):
    from agent_manager import cli

    assert getattr(cli, name) is getattr(runs, name)


def test_cli_error_subclasses_share_the_moved_base():
    from agent_manager import cli

    assert issubclass(cli.UnknownRunError, runs.CliError)
    assert issubclass(cli.CheckpointMismatchError, runs.CliError)
    assert runs.CliError in cli.HANDLED


def test_default_runner_factory_stays_in_cli():
    from agent_manager import cli

    assert cli.default_runner_factory.__module__ == "agent_manager.cli"
    assert not hasattr(runs, "default_runner_factory")
    assert not hasattr(runs, "run_direct")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runs.py -v`
Expected: the nine `test_cli_re_exports_the_moved_name_as_the_same_object[...]` cases FAIL for the class/function names (`CliError`, `RepoDirError`, `resolve_repo_dir`, `mint_run_id`, `worktree_for`, `RunnerFactory`, `gate_context`) with `AssertionError` (distinct objects); `test_cli_error_subclasses_share_the_moved_base` FAILS (`cli.UnknownRunError` subclasses `cli`'s own `CliError`, not `runs.CliError`, and `cli.HANDLED` holds `cli`'s own `CliError`). `RUN_ID_TIME_FORMAT` may pass by string interning and `WORKTREE_PARTS` may pass by constant tuple caching — that is fine; the other failures are the RED signal. `test_default_runner_factory_stays_in_cli` passes already (it guards the exemption).

- [ ] **Step 3: Replace the constants and the two base errors with the re-export**

In `src/agent_manager/cli.py`, change line 27 from:

```python
from typing import Any, Protocol
```

to:

```python
from typing import Any
```

Then replace this block (lines 46-68):

```python
from agent_manager.store import Store
from agent_manager.workflow import task as task_workflow

EXIT_ESCALATED = 1
"""The subtask escalated. §12: a full stop a human has to read."""

EXIT_ERROR = 3
"""The tool could not run the subtask at all. `2` belongs to Typer's usage errors."""

RUN_ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
"""Sortable, path-safe, second-resolution UTC. Run ids are directory names."""

WORKTREE_PARTS = (".claude", "worktrees")
"""Where a subtask's worktree lives under the repo, matching the layout the rest
of this project already uses."""


class CliError(RuntimeError):
    """The command refused to start a run. One base type for the envelope."""


class RepoDirError(CliError):
    """`--repo-dir` does not name a directory this tool can work in."""


class UnknownRunError(CliError):
```

with:

```python
from agent_manager.store import Store
from agent_manager.workflow import task as task_workflow

# Re-exported so every `cli.X` caller keeps working while S1 moves the plain
# run helpers out of the Typer module; `cli.X is runs.X` for each name.
from agent_manager.runs import (
    RUN_ID_TIME_FORMAT,
    WORKTREE_PARTS,
    CliError,
    RepoDirError,
    RunnerFactory,
    gate_context,
    mint_run_id,
    resolve_repo_dir,
    worktree_for,
)

EXIT_ESCALATED = 1
"""The subtask escalated. §12: a full stop a human has to read."""

EXIT_ERROR = 3
"""The tool could not run the subtask at all. `2` belongs to Typer's usage errors."""


class UnknownRunError(CliError):
```

- [ ] **Step 4: Delete the three helpers and the Protocol and `gate_context`**

In `src/agent_manager/cli.py`, replace this block (originally lines 162-201):

```python
class RunIsLiveError(CliError):
    """`am resume` was asked for a run another live process still holds (C10)."""


def resolve_repo_dir(repo_dir: Path) -> Path:
    """`--repo-dir` as an existing absolute directory, or `RepoDirError`.

    Resolved before anything is derived from it: `steps/worktree.ensure` refuses
    a relative `worktree` or `repo_dir` outright, and the default value of the
    option is `.`.
    """
    resolved = Path(repo_dir).expanduser().resolve()
    if not resolved.is_dir():
        raise RepoDirError(
            f"--repo-dir {str(repo_dir)!r} is not a directory (resolved to {resolved})"
        )
    return resolved


def mint_run_id(card_id: str, now: datetime) -> str:
    """`<UTC timestamp>-<short card id>`: unique, sortable, and greppable.

    The short id comes from `dag`, like every other derived name in the program,
    which also means a card id that is not a UUID is refused here rather than
    producing a run directory nobody can trace back to a card.
    """
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{dag.short_id(card_id)}"


def worktree_for(repo_dir: Path, branch: str) -> Path:
    """`<repo_dir>/.claude/worktrees/<branch>`, absolute.

    Absolute because `steps/worktree.ensure` requires it, and built by joining
    the branch's own segments so a branch like `m1/task-x` becomes two path
    components rather than one with a slash in its name.
    """
    return Path(repo_dir).resolve().joinpath(*WORKTREE_PARTS, *branch.split("/"))


def ok_envelope(data: Any) -> dict[str, Any]:
```

with:

```python
class RunIsLiveError(CliError):
    """`am resume` was asked for a run another live process still holds (C10)."""


def ok_envelope(data: Any) -> dict[str, Any]:
```

Then replace this block (originally lines 615-635):

```python
WORKFLOW_NAME = "task"
"""The only document `run --card` drives. `--workflow` is §10's, not this card's."""

class RunnerFactory(Protocol):
    """How the command gets its `AgentPhaseRunner`.

    A factory rather than a runner, because a real `dispatch.AgentRunner` needs
    the store and three ids that do not exist until the run is
    already half set up -- and because a factory is the seam the tests replace
    to launch no harness at all (§14: the launcher is injected).
    """

    def __call__(
        self,
        *,
        store: Store,
        run_id: str,
        story_id: str,
        card_id: str,
    ) -> AgentPhaseRunner: ...


def default_runner_factory(
```

with:

```python
WORKFLOW_NAME = "task"
"""The only document `run --card` drives. `--workflow` is §10's, not this card's."""


def default_runner_factory(
```

Then replace this block (originally lines 651-680, the tail of `default_runner_factory`, `gate_context`, and the head of `SubtaskDrive`):

```python
    return dispatch.AgentRunner(
        store=store,
        launcher=run_direct,
        run_id=run_id,
        story_id=story_id,
        card_id=card_id,
    )


def gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]:
    """The gate parameters `TASK`'s gates bind and `subtask_context` lacks.

    `explore` gates on `verification_gate(suite_cmds, allow_no_verification,
    caller_provided)` and `exploration_output_gate(explore,
    provided_verification)`. Three of those four names come from the caller, and
    this is the caller. `bool()` is deliberate: the reducer tests
    `allow_no_verification is True`, so a truthy stand-in must not open the
    opt-out by accident. `caller_provided` is `False` and
    `provided_verification` is `None` because this card discovers no suite --
    per-run verification discovery is the milestone runner's, not this command's.
    """
    return {
        "suite_cmds": list(commands),
        "allow_no_verification": bool(allow_no_verification),
        "caller_provided": False,
        "provided_verification": None,
    }


@dataclass(frozen=True)
class SubtaskDrive:
```

with:

```python
    return dispatch.AgentRunner(
        store=store,
        launcher=run_direct,
        run_id=run_id,
        story_id=story_id,
        card_id=card_id,
    )


@dataclass(frozen=True)
class SubtaskDrive:
```

Do not touch `default_runner_factory`'s signature or body, the `from agent_manager.harness.launcher import run_direct` line, the `datetime`/`timezone`/`Sequence`/`Path`/`AgentPhaseRunner`/`Store`/`dag` imports (all still used elsewhere in `cli.py`: `_utcnow`, `already_done_entries`, `default_runner_factory`, `drive_subtask_async`, `dag.task_branch`, ...), the deferred imports inside `cli.py`, or `dry_run_payload`.

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_runs.py -v`
Expected: all tests PASS (13 from Task 1 + 9 parametrized identity cases + 2).

- [ ] **Step 6: Run the full unmodified suite**

Run: `uv run pytest`
Expected: PASS, with the same pass/skip counts as master plus the new `tests/test_runs.py` cases. In particular these existing files pass without edits: `tests/test_cli.py`, `tests/workflow/test_task.py`, `tests/test_orchestrate.py`, `tests/test_bases.py`, `tests/test_integration.py`, `tests/e2e/test_live_control.py`, `tests/e2e/test_milestone_run.py`, `tests/e2e/test_milestone_resume.py`. Confirm `git status --short tests/` shows only `tests/test_runs.py` (no existing test file changed). If `grep -n "Protocol" src/agent_manager/cli.py` prints anything, a `Protocol` use remains and the typing import must keep it — restore `Protocol` in that case.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_runs.py
git commit -m "refactor(cli): re-export the simple run helpers from runs instead of defining them"
```
