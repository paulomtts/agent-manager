<!-- task-pipeline: validated -->
# Claim a milestone run's milestone, cards and integration branch (card 1a3fdd73)

Narrows Task 2.3 of `docs/superpowers/plans/2026-09-27-multi-process.md` (milestone 10, decisions X2–X11 of `docs/superpowers/specs/2026-09-27-multi-process-design.md`). Story f67dc4e4 "Leases that own runs, cards and branches". Branch prefix `m10`. Last of the story's three subtasks.

Note: the exploration summary handed to this stage was truncated at 8000 of 11782 characters, after the "What siblings own" section. That means the upstream stage wrote more than it was asked to. Nothing below relies on the missing text. The test-placement rule was re-read from the design spec, and the milestone-10 plan and spec could not be found in this worktree, so the plan's Task 2.3 wording is taken from the findings.

## Dependency

This work builds on siblings a7ed6c11 (store: `take_lease`, `claim_conflicts`, `release_claims`, `LeaseLostError`, ...) and ec7ae954 (`control.card_claim`, `control.branch_claim`, `control.Lease(store, claims=...)` with `.displaced`, and `cli.ClaimedError`, `cli.refuse_claimed`, `cli.run_lease`, `took_over` in `_resume_from_checkpoint`, `LeaseLostError` in `HANDLED`). Both are already in this worktree (cli.py:836-892, control.py:79-135), but neither is on master yet. Use them as they are. Do not change `store.py`, `control.py`, `cli.py`'s card/task-resume paths, `HANDLED` or the schema.

## Scope

All changes are in `src/agent_manager/orchestrate.py`:

- New pure function `milestone_claims(milestone_id: str, stories: Sequence[census.StoryPlan], branch_prefix: str) -> list[str]`. It returns `[control.card_claim(milestone_id)]`, then `control.card_claim(s.id)` for each `s` in `dag.remaining_subtasks(story)` for each story in census order, then `control.branch_claim(integration.integration_branch(branch_prefix))`. The list has no duplicates, keeping the first occurrence in order. This is spec X6's claim set for `am run --milestone M`: `card:M`, `card:<each remaining subtask>`, `branch:<prefix>-integrate`. A done subtask or a closed story adds no key.
- `run_milestone` (orchestrate.py:1299-), changing only how its lease and claims are taken:
  - Compute `keys = milestone_claims(milestone_card.id, plan.stories, branch_prefix)` once the plan is derived and after every existing refusal (`max_concurrent`, `resumable_milestone_run`, `find_milestone`/`find_run_milestone`, `flatten_milestone`, `plan_levels`).
  - Call `cli.refuse_claimed(root, keys, run_id=None if resumed is None else resumed.id)` before `refresh_git` on a fresh run and before `Store.open` on both paths. On a resume, passing `run_id` excludes the run's own rows.
  - Replace `with control.Lease(store) as lease:` (orchestrate.py:1423) with `with cli.run_lease(store, claims=keys) as lease:`, entered right after `Store.open` inside the existing `try` that closes the store. The same `lease` still goes to `control.controlled(...)`. The lease and claims are released before `store.close()`.
  - On a resume, `resume_checkpoints` only reads, so it may stay before the lease or move inside it. `refresh_git` (currently at orchestrate.py:1420, before the lease) and every `record_*`/`reopen_rows` must run inside the `run_lease` block.
  - On a resume, if `lease.displaced is not None`, every payload also gets `took_over = {"pid": lease.displaced.pid, "host": lease.displaced.host, "heartbeat_at": lease.displaced.heartbeat_at.isoformat()}`, copying cli.py:1655-1661. Add this in the existing `report` helper so every outcome shape gets it (done, escalated, paused, cancelled, Integrate-escalated).
  - Update the `run_milestone` docstring to describe the claims.

Out of scope: messages (`_claimed_error` is generic "{kind} {name} is being driven by run R ...", used as built), store/control/cli internals, dry-run and reader paths (these never call `milestone_claims`, `refuse_claimed` or `run_lease`), ProcessLock, and cross-process e2e proofs (spec §7 "End to end" bullets are not assigned to this card).

## Observable behaviour and error paths

- Ordering invariant (M7 + X5): every refusal, `refuse_claimed` included, comes before `refresh_git`, and on a fresh run `refresh_git` comes before `Store.open`. A refused `am run --milestone` leaves no run row, journal, fetch, prune or worktree. The one allowed leftover is an empty run directory, when the preflight passed but `take_lease` lost the race.
- Another live run claims the milestone, a remaining subtask, or `<prefix>-integrate`: `ClaimedError`, exit 3, in the unchanged envelope (`{"ok": false, "error": {"type", "message"}}`). The message names the first conflicting key's kind and name and the holder's run id and pid. X11 words the branch case as "belongs to run R of milestone M; use another --branch-prefix", but ec7ae954 built a generic message and changing it is out of scope. Tests assert the type and the key/run id, not that wording.
- A claim held by a dead holder (per `lease_is_live`) does not refuse. The run proceeds, and a resume reports `took_over`.
- Resuming a milestone run that is still live: `RunIsLiveError`, unchanged (C10).
- Losing the lease mid-run: `LeaseLostError` reaches the envelope at exit 3, unchanged. The run writes nothing more.
- Claims and lease are released on every exit: done, escalated, paused, cancelled, Integrate escalation, and an exception from `supervise`/Integrate.
- `am status` of a live milestone run lists its keys under `control.claims`. This comes from ec7ae954's `status_for`, and here it is only observed.

## Tests

Placement rule (design spec §14 "Testing", as applied by sibling ec7ae954): pure functions get plain unit tests. Anything touching git or board state is a Steps test against a real temp git repo and a temp `brd` board, with no network and no filesystem mocking. `tests/e2e/` holds only the single opt-in real-harness test, so none of these tests go there. Tests never sleep to prove ordering; use pipes, marker files, fake-driver rendezvous or exit codes. Child processes inherit the test `XDG_DATA_HOME`. Plant live or dead holders the way ec7ae954's `_plant_lease` does: `run_leases` and `run_claims` rows written over a second `open_db` connection.

`tests/test_orchestrate.py`:
- `test_milestone_claims_lists_milestone_remaining_subtasks_then_integration_branch`: census order. Done subtasks and closed stories are left out. The branch key is `branch:<prefix>-integrate`. Unit (pure).
- `test_milestone_claims_has_no_duplicates`: Unit (pure).
- `test_a_milestone_run_is_refused_while_a_live_run_claims_one_of_its_cards`: exit/`ClaimedError` naming the holder. Asserts no run row, no worktree, no fetch (a `refresh_git` spy was never called), and no run directory. Steps (default suite).
- `test_a_milestone_run_is_refused_while_a_live_run_claims_its_integration_branch`: same assertions, with key `branch:<prefix>-integrate`. Steps.
- `test_a_dead_claim_does_not_refuse_a_milestone_run`: Steps.
- `test_a_milestone_run_holds_its_claims_while_driving`: a fake driver reads `held_claims`/`am status` mid-run and sees exactly `milestone_claims(...)`. Steps.
- `test_a_milestone_run_releases_its_claims_on_every_exit`: done, escalated, and a driver that raises. Steps.
- `test_refresh_git_and_first_write_run_inside_the_lease_on_resume`: spies on `refresh_git` and `Store.record_run` see `store._token` set. Steps.
- `test_a_milestone_resume_excludes_its_own_claims_and_reports_took_over`: dead lease plus the run's own claims planted. The resume proceeds, and `took_over.pid` equals the dead pid. Steps.

## Verification

`uv run pytest` (full suite; there is no separate lint or typecheck, per CLAUDE.md).

---

# Milestone-run claims Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `am run --milestone` (fresh and resumed) refuse, before any side effect, a milestone whose card, remaining subtasks or `<prefix>-integrate` branch another live run claims, and hold exactly those claims under its lease for the whole run.

**Architecture:** One new pure function, `orchestrate.milestone_claims`, builds the X6 key list. `run_milestone` calls ec7ae954's read-only `cli.refuse_claimed` as its last refusal (before `refresh_git` and `Store.open`) and swaps `control.Lease(store)` for `cli.run_lease(store, claims=keys)`. On a resume, `refresh_git` moves inside the lease block and the `report` helper adds `took_over` when a dead holder was displaced.

**Tech Stack:** Python 3, pytest, SQLite (via `agent_manager.store`), real temp git repo + `brd` board fixtures, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-claim-a-milestone-run-s-1a3fdd73/docs/superpowers/specs/task-claim-a-milestone-run-s-1a3fdd73-design.md` (reproduced verbatim above).

**Upstream truncation notice:** both the spec summary (2733 of a 2000-character cap) and the exploration summary (11782 of an 8000-character cap) handed to this planning stage were truncated, which means those upstream stages over-ran their brief. This plan was written from the spec read from disk and from the code itself, not from the missing text.

**Branch / worktree:** `m10/task-claim-a-milestone-run-s-1a3fdd73` at `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-claim-a-milestone-run-s-1a3fdd73`, cut from `origin/m10/task-lease-with-claims-for-ec7ae954`. Every path below is relative to that worktree. The ec7ae954/a7ed6c11 code this plan consumes (`cli.refuse_claimed` at `src/agent_manager/cli.py:850`, `cli.run_lease` at `src/agent_manager/cli.py:876`, `cli.ClaimedError` at `src/agent_manager/cli.py:168`, `control.card_claim`/`control.branch_claim` at `src/agent_manager/control.py:79-86`, `control.Lease.displaced` at `src/agent_manager/control.py:122`, `store.held_claims` at `src/agent_manager/store.py:735`, `Store._token` at `src/agent_manager/store.py:872`) is on this branch already. No other subtask's code is assumed.

## Global Constraints

- The only source file changed is `src/agent_manager/orchestrate.py`. Do not change `store.py`, `control.py`, `cli.py` (card/task-resume paths, `HANDLED`, `_claimed_error` wording) or the schema.
- All new and changed tests live in `tests/test_orchestrate.py`. Nothing goes in `tests/e2e/`.
- Ordering invariant (M7 + X5): every refusal, `refuse_claimed` included, comes before `refresh_git`; on a fresh run `refresh_git` comes before `Store.open`. A refused run leaves no run row, journal, fetch, prune or worktree.
- Claim keys are exactly `card:<milestone id>`, `card:<each remaining subtask id>` in census order, `branch:<prefix>-integrate`, with no duplicates.
- Dry-run and reader paths never call `milestone_claims`, `refuse_claimed` or `run_lease`.
- Tests never sleep to prove ordering; they use fake-driver gates, spies and exit codes.
- CLI envelope unchanged: `{"ok": true, "data": ...}` / `{"ok": false, "error": {...}}` at exit 3.
- Verification: `uv run pytest` (no separate lint or typecheck).

## Review Focus

- A resume blocked by another live run's claim on one of its remaining subtasks: expect `ClaimedError` before `Store.open` and before git, with the run's rows untouched. Pinned by `test_a_milestone_resume_is_refused_before_the_store_opens_while_a_live_run_claims_its_card` in Task 2.
- Resuming a milestone run whose own lease is still live, with its own claims planted: expect `RunIsLiveError` (C10), not a `ClaimedError` about its own keys (this proves `run_id` was passed), and no git call. Pinned by `test_a_still_live_milestone_run_refuses_its_resume_before_touching_git` in Task 3.
- Another `am run --milestone M` with a different `--branch-prefix` shares only the milestone card key `card:M`: expect a refusal naming `card:M`. Pinned by the `milestone` case of the parametrized `test_a_milestone_run_is_refused_while_a_live_run_claims_one_of_its_cards` in Task 2.
- A subtask already `done` on the board is not claimed, so a human can still `am run --card` it elsewhere. Pinned in `test_a_milestone_run_holds_its_claims_while_driving` (a1 is done and absent from the held keys) in Task 2.
- Claims leak on a pause, a cancel, a lane `BaseException` or a raising Integrate, leaving the milestone unrunnable until the heartbeat goes stale. Pinned by the `paused`, `cancelled`, `killed` and `integrate_raises` cases of `test_a_milestone_run_releases_its_claims_on_every_exit` in Task 2.

Known collateral: `refuse_claimed` opens the project database (`paths.project_db_path`) before `refresh_git`, so the existing `test_a_failed_fetch_propagates_and_leaves_no_run_behind` (`tests/test_orchestrate.py:1801-1829`), which asserts that nothing but lock files exists under the data dir after a failed fetch, would now also see the empty projection file `projects/<digest>.db`. That file is not a run: it holds no run row, and ec7ae954's card-run refusal tests accept it the same way (`_run_dirs() == []`, `_recorded_run_ids(project) == []`). Task 2 Step 2 widens that test's filter to the project db file and adds an explicit "no run row" assertion.

---

### Task 1: `milestone_claims`, the X6 key list

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (insert a new function just above `def run_milestone(` at line 1299)
- Test: `tests/test_orchestrate.py` (insert two unit tests just above `def test_the_before_phase_is_read_out_of_a_stopped_detail():` at line 174)

**Interfaces:**
- Consumes: `control.card_claim(card_id: str) -> str`, `control.branch_claim(branch: str) -> str`, `dag.remaining_subtasks(story: census.StoryPlan) -> list[census.SubtaskPlan]`, `integration.integration_branch(branch_prefix: str) -> str` (all already imported in `orchestrate.py` line 55).
- Produces: `orchestrate.milestone_claims(milestone_id: str, stories: Sequence[census.StoryPlan], branch_prefix: str) -> list[str]`, used by Task 2.

- [ ] **Step 1: Write the failing unit tests**

In `tests/test_orchestrate.py`, insert immediately above `def test_the_before_phase_is_read_out_of_a_stopped_detail():`:

```python
def test_milestone_claims_lists_milestone_remaining_subtasks_then_integration_branch():
    """X6: `card:M`, every remaining subtask in census order, then
    `branch:<prefix>-integrate`. A done subtask, a closed story and a
    subtask-less story add no key."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    closed = _plan_story(2, [_plan_subtask(21)], status="done")
    c = _plan_story(3, [_plan_subtask(31), _plan_subtask(32)], blocked_by=[a.id])
    empty = _plan_story(4, [])

    keys = orchestrate.milestone_claims(_plan_id(99), [a, closed, c, empty], "m3")

    assert keys == [
        f"card:{_plan_id(99)}",
        f"card:{_plan_id(12)}",
        f"card:{_plan_id(31)}",
        f"card:{_plan_id(32)}",
        "branch:m3-integrate",
    ]


def test_milestone_claims_has_no_duplicates():
    """A card listed under two stories is claimed once, at its first place."""
    shared = _plan_subtask(11)
    a = _plan_story(1, [shared, _plan_subtask(12)])
    b = _plan_story(2, [shared])

    keys = orchestrate.milestone_claims(_plan_id(99), [a, b], "m3")

    assert keys == [
        f"card:{_plan_id(99)}",
        f"card:{_plan_id(11)}",
        f"card:{_plan_id(12)}",
        "branch:m3-integrate",
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k milestone_claims -v`
Expected: 2 FAILED with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'milestone_claims'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/orchestrate.py`, insert immediately above `def run_milestone(` (after the `supervise` function's `finally: grafo_logger.setLevel(level_before)` block ending at line 1296):

```python
def milestone_claims(
    milestone_id: str, stories: Sequence[census.StoryPlan], branch_prefix: str
) -> list[str]:
    """The `run_claims` keys a milestone run holds under its lease (X5, X6).

    `card:<milestone_id>`, then `card:<id>` for every remaining subtask
    (`dag.remaining_subtasks`: a done subtask or a closed story adds none) in
    census order, then `branch:<branch_prefix>-integrate`. Pure; a key
    already listed is not repeated, so the first occurrence keeps its place.
    """
    keys = [control.card_claim(milestone_id)]
    keys.extend(
        control.card_claim(subtask.id)
        for story in stories
        for subtask in dag.remaining_subtasks(story)
    )
    keys.append(control.branch_claim(integration.integration_branch(branch_prefix)))
    return list(dict.fromkeys(keys))


```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k milestone_claims -v`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): milestone_claims lists a milestone run's X6 claim keys"
```

---

### Task 2: Refuse claimed keys first, then hold them under the run's lease

**Files:**
- Modify: `src/agent_manager/orchestrate.py:1390-1423` (inside `run_milestone`: add the preflight after `drive = ...`, swap the lease)
- Modify: `tests/test_orchestrate.py:1-50` (imports), `tests/test_orchestrate.py:1817-1829` (widen `test_a_failed_fetch_propagates_and_leaves_no_run_behind`)
- Test: `tests/test_orchestrate.py` (append a new section at end of file, after `test_a_pause_lets_the_running_phase_finish_and_parks_before_the_next`, currently ending at line 4719)

**Interfaces:**
- Consumes: `orchestrate.milestone_claims` (Task 1); `cli.refuse_claimed(root: Path, keys: Sequence[str], *, run_id: str | None = None) -> None` (raises `cli.ClaimedError` with `.key` and `.run_id`); `cli.run_lease(store: Store, *, claims: Sequence[str] = ()) -> ContextManager[control.Lease]` (raises `cli.ClaimedError` or `cli.RunIsLiveError` on entry); `store_module.read_lease`, `store_module.held_claims`, `store_module.immediate`, `store_module.open_db`.
- Produces (test helpers in `tests/test_orchestrate.py`, reused by Task 3): `HERE: str`, `OTHER_RUN_ID: str`, `_plant_lease(project, *, run_id, token, pid, heartbeat_at, claims=()) -> None`, `_reaped_pid() -> int`, `_claim_rows(project) -> list[tuple[str, str, str]]`, `_held_keys(project, run_id) -> list[str]`, `_run_dirs() -> list[Path]`, `_worktree_count(project) -> int`, `_forbidden(name) -> Callable[..., Any]`, `_expected_claims(milestone: str, cards: list[str]) -> list[str]`. In `run_milestone`, the local `keys: list[str]` and `lease` (a `control.Lease` from `cli.run_lease`) that Task 3 extends.

- [ ] **Step 1: Add the imports the new tests need**

In `tests/test_orchestrate.py`, change the stdlib import block at the top:

```python
import ast
import asyncio
import inspect
import json
import logging
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
```

(`os` and `socket` are the two additions, in alphabetical place.)

- [ ] **Step 2: Widen the failed-fetch test to the project database the preflight opens**

In `tests/test_orchestrate.py`, in `test_a_failed_fetch_propagates_and_leaves_no_run_behind`, replace:

```python
    assert driver.calls == []
    # No run was left behind: the data directory holds nothing but the `git`
    # ProcessLock's own lock file, the one thing spec X7 does put there even on
    # this early a failure (paths.project_lock_path creates its `projects`
    # directory as soon as the lock object exists).
    data = paths.data_dir()
    projects = data / "projects"
    written = sorted(
        str(entry.relative_to(data))
        for entry in data.rglob("*")
        if entry != projects
        and not (entry.parent == projects and entry.suffix == ".lock")
    )
    assert written == []
```

with:

```python
    assert driver.calls == []
    # No run was left behind: the data directory holds nothing but the `git`
    # ProcessLock's own lock file, the one thing spec X7 does put there even on
    # this early a failure (paths.project_lock_path creates its `projects`
    # directory as soon as the lock object exists), and the project's
    # projection, which the read-only claims preflight (`cli.refuse_claimed`,
    # X5) opens before the fetch. That projection records no run.
    data = paths.data_dir()
    projects = data / "projects"
    db_name = paths.project_db_path(cli.resolve_repo_dir(project)).name
    written = sorted(
        str(entry.relative_to(data))
        for entry in data.rglob("*")
        if entry != projects
        and not (entry.parent == projects and entry.suffix == ".lock")
        and not (entry.parent == projects and entry.name.startswith(db_name))
    )
    assert written == []
    assert _run_ids(project) == []
```

Run: `uv run pytest tests/test_orchestrate.py::test_a_failed_fetch_propagates_and_leaves_no_run_behind -v`
Expected: PASS (the widened filter is still green before the change; after Step 6 it is what keeps it green).

- [ ] **Step 3: Write the failing Steps tests and their helpers**

Append to the end of `tests/test_orchestrate.py`:

```python
# ── claims: a milestone run's milestone, cards and integration branch (card 1a3fdd73) ──


HERE = socket.gethostname()
"""This host, as `control.Lease` records it."""

OTHER_RUN_ID = "20260930T080000Z-a1b2c3d4"
"""Another run, driven by another `am` process, that holds a claim."""


def _plant_lease(
    project: Path,
    *,
    run_id: str,
    token: str,
    pid: int,
    heartbeat_at: datetime,
    claims: tuple[str, ...] = (),
) -> None:
    """A `run_leases` row and its `run_claims`, as another process's `Lease` would leave them.

    Written over a second `open_db` connection inside `store.immediate`, on
    this host, window open. Live by C2 when `pid` is alive and `heartbeat_at`
    is fresh; dead when `pid` is `_reaped_pid()`.
    """
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        with store_module.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, 1)"
                " ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,"
                " pid=excluded.pid, host=excluded.host, acquired_at=excluded.acquired_at,"
                " heartbeat_at=excluded.heartbeat_at, accepting=1",
                (run_id, token, pid, HERE, heartbeat_at.isoformat(), heartbeat_at.isoformat()),
            )
            for key in claims:
                conn.execute(
                    "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " run_id=excluded.run_id, token=excluded.token,"
                    " claimed_at=excluded.claimed_at",
                    (key, run_id, token, heartbeat_at.isoformat()),
                )
    finally:
        conn.close()


def _reaped_pid() -> int:
    """The pid of a child that has exited and been waited for: dead by `pid_alive`."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _claim_rows(project: Path) -> list[tuple[str, str, str]]:
    """Every `run_claims` row as `(key, run_id, token)`, in key order."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [
            (row["key"], row["run_id"], row["token"])
            for row in conn.execute("SELECT key, run_id, token FROM run_claims ORDER BY key")
        ]
    finally:
        conn.close()


def _held_keys(project: Path, run_id: str) -> list[str]:
    """The keys `run_id`'s current lease holds, in key order, read as `am status` would."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        lease = store_module.read_lease(conn, run_id)
        if lease is None:
            return []
        return [claim.key for claim in store_module.held_claims(conn, run_id, lease.token)]
    finally:
        conn.close()


def _run_dirs() -> list[Path]:
    runs_root = paths.data_dir() / "runs"
    return sorted(runs_root.iterdir()) if runs_root.exists() else []


def _worktree_count(project: Path) -> int:
    return sum(
        1
        for line in _git(project, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    )


def _forbidden(name: str) -> Callable[..., Any]:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        pytest.fail(f"{name} ran before the claim refusal")

    return refuse


def _expected_claims(milestone: str, cards: list[str]) -> list[str]:
    """`milestone_claims`' keys spelled out, in `held_claims`' key order."""
    return sorted(
        [f"card:{milestone}", *(f"card:{card}" for card in cards), f"branch:{INTEGRATION_BRANCH}"]
    )


def _recording(
    project: Path, run_id: str, during: list[list[str]], then: Gate | None = None
) -> Gate:
    """A gate that records the run's held keys mid-subtask, then runs `then`."""

    async def gate(stop: StopSignal | None) -> None:
        during.append(_held_keys(project, run_id))
        if then is not None:
            await then(stop)

    return gate


@pytest.mark.parametrize("claimed", ["milestone", "subtask"])
@requires_git
@requires_brd
def test_a_milestone_run_is_refused_while_a_live_run_claims_one_of_its_cards(
    project, monkeypatch, claimed
):
    """X5/X6: the preflight refuses before git is refreshed and before the
    store opens, so nothing is fetched, pruned, recorded or made. `milestone`
    is another milestone run of M under another prefix: only `card:M` is shared."""
    shape = _milestone(project, {"A": 2})
    _a1, a2 = shape["subtasks"]["A"]
    card = shape["milestone"] if claimed == "milestone" else a2
    key = f"card:{card}"
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _forbidden("refresh_git"))
    driver = FakeDriver()

    with pytest.raises(cli.ClaimedError) as caught:
        _run(project, shape["milestone"], driver)

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert str(caught.value).startswith(f"card {card} is being driven by run {OTHER_RUN_ID}")
    assert driver.calls == []
    assert _run_ids(project) == []
    assert _run_dirs() == []
    assert _worktree_count(project) == 1
    assert _local_branches(project) == ["main"]
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@requires_git
@requires_brd
def test_a_milestone_run_is_refused_while_a_live_run_claims_its_integration_branch(
    project, monkeypatch
):
    shape = _milestone(project, {"A": 1})
    key = f"branch:{INTEGRATION_BRANCH}"
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _forbidden("refresh_git"))
    driver = FakeDriver()

    with pytest.raises(cli.ClaimedError) as caught:
        _run(project, shape["milestone"], driver)

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert str(caught.value).startswith(
        f"branch {INTEGRATION_BRANCH} is being driven by run {OTHER_RUN_ID}"
    )
    assert driver.calls == []
    assert _run_ids(project) == []
    assert _run_dirs() == []
    assert _worktree_count(project) == 1
    assert _local_branches(project) == ["main"]
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@requires_git
@requires_brd
def test_a_dead_claim_does_not_refuse_a_milestone_run(project):
    """A dead holder's claims are taken over by `take_lease`, then released
    with this run's lease; the dead holder's own lease row is left alone."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="dead-life",
        pid=_reaped_pid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(f"card:{a1}", f"branch:{INTEGRATION_BRANCH}"),
    )

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert _claim_rows(project) == []
    other = _lease(project, OTHER_RUN_ID)
    assert other is not None and other.token == "dead-life"


@requires_git
@requires_brd
def test_a_milestone_run_holds_its_claims_while_driving(project):
    """Mid-run the lease holds exactly `milestone_claims`: the milestone, the
    remaining subtasks (a1 is done on the board, so it is not claimed) and
    the integration branch. A fresh run never reports `took_over`."""
    shape = _milestone(project, {"A": 2, "B": 1})
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    rollup.set_status(a1, "done", repo_dir=project)
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    during: list[list[str]] = []
    driver = GatedDriver(
        gates={
            a2: _recording(project, run_id, during),
            b1: _recording(project, run_id, during),
        }
    )

    result = _run(project, shape["milestone"], driver)

    assert result["done"] is True, result
    assert "took_over" not in result
    expected = _expected_claims(shape["milestone"], [a2, b1])
    assert during == [expected, expected]
    assert _claim_rows(project) == []


@pytest.mark.parametrize(
    "exit_by", ["done", "escalated", "paused", "cancelled", "killed", "integrate_raises"]
)
@requires_git
@requires_brd
def test_a_milestone_run_releases_its_claims_on_every_exit(project, integrate_recorder, exit_by):
    """X5: the claims are held mid-run and gone, with the lease, however the
    run ends -- a lane's `BaseException` and a raising Integrate included."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    during: list[list[str]] = []
    outcomes: dict[str, Any] = {}
    then: Gate | None = None
    if exit_by == "escalated":
        outcomes[a1] = ("review", "boom")
    elif exit_by == "paused":
        then = _send_then_await_stop(project, run_id, "pause")
    elif exit_by == "cancelled":
        then = _send_then_await_stop(project, run_id, "cancel")
    elif exit_by == "killed":
        outcomes[a1] = _LaneKilled("the manager died mid-lane")
    elif exit_by == "integrate_raises":
        integrate_recorder.outcome = RuntimeError("integrate blew up")
    driver = GatedDriver(outcomes=outcomes, gates={a1: _recording(project, run_id, during, then)})

    def go() -> dict[str, Any]:
        return _run(project, shape["milestone"], driver, control_interval=0)

    if exit_by == "killed":
        with pytest.raises(_LaneKilled):
            _run_or_fail_if_it_hangs(go)
    elif exit_by == "integrate_raises":
        with pytest.raises(RuntimeError, match="integrate blew up"):
            go()
    else:
        go()

    assert during == [_expected_claims(shape["milestone"], [a1])]
    assert _claim_rows(project) == []
    assert _lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_milestone_resume_is_refused_before_the_store_opens_while_a_live_run_claims_its_card(
    project, monkeypatch
):
    """Review Focus 1: another live run took a1 since the interrupt. The
    resume refuses read-only, before `Store.open` and before git."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    key = f"card:{a1}"
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(store_module.Store, "open", _forbidden("Store.open"))
    monkeypatch.setattr(orchestrate, "refresh_git", _forbidden("refresh_git"))
    driver = FakeDriver()

    with pytest.raises(cli.ClaimedError) as caught:
        _resume(project, run_id, driver)

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert driver.calls == []
    assert _load(project, run_id).status == "escalated"
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "refused_while_a_live_run_claims or dead_claim_does_not_refuse_a_milestone or holds_its_claims_while_driving or releases_its_claims_on_every_exit or resume_is_refused_before_the_store_opens" -v`
Expected: all FAILED:
- the three `refused_while...`/`claims_one_of_its_cards[...]` cases with `Failed: refresh_git ran before the claim refusal`;
- `test_a_dead_claim_does_not_refuse_a_milestone_run` on `assert _claim_rows(project) == []` (the dead `card:`/`branch:` rows are still there);
- `test_a_milestone_run_holds_its_claims_while_driving` on `assert during == [expected, expected]` (`during` is `[[], []]`);
- every `releases_its_claims_on_every_exit[...]` case on `assert during == [...]` (`during` is `[[]]`);
- the resume refusal with `Failed: Store.open ran before the claim refusal`.

- [ ] **Step 5: Add the preflight to `run_milestone`**

In `src/agent_manager/orchestrate.py`, inside `run_milestone`, replace:

```python
    drive = cli.drive_subtask_async if driver is None else driver

    if resumed is None:
        # The first side effect. It runs after every refusal and before the store
        # is opened, so a failed fetch leaves no run directory behind.
        refresh_git(root)
```

with:

```python
    drive = cli.drive_subtask_async if driver is None else driver
    keys = milestone_claims(milestone_card.id, plan.stories, branch_prefix)
    # The last refusal (X5, X6): read-only, before `refresh_git` and before
    # `Store.open`, so a milestone, remaining subtask or integration branch
    # another live run claims leaves no fetch, prune, run row or run
    # directory. A resume's own rows are not a conflict; `take_lease` below
    # re-checks atomically.
    cli.refuse_claimed(root, keys, run_id=None if resumed is None else resumed.id)

    if resumed is None:
        # The first side effect. It runs after every refusal and before the store
        # is opened, so a failed fetch leaves no run directory behind.
        refresh_git(root)
```

- [ ] **Step 6: Take the lease with the claims**

In `src/agent_manager/orchestrate.py`, inside `run_milestone`, replace:

```python
        # After every refusal, and inside the `try` that closes the store, so
        # the lease is released before `store.close()` (live control C2).
        with control.Lease(store) as lease:
```

with:

```python
        # After every refusal, and inside the `try` that closes the store, so
        # the claims and the lease are released before `store.close()` on
        # every exit (live control C2, X5). Taken before `record_run`, so
        # every run write is fenced by this token; a lost race is
        # `ClaimedError` or `RunIsLiveError` with nothing recorded.
        with cli.run_lease(store, claims=keys) as lease:
```

The block body is unchanged: the same `lease` is still passed as `lease=lease` to `control.controlled(...)`.

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "refused_while_a_live_run_claims or dead_claim_does_not_refuse_a_milestone or holds_its_claims_while_driving or releases_its_claims_on_every_exit or resume_is_refused_before_the_store_opens" -v`
Expected: all PASSED.

- [ ] **Step 8: Run the whole module to catch regressions**

Run: `uv run pytest tests/test_orchestrate.py -q`
Expected: all PASSED. In particular `test_a_failed_fetch_propagates_and_leaves_no_run_behind` (widened in Step 2), `test_a_refused_resume_never_takes_a_lease` (`control.Lease.__enter__` is still what `cli.run_lease` enters, after `resume_checkpoints`), and `test_the_lease_is_released_and_its_window_closed_when_run_milestone_returns`.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): refuse claimed keys and hold milestone claims under the run lease"
```

---

### Task 3: On a resume, refresh git under the lease and report `took_over`

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (inside `run_milestone`: the resume block after `Store.open`, the start of the `run_lease` block, the `report` helper, and the docstring)
- Test: `tests/test_orchestrate.py` (append after the Task 2 section at end of file)

**Interfaces:**
- Consumes: Task 2's test helpers (`HERE`, `_plant_lease`, `_reaped_pid`, `_claim_rows`, `_expected_claims`), the existing helpers `_resume`, `_run`, `_lease`, `_record_git`, `_runs_tree`, `_statuses`, `_load`; `control.Lease.displaced: store.LeaseRow | None` (fields `pid: int`, `host: str`, `heartbeat_at: datetime`); `Store._token: str | None`.
- Produces: resumed `run_milestone` payloads carry `"took_over": {"pid": int, "host": str, "heartbeat_at": str}` when a dead holder was displaced.

- [ ] **Step 1: Write the failing Steps tests**

Append to the end of `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_refresh_git_and_first_write_run_inside_the_lease_on_resume(project, monkeypatch):
    """X5: on a resume the fetch/prune and the first journal line both happen
    under this life's lease, which already holds every claim."""
    shape = _milestone(project, {"A": 1, "B": 1})
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    seen_by_git: list[store_module.LeaseRow | None] = []
    real_refresh = orchestrate.refresh_git

    def refresh_spy(root: Path) -> None:
        seen_by_git.append(_lease(project, run_id))
        real_refresh(root)

    first_write: list[tuple[str | None, list[str]]] = []
    real_record_run = store_module.Store.record_run

    def record_spy(self, run):
        if not first_write:
            token = self._token
            held = (
                []
                if token is None
                else [
                    claim.key
                    for claim in store_module.held_claims(self.connection, self.run_id, token)
                ]
            )
            first_write.append((token, held))
        return real_record_run(self, run)

    monkeypatch.setattr(orchestrate, "refresh_git", refresh_spy)
    monkeypatch.setattr(store_module.Store, "record_run", record_spy)

    result = _resume(project, run_id, FakeDriver())

    assert result["done"] is True, result
    ((token, held),) = first_write
    assert token is not None
    assert held == _expected_claims(shape["milestone"], [a1, b1])
    (git_saw,) = seen_by_git
    assert git_saw is not None, "git was refreshed before the resume took its lease"
    assert (git_saw.token, git_saw.pid) == (token, os.getpid())


@pytest.mark.parametrize("outcome", ["done", "escalated"])
@requires_git
@requires_brd
def test_a_milestone_resume_excludes_its_own_claims_and_reports_took_over(project, outcome):
    """The interrupted life's lease is dead and its own claims are still
    planted: neither refuses, the resume takes them over, and every payload
    shape names the dead holder under `took_over`."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    dead = _reaped_pid()
    beat = datetime.now(timezone.utc)
    _plant_lease(
        project,
        run_id=run_id,
        token="crashed-life",
        pid=dead,
        heartbeat_at=beat,
        claims=tuple(_expected_claims(shape["milestone"], [a1])),
    )
    driver = FakeDriver(outcomes={} if outcome == "done" else {a1: ("review", "still")})

    result = _resume(project, run_id, driver)

    assert result.get(outcome) is True, result
    assert result["resumed"] is True
    assert result["took_over"] == {
        "pid": dead,
        "host": HERE,
        "heartbeat_at": beat.isoformat(),
    }
    assert _claim_rows(project) == []
    assert _lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_still_live_milestone_run_refuses_its_resume_before_touching_git(project, monkeypatch):
    """Review Focus 2 (C10): the run's own lease is live and holds its own
    claims. The preflight skips the run's own rows (so no `ClaimedError`
    about them), `run_lease` refuses with `RunIsLiveError`, and git, the
    journal and the rows are untouched."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    own = _expected_claims(shape["milestone"], [a1])
    _plant_lease(
        project,
        run_id=run_id,
        token="still-running",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=tuple(own),
    )
    git_calls = _record_git(monkeypatch)
    before = (_runs_tree(), _statuses(_load(project, run_id)))
    driver = FakeDriver()

    with pytest.raises(cli.RunIsLiveError):
        _resume(project, run_id, driver)

    assert driver.calls == []
    assert git_calls == []
    assert (_runs_tree(), _statuses(_load(project, run_id))) == before
    assert _claim_rows(project) == [(key, run_id, "still-running") for key in own]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "inside_the_lease_on_resume or reports_took_over or refuses_its_resume_before_touching_git" -v`
Expected: all FAILED:
- `test_refresh_git_and_first_write_run_inside_the_lease_on_resume` with `AssertionError: git was refreshed before the resume took its lease`;
- both `reports_took_over[...]` cases with `KeyError: 'took_over'`;
- `test_a_still_live_milestone_run_refuses_its_resume_before_touching_git` on `assert git_calls == []` (the resume ran `remote`/`worktree prune` before `run_lease` refused).

- [ ] **Step 3: Move the resume's `refresh_git` inside the lease**

In `src/agent_manager/orchestrate.py`, inside `run_milestone`, replace:

```python
        if resumed is not None:
            cards = open_cards(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
            # The store's own refusal, a checkpoint saved under another
            # workflow, comes before the first write and before git is touched.
            checkpoints = resume_checkpoints(store, cards)
            refresh_git(root)
```

with:

```python
        if resumed is not None:
            cards = open_cards(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
            # The store's own refusal, a checkpoint saved under another
            # workflow, comes before the first write and before git is touched.
            # Read-only, so it runs before the lease is taken.
            checkpoints = resume_checkpoints(store, cards)
```

and replace:

```python
        with cli.run_lease(store, claims=keys) as lease:
            store.record_run(run_record)
```

with:

```python
        with cli.run_lease(store, claims=keys) as lease:
            if resumed is not None:
                # A resume's first side effect, under this life's lease (X5):
                # a run still live elsewhere was refused on entry, before git.
                refresh_git(root)
            store.record_run(run_record)
```

- [ ] **Step 4: Report `took_over` from the `report` helper**

In `src/agent_manager/orchestrate.py`, inside `run_milestone`, replace:

```python
            def report(payload: dict[str, Any]) -> dict[str, Any]:
                """Every payload shape on the same terms: `bases` when built, `resumed` on a resume."""
                if resumed is not None:
                    payload["resumed"] = True
                return with_bases(payload, built_bases)
```

with:

```python
            def report(payload: dict[str, Any]) -> dict[str, Any]:
                """Every payload shape on the same terms: `bases` when built, and on
                a resume `resumed` plus `took_over` when a dead holder's lease
                was taken over (X5), as `cli._resume_from_checkpoint` reports it."""
                if resumed is not None:
                    payload["resumed"] = True
                    if lease.displaced is not None:
                        payload["took_over"] = {
                            "pid": lease.displaced.pid,
                            "host": lease.displaced.host,
                            "heartbeat_at": lease.displaced.heartbeat_at.isoformat(),
                        }
                return with_bases(payload, built_bases)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "inside_the_lease_on_resume or reports_took_over or refuses_its_resume_before_touching_git" -v`
Expected: all PASSED.

- [ ] **Step 6: Update the `run_milestone` docstring**

In `src/agent_manager/orchestrate.py`, in the `run_milestone` docstring, replace:

```python
    `milestone` is a card id or a title needle (O1). Everything that can refuse,
    `max_concurrent < 1` included, runs before the store is opened. Then the
    run takes a `control.Lease`, one `milestone` run is recorded with its
```

with:

```python
    `milestone` is a card id or a title needle (O1). Everything that can refuse,
    `max_concurrent < 1` and another live run's claim included, runs before
    the store is opened. Then the run takes a `control.Lease` with its
    `milestone_claims` (`cli.run_lease`), one `milestone` run is recorded with its
```

and replace:

```python
    The lease (live control C2) is held from `record_run` to the run's final
    record, and released before the store closes; every refusal comes before
    it. `controlled` polls this lease's `am pause`/`am cancel` requests every
    `control_interval` seconds and applies them to the run's one
    `StopSignal`; it closes the window and sweeps once more when the tree
    returns, before Integrate. A crash propagates and releases the lease.
    """
```

with:

```python
    Claims (multi-process X5, X6): the run's keys are `milestone_claims` --
    `card:<milestone>`, `card:<id>` of every remaining subtask, and
    `branch:<branch_prefix>-integrate`. `cli.refuse_claimed` checks them
    read-only as the last refusal, before `refresh_git` and `Store.open`, so
    a key another live run holds is `ClaimedError` with no fetch, prune, run
    row or run directory; a resume's own rows are no conflict. On a resume,
    `refresh_git` runs inside the lease, after `resume_checkpoints`, and a
    dead holder the lease took over is reported under `took_over` in every
    payload.

    The lease (live control C2) and its claims are held from `record_run` to
    the run's final record, and released before the store closes; every
    refusal comes before it. `controlled` polls this lease's `am pause`/`am
    cancel` requests every `control_interval` seconds and applies them to the
    run's one `StopSignal`; it closes the window and sweeps once more when the
    tree returns, before Integrate. A crash propagates and releases the lease
    and its claims.
    """
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: all PASSED (e2e tests skip unless opted in, as before). If any test outside `tests/test_orchestrate.py` asserts an empty data directory after a milestone run that failed in `refresh_git`, it is the same collateral as Task 2 Step 2; widen it the same way (exclude the project db file, assert no run row) rather than moving `refuse_claimed`.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): resume refreshes git under the lease and reports took_over"
```
