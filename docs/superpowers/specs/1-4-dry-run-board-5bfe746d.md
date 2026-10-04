# Dry-run board reports each milestone's `base_branch` — design

Date: 2026-10-04
Card: `5bfe746d` (subtask 1.4 of story `8fd3e3de`)
Blocked by: `5b772688` (1.3, `run_board` stacks each milestone on its own base), done on this branch
Status: approved scope (card), pre-plan

## 0. Parent spec and line references

Parent: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`.
"parent L<n>" below means a line in that file. Source and test line numbers
are from this branch's HEAD (`26b9471`).

Inherited constraints:

| constraint | parent |
|---|---|
| `--dry-run --board` reports `base_branch` on each milestone entry: `{"milestone_id","title","branch_prefix","base_branch","plan"}` | §1, L71-72 |
| Each milestone's `plan` is computed against that base, so the `base` column shows the stacking | L72-74 |
| The new key is additive | L74; Compatibility L102 |
| The base of each milestone is the table at L41-47: no open/unlanded blocker → `--base-branch`; one open blocker → its `<prefix>-integrate`; one `done` blocker whose integrate branch exists locally → that branch; `done` blocker without it → `--base-branch`; two or more → refused | L39-47 |
| Two or more stack candidates is `MilestoneBlockersError`, exit 3, usual envelope | L52-56 |
| `prefix(B)` for a blocker that is no longer open uses the same derivation | L57-61 |
| The decision is the pure `milestone_bases(milestones, prefixes, branch_exists, base_branch)` with `branch_exists` injected | L62-64 |
| Boards with no inter-milestone `blocked_by` edges behave exactly as today | Compatibility, L100 |
| A `done` blocker with no local integrate branch behaves as today | L101 |
| All JSON changes additive; journal and watch schema stay 1 | L102 |
| Unit testing covers "dry-run `base_branch` and `base` column" | Testing, L107-111 |
| `am` never touches `--base-branch` | Non-goals, L30 |

Already in place (cards 1.1-1.3), consumed unchanged by this card:

- `orchestrate.board_prefixes(milestones, branch_prefix_of, *, roots=())`
  (`orchestrate.py:2198`): keys the given milestones, then their direct
  blocker roots from `roots`, of any status.
- `orchestrate.MilestoneBlockersError(ValueError)` (`orchestrate.py:2248`).
- `orchestrate.milestone_bases(milestones, prefixes, branch_exists, base_branch)`
  (`orchestrate.py:2273`): `milestones` is every root; only open ones get a
  key; `branch_exists` is asked only for an unlanded, non-open blocker.
- `orchestrate._local_branch_exists(root) -> Callable[[str], bool]`
  (`orchestrate.py:2367`): read-only `git -C <root> rev-parse --verify --quiet
  refs/heads/<branch>`; exit 1 is `False`, any other `GitError` propagates.
- `orchestrate.run_board` (`orchestrate.py:2394`, composition at L2453-2457):
  `all_roots = board.roots(...)`; `levels = dag.board_levels(all_roots)`;
  `prefixes = board_prefixes(milestones, ..., roots=all_roots)`;
  `bases = milestone_bases(all_roots, prefixes, _local_branch_exists(root), base_branch)`.
- `cli.HANDLED` contains `ValueError` (`cli.py:1365-1373`), so
  `MilestoneBlockersError` reaching the CLI is already `ok: false`, exit 3.

## 1. Purpose

`cli.dry_run_board` (`cli.py:1315-1362`) previews every open milestone, but
it still computes every milestone's `plan` on the one shared `--base-branch`
(L1353) and never consults `milestone_bases`. Since 1.3, the real run stacks
a blocked milestone on its blocker's integrate branch, so the preview now
lies about where a stacked milestone's stories will start, and it does not
refuse a board that the real run refuses with `MilestoneBlockersError`.

This card makes the board dry run compute each milestone's base exactly as
`run_board` does, report it as `base_branch` on each entry, and plan each
milestone against it.

## 2. Behavior

### 2.1 `cli.dry_run_board`

Signature unchanged:

```python
def dry_run_board(
    *, repo_dir: Path, branch_prefix: str | None, base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]
```

Behavior, in order (mirrors `run_board`'s refusal order, parent L52-54):

1. `root = resolve_repo_dir(repo_dir)`.
2. `board.roots(repo_dir=root)` is read **once**; call it `all_roots`.
3. `dag.board_levels(all_roots)` levels the open milestones (a done milestone
   drops out; a cycle is `DependencyCycleError`). Unchanged.
4. Prefixes come from `orchestrate.board_prefixes(milestones,
   board_prefix_of(branch_prefix), roots=all_roots)`: every open milestone
   plus each non-open direct blocker root, so a non-open blocker's integrate
   branch can be named (parent L57-61). Blank/shared prefixes stay
   `ValueError`. Note: a non-open blocker root sharing a prefix with an open
   milestone is now also refused, exactly as `run_board` refuses it.
5. Bases come from `orchestrate.milestone_bases(all_roots, prefixes,
   orchestrate._local_branch_exists(root), base_branch)`. `_local_branch_exists`
   is looked up on the `orchestrate` module at call time (so a test can
   replace it with `monkeypatch.setattr(orchestrate, "_local_branch_exists", ...)`).
   Two or more stack candidates for one milestone is `MilestoneBlockersError`,
   which reaches the CLI as the `ok: false` envelope, exit 3, nothing written.
6. Each milestone entry becomes:

   ```json
   {"milestone_id": "...", "title": "...", "branch_prefix": "<prefix>",
    "base_branch": "<bases[milestone_id]>", "plan": { ... }}
   ```

   where `plan` is `dry_run_payload(census.flatten_milestone(card).stories,
   repo_dir=root, branch_prefix=prefixes[card.id],
   base_branch=bases[card.id], max_concurrent=max_concurrent)`.

   Consequence: in a stacked milestone's `plan`, every unblocked story's
   `root` and its first remaining subtask's `base` read
   `<blocker prefix>-integrate`, and `integrate.order` tips are computed on
   that base too. In an unstacked milestone they read `--base-branch`, as
   today.
7. The envelope `data` is unchanged otherwise: exactly
   `{"board", "max_concurrent", "levels"}`; each level `{"level", "milestones"}`;
   no `ok`, no `run_id` inside `data`.

`base_branch` on an entry is always a non-empty string when `--base-branch`
is (CLI default `master`, `cli.py:1692-1694`). Only milestones that appear
in `levels` get an entry; `bases` may also hold no key for a non-open root,
and none is reported.

### 2.2 Read-only guarantees (unchanged, re-pinned)

- No `Store` is opened, no claim is checked (`refuse_claimed` never called),
  no runner is built, `orchestrate.run_board` / `run_milestone` and
  `cli.dry_run_milestone` are never called, nothing is written under the data
  dir, the board is unchanged.
- The only new I/O is the read-only `git rev-parse` per (open milestone,
  unlanded non-open blocker) pair. Open and landed (`merged`/`canceled`/
  `archived`) blockers never trigger git.
- A board with no inter-milestone `blocked_by` edges produces byte-identical
  `plan`s to today, plus the additive `base_branch` key equal to
  `--base-branch` (parent L100).

### 2.3 Error paths

| condition | result |
|---|---|
| a milestone with two open blockers (or one open + one unlanded with branch, or two unlanded with branches) | `ok: false`, `error.type == "MilestoneBlockersError"`, exit 3 (`cli.EXIT_ERROR`), nothing written |
| milestone cycle, non-uuid milestone id, two open milestones deriving one prefix | unchanged: `DependencyCycleError` / `ValueError`, exit 3 |
| a non-open blocker root deriving the same prefix as an open milestone | `ValueError`, exit 3 (new, from `board_prefixes(roots=)`; same as `run_board`) |
| `git rev-parse` fails with anything but exit 1 (e.g. `--repo-dir` not a git repository) while checking an unlanded blocker | `GitError` propagates unchanged, as it does from `run_board`; not converted here |

## 3. Documentation

`README.md` L181 (the `--dry-run` with `--board` paragraph): the milestone
entry shape becomes `{"milestone_id", "title", "branch_prefix",
"base_branch", "plan"}`; add that `base_branch` is the branch the milestone
would start from (the `--base-branch`, or its one blocker milestone's
`<prefix>-integrate` when it stacks), that `plan` is computed against it so
the `base` column shows the stacking, and that a milestone with two or more
open/unlanded blockers is refused the same way as the real run. Only this
paragraph changes.

## 4. Tests

All in `tests/test_cli.py`, written first (TDD). Helpers changed:

- `_expected_board_milestone(card, prefix, *, root, max_concurrent,
  base_branch="main")` gains `base_branch`, emits it as the `"base_branch"`
  key and passes it to `cli.dry_run_payload`.
- A small helper (e.g. `_answer_local_branches(monkeypatch, existing:
  set[str])`) that replaces `orchestrate._local_branch_exists` with a factory
  returning `lambda branch: branch in existing`, recording each asked branch
  and the `root` it was built for.

| # | test | tier | why this tier |
|---|---|---|---|
| T1 | Update `test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id` (L3620): entry keys == `{"milestone_id","title","branch_prefix","base_branch","plan"}`; its docstring names the new key. | unit | `_serve_roots` fake, tmp_path, no subprocess (both milestones open, so git is never asked) |
| T2 | No stacking: two independent open milestones (no `blocked_by`) → each entry's `base_branch == "main"` and `plan == dry_run_payload(..., base_branch="main")`; git never asked (`_answer_local_branches` records no call). | unit | fakes only |
| T3 | Stacking on an open blocker: M1 open, M2 `blocked_by` M1 → M1 `base_branch == "main"`; M2 `base_branch == integration.integration_branch(prefix(M1))`; M2's `plan == dry_run_payload(..., base_branch=<that>)`; M2's level-0 story `root` and first subtask `base` equal it. Also run once with `--branch-prefix sprint9` so the stacked base is `sprint9-<stem1>-integrate`. | unit | fakes only; open blockers never call git |
| T4 | Unlanded `done` blocker: M1 `done` (dropped from levels), M2 blocked by M1. With the fake answering M1's integrate branch exists → M2 `base_branch == "<stem1>-integrate"`; with it absent → `"main"`. Asserts the fake was built for `cli.resolve_repo_dir(tmp_path)` and asked exactly `["<stem1>-integrate"]`. | unit | the git seam is replaced; no subprocess |
| T5 | Landed blockers: M1 `merged` / `canceled` / `archived` (parametrized), M2 blocked by it → M2 `base_branch == "main"`; git never asked. | unit | fakes only |
| T6 | Refusal: M3 blocked by two open milestones M1, M2 → `_refusal(...)` `type == "MilestoneBlockersError"`, exit 3, `paths.data_dir()` empty. Add as a parametrize case of `test_a_board_dry_run_refusal_is_an_envelope_and_writes_nothing` (L3680) or as its own test. | unit | fakes only |
| T7 | Update `test_the_board_dry_run_derives_prefixes_from_a_given_prefix_and_drops_done_milestones` (L3564): its `done` M1 blocks M2, so it now asks git; install `_answer_local_branches(monkeypatch, set())` and expect every entry's `base_branch == "main"`, M3's `base_branch == "sprint9-<stem2>-integrate"` (M3 is blocked by the open M2) and its `plan` against that base. | unit | without the fake it would spawn git on a non-repo tmp_path (exit 128) — forbidden in the unit tier |
| T8 | Update `test_the_board_dry_run_previews_every_open_milestone_by_level_and_writes_nothing` (L3478): second milestone's expected entry has `base_branch == "<stem(first)>-integrate"` and its plan against that base; key set includes `base_branch`. | `git` + `brd` (existing markers) | real brd board and real repo already; unchanged tier |
| T9 | Real git seam: `git init` a repo in tmp_path, create local branch `<stem1>-integrate` (and separately a **tag** of that name with no branch), serve roots (M1 `done`, M2 blocked by M1) with `_serve_roots`, run the dry run without replacing `_local_branch_exists` → branch case stacks on it; tag-only case keeps `main`. | `git` | real `git` in tmp_path, no brd, no claude |
| T10 | Read-only re-pin: T3 and T4 run under `_forbid_board_dry_run_writes` and assert `paths.data_dir()` is empty afterwards. | unit | covered inside T3/T4, no extra test needed |

`test_an_empty_board_dry_runs_to_no_levels_and_exits_zero` (L3605) stays
as is: an empty board asks nothing and still returns `levels: []`.

## 5. Out of scope

- `run_board`, `milestone_bases`, `board_prefixes`, `_local_branch_exists`:
  consumed as landed by 1.1-1.3; not changed. No public alias is added for
  `_local_branch_exists`.
- Converting `GitError` into an envelope (it is not in `HANDLED` for
  `run_board` either).
- Refusing an empty `--base-branch` in the dry run (the real run's guard is
  not part of this card).
- `--detach` for `--board` (story 2 of the parent spec) and keeping
  `--detach --board --dry-run` refused (parent L92) — sibling cards.
- The `e2e_fake` stacking scenarios (parent L112-115) — sibling cards.
- `dry_run_milestone` / `--milestone --dry-run`: a single milestone has no
  milestone-level blockers in this design; unchanged.
- Any journal or watch schema change (stays 1).
