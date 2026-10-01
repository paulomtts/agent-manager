<!-- task-pipeline: validated -->
# Comment on lane outcomes, merged bases and run end — subtask design

Card `65ed3c70-b4e3-473e-a634-f64688ff0ba5`, story `77b0a064` ("Outbox and wiring"), plan Task 2.2 of `docs/superpowers/plans/2026-09-29-board-comments.md`. This narrows the board-comments addendum (`docs/superpowers/specs/2026-09-29-board-comments-design.md`, B2, B5, B7, B8, B9) to the call sites below. Commit: `feat(orchestrate): comment outcomes on the board`.

## Scope

This branch is based on `m12/task-enqueue-and-flush-b5b7b813`, and that base already has `comments.compose_*`, `comments.agent_reason`, `comments.enqueue`, `comments.flush`, `board.comment_add`/`comment_list` and the store's `board_comments` outbox. This subtask only **calls** those functions, at the outcome sites. It changes nothing in `comments.py`, `board.py` or `store.py`.

Every site follows one pattern. First the outcome is recorded exactly as it is today. Then `comments.enqueue(store, comment, run_id=run_id, now=<utc now>)` runs, then `comments.flush(...)`. Each warning that `flush` returns is added to the warnings that reach the run's report. A flush never raises for a board failure (B8), so no outcome, status or return value changes because of the board.

### Sites (in `src/agent_manager/orchestrate.py` unless noted)

1. **Subtask done (`lane`).** This site comes right after `store.record_subtask(story.id, row… "done")`. It calls `compose_done(run_id=…, card_id=subtask.id, summary=result.summary, branch=row's branch, resumed_at=…)`, then enqueues and flushes. `resumed_at` is `runtime.engine.pending_phase(checkpoint)` when the lane passed a checkpoint to the driver as `resume_from`, and `None` otherwise. Flush is a blocking subprocess, so inside the async lane it runs off the event loop, the same way `board.show` does (`asyncio.to_thread`). Its warnings join the lane's `warnings`, so `LaneOutcome.warnings` carries them to the report.
2. **Subtask escalated (`lane`, non-`done`/non-`stopped` summary branch).** This site comes after the subtask and story are recorded `escalated` and before `LaneEscalated` is raised. It calls `compose_escalated(run_id, card_id=subtask.id, token=<this life's lease token>, failed_phase=summary.failed_phase, detail=summary.detail, reason=agent_reason(summary.results, summary.failed_phase))`, then enqueues and flushes. The flush warnings must still reach the report through the raised outcome's `warnings`. `lane` has no lease token today: `run_milestone` holds it (`lease.token` from `cli.run_lease`), so the token has to be threaded down through `supervise`/`SupervisorPlan` to `lane`. The plan stage decides how. The `escalated:<token>` key is what lets a second life that escalates at the same phase post a second comment (Review Focus 3).
3. **Merged base failed.** This covers both `except bases.BaseFailed` sites: the one in `lane` and the one in `base_only_lane`. It applies only when `error.stopped` is false. It runs after the escalation is recorded and before `LaneEscalated` is raised. It calls `compose_base_failed(run_id, story_id=story.id, base_branch=root_plan.branch, detail=error.detail)` on the **story** card, then enqueues and flushes. In `base_only_lane` there is no store row, but the comment still goes on the story card. The site lives in orchestrate rather than inside `bases.build` for three reasons: orchestrate is where `stopped` versus escalated is decided, both lanes are covered by one pattern, and `tests/test_orchestrate.py`'s `fake_bases` fixture replaces `bases.build` wholesale. `bases.py` is therefore expected to stay unchanged. If the implementer finds a reason to touch it after all, the matching test goes in `tests/test_bases.py`.
4. **Run end (`run_milestone`).** Three exits get a comment:
   - the lane-escalation branch (`status="escalated"`, including when `control: "pause"` is added);
   - the Integrate-escalation branch (`integrate_escalated_payload`);
   - the `done` branch.

   In each case the site comes after `store.record_run(...)`. It builds the report payload and calls `compose_run_end(run_id, milestone_id=milestone_card.id, token=lease.token, payload=<that payload plus "total": the milestone's subtask count>)`. `total` goes only into the dict handed to `compose_run_end` and must not change the report's shape. The site then enqueues, flushes, and adds the flush warnings to the returned payload's `warnings` before returning.

   Three exits get no run-end comment here:
   - **Cancel.** This branch belongs to sibling `5d9a875f`.
   - **Pure pause.** This branch also belongs to sibling `5d9a875f`.
   - **An exception from Integrate.** The run is never recorded, so it stays unrecorded and uncommented.
5. **Flush at start (`run_milestone`, fresh and resume).** This runs once the lease is held (so the fenced `mark_comment_posted` writes are allowed) and before `supervise` drives anything. It calls `flush(store, root, card_ids=<milestone card id + every story id + every subtask id of plan.stories>)`, which posts any run's leftover pending rows for this milestone's cards (B7, relaunch). Its warnings start the run's `warnings` list. Every refusal still happens before `Store.open`, exactly as today.
6. **`cli.resume_run`.** A milestone resume already goes through `run_milestone(resume_run_id=…)` and gets the flush from item 5. A `task`-workflow resume (`_resume_from_checkpoint`) flushes that run's pending rows (`run_id=run.id`) once its lease is held and before the walk continues. In both paths, refusals (unknown, cancelled or live run) stay before any store write and are unchanged.

### Explicitly not commented

- Lanes that are parked or `stopped` because another card escalated. The run-end comment lists them.
- Started events, per-phase events, retries, revision loops and gate warnings.
- The `lane` catch-all escalation (a lane bug with no `SubtaskSummary` and no phase). The run-end comment still records it.
- Cancel, pause-only and `run_card` outcomes, which belong to `5d9a875f`.
- Out-of-scope items from addendum §7.

## Observable behavior

- A clean run leaves exactly one `am · done` comment on each subtask it drove, and one `am · done · run <id>` run-end comment on the milestone card. Every body is authored `am` and ends `am-key: <key>`.
- When a run escalates, the escalated subtask gets an `am · escalated` comment and the milestone gets an escalated run-end comment. Parked siblings and their stories get nothing.
- Resuming after the fix leaves the first life's escalation comment in place. It adds `done` on the subtask with `(resumed at review)`, plus the second life's run-end comment, which has a distinct `run-end:<token>` key.
- A replay or resume that reaches a site with the same key posts nothing a second time (B9; handled by the outbox).
- When the board is down, the outcome, status and return value stay as they would be without comments. Only `warnings` gains the flush's warnings.

## Error paths

- A `BoardError` or lock timeout during a flush is turned into a warning inside `flush`, and the run carries on.
- `LeaseLostError` from `enqueue` or `flush` propagates like any fenced write (it is a `BaseException` and is never caught by the lane catch-all). Existing lease-loss behavior is unchanged.
- An unknown or partial payload in `compose_run_end` reads as `ended` and never raises (already in `comments.py`).
- `summary.failed_phase` is never `None` on the non-`done`/non-`stopped` branch: `runtime/walk.py`'s `_escalate` is the only path that sets a summary's status to `escalated`, and it always sets `failed_phase` to the phase name in the same call. So `compose_escalated`/`agent_reason` (both take a `str`) always get one. The lane's own catch-all escalation (no `SubtaskSummary`, Exception branch) stays uncommented, as already stated above. The rule is "no crash, no agent text beyond `_REASON_FIELDS`".

## Tests

Test placement: addendum §6 places these scenarios in **orchestrate tests driven by fake drivers**. Design §14 says "Engine — driven with a fake adapter" and "Steps — against … a temporary `brd` board; no network". So every test below goes in **`tests/test_orchestrate.py`**, at the existing fake-driver tier, on that module's temporary `brd` board (skipped when `brd` is absent, as today). Comments are read back with `board.comment_list` or from the store outbox. A fake `board_api` (or monkeypatched `board.comment_add`/`comment_list`/`write_lock`) is used only where a board failure is injected. The `tests/e2e` real-board proofs belong to Task 3.1 and are not written here. `tests/conftest.py`'s data-directory isolation guard must stay green.

1. A clean two-story run gives one done comment per subtask, each keyed `<run>/<subtask>/done`, plus exactly one run-end comment on the milestone card. No other card gets a comment. (`tests/test_orchestrate.py`, fake-driver tier.)
2. In an escalation with a parked sibling, comments appear only on the escalated subtask (`escalated:<token>` key, `phase:` line, the agent's `_REASON_FIELDS` reason quoted) and the milestone card (escalated run-end). The parked sibling and its story have none. (`tests/test_orchestrate.py`, fake-driver tier.)
3. Resume after the fix: the escalation comment is still there, and a done comment containing `(resumed at review)` is added. (`tests/test_orchestrate.py`, fake-driver tier with a checkpoint whose pending phase is `review`.)
4. A second life that escalates at the same phase gives two escalation comments on the subtask, with distinct token-scoped keys (Review Focus 3). (`tests/test_orchestrate.py`, fake-driver tier.)
5. A failed merged base (`fake_bases` returns `BaseFailed("…")`, not stopped) gives a `base-failed` comment on the story card, in both the `lane` and the `base_only_lane` cases. `BaseFailed(stopped=True)` gives none. (`tests/test_orchestrate.py`, `fake_bases` tier.)
6. An Integrate escalation gives a milestone run-end comment carrying `integrate failed at …`. (`tests/test_orchestrate.py`, fake-driver tier with `integration.integrate_milestone` replaced.)
7. With the board down for the run, the run still ends `done`, its status and return value match a board-up run except that `warnings`, and the rows stay pending. A later `run_milestone`/`am resume` start flush posts them. (`tests/test_orchestrate.py`, fake-driver tier with a failing fake board.)

Existing tests that assert exact `warnings` lists while the board is faked to fail, or with no real board, may now see flush warnings. Those assertions get updated only where the warnings genuinely come from the new flushes. No behavior assertion is loosened. `uv run pytest`, the whole suite including `tests/e2e`, must be green.

---

# Comment on Lane Outcomes, Merged Bases and Run End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post the board comments for a subtask that finishes done, a subtask that escalates, a merged base that fails, and the end of a milestone run. Each one goes through the outbox that already exists (`comments.enqueue` + `comments.flush`). Also flush leftover rows when a run starts or resumes.

**Architecture:** `orchestrate.py` gets two small helpers. `post_comment` (sync) enqueues one `comments.Comment` and flushes that card's pending rows, then returns the flush warnings. `post_comment_async` runs `post_comment` through `asyncio.to_thread` for the async lanes. These helpers are called at each outcome site, always after the outcome is recorded. The warnings they return join the lane's `warnings` (through `LaneOutcome.warnings`) or the run payload's `warnings`. The lease token is passed down as a new required keyword `lease_token` on `supervise` and `lane`. `run_milestone` and `cli._resume_from_checkpoint` each flush once before they drive anything. `comments.py`, `board.py`, `store.py` and `bases.py` stay unchanged.

**Tech Stack:** Python 3, asyncio, grafo, pytest, real temporary `brd` board + git repo (the existing `project` fixture), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m12/task-comment-on-lane-65ed3c70/docs/superpowers/specs/task-comment-on-lane-65ed3c70-design.md` (reproduced above). It extends `docs/superpowers/specs/2026-09-29-board-comments-design.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.

**Branch / worktree:** `m12/task-comment-on-lane-65ed3c70`, in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m12/task-comment-on-lane-65ed3c70`. It was cut from `m12/task-enqueue-and-flush-b5b7b813`. Every path below is relative to that worktree, and every command runs from there. Nothing from sibling `5d9a875f` is on this branch, and nothing here relies on it.

**Notes on inputs:** The spec author's summary and the exploration summary that reached this stage were both cut off by the harness (2061 chars capped at 2000, and 13185 capped at 8000). Both stages over-ran their brief. Nothing in this plan comes from the missing text. The open question the summary was cut off on (can `summary.failed_phase` be `None`?) is settled by the on-disk spec's Error paths section. The exploration summary's line numbers and its `StoryRecorder` references do not match this branch. Every line number below was read from this worktree.

## Global Constraints

- Verification: `uv run pytest` must be green, the whole suite including `tests/e2e`. Tests never touch the real data directory, and `tests/conftest.py`'s isolation guard stays green.
- `brd` is only ever called from `src/agent_manager/board.py`. Bodies go to `brd comment add` on stdin (`-`), never in argv. This is already true in `board.comment_add`, and this plan adds no `brd` call outside `board.py`.
- A board failure never escalates, parks or changes a run's status. It becomes a report warning (`comments.flush` returns it).
- No agent text goes on the board except the three failure fields in `comments._REASON_FIELDS` (`validate_spec`/`validate_plan` → `reason`, `implement` → `blocked_reason`, `review` → `unresolved_blockers`), reached only through `comments.agent_reason`.
- Author is `am`. Every body's last line is `am-key: <key>`. The cap is 1,500 characters per body. `comments.compose_*` already does all of this, and this plan never builds a body by hand in `src/`.
- `comments.py`, `board.py`, `store.py` and `bases.py` are not modified.
- Out of scope: cancel comments, pause-only comments and `cli.run_card` outcomes (sibling `5d9a875f`), plus addendum §7 (leave-me-alone's orchestrator, prompts, deleting comments, opening issues).
- Final commit message: `feat(orchestrate): comment outcomes on the board`.

## Review Focus

1. **The board is down for a whole run.** The run should end exactly as it would with the board up: same status, same payload keys. Only `warnings` grows, and the rows stay pending for the next start flush. Tested in Task 6.
2. **Two lanes finish subtasks at the same moment** (`max_concurrent=2`, both flushing in worker threads). Each subtask should get exactly one done comment, and none should be posted twice. Tested in Task 1 (the test runs with `max_concurrent=2`).
3. **A relaunch under a new run id over cards whose earlier run left pending rows.** The new run's start flush should post the old rows before anything is driven, and should not post them a second time. Tested in Task 6.
4. **An escalated summary whose `results` has no entry for the failed phase** (the fake drivers' default, and a real phase that died before writing a result). There should be no crash and no `reason:` line. Tested in Task 3 (second-life test).
5. **The run-end `total` leaking into the report.** `am run`'s JSON shape should not change. Tested in Task 2 (`"total" not in result` on the done and Integrate-escalated exits).

---

## File Structure

- Modify `src/agent_manager/orchestrate.py`:
  - add the `comments` import;
  - add `post_comment`, `post_comment_async` and `milestone_card_ids` next to `_utcnow`;
  - add comment calls at the done, escalated and base-failed sites in `lane` and `base_only_lane`;
  - add the `lease_token` keyword to `supervise` and `lane`;
  - add the run-end helper and the start flush in `run_milestone`.
- Modify `src/agent_manager/cli.py`: add the `comments` import, and the start flush in `_resume_from_checkpoint`.
- Modify `tests/test_orchestrate.py`: add a new section at the end of the file, `# ── board comments (card 65ed3c70) ──`, holding the helpers and scenarios 1–7.
- Modify `tests/test_cli.py`: add one test next to the existing task-workflow resume tests (after `test_a_pygents_resume_marks_the_orphan_attempt_harness_error`).

---

### Task 1: Done comment after each subtask finishes

**Files:**
- Modify: `src/agent_manager/orchestrate.py:55` (import), `:303-306` (helpers after `_utcnow`), `:1130-1131` (done site in `lane`)
- Test: `tests/test_orchestrate.py` (new section at end of file)

**Interfaces:**
- Consumes: `comments.Comment`, `comments.enqueue(store, comment, *, run_id, now)`, `comments.flush(store, root, *, run_id=None, card_ids=None, board_api=board) -> list[str]`, `comments.compose_done(*, run_id, card_id, summary, branch, resumed_at) -> Comment`.
- Produces: `orchestrate.post_comment(store: Store, root: Path, comment: comments.Comment, *, run_id: str) -> list[str]` and `async orchestrate.post_comment_async(store: Store, root: Path, comment: comments.Comment, *, run_id: str) -> list[str]`. Test helpers `_comments(project, card_id) -> list[board.BoardComment]`, `_keys(found) -> list[str]` and `_comment_states(project) -> list[tuple[str, str]]`, which later tasks reuse.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/test_orchestrate.py`:

```python
# ── board comments (card 65ed3c70) ──────────────────────────────────────────


def _comments(project: Path, card_id: str) -> list[board.BoardComment]:
    """`card_id`'s comments on the temporary board, oldest first."""
    return board.comment_list(card_id, repo_dir=project)


def _keys(found: list[board.BoardComment]) -> list[str]:
    """Each comment's `am-key:` value, read off its last line, in board order."""
    return [
        comment.body.rstrip().rsplit("\n", 1)[-1].removeprefix("am-key: ")
        for comment in found
    ]


def _comment_states(project: Path) -> list[tuple[str, str]]:
    """Every outbox row as `(key, state)`, in insertion order."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [
            (row["key"], row["state"])
            for row in conn.execute("SELECT key, state FROM board_comments ORDER BY rowid")
        ]
    finally:
        conn.close()


@requires_git
@requires_brd
def test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story(project):
    """Spec test 1, subtask half; Review Focus 2: two lanes finish at once,
    yet each subtask gets exactly one done comment and nothing stays pending."""
    shape = _milestone(project, {"A": 2, "B": 1})
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]

    result = _run(project, shape["milestone"], FakeDriver(), max_concurrent=2)

    assert result["done"] is True, result
    run_id = result["run_id"]
    for subtask in (a1, a2, b1):
        found = _comments(project, subtask)
        assert _keys(found) == [f"{run_id}/{subtask}/done"], subtask
        (comment,) = found
        assert comment.author == "am"
        assert comment.body.startswith(f"am · done · run {run_id}\n")
        assert f"branch: {_branch(project, subtask)}" in comment.body
        assert "(resumed at" not in comment.body
    for story in shape["stories"].values():
        assert _comments(project, story) == [], story
    assert [state for _key, state in _comment_states(project) if "/done" in _key] == [
        "posted",
        "posted",
        "posted",
    ]
    assert result["warnings"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story -v`
Expected: FAIL. The first `_keys(found)` assertion fails with `[] == ['<run>/<a1>/done']` because no comment is posted yet.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`, change the import on line 55 from:

```python
from agent_manager import bases, board, census, cli, control, dag, integration, models
```

to:

```python
from agent_manager import bases, board, census, cli, comments, control, dag, integration, models
```

Directly after `_utcnow` (which ends at line 306), add:

```python
def post_comment(
    store: Store, root: Path, comment: comments.Comment, *, run_id: str
) -> list[str]:
    """Queue `comment`, then flush its card's pending rows; the flush's warnings.

    Called only after the outcome it describes is recorded (board-comments B2).
    A board failure comes back as a warning, never an exception (B8); a lost
    lease propagates like any fenced write. Flushing by card also posts any
    earlier run's leftover row for that card.
    """
    comments.enqueue(store, comment, run_id=run_id, now=_utcnow())
    return comments.flush(store, root, card_ids=[comment.card_id])


async def post_comment_async(
    store: Store, root: Path, comment: comments.Comment, *, run_id: str
) -> list[str]:
    """`post_comment` off the run's event loop: a flush runs `brd`, a blocking
    subprocess, so a lane awaits it in a worker thread as it awaits `board.show`."""
    return await asyncio.to_thread(post_comment, store, root, comment, run_id=run_id)
```

In `lane`, replace lines 1130-1131:

```python
                store.record_subtask(story.id, row.model_copy(update={"status": "done"}))
                completed.append(subtask.id)
```

with:

```python
                store.record_subtask(story.id, row.model_copy(update={"status": "done"}))
                completed.append(subtask.id)
                # Board-comments B2: after the outcome is recorded, never instead of it.
                warnings.extend(
                    await post_comment_async(
                        store,
                        root,
                        comments.compose_done(
                            run_id=run_id,
                            card_id=subtask.id,
                            summary=summary,
                            branch=row.branch,
                            resumed_at=None,
                        ),
                        run_id=run_id,
                    )
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrate.py::test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story -v`
Expected: PASS

- [ ] **Step 5: Run the orchestrate module to catch regressions**

Run: `uv run pytest tests/test_orchestrate.py -q`
Expected: all pass. With the real temporary board up, a flush adds no warnings, so the existing `warnings` assertions are unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): comment done subtasks on the board"
```

---

### Task 2: Run-end comment on the milestone (done, lane-escalated, Integrate-escalated)

**Files:**
- Modify: `src/agent_manager/orchestrate.py:1512-1582` (inside `run_milestone`, after `built_bases = bases_payload(outcomes)`, and the three returns)
- Test: `tests/test_orchestrate.py` (end of the board-comments section)

**Interfaces:**
- Consumes: `post_comment` (Task 1), `comments.compose_run_end(*, run_id, milestone_id, token, payload) -> Comment`, `lease.token` (`control.Lease.token: str`), and `milestone_card.id`.
- Produces: no new public names. The local closure `comment_run_end(payload) -> payload` lives inside `run_milestone`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone(project):
    """Spec test 1, milestone half; Review Focus 5: `total` reaches the comment only."""
    shape = _milestone(project, {"A": 2, "B": 1})
    milestone = shape["milestone"]

    result = _run(project, milestone, FakeDriver())

    assert result["done"] is True, result
    run_id = result["run_id"]
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    (comment,) = found
    assert comment.author == "am"
    assert comment.body.startswith(f"am · done · run {run_id}\n")
    assert "done: 3 of 3" in comment.body
    assert f"integrated: {INTEGRATION_BRANCH}" in comment.body
    assert f"next: `git merge {INTEGRATION_BRANCH}`" in comment.body
    assert "total" not in result
    assert result["warnings"] == []


@requires_git
@requires_brd
def test_an_integrate_escalation_leaves_an_integrate_failed_run_end_comment(
    project, integrate_recorder
):
    """Spec test 6."""
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    milestone, story_b = shape["milestone"], shape["stories"]["B"]
    integrate_recorder.outcome = integration.IntegrateEscalation(
        story=story_b, files=["shared.txt"], detail="the resolver did not finish"
    )

    result = _run(project, milestone, FakeDriver())

    assert result["escalated"] is True, result
    run_id = result["run_id"]
    (comment,) = _comments(project, milestone)
    assert comment.body.startswith(f"am · escalated · run {run_id}\n")
    assert (
        f"integrate failed at integrate on [[{story_b}]]: the resolver did not finish"
        in comment.body
    )
    assert f"next: `am run --milestone {milestone}`" in comment.body
    assert "total" not in result
    assert result["warnings"] == []


@requires_git
@requires_brd
def test_a_lane_escalation_leaves_an_escalated_run_end_comment_naming_the_parked(project):
    """Spec test 2, milestone half: the run-end names the escalated subtask and
    the parked one, and tells a human to resume."""
    shape = _milestone(project, {"A": 1, "B": 2})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    b1, _b2 = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, milestone, driver, max_concurrent=2)

    assert result["escalated"] is True, result
    run_id = result["run_id"]
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    body = found[0].body
    assert body.startswith(f"am · escalated · run {run_id}\n")
    assert f"escalated: [[{a1}]] at review" in body
    assert f"parked: [[{b1}]]" in body
    assert f"next: `am resume {run_id}`" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "run_end_comment" -v`
Expected: 3 FAIL. Each fails at its first milestone-comment assertion with a "not enough values to unpack (expected 1, got 0)" error, because the milestone has no comments yet.

- [ ] **Step 3: Write minimal implementation**

In `run_milestone`, right after `built_bases = bases_payload(outcomes)` (line 1512) and before `def report(...)`, add:

```python
            total = sum(len(story.subtasks) for story in plan.stories)
```

Directly after the `report` closure (after line 1526, `return with_bases(payload, built_bases)`), add:

```python
            def comment_run_end(payload: dict[str, Any]) -> dict[str, Any]:
                """Comment `payload`'s outcome on the milestone card (board-comments B2).

                Called after the run's final record. `total` goes only into the
                dict `compose_run_end` reads, so the report keeps its shape; the
                flush's warnings join the report's own `warnings`. Cancel and
                pause-only exits are not commented here (card 5d9a875f).
                """
                comment = comments.compose_run_end(
                    run_id=run_id,
                    milestone_id=milestone_card.id,
                    token=lease.token,
                    payload={**payload, "total": total},
                )
                payload["warnings"].extend(post_comment(store, root, comment, run_id=run_id))
                return payload
```

Change the lane-escalation return (line 1540) from:

```python
                return report(payload)
```

to:

```python
                return comment_run_end(report(payload))
```

Change the Integrate-escalation return (line 1566) from:

```python
                return report(integrate_escalated_payload(run_id, outcome, warnings))
```

to:

```python
                return comment_run_end(
                    report(integrate_escalated_payload(run_id, outcome, warnings))
                )
```

Change the done return (lines 1569-1582) from `return report(` to `return comment_run_end(report(`, and close it with one more `)`:

```python
            store.record_run(run_record.model_copy(update={"status": "done"}))
            return comment_run_end(
                report(
                    {
                        "done": True,
                        "run_id": run_id,
                        "levels": [
                            {"level": index, "stories": [planned.story.id for planned in level]}
                            for index, level in enumerate(levels)
                        ],
                        "completed": completed,
                        "tips": tips,
                        "warnings": warnings,
                        "integrated": integrated_payload(outcome),
                    }
                )
            )
```

Leave the cancel return (line 1533) and the pause return (line 1543) exactly as they are.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "run_end_comment" -v`
Expected: 3 PASS

- [ ] **Step 5: Run the orchestrate module to catch regressions**

Run: `uv run pytest tests/test_orchestrate.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): comment the run's end on the milestone card"
```

---

### Task 3: Escalation comment on the subtask, keyed by this life's lease token

**Files:**
- Modify: `src/agent_manager/orchestrate.py`: the `lane` signature (`:932-948`), the escalated branch in `lane` (`:1118-1129`), the `supervise` signature (`:1188-1200`), the `lane(...)` call inside `supervise` (`:1242-1257`), and the `supervise(...)` call in `run_milestone` (`:1484-1501`)
- Test: `tests/test_orchestrate.py` (end of the board-comments section)

**Interfaces:**
- Consumes: `post_comment_async` (Task 1), `comments.compose_escalated(*, run_id, card_id, token, failed_phase, detail, reason) -> Comment`, `comments.agent_reason(results, failed_phase) -> str | None`.
- Produces: `supervise(..., lease_token: str, ...)` and `lane(..., lease_token: str, ...)` as new required keyword parameters. Test helper `WithResults(inner, results)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrate.py`:

```python
@dataclass
class WithResults:
    """Wraps a fake driver and merges canned phase results into a card's
    summary, as the real walk returns them, so `agent_reason` has its field."""

    inner: Any
    results: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def __call__(self, **kwargs: Any) -> cli.SubtaskDrive:
        drive = await self.inner(**kwargs)
        extra = self.results.get(kwargs["card"].id)
        if extra is None:
            return drive
        summary = replace(drive.summary, results={**drive.summary.results, **extra})
        return replace(drive, summary=summary)


@requires_git
@requires_brd
def test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone(project):
    """Spec test 2: the escalated subtask gets phase, detail and the agent's
    one reason field (quoted, `[[` broken); parked b1, pending c1 and every
    story get nothing."""
    shape = _milestone(project, {"A": 1, "B": 2, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = asyncio.Barrier(2)
    driver = WithResults(
        GatedDriver(
            outcomes={a1: ("review", "reviewer found a blocker")},
            gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
        ),
        results={
            a1: {"review": {"unresolved_blockers": ["the [[parser]] still drops input", "no test"]}}
        },
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["escalated"] is True, result
    run_id = result["run_id"]
    found = _comments(project, a1)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{a1}/escalated:")
    body = found[0].body
    assert found[0].author == "am"
    assert body.startswith(f"am · escalated · run {run_id}\n")
    assert "phase: review" in body
    assert "detail: reviewer found a blocker" in body
    assert 'reason: "the [ [parser]] still drops input; no test"' in body
    assert f"next: `am resume {run_id}`" in body
    for quiet in (story_a, story_b, b1, b2, story_c, c1):
        assert _comments(project, quiet) == [], quiet
    assert len(_comments(project, shape["milestone"])) == 1
    assert result["warnings"] == []


@requires_git
@requires_brd
def test_a_second_life_escalating_at_the_same_phase_adds_a_second_escalation_comment(project):
    """Spec test 4 (Review Focus 3 of the card); Review Focus 4 here: no
    review result at all gives no `reason:` line and no crash."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, milestone, FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]

    again = _resume(project, run_id, FakeDriver(outcomes={a1: ("review", "still")}))

    assert again["escalated"] is True, again
    found = _comments(project, a1)
    keys = _keys(found)
    assert len(keys) == 2 and len(set(keys)) == 2, keys
    assert all(key.startswith(f"{run_id}/{a1}/escalated:") for key in keys)
    assert "detail: boom" in found[0].body
    assert "detail: still" in found[1].body
    assert all("reason:" not in comment.body for comment in found)
    milestone_keys = _keys(_comments(project, milestone))
    assert len(milestone_keys) == 2 and len(set(milestone_keys)) == 2, milestone_keys
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "escalation_comments_only or second_life_escalating" -v`
Expected: 2 FAIL. The first fails with "not enough values to unpack (expected 1, got 0)" on a1's comments. The second fails with `len(keys) == 2` while `keys == []`.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`, add `lease_token: str,` to `lane`'s keyword parameters, right after `run_id: str,` (line 937):

```python
async def lane(
    story: census.StoryPlan,
    *,
    plan: SupervisorPlan,
    store: Store,
    run_id: str,
    lease_token: str,
    root: Path,
```

Append this sentence to the last paragraph of `lane`'s docstring (just before its closing `"""`):

```python
    `lease_token` is this life's `control.Lease.token`; it keys the escalation
    comment, so a later life escalating at the same phase comments again.
```

In `lane`'s non-`done` branch (lines 1118-1129), between `store.record_story(story_row.model_copy(update={"status": "escalated"}))` and `raise LaneEscalated(`, insert:

```python
                    # Board-comments B2: keyed by this life's lease token, so a
                    # second life escalating at the same phase comments again.
                    warnings.extend(
                        await post_comment_async(
                            store,
                            root,
                            comments.compose_escalated(
                                run_id=run_id,
                                card_id=subtask.id,
                                token=lease_token,
                                failed_phase=summary.failed_phase,
                                detail=summary.detail,
                                reason=comments.agent_reason(
                                    summary.results, summary.failed_phase
                                ),
                            ),
                            run_id=run_id,
                        )
                    )
```

Do not change `outcome(...)`: it reads `warnings` when it is called, so the flush warnings already reach `LaneEscalated.outcome.warnings`.

Add `lease_token: str,` to `supervise`'s keyword parameters, right after `run_id: str,` (line 1192):

```python
async def supervise(
    plan: SupervisorPlan,
    *,
    store: Store,
    run_id: str,
    lease_token: str,
    root: Path,
```

In `node_coroutine`'s `lane(...)` call inside `supervise`, add `lease_token=lease_token,` after `run_id=run_id,`:

```python
                    result = await lane(
                        story,
                        plan=plan,
                        store=store,
                        run_id=run_id,
                        lease_token=lease_token,
                        root=root,
```

In `run_milestone`'s `supervise(...)` call, add `lease_token=lease.token,` after `run_id=run_id,`:

```python
                        store=store,
                        run_id=run_id,
                        lease_token=lease.token,
                        root=root,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "escalation_comments_only or second_life_escalating" -v`
Expected: 2 PASS

- [ ] **Step 5: Run the orchestrate module to catch regressions**

Run: `uv run pytest tests/test_orchestrate.py -q`
Expected: all pass. `_record_bounds` forwards `**kwargs`, so it passes the new keyword through.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): comment a subtask's escalation keyed by the lease token"
```

---

### Task 4: `resumed_at` on a done comment that continued from a checkpoint

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (the `resumed_at=None` argument added in Task 1 inside `lane`)
- Test: `tests/test_orchestrate.py` (end of the board-comments section)

**Interfaces:**
- Consumes: `runtime_engine.pending_phase(checkpoint: Checkpoint) -> str | None` (already imported as `runtime_engine`), and the `checkpoint` local in `lane`, which is set right before `drive(...)`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review(project):
    """Spec test 3: the first life's escalation stays; the second life adds
    `done` with `(resumed at review)` and its own, distinct run-end."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, milestone, FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",))
    driver = CheckpointDriver()

    result = _resume(project, run_id, driver)

    assert result["done"] is True, result
    assert driver.resumed[a1] is not _ABSENT
    found = _comments(project, a1)
    keys = _keys(found)
    assert len(keys) == 2, keys
    assert keys[0].startswith(f"{run_id}/{a1}/escalated:")
    assert keys[1] == f"{run_id}/{a1}/done"
    assert found[1].body.startswith(f"am · done · run {run_id}\n(resumed at review)\n")
    on_milestone = _comments(project, milestone)
    assert [comment.body.split("\n", 1)[0] for comment in on_milestone] == [
        f"am · escalated · run {run_id}",
        f"am · done · run {run_id}",
    ]
    assert len(set(_keys(on_milestone))) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review -v`
Expected: FAIL on the `startswith(... "(resumed at review)\n")` assertion. The body's second line is still `branch: ...`.

- [ ] **Step 3: Write minimal implementation**

In `lane`'s done-comment call (added in Task 1), replace:

```python
                            resumed_at=None,
```

with:

```python
                            # Where the walk picked up, only when the lane
                            # handed the driver a checkpoint as `resume_from`.
                            resumed_at=None
                            if checkpoint is None
                            else runtime_engine.pending_phase(checkpoint),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrate.py::test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review tests/test_orchestrate.py::test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story -v`
Expected: 2 PASS. The fresh run still has no `(resumed at` line.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): say where a resumed subtask picked up in its done comment"
```

---

### Task 5: Base-failed comment on the story card, in both lanes

**Files:**
- Modify: `src/agent_manager/orchestrate.py:916-922` (`base_only_lane`'s `except bases.BaseFailed`), `:1056-1065` (`lane`'s `except bases.BaseFailed`)
- Test: `tests/test_orchestrate.py` (end of the board-comments section)

**Interfaces:**
- Consumes: `post_comment_async` (Task 1), `comments.compose_base_failed(*, run_id, story_id, base_branch, detail) -> Comment`, `root_plan.branch` (`dag.RootPlan.branch`), and the `fake_bases` fixture / `FakeBases`.
- Produces: nothing new. `bases.py` is unchanged, so `tests/test_bases.py` is untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_a_failed_merged_base_comments_on_its_story(project, fake_bases):
    """Spec test 5, `lane` case: one `base-failed` comment on C, none on c1."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (c1,) = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    fake_bases.outcomes[story_c] = bases.BaseFailed("conflict nobody could resolve")

    result = _run(project, shape["milestone"], FakeDriver())

    assert (result["story"], result["failed_phase"]) == (story_c, "base"), result
    run_id = result["run_id"]
    found = _comments(project, story_c)
    assert _keys(found) == [f"{run_id}/{story_c}/base-failed"]
    body = found[0].body
    assert found[0].author == "am"
    assert body.startswith(f"am · base failed · run {run_id}\n")
    assert f"base branch: {root_plan.branch}" in body
    assert "detail: conflict nobody could resolve" in body
    assert _comments(project, c1) == []
    assert result["warnings"] == []


@requires_git
@requires_brd
def test_a_subtask_less_storys_failed_base_comments_on_that_story(project, fake_bases):
    """Spec test 5, `base_only_lane` case: J has no store row, yet its story
    card gets the comment."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_j = shape["stories"]["J"]
    root_plan = _root_plan(project, shape["milestone"], story_j)
    fake_bases.outcomes[story_j] = bases.BaseFailed("J's base broke")

    result = _run(project, shape["milestone"], FakeDriver())

    assert (result["story"], result["failed_phase"]) == (story_j, "base"), result
    run_id = result["run_id"]
    found = _comments(project, story_j)
    assert _keys(found) == [f"{run_id}/{story_j}/base-failed"]
    assert f"base branch: {root_plan.branch}" in found[0].body
    assert "detail: J's base broke" in found[0].body
    assert result["warnings"] == []


@requires_git
@requires_brd
def test_a_base_whose_resolver_was_stopped_gets_no_base_failed_comment(project, fake_bases):
    """Spec test 5, stopped case: `BaseFailed(stopped=True)` is a park, not a failure."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 1}, blocked_by={"C": ["A", "B"]}
    )
    story_c = shape["stories"]["C"]
    (d1,) = shape["subtasks"]["D"]
    c_building = asyncio.Event()

    async def park_with_the_stop(stop: StopSignal | None) -> None:
        c_building.set()
        await _await_stop(stop)

    async def escalate_once_c_builds(stop: StopSignal | None) -> None:
        await _within(c_building.wait(), "C's base to start building")

    fake_bases.gates[story_c] = park_with_the_stop
    fake_bases.outcomes[story_c] = bases.BaseFailed("the resolver was stopped", stopped=True)
    driver = GatedDriver(
        outcomes={d1: ("review", "d broke")}, gates={d1: escalate_once_c_builds}
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=3)

    assert result["stopped"] == [{"story": story_c, "subtask": None, "before_phase": None}]
    assert _comments(project, story_c) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "failed_merged_base_comments or failed_base_comments_on_that_story or no_base_failed_comment" -v`
Expected: the first two FAIL with `[] == ['<run>/<story>/base-failed']`. The third (stopped) already PASSES. It is a guard that must stay green after the change.

- [ ] **Step 3: Write minimal implementation**

In `base_only_lane`, replace the `except bases.BaseFailed` block (lines 916-922):

```python
        except bases.BaseFailed as error:
            if error.stopped:
                raise LaneStopped(outcome("stopped")) from error
            stop.trigger(story.id)
            raise LaneEscalated(
                outcome("escalated", failed_phase="base", detail=error.detail)
            ) from error
```

with:

```python
        except bases.BaseFailed as error:
            if error.stopped:
                raise LaneStopped(outcome("stopped")) from error
            stop.trigger(story.id)
            # Board-comments B2: on the story card; there is no store row here.
            flushed = await post_comment_async(
                store,
                root,
                comments.compose_base_failed(
                    run_id=run_id,
                    story_id=story.id,
                    base_branch=root_plan.branch,
                    detail=error.detail,
                ),
                run_id=run_id,
            )
            raise LaneEscalated(
                outcome(
                    "escalated",
                    failed_phase="base",
                    detail=error.detail,
                    warnings=tuple(flushed),
                )
            ) from error
```

In `lane`, inside its `except bases.BaseFailed as error:` block (lines 1056-1065), between `store.record_story(story_row.model_copy(update={"status": "escalated"}))` and `raise LaneEscalated(`, insert:

```python
                    # Board-comments B2: the failed base is the story's, so is the comment.
                    warnings.extend(
                        await post_comment_async(
                            store,
                            root,
                            comments.compose_base_failed(
                                run_id=run_id,
                                story_id=story.id,
                                base_branch=root_plan.branch,
                                detail=error.detail,
                            ),
                            run_id=run_id,
                        )
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "failed_merged_base_comments or failed_base_comments_on_that_story or no_base_failed_comment" -v`
Expected: 3 PASS

- [ ] **Step 5: Run the orchestrate module to catch regressions**

Run: `uv run pytest tests/test_orchestrate.py -q`
Expected: all pass. The existing `fake_bases` tests still see `"warnings": []` because the board is up.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): comment a failed merged base on its story"
```

---

### Task 6: Start flush in `run_milestone`, and the board-down behaviour

**Files:**
- Modify: `src/agent_manager/orchestrate.py`: add `milestone_card_ids` after `milestone_claims` (`:1299-1316`), and the start flush at `:1475` (`warnings = reroll_stale_stories(plan.stories, root)`)
- Test: `tests/test_orchestrate.py`: one pure test after `test_milestone_claims_has_no_duplicates` (line 196-209), and one runner test at the end of the board-comments section

**Interfaces:**
- Consumes: `comments.flush(store, root, *, card_ids=...)`, `census.StoryPlan.id`/`.subtasks`, `board.BoardError(message, *, argv, exit_code=None, error_type=None)`.
- Produces: `orchestrate.milestone_card_ids(milestone_id: str, stories: Sequence[census.StoryPlan]) -> list[str]`, and the test helper `_board_down(monkeypatch) -> dict[str, bool]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, directly after `test_milestone_claims_has_no_duplicates`, add:

```python
def test_milestone_card_ids_cover_the_milestone_every_story_and_every_subtask():
    """Board-comments B7: the start flush covers done and closed cards too,
    since an earlier run may have left a pending comment on any of them."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12)])
    closed = _plan_story(2, [_plan_subtask(21)], status="done")
    empty = _plan_story(3, [])
    shared = _plan_story(4, [_plan_subtask(12)])

    ids = orchestrate.milestone_card_ids(_plan_id(99), [a, closed, empty, shared])

    assert ids == [
        _plan_id(99),
        _plan_id(1),
        _plan_id(11),
        _plan_id(12),
        _plan_id(2),
        _plan_id(21),
        _plan_id(3),
        _plan_id(4),
    ]
```

At the end of the board-comments section, add:

```python
def _board_down(monkeypatch) -> dict[str, bool]:
    """Fail `board.comment_list` -- the first `brd` call a flush makes per
    row -- while `state["down"]`; every other board call stays real."""
    state = {"down": True}
    real = board.comment_list

    def flaky(card_id: str, *, repo_dir: Path | None = None) -> list[board.BoardComment]:
        if state["down"]:
            raise board.BoardError(
                "brd is down", argv=["brd", "comment", "list", card_id], exit_code=1
            )
        return real(card_id, repo_dir=repo_dir)

    monkeypatch.setattr(board, "comment_list", flaky)
    return state


@requires_git
@requires_brd
def test_a_board_that_is_down_changes_only_warnings_and_a_relaunch_posts_the_rows(
    project, monkeypatch
):
    """Spec test 7; Review Focus 1 and 3: the run still ends done with its usual
    keys, each failed row is one warning and stays pending, and a relaunch
    under a new run id posts both before anything else, exactly once."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    state = _board_down(monkeypatch)

    first = _run(project, milestone, BranchingDriver())

    run_id = first["run_id"]
    assert first["done"] is True, first
    assert first["completed"] == [a1]
    assert set(first) == {"done", "run_id", "levels", "completed", "tips", "warnings", "integrated"}
    assert _load(project, run_id).status == "done"
    assert len(first["warnings"]) == 2, first["warnings"]
    assert f"board comment {run_id}/{a1}/done on card {a1} not posted" in first["warnings"][0]
    assert f"board comment {run_id}/{milestone}/run-end:" in first["warnings"][1]
    assert all("will retry" in warning for warning in first["warnings"])
    assert [row_state for _key, row_state in _comment_states(project)] == ["pending", "pending"]

    state["down"] = False
    second = _run(project, milestone, BranchingDriver(), clock=lambda: LATER)

    assert second["done"] is True, second
    assert second["warnings"] == []
    assert _keys(_comments(project, a1)) == [f"{run_id}/{a1}/done"]
    milestone_keys = _keys(_comments(project, milestone))
    assert len(milestone_keys) == 2, milestone_keys
    assert milestone_keys[0].startswith(f"{run_id}/{milestone}/run-end:")
    assert milestone_keys[1].startswith(f"{second['run_id']}/{milestone}/run-end:")
    assert all(row_state == "posted" for _key, row_state in _comment_states(project))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "milestone_card_ids or board_that_is_down" -v`
Expected: 2 FAIL. The pure test fails with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'milestone_card_ids'`. The runner test gets through the first half, then fails on `_keys(_comments(project, a1)) == [...]` with `[]`, because the relaunch drives nothing on a1 and no start flush posts its old row.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`, directly after `milestone_claims` (which ends at line 1316), add:

```python
def milestone_card_ids(
    milestone_id: str, stories: Sequence[census.StoryPlan]
) -> list[str]:
    """The milestone card, then each story followed by its subtasks, census order.

    The cards a run's start flush covers (board-comments B7): done subtasks
    and closed stories included, since an earlier run may have left a pending
    comment on any of them. Pure; a card already listed is not repeated.
    """
    ids = [milestone_id]
    for story in stories:
        ids.append(story.id)
        ids.extend(subtask.id for subtask in story.subtasks)
    return list(dict.fromkeys(ids))
```

In `run_milestone`, replace line 1475:

```python
            warnings = reroll_stale_stories(plan.stories, root)
```

with:

```python
            # Board-comments B7: any run's leftover comments on this milestone's
            # cards go out under this lease, before anything is driven; a board
            # failure is a warning and the run goes on (B8).
            warnings = comments.flush(
                store, root, card_ids=milestone_card_ids(milestone_card.id, plan.stories)
            )
            warnings.extend(reroll_stale_stories(plan.stories, root))
```

Add one paragraph to `run_milestone`'s docstring, right before the paragraph that starts `The lease (live control C2)`:

```python
    Board comments (board-comments B2, B7): once the lease is held, before
    anything is driven, every pending outbox row on the milestone's cards is
    flushed. Each done or escalated subtask and each failed merged base is
    commented by its lane; a lane-escalated, Integrate-escalated or done run
    comments its end on the milestone card. A flush's warnings join the
    report's `warnings`; nothing else about the run changes.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "milestone_card_ids or board_that_is_down" -v`
Expected: 2 PASS

- [ ] **Step 5: Run the orchestrate module to catch regressions**

Run: `uv run pytest tests/test_orchestrate.py -q`
Expected: all pass. With nothing pending, the start flush makes no `brd` call (`pending_comments` returns `[]`), and the refusal tests never reach it because it runs inside the lease.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): flush the milestone's pending comments before driving"
```

---

### Task 7: Start flush in a `task`-workflow resume

**Files:**
- Modify: `src/agent_manager/cli.py:32-42` (import), `:1589-1593` (inside `_resume_from_checkpoint`, after `checkpoint_resume_phase`), `:1649` (payload `warnings`)
- Test: `tests/test_cli.py`, directly after `test_a_pygents_resume_marks_the_orphan_attempt_harness_error` (ends at line 4527)

**Interfaces:**
- Consumes: `comments.flush(store, root, *, run_id=...) -> list[str]`, the existing test helpers `_crash_pygents`, `_resume_factory`, `RESUME_KEYS`, `cards`, `project`, and `store_module.Store.enqueue_comment(*, run_id, card_id, key, body, now) -> bool`.
- Produces: nothing new. The payload keys are unchanged (`RESUME_KEYS`).

- [ ] **Step 1: Write the failing test**

In `tests/test_cli.py`, directly after `test_a_pygents_resume_marks_the_orphan_attempt_harness_error`, add:

```python
@requires_git
@requires_brd
def test_a_task_resume_posts_its_runs_pending_comments_before_the_walk_goes_on(
    project, cards
):
    """Board-comments B7 (card 65ed3c70): a row the killed life queued but
    never posted is on the card before the resumed walk's first phase."""
    run_id = _crash_pygents(project, cards, "plan")
    key = f"{run_id}/{cards['subtask']}/escalated:an-earlier-life"
    body = f"am · escalated · run {run_id}\nphase: plan\nam-key: {key}"
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.enqueue_comment(
            run_id=run_id,
            card_id=cards["subtask"],
            key=key,
            body=body,
            now=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        )
    finally:
        opened.close()
    inner = _resume_factory()
    on_the_card_at_walk_start: list[list[str]] = []

    def factory(**kwargs):
        if not on_the_card_at_walk_start:
            on_the_card_at_walk_start.append(
                [c.body for c in board.comment_list(cards["subtask"], repo_dir=project)]
            )
        return inner(**kwargs)

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=factory)

    assert payload["status"] == "done"
    assert set(payload) == RESUME_KEYS
    (seen,) = on_the_card_at_walk_start
    assert [b.rstrip().endswith(f"am-key: {key}") for b in seen] == [True]
    found = board.comment_list(cards["subtask"], repo_dir=project)
    assert [c.body.rstrip().endswith(f"am-key: {key}") for c in found] == [True]
    assert found[0].author == "am"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_a_task_resume_posts_its_runs_pending_comments_before_the_walk_goes_on -v`
Expected: FAIL. `seen` is `[]`, so the `== [True]` assertion fails, because nothing flushes the run's pending row.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/cli.py`, add `comments,` to the `from agent_manager import (...)` list (lines 32-42), after `census,`:

```python
from agent_manager import (
    board,
    census,
    comments,
    control,
    dag,
    dispatch,
    locks,
    models,
    prompt,
    store as store_module,
)
```

In `_resume_from_checkpoint`, directly after:

```python
            phase = checkpoint_resume_phase(
                checkpoint, card_id=subtask.card_id, run_id=run.id
            )
```

insert:

```python
            # Board-comments B7: this run's leftover comments go out under this
            # life's lease, after every refusal and before the walk goes on; a
            # board failure is a warning, never a refusal (B8).
            flushed = comments.flush(store, root, run_id=run.id)
```

In the payload, change:

```python
            "warnings": drive.warnings,
```

to:

```python
            "warnings": [*flushed, *drive.warnings],
```

Append this sentence to `_resume_from_checkpoint`'s docstring (before its closing `"""`):

```python
    Once the checkpoint is accepted, the run's pending board comments are
    flushed (board-comments B7) and their warnings lead the payload's.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_a_task_resume_posts_its_runs_pending_comments_before_the_walk_goes_on -v`
Expected: PASS

- [ ] **Step 5: Run the CLI module to catch regressions**

Run: `uv run pytest tests/test_cli.py -q`
Expected: all pass. With no pending rows, `flush` returns `[]` and makes no `brd` call, so every existing `warnings` assertion holds.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): flush a task run's pending comments on resume"
```

---

### Task 8: Full verification and the card's commit

**Files:**
- Possibly modify: existing assertions in `tests/` only where a `warnings` value genuinely comes from one of the new flushes (see Step 2).

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest`
Expected: all green, including `tests/e2e` and `tests/conftest.py`'s data-directory guard.

- [ ] **Step 2: Triage any failure**

A failure counts only when its cause is one of the new flushes. That means a test that fakes the board to fail, or that monkeypatches `board.comment_list`/`board.comment_add`/`board.write_lock`, and then asserts an exact `warnings` list. For such a test, change only the expected `warnings` to add the exact `board comment <key> on card <id> not posted (attempt 1 of 3), will retry: ...` lines the flush returned. Do not loosen any other assertion. Any other failure is a bug in Tasks 1–7: debug it with superpowers:systematic-debugging and fix the code, not the test. Re-run `uv run pytest` until it is green.

- [ ] **Step 3: Confirm the untouched files are untouched**

Run: `git diff --stat m12/task-enqueue-and-flush-b5b7b813 -- src/agent_manager/comments.py src/agent_manager/board.py src/agent_manager/store.py src/agent_manager/bases.py`
Expected: no output.

- [ ] **Step 4: Commit with the card's message**

```bash
git add -A src tests
git commit --allow-empty -m "feat(orchestrate): comment outcomes on the board"
```
