# Task 3.1 — Add pygents and the pool codec (card 8ae25085)

Narrows `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 3.1 (lines 614-732) and `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` §3/§5/§9 to this one subtask. Parent story: f9c19dc3 "Run a workflow on pygents".

## Scope

In scope:

- `pyproject.toml`: add `pygents>=0.6.7` as a runtime dependency (the only new runtime dependency of the milestone), add `pytest-asyncio` to the `dev` dependency group, and add `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`. The repo has no existing async-test convention, so this is the convention. Existing `addopts` (`--import-mode=importlib -m "not e2e"`), `testpaths` and `markers` stay unchanged.
- New package `src/agent_manager/runtime/` with an empty `__init__.py` and `context.py`.
- New test package `tests/runtime/` with `__init__.py` and `test_context.py`.

Out of scope (sibling cards own these; do not touch): `dispatch.py` and `prompt.py` (1bbb532d); `runtime/state.py`, `runtime/bridge.py`, `runtime/compile.py`, launcher `on_spawn`/`kill_tree`, and `engine.run_one_step` (023d918e); `runtime/engine.run_subtask` and the engine-parametrised `tests/test_engine.py` (2853e536); Goto loops and rendering of `feedback` in prompts (b904b9e7). `workflow/phases.py`, `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads are unchanged.

## Observable behaviour of `agent_manager.runtime.context`

The module docstring states the invariant: the pool holds JSON only, and this module is the single codec between the pool and typed Python.

- `SUBTASK = "subtask"` and `SKIPPED = "skipped"` are module constants (reserved pool item ids).
- `encode(value: Any) -> Any` returns a JSON-serialisable value. It checks in this order:
  1. pydantic `BaseModel`: `{"$model": "<module>:<qualname>", "data": value.model_dump(mode="json")}`. `by_alias` is not used, so field names are kept (for example `Verification.full_suite`, not `fullSuite`). Gates that need aliases still get them from `dispatch.gate_values`.
  2. `PurePath`: `{"$path": str(value)}`.
  3. `datetime`: `{"$dt": value.isoformat()}`.
  4. `Mapping`: a dict with `str` keys and encoded values.
  5. `list`/`tuple`: a list of encoded values.
  6. Anything else passes through unchanged.
- `decode(value: Any) -> Any` is the inverse. For a dict, it checks the tags in this order: `$model` imports the module with `importlib` and walks the dotted qualname with `getattr`, then calls `model_validate(data)`. `$path` becomes `Path`. `$dt` becomes `datetime.fromisoformat`. Any other dict is decoded key by key, lists element by element, and scalars pass through. Tuples come back as lists. That is accepted: the round-trip guarantee covers dicts, lists, scalars, paths, datetimes and models.
- `seed_item(binding: Mapping[str, Any]) -> ContextItem` returns `ContextItem(id=SUBTASK, description="fixed subtask context", content=encode(dict(binding)))`.
- `binding_table(pool: ContextPool, memory: ContextQueue, phase: str) -> dict[str, Any]` is synchronous. It builds the table in three steps:
  1. Start with the decoded seed content.
  2. For every pool item whose id is not `SUBTASK` or `SKIPPED`, set `table[item.id]` to the decoded content.
  3. Set `table["feedback"]` to a list of `dict` copies of the queue items whose content is a `Mapping` with `content["for"] == phase`, in queue order. The list is empty when nothing matches.

Within `src/agent_manager/`, only `src/agent_manager/runtime/` imports `pygents`; this card adds no pygents import to any other production module. `tests/runtime/test_context.py` does import `pygents` directly (`ContextPool`, `ContextQueue`, `ContextItem`) to exercise the codec against real containers, per the Tests section below.

## Error paths

No new error handling is added. Failures propagate as the underlying libraries raise them:

- A `$model` tag whose module or attribute cannot be resolved raises `ImportError` or `AttributeError`.
- Model data that no longer validates raises pydantic `ValidationError`.
- A malformed `$dt` raises `ValueError`.
- A pool with no `SUBTASK` seed fails with whatever `pool.get` raises.

Values that are not JSON-safe and not covered by a tag pass through `encode` unchanged. `json.dumps` rejects them at checkpoint time, which is the intended loud failure.

## Tests

Placement rule: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 places tests by subject, mirroring the source tree. Pure functions get unit tests beside their logic, and only the single real-harness test is `e2e` (opt-in). Pygents-engine spec §9 adds that no test calls a model. `context.py` is pure codec and data-shaping code over in-memory pygents containers, so both tests belong in the **default-suite unit tier** in `tests/runtime/test_context.py`: no git repo, no board, no fake adapter, no `e2e` marker.

1. `test_round_trip_through_json` (unit, default suite). Encodes a dict holding a `Path`, a timezone-aware `datetime` and an `ExploreResult` with a nested `Verification` (`src/agent_manager/results.py:39-53`), passes it through `json.dumps`/`json.loads`, decodes it, and asserts equality with the original. This proves plan Review Focus item 3: results holding `Path` or `datetime` survive checkpointing. It also proves that the `fullSuite` serialization alias does not break the round-trip.
2. `test_binding_table_rebuilds_seed_results_and_feedback` (unit, default suite, async via `asyncio_mode = "auto"`). Uses a real `pygents.ContextPool` and `ContextQueue(limit=10)`. It seeds `{"branch", "worktree": Path}`, adds a `spec` result item, and appends two feedback items (`for: spec` and `for: plan`). It asserts that `binding_table(..., "spec")` returns the decoded `Path`, the `spec` result, and only the `spec` feedback item.

The exact test bodies are given verbatim in the plan (lines 633-662).

The pygents-engine spec §9 also describes a test that `binding_table` matches the old engine's table per phase of TASK and INTEGRATE. That needs the compiler and `run_subtask`, so it is deferred to the sibling cards (023d918e and 2853e536) and not added here.

## Verification

`uv run pytest` must be fully green, including the existing yaml-engine suite. This card adds no engine-selectable behaviour.

## Note on inputs

The exploration findings given to this stage were cut off at 8000 characters, partway through the test-placement paragraph. The placement tier above comes from the cited §14 rule and pygents spec §9 directly, not from the missing text.
