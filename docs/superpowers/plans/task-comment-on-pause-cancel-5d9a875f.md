# Comment on pause, cancel and `--card` runs (card 5d9a875f)

Subtask of story 77b0a064 "Outbox and wiring", milestone 21f4cf06. Narrows Task 2.3 of `docs/superpowers/plans/2026-09-29-board-comments.md`. Base: the state left by the done siblings b5b7b813 (outbox enqueue/flush) and 65ed3c70 (lane, base and clean/escalated run-end comments). Branch prefix `m12`.

## Scope

Wire the existing composers into the three run exits that still post nothing. Only two files change: `src/agent_manager/orchestrate.py` (`run_milestone`'s cancel and pause-only branches) and `src/agent_manager/cli.py` (`run_card`). `comments.py`, `board.py`, `store.py` and the outbox machinery are not touched; the composers (`compose_cancelled`, `compose_run_end`, `compose_done`, `compose_escalated`, `agent_reason`) and the `post_comment` helper are reused as built.

Out of scope (owned by 65ed3c70, already done): lane done/escalated comments, base-failed comments, run-end on clean, Integrate-escalated and lane-escalated exits (including escalated-with-pause), resume flushing of pending rows. Their tests are not duplicated.

## Observable behavior

All comments are posted only after the outcome they describe is recorded (B2), through `post_comment` (enqueue, then flush by card). Every flush warning is appended to the returned payload's `warnings`.

1. **Milestone cancel** (`stop.requested == "cancel"` in `run_milestone`, after `store.record_run(... "cancelled")`):
   - For each `LaneOutcome` with `kind == "stopped"` and a non-`None` `subtask`, in the order `outcomes` already has (wave order, census order within a wave): post `comments.compose_cancelled(run_id=run_id, card_id=outcome.subtask, before_phase=outcome.before_phase, branch=<that subtask's branch>, relaunch=f"am run --milestone {milestone_card.id}")`. The branch is the subtask's recorded branch, read off the `rows` mapping `record_plan` already built for this run: `rows[outcome.story][1][outcome.subtask].branch` (the `SubtaskRun` row's `branch` field — not a fresh `dag.subtask_branch(branch_prefix, subtask)` call, which takes a `SubtaskPlan`, not the `str` subtask id `outcome.subtask` carries).
   - Then one run-end comment on the milestone card via the existing `comment_run_end` closure, fed the report of `controlled_payload(run_id, "cancel", ...)`. That payload already carries `cancelled: True`, so `compose_run_end` reads outcome `cancelled` and suggests `am run --milestone <id>`.
   - No comment on pending stories, on story cards, or on subtasks already done. An escalated lane under a cancel already got its escalation comment from its lane; nothing extra is posted for it here.
2. **Milestone pause only** (`stop.requested == "pause"` with no escalated lane, after `record_run(... "stopped")`): exactly one comment, the run-end on the milestone card via `comment_run_end`. `controlled_payload(..., "pause", ...)` already sets `paused: True`, so no payload change is needed. No subtask comments.
3. **`run_card`** (after `card_run_status` and the three `store.record_*` calls, still inside `run_lease`, so the writes are fenced by the lease): post at most one comment on the subtask card itself, chosen by `summary.status`:
   - `done` -> `compose_done(run_id, card_id=card.id, summary, branch=branch, resumed_at=None)`.
   - `escalated` -> `compose_escalated(run_id, card_id=card.id, token=lease.token, failed_phase=summary.failed_phase, detail=summary.detail, reason=comments.agent_reason(summary.results, summary.failed_phase))`.
   - `stopped` with `stop.requested == "cancel"` -> `compose_cancelled(run_id, card_id=card.id, before_phase=orchestrate.stopped_before_phase(summary.detail), branch=branch, relaunch=f"am run --card {card.id}")`.
   - `stopped` under a pause -> no comment.
   - Never a milestone or story comment (`run_card` has no milestone). The flush warnings are appended to the returned `warnings` (`drive.warnings`).
   - `cli.py` reaches `post_comment` as `orchestrate.post_comment` (no new import cycle at module load; follow how `cli` already references `orchestrate`, otherwise import lazily inside the function).

## Error paths

- A `brd` failure, refusal or timeout during a flush is a warning in the payload only (B8): the run status, exit code, recorded rows and payload shape are unchanged, and the run is never escalated or parked because of it.
- A lost lease (`LeaseLostError` from a fenced store write) propagates as it does for any other fenced write; it is not swallowed.
- A crash between `record_run` and the flush leaves pending outbox rows that the existing resume/flush path posts later; keys are stable (`key(run_id, card_id, "cancelled")`, run-end keyed by lease token), so a replay never double-posts.
- Bodies reach `brd comment add` on stdin only, author `am`, last line `am-key: <key>` -- all guaranteed by the reused composers and `board.py`; this subtask adds no board calls of its own.

## Tests

All new tests follow the design spec's testing rule (step 14 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md`): they are **Steps-tier** tests driven with a fake driver against a temporary git repo and temporary `brd` board (no network, no real harness), in the style of 65ed3c70's tests at `tests/test_orchestrate.py:5251+` (`project` fixture, `_comments`, `_comment_states`). No unit test is added for `comments.py` (unchanged) and no end-to-end test is added. `tests/conftest.py`'s data-directory isolation guard must stay green.

`tests/test_orchestrate.py` (Steps tier):
- Cancel with two parked (stopped) subtasks leaves one `cancelled` comment on each, naming `stopped before: <phase>`, the branch and `am run --milestone <id>`, and one `cancelled` run-end comment on the milestone; done subtasks get only their earlier done comment; story cards get none.
- A pause with no escalation leaves exactly one comment, a `paused` run-end on the milestone naming `am resume <run_id>`; no subtask card gets a new comment.
- A cancel whose run-end comment the board refuses still returns `cancelled: True` with the run recorded `cancelled`, and the refusal appears in `warnings`.

`tests/test_cli.py` (Steps tier, `run_card` with injected `runner_factory`/fake driver):
- `am run --card` that finishes `done` leaves one done comment on the card and none on its parent or any milestone.
- `am run --card` that escalates leaves one escalation comment on the card with the failed phase.
- `am run --card` cancelled mid-walk leaves one `cancelled` comment naming `am run --card <id>`; a paused one leaves none.
- A board refusal on the `--card` comment is a warning; `status` and the recorded rows are unchanged.

Verification: `uv run pytest` (no separate lint or typecheck, per CLAUDE.md).

---

# Comment on pause, cancel and `--card` runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post board comments on the three run exits that still post nothing: a cancelled milestone run (per parked subtask plus the milestone run-end), a paused milestone run (milestone run-end only), and every `am run --card` outcome except a pause.

**Architecture:** Two call sites change. In `orchestrate.run_milestone`, the cancel branch composes one `compose_cancelled` per stopped lane outcome that names a subtask, posts each through the existing `post_comment`, then routes the payload through the existing `comment_run_end` closure; the pause-only branch routes its payload through `comment_run_end`. In `cli.run_card`, a new pure helper `card_outcome_comment` picks the composer from `summary.status` (and the stop), and the result is posted with `orchestrate.post_comment` after the three `store.record_*` calls, inside `run_lease`, with flush warnings appended to `drive.warnings`.

**Tech Stack:** Python 3, Typer CLI, pytest; tests use a real temporary git repo and a real temporary `brd` board (Steps tier).

**Spec:** `docs/superpowers/specs/task-comment-on-pause-cancel-5d9a875f-design.md` (prepended above, verbatim).

All paths below are relative to the worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m12/task-comment-on-pause-cancel-5d9a875f` (branch `m12/task-comment-on-pause-cancel-5d9a875f`, cut from `m12/task-comment-on-lane-65ed3c70`). Run every command from that directory. This plan assumes only what is on that branch: `comments.py` (composers, `enqueue`, `flush`), `orchestrate.post_comment`, `orchestrate.stopped_before_phase`, the `comment_run_end` closure and 65ed3c70's test helpers `_comments`, `_keys`, `_comment_states`, `_board_down` in `tests/test_orchestrate.py`. Nothing from any other subtask is assumed.

Orientation note: the spec summary handed to this planner was truncated at 2000 characters by the upstream stage (it over-ran its brief). This plan was written from the spec file on disk, not from that summary.

## Global Constraints

- Only `src/agent_manager/orchestrate.py` and `src/agent_manager/cli.py` change in `src/`; `comments.py`, `board.py`, `store.py` are not touched.
- Comments are posted only after the outcome they describe is recorded (B2), through `post_comment` (enqueue, then flush by card).
- A board failure, refusal or timeout is a warning in the payload only (B8): run status, exit code, recorded rows and payload shape unchanged.
- A lost lease (`LeaseLostError`) propagates; it is never swallowed.
- Keys are stable: `key(run_id, card_id, "cancelled")`; run-end keyed by lease token (`run-end:<token>`); escalated keyed by lease token (`escalated:<token>`).
- `brd` is reached only from `board.py`; bodies go on stdin, author `am`, last line `am-key: <key>` (all by the reused composers).
- Milestone cancel relaunch hint is exactly `am run --milestone <milestone id>`; card cancel relaunch hint is exactly `am run --card <card id>`.
- `cli.py` reaches `orchestrate` lazily inside the function (`from agent_manager import orchestrate`), as `cli.py:1343` and `cli.py:1742` already do; no module-level import.
- Tests are Steps tier only (temporary git repo + temporary `brd` board, fake driver / fake runner); no new e2e test; `tests/conftest.py` isolation guard stays green.
- Verification: `uv run pytest`. No lint or typecheck command exists.

## Review Focus

1. A cancel that lands while a lane is still waiting for a concurrency slot: that lane ends `stopped` with a subtask but `before_phase=None`. Expected: the subtask still gets one `cancelled` comment naming its branch and relaunch hint, with no `stopped before:` line and no crash. Test added to Task 1 (`test_a_cancel_on_a_lane_waiting_for_a_slot_comments_its_subtask_without_a_phase`).
2. A cancel that lands while a story's merged base is being built: the lane ends `stopped` with `subtask=None`. Expected: no comment on that story or its subtask, no `KeyError` from the `rows` lookup, and the milestone still gets its `cancelled` run-end. Test added to Task 1 (`test_a_cancel_while_a_base_builds_comments_no_story_and_still_ends_the_run`).
3. A cancel that meets a lane escalation: the escalated subtask must not get a second, `cancelled` comment; the run-end names the escalation and the parked subtask. Test added to Task 1 (`test_a_cancel_with_an_escalated_lane_comments_only_the_parked_subtask_as_cancelled`).
4. A paused milestone run that is later resumed to done: the milestone must end with two distinct run-end comments (paused, then done), never a replayed or merged one. Test added to Task 2 (`test_a_paused_then_resumed_run_leaves_a_paused_then_a_done_run_end`).
5. `am run --card` where a cancel meets an escalation: the run is recorded `cancelled` but the subtask's status is `escalated`; the card must get the escalation comment (chosen by `summary.status`), not a `cancelled` one. Test added to Task 3 (`test_a_card_cancel_that_meets_an_escalation_comments_the_escalation`).

---

### Task 1: Comment a cancelled milestone run

**Files:**
- Modify: `src/agent_manager/orchestrate.py:1506-1511` (docstring paragraph "Board comments"), `src/agent_manager/orchestrate.py:1656-1671` (`comment_run_end` docstring), `src/agent_manager/orchestrate.py:1676-1678` (cancel branch)
- Test: `tests/test_orchestrate.py` (append at end of file, after `test_a_subtask_less_storys_refused_base_comment_is_a_warning_on_the_run`)

**Interfaces:**
- Consumes (already on the branch): `orchestrate.post_comment(store: Store, root: Path, comment: comments.Comment, *, run_id: str) -> list[str]`; `comments.compose_cancelled(*, run_id: str, card_id: str, before_phase: str | None, branch: str, relaunch: str) -> Comment`; `comment_run_end(payload: dict[str, Any]) -> dict[str, Any]` (closure inside `run_milestone`); `rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]` (from `record_plan`, line 1591); `LaneOutcome` fields `kind`, `story`, `subtask`, `before_phase`.
- Test helpers consumed (already in `tests/test_orchestrate.py`): `_milestone`, `_run`, `_branch`, `_comments`, `_keys`, `_comment_states`, `_board_down`, `_load`, `_send`, `_send_then_await_stop`, `_meet`, `_meet_then_await_stop`, `_await_stop`, `_within`, `_census_levels`, `_subtasks_by_story`, `GatedDriver`, `fake_bases` fixture, `requires_git`, `requires_brd`, `STARTED_AT`.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_orchestrate.py`:

```python


# ── board comments on cancel and pause (card 5d9a875f) ─────────────────────


@requires_git
@requires_brd
def test_a_cancel_comments_each_parked_subtask_and_the_milestone(project):
    """Spec test 1: a2 and b1 park under the cancel and each get one
    `cancelled` comment; a1 keeps only its done comment; stories get none;
    the milestone gets one `cancelled` run-end. Comments go out in the
    order `stopped` lists the parked lanes (wave order)."""
    shape = _milestone(project, {"A": 2, "B": 1})
    milestone = shape["milestone"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_then_cancel(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a2 and b1 in flight together")
        _send(project, run_id, "cancel")
        await _await_stop(stop)

    driver = GatedDriver(gates={a2: meet_then_cancel, b1: _meet_then_await_stop(pair)})

    result = _run(project, milestone, driver, max_concurrent=2, control_interval=0)

    assert result["cancelled"] is True, result
    assert _load(project, run_id).status == "cancelled"
    parked = [row["subtask"] for row in result["stopped"]]
    assert sorted(parked) == sorted([a2, b1]), result
    for subtask in (a2, b1):
        found = _comments(project, subtask)
        assert _keys(found) == [f"{run_id}/{subtask}/cancelled"], subtask
        (comment,) = found
        assert comment.author == "am"
        assert comment.body.startswith(f"am · cancelled · run {run_id}\n")
        assert "stopped before: implement" in comment.body
        assert f"branch: {_branch(project, subtask)}" in comment.body
        assert f"relaunch: `am run --milestone {milestone}`" in comment.body
    assert _keys(_comments(project, a1)) == [f"{run_id}/{a1}/done"]
    for story in shape["stories"].values():
        assert _comments(project, story) == [], story
    on_milestone = _comments(project, milestone)
    (key,) = _keys(on_milestone)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    body = on_milestone[0].body
    assert body.startswith(f"am · cancelled · run {run_id}\n")
    assert "done: 1 of 3" in body
    assert "parked: " + ", ".join(f"[[{card}]]" for card in parked) in body
    assert f"next: `am run --milestone {milestone}`" in body
    cancelled_keys = [key for key, _state in _comment_states(project) if key.endswith("/cancelled")]
    assert cancelled_keys == [f"{run_id}/{card}/cancelled" for card in parked]
    assert all(state == "posted" for _key, state in _comment_states(project))
    assert "total" not in result
    assert result["warnings"] == []


@requires_git
@requires_brd
def test_a_cancel_with_an_escalated_lane_comments_only_the_parked_subtask_as_cancelled(project):
    """Review Focus 3: a1's lane already posted its escalation; the cancel
    adds no `cancelled` comment for it, only for parked b1, and the run-end
    names both."""
    shape = _milestone(project, {"A": 1, "B": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_cancel_then_escalate(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a1 and b1 in flight together")
        _send(project, run_id, "cancel")
        await _await_stop(stop)

    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: meet_cancel_then_escalate, b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, milestone, driver, max_concurrent=2, control_interval=0)

    assert result["cancelled"] is True, result
    a1_keys = _keys(_comments(project, a1))
    assert len(a1_keys) == 1 and a1_keys[0].startswith(f"{run_id}/{a1}/escalated:"), a1_keys
    assert _keys(_comments(project, b1)) == [f"{run_id}/{b1}/cancelled"]
    (comment,) = _comments(project, milestone)
    assert comment.body.startswith(f"am · cancelled · run {run_id}\n")
    assert f"escalated: [[{a1}]] at review" in comment.body
    assert f"parked: [[{b1}]]" in comment.body
    assert f"next: `am run --milestone {milestone}`" in comment.body


@requires_git
@requires_brd
def test_a_cancel_on_a_lane_waiting_for_a_slot_comments_its_subtask_without_a_phase(project):
    """Review Focus 1: `queued` never reached the driver, so its stopped
    outcome has no `before_phase`; q1 still gets one `cancelled` comment,
    with its branch and relaunch hint and no `stopped before:` line."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    milestone = shape["milestone"]
    (first, second, queued) = _census_levels(project, milestone)[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,), (q1,) = subtasks[first], subtasks[second], subtasks[queued]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_then_cancel(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "both slotted lanes in flight")
        _send(project, run_id, "cancel")
        await _await_stop(stop)

    driver = GatedDriver(gates={f1: meet_then_cancel, s1: _meet_then_await_stop(pair)})

    result = _run(project, milestone, driver, max_concurrent=2, control_interval=0)

    assert result["cancelled"] is True, result
    assert {"story": queued, "subtask": q1, "before_phase": None} in result["stopped"]
    found = _comments(project, q1)
    assert _keys(found) == [f"{run_id}/{q1}/cancelled"]
    body = found[0].body
    assert "stopped before:" not in body
    assert f"branch: {_branch(project, q1)}" in body
    assert f"relaunch: `am run --milestone {milestone}`" in body
    for subtask in (f1, s1):
        assert "stopped before: implement" in _comments(project, subtask)[0].body


@requires_git
@requires_brd
def test_a_cancel_while_a_base_builds_comments_no_story_and_still_ends_the_run(
    project, fake_bases
):
    """Review Focus 2: C's lane parks while its merged base builds, so its
    stopped outcome names no subtask. Neither C nor c1 gets a comment, and
    the milestone still gets its `cancelled` run-end."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    milestone = shape["milestone"]
    story_c = shape["stories"]["C"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    fake_bases.gates[story_c] = _send_then_await_stop(project, run_id, "cancel")
    fake_bases.outcomes[story_c] = bases.BaseFailed("the resolver was stopped", stopped=True)

    result = _run(project, milestone, FakeDriver(), control_interval=0)

    assert result["cancelled"] is True, result
    assert result["stopped"] == [{"story": story_c, "subtask": None, "before_phase": None}]
    assert _comments(project, story_c) == []
    assert _comments(project, c1) == []
    assert _keys(_comments(project, a1)) == [f"{run_id}/{a1}/done"]
    assert _keys(_comments(project, b1)) == [f"{run_id}/{b1}/done"]
    (comment,) = _comments(project, milestone)
    assert comment.body.startswith(f"am · cancelled · run {run_id}\n")
    assert result["warnings"] == []


@requires_git
@requires_brd
def test_a_cancel_whose_comments_the_board_refuses_is_still_cancelled_with_warnings(
    project, monkeypatch
):
    """Spec test 3 (error path B8): the board refuses both the parked
    subtask's and the milestone's comments. The run is still recorded
    `cancelled`, the payload keeps its shape, each refusal is one warning,
    and both rows stay pending for a later flush."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    _board_down(monkeypatch)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "cancel")})

    result = _run(project, milestone, driver, control_interval=0)

    assert result["cancelled"] is True, result
    assert set(result) == {"cancelled", "run_id", "stopped", "completed", "pending", "warnings"}
    assert _load(project, run_id).status == "cancelled"
    assert len(result["warnings"]) == 2, result["warnings"]
    assert (
        f"board comment {run_id}/{a1}/cancelled on card {a1} not posted" in result["warnings"][0]
    )
    assert f"board comment {run_id}/{milestone}/run-end:" in result["warnings"][1]
    assert [state for _key, state in _comment_states(project)] == ["pending", "pending"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "a_cancel_comments_each_parked or a_cancel_with_an_escalated_lane_comments_only or a_cancel_on_a_lane_waiting_for_a_slot_comments or a_cancel_while_a_base_builds_comments or a_cancel_whose_comments_the_board_refuses" -v`
Expected: all 5 FAIL. The first four fail on a comment assertion such as `assert [] == ['<run_id>/<card>/cancelled']` or `ValueError: not enough values to unpack` on `(comment,) = _comments(project, milestone)`; the board-refusal test fails on `assert 0 == 2` for `len(result["warnings"])`.

- [ ] **Step 3: Implement the cancel branch**

In `src/agent_manager/orchestrate.py`, replace the cancel branch (currently lines 1676-1678):

```python
            if stop.requested == "cancel":
                store.record_run(run_record.model_copy(update={"status": "cancelled"}))
                return report(controlled_payload(run_id, "cancel", outcomes, warnings))
```

with:

```python
            if stop.requested == "cancel":
                store.record_run(run_record.model_copy(update={"status": "cancelled"}))
                payload = report(controlled_payload(run_id, "cancel", outcomes, warnings))
                # Board-comments B2 (card 5d9a875f): after the cancel is recorded,
                # each subtask it parked, in wave order, then the milestone. A lane
                # stopped while its base built names no subtask and gets nothing;
                # an escalated lane already commented its own escalation.
                for outcome in outcomes:
                    if outcome.kind != "stopped" or outcome.subtask is None:
                        continue
                    assert outcome.story is not None
                    comment = comments.compose_cancelled(
                        run_id=run_id,
                        card_id=outcome.subtask,
                        before_phase=outcome.before_phase,
                        branch=rows[outcome.story][1][outcome.subtask].branch,
                        relaunch=f"am run --milestone {milestone_card.id}",
                    )
                    payload["warnings"].extend(post_comment(store, root, comment, run_id=run_id))
                return comment_run_end(payload)
```

- [ ] **Step 4: Update the two docstrings that say cancel is not commented**

In `src/agent_manager/orchestrate.py`, in the `comment_run_end` docstring, replace:

```python
                Called after the run's final record. `total` goes only into the
                dict `compose_run_end` reads, so the report keeps its shape; the
                flush's warnings join the report's own `warnings`. Cancel and
                pause-only exits are not commented here (card 5d9a875f).
                """
```

with:

```python
                Called after the run's final record, on every exit that records
                one. `total` goes only into the dict `compose_run_end` reads, so
                the report keeps its shape; the flush's warnings join the
                report's own `warnings`.
                """
```

In the `run_milestone` docstring, replace:

```python
    flushed. Each done or escalated subtask and each failed merged base is
    commented by its lane; a lane-escalated, Integrate-escalated or done run
    comments its end on the milestone card. A flush's warnings join the
    report's `warnings`; nothing else about the run changes.
```

with:

```python
    flushed. Each done or escalated subtask and each failed merged base is
    commented by its lane. A cancel comments each subtask it parked, in wave
    order (card 5d9a875f). Every recorded end -- cancelled, escalated, paused
    or done -- is commented on the milestone card. A flush's warnings join
    the report's `warnings`; nothing else about the run changes.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "a_cancel_comments_each_parked or a_cancel_with_an_escalated_lane_comments_only or a_cancel_on_a_lane_waiting_for_a_slot_comments or a_cancel_while_a_base_builds_comments or a_cancel_whose_comments_the_board_refuses" -v`
Expected: 5 PASS.

- [ ] **Step 6: Run the existing cancel tests to make sure nothing regressed**

Run: `uv run pytest tests/test_orchestrate.py -k "cancel" -v`
Expected: all PASS, including `test_a_cancelled_milestone_records_cancelled_and_skips_integrate` and `test_a_cancel_with_an_escalated_lane_records_cancelled_and_lists_escalations`, whose exact-payload assertions (`"warnings": []`, no `total` key) must still hold.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: comment a cancelled milestone run on its parked subtasks and milestone"
```

---

### Task 2: Comment a paused milestone run

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (the pause-only branch, currently lines 1686-1688; after Task 1 it sits a few lines lower, directly after the lane-escalated branch that ends `return comment_run_end(report(payload))`)
- Test: `tests/test_orchestrate.py` (append at end of file, after Task 1's tests)

**Interfaces:**
- Consumes: `comment_run_end(payload: dict[str, Any]) -> dict[str, Any]`; `controlled_payload(run_id, "pause", outcomes, warnings)` (already sets `paused: True` and `resume`); test helpers `_milestone`, `_run`, `_resume`, `_comments`, `_keys`, `_comment_states`, `_send_then_await_stop`, `GatedDriver`, `FakeDriver`, `STARTED_AT`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_orchestrate.py`:

```python


@requires_git
@requires_brd
def test_a_pause_leaves_exactly_one_paused_run_end_on_the_milestone(project):
    """Spec test 2: one comment in total, on the milestone, telling a human
    to resume; parked a1, pending a2 and b1, and every story get nothing."""
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    milestone = shape["milestone"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "pause")})

    result = _run(project, milestone, driver, control_interval=0)

    assert result["paused"] is True, result
    assert len(_comment_states(project)) == 1
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    body = found[0].body
    assert found[0].author == "am"
    assert body.startswith(f"am · paused · run {run_id}\n")
    assert "done: 0 of 3" in body
    assert f"parked: [[{a1}]]" in body
    assert f"next: `am resume {run_id}`" in body
    for quiet in (a1, a2, b1, *shape["stories"].values()):
        assert _comments(project, quiet) == [], quiet
    assert "total" not in result
    assert result["warnings"] == []


@requires_git
@requires_brd
def test_a_paused_then_resumed_run_leaves_a_paused_then_a_done_run_end(project):
    """Review Focus 4: the resumed life has its own lease token, so its done
    run-end is a second comment with its own key, after the paused one."""
    shape = _milestone(project, {"A": 1})
    milestone = shape["milestone"]
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "pause")})
    first = _run(project, milestone, driver, control_interval=0)
    assert first["paused"] is True, first

    again = _resume(project, run_id, FakeDriver(), control_interval=0)

    assert again["done"] is True, again
    on_milestone = _comments(project, milestone)
    assert [comment.body.split("\n", 1)[0] for comment in on_milestone] == [
        f"am · paused · run {run_id}",
        f"am · done · run {run_id}",
    ]
    keys = _keys(on_milestone)
    assert len(set(keys)) == 2, keys
    assert all(key.startswith(f"{run_id}/{milestone}/run-end:") for key in keys)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "a_pause_leaves_exactly_one_paused_run_end or a_paused_then_resumed_run_leaves" -v`
Expected: both FAIL. The first on `assert 0 == 1` for `len(_comment_states(project))`; the second on the milestone list, which has only the done run-end (`[f"am · done · run {run_id}"]` instead of two entries).

- [ ] **Step 3: Implement the pause-only branch**

In `src/agent_manager/orchestrate.py`, replace:

```python
            if stop.requested == "pause":
                store.record_run(run_record.model_copy(update={"status": "stopped"}))
                return report(controlled_payload(run_id, "pause", outcomes, warnings))
```

with:

```python
            if stop.requested == "pause":
                store.record_run(run_record.model_copy(update={"status": "stopped"}))
                # Board-comments B2 (card 5d9a875f): only the milestone's run-end;
                # a parked subtask is resumed, not closed, so it gets no comment.
                return comment_run_end(
                    report(controlled_payload(run_id, "pause", outcomes, warnings))
                )
```

(The other `if stop.requested == "pause":` in this function, inside the lane-escalated branch, sets `payload["control"] = "pause"` and is not changed.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "a_pause_leaves_exactly_one_paused_run_end or a_paused_then_resumed_run_leaves" -v`
Expected: 2 PASS.

- [ ] **Step 5: Run the existing pause tests to make sure nothing regressed**

Run: `uv run pytest tests/test_orchestrate.py -k "pause or paused" -v`
Expected: all PASS, including `test_a_paused_milestone_parks_records_stopped_and_skips_integrate` and `test_a_pause_applied_after_the_last_lane_already_finished_still_skips_integrate`, whose exact-payload assertions must still hold (`warnings == []`, no new keys).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: comment a paused milestone run's end on the milestone card"
```

---

### Task 3: Comment the card's own outcome in `run_card`

**Files:**
- Modify: `src/agent_manager/cli.py` (add `card_outcome_comment` directly above `def run_card(` at line 896; insert the post after the three `store.record_*` calls at lines 1004-1008, inside `with run_lease(...)`; extend the `run_card` docstring)
- Test: `tests/test_cli.py` (append at end of file, after `test_readers_never_take_a_lease_or_a_lock`)

**Interfaces:**
- Consumes: `comments.compose_done(*, run_id, card_id, summary, branch, resumed_at) -> Comment`; `comments.compose_escalated(*, run_id, card_id, token, failed_phase, detail, reason) -> Comment`; `comments.compose_cancelled(*, run_id, card_id, before_phase, branch, relaunch) -> Comment`; `comments.agent_reason(results, failed_phase) -> str | None`; `orchestrate.stopped_before_phase(detail: str | None) -> str | None`; `orchestrate.post_comment(store, root, comment, *, run_id) -> list[str]`; `control.Lease.token: str`; `StopSignal.requested`; test helpers in `tests/test_cli.py`: `project`, `cards`, `control_applied` fixtures, `fake_runner`, `_controlling_factory`, `_controlled_card_run`, `_card_statuses`, `requires_git`, `requires_brd`.
- Produces: `cli.card_outcome_comment(*, run_id: str, card: models.Card, summary: SubtaskSummary, stop: StopSignal, branch: str, token: str) -> comments.Comment | None`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python


# ── board comments on `run --card` (card 5d9a875f) ──────────────────────────


def _card_comment_keys(project: Path, card_id: str) -> list[str]:
    """`card_id`'s comments on the temporary board, as their `am-key:` values."""
    return [
        comment.body.rstrip().rsplit("\n", 1)[-1].removeprefix("am-key: ")
        for comment in board.comment_list(card_id, repo_dir=project)
    ]


def _card_outbox(project: Path) -> list[tuple[str, str]]:
    """Every outbox row as `(key, state)`, in insertion order."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [
            (row["key"], row["state"])
            for row in conn.execute("SELECT key, state FROM board_comments ORDER BY rowid")
        ]
    finally:
        conn.close()


def _run_card_with(project: Path, cards: dict[str, str], factory) -> dict[str, Any]:
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=factory,
    )


@requires_git
@requires_brd
def test_a_done_card_run_leaves_one_done_comment_on_the_card_only(project, cards):
    """Spec cli test 1: one done comment naming the branch; nothing on the
    story or the milestone."""
    payload = _run_card_with(project, cards, lambda **kwargs: fake_runner())

    assert payload["status"] == "done", payload
    run_id, card = payload["run_id"], cards["subtask"]
    assert _card_comment_keys(project, card) == [f"{run_id}/{card}/done"]
    (comment,) = board.comment_list(card, repo_dir=project)
    assert comment.author == "am"
    assert comment.body.startswith(f"am · done · run {run_id}\n")
    assert f"branch: {payload['branch']}" in comment.body
    assert board.comment_list(cards["story"], repo_dir=project) == []
    assert board.comment_list(cards["milestone"], repo_dir=project) == []
    assert not [w for w in payload["warnings"] if "board comment" in w], payload["warnings"]
    assert _card_outbox(project) == [(f"{run_id}/{card}/done", "posted")]


@requires_git
@requires_brd
def test_an_escalated_card_run_leaves_one_escalation_comment_with_the_phase(project, cards):
    """Spec cli test 2: keyed by this run's lease token; names the failed
    phase and its detail, and tells a human to resume."""
    payload = _run_card_with(project, cards, lambda **kwargs: fake_runner(fail="review"))

    assert payload["status"] == "escalated", payload
    run_id, card = payload["run_id"], cards["subtask"]
    (key,) = _card_comment_keys(project, card)
    assert key.startswith(f"{run_id}/{card}/escalated:")
    (comment,) = board.comment_list(card, repo_dir=project)
    assert comment.body.startswith(f"am · escalated · run {run_id}\n")
    assert "phase: review" in comment.body
    assert "canned gate failure" in comment.body
    assert f"next: `am resume {run_id}`" in comment.body
    assert board.comment_list(cards["story"], repo_dir=project) == []
    assert board.comment_list(cards["milestone"], repo_dir=project) == []


@requires_git
@requires_brd
def test_a_cancelled_card_run_leaves_one_cancelled_comment_naming_run_card(
    project, cards, control_applied
):
    """Spec cli test 3, cancel half: where it stopped, its branch, and the
    `am run --card` relaunch."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[]
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "cancelled", payload
    run_id, card = payload["run_id"], cards["subtask"]
    assert _card_comment_keys(project, card) == [f"{run_id}/{card}/cancelled"]
    (comment,) = board.comment_list(card, repo_dir=project)
    assert comment.body.startswith(f"am · cancelled · run {run_id}\n")
    assert "stopped before: validate_spec" in comment.body
    assert f"branch: {payload['branch']}" in comment.body
    assert f"relaunch: `am run --card {card}`" in comment.body
    assert board.comment_list(cards["story"], repo_dir=project) == []
    assert board.comment_list(cards["milestone"], repo_dir=project) == []


@requires_git
@requires_brd
def test_a_paused_card_run_leaves_no_comment(project, cards, control_applied):
    """Spec cli test 3, pause half: a park is resumed, not closed."""
    factory = _controlling_factory(
        project, control_applied, command="pause", at="spec", seen=[]
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "stopped", payload
    for card_id in cards.values():
        assert board.comment_list(card_id, repo_dir=project) == [], card_id
    assert _card_outbox(project) == []


@requires_git
@requires_brd
def test_a_card_cancel_that_meets_an_escalation_comments_the_escalation(
    project, cards, control_applied
):
    """Review Focus 5: the run is `cancelled` (C6) but the walk escalated;
    the comment follows `summary.status`, so it is the escalation."""
    factory = _controlling_factory(
        project, control_applied, command="cancel", at="spec", seen=[], fail=True
    )

    payload = _controlled_card_run(project, cards, factory)

    assert payload["status"] == "cancelled", payload
    run_id, card = payload["run_id"], cards["subtask"]
    (key,) = _card_comment_keys(project, card)
    assert key.startswith(f"{run_id}/{card}/escalated:")
    assert "phase: spec" in board.comment_list(card, repo_dir=project)[0].body


@requires_git
@requires_brd
def test_a_card_comment_the_board_refuses_is_a_warning_and_changes_nothing_else(
    project, cards, monkeypatch
):
    """Spec cli test 4 (B8): status, recorded rows and payload keys are
    unchanged; the refusal is one warning and the row stays pending."""
    real_list = board.comment_list

    def refuse(card_id: str, *, repo_dir: Path | None = None):
        if card_id == cards["subtask"]:
            raise board.BoardError(
                "brd is down", argv=["brd", "comment", "list", card_id], exit_code=1
            )
        return real_list(card_id, repo_dir=repo_dir)

    monkeypatch.setattr(board, "comment_list", refuse)

    payload = _run_card_with(project, cards, lambda **kwargs: fake_runner())

    run_id, card = payload["run_id"], cards["subtask"]
    assert payload["status"] == "done", payload
    assert set(payload) == {
        "run_id",
        "card_id",
        "story_id",
        "branch",
        "base_branch",
        "worktree",
        "status",
        "failed_phase",
        "detail",
        "skipped",
        "warnings",
    }
    assert _card_statuses(project, run_id) == {"run": "done", "story": "done", "subtask": "done"}
    refused = [w for w in payload["warnings"] if f"board comment {run_id}/{card}/done" in w]
    assert len(refused) == 1, payload["warnings"]
    assert "not posted" in refused[0] and "brd is down" in refused[0]
    assert _card_outbox(project) == [(f"{run_id}/{card}/done", "pending")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "a_done_card_run_leaves_one_done_comment or an_escalated_card_run_leaves_one_escalation or a_cancelled_card_run_leaves_one_cancelled or a_paused_card_run_leaves_no_comment or a_card_cancel_that_meets_an_escalation or a_card_comment_the_board_refuses" -v`
Expected: 5 FAIL and 1 PASS. The done/escalated/cancelled/cancel-over-escalation tests fail on `assert [] == [...]` or `ValueError: not enough values to unpack`; the refusal test fails on `assert 0 == 1` for `len(refused)`. `test_a_paused_card_run_leaves_no_comment` already passes (it pins that the change adds nothing on a pause) and must stay green after Step 4.

- [ ] **Step 3: Add the pure composer chooser**

In `src/agent_manager/cli.py`, directly above `def run_card(` (line 896), add:

```python
def card_outcome_comment(
    *,
    run_id: str,
    card: models.Card,
    summary: SubtaskSummary,
    stop: StopSignal,
    branch: str,
    token: str,
) -> comments.Comment | None:
    """The one board comment a `run --card` walk leaves on its card, or None (card 5d9a875f).

    Chosen by `summary.status`, so a cancel that met an escalation comments
    the escalation: `done` is the done comment, `escalated` the escalation
    keyed by this life's lease `token`, and `stopped` under a cancel the
    cancelled comment with the `am run --card` relaunch. A stop under a pause
    is resumed, not closed, so it gets None. Never a story or milestone comment.
    """
    if summary.status == "done":
        return comments.compose_done(
            run_id=run_id, card_id=card.id, summary=summary, branch=branch, resumed_at=None
        )
    if summary.status == "escalated":
        failed_phase = summary.failed_phase or ""
        return comments.compose_escalated(
            run_id=run_id,
            card_id=card.id,
            token=token,
            failed_phase=failed_phase,
            detail=summary.detail,
            reason=comments.agent_reason(summary.results, failed_phase),
        )
    if stop.requested == "cancel":
        # `orchestrate` imports `cli`, so it is read here, at call time.
        from agent_manager import orchestrate

        return comments.compose_cancelled(
            run_id=run_id,
            card_id=card.id,
            before_phase=orchestrate.stopped_before_phase(summary.detail),
            branch=branch,
            relaunch=f"am run --card {card.id}",
        )
    return None


```

- [ ] **Step 4: Post it from `run_card`, after the rows are recorded and inside the lease**

In `src/agent_manager/cli.py`, inside `run_card`, replace:

```python
            store.record_run(run_record.model_copy(update={"status": run_status}))
            store.record_story(story.model_copy(update={"status": summary.status}))
            store.record_subtask(
                story.card_id, subtask.model_copy(update={"status": summary.status})
            )

        return {
```

with:

```python
            store.record_run(run_record.model_copy(update={"status": run_status}))
            store.record_story(story.model_copy(update={"status": summary.status}))
            store.record_subtask(
                story.card_id, subtask.model_copy(update={"status": summary.status})
            )

            # Board-comments B2 (card 5d9a875f): after the outcome is recorded and
            # still under the lease, so the outbox write is fenced. A board
            # failure is a warning (B8); a lost lease propagates.
            comment = card_outcome_comment(
                run_id=run_id,
                card=card,
                summary=summary,
                stop=stop,
                branch=branch,
                token=lease.token,
            )
            if comment is not None:
                # `orchestrate` imports `cli`, so it is read here, at call time.
                from agent_manager import orchestrate

                drive.warnings.extend(
                    orchestrate.post_comment(store, root, comment, run_id=run_id)
                )

        return {
```

- [ ] **Step 5: Extend the `run_card` docstring**

In `src/agent_manager/cli.py`, in the `run_card` docstring, replace:

```python
    the run `cancelled` (`card_run_status`). No control cancels a running phase.
    """
```

with:

```python
    the run `cancelled` (`card_run_status`). No control cancels a running phase.

    Board comments (card 5d9a875f): once the rows are recorded, still under
    the lease, the card gets at most one comment (`card_outcome_comment`);
    a flush's warnings join the payload's `warnings` and nothing else changes.
    """
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "a_done_card_run_leaves_one_done_comment or an_escalated_card_run_leaves_one_escalation or a_cancelled_card_run_leaves_one_cancelled or a_paused_card_run_leaves_no_comment or a_card_cancel_that_meets_an_escalation or a_card_comment_the_board_refuses" -v`
Expected: 6 PASS.

- [ ] **Step 7: Run the existing `run_card` and import-order tests to make sure nothing regressed**

Run: `uv run pytest tests/test_cli.py -k "run_card or card_run or envelope_keys or import_cleanly or warnings" -v`
Expected: all PASS, including `test_the_run_success_envelope_keys_are_frozen`, `test_a_failed_best_effort_board_phase_shows_up_in_warnings` and `test_cli_and_orchestrate_import_cleanly_in_either_order`.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: comment a run --card walk's own outcome on its card"
```

---

### Task 4: Full verification

**Files:** none changed.

**Interfaces:** none.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with no failures and no errors. `tests/conftest.py`'s data-directory isolation guard passes, which shows no test touched the real data directory.

- [ ] **Step 2: If anything fails, fix it in the task that owns the code and re-run**

A failure in `tests/test_orchestrate.py` belongs to Task 1 or Task 2 (`orchestrate.py`). A failure in `tests/test_cli.py` belongs to Task 3 (`cli.py`). Use superpowers:systematic-debugging, fix the code (not the assertion, unless the assertion contradicts the spec), re-run `uv run pytest`, and commit with a `fix:` message that names the test.
