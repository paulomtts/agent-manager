"""The builtin `task` workflow as declared phase-model data (spec G3).

Every callable is the real function object, never a name looked up at run
time, because the digest names callables by `module.qualname` and a resumed
run must be able to tell whether the workflow it checkpointed is this one.
`tests/workflow/test_declared.py` pins the timeouts, the critics' `on_fail`
loops and that the workflow validates.

G2 requires every agent turn timeout to exceed the launcher's strictly (the
launcher must kill `claude -p` before the turn is cancelled), so each phase
gets `max(chosen, floor)` with the floor five minutes above the launcher
timeout. The launcher's is never lowered.

G4: `validate_spec` loops back to `spec` and `validate_plan` to `plan`, at
most once each (`Goto`'s default `max_loops=1`); a second block escalates
`validation` as before. Review does not loop. The looped-to phase reads the
critic's reason through its `feedback` input; with no loop that input is empty
and renders no section, so a clean run's briefs are unchanged.

No pygents import here (rule 1).
"""

from __future__ import annotations

from datetime import timedelta

from agent_manager import dispatch, results
from agent_manager.steps import docs_commit, plan_check, reducers, rollup, verify, worktree
from agent_manager.workflow.phases import LAUNCHER_MARGIN, AgentPhase, Goto, Retry, Step, Workflow

LAUNCHER_TIMEOUT = timedelta(seconds=dispatch.DEFAULT_TIMEOUT)
"""How long the launcher lets one `claude -p` run before killing it (1800 s)."""

AGENT_TIMEOUT_FLOOR = LAUNCHER_TIMEOUT + LAUNCHER_MARGIN
"""The lowest agent-phase timeout: strictly above `LAUNCHER_TIMEOUT` (G2)."""


def agent_timeout(minutes: int) -> timedelta:
    """The chosen timeout, raised to `AGENT_TIMEOUT_FLOOR` if it is not above it."""
    return max(timedelta(minutes=minutes), AGENT_TIMEOUT_FLOOR)


TASK = Workflow("task", (
    Step("worktree", worktree.ensure, args={"fast_forward": True}),
    AgentPhase(
        "explore",
        role="explorer",
        inputs=("card", "parent_story", "repo_docs", "verification"),
        result=results.ExploreResult,
        gates=(reducers.exploration_output_gate, reducers.verification_gate),
        retry=Retry(2, ("schema_invalid", "gate_failed")),
        timeout=agent_timeout(20),
    ),
    Step("mark_in_progress", rollup.set_status, args={"status": "in_progress"}, best_effort=True),
    Step(
        "plan_check",
        plan_check.find_validated_plan,
        when=plan_check.has_validated_plan,
        skip_to="docs_commit",
    ),
    AgentPhase(
        "spec",
        role="spec_author",
        inputs=("card", "explore", "spec_path", "feedback"),
        result=results.SpecResult,
        writes="docs/superpowers/specs/{stem}.md",
        timeout=agent_timeout(30),
    ),
    AgentPhase(
        "validate_spec",
        role="spec_critic",
        inputs=("card", "spec_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
        on_fail=Goto("spec"),
    ),
    AgentPhase(
        "plan",
        role="planner",
        inputs=("spec_path", "plan_path", "feedback"),
        result=results.PlanResult,
        writes="docs/superpowers/plans/{stem}.md",
        timeout=agent_timeout(30),
    ),
    AgentPhase(
        "validate_plan",
        role="plan_critic",
        inputs=("spec_path", "plan_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
        on_fail=Goto("plan"),
    ),
    Step("mark_validated", plan_check.mark_validated),
    Step("docs_commit", docs_commit.commit_documents, gates=(reducers.stale_branch_gate,)),
    AgentPhase(
        "implement",
        role="coder",
        inputs=("plan_path", "spec_path", "branch", "base_branch", "plan_hash"),
        result=results.ImplementResult,
        gates=(reducers.implement_blocked_gate,),
        timeout=agent_timeout(90),
    ),
    AgentPhase(
        "review",
        role="reviewer",
        inputs=("branch", "base_branch", "plan_path", "plan_hash"),
        result=results.ReviewResult,
        gates=(
            reducers.review_blockers_gate,
            reducers.review_gate,
            reducers.plan_hash_gate_adapter,
        ),
        timeout=agent_timeout(45),
    ),
    Step("verify", verify.run_suite, gates=(reducers.verification_passed_gate,)),
    Step("mark_done", rollup.set_status, args={"status": "done"}, best_effort=True),
))
"""The fourteen-phase task workflow of design §5, phase for phase."""
