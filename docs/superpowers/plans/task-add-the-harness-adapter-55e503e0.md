<!-- task-pipeline: validated -->
# Spec (verbatim): task-add-the-harness-adapter-55e503e0-design.md

# Subtask 55e503e0 — the harness adapter protocol and the direct launcher

Parent story b4a96f6d ("Roles, the harness adapter protocol and the Claude adapter"), scoped to design spec §8 and decisions D4, D6, D7. Builds on sibling 47bd4ee6 (role bundle loader, `models.py`, `paths.py`), which is done; sibling 9a2524a8 (the Claude adapter) is blocked on this card and owns `harness/claude.py`.

## Scope

Two modules and their tests:

- `src/agent_manager/harness/base.py` — the `HarnessAdapter` Protocol exactly as §8 lines 306-313 writes it, plus the `Outcome` and `Usage` types it and the launcher trade in.
- `src/agent_manager/harness/launcher.py` — the `direct` launcher, plus `bwrap` and `container` as named-but-unimplemented modes (D7, line 72).
- `src/agent_manager/harness/__init__.py` — package marker; re-exports nothing the two modules do not already own.
- One field added to `Dispatch` in `src/agent_manager/models.py`: `timeout`. §8 line 315 states `Dispatch` carries "the prompt text, the role bundle, the cwd, the result path, the model, and a timeout", and the sibling's `Dispatch` has every one of those but the timeout. The launcher needs it and D1's stateless dispatch means it must be recorded, not held in memory. This is the only edit outside `harness/`.

**Not in scope.** No concrete harness: `claude.py`, `codex.py`, `pi.py` are other cards. No `build_command`/`parse_usage` implementation of any kind — only the Protocol that shapes them. No engine, no phase loop, no result-file reading or Pydantic validation of results (that is the engine's step 5 in §6), no capability-matrix routing (the engine refuses at plan time; this module only exposes `capabilities` for it to read). No real confinement. No milestone orchestration, no census/levels/integrate.

## Observable behaviour

**`HarnessAdapter`** is a `typing.Protocol` (runtime-checkable is not required and is not added — structural checks at import time are not a goal) with `name: str`, `capabilities: frozenset[str]`, `build_command(self, d: Dispatch) -> list[str]`, `parse_usage(self, stdout: str) -> Usage | None`. It is a pure interface: no default implementations, no base class anyone inherits from. `build_command` returns an argv list, never a shell string — §5 line 252 is explicit that nothing in this program builds a shell command string. `parse_usage` returns `None` rather than raising when a harness's stdout has no usage in it, because stdout is a log and not a channel (D4) and a chatty or truncated log must not fail an otherwise successful attempt.

**`Usage`** is a Pydantic model (`_Model` conventions: `extra="forbid"`, frozen) with `tokens_in: int | None`, `tokens_out: int | None`, `cost: float | None`, all `ge=0`, all defaulting to `None`. Pydantic rather than a dataclass because it is parsed out of another program's stdout — a process boundary, per CLAUDE.md. Its fields line up one-for-one with `Attempt.tokens_in/tokens_out/cost` so the engine's journalling is a copy.

**`Outcome`** is what the launcher returns and what the engine classifies into §6's four journalled outcomes. Plain dataclass (frozen): it never crosses a process boundary, it is constructed in-process from a completed subprocess. Fields: `argv: list[str]`, `exit_code: int | None`, `timed_out: bool`, `duration: float`, `stdout_path: Path`. `exit_code` is `None` exactly when `timed_out` is true. The engine reads `timed_out or exit_code != 0` as `harness_error`; the third `harness_error` trigger in §6 line 278, a missing result file, is the engine's to detect because the launcher never looks at the result path. `Outcome` carries no result payload and no parsed usage — keeping the launcher blind to both is what lets the same launcher serve every adapter.

**The launcher.** `run_direct(argv: list[str], *, cwd: Path, timeout: float, stdout_path: Path) -> Outcome` starts the process with `cwd` pinned (D7: cwd is the subtask worktree), stdout **and** stderr both written to `stdout_path` opened for writing, stdin closed or attached to devnull so a harness that prompts cannot hang forever, and the timeout enforced. On timeout the process is killed, the partial log is left on disk, and `Outcome(timed_out=True, exit_code=None)` is returned — a timeout is a value, not an exception, because §6 wants it journalled as an attempt rather than propagated. `duration` is wall-clock seconds measured around the call. The launcher creates `stdout_path`'s parent if it is missing but does not invent the path: the engine passes `attempt_dir(run_id, card, phase, attempt) / "stdout.log"` from `paths.py`, which is rooted under `data_dir()` and therefore outside every worktree (§6 line 265).

`LauncherFn` is the injected type — a `Protocol` (or `Callable` alias) with `run_direct`'s exact signature. Nothing in the engine or an adapter imports `run_direct` directly; a launcher is passed in. That injection is the whole point of the seam (§14 line 485).

`get_launcher(kind: Launcher) -> LauncherFn` maps the existing `Launcher = Literal["direct", "bwrap", "container"]` from `models.py` — no new enum is invented — onto an implementation. `"direct"` returns `run_direct`. `"bwrap"` and `"container"` are *named* and raise.

## Error paths

- `get_launcher("bwrap")` / `get_launcher("container")` raise `UnsupportedLauncherError`, a module-level error type carrying the requested `kind` and a message that says the mode is a seam and only `direct` is implemented (D7). They are named in the mapping so the failure is a deliberate refusal at config time, not an unhandled `KeyError` deep in a run.
- `get_launcher` with any other value raises the same error type with a different reason. The `Literal` catches this at type-check time; the runtime guard exists because `RunConfig.launcher` can arrive from a journal line.
- A non-existent or non-directory `cwd` raises rather than returning an `Outcome`: it is a programming error in the engine (the worktree step runs first), not a harness failure, and swallowing it as `harness_error` would burn `max_attempts` re-dispatching into a directory that will never exist.
- A non-positive `timeout` raises `ValueError`. `Dispatch.timeout` is validated `gt=0` at the model, so this only fires when a caller passes one directly.
- A non-zero exit is **not** an error path: it is an `Outcome` with that exit code. Same for a timeout. The launcher raises only for things no retry could fix.
- Adding `timeout` to `Dispatch` keeps `extra="forbid"` intact and gives the field a default so journal lines written before this card still load (the models module's stated contract is that a stale line must fail loudly *or* load — a silently dropped key is what `extra="forbid"` prevents, and a defaulted new field is the compatible direction).

## Test list

Tier per spec §14 lines 477-490 — the governing rule for this card is line 484: "Adapters — `build_command` is pure and asserted per harness; the launcher is injected, so no harness is executed in unit tests." There is no separate testing-standards doc.

`tests/harness/test_base.py` — **unit tier** (pure, in-process, no subprocess at all):

1. A minimal stub adapter defined in the test satisfies `HarnessAdapter` structurally — asserts the Protocol's member set and signatures are what §8 lines 306-313 print, so a later adapter card cannot drift the interface unnoticed.
2. `Usage` accepts a full payload and round-trips it; `Usage()` with everything absent is valid (a harness that reports nothing).
3. `Usage` rejects negative tokens, negative cost, and an unknown key (`extra="forbid"`).
4. `Outcome` is frozen and its fields are readable; an `Outcome` with `timed_out=True` carries `exit_code is None`.

`tests/harness/test_launcher.py` — **unit tier**. §14's prohibition is on executing *a harness*; these tests execute a trivial non-harness child (`sys.executable -c ...`) to exercise the launcher's own contract, which nothing above it can cover once it is injected. Actually running a harness binary stays in the single opt-in, marked, excluded-by-default end-to-end test (§14 lines 489-490), which this card does not add.

5. A command that exits 0 and prints returns `exit_code == 0`, `timed_out is False`, and `stdout_path` contains the printed text.
6. Stderr from the child lands in the same `stdout_path`.
7. A command that exits non-zero returns that exit code and does not raise.
8. A command that sleeps past a short timeout returns `timed_out is True`, `exit_code is None`, and leaves the partial log on disk.
9. `cwd` is honoured — the child reports its own working directory and it matches the tmp dir passed in, not the test process's.
10. `duration` is positive and finite.
11. A missing `cwd` raises; a `timeout <= 0` raises `ValueError`.
12. `get_launcher("direct")` returns a callable satisfying `LauncherFn`.
13. `get_launcher("bwrap")` and `get_launcher("container")` each raise `UnsupportedLauncherError` naming the mode; an unknown string raises it too.

`tests/test_models.py` (extending the sibling's file) — **unit tier**:

14. `Dispatch` accepts a `timeout`, rejects a non-positive one, and still loads a payload that omits it.

No fake adapter or fake launcher fixture ships in this card's tests beyond the stub in test 1 — the fake adapter returning canned result files belongs to the engine card (§14 line 486).

---

# Harness Adapter Protocol and Direct Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `HarnessAdapter` Protocol, the `Usage`/`Outcome` types it and the launcher trade in, and the `direct` launcher behind an injectable seam that names but refuses `bwrap` and `container`.

**Architecture:** `harness/base.py` holds pure interface and data — a `typing.Protocol` with no implementations, a frozen Pydantic `Usage` (parsed from another program's stdout, a process boundary), and a frozen dataclass `Outcome` (built in-process from a finished subprocess). `harness/launcher.py` holds the one implementation, `run_direct`, plus `LauncherFn` (the injected type) and `get_launcher`, which maps the existing `Launcher` literal from `models.py` onto an implementation and raises `UnsupportedLauncherError` for the two unimplemented modes. `models.Dispatch` gains the `timeout` field §8 line 315 names and the sibling card omitted.

**Tech Stack:** Python 3.12+, pydantic 2.9+, pytest 9.1+, `uv` for packaging and test running. Standard-library `subprocess`/`time` only in the launcher — no new dependencies.

**Spec:** `docs/superpowers/specs/task-add-the-harness-adapter-55e503e0-design.md` (reproduced verbatim above), which is itself scoped to `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §8 and decisions D4, D6, D7.

## Global Constraints

- Branch `m1/task-add-the-harness-adapter-55e503e0`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-add-the-harness-adapter-55e503e0`, cut from `origin/m1/task-add-the-role-bundle-47bd4ee6`. Only that sibling's code exists; no other subtask's code may be assumed.
- Source under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md). `tests/harness/` is a new directory with **no** `__init__.py` — `tests/roles/` and `tests/steps/` have none either.
- Verification is `uv run pytest` from the worktree root. There is no lint or typecheck command, so every type-level claim in this plan is additionally pinned by a runtime assertion in a test.
- Pydantic models for anything validated at a process boundary; plain dataclasses for internal-only state (CLAUDE.md). `Usage` is pydantic, `Outcome` is a dataclass.
- `Launcher = Literal["direct", "bwrap", "container"]` already exists in `src/agent_manager/models.py:38`. Do not invent a new enum.
- Only `direct` is implemented (D7, design §3 line 72). `bwrap` and `container` are named in the mapping and raise.
- No concrete harness adapter in this card: `harness/claude.py`, `codex.py`, `pi.py` belong to other cards.
- Heavy narrative module docstrings citing spec section and line numbers, matching `src/agent_manager/roles/loader.py` and `src/agent_manager/models.py`.
- Custom error types carry structured context and build their own message, matching `RoleBundleError` (`src/agent_manager/roles/loader.py:35-47`).

## Review Focus

Five input classes the spec implies but does not pin; each gets a test in the task named:

- An `argv[0]` that does not exist on disk (a harness binary that is not installed) — the launcher must let `FileNotFoundError` propagate rather than reporting it as an ordinary non-zero `Outcome` the engine would retry `max_attempts` times. Task 3.
- An empty `argv` — must be refused with `ValueError` at the top of `run_direct`, not surface as an obscure `IndexError`/`OSError` from inside `subprocess`. Task 3.
- A child process that reads stdin (an interactive harness prompting for confirmation) — stdin at devnull means it reads EOF and exits, instead of hanging until the timeout on every attempt. Task 3.
- A `stdout_path` that already holds a previous attempt's log (a resumed run reusing an attempt directory) — the file must be truncated, so the log read back is this attempt's, not two attempts concatenated. Task 3.
- A `cost` of `inf` or `nan` from a garbled usage line in stdout — `Usage` must reject it, because `inf >= 0` is true and every `nan` comparison is false, so a plain lower bound would let it through into the journal (`Attempt` already guards this with `allow_inf_nan=False`, `src/agent_manager/models.py:71-74`). Task 2.

---

### Task 1: `Dispatch.timeout`

The launcher takes a timeout and D1's stateless dispatch means it has to be recorded, not held in memory. §8 line 315 lists it among `Dispatch`'s contents; `src/agent_manager/models.py:53-61` omits it.

**Files:**
- Modify: `src/agent_manager/models.py:53-61` (the `Dispatch` model)
- Test: `tests/test_models.py` (append; the file already exists from card 47bd4ee6)

**Interfaces:**
- Consumes: `models.Dispatch` and its `_Model` base (`extra="forbid"`), both already present.
- Produces: `models.Dispatch.timeout: float`, default `1800.0`, `gt=0`, `allow_inf_nan=False`. Tasks 3 and 4 use the same `float` seconds unit for `run_direct`'s `timeout` keyword.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py` (after `test_dispatch_requires_harness_and_role`, which ends at line 57):

```python
def test_dispatch_carries_an_explicit_timeout():
    # §8 line 315: a Dispatch carries the prompt, the role, the cwd, the result
    # path, the model *and a timeout*. Seconds, because that is what the
    # launcher hands subprocess.
    dispatch = models.Dispatch(
        harness="claude",
        model="opus",
        role="coder",
        cwd=Path("/repo/wt"),
        prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
        result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
        timeout=90.0,
    )
    assert dispatch.timeout == 90.0


def test_dispatch_defaults_its_timeout_so_older_journal_lines_still_load():
    # A journal line written before this field existed has no `timeout` key.
    # `extra="forbid"` protects against a dropped key; a *new* field has to
    # carry a default or every stored line stops loading.
    payload = _dispatch().model_dump(mode="json")
    del payload["timeout"]
    restored = models.Dispatch.model_validate(payload)
    assert restored.timeout == 1800.0


def test_dispatch_rejects_a_useless_timeout():
    # Zero or negative would kill the harness before it started; inf and nan
    # would sail past a plain lower bound and reach subprocess.wait().
    for bad in (0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ValidationError) as excinfo:
            models.Dispatch(
                harness="claude",
                model="opus",
                role="coder",
                cwd=Path("/repo/wt"),
                prompt_path=Path("/runs/run-1/1535b285/implement.1/prompt.txt"),
                result_path=Path("/runs/run-1/1535b285/implement.1/result.json"),
                timeout=bad,
            )
        assert [error["loc"] for error in excinfo.value.errors()] == [("timeout",)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -k timeout -v`
Expected: FAIL — `test_dispatch_carries_an_explicit_timeout` and `test_dispatch_rejects_a_useless_timeout` raise `ValidationError` for an unexpected keyword `timeout` (`extra="forbid"`), and `test_dispatch_defaults_its_timeout_so_older_journal_lines_still_load` fails with `KeyError: 'timeout'`.

- [ ] **Step 3: Add the field**

In `src/agent_manager/models.py`, replace the `Dispatch` class body:

```python
class Dispatch(_Model):
    """Everything needed to launch one harness process for one attempt."""

    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)
    role: str = Field(min_length=1)
    cwd: Path
    prompt_path: Path
    result_path: Path
    timeout: float = Field(default=1800.0, gt=0, allow_inf_nan=False)
    """Wall-clock seconds the harness gets before it is killed (§8 line 315).

    Defaulted rather than required: journal lines written before this field
    existed carry no `timeout` key, and under `extra="forbid"` a new *required*
    field would stop every stored line from loading. Thirty minutes is a
    deliberately generous ceiling -- it exists to stop a wedged process, not to
    bound a working one.
    """
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, all of it — the pre-existing tests build `Dispatch` without a timeout and must keep passing on the default.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py
git commit -m "feat(models): give Dispatch the timeout §8 names"
```

---

### Task 2: `harness/base.py` — the adapter Protocol, `Usage` and `Outcome`

**Files:**
- Create: `src/agent_manager/harness/__init__.py`
- Create: `src/agent_manager/harness/base.py`
- Create: `tests/harness/test_base.py`

**Interfaces:**
- Consumes: `models.Dispatch` (with `timeout`, Task 1).
- Produces, for Tasks 3 and 4 and for the Claude-adapter card:
  - `harness.base.Usage(tokens_in: int | None = None, tokens_out: int | None = None, cost: float | None = None)` — frozen pydantic, `extra="forbid"`.
  - `harness.base.Outcome(argv: list[str], exit_code: int | None, timed_out: bool, duration: float, stdout_path: Path)` — frozen dataclass, keyword-constructible, fields in that order.
  - `harness.base.HarnessAdapter` — `typing.Protocol` with `name: str`, `capabilities: frozenset[str]`, `build_command(self, d: Dispatch) -> list[str]`, `parse_usage(self, stdout: str) -> Usage | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/test_base.py`:

```python
"""Behaviour of the harness adapter protocol and its data types (design §8
lines 306-318, card 55e503e0).

Unit tier per design §14 line 484 ("Adapters -- `build_command` is pure and
asserted per harness; the launcher is injected, so no harness is executed in
unit tests"). Nothing here spawns a process or touches disk: the module is a
Protocol and two value types. The stub adapter below is the interface's only
consumer in this card -- the real adapters are sibling cards, and the fake
adapter that returns canned result files belongs to the engine card (§14
line 486).
"""

import dataclasses
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_manager.harness import base
from agent_manager.models import Dispatch


class StubAdapter:
    """The smallest thing that is a `HarnessAdapter`. Structural only: it
    inherits from nothing."""

    name = "stub"
    capabilities = frozenset({"browser"})

    def build_command(self, d: Dispatch) -> list[str]:
        return [self.name, "--model", d.model, "--result", str(d.result_path)]

    def parse_usage(self, stdout: str) -> base.Usage | None:
        return None


def _dispatch() -> Dispatch:
    return Dispatch(
        harness="stub",
        model="opus",
        role="coder",
        cwd=Path("/repo/wt"),
        prompt_path=Path("/runs/run-1/55e503e0/implement.1/prompt.txt"),
        result_path=Path("/runs/run-1/55e503e0/implement.1/result.json"),
        timeout=60.0,
    )


def test_the_protocol_declares_exactly_the_four_members_the_spec_prints():
    # §8 lines 306-313 are the contract every adapter card codes against. A
    # fifth member added here, or a rename, would silently break a sibling
    # adapter written against the printed version.
    assert set(base.HarnessAdapter.__protocol_attrs__) == {
        "name",
        "capabilities",
        "build_command",
        "parse_usage",
    }


def test_the_protocol_methods_have_the_signatures_the_spec_prints():
    # `base.py` has no `from __future__ import annotations`, so these come back
    # evaluated rather than as strings. That is deliberate: the objects are
    # what a sibling adapter card has to match.
    build = inspect.signature(base.HarnessAdapter.build_command)
    assert list(build.parameters) == ["self", "d"]
    assert build.parameters["d"].annotation is Dispatch
    assert build.return_annotation == list[str]

    parse = inspect.signature(base.HarnessAdapter.parse_usage)
    assert list(parse.parameters) == ["self", "stdout"]
    assert parse.parameters["stdout"].annotation is str
    assert parse.return_annotation == base.Usage | None


def test_a_structural_stub_satisfies_the_adapter_interface():
    adapter: base.HarnessAdapter = StubAdapter()
    assert adapter.name == "stub"
    assert adapter.capabilities == frozenset({"browser"})
    argv = adapter.build_command(_dispatch())
    assert argv == [
        "stub",
        "--model",
        "opus",
        "--result",
        "/runs/run-1/55e503e0/implement.1/result.json",
    ]
    # §5 line 252: an argv list, never a shell string.
    assert all(isinstance(word, str) for word in argv)
    assert adapter.parse_usage("no usage in this log") is None


def test_the_protocol_is_not_runtime_checkable():
    # Deliberate: structural isinstance() checks are not a goal, and a
    # runtime_checkable Protocol only checks member *presence*, which would
    # read as a guarantee it cannot give.
    with pytest.raises(TypeError):
        isinstance(StubAdapter(), base.HarnessAdapter)


def test_usage_round_trips_a_full_payload():
    usage = base.Usage(tokens_in=8000, tokens_out=1500, cost=0.31)
    assert usage.tokens_in == 8000
    assert usage.tokens_out == 1500
    assert usage.cost == 0.31
    assert base.Usage.model_validate(usage.model_dump(mode="json")) == usage


def test_usage_is_empty_when_a_harness_reports_nothing():
    # D4: stdout is a log, not a channel. A harness that prints no usage line
    # still ran fine, so every field is optional.
    usage = base.Usage()
    assert usage.tokens_in is None
    assert usage.tokens_out is None
    assert usage.cost is None


def test_usage_fields_match_the_attempt_fields_they_are_copied_into():
    # The engine's journalling is a straight copy; a rename here would make it
    # a translation nobody wrote.
    assert set(base.Usage.model_fields) == {"tokens_in", "tokens_out", "cost"}


def test_usage_is_frozen():
    usage = base.Usage(tokens_in=10)
    with pytest.raises(ValidationError):
        usage.tokens_in = 20


def test_usage_rejects_negative_counts_and_cost():
    for field, value in (("tokens_in", -1), ("tokens_out", -1), ("cost", -0.01)):
        with pytest.raises(ValidationError) as excinfo:
            base.Usage(**{field: value})
        assert [error["loc"] for error in excinfo.value.errors()] == [(field,)]


def test_usage_rejects_an_infinite_or_nan_cost():
    # A garbled stdout line can parse to inf or nan. `inf >= 0` is true and
    # every nan comparison is false, so a plain lower bound would let both
    # through into the journal.
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            base.Usage(cost=bad)


def test_usage_rejects_an_unknown_key():
    # A harness that renames its usage field must fail loudly, not report zero.
    with pytest.raises(ValidationError) as excinfo:
        base.Usage(total_tokens=9500)
    assert "total_tokens" in str(excinfo.value)


def test_outcome_carries_what_the_engine_classifies_on():
    outcome = base.Outcome(
        argv=["claude", "-p"],
        exit_code=0,
        timed_out=False,
        duration=12.5,
        stdout_path=Path("/runs/run-1/55e503e0/implement.1/stdout.log"),
    )
    assert outcome.argv == ["claude", "-p"]
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert outcome.duration == 12.5
    assert outcome.stdout_path.name == "stdout.log"


def test_a_timed_out_outcome_has_no_exit_code():
    # §6 line 278: timeout is one of the three harness_error triggers, and a
    # killed process has no exit status of its own to report.
    outcome = base.Outcome(
        argv=["claude", "-p"],
        exit_code=None,
        timed_out=True,
        duration=1800.0,
        stdout_path=Path("/runs/run-1/55e503e0/implement.1/stdout.log"),
    )
    assert outcome.timed_out is True
    assert outcome.exit_code is None


def test_outcome_is_frozen():
    outcome = base.Outcome(
        argv=["claude"],
        exit_code=1,
        timed_out=False,
        duration=0.5,
        stdout_path=Path("/tmp/stdout.log"),
    )
    assert dataclasses.is_dataclass(outcome)
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.exit_code = 0


def test_outcome_carries_no_result_payload_and_no_usage():
    # The launcher never opens the result file and never parses usage; keeping
    # both off this type is what lets one launcher serve every adapter.
    fields = {field.name for field in dataclasses.fields(base.Outcome)}
    assert fields == {"argv", "exit_code", "timed_out", "duration", "stdout_path"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_base.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'agent_manager.harness'`.

- [ ] **Step 3: Create the package marker**

Create `src/agent_manager/harness/__init__.py`:

```python
"""Harness adapters and the launcher seam (design §4 lines 129-132, §8 lines
306-318, decisions D4 and D7).

A package marker only. `base.py` owns the adapter Protocol and the value types;
`launcher.py` owns the one launcher that is implemented. Re-exporting either
from here would give two import paths for one name, and a later adapter card
would pick whichever it saw first.
"""
```

- [ ] **Step 4: Write the module**

Create `src/agent_manager/harness/base.py`:

```python
"""The harness adapter interface and the two value types it trades in.

Design §8 lines 306-313 print `HarnessAdapter` as a Protocol, and this module
is that Protocol verbatim plus the `Usage` and `Outcome` it names. It is pure
interface and pure data: no adapter is implemented here (`claude.py`,
`codex.py`, `pi.py` are their own cards), nothing is launched here
(`launcher.py` owns that), and nothing reads the result file (§6 step 5 is the
engine's).

The split between the two value types is the CLAUDE.md rule applied twice.
`Usage` is parsed out of another program's stdout -- a process boundary -- so
it is pydantic and validates. `Outcome` is built in-process from a subprocess
that has already finished, so it is a plain frozen dataclass; validating it
would only re-check values this program just produced.

`Outcome` deliberately carries no result payload and no parsed usage. The
launcher never opens the result path and never reads the log it wrote, which is
what lets one launcher serve every adapter, and what leaves §6 line 278's third
harness_error trigger -- a missing result file -- to the engine.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent_manager.models import Dispatch


class Usage(BaseModel):
    """What one attempt cost, as reported by the harness's own log.

    Frozen and `extra="forbid"` for the same reason the state models are: a
    harness that renames its usage field must fail loudly rather than journal a
    silent zero. Every field is optional because D4 makes stdout a log and not
    a channel -- a chatty, truncated or usage-free log must not fail an
    otherwise successful attempt, so `parse_usage` returns `Usage()` or `None`
    rather than raising.

    The field names match `Attempt.tokens_in`/`tokens_out`/`cost`
    (models.py lines 72-74) one for one, so the engine's journalling is a copy
    and not a translation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)


@dataclass(frozen=True)
class Outcome:
    """The result of running one harness process, before anyone classifies it.

    The engine turns this into one of §6 line 277's four journalled outcomes:
    `timed_out or exit_code != 0` is `harness_error`, and the remaining
    distinctions (`ok`, `schema_invalid`, `gate_failed`, and the missing-result
    -file flavour of `harness_error`) come from the result file, which this
    type never sees.

    `exit_code` is `None` exactly when `timed_out` is true: a process the
    launcher killed has no exit status of its own to report, and `-9` would be
    indistinguishable from a harness that genuinely died of SIGKILL.
    """

    argv: list[str]
    exit_code: int | None
    timed_out: bool
    duration: float
    stdout_path: Path


class HarnessAdapter(Protocol):
    """What every harness adapter must provide (§8 lines 306-313, verbatim).

    Pure interface: no default implementations and no base class anyone
    inherits from, so an adapter is a `HarnessAdapter` by shape alone. Not
    `runtime_checkable` -- a runtime check would only assert member presence,
    which reads as a guarantee it cannot give, and nothing in this program
    needs to ask.

    `build_command` returns an argv list, never a shell string: §5 line 252 is
    explicit that nothing here builds a command string for anything to run
    verbatim.

    `parse_usage` returns `None` rather than raising when the log holds no
    usage. `capabilities` is data for the engine's plan-time capability check
    (§8 lines 336-339); methodology is never a capability, that is what
    vendoring is for.
    """

    name: str
    capabilities: frozenset[str]

    def build_command(self, d: Dispatch) -> list[str]: ...
    def parse_usage(self, stdout: str) -> Usage | None: ...
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_base.py -v`
Expected: PASS (15 tests).

Do not add `from __future__ import annotations` to `base.py` — it would turn the annotations `test_the_protocol_methods_have_the_signatures_the_spec_prints` inspects into bare strings, and the point of that test is to pin the evaluated types a sibling adapter has to match.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/harness/__init__.py src/agent_manager/harness/base.py tests/harness/test_base.py
git commit -m "feat(harness): add the adapter protocol, Usage and Outcome"
```

---

### Task 3: `run_direct` — the one implemented launcher

**Files:**
- Create: `src/agent_manager/harness/launcher.py`
- Create: `tests/harness/test_launcher.py`

**Interfaces:**
- Consumes: `harness.base.Outcome` (Task 2).
- Produces, for Task 4 and the engine card: `harness.launcher.run_direct(argv: list[str], *, cwd: Path, timeout: float, stdout_path: Path) -> Outcome`.

These tests run a trivial `sys.executable -c ...` child. That is not a harness: §14 line 485's prohibition is on executing *a harness*, and a real harness binary appears only in §14 line 489's opt-in end-to-end test, which this card does not add. The launcher's own contract cannot be covered from above once it is injected, so it is covered here.

- [ ] **Step 1: Write the failing tests for the happy paths**

Create `tests/harness/test_launcher.py`:

```python
"""Behaviour of the direct launcher (design §6 step 4, decision D7, card
55e503e0).

Unit tier per design §14 line 484. The children below are `sys.executable -c`
one-liners -- deterministic, dependency-free, and emphatically not harnesses.
§14's rule is that no *harness* is executed in unit tests because the launcher
is injected everywhere above this module; running a real harness binary is
reserved for the single opt-in, excluded-by-default end-to-end test in §14
lines 489-490, which this card does not add. The launcher's own contract has no
other place to be tested.
"""

import math
import sys
from pathlib import Path

import pytest

from agent_manager.harness import launcher


def test_a_successful_command_reports_exit_zero_and_captures_stdout(tmp_path):
    # The stdout.log parent is deliberately absent: the engine passes
    # `attempt_dir(...) / "stdout.log"` and the launcher must be willing to
    # create the directory it was handed.
    log = tmp_path / "implement.1" / "stdout.log"
    outcome = launcher.run_direct(
        [sys.executable, "-c", "print('hello from the child')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert outcome.stdout_path == log
    assert outcome.argv[0] == sys.executable
    assert "hello from the child" in log.read_text()


def test_stderr_is_merged_into_the_same_log(tmp_path):
    # One log per attempt (§6 line 270). A harness's diagnostics are the most
    # useful thing in it when an attempt fails, so they must not go to the
    # manager's own stderr, where nothing journals them.
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('a warning\\n'); print('a result')",
        ],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    assert outcome.exit_code == 0
    text = log.read_text()
    assert "a warning" in text
    assert "a result" in text


def test_a_non_zero_exit_is_a_value_not_an_exception(tmp_path):
    # §6 line 278 wants a non-zero exit journalled as harness_error, which
    # means the engine has to receive it as data it can record.
    outcome = launcher.run_direct(
        [sys.executable, "-c", "raise SystemExit(3)"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
    )
    assert outcome.exit_code == 3
    assert outcome.timed_out is False


def test_a_timeout_kills_the_child_and_returns_a_value(tmp_path):
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [
            sys.executable,
            "-c",
            "import time; print('before the sleep', flush=True); time.sleep(30)",
        ],
        cwd=tmp_path,
        timeout=0.5,
        stdout_path=log,
    )
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    # The partial log is the whole point of leaving it on disk: it is the only
    # evidence of what the harness was doing when the clock ran out.
    assert "before the sleep" in log.read_text()
    # The kill actually happened -- we did not just wait out the 30s sleep.
    assert outcome.duration < 20.0


def test_the_child_runs_in_the_cwd_it_was_given(tmp_path):
    # D7: cwd pinned to the subtask worktree is the isolation, so a launcher
    # that silently inherited the manager's cwd would run every harness in the
    # wrong repository.
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    log = tmp_path / "stdout.log"
    launcher.run_direct(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=worktree,
        timeout=30.0,
        stdout_path=log,
    )
    assert Path(log.read_text().strip()).resolve() == worktree.resolve()


def test_duration_is_positive_and_finite(tmp_path):
    outcome = launcher.run_direct(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
    )
    assert outcome.duration > 0
    assert math.isfinite(outcome.duration)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_launcher.py -v`
Expected: FAIL at collection — `ImportError: cannot import name 'launcher' from 'agent_manager.harness'`.

- [ ] **Step 3: Write `run_direct`**

Create `src/agent_manager/harness/launcher.py`:

```python
"""How a harness process is actually started (design §4 line 132, decision D7).

D7 says v1 launches harnesses full-auto with cwd pinned to the subtask
worktree, and puts confinement behind a seam: the adapter takes a launcher,
`direct` | `bwrap` | `container`, and only `direct` is implemented. This module
is that seam. The seam, not the confinement, is the deliverable here.

Everything this module does is deliberately blind to what it is running. It
never reads the result file (§6 step 5 is the engine's), never parses the log
it wrote (that is `parse_usage`'s job, on the adapter), and never builds a
shell string (§5 line 252). That blindness is what lets one launcher serve
every adapter.

The line between raising and returning is drawn on retryability. A non-zero
exit and a timeout are `Outcome`s, because §6 wants them journalled as attempts
and a re-dispatch might well succeed. A missing worktree, an empty argv and a
useless timeout are raised, because no retry fixes them and swallowing them as
`harness_error` would burn `max_attempts` re-dispatching into a situation that
cannot change.
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

from agent_manager.harness.base import Outcome
from agent_manager.models import Launcher


class UnsupportedLauncherError(RuntimeError):
    """A launcher mode that is named but cannot run.

    Carries the requested `kind` and a reason, matching `RoleBundleError`'s
    shape: the caller journals the message, and "unsupported" without the mode
    name is unactionable when three modes exist.
    """

    def __init__(self, kind: str, *, reason: str) -> None:
        self.kind = kind
        self.reason = reason
        super().__init__(f"launcher {kind!r}: {reason}")


class LauncherFn(Protocol):
    """The injected launcher type (§14 line 485).

    Nothing in the engine or an adapter imports `run_direct`; a launcher is
    passed in, which is what lets every test above this module run without
    spawning anything. The Protocol exists so the injection point has a name
    with the real signature on it.
    """

    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: float,
        stdout_path: Path,
    ) -> Outcome: ...


def run_direct(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    stdout_path: Path,
) -> Outcome:
    """Run `argv` in `cwd`, log to `stdout_path`, kill it after `timeout`.

    `stdout_path` is supplied, not derived: the engine passes
    `attempt_dir(run_id, card, phase, attempt) / "stdout.log"`, which
    `paths.py` roots under `data_dir()` and therefore outside every worktree
    (§6 line 265). Deriving a path here would put a second opinion about run
    layout in a module that has no business holding one. The parent directory
    is created because the caller may hand over a path whose directory does not
    exist yet.

    stdin is devnull: a harness that stops to ask a question reads EOF and
    exits, instead of hanging until the timeout on every single attempt.

    The log is opened for writing, not appending: a resumed run may reuse an
    attempt directory, and a log holding two attempts concatenated is worse
    evidence than a log holding the current one.
    """
    if not argv:
        raise ValueError("launcher argv is empty: there is no program to run")
    if not cwd.is_dir():
        raise NotADirectoryError(
            f"launcher cwd is not a directory: {cwd} -- the worktree step runs "
            f"before any dispatch, so this is a bug above the launcher, not a "
            f"harness failure"
        )
    if not timeout > 0:  # also rejects nan, for which every comparison is false
        raise ValueError(f"launcher timeout must be positive, got {timeout!r}")

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with stdout_path.open("wb") as log, open(os.devnull, "rb") as devnull:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=devnull,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            exit_code: int | None = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            # Reap it, so the wait status is collected and the log file has no
            # writer left when we close it.
            process.wait()
            exit_code = None
            timed_out = True

    return Outcome(
        argv=list(argv),
        exit_code=exit_code,
        timed_out=timed_out,
        duration=time.monotonic() - started,
        stdout_path=stdout_path,
    )
```

(`Launcher` is imported here for Task 4's `get_launcher` signature; if a linter-free run of Task 3 alone flags it as unused, leave it — Task 4 uses it and the module is committed whole.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_launcher.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Write the failing tests for the refusals and the awkward inputs**

Append to `tests/harness/test_launcher.py`:

```python
def test_a_missing_or_non_directory_cwd_is_refused(tmp_path):
    # Refusing beats returning an Outcome: classified as harness_error it would
    # be re-dispatched max_attempts times into a directory that never appears.
    with pytest.raises(NotADirectoryError):
        launcher.run_direct(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path / "never-created",
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )

    a_file = tmp_path / "not-a-worktree.txt"
    a_file.write_text("")
    with pytest.raises(NotADirectoryError):
        launcher.run_direct(
            [sys.executable, "-c", "pass"],
            cwd=a_file,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_useless_timeout_is_refused(tmp_path):
    for bad in (0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            launcher.run_direct(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=bad,
                stdout_path=tmp_path / "stdout.log",
            )


def test_an_empty_argv_is_refused(tmp_path):
    # Without the guard this surfaces from inside subprocess as an IndexError
    # naming nothing the operator can act on.
    with pytest.raises(ValueError):
        launcher.run_direct(
            [],
            cwd=tmp_path,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_missing_executable_propagates_rather_than_looking_like_an_exit(tmp_path):
    # A harness binary that is not installed is a configuration problem. If it
    # came back as an ordinary non-zero Outcome the engine would retry it
    # max_attempts times and journal harness_error with no hint why.
    with pytest.raises(FileNotFoundError):
        launcher.run_direct(
            [str(tmp_path / "no-such-harness"), "-p"],
            cwd=tmp_path,
            timeout=30.0,
            stdout_path=tmp_path / "stdout.log",
        )


def test_a_child_that_reads_stdin_gets_eof_instead_of_hanging(tmp_path):
    # An interactive harness prompting for confirmation must not burn the whole
    # timeout on every attempt.
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [sys.executable, "-c", "import sys; print('stdin gave', repr(sys.stdin.read()))"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert "stdin gave ''" in log.read_text()


def test_an_existing_log_from_a_previous_attempt_is_truncated(tmp_path):
    # A resumed run can reuse an attempt directory. Two attempts concatenated
    # is worse evidence than one.
    log = tmp_path / "stdout.log"
    log.write_text("output from the attempt before the crash\n")
    launcher.run_direct(
        [sys.executable, "-c", "print('this attempt')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
    )
    text = log.read_text()
    assert "this attempt" in text
    assert "before the crash" not in text
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_launcher.py -v`
Expected: PASS (12 tests). The guards written in Step 3 already cover every one of these; if any fails, the guard is wrong and the source is what changes, not the test.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/harness/launcher.py tests/harness/test_launcher.py
git commit -m "feat(harness): add the direct launcher"
```

---

### Task 4: `get_launcher` — the seam that names `bwrap` and `container`

**Files:**
- Modify: `src/agent_manager/harness/launcher.py` (append `get_launcher` and its mapping; `UnsupportedLauncherError` and `LauncherFn` already landed in Task 3)
- Test: `tests/harness/test_launcher.py` (append)

**Interfaces:**
- Consumes: `models.Launcher` (`Literal["direct", "bwrap", "container"]`, `src/agent_manager/models.py:38`), `launcher.run_direct`, `launcher.LauncherFn`, `launcher.UnsupportedLauncherError` (Task 3).
- Produces: `harness.launcher.get_launcher(kind: Launcher) -> LauncherFn`. The engine card calls this once with `RunConfig.launcher` and injects the result.

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/test_launcher.py`:

```python
def test_get_launcher_direct_returns_the_direct_launcher(tmp_path):
    fn = launcher.get_launcher("direct")
    assert fn is launcher.run_direct
    # It is usable through the injected type's call shape, keyword-only args
    # and all -- the engine never calls run_direct by name.
    injected: launcher.LauncherFn = fn
    outcome = injected(
        [sys.executable, "-c", "print('injected')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
    )
    assert outcome.exit_code == 0


@pytest.mark.parametrize("kind", ["bwrap", "container"])
def test_the_unimplemented_modes_are_named_and_refuse(kind):
    # D7: they are in the mapping on purpose. A deliberate refusal at config
    # time beats a KeyError surfacing mid-run, and it keeps the two names
    # discoverable as the seam they are.
    with pytest.raises(launcher.UnsupportedLauncherError) as excinfo:
        launcher.get_launcher(kind)
    assert excinfo.value.kind == kind
    message = str(excinfo.value)
    assert kind in message
    assert "direct" in message


def test_an_unknown_launcher_name_raises_the_same_error_type():
    # The Literal catches this at type-check time; the runtime guard exists
    # because RunConfig.launcher can arrive from a journal line written by an
    # older or newer build.
    with pytest.raises(launcher.UnsupportedLauncherError) as excinfo:
        launcher.get_launcher("docker")
    assert excinfo.value.kind == "docker"
    for known in ("direct", "bwrap", "container"):
        assert known in str(excinfo.value)


def test_every_launcher_literal_member_is_accounted_for():
    # If models.Launcher grows a fourth mode, this fails rather than letting it
    # fall through to "unknown launcher" at run time.
    from typing import get_args

    from agent_manager.models import Launcher

    assert set(get_args(Launcher)) == set(launcher.LAUNCHERS)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_launcher.py -k get_launcher -v`
Expected: FAIL with `AttributeError: module 'agent_manager.harness.launcher' has no attribute 'get_launcher'` (and `LAUNCHERS` for the last test).

- [ ] **Step 3: Write the mapping and the lookup**

Append to `src/agent_manager/harness/launcher.py`:

```python
LAUNCHERS: dict[str, LauncherFn | None] = {
    "direct": run_direct,
    "bwrap": None,
    "container": None,
}
"""Every mode `models.Launcher` names, mapped to its implementation or `None`.

The two `None`s are the seam, spelled out. Leaving `bwrap` and `container` out
of this dict entirely would make asking for one an "unknown launcher" -- which
is wrong, they are known, they are simply not built -- and would lose the only
place in the code where D7's deferred work is visible.
"""


def get_launcher(kind: Launcher) -> LauncherFn:
    """Resolve a launcher mode to the function the engine will inject.

    Called once, at run start, with `RunConfig.launcher`. Failing here means
    failing before a single worktree is created, which is the whole reason the
    unimplemented modes are named rather than omitted.
    """
    if kind not in LAUNCHERS:
        raise UnsupportedLauncherError(
            kind,
            reason=(
                "not a launcher mode; the modes are direct, bwrap and container"
            ),
        )
    implementation = LAUNCHERS[kind]
    if implementation is None:
        raise UnsupportedLauncherError(
            kind,
            reason=(
                "is a seam, not an implementation -- v1 implements only "
                "direct, which launches with permissions bypassed and cwd "
                "pinned to the subtask worktree (D7)"
            ),
        )
    return implementation
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_launcher.py -v`
Expected: PASS (17 tests -- 12 from Task 3 plus 5 new test functions here, one of which is parametrized over `["bwrap", "container"]` and so collects as 2 items).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — this card's 17 launcher tests, 15 base tests and 3 new model tests, plus every test card 47bd4ee6 left green.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/harness/launcher.py tests/harness/test_launcher.py
git commit -m "feat(harness): resolve launcher modes, refusing the two unbuilt seams"
```
