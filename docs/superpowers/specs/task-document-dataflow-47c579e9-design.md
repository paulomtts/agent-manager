# Document dataflow scheduling, merged bases and milestone resume (card 47c579e9)

Subtask of story 03ac7831 "Documentation", milestone c2a981a3 "Milestone 7: the supervisor tree". Narrows plan Task 5.1 in `docs/superpowers/plans/2026-09-25-supervisor-tree.md`. Source of truth: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (T1-T10, sections 1-10). Documentation only.

## Scope

Files changed, and nothing else:

- `README.md`, the milestone-run sections listed below.
- `docs/superpowers/specs/2026-09-23-agent-manager-design.md`: one added line pointing at the supervisor-tree addendum (`2026-09-25-supervisor-tree-design.md`).

Document behavior as built. The source is `src/agent_manager/orchestrate.py` (`supervise`, `lane`, `base_only_lane`, `build_merged_base`, `blocker_tips`, `run_milestone`, `bases_payload`/`with_bases`), `src/agent_manager/bases.py`, `src/agent_manager/runtime/stop.py` and `cli.resume_run`/`cli.dry_run_payload`, all in this worktree. Where the code and the addendum disagree, the code wins, and the disagreement is noted in the commit body.

Out of scope: any code or test change. That includes the stale `cli.dry_run_payload` docstring (cli.py:884-886 still says the real run refuses a multi-blocker story), because this card is docs only. Also out of scope, per the milestone: verification discovery, live pause/cancel/watch/retry, more than one `am` process per repo, a grafo `max_workers` option, and leave-me-alone's multi-blocker support.

## Required README changes (observable result)

1. **Dataflow scheduling, not level barriers.** Every sentence that says levels are barriers, or that a level runs "after" another, changes. A story starts as soon as all of its own blockers have succeeded. Levels (waves) remain only as a preview and report grouping (`data.levels`). The affected text:
   - "What a clean run leaves behind": "Levels run one after another…" (README.md:110).
   - Integrate: "After the last level" (README.md:134). The new wording is "after every story".
   - "Parallel runs": `--max-concurrent` bounds stories running at once across the milestone, not "of one level" (README.md:174). Also the "Levels are barriers" bullet (README.md:181-182), and "a story of the same level… no later level starts" (README.md:192). The stop semantics must match the code: which stories stay `pending` once the stop is set.
   - README.md:201: this paragraph says `am resume` is not the way to continue a milestone. Rewrite it to offer both relaunch and `am resume <run-id>`.
   - The escalation report intro (README.md:219-220): the other lanes park, and no new story starts. `level` in the payload is the story's wave.
2. **Dry run (README.md:73-106).** Document the `merged_from` key: it appears on a story row only when its root is a merged base, lists the blockers in `blocked_by` order, and is absent otherwise. Add a `base` bullet saying a story with two or more blockers roots on its merged base branch. Remove "or a story blocked by two or more stories in the milestone" and "A stack roots on one branch only". Only a cycle is still refused with exit 3.
3. **New "Multiple blockers" section.** Place it after "Parallel runs". It covers:
   - The branch name `<prefix>/base-<short id of story>`.
   - How the base is built: the blockers' tips are merged in census order, then verified once.
   - A clean merge is silent.
   - A conflict goes to the Integrate resolver. In `am status`, that shows as a synthetic story titled "Merged bases" with subtask `base-<story id>`.
   - A `MergeInProgressError` escalates, and the human finishes the merge by hand and then resumes.
   - A story with one blocker keeps the fast path: no base branch and no extra verify.
   - The base branch never moves, and nothing is pushed.
   - A failed base in the escalation report: `failed_phase: "base"`, `subtask: null`.
   - The run's `data.bases` list, `[{"story","branch","blockers"}]`, which is present only when non-empty.
4. **"Relaunching resumes" (README.md:253-269).** `am resume <run-id>` on a `milestone` run continues the milestone under the same run id. It re-derives the plan from the board, reuses the recorded `branch_prefix`, `base_branch` and `max_concurrent_stories`, and reports `resumed: true`. Refusals, each with exit 3 and the `{"ok": false, "error": {...}}` envelope:
   - the run is `done`;
   - any open subtask or base-resolver checkpoint has a stale digest. This refuses the whole resume, and nothing is written.

   Resume of a `task` run is unchanged. The existing bullets become explicitly about `task` runs, including the one that refuses "more than one in-flight subtask". Delete README.md:269 ("`am resume` is not milestone-aware…").
5. **"Not there yet".** Delete the bullet "`am resume` is not milestone-aware." (README.md:273). Add the supervisor-tree addendum's deferred section to the "See section …" pointer list if that addendum has one. Do not invent one.

The docs must not contradict the invariants:

- only `orchestrate.py` imports grafo, and every Node has `timeout=None`;
- only the subtask is a pygents Agent;
- the base branch never moves, and nothing is pushed;
- one `am` process per repo.

Keep the house style: JSON envelope names, and exact key names in backticks.

## Error paths

None at runtime, since no code changes. The authoring error paths are these:

- README text that states behavior the code does not have. Guard against it by checking every claim against the modules named above and against the tests in `tests/test_bases.py`, `tests/test_orchestrate.py`, `tests/e2e/test_parallel_milestone.py` and `tests/e2e/test_milestone_resume.py`.
- Leftover stale text. Before committing, grep README.md for `barrier`, `not milestone-aware`, `two or more stories` and `level N+1`, and each must return nothing.

## Tests

No new tests. The testing section of the design spec (section 14: pure functions, steps, adapters, engine, and one opt-in end to end per feature) has no tier for prose. No test in the repo asserts README content: `tests/steps/test_docs_commit.py` covers the docs-commit step, not this README.

- Verification: `uv run pytest` passes in full, including `tests/e2e`. This proves that no code was touched by accident.
- Commit message: `docs: dataflow scheduling, merged bases and milestone resume`.

Note: the exploration findings passed to this stage were cut off at 8000 characters, partway through the test-placement rule. The testing tiers above were re-read directly from the design spec (lines 501-516), so the tier guidance here does not depend on the missing text.
