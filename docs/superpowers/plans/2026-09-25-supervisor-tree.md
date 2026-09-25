# The supervisor tree — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **In this repo** every task below is one `brd` subtask card driven by the `task` workflow, which writes its own per-subtask spec and TDD plan from the card and this file. Task numbers are `<story>.<subtask>`; each card's description names its Task and carries the snippets it needs.

**Goal:** Replace `orchestrate.py`'s thread pool with one event loop where stories are a grafo tree (start when all blockers succeed), subtasks run on M6's pygents agents with a `pause()`-based stop, stories with several blockers get a merged base, and `am resume` continues a whole milestone.

**Architecture:** `run_milestone` → `asyncio.run(supervise(...))`. `supervise` builds one grafo `Node` per story and one edge per in-milestone blocker, runs `TreeExecutor`, and reads outcomes from node outputs and `executor.errors`. Each node coroutine is a lane: a semaphore slot, a root (base branch, the blocker's tip, or a merged base from `bases.build`), then the story's subtasks via `cli.drive_subtask_async`. `StopSignal` pauses registered subtask agents; M6's `ON_PAUSE` hook checkpoints and raises `Parked`.

**Tech Stack:** Python 3.12, `pygents>=0.6.7` (M6), `grafo>=0.3.5`, pytest + pytest-asyncio, real temporary git repos for merge tests.

**Spec:** `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (T1–T10). Read it, and M6's `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`, before any task.

## Global Constraints

- New runtime dependency: `grafo>=0.3.5` only. Only `src/agent_manager/orchestrate.py` imports `grafo`.
- Every grafo `Node` is built with `timeout=None` (grafo's default is 60 s and cancels the lane).
- Only the subtask is a pygents Agent (T2). Supervisor and lanes are plain async functions.
- One `asyncio.run` per `am` invocation for milestone runs; no `ThreadPoolExecutor` after Task 3.1.
- `--max-concurrent` default 4, meaning stories running at once; a lane takes its slot after its blockers finished.
- CLI envelope unchanged (`{"ok": true, "data": ...}`, `--pretty`); report changes are only the additive ones in spec §8.
- Never `break`/`return` out of `agent.run()`; pygents hooks are module-level with `tags={"subtask"}` (M6 rules still hold).
- The milestone's base branch never moves and nothing is pushed.
- Verification for every task: `uv run pytest` — the whole suite green, each task alone.
- Branch prefix `m7`. Base: `master` with milestone 6 merged. grafo 0.3.5 released on PyPI before Task 1.3.

## Review Focus

1. **A lane running longer than a minute.** grafo's `Node` default `timeout=60.0` would cancel it mid-phase. Every node must carry `timeout=None`. Test in Task 3.1 (assert on every built node; plus one lane that awaits past a patched 0.05 s default is not cancelled).
2. **A lane bug while grafo logs.** grafo logs node errors with a traceback; stdout must still be exactly one JSON line. Test in Task 3.1 (`capsys`: stdout parses as JSON, the traceback is not on stdout).
3. **The stop firing while a lane waits for its slot.** It must end `stopped` without running a subtask. Test in Task 3.1.
4. **Two blockers where one already contains the other** (C blocked by A and B, B stacked on A). The second merge is `already_merged`; the base equals B's tip; no resolver. Test in Task 2.1.
5. **A blocker tip ref that does not exist** (deleted local branch, done in an earlier run). The story escalates at `base` naming the missing ref, instead of a raw git error or a hang. Test in Task 2.1.

---

## File map

| File | Responsibility | Tasks |
|---|---|---|
| `src/agent_manager/runtime/stop.py` | `StopSignal` | 1.1 |
| `src/agent_manager/runtime/engine.py` | public `run_subtask_async`; `stop` parameter | 1.1, 3.3 |
| `src/agent_manager/runtime/checkpoint.py` | `ON_PAUSE` hook | 1.1, 3.3 |
| `src/agent_manager/cli.py` | `drive_subtask_async`; milestone resume | 1.2, 4.1 |
| `src/agent_manager/dag.py` | `RootPlan`, multi-blocker roots | 1.3 |
| `src/agent_manager/bases.py` | merged bases | 2.1, 2.2 |
| `src/agent_manager/orchestrate.py` | `supervise`, `lane`, outcomes | 3.1, 3.2, 3.3 |
| `README.md` | docs | 5.1 |

---

## Story 1 — Groundwork

### Task 1.1: Add `StopSignal` and park subtasks through `ON_PAUSE`

**Files:** Create `src/agent_manager/runtime/stop.py`; modify `runtime/engine.py`, `runtime/checkpoint.py`, `runtime/state.py`. Test: `tests/runtime/test_stop.py`.

**Interfaces:**
- Produces: `StopSignal` with `.triggered: bool`, `.primary: str | None`, `.trigger(story_id: str) -> bool` (True for the first caller), `.register(agent) -> None`, `.unregister(agent) -> None`; `runtime.engine.run_subtask_async(workflow, store, *, ..., stop: StopSignal | None = None, resume_from=None) -> SubtaskSummary` (public; `run_subtask` = `asyncio.run(run_subtask_async(...))`); `RunDeps.stop`.
- Keeps (until Task 3.3): `should_stop` and the `BEFORE_TURN` stop branch, so `orchestrate.py`'s threads still work.

- [ ] **Step 1: Failing tests**

```python
# tests/runtime/test_stop.py
import asyncio
from agent_manager.runtime.stop import StopSignal

class FakeAgent:
    def __init__(self): self.paused = 0
    def pause(self): self.paused += 1

def test_first_trigger_is_primary():
    stop = StopSignal()
    assert stop.trigger("A") is True and stop.trigger("B") is False
    assert stop.primary == "A" and stop.triggered

def test_trigger_pauses_registered_agents_and_late_registrations():
    stop, early, late = StopSignal(), FakeAgent(), FakeAgent()
    stop.register(early)
    stop.trigger("A")
    stop.register(late)
    assert early.paused == 1 and late.paused == 1

def test_unregistered_agents_are_not_paused():
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent); stop.unregister(agent); stop.trigger("A")
    assert agent.paused == 0
```

Plus, in `tests/runtime/test_stop_bridge.py`, a pygents test: two subtasks run concurrently with `run_subtask_async(..., stop=stop)` on one loop; the first's fake step triggers `stop.trigger("A")` mid-phase; the second's newest checkpoint is `parked` with its next phase at the queue head, its summary `stopped`, `detail == "stopped before <phase>"`.

- [ ] **Step 2:** Watch them fail.
- [ ] **Step 3: Implement**

```python
# src/agent_manager/runtime/stop.py
"""The milestone's cooperative stop (spec T5). One per run, on the run's one loop."""
from __future__ import annotations
from typing import Any

class StopSignal:
    def __init__(self) -> None:
        self.triggered = False
        self.primary: str | None = None
        self._agents: set[Any] = set()

    def trigger(self, story_id: str) -> bool:
        first = not self.triggered
        if first:
            self.triggered, self.primary = True, story_id
        for agent in list(self._agents):
            agent.pause()
        return first

    def register(self, agent: Any) -> None:
        self._agents.add(agent)
        if self.triggered:
            agent.pause()

    def unregister(self, agent: Any) -> None:
        self._agents.discard(agent)
```

In `runtime/checkpoint.py` add the module-level hook:

```python
@hook(AgentHook.ON_PAUSE, tags={"subtask"})
async def on_pause(agent) -> None:
    deps = current_run.get(None)
    if deps is None:
        return
    head = agent.to_dict()["queue"][0]["kwargs"]["phase"]
    save(agent, "parked")
    raise Parked(head)
```

In `runtime/engine.py`: rename the internal `_drive` to public `run_subtask_async` (keep `run_subtask` as its `asyncio.run` wrapper), add `stop`, and around `_run`: `if stop: stop.register(agent)` before `run()`, `stop.unregister(agent)` in `finally`. `RunDeps` gains `stop: StopSignal | None = None`.

- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(runtime): StopSignal parks subtask agents through ON_PAUSE"`

### Task 1.2: Add `cli.drive_subtask_async`

**Files:** Modify `src/agent_manager/cli.py`. Test: `tests/test_cli.py`.

**Interfaces:**
- Produces: `async def drive_subtask_async(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, stop: StopSignal | None = None, resume_from=None) -> SubtaskDrive`. `drive_subtask` (sync) becomes `asyncio.run(drive_subtask_async(...))` with the same signature it has after M6 (its `should_stop` still passed through until Task 3.3).

- [ ] **Step 1: Failing test** — `drive_subtask_async` awaited inside a running loop with a fake runner factory returns the same `SubtaskDrive` (status, results keys, warnings) as `drive_subtask` for the same inputs; calling it inside a running loop does not raise `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- [ ] **Step 2:** Watch it fail.
- [ ] **Step 3: Implement** by moving `drive_subtask`'s body into the async function, calling `await runtime.engine.run_subtask_async(workflow.task.TASK, ...)`.
- [ ] **Step 4:** `uv run pytest` → green.  **Step 5: Commit** — `git commit -m "feat(cli): an awaitable subtask driver"`

### Task 1.3: Add grafo and multi-blocker roots in `dag`

**Files:** Modify `pyproject.toml` (`uv add "grafo>=0.3.5"`), `src/agent_manager/dag.py`, `src/agent_manager/cli.py` (dry run), `src/agent_manager/orchestrate.py` (refuse `merged` roots as today, until Task 3.2). Test: `tests/test_dag.py`, `tests/test_cli.py`.

**Interfaces:**
- Produces: `@dataclass(frozen=True) class RootPlan: kind: Literal["base", "tip", "merged"]; branch: str; blockers: tuple[str, ...]`; `dag.story_root(story, stories_by_id, prefix, base_branch) -> RootPlan`; `dag.base_branch_name(prefix, story) -> str` = `f"{prefix}/base-{short_id(story.id)}"`. Cycles still raise `DependencyCycleError`; two or more in-milestone blockers no longer raise `StackRootError`.
- Dry run: a story row whose root is `merged` has `"root": "<prefix>/base-<id>"` and `"merged_from": [<blocker ids in census order>]`.

- [ ] **Step 1: Failing tests**

```python
def test_two_blockers_give_a_merged_root():
    a, b = story("A"), story("B")
    c = story("C", blocked_by=[b.id, a.id])            # census order is A, B
    plan = dag.story_root(c, {s.id: s for s in (a, b, c)}, "m7", "master")
    assert plan.kind == "merged"
    assert plan.blockers == (a.id, b.id)
    assert plan.branch == f"m7/base-{dag.short_id(c.id)}"

def test_one_blocker_roots_on_its_tip(): ...          # kind "tip", branch = the blocker's tip, blockers (a.id,)
def test_no_blocker_roots_on_the_base(): ...          # kind "base", branch "master", blockers ()
def test_a_cycle_is_still_refused(): ...              # DependencyCycleError
```

`test_cli`: the dry run of a milestone with a two-blocker story exits 0 and shows `merged_from`; a real run on that milestone (still the thread runner) refuses with exit 3 and the message it gives today.
- [ ] **Step 2:** Watch them fail.  **Step 3: Implement** (the `story` test helper already exists in `tests/test_dag.py`; reuse it).
- [ ] **Step 4:** `uv run pytest` → green.  **Step 5: Commit** — `git commit -m "feat(dag): multi-blocker stories root on a merged base"`

---

## Story 2 — Merged bases

### Task 2.1: Build a merged base from clean merges

**Files:** Create `src/agent_manager/bases.py`. Test: `tests/test_bases.py` (real temporary git repos; reuse the M5 fixtures in `tests/steps/test_integrate.py` / `tests/conftest.py`).

**Interfaces:**
- Consumes: `worktree.ensure(branch, base, worktree, repo_dir)`, `steps.integrate.merge_tip(repo_dir, worktree, integration_branch, base_branch, tip, git_runner=run_git)`, `steps.integrate.MergeInProgressError`, `verify.run_suite`, `reducers.verification_gate`, `reducers.verification_passed_gate`, `paths` for the worktree location.
- Produces: `@dataclass(frozen=True) class BaseResult: branch: str; merged: list[str]; already_merged: list[str]; resolved: list[str]`; `class BaseFailed(Exception)` with `.detail: str` and `.stopped: bool`; `async def build(root: RootPlan, tips: list[str], *, repo_dir, commands, allow_no_verification, store, run_id, story_id, runner_factory, stop) -> BaseResult`. `tips` are in `root.blockers` order.

- [ ] **Step 1: Failing tests**

```python
async def test_two_clean_tips_merge_into_the_base(two_story_repo):
    root = RootPlan("merged", "m7/base-cccccccc", ("A", "B"))
    result = await bases.build(root, ["m7/a", "m7/b"], repo_dir=two_story_repo, commands=["true"], ...)
    assert result.branch == "m7/base-cccccccc" and result.merged == ["m7/b"]
    assert is_ancestor(two_story_repo, "m7/a", root.branch) and is_ancestor(two_story_repo, "m7/b", root.branch)
    assert rev(two_story_repo, "master") == MASTER_BEFORE          # base branch untouched

async def test_building_twice_merges_nothing_the_second_time(...): ...   # second call: already_merged == ["m7/b"]
async def test_a_tip_already_inside_the_other_is_already_merged(...): ... # Review Focus 4: B stacked on A
async def test_a_missing_tip_fails_naming_the_ref(...):                   # Review Focus 5
    with pytest.raises(bases.BaseFailed, match="m7/gone"): ...
async def test_a_failing_verify_fails_the_base(...): ...                  # commands=["false"] → BaseFailed, not stopped
async def test_a_merge_in_progress_fails_for_a_human(...): ...            # leave MERGE_HEAD → BaseFailed("never resolved")
```

A conflicting merge is out of this task: assert it raises `BaseFailed("conflict ... resolver not wired")` here; Task 2.2 replaces that branch.

- [ ] **Step 2:** Watch them fail.
- [ ] **Step 3: Implement** — every git and verify call through `asyncio.to_thread`; the flow is spec §5's box. Verify mirrors `integration._final_verification` (empty suite judged by `verification_gate` first).
- [ ] **Step 4:** `uv run pytest` → green.  **Step 5: Commit** — `git commit -m "feat(bases): build a merged base from clean merges"`

### Task 2.2: Resolve base conflicts with the Integrate resolver

**Files:** Modify `src/agent_manager/bases.py`. Test: `tests/test_bases.py`, `tests/e2e/test_fake_claude.py` resolver mode (reuse M5's).

**Interfaces:**
- Consumes: `runtime.engine.run_subtask_async(workflow.integrate.INTEGRATE, store, *, story_id="bases", subtask=SubtaskRun(card_id=f"base-{story_id}", ...), repo_dir, commands, extra_context={"merge_tip": tip, "conflict_files": files, **cli.gate_context(commands, allow_no_verification)}, agent_runner=runner_factory(...), stop=stop)`.
- Produces: `BASES_STORY_ID = "bases"`, `BASES_STORY_TITLE = "Merged bases"`; the synthetic story is recorded once per run on first need; `BaseResult.resolved` lists resolved tips; a resolver that escalates → `BaseFailed(detail, stopped=False)`, parked → `BaseFailed(detail, stopped=True)`.

- [ ] **Step 1: Failing tests** — conflicting A/B tips: the fake-claude resolver resolves, `resolved == ["m7/b"]`, the base contains both, `am status` shows phases under story `bases`, subtask `base-<C id>`; a resolver that gives up → `BaseFailed(stopped=False)`; a `stop.trigger` during the resolver's first phase → `BaseFailed(stopped=True)` and a `parked` checkpoint for card `base-<C id>`.
- [ ] **Step 2:** Watch them fail.  **Step 3: Implement** mirroring `integration._resolve_conflict` (record the synthetic subtask before the engine journals its first phase).
- [ ] **Step 4:** `uv run pytest` → green.  **Step 5: Commit** — `git commit -m "feat(bases): resolve conflicts with the Integrate resolver"`

---

## Story 3 — The supervisor

### Task 3.1: Run a milestone's stories as a grafo tree

**Files:** Modify `src/agent_manager/orchestrate.py`. Test: `tests/test_orchestrate.py`, `tests/e2e/test_parallel_milestone.py`.

**Interfaces:**
- Consumes: `grafo.Node`, `grafo.TreeExecutor`, `StopSignal`, `cli.drive_subtask_async`, `dag.story_root`.
- Produces: `async def supervise(plan, *, store, run_id, root, drive, commands, allow_no_verification, runner_factory, max_concurrent, stop) -> list[LaneOutcome]`; `class LaneEscalated(Exception)` / `class LaneStopped(Exception)` carrying `.outcome: LaneOutcome`; `run_milestone` keeps its signature and calls `asyncio.run(...)`. The `Driver` protocol becomes async (`async def __call__(...) -> SubtaskDrive`), default `cli.drive_subtask_async`. `ThreadPoolExecutor`, `RunStop` and the level loop are deleted. `merged` roots are still refused (Task 3.2 lifts it).

- [ ] **Step 1: Failing tests** (rewrite, do not weaken, the barrier tests):

```python
async def test_a_story_starts_when_its_blocker_finishes_not_its_level():
    gate_b = asyncio.Event(); started = []
    async def drive(*, card, **kw):
        started.append(card.id)
        if card.id.startswith("b"): await gate_b.wait()
        return done_drive(card)
    task = asyncio.create_task(supervise(plan(A=["a1"], B=["b1"], C=(["c1"], "A")), drive=drive, ...))
    await wait_until(lambda: "c1" in started)          # event-driven helper, no sleeps
    assert "b1" in started and not gate_b.is_set()     # C started while B is still blocked
    gate_b.set(); outcomes = await task
    assert {o.story: o.status for o in outcomes} == {"A": "done", "B": "done", "C": "done"}

async def test_at_most_max_concurrent_lanes_run(): ...            # 5 ready stories, max 2 → peak 2
async def test_a_chain_finishes_with_one_slot(): ...              # A←B←C, max_concurrent=1
async def test_an_escalation_parks_running_lanes_and_blocks_new_ones(): ...  # real M6 agents, fake runner
async def test_two_escalations_in_one_tick_give_one_primary(): ...
async def test_stop_while_waiting_for_a_slot_ends_stopped(): ...  # Review Focus 3
def test_every_node_has_no_timeout(): ...                         # Review Focus 1: inspect built nodes' _timeout is None
def test_a_lane_bug_keeps_stdout_one_json_line(capsys): ...       # Review Focus 2, through the CLI
```

- [ ] **Step 2:** Watch them fail.
- [ ] **Step 3: Implement**

```python
async def supervise(plan, *, store, run_id, root, drive, commands, allow_no_verification,
                    runner_factory, max_concurrent, stop):
    slots = asyncio.Semaphore(max_concurrent)
    by_id = {s.id: s for s in plan.stories}
    logging.getLogger("grafo").setLevel(logging.CRITICAL)

    def lane(story):
        async def run(**tips):                           # grafo forwards blocker tips here
            remaining = dag.remaining_subtasks(story)
            if not remaining:
                return dag.story_tip(story, by_id, plan.prefix, plan.base_branch)
            async with slots:
                if stop.triggered:
                    raise LaneStopped(LaneOutcome(story.id, "stopped", before=remaining[0].id))
                base = plan.roots[story.id].branch                  # Task 3.2: merged → bases.build
                for sub in remaining:
                    if stop.triggered:
                        raise LaneStopped(LaneOutcome(story.id, "stopped", before=sub.id))
                    result = await drive(card=..., subtask=..., base=base, stop=stop, ...)
                    if result.summary.status == "escalated":
                        stop.trigger(story.id)
                        raise LaneEscalated(escalated_outcome(story, sub, result))
                    if result.summary.status == "stopped":
                        raise LaneStopped(stopped_outcome(story, sub, result))
                    base = dag.subtask_branch(plan.prefix, sub)
                return base
        return run

    nodes = {s.id: Node(coroutine=lane(s), uuid=s.id, timeout=None) for s in plan.stories}
    for s in plan.stories:
        for b in plan.roots[s.id].blockers:
            await nodes[b].connect(nodes[s.id], forward=f"tip_{dag.short_id(b)}")
    executor = TreeExecutor(uuid=run_id, roots=[nodes[s.id] for s in plan.stories if not plan.roots[s.id].blockers])
    await executor.run()
    return collect_outcomes(plan, nodes, executor.errors)   # spec T6
```

`collect_outcomes`: node output → `done` with that tip; `LaneEscalated` → its outcome (first in `errors` is primary, later ones `also_escalated`); `LaneStopped` → its outcome; any other exception → `escalated` with `"<Type>: <msg>"` at the story's current subtask; no output and no error → `pending`. The report keeps `levels` as waves (`dag.compute_levels`).

- [ ] **Step 4:** `uv run pytest` → green, including `tests/e2e/test_parallel_milestone.py`.
- [ ] **Step 5: Commit** — `git commit -m "feat(orchestrate): run stories as a grafo tree on one event loop"`

### Task 3.2: Root multi-blocker stories on merged bases

**Files:** Modify `src/agent_manager/orchestrate.py`. Test: `tests/test_orchestrate.py`, `tests/e2e/test_parallel_milestone.py`.

**Interfaces:** in `lane`, `root.kind == "merged"` → `base = (await bases.build(root, [tips[f"tip_{short_id(b)}"] for b in root.blockers], ...)).branch`; `BaseFailed(stopped=False)` → `stop.trigger(story.id)`, `LaneEscalated` with `failed_phase="base"`, `subtask=None`; `BaseFailed(stopped=True)` → `LaneStopped`. The run's `data` gains `bases: [{"story", "branch", "blockers"}]` when non-empty. The Task 1.3 refusal is removed.

- [ ] **Step 1: Failing tests** — fake-claude e2e: C blocked by A and B runs after both, on `m7/base-<C>`, and its first subtask's base is that branch; a failed B leaves C `pending`; a base resolver failure escalates C at `base` and parks the other running lanes; the report lists `bases`.
- [ ] **Step 2–4:** fail, implement, `uv run pytest` green.
- [ ] **Step 5: Commit** — `git commit -m "feat(orchestrate): multi-blocker stories build and root on merged bases"`

### Task 3.3: Delete the thread-era stop bridge

**Files:** Modify `runtime/engine.py`, `runtime/checkpoint.py`, `runtime/state.py`, `cli.py`. Test: existing tests updated.

**Interfaces:** remove `should_stop` from `run_subtask`, `run_subtask_async`, `RunDeps`, `drive_subtask`, `drive_subtask_async`; remove the stop branch from the `BEFORE_TURN` hook (it still saves `turn`). Stopping is only `StopSignal` + `ON_PAUSE`.

- [ ] **Step 1:** `grep -rn "should_stop" src tests` — every hit is updated or deleted in this task; tests that set `should_stop` switch to a `StopSignal`.
- [ ] **Step 2:** `uv run pytest` → green.  **Step 3: Commit** — `git commit -m "refactor(runtime): the stop is StopSignal only"`

---

## Story 4 — Milestone-wide resume

### Task 4.1: `am resume` continues a milestone run

**Files:** Modify `src/agent_manager/cli.py` (`resume_run` dispatches on `run.workflow`), `src/agent_manager/orchestrate.py` (`run_milestone(..., resume_run_id=None)`). Test: `tests/test_cli.py`, `tests/test_orchestrate.py`.

**Interfaces:** `run_milestone(..., resume_run_id: str | None = None)`: when given, it reuses that run id and its recorded `branch_prefix`, `base_branch`, `max_concurrent_stories`; loads `Store.latest_checkpoint(card_id)` for every open subtask and every `base-<story>` resolver of that run; refuses the whole resume (exit 3, nothing written) on a digest mismatch or a `done` run; marks orphan attempts `harness_error`; passes each checkpoint as `resume_from` to `drive_subtask_async` / `bases.build`. The report adds `resumed: true`.

- [ ] **Step 1: Failing tests** — a milestone run stopped by an escalation, fixed (the fake runner now succeeds), `am resume <run-id>`: same run id, the escalated subtask resumes at its failed phase, the parked one continues, done cards are not driven, Integrate runs; a stale digest → exit 3 "workflow changed since checkpoint", nothing written; a `done` run → exit 3 "run <id> finished"; `am resume` on a `task` run is unchanged.
- [ ] **Step 2–4:** fail, implement, `uv run pytest` green.
- [ ] **Step 5: Commit** — `git commit -m "feat(cli): am resume continues a whole milestone"`

### Task 4.2: Prove a killed milestone resumes under the fake claude

**Files:** Test: `tests/e2e/test_milestone_resume.py` (new), `tests/e2e/fake_claude.py` if a kill switch is needed (an env var that makes the fake exit abruptly in a named phase — scaffolding, never in a brief).

- [ ] **Step 1: Test** — run a three-story milestone (one multi-blocker story) under the fake claude; kill it (`BaseException` injected / process killed) while one lane is in `plan` and another in `implement`; `am resume <run-id>`; assert per phase and card the fake's invocation counts: nothing before the interrupted phases is dispatched again, the merged base is not merged again, the run ends `done` with `integrated`.
- [ ] **Step 2:** `uv run pytest` → green (this task adds only tests; any bug it finds is fixed here with its own failing test first).
- [ ] **Step 3: Commit** — `git commit -m "test(e2e): a killed milestone resumes where it stopped"`

---

## Story 5 — Documentation

### Task 5.1: Document dataflow scheduling, merged bases and milestone resume

**Files:** Modify `README.md`; add a pointer to the addendum in `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.

- [ ] **Step 1:** Read `orchestrate.py`, `bases.py`, `runtime/stop.py` and `cli.resume_run` as built. Document from the code, not the spec.
- [ ] **Step 2:** README: "Parallel runs" — stories start when their blockers finish (no level barriers; waves in the dry run are a preview); "Multiple blockers" — merged bases, their branch name, conflicts go to the resolver, how a failed base reads in the report; "Relaunching resumes" — `am resume <run-id>` continues a milestone under the same run id, and when it refuses; the dry run's `merged_from`; remove "am resume is not milestone-aware" and the two-blocker refusal from the dry-run section.
- [ ] **Step 3:** `uv run pytest` → green.  **Step 4: Commit** — `git commit -m "docs: dataflow scheduling, merged bases and milestone resume"`
