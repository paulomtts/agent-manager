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
