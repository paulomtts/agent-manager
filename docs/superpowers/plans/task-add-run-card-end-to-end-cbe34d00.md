<!-- task-pipeline: validated -->
# Add `run --card` end to end (cbe34d00)

## What this subtask delivers

The first `src/agent_manager/cli.py`: a Typer app whose single command is `run`, in its card-scoped form. It is the first thing in the project that drives `workflow/builtin/task.yaml` to completion for one real card, so it is also the first place where `board.py`, `dag.py`, `paths.py`, `store.py`, `workflow/loader.py`, `engine.py` and `dispatch.py` are composed. Per §4, `cli.py` is "typer app" only: it resolves inputs, constructs the `Run`/`StoryRun`/`SubtaskRun` records, injects collaborators, calls `engine.run_subtask` once, and renders the envelope. No step logic, no gate logic, no branch-string building, no journal or SQLite writing that does not go through `Store`.

Out of scope, owned elsewhere: `status`/`runs` (3c39ae43), `logs` (be23d3c0), `resume` (5524ae72) — this card creates the `Run` record those three later read, and nothing more; milestone orchestration (census, levels, parallel stories, integrate) and non-Claude harnesses belong to later milestones; `watch`, `retry`, `cancel`, `--dry-run`, `--workflow`, `--harness`, `--max-concurrent` from the §10 grammar are not implemented here.

## Command surface

```
agent-manager run --card <id> [--repo-dir .] [--base-branch master]
                  [--branch-prefix m1] [--allow-no-verification] [--pretty]
```

`--card` is required. `--repo-dir` defaults to the current directory and is resolved to an absolute path before anything else; it is both the directory `brd` is run in (`board.show(card_id, repo_dir=...)`) and the `root` that keys the SQLite projection (`Store.open(root, run_id)` → `paths.project_db_path`). `--pretty` is the §10 human switch: JSON is the default, on one line; `--pretty` indents it. Both forms use brd's envelope: `{"ok": true, "data": ...}` or `{"ok": false, "error": {"type": ..., "message": ...}}`, printed to stdout. Diagnostics, if any, go to stderr.

## Observable behaviour

On a successful invocation the command, in order:

1. Resolves the card with `board.show(card_id, repo_dir=repo_dir)` and its parent with `board.show(card.parent_id, ...)`. These are the §7 `card` / `parent_story` cached-at-run-start reads; nothing else in the run talks to the board except the two `best_effort` `rollup.set_status` phases.
2. Derives the branch with `dag.task_branch(branch_prefix, card)` — never by string concatenation in `cli.py` — and the worktree path as `<repo_dir>/.claude/worktrees/<branch>`, absolute, matching the layout `steps/worktree.ensure` expects (it requires an absolute `worktree` and refuses anything else).
3. Mints a run id (`<UTC timestamp>-<dag.short_id(card_id)>`, from an injectable clock so tests are deterministic) and opens `Store.open(repo_dir, run_id)`. Every artifact path comes from `paths.py`; nothing is written under the worktree (D4/§9).
4. Loads the document with `workflow.loader.load_builtin("task")`.
5. Records, before the walk and through `Store` only, so `status`/`logs`/`resume` have something to read even if the process dies immediately: `models.Run(id=run_id, workflow="task", repo_dir=..., base_branch=..., branch_prefix=..., status="started", started_at=..., config=models.RunConfig())` → `record_run`; `models.StoryRun(card_id=parent.id, title=parent.title, level=0, status="started", tip_branch=branch)` → `record_story`; `models.SubtaskRun(card_id=card.id, branch=..., base_branch=..., status="started", worktree_path=...)` → `record_subtask`. Field names and types are used verbatim as models.py:89-159 declares them.
6. Builds the real agent runner — `dispatch.AgentRunner(workflow=..., store=..., launcher=<the direct launcher>, run_id=..., story_id=parent.id, card_id=card.id)`, leaving `adapters` at `harness.registry.default_adapters()` and `harness_map` empty so roles fall back to `DEFAULT_HARNESS` — and calls `engine.run_subtask(workflow, store, story_id=parent.id, subtask=subtask, repo_dir=repo_dir, commands=(), card=card, parent_story=parent, agent_runner=runner)`. The runner is a constructor parameter of the command's implementation function, not a hard-coded import inside it: that injection seam is what lets the tests pass a fake and launch no harness.
7. Re-records the run, story and subtask with their terminal status from the returned `SubtaskSummary` (`done` → `done`; `escalated` → `escalated`), then renders the envelope.

Success payload (`data`): `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status` (`done` | `escalated`), `failed_phase`, `detail`, `skipped`, `warnings` (the summary's, including best-effort board-write failures — §12 forbids reporting a clean success when the board never moved). Exit code `0` when the summary is `done`, `1` when it is `escalated`: an escalated subtask is a truthful result, so the envelope stays `ok: true` and the exit code carries the bad news.

### Verification context and `--allow-no-verification`

§12's escape hatch is `steps/reducers.verification_gate(suite_cmds, allow_no_verification, caller_provided)`, and `builtin/task.yaml` puts that gate on the `explore` phase. Gate arguments are bound by name from the phase's binding table (`dispatch.gate_values` over the engine's context), and `engine.subtask_context` supplies none of those three names — so running `task.yaml` today would fail the gate's binding with `EngineError: parameter 'suite_cmds': no value for a required parameter`. This card is where that surfaces, and the narrowest fix it owns is a seam, not a reimplementation: `engine.run_subtask` gains an optional extra-context mapping that is folded into the binding table after `subtask_context` and before `_document_paths`, and `cli.py` passes exactly `{"suite_cmds": list(commands), "allow_no_verification": <flag>, "caller_provided": False}`. The flag is forwarded as a real `bool` because the gate tests identity (`allow_no_verification is True`). `commands` stays `()` for this card (verification discovery per run is not this subtask's), so with the flag absent an empty suite blocks `explore`, and with the flag present the walk proceeds — which is precisely the behaviour §12 describes. No gate wording, no gate logic and no reducer duplication enters `cli.py`.

## Error paths

Every one of these prints `{"ok": false, "error": {"type": <exception class name>, "message": <str>}}` and exits `3` (Typer keeps `2` for its own usage errors, so `3` distinguishes "the tool could not run this" from "the subtask escalated"):

- `board.BoardError` — `brd` missing from PATH, a non-zero exit, a non-envelope stdout, an unknown card id. The message is brd's verbatim; the CLI invents no "not found" semantics.
- The card has no `parent_id`. `run --card` drives a subtask of a story; a card with no parent has no `StoryRun` to key `record_phase`/`record_subtask` by. Refused before any run id is minted, with a message naming the card and the missing parent.
- `--repo-dir` does not exist, or is not a directory.
- `workflow.loader` failures from `load_builtin("task")` (a broken document is a load-time bug and must reach the operator unchanged).
- `errors.EngineError` escaping the walk — e.g. an unresolvable declared input, or an unbindable gate parameter. `engine.run_subtask` deliberately lets these out rather than journalling them as a phase failure.

Errors raised *after* the `Run` row exists still leave that row and the journal in place; the CLI does not delete run state on failure. `AgentPhaseFailed` never reaches the CLI — the engine converts it to an `escalated` summary.

## Tests

All CLI tests live in `tests/test_cli.py` (mirroring `src/agent_manager/cli.py`) and run under the default suite: `uv run pytest`. Tiering follows §14 of the design spec (lines 477-492). The card's own text — "a fake adapter, a temporary git repo and a temporary brd board, no real harness is launched" — puts the integration tests in the **Engine tier** (fake agent runner returning canned results) built on **Steps-tier fixtures** (temp git repo, temp brd board, `XDG_DATA_HOME` pointed at a tmp dir so `paths.data_dir()` never touches the real one). Explicitly *not* the End-to-end tier: that tier is the single opt-in real-harness toy milestone, out of scope here.

1. **Happy path.** `run --card` over the temp board drives `task.yaml` to `done` with a fake agent runner supplying valid canned results: envelope is `{"ok": true, ...}` with `status: "done"`, exit code `0`. *Engine tier (Steps-tier fixtures).*
2. **Branch and worktree derivation.** The recorded branch equals `dag.task_branch("m1", card)` for the default prefix and equals the prefixed form for a custom `--branch-prefix`; the worktree path is `<repo_dir>/.claude/worktrees/<branch>` and absolute; `--base-branch` lands in both `Run.base_branch` and `SubtaskRun.base_branch`. *Engine tier.*
3. **State is durable and outside the worktree.** After a run, the project DB at `paths.project_db_path(repo_dir)` holds the run, story and subtask rows, `paths.run_dir(run_id)/journal.jsonl` holds the matching lines in journal-before-row order, and `git status` in the temp repo is clean — no artifact was written inside it. *Engine tier (Steps-tier fixtures).*
4. **Rows exist before the walk.** With a fake runner that raises on its first call, the run/story/subtask rows are already recorded — the guarantee `status`/`resume` depend on. *Engine tier.*
5. **Escalation.** A fake adapter whose result fails a non-retryable gate yields `status: "escalated"`, a populated `failed_phase` and `detail`, exit code `1`, and an `escalated` subtask row. *Engine tier.*
6. **Best-effort warning surfaces.** A board write that fails (brd stub exiting non-zero for `update`) still reaches `done`, and the warning appears in the payload's `warnings`. *Engine tier (Steps-tier fixtures).*
7. **`--allow-no-verification`.** Without the flag and with no discovered suite, `explore`'s `verification_gate` blocks and the subtask escalates; with the flag, the same fixture walks past `explore`. *Engine tier.* (The gate's own truth table is already unit-tested in `tests/steps/test_reducers.py` and is not re-tested here.)
8. **Unknown card.** `brd show` failing produces `{"ok": false, "error": {...}}` carrying brd's message, exit code `3`, and no run directory under the tmp data dir. *Engine tier (Steps-tier fixtures).*
9. **Parentless card.** A board card with no `parent_id` is refused with an `ok: false` envelope naming the card, exit code `3`. *Engine tier (Steps-tier fixtures).*
10. **No harness is launched.** The injected fake runner is the only thing called; the real `dispatch.AgentRunner`/launcher is never constructed in any test above — asserted once, directly, by running with a launcher that fails the test if invoked. *Engine tier (the adapter-tier rule that the launcher is injected, applied at the CLI seam).*
11. **Envelope rendering.** The pure renderer produces single-line JSON by default and indented JSON under `--pretty`, with the `{ok, data}` / `{ok, error}` shapes. *Unit tier (pure function).*
12. **Run-id and worktree-path derivation.** The pure helpers: a fixed clock and card id give a stable run id; the worktree path composes from repo dir and branch. *Unit tier (pure functions).*

---

# `run --card` End to End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `src/agent_manager/cli.py` with a single `run --card` command that composes board, dag, store, loader and engine into one walk of `builtin/task.yaml` for one subtask card, and prints a brd-shaped envelope.

**Architecture:** `cli.py` holds a Typer app plus four pure helpers (`resolve_repo_dir`, `mint_run_id`, `worktree_for`, `render`) and one composition function, `run_card`, that does the §Observable-behaviour sequence and returns a payload dict. Collaborators are injected: the agent runner comes from a module-level `default_runner_factory` that builds the real `dispatch.AgentRunner` in production and is replaced by a fake callable in every test, so no harness is ever launched. Two integration seams outside `cli.py` are opened first, because the walk cannot reach `done` without them: an optional `extra_context` mapping on `engine.run_subtask` (the spec's gate-binding fix), and a real `verification_passed_gate` in `steps/reducers.py` replacing the registry placeholder that would otherwise fail the `verify` phase.

**Tech Stack:** Python 3.12+, Typer (already a dependency, `pyproject.toml:8`), Pydantic v2 models from `models.py`, SQLite + JSONL through `store.Store`, pytest with `--import-mode=importlib`.

**Spec:** `docs/superpowers/specs/task-add-run-card-end-to-end-cbe34d00-design.md` (reproduced verbatim above).

## Global Constraints

- Branch: `m1/task-add-run-card-end-to-end-cbe34d00`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-add-run-card-end-to-end-cbe34d00`, cut from `origin/m1/task-run-agent-phases-bf8e415b`. Every path below is relative to that worktree.
- Verification is exactly `uv run pytest`. There is no lint and no typecheck command (CLAUDE.md).
- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md). `tests/test_cli.py` is therefore the only new test file; the two seam tasks extend `tests/test_engine.py`, `tests/steps/test_reducers.py` and `tests/workflow/test_registry.py`, which are the tiers §14 assigns to that code.
- CLI output is JSON by default, `--pretty` for humans, in brd's envelope `{"ok": true, "data": ...}` / `{"ok": false, "error": {"type", "message"}}` (CLAUDE.md, design §10 line 404).
- Exit codes: `0` done, `1` escalated, `3` operational error. Typer keeps `2` for its own usage errors.
- Branch names come from `dag.task_branch(prefix, card)` only (dag.py:63). Artifact paths come from `paths.py` only. Board access goes through `board.py` only. Run state is written through `Store` only.
- Pydantic models for boundary-validated data; plain dicts/dataclasses for internal state (CLAUDE.md).
- `pyproject.toml:19` already declares `agent-manager = "agent_manager.cli:app"`. Until Task 3 lands, that console script points at a module that does not exist; Task 3 is what makes it real.

## Review Focus

- **A relative `--repo-dir` (including its default, `.`).** `steps/worktree.ensure` raises `ValueError` on any non-absolute `worktree` or `repo_dir` (worktree.py:100-111), so the CLI must resolve the directory to an absolute path before deriving anything from it. Test in Task 3 (`test_resolve_repo_dir_returns_an_absolute_path`) and Task 4 (`test_a_relative_repo_dir_still_produces_an_absolute_worktree`).
- **A `--card` value that is not a UUID.** `dag.short_id` raises a bare `ValueError` for anything that is not 32 hex characters (dag.py:23-30), and the run id is minted from the card id. A typo must come back as an envelope, not a traceback. Test in Task 6 (`test_a_card_id_that_is_not_a_uuid_is_an_envelope_not_a_traceback`).
- **Non-JSON-native values in the payload.** `worktree` is a `Path` and warnings may carry `Path` repr fragments; `json.dumps` raises `TypeError` on a `Path`. The renderer must pass `default=str`. Test in Task 3 (`test_render_survives_a_path_in_the_payload`).
- **A best-effort board phase that fails.** `rollup.set_status` is still a registry placeholder that raises `NotImplementedError` (workflow/registry.py:221-224), and §12 forbids reporting a clean `done` while the card never moved. The warning must reach `warnings` in the payload. Test in Task 5 (`test_a_failed_best_effort_board_phase_shows_up_in_warnings`).
- **The parent lookup failing after the card resolved.** `board.show(card.parent_id)` is a second subprocess that can fail on its own (a deleted parent, a board permission error); it must produce the same `ok: false` envelope and leave no run directory behind. Test in Task 6 (`test_a_failing_parent_lookup_is_an_envelope_and_leaves_no_run_directory`).

## Integration gaps this plan closes, and the ones it deliberately leaves open

Read before starting. These are facts about the branch, verified in the code:

1. **Gate parameters are unbound (closed here, Task 1).** `builtin/task.yaml:10` puts `exploration_output_gate` and `verification_gate` on `explore`. `dispatch.evaluate_gates` binds gate parameters from `dispatch.gate_values(context, ...)`, i.e. the engine's context (dispatch.py:289-291, 473-478), and `engine.subtask_context` supplies none of `suite_cmds`, `allow_no_verification`, `caller_provided`, `provided_verification`. Task 1 adds the `extra_context` seam the spec asks for. **Deviation from the spec, on purpose:** the spec names three keys; `exploration_output_gate(explore, provided_verification)` (reducers.py:257-260) declares a fourth with no default, so the CLI passes four. Passing three would move the `EngineError` from `verification_gate` to `exploration_output_gate` and fix nothing.
2. **`verification_passed_gate` is a placeholder (closed here, Task 2).** `builtin/task.yaml:72` gates the deterministic `verify` phase on it, and `workflow/registry.py:230-234` registers a placeholder that raises `NotImplementedError`. `engine._run_deterministic` catches that as a phase failure, and `verify` is not `best_effort`, so **the walk cannot reach `done` with the default registry**. Task 2 implements the gate (a pure reducer, §14's "pure functions" tier) and swaps the placeholder. Without it, spec test 1 is unsatisfiable.
3. **`rollup.set_status` is a placeholder (left open).** `workflow/registry.py:221-224`. Both phases that call it are `best_effort: true` (`builtin/task.yaml:17, 78`), so the walk continues and the failure surfaces as a warning — which is exactly the behaviour spec test 6 asks for. This plan asserts the warning (Task 5) and does not implement `steps/rollup.py`; it belongs to the card the registry comment names.
4. **`results.RESULT_MODELS` is empty and `critic_blockers_gate` is a placeholder (left open).** `results.py:21`, `workflow/registry.py:226-228`. Both live *inside* `dispatch.AgentRunner`, which this card's tests replace with a fake callable, so neither is reachable from `tests/test_cli.py`. They do mean the *production* path (`default_runner_factory` → real `AgentRunner`) escalates at `explore` today. That is a truthful, journalled escalation, not a crash, and closing it is the agent-phase-results card's work, not this one's. Do not invent result models here.
5. **Because the tests inject a fake runner, agent-phase gates never execute in `tests/test_cli.py`.** Spec test 7 therefore cannot observe `verification_gate` blocking through the walk. Task 6 tests the §12 escape hatch at the seam the CLI actually owns: the extra context the CLI hands the engine, fed to the *real* `reducers.verification_gate`. This is stated in the test's docstring so nobody later reads it as a weaker assertion than it is.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/agent_manager/cli.py` (create) | Typer app, the four pure helpers, the runner-factory seam, and `run_card`. No step logic, no gate logic. |
| `src/agent_manager/engine.py` (modify, `run_subtask` at :330) | Gains `extra_context`, folded in after `subtask_context` and before `_document_paths`. |
| `src/agent_manager/steps/reducers.py` (modify, append) | Gains the pure `verification_passed_gate`. |
| `src/agent_manager/workflow/registry.py` (modify, :192-235) | Binds `verification_passed_gate` to the real reducer instead of a placeholder. |
| `tests/test_cli.py` (create) | Unit-tier tests for the pure helpers; Engine-tier tests for `run_card`/`run` on Steps-tier fixtures (temp git repo + temp brd board + tmp `XDG_DATA_HOME`). |
| `tests/test_engine.py` (modify, append) | Engine-tier tests for the `extra_context` seam. |
| `tests/steps/test_reducers.py` (modify, append) | Pure-tier tests for `verification_passed_gate`. |
| `tests/workflow/test_registry.py` (modify, :107-123) | The placeholder list loses one name and the real binding gains an assertion. |

---

### Task 1: The `extra_context` seam on `engine.run_subtask`

**Files:**
- Modify: `src/agent_manager/engine.py:330-354`
- Test: `tests/test_engine.py` (append at end of file)

**Interfaces:**
- Consumes: `engine.run_subtask(workflow, store, *, story_id, subtask, repo_dir, commands=(), card=None, parent_story=None, agent_runner=None, start_phase=None, clock=_utcnow) -> SubtaskSummary` as it exists today; `engine.RESERVED_CONTEXT_KEYS`; `errors.EngineError`.
- Produces: `engine.run_subtask(..., extra_context: Mapping[str, Any] | None = None, ...)` — a keyword-only parameter placed after `parent_story`. Keys land in the binding table every phase, `when` predicate and gate binds from. A key that collides with `RESERVED_CONTEXT_KEYS` raises `EngineError`. Task 4 calls it with `{"suite_cmds": [], "allow_no_verification": False, "caller_provided": False, "provided_verification": None}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
def test_extra_context_reaches_a_deterministic_phase_binding(tmp_path: Path):
    """The §12 escape hatch's parameters have to arrive somehow: `subtask_context`
    is a fixed table, and `builtin/task.yaml`'s gates bind names it does not hold.
    """
    seen: dict[str, Any] = {}

    def step(suite_cmds: list[str], allow_no_verification: bool) -> dict[str, Any]:
        seen["suite_cmds"] = suite_cmds
        seen["allow_no_verification"] = allow_no_verification
        return {"ok": True}

    registry = FunctionRegistry()
    registry.register("only.step", step)
    document = tmp_path / "one.yaml"
    document.write_text(
        "name: one\n"
        "description: one deterministic phase\n"
        "phases:\n"
        "  - name: only\n"
        "    kind: deterministic\n"
        "    run: only.step\n",
        encoding="utf-8",
    )
    workflow = load_workflow(document, registry)
    store = store_module.Store.open(tmp_path, "run-extra-1")

    summary = engine.run_subtask(
        workflow,
        store,
        story_id="story-1",
        subtask=_subtask(),
        repo_dir=REPO,
        extra_context={"suite_cmds": [], "allow_no_verification": True},
    )

    assert summary.status == "done"
    assert seen == {"suite_cmds": [], "allow_no_verification": True}


def test_extra_context_may_not_redefine_a_reserved_key(tmp_path: Path):
    """`worktree`, `card` and friends are the engine's own: letting a caller
    overwrite one would point every later step at a path the engine never chose.
    """
    registry = FunctionRegistry()
    registry.register("only.step", lambda: {"ok": True})
    document = tmp_path / "one.yaml"
    document.write_text(
        "name: one\n"
        "description: one deterministic phase\n"
        "phases:\n"
        "  - name: only\n"
        "    kind: deterministic\n"
        "    run: only.step\n",
        encoding="utf-8",
    )
    workflow = load_workflow(document, registry)
    store = store_module.Store.open(tmp_path, "run-extra-2")

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id="story-1",
            subtask=_subtask(),
            repo_dir=REPO,
            extra_context={"worktree": "/somewhere/else"},
        )

    assert "worktree" in str(caught.value)
```

`Path`, `Any`, `pytest`, `engine`, `store_module`, `load_workflow`, `FunctionRegistry`, `REPO` and `_subtask` are all already imported at the top of `tests/test_engine.py` (lines 10-32).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -k extra_context -v`
Expected: FAIL — `TypeError: run_subtask() got an unexpected keyword argument 'extra_context'`.

- [ ] **Step 3: Add the parameter**

In `src/agent_manager/engine.py`, change the signature of `run_subtask` (currently line 330-343) by inserting one parameter after `parent_story`:

```python
def run_subtask(
    workflow: Workflow,
    store: Store,
    *,
    story_id: str,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    card: models.Card | None = None,
    parent_story: models.Card | None = None,
    extra_context: Mapping[str, Any] | None = None,
    agent_runner: AgentPhaseRunner | None = None,
    start_phase: str | None = None,
    clock: Clock = _utcnow,
) -> SubtaskSummary:
```

and extend its docstring with one paragraph, after the existing `story_id` paragraph:

```python
    """Walk `workflow`'s phases for one subtask, running the deterministic ones.

    `story_id` is the caller's: `Store.record_phase` and `Store.record_subtask`
    are both keyed by it, and nothing in a subtask knows its story.

    `extra_context` is the caller's half of the binding table (§12): the shipped
    `builtin/task.yaml` gates on `verification_gate(suite_cmds,
    allow_no_verification, caller_provided)` and `exploration_output_gate(explore,
    provided_verification)`, and `subtask_context` is a fixed table that holds
    none of those names. Rather than teach this module about a particular
    document's gates, the caller supplies them. Reserved keys are refused: a
    caller that could overwrite `worktree` would point every later step at a
    path the engine never derived.
    """
```

Then, immediately after the `context = subtask_context(...)` call (currently lines 350-352) and *before* `context.update(_document_paths(workflow, card))`, insert:

```python
    if extra_context:
        reserved = sorted(set(extra_context) & set(RESERVED_CONTEXT_KEYS))
        if reserved:
            raise EngineError(
                "extra_context supplies "
                f"{', '.join(repr(key) for key in reserved)}, which the engine owns "
                f"(reserved: {', '.join(RESERVED_CONTEXT_KEYS)})"
            )
        context.update(extra_context)
```

`Mapping` and `Any` are already imported (engine.py:20, 24).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS, including the whole pre-existing engine suite.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): let a caller add gate parameters to the binding table"
```

---

### Task 2: A real `verification_passed_gate`

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (append at end of file)
- Modify: `src/agent_manager/workflow/registry.py:206-235`
- Test: `tests/steps/test_reducers.py` (append at end of file)
- Test: `tests/workflow/test_registry.py:107-123`

**Interfaces:**
- Consumes: `verify.run_suite(commands, worktree) -> dict` whose result is `{"passed": bool, "verified": list[dict], "detail": str}` (verify.py:222-274); `builtin/task.yaml:69-72` binds this gate's `result` parameter to that dict via `engine._gate_values`.
- Produces: `agent_manager.steps.reducers.verification_passed_gate(result: object) -> dict[str, str] | None` — `None` when `result["passed"]` is exactly `True`, else `{"blocked": "verification", "detail": ...}`. `workflow.registry.default_registry()` resolves `"verification_passed_gate"` to that exact function object.

- [ ] **Step 1: Write the failing tests**

Append to `tests/steps/test_reducers.py`:

```python
def test_verification_passed_gate_passes_a_green_suite():
    assert verification_passed_gate({"passed": True, "verified": [], "detail": ""}) is None


def test_verification_passed_gate_blocks_a_red_suite_and_carries_its_detail():
    verdict = verification_passed_gate(
        {"passed": False, "verified": [], "detail": "verification failed: uv run pytest — 1 failed"}
    )
    assert verdict["blocked"] == "verification"
    assert "1 failed" in verdict["detail"]


def test_verification_passed_gate_blocks_a_truthy_stand_in_for_passed():
    """Strict identity, like `verification_gate`'s opt-out: a step that reported
    `passed: "yes"` has not told us the suite was green, it has told us it is
    broken."""
    verdict = verification_passed_gate({"passed": "yes", "verified": [], "detail": ""})
    assert verdict["blocked"] == "verification"


def test_verification_passed_gate_blocks_anything_that_is_not_a_result_mapping():
    verdict = verification_passed_gate(None)
    assert verdict["blocked"] == "verification"
    assert "no verification result" in verdict["detail"]
```

Add `verification_passed_gate` to the existing `from agent_manager.steps.reducers import (...)` list at the top of `tests/steps/test_reducers.py` (it already imports `verification_gate` that way, at lines 12-22).

In `tests/workflow/test_registry.py`, replace the placeholder test at lines 116-122 in full:

```python
def test_placeholders_resolve_at_load_time_and_raise_when_called() -> None:
    registry = default_registry()
    for name in ("rollup.set_status", "critic_blockers_gate"):
        fn = registry.resolve(name)
        with pytest.raises(NotImplementedError) as caught:
            fn()
        assert name in str(caught.value)
```

and append one line to `test_default_registry_resolves_implemented_steps_to_the_real_callables` (lines 107-113):

```python
    assert registry.resolve("verification_passed_gate") is reducers.verification_passed_gate
```

`reducers` and `default_registry` are already imported in that file (lines 5 and 12).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -k verification_passed -v`
Expected: FAIL at import — `ImportError: cannot import name 'verification_passed_gate'`.

- [ ] **Step 3: Implement the gate**

Append to `src/agent_manager/steps/reducers.py`:

```python
# The `verify` phase's gate (`builtin/task.yaml`). `verify.run_suite` reports
# what happened and judges nothing -- it returns `passed: false` for a red
# command and raises `VerifyError` only for a command it could not launch at
# all. Something has to turn "red" into a stop, and doing it here rather than
# inside the step keeps the one rule in one place, the way `review_gate` owns
# the dirty-worktree rule. Identity on `True`, for the same reason
# `verification_gate`'s opt-out uses it: a step that answered `passed: "yes"`
# is broken, and reading that as green would ship unverified work.
def verification_passed_gate(result: object) -> dict[str, str] | None:
    """``None`` when the suite really passed, else a blocked verdict."""
    if not isinstance(result, Mapping):
        return {
            "blocked": "verification",
            "detail": (
                "no verification result to judge: the verify step returned "
                f"{_js_text(result)} instead of a result mapping, so nothing "
                "established that this subtask's tests are green."
            ),
        }
    if result.get("passed") is True:
        return None
    detail = str(result.get("detail") or "").strip()
    return {
        "blocked": "verification",
        "detail": detail
        or (
            "the verification suite did not pass and reported no detail "
            f"(result: {_json(dict(result))})"
        ),
    }
```

- [ ] **Step 4: Swap the placeholder for the real binding**

In `src/agent_manager/workflow/registry.py`, move `verification_passed_gate` out of the placeholder block. Add to the gates block (after line 212):

```python
    registry.register("verification_passed_gate", reducers.verification_passed_gate)
```

and delete the placeholder registration (lines 229-234):

```python
    registry.register(
        "verification_passed_gate",
        _placeholder(
            "verification_passed_gate", "the sibling subtask that adds the agent-phase gates"
        ),
    )
```

Update the `default_registry` docstring's last paragraph (lines 199-204) to say two, not three:

```python
    The five reducers and the four implemented steps are the real, imported
    callables -- not wrappers -- so `resolve(name) is the_function` holds and a
    sibling's bugfix reaches the engine without touching this table. The two
    remaining names have no implementation on this branch (`steps/rollup.py`
    does not exist; `critic_blockers_gate` is not in `steps/reducers.py`), so
    they resolve to placeholders.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py tests/workflow -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/reducers.py src/agent_manager/workflow/registry.py tests/steps/test_reducers.py tests/workflow/test_registry.py
git commit -m "feat(steps): implement verification_passed_gate and register it"
```

---

### Task 3: `cli.py` — the pure helpers and the Typer skeleton

**Files:**
- Create: `src/agent_manager/cli.py`
- Test: `tests/test_cli.py` (create)

**Interfaces:**
- Consumes: `dag.short_id(card_id) -> str` (dag.py:23); `dag.task_branch(prefix, card) -> str` (dag.py:63).
- Produces, all in `agent_manager.cli`:
  - `EXIT_ESCALATED: int = 1`, `EXIT_ERROR: int = 3`
  - `class CliError(RuntimeError)`, `class RepoDirError(CliError)`, `class ParentlessCardError(CliError)`
  - `resolve_repo_dir(repo_dir: Path) -> Path`
  - `mint_run_id(card_id: str, now: datetime) -> str`
  - `worktree_for(repo_dir: Path, branch: str) -> Path`
  - `ok_envelope(data: Any) -> dict[str, Any]`, `error_envelope(error: BaseException) -> dict[str, Any]`, `render(envelope: Mapping[str, Any], *, pretty: bool = False) -> str`
  - `app: typer.Typer`
  Task 4 adds `run_card` and the `run` command to this same module.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
"""Behaviour of the `run --card` command (design §10, spec card cbe34d00).

Two tiers live here, per design §14 lines 477-492 and the spec's Tests section:

- the pure helpers (`render`, `mint_run_id`, `worktree_for`, `resolve_repo_dir`)
  are unit tests -- no clock, no filesystem beyond `tmp_path`, no subprocess;
- `run_card` and the Typer command are **Engine tier**: a fake
  `engine.AgentPhaseRunner` returning canned results, on **Steps-tier fixtures**
  (a real temporary git repo, a real temporary brd board, and `XDG_DATA_HOME`
  pointed at `tmp_path` so `paths.data_dir()` never touches the developer's own).
  No harness process is ever launched: the real `dispatch.AgentRunner` and its
  launcher are never constructed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import cli


def test_render_is_one_line_of_json_by_default():
    text = cli.render({"ok": True, "data": {"status": "done"}})
    assert "\n" not in text
    assert json.loads(text) == {"ok": True, "data": {"status": "done"}}


def test_render_indents_under_pretty():
    text = cli.render({"ok": True, "data": {"status": "done"}}, pretty=True)
    assert "\n" in text
    assert json.loads(text) == {"ok": True, "data": {"status": "done"}}


def test_ok_envelope_matches_brds_shape():
    assert cli.ok_envelope({"run_id": "r1"}) == {"ok": True, "data": {"run_id": "r1"}}


def test_error_envelope_carries_the_exception_class_name_and_message():
    envelope = cli.error_envelope(ValueError("not a card id: 'nope'"))
    assert envelope == {
        "ok": False,
        "error": {"type": "ValueError", "message": "not a card id: 'nope'"},
    }


def test_render_survives_a_path_in_the_payload():
    """`worktree` is a Path and `json.dumps` refuses one. A renderer that raises
    would turn a finished run into a traceback with no envelope at all."""
    text = cli.render(cli.ok_envelope({"worktree": Path("/repo/.claude/worktrees/m1/x")}))
    assert json.loads(text)["data"]["worktree"] == "/repo/.claude/worktrees/m1/x"


def test_mint_run_id_is_the_timestamp_and_the_cards_short_id():
    run_id = cli.mint_run_id(
        "cbe34d00-9d8d-4f41-9c94-f99e665771b0",
        datetime(2026, 9, 23, 14, 5, 6, tzinfo=timezone.utc),
    )
    assert run_id == "20260923T140506Z-cbe34d00"


def test_mint_run_id_refuses_something_that_is_not_a_card_id():
    with pytest.raises(ValueError):
        cli.mint_run_id("not-a-uuid", datetime(2026, 9, 23, tzinfo=timezone.utc))


def test_worktree_for_is_absolute_and_under_dot_claude_worktrees():
    worktree = cli.worktree_for(Path("/repo"), "m1/task-add-run-card-cbe34d00")
    assert worktree == Path("/repo/.claude/worktrees/m1/task-add-run-card-cbe34d00")
    assert worktree.is_absolute()


def test_resolve_repo_dir_returns_an_absolute_path(tmp_path, monkeypatch):
    """`steps/worktree.ensure` refuses a relative path outright, so the CLI has
    to resolve `--repo-dir` -- whose default is `.` -- before deriving anything."""
    monkeypatch.chdir(tmp_path)
    resolved = cli.resolve_repo_dir(Path("."))
    assert resolved.is_absolute()
    assert resolved == tmp_path.resolve()


def test_resolve_repo_dir_refuses_a_path_that_is_not_a_directory(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(cli.RepoDirError) as caught:
        cli.resolve_repo_dir(missing)
    assert "nope" in str(caught.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'agent_manager.cli'`.

- [ ] **Step 3: Write the module**

Create `src/agent_manager/cli.py`:

```python
"""The Typer app (design §4 line 128, §10 lines 388-411).

This module composes and renders; it decides nothing a collaborator already
decides. Branch names come from `dag`, board reads from `board`, artifact paths
from `paths` via `store`, the phase walk from `engine`, and the dispatch from
`dispatch.AgentRunner`. §4 calls this file "typer app" and that is the whole
constraint: no step logic, no gate logic, no branch strings built by hand, and
no run state written anywhere but through `Store`.

Output is brd's envelope, because a human and a script read the same two tools
and `{"ok": ..., "data": ...}` is already what one of them prints (CLAUDE.md,
§10 line 404). JSON is one line by default and indented under `--pretty`.

Exit codes carry what the envelope cannot: `0` for a subtask that finished, `1`
for one that escalated -- an escalation is a truthful result, so the envelope
stays `ok: true` -- and `3` for "this tool could not run that", leaving `2` to
Typer's own usage errors.
"""

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from agent_manager import dag

EXIT_ESCALATED = 1
"""The subtask escalated. §12: a full stop a human has to read."""

EXIT_ERROR = 3
"""The tool could not run the subtask at all. `2` belongs to Typer's usage errors."""

RUN_ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
"""Sortable, path-safe, second-resolution UTC. Run ids are directory names."""

WORKTREE_PARTS = (".claude", "worktrees")
"""Where a subtask's worktree lives under the repo, matching the layout the rest
of this project already uses."""


class CliError(RuntimeError):
    """The command refused to start a run. One base type for the envelope."""


class RepoDirError(CliError):
    """`--repo-dir` does not name a directory this tool can work in."""


class ParentlessCardError(CliError):
    """The card has no parent story.

    `run --card` drives a subtask *of a story*: `Store.record_subtask` and
    `Store.record_phase` are both keyed by a story id, and inventing one would
    put rows in the projection that `status` and `resume` could never join back
    to a real card.
    """


def resolve_repo_dir(repo_dir: Path) -> Path:
    """`--repo-dir` as an existing absolute directory, or `RepoDirError`.

    Resolved before anything is derived from it: `steps/worktree.ensure` refuses
    a relative `worktree` or `repo_dir` outright, and the default value of the
    option is `.`.
    """
    resolved = Path(repo_dir).expanduser().resolve()
    if not resolved.is_dir():
        raise RepoDirError(
            f"--repo-dir {str(repo_dir)!r} is not a directory (resolved to {resolved})"
        )
    return resolved


def mint_run_id(card_id: str, now: datetime) -> str:
    """`<UTC timestamp>-<short card id>`: unique, sortable, and greppable.

    The short id comes from `dag`, like every other derived name in the program,
    which also means a card id that is not a UUID is refused here rather than
    producing a run directory nobody can trace back to a card.
    """
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{dag.short_id(card_id)}"


def worktree_for(repo_dir: Path, branch: str) -> Path:
    """`<repo_dir>/.claude/worktrees/<branch>`, absolute.

    Absolute because `steps/worktree.ensure` requires it, and built by joining
    the branch's own segments so a branch like `m1/task-x` becomes two path
    components rather than one with a slash in its name.
    """
    return Path(repo_dir).resolve().joinpath(*WORKTREE_PARTS, *branch.split("/"))


def ok_envelope(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def error_envelope(error: BaseException) -> dict[str, Any]:
    """brd's failure envelope. The type is the exception's own class name, so an
    operator can grep the source for the thing that refused."""
    return {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}}


def render(envelope: Mapping[str, Any], *, pretty: bool = False) -> str:
    """The envelope as text: one line by default, indented under `--pretty`.

    `default=str` is not decoration: the payload carries a `Path`, and a
    renderer that raised `TypeError` on it would turn a finished run into a
    traceback with no envelope at all. `sort_keys` makes the output diffable.
    """
    if pretty:
        return json.dumps(envelope, indent=2, sort_keys=True, default=str)
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True, default=str)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


app = typer.Typer(
    add_completion=False,
    help="Drive brd cards through the agent-manager workflow engine.",
)


@app.callback()
def main() -> None:
    """agent-manager: run one subtask card end to end.

    The callback exists so `run` stays a named subcommand: a Typer app with one
    command and no callback collapses into a bare command, and §10's grammar is
    `agent-manager run ...`.
    """
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add the typer app skeleton and its pure helpers"
```

---

### Task 4: `run_card` — the composition, and the happy path

**Files:**
- Modify: `src/agent_manager/cli.py` (append)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `board.show(card_id, *, repo_dir=None) -> models.Card` (board.py:162); `dag.task_branch` (dag.py:63); `load_builtin("task") -> Workflow` (workflow/loader.py:345); `Store.open(root, run_id)`, `record_run`, `record_story`, `record_subtask` (store.py:328-380); `models.Run`/`StoryRun`/`SubtaskRun`/`RunConfig` (models.py:109-159); `engine.run_subtask(..., extra_context=...)` from Task 1; `dispatch.AgentRunner` (dispatch.py:344); `harness.launcher.run_direct` (launcher.py:94).
- Produces, in `agent_manager.cli`:
  - `class RunnerFactory(Protocol)` with `__call__(self, *, workflow: Workflow, store: Store, run_id: str, story_id: str, card_id: str) -> engine.AgentPhaseRunner`
  - `default_runner_factory(*, workflow, store, run_id, story_id, card_id) -> engine.AgentPhaseRunner`
  - `gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]`
  - `run_card(card_id: str, *, repo_dir: Path, base_branch: str = "master", branch_prefix: str = "m1", allow_no_verification: bool = False, commands: Sequence[str] = (), runner_factory: RunnerFactory | None = None, clock: Callable[[], datetime] = _utcnow) -> dict[str, Any]` returning the payload with keys `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`.
  Task 5 adds the `run` command that calls `run_card`; Task 6 adds error-path tests only.

- [ ] **Step 1: Write the failing tests (fixtures plus the happy path)**

Append to `tests/test_cli.py`:

```python
import shutil
import subprocess
from typing import Any

from agent_manager import dag, models, paths

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the CLI's steps-tier fixtures",
)
requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the CLI's steps-tier fixtures",
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)["data"]["id"]


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board.

    `--repo-dir` is both at once in production, so the fixture is too.
    XDG_DATA_HOME points into tmp_path, which isolates brd's own database *and*
    `paths.data_dir()`, so no run artifact can land in the developer's home.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True, text=True
    )
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "agent-manager tests")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


@pytest.fixture
def cards(project) -> dict[str, str]:
    """A milestone -> story -> subtask chain, the shape `run --card` requires."""
    milestone = _add_card(project, "Milestone 1: walking skeleton")
    story = _add_card(project, "The CLI: run, status, logs, resume", milestone)
    subtask = _add_card(project, "Add run --card end to end", story)
    return {"milestone": milestone, "story": story, "subtask": subtask}


EXPLORE_RESULT = {
    "summary": "the CLI composes board, dag, store, loader and engine for one card",
    "verification": {"fullSuite": ["uv run pytest"]},
}


def fake_runner(seen: list[tuple[str, dict[str, Any]]] | None = None, fail: str | None = None):
    """An `engine.AgentPhaseRunner` that returns canned results and runs nothing.

    §14's Engine tier: the agent phases are faked at the seam `engine.run_subtask`
    already injects, so no attempt directory, no adapter and no launcher exist in
    these tests at all.
    """

    def runner(phase, context, rendered):
        if seen is not None:
            seen.append((phase.name, dict(context)))
        if fail is not None and phase.name == fail:
            raise AgentPhaseFailed(
                phase.name, outcome="gate_failed", detail="canned gate failure"
            )
        if phase.name == "explore":
            return dict(EXPLORE_RESULT)
        return {"phase": phase.name, "ok": True}

    return runner


@requires_git
@requires_brd
def test_run_card_drives_the_task_workflow_to_done(project, cards):
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["status"] == "done"
    assert payload["card_id"] == cards["subtask"]
    assert payload["story_id"] == cards["story"]
    assert payload["failed_phase"] is None
    assert payload["detail"] is None


@requires_git
@requires_brd
def test_run_card_derives_its_branch_and_worktree_from_dag(project, cards):
    card = board.show(cards["subtask"], repo_dir=project)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m7",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["branch"] == dag.task_branch("m7", card)
    assert payload["base_branch"] == "main"
    assert Path(payload["worktree"]).is_absolute()
    assert Path(payload["worktree"]) == project.resolve() / ".claude" / "worktrees" / payload[
        "branch"
    ]
    assert Path(payload["worktree"]).is_dir()


@requires_git
@requires_brd
def test_a_relative_repo_dir_still_produces_an_absolute_worktree(project, cards, monkeypatch):
    """The option's default is `.`, and `worktree.ensure` refuses anything
    relative -- so the resolution has to happen in the CLI, not in the step."""
    monkeypatch.chdir(project)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=Path("."),
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(),
    )
    assert Path(payload["worktree"]).is_absolute()
    assert payload["status"] == "done"


@requires_git
@requires_brd
def test_run_card_hands_the_engine_the_gate_parameters_task_yaml_binds(project, cards):
    """§12's escape hatch is bound by name out of the engine's context, and
    `subtask_context` holds none of these four names."""
    seen: list[tuple[str, dict[str, Any]]] = []
    cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(seen),
    )

    _phase, context = seen[0]
    assert context["suite_cmds"] == []
    assert context["allow_no_verification"] is False
    assert context["caller_provided"] is False
    assert context["provided_verification"] is None
```

Add `board` and `AgentPhaseFailed` to the imports at the top of the file:

```python
from agent_manager import board, cli
from agent_manager.errors import AgentPhaseFailed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k run_card -v`
Expected: FAIL — `AttributeError: module 'agent_manager.cli' has no attribute 'run_card'`.

- [ ] **Step 3: Write the composition**

Append to `src/agent_manager/cli.py` (and extend the imports at the top of the file to the full set):

```python
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import typer

from agent_manager import board, dag, dispatch, engine, models
from agent_manager.harness.launcher import run_direct
from agent_manager.store import Store
from agent_manager.workflow.loader import Workflow, load_builtin
```

```python
WORKFLOW_NAME = "task"
"""The only document `run --card` drives. `--workflow` is §10's, not this card's."""


class RunnerFactory(Protocol):
    """How the command gets its `engine.AgentPhaseRunner`.

    A factory rather than a runner, because a real `dispatch.AgentRunner` needs
    the store, the workflow and three ids that do not exist until the run is
    already half set up -- and because a factory is the seam the tests replace
    to launch no harness at all (§14: the launcher is injected).
    """

    def __call__(
        self,
        *,
        workflow: Workflow,
        store: Store,
        run_id: str,
        story_id: str,
        card_id: str,
    ) -> engine.AgentPhaseRunner: ...


def default_runner_factory(
    *,
    workflow: Workflow,
    store: Store,
    run_id: str,
    story_id: str,
    card_id: str,
) -> engine.AgentPhaseRunner:
    """The production runner: real adapters, real roles, the direct launcher.

    `adapters` and `result_models` keep `AgentRunner`'s own defaults and
    `harness_map` stays empty, so every role falls back to `DEFAULT_HARNESS` and
    to the model its own `policy.toml` names (D6). Choosing a harness per role is
    `--harness`'s job, and `--harness` is not this card's.
    """
    return dispatch.AgentRunner(
        workflow=workflow,
        store=store,
        launcher=run_direct,
        run_id=run_id,
        story_id=story_id,
        card_id=card_id,
    )


def gate_context(commands: Sequence[str], allow_no_verification: bool) -> dict[str, Any]:
    """The gate parameters `builtin/task.yaml` binds and `subtask_context` lacks.

    `explore` gates on `verification_gate(suite_cmds, allow_no_verification,
    caller_provided)` and `exploration_output_gate(explore,
    provided_verification)`. Three of those four names come from the caller, and
    this is the caller. `bool()` is deliberate: the reducer tests
    `allow_no_verification is True`, so a truthy stand-in must not open the
    opt-out by accident. `caller_provided` is `False` and
    `provided_verification` is `None` because this card discovers no suite --
    per-run verification discovery is the milestone runner's, not this command's.
    """
    return {
        "suite_cmds": list(commands),
        "allow_no_verification": bool(allow_no_verification),
        "caller_provided": False,
        "provided_verification": None,
    }


def run_card(
    card_id: str,
    *,
    repo_dir: Path,
    base_branch: str = "master",
    branch_prefix: str = "m1",
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Drive one subtask card through `builtin/task.yaml` once, and report.

    The order is the spec's and it is load-bearing: the board reads happen before
    a run id exists (so a bad card leaves no run directory), and the run, story
    and subtask rows are written before the walk starts (so `status` and `resume`
    can see a run that died on its first phase).
    """
    root = resolve_repo_dir(repo_dir)
    card = board.show(card_id, repo_dir=root)
    if not card.parent_id:
        raise ParentlessCardError(
            f"card {card.id} ({card.title!r}) has no parent card; `run --card` drives "
            "a subtask of a story, and the story is what every run record is keyed by"
        )
    parent = board.show(card.parent_id, repo_dir=root)

    branch = dag.task_branch(branch_prefix, card)
    worktree = worktree_for(root, branch)
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)
    workflow = load_builtin(WORKFLOW_NAME)

    store = Store.open(root, run_id)
    try:
        run_record = models.Run(
            id=run_id,
            workflow=WORKFLOW_NAME,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(),
        )
        story = models.StoryRun(
            card_id=parent.id,
            title=parent.title,
            level=0,
            status="started",
            tip_branch=branch,
        )
        subtask = models.SubtaskRun(
            card_id=card.id,
            branch=branch,
            base_branch=base_branch,
            status="started",
            worktree_path=worktree,
        )
        store.record_run(run_record)
        store.record_story(story)
        store.record_subtask(story.card_id, subtask)

        factory = default_runner_factory if runner_factory is None else runner_factory
        runner = factory(
            workflow=workflow,
            store=store,
            run_id=run_id,
            story_id=parent.id,
            card_id=card.id,
        )
        summary = engine.run_subtask(
            workflow,
            store,
            story_id=parent.id,
            subtask=subtask,
            repo_dir=root,
            commands=commands,
            card=card,
            parent_story=parent,
            extra_context=gate_context(commands, allow_no_verification),
            agent_runner=runner,
        )

        store.record_run(run_record.model_copy(update={"status": summary.status}))
        store.record_story(story.model_copy(update={"status": summary.status}))
        store.record_subtask(
            story.card_id, subtask.model_copy(update={"status": summary.status})
        )

        # `AgentRunner` collects gate warnings out of band (dispatch.py:375):
        # its signature returns a result, so a warning has nowhere else to go,
        # and dropping them is the §12 failure this whole list exists to prevent.
        warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
        return {
            "run_id": run_id,
            "card_id": card.id,
            "story_id": parent.id,
            "branch": branch,
            "base_branch": base_branch,
            "worktree": str(worktree),
            "status": summary.status,
            "failed_phase": summary.failed_phase,
            "detail": summary.detail,
            "skipped": list(summary.skipped),
            "warnings": warnings,
        }
    finally:
        store.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS. If `git` or `brd` is missing the Engine-tier tests skip; the unit-tier ones must still pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): compose one run --card walk of the task workflow"
```

---

### Task 5: The `run` command — envelope, exit codes, durability

**Files:**
- Modify: `src/agent_manager/cli.py` (append the command)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: everything Task 3 and Task 4 produced, plus `paths.project_db_path(root)` and `paths.run_dir(run_id)` (paths.py:22, 29) and `store.Journal` (store.py:151) in the tests only.
- Produces: the Typer command `run` in `agent_manager.cli`, invoked as `agent-manager run --card <id> [--repo-dir .] [--base-branch master] [--branch-prefix m1] [--allow-no-verification] [--pretty]`, plus the module-level `HANDLED` tuple of exception types it converts into an `ok: false` envelope. Task 6 adds error-path tests against it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
from typer.testing import CliRunner

runner = CliRunner()


def _invoke(project: Path, card_id: str, *extra: str):
    """Run the Typer command with the agent runner faked out.

    The factory is patched on the module rather than passed as an option: the
    injection seam is `cli.default_runner_factory`, and patching it is what
    proves the command reaches for that name instead of building an
    `AgentRunner` inline.
    """
    return runner.invoke(
        cli.app,
        ["run", "--card", card_id, "--repo-dir", str(project), "--base-branch", "main", *extra],
    )


@requires_git
@requires_brd
def test_the_command_prints_an_ok_envelope_and_exits_zero(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "done"
    assert "\n" not in result.stdout.strip()


@requires_git
@requires_brd
def test_pretty_indents_the_same_envelope(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"], "--pretty")

    assert result.exit_code == 0
    assert "\n" in result.stdout.strip()
    assert json.loads(result.stdout)["data"]["status"] == "done"


@requires_git
@requires_brd
def test_an_escalated_subtask_is_ok_true_and_exit_one(project, cards, monkeypatch):
    monkeypatch.setattr(
        cli, "default_runner_factory", lambda **kwargs: fake_runner(fail="review")
    )
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ESCALATED
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "escalated"
    assert envelope["data"]["failed_phase"] == "review"
    assert "canned gate failure" in envelope["data"]["detail"]


@requires_git
@requires_brd
def test_a_failed_best_effort_board_phase_shows_up_in_warnings(project, cards):
    """§12: a run that says `done` while the card never moved is the exact
    failure this list exists to prevent. `rollup.set_status` is still a registry
    placeholder that raises, which is one honest way for the write to fail."""
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["status"] == "done"
    assert any("mark_in_progress" in warning for warning in payload["warnings"])
    assert any("mark_done" in warning for warning in payload["warnings"])


@requires_git
@requires_brd
def test_the_run_story_and_subtask_rows_land_in_the_project_db(project, cards):
    import sqlite3

    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    conn = sqlite3.connect(paths.project_db_path(project))
    try:
        run_row = conn.execute(
            "SELECT status, base_branch, branch_prefix FROM runs WHERE id = ?",
            (payload["run_id"],),
        ).fetchone()
        story_row = conn.execute(
            "SELECT card_id, status FROM stories WHERE run_id = ?", (payload["run_id"],)
        ).fetchone()
        subtask_row = conn.execute(
            "SELECT card_id, branch, base_branch, status FROM subtasks WHERE run_id = ?",
            (payload["run_id"],),
        ).fetchone()
    finally:
        conn.close()

    assert run_row == ("done", "main", "m1")
    assert story_row == (cards["story"], "done")
    assert subtask_row == (cards["subtask"], payload["branch"], "main", "done")


@requires_git
@requires_brd
def test_no_run_artifact_is_written_inside_the_repository(project, cards):
    """D4/§9: every artifact path comes from `paths.py`, which roots under
    `data_dir()`. The only thing this run may add to the repo is the worktree
    git itself registered."""
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert list(project.rglob("journal.jsonl")) == []
    assert list(project.rglob("*.db")) == []
    porcelain = _git(project, "status", "--porcelain").splitlines()
    assert all(".claude" in line or ".brd" in line for line in porcelain), porcelain
    assert (paths.run_dir(payload["run_id"]) / "journal.jsonl").is_file()


@requires_git
@requires_brd
def test_the_journal_opens_with_the_run_story_and_subtask_lines(project, cards):
    from agent_manager import store as store_module

    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    lines = store_module.Journal(payload["run_id"]).read()
    assert [line.event for line in lines[:3]] == [
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
    ]


@requires_git
@requires_brd
def test_the_rows_exist_even_when_the_first_agent_phase_blows_up(project, cards):
    """The guarantee `status` and `resume` are built on: a process that dies on
    its first dispatch still left a run behind."""
    import sqlite3

    def exploding_factory(**kwargs):
        def runner(phase, context, rendered):
            raise RuntimeError("the runner died on its first call")

        return runner

    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=exploding_factory,
    )

    assert payload["status"] == "escalated"
    assert payload["failed_phase"] == "explore"
    conn = sqlite3.connect(paths.project_db_path(project))
    try:
        assert conn.execute(
            "SELECT count(*) FROM runs WHERE id = ?", (payload["run_id"],)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM subtasks WHERE run_id = ?", (payload["run_id"],)
        ).fetchone()[0] == 1
    finally:
        conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "command or envelope or warnings or rows or journal or artifact" -v`
Expected: FAIL — `Usage: ... No such command 'run'`, and `AttributeError` on `cli.default_runner_factory` patching only if Task 4 was skipped (it was not).

- [ ] **Step 3: Write the command**

Append to `src/agent_manager/cli.py`:

```python
HANDLED: tuple[type[BaseException], ...] = (
    CliError,
    board.BoardError,
    WorkflowLoadError,
    EngineError,
    ValueError,
)
"""Everything the command turns into an `ok: false` envelope and exit 3.

`ValueError` is in the list for one concrete reason: `dag.short_id` raises a
bare one for a card id that is not a UUID, and a typed `--card` must not come
back as a traceback. Anything outside this tuple is a bug in this program and
should crash loudly with its stack intact.
"""


@app.command("run")
def run(
    card: str = typer.Option(..., "--card", help="The subtask card id to drive."),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository and brd board to work in."
    ),
    base_branch: str = typer.Option(
        "master", "--base-branch", help="The branch this subtask's branch is cut from."
    ),
    branch_prefix: str = typer.Option(
        "m1", "--branch-prefix", help="Milestone prefix for the derived branch name."
    ),
    allow_no_verification: bool = typer.Option(
        False,
        "--allow-no-verification",
        help="Proceed even when no verification suite is available (§12's opt-out).",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Drive one subtask card through the task workflow, end to end."""
    try:
        payload = run_card(
            card,
            repo_dir=repo_dir,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            allow_no_verification=allow_no_verification,
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
    if payload["status"] == "escalated":
        raise typer.Exit(EXIT_ESCALATED)
```

Extend the module's imports with the two error types the tuple names:

```python
from agent_manager.errors import EngineError
from agent_manager.workflow.registry import WorkflowLoadError
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add the run command, its envelope and its exit codes"
```

---

### Task 6: Error paths, the escape hatch, and the no-harness guarantee

**Files:**
- Test: `tests/test_cli.py` (append) — no source change is expected; if one of these fails, fix `cli.py` under the same task.

**Interfaces:**
- Consumes: `cli.run_card`, `cli.app`, `cli.EXIT_ERROR`, `cli.ParentlessCardError`, `cli.RepoDirError`, `cli.HANDLED` from Tasks 3-5; `steps.reducers.verification_gate` (reducers.py:30) and `paths.data_dir()` in the tests.
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
@requires_git
@requires_brd
def test_an_unknown_card_is_an_envelope_with_brds_own_message(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, "no-such-card")

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "BoardError"
    assert "no-such-card" in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs").exists()


@requires_git
@requires_brd
def test_a_parentless_card_is_refused_before_a_run_exists(project, cards, monkeypatch):
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["milestone"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "ParentlessCardError"
    assert cards["milestone"] in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs").exists()


@requires_git
@requires_brd
def test_a_failing_parent_lookup_is_an_envelope_and_leaves_no_run_directory(
    project, cards, monkeypatch
):
    """`board.show` runs twice, and the second call can fail on its own."""
    real_show = board.show

    def show(card_id, *, repo_dir=None):
        if card_id == cards["story"]:
            raise board.BoardError(
                "card not found", argv=["brd", "show", card_id], exit_code=1
            )
        return real_show(card_id, repo_dir=repo_dir)

    monkeypatch.setattr(cli.board, "show", show)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    assert json.loads(result.stdout)["error"]["type"] == "BoardError"
    assert not (paths.data_dir() / "runs").exists()


def test_a_repo_dir_that_is_not_a_directory_is_an_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--card",
            "cbe34d00-9d8d-4f41-9c94-f99e665771b0",
            "--repo-dir",
            str(tmp_path / "missing"),
        ],
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "RepoDirError"
    assert "missing" in envelope["error"]["message"]


@requires_git
@requires_brd
def test_a_card_id_that_is_not_a_uuid_is_an_envelope_not_a_traceback(
    project, cards, monkeypatch
):
    """`dag.short_id` raises a bare ValueError, and the run id is minted from the
    card id brd returned. A board that answers with a non-UUID id must not take
    the tool down with a stack trace."""

    def show(card_id, *, repo_dir=None):
        if card_id == cards["subtask"]:
            return models.Card(
                id="not-a-uuid", title="Odd card", status="todo", parent_id=cards["story"]
            )
        return models.Card(id=cards["story"], title="A story", status="todo")

    monkeypatch.setattr(cli.board, "show", show)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "ValueError"
    assert "not a card id" in envelope["error"]["message"]


@requires_git
@requires_brd
def test_an_engine_error_escaping_the_walk_reaches_the_operator(project, cards, monkeypatch):
    """`run_subtask` deliberately lets `EngineError` out rather than journalling
    it as a phase failure: an unbindable gate is a document bug, not an attempt."""

    def exploding(*args, **kwargs):
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(cli.engine, "run_subtask", exploding)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "EngineError"
    assert "explore" in envelope["error"]["message"]


@requires_git
@requires_brd
def test_a_workflow_that_will_not_load_reaches_the_operator_unchanged(
    project, cards, monkeypatch
):
    """A broken document is a load-time bug: the loader's own message names the
    workflow, the phase and the field, and the CLI must not paraphrase it."""

    def exploding(name, registry=None):
        raise WorkflowLoadError(
            "names 1 function(s) nobody registered", workflow="task", phase="verify"
        )

    monkeypatch.setattr(cli, "load_builtin", exploding)
    monkeypatch.setattr(cli, "default_runner_factory", lambda **kwargs: fake_runner())
    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "WorkflowLoadError"
    assert "verify" in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs").exists()


@requires_git
@requires_brd
def test_no_harness_is_ever_launched(project, cards, monkeypatch):
    """§14's adapter rule at the CLI seam: the launcher is injected, so a test
    that gets as far as launching one has already failed. `run_direct` is the
    only thing `default_runner_factory` would hand to a real `AgentRunner`."""

    def forbidden(*args, **kwargs):
        raise AssertionError("the CLI launched a harness process")

    monkeypatch.setattr(cli, "run_direct", forbidden)
    monkeypatch.setattr(cli.dispatch, "AgentRunner", forbidden)
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(),
    )
    assert payload["status"] == "done"


@requires_git
@requires_brd
def test_allow_no_verification_flips_the_gate_the_cli_supplies_arguments_for(
    project, cards
):
    """§12's escape hatch, asserted at the seam this card owns.

    The gate itself runs inside `dispatch.AgentRunner`, which these tests replace
    with a fake -- so the honest assertion is that the context the CLI hands the
    engine drives the *real* `verification_gate` to the two verdicts §12
    describes. The gate's own truth table is unit-tested in
    `tests/steps/test_reducers.py` and is not re-tested here.
    """
    from agent_manager.steps.reducers import verification_gate

    seen_off: list[tuple[str, dict[str, Any]]] = []
    cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        runner_factory=lambda **kwargs: fake_runner(seen_off),
    )
    off = seen_off[0][1]
    blocked = verification_gate(
        off["suite_cmds"], off["allow_no_verification"], off["caller_provided"]
    )
    assert blocked["blocked"] == "verification"

    seen_on: list[tuple[str, dict[str, Any]]] = []
    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        allow_no_verification=True,
        runner_factory=lambda **kwargs: fake_runner(seen_on),
    )
    on = seen_on[0][1]
    assert on["allow_no_verification"] is True
    assert (
        verification_gate(on["suite_cmds"], on["allow_no_verification"], on["caller_provided"])
        is None
    )
    assert payload["status"] == "done"
```

Extend the test file's imports with the two error types these tests raise directly:

```python
from agent_manager.errors import AgentPhaseFailed, EngineError
from agent_manager.workflow.registry import WorkflowLoadError
```

(the `AgentPhaseFailed` import already exists from Task 4; add `EngineError` beside it.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "unknown_card or parentless or parent_lookup or repo_dir or not_a_uuid or engine_error or will_not_load or harness or allow_no_verification" -v`
Expected: FAIL. The two cheapest failures to predict: `test_no_harness_is_ever_launched` fails with `AttributeError` if `run_direct` was imported under another name, and the envelope-type assertions fail if `HANDLED` is missing a type.

- [ ] **Step 3: Make them pass**

No source change should be needed. If one is:
- an envelope whose `type` is wrong means `HANDLED` is missing an entry — add the exact class, never a bare `Exception`;
- a run directory existing after a board failure means `Store.open` is being called before the two `board.show` calls — move it back below them, since `store.Journal.__init__` calls `paths.run_dir`, which creates the directory;
- `cli.run_direct` not existing means the import was written as `from agent_manager.harness import launcher` — restore `from agent_manager.harness.launcher import run_direct` so the seam has a patchable module-level name.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with skips only where `git` or `brd` is absent.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py src/agent_manager/cli.py
git commit -m "test(cli): pin the error paths, the escape hatch and the no-harness rule"
```

---

## Final verification

- [ ] **Run the full suite one last time**

Run: `uv run pytest`
Expected: PASS. This is the only verification command this repo has (CLAUDE.md); there is no lint and no typecheck step.

- [ ] **Sanity-check the console script resolves**

Run: `uv run agent-manager run --help`
Expected: the `run` command's help, listing `--card`, `--repo-dir`, `--base-branch`, `--branch-prefix`, `--allow-no-verification`, `--pretty` — and nothing else. Seeing `status`, `logs` or `resume` here means this card drifted into a sibling's scope.
