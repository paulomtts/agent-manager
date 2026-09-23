"""Run one agent phase to a terminal outcome (design §6 lines 261-278).

`engine.run_subtask` resolves an agent phase's `inputs` and renders its prompt,
then hands `(phase, context, rendered)` to an injected `AgentPhaseRunner`
(`engine.py` lines 211-222). This module is that runner: the attempt directory,
the dispatch, the result file, the gates and the retry loop.

Three rules shape everything here, and none of them is negotiable:

- D7 / §8 line 317: nothing in this module starts a process. The argv comes
  from the adapter's `build_command` and is run through the injected
  `LauncherFn`, which is what keeps `bwrap` a later swap and every test above
  the launcher process-free. `subprocess` is deliberately not imported.
- D4 / §6 step 5: the contract is `result.json`. `stdout.log` is captured as a
  log and is only ever handed to `parse_usage`, never parsed for a result.
- §6 step 3: the attempt directory comes from `paths.attempt_dir`, which is
  rooted under `paths.data_dir()` and therefore outside every worktree -- a
  result file written inside the worktree would fail the verify step's
  clean-tree check or be swept into a commit.
"""

from dataclasses import replace

from agent_manager import paths, prompt

RESULT_NAME = "result.json"
"""The result file §6 step 3 puts in every attempt directory."""

STDOUT_NAME = "stdout.log"
"""The captured harness log of one attempt (§6 step 4). A log, not a channel."""

FEEDBACK_HEADING = "## feedback on the previous attempt"
"""Heading of the block §6 step 7 appends before a re-dispatch.

A `##` section, matching `prompt._assemble`'s section format, so the retry
prompt reads as one more input section rather than as a stray paragraph.
"""


def next_attempt(run_id: str, card: str, phase: str) -> int:
    """The lowest attempt number this phase has no directory for yet.

    Scanned from disk rather than counted in memory: a resumed run finds the
    attempts a previous process made, and overwriting one would destroy the
    prompt, result and log that are the only evidence of what happened.
    """
    card_dir = paths.run_dir(run_id) / card
    attempt = 1
    while (card_dir / f"{phase}.{attempt}").exists():
        attempt += 1
    return attempt


def with_feedback(
    rendered: prompt.RenderedPrompt, feedback: str
) -> prompt.RenderedPrompt:
    """The same prompt with one feedback block appended (§6 step 7).

    Appended to whatever it is handed, so a third attempt carries both earlier
    complaints: the spec's "the prior prompt plus the feedback block". Dispatch
    is stateless (D1), so the whole input of every attempt has to be the file on
    disk -- nothing is carried in the harness's head between attempts.
    """
    return replace(
        rendered,
        text=f"{rendered.text}\n{FEEDBACK_HEADING}\n{feedback}\n",
        sections=rendered.sections + (("feedback", feedback),),
    )
