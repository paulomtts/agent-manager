"""Card identity in every derived name: branches, plan files, spec files.

The two halves of a derived name are not equal. The short id is load-bearing
and the slug is decoration: a card's title can be edited after its branch
exists, and if matching keyed on the slug that edit would orphan the branch —
the run would report finished work as not done. So everything that MATCHES
uses the id, and the slug exists only so ``git branch`` output is readable.

Eight hex characters collides with probability that does not matter inside one
milestone. The check is deliberately strict: a card id is a UUID, and anything
else is a bug worth surfacing loudly rather than inventing a plausible short
id.

The rest of the module is the milestone's dependency graph, ported from the
leave-me-alone orchestrator: doneness read from brd ``status`` alone
(``is_subtask_done``, ``is_story_closed``, ``remaining_subtasks``), stories
grouped into dependency levels for dispatch and for integrate
(``topological_levels``, ``compute_levels``, ``compute_integrate_levels``),
a blocker-cycle check that must run before any stack geometry
(``assert_no_blocker_cycles``), and the stack geometry itself: where each
story's stack roots and ends and what each subtask's branch stacks on
(``subtask_branch``, ``base_branch_name``, ``story_tip``, ``story_root`` and
its ``RootPlan``, ``stack_bases``), all
derived from the census and never discovered. All of them read
``census.StoryPlan`` and ``census.SubtaskPlan`` by attribute, keep census
order, and ignore blockers outside the milestone. ``board_levels`` is the
same leveling one level up: open milestone roots (``models.CardNode``)
grouped by their own ``blocked_by`` edges, for display and claims only;
``milestone_is_open`` is the one test of which roots are open.

This module is pure: no I/O, no subprocesses, no ``brd``.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from agent_manager.census import StoryPlan, SubtaskPlan, is_finished, is_out_of_play
from agent_manager.models import CardNode

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")


def short_id(card_id: object) -> str:
    """First eight hex characters of a card UUID, dashes stripped, lowercased."""
    if not isinstance(card_id, str):
        raise ValueError(f"not a card id: {card_id!r}")
    hex_only = card_id.replace("-", "")
    if not _HEX32.match(hex_only):
        raise ValueError(f"not a card id: {card_id!r}")
    return hex_only[:8].lower()


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title: object, max: int = 24) -> str:
    """Lowercase dash-joined slug of a title, cut at a word boundary."""
    flat = _NON_SLUG.sub("-", str("" if title is None else title).lower()).strip("-")
    if len(flat) <= max:
        return flat
    # Cut at a word boundary rather than mid-word: a trailing "-uv" fragment
    # makes a branch name harder to read, not easier.
    cut = flat[:max]
    last_dash = cut.rfind("-")
    kept = cut[:last_dash] if last_dash > 0 else cut
    return kept.rstrip("-")


def _field(card: object, name: str) -> object:
    """Read ``name`` off a card given either as a mapping or as an object."""
    if isinstance(card, Mapping):
        return card.get(name)
    return getattr(card, name, None)


def task_stem(card: object) -> str:
    """Readable slug plus the load-bearing short id, or the short id alone."""
    slug = slugify(_field(card, "title"))
    card_short_id = short_id(_field(card, "id"))
    return f"{slug}-{card_short_id}" if slug else card_short_id


def task_branch(prefix: str, card: object) -> str:
    """Branch name for one card's task worktree."""
    return f"{prefix}/task-{task_stem(card)}"


# ── doneness ────────────────────────────────────────────────────────────────
# Port of orchestrator.js:84-101. brd `status` is the only source of truth for
# doneness; there is no other field to consult.


def is_subtask_done(subtask: SubtaskPlan) -> bool:
    """True when the subtask's brd status is finished (``done``/``merged``)."""
    return is_finished(subtask.status)


def is_story_closed(story: StoryPlan) -> bool:
    """True when the story's own brd status is finished (``done``/``merged``).

    A story marked done is finished, full stop: its subtasks are never
    re-dispatched. During the 2026-08-17 outage per-subtask lookups returned
    null and closed stories were re-implemented. The story's single status
    field cannot be corrupted piecemeal, so it is the safer gate; a story
    closed by mistake is reopened by hand.
    """
    return is_finished(story.status)


def remaining_subtasks(story: StoryPlan) -> list[SubtaskPlan]:
    """The story's not-finished, in-play subtasks in census order; none if closed.

    ``canceled``/``archived`` subtasks are out of play and never remaining work.

    The census already ordered the subtasks by their ``blocked_by`` chain, so
    nothing is re-sorted here.
    """
    if is_story_closed(story):
        return []
    return [
        subtask
        for subtask in story.subtasks
        if not is_subtask_done(subtask) and not is_out_of_play(subtask.status)
    ]


# ── dependency levels ───────────────────────────────────────────────────────
# Port of orchestrator.js:131-165. One topological engine groups stories into
# levels by their ``blocked_by`` edges; dispatch and integrate differ only in
# which stories they feed it.


class DependencyCycleError(ValueError):
    """Stories' ``blocked_by`` edges form a cycle, so no order exists.

    Subclasses ``ValueError`` because ``ValueError`` is already in
    ``cli.HANDLED``: a CLI caller turns it into an ``ok: false`` envelope
    without this module importing ``cli``.
    """


def topological_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]:
    """``stories`` grouped into dependency levels, each level in input order.

    A story is ready once every blocker is either outside ``stories`` (ignored:
    it is not this milestone's to order) or already placed. The same
    ``StoryPlan`` objects come back; the input list is not touched.
    """
    ids = {story.id for story in stories}
    placed: set[str] = set()
    levels: list[list[StoryPlan]] = []
    rest = list(stories)
    while rest:
        ready = [
            story
            for story in rest
            if all(dep not in ids or dep in placed for dep in story.blocked_by or [])
        ]
        if not ready:
            listed = ", ".join(f"#{story.id}" for story in rest)
            raise DependencyCycleError(f"dag: dependency cycle among stories {listed}")
        levels.append(ready)
        placed.update(story.id for story in ready)
        rest = [story for story in rest if story.id not in placed]
    return levels


def compute_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]:
    """Dispatch levels: only stories that still have subtasks to run.

    A closed story, or one with no remaining subtasks, is dropped. Its id then
    sits outside the pending set, so a story it blocked lands in level 0.

    Card ids are opaque strings with no inherent order, so the census's own
    order is the only stable one: pending stories keep it, never re-sorted.
    """
    pending = [
        story
        for story in stories
        if not is_story_closed(story)
        and not is_out_of_play(story.status)
        and remaining_subtasks(story)
    ]
    return topological_levels(pending)


def compute_integrate_levels(stories: list[StoryPlan]) -> list[list[StoryPlan]]:
    """Integrate levels: every story, finished or not.

    A story finished in an earlier run still needs its tip folded in, or a
    resumed milestone's Integrate would report only its own slice as the
    whole milestone.
    """
    return topological_levels(stories)


def _has_open_descendant(node: CardNode) -> bool:
    """True when any in-play card under ``node``, at any depth, is not finished.

    An out-of-play card (``canceled``/``archived``) and its whole subtree are
    skipped: they are neither open work nor finished work.
    """
    return any(
        not is_finished(child.status) or _has_open_descendant(child)
        for child in node.children
        if not is_out_of_play(child.status)
    )


def milestone_is_open(root: CardNode) -> bool:
    """True when the milestone ``root`` still has work a board run would dispatch.

    The one definition of an open milestone, shared by ``board_levels`` and
    ``orchestrate.milestone_bases``. A root is open when it is neither finished
    nor out of play and some in-play card under it, at any depth, is not
    finished.
    """
    return (
        not is_finished(root.status)
        and not is_out_of_play(root.status)
        and _has_open_descendant(root)
    )


def board_levels(roots: list[CardNode]) -> list[list[CardNode]]:
    """Open milestone roots grouped into dependency levels, each in input order.

    The board-wide twin of ``compute_levels``: a root still has work only if
    it is not ``done`` and some card under it, at any depth, is not ``done``.
    Any other root is dropped first, so its id sits outside the set and a
    root it blocked lands in level 0. A kept root is ready once every blocker
    is either outside the kept set (ignored: it is not this board's to order)
    or already placed. The same ``CardNode`` objects come back; the input list
    is not touched. Used only for display and for computing the claim set up
    front; it never drives execution order.
    """
    pending = [root for root in roots if milestone_is_open(root)]
    ids = {root.id for root in pending}
    placed: set[str] = set()
    levels: list[list[CardNode]] = []
    rest = list(pending)
    while rest:
        ready = [
            root
            for root in rest
            if all(dep not in ids or dep in placed for dep in root.blocked_by or [])
        ]
        if not ready:
            listed = ", ".join(f"#{root.id}" for root in rest)
            raise DependencyCycleError(f"dag: dependency cycle among milestones {listed}")
        levels.append(ready)
        placed.update(root.id for root in ready)
        rest = [root for root in rest if root.id not in placed]
    return levels


# ── cycle detection ─────────────────────────────────────────────────────────
# Port of orchestrator.js:185-208.


def assert_no_blocker_cycles(stories: list[StoryPlan]) -> None:
    """Raise ``DependencyCycleError`` if the stories' ``blocked_by`` edges cycle.

    This must run before any stack geometry. ``compute_levels`` also refuses
    a cycle, but the geometry has to be sound first, and a cycle is exactly
    what breaks it. ``story_root``'s own guard is not enough either:
    ``story_tip`` returns a branch immediately for a story with subtasks, so a
    cycle between two populated stories never recurses back to trip it.

    Depth-first from each story in input order; only blockers that are stories
    in ``stories`` are followed, so blockers outside the milestone are ignored.
    """
    by_id = {story.id: story for story in stories}
    state: dict[str, str] = {}  # id -> "visiting" | "done"

    def walk(story_id: str, trail: list[str]) -> None:
        if state.get(story_id) == "done":
            return
        if state.get(story_id) == "visiting":
            cycle = trail[trail.index(story_id):] + [story_id]
            joined = " -> ".join(f"#{node}" for node in cycle)
            raise DependencyCycleError(
                f"dag: dependency cycle among stories {joined}"
                " — no stack can be rooted until it is broken"
            )
        state[story_id] = "visiting"
        for dep in by_id[story_id].blocked_by or []:
            if dep in by_id:
                walk(dep, [*trail, story_id])
        state[story_id] = "done"

    for story in stories:
        walk(story.id, [])


# ── stack geometry ──────────────────────────────────────────────────────────
# Port of orchestrator.js:211-268. Where each subtask's branch stacks is
# DERIVED from the census, never discovered. ``assert_no_blocker_cycles`` must
# run before any of these functions.


@dataclass(frozen=True)
class RootPlan:
    """Where a story's stack starts, and why.

    ``kind`` is ``"base"`` when the story has no in-milestone blocker
    (``branch`` is the milestone's base branch), ``"tip"`` when it has exactly
    one (``branch`` is that blocker's tip), and ``"merged"`` when it has two or
    more (``branch`` is the story's own ``base_branch_name``, which a later
    card builds by merging the blockers' tips). ``blockers`` are the
    in-milestone blockers, de-duplicated, in the order the story's
    ``blocked_by`` lists them. Internal state, so a dataclass (CLAUDE.md).
    """

    kind: Literal["base", "tip", "merged"]
    branch: str
    blockers: tuple[str, ...]


def subtask_branch(prefix: str, subtask: SubtaskPlan) -> str:
    """The branch a subtask's work lives on: exactly ``task_branch``.

    Derived, never looked up. An earlier orchestrator preferred a PR's real
    head ref, which made the geometry depend on the PRs and the PR matching
    depend on the geometry — a circularity that bred two bugs in one
    afternoon. Determinism beats reconciling against an external system, so
    the prefix is part of the milestone's identity, full stop.
    """
    return task_branch(prefix, subtask)


def base_branch_name(prefix: str, story: StoryPlan) -> str:
    """The branch a multi-blocker story's merged base lives on.

    Keyed on the story's short id like every derived name, so a title edit
    cannot orphan it; ``short_id`` refuses anything that is not a card id.
    """
    return f"{prefix}/base-{short_id(story.id)}"


def story_tip(
    story: StoryPlan,
    stories_by_id: Mapping[str, StoryPlan],
    prefix: str,
    base_branch: str,
    seen: set[str] | None = None,
) -> str:
    """The branch a story's stack ends on — what a dependent story roots from.

    The last subtask of the FULL ordered list, whatever its status. A story
    with no subtasks contributes no branch, so it falls through to its own
    root with the same ``seen``. Run ``assert_no_blocker_cycles`` first.
    """
    if story.subtasks:
        return subtask_branch(prefix, story.subtasks[-1])
    return story_root(story, stories_by_id, prefix, base_branch, seen).branch


def story_root(
    story: StoryPlan,
    stories_by_id: Mapping[str, StoryPlan],
    prefix: str,
    base_branch: str,
    seen: set[str] | None = None,
) -> RootPlan:
    """Where a story's stack starts: the base, one blocker's tip, or a merged base.

    No in-milestone blocker roots on ``base_branch``; exactly one roots on that
    blocker's tip; two or more root on the story's own ``base_branch_name``,
    which a later card builds by merging every blocker's tip. A blocker marked
    ``done`` still counts and still yields its tip: done does not mean its code
    landed anywhere. ``assert_no_blocker_cycles`` must run first; the ``seen``
    guard here is only a backstop, because ``story_tip`` returns immediately
    for a story with subtasks, so a cycle between two populated stories never
    recurses back to trip it.
    """
    seen = set() if seen is None else seen
    if story.id in seen:
        raise DependencyCycleError(
            f"dag: dependency cycle reached story #{story.id} while computing its stack root"
        )
    seen.add(story.id)
    # Only blockers inside this milestone can be stacked on; anything else is
    # external work whose branch this run knows nothing about.
    # A blocker listed twice is still one blocker.
    blockers = tuple(
        dep for dep in dict.fromkeys(story.blocked_by or []) if dep in stories_by_id
    )
    if not blockers:
        return RootPlan("base", base_branch, ())
    if len(blockers) > 1:
        return RootPlan("merged", base_branch_name(prefix, story), blockers)
    tip = story_tip(stories_by_id[blockers[0]], stories_by_id, prefix, base_branch, seen)
    return RootPlan("tip", tip, blockers)


def stack_bases(
    story: StoryPlan,
    stories_by_id: Mapping[str, StoryPlan],
    prefix: str,
    base_branch: str,
) -> dict[str, str]:
    """Each subtask id mapped to the branch it stacks on, in census order.

    The first subtask stacks on the story's root; every other one on the
    previous subtask in the FULL list, never ``remaining_subtasks``, so a done
    first subtask still anchors the second. The root is computed even for a
    story with no subtasks, so its errors surface. Run
    ``assert_no_blocker_cycles`` first.
    """
    root = story_root(story, stories_by_id, prefix, base_branch).branch
    ordered = story.subtasks
    return {
        subtask.id: root if index == 0 else subtask_branch(prefix, ordered[index - 1])
        for index, subtask in enumerate(ordered)
    }
