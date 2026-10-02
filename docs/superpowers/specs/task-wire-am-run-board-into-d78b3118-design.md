# Wire `am run --board` into the CLI (card d78b3118) — design

Parent story 488d9bf4 ("am run --board drives every open milestone as one DAG"). Narrows `docs/superpowers/specs/2026-10-01-run-board-design.md` sections 3.1 (CLI surface) and 3.2 (branch prefix derivation) to the CLI layer. Source of truth for everything not restated here is that spec.

## Scope

In scope, all in `src/agent_manager/cli.py` plus tests in `tests/test_cli.py`:

- A boolean `--board` option on `am run` (the `run` command, `cli.py:1022`).
- `--branch-prefix` becomes optional at the Typer level (`str | None = typer.Option(None, "--branch-prefix", ...)`), with the "required unless --board" rule enforced in `_check_run_targets`.
- A small prefix-derivation helper (e.g. `board_prefix_of(branch_prefix: str | None) -> Callable[[CardNode], str]`) implementing 3.2.
- A read-only `dry_run_board(...)` preview function, sibling of `dry_run_milestone` (`cli.py:913`).
- A board branch in `run`'s dispatch, calling `orchestrate.run_board` (read as `orchestrate.run_board` so tests can patch it there, like `run_milestone`).
- A board branch in the escalation exit-code logic (`cli.py:1131-1136`).
- Updated `--help` text for `--board`, `--branch-prefix`, `--dry-run`, `--max-concurrent`, and one `RUN_EXAMPLES` line for `--board`.

Out of scope (owned by done siblings; consume, do not touch or reimplement): `dag.board_levels` (34bc3c16, `dag.py:207`); `orchestrate.run_board`, `board_prefixes`, `board_claims`, `_run_board_async` and their dispatch, claim and async logic (baef4f94, `orchestrate.py:1847-1997`). No new exception type and no change to `HANDLED`.

## Observable behavior

**Target validation** (`_check_run_targets`, gains `board: bool` and `branch_prefix: str | None` keyword params; every refusal is `typer.BadParameter`, so exit 2 before anything is read, worded and `param_hint`-ed like the existing `--card`/`--milestone` conflict at `cli.py:981-985`):

- `--board` with `--card` or `--milestone`: refused, e.g. `"give --board or --card, not both"` / `"give --board or --milestone, not both"`, `param_hint` naming both options (`"'--board' / '--card'"` etc.). If all three are given, either message is acceptable. The existing `--card`+`--milestone` refusal is unchanged.
- None of the three given: refused as `"one of --card, --milestone or --board is required"`, `param_hint="'--card' / '--milestone' / '--board'"`.
- `--branch-prefix` omitted with `--card` or `--milestone`: refused, `"--branch-prefix is required with --card or --milestone"`, `param_hint="'--branch-prefix'"` (preserves today's exit 2 for a missing required option). Omitted with `--board`: allowed.
- `--branch-prefix` given but blank with `--board`: refused (`param_hint="'--branch-prefix'"`), mirroring the blank-`--milestone` refusal — otherwise it would produce a `-<stem>` prefix. (Judgement call, smallest guard consistent with existing style.)
- `--dry-run` remains refused only with `--card`; allowed with `--board`.
- `--max-concurrent`: same `< 1` refusal; the "not with --card" refusal message becomes `"--max-concurrent applies only to --milestone or --board"`. Default stays `DEFAULT_MAX_CONCURRENT` (`cli.py:846`); in board mode it is passed through as `run_board`'s `max_concurrent`, meaning board-wide stories at once.

**Prefix derivation (3.2)**: with `--branch-prefix` omitted, a milestone's prefix is `dag.task_stem(milestone_card)`; given, it is `f"{branch_prefix}-{dag.task_stem(milestone_card)}"`, never the literal value. Blank/duplicate validation is left to `orchestrate.board_prefixes` (pure; called, not copied).

**Real run** (`--board` without `--dry-run`): calls `orchestrate.run_board(repo_dir=repo_dir, base_branch=base_branch, branch_prefix_of=board_prefix_of(branch_prefix), commands=list(verify), allow_no_verification=allow_no_verification, max_concurrent=lanes)` with no `runner_factory`/`driver` (production defaults), and prints `render(ok_envelope(payload), pretty=pretty)` with the payload unchanged. Any `HANDLED` exception (run_board's `ValueError`, `DependencyCycleError` as a `ValueError`, `BoardError`, `ClaimedError` as whatever `HANDLED` type it already is, `LeaseLostError`, ...) becomes the existing `ok: false` envelope + `EXIT_ERROR` (3).

**Exit code for a board payload**: exit `EXIT_ESCALATED` iff some entry in `payload.get("milestones", [])` has `status == "escalated"` (strict equality) — `.get` with an empty-list default, not a strict index, because a dry-run board payload (below) has no top-level `"milestones"` key at all (its `"levels"` nests milestone preview objects with no `status` field), and a strict `payload["milestones"]` would raise `KeyError` and break the "never exits `EXIT_ESCALATED`" dry-run guarantee below. This mirrors why the milestone branch already reads `.get("escalated")` rather than indexing. This is the board-wide form of the milestone rule (`escalated is True` → escalated; a pause or cancel is not an escalation and exits 0). `stopped`, `cancelled` and `blocked` entries alone exit 0; a `blocked` entry always has a non-`done` blocker that carries its own status, so it never needs to count separately. `payload["ok"]` (present only on a real run) is printed but not used for the exit code. The existing card (`status`) and milestone (`.get("escalated")`) branches are unchanged; the decision branches on `board` first.

**Dry run** (`--dry-run --board`): `dry_run_board(*, repo_dir, branch_prefix, base_branch, max_concurrent)` resolves the repo dir, reads `board.roots()` once, levels it with `dag.board_levels`, derives prefixes via `orchestrate.board_prefixes(milestones, board_prefix_of(branch_prefix))`, and for each milestone builds the existing `dry_run_payload(census.flatten_milestone(card).stories, repo_dir=root, branch_prefix=<its prefix>, base_branch=..., max_concurrent=...)`. Payload, keys in this order:

```json
{
  "board": true,
  "max_concurrent": 4,
  "levels": [
    {"level": 0, "milestones": [
      {"milestone_id": "<id>", "title": "<title>", "branch_prefix": "<derived>", "plan": {"max_concurrent": 4, "levels": [], "already_done": [], "integrate": {}}}
    ]}
  ]
}
```

Read-only by construction, like `dry_run_milestone`: no `Store` opened, no run directory, no claims checked, no runner, nothing fetched/branched/written to git or the board, `orchestrate.run_board` not called. An empty board yields `"levels": []`. Never exits `EXIT_ESCALATED`. A cycle or a blank/shared derived prefix is the same `HANDLED` refusal the real run would give.

## Tests

Test-placement rule in effect: main design spec §14 (`2026-09-23-agent-manager-design.md`, "## 14. Testing") — CLI/orchestration tests driven with fakes/patches belong in the default, unmarked `uv run pytest` suite; only real-harness tests are `e2e`-marked. The proposed six-tier addendum (`2026-10-02-test-tier-design.md`) is explicitly out of scope for milestone 14 and does not apply. All tests below therefore go in `tests/test_cli.py`, **unmarked default suite**, patching `orchestrate.run_board` with a `_patch_run_board` helper modeled on `_patch_run_milestone` (`tests/test_cli.py:3160`), and using `_Forbidden` (`tests/test_cli.py:2628`) to prove nothing is called on refusals and in dry-run.

1. `--board` with `--card` → exit 2, message names both options, `run_board`/`run_card` forbidden. (default suite, test_cli.py)
2. `--board` with `--milestone` → exit 2, same shape. (default suite)
3. No target at all → exit 2 with the three-option message. (default suite)
4. `--milestone` (and `--card`) without `--branch-prefix` → exit 2, `'--branch-prefix'` hint, nothing dispatched. (default suite)
5. `--board` without `--branch-prefix` is accepted; the patched `run_board` receives a `branch_prefix_of` that maps a card to `dag.task_stem(card)`. (default suite)
6. `--board --branch-prefix sprint9` → `branch_prefix_of(card) == f"sprint9-{dag.task_stem(card)}"`, never `"sprint9"`. (default suite)
7. Blank `--branch-prefix` with `--board` → exit 2. (default suite)
8. Forwarding: `--base-branch`, repeated `--verify`, `--allow-no-verification`, and `--max-concurrent` (given and defaulted to `DEFAULT_MAX_CONCURRENT`) reach `run_board` unchanged; no `runner_factory`/`driver` passed. (default suite)
9. `--max-concurrent 0 --board` → exit 2; `--max-concurrent` with `--card` still exit 2 with the updated message. (default suite)
10. Envelope: patched payload is printed as `{"ok": true, "data": <payload>}`; `--pretty` indents. (default suite)
11. Exit codes: a payload with an `escalated` entry → `EXIT_ESCALATED`; only `done` → 0; `stopped`/`cancelled`/`blocked` without `escalated` → 0. (default suite)
12. A `HANDLED` error raised from patched `run_board` (e.g. `ValueError`) → `ok: false` envelope, exit 3. (default suite)
13. `--dry-run --board` against a temporary `brd` board with two milestones (one `blocked_by` the other): two levels, each milestone carrying its derived `branch_prefix` and a nested `plan` equal to its own `dry_run_payload`; `run_board`, `Store.open` and `run_milestone` forbidden; no run directory created. (default suite)
14. Unit test of `board_prefix_of` for both cases (omitted / given). (default suite)

Existing `run`/`_check_run_targets` tests must keep passing; any that assert the old "one of --card or --milestone is required" or "applies only to --milestone" wording are updated to the new wording.

## Verification

`uv run pytest` (no separate lint or typecheck command).
