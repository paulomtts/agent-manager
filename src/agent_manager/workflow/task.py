"""The builtin `task` workflow as declared phase-model data (spec G3).

Pinned to `builtin/task.yaml`: `tests/workflow/test_declared.py` asserts that
`TASK.digest()` equals the digest of the shipped YAML run through
`phases.from_loader`, so the two cannot drift apart silently. Every callable
is the real function object `registry.default_registry()` binds -- never a
registry lookup -- because the digest names callables by `module.qualname`.

Timeouts are the one thing the YAML never had. G2 requires every agent turn
timeout to exceed the launcher's strictly (the launcher must kill `claude -p`
before the turn is cancelled), so each phase gets `max(chosen, floor)` with the
floor five minutes above the launcher timeout. The launcher's is never lowered.

No pygents import here (rule 1).
"""

from __future__ import annotations

from datetime import timedelta

from agent_manager import dispatch, results
from agent_manager.steps import docs_commit, plan_check, reducers, rollup, verify, worktree
from agent_manager.workflow.phases import AgentPhase, Retry, Step, Workflow

LAUNCHER_TIMEOUT = timedelta(seconds=dispatch.DEFAULT_TIMEOUT)
"""How long the launcher lets one `claude -p` run before killing it (1800 s)."""

AGENT_TIMEOUT_FLOOR = LAUNCHER_TIMEOUT + timedelta(minutes=5)
"""The lowest agent-phase timeout: strictly above `LAUNCHER_TIMEOUT` (G2)."""


def agent_timeout(minutes: int) -> timedelta:
    """The chosen timeout, raised to `AGENT_TIMEOUT_FLOOR` if it is not above it."""
    return max(timedelta(minutes=minutes), AGENT_TIMEOUT_FLOOR)


TASK = Workflow("task", (
    Step("worktree", worktree.ensure),
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
        inputs=("card", "explore", "spec_path"),
        result=results.SpecResult,
        writes="docs/superpowers/specs/{stem}.md",
        timeout=agent_timeout(30),
    ),
    AgentPhase(
        "validate_spec",
        role="critic",
        inputs=("card", "spec_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
    ),
    AgentPhase(
        "plan",
        role="planner",
        inputs=("spec_path", "plan_path"),
        result=results.PlanResult,
        writes="docs/superpowers/plans/{stem}.md",
        timeout=agent_timeout(30),
    ),
    AgentPhase(
        "validate_plan",
        role="critic",
        inputs=("spec_path", "plan_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
    ),
    Step("mark_validated", plan_check.mark_validated),
    Step("docs_commit", docs_commit.commit_documents),
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
        inputs=("branch", "base_branch", "plan_path"),
        result=results.ReviewResult,
        gates=(reducers.review_gate, reducers.plan_hash_gate_adapter),
        timeout=agent_timeout(45),
    ),
    Step("verify", verify.run_suite, gates=(reducers.verification_passed_gate,)),
    Step("mark_done", rollup.set_status, args={"status": "done"}, best_effort=True),
))
"""`builtin/task.yaml`, phase for phase."""
