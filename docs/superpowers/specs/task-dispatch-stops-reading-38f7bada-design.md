# Subtask 38f7bada — dispatch stops reading harness usage into the journalled attempt

Parent story: d5aa963b "Remove dead cost/token tracking". Milestone design: `docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md` (§3.1, §4.1, §4.5, §5.2, §5.3 item 6). This is the safe first slice: nothing downstream yet assumes the `Attempt` cost/token fields are gone.

## Scope

In `src/agent_manager/dispatch.py`:

- Delete the `_usage(adapter, outcome) -> Usage | None` helper (opens `outcome.stdout_path`, swallows `OSError`, calls `adapter.parse_usage`).
- Delete its call site (`usage = _usage(target.adapter, outcome)`) and the three kwargs it feeds into the journalled `models.Attempt(...)`: `tokens_in=...`, `tokens_out=...`, `cost=...`. The attempt is built from `exit_code`, `duration`, status, dispatch, and paths only; the three fields fall back to their model defaults (`None`).
- In `from agent_manager.harness.base import HarnessAdapter, Outcome, Usage`, drop `Usage` only.
- Reword the module docstring sentence "stdout.log is captured as a log and is only ever handed to parse_usage, never parsed for a result" to say it is captured as a log and never read by the engine.

In `src/agent_manager/harness/launcher.py`: reword the docstring clause "never parses the log it wrote (that is parse_usage's job, on the adapter)" so it says nothing parses it (D4).

No deprecation shims, no placeholder variables (§4.1): delete outright.

## Out of scope (owned by siblings)

- 3ebd08fb (blocked on this subtask): deleting `Attempt.tokens_in/tokens_out/cost` from `models.py`, the `attempts` table columns in `store.py`, `load_run`/`_write_attempt_row` column lists, the `_RETIRED_ATTEMPT_KEYS` replay shim. Do not touch `models.py` or `store.py`.
- 7988f1db: deleting `harness.base.Usage`, `HarnessAdapter.parse_usage`, `ClaudeAdapter.parse_usage` and its regex helpers, and every fake-adapter `parse_usage` stub (including `FakeAdapter.parse_usage` in `tests/test_dispatch.py`). Do not touch `harness/base.py` or `harness/claude.py`.

## Observable behavior

- The engine never opens `outcome.stdout_path`. A launcher that leaves the log absent or unreadable produces exactly the same attempt as one that writes it.
- The journalled terminal `attempt_upsert` payload carries the launcher's `duration` and `exit_code` unchanged; `tokens_in`, `tokens_out`, `cost` are always `None` (the keys are still present because `Attempt` still declares them and the payload is `attempt.model_dump(mode="json")`, see store.py around line 1463).
- `adapter.parse_usage` is never called by dispatch, even though adapters still implement it.

Error paths: the only one that existed (`OSError` reading the log, swallowed) disappears with the read. No new error paths.

## Tests (all in `tests/test_dispatch.py`)

Test fixture changes:
- `FakeLauncher.stdout` default and the text `_outcome` writes: change `"usage: tokens\n"` to the neutral `"fake-harness ran\n"` (§3.1). The old string was bait for `parse_usage`.
- `FakeAdapter.parse_usage` stays for now (7988f1db removes it), so the `Usage` import on line 33 stays only while that stub still builds a `Usage`. Check this when implementing.

Tests:
1. Replace `test_usage_parsed_from_the_log_is_journalled_on_the_attempt` with `test_the_outcome_is_journalled_on_the_attempt`. The terminal `ok` `attempt_upsert` payload has `duration == 1.25` and `exit_code == 0`, and `tokens_in`, `tokens_out`, `cost` are all `None`. **Tier: unit (unmarked)**, driven through `FakeLauncher` and `FakeAdapter`, no subprocess.
   - Deviation from §5.2, required by this slice: §5.2's `set(payload) & {"tokens_in", "tokens_out", "cost"} == set()` cannot pass until 3ebd08fb removes the fields from `Attempt`. In this slice, assert the three values are `None`. The model still makes `FakeAdapter.parse_usage` return `Usage(11, 22, 0.5)` when "usage" appears in the log, so to show that nothing reads the log, the test must use a `FakeLauncher(stdout="usage: tokens\n", ...)` override. With that override, the `None` assertion proves dispatch no longer consults the adapter. 3ebd08fb tightens the assertion to key-absence.
2. New `test_the_engine_never_opens_the_harness_log` (§5.3 item 6): `FakeLauncher` returns an `Outcome` whose `stdout_path` points at a file that does not exist (a subclass or flag that skips the write). The phase still yields an `ok` attempt with `duration == 1.25`. **Tier: unit (unmarked)**, no subprocess.
3. Any other existing assertion in this file that loses a usage/cost check asserts `duration` instead (§4.5). Coverage is moved there, not dropped. **Tier: unchanged (unit)**.

Verification: `uv run pytest` (default unit + git tiers) passes.
