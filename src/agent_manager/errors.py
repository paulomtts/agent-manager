"""The package's exception classes.

`EngineError` lives in `agent_manager.runtime.errors`, importable without
pygents.
"""

from collections.abc import Sequence
from typing import Any


class AgentPhaseFailed(RuntimeError):
    """An agent phase ended on a terminal failure, with its attempts recorded.

    Raised rather than returned because `runtime.walk.AgentPhaseRunner` is typed
    `(phase, context, rendered) -> result`: there is no second channel in that
    signature, and returning a sentinel result would be indistinguishable from a
    phase whose harness genuinely produced one. The pygents walk's `agent_phase`
    tool catches it: a critic with `on_fail` loops back, anything else escalates
    -- the same edge a step reaches through `_Outcome(ok=False, ...)`.

    `outcome` is one of §6 line 277's four journalled names, so an operator
    reading the message knows whether to fix a prompt, a gate or a harness.

    `result` is the agent's own decoded payload when a dispatch succeeded but a
    gate then failed it (e.g. review's `unresolved_blockers`) -- `None` when
    nothing was ever produced (a raised or broken gate, a harness failure). A
    failed phase's `ContextItem` is never yielded (only a successful phase's
    is), so this is the only channel that gets the agent's own explanation of
    *why* into `SubtaskSummary.results[failed_phase]` for an escalation, which
    is what `comments.agent_reason` reads to compose the board comment.
    """

    def __init__(
        self, phase: str, *, outcome: str, detail: str, result: Any = None
    ) -> None:
        self.phase = phase
        self.outcome = outcome
        self.detail = detail
        self.result = result
        super().__init__(f"phase {phase!r} ended {outcome}: {detail}")


class LimitWaitInterrupted(RuntimeError):
    """A stop was requested while an agent phase waited out a usage limit.

    The phase is neither failed nor done: the walk re-queues it so the run
    parks before it, and a resume dispatches it again.
    """

    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__(f"phase {phase!r}: the usage-limit wait was interrupted by a stop")


class StoryNotFoundError(ValueError):
    """No story card, or more than one, matches what the caller typed, or the
    card it names is not a story."""


class StoryBlockedError(ValueError):
    """A story run refused because a direct blocker of the story is still open.

    Raised before anything is written. `blockers` are `(id, title)` pairs in
    census order; only their ids are kept, as `blockers`, so a caller can act
    without parsing the message. Takes plain pairs rather than census plans
    because this module imports nothing from `agent_manager`.
    """

    def __init__(
        self, story_id: str, story_title: str, blockers: Sequence[tuple[str, str]]
    ) -> None:
        self.story_id = story_id
        self.blockers = tuple(blocker_id for blocker_id, _title in blockers)
        named = ", ".join(f'"{title}" ({blocker_id})' for blocker_id, title in blockers)
        super().__init__(
            f'story "{story_title}" ({story_id}) is blocked by {named}'
            " — run them first, or run the milestone"
        )
