<!-- task-pipeline: validated -->
# Continue a milestone run with `am resume` (card 54e4ec29)

Narrows plan Task 4.1 (`docs/superpowers/plans/2026-09-25-supervisor-tree.md`, lines 337-345) and addendum decision T8 / sections 6, 8 and 9 (`docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`) to this one subtask. Parent story: 84802b0b "Milestone-wide resume".

## Prerequisites

This card assumes plan Tasks 1.1-1.3, 2.1-2.2 and 3.1-3.3 are merged: `StopSignal`, `cli.drive_subtask_async`, grafo-based dataflow roots, `bases.build`, and the grafo-tree `run_milestone` (`supervise` / `lane`). If they are missing from the working tree, the card is blocked. Do not re-implement them here.

## Scope

Files: `src/agent_manager/cli.py` (`resume_run`), `src/agent_manager/orchestrate.py` (`run_milestone`), and `src/agent_manager/bases.py` (a `resume_from: Checkpoint | None = None` keyword-only parameter threaded through `build` and `_resolve_conflict` into `runtime_engine.run_subtask_async`, which already accepts it — needed because point 2 below requires each base resolver's checkpoint to reach `bases.build`). Tests go in `tests/test_cli.py`, `tests/test_orchestrate.py`, and `tests/test_bases.py` if the resolver-resume case needs its own fixture.

1. `cli.resume_run(run_id, ...)` reads `run.workflow` and dispatches on it. `"task"` keeps the current `_resume_from_checkpoint` path exactly as it is. `"milestone"` calls `orchestrate.run_milestone(..., resume_run_id=run_id)`.
2. `orchestrate.run_milestone(..., resume_run_id: str | None = None)` gets a new keyword-only parameter. When it is given:
   - Reuse that run id and its recorded `branch_prefix`, `base_branch` and `max_concurrent_stories`. Do not create a new run.
   - Refresh git the same way a fresh run does: fetch origin when present, then worktree prune.
   - Re-derive the plan fresh from the board: a fresh census, with done cards skipped. No story-level or milestone-level state is checkpointed or read back.
   - For every open subtask of this run and every `base-<story>` resolver of this run, load `Store.latest_checkpoint(card_id)`. `Store.latest_open_checkpoint` may be used to decide which ones are open.
   - Validate all checkpoints before writing anything (see Error paths).
   - Mark orphan attempts `harness_error`, reusing `cli.orphan_attempts` and the existing marking pattern in `_resume_from_checkpoint` at `cli.py` around lines 1328-1341.
   - Row transitions: subtask/resolver rows that are stopped, escalated or started become `started`. The run row becomes `started`.
   - Run `supervise(...)` as a fresh run under the same run id. Each subtask with an open checkpoint gets it passed as `resume_from` to `drive_subtask_async`, and each base resolver's checkpoint goes to `bases.build`.
   - Bases re-derive and do not merge again. This relies on the existing `already_merged` short-circuit in `merge_tip`.
   - Integrate runs when all stories finish, the same as in a fresh run.
3. Report: the same shape as a fresh milestone run's report, plus `resumed: true`. `completed` lists only what finished during this invocation.

Resume is strict and relaunch is lenient. `am run --milestone` keeps its per-card leniency, and the two paths must not share the strict refusal.

## Error paths

All of these exit with code 3 and write nothing: no row changes, no orphan marking, no git mutation beyond the refresh.

- **Digest mismatch.** The newest turn/parked/escalated checkpoint of any open subtask has a digest different from the current TASK digest, or any `base-<story>` resolver's checkpoint differs from the current INTEGRATE digest. The whole resume is refused with "workflow changed since checkpoint". The message follows the wording of the single-subtask check at `cli.py:497-502` and names the card and both digests.
- **Run is `done`.** Refused with "run <id> finished; start new work with am run --milestone".
- `am resume` on a `task` run behaves exactly as it does today, including its own refusals.

## Invariants (milestone-wide rules)

- Only `orchestrate.py` imports grafo, and every grafo Node is built with `timeout=None`.
- Only the subtask is a pygents Agent.
- No test sleeps to prove ordering. Fake drivers block on `asyncio.Event`s.
- The milestone's base branch never moves, and nothing is pushed.
- The whole default suite stays green, including `tests/e2e`.

## Out of scope

- The fake-claude kill-and-resume end-to-end proof. That is `tests/e2e/test_milestone_resume.py` plus any kill switch in `tests/e2e/fake_claude.py`, and it belongs to sibling card 949d51a0.
- Verification discovery.
- Live pause, cancel, watch or retry.
- More than one `am` process per repo.
- A grafo `max_workers` option.
- Multi-blocker support for leave-me-alone.

## Tests

Placement rule (design spec section 14 and addendum section 9): engine and orchestrator mechanics are driven with a fake runner or fake drivers in `tests/test_orchestrate.py` / `tests/test_cli.py`, with temporary git repos and a temporary brd board and no real harness process. Only `tests/e2e/*` runs the fake `claude` binary as a subprocess, and this card adds nothing there.

| # | Test | Tier / file |
|---|---|---|
| 1 | A milestone run stops on an escalation, then the fake runner is fixed to succeed, then `am resume <run-id>`. Checks: the same run id is reused; the escalated subtask resumes at its failed phase and earlier phases are not re-dispatched; the parked subtask continues from its checkpoint; done cards are not driven; Integrate runs; the report has `resumed: true` and `completed` holds only this invocation's work. | fake-runner, `tests/test_cli.py` |
| 2 | `run_milestone(resume_run_id=...)` reuses the recorded `branch_prefix`, `base_branch` and `max_concurrent_stories`, and passes each open checkpoint as `resume_from` to the fake `drive_subtask_async` / `bases.build`. | fake-driver, `tests/test_orchestrate.py` |
| 3 | A stale digest on one open subtask gives exit 3 "workflow changed since checkpoint", and no rows or attempts change. | fake-runner, `tests/test_cli.py` |
| 4 | A stale digest on a `base-<story>` resolver checkpoint gives exit 3, and nothing is written. | fake-driver, `tests/test_orchestrate.py` |
| 5 | Resuming a `done` milestone run gives exit 3 "run <id> finished; start new work with am run --milestone", and nothing is written. | fake-runner, `tests/test_cli.py` |
| 6 | An orphan attempt (still `started` from the interrupted run) is marked `harness_error` on resume. | fake-runner, `tests/test_cli.py` |
| 7 | A merged base from the interrupted run is reused and not merged again (`already_merged`), and the milestone base branch has not moved. | temp git repo + fake driver, `tests/test_orchestrate.py` |
| 8 | `am resume` on a `task` run behaves as before: the existing task-resume tests pass unchanged, plus one assertion that dispatch routes to `_resume_from_checkpoint`. | fake-runner, `tests/test_cli.py` |

---

# Milestone Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am resume <run-id>` on a `milestone` run continues the whole milestone under the same run id, strictly (any stale checkpoint refuses the whole resume), while `am resume` on a `task` run stays exactly as it is.

**Architecture:** `cli.resume_run` dispatches on `run.workflow`. `orchestrate.run_milestone` gains a keyword-only `resume_run_id`; on that path it loads the recorded run (refusing unknown / non-milestone / `done` runs), finds the milestone from the run id's short id, re-derives the plan from the board, loads and validates one checkpoint per open card (subtasks under `TASK`, `base-<story>` resolvers under `INTEGRATE`) before any write or git refresh, then records the run `started`, re-records the plan, marks orphans `harness_error`, reopens stopped/escalated/started rows, and runs `supervise` with the validated checkpoints carried on the `SupervisorPlan`. The lane hands a subtask's checkpoint to the driver as `resume_from`; `build_merged_base` hands a resolver's checkpoint to `bases.build(resume_from=...)`, which continues the resolver before it re-derives the base with `merge_tip`'s `already_merged` short-circuit.

**Tech Stack:** Python 3, Typer, Pydantic, pygents (through `runtime.engine` only), grafo (in `orchestrate.py` only), SQLite store, pytest with asyncio auto mode, real temporary git repos and brd boards.

**Spec:** `docs/superpowers/specs/task-continue-a-milestone-54e4ec29-design.md` (prepended above, verbatim).

## Upstream notes

- The workflow's spec summary and exploration findings both arrived truncated (at 2000 and 8000 characters). This plan was written from the spec file on disk and from the code in this worktree, not from those summaries. The worktree already contains every prerequisite the spec names (`cli.drive_subtask_async`, `bases.build`, `orchestrate.supervise` / `lane`, `Store.latest_checkpoint`), so the card is not blocked.
- **Deviation 1 (store.py).** The spec's Scope lists `cli.py`, `orchestrate.py` and `bases.py`. Spec test 1 requires "the escalated subtask resumes at its failed phase", but the newest row a phase escalation leaves is an `escalated` row holding no turn (`runtime_engine.pending_phase` returns `None`, `runtime/engine.py:39-52`). The only row that holds the failed phase is the newest `turn` row, saved by `BEFORE_TURN` before that phase ran (`runtime/checkpoint.py:59-62`). No existing `Store` method reads it, so Task 1 adds `Store.latest_turn_checkpoint(card_id)` (one query, beside `latest_checkpoint`), tested in `tests/test_store.py`.
- **Deviation 2 (existing test).** `tests/test_cli.py::test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused` (line 4511) resumes a `workflow="milestone"` run through the task path and asserts the task payload. After dispatch, a milestone run goes through `run_milestone`, so Task 5 rewrites that test to the milestone payload. It becomes the "parked subtask continues from its checkpoint" half of spec test 1.
- **Deviation 3 (signature).** `models.Run` records no milestone id, so the resume path finds the milestone as the one root card whose short id is the run id's suffix (`cli.mint_run_id` builds `<timestamp>-<short milestone id>`). `run_milestone`'s `milestone`, `base_branch` and `branch_prefix` become `str | None` with `None` defaults; a fresh run still refuses any of them missing with `ValueError` (in `HANDLED`, so exit 3). `am run --milestone` always passes all three, so its kwargs are unchanged.
- **Deviation 4 (stricter order).** The spec allows "no git mutation beyond the refresh" on a refusal. The plan validates every checkpoint before `refresh_git`, so a refused resume does not touch git at all.
- **Deviation 5 (resume command).** A milestone payload has no `status` key, so the `resume` command's exit code must read `escalated` like `run --milestone` does (`cli.py:1143-1153`). For a milestone run `--verify` and `--allow-no-verification` now reach merged bases, Integrate and freshly started subtasks, so their help text is corrected.

## Global Constraints

- Only `orchestrate.py` imports grafo, and every grafo Node is built with `timeout=None`.
- Only the subtask is a pygents Agent; `cli`, `orchestrate` and `integration` never import `pygents` directly (guarded by `tests/test_orchestrate.py::test_the_engine_selecting_modules_never_import_pygents`).
- No test sleeps to prove ordering. Fake drivers block on `asyncio.Event`s.
- The milestone's base branch never moves, and nothing is pushed.
- The whole default suite stays green, including `tests/e2e`: `uv run pytest`.
- Every refusal exits 3 and writes nothing: no row changes, no orphan marking, no checkpoint rows, no journal lines.
- Refusal wording: digest mismatch contains `workflow changed since checkpoint`, the card id and both digests; a done run is exactly `run <id> finished; start new work with am run --milestone`.
- Resume is strict; relaunch (`am run --milestone`, `cli.continuable_checkpoint`) stays lenient and must not share the strict refusal.
- The fake-claude kill-and-resume proof (`tests/e2e/test_milestone_resume.py`) belongs to sibling card 949d51a0; this card adds nothing under `tests/e2e`.

## Review Focus

- A card finished on the board by hand since the interrupt, whose checkpoint in the run was saved under another digest: it is not open, so it is neither checked nor driven, and the resume goes on. Test in Task 4.
- A subtask whose newest row is `done` under another digest (the walk finished, the board write was lost): not refused, and driven from its first phase exactly as a relaunch would. Test in Task 3.
- A base resolver parked after it committed its merge (no `MERGE_HEAD`, queue head `verify`): continued at `verify`, `resolve` is not re-dispatched, and the base branch does not move. Test in Task 2.
- A milestone run that stopped at Integrate with nothing left to drive: resume retries Integrate under the same run id and ends `done`. Test in Task 4.
- `am resume --verify ... --allow-no-verification` on a milestone run: the commands and the opt-out reach `run_milestone` (and so merged bases, Integrate and freshly started subtasks). Test in Task 5.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/agent_manager/store.py` | Modify (after line 904) | `Store.latest_turn_checkpoint`: the newest `turn` row of a card in this run |
| `src/agent_manager/bases.py` | Modify (`_resolve_conflict` 112-162, `build` 188-299, new helpers) | `resolver_card_id`; `build(..., resume_from=)` continues a resolver before re-deriving the base |
| `src/agent_manager/orchestrate.py` | Modify (imports 44-57, `SupervisorPlan` 362-381, `supervisor_plan` 384-411, `build_merged_base` 570-604, `base_only_lane` 680-691, `lane` 813-855, `run_milestone` 1019-1162, new helpers) | Resume path: load/refuse the run, find its milestone, validate checkpoints, reopen rows, hand checkpoints to lanes, `resumed: true` |
| `src/agent_manager/cli.py` | Modify (`_resume_from_checkpoint` docstring 1314-1324, `resume_run` 1389-1431, `resume` 1434-1480) | Dispatch on `run.workflow`; exit code for a milestone payload |
| `tests/test_store.py` | Modify | `latest_turn_checkpoint` |
| `tests/test_bases.py` | Modify | Resolver resume against real temp git repos |
| `tests/test_orchestrate.py` | Modify | Resume helpers (unit) and `run_milestone(resume_run_id=)` on fake drivers |
| `tests/test_cli.py` | Modify | Dispatch, exit codes, and end-to-end resume on the fake runner |

---

### Task 1: `Store.latest_turn_checkpoint`

**Files:**
- Modify: `src/agent_manager/store.py:896-904` (add the method right after `latest_checkpoint`)
- Test: `tests/test_store.py` (add after `test_latest_checkpoint_is_the_highest_seq_of_this_stores_run`, line 1832)

**Interfaces:**
- Consumes: `Store.save_checkpoint`, `_checkpoint_from_row` (existing).
- Produces: `Store.latest_turn_checkpoint(self, card_id: str) -> Checkpoint | None` — highest-`seq` row of `card_id` in this store's run whose `reason == "turn"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_store.py` after line 1832:

```python
def test_latest_turn_checkpoint_is_the_newest_turn_row_of_this_stores_run(repo):
    """Card 54e4ec29: a phase escalation's newest row holds no turn, so a
    milestone resume rewinds to the turn the failing phase ran in."""
    other = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(other, "card-a", reason="turn", saved_at=_at(20))
    finally:
        other.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(0))
        wanted = _save_checkpoint(st, "card-a", reason="turn", agent={"turn": 1}, saved_at=_at(1))
        _save_checkpoint(st, "card-a", reason="escalated", saved_at=_at(2))
        _save_checkpoint(st, "card-b", reason="parked", saved_at=_at(3))
        found = st.latest_turn_checkpoint("card-a")
        only_parked = st.latest_turn_checkpoint("card-b")
        unknown = st.latest_turn_checkpoint("card-never-saved")
    finally:
        st.close()

    assert found == wanted
    assert found is not None and found.run_id == RUN_ID and found.seq == 1
    assert only_parked is None
    assert unknown is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_latest_turn_checkpoint_is_the_newest_turn_row_of_this_stores_run -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'latest_turn_checkpoint'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/store.py`, insert right after `latest_checkpoint` (after line 904):

```python
    def latest_turn_checkpoint(self, card_id: str) -> Checkpoint | None:
        """The highest-`seq` `turn` checkpoint of `card_id` in this store's run.

        A phase escalation's closing `escalated` row holds no turn
        (`runtime_engine.pending_phase`); the turn the failing phase ran in
        is the newest `turn` row, saved by `BEFORE_TURN` before it ran. A
        milestone resume rewinds to it (card 54e4ec29).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? AND card_id = ?"
                " AND reason = 'turn' ORDER BY seq DESC LIMIT 1",
                (self.run_id, card_id),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (all of `tests/test_store.py`)

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): latest_turn_checkpoint for a milestone resume's rewind"
```

---

### Task 2: `bases.build` continues a resolver from a checkpoint

**Files:**
- Modify: `src/agent_manager/bases.py:29-43` (imports), `:112-162` (`_resolve_conflict`), `:165-185` (new helpers beside `_resolver_detail`/`_stopped_detail`), `:188-299` (`build`)
- Test: `tests/test_bases.py` (import at line 35, helper `_resolve_build` at 446-470, new tests at the end of the resolver section, after line 866)

**Interfaces:**
- Consumes: `Store.latest_turn_checkpoint` (Task 1), `runtime_engine.run_subtask_async(..., resume_from=)` (existing).
- Produces:
  - `bases.resolver_card_id(story_id: str) -> str` returning `f"base-{story_id}"`.
  - `bases.build(root, tips, *, repo_dir, commands, allow_no_verification, store, run_id, story_id, runner_factory, stop, resume_from: Checkpoint | None = None) -> BaseResult`. With `resume_from`, the resolver is continued first; a tip it finished is reported in `merged` and `resolved`, never in `already_merged`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_bases.py`, change the import at line 35:

```python
from agent_manager.store import Checkpoint, Store
```

Replace `_resolve_build` (lines 446-470) with:

```python
async def _resolve_build(
    repo: Path,
    tips: list[str],
    *,
    store: Store | None,
    factory: FakeFactory | None,
    root: RootPlan = ROOT,
    story_id: str | None = STORY_C,
    run_id: str | None = RUN_ID,
    commands: tuple[str, ...] | list[str] = ("true",),
    stop: StopSignal | None = None,
    resume_from: Checkpoint | None = None,
) -> bases.BaseResult:
    """`bases.build` with the resolver parameters filled in. `resume_from` is
    passed only when given, so every earlier call is made exactly as before."""
    extra: dict[str, Any] = {} if resume_from is None else {"resume_from": resume_from}
    return await bases.build(
        root,
        list(tips),
        repo_dir=repo,
        commands=list(commands),
        allow_no_verification=False,
        store=store,
        run_id=run_id,
        story_id=story_id,
        runner_factory=factory,
        stop=stop,
        **extra,
    )
```

Add after `test_a_merge_in_progress_fails_for_a_human` (after line 866):

```python
# ── continuing a resolver on a milestone resume (card 54e4ec29) ─────────────


def test_the_resolver_card_is_base_and_the_story_id():
    assert bases.resolver_card_id(STORY_C) == CARD_C


@requires_git
async def test_a_resumed_resolver_parked_after_its_merge_continues_at_verify(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    """Review Focus 3: the merge is committed and the resolver was parked
    before `verify`. The resume runs `verify` only, re-dispatches no resolve,
    and merges nothing again."""
    repo = conflicting_repo
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    fired = threading.Event()

    def fire() -> None:
        stop.trigger(STORY_C)
        fired.set()

    def during() -> None:
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")

    with pytest.raises(bases.BaseFailed):
        await _resolve_build(
            repo,
            ["m7/a", "m7/b"],
            store=store,
            factory=FakeFactory(resolver=FakeResolver(during=during)),
            stop=stop,
        )
    parked = store.latest_checkpoint(CARD_C)
    assert parked is not None and parked.reason == "parked"
    built = rev(repo, BASE)
    again = FakeFactory()

    result = await _resolve_build(
        repo, ["m7/a", "m7/b"], store=store, factory=again, resume_from=parked
    )

    assert result == bases.BaseResult(
        branch=BASE, merged=[], already_merged=["m7/b"], resolved=[]
    )
    assert again.resolver.calls == []
    assert again.calls == [{"run_id": RUN_ID, "story_id": "bases", "card_id": CARD_C}]
    [subtask] = _bases_story(store).subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "done")
    assert [phase.name for phase in subtask.phases] == ["resolve", "verify"]
    assert store.latest_checkpoint(CARD_C).reason == "done"
    assert rev(repo, BASE) == built
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_resumed_resolver_rewound_to_its_failed_turn_finishes_the_merge(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    """The resolver escalated and left the merge in progress. Resumed from
    the `resolve` turn it failed in, it resolves once and the tip is merged."""
    repo = conflicting_repo
    with pytest.raises(bases.BaseFailed):
        await _resolve_build(
            repo,
            ["m7/a", "m7/b"],
            store=store,
            factory=FakeFactory(resolver=FakeResolver(refuse=True)),
        )
    assert store.latest_checkpoint(CARD_C).reason == "escalated"
    turn = store.latest_turn_checkpoint(CARD_C)
    assert turn is not None
    again = FakeFactory()

    result = await _resolve_build(
        repo, ["m7/a", "m7/b"], store=store, factory=again, resume_from=turn
    )

    assert result == bases.BaseResult(
        branch=BASE, merged=["m7/b"], already_merged=[], resolved=["m7/b"]
    )
    assert again.resolver.calls == [["shared.txt"]]
    assert _merge_head(base_worktree(repo)) is None
    assert is_ancestor(repo, "m7/a", BASE) and is_ancestor(repo, "m7/b", BASE)
    [subtask] = _bases_story(store).subtasks
    assert (subtask.card_id, subtask.status) == (CARD_C, "done")
    assert rev(repo, "master") == MASTER_BEFORE


@requires_git
async def test_a_resumed_resolver_with_no_runner_factory_fails_for_a_human(
    conflicting_repo: Path, store: Store, MASTER_BEFORE: str
):
    repo = conflicting_repo
    with pytest.raises(bases.BaseFailed):
        await _resolve_build(
            repo,
            ["m7/a", "m7/b"],
            store=store,
            factory=FakeFactory(resolver=FakeResolver(refuse=True)),
        )
    turn = store.latest_turn_checkpoint(CARD_C)

    with pytest.raises(bases.BaseFailed, match="continuing it needs") as excinfo:
        await _resolve_build(
            repo, ["m7/a", "m7/b"], store=store, factory=None, resume_from=turn
        )

    assert excinfo.value.stopped is False
    assert BASE in excinfo.value.detail
    assert _merge_head(base_worktree(repo)) == rev(repo, "m7/b")
    assert rev(repo, "master") == MASTER_BEFORE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bases.py -k "resolver_card or resumed_resolver" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.bases' has no attribute 'resolver_card_id'` and `TypeError: build() got an unexpected keyword argument 'resume_from'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/bases.py`, change the two import lines (41-42):

```python
from agent_manager.steps.worktree import GitError, ensure, run_git
from agent_manager.store import Checkpoint, Store
```

Add right after `BASES_STORY_TITLE = "Merged bases"` (line 48):

```python


def resolver_card_id(story_id: str) -> str:
    """The synthetic subtask a story's base resolver walks under: `base-<story id>`.

    One name for the card the resolver records and checkpoints under, and the
    one a milestone resume looks its checkpoint up by (card 54e4ec29).
    """
    return f"base-{story_id}"


def _bases_story() -> models.StoryRun:
    """The synthetic story every resolver subtask hangs from, recorded `started`."""
    return models.StoryRun(
        card_id=BASES_STORY_ID,
        title=BASES_STORY_TITLE,
        level=0,
        status="started",
    )
```

Add after `_missing_tips` (after line 85):

```python


def _in_progress_tip(worktree: Path, tips: list[str]) -> str | None:
    """The tip `worktree`'s unfinished merge is merging, or None.

    None when the worktree does not exist, holds no merge, or its MERGE_HEAD
    is none of `tips`. `rev-parse --verify --quiet MERGE_HEAD` exits 1 when
    there is no merge; any other failure propagates.
    """
    if not worktree.exists():
        return None
    try:
        head = run_git(
            ["-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"]
        ).strip()
    except GitError as error:
        if error.exit_code == 1:
            return None
        raise
    for tip in tips:
        sha = run_git(["-C", str(worktree), "rev-parse", "--verify", f"{tip}^{{commit}}"])
        if sha.strip() == head:
            return tip
    return None
```

Replace `_resolve_conflict` (lines 112-162) with:

```python
async def _resolve_conflict(
    *,
    story_id: str,
    tip: str,
    files: list[str],
    branch: str,
    base_branch: str,
    worktree: Path,
    repo_dir: Path,
    commands: list[str],
    allow_no_verification: bool,
    store: Store,
    run_id: str,
    runner_factory: cli.RunnerFactory,
    stop: StopSignal | None,
    resume_from: Checkpoint | None = None,
) -> SubtaskSummary:
    """Walk `workflow.integrate.INTEGRATE` once for one conflicting tip.

    Mirrors `integration._resolve_conflict`, awaited on the running loop and
    stop-aware. The synthetic subtask `base-<story id>` is recorded before the
    engine journals its first phase, because `store.rebuild_from_journal`
    refuses a phase whose subtask no earlier line created. The caller has
    already recorded the `bases` story. `resume_from` continues the walk from
    a saved checkpoint (card 54e4ec29); the engine then binds from the
    checkpoint's pool, so `tip` and `files` only name it.
    """
    card_id = resolver_card_id(story_id)
    subtask = models.SubtaskRun(
        card_id=card_id,
        branch=branch,
        base_branch=base_branch,
        status="started",
        worktree_path=worktree,
    )
    store.record_subtask(BASES_STORY_ID, subtask)
    runner = runner_factory(
        store=store, run_id=run_id, story_id=BASES_STORY_ID, card_id=card_id
    )
    return await runtime_engine.run_subtask_async(
        integrate_workflow.INTEGRATE,
        store,
        story_id=BASES_STORY_ID,
        subtask=subtask,
        repo_dir=repo_dir,
        commands=commands,
        extra_context={
            "merge_tip": tip,
            "conflict_files": list(files),
            **cli.gate_context(commands, allow_no_verification),
        },
        agent_runner=runner,
        stop=stop,
        resume_from=resume_from,
    )
```

Add after `_stopped_detail` (after line 185):

```python


def _resolver_failure(
    tip: str, branch: str, worktree: Path, summary: SubtaskSummary
) -> BaseFailed | None:
    """The `BaseFailed` a resolver's summary means, or None when it finished `done`."""
    if summary.status == "stopped":
        return BaseFailed(_stopped_detail(tip, branch, worktree, summary), stopped=True)
    if summary.status != "done":
        return BaseFailed(_resolver_detail(tip, branch, worktree, summary))
    return None
```

Replace `build` (lines 188-299) with:

```python
async def build(
    root: RootPlan,
    tips: list[str],
    *,
    repo_dir: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    store: Store | None,
    run_id: str | None,
    story_id: str | None,
    runner_factory: cli.RunnerFactory | None,
    stop: StopSignal | None,
    resume_from: Checkpoint | None = None,
) -> BaseResult:
    """Cut `root.branch` from `tips[0]`, merge every other tip, verify once.

    `tips` are the blockers' tips in `root.blockers` order. An existing branch
    or worktree is reused, and a tip already inside the base is reported as
    `already_merged`, so a relaunch re-merges nothing. A conflict needs
    `store`, `story_id` and `runner_factory` to reach the resolver, and fails
    for a human without them; `run_id` defaults to the store's, and `stop=None`
    means nothing can stop the resolver.

    `resume_from` (card 54e4ec29) is the resolver's checkpoint from an
    interrupted run. The resolver is continued from it first, before any
    tip is merged, because an unfinished merge it left would refuse every
    `merge_tip`. The tip it was resolving is the one `MERGE_HEAD` names; a
    tip it finished counts as `merged` and `resolved`. With no merge in
    progress (it was parked after committing) the tip is unknown, so it is
    reported as the loop finds it, `already_merged`.
    """
    tips = list(tips)
    if not tips:
        raise ValueError(
            f"bases.build needs at least one blocker tip to build {root.branch}, got none"
        )
    repo = Path(repo_dir).resolve()
    worktree = cli.worktree_for(repo, root.branch)
    suite = list(commands)

    missing = await asyncio.to_thread(_missing_tips, repo, tips)
    if missing:
        raise BaseFailed(
            f"cannot build the merged base {root.branch}: blocker tip "
            f"{', '.join(missing)} does not exist in {repo}"
        )

    await asyncio.to_thread(ensure, root.branch, tips[0], worktree, repo)

    merged: list[str] = []
    already_merged: list[str] = []
    resolved: list[str] = []
    story_recorded = False
    if resume_from is not None:
        if store is None or story_id is None or runner_factory is None:
            raise BaseFailed(
                f"cannot continue the resolver of the merged base {root.branch}: "
                "continuing it needs a store, a story id and a runner factory; the "
                f"worktree is left as it is in {worktree}"
            )
        resumed_tip = await asyncio.to_thread(_in_progress_tip, worktree, tips[1:])
        label = resumed_tip if resumed_tip is not None else "the tip it was resolving"
        store.record_story(_bases_story())
        story_recorded = True
        summary = await _resolve_conflict(
            story_id=story_id,
            tip=label,
            files=[],
            branch=root.branch,
            base_branch=tips[0],
            worktree=worktree,
            repo_dir=repo,
            commands=suite,
            allow_no_verification=allow_no_verification,
            store=store,
            run_id=run_id if run_id is not None else store.run_id,
            runner_factory=runner_factory,
            stop=stop,
            resume_from=resume_from,
        )
        failure = _resolver_failure(label, root.branch, worktree, summary)
        if failure is not None:
            raise failure
        if resumed_tip is not None:
            resolved.append(resumed_tip)

    for tip in tips[1:]:
        try:
            result = await asyncio.to_thread(
                merge_tip, repo, worktree, root.branch, tips[0], tip, git_runner=run_git
            )
        except MergeInProgressError as error:
            # The error already says the earlier conflict was never resolved
            # and a human must finish it in the worktree.
            raise BaseFailed(
                f"cannot build the merged base {root.branch}: {error}"
            ) from error
        if result["conflict"]:
            files = [str(name) for name in result["files"]]
            if store is None or story_id is None or runner_factory is None:
                raise BaseFailed(
                    f"conflict merging {tip} into the merged base {root.branch} "
                    f"({', '.join(files)}): no resolver is available, so the merge "
                    f"is left in progress in {worktree} for a human"
                )
            if not story_recorded:
                store.record_story(_bases_story())
                story_recorded = True
            summary = await _resolve_conflict(
                story_id=story_id,
                tip=tip,
                files=files,
                branch=root.branch,
                base_branch=tips[0],
                worktree=worktree,
                repo_dir=repo,
                commands=suite,
                allow_no_verification=allow_no_verification,
                store=store,
                run_id=run_id if run_id is not None else store.run_id,
                runner_factory=runner_factory,
                stop=stop,
            )
            failure = _resolver_failure(tip, root.branch, worktree, summary)
            if failure is not None:
                raise failure
            resolved.append(tip)
        # A tip the resumed resolver finished is contained by now, but this
        # run merged it: it is reported merged, not already merged.
        if result["already_merged"] and tip not in resolved:
            already_merged.append(tip)
        else:
            merged.append(tip)

    verdict = await asyncio.to_thread(
        _verify, suite, allow_no_verification, root.branch, worktree
    )
    if verdict is not None:
        raise BaseFailed(verdict)

    return BaseResult(
        branch=root.branch,
        merged=merged,
        already_merged=already_merged,
        resolved=resolved,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bases.py -v`
Expected: PASS (new tests and every existing `tests/test_bases.py` test, including `test_every_git_and_verify_call_runs_off_the_event_loop_thread`)

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/bases.py tests/test_bases.py
git commit -m "feat(bases): continue a base resolver from its checkpoint"
```

---

### Task 3: Resume helpers in `orchestrate`

**Files:**
- Modify: `src/agent_manager/orchestrate.py:44-57` (imports), add helpers after `refresh_git` (after line 485)
- Test: `tests/test_orchestrate.py` (imports at lines 40-47; new section at the end of the file)

**Interfaces:**
- Consumes: `Store.latest_checkpoint`, `Store.latest_turn_checkpoint` (Task 1), `bases.resolver_card_id` (Task 2), `runtime_engine.pending_phase`, `cli.orphan_attempts`, `cli.CheckpointMismatchError`, `cli.NotResumableError`, `cli.UnknownRunError`, `store.open_db`, `store.load_run`.
- Produces (all in `agent_manager.orchestrate`):
  - `REOPENED_STATUSES: tuple[str, ...] = ("stopped", "escalated", "started")`
  - `resumable_milestone_run(root: Path, run_id: str) -> models.Run`
  - `find_run_milestone(roots: Sequence[models.CardNode] | None, run_id: str) -> models.CardNode`
  - `open_cards(stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str) -> list[tuple[str, Workflow]]`
  - `resume_point(store: Store, card_id: str, workflow: Workflow) -> Checkpoint | None`
  - `resume_checkpoints(store: Store, cards: Sequence[tuple[str, Workflow]]) -> dict[str, Checkpoint]`
  - `reopen_rows(store: Store, run: models.Run, open_card_ids: set[str]) -> None`

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, add to the imports (after line 46):

```python
from agent_manager.workflow import integrate as integrate_workflow
```

Append at the end of `tests/test_orchestrate.py`:

```python
# ── milestone-wide resume helpers (card 54e4ec29) ───────────────────────────

RESUME_RUN_ID = "20260924T120000Z-00000009"


def _resume_root(tmp_path: Path, monkeypatch) -> Path:
    """A project root with its projection under tmp_path; no git, no board."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    return root.resolve()


def _record_resume_run(
    root: Path, run_id: str = RESUME_RUN_ID, *, workflow: str = "milestone", status: str = "escalated"
) -> None:
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=root,
                base_branch="main",
                branch_prefix=PREFIX,
                status=status,
                config=models.RunConfig(max_concurrent_stories=3),
            )
        )
    finally:
        opened.close()


def _save(
    store: store_module.Store,
    card_id: str,
    reason: str,
    *,
    phase: str | None = None,
    workflow: Workflow = task_workflow.TASK,
    digest: str | None = None,
) -> store_module.Checkpoint:
    """One checkpoint of `card_id`; `phase` is the turn in flight, None for a row holding no turn."""
    return store.save_checkpoint(
        card_id,
        workflow=workflow.name,
        digest=workflow.digest() if digest is None else digest,
        reason=reason,
        agent={
            "current_turn": None if phase is None else {"kwargs": {"phase": phase, "loop": 0}},
            "queue": [],
        },
        saved_at=EARLIER,
    )


def test_a_resumable_milestone_run_is_the_recorded_run(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root)

    run = orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)

    assert (run.id, run.workflow, run.status) == (RESUME_RUN_ID, "milestone", "escalated")
    assert (run.base_branch, run.branch_prefix) == ("main", PREFIX)
    assert run.config.max_concurrent_stories == 3


def test_a_finished_milestone_run_is_refused_with_the_relaunch_remedy(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root, status="done")

    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)

    assert str(caught.value) == (
        f"run {RESUME_RUN_ID} finished; start new work with am run --milestone"
    )


def test_a_task_run_and_an_unknown_run_are_not_milestone_resumes(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root, workflow="task", status="started")

    with pytest.raises(cli.NotResumableError, match="'task'"):
        orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)
    with pytest.raises(cli.UnknownRunError, match="no-such-run"):
        orchestrate.resumable_milestone_run(root, "no-such-run")


def test_a_resumed_run_names_its_milestone_by_the_short_id_in_its_run_id():
    wanted = models.CardNode(id=_plan_id(9), title="Milestone 9", status="todo")
    other = models.CardNode(id=_plan_id(8), title="Milestone 8", status="todo")

    assert orchestrate.find_run_milestone([other, wanted], RESUME_RUN_ID) is wanted
    with pytest.raises(cli.NotResumableError, match="00000007"):
        orchestrate.find_run_milestone([other, wanted], "20260924T120000Z-00000007")


def _resume_stories() -> list[census.StoryPlan]:
    """A: 11 done, 12 and 13 open. B: 21 open. C on A and B: 31 open, a merged
    root. D on A and B is closed, so its base is nobody's to build."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12), _plan_subtask(13)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id, b.id])
    d = _plan_story(4, [_plan_subtask(41, "done")], status="done", blocked_by=[a.id, b.id])
    return [a, b, c, d]


def test_the_open_cards_are_the_remaining_subtasks_and_every_open_merged_roots_resolver():
    cards = orchestrate.open_cards(_resume_stories(), branch_prefix=PREFIX, base_branch="main")

    assert [(card_id, workflow.name) for card_id, workflow in cards] == [
        (_plan_id(12), "task"),
        (_plan_id(13), "task"),
        (_plan_id(21), "task"),
        (_plan_id(31), "task"),
        (bases.resolver_card_id(_plan_id(3)), "integrate"),
    ]


def test_each_open_card_resumes_from_its_newest_row_or_the_turn_it_failed_in(
    tmp_path, monkeypatch
):
    """12 escalated with no turn left: rewound to its failed `review` turn. 13
    parked: that row. 21's newest row is `done`, under another digest even
    (Review Focus 2): nothing, and no refusal. 31 has none. C's resolver:
    its parked INTEGRATE row."""
    root = _resume_root(tmp_path, monkeypatch)
    base_c = bases.resolver_card_id(_plan_id(3))
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, _plan_id(12), "turn", phase="implement")
        failed = _save(opened, _plan_id(12), "turn", phase="review")
        _save(opened, _plan_id(12), "escalated")
        parked = _save(opened, _plan_id(13), "parked", phase="validate_spec")
        _save(opened, _plan_id(21), "turn", phase="plan")
        _save(opened, _plan_id(21), "done", digest="saved-under-another-task")
        resolver = _save(
            opened, base_c, "parked", phase="verify", workflow=integrate_workflow.INTEGRATE
        )
        cards = orchestrate.open_cards(_resume_stories(), branch_prefix=PREFIX, base_branch="main")

        found = orchestrate.resume_checkpoints(opened, cards)
    finally:
        opened.close()

    assert found == {_plan_id(12): failed, _plan_id(13): parked, base_c: resolver}


def test_a_subtask_saved_under_another_task_refuses_naming_the_card_and_both_digests(
    tmp_path, monkeypatch
):
    root = _resume_root(tmp_path, monkeypatch)
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, _plan_id(13), "parked", phase="plan", digest="saved-under-another-task")
        cards = orchestrate.open_cards(_resume_stories(), branch_prefix=PREFIX, base_branch="main")

        with pytest.raises(cli.CheckpointMismatchError) as caught:
            orchestrate.resume_checkpoints(opened, cards)
    finally:
        opened.close()

    message = str(caught.value)
    assert message.startswith("workflow changed since checkpoint")
    assert _plan_id(13) in message
    assert "saved-under-another-task" in message
    assert task_workflow.TASK.digest() in message


def test_a_resolver_is_judged_against_integrate_not_task(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    base_c = bases.resolver_card_id(_plan_id(3))
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, base_c, "parked", phase="verify", workflow=task_workflow.TASK)
        cards = orchestrate.open_cards(_resume_stories(), branch_prefix=PREFIX, base_branch="main")

        with pytest.raises(cli.CheckpointMismatchError) as caught:
            orchestrate.resume_checkpoints(opened, cards)
    finally:
        opened.close()

    assert base_c in str(caught.value)
    assert integrate_workflow.INTEGRATE.digest() in str(caught.value)


def _dispatch(root: Path) -> models.Dispatch:
    return models.Dispatch(
        harness="fake",
        model="fake",
        role="reviewer",
        cwd=root,
        prompt_path=root / "prompt.txt",
        result_path=root / "result.json",
    )


def test_reopening_marks_orphans_harness_error_and_open_rows_started(tmp_path, monkeypatch):
    root = _resume_root(tmp_path, monkeypatch)
    statuses = {
        "a1": "done",
        "a2": "escalated",
        "a3": "stopped",
        "a4": "started",
        "a5": "pending",
        "closed": "escalated",
    }
    _record_resume_run(root)
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        opened.record_story(models.StoryRun(card_id="story-a", title="A", level=0, status="escalated"))
        for card, status in statuses.items():
            opened.record_subtask(
                "story-a",
                models.SubtaskRun(card_id=card, branch=f"m3/{card}", base_branch="main", status=status),
            )
        opened.record_phase("story-a", "a2", models.PhaseRun(name="review", kind="agent", status="started"))
        opened.record_attempt(
            "story-a", "a2", "review", models.Attempt(n=1, dispatch=_dispatch(root), status="started")
        )
        run = opened.load_run(RESUME_RUN_ID)

        orchestrate.reopen_rows(opened, run, {"a2", "a3", "a4", "a5"})

        after = opened.load_run(RESUME_RUN_ID)
    finally:
        opened.close()

    [story] = after.stories
    assert {subtask.card_id: subtask.status for subtask in story.subtasks} == {
        "a1": "done",
        "a2": "started",
        "a3": "started",
        "a4": "started",
        "a5": "pending",
        "closed": "escalated",
    }
    [review] = [subtask for subtask in story.subtasks if subtask.card_id == "a2"][0].phases
    assert [attempt.status for attempt in review.attempts] == ["harness_error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "resumable or finished_milestone or not_milestone_resumes or short_id_in_its_run_id or open_cards or open_card_resumes or another_task_refuses or judged_against_integrate or reopening" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'resumable_milestone_run'` (and likewise for `find_run_milestone`, `open_cards`, `resume_checkpoints`, `reopen_rows`)

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`, replace the import block lines 54-57 with:

```python
from agent_manager import bases, board, census, cli, dag, integration, models
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps import rollup, worktree
from agent_manager.store import Checkpoint, Store, load_run, open_db
from agent_manager.workflow import integrate as integrate_workflow
from agent_manager.workflow import task as task_workflow
from agent_manager.workflow.phases import Workflow
```

Add after `refresh_git` (after line 485):

```python


REOPENED_STATUSES = ("stopped", "escalated", "started")
"""The row statuses a resume records `started` again (spec, point 2)."""


def resumable_milestone_run(root: Path, run_id: str) -> models.Run:
    """The recorded milestone run `run_id`, or the refusal that says why not.

    Read-only through the free `open_db` / `load_run`, like `cli.resume_run`:
    `Store.open` would construct a `Journal`. An unknown run, a run of
    another workflow, and a `done` run are refused (card 54e4ec29).
    """
    conn = open_db(root)
    try:
        run = load_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise cli.UnknownRunError(
            f"run {run_id!r} is not in the projection for {root}"
            " (`agent-manager runs` lists the ones that are)"
        )
    if run.workflow != MILESTONE_WORKFLOW:
        raise cli.NotResumableError(
            f"run {run_id!r} is a {run.workflow!r} run, not a {MILESTONE_WORKFLOW!r} run"
        )
    if run.status == "done":
        raise cli.NotResumableError(
            f"run {run.id} finished; start new work with am run --milestone"
        )
    return run


def find_run_milestone(
    roots: Sequence[models.CardNode] | None, run_id: str
) -> models.CardNode:
    """The one root card whose short id ends `run_id`.

    `cli.mint_run_id` builds a milestone run's id as `<timestamp>-<short
    milestone id>`, and `models.Run` records no milestone id of its own, so
    the id is how a resume finds its milestone. A title edit cannot break it.
    """
    short = run_id.rsplit("-", 1)[-1]
    matches = [node for node in roots or [] if dag.short_id(node.id) == short]
    if len(matches) != 1:
        raise cli.NotResumableError(
            f"run {run_id!r} belongs to milestone {short}, and {len(matches)} root"
            " cards on the board have that short id"
        )
    return matches[0]


def open_cards(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[tuple[str, Workflow]]:
    """Every card a resume may continue, with the workflow its checkpoint must match.

    Each remaining subtask under `TASK`, then, for a story that is not closed
    and roots on a merged base, its resolver `base-<story id>` under
    `INTEGRATE`. Census order. `dag.assert_no_blocker_cycles` must have run.
    """
    stories = list(stories)
    by_id = {story.id: story for story in stories}
    cards: list[tuple[str, Workflow]] = []
    for story in stories:
        for subtask in dag.remaining_subtasks(story):
            cards.append((subtask.id, task_workflow.TASK))
        root_plan = dag.story_root(story, by_id, branch_prefix, base_branch)
        if root_plan.kind == "merged" and not dag.is_story_closed(story):
            cards.append((bases.resolver_card_id(story.id), integrate_workflow.INTEGRATE))
    return cards


def _refuse_changed_workflow(checkpoint: Checkpoint, workflow: Workflow, run_id: str) -> None:
    """`cli.CheckpointMismatchError` when `checkpoint` was saved under another digest.

    Worded like `cli.checkpoint_resume_phase`'s refusal, with the milestone
    remedy.
    """
    digest = workflow.digest()
    if checkpoint.digest != digest:
        raise cli.CheckpointMismatchError(
            f"workflow changed since checkpoint: checkpoint #{checkpoint.seq} of card"
            f" {checkpoint.card_id} in run {run_id!r} was saved under digest"
            f" {checkpoint.digest}, but workflow {workflow.name!r} now has digest"
            f" {digest}; start new work with am run --milestone"
        )


def resume_point(store: Store, card_id: str, workflow: Workflow) -> Checkpoint | None:
    """The checkpoint a resume continues `card_id` from, None to start it fresh, or a refusal.

    The newest row of `card_id` in this store's run decides. None, or `done`
    (only a board write was lost), starts the card fresh. Any other newest row
    is judged against `workflow`'s digest and refused on a mismatch. A row
    that holds a turn is continued as is; one that does not -- a phase
    escalation -- is rewound to the card's newest `turn` row, the turn the
    failing phase ran in, judged the same way.
    """
    newest = store.latest_checkpoint(card_id)
    if newest is None or newest.reason == "done":
        return None
    _refuse_changed_workflow(newest, workflow, store.run_id)
    if runtime_engine.pending_phase(newest) is not None:
        return newest
    turn = store.latest_turn_checkpoint(card_id)
    if turn is None:
        return None
    _refuse_changed_workflow(turn, workflow, store.run_id)
    return turn


def resume_checkpoints(
    store: Store, cards: Sequence[tuple[str, Workflow]]
) -> dict[str, Checkpoint]:
    """`resume_point` for every open card, keyed by card id, only where there is one.

    Reads only, so a refusal on any card leaves everything as it was: the
    whole resume is refused (spec, Error paths).
    """
    found: dict[str, Checkpoint] = {}
    for card_id, workflow in cards:
        checkpoint = resume_point(store, card_id, workflow)
        if checkpoint is not None:
            found[card_id] = checkpoint
    return found


def reopen_rows(store: Store, run: models.Run, open_card_ids: set[str]) -> None:
    """Mark every orphan attempt `harness_error`, then reopen the open rows.

    `run` is the tree as the interrupted run left it. An orphan is
    `cli.orphan_attempts`' in-flight attempt, marked as `cli`'s
    `_resume_from_checkpoint` marks it. A subtask or resolver row of an open
    card that is stopped, escalated or started is recorded `started`.
    """
    for story in run.stories:
        for subtask in story.subtasks:
            for phase, attempt in cli.orphan_attempts(subtask):
                store.record_attempt(
                    story.card_id,
                    subtask.card_id,
                    phase.name,
                    attempt.model_copy(update={"status": "harness_error"}),
                )
            if subtask.card_id in open_card_ids and subtask.status in REOPENED_STATUSES:
                store.record_subtask(
                    story.card_id, subtask.model_copy(update={"status": "started"})
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: PASS (new tests, `test_the_engine_selecting_modules_never_import_pygents`, `test_only_orchestrate_imports_grafo`, and every existing test)

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): load, validate and reopen a milestone run for resume"
```

---

### Task 4: `run_milestone(resume_run_id=...)`

**Files:**
- Modify: `src/agent_manager/orchestrate.py:47` (`field` import), `:362-381` (`SupervisorPlan`), `:384-411` (`supervisor_plan`), `:570-604` (`build_merged_base`), `:680-691` (`base_only_lane`), `:708-916` (`lane`: base call 815-826 and checkpoint lookup 850-855), `:1019-1162` (`run_milestone`)
- Test: `tests/test_orchestrate.py` (`FakeBases` at 2764-2815; new tests appended after Task 3's section)

**Interfaces:**
- Consumes: everything Task 3 produces; `bases.build(..., resume_from=)` and `bases.resolver_card_id` (Task 2).
- Produces:
  - `SupervisorPlan.checkpoints: Mapping[str, Checkpoint]` and `SupervisorPlan.resuming: bool` (both defaulted).
  - `supervisor_plan(stories, levels, rows, *, branch_prefix, base_branch, checkpoints: Mapping[str, Checkpoint] | None = None) -> SupervisorPlan`.
  - `build_merged_base(..., stop, resume_from: Checkpoint | None = None) -> None`.
  - `run_milestone(milestone: str | None, *, repo_dir, base_branch: str | None = None, branch_prefix: str | None = None, commands=(), allow_no_verification=False, runner_factory=None, driver=None, clock=_utcnow, max_concurrent=1, resume_run_id: str | None = None) -> dict[str, Any]`; every payload gains `"resumed": True` on the resume path.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, replace `FakeBases` (lines 2764-2815) so it also takes and records `resume_from`:

```python
@dataclass
class FakeBases:
    """Stands in for `bases.build`, which the lane reads off `bases` at call time.

    Every call is recorded. `gates[story]` is awaited first with the call's
    `stop` (Events and Barriers, never sleeps). `outcomes[story]` is an
    exception to raise; with none the base counts as built and a
    `BaseResult` naming `root.branch` comes back. It touches no git: a
    `FakeDriver` never needs the branch to exist. `resumed[story]` is the
    `resume_from` it was handed, `_ABSENT` when none was (card 54e4ec29).
    """

    outcomes: dict[str, BaseException] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    resumed: dict[str, Any] = field(default_factory=dict)

    async def __call__(
        self,
        root,
        tips,
        *,
        repo_dir,
        commands,
        allow_no_verification,
        store,
        run_id,
        story_id,
        runner_factory,
        stop,
        resume_from=_ABSENT,
    ) -> bases.BaseResult:
        self.resumed[story_id] = resume_from
        self.calls.append(
            {
                "root": root,
                "tips": list(tips),
                "repo_dir": repo_dir,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "store": store,
                "run_id": run_id,
                "story_id": story_id,
                "runner_factory": runner_factory,
                "stop": stop,
            }
        )
        gate = self.gates.get(story_id)
        if gate is not None:
            await gate(stop)
        error = self.outcomes.get(story_id)
        if error is not None:
            raise error
        return bases.BaseResult(
            branch=root.branch, merged=list(tips[1:]), already_merged=[], resolved=[]
        )
```

Append after Task 3's section at the end of `tests/test_orchestrate.py`:

```python
# ── run_milestone(resume_run_id=...) (card 54e4ec29) ────────────────────────


def _resume(project: Path, run_id: str, driver: Any, **overrides: Any) -> dict[str, Any]:
    """`run_milestone` continuing `run_id`: no milestone, prefix, base or bound given."""
    kwargs: dict[str, Any] = {"repo_dir": project, "driver": driver, "resume_run_id": run_id}
    kwargs.update(overrides)
    return orchestrate.run_milestone(None, **kwargs)


def _plant_integrate(
    project: Path, run_id: str, story_id: str, reason: str, *, digest: str | None = None
) -> store_module.Checkpoint:
    """One `INTEGRATE` checkpoint of `story_id`'s resolver, saved by `run_id`."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        return opened.save_checkpoint(
            bases.resolver_card_id(story_id),
            workflow=integrate_workflow.INTEGRATE.name,
            digest=integrate_workflow.INTEGRATE.digest() if digest is None else digest,
            reason=reason,
            agent={"current_turn": None, "queue": [{"kwargs": {"phase": "verify", "loop": 0}}]},
            saved_at=EARLIER,
        )
    finally:
        opened.close()


def _record_bounds(monkeypatch) -> list[int]:
    """Wrap `orchestrate.supervise`, which `run_milestone` reads at call time,
    and record the lane bound it is given."""
    bounds: list[int] = []
    real = orchestrate.supervise

    async def recording(plan, **kwargs):
        bounds.append(kwargs["max_concurrent"])
        return await real(plan, **kwargs)

    monkeypatch.setattr(orchestrate, "supervise", recording)
    return bounds


def _run_ids(project: Path) -> list[str]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [row["id"] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _checkpoint_rows(project: Path) -> list[tuple]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT run_id, card_id, seq, reason, digest FROM checkpoints"
                " ORDER BY run_id, card_id, seq"
            )
        ]
    finally:
        conn.close()


def _runs_tree() -> dict[str, bytes]:
    """Every path under the data dir's `runs`, with file contents: the journals included."""
    root = paths.data_dir() / "runs"
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else b"<dir>"
        for path in sorted(root.rglob("*"))
    }


def _never_consulted(store, card_id):
    pytest.fail("a resume consulted the lenient relaunch lookup cli.continuable_checkpoint")


@requires_git
@requires_brd
def test_a_resume_reuses_the_recorded_settings_and_hands_each_open_checkpoint_on(
    project, fake_bases, monkeypatch
):
    """Spec test 2: no prefix, base or bound is given, yet the subtasks stack
    on `main` under `PREFIX` and the tree runs three lanes; a1 continues from
    its checkpoint, C's resolver checkpoint reaches `bases.build`, and the
    lenient relaunch lookup is never read."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}), max_concurrent=3)
    assert first["escalated"] is True, first
    run_id = first["run_id"]
    turn = _plant(project, run_id, a1, "turn", queue=("review",))
    resolver = _plant_integrate(project, run_id, story_c, "parked")
    bounds = _record_bounds(monkeypatch)
    monkeypatch.setattr(cli, "continuable_checkpoint", _never_consulted)
    driver = CheckpointDriver()

    result = _resume(project, run_id, driver)

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert result["run_id"] == run_id
    assert _run_ids(project) == [run_id]
    assert bounds == [3]
    by_card = {call["card"]: call for call in driver.calls}
    assert by_card[a1]["base"] == "main"
    assert by_card[a1]["branch"] == _branch(project, a1)
    root_plan = _root_plan(project, shape["milestone"], story_c)
    assert by_card[c1]["base"] == root_plan.branch
    got = driver.resumed[a1]
    assert (got.run_id, got.card_id, got.seq, got.reason) == (run_id, a1, turn.seq, "turn")
    assert driver.resumed[b1] is _ABSENT
    assert driver.resumed[c1] is _ABSENT
    base_got = fake_bases.resumed[story_c]
    assert (base_got.card_id, base_got.seq, base_got.reason) == (
        bases.resolver_card_id(story_c),
        resolver.seq,
        "parked",
    )
    run = _load(project, run_id)
    assert (run.status, run.branch_prefix, run.base_branch) == ("done", PREFIX, "main")
    assert run.config.max_concurrent_stories == 3


@requires_git
@requires_brd
def test_a_stale_resolver_checkpoint_refuses_the_whole_resume_and_writes_nothing(
    project, fake_bases, monkeypatch
):
    """Spec test 4: one resolver saved under another INTEGRATE refuses the
    whole resume before anything is driven, recorded or fetched."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}), max_concurrent=3)
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",))
    _plant_integrate(project, run_id, story_c, "parked", digest="saved-under-another-integrate")
    before = (
        _runs_tree(),
        _statuses(_load(project, run_id)),
        _checkpoint_rows(project),
        _local_branches(project),
    )
    git_calls = _record_git(monkeypatch)
    driver = CheckpointDriver()

    with pytest.raises(cli.CheckpointMismatchError) as caught:
        _resume(project, run_id, driver)

    message = str(caught.value)
    assert message.startswith("workflow changed since checkpoint")
    assert bases.resolver_card_id(story_c) in message
    assert "saved-under-another-integrate" in message
    assert integrate_workflow.INTEGRATE.digest() in message
    assert driver.calls == [] and fake_bases.calls == []
    assert git_calls == []
    assert (
        _runs_tree(),
        _statuses(_load(project, run_id)),
        _checkpoint_rows(project),
        _local_branches(project),
    ) == before


@requires_git
@requires_brd
def test_a_merged_base_from_the_interrupted_run_is_reused_and_not_merged_again(project):
    """Spec test 7, on the real `bases.build`: A and B finished and C's base
    was merged before c1 escalated. The resume drives c1 only, on the same
    base commit, and neither the base nor `main` moves."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (c1,) = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    first = _run(
        project,
        shape["milestone"],
        BranchingDriver(outcomes={c1: ("review", "boom")}),
        commands=[PASS_CMD],
    )
    assert first["escalated"] is True, first
    assert first["bases"] == [_bases_entry(story_c, root_plan)]
    base_sha = _sha(project, root_plan.branch)
    main_sha = _sha(project, "main")
    driver = BranchingDriver()

    result = _resume(project, first["run_id"], driver, commands=[PASS_CMD])

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert [call["card"] for call in driver.calls] == [c1]
    assert driver.calls[0]["base"] == root_plan.branch
    assert result["completed"] == [c1]
    assert result["bases"] == [_bases_entry(story_c, root_plan)]
    assert _sha(project, root_plan.branch) == base_sha
    assert _sha(project, "main") == main_sha


@requires_git
@requires_brd
def test_a_card_finished_by_hand_since_the_interrupt_is_neither_checked_nor_driven(project):
    """Review Focus 1: a1's checkpoint is stale, but a human finished a1 on the
    board, so it is not open: no refusal, and only a2 is driven."""
    shape = _milestone(project, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",), digest="saved-under-another-task")
    rollup.set_status(a1, "done", repo_dir=project)
    driver = CheckpointDriver()

    result = _resume(project, run_id, driver)

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert [call["card"] for call in driver.calls] == [a2]
    assert result["completed"] == [a2]


@requires_git
@requires_brd
def test_a_resume_after_an_integrate_escalation_retries_integrate(project, integrate_recorder):
    """Review Focus 4: nothing is left to drive, so the resume runs no lane
    and retries Integrate under the same run id."""
    shape = _milestone(project, {"A": 1})
    integrate_recorder.outcome = integration.IntegrateEscalation(
        story=None, files=[], detail="the suite is red"
    )
    first = _run(project, shape["milestone"], BranchingDriver())
    assert first["escalated"] is True, first
    integrate_recorder.outcome = None
    driver = BranchingDriver()

    result = _resume(project, first["run_id"], driver)

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert result["completed"] == []
    assert driver.calls == []
    assert [call["run_id"] for call in integrate_recorder.calls] == [first["run_id"]] * 2
    assert _load(project, first["run_id"]).status == "done"


@requires_git
@requires_brd
def test_an_escalated_resume_still_says_it_resumed(project):
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))

    again = _resume(project, first["run_id"], FakeDriver(outcomes={a1: ("review", "still")}))

    assert again["escalated"] is True
    assert again["resumed"] is True
    assert again["run_id"] == first["run_id"]
    assert again["detail"] == "still"


def test_a_fresh_run_without_a_prefix_is_refused_before_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(board, "roots", lambda **kwargs: pytest.fail("the board was read"))

    with pytest.raises(ValueError, match="branch prefix"):
        orchestrate.run_milestone("Milestone 3", repo_dir=tmp_path, base_branch="main")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "reuses_the_recorded or stale_resolver or not_merged_again or finished_by_hand or retries_integrate or still_says_it_resumed or without_a_prefix" -v`
Expected: FAIL with `TypeError: run_milestone() got an unexpected keyword argument 'resume_run_id'` (and `TypeError: run_milestone() missing 1 required keyword-only argument: 'branch_prefix'` for the last test)

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`:

Line 47:

```python
from dataclasses import dataclass, field, replace
```

Replace `SupervisorPlan` (lines 362-381) with:

```python
@dataclass(frozen=True)
class SupervisorPlan:
    """What `supervise` schedules (T1). Internal state, so a dataclass.

    `stories` is every census story, done ones included, in census order: each
    becomes a node. `roots` and `tips` cover all of them. `levels` are the
    pending stories' waves from `plan_levels`, and `rows` their store rows from
    `record_plan`. `resuming` is set on a milestone resume (card 54e4ec29),
    whose validated checkpoints, keyed by card id -- subtasks and
    `base-<story>` resolvers -- are `checkpoints`.
    """

    stories: tuple[census.StoryPlan, ...]
    levels: tuple[tuple[PlannedStory, ...], ...]
    roots: dict[str, dag.RootPlan]
    tips: dict[str, str]
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]
    checkpoints: Mapping[str, Checkpoint] = field(default_factory=dict)
    resuming: bool = False

    @property
    def planned(self) -> dict[str, PlannedStory]:
        """The pending stories by id, in wave order."""
        return {planned.story.id: planned for level in self.levels for planned in level}
```

Replace `supervisor_plan` (lines 384-411) with:

```python
def supervisor_plan(
    stories: Sequence[census.StoryPlan],
    levels: Sequence[Sequence[PlannedStory]],
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]],
    *,
    branch_prefix: str,
    base_branch: str,
    checkpoints: Mapping[str, Checkpoint] | None = None,
) -> SupervisorPlan:
    """Every census story's root and tip beside the pending waves and their rows.

    Pure. `plan_levels` has already run the cycle check, so this derives
    geometry and refuses nothing. `checkpoints` is given only on a resume,
    and marks the plan `resuming` even when it is empty.
    """
    stories = tuple(stories)
    by_id = {story.id: story for story in stories}
    return SupervisorPlan(
        stories=stories,
        levels=tuple(tuple(level) for level in levels),
        roots={
            story.id: dag.story_root(story, by_id, branch_prefix, base_branch)
            for story in stories
        },
        tips={
            story.id: dag.story_tip(story, by_id, branch_prefix, base_branch)
            for story in stories
        },
        rows=rows,
        checkpoints=dict(checkpoints or {}),
        resuming=checkpoints is not None,
    )
```

Replace `build_merged_base` (lines 570-604) with:

```python
async def build_merged_base(
    story: census.StoryPlan,
    root_plan: dag.RootPlan,
    tips: Sequence[str],
    *,
    store: Store,
    run_id: str,
    root: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    stop: StopSignal,
    resume_from: Checkpoint | None = None,
) -> None:
    """Await `bases.build` for one merged-root story (supervisor-tree §5).

    `tips` are the blockers' tips, already resolved by the caller in
    `root_plan.blockers` order (`blocker_tips`). `bases.build` is read off its
    module at call time so a test can replace it. A `None` factory is
    production's, `cli.default_runner_factory`, read at call time as
    Integrate reads it, so a conflicting tip reaches the resolver instead of
    failing for a human. `resume_from` is the resolver's checkpoint on a
    resume (card 54e4ec29), passed only when there is one.
    """
    factory = cli.default_runner_factory if runner_factory is None else runner_factory
    extra: dict[str, Any] = {}
    if resume_from is not None:
        extra["resume_from"] = resume_from
    await bases.build(
        root_plan,
        list(tips),
        repo_dir=root,
        commands=list(commands),
        allow_no_verification=allow_no_verification,
        store=store,
        run_id=run_id,
        story_id=story.id,
        runner_factory=factory,
        stop=stop,
        **extra,
    )
```

In `base_only_lane`, replace the `build_merged_base` call (lines 680-691) with:

```python
            await build_merged_base(
                story,
                root_plan,
                tips,
                store=store,
                run_id=run_id,
                root=root,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                stop=stop,
                resume_from=plan.checkpoints.get(bases.resolver_card_id(story.id)),
            )
```

In `lane`, replace the `build_merged_base` call (lines 815-826) with:

```python
                    await build_merged_base(
                        story,
                        root_plan,
                        tips,
                        store=store,
                        run_id=run_id,
                        root=root,
                        commands=commands,
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        stop=stop,
                        resume_from=plan.checkpoints.get(bases.resolver_card_id(story.id)),
                    )
```

In `lane`, replace lines 850-855 with:

```python
                # Relaunch continuation (card 02890d5d) is lenient and reads
                # across runs; a resume (card 54e4ec29) hands on exactly the
                # checkpoints `resume_checkpoints` already validated. Either
                # way the keyword is passed only when there is a row, so a
                # driver that predates it works.
                extra: dict[str, Any] = {}
                if plan.resuming:
                    checkpoint = plan.checkpoints.get(subtask.id)
                else:
                    checkpoint = cli.continuable_checkpoint(store, subtask.id)
                if checkpoint is not None:
                    extra["resume_from"] = checkpoint
```

Also append to `lane`'s docstring (before its closing `"""` at line 759):

```text

    On a resume (`plan.resuming`, card 54e4ec29) the subtask's checkpoint is
    `plan.checkpoints`' and the lenient relaunch lookup is never read; a
    merged base gets its resolver's checkpoint the same way.
```

Replace `run_milestone` (lines 1019-1162) with:

```python
def run_milestone(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: cli.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    resume_run_id: str | None = None,
) -> dict[str, Any]:
    """Drive every remaining subtask of `milestone` as a grafo tree, and report (O6, T1-T6).

    `milestone` is a card id or a title needle (O1). Everything that can refuse,
    `max_concurrent < 1` included, runs before the store is opened. Then one
    `milestone` run is recorded with its whole plan `pending`, and
    `asyncio.run(supervise(...))` runs every story the moment its blockers
    succeeded, at most `max_concurrent` at once. A subtask already `done` on
    the board is never driven, but its branch still anchors the next
    subtask's base. The card and its story are read fresh from the board
    before each subtask. The default driver is `cli.drive_subtask_async`,
    read at call time.

    The first escalation triggers the run's `StopSignal`: running subtasks
    park at their next phase boundary and are recorded `stopped`, a lane
    between subtasks or waiting for a slot ends `stopped` without driving
    anything more, and grafo starts no dependent of a failed lane, so those
    stories stay `pending`.

    When every lane finished clean -- or none had anything to run --
    Integrate folds every story tip into `<branch_prefix>-integrate` before
    the run is recorded. Success records `done` and adds `integrated`; an
    Integrate escalation records `escalated` and returns
    `integrate_escalated_payload`. An exception from Integrate propagates and
    the run is never recorded `done`.

    `resume_run_id` continues that milestone run instead (card 54e4ec29).
    `milestone`, `base_branch`, `branch_prefix`, `max_concurrent` and
    `clock` are then not read: the milestone is the one the run id names
    (`find_run_milestone`) and the rest is what the run recorded. The plan is
    re-derived from the board as a fresh run derives it. Every refusal -- an
    unknown, non-milestone or `done` run, an unknown milestone, a blocker
    cycle, and a checkpoint saved under another workflow digest -- comes
    before the first write and before git is refreshed. Then the run is
    recorded `started`, the plan is re-recorded, orphan attempts are marked
    `harness_error` and every open stopped, escalated or started row is
    recorded `started` (`reopen_rows`), and `supervise` runs under the same
    run id with each open checkpoint handed on as `resume_from`. Every
    payload gains `resumed: true`; `completed` is this invocation's work.
    """
    if resume_run_id is None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
        if milestone is None or base_branch is None or branch_prefix is None:
            raise ValueError(
                "a fresh milestone run needs a milestone, a base branch and a branch prefix"
            )
    root = cli.resolve_repo_dir(repo_dir)
    resumed = None if resume_run_id is None else resumable_milestone_run(root, resume_run_id)
    if resumed is not None:
        base_branch = resumed.base_branch
        branch_prefix = resumed.branch_prefix
        max_concurrent = resumed.config.max_concurrent_stories
    roots = board.roots(repo_dir=root)
    if resumed is None:
        milestone_card = census.find_milestone(roots, milestone)
    else:
        milestone_card = find_run_milestone(roots, resumed.id)
    plan = census.flatten_milestone(board.tree(milestone_card.id, repo_dir=root))
    levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    tips = story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    drive = cli.drive_subtask_async if driver is None else driver

    if resumed is None:
        # The first side effect. It runs after every refusal and before the store
        # is opened, so a failed fetch leaves no run directory behind.
        refresh_git(root)
        started_at = clock()
        run_id = cli.mint_run_id(milestone_card.id, started_at)
        run_record = models.Run(
            id=run_id,
            workflow=MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(max_concurrent_stories=max_concurrent),
        )
    else:
        run_id = resumed.id
        run_record = resumed.model_copy(update={"status": "started"})
    store = Store.open(root, run_id)
    try:
        checkpoints: dict[str, Checkpoint] | None = None
        cards: list[tuple[str, Workflow]] = []
        if resumed is not None:
            cards = open_cards(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
            # The store's own refusal, a checkpoint saved under another
            # workflow, comes before the first write and before git is touched.
            checkpoints = resume_checkpoints(store, cards)
            refresh_git(root)
        store.record_run(run_record)
        rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
        if resumed is not None:
            # After `record_plan`, which records every planned row `pending`.
            reopen_rows(store, resumed, {card_id for card_id, _workflow in cards})
        warnings = reroll_stale_stories(plan.stories, root)
        completed: list[str] = []
        stop = StopSignal()

        outcomes = asyncio.run(
            supervise(
                supervisor_plan(
                    plan.stories,
                    levels,
                    rows,
                    branch_prefix=branch_prefix,
                    base_branch=base_branch,
                    checkpoints=checkpoints,
                ),
                store=store,
                run_id=run_id,
                root=root,
                drive=drive,
                commands=list(commands),
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                max_concurrent=max_concurrent,
                stop=stop,
            )
        )
        # Wave order, census order within a wave, never finish order.
        for outcome in outcomes:
            completed.extend(outcome.completed)
            warnings.extend(outcome.warnings)
        built_bases = bases_payload(outcomes)

        def report(payload: dict[str, Any]) -> dict[str, Any]:
            """Every payload shape on the same terms: `bases` when built, `resumed` on a resume."""
            if resumed is not None:
                payload["resumed"] = True
            return with_bases(payload, built_bases)

        if any(outcome.kind == "escalated" for outcome in outcomes):
            store.record_run(run_record.model_copy(update={"status": "escalated"}))
            primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
            return report(escalated_payload(run_id, primary, outcomes, warnings))

        # Integrate (addendum I6) runs only once every lane finished clean,
        # and also when there was nothing left to drive: that is how a relaunch
        # retries an Integrate escalation, and why a finished milestone's
        # relaunch is a no-op merge. Read as `integration.integrate_milestone`
        # so a test can replace it, as `driver` is. It needs a factory for a
        # conflicting tip; `None` is production's, read off `cli` now.
        factory = cli.default_runner_factory if runner_factory is None else runner_factory
        outcome = integration.integrate_milestone(
            stories=plan.stories,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            commands=list(commands),
            allow_no_verification=allow_no_verification,
            store=store,
            run_id=run_id,
            runner_factory=factory,
        )
        if isinstance(outcome, integration.IntegrateEscalation):
            # The branch and worktree stay exactly as Integrate left them (I5).
            store.record_run(run_record.model_copy(update={"status": "escalated"}))
            return report(integrate_escalated_payload(run_id, outcome, warnings))

        store.record_run(run_record.model_copy(update={"status": "done"}))
        return report(
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
    finally:
        store.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py tests/test_bases.py -v`
Expected: PASS (every test in both files, including `test_every_node_has_no_timeout`, `test_only_orchestrate_imports_grafo`, the relaunch test `test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh`, and `test_a_bound_below_one_is_refused_before_anything_is_written`)

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): run_milestone(resume_run_id=) continues a milestone run"
```

---

### Task 5: `am resume` dispatches on the run's workflow

**Files:**
- Modify: `src/agent_manager/cli.py:1314-1324` (`_resume_from_checkpoint` docstring), `:1389-1431` (`resume_run`), `:1434-1480` (`resume` command)
- Test: `tests/test_cli.py` (rewrite `test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused` at 4509-4526; new section appended at the end of the file)

**Interfaces:**
- Consumes: `orchestrate.run_milestone(None, ..., resume_run_id=)` and `orchestrate.MILESTONE_WORKFLOW` (Task 4).
- Produces: `cli.resume_run(run_id, *, repo_dir, allow_no_verification=False, commands=(), runner_factory=None) -> dict[str, Any]`, unchanged signature; `"task"` → `_resume_from_checkpoint(run, root=, allow_no_verification=, commands=, runner_factory=)`; `"milestone"` → `orchestrate.run_milestone(None, repo_dir=root, commands=list(commands), allow_no_verification=, runner_factory=, resume_run_id=run.id)`; any other workflow → `NotResumableError`. The `resume` command exits 1 when `payload.get("status") == "escalated"` or `payload.get("escalated") is True`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, replace `test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused` (lines 4509-4526) with:

```python
@requires_git
@requires_brd
def test_a_parked_milestone_subtask_resumes_on_pygents_instead_of_being_refused(
    project, cards, monkeypatch
):
    """Spec test 1, parked half (card 54e4ec29): the run is a `milestone` run,
    so `resume` continues the milestone, and its parked subtask goes on from
    its checkpoint at `validate_spec`."""
    run_id = _park_pygents(project, cards)
    integrate = _integrate_ok(monkeypatch)

    after: list[str] = []
    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory(after))

    assert payload["done"] is True, payload
    assert payload["resumed"] is True
    assert payload["run_id"] == run_id
    assert payload["completed"] == [cards["subtask"]]
    assert after[0] == "validate_spec"
    assert not {"explore", "spec"} & set(after)
    assert [call["run_id"] for call in integrate] == [run_id]
    assert [row for row in _attempt_rows(project) if row[5] == "started"] == []
    assert board.show(cards["subtask"], repo_dir=project).status == "done"
```

Append at the end of `tests/test_cli.py`:

```python
# ── am resume on a milestone run (card 54e4ec29) ─────────────────────────────

MILESTONE_AT = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
"""The interrupted milestone run's clock, so its run id is known."""


def _integrate_ok(monkeypatch) -> list[dict[str, Any]]:
    """Replace `integration.integrate_milestone`, which `run_milestone` reads at
    call time, with a success that merges nothing; record every call."""
    calls: list[dict[str, Any]] = []

    def succeed(**kwargs: Any) -> integration.IntegrateSuccess:
        calls.append(kwargs)
        branch = integration.integration_branch(kwargs["branch_prefix"])
        return integration.IntegrateSuccess(
            branch=branch,
            worktree=cli.worktree_for(kwargs["repo_dir"], branch),
            merged=[story.id for story in kwargs["stories"] if story.subtasks],
        )

    monkeypatch.setattr(integration, "integrate_milestone", succeed)
    return calls


def _milestone_factory(seen: dict[str, list[str]], fail: dict[str, str] | None = None):
    """A `cli.RunnerFactory` for a whole milestone: `fake_runner` per card, each
    agent phase recorded under its card, and `fail[card]` failing that phase."""
    failing = dict(fail or {})

    def factory(*, store, run_id, story_id, card_id):
        inner = fake_runner(fail=failing.get(card_id))

        def runner(phase, context, rendered):
            seen.setdefault(card_id, []).append(phase.name)
            return inner(phase, context, rendered)

        return runner

    return factory


@pytest.fixture
def resume_board(project) -> dict[str, str]:
    """Milestone 4: story A (a1 then a2) and story B (b1), B blocked by A."""
    milestone = _add_card(project, "Milestone 4: resume")
    story_a = _add_card(project, "Story A: first", milestone)
    a1 = _add_card(project, "a1: first of A", story_a)
    a2 = _add_card(project, "a2: second of A", story_a)
    _block(project, a2, a1)
    story_b = _add_card(project, "Story B: second", milestone)
    b1 = _add_card(project, "b1: only of B", story_b)
    _block(project, story_b, story_a)
    return {
        "milestone": milestone,
        "story_a": story_a,
        "a1": a1,
        "a2": a2,
        "story_b": story_b,
        "b1": b1,
    }


def _escalate_milestone(project: Path, shape: dict[str, str]) -> str:
    """A real milestone run on the fake runner: a1 done, a2 escalated at
    `review`, B never started. Returns the run id."""
    payload = orchestrate.run_milestone(
        shape["milestone"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m4",
        runner_factory=_milestone_factory({}, fail={shape["a2"]: "review"}),
        clock=lambda: MILESTONE_AT,
        max_concurrent=2,
    )
    assert payload["escalated"] is True, payload
    assert (payload["subtask"], payload["failed_phase"]) == (shape["a2"], "review")
    return payload["run_id"]


def _plant_orphan(project: Path, run_id: str, story_id: str, card_id: str, phase: str) -> None:
    """An attempt left `started` by a kill mid-dispatch, as `dispatch.AgentRunner` records it."""
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.record_phase(
            story_id, card_id, models.PhaseRun(name=phase, kind="agent", status="started")
        )
        opened.record_attempt(
            story_id,
            card_id,
            phase,
            models.Attempt(n=1, dispatch=_recorded_dispatch(run_id), status="started"),
        )
    finally:
        opened.close()


def _project_run_ids(project: Path) -> list[str]:
    conn = sqlite3.connect(paths.project_db_path(project))
    try:
        return [row[0] for row in conn.execute("SELECT id FROM runs ORDER BY id")]
    finally:
        conn.close()


def _loaded(project: Path, run_id: str) -> models.Run:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        run = store_module.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None
    return run


def _record_milestone(root: Path, run_id: str, *, status: str, workflow: str = "milestone") -> None:
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow=workflow,
                repo_dir=root,
                base_branch="main",
                branch_prefix="m4",
                status=status,
                started_at=RECORDED_AT,
            )
        )
    finally:
        opened.close()


@requires_git
@requires_brd
def test_a_milestone_that_escalated_resumes_under_its_own_run_id(
    project, resume_board, monkeypatch
):
    """Spec test 1, escalated half: a2 resumes at `review` with nothing before
    it re-dispatched, a1 (done) is not driven, b1 starts fresh, Integrate
    runs, and only this invocation's work is `completed`."""
    shape = resume_board
    run_id = _escalate_milestone(project, shape)
    integrate = _integrate_ok(monkeypatch)
    seen: dict[str, list[str]] = {}

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_milestone_factory(seen))

    assert payload["done"] is True, payload
    assert payload["resumed"] is True
    assert payload["run_id"] == run_id
    assert payload["completed"] == [shape["a2"], shape["b1"]]
    assert shape["a1"] not in seen
    assert seen[shape["a2"]][0] == "review"
    assert not {
        "explore",
        "spec",
        "validate_spec",
        "plan",
        "validate_plan",
        "implement",
    } & set(seen[shape["a2"]])
    assert seen[shape["b1"]][0] == "explore"
    assert [call["run_id"] for call in integrate] == [run_id]
    assert _project_run_ids(project) == [run_id]
    run = _loaded(project, run_id)
    assert run.status == "done"
    assert run.config.max_concurrent_stories == 2
    assert board.show(shape["a2"], repo_dir=project).status == "done"
    assert board.show(shape["b1"], repo_dir=project).status == "done"


@requires_git
@requires_brd
def test_a_milestone_resume_marks_an_orphan_attempt_harness_error(
    project, resume_board, monkeypatch
):
    """Spec test 6: an attempt still `started` from the interrupted run."""
    shape = resume_board
    run_id = _escalate_milestone(project, shape)
    _plant_orphan(project, run_id, shape["story_a"], shape["a2"], "review")
    _integrate_ok(monkeypatch)

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_milestone_factory({}))

    assert payload["done"] is True, payload
    rows = [row for row in _attempt_rows(project) if row[2] == shape["a2"]]
    assert [(row[3], row[4], row[5]) for row in rows] == [("review", 1, "harness_error")]


@requires_git
@requires_brd
def test_a_milestone_resume_across_a_workflow_change_is_exit_three_and_writes_nothing(
    project, resume_board, monkeypatch
):
    """Spec test 3: one stale subtask refuses the whole resume; the orphan is
    still `started`, and no row, attempt, checkpoint, journal line or branch changed."""
    shape = resume_board
    run_id = _escalate_milestone(project, shape)
    _plant_orphan(project, run_id, shape["story_a"], shape["a2"], "review")
    _plant_changed_digest(project, run_id, shape["a2"])
    before = _resume_state(project)
    branches = _git(project, "branch", "--format=%(refname:short)")
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CheckpointMismatchError"
    message = envelope["error"]["message"]
    assert message.startswith("workflow changed since checkpoint")
    assert shape["a2"] in message
    assert "saved-under-another-task" in message
    assert task_workflow.TASK.digest() in message
    assert _resume_state(project) == before
    assert _git(project, "branch", "--format=%(refname:short)") == branches
    assert [row[5] for row in _attempt_rows(project) if row[2] == shape["a2"]] == ["started"]


def test_resuming_a_finished_milestone_run_is_exit_three_and_writes_nothing(
    projection, monkeypatch
):
    """Spec test 5: refused before the board is read or the store opened."""
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="done")
    before = (_runs_snapshot(), _attempt_rows(projection))
    monkeypatch.setattr(cli.board, "roots", _Forbidden("board.roots"))

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "NotResumableError"
    assert envelope["error"]["message"] == (
        f"run {run_id} finished; start new work with am run --milestone"
    )
    assert (_runs_snapshot(), _attempt_rows(projection)) == before


def test_resume_routes_a_task_run_to_the_single_subtask_path(projection, monkeypatch):
    """Spec test 8: a `task` run goes where it always went, with the same arguments."""
    run_id = "20260923T090000Z-cbe34d00"
    _record(projection, run_id, started_at=RECORDED_AT, status="started")
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def fake_resume(run, **kwargs):
        seen.append((run.id, run.workflow, kwargs))
        return {"status": "done"}

    monkeypatch.setattr(cli, "_resume_from_checkpoint", fake_resume)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))

    payload = cli.resume_run(run_id, repo_dir=projection)

    assert payload == {"status": "done"}
    assert seen == [
        (
            run_id,
            "task",
            {
                "root": projection.resolve(),
                "allow_no_verification": False,
                "commands": (),
                "runner_factory": None,
            },
        )
    ]


def test_resume_routes_a_milestone_run_to_run_milestone_under_its_own_id(
    projection, monkeypatch
):
    """Review Focus 5: `--verify` and the opt-out reach the milestone resume."""
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="escalated")
    calls: list[tuple[Any, dict[str, Any]]] = []

    def fake_run_milestone(milestone, **kwargs):
        calls.append((milestone, kwargs))
        return {"done": True, "run_id": run_id, "resumed": True}

    def factory(**kwargs):
        return None

    monkeypatch.setattr(orchestrate, "run_milestone", fake_run_milestone)
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))

    payload = cli.resume_run(
        run_id,
        repo_dir=projection,
        commands=("uv run pytest",),
        allow_no_verification=True,
        runner_factory=factory,
    )

    assert payload == {"done": True, "run_id": run_id, "resumed": True}
    assert calls == [
        (
            None,
            {
                "repo_dir": projection.resolve(),
                "commands": ["uv run pytest"],
                "allow_no_verification": True,
                "runner_factory": factory,
                "resume_run_id": run_id,
            },
        )
    ]


def test_resume_refuses_a_run_of_a_workflow_it_does_not_know(projection, monkeypatch):
    run_id = "20260927T100000Z-cbe34d00"
    _record_milestone(projection, run_id, status="started", workflow="integrate")
    monkeypatch.setattr(cli, "_resume_from_checkpoint", _Forbidden("_resume_from_checkpoint"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("orchestrate.run_milestone"))

    with pytest.raises(cli.NotResumableError, match="'integrate'"):
        cli.resume_run(run_id, repo_dir=projection)


@pytest.mark.parametrize(
    "payload, code",
    [
        ({"done": True, "run_id": "r", "resumed": True}, 0),
        ({"escalated": True, "run_id": "r", "resumed": True}, cli.EXIT_ESCALATED),
    ],
    ids=["done", "escalated"],
)
def test_the_resume_command_reads_a_milestone_payloads_escalated_flag(
    tmp_path, monkeypatch, payload, code
):
    """A milestone payload has no `status` key, as for `run --milestone`."""
    monkeypatch.setattr(cli, "resume_run", lambda run_id, **kwargs: payload)

    result = runner.invoke(
        cli.app, ["resume", "20260927T100000Z-cbe34d00", "--repo-dir", str(tmp_path)]
    )

    assert result.exit_code == code, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(payload)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "parked_milestone_subtask or escalated_resumes_under or milestone_resume_marks or milestone_resume_across or finished_milestone_run or routes_a or workflow_it_does_not_know or milestone_payloads_escalated" -v`
Expected: FAIL — the milestone resumes raise `NotResumableError` from `select_resumable` (the task path), the routing tests reach the `_Forbidden` stand-ins, the done-run message is the task path's, and the command tests raise `KeyError: 'status'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/cli.py`, replace `_resume_from_checkpoint`'s docstring (lines 1314-1324) with:

```python
    """Continue a `task` run's one in-flight subtask from its newest checkpoint.

    Every refusal that needs no store -- nothing in flight, a card the board
    lost -- comes before `Store.open`. The checkpoint can only be read through
    the store, so its refusals (`checkpoint_resume_phase`) come right after it
    is opened and before the first write. Then the orphan attempts are marked
    `harness_error`, the run, story and subtask are recorded `started`, and
    `drive_subtask` walks `TASK` from the checkpoint, whose queue says where the
    walk goes on. A `milestone` run never comes here: `resume_run` hands it to
    `orchestrate.run_milestone` (card 54e4ec29).
    """
```

Replace `resume_run` (lines 1389-1431) with:

```python
def resume_run(
    run_id: str,
    *,
    repo_dir: Path,
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
) -> dict[str, Any]:
    """Pick a stopped, escalated or killed run back up from its checkpoints (§9).

    The run's recorded `workflow` decides. A `task` run continues its one
    in-flight subtask (`_resume_from_checkpoint`, card 02890d5d), exactly as
    before. A `milestone` run continues the whole milestone under the same
    run id (`orchestrate.run_milestone(resume_run_id=...)`, card 54e4ec29).
    Any other workflow is refused.

    The order is load-bearing in the same way `run_card`'s is, only inverted:
    every refusal -- unknown run, nothing in flight, a card the board lost --
    happens before `Store.open`, because `Store.open` constructs a `Journal`
    and therefore mints a run directory, and a refusal that left one behind
    would be this command writing state for a run it declined to touch.

    Branch, base branch and worktree come from the recorded run and never
    from a flag: §9's "the run records what it was started with" is the
    reason the record exists. The two knobs the record does *not* carry --
    `models.RunConfig` has no suite commands and no `allow_no_verification` --
    are still taken as arguments. A walk continued from a checkpoint never
    reads them: its binding comes from the checkpoint's pool. On a milestone
    they also reach what starts afresh -- subtasks with no checkpoint, merged
    bases and Integrate.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
    finally:
        conn.close()
    if run.workflow == WORKFLOW_NAME:
        return _resume_from_checkpoint(
            run,
            root=root,
            allow_no_verification=allow_no_verification,
            commands=commands,
            runner_factory=runner_factory,
        )
    # Imported here for the reason `run` gives: `orchestrate` imports this
    # module at load time. Read as `orchestrate.run_milestone` so a test can
    # patch it there.
    from agent_manager import orchestrate

    if run.workflow == orchestrate.MILESTONE_WORKFLOW:
        return orchestrate.run_milestone(
            None,
            repo_dir=root,
            commands=list(commands),
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            resume_run_id=run.id,
        )
    raise NotResumableError(
        f"run {run.id!r} records workflow {run.workflow!r}, and `resume` continues"
        f" only {WORKFLOW_NAME!r} and {orchestrate.MILESTONE_WORKFLOW!r} runs"
    )
```

Replace the `resume` command (lines 1434-1480) with:

```python
@app.command("resume")
def resume(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run to pick back up."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help=(
            "A walk continued from a checkpoint keeps the opt-out the run started "
            "with. On a milestone run, this applies to what starts afresh: "
            "subtasks with no checkpoint, merged bases and Integrate."
        ),
    ),
    verify: list[str] = typer.Option(
        [],
        "--verify",
        help=(
            "A walk continued from a checkpoint keeps the suite the run started "
            "with. On a milestone run, this is the suite for what starts afresh: "
            "subtasks with no checkpoint, merged bases and Integrate."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Continue a stopped, escalated or killed run from its checkpoints, and drive it to the end.

    No `--base-branch` and no `--branch-prefix`: both were decided when the run
    started and are recorded (§9). A `task` run continues its one subtask; a
    `milestone` run continues the whole milestone under the same run id.
    """
    try:
        payload = resume_run(
            run_id,
            repo_dir=repo_dir,
            allow_no_verification=allow_no_verification,
            commands=list(verify),
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    # A task payload reports `status`; a milestone payload has none and
    # carries `escalated: true` only when it stopped, as for `run`. Both
    # checks are strict on purpose: a `stopped` walk (addendum P4) is not an
    # escalation, so it exits 0 with an ok envelope.
    if payload.get("status") == "escalated" or payload.get("escalated") is True:
        raise typer.Exit(EXIT_ESCALATED)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (new tests; every existing task-resume test unchanged: `test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint`, `test_a_pygents_resume_marks_the_orphan_attempt_harness_error`, `test_a_pygents_resume_of_a_done_checkpoint_writes_nothing`, `test_a_pygents_resume_refuses_a_phase_escalation_and_writes_nothing`, `test_a_pygents_resume_across_a_workflow_change_writes_nothing`, `test_a_pygents_resume_across_a_workflow_change_is_an_envelope_at_exit_three`, `test_the_resume_command_prints_an_ok_envelope_and_exits_zero`, `test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one`, `test_a_resumed_walk_that_stops_is_ok_true_and_exit_zero`, `test_resume_of_a_run_with_nothing_in_flight_is_an_envelope`, `test_cli_and_orchestrate_import_cleanly_in_either_order`)

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, `tests/e2e` included (its only `resume` is a `--card` run, `tests/e2e/test_milestone_run.py:297-358`)

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): am resume continues a whole milestone"
```

---

## Self-Review

**Spec coverage.**
- Point 1 (dispatch on `run.workflow`, task path unchanged) → Task 5 `resume_run`; tests `test_resume_routes_a_task_run_to_the_single_subtask_path`, `test_resume_routes_a_milestone_run_to_run_milestone_under_its_own_id`, every existing task-resume test left as is.
- Point 2, reuse run id / prefix / base / bound → Task 4 (`resumable_milestone_run` + `run_record = resumed.model_copy`); spec test 2.
- Point 2, refresh git → Task 4 (`refresh_git(root)` after validation); refusal never touches git (spec test 4 asserts `git_calls == []`).
- Point 2, fresh census, done cards skipped → Task 4 re-derives `plan` from `board.tree`; `open_cards` uses `dag.remaining_subtasks` (Task 3); spec test 1 (`a1` not driven), Review Focus 1.
- Point 2, `latest_checkpoint` per open subtask and `base-<story>` resolver → Task 3 `open_cards` / `resume_point` / `resume_checkpoints`.
- Point 2, validate all before writing → Task 3 `resume_checkpoints` is read-only and runs before `store.record_run` in Task 4; spec tests 3 and 4.
- Point 2, orphans `harness_error` reusing `cli.orphan_attempts` → Task 3 `reopen_rows`; spec test 6 plus the unit test.
- Point 2, row transitions → Task 3 `reopen_rows` (subtasks and resolvers) and Task 4 (`run_record` `started`).
- Point 2, `supervise` under the same run id with `resume_from` → Task 4 (`SupervisorPlan.checkpoints`, lane, `build_merged_base`) and Task 2 (`bases.build(resume_from=)` through `_resolve_conflict`); spec test 2.
- Point 2, bases not re-merged → Task 2 keeps `merge_tip`'s `already_merged`; spec test 7.
- Point 2, Integrate runs → Task 4 unchanged Integrate call; spec test 1, Review Focus 4.
- Point 3, `resumed: true`, `completed` this invocation only → Task 4 `report`; spec test 1 and `test_an_escalated_resume_still_says_it_resumed`.
- Strict vs lenient → Task 4 lane reads `plan.checkpoints` on resume and never `cli.continuable_checkpoint` (asserted in spec test 2); the relaunch test is untouched.
- Error paths: digest mismatch (spec tests 3, 4, Task 3 unit tests), `done` run (spec test 5, Task 3 unit test), task run unchanged (spec test 8).
- Invariants: no grafo import added outside `orchestrate.py`; no new grafo Node; no test sleeps (the parked-resolver test reuses the existing `threading.Event` handshake); base branch sha asserted in spec test 7 and Task 2 tests.
- Out of scope: nothing under `tests/e2e`.

**Placeholder scan.** No TBD/TODO; every code step carries its code; every helper used in a test is defined in the same task or already exists in the file (`_plant`, `_plant_changed_digest`, `_resume_state`, `_attempt_rows`, `_runs_snapshot`, `_record`, `_recorded_dispatch`, `_Forbidden`, `_park_pygents`, `_resume_factory`, `fake_runner`, `CheckpointDriver`, `BranchingDriver`, `_record_git`, `_local_branches`, `_root_plan`, `_bases_entry`, `_sha`, `_statuses`, `_load`, `PASS_CMD`, `EARLIER`, `_ABSENT`, `PREFIX`).

**Type consistency.** `resolver_card_id(story_id) -> str` (Task 2) is what Tasks 3 and 4 call; `latest_turn_checkpoint(card_id) -> Checkpoint | None` (Task 1) is what Tasks 2 (tests) and 3 call; `open_cards(...) -> list[tuple[str, Workflow]]` feeds `resume_checkpoints(store, cards)` and the `{card_id for card_id, _workflow in cards}` set passed to `reopen_rows(store, run, open_card_ids)`; `supervisor_plan(..., checkpoints=)` sets `SupervisorPlan.checkpoints` / `.resuming`, which `lane` and `base_only_lane` read; `run_milestone(None, ..., resume_run_id=)` is what `cli.resume_run` calls with exactly the kwargs its routing test asserts.

**Review Focus.** Each of the five lines has a test in its owning task: 1 → Task 4 `test_a_card_finished_by_hand_since_the_interrupt_is_neither_checked_nor_driven`; 2 → Task 3 `test_each_open_card_resumes_from_its_newest_row_or_the_turn_it_failed_in` (card 21); 3 → Task 2 `test_a_resumed_resolver_parked_after_its_merge_continues_at_verify`; 4 → Task 4 `test_a_resume_after_an_integrate_escalation_retries_integrate`; 5 → Task 5 `test_resume_routes_a_milestone_run_to_run_milestone_under_its_own_id`.
