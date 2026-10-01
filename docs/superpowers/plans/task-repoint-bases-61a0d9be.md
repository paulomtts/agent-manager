<!-- task-pipeline: validated -->
# Repoint bases/integration/orchestrate at runs.py and delete cli.py's deferred imports (61a0d9be)

Card: 61a0d9be-083c-4627-9d0f-6a817f36553d. Story: 4bc0a3e0 ("cli.py sheds its collaborator role"). Milestone: 9c44c2fb (milestone 13, architecture cleanup).
Spec of record: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, decision S1 (§3), testing note (§6), out-of-scope list (§8).
Base branch: `m13/task-move-dry-run-payload-s-64babfa2` (tip `7256dd6`), not master. Master has no `runs.py`.

This is the last of four S1 subtasks. Siblings 46244d0e, 1c21b3dd and 64babfa2 are done and already moved the helpers into `runs.py`. This card only repoints the callers and removes the scaffolding those moves left behind.

## Scope

Line numbers below were read on the base branch. They will drift, so re-read the code as built. The card's own line numbers (895/1137/1460) and its mention of a deferred `integration` import are stale and should be ignored.

1. **`bases.py`.** Replace `from agent_manager import cli, ...` (about line 34) with an import of `runs`. Rewrite every `cli.RunnerFactory`, `cli.gate_context` and `cli.worktree_for` as `runs.<name>`. After this, `bases.py` does not import `cli` at all.
2. **`integration.py`.** Same change at about line 29, for the same three names. After this, `integration.py` does not import `cli` at all.
3. **`orchestrate.py`.** Add `runs` to the import line (about line 55). Keep `cli` in that line.
   - Repoint only the names that S1 moved: `RunnerFactory`, `UnknownRunError`, `NotResumableError`, `CheckpointMismatchError`, `mint_run_id`, `orphan_attempts`, `worktree_for`, `continuable_checkpoint` and `resolve_repo_dir`. Apply this to annotations and docstring mentions as well as calls.
   - These stay as `cli.`, because they are still defined in `cli.py` and are not part of S1:
     - `cli.default_runner_factory` (the explicit exemption, used at about lines 800/805/1491)
     - `cli.SubtaskDrive`
     - `cli.drive_subtask_async`
     - `cli._utcnow`
     - `cli.resume_run`
     - `cli.checkpoint_resume_phase`
   - `orchestrate.py` therefore still legitimately imports `cli`.
4. **`cli.py` deferred imports.** Delete the in-function `from agent_manager import orchestrate` imports and their cycle-breaking comments (currently two sites, at about lines 999 and 1366). Replace them with one module-level `orchestrate` import.
   - This is safe only if no cycle is left. Once steps 1–3 are done, `bases` and `integration` no longer import `cli`. `orchestrate` still imports `cli`, though, so a module-level `cli → orchestrate → cli` cycle would remain.
   - If a top-level import fails for that reason, keep the deferred import at those call sites. Record the reason in the card's notes. Do not restructure `orchestrate`, because moving `default_runner_factory` or the drive functions is out of scope.
   - There is no deferred `integration` import left in `cli.py`, so there is nothing to remove for it.
5. **`cli.py` re-exports.** In the `from agent_manager.runs import (...)` block (about lines 49–69), the spec's rule is to drop each name that no module outside `cli.py` still reads through `cli.` ("outside" includes `tests/`) — but per the "Deviation from the spec, decided here" section below, that rule does not license dropping any of the seventeen names actually in the block, because `tests/test_runs.py`'s `MOVED_NAMES`-parametrized `test_cli_re_exports_the_moved_name_as_the_same_object` reads every one of them via `getattr(cli, name)`, and the spec forbids editing existing tests beyond the monkeypatch retargeting described above. **No name is dropped from the block; only its comment is reworded** (see Task 5).
   - Names still used inside `cli.py` stay imported, but the "re-exported so every `cli.X` caller keeps working" comment must be reworded to match what the block now does.
   - Many names (`worktree_for`, `resolve_repo_dir`, `mint_run_id`, `select_resumable`, `orphan_attempts`, `gate_context`, `continuable_checkpoint`, the error types) are still read as `cli.X` by `tests/test_cli.py`, `tests/test_integration.py`, `tests/workflow/test_task.py` and `tests/e2e/*`. In particular, `tests/test_cli.py:4337` calls `cli.continuable_checkpoint(...)` directly (not a monkeypatch) — dropping it from the re-export block breaks that test.
   - `RUN_ID_TIME_FORMAT`, `WORKTREE_PARTS`, `DryRunPlan` and `RunnerFactory` look, from live-code usage alone, droppable (they are read nowhere as `cli.X` in `src/`, and the `cli.RunnerFactory` occurrences in `tests/test_bases.py`, `tests/test_integration.py` and `tests/test_cli.py` are docstring prose, not attribute access) — **but they stay**, because `tests/test_runs.py::MOVED_NAMES` reads all four through `getattr(cli, name)`, and that existing test is not eligible for rewriting under this card. `compute_dry_run_plan` also stays, for the independent reason that `cli.py`'s own `dry_run_payload` still calls it by its bare name.
   - Do not touch `default_runner_factory`. It was never re-exported and stays defined in `cli.py`.

## Observable behaviour

No change to the CLI or the runtime (spec §5).

- Every `am` command produces the same JSON envelope (`{"ok": true, "data": ...}`), the same `--pretty` output and the same exit codes.
- Error paths are unchanged. `UnknownRunError`, `NotResumableError`, `CheckpointMismatchError`, `CliError` and `RepoDirError` are the same objects (`cli.X is runs.X` still holds for every name that is still re-exported). They are raised and rendered exactly as before.
- The only visible difference is which module owns each attribute lookup. That matters for monkeypatching (see the next section).

## Test-suite consequence of repointing (monkeypatch targets)

`tests/test_orchestrate.py` monkeypatches `cli.continuable_checkpoint` in two places: around line 2948 (the `broken` fixture) and around line 3927 (`_never_consulted`).

After step 3, `orchestrate` looks up `runs.continuable_checkpoint`, so a patch on `cli` no longer reaches it:

- The first test would fail.
- The second would pass without testing anything.

Both `setattr` targets, and the `_never_consulted` failure message that names `cli.continuable_checkpoint`, must be retargeted to `runs`. This counts as repointing imports at `runs`, which is what spec §6 allows. It is the only permitted edit to existing tests.

Before finishing, grep `tests/` for `setattr(cli, "<moved name>"` and for string-path patches (`"agent_manager.cli.<moved name>"`) of any other moved name that `bases`, `integration` or `orchestrate` now read from `runs`. Retarget those the same way.

## Tests

The placement rule is CLAUDE.md's "tests mirror `src/agent_manager/` under `tests/`": one flat `tests/test_<module>.py` per source module. There is no unit/integration tier split for these modules. The precedent is the ast-based import checks already in `tests/test_runs.py:98-109`, `tests/test_control.py`, `tests/test_census.py` and `tests/test_orchestrate.py`.

New tests:

- **`tests/test_bases.py`: `test_bases_source_never_imports_cli`.** Module-mirrored file for `bases.py`. Parse `bases.py` with `ast`, collect every `Import`/`ImportFrom` at any depth, and assert that none of them binds `agent_manager.cli`. That means no `from agent_manager import cli`, no `import agent_manager.cli` and no `from agent_manager.cli import ...`. This is a static check, not a behavioural one.
- **`tests/test_integration.py`: `test_integration_source_never_imports_cli`.** Module-mirrored file for `integration.py`. Same ast assertion against `integration.py`.
- There is deliberately no equivalent test for `orchestrate.py`, which still needs `cli` for `default_runner_factory` and the drive functions.

Existing tests:

- `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_bases.py`, `tests/test_integration.py`, `tests/test_runs.py`, `tests/workflow/`, `tests/e2e/` must all pass.
- The only edit to existing tests is the monkeypatch retargeting described above.

Verification is `uv run pytest`, the full suite. There is no lint or typecheck command (CLAUDE.md).

## Out of scope

- Anything in spec §8: the pygents turn/phase model, the checkpoint format, the harness adapter contract, adding a typecheck/CI gate, rewriting the roughly 261 legitimate boundary monkeypatches, and replacing grafo.
- Moving `default_runner_factory`, `SubtaskDrive`, `drive_subtask_async`, `resume_run` or `checkpoint_resume_phase` out of `cli.py`.
- Moving any further helpers into `runs.py`, or changing `runs.py`'s contents.
- Other milestone-13 cards: schema, gate evaluation, resolver unification, StoryRecorder, error hierarchy, runtime typing, `paths.py`, Collaborators injection.

---

# Repoint bases/integration/orchestrate at runs.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bases.py` and `integration.py` read the run helpers from `runs` instead of `cli`, make `orchestrate.py` read the S1-moved names from `runs`, and replace `cli.py`'s two in-function `orchestrate` imports with one module-level import, with no observable CLI change.

**Architecture:** Pure import rewiring. `runs.py` (landed by siblings 46244d0e/1c21b3dd/64babfa2) already owns `RunnerFactory`, `gate_context`, `worktree_for`, the error types, `mint_run_id`, `resolve_repo_dir`, `orphan_attempts` and `continuable_checkpoint`; `cli.py` re-exports them as the same objects. Callers switch to `runs.<name>`; `orchestrate` keeps `cli` for `default_runner_factory`, `SubtaskDrive`, `drive_subtask_async`. Once `bases`/`integration` stop importing `cli`, the only `cli`↔`orchestrate` edge left is `orchestrate → cli` read at call time, which Python 3.12's `from package import submodule` fallback to `sys.modules` tolerates, so `cli` can import `orchestrate` at module level.

**Tech Stack:** Python 3.12 (`requires-python = ">=3.12"`), pytest, `ast` for static import checks, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-repoint-bases-61a0d9be/docs/superpowers/specs/task-repoint-bases-61a0d9be-design.md` (prepended above). Parent spec: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` S1 (§3), §6, §8.

**Work location:** worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-repoint-bases-61a0d9be`, branch `m13/task-repoint-bases-61a0d9be`, cut from `m13/task-move-dry-run-payload-s-64babfa2`. All paths below are relative to that worktree root. Run every command from that root.

## Global Constraints

- Verification is `uv run pytest` (full suite). There is no lint or typecheck command (CLAUDE.md).
- No change to the CLI or runtime: same `{"ok": true, "data": ...}` envelope, same `--pretty` output, same exit codes (spec §5).
- `default_runner_factory` is not touched: it stays defined in `cli.py`, is never re-exported from `runs.py`, and `orchestrate` keeps reading it as `cli.default_runner_factory`.
- `cli.SubtaskDrive`, `cli.drive_subtask_async`, `cli._utcnow`, `cli.resume_run`, `cli.checkpoint_resume_phase` stay as `cli.` names in `orchestrate.py`.
- `runs.py`'s contents do not change (spec, Out of scope).
- The only permitted edit to existing tests is retargeting monkeypatches (and the one failure message) of moved names from `cli` to `runs`. New tests are added; existing tests are not otherwise rewritten.
- `bases.py` and `integration.py` must not import `agent_manager.cli` in any form after this card.

## Deviation from the spec, decided here (read before Task 5)

Spec Scope item 5 lists `RUN_ID_TIME_FORMAT`, `WORKTREE_PARTS`, `DryRunPlan` and `RunnerFactory` as droppable from `cli.py`'s re-export block. The spec's governing rule for that item is "drop each name that no module outside `cli.py` still reads through `cli.`. 'Outside' includes `tests/`." That rule does not hold for those four: `tests/test_runs.py:121-146` (`MOVED_NAMES` + `test_cli_re_exports_the_moved_name_as_the_same_object`) does `getattr(cli, name) is getattr(runs, name)` for every one of the 17 moved names, including all four. Dropping them would fail four parametrized cases, and the spec forbids editing existing tests beyond monkeypatch retargeting. So the plan applies the spec's rule and its test-edit constraint, which take precedence over its illustrative list: **no name is dropped from the re-export block**; only its comment is reworded (Task 5). The implementer records this in the card's notes. If a human later wants the four gone, that is a one-line `MOVED_NAMES` change plus the import-block edit, in a follow-up.

## Review Focus

1. Import order: a fresh interpreter that imports `agent_manager.bases` first (nothing imports it first today) must load `bases` without pulling in `cli`, and then `cli` and `orchestrate` must still import cleanly. Test added in Task 1 (`test_importing_bases_first_loads_no_cli_and_leaves_cli_and_orchestrate_importable`). The `cli`/`orchestrate`/`integration`-first orders are already pinned by `tests/test_cli.py::test_cli_and_orchestrate_import_cleanly_in_either_order`, which Task 4 relies on.
2. A monkeypatch of a moved name on `cli` that silently stops reaching `orchestrate` (a test that passes without testing anything). Handled in Task 3 by retargeting the two known patches, running the patched test RED first, and a grep step for any others.
3. A static check that only recognises one spelling of the forbidden import (e.g. misses `import agent_manager.cli`, `from agent_manager.cli import X`, or relative `from . import cli`). Task 1 adds a parametrized self-test of the `_cli_imports` helper over each spelling.
4. `cli.X is runs.X` identity for the error types after the re-export block is touched, so `HANDLED` still catches what `orchestrate` now raises as `runs.NotResumableError` etc. Pinned by existing `tests/test_runs.py::test_cli_re_exports_the_moved_name_as_the_same_object` and `test_cli_error_subclasses_share_the_moved_base`; Task 5 runs them explicitly.
5. `am run --milestone` / `am resume` still reach a monkeypatched `orchestrate.run_milestone` after the import moves to module level (the function must still be read as `orchestrate.run_milestone` at call time, not bound with `from agent_manager.orchestrate import run_milestone`). Pinned by the existing `_patch_run_milestone` tests in `tests/test_cli.py`; Task 4 runs `tests/test_cli.py` in full.

---

### Task 1: `bases.py` reads the run helpers from `runs`

**Files:**
- Modify: `src/agent_manager/bases.py:25` (docstring), `:34` (import), `:139` (docstring), `:168`, `:204`, `:256`, `:283`
- Test: `tests/test_bases.py` (add `import ast`, `import sys` to the stdlib imports at lines 17-27; append new tests at end of file)

**Interfaces:**
- Consumes: `agent_manager.runs.RunnerFactory` (Protocol), `runs.gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]`, `runs.worktree_for(repo: Path, branch: str) -> Path` — all already on the base branch.
- Produces: `tests/test_bases.py::_cli_imports(source: str) -> list[str]` (test-local helper; Task 2 defines its own copy in `tests/test_integration.py`).

- [ ] **Step 1: Add the stdlib imports to `tests/test_bases.py`**

In the stdlib import block (lines 17-23, `import asyncio` … `import threading`), add `import ast` before `import asyncio` and `import sys` after `import subprocess`, so the block reads:

```python
import ast
import asyncio
import dataclasses
import inspect
import json
import shutil
import subprocess
import sys
import threading
```

(`subprocess` and `Path` are already imported; confirm `from pathlib import Path` is at line 26.)

- [ ] **Step 2: Write the failing tests (append to the end of `tests/test_bases.py`)**

```python
# ── S1: bases reads the run helpers from `runs`, never from `cli` (card 61a0d9be) ──


def _cli_imports(source: str) -> list[str]:
    """Every import in `source`, at any depth, that binds `agent_manager.cli`.

    Covers `import agent_manager.cli`, `from agent_manager import cli`,
    `from agent_manager.cli import X` and their relative spellings, resolved
    against the `agent_manager` package the module lives in.
    """
    offending: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            offending += [
                alias.name
                for alias in node.names
                if alias.name == "agent_manager.cli" or alias.name.startswith("agent_manager.cli.")
            ]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = "agent_manager" + (f".{module}" if module else "")
            if module == "agent_manager":
                offending += [f"agent_manager.{alias.name}" for alias in node.names if alias.name == "cli"]
            elif module == "agent_manager.cli" or module.startswith("agent_manager.cli."):
                offending.append(module)
    return offending


@pytest.mark.parametrize(
    "source",
    [
        "from agent_manager import cli",
        "from agent_manager import models, cli",
        "import agent_manager.cli",
        "import agent_manager.cli as c",
        "from agent_manager.cli import worktree_for",
        "from . import cli",
        "from .cli import worktree_for",
        "def f():\n    from agent_manager import cli\n",
    ],
)
def test_cli_imports_catches_every_spelling_of_importing_cli(source):
    assert _cli_imports(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "from agent_manager import runs, models",
        "from agent_manager.runs import worktree_for",
        "import agent_manager.runs",
        "from agent_manager import client",
        "from agent_manager.clique import x",
    ],
)
def test_cli_imports_ignores_imports_that_are_not_cli(source):
    assert _cli_imports(source) == []


def test_bases_source_never_imports_cli():
    """S1 (card 61a0d9be): `bases` reads `RunnerFactory`, `gate_context` and
    `worktree_for` from `runs`. A static check, not a behavioural one."""
    source = Path(bases.__file__).read_text(encoding="utf-8")
    assert _cli_imports(source) == []


def test_importing_bases_first_loads_no_cli_and_leaves_cli_and_orchestrate_importable():
    """Review Focus 1: a fresh interpreter, so earlier tests' imports do not
    hide a cycle. `bases` alone must not pull in `cli`; after it, `cli` and
    `orchestrate` still load and see the same module objects."""
    code = (
        "import sys\n"
        "import agent_manager.bases\n"
        "assert 'agent_manager.cli' not in sys.modules, sorted(sys.modules)\n"
        "from agent_manager import bases, cli, orchestrate, runs\n"
        "assert orchestrate.cli is cli\n"
        "assert orchestrate.bases is bases\n"
        "assert bases.runs is runs\n"
    )

    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
```

- [ ] **Step 3: Run the new tests to verify the right ones fail**

Run: `uv run pytest tests/test_bases.py -k "cli_imports or never_imports_cli or importing_bases_first" -v`
Expected: the 13 `_cli_imports` self-test cases PASS (the helper is correct on its own); `test_bases_source_never_imports_cli` FAILS with `AssertionError: assert ['agent_manager.cli'] == []`; `test_importing_bases_first_...` FAILS with `AssertionError` from the `'agent_manager.cli' not in sys.modules` line in `completed.stderr`.

- [ ] **Step 4: Repoint `src/agent_manager/bases.py`**

Line 34, replace:

```python
from agent_manager import cli, models
```

with:

```python
from agent_manager import models, runs
```

Then replace each remaining `cli.` occurrence (there are exactly six; confirm with a search for `cli` in the file afterwards returning nothing):

- line 25 (module docstring): `` base worktree `cli.worktree_for` names. `` → `` base worktree `runs.worktree_for` names. ``
- line 139 (docstring): `` `bool()` matches `cli.gate_context`: `` → `` `bool()` matches `runs.gate_context`: ``
- line 168: `    runner_factory: cli.RunnerFactory,` → `    runner_factory: runs.RunnerFactory,`
- line 204: `            **cli.gate_context(commands, allow_no_verification),` → `            **runs.gate_context(commands, allow_no_verification),`
- line 256: `    runner_factory: cli.RunnerFactory | None,` → `    runner_factory: runs.RunnerFactory | None,`
- line 283: `    worktree = cli.worktree_for(repo, root.branch)` → `    worktree = runs.worktree_for(repo, root.branch)`

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_bases.py -k "cli_imports or never_imports_cli or importing_bases_first" -v`
Expected: all PASS.

- [ ] **Step 6: Run the whole bases file**

Run: `uv run pytest tests/test_bases.py -v`
Expected: all PASS (tests gated by `requires_git`/`requires_brd` may SKIP if those tools are absent; no FAIL).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/bases.py tests/test_bases.py
git commit -m "refactor(bases): read run helpers from runs, not cli (S1, 61a0d9be)"
```

---

### Task 2: `integration.py` reads the run helpers from `runs`

**Files:**
- Modify: `src/agent_manager/integration.py:8` (docstring), `:29` (import), `:107` (docstring), `:136`, `:166`, `:193`, `:204`
- Test: `tests/test_integration.py` (add `import ast` at the top of the stdlib block at line 19; append new test at end of file)

**Interfaces:**
- Consumes: `runs.RunnerFactory`, `runs.gate_context`, `runs.worktree_for` (same signatures as Task 1).
- Produces: `tests/test_integration.py::_cli_imports(source: str) -> list[str]` — a copy of Task 1's helper (test files here are not a package, so it is not imported across files).

- [ ] **Step 1: Add `import ast` to `tests/test_integration.py`**

The stdlib block starts at line 19 with `import json`. Insert `import ast` above it:

```python
import ast
import json
import os
```

(`Path` is already imported at line 27.)

- [ ] **Step 2: Write the failing test (append to the end of `tests/test_integration.py`)**

```python
# ── S1: integration reads the run helpers from `runs`, never from `cli` (card 61a0d9be) ──


def _cli_imports(source: str) -> list[str]:
    """Every import in `source`, at any depth, that binds `agent_manager.cli`.

    Same helper as `tests/test_bases.py::_cli_imports`, whose parametrized
    self-tests pin every spelling it catches.
    """
    offending: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            offending += [
                alias.name
                for alias in node.names
                if alias.name == "agent_manager.cli" or alias.name.startswith("agent_manager.cli.")
            ]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = "agent_manager" + (f".{module}" if module else "")
            if module == "agent_manager":
                offending += [f"agent_manager.{alias.name}" for alias in node.names if alias.name == "cli"]
            elif module == "agent_manager.cli" or module.startswith("agent_manager.cli."):
                offending.append(module)
    return offending


def test_integration_source_never_imports_cli():
    """S1 (card 61a0d9be): `integration` reads `RunnerFactory`, `gate_context`
    and `worktree_for` from `runs`. A static check, not a behavioural one."""
    source = Path(integration.__file__).read_text(encoding="utf-8")
    assert _cli_imports(source) == []
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_integration.py::test_integration_source_never_imports_cli -v`
Expected: FAIL with `AssertionError: assert ['agent_manager.cli'] == []`.

- [ ] **Step 4: Repoint `src/agent_manager/integration.py`**

Line 29, replace:

```python
from agent_manager import cli, dag, models
```

with:

```python
from agent_manager import dag, models, runs
```

Then replace each remaining `cli.` occurrence (exactly six; a search for `cli` in the file afterwards must return nothing):

- line 8 (module docstring): `` the worktree `cli.worktree_for` names. `` → `` the worktree `runs.worktree_for` names. ``
- line 107 (docstring): `` `bool()` matches `cli.gate_context`: `` → `` `bool()` matches `runs.gate_context`: ``
- line 136: `    runner_factory: cli.RunnerFactory,` → `    runner_factory: runs.RunnerFactory,`
- line 166: `            **cli.gate_context(commands, allow_no_verification),` → `            **runs.gate_context(commands, allow_no_verification),`
- line 193: `    runner_factory: cli.RunnerFactory,` → `    runner_factory: runs.RunnerFactory,`
- line 204: `    worktree = cli.worktree_for(root, branch)` → `    worktree = runs.worktree_for(root, branch)`

Note: `runs.compute_dry_run_plan` imports `integration` inside the function (`runs.py:282`), so `integration → runs` at module level creates no load-time cycle. Do not edit `runs.py`.

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_integration.py::test_integration_source_never_imports_cli -v`
Expected: PASS.

- [ ] **Step 6: Run the integration and runs files, plus the import-order guard**

Run: `uv run pytest tests/test_integration.py tests/test_runs.py "tests/test_cli.py::test_cli_and_orchestrate_import_cleanly_in_either_order" -v`
Expected: all PASS (git/brd-gated tests may SKIP). `tests/test_runs.py::test_compute_dry_run_plan_runs_in_a_fresh_interpreter_that_imported_runs_first` in particular must PASS: it proves `runs → (call-time) integration → runs` does not cycle.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/integration.py tests/test_integration.py
git commit -m "refactor(integration): read run helpers from runs, not cli (S1, 61a0d9be)"
```

---

### Task 3: `orchestrate.py` reads the S1-moved names from `runs`

**Files:**
- Modify: `src/agent_manager/orchestrate.py:33-36` (module docstring), `:55` (import), `:332`, `:562`, `:567`, `:571`, `:575`, `:586`, `:593`, `:622`, `:629`, `:680`, `:686`, `:773`, `:791`, `:876`, `:936`, `:975`, `:1082`, `:1191`, `:1301`, `:1370`, `:1391`
- Test: `tests/test_orchestrate.py:40` (import), `:2948`, `:3904`, `:3927` — the only permitted edits to existing tests.

**Interfaces:**
- Consumes: `runs.RunnerFactory`, `runs.UnknownRunError`, `runs.NotResumableError`, `runs.CheckpointMismatchError`, `runs.mint_run_id(card_id: str, now: datetime) -> str`, `runs.orphan_attempts(subtask: models.SubtaskRun)`, `runs.worktree_for(repo: Path, branch: str) -> Path`, `runs.continuable_checkpoint(store: Store, card_id: str) -> Checkpoint | None`, `runs.resolve_repo_dir(repo_dir: Path) -> Path`.
- Produces: `orchestrate.runs` module attribute (Task 4's import-order reasoning relies on `orchestrate` still importing `cli` as a module and reading it only at call time).

- [ ] **Step 1: Retarget the two monkeypatches in `tests/test_orchestrate.py` (RED)**

Line 40, replace:

```python
from agent_manager import bases, board, census, cli, control, dag, integration, models, orchestrate, paths
```

with:

```python
from agent_manager import bases, board, census, cli, control, dag, integration, models, orchestrate, paths, runs
```

Line 2948, replace:

```python
    monkeypatch.setattr(cli, "continuable_checkpoint", broken)
```

with:

```python
    monkeypatch.setattr(runs, "continuable_checkpoint", broken)
```

Line 3904, replace:

```python
    pytest.fail("a resume consulted the lenient relaunch lookup cli.continuable_checkpoint")
```

with:

```python
    pytest.fail("a resume consulted the lenient relaunch lookup runs.continuable_checkpoint")
```

Line 3927, replace:

```python
    monkeypatch.setattr(cli, "continuable_checkpoint", _never_consulted)
```

with:

```python
    monkeypatch.setattr(runs, "continuable_checkpoint", _never_consulted)
```

- [ ] **Step 2: Run the retargeted lookup-failure test to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_a_checkpoint_lookup_that_fails_escalates_that_subtask -v`
Expected: FAIL at `assert result["escalated"] is True` — `orchestrate` still reads `cli.continuable_checkpoint`, which `cli.py` bound at import time to the real function, so the patch on `runs` never reaches it and the subtask is driven normally. (This test is gated by `requires_git` and `requires_brd`; if it SKIPs, the environment lacks git or brd and the RED/GREEN evidence for this task must come from an environment that has both. Do not proceed on a SKIP.)

- [ ] **Step 3: Repoint the import line in `src/agent_manager/orchestrate.py`**

Line 55, replace:

```python
from agent_manager import bases, board, census, cli, control, dag, integration, models
```

with:

```python
from agent_manager import bases, board, census, cli, control, dag, integration, models, runs
```

- [ ] **Step 4: Rewrite the module docstring paragraph at lines 33-36**

Replace:

```python
`cli` is imported as a module and every name on it is read at call time: the
CLI wiring card makes `cli` import this module, and binding a `cli` name at
import or definition time would break under that circular import. The clock
default is this module's own `_utcnow` for the same reason.
```

with:

```python
The run helpers S1 moved out of the Typer module (`RunnerFactory`, the resume
error types, `mint_run_id`, `resolve_repo_dir`, `worktree_for`,
`orphan_attempts`, `continuable_checkpoint`) are read off `runs`. `cli` is
still imported, as a module, for what it alone defines -- the production
`default_runner_factory` and the `drive_subtask_async` driver -- and every
name on it is read at call time: `cli` imports this module, and binding a
`cli` name at import or definition time would break under that circular
import. The clock default is this module's own `_utcnow` for the same reason.
```

- [ ] **Step 5: Repoint every S1-moved name (and only those)**

Make exactly these replacements. Each left-hand text is the whole current line (leading spaces included).

| Line | Current | New |
|---|---|---|
| 332 | `        runner_factory: cli.RunnerFactory \| None = None,` | `        runner_factory: runs.RunnerFactory \| None = None,` |
| 562 | `        raise cli.UnknownRunError(` | `        raise runs.UnknownRunError(` |
| 567 | `        raise cli.NotResumableError(` | `        raise runs.NotResumableError(` |
| 571 | `        raise cli.NotResumableError(` | `        raise runs.NotResumableError(` |
| 575 | `        raise cli.NotResumableError(` | `        raise runs.NotResumableError(` |
| 586 | ``    `cli.mint_run_id` builds a milestone run's id as `<timestamp>-<short`` | ``    `runs.mint_run_id` builds a milestone run's id as `<timestamp>-<short`` |
| 593 | `        raise cli.NotResumableError(` | `        raise runs.NotResumableError(` |
| 622 | ``    """`cli.CheckpointMismatchError` when `checkpoint` was saved under another digest.`` | ``    """`runs.CheckpointMismatchError` when `checkpoint` was saved under another digest.`` |
| 629 | `        raise cli.CheckpointMismatchError(` | `        raise runs.CheckpointMismatchError(` |
| 680 | ``    `cli.orphan_attempts`' in-flight attempt, marked as `cli`'s`` | ``    `runs.orphan_attempts`' in-flight attempt, marked as `cli`'s`` |
| 686 | `            for phase, attempt in cli.orphan_attempts(subtask):` | `            for phase, attempt in runs.orphan_attempts(subtask):` |
| 773 | `                    worktree_path=cli.worktree_for(root, branch),` | `                    worktree_path=runs.worktree_for(root, branch),` |
| 791 | `    runner_factory: cli.RunnerFactory \| None,` | `    runner_factory: runs.RunnerFactory \| None,` |
| 876 | `    runner_factory: cli.RunnerFactory \| None,` | `    runner_factory: runs.RunnerFactory \| None,` |
| 936 | `    runner_factory: cli.RunnerFactory \| None,` | `    runner_factory: runs.RunnerFactory \| None,` |
| 975 | ``    (`cli.continuable_checkpoint`), inside the same `try`, and handed to the`` | ``    (`runs.continuable_checkpoint`), inside the same `try`, and handed to the`` |
| 1082 | `                    checkpoint = cli.continuable_checkpoint(store, subtask.id)` | `                    checkpoint = runs.continuable_checkpoint(store, subtask.id)` |
| 1191 | `    runner_factory: cli.RunnerFactory \| None,` | `    runner_factory: runs.RunnerFactory \| None,` |
| 1301 | `    runner_factory: cli.RunnerFactory \| None = None,` | `    runner_factory: runs.RunnerFactory \| None = None,` |
| 1370 | `    root = cli.resolve_repo_dir(repo_dir)` | `    root = runs.resolve_repo_dir(repo_dir)` |
| 1391 | `        run_id = cli.mint_run_id(milestone_card.id, started_at)` | `        run_id = runs.mint_run_id(milestone_card.id, started_at)` |

(The `\|` in the table is a Markdown escape for `|`; the source text has a plain `|`.)

Leave these `cli.` occurrences exactly as they are — they name things still defined only in `cli.py`: line 304 (`cli._utcnow`), 305 (`` `cli` name ``), 310 (`cli.drive_subtask_async`), 314 (`` `cli` name ``), 335 (`cli.SubtaskDrive`), 551 (`cli.resume_run`), 624 (`cli.checkpoint_resume_phase`), 680's trailing `` `cli`'s `_resume_from_checkpoint` ``, 800 and 805 (`cli.default_runner_factory`), 1318 and 1384 (`cli.drive_subtask_async`), 1490-1491 (`off \`cli\` now`, `cli.default_runner_factory`).

- [ ] **Step 6: Verify no moved name is still read off `cli` in `orchestrate.py`**

Search `src/agent_manager/orchestrate.py` for the regex `cli\.(RunnerFactory|UnknownRunError|NotResumableError|CheckpointMismatchError|mint_run_id|orphan_attempts|worktree_for|continuable_checkpoint|resolve_repo_dir|gate_context|select_resumable|CliError|RepoDirError)\b`.
Expected: zero matches. Then search for `cli\.` and confirm every remaining match is one of: `_utcnow`, `drive_subtask_async`, `SubtaskDrive`, `resume_run`, `checkpoint_resume_phase`, `default_runner_factory`.

- [ ] **Step 7: Run the retargeted tests to verify they pass**

Run: `uv run pytest "tests/test_orchestrate.py::test_a_checkpoint_lookup_that_fails_escalates_that_subtask" "tests/test_orchestrate.py::test_a_resume_reuses_the_recorded_settings_and_hands_each_open_checkpoint_on" -v`
Expected: both PASS.

- [ ] **Step 8: Prove the second retarget is not vacuous**

Temporarily change `src/agent_manager/orchestrate.py:1079-1082` so the resume path also calls the lenient lookup — replace `checkpoint = plan.checkpoints.get(subtask.id)` (line 1080) with `checkpoint = runs.continuable_checkpoint(store, subtask.id)`. Run:

`uv run pytest "tests/test_orchestrate.py::test_a_resume_reuses_the_recorded_settings_and_hands_each_open_checkpoint_on" -v`

Expected: FAIL with `a resume consulted the lenient relaunch lookup runs.continuable_checkpoint`. Then revert line 1080 to exactly `                    checkpoint = plan.checkpoints.get(subtask.id)` and confirm `git diff src/agent_manager/orchestrate.py` shows no change at line 1080.

- [ ] **Step 9: Grep `tests/` for any other patch of a moved name on `cli`**

Search `tests/` (multiline) for each of:
- `setattr\(\s*cli,\s*"(RUN_ID_TIME_FORMAT|WORKTREE_PARTS|CliError|RepoDirError|resolve_repo_dir|mint_run_id|worktree_for|RunnerFactory|gate_context|UnknownRunError|NotResumableError|CheckpointMismatchError|select_resumable|orphan_attempts|continuable_checkpoint|DryRunPlan|compute_dry_run_plan)"`
- `"agent_manager\.cli\.(RUN_ID_TIME_FORMAT|WORKTREE_PARTS|CliError|RepoDirError|resolve_repo_dir|mint_run_id|worktree_for|RunnerFactory|gate_context|UnknownRunError|NotResumableError|CheckpointMismatchError|select_resumable|orphan_attempts|continuable_checkpoint|DryRunPlan|compute_dry_run_plan)"`

Expected: zero matches (on the base branch the only two were the ones retargeted in Step 1). If a match appears whose patched name is read by `bases`, `integration` or `orchestrate`, retarget it from `cli` to `runs` the same way as Step 1 and add `runs` to that file's `from agent_manager import ...` line. A patch of a name that is read only inside `cli.py` stays on `cli`.

- [ ] **Step 10: Run the orchestrate, bases and integration files**

Run: `uv run pytest tests/test_orchestrate.py tests/test_bases.py tests/test_integration.py -v`
Expected: all PASS (git/brd-gated tests may SKIP).

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "refactor(orchestrate): read S1-moved run helpers from runs (61a0d9be)"
```

---

### Task 4: `cli.py` imports `orchestrate` at module level

**Files:**
- Modify: `src/agent_manager/cli.py:31-40` (import block), `:992-999` (first deferred import), `:1363-1366` (second deferred import)
- Test: existing `tests/test_cli.py::test_cli_and_orchestrate_import_cleanly_in_either_order` (parametrized over `cli`, `orchestrate`, `integration` first) and Task 1's `test_importing_bases_first_loads_no_cli_and_leaves_cli_and_orchestrate_importable` — no new test file; these are the guards that fail if the module-level import reintroduces a load-time cycle.

**Interfaces:**
- Consumes: `orchestrate.run_milestone(...)` and `orchestrate.MILESTONE_WORKFLOW`, still read as attributes of the module at call time so `tests/test_cli.py`'s `_patch_run_milestone` (which patches `orchestrate.run_milestone`) keeps reaching them.
- Produces: `cli.orchestrate` module attribute.

This is a behaviour-preserving refactor, so its RED is the existing guard suite: run it first to record the baseline, then make the change, then run it again.

- [ ] **Step 1: Record the baseline of the guards**

Run: `uv run pytest "tests/test_cli.py::test_cli_and_orchestrate_import_cleanly_in_either_order" "tests/test_bases.py::test_importing_bases_first_loads_no_cli_and_leaves_cli_and_orchestrate_importable" -v`
Expected: all 4 cases PASS.

- [ ] **Step 2: Add `orchestrate` to `cli.py`'s module-level import block**

Lines 31-40, replace:

```python
from agent_manager import (
    board,
    census,
    control,
    dag,
    dispatch,
    models,
    prompt,
    store as store_module,
)
```

with:

```python
from agent_manager import (
    board,
    census,
    control,
    dag,
    dispatch,
    models,
    orchestrate,
    prompt,
    store as store_module,
)
```

- [ ] **Step 3: Delete the first deferred import (in `run`, lines 992-999)**

Replace:

```python
        elif milestone is not None:
            # `orchestrate` imports this module at load time and reads its names
            # at call time, so importing it at the top of this module would be
            # circular. By the time a command runs, both are fully loaded. Read
            # as `orchestrate.run_milestone` so a test can patch it there. No
            # runner_factory and no driver: production gets
            # `default_runner_factory` and `drive_subtask`.
            from agent_manager import orchestrate

            payload = orchestrate.run_milestone(
```

with:

```python
        elif milestone is not None:
            # Read as `orchestrate.run_milestone` so a test can patch it there.
            # No runner_factory and no driver: production gets
            # `default_runner_factory` and `drive_subtask`.
            payload = orchestrate.run_milestone(
```

- [ ] **Step 4: Delete the second deferred import (in `resume_run`, lines 1363-1366)**

Replace:

```python
    # Imported here for the reason `run` gives: `orchestrate` imports this
    # module at load time. Read as `orchestrate.run_milestone` so a test can
    # patch it there.
    from agent_manager import orchestrate

    if run.workflow == orchestrate.MILESTONE_WORKFLOW:
```

with:

```python
    # Read as `orchestrate.run_milestone` so a test can patch it there.
    if run.workflow == orchestrate.MILESTONE_WORKFLOW:
```

- [ ] **Step 5: Confirm no deferred `orchestrate` or `integration` import remains in `cli.py`**

Search `src/agent_manager/cli.py` for `^\s+from agent_manager import (orchestrate|integration)` and `^\s+import agent_manager\.(orchestrate|integration)`.
Expected: zero matches. Search for `^from agent_manager import \($` and confirm the block from Step 2 is the only one that names `orchestrate`.

- [ ] **Step 6: Run the import-order guards**

Run: `uv run pytest "tests/test_cli.py::test_cli_and_orchestrate_import_cleanly_in_either_order" "tests/test_bases.py::test_importing_bases_first_loads_no_cli_and_leaves_cli_and_orchestrate_importable" -v`
Expected: all 4 cases PASS.

If any case FAILS with an `ImportError`/`AttributeError` naming a partially initialised module (the `cli → orchestrate → cli` cycle the spec warns about): revert Steps 2-4 (`git checkout -- src/agent_manager/cli.py`), keep both deferred imports as they were, and write in the card's notes: "Kept cli.py's two deferred `orchestrate` imports: a module-level import fails with <paste the stderr line> because orchestrate still imports cli for default_runner_factory/drive_subtask_async, which S1 leaves in cli.py." Then skip to Step 8 and commit nothing for this task.

- [ ] **Step 7: Run the full `test_cli.py` (Review Focus 5)**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all PASS (git/brd-gated tests may SKIP). The `_patch_run_milestone` tests (e.g. `test_an_unhandled_error_from_a_milestone_run_crashes_loudly`) prove `run` and `resume_run` still read `orchestrate.run_milestone` at call time.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/cli.py
git commit -m "refactor(cli): import orchestrate at module level, drop deferred imports (61a0d9be)"
```

---

### Task 5: Reword `cli.py`'s `runs` import block and run the full suite

**Files:**
- Modify: `src/agent_manager/cli.py:49-50` (comment above `from agent_manager.runs import (...)`)
- Test: existing `tests/test_runs.py::test_cli_re_exports_the_moved_name_as_the_same_object`, `tests/test_runs.py::test_cli_error_subclasses_share_the_moved_base`, `tests/test_runs.py::test_default_runner_factory_stays_in_cli`; then the full suite.

**Interfaces:**
- Consumes: nothing new.
- Produces: no API change — every name in the block stays importable as `cli.X` with `cli.X is runs.X` (see "Deviation from the spec" above for why none is dropped).

- [ ] **Step 1: Re-check which block names are still read through `cli.` outside `cli.py`**

For each of `RUN_ID_TIME_FORMAT`, `WORKTREE_PARTS`, `DryRunPlan`, `RunnerFactory`, `compute_dry_run_plan`, search `src/` and `tests/` excluding `src/agent_manager/cli.py` for `cli\.<name>\b` and for the string `"<name>"` in `tests/test_runs.py`.
Expected: every one of them appears in `tests/test_runs.py`'s `MOVED_NAMES` tuple (lines 121-139), which `test_cli_re_exports_the_moved_name_as_the_same_object` reads via `getattr(cli, name)`. So by the spec's rule each is still read through `cli.` by `tests/`, and none may be dropped without editing that existing test, which the spec forbids. Leave the import list (lines 51-69) unchanged. If `MOVED_NAMES` has changed on the branch so that a name is no longer listed there and no other `cli.<name>` read exists outside `cli.py` and it is not used bare inside `cli.py`, drop that one name from the block and note it in the commit message.

- [ ] **Step 2: Reword the comment above the block**

Lines 49-50, replace:

```python
# Re-exported so every `cli.X` caller keeps working while S1 moves the plain
# run helpers out of the Typer module; `cli.X is runs.X` for each name.
```

with:

```python
# The run helpers S1 moved to `runs` (card 61a0d9be finished the move: bases,
# integration and orchestrate read them off `runs`). This module uses some by
# their bare names; the rest stay importable as `cli.X` for the tests and e2e
# drivers that still read them here. `cli.X is runs.X` for every name.
```

- [ ] **Step 3: Run the identity and exemption tests (Review Focus 4)**

Run: `uv run pytest "tests/test_runs.py::test_cli_re_exports_the_moved_name_as_the_same_object" "tests/test_runs.py::test_cli_error_subclasses_share_the_moved_base" "tests/test_runs.py::test_default_runner_factory_stays_in_cli" -v`
Expected: all PASS (17 parametrized cases + 2).

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: exit code 0, no FAIL and no ERROR. SKIPs are only the pre-existing `requires_git`/`requires_brd`/e2e gates.

- [ ] **Step 5: Final static check across the three repointed modules**

Search `src/agent_manager/bases.py` and `src/agent_manager/integration.py` for `\bcli\b`: expected zero matches. Search `src/agent_manager/cli.py` for `default_runner_factory` and confirm its `def default_runner_factory` definition is unchanged: `git diff m13/task-move-dry-run-payload-s-64babfa2...HEAD -- src/agent_manager/cli.py` shows only the Task 4 and Task 5 hunks (import block, two deferred-import sites, the runs-block comment) and none touching `default_runner_factory`. Confirm `git diff m13/task-move-dry-run-payload-s-64babfa2...HEAD -- src/agent_manager/runs.py` is empty.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py
git commit -m "docs(cli): describe the runs import block after S1 repointing (61a0d9be)"
```

- [ ] **Step 7: Record the deviation in the card's notes**

Add to card 61a0d9be's notes: "Kept RUN_ID_TIME_FORMAT, WORKTREE_PARTS, DryRunPlan and RunnerFactory in cli.py's runs import block: tests/test_runs.py MOVED_NAMES reads all 17 moved names via getattr(cli, name), and the spec permits no edit to existing tests beyond monkeypatch retargeting." Also note, if Task 4 Step 6 took the fallback, why the deferred imports stayed.
