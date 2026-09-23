<!-- task-pipeline: validated -->
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

---

# Exploration and Verification Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `src/agent_manager/steps/reducers.py` holding two pure, total, verdict-returning gates — `verification_gate` and `exploration_output_gate` — ported faithfully from `task.js` lines 58-153 with `task.test.mjs` lines 30-186 as the behavioural specification.

**Architecture:** A new `src/agent_manager/steps/` subpackage (per design §4 line 126-128) whose `reducers.py` contains only module-level constants, two tiny private helpers (`_json` for `JSON.stringify`-shaped rendering, `_field` for defensive mapping reads) and the two gates. Each gate returns `None` to pass or a verdict `dict` to fail; neither raises, neither touches the filesystem, network, a model, or module-level mutable state. Nothing wires the gates into a step, phase, or CLI in this subtask.

**Tech Stack:** Python 3.12+, stdlib only (`json`, `collections.abc.Mapping`), pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-port-the-exploration-ef33352b-design.md` (prepended verbatim above); upstream design `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §4/§5/§14.

## Global Constraints

- Both gates are **pure**: no filesystem, network, model calls, logging, side-effecting imports, or module-level mutable state.
- Both gates are **total**: every malformed input resolves to a verdict dict; nothing raises.
- Verdict convention: `None` passes. `exploration_output_gate` fails with `{"detail": "..."}` and **no** `blocked` key; `verification_gate` fails with `{"blocked": "verification", "detail": "..."}`.
- JSON field names from the harness are preserved verbatim inside the data (`summary`, `verification`, `fullSuite`); Python identifiers are snake_case.
- Operator-facing detail strings port **verbatim** from `task.js`, including the em dash, the `origin/<baseBranch>` literal and the `allowNoVerification: true` spelling.
- Out of scope: `review_gate`, `plan_hash_gate`, `count_of`, `is_plan_hash`, `plan_hash_mismatch` (sibling subtask 5ee2ee50), `shell_quote` (never ports), Pydantic models for gate inputs, and any step/phase/CLI wiring.
- Tests live in the pure-functions unit tier only: `tests/steps/test_reducers.py`. The existing `tests/` tree is flat and has **no** `__init__.py` files — do not add any.
- Verification: `uv run pytest`. No typecheck command, no lint command.

## Review Focus

Input classes the spec implies but the direct port from `task.test.mjs` does not exercise. Each has a test in the task that owns the code.

- `provided_verification={}` — an empty mapping is truthy in JS but falsy in Python, so the Python port takes the implausible-command branch instead of the exact-match branch. Pinned in Task 2 so the divergence is a decision on record, not an accident.
- `fullSuite` returned as a bare string (`"uv run pytest"`) — a string is iterable in Python but is not an array in JS; it must reject with "did not return an array", never be scanned character by character. Task 2.
- Non-string `fullSuite` entries (`[3]`, `[None]`) and an entry `json.dumps` cannot serialise (`[object()]`) — both must produce the implausible-command verdict, not `AttributeError` or `TypeError`. Task 2.
- A caller-provided list that is Python-equal but JSON-different (`[True]` vs `[1]`) — must still be reported as a deviation, because the JS compares `JSON.stringify` output. Task 2.
- A long whitespace-only summary, and a padded placeholder (`"  todo  "`) — stripping must happen before both the length test and the placeholder lookup. Task 2.
- `verification_gate` detail always ends with the three-remedy sentence, on both caller-provided branches. Task 1.

---

### Task 1: The `steps` package and `verification_gate`

**Files:**
- Create: `src/agent_manager/steps/__init__.py`
- Create: `src/agent_manager/steps/reducers.py`
- Test: `tests/steps/test_reducers.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (this is the first task on the branch).
- Produces: `agent_manager.steps.reducers.verification_gate(suite_cmds: list[str], allow_no_verification: object, caller_provided: object) -> dict[str, str] | None`. Task 2 adds `MIN_SUMMARY_LENGTH: int`, `PLACEHOLDER_SUMMARIES: frozenset[str]`, the private helpers `_json(value: object) -> str` and `_field(mapping: object, name: str) -> object`, and `exploration_output_gate(explore: object, provided_verification: object) -> dict[str, str] | None` to the same module.

- [ ] **Step 1: Write the failing tests for `verification_gate`**

Create `tests/steps/test_reducers.py` (create the `tests/steps/` directory; add no `__init__.py`, matching the existing flat `tests/` tree) with exactly this content:

```python
"""Unit tests for the gates ported from task.js.

Ported case-by-case from the sibling plugin's `workflows/task.test.mjs`
(lines 30-186), which is the behavioural specification for these gates.
"""

import pytest

from agent_manager.steps.reducers import verification_gate


def test_a_discovered_suite_proceeds():
    assert verification_gate(["npm test"], None, False) is None


def test_an_empty_suite_is_a_hard_stop_not_a_warning():
    gate = verification_gate([], None, False)
    assert gate["blocked"] == "verification"
    assert "still report success" in gate["detail"]


def test_the_empty_suite_stop_names_the_caller_when_the_caller_supplied_the_empty_list():
    # Which half of the pipeline to go fix differs entirely: an empty list the
    # orchestrator passed down means the BASE BRANCH documents no commands.
    caller = verification_gate([], None, True)
    exploration = verification_gate([], None, False)
    assert "orchestrator discovers these from origin" in caller["detail"]
    assert "Exploration found none" in exploration["detail"]


def test_allow_no_verification_true_is_the_only_way_past_an_empty_suite():
    assert verification_gate([], True, False) is None


@pytest.mark.parametrize("sloppy", ["true", 1, {}, "yes"])
def test_a_truthy_stand_in_does_not_open_the_gate(sloppy):
    # The opt-out is deliberate, so the check is strict identity with True.
    assert verification_gate([], sloppy, False)["blocked"] == "verification"


def test_a_discovered_suite_proceeds_whatever_the_other_two_arguments_say():
    assert verification_gate(["uv run pytest"], False, True) is None
    assert verification_gate(["uv run pytest"], None, None) is None


@pytest.mark.parametrize("caller_provided", [True, False])
def test_the_blocked_detail_always_offers_the_three_remedies(caller_provided):
    detail = verification_gate([], None, caller_provided)["detail"]
    assert detail.endswith(
        " Document the command, pass verification.fullSuite explicitly, or set "
        "allowNoVerification: true to proceed unverified on purpose."
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'agent_manager.steps'`.

- [ ] **Step 3: Create the `steps` subpackage**

Create `src/agent_manager/steps/__init__.py` containing exactly:

```python
"""Deterministic phases and the gates ported from task.js."""
```

- [ ] **Step 4: Write `reducers.py` with `verification_gate` only**

Create `src/agent_manager/steps/reducers.py`:

```python
"""The gates ported faithfully from the `PURE:BEGIN`/`PURE:END` region of
`task.js` (lines 58-153 of the sibling leave-me-alone plugin's
`workflows/task.js`), with its `task.test.mjs` as the behavioural
specification.

Each gate exists because a live run got past it once, and the comments here
record the incident. Every gate is pure — no filesystem, network, model calls
or module-level mutable state — and total: malformed input produces a verdict,
never an exception. A gate returns ``None`` to pass, or a verdict ``dict`` to
fail.
"""


# An empty suite makes every downstream gate vacuous: Ship runs nothing and
# reports passed=true, Review has no red/green to work against, and the card
# reaches `done` unverified. Observed on a run whose base branch documented no
# commands — the Ship agents happened to improvise and find the tests
# themselves, which is luck, not design, and their prompt explicitly tells them
# NOT to substitute commands. Fail loudly instead, with a deliberate opt-out
# for repos that genuinely have no suite yet.
def verification_gate(
    suite_cmds: list[str],
    allow_no_verification: object,
    caller_provided: object,
) -> dict[str, str] | None:
    """``None`` when a suite exists or the opt-out was set, else a blocked verdict."""
    if len(suite_cmds) > 0:
        return None
    # Strict identity: truthy stand-ins must not open a deliberate opt-out.
    if allow_no_verification is True:
        return None
    if caller_provided:
        source = (
            "The caller passed an empty verification.fullSuite; the orchestrator "
            "discovers these from origin/<baseBranch>, so check that the base branch "
            "actually documents its test commands."
        )
    else:
        source = "Exploration found none in CLAUDE.md, the CI workflows, or the manifest."
    return {
        "blocked": "verification",
        "detail": (
            "no full-suite command is available for this repo, so nothing downstream "
            "could verify this subtask — Ship would run zero commands and still "
            "report success. "
            + source
            + " Document the command, pass verification.fullSuite explicitly, or set "
            "allowNoVerification: true to proceed unverified on purpose."
        ),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS — 11 passed (the two parametrised tests expand to 4 and 2 cases).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no regressions in the existing `tests/test_*.py` files.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/steps/__init__.py src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(steps): port verificationGate from task.js"
```

---

### Task 2: `exploration_output_gate`

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (append constants, helpers and the second gate)
- Modify: `tests/steps/test_reducers.py` (append the exploration-gate tests)

**Interfaces:**
- Consumes: the module created in Task 1 — `agent_manager.steps.reducers` already exports `verification_gate(suite_cmds, allow_no_verification, caller_provided) -> dict[str, str] | None`.
- Produces: `MIN_SUMMARY_LENGTH: int = 60`, `PLACEHOLDER_SUMMARIES: frozenset[str]`, `_json(value: object) -> str`, `_field(mapping: object, name: str) -> object`, and `exploration_output_gate(explore: object, provided_verification: object) -> dict[str, str] | None` in the same module. No later task in this subtask depends on them.

- [ ] **Step 1: Write the failing summary-plausibility tests**

Append to `tests/steps/test_reducers.py`, and change the import line at the top of the file from `from agent_manager.steps.reducers import verification_gate` to:

```python
from agent_manager.steps.reducers import exploration_output_gate, verification_gate
```

Appended content:

```python
# ── exploration_output_gate ──────────────────────────────────────────────────
# Ported from task.test.mjs:151-187. Added upstream after a live run (#1296)
# where the explore agent did real work, then gave up and submitted a
# placeholder that trivially satisfies the schema: summary="test",
# verification.fullSuite=["a"].

REAL_SUMMARY = (
    "graph_canvas.js renderEdges (lines 228-253) needs a transparent hit-path emitted "
    "before the visible path, per issue #1296; graph_shell.css needs the matching "
    "cursor rule."
)
REAL_VERIFICATION = {"fullSuite": ["uv run pytest tests/unit -q", "uv run ruff check ."]}


def test_a_real_summary_and_plausible_verification_pass_the_gate():
    explore = {"summary": REAL_SUMMARY, "verification": REAL_VERIFICATION}
    assert exploration_output_gate(explore, None) is None


def test_the_1296_placeholder_output_is_caught_as_a_short_summary():
    gate = exploration_output_gate({"summary": "test", "verification": {"fullSuite": ["a"]}}, None)
    assert "implausibly short" in gate["detail"]
    assert "blocked" not in gate


@pytest.mark.parametrize("word", ["todo", "TBD", "n/a", "None", "placeholder"])
def test_a_summary_that_is_exactly_a_placeholder_word_is_caught_case_insensitively(word):
    gate = exploration_output_gate({"summary": word, "verification": REAL_VERIFICATION}, None)
    assert gate is not None


def test_a_padded_placeholder_word_is_stripped_before_the_lookup():
    gate = exploration_output_gate({"summary": "  todo  ", "verification": REAL_VERIFICATION}, None)
    assert "implausibly short" in gate["detail"]
    # The detail carries the STRIPPED summary, JSON-quoted.
    assert '"todo"' in gate["detail"]


def test_a_long_whitespace_only_summary_is_short_once_stripped():
    gate = exploration_output_gate({"summary": " " * 100, "verification": REAL_VERIFICATION}, None)
    assert gate["detail"].endswith('subtask: ""')


@pytest.mark.parametrize("explore", [None, {}, {"summary": None}, {"summary": 7}])
def test_a_missing_or_non_string_summary_returns_a_verdict_instead_of_raising(explore):
    gate = exploration_output_gate(explore, None)
    assert "implausibly short" in gate["detail"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: collection error — `ImportError: cannot import name 'exploration_output_gate' from 'agent_manager.steps.reducers'`.

- [ ] **Step 3: Add the constants, helpers and the summary check**

Append to `src/agent_manager/steps/reducers.py` (and add `import json` plus `from collections.abc import Mapping` at the top of the file, directly below the module docstring, separated from it by a blank line):

```python
# A degenerate Explore result is schema-valid but carries no real findings.
# Seen live on #1296: the explore agent did the actual work, but its
# StructuredOutput call omitted the required `verification` field three times
# in a row, and it then submitted summary="test", fullSuite=["a"] — which Spec
# correctly refused to design from, reading as a Spec-stage bug when the real
# defect was that nothing checked Explore's output was real.
MIN_SUMMARY_LENGTH = 60
PLACEHOLDER_SUMMARIES = frozenset(
    {"test", "todo", "tbd", "n/a", "na", "none", "placeholder", "unknown"}
)


def _json(value: object) -> str:
    """Render ``value`` the way ``JSON.stringify`` does: no spaces after separators.

    ``default=str`` keeps the gate total — a value the encoder cannot handle
    becomes text in the detail message rather than a ``TypeError``.
    """
    return json.dumps(value, separators=(",", ":"), default=str)


def _field(mapping: object, name: str) -> object:
    """Read ``name`` off a mapping, or ``None`` if it is not a mapping at all."""
    return mapping.get(name) if isinstance(mapping, Mapping) else None


def exploration_output_gate(
    explore: object,
    provided_verification: object,
) -> dict[str, str] | None:
    """``None`` when the Explore output is plausible, else a verdict with a detail."""
    raw_summary = _field(explore, "summary")
    # Mirrors JS `String((explore && explore.summary) || '')`: any falsy value
    # (absent, None, '', 0) becomes the empty string.
    summary = ("" if not raw_summary else str(raw_summary)).strip()
    if len(summary) < MIN_SUMMARY_LENGTH or summary.lower() in PLACEHOLDER_SUMMARIES:
        return {
            "detail": "exploration summary is implausibly short/placeholder for real "
            f"findings on a subtask: {_json(summary[:80])}"
        }

    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS — the summary tests and the existing `verification_gate` tests all green.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(steps): reject implausible exploration summaries"
```

- [ ] **Step 6: Write the failing fullSuite-shape tests**

Append to `tests/steps/test_reducers.py`:

```python
def test_a_verification_mapping_without_full_suite_is_caught_not_raised():
    gate = exploration_output_gate({"summary": REAL_SUMMARY, "verification": {}}, None)
    assert gate["detail"] == "exploration did not return an array for verification.fullSuite"


def test_an_explore_with_no_verification_key_at_all_is_caught_not_raised():
    gate = exploration_output_gate({"summary": REAL_SUMMARY}, None)
    assert gate["detail"] == "exploration did not return an array for verification.fullSuite"


@pytest.mark.parametrize("not_a_list", ["uv run pytest", {"0": "uv run pytest"}, 3, None])
def test_a_non_array_full_suite_is_caught(not_a_list):
    # A bare string is iterable in Python but is not an array in JS; it must be
    # rejected, never scanned character by character.
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": not_a_list}}, None
    )
    assert gate["detail"] == "exploration did not return an array for verification.fullSuite"
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: FAIL — 6 failures, each `TypeError: 'NoneType' object is not subscriptable` on `gate["detail"]`, because the gate currently returns `None` once the summary is plausible.

- [ ] **Step 8: Add the fullSuite shape check**

In `src/agent_manager/steps/reducers.py`, replace the trailing `    return None` of `exploration_output_gate` with:

```python
    full_suite = _field(_field(explore, "verification"), "fullSuite")
    if not isinstance(full_suite, list):
        return {"detail": "exploration did not return an array for verification.fullSuite"}

    return None
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(steps): require an array for verification.fullSuite"
```

- [ ] **Step 11: Write the failing caller-provided-branch tests**

Append to `tests/steps/test_reducers.py`:

```python
def test_caller_provided_verification_must_come_back_exactly_unchanged():
    provided = {"fullSuite": ["make test", "make lint"]}
    explore = {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["make test", "make lint"]}}
    assert exploration_output_gate(explore, provided) is None


def test_any_deviation_from_caller_provided_verification_fails():
    provided = {"fullSuite": ["make test", "make lint"]}
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["a"]}}, provided
    )
    assert "did not return the caller-provided verification" in gate["detail"]
    assert '(expected ["make test","make lint"], got ["a"])' in gate["detail"]


def test_an_implausible_but_exactly_matching_full_suite_passes():
    # Pins the deliberate early return: in the caller-provided branch the
    # implausible-command check is NOT applied (task.js line 146).
    provided = {"fullSuite": ["a"]}
    explore = {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["a"]}}
    assert exploration_output_gate(explore, provided) is None


def test_a_caller_provided_mapping_without_full_suite_expects_an_empty_list():
    assert (
        exploration_output_gate(
            {"summary": REAL_SUMMARY, "verification": {"fullSuite": []}}, {"note": "none found"}
        )
        is None
    )
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": REAL_VERIFICATION}, {"note": "none found"}
    )
    assert "(expected [], got " in gate["detail"]


def test_a_python_equal_but_json_different_list_is_still_a_deviation():
    # JSON renders True as `true` and 1 as `1`; the JS compares the rendered
    # strings, so [True] is not [1] here even though Python says it is.
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": [True]}}, {"fullSuite": [1]}
    )
    assert "(expected [1], got [true])" in gate["detail"]
```

- [ ] **Step 12: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: FAIL — the three deviation tests fail with `TypeError: 'NoneType' object is not subscriptable`, because the gate currently returns `None` for every well-shaped list.

- [ ] **Step 13: Add the caller-provided branch**

In `src/agent_manager/steps/reducers.py`, replace the trailing `    return None` of `exploration_output_gate` with:

```python
    # When the caller already discovered verification commands, the prompt
    # tells Explore to return them EXACTLY as given — so any deviation, not
    # just an implausible one, is itself proof the output is not trustworthy.
    if provided_verification:
        want = _json(_field(provided_verification, "fullSuite") or [])
        got = _json(full_suite)
        if got != want:
            return {
                "detail": "exploration did not return the caller-provided "
                f"verification.fullSuite unchanged (expected {want}, got {got})"
            }
        return None

    return None
```

- [ ] **Step 14: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS.

- [ ] **Step 15: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(steps): require caller-provided verification back unchanged"
```

- [ ] **Step 16: Write the failing implausible-command tests**

Append to `tests/steps/test_reducers.py`:

```python
def test_a_single_letter_full_suite_command_is_caught():
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["a"]}}, None
    )
    assert "implausible command" in gate["detail"]
    assert gate["detail"].endswith('["a"]')


@pytest.mark.parametrize("bad", [3, None, ["uv run pytest"], "  x  ", ""])
def test_a_non_string_or_too_short_entry_is_caught_not_raised(bad):
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["uv run pytest", bad]}}, None
    )
    assert "implausible command" in gate["detail"]


def test_an_entry_json_cannot_serialise_still_produces_a_verdict():
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"fullSuite": [object()]}}, None
    )
    assert "implausible command" in gate["detail"]


def test_an_empty_full_suite_is_not_an_implausible_command():
    # Emptiness is verification_gate's business, not this gate's.
    assert (
        exploration_output_gate({"summary": REAL_SUMMARY, "verification": {"fullSuite": []}}, None)
        is None
    )


def test_an_empty_caller_provided_mapping_takes_the_plausibility_branch():
    # {} is truthy in JS but falsy in Python, so the port checks plausibility
    # here rather than exact equality. Recorded deliberately.
    explore_ok = {"summary": REAL_SUMMARY, "verification": REAL_VERIFICATION}
    assert exploration_output_gate(explore_ok, {}) is None
    explore_bad = {"summary": REAL_SUMMARY, "verification": {"fullSuite": ["a"]}}
    assert "implausible command" in exploration_output_gate(explore_bad, {})["detail"]
```

- [ ] **Step 17: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: FAIL — 8 failures, each `TypeError: 'NoneType' object is not subscriptable`, because no plausibility check exists yet (the empty-`fullSuite` test already passes).

- [ ] **Step 18: Add the implausible-command check**

In `src/agent_manager/steps/reducers.py`, replace the final `    return None` of `exploration_output_gate` (the one after the `if provided_verification:` block) with:

```python
    if any(not isinstance(cmd, str) or len(cmd.strip()) < 3 for cmd in full_suite):
        return {
            "detail": "exploration's verification.fullSuite contains an implausible "
            f"command: {_json(full_suite)}"
        }
    return None
```

- [ ] **Step 19: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS.

- [ ] **Step 20: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no regressions.

- [ ] **Step 21: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(steps): reject implausible commands in verification.fullSuite"
```
