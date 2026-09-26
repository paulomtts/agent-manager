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
