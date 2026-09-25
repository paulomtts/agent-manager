<!-- task-pipeline: validated -->
# Subtask 69bcf17e: Default to four lanes and add --max-concurrent

Parent story: 1a254739 "Run a level's stories in parallel" (milestone cdbfa10d). Blocked by fe2c716b. Spec of record: `2026-09-24-parallel-stories-design.md` decision P1, plus main spec section 11 (default 4).

## Precondition

The sibling fe2c716b is present in this worktree. `orchestrate.run_milestone` takes `max_concurrent: int = 1`, rejects a value below 1 with `ValueError`, and records `models.RunConfig(max_concurrent_stories=max_concurrent)`. The store, board and repo locks (P2/P3) are also present. The main checkout at 72a6cff does not have this code, so this card has to be built on this worktree's branch. This card does not change `orchestrate.py`.

## Scope

1. **Model default.** `RunConfig.max_concurrent_stories` in `src/agent_manager/models.py` goes from `Field(default=1, gt=0)` to `Field(default=4, gt=0)`. `run_card` keeps building `models.RunConfig()` for a single card and stays single-lane. Its recorded config now reads 4, which is the documented default and changes nothing in how it runs.
2. **CLI flag.** `am run` gains `--max-concurrent N`, an int. The Option default is `None` so the code can tell whether the flag was given. The effective value is `N if given else 4`, and the help text says the default is 4. In the `--milestone` (non-dry-run) branch it is passed as `orchestrate.run_milestone(..., max_concurrent=<effective>)`. Orchestrate records it in the run's `RunConfig`, and this card does not record it separately.
3. **Validation (usage errors, exit 2, empty stdout).** `_check_run_targets` gains a `max_concurrent: int | None` parameter and raises `typer.BadParameter` (param hint `'--max-concurrent'`) when either of these holds:
   - `max_concurrent is not None and max_concurrent < 1`. The message says it must be at least 1.
   - `card is not None and max_concurrent is not None`. The message says `--max-concurrent` applies only to `--milestone`.
   Both checks run before the `HANDLED` try block, as the existing checks do. Nothing is read, dispatched or written. `min=1` on the Option is not used, so that every run-target refusal is worded and routed the same way.
4. **Dry run.** `--milestone X --dry-run --max-concurrent N` is accepted. `dry_run_payload` gains a keyword-only parameter `max_concurrent: int = 4`, and `dry_run_milestone` gains the same parameter and passes it through. The CLI passes the effective value, which is 4 when the flag is omitted. The additions to the payload are additive only:
   - a top-level `"max_concurrent": N`, and
   - on each level row, `"concurrent": min(len(level stories), N)`. This is how many of that level's stories would run together.
   `levels[].level`, `levels[].stories` and `already_done` are unchanged. The dry run still writes nothing: no Store, no run directory, no worktree, no board write.
5. **Docs.** `README.md` usage for `am run --milestone` shows `[--max-concurrent N]` (default 4) and gets one line saying `--max-concurrent 1` runs stories one at a time as before. The README refusal list gains two entries: `--max-concurrent` with `--card`, and `--max-concurrent` below 1. In the main spec section 10 (`2026-09-23-agent-manager-design.md` around line 415), the sentence that calls `--max-concurrent` deferred and runs sequential changes to say that a level's stories run on up to `--max-concurrent` lanes (default 4), with a pointer to the parallel-stories addendum P1. Nothing else in that spec changes.

## Out of scope

Everything inside `orchestrate.py`: the lane pool, the barrier, the stop Event, the escalation payload, and recording the config. Also out: the locks (P2/P3), Integrate, per-story readiness, milestone-aware `am resume`, watch/retry/cancel, cost capture, and Ctrl-C.

## Behaviour to keep

`--max-concurrent 1` behaves exactly as the sequential runner does (P1). The whole default suite stays green, including `tests/e2e/test_milestone_run.py`. Its `run_milestone_cli` fixture passes no `--max-concurrent`, so it now runs at the default of 4. If it fails, that points at a sibling's concurrency and should be reported, not worked around by pinning the fixture to 1.

## Tests

Placement rule: main spec section 14 and CLAUDE.md "tests mirror source". CLI option and validation behaviour goes in `tests/test_cli.py` through `CliRunner`, with `orchestrate.run_milestone` monkeypatched. Model defaults go in `tests/test_models.py`. Nothing here goes in `tests/e2e`, because this card has no real-harness wiring.

`tests/test_models.py` (unit, pure model):
- Update the default assertion (around line 299) from `== 1` to `== 4`. The `gt=0` refusal test stays as it is.

`tests/test_cli.py` (CLI tier, CliRunner with `run_milestone` patched through `_patch_run_milestone`):
- Omitting `--max-concurrent` passes `max_concurrent=4` to `run_milestone`. Update every test that compares the kwargs dict as a whole (for example `test_a_milestone_run_calls_run_milestone_once_with_the_run_options` and its sibling) to include `"max_concurrent": 4`.
- `--max-concurrent 2` passes `max_concurrent=2`, and `--max-concurrent 1` passes `1`.
- `--max-concurrent 0` and `--max-concurrent -1` exit 2 with empty stdout, and `run_milestone` is not called. Add these to the existing usage-error parametrization.
- `--card <id> --max-concurrent 2` exits 2 with empty stdout, and `run_card` is not called. This goes in the same parametrization.

`tests/test_cli.py` (pure function, next to the existing `dry_run_payload` tests):
- `dry_run_payload(..., max_concurrent=2)` over a level with 3 stories and a level with 1 story reports `max_concurrent == 2` and per-level `concurrent` values `[2, 1]`.
- The existing calls without `max_concurrent` still pass and report `max_concurrent == 4`.

`tests/test_cli.py` (CLI dry run, with `_dry_run` and `_forbid_writes` guards):
- `am run --milestone X --dry-run --max-concurrent 3` exits 0 and puts `max_concurrent: 3` in `data`. The forbidden-write guards do not fire, and no run directory is created.
- The same command without the flag reports `max_concurrent: 4`.

---

# Default to Four Lanes and Add --max-concurrent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make four lanes the default for milestone runs, expose `am run --max-concurrent N`, validate it as a usage error, and echo the bound in the dry-run preview.

**Architecture:** `models.RunConfig.max_concurrent_stories` defaults to 4. `cli.run` gains an `int | None` option; `None` means "not given" and resolves to `cli.DEFAULT_MAX_CONCURRENT` (4). `_check_run_targets` refuses a value below 1 and any explicit value with `--card`, before the `HANDLED` try block. The resolved value goes to `orchestrate.run_milestone(max_concurrent=...)` (already present from sibling fe2c716b, which records it in `RunConfig`) or to `dry_run_milestone`/`dry_run_payload`, which add a top-level `max_concurrent` and a per-level `concurrent`.

**Tech Stack:** Python, Typer, Pydantic, pytest with `typer.testing.CliRunner`, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-default-to-four-lanes-69bcf17e/docs/superpowers/specs/task-default-to-four-lanes-69bcf17e-design.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-default-to-four-lanes-69bcf17e`. Run every command from that directory.

## Global Constraints

- Default lane count is 4 (main spec section 11, addendum P1).
- `--max-concurrent 1` must behave exactly as the sequential runner does (P1). Levels stay barriers.
- Usage errors are `typer.BadParameter`, raised before the `HANDLED` try block: exit 2, nothing on stdout.
- Handled errors are an `ok:false` envelope at exit 3; this card adds none.
- CLI output is JSON by default (`{"ok": true, "data": ...}`), `--pretty` for humans.
- Dry-run payload additions are additive only; `levels[].level`, `levels[].stories`, `already_done` are unchanged.
- The dry run writes nothing: no Store, no run directory, no worktree, no board write.
- Do NOT modify `src/agent_manager/orchestrate.py` (owned by fe2c716b).
- The whole default suite (`uv run pytest`, including `tests/e2e/test_milestone_run.py`) stays green. If the e2e milestone test fails at the new default of 4, report it; do not pin its fixture to `--max-concurrent 1`.
- Tests: model defaults in `tests/test_models.py`; CLI option, validation and dry-run tests in `tests/test_cli.py`. Nothing in `tests/e2e`.

## Review Focus

- An explicit `--max-concurrent 4` with `--card` (a value equal to the default) must still be refused; detection is "given", not "differs from default". Pinned in Task 3's parametrization.
- `--max-concurrent=-1` (negative, `=` form so Click cannot read it as an option) must be exit 2, not reach `run_milestone`'s `ValueError` and come back as an exit-3 envelope. Pinned in Task 3.
- `--dry-run --max-concurrent 0` must be refused as a usage error too, not produce a preview with `concurrent: 0`. Pinned in Task 3.
- A bound larger than a level's size reports the level size (`min`), not the bound. Pinned in Task 4's pure test.
- The CLI's default and the model's default must not drift apart (4 in both places). Pinned in Task 2.

## File map

- `src/agent_manager/models.py:143` - `RunConfig.max_concurrent_stories` default 1 -> 4.
- `src/agent_manager/cli.py` - new `DEFAULT_MAX_CONCURRENT` constant; `dry_run_payload` (860-903) and `dry_run_milestone` (906-921) gain `max_concurrent`; `_check_run_targets` (940-968) gains two refusals; `run` (971-1065) gains `--max-concurrent` and passes it on.
- `tests/test_models.py:300` - default assertion.
- `tests/test_orchestrate.py:630` - pins the recorded config of a run that passes no `max_concurrent` (orchestrate's own default is 1, so it no longer equals `RunConfig()`). Test-only change; `orchestrate.py` is not touched.
- `tests/test_cli.py` - kwargs test (2713-2745), usage-error parametrization (2620-2650), dry-run payload tests (1055-1189), dry-run CLI tests (2358-2404), new tests.
- `README.md` - usage, refusal list, dry-run payload description, "one at a time" sentences.
- `docs/superpowers/specs/2026-09-23-agent-manager-design.md:414-421` - section 10 status sentence.

---

### Task 1: Default `RunConfig.max_concurrent_stories` to 4

**Files:**
- Modify: `src/agent_manager/models.py:143`
- Test: `tests/test_models.py:300`
- Test: `tests/test_orchestrate.py:630`

**Interfaces:**
- Consumes: nothing.
- Produces: `models.RunConfig().max_concurrent_stories == 4`.

- [ ] **Step 1: Write the failing test**

In `tests/test_models.py`, inside `test_minimal_run_needs_only_its_identity_fields`, change line 300 from:

```python
    assert run.config.max_concurrent_stories == 1
```

to:

```python
    assert run.config.max_concurrent_stories == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_minimal_run_needs_only_its_identity_fields -v`
Expected: FAIL with `assert 1 == 4`.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/models.py`, change line 143 from:

```python
    max_concurrent_stories: int = Field(default=1, gt=0)
```

to:

```python
    max_concurrent_stories: int = Field(default=4, gt=0)
```

- [ ] **Step 4: Pin the orchestrate test that compared against the old default**

`tests/test_orchestrate.py::test_subtasks_run_in_order_each_stacked_on_the_one_before` calls `run_milestone` with no `max_concurrent`, so orchestrate records `RunConfig(max_concurrent_stories=1)` (its own default). Line 630 compared that to `models.RunConfig()`, which is now 4. Change line 630 from:

```python
    assert run.config == models.RunConfig()
```

to:

```python
    assert run.config == models.RunConfig(max_concurrent_stories=1)
```

- [ ] **Step 5: Run the touched tests to verify they pass**

Run: `uv run pytest tests/test_models.py tests/test_orchestrate.py::test_subtasks_run_in_order_each_stacked_on_the_one_before tests/test_store.py -v`
Expected: PASS (the orchestrate test is skipped only if `git` or `brd` is missing).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS. If anything else compared a recorded config to `RunConfig()`, fix that assertion the same way (explicit `max_concurrent_stories=` of what the code under test records), never by changing `orchestrate.py`.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py tests/test_orchestrate.py
git commit -m "Default RunConfig.max_concurrent_stories to 4" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 2: Add `--max-concurrent` and pass it to `run_milestone`

**Files:**
- Modify: `src/agent_manager/cli.py` (new constant above `already_done_entries` at line 833; `run` at 971-1054)
- Test: `tests/test_cli.py` (2713-2745 and new tests after 2761)

**Interfaces:**
- Consumes: `orchestrate.run_milestone(milestone, *, ..., max_concurrent: int = 1)` from fe2c716b (already in `src/agent_manager/orchestrate.py:476`); `models.RunConfig` default 4 from Task 1.
- Produces: `cli.DEFAULT_MAX_CONCURRENT: int = 4`; `run` option `max_concurrent: int | None` (flag `--max-concurrent`); a local `lanes: int` in `run` equal to `DEFAULT_MAX_CONCURRENT if max_concurrent is None else max_concurrent`, used by Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, in `test_a_milestone_run_calls_run_milestone_once_with_the_run_options`, replace the expected `calls` (lines 2734-2745) with:

```python
    assert calls == [
        (
            "Milestone 3",
            {
                "repo_dir": tmp_path,
                "base_branch": "main",
                "branch_prefix": "m3",
                "commands": ["uv run pytest", "uv run ruff check"],
                "allow_no_verification": True,
                "max_concurrent": 4,
            },
        )
    ]
```

Then, directly after `test_a_milestone_run_without_verify_passes_an_empty_list_and_no_opt_out` (ends at line 2761), add:

```python
@pytest.mark.parametrize("given, passed", [("2", 2), ("1", 1), ("4", 4)])
def test_an_explicit_max_concurrent_reaches_run_milestone(
    tmp_path, monkeypatch, given, passed
):
    """P1: the flag's value is what `run_milestone` gets, and `1` is passed as
    `1`, so `--max-concurrent 1` is the sequential runner."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_run_milestone(monkeypatch, CLEAN_MILESTONE)

    result = _milestone_run(tmp_path, "--max-concurrent", given)

    assert result.exit_code == 0, result.output
    ((_, kwargs),) = calls
    assert kwargs["max_concurrent"] == passed


def test_the_cli_default_lane_count_is_the_models_default():
    """Review focus: the flag's default and the recorded model default are the
    same number, so a run with no flag records what it ran with."""
    assert cli.DEFAULT_MAX_CONCURRENT == 4
    assert models.RunConfig().max_concurrent_stories == cli.DEFAULT_MAX_CONCURRENT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "calls_run_milestone_once_with_the_run_options or explicit_max_concurrent_reaches or default_lane_count" -v`
Expected: FAIL. The kwargs test fails on the missing `"max_concurrent": 4` key; the parametrized test fails with exit code 2 (`No such option: --max-concurrent`); the default test fails with `AttributeError: module 'agent_manager.cli' has no attribute 'DEFAULT_MAX_CONCURRENT'`.

- [ ] **Step 3: Add the constant**

In `src/agent_manager/cli.py`, directly above `def already_done_entries(` (line 833), add:

```python
DEFAULT_MAX_CONCURRENT = 4
"""How many of a level's stories a milestone run drives at once when
`--max-concurrent` is not given (main spec section 11, addendum P1). It matches
`models.RunConfig.max_concurrent_stories`'s default."""


```

- [ ] **Step 4: Add the option and pass it on**

In `src/agent_manager/cli.py`, in `run`'s signature, directly after the `dry_run` option (ends at line 988), add:

```python
    max_concurrent: int | None = typer.Option(
        None,
        "--max-concurrent",
        help=(
            "With --milestone: how many of a level's stories run at once "
            f"(default {DEFAULT_MAX_CONCURRENT}). 1 runs them one at a time."
        ),
    ),
```

Directly after the existing `_check_run_targets(card=card, milestone=milestone, dry_run=dry_run)` line, add:

```python
    lanes = DEFAULT_MAX_CONCURRENT if max_concurrent is None else max_concurrent
```

In the `orchestrate.run_milestone(...)` call, after `allow_no_verification=allow_no_verification,` add a line so the call reads:

```python
            payload = orchestrate.run_milestone(
                milestone,
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "calls_run_milestone_once_with_the_run_options or explicit_max_concurrent_reaches or default_lane_count or without_verify_passes" -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS, including `tests/e2e/test_milestone_run.py`, which now runs at 4 lanes. If that e2e test fails, stop and report it with its output; do not add `--max-concurrent 1` to its fixture.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Add am run --max-concurrent, defaulting to four lanes" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 3: Refuse a bad `--max-concurrent` as a usage error

**Files:**
- Modify: `src/agent_manager/cli.py` (`_check_run_targets` at 940-968 and its call in `run`)
- Test: `tests/test_cli.py:2620-2650`

**Interfaces:**
- Consumes: `run`'s `max_concurrent: int | None` option from Task 2.
- Produces: `_check_run_targets(*, card: str | None, milestone: str | None, dry_run: bool, max_concurrent: int | None = None) -> None`, raising `typer.BadParameter` with `param_hint="'--max-concurrent'"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, extend the parametrization of `test_bad_run_targets_are_usage_errors_that_start_nothing` (lines 2620-2631) so it reads:

```python
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
        (["--milestone", "2", "--max-concurrent", "0"], "least"),
        (["--milestone", "2", "--max-concurrent=-1"], "least"),
        (["--milestone", "2", "--dry-run", "--max-concurrent", "0"], "least"),
        (["--card", SOME_CARD, "--max-concurrent", "2"], "only"),
        (["--card", SOME_CARD, "--max-concurrent", "4"], "only"),
    ],
)
```

In the same test's body, directly after `monkeypatch.setattr(cli, "dry_run_milestone", _Forbidden("dry_run_milestone"))`, add:

```python
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))
```

and update its docstring to:

```python
    """Validation happens before the `HANDLED` try block, so these are Typer's
    exit 2 and never an envelope. Nothing is dispatched: `run_card`,
    `dry_run_milestone` and `run_milestone` are all forbidden here. An explicit
    `--max-concurrent 4` with `--card` is refused even though 4 is the default:
    the check is "was it given", not "does it differ"."""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_bad_run_targets_are_usage_errors_that_start_nothing -v`
Expected: the seven existing cases PASS; the five new cases FAIL with `Failed: the milestone dry run reached cli.run_milestone`, `...cli.dry_run_milestone` or `...cli.run_card` (the `_Forbidden` guards), because nothing refuses them yet.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/cli.py`, replace `_check_run_targets` (lines 940-968) with:

```python
def _check_run_targets(
    *,
    card: str | None,
    milestone: str | None,
    dry_run: bool,
    max_concurrent: int | None = None,
) -> None:
    """Refuse a bad `--card` / `--milestone` / `--dry-run` / `--max-concurrent` combination as a usage error.

    `typer.BadParameter` is Typer's own exit 2, which `EXIT_ERROR`'s docstring
    reserves. It is raised before the `HANDLED` try block, so nothing is read
    or dispatched. A blank `--milestone` is refused here too: the census strips
    the needle, and an empty needle is a substring of every title, so on a
    one-milestone board it would silently pick that milestone.
    `--max-concurrent` is `None` when not given, so giving it with `--card` is
    refused whatever its value, the default included. The Option has no
    `min=1`, so a value below 1 is refused here, worded and routed like every
    other run-target refusal.
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
    if max_concurrent is not None and max_concurrent < 1:
        raise typer.BadParameter(
            f"--max-concurrent must be at least 1, got {max_concurrent}",
            param_hint="'--max-concurrent'",
        )
    if card is not None and max_concurrent is not None:
        raise typer.BadParameter(
            "--max-concurrent applies only to --milestone",
            param_hint="'--max-concurrent'",
        )
```

In `run`, change the call from:

```python
    _check_run_targets(card=card, milestone=milestone, dry_run=dry_run)
```

to:

```python
    _check_run_targets(
        card=card, milestone=milestone, dry_run=dry_run, max_concurrent=max_concurrent
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py::test_bad_run_targets_are_usage_errors_that_start_nothing -v`
Expected: PASS for all twelve cases.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Refuse --max-concurrent below 1 or with --card as a usage error" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 4: Echo the lane bound in the dry run

**Files:**
- Modify: `src/agent_manager/cli.py` (`dry_run_payload` 860-903, `dry_run_milestone` 906-921, the dry-run branch of `run`)
- Test: `tests/test_cli.py` (1055-1105, 1149-1168, 2378, new tests)

**Interfaces:**
- Consumes: `cli.DEFAULT_MAX_CONCURRENT` and `run`'s local `lanes` from Task 2; the refusal of values below 1 from Task 3.
- Produces: `dry_run_payload(stories, *, branch_prefix: str, base_branch: str, max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> dict[str, Any]` returning `{"max_concurrent": int, "levels": [{"level": int, "concurrent": int, "stories": [...]}], "already_done": [...]}`; `dry_run_milestone(needle, *, repo_dir: Path, branch_prefix: str, base_branch: str, max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing pure-function tests**

In `tests/test_cli.py`, update the whole-dict expectation in `test_the_dry_run_payload_lists_remaining_subtasks_on_full_list_bases` (lines 1072-1105) so it reads:

```python
    assert payload == {
        "max_concurrent": 4,
        "levels": [
            {
                "level": 0,
                "concurrent": 1,
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
```

Update the expectation in `test_a_milestone_with_nothing_left_has_no_levels_and_lists_every_story_as_done` (lines 1161-1168) so it reads:

```python
    assert payload == {
        "max_concurrent": 4,
        "levels": [],
        "already_done": [
            {"kind": "story", "id": closed.id, "title": "story 1"},
            {"kind": "story", "id": finished.id, "title": "story 2"},
            {"kind": "story", "id": empty.id, "title": "story 3"},
        ],
    }
```

Directly after `test_a_blocker_outside_the_milestone_roots_the_story_on_the_base_branch` (ends at line 1189), add:

```python
def _three_then_one() -> list[census.StoryPlan]:
    """Level 0 holds stories 1, 2 and 3; level 1 holds story 4, blocked by 1."""
    return [
        _plan_story(1, [_plan_subtask(11)]),
        _plan_story(2, [_plan_subtask(21)]),
        _plan_story(3, [_plan_subtask(31)]),
        _plan_story(4, [_plan_subtask(41)], blocked_by=[_plan_id(1)]),
    ]


@pytest.mark.parametrize(
    "bound, concurrent", [(2, [2, 1]), (1, [1, 1]), (3, [3, 1]), (10, [3, 1])]
)
def test_the_dry_run_payload_reports_the_bound_and_each_levels_concurrency(
    bound, concurrent
):
    """A level runs `min(len(level), bound)` stories together; a bound larger
    than a level reports the level's size, not the bound."""
    payload = cli.dry_run_payload(
        _three_then_one(), branch_prefix="m3", base_branch="main", max_concurrent=bound
    )

    assert payload["max_concurrent"] == bound
    assert [len(level["stories"]) for level in payload["levels"]] == [3, 1]
    assert [level["concurrent"] for level in payload["levels"]] == concurrent


def test_the_dry_run_payload_defaults_to_four_lanes():
    payload = cli.dry_run_payload(_three_then_one(), branch_prefix="m3", base_branch="main")

    assert payload["max_concurrent"] == 4
    assert [level["concurrent"] for level in payload["levels"]] == [3, 1]
```

- [ ] **Step 2: Write the failing CLI dry-run tests**

In `tests/test_cli.py`, in `test_the_milestone_dry_run_stacks_each_story_on_the_previous_ones_tip`, change line 2378 from:

```python
    assert set(data) == {"levels", "already_done"}
```

to:

```python
    assert set(data) == {"max_concurrent", "levels", "already_done"}
```

Directly after `test_the_milestone_dry_run_pretty_indents_the_same_envelope` (ends at line 2480), add:

```python
@requires_git
@requires_brd
@pytest.mark.parametrize("extra, bound", [((), 4), (("--max-concurrent", "3"), 3)])
def test_the_milestone_dry_run_echoes_the_lane_bound_and_writes_nothing(
    project, milestone_board, monkeypatch, extra, bound
):
    """`milestone_board` is three one-story levels, so each level runs one
    story whatever the bound."""
    board_before = board.roots(repo_dir=project)
    porcelain_before = _git(project, "status", "--porcelain")
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))

    result = _dry_run(project, milestone_board["milestone"], *extra)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["max_concurrent"] == bound
    assert [level["concurrent"] for level in data["levels"]] == [1, 1, 1]
    _assert_nothing_written(project, porcelain_before)
    assert board.roots(repo_dir=project) == board_before


@pytest.mark.parametrize("extra, bound", [((), 4), (("--max-concurrent", "3"), 3)])
def test_a_milestone_dry_run_passes_the_lane_bound_to_the_preview(
    tmp_path, monkeypatch, extra, bound
):
    """No git or brd needed: `dry_run_milestone` is replaced by a recorder, and
    every write path and `run_milestone` are forbidden."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(orchestrate, "run_milestone", _Forbidden("run_milestone"))
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_dry_run_milestone(needle, **kwargs):
        calls.append((needle, kwargs))
        return {"max_concurrent": kwargs["max_concurrent"], "levels": [], "already_done": []}

    monkeypatch.setattr(cli, "dry_run_milestone", fake_dry_run_milestone)

    result = _milestone_run(tmp_path, "--dry-run", *extra)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["max_concurrent"] == bound
    assert calls == [
        (
            "Milestone 3",
            {
                "repo_dir": tmp_path,
                "branch_prefix": "m3",
                "base_branch": "main",
                "max_concurrent": bound,
            },
        )
    ]
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "dry_run_payload or nothing_left or dry_run_stacks or echoes_the_lane_bound or passes_the_lane_bound" -v`
Expected: FAIL. The payload tests fail on the missing `max_concurrent`/`concurrent` keys or with `TypeError: dry_run_payload() got an unexpected keyword argument 'max_concurrent'`; the CLI tests fail on `KeyError: 'max_concurrent'` or the set comparison. (The cycle and two-blocker payload tests keep passing.)

- [ ] **Step 4: Write minimal implementation**

In `src/agent_manager/cli.py`, change `dry_run_payload`'s signature (lines 860-862) to:

```python
def dry_run_payload(
    stories: Sequence[census.StoryPlan],
    *,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
```

Append to its docstring, before the closing `"""`:

```python
    `max_concurrent` is echoed at the top, and each level row says how many of
    its stories would run together: `min(len(level), max_concurrent)`. The
    caller refuses a bound below 1 before this runs.
```

Replace its last two lines (902-903):

```python
        level_rows.append({"level": index, "stories": story_rows})
    return {"levels": level_rows, "already_done": already_done_entries(stories)}
```

with:

```python
        level_rows.append(
            {
                "level": index,
                "concurrent": min(len(level), max_concurrent),
                "stories": story_rows,
            }
        )
    return {
        "max_concurrent": max_concurrent,
        "levels": level_rows,
        "already_done": already_done_entries(stories),
    }
```

Replace `dry_run_milestone`'s signature (lines 906-908) with:

```python
def dry_run_milestone(
    needle: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, Any]:
```

and its return (lines 919-921) with:

```python
    return dry_run_payload(
        plan.stories,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        max_concurrent=max_concurrent,
    )
```

In `run`, change the dry-run call to:

```python
            payload = dry_run_milestone(
                milestone,
                repo_dir=repo_dir,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
                max_concurrent=lanes,
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "dry_run or nothing_left or blocker or echoes_the_lane_bound or passes_the_lane_bound" -v`
Expected: PASS (git/brd-marked tests are skipped only if those tools are missing).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "Echo the lane bound and per-level concurrency in the dry run" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 5: Document `--max-concurrent`

**Files:**
- Modify: `README.md` (lines 39-46, 62-65, 73-80, 100-101, 149)
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:414-421`

**Interfaces:**
- Consumes: the behaviour from Tasks 1-4 (default 4, the two refusals, `data.max_concurrent` and `levels[].concurrent`).
- Produces: documentation only.

The README sentences at lines 39-40, 100 and 149 say stories run one at a time. This card makes 4 the default, so they become false with it; they are corrected here together with the usage and refusal lines the spec names. Nothing else in the README changes.

- [ ] **Step 1: Update the milestone usage block**

In `README.md`, replace lines 39-46:

````markdown
Drive every remaining subtask of one milestone, one at a time, each on its own
local branch stacked on the branch before it:

```bash
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "uv run pytest"
```
````

with:

````markdown
Drive every remaining subtask of one milestone. A level's stories run side by
side, and each story's subtasks run one at a time, each on its own local branch
stacked on the branch before it:

```bash
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "uv run pytest" \
  [--max-concurrent N]
```

`--max-concurrent N` is how many of a level's stories run at once. It defaults
to 4. `--max-concurrent 1` runs stories one at a time, as before.
````

- [ ] **Step 2: Update the refusal list**

Replace lines 62-65:

```markdown
Some combinations are refused before anything is read: `--card` together with
`--milestone`, neither of them, a blank `--milestone`, and `--dry-run` with
`--card`. These are usage errors, like a missing `--branch-prefix`: Typer prints
the message on stderr, nothing is printed on stdout, and the exit code is 2.
```

with:

```markdown
Some combinations are refused before anything is read: `--card` together with
`--milestone`, neither of them, a blank `--milestone`, `--dry-run` with
`--card`, `--max-concurrent` with `--card`, and a `--max-concurrent` below 1.
These are usage errors, like a missing `--branch-prefix`: Typer prints the
message on stderr, nothing is printed on stdout, and the exit code is 2.
```

- [ ] **Step 3: Describe the new dry-run keys**

Replace lines 73-75:

```markdown
The preview reads the board and writes nothing: no run directory, no branch, no
worktree, no board change. It exits 0. `data.levels` is a list of
`{"level", "stories"}`. Each story is `{"story", "title", "root", "subtasks"}`,
```

with:

```markdown
The preview reads the board and writes nothing: no run directory, no branch, no
worktree, no board change. It exits 0. It takes `--max-concurrent` too, and
`data.max_concurrent` echoes it (4 when not given). `data.levels` is a list of
`{"level", "concurrent", "stories"}`, where `concurrent` is how many of that
level's stories would run at once. Each story is `{"story", "title", "root", "subtasks"}`,
```

- [ ] **Step 4: Correct the "one at a time" sentences**

Replace lines 100-101:

```markdown
Stories run one at a time, level by level, and each story's subtasks run in
order. Before the first subtask the run does `git fetch origin` once (only when
```

with:

```markdown
Levels run one after another, a level's stories run on up to `--max-concurrent`
lanes, and each story's subtasks run in order. Before the first subtask the run
does `git fetch origin` once (only when
```

In the "Not there yet" list, delete line 149:

```markdown
- Stories never run in parallel: one story at a time, even inside a level.
```

- [ ] **Step 5: Update the main spec's section 10 status sentence**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace lines 418-421:

```markdown
single-card run. Deferred: `watch`, `retry`, `cancel`, `--workflow`,
`--harness`, `--max-concurrent` (runs are sequential, one story at a time), and
a milestone-aware `resume`. See section 4 of the orchestration addendum,
`2026-09-24-orchestration-design.md`.
```

with:

```markdown
single-card run. With `--milestone`, a level's stories run on up to
`--max-concurrent` lanes (default 4); see P1 of the parallel-stories addendum,
`2026-09-24-parallel-stories-design.md`. Deferred: `watch`, `retry`, `cancel`,
`--workflow`, `--harness`, and a milestone-aware `resume`. See section 4 of the
orchestration addendum, `2026-09-24-orchestration-design.md`.
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS (docs only; confirms nothing regressed).

- [ ] **Step 7: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "Document am run --max-concurrent" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

## Spec coverage

| Spec item | Task |
|---|---|
| Scope 1, model default 4; `tests/test_models.py` default assertion | 1 |
| Scope 2, `--max-concurrent` (Option default `None`, effective 4) passed as `max_concurrent=` to `run_milestone`; whole-kwargs test gains `"max_concurrent": 4`; `2` and `1` pass through | 2 |
| Scope 3, below 1 and with `--card` are exit 2, empty stdout, nothing dispatched; no `min=1` | 3 |
| Scope 4, `dry_run_payload`/`dry_run_milestone` `max_concurrent=4`, top-level `max_concurrent`, per-level `concurrent`; existing calls report 4; CLI dry run with and without the flag; nothing written | 4 |
| Scope 5, README usage, `--max-concurrent 1` line, refusal list; main spec section 10 sentence | 5 |
| Behaviour to keep: `--max-concurrent 1` passes `1`; full suite incl. e2e green, e2e failure reported not pinned | 2 (Steps 1 and 6), every task's full-suite step |
