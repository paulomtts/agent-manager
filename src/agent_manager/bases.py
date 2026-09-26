"""Build a multi-blocker story's merged base from its blockers' tips.

Supervisor-tree design §5 (`docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`),
plan Task 2.1 (card 06bf46bb). A story blocked by two or more in-milestone
stories roots on `dag.base_branch_name`, a branch this module builds: it is
cut from the first blocker's tip with `worktree.ensure`, every other tip is
merged in with `steps.integrate.merge_tip`, and the result is verified once
the way `integration._final_verification` verifies the integrated branch.

A plain async function, not a pygents Agent (milestone rule 2), and grafo is
not imported here. Every git and verify call runs in a thread through
`asyncio.to_thread`, so a lane awaiting its base never blocks the loop.

Task 2.2 (card 8fe30578) wires conflicts to the Integrate resolver and is the
first user of `store`, `run_id`, `story_id`, `runner_factory` and `stop`,
which `build` already accepts so its call site never changes.

Nothing is pushed, and the milestone's base branch is never checked out,
merged into or moved: every git write is `ensure`'s or `merge_tip`'s, in the
base worktree `cli.worktree_for` names. The outcome is internal state, so it
is a plain dataclass (CLAUDE.md).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_manager import cli
from agent_manager.dag import RootPlan
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps.integrate import MergeInProgressError, _ref_exists, merge_tip
from agent_manager.steps.worktree import ensure, run_git
from agent_manager.store import Store


@dataclass(frozen=True)
class BaseResult:
    """The merged base is built and verified.

    `merged` and `already_merged` list the tips after the first, in the order
    given; `resolved` lists the tips a resolver fixed (always empty until
    Task 2.2).
    """

    branch: str
    merged: list[str]
    already_merged: list[str]
    resolved: list[str]


class BaseFailed(Exception):
    """The merged base could not be built. A human reads `detail`.

    `stopped` is True only when a stop signal cut the build short (Task 2.2);
    every failure a clean-merge build raises is a real failure.
    """

    def __init__(self, detail: str, *, stopped: bool = False) -> None:
        self.detail = detail
        self.stopped = stopped
        super().__init__(detail)


def _missing_tips(repo: Path, tips: list[str]) -> list[str]:
    """The tips that do not resolve in `repo`, in the order given.

    Asked before anything is cut or merged, so a deleted blocker branch fails
    the base by name instead of surfacing as a raw git error mid-build.
    """
    return [tip for tip in tips if not _ref_exists(run_git, str(repo), tip)]


async def build(
    root: RootPlan,
    tips: list[str],
    *,
    repo_dir: Path,
    commands: Sequence[str],
    allow_no_verification: bool,
    store: Store | None,
    run_id: str | None,
    story_id: str | None,
    runner_factory: cli.RunnerFactory | None,
    stop: StopSignal | None,
) -> BaseResult:
    """Cut `root.branch` from `tips[0]`, merge every other tip, verify once.

    `tips` are the blockers' tips in `root.blockers` order. An existing branch
    or worktree is reused, and a tip already inside the base is reported as
    `already_merged`, so a relaunch re-merges nothing. `store`, `run_id`,
    `story_id`, `runner_factory` and `stop` are unused until Task 2.2.
    """
    tips = list(tips)
    if not tips:
        raise ValueError(
            f"bases.build needs at least one blocker tip to build {root.branch}, got none"
        )
    repo = Path(repo_dir).resolve()
    worktree = cli.worktree_for(repo, root.branch)

    missing = await asyncio.to_thread(_missing_tips, repo, tips)
    if missing:
        raise BaseFailed(
            f"cannot build the merged base {root.branch}: blocker tip "
            f"{', '.join(missing)} does not exist in {repo}"
        )

    await asyncio.to_thread(ensure, root.branch, tips[0], worktree, repo)

    merged: list[str] = []
    already_merged: list[str] = []
    for tip in tips[1:]:
        try:
            result = await asyncio.to_thread(
                merge_tip, repo, worktree, root.branch, tips[0], tip, git_runner=run_git
            )
        except MergeInProgressError as error:
            # The error already says the earlier conflict was never resolved
            # and a human must finish it in the worktree.
            raise BaseFailed(
                f"cannot build the merged base {root.branch}: {error}"
            ) from error
        if result["conflict"]:
            files = ", ".join(str(name) for name in result["files"])
            raise BaseFailed(
                f"conflict merging {tip} into the merged base {root.branch} "
                f"({files}): resolver not wired, so the merge is left in progress "
                f"in {worktree} for a human"
            )
        if result["already_merged"]:
            already_merged.append(tip)
        else:
            merged.append(tip)

    return BaseResult(
        branch=root.branch, merged=merged, already_merged=already_merged, resolved=[]
    )
