# Subtask 94580a42 — Drop unnecessary fixtures and collapse the `--pretty` tests

Parent story a0b987c1 "Move test_cli.py off the real board" (milestone 66ed75cd). Narrows V5 of `docs/superpowers/specs/2026-10-02-test-tier-design.md`, layered on `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14. The only file touched is `tests/test_cli.py`. No change under `src/`.

## Precondition

This subtask depends on the FakeBoard seam from bf9f2b42. That work has not landed on `master`, but this worktree already has it: `tests/conftest.py` defines `class FakeBoard` and the `fake_board` fixture, and the `cards` fixture in `tests/test_cli.py` seeds `fake_board`. If a working tree is missing `FakeBoard`, stop and report that. Do not rebuild the seam here. The line numbers in the card and in the milestone spec (`:2205,2291,2321,2459,4793`, `:1863,2767,…`) are out of date. Find tests by what they contain, not by line number.

## Scope

1. **Fixture-less tests.** The governing spec's file map (`2026-10-02-test-tier-design.md` §4) says "five" from a since-renumbered pass over `test_cli.py`; a fresh pass over this worktree's current `test_cli.py`, checking every test that takes `project`, `cards`, `milestone_board`, or `resume_board` for whether it replaces the command body completely (for example `cli.run_card` / `cli.resume_run` swapped for a recorder with `monkeypatch.setattr`, not wrapped — as opposed to tests that only swap `default_runner_factory`, `board.show`, or similar and still drive the real `run_card`/`resume_run` against the real repo/board), found exactly three such tests, and all three only use `project`/`cards` for id strings or a `--repo-dir` path:
   - `test_repeated_verify_options_reach_run_card_in_command_line_order`
   - `test_no_verify_option_means_an_empty_command_list_not_none`
   - `test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties`

   Change each one to take `tmp_path` and a literal card-id (or run-id) string, the way `test_a_stopped_card_run_is_ok_true_and_exit_zero` already does with `"cbe34d00-9d8d-4f41-9c94-f99e665771b0"`. Any `_fake_payload(card_id, cards["story"])` argument becomes a literal story id string. The implementer should do one more pass in case the worktree has changed since this spec was written, but should not spend long hunting for a specific count: convert whichever tests fit the criterion above, however many that is, and do not force-convert a test that really uses the repo or board just to hit a target number.
2. **`--pretty` collapse.** There are 8 `--pretty` CliRunner call sites: `run`, milestone `--dry-run`, `_milestone_run`, `status`, `runs`, `logs`, `resume`, and control (pause/cancel). Reduce these to two things: the existing `test_render_indents_under_pretty` (unchanged), and one new parametrized CliRunner smoke test on the cheapest command. That should be a `projection`-backed command (`status`/`runs`/`logs`), which spawns no subprocess. Remove only the `--pretty` part of each old test. When a test's `--pretty` assertion is its whole purpose, delete the test. When it also checks other behaviour, keep it and drop just the `--pretty` invocation and its asserts.
3. **Re-tier.** Every converted or new test that no longer touches git or brd must lose `@requires_git` / `@requires_brd` and any git/brd marker. Those tests now belong to the default `unit` tier.

## Observable behaviour / compatibility

- CLI behaviour does not change at all (§5). Production code is not edited.
- No assertion is lost. Every assertion removed from an old `--pretty` test must already be covered by `test_render_indents_under_pretty` (indentation and envelope rendering) or by the new smoke test (`--pretty` reaches `render` through the CLI, exits 0, and gives indented output that parses back to the same envelope as the non-pretty run). If a removed assertion checks something command-specific beyond rendering, keep it in its original test without `--pretty`.
- The `--pretty` error path must still be covered. The current control test checks a `--pretty` refusal (`no-such-run`). The new smoke test must include at least one error-envelope case (`ok: false`, non-zero exit) as well as the success case.

## Out of scope

Changing the FakeBoard seam, `board.py`, or the fixture conversion itself (bf9f2b42). Making pytest-xdist the canonical verify. Changes to the engine, checkpoint format, harness adapter contract, or `dispatch.py`'s `LauncherFn`. The e2e tier (keep its 5 tests as they are). Milestone 14 `am run --board`.

## Tests (tier per V1 placement rule)

| Test | Change | Tier |
|---|---|---|
| `test_repeated_verify_options_reach_run_card_in_command_line_order` | `tmp_path` + literal id, decorators dropped | `unit` |
| `test_no_verify_option_means_an_empty_command_list_not_none` | same | `unit` |
| `test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties` | same | `unit` |
| `test_render_indents_under_pretty` | unchanged | `unit` |
| new `test_pretty_renders_through_the_cli` (parametrized: success + error envelope, `projection`-backed command) | new | `unit` (plain `tmp_path`, no subprocess) |
| the 7 other `--pretty` call sites | `--pretty` part removed; test deleted only when that was its entire purpose | keeps its current tier (unchanged by this subtask) |

## Verification

- `uv run pytest` is green.
- V5 §6 proof: run the old and new `tests/test_cli.py` with `-k` filters for the affected tests (the verify-option tests, the pretty tests) and diff pass/fail. Outcomes must match and wall time should be lower. A green suite alone is not enough.
- Grep check: none of the converted tests, and not the new smoke test, still requests `project`, `cards`, `milestone_board`, or `resume_board`, or carries `requires_git` / `requires_brd`.

---

# Drop Unnecessary Fixtures and Collapse the `--pretty` Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move three `--verify` recorder tests in `tests/test_cli.py` off the `project`/`cards` fixtures and into the unit tier, and replace eight per-command `--pretty` CliRunner checks with the existing `render` unit test plus one parametrized `status --pretty` smoke test (success and error envelope).

**Architecture:** This is a test-only refactor of one file, `tests/test_cli.py`. Nothing under `src/` changes. In the three verify tests, `cli.run_card` is already replaced by a recorder, so the real git repo and board they build are never used. They switch to `tmp_path` plus literal ids, following the pattern in `test_a_stopped_card_run_is_ok_true_and_exit_zero`. Every command calls the same `render(envelope, pretty=pretty)`, so one CliRunner smoke test on `status` (backed by `projection`: a directory plus a SQLite projection, no subprocess) is enough to show that `--pretty` reaches `render` through the CLI. `test_render_indents_under_pretty` pins what `render` itself does.

**Tech Stack:** Python, pytest, Typer `CliRunner`, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-drop-unnecessary-94580a42/docs/superpowers/specs/task-drop-unnecessary-94580a42-design.md` (prepended above). Governing spec: `docs/superpowers/specs/2026-10-02-test-tier-design.md` V1/V5/§6.

## Global Constraints

- Only `tests/test_cli.py` is edited. No file under `src/` is changed in the final diff. Task 3 makes one temporary sabotage edit to `src/agent_manager/cli.py` and reverts it in the very next step.
- No CLI-observable behaviour change (§5).
- No assertion is lost unless an equivalent already covers it (§5).
- Tier placement (V1): unit tests in this repo are unmarked functions in `tests/test_cli.py`. There is no tier directory and no `pytestmark` in this file. `git`/`brd` tests here are gated by the module-level `requires_git` / `requires_brd` skipif decorators (`tests/test_cli.py:1193-1200`). Converted and new tests carry neither decorator and no marker.
- Do not touch `tests/conftest.py`'s `FakeBoard`/`fake_board`, `board.py`, the e2e tier, `dispatch.py`, or the engine.
- Locate tests by name or content. The line numbers below are from the worktree as of plan-writing and are only hints.
- Verification command: `uv run pytest`.

## Review Focus

1. A command forgets to pass `pretty` through to `render`. Most commands are no longer smoke-tested under `--pretty` (V5 accepts this trade). A reviewer should check that `src/agent_manager/cli.py` is unchanged, so that every `render(..., pretty=pretty)` call site (`cli.py:1189-1191, 1266-1268, 1298-1300, 1366-1368, 1620-1622, 1787-1789`) are still the code the old tests passed against. The Task 5 diff check pins this: `git diff --stat` must not list `src/`.
2. `--pretty` on an error envelope must keep the error exit code and must still parse back to the same envelope. This is covered by the `error-envelope` case of the new smoke test (Task 3).
3. A converted verify test that unexpectedly reaches the data dir or a real subprocess would make it a hidden git/brd test. Each converted test points `XDG_DATA_HOME` into `tmp_path` and replaces `run_card` completely. Task 2, Step 6 requires them to PASS (never SKIP) and stay out of the ≥0.5s durations list, and Task 5, Step 4's grep shows they request no board fixture.
4. Removing the `--pretty` half of `test_an_escalated_milestone_exits_one_with_an_ok_envelope` must not lose its exit-code check. The plain invocation still asserts `cli.EXIT_ESCALATED` and the full envelope (Task 4, Step 3).
5. Removing the control `--pretty` test must not lose the pause/cancel refusal or the `CONTROL_KEYS` checks. These are already covered by `test_a_request_to_an_unknown_run_is_refused` (parametrized pause/cancel, `UnknownRunError`, exit `EXIT_ERROR`) and `test_a_request_to_a_live_accepting_run_is_recorded_under_its_lease` (`set(data) == CONTROL_KEYS`, `command`). Task 4, Step 1 re-runs both by name.

---

### Task 1: Precondition check and the "before" baseline

**Files:**
- Read only: `tests/conftest.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `${TMPDIR:-/tmp}/am-94580a42-before.txt`, the pass/fail and duration report that Task 5 diffs against.

- [ ] **Step 1: Confirm the FakeBoard seam is present**

Run: `grep -n "class FakeBoard\|def fake_board" tests/conftest.py && grep -n "def cards(project, fake_board)" tests/test_cli.py`
Expected: three hits (`class FakeBoard`, `def fake_board`, `def cards(project, fake_board)`). If any is missing, STOP and report "FakeBoard seam (bf9f2b42) missing from this working tree". Do not rebuild it.

- [ ] **Step 2: Capture the before-baseline for every affected test**

Run:

```bash
uv run pytest tests/test_cli.py -rA -p no:randomly --durations=0 \
  -k "test_repeated_verify_options_reach_run_card_in_command_line_order or test_no_verify_option_means_an_empty_command_list_not_none or test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties or pretty or test_an_escalated_milestone_exits_one_with_an_ok_envelope or test_a_request_to_an_unknown_run_is_refused or test_a_request_to_a_live_accepting_run_is_recorded_under_its_lease or test_the_command_prints_an_ok_envelope_and_exits_zero or test_the_resume_command_prints_an_ok_envelope_and_exits_zero or test_status_prints_a_row_for_every_recorded_attempt or test_status_for_an_unknown_run_id_is_an_envelope" \
  2>&1 | tee "${TMPDIR:-/tmp}/am-94580a42-before.txt"
```

(If `-p no:randomly` errors with "unknown plugin", drop that flag. It only matters when pytest-randomly is installed.)
Expected: everything PASSED (or SKIPPED where `git`/`brd` is missing on PATH). The `pretty` tests listed are `test_render_indents_under_pretty`, `test_pretty_indents_the_same_envelope`, `test_the_milestone_dry_run_pretty_indents_the_same_envelope`, `test_status_pretty_indents_the_same_envelope`, `test_runs_pretty_indents_the_same_envelope`, `test_logs_pretty_indents_the_same_envelope`, `test_resume_pretty_indents_the_same_envelope`, and `test_pause_and_cancel_pretty_indent_the_same_envelope[pause|cancel]`. Note the total wall time on the last line.

No commit. Nothing changed.

---

### Task 2: Convert the three `--verify` recorder tests to literal ids (unit tier)

**Files:**
- Modify: `tests/test_cli.py` (`test_repeated_verify_options_reach_run_card_in_command_line_order` ~:2365-2390, `test_no_verify_option_means_an_empty_command_list_not_none` ~:2393-2409, `test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties` ~:2412-2437)

**Interfaces:**
- Consumes: `_invoke(project: Path, card_id: str, *extra: str)` (`tests/test_cli.py` ~:1897). Its first argument is only used as `--repo-dir`, so `tmp_path` works. Also `_fake_payload(card_id: str, story_id: str) -> dict[str, Any]` (~:2344) and `runner = CliRunner()` (~:1894).
- Produces: nothing new. Same three test names, unit tier.

- [ ] **Step 1: Re-check whether more tests fit the criterion**

Run: `grep -n 'setattr(cli, "run_card"\|setattr(cli, "resume_run"' tests/test_cli.py`
For each hit, read the enclosing test. A test qualifies only if all three hold: (a) it takes `project`, `cards`, `milestone_board`, or `resume_board`; (b) it replaces `run_card`/`resume_run` with a recorder or a lambda (not `_Forbidden`, and not a wrapper that calls `real_run_card`); and (c) the fixture values are used only as id strings or as a `--repo-dir` path. At plan-writing time only the three hits at ~:2379, ~:2405, and ~:2426 qualify. The other hits are already on `tmp_path` (~:4123, ~:4137, ~:5252), use `_Forbidden` (~:2657, ~:4897-4898), or wrap the real `run_card` against the real repo (~:6166, `test_a_control_and_an_escalation_follow_c6_at_the_command`). If a new hit qualifies, convert it the same way as Steps 3-5 and add its name to the Task 5 grep. If none does, record "three, per spec §Scope 1" in the commit message. Do not force-convert anything to reach five.

- [ ] **Step 2: Add the shared literal ids just above the first converted test**

Insert directly after `_fake_payload` (after the line `    }` that closes its return dict, ~:2362) and before `@requires_git` of `test_repeated_verify_options_reach_run_card_in_command_line_order`:

```python


VERIFY_CARD_ID = "cbe34d00-9d8d-4f41-9c94-f99e665771b0"
VERIFY_STORY_ID = "story-1"
"""Literal ids for the tests that replace `run_card` outright: the card is never
looked up, so no repo or board is built for it (test-tier V5)."""
```

- [ ] **Step 3: Rewrite `test_repeated_verify_options_reach_run_card_in_command_line_order`**

Replace the whole test (both decorators included) with:

```python
def test_repeated_verify_options_reach_run_card_in_command_line_order(tmp_path, monkeypatch):
    """§12's suite is the caller's to supply, and the engine runs the commands in
    sequence -- so the order the operator typed is behaviour, not decoration."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen["card_id"] = card_id
        seen.update(kwargs)
        return _fake_payload(card_id, VERIFY_STORY_ID)

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(
        tmp_path,
        VERIFY_CARD_ID,
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
    )

    assert result.exit_code == 0, result.output
    assert list(seen["commands"]) == ["uv run pytest", "uv run ruff check"]
```

- [ ] **Step 4: Rewrite `test_no_verify_option_means_an_empty_command_list_not_none`**

Replace the whole test (both decorators included) with:

```python
def test_no_verify_option_means_an_empty_command_list_not_none(tmp_path, monkeypatch):
    """`gate_context` calls `list(commands)` and `verification_gate` tells an
    empty suite apart from a missing one, so `None` here would be a crash or a
    silently different verdict."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, VERIFY_STORY_ID)

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(tmp_path, VERIFY_CARD_ID)

    assert result.exit_code == 0, result.output
    assert seen["commands"] == []
```

- [ ] **Step 5: Rewrite `test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties`**

Replace the whole test (both decorators included) with:

```python
def test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties(
    tmp_path, monkeypatch
):
    """Review Focus: one occurrence is one whole command string. The CLI does no
    word-splitting, no parsing and no validation -- whether a command is nonsense
    is the engine's business, not this layer's."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, VERIFY_STORY_ID)

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(
        tmp_path,
        VERIFY_CARD_ID,
        "--verify",
        "uv run pytest -k 'not slow'",
        "--verify",
        "",
    )

    assert result.exit_code == 0, result.output
    assert list(seen["commands"]) == ["uv run pytest -k 'not slow'", ""]
```

- [ ] **Step 6: Run the three converted tests**

Run: `uv run pytest tests/test_cli.py -v -k "test_repeated_verify_options_reach_run_card_in_command_line_order or test_no_verify_option_means_an_empty_command_list_not_none or test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties"`
Expected: 3 PASSED, no SKIPPED. They no longer depend on `git`/`brd` being on PATH, and none should appear in the `--durations` slow list (≥0.5s).

- [ ] **Step 7: Prove the converted tests can still fail (mutation check, reverted)**

In `test_repeated_verify_options_reach_run_card_in_command_line_order`, temporarily swap the two `--verify` values in the `_invoke(...)` call (`"uv run ruff check"` first, `"uv run pytest"` second). Run:
`uv run pytest tests/test_cli.py -v -k test_repeated_verify_options_reach_run_card_in_command_line_order`
Expected: FAIL on `assert list(seen["commands"]) == ["uv run pytest", "uv run ruff check"]`. This shows the recorder really sees the CLI's arguments. Then restore the original order exactly as in Step 3 and re-run. Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): run the --verify recorder tests on tmp_path and literal ids

run_card is replaced outright in these three tests, so the real repo and
board the project/cards fixtures built were never read. They move to the
unit tier (test-tier V5). A fresh pass found three such tests, not five."
```

---

### Task 3: Add the parametrized `--pretty` smoke test on `status` (unit tier)

**Files:**
- Modify: `tests/test_cli.py`. Insert the new test exactly where `test_status_pretty_indents_the_same_envelope` now starts (~:3532), directly before it. Task 4 deletes the old test.
- Temporary, reverted in the same task: `src/agent_manager/cli.py:178`

**Interfaces:**
- Consumes: the `projection` fixture (`tests/test_cli.py` ~:3345-3356: `tmp_path/"recorded"` plus `XDG_DATA_HOME`), `_record(root: Path, run_id: str, *, started_at: datetime, status: str = "done", with_phases: bool = True) -> None` (~:3370), `RECORDED_AT` (~:3434), `runner`, `cli.EXIT_ERROR`.
- Produces: `test_pretty_renders_through_the_cli[ok-envelope]` and `test_pretty_renders_through_the_cli[error-envelope]`.

- [ ] **Step 1: Write the smoke test**

Insert before `def test_status_pretty_indents_the_same_envelope(projection):`:

```python
@pytest.mark.parametrize(
    "run_id, exit_code, ok",
    [
        ("20260923T090000Z-cbe34d00", 0, True),
        ("no-such-run", cli.EXIT_ERROR, False),
    ],
    ids=["ok-envelope", "error-envelope"],
)
def test_pretty_renders_through_the_cli(projection, run_id, exit_code, ok):
    """Test-tier V5: the one CliRunner check of `--pretty`. Every command hands its
    envelope to the same `render(..., pretty=pretty)`, and
    `test_render_indents_under_pretty` pins what `render` does, so this checks
    only that the flag reaches it through Typer -- for an ok envelope and for a
    refusal -- on `status`, the cheapest command (no git, no brd, no subprocess)."""
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)
    argv = ["status", run_id, "--repo-dir", str(projection)]

    plain = runner.invoke(cli.app, argv)
    pretty = runner.invoke(cli.app, [*argv, "--pretty"])

    assert plain.exit_code == exit_code, plain.output
    assert pretty.exit_code == exit_code, pretty.output
    assert "\n" not in plain.stdout.strip()
    assert "\n  " in pretty.stdout
    envelope = json.loads(pretty.stdout)
    assert envelope == json.loads(plain.stdout)
    assert envelope["ok"] is ok
```

- [ ] **Step 2: RED — sabotage `--pretty` in `render` and watch the smoke test fail**

Temporarily change `src/agent_manager/cli.py:178` from

```python
    if pretty:
```

to

```python
    if False:
```

Run: `uv run pytest tests/test_cli.py -v -k test_pretty_renders_through_the_cli`
Expected: 2 FAILED, both on `assert "\n  " in pretty.stdout`. This shows both cases detect a `--pretty` that never reaches indentation.

- [ ] **Step 3: Revert the sabotage**

Run: `git checkout -- src/agent_manager/cli.py && git diff --stat -- src/`
Expected: empty `git diff --stat` output. `src/` is untouched again.

- [ ] **Step 4: GREEN — run the smoke test**

Run: `uv run pytest tests/test_cli.py -v -k test_pretty_renders_through_the_cli`
Expected: `test_pretty_renders_through_the_cli[ok-envelope] PASSED` and `test_pretty_renders_through_the_cli[error-envelope] PASSED`, neither in the ≥0.5s durations list.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): one parametrized --pretty smoke on status, ok and error envelopes

Test-tier V5: the single CliRunner check that --pretty reaches render, on a
projection-backed command with no subprocess."
```

---

### Task 4: Remove the seven redundant `--pretty` call sites

**Files:**
- Modify: `tests/test_cli.py`. Delete `test_pretty_indents_the_same_envelope` (~:1935-1943), `test_the_milestone_dry_run_pretty_indents_the_same_envelope` (~:2843-2855), `test_status_pretty_indents_the_same_envelope` (~:3532-3545), `test_runs_pretty_indents_the_same_envelope` (~:3609-3617), `test_logs_pretty_indents_the_same_envelope` (~:3785-3797), `test_resume_pretty_indents_the_same_envelope` (~:4834-4846), and `test_pause_and_cancel_pretty_indent_the_same_envelope` (~:5582-5604). Trim `test_an_escalated_milestone_exits_one_with_an_ok_envelope` (~:3262-3275).

**Interfaces:**
- Consumes: `test_pretty_renders_through_the_cli` (Task 3) and `test_render_indents_under_pretty` (`tests/test_cli.py:64`, unchanged).
- Produces: nothing new.

Coverage map. Each deleted assertion and the test that already covers it:

| Removed | What it asserted | Already covered by |
|---|---|---|
| `test_pretty_indents_the_same_envelope` (run) | exit 0, indented, `status == "done"` | `test_the_command_prints_an_ok_envelope_and_exits_zero` (exit 0, `ok`, `status == "done"`), plus the smoke test and `test_render_indents_under_pretty` for indentation |
| `test_the_milestone_dry_run_pretty_indents_the_same_envelope` | exit 0, indented, same envelope as plain | smoke `ok-envelope` (indented, same envelope as plain); plain dry-run output pinned by `test_a_title_substring_names_the_same_milestone_as_its_id` and the other dry-run tests |
| `test_status_pretty_indents_the_same_envelope` | same | smoke `ok-envelope` (identical command and run) |
| `test_runs_pretty_indents_the_same_envelope` | same | smoke + `test_runs_lists_the_projects_history_newest_first` |
| `test_logs_pretty_indents_the_same_envelope` | same | smoke + `test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase` |
| `test_resume_pretty_indents_the_same_envelope` | exit 0, indented, `status == "done"` | `test_the_resume_command_prints_an_ok_envelope_and_exits_zero` + smoke |
| `test_pause_and_cancel_pretty_indent_the_same_envelope` | ok: exit 0, `"\n  "`, `CONTROL_KEYS`, `command`; refusal: `EXIT_ERROR`, `"\n  "`, `ok False`, `UnknownRunError` | `test_a_request_to_a_live_accepting_run_is_recorded_under_its_lease[pause|cancel]` (exit 0, `set(data) == CONTROL_KEYS`, `command`), `test_a_request_to_an_unknown_run_is_refused[pause|cancel]` (`EXIT_ERROR`, `ok False`, `UnknownRunError`), smoke `error-envelope` (`"\n  "` on a refusal with error exit) |
| `--pretty` half of `test_an_escalated_milestone_exits_one_with_an_ok_envelope` | pretty exit `EXIT_ESCALATED`, indented, same envelope | plain half kept (exit `EXIT_ESCALATED`, full envelope); exit code is computed before `render` (`cli.py` call sites pass only `pretty=` to `render`), smoke covers indentation |

- [ ] **Step 1: Confirm the covering tests pass before deleting anything**

Run: `uv run pytest tests/test_cli.py -v -k "test_the_command_prints_an_ok_envelope_and_exits_zero or test_a_title_substring_names_the_same_milestone_as_its_id or test_runs_lists_the_projects_history_newest_first or test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase or test_the_resume_command_prints_an_ok_envelope_and_exits_zero or test_a_request_to_a_live_accepting_run_is_recorded_under_its_lease or test_a_request_to_an_unknown_run_is_refused or test_pretty_renders_through_the_cli or test_render_indents_under_pretty"`
Expected: all PASSED (any `@requires_git`/`@requires_brd` test may be SKIPPED only if git/brd is absent, and it must match its Task 1 baseline outcome).

- [ ] **Step 2: Delete the six whole-purpose `--pretty` tests**

Delete each of these blocks completely, decorators included, along with one of the two blank lines that separated it from the next test, so that two blank lines remain between top-level definitions:

```python
@requires_git
@requires_brd
def test_pretty_indents_the_same_envelope(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"], "--pretty")

    assert result.exit_code == 0
    assert "\n" in result.stdout.strip()
    assert json.loads(result.stdout)["data"]["status"] == "done"
```

```python
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

```python
def test_status_pretty_indents_the_same_envelope(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    plain = runner.invoke(
        cli.app, ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)]
    )
    pretty = runner.invoke(
        cli.app,
        ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection), "--pretty"],
    )

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)
```

```python
def test_runs_pretty_indents_the_same_envelope(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    plain = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])
    pretty = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection), "--pretty"])

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)
```

```python
def test_logs_pretty_indents_the_same_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    plain = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )
    pretty = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection), "--pretty"]
    )

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)
```

```python
@requires_git
@requires_brd
def test_resume_pretty_indents_the_same_envelope(project, cards, monkeypatch):
    run_id = _crash_pygents(project, cards, "plan")
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(project), "--pretty"]
    )

    assert result.exit_code == 0, result.output
    assert "\n" in result.stdout.strip()
    assert json.loads(result.stdout)["data"]["status"] == "done"
```

- [ ] **Step 3: Delete the control `--pretty` test**

Delete this block completely:

```python
@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_pause_and_cancel_pretty_indent_the_same_envelope(projection, monkeypatch, command):
    """Spec test 9."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(projection)

    result = _invoke_control(projection, command, CONTROL_RUN_ID, "--pretty")

    assert result.exit_code == 0, result.output
    assert "\n  " in result.stdout
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert set(envelope["data"]) == CONTROL_KEYS
    assert envelope["data"]["command"] == command

    refusal = _invoke_control(projection, command, "no-such-run", "--pretty")

    assert refusal.exit_code == cli.EXIT_ERROR, refusal.output
    assert "\n  " in refusal.stdout
    refused = json.loads(refusal.stdout)
    assert refused["ok"] is False
    assert refused["error"]["type"] == "UnknownRunError"
```

- [ ] **Step 4: Trim `test_an_escalated_milestone_exits_one_with_an_ok_envelope` to its plain half**

Replace:

```python
    plain = _milestone_run(tmp_path)
    pretty = _milestone_run(tmp_path, "--pretty")

    assert plain.exit_code == cli.EXIT_ESCALATED, plain.output
    assert json.loads(plain.stdout) == cli.ok_envelope(ESCALATED_MILESTONE)
    assert pretty.exit_code == cli.EXIT_ESCALATED, pretty.output
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)
```

with:

```python
    result = _milestone_run(tmp_path)

    assert result.exit_code == cli.EXIT_ESCALATED, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(ESCALATED_MILESTONE)
```

Its decorators and docstring stay as they are. It is already an unmarked test on `tmp_path`, so its tier does not change.

- [ ] **Step 5: Check that only the smoke test still passes `--pretty`**

Run: `grep -n '"--pretty"' tests/test_cli.py`
Expected: exactly one hit, the `[*argv, "--pretty"]` line inside `test_pretty_renders_through_the_cli`.
Run: `grep -n "def test_\w*pretty" tests/test_cli.py`
Expected: exactly `test_render_indents_under_pretty` and `test_pretty_renders_through_the_cli`.

- [ ] **Step 6: Run the touched area**

Run: `uv run pytest tests/test_cli.py -v -k "pretty or test_an_escalated_milestone_exits_one_with_an_ok_envelope or test_a_request_to_an_unknown_run_is_refused or test_a_request_to_a_live_accepting_run_is_recorded_under_its_lease"`
Expected: all PASSED. `test_render_indents_under_pretty`, both `test_pretty_renders_through_the_cli` cases, the trimmed escalated-milestone test, and the control tests all pass, with no errors from fixtures that are now unused.

- [ ] **Step 7: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): collapse the per-command --pretty tests (test-tier V5)

Every command passes its envelope to the same render(..., pretty=pretty).
test_render_indents_under_pretty and test_pretty_renders_through_the_cli
(ok and error envelope) cover that path. The per-command copies are gone;
the escalated-milestone test keeps its plain-run assertions."
```

---

### Task 5: Full verification and the V5 §6 before/after proof

**Files:**
- Read only: `tests/test_cli.py`

**Interfaces:**
- Consumes: `${TMPDIR:-/tmp}/am-94580a42-before.txt` (Task 1).
- Produces: `${TMPDIR:-/tmp}/am-94580a42-after.txt`.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: green. The pass count changes by +2 smoke cases, −8 deleted `--pretty` cases (six single tests plus two control params), and 0 for the three converted tests, compared with a run on the base commit.

- [ ] **Step 2: Capture the after-run with the same `-k` filter**

```bash
uv run pytest tests/test_cli.py -rA -p no:randomly --durations=0 \
  -k "test_repeated_verify_options_reach_run_card_in_command_line_order or test_no_verify_option_means_an_empty_command_list_not_none or test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties or pretty or test_an_escalated_milestone_exits_one_with_an_ok_envelope or test_a_request_to_an_unknown_run_is_refused or test_a_request_to_a_live_accepting_run_is_recorded_under_its_lease or test_the_command_prints_an_ok_envelope_and_exits_zero or test_the_resume_command_prints_an_ok_envelope_and_exits_zero or test_status_prints_a_row_for_every_recorded_attempt or test_status_for_an_unknown_run_id_is_an_envelope" \
  2>&1 | tee "${TMPDIR:-/tmp}/am-94580a42-after.txt"
```

(Drop `-p no:randomly` if Task 1 dropped it.)

- [ ] **Step 3: Diff pass/fail outcomes**

```bash
diff <(grep -E '^(PASSED|FAILED|SKIPPED|ERROR) ' "${TMPDIR:-/tmp}/am-94580a42-before.txt" | sort) \
     <(grep -E '^(PASSED|FAILED|SKIPPED|ERROR) ' "${TMPDIR:-/tmp}/am-94580a42-after.txt" | sort)
```

Expected diff, and nothing else:
- `<` lines only for the eight removed cases: `test_pretty_indents_the_same_envelope`, `test_the_milestone_dry_run_pretty_indents_the_same_envelope`, `test_status_pretty_indents_the_same_envelope`, `test_runs_pretty_indents_the_same_envelope`, `test_logs_pretty_indents_the_same_envelope`, `test_resume_pretty_indents_the_same_envelope`, `test_pause_and_cancel_pretty_indent_the_same_envelope[pause]`, `test_pause_and_cancel_pretty_indent_the_same_envelope[cancel]`. All were PASSED (or SKIPPED) before.
- `>` lines only for `PASSED tests/test_cli.py::test_pretty_renders_through_the_cli[ok-envelope]` and `[error-envelope]`.
- The three verify tests, `test_render_indents_under_pretty`, the trimmed escalated-milestone test, and every coverage-map test are identical PASSED on both sides. The only allowed exception: a verify test that was SKIPPED before because git/brd was missing and is PASSED now. That is the point of re-tiering, so note it.

Then compare the final `==== N passed ... in X.XXs ====` lines in the two files. The after wall time must be lower than the before wall time. Record both numbers in the final report.

- [ ] **Step 4: Grep check that converted and new tests use no board fixture or real-subprocess decorator**

```bash
for t in test_repeated_verify_options_reach_run_card_in_command_line_order \
         test_no_verify_option_means_an_empty_command_list_not_none \
         test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties \
         test_pretty_renders_through_the_cli; do
  grep -n -B4 -A3 "def $t" tests/test_cli.py
done
```

Expected: none of the printed signature lines contains `project`, `cards`, `milestone_board`, or `resume_board`, and none of the decorator lines above them is `@requires_git`, `@requires_brd`, `@pytest.mark.git`, or `@pytest.mark.brd`. (`test_pretty_renders_through_the_cli` shows only its `@pytest.mark.parametrize`.)

- [ ] **Step 5: Confirm production code is untouched**

Run: `git diff --stat $(git merge-base HEAD m15/task-convert-cards-milestone-bf9f2b42) -- src/ tests/conftest.py`
Expected: empty output.

No commit. Nothing changed in this task.
