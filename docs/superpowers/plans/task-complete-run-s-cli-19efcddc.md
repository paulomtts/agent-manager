<!-- task-pipeline: validated -->
# Card 19efcddc — Complete `run`'s CLI surface: `--verify` and a required `--branch-prefix`

Narrows R3 of `docs/superpowers/specs/2026-09-23-real-harness-design.md` ("The CLI carries what a run needs") to the Typer layer of `src/agent_manager/cli.py`. R3 amends the main design spec (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`), which remains the source of truth for everything this card does not touch.

## Why

`run_card` (cli.py:633) and `resume_run` (cli.py:946) already take `commands: Sequence[str]` and already forward it twice — to `engine.run_subtask(..., commands=commands, ...)` and to `gate_context(commands, allow_no_verification)` (cli.py:710-713 and cli.py:1031-1034). Nothing on the command line can reach that parameter today, so every CLI-driven run sees an empty suite and §12's gate has nothing to check. Separately, `--branch-prefix` defaults to `"m1"` (cli.py:769-771, and `run_card`'s own `branch_prefix: str = "m1"` at cli.py:638), which was scaffolding for building milestone 1 and now silently mislabels branches for every other milestone.

## Scope

Three changes, all in `src/agent_manager/cli.py` plus `README.md`, plus tests in `tests/test_cli.py`.

1. **`--verify` on `am run` and `am resume`.** A repeatable `typer.Option` typed `list[str]` (empty by default, so a run with no suite stays legal and lands on the existing `--allow-no-verification` path). Each occurrence contributes one whole shell command string; the command is not split, parsed or deduplicated. The list is handed to `run_card(..., commands=...)` / `resume_run(..., commands=...)` **in command-line order** — ordering is observable because `gate_context` copies it straight into `suite_cmds` (cli.py:626) and the engine runs the commands in sequence.
2. **`--branch-prefix` becomes required on `am run`.** `typer.Option(..., "--branch-prefix", help=...)`. The `"m1"` default is deleted from the Typer signature and from `run_card`'s keyword default at cli.py:638, so no caller inherits the leftover; the existing `tests/test_cli.py` call sites that rely on the default (`cli.run_card(...)` at roughly lines 1086, 1104, 1127, 1143, 1220 and their neighbours) must pass `branch_prefix=` explicitly.
3. **`README.md` gains a Usage section** between the intro and `## Develop`, showing `am run --card <id> --branch-prefix <prefix> --verify "<cmd>" --verify "<cmd>"` and `am resume <RUN_ID> --verify "<cmd>"`, and noting that `--pretty` indents the JSON envelope.

Everything else about these commands is unchanged: the `brd`-style envelope (`ok_envelope` / `error_envelope`), `--pretty`, exit codes (`0`, `EXIT_ESCALATED`, `EXIT_ERROR`), and the payload dicts returned by `run_card` and `resume_run`. `resume` still takes no `--base-branch` and no `--branch-prefix`: both were decided when the run started and are recorded on the `SubtaskRun` (cli.py:1085-1088). `--verify` *is* offered on `resume` for the symmetric reason the docstring at cli.py:964-969 already gives — `models.RunConfig` carries neither the suite commands nor the opt-out, so a resume must be told the same things a fresh `run` was told. No new field is grown onto `models.RunConfig`.

## Observable behaviour

- `am run --card C --branch-prefix m2 --verify "uv run pytest" --verify "uv run ruff check"` → `run_card` receives `commands=["uv run pytest", "uv run ruff check"]`, that exact list reaches `engine.run_subtask`, and `gate_context` reports `suite_cmds` equal to it with `allow_no_verification` false, `caller_provided` false, `provided_verification` `None`.
- `am run` with no `--verify` → `commands` is empty; behaviour is byte-for-byte what it is today.
- `am run` without `--branch-prefix` → Typer's own missing-option usage error on stderr and **exit code 2**. This is deliberately *not* the `ok: false` envelope: `HANDLED` (cli.py:744) covers program-level refusals, while a missing required option never enters the `try` block. Exit 2 stays distinct from `EXIT_ERROR`.
- `am resume RUN_ID --verify "uv run pytest"` → `resume_run` receives `commands=["uv run pytest"]`; the envelope still carries `resumed_from` and `discarded_attempts` unchanged.
- The success envelope for `run` remains `{"ok": true, "data": {...}}` with data keys exactly `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`.

## Error paths

- Missing `--branch-prefix`: Typer usage error, exit 2, nothing written — no run id minted, no run directory, because `run_card` is never called.
- `--verify` with an empty-string value: accepted verbatim and passed through. This card adds no validation of command text; whether an empty or nonsense command is a failure is the engine's and the gate's business, and inventing a CLI-level rejection here would be scope the findings do not support.
- Every pre-existing failure mode (`ParentlessCardError`, `board.BoardError`, `WorkflowLoadError`, `EngineError`, `ValueError`, `RepoDirError`, `UnknownRunError`, `NotResumableError`) keeps its current envelope and `EXIT_ERROR`.

## Tests

Per the test-placement rule — main spec section 14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`): pure functions get unit tests; steps run against temp git repos and a temp `brd` board; adapters are asserted purely with injected launchers; the engine is driven with fake adapters; end-to-end is one slow opt-in test excluded from the default suite. Every test below is a **CLI tier** test in `tests/test_cli.py`, driven through `CliRunner` (`runner`, tests/test_cli.py:1157) with the `project` / `cards` fixtures (1012 / 1047) and a faked `cli.default_runner_factory` or a monkeypatched `cli.run_card`. **No test in this card belongs to the adapter, engine or end-to-end tier**: nothing here launches a real harness, and the card's text contains no e2e work (R4's fake-claude wiring and e2e tests are explicitly out of scope).

New:

1. `run` with two `--verify` values passes both to `run_card` as `commands` **in order** — monkeypatch `cli.run_card` (or `cli.engine.run_subtask`) and capture the keyword.
2. `run` with no `--verify` passes an empty `commands`, and `gate_context` still reports `suite_cmds == []` — guards the default against a `None`-vs-`[]` regression.
3. `run` without `--branch-prefix` exits 2 and prints a usage error, not a JSON envelope.
4. `run` with `--branch-prefix m2` puts that prefix in the payload's `branch` (proves the value is still threaded, now that the default is gone).
5. The `run` success envelope is `{"ok": true, "data": {...}}` with exactly the eleven data keys listed above — a shape-freeze test, asserted as a key-set equality.
6. `resume RUN_ID --verify ...` passes the commands to `resume_run`, placed beside the existing resume tests at tests/test_cli.py:2553-2590 and patching `cli.default_runner_factory` the way they do.

Updated (these break the moment `--branch-prefix` is required, and are part of this card):

7. The `_invoke` helper at tests/test_cli.py:1160-1171 adds `--branch-prefix` to its argv.
8. The standalone `runner.invoke(cli.app, ["run", ...])` at tests/test_cli.py:1444-1453 adds `--branch-prefix`.
9. Every direct `cli.run_card(...)` call that relied on the `"m1"` default now passes `branch_prefix=` explicitly.

## Verification

`uv run pytest` (full suite). No typecheck step, no lint step in this repo.

## Out of scope

Milestone orchestration, parallel stories, `integrate`, non-Claude harnesses, per-role `--harness`, the addendum's section 4 deferrals (`Card`/`CardNode.blocked_by`, `Store` thread-safety, `board.set_status` ancestor rollup), the sibling cards' brief composition (a1af1e04) and `prompt.txt` writing (c0a4bc75), and R4's fake-claude production-wiring and end-to-end tests.

---

# Complete `run`'s CLI surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `am run` and `am resume` a repeatable `--verify` option that reaches the already-wired `commands=` parameter, and make `am run --branch-prefix` a required option with the leftover `"m1"` default deleted.

**Architecture:** Four changes, all at the Typer layer of `src/agent_manager/cli.py` plus `README.md`. `run_card` and `resume_run` already accept `commands: Sequence[str]` and already forward it to `engine.run_subtask` and `gate_context`; this card only supplies it from the command line. `--branch-prefix` loses its default in both the Typer signature and `run_card`'s keyword, which forces every existing test call site to name it. No payload, envelope or exit code changes.

**Tech Stack:** Python 3, Typer (with `typer.testing.CliRunner`), Pydantic models for boundary state, `uv` for packaging, `pytest`.

**Spec:** `docs/superpowers/specs/task-complete-run-s-cli-19efcddc-design.md` (reproduced verbatim above).

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` — this card touches only `src/agent_manager/cli.py`, `tests/test_cli.py` and `README.md`.
- CLI output stays the `brd` envelope: `{"ok": true, "data": ...}` / `{"ok": false, "error": {...}}`, one line of JSON by default, indented under `--pretty`.
- Exit codes are unchanged: `0` on success, `cli.EXIT_ESCALATED` on an escalated walk, `cli.EXIT_ERROR` for anything in `HANDLED`. A missing required option is Typer's own usage error at **exit 2**, which is deliberately not an envelope.
- `run_card` and `resume_run` payload shapes do not change. The `run` payload keys stay exactly: `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`.
- No new field is grown onto `models.RunConfig`. `resume` gets no `--base-branch` and no `--branch-prefix`.
- `--verify` values are passed through verbatim: not split, not parsed, not deduplicated, not validated. Order is preserved.
- Every test in this card is a **CLI tier** test in `tests/test_cli.py` (main spec section 14, `docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`). No real harness process is ever launched; the agent runner is faked via `cli.default_runner_factory` or `cli.run_card` is monkeypatched. Nothing in this card belongs to the adapter, engine or opt-in end-to-end tier.
- Verification is `uv run pytest` (full suite). There is no lint and no typecheck command in this repo.

## Review Focus

- **`--verify` with an empty-string value** (`--verify ""`) must be accepted verbatim and arrive as `[""]`, not be dropped or rejected — the CLI adds no command-text validation. Test in Task 1.
- **No `--verify` at all** must produce `commands == []`, a real empty list and never `None`, because `gate_context` calls `list(commands)` and `verification_gate` distinguishes an empty suite from a missing one. Test in Task 1.
- **A `--verify` value containing spaces and quotes** (`"uv run pytest -k 'not slow'"`) must stay one single list element — the option must not word-split. Test in Task 1.
- **`--branch-prefix` omitted** must be Typer's usage error at exit 2 with nothing written (no run id minted, no run directory), not an `ok: false` envelope at `EXIT_ERROR`. Test in Task 2.
- **The success envelope's key set** must stay exactly the eleven documented keys after the signature churn — asserted as key-set equality, so an accidentally added or renamed key fails loudly. Test in Task 2.

---

### Task 1: `--verify` on `am run`

**Files:**
- Modify: `src/agent_manager/cli.py:760-793` (the `run` Typer command)
- Test: `tests/test_cli.py` (append after `test_allow_no_verification_flips_the_gate_the_cli_supplies_arguments_for`, which ends at line 1591)

**Interfaces:**
- Consumes: `cli.run_card(card_id, *, repo_dir, base_branch, branch_prefix, allow_no_verification, commands, runner_factory, clock)` — already accepts `commands: Sequence[str] = ()` at cli.py:640 and forwards it to `engine.run_subtask` and `gate_context`. Test helpers `_invoke(project, card_id, *extra)` (tests/test_cli.py:1160), `fake_runner(seen=None, fail=None)` (1061), fixtures `project` (1011) and `cards` (1046).
- Produces: a `verify: list[str]` Typer option named `--verify` on the `run` command, passed as `commands=list(verify)` to `run_card`. Task 3 mirrors the same option on `resume`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`, immediately after `test_allow_no_verification_flips_the_gate_the_cli_supplies_arguments_for` (which ends at line 1591) and before the `projection` fixture at line 1594:

```python
def _fake_payload(card_id: str, story_id: str) -> dict[str, Any]:
    """The exact `run_card` payload shape, for tests that replace `run_card`.

    Spelled out rather than built from a loop so that a key this program stops
    returning shows up here as a diff, not as a silently absent assertion.
    """
    return {
        "run_id": "20260923T140506Z-cbe34d00",
        "card_id": card_id,
        "story_id": story_id,
        "branch": "m2/task-fake-cbe34d00",
        "base_branch": "main",
        "worktree": "/tmp/agent-manager-fake-worktree",
        "status": "done",
        "failed_phase": None,
        "detail": None,
        "skipped": [],
        "warnings": [],
    }


@requires_git
@requires_brd
def test_repeated_verify_options_reach_run_card_in_command_line_order(
    project, cards, monkeypatch
):
    """§12's suite is the caller's to supply, and the engine runs the commands in
    sequence -- so the order the operator typed is behaviour, not decoration."""
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen["card_id"] = card_id
        seen.update(kwargs)
        return _fake_payload(card_id, cards["story"])

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(
        project,
        cards["subtask"],
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
    )

    assert result.exit_code == 0
    assert list(seen["commands"]) == ["uv run pytest", "uv run ruff check"]


@requires_git
@requires_brd
def test_no_verify_option_means_an_empty_command_list_not_none(project, cards, monkeypatch):
    """`gate_context` calls `list(commands)` and `verification_gate` tells an
    empty suite apart from a missing one, so `None` here would be a crash or a
    silently different verdict."""
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, cards["story"])

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == 0
    assert seen["commands"] == []


@requires_git
@requires_brd
def test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties(
    project, cards, monkeypatch
):
    """Review Focus: one occurrence is one whole command string. The CLI does no
    word-splitting, no parsing and no validation -- whether a command is nonsense
    is the engine's business, not this layer's."""
    seen: dict[str, Any] = {}

    def fake_run_card(card_id, **kwargs):
        seen.update(kwargs)
        return _fake_payload(card_id, cards["story"])

    monkeypatch.setattr(cli, "run_card", fake_run_card)
    result = _invoke(
        project,
        cards["subtask"],
        "--verify",
        "uv run pytest -k 'not slow'",
        "--verify",
        "",
    )

    assert result.exit_code == 0
    assert list(seen["commands"]) == ["uv run pytest -k 'not slow'", ""]


@requires_git
@requires_brd
def test_verify_values_reach_the_gate_context_through_the_real_run_card(
    project, cards, monkeypatch
):
    """The whole chain, not just the call: `--verify` -> `run_card` ->
    `gate_context` -> the context `builtin/task.yaml` binds its gates out of."""
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner(seen))

    result = _invoke(
        project,
        cards["subtask"],
        "--verify",
        "uv run pytest",
        "--verify",
        "uv run ruff check",
    )

    assert result.exit_code == 0
    _phase, context = seen[0]
    assert context["suite_cmds"] == ["uv run pytest", "uv run ruff check"]
    assert context["allow_no_verification"] is False
    assert context["caller_provided"] is False
    assert context["provided_verification"] is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "verify" -v`

Expected: the four new tests FAIL. Three of them pass `--verify`, which is not a known option yet, so `CliRunner` returns exit code 2 and `result.exit_code == 0` fails. `test_no_verify_option_means_an_empty_command_list_not_none` passes no option at all and fails differently — with `KeyError: 'commands'`, because the command does not hand `run_card` that keyword today. Pre-existing `verify`-matching tests (`test_allow_no_verification_flips_the_gate_the_cli_supplies_arguments_for`, `test_resume_passes_its_own_allow_no_verification_into_the_gate_context`) still PASS.

- [ ] **Step 3: Add the option to the `run` command**

In `src/agent_manager/cli.py`, in the `run` command signature (currently cli.py:760-778), insert the `verify` option between `allow_no_verification` and `pretty`:

```python
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
```

and pass it in the body (currently cli.py:781-787). `list(verify)` is deliberate: click hands a repeated option back as a tuple, and `commands` is documented as an ordered list.

```python
        payload = run_card(
            card,
            repo_dir=repo_dir,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            allow_no_verification=allow_no_verification,
            commands=list(verify),
        )
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "verify" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no new failures.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add a repeatable --verify option to am run"
```

---

### Task 2: `--branch-prefix` becomes required on `am run`

**Files:**
- Modify: `src/agent_manager/cli.py:638` (`run_card`'s keyword default) and `src/agent_manager/cli.py:769-771` (the Typer option)
- Test: `tests/test_cli.py` — three new tests, plus the `_invoke` helper at 1160-1171, the standalone invoke at 1444-1453, and every direct `cli.run_card(...)` call

**Interfaces:**
- Consumes: `cli.run_card` as left by Task 1, `dag.task_branch(prefix, card)` (used at tests/test_cli.py:1112), `board.show(card_id, repo_dir=...)`, `_invoke` (tests/test_cli.py:1160).
- Produces: `run_card(card_id, *, repo_dir: Path, branch_prefix: str, base_branch: str = "master", allow_no_verification: bool = False, commands: Sequence[str] = (), runner_factory: RunnerFactory | None = None, clock: Callable[[], datetime] = _utcnow)` — `branch_prefix` is now a required keyword with no default. `_invoke` now always sends `--branch-prefix m1`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`, after the tests Task 1 added (i.e. after `test_verify_values_reach_the_gate_context_through_the_real_run_card`, still before the `projection` fixture):

```python
@requires_git
@requires_brd
def test_run_without_branch_prefix_is_a_usage_error_not_an_envelope(
    project, cards, monkeypatch
):
    """A missing required option never enters the `HANDLED` try block, so it is
    Typer's own usage error at exit 2 -- distinct from `EXIT_ERROR`, and with
    nothing written: no run id is minted because `run_card` is never called."""
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            cards["subtask"],
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
        ],
    )

    assert result.exit_code == 2
    assert "--branch-prefix" in result.output
    # Not the `ok: false` envelope: a usage error never reaches the try block.
    assert '"ok"' not in result.stdout
    assert not (paths.data_dir() / "runs").exists()


@requires_git
@requires_brd
def test_the_branch_prefix_the_operator_gave_lands_in_the_payloads_branch(
    project, cards, monkeypatch
):
    """The default is gone, so the only way a prefix reaches the branch name is
    the option -- and the branch is what every later command keys off."""
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    card = board.show(cards["subtask"], repo_dir=project)

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            cards["subtask"],
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m2",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)["data"]
    assert data["branch"] == dag.task_branch("m2", card)
    assert data["branch"].startswith("m2/")


@requires_git
@requires_brd
def test_the_run_success_envelope_keys_are_frozen(project, cards, monkeypatch):
    """A shape freeze: `status`, `logs` and every downstream consumer read these
    eleven names, so an added, dropped or renamed key is a contract break."""
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}
    assert envelope["ok"] is True
    assert set(envelope["data"]) == {
        "run_id",
        "card_id",
        "story_id",
        "branch",
        "base_branch",
        "worktree",
        "status",
        "failed_phase",
        "detail",
        "skipped",
        "warnings",
    }
```

- [ ] **Step 2: Run the new tests and record which one is red**

Run: `uv run pytest tests/test_cli.py -k "branch_prefix or envelope_keys_are_frozen" -v`

Expected: `test_run_without_branch_prefix_is_a_usage_error_not_an_envelope` FAILS — the option still defaults to `"m1"`, so the command succeeds with exit code 0 instead of 2. The other two already PASS: they are the regression guards that prove the value is still threaded and the payload shape is untouched once the default is removed, and they must keep passing through the rest of this task.

- [ ] **Step 3: Make `branch_prefix` required in both places**

In `src/agent_manager/cli.py`, change `run_card`'s signature (currently cli.py:633-643) so `branch_prefix` is a required keyword with no default, listed next to the other required keyword:

```python
def run_card(
    card_id: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str = "master",
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
```

and change the Typer option (currently cli.py:769-771):

```python
    branch_prefix: str = typer.Option(
        ...,
        "--branch-prefix",
        help="Milestone prefix for the derived branch name, e.g. `m2`.",
    ),
```

- [ ] **Step 4: Run the full suite and see the call sites break**

Run: `uv run pytest`

Expected: `test_run_without_branch_prefix_is_a_usage_error_not_an_envelope` now PASSES, and many other `tests/test_cli.py` tests FAIL — `TypeError: run_card() missing 1 required keyword-only argument: 'branch_prefix'` for the direct calls, and exit-code-2 assertion failures for the `CliRunner` invocations that no longer pass the option.

- [ ] **Step 5: Update the `_invoke` helper**

In `tests/test_cli.py:1168-1171`, add the prefix to the argv the helper builds. `"m1"` keeps every existing branch-name and database assertion (for instance `assert run_row == ("done", "main", "m1")` at line 1311) exactly as it was:

```python
    return runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            card_id,
            "--repo-dir",
            str(project),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m1",
            *extra,
        ],
    )
```

- [ ] **Step 6: Update the standalone `run` invocation**

In `tests/test_cli.py`, `test_a_repo_dir_that_is_not_a_directory_is_an_envelope` (currently lines 1442-1458) invokes the command directly. Without the option it would now die at exit 2 before `RepoDirError` is ever raised, which would silently gut the test:

```python
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            "cbe34d00-9d8d-4f41-9c94-f99e665771b0",
            "--repo-dir",
            str(tmp_path / "missing"),
            "--branch-prefix",
            "m1",
        ],
    )
```

- [ ] **Step 7: Name `branch_prefix` at every direct `cli.run_card(...)` call**

In `tests/test_cli.py`, add `branch_prefix="m1",` immediately after the `base_branch="main",` line of every `cli.run_card(` call that does not already pass a prefix. Before this task's edits those calls start at lines 1086, 1127, 1143, 1220, 1248, 1269, 1288, 1322, 1343, 1370, 1542, 1565, 1578 and 2266 (line numbers shift as you edit; `grep -n "cli.run_card(" tests/test_cli.py` re-finds them). The call at line 1104 already passes `branch_prefix="m7"` and is left alone.

`"m1"` is the right value everywhere: it is what the deleted default produced, so `test_the_run_story_and_subtask_rows_land_in_the_project_db` keeps its `("done", "main", "m1")` row, and the resume tests keep matching `_record_interrupted`'s `dag.task_branch("m1", ...)` at tests/test_cli.py:2401. Each edited call ends up shaped like this one (`test_run_card_drives_the_task_workflow_to_done`, currently line 1086):

```python
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )
```

and the crash helper `_crash_mid_phase` (currently line 2266) like this:

```python
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            clock=lambda: CRASHED_AT,
            runner_factory=_resume_factory(crash_at=phase),
        )
```

- [ ] **Step 8: Confirm no call site was missed**

Run: `grep -n "cli.run_card(" -A 6 tests/test_cli.py`
Expected: every listed call shows a `branch_prefix=` keyword.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest`
Expected: PASS, including the three tests added in Step 1.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli)!: require --branch-prefix on am run and drop the m1 default"
```

---

### Task 3: `--verify` on `am resume`

**Files:**
- Modify: `src/agent_manager/cli.py:1070-1101` (the `resume` Typer command)
- Test: `tests/test_cli.py` — one new test after `test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one` (currently ends at line 2604)

**Interfaces:**
- Consumes: `cli.resume_run(run_id, *, repo_dir, allow_no_verification=False, commands=(), runner_factory=None, clock=_utcnow)` — already accepts `commands: Sequence[str]` at cli.py:951. Test helpers `_crash_mid_phase(project, cards, phase)` (tests/test_cli.py:2262, now passing `branch_prefix="m1"` after Task 2) and `fake_runner` (1061).
- Produces: a `verify: list[str]` Typer option named `--verify` on `resume`, passed as `commands=list(verify)`. Same name, same type and same semantics as the `run` option from Task 1.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` after `test_a_resumed_walk_that_escalates_is_ok_true_and_exit_one` (currently ends at line 2604):

```python
@requires_git
@requires_brd
def test_resume_passes_its_repeated_verify_options_into_the_gate_context(
    project, cards, monkeypatch
):
    """`models.RunConfig` carries neither the suite commands nor the opt-out, so
    a resume has to be told the same things a fresh `run` was told -- and in the
    same order, since the engine runs the commands in sequence."""
    run_id = _crash_mid_phase(project, cards, "implement")
    contexts: list[dict[str, Any]] = []

    def collecting_factory(**kwargs):
        inner = fake_runner()

        def collect(phase, context, rendered):
            contexts.append(dict(context))
            return inner(phase, context, rendered)

        return collect

    monkeypatch.setattr(cli, "default_runner_factory", collecting_factory)

    result = runner.invoke(
        cli.app,
        [
            "resume",
            run_id,
            "--repo-dir",
            str(project),
            "--verify",
            "uv run pytest",
            "--verify",
            "uv run ruff check",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["resumed_from"] == "implement"
    assert contexts[0]["suite_cmds"] == ["uv run pytest", "uv run ruff check"]
    assert contexts[0]["allow_no_verification"] is False
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_resume_passes_its_repeated_verify_options_into_the_gate_context -v`
Expected: FAIL — `resume` has no `--verify` option, so `CliRunner` returns exit code 2 and `assert result.exit_code == 0` fails.

- [ ] **Step 3: Add the option to the `resume` command**

In `src/agent_manager/cli.py`, in the `resume` signature (currently cli.py:1071-1081), insert `verify` between `allow_no_verification` and `pretty`:

```python
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help="Proceed even when no verification suite is available (§12's opt-out).",
    ),
    verify: list[str] = typer.Option(
        [],
        "--verify",
        help=(
            "One whole verification command, repeatable. The run record does not "
            "carry the suite, so a resume is told it the way a fresh run was."
        ),
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
```

and pass it in the body (currently cli.py:1091-1095):

```python
        payload = resume_run(
            run_id,
            repo_dir=repo_dir,
            allow_no_verification=allow_no_verification,
            commands=list(verify),
        )
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_resume_passes_its_repeated_verify_options_into_the_gate_context -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no new failures. In particular `resume` still has no `--base-branch` and no `--branch-prefix`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add a repeatable --verify option to am resume"
```

---

### Task 4: README usage section

**Files:**
- Modify: `README.md` (insert between line 10, the design-spec pointer, and line 12, `## Develop`)

**Interfaces:**
- Consumes: the `--card`, `--repo-dir`, `--base-branch`, `--branch-prefix`, `--allow-no-verification`, `--verify` and `--pretty` options as they exist after Tasks 1-3.
- Produces: nothing other tasks depend on; this is the last task.

- [ ] **Step 1: Add the Usage section**

In `README.md`, insert the following between the `See docs/...` line and `## Develop`:

````markdown
## Usage

Drive one subtask card end to end. `--branch-prefix` is required — it is the
milestone prefix of the branch the run cuts — and `--verify` is repeatable, one
whole verification command per occurrence, run in the order given:

```bash
am run --card 19efcddc-0000-0000-0000-000000000000 \
  --branch-prefix m2 \
  --verify "uv run pytest" \
  --verify "uv run ruff check"
```

Pick a killed run back up at the phase it died in. There is no `--base-branch`
and no `--branch-prefix` here: both were decided when the run started and are
recorded on the run. The verification suite is not recorded, so a resume is told
it the same way a fresh run was:

```bash
am resume 20260923T140506Z-19efcddc --verify "uv run pytest"
```

Every command prints one line of JSON — `{"ok": true, "data": ...}` on success,
`{"ok": false, "error": {...}}` on a refusal. Add `--pretty` to indent it.
````

- [ ] **Step 2: Check the rendered file reads correctly**

Run: `sed -n '1,40p' README.md`
Expected: the intro, then `## Usage` with the two fenced `bash` blocks, then `## Develop` with its `uv sync` / `uv run pytest` block, and no duplicated or orphaned fences.

- [ ] **Step 3: Run the full suite one last time**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document am run and am resume usage in the README"
```
