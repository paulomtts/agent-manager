<!-- task-pipeline: validated -->
# Subtask cfcfa6e3: Prove several `am` processes on one repository end to end

Card `cfcfa6e3-13b1-4bc4-ba99-a925f5d95c57`, story f88c5d7d ("Proof and documentation"), Milestone 10 ("several am processes per repository"). This narrows Task 3.1 of `docs/superpowers/plans/2026-09-27-multi-process.md` (on the `docs-multi-process` worktree), following §7 "Testing" of `docs/superpowers/specs/2026-09-27-multi-process-design.md`.

## Scope

This subtask adds tests and test scaffolding only. Production code changes only if a test finds a bug. In that case the fix goes in this subtask and gets its own failing test first, placed in the tier that the §7 placement rule assigns to it. The subtask builds on the milestone-10 code as it exists in this worktree: `locks.py`, `run_claims`, `Store.take_lease`, `control.card_claim` and `branch_claim`, `cli.ClaimedError` and `RunIsLiveError`, `orchestrate.milestone_claims`, and `am status`'s `data.control.claims`. Where the design doc's names differ from that code, the code wins.

Files:

- **New:** `tests/e2e/test_multi_process.py`. The scenarios below are unmarked, so they run in the default `uv run pytest`.
- **Modified:** `tests/e2e/fake_claude.py` gets a hold knob.
- **Modified:** `tests/e2e/conftest.py` gets `spawn_am`, `am` and `wait_for_file`, plus the hold env-name constants.
- **Modified:** `tests/e2e/test_fake_claude.py` pins the new constants.

Commit: `test(e2e): several am processes on one repository`. The branch prefix is `m10`. Nothing is pushed and the base branch does not move.

## Scaffolding behaviour

- **`fake_claude.py` hold knob**
  - Add the constants `HOLD_DIR_ENV = "FAKE_CLAUDE_HOLD_DIR"` and `HOLD_PHASE_ENV = "FAKE_CLAUDE_HOLD_PHASE"`. The phase defaults to `"implement"`.
  - When `HOLD_DIR_ENV` is set and the invocation enters the hold phase, the fake:
    1. writes `<dir>/<short_id(card_id)>.held`;
    2. polls for `<dir>/<short_id(card_id)>.release`, reusing the existing rendezvous poll interval and timeout (`RENDEZVOUS_POLL` / `RENDEZVOUS_TIMEOUT`).
  - If the timeout expires, the fake exits non-zero with a message naming the missing file, the same way the rendezvous does.
  - When `HOLD_DIR_ENV` is unset, nothing changes.
  - The knob is read only from the environment, never from the brief or prompt.
  - `short_id` is `agent_manager.dag.short_id`, or an equivalent local copy if the fake must stay import-free. The test and the fake must compute the same name.
- **`conftest.py`**
  - `spawn_am(*args, env=None) -> subprocess.Popen` runs `[sys.executable, "-c", "from agent_manager.cli import app; app()", *args]` with `stdout=PIPE` and `text=True`. By default it inherits `os.environ`, so the monkeypatched `XDG_DATA_HOME`, the `PATH` pointing at the fake `claude`, and the hold/rendezvous vars all reach the child. An explicit `env` overlays the inherited environment.
  - `am(*args) -> tuple[int, dict]` runs one child to completion and returns its exit code and the parsed JSON envelope.
  - `wait_for_file(path, child)` polls for `path` until a deadline. If `child.poll()` shows the child has exited before the file appears, it fails immediately and includes the child's captured output. If the deadline passes, it fails.
  - Ordering between processes is always proven by events such as marker files and exits. The tests never use `sleep` to order anything.
  - Add `FAKE_HOLD_DIR_ENV` and `FAKE_HOLD_PHASE_ENV` constants, mirroring the existing `FAKE_RENDEZVOUS_*` pair.
- **Cleanup:** every spawned child is killed and reaped in teardown (a fixture or `try/finally`), even when an assertion fails. No child outlives its test.

## Observable behaviour proven (e2e tier)

Error envelopes have the shape `{"ok": false, "error": {"type", "message"}}`. The `key` and `run_id` attributes of `ClaimedError` are not in the envelope. `cli._claimed_error` renders a key `kind:name` as `"{kind} {name} is being driven by run ..."` — a space, not the key's colon (pinned by `tests/test_cli.py::test_refuse_claimed_names_the_kind_and_the_live_holder`) — and never names the `--branch-prefix` flag. So tests check `message` for the kind and name as that space-joined substring (e.g. `f"card {a2} is"` or `f"branch {branch} is"`), never the colon-joined key, and never assert a `--branch-prefix` mention. (The multi-process design doc's §6 table says a branch conflict's message adds "use another --branch-prefix"; the code as built does not do this, and per this spec's Scope note the code wins — that phrasing is stale.)

1. **A card claimed by a live milestone.**
   - Setup: a milestone run is spawned and held in `implement` on a1. `wait_for_file` waits for a1's `.held` marker.
   - `am run --card a2` exits 3 with type `ClaimedError`, and `am runs` shows no new row.
   - `am resume <milestone-run>` exits 3 with type `RunIsLiveError`.
   - `am status <milestone-run>` has `f"card:{a2}"` in `data.control.claims`.
   - After a1's `.release` file is written, the milestone child exits 0 and its run is `done`.
2. **Two milestones with different prefixes run concurrently.**
   - Setup: two milestones on one repository, one with `--branch-prefix m10a` and one with `m10b`. The existing `rendezvous` fixture with count 2 forces their implement phases to overlap.
   - Both runs finish `done` and integrate.
   - Each `<prefix>-integrate` branch contains only its own milestone's task tips.
   - Every story and milestone card's board status equals `rollup_status` of its children.
3. **Two milestones with the same prefix.** Setup: the first milestone is held live. The second milestone, using the same `--branch-prefix`, exits 3 with type `ClaimedError`, and its message contains `f"branch {prefix}-integrate is"` (the space-joined kind and name; no `--branch-prefix` mention — see the note above).
4. **Taking over a killed milestone.**
   - Setup: a milestone run is held in `implement` and then killed with SIGKILL. The test records its pid and reaps it.
   - `am resume <run>` exits 0 with `data.took_over.pid == dead_pid`.
   - The run then finishes: `done: true` and run status `done`.

## Error paths covered

- A second run that needs a card claimed by a live run is refused (`ClaimedError`, exit 3) and leaves no row behind.
- Resuming a run another process holds is refused (`RunIsLiveError`, exit 3).
- Reusing a branch prefix that is in use is refused (`ClaimedError` naming the branch claim, per the note under "Observable behaviour proven").
- A dead holder's lease is taken over rather than refused.
- On the scaffolding side:
  - a child that exits early makes `wait_for_file` fail fast and show that child's output;
  - a hold that is never released makes the fake time out and exit non-zero, instead of hanging the suite.

## Test list and tiers

The tiers follow the placement rule in multi-process design §7. Only the end-to-end tier uses real `subprocess.Popen` `am` children. The lock, store, CLI and orchestrate tiers are already covered by earlier milestone-10 subtasks and are not added to here.

| Test | File | Tier |
|---|---|---|
| Scenario 1: live milestone claims a2; `--card` is refused, no new row, `resume` is refused, `status` lists the claim, the milestone finishes after release | `tests/e2e/test_multi_process.py` | e2e (default suite, unmarked, real `am` children + fake `claude`) |
| Scenario 2: `m10a`/`m10b` overlap via rendezvous; both `done`; integrate branches isolated; rollups consistent | `tests/e2e/test_multi_process.py` | e2e (unmarked) |
| Scenario 3: same prefix is refused with `ClaimedError` whose message contains `f"branch {prefix}-integrate is"` | `tests/e2e/test_multi_process.py` | e2e (unmarked) |
| Scenario 4: SIGKILLed holder; `resume` takes over (`took_over.pid == dead_pid`) and finishes | `tests/e2e/test_multi_process.py` | e2e (unmarked) |
| Hold env-name constants equal the conftest's (`FAKE_CLAUDE_HOLD_DIR`, `FAKE_CLAUDE_HOLD_PHASE`) and the phase default is `implement` | `tests/e2e/test_fake_claude.py` | fake-harness unit tests, alongside the existing rendezvous-name pin (test_fake_claude.py:635-640) |
| Hold behaviour: writes `.held` on the hold phase, returns once `.release` exists, times out non-zero without it, is inert when unset or in another phase | `tests/e2e/test_fake_claude.py` | fake-harness unit tests (same file and tier as the existing rendezvous tests) |
| Any bug fix uncovered by the scenarios | `tests/test_locks.py`, `tests/test_store.py`, `tests/test_cli.py` or `tests/test_orchestrate.py`, as §7 assigns | the tier that owns the faulty layer (for CLI/orchestrate, leases/claims planted over a second connection, no subprocesses) |

No test here carries the `e2e` marker. That marker is reserved for `tests/e2e/test_real_harness*.py`, which drive the real `claude` (see pyproject `markers` and the `test_live_control.py` docstring).

## Note on inputs

The exploration findings given to this stage were cut off at 8000 characters, partway through the Global Constraints section. This spec relies only on the parts that were received and on the code in this worktree. The Global Constraints on the card should be re-read at the planning stage.

---

# Several `am` processes on one repository — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, with real `am` child processes and the fake `claude`, that several `am` processes share one repository safely: a live milestone's claims refuse other runs, two milestones with different prefixes run concurrently and stay apart, a reused prefix is refused, and a SIGKILLed milestone is taken over by `am resume`.

**Architecture:** The fake `claude` gains an env-only hold knob (`FAKE_CLAUDE_HOLD_DIR` / `FAKE_CLAUDE_HOLD_PHASE`) that parks one card's phase until the test writes a `.release` file, so a milestone run can be kept live for exactly as long as a test needs it. `tests/e2e/conftest.py` gains an `AmProcesses` registry (behind the `spawn_am`, `am`, `finish_am` and `wait_for_file` fixtures) that runs `python -c "from agent_manager.cli import app; app()" ...` children, parses their envelopes, orders by marker files and child exits, and kills and reaps every child at teardown, plus a `Hold` fixture and a `two_milestone_board` fixture. `tests/e2e/test_multi_process.py` holds the scaffolding self-tests and the four scenarios.

**Tech Stack:** Python 3, pytest (`--import-mode=importlib`, so conftest names are reached as fixtures, never imported), `subprocess.Popen`, real `git` and `brd`, Typer CLI `agent_manager.cli.app`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-prove-several-am-cfcfa6e3/docs/superpowers/specs/task-prove-several-am-cfcfa6e3-design.md` (reproduced above).

## Global Constraints

- Tests and test scaffolding only. A production change is allowed only when a scenario exposes a bug, and then only after a failing test for it in the tier §7 assigns (`tests/test_locks.py`, `tests/test_store.py`, `tests/test_cli.py` with leases/claims planted over a second connection, or `tests/test_orchestrate.py`), following superpowers:systematic-debugging.
- Read the code as built first: names used below were read from this worktree (`cli.ClaimedError` cli.py:168, `cli.RunIsLiveError` cli.py:164, `cli._claimed_error` cli.py:836, `cli.EXIT_ERROR = 3` cli.py:54, `orchestrate.run_milestone` payload keys `done`/`run_id`/`integrated`/`resumed`/`took_over` orchestrate.py:1514-1582, `control.lease_is_live` control.py:65, `store.RunSummary.id` store.py:420, `dag.short_id` dag.py:41, `integration.integration_branch` integration.py:73, `rollup.rollup_status` steps/rollup.py:53, `board.tree` board.py:214).
- One liveness mechanism: the lease's heartbeat plus `control.lease_is_live`. The `.held` marker's pid is test scaffolding for killing an orphaned fake, never a liveness signal.
- Refuse before any side effect: a refused driving command leaves no run row and no worktree.
- CLI envelope unchanged: `{"ok": true, "data": ...}`, `{"ok": false, "error": {"type", "message"}}` at exit 3.
- No test sleeps to prove ordering. Cross-process order comes from marker files, the fake's rendezvous and exit codes. (A poll interval inside `wait_for_file` and the fake's hold loop is a polling cadence, not an ordering sleep.)
- Child processes inherit the test's `XDG_DATA_HOME` (`fresh_project` / the root `tests/conftest.py` autouse fixture), the `PATH` with the fake `claude` (`fake_claude_bin`), and the hold/rendezvous env vars, so they share one store, lock files and fake.
- The hold knob is read only from the environment. Which card is held comes from the brief's own result path (`<run dir>/<card id>/<phase>.<n>/result.json`), never from a new brief section.
- `fake_claude.py` stays standard-library only: its `short_id` is a local copy of `dag.short_id`, pinned equal by a test.
- No test in this subtask carries the `e2e` marker.
- Verification: `uv run pytest`, whole suite green, `tests/e2e` included.
- Branch prefix `m10`; branch `m10/task-prove-several-am-cfcfa6e3`; base `master` with milestone 9 merged. Nothing is pushed, and the base branch never moves.
- One commit at the end, with exactly the message `test(e2e): several am processes on one repository`.

## Review Focus

1. **A half-written `.held` marker.** The test reads the fake's pid from `.held` the moment the file appears; a marker written in two steps could be read empty. Expected: the fake writes it atomically (temp file plus `os.replace`), so any visible marker holds a whole pid. Pinned in Task 1 (`test_a_released_hold_writes_its_pid_marker_and_returns` asserts the pid and that no temp file is left).
2. **A typo'd hold phase silently never holds.** `FAKE_CLAUDE_HOLD_PHASE=implemnt` would make `wait_for_file` sit out its whole deadline with no clue. Expected: the fake refuses an unknown phase, naming the env var. Pinned in Task 1 (`test_an_unknown_hold_phase_is_refused_on_any_phase`).
3. **The orphaned fake of a SIGKILLed `am`.** `run_direct` starts the fake in its own session, so killing `am` leaves it polling; released, it would commit in the same worktree as the resumed run's implement. Expected: the test kills that fake by the pid in its marker before releasing. Pinned in Task 6 (scenario 4 asserts the marker's pid is not `am`'s own, SIGKILLs that fake before writing the release, and the resumed run must still finish `done`).
4. **A child that prints a traceback instead of an envelope.** Expected: `finish_am` fails with the child's stdout and stderr, not a bare `JSONDecodeError`. Pinned in Task 2 (`test_a_child_that_prints_no_envelope_fails_with_its_output`).
5. **Held fakes left behind when a test fails.** Expected: the `hold` fixture releases every held card at teardown so no fake keeps polling past its test, and `am_processes` kills and reaps every live child. Pinned in Task 2 (`test_release_all_releases_every_held_card` and `test_closing_kills_and_reaps_every_live_child`).

---

## File map

| File | Responsibility | Tasks |
|---|---|---|
| `tests/e2e/fake_claude.py` | Hold knob: `HOLD_*` constants, `HOLD_PHASES`, `short_id`, `hold`, the call in `main` | 1 |
| `tests/e2e/test_fake_claude.py` | Pins and unit tests for the hold knob | 1 |
| `tests/e2e/conftest.py` | `FAKE_HOLD_*` twins (Task 1); `AmProcesses`, `Hold`, fixtures `am_processes`/`spawn_am`/`am`/`finish_am`/`wait_for_file`/`hold` (Task 2); `two_milestone_board` (Task 4) | 1, 2, 4 |
| `tests/e2e/test_multi_process.py` | Scaffolding self-tests and the four scenarios | 2-6 |

---

### Task 1: The fake `claude`'s hold knob

**Files:**
- Modify: `tests/e2e/fake_claude.py` (docstring lines 12-26; new block after `rendezvous` ending line 323; `main` lines 727-733)
- Modify: `tests/e2e/conftest.py` (constants after `FAKE_RENDEZVOUS_COUNT_ENV`, line 67-68)
- Test: `tests/e2e/test_fake_claude.py` (imports lines 14-24; new tests after `test_a_rendezvous_failure_makes_the_fake_process_exit_1`, which ends at line 783)

**Interfaces:**
- Consumes: `fake_claude.RENDEZVOUS_TIMEOUT` (float, patched by tests), `fake_claude.RENDEZVOUS_POLL`, `fake_claude.FakeClaudeError`, test helpers `_brief`, `_run_fake`, `_implement_repo`, `_head`, `_conftest_constant`, `IMPLEMENT_SCHEMA`, `PLAN_RELATIVE`, `BRIEF_HASH` already in `test_fake_claude.py`.
- Produces:
  - `fake_claude.HOLD_DIR_ENV = "FAKE_CLAUDE_HOLD_DIR"`, `HOLD_PHASE_ENV = "FAKE_CLAUDE_HOLD_PHASE"`, `HOLD_DEFAULT_PHASE = "implement"`, `HOLD_SUFFIX = ".held"`, `RELEASE_SUFFIX = ".release"`, `HOLD_PHASES` (tuple of phase names).
  - `fake_claude.short_id(card_id) -> str` (raises `FakeClaudeError` on a non-UUID).
  - `fake_claude.hold(phase: str, result_path: Path) -> None`.
  - conftest constants `FAKE_HOLD_DIR_ENV`, `FAKE_HOLD_PHASE_ENV`, `FAKE_HOLD_SUFFIX`, `FAKE_RELEASE_SUFFIX` with the same literal values.

- [ ] **Step 1: Add the imports the new tests need**

In `tests/e2e/test_fake_claude.py`, replace:

```python
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_manager.steps.reducers import critic_blockers_gate, review_gate
```

with:

```python
import ast
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_manager import dag
from agent_manager.steps.reducers import critic_blockers_gate, review_gate
```

- [ ] **Step 2: Write the failing hold tests**

In `tests/e2e/test_fake_claude.py`, insert right after the end of `test_a_rendezvous_failure_makes_the_fake_process_exit_1` (after its line `    assert not result_path.exists()`, before `IMPLEMENT_BRANCH = "m3/task-a1-00000001"`):

```python
HOLD_CARD = "0123abcd-4567-89ef-0123-456789abcdef"
"""A card id shaped like brd's, so the fake's `short_id` accepts it."""

HOLD_SHORT = "0123abcd"
"""`dag.short_id(HOLD_CARD)`, written out so a drift in either shows."""


def _hold_result_path(tmp_path, phase="implement"):
    """`<run dir>/<card id>/<phase>.<n>/result.json`, the shape `paths.attempt_dir` gives."""
    return tmp_path / "runs" / "r1" / HOLD_CARD / f"{phase}.1" / "result.json"


def _arm_hold(monkeypatch, tmp_path, phase=None):
    """Point the hold at `<tmp>/hold`; `phase=None` leaves the default phase."""
    folder = tmp_path / "hold"
    monkeypatch.setenv(fake_claude.HOLD_DIR_ENV, str(folder))
    if phase is None:
        monkeypatch.delenv(fake_claude.HOLD_PHASE_ENV, raising=False)
    else:
        monkeypatch.setenv(fake_claude.HOLD_PHASE_ENV, phase)
    return folder


def test_the_hold_names_are_pinned_and_are_the_conftest_twins():
    """The fixture and the script meet across a process boundary, like the
    rendezvous names above."""
    assert fake_claude.HOLD_DIR_ENV == "FAKE_CLAUDE_HOLD_DIR"
    assert fake_claude.HOLD_PHASE_ENV == "FAKE_CLAUDE_HOLD_PHASE"
    assert fake_claude.HOLD_DEFAULT_PHASE == "implement"
    assert fake_claude.HOLD_SUFFIX == ".held"
    assert fake_claude.RELEASE_SUFFIX == ".release"
    assert fake_claude.HOLD_DIR_ENV == _conftest_constant("FAKE_HOLD_DIR_ENV")
    assert fake_claude.HOLD_PHASE_ENV == _conftest_constant("FAKE_HOLD_PHASE_ENV")
    assert fake_claude.HOLD_SUFFIX == _conftest_constant("FAKE_HOLD_SUFFIX")
    assert fake_claude.RELEASE_SUFFIX == _conftest_constant("FAKE_RELEASE_SUFFIX")


def test_the_fakes_short_id_is_dags():
    """The test names a marker with `dag.short_id`, the fake with its own copy."""
    for card in (HOLD_CARD, HOLD_CARD.upper(), HOLD_CARD.replace("-", "")):
        assert fake_claude.short_id(card) == dag.short_id(card) == HOLD_SHORT


def test_a_result_path_that_names_no_card_is_refused_by_the_hold(tmp_path, monkeypatch):
    _arm_hold(monkeypatch, tmp_path)
    result_path = tmp_path / "runs" / "r1" / "not-a-card" / "implement.1" / "result.json"

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.hold("implement", result_path)

    assert "not-a-card" in str(caught.value)


def test_without_a_hold_dir_nothing_is_held(tmp_path, monkeypatch):
    """Unset means today's behaviour exactly; a wait would raise at 0.2s."""
    monkeypatch.delenv(fake_claude.HOLD_DIR_ENV, raising=False)
    monkeypatch.setenv(fake_claude.HOLD_PHASE_ENV, "implement")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    fake_claude.hold("implement", _hold_result_path(tmp_path))

    assert not (tmp_path / "hold").exists()


def test_a_hold_for_another_phase_passes_straight_through(tmp_path, monkeypatch):
    folder = _arm_hold(monkeypatch, tmp_path, phase="plan")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    fake_claude.hold("implement", _hold_result_path(tmp_path))

    assert not folder.exists()


def test_an_unknown_hold_phase_is_refused_on_any_phase(tmp_path, monkeypatch):
    """Review focus: a typo must stop the fake, not read as "never hold"."""
    _arm_hold(monkeypatch, tmp_path, phase="implemnt")

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.hold("explore", _hold_result_path(tmp_path, "explore"))

    assert fake_claude.HOLD_PHASE_ENV in str(caught.value)
    assert "implemnt" in str(caught.value)


def test_a_released_hold_writes_its_pid_marker_and_returns(tmp_path, monkeypatch):
    """Review focus: the marker holds this process's whole pid, and the
    atomic write leaves no temp file behind."""
    folder = _arm_hold(monkeypatch, tmp_path)
    folder.mkdir()
    (folder / f"{HOLD_SHORT}.release").write_text("", encoding="utf-8")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    fake_claude.hold("implement", _hold_result_path(tmp_path))

    marker = folder / f"{HOLD_SHORT}.held"
    assert marker.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert sorted(path.name for path in folder.iterdir()) == [
        f"{HOLD_SHORT}.held",
        f"{HOLD_SHORT}.release",
    ]


def test_a_hold_waits_until_its_release_appears(tmp_path, monkeypatch):
    """The `.held` marker is written before the wait, and the wait ends only
    when the release is written by someone else."""
    folder = _arm_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 30.0)
    marker = folder / f"{HOLD_SHORT}.held"
    saw_marker = threading.Event()

    def release_once_held():
        deadline = time.monotonic() + 30.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if marker.exists():
            saw_marker.set()
        (folder / f"{HOLD_SHORT}.release").write_text("", encoding="utf-8")

    releaser = threading.Thread(target=release_once_held)
    releaser.start()
    try:
        fake_claude.hold("implement", _hold_result_path(tmp_path))
    finally:
        releaser.join(timeout=30.0)

    assert saw_marker.is_set()


def test_a_named_hold_phase_holds_that_phase(tmp_path, monkeypatch):
    folder = _arm_hold(monkeypatch, tmp_path, phase="plan")
    folder.mkdir()
    (folder / f"{HOLD_SHORT}.release").write_text("", encoding="utf-8")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    fake_claude.hold("plan", _hold_result_path(tmp_path, "plan"))

    assert (folder / f"{HOLD_SHORT}.held").is_file()


def test_an_unreleased_hold_times_out_naming_the_release_file(tmp_path, monkeypatch):
    folder = _arm_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.hold("implement", _hold_result_path(tmp_path))

    message = str(caught.value)
    assert "timed out" in message
    assert str(folder / f"{HOLD_SHORT}.release") in message
    assert (folder / f"{HOLD_SHORT}.held").is_file()


def test_the_fake_process_holds_before_it_implements(tmp_path, monkeypatch):
    """`main` wires the hold in: the child writes its marker, finds the
    release, then implements and writes its result."""
    folder = _arm_hold(monkeypatch, tmp_path)
    folder.mkdir()
    (folder / f"{HOLD_SHORT}.release").write_text("", encoding="utf-8")
    repo = _implement_repo(tmp_path)
    before = _head(repo)
    result_path = _hold_result_path(tmp_path)
    result_path.parent.mkdir(parents=True)
    prompt_path = _brief(
        tmp_path,
        "implement",
        "coder",
        f"\n## plan_path\n{PLAN_RELATIVE}\n\n## plan_hash\n{BRIEF_HASH}\n",
        IMPLEMENT_SCHEMA,
        result_path,
    )

    completed = _run_fake(prompt_path, repo)

    assert completed.returncode == 0, completed.stderr
    assert int((folder / f"{HOLD_SHORT}.held").read_text(encoding="utf-8")) > 0
    assert json.loads(result_path.read_text(encoding="utf-8"))["plan_hash"] == BRIEF_HASH
    assert _head(repo) != before
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -k "hold or short_id" -v`
Expected: every new test FAILS, with `AttributeError: module 'e2e_fake_claude' has no attribute 'HOLD_DIR_ENV'` (or `'short_id'`); the pin test may instead fail on `AssertionError: tests/e2e/conftest.py defines no FAKE_HOLD_DIR_ENV`.

- [ ] **Step 4: Add the conftest twins**

In `tests/e2e/conftest.py`, replace:

```python
FAKE_RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"
"""Must equal `fake_claude.RENDEZVOUS_COUNT_ENV`, which `test_fake_claude.py` pins."""
```

with:

```python
FAKE_RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"
"""Must equal `fake_claude.RENDEZVOUS_COUNT_ENV`, which `test_fake_claude.py` pins."""

FAKE_HOLD_DIR_ENV = "FAKE_CLAUDE_HOLD_DIR"
"""Must equal `fake_claude.HOLD_DIR_ENV`, which `test_fake_claude.py` pins."""

FAKE_HOLD_PHASE_ENV = "FAKE_CLAUDE_HOLD_PHASE"
"""Must equal `fake_claude.HOLD_PHASE_ENV`, which `test_fake_claude.py` pins."""

FAKE_HOLD_SUFFIX = ".held"
"""Must equal `fake_claude.HOLD_SUFFIX`: the marker a held phase writes."""

FAKE_RELEASE_SUFFIX = ".release"
"""Must equal `fake_claude.RELEASE_SUFFIX`: the file that lets a held phase go on."""
```

- [ ] **Step 5: Implement the hold in the fake**

In `tests/e2e/fake_claude.py`, insert right after the end of `rendezvous` (after its line `        time.sleep(RENDEZVOUS_POLL)`, before `CRITIC_BLOCKS_ENV = "FAKE_CLAUDE_CRITIC_BLOCKS"`):

```python
HOLD_DIR_ENV = "FAKE_CLAUDE_HOLD_DIR"
"""Test scaffolding, never in a brief: where a held phase announces itself and waits.

Unset or empty means no hold at all. Set, the hold phase writes
`<dir>/<short id><HOLD_SUFFIX>` holding this process's pid, then waits for
`<dir>/<short id><RELEASE_SUFFIX>`, polling every `RENDEZVOUS_POLL` and giving
up after `RENDEZVOUS_TIMEOUT`. Whether and where to hold comes from the
environment alone; which card is held is the card the brief's own result path
names (`<run dir>/<card id>/<phase>.<n>/result.json`). The multi-process tests
use it to keep a milestone run live for exactly as long as they need."""

HOLD_PHASE_ENV = "FAKE_CLAUDE_HOLD_PHASE"
"""Which phase holds. Unset or empty means `HOLD_DEFAULT_PHASE`."""

HOLD_DEFAULT_PHASE = "implement"

HOLD_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
    "resolve",
)
"""Every phase `build_result` knows. A hold phase outside it is a typo and stops
the fake, instead of silently never holding."""

HOLD_SUFFIX = ".held"
RELEASE_SUFFIX = ".release"

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")


def short_id(card_id):
    """`agent_manager.dag.short_id`, copied: this script imports nothing from the package."""
    hex_only = str(card_id).replace("-", "")
    if not _HEX32.match(hex_only):
        raise FakeClaudeError(f"not a card id: {card_id!r}")
    return hex_only[:8].lower()


def hold(phase, result_path):
    """Announce this card's `phase` and wait for its release. A no-op unless armed.

    The marker is written to a temp name and renamed into place, so a test
    that sees `.held` always reads a whole pid.
    """
    directory = os.environ.get(HOLD_DIR_ENV)
    if not directory:
        return
    wanted = os.environ.get(HOLD_PHASE_ENV) or HOLD_DEFAULT_PHASE
    if wanted not in HOLD_PHASES:
        raise FakeClaudeError(
            f"{HOLD_PHASE_ENV} is {wanted!r}, not a phase this fake runs "
            f"(one of {list(HOLD_PHASES)})"
        )
    if phase != wanted:
        return
    card = short_id(Path(result_path).parents[1].name)
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    staging = folder / f".{card}{HOLD_SUFFIX}.tmp"
    staging.write_text(f"{os.getpid()}\n", encoding="utf-8")
    os.replace(staging, folder / f"{card}{HOLD_SUFFIX}")
    release = folder / f"{card}{RELEASE_SUFFIX}"
    deadline = time.monotonic() + RENDEZVOUS_TIMEOUT
    while not release.exists():
        if time.monotonic() >= deadline:
            raise FakeClaudeError(
                f"hold in {folder} timed out after {RENDEZVOUS_TIMEOUT}s: "
                f"{release} was never written"
            )
        time.sleep(RENDEZVOUS_POLL)
```

Then in `main`, replace:

```python
    result_path = result_path_of(text)
    cwd = Path(os.getcwd())
```

with:

```python
    result_path = result_path_of(text)
    # Test scaffolding: park here, before any work, when the hold is armed.
    hold(phase, result_path)
    cwd = Path(os.getcwd())
```

Then in the module docstring, replace:

```
fail, because that failure is the test's whole point. There are exactly five
test-controlled inputs, and none tells the fake anything the brief owns:
```

with:

```
fail, because that failure is the test's whole point. There are exactly six
test-controlled inputs, and none tells the fake anything the brief owns:
```

and replace:

```
`CRITIC_BLOCKS_ENV`, a budget file that makes a critic block a set number of
times with a fixed reason. The resolve phase learns the tip and the
```

with:

```
`CRITIC_BLOCKS_ENV`, a budget file that makes a critic block a set number of
times with a fixed reason; and the hold (`HOLD_DIR_ENV` / `HOLD_PHASE_ENV`),
which only parks one phase of the card the brief's result path names until a
release file appears. The resolve phase learns the tip and the
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: PASS, the whole file (the existing rendezvous, resolver and critic tests included).

---

### Task 2: `am` child processes, marker waits and the hold fixture

**Files:**
- Modify: `tests/e2e/conftest.py` (imports lines 11-19; append after `checkpoint_rows`, the last fixture, ending line 571)
- Create: `tests/e2e/test_multi_process.py`

**Interfaces:**
- Consumes: conftest constants `FAKE_HOLD_DIR_ENV`, `FAKE_HOLD_PHASE_ENV`, `FAKE_HOLD_SUFFIX`, `FAKE_RELEASE_SUFFIX` (Task 1); `dag.short_id`.
- Produces (all conftest; reached by tests as fixtures):
  - `AM_WAIT = 240.0`, `MARKER_POLL = 0.05`, `AM_ENTRY = "from agent_manager.cli import app; app()"`.
  - `class AmProcesses` with `child_env(env: Mapping[str, str] | None) -> dict[str, str]`, `spawn(*args: str, env=None) -> subprocess.Popen`, `track(child: subprocess.Popen) -> subprocess.Popen`, `finish(child, timeout: float = AM_WAIT) -> tuple[int, dict[str, Any]]`, `wait_for_file(path: Path, child, timeout: float = AM_WAIT) -> Path`, `close() -> None`.
  - fixtures `am_processes -> AmProcesses`, `spawn_am -> AmProcesses.spawn`, `am -> Callable[..., tuple[int, dict]]` (`am(*args, env=None)`), `finish_am -> AmProcesses.finish`, `wait_for_file -> AmProcesses.wait_for_file`.
  - `class Hold` with `arm(phase: str | None = None) -> Path`, `held_marker(card_id: str) -> Path`, `release(*card_ids: str) -> None`, `release_all() -> None`, `holder_pid(card_id: str) -> int`; fixture `hold -> Hold` (releases all at teardown).
  - In `tests/e2e/test_multi_process.py`: constants `PREFIX = "m10"`, `VERIFY = "git rev-parse --verify HEAD"`; helpers `_common(root, prefix) -> list[str]`, `_milestone_argv(root, milestone, prefix, *, max_concurrent=1) -> list[str]`, `_run_ids(am, root) -> list[str]`, `_only_run_id(am, root) -> str`, `_error(code, envelope) -> dict`, `_data(code, envelope) -> dict`.

- [ ] **Step 1: Write the failing scaffolding tests**

Create `tests/e2e/test_multi_process.py`:

```python
"""Default-suite e2e tier: several `am` processes on one repository (card cfcfa6e3).

Multi-process design §7, end-to-end tier. Every `am` here is a real child
process (`python -c "from agent_manager.cli import app; app()" ...`), never
`CliRunner` in a thread, so leases, claims and locks meet across genuine
process boundaries. Each child inherits the test's `XDG_DATA_HOME`, the `PATH`
with the fake `claude` first, and the hold/rendezvous env vars. The only
stand-in is the fake `claude`; its env-only hold parks one card's `implement`
until the test writes that card's release file, which is how a milestone run
is kept live. Order is proven by marker files and exits, never by sleeping.

Unmarked on purpose: fake-claude e2e tests run on every `uv run pytest`; only
`tests/e2e/test_real_harness*.py` carry the `e2e` marker.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from agent_manager import board, cli, dag
from agent_manager.steps import rollup

PREFIX = "m10"
"""The `--branch-prefix` of every single-prefix scenario."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

SOME_CARD = "0123abcd-4567-89ef-0123-456789abcdef"
"""A card id shaped like brd's, for the hold fixture's own tests."""


def _common(root: Path, prefix: str) -> list[str]:
    """The flags `am run` needs for this repo, prefix and suite."""
    return [
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--branch-prefix",
        prefix,
        "--verify",
        VERIFY,
    ]


def _milestone_argv(
    root: Path, milestone: str, prefix: str, *, max_concurrent: int = 1
) -> list[str]:
    """`am run --milestone`, one story at a time unless asked, so an overlap
    can only come from another process."""
    return [
        "run",
        "--milestone",
        milestone,
        *_common(root, prefix),
        "--max-concurrent",
        str(max_concurrent),
    ]


def _data(code: int, envelope: dict) -> dict:
    """The `data` of an ok envelope from a child that exited 0."""
    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _error(code: int, envelope: dict) -> dict:
    """The `error` of a failure envelope from a child that exited 3."""
    assert code == cli.EXIT_ERROR, envelope
    assert envelope["ok"] is False, envelope
    return envelope["error"]


def _run_ids(am, root: Path) -> list[str]:
    """`am runs`: every run id this repo has recorded, newest first."""
    data = _data(*am("runs", "--repo-dir", str(root)))
    return [row["id"] for row in data["runs"]]


def _only_run_id(am, root: Path) -> str:
    ids = _run_ids(am, root)
    assert len(ids) == 1, ids
    return ids[0]


def _sleeper() -> subprocess.Popen:
    """A child that outlives any short wait; the caller tracks it for teardown."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.PIPE,
        text=True,
    )


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or the multi-process proof stops
    running on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_am_returns_the_exit_code_and_the_parsed_envelope(tmp_path, am):
    code, envelope = am("runs", "--repo-dir", str(tmp_path / "no-such-repo"))

    assert _error(code, envelope)["type"] == "RepoDirError"


def test_a_child_that_prints_no_envelope_fails_with_its_output(am_processes, finish_am):
    """Review focus: a traceback instead of an envelope names what the child said."""
    child = am_processes.track(
        subprocess.Popen(
            [sys.executable, "-c", "print('not json at all')"],
            stdout=subprocess.PIPE,
            text=True,
        )
    )

    with pytest.raises(pytest.fail.Exception) as caught:
        finish_am(child)

    assert "without a JSON envelope" in str(caught.value)
    assert "not json at all" in str(caught.value)


def test_the_child_env_inherits_the_tests_and_overlays_the_given_one(
    am_processes, monkeypatch
):
    monkeypatch.setenv("AM_TEST_INHERITED", "from-the-test")

    plain = am_processes.child_env(None)
    overlaid = am_processes.child_env({"AM_TEST_INHERITED": "over", "AM_TEST_NEW": "1"})

    assert plain["AM_TEST_INHERITED"] == "from-the-test"
    assert plain["PATH"] == os.environ["PATH"]
    assert overlaid["AM_TEST_INHERITED"] == "over"
    assert overlaid["AM_TEST_NEW"] == "1"
    assert overlaid["XDG_DATA_HOME"] == os.environ["XDG_DATA_HOME"]


def test_wait_for_file_fails_fast_with_the_output_of_a_child_that_exited(
    tmp_path, spawn_am, wait_for_file
):
    """The child exits 3 at once; the wait must not sit out its deadline."""
    child = spawn_am("runs", "--repo-dir", str(tmp_path / "no-such-repo"))

    with pytest.raises(pytest.fail.Exception) as caught:
        wait_for_file(tmp_path / "never.held", child)

    message = str(caught.value)
    assert "exited 3" in message
    assert "RepoDirError" in message


def test_wait_for_file_fails_at_its_deadline_while_the_child_lives(
    tmp_path, am_processes, wait_for_file
):
    child = am_processes.track(_sleeper())

    with pytest.raises(pytest.fail.Exception) as caught:
        wait_for_file(tmp_path / "never.held", child, timeout=0.2)

    assert "within 0.2s" in str(caught.value)
    assert child.poll() is None


def test_wait_for_file_returns_the_path_once_it_exists(tmp_path, am_processes, wait_for_file):
    child = am_processes.track(_sleeper())
    marker = tmp_path / "here.held"
    marker.write_text("1\n", encoding="utf-8")

    assert wait_for_file(marker, child, timeout=0.2) == marker


def test_closing_kills_and_reaps_every_live_child(am_processes):
    """Review focus: no child outlives its test."""
    child = am_processes.track(_sleeper())

    am_processes.close()

    assert child.returncode == -signal.SIGKILL


def test_the_hold_names_its_markers_by_the_fakes_short_id(hold):
    directory = hold.arm()

    assert os.environ["FAKE_CLAUDE_HOLD_DIR"] == str(directory)
    assert "FAKE_CLAUDE_HOLD_PHASE" not in os.environ
    assert hold.held_marker(SOME_CARD) == directory / f"{dag.short_id(SOME_CARD)}.held"


def test_release_all_releases_every_held_card(hold):
    """Review focus: the fixture's teardown lets any still-held fake go on."""
    directory = hold.arm("plan")
    hold.held_marker(SOME_CARD).write_text("4242\n", encoding="utf-8")

    hold.release_all()

    assert os.environ["FAKE_CLAUDE_HOLD_PHASE"] == "plan"
    assert hold.holder_pid(SOME_CARD) == 4242
    assert (directory / f"{dag.short_id(SOME_CARD)}.release").is_file()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/e2e/test_multi_process.py -v`
Expected: `test_this_module_runs_in_the_default_suite_unmarked` PASSES; every other test ERRORS with `fixture 'am' not found` / `fixture 'am_processes' not found` / `fixture 'spawn_am' not found` / `fixture 'hold' not found`.

- [ ] **Step 3: Extend the conftest imports**

In `tests/e2e/conftest.py`, replace:

```python
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
```

with:

```python
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
```

- [ ] **Step 4: Add the process registry, the hold and their fixtures**

Append to the end of `tests/e2e/conftest.py` (after `checkpoint_rows`):

```python
AM_ENTRY = "from agent_manager.cli import app; app()"
"""What a spawned `am` child runs: the real Typer app, argv from its own `sys.argv`."""

AM_WAIT = 240.0
"""Seconds a spawned `am` or a marker wait may take. It only bounds a broken
run; a healthy one never waits this long."""

MARKER_POLL = 0.05
"""Seconds between checks for a marker file. A polling cadence, never an ordering."""


def _describe(child: subprocess.Popen) -> str:
    return " ".join(str(part) for part in child.args)


@dataclass
class AmProcesses:
    """Every child process one test started, killed and reaped by `close`.

    stdout is a pipe (the envelope); stderr goes to a per-child file under
    `log_dir`, so a chatty child can never fill a pipe nobody reads.
    """

    log_dir: Path
    children: list[subprocess.Popen] = field(default_factory=list)
    stderr_paths: dict[int, Path] = field(default_factory=dict)

    def child_env(self, env: Mapping[str, str] | None) -> dict[str, str]:
        """The test's own environment, with `env` laid over it."""
        return {**os.environ, **(env or {})}

    def track(self, child: subprocess.Popen) -> subprocess.Popen:
        self.children.append(child)
        return child

    def spawn(self, *args: str, env: Mapping[str, str] | None = None) -> subprocess.Popen:
        """Start `am *args` as a real child process and track it."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stderr_path = self.log_dir / f"am-{len(self.children)}.stderr"
        with stderr_path.open("w", encoding="utf-8") as stderr:
            child = subprocess.Popen(
                [sys.executable, "-c", AM_ENTRY, *args],
                env=self.child_env(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
            )
        self.stderr_paths[child.pid] = stderr_path
        return self.track(child)

    def stderr_of(self, child: subprocess.Popen) -> str:
        path = self.stderr_paths.get(child.pid)
        if path is None or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def finish(
        self, child: subprocess.Popen, timeout: float = AM_WAIT
    ) -> tuple[int, dict[str, Any]]:
        """Wait for `child` and return its exit code and parsed envelope."""
        try:
            stdout, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            pytest.fail(
                f"`{_describe(child)}` did not exit within {timeout}s\n"
                f"stderr: {self.stderr_of(child)}"
            )
        try:
            envelope = json.loads(stdout)
        except (TypeError, json.JSONDecodeError):
            pytest.fail(
                f"`{_describe(child)}` exited {child.returncode} without a JSON envelope\n"
                f"stdout: {stdout!r}\nstderr: {self.stderr_of(child)}"
            )
        return child.returncode, envelope

    def wait_for_file(
        self, path: Path, child: subprocess.Popen, timeout: float = AM_WAIT
    ) -> Path:
        """`path` once it exists; fail at once if `child` exits first, or at the deadline."""
        deadline = time.monotonic() + timeout
        while not path.exists():
            if child.poll() is not None:
                if path.exists():
                    break
                stdout, _ = child.communicate()
                pytest.fail(
                    f"{path} never appeared: `{_describe(child)}` exited "
                    f"{child.returncode} first\nstdout: {stdout!r}\n"
                    f"stderr: {self.stderr_of(child)}"
                )
            if time.monotonic() >= deadline:
                pytest.fail(
                    f"{path} did not appear within {timeout}s; "
                    f"`{_describe(child)}` is still running"
                )
            time.sleep(MARKER_POLL)
        return path

    def close(self) -> None:
        """Kill every child still running, reap every child, close every pipe."""
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()
            if child.stdout is not None and not child.stdout.closed:
                child.stdout.close()


@pytest.fixture
def am_processes(tmp_path) -> Any:
    """The test's child processes; every one is killed and reaped at teardown."""
    processes = AmProcesses(log_dir=tmp_path / "am-logs")
    yield processes
    processes.close()


@pytest.fixture
def spawn_am(am_processes) -> Callable[..., subprocess.Popen]:
    """`spawn_am(*args, env=None)`: start a real `am` child and return it."""
    return am_processes.spawn


@pytest.fixture
def finish_am(am_processes) -> Callable[..., tuple[int, dict[str, Any]]]:
    """`finish_am(child)`: wait for a spawned child, return `(exit code, envelope)`."""
    return am_processes.finish


@pytest.fixture
def am(am_processes) -> Callable[..., tuple[int, dict[str, Any]]]:
    """`am(*args, env=None)`: run one `am` child to completion, return `(exit code, envelope)`."""

    def run(*args: str, env: Mapping[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        return am_processes.finish(am_processes.spawn(*args, env=env))

    return run


@pytest.fixture
def wait_for_file(am_processes) -> Callable[..., Path]:
    """`wait_for_file(path, child, timeout=AM_WAIT)`: order by a marker, never by a sleep."""
    return am_processes.wait_for_file


@dataclass
class Hold:
    """Arms the fake's env-only hold for one test and releases held cards.

    Env vars go through the test's function-scoped `monkeypatch`, so they are
    undone when the test ends; `am` children inherit them, and the fake
    inherits them from `am` (`run_direct` passes no `env=`).
    """

    directory: Path
    monkeypatch: pytest.MonkeyPatch

    def arm(self, phase: str | None = None) -> Path:
        """Hold `phase` (the fake's default, `implement`, when `None`)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        self.monkeypatch.setenv(FAKE_HOLD_DIR_ENV, str(self.directory))
        if phase is None:
            self.monkeypatch.delenv(FAKE_HOLD_PHASE_ENV, raising=False)
        else:
            self.monkeypatch.setenv(FAKE_HOLD_PHASE_ENV, phase)
        return self.directory

    def held_marker(self, card_id: str) -> Path:
        """Where the fake announces that `card_id`'s phase is held."""
        return self.directory / f"{dag.short_id(card_id)}{FAKE_HOLD_SUFFIX}"

    def release(self, *card_ids: str) -> None:
        """Let each card's held phase go on (or pass straight through later)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        for card_id in card_ids:
            (self.directory / f"{dag.short_id(card_id)}{FAKE_RELEASE_SUFFIX}").write_text(
                "", encoding="utf-8"
            )

    def release_all(self) -> None:
        """Release every card that has announced a hold."""
        if not self.directory.is_dir():
            return
        for marker in self.directory.glob(f"*{FAKE_HOLD_SUFFIX}"):
            marker.with_suffix(FAKE_RELEASE_SUFFIX).write_text("", encoding="utf-8")

    def holder_pid(self, card_id: str) -> int:
        """The pid of the fake `claude` holding `card_id`, from its marker."""
        return int(self.held_marker(card_id).read_text(encoding="utf-8").strip())


@pytest.fixture
def hold(tmp_path, monkeypatch) -> Any:
    """The test's hold, unarmed. Its dir is beside the repo, never inside it.
    Every held card is released at teardown, so no fake polls past its test."""
    held = Hold(directory=tmp_path / "hold", monkeypatch=monkeypatch)
    yield held
    held.release_all()
```

- [ ] **Step 5: Run the scaffolding tests to verify they pass**

Run: `uv run pytest tests/e2e/test_multi_process.py -v`
Expected: all 10 tests PASS.

---

### Task 3: Scenario 1 — a card a live milestone claims

**Files:**
- Test: `tests/e2e/test_multi_process.py` (append)

**Interfaces:**
- Consumes: fixtures `milestone_board` (conftest.py:270; keys `root`, `milestone`, `subtasks` = `{"A": [a1, a2], "B": [b1], "C": [c1]}`; B blocked by A, C by B), `fake_claude_bin`, `hold`, `spawn_am`, `am`, `finish_am`, `wait_for_file`; helpers `_common`, `_milestone_argv`, `_data`, `_error`, `_run_ids`, `_only_run_id`, `PREFIX`, `VERIFY` (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Write the scenario**

Append to `tests/e2e/test_multi_process.py`:

```python
def test_a_card_a_live_milestone_claims_is_refused_to_every_other_process(
    milestone_board, fake_claude_bin, hold, spawn_am, am, finish_am, wait_for_file
):
    """Spec scenario 1: `run --card`, `resume` and `status` from other processes
    while a real milestone process holds a1 in `implement`."""
    root = milestone_board["root"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)  # only a1 is held; the rest pass straight through
    milestone = spawn_am(*_milestone_argv(root, milestone_board["milestone"], PREFIX))
    wait_for_file(hold.held_marker(a1), milestone)
    run_id = _only_run_id(am, root)

    error = _error(*am("run", "--card", a2, *_common(root, PREFIX)))
    assert error["type"] == "ClaimedError", error
    assert f"card {a2} is" in error["message"]
    assert run_id in error["message"]
    assert _run_ids(am, root) == [run_id]
    a2_branch = dag.task_branch(PREFIX, board.show(a2, repo_dir=root))
    assert not cli.worktree_for(root, a2_branch).exists()

    error = _error(*am("resume", run_id, "--repo-dir", str(root), "--verify", VERIFY))
    assert error["type"] == "RunIsLiveError", error

    live = _data(*am("status", run_id, "--repo-dir", str(root)))
    assert live["run"]["status"] == "started"
    assert live["control"]["lease"]["live"] is True
    assert live["control"]["lease"]["pid"] == milestone.pid
    assert f"card:{a2}" in live["control"]["claims"]

    hold.release(a1)
    finished = _data(*finish_am(milestone))
    assert finished["done"] is True, finished
    assert finished["run_id"] == run_id
    after = _data(*am("status", run_id, "--repo-dir", str(root)))
    assert after["run"]["status"] == "done"
    assert after["control"]["claims"] == []
```

- [ ] **Step 2: Run it**

Run: `uv run pytest "tests/e2e/test_multi_process.py::test_a_card_a_live_milestone_claims_is_refused_to_every_other_process" -v`
Expected: PASS. The behaviour is already built by earlier milestone-10 subtasks; this test characterises it across real processes. If it FAILS on a product assertion (not a scaffolding error), that is a production bug: stop, use superpowers:systematic-debugging, write a failing test for the faulty layer in the tier §7 assigns (e.g. `tests/test_cli.py` with a claim planted over a second connection), fix `src/agent_manager/...` minimally, confirm that test passes, then re-run this one.

---

### Task 4: Scenario 2 — two milestones with different prefixes at once

**Files:**
- Modify: `tests/e2e/conftest.py` (new fixture after `two_story_board`, which ends at line 485)
- Test: `tests/e2e/test_multi_process.py` (append)

**Interfaces:**
- Consumes: `_init_project` via `fresh_project`, `_add_card`, `UNION_ATTRIBUTE` (conftest); `rendezvous` fixture (`Rendezvous.arm(count) -> Path`, `.markers() -> list[Path]`); helpers from Task 2; `rollup.rollup_status(children_statuses) -> str | None`; `board.tree(card_id, *, repo_dir) -> models.CardNode` (`.status`, `.children`); `board.show(card_id, *, repo_dir) -> models.Card`.
- Produces: fixture `two_milestone_board -> dict` with keys `root: Path`, `milestones: {"first": str, "second": str}`, `stories: {"first": [a, b], "second": [c, d]}`, `subtasks: {"first": [a1, b1], "second": [c1, d1]}`. Reused by Task 5.

- [ ] **Step 1: Write the scenario**

Append to `tests/e2e/test_multi_process.py`:

```python
def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _local_branches(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.split()


def _assert_rolled_up(root: Path, parents) -> None:
    """Every parent's board status is `rollup_status` of its direct children."""
    for card_id in parents:
        node = board.tree(card_id, repo_dir=root)
        children = [child.status for child in node.children]
        assert children, card_id
        assert node.status == rollup.rollup_status(children), (card_id, node.status, children)


def test_two_milestones_with_different_prefixes_run_at_once_and_stay_apart(
    two_milestone_board, fake_claude_bin, rendezvous, spawn_am, finish_am
):
    """Spec scenario 2. Each milestone drives one story at a time, so the
    count-2 rendezvous can only be met by the two processes' implements
    overlapping."""
    root = two_milestone_board["root"]
    prefixes = {"first": "m10a", "second": "m10b"}
    branches = {
        side: [
            dag.task_branch(prefixes[side], board.show(card_id, repo_dir=root))
            for card_id in two_milestone_board["subtasks"][side]
        ]
        for side in prefixes
    }
    rendezvous.arm(2)

    children = {
        side: spawn_am(
            *_milestone_argv(root, two_milestone_board["milestones"][side], prefix)
        )
        for side, prefix in prefixes.items()
    }
    results = {side: _data(*finish_am(child)) for side, child in children.items()}

    assert results["first"]["run_id"] != results["second"]["run_id"]
    for side, prefix in prefixes.items():
        data = results[side]
        other = "second" if side == "first" else "first"
        integrate = f"{prefix}-integrate"
        assert data["done"] is True, data
        assert data["completed"] == two_milestone_board["subtasks"][side]
        assert data["integrated"]["branch"] == integrate
        assert data["integrated"]["merged"] == two_milestone_board["stories"][side]
        for tip in branches[side]:
            assert _is_ancestor(root, tip, integrate), (tip, integrate)
        for tip in branches[other]:
            assert not _is_ancestor(root, tip, integrate), (tip, integrate)
    assert {"m10a-integrate", "m10b-integrate"} <= set(_local_branches(root))
    # One marker per implement cwd: all four subtasks reached the rendezvous.
    assert len(rendezvous.markers()) == 4

    for side in prefixes:
        for card_id in two_milestone_board["subtasks"][side]:
            assert board.show(card_id, repo_dir=root).status == "done", card_id
    parents = [
        *(story for side in prefixes for story in two_milestone_board["stories"][side]),
        *two_milestone_board["milestones"].values(),
    ]
    _assert_rolled_up(root, parents)
    for card_id in parents:
        assert board.show(card_id, repo_dir=root).status == "done", card_id
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest "tests/e2e/test_multi_process.py::test_two_milestones_with_different_prefixes_run_at_once_and_stay_apart" -v`
Expected: ERROR with `fixture 'two_milestone_board' not found`.

- [ ] **Step 3: Add the board fixture**

In `tests/e2e/conftest.py`, insert after the `two_story_board` fixture (after its closing `    }` and before `@pytest.fixture\ndef run_milestone_cli`):

```python
@pytest.fixture
def two_milestone_board(fresh_project) -> dict[str, Any]:
    """Two milestones on one board, each with two independent one-subtask stories.

    First: A (a1) and B (b1). Second: C (c1) and D (d1). Nothing is shared, so
    two `am` processes can drive them at once; which prefix each uses is the
    test's choice, so branch names are derived in the test, never here.
    `UNION_ATTRIBUTE` lets each Integrate fold its two stories'
    `IMPLEMENTATION.md` without a resolver.
    """
    root = fresh_project
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    first = _add_card(root, "Milestone 10a: the first of two concurrent milestones")
    a = _add_card(root, "Story A: one side of the first milestone", first)
    b = _add_card(root, "Story B: other side of the first milestone", first)
    second = _add_card(root, "Milestone 10b: the second of two concurrent milestones")
    c = _add_card(root, "Story C: one side of the second milestone", second)
    d = _add_card(root, "Story D: other side of the second milestone", second)
    a1 = _add_card(root, "a1: only subtask of story A", a)
    b1 = _add_card(root, "b1: only subtask of story B", b)
    c1 = _add_card(root, "c1: only subtask of story C", c)
    d1 = _add_card(root, "d1: only subtask of story D", d)
    return {
        "root": root,
        "milestones": {"first": first, "second": second},
        "stories": {"first": [a, b], "second": [c, d]},
        "subtasks": {"first": [a1, b1], "second": [c1, d1]},
    }
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest "tests/e2e/test_multi_process.py::test_two_milestones_with_different_prefixes_run_at_once_and_stay_apart" -v`
Expected: PASS. A product-assertion failure (e.g. a lock timeout, a rollup mismatch, a tip on the wrong integrate branch) is a production bug: follow the procedure in Task 3 Step 2, placing the failing test in the tier §7 assigns (`tests/test_locks.py`, `tests/steps/test_rollup.py`, `tests/steps/test_worktree.py`, `tests/test_board.py` or `tests/test_orchestrate.py`).

---

### Task 5: Scenario 3 — a prefix already in use

**Files:**
- Test: `tests/e2e/test_multi_process.py` (append)

**Interfaces:**
- Consumes: `two_milestone_board` (Task 4); `hold`, `spawn_am`, `am`, `finish_am`, `wait_for_file` (Task 2); `_milestone_argv`, `_error`, `_data`, `_run_ids`, `_only_run_id`, `_local_branches` (Tasks 2 and 4); `PREFIX`.
- Produces: nothing new.

- [ ] **Step 1: Write the scenario**

Append to `tests/e2e/test_multi_process.py`:

```python
def test_a_second_milestone_on_a_prefix_in_use_is_refused_by_its_integration_branch(
    two_milestone_board, fake_claude_bin, hold, spawn_am, am, finish_am, wait_for_file
):
    """Spec scenario 3. The two milestones share no card, so the only key they
    both need is `branch:<prefix>-integrate`."""
    root = two_milestone_board["root"]
    a1, b1 = two_milestone_board["subtasks"]["first"]
    c1, d1 = two_milestone_board["subtasks"]["second"]
    hold.arm()
    hold.release(b1)
    live = spawn_am(
        *_milestone_argv(root, two_milestone_board["milestones"]["first"], PREFIX)
    )
    wait_for_file(hold.held_marker(a1), live)
    run_id = _only_run_id(am, root)

    error = _error(
        *am(*_milestone_argv(root, two_milestone_board["milestones"]["second"], PREFIX))
    )

    assert error["type"] == "ClaimedError", error
    assert f"branch {PREFIX}-integrate is" in error["message"]
    assert run_id in error["message"]
    assert _run_ids(am, root) == [run_id]
    for card_id in (c1, d1):
        branch = dag.task_branch(PREFIX, board.show(card_id, repo_dir=root))
        assert branch not in _local_branches(root), branch

    hold.release(a1)
    finished = _data(*finish_am(live))
    assert finished["done"] is True, finished
    assert finished["integrated"]["branch"] == f"{PREFIX}-integrate"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest "tests/e2e/test_multi_process.py::test_a_second_milestone_on_a_prefix_in_use_is_refused_by_its_integration_branch" -v`
Expected: PASS. A product-assertion failure is a production bug: follow Task 3 Step 2 (orchestrate refusals are tested in `tests/test_orchestrate.py` with claims planted over a second connection).

---

### Task 6: Scenario 4 — `am resume` takes over a killed milestone

**Files:**
- Test: `tests/e2e/test_multi_process.py` (append)

**Interfaces:**
- Consumes: `milestone_board`, `hold` (`holder_pid`), `spawn_am`, `am`, `wait_for_file`; `_milestone_argv`, `_data`, `_only_run_id`, `PREFIX`, `VERIFY`.
- Produces: helper `_kill_orphaned_fake(pid: int) -> None` in the test module.

- [ ] **Step 1: Write the scenario**

Append to `tests/e2e/test_multi_process.py`:

```python
def _kill_orphaned_fake(pid: int) -> None:
    """SIGKILL the held fake `claude` a killed `am` left behind.

    `run_direct` starts the fake in its own session, so killing `am` does not
    reach it. Left alive it would wake on the release and commit in the same
    worktree as the resumed run's implement. The pid is the one the fake wrote
    into its `.held` marker, and it is still polling because no release
    exists yet, so the pid is still that fake's.
    """
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_resume_takes_over_a_killed_milestone_and_finishes_it(
    milestone_board, fake_claude_bin, hold, spawn_am, am, wait_for_file
):
    """Spec scenario 4: a dead holder's lease is taken over, not refused."""
    root = milestone_board["root"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)
    milestone = spawn_am(*_milestone_argv(root, milestone_board["milestone"], PREFIX))
    wait_for_file(hold.held_marker(a1), milestone)
    run_id = _only_run_id(am, root)
    fake_pid = hold.holder_pid(a1)
    assert fake_pid != milestone.pid

    dead_pid = milestone.pid
    milestone.kill()
    milestone.wait()
    assert milestone.returncode == -signal.SIGKILL
    _kill_orphaned_fake(fake_pid)
    hold.release(a1)

    resumed = _data(*am("resume", run_id, "--repo-dir", str(root), "--verify", VERIFY))

    assert resumed["done"] is True, resumed
    assert resumed["resumed"] is True
    assert resumed["run_id"] == run_id
    assert resumed["took_over"]["pid"] == dead_pid
    status = _data(*am("status", run_id, "--repo-dir", str(root)))
    assert status["run"]["status"] == "done"
    assert status["control"]["claims"] == []
```

- [ ] **Step 2: Run it**

Run: `uv run pytest "tests/e2e/test_multi_process.py::test_resume_takes_over_a_killed_milestone_and_finishes_it" -v`
Expected: PASS. A product-assertion failure (e.g. `RunIsLiveError` for a dead pid, a stale lock left by the killed process, missing `took_over`) is a production bug: follow Task 3 Step 2 (`tests/test_store.py` for takeover semantics, `tests/test_locks.py` for a lock a dead holder left, `tests/test_orchestrate.py` for the payload).

---

### Task 7: Whole suite and commit

**Files:**
- No new files.

**Interfaces:**
- Consumes: everything above.
- Produces: the commit.

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, whole suite green, `tests/e2e` included, no new `e2e`-marked test, no warnings about unreaped children.

- [ ] **Step 2: Check the tree is only this subtask's work**

Run: `git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-prove-several-am-cfcfa6e3 status --porcelain`
Expected: modified `tests/e2e/fake_claude.py`, `tests/e2e/conftest.py`, `tests/e2e/test_fake_claude.py`; new `tests/e2e/test_multi_process.py`; the spec and this plan under `docs/superpowers/`; plus any `src/agent_manager/...` fix and its test only if a scenario found a bug.

- [ ] **Step 3: Commit**

```bash
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-prove-several-am-cfcfa6e3 add tests/e2e/fake_claude.py tests/e2e/conftest.py tests/e2e/test_fake_claude.py tests/e2e/test_multi_process.py docs/superpowers/specs/task-prove-several-am-cfcfa6e3-design.md docs/superpowers/plans/task-prove-several-am-cfcfa6e3.md
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-prove-several-am-cfcfa6e3 commit -m "test(e2e): several am processes on one repository"
```

If a scenario found a bug, also `git add` the `src/agent_manager/...` fix and its test file before committing. Do not push.
