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

---

# Wire `am run --board` into the CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `am run` a `--board` mode that previews (`--dry-run`) or drives every open milestone through the already-built `orchestrate.run_board`, with per-milestone branch prefixes derived per spec 3.2.

**Architecture:** All production changes live in `src/agent_manager/cli.py`: a pure `board_prefix_of` factory, extended `_check_run_targets` validation, a read-only `dry_run_board` built from `board.roots` + `dag.board_levels` + `orchestrate.board_prefixes` + the existing `dry_run_payload`, and two new branches in the `run` command (dispatch and exit code). `orchestrate.run_board` is called, never reimplemented, and read as `orchestrate.run_board` so tests patch it there.

**Tech Stack:** Python, Typer (`typer.BadParameter`, `CliRunner`), Pydantic `models.CardNode`, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-wire-am-run-board-into-d78b3118/docs/superpowers/specs/task-wire-am-run-board-into-d78b3118-design.md` (prepended verbatim above; parent spec `docs/superpowers/specs/2026-10-01-run-board-design.md` sections 3.1, 3.2).

Note on inputs: the spec author's summary handed to the plan writer was truncated at 2000 characters; the plan was written from the full spec read from disk, so nothing from the truncated tail was guessed.

## Global Constraints

- Branch `m14/task-wire-am-run-board-into-d78b3118`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-wire-am-run-board-into-d78b3118`; all paths below are relative to that worktree.
- Only `src/agent_manager/cli.py` and `tests/test_cli.py` change. Do not touch `dag.py`, `orchestrate.py`, `HANDLED`, or add any exception type.
- Every refusal in `_check_run_targets` is `typer.BadParameter` (exit 2), with `param_hint` in the `"'--a' / '--b'"` style of `cli.py:981-985`.
- Exact refusal wording: `"give --board or --card, not both"`, `"give --board or --milestone, not both"`, `"one of --card, --milestone or --board is required"`, `"--branch-prefix is required with --card or --milestone"`, `"--max-concurrent applies only to --milestone or --board"`, blank board prefix: `"--branch-prefix with --board needs a non-blank prefix, not a blank string"`.
- Prefix derivation: omitted → `dag.task_stem(card)`; given → `f"{branch_prefix}-{dag.task_stem(card)}"`.
- `--max-concurrent` default stays `DEFAULT_MAX_CONCURRENT` (4); in board mode it is passed as `run_board(max_concurrent=...)`.
- Board exit code: `EXIT_ESCALATED` iff any `payload.get("milestones", [])` entry has `status == "escalated"`.
- Dry-run board payload key order: `board`, `max_concurrent`, `levels`; each level `{"level", "milestones"}`; each milestone `{"milestone_id", "title", "branch_prefix", "plan"}`.
- Tests: all in `tests/test_cli.py`, unmarked default suite (main spec §14). No `e2e` marker. Git/brd-backed tests use the existing `@requires_git` / `@requires_brd` skip markers, like their neighbours.
- Verification: `uv run pytest` (no lint/typecheck command exists).
- Python parameter for `--board` inside `run` is named `whole_board`, because `run`'s module scope imports the `board` module and a `board` parameter would shadow it. `_check_run_targets` keeps the spec's `board` keyword (it never uses the module).

## Review Focus

- A milestone root whose id is not a UUID (`dag.task_stem` → `dag.short_id` raises `ValueError`) under `--dry-run --board` → `ok: false` envelope, exit 3, not a traceback. Test added in Task 3.
- Two milestones that derive the same prefix (same title and same first eight hex of id) under `--dry-run --board` → `board_prefixes`'s `ValueError` envelope, exit 3. Test added in Task 3.
- A blocker cycle among milestones under `--dry-run --board` → `DependencyCycleError` envelope, exit 3, nothing written. Test added in Task 3.
- A `done` milestone is omitted from the dry-run preview and the milestone it blocked lands in level 0; an empty board previews `"levels": []` and exits 0. Tests added in Task 3.
- An error outside `HANDLED` (e.g. `RuntimeError`) raised by `run_board` crashes loudly with its traceback, never an envelope; an empty real-run payload (`milestones: []`) exits 0. Tests added in Task 4.

---

## File Structure

- Modify: `src/agent_manager/cli.py`
  - after `dry_run_milestone` (ends `cli.py:937`): new `board_prefix_of` (Task 1) and `dry_run_board` (Task 3).
  - `_check_run_targets` (`cli.py:962-1010`): new keywords and refusals (Task 2).
  - `RUN_EXAMPLES` (`cli.py:1013-1019`): one `--board` line (Task 4).
  - `run` (`cli.py:1022-1136`): `--board` option, optional `--branch-prefix` (Task 2); dry-run dispatch + board exit branch (Task 3); real-run dispatch + help text (Task 4).
- Modify: `tests/test_cli.py`
  - add `import typer` to the imports (Task 2).
  - new tests inserted after `test_an_unhandled_error_from_a_milestone_run_crashes_loudly` (ends `tests/test_cli.py:3313`) and before the `@pytest.mark.parametrize("first", ...)` decorator of `test_cli_and_orchestrate_import_cleanly_in_either_order` (`tests/test_cli.py:3316`). Each task appends its block after the previous task's block, still before that decorator.

---

### Task 1: `board_prefix_of` (spec 3.2 prefix derivation)

**Files:**
- Modify: `src/agent_manager/cli.py` (insert after `dry_run_milestone`, i.e. after line 937, before `HANDLED` at line 940)
- Test: `tests/test_cli.py` (insert after line 3313)

**Interfaces:**
- Consumes: `dag.task_stem(card: object) -> str` (`dag.py:77`), `models.CardNode`.
- Produces: `cli.board_prefix_of(branch_prefix: str | None) -> Callable[[models.CardNode], str]`; test constant `BOARD_CARD: models.CardNode` (used by Task 4).

- [ ] **Step 1: Write the failing test**

Insert into `tests/test_cli.py` immediately after `test_an_unhandled_error_from_a_milestone_run_crashes_loudly` (after line 3313):

```python
BOARD_CARD = models.CardNode(id=SOME_CARD, title="Milestone 14: run the board", status="todo")
"""A milestone root for the prefix tests. Its id is a real UUID, so `dag.task_stem` accepts it."""


def test_board_prefix_of_without_a_prefix_is_the_milestones_own_stem():
    """Spec 3.2: with --branch-prefix omitted, each milestone's prefix is its card stem."""
    prefix_of = cli.board_prefix_of(None)

    assert prefix_of(BOARD_CARD) == dag.task_stem(BOARD_CARD)
    assert prefix_of(BOARD_CARD).endswith("cbe34d00")


def test_board_prefix_of_with_a_prefix_joins_it_to_the_stem_and_never_reuses_it_verbatim():
    """Spec 3.2: a given prefix is `<prefix>-<stem>`, so two milestones never share it."""
    prefix_of = cli.board_prefix_of("sprint9")

    assert prefix_of(BOARD_CARD) == f"sprint9-{dag.task_stem(BOARD_CARD)}"
    assert prefix_of(BOARD_CARD) != "sprint9"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k board_prefix_of -v`
Expected: 2 FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'board_prefix_of'`.

- [ ] **Step 3: Write minimal implementation**

Insert into `src/agent_manager/cli.py` after `dry_run_milestone` (after line 937):

```python
def board_prefix_of(branch_prefix: str | None) -> Callable[[models.CardNode], str]:
    """Run-board spec 3.2: how one milestone's branch prefix is derived under `--board`.

    With `--branch-prefix` omitted the prefix is the milestone card's own
    `dag.task_stem`; given, it is `<branch_prefix>-<stem>`, never the given
    value verbatim, so two milestones can never share it. Checking the result
    (blank, shared) is `orchestrate.board_prefixes`'s job, not this one's.
    """

    def prefix_of(card: models.CardNode) -> str:
        stem = dag.task_stem(card)
        return stem if branch_prefix is None else f"{branch_prefix}-{stem}"

    return prefix_of
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -k board_prefix_of -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Add board_prefix_of: derive each milestone's prefix under --board"
```

---

### Task 2: `--board` flag and target validation

**Files:**
- Modify: `src/agent_manager/cli.py:962-1010` (`_check_run_targets`), `cli.py:1022-1089` (`run` signature and its `_check_run_targets` call)
- Test: `tests/test_cli.py` (add `import typer` after line 31 `from typer.testing import CliRunner`; tests appended after Task 1's block)

**Interfaces:**
- Consumes: `_Forbidden` (`tests/test_cli.py:2628`), `_forbid_writes` (`tests/test_cli.py:2648`), `SOME_CARD` (`tests/test_cli.py:3057`).
- Produces: `cli._check_run_targets(*, card: str | None, milestone: str | None, dry_run: bool, max_concurrent: int | None = None, board: bool = False, branch_prefix: str | None = None) -> None`; `run`'s `whole_board: bool` parameter for `--board`; `branch_prefix: str | None`; test helper `_forbid_board_paths(monkeypatch) -> None` (extended in Task 3, used in Task 4).

Note: after this task, `--board` passes validation but is not dispatched yet (it would fall to `run_card`); Task 3 and Task 4 add the dispatch. No test in this task reaches that path.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add `import typer` on its own line right after `from typer.testing import CliRunner` (line 31).

Append after Task 1's block:

```python
BLANK_BOARD_PREFIX = "--branch-prefix with --board needs a non-blank prefix, not a blank string"
PREFIX_REQUIRED = "--branch-prefix is required with --card or --milestone"


@pytest.mark.parametrize(
    "kwargs, message, hint",
    [
        (
            {"card": SOME_CARD, "milestone": None, "board": True, "branch_prefix": "m2"},
            "give --board or --card, not both",
            "'--board' / '--card'",
        ),
        (
            {"card": None, "milestone": "2", "board": True, "branch_prefix": "m2"},
            "give --board or --milestone, not both",
            "'--board' / '--milestone'",
        ),
        (
            {"card": None, "milestone": None, "board": False, "branch_prefix": "m2"},
            "one of --card, --milestone or --board is required",
            "'--card' / '--milestone' / '--board'",
        ),
        (
            {"card": None, "milestone": "2", "board": False, "branch_prefix": None},
            PREFIX_REQUIRED,
            "'--branch-prefix'",
        ),
        (
            {"card": SOME_CARD, "milestone": None, "board": False, "branch_prefix": None},
            PREFIX_REQUIRED,
            "'--branch-prefix'",
        ),
        (
            {"card": None, "milestone": None, "board": True, "branch_prefix": ""},
            BLANK_BOARD_PREFIX,
            "'--branch-prefix'",
        ),
        (
            {"card": None, "milestone": None, "board": True, "branch_prefix": "   "},
            BLANK_BOARD_PREFIX,
            "'--branch-prefix'",
        ),
        (
            {
                "card": SOME_CARD,
                "milestone": None,
                "board": False,
                "branch_prefix": "m2",
                "max_concurrent": 2,
            },
            "--max-concurrent applies only to --milestone or --board",
            "'--max-concurrent'",
        ),
        (
            {
                "card": None,
                "milestone": None,
                "board": True,
                "branch_prefix": None,
                "max_concurrent": 0,
            },
            "--max-concurrent must be at least 1, got 0",
            "'--max-concurrent'",
        ),
    ],
)
def test_check_run_targets_words_each_board_refusal_like_the_card_milestone_conflict(
    kwargs, message, hint
):
    """Exact wording and hint, checked on the function itself so Typer's error
    box cannot wrap the text out from under the assertion."""
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(dry_run=False, **kwargs)

    assert caught.value.message == message
    assert caught.value.param_hint == hint


@pytest.mark.parametrize(
    "kwargs",
    [
        {"board": True, "branch_prefix": None, "dry_run": False, "max_concurrent": None},
        {"board": True, "branch_prefix": "sprint9", "dry_run": False, "max_concurrent": 2},
        {"board": True, "branch_prefix": None, "dry_run": True, "max_concurrent": 1},
    ],
)
def test_check_run_targets_accepts_board_with_or_without_a_prefix_and_with_dry_run(kwargs):
    """--branch-prefix is optional only in board mode; --dry-run and
    --max-concurrent both apply to --board."""
    assert cli._check_run_targets(card=None, milestone=None, **kwargs) is None


def _forbid_board_paths(monkeypatch) -> None:
    """Every run path forbidden: a refusal must start nothing at all."""
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))
    monkeypatch.setattr(orchestrate, "run_board", _Forbidden("run_board"))


@pytest.mark.parametrize(
    "targets, word",
    [
        (["--board", "--card", SOME_CARD, "--branch-prefix", "m2"], "both"),
        (["--board", "--milestone", "2", "--branch-prefix", "m2"], "both"),
        (["--board", "--milestone", "2", "--dry-run"], "both"),
        (["--branch-prefix", "m2"], "required"),
        (["--milestone", "2"], "required"),
        (["--milestone", "2", "--dry-run"], "required"),
        (["--card", SOME_CARD], "required"),
        (["--board", "--branch-prefix", ""], "blank"),
        (["--board", "--branch-prefix", "   "], "blank"),
        (["--board", "--max-concurrent", "0"], "least"),
        (["--board", "--dry-run", "--max-concurrent", "0"], "least"),
        (["--card", SOME_CARD, "--branch-prefix", "m2", "--max-concurrent", "2"], "only"),
    ],
)
def test_bad_board_targets_are_usage_errors_that_start_nothing(
    tmp_path, monkeypatch, targets, word
):
    """Spec tests 1-4, 7, 9 at the command line: Typer's exit 2, never an
    envelope, and `run_board`, `run_milestone`, `run_card` all unreached."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)

    result = runner.invoke(cli.app, ["run", *targets, "--repo-dir", str(tmp_path)])

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert word in result.output
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "check_run_targets or bad_board_targets" -v`
Expected: the `_check_run_targets` tests FAIL with `TypeError: _check_run_targets() got an unexpected keyword argument 'board'`; most CLI cases FAIL on the `word in result.output` assertion (Typer says `No such option: --board` / `Missing option '--branch-prefix'`).

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/cli.py`, replace the whole `_check_run_targets` function (lines 962-1010) with:

```python
def _check_run_targets(
    *,
    card: str | None,
    milestone: str | None,
    dry_run: bool,
    max_concurrent: int | None = None,
    board: bool = False,
    branch_prefix: str | None = None,
) -> None:
    """Refuse a bad `--card` / `--milestone` / `--board` / `--branch-prefix` / `--dry-run` / `--max-concurrent` combination as a usage error.

    `typer.BadParameter` is Typer's own exit 2, which `EXIT_ERROR`'s docstring
    reserves. It is raised before the `HANDLED` try block, so nothing is read
    or dispatched. Exactly one of `--card`, `--milestone` and `--board` is a
    target. A blank `--milestone` is refused here too: the census strips
    the needle, and an empty needle is a substring of every title, so on a
    one-milestone board it would silently pick that milestone.
    `--branch-prefix` is an Option with no default so that board mode can
    omit it (each milestone then uses its own card stem, run-board spec 3.2);
    `--card` and `--milestone` still require it, refused here at the same
    exit 2 Typer gave a missing required option. A blank one with `--board`
    is refused, since `<prefix>-<stem>` would start with a dash.
    `--max-concurrent` is `None` when not given, so giving it with `--card` is
    refused whatever its value, the default included. The Option has no
    `min=1`, so a value below 1 is refused here, worded and routed like every
    other run-target refusal.
    """
    if board and card is not None:
        raise typer.BadParameter(
            "give --board or --card, not both",
            param_hint="'--board' / '--card'",
        )
    if board and milestone is not None:
        raise typer.BadParameter(
            "give --board or --milestone, not both",
            param_hint="'--board' / '--milestone'",
        )
    if card is not None and milestone is not None:
        raise typer.BadParameter(
            "give --card or --milestone, not both",
            param_hint="'--card' / '--milestone'",
        )
    if card is None and milestone is None and not board:
        raise typer.BadParameter(
            "one of --card, --milestone or --board is required",
            param_hint="'--card' / '--milestone' / '--board'",
        )
    if milestone is not None and not milestone.strip():
        raise typer.BadParameter(
            "--milestone needs a card id or a title substring, not a blank string",
            param_hint="'--milestone'",
        )
    if not board and branch_prefix is None:
        raise typer.BadParameter(
            "--branch-prefix is required with --card or --milestone",
            param_hint="'--branch-prefix'",
        )
    if board and branch_prefix is not None and not branch_prefix.strip():
        raise typer.BadParameter(
            "--branch-prefix with --board needs a non-blank prefix, not a blank string",
            param_hint="'--branch-prefix'",
        )
    if dry_run and card is not None:
        raise typer.BadParameter(
            "--dry-run previews a milestone and does not apply to --card",
            param_hint="'--dry-run'",
        )
    if max_concurrent is not None and max_concurrent < 1:
        raise typer.BadParameter(
            f"--max-concurrent must be at least 1, got {max_concurrent}",
            param_hint="'--max-concurrent'",
        )
    if card is not None and max_concurrent is not None:
        raise typer.BadParameter(
            "--max-concurrent applies only to --milestone or --board",
            param_hint="'--max-concurrent'",
        )
```

In the `run` signature, change the `--card` option's help (lines 1024-1026) to:

```python
    card: str | None = typer.Option(
        None,
        "--card",
        help="The subtask card id to drive. Exclusive with --milestone and --board.",
    ),
```

change the `--milestone` help's last line `"Exclusive with --card."` (line 1032) to `"Exclusive with --card and --board."`, and insert this option right after the `milestone` option (after line 1034, before `dry_run`):

```python
    whole_board: bool = typer.Option(
        False,
        "--board",
        help=(
            "Drive every open milestone on the board as one dependency graph: a "
            "milestone starts once every milestone blocking it finished done. "
            "Exclusive with --card and --milestone."
        ),
    ),
```

Replace the `branch_prefix` option (lines 1063-1067) with:

```python
    branch_prefix: str | None = typer.Option(
        None,
        "--branch-prefix",
        help=(
            "Milestone prefix for the derived branch name, e.g. `m2`. Required with "
            "--card and --milestone. Optional with --board: each milestone's prefix "
            "is its own card stem, or `<prefix>-<stem>` when given."
        ),
    ),
```

Replace the `_check_run_targets(...)` call in `run` (lines 1084-1089) with:

```python
    _check_run_targets(
        card=card,
        milestone=milestone,
        dry_run=dry_run,
        max_concurrent=max_concurrent,
        board=whole_board,
        branch_prefix=branch_prefix,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "check_run_targets or bad_board_targets or bad_run_targets or branch_prefix" -v`
Expected: all PASS, including the pre-existing `test_bad_run_targets_are_usage_errors_that_start_nothing` (its `"required"` and `"only"` words still appear in the new wording) and `test_run_without_branch_prefix_is_a_usage_error_not_an_envelope` (still exit 2 naming `--branch-prefix`; skipped if git/brd are absent).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Validate am run --board targets and make --branch-prefix board-optional"
```

---

### Task 3: `--dry-run --board` preview (`dry_run_board`) and the board exit-code branch

**Files:**
- Modify: `src/agent_manager/cli.py` (new `dry_run_board` after `board_prefix_of`; `run`'s try block, lines 1091-1121; exit-code block, lines 1126-1136)
- Test: `tests/test_cli.py` (append after Task 2's block; also edit `_forbid_board_paths` from Task 2)

**Interfaces:**
- Consumes: `cli.board_prefix_of` (Task 1); `board.roots(*, repo_dir) -> list[models.CardNode]` (`board.py:235`); `dag.board_levels(roots: list[CardNode]) -> list[list[CardNode]]` (`dag.py:207`); `orchestrate.board_prefixes(milestones, branch_prefix_of) -> dict[str, str]` (`orchestrate.py:1847`); `census.flatten_milestone(root: CardNode) -> Census` with `.stories` (`census.py:197`); `cli.dry_run_payload` (`cli.py:879`); `resolve_repo_dir` (`runs.py:75`); test helpers `_add_card`, `_block`, `_git`, `_assert_nothing_written`, `_refusal`, `_plan_id`, `project` fixture, `requires_git`, `requires_brd`.
- Produces: `cli.dry_run_board(*, repo_dir: Path, branch_prefix: str | None, base_branch: str, max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> dict[str, Any]`; the board branch of `run`'s exit-code logic (Task 4 relies on it).

- [ ] **Step 1: Write the failing tests**

In Task 2's `_forbid_board_paths`, add one line at the end of its body so refusals also prove the board preview is unreached:

```python
    monkeypatch.setattr(cli, "dry_run_board", _Forbidden("dry_run_board"))
```

(This line makes Task 2's refusal tests fail with `AttributeError` until Step 3 adds `dry_run_board`; that is expected RED for this task.)

Append after Task 2's block:

```python
def _as_json(value: Any) -> Any:
    """`value` as the CLI would print it: `render` stringifies any `Path`."""
    return json.loads(cli.render({"value": value}))["value"]


def _forbid_board_dry_run_writes(monkeypatch) -> None:
    """The board preview must open no Store, check no claims and run nothing."""
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(store_module, "Store", _Forbidden("store.Store"))
    monkeypatch.setattr(cli, "refuse_claimed", _Forbidden("refuse_claimed"))
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    monkeypatch.setattr(orchestrate, "run_board", _Forbidden("run_board"))
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))


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


def _board_dry_run(repo_dir: Path, *extra: str):
    return runner.invoke(
        cli.app,
        [
            "run",
            "--board",
            "--dry-run",
            "--repo-dir",
            str(repo_dir),
            "--base-branch",
            "main",
            *extra,
        ],
    )


@requires_git
@requires_brd
def test_the_board_dry_run_previews_every_open_milestone_by_level_and_writes_nothing(
    project, monkeypatch
):
    """Spec test 13: two milestones, the second blocked by the first, on a real
    temporary brd board. Two levels, each milestone with its derived prefix and
    its own `dry_run_payload` nested as `plan`; nothing run, nothing written."""
    first = _add_card(project, "Milestone A: the adapter")
    first_story = _add_card(project, "Story A1: read cards", first)
    _add_card(project, "a1: read one card", first_story)
    second = _add_card(project, "Milestone B: the store")
    second_story = _add_card(project, "Story B1: the schema", second)
    _add_card(project, "b1: write the schema", second_story)
    _block(project, second, first)
    board_before = board.roots(repo_dir=project)
    roots = {card.id: card for card in board_before}
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(project)

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert list(data) == ["board", "max_concurrent", "levels"]
    assert data["board"] is True
    assert data["max_concurrent"] == cli.DEFAULT_MAX_CONCURRENT
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
            assert list(entry) == ["milestone_id", "title", "branch_prefix", "plan"]
    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


def _board_milestone(
    n: int,
    *,
    status: str = "todo",
    blocked_by: tuple[str, ...] = (),
    card_id: str | None = None,
    title: str | None = None,
) -> models.CardNode:
    """A milestone root with one open story holding one open subtask."""
    return models.CardNode(
        id=card_id or _plan_id(n),
        title=title or f"Milestone {n}",
        status=status,
        blocked_by=list(blocked_by),
        children=[
            models.CardNode(
                id=_plan_id(n * 100 + 1),
                title=f"story {n}",
                status="todo",
                children=[
                    models.CardNode(id=_plan_id(n * 100 + 2), title=f"subtask {n}", status="todo")
                ],
            )
        ],
    )


def _serve_roots(monkeypatch, roots: list[models.CardNode]) -> None:
    monkeypatch.setattr(cli.board, "roots", lambda *, repo_dir=None: list(roots))


def test_the_board_dry_run_derives_prefixes_from_a_given_prefix_and_drops_done_milestones(
    tmp_path, monkeypatch
):
    """A done milestone is not previewed and the one it blocked lands in level 0.
    A given prefix becomes `<prefix>-<stem>` in each milestone's nested plan, and
    --max-concurrent is echoed at both levels of the payload."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    done = _board_milestone(1, status="done")
    second = _board_milestone(2, blocked_by=(done.id,))
    third = _board_milestone(3, blocked_by=(second.id,))
    _serve_roots(monkeypatch, [done, second, third])
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


def test_an_empty_board_dry_runs_to_no_levels_and_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _serve_roots(monkeypatch, [])
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {
        "board": True,
        "max_concurrent": cli.DEFAULT_MAX_CONCURRENT,
        "levels": [],
    }


@pytest.mark.parametrize(
    "roots, error_type",
    [
        (
            [
                _board_milestone(1, blocked_by=(_plan_id(2),)),
                _board_milestone(2, blocked_by=(_plan_id(1),)),
            ],
            "DependencyCycleError",
        ),
        ([_board_milestone(1, card_id="not-a-uuid")], "ValueError"),
        (
            [
                _board_milestone(1, title="Milestone twin"),
                _board_milestone(
                    2, card_id="00000001-0000-4000-8000-000000000001", title="Milestone twin"
                ),
            ],
            "ValueError",
        ),
    ],
    ids=["milestone-cycle", "non-uuid-milestone", "shared-derived-prefix"],
)
def test_a_board_dry_run_refusal_is_an_envelope_and_writes_nothing(
    tmp_path, monkeypatch, roots, error_type
):
    """Review focus: a cycle among milestones, a milestone id `dag.task_stem`
    cannot read, and two milestones deriving one prefix are each the same
    `HANDLED` refusal the real run gives: `ok: false`, exit 3."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _serve_roots(monkeypatch, roots)
    _forbid_board_dry_run_writes(monkeypatch)

    error = _refusal(_board_dry_run(tmp_path))

    assert error["type"] == error_type
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "board_dry_run or empty_board or bad_board_targets" -v`
Expected: FAIL. `bad_board_targets` cases fail with `AttributeError: <module 'agent_manager.cli'> has no attribute 'dry_run_board'`; the dry-run tests fail because `--board --dry-run` reaches the forbidden `cli.run_card` (`Failed: the milestone dry run reached cli.run_card`). The brd-backed test is skipped if git/brd are not installed.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/cli.py`, insert after `board_prefix_of` (Task 1), before `HANDLED`:

```python
def dry_run_board(
    *,
    repo_dir: Path,
    branch_prefix: str | None,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
    """`--dry-run --board`: every open milestone, by level, each with its own preview.

    Read-only by construction, like `dry_run_milestone`. `board.roots()` is
    read once (each root already nests its whole tree), leveled by
    `dag.board_levels` (a done milestone drops out, a cycle is
    `DependencyCycleError`), and each milestone's prefix comes from
    `orchestrate.board_prefixes` over `board_prefix_of`, the very check
    `run_board` makes. Each milestone's `plan` is its own `dry_run_payload`.
    No `Store` is opened, no claim is checked, no runner is built, and
    `orchestrate.run_board` is never called. Every refusal is a type already
    in `HANDLED`.
    """
    root = resolve_repo_dir(repo_dir)
    levels = dag.board_levels(board.roots(repo_dir=root))
    milestones = [card for level in levels for card in level]
    prefixes = orchestrate.board_prefixes(milestones, board_prefix_of(branch_prefix))
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
                        "plan": dry_run_payload(
                            census.flatten_milestone(card).stories,
                            repo_dir=root,
                            branch_prefix=prefixes[card.id],
                            base_branch=base_branch,
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

In `run`, replace the first branch of the try block — the line `        if milestone is not None and dry_run:` (line 1092) — with these lines, so the existing milestone dry-run branch becomes an `elif`:

```python
        if whole_board and dry_run:
            payload = dry_run_board(
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
                max_concurrent=lanes,
            )
        elif milestone is not None and dry_run:
```

Replace the exit-code block at the end of `run` (lines 1126-1136, from the `# A card payload reports` comment through `raise typer.Exit(EXIT_ESCALATED)`) with:

```python
    # A board payload carries one entry per milestone under `milestones`, each
    # with a `status`; a board dry-run carries no `milestones` key at all, so
    # it is read with `.get` and an empty default. A card payload reports
    # `status`. A milestone payload has no `status` key: it carries
    # `escalated: true` only when it stopped, a clean one carries `done: true`,
    # and a dry-run preview carries neither, so it is read with `.get`, never
    # indexed. Every check is strict equality on purpose: a `stopped` card
    # (addendum P4), or a stopped, cancelled or blocked milestone, is not an
    # escalation and exits 0.
    if whole_board:
        escalated = any(
            entry.get("status") == "escalated" for entry in payload.get("milestones", [])
        )
    elif milestone is None:
        escalated = payload["status"] == "escalated"
    else:
        escalated = payload.get("escalated") is True
    if escalated:
        raise typer.Exit(EXIT_ESCALATED)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "board_dry_run or empty_board or bad_board_targets or check_run_targets or board_prefix_of" -v`
Expected: all PASS (the brd-backed preview test PASSes where git and brd are installed). If `brd block` refuses to block one root card by another on the installed brd, keep the test's shape but serve the two roots by hand through `monkeypatch.setattr(cli.board, "roots", ...)` built from `board.tree(first)` / `board.tree(second)` with `blocked_by` set on the second, the way `test_a_story_cycle_from_the_board_is_an_envelope_naming_both_stories` (`tests/test_cli.py:2917`) serves its tree.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Add am run --board --dry-run: a read-only per-milestone preview by level"
```

---

### Task 4: Real `--board` run through `orchestrate.run_board`, exit codes, help and examples

**Files:**
- Modify: `src/agent_manager/cli.py` (`run`'s try block, after the `whole_board and dry_run` branch from Task 3; `--dry-run` and `--max-concurrent` help in `run`'s signature, lines 1035-1051; `RUN_EXAMPLES`, lines 1013-1019; `run`'s docstring, line 1083)
- Test: `tests/test_cli.py` (append after Task 3's block)

**Interfaces:**
- Consumes: `orchestrate.run_board(*, repo_dir, base_branch, branch_prefix_of, commands=(), allow_no_verification=False, runner_factory=None, driver=None, clock=..., max_concurrent=1, control_interval=...) -> dict[str, Any]` (`orchestrate.py:1910`); `cli.board_prefix_of` (Task 1); `_forbid_board_paths` (Task 2, extended in Task 3); `BOARD_CARD` (Task 1); `_refusal`, `_plan_id`; the board exit-code branch (Task 3).
- Produces: the `--board` real-run dispatch in `run`; `RUN_EXAMPLES` containing `am run --board`.

- [ ] **Step 1: Write the failing tests**

Append after Task 3's block:

```python
def _board_payload(*statuses: str) -> dict[str, Any]:
    """`run_board`'s payload shape with one milestone entry per status."""
    entries = [
        {
            "milestone_id": _plan_id(index + 1),
            "status": status,
            "run_id": f"20261001T000000Z-{index + 1:08x}",
        }
        for index, status in enumerate(statuses)
    ]
    return {
        "ok": all(status == "done" for status in statuses),
        "board": True,
        "levels": (
            [{"level": 0, "milestones": [entry["milestone_id"] for entry in entries]}]
            if entries
            else []
        ),
        "milestones": entries,
    }


def _board_run(tmp_path: Path, *extra: str):
    return runner.invoke(
        cli.app,
        ["run", "--board", "--repo-dir", str(tmp_path), "--base-branch", "main", *extra],
    )


def _patch_run_board(monkeypatch, outcome: Any) -> list[dict[str, Any]]:
    """Replace `orchestrate.run_board`, forbid every other run path, record calls.

    `outcome` is returned, or raised when it is an exception instance.
    """
    _forbid_board_paths(monkeypatch)
    calls: list[dict[str, Any]] = []

    def fake_run_board(**kwargs):
        calls.append(kwargs)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(orchestrate, "run_board", fake_run_board)
    return calls


def test_a_board_run_calls_run_board_once_with_the_run_options(tmp_path, monkeypatch):
    """Spec tests 5 and 8: base branch, verify order, the opt-out and the default
    lane count reach `run_board` unchanged, with no `runner_factory` or `driver`
    (the kwargs are compared whole, so an extra key fails). With --branch-prefix
    omitted, each milestone's prefix is its own stem."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_board(monkeypatch, _board_payload("done"))

    result = _board_run(
        tmp_path,
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
        "--allow-no-verification",
    )

    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    prefix_of = kwargs.pop("branch_prefix_of")
    assert kwargs == {
        "repo_dir": tmp_path,
        "base_branch": "main",
        "commands": ["uv run pytest", "uv run ruff check"],
        "allow_no_verification": True,
        "max_concurrent": cli.DEFAULT_MAX_CONCURRENT,
    }
    assert prefix_of(BOARD_CARD) == dag.task_stem(BOARD_CARD)


def test_a_board_run_with_a_prefix_hands_run_board_prefix_dash_stem(tmp_path, monkeypatch):
    """Spec test 6: never the given prefix verbatim."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_board(monkeypatch, _board_payload("done"))

    result = _board_run(tmp_path, "--branch-prefix", "sprint9")

    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    assert kwargs["branch_prefix_of"](BOARD_CARD) == f"sprint9-{dag.task_stem(BOARD_CARD)}"
    assert kwargs["branch_prefix_of"](BOARD_CARD) != "sprint9"


@pytest.mark.parametrize("given, passed", [("1", 1), ("2", 2), ("7", 7)])
def test_an_explicit_max_concurrent_reaches_run_board(tmp_path, monkeypatch, given, passed):
    """Spec test 8: in board mode the flag is the board-wide story bound."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_board(monkeypatch, _board_payload("done"))

    result = _board_run(tmp_path, "--max-concurrent", given)

    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    assert kwargs["max_concurrent"] == passed


def test_a_board_run_without_verify_passes_an_empty_list_and_no_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_board(monkeypatch, _board_payload("done"))

    result = _board_run(tmp_path)

    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    assert kwargs["commands"] == []
    assert kwargs["allow_no_verification"] is False


def test_a_board_run_prints_run_boards_payload_in_the_ok_envelope(tmp_path, monkeypatch):
    """Spec test 10: the payload unchanged under `data`; --pretty indents the same JSON."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    payload = _board_payload("done", "done")
    _patch_run_board(monkeypatch, payload)

    plain = _board_run(tmp_path)
    pretty = _board_run(tmp_path, "--pretty")

    assert plain.exit_code == 0, plain.output
    assert "\n" not in plain.stdout.strip()
    assert json.loads(plain.stdout) == cli.ok_envelope(payload)
    assert pretty.exit_code == 0, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


@pytest.mark.parametrize(
    "statuses, exit_code",
    [
        (("done",), 0),
        (("done", "done"), 0),
        ((), 0),
        (("stopped",), 0),
        (("cancelled",), 0),
        (("done", "stopped", "blocked"), 0),
        (("cancelled", "blocked"), 0),
        (("escalated",), cli.EXIT_ESCALATED),
        (("done", "escalated"), cli.EXIT_ESCALATED),
        (("escalated", "blocked"), cli.EXIT_ESCALATED),
        (("stopped", "escalated", "cancelled"), cli.EXIT_ESCALATED),
    ],
)
def test_a_board_run_exits_escalated_only_when_some_milestone_escalated(
    tmp_path, monkeypatch, statuses, exit_code
):
    """Spec test 11: the board-wide form of the milestone rule. A stopped,
    cancelled or blocked milestone is not an escalation; an empty board is clean.
    The envelope is `ok: true` either way: an escalation is a truthful result."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    payload = _board_payload(*statuses)
    _patch_run_board(monkeypatch, payload)

    result = _board_run(tmp_path)

    assert result.exit_code == exit_code, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(payload)


@pytest.mark.parametrize(
    "error",
    [
        ValueError("max_concurrent must be at least 1, got 0"),
        dag.DependencyCycleError("dag: dependency cycle among milestones #a, #b"),
        board.BoardError("brd refused", argv=["brd", "tree"]),
        cli.CliError("run 20261001T000000Z-00000001 already claims branch:m-integrate"),
    ],
    ids=["ValueError", "DependencyCycleError", "BoardError", "CliError"],
)
def test_a_handled_error_from_a_board_run_is_an_envelope(tmp_path, monkeypatch, error):
    """Spec test 12: every `HANDLED` refusal is `ok: false` at exit 3."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_board(monkeypatch, error)

    refusal = _refusal(_board_run(tmp_path))

    assert refusal["type"] == type(error).__name__
    assert refusal["message"] == str(error)


def test_an_unhandled_error_from_a_board_run_crashes_loudly(tmp_path, monkeypatch):
    """Review focus: anything outside `HANDLED` is a bug and keeps its traceback."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_board(monkeypatch, RuntimeError("boom"))

    result = _board_run(tmp_path)

    assert isinstance(result.exception, RuntimeError)
    assert '"ok"' not in result.stdout


def test_the_run_examples_and_help_show_the_board_mode():
    """Spec scope: `--board` is documented in `--help` and in the examples epilog."""
    assert "am run --board" in cli.RUN_EXAMPLES

    result = runner.invoke(cli.app, ["run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--board" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "board_run or run_board or run_examples" -v`
Expected: the `_patch_run_board` tests FAIL with `Failed: the milestone dry run reached cli.run_card` (a non-dry `--board` still falls through to `run_card`); `test_the_run_examples_and_help_show_the_board_mode` FAILS on `assert "am run --board" in cli.RUN_EXAMPLES`.

- [ ] **Step 3: Write minimal implementation**

In `run`'s try block in `src/agent_manager/cli.py`, insert this branch right after the `if whole_board and dry_run:` branch added in Task 3 (i.e. directly before `elif milestone is not None and dry_run:`):

```python
        elif whole_board:
            # Read as `orchestrate.run_board` so a test can patch it there.
            # No runner_factory and no driver: production gets the defaults.
            # `lanes` is the board-wide bound on stories running at once.
            payload = orchestrate.run_board(
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix_of=board_prefix_of(branch_prefix),
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
            )
```

Replace the `dry_run` option's help (lines 1038-1041) with:

```python
        help=(
            "With --milestone: show the plan (story order, each subtask's branch "
            "and base, merged bases) and write nothing. With --board: show every "
            "open milestone by level, each with its own plan, and write nothing."
        ),
```

Replace the `max_concurrent` option's help (lines 1046-1050) with:

```python
        help=(
            "With --milestone: how many stories run at once "
            f"(default {DEFAULT_MAX_CONCURRENT}). A story starts as soon as its "
            "blockers finish. With --board: how many stories run at once across "
            "the whole board."
        ),
```

Replace `RUN_EXAMPLES` (lines 1013-1019) with:

```python
RUN_EXAMPLES = """\
Examples:
  am run --milestone "M9" --branch-prefix m9 --dry-run --pretty       # preview the plan
  am run --milestone "M9" --branch-prefix m9 --verify "uv run pytest"  # run it
  am run --board --verify "uv run pytest"                             # run every open milestone
  am status <run-id> --pretty                                         # watch it (another terminal)
  am resume <run-id> --verify "uv run pytest"                         # after a fix, stop or crash
"""
```

Replace `run`'s docstring (line 1083) with:

```python
    """Drive one subtask card, a whole milestone, or every open milestone (--board) end to end, or preview a milestone or the board with --dry-run."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "board or run_board or run_examples or check_run_targets" -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all PASS (the `e2e`-marked test stays deselected by `addopts`). In particular the pre-existing `test_bad_run_targets_are_usage_errors_that_start_nothing`, `test_run_without_branch_prefix_is_a_usage_error_not_an_envelope`, the milestone-run tests, and `test_cli_and_orchestrate_import_cleanly_in_either_order` still pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Wire am run --board to orchestrate.run_board with board-wide exit codes"
```

---

## Spec coverage map

| Spec item | Task |
|---|---|
| `--board` option; `--branch-prefix` optional at Typer level | 2 |
| `_check_run_targets` board/prefix refusals, wording, hints; dry-run allowed with board; max-concurrent message | 2 (tests 1-4, 7, 9) |
| `board_prefix_of` per 3.2 | 1 (test 14), 4 (tests 5, 6) |
| Real run calls `orchestrate.run_board` with forwarded options, no runner_factory/driver | 4 (test 8) |
| Envelope / `--pretty` | 4 (test 10) |
| Board exit code via `payload.get("milestones", [])` | 3 (implementation; dry-run exits 0), 4 (test 11) |
| `HANDLED` → exit 3 | 3 (dry-run refusals), 4 (test 12) |
| `dry_run_board` read-only, key order, nested `dry_run_payload`, empty board | 3 (test 13 + review-focus tests) |
| Help text for `--board`, `--branch-prefix`, `--dry-run`, `--max-concurrent`; `RUN_EXAMPLES` line | 2, 4 |
| Existing tests keep passing / reworded messages | 2 (Step 4), 4 (Step 5) |
