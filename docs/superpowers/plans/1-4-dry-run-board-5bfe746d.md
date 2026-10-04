# Dry-run board reports each milestone's `base_branch` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am run --board --dry-run` computes each open milestone's base exactly as `run_board` does, reports it as `base_branch` on each milestone entry, and plans each milestone against it.

**Architecture:** One function changes: `cli.dry_run_board` (`src/agent_manager/cli.py:1315-1362`). It reuses the landed `orchestrate.board_prefixes(..., roots=all_roots)` and `orchestrate.milestone_bases(all_roots, prefixes, orchestrate._local_branch_exists(root), base_branch)`, the same composition `run_board` uses (`orchestrate.py:2453-2457`). Tests live in `tests/test_cli.py`; unit tests replace the git seam `orchestrate._local_branch_exists` with a recording fake, one `git`-tier test exercises the real seam. README's board dry-run paragraph is updated.

**Tech Stack:** Python 3, Typer (`typer.testing.CliRunner`), pytest, `uv`.

**Spec:** `docs/superpowers/specs/1-4-dry-run-board-5bfe746d.md` (reproduced verbatim below, before the tasks).

## Global Constraints

- `--dry-run --board` milestone entry shape: `{"milestone_id","title","branch_prefix","base_branch","plan"}` (parent §1, L71-72).
- The new key is additive; all JSON changes additive; journal and watch schema stay 1.
- `data` stays exactly `{"board", "max_concurrent", "levels"}`; each level `{"level", "milestones"}`; no `ok`, no `run_id` inside `data`.
- `dry_run_board` signature is unchanged: `dry_run_board(*, repo_dir: Path, branch_prefix: str | None, base_branch: str, max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> dict[str, Any]`.
- `_local_branch_exists` is looked up on the `orchestrate` module at call time (`orchestrate._local_branch_exists(root)`); no public alias is added.
- `run_board`, `milestone_bases`, `board_prefixes`, `_local_branch_exists` are not changed.
- `GitError` is not converted into an envelope.
- `am` never touches `--base-branch`.
- Test tiers (CLAUDE.md): unmarked = unit, no subprocess of any kind (stub `brd`/`git`/`claude` on `PATH` exit 99); `@pytest.mark.git` = real git in `tmp_path` only. Default suite: `uv run pytest`.
- Read-only: no `Store` opened, `refuse_claimed` never called, no runner built, `run_board`/`run_milestone`/`dry_run_milestone` never called, nothing written under the data dir, board unchanged.

## Review Focus

1. A `done` blocker whose `<prefix>-integrate` exists only as a **tag** (no local branch) → the blocked milestone stays on `--base-branch`, never stacks on the tag. Pinned in Task 2 (`tag-only` case of the real-git test).
2. `--repo-dir` is not a git repository and some milestone has an unlanded (`done`) blocker → the `GitError` propagates (no envelope, non-zero exit), never a silent fallback to `--base-branch`. Pinned in Task 2 (`test_the_board_dry_run_lets_a_git_error_from_a_non_repository_propagate`).
3. A non-open blocker root that derives the same prefix as an open milestone → `ok: false`, `ValueError`, exit 3, nothing written (new refusal, same as `run_board`). Pinned in Task 1 (`blocker-shares-a-prefix` refusal case).
4. A milestone blocked by one open milestone, one landed (`merged`) milestone and one id that is not on the board → stacks on the open one, git never asked, no `MilestoneBlockersError`. Pinned in Task 1 (`test_the_board_dry_run_stacks_on_the_one_open_blocker_among_landed_and_unknown_ones`).
5. `--branch-prefix sprint9` with a stacked milestone → the stacked base is `sprint9-<stem(blocker)>-integrate`, not `<stem>-integrate` and not `sprint9-integrate`. Pinned in Task 1 (`given-prefix` case of the open-blocker stacking test).

---

## The spec (verbatim)

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

---

## File map

- Modify: `src/agent_manager/cli.py:1315-1362` — `dry_run_board`: read roots once into `all_roots`, `board_prefixes(..., roots=all_roots)`, `milestone_bases(...)`, `base_branch` on each entry, plan against it; docstring rewritten.
- Modify: `tests/test_cli.py:3433-3694` — board dry-run helpers and tests (unit tier, plus the existing `git`+`brd` test at L3476).
- Modify: `tests/test_cli.py` (new `git`-tier tests appended after the board dry-run refusal test) — the real git seam.
- Modify: `README.md:181` — the `--dry-run` with `--board` paragraph only.

---

### Task 1: `dry_run_board` reports and plans each milestone on its own base

**Files:**
- Modify: `src/agent_manager/cli.py:1315-1362`
- Test: `tests/test_cli.py` (helpers at L3443-3457, tests at L3476-3693)

**Interfaces:**
- Consumes (already landed, do not change):
  - `orchestrate.board_prefixes(milestones: Sequence[models.CardNode], branch_prefix_of: Callable[[models.CardNode], str], *, roots: Sequence[models.CardNode] = ()) -> dict[str, str]`
  - `orchestrate.milestone_bases(milestones: Sequence[models.CardNode], prefixes: Mapping[str, str], branch_exists: Callable[[str], bool], base_branch: str) -> dict[str, str]` — raises `orchestrate.MilestoneBlockersError` (a `ValueError`) for two or more stack candidates.
  - `orchestrate._local_branch_exists(root: Path) -> Callable[[str], bool]`
  - `integration.integration_branch(branch_prefix: str) -> str` → `f"{branch_prefix}-integrate"`
  - Test helpers already in `tests/test_cli.py`: `_board_milestone(n, *, status="todo", blocked_by=(), card_id=None, title=None)`, `_serve_roots(monkeypatch, roots)`, `_forbid_board_dry_run_writes(monkeypatch)`, `_board_dry_run(repo_dir, *extra)` (passes `--base-branch main`), `_as_json(value)`, `_refusal(result) -> dict`, `_plan_id(n)`, `_block`, `_add_card`, `_git`, `_assert_nothing_written`.
- Produces (Task 2 relies on these):
  - `_expected_board_milestone(card: models.CardNode, prefix: str, *, root: Path, max_concurrent: int, base_branch: str = "main") -> dict[str, Any]` — emits `"base_branch"` and passes it to `cli.dry_run_payload`.
  - `_answer_local_branches(monkeypatch, existing: set[str]) -> dict[str, list[Any]]` — replaces `orchestrate._local_branch_exists`; returns `{"roots": [Path, ...], "asked": [str, ...]}`.
  - Each `dry_run_board` milestone entry: `{"milestone_id", "title", "branch_prefix", "base_branch", "plan"}`.

- [ ] **Step 1: Update the helper `_expected_board_milestone` and add `_answer_local_branches`**

In `tests/test_cli.py`, replace the whole of `_expected_board_milestone` (currently L3443-3457):

```python
def _expected_board_milestone(
    card: models.CardNode, prefix: str, *, root: Path, max_concurrent: int
) -> dict[str, Any]:
    return {
        "milestone_id": card.id,
        "title": card.title,
        "branch_prefix": prefix,
        "plan": cli.dry_run_payload(
            census.flatten_milestone(card).stories,
            repo_dir=root,
            branch_prefix=prefix,
            base_branch="main",
            max_concurrent=max_concurrent,
        ),
    }
```

with:

```python
def _expected_board_milestone(
    card: models.CardNode,
    prefix: str,
    *,
    root: Path,
    max_concurrent: int,
    base_branch: str = "main",
) -> dict[str, Any]:
    return {
        "milestone_id": card.id,
        "title": card.title,
        "branch_prefix": prefix,
        "base_branch": base_branch,
        "plan": cli.dry_run_payload(
            census.flatten_milestone(card).stories,
            repo_dir=root,
            branch_prefix=prefix,
            base_branch=base_branch,
            max_concurrent=max_concurrent,
        ),
    }


def _answer_local_branches(monkeypatch, existing: set[str]) -> dict[str, list[Any]]:
    """Replace `orchestrate._local_branch_exists` with a fake that answers from `existing`.

    No `git` runs. Returns what the fake saw: `roots`, each repo root the
    factory was built for, and `asked`, each branch it was asked about, in order.
    """
    seen: dict[str, list[Any]] = {"roots": [], "asked": []}

    def factory(root: Path):
        seen["roots"].append(root)

        def exists(branch: str) -> bool:
            seen["asked"].append(branch)
            return branch in existing

        return exists

    monkeypatch.setattr(orchestrate, "_local_branch_exists", factory)
    return seen
```

- [ ] **Step 2: Update the existing real-brd test (spec T8) to expect the stacked second milestone**

In `test_the_board_dry_run_previews_every_open_milestone_by_level_and_writes_nothing`, replace:

```python
    root = cli.resolve_repo_dir(project)
    expected = [
        {
            "level": index,
            "milestones": [
                _expected_board_milestone(
                    roots[card_id],
                    dag.task_stem(roots[card_id]),
                    root=root,
                    max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                )
            ],
        }
        for index, card_id in enumerate([first, second])
    ]
    assert data["levels"] == _as_json(expected)
    for level in data["levels"]:
        for entry in level["milestones"]:
            assert set(entry) == {"milestone_id", "title", "branch_prefix", "plan"}
```

with:

```python
    root = cli.resolve_repo_dir(project)
    first_prefix = dag.task_stem(roots[first])
    expected = [
        {
            "level": 0,
            "milestones": [
                _expected_board_milestone(
                    roots[first],
                    first_prefix,
                    root=root,
                    max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                )
            ],
        },
        {
            "level": 1,
            "milestones": [
                _expected_board_milestone(
                    roots[second],
                    dag.task_stem(roots[second]),
                    root=root,
                    max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                    base_branch=integration.integration_branch(first_prefix),
                )
            ],
        },
    ]
    assert data["levels"] == _as_json(expected)
    for level in data["levels"]:
        for entry in level["milestones"]:
            assert set(entry) == {
                "milestone_id",
                "title",
                "branch_prefix",
                "base_branch",
                "plan",
            }
```

Also, in the same test's docstring, replace `its own \`dry_run_payload\` nested as \`plan\`; nothing run, nothing written."""` with:

```python
    its own `dry_run_payload` nested as `plan`; the second milestone stacks on
    the first's `<prefix>-integrate`, reported as `base_branch` and planned
    against; nothing run, nothing written."""
```

- [ ] **Step 3: Update the given-prefix/done-milestone test (spec T7)**

Replace the body of `test_the_board_dry_run_derives_prefixes_from_a_given_prefix_and_drops_done_milestones` from `_forbid_board_dry_run_writes(monkeypatch)` through the end of the function:

```python
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(tmp_path, "--branch-prefix", "sprint9", "--max-concurrent", "2")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    assert data == _as_json(
        {
            "board": True,
            "max_concurrent": 2,
            "levels": [
                {
                    "level": index,
                    "milestones": [
                        _expected_board_milestone(
                            card,
                            f"sprint9-{dag.task_stem(card)}",
                            root=root,
                            max_concurrent=2,
                        )
                    ],
                }
                for index, card in enumerate([second, third])
            ],
        }
    )
    assert list(paths.data_dir().iterdir()) == []
```

with:

```python
    _forbid_board_dry_run_writes(monkeypatch)
    # The done milestone blocks `second`, so its integrate branch is looked up;
    # the fake answers "absent" without spawning git (unit tier).
    seen = _answer_local_branches(monkeypatch, set())

    result = _board_dry_run(tmp_path, "--branch-prefix", "sprint9", "--max-concurrent", "2")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    second_prefix = f"sprint9-{dag.task_stem(second)}"
    assert data == _as_json(
        {
            "board": True,
            "max_concurrent": 2,
            "levels": [
                {
                    "level": 0,
                    "milestones": [
                        _expected_board_milestone(
                            second, second_prefix, root=root, max_concurrent=2
                        )
                    ],
                },
                {
                    "level": 1,
                    "milestones": [
                        _expected_board_milestone(
                            third,
                            f"sprint9-{dag.task_stem(third)}",
                            root=root,
                            max_concurrent=2,
                            base_branch=integration.integration_branch(second_prefix),
                        )
                    ],
                },
            ],
        }
    )
    assert seen["asked"] == [integration.integration_branch(f"sprint9-{dag.task_stem(done)}")]
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 4: Update the shape test (spec T1)**

In `test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id`, replace the docstring lines:

```python
    `{level, milestones}`; each milestone is exactly `{milestone_id, title,
    branch_prefix, plan}`, and `plan` is that milestone's `dry_run_payload`
```

with:

```python
    `{level, milestones}`; each milestone is exactly `{milestone_id, title,
    branch_prefix, base_branch, plan}` (card 5bfe746d added `base_branch`, the
    branch the milestone starts from), and `plan` is that milestone's `dry_run_payload`
```

and in its loop replace:

```python
        assert set(entry) == {"milestone_id", "title", "branch_prefix", "plan"}
```

with:

```python
        assert set(entry) == {"milestone_id", "title", "branch_prefix", "base_branch", "plan"}
```

- [ ] **Step 5: Add the refusal cases (spec T6 and Review Focus 3)**

In the `@pytest.mark.parametrize("roots, error_type", ...)` above `test_a_board_dry_run_refusal_is_an_envelope_and_writes_nothing`, replace:

```python
            "ValueError",
        ),
    ],
    ids=["milestone-cycle", "non-uuid-milestone", "shared-derived-prefix"],
)
```

with:

```python
            "ValueError",
        ),
        (
            [
                _board_milestone(1),
                _board_milestone(2),
                _board_milestone(3, blocked_by=(_plan_id(1), _plan_id(2))),
            ],
            "MilestoneBlockersError",
        ),
        (
            [
                _board_milestone(1, status="done", title="Milestone twin"),
                _board_milestone(
                    2,
                    card_id="00000001-0000-4000-8000-000000000001",
                    title="Milestone twin",
                    blocked_by=(_plan_id(1),),
                ),
            ],
            "ValueError",
        ),
    ],
    ids=[
        "milestone-cycle",
        "non-uuid-milestone",
        "shared-derived-prefix",
        "two-open-blockers",
        "blocker-shares-a-prefix",
    ],
)
```

And extend that test's docstring: replace

```python
    cannot read, and two milestones deriving one prefix are each the same
    `HANDLED` refusal the real run gives: `ok: false`, exit 3."""
```

with:

```python
    cannot read, two milestones deriving one prefix, a milestone blocked by two
    open milestones (`MilestoneBlockersError`) and a done blocker root deriving
    an open milestone's prefix are each the same `HANDLED` refusal the real run
    gives: `ok: false`, exit 3. None of these cases asks git."""
```

(Neither new case reaches git: the two-blocker case has only open blockers, and the prefix case is refused by `board_prefixes` before `milestone_bases` runs.)

- [ ] **Step 6: Add the new unit tests (spec T2, T3, T4, T5, T10, Review Focus 4 and 5)**

Insert immediately after `test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id` (before the refusal parametrize):

```python
def test_the_board_dry_run_puts_independent_milestones_on_the_base_branch(
    tmp_path, monkeypatch
):
    """Card 5bfe746d, spec T2: no `blocked_by` between milestones, so each entry's
    `base_branch` is `--base-branch` and its plan is today's, and git is never asked."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    first = _board_milestone(1)
    second = _board_milestone(2)
    _serve_roots(monkeypatch, [first, second])
    _forbid_board_dry_run_writes(monkeypatch)
    seen = _answer_local_branches(monkeypatch, set())

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        card,
                        dag.task_stem(card),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                    )
                    for card in (first, second)
                ],
            }
        ]
    )
    assert seen["asked"] == []
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.parametrize("given", [None, "sprint9"], ids=["derived-prefix", "given-prefix"])
def test_the_board_dry_run_stacks_a_milestone_on_its_open_blockers_integrate_branch(
    tmp_path, monkeypatch, given
):
    """Card 5bfe746d, spec T3/T10: M2 blocked by the open M1 starts from M1's
    `<prefix>-integrate`. That is its `base_branch`, its plan is computed
    against it (story `root`, first subtask `base`, integrate tips), and with
    `--branch-prefix sprint9` it reads `sprint9-<stem(M1)>-integrate`. An open
    blocker never asks git; nothing is written."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    first = _board_milestone(1)
    second = _board_milestone(2, blocked_by=(first.id,))
    _serve_roots(monkeypatch, [first, second])
    _forbid_board_dry_run_writes(monkeypatch)
    seen = _answer_local_branches(monkeypatch, set())
    extra = () if given is None else ("--branch-prefix", given)

    def prefix(card: models.CardNode) -> str:
        stem = dag.task_stem(card)
        return stem if given is None else f"{given}-{stem}"

    result = _board_dry_run(tmp_path, *extra)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    stacked = integration.integration_branch(prefix(first))
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        first,
                        prefix(first),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                    )
                ],
            },
            {
                "level": 1,
                "milestones": [
                    _expected_board_milestone(
                        second,
                        prefix(second),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                        base_branch=stacked,
                    )
                ],
            },
        ]
    )
    first_entry = data["levels"][0]["milestones"][0]
    second_entry = data["levels"][1]["milestones"][0]
    assert first_entry["base_branch"] == "main"
    assert second_entry["base_branch"] == stacked
    story_row = second_entry["plan"]["levels"][0]["stories"][0]
    assert story_row["root"] == stacked
    assert story_row["subtasks"][0]["base"] == stacked
    assert seen["asked"] == []
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.parametrize("exists", [True, False], ids=["branch-kept", "branch-gone"])
def test_the_board_dry_run_stacks_on_a_done_blockers_branch_only_while_it_exists_locally(
    tmp_path, monkeypatch, exists
):
    """Card 5bfe746d, spec T4/T10: M1 is `done` (so not previewed) and blocks M2.
    M2 stacks on `<stem(M1)>-integrate` only when that local branch exists;
    otherwise it starts from `--base-branch`, today's behaviour. The git seam is
    built for the resolved repo dir and asked exactly that one branch."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    done = _board_milestone(1, status="done")
    second = _board_milestone(2, blocked_by=(done.id,))
    _serve_roots(monkeypatch, [done, second])
    _forbid_board_dry_run_writes(monkeypatch)
    integrate = integration.integration_branch(dag.task_stem(done))
    seen = _answer_local_branches(monkeypatch, {integrate} if exists else set())

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        second,
                        dag.task_stem(second),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                        base_branch=integrate if exists else "main",
                    )
                ],
            }
        ]
    )
    assert seen == {"roots": [root], "asked": [integrate]}
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.parametrize("status", ["merged", "canceled", "archived"])
def test_the_board_dry_run_never_stacks_on_or_asks_git_about_a_landed_blocker(
    tmp_path, monkeypatch, status
):
    """Card 5bfe746d, spec T5: a landed blocker is ignored even when its integrate
    branch would answer "exists"; git is never asked and M2 starts from `main`."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    landed = _board_milestone(1, status=status)
    second = _board_milestone(2, blocked_by=(landed.id,))
    _serve_roots(monkeypatch, [landed, second])
    _forbid_board_dry_run_writes(monkeypatch)
    seen = _answer_local_branches(
        monkeypatch, {integration.integration_branch(dag.task_stem(landed))}
    )

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    root = cli.resolve_repo_dir(tmp_path)
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        second,
                        dag.task_stem(second),
                        root=root,
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                    )
                ],
            }
        ]
    )
    assert seen["asked"] == []
    assert list(paths.data_dir().iterdir()) == []


def test_the_board_dry_run_stacks_on_the_one_open_blocker_among_landed_and_unknown_ones(
    tmp_path, monkeypatch
):
    """Review focus: M3 is blocked by the open M1, the merged M2 and an id that is
    no milestone on the board. Only M1 is a stack candidate, so M3 starts from
    M1's integrate branch: no `MilestoneBlockersError`, and git is never asked."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    first = _board_milestone(1)
    merged = _board_milestone(2, status="merged")
    third = _board_milestone(3, blocked_by=(first.id, merged.id, _plan_id(99)))
    _serve_roots(monkeypatch, [first, merged, third])
    _forbid_board_dry_run_writes(monkeypatch)
    seen = _answer_local_branches(monkeypatch, set())

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    entries = {
        entry["milestone_id"]: entry
        for level in data["levels"]
        for entry in level["milestones"]
    }
    assert set(entries) == {first.id, third.id}
    assert entries[first.id]["base_branch"] == "main"
    assert entries[third.id]["base_branch"] == integration.integration_branch(
        dag.task_stem(first)
    )
    assert seen["asked"] == []
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 7: Run the board dry-run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "board_dry_run" -v`
Expected: FAIL. The new/updated unit tests fail on the missing `"base_branch"` key (e.g. `assert ... == ...` diff showing `'base_branch'` only on the right, or `KeyError: 'base_branch'`); the `two-open-blockers` and `blocker-shares-a-prefix` refusal cases fail in `_refusal` with `assert 0 == 3` (the dry run currently succeeds). `test_an_empty_board_dry_runs_to_no_levels_and_exits_zero`, `milestone-cycle`, `non-uuid-milestone` and `shared-derived-prefix` still PASS.

Also run the brd-tier test: `uv run pytest -m brd tests/test_cli.py::test_the_board_dry_run_previews_every_open_milestone_by_level_and_writes_nothing -v`
Expected: FAIL (missing `base_branch`; second milestone planned on `main`).

- [ ] **Step 8: Implement in `cli.dry_run_board`**

In `src/agent_manager/cli.py`, replace the whole of `dry_run_board` (L1315-1362) with:

```python
def dry_run_board(
    *,
    repo_dir: Path,
    branch_prefix: str | None,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
    """`--dry-run --board`: every open milestone, by level, each on its own base.

    Read-only by construction, like `dry_run_milestone`, and composed the way
    `run_board` composes its refusals. `board.roots()` is read once (each root
    already nests its whole tree), leveled by `dag.board_levels` (a done
    milestone drops out, a cycle is `DependencyCycleError`). Each milestone's
    prefix, and each non-open blocker root's, comes from
    `orchestrate.board_prefixes(..., roots=)` over `board_prefix_of`, the very
    check `run_board` makes. Each milestone's base comes from
    `orchestrate.milestone_bases` with `orchestrate._local_branch_exists`, read
    at call time: its one open blocker's `<prefix>-integrate`, or its one
    unlanded blocker's when that branch exists locally, else `base_branch`;
    two such blockers is `MilestoneBlockersError`. The base is the entry's
    `base_branch`, and its `plan` (its own `dry_run_payload`) is computed
    against it. The only I/O past the board read is that read-only
    `git rev-parse`, asked only about an unlanded, non-open blocker. No
    `Store` is opened, no claim is checked, no runner is built, and
    `orchestrate.run_board` is never called. Every refusal is a type already
    in `HANDLED`; a `GitError` from a broken repository propagates, as it
    does from `run_board`.
    """
    root = resolve_repo_dir(repo_dir)
    all_roots = board.roots(repo_dir=root)
    levels = dag.board_levels(all_roots)
    milestones = [card for level in levels for card in level]
    prefixes = orchestrate.board_prefixes(
        milestones, board_prefix_of(branch_prefix), roots=all_roots
    )
    bases = orchestrate.milestone_bases(
        all_roots, prefixes, orchestrate._local_branch_exists(root), base_branch
    )
    return {
        "board": True,
        "max_concurrent": max_concurrent,
        "levels": [
            {
                "level": index,
                "milestones": [
                    {
                        "milestone_id": card.id,
                        "title": card.title,
                        "branch_prefix": prefixes[card.id],
                        "base_branch": bases[card.id],
                        "plan": dry_run_payload(
                            census.flatten_milestone(card).stories,
                            repo_dir=root,
                            branch_prefix=prefixes[card.id],
                            base_branch=bases[card.id],
                            max_concurrent=max_concurrent,
                        ),
                    }
                    for card in level
                ],
            }
            for index, level in enumerate(levels)
        ],
    }
```

- [ ] **Step 9: Run the board dry-run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "board_dry_run" -v`
Expected: PASS (all, including the empty-board test).

Run: `uv run pytest -m brd tests/test_cli.py::test_the_board_dry_run_previews_every_open_milestone_by_level_and_writes_nothing -v`
Expected: PASS.

- [ ] **Step 10: Run the default suite**

Run: `uv run pytest`
Expected: PASS, no new failures.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: board dry run reports and plans each milestone on its own base_branch (card 5bfe746d)"
```

---

### Task 2: Pin the real git seam and document `base_branch`

**Files:**
- Test: `tests/test_cli.py` (append right after `test_a_board_dry_run_refusal_is_an_envelope_and_writes_nothing`, before `_board_payload`)
- Modify: `README.md:181`

**Interfaces:**
- Consumes: `cli.dry_run_board` entries with `"base_branch"` (Task 1); `_expected_board_milestone(card, prefix, *, root, max_concurrent, base_branch="main")` (Task 1); existing `_board_milestone`, `_serve_roots`, `_forbid_board_dry_run_writes`, `_board_dry_run`, `_as_json`, `_git`; `orchestrate.worktree.GitError` (the `agent_manager.steps.worktree` module as imported by `orchestrate`).
- Produces: nothing new for later tasks.

These are pin tests for behaviour Task 1 already implements through the landed `_local_branch_exists`; they are expected to pass on first run. To prove they bite, Step 2 temporarily breaks the seam.

- [ ] **Step 1: Write the real-git tests**

Insert after `test_a_board_dry_run_refusal_is_an_envelope_and_writes_nothing`:

```python
def _bare_main_repo(path: Path) -> Path:
    """A real git repo at `path` on `main` with one empty commit, no brd board."""
    path.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True, text=True
    )
    _git(path, "config", "user.email", "tests@example.com")
    _git(path, "config", "user.name", "agent-manager tests")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "commit", "--allow-empty", "-m", "base")
    return path


@pytest.mark.git
@pytest.mark.parametrize(
    "ref_kind, stacks", [("branch", True), ("tag", False)], ids=["local-branch", "tag-only"]
)
def test_the_board_dry_run_asks_real_git_for_a_done_blockers_local_integrate_branch(
    tmp_path, monkeypatch, ref_kind, stacks
):
    """Card 5bfe746d, spec T9: the unreplaced `_local_branch_exists` against a real
    repo. M1 is `done` and blocks M2. A local branch `<stem(M1)>-integrate`
    stacks M2 on it; a tag of the same name, with no branch, does not, and M2
    stays on `main`. The repo's branches, tags and work tree are untouched."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    repo = _bare_main_repo(tmp_path / "repo")
    done = _board_milestone(1, status="done")
    second = _board_milestone(2, blocked_by=(done.id,))
    integrate = integration.integration_branch(dag.task_stem(done))
    _git(repo, ref_kind, integrate)
    _serve_roots(monkeypatch, [done, second])
    _forbid_board_dry_run_writes(monkeypatch)
    refs_before = _git(repo, "show-ref")
    porcelain_before = _git(repo, "status", "--porcelain")

    result = _board_dry_run(repo)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["levels"] == _as_json(
        [
            {
                "level": 0,
                "milestones": [
                    _expected_board_milestone(
                        second,
                        dag.task_stem(second),
                        root=cli.resolve_repo_dir(repo),
                        max_concurrent=cli.DEFAULT_MAX_CONCURRENT,
                        base_branch=integrate if stacks else "main",
                    )
                ],
            }
        ]
    )
    assert _git(repo, "show-ref") == refs_before
    assert _git(repo, "status", "--porcelain") == porcelain_before
    assert list(paths.data_dir().iterdir()) == []


@pytest.mark.git
def test_the_board_dry_run_lets_a_git_error_from_a_non_repository_propagate(
    tmp_path, monkeypatch
):
    """Card 5bfe746d, spec 2.3 / review focus: `--repo-dir` is no git repository and
    M2's blocker is `done`, so its integrate branch must be looked up. git exits
    128, which is not an answer: the `GitError` propagates (no envelope), as it
    does from `run_board`, rather than silently previewing M2 on `main`."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # Stop git's repository discovery at tmp_path, so an enclosing repo can't answer.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    done = _board_milestone(1, status="done")
    second = _board_milestone(2, blocked_by=(done.id,))
    _serve_roots(monkeypatch, [done, second])
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(not_a_repo)

    assert result.exit_code != 0
    assert isinstance(result.exception, orchestrate.worktree.GitError)
    assert result.exception.exit_code == 128
    assert '"ok"' not in result.stdout
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 2: Run them, then prove they bite**

Run: `uv run pytest tests/test_cli.py -k "asks_real_git or git_error_from_a_non_repository" -v`
Expected: PASS (3 tests: `local-branch`, `tag-only`, and the non-repository one).

Then temporarily change the `orchestrate._local_branch_exists(root)` argument in `cli.dry_run_board` to `lambda branch: False`, re-run the same command, and confirm `local-branch` and the non-repository test FAIL (`tag-only` still passes). Revert the change (`git diff src/agent_manager/cli.py` must show nothing afterwards).

- [ ] **Step 3: Update the README paragraph**

In `README.md`, replace the line (L181) that starts with `` `--dry-run` with `--board` is read-only.``:

```markdown
`--dry-run` with `--board` is read-only. It opens no store, checks no claim, writes nothing, and exits 0. It still refuses a blocker cycle, a bad prefix and a board that cannot be read, the same way as above. Its `data` is `{"board": true, "max_concurrent", "levels"}`, with no `ok` and no `run_id`. `levels` is a list of `{"level", "milestones"}`, and each milestone is `{"milestone_id", "title", "branch_prefix", "plan"}`. `branch_prefix` is the prefix that milestone will run under. `plan` is exactly that milestone's own `--milestone --dry-run` data (`max_concurrent`, `levels`, `already_done`, `integrate`). Read each plan as described in [Preview with `--dry-run`](#preview-with---dry-run), `base` column included.
```

with:

```markdown
`--dry-run` with `--board` is read-only. It opens no store, checks no claim, writes nothing, and exits 0. It still refuses a blocker cycle, a bad prefix and a board that cannot be read, the same way as above, and it refuses a milestone with two or more open or unlanded blocker milestones the same way as the real run (`MilestoneBlockersError`, exit 3). Its `data` is `{"board": true, "max_concurrent", "levels"}`, with no `ok` and no `run_id`. `levels` is a list of `{"level", "milestones"}`, and each milestone is `{"milestone_id", "title", "branch_prefix", "base_branch", "plan"}`. `branch_prefix` is the prefix that milestone will run under. `base_branch` is the branch the milestone would start from: the `--base-branch`, or its one blocker milestone's `<prefix>-integrate` when it stacks on it. `plan` is exactly that milestone's own `--milestone --dry-run` data (`max_concurrent`, `levels`, `already_done`, `integrate`), computed against `base_branch`, so the `base` column shows the stacking. Read each plan as described in [Preview with `--dry-run`](#preview-with---dry-run), `base` column included.
```

Only this paragraph changes.

- [ ] **Step 4: Run the default suite**

Run: `uv run pytest`
Expected: PASS, no new failures.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py README.md
git commit -m "test: pin the board dry run's real git seam and document base_branch (card 5bfe746d)"
```

---

## Self-review (against the spec)

- §2.1 steps 1-7 → Task 1 Step 8 (roots read once, `board_prefixes(roots=)`, `milestone_bases` with module-level `_local_branch_exists`, entry shape, plan on `bases[id]`, `data` keys unchanged — T1 shape test).
- §2.2 read-only → every new test runs under `_forbid_board_dry_run_writes` and asserts `paths.data_dir()` empty; open/landed blockers never ask git (T3, T5, review-focus 4); no-edge board identical to today plus `base_branch == "main"` (T2).
- §2.3 error table → `two-open-blockers` (MilestoneBlockersError), existing cycle / non-uuid / shared-prefix cases, `blocker-shares-a-prefix` (new ValueError), non-repository `GitError` propagation (Task 2).
- §3 README → Task 2 Step 3.
- §4 T1-T10 → T1 Task 1 Step 4; T2/T3/T4/T5/T10 Step 6; T6 Step 5; T7 Step 3; T8 Step 2; T9 Task 2 Step 1. Empty-board test left unchanged.
- §5 out of scope respected: no change to `run_board`, `milestone_bases`, `board_prefixes`, `_local_branch_exists`, `dry_run_milestone`, schemas.
- Names consistent: `_expected_board_milestone(..., base_branch=)`, `_answer_local_branches(monkeypatch, existing) -> {"roots", "asked"}`, `integration.integration_branch`, `orchestrate.worktree.GitError`.
<!-- task-pipeline: validated -->
