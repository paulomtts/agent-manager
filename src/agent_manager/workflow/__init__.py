"""The workflow as declared Python data.

`workflow.phases` is the phase model; `workflow.task.TASK` and
`workflow.integrate.INTEGRATE` are the two shipped workflows. Nothing here
executes a phase: `runtime/engine.py` walks a workflow, `prompt.py` renders an
agent phase's inputs and `dispatch.py` runs one. Nothing is re-exported, so
importing `agent_manager.workflow.phases` loads nothing else.
"""
