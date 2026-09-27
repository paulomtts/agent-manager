<!-- task-pipeline: validated -->
# Prove a killed milestone resumes under the fake claude (card 949d51a0)

Parent story: 84802b0b "Milestone-wide resume". Sibling 54e4ec29 ("Continue a milestone run with `am resume`") owns the production code: `cli.resume_run` dispatching on `run.workflow`, and `orchestrate.run_milestone(..., resume_run_id=...)` with `resumable_milestone_run`, `find_run_milestone`, `open_cards`, `resume_point`, `resume_checkpoints`, `_refuse_changed_workflow`. That code is present in this worktree (`src/agent_manager/cli.py:1389`, `src/agent_manager/orchestrate.py:1211`). Source of truth: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §6, §7, §9; plan `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 4.2.

## Scope

This card adds tests only. Its deliverable is a new `tests/e2e/test_milestone_resume.py`. If needed, `tests/e2e/fake_claude.py` gets one new kill-switch env var, and `tests/e2e/conftest.py` gets small fixtures or helpers. It does not reimplement or restructure 54e4ec29's resume code. If the new test exposes a bug, the fix lands here, and the failing test must be committed first. Commit message: `test(e2e): a killed milestone resumes where it stopped`.

Out of scope, per spec §10: verification discovery, live pause/cancel/watch/retry, more than one `am` process per repo, grafo `max_workers`, and the multi-blocker handling of leave-me-alone. Unit coverage that already exists stays where it is and must not be duplicated here. `tests/test_orchestrate.py` already covers a `BaseException` propagating rather than escalating (around lines 1643 and 2144-2177). It also covers `run_milestone(resume_run_id=...)` with a `FakeDriver` (3613+).

## Observable behaviour under test

The test uses a milestone with one multi-blocker story. The `merged_base_board` fixture fits: A (a1) and B (b1) are independent, C (c1) is blocked by both, and D (d1 -> d2 -> d3) is a sibling lane. The card text says "three-story", but a multi-blocker story plus a second lane that is live while it runs needs the D sibling. Reuse this fixture rather than adding a new board. The run goes through `am run --milestone` (the `run_milestone_cli` fixture) with the real `ClaudeAdapter`, the real `launcher.run_direct` and the fake `claude` on PATH.

1. Kill point. The manager dies with a plain `BaseException` subclass. It must not be `KeyboardInterrupt`, which follows the precedent `_Killed` in `tests/e2e/test_milestone_run.py:245-294`. At that moment, C's merged base has already been built and one lane is inside `plan` while another is inside `implement`. The natural choice is c1 in `plan` and a D subtask in `implement`. The kill must be deterministic and must not use sleeps or timing. Allowed mechanisms:
   - A one-shot `run_direct` monkeypatch in the manager process, like `_kill_after`.
   - A new fake env var that makes the fake exit abruptly in a named phase (per-card/phase or per-branch). It follows the shape of the existing whitelist: `FAKE_CLAUDE_*` naming, strict parsing, a documented no-op when unset, an entry in the module docstring, and it is set through the test's `monkeypatch`.
   - Existing synchronisation (the rendezvous env vars) to hold the second lane in `implement` until the kill.

   The kill-switch must never appear in a brief or prompt. `am run` raises the exception out of `CliRunner`, and the run is left non-`done` with checkpoints written.
2. Resume. `am resume <run-id>` goes through `CliRunner` against the same repo and exits 0. It keeps the same run id and reuses the recorded prefix, base and `max_concurrent`.
3. Assertions after resume. The fake's per-run log (`read_fake_log(run_id)`), counted per (card, phase), shows the following:
   - Every phase that finished before the kill ran exactly once across both invocations. This covers a1 and b1 end to end, and the earlier phases of c1 and the D subtask.
   - The two interrupted phases ran exactly twice: once killed, once resumed.
   - Everything after them ran once.

   The merged base for C is not rebuilt: its branch tip is unchanged across the resume, and no second merge of `merged_from` happens. Assert this through git (the ref and merge commits) or the run's recorded attempts, not through timing. The run ends `done`, Integrate has run, and the report says `integrated`. The report carries `resumed: true`, and its `completed` lists only what finished during the resume invocation. `main` has not moved and nothing has been pushed.
4. The whole default suite stays green, including `tests/e2e`. `tests/e2e/test_parallel_milestone.py` and `tests/test_orchestrate.py` pass unchanged. So does the fake's own contract test, `tests/e2e/test_fake_claude.py`, which gets a case for the new env var if one is added.

## Error paths

- Resume refusals (a stale digest exits 3 and writes nothing, and resuming a `done` run exits 3) are named in spec §9. Cover them end to end here only if they are not already covered in e2e by 54e4ec29. If they are covered, do not duplicate them. If added, they belong in `tests/e2e/test_milestone_resume.py`.
- A new fake env var with a malformed value must fail loudly (`FakeClaudeError`), like `CRITIC_BLOCKS_ENV` and `RENDEZVOUS_COUNT_ENV`.

## Tests

Tier rule: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 and supervisor-tree spec §9. Anything that must run through the real CLI, the real adapter and launcher, the fake `claude` subprocess and real git worktrees belongs in `tests/e2e/`. In-process `FakeDriver` coverage stays in `tests/test_orchestrate.py` and `tests/test_cli.py`.

| Test | Tier |
|---|---|
| `test_a_killed_milestone_resumes_where_it_stopped`: kill with one lane in plan and one in implement after C's merged base is built, then `am resume`. It asserts the same run id, per-(card, phase) counts (finished phases once, interrupted phases twice), the merged base not merged again, `done` + `integrated`, `resumed: true`, and `main` unmoved. | e2e (`tests/e2e/test_milestone_resume.py`) |
| Only if a kill-switch env var is added: the fake exits abruptly in the named phase, is a no-op when unset, and rejects malformed values. | e2e (`tests/e2e/test_fake_claude.py`, the fake's contract tests) |
| Only if not already covered by 54e4ec29 in e2e: stale digest gives exit 3 with nothing written; resuming a `done` milestone run gives exit 3. | e2e (`tests/e2e/test_milestone_resume.py`) |
| Any regression test for a bug the e2e test exposes | the tier of the module the fix touches (unit test beside it under `tests/`), written failing first |

## Invariants

- Only `orchestrate.py` imports grafo, and every Node is built with `timeout=None`.
- Only the subtask is a pygents Agent.
- No test sleeps to prove ordering.
- A fake `claude` knows nothing beyond its brief and the explicit env-var whitelist.
- The milestone's base branch never moves and nothing is pushed.

---

# Killed-Milestone Resume E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove end to end, under the fake `claude`, that a milestone run killed with a plain `BaseException` while c1 is in `plan` and d3 is in `implement` (after C's merged base was built) is finished by `am resume <run-id>` under the same run id, re-dispatching only the two interrupted phases; and fix the bug that stops this from working today.

**Architecture:** One new e2e module, `tests/e2e/test_milestone_resume.py`, drives `am run --milestone` and `am resume` through `CliRunner` on `merged_base_board`. The kill is a one-shot `cli.run_direct` monkeypatch in the manager process (the `_kill_after` precedent), synchronised with two `threading.Event`s so that c1's `plan` launch and d3's `implement` launch are both returned-but-unrecorded when the manager dies; no fake env var is added, so `tests/e2e/fake_claude.py` and `tests/e2e/test_fake_claude.py` are untouched. Reading grafo 0.x's `TreeExecutor` (`.venv/.../grafo/executor.py:152` catches only `Exception`, and `run()` ends in `asyncio.gather(*workers, return_exceptions=True)`) shows that a plain `BaseException` in a lane is stored on the grafo worker task and then dropped, so today the killed lanes read as `pending` and the run goes on to Integrate and records `done`; asyncio only re-raises `KeyboardInterrupt`/`SystemExit` out of the loop, which is why the existing unit test at `tests/test_orchestrate.py:2143` does not see it. The fix is a small watcher in `orchestrate.supervise` that re-raises such an exception as soon as any lane dies of it, pinned first by a unit test in `tests/test_orchestrate.py`.

**Tech Stack:** Python 3.12, pytest, Typer `CliRunner`, grafo `TreeExecutor`, brd, git, the fake `claude` script.

**Spec:** `docs/superpowers/specs/task-prove-a-killed-949d51a0-design.md` (prepended above, verbatim). Upstream: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §6, §7, §9.

**Note on inputs:** both upstream summaries handed to this planning stage (the spec author's summary and the exploration findings) were cut off by the harness at their character caps, mid-sentence. That truncation is itself evidence the upstream stages over-ran their briefs. This plan does not guess at the missing text: it was written from the spec file on disk and from the code in this worktree.

## Global Constraints

- Only `src/agent_manager/orchestrate.py` imports `grafo`; every `grafo.Node` is built with `timeout=None` (guarded by `tests/test_orchestrate.py::test_only_orchestrate_imports_grafo`).
- Only the subtask is a pygents Agent; supervisor and lanes stay plain async functions.
- No test sleeps to prove ordering: held launches wait on `threading.Event`s with a bounded `WAIT`, as `tests/e2e/test_milestone_run.py:364-422` does.
- A fake `claude` knows nothing beyond its brief and its env-var whitelist: this plan adds no env var and does not modify `tests/e2e/fake_claude.py`.
- The milestone's base branch (`main`) never moves and nothing is pushed (the fixture repo has no remote).
- 54e4ec29's resume code (`cli.resume_run`, `orchestrate.run_milestone(resume_run_id=...)`, `resumable_milestone_run`, `find_run_milestone`, `open_cards`, `resume_point`, `resume_checkpoints`, `_refuse_changed_workflow`, `reopen_rows`) is not modified.
- The whole default suite (`uv run pytest`) stays green, including `tests/e2e`; `tests/test_orchestrate.py` and `tests/e2e/test_parallel_milestone.py` pass with their existing tests unchanged.
- The final commit message is exactly `test(e2e): a killed milestone resumes where it stopped`.

## Review Focus

1. A second `am resume` of the run the first resume finished: a person expects exit 3 (`NotResumableError`) and no agent launched. This also covers the spec's "resuming a `done` run" refusal. Pinned in Task 1's test, at its end.
2. The killed first invocation must not be recorded `done` or reach Integrate: a person expects status `started` and no `m3-integrate` branch after the kill. Pinned in Task 1's test, right after the kill. This is the assertion the grafo bug breaks.
3. d3's `implement` is re-dispatched on a branch where its first launch already committed: a person expects no second implement commit. Pinned in Task 1's test: exactly one `feat: implement this card` commit on `d2..d3`.
4. Worktrees of the two resumed subtasks are left clean: a person expects `git status --porcelain` to be empty in c1's and d3's worktrees after the resume. Pinned in Task 1's test.
5. The kill switch leaking into later tests: a person expects `cli.run_direct` to be the real `launcher.run_direct` once the test ends. Pinned by `test_no_kill_switch_is_left_armed_for_later_tests`, the last test in the module (Task 3).

## File Structure

- Create `tests/e2e/test_milestone_resume.py`: the e2e resume scenario, the stale-digest refusal, the unmarked guard and the kill-switch guard. Helpers are module-private, following `tests/e2e/test_milestone_run.py` and `tests/e2e/test_parallel_milestone.py`, which each define their own `_git`, `_envelope`, `_load_run` and constants instead of importing from conftest.
- Modify `src/agent_manager/orchestrate.py`: add `run_until_killed` and use it in `supervise`, so that a lane's non-`Exception` `BaseException` leaves `asyncio.run`.
- Modify `tests/test_orchestrate.py`: one regression test next to `test_a_keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates`.
- Untouched: `tests/e2e/fake_claude.py`, `tests/e2e/test_fake_claude.py`, `tests/e2e/conftest.py` (all fixtures used already exist there: `merged_base_board`, `two_story_board`, `run_milestone_cli`, `read_fake_log`, `checkpoint_rows`, `fake_claude_bin`).

---

### Task 1: The killed-milestone e2e test (RED)

**Files:**
- Create: `tests/e2e/test_milestone_resume.py`

**Interfaces:**
- Consumes (conftest fixtures, `tests/e2e/conftest.py`): `merged_base_board` -> dict with `root: Path`, `milestone: str`, `stories: {"A","B","C","D"} -> id`, `subtasks: {"A": [a1], "B": [b1], "C": [c1], "D": [d1, d2, d3]}`, `branches: card id -> branch`, `base_branch: str`, `merged_from: list[str]`; `run_milestone_cli(root, milestone, max_concurrent=None, verify=None) -> click.testing.Result`; `read_fake_log(run_id) -> list[dict]` (keys `phase`, `cwd`, `result_path`); `checkpoint_rows(root, run_id) -> int`.
- Consumes (production): `cli.app`, `cli.EXIT_ERROR == 3`, `cli.worktree_for(root, branch)`, `cli.resolve_repo_dir(root)`, `cli.run_direct` (read at call time by `cli.default_runner_factory`), `launcher.run_direct(argv, *, cwd, timeout, stdout_path, on_spawn=None)`, `store.open_db(root)`, `store.load_run(conn, run_id) -> models.Run | None`, `store.latest_run_id(conn) -> str | None`, `board.show(card_id, repo_dir=root).status`.
- Produces (used by Task 3, same module): `_git`, `_envelope`, `_error`, `_load_run`, `_local_branches`, `_resume(root, run_id)`, `REVIEW_FAIL_MARKER`.

- [ ] **Step 1: Write the failing test module**

Create `tests/e2e/test_milestone_resume.py` with exactly this content:

```python
"""Default-suite e2e tier: a killed milestone resumes where it stopped (card 949d51a0).

Supervisor-tree spec §6, §7 and §9 ("Resume, end to end under the fake
claude"). `am run --milestone` and `am resume` run through `CliRunner` on the
real `cli.app` with no `runner_factory` and no `driver`, so every launch goes
through `cli.drive_subtask_async`, `cli.default_runner_factory`, the real
`ClaudeAdapter` and `launcher.run_direct` to the fake `claude` first on `PATH`.

On `merged_base_board`, A (a1) and B (b1) are independent, C (c1) is blocked by
both and runs on their merged base, and D (d1 -> d2 -> d3) is a sibling lane.
The manager is killed with a plain `BaseException` while c1 is inside `plan`
and d3 inside `implement`, after C's merged base was built. The kill is test
scaffolding in the manager process (`cli.run_direct`, read at call time); the
fake is never told anything. Unmarked on purpose: it must run on every
`uv run pytest`.
"""

import json
import subprocess
import threading
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, models, store
from agent_manager.harness import launcher

PREFIX = "m3"
"""Must equal the conftest's `MILESTONE_PREFIX`: the board fixtures derive their branches with it."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)
"""Must equal the conftest's `AGENT_PHASES`: `TASK`'s seven agent phases, in order."""

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the `m3` prefix."""

IMPLEMENT_SUBJECT = "feat: implement this card"
"""The subject line of the commit the fake coder makes (`fake_claude.build_result`)."""

REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""Must equal the conftest's `FAKE_REVIEW_FAIL_MARKER` (and `fake_claude.REVIEW_FAIL_MARKER`)."""

MAX_CONCURRENT = 3
"""A, B and D run at once; C waits for its blockers without holding a slot."""

WAIT = 120.0
"""Seconds a held launch waits for the other lane before giving up. Generous:
it only bounds a broken run, a healthy one never waits this long."""


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


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _merge_count(root: Path, branch: str) -> int:
    """How many merge commits `branch` holds that `main` does not."""
    return int(_git(root, "rev-list", "--merges", "--count", f"main..{branch}").strip())


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _error(result) -> dict:
    """The `error` of brd's failure envelope, `{"type": ..., "message": ...}`."""
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False, envelope
    return envelope["error"]


def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _latest_run_id(root: Path) -> str:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _run_ids(root: Path) -> list[str]:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return [row["id"] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _all_cards(shape) -> list[str]:
    return [
        *(card for chain in shape["subtasks"].values() for card in chain),
        *shape["stories"].values(),
        shape["milestone"],
    ]


def _resume(root: Path, run_id: str):
    """`am resume <run-id>`: no prefix, base or bound, only what the record lacks."""
    return CliRunner().invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(root), "--verify", VERIFY]
    )


def _attempt_of(stdout_path: Path) -> tuple[str, str]:
    """(card id, phase) of the attempt a launch belongs to.

    `paths.attempt_dir` is `<run dir>/<card>/<phase>.<n>` and the dispatcher
    hands the launcher `<attempt dir>/stdout.log`, so the launch names its own
    attempt; the fake is never asked.
    """
    attempt = Path(stdout_path).parent
    return attempt.parent.name, attempt.name.rsplit(".", 1)[0]


def _card_phase_counts(entries: Iterable[Mapping]) -> Counter:
    """How many times the fake ran each (card id, phase).

    The fake logs its result path, `<run dir>/<card>/<phase>.<n>/result.json`
    (`paths.attempt_dir`), so the card is read off the path, never off the fake.
    """
    return Counter(
        (Path(entry["result_path"]).parents[1].name, entry["phase"]) for entry in entries
    )


def _counts(
    full: Iterable[str],
    partial: Mapping[str, str] | None = None,
    twice: Iterable[tuple[str, str]] = (),
) -> Counter:
    """Every agent phase once per `full` card; for each `partial` card, its
    phases up to and including the named one once; then one more launch of
    each (card, phase) in `twice`."""
    counts: Counter = Counter()
    for card in full:
        counts.update((card, phase) for phase in AGENT_PHASES)
    for card, last in (partial or {}).items():
        counts.update(
            (card, phase) for phase in AGENT_PHASES[: AGENT_PHASES.index(last) + 1]
        )
    counts.update(twice)
    return counts


class _Killed(BaseException):
    """The manager process dying mid-phase. A plain `BaseException`, so neither
    the engine nor `CliRunner` swallows it, and not `KeyboardInterrupt`, which
    asyncio re-raises out of the event loop before the engine unwinds."""


def _kill_with_c1_in_plan_and_d3_in_implement(monkeypatch, c1: str, d3: str) -> None:
    """Kill the manager once, while c1 is inside `plan` and d3 inside `implement`.

    Test scaffolding in the manager process: `cli.default_runner_factory`
    reads `cli.run_direct` at call time, so the real launcher still spawns the
    real fake, which writes its result and logs the phase as always. Both held
    launches raise after the real launch returned and before the dispatcher
    records the outcome, which leaves each attempt and phase `started`: the
    crash signature a real kill leaves.

    d3's `implement` launch, once returned, announces itself and waits for
    the kill. c1's `plan` launch, once returned, waits until d3 is held, then
    fires the kill and raises. So at the kill both lanes are inside their
    phase, whichever reached it first, and c1 can only be in `plan` after its
    lane built C's merged base. The waits are `threading.Event`s, since each
    launch runs in a `to_thread` worker; no sleep. One-shot: once the kill has
    fired, every later launch, the resume's included, passes straight through.
    """
    real = launcher.run_direct
    armed = {"on": True}
    d3_in_implement = threading.Event()
    killed = threading.Event()

    def killing(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        outcome = real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )
        if not armed["on"]:
            return outcome
        attempt = _attempt_of(stdout_path)
        if attempt == (d3, "implement"):
            d3_in_implement.set()
            if not killed.wait(WAIT):
                raise AssertionError("c1 never reached plan while d3 was in implement")
            raise _Killed(f"killed while {d3} was in implement")
        if attempt == (c1, "plan"):
            if not d3_in_implement.wait(WAIT):
                raise AssertionError("d3 never reached implement while c1 was in plan")
            armed["on"] = False
            killed.set()
            raise _Killed(f"killed while {c1} was in plan")
        return outcome

    monkeypatch.setattr(cli, "run_direct", killing)


def test_a_killed_milestone_resumes_where_it_stopped(
    merged_base_board, run_milestone_cli, read_fake_log, checkpoint_rows, monkeypatch
):
    """Spec test 1: killed with c1 in plan and d3 in implement, after C's
    merged base was built; `am resume` finishes it under the same run id,
    re-dispatching only those two phases, and never merges the base again."""
    root = merged_base_board["root"]
    milestone = merged_base_board["milestone"]
    stories = merged_base_board["stories"]
    branches = merged_base_board["branches"]
    base = merged_base_board["base_branch"]
    (a1,) = merged_base_board["subtasks"]["A"]
    (b1,) = merged_base_board["subtasks"]["B"]
    (c1,) = merged_base_board["subtasks"]["C"]
    d1, d2, d3 = merged_base_board["subtasks"]["D"]
    main_before = _git(root, "rev-parse", "main").strip()
    _kill_with_c1_in_plan_and_d3_in_implement(monkeypatch, c1, d3)

    # First invocation: the manager dies.
    with pytest.raises(_Killed):
        run_milestone_cli(root, milestone, max_concurrent=MAX_CONCURRENT)

    run_id = _latest_run_id(root)
    # Review Focus 2: a killed run is neither recorded done nor integrated.
    assert _load_run(root, run_id).status == "started"
    assert INTEGRATION_BRANCH not in _local_branches(root)
    assert checkpoint_rows(root, run_id) > 0
    # C's merged base was built before c1 ran: one --no-ff merge of the other blocker.
    assert base in _local_branches(root)
    base_tip = _git(root, "rev-parse", base).strip()
    assert _merge_count(root, base) == 1
    assert _card_phase_counts(read_fake_log(run_id)) == _counts(
        full=(a1, b1, d1, d2), partial={d3: "implement", c1: "plan"}
    )

    # Second invocation: am resume <run-id>.
    result = _resume(root, run_id)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert data["run_id"] == run_id
    assert data["resumed"] is True
    assert sorted(data["completed"]) == sorted([c1, d3])
    assert data["bases"] == [
        {"story": stories["C"], "branch": base, "blockers": merged_base_board["merged_from"]}
    ]
    assert set(data["integrated"]["merged"]) == set(stories.values())
    assert data["integrated"]["resolved"] == []
    assert _run_ids(root) == [run_id]
    run = _load_run(root, run_id)
    assert run.status == "done"
    assert (run.branch_prefix, run.base_branch) == (PREFIX, "main")
    assert run.config.max_concurrent_stories == MAX_CONCURRENT

    # Finished phases once in total; the two interrupted phases twice.
    assert _card_phase_counts(read_fake_log(run_id)) == _counts(
        full=(a1, b1, c1, d1, d2, d3), twice=[(c1, "plan"), (d3, "implement")]
    )

    # The merged base was not merged again: same tip, still one merge commit.
    assert _git(root, "rev-parse", base).strip() == base_tip
    assert _merge_count(root, base) == 1
    assert _is_ancestor(root, base, branches[c1])
    # Review Focus 3: d3's re-dispatched implement made no second commit.
    subjects = _git(root, "log", "--format=%s", f"{branches[d2]}..{branches[d3]}").splitlines()
    assert subjects.count(IMPLEMENT_SUBJECT) == 1, subjects
    # Review Focus 4: the resumed subtasks' worktrees are clean.
    for card in (c1, d3):
        assert _git(cli.worktree_for(root, branches[card]), "status", "--porcelain") == ""
    for card_id in _all_cards(merged_base_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
    assert _git(root, "rev-parse", "main").strip() == main_before
    assert _git(root, "remote").strip() == ""  # nowhere to push to, so nothing was pushed

    # Spec §9 and Review Focus 1: resuming the now-done run is refused, exit 3,
    # and launches nothing.
    launches = len(read_fake_log(run_id))
    again = _resume(root, run_id)

    assert again.exit_code == cli.EXIT_ERROR, (again.output, again.exception)
    refusal = _error(again)
    assert refusal["type"] == "NotResumableError"
    assert "finished" in refusal["message"]
    assert len(read_fake_log(run_id)) == launches
    assert _load_run(root, run_id).status == "done"
```

- [ ] **Step 2: Run the test to verify it fails for the grafo reason**

Run: `uv run pytest tests/e2e/test_milestone_resume.py::test_a_killed_milestone_resumes_where_it_stopped -v`

Expected: FAIL at the first `pytest.raises(_Killed)` with `Failed: DID NOT RAISE <class '...test_milestone_resume._Killed'>`. Why: `_Killed` is raised inside a grafo worker task; `grafo/executor.py:152` catches only `Exception`, asyncio stores any other `BaseException` on the task (it only re-raises `KeyboardInterrupt`/`SystemExit`), and `TreeExecutor.run` drops it in `gather(..., return_exceptions=True)`. So C and D read as `pending`, Integrate runs and `am run` returns normally. This is the bug Task 2 fixes. If it fails in any other way, stop and use superpowers:systematic-debugging before going on; if it unexpectedly raises `_Killed` and then passes, the grafo analysis above was wrong: skip Task 2 and say so in the review.

Do not commit yet: the e2e module is committed in Task 4, after the regression test and the fix.

---

### Task 2: A plain `BaseException` in a lane leaves the run (regression test first, then the fix)

**Files:**
- Modify: `tests/test_orchestrate.py` (insert after `test_a_keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates`, which ends at line 2193)
- Modify: `src/agent_manager/orchestrate.py:44-46` (import), `:1097-1098` (new helper before `supervise`), `:1136-1192` (`supervise` body)

**Interfaces:**
- Consumes (test helpers already in `tests/test_orchestrate.py`): `_milestone(project, {"A": 1, "B": 1}) -> {"milestone", "stories", "subtasks"}`, `GatedDriver(outcomes=..., gates=...)`, `_meet(barrier) -> Gate`, `_within(awaitable, what)`, `_run(project, milestone, driver, **overrides)`, `_load(project, run_id)`, `_statuses(run)`, `STARTED_AT`, the autouse `integrate_recorder` fixture (`.calls: list[dict]`), `requires_git`, `requires_brd`.
- Produces: `orchestrate.run_until_killed(work: Awaitable[Any], killed: asyncio.Event, fatal: Sequence[BaseException]) -> None`: awaits `work`, unless `killed` is set first, in which case it cancels `work` and raises `fatal[0]`.

- [ ] **Step 1: Write the failing regression test**

In `tests/test_orchestrate.py`, insert right after the last line of `test_a_keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates` (the `}` closing its `_statuses` assertion at line 2193), before `test_warnings_and_completed_follow_census_order_not_finish_order`:

```python


class _LaneKilled(BaseException):
    """A process death inside a lane, as the e2e resume test injects it (card
    949d51a0). Not `KeyboardInterrupt`: asyncio re-raises that out of the loop
    by itself, but stores any other `BaseException` on the task, where grafo's
    `gather(..., return_exceptions=True)` would drop it."""


@requires_git
@requires_brd
def test_a_plain_base_exception_in_one_lane_cancels_the_other_and_propagates(
    project, integrate_recorder
):
    """§7 for a BaseException asyncio does not re-raise by itself: it still
    leaves the run, the other lane is cancelled where it stands, Integrate
    never runs and the rows stay as they were, for `am resume`."""
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
    cancelled: list[str] = []

    async def meet_then_wait_to_be_cancelled(stop: StopSignal | None) -> None:
        try:
            await _within(pair.wait(), "a1 and b1 in flight together")
            await _within(asyncio.Event().wait(), "the lane to be cancelled")
        except asyncio.CancelledError:
            cancelled.append(b1)
            raise

    driver = GatedDriver(
        outcomes={a1: _LaneKilled("the manager died while a1 ran")},
        gates={a1: _meet(pair), b1: meet_then_wait_to_be_cancelled},
    )

    with pytest.raises(_LaneKilled):
        _run(project, shape["milestone"], driver, max_concurrent=2)

    assert cancelled == [b1]
    assert integrate_recorder.calls == []
    run = _load(project, cli.mint_run_id(shape["milestone"], STARTED_AT))
    assert _statuses(run) == {
        "run": "started",
        story_a: "started",
        a1: "started",
        story_b: "started",
        b1: "started",
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_a_plain_base_exception_in_one_lane_cancels_the_other_and_propagates -v`

Expected: FAIL with `Failed: DID NOT RAISE <class '...test_orchestrate._LaneKilled'>` after about 10 seconds (`WAIT`): a1's `_LaneKilled` is swallowed by grafo, b1's second `_within` times out into an `AssertionError`, b1's lane escalates, and `run_milestone` returns an escalated payload instead of raising.

- [ ] **Step 3: Commit the failing regression test**

```bash
git add tests/test_orchestrate.py
git commit -m "test(orchestrate): a plain BaseException in a lane must leave the run

grafo's TreeExecutor catches only Exception and gathers its workers with
return_exceptions=True, so a BaseException that asyncio does not re-raise
itself is dropped and the killed lanes read as pending. Failing on purpose;
the fix follows."
```

- [ ] **Step 4: Add `Awaitable` to the imports**

In `src/agent_manager/orchestrate.py`, replace:

```python
from collections.abc import Callable, Mapping, Sequence
```

with:

```python
from collections.abc import Awaitable, Callable, Mapping, Sequence
```

- [ ] **Step 5: Add `run_until_killed` right before `supervise`**

In `src/agent_manager/orchestrate.py`, replace:

```python
    finished[story.id] = outcome("done", None)
    return planned.tip


async def supervise(
```

with:

```python
    finished[story.id] = outcome("done", None)
    return planned.tip


async def run_until_killed(
    work: Awaitable[Any], killed: asyncio.Event, fatal: Sequence[BaseException]
) -> None:
    """Await `work`, unless a lane dies of a `BaseException` first: then re-raise it.

    grafo's workers catch only `Exception`. asyncio re-raises only
    `KeyboardInterrupt` and `SystemExit` out of the loop by itself; any other
    `BaseException` is stored on the grafo worker task, and
    `TreeExecutor.run` drops it in `gather(..., return_exceptions=True)`. The
    killed lanes would then read as `pending` and the run would go on to
    Integrate. So `supervise` records such an exception in `fatal` and sets
    `killed`, and this re-raises the first one at once: it leaves
    `asyncio.run`, which cancels every other lane where it stands, and the
    latest checkpoints stand for `am resume` (supervisor-tree §7, card
    949d51a0).
    """
    running = asyncio.ensure_future(work)
    watcher = asyncio.ensure_future(killed.wait())
    try:
        await asyncio.wait({running, watcher}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        watcher.cancel()
    if fatal:
        running.cancel()
        raise fatal[0]
    await running


async def supervise(
```

- [ ] **Step 6: Record a lane's fatal `BaseException` in `supervise`**

In `src/agent_manager/orchestrate.py`, replace:

```python
        story_ok: dict[str, bool] = {}

        def node_coroutine(story: census.StoryPlan) -> Callable[..., Any]:
```

with:

```python
        story_ok: dict[str, bool] = {}
        # A lane's `BaseException` that is neither an `Exception` nor a
        # cancellation: grafo would drop it (`run_until_killed`).
        fatal: list[BaseException] = []
        killed = asyncio.Event()

        def node_coroutine(story: census.StoryPlan) -> Callable[..., Any]:
```

Then replace:

```python
                except BaseException:
                    story_ok[story.id] = False
                    raise
```

with:

```python
                except BaseException as error:
                    story_ok[story.id] = False
                    if not isinstance(error, (Exception, asyncio.CancelledError)):
                        fatal.append(error)
                        killed.set()
                    raise
```

Then replace:

```python
            executor = grafo.TreeExecutor(uuid=run_id, roots=roots)
            await executor.run()
            errors = list(executor.errors)
```

with:

```python
            executor = grafo.TreeExecutor(uuid=run_id, roots=roots)
            await run_until_killed(executor.run(), killed, fatal)
            errors = list(executor.errors)
```

- [ ] **Step 7: Mention it in `supervise`'s docstring**

In `src/agent_manager/orchestrate.py`, replace:

```python
    The `grafo` logger is at CRITICAL for exactly this call: a lane's
```

with:

```python
    A lane that dies of a `BaseException` other than a cancellation ends the
    whole call at once, re-raised by `run_until_killed`: grafo alone would
    drop it (§7).

    The `grafo` logger is at CRITICAL for exactly this call: a lane's
```

- [ ] **Step 8: Run the regression test and its neighbours**

Run: `uv run pytest tests/test_orchestrate.py -k "base_exception or keyboard_interrupt or grafo" -v`

Expected: PASS, including `test_a_plain_base_exception_in_one_lane_cancels_the_other_and_propagates`, `test_a_keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates`, `test_a_keyboard_interrupt_from_the_driver_is_not_swallowed` and `test_only_orchestrate_imports_grafo`.

- [ ] **Step 9: Run the whole orchestrate module unchanged**

Run: `uv run pytest tests/test_orchestrate.py -q`

Expected: PASS, every test (the existing ones are not edited).

- [ ] **Step 10: Run the e2e test from Task 1**

Run: `uv run pytest tests/e2e/test_milestone_resume.py::test_a_killed_milestone_resumes_where_it_stopped -v`

Expected: PASS. If it now fails past the kill (in the resume or its assertions), that is a second bug: use superpowers:systematic-debugging, write a failing unit test beside the module the fix touches (for example `tests/test_orchestrate.py` or `tests/test_bases.py`), commit it failing, then fix minimally without restructuring 54e4ec29's resume code.

- [ ] **Step 11: Commit the fix**

```bash
git add src/agent_manager/orchestrate.py
git commit -m "fix(orchestrate): a lane's BaseException leaves the run instead of vanishing in grafo

supervise records a lane's non-Exception, non-cancellation BaseException and
run_until_killed re-raises it as soon as it happens, so asyncio.run cancels
the other lanes and the checkpoints stand for am resume (supervisor-tree §7)."
```

---

### Task 3: Stale-digest refusal end to end, and the module's guards

54e4ec29 covers both refusals only at the unit level (`tests/test_orchestrate.py:3422`, `:3504`, `:3773`); `tests/e2e` has no milestone `resume` test at all, so the spec's conditional e2e refusal tests are due. The "resuming a `done` run" refusal is already pinned at the end of Task 1's test (Review Focus 1), so it is not repeated here.

**Files:**
- Modify: `tests/e2e/test_milestone_resume.py` (append)

**Interfaces:**
- Consumes (Task 1, same module): `_git`, `_envelope`, `_error`, `_load_run`, `_local_branches`, `_resume`, `REVIEW_FAIL_MARKER`.
- Consumes (conftest): `two_story_board` -> dict with `root`, `milestone`, `stories: {"A","B"}`, `subtasks: {"A": [a1], "B": [b1]}`, `branches`; `run_milestone_cli`; `read_fake_log`.
- Consumes (production): `store.Store.open(root, run_id)`, `Store.save_checkpoint(card_id, *, workflow: str, digest: str, reason: str, agent: dict, saved_at: datetime) -> Checkpoint`, `Store.close()`, `paths.run_dir(run_id) -> Path`, `task_workflow.TASK.name`, `task_workflow.TASK.digest()`, `cli.EXIT_ESCALATED == 1`.

- [ ] **Step 1: Add the imports the new tests use**

In `tests/e2e/test_milestone_resume.py`, replace:

```python
from collections.abc import Iterable, Mapping
from pathlib import Path
```

with:

```python
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
```

and replace:

```python
from agent_manager import board, cli, models, store
from agent_manager.harness import launcher
```

with:

```python
from agent_manager import board, cli, models, paths, store
from agent_manager.harness import launcher
from agent_manager.workflow import task as task_workflow
```

- [ ] **Step 2: Append the stale-digest test and the two guards**

Append to the end of `tests/e2e/test_milestone_resume.py`:

```python


STALE_DIGEST = "saved-under-another-task"
"""The digest a planted checkpoint claims: no `TASK` ever has it."""


def _tree(directory: Path) -> dict[str, bytes]:
    """Every path under `directory`, with file contents: the journal and the fake log included."""
    return {
        str(path.relative_to(directory)): path.read_bytes() if path.is_file() else b"<dir>"
        for path in sorted(directory.rglob("*"))
    }


def _checkpoints(root: Path, run_id: str) -> list[tuple]:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT card_id, seq, reason, digest FROM checkpoints"
                " WHERE run_id = ? ORDER BY card_id, seq",
                (run_id,),
            )
        ]
    finally:
        conn.close()


def _plant_stale_checkpoint(root: Path, run_id: str, card_id: str) -> None:
    """A newest `turn` checkpoint of `card_id` in `run_id`, saved under `STALE_DIGEST`.

    Stands for a `TASK` that changed between the interrupt and the resume. It
    is the card's highest `seq`, so it is the row `orchestrate.resume_point`
    judges first.
    """
    opened = store.Store.open(cli.resolve_repo_dir(root), run_id)
    try:
        opened.save_checkpoint(
            card_id,
            workflow=task_workflow.TASK.name,
            digest=STALE_DIGEST,
            reason="turn",
            agent={"current_turn": None, "queue": []},
            saved_at=datetime.now(timezone.utc),
        )
    finally:
        opened.close()


def test_a_checkpoint_saved_under_another_task_refuses_the_resume_and_writes_nothing(
    two_story_board, run_milestone_cli, read_fake_log
):
    """Spec §9 error path: a stale digest refuses the whole milestone resume
    with exit 3, and nothing is written: no journal line, no row, no status,
    no branch, no launch."""
    root = two_story_board["root"]
    branches = two_story_board["branches"]
    (a1,) = two_story_board["subtasks"]["A"]
    marker = root / ".git" / REVIEW_FAIL_MARKER
    marker.write_text(f"{branches[a1]}\n", encoding="utf-8")

    first = run_milestone_cli(root, two_story_board["milestone"])

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["subtask"] == a1, stopped
    run_id = stopped["run_id"]
    marker.unlink()
    _plant_stale_checkpoint(root, run_id, a1)
    before = (
        _tree(paths.run_dir(run_id)),
        _load_run(root, run_id).status,
        _checkpoints(root, run_id),
        _local_branches(root),
        _git(root, "rev-parse", "main").strip(),
        len(read_fake_log(run_id)),
    )

    result = _resume(root, run_id)

    assert result.exit_code == cli.EXIT_ERROR, (result.output, result.exception)
    refusal = _error(result)
    assert refusal["type"] == "CheckpointMismatchError"
    assert refusal["message"].startswith("workflow changed since checkpoint")
    assert a1 in refusal["message"]
    assert STALE_DIGEST in refusal["message"]
    assert task_workflow.TASK.digest() in refusal["message"]
    assert (
        _tree(paths.run_dir(run_id)),
        _load_run(root, run_id).status,
        _checkpoints(root, run_id),
        _local_branches(root),
        _git(root, "rev-parse", "main").strip(),
        len(read_fake_log(run_id)),
    ) == before


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_milestone_run.py`'s guard: no `e2e` marker may reach this
    module, or milestone resume stops being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()


def test_no_kill_switch_is_left_armed_for_later_tests():
    """Review Focus 5: the kill switch is set through the function-scoped
    `monkeypatch`; it must be gone once its test ends, or every later launch
    in the session could be killed. Kept last in the module."""
    assert cli.run_direct is launcher.run_direct
```

- [ ] **Step 3: Run the new tests**

Run: `uv run pytest tests/e2e/test_milestone_resume.py -v`

Expected: PASS, all four tests. The stale-digest test pins behaviour 54e4ec29 already built (`orchestrate.resume_checkpoints` runs before any write), so it is expected to pass on its first run. To prove it can fail, temporarily change `STALE_DIGEST in refusal["message"]` to `"not-the-digest" in refusal["message"]`, run it, see `AssertionError`, then revert. If it fails for real (for example, the run directory changed), that is a 54e4ec29 bug: use superpowers:systematic-debugging, and put the failing regression test beside the module that needs the fix, committed before the fix.

---

### Task 4: Full verification and the card's commit

**Files:**
- Commit: `tests/e2e/test_milestone_resume.py`

- [ ] **Step 1: Run the whole default suite**

Run: `uv run pytest`

Expected: PASS with no failures, including `tests/e2e/test_parallel_milestone.py`, `tests/e2e/test_milestone_run.py`, `tests/e2e/test_fake_claude.py` (untouched) and `tests/test_orchestrate.py`.

- [ ] **Step 2: Confirm the fake and the conftest are untouched**

Run: `git status --porcelain tests/e2e/fake_claude.py tests/e2e/conftest.py tests/e2e/test_fake_claude.py`

Expected: no output.

- [ ] **Step 3: Commit the e2e module with the card's message**

```bash
git add tests/e2e/test_milestone_resume.py
git commit -m "test(e2e): a killed milestone resumes where it stopped"
```

---

## Self-Review

- **Spec coverage:** Kill point (spec 1): Task 1 `_kill_with_c1_in_plan_and_d3_in_implement`, a one-shot `run_direct` monkeypatch; C's base is built before c1 reaches `plan`, which the test checks with the base branch and its merge count right after the kill. Plain `BaseException`, not `KeyboardInterrupt`: `_Killed`. Kill raised out of `CliRunner` and run left non-`done` with checkpoints: `pytest.raises(_Killed)`, `status == "started"`, `checkpoint_rows > 0`. Resume (spec 2): `_resume`, exit 0, same run id, `_run_ids == [run_id]`, recorded prefix, base and `max_concurrent_stories`. Counts (spec 3): the `_counts` assertions before and after. Base not rebuilt: same tip, `_merge_count == 1`. `done`, `integrated`, `resumed: true`, `completed == {c1, d3}`, `main` unmoved, no remote. Suite green (spec 4): Task 2 Steps 8-9 and Task 4 Step 1. Error paths: both refusals are e2e-uncovered by 54e4ec29, so both are added (done run: end of Task 1; stale digest: Task 3). No fake env var is added, so the `FakeClaudeError` error path and the `test_fake_claude.py` row do not apply. Bug found: regression test in the tier of the module fixed (`tests/test_orchestrate.py`), committed failing before the fix (Task 2 Steps 1-3, then 11).
- **Placeholder scan:** every code step carries complete code; no TBD or "similar to".
- **Type consistency:** `_counts(full, partial, twice)`, `_card_phase_counts(entries)`, `_resume(root, run_id)`, `_error(result)` and `run_until_killed(work, killed, fatal)` are used with the signatures they are defined with. `REVIEW_FAIL_MARKER`, `STALE_DIGEST` and `MAX_CONCURRENT` are defined once each. The Task 3 imports (`datetime`, `timezone`, `paths`, `task_workflow`) are added in Task 3 Step 1, before first use.
- **Review Focus:** all five lines have assertions in Task 1's test, or the guard test in Task 3.
