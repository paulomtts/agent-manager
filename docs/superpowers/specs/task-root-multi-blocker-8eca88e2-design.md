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
