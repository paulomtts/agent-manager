<!-- task-pipeline: validated -->
# Add `am run --milestone --dry-run` (card 36faf21e)

Parent story 09a9203b "Stack geometry, and a dry run that shows it" (milestone 3). Narrows O3 of `docs/superpowers/specs/2026-09-24-orchestration-design.md` (lines 56-63) to one change in `src/agent_manager/cli.py`. The geometry (O2) is already in `dag.py` from the done siblings c4e32e4a and 59977446; the census (O1) and `board.roots` come from other m3 cards. This card consumes them and does not reimplement or change them.

## Precondition

The working branch must contain `census.find_milestone`, `census.flatten_milestone`, `board.roots`, `models.CardNode.blocked_by` and `dag.stack_bases` / `story_root` / `subtask_branch` / `assert_no_blocker_cycles` / `compute_levels` / `remaining_subtasks` / `is_story_closed` / `is_subtask_done`. As of writing they exist together only on `m3/task-port-stack-roots-tips-59977446` (HEAD 5d55fce); master (68330a4) lacks them. Build on top of that line of work, not on bare master.

## Scope

Only `cli.py` and `tests/test_cli.py`. No change to `dag.py`, `census.py`, `board.py`, `models.py`, `engine`, `store` or `paths`.

## Observable behavior

`am run` options:

- `--card <id>` becomes optional (default `None`). `--milestone <id|title>` is new, optional, and takes a card id or title substring exactly as `census.find_milestone` accepts it.
- `--dry-run` is a new boolean flag, default off.
- `--branch-prefix` stays required. `--base-branch` keeps its `"master"` default. `--repo-dir`, `--pretty`, `--verify` and `--allow-no-verification` are unchanged (the last two are accepted and ignored on the dry path).

Argument validation happens before anything else. These are Typer usage errors (exit 2, which `EXIT_ERROR`'s docstring already reserves for Typer), not envelopes:

- both `--card` and `--milestone` given;
- neither given;
- `--dry-run` with `--card`.

Dispatch:

- `--card` without `--dry-run` works exactly as it does today through `run_card`. The envelope, the exit codes and every existing test stay the same.
- `--milestone` without `--dry-run` raises a new `CliError` subclass (for example `MilestoneRunNotImplementedError`). It becomes an `ok: false` envelope with exit `EXIT_ERROR` (3), and its message says milestone runs are not implemented yet and names `--dry-run`. The next story wires the real run.
- `--milestone --dry-run` calls a new function (for example `dry_run_milestone(needle, *, repo_dir, branch_prefix, base_branch) -> dict`). It returns the payload, and the command prints it with `render(ok_envelope(payload), pretty=pretty)`, exit 0.

Dry-run flow, in this order: `resolve_repo_dir(repo_dir)`, then `board.roots(repo_dir=)`, then `census.find_milestone(roots, needle)`, then `board.tree(milestone.id, repo_dir=)`, then `census.flatten_milestone(tree)` (returns a `Census`; `stories` below means its `.stories` list), then `dag.assert_no_blocker_cycles(stories)`, which runs before any geometry, then `dag.compute_levels(stories)`. For each story in each level it calls `dag.story_root` and `dag.stack_bases(story, stories_by_id, prefix, base_branch)`, with `stories_by_id` built over all flattened stories, closed ones included, so that a story blocked by a done story still roots on that story's tip (O2). Each subtask's branch comes from `dag.subtask_branch(prefix, subtask)`.

Payload shape (pinned by tests):

```
{
  "levels": [
    {"level": 0, "stories": [
      {"story": "<id>", "title": "...", "root": "<branch>",
       "subtasks": [{"id": "...", "title": "...", "status": "todo|in_progress|...", "branch": "...", "base": "..."}]}
    ]}
  ],
  "already_done": [
    {"kind": "story", "id": "<story id>", "title": "..."},
    {"kind": "subtask", "id": "<subtask id>", "title": "...", "story": "<story id>"}
  ]
}
```

- `level` is the 0-based index from `compute_levels`. Stories within a level and levels themselves keep census order.
- A story's `subtasks` lists only `dag.remaining_subtasks(story)`, which are the ones that would be dispatched. Each entry's `status` is the census status (`blocked` is already flattened to `todo`). The entry's `base` still comes from `stack_bases` over the full ordered list, so when a first subtask is done, the second one's base is the first one's branch, not the root.
- `already_done` lists, in census order, everything that never enters a level. A story that is closed (`is_story_closed`) or has no remaining subtasks appears as one `kind: "story"` entry, and its subtasks are not listed separately. A done subtask (`is_subtask_done`) of a story that is still pending appears as a `kind: "subtask"` entry carrying its story id.

Writes: none of any kind. The dry path must not construct a `Store` (which mints a run directory under `paths.run_dir`), call `run_card` or build a runner, create a worktree or branch, write to the board, fetch, or prune. Its only I/O is the read-only `board.roots` / `board.tree` calls.

## Error paths

Everything below is an `ok: false` envelope with exit 3 through the existing `HANDLED` tuple, and no new entries go into `HANDLED`:

- The repo dir is bad: `RepoDirError`.
- The milestone is not found or ambiguous: the census's `ValueError` subclasses, such as `MilestoneNotFoundError`.
- The board read fails: `board.BoardError`.
- A cycle among stories: `dag.DependencyCycleError`, raised by `assert_no_blocker_cycles`, whose message names the cycle as `#a -> #b -> #a`.
- A story with two or more in-milestone blockers: `dag.StackRootError`, whose message names both blockers.
- `--milestone` without `--dry-run`: the new `CliError` subclass.

## Tests (all in `tests/test_cli.py`)

Test tiers follow design spec section 14 (lines 479-494) and the `tests/test_cli.py` docstring. Pure CLI helpers are unit tests. The `run` Typer command runs in the Engine tier with Steps-tier fixtures: a real temp git repo, a real temp brd board, and `XDG_DATA_HOME` set to `tmp_path`, all reusing the existing skip-if-no-git/brd fixtures, the `_git` helper and `CliRunner` (around lines 1025-1083). There are no tests in `tests/e2e`, no new tier, and no fake `claude`.

1. Engine tier: the dry run on a milestone-2-shaped board, with three chained stories (B blocked by A, C blocked by B) and 2-3 subtasks each. It asserts the `{ok: true, data}` envelope and exit 0, and that there are three levels with one story each. It checks the base column: story A's first subtask is on `--base-branch`, each later subtask is on the previous subtask's branch, and the first subtask of B (and of C) is on the last subtask branch of A (and of B). Each `root` equals its first subtask's base. `already_done` is `[]`. Afterwards `paths.data_dir()` holds no run directory, and `git worktree list` shows only the main checkout.
2. Engine tier: the same board with A marked done and B's first subtask done. A appears in `already_done` as `kind: "story"` and in no level. B is in level 0 and roots on A's tip. B's done first subtask appears as `kind: "subtask"` with `story` = B, and does not appear under B's `subtasks`. B's second subtask's base is B's first subtask's branch. Nothing is written.
3. Engine tier: `--milestone` given as a title substring resolves to the same payload as the id.
4. Engine tier: two stories that block each other produce `ok: false` and exit 3, with the message naming the cycle. Nothing is written.
5. Engine tier: a story blocked by two in-milestone stories produces `ok: false` and exit 3, with the message naming both blockers.
6. Engine tier: an unknown milestone needle produces `ok: false` and exit 3.
7. Engine tier (CliRunner, no board needed): `--card` with `--milestone`, neither of them, and `--dry-run` with `--card` each exit 2 without calling `run_card`.
8. Engine tier: `--milestone` without `--dry-run` produces `ok: false` and exit 3 with the not-implemented message, and no run directory is created.
9. Unit tier, only if the payload builder is extracted as a pure function over `StoryPlan`/`SubtaskPlan`: `already_done` shape and ordering, and remaining-only `subtasks` with full-list bases.

The whole default suite, including `tests/e2e`, stays green, and existing `run --card` tests pass unmodified.

## Out of scope

The `dag`, `census` and `board` logic; `run_milestone` and real milestone runs; `Store`/run-dir work and O4-O8; parallel stories; Integrate; milestone-aware `am resume`; watch/retry/cancel; git-measured review counts; verification discovery; any milestone title or metadata in the payload beyond O3's shape.

---

# `am run --milestone --dry-run` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `am run` a `--milestone <id|title> --dry-run` mode that prints one milestone's dispatch levels and stack geometry (the base column) as a `{ok, data}` envelope and writes nothing, with `--card`/`--milestone`/`--dry-run` validated as Typer usage errors.

**Architecture:** Everything goes in `src/agent_manager/cli.py`. A pure `dry_run_payload(stories, *, branch_prefix, base_branch)` composes the `dag` functions that already exist over `census.StoryPlan`s, and `already_done_entries(stories)` builds the `already_done` list. A thin `dry_run_milestone(needle, *, repo_dir, branch_prefix, base_branch)` does the only I/O, which is the read-only `board.roots` / `board.tree` calls followed by `census.find_milestone` / `census.flatten_milestone`. The `run` command gains `--milestone` and `--dry-run`, and `--card` becomes optional. A `_check_run_targets` helper raises `typer.BadParameter` (exit 2) for bad combinations, and a new `MilestoneRunNotImplementedError(CliError)` covers `--milestone` without `--dry-run`.

**Tech Stack:** Python 3, Typer (`typer.BadParameter`, `typer.testing.CliRunner`), pytest, real `git` and `brd` CLIs in Engine-tier fixtures, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-add-am-run-milestone-36faf21e/docs/superpowers/specs/task-add-am-run-milestone-36faf21e-design.md` (reproduced verbatim above). The parent design is `docs/superpowers/specs/2026-09-24-orchestration-design.md` O2/O3.

## Global Constraints

- Scope: only `src/agent_manager/cli.py` and `tests/test_cli.py` change. No change to `dag.py`, `census.py`, `board.py`, `models.py`, `engine`, `store` or `paths`.
- The branch is `m3/task-add-am-run-milestone-36faf21e`, cut from `m3/task-port-stack-roots-tips-59977446` (5d55fce). Use the `census`/`board.roots`/`dag` functions already there and do not reimplement them.
- CLI output is the brd envelope `{"ok": true, "data": ...}`, one line by default and indented under `--pretty` (CLAUDE.md).
- Usage errors exit `2` (Typer's). Refusals are `ok: false` envelopes at `EXIT_ERROR` (`3`) through the existing `HANDLED` tuple. Add nothing to `HANDLED`.
- `--branch-prefix` stays required. `--base-branch` keeps its default `"master"`.
- The dry path writes nothing: no `Store`, no `run_card`, no runner, no run dir, no worktree, no branch, no board write, no fetch, no prune.
- Payload keys are exactly `levels` and `already_done`. A level is `{level, stories}`, a story `{story, title, root, subtasks}`, a subtask `{id, title, status, branch, base}`. An `already_done` entry is `{kind: "story", id, title}` or `{kind: "subtask", id, title, story}`.
- Test tiers: the pure builder gets unit tests at the top (pure) section of `tests/test_cli.py`. The Typer command gets Engine-tier tests in `tests/test_cli.py` on the existing `project` fixture (real temp git and brd, `XDG_DATA_HOME` under `tmp_path`), `requires_git`/`requires_brd` and `CliRunner`. Nothing goes in `tests/e2e` and there is no fake `claude`.
- Verification: `uv run pytest` (the whole default suite, `tests/e2e` included) stays green.

## Review Focus

1. **Blank `--milestone ""` / whitespace.** `census.find_milestone` strips the needle, and an empty needle is a substring of every title, so on a board with one root it would silently resolve to that root. Expect a usage error (exit 2) naming the blank value. Pinned in Task 3.
2. **A milestone with nothing left to run** (every story done, or a story that has no subtasks at all). Expect `ok: true` with `levels: []` and each such story as one `kind: "story"` entry, not a crash and not an empty level. Pinned in Task 1.
3. **A story blocked by a card outside the milestone.** Expect it to root on `--base-branch`, and not to count as a two-blocker refusal. Pinned in Task 1.
4. **`--pretty` on the dry run.** Expect the same `data`, indented. Pinned in Task 2.
5. **A story cycle that reaches the CLI through a real census.** brd cannot store a cycle (`tests/test_census.py:279`), and `census.flatten_milestone` orders stories with `order_siblings`, so a cyclic tree is refused as `CensusOrderError` before `dag.assert_no_blocker_cycles` runs. Expect `ok: false`, exit 3, both story ids named. The spec's `DependencyCycleError` path is pinned separately by injecting a cyclic `Census`. Pinned in Task 2 (both) and Task 1 (ordering: the cycle check runs before `compute_levels`/geometry).

---

## File Structure

- Modify: `src/agent_manager/cli.py`
  - import `census` alongside the other `agent_manager` modules (lines 28-36);
  - new `MilestoneRunNotImplementedError(CliError)` after `NotResumableError` (after line 122);
  - new pure `already_done_entries`, pure `dry_run_payload` and I/O `dry_run_milestone` between `run_card` (ends line 757) and `HANDLED` (line 760);
  - new `_check_run_targets` and a rewritten `run` command (lines 776-820).
- Modify: `tests/test_cli.py`
  - import `census` (lines 27-36);
  - unit tests for the builder at the end of the pure section, just before `requires_git = pytest.mark.skipif(` (line 1023);
  - Engine-tier fixtures and tests for the command just before `@pytest.fixture\ndef projection(` (line 1924).

---

### Task 1: The pure dry-run payload builder

**Files:**
- Modify: `src/agent_manager/cli.py:28-36` (import), `:757-760` (new functions)
- Test: `tests/test_cli.py:27-36` (import), insert before line 1023 (`requires_git = ...`)

**Interfaces:**
- Consumes: `census.StoryPlan(id, title, status, blocked_by, subtasks)`, `census.SubtaskPlan(id, title, status)`, `dag.assert_no_blocker_cycles(stories) -> None`, `dag.compute_levels(stories) -> list[list[StoryPlan]]`, `dag.stack_bases(story, stories_by_id, prefix, base_branch) -> dict[str, str]`, `dag.story_root(story, stories_by_id, prefix, base_branch) -> str`, `dag.subtask_branch(prefix, subtask) -> str`, `dag.remaining_subtasks(story) -> list[SubtaskPlan]`, `dag.is_story_closed(story) -> bool`, `dag.is_subtask_done(subtask) -> bool`.
- Produces: `cli.already_done_entries(stories: Sequence[census.StoryPlan]) -> list[dict[str, str]]` and `cli.dry_run_payload(stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str) -> dict[str, Any]`. It returns `{"levels": [...], "already_done": [...]}` and raises `dag.DependencyCycleError` / `dag.StackRootError`. Test helpers `_plan_id(n: int) -> str`, `_plan_subtask(n: int, status: str = "todo") -> census.SubtaskPlan` and `_plan_story(n: int, subtasks, *, status: str = "todo", blocked_by=()) -> census.StoryPlan` are defined in `tests/test_cli.py` and reused by Task 2.

- [ ] **Step 1: Verify the precondition functions exist on this branch**

Run: `uv run python -c "from agent_manager import board, census, dag, models; census.find_milestone; census.flatten_milestone; board.roots; models.CardNode.model_fields['blocked_by']; dag.stack_bases; dag.story_root; dag.subtask_branch; dag.assert_no_blocker_cycles; dag.compute_levels; dag.remaining_subtasks; dag.is_story_closed; dag.is_subtask_done; print('ok')"`
Expected: `ok`. If an `AttributeError`/`KeyError` appears, stop: the branch was not cut from `m3/task-port-stack-roots-tips-59977446`. Do not reimplement the missing function.

- [ ] **Step 2: Import `census` in the test module**

In `tests/test_cli.py`, replace

```python
from agent_manager import (
    board,
    cli,
    dag,
```

with

```python
from agent_manager import (
    board,
    census,
    cli,
    dag,
```

- [ ] **Step 3: Write the failing unit tests**

In `tests/test_cli.py`, insert immediately before the line `requires_git = pytest.mark.skipif(`:

```python
def _plan_id(n: int) -> str:
    """A UUID-shaped card id whose short id is `n` in eight hex digits.

    `dag.subtask_branch` goes through `dag.short_id`, which refuses anything
    that is not 32 hex characters, so the pure plans need real-shaped ids.
    """
    return f"{n:08x}-0000-4000-8000-000000000000"


def _plan_subtask(n: int, status: str = "todo") -> census.SubtaskPlan:
    return census.SubtaskPlan(id=_plan_id(n), title=f"subtask {n}", status=status)


def _plan_story(
    n: int,
    subtasks: list[census.SubtaskPlan],
    *,
    status: str = "todo",
    blocked_by: tuple[str, ...] | list[str] = (),
) -> census.StoryPlan:
    return census.StoryPlan(
        id=_plan_id(n),
        title=f"story {n}",
        status=status,
        blocked_by=list(blocked_by),
        subtasks=list(subtasks),
    )


def test_the_dry_run_payload_lists_remaining_subtasks_on_full_list_bases():
    """A done story is `already_done` and roots its dependent. A pending story
    lists only its remaining subtasks, and a done first subtask still anchors
    the second one's base."""
    a = _plan_story(1, [_plan_subtask(11, "done"), _plan_subtask(12, "done")], status="done")
    b = _plan_story(
        2,
        [_plan_subtask(21, "done"), _plan_subtask(22), _plan_subtask(23, "in_progress")],
        status="in_progress",
        blocked_by=[a.id],
    )

    payload = cli.dry_run_payload([a, b], branch_prefix="m3", base_branch="main")

    def branch(subtask: census.SubtaskPlan) -> str:
        return dag.subtask_branch("m3", subtask)

    assert payload == {
        "levels": [
            {
                "level": 0,
                "stories": [
                    {
                        "story": b.id,
                        "title": "story 2",
                        "root": branch(a.subtasks[-1]),
                        "subtasks": [
                            {
                                "id": _plan_id(22),
                                "title": "subtask 22",
                                "status": "todo",
                                "branch": branch(b.subtasks[1]),
                                "base": branch(b.subtasks[0]),
                            },
                            {
                                "id": _plan_id(23),
                                "title": "subtask 23",
                                "status": "in_progress",
                                "branch": branch(b.subtasks[2]),
                                "base": branch(b.subtasks[1]),
                            },
                        ],
                    }
                ],
            }
        ],
        "already_done": [
            {"kind": "story", "id": a.id, "title": "story 1"},
            {"kind": "subtask", "id": _plan_id(21), "title": "subtask 21", "story": b.id},
        ],
    }


def test_the_dry_run_payload_keeps_census_order_across_and_within_levels():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id])

    payload = cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")

    assert [level["level"] for level in payload["levels"]] == [0, 1]
    assert [[story["story"] for story in level["stories"]] for level in payload["levels"]] == [
        [a.id, b.id],
        [c.id],
    ]
    assert payload["levels"][0]["stories"][0]["root"] == "main"
    assert payload["levels"][0]["stories"][1]["root"] == "main"
    assert payload["levels"][1]["stories"][0]["root"] == dag.subtask_branch("m3", a.subtasks[-1])


def test_the_dry_run_checks_for_blocker_cycles_before_any_geometry():
    """`compute_levels` would also refuse, but with a comma list. The arrow
    trail is `assert_no_blocker_cycles`'s, which proves it ran first."""
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])

    with pytest.raises(dag.DependencyCycleError) as caught:
        cli.dry_run_payload([a, b], branch_prefix="m3", base_branch="main")

    assert f"#{a.id} -> #{b.id} -> #{a.id}" in str(caught.value)


def test_the_dry_run_refuses_a_story_with_two_in_milestone_blockers():
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=[a.id, b.id])

    with pytest.raises(dag.StackRootError) as caught:
        cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")

    assert f"#{a.id}" in str(caught.value)
    assert f"#{b.id}" in str(caught.value)


def test_a_milestone_with_nothing_left_has_no_levels_and_lists_every_story_as_done():
    """Review focus: a closed story, a story whose subtasks are all done, and a
    story with no subtasks at all each become one `kind: "story"` entry, in
    census order, with their subtasks not listed separately."""
    closed = _plan_story(1, [_plan_subtask(11)], status="done")
    finished = _plan_story(2, [_plan_subtask(21, "done"), _plan_subtask(22, "Done")])
    empty = _plan_story(3, [])

    payload = cli.dry_run_payload(
        [closed, finished, empty], branch_prefix="m3", base_branch="main"
    )

    assert payload == {
        "levels": [],
        "already_done": [
            {"kind": "story", "id": closed.id, "title": "story 1"},
            {"kind": "story", "id": finished.id, "title": "story 2"},
            {"kind": "story", "id": empty.id, "title": "story 3"},
        ],
    }


def test_a_blocker_outside_the_milestone_roots_the_story_on_the_base_branch():
    """Review focus: one foreign blocker plus one in-milestone blocker is ONE
    in-milestone blocker, not a two-blocker refusal."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=["not-in-this-milestone"])
    c = _plan_story(3, [_plan_subtask(31)], blocked_by=["not-in-this-milestone", a.id])

    payload = cli.dry_run_payload([a, b, c], branch_prefix="m3", base_branch="main")

    roots = {
        story["story"]: story["root"]
        for level in payload["levels"]
        for story in level["stories"]
    }
    assert roots == {
        a.id: "main",
        b.id: "main",
        c.id: dag.subtask_branch("m3", a.subtasks[-1]),
    }
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "dry_run_payload or dry_run_checks or dry_run_refuses or nothing_left or blocker_outside" -v`
Expected: 6 FAILED with `AttributeError: module 'agent_manager.cli' has no attribute 'dry_run_payload'`.

- [ ] **Step 5: Import `census` in `cli.py`**

In `src/agent_manager/cli.py`, replace

```python
from agent_manager import (
    board,
    dag,
    dispatch,
```

with

```python
from agent_manager import (
    board,
    census,
    dag,
    dispatch,
```

- [ ] **Step 6: Implement the builder**

In `src/agent_manager/cli.py`, replace

```python
    finally:
        store.close()


HANDLED: tuple[type[BaseException], ...] = (
```

with

```python
    finally:
        store.close()


def already_done_entries(stories: Sequence[census.StoryPlan]) -> list[dict[str, str]]:
    """Everything in the census that never enters a dispatch level, in census order.

    A story that is closed, or that has no remaining subtasks, is one
    `kind: "story"` entry, and its subtasks are not listed on their own: the
    story is the unit that is skipped. A done subtask of a story that is still
    pending is a `kind: "subtask"` entry naming its story, because that story
    shows up in a level without it.
    """
    entries: list[dict[str, str]] = []
    for story in stories:
        if dag.is_story_closed(story) or not dag.remaining_subtasks(story):
            entries.append({"kind": "story", "id": story.id, "title": story.title})
            continue
        for subtask in story.subtasks:
            if dag.is_subtask_done(subtask):
                entries.append(
                    {
                        "kind": "subtask",
                        "id": subtask.id,
                        "title": subtask.title,
                        "story": story.id,
                    }
                )
    return entries


def dry_run_payload(
    stories: Sequence[census.StoryPlan], *, branch_prefix: str, base_branch: str
) -> dict[str, Any]:
    """O3's preview: dispatch levels with each subtask's branch and base.

    Pure over the census, and every derivation belongs to `dag`. The cycle
    check runs first because a cycle is what breaks the geometry, and
    `story_root`'s own guard misses a cycle between two populated stories.
    `stories_by_id` covers every story, closed ones included, so a story
    blocked by a done story still roots on that story's tip. A story's
    `subtasks` lists only what would be dispatched, but each `base` comes
    from `stack_bases` over the full ordered list, so a done first subtask
    still anchors the second.
    """
    stories = list(stories)
    dag.assert_no_blocker_cycles(stories)
    levels = dag.compute_levels(stories)
    stories_by_id = {story.id: story for story in stories}
    level_rows: list[dict[str, Any]] = []
    for index, level in enumerate(levels):
        story_rows: list[dict[str, Any]] = []
        for story in level:
            bases = dag.stack_bases(story, stories_by_id, branch_prefix, base_branch)
            story_rows.append(
                {
                    "story": story.id,
                    "title": story.title,
                    "root": dag.story_root(
                        story, stories_by_id, branch_prefix, base_branch
                    ),
                    "subtasks": [
                        {
                            "id": subtask.id,
                            "title": subtask.title,
                            "status": subtask.status,
                            "branch": dag.subtask_branch(branch_prefix, subtask),
                            "base": bases[subtask.id],
                        }
                        for subtask in dag.remaining_subtasks(story)
                    ],
                }
            )
        level_rows.append({"level": index, "stories": story_rows})
    return {"levels": level_rows, "already_done": already_done_entries(stories)}


HANDLED: tuple[type[BaseException], ...] = (
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "dry_run_payload or dry_run_checks or dry_run_refuses or nothing_left or blocker_outside" -v`
Expected: 6 passed.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: all pass (no existing test touched).

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): build the milestone dry-run payload from the census

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 2: `run --milestone --dry-run` end to end on a real board

**Files:**
- Modify: `src/agent_manager/cli.py` (new `dry_run_milestone` after `dry_run_payload`; `run` command at the `@app.command("run")` block, originally lines 776-820)
- Test: `tests/test_cli.py`, insert before `@pytest.fixture\ndef projection(tmp_path, monkeypatch) -> Path:` (originally line 1924)

**Interfaces:**
- Consumes: `cli.dry_run_payload(...)` and the `_plan_id`/`_plan_story`/`_plan_subtask` test helpers from Task 1; `cli.resolve_repo_dir(repo_dir) -> Path`; `board.roots(*, repo_dir) -> list[models.CardNode]`; `board.tree(card_id, *, repo_dir) -> models.CardNode`; `census.find_milestone(roots, needle) -> models.CardNode`; `census.flatten_milestone(root) -> census.Census`; the existing test helpers `_git`, `_add_card`, `project` fixture, `runner`, `requires_git`, `requires_brd`.
- Produces: `cli.dry_run_milestone(needle: str, *, repo_dir: Path, branch_prefix: str, base_branch: str) -> dict[str, Any]`. The `run` command now takes `card: str | None` (`--card`, default `None`), `milestone: str | None` (`--milestone`, default `None`) and `dry_run: bool` (`--dry-run`, default `False`). Test helpers `_block`, `milestone_board` fixture, `_m2_branch`, `_dry_run`, `_Forbidden`, `_forbid_writes` and `_assert_nothing_written` are reused by Task 3.

- [ ] **Step 1: Add the Engine-tier fixtures and helpers**

In `tests/test_cli.py`, insert immediately before the lines

```python
@pytest.fixture
def projection(tmp_path, monkeypatch) -> Path:
```

this block:

```python
def _block(root: Path, card_id: str, blocker: str) -> None:
    subprocess.run(
        ["brd", "block", card_id, "--by", blocker],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


M2_SHAPE = (
    ("A", "Story A: the board adapter", ("a1: read one card", "a2: read a subtree")),
    ("B", "Story B: the store", ("b1: the schema", "b2: the journal", "b3: replay")),
    ("C", "Story C: the CLI", ("c1: run --card", "c2: status")),
)
"""Milestone 2's shape: three chained stories with two or three subtasks each."""


@pytest.fixture
def milestone_board(project) -> dict[str, Any]:
    """A real brd board shaped like milestone 2, next to a decoy milestone.

    B is blocked by A and C by B. Each story's subtasks are chained with
    `brd block` so the census order does not depend on creation timestamps.
    The decoy root shares the word "skeleton", so only a longer substring
    names milestone 2.
    """
    _add_card(project, "Milestone 1: walking skeleton")
    milestone = _add_card(project, "Milestone 2: make the skeleton real")
    stories: dict[str, str] = {}
    subtasks: dict[str, list[str]] = {}
    titles: dict[str, str] = {}
    previous_story: str | None = None
    for key, story_title, subtask_titles in M2_SHAPE:
        story = _add_card(project, story_title, milestone)
        titles[story] = story_title
        if previous_story is not None:
            _block(project, story, previous_story)
        chain: list[str] = []
        for subtask_title in subtask_titles:
            subtask = _add_card(project, subtask_title, story)
            titles[subtask] = subtask_title
            if chain:
                _block(project, subtask, chain[-1])
            chain.append(subtask)
        stories[key] = story
        subtasks[key] = chain
        previous_story = story
    return {"milestone": milestone, "stories": stories, "subtasks": subtasks, "titles": titles}


def _m2_branch(project: Path, card_id: str) -> str:
    return dag.task_branch("m2", board.show(card_id, repo_dir=project))


def _dry_run(project: Path, needle: str, *extra: str):
    return runner.invoke(
        cli.app,
        [
            "run",
            "--milestone",
            needle,
            "--dry-run",
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m2",
            *extra,
        ],
    )


class _Forbidden:
    """Stands in for anything the dry path must never reach, and fails loudly.

    `pytest.fail` raises a `BaseException`, which `CliRunner` does not swallow
    and `HANDLED` does not catch, so reaching one of these fails the test
    instead of turning into an envelope.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        pytest.fail(f"the milestone dry run reached cli.{self._name}")

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("__"):
            raise AttributeError(attr)
        pytest.fail(f"the milestone dry run reached cli.{self._name}.{attr}")


def _forbid_writes(monkeypatch) -> None:
    monkeypatch.setattr(cli, "run_card", _Forbidden("run_card"))
    monkeypatch.setattr(cli, "Store", _Forbidden("Store"))
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))
    monkeypatch.setattr(cli.board, "set_status", _Forbidden("board.set_status"))


def _assert_nothing_written(project: Path, porcelain_before: str) -> None:
    """No run dir or projection, no worktree, no branch, no repo change."""
    assert list(paths.data_dir().iterdir()) == []
    worktrees = [
        line
        for line in _git(project, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    assert len(worktrees) == 1, worktrees
    assert _git(project, "branch", "--format=%(refname:short)").split() == ["main"]
    assert not (project / ".claude").exists()
    assert _git(project, "status", "--porcelain") == porcelain_before
```

- [ ] **Step 2: Write the failing happy-path Engine tests**

Append directly after the block from Step 1 (still before `@pytest.fixture\ndef projection`):

```python
@requires_git
@requires_brd
def test_the_milestone_dry_run_stacks_each_story_on_the_previous_ones_tip(
    project, milestone_board, monkeypatch
):
    stories = milestone_board["stories"]
    subtasks = milestone_board["subtasks"]
    titles = milestone_board["titles"]
    board_before = board.roots(repo_dir=project)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    result = _dry_run(project, milestone_board["milestone"])

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == {"levels", "already_done"}
    assert data["already_done"] == []
    assert [level["level"] for level in data["levels"]] == [0, 1, 2]
    assert [
        [story["story"] for story in level["stories"]] for level in data["levels"]
    ] == [[stories["A"]], [stories["B"]], [stories["C"]]]

    previous_tip = "main"
    for level, key in zip(data["levels"], "ABC"):
        (story,) = level["stories"]
        branches = [_m2_branch(project, subtask) for subtask in subtasks[key]]
        assert story["title"] == titles[stories[key]]
        assert story["root"] == previous_tip
        assert [row["id"] for row in story["subtasks"]] == subtasks[key]
        assert [row["title"] for row in story["subtasks"]] == [
            titles[subtask] for subtask in subtasks[key]
        ]
        assert [row["status"] for row in story["subtasks"]] == ["todo"] * len(subtasks[key])
        assert [row["branch"] for row in story["subtasks"]] == branches
        # The base column: the first subtask on the previous story's tip (or
        # the base branch), every later one on the subtask before it.
        assert [row["base"] for row in story["subtasks"]] == [previous_tip, *branches[:-1]]
        assert story["root"] == story["subtasks"][0]["base"]
        previous_tip = branches[-1]

    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


@requires_git
@requires_brd
def test_done_work_is_already_done_and_still_anchors_the_stack(
    project, milestone_board, monkeypatch
):
    stories = milestone_board["stories"]
    subtasks = milestone_board["subtasks"]
    titles = milestone_board["titles"]
    for subtask in subtasks["A"]:
        board.set_status(subtask, "done", repo_dir=project)
    board.set_status(stories["A"], "done", repo_dir=project)
    board.set_status(subtasks["B"][0], "done", repo_dir=project)
    board_before = board.roots(repo_dir=project)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    result = _dry_run(project, milestone_board["milestone"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["already_done"] == [
        {"kind": "story", "id": stories["A"], "title": titles[stories["A"]]},
        {
            "kind": "subtask",
            "id": subtasks["B"][0],
            "title": titles[subtasks["B"][0]],
            "story": stories["B"],
        },
    ]
    assert [
        [story["story"] for story in level["stories"]] for level in data["levels"]
    ] == [[stories["B"]], [stories["C"]]]

    a_tip = _m2_branch(project, subtasks["A"][-1])
    b_branches = [_m2_branch(project, subtask) for subtask in subtasks["B"]]
    (b_row,) = data["levels"][0]["stories"]
    assert b_row["root"] == a_tip
    assert [row["id"] for row in b_row["subtasks"]] == subtasks["B"][1:]
    assert [row["base"] for row in b_row["subtasks"]] == b_branches[:2]
    (c_row,) = data["levels"][1]["stories"]
    assert c_row["root"] == b_branches[-1]

    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


@requires_git
@requires_brd
def test_a_title_substring_names_the_same_milestone_as_its_id(
    project, milestone_board, monkeypatch
):
    _forbid_writes(monkeypatch)

    by_id = _dry_run(project, milestone_board["milestone"])
    by_title = _dry_run(project, "skeleton real")

    assert by_id.exit_code == 0, by_id.output
    assert by_title.exit_code == 0, by_title.output
    assert json.loads(by_title.stdout) == json.loads(by_id.stdout)


@requires_git
@requires_brd
def test_the_milestone_dry_run_pretty_indents_the_same_envelope(
    project, milestone_board, monkeypatch
):
    _forbid_writes(monkeypatch)

    plain = _dry_run(project, milestone_board["milestone"])
    pretty = _dry_run(project, milestone_board["milestone"], "--pretty")

    assert pretty.exit_code == 0, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)
```

- [ ] **Step 3: Write the failing refusal Engine tests**

Append directly after the tests from Step 2:

```python
def _refusal(result) -> dict[str, Any]:
    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    return envelope["error"]


@requires_git
@requires_brd
def test_a_story_cycle_from_the_board_is_an_envelope_naming_both_stories(
    project, monkeypatch
):
    """brd refuses to store a cycle, so the tree is served by hand. The census
    orders sibling stories by `blocked_by` and meets the cycle first, so the
    refusal is `CensusOrderError`, before any geometry."""
    milestone = _add_card(project, "Milestone 9: cyclic")
    a, b = _plan_id(1), _plan_id(2)

    def tree(card_id, *, repo_dir=None):
        return models.CardNode(
            id=milestone,
            title="Milestone 9: cyclic",
            status="todo",
            children=[
                models.CardNode(
                    id=a,
                    title="story a",
                    status="todo",
                    blocked_by=[b],
                    children=[models.CardNode(id=_plan_id(11), title="a1", status="todo")],
                ),
                models.CardNode(
                    id=b,
                    title="story b",
                    status="todo",
                    blocked_by=[a],
                    children=[models.CardNode(id=_plan_id(21), title="b1", status="todo")],
                ),
            ],
        )

    monkeypatch.setattr(cli.board, "tree", tree)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(project, milestone))

    assert error["type"] == "CensusOrderError"
    assert a in error["message"]
    assert b in error["message"]
    _assert_nothing_written(project, porcelain_before)


@requires_git
@requires_brd
def test_a_story_cycle_in_the_census_is_named_as_a_trail_by_the_dag_check(
    project, monkeypatch
):
    """The spec's `DependencyCycleError` path: a cyclic census that got past
    ordering is refused by `assert_no_blocker_cycles`, through the envelope."""
    milestone = _add_card(project, "Milestone 9: cyclic")
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    monkeypatch.setattr(
        cli.census,
        "flatten_milestone",
        lambda root: census.Census(milestone_title=root.title, stories=[a, b]),
    )
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(project, milestone))

    assert error["type"] == "DependencyCycleError"
    assert f"#{a.id} -> #{b.id} -> #{a.id}" in error["message"]
    _assert_nothing_written(project, porcelain_before)


@requires_git
@requires_brd
def test_a_story_blocked_by_two_stories_is_an_envelope_naming_both(project, monkeypatch):
    milestone = _add_card(project, "Milestone 8: diamond")
    first = _add_card(project, "Story one", milestone)
    second = _add_card(project, "Story two", milestone)
    joined = _add_card(project, "Story three", milestone)
    for story in (first, second, joined):
        _add_card(project, f"only subtask of {story}", story)
    _block(project, joined, first)
    _block(project, joined, second)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(project, milestone))

    assert error["type"] == "StackRootError"
    assert f"#{first}" in error["message"]
    assert f"#{second}" in error["message"]
    _assert_nothing_written(project, porcelain_before)


@requires_git
@requires_brd
def test_an_unknown_milestone_is_an_envelope(project, milestone_board, monkeypatch):
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(project, "Milestone 404"))

    assert error["type"] == "MilestoneNotFoundError"
    assert "Milestone 404" in error["message"]


def test_a_milestone_dry_run_with_a_missing_repo_dir_is_an_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(tmp_path / "missing", "2"))

    assert error["type"] == "RepoDirError"
    assert "missing" in error["message"]


@requires_brd
def test_a_milestone_dry_run_outside_a_brd_project_is_a_board_error(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    plain = tmp_path / "plain"
    plain.mkdir()
    _forbid_writes(monkeypatch)

    error = _refusal(_dry_run(plain, "2"))

    assert error["type"] == "BoardError"
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 4: Run the new Engine tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "milestone_dry_run or done_work_is_already_done or title_substring or story_cycle or blocked_by_two_stories or unknown_milestone" -v`
Expected: all FAIL. Each invocation exits 2 with Typer's `No such option: --milestone`, so the `exit_code == 0` / `== cli.EXIT_ERROR` asserts fail.

- [ ] **Step 5: Implement `dry_run_milestone`**

In `src/agent_manager/cli.py`, replace

```python
    return {"levels": level_rows, "already_done": already_done_entries(stories)}


HANDLED: tuple[type[BaseException], ...] = (
```

with

```python
    return {"levels": level_rows, "already_done": already_done_entries(stories)}


def dry_run_milestone(
    needle: str, *, repo_dir: Path, branch_prefix: str, base_branch: str
) -> dict[str, Any]:
    """O3's order: repo dir, roots, milestone, tree, census, then the payload.

    Read-only by construction. The two `brd` reads are its only I/O. No
    `Store` is opened (that would mint a run directory), no runner is built,
    and nothing is fetched, pruned, branched or written to the board. Every
    refusal is a type already in `HANDLED`.
    """
    root = resolve_repo_dir(repo_dir)
    milestone = census.find_milestone(board.roots(repo_dir=root), needle)
    plan = census.flatten_milestone(board.tree(milestone.id, repo_dir=root))
    return dry_run_payload(
        plan.stories, branch_prefix=branch_prefix, base_branch=base_branch
    )


HANDLED: tuple[type[BaseException], ...] = (
```

- [ ] **Step 6: Give the `run` command `--milestone` and `--dry-run`**

In `src/agent_manager/cli.py`, replace the whole command, from

```python
@app.command("run")
def run(
    card: str = typer.Option(..., "--card", help="The subtask card id to drive."),
```

through

```python
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    if payload["status"] == "escalated":
        raise typer.Exit(EXIT_ESCALATED)


def status_for(run_id: str | None, *, repo_dir: Path) -> dict[str, Any]:
```

with

```python
@app.command("run")
def run(
    card: str | None = typer.Option(
        None, "--card", help="The subtask card id to drive. Exclusive with --milestone."
    ),
    milestone: str | None = typer.Option(
        None,
        "--milestone",
        help="A milestone card id or title substring. Needs --dry-run for now.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="With --milestone: print the levels and stack bases, and write nothing.",
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    base_branch: str = typer.Option(
        "master", "--base-branch", help="The branch this subtask's branch is cut from."
    ),
    branch_prefix: str = typer.Option(
        ...,
        "--branch-prefix",
        help="Milestone prefix for the derived branch name, e.g. `m2`.",
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help="Proceed even when no verification suite is available (§12's opt-out).",
    ),
    verify: list[str] = typer.Option(
        [],
        "--verify",
        help=(
            "One whole verification command, repeatable. Passed through verbatim "
            "and in the order given; the engine runs them in sequence."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Drive one subtask card end to end, or preview a milestone with --dry-run."""
    try:
        if milestone is not None and dry_run:
            payload = dry_run_milestone(
                milestone,
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
            )
        else:
            payload = run_card(
                card,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                allow_no_verification=allow_no_verification,
                commands=list(verify),
            )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    if milestone is None and payload["status"] == "escalated":
        raise typer.Exit(EXIT_ESCALATED)


def status_for(run_id: str | None, *, repo_dir: Path) -> dict[str, Any]:
```

(Task 3 adds the argument checks and the not-implemented refusal to this same command.)

- [ ] **Step 7: Run the new Engine tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "milestone_dry_run or done_work_is_already_done or title_substring or story_cycle or blocked_by_two_stories or unknown_milestone" -v`
Expected: all passed (the `requires_git`/`requires_brd` ones skip only if those CLIs are missing, and they are not on this machine).

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: all pass. The existing `run --card` tests, including `test_run_without_branch_prefix_is_a_usage_error_not_an_envelope` and `test_the_run_success_envelope_keys_are_frozen`, are unmodified and green.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): preview a milestone's stack geometry with run --milestone --dry-run

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 3: Argument validation and the not-yet-implemented milestone run

**Files:**
- Modify: `src/agent_manager/cli.py` (new `MilestoneRunNotImplementedError` after `NotResumableError`; new `_check_run_targets` just above `@app.command("run")`; dispatch inside `run`)
- Test: `tests/test_cli.py`, append right after `test_a_milestone_dry_run_outside_a_brd_project_is_a_board_error` (from Task 2)

**Interfaces:**
- Consumes: the `run` command from Task 2; the `_Forbidden`, `_forbid_writes` and `_refusal` test helpers from Task 2; `runner`, `paths`.
- Produces: `cli.MilestoneRunNotImplementedError(CliError)` and `cli._check_run_targets(*, card: str | None, milestone: str | None, dry_run: bool) -> None`, which raises `typer.BadParameter` (exit 2).

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, append right after `test_a_milestone_dry_run_outside_a_brd_project_is_a_board_error`:

```python
SOME_CARD = "cbe34d00-9d8d-4f41-9c94-f99e665771b0"


@pytest.mark.parametrize(
    "targets, word",
    [
        (["--card", SOME_CARD, "--milestone", "2"], "both"),
        (["--card", SOME_CARD, "--milestone", "2", "--dry-run"], "both"),
        ([], "required"),
        (["--dry-run"], "required"),
        (["--card", SOME_CARD, "--dry-run"], "previews"),
        (["--milestone", "", "--dry-run"], "blank"),
        (["--milestone", "   ", "--dry-run"], "blank"),
    ],
)
def test_bad_run_targets_are_usage_errors_that_start_nothing(
    tmp_path, monkeypatch, targets, word
):
    """Validation happens before the `HANDLED` try block, so these are Typer's
    exit 2 and never an envelope. Nothing is dispatched: `run_card` and
    `dry_run_milestone` are both forbidden here."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))

    result = runner.invoke(
        cli.app,
        ["run", *targets, "--repo-dir", str(tmp_path), "--branch-prefix", "m2"],
    )

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert word in result.output
    assert list(paths.data_dir().iterdir()) == []


def test_a_milestone_run_without_dry_run_is_a_not_implemented_envelope(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))

    error = _refusal(
        runner.invoke(
            cli.app,
            [
                "run",
                "--milestone",
                "2",
                "--repo-dir",
                str(tmp_path),
                "--branch-prefix",
                "m2",
            ],
        )
    )

    assert error["type"] == "MilestoneRunNotImplementedError"
    assert "not implemented" in error["message"]
    assert "--dry-run" in error["message"]
    assert not (paths.data_dir() / "runs").exists()
    assert list(paths.data_dir().iterdir()) == []


def test_the_not_implemented_refusal_is_a_cli_error():
    assert issubclass(cli.MilestoneRunNotImplementedError, cli.CliError)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "bad_run_targets or not_implemented" -v`
Expected: FAIL. The "both", "required" and "previews" cases reach the forbidden `run_card` (pytest `Failed: the milestone dry run reached cli.run_card`). The "blank" cases reach the forbidden `dry_run_milestone`. The not-implemented test reaches `run_card`. `test_the_not_implemented_refusal_is_a_cli_error` fails with `AttributeError: ... has no attribute 'MilestoneRunNotImplementedError'`.

- [ ] **Step 3: Add the refusal type**

In `src/agent_manager/cli.py`, replace

```python
    status this message names, and a script can branch on the `type` field.
    """


def resolve_repo_dir(repo_dir: Path) -> Path:
```

with

```python
    status this message names, and a script can branch on the `type` field.
    """


class MilestoneRunNotImplementedError(CliError):
    """`run --milestone` without `--dry-run`: the real milestone run is not built yet.

    A `CliError` so it rides `HANDLED` into an `ok: false` envelope at exit 3.
    It is raised before the repo dir is resolved or the board is read, so a
    refusal reads nothing and writes nothing. The next story replaces it with
    the real run.
    """


def resolve_repo_dir(repo_dir: Path) -> Path:
```

- [ ] **Step 4: Add the argument checks and the dispatch**

In `src/agent_manager/cli.py`, replace

```python
@app.command("run")
def run(
    card: str | None = typer.Option(
```

with

```python
def _check_run_targets(*, card: str | None, milestone: str | None, dry_run: bool) -> None:
    """Refuse a bad `--card` / `--milestone` / `--dry-run` combination as a usage error.

    `typer.BadParameter` is Typer's own exit 2, which `EXIT_ERROR`'s docstring
    reserves. It is raised before the `HANDLED` try block, so nothing is read
    or dispatched. A blank `--milestone` is refused here too: the census strips
    the needle, and an empty needle is a substring of every title, so on a
    one-milestone board it would silently pick that milestone.
    """
    if card is not None and milestone is not None:
        raise typer.BadParameter(
            "give --card or --milestone, not both",
            param_hint="'--card' / '--milestone'",
        )
    if card is None and milestone is None:
        raise typer.BadParameter(
            "one of --card or --milestone is required",
            param_hint="'--card' / '--milestone'",
        )
    if milestone is not None and not milestone.strip():
        raise typer.BadParameter(
            "--milestone needs a card id or a title substring, not a blank string",
            param_hint="'--milestone'",
        )
    if dry_run and card is not None:
        raise typer.BadParameter(
            "--dry-run previews a milestone and does not apply to --card",
            param_hint="'--dry-run'",
        )


@app.command("run")
def run(
    card: str | None = typer.Option(
```

Then, in the same command body, replace

```python
    """Drive one subtask card end to end, or preview a milestone with --dry-run."""
    try:
        if milestone is not None and dry_run:
            payload = dry_run_milestone(
                milestone,
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
            )
        else:
            payload = run_card(
```

with

```python
    """Drive one subtask card end to end, or preview a milestone with --dry-run."""
    _check_run_targets(card=card, milestone=milestone, dry_run=dry_run)
    try:
        if milestone is not None and not dry_run:
            raise MilestoneRunNotImplementedError(
                f"milestone runs are not implemented yet; `run --milestone {milestone}"
                " --dry-run` previews the levels and stack bases without writing anything"
            )
        if milestone is not None:
            payload = dry_run_milestone(
                milestone,
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
            )
        else:
            payload = run_card(
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "bad_run_targets or not_implemented" -v`
Expected: 9 passed (7 parametrized cases and 2 tests).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all pass, including every Task 1 and Task 2 test and the unmodified `run --card` tests. (`test_a_repo_dir_that_is_not_a_directory_is_an_envelope` still gets `RepoDirError`: `--card` alone passes `_check_run_targets`.)

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): validate run's card/milestone targets and refuse real milestone runs for now

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

## Spec coverage map

- Options (`--card` optional, `--milestone`, `--dry-run`, `--branch-prefix` required, `--base-branch` default `master`): Task 2 Step 6.
- Usage errors (both, neither, `--dry-run` with `--card`), exit 2: Task 3, spec test 7.
- `--card` without `--dry-run` unchanged: Task 2 Step 6 dispatch, with the existing tests run in Task 2 Step 8 and Task 3 Step 6.
- `--milestone` without `--dry-run` gives `MilestoneRunNotImplementedError`, exit 3, message naming `--dry-run`: Task 3, spec test 8.
- Dry-run flow order (repo dir, roots, find, tree, flatten, cycle check before geometry, levels, bases/root/branch): Task 2 Step 5 and Task 1 Step 6. Cycle-first is proven by `test_the_dry_run_checks_for_blocker_cycles_before_any_geometry`.
- `stories_by_id` over all stories including closed ones: Task 1 `test_the_dry_run_payload_lists_remaining_subtasks_on_full_list_bases` and Task 2 `test_done_work_is_already_done_and_still_anchors_the_stack` (spec test 2).
- Payload shape and `already_done` shape/order: Task 1 (spec test 9) and Task 2 (spec tests 1 and 2).
- No writes (Store, run_card, runner, run dir, worktree, branch, board write): `_forbid_writes` and `_assert_nothing_written` in Task 2, spec tests 1, 2 and 4.
- Title substring equals id: spec test 3, Task 2.
- Cycle refusal: spec test 4, Task 2. There are two tests, because a real census refuses a story cycle as `CensusOrderError` before the dag check (see Review Focus 5), and an injected census proves the `DependencyCycleError` envelope.
- Two-blocker refusal: spec test 5, Task 2 (Engine) and Task 1 (unit).
- Unknown milestone: spec test 6, Task 2. `RepoDirError` and `BoardError` paths: Task 2.
- No `HANDLED` change, and no change outside `cli.py` / `tests/test_cli.py`: every task's Files block.
