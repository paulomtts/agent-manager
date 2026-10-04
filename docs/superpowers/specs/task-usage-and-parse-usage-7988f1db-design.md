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
