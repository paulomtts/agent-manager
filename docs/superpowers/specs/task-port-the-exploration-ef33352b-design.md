# Subtask ef33352b — Port the exploration and verification gates

Parent story: 90d6bbf6 "Gates: the reducers ported from task.js" (milestone 352e955b). Source of truth for the design: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §5 (lines 231-253, "Reducers to port faithfully"), §4 (lines 112-139, package layout), §14 (lines 477-490, testing tiers). This document only narrows that agreed design to this subtask; it introduces no new design decisions.

## Scope

Create `src/agent_manager/steps/reducers.py`. The `steps/` subpackage does not exist yet (the package currently holds `__init__.py`, `board.py`, `dag.py`, `models.py`, `paths.py`, `store.py` at the top level, none of them a package); this subtask creates `src/agent_manager/steps/` with an `__init__.py` and `reducers.py` inside it, and the mirroring `tests/steps/` directory (with an `__init__.py` only if the existing `tests/` tree uses them — match whatever convention `tests/test_dag.py` etc. already follow). `reducers.py` contains exactly two pure functions ported faithfully from `/home/paulomtts/Code/leave-me-alone/plugins/leave-me-alone/workflows/task.js` lines 58-153 (the `PURE:BEGIN`/`PURE:END` region), with `workflows/task.test.mjs` lines 30-186 as the behavioural specification:

- `exploration_output_gate(explore, provided_verification)`
- `verification_gate(suite_cmds, allow_no_verification, caller_provided)`

Plus the two module-level constants the first gate needs: `MIN_SUMMARY_LENGTH = 60` and the placeholder-summary set `{"test", "todo", "tbd", "n/a", "na", "none", "placeholder", "unknown"}` (constants, not mutable state).

Out of scope, owned by blocked sibling subtask 5ee2ee50: `review_gate`, `plan_hash_gate`, `count_of`, `is_plan_hash`, `plan_hash_mismatch`. Out of scope permanently per §5 line 252: `shell_quote`. Also out of scope: wiring either gate into a step, phase, or CLI; Pydantic models for the gate inputs (per CLAUDE.md, Pydantic is only for validated process-boundary data — these reducers take plain mappings/lists that a caller has already read).

Purity is a hard requirement: no filesystem, network, model calls, imports with side effects, logging, or module-level mutable state. Both functions are total — they return a verdict rather than raising, including on malformed input.

## Observable behaviour

Both gates follow the JS convention: return `None` to pass, or a verdict `dict` to fail. `exploration_output_gate` returns `{"detail": "..."}` with no `blocked` key (unlike `review_gate`, which is not in this subtask). `verification_gate` returns `{"blocked": "verification", "detail": "..."}`.

### `exploration_output_gate(explore, provided_verification)`

`explore` is the mapping an Explore harness returned (JSON field names preserved: `summary`, `verification.fullSuite`); `provided_verification` is the caller-supplied verification mapping or a falsy value (`None`) when the caller discovered nothing. Checks run in this order, first failure wins:

1. **Summary plausibility.** Take `explore["summary"]` defensively (missing `explore`, missing key, or a non-string value coerces to text the way `String(x || '')` does in JS; absent/empty becomes the empty string) and strip it. Reject if the stripped length is `< MIN_SUMMARY_LENGTH` or the case-folded stripped value is in the placeholder set. Detail text: `exploration summary is implausibly short/placeholder for real findings on a subtask: ` followed by the JSON-quoted first 80 characters of the stripped summary.
2. **fullSuite shape.** `explore["verification"]["fullSuite"]` must be a list. Absent `verification`, absent `fullSuite`, or a non-list value (including a string, which is not an array in JS either) rejects with detail `exploration did not return an array for verification.fullSuite`.
3. **Caller-provided branch.** If `provided_verification` is truthy, the returned list must equal `provided_verification.get("fullSuite") or []` exactly. On mismatch the detail is `exploration did not return the caller-provided verification.fullSuite unchanged (expected <want>, got <got>)` where both are JSON renderings of the lists. On match, return `None` immediately — the implausible-command check is deliberately **not** applied in this branch, matching the source.
4. **Implausible commands** (falsy `provided_verification` only). Reject if any entry is not a string or its stripped length is `< 3`. Detail: `exploration's verification.fullSuite contains an implausible command: ` followed by the JSON rendering of the whole list.

Otherwise return `None`. Note on equality: the JS compares `JSON.stringify` of the two lists; the Python port compares the JSON renderings so that types which Python considers equal but JSON renders differently (e.g. `True` vs `1`) still fail, preserving "any deviation is proof the output is untrustworthy".

### `verification_gate(suite_cmds, allow_no_verification, caller_provided)`

Passes (`None`) when `suite_cmds` is non-empty, or when `allow_no_verification is True`. The opt-out is strict identity with `True`: truthy stand-ins (`"true"`, `1`, `{}`, `"yes"`) must not open the gate — this is the deliberate-opt-out property the JS test asserts explicitly. Otherwise return `{"blocked": "verification", "detail": ...}` where the detail is the source's three-part string: the fixed preamble ending in `Ship would run zero commands and still report success. `, then the branch — `caller_provided` truthy gives the sentence naming `the orchestrator discovers these from origin/<baseBranch>`, falsy gives `Exploration found none in CLAUDE.md, the CI workflows, or the manifest.` — then the fixed closing sentence offering the three remedies. The strings port verbatim; they are the operator-facing product of the gate and downstream tests match on them.

## Error paths

There are no exceptions to define. Every malformed input listed above resolves to a verdict dict: missing `explore`, `explore` without `verification`, `verification` without `fullSuite`, non-list `fullSuite`, non-string commands, non-string summary. `verification_gate` is given an already-materialised list by its caller, so an empty list is the only degenerate case and it is a blocked verdict, not an error.

## Test list

Per §14 line 479, `steps/reducers.py` is the **pure-functions tier**: all tests below are unit tests, ported case-by-case from `task.test.mjs`, living at `tests/steps/test_reducers.py` (mirroring `src/agent_manager/steps/reducers.py`, per CLAUDE.md). None of them belong in the Steps tier (temp git repo / temp `brd` board), the Engine fake-adapter tier, or the opt-in end-to-end tier — no gate here touches a repo, a harness, or the engine loop.

`verification_gate` (from `task.test.mjs:32-57) — unit tier:

1. A discovered suite proceeds (`["npm test"]` → `None`).
2. An empty suite is a hard stop, not a warning: `blocked == "verification"` and the detail mentions still reporting success.
3. The empty-suite stop names the caller when `caller_provided` is truthy (detail mentions `orchestrator discovers these from origin`) and names Exploration when it is falsy.
4. `allow_no_verification=True` is the only way past an empty suite; parametrised over the sloppy truthy values `"true"`, `1`, `{}`, `"yes"`, each of which still blocks.

`exploration_output_gate` (from `task.test.mjs:151-187`) — unit tier:

5. A real summary plus plausible verification passes.
6. The #1296 placeholder output is caught: `summary="test"`, `fullSuite=["a"]` → detail matches "implausibly short".
7. A summary that is exactly a known placeholder word is caught case-insensitively; parametrised over `todo`, `TBD`, `n/a`, `None`, `placeholder`.
8. A single-letter `fullSuite` entry (`["a"]`) with a real summary is caught as an implausible command.
9. With caller-provided verification, an exactly-unchanged `fullSuite` passes and any deviation fails with "did not return the caller-provided verification".
10. A missing or non-array `fullSuite` is caught, not raised: `verification={}`, and `explore` with no `verification` key at all.

Two additions beyond the direct port, both still unit tier and both asserting behaviour already implied by the source rather than new scope:

11. A `None`/empty `explore` mapping returns the summary verdict instead of raising (the JS `explore && explore.summary` guard).
12. In the caller-provided branch, an implausible-but-exactly-matching `fullSuite` (caller provided `["a"]`, explore returned `["a"]`) passes — pinning the deliberate early return at line 146 of the source.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none
