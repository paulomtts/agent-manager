<!-- task-pipeline: validated -->
# Prove parallel stories under a fake claude with a rendezvous (card 1976123f)

Parent: story 4633be8c ("Prove it: parallel under a fake claude, against a real harness, and documented"), milestone cdbfa10d (Milestone 4). Governing docs: the main design spec `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (section 14, test tiers) and the parallel-stories addendum (decisions P1, P4, P5, P7; section 5 lists what is out of scope).

Note on inputs: the exploration summary for this card was cut off at its 8000-character cap, so this spec relies only on the part that came through. I checked the cited code in this worktree, which contains the merged m4 code: `orchestrate.run_milestone(..., max_concurrent=...)`, the `stopped` status, `should_stop`, `--max-concurrent`, and the escalation payload keys `also_escalated` and `stopped`, whose entries are `{story, subtask, before_phase}`.

## Scope

Only test code, all under `tests/e2e/` (P7). No file under `src/` changes. This card does not cover the real-harness `-m e2e` test (sibling 2af0e413) or the README and main-spec section 11 docs (sibling a2dad516). Also out of scope: Integrate, per-story readiness, milestone-aware resume, watch/retry/cancel, cost capture, the reviewer Plan-Hash brief, and Ctrl-C.

## A. Rendezvous in the fake claude (`tests/e2e/fake_claude.py`, extended in place, not copied)

- New module constants: `RENDEZVOUS_DIR_ENV = "FAKE_CLAUDE_RENDEZVOUS_DIR"`, `RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"`, and a timeout of 20 seconds.
- In the `implement` phase only, and only when `FAKE_CLAUDE_RENDEZVOUS_DIR` is set, the fake does three things:
  1. It writes a marker file in that dir. The file name comes from its own cwd, is filesystem-safe, and is the same on every run for the same cwd.
  2. It polls until the number of marker files is at least `FAKE_CLAUDE_RENDEZVOUS_COUNT`.
  3. It then continues with the normal implement behaviour.
- If the count is still short after the timeout, the fake raises `FakeClaudeError` naming the dir, the count it saw and the count it needed, so `__main__` exits 1. A missing or non-integer count while the dir is set is also a `FakeClaudeError`.
- When the env var is unset, the fake behaves exactly as it does today: no wait and no files.
- The existing design rule still holds. The fake stays stdlib-only, imports nothing from `agent_manager`, takes every other input (result path, plan hash, branch) from the brief, and never commits the spec or plan. The rendezvous dir and the review-fail marker are the only inputs the test controls.
- Markers accumulate for the whole run. After a count is reached once, every later implement in the same run passes straight through.
- Pin the two env-var names in `tests/e2e/conftest.py`, next to `FAKE_LOG_NAME` and `FAKE_REVIEW_FAIL_MARKER`, as mirrored constants (`FAKE_RENDEZVOUS_DIR_ENV`, `FAKE_RENDEZVOUS_COUNT_ENV`). Add equality pins in `test_fake_claude.py` in the same way the existing ones are written.

## B. Parallel milestone tests (new module `tests/e2e/test_parallel_milestone.py`)

- **Production wiring:** no `runner_factory` or `driver` override. The run goes through `cli.default_runner_factory`, the real `ClaudeAdapter`, `harness.launcher.run_direct`, and the fake first on `PATH` (the `fake_claude_bin` fixture).
- **How the tests run a milestone:** either call `orchestrate.run_milestone(..., max_concurrent=N)` directly or invoke `am run --milestone ... --max-concurrent N`. For the CLI route, extend `run_milestone_cli` with an optional `max_concurrent` argument, or add a sibling fixture; the existing callers must keep their current argv.
- **Environment:** tests set both env vars with `monkeypatch.setenv`. `run_direct` calls `Popen` without `env=`, so the child inherits `os.environ`; this is confirmed at `src/agent_manager/harness/launcher.py:132`.
- **Board:** a new function-scoped fixture (for example `parallel_board`) built on `fresh_project`, which gives each test its own repo, board and `XDG_DATA_HOME`. It uses `_add_card(..., blocked_by=[...])`, `dag.task_branch(MILESTONE_PREFIX, ...)` and `MILESTONE_PREFIX` from conftest.
  - Story A has subtasks a1 -> a2.
  - Story B is independent of A and has subtasks b1 -> b2.
  - Story C is blocked by A and has one subtask, c1.
  - The fixture also exposes a review-fail marker path, `<root>/.git/fake-claude-review-fail`. The existing `review_fail_marker` fixture depends on `milestone_board`, so it cannot be reused here.
- **Unmarked:** the module is not marked `e2e` and carries a guard test like `test_this_module_runs_in_the_default_suite_unmarked` in `test_milestone_run.py`.

### Observable behaviour to prove

1. **Two lanes, count 2.** `max_concurrent=2`, rendezvous count 2, and the run completes.
   - The run cannot complete unless a1 and b1 were in implement at the same time, so completion proves the overlap.
   - Every subtask branch contains its predecessor (`_is_ancestor`).
   - c1's branch roots on A's tip.
   - Board status for stories A, B and C and for the milestone is `done` (`board.show(...).status`).
   - `git rev-parse main` is the same before and after the run.
2. **One lane, count 1.** `max_concurrent=1`, rendezvous count 1, and the run completes. The recorded `PhaseRun.started_at`/`ended_at` intervals are loaded through `store.open_db(cli.resolve_repo_dir(root))` and `store.load_run`. The span of story A's subtasks and the span of story B's subtasks do not overlap. This shows that `--max-concurrent 1` still behaves as the sequential runner did.
3. **Journal integrity.** After the run in test 1:
   - the journal's sequence numbers are unique and contiguous;
   - `Store.rebuild_from_journal(run_id)` equals the DB projection loaded by `load_run`.
4. **Escalation in one lane.** `max_concurrent=2`, rendezvous count 2, and a1's branch written into the review-fail marker.
   - Exit code is `cli.EXIT_ESCALATED` (via the CLI), or the report says `escalated: true` (direct call).
   - The report's `story` is A.
   - `stopped` contains exactly one entry for B's lane: `{story: B, subtask: <b1 or b2>, before_phase: <non-null phase>}`.
   - That subtask's recorded status is `stopped`. No attempt exists for its `before_phase` or any later phase.
   - Story C never started: `cli.worktree_for(root, c1_branch)` does not exist, `c1_branch` does not exist, and there is no c1 attempt and no fake-log entry with c1's cwd.
5. **Relaunch.** Test 4 continues: remove the marker and run the same milestone again.
   - The milestone completes.
   - Subtasks that finished `done` in the first run are reported in `completed` or as skipped, and the second run's `read_fake_log(run_id)` has no entry with their cwds.
   - c1 runs, and the stories and milestone end `done`.

### Determinism of test 4 (an open point for the plan)

A count-based rendezvous lets every waiting process go at once, so on its own it cannot hold B inside implement while A goes on to review. The rendezvous in test 4 does guarantee three things:

- a1 and b1 overlap;
- B has more work left after that point than A does (b1's remaining phases plus all of b2, against a1's verify and review);
- the stop is observed at B's next phase boundary.

The assertions above are therefore written to be correct at whichever boundary B stops: they check `before_phase` and "no later attempt", not "stopped in implement". The plan must either accept this ordering margin, adding more subtasks to B if needed, or find a strictly deterministic hold that still uses only the rendezvous dir and the review-fail marker. It must not add a new test input to the fake.

### Error paths

- A rendezvous that times out fails the fake, and so fails the attempt. Tests must never depend on the timeout to pass: a passing run is fast and a failing one takes about 20 s.
- The whole default suite (`uv run pytest`) stays green.
- Threads share one process. Nothing starts two `am` processes.

## Test list

Tier rule: main spec section 14. In this repo, `tests/e2e/` holds the unmarked production-wiring tier (the fake claude on `PATH` with the real adapter and launcher). Real-harness tests are marked `e2e`. The injected-driver tier is `tests/test_orchestrate.py` and is not used here.

| Test | File | Tier |
|---|---|---|
| env-var constants pinned equal to conftest mirrors | tests/e2e/test_fake_claude.py | e2e dir, production-wiring tier (unmarked), fake self-test |
| rendezvous unset: implement does not wait and writes no marker | tests/e2e/test_fake_claude.py | same |
| rendezvous count reached: marker named for cwd written, implement proceeds | tests/e2e/test_fake_claude.py | same |
| rendezvous unmet: fake fails with `FakeClaudeError`, exit 1 (short timeout injected via a module-level constant patched in-process, so the test stays fast) | tests/e2e/test_fake_claude.py | same |
| module runs in default suite unmarked (guard) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| two lanes overlap and complete (1) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| one lane never overlaps (2) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| journal contiguous, rebuild equals projection (3) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| escalation stops the other lane, later level never starts (4) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |
| relaunch after escalation completes and skips done subtasks (5) | tests/e2e/test_parallel_milestone.py | production-wiring tier, unmarked |

---

# Parallel Stories Under a Fake Claude Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, in the default-suite production-wiring tier, that `am run --milestone --max-concurrent N` really runs a level's stories in parallel, stays sequential at `N=1`, keeps a consistent journal, stops sibling lanes on an escalation, and resumes cleanly on relaunch.

**Architecture:** The fake `claude` (`tests/e2e/fake_claude.py`) gains an env-driven, implement-only rendezvous: each implement writes a cwd-named marker into a test-owned directory and waits until the marker count reaches a test-set number, so a run with count 2 can only complete if two lanes were inside implement at once. New conftest fixtures build a two-root board (A: a1->a2, B: b1->b2, C blocked by A), arm/disarm the rendezvous, and pass `--max-concurrent` through the existing CLI fixture. A new unmarked module `tests/e2e/test_parallel_milestone.py` drives `cli.app` through `CliRunner` with no `runner_factory` and no `driver`, so the real adapter, the real `launcher.run_direct` and real child processes run.

**Tech Stack:** Python 3, pytest (`--import-mode=importlib -m "not e2e"`), Typer `CliRunner`, git, brd, the repo's `uv` toolchain.

**Spec:** `docs/superpowers/specs/task-prove-parallel-stories-1976123f-design.md` (reproduced verbatim above).

Input note: both upstream summaries handed to this planning stage (the spec author's summary and the exploration summary) were truncated at their character caps. This plan was written from the spec file on disk and from the code in this worktree, not from those summaries, so nothing in it depends on the missing text.

## Global Constraints

- Only files under `tests/e2e/` change. No file under `src/` changes (P7).
- `tests/e2e/fake_claude.py` stays standard-library only, imports nothing from `agent_manager`, never hashes the plan (reads `## plan_hash`), and never commits the spec or the plan.
- The only test-controlled inputs to the fake are the rendezvous dir (`FAKE_CLAUDE_RENDEZVOUS_DIR`, with `FAKE_CLAUDE_RENDEZVOUS_COUNT`) and the review-fail marker `<git common dir>/fake-claude-review-fail`. No other env var or argv flag is added.
- Rendezvous timeout: 20 seconds (`RENDEZVOUS_TIMEOUT = 20.0`). No passing test may depend on the timeout firing at that length; the timeout test patches it down in-process.
- The new module is unmarked (no `e2e` marker) and carries the `test_this_module_runs_in_the_default_suite_unmarked` guard.
- Each scenario gets its own repo and board (`fresh_project` is function-scoped).
- The existing `run_milestone_cli` callers keep their current argv (no `--max-concurrent` unless asked for).
- Threads share one process; nothing starts two `am` processes.
- Verification: `uv run pytest` (there is no separate lint or typecheck command).
- Out of scope: the real-harness `-m e2e` test (2af0e413), README / main-spec section 11 docs (a2dad516), Integrate, per-story readiness, milestone-aware resume, watch/retry/cancel, cost capture, the reviewer Plan-Hash brief, Ctrl-C.

## Review Focus

1. A rendezvous count of `0` or a negative number while the dir is set: a reasonable person expects a refusal, not a rendezvous that silently passes; pinned by the parametrised bad-count test in Task 1.
2. A rendezvous dir that does not exist yet: the fake should create it rather than crash with `FileNotFoundError`; pinned by the missing-dir test in Task 1.
3. The same cwd arriving twice (a retried or relaunched implement in one worktree): it must count once, so one lane alone can never satisfy a count of 2; pinned by the same-cwd-twice test in Task 1.
4. A rendezvous armed in one test leaking into later tests through the process environment: later fakes would wait on a stale dir; pinned by the module-final env-leak guard in Task 4.
5. Test 4's ordering margin (lane B finishing entirely before lane A escalates): the test must fail with a message that names the cause, not a `KeyError`; pinned by the explicit `"stopped" in data` assertion with a message in Task 4.

## Resolution of the spec's open point (determinism of test 4)

Decision: accept the ordering margin; do not add a new input to the fake. Reasoning, from the code in this worktree:

- `builtin/task.yaml` orders the agent-bearing tail as `implement -> review -> verify -> mark_done`. With count 2, a1 and b1 leave implement together. Lane A then runs one fake process (a1's `review`), and `orchestrate.run_story_lane` calls `stop.escalate` as soon as `drive` returns the escalated summary.
- For lane B to finish without being stopped it would have to run b1's `review`, `verify`, `mark_done`, then all of b2 (`worktree`, `explore`, `mark_in_progress`, `plan_check`, `spec`, `validate_spec`, `plan`, `validate_plan`, `mark_validated`, `docs_commit`, `implement`, `review`, `verify`, `mark_done`): seven more fake processes plus several git and brd calls, against lane A's one. B already has two subtasks, which is the spec's "adding more subtasks to B if needed".
- `engine.run_subtask` asks `should_stop` before every phase, so B parks at whichever boundary comes first. The assertions are boundary-agnostic: they read `before_phase` out of the report and check the recorded phases against `load_builtin("task").phase_names`.
- A strictly deterministic hold was considered: running the milestone on a background thread and having the test delete or add marker files mid-run. It still races (the test's poll latency against the fakes'), adds a thread to the test, and moves the rendezvous's meaning from "overlap" to "test choreography". Rejected.
- If the margin is ever lost, the test fails on `assert "stopped" in data` with a message naming the cause, rather than on an opaque `KeyError`.

## Other deliberate choices

- Spec test 3 checks the journal "after the run in test 1". Here it runs its own two-lane run through the same `_run_two_lanes` helper, because `fresh_project` is function-scoped and each test must own its board. The run's shape is the same.
- Spec test 5 "continues test 4". Here it is a separate test that repeats test 4's launch through `_launch_with_a1_review_failing` and then relaunches, so each row of the spec's test list is one test that can fail on its own.
- The relaunch disarms the rendezvous. The rendezvous has no role in a relaunch, and leaving it armed would only pass through on the markers the first run left.

## File Structure

- Modify `tests/e2e/fake_claude.py`: add the rendezvous constants, `rendezvous_marker_name`, `_rendezvous_count`, `rendezvous`, and one call at the top of `build_result`'s `implement` branch; update the module docstring's "one test-controlled input" paragraph.
- Modify `tests/e2e/test_fake_claude.py`: append the rendezvous pins and self-tests.
- Modify `tests/e2e/conftest.py`: add the `FAKE_RENDEZVOUS_DIR_ENV` / `FAKE_RENDEZVOUS_COUNT_ENV` mirrors, the `Rendezvous` helper and `rendezvous` fixture, the `parallel_board` fixture, and an optional `max_concurrent` on `run_milestone_cli`.
- Create `tests/e2e/test_parallel_milestone.py`: the guard, the five behaviour tests, and the env-leak guard.

---

### Task 1: Rendezvous in the fake claude

**Files:**
- Modify: `tests/e2e/fake_claude.py:1-27` (docstring and imports), `tests/e2e/fake_claude.py:202-216` (new constants after `REVIEW_FAIL_PORCELAIN`), `tests/e2e/fake_claude.py:303-308` (`implement` branch)
- Modify: `tests/e2e/conftest.py:51-52` (mirrors after `FAKE_REVIEW_FAIL_MARKER`)
- Test: `tests/e2e/test_fake_claude.py` (append at end of file)

**Interfaces:**
- Consumes: the existing `fake_claude.FakeClaudeError`, `build_result`, and the test helpers `_implement_repo`, `_implement`, `_head`, `_brief`, `_run_fake`, `IMPLEMENT_SCHEMA`, `PLAN_RELATIVE`, `BRIEF_HASH` already in `tests/e2e/test_fake_claude.py`.
- Produces (in `tests/e2e/fake_claude.py`):
  - `RENDEZVOUS_DIR_ENV: str = "FAKE_CLAUDE_RENDEZVOUS_DIR"`
  - `RENDEZVOUS_COUNT_ENV: str = "FAKE_CLAUDE_RENDEZVOUS_COUNT"`
  - `RENDEZVOUS_TIMEOUT: float = 20.0` (read at call time, so tests can patch it)
  - `RENDEZVOUS_POLL: float = 0.05`
  - `RENDEZVOUS_SUFFIX: str = ".arrived"`
  - `rendezvous_marker_name(cwd) -> str` (16 hex chars of sha256 of the resolved cwd, plus `RENDEZVOUS_SUFFIX`)
  - `rendezvous(cwd) -> None` (no-op when the dir env var is unset or empty; raises `FakeClaudeError` on a bad count or a timeout)
- Produces (in `tests/e2e/conftest.py`): `FAKE_RENDEZVOUS_DIR_ENV = "FAKE_CLAUDE_RENDEZVOUS_DIR"`, `FAKE_RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-prove-parallel-stories-1976123f/tests/e2e/test_fake_claude.py`:

```python
def test_the_rendezvous_env_var_names_are_the_ones_the_e2e_fixtures_set():
    """`tests/e2e/conftest.py` sets `FAKE_RENDEZVOUS_DIR_ENV` and
    `FAKE_RENDEZVOUS_COUNT_ENV`; the fixture and the script meet across a
    process boundary, like `LOG_NAME` and `REVIEW_FAIL_MARKER`."""
    assert fake_claude.RENDEZVOUS_DIR_ENV == "FAKE_CLAUDE_RENDEZVOUS_DIR"
    assert fake_claude.RENDEZVOUS_COUNT_ENV == "FAKE_CLAUDE_RENDEZVOUS_COUNT"
    assert fake_claude.RENDEZVOUS_TIMEOUT == 20


def test_a_rendezvous_marker_name_is_stable_per_cwd_and_filesystem_safe(tmp_path):
    first = tmp_path / "worktrees" / "m3" / "task-a1-00000001"
    second = tmp_path / "worktrees" / "m3" / "task-b1-00000002"

    name = fake_claude.rendezvous_marker_name(first)

    assert name == fake_claude.rendezvous_marker_name(first)
    assert name != fake_claude.rendezvous_marker_name(second)
    assert name.endswith(fake_claude.RENDEZVOUS_SUFFIX)
    stem = name[: -len(fake_claude.RENDEZVOUS_SUFFIX)]
    assert len(stem) == 16 and set(stem) <= set("0123456789abcdef")


def test_without_a_rendezvous_dir_implement_neither_waits_nor_writes_a_marker(
    tmp_path, monkeypatch
):
    """Unset means today's behaviour exactly. The count is set to something
    unmeetable and the timeout is short, so any wait would raise."""
    monkeypatch.delenv(fake_claude.RENDEZVOUS_DIR_ENV, raising=False)
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "99")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)
    repo = _implement_repo(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())

    payload = _implement(repo)

    assert payload["resumed"] is False
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_a_met_rendezvous_writes_a_marker_named_for_the_cwd_and_implements(
    tmp_path, monkeypatch
):
    """Review focus: the dir does not exist yet, and the fake creates it."""
    folder = tmp_path / "rendezvous" / "created-by-the-fake"
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(folder))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "1")
    repo = _implement_repo(tmp_path)
    before = _head(repo)

    payload = _implement(repo)

    marker = folder / fake_claude.rendezvous_marker_name(repo)
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == str(repo)
    assert payload["resumed"] is False
    assert payload["plan_hash"] == BRIEF_HASH
    assert _head(repo) != before


def test_a_rendezvous_counts_markers_other_lanes_left(tmp_path, monkeypatch):
    """Count 2 with one peer marker already present: the second arrival passes
    straight through, which is how two lanes release each other."""
    folder = tmp_path / "rendezvous"
    folder.mkdir()
    (folder / ("0" * 16 + fake_claude.RENDEZVOUS_SUFFIX)).write_text(
        "peer\n", encoding="utf-8"
    )
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(folder))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "2")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 5.0)
    repo = _implement_repo(tmp_path)

    payload = _implement(repo)

    assert payload["resumed"] is False
    assert len(list(folder.glob(f"*{fake_claude.RENDEZVOUS_SUFFIX}"))) == 2


def test_an_unmet_rendezvous_fails_the_fake_before_it_commits(tmp_path, monkeypatch):
    folder = tmp_path / "rendezvous"
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(folder))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "2")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)
    repo = _implement_repo(tmp_path)
    before = _head(repo)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _implement(repo)

    message = str(caught.value)
    assert str(folder) in message
    assert "saw 1 of 2" in message
    assert _head(repo) == before


def test_the_same_cwd_arriving_twice_counts_once(tmp_path, monkeypatch):
    """Review focus: a retried implement in one worktree must not satisfy a
    count of 2 on its own, or a single lane would fake an overlap."""
    folder = tmp_path / "rendezvous"
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(folder))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "2")
    monkeypatch.setattr(fake_claude, "RENDEZVOUS_TIMEOUT", 0.2)
    repo = _implement_repo(tmp_path)

    for _ in range(2):
        with pytest.raises(fake_claude.FakeClaudeError):
            fake_claude.rendezvous(repo)

    assert len(list(folder.glob(f"*{fake_claude.RENDEZVOUS_SUFFIX}"))) == 1


@pytest.mark.parametrize("raw", [None, "", "two", "1.5", "0", "-1"])
def test_a_missing_or_bad_rendezvous_count_is_refused(tmp_path, monkeypatch, raw):
    """Review focus: a count below 1 would pass silently, and a non-number
    would be a guess; both are refusals naming the env var."""
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(tmp_path / "rendezvous"))
    if raw is None:
        monkeypatch.delenv(fake_claude.RENDEZVOUS_COUNT_ENV, raising=False)
    else:
        monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, raw)

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.rendezvous(tmp_path)

    assert fake_claude.RENDEZVOUS_COUNT_ENV in str(caught.value)


def test_a_rendezvous_failure_makes_the_fake_process_exit_1(tmp_path, monkeypatch):
    """The `__main__` mapping, end to end: the child inherits the env (as it does
    under `launcher.run_direct`), refuses the count, writes no result."""
    monkeypatch.setenv(fake_claude.RENDEZVOUS_DIR_ENV, str(tmp_path / "rendezvous"))
    monkeypatch.setenv(fake_claude.RENDEZVOUS_COUNT_ENV, "two")
    attempt = tmp_path / "runs" / "r1" / "card" / "implement.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path,
        "implement",
        "coder",
        f"\n## plan_path\n{PLAN_RELATIVE}\n\n## plan_hash\n{BRIEF_HASH}\n",
        IMPLEMENT_SCHEMA,
        result_path,
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 1
    assert "FAKE_CLAUDE_RENDEZVOUS_COUNT" in completed.stderr
    assert not result_path.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v -k "rendezvous"`
Expected: FAIL. The in-process tests fail with `AttributeError: module 'e2e_fake_claude' has no attribute 'RENDEZVOUS_DIR_ENV'` (or `rendezvous_marker_name` / `rendezvous`); the subprocess test fails because `returncode` is 0 (the env var is ignored today).

- [ ] **Step 3: Implement the rendezvous in the fake**

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-prove-parallel-stories-1976123f/tests/e2e/fake_claude.py`, replace the docstring paragraph (lines 11-16):

```python
(`prompt.py:281-374`). There is deliberately no environment variable, no extra
argv flag and no import of `agent_manager` -- a brief that omits the contract
must make this script fail, because that failure is the test's whole point.
The one test-controlled input is `REVIEW_FAIL_MARKER`, a file in the repo's git
common dir that the fake finds from its own cwd and compares with the brief's
`## branch`.
```

with:

```python
(`prompt.py:281-374`). There is deliberately no extra argv flag and no import
of `agent_manager` -- a brief that omits the contract must make this script
fail, because that failure is the test's whole point. There are exactly two
test-controlled inputs, and neither tells the fake anything the brief owns:
`REVIEW_FAIL_MARKER`, a file in the repo's git common dir that the fake finds
from its own cwd and compares with the brief's `## branch`; and the
implement-only rendezvous (`RENDEZVOUS_DIR_ENV` / `RENDEZVOUS_COUNT_ENV`),
which only makes implement wait for other lanes and changes nothing it writes.
```

Replace the imports block (lines 21-27):

```python
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
```

with:

```python
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
```

Immediately after the `REVIEW_FAIL_PORCELAIN` constant and its docstring (after line 215), insert:

```python
RENDEZVOUS_DIR_ENV = "FAKE_CLAUDE_RENDEZVOUS_DIR"
"""Test scaffolding, never in a brief: a directory where each implement leaves a
marker named for its cwd and then waits for other lanes' markers. Unset or
empty means no rendezvous at all. The parallel milestone tests set it so a run
can only finish if two lanes were inside implement at the same time."""

RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"
"""How many markers implement waits for; a whole number of at least 1."""

RENDEZVOUS_TIMEOUT = 20.0
"""Seconds to wait before failing. Read at call time, so a self-test can patch it."""

RENDEZVOUS_POLL = 0.05
"""Seconds between marker counts."""

RENDEZVOUS_SUFFIX = ".arrived"
"""Only files with this suffix count, so nothing else in the dir can release a wait."""


def rendezvous_marker_name(cwd):
    """A filesystem-safe marker name that is the same on every run for one cwd.

    Hashed rather than escaped, so a path of any shape or length is safe. One
    name per cwd means a retried implement in the same worktree counts once.
    """
    digest = hashlib.sha256(str(Path(cwd).resolve()).encode("utf-8")).hexdigest()
    return digest[:16] + RENDEZVOUS_SUFFIX


def _rendezvous_count(raw):
    try:
        needed = int(raw)
    except (TypeError, ValueError):
        raise FakeClaudeError(
            f"{RENDEZVOUS_DIR_ENV} is set but {RENDEZVOUS_COUNT_ENV} is {raw!r}, "
            "not a whole number"
        ) from None
    if needed < 1:
        raise FakeClaudeError(
            f"{RENDEZVOUS_COUNT_ENV} must be at least 1, got {needed}"
        )
    return needed


def rendezvous(cwd):
    """Leave this cwd's marker and wait until enough lanes have left theirs.

    A no-op unless `RENDEZVOUS_DIR_ENV` is set. Markers are never removed, so
    once a run reaches the count every later implement passes straight through.
    """
    directory = os.environ.get(RENDEZVOUS_DIR_ENV)
    if not directory:
        return
    needed = _rendezvous_count(os.environ.get(RENDEZVOUS_COUNT_ENV))
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / rendezvous_marker_name(cwd)).write_text(f"{cwd}\n", encoding="utf-8")
    deadline = time.monotonic() + RENDEZVOUS_TIMEOUT
    while True:
        seen = len(list(folder.glob(f"*{RENDEZVOUS_SUFFIX}")))
        if seen >= needed:
            return
        if time.monotonic() >= deadline:
            raise FakeClaudeError(
                f"rendezvous in {folder} timed out after {RENDEZVOUS_TIMEOUT}s: "
                f"saw {seen} of {needed} marker(s)"
            )
        time.sleep(RENDEZVOUS_POLL)
```

In `build_result`, replace the start of the implement branch:

```python
    if phase == "implement":
        # Card f26b377d: the hash comes from the brief's `## plan_hash` section,
```

with:

```python
    if phase == "implement":
        # Test scaffolding: wait here for the other lanes, before any work, so
        # an unmet rendezvous fails without committing anything.
        rendezvous(cwd)
        # Card f26b377d: the hash comes from the brief's `## plan_hash` section,
```

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-prove-parallel-stories-1976123f/tests/e2e/conftest.py`, after the `FAKE_REVIEW_FAIL_MARKER` constant and its docstring (after line 52), insert:

```python
FAKE_RENDEZVOUS_DIR_ENV = "FAKE_CLAUDE_RENDEZVOUS_DIR"
"""Must equal `fake_claude.RENDEZVOUS_DIR_ENV`, which `test_fake_claude.py` pins."""

FAKE_RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"
"""Must equal `fake_claude.RENDEZVOUS_COUNT_ENV`, which `test_fake_claude.py` pins."""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: PASS, every test in the file (the old ones included), in a few seconds (no test waits longer than 0.2 s on the rendezvous).

- [ ] **Step 5: Run the existing production-wiring tier to verify nothing changed with the env unset**

Run: `uv run pytest tests/e2e -v`
Expected: PASS. `test_parallel_milestone.py` does not exist yet; `test_production_wiring.py` and `test_milestone_run.py` pass unchanged because neither sets the rendezvous env vars.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_fake_claude.py tests/e2e/conftest.py
git commit -m "test(e2e): add an implement-only rendezvous to the fake claude"
```

---

### Task 2: Parallel board fixtures and the two-lane overlap test

**Files:**
- Modify: `tests/e2e/conftest.py:11-23` (imports), after the `milestone_board` fixture (line 267) (new fixtures), `tests/e2e/conftest.py:270-295` (`run_milestone_cli`)
- Create: `tests/e2e/test_parallel_milestone.py`

**Interfaces:**
- Consumes: `FAKE_RENDEZVOUS_DIR_ENV`, `FAKE_RENDEZVOUS_COUNT_ENV` (Task 1, conftest); the fake's marker files, one per distinct implement cwd (Task 1); existing conftest `fresh_project`, `_add_card`, `dag.task_branch`, `board.show`, `MILESTONE_PREFIX`, `FAKE_REVIEW_FAIL_MARKER`, `fake_claude_bin`, `VERIFY_COMMANDS`, `read_fake_log`.
- Produces (conftest):
  - `class Rendezvous` (dataclass) with `directory: Path`, `monkeypatch: pytest.MonkeyPatch`, `arm(count: int) -> Path`, `disarm() -> None`, `markers() -> list[Path]`.
  - fixture `rendezvous(tmp_path, monkeypatch) -> Rendezvous`, directory `tmp_path / "rendezvous"` (outside the repo at `tmp_path / "project"`).
  - fixture `parallel_board(fresh_project) -> dict[str, Any]` with keys `root: Path`, `milestone: str`, `stories: {"A","B","C"} -> str`, `subtasks: {"A": [a1, a2], "B": [b1, b2], "C": [c1]}`, `branches: {card_id: branch}`, `review_fail_marker: Path`.
  - `run_milestone_cli(root: Path, milestone: str, max_concurrent: int | None = None) -> click.testing.Result`; argv unchanged when `max_concurrent is None`, otherwise adds `--max-concurrent <n>`.
- Produces (test module helpers, used by Tasks 3 and 4): `_git`, `_is_ancestor`, `_envelope`, `_load_run`, `_cwds`, `_all_cards`, `_subtask_rows`, `_run_two_lanes(parallel_board, rendezvous, run_milestone_cli) -> click.testing.Result`.

- [ ] **Step 1: Write the failing test module**

Create `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-prove-parallel-stories-1976123f/tests/e2e/test_parallel_milestone.py`:

```python
"""Default-suite e2e tier: parallel stories through the production wiring.

Addendum P7 and main spec section 14: `am run --milestone --max-concurrent N`
runs through `typer.testing.CliRunner` on the real `cli.app` with no
`runner_factory` and no `driver`, so `orchestrate.run_milestone` reaches
`cli.drive_subtask`, `cli.default_runner_factory`, the real `ClaudeAdapter` and
`launcher.run_direct`. The only stand-in is the fake `claude` first on `PATH`,
armed with an implement-only rendezvous: at count 2 a run can only finish if
two lanes were inside implement at the same time. Unmarked on purpose.

Each test builds its own repo and board (`parallel_board`): A (a1 -> a2) and B
(b1 -> b2) are independent roots, C (c1) is blocked by A.
"""

import json
import subprocess
from pathlib import Path

from agent_manager import board, cli, models, store


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _cwds(entries) -> set[Path]:
    return {Path(entry["cwd"]).resolve() for entry in entries}


def _all_cards(parallel_board) -> list[str]:
    return [
        *(card for chain in parallel_board["subtasks"].values() for card in chain),
        *parallel_board["stories"].values(),
        parallel_board["milestone"],
    ]


def _subtask_rows(run: models.Run) -> dict[str, models.SubtaskRun]:
    return {
        subtask.card_id: subtask
        for story in run.stories
        for subtask in story.subtasks
    }


def _run_two_lanes(parallel_board, rendezvous, run_milestone_cli):
    """Two lanes, rendezvous count 2: finishing at all proves a1 and b1 overlapped."""
    rendezvous.arm(2)
    return run_milestone_cli(
        parallel_board["root"], parallel_board["milestone"], max_concurrent=2
    )


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """No `e2e` marker may reach this module, or parallel wiring stops being
    checked on every `uv run pytest`."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_two_lanes_overlap_in_implement_and_the_milestone_finishes(
    parallel_board, rendezvous, run_milestone_cli
):
    """Spec test 1."""
    root = parallel_board["root"]
    stories = parallel_board["stories"]
    subtasks = parallel_board["subtasks"]
    branches = parallel_board["branches"]
    a1, a2 = subtasks["A"]
    b1, b2 = subtasks["B"]
    (c1,) = subtasks["C"]
    main_before = _git(root, "rev-parse", "main").strip()

    result = _run_two_lanes(parallel_board, rendezvous, run_milestone_cli)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert [level["stories"] for level in data["levels"]] == [
        [stories["A"], stories["B"]],
        [stories["C"]],
    ]
    assert data["completed"] == [a1, a2, b1, b2, c1]
    # The env reached every implement child: one marker per subtask worktree.
    assert len(rendezvous.markers()) == 5

    for card_id in _all_cards(parallel_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id

    # Each story is its own stacked line, rooted where the DAG says.
    assert _is_ancestor(root, branches[a1], branches[a2])
    assert _is_ancestor(root, branches[b1], branches[b2])
    assert _is_ancestor(root, "main", branches[b1])
    assert not _is_ancestor(root, branches[a1], branches[b1])
    # C is blocked by A, so c1 roots on A's tip.
    assert _is_ancestor(root, branches[a2], branches[c1])
    assert not _is_ancestor(root, branches[b2], branches[c1])

    assert _git(root, "rev-parse", "main").strip() == main_before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/e2e/test_parallel_milestone.py -v`
Expected: the guard test PASSES; `test_two_lanes_overlap_in_implement_and_the_milestone_finishes` ERRORS with `fixture 'parallel_board' not found`.

- [ ] **Step 3: Add the fixtures and the `--max-concurrent` pass-through**

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-prove-parallel-stories-1976123f/tests/e2e/conftest.py`, replace:

```python
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
```

with:

```python
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
```

Immediately after the `milestone_board` fixture (after its `return {...}` ending at line 267), insert:

```python
@dataclass
class Rendezvous:
    """Arms and disarms the fake's implement-only rendezvous for one test.

    Env vars go through the test's own function-scoped `monkeypatch`, so they
    are undone when the test ends. Child processes inherit them: `run_direct`
    calls `Popen` with no `env=` (`harness/launcher.py:132`).
    """

    directory: Path
    monkeypatch: pytest.MonkeyPatch

    def arm(self, count: int) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.monkeypatch.setenv(FAKE_RENDEZVOUS_DIR_ENV, str(self.directory))
        self.monkeypatch.setenv(FAKE_RENDEZVOUS_COUNT_ENV, str(count))
        return self.directory

    def disarm(self) -> None:
        self.monkeypatch.delenv(FAKE_RENDEZVOUS_DIR_ENV, raising=False)
        self.monkeypatch.delenv(FAKE_RENDEZVOUS_COUNT_ENV, raising=False)

    def markers(self) -> list[Path]:
        """The markers the fake left, one per distinct implement cwd."""
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.iterdir())


@pytest.fixture
def rendezvous(tmp_path, monkeypatch) -> Rendezvous:
    """The test's rendezvous, unarmed. Its dir is beside the repo, never inside it."""
    return Rendezvous(directory=tmp_path / "rendezvous", monkeypatch=monkeypatch)


@pytest.fixture
def parallel_board(fresh_project) -> dict[str, Any]:
    """One milestone: A (a1 -> a2) and B (b1 -> b2) independent, C (c1) blocked by A.

    Level 0 is A and B, level 1 is C. Subtasks are chained with `brd block`, so
    the census order does not depend on timestamps. Branch names come from
    `dag`, never retyped here. `review_fail_marker` is where the fake looks for
    branches whose review must fail; the test writes it and removes it.
    """
    root = fresh_project
    milestone = _add_card(root, "Milestone 4: parallel stories under a fake claude")
    a = _add_card(root, "Story A: an independent root story", milestone)
    b = _add_card(root, "Story B: independent of story A", milestone)
    c = _add_card(root, "Story C: blocked by story A", milestone, blocked_by=[a])
    a1 = _add_card(root, "a1: first subtask of story A", a)
    a2 = _add_card(root, "a2: second subtask of story A", a, blocked_by=[a1])
    b1 = _add_card(root, "b1: first subtask of story B", b)
    b2 = _add_card(root, "b2: second subtask of story B", b, blocked_by=[b1])
    c1 = _add_card(root, "c1: only subtask of story C", c)
    subtasks = {"A": [a1, a2], "B": [b1, b2], "C": [c1]}
    branches = {
        card_id: dag.task_branch(MILESTONE_PREFIX, board.show(card_id, repo_dir=root))
        for chain in subtasks.values()
        for card_id in chain
    }
    return {
        "root": root,
        "milestone": milestone,
        "stories": {"A": a, "B": b, "C": c},
        "subtasks": subtasks,
        "branches": branches,
        "review_fail_marker": root / ".git" / FAKE_REVIEW_FAIL_MARKER,
    }
```

Replace the `run_milestone_cli` fixture body's inner function:

```python
    def invoke(root: Path, milestone: str):
        argv = [
            "run",
            "--milestone",
            milestone,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            MILESTONE_PREFIX,
        ]
        for command in VERIFY_COMMANDS:
            argv += ["--verify", command]
        return runner.invoke(cli.app, argv)
```

with:

```python
    def invoke(root: Path, milestone: str, max_concurrent: int | None = None):
        argv = [
            "run",
            "--milestone",
            milestone,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            MILESTONE_PREFIX,
        ]
        for command in VERIFY_COMMANDS:
            argv += ["--verify", command]
        # Only when asked: existing callers keep their exact argv.
        if max_concurrent is not None:
            argv += ["--max-concurrent", str(max_concurrent)]
        return runner.invoke(cli.app, argv)
```

and change that fixture's return annotation line from:

```python
def run_milestone_cli(fake_claude_bin) -> Callable[[Path, str], Any]:
```

to:

```python
def run_milestone_cli(fake_claude_bin) -> Callable[..., Any]:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_parallel_milestone.py tests/e2e/test_milestone_run.py -v`
Expected: PASS, both modules; the overlap test finishes in seconds, not 20+.

- [ ] **Step 5: Prove completion really requires the overlap (mutation check, then revert)**

Temporarily change `max_concurrent=2` to `max_concurrent=1` inside `_run_two_lanes` in `tests/e2e/test_parallel_milestone.py`, then run:

Run: `uv run pytest tests/e2e/test_parallel_milestone.py::test_two_lanes_overlap_in_implement_and_the_milestone_finishes -v`
Expected: FAIL after about 20 s, on `assert result.exit_code == 0` (exit code `1`, `EXIT_ESCALATED`): a single lane can never meet a count of 2, so a1's implement times out. Then revert `_run_two_lanes` to `max_concurrent=2` and re-run the same command: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_parallel_milestone.py
git commit -m "test(e2e): prove two story lanes overlap under the fake claude"
```

---

### Task 3: One lane stays sequential, and the journal stays consistent

**Files:**
- Modify: `tests/e2e/test_parallel_milestone.py` (append after the two-lane test)

**Interfaces:**
- Consumes: `parallel_board`, `rendezvous`, `run_milestone_cli(..., max_concurrent=...)` (Task 2); module helpers `_envelope`, `_load_run`, `_run_two_lanes` (Task 2); `store.Journal(run_id).read() -> list[store.JournalLine]` (fields `seq`, `story`); `store.Store.open(root, run_id)`, `Store.load_run(run_id)`, `Store.rebuild_from_journal(run_id)`, `Store.close()`; `models.PhaseRun.started_at` / `ended_at`.
- Produces: `_story_span(run: models.Run, story_id: str) -> tuple[datetime, datetime]`.

These two tests characterise engine behaviour that already exists on this branch, so they are expected to pass on first run. Their power is shown by a mutation check in Step 3.

- [ ] **Step 1: Write the tests**

Append to `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-prove-parallel-stories-1976123f/tests/e2e/test_parallel_milestone.py`:

```python
def _story_span(run: models.Run, story_id: str):
    """The earliest phase start and the latest phase end across one story's subtasks."""
    (story,) = [story for story in run.stories if story.card_id == story_id]
    phases = [phase for subtask in story.subtasks for phase in subtask.phases]
    starts = [phase.started_at for phase in phases if phase.started_at is not None]
    ends = [phase.ended_at for phase in phases if phase.ended_at is not None]
    assert starts and ends, story_id  # non-vacuity: the story really ran phases
    return min(starts), max(ends)


def test_one_lane_runs_the_level_s_stories_one_after_the_other(
    parallel_board, rendezvous, run_milestone_cli
):
    """Spec test 2: `--max-concurrent 1` behaves as the sequential runner did.
    Count 1 keeps the rendezvous satisfiable by a single lane."""
    root = parallel_board["root"]
    stories = parallel_board["stories"]
    rendezvous.arm(1)

    result = run_milestone_cli(root, parallel_board["milestone"], max_concurrent=1)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert len(rendezvous.markers()) == 5
    run = _load_run(root, data["run_id"])
    a_start, a_end = _story_span(run, stories["A"])
    b_start, b_end = _story_span(run, stories["B"])
    # Census order within the level: A's lane runs to the end before B's starts.
    assert a_end <= b_start, (a_start, a_end, b_start, b_end)


def test_the_journal_of_a_two_lane_run_is_contiguous_and_rebuilds_the_projection(
    parallel_board, rendezvous, run_milestone_cli
):
    """Spec test 3, on its own two-lane run (same shape as spec test 1)."""
    root = parallel_board["root"]
    stories = parallel_board["stories"]

    result = _run_two_lanes(parallel_board, rendezvous, run_milestone_cli)

    assert result.exit_code == 0, (result.output, result.exception)
    run_id = _envelope(result)["run_id"]
    lines = store.Journal(run_id).read()
    seqs = [line.seq for line in lines]
    assert seqs == list(range(1, len(seqs) + 1))
    # Non-vacuity: the two lanes' phase lines really interleave, so contiguity
    # was tested under concurrent appends and not a sequential run. Phase lines
    # only: `record_plan` journals every story `pending` up front, so story
    # lines would interleave even in a one-lane run.
    phase_lines = [line for line in lines if line.event == "phase_upsert"]
    first_b = min(line.seq for line in phase_lines if line.story == stories["B"])
    last_a = max(line.seq for line in phase_lines if line.story == stories["A"])
    assert first_b < last_a, (first_b, last_a)

    st = store.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        projection = st.load_run(run_id)
        rebuilt = st.rebuild_from_journal(run_id)
        after = st.load_run(run_id)
    finally:
        st.close()
    assert projection is not None
    assert rebuilt == projection
    assert after == projection
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/e2e/test_parallel_milestone.py -v -k "one_lane or journal"`
Expected: PASS (characterisation of the merged m4 engine).

- [ ] **Step 3: Prove the one-lane test can fail (mutation check, then revert)**

Temporarily change the one-lane test's `rendezvous.arm(1)` to `rendezvous.arm(2)` and its `max_concurrent=1` to `max_concurrent=2`, then run:

Run: `uv run pytest tests/e2e/test_parallel_milestone.py::test_one_lane_runs_the_level_s_stories_one_after_the_other -v`
Expected: FAIL on `assert a_end <= b_start` (the rendezvous forces a1 and b1 to overlap, so A's span ends after B's starts). Revert both edits and re-run the same command: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_parallel_milestone.py
git commit -m "test(e2e): pin one-lane sequencing and journal integrity under parallel lanes"
```

---

### Task 4: Escalation in one lane stops the other, and a relaunch finishes the milestone

**Files:**
- Modify: `tests/e2e/test_parallel_milestone.py` (imports at top; append tests at end)

**Interfaces:**
- Consumes: `parallel_board["review_fail_marker"]`, `rendezvous.arm/disarm`, `run_milestone_cli(..., max_concurrent=2)`, `read_fake_log(run_id) -> list[dict]` (conftest); module helpers from Task 2; `cli.EXIT_ESCALATED`, `cli.WORKFLOW_NAME`, `cli.worktree_for(root, branch) -> Path`; `agent_manager.workflow.loader.load_builtin(name).phase_names -> tuple[str, ...]`; report keys `escalated`, `story`, `subtask`, `failed_phase`, `also_escalated`, `stopped` (entries `{story, subtask, before_phase}`) from `orchestrate.escalated_payload`.
- Produces: `_launch_with_a1_review_failing(parallel_board, rendezvous, run_milestone_cli) -> click.testing.Result`.

- [ ] **Step 1: Write the failing tests**

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-prove-parallel-stories-1976123f/tests/e2e/test_parallel_milestone.py`, replace the imports:

```python
import json
import subprocess
from pathlib import Path

from agent_manager import board, cli, models, store
```

with:

```python
import json
import os
import subprocess
from pathlib import Path

from agent_manager import board, cli, models, store
from agent_manager.workflow.loader import load_builtin
```

Append at the end of the file:

```python
def _launch_with_a1_review_failing(parallel_board, rendezvous, run_milestone_cli):
    """Two lanes, count 2, and a1's review failing through the production gate.

    The rendezvous makes a1 and b1 leave implement together. Lane A then runs
    one fake process (a1's review) before it escalates; lane B would need b1's
    review, verify and mark_done plus every phase of b2 to finish, so it is
    parked by the stop at some phase boundary. Which boundary is not fixed, so
    the assertions read it out of the report (see the plan's determinism note).
    """
    a1 = parallel_board["subtasks"]["A"][0]
    parallel_board["review_fail_marker"].write_text(
        f"{parallel_board['branches'][a1]}\n", encoding="utf-8"
    )
    rendezvous.arm(2)
    return run_milestone_cli(
        parallel_board["root"], parallel_board["milestone"], max_concurrent=2
    )


def test_an_escalation_in_one_lane_stops_the_other_and_the_next_level_never_starts(
    parallel_board, rendezvous, run_milestone_cli, read_fake_log
):
    """Spec test 4 (P4, P5)."""
    root = parallel_board["root"]
    stories = parallel_board["stories"]
    branches = parallel_board["branches"]
    a1, a2 = parallel_board["subtasks"]["A"]
    b1, b2 = parallel_board["subtasks"]["B"]
    (c1,) = parallel_board["subtasks"]["C"]
    c_worktree = cli.worktree_for(root, branches[c1])
    c_status_before = board.show(c1, repo_dir=root).status
    main_before = _git(root, "rev-parse", "main").strip()

    result = _launch_with_a1_review_failing(parallel_board, rendezvous, run_milestone_cli)

    assert result.exit_code == cli.EXIT_ESCALATED, (result.output, result.exception)
    data = _envelope(result)
    assert data["escalated"] is True, data
    assert data["story"] == stories["A"]
    assert data["subtask"] == a1
    assert data["failed_phase"] == "review"
    assert "review-fail marker" in data["detail"]
    assert "also_escalated" not in data, data
    assert "stopped" in data, (
        "lane B finished before lane A escalated, so nothing was stopped; the "
        "ordering margin this test relies on was lost",
        data,
    )
    (parked,) = data["stopped"]
    assert parked["story"] == stories["B"]
    assert parked["subtask"] in (b1, b2)
    phase_names = load_builtin(cli.WORKFLOW_NAME).phase_names
    before = parked["before_phase"]
    assert before in phase_names, parked
    later = set(phase_names[phase_names.index(before):])

    run = _load_run(root, data["run_id"])
    rows = _subtask_rows(run)
    story_status = {story.card_id: story.status for story in run.stories}
    assert story_status == {
        stories["A"]: "escalated",
        stories["B"]: "stopped",
        stories["C"]: "pending",
    }
    assert rows[a1].status == "escalated"
    assert rows[a2].status == "pending" and rows[a2].phases == []
    stopped_row = rows[parked["subtask"]]
    assert stopped_row.status == "stopped"
    # Nothing ran at or after the phase it was parked before: no phase row, so
    # no attempt, and no fake process in its worktree for any such phase.
    assert not ({phase.name for phase in stopped_row.phases} & later), (
        before,
        [phase.name for phase in stopped_row.phases],
    )
    entries = read_fake_log(data["run_id"])
    assert entries  # non-vacuity: agents did run in this run
    stopped_worktree = cli.worktree_for(root, branches[parked["subtask"]]).resolve()
    assert not {
        entry["phase"]
        for entry in entries
        if Path(entry["cwd"]).resolve() == stopped_worktree
    } & later
    if parked["subtask"] == b2:
        assert rows[b1].status == "done"

    # Story C never started: no worktree, no branch, no phase, no agent.
    assert not c_worktree.exists()
    assert branches[c1] not in _git(root, "branch", "--format=%(refname:short)").split()
    assert rows[c1].status == "pending"
    assert rows[c1].phases == []
    assert c_worktree.resolve() not in _cwds(entries)
    assert board.show(c1, repo_dir=root).status == c_status_before

    assert _git(root, "rev-parse", "main").strip() == main_before


def test_a_relaunch_after_the_escalation_finishes_and_skips_done_subtasks(
    parallel_board, rendezvous, run_milestone_cli, read_fake_log
):
    """Spec test 5: continues spec test 4's scenario on a board of its own."""
    root = parallel_board["root"]
    milestone = parallel_board["milestone"]
    stories = parallel_board["stories"]
    subtasks = parallel_board["subtasks"]
    branches = parallel_board["branches"]
    a1, a2 = subtasks["A"]
    b1, b2 = subtasks["B"]
    (c1,) = subtasks["C"]
    main_before = _git(root, "rev-parse", "main").strip()

    first = _launch_with_a1_review_failing(parallel_board, rendezvous, run_milestone_cli)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    every_subtask = [card for chain in subtasks.values() for card in chain]
    done_first = [
        card for card in every_subtask
        if board.show(card, repo_dir=root).status == "done"
    ]
    assert a1 not in done_first and c1 not in done_first, done_first

    # Fix the fake and drop the rendezvous, then relaunch the same command.
    parallel_board["review_fail_marker"].unlink()
    rendezvous.disarm()
    second = run_milestone_cli(root, milestone, max_concurrent=2)

    assert second.exit_code == 0, (second.output, second.exception)
    finished = _envelope(second)
    assert finished["done"] is True, finished
    assert finished["run_id"] != stopped["run_id"]
    assert [level["stories"] for level in finished["levels"]] == [
        [stories["A"], stories["B"]],
        [stories["C"]],
    ]
    expected = [
        card
        for key in ("A", "B", "C")
        for card in subtasks[key]
        if card not in done_first
    ]
    assert finished["completed"] == expected
    second_entries = read_fake_log(finished["run_id"])
    assert second_entries
    done_worktrees = {cli.worktree_for(root, branches[card]).resolve() for card in done_first}
    assert not (_cwds(second_entries) & done_worktrees)
    assert cli.worktree_for(root, branches[c1]).resolve() in _cwds(second_entries)

    for card_id in _all_cards(parallel_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
    assert _is_ancestor(root, branches[a1], branches[a2])
    assert _is_ancestor(root, branches[b1], branches[b2])
    assert _is_ancestor(root, branches[a2], branches[c1])
    assert _git(root, "rev-parse", "main").strip() == main_before


def test_no_rendezvous_is_left_armed_for_later_tests():
    """Review focus: the tests above arm the rendezvous through the
    function-scoped `monkeypatch`; it must be gone once they end, or every later
    fake in the session would wait on a stale dir. Kept last in the module."""
    assert "FAKE_CLAUDE_RENDEZVOUS_DIR" not in os.environ
    assert "FAKE_CLAUDE_RENDEZVOUS_COUNT" not in os.environ
```

- [ ] **Step 2: Run the tests with the failure trigger removed, to see them fail**

These tests exercise engine behaviour that already exists, so the RED is shown by removing their trigger. Temporarily comment out the `parallel_board["review_fail_marker"].write_text(...)` call in `_launch_with_a1_review_failing`, then run:

Run: `uv run pytest tests/e2e/test_parallel_milestone.py -v -k "escalation or relaunch"`
Expected: FAIL. `test_an_escalation_in_one_lane_...` fails on `assert result.exit_code == cli.EXIT_ESCALATED` (exit 0: the milestone finished), and `test_a_relaunch_after_the_escalation_...` fails on `assert first.exit_code == cli.EXIT_ESCALATED`. Restore the marker write.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_parallel_milestone.py -v`
Expected: PASS, all seven tests (guard, two-lane, one-lane, journal, escalation, relaunch, env-leak guard). No test takes 20 s or more.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_parallel_milestone.py
git commit -m "test(e2e): prove an escalation parks sibling lanes and a relaunch finishes"
```

---

### Task 5: Whole-suite verification

**Files:**
- None changed (verification only).

**Interfaces:**
- Consumes: everything above.
- Produces: a green default suite.

- [ ] **Step 1: Run the full default suite**

Run: `uv run pytest`
Expected: PASS, with no failures and no errors. The `e2e`-marked real-harness tests are deselected by `addopts = --import-mode=importlib -m "not e2e"`; `tests/e2e/test_parallel_milestone.py` and `tests/e2e/test_fake_claude.py` are collected and pass.

- [ ] **Step 2: Confirm scope**

Run: `git diff --stat m4/task-default-to-four-lanes-69bcf17e...HEAD -- src`
Expected: empty output (no file under `src/` changed).

- [ ] **Step 3: Commit (only if Step 1 required a fix)**

```bash
git add tests/e2e
git commit -m "test(e2e): keep the default suite green with parallel lanes"
```
