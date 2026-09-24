<!-- task-pipeline: validated -->
# Wire `am run --milestone` and prove it under a fake claude (card 3e0ab2b9)

Parent story f8290fd6 ("Run a milestone: rollup, the shared driver, the runner"), milestone 3. Narrows the orchestration addendum (`docs/superpowers/specs/2026-09-24-orchestration-design.md`) decisions O4, O6 and O8 and acceptance items 2, 3 and 5 to one subtask. The siblings are already built on this branch: the shared driver `cli.drive_subtask` (38809280), the ancestor rollup in `rollup.set_status` (bf26f482), and `orchestrate.run_milestone` (c9037ac9). This card wires the CLI and proves the whole path end to end. It does not redesign any of them.

## Scope

1. `src/agent_manager/cli.py`, the `run` command (currently ~L961-1031).
   - `--milestone X` without `--dry-run` calls `orchestrate.run_milestone(X, repo_dir=..., base_branch=..., branch_prefix=..., commands=list(verify), allow_no_verification=...)`. It passes no `runner_factory` and no `driver`, so production gets `cli.default_runner_factory` and `cli.drive_subtask`.
   - It uses the same `--verify`, `--allow-no-verification`, `--base-branch` and `--branch-prefix` options as `--card`. It adds no new options.
   - `MilestoneRunNotImplementedError` and the branch that raises it are removed. The `--milestone` help text drops "Needs --dry-run for now".
   - `orchestrate` already imports `cli` at module level, so `cli` must reach `orchestrate` without a circular import at load time. A function-local import in the milestone branch is one way to do that. Planning picks the mechanism.
   - `_check_run_targets`, the `--dry-run` path and the whole `--card` path stay exactly as they are (story rule: `am run --card` behaves exactly as now).
2. `tests/e2e/`: a new fake-claude milestone module, plus extensions to `conftest.py` and `fake_claude.py`. Both files are extended, not copied.
3. `orchestrate.py` and `steps/rollup.py` change only for a bug the e2e exposes. Any such fix is named in the plan and gets its own regression test in the matching unit module.

## Observable behavior

- Clean milestone: prints `{"ok": true, "data": <run_milestone payload>}` (with `done: true`, `run_id`, `levels`, `completed`, `tips`, `warnings`) and exits 0. `--pretty` indents it.
- Escalated milestone: prints `ok: true` with the escalation payload (`escalated: true`, `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail`, `warnings`) and exits `EXIT_ESCALATED` (1). This mirrors `run --card` on `status == "escalated"`: an escalation is a truthful result, not an error.
- The exit-code check must key off the payload's `escalated` flag for milestones and `status` for cards. A milestone payload has no `status` key, so it must never be indexed for one. The clean payload also has no `escalated` key (it carries `done: true`), so read the flag with `payload.get("escalated")`, never `payload["escalated"]`.

## Error paths

- Any `HANDLED` exception from `run_milestone` gives an `ok: false` `error_envelope` and `EXIT_ERROR` (3). Examples: an unknown or ambiguous milestone, a two-blocker story or a cycle (`CliError`/`ValueError`), a `board.BoardError`, a `WorkflowLoadError`, an `EngineError`. These refusals come before any write, as `run_milestone` already guarantees.
- Exceptions outside `HANDLED` keep crashing loudly, as today.
- Usage errors (`--card` with `--milestone`, a blank `--milestone`, `--dry-run` with `--card`) are unchanged Typer exit 2s.

## Fake-claude constraints (O8, milestone-2 rule)

The fake must never know more than the brief tells it. It takes the hash from `## plan_hash`, never commits the spec or plan, and learns the result path only from the prompt text. It gets no env var, argv flag or other side channel.

- **Failing B's review.** A new `review` branch in `build_result` must produce a schema-valid result that a real `review` gate blocks, so `failed_phase == "review"` comes through the production gate path.
  - The trigger comes from the review brief, specifically its `## branch` section, which carries B's subtask branch.
  - Note: `review_gate` blocks on a dirty `porcelain`, zero commits, or `tagged_count < commit_count`, and `plan_hash_gate` blocks on a hash mismatch. Nothing reads `unresolved_blockers`, so setting that field alone fails nothing.
  - Every field set must pass `override()`'s drift check.
- **Fixing it for the relaunch.** The test must switch the failure off between runs through something the fake can legitimately see. The agreed shape is a test-controlled marker file that names the branch(es) whose review should fail. The fake finds the marker from its own cwd, and it must neither sit in the worktree's tracked or untracked tree nor dirty `git status`. The fake compares the marker to `## branch`, and the test removes or empties it before relaunching. Planning fixes the exact location.
- **Re-entering B.** On relaunch, B's subtask re-enters through `worktree.ensure`, `plan_check`'s skip and Plan-Hash re-entrancy. Its `implement` must still succeed under the fake (for example, the fake must not fail on "nothing to commit" when its implementation file is already committed), and the fake still must not compute the hash. Planning must confirm this path.
- Existing e2e tests (`test_production_wiring.py`, `test_fake_claude.py`, `test_real_harness.py`) must keep passing unchanged in meaning.

## Fixtures

- `conftest.py`'s repo+board setup is reused. The current `project` fixture is one module-scoped repo, so the milestone tests need their own fresh repo+board per scenario. The fixture is factored or parameterised to allow that, not duplicated.
- `_add_card` gains blocker support through `brd block <id> --by <blocker>`, as `tests/test_orchestrate.py::_block` uses.
- Board: one milestone and three stories.
  - A has two subtasks, chained by `brd block`.
  - B is blocked by A and has one subtask.
  - C is blocked by B and has one subtask.
- The repo is on `main` with no `origin`, so `refresh_git` skips the fetch. `VERIFY_COMMANDS` is reused.
- Both tests invoke the real command through `typer.testing.CliRunner` on `cli.app` (`run --milestone <id> --repo-dir <repo> --base-branch main --branch-prefix <p> --verify ...`) with `fake_claude_bin` first on PATH. They pass no runner_factory, so the real `ClaudeAdapter` and `launcher.run_direct` run.

## Tests

The tier comes from main spec §14 "Testing" and addendum O8: a fake-claude milestone proof through production wiring belongs in the unmarked default-suite e2e tier (`tests/e2e/`), and CLI wiring assertions may go in `tests/test_cli.py`.

**tests/e2e/ (default suite, unmarked), new module, e.g. `tests/e2e/test_milestone_run.py`:**

1. *Clean three-story milestone* (acceptance 2). Asserts:
   - Exit 0 and `ok: true`, `data.done` is true.
   - Every subtask card is `done` on the board.
   - Stories A, B and C and the milestone are `done` through rollup, not written directly by the test.
   - Each subtask branch contains its predecessor's commit (`git merge-base --is-ancestor`): a1 before a2, and in particular a2 (A's last) before b1 (B's first), and b1 before c1.
   - Every commit the run made on every subtask branch, above the base, carries a `Plan-Hash:` trailer.
   - `main`'s tip SHA is identical before and after the run.
2. *Escalation stops the run, and relaunching resumes* (acceptances 3 and 5).
   - With the review-fail marker naming B's subtask branch, the first invocation exits 1 with `ok: true`.
   - `data.escalated` is true, `data.story` is B, `data.subtask` is b1, `data.failed_phase` is `"review"`.
   - Story C never started:
     - No worktree exists at `cli.worktree_for(repo, <c1 branch>)`.
     - The run's store projection shows c1 with no started phases or attempts.
     - The fake's log for that run has no entry whose cwd is C's worktree.
   - The test removes the marker, and the same command is run again. It exits 0 with `data.done` true, and `data.completed` equals `[b1, c1]`. A's subtasks are not re-driven: the second run's fake log has no entry in A's worktrees.
   - All cards, stories and the milestone end `done`.

**tests/test_cli.py (CLI wiring, `orchestrate.run_milestone` monkeypatched):**

3. `--milestone` without `--dry-run` calls `run_milestone` once with the given milestone, `--repo-dir`, `--base-branch`, `--branch-prefix`, `--verify` list (order kept) and `--allow-no-verification`, and with no `runner_factory`. It exits 0 with `ok_envelope(payload)`.
4. A payload with `escalated: true` exits 1 with an `ok: true` envelope.
5. A `HANDLED` error raised by `run_milestone`, for example a `CliError` or `board.BoardError`, gives `ok: false` at exit 3.
6. `test_a_milestone_run_without_dry_run_is_a_not_implemented_envelope` (~L2586) and the `MilestoneRunNotImplementedError` subclass assertion (~L1191) are removed or replaced by tests 3-5. Every `--card` and `--dry-run` test stays green unchanged.

**tests/e2e/test_fake_claude.py (default suite):** the new review-failure branch gets a unit-style check next to the existing ones. With the marker naming the brief's branch, the review result is one a review gate blocks. Without it, the result is unchanged. This pins the trigger to what the brief and the marker say.

## Out of scope

- Parallel stories, Integrate, milestone-aware `am resume`, and `watch`/`retry`/`cancel`.
- Review counts measured by git, and verification discovery (addendum §4).
- The `--dry-run` preview (36faf21e) and the blocked-coder gate (dd321e61, O7).
- Changes to the runner's ordering, stacking, escalation or skip logic, or to the rollup walk.
- The opt-in real-`claude` milestone test (`pytest -m e2e`) is not part of this card's default-suite proof.

Verification: `uv run pytest` (whole default suite, including `tests/e2e`) green. There is no typecheck or lint step.

---

# Wire `am run --milestone` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am run --milestone X` without `--dry-run` drives the whole milestone through `orchestrate.run_milestone`, and a default-suite e2e tier proves it with the real adapter, the real launcher and a fake `claude` on PATH, covering a clean run, an escalation at B's review, and a relaunch that finishes the milestone.

**Architecture:** `cli.run` reaches `orchestrate` with a function-local import, because `orchestrate` imports `cli` at module level. It picks the exit code from `status` for cards and from `payload.get("escalated")` for milestones. The fake `claude` gets two brief-driven changes. First, its implement file content is keyed on the brief's `## plan_path`, so stacked subtasks each commit, and it reports `resumed: true` when there is nothing new to commit. Second, its review reports a dirty `porcelain` when a marker file in the repo's git common dir names the brief's `## branch`. `tests/e2e/conftest.py` is factored so each milestone scenario gets a fresh repo and board.

**Tech Stack:** Python 3, Typer (`typer.testing.CliRunner`), pytest, git, the `brd` CLI, `uv`.

**Spec:** `docs/superpowers/specs/task-wire-am-run-milestone-3e0ab2b9-design.md`, reproduced verbatim above.

**Branch / worktree:** `m3/task-wire-am-run-milestone-3e0ab2b9` at `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-wire-am-run-milestone-3e0ab2b9`, cut from `m3/task-add-the-sequential-c9037ac9`. Every path below is relative to that worktree. The plan relies only on code already on that branch: `orchestrate.run_milestone` (`src/agent_manager/orchestrate.py:235`), `cli.drive_subtask` (`cli.py:673`), `cli.dry_run_milestone` (`cli.py:896`), `_check_run_targets` (`cli.py:930`) and the `run` command (`cli.py:961-1031`).

## Global Constraints

- Verification: `uv run pytest`, the whole default suite including `tests/e2e`. There is no typecheck or lint step.
- CLI output is brd's envelope `{"ok": true, "data": ...}`, one line by default, indented under `--pretty`. `EXIT_ESCALATED = 1`, `EXIT_ERROR = 3`, and 2 is Typer's usage error.
- `am run --card` behaves exactly as now, and the `--dry-run` path and `_check_run_targets` are untouched.
- The CLI passes no `runner_factory` and no `driver` to `run_milestone`, and it adds no new options.
- A milestone payload is never indexed for `status`, and `escalated` is read with `payload.get("escalated")`.
- The fake `claude` knows only what the brief says. It never computes the plan hash for `implement` (it takes `## plan_hash`), never commits the spec or plan, learns the result path only from the prompt, and gets no env var or argv flag. The one extra input is the test-controlled review-fail marker file, which it finds from its own cwd through git. That file is not in any worktree's tree and does not dirty `git status`.
- `override()`'s drift check stays: every field the fake sets must exist in the embedded schema.
- `orchestrate.py` and `steps/rollup.py` change only for a bug the e2e exposes, and each such fix gets its own regression test in `tests/test_orchestrate.py` or `tests/steps/test_rollup.py`.
- The e2e tests are unmarked (default suite), live in `tests/e2e/`, and reuse `conftest.py` and `fake_claude.py` rather than copying them.
- Tests under `tests/e2e` cannot import `conftest` by name (`--import-mode=importlib`), so shared values reach test modules as fixtures.

## Review Focus

1. **Import order between `cli` and `orchestrate`.** A fresh interpreter that imports `agent_manager.orchestrate` first (as `tests/test_orchestrate.py` does) or `agent_manager.cli` first must load both, and `orchestrate.cli` must be `cli`. Pinned in Task 1 (`test_cli_and_orchestrate_import_cleanly_in_either_order`).
2. **An exception outside `HANDLED` from `run_milestone`** (a bug such as `RuntimeError`) must crash loudly, not become an envelope. Pinned in Task 1 (`test_an_unhandled_error_from_a_milestone_run_crashes_loudly`).
3. **No `--verify` and no `--allow-no-verification`** must reach `run_milestone` as `commands == []` (never `None`) and `allow_no_verification is False`. Pinned in Task 1 (`test_a_milestone_run_without_verify_passes_an_empty_list_and_no_opt_out`).
4. **A stacked subtask whose base already holds the fake's implementation file**, and a re-entered subtask whose implementation is already committed, must both get through `implement`. The first must make its own commit, and the second must resume instead of failing on "nothing to commit". Pinned in Task 2 (`test_a_stacked_subtask_with_its_own_plan_commits_its_own_implementation`, `test_a_second_implement_on_the_same_plan_resumes_instead_of_failing`).
5. **Relaunching a milestone that is already finished** must drive nothing and exit 0 with `completed == []`. Pinned in Task 4 (the third invocation inside `test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it`).

## File Structure

- Modify `src/agent_manager/cli.py`: remove `MilestoneRunNotImplementedError` (L127-134), update the `--milestone` help text (L969) and the `run` body (L1002-1031).
- Modify `tests/test_cli.py`: remove `test_the_not_implemented_refusal_is_a_cli_error` (L1190-1191) and `test_a_milestone_run_without_dry_run_is_a_not_implemented_envelope` (L2586-2612), and add the milestone wiring tests in their place near L2586.
- Modify `tests/e2e/fake_claude.py`: make `implement` plan-keyed and resumable, and add the review-fail marker to `review`.
- Modify `tests/e2e/test_fake_claude.py`: add tests for the new fake behaviour.
- Modify `tests/e2e/conftest.py`: add `_block`, blocker support in `_add_card`, a factored `_init_project`, and the new fixtures `fresh_project`, `milestone_board`, `run_milestone_cli`, `read_fake_log` and `review_fail_marker`.
- Create `tests/e2e/test_milestone_run.py`: the two milestone scenarios.

---

### Task 1: Wire `am run --milestone` to `orchestrate.run_milestone`

**Files:**
- Modify: `src/agent_manager/cli.py:127-134` (delete the class), `cli.py:966-970` (help text), `cli.py:1002-1031` (command body)
- Test: `tests/test_cli.py` (imports at L15-41; delete L1190-1191; replace L2586-2612)

**Interfaces:**
- Consumes: `orchestrate.run_milestone(milestone: str, *, repo_dir: Path, base_branch: str, branch_prefix: str, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory=None, driver=None, clock=...) -> dict[str, Any]` (`src/agent_manager/orchestrate.py:235`). Its clean payload has the keys `done, run_id, levels, completed, tips, warnings`. Its escalated payload has the keys `escalated, run_id, level, story, subtask, failed_phase, detail, warnings`.
- Produces: `am run --milestone X [--verify C]... [--allow-no-verification] --base-branch B --branch-prefix P --repo-dir R` calls `orchestrate.run_milestone(X, repo_dir=R, base_branch=B, branch_prefix=P, commands=[C...], allow_no_verification=bool)` with exactly those keyword arguments. It exits 0 on a clean run, 1 on `escalated: true` and 3 on a `HANDLED` error. Tasks 3 and 4 drive this command through `CliRunner`.

- [ ] **Step 1: Delete the tests the spec retires**

In `tests/test_cli.py`, delete these two lines together with the blank lines that separate them from their neighbours (L1190-1191):

```python
def test_the_not_implemented_refusal_is_a_cli_error():
    assert issubclass(cli.MilestoneRunNotImplementedError, cli.CliError)
```

Then delete the whole function `test_a_milestone_run_without_dry_run_is_a_not_implemented_envelope` (L2586-2612, from its `def` line down to `assert list(paths.data_dir().iterdir()) == []`).

- [ ] **Step 2: Add the imports the new tests need**

In `tests/test_cli.py`, add `import sys` after `import subprocess` (L19). Then add `orchestrate` to the `from agent_manager import (...)` block (L27-37), so that it reads:

```python
from agent_manager import (
    board,
    census,
    cli,
    dag,
    dispatch,
    models,
    orchestrate,
    paths,
    prompt,
    store as store_module,
)
```

- [ ] **Step 3: Write the failing wiring tests**

In `tests/test_cli.py`, where the deleted `test_a_milestone_run_without_dry_run_is_a_not_implemented_envelope` stood (just before `@pytest.fixture` / `def projection`), insert:

```python
CLEAN_MILESTONE = {
    "done": True,
    "run_id": "20260924T000000Z-0badcafe",
    "levels": [{"level": 0, "stories": ["story-a"]}],
    "completed": ["subtask-a1"],
    "tips": [{"story": "story-a", "tip": "m3/task-a1"}],
    "warnings": [],
}
"""`run_milestone`'s clean payload shape: `done: true` and no `status` or `escalated` key."""

ESCALATED_MILESTONE = {
    "escalated": True,
    "run_id": "20260924T000000Z-0badcafe",
    "level": 1,
    "story": "story-b",
    "subtask": "subtask-b1",
    "failed_phase": "review",
    "detail": "phase 'review' gate 'review_gate' failed",
    "warnings": [],
}
"""`run_milestone`'s escalation payload shape: no `status` key either."""


def _milestone_run(tmp_path: Path, *extra: str):
    return runner.invoke(
        cli.app,
        [
            "run",
            "--milestone",
            "Milestone 3",
            "--repo-dir",
            str(tmp_path),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m3",
            *extra,
        ],
    )


def _patch_run_milestone(monkeypatch, outcome: Any) -> list[tuple[str, dict[str, Any]]]:
    """Replace `orchestrate.run_milestone`, forbid every other run path, record calls.

    `outcome` is returned, or raised when it is an exception instance.
    """
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run_milestone(milestone, **kwargs):
        calls.append((milestone, kwargs))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(orchestrate, "run_milestone", fake_run_milestone)
    return calls


def test_a_milestone_run_calls_run_milestone_once_with_the_run_options(
    tmp_path, monkeypatch
):
    """Spec test 3: the same options as `--card`, the verify order kept, and no
    `runner_factory` or `driver`, so production gets `cli.default_runner_factory`
    and `cli.drive_subtask`. The kwargs are compared whole, so an extra key fails."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_milestone(monkeypatch, CLEAN_MILESTONE)

    result = _milestone_run(
        tmp_path,
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
        "--allow-no-verification",
    )

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    assert json.loads(result.stdout) == cli.ok_envelope(CLEAN_MILESTONE)
    assert calls == [
        (
            "Milestone 3",
            {
                "repo_dir": tmp_path,
                "base_branch": "main",
                "branch_prefix": "m3",
                "commands": ["uv run pytest", "uv run ruff check"],
                "allow_no_verification": True,
            },
        )
    ]


def test_a_milestone_run_without_verify_passes_an_empty_list_and_no_opt_out(
    tmp_path, monkeypatch
):
    """Review focus: `gate_context` calls `list(commands)`, so `None` would crash,
    and the opt-out must stay closed unless the flag is given."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_milestone(monkeypatch, CLEAN_MILESTONE)

    result = _milestone_run(tmp_path)

    assert result.exit_code == 0, result.output
    ((_, kwargs),) = calls
    assert kwargs["commands"] == []
    assert kwargs["allow_no_verification"] is False


def test_an_escalated_milestone_exits_one_with_an_ok_envelope(tmp_path, monkeypatch):
    """Spec test 4: an escalation is a truthful result. The payload has no
    `status` key, so the exit code must come from its `escalated` flag."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, ESCALATED_MILESTONE)

    plain = _milestone_run(tmp_path)
    pretty = _milestone_run(tmp_path, "--pretty")

    assert plain.exit_code == cli.EXIT_ESCALATED, plain.output
    assert json.loads(plain.stdout) == cli.ok_envelope(ESCALATED_MILESTONE)
    assert pretty.exit_code == cli.EXIT_ESCALATED, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


@pytest.mark.parametrize(
    "error",
    [
        cli.CliError("no milestone matches 'Milestone 3'"),
        board.BoardError("brd refused", argv=["brd", "tree"]),
        ValueError("not a card id: 'x'"),
    ],
    ids=["CliError", "BoardError", "ValueError"],
)
def test_a_handled_error_from_a_milestone_run_is_an_envelope(tmp_path, monkeypatch, error):
    """Spec test 5: every `HANDLED` refusal is `ok: false` at exit 3."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, error)

    refusal = _refusal(_milestone_run(tmp_path))

    assert refusal["type"] == type(error).__name__
    assert refusal["message"] == str(error)


def test_an_unhandled_error_from_a_milestone_run_crashes_loudly(tmp_path, monkeypatch):
    """Review focus: anything outside `HANDLED` is a bug and keeps its traceback."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _patch_run_milestone(monkeypatch, RuntimeError("boom"))

    result = _milestone_run(tmp_path)

    assert isinstance(result.exception, RuntimeError)
    assert '"ok"' not in result.stdout


@pytest.mark.parametrize("first", ["agent_manager.cli", "agent_manager.orchestrate"])
def test_cli_and_orchestrate_import_cleanly_in_either_order(first):
    """Review focus: `orchestrate` imports `cli` at module level, so `cli` must
    not import `orchestrate` at load time. A fresh interpreter, so this test
    does not depend on what earlier tests already imported."""
    code = (
        f"import {first}\n"
        "from agent_manager import cli, orchestrate\n"
        "assert orchestrate.cli is cli\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "milestone_run or escalated_milestone or import_cleanly" -v`
Expected: the five wiring tests fail. They get an `ok: false` envelope with `"type": "MilestoneRunNotImplementedError"` at exit 3 where they expect exit 0 or 1, and the crash test finds `"ok"` in stdout. The parametrized `BoardError`/`CliError`/`ValueError` cases fail on `refusal["type"]`. The two `test_cli_and_orchestrate_import_cleanly_in_either_order` cases PASS already. They guard the import order that Step 5 must preserve, and are expected to stay green.

- [ ] **Step 5: Delete `MilestoneRunNotImplementedError`**

In `src/agent_manager/cli.py`, delete the whole class and the two blank lines after it (L127-135):

```python
class MilestoneRunNotImplementedError(CliError):
    """`run --milestone` without `--dry-run`: the real milestone run is not built yet.

    A `CliError` so it rides `HANDLED` into an `ok: false` envelope at exit 3.
    It is raised before the repo dir is resolved or the board is read, so a
    refusal reads nothing and writes nothing. The next story replaces it with
    the real run.
    """
```

- [ ] **Step 6: Update the `--milestone` help text**

In `src/agent_manager/cli.py`, replace:

```python
    milestone: str | None = typer.Option(
        None,
        "--milestone",
        help="A milestone card id or title substring. Needs --dry-run for now.",
    ),
```

with:

```python
    milestone: str | None = typer.Option(
        None,
        "--milestone",
        help=(
            "A milestone card id or title substring: drive every remaining subtask. "
            "Exclusive with --card."
        ),
    ),
```

- [ ] **Step 7: Replace the command body**

In `src/agent_manager/cli.py`, replace everything from the docstring `"""Drive one subtask card end to end, or preview a milestone with --dry-run."""` to the end of the `run` function (`raise typer.Exit(EXIT_ESCALATED)`) with:

```python
    """Drive one subtask card or a whole milestone end to end, or preview a milestone with --dry-run."""
    _check_run_targets(card=card, milestone=milestone, dry_run=dry_run)
    try:
        if milestone is not None and dry_run:
            payload = dry_run_milestone(
                milestone,
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
            )
        elif milestone is not None:
            # `orchestrate` imports this module at load time and reads its names
            # at call time, so importing it at the top of this module would be
            # circular. By the time a command runs, both are fully loaded. Read
            # as `orchestrate.run_milestone` so a test can patch it there. No
            # runner_factory and no driver: production gets
            # `default_runner_factory` and `drive_subtask`.
            from agent_manager import orchestrate

            payload = orchestrate.run_milestone(
                milestone,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=list(verify),
                allow_no_verification=allow_no_verification,
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
    # A card payload reports `status`. A milestone payload has no `status` key:
    # it carries `escalated: true` only when it stopped, a clean one carries
    # `done: true`, and a dry-run preview carries neither. So the flag is read
    # with `.get`, never indexed.
    if milestone is None:
        escalated = payload["status"] == "escalated"
    else:
        escalated = payload.get("escalated") is True
    if escalated:
        raise typer.Exit(EXIT_ESCALATED)
```

- [ ] **Step 8: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "milestone_run or escalated_milestone or import_cleanly" -v`
Expected: PASS for every case.

- [ ] **Step 9: Run the whole CLI module to prove `--card` and `--dry-run` are unchanged**

Run: `uv run pytest tests/test_cli.py tests/test_orchestrate.py -v`
Expected: PASS. This includes every existing `--card`, `--dry-run` and `test_bad_run_targets_are_usage_errors_that_start_nothing` case, none of which were edited.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): wire run --milestone to the sequential milestone runner"
```

---

### Task 2: Teach the fake `claude` to stack, re-enter, and fail a review on a marker

**Files:**
- Modify: `tests/e2e/fake_claude.py` (module docstring L1-16, constants near L185-197, `build_result` L270-307)
- Test: `tests/e2e/test_fake_claude.py` (helper `_implement_brief_text` at L376-384, and new tests appended at the end)

**Interfaces:**
- Consumes: the brief sections `## plan_path`, `## plan_hash` (implement), and `## branch`, `## base_branch`, `## plan_path` (review), as `builtin/task.yaml:65-77` declares and `prompt._assemble` renders them (`## <name>\n<value>`).
- Produces:
  - `fake_claude.REVIEW_FAIL_MARKER = "fake-claude-review-fail"`: the marker's file name, inside the directory `git rev-parse --git-common-dir` names from the fake's cwd.
  - `fake_claude.REVIEW_FAIL_PORCELAIN`: the string the failing review reports as `porcelain`, which contains the words `review-fail marker`.
  - `fake_claude.review_fail_branches(cwd) -> set[str]`.
  - `implement` writes `IMPLEMENTATION.md` with content that names the brief's plan path. When that leaves nothing to commit, it returns `resumed: True` and makes no commit.
  - Task 3 relies on stacked subtasks each committing. Task 4 relies on the marker location (`<repo>/.git/fake-claude-review-fail`, one branch per line) and on the `review-fail marker` wording.

- [ ] **Step 1: Let the implement brief helper take a plan path**

In `tests/e2e/test_fake_claude.py`, replace `_implement_brief_text` (L376-384) with:

```python
def _implement_brief_text(digest=None, plan=PLAN_RELATIVE):
    """An `implement` brief, optionally without its `## plan_hash` section."""
    section = "" if digest is None else f"\n## plan_hash\n{digest}\n"
    return (
        "# Coder\n\nstanding instructions\n\n"
        "# phase: implement\n# role: coder\n"
        f"\n## plan_path\n{plan}\n"
        f"{section}"
    )
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/e2e/test_fake_claude.py`:

```python
def _head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _porcelain(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _implement(repo, plan=PLAN_RELATIVE):
    return fake_claude.build_result(
        "implement",
        fake_claude.payload_from_schema(IMPLEMENT_SCHEMA),
        _implement_brief_text(BRIEF_HASH, plan),
        repo,
    )


def test_a_stacked_subtask_with_its_own_plan_commits_its_own_implementation(tmp_path):
    """Review focus: a milestone stacks a2 on a1's branch, so a2's worktree already
    holds a1's implementation file. The file's content names the brief's plan
    path, so a2's coder still has something to commit."""
    repo = _implement_repo(tmp_path)
    first = _implement(repo)
    after_first = _head(repo)

    second = _implement(repo, plan="docs/superpowers/plans/y-00000002.md")

    assert first["resumed"] is False
    assert second["resumed"] is False
    assert _head(repo) != after_first
    assert _head_message(repo).rstrip("\n").endswith(f"Plan-Hash: {BRIEF_HASH}")
    assert _porcelain(repo) == ""


def test_a_second_implement_on_the_same_plan_resumes_instead_of_failing(tmp_path):
    """Review focus / spec "Re-entering B": a relaunched subtask's implementation
    is already committed. The fake reports `resumed` rather than dying on
    "nothing to commit", and it still takes its hash from the brief."""
    repo = _implement_repo(tmp_path)
    _implement(repo)
    committed = _head(repo)

    again = _implement(repo)

    assert again["resumed"] is True
    assert again["plan_hash"] == BRIEF_HASH
    assert _head(repo) == committed
    assert _porcelain(repo) == ""


REVIEW_SCHEMA = {
    "properties": {
        "findings": {"items": {"type": "string"}, "type": "array"},
        "unresolved_blockers": {"items": {"type": "string"}, "type": "array"},
        "fix_summary": {"type": "string"},
        "porcelain": {"type": "string"},
        "commit_count": {"type": "integer"},
        "tagged_count": {"type": "integer"},
        "plan_hash": {"type": "string"},
    },
    "type": "object",
}
"""`results.ReviewResult`'s shape, written out for the same reason as
`IMPLEMENT_SCHEMA`."""

REVIEW_BRANCH = "m3/task-b1-00000003"


def _review_worktree(tmp_path):
    """A linked worktree on `REVIEW_BRANCH` with one tagged commit, as review finds it.

    Linked, not the main checkout, because a subtask's agents run in
    `<repo>/.claude/worktrees/<branch>`, where `.git` is a file and not the
    directory the marker lives in.
    """
    repo = _implement_repo(tmp_path)
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", str(worktree), "-b", REVIEW_BRANCH],
        check=True,
        capture_output=True,
    )
    _implement(worktree)
    return repo, worktree


def _review(worktree, branch=REVIEW_BRANCH):
    text = (
        "# Reviewer\n\nstanding instructions\n\n"
        "# phase: review\n# role: reviewer\n"
        f"\n## branch\n{branch}\n"
        "\n## base_branch\nmain\n"
        f"\n## plan_path\n{PLAN_RELATIVE}\n"
    )
    return fake_claude.build_result(
        "review", fake_claude.payload_from_schema(REVIEW_SCHEMA), text, worktree
    )


def test_the_review_fail_marker_name_is_the_one_the_e2e_fixtures_write():
    """`tests/e2e/conftest.py` writes `FAKE_REVIEW_FAIL_MARKER`; the script and the
    fixture meet across a process boundary, like `LOG_NAME`."""
    assert fake_claude.REVIEW_FAIL_MARKER == "fake-claude-review-fail"


def test_without_a_marker_the_review_passes_the_real_review_gate(tmp_path):
    _, worktree = _review_worktree(tmp_path)

    payload = _review(worktree)

    assert payload["findings"] == []
    assert payload["unresolved_blockers"] == []
    assert payload["porcelain"] == ""
    assert payload["commit_count"] == payload["tagged_count"] == 1
    assert review_gate(payload, REVIEW_BRANCH, "main") is None


def test_a_marker_naming_the_briefs_branch_makes_a_review_the_real_gate_blocks(tmp_path):
    """Spec: the trigger is the brief's `## branch` matched against a marker in the
    repo's git common dir. The failing result is schema-shaped (every field went
    through `override`) and `review_gate` blocks it on `porcelain`."""
    repo, worktree = _review_worktree(tmp_path)
    marker = repo / ".git" / fake_claude.REVIEW_FAIL_MARKER
    marker.write_text(f"m3/task-other-00000009\n{REVIEW_BRANCH}\n", encoding="utf-8")

    payload = _review(worktree)

    assert sorted(payload) == sorted(REVIEW_SCHEMA["properties"])
    assert payload["porcelain"] == fake_claude.REVIEW_FAIL_PORCELAIN
    assert "review-fail marker" in payload["porcelain"]
    assert payload["unresolved_blockers"]
    verdict = review_gate(payload, REVIEW_BRANCH, "main")
    assert verdict is not None and "blocked" in verdict
    # The marker lives outside every worktree's tree.
    assert _porcelain(worktree) == ""
    assert _porcelain(repo) == ""


def test_a_marker_naming_only_other_branches_does_not_fail_this_review(tmp_path):
    """Whole-line equality, never a prefix test: a branch that merely starts with
    this one's name must not fail it."""
    repo, worktree = _review_worktree(tmp_path)
    (repo / ".git" / fake_claude.REVIEW_FAIL_MARKER).write_text(
        f"{REVIEW_BRANCH}-later\n", encoding="utf-8"
    )

    payload = _review(worktree)

    assert payload["porcelain"] == ""
    assert review_gate(payload, REVIEW_BRANCH, "main") is None
```

Then add the gate import after `import pytest` (L20), leaving a blank line between them:

```python
from agent_manager.steps.reducers import review_gate
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected:
- `test_a_stacked_subtask_with_its_own_plan_commits_its_own_implementation` fails with `FakeClaudeError: git commit ... failed ... nothing to commit`.
- `test_a_second_implement_on_the_same_plan_resumes_instead_of_failing` fails the same way.
- The marker-name test fails with `AttributeError: ... has no attribute 'REVIEW_FAIL_MARKER'`.
- `test_a_marker_naming_the_briefs_branch_makes_a_review_the_real_gate_blocks` and `test_a_marker_naming_only_other_branches_does_not_fail_this_review` fail with the same `AttributeError` on `REVIEW_FAIL_MARKER`.
- `test_without_a_marker_the_review_passes_the_real_review_gate` passes already, because today's review already passes without a marker. It pins that the new branch leaves the unmarked result unchanged.
- Every pre-existing test still passes.

- [ ] **Step 4: Add the marker constants and lookup**

In `tests/e2e/fake_claude.py`, directly after the `LOG_NAME` constant and its docstring (after L197), insert:

```python
REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""A file, in the repo's git common dir, naming branches whose review must fail.

One branch per line. It is test-controlled and found from this process's own
cwd through `git rev-parse --git-common-dir`, so it sits inside `.git`: it is
in no worktree's tree and never shows in `git status`. It is compared with
the review brief's `## branch` section, so the brief is still what picks the
subtask. This is not an env var or an argv flag, and the fake computes nothing
it could not read.
"""

REVIEW_FAIL_PORCELAIN = "?? fake-claude: the review-fail marker names this branch"
"""What a failing review reports as `porcelain`. Non-empty, so the production
`review_gate` blocks on it, and worded so the escalation detail says why."""


def review_fail_branches(cwd):
    """The branches the review-fail marker names, or an empty set when there is none."""
    common = git(cwd, "rev-parse", "--git-common-dir").strip()
    # Relative (`.git`) in a main checkout, absolute in a linked worktree;
    # joining onto the cwd handles both.
    marker = Path(cwd) / common / REVIEW_FAIL_MARKER
    if not marker.is_file():
        return set()
    return {
        line.strip()
        for line in marker.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
```

- [ ] **Step 5: Make `implement` plan-keyed and resumable**

In `tests/e2e/fake_claude.py`, replace the whole `if phase == "implement":` block (L270-288) with:

```python
    if phase == "implement":
        # Card f26b377d: the hash comes from the brief's `## plan_hash` section,
        # never from hashing the plan. A fake that computed it would keep the
        # wiring test green with the input missing from `builtin/task.yaml`,
        # which is the one thing this tier exists to catch (R4).
        digest = _section(found, "plan_hash", phase)
        relative = _section(found, "plan_path", phase)
        # The content names this card's plan, so a subtask stacked on another's
        # branch (where the file already exists) still has a change to commit.
        (Path(cwd) / IMPLEMENTATION_NAME).write_text(
            f"# implementation of {relative}\n\n{SUMMARY}\n", encoding="utf-8"
        )
        git(cwd, "add", "-A")
        # A relaunched subtask's implementation is already committed: nothing
        # changed, so there is nothing to commit, and the honest answer is
        # `resumed`, not a failed `git commit`.
        resumed = git(cwd, "status", "--porcelain").strip() == ""
        if not resumed:
            git(cwd, "commit", "-m", f"feat: implement this card\n\nPlan-Hash: {digest}")
        return override(
            payload,
            blocked=False,
            blocked_reason=None,
            resumed=resumed,
            plan_hash=digest,
            report=SUMMARY,
        )
```

- [ ] **Step 6: Add the marker branch to `review`**

In `tests/e2e/fake_claude.py`, replace the whole `if phase == "review":` block (L289-307) with:

```python
    if phase == "review":
        relative = _section(found, "plan_path", phase)
        base = _section(found, "base_branch", phase)
        branch = _section(found, "branch", phase)
        revisions = git(cwd, "rev-list", f"{base}..HEAD").split()
        tagged = [
            revision
            for revision in revisions
            if "Plan-Hash:" in git(cwd, "show", "-s", "--format=%B", revision)
        ]
        if branch in review_fail_branches(cwd):
            # A review the production `review_gate` blocks: a non-empty
            # `porcelain`. `unresolved_blockers` alone would fail nothing,
            # because no gate reads it.
            findings = [f"the review-fail marker names {branch}"]
            porcelain = REVIEW_FAIL_PORCELAIN
        else:
            findings = []
            porcelain = git(cwd, "status", "--porcelain").strip()
        return override(
            payload,
            findings=findings,
            unresolved_blockers=list(findings),
            fix_summary=SUMMARY,
            porcelain=porcelain,
            commit_count=len(revisions),
            tagged_count=len(tagged),
            plan_hash=plan_hash_of(Path(cwd) / relative),
        )
```

- [ ] **Step 7: Record the marker in the module docstring**

In `tests/e2e/fake_claude.py`, replace the module docstring's third paragraph (L8-13, from "Everything it needs comes out of the brief on disk" to "that failure is the test's whole point.") with:

```python
Everything it needs comes out of the brief on disk: the prompt path from the
adapter's `-p` sentence (`harness/claude.py:30`), and the absolute result path
plus the JSON Schema from the `## Result contract` section the brief carries
(`prompt.py:281-374`). There is deliberately no environment variable, no extra
argv flag and no import of `agent_manager` -- a brief that omits the contract
must make this script fail, because that failure is the test's whole point.
The one test-controlled input is `REVIEW_FAIL_MARKER`, a file in the repo's git
common dir that the fake finds from its own cwd and compares with the brief's
`## branch`.
```

- [ ] **Step 8: Run the fake's tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: PASS for every test, old and new.

- [ ] **Step 9: Run the single-card production-wiring tier to prove its meaning is unchanged**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: PASS. That run has no marker, so its review still reports the real `porcelain` and its implement still makes exactly one commit.

- [ ] **Step 10: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_fake_claude.py
git commit -m "test(e2e): let the fake claude stack, re-enter and fail a review on a marker"
```

---

### Task 3: Fresh milestone boards in the e2e conftest, and the clean three-story run

**Files:**
- Modify: `tests/e2e/conftest.py` (imports L11-21, constants after L44, `_add_card` L55-62, `project` L80-110, new fixtures appended)
- Create: `tests/e2e/test_milestone_run.py`

**Interfaces:**
- Consumes: `am run --milestone` from Task 1, and the stacking-safe fake from Task 2.
- Produces (fixtures in `tests/e2e/conftest.py`, used again in Task 4):
  - `fresh_project -> Path`: function-scoped. A new git repo on `main` and a new brd board, with `XDG_DATA_HOME` in `tmp_path`.
  - `milestone_board -> dict[str, Any]` with the keys `root: Path`, `milestone: str`, `stories: {"A": id, "B": id, "C": id}`, `subtasks: {"A": [a1, a2], "B": [b1], "C": [c1]}` and `branches: {subtask_id: branch}` (prefix `MILESTONE_PREFIX = "m3"`).
  - `run_milestone_cli -> Callable[[Path, str], click.testing.Result]`: invokes `run --milestone <id> --repo-dir <root> --base-branch main --branch-prefix m3 --verify <VERIFY_COMMANDS...>` with `fake_claude_bin` on PATH.
  - `read_fake_log -> Callable[[str], list[dict]]`: the fake's log entries for a run id, or `[]` when it has none.
  - Plain helpers `_block(root, card_id, blocker)` and `_add_card(root, title, parent=None, blocked_by=())`.

- [ ] **Step 1: Write the failing clean-run test**

Create `tests/e2e/test_milestone_run.py`:

```python
"""Default-suite e2e tier: `am run --milestone` through the production wiring.

Addendum O8 and main spec §14: the command runs through `typer.testing.CliRunner`
on the real `cli.app`, with no `runner_factory`. So `orchestrate.run_milestone`
reaches `cli.drive_subtask`, `cli.default_runner_factory`, the real
`ClaudeAdapter` and `launcher.run_direct`, and the only stand-in is the fake
`claude` first on `PATH`. Unmarked on purpose: this costs no model and must run
on every `uv run pytest`.

Each test builds its own repo and board (`milestone_board`), because a
milestone run moves every card it touches.
"""

import json
import subprocess
from pathlib import Path

from agent_manager import board, cli, models, store


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """`git merge-base --is-ancestor`: exit 0 yes, exit 1 no, anything else fails."""
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _all_cards(milestone_board) -> list[str]:
    return [
        *(card for chain in milestone_board["subtasks"].values() for card in chain),
        *milestone_board["stories"].values(),
        milestone_board["milestone"],
    ]


def test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line(
    milestone_board, run_milestone_cli
):
    """Spec test 1 / acceptance 2."""
    root = milestone_board["root"]
    subtasks = milestone_board["subtasks"]
    stories = milestone_board["stories"]
    branches = milestone_board["branches"]
    order = [*subtasks["A"], *subtasks["B"], *subtasks["C"]]
    main_before = _git(root, "rev-parse", "main").strip()

    result = run_milestone_cli(root, milestone_board["milestone"])

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert data["completed"] == order
    assert [level["stories"] for level in data["levels"]] == [
        [stories["A"]],
        [stories["B"]],
        [stories["C"]],
    ]

    # Done ON THE BOARD, subtasks by `mark_done` and stories and the milestone
    # by the rollup walk. This test writes no status itself.
    for card_id in _all_cards(milestone_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id

    # One stacked line: each branch contains its predecessor's commits.
    chain = [branches[card_id] for card_id in order]
    for earlier, later in zip(chain, chain[1:]):
        assert _is_ancestor(root, earlier, later), (earlier, later)
    assert _is_ancestor(root, branches[subtasks["A"][-1]], branches[subtasks["B"][0]])

    # Every commit the run made, on every subtask branch, carries the trailer.
    for branch in chain:
        revisions = _git(root, "rev-list", f"main..{branch}").split()
        assert revisions, branch  # non-vacuity
        for revision in revisions:
            message = _git(root, "show", "-s", "--format=%B", revision)
            assert any(
                line.startswith("Plan-Hash: ") for line in message.splitlines()
            ), (branch, revision, message)

    assert _git(root, "rev-parse", "main").strip() == main_before


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_production_wiring.py`'s guard: no `e2e` marker may reach this
    module, or the milestone wiring stops being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()
```

`models` and `store` are imported now because Task 4 appends to this module and uses them.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/e2e/test_milestone_run.py -v`
Expected: `test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line` ERRORS with `fixture 'milestone_board' not found`. The marker guard passes.

- [ ] **Step 3: Extend the conftest imports and constants**

In `tests/e2e/conftest.py`, replace the import block (L11-21) with:

```python
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, dag, models, paths, store
```

After the `AGENT_PHASES` constant and its docstring (after L44), insert:

```python
MILESTONE_PREFIX = "m3"
"""The `--branch-prefix` every milestone-run test uses."""
```

- [ ] **Step 4: Give `_add_card` blocker support**

In `tests/e2e/conftest.py`, replace `_add_card` (L55-62) with:

```python
def _block(root: Path, card_id: str, blocker: str) -> None:
    """`brd block <id> --by <blocker>`, as `tests/test_orchestrate.py::_block` does."""
    subprocess.run(
        ["brd", "block", card_id, "--by", blocker],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _add_card(
    root: Path,
    title: str,
    parent: str | None = None,
    blocked_by: Sequence[str] = (),
) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    card_id = json.loads(completed.stdout)["data"]["id"]
    for blocker in blocked_by:
        _block(root, card_id, blocker)
    return card_id
```

- [ ] **Step 5: Factor the repo+board setup out of `project`**

In `tests/e2e/conftest.py`, replace the whole `project` fixture (L80-110) with:

```python
def _init_project(root: Path, board_name: str) -> Path:
    """Make `root` both a real git repo on `main` and a real brd board.

    Shared by the module-scoped `project` and the per-test `fresh_project`, so
    the two tiers' repos cannot drift apart. The caller points `XDG_DATA_HOME`
    into tmp first.
    """
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(root, "config", "user.email", "tests@example.com")
    git(root, "config", "user.name", "agent-manager tests")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "base")
    subprocess.run(
        ["brd", "init", "--name", board_name],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    # brd leaves its `.gitignore`/`.brd` markers untracked; committing them keeps
    # the baseline clean, so the later porcelain check reflects only the run.
    git(root, "add", "-A")
    git(root, "commit", "-m", "brd init")
    return root


@pytest.fixture(scope="module")
def project(tmp_path_factory, module_monkeypatch, toolchain) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board."""
    base = tmp_path_factory.mktemp("e2e")
    module_monkeypatch.setenv("XDG_DATA_HOME", str(base / "xdg"))
    return _init_project(base / "project", "e2e-board")
```

- [ ] **Step 6: Add the milestone fixtures**

Append to the end of `tests/e2e/conftest.py`:

```python
@pytest.fixture
def fresh_project(tmp_path, monkeypatch, toolchain) -> Path:
    """A new repo+board per test, with its own `XDG_DATA_HOME`.

    Function-scoped because a milestone run moves every card and branch it
    touches, so two scenarios cannot share one board.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return _init_project(tmp_path / "project", "e2e-milestone-board")


@pytest.fixture
def milestone_board(fresh_project) -> dict[str, Any]:
    """One milestone and three stories: A (a1 -> a2), B blocked by A (b1), C blocked by B (c1).

    A's subtasks are chained with `brd block`, so the census order does not
    depend on timestamps. Branch names come from `dag`, never retyped here.
    """
    root = fresh_project
    milestone = _add_card(root, "Milestone 3: run a milestone under a fake claude")
    a = _add_card(root, "Story A: the first level", milestone)
    b = _add_card(root, "Story B: blocked by story A", milestone, blocked_by=[a])
    c = _add_card(root, "Story C: blocked by story B", milestone, blocked_by=[b])
    a1 = _add_card(root, "a1: first subtask of story A", a)
    a2 = _add_card(root, "a2: second subtask of story A", a, blocked_by=[a1])
    b1 = _add_card(root, "b1: only subtask of story B", b)
    c1 = _add_card(root, "c1: only subtask of story C", c)
    subtasks = {"A": [a1, a2], "B": [b1], "C": [c1]}
    branches = {
        card_id: dag.task_branch(MILESTONE_PREFIX, board.show(card_id, repo_dir=root))
        for chain in subtasks.values()
        for card_id in chain
    }
    return {
        "root": root,
        "milestone": milestone,
        "stories": {"A": a, "B": b, "C": c},
        "subtasks": subtasks,
        "branches": branches,
    }


@pytest.fixture
def run_milestone_cli(fake_claude_bin) -> Callable[[Path, str], Any]:
    """`am run --milestone` through `CliRunner`, with no runner_factory anywhere.

    Depends on `fake_claude_bin` so the fake is first on `PATH`: the real
    `ClaudeAdapter` resolves `claude` to it through the real `run_direct`.
    """
    runner = CliRunner()

    def invoke(root: Path, milestone: str):
        argv = [
            "run",
            "--milestone",
            milestone,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            MILESTONE_PREFIX,
        ]
        for command in VERIFY_COMMANDS:
            argv += ["--verify", command]
        return runner.invoke(cli.app, argv)

    return invoke


@pytest.fixture
def read_fake_log() -> Callable[[str], list[dict[str, Any]]]:
    """The fake's cwd log for one run id, or `[]` for a run that launched no agent."""

    def read(run_id: str) -> list[dict[str, Any]]:
        log = paths.run_dir(run_id) / FAKE_LOG_NAME
        if not log.is_file():
            return []
        return [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return read
```

- [ ] **Step 7: Run the clean-run test to verify it passes**

Run: `uv run pytest tests/e2e/test_milestone_run.py -v`
Expected: PASS. If it fails for a reason that is not in this task's test or fixture code, it is an e2e-exposed bug. Follow superpowers:systematic-debugging to find the root cause. If the cause is in `src/agent_manager/orchestrate.py` or `src/agent_manager/steps/rollup.py`, first write a regression test that reproduces it in `tests/test_orchestrate.py` or `tests/steps/test_rollup.py` and watch it fail. Then fix the cause, and name the fix in the commit message. Do not loosen an assertion in this test to get past it.

- [ ] **Step 8: Run the rest of the e2e tier to prove the `project` refactor changed nothing**

Run: `uv run pytest tests/e2e -v`
Expected: PASS. `test_real_harness.py` stays opt-in or skipped, exactly as before.

- [ ] **Step 9: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_milestone_run.py
git commit -m "test(e2e): prove a clean three-story am run --milestone under a fake claude"
```

---

### Task 4: Escalation at B's review stops the run, and a relaunch finishes it

**Files:**
- Modify: `tests/e2e/conftest.py` (append one fixture and one constant)
- Modify: `tests/e2e/test_milestone_run.py` (append one test)

**Interfaces:**
- Consumes: `milestone_board`, `run_milestone_cli` and `read_fake_log` from Task 3. From Task 2, `fake_claude.REVIEW_FAIL_MARKER == "fake-claude-review-fail"` and the words `review-fail marker` in the failing review's `porcelain`. Also `store.open_db(root)` and `store.load_run(conn, run_id) -> models.Run | None` (as `conftest.run_tree` uses them), `cli.worktree_for(root, branch) -> Path` and `cli.EXIT_ESCALATED`.
- Produces: the `review_fail_marker -> Path` fixture (`<root>/.git/fake-claude-review-fail`).

- [ ] **Step 1: Write the failing escalation-and-relaunch test**

Append to `tests/e2e/test_milestone_run.py`:

```python
def _load_run(root: Path, run_id: str) -> models.Run:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run


def _cwds(entries) -> set[Path]:
    return {Path(entry["cwd"]).resolve() for entry in entries}


def test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it(
    milestone_board, review_fail_marker, run_milestone_cli, read_fake_log
):
    """Spec test 2 / acceptances 3 and 5."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    stories = milestone_board["stories"]
    branches = milestone_board["branches"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    c_worktree = cli.worktree_for(root, branches[c1])
    b_worktree = cli.worktree_for(root, branches[b1])
    review_fail_marker.write_text(f"{branches[b1]}\n", encoding="utf-8")

    # First launch: B's review fails through the production review gate.
    first = run_milestone_cli(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["escalated"] is True, stopped
    assert stopped["story"] == stories["B"]
    assert stopped["subtask"] == b1
    assert stopped["failed_phase"] == "review"
    assert "review_gate" in stopped["detail"]
    assert "review-fail marker" in stopped["detail"]
    # The marker never dirtied B's worktree.
    assert _git(b_worktree, "status", "--porcelain") == ""

    # Story C never started: no worktree, no branch, no phase, no agent.
    assert not c_worktree.exists()
    assert branches[c1] not in _git(root, "branch", "--format=%(refname:short)").split()
    rows = {
        subtask.card_id: subtask
        for story in _load_run(root, stopped["run_id"]).stories
        for subtask in story.subtasks
    }
    assert rows[b1].status == "escalated"
    assert rows[c1].status == "pending"
    assert rows[c1].phases == []
    first_entries = read_fake_log(stopped["run_id"])
    assert first_entries  # non-vacuity: agents did run in this run
    assert c_worktree.resolve() not in _cwds(first_entries)
    assert board.show(c1, repo_dir=root).status == "todo"

    # Fix the fake, then relaunch the same command.
    review_fail_marker.unlink()
    second = run_milestone_cli(root, milestone)

    assert second.exit_code == 0, (second.output, second.exception)
    finished = _envelope(second)
    assert finished["done"] is True, finished
    assert finished["run_id"] != stopped["run_id"]
    assert finished["completed"] == [b1, c1]
    second_entries = read_fake_log(finished["run_id"])
    assert second_entries
    a_worktrees = {cli.worktree_for(root, branches[card]).resolve() for card in (a1, a2)}
    assert not (_cwds(second_entries) & a_worktrees)
    for card_id in _all_cards(milestone_board):
        assert board.show(card_id, repo_dir=root).status == "done", card_id
    assert _is_ancestor(root, branches[a2], branches[b1])
    assert _is_ancestor(root, branches[b1], branches[c1])

    # Review focus: relaunching a finished milestone drives nothing.
    third = run_milestone_cli(root, milestone)

    assert third.exit_code == 0, (third.output, third.exception)
    idle = _envelope(third)
    assert idle["done"] is True
    assert idle["completed"] == []
    assert read_fake_log(idle["run_id"]) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/e2e/test_milestone_run.py::test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it -v`
Expected: ERROR with `fixture 'review_fail_marker' not found`.

- [ ] **Step 3: Add the marker fixture**

In `tests/e2e/conftest.py`, after the `MILESTONE_PREFIX` constant, insert:

```python
FAKE_REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""Must equal `fake_claude.REVIEW_FAIL_MARKER`, which `test_fake_claude.py` pins."""
```

Append to the end of `tests/e2e/conftest.py`:

```python
@pytest.fixture
def review_fail_marker(milestone_board) -> Path:
    """Where the fake looks for branches whose review must fail.

    The repo's git common dir, which the fake reaches from any worktree's cwd
    through `git rev-parse --git-common-dir`. Inside `.git`, so it is in no
    worktree's tree and never in `git status`. The test writes it and removes it.
    """
    return milestone_board["root"] / ".git" / FAKE_REVIEW_FAIL_MARKER
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/e2e/test_milestone_run.py -v`
Expected: PASS for all three tests. The relaunch re-enters b1 through the path the spec asks planning to confirm:
- `worktree.ensure` finds the branch and the worktree already there and reuses them.
- `plan_check.find_validated_plan` looks in `<repo>/.claude/plans`, finds nothing, and so does not skip.
- `spec` and `plan` rewrite byte-identical documents, and `mark_validated` appends the same marker.
- `docs_commit` stages nothing, finds its `Plan-Hash` already on the branch and returns the same hash.
- The fake's `implement` finds nothing to commit and reports `resumed: true` (Task 2).
- `review` passes on the two tagged commits.

If the test fails anywhere outside its own test and fixture code, apply the rule from Task 3 Step 7: use superpowers:systematic-debugging, and for a fix in `orchestrate.py` or `steps/rollup.py`, write a regression test in `tests/test_orchestrate.py` or `tests/steps/test_rollup.py` first and name the fix in the commit.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_milestone_run.py
git commit -m "test(e2e): an escalated milestone stops before C and a relaunch finishes it"
```

---

### Task 5: Full-suite verification

**Files:** none changed.

- [ ] **Step 1: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, with `tests/e2e/test_milestone_run.py`, `tests/e2e/test_production_wiring.py`, `tests/e2e/test_fake_claude.py`, `tests/test_cli.py` and `tests/test_orchestrate.py` all collected and green, and the opt-in real-`claude` test deselected or skipped as before.

- [ ] **Step 2: Confirm nothing outside the planned files changed**

Run: `git diff --stat m3/task-add-the-sequential-c9037ac9..HEAD`
Expected: the only paths listed are `src/agent_manager/cli.py`, `tests/test_cli.py`, `tests/e2e/fake_claude.py`, `tests/e2e/test_fake_claude.py`, `tests/e2e/conftest.py`, `tests/e2e/test_milestone_run.py` and the spec and plan docs. The exception is an e2e-exposed fix made under Task 3 Step 7, which also lists its source file and regression test.
