# Integrate — design addendum

Date: 2026-09-25
Extends: `2026-09-24-orchestration-design.md` (O6), `2026-09-24-parallel-stories-design.md`,
and `2026-09-23-agent-manager-design.md` (§2 "Never", §12)
Status: milestone 5 scope; decisions I1–I7

## 1. Why this shape

After `am run --milestone` finishes, every story is a stack of local branches
and the run reports one tip per story. With parallel stories (milestone 4) a
milestone can have several independent tips, and merging them by hand is the
tedious, error-prone step the old orchestrator's Integrate phase removed.

Integrate merges every story's tip into **one local branch** that a human then
reviews and merges into the base branch. It never touches the base branch and
never pushes.

## 2. What was found by reading the code

- `orchestrate.run_milestone` ends by returning `{done, levels, completed, tips,
  warnings}` and records the run `done`. `story_tips` exists and its docstring says
  it is "what a human merges after a clean run, since this card has no Integrate".
  `dag.compute_integrate_levels` (merge order over *all* stories, done or not) was
  ported in milestone 3 and is not called by anything.
- An agent phase cannot run bare. `dispatch.AgentRunner` journals every phase
  under a `(story_id, card_id)` pair, and `store.rebuild_from_journal` refuses a
  phase line whose story or subtask no earlier line created. A resolver
  attempt therefore needs a story row and a subtask row to hang from.
- `engine.run_subtask` already walks any workflow document with retries, gates,
  journalling and `am status`. A small `integrate.yaml` can reuse all of it.
- `worktree.ensure(branch, base, worktree, repo_dir)` already creates a branch
  from a base with the local-ref fallback for a repo with no `origin`, and is
  idempotent. The old `integrate.mjs` needed its own fix for that fallback (leave-
  me-alone #54); reusing `ensure` means it lives in one place here.
- The prompt renderer resolves inputs through a fixed table with no fallback, so a
  resolver's inputs (`merge_tip`, `conflict_files` and so on) must be added to it.

## 3. Decisions

**I1 — Integrate runs last, in one worktree, one merge at a time.** It starts only
when every story of the milestone is `done` (including stories finished by an
earlier run) and nothing escalated or stopped. The integration branch is
`<branch_prefix>-integrate`, in a worktree under `.claude/worktrees/`. Tips merge
in `dag.compute_integrate_levels` order (levels over *all* stories), and in census
order within a level. A story with no subtasks has no tip and is skipped. Merges
are sequential because they share one working tree.

**I2 — The merge step is deterministic, idempotent and relaunch-safe.**
`steps/integrate.py` creates or reuses the integration branch and worktree through
`worktree.ensure`, then runs `git merge --no-ff <tip>`. A tip already merged is a
no-op ("already up to date"), so a relaunch never re-merges. On a content conflict
it leaves the merge **in progress** (`MERGE_HEAD` set, markers in the tree) and
reports the conflicting files: that state is exactly what a resolver needs. A
non-conflict git failure (a bad ref, an I/O error) raises. It refuses to start a
merge while one is already in progress, since that is an unresolved conflict from
an earlier run: the run stops and says so, and a human resolves and commits, then
relaunches.

**I3 — Conflicts go to an agent, and git judges the result, not the agent.** A new
`resolver` role (system prompt, no vendored methodology) returns
`ResolveResult{resolved: bool, summary: str}`. Each conflict is driven through a
new builtin `integrate.yaml` (an agent phase `resolve`, then a deterministic
`verify`) with `engine.run_subtask`, against a **synthetic story "Integrate"** and
one **synthetic subtask per conflicting tip** (card = that story's id, branch =
the integration branch, base = the base branch, worktree = the integration
worktree). Attempts, retries, the journal and `am status` work unchanged. The
agent's `resolved` flag is advisory. A deterministic `merge_completed_gate`
measures with git: no `MERGE_HEAD`, a clean working tree, and no conflict markers
left in any file the merge touched. That is the rule the project already follows:
agents decide as little as possible, and anything git can measure is not left to
an agent's report. The `verify` phase then runs the verification commands in the
integration worktree through the existing `verification_passed_gate`.

**I4 — The integrated result is verified once more at the end.** After the last
merge the verification commands run once on the integrated branch. Two stories can
merge cleanly, in different files, and still break each other; the old
orchestrator verified only after a conflict resolution, so a clean-but-broken
combination shipped unchecked. The cost is one extra run of the repo's own tests.
A failure escalates at Integrate.

**I5 — Integrate never touches the base branch and never pushes.** Every story
branch is untouched by it. On any Integrate escalation the integration branch and
worktree stay exactly as they are, for a human to inspect or finish.

**I6 — The report and the run status.** On success the payload gains `integrated:
{branch, worktree, merged: [story ids], resolved: [story ids]}` and the run is
`done` only then. On failure the payload is `{escalated: true, phase: "integrate",
story, files, detail, ...}` and the run is `escalated`. When every story is already
`done`, `run_milestone` still runs Integrate, so relaunching after an Integrate
escalation retries it. `--dry-run` reports the integration plan (branch, worktree,
the ordered tips) and still writes nothing.

**I7 — Proof, in the same style as the earlier milestones.** Under the fake
`claude` (which must never know more than the brief tells it): two stories that
edit the same line, resolved by a fake resolver that reads the conflict list only
from its brief; a resolver that fails, leaving `MERGE_HEAD` in place and stopping
the run, and a relaunch after a human finishes the merge that completes; a
non-conflicting milestone that integrates with **no agent dispatched at all**; and
a combination that merges cleanly but breaks the suite, caught by the final
verification. One opt-in test (`pytest -m e2e`) forces a real conflict resolved by
a real agent. It is a human step.

## 4. Acceptance

1. A clean two-story milestone integrates onto `<prefix>-integrate` with no agent
   dispatched; the branch contains both stories' work and the base branch is
   untouched.
2. A conflicting pair is resolved by the resolver; the result has no markers, no
   `MERGE_HEAD` and a clean tree, and the suite passes.
3. A resolver that does not finish the merge escalates at Integrate with the merge
   left in progress; relaunching after a human commits completes.
4. A textually clean merge that breaks the suite escalates at Integrate.
5. A relaunch of a fully finished, already-integrated milestone changes nothing.
6. `--dry-run` shows the integration plan and writes nothing.
7. By hand, after the milestone: a real agent resolves a real conflict.

## 5. Limits stated plainly

- A resolver can misjudge what two edits *meant* even when the result compiles and
  passes. The final verification narrows that; it does not remove it. A human still
  reviews the integration branch before merging it, as with every branch here.
- Merges are sequential. That is a property of one working tree, not a choice.
- A milestone with hundreds of stories merges hundreds of tips; nothing batches them.

## 6. Deferred

- A `--no-integrate` option.
- A milestone-aware `resume`, plus `watch`, `retry` and `cancel`.
- Cost capture (real `claude -p` attempts record no tokens or cost).
- `am`'s reviewer brief never mentions `Plan-Hash`, and the `review` phase is not
  given the hash: the first real review that commits a fix will trip `review_gate`.
- The suite has grown to about three and a half minutes; the slowest tests should
  be marked so the pipeline can skip them.
- Per-story readiness in place of the level barrier.
