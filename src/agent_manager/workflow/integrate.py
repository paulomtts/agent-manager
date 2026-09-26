"""The builtin `integrate` workflow as declared phase-model data (spec G3).

Pinned to `builtin/integrate.yaml` by `tests/workflow/test_declared.py`, the
same way `workflow.task.TASK` is pinned to `task.yaml`. The timeout floor is
`workflow.task`'s -- one launcher timeout for every declared workflow.

`merge_completed_gate` lives in `steps/integrate.py`; that module is imported
as `integrate_steps` so it is never confused with this one.

No pygents import here (rule 1).
"""

from __future__ import annotations

from agent_manager import results
from agent_manager.steps import integrate as integrate_steps
from agent_manager.steps import reducers, verify
from agent_manager.workflow.phases import AgentPhase, Retry, Step, Workflow
from agent_manager.workflow.task import agent_timeout

INTEGRATE = Workflow("integrate", (
    AgentPhase(
        "resolve",
        role="resolver",
        inputs=("branch", "base_branch", "merge_tip", "conflict_files", "verification"),
        result=results.ResolveResult,
        gates=(integrate_steps.merge_completed_gate,),
        retry=Retry(2, ("gate_failed", "schema_invalid")),
        timeout=agent_timeout(30),
    ),
    Step("verify", verify.run_suite, gates=(reducers.verification_passed_gate,)),
))
"""`builtin/integrate.yaml`, phase for phase."""
