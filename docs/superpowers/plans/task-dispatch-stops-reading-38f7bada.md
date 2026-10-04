<!-- task-pipeline: validated -->
# Subtask 38f7bada — dispatch stops reading harness usage into the journalled attempt

Parent story: d5aa963b "Remove dead cost/token tracking". Milestone design: `docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md` (§3.1, §4.1, §4.5, §5.2, §5.3 item 6). This is the safe first slice: nothing downstream yet assumes the `Attempt` cost/token fields are gone.

## Scope

In `src/agent_manager/dispatch.py`:

- Delete the `_usage(adapter, outcome) -> Usage | None` helper (opens `outcome.stdout_path`, swallows `OSError`, calls `adapter.parse_usage`).
- Delete its call site (`usage = _usage(target.adapter, outcome)`) and the three kwargs it feeds into the journalled `models.Attempt(...)`: `tokens_in=...`, `tokens_out=...`, `cost=...`. The attempt is built from `exit_code`, `duration`, status, dispatch, and paths only; the three fields fall back to their model defaults (`None`).
- In `from agent_manager.harness.base import HarnessAdapter, Outcome, Usage`, drop `Usage` only.
- Reword the module docstring sentence "stdout.log is captured as a log and is only ever handed to parse_usage, never parsed for a result" to say it is captured as a log and never read by the engine.

In `src/agent_manager/harness/launcher.py`: reword the docstring clause "never parses the log it wrote (that is parse_usage's job, on the adapter)" so it says nothing parses it (D4).

No deprecation shims, no placeholder variables (§4.1): delete outright.

## Out of scope (owned by siblings)

- 3ebd08fb (blocked on this subtask): deleting `Attempt.tokens_in/tokens_out/cost` from `models.py`, the `attempts` table columns in `store.py`, `load_run`/`_write_attempt_row` column lists, the `_RETIRED_ATTEMPT_KEYS` replay shim. Do not touch `models.py` or `store.py`.
- 7988f1db: deleting `harness.base.Usage`, `HarnessAdapter.parse_usage`, `ClaudeAdapter.parse_usage` and its regex helpers, and every fake-adapter `parse_usage` stub (including `FakeAdapter.parse_usage` in `tests/test_dispatch.py`). Do not touch `harness/base.py` or `harness/claude.py`.

## Observable behavior

- The engine never opens `outcome.stdout_path`. A launcher that leaves the log absent or unreadable produces exactly the same attempt as one that writes it.
- The journalled terminal `attempt_upsert` payload carries the launcher's `duration` and `exit_code` unchanged; `tokens_in`, `tokens_out`, `cost` are always `None` (the keys are still present because `Attempt` still declares them and the payload is `attempt.model_dump(mode="json")`, see store.py around line 1463).
- `adapter.parse_usage` is never called by dispatch, even though adapters still implement it.

Error paths: the only one that existed (`OSError` reading the log, swallowed) disappears with the read. No new error paths.

## Tests (all in `tests/test_dispatch.py`)

Test fixture changes:
- `FakeLauncher.stdout` default and the text `_outcome` writes: change `"usage: tokens\n"` to the neutral `"fake-harness ran\n"` (§3.1). The old string was bait for `parse_usage`.
- `FakeAdapter.parse_usage` stays for now (7988f1db removes it), so the `Usage` import on line 33 stays only while that stub still builds a `Usage`. Check this when implementing.

Tests:
1. Replace `test_usage_parsed_from_the_log_is_journalled_on_the_attempt` with `test_the_outcome_is_journalled_on_the_attempt`. The terminal `ok` `attempt_upsert` payload has `duration == 1.25` and `exit_code == 0`, and `tokens_in`, `tokens_out`, `cost` are all `None`. **Tier: unit (unmarked)**, driven through `FakeLauncher` and `FakeAdapter`, no subprocess.
   - Deviation from §5.2, required by this slice: §5.2's `set(payload) & {"tokens_in", "tokens_out", "cost"} == set()` cannot pass until 3ebd08fb removes the fields from `Attempt`. In this slice, assert the three values are `None`. The model still makes `FakeAdapter.parse_usage` return `Usage(11, 22, 0.5)` when "usage" appears in the log, so to show that nothing reads the log, the test must use a `FakeLauncher(stdout="usage: tokens\n", ...)` override. With that override, the `None` assertion proves dispatch no longer consults the adapter. 3ebd08fb tightens the assertion to key-absence.
2. New `test_the_engine_never_opens_the_harness_log` (§5.3 item 6): `FakeLauncher` returns an `Outcome` whose `stdout_path` points at a file that does not exist (a subclass or flag that skips the write). The phase still yields an `ok` attempt with `duration == 1.25`. **Tier: unit (unmarked)**, no subprocess.
3. Any other existing assertion in this file that loses a usage/cost check asserts `duration` instead (§4.5). Coverage is moved there, not dropped. **Tier: unchanged (unit)**.

Verification: `uv run pytest` (default unit + git tiers) passes.

---

# Dispatch Stops Reading Harness Usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `AgentRunner` stops opening `stdout.log` and stops asking the adapter for usage, so the journalled attempt carries only the launcher's `duration`/`exit_code` and `None` for `tokens_in`/`tokens_out`/`cost`.

**Architecture:** One deletion in `src/agent_manager/dispatch.py` (the `_usage` helper, its call site, its three `models.Attempt` kwargs, and `Usage` from the import), two docstring rewordings (`dispatch.py` module docstring, `harness/launcher.py` module docstring), and test changes confined to `tests/test_dispatch.py`. `models.py`, `store.py`, `harness/base.py`, `harness/claude.py` and `FakeAdapter.parse_usage` are not touched.

**Tech Stack:** Python, pytest, Pydantic, `uv`.

**Spec:** `docs/superpowers/specs/task-dispatch-stops-reading-38f7bada-design.md` (prepended above, verbatim). Milestone spec: `docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md`.

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-dispatch-stops-reading-38f7bada` on `m19/task-dispatch-stops-reading-38f7bada`. Every path below is relative to that worktree root. Run every command from that directory. Do not assume any other subtask (3ebd08fb, 7988f1db) has landed: `Attempt` still declares `tokens_in`/`tokens_out`/`cost`, and `Usage` / `parse_usage` still exist.

## Global Constraints

- No deprecation shims, no `Optional` placeholders, no leftover `usage = None` variables — delete outright (§4.1).
- Do not edit `src/agent_manager/models.py`, `src/agent_manager/store.py`, `src/agent_manager/harness/base.py`, `src/agent_manager/harness/claude.py`.
- Do not delete `FakeAdapter.parse_usage` in `tests/test_dispatch.py` (7988f1db owns that); keep the `Usage` import in `tests/test_dispatch.py` because that stub still constructs `Usage(tokens_in=11, tokens_out=22, cost=0.5)`.
- Replacement bait-free log text is exactly `"fake-harness ran\n"`.
- Every test that loses a usage/cost assertion asserts `duration` instead (§4.5).
- All new/changed tests are unit tier: unmarked, in `tests/test_dispatch.py`, driven through `FakeLauncher`/`FakeAdapter`; no `@pytest.mark.git` or other marker; no subprocess.
- Verification command: `uv run pytest`.

## Review Focus

- Log absent after the launcher returns (the launcher crashed before writing, or cleaned up): the attempt must still be `ok` with the launcher's `duration`. Pinned by `test_the_engine_never_opens_the_harness_log[absent]` in Task 1.
- Log path exists but is a directory (unreadable as text, would raise `IsADirectoryError`): same `ok` attempt, no read attempted. Pinned by `test_the_engine_never_opens_the_harness_log[directory]` in Task 1.
- Log contains non-UTF-8 bytes that still decode (under `errors="replace"`) to text containing "usage": no cost/token values may leak into the attempt. Pinned by `test_the_engine_never_opens_the_harness_log[non_utf8]` in Task 1.
- An adapter whose `parse_usage` raises: dispatch must not call it, so the phase still succeeds. Pinned by `test_dispatch_never_asks_the_adapter_for_usage` in Task 1.
- A timed-out attempt (`exit_code=None`, `harness_error`): the terminal attempt still records the launcher's `duration` and `None` cost/tokens, never a value read off the log. Pinned by `test_a_timed_out_attempt_journals_its_duration_and_no_usage` in Task 1.

---

### Task 1: Dispatch stops reading the harness log for usage

**Files:**
- Modify: `src/agent_manager/dispatch.py:14-15` (module docstring), `:36` (import), `:555-566` (call site and kwargs), `:705-716` (`_usage` helper, delete)
- Test: `tests/test_dispatch.py:659-674` (replace `test_usage_parsed_from_the_log_is_journalled_on_the_attempt`; add new tests and helpers directly after it)

**Interfaces:**
- Consumes: existing test helpers in `tests/test_dispatch.py` — `FakeLauncher` (dataclass at :288, fields `results`, `stdout`, `exit_code`, `timed_out`, ...; `__call__(argv, *, cwd, timeout, stdout_path) -> Outcome`, returns `duration=1.25`), `FakeAdapter` (:197), `_runner(store, launcher, tmp_path, worktree, **overrides) -> (dispatch.AgentRunner, adapter)` (:575, accepts `adapter=` override), `_workflow`, `AGENT_DOCUMENT`, `_context`, `_rendered`, `_attempt_statuses`, fixtures `store`, `worktree`, constants `VALID_RESULT`, `dispatch.STDOUT_NAME` (`"stdout.log"`).
- Produces (used by Task 2): helper `_terminal_attempts(opened) -> list[dict]` in `tests/test_dispatch.py`, returning every `attempt_upsert` payload whose `status != "started"`, in journal order.

- [ ] **Step 1: Write the failing tests**

In `tests/test_dispatch.py`, replace the whole of `test_usage_parsed_from_the_log_is_journalled_on_the_attempt` (lines 659-674, from `def test_usage_parsed_from_the_log_is_journalled_on_the_attempt(` through `    assert terminal["exit_code"] == 0`) with:

```python
def _terminal_attempts(opened) -> list[dict]:
    """Every journalled attempt payload past `started`, in journal order."""
    return [
        line.payload
        for line in opened.journal.read()
        if line.event == "attempt_upsert" and line.payload["status"] != "started"
    ]


def test_the_outcome_is_journalled_on_the_attempt(store, tmp_path, worktree):
    # §5.2, narrowed for this slice: `Attempt` still declares the three fields
    # (3ebd08fb removes them and tightens this to key-absence), so they are
    # asserted `None`. The log is deliberately the old bait -- FakeAdapter's
    # `parse_usage` would turn it into 11/22/0.5 if anything still asked.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], stdout="usage: tokens\n")
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    [terminal] = _terminal_attempts(store)
    assert terminal["status"] == "ok"
    assert terminal["duration"] == 1.25
    assert terminal["exit_code"] == 0
    assert terminal["tokens_in"] is None
    assert terminal["tokens_out"] is None
    assert terminal["cost"] is None


@dataclass
class UnreadableLogLauncher(FakeLauncher):
    """A `FakeLauncher` that leaves `stdout_path` absent or unreadable as text.

    `log` is `"absent"` (no file), `"directory"` (a directory where the log
    should be) or `"non_utf8"` (bytes that are not UTF-8 but still contain
    the old "usage" bait once decoded with replacement).
    """

    log: str = "absent"

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        outcome = super().__call__(argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path)
        stdout_path.unlink()
        if self.log == "directory":
            stdout_path.mkdir()
        elif self.log == "non_utf8":
            stdout_path.write_bytes(b"\xff\xfeusage: \x80tokens\n")
        return outcome


@pytest.mark.parametrize("log", ["absent", "directory", "non_utf8"])
def test_the_engine_never_opens_the_harness_log(store, tmp_path, worktree, monkeypatch, log):
    # §5.3 item 6. D4: stdout.log is a log, never read by the engine. Any read
    # of a file named stdout.log is recorded, so "swallowed the OSError" and
    # "never looked" are told apart.
    reads: list[Path] = []
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes
    real_open = Path.open

    def read_text(self, *args, **kwargs):
        if self.name == dispatch.STDOUT_NAME:
            reads.append(self)
        return real_read_text(self, *args, **kwargs)

    def read_bytes(self, *args, **kwargs):
        if self.name == dispatch.STDOUT_NAME:
            reads.append(self)
        return real_read_bytes(self, *args, **kwargs)

    def open_(self, *args, **kwargs):
        # Only reads count: FakeLauncher's own `write_text` goes through
        # `Path.open(mode="w")` on some Python versions.
        mode = args[0] if args else kwargs.get("mode", "r")
        if self.name == dispatch.STDOUT_NAME and "r" in mode:
            reads.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "open", open_)
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = UnreadableLogLauncher(results=[VALID_RESULT], log=log)
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert reads == []
    [terminal] = _terminal_attempts(store)
    assert terminal["status"] == "ok"
    assert terminal["duration"] == 1.25
    assert terminal["exit_code"] == 0
    assert terminal["tokens_in"] is None
    assert terminal["tokens_out"] is None
    assert terminal["cost"] is None


class UsageRefusingAdapter(FakeAdapter):
    """A `FakeAdapter` whose `parse_usage` must never be reached."""

    def parse_usage(self, stdout: str) -> Usage | None:
        raise AssertionError("dispatch must never ask the adapter for usage")


def test_dispatch_never_asks_the_adapter_for_usage(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], stdout="usage: tokens\n")
    runner, _ = _runner(
        store, launcher, tmp_path, worktree, adapter=UsageRefusingAdapter()
    )

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_a_timed_out_attempt_journals_its_duration_and_no_usage(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(
        results=[None], exit_code=None, timed_out=True, stdout="usage: tokens\n"
    )
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    terminals = _terminal_attempts(store)
    assert terminals
    for terminal in terminals:
        assert terminal["status"] == "harness_error"
        assert terminal["duration"] == 1.25
        assert terminal["exit_code"] is None
        assert terminal["tokens_in"] is None
        assert terminal["tokens_out"] is None
        assert terminal["cost"] is None
```

Notes for the implementer: `dataclass`, `Path`, `Outcome`, `Usage`, `AgentPhaseFailed` and `pytest` are already imported at the top of `tests/test_dispatch.py` (lines 15, 17, 19, 31, 33). None of these tests gets a tier marker: they spawn nothing, so they are unit tier.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -k "outcome_is_journalled or never_opens_the_harness_log or never_asks_the_adapter or timed_out_attempt_journals" -v`

Expected: FAIL, for these reasons:
- `test_the_outcome_is_journalled_on_the_attempt`: `assert 11 is None` on `tokens_in` (dispatch still parses the bait).
- `test_the_engine_never_opens_the_harness_log[absent|directory|non_utf8]`: `assert reads == []` fails (the list holds the `stdout.log` path; `_usage` read it). For `non_utf8` the `tokens_in` assertion would also fail.
- `test_dispatch_never_asks_the_adapter_for_usage`: `AssertionError: dispatch must never ask the adapter for usage` (raised out of `_usage`).
- `test_a_timed_out_attempt_journals_its_duration_and_no_usage`: `assert 11 is None` on `tokens_in`.

If any of them passes here, stop: the test is not pinning the change.

- [ ] **Step 3: Delete the call site and its three kwargs**

In `src/agent_manager/dispatch.py`, replace:

```python
        usage = _usage(target.adapter, outcome)
        self._record_attempt(
            phase,
            models.Attempt(
                n=n,
                dispatch=dispatch_record,
                status=verdict.status,
                exit_code=outcome.exit_code,
                duration=outcome.duration,
                tokens_in=None if usage is None else usage.tokens_in,
                tokens_out=None if usage is None else usage.tokens_out,
                cost=None if usage is None else usage.cost,
                prompt_path=prompt_path,
                result_path=dispatch_record.result_path,
                stdout_path=stdout_path,
            ),
        )
```

with:

```python
        self._record_attempt(
            phase,
            models.Attempt(
                n=n,
                dispatch=dispatch_record,
                status=verdict.status,
                exit_code=outcome.exit_code,
                duration=outcome.duration,
                prompt_path=prompt_path,
                result_path=dispatch_record.result_path,
                stdout_path=stdout_path,
            ),
        )
```

- [ ] **Step 4: Delete the `_usage` helper**

In `src/agent_manager/dispatch.py`, delete this block in full (it is at the end of the file, after `_decline`), including the two blank lines that precede it, so the file ends with `        return None` from `_decline` followed by a single newline:

```python


def _usage(adapter: HarnessAdapter, outcome: Outcome) -> Usage | None:
    """What the attempt cost, as far as `stdout.log` says. Never raises.

    The only thing the log is ever read for (D4). `errors="replace"` and the
    swallowed `OSError` are deliberate: a truncated or unreadable log must not
    turn an attempt that produced a perfectly good result file into a failure.
    """
    try:
        text = outcome.stdout_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return adapter.parse_usage(text)
```

Before deleting, check with `grep -n "_usage\|Usage" src/agent_manager/dispatch.py` that the only hits are this helper, the import on line 36 and the docstring on line 15; if anything else references `_usage`, stop and report it.

- [ ] **Step 5: Drop `Usage` from the import**

In `src/agent_manager/dispatch.py` line 36, replace:

```python
from agent_manager.harness.base import HarnessAdapter, Outcome, Usage
```

with:

```python
from agent_manager.harness.base import HarnessAdapter, Outcome
```

(`HarnessAdapter` stays: `Target.adapter` at :104, `resolve_target` at :111 and `AgentRunner.adapters` at :378 use it. `Outcome` stays: `classify` at :232 uses it.)

- [ ] **Step 6: Reword the module docstring**

In `src/agent_manager/dispatch.py` lines 14-15, replace:

```python
- D4 / §6 step 5: the contract is `result.json`. `stdout.log` is captured as a
  log and is only ever handed to `parse_usage`, never parsed for a result.
```

with:

```python
- D4 / §6 step 5: the contract is `result.json`. `stdout.log` is captured as a
  log and never read by the engine.
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -k "outcome_is_journalled or never_opens_the_harness_log or never_asks_the_adapter or timed_out_attempt_journals" -v`

Expected: PASS (6 tests: 1 + 3 parametrized + 1 + 1).

- [ ] **Step 8: Run the whole dispatch test file**

Run: `uv run pytest tests/test_dispatch.py -v`

Expected: PASS. `test_usage_parsed_from_the_log_is_journalled_on_the_attempt` no longer exists.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "fix: dispatch no longer reads stdout.log for usage on the journalled attempt"
```

---

### Task 2: Neutral fake-harness log, launcher docstring, full verification

**Files:**
- Modify: `tests/test_dispatch.py:298` (`FakeLauncher.stdout` default), `:331` (`_outcome` log text)
- Modify: `src/agent_manager/harness/launcher.py:9-12` (module docstring)

**Interfaces:**
- Consumes: Task 1's tests, which pass `stdout="usage: tokens\n"` explicitly wherever they need the old bait, so changing the default here does not weaken them.
- Produces: nothing new.

- [ ] **Step 1: Neutralize the `FakeLauncher` default log**

In `tests/test_dispatch.py` line 298, inside `class FakeLauncher`, replace:

```python
    stdout: str = "usage: tokens\n"
```

with:

```python
    stdout: str = "fake-harness ran\n"
```

- [ ] **Step 2: Neutralize the `_outcome` log**

In `tests/test_dispatch.py` line 331, inside `_outcome`, replace:

```python
    log.write_text("usage: tokens\n", encoding="utf-8")
```

with:

```python
    log.write_text("fake-harness ran\n", encoding="utf-8")
```

- [ ] **Step 3: Confirm the `Usage` test import is still needed**

Run: `grep -n "Usage" tests/test_dispatch.py`

Expected: hits at line 33 (`from agent_manager.harness.base import Outcome, Usage`), in `FakeAdapter.parse_usage` (lines 218-219) and in `UsageRefusingAdapter.parse_usage`. Leave the import as is — `FakeAdapter.parse_usage` still constructs `Usage`, and 7988f1db removes both together. Do not edit `FakeAdapter.parse_usage`.

- [ ] **Step 4: Reword the launcher docstring**

In `src/agent_manager/harness/launcher.py` lines 8-12, replace:

```python
Everything this module does is deliberately blind to what it is running. It
never reads the result file (§6 step 5 is the engine's), never parses the log
it wrote (that is `parse_usage`'s job, on the adapter), and never builds a
shell string (§5 line 252). That blindness is what lets one launcher serve
every adapter.
```

with:

```python
Everything this module does is deliberately blind to what it is running. It
never reads the result file (§6 step 5 is the engine's), never parses the log
it wrote (nothing does: D4), and never builds a shell string (§5 line 252).
That blindness is what lets one launcher serve every adapter.
```

- [ ] **Step 5: Check no stale references remain in the touched source**

Run: `grep -n "parse_usage\|_usage\|Usage" src/agent_manager/dispatch.py src/agent_manager/harness/launcher.py`

Expected: no output.

- [ ] **Step 6: Run the full default suite**

Run: `uv run pytest`

Expected: PASS (unit + git tiers), no failures or errors. In particular `tests/test_dispatch.py`, `tests/harness/test_launcher.py`, `tests/test_store.py`, `tests/test_models.py`, `tests/harness/test_base.py` and `tests/harness/test_claude.py` are all green — the last four are untouched and still exercise `Attempt`'s cost fields and `Usage`/`parse_usage` directly, which this slice leaves in place.

- [ ] **Step 7: Commit**

```bash
git add tests/test_dispatch.py src/agent_manager/harness/launcher.py
git commit -m "test: neutral fake-harness log text; launcher docstring says nothing parses the log"
```

---

## Spec coverage map

- Delete `_usage`, its call site and three kwargs: Task 1 Steps 3-4.
- Drop `Usage` only from the `harness.base` import: Task 1 Step 5.
- Reword dispatch module docstring: Task 1 Step 6.
- Reword launcher docstring ("nothing does: D4"): Task 2 Step 4.
- Out-of-scope files untouched: Global Constraints; Task 2 Step 6 runs their tests unchanged.
- Fixture bait to `"fake-harness ran\n"` in `FakeLauncher` and `_outcome`: Task 2 Steps 1-2.
- `Usage` test import checked: Task 2 Step 3 (it stays).
- Spec test 1 (`test_the_outcome_is_journalled_on_the_attempt`, `None` values, `stdout="usage: tokens\n"` override): Task 1 Step 1.
- Spec test 2 (`test_the_engine_never_opens_the_harness_log`, absent log still `ok` with `duration == 1.25`): Task 1 Step 1, extended to directory and non-UTF-8 logs.
- Spec test 3 (other lost usage assertions move to `duration`): the only usage/cost assertions in `tests/test_dispatch.py` were in `test_usage_parsed_from_the_log_is_journalled_on_the_attempt` (lines 670-672), which Task 1 replaces with a test that keeps `duration == 1.25` and `exit_code == 0`; no other test in the file asserts tokens/cost.
