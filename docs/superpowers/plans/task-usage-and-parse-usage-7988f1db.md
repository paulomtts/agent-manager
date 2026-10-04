<!-- task-pipeline: validated -->
# Usage and parse_usage leave the harness adapter protocol (card 7988f1db)

Story: d5aa963b "Remove dead cost/token tracking". Parent design: `docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md` §3.1 (source, documentation and test rows), §4.4, §5.1. This card is the last slice of that design. It narrows the design and adds nothing new.

## Base

This worktree (`.claude/worktrees/m19/task-usage-and-parse-usage-7988f1db`) already contains the landed sibling work. 38f7bada removed `dispatch._usage` and its call site, and `dispatch.py` no longer imports `Usage`. 3ebd08fb removed the `Attempt` fields, the `attempts` columns and the `_RETIRED_ATTEMPT_KEYS` replay shim. Do not redo or touch any of that. The master checkout is stale and is not the base. Line numbers below refer to this worktree.

## Scope

After this card, `HarnessAdapter` has exactly three members: `name`, `capabilities` and `build_command`. `Usage` no longer exists anywhere in `src/` or `tests/`. Nothing in `src/` reads `stdout.log` except `am logs`, which shows it to an operator. This makes D4 strictly stronger (§4.4).

### Source

- `src/agent_manager/harness/base.py`: delete `class Usage` (31-50), `HarnessAdapter.parse_usage` (98), and the `from pydantic import BaseModel, ConfigDict, Field` import (26), which only `Usage` used. Reword the docstrings at 4, 11-14, 16-19 and 88-89 so they no longer mention `Usage` or `parse_usage`. `Outcome`'s "no parsed usage" sentence becomes "no log contents".
- `src/agent_manager/harness/claude.py`: delete the log scanner (44-102: `_key`, `_VALUE`, `_TOKENS_IN`, `_TOKENS_OUT`, `_COST`, `_tokens`, `_cost`) and `ClaudeAdapter.parse_usage` (158-188). Delete the imports this leaves unused (18-23): `math`, `re`, `pydantic.ValidationError` and `harness.base.Usage`. Reword the docstrings at 13-15 ("`parse_usage` never raises…") and 106 ("reads usage back out").
- `src/agent_manager/harness/launcher.py:10`: change "(that is `parse_usage`'s job, on the adapter)" to "(nothing does: D4)".

Runtime behaviour does not change. The sibling card already made `parse_usage` uncalled, so no CLI output, journal payload or error path changes. The only error path this card introduces is at import time: any leftover reference to `base.Usage` or `parse_usage` must fail as an `AttributeError` or `ImportError` during collection. That failure is how we detect an incomplete removal.

### Documentation

- `README.md:504`, the `attempt_upsert` row: replace "with its cost, token and duration fields" with "with its `exit_code` and `duration`". This uses the parent spec's exact wording (§3.1 Documentation, row 1).
- `docs/superpowers/specs/2026-09-23-agent-manager-design.md:366`, the §9 tree: drop `tokens_in, tokens_out, cost` from the line and add a one-line pointer to `2026-10-03-remove-cost-tracking-design.md`.
- Same file, 563-565, the §17 open question "Cost accounting on non-Claude harnesses": mark it resolved by the 2026-10-03 spec (closed, because `am` keeps no per-phase cost).
- Same file, line 27 (motivation 1, "Cost and latency"): leave it unchanged.
- **Gap in the parent spec, included here:** the §8 Protocol block at line 316 still prints `def parse_usage(self, stdout: str) -> Usage | None: ...`. `test_the_protocol_declares_exactly_the_three_members_the_spec_prints` pins the Protocol to "the members the spec prints". If that line stays, the test and the spec contradict each other. Delete the line. The parent spec's §3.1 does not list it, but §4.4 ("the protocol becomes `name`, `capabilities`, `build_command`") requires it.

### Tests

Fake-adapter stubs: delete every `parse_usage` method. That is the six fake adapters named in §4.4 plus `StubAdapter`:

- `tests/harness/test_base.py:34-35` (`StubAdapter`)
- `tests/test_dispatch.py:218-219` (`FakeAdapter`)
- `tests/test_bases.py:378`
- `tests/test_engine.py:2148`
- `tests/test_integrate_workflow.py:192`
- `tests/test_integration.py:259`
- `tests/runtime/test_exactly_once.py:132`

Also drop `Usage` from the import at `tests/test_dispatch.py:33`.

`tests/test_dispatch.py`, which the exploration flagged for evaluation:

- Delete both `UsageRefusingAdapter` (757-761) and `test_dispatch_never_asks_the_adapter_for_usage` (764-774). Once the Protocol has no `parse_usage`, the refusal method would be a method that nothing could call, so the test would pass vacuously. `test_the_engine_never_opens_the_harness_log` (711-754) already pins the D4 property more strongly.
- At 678 and 780, replace the `stdout="usage: tokens\n"` bait with the neutral `"fake-harness ran\n"`, which matches the parent spec's §3.1 row for the same string. Reword the comment at 673-676 so it no longer refers to `FakeAdapter`'s `parse_usage`.

`tests/e2e/fake_claude.py:820-822`: reword the comment to "stdout is a log, never a channel (D4); nothing reads it". This is a comment-only change. If that §3.1 row has not already been done by a sibling card, it belongs here, because the comment names `parse_usage`.

#### Test list, with tiers

Tier rule from CLAUDE.md: a test's tier depends on what it spawns, not which directory it is in. Every test below runs against pure data or an injected fake (`StubAdapter`, `FakeAdapter`, `FakeLauncher`, `ast` over source text), and none spawns a process. So every test is **unit**: no mark, default suite. None of them live under `tests/steps/` or `tests/e2e/`, so the conftest auto-marking does not apply.

| Test | Change | Tier |
|---|---|---|
| `tests/harness/test_base.py::test_the_protocol_declares_exactly_the_four_members_the_spec_prints` | rename to `…exactly_the_three_members…`; assert `== {"name", "capabilities", "build_command"}` | unit |
| `tests/harness/test_base.py::test_the_protocol_methods_have_the_signatures_the_spec_prints` | delete the `parse_usage` block (71-74); keep `build_command` | unit |
| `tests/harness/test_base.py::test_a_structural_stub_satisfies_the_adapter_interface` | delete the assertion at 96 | unit |
| `tests/harness/test_base.py` seven `Usage` tests (107-156: `round_trips`, `is_empty`, `fields_match_attempt`, `is_frozen`, `rejects_negative`, `rejects_inf_nan`, `rejects_unknown_key`) | delete | — |
| `tests/harness/test_base.py::test_outcome_carries_no_result_payload_and_no_usage` (201-205) | keep; rename `…_and_no_log_contents`; reword the comment so it no longer says "usage" or "parses usage" | unit |
| `tests/harness/test_claude.py::test_the_adapter_satisfies_the_protocol_structurally` | delete `assert callable(adapter.parse_usage)` (52) | unit |
| `tests/harness/test_claude.py` module docstring (8), `Usage` import (22) | reword / delete | — |
| `tests/harness/test_claude.py` `FULL_LOG` and ten `parse_usage` tests (195-297, including `hostile_logs_never_raise` ×7) | delete | — |
| `tests/harness/test_claude.py` import audit (300-379: `_imported_names`, `FORBIDDEN_IMPORTS`, both parametrized guard tests, `test_the_adapter_launches_nothing_itself`) | keep unchanged (see below) | unit |
| `tests/harness/test_claude.py::test_the_adapter_imports_neither_re_nor_math_nor_pydantic` (new, after `test_the_adapter_launches_nothing_itself`) | add: `_imported_names(source) & {"re", "math", "pydantic"} == set()` (parent §5.3 item 7, delegated here by 3ebd08fb's own scope section) | unit |
| `tests/test_dispatch.py::test_the_outcome_is_journalled_on_the_attempt` | neutral stdout and reworded comment only; assertions unchanged | unit |
| `tests/test_dispatch.py::test_a_timed_out_attempt_journals_its_duration_and_no_usage` | neutral stdout only | unit |
| `tests/test_dispatch.py::test_dispatch_never_asks_the_adapter_for_usage` | delete, with `UsageRefusingAdapter` | — |

**Import audit: deliberate departure from the exploration findings.** The exploration proposed adding `re`, `math` and pydantic to `FORBIDDEN_IMPORTS` and flipping the "legitimately needs" parametrize cases. This spec does not do that. Parent §3.1 decides this row explicitly: "keep; unaffected (`re`/`math` were never forbidden, and removing them cannot fail it)". `FORBIDDEN_IMPORTS` guards against launching a process, not against parsing, and the "clears what the adapter legitimately needs" cases test that the guard is not over-broad on hypothetical source strings. Leave the runtime-half allowlist `{"math", "re"}` at 375-378 as it is, unchanged. It only permits those modules and does not require them.

**One new test, not zero.** Parent §5.3 item 7 ("Import hygiene") is new and belongs here, not to a sibling: 3ebd08fb's own spec states its scope boundary explicitly — "import hygiene … belong[s] to sibling 7988f1db, which is blocked on this card." Add `test_the_adapter_imports_neither_re_nor_math_nor_pydantic` to `tests/harness/test_claude.py`, next to the existing import-audit tests (after `test_the_adapter_launches_nothing_itself`): read `claude_module`'s source with `_imported_names` and assert the result is disjoint from `{"re", "math", "pydantic"}`. This is distinct from the existing `FORBIDDEN_IMPORTS` guard (which checks for process-launching imports and is deliberately left alone, above) — it instead pins that this card's own deletions actually removed the regex parser's imports, so a future edit cannot quietly reintroduce `parse_usage`'s machinery without this test catching it. **Tier: unit** (`ast` over source text, no subprocess).

## Done when

- A grep for `parse_usage` or `\bUsage\b` (excluding Typer/brd "Usage:" strings and dated `task-*` / 2026-10-02 history docs) finds nothing in `src/`, `tests/`, `README.md` or the 2026-09-23 design spec.
- `uv run pytest` (unit + git) passes.

## Note on inputs

The exploration summary given to this stage was cut off at 8000 of 11091 characters, mid-sentence in its "spec-stated invariants" list. That truncation is evidence the upstream stage overran its brief. Everything above was re-checked against the parent spec and this worktree's code rather than inferred from the missing text.

---

# Usage and parse_usage leave the harness adapter protocol — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete `Usage` and `HarnessAdapter.parse_usage` (and the Claude adapter's log scanner) so the adapter Protocol has exactly `name`, `capabilities`, `build_command`, and correct the docs that still describe cost/token tracking.

**Architecture:** Pure deletion plus docstring/doc rewording. `src/agent_manager/harness/base.py` and `src/agent_manager/harness/claude.py` lose `Usage`/`parse_usage` and their now-unused imports in one task, together with every test that imports `Usage` (otherwise collection breaks). Vestigial `-> None` stubs on other fakes, the fake-claude comment, and the docs follow in separate tasks. No runtime behaviour changes.

**Tech Stack:** Python 3, pytest, uv, pydantic (no longer imported by the harness layer).

**Spec:** `docs/superpowers/specs/task-usage-and-parse-usage-7988f1db-design.md` (prepended above). Parent: `docs/superpowers/specs/2026-10-03-remove-cost-tracking-design.md`.

**Working directory:** every command below runs from the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-usage-and-parse-usage-7988f1db` on branch `m19/task-usage-and-parse-usage-7988f1db` (cut from `m19/task-attempt-drops-cost-3ebd08fb`). All paths are relative to that root.

## Global Constraints

- After this card `HarnessAdapter.__protocol_attrs__ == {"name", "capabilities", "build_command"}`.
- `Usage` exists nowhere in `src/` or `tests/` (Typer/brd `"Usage:"` strings excepted).
- Nothing in `src/` reads `stdout.log` except `am logs` (D4, parent §4.4).
- Runtime behaviour, CLI output and journal payloads do not change.
- `FORBIDDEN_IMPORTS`, both parametrized import-guard tests, and the runtime-half allowlist `{"math", "re"}` in `tests/harness/test_claude.py` stay exactly as they are.
- `docs/superpowers/specs/2026-09-23-agent-manager-design.md:27` (motivation 1, "Cost and latency") is not edited.
- Do not touch `dispatch.py`, `models.py`, `store.py` or anything the sibling cards 38f7bada / 3ebd08fb own.
- `src/agent_manager/harness/launcher.py:10` already reads "never parses the log it wrote (nothing does: D4)" on this base — verified; no edit needed there.
- Every test in this plan is unit tier (no mark): nothing here spawns a process.
- Verification: `uv run pytest`.

## Review Focus

- A leftover `from agent_manager.harness.base import Usage` somewhere outside the files listed (a module nobody grepped) — expected: collection fails with `ImportError`, not a silent survival. Pinned by the new `test_usage_is_gone_from_the_adapter_layer` in Task 1 plus the Task 4 grep.
- An adapter (third-party or a future `codex.py`) that still defines an extra `parse_usage` method — expected: it still satisfies the narrowed Protocol structurally, since extra members are allowed. Already pinned by `test_a_structural_stub_satisfies_the_adapter_interface` (stub with exactly the three members) and `test_the_adapter_satisfies_the_protocol_structurally`; no new test needed.
- A harness log that is absent, a directory, or non-UTF-8 — expected: the engine still never opens it and the attempt still journals `ok`. Already pinned by `tests/test_dispatch.py::test_the_engine_never_opens_the_harness_log` (parametrized ×3), which this plan keeps unchanged.
- An operator running `am logs` after this change — expected: it still shows `stdout.log` (the one legitimate reader). Already pinned by the `test_logs_*` tests in `tests/test_cli.py` (e.g. `test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase`, `test_logs_reports_an_attempt_whose_stdout_was_never_written`); this plan does not touch `cli.py`.
- A future edit re-adding a regex usage scanner to `claude.py` — expected: a test fails. Pinned by the new `test_the_adapter_imports_neither_re_nor_math_nor_pydantic` in Task 1.

---

### Task 1: `Usage` and `parse_usage` leave `base.py` and `claude.py`

This task is one unit because deleting `Usage` from `base.py` breaks import of `claude.py`, `tests/harness/test_claude.py` and `tests/test_dispatch.py` at collection, and deleting `ClaudeAdapter.parse_usage` alone would fail `test_the_adapter_satisfies_the_protocol_structurally` while the Protocol still lists it.

**Files:**
- Modify: `src/agent_manager/harness/base.py` (whole file rewritten, 1-98)
- Modify: `src/agent_manager/harness/claude.py` (whole file rewritten, 1-188)
- Test: `tests/harness/test_base.py` (lines 1-205)
- Test: `tests/harness/test_claude.py` (lines 1-52, 195-297, append after 379)
- Test: `tests/test_dispatch.py` (lines 33, 218-219, 672-678, 757-774, 779-781)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `agent_manager.harness.base` exports `Outcome` and `HarnessAdapter` only (no `Usage`); `HarnessAdapter` members are `name: str`, `capabilities: frozenset[str]`, `build_command(self, d: Dispatch) -> list[str]`. `ClaudeAdapter` has `name`, `capabilities`, `build_command` and nothing else; `claude.py` imports only `from agent_manager.models import Dispatch`.

- [ ] **Step 1: Write the failing tests in `tests/harness/test_base.py`**

Replace the module docstring's line 7 sentence so the docstring (lines 1-11) reads:

```python
"""Behaviour of the harness adapter protocol and its data type (design §8
lines 306-318, card 55e503e0).

Unit tier per design §14 line 484 ("Adapters -- `build_command` is pure and
asserted per harness; the launcher is injected, so no harness is executed in
unit tests"). Nothing here spawns a process or touches disk: the module is a
Protocol and one value type. The stub adapter below is the interface's only
consumer in this card -- the real adapters are sibling cards, and the fake
adapter that returns canned result files belongs to the engine card (§14
line 486).
"""
```

Delete line 18 `from pydantic import ValidationError` (only the `Usage` tests used it).

Delete `StubAdapter.parse_usage` (lines 34-35) so the class ends at `build_command`:

```python
class StubAdapter:
    """The smallest thing that is a `HarnessAdapter`. Structural only: it
    inherits from nothing."""

    name = "stub"
    capabilities = frozenset({"browser"})

    def build_command(self, d: Dispatch) -> list[str]:
        return [self.name, "--model", d.model, "--result", str(d.result_path)]
```

Replace `test_the_protocol_declares_exactly_the_four_members_the_spec_prints` (lines 50-59) with:

```python
def test_the_protocol_declares_exactly_the_three_members_the_spec_prints():
    # §8 lines 306-313 are the contract every adapter card codes against. A
    # fourth member added here, or a rename, would silently break a sibling
    # adapter written against the printed version. `parse_usage` left with
    # the 2026-10-03 remove-cost-tracking spec (§4.4): nothing reads the log.
    assert set(base.HarnessAdapter.__protocol_attrs__) == {
        "name",
        "capabilities",
        "build_command",
    }
```

In `test_the_protocol_methods_have_the_signatures_the_spec_prints`, delete lines 71-74 (the `parse = inspect.signature(base.HarnessAdapter.parse_usage)` block and its three asserts), leaving:

```python
def test_the_protocol_methods_have_the_signatures_the_spec_prints():
    # `base.py` has no `from __future__ import annotations`, so these come back
    # evaluated rather than as strings. That is deliberate: the objects are
    # what a sibling adapter card has to match.
    build = inspect.signature(base.HarnessAdapter.build_command)
    assert list(build.parameters) == ["self", "d"]
    assert build.parameters["d"].annotation is Dispatch
    assert build.return_annotation == list[str]
```

In `test_a_structural_stub_satisfies_the_adapter_interface`, delete line 96 `assert adapter.parse_usage("no usage in this log") is None`; the test now ends with `assert all(isinstance(word, str) for word in argv)`.

Delete the seven `Usage` tests, lines 107-156 inclusive: `test_usage_round_trips_a_full_payload`, `test_usage_is_empty_when_a_harness_reports_nothing`, `test_usage_fields_match_the_attempt_fields_they_are_copied_into`, `test_usage_is_frozen`, `test_usage_rejects_negative_counts_and_cost`, `test_usage_rejects_an_infinite_or_nan_cost`, `test_usage_rejects_an_unknown_key`. In their place (directly after `test_the_protocol_is_not_runtime_checkable`) add:

```python
def test_usage_is_gone_from_the_adapter_layer():
    # 2026-10-03 remove-cost-tracking §4.4: `am` keeps no per-phase cost, so
    # the adapter layer has no usage type to trade in. A leftover reference
    # to the old usage model anywhere must fail loudly at import, not linger.
    assert not hasattr(base, "Usage")
```

Replace `test_outcome_carries_no_result_payload_and_no_usage` (lines 201-205) with:

```python
def test_outcome_carries_no_result_payload_and_no_log_contents():
    # The launcher never opens the result file and never reads the log it
    # wrote (nothing does: D4); keeping both off this type is what lets one
    # launcher serve every adapter.
    fields = {field.name for field in dataclasses.fields(base.Outcome)}
    assert fields == {"argv", "exit_code", "timed_out", "duration", "stdout_path"}
```

- [ ] **Step 2: Write the failing test and drop the `parse_usage` tests in `tests/harness/test_claude.py`**

Replace lines 7-8 of the module docstring so lines 4-11 read:

```python
Unit tier per design §14 line 484 ("Adapters -- `build_command` is pure and
asserted per harness; the launcher is injected, so no harness is executed in
unit tests"). Nothing here spawns a process, runs a real `claude` binary,
creates a worktree or writes a result file: `build_command` is asserted on the
argv it returns. Launcher behaviour is `test_launcher.py`'s; canned result
files belong to the engine card (§14 line 486); a real harness run is the
single opt-in end-to-end test (§14 lines 489-490).
```

Change line 22 from `from agent_manager.harness.base import HarnessAdapter, Usage` to:

```python
from agent_manager.harness.base import HarnessAdapter
```

Replace `test_the_adapter_satisfies_the_protocol_structurally` (lines 43-52) with:

```python
def test_the_adapter_satisfies_the_protocol_structurally():
    # `HarnessAdapter` is deliberately not runtime_checkable (see its
    # docstring in base.py), so conformance is asserted member by member --
    # and the adapter inherits from nothing, which is the whole point of a
    # structural Protocol.
    adapter = ClaudeAdapter()
    for member in HarnessAdapter.__protocol_attrs__:
        assert hasattr(adapter, member), member
    assert ClaudeAdapter.__bases__ == (object,)
    assert callable(adapter.build_command)
```

Delete lines 195-299 inclusive (through the second of the two blank lines that follow the last test, so the pre-existing blank pair at 193-194 becomes the only gap): the `FULL_LOG` constant and the ten tests `test_a_full_usage_report_comes_back_whole`, `test_a_log_with_no_usage_reports_nothing`, `test_partial_reporting_yields_a_partial_usage`, `test_the_last_report_wins`, `test_the_result_carries_no_key_outside_the_three`, `test_hostile_logs_never_raise` (with its `@pytest.mark.parametrize` decorator), `test_cache_counters_are_not_mistaken_for_the_prompt_size`, `test_a_multi_megabyte_log_is_parsed_without_backtracking`, `test_crlf_line_endings_and_human_spelling_report_the_same_numbers`. After the deletion, `test_a_model_that_looks_like_a_flag_stays_its_own_argv_element` is followed by two blank lines and then `def _imported_names(source: str) -> set[str]:`.

Leave `_imported_names`, `FORBIDDEN_IMPORTS`, `test_the_import_guard_catches_every_spelling_of_launching`, `test_the_import_guard_clears_what_the_adapter_legitimately_needs` and `test_the_adapter_launches_nothing_itself` byte-for-byte unchanged. Append at the end of the file, after `test_the_adapter_launches_nothing_itself`:

```python


def test_the_adapter_imports_neither_re_nor_math_nor_pydantic():
    # Parent spec 2026-10-03 §5.3 item 7. The adapter reads nothing back from
    # the harness (D4), so the old log scanner's machinery has no reason to
    # be imported here; this pins its removal so `parse_usage` cannot quietly
    # come back. Distinct from FORBIDDEN_IMPORTS, which guards launching.
    source = Path(claude_module.__file__).read_text(encoding="utf-8")
    assert _imported_names(source) & {"re", "math", "pydantic"} == set()
```

- [ ] **Step 3: Remove `Usage` from `tests/test_dispatch.py`**

Line 33, change `from agent_manager.harness.base import Outcome, Usage` to:

```python
from agent_manager.harness.base import Outcome
```

Delete `FakeAdapter.parse_usage` (lines 218-219, plus the blank line before it) so `FakeAdapter` ends with `build_command`'s `return [...]`.

Replace the opening of `test_the_outcome_is_journalled_on_the_attempt` (lines 672-678) with:

```python
def test_the_outcome_is_journalled_on_the_attempt(store, tmp_path, worktree):
    # §5.2: `Attempt` no longer declares the three usage fields, so the
    # journalled payload carries no such keys at all. Nothing reads the log
    # (D4), so nothing it says can put them back.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT], stdout="fake-harness ran\n")
```

(the rest of that test, lines 679-687, is unchanged).

Delete `UsageRefusingAdapter` and `test_dispatch_never_asks_the_adapter_for_usage` (lines 757-776 inclusive, through the second of the two blank lines that follow the test, so the pre-existing blank pair at 755-756 becomes the only gap), so `test_the_engine_never_opens_the_harness_log` is followed by two blank lines and then `def test_a_timed_out_attempt_journals_its_duration_and_no_usage`.

In `test_a_timed_out_attempt_journals_its_duration_and_no_usage`, change the launcher (lines 779-781) to:

```python
    launcher = FakeLauncher(
        results=[None], exit_code=None, timed_out=True, stdout="fake-harness ran\n"
    )
```

Leave `UnreadableLogLauncher` (including its `b"\xff\xfeusage: \x80tokens\n"` bytes) unchanged: it exercises an unreadable log, not `parse_usage`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_base.py tests/harness/test_claude.py tests/test_dispatch.py -q`

Expected: exactly these four FAIL, everything else passes:
- `test_the_protocol_declares_exactly_the_three_members_the_spec_prints` — AssertionError, extra item `'parse_usage'`.
- `test_a_structural_stub_satisfies_the_adapter_interface` — `AssertionError: parse_usage` (the stub no longer has it, the Protocol still does).
- `test_usage_is_gone_from_the_adapter_layer` — `assert not True`.
- `test_the_adapter_imports_neither_re_nor_math_nor_pydantic` — AssertionError, intersection `{'math', 'pydantic', 're'}`.

- [ ] **Step 5: Rewrite `src/agent_manager/harness/base.py`**

Replace the whole file with:

```python
"""The harness adapter interface and the value type it trades in.

Design §8 lines 306-313 print `HarnessAdapter` as a Protocol, and this module
is that Protocol verbatim plus the `Outcome` the launcher hands back. It is
pure interface and pure data: no adapter is implemented here (`claude.py`,
`codex.py`, `pi.py` are their own cards), nothing is launched here
(`launcher.py` owns that), and nothing reads the result file (§6 step 5 is the
engine's).

`Outcome` is built in-process from a subprocess that has already finished, so
it is a plain frozen dataclass rather than a pydantic model: the CLAUDE.md rule
keeps pydantic for what crosses a process boundary, and validating `Outcome`
would only re-check values this program just produced.

`Outcome` deliberately carries no result payload and no log contents. The
launcher never opens the result path and never reads the log it wrote --
nothing does (D4) -- which is what lets one launcher serve every adapter, and
what leaves §6 line 278's third harness_error trigger -- a missing result
file -- to the engine.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent_manager.models import Dispatch


@dataclass(frozen=True)
class Outcome:
    """The result of running one harness process, before anyone classifies it.

    The engine turns this into one of §6 line 277's four journalled outcomes:
    `timed_out or exit_code != 0` is `harness_error`, and the remaining
    distinctions (`ok`, `schema_invalid`, `gate_failed`, and the missing-result
    -file flavour of `harness_error`) come from the result file, which this
    type never sees.

    `exit_code` is `None` exactly when `timed_out` is true: a process the
    launcher killed has no exit status of its own to report, and `-9` would be
    indistinguishable from a harness that genuinely died of SIGKILL.
    """

    argv: list[str]
    exit_code: int | None
    timed_out: bool
    duration: float
    stdout_path: Path


class HarnessAdapter(Protocol):
    """What every harness adapter must provide (§8 lines 306-313, verbatim).

    Pure interface: no default implementations and no base class anyone
    inherits from, so an adapter is a `HarnessAdapter` by shape alone. Not
    `runtime_checkable` -- a runtime check would only assert member presence,
    which reads as a guarantee it cannot give, and nothing in this program
    needs to ask.

    `build_command` returns an argv list, never a shell string: §5 line 252 is
    explicit that nothing here builds a command string for anything to run
    verbatim. It is the adapter's whole job: D4 makes the harness's stdout a
    log rather than a channel, so no adapter reads anything back.

    `capabilities` is data for the engine's plan-time capability check
    (§8 lines 336-339); methodology is never a capability, that is what
    vendoring is for.
    """

    name: str
    capabilities: frozenset[str]

    def build_command(self, d: Dispatch) -> list[str]: ...
```

- [ ] **Step 6: Rewrite `src/agent_manager/harness/claude.py`**

Replace the whole file with (the `COMMAND`, `PROMPT_INSTRUCTION`, `name`, `capabilities` and `build_command` bodies are unchanged from the current file; the scanner, `parse_usage` and four imports are gone):

```python
"""The Claude adapter: one `Dispatch` in, one `claude -p` argv out (card
9a2524a8).

Design §8 lines 306-313 prints the `HarnessAdapter` Protocol; this module is
its first concrete implementation. It satisfies that Protocol structurally and
inherits from nothing -- the Protocol is pure interface and deliberately not
`runtime_checkable` (see its docstring in `base.py`), so a base class would buy
nothing and would invite default implementations the other adapters do not
want.

Two rules shape everything here. `build_command` is pure: it reads no file,
starts no process and consults no clock, so `launcher.py` (injected by the
engine, §14 line 485) stays the only thing in the program that runs anything.
And the adapter reads nothing back: D4 makes the harness's stdout a log rather
than a channel, so once the argv is built the adapter's job is done.
"""

from agent_manager.models import Dispatch

COMMAND = "claude"
"""The executable. A bare name, resolved on PATH by the launcher's Popen: the
adapter has no business knowing where a machine installed its harness."""

PROMPT_INSTRUCTION = (
    "Read {path} and follow the instructions in it exactly. It is your "
    "complete brief for this task."
)
"""The `-p` argument: a pointer at the materialized prompt, not the prompt.

§7 lines 296-298 pass documents by path rather than inlining them, and D4 puts
the result path inside that rendered prompt text upstream -- so this one
sentence is the whole bridge between the file the engine wrote and the process
the launcher starts. Inlining the file instead would re-bill it, invite a stale
copy, and make `build_command` read the disk.
"""


class ClaudeAdapter:
    """Turns a `Dispatch` into a `claude -p` argv. Reads nothing back (D4)."""

    name = "claude"
    """The key `Dispatch.harness` carries and `Policy.default_model` is indexed
    by (`roles/bundles/coder/policy.toml` -> `default_model.claude`). One
    spelling, so routing, policy lookup and journalling cannot drift apart."""

    capabilities = frozenset({"bash", "edit"})
    """What this harness can genuinely do, for the plan-time capability check
    (§8 lines 336-339).

    `browser` is deliberately absent: Claude Code only drives a browser through
    an extension that a headless runner is not guaranteed to have, and claiming
    it would make the capability check pass for a phase that then cannot run.
    Claiming less than the truth refuses a phase early, which is the safe
    direction. Methodology is never listed -- D6 makes it vendored prompt text.
    """

    def build_command(self, d: Dispatch) -> list[str]:
        """The argv for one attempt. Pure: no disk, no clock, no process.

        The two checks are the ones `Dispatch` cannot make for itself. Both are
        `ValueError` rather than a bespoke class because no retry can fix
        either, and neither is a harness failure to journal -- they are bugs
        above the adapter.
        """
        if d.harness != self.name:
            raise ValueError(
                f"dispatch is routed to harness {d.harness!r}, not "
                f"{self.name!r}: building a {self.name!r} argv for it would run "
                f"the wrong program against a real worktree"
            )
        for field in ("cwd", "prompt_path", "result_path"):
            path = getattr(d, field)
            if not path.is_absolute():
                raise ValueError(
                    f"Dispatch.{field} must be absolute, got {str(path)!r}: the "
                    f"harness starts in the worktree, so a relative path would "
                    f"resolve inside it -- and a result file written there gets "
                    f"committed (D4 keeps it outside)"
                )
        return [
            COMMAND,
            "--model",
            d.model,
            # D7: v1 launches full-auto. Confinement is the launcher's seam,
            # not a flag the adapter negotiates.
            "--dangerously-skip-permissions",
            "-p",
            PROMPT_INSTRUCTION.format(path=d.prompt_path),
        ]
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_base.py tests/harness/test_claude.py tests/test_dispatch.py -q`
Expected: all PASS.

Then run the whole default suite, since other fakes still carry a harmless `parse_usage` and nothing else may import `Usage`:

Run: `uv run pytest`
Expected: all PASS, no collection errors.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/harness/base.py src/agent_manager/harness/claude.py tests/harness/test_base.py tests/harness/test_claude.py tests/test_dispatch.py
git commit -m "cleanup: Usage and parse_usage leave the harness adapter protocol"
```

---

### Task 2: Vestigial `parse_usage` stubs leave the remaining fakes

These five stubs return `None` and import nothing, so no test fails because of them; this task removes dead members so no fake claims a Protocol member that no longer exists. The guard is the Task 4 grep plus the full suite staying green.

**Files:**
- Modify: `tests/test_bases.py:378-379`
- Modify: `tests/test_engine.py:2148-2149`
- Modify: `tests/test_integrate_workflow.py:192-193`
- Modify: `tests/test_integration.py:259-260`
- Modify: `tests/runtime/test_exactly_once.py:132-133`
- Modify: `tests/e2e/fake_claude.py:820-822` (comment only)

**Interfaces:**
- Consumes: Task 1's three-member `HarnessAdapter` (`name`, `capabilities`, `build_command`).
- Produces: nothing new.

- [ ] **Step 1: Delete the stub in `tests/test_bases.py`**

Delete these lines (378-379) and the blank line directly before them, so the class ends with `build_command`:

```python
    def parse_usage(self, stdout: str) -> None:
        return None
```

The class's last method then reads:

```python
    def build_command(self, d: models.Dispatch) -> list[str]:
        return ["fake-resolver", "--prompt", str(d.prompt_path)]
```

- [ ] **Step 2: Delete the stub in `tests/test_engine.py`**

Delete lines 2148-2149 and the blank line before them:

```python
    def parse_usage(self, stdout: str) -> None:
        return None
```

The class's last method then reads:

```python
    def build_command(self, d: models.Dispatch) -> list[str]:
        return ["fake-harness", "--role", d.role, "--result", str(d.result_path)]
```

- [ ] **Step 3: Delete the stub in `tests/test_integrate_workflow.py`**

Delete lines 192-193 and the blank line before them:

```python
    def parse_usage(self, stdout: str) -> None:
        return None
```

The class then ends with `build_command`'s closing `]` at line 190.

- [ ] **Step 4: Delete the stub in `tests/test_integration.py`**

Delete lines 259-260 and the blank line before them:

```python
    def parse_usage(self, stdout: str) -> None:
        return None
```

The class's last method then reads:

```python
    def build_command(self, d: models.Dispatch) -> list[str]:
        return ["fake-resolver", "--prompt", str(d.prompt_path)]
```

- [ ] **Step 5: Delete the stub in `tests/runtime/test_exactly_once.py`**

Delete lines 132-133 and the blank line before them:

```python
    def parse_usage(self, stdout: str):
        return None
```

The class then ends with `build_command`'s closing `]` at line 130.

- [ ] **Step 6: Reword the comment in `tests/e2e/fake_claude.py`**

Replace lines 820-822:

```python
    # stdout is a log, never a channel (D4). Usage-free on purpose: the adapter
    # scans it with `parse_usage`, and inventing token counts here would
    # journal fiction.
```

with:

```python
    # stdout is a log, never a channel (D4); nothing reads it.
```

- [ ] **Step 7: Run the default suite**

Run: `uv run pytest`
Expected: all PASS. (`tests/e2e/fake_claude.py` is a script driven by the opt-in `e2e_fake` tier; a comment-only edit cannot change it, so no `-m e2e_fake` run is required.)

- [ ] **Step 8: Commit**

```bash
git add tests/test_bases.py tests/test_engine.py tests/test_integrate_workflow.py tests/test_integration.py tests/runtime/test_exactly_once.py tests/e2e/fake_claude.py
git commit -m "cleanup: drop vestigial parse_usage stubs from the fake adapters"
```

---

### Task 3: Docs stop describing cost/token tracking

No test reads these docs (verified: the only test reference to the 2026-09-23 spec is a docstring in `tests/test_prompt.py:446` about §7), so verification is the Task 4 grep.

**Files:**
- Modify: `README.md:504`
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:316, 366, 563-565`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Fix the README `attempt_upsert` row**

In `README.md` line 504, replace:

```markdown
| `attempt_upsert` | one dispatch of a phase: `started`, then `ok`, `schema_invalid`, `gate_failed` or `harness_error`, with its cost, token and duration fields |
```

with:

```markdown
| `attempt_upsert` | one dispatch of a phase: `started`, then `ok`, `schema_invalid`, `gate_failed` or `harness_error`, with its `exit_code` and `duration` |
```

- [ ] **Step 2: Delete `parse_usage` from the §8 Protocol block**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace lines 310-317:

````markdown
```python
class HarnessAdapter(Protocol):
    name: str
    capabilities: frozenset[str]

    def build_command(self, d: Dispatch) -> list[str]: ...
    def parse_usage(self, stdout: str) -> Usage | None: ...
```
````

with:

````markdown
```python
class HarnessAdapter(Protocol):
    name: str
    capabilities: frozenset[str]

    def build_command(self, d: Dispatch) -> list[str]: ...
```
````

- [ ] **Step 3: Drop the cost/token fields from the §9 tree**

In the same file, the §9 tree (line 365 after Step 2's one-line deletion; originally 366), replace:

```
            └── attempts: list[Attempt]
                ├── n, exit_code, duration, tokens_in, tokens_out, cost
```

with:

```
            └── attempts: list[Attempt]
                ├── n, exit_code, duration
```

Then, directly after the tree's closing ```` ``` ```` fence (and before the blank line that precedes `**Write ordering.**`), insert one blank line and this paragraph:

```markdown
`tokens_in`, `tokens_out` and `cost` were removed from `Attempt` by [2026-10-03-remove-cost-tracking-design.md](2026-10-03-remove-cost-tracking-design.md).
```

- [ ] **Step 4: Mark the §17 open question resolved**

In the same file, §17, replace:

```markdown
- **Cost accounting on non-Claude harnesses.** `parse_usage` may return nothing
  where a harness does not report tokens. Per-phase cost then has gaps. Live
  with the gaps in v1, or require a usage source per adapter?
```

with:

```markdown
- ~~**Cost accounting on non-Claude harnesses.**~~ Resolved by
  [2026-10-03-remove-cost-tracking-design.md](2026-10-03-remove-cost-tracking-design.md):
  closed, because `am` keeps no per-phase cost, so no adapter needs a usage
  source.
```

Do not edit line 27 (motivation 1, "Cost and latency").

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "docs: drop cost/token tracking from the README and the 2026-09-23 design"
```

---

### Task 4: Verify the Done-when conditions

**Files:** none modified (fix-forward in the owning task's files if a check fails).

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: nothing.

- [ ] **Step 1: Grep for leftovers**

Use the Grep tool (or `rg`) with pattern `parse_usage|\bUsage\b` over `src/`, `tests/`, `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md`:

```bash
rg -n 'parse_usage|\bUsage\b' src tests README.md docs/superpowers/specs/2026-09-23-agent-manager-design.md
```

Expected: only these hits, all of which are Typer/brd "Usage" strings or headings and are allowed:
- `tests/test_board.py:87` and `:92` (`Usage: brd [OPTIONS]`)
- `tests/steps/test_rollup.py:543` (`"type": "Usage"` brd error type)
- `README.md:26` (`## Usage` heading)
- `tests/harness/test_base.py`, the single line `assert not hasattr(base, "Usage")` in `test_usage_is_gone_from_the_adapter_layer` (the absence check itself)

Any other hit is an incomplete removal: fix it in the file it lives in.

- [ ] **Step 2: Confirm the scanner imports are gone**

```bash
rg -n '^import (math|re)$|^from pydantic' src/agent_manager/harness/
```

Expected: no output.

- [ ] **Step 3: Run the full default suite**

Run: `uv run pytest`
Expected: all PASS, no collection errors.

- [ ] **Step 4: Commit (only if Step 1-3 required a fix)**

```bash
git add -A src tests README.md docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "cleanup: remove the last Usage/parse_usage reference"
```
