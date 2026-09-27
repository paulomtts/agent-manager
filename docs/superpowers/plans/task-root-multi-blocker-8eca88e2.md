<!-- task-pipeline: validated -->
# Root multi-blocker stories on merged bases (card 8eca88e2)

Subtask of story 92c0ab94 "The supervisor", milestone c2a981a3 "Milestone 7: the supervisor tree". Plan: `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 3.2. Design: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (T1-T10, section 5 merged bases, section 10 deferred). Builds on branch `m7/task-run-a-milestone-s-c0dbd454` (Task 3.1, the blocker), not master.

## Scope

This card only wires the pieces together. `dag.RootPlan`/`dag.story_root`/`dag.base_branch_name` (Groundwork) and `bases.build`/`BaseResult`/`BaseFailed` (Merged bases story) already exist. This card consumes them and does not change them. Source change is in `src/agent_manager/orchestrate.py` only.

1. Remove the Task 1.3 refusal. Delete `_merged_root_behind` and `_merged_root_error`, and the loop in `plan_levels` that raises `dag.StackRootError` for a story whose root is (or falls through to) `merged`. `plan_levels` keeps its cycle check and geometry. Update the docstrings of `plan_levels` and `supervisor_plan` so they no longer describe a refusal. `dag.py` is not touched.
2. Forward the tips. `supervise`'s node coroutine `run(**tips)` passes the forwarded `tip_<short id>` keywords on to `lane`, which gains a parameter for them named `forwarded_tips` (not `tips`: `lane` already reads the story's own tip off `plan.tips[story.id]`, a different mapping keyed by story id rather than `tip_<short id>`, and reusing the name would collide with it).
3. Build the base in `lane`. When `plan.roots[story.id].kind == "merged"` and the story is pending (in `plan.planned`), the lane takes its slot and checks the stop as it does today. Before its first subtask it then binds `root_plan = plan.roots[story.id]` (`lane`'s own parameter is already named `root: Path`, the repo directory, so the story's `RootPlan` needs a different name) and awaits `bases.build(root_plan, [forwarded_tips[f"tip_{dag.short_id(b)}"] for b in root_plan.blockers], repo_dir=root, commands=..., allow_no_verification=..., store=store, run_id=run_id, story_id=story.id, runner_factory=..., stop=stop)`. The tips go in `root_plan.blockers` order. The first remaining subtask's base is the recorded `planned.bases` entry, which `dag.stack_bases` already sets to `root_plan.branch` (`<prefix>/base-<short id>`). A pending story with a merged root but no remaining subtasks still builds its base before it returns its tip, so a dependent that falls through to that root finds the branch. The refusal used to cover that fall-through case.
4. Lone-blocker (`"tip"`) and no-blocker (`"base"`) stories are unchanged: no base branch and no extra verify (fast path, section 5).

## Observable behavior and error paths

- The stop has already fired when the lane gets its slot: the story is recorded `stopped` and the lane raises `LaneStopped`, as today. The base is never built.
- `BaseFailed(stopped=False)`: the lane calls `stop.trigger(story.id)`, records the story row `escalated` and raises `LaneEscalated`. The outcome has `failed_phase="base"`, `subtask=None` and `detail=error.detail`. No subtask row is marked escalated and no subtask is driven. Other running lanes park through the existing StopSignal path, and grafo starts no dependent of this story.
- `BaseFailed(stopped=True)`: the story is recorded `stopped` and the lane raises `LaneStopped` with `subtask=None`. This is not an escalation.
- Any other exception from `bases.build` goes to the existing catch-all: the stop is triggered and the lane escalates with `subtask=None` and detail `"<Type>: <msg>"`.
- A failed blocker lane: grafo never starts the merged-root story, so `collect_outcomes` reports it `pending`. `bases.build` is not called.
- Report: `run_milestone`'s returned `data` gains `"bases": [{"story": <id>, "branch": <root_plan.branch>, "blockers": [<blocker ids in root_plan.blockers order>]}, ...]`. It has one entry for each story whose lane built its merged base in this run, in wave order. The key is present only when the list is non-empty, whatever shape the payload has (done/integrated, lane-escalated, integrate-escalated). How the lane hands the `BaseResult` back (for example a field on `LaneOutcome`) is left to the plan.
- Invariants stay as they are: only `orchestrate.py` imports grafo, and every Node has `timeout=None`. `bases.build` is a plain async function, not an Agent. The milestone's base branch never moves and nothing is pushed.

## Out of scope

- Deleting `should_stop` (sibling dfc86724).
- Any change to `dag.py` root logic or to `bases.py` merge/conflict/verify logic.
- A done (non-pending) story whose merged base was never built. Milestone-wide resume owns that.
- Verification discovery, live `am pause`/`cancel`/`watch`/`retry`, more than one `am` process per repo, a grafo `max_workers` option, and multi-blocker support in leave-me-alone.

## Tests

Tier placement follows design spec section 14 as the findings apply it. Lane behavior goes in the Engine tier: `tests/test_orchestrate.py`, driven by `FakeDriver`, with `bases.build` monkeypatched where a canned result or failure is needed. Wiring through the real stack goes in the default-suite e2e tier: `tests/e2e/test_parallel_milestone.py`, unmarked, with the real CLI, adapter and launcher and only the fake `claude` on PATH. No test goes in `tests/test_bases.py` (Steps tier, owned by Merged bases) or `tests/test_dag.py` (owned by Groundwork). Ordering is proven by blocking on `asyncio.Event`, never by sleeps. The fake `claude` knows only what its brief says.

Engine tier (`tests/test_orchestrate.py`):
- Remove the four refusal tests: `test_plan_levels_refuses_a_story_with_two_in_milestone_blockers`, `test_plan_levels_refuses_a_story_rooted_through_a_subtask_less_story_on_two_blockers`, `test_plan_levels_refuses_a_two_blocker_story_with_todays_message_word_for_word` and `test_merged_root_is_refused`. In their place:
  - `plan_levels` returns levels for a two-blocker story, and its first subtask's base is `<prefix>/base-<short id>`.
  - The same holds for a story that falls through a subtask-less two-blocker story.
- A merged-root story runs only after both blockers. `bases.build` is called once, with the blockers' forwarded tips in `root_plan.blockers` order and `story_id` set to the story. Its first subtask is driven on the base branch. The report has `bases` with story, branch and blockers.
- A run with no merged root has no `bases` key.
- `BaseFailed(stopped=False)`: the story escalates at `failed_phase="base"` with `subtask=None` and the `BaseFailed` detail. The stop is triggered. No subtask of that story is driven. A concurrently running sibling lane (held on an Event) is recorded `stopped`.
- `BaseFailed(stopped=True)`: the story is `stopped` and not escalated, with `subtask=None`.
- A failed blocker leaves the merged-root story `pending`, and `bases.build` is never called.
- A pending subtask-less story with a merged root builds its base, and its dependent stacks on that branch.

Default-suite e2e tier (`tests/e2e/test_parallel_milestone.py`, plus a new board fixture in `tests/e2e/conftest.py`):
- The fixture is a milestone where C is blocked by both A and B. It is a new fixture beside `parallel_board`, so the existing lone-blocker tests keep covering the fast path.
- C runs only after A and B both finish, on `m7/base-<C>`. Git ancestry shows both tips inside the base, and C's first subtask branch descends from it. The milestone's base branch did not move.
- A failed B leaves C `pending`, and no `m7/base-<C>` branch exists.
- A base-resolver failure (A and B tips conflict, and the fake `claude` resolver fails) escalates C at `failed_phase="base"`. A sibling lane running at the same time is parked `stopped`.
- The run's report lists `bases` with C's entry.
- The whole default suite (`uv run pytest`, including `tests/e2e`) stays green.

---

# Root Multi-Blocker Stories on Merged Bases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a milestone run drive a story with two or more in-milestone blockers. Its lane builds the merged base with `bases.build` from the tips grafo forwards, stacks the story on that base, handles every `BaseFailed` outcome, and reports the bases it built.

**Architecture:** All source changes are in `src/agent_manager/orchestrate.py`. `plan_levels` no longer refuses merged roots. `supervise` forwards the grafo `tip_<short id>` keywords into `lane` as `forwarded_tips`. `lane` awaits a new `build_merged_base` helper, which calls `bases.build`, after it takes its slot and checks the stop and before its first subtask. It turns `BaseFailed` into `LaneEscalated(failed_phase="base")` or `LaneStopped`. A subtask-less, still-open story on a merged root gets its own `base_only_lane`, so a dependent that falls through to its root finds the branch. `LaneOutcome` gains `base: dag.RootPlan | None`. `bases_payload`/`with_bases` add the `bases` key to every payload shape.

**Tech Stack:** Python 3.12, grafo (`TreeExecutor`/`Node`), asyncio, pytest, `uv`, real temporary git + brd in the engine and e2e tiers, and the fake `claude` binary in e2e.

**Spec:** `docs/superpowers/specs/task-root-multi-blocker-8eca88e2-design.md` (reproduced verbatim above).

**Note on inputs:** The spec summary this plan was commissioned with was cut off at 2000 characters, which means the upstream stage ran past its brief. This plan was written from the spec file on disk, which was read in full, so no requirement depends on the truncated text.

## Global Constraints

- Source change is in `src/agent_manager/orchestrate.py` only. `dag.py` and `bases.py` are not touched. The stale refusal wording in the `dag.StackRootError` docstring (`dag.py:241-251`) and in the `cli.dry_run_payload` docstring (`cli.py:887-891`) stays, because the spec limits the source change to `orchestrate.py`. Mention it in the final review; do not edit it here.
- Only `orchestrate.py` imports grafo, and every `grafo.Node` is built with `timeout=None` (T10). `tests/test_orchestrate.py::test_only_orchestrate_imports_grafo` and `::test_every_node_has_no_timeout` already pin this.
- Only the subtask is a pygents Agent. `bases.build` is a plain async function, and the lanes/supervisor stay plain async functions.
- Every task leaves the WHOLE default suite green: `uv run pytest`, including `tests/e2e`.
- No test sleeps to prove ordering. Block fakes on `asyncio.Event`/`asyncio.Barrier` with the existing `_within` timeout wrapper.
- A fake `claude` never knows more than its brief tells it. Use only its existing test-controlled inputs: the review-fail marker, the implement-edits marker, the rendezvous, and `FAKE_CLAUDE_RESOLVER`.
- The milestone's base branch never moves and nothing is pushed.
- Branch name of a merged base: `<prefix>/base-<short id of story>`, always taken from `dag.story_root(...).branch` / `dag.base_branch_name`, never retyped in source.
- Tips are passed to `bases.build` in `root_plan.blockers` order.
- The `lane` parameter is named `forwarded_tips`, and the story's `RootPlan` is bound as `root_plan`. `root` stays the repo `Path`.
- The report key is `"bases": [{"story", "branch", "blockers"}]`. It is present only when non-empty, in every payload shape.
- Do not touch `should_stop` anywhere (sibling dfc86724).
- No new tests in `tests/test_bases.py` or `tests/test_dag.py`.

## Review Focus

1. The stop fires while a merged-root lane is queued for its slot (another story escalated). A person expects that story to be recorded `stopped` with nothing built: no `bases.build` call and no base branch. Pinned in Task 4 by `test_a_merged_lane_that_finds_the_stop_fired_never_builds_its_base`.
2. A subtask-less story on two blockers fails to build its base. The run must escalate at `base` and must not reach Integrate. Such a story has no wave and no store row, so a naive `collect_outcomes` would silently drop the escalation. Pinned in Task 5 by `test_a_subtask_less_storys_failed_base_escalates_the_run_and_its_dependent_stays_pending` and by the pure `collect_outcomes` test.
3. A relaunch where one blocker is already `done`. Its node forwards its existing tip, and that tip must be the one merged into the base in blocker order. Pinned in Task 3 by `test_a_done_blockers_existing_tip_goes_into_the_base`.
4. Production passes `runner_factory=None`. If the lane forwarded `None`, every base conflict would fail with "no resolver is available". A person expects the resolver to run, as it does for Integrate. Pinned in Task 3 (`call["runner_factory"] is cli.default_runner_factory` plus a given-factory test) and end to end in Task 6's resolver test, which requires a `resolve` entry in the fake log.
5. A base is built and then something later fails (a later subtask escalates, or Integrate escalates). The report must still list the base, because it now exists on disk. Pinned in Task 3 by `test_a_lane_that_escalates_after_building_its_base_still_lists_it` and `test_an_integrate_escalation_still_lists_the_bases_built`.

---

### Task 1: `plan_levels` plans merged roots instead of refusing them

**Files:**
- Modify: `src/agent_manager/orchestrate.py:21-24` (module docstring), `:278-351` (delete `_merged_root_behind`, `_merged_root_error`, rewrite `plan_levels`), `:405-408` (`supervisor_plan` docstring)
- Test: `tests/test_orchestrate.py:108-117`, `:128-164` (replace), `:1480-1511` (delete)

**Interfaces:**
- Consumes: `dag.stack_bases`, `dag.story_tip`, `dag.compute_levels`, `dag.assert_no_blocker_cycles` (unchanged).
- Produces: `orchestrate.plan_levels(stories, *, branch_prefix, base_branch) -> list[list[PlannedStory]]`. It no longer raises `dag.StackRootError`, and it still raises `dag.DependencyCycleError`.

- [ ] **Step 1: Replace the pure refusal tests with the positive ones**

In `tests/test_orchestrate.py`, replace the whole of `test_plan_levels_refuses_a_story_with_two_in_milestone_blockers` (lines 108-117) with:

```python
def test_plan_levels_roots_a_two_blocker_story_on_its_merged_base():
    """Supervisor-tree §5: a story with two in-milestone blockers is planned,
    not refused. Its first subtask stacks on its own merged base
    `<prefix>/base-<short id>`, the rest on the subtask before them."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31), _plan_subtask(32)], blocked_by=[b.id, "outside", a.id])

    levels = orchestrate.plan_levels([a, b, c], branch_prefix="m3", base_branch="main")

    assert [[planned.story.id for planned in level] for level in levels] == [[a.id, b.id], [c.id]]
    c_plan = levels[1][0]
    assert c_plan.bases == {
        _plan_id(31): "m3/base-00000003",
        _plan_id(32): _branch_of(c.subtasks[0]),
    }
    assert c_plan.tip == _branch_of(c.subtasks[-1])
```

Then replace both `test_plan_levels_refuses_a_story_rooted_through_a_subtask_less_story_on_two_blockers` and `test_plan_levels_refuses_a_two_blocker_story_with_todays_message_word_for_word` (lines 128-164; keep `test_plan_levels_refuses_a_blocker_cycle_before_any_geometry` between them untouched) with this single test:

```python
def test_plan_levels_roots_a_story_behind_a_subtask_less_two_blocker_story_on_that_base():
    """A subtask-less story on two blockers has a merged root, and a story it
    blocks falls through to it, as `dag.story_tip` does. The joined story has
    nothing to drive, so it is in no wave, and the story behind it lands in
    wave 0 and stacks on the joined story's base."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    joined = _plan_story(3, [], blocked_by=[a.id, b.id])
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[joined.id])

    levels = orchestrate.plan_levels([a, b, joined, d], branch_prefix="m3", base_branch="main")

    assert [[planned.story.id for planned in level] for level in levels] == [[a.id, b.id, d.id]]
    d_plan = levels[0][2]
    assert d_plan.bases == {_plan_id(41): "m3/base-00000003"}
```

Then delete the whole `test_merged_root_is_refused` function, including its two decorator lines `@requires_git` / `@requires_brd` (lines 1480-1511).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "roots_a_two_blocker_story_on_its_merged_base or roots_a_story_behind_a_subtask_less" -v`
Expected: 2 FAILED, each with `agent_manager.dag.StackRootError: dag: story #... is blocked by 2 stories`.

- [ ] **Step 3: Remove the refusal from `orchestrate.py`**

Delete `_merged_root_behind` and `_merged_root_error` entirely (lines 278-314). Replace `plan_levels` (lines 317-351) with:

```python
def plan_levels(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> list[list[PlannedStory]]:
    """Dispatch levels with each pending story's bases and tip, derived before any write.

    The same composition as `cli.dry_run_payload`: the cycle check runs first,
    because a cycle is what breaks the geometry, and `stories_by_id` covers
    every story, done ones included, so a story blocked by a done story still
    roots on that story's tip. A story rooted on a merged base -- its own, for
    two or more in-milestone blockers, or a subtask-less blocker's that it
    falls through to -- is planned like any other: `dag.stack_bases` roots its
    first subtask on that base branch, and the lane of the story that owns the
    base builds it before anything stacks on it (supervisor-tree §5).
    """
    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    stories_by_id = {story.id: story for story in stories}
    return [
        [
            PlannedStory(
                story=story,
                level=index,
                bases=dag.stack_bases(story, stories_by_id, branch_prefix, base_branch),
                tip=dag.story_tip(story, stories_by_id, branch_prefix, base_branch),
            )
            for story in level
        ]
        for index, level in enumerate(dag.compute_levels(stories))
    ]
```

In `supervisor_plan`'s docstring, replace:

```python
    Pure. `plan_levels` has already run the cycle check and refused `merged`
    roots for every pending story, so this derives geometry and refuses nothing.
```

with:

```python
    Pure. `plan_levels` has already run the cycle check, so this derives
    geometry and refuses nothing.
```

In the module docstring, replace:

```python
The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle, a story whose root would be a merged base -- runs before the
first write, so a refusal leaves no run directory, no store, no fetch and no
prune behind.
```

with:

```python
The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle -- runs before the first write, so a refusal leaves no run
directory, no store, no fetch and no prune behind.
```

- [ ] **Step 4: Run the orchestrate tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS. No test references `_merged_root_behind`, `_merged_root_error` or `test_merged_root_is_refused` any more.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): plan merged-root stories instead of refusing them"
```

---

### Task 2: `LaneOutcome.base` and the `bases` report helpers

**Files:**
- Modify: `src/agent_manager/orchestrate.py:83-104` (`LaneOutcome`), and insert after `integrate_escalated_payload` (after line ~218)
- Test: `tests/test_orchestrate.py` (pure section, right after `test_a_final_verification_escalation_payload_has_no_story`, ~line 320)

**Interfaces:**
- Consumes: `dag.RootPlan(kind, branch, blockers: tuple[str, ...])`.
- Produces:
  - `LaneOutcome.base: dag.RootPlan | None = None`. This is the last field, after `primary`.
  - `orchestrate.bases_payload(outcomes: Sequence[LaneOutcome]) -> list[dict[str, Any]]`. It returns `[{"story": str, "branch": str, "blockers": list[str]}]` in outcome order, for outcomes whose `base` is not None.
  - `orchestrate.with_bases(payload: dict[str, Any], built: list[dict[str, Any]]) -> dict[str, Any]`. It sets `payload["bases"] = built` only when `built` is non-empty, and returns `payload`.

- [ ] **Step 1: Write the failing pure tests**

Insert after `test_a_final_verification_escalation_payload_has_no_story`:

```python
def test_the_bases_payload_lists_every_built_base_in_outcome_order():
    """Spec, Report: one entry per lane that built its merged base, in the
    order `collect_outcomes` gave (wave order), blockers in `root_plan` order.
    An outcome with no base contributes nothing, whatever its kind."""
    first = dag.RootPlan("merged", "m3/base-00000003", (_plan_id(2), _plan_id(1)))
    second = dag.RootPlan("merged", "m3/base-00000005", (_plan_id(4), _plan_id(3)))
    outcomes = [
        orchestrate.LaneOutcome(kind="done", story=_plan_id(1), level=0),
        orchestrate.LaneOutcome(kind="done", story=_plan_id(3), level=1, base=first),
        orchestrate.LaneOutcome(
            kind="escalated", story=_plan_id(5), level=2, subtask=_plan_id(51), base=second
        ),
        orchestrate.LaneOutcome(kind="pending", story=_plan_id(6), level=2),
    ]

    assert orchestrate.bases_payload(outcomes) == [
        {"story": _plan_id(3), "branch": "m3/base-00000003", "blockers": [_plan_id(2), _plan_id(1)]},
        {"story": _plan_id(5), "branch": "m3/base-00000005", "blockers": [_plan_id(4), _plan_id(3)]},
    ]
    assert orchestrate.bases_payload([]) == []


def test_the_bases_key_is_added_only_when_a_base_was_built():
    entry = {"story": _plan_id(3), "branch": "m3/base-00000003", "blockers": [_plan_id(1)]}

    assert orchestrate.with_bases({"done": True}, []) == {"done": True}
    assert orchestrate.with_bases({"done": True}, [entry]) == {"done": True, "bases": [entry]}


def test_a_lane_outcome_has_no_base_by_default():
    assert orchestrate.LaneOutcome(kind="done", story="A", level=0).base is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "bases_payload or bases_key or has_no_base_by_default" -v`
Expected: FAIL with `TypeError: LaneOutcome.__init__() got an unexpected keyword argument 'base'` and `AttributeError: module 'agent_manager.orchestrate' has no attribute 'bases_payload'` / `'with_bases'` / `'LaneOutcome' object has no attribute 'base'`.

- [ ] **Step 3: Implement**

In `LaneOutcome`, add the field after `primary: bool = False`:

```python
    primary: bool = False
    base: dag.RootPlan | None = None
```

and extend its docstring's last sentence block, replacing:

```python
    `primary` marks the first escalation in `executor.errors`.
    """
```

with:

```python
    `primary` marks the first escalation in `executor.errors`. `base` is the
    merged base this lane built, as the story's `dag.RootPlan`, or None when it
    built none; `run_milestone` reports it under `bases` (supervisor-tree §5).
    """
```

After `integrate_escalated_payload`, add:

```python
def bases_payload(outcomes: Sequence[LaneOutcome]) -> list[dict[str, Any]]:
    """Every merged base a lane built this run, in the order the outcomes come.

    `collect_outcomes` gives wave order, so this is wave order. `blockers` is
    the story's `root_plan.blockers`, the order the tips were merged in.
    """
    return [
        {
            "story": outcome.story,
            "branch": outcome.base.branch,
            "blockers": list(outcome.base.blockers),
        }
        for outcome in outcomes
        if outcome.base is not None
    ]


def with_bases(payload: dict[str, Any], built: list[dict[str, Any]]) -> dict[str, Any]:
    """`payload` with `bases` set, only when a base was built: every payload
    shape carries the key on the same terms (spec, Report)."""
    if built:
        payload["bases"] = built
    return payload
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS, including `test_outcomes_follow_t6_in_wave_order`, which compares `LaneOutcome`s by equality and is unaffected by a defaulted field.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): carry a built merged base on LaneOutcome and report it"
```

---

### Task 3: Forward the tips and build the merged base in the lane (happy path + report)

**Files:**
- Modify: `src/agent_manager/orchestrate.py`: the import at line 48, the module docstring, a new `build_merged_base` before `lane` (~line 571), `lane` (571-703), `supervise` (706-774: docstring plus `run(**tips)`), `run_milestone` (868-912)
- Test: `tests/test_orchestrate.py` (import at line 40; new section appended at the end of the file)

**Interfaces:**
- Consumes: `bases.build(root: RootPlan, tips: list[str], *, repo_dir, commands, allow_no_verification, store, run_id, story_id, runner_factory, stop) -> BaseResult` (read off the `bases` module at call time); `bases_payload`, `with_bases`, `LaneOutcome.base` from Task 2.
- Produces:
  - `orchestrate.build_merged_base(story: census.StoryPlan, root_plan: dag.RootPlan, forwarded_tips: Mapping[str, str], *, store: Store, run_id: str, root: Path, commands: Sequence[str], allow_no_verification: bool, runner_factory: cli.RunnerFactory | None, stop: StopSignal) -> None`
  - `orchestrate.lane(..., finished: dict[str, LaneOutcome], forwarded_tips: Mapping[str, str]) -> str`. `forwarded_tips` is a new required keyword.
  - Test helpers `FakeBases`, the `fake_bases` fixture and `_root_plan(project, milestone, story_id) -> dag.RootPlan`, which Tasks 4 and 5 reuse.

- [ ] **Step 1: Add the test scaffolding and the failing tests**

In `tests/test_orchestrate.py`, change line 40 to:

```python
from agent_manager import bases, board, census, cli, dag, integration, models, orchestrate, paths
```

Append at the end of the file:

```python
# ── merged bases (supervisor-tree §5, card 8eca88e2) ────────────────────────


@dataclass
class FakeBases:
    """Stands in for `bases.build`, which the lane reads off `bases` at call time.

    Every call is recorded. `gates[story]` is awaited first with the call's
    `stop` (Events and Barriers, never sleeps). `outcomes[story]` is an
    exception to raise; with none the base counts as built and a
    `BaseResult` naming `root.branch` comes back. It touches no git: a
    `FakeDriver` never needs the branch to exist.
    """

    outcomes: dict[str, BaseException] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

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
    ) -> bases.BaseResult:
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


@pytest.fixture
def fake_bases(monkeypatch) -> FakeBases:
    recorder = FakeBases()
    monkeypatch.setattr(bases, "build", recorder)
    return recorder


def _root_plan(project: Path, milestone: str, story_id: str) -> dag.RootPlan:
    """The story's `RootPlan` as the run derives it: census order, `PREFIX`, `main`."""
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    by_id = {story.id: story for story in plan.stories}
    return dag.story_root(by_id[story_id], by_id, PREFIX, "main")


def _bases_entry(story_id: str, root_plan: dag.RootPlan) -> dict[str, Any]:
    return {"story": story_id, "branch": root_plan.branch, "blockers": list(root_plan.blockers)}


@requires_git
@requires_brd
def test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it(
    project, fake_bases
):
    """Spec, Engine tier: C (blocked by A and B) builds its base once, only
    after both blockers returned, from their forwarded tips in
    `root_plan.blockers` order; c1 stacks on the base, c2 on c1; the report
    lists the base."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 2}, blocked_by={"C": ["A", "B"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    c1, c2 = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    a_returned, b_returned = asyncio.Event(), asyncio.Event()

    async def both_blockers_returned(stop: StopSignal | None) -> None:
        assert a_returned.is_set() and b_returned.is_set(), (
            "C's base was built before both blockers finished"
        )

    fake_bases.gates[story_c] = both_blockers_returned
    driver = GatedDriver(returned={a1: a_returned, b1: b_returned})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    assert root_plan.kind == "merged"
    assert root_plan.branch == f"{PREFIX}/base-{dag.short_id(story_c)}"
    assert sorted(root_plan.blockers) == sorted([story_a, story_b])
    tips = {story_a: _branch(project, a1), story_b: _branch(project, b1)}
    (call,) = fake_bases.calls
    assert call["root"] == root_plan
    assert call["tips"] == [tips[blocker] for blocker in root_plan.blockers]
    assert call["story_id"] == story_c
    assert call["repo_dir"] == cli.resolve_repo_dir(project)
    assert call["run_id"] == result["run_id"]
    assert call["commands"] == []
    assert call["allow_no_verification"] is False
    assert isinstance(call["stop"], StopSignal)
    # Production passes no factory; the base's resolver gets production's.
    assert call["runner_factory"] is cli.default_runner_factory
    driven_on = {entry["card"]: entry["base"] for entry in driver.calls}
    assert driven_on[c1] == root_plan.branch
    assert driven_on[c2] == _branch(project, c1)
    assert result["bases"] == [_bases_entry(story_c, root_plan)]
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1], statuses[c2]) == ("done", "done", "done")


@requires_git
@requires_brd
def test_a_given_runner_factory_reaches_the_base_builder(project, fake_bases):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})

    result = _run(project, shape["milestone"], FakeDriver(), runner_factory=_no_resolver)

    assert result["done"] is True, result
    (call,) = fake_bases.calls
    assert call["runner_factory"] is _no_resolver


@requires_git
@requires_brd
def test_a_done_blockers_existing_tip_goes_into_the_base(project, fake_bases):
    """Review Focus 3: on a relaunch A is already done. Its node forwards the
    tip it already has, and that tip is merged in its blocker position."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    for card in (a1, story_a):
        board.set_status(card, "done", repo_dir=project)
    root_plan = _root_plan(project, shape["milestone"], story_c)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert result["done"] is True, result
    assert [call["card"] for call in driver.calls] == [b1, c1]
    tips = {story_a: _branch(project, a1), story_b: _branch(project, b1)}
    (call,) = fake_bases.calls
    assert call["tips"] == [tips[blocker] for blocker in root_plan.blockers]
    assert result["bases"] == [_bases_entry(story_c, root_plan)]


@requires_git
@requires_brd
def test_a_run_with_no_merged_root_builds_no_base_and_reports_no_bases(project, fake_bases):
    """Spec: a lone-blocker story stays the fast path, and the key is absent
    when no base was built."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"B": ["A"]})

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert fake_bases.calls == []
    assert "bases" not in result


@requires_git
@requires_brd
def test_a_lane_that_escalates_after_building_its_base_still_lists_it(project, fake_bases):
    """Review Focus 5: the base exists once built, so a lane-escalated payload
    lists it too."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (c1,) = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    driver = FakeDriver(outcomes={c1: ("verify", "suite red on the base")})

    result = _run(project, shape["milestone"], driver)

    assert result["escalated"] is True, result
    assert (result["story"], result["subtask"], result["failed_phase"]) == (story_c, c1, "verify")
    assert result["bases"] == [_bases_entry(story_c, root_plan)]


@requires_git
@requires_brd
def test_an_integrate_escalation_still_lists_the_bases_built(
    project, fake_bases, integrate_recorder
):
    """Review Focus 5: the integrate-escalated payload carries `bases` too."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    integrate_recorder.outcome = integration.IntegrateEscalation(
        story=story_c, files=["shared.txt"], detail="the resolver did not finish"
    )

    result = _run(project, shape["milestone"], FakeDriver())

    assert (result["escalated"], result["phase"]) == (True, "integrate"), result
    assert result["bases"] == [_bases_entry(story_c, root_plan)]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "merged_root_story_builds or given_runner_factory_reaches or done_blockers_existing_tip or no_merged_root_builds_no_base or escalates_after_building or integrate_escalation_still_lists" -v`
Expected: `test_a_run_with_no_merged_root_builds_no_base_and_reports_no_bases` PASSES already. It is a guard: nothing built a base before this task either. The other five FAIL. The lane never calls `bases.build`, so `(call,) = fake_bases.calls` raises `ValueError: not enough values to unpack (expected 1, got 0)`, or `result["bases"]` raises `KeyError: 'bases'`.

- [ ] **Step 3: Implement forwarding, the base build and the report**

Change the import at line 48 to:

```python
from agent_manager import bases, board, census, cli, dag, integration, models
```

In the module docstring, after the paragraph ending `...collect_outcomes reads every story's outcome after the tree ran (T6).`, add:

```python
A story with two or more in-milestone blockers roots on a merged base
(supervisor-tree §5): its lane awaits `bases.build` with the tips grafo
forwarded, after it took its slot and before its first subtask, so that
subtask stacks on `<prefix>/base-<short id>`. A lone-blocker story stays the
fast path: no base branch and no extra verify.
```

Insert before `async def lane(`:

```python
async def build_merged_base(
    story: census.StoryPlan,
    root_plan: dag.RootPlan,
    forwarded_tips: Mapping[str, str],
    *,
    store: Store,
    run_id: str,
    root: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    stop: StopSignal,
) -> None:
    """Await `bases.build` for one merged-root story (supervisor-tree §5).

    The tips are the ones grafo forwarded as `tip_<short id>`, taken in
    `root_plan.blockers` order. `bases.build` is read off its module at call
    time so a test can replace it. A `None` factory is production's,
    `cli.default_runner_factory`, read at call time as Integrate reads it, so
    a conflicting tip reaches the resolver instead of failing for a human.
    """
    factory = cli.default_runner_factory if runner_factory is None else runner_factory
    await bases.build(
        root_plan,
        [forwarded_tips[f"tip_{dag.short_id(blocker)}"] for blocker in root_plan.blockers],
        repo_dir=root,
        commands=list(commands),
        allow_no_verification=allow_no_verification,
        store=store,
        run_id=run_id,
        story_id=story.id,
        runner_factory=factory,
        stop=stop,
    )
```

In `lane`'s signature, add after `finished: dict[str, LaneOutcome],`:

```python
    forwarded_tips: Mapping[str, str],
```

In `lane`'s docstring, before the paragraph starting `Each subtask's open checkpoint is looked up first`, add:

```python
    A story whose root is `merged` awaits `build_merged_base` with
    `forwarded_tips` after it took its slot and before its first subtask; that
    subtask's recorded base is the merged base branch. The outcome then carries
    the story's `RootPlan` as `base`, whatever happens after.
```

Replace the lane body from `planned = plan.planned.get(story.id)` down to and including `for position, subtask in enumerate(planned.remaining):` with:

```python
    planned = plan.planned.get(story.id)
    if planned is None:
        return plan.tips[story.id]
    root_plan = plan.roots[story.id]
    story_row, subtask_rows = plan.rows[story.id]
    completed: list[str] = []
    warnings: list[str] = []
    built: dag.RootPlan | None = None

    def outcome(kind: LaneKind, subtask: str | None, **fields: Any) -> LaneOutcome:
        return LaneOutcome(
            kind=kind,
            story=story.id,
            level=planned.level,
            subtask=subtask,
            completed=tuple(completed),
            warnings=tuple(warnings),
            base=built,
            **fields,
        )

    async with slots:
        current: census.SubtaskPlan | None = None
        try:
            if root_plan.kind == "merged":
                await build_merged_base(
                    story,
                    root_plan,
                    forwarded_tips,
                    store=store,
                    run_id=run_id,
                    root=root,
                    commands=commands,
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    stop=stop,
                )
                built = root_plan
            for position, subtask in enumerate(planned.remaining):
```

(The rest of the loop body and the `except` clauses stay exactly as they are.)

In `supervise`, change `run` to forward the tips:

```python
            async def run(**tips: str) -> str:
                return await lane(
                    story,
                    plan=plan,
                    store=store,
                    run_id=run_id,
                    root=root,
                    drive=drive,
                    commands=commands,
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    slots=slots,
                    stop=stop,
                    finished=finished,
                    forwarded_tips=tips,
                )
```

and in its docstring replace `forwarding the blocker's tip as `tip_<short id>` (Task 3.2 reads those for merged bases).` with `forwarding the blocker's tip as `tip_<short id>`, which a merged-root story's lane hands to `bases.build`.`

In `run_milestone`, replace the block from `# Wave order, census order within a wave, never finish order.` down to the final `return {...}` (lines 868-912) with:

```python
        # Wave order, census order within a wave, never finish order.
        for outcome in outcomes:
            completed.extend(outcome.completed)
            warnings.extend(outcome.warnings)
        built_bases = bases_payload(outcomes)
        if any(outcome.kind == "escalated" for outcome in outcomes):
            store.record_run(run_record.model_copy(update={"status": "escalated"}))
            primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
            return with_bases(
                escalated_payload(run_id, primary, outcomes, warnings), built_bases
            )

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
            return with_bases(integrate_escalated_payload(run_id, outcome, warnings), built_bases)

        store.record_run(run_record.model_copy(update={"status": "done"}))
        return with_bases(
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
            },
            built_bases,
        )
```

- [ ] **Step 4: Run the orchestrate tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS, including the existing `test_each_blocker_forwards_its_tip_to_its_dependent` and `test_every_node_has_no_timeout`.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): build a merged-root story's base from its forwarded tips"
```

---

### Task 4: Stop and failure paths of the merged base

**Files:**
- Modify: `src/agent_manager/orchestrate.py`, `lane` (the `if root_plan.kind == "merged":` block added in Task 3, and the docstring paragraph added in Task 3)
- Test: `tests/test_orchestrate.py` (append after Task 3's tests)

**Interfaces:**
- Consumes: `bases.BaseFailed(detail, *, stopped=False)` with attributes `.detail: str` and `.stopped: bool`; `FakeBases`, `fake_bases` and `_root_plan` from Task 3.
- Produces: the lane contract. On `BaseFailed(stopped=False)` it raises `LaneEscalated(LaneOutcome(kind="escalated", subtask=None, failed_phase="base", detail=error.detail))` after `stop.trigger(story.id)`. On `BaseFailed(stopped=True)` it raises `LaneStopped(LaneOutcome(kind="stopped", subtask=None))`. If the stop has already fired, it raises `LaneStopped` at the first remaining subtask and does not call `bases.build`.

- [ ] **Step 1: Write the failing tests**

Append:

```python
@requires_git
@requires_brd
def test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling(
    project, fake_bases
):
    """Spec: `BaseFailed(stopped=False)` escalates C at `base` with no
    subtask, triggers the stop, drives no subtask of C, and D -- held in
    flight on an Event until the stop fires -- is recorded `stopped`."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 2}, blocked_by={"C": ["A", "B"]}
    )
    story_a, story_b, story_c, story_d = (shape["stories"][key] for key in "ABCD")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    d1, d2 = shape["subtasks"]["D"]
    d1_in_flight = asyncio.Event()

    async def hold_d1_until_the_stop(stop: StopSignal | None) -> None:
        d1_in_flight.set()
        await _await_stop(stop)

    async def fail_once_d1_is_in_flight(stop: StopSignal | None) -> None:
        await _within(d1_in_flight.wait(), "d1 in flight beside C's base")

    fake_bases.gates[story_c] = fail_once_d1_is_in_flight
    fake_bases.outcomes[story_c] = bases.BaseFailed("conflict nobody could resolve")
    driver = GatedDriver(gates={d1: hold_d1_until_the_stop})

    result = _run(project, shape["milestone"], driver, max_concurrent=3)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 1,
        "story": story_c,
        "subtask": None,
        "failed_phase": "base",
        "detail": "conflict nobody could resolve",
        "warnings": [],
        "stopped": [{"story": story_d, "subtask": d1, "before_phase": "implement"}],
    }
    assert c1 not in [call["card"] for call in driver.calls]
    assert len(fake_bases.calls) == 1
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "done",
        b1: "done",
        story_c: "escalated",
        c1: "pending",
        story_d: "stopped",
        d1: "stopped",
        d2: "pending",
    }


@requires_git
@requires_brd
def test_a_base_whose_resolver_was_stopped_ends_stopped_not_escalated(project, fake_bases):
    """Spec: `BaseFailed(stopped=True)` -- D escalates while C's base is being
    built; C's resolver parks, and C is `stopped` with no subtask."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 1}, blocked_by={"C": ["A", "B"]}
    )
    story_a, story_b, story_c, story_d = (shape["stories"][key] for key in "ABCD")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
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

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 0,
        "story": story_d,
        "subtask": d1,
        "failed_phase": "review",
        "detail": "d broke",
        "warnings": [],
        "stopped": [{"story": story_c, "subtask": None, "before_phase": None}],
    }
    assert c1 not in [call["card"] for call in driver.calls]
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "done",
        b1: "done",
        story_c: "stopped",
        c1: "pending",
        story_d: "escalated",
        d1: "escalated",
    }


@requires_git
@requires_brd
def test_a_merged_lane_that_finds_the_stop_fired_never_builds_its_base(project, fake_bases):
    """Review Focus 1: one slot. The three roots queue on it in census order;
    `joined` (blocked by the first two) is started only after the second
    returned, so it queues behind `last`. `last` escalates, and `joined` takes
    the slot with the stop already fired: stopped at j1, nothing built."""
    milestone = _add_card(project, "Milestone 3: orchestration")
    only_subtask: dict[str, str] = {}
    for key in ("P", "Q", "R"):
        story = _add_card(project, f"Story {key}", milestone)
        only_subtask[story] = _add_card(project, f"{key.lower()}1: only subtask of story {key}", story)
    first, second, last = _census_stories(project, milestone)
    joined = _add_card(project, "Story J: blocked by the first two", milestone)
    j1 = _add_card(project, "j1: only subtask of story J", joined)
    _block(project, joined, first)
    _block(project, joined, second)
    driver = GatedDriver(outcomes={only_subtask[last]: ("review", "the last root broke")})

    result = _run(project, milestone, driver, max_concurrent=1)

    assert fake_bases.calls == []
    assert j1 not in [call["card"] for call in driver.calls]
    assert (result["story"], result["subtask"]) == (last, only_subtask[last])
    assert result["stopped"] == [{"story": joined, "subtask": j1, "before_phase": None}]
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[joined], statuses[j1]) == ("stopped", "pending")


@requires_git
@requires_brd
def test_any_other_error_from_the_base_is_a_lane_escalation_with_no_subtask(
    project, fake_bases
):
    """Spec: a non-`BaseFailed` error goes to the catch-all: `"<Type>: <msg>"`,
    no subtask, no failed phase, and no subtask row escalated."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (c1,) = shape["subtasks"]["C"]
    fake_bases.outcomes[story_c] = RuntimeError("git fell over")

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["escalated"] is True, result
    assert (result["level"], result["story"], result["subtask"], result["failed_phase"]) == (
        1,
        story_c,
        None,
        None,
    )
    assert result["detail"] == "RuntimeError: git fell over"
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("escalated", "pending")


@requires_git
@requires_brd
def test_a_failed_blocker_leaves_the_merged_story_pending_and_builds_no_base(
    project, fake_bases
):
    """Spec: grafo never starts C when B escalated, so C is `pending` and
    `bases.build` is never called."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_b, story_c = shape["stories"]["B"], shape["stories"]["C"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    driver = FakeDriver(outcomes={b1: ("review", "b broke")})

    result = _run(project, shape["milestone"], driver)

    assert (result["story"], result["subtask"]) == (story_b, b1), result
    assert fake_bases.calls == []
    assert c1 not in [call["card"] for call in driver.calls]
    assert "bases" not in result
    statuses = _statuses(_load(project, result["run_id"]))
    assert (statuses[story_c], statuses[c1]) == ("pending", "pending")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "failed_base_escalates or resolver_was_stopped or finds_the_stop_fired or any_other_error_from_the_base or failed_blocker_leaves_the_merged" -v`
Expected:
- `test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling` FAILS: `failed_phase` is `None` and `detail` is `"BaseFailed: conflict nobody could resolve"`. The catch-all took it.
- `test_a_base_whose_resolver_was_stopped_ends_stopped_not_escalated` FAILS: C is escalated with `"BaseFailed: ..."` and appears as `also_escalated` instead of `stopped`.
- `test_a_merged_lane_that_finds_the_stop_fired_never_builds_its_base` FAILS: `fake_bases.calls` has one call, because the base is built before the per-subtask stop check.
- `test_any_other_error_from_the_base_is_a_lane_escalation_with_no_subtask` and `test_a_failed_blocker_leaves_the_merged_story_pending_and_builds_no_base` PASS already. They are guards: the catch-all and grafo's dataflow already give this behavior, and it must survive Step 3.

- [ ] **Step 3: Implement the stop check and the `BaseFailed` translation**

In `lane`, replace the Task 3 block:

```python
            if root_plan.kind == "merged":
                await build_merged_base(
                    story,
                    root_plan,
                    forwarded_tips,
                    store=store,
                    run_id=run_id,
                    root=root,
                    commands=commands,
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    stop=stop,
                )
                built = root_plan
```

with:

```python
            if root_plan.kind == "merged":
                # Checked before the base as before every subtask: a lane that
                # finds the stop fired builds nothing (spec, first error path).
                if stop.triggered:
                    store.record_story(story_row.model_copy(update={"status": "stopped"}))
                    raise LaneStopped(outcome("stopped", planned.remaining[0].id))
                try:
                    await build_merged_base(
                        story,
                        root_plan,
                        forwarded_tips,
                        store=store,
                        run_id=run_id,
                        root=root,
                        commands=commands,
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        stop=stop,
                    )
                except bases.BaseFailed as error:
                    # A parked resolver is a stop, not an escalation (P4).
                    if error.stopped:
                        store.record_story(story_row.model_copy(update={"status": "stopped"}))
                        raise LaneStopped(outcome("stopped", None)) from error
                    stop.trigger(story.id)
                    store.record_story(story_row.model_copy(update={"status": "escalated"}))
                    raise LaneEscalated(
                        outcome("escalated", None, failed_phase="base", detail=error.detail)
                    ) from error
                built = root_plan
```

In `lane`'s docstring, replace the paragraph added in Task 3:

```python
    A story whose root is `merged` awaits `build_merged_base` with
    `forwarded_tips` after it took its slot and before its first subtask; that
    subtask's recorded base is the merged base branch. The outcome then carries
    the story's `RootPlan` as `base`, whatever happens after.
```

with:

```python
    A story whose root is `merged` checks the stop once it has its slot (fired:
    `stopped` at its first subtask, nothing built), then awaits
    `build_merged_base` with `forwarded_tips` before its first subtask, whose
    recorded base is the merged base branch. `BaseFailed(stopped=False)`
    triggers the stop, records the story `escalated` and raises `LaneEscalated`
    with `failed_phase="base"`, no subtask and the failure's detail;
    `BaseFailed(stopped=True)` records it `stopped` and raises `LaneStopped`
    with no subtask. Any other error from the base reaches the catch-all with
    no subtask. Once built, the outcome carries the story's `RootPlan` as
    `base`, whatever happens after.
```

- [ ] **Step 4: Run the orchestrate tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): escalate a failed merged base at base, stop a parked one"
```

---

### Task 5: A subtask-less story on a merged root still builds its base

**Files:**
- Modify: `src/agent_manager/orchestrate.py`: new `builds_a_base_alone` and `base_only_lane` before `lane`, the top of `lane`, and `collect_outcomes` (427-472)
- Test: `tests/test_orchestrate.py` (the pure tests go after `test_an_error_that_is_no_lane_error_is_escalated_with_its_type_and_message`, ~line 430; the engine tests are appended at the end of the file)

**Interfaces:**
- Consumes: `build_merged_base` (Task 3), `LaneOutcome.base` (Task 2), `dag.is_story_closed`.
- Produces:
  - `orchestrate.builds_a_base_alone(story: census.StoryPlan, root_plan: dag.RootPlan) -> bool`. It is True iff `root_plan.kind == "merged"`, `story.subtasks` is empty, and the story is not closed.
  - `orchestrate.base_only_lane(story, root_plan, forwarded_tips, *, plan, store, run_id, root, commands, allow_no_verification, runner_factory, slots, stop, finished) -> str`. It returns `plan.tips[story.id]`, which is the base branch. Its outcomes have `level=None`, and no store row is written, because `record_plan` records only stories with work.
  - `collect_outcomes` appends, after the wave-ordered outcomes and before the foreign errors, the outcome of every census story that is not in `plan.planned` but has a finished, escalated or stopped outcome, in census order.

- [ ] **Step 1: Write the failing pure tests**

Insert after `test_an_error_that_is_no_lane_error_is_escalated_with_its_type_and_message`:

```python
def test_only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone():
    merged = dag.RootPlan("merged", "m3/base-00000003", (_plan_id(1), _plan_id(2)))
    lone = dag.RootPlan("tip", "m3/some-tip", (_plan_id(1),))

    assert orchestrate.builds_a_base_alone(_plan_story(3, []), merged) is True
    assert orchestrate.builds_a_base_alone(_plan_story(3, [], status="done"), merged) is False
    assert orchestrate.builds_a_base_alone(_plan_story(3, [_plan_subtask(31)]), merged) is False
    assert orchestrate.builds_a_base_alone(_plan_story(3, []), lone) is False


def test_a_base_only_lanes_outcome_follows_the_waves_in_census_order():
    """A subtask-less story is in no wave, so its lane's outcome comes after
    every wave's, before any foreign error; a failure is not dropped."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    joined = _plan_story(3, [], blocked_by=[a.id, b.id])
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[joined.id])
    plan = _supervisor_plan([a, b, joined, d])
    done_a = orchestrate.LaneOutcome(kind="done", story=a.id, level=0)
    done_b = orchestrate.LaneOutcome(kind="done", story=b.id, level=0)
    done_d = orchestrate.LaneOutcome(kind="done", story=d.id, level=0)
    built = orchestrate.LaneOutcome(
        kind="done", story=joined.id, level=None, base=plan.roots[joined.id]
    )
    every_output = {story.id: SimpleNamespace(output="tip") for story in (a, b, joined, d)}

    assert orchestrate.collect_outcomes(
        plan, every_output, [], {a.id: done_a, b.id: done_b, d.id: done_d, joined.id: built}
    ) == [done_a, done_b, done_d, built]

    failed = orchestrate.LaneOutcome(
        kind="escalated", story=joined.id, level=None, failed_phase="base", detail="broke"
    )
    outputs = {
        a.id: SimpleNamespace(output="tip"),
        b.id: SimpleNamespace(output="tip"),
        joined.id: SimpleNamespace(output=None),
        d.id: SimpleNamespace(output=None),
    }

    assert orchestrate.collect_outcomes(
        plan, outputs, [orchestrate.LaneEscalated(failed)], {a.id: done_a, b.id: done_b}
    ) == [
        done_a,
        done_b,
        orchestrate.LaneOutcome(kind="pending", story=d.id, level=0),
        replace(failed, primary=True),
    ]
```

Append the engine tests at the end of the file:

```python
@requires_git
@requires_brd
def test_a_subtask_less_story_on_two_blockers_builds_its_base_and_its_dependent_stacks_on_it(
    project, fake_bases
):
    """Spec: J has no subtasks and two blockers; D (blocked by J) falls through
    to J's merged base. J's lane builds it before D starts, D's first subtask
    stacks on it, and the report lists it."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_a, story_b, story_j = (shape["stories"][key] for key in "ABJ")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (d1,) = shape["subtasks"]["D"]
    root_plan = _root_plan(project, shape["milestone"], story_j)

    async def the_base_is_built(stop: StopSignal | None) -> None:
        assert [call["story_id"] for call in fake_bases.calls] == [story_j], (
            "d1 started before J's base was built"
        )

    driver = GatedDriver(gates={d1: the_base_is_built})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    assert root_plan.kind == "merged"
    tips = {story_a: _branch(project, a1), story_b: _branch(project, b1)}
    (call,) = fake_bases.calls
    assert call["root"] == root_plan
    assert call["tips"] == [tips[blocker] for blocker in root_plan.blockers]
    assert next(entry for entry in driver.calls if entry["card"] == d1)["base"] == root_plan.branch
    assert result["bases"] == [_bases_entry(story_j, root_plan)]


@requires_git
@requires_brd
def test_a_subtask_less_storys_failed_base_escalates_the_run_and_its_dependent_stays_pending(
    project, fake_bases, integrate_recorder
):
    """Review Focus 2: J is in no wave and has no store row, yet its failed
    base must escalate the run, never reach Integrate."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    story_a, story_b, story_j, story_d = (shape["stories"][key] for key in "ABJD")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (d1,) = shape["subtasks"]["D"]
    fake_bases.outcomes[story_j] = bases.BaseFailed("J's base broke")
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": None,
        "story": story_j,
        "subtask": None,
        "failed_phase": "base",
        "detail": "J's base broke",
        "warnings": [],
    }
    assert d1 not in [call["card"] for call in driver.calls]
    assert integrate_recorder.calls == []
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "done",
        a1: "done",
        story_b: "done",
        b1: "done",
        story_d: "pending",
        d1: "pending",
    }


@requires_git
@requires_brd
def test_a_closed_subtask_less_story_builds_no_base(project, fake_bases):
    """Spec, Out of scope: a done story's missing base is milestone-wide
    resume's; this lane only returns its tip."""
    shape = _milestone(
        project,
        {"A": 1, "B": 1, "J": 0, "D": 1},
        blocked_by={"J": ["A", "B"], "D": ["J"]},
    )
    board.set_status(shape["stories"]["J"], "done", repo_dir=project)

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert fake_bases.calls == []
    assert "bases" not in result
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "builds_a_base_alone or base_only_lanes_outcome or subtask_less_story_on_two_blockers_builds or subtask_less_storys_failed_base or closed_subtask_less_story" -v`
Expected:
- `test_only_an_open_subtask_less_story_on_a_merged_root_builds_a_base_alone` FAILS: `AttributeError: ... has no attribute 'builds_a_base_alone'`.
- `test_a_base_only_lanes_outcome_follows_the_waves_in_census_order` FAILS: `built` and the escalation are missing from the list, which has 3 items instead of 4.
- `test_a_subtask_less_story_on_two_blockers_builds_its_base_and_its_dependent_stacks_on_it` FAILS. J's lane returns its tip without building, so d1's gate raises "d1 started before J's base was built" and the run escalates.
- `test_a_subtask_less_storys_failed_base_escalates_the_run_and_its_dependent_stays_pending` FAILS: the run ends `done`.
- `test_a_closed_subtask_less_story_builds_no_base` PASSES already. It is a guard for the boundary.

- [ ] **Step 3: Implement the base-only lane and extend `collect_outcomes`**

Insert before `async def lane(` (after `build_merged_base`):

```python
def builds_a_base_alone(story: census.StoryPlan, root_plan: dag.RootPlan) -> bool:
    """Whether a story with no subtasks must still build its merged base.

    Such a story is in no wave (`dag.compute_levels` drops it), but a story it
    blocks falls through to its root, so the branch must exist before that
    dependent runs. A closed story is left alone: a done story whose base was
    never built is milestone-wide resume's.
    """
    return (
        root_plan.kind == "merged"
        and not story.subtasks
        and not dag.is_story_closed(story)
    )


async def base_only_lane(
    story: census.StoryPlan,
    root_plan: dag.RootPlan,
    forwarded_tips: Mapping[str, str],
    *,
    plan: SupervisorPlan,
    store: Store,
    run_id: str,
    root: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    slots: asyncio.Semaphore,
    stop: StopSignal,
    finished: dict[str, LaneOutcome],
) -> str:
    """A subtask-less story's lane: build its merged base, return it as its tip.

    It takes a slot, since a base can dispatch a resolver, and checks the stop
    first. The failure paths are `lane`'s for a merged base, but with no
    subtask to name and no store row to write -- `record_plan` records only
    stories with work -- so every outcome has `level=None` and
    `collect_outcomes` reports it after the waves.
    """

    def outcome(kind: LaneKind, **fields: Any) -> LaneOutcome:
        return LaneOutcome(kind=kind, story=story.id, level=None, **fields)

    async with slots:
        if stop.triggered:
            raise LaneStopped(outcome("stopped"))
        try:
            await build_merged_base(
                story,
                root_plan,
                forwarded_tips,
                store=store,
                run_id=run_id,
                root=root,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                stop=stop,
            )
        except bases.BaseFailed as error:
            if error.stopped:
                raise LaneStopped(outcome("stopped")) from error
            stop.trigger(story.id)
            raise LaneEscalated(
                outcome("escalated", failed_phase="base", detail=error.detail)
            ) from error
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            stop.trigger(story.id)
            raise LaneEscalated(
                outcome("escalated", detail=f"{type(error).__name__}: {error}")
            ) from error
    finished[story.id] = outcome("done", base=root_plan)
    return plan.tips[story.id]
```

At the top of `lane`, replace:

```python
    planned = plan.planned.get(story.id)
    if planned is None:
        return plan.tips[story.id]
    root_plan = plan.roots[story.id]
```

with:

```python
    root_plan = plan.roots[story.id]
    planned = plan.planned.get(story.id)
    if planned is None:
        if builds_a_base_alone(story, root_plan):
            return await base_only_lane(
                story,
                root_plan,
                forwarded_tips,
                plan=plan,
                store=store,
                run_id=run_id,
                root=root,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                slots=slots,
                stop=stop,
                finished=finished,
            )
        return plan.tips[story.id]
```

and in `lane`'s docstring replace `A story with nothing left to run returns its tip without taking a slot.` with `A story with nothing left to run returns its tip without taking a slot, unless `builds_a_base_alone` says it must first build its merged base (`base_only_lane`).`

In `collect_outcomes`, replace:

```python
            else:
                outcomes.append(LaneOutcome(kind="pending", story=story_id, level=planned.level))
    return outcomes + foreign
```

with:

```python
            else:
                outcomes.append(LaneOutcome(kind="pending", story=story_id, level=planned.level))
    # A base-only lane (`base_only_lane`) belongs to no wave: its outcome
    # follows the waves, in census order, so a failed base is never dropped.
    for story in plan.stories:
        if story.id in plan.planned:
            continue
        if story.id in finished:
            outcomes.append(finished[story.id])
        elif story.id in escalated:
            outcomes.append(escalated[story.id])
        elif story.id in stopped:
            outcomes.append(stopped[story.id])
    return outcomes + foreign
```

and in its docstring replace `One outcome per pending story in wave order, then one per foreign error (T6).` with `One outcome per pending story in wave order, then one per base-only lane that ran, in census order, then one per foreign error (T6).`

- [ ] **Step 4: Run the orchestrate tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS, including the existing `test_a_story_behind_a_subtask_less_story_waits_for_the_blocker_beneath` (J has one blocker there, a `"tip"` root, so it takes no base-only lane).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): build a subtask-less story's merged base for its dependents"
```

---

### Task 6: Default-suite e2e proof through the production wiring

**Files:**
- Modify: `tests/e2e/conftest.py` (the import at line 24; a new fixture `merged_base_board` after `parallel_board`, ~line 399)
- Test: `tests/e2e/test_parallel_milestone.py` (the module docstring; new helpers and three tests inserted just before `test_no_rendezvous_is_left_armed_for_later_tests`, which must stay last)

**Interfaces:**
- Consumes: the whole Tasks 1-5 behavior through `am run --milestone` (`run_milestone_cli`), plus the existing fixtures `fresh_project`, `rendezvous`, `fake_resolver`, `read_fake_log`, and the constants `UNION_ATTRIBUTE`, `FAKE_REVIEW_FAIL_MARKER`, `FAKE_IMPLEMENT_EDITS_MARKER`, `MILESTONE_PREFIX`.
- Produces: the fixture `merged_base_board -> dict` with keys `root`, `milestone`, `stories` {A,B,C,D}, `subtasks` {A:[a1], B:[b1], C:[c1], D:[d1,d2,d3]}, `branches` {subtask id: branch}, `base_branch` (C's merged base from `dag.story_root`), `merged_from` (C's blockers in `root_plan.blockers` order), `review_fail_marker`, `implement_edits_marker`.

These tests exercise behavior that Tasks 1-5 already implemented, so they are expected to PASS on their first run. They are the wiring proof the spec asks for. A failure here points at production wiring, for example the runner factory not reaching `bases.build`, and not at the tests. Diagnose it with superpowers:systematic-debugging before changing anything.

- [ ] **Step 1: Add the fixture**

In `tests/e2e/conftest.py`, change line 24 to:

```python
from agent_manager import board, census, cli, dag, models, paths, store
```

Insert after the `parallel_board` fixture:

```python
@pytest.fixture
def merged_base_board(fresh_project) -> dict[str, Any]:
    """One milestone: A (a1) and B (b1) independent, C (c1) blocked by BOTH, D (d1 -> d2 -> d3) independent.

    C is the only multi-blocker story, so its lane builds the merged base from
    A's and B's tips before c1 runs (supervisor-tree §5). D is a sibling lane
    with more work than A and B, so it is still running when C's base is
    built. This is a new fixture beside `parallel_board`, which keeps covering
    the lone-blocker fast path. `base_branch` and `merged_from` come from
    `dag.story_root` over the census, never retyped: `merged_from` is C's
    blockers in the order brd reports them, the order their tips are merged
    in. `UNION_ATTRIBUTE` folds the fake coder's `IMPLEMENTATION.md`, so only
    the implement-edits marker's own files can make A's and B's tips conflict.
    """
    root = fresh_project
    attributes = root / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(UNION_ATTRIBUTE, encoding="utf-8")
    milestone = _add_card(root, "Milestone 7: a merged base under a fake claude")
    a = _add_card(root, "Story A: one blocker of story C", milestone)
    b = _add_card(root, "Story B: the other blocker of story C", milestone)
    c = _add_card(root, "Story C: blocked by stories A and B", milestone, blocked_by=[a, b])
    d = _add_card(root, "Story D: a sibling lane beside them", milestone)
    a1 = _add_card(root, "a1: only subtask of story A", a)
    b1 = _add_card(root, "b1: only subtask of story B", b)
    c1 = _add_card(root, "c1: only subtask of story C", c)
    d1 = _add_card(root, "d1: first subtask of story D", d)
    d2 = _add_card(root, "d2: second subtask of story D", d, blocked_by=[d1])
    d3 = _add_card(root, "d3: third subtask of story D", d, blocked_by=[d2])
    subtasks = {"A": [a1], "B": [b1], "C": [c1], "D": [d1, d2, d3]}
    branches = {
        card_id: dag.task_branch(MILESTONE_PREFIX, board.show(card_id, repo_dir=root))
        for chain in subtasks.values()
        for card_id in chain
    }
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=root))
    by_id = {story.id: story for story in plan.stories}
    c_root = dag.story_root(by_id[c], by_id, MILESTONE_PREFIX, "main")
    assert c_root.kind == "merged", c_root
    return {
        "root": root,
        "milestone": milestone,
        "stories": {"A": a, "B": b, "C": c, "D": d},
        "subtasks": subtasks,
        "branches": branches,
        "base_branch": c_root.branch,
        "merged_from": list(c_root.blockers),
        "review_fail_marker": root / ".git" / FAKE_REVIEW_FAIL_MARKER,
        "implement_edits_marker": root / ".git" / FAKE_IMPLEMENT_EDITS_MARKER,
    }
```

- [ ] **Step 2: Write the e2e tests**

In `tests/e2e/test_parallel_milestone.py`, replace the module docstring's last paragraph:

```python
Each test builds its own repo and board (`parallel_board`): A (a1 -> a2) and B
(b1 -> b2) are independent roots, C (c1) is blocked by A. Levels are waves in
the report only; C is scheduled by its blocker A (supervisor-tree T1).
"""
```

with:

```python
Each test builds its own repo and board. On `parallel_board`, A (a1 -> a2) and
B (b1 -> b2) are independent roots and C (c1) is blocked by A alone: the
lone-blocker fast path. Levels are waves in the report only; C is scheduled by
its blocker A (supervisor-tree T1). On `merged_base_board`, C (c1) is blocked
by both A (a1) and B (b1), so its lane builds a merged base from their tips
before c1 runs (supervisor-tree §5), while D (d1 -> d2 -> d3) runs beside.
"""
```

Insert immediately before `def test_no_rendezvous_is_left_armed_for_later_tests():`:

```python
SHARED = "shared.txt"
BASE_LINE = "the line both blockers rewrite\n"
A_LINE = "story A rewrote this line\n"
B_LINE = "story B rewrote this line\n"


def _local_branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _merge_in_progress(worktree: Path) -> bool:
    """Whether git holds a MERGE_HEAD in `worktree`."""
    probe = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def test_a_story_blocked_by_two_stories_runs_on_their_merged_base(
    merged_base_board, run_milestone_cli
):
    """C runs only after A and B both finished, on its merged base: both
    finished tips are inside the base, c1 descends from it, and the report
    lists it. A clean merge dispatches no resolver; main never moves."""
    root = merged_base_board["root"]
    stories = merged_base_board["stories"]
    branches = merged_base_board["branches"]
    base = merged_base_board["base_branch"]
    (a1,) = merged_base_board["subtasks"]["A"]
    (b1,) = merged_base_board["subtasks"]["B"]
    (c1,) = merged_base_board["subtasks"]["C"]
    main_before = _git(root, "rev-parse", "main").strip()

    result = run_milestone_cli(root, merged_base_board["milestone"], max_concurrent=3)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert data["bases"] == [
        {"story": stories["C"], "branch": base, "blockers": merged_base_board["merged_from"]}
    ]
    assert base in _local_branches(root)
    assert _is_ancestor(root, branches[a1], base)
    assert _is_ancestor(root, branches[b1], base)
    assert _is_ancestor(root, base, branches[c1])
    run = _load_run(root, data["run_id"])
    assert _subtask_rows(run)[c1].base_branch == base
    assert "bases" not in {story.card_id for story in run.stories}
    assert stories["C"] in data["integrated"]["merged"]
    for card_id in _all_cards(merged_base_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
    assert _git(root, "rev-parse", "main").strip() == main_before


def test_a_failed_blocker_leaves_the_merged_story_pending_with_no_base(
    merged_base_board, run_milestone_cli
):
    """B's review fails: grafo never starts C, so C stays pending, and no
    merged base branch or worktree is ever made."""
    root = merged_base_board["root"]
    stories = merged_base_board["stories"]
    branches = merged_base_board["branches"]
    base = merged_base_board["base_branch"]
    (b1,) = merged_base_board["subtasks"]["B"]
    (c1,) = merged_base_board["subtasks"]["C"]
    merged_base_board["review_fail_marker"].write_text(f"{branches[b1]}\n", encoding="utf-8")
    main_before = _git(root, "rev-parse", "main").strip()

    result = run_milestone_cli(root, merged_base_board["milestone"], max_concurrent=3)

    assert result.exit_code == cli.EXIT_ESCALATED, (result.output, result.exception)
    data = _envelope(result)
    assert (data["story"], data["subtask"], data["failed_phase"]) == (
        stories["B"],
        b1,
        "review",
    ), data
    assert "bases" not in data
    run = _load_run(root, data["run_id"])
    story_status = {story.card_id: story.status for story in run.stories}
    assert story_status[stories["C"]] == "pending"
    rows = _subtask_rows(run)
    assert rows[c1].status == "pending"
    assert rows[c1].phases == []
    assert base not in _local_branches(root)
    assert not cli.worktree_for(root, base).exists()
    assert "bases" not in story_status
    assert _git(root, "rev-parse", "main").strip() == main_before


def test_a_base_the_resolver_cannot_finish_escalates_the_story_at_base_and_parks_a_sibling(
    merged_base_board, rendezvous, fake_resolver, run_milestone_cli, read_fake_log
):
    """A's and B's tips rewrite the same line; the base's resolver (the fake,
    told to refuse) leaves the merge unfinished. C escalates at `base` with no
    subtask, the merge is left in the base worktree, and D -- still running,
    with far more work left than C's single resolve -- is parked.

    The rendezvous (count 3) makes a1, b1 and d1 leave implement together, so
    D has d1's later phases plus every phase of d2 and d3 left when C's base
    fails; the assertions read which boundary D parked at out of the report.
    """
    root = merged_base_board["root"]
    stories = merged_base_board["stories"]
    branches = merged_base_board["branches"]
    base = merged_base_board["base_branch"]
    (a1,) = merged_base_board["subtasks"]["A"]
    (b1,) = merged_base_board["subtasks"]["B"]
    (c1,) = merged_base_board["subtasks"]["C"]
    (root / SHARED).write_text(BASE_LINE, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed the line both blockers rewrite")
    main_before = _git(root, "rev-parse", "main").strip()
    merged_base_board["implement_edits_marker"].write_text(
        json.dumps({branches[a1]: {SHARED: A_LINE}, branches[b1]: {SHARED: B_LINE}}),
        encoding="utf-8",
    )
    fake_resolver.refuse()
    rendezvous.arm(3)

    result = run_milestone_cli(root, merged_base_board["milestone"], max_concurrent=3)

    assert result.exit_code == cli.EXIT_ESCALATED, (result.output, result.exception)
    data = _envelope(result)
    assert data["escalated"] is True, data
    assert (data["level"], data["story"], data["subtask"], data["failed_phase"]) == (
        1,
        stories["C"],
        None,
        "base",
    ), data
    assert base in data["detail"]
    assert "also_escalated" not in data, data
    assert "bases" not in data
    assert "stopped" in data, (
        "lane D finished before C's base failed, so nothing was stopped; the "
        "ordering margin this test relies on was lost",
        data,
    )
    (parked,) = data["stopped"]
    assert parked["story"] == stories["D"]
    assert parked["subtask"] in merged_base_board["subtasks"]["D"]

    run = _load_run(root, data["run_id"])
    story_status = {story.card_id: story.status for story in run.stories}
    assert story_status[stories["C"]] == "escalated"
    assert story_status[stories["D"]] == "stopped"
    rows = _subtask_rows(run)
    assert rows[c1].status == "pending"
    assert rows[c1].phases == []
    assert not cli.worktree_for(root, branches[c1]).exists()

    base_worktree = cli.worktree_for(root, base)
    assert _merge_in_progress(base_worktree)
    resolves = [entry for entry in read_fake_log(data["run_id"]) if entry["phase"] == "resolve"]
    assert resolves  # non-vacuity: production's resolver really was dispatched
    assert {Path(entry["cwd"]).resolve() for entry in resolves} == {base_worktree.resolve()}
    assert _git(root, "rev-parse", "main").strip() == main_before
```

- [ ] **Step 3: Run the e2e module**

Run: `uv run pytest tests/e2e/test_parallel_milestone.py -v`
Expected: all PASS, including the three new tests, the existing `parallel_board` tests (the lone-blocker fast path is unchanged), and `test_no_rendezvous_is_left_armed_for_later_tests`, which is still the last test in the module.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_parallel_milestone.py
git commit -m "test(e2e): prove merged bases through the production wiring"
```

---

### Task 7: Full verification

**Files:**
- None modified.

- [ ] **Step 1: Run the whole default suite**

Run: `uv run pytest`
Expected: every test passes, `tests/e2e` included, and nothing is skipped beyond what was already skipped on the base branch.

- [ ] **Step 2: Confirm the change surface**

Run: `git diff --stat m7/task-run-a-milestone-s-c0dbd454...HEAD`
Expected: only these files changed: `src/agent_manager/orchestrate.py`, `tests/test_orchestrate.py`, `tests/e2e/conftest.py`, `tests/e2e/test_parallel_milestone.py`, plus the spec and plan documents under `docs/superpowers/`. There are no changes to `dag.py`, `bases.py`, `cli.py`, `runtime/`, `tests/test_bases.py` or `tests/test_dag.py`.

- [ ] **Step 3: Confirm the invariants by grep**

Run: `uv run pytest tests/test_orchestrate.py -k "only_orchestrate_imports_grafo or every_node_has_no_timeout or never_import_pygents" -v`
Expected: 3 PASS.
