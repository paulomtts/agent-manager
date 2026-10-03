<!-- task-pipeline: validated -->
# Add the `run_brd` injection seam to `board.py` (card cf9f8555)

Parent story: 13c63fea "Give board.py an injection seam and a FakeBoard fake". Source decision: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, V3 (lines 118-127). This subtask delivers only the first sentence of V3: the seam and the repointed call sites.

## Scope

In `src/agent_manager/board.py`:

1. Add a module-level seam, defined after `_run` (board.py:137-165), mirroring `GitRunner`/`run_git` in `steps/worktree.py:36,57` and `CommandRunner`/`run_command` in `steps/verify.py:95,118`:
   `run_brd: Callable[[Sequence[str], Path | None, str | None], subprocess.CompletedProcess[str]] = _run`
   V3 writes the second parameter as `Path`. Every public function accepts and forwards `repo_dir: Path | None = None`, so the annotation uses `Path | None` to describe what is actually passed. That is the only change to V3's wording.
2. So that `_run` matches that positional three-argument shape, make `input` positional-or-keyword by dropping the bare `*` from `_run`'s signature. The default stays `None` and the body does not change. The existing keyword caller at `tests/test_board.py:1165` (`board._run(["cat"], None, input=text)`) and the two-argument callers at lines 81, 96, 105 and 116 keep working unmodified.
3. Repoint all six public functions to call `run_brd` instead of `_run`: `show`, `tree`, `roots`, `set_status`, `comment_add` and `comment_list` (board.py:231-370). Pass all three arguments positionally: `run_brd(argv, repo_dir, None)` for reads and for `set_status`, and `run_brd(argv, repo_dir, body)` for `comment_add`. This way a replacement only has to honour the declared positional shape, not `_run`'s parameter names.
4. Each call site must look up `run_brd` as a module global at call time. Do not capture it as a default argument, a closure or a local alias. Otherwise `monkeypatch.setattr(board, "run_brd", fake)` from sibling 19b53ab3 would have no effect.

## Invariants (must not change)

- Public function signatures, return types and envelope handling stay the same. `_decode` (board.py:167) and `_validated` (board.py:219) are untouched, and every payload is still validated through `models.Card`, `models.CardNode` or the comment checks at the process boundary (CLAUDE.md convention).
- Locking stays exactly as it is. In `set_status`, the `run_brd(...)` call stays inside the existing `with write_lock(repo_dir):` block, in the same place `_run` occupies today. `comment_add` still takes no lock. The module docstring (board.py:1-28) and the `WRITE_LOCK` / `write_lock()` structure are unchanged.
- `_run` keeps its behaviour: it raises `BoardError` on `FileNotFoundError` and on a non-zero exit with empty stdout, and it reads `BRD` at argv-build time, so `monkeypatch.setattr(board, "BRD", ...)` at `tests/test_board.py:395` still works.
- The `*_argv` builders are unchanged.

## Observable behaviour and error paths

There is no observable change. With the default binding (`run_brd is _run`), every public function spawns the same argv in the same cwd with the same stdin, and it raises the same `BoardError` (same message, `argv`, `exit_code` and `error_type`) on a missing binary, a bare non-zero exit, non-JSON output, a non-envelope, `ok: false`, an ok envelope with a non-zero exit, missing `data`, a payload of the wrong shape, or a validation failure. A replaced `run_brd` gets its output fed through the same `_decode` → shape check → `_validated` path, so a fake that returns a malformed envelope fails the same way real brd would.

## Out of scope

- `FakeBoard`, any `tests/conftest.py` change, and the brd-tier test that pins `FakeBoard` against real brd. Sibling 19b53ab3 owns all of these and consumes this seam.
- Tier markers, `pyproject.toml` changes and the conftest hooks (V1/V2, other stories).
- pytest-xdist as the verify command; the pygents engine, checkpoint format, harness adapter contract and `dispatch.py`'s `LauncherFn`; the e2e tier's 5 tests; milestone 14's `am run --board`.

## Tests

No new tests and no test edits; the subtask description says so explicitly. The existing suite in `tests/test_board.py` (with the `_run` calls at lines 81, 96, 105, 116 and 1165, and the `BRD` monkeypatch at line 395) and every other caller of the board functions must pass unmodified. Under the placement rule (V1, lines 84-92), these existing tests stay where they are. They execute the real `brd` binary or a PATH-shimmed script, which makes them `brd`/`git`-tier by what they execute. Re-tiering them is not this subtask's job. The first tests that drive public functions through a replaced `run_brd` belong to 19b53ab3. Those that use `FakeBoard` are `unit` tier (injected fake, no subprocess). The `FakeBoard`-vs-real-brd pinning test is `brd` tier (opt-in, `-m brd`).

## Verification

`uv run pytest` is green. There is no lint or typecheck command (CLAUDE.md).

---

# `run_brd` Injection Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `src/agent_manager/board.py` a module-level `run_brd` seam (defaulting to `_run`) that all six public functions call positionally at call time, with no behaviour change.

**Architecture:** `run_brd` is a module global bound to `_run`, declared right after `_run`, typed as a positional three-argument callable. `_run` loses its bare `*` so `input` can be passed positionally. Each public function replaces its `_run(...)` call with `run_brd(argv, repo_dir, None)` (or `body` for `comment_add`), looked up as a global on every call so `monkeypatch.setattr(board, "run_brd", fake)` takes effect.

**Tech Stack:** Python 3 (PEP 604 unions at runtime), `collections.abc.Callable`/`Sequence`, `subprocess`, pytest via `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-add-the-run-brd-cf9f8555-design.md` (prepended verbatim above). Upstream decision: `docs/superpowers/specs/2026-10-02-test-tier-design.md` V3 (lines 118-127).

## Global Constraints

- Branch `m15/task-add-the-run-brd-cf9f8555`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-run-brd-cf9f8555`, cut fresh from `origin/master`. No sibling (19b53ab3) code exists here: there is no `FakeBoard` and no tier markers.
- Seam annotation exactly: `run_brd: Callable[[Sequence[str], Path | None, str | None], subprocess.CompletedProcess[str]] = _run`, defined after `_run`.
- No new tests and no test edits (spec "Tests"). Nothing under `tests/` changes; `tests/conftest.py` and `pyproject.toml` are untouched.
- No change to any public signature, return type, `_decode`, `_validated`, the `*_argv` builders, the module docstring, `WRITE_LOCK` or `write_lock()`.
- Call sites pass all three arguments positionally and reference the module global `run_brd` directly (no default-arg capture, closure or local alias).
- Verification: `uv run pytest` green. No lint or typecheck command exists.

## Review Focus

- A sibling monkeypatches `board.run_brd` after import: every one of the six public functions must route through the replacement (a call-time global lookup), not through `_run`. Pinned by the probe in Task 1 Step 1.
- A replacement that only implements the positional shape `(argv, repo_dir, stdin)` with no `input=` keyword: every call must be positional, and `comment_add` must pass the body as the third argument while all others pass `None`. Pinned by the probe (it records `args` and `kwargs` and asserts `kwargs == {}`).
- `set_status` through the seam must still run under `WRITE_LOCK`, and reads/`comment_add` must not take it. Pinned by the probe recording `board.WRITE_LOCK._is_owned()` inside the fake.
- A replacement returning an `ok: false` envelope must surface as `BoardError` with brd's `error_type`, exactly as real brd would (same `_decode` path). Pinned by the probe.
- `_run` called with `input` positionally must pipe it to stdin, while the existing keyword caller (`tests/test_board.py:1165`) and the `BRD` monkeypatch (`tests/test_board.py:395`) keep working. Pinned by the probe's `cat` check plus the unmodified existing suite in Task 1 Step 6.

Note on the probe: the spec forbids new or edited test files, so the RED/GREEN check for this task is a throwaway inline script run through `uv run python -`. It is not saved to the repository and is not committed. The permanent tests that drive the seam (`unit` tier via `FakeBoard`, and the `brd`-tier pinning test) belong to sibling 19b53ab3.

---

### Task 1: Add the `run_brd` seam and repoint the six public functions

**Files:**
- Modify: `src/agent_manager/board.py:30-35` (imports)
- Modify: `src/agent_manager/board.py:137-165` (`_run` signature, plus the new `run_brd` seam right after it)
- Modify: `src/agent_manager/board.py:239` (`show`), `:254` (`tree`), `:275` (`roots`), `:309` (`set_status`), `:333` (`comment_add`), `:356` (`comment_list`)
- Test: none committed (spec forbids test edits); throwaway probe via `uv run python -`, then the existing `tests/test_board.py` and full suite unmodified.

**Interfaces:**
- Consumes: existing `board._run(argv: list[str], repo_dir: Path | None, input: str | None = None) -> subprocess.CompletedProcess[str]`, `board.show_argv`, `tree_argv`, `roots_argv`, `set_status_argv`, `comment_add_argv`, `comment_list_argv`, `board.BoardError` (with `.error_type`), `board.WRITE_LOCK` (a `threading.RLock`).
- Produces (for sibling 19b53ab3): module global `board.run_brd: Callable[[Sequence[str], Path | None, str | None], subprocess.CompletedProcess[str]]`, default `board._run`. Contract for a replacement: called positionally as `run_brd(argv, repo_dir, stdin)` where `stdin` is `None` for `show`/`tree`/`roots`/`set_status`/`comment_list` and the comment body for `comment_add`; its `.stdout` and `.returncode` go through `_decode` unchanged.

- [ ] **Step 1: Run the throwaway probe to verify it fails (RED)**

From the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-run-brd-cf9f8555`, run (XDG_DATA_HOME is pointed at a temp dir so `set_status`'s lock file does not land in your real data dir):

```bash
XDG_DATA_HOME="$(mktemp -d)" uv run python - <<'EOF'
import inspect
import subprocess
import tempfile
from pathlib import Path

from agent_manager import board

# _run takes input positionally-or-by-keyword.
params = list(inspect.signature(board._run).parameters.values())
assert [p.name for p in params] == ["argv", "repo_dir", "input"], params
assert params[2].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, params[2].kind
assert params[2].default is None
assert board._run(["cat"], None, "hi\n").stdout == "hi\n"
assert board._run(["cat"], None, input="kw\n").stdout == "kw\n"

# The seam exists and defaults to _run.
assert board.run_brd is board._run

repo = Path(tempfile.mkdtemp())
calls = []


def fake(*args, **kwargs):
    calls.append((args, kwargs, board.WRITE_LOCK._is_owned()))
    return subprocess.CompletedProcess(
        list(args[0]),
        0,
        stdout='{"ok": false, "error": {"type": "Probe", "message": "probe"}}',
        stderr="",
    )


board.run_brd = fake  # replaced AFTER import: call sites must look it up at call time

probes = [
    ("show", lambda: board.show("c1", repo_dir=repo), board.show_argv("c1"), None, False),
    ("tree", lambda: board.tree("c1", repo_dir=repo), board.tree_argv("c1"), None, False),
    ("roots", lambda: board.roots(repo_dir=repo), board.roots_argv(), None, False),
    ("set_status", lambda: board.set_status("c1", "done", repo_dir=repo), board.set_status_argv("c1", "done"), None, True),
    ("comment_add", lambda: board.comment_add("c1", "body\nline", author="am", repo_dir=repo), board.comment_add_argv("c1", "am"), "body\nline", False),
    ("comment_list", lambda: board.comment_list("c1", repo_dir=repo), board.comment_list_argv("c1"), None, False),
]
for name, call, argv, stdin, locked in probes:
    calls.clear()
    try:
        call()
    except board.BoardError as exc:
        assert exc.error_type == "Probe", (name, exc.error_type)
        assert exc.message == "probe", (name, exc.message)
    else:
        raise AssertionError(f"{name}: expected BoardError from the fake's ok:false envelope")
    assert calls == [((argv, repo, stdin), {}, locked)], (name, calls)

board.run_brd = board._run
print("PROBE OK")
EOF
```

Expected: FAIL with `AssertionError: <_ParameterKind.KEYWORD_ONLY: 3>` (the bare `*` still makes `input` keyword-only). Even past that line it would fail with `AttributeError: module 'agent_manager.board' has no attribute 'run_brd'`.

- [ ] **Step 2: Add the imports**

In `src/agent_manager/board.py`, replace the import block at lines 30-35:

```python
import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
```

with:

```python
import json
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
```

(`from collections.abc import ...` matches `steps/worktree.py:28` and `steps/verify.py:22`.)

- [ ] **Step 3: Drop the bare `*` from `_run` and add the `run_brd` seam after it**

Replace the `_run` signature (board.py:137-139):

```python
def _run(
    argv: list[str], repo_dir: Path | None, *, input: str | None = None
) -> "subprocess.CompletedProcess[str]":
```

with:

```python
def _run(
    argv: list[str], repo_dir: Path | None, input: str | None = None
) -> "subprocess.CompletedProcess[str]":
```

Leave the docstring and body of `_run` unchanged. Then, immediately after `_run`'s final `return completed` line and before `def _decode(`, insert:

```python
run_brd: Callable[
    [Sequence[str], Path | None, str | None], subprocess.CompletedProcess[str]
] = _run
"""The seam every public function runs `brd` through: `run_brd(argv, repo_dir, stdin)`.

Mirrors `GitRunner`/`run_git` in `steps/worktree.py` and
`CommandRunner`/`run_command` in `steps/verify.py`, but as a module global
rather than a parameter, so no public signature changes. Called positionally
-- `stdin` is the comment body for `comment_add` and `None` everywhere else --
and looked up at call time, so `monkeypatch.setattr(board, "run_brd", fake)`
takes effect. Whatever it returns still goes through `_decode` and
`_validated`, so a replacement's malformed envelope fails exactly as real brd's
would. Defaults to `_run`, which spawns the real binary.
"""
```

- [ ] **Step 4: Repoint the six public functions**

Make exactly these six single-line replacements; nothing else in each function changes.

In `show` (board.py:239):

```python
    completed = _run(argv, repo_dir)
```

becomes:

```python
    completed = run_brd(argv, repo_dir, None)
```

In `tree` (board.py:254), `roots` (board.py:275) and `comment_list` (board.py:356), the same line `    completed = _run(argv, repo_dir)` becomes:

```python
    completed = run_brd(argv, repo_dir, None)
```

In `set_status` (board.py:309), keep it inside the existing `with write_lock(repo_dir):` block at its current indentation:

```python
    with write_lock(repo_dir):
        completed = _run(argv, repo_dir)
```

becomes:

```python
    with write_lock(repo_dir):
        completed = run_brd(argv, repo_dir, None)
```

In `comment_add` (board.py:333):

```python
    completed = _run(argv, repo_dir, input=body)
```

becomes:

```python
    completed = run_brd(argv, repo_dir, body)
```

Afterwards, confirm no public function still calls `_run` directly:

Run: `grep -n "_run(" src/agent_manager/board.py`
Expected: exactly one hit, the `def _run(` line.

Run: `grep -n "run_brd(" src/agent_manager/board.py`
Expected: exactly seven hits -- one in the `run_brd` seam's own docstring (which names its call shape as `run_brd(argv, repo_dir, stdin)`), plus one call site each in `show`, `tree`, `roots`, `set_status`, `comment_add`, `comment_list`.

- [ ] **Step 5: Re-run the throwaway probe to verify it passes (GREEN)**

Run the identical command from Step 1.
Expected: prints `PROBE OK` and exits 0.

- [ ] **Step 6: Run the existing board tests unmodified**

Run: `uv run pytest tests/test_board.py -v`
Expected: all PASS (in particular the `_run` callers at lines 81, 96, 105, 116 and the keyword `input=text` caller at line 1165, and the `BRD` monkeypatches at lines 395, 870, 952, 1081, 1102).

Run: `git status --porcelain tests/`
Expected: empty output (no test file was touched).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: all PASS (the default `addopts = -m "not e2e"` deselects the e2e tier as before).

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/board.py
git commit -m "Add the run_brd injection seam to board.py

All six public board functions now call the module-level run_brd seam
positionally, looked up at call time, instead of _run directly. run_brd
defaults to _run, whose input parameter is now positional-or-keyword to
match the seam's three-argument shape. No behaviour change."
```
