<!-- task-pipeline: validated -->
# Prove the pygents engine against a real harness (card a300ab2b)

Task 5.2 of Story 5 "Switch over" (milestone `84c3b532`, plan `docs/superpowers/plans/2026-09-25-pygents-engine.md`). This narrows the agreed pygents-engine design (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md` §9, §10) to one subtask. It is the gate that must clear before `7a744199` "Make pygents the only engine" can land.

## Scope

- Modify exactly one file: `tests/e2e/test_real_harness.py`. No `src/` changes, no new test module, no changes to `tests/e2e/conftest.py`, and no changes to the other opt-in real-harness modules (`test_real_harness_parallel.py`, `test_real_harness_milestone.py`, `test_real_harness_integrate.py`).
- Parametrize the existing single-engine test over `engine in {yaml, pygents}` by using the established pattern from `test_production_wiring.py:34`, `test_milestone_run.py:69` and `test_parallel_milestone.py:93`. That pattern is a module-local override `@pytest.fixture(scope="module", params=["yaml", "pygents"]) def engine(request) -> str: return request.param`, which shadows the conftest default (`conftest.py:172-180`, which returns `"yaml"`).
- Thread the engine into the module's `completed_run` override (currently `test_real_harness.py:107-124`). Add `engine` to its parameters and pass `engine=engine` to `cli.run_card(...)`. Everything else in that call stays unchanged, including `BRANCH_PREFIX`, `base_branch="main"` and `VERIFY_COMMANDS`. The conftest's `project` fixture is already keyed by `engine` (one repo and board per engine), so `cards`, `run_tree`, `agent_attempts` and `worktree` resolve per engine without further changes.
- The module docstring should be updated to say that the test now runs once per engine and how to select one engine (see the run command below). The fixture docstring may also say so. No other prose changes are needed.
- Keep unchanged: `pytestmark = pytest.mark.e2e`, the `real_claude` skip fixture, the absence of `fake_claude_bin`, and every assertion in `test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch`. That means the `status == "done"` check on both the payload and `board.show`, the single shared `Plan-Hash` trailer value across `main..HEAD`, per-phase result-file validation via `load_builtin("task")` plus `results.resolve_result_model`, and the non-vacuity check `validated == set(AGENT_PHASES)`. Per G10, these must hold identically on both engines. The `SubtaskSummary`, journal, phase/attempt rows and result files are the same shape.
- Out of scope, owned by siblings: deleting the yaml engine or `--engine`, or moving runtime helpers (`7a744199`); README and design-doc pointers (`e46098be`); critic-loop behaviour and its fake-claude scenarios (`058981d3`, done).

## Observable behaviour

- `uv run pytest` (default suite): the module is still deselected by `addopts = -m "not e2e"`, so it collects zero tests and the whole default suite stays green on both engines (card rule 2).
- `uv run pytest -m e2e tests/e2e/test_real_harness.py --collect-only` lists two test ids, `...[yaml]` and `...[pygents]`.
- The paid proof run is `uv run pytest -m e2e -k pygents -v tests/e2e/test_real_harness.py`. The guard is the `e2e` marker. There is no environment-variable guard: the plan's `AM_REAL_HARNESS=1` wording is stale, so do not add one. The run drives the toy subtask to `done` with a real `claude -p` on `engine="pygents"`.

## Error paths

- If `claude` is not on `PATH`, `real_claude` skips each parametrization with its existing message. Nothing new is needed.
- If the run does not reach `done`, the existing assertion messages (`failed_phase`, `detail`, `warnings`) surface the failure. A failing pygents real run blocks this card. Fixing an engine defect found this way is not in this card's scope. Report it rather than weakening assertions.

## Deliverable proof (human-run, not CI-asserted)

Per real-harness addendum R4/§3, the pipeline cannot verify the paid run, because it is excluded by default. The implementer must actually run the pygents parametrization and record its `run_id` (from `completed_run["run_id"]`, for example via the store or run directory) in the commit message. Commit subject: `test(e2e): prove the pygents engine against a real harness`, then a body line carrying the run id, then the required attribution trailers. The yaml parametrization does not need a paid rerun for this card.

## Tests

| Test | Tier (per design §14 as amended by real-harness R4 and pygents §9) |
|---|---|
| `test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch[yaml]`: existing assertions, unchanged | Tier 5, opt-in end-to-end (`pytest.mark.e2e`, excluded by default, real model). |
| `test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch[pygents]`: same assertions on the pygents engine | Tier 5, opt-in end-to-end. This is the one place §9 lets a real model run on pygents. |

No tier-4 (`test_production_wiring.py`, fake `claude`, default suite) or lower-tier tests are added or changed. Regression coverage is simply `uv run pytest` staying green.

Note: the upstream exploration summary was truncated at 8000 characters (it over-ran its brief) and stopped mid-reference list. This spec relies only on the parts that were received, and on the files it names, which were read directly.

---

# Prove the pygents engine against a real harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the one opt-in real-harness test (`tests/e2e/test_real_harness.py`) once per engine (`yaml`, `pygents`), then do one paid run on `pygents` and record its run id in the commit.

**Architecture:** Test-only change in tier 5 (opt-in end-to-end, `pytest.mark.e2e`). A module-local, module-scoped `engine` fixture with `params=["yaml", "pygents"]` shadows the conftest default (`tests/e2e/conftest.py:172-180`). The conftest's `project` fixture (`conftest.py:183-191`) already depends on `engine`, so each engine gets its own repo, board and `XDG_DATA_HOME`. The module's `completed_run` override passes `engine=engine` to `cli.run_card` (`src/agent_manager/cli.py:852`, which already accepts `engine: Engine = "yaml"`). No `src/` code changes.

**Tech Stack:** Python, pytest (fixture params, `-m` marker selection, `-k` id selection), `uv`.

**Spec:** `docs/superpowers/specs/task-prove-the-pygents-a300ab2b-design.md` (prepended verbatim above).

## Global Constraints

- Modify exactly one file: `tests/e2e/test_real_harness.py`. Do not touch `src/`, `tests/e2e/conftest.py`, `test_real_harness_parallel.py`, `test_real_harness_milestone.py`, `test_real_harness_integrate.py`, or `test_production_wiring.py`.
- Keep `pytestmark = pytest.mark.e2e`, the `real_claude` fixture, and every assertion in `test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch` exactly as they are. Do not add `fake_claude_bin`.
- Do not add an `AM_REAL_HARNESS` (or any other) environment-variable guard. The `e2e` marker is the guard.
- Use fixture params, not `@pytest.mark.parametrize`. This matches `test_production_wiring.py:34-39`.
- The yaml engine and `--engine` flag must still exist afterwards. Deleting them is sibling `7a744199`'s job.
- `uv run pytest` must stay green, and this module must be deselected in that run.
- Commit subject: `test(e2e): prove the pygents engine against a real harness`, followed by a body line carrying the pygents `run_id`, followed by the attribution trailers.
- If the paid pygents run fails, stop and report the failure. Do not weaken assertions and do not commit.

## Review Focus

- `-k pygents` must select only the pygents id. Nothing else in the module path or test name contains "pygents". Step 5 verifies this by collecting with `-k pygents` before paying.
- Running `uv run pytest -m e2e` without `-k` now runs two paid runs for this module instead of one. The module docstring must say so, so a human is not surprised by the cost (Step 3).
- There must be a way to recover the run id after the paid run, because the test does not print it. The plan pins `--basetemp` so the run directory `<basetemp>/e2e-pygents0/xdg/agent-manager/runs/<run_id>` is at a known path (`conftest.py:189-190` together with `src/agent_manager/paths.py:29-33`). See Steps 6 and 7.
- The default suite must not start collecting this module. Step 4 checks this with `uv run pytest`.
- If `claude` is missing, each parametrization must skip with the existing `real_claude` message and not error. This path is unchanged, and Step 5 exercises it on machines without `claude`.

---

### Task 1: Parametrize the real-harness test over both engines and prove pygents for real

**Files:**
- Modify: `tests/e2e/test_real_harness.py:1-23` (module docstring)
- Modify: `tests/e2e/test_real_harness.py:88-124` (add `engine` fixture, thread `engine` into `completed_run`)
- Test: `tests/e2e/test_real_harness.py` (tier 5, opt-in e2e, same file)

**Interfaces:**
- Consumes: `cli.run_card(card_id, *, repo_dir, base_branch, branch_prefix, commands, engine: Engine = "yaml")` (`src/agent_manager/cli.py:852-862`). The conftest fixtures `project(…, engine)`, `cards(project)`, `agent_attempts`, `worktree` (`tests/e2e/conftest.py:183-266`).
- Produces: fixture `engine(request) -> str` (module-scoped, params `["yaml", "pygents"]`). Test ids `test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch[yaml]` and `…[pygents]`.

- [ ] **Step 1: RED: confirm the module collects only one, unparametrized id**

Run: `uv run pytest -m e2e tests/e2e/test_real_harness.py --collect-only -q`
Expected: exactly one id, `tests/e2e/test_real_harness.py::test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch`, with no `[yaml]`/`[pygents]` suffix. This is the failing state for the spec's "lists two test ids" requirement. Collection costs nothing, because no fixture runs.

Then run: `uv run pytest -m e2e -k pygents tests/e2e/test_real_harness.py --collect-only -q`
Expected: `no tests collected` / `1 deselected` (exit code 5). The pygents run cannot be selected yet.

- [ ] **Step 2: GREEN: add the `engine` override and thread it into `completed_run`**

In `tests/e2e/test_real_harness.py`, insert this fixture directly above the existing `@pytest.fixture(scope="module") def real_claude()` (currently line 90):

```python
@pytest.fixture(scope="module", params=["yaml", "pygents"])
def engine(request) -> str:
    """Overrides the conftest's `engine`: the one real run happens once per
    engine, each on its own repo and board (pygents spec §9). Fixture params,
    not a parametrize mark, so no marker other than `e2e` reaches this module.
    Select one engine with `-k yaml` or `-k pygents`."""
    return request.param
```

Then replace the whole `completed_run` fixture (currently lines 107-124) with:

```python
@pytest.fixture(scope="module")
def completed_run(real_claude, project, cards, engine) -> dict[str, Any]:
    """One real, paid `cli.run_card` on `engine` -- no `runner_factory`, no
    fake on `PATH`.

    Overrides the conftest fixture of the same name, so `run_tree`,
    `agent_attempts` and `worktree` resolve against THIS run. `fake_claude_bin`
    is deliberately absent from the parameter list: `cli.default_runner_factory`
    (`cli.py:588`) builds the real `dispatch.AgentRunner` over
    `harness/launcher.py:94` `run_direct`, which resolves `claude` on `PATH`,
    and the whole point of this module is that what it finds there is real.
    """
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix=BRANCH_PREFIX,
        commands=list(VERIFY_COMMANDS),
        engine=engine,
    )
```

Leave the test function, `real_claude`, the constants and the helpers unchanged.

- [ ] **Step 3: Update the module docstring**

Replace the paragraph at `tests/e2e/test_real_harness.py:7-11`:

```python
This module costs real money every time it runs, so `pyproject.toml`'s
`addopts` carries `-m "not e2e"` and the marker below opts it out of the
default suite. To run it: `uv run pytest -m e2e` (a bare path invocation such
as `uv run pytest tests/e2e/test_real_harness.py` is still deselected by
`addopts` and exits 5 -- pass `-m e2e` alongside the path).
```

with:

```python
This module costs real money every time it runs, so `pyproject.toml`'s
`addopts` carries `-m "not e2e"` and the marker below opts it out of the
default suite. To run it: `uv run pytest -m e2e` (a bare path invocation such
as `uv run pytest tests/e2e/test_real_harness.py` is still deselected by
`addopts` and exits 5 -- pass `-m e2e` alongside the path).

The test runs once per engine (`[yaml]` and `[pygents]`, pygents-engine spec
§9), so `-m e2e` alone pays for TWO real runs of this module. To pay for one,
select an engine by id:
`uv run pytest -m e2e -k pygents -v tests/e2e/test_real_harness.py`.
```

Keep the rest of the docstring as it is.

- [ ] **Step 4: Verify collection now shows both ids, and the default suite is green**

Run: `uv run pytest -m e2e tests/e2e/test_real_harness.py --collect-only -q`
Expected: exactly two ids:
```
tests/e2e/test_real_harness.py::test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch[yaml]
tests/e2e/test_real_harness.py::test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch[pygents]
```

Run: `uv run pytest`
Expected: all pass, and `tests/e2e/test_real_harness.py` contributes zero tests because it is deselected. This is the same pass/deselect totals as before the change, plus nothing from this module.

- [ ] **Step 5: Verify `-k pygents` selects only the pygents id (still free)**

Run: `uv run pytest -m e2e -k pygents tests/e2e/test_real_harness.py --collect-only -q`
Expected: exactly one id, `…[pygents]`, with `1 deselected` (the yaml one). If more than one id is selected, stop and tighten the selector to `-k "pygents and real_claude"` before paying.

- [ ] **Step 6: The paid proof run on pygents (human-run, costs money)**

Pin the pytest base temp so the run directory can be found afterwards:

```bash
BASETEMP="$(mktemp -d)/am-e2e"
uv run pytest -m e2e -k pygents -v --basetemp="$BASETEMP" tests/e2e/test_real_harness.py
```

Expected: `test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch[pygents] PASSED`, `1 passed, 1 deselected`.
If it prints `SKIPPED` (no `claude` on `PATH`), install or expose `claude` and re-run, because a skip is not proof. If it FAILS, stop: do not edit assertions and do not commit. Report the assertion tuple (`failed_phase`, `detail`, `warnings`) as a blocker for this card.

- [ ] **Step 7: Recover the pygents run id**

`project` calls `tmp_path_factory.mktemp("e2e-pygents")`, which creates `e2e-pygents0`, and sets `XDG_DATA_HOME` to `<that>/xdg` (`conftest.py:189-190`). Runs live under `<XDG_DATA_HOME>/agent-manager/runs/<run_id>` (`src/agent_manager/paths.py:29-33`).

```bash
ls "$BASETEMP/e2e-pygents0/xdg/agent-manager/runs/"
```

Expected: exactly one directory name, which is the pygents `run_id`. Write it down for Step 8.

- [ ] **Step 8: Commit, with the run id in the body**

Replace `<RUN_ID>` below with the literal directory name printed by Step 7 before you run the command:

```bash
git add tests/e2e/test_real_harness.py
git commit -m "$(cat <<'EOF'
test(e2e): prove the pygents engine against a real harness

Pygents real-harness run id: <RUN_ID>
(uv run pytest -m e2e -k pygents -v tests/e2e/test_real_harness.py, passed)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
)"
```

Then run `git show --stat HEAD`.
Expected: exactly one file changed, `tests/e2e/test_real_harness.py`, and the message body contains the real run id with no `<RUN_ID>` left in it.
